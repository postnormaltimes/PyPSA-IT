"""Bounded cached native-cell QA and resumable closeout; no model or weather build."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import xarray as xr

from .common import ROOT, ZONES, SCENARIOS, dump_json, load_yaml, sha256_file
from .final_methodology_closure import STATE, SPEC, SPEC_SHA256, record_phase
from .final_wind_cells import allocate_sites
from .final_wind_resource import QA, RESOURCE, fixed_mw_allocation

KEY = ["year", "scenario", "zone", "technology"]


def expected_case_keys():
    return {(year, scenario, zone, family) for year,scenario in SCENARIOS
            for zone in ZONES for family in ("WIND_ONSHORE", "WIND_OFFSHORE")}


def validate_allocation(cells, selected, frozen_MW):
    """Validate identities, endpoints, physical upper bounds and exact frozen MW."""
    if cells.site_id.duplicated().any() or selected.site_id.duplicated().any():
        raise RuntimeError("CELL_ALLOCATION_DUPLICATE_SITE")
    if not set(selected.site_id).issubset(set(cells.site_id)):
        raise RuntimeError("CELL_ALLOCATION_UNKNOWN_SITE")
    source = cells.set_index("site_id").loc[selected.site_id]
    for column in ("zone", "technology"):
        if not np.array_equal(source[column], selected[column]):
            raise RuntimeError("CELL_ALLOCATION_ZONE_OR_TECHNOLOGY_DRIFT")
    for column in ("p_nom_max_MW", "annual_CF", "hourly_column"):
        np.testing.assert_allclose(source[column], selected[column], atol=1e-10, rtol=1e-12)
    values = selected.selected_MW.to_numpy()
    if (not np.isfinite(values).all() or (values <= 0).any() or
            (values > source.p_nom_max_MW.to_numpy()+1e-7).any()):
        raise RuntimeError("CELL_ALLOCATION_PHYSICAL_BOUND_FAIL")
    if not np.isclose(values.sum(), frozen_MW, atol=1e-7, rtol=1e-12):
        raise RuntimeError("CELL_ALLOCATION_FROZEN_MW_FAIL")
    return float(values.sum()-frozen_MW)


def verify_effective_profile(profile, selected, hourly, frozen_MW):
    """Reconstruct from persisted selected cells, without unselected dense traces."""
    expected = pd.date_range("2019-01-01", periods=8760, freq="h", tz="UTC")
    ordered = profile.sort_values("snapshot")
    if not pd.DatetimeIndex(ordered.snapshot).equals(expected):
        raise RuntimeError("CELL_EFFECTIVE_CHRONOLOGY_FAIL")
    if not pd.DatetimeIndex(hourly.time.values).equals(expected.tz_localize(None)):
        raise RuntimeError("CELL_SELECTED_PROFILE_CHRONOLOGY_FAIL")
    source = hourly.p_max_pu.sel(hourly_column=selected.hourly_column.to_numpy()).to_numpy()
    actual = ordered.p_max_pu.to_numpy()
    if (not np.isfinite(source).all() or not np.isfinite(actual).all() or
            (source < -1e-12).any() or (source > 1+1e-12).any() or
            (actual < -1e-12).any() or (actual > 1+1e-12).any()):
        raise RuntimeError("CELL_EFFECTIVE_PROFILE_BOUND_FAIL")
    reconstructed = source @ (selected.selected_MW.to_numpy()/frozen_MW)
    np.testing.assert_allclose(actual, reconstructed, atol=1e-12, rtol=1e-12)
    return float(np.abs(actual-reconstructed).max())


def complete_native_cell_qa():
    """Finish missing QA columns and verify cached outputs; do not resite or promote."""
    cells = pd.read_csv(QA / "NATIVE_CELL_RESOURCE_TABLE.csv")
    allocations = pd.read_csv(QA / "NATIVE_CELL_SITING_ALLOCATION.csv")
    profiles = pd.read_parquet(RESOURCE / "NATIVE_CELL_EFFECTIVE_WIND_2019.parquet")
    cases = pd.read_csv(QA / "FINAL_WIND_PROFILE_QA.csv")
    comparison = pd.read_csv(QA / "RESOURCE_CLASS_TO_NATIVE_REFERENCE_QA.csv")
    costs = json.loads((QA / "OFFSHORE_SITE_LCOE_RECEIPT.json").read_text())
    annual = {row["technology"]: row["annual_cost"] for row in costs["components"]}
    capacities = pd.read_csv(ROOT / "qa/stage_b/zonal_vre_v1/MEM_STAGE_B_ZONAL_VRE_CAPACITY_COVERAGE.csv")
    capacities = capacities.loc[capacities.technology.isin(("WIND_ONSHORE", "WIND_OFFSHORE"))]
    if cells.duplicated(["technology", "zone", "site_id"]).any() or cases.duplicated(KEY).any():
        raise RuntimeError("CELL_QA_DUPLICATE_KEY")
    if profiles.duplicated(KEY+["snapshot"]).any():
        raise RuntimeError("CELL_EFFECTIVE_PROFILE_DUPLICATE_KEY")
    expected_keys = expected_case_keys()
    if set(map(tuple, cases[KEY].to_numpy())) != expected_keys:
        raise RuntimeError("CELL_QA_SCENARIO_COVERAGE_FAIL")
    if set(map(tuple, profiles[KEY].drop_duplicates().to_numpy())) != expected_keys:
        raise RuntimeError("CELL_PROFILE_SCENARIO_COVERAGE_FAIL")
    np.testing.assert_allclose(cells.p_nom_max_MW,
        cells.eligible_area_fraction*cells.cell_area_km2*cells.capacity_per_sqkm, atol=1e-8, rtol=1e-12)
    if (not np.isfinite(cells.select_dtypes("number")).all().all() or
            not cells.eligible_area_fraction.between(0,1,inclusive="right").all() or
            (cells.cell_area_km2 <= 0).any()):
        raise RuntimeError("CELL_RESOURCE_METADATA_INVALID")
    if len(comparison) != 336 or comparison.duplicated(KEY+["resolution"]).any():
        raise RuntimeError("CELL_CLASS_COMPARISON_COVERAGE_FAIL")
    # Fill the selected-potential fields missing from the interrupted export,
    # using only cached class records and the already-saved cell allocation.
    case_potential = []
    class_potential = {}
    case_checks = []
    for family in ("WIND_ONSHORE", "WIND_OFFSHORE"):
        class_table = pd.read_csv(QA / f"{family}_RESOURCE_CLASSES.csv")
        old_potential = class_table.loc[class_table.resource_classes.eq(1)].set_index("zone").p_nom_max_MW
        native_potential = cells.loc[cells.technology.eq(family)].groupby("zone").p_nom_max_MW.sum()
        np.testing.assert_allclose(native_potential.reindex(ZONES), old_potential.reindex(ZONES), atol=1e-7, rtol=1e-12)
        with xr.open_dataset(RESOURCE / f"{family}_SELECTED_NATIVE_CELL_PROFILES_2019.nc") as hourly:
            hourly.load()
            for case in cases.loc[cases.technology.eq(family)].itertuples(index=False):
                key = tuple(getattr(case, column) for column in KEY)
                mask = lambda table: np.logical_and.reduce([table[column].eq(value) for column,value in zip(KEY,key)])
                sites = cells.loc[cells.technology.eq(family) & cells.zone.eq(case.zone)].sort_values("site_id")
                selected = allocations.loc[mask(allocations)].sort_values("site_id")
                frozen = capacities.loc[mask(capacities), "p_nom_MW"]
                if len(frozen) != 1 or not np.isclose(float(frozen.iloc[0]), case.frozen_MW, atol=1e-7, rtol=1e-12):
                    raise RuntimeError("CELL_CAPACITY_AUTHORITY_DRIFT")
                residual = validate_allocation(sites, selected, case.frozen_MW)
                error = verify_effective_profile(profiles.loc[mask(profiles)], selected, hourly, case.frozen_MW)
                common = annual["onwind" if family == "WIND_ONSHORE" else "offwind"]
                connection = np.zeros(len(sites)) if family == "WIND_ONSHORE" else (
                    annual["offwind-ac-station"]+1.25*(sites.distance_km.to_numpy()*annual["offwind-ac-connection-submarine"]
                        +20*annual["offwind-ac-connection-underground"]))
                ordered, rebuilt, lcoe = allocate_sites(case.frozen_MW, sites, common, connection)
                saved = selected.set_index("site_id").selected_MW.reindex(ordered.site_id,fill_value=0.)
                np.testing.assert_allclose(rebuilt, saved, atol=1e-7, rtol=1e-12)
                np.testing.assert_allclose(lcoe[rebuilt > 0], selected.SITE_LCOE, atol=1e-10, rtol=1e-12)
                potential = float(selected.p_nom_max_MW.sum())
                case_potential.append((*key,potential))
                class_potential[(*key,"NATIVE_CELLS")] = potential
                for count in (1,4,8):
                    group = class_table.loc[class_table.zone.eq(case.zone) & class_table.resource_classes.eq(count)].sort_values("class_number")
                    ccost = np.zeros(len(group)) if family == "WIND_ONSHORE" else (
                        annual["offwind-ac-station"]+1.25*(group.average_distance_km.to_numpy()*annual["offwind-ac-connection-submarine"]+20*annual["offwind-ac-connection-underground"]))
                    useful = group.annual_CF.gt(0).to_numpy() & group.p_nom_max_MW.gt(0).to_numpy()
                    allocation = np.zeros(len(group))
                    allocation[useful] = fixed_mw_allocation(case.frozen_MW, group.p_nom_max_MW.to_numpy()[useful],
                        -(common+ccost[useful])/(8760*group.annual_CF.to_numpy()[useful]))
                    class_potential[(*key,f"{count}_CLASSES")] = float(group.loc[allocation > 0,"p_nom_max_MW"].sum())
                case_checks.append({**dict(zip(KEY,key)), "selected_cells": len(selected),
                    "capacity_residual_MW": residual, "hourly_reconstruction_max_abs": error,
                    "deterministic_allocation_reconstruction": "PASS", "status": "PASS"})
    cases = cases.drop(columns="selected_technical_potential_MW", errors="ignore").merge(
        pd.DataFrame(case_potential,columns=KEY+["selected_technical_potential_MW"]), on=KEY, validate="one_to_one")
    comparison["selected_technical_potential_MW"] = [class_potential[tuple(row)] for row in comparison[KEY+["resolution"]].to_numpy()]
    cases.to_csv(QA / "FINAL_WIND_PROFILE_QA.csv", index=False)
    comparison.to_csv(QA / "RESOURCE_CLASS_TO_NATIVE_REFERENCE_QA.csv", index=False)
    historical = pd.read_csv(QA / "TERNA_2019_WIND_PLAUSIBILITY.csv")
    historical = historical.loc[historical.basis.eq("Netta")]
    if len(historical) != 7 or historical.zone.duplicated().any():
        raise RuntimeError("CELL_PLAUSIBILITY_SOURCE_GRAIN_FAIL")
    old = pd.read_csv(QA / "ONE_CLASS_REFERENCE_RECONCILIATION.csv")
    plausibility = cases[KEY+["frozen_MW","availability_CF"]].merge(old[["zone","technology","old_CF"]],
        on=["zone","technology"],validate="many_to_one").merge(historical,on="zone",validate="many_to_one")
    plausibility = plausibility.rename(columns={"availability_CF": "native_cell_CF", "old_CF": "old_one_class_CF"})
    plausibility["historical_rescaling_applied"] = False
    plausibility.to_csv(QA / "FINAL_WIND_PLAUSIBILITY_QA.csv",index=False)
    robustness = pd.read_csv(QA / "OFFSHORE_COST_RANKING_ROBUSTNESS.csv")
    tolerances = load_yaml(ROOT / "config/final_methodology_closure.yaml")["wind"]["site_cost_robustness_tolerances_declared_before_stress"]
    if len(robustness) != 126 or robustness.duplicated(["year","scenario","zone","common_generation_cost_scale"]).any():
        raise RuntimeError("CELL_ROBUSTNESS_COVERAGE_FAIL")
    passed = np.logical_and.reduce([robustness[column].le(limit) for column,limit in tolerances.items()])
    if not np.array_equal(passed, robustness.status.eq("PASS")):
        raise RuntimeError("CELL_ROBUSTNESS_STATUS_DRIFT")
    failed = robustness.loc[~passed]
    state = json.loads(STATE.read_text())
    parents = []
    for parent in state["controlling_parents"]:
        if sha256_file(ROOT / parent["path"]) != parent["sha256"]:
            raise RuntimeError("CELL_WIND_PARENT_HASH_FAIL")
        parents.append({**parent,"unchanged": True})
    successors = ROOT / "networks/unsolved/final_wind_native_v1"
    if successors.exists() and list(successors.glob("*.nc")):
        raise RuntimeError("CELL_BLOCKED_WIND_SUCCESSOR_UNEXPECTED")
    result = {"state": "WIND_RESOURCE_SITING_BLOCKED" if len(failed) else "NATIVE_CELL_VERIFICATION_PASS_PENDING_PROMOTION",
        "physical_and_cached_output_validation": "PASS", "native_resource_records": len(cells),
        "allocation_records": len(allocations), "effective_profile_rows": len(profiles),
        "case_checks_PASS": len(case_checks), "class_native_comparison_records": len(comparison),
        "maximum_capacity_residual_MW": max(abs(row["capacity_residual_MW"]) for row in case_checks),
        "maximum_hourly_reconstruction_difference": max(row["hourly_reconstruction_max_abs"] for row in case_checks),
        "profile_bounds": {"minimum": float(profiles.p_max_pu.min()),"maximum": float(profiles.p_max_pu.max()),
            "numerical_tolerance": 1e-12, "verification_clipping_applied": False},
        "deterministic_allocation_reconstruction": "PASS", "robustness_cases": len(robustness),
        "robustness_PASS": int(passed.sum()), "robustness_failed_cases": failed.to_dict("records"),
        "materiality_limits": tolerances, "raw_2050_cost_authority": "PINNED_LOCAL_HORIZON_INPUT",
        "2040_cost_authority": "SOLE_LOCAL_2050_REFERENCE_TRANSFER_NOT_ROBUST_IN_CNOR_BASE_HIGH",
        "resource_class_discretization_final_authority": False,
        "RESOURCE_CLASS_DISCRETIZATION_NOT_USED_AS_FINAL_SITING_AUTHORITY": True,
        "ONSHORE_AND_OFFSHORE_USE_COMMON_FIXED_CAPACITY_SITE_LCOE_FRAMEWORK": True,
        "parent_hash_verification": parents, "parent_hash_changes": 0,
        "prior_accepted_class_resource_and_protected_audits_reused": True,
        "W_successor_networks_created": 0,
        "successor_phases": {phase: state["phases"][phase]["status"] for phase in ("H","N","C","F")},
        "optimization_model_constructed": False, "production_optimization_executed": False,
        "production_solver_invocations": 0, "solver_invocations": 0, "new_weather_downloads": 0,
        "production_inputs_modified": 0, "production_results_modified": 0, "solved_networks_modified": 0}
    dump_json(QA / "NATIVE_CELL_CASE_RECONSTRUCTION_QA.json", {"status":"PASS", "cases": case_checks})
    dump_json(QA / "FINAL_WIND_VERIFICATION.json", result)
    return result


def checkpoint_native_wind_blocked():
    """Pin the new residual only; do not rerun accepted resource/model checks."""
    final = json.loads((QA / "FINAL_WIND_VERIFICATION.json").read_text())
    tests = json.loads((QA / "NATIVE_CELL_TEST_RESULTS.json").read_text())
    independent = json.loads((QA / "INDEPENDENT_NATIVE_CELL_REVIEW.json").read_text())
    state = json.loads(STATE.read_text())
    if (final["state"] != "WIND_RESOURCE_SITING_BLOCKED" or final["case_checks_PASS"] != 84 or
            tests["status"] != "PASS" or tests["tests_failed"] != 0 or
            tests["solver_invocations"] != 0 or final["production_solver_invocations"] != 0 or
            independent["independent_verification"]["frozen_cases_checked"] != final["case_checks_PASS"]):
        raise RuntimeError("CELL_WIND_BLOCKED_CHECKPOINT_PRECONDITION_FAIL")
    if sha256_file(SPEC) != SPEC_SHA256:
        raise RuntimeError("CLOSURE_SPEC_HASH_FAIL")
    if any(state["phases"][phase]["status"] != "NOT_STARTED" for phase in ("H","N","C","F")):
        raise RuntimeError("CELL_WIND_UNEXPECTED_SUCCESSOR_PHASE_STATE")
    failed_2040 = [row for row in final["robustness_failed_cases"] if row["year"] == 2040]
    if not failed_2040:
        raise RuntimeError("CELL_WIND_2040_RESIDUAL_NOT_PRESENT")
    unresolved = [{"state":"2040_OFFSHORE_COST_AUTHORITY_REQUIRED", "gate":"W_RESOLUTION_6",
        "scope":"2040_CNOR_WIND_OFFSHORE_BASE_HIGH",
        "decision_required":"Provide/approve a controlling 2040 offshore siting-cost basis, or explicitly authorize the pinned local 2050 reference for 2040 despite the documented CNOR hourly-profile sensitivity.",
        "evidence":"qa/final_methodology_closure/wind/OFFSHORE_COST_RANKING_ROBUSTNESS.csv",
        "failed_2040_cases":failed_2040, "direct_2050_horizon_authority_exists":True,
        "old_4_vs_8_failures_are_not_current_blockers":True}]
    receipt_path = QA / "NATIVE_CELL_SITING_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text())
    receipt.update({"phase_result":"WIND_RESOURCE_SITING_BLOCKED", "physical_allocation_profile_QA":"PASS",
        "final_verification":"qa/final_methodology_closure/wind/FINAL_WIND_VERIFICATION.json",
        "final_verification_sha256":sha256_file(QA / "FINAL_WIND_VERIFICATION.json"),
        "current_residual":unresolved, "tests_passed":tests["tests_passed"],
        "2050_cost_authority":"DIRECT_PINNED_LOCAL_2050_HORIZON_INPUT",
        "2040_cost_authority":"UNRESOLVED_TRANSFER_OF_2050_REFERENCE",
        "cost_stress_failures_not_resource_class_convergence":True})
    dump_json(receipt_path,receipt)
    authority_path = QA / "LOCAL_PINNED_SITE_COST_AUTHORITY.json"
    authority = json.loads(authority_path.read_text())
    authority["status"] = "LOCAL_PINNED_2050_AUTHORITY_RECOVERED__2040_REUSE_ROBUSTNESS_FAILED"
    authority["horizon_policy"] = "Pinned raw 2050 costs provide direct 2050 horizon authority. Reuse for 2040 requires robustness or explicit project acceptance; CNOR 2040 Base/High fail the declared stress gate. The 2050 stress failures remain sensitivity diagnostics, not missing horizon authority."
    authority["robustness_results"] = {"cases":126,"PASS":final["robustness_PASS"],
        "failed":final["robustness_failed_cases"],"final_verification":str((QA / "FINAL_WIND_VERIFICATION.json").relative_to(ROOT))}
    dump_json(authority_path,authority)
    handoff = ROOT / "docs/final_methodology_closure/MEM_FINAL_METHODOLOGY_PHASE_W_NATIVE_CELL_HANDOFF.md"
    manifest = QA / "NATIVE_CELL_WIND_OUTPUT_MANIFEST.csv"
    closeout = QA / "NATIVE_CELL_CLOSEOUT_RECEIPT.json"
    paths = list(QA.glob("*.csv"))+list(QA.glob("*.json"))
    paths += list(RESOURCE.glob("*.nc"))+list(RESOURCE.glob("*.geojson"))+list(RESOURCE.glob("*.parquet"))
    paths += [SPEC,handoff,ROOT / "docs/final_methodology_closure/PHASE_W_NATIVE_CELL_RESOLUTION.md",
        ROOT / "config/final_methodology_closure.yaml"]
    paths += [ROOT / f"src/mem_model/{name}.py" for name in (
        "final_methodology_closure","final_wind_cells","final_wind_cell_verification",
        "final_wind_resource","final_wind_diagnostics","final_wind_verification")]
    paths += [ROOT / f"tests/{name}.py" for name in (
        "test_final_methodology_closure","test_final_wind_cells","test_final_wind_cell_verification",
        "test_final_wind_resource","test_final_wind_verification")]
    paths = sorted(set(paths)-{manifest,closeout,QA / "WIND_RESOURCE_OUTPUT_MANIFEST.csv"},key=lambda path:str(path).casefold())
    def artifact_role(path):
        if path.name in {"W_CLASS_DIAGNOSTIC_CHECKPOINT.json","WIND_RESOURCE_SITING_GATE_RECEIPT.json"}:
            return "SUPERSEDED_PRE_RESOLUTION_GATE_RETAINED_FOR_HISTORY"
        if path.name == "OFFSHORE_NATIVE_COST_REFERENCE.json":
            return "HISTORICAL_PROCESSED_COST_DIAGNOSTIC_NOT_CURRENT_AUTHORITY"
        if path == SPEC or path.name == "PHASE_W_NATIVE_CELL_RESOLUTION.md":
            return "CONTROLLING_PROJECT_AUTHORITY"
        if path.suffix in {".py",".yaml"}:
            return "CURRENT_PHASE_W_IMPLEMENTATION_CONFIG_OR_TEST"
        if (path.name.startswith(("NATIVE_CELL","FINAL_WIND","ONSHORE_SITING","OFFSHORE_SITE","OFFSHORE_COST_RANKING","LOCAL_PINNED","INDEPENDENT_NATIVE_CELL","RESOURCE_CLASS_TO_NATIVE")) or
                "SELECTED_NATIVE_CELL_PROFILES" in path.name or path == handoff):
            return "CURRENT_NATIVE_CELL_EVIDENCE_NOT_PROMOTED_NETWORK"
        return "RETAINED_CLASS_SOURCE_DIAGNOSTIC_NOT_FINAL_SITING_AUTHORITY"
    records = [{"path":str(path.relative_to(ROOT)),"bytes":path.stat().st_size,"sha256":sha256_file(path),
        "role":artifact_role(path)} for path in paths]
    pd.DataFrame(records).to_csv(manifest,index=False)
    dump_json(closeout,{"state":"WIND_RESOURCE_SITING_BLOCKED", "current_residual":unresolved,
        "physical_and_cached_output_QA":"PASS", "native_cell_cases_PASS":84,
        "independent_cases_PASS":independent["independent_verification"]["frozen_cases_checked"],
        "focused_tests":tests, "manifest":str(manifest.relative_to(ROOT)),"manifest_sha256":sha256_file(manifest),
        "manifest_members":len(records),"parent_hash_changes":0,"W_successors_created":0,
        "handoff":str(handoff.relative_to(ROOT)),"handoff_sha256":sha256_file(handoff),
        "optimization_model_constructed":False,"production_optimization_executed":False,
        "production_solver_invocations":0,"production_results_modified":0,"production_state":"PRODUCTION_NOT_EXECUTED"})
    paths += [manifest,closeout]
    state = record_phase("W","BLOCKED",parents=state["controlling_parents"],artifacts=paths,
        decisions=["EXPLICIT_NATIVE_CELL_RESOLUTION_ACCEPTED",
            "ONSHORE_AND_OFFSHORE_USE_COMMON_FIXED_CAPACITY_SITE_LCOE_FRAMEWORK",
            "ONSHORE_COMMON_COST_LCOE_EQUIVALENT_TO_CF_PRIORITY",
            "OFFSHORE_NATIVE_AC_SITE_LCOE_WITHOUT_PRICE_ASSUMPTION",
            "EXACT_FROZEN_ZONAL_MW_PHYSICAL_CELL_CAPS_PARTIAL_MARGINAL_CELL",
            "RESOURCE_CLASS_DISCRETIZATION_NOT_USED_AS_FINAL_SITING_AUTHORITY",
            "RETAIN_ALL_OLD_4_VS_8_DIAGNOSTIC_FAILURES_UNRELAXED",
            "REUSE_ACCEPTED_WEATHER_GEOGRAPHY_RESOURCE_CONVERSION_AND_PROTECTED_AUDITS",
            "DIRECT_2050_COST_AUTHORITY_RECOVERED_2040_TRANSFER_REQUIRES_RESIDUAL_DECISION"],
        tests={"focused_non_solving":tests,"native_cell_case_checks_PASS":84,
            "offshore_robustness_PASS":122,"offshore_robustness_NOT_PASS":4,
            "independent_review":"qa/final_methodology_closure/wind/INDEPENDENT_NATIVE_CELL_REVIEW.json",
            "prior_accepted_focused_tests_reused":21},unresolved=unresolved)
    state["phases"]["W"].update({"phase_result":"WIND_RESOURCE_SITING_BLOCKED",
        "current_handoff":str(handoff.relative_to(ROOT)),"current_gate":"2040_OFFSHORE_COST_AUTHORITY_REQUIRED",
        "superseded_block":"qa/final_methodology_closure/wind/W_CLASS_DIAGNOSTIC_CHECKPOINT.json",
        "resource_class_discretization_final_authority":False})
    dump_json(STATE,state)
    for item in state["phases"]["W"]["created_artifacts"]:
        if sha256_file(ROOT / item["path"]) != item["sha256"]:
            raise RuntimeError("CELL_WIND_CHECKPOINT_HASH_FAIL")
    return {"state":state["state"],"next_phase":state["next_phase"],
        "phase_W":"WIND_RESOURCE_SITING_BLOCKED","current_gate":"2040_OFFSHORE_COST_AUTHORITY_REQUIRED",
        "manifest_members":len(records),"checkpoint_artifact_hashes_verified":len(paths),
        "focused_non_solving_tests_passed":tests["tests_passed"],"production_solver_invocations":0}

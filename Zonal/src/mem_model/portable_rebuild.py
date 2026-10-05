"""Deterministic, offline Path-B reconstruction of the accepted final family.

This entry point uses frozen runtime/control tables and the existing pure
transformations. It never calls historical build/promotion/state wrappers,
downloads research inputs, creates an optimization model, or invokes a solver.
Only dedicated candidate outputs are written; accepted networks are read-only.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Iterable
from unittest.mock import patch

import numpy as np
import pandas as pd
import pypsa

from .common import ROOT, SCENARIOS, STATIC, ZONES, safe_id, sha256_file
from . import network as baseline
from . import stage_b_p2x_flex as p2x
from . import stage_b_uc1 as uc1
from . import stage_b_uc2a as uc2a
from . import stage_b_zonal_vre as zonal
from . import final_hydro_spatial as hydro
from . import final_methodology_networks as final
from . import final_network_signed as signed

VERSION = "MEM_FINAL_REBUILD_V1"
DEFAULT_OUTPUT = ROOT / "outputs/rebuild"
CROSSWALK = ROOT / "qa/stage_b/uc2a_heterogeneous_v1/MEM_UC2A_CHILD_CROSSWALK.csv"
CENSUS = ROOT / "qa/final_methodology_closure/hydro/HYDRO_CURRENT_LINEAGE_AND_STATE_CENSUS.csv"
ZONAL = ROOT / "runtime_inputs/stage_b_zonal_vre_v1/MEM_STAGE_B_ZONAL_VRE_2019_HOURLY.parquet"
WIND = ROOT / "runtime_inputs/final_methodology_closure/wind/horizon_cost_v1/HORIZON_SPECIFIC_NATIVE_CELL_EFFECTIVE_WIND_2019.parquet"
INFLOW = ROOT / "runtime_inputs/final_methodology_closure/hydro/zonal_runoff_v1/ZONAL_NATURAL_INFLOW_CANDIDATE_2019.parquet"
_ISOLATION: ContextVar[dict | None] = ContextVar("final_rebuild_isolation", default=None)
_HOOK_INSTALLED = False


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _audit_hook(event: str, args: tuple) -> None:
    policy = _ISOLATION.get()
    if policy is not None and event in {"socket.connect", "socket.getaddrinfo"}:
        raise RuntimeError("REBUILD_REBUILD_NETWORK_ACCESS_FORBIDDEN")
    if policy is None or event not in {"open", "os.listdir", "os.scandir"} or not args:
        return
    value = args[0]
    if not isinstance(value, (str, bytes, os.PathLike)):
        return
    # abspath does not read the filesystem and cannot recursively trigger open.
    path = Path(os.path.abspath(os.fsdecode(value)))
    if _within(path, policy["workspace"]) and not any(_within(path, allowed) for allowed in policy["allowed"]):
        policy["blocked_accesses"].append({"event": event, "path": str(path)})
        raise RuntimeError(f"REBUILD_ORIGINAL_WORKSPACE_ACCESS_FORBIDDEN: {path}")
    policy["audited_accesses"] += 1


@contextmanager
def isolated_filesystem(data_root: Path, *, candidate_root: Path = ROOT,
                        workspace_root: Path | None = None) -> Iterable[dict]:
    """Block Python filesystem reads in the old workspace outside allowed roots.

    Explicit input/output containment checks also cover paths handed to native
    Parquet/NetCDF readers, which need not emit Python audit events. Installed
    dependencies and the Python runtime outside the workspace remain readable.
    """
    global _HOOK_INSTALLED
    if not _HOOK_INSTALLED:
        sys.addaudithook(_audit_hook)
        _HOOK_INSTALLED = True
    candidate_root, data_root = candidate_root.resolve(), data_root.resolve()
    policy = {"workspace": (workspace_root or candidate_root.parent).resolve(),
              "allowed": (candidate_root, data_root), "blocked_accesses": [], "audited_accesses": 0}
    token = _ISOLATION.set(policy)
    try:
        yield policy
    finally:
        _ISOLATION.reset(token)


def _output_root(path: Path) -> Path:
    path = path.resolve()
    if not any(_within(path, root.resolve()) for root in (ROOT / "outputs/rebuild", ROOT / "outputs/rebuild_validation", ROOT / "qa/validation", ROOT / "qa/installation_validation")):
        raise ValueError("REBUILD_OUTPUT_MUST_BE_UNDER_OUTPUTS_OR_VALIDATION")
    return path


def _selected_case(frame: pd.DataFrame, year: int, scenario: str) -> pd.DataFrame:
    return frame.loc[frame.year.eq(year) & frame.scenario.eq(scenario)].copy()


def apply_p2x(parent: pypsa.Network, year: int, scenario: str,
              contract_root: Path) -> pypsa.Network:
    """Reproduce corrected rigid load, shedding caps and native P2X equality.

    The same exact annual-scaling function, frozen shares and _add_p2x adapter
    used in the accepted implementation are reused; no runtime hash wrapper runs.
    """
    output = parent.copy()
    totals = p2x.scenario_totals(year, scenario)
    controls = pd.read_csv(STATIC / "MEM_Annual_Zonal_Demand_Contract.csv")
    controls = _selected_case(controls, year, scenario).set_index("zone")
    if set(controls.index) != set(ZONES) or controls.index.has_duplicates:
        raise RuntimeError("REBUILD_P2X_FROZEN_ZONAL_CONTROL_COVERAGE_FAIL")
    rows = []
    for zone in ZONES:
        share = float(controls.at[zone, "frozen_2025_share"])
        rigid = float(totals["rigid_TWh"]) * 1e6 * share
        load_id, shed_id = f"LOAD_{zone}", f"LOAD_SHEDDING_{zone}"
        values = output.loads_t.p_set[load_id].to_numpy()
        expected_total = float(controls.at[zone, "annual_zonal_demand_MWh"])
        if not np.isclose(values.sum(), expected_total, rtol=0, atol=1e-3):
            raise RuntimeError("REBUILD_P2X_BASELINE_LOAD_CONTROL_FAIL")
        corrected = p2x._exact_scaled_profile(values, rigid)
        output.loads_t.p_set[load_id] = corrected
        peak = float(corrected.max())
        output.generators.at[shed_id, "p_nom"] = peak
        output.generators_t.p_max_pu[shed_id] = corrected / peak
        rows.append({"year": year, "scenario": scenario, "zone": zone,
                     "p2x_power_MW": float(totals["p2x_power_MW"]) * share,
                     "annual_p2x_MWh": float(totals["p2x_TWh"]) * 1e6 * share})
    directory = contract_root / str(year)
    directory.mkdir(parents=True, exist_ok=True)
    # Accepted build_runtime explicitly serialized this adapter at 15 digits.
    pd.DataFrame(rows).to_csv(directory / "p2x_contract.csv", index=False, lineterminator="\n", float_format="%.15g")
    with patch.object(p2x, "RUNTIME_ROOT", contract_root):
        p2x._add_p2x(output, year, scenario)
    output.meta.update({"model_version": "P2X_FLEX_V1", "baseline_status": "PRE_P2X_FLEX_DIAGNOSTIC_BASELINE",
                        "p2x_formulation": "NEGATIVE_SIGN_GENERATOR_PER_ZONE_OPERATIONAL_LIMIT_EQUALITY",
                        "full_year_solve_authorized": False, "production_authorization": "MANUAL_ONLY_AFTER_BUILD_QA"})
    return output


def load_crosswalk(path: Path = CROSSWALK) -> pd.DataFrame:
    # Default pandas parsing can round a frozen 17-digit scalar by one ULP.
    # Do not re-estimate child sizes/efficiencies from external PPM research.
    frame = pd.read_csv(path, float_precision="round_trip")
    required = {"year", "scenario", "parent_id", "v1_child_id", "child_id", *uc2a.OVERRIDE_FIELDS}
    if not required.issubset(frame.columns) or frame.child_id.duplicated().any():
        raise RuntimeError("REBUILD_ACCEPTED_UC2A_CROSSWALK_FAIL")
    return frame


def apply_wind(parent: pypsa.Network, profiles: pd.DataFrame,
               year: int, scenario: str) -> pypsa.Network:
    output = parent.copy()
    case = _selected_case(profiles, year, scenario)
    names = parent.generators.index[parent.generators.carrier.isin(("wind_onshore", "wind_offshore"))]
    if len(names) != 14 or parent.generators.loc[names].committable.any():
        raise RuntimeError("REBUILD_WIND_TARGET_SCOPE_FAIL")
    for name in names:
        row = parent.generators.loc[name]
        technology = "WIND_ONSHORE" if row.carrier == "wind_onshore" else "WIND_OFFSHORE"
        selected = case.loc[case.zone.eq(row.bus) & case.technology.eq(technology)].sort_values("snapshot")
        if len(selected) != 8760 or not _profile_times(selected).equals(parent.snapshots):
            raise RuntimeError("REBUILD_WIND_PROFILE_CHRONOLOGY_FAIL")
        output.generators_t.p_max_pu[name] = selected.p_max_pu.to_numpy()
    output.meta.update({"final_wind_version": "NATIVE_CELL_SITE_LCOE_V1", "wind_weather_year": 2019,
                        "wind_siting_scenario_specific": True,
                        "ONSHORE_AND_OFFSHORE_USE_COMMON_FIXED_CAPACITY_SITE_LCOE_FRAMEWORK": True,
                        "production_executed_by_final_closure": False})
    return output


def _profile_times(frame: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(frame.snapshot, utc=True)).tz_localize(None)


def apply_hydro(parent: pypsa.Network, profiles: pd.DataFrame, census_all: pd.DataFrame,
                year: int, scenario: str) -> tuple[pypsa.Network, dict, pd.DataFrame]:
    output = parent.copy()
    case = _selected_case(profiles, year, scenario)
    census = _selected_case(census_all, year, scenario)
    if case.hydro_id.nunique() != 24 or len(case) != 24 * 8760:
        raise RuntimeError("REBUILD_HYDRO_NATURAL_PROFILE_COVERAGE_FAIL")
    for row in census.loc[census.annual_inflow_MWh_water.gt(0)].itertuples():
        selected = case.loc[case.hydro_id.eq(row.runtime_id)].sort_values("snapshot")
        if not _profile_times(selected).equals(parent.snapshots):
            raise RuntimeError("REBUILD_HYDRO_PROFILE_CHRONOLOGY_FAIL")
        water = selected.inflow_MW_water_equivalent.to_numpy()
        if row.hydro_class == "RUN_OF_RIVER":
            output.generators_t.p_max_pu[row.runtime_id] = hydro.ror_turbine_mapping(
                water, row.turbine_MW_NET, row.turbine_efficiency)[0]
        else:
            generator, spill = "INFLOW_" + safe_id(row.runtime_id), "SPILL_" + safe_id(row.runtime_id)
            pu = water / parent.generators.at[generator, "p_nom"]
            output.generators_t.p_min_pu[generator] = pu
            output.generators_t.p_max_pu[generator] = pu
            maximum = max(float(water.max()), float((pu * parent.generators.at[generator, "p_nom"]).max()))
            output.generators.at[spill, "p_nom"] = hydro.spill_safety_cap(parent.generators.at[spill, "p_nom"], maximum)
    output.meta.update({"final_hydro_spatial_v1": {
        "adapter": "ATLITE_RUNOFF_OVER_ACCEPTED_MARKET_ZONE_POLYGONS", "weather_year": 2019,
        "annual_water_control_unchanged": True, "ROR_mapping": "NATIVE_FIXED_NET_TURBINE_SATURATION_WITH_EXPLICIT_BYPASS",
        "water_value": "NATIVE_STORE_ENERGY_BALANCE_DUAL_FIXED_COMMITMENT_LP", "assign_all_duals_required": True,
        "within_zone_natural_classes_share_driver": True, "plant_catchment_routing_required": False,
        "spill_role": "NON_BINDING_FORCED_INFLOW_SAFETY_BOUND", "production_executed": False}})
    changes, checks, ror, hours = hydro.verify_hydro_derivative(parent, output, case, census)
    qa = {"status": "PASS", "natural_inflow_controls": len(checks), "spill_rows_changed": len(changes),
          "maximum_annual_water_residual_MWh": max(abs(r["annual_residual_MWh"]) for r in checks),
          "ror_native_saturation_and_bypass": ror}
    return output, qa, pd.concat(hours, ignore_index=True)


def compare_networks(expected: pypsa.Network, observed: pypsa.Network) -> dict:
    """Compare every component, static attribute and stored hourly input series.

    Row/column ordering and pandas dtype annotations are serialization details.
    Values, component IDs, snapshots, weighting and investment periods are exact.
    Governance/path metadata is assessed separately from model identity.
    """
    pd.testing.assert_index_equal(expected.snapshots, observed.snapshots, check_names=False, exact=False)
    pd.testing.assert_index_equal(expected.investment_periods, observed.investment_periods, check_names=False, exact=False)
    pd.testing.assert_frame_equal(expected.snapshot_weightings, observed.snapshot_weightings,
                                  check_dtype=False, check_freq=False, check_exact=True, check_names=False)
    if {c.name for c in expected.components} != {c.name for c in observed.components}:
        raise RuntimeError("REBUILD_MODEL_COMPONENT_SET_DRIFT")
    if semantic_metadata(expected.meta) != semantic_metadata(observed.meta):
        raise RuntimeError("REBUILD_MODEL_SEMANTIC_METADATA_DRIFT")
    static_cells = dynamic_cells = 0
    components = []
    for component in expected.components:
        other = observed.components[component.name]
        original, rebuilt = component.static.sort_index().sort_index(axis=1), other.static.sort_index().sort_index(axis=1)
        pd.testing.assert_frame_equal(original, rebuilt, check_dtype=False, check_freq=False,
                                      check_exact=True, check_names=False)
        static_cells += original.size
        if set(component.dynamic) != set(other.dynamic):
            raise RuntimeError(f"REBUILD_COMPONENT_DYNAMIC_SCHEMA_DRIFT: {component.name}")
        for field, values in component.dynamic.items():
            pd.testing.assert_frame_equal(values.sort_index(axis=1), other.dynamic[field].sort_index(axis=1),
                                          check_dtype=False, check_freq=False, check_exact=True, check_names=False)
            dynamic_cells += values.size
        components.append({"component": component.name, "rows": len(original),
                           "static_fields": len(original.columns), "dynamic_fields": len(component.dynamic)})
    return {"status": "PASS", "comparison": "EXACT_ALL_MODEL_COMPONENT_CELLS",
            "snapshots": len(expected.snapshots), "static_cells": static_cells,
            "dynamic_cells": dynamic_cells, "components": components,
            "metadata_comparison": "SEPARATE_GOVERNANCE_IDENTITY_AND_PORTABLE_INPUT_PROVENANCE"}


def semantic_metadata(meta: dict) -> dict:
    """Retain methodology/reporting semantics; separate path/hash provenance."""
    provenance = {"runtime_bundle", "runtime_manifest_sha256", "runtime_authority",
                  "p2x_runtime_manifest_sha256", "uc1_parent", "final_methodology_parent_sha256",
                  "portable_rebuild", "portable_runtime_provenance"}
    values = {key: value for key, value in meta.items() if key not in provenance}
    if "final_hydro_spatial_v1" in values:
        values["final_hydro_spatial_v1"] = {key: value for key, value in values["final_hydro_spatial_v1"].items()
                                             if key not in {"parent_sha256", "runoff_sha256"}}
    return values


def model_identity(network: pypsa.Network) -> str:
    """Stable content identity excluding filesystem/governance metadata."""
    digest = hashlib.sha256()
    for label, frame in [("snapshot_weightings", network.snapshot_weightings)]:
        digest.update(label.encode())
        digest.update(frame.to_csv(float_format="%.17g", lineterminator="\n").encode())
    digest.update(json.dumps(semantic_metadata(network.meta), sort_keys=True, separators=(",", ":")).encode())
    for component in network.components:
        for field, frame in [("static", component.static), *sorted(component.dynamic.items())]:
            digest.update(f"{component.name}/{field}".encode())
            digest.update(frame.sort_index().sort_index(axis=1).to_csv(float_format="%.17g", lineterminator="\n").encode())
    return digest.hexdigest()


def _dependencies() -> list[Path]:
    return [CROSSWALK, CENSUS, ZONAL, WIND, INFLOW,
            *[STATIC / name for name in ("MEM_carriers_static_final.csv", "MEM_generators_static_final.csv",
               "MEM_storage_static_final.csv", "MEM_Interzonal_Static_Contract.csv",
               "MEM_External_Interface_Static_Contract.csv", "MEM_Annual_Zonal_Demand_Contract.csv")],
            *[ROOT / "config" / name for name in ("stage_b_2040_runtime_contract.yaml", "stage_b_2050_runtime_contract.yaml",
                                                  "stage_b_p2x_flex_v1.yaml", "stage_b_uc1_common.yaml")],
            *[ROOT / "runtime_inputs" / directory / filename for directory in ("accepted", "accepted_2050")
              for filename in baseline.HOURLY_FILES.values()]]


def rebuild(data_root: Path, *, output_root: Path = DEFAULT_OUTPUT,
            cases: tuple[tuple[int, str], ...] = SCENARIOS) -> dict:
    data_root, output_root = data_root.resolve(), _output_root(output_root)
    if not cases or len(set(cases)) != len(cases) or any(case not in SCENARIOS for case in cases):
        raise ValueError("REBUILD_INVALID_CASE_SELECTION")
    frozen_root = data_root / "path_a/networks/unsolved/final_methodology_v1"
    inputs = _dependencies()
    if any(not _within(path.resolve(), ROOT.resolve()) for path in inputs):
        raise RuntimeError("REBUILD_SELECTED_INPUT_ESCAPES_CANDIDATE")
    missing = [str(path) for path in inputs if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"REBUILD_SELECTED_INPUTS_MISSING: {missing}")
    output_root.mkdir(parents=True, exist_ok=True)
    receipt = {"schema_version": VERSION, "status": "IN_PROGRESS", "cases": [],
               "optimizer_invocations": 0, "optimization_models_constructed": 0,
               "production_execution": False, "weather_conversion": False, "research_downloads": False,
               "historic_state_wrappers_called": False,
               "pure_transformations": ["network.build_network", "p2x.scenario_totals/_exact_scaled_profile/_add_p2x",
                   "uc1.plan_decomposition/derive_network", "uc2a.derive(accepted_crosswalk_six_override_fields)",
                   "zonal_vre.apply_profiles", "final_wind_cells.promote_wind_successors:p_max_pu_loop",
                   "final_hydro_spatial.ror_turbine_mapping/spill_safety_cap/verify_hydro_derivative",
                   "final_network_signed.convert_interfaces", "final_methodology_networks.continuous_counterfactual"],
               "input_manifest": [{"path": str(p.relative_to(ROOT)), "sha256": sha256_file(p), "bytes": p.stat().st_size} for p in inputs],
               "bounds": {"comparison": "all snapshots and all static/dynamic model component cells",
                          "accepted_methodology_changed": False, "source_workspace_read_authorized": False,
                          "original_networks_overwritten": False, "metadata_is_model_identity": False}}
    qa_path = output_root / "RECONSTRUCTION_VALIDATION.json"
    def save():
        qa_path.write_text(json.dumps(receipt, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    save()
    try:
        with isolated_filesystem(data_root) as isolation, zonal.no_models_or_solves(), tempfile.TemporaryDirectory(
                prefix="baseline_", dir=output_root) as temp:
            temporary = Path(temp)
            for year in sorted({y for y, _ in cases}):
                directory = temporary / str(year)
                directory.mkdir()
                for filename in baseline.HOURLY_FILES.values():
                    source = ROOT / "runtime_inputs" / ("accepted" if year == 2040 else "accepted_2050") / filename
                    shutil.copyfile(source, directory / filename)
            crosswalk = load_crosswalk()
            census, zonal_profiles = pd.read_csv(CENSUS), pd.read_parquet(ZONAL)
            wind_profiles, inflows = pd.read_parquet(WIND), pd.read_parquet(INFLOW)
            cfg = uc1.settings()
            for year, scenario in cases:
                stem = f"MEM_{year}_{scenario.upper()}_FINAL_METHODOLOGY_V1"
                paths = [output_root / f"{stem}_8760h_UNSOLVED.nc",
                         output_root / f"{stem}_CONTINUOUS_REFERENCE_8760h_UNSOLVED.nc"]
                if any(path.exists() for path in paths):
                    raise FileExistsError("REBUILD_REBUILD_OUTPUT_OVERWRITE_REFUSED")
                parent, _ = baseline.build_network(temporary / str(year), year, scenario)
                parent = apply_p2x(parent, year, scenario, temporary / "p2x_contracts")
                plan, availability = uc1.plan_decomposition(parent, year, scenario, cfg)
                v1 = uc1.derive_network(parent, plan, year, scenario)
                uc1.verify_derivative(parent, v1, plan)
                child_plan = _selected_case(crosswalk, year, scenario)
                if set(child_plan.v1_child_id) != set(plan.child_id) or len(child_plan) != final.EXPECTED_COUNTS[(year, scenario)]:
                    raise RuntimeError("REBUILD_UC2A_ACCEPTED_CROSSWALK_COHORT_FAIL")
                v2a = uc2a.derive(v1, child_plan)
                # Preserve exactly the accepted static override scope and count.
                uc2a.verify_structure(v1, v2a, child_plan)
                spatial = zonal.apply_profiles(v2a, zonal_profiles)
                zonal.verify_derivative(v2a, spatial, zonal_profiles)
                windy = apply_wind(spatial, wind_profiles, year, scenario)
                watery, hydro_qa, ror_hours = apply_hydro(windy, inflows, census, year, scenario)
                uc, interfaces = signed.convert_interfaces(watery)
                uc.meta.update({"final_methodology_variant": "final_v1", "production_optimization_executed": False,
                                "portable_rebuild": VERSION,
                                "portable_runtime_provenance": f"path_b/runtime_inputs/{'accepted' if year == 2040 else 'accepted_2050'}"})
                uc.meta["runtime_bundle"] = f"path_b/runtime_inputs/{'accepted' if year == 2040 else 'accepted_2050'}"
                uc.meta["runtime_authority"] = f"ACCEPTED_FROZEN_PORTABLE_{year}"
                uc.meta["runtime_manifest_sha256"] = None
                ref = final.continuous_counterfactual(uc, parent, child_plan)
                uc_qa = final.structural_check(uc, year, scenario)
                ref_qa = final.structural_check(ref, year, scenario, continuous=True)
                checks = []
                for prepared, path in zip((uc, ref), paths):
                    accepted_path = frozen_root / path.name
                    if not _within(accepted_path.resolve(), data_root):
                        raise RuntimeError("REBUILD_FROZEN_INPUT_ESCAPE")
                    accepted = pypsa.Network(accepted_path)
                    check = compare_networks(accepted, prepared)
                    prepared.export_to_netcdf(path)
                    reloaded = pypsa.Network(path)
                    compare_networks(prepared, reloaded)
                    compare_networks(accepted, reloaded)
                    check.update({"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path),
                                  "model_identity_sha256": model_identity(reloaded),
                                  "accepted_frozen_sha256": sha256_file(accepted_path), "export_reload": "EXACT_PASS",
                                  "accepted_governance_identity": accepted.meta,
                                  "rebuilt_portable_provenance": reloaded.meta})
                    checks.append(check)
                ror_path = output_root / f"MEM_{year}_{scenario.upper()}_ROR_ACCESSIBLE_AND_BYPASS_2019.parquet"
                ror_hours.to_parquet(ror_path, index=False)
                receipt["cases"].append({"year": year, "scenario": scenario, "status": "PASS",
                                         "uc": uc_qa, "continuous_reference": ref_qa, "networks": checks,
                                         "hydro": hydro_qa, "uc1_availability_rows": len(availability),
                                         "signed_corridors": int(interfaces.eligible.sum()),
                                         "ror_artifact": str(ror_path.relative_to(ROOT))})
                receipt["isolation"] = {"status": "PASS", "original_workspace_accesses": len(isolation["blocked_accesses"]),
                                        "audited_filesystem_accesses": isolation["audited_accesses"],
                                        "native_reader_paths_explicitly_selected_and_contained": True}
                save()
            changed = [str(p.relative_to(ROOT)) for p, entry in zip(inputs, receipt["input_manifest"]) if sha256_file(p) != entry["sha256"]]
            if changed:
                raise RuntimeError(f"REBUILD_FROZEN_INPUT_CHANGED: {changed}")
        receipt["status"] = "PASS"
        receipt["all_six_cases_validated"] = set(cases) == set(SCENARIOS)
        receipt["frozen_inputs_unchanged"] = True
    except Exception as exc:
        receipt.update({"status": "FAIL_CLOSED", "failure_type": type(exc).__name__, "failure": str(exc)})
        save()
        raise
    save()
    return receipt


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True, help="Locally extracted accepted production-data-v1 release")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--case", action="append", help="Optional YEAR_SCENARIO; default reconstructs all six cases")
    args = parser.parse_args(argv)
    cases = SCENARIOS if args.case is None else tuple((int(v.split("_", 1)[0]), v.split("_", 1)[1].title()) for v in args.case)
    receipt = rebuild(args.data_root, output_root=args.output_root, cases=cases)
    print(json.dumps({"status": receipt["status"], "cases": len(receipt["cases"]), "optimizer_invocations": 0,
                      "receipt": str(args.output_root / "RECONSTRUCTION_VALIDATION.json")}, indent=2))


if __name__ == "__main__":
    main()

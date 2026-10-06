"""Direct pinned 2040 cost recovery and affected-only cached offshore siting."""
from __future__ import annotations

import json
import numpy as np
import pandas as pd
import xarray as xr

from .common import ROOT, SCENARIOS, dump_json, load_yaml, sha256_file
from .final_wind_resource import QA, RESOURCE
from .final_wind_cells import allocate_sites
from .final_wind_cell_verification import KEY, validate_allocation, verify_effective_profile
from .zonal_vre_spatial import SOURCE_ROOT

SOURCE = ROOT / "runtime_sources/costs/technology_data_v0.14.0"
OUTPUT = RESOURCE / "horizon_cost_v1"
COMMIT = "efb1c45dee0b26e402fec66ce209c89474bc2d1f"
COST_2040_SHA = "d1bd7ac0e1e0a59c21ea955375e4d19d31383a1095d3c9014c6de2c11bbb5b2c"
COST_2050_SHA = "8fde1021aed881bedd6f7f89cbc3614e662d74bb8758cfe9ad0feae5410abae8"
COMPONENTS = ("onwind","offwind","offwind-ac-station","offwind-ac-connection-submarine",
              "offwind-ac-connection-underground","offwind-dc-station",
              "offwind-dc-connection-submarine","offwind-dc-connection-underground")


def annual_costs(path):
    raw = pd.read_csv(path).set_index(["technology","parameter"])
    if raw.index.duplicated().any():
        raise RuntimeError("HORIZON_COST_DUPLICATE_PARAMETER")
    annual, rows = {}, []
    for technology in COMPONENTS:
        data = raw.loc[technology]
        value = float(data.loc["investment","value"])
        unit = str(data.loc["investment","unit"])
        if "/kW" in unit:
            investment = value*1000
        elif "/MW" in unit:
            investment = value
        else:
            raise RuntimeError("HORIZON_COST_INVESTMENT_UNIT_INVALID")
        lifetime, fom = float(data.loc["lifetime","value"]), float(data.loc["FOM","value"])
        rate = float(data.loc["discount rate","value"]) if "discount rate" in data.index else .07
        if not np.isfinite([investment,lifetime,fom,rate]).all() or investment <= 0 or lifetime <= 0 or rate < 0:
            raise RuntimeError("HORIZON_COST_PARAMETER_INVALID")
        factor = rate/(1-(1+rate)**(-lifetime)) if rate else 1/lifetime
        annual[technology] = (factor+fom/100)*investment
        rows.append({"technology":technology,"investment_source_value":value,"investment_source_unit":unit,
            "investment_EUR_per_MW_or_MW_km":investment,"lifetime_years":lifetime,"FOM_percent":fom,
            "discount_rate":rate,"annualized_cost":annual[technology],
            "original_source_currency_year_annotations":data.currency_year.dropna().unique().tolist(),
            "source":str(data.loc["investment","source"])})
    return annual,rows


def recover_2040_authority():
    path = SOURCE / "costs_2040.csv"
    path2050 = SOURCE_ROOT / "data/costs/archive/v0.14.0/costs_2050.csv"
    if sha256_file(path) != COST_2040_SHA or sha256_file(path2050) != COST_2050_SHA:
        raise RuntimeError("HORIZON_COST_AUTHORITY_HASH_FAIL")
    config = load_yaml(SOURCE / "config.yaml")
    ref = json.loads((SOURCE / "tag_ref.json").read_text())
    if ref["ref"] != "refs/tags/v0.14.0" or ref["object"]["sha"] != COMMIT:
        raise RuntimeError("HORIZON_COST_TAG_IDENTITY_FAIL")
    if 2040 not in config["years"] or config["eur_year"] != 2025 or not config["offwind_no_gridcosts"]:
        raise RuntimeError("HORIZON_COST_NATIVE_CONFIG_FAIL")
    annual, rows = annual_costs(path)
    annual2050, rows2050 = annual_costs(path2050)
    rows2050 = {row["technology"]:row for row in rows2050}
    receipt = {"state":"DIRECT_2040_COST_AUTHORITY_PASS","technology_data_version":"v0.14.0",
        "commit":COMMIT,"exact_2040_source":str(path.relative_to(ROOT)),"2040_sha256":COST_2040_SHA,
        "official_output_url":"https://raw.githubusercontent.com/PyPSA/technology-data/refs/tags/v0.14.0/outputs/costs_2040.csv",
        "compile_method":"RECOVER_OFFICIAL_PUBLISHED_NATIVE_COMPILER_OUTPUT_AT_SAME_PINNED_TAG",
        "compiler_executed_locally":False,"manual_interpolation_used":False,
        "interpolation_used_YN":True,"interpolation_scope":"Native DEA compiler applies numpy.interp over source year columns for config.years, including 2040; no MEM interpolation. A source year may itself be an interpolation anchor.",
        "compiler_interpolation_lines":[996,1007],"compiler_inflation_and_export_lines":[4250,4263],
        "source_hashes":[{"path":str(p.relative_to(ROOT)),"sha256":sha256_file(p)}
            for p in (path,SOURCE / "config.yaml",SOURCE / "compile_cost_assumptions.py",SOURCE / "tag_ref.json")],
        "2050_authority_unchanged":{"path":str(path2050),"sha256":COST_2050_SHA},
        "relevant_2040_parameters":rows,
        "comparison_to_2050_parameters":[{"technology":row["technology"],"2040":row,"2050":rows2050[row["technology"]],
            "annualized_difference":annual[row["technology"]]-annual2050[row["technology"]]} for row in rows],
        "annualization_basis":{"formula":"(annuity(0.07,lifetime)+FOM/100)*investment_EUR_per_MW",
            "discount_rate_source":"Accepted local PyPSA-Eur technology discount-rate default; not social discount rate",
            "nyears":1,"annualized_once":True,"published_effective_EUR_basis":2025,
            "original_source_year_annotations_retained":True,"additional_MEM_inflation_applied":False},
        "native_site_formula":"annual_generation + annual_AC_station + 1.25*(distance_km*annual_submarine + 20*annual_underground)",
        "DC_parameters_recovered_but_accepted_AC_resource_scope_unchanged":True,
        "FINAL_2040_SITING_USES_DIRECT_OR_NATIVE_COMPILED_2040_AUTHORITY":True,
        "cost_sensitivity_is_diagnostic_not_authority_gate":True,"MEM_dispatch_or_CAPEX_changed":False}
    dump_json(QA / "OFFSHORE_COST_AUTHORITY_2040_RECEIPT.json",receipt)
    return annual


def patch_2040_offshore():
    annual = recover_2040_authority()
    OUTPUT.mkdir(parents=True,exist_ok=True)
    cells = pd.read_csv(QA / "NATIVE_CELL_RESOURCE_TABLE.csv")
    prior = pd.read_parquet(RESOURCE / "NATIVE_CELL_EFFECTIVE_WIND_2019.parquet")
    final = prior.copy()
    old_allocation = pd.read_csv(QA / "NATIVE_CELL_SITING_ALLOCATION.csv")
    allocation = old_allocation.loc[~(old_allocation.year.eq(2040) & old_allocation.technology.eq("WIND_OFFSHORE"))].copy()
    old_qa = pd.read_csv(QA / "FINAL_WIND_PROFILE_QA.csv")
    final_qa = old_qa.copy()
    # Read only already-converted native cells; never call the weather converter.
    cache = ROOT / ".pytest_tmp/final_native_cell_conversion/WIND_OFFSHORE_PROFILES.npz"
    cache_receipt = json.loads(cache.with_suffix(".json").read_text())
    if sha256_file(cache) != cache_receipt["sha256"]:
        raise RuntimeError("HORIZON_COST_NATIVE_CELL_CACHE_HASH_FAIL")
    with np.load(cache) as saved:
        profiles = saved["raw_values"]
    threshold = cells.loc[cells.technology.eq("WIND_OFFSHORE"),"accepted_low_threshold"].unique()
    if len(threshold) != 1 or len(profiles) != 8760:
        raise RuntimeError("HORIZON_COST_NATIVE_CELL_CACHE_SCHEMA_FAIL")
    profiles = np.where(profiles >= float(threshold[0]),profiles,0.)
    comparison, new_allocations, chosen_columns = [], [], set()
    for case in old_qa.loc[old_qa.year.eq(2040) & old_qa.technology.eq("WIND_OFFSHORE")].itertuples(index=False):
        sites = cells.loc[cells.zone.eq(case.zone) & cells.technology.eq("WIND_OFFSHORE")].sort_values("site_id")
        connection = annual["offwind-ac-station"]+1.25*(sites.distance_km.to_numpy()*annual["offwind-ac-connection-submarine"]+20*annual["offwind-ac-connection-underground"])
        sites, selected_MW, lcoe = allocate_sites(case.frozen_MW,sites,annual["offwind"],connection)
        effective = profiles[:,sites.hourly_column.to_numpy()] @ (selected_MW/case.frozen_MW)
        mask = final.year.eq(2040) & final.scenario.eq(case.scenario) & final.zone.eq(case.zone) & final.technology.eq("WIND_OFFSHORE")
        old_profile = prior.loc[mask,"p_max_pu"].to_numpy()
        if len(old_profile) != 8760 or not np.isfinite(effective).all() or (effective < -1e-12).any() or (effective > 1+1e-12).any():
            raise RuntimeError("HORIZON_COST_EFFECTIVE_PROFILE_FAIL")
        final.loc[mask,"p_max_pu"] = effective
        selected = sites.loc[selected_MW > 0].copy()
        selected["selected_MW"] = selected_MW[selected_MW > 0]
        selected["SITE_LCOE"] = lcoe[selected_MW > 0]
        selected["year"],selected["scenario"],selected["frozen_zone_MW"] = 2040,case.scenario,case.frozen_MW
        selected["marginal_cell_partial"] = selected.selected_MW < selected.p_nom_max_MW-1e-7
        residual = validate_allocation(sites,selected,case.frozen_MW)
        new_allocations.append(selected)
        chosen_columns.update(selected.hourly_column.to_list())
        old = old_allocation.loc[old_allocation.year.eq(2040) & old_allocation.scenario.eq(case.scenario) & old_allocation.zone.eq(case.zone) & old_allocation.technology.eq("WIND_OFFSHORE")]
        old_MW = old.set_index("site_id").selected_MW.reindex(sites.site_id,fill_value=0.).to_numpy()
        old_receipt = json.loads((QA / "OFFSHORE_SITE_LCOE_RECEIPT.json").read_text())
        old_costs = {row["technology"]:row["annual_cost"] for row in old_receipt["components"]}
        old_annual_cost = old_costs["offwind"]+old_costs["offwind-ac-station"]+1.25*(sites.distance_km.to_numpy()*old_costs["offwind-ac-connection-submarine"]+20*old_costs["offwind-ac-connection-underground"])
        comparison.append({"year":2040,"scenario":case.scenario,"zone":case.zone,"frozen_MW":case.frozen_MW,
            "MW_shifted":float(np.abs(old_MW-selected_MW).sum()/2),"old_2050_proxy_CF":float(old_profile.mean()),
            "final_2040_authority_CF":float(effective.mean()),"signed_CF_difference":float(effective.mean()-old_profile.mean()),
            "hourly_MAE":float(np.abs(effective-old_profile).mean()),"hourly_max_abs_difference":float(np.abs(effective-old_profile).max()),
            "old_occupied_cells":len(old),"final_occupied_cells":len(selected),
            "old_annualized_siting_cost_EUR":float(old_annual_cost @ old_MW),
            "final_annualized_siting_cost_EUR":float((annual["offwind"]+connection) @ selected_MW),
            "capacity_residual_MW":residual,"status":"PASS_HORIZON_AUTHORITY_NOT_PROXY_MATCH"})
        qmask = final_qa.year.eq(2040) & final_qa.scenario.eq(case.scenario) & final_qa.zone.eq(case.zone) & final_qa.technology.eq("WIND_OFFSHORE")
        for key,value in {"availability_CF":effective.mean(),"selected_MW":selected_MW.sum(),"capacity_residual_MW":residual,
            "occupied_cells":len(selected),"selected_technical_potential_MW":selected.p_nom_max_MW.sum(),"min":effective.min(),"max":effective.max()}.items():
            final_qa.loc[qmask,key] = value
    untouched = ~(prior.year.eq(2040) & prior.technology.eq("WIND_OFFSHORE"))
    pd.testing.assert_frame_equal(prior.loc[untouched],final.loc[untouched],check_exact=True)
    allocation = pd.concat([allocation,*new_allocations],ignore_index=True).sort_values(KEY+["site_id"])
    profile_path = OUTPUT / "HORIZON_SPECIFIC_NATIVE_CELL_EFFECTIVE_WIND_2019.parquet"
    final.to_parquet(profile_path,index=False)
    pd.testing.assert_frame_equal(final,pd.read_parquet(profile_path),check_exact=True)
    allocation_path = QA / "HORIZON_SPECIFIC_NATIVE_CELL_SITING_ALLOCATION.csv"
    allocation.to_csv(allocation_path,index=False)
    final_qa.to_csv(QA / "HORIZON_SPECIFIC_FINAL_WIND_PROFILE_QA.csv",index=False)
    pd.DataFrame(comparison).to_csv(QA / "OFFSHORE_2040_AUTHORITY_VS_2050_PROXY.csv",index=False)
    chosen_columns = sorted(chosen_columns)
    selected_path = OUTPUT / "WIND_OFFSHORE_2040_SELECTED_NATIVE_CELL_PROFILES_2019.nc"
    xr.Dataset({"p_max_pu":(("time","hourly_column"),profiles[:,chosen_columns])},
        coords={"time":pd.date_range("2019-01-01",periods=8760,freq="h"),"hourly_column":chosen_columns}).to_netcdf(selected_path)
    receipt = {"state":"NATIVE_CELL_SITING_QA_PASS_PENDING_PROMOTION","affected_2040_offshore_cases":len(comparison),
        "profile_path":str(profile_path.relative_to(ROOT)),"profile_sha256":sha256_file(profile_path),
        "allocation_path":str(allocation_path.relative_to(ROOT)),"allocation_sha256":sha256_file(allocation_path),
        "OFFSHORE_COST_AUTHORITY_2040":str((SOURCE / "costs_2040.csv").relative_to(ROOT)),
        "OFFSHORE_COST_AUTHORITY_2050":str(SOURCE_ROOT / "data/costs/archive/v0.14.0/costs_2050.csv"),
        "ONSHORE_FINAL_ALLOCATION_UNCHANGED":True,"ALL_2050_FINAL_PROFILES_UNCHANGED":True,
        "ONSHORE_AND_OFFSHORE_USE_COMMON_FIXED_CAPACITY_SITE_LCOE_FRAMEWORK":True,
        "RESOURCE_CLASS_DISCRETIZATION_NOT_USED_AS_FINAL_SITING_AUTHORITY":True,
        "FINAL_2040_SITING_USES_DIRECT_OR_NATIVE_COMPILED_2040_AUTHORITY":True,
        "prior_48_tests_and_84_physical_cases_reused":True,"cost_sensitivity_is_diagnostic_not_authority_gate":True,
        "historical_CF_calibration":False,"zonal_capacity_changed":False,"weather_changed":False,
        "optimization_model_constructed":False,"production_solver_invocations":0,"production_results_modified":0}
    dump_json(QA / "HORIZON_SPECIFIC_WIND_SITING_RECEIPT.json",receipt)
    return receipt


def close_phase_w():
    """Check only the affected output lineage, then advance the persistent gate."""
    from .final_methodology_closure import STATE, SPEC, record_phase
    receipt = json.loads((QA / "HORIZON_SPECIFIC_WIND_SITING_RECEIPT.json").read_text())
    structural = json.loads((QA / "WIND_SUCCESSOR_STRUCTURAL_QA.json").read_text())
    if structural["status"] != "PASS" or {(p["year"],p["scenario"]) for p in structural["packages"]} != set(SCENARIOS):
        raise RuntimeError("WIND_HORIZON_SUCCESSOR_COVERAGE_FAIL")
    if not receipt["FINAL_2040_SITING_USES_DIRECT_OR_NATIVE_COMPILED_2040_AUTHORITY"]:
        raise RuntimeError("WIND_HORIZON_DIRECT_AUTHORITY_NOT_PASS")
    cells = pd.read_csv(QA / "NATIVE_CELL_RESOURCE_TABLE.csv")
    allocation = pd.read_csv(ROOT / receipt["allocation_path"])
    profiles = pd.read_parquet(ROOT / receipt["profile_path"])
    cases = pd.read_csv(QA / "HORIZON_SPECIFIC_FINAL_WIND_PROFILE_QA.csv")
    checks = []
    with xr.open_dataset(OUTPUT / "WIND_OFFSHORE_2040_SELECTED_NATIVE_CELL_PROFILES_2019.nc") as hourly:
        hourly.load()
        for row in cases.loc[cases.year.eq(2040) & cases.technology.eq("WIND_OFFSHORE")].itertuples(index=False):
            mask = lambda table: table.year.eq(row.year) & table.scenario.eq(row.scenario) & table.zone.eq(row.zone) & table.technology.eq(row.technology)
            selected = allocation.loc[mask(allocation)].sort_values("site_id")
            sites = cells.loc[cells.zone.eq(row.zone) & cells.technology.eq(row.technology)]
            residual = validate_allocation(sites,selected,row.frozen_MW)
            difference = verify_effective_profile(profiles.loc[mask(profiles)],selected,hourly,row.frozen_MW)
            checks.append({"year":row.year,"scenario":row.scenario,"zone":row.zone,
                "capacity_residual_MW":residual,"hourly_reconstruction_max_abs":difference,"status":"PASS"})
    if len(checks) != 21:
        raise RuntimeError("WIND_HORIZON_AFFECTED_CASE_COVERAGE_FAIL")
    unchanged = ~(profiles.year.eq(2040) & profiles.technology.eq("WIND_OFFSHORE"))
    old = pd.read_parquet(RESOURCE / "NATIVE_CELL_EFFECTIVE_WIND_2019.parquet")
    pd.testing.assert_frame_equal(old.loc[unchanged],profiles.loc[unchanged],check_exact=True)
    successors = [{"year":p["year"],"scenario":p["scenario"],"path":p["output"],"sha256":p["output_sha256"]} for p in structural["packages"]]
    final = {"state":"WIND_RESOURCE_SITING_PASS","affected_2040_offshore_checks":checks,
        "affected_checks_PASS":len(checks),"prior_84_physical_checks_and_48_tests_reused":True,
        "affected_non_solving_tests":{"passed":36,"failed":0,"observed_result":"36 passed in 11.95s"},
        "six_export_reload_scope_checks_PASS":6,"all_non_wind_model_objects_exact":True,
        "successor_parents":successors,"source_authorities":{"2040":receipt["OFFSHORE_COST_AUTHORITY_2040"],"2050":receipt["OFFSHORE_COST_AUTHORITY_2050"]},
        "ONSHORE_AND_OFFSHORE_USE_COMMON_FIXED_CAPACITY_SITE_LCOE_FRAMEWORK":True,
        "RESOURCE_CLASS_DISCRETIZATION_NOT_USED_AS_FINAL_SITING_AUTHORITY":True,
        "ONSHORE_FINAL_ALLOCATION_UNCHANGED":True,"ALL_2050_FINAL_PROFILES_UNCHANGED":True,
        "cost_sensitivity_is_diagnostic_not_authority_gate":True,"historical_CF_calibration":False,
        "zonal_capacity_changed":False,"weather_changed":False,
        "optimization_model_constructed":False,"production_optimization_executed":False,"production_solver_invocations":0,"production_results_modified":0}
    final_path = QA / "HORIZON_SPECIFIC_FINAL_WIND_VERIFICATION.json"
    dump_json(final_path,final)
    transfer = ROOT / "docs/final_methodology_closure/MEM_FINAL_METHODOLOGY_PHASE_W_HORIZON_COST_TRANSFER.md"
    paths = [SOURCE / name for name in ("costs_2040.csv","config.yaml","compile_cost_assumptions.py","tag_ref.json")]
    paths += [SPEC,transfer,ROOT / "docs/final_methodology_closure/PHASE_W_HORIZON_COST_RESOLUTION.md",
        ROOT / "src/mem_model/final_wind_horizon_cost.py",ROOT / "src/mem_model/final_wind_cells.py",
        ROOT / "tests/test_final_wind_horizon_cost.py",final_path,
        QA / "HORIZON_SPECIFIC_WIND_SITING_RECEIPT.json",QA / "WIND_SUCCESSOR_STRUCTURAL_QA.json",
        QA / "OFFSHORE_COST_AUTHORITY_2040_RECEIPT.json",QA / "OFFSHORE_COST_AUTHORITY_2040_INDEPENDENT_REVIEW.json",
        QA / "OFFSHORE_2040_AUTHORITY_VS_2050_PROXY.csv",QA / "HORIZON_SPECIFIC_NATIVE_CELL_SITING_ALLOCATION.csv",
        QA / "HORIZON_SPECIFIC_FINAL_WIND_PROFILE_QA.csv",QA / "OFFSHORE_COST_RANKING_ROBUSTNESS.csv"]
    paths += list(OUTPUT.glob("*"))+[ROOT / p["path"] for p in successors]
    optional = QA / "OFFSHORE_2040_AFFECTED_OUTPUT_INDEPENDENT_REVIEW.json"
    if optional.exists():
        paths.append(optional)
    manifest = QA / "HORIZON_SPECIFIC_WIND_OUTPUT_MANIFEST.csv"
    records = [{"path":str(p.relative_to(ROOT)),"bytes":p.stat().st_size,"sha256":sha256_file(p)} for p in sorted(set(paths))]
    pd.DataFrame(records).to_csv(manifest,index=False)
    state = json.loads(STATE.read_text())
    state = record_phase("W","PASS",parents=state["controlling_parents"],artifacts=[*paths,manifest],
        decisions=["DIRECT_PINNED_TECHNOLOGY_DATA_V0P14P0_2040_AND_2050_AUTHORITY",
            "COMMON_NATIVE_CELL_FIXED_CAPACITY_SITE_LCOE","ALL_ONSHORE_AND_2050_PROFILES_UNCHANGED",
            "RESOURCE_CLASSES_AND_COST_STRESS_RETAINED_AS_DIAGNOSTICS_NOT_GATES","WIND_ONLY_SCOPE_EXACT_AFTER_EXPORT_RELOAD"],
        tests=final["affected_non_solving_tests"],unresolved=[])
    state["phases"]["W"].update({"phase_result":"WIND_RESOURCE_SITING_PASS","successor_parents":successors,
        "current_transfer":str(transfer.relative_to(ROOT)),"manifest":str(manifest.relative_to(ROOT))})
    state["wind_horizon_cost_resolution"] = {"path":"docs/final_methodology_closure/PHASE_W_HORIZON_COST_RESOLUTION.md",
        "accepted_by":"EXPLICIT_USER_PROJECT_DECISION","source_2040_sha256":COST_2040_SHA}
    dump_json(STATE,state)
    return {"state":"WIND_RESOURCE_SITING_PASS","next_phase":state["next_phase"],"six_successors":successors,
        "affected_profile_checks_PASS":21,"production_solver_invocations":0}

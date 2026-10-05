"""Resource-siting evidence, not an automatically selected MEM economics rule."""
from __future__ import annotations

import json
import subprocess

import numpy as np
import pandas as pd
import xarray as xr

from .common import ROOT, ZONES, load_yaml, dump_json, sha256_file
from .final_wind_resource import QA, RESOURCE, ACCEPTED, SOURCE_ROOT, fixed_mw_allocation, reported_capacity_heuristic
from .zonal_vre_spatial import accepted_region_zones


def native_method_receipt():
    import pypsa
    import atlite
    sources = [SOURCE_ROOT / f"scripts/{name}.py" for name in (
        "build_renewable_profiles", "determine_availability_matrix", "add_electricity", "process_cost_data")]
    sources += [SOURCE_ROOT / "config/config.default.yaml"]
    receipt = {"pypsa": pypsa.__version__, "atlite": atlite.__version__,
        "pypsa_eur_commit": subprocess.check_output(["git", "-c", f"safe.directory={SOURCE_ROOT.as_posix()}",
                                                    "rev-parse", "HEAD"], cwd=SOURCE_ROOT, text=True).strip(),
        "method": "NATIVE_EQUAL_WIDTH_RESOURCE_CLASSES_PLUS_SEPARATE_FIXED_MW_SITING",
        "class_edges": "EXCLUSION_FREE_REGION_CF_MIN_MINUS_0P001_TO_MAX_PLUS_0P001",
        "physical_potential": "ELIGIBLE_AREA_KM2_TIMES_ACCEPTED_DENSITY_NOT_CF_WEIGHTED",
        "hourly_layout": "CELL_MEAN_CF_TIMES_ELIGIBLE_AREA_TIMES_DENSITY",
        "native_reported_capacity_heuristic": "MEAN_PROFILE_TIMES_P_NOM_MAX;NATIVE_0P1_MW_TRUNCATION",
        "native_physical_cap_relaxation_not_adopted": True,
        "offshore": "AC;20_KM_LANDFALL;WEIGHTED_DISTANCE_TO_ASSOCIATED_ONSHORE_REGION",
        "native_investment_preference": "DISPATCH_DEPENDENT_OUTPUT_VALUE_MINUS_ANNUALIZED_INVESTMENT_COST",
        "accepted_MEM_offshore_scalar_siting_objective_found": False,
        "source_files": [{"path": str(p), "sha256": sha256_file(p)} for p in sources],
        "production_optimization_executed": False, "production_solver_invocations": 0}
    if receipt["pypsa"] != "1.2.3":
        raise RuntimeError("CLOSURE_INSTALLED_PYPSA_VERSION_DRIFT")
    dump_json(QA / "LOCAL_NATIVE_METHOD_RECEIPT.json", receipt)
    return receipt


def native_offshore_cost_reference():
    path = SOURCE_ROOT / "resources/eu_context_2013_1w_country_lowres/costs_2050_processed.csv"
    costs = pd.read_csv(path, index_col="technology")
    annual = {}
    provenance = []
    for technology in ("offwind", "offwind-ac-station", "offwind-ac-connection-submarine", "offwind-ac-connection-underground"):
        row = costs.loc[technology]
        rate, lifetime = float(row["discount rate"]), float(row.lifetime)
        annuity = rate/(1-(1+rate)**(-lifetime)) if rate else 1/lifetime
        annual[technology] = (annuity+float(row.FOM)/100)*float(row.investment)
        provenance.append({"technology": technology, "source_investment": float(row.investment),
            "lifetime_years": lifetime, "discount_rate": rate, "FOM_percent": float(row.FOM),
            "native_full_year_annualized_cost": annual[technology]})
    # Existing native coefficient; diagnostics only, not a MEM cost override.
    base = annual["offwind"] + annual["offwind-ac-station"] + 1.25*20*annual["offwind-ac-connection-underground"]
    slope = 1.25 * annual["offwind-ac-connection-submarine"]
    receipt = {"role": "LOCAL_2050_REFERENCE_ECONOMICS_DIAGNOSTIC_ONLY_NOT_ADOPTED_FOR_2040",
        "source": str(path), "source_sha256": sha256_file(path), "nyears": 1,
        "processed_168_HOURS_capital_cost_not_used_directly": True,
        "length_factor": 1.25, "landfall_km": 20, "coefficient_rows": provenance,
        "cost_intercept_EUR_per_MW_year": base,
        "distance_slope_EUR_per_MW_km_year": slope,
        "no_MEM_generator_cost_changed": True}
    dump_json(QA / "OFFSHORE_NATIVE_COST_REFERENCE.json", receipt)
    return base, slope


def historical_plausibility():
    corpus = ROOT.parent / "outputs/01a0595d-8cf1-7202-8ca1-1d3ab732e98e/thermal_stack_phase"
    directory = corpus / "raw/terna/historical_2019_2025_api"
    capacity_path = directory / "renewable-source-capacity_2019_RAW.json"
    generation_path = directory / "renewable-sources-production_2019_RAW.json"
    capacities = pd.DataFrame(json.loads(capacity_path.read_text())["renewable_sources"])
    generation = pd.DataFrame(json.loads(generation_path.read_text())["renewable_sources"])
    capacities = capacities.loc[capacities.source.eq("Eolico") & capacities.year.eq(2019)]
    generation = generation.loc[generation.renewable_source.eq("Eolico") & generation.year.eq(2019)]
    for table, basis, value in ((capacities, "capacity_type", "efficient_power_MW"),
                                 (generation, "production_type", "production_GWh")):
        if table.duplicated(["year", basis, "region", "province"]).any() or table[value].isna().any():
            raise RuntimeError("TERNA_2019_WIND_GRAIN_INVALID")
    normalize = lambda value: "".join(c for c in str(value).casefold() if c.isalnum())
    zones = accepted_region_zones()
    # Official bilingual administrative labels and the Terna API's Italian
    # names are aliases of the same accepted region code, not new geography.
    mapping = {normalize(alias): row.market_zone for row in zones.itertuples(index=False)
               for alias in (row.region_name, row.region_name.split("/", 1)[0])}
    for table in (capacities, generation):
        table["zone"] = table.region.map(normalize).map(mapping)
        if table.zone.isna().any():
            raise RuntimeError(f"TERNA_REGION_ALIAS_UNRESOLVED: {table.loc[table.zone.isna(), 'region'].unique()}")
    output = []
    for basis in ("Netta", "Lorda"):
        c = capacities.loc[capacities.capacity_type.eq(basis)].groupby("zone").efficient_power_MW.sum().reindex(ZONES)
        g = generation.loc[generation.production_type.eq(basis)].groupby("zone").production_GWh.sum().reindex(ZONES)
        for zone in ZONES:
            output.append({"zone": zone, "basis": basis, "year": 2019,
                "year_end_capacity_MW": float(c[zone]), "annual_generation_GWh": float(g[zone]),
                "historical_fleet_annual_ratio": float(g[zone]*1000/(c[zone]*8760)),
                "use": "EXTERNAL_SANITY_NOT_CALIBRATION",
                "limitation": "YEAR_END_CAPACITY_DENOMINATOR;AGGREGATE_EOLICO_NOT_OFFSHORE_SPLIT;REALIZED_HISTORICAL_FLEET_NOT_WEATHER_ONLY_CF"})
    result = pd.DataFrame(output)
    result.to_csv(QA / "TERNA_2019_WIND_PLAUSIBILITY.csv", index=False)
    dump_json(QA / "TERNA_2019_WIND_SOURCE_QA.json", {
        "status": "PASS", "capacity_province_rows_per_basis": len(capacities)//2,
        "generation_province_rows_per_basis": len(generation)//2,
        "region_coverage": int(capacities.region.nunique()), "nulls": 0, "duplicate_keys": 0,
        "source_paths_hashes": [{"path": str(p), "sha256": sha256_file(p)} for p in (capacity_path, generation_path)],
        "observation_not_used_as_profile_target": True})
    return result


def diagnose_siting():
    native_method_receipt()
    historical_plausibility()
    base_cost, cost_slope = native_offshore_cost_reference()
    capacities = pd.read_csv(ROOT / "qa/stage_b/zonal_vre_v1/MEM_STAGE_B_ZONAL_VRE_CAPACITY_COVERAGE.csv")
    capacities = capacities.loc[capacities.technology.isin(("WIND_ONSHORE", "WIND_OFFSHORE"))]
    old = pd.read_parquet(ACCEPTED / "MEM_STAGE_B_ZONAL_VRE_2019_HOURLY.parquet")
    headroom, allocations, comparison, tradeoffs, reference = [], [], [], [], []
    for family in ("WIND_ONSHORE", "WIND_OFFSHORE"):
        table = pd.read_csv(QA / f"{family}_RESOURCE_CLASSES.csv")
        ds = xr.open_dataset(RESOURCE / f"{family}_RESOURCE_CLASSES_2019.nc")
        for zone in ZONES:
            baseline = old.loc[old.zone.eq(zone) & old.technology.eq(family)].sort_values("snapshot").p_max_pu.to_numpy()
            current = ds.profile.sel(class_id=f"{zone}__RC1__C01").to_numpy()
            difference = float(np.max(np.abs(current-baseline)))
            if difference > 1e-12:
                raise RuntimeError(f"WIND_ONE_CLASS_REFERENCE_DRIFT: {family}/{zone}/{difference}")
            reference.append({"technology": family, "zone": zone, "old_CF": float(baseline.mean()),
                "recovered_one_class_CF": float(current.mean()), "hourly_max_abs_difference": difference,
                "snapshots": len(current), "status": "PASS"})
            for count in (1, 4, 8):
                classes = table.loc[table.zone.eq(zone) & table.resource_classes.eq(count)].sort_values("class_number")
                potential, cf = classes.p_nom_max_MW.to_numpy(), classes.annual_CF.to_numpy()
                profiles = ds.profile.sel(class_id=classes.class_id.to_numpy()).to_numpy()
                costs = base_cost+cost_slope*classes.average_distance_km.to_numpy()
                if family == "WIND_OFFSHORE" and count > 1:
                    for a in range(count):
                        for b in range(a+1, count):
                            if cf[b] > cf[a] and costs[b] > costs[a] and potential[a] > 0 and potential[b] > 0:
                                tradeoffs.append({"zone": zone, "resource_classes": count,
                                    "lower_CF_class": classes.iloc[a].class_id, "higher_CF_class": classes.iloc[b].class_id,
                                    "lower_CF": cf[a], "higher_CF": cf[b], "extra_cost_EUR_per_MW_year": costs[b]-costs[a],
                                    "extra_potential_output_MWh_per_MW_year": 8760*(cf[b]-cf[a]),
                                    "break_even_uniform_uncurtailed_EUR_per_MWh": (costs[b]-costs[a])/(8760*(cf[b]-cf[a])),
                                    "cost_vintage": "LOCAL_2050_REFERENCE_DIAGNOSTIC_ONLY",
                                    "hourly_profiles_cross": bool(((profiles[:, a] > profiles[:, b]+1e-12).any()) and
                                                                  ((profiles[:, b] > profiles[:, a]+1e-12).any()))})
                for case in capacities.loc[capacities.zone.eq(zone) & capacities.technology.eq(family)].itertuples(index=False):
                    p = float(case.p_nom_MW)
                    if p > potential.sum()+1e-7:
                        raise RuntimeError("WIND_FROZEN_CAPACITY_EXCEEDS_PHYSICAL_POTENTIAL")
                    headroom.append({"year": case.year, "scenario": case.scenario, "zone": zone, "technology": family,
                        "resource_classes": count, "frozen_MW": p, "physical_potential_MW": potential.sum(),
                        "headroom_MW": potential.sum()-p, "status": "PASS"})
                    by_cf = fixed_mw_allocation(p, potential, cf)
                    effective = profiles @ by_cf / p
                    raw_heuristic, exceeds, truncated = reported_capacity_heuristic(p, potential, cf)
                    for j, (_, row) in enumerate(classes.iterrows()):
                        allocations.append({"year": case.year, "scenario": case.scenario, "zone": zone,
                            "technology": family, "resource_classes": count, "class_id": row.class_id,
                            "p_nom_max_MW": potential[j], "annual_CF": cf[j],
                            "CF_priority_diagnostic_MW": by_cf[j],
                            "native_reported_heuristic_MW": raw_heuristic[j],
                            "native_heuristic_exceeds_physical_cap": bool(exceeds[j]),
                            "native_0P1_MW_truncated_increment_MW": truncated[j],
                            "status": "ONSHORE_EQUAL_COST_ORDER" if family == "WIND_ONSHORE" else "OFFSHORE_CF_ONLY_DIAGNOSTIC_NOT_SELECTED"})
                    by_cost = fixed_mw_allocation(p, potential, -costs) if family == "WIND_OFFSHORE" else by_cf
                    comparison.append({"year": case.year, "scenario": case.scenario, "zone": zone,
                        "technology": family, "resource_classes": count, "one_class_CF": baseline.mean(),
                        "CF_priority_diagnostic_CF": effective.mean(),
                        "cost_priority_diagnostic_CF": float((profiles @ by_cost / p).mean()),
                        "allocation_max_MW_difference_CF_vs_cost": float(np.max(np.abs(by_cf-by_cost))),
                        "CF_priority_native_cost_EUR_per_year": float(np.dot(by_cf, costs)) if family == "WIND_OFFSHORE" else None,
                        "cost_priority_native_cost_EUR_per_year": float(np.dot(by_cost, costs)) if family == "WIND_OFFSHORE" else None,
                        "no_offshore_production_rule_selected": family == "WIND_OFFSHORE"})
        ds.close()
    for name, rows in (("PHYSICAL_POTENTIAL_HEADROOM", headroom), ("ALLOCATION_DIAGNOSTICS_NOT_FINAL", allocations),
                       ("SITING_CRITERION_COMPARISON", comparison), ("OFFSHORE_ECONOMIC_TRADEOFFS", tradeoffs),
                       ("ONE_CLASS_REFERENCE_RECONCILIATION", reference)):
        pd.DataFrame(rows).to_csv(QA / f"{name}.csv", index=False)
    return {"physical_headroom_rows": len(headroom), "one_class_reference_rows": len(reference),
            "offshore_cost_output_tradeoff_pairs": len(tradeoffs),
            "offshore_distinct_CF_vs_cost_allocations": sum(1 for row in comparison if row["technology"] == "WIND_OFFSHORE" and row["resource_classes"] > 1 and row["allocation_max_MW_difference_CF_vs_cost"] > 1e-7),
            "production_optimization_executed": False, "production_solver_invocations": 0}


def profile_convergence(four, eight, tolerances):
    """Declared W5 tolerances, with no retuning to the observed result."""
    four, eight = np.asarray(four, float), np.asarray(eight, float)
    if (four.shape != eight.shape or four.ndim != 1 or
            not np.isfinite(four).all() or not np.isfinite(eight).all()):
        raise ValueError("WIND_CONVERGENCE_PROFILE_INVALID")
    difference = np.abs(four-eight)
    metrics = {"annual_CF_absolute": float(abs(four.mean()-eight.mean())),
               "hourly_MAE": float(difference.mean()),
               "hourly_max_absolute": float(difference.max())}
    return {**metrics, "status": "PASS" if all(
        value <= float(tolerances[key]) for key, value in metrics.items()) else "NOT_CONVERGED"}


def diagnose_convergence():
    """Cached 4/8 comparison, never an implicit offshore siting selection."""
    cfg = load_yaml(ROOT / "config/final_methodology_closure.yaml")
    tolerances = cfg["wind"]["convergence_tolerances_declared_before_results"]
    capacities = pd.read_csv(ROOT / "qa/stage_b/zonal_vre_v1/MEM_STAGE_B_ZONAL_VRE_CAPACITY_COVERAGE.csv")
    base, slope = native_offshore_cost_reference()
    rows = []
    for family in ("WIND_ONSHORE", "WIND_OFFSHORE"):
        table = pd.read_csv(QA / f"{family}_RESOURCE_CLASSES.csv")
        with xr.open_dataset(RESOURCE / f"{family}_RESOURCE_CLASSES_2019.nc") as ds:
            for case in capacities.loc[capacities.technology.eq(family)].itertuples(index=False):
                criteria = ("EQUAL_COST_EXPECTED_OUTPUT",) if family == "WIND_ONSHORE" else (
                    "CF_PRIORITY_DIAGNOSTIC_NOT_SELECTED", "COST_PRIORITY_DIAGNOSTIC_NOT_SELECTED")
                for criterion in criteria:
                    effective, potential, selected = {}, {}, {}
                    for count in (4, 8):
                        classes = table.loc[table.zone.eq(case.zone) & table.resource_classes.eq(count)].sort_values("class_number")
                        score = classes.annual_CF.to_numpy()
                        if criterion == "COST_PRIORITY_DIAGNOSTIC_NOT_SELECTED":
                            score = -(base+slope*classes.average_distance_km.to_numpy())
                        allocation = fixed_mw_allocation(case.p_nom_MW, classes.p_nom_max_MW, score)
                        effective[count] = ds.profile.sel(class_id=classes.class_id.to_numpy()).to_numpy() @ allocation / case.p_nom_MW
                        potential[count] = float(classes.p_nom_max_MW.sum())
                        selected[count] = int((allocation > 1e-7).sum())
                    rows.append({"year": case.year, "scenario": case.scenario, "zone": case.zone,
                        "technology": family, "criterion": criterion,
                        "offshore_rule_selected": False, "four_class_CF": float(effective[4].mean()),
                        "eight_class_CF": float(effective[8].mean()),
                        "four_selected_classes": selected[4], "eight_selected_classes": selected[8],
                        "physical_potential_difference_MW": potential[8]-potential[4],
                        **profile_convergence(effective[4], effective[8], tolerances)})
    result = pd.DataFrame(rows)
    result.to_csv(QA / "FOUR_VS_EIGHT_CLASS_CONVERGENCE.csv", index=False)
    receipt = {"status": "DIAGNOSTIC_ONLY_OFFSHORE_CRITERION_UNRESOLVED",
        "declared_tolerances": tolerances, "tolerances_not_adjusted_to_results": True,
        "rows": len(result), "onshore_rows": int(result.technology.eq("WIND_ONSHORE").sum()),
        "onshore_not_converged": int((result.technology.eq("WIND_ONSHORE") & result.status.ne("PASS")).sum()),
        "more_than_eight_classes_generated": False, "final_class_count_selected": False,
        "production_optimization_executed": False, "production_solver_invocations": 0}
    dump_json(QA / "CONVERGENCE_DIAGNOSTIC_RECEIPT.json", receipt)
    return receipt

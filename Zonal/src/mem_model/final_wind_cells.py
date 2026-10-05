"""Native-cell fixed-MW site-LCOE preprocessing; no dispatch or solve."""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

from .common import ROOT, ZONES, load_yaml, dump_json, sha256_file
from .final_wind_resource import QA, RESOURCE, ACCEPTED, native_offshore_distance_reference, fixed_mw_allocation
from .stage_a.temporal_2019 import _atlite_resource, _cell_area_sqkm
from .zonal_vre_spatial import CUTOUT_PATH


def allocate_sites(capacity, sites, common_cost, connection_cost):
    """Lowest site LCOE; stable cell-ID tie break, no price assumption."""
    sites = sites.assign(connection_cost_EUR_per_MW_year=np.asarray(connection_cost, float)).sort_values("site_id")
    cf = sites.annual_CF.to_numpy()
    eligible = cf > 0
    result = np.zeros(len(sites))
    annual_cost = common_cost+sites.connection_cost_EUR_per_MW_year.to_numpy()
    lcoe = np.divide(annual_cost, 8760*cf, out=np.full(len(cf), np.inf), where=eligible)
    if not np.isfinite(annual_cost).all() or (annual_cost <= 0).any():
        raise ValueError("CELL_SITING_COST_INVALID")
    result[eligible] = fixed_mw_allocation(capacity, sites.p_nom_max_MW.to_numpy()[eligible], -lcoe[eligible])
    return sites, result, lcoe


def recover_cells():
    """Recover only 489/253 eligible cell traces in memory, not dense storage."""
    import atlite
    cfg = load_yaml(ROOT / "config/stage_a_temporal_2019.yaml")
    cutout = atlite.Cutout(str(CUTOUT_PATH))
    cutout.data = cutout.data.sel(x=slice(-6.,28.), y=slice(33.,53.))
    area = _cell_area_sqkm(cutout)
    grid_points = cutout.grid.representative_point().to_crs(3035)
    resources = {}
    tables = []
    scratch = ROOT / ".pytest_tmp/final_native_cell_conversion"
    scratch.mkdir(parents=True, exist_ok=True)
    for key, technology in (("onwind", "WIND_ONSHORE"), ("offwind-ac", "WIND_OFFSHORE")):
        print(f"Recover native eligible hourly cells: {technology}", flush=True)
        params = cfg["vre"]["onwind" if key == "onwind" else "offwind"]
        with xr.open_dataarray(ACCEPTED / f"availability_matrix_zonal_2019_{key}.nc") as source:
            eligibility = source.sel(bus=list(ZONES), x=cutout.coords["x"], y=cutout.coords["y"]).load()
        candidate = np.flatnonzero((eligibility.max("bus") > 0).to_numpy().ravel())
        expected = pd.date_range("2019-01-01", periods=8760, freq="h")
        cache_path = scratch / f"{technology}_PROFILES.npz"
        cache_receipt = cache_path.with_suffix(".json")
        identity = {"source_config_sha256": sha256_file(ROOT / "config/stage_a_temporal_2019.yaml"),
            "eligibility_sha256": sha256_file(ACCEPTED / f"availability_matrix_zonal_2019_{key}.nc"),
            "accepted_cutout_control_reused": "E6CF2B4C9D463D64B4DFB3A613EF20C3C69E99624DAADEEE6EE9C76CF74E9D1F"}
        if cache_path.exists() and cache_receipt.exists():
            prior = json.loads(cache_receipt.read_text())
            if prior["identity"] != identity or sha256_file(cache_path) != prior["sha256"]:
                raise RuntimeError("CELL_TRANSIENT_CONVERSION_CACHE_DRIFT")
            with np.load(cache_path) as cache:
                np.testing.assert_array_equal(candidate,cache["candidate"])
                np.testing.assert_array_equal(expected.values,cache["timestamps"])
                values = cache["raw_values"]
        else:
            raw = (_atlite_resource(cutout, technology, aggregate_time=None)*float(params["correction_factor"]))
            raw = raw.stack(site=["y", "x"]).isel(site=candidate).transpose("time", "site").compute()
            if not pd.DatetimeIndex(raw.time.values).equals(expected):
                raise RuntimeError("CELL_RESOURCE_CHRONOLOGY_FAIL")
            values = raw.to_numpy()
            # Small compressed recovery checkpoint; not a dense production input.
            np.savez_compressed(cache_path, raw_values=values, candidate=candidate, timestamps=expected.values)
            dump_json(cache_receipt, {"identity": identity,"sha256": sha256_file(cache_path),
                "bytes": cache_path.stat().st_size,"role": "TRANSIENT_RECONVERSION_RECOVERY_ONLY"})
        if not np.isfinite(values).all() or (values < 0).any() or (values > 1+1e-12).any():
            raise RuntimeError("CELL_RESOURCE_PROFILE_INVALID")
        # Verify the conversion against already-accepted pre-class mean objects.
        with xr.open_dataset(RESOURCE / f"{technology}_RESOURCE_CLASSES_2019.nc") as ds:
            reference = ds.cell_CF.stack(site=["y", "x"]).isel(site=candidate).to_numpy()
        np.testing.assert_allclose(values.mean(axis=0), reference, atol=1e-12, rtol=1e-12)
        # Same accepted low-output threshold, now at the production resource unit.
        values = np.where(values >= float(params["clip_p_max_pu_below"]), values, 0.)
        land = gpd.read_file(ACCEPTED / "zonal_resource_geometry_onwind.geojson").set_index("bus")
        if key == "offwind-ac":
            land.geometry = land.geometry.map(native_offshore_distance_reference)
        else:
            land.geometry = land.geometry.representative_point()
        land = land.to_crs(3035)
        for zone in ZONES:
            fraction = eligibility.sel(bus=zone).to_numpy().ravel()[candidate]
            members = np.flatnonzero(fraction > 0)
            distances = grid_points.iloc[candidate[members]].distance(land.loc[zone, "geometry"]).to_numpy()/1e3
            for j, distance in zip(members, distances):
                flat = int(candidate[j])
                iy, ix = np.unravel_index(flat, cutout.shape)
                tables.append({"technology": technology, "zone": zone,
                    "site_id": f"{technology}__Y{iy:03d}__X{ix:03d}",
                    "hourly_column": int(j), "y_index": int(iy), "x_index": int(ix),
                    "latitude": float(cutout.coords["y"][iy]), "longitude": float(cutout.coords["x"][ix]),
                    "eligible_area_fraction": float(fraction[j]), "cell_area_km2": float(area.values.ravel()[flat]),
                    "capacity_per_sqkm": float(params["capacity_per_sqkm"]),
                    "p_nom_max_MW": float(fraction[j]*area.values.ravel()[flat]*float(params["capacity_per_sqkm"])),
                    "annual_CF": float(values[:,j].mean()), "raw_CF_before_accepted_threshold": float(reference[j]),
                    "accepted_low_threshold": float(params["clip_p_max_pu_below"]),
                    "distance_km": float(distance), "weather_year": 2019})
        resources[technology] = {"profiles": values, "timestamps": expected}
    cutout.data.close()
    table = pd.DataFrame(tables).sort_values(["technology", "zone", "site_id"])
    if table.duplicated(["technology","zone","site_id"]).any():
        raise RuntimeError("CELL_RESOURCE_DUPLICATE_WITHIN_ZONE")
    for technology in resources:
        classes = pd.read_csv(QA / f"{technology}_RESOURCE_CLASSES.csv")
        expected_potential = classes.loc[classes.resource_classes.eq(1)].set_index("zone").p_nom_max_MW.reindex(ZONES)
        actual = table.loc[table.technology.eq(technology)].groupby("zone").p_nom_max_MW.sum().reindex(ZONES)
        np.testing.assert_allclose(actual, expected_potential, atol=1e-7, rtol=1e-12)
    table.to_csv(QA / "NATIVE_CELL_RESOURCE_TABLE.csv", index=False)
    return table, resources


def site_costs():
    """Raw pinned workflow authority, annualized once, no MEM cost edits."""
    from .zonal_vre_spatial import SOURCE_ROOT
    path = SOURCE_ROOT / "data/costs/archive/v0.14.0/costs_2050.csv"
    if sha256_file(path) != "8fde1021aed881bedd6f7f89cbc3614e662d74bb8758cfe9ad0feae5410abae8":
        raise RuntimeError("CELL_SITING_PINNED_COST_HASH_FAIL")
    raw = pd.read_csv(path).set_index(["technology","parameter"])
    annual = {}
    rows = []
    for technology in ("onwind", "offwind", "offwind-ac-station", "offwind-ac-connection-submarine", "offwind-ac-connection-underground"):
        data = raw.loc[technology]
        investment = float(data.loc["investment", "value"])
        unit = str(data.loc["investment", "unit"])
        if "/kW" in unit:
            investment *= 1000
        elif "/MW" not in unit:
            raise RuntimeError(f"CELL_SITING_COST_UNIT_UNSUPPORTED: {technology}/{unit}")
        lifetime, fom = float(data.loc["lifetime","value"]), float(data.loc["FOM","value"])
        # Same explicit technology discount-rate fill as native prepare_costs.
        rate = float(data.loc["discount rate","value"]) if "discount rate" in data.index else .07
        annuity = rate/(1-(1+rate)**(-lifetime)) if rate else 1/lifetime
        annual[technology] = (annuity+fom/100)*investment
        rows.append({"technology": technology, "investment_EUR_per_MW": investment,
            "lifetime": lifetime, "FOM_percent": fom, "discount_rate": rate,
            "annual_cost": annual[technology], "original_price_annotations":
                data["currency_year"].dropna().astype(str).unique().tolist() if "currency_year" in data.columns else []})
    receipt = {"authority": str(path), "sha256": sha256_file(path),
        "role": "PINNED_LOCAL_WORKFLOW_INPUT;SOLE_AVAILABLE_VINTAGE_REQUIRES_ROBUSTNESS",
        "source_year": 2050, "nyears": 1, "annualized_once": True,
        "components": rows, "common_generation_stress_multipliers": [.75,1.,1.25],
        "stress_not_empirical_vintages": True, "onshore_site_specific_cost_term": False,
        "carrier": "offwind-ac", "DC_or_floating_resources_introduced": False,
        "length_factor": 1.25, "landfall_km": 20., "common_wind_VOM_excluded_from_rank": True,
        "mixed_native_original_price_annotations_retained_without_new_inflation": True,
        "MEM_dispatch_costs_changed": False}
    dump_json(QA / "OFFSHORE_SITE_LCOE_RECEIPT.json", receipt)
    return annual


def prepare_cell_siting():
    """Calculate bounded siting/profile QA; promotion is a separate gate."""
    from .final_wind_diagnostics import profile_convergence
    annual = site_costs()
    cells, resource = recover_cells()
    capacities = pd.read_csv(ROOT / "qa/stage_b/zonal_vre_v1/MEM_STAGE_B_ZONAL_VRE_CAPACITY_COVERAGE.csv")
    cfg = load_yaml(ROOT / "config/final_methodology_closure.yaml")
    tolerances = cfg["wind"]["site_cost_robustness_tolerances_declared_before_stress"]
    allocations, profiles, qa, robustness, comparisons = [], [], [], [], []
    selected_columns = {technology: set() for technology in resource}
    for case in capacities.loc[capacities.technology.isin(resource)].itertuples(index=False):
        sites = cells.loc[cells.zone.eq(case.zone) & cells.technology.eq(case.technology)].sort_values("site_id")
        values = resource[case.technology]["profiles"]
        common = annual["onwind" if case.technology == "WIND_ONSHORE" else "offwind"]
        connection = np.zeros(len(sites)) if case.technology == "WIND_ONSHORE" else (
            annual["offwind-ac-station"]+1.25*(sites.distance_km.to_numpy()*annual["offwind-ac-connection-submarine"]
                                              +20*annual["offwind-ac-connection-underground"]))
        sites, allocation, lcoe = allocate_sites(case.p_nom_MW, sites, common, connection)
        effective = values[:, sites.hourly_column.to_numpy()] @ (allocation/case.p_nom_MW)
        if not np.isfinite(effective).all() or (effective < -1e-12).any() or (effective > 1+1e-12).any():
            raise RuntimeError("CELL_EFFECTIVE_PROFILE_INVALID")
        selected = sites.loc[allocation > 0].copy()
        selected["selected_MW"] = allocation[allocation > 0]
        selected["SITE_LCOE"] = lcoe[allocation > 0]
        selected["year"], selected["scenario"] = case.year, case.scenario
        selected["frozen_zone_MW"] = case.p_nom_MW
        selected["marginal_cell_partial"] = selected.selected_MW < selected.p_nom_max_MW-1e-7
        allocations.extend(selected.to_dict("records"))
        selected_columns[case.technology].update(selected.hourly_column.to_list())
        profiles.append(pd.DataFrame({"year": case.year, "scenario": case.scenario, "zone": case.zone,
            "technology": case.technology, "snapshot": resource[case.technology]["timestamps"].tz_localize("UTC"),
            "p_max_pu": effective}))
        qa.append({"year": case.year, "scenario": case.scenario, "zone": case.zone, "technology": case.technology,
            "frozen_MW": case.p_nom_MW, "selected_MW": float(allocation.sum()),
            "capacity_residual_MW": float(allocation.sum()-case.p_nom_MW),
            "technical_potential_MW": float(sites.p_nom_max_MW.sum()), "occupied_cells": len(selected),
            "selected_technical_potential_MW": float(selected.p_nom_max_MW.sum()),
            "availability_CF": float(effective.mean()), "snapshots": len(effective),
            "min": float(effective.min()), "max": float(effective.max()),
            "physical_bounds_PASS": bool((allocation <= sites.p_nom_max_MW.to_numpy()+1e-7).all()),
            "weather_year": 2019, "status": "PASS"})
        if case.technology == "WIND_OFFSHORE":
            for scale in (.75,1.,1.25):
                _, stress, _ = allocate_sites(case.p_nom_MW, sites, common*scale, sites.connection_cost_EUR_per_MW_year)
                stress_profile = values[:,sites.hourly_column.to_numpy()] @ (stress/case.p_nom_MW)
                robustness.append({"year": case.year, "scenario": case.scenario, "zone": case.zone,
                    "common_generation_cost_scale": scale, "selected_MW_changed": float(np.abs(stress-allocation).sum()/2),
                    **profile_convergence(effective, stress_profile, tolerances),
                    "connection_cost_differentials_unchanged": True, "cost_reference": "PINNED_V0P14P0_2050"})
        class_table = pd.read_csv(QA / f"{case.technology}_RESOURCE_CLASSES.csv")
        with xr.open_dataset(RESOURCE / f"{case.technology}_RESOURCE_CLASSES_2019.nc") as ds:
            for count in (1,4,8):
                group = class_table.loc[class_table.zone.eq(case.zone) & class_table.resource_classes.eq(count)].sort_values("class_number")
                ccost = np.zeros(len(group)) if case.technology == "WIND_ONSHORE" else (
                    annual["offwind-ac-station"]+1.25*(group.average_distance_km.to_numpy()*annual["offwind-ac-connection-submarine"]+20*annual["offwind-ac-connection-underground"]))
                useful = group.annual_CF.gt(0).to_numpy() & group.p_nom_max_MW.gt(0).to_numpy()
                score = -(common+ccost[useful])/(8760*group.annual_CF.to_numpy()[useful])
                by_class = np.zeros(len(group))
                by_class[useful] = fixed_mw_allocation(case.p_nom_MW, group.p_nom_max_MW.to_numpy()[useful], score)
                class_profile = ds.profile.sel(class_id=group.class_id.to_numpy()).to_numpy() @ (by_class/case.p_nom_MW)
                comparisons.append({"year": case.year, "scenario": case.scenario, "zone": case.zone,
                    "technology": case.technology, "resolution": f"{count}_CLASSES", "annual_CF": float(class_profile.mean()),
                    "hourly_MAE_vs_native": float(np.abs(class_profile-effective).mean()),
                    "hourly_max_abs_vs_native": float(np.abs(class_profile-effective).max()),
                    "selected_MW": float(by_class.sum()), "available_potential_MW": float(group.p_nom_max_MW.sum()),
                    "selected_technical_potential_MW": float(group.loc[by_class > 0,"p_nom_max_MW"].sum()),
                    "occupied_resource_units": int((by_class > 0).sum()),
                    "authority": "RESOURCE_CLASS_DISCRETIZATION_NOT_USED_AS_FINAL_SITING_AUTHORITY"})
        comparisons.append({"year": case.year, "scenario": case.scenario, "zone": case.zone,
            "technology": case.technology, "resolution": "NATIVE_CELLS", "annual_CF": float(effective.mean()),
            "hourly_MAE_vs_native": 0., "hourly_max_abs_vs_native": 0.,
            "selected_MW": float(allocation.sum()), "available_potential_MW": float(sites.p_nom_max_MW.sum()),
            "selected_technical_potential_MW": float(selected.p_nom_max_MW.sum()),
            "occupied_resource_units": len(selected), "authority": "ACCEPTED_NATIVE_CELL_SITE_LCOE_FRAMEWORK"})
    output = RESOURCE / "NATIVE_CELL_EFFECTIVE_WIND_2019.parquet"
    pd.concat(profiles, ignore_index=True).to_parquet(output, index=False)
    pd.DataFrame(allocations).to_csv(QA / "NATIVE_CELL_SITING_ALLOCATION.csv", index=False)
    pd.DataFrame(qa).to_csv(QA / "FINAL_WIND_PROFILE_QA.csv", index=False)
    pd.DataFrame(robustness).to_csv(QA / "OFFSHORE_COST_RANKING_ROBUSTNESS.csv", index=False)
    pd.DataFrame(comparisons).to_csv(QA / "RESOURCE_CLASS_TO_NATIVE_REFERENCE_QA.csv", index=False)
    # Persist only selected cells; unselected hourly traces are regenerable.
    for technology, columns in selected_columns.items():
        columns = sorted(columns)
        xr.Dataset({"p_max_pu": (("time","hourly_column"), resource[technology]["profiles"][:,columns])},
            coords={"time": resource[technology]["timestamps"], "hourly_column": columns}).to_netcdf(
                RESOURCE / f"{technology}_SELECTED_NATIVE_CELL_PROFILES_2019.nc")
    dump_json(QA / "ONSHORE_SITING_RECEIPT.json", {
        "framework": "COMMON_FIXED_CAPACITY_SITE_LCOE", "common_onshore_cost_means_CF_DESCENDING": True,
        "reported_capacity_heuristic_QA_only": True, "source_cost_EUR_per_MW_year": annual["onwind"]})
    all_robust = all(row["status"] == "PASS" for row in robustness)
    receipt = {"state": "NATIVE_CELL_SITING_QA_PASS_PENDING_PROMOTION" if all_robust else "WIND_SITE_COST_ROBUSTNESS_BLOCKED",
        "resource_cells": len(cells), "scenario_zone_wind_cases": len(qa), "robustness_cases": len(robustness),
        "robustness_failed_cases": sum(row["status"] != "PASS" for row in robustness),
        "ONSHORE_AND_OFFSHORE_USE_COMMON_FIXED_CAPACITY_SITE_LCOE_FRAMEWORK": True,
        "resource_class_discretization_final_authority": False, "old_4_8_failures_retained": True,
        "native_cost_authority": "PINNED_LOCAL_WORKFLOW_V0P14P0_2050_RAW;SOLE_VINTAGE_WITH_STRESS_QA",
        "optimization_model_constructed": False, "production_solver_invocations": 0,
        "production_optimization_executed": False, "parent_networks_modified": 0}
    dump_json(QA / "NATIVE_CELL_SITING_RECEIPT.json", receipt)
    return receipt


def promote_wind_successors():
    """Only wind p_max_pu changes; exact comparison before/after serialization."""
    import pypsa
    from .final_methodology_closure import STATE
    current = QA / "HORIZON_SPECIFIC_WIND_SITING_RECEIPT.json"
    receipt = json.loads((current if current.exists() else QA / "NATIVE_CELL_SITING_RECEIPT.json").read_text())
    if receipt["state"] != "NATIVE_CELL_SITING_QA_PASS_PENDING_PROMOTION":
        raise RuntimeError("CELL_WIND_PROMOTION_REQUIRES_SITING_QA_PASS")
    profile_path = ROOT / receipt["profile_path"] if "profile_path" in receipt else RESOURCE / "NATIVE_CELL_EFFECTIVE_WIND_2019.parquet"
    if "profile_sha256" in receipt and sha256_file(profile_path) != receipt["profile_sha256"]:
        raise RuntimeError("CELL_WIND_EFFECTIVE_PROFILE_HASH_FAIL")
    frame = pd.read_parquet(profile_path)
    state = json.loads(STATE.read_text())
    directory = ROOT / "networks/unsolved/final_wind_native_v1"
    directory.mkdir(parents=True, exist_ok=True)
    packages = []
    for parent in state["controlling_parents"]:
        path = ROOT / parent["path"]
        if sha256_file(path) != parent["sha256"]:
            raise RuntimeError("CELL_WIND_PARENT_HASH_FAIL")
        original = pypsa.Network(path)
        derived = original.copy()
        case = frame.loc[frame.year.eq(parent["year"]) & frame.scenario.eq(parent["scenario"])]
        targets = original.generators.index[original.generators.carrier.isin(("wind_onshore","wind_offshore"))]
        if len(targets) != 14 or original.generators.loc[targets].committable.any():
            raise RuntimeError("CELL_WIND_GENERATOR_SCOPE_FAIL")
        for name in targets:
            row = original.generators.loc[name]
            technology = "WIND_ONSHORE" if row.carrier == "wind_onshore" else "WIND_OFFSHORE"
            profile = case.loc[case.zone.eq(row.bus) & case.technology.eq(technology)].sort_values("snapshot")
            if len(profile) != 8760:
                raise RuntimeError("CELL_WIND_PROFILE_COVERAGE_FAIL")
            derived.generators_t.p_max_pu[name] = profile.p_max_pu.to_numpy()
        derived.meta = {**original.meta, "final_wind_version": "NATIVE_CELL_SITE_LCOE_V1",
            "wind_weather_year": 2019, "wind_siting_scenario_specific": True,
            "ONSHORE_AND_OFFSHORE_USE_COMMON_FIXED_CAPACITY_SITE_LCOE_FRAMEWORK": True,
            "production_executed_by_final_closure": False}
        destination = directory / f"MEM_{parent['year']}_{parent['scenario'].upper()}_FINAL_WIND_NATIVE_V1_8760h_UNSOLVED.nc"
        compare_wind_only(original, derived, targets)
        if not destination.exists():
            derived.export_to_netcdf(destination)
        reloaded = pypsa.Network(destination)
        compare_wind_only(original, reloaded, targets)
        for name in targets:
            np.testing.assert_array_equal(derived.generators_t.p_max_pu[name], reloaded.generators_t.p_max_pu[name])
        packages.append({**parent, "parent": parent["path"], "parent_sha256": parent["sha256"],
            "output": str(destination.relative_to(ROOT)), "output_sha256": sha256_file(destination),
            "wind_columns": len(targets), "status": "PASS", "serialization_reimport": "PASS",
            "all_non_wind_components_exact": True, "production_optimization_executed": False})
        dump_json(QA / "WIND_SUCCESSOR_STRUCTURAL_QA.json", {"status": "IN_PROGRESS", "packages": packages,
            "snapshots": 8760, "optimization_model_constructed": False, "solver_invocations": 0})
    dump_json(QA / "WIND_SUCCESSOR_STRUCTURAL_QA.json", {"status": "PASS", "packages": packages,
        "snapshots": 8760, "optimization_model_constructed": False, "solver_invocations": 0})
    return packages


def compare_wind_only(parent, output, targets):
    pd.testing.assert_index_equal(parent.snapshots, output.snapshots)
    pd.testing.assert_frame_equal(parent.snapshot_weightings, output.snapshot_weightings, check_dtype=False, check_freq=False)
    for component in parent.components:
        original, changed = component.static, output.components[component.name].static
        pd.testing.assert_frame_equal(original, changed, check_dtype=False)
        dynamic = output.components[component.name].dynamic
        if set(component.dynamic) != set(dynamic):
            raise RuntimeError("CELL_WIND_DYNAMIC_ATTRIBUTES_DRIFT")
        for field, values in component.dynamic.items():
            selected = values.columns
            if component.name == "Generator" and field == "p_max_pu":
                pd.testing.assert_index_equal(values.columns, dynamic[field].columns)
                selected = values.columns.difference(targets)
            pd.testing.assert_frame_equal(values[selected], dynamic[field][selected], check_dtype=False, check_freq=False)
    for key, value in parent.meta.items():
        if output.meta.get(key) != value:
            raise RuntimeError("CELL_WIND_PARENT_METADATA_DRIFT")

"""Offline seven-zone 2019 VRE adapter; no optimization/model-building path.

Accepted national Stage-A sources and calibrated v2A parents are read-only.
Spatial authority and exclusion eligibility live in zonal_vre_spatial; weather
conversion reuses the already accepted MEM B3 pure AtLite conversion function.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from .common import ROOT, CONFIG, ZONES, SCENARIOS, load_yaml, dump_json, sha256_file

CONFIG_PATH = CONFIG / "stage_b_zonal_vre_v1.yaml"
SUCCESS = "STAGE_B_ZONAL_VRE_CORRECTION_PASS"
PROFILE_NAME = "MEM_STAGE_B_ZONAL_VRE_2019_HOURLY.parquet"
RECEIPT_NAME = "MEM_STAGE_B_ZONAL_VRE_PREPARATION_RECEIPT.json"
MANIFEST_NAME = "MEM_STAGE_B_ZONAL_VRE_OUTPUT_MANIFEST.csv"
FINAL_NAME = "MEM_STAGE_B_ZONAL_VRE_FINAL_VERIFICATION.json"
FAMILIES = {"solar": "SOLAR_PV", "onwind": "WIND_ONSHORE", "offwind": "WIND_OFFSHORE"}
CARRIERS = {"solar_pv_rooftop": "SOLAR_PV", "solar_pv_utility": "SOLAR_PV",
            "wind_onshore": "WIND_ONSHORE", "wind_offshore": "WIND_OFFSHORE"}
SNAPSHOTS = pd.date_range("2019-01-01", periods=8760, freq="h", name="snapshot")


def settings():
    cfg = load_yaml(CONFIG_PATH)
    if (cfg["schema_version"] != "MEM_STAGE_B_ZONAL_VRE_V1" or
            cfg["model_construction_authorized"] or cfg["optimization_authorized"] or
            cfg["weather_download_authorized"] or tuple(cfg["zone_order"]) != ZONES):
        raise RuntimeError("ZONAL_VRE_SCOPE_GATE_FAIL")
    return cfg


@contextmanager
def no_models_or_solves():
    """Stop even a mathematical model constructor, not merely solver calls."""
    from pypsa.optimization.optimize import OptimizationAccessor
    from linopy import Model
    def forbidden(*args, **kwargs):
        raise RuntimeError("ZONAL_VRE_MODEL_OR_SOLVER_FORBIDDEN")
    with patch.object(Model, "__init__", forbidden), patch.object(Model, "solve", forbidden), \
            patch.object(OptimizationAccessor, "__call__", forbidden), \
            patch.object(OptimizationAccessor, "create_model", forbidden), \
            patch.object(OptimizationAccessor, "solve_model", forbidden):
        yield


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def profile_path():
    return ROOT / settings()["profile_directory"] / PROFILE_NAME


def output_path(year, scenario):
    if (year, scenario) not in SCENARIOS:
        raise ValueError("ZONAL_VRE_UNKNOWN_SCENARIO")
    return ROOT / settings()["network_directory"] / (
        f"MEM_{year}_{scenario.upper()}_UC2A_ZONAL_VRE_V1_8760h_UNSOLVED.nc")


def parent_packages():
    cfg = settings()
    receipt = read_json(ROOT / cfg["parent_receipt"])
    if (receipt["calibration_revision"] != cfg["calibrated_parent_revision"] or
            receipt["status"] != "HETEROGENEOUS_UC_V2A_TECHNICALLY_PREPARED"):
        raise RuntimeError("ZONAL_VRE_CALIBRATED_PARENT_FAIL")
    packages = receipt["structural_packages"]
    if {(p["year"], p["scenario"]) for p in packages} != set(SCENARIOS) or len(packages) != 6:
        raise RuntimeError("ZONAL_VRE_PARENT_COVERAGE_FAIL")
    for package in packages:
        if package["status"] != "PASS" or sha256_file(ROOT / package["output"]) != package["output_sha256"]:
            raise RuntimeError("ZONAL_VRE_PARENT_HASH_FAIL")
    return packages


def validate_profiles(frame):
    required = {"snapshot", "zone", "technology", "p_max_pu"}
    if not required.issubset(frame):
        raise RuntimeError("ZONAL_VRE_PROFILE_SCHEMA_FAIL")
    if frame.duplicated(["snapshot", "zone", "technology"]).any():
        raise RuntimeError("ZONAL_VRE_DUPLICATE_PROFILE_KEY")
    expected = {(zone, family) for zone in ZONES for family in FAMILIES.values()}
    if set(frame.groupby(["zone", "technology"]).groups) != expected:
        raise RuntimeError("ZONAL_VRE_PROFILE_COVERAGE_FAIL")
    rows, pairs = [], []
    for (zone, technology), group in frame.groupby(["zone", "technology"], sort=True):
        group = group.sort_values("snapshot")
        times = pd.DatetimeIndex(group.snapshot).tz_convert("UTC").tz_localize(None)
        if not times.equals(SNAPSHOTS):
            raise RuntimeError("ZONAL_VRE_PROFILE_CHRONOLOGY_FAIL")
        values = group.p_max_pu.to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values < 0).any() or (values > 1).any():
            raise RuntimeError("ZONAL_VRE_PROFILE_RANGE_FAIL")
        tiled = [hours for hours in (24, 168, 720, 876) if
                 np.array_equal(values[hours:], values[:-hours])]
        if tiled:
            raise RuntimeError(f"ZONAL_VRE_TILED_PROFILE: {zone} {technology} {tiled}")
        quantiles = np.quantile(values, [.05, .25, .5, .75, .95])
        rows.append({"zone": zone, "technology": technology, "snapshots": len(values),
                     "availability_CF": float(values.mean()), "min": float(values.min()),
                     "mean": float(values.mean()), "p5": quantiles[0], "p25": quantiles[1],
                     "median": quantiles[2], "p75": quantiles[3], "p95": quantiles[4],
                     "max": float(values.max()), "zero_hours": int((values == 0).sum()),
                     "full_hours": int((values == 1).sum()), "invalid_values": 0,
                     "tiled_short_block": False})
    for family in FAMILIES.values():
        wide = frame.loc[frame.technology.eq(family)].pivot(index="snapshot", columns="zone", values="p_max_pu")
        for i, left in enumerate(ZONES):
            for right in ZONES[i+1:]:
                a, b = wide[left].to_numpy(), wide[right].to_numpy()
                duplicate = np.array_equal(a, b)
                pairs.append({"technology": family, "zone_left": left, "zone_right": right,
                              "correlation": float(np.corrcoef(a, b)[0, 1]),
                              "max_abs_difference": float(np.max(np.abs(a-b))),
                              "exact_duplicate": duplicate})
                if duplicate:
                    raise RuntimeError(f"ZONAL_VRE_CROSS_ZONE_DUPLICATE: {family} {left} {right}")
    return pd.DataFrame(rows), pd.DataFrame(pairs)


def convert_profiles(matrices, directory):
    import atlite
    from .stage_a.temporal_2019 import _compute_profile_from_availability
    cfg = load_yaml(CONFIG / "stage_a_temporal_2019.yaml")
    cutout = atlite.Cutout(str(ROOT / cfg["paths"]["cutout"]))
    # Italy and its existing resource footprint only, on the unchanged cutout grid.
    bounds = [-6., 33., 28., 53.]
    cutout.data = cutout.data.sel(x=slice(bounds[0], bounds[2]), y=slice(bounds[1], bounds[3]))
    parts, potentials, transforms = [], [], []
    for key, family in FAMILIES.items():
        print(f"Offline 2019 conversion: {family}", flush=True)
        resource = cfg["vre"][key]
        availability = matrices[family].sel(x=cutout.coords["x"], y=cutout.coords["y"])
        method = "pv" if key == "solar" else "wind"
        original_conversion = getattr(cutout, method)
        raw_holder = {}
        def capture_source(**kwargs):
            result = original_conversion(**kwargs)
            if kwargs.get("aggregate_time", "unspecified") is None:
                result = result.compute()
                raw_holder["values"] = result.transpose("time", "bus").to_pandas()
            return result
        with patch.object(cutout, method, capture_source):
            values, potential = _compute_profile_from_availability(cutout, availability, family,
                float(resource["capacity_per_sqkm"]), float(resource["correction_factor"]),
                float(resource["clip_p_max_pu_below"]))
        raw = raw_holder["values"] * float(resource["correction_factor"])
        expected = raw.where(raw >= float(resource["clip_p_max_pu_below"]), 0)
        if not np.array_equal(values.to_numpy(), expected.to_numpy()):
            raise RuntimeError("ZONAL_VRE_SOURCE_CONVERSION_RECONCILIATION_FAIL")
        for zone in ZONES:
            zeros = raw[zone].to_numpy() == 0
            false_positive = int(((values[zone].to_numpy() > 0) & zeros).sum())
            if false_positive:
                raise RuntimeError("ZONAL_VRE_SOURCE_ZERO_NOT_PRESERVED")
            transforms.append({"zone": zone, "technology": family, "source_unit": "PER_UNIT",
                "raw_source_zero_hours": int(zeros.sum()), "positive_MEM_when_source_zero": false_positive,
                "correction_factor": resource["correction_factor"], "lower_threshold": resource["clip_p_max_pu_below"],
                "positive_values_zeroed_by_accepted_threshold": int(((raw[zone] > 0) & (raw[zone] < resource["clip_p_max_pu_below"])).sum()),
                "max_abs_transformation_difference": 0., "missing_filling_or_tiling": False,
                "installed_capacity_used_in_profile": False, "annual_CF_normalization": False})
        if set(values.columns) != set(ZONES):
            raise RuntimeError(f"ZONAL_VRE_ZERO_RESOURCE_ZONE: {family}")
        part = values.rename_axis("snapshot").reset_index().melt(
            id_vars="snapshot", var_name="zone", value_name="p_max_pu")
        part["snapshot"] = pd.to_datetime(part.snapshot, utc=True)
        part["technology"] = family
        part["profile_id"] = part.zone.map(lambda z: f"MEM_2019_{z}_{family}_ZONAL_V1")
        parts.append(part)
        potentials.extend({"zone": str(z), "technology": family,
                           "eligible_resource_potential_MW": float(v)} for z, v in potential.items())
    cutout.data.close()
    frame = pd.concat(parts, ignore_index=True).sort_values(["technology", "zone", "snapshot"]).reset_index(drop=True)
    summary, pairs = validate_profiles(frame)
    frame.to_parquet(directory / PROFILE_NAME, index=False, engine="pyarrow", compression="zstd")
    pd.DataFrame(potentials).to_csv(directory / "MEM_STAGE_B_ZONAL_VRE_RESOURCE_WEIGHTS.csv", index=False)
    pd.DataFrame(transforms).to_csv(directory / "MEM_STAGE_B_ZONAL_VRE_SOURCE_TRANSFORMATION_QA.csv", index=False)
    return frame, summary, pairs


def profile_capacity_qa(summary, capacities, resource_weights):
    """Join weather statistics to frozen scenario MW, never rescale a profile."""
    keys = ["year", "scenario", "zone", "carrier"]
    expected = {(year, scenario, zone, carrier) for year, scenario in SCENARIOS
                for zone in ZONES for carrier in CARRIERS}
    if (capacities.duplicated(keys).any() or
            set(capacities[keys].itertuples(index=False, name=None)) != expected or
            not np.isfinite(capacities.p_nom_MW).all() or (capacities.p_nom_MW < 0).any() or
            not capacities.technology.equals(capacities.carrier.map(CARRIERS))):
        raise RuntimeError("ZONAL_VRE_CAPACITY_QA_COVERAGE_FAIL")
    profile_keys = ["zone", "technology"]
    expected_profiles = {(zone, family) for zone in ZONES for family in FAMILIES.values()}
    for table in (summary, resource_weights):
        if (table.duplicated(profile_keys).any() or
                set(table[profile_keys].itertuples(index=False, name=None)) != expected_profiles):
            raise RuntimeError("ZONAL_VRE_CAPACITY_PROFILE_JOIN_FAIL")
    grouped = capacities.groupby(["year", "scenario", *profile_keys], as_index=False).p_nom_MW.sum()
    result = grouped.merge(summary, on=profile_keys, validate="many_to_one").merge(
        resource_weights, on=profile_keys, validate="many_to_one")
    result["potential_TWh"] = result.p_nom_MW * result.availability_CF * 8760 / 1e6
    result["profile_id"] = "MEM_2019_" + result.zone + "_" + result.technology + "_ZONAL_V1"
    result["source_members_crosswalk"] = "runtime_inputs/stage_b_zonal_vre_v1/spatial_source_market_zone_crosswalk.csv"
    result["aggregation_weight_semantics"] = "MEAN_CELL_RESOURCE_CF_TIMES_ELIGIBLE_CELL_AREA_TIMES_DENSITY"
    return result.sort_values(["year", "scenario", "zone", "technology"]).reset_index(drop=True)


def apply_profiles(parent, profiles):
    output = parent.copy()
    names = parent.generators.index[parent.generators.carrier.isin(CARRIERS)]
    if len(names) != 28 or parent.generators.loc[names].committable.any():
        raise RuntimeError("ZONAL_VRE_TARGET_GENERATOR_SCOPE_FAIL")
    if not set(names).issubset(parent.generators_t.p_max_pu.columns):
        raise RuntimeError("ZONAL_VRE_MISSING_PARENT_AVAILABILITY")
    for name in names:
        row = parent.generators.loc[name]
        if row.bus not in ZONES:
            raise RuntimeError("ZONAL_VRE_UNKNOWN_BUS")
        group = profiles.loc[profiles.zone.eq(row.bus) & profiles.technology.eq(CARRIERS[row.carrier])].sort_values("snapshot")
        if len(group) != len(parent.snapshots):
            raise RuntimeError("ZONAL_VRE_TARGET_HOUR_COUNT_FAIL")
        output.generators_t.p_max_pu[name] = group.p_max_pu.to_numpy()
    output.meta = {**parent.meta, "stage_b_zonal_vre_version": "STAGE_B_ZONAL_VRE_V1",
                   "zonal_vre_weather_year": 2019, "network_v2b_applied": False,
                   "production_executed_by_zonal_vre_preparation": False}
    return output


def verify_derivative(parent, output, profiles):
    pd.testing.assert_index_equal(parent.snapshots, output.snapshots)
    if len(output.snapshots) != 8760 or not pd.DatetimeIndex(output.snapshots).equals(SNAPSHOTS):
        raise RuntimeError("ZONAL_VRE_NETWORK_CHRONOLOGY_FAIL")
    pd.testing.assert_frame_equal(parent.snapshot_weightings, output.snapshot_weightings, check_dtype=False, check_freq=False)
    for attr in ("buses", "carriers", "loads", "generators", "links", "stores", "storage_units",
                 "lines", "transformers", "shunt_impedances", "global_constraints", "line_types", "transformer_types"):
        pd.testing.assert_frame_equal(getattr(parent, attr), getattr(output, attr), check_dtype=False)
        original_dynamic = getattr(parent, attr + "_t", {})
        changed_dynamic = getattr(output, attr + "_t", {})
        if set(original_dynamic) != set(changed_dynamic):
            raise RuntimeError("ZONAL_VRE_DYNAMIC_ATTRIBUTE_DRIFT")
        for field, values in original_dynamic.items():
            if attr == "generators" and field == "p_max_pu":
                names = parent.generators.index[parent.generators.carrier.isin(CARRIERS)]
                pd.testing.assert_index_equal(values.columns, changed_dynamic[field].columns)
                untouched = values.columns.difference(names)
                pd.testing.assert_frame_equal(values[untouched], changed_dynamic[field][untouched], check_dtype=False, check_freq=False)
            else:
                pd.testing.assert_frame_equal(values, changed_dynamic[field], check_dtype=False, check_freq=False)
    changed, coverage = 0, []
    for name, row in parent.generators.loc[parent.generators.carrier.isin(CARRIERS)].iterrows():
        family = CARRIERS[row.carrier]
        expected = profiles.loc[profiles.zone.eq(row.bus) & profiles.technology.eq(family)].sort_values("snapshot").p_max_pu.to_numpy()
        actual = output.generators_t.p_max_pu[name].to_numpy()
        if not np.array_equal(actual, expected):
            raise RuntimeError(f"ZONAL_VRE_NETWORK_PROFILE_VALUE_DRIFT: {name}")
        changed += int(not np.array_equal(actual, parent.generators_t.p_max_pu[name].to_numpy()))
        coverage.append({"generator_id": name, "zone": row.bus, "carrier": row.carrier,
                         "technology": family, "p_nom_MW": float(row.p_nom),
                         "profile_id": f"MEM_2019_{row.bus}_{family}_ZONAL_V1", "snapshots": 8760,
                         "max_abs_difference": 0., "mismatched_timestamps": 0,
                         "missing_timestamps": 0, "extra_timestamps": 0})
    if changed != 28:
        raise RuntimeError("ZONAL_VRE_NO_SPATIAL_CHANGE")
    for key, value in parent.meta.items():
        if output.meta.get(key) != value:
            raise RuntimeError(f"ZONAL_VRE_PARENT_METADATA_DRIFT: {key}")
    return {"status": "PASS", "snapshots": 8760, "changed_VRE_columns": changed,
            "all_static_components_exact": True, "non_VRE_dynamic_components_exact": True,
            "capacity_change_MW": 0., "P2X_load_storage_hydro_UC_prices_topology_unchanged": True,
            "target_profile_timestamp_equality": True, "scenario_specific_weather": False}, coverage


def national_reconciliation(frame, packages):
    import pypsa
    old = pd.read_parquet(ROOT / "stage_a_inputs/temporal_2019_v1_0/MEM_ETX7B3_VRE_Availability_Hourly_v1.0.parquet")
    old = old.loc[old.country_code.eq("IT")]
    weights = pd.read_csv(profile_path().parent / "MEM_STAGE_B_ZONAL_VRE_RESOURCE_WEIGHTS.csv")
    rows = []
    for package in packages:
        network = pypsa.Network(ROOT / package["output"])
        for carrier, family in CARRIERS.items():
            generators = network.generators.loc[network.generators.carrier.eq(carrier)]
            wide = frame.loc[frame.technology.eq(family)].pivot(index="snapshot", columns="zone", values="p_max_pu").loc[:, list(ZONES)]
            installed = generators.groupby("bus").p_nom.sum().reindex(ZONES)
            resource = weights.loc[weights.technology.eq(family)].set_index("zone").eligible_resource_potential_MW.reindex(ZONES)
            new_installed = wide.to_numpy() @ (installed.to_numpy() / installed.sum())
            new_resource = wide.to_numpy() @ (resource.to_numpy() / resource.sum())
            old_family = carrier.upper()
            selection = old.loc[old.stage_a_static_or_profile_class.eq(old_family) &
                                old.profile_id.eq(f"ETX7B3_IT_{old_family}_{package['year']}")].sort_values("snapshot")
            if len(selection) != 8760:
                raise RuntimeError(f"ZONAL_VRE_NATIONAL_REFERENCE_COVERAGE: {old_family}")
            baseline = selection.p_max_pu.to_numpy()
            rows.append({"year": package["year"], "scenario": package["scenario"], "technology": old_family,
                         "p_nom_MW": float(installed.sum()), "old_national_CF": float(baseline.mean()),
                         "new_installed_capacity_weighted_CF": float(new_installed.mean()),
                         "new_source_potential_weighted_CF": float(new_resource.mean()),
                         "installed_weighted_hourly_max_abs_difference": float(np.max(np.abs(new_installed-baseline))),
                         "source_weighted_hourly_max_abs_difference": float(np.max(np.abs(new_resource-baseline))),
                         "annual_potential_energy_difference_TWh": float((new_installed-baseline).sum() * installed.sum()/1e6),
                         "exact_national_equality_expected": False,
                         "difference_reason": "OFFICIAL_ZONAL_GEOMETRY_AND_LAYOUT_WEIGHTS_VS_NATIONAL_IT2_IT4_CAPACITY_WEIGHTS;EXCLUSION_BOUNDARY_RASTERIZATION;POST_REGION_0P01_THRESHOLD",
                         "normalization_to_old_national": False})
    return pd.DataFrame(rows)


def refresh_manifest():
    cfg = settings()
    qa = ROOT / cfg["qa_directory"]
    receipt = read_json(qa / RECEIPT_NAME)
    files = list((ROOT / cfg["profile_directory"]).rglob("*"))
    files += [ROOT / p["output"] for p in receipt["structural_packages"]]
    files += [p for p in qa.glob("MEM_STAGE_B_ZONAL_VRE_*") if p.suffix in {".csv", ".json"} and p.name not in {MANIFEST_NAME, FINAL_NAME}]
    files += [CONFIG_PATH, Path(__file__), ROOT / "src/mem_model/zonal_vre_spatial.py",
              ROOT / "tests/test_stage_b_zonal_vre.py", ROOT / "tests/test_zonal_vre_spatial.py",
              ROOT / "docs/MEM_STAGE_B_ZONAL_VRE_V1_PREPARATION_TRANSFER.md"]
    required = required_artifacts(receipt)
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        raise RuntimeError(f"ZONAL_VRE_REQUIRED_ARTIFACT_MISSING: {missing}")
    files += list(required)
    rows = [{"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size, "sha256": sha256_file(p),
             "role": "UNSOLVED_NETWORK" if p.suffix == ".nc" and "networks" in p.parts else "PROFILE_OR_QA_OR_IMPLEMENTATION"}
            for p in sorted(set(files)) if p.is_file()]
    pd.DataFrame(rows).to_csv(qa / MANIFEST_NAME, index=False)


def validate_package_coverage(receipt):
    packages = receipt["structural_packages"]
    if len(packages) != 6 or {(p["year"], p["scenario"]) for p in packages} != set(SCENARIOS):
        raise RuntimeError("ZONAL_VRE_DERIVATIVE_COVERAGE_FAIL")
    for p in packages:
        if ROOT / p["output"] != output_path(p["year"], p["scenario"]):
            raise RuntimeError("ZONAL_VRE_NONCANONICAL_OUTPUT_PATH")
        if p["status"] != "PASS" or p["serialization_reimport"] != "PASS":
            raise RuntimeError("ZONAL_VRE_STRUCTURAL_QA_FAIL")
    return packages


def required_artifacts(receipt):
    cfg = settings()
    qa, directory = ROOT / cfg["qa_directory"], ROOT / cfg["profile_directory"]
    packages = validate_package_coverage(receipt)
    names = ["MEM_STAGE_B_ZONAL_VRE_PROFILE_QA.csv", "MEM_STAGE_B_ZONAL_VRE_PAIRWISE_QA.csv",
             "MEM_STAGE_B_ZONAL_VRE_SCENARIO_ZONE_PROFILE_QA.csv",
             "MEM_STAGE_B_ZONAL_VRE_SOURCE_RUNTIME_NETWORK_RECONCILIATION.csv",
             "MEM_STAGE_B_ZONAL_VRE_CAPACITY_COVERAGE.csv", "MEM_STAGE_B_ZONAL_VRE_NATIONAL_RECONCILIATION_QA.csv", RECEIPT_NAME]
    files = [qa / name for name in names]
    files += [directory / name for name in [PROFILE_NAME, "spatial_source_market_zone_crosswalk.csv",
               "spatial_mapping_eligibility_qa.json", "MEM_STAGE_B_ZONAL_VRE_RESOURCE_WEIGHTS.csv",
               "MEM_STAGE_B_ZONAL_VRE_SOURCE_TRANSFORMATION_QA.csv", "official_market_zone_land_polygons.geojson"]]
    files += [directory / f"availability_matrix_zonal_2019_{key}.nc" for key in ("solar", "onwind", "offwind-ac")]
    files += [directory / f"zonal_resource_geometry_{key}.geojson" for key in ("solar", "onwind", "offwind-ac")]
    files += [ROOT / p["output"] for p in packages]
    files += [qa / f"MEM_STAGE_B_ZONAL_VRE_{p['year']}_{p['scenario'].upper()}_STRUCTURAL_QA.json" for p in packages]
    files += [CONFIG_PATH, Path(__file__), ROOT / "src/mem_model/zonal_vre_spatial.py",
              ROOT / "tests/test_stage_b_zonal_vre.py", ROOT / "tests/test_zonal_vre_spatial.py",
              ROOT / "docs/MEM_STAGE_B_ZONAL_VRE_V1_PREPARATION_TRANSFER.md"]
    return files


def prepare():
    import pypsa
    from .zonal_vre_spatial import build_spatial_package
    cfg = settings()
    qa, directory = ROOT / cfg["qa_directory"], ROOT / cfg["profile_directory"]
    qa.mkdir(parents=True, exist_ok=True)
    directory.mkdir(parents=True, exist_ok=True)
    if profile_path().exists() or any(output_path(*case).exists() for case in SCENARIOS):
        raise FileExistsError("ZONAL_VRE_ALREADY_PREPARED_USE_VERIFY_NO_OVERWRITE")
    packages = parent_packages()
    spatial = build_spatial_package(directory)
    matrices = {"SOLAR_PV": spatial["availability"]["solar"],
                "WIND_ONSHORE": spatial["availability"]["onwind"],
                "WIND_OFFSHORE": spatial["availability"]["offwind-ac"]}
    spatial_receipt = spatial["qa"]
    frame, summary, pairs = convert_profiles(matrices, directory)
    summary.to_csv(qa / "MEM_STAGE_B_ZONAL_VRE_PROFILE_QA.csv", index=False)
    pairs.to_csv(qa / "MEM_STAGE_B_ZONAL_VRE_PAIRWISE_QA.csv", index=False)
    structural, mappings, capacities = [], [], []
    for package in packages:
        year, scenario = package["year"], package["scenario"]
        parent = pypsa.Network(ROOT / package["output"])
        derived = apply_profiles(parent, frame)
        check, coverage = verify_derivative(parent, derived, frame)
        destination = output_path(year, scenario)
        destination.parent.mkdir(parents=True, exist_ok=True)
        derived.export_to_netcdf(destination)
        reloaded = pypsa.Network(destination)
        reload_check, _ = verify_derivative(parent, reloaded, frame)
        check.update({"year": year, "scenario": scenario, "parent": package["output"],
                      "parent_sha256": package["output_sha256"], "output": str(destination.relative_to(ROOT)),
                      "output_sha256": sha256_file(destination), "serialization_reimport": reload_check["status"]})
        structural.append(check)
        mappings.extend({"year": year, "scenario": scenario, **row} for row in coverage)
        capacities.extend({"year": year, "scenario": scenario, "zone": row["zone"], "carrier": row["carrier"],
                           "technology": row["technology"], "p_nom_MW": row["p_nom_MW"]} for row in coverage)
        dump_json(qa / f"MEM_STAGE_B_ZONAL_VRE_{year}_{scenario.upper()}_STRUCTURAL_QA.json", check)
    pd.DataFrame(mappings).to_csv(qa / "MEM_STAGE_B_ZONAL_VRE_SOURCE_RUNTIME_NETWORK_RECONCILIATION.csv", index=False)
    pd.DataFrame(capacities).to_csv(qa / "MEM_STAGE_B_ZONAL_VRE_CAPACITY_COVERAGE.csv", index=False)
    profile_capacity_qa(summary, pd.DataFrame(capacities), pd.read_csv(
        directory / "MEM_STAGE_B_ZONAL_VRE_RESOURCE_WEIGHTS.csv")).to_csv(
        qa / "MEM_STAGE_B_ZONAL_VRE_SCENARIO_ZONE_PROFILE_QA.csv", index=False)
    national_reconciliation(frame, packages).to_csv(qa / "MEM_STAGE_B_ZONAL_VRE_NATIONAL_RECONCILIATION_QA.csv", index=False)
    # Only newly consumed parents are rechecked. The accepted 149-artifact audit is reused.
    parent_packages()
    receipt = {"state": SUCCESS, "next_phase": "V2B_PREPARATION_REQUIRED", "hydro_review": "HYDRO_METHODOLOGY_REVIEW_REQUIRED", "production_state": "PRODUCTION_NOT_EXECUTED",
               "created_UTC": datetime.now(timezone.utc).isoformat(), "profile_rows": len(frame),
               "profile_series": 21, "profile_sha256": sha256_file(profile_path()),
               "structural_packages": structural, "spatial_authority": spatial_receipt,
               "accepted_recovery_audit_reused": cfg["accepted_recovery_receipt"],
               "accepted_recovery_receipt_sha256": sha256_file(ROOT / cfg["accepted_recovery_receipt"]),
               "no_proxy_adopted": True, "scenario_shapes_identical": True,
               "parent_hashes_unchanged": True, "optimization_model_constructed": False,
               "solver_invocations": 0, "production_results_modified": 0, "weather_downloads": 0}
    dump_json(qa / RECEIPT_NAME, receipt)
    refresh_manifest()
    return receipt


def verify():
    import pypsa
    cfg = settings()
    qa = ROOT / cfg["qa_directory"]
    receipt = read_json(qa / RECEIPT_NAME)
    if receipt["state"] != SUCCESS or receipt["optimization_model_constructed"] or receipt["solver_invocations"]:
        raise RuntimeError("ZONAL_VRE_RECEIPT_FAIL")
    packages = validate_package_coverage(receipt)
    manifest = pd.read_csv(qa / MANIFEST_NAME)
    if manifest.path.duplicated().any():
        raise RuntimeError("ZONAL_VRE_MANIFEST_DUPLICATE_PATH")
    if not {str(p.relative_to(ROOT)) for p in required_artifacts(receipt)}.issubset(set(manifest.path)):
        raise RuntimeError("ZONAL_VRE_REQUIRED_MANIFEST_MEMBER_MISSING")
    for row in manifest.itertuples(index=False):
        if sha256_file(ROOT / row.path) != row.sha256:
            raise RuntimeError(f"ZONAL_VRE_MANIFEST_HASH_FAIL: {row.path}")
    frame = pd.read_parquet(profile_path())
    summary, pairs = validate_profiles(frame)
    parents = {(p["year"], p["scenario"]): p for p in parent_packages()}
    reconciliations, capacities = 0, []
    for item in packages:
        parent = pypsa.Network(ROOT / parents[item["year"], item["scenario"]]["output"])
        output = pypsa.Network(ROOT / item["output"])
        _, coverage = verify_derivative(parent, output, frame)
        reconciliations += len(coverage)
        capacities.extend({"year": item["year"], "scenario": item["scenario"], **row} for row in coverage)
        if sha256_file(ROOT / item["output"]) != item["output_sha256"]:
            raise RuntimeError("ZONAL_VRE_OUTPUT_HASH_FAIL")
    expected_capacity_qa = profile_capacity_qa(summary, pd.DataFrame(capacities), pd.read_csv(
        profile_path().parent / "MEM_STAGE_B_ZONAL_VRE_RESOURCE_WEIGHTS.csv"))
    pd.testing.assert_frame_equal(expected_capacity_qa, pd.read_csv(
        qa / "MEM_STAGE_B_ZONAL_VRE_SCENARIO_ZONE_PROFILE_QA.csv"),
        check_dtype=False, check_exact=False, rtol=1e-14, atol=1e-14)
    final = {"state": SUCCESS, "next_phase": "V2B_PREPARATION_REQUIRED", "hydro_review": "HYDRO_METHODOLOGY_REVIEW_REQUIRED", "production_state": "PRODUCTION_NOT_EXECUTED",
             "manifest_sha256": sha256_file(qa / MANIFEST_NAME), "manifest_members_verified": len(manifest),
             "series_verified": len(summary), "pairwise_comparisons": len(pairs), "unsolved_derivatives_verified": len(packages),
             "runtime_network_reconciliations": reconciliations, "all_non_VRE_model_data_exact": True,
             "scenario_zone_profile_QA_rows_verified": len(expected_capacity_qa),
             "accepted_recovery_audit_reused_not_repeated": True, "parent_hashes_unchanged": True,
             "optimization_model_constructed": False, "solver_invocations": 0, "production_results_modified": 0,
             "weather_downloads": 0}
    dump_json(qa / FINAL_NAME, final)
    return final


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "verify"])
    args = parser.parse_args(argv)
    with no_models_or_solves():
        result = prepare() if args.action == "prepare" else verify()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

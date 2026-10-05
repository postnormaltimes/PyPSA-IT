"""Offline native PyPSA-Eur resource-class recovery; no MEM dispatch model.

The local build_renewable_profiles script has no importable class builder.
This compatibility adapter follows its equal-width CF bins, eligibility,
resource layout, per-unit profiles and distances using the accepted local
geometry/matrices. Fixed-MW siting is deliberately separate from resource
conversion: no implicit economic ranking is invented here.
"""
from __future__ import annotations

import json
from operator import attrgetter
from itertools import takewhile

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
from shapely.geometry import MultiPolygon

from .common import ROOT, ZONES, load_yaml, dump_json, sha256_file
from .zonal_vre_spatial import CUTOUT_PATH, SOURCE_ROOT
from .stage_a.temporal_2019 import _atlite_resource, _cell_area_sqkm, DASK_KWARGS

QA = ROOT / "qa/final_methodology_closure/wind"
RESOURCE = ROOT / "runtime_inputs/final_methodology_closure/wind"
ACCEPTED = ROOT / "runtime_inputs/stage_b_zonal_vre_v1"
FAMILIES = {"onwind": "WIND_ONSHORE", "offwind-ac": "WIND_OFFSHORE"}


def native_offshore_distance_reference(polys):
    """Local build_shapes._simplify_polys(g, minarea=1), on a copy only."""
    if isinstance(polys, MultiPolygon):
        parts = sorted(polys.geoms, key=attrgetter("area"), reverse=True)
        main = parts[0]
        mainlength = np.sqrt(main.area/(2*np.pi))
        if main.area > 1:
            return MultiPolygon([p for p in takewhile(lambda p: p.area > 1, parts)
                                 if main.distance(p) < mainlength])
        return main
    return polys


def resource_class_masks(cell_cf, indicator, nclasses):
    """Exact native ±0.001 equal-width bin edges; not area/CF quantiles."""
    if nclasses not in (1, 4, 8):
        raise ValueError("WIND_RESOURCE_CLASS_COUNT_NOT_AUTHORIZED")
    cf_by_zone = cell_cf * indicator.where(indicator > 0)
    cf_min = cf_by_zone.min(dim=["y", "x"]) - 1e-3
    cf_max = cf_by_zone.max(dim=["y", "x"]) + 1e-3
    edges = cf_min + (cf_max-cf_min) * xr.DataArray(np.linspace(0, 1, nclasses+1), dims=["bin"])
    masks = []
    records = []
    for zone in ZONES:
        for i in range(nclasses):
            name = f"{zone}__RC{nclasses}__C{i+1:02d}"
            mask = ((cf_by_zone.sel(bus=zone) >= edges.sel(bus=zone).isel(bin=i)) &
                    (cf_by_zone.sel(bus=zone) < edges.sel(bus=zone).isel(bin=i+1)))
            masks.append(mask.expand_dims(class_id=[name]))
            records.append({"class_id": name, "zone": zone, "resource_classes": nclasses,
                            "class_number": i+1, "CF_lower_bound": float(edges.sel(bus=zone).isel(bin=i)),
                            "CF_upper_bound": float(edges.sel(bus=zone).isel(bin=i+1)),
                            "resource_cells": int(mask.sum())})
    return xr.concat(masks, dim="class_id"), pd.DataFrame(records)


def fixed_mw_allocation(capacity, potential, score):
    """Analytic linear allocation once a controlling economic score exists."""
    potential, score = np.asarray(potential, float), np.asarray(score, float)
    if (capacity < 0 or not np.isfinite(potential).all() or (potential < 0).any() or
            not np.isfinite(score).all()):
        raise ValueError("WIND_SITING_INPUT_INVALID")
    if capacity > potential.sum() + 1e-7:
        raise RuntimeError("WIND_FROZEN_CAPACITY_EXCEEDS_PHYSICAL_POTENTIAL")
    order = np.lexsort((np.arange(len(score)), -score))
    allocation = np.zeros(len(score))
    remaining = float(capacity)
    for i in order:
        allocation[i] = min(remaining, potential[i])
        remaining -= allocation[i]
    if abs(remaining) > 1e-7 or not np.isclose(allocation.sum(), capacity, atol=1e-7, rtol=0):
        raise RuntimeError("WIND_SITING_CAPACITY_RECONCILIATION_FAIL")
    return allocation


def reported_capacity_heuristic(capacity, potential, cf):
    """Native CF*potential comparison, bounded without raising physical caps.

    Native attach_wind_and_solar uses proportional weights and drops <0.1 MW;
    its later update_p_nom_max may relax a cap. Both are reported, never copied
    as hidden MEM capacity changes.
    """
    potential, cf = np.asarray(potential, float), np.asarray(cf, float)
    weights = potential * cf
    if capacity > potential.sum() + 1e-7 or weights.sum() <= 0:
        raise RuntimeError("WIND_NATIVE_HEURISTIC_INFEASIBLE")
    raw = capacity * weights/weights.sum()
    return raw, raw > potential + 1e-7, np.where(raw > 0.1, raw, 0)


def build_resource_classes():
    """Convert existing 2019 weather only; never recreate accepted masks."""
    import atlite
    from atlite.gis import ExclusionContainer

    RESOURCE.mkdir(parents=True, exist_ok=True)
    QA.mkdir(parents=True, exist_ok=True)
    cfg = load_yaml(ROOT / "config/stage_a_temporal_2019.yaml")
    completed = QA / "RESOURCE_CLASS_CONVERSION_RECEIPT.json"
    if completed.exists():
        receipt = json.loads(completed.read_text())
        for item in receipt["artifacts"]:
            if sha256_file(ROOT / item["path"]) != item["sha256"]:
                raise RuntimeError("WIND_RESOURCE_CACHE_HASH_FAIL")
        return receipt
    cutout = atlite.Cutout(str(CUTOUT_PATH))
    cutout.data = cutout.data.sel(x=slice(-6., 28.), y=slice(33., 53.))
    area = _cell_area_sqkm(cutout)
    records, artifacts = [], []
    for key, family in FAMILIES.items():
        destination = RESOURCE / f"{family}_RESOURCE_CLASSES_2019.nc"
        if destination.exists():
            raise FileExistsError("WIND_PARTIAL_CACHE_EXISTS_INSPECT_BEFORE_OVERWRITE")
        print(f"Native resource-class conversion: {family}", flush=True)
        params = cfg["vre"]["onwind" if key == "onwind" else "offwind"]
        correction, density = float(params["correction_factor"]), float(params["capacity_per_sqkm"])
        geometry = gpd.read_file(ACCEPTED / f"zonal_resource_geometry_{key}.geojson").set_index("bus")
        geometry = geometry.reindex(ZONES)
        availability = xr.open_dataarray(ACCEPTED / f"availability_matrix_zonal_2019_{key}.nc").sel(
            bus=list(ZONES), x=cutout.coords["x"], y=cutout.coords["y"])
        cell_cf = (correction * _atlite_resource(cutout, family, aggregate_time="mean")).compute()
        # Native class bins use unexcluded footprint membership; physical caps
        # and profiles use the already accepted exclusion matrix separately.
        indicator = np.ceil(cutout.availabilitymatrix(geometry.geometry, ExclusionContainer(),
                               nprocesses=1, disable_progressbar=True))
        indicator = indicator.sel(bus=list(ZONES), x=cutout.coords["x"], y=cutout.coords["y"])
        all_masks, all_records = [], []
        for count in (1, 4, 8):
            masks, table = resource_class_masks(cell_cf, indicator, count)
            all_masks.append(masks)
            all_records.append(table)
        masks = xr.concat(all_masks, dim="class_id")
        classes = pd.concat(all_records, ignore_index=True)
        zone_eligibility = xr.concat([availability.sel(bus=zone).drop_vars("bus").expand_dims(
            class_id=[name]) for zone, name in zip(classes.zone, classes.class_id)], dim="class_id")
        eligible = zone_eligibility * masks
        potential = (density * eligible * area).sum(dim=["y", "x"])
        layout = cell_cf * area * density
        positive = potential.class_id[potential > 0]
        matrix = eligible.sel(class_id=positive).stack(spatial=["y", "x"])
        turbine = "Vestas_V112_3MW" if key == "onwind" else "NREL_ReferenceTurbine_2020ATB_5.5MW"
        profile = correction * cutout.wind(turbine=turbine, smooth=False, add_cutout_windspeed=True,
            matrix=matrix, layout=layout, index=matrix.indexes["class_id"], per_unit=True,
            return_capacity=False, aggregate_time=None, show_progress=False, dask_kwargs=DASK_KWARGS).compute()
        profile = profile.where(profile >= float(params["clip_p_max_pu_below"]), 0).reindex(
            class_id=classes.class_id.to_numpy(), fill_value=0).transpose("time", "class_id")
        # Native matrix summation can produce 1+epsilon at saturation.
        # Preserve the raw values, rather than clipping or changing resources.
        if (not np.isfinite(profile).all() or (profile < -1e-12).any() or (profile > 1+1e-12).any()):
            profile.to_netcdf(RESOURCE / f"{family}_INVALID_PROFILE_DIAGNOSTIC.nc")
            raise RuntimeError(f"WIND_CLASS_PROFILE_INVALID: min={float(profile.min())} "
                               f"max={float(profile.max())} nonfinite={int((~np.isfinite(profile)).sum())}")
        layoutmatrix = (layout * eligible).stack(spatial=["y", "x"])
        coords = cutout.grid.representative_point().to_crs(3035)
        # Native offshore distance reference is onshore connection region.
        # Use the corresponding accepted onshore resource-region geometry and
        # native offshore simplification, never nearest-bus resource mapping.
        land = gpd.read_file(ACCEPTED / "zonal_resource_geometry_onwind.geojson").set_index("bus")
        if key == "offwind-ac":
            land.geometry = land.geometry.map(native_offshore_distance_reference)
        else:
            land.geometry = land.geometry.representative_point()
        land = land.to_crs(3035)
        distances = []
        geometries = []
        grid = cutout.grid.set_index(["y", "x"])
        for row in classes.itertuples(index=False):
            weighted = layoutmatrix.sel(class_id=row.class_id).to_numpy()
            if weighted.sum() > 0:
                reference = land.loc[row.zone, "geometry"]
                distance = float(np.dot(coords.distance(reference).to_numpy()/1e3, weighted)/weighted.sum())
            else:
                distance = 0.
            distances.append(distance)
            cell_mask = masks.sel(class_id=row.class_id).stack(spatial=["y", "x"]).to_numpy()
            geometries.append(grid.loc[cell_mask].geometry.intersection(geometry.loc[row.zone, "geometry"]).union_all().buffer(0))
        classes["technology"] = family
        classes["p_nom_max_MW"] = potential.to_numpy()
        classes["annual_CF"] = profile.mean("time").to_numpy()
        classes["average_distance_km"] = distances
        classes["distance_semantics"] = "NATIVE_WEIGHTED_DISTANCE_TO_ZONE_ONSHORE_REGION" if key == "offwind-ac" else "NATIVE_WEIGHTED_DISTANCE_TO_REGION_REPRESENTATIVE_POINT"
        for count in (1, 4, 8):
            summed = classes.loc[classes.resource_classes.eq(count)].groupby("zone").p_nom_max_MW.sum().reindex(ZONES)
            accepted_potential = density * availability @ area
            np.testing.assert_allclose(summed, accepted_potential.to_numpy(), rtol=1e-12, atol=1e-7)
        gpd.GeoDataFrame(classes, geometry=geometries, crs=4326).to_file(
            RESOURCE / f"{family}_RESOURCE_CLASS_GEOMETRY.geojson", driver="GeoJSON")
        xr.Dataset({"profile": profile, "p_nom_max_MW": potential,
                    "cell_CF": cell_cf, "class_masks": masks,
                    "average_distance_km": xr.DataArray(distances, coords={"class_id": classes.class_id.to_numpy()}, dims=["class_id"])}).to_netcdf(destination)
        classes.to_csv(QA / f"{family}_RESOURCE_CLASSES.csv", index=False)
        records.extend(classes.to_dict("records"))
        artifacts.extend([destination, RESOURCE / f"{family}_RESOURCE_CLASS_GEOMETRY.geojson", QA / f"{family}_RESOURCE_CLASSES.csv"])
        availability.close()
    cutout.data.close()
    receipt = {"status": "RESOURCE_CONVERSION_PASS_SITING_NOT_YET_ACCEPTED",
        "resource_class_records": len(records), "resource_classes": [1, 4, 8],
        "profile_weather_year": 2019, "snapshots": 8760,
        "binning": "LOCAL_PYPSA_EUR_EQUAL_WIDTH_CF_WITH_EPSILON_0P001",
        "physical_potential_reconciles_to_accepted_matrices": True,
        "production_optimization_executed": False, "production_solver_invocations": 0,
        "new_weather_downloads": 0,
        "artifacts": [{"path": str(p.relative_to(ROOT)), "sha256": sha256_file(p)} for p in artifacts]}
    dump_json(completed, receipt)
    return receipt


def refresh_native_distances():
    """Narrow compatibility repair: no weather conversion or profile change."""
    import atlite
    receipt_path = QA / "RESOURCE_CLASS_CONVERSION_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("native_distance_reference_exact"):
        return receipt
    for item in receipt["artifacts"]:
        if sha256_file(ROOT / item["path"]) != item["sha256"]:
            raise RuntimeError("WIND_RESOURCE_CACHE_HASH_FAIL")
    cutout = atlite.Cutout(str(CUTOUT_PATH))
    cutout.data = cutout.data.sel(x=slice(-6., 28.), y=slice(33., 53.))
    area = _cell_area_sqkm(cutout)
    coords = cutout.grid.representative_point().to_crs(3035)
    changes = []
    for key, family in FAMILIES.items():
        path = RESOURCE / f"{family}_RESOURCE_CLASSES_2019.nc"
        with xr.open_dataset(path) as opened:
            ds = opened.load()
        original_profile = ds.profile.copy(deep=True)
        original_potential = ds.p_nom_max_MW.copy(deep=True)
        classes = pd.read_csv(QA / f"{family}_RESOURCE_CLASSES.csv")
        land = gpd.read_file(ACCEPTED / "zonal_resource_geometry_onwind.geojson").set_index("bus")
        if key == "offwind-ac":
            land.geometry = land.geometry.map(native_offshore_distance_reference)
        else:
            land.geometry = land.geometry.representative_point()
        land = land.to_crs(3035)
        eligibility = xr.open_dataarray(ACCEPTED / f"availability_matrix_zonal_2019_{key}.nc").sel(
            x=cutout.coords["x"], y=cutout.coords["y"])
        density = 3 if key == "onwind" else 2
        layout = ds.cell_CF * area * density
        distances = []
        for row in classes.itertuples(index=False):
            weights = (layout * eligibility.sel(bus=row.zone) * ds.class_masks.sel(class_id=row.class_id)).stack(
                spatial=["y", "x"]).to_numpy()
            distance = float(np.dot(coords.distance(land.loc[row.zone, "geometry"]).to_numpy()/1e3, weights)/weights.sum()) if weights.sum() > 0 else 0.
            distances.append(distance)
        changes.append({"technology": family, "max_abs_distance_change_km": float(np.max(
            np.abs(classes.average_distance_km.to_numpy()-np.asarray(distances)))),
            "hourly_profiles_unchanged": True, "physical_potential_unchanged": True})
        classes["pre_native_reference_distance_km"] = classes.average_distance_km
        classes["average_distance_km"] = distances
        classes["distance_semantics"] = "LOCAL_NATIVE_ONSHORE_RESOURCE_REGION_REFERENCE;OFFSHORE_MINAREA_1_SIMPLIFICATION"
        ds["average_distance_km"] = xr.DataArray(distances, coords={"class_id": classes.class_id.to_numpy()}, dims=["class_id"])
        xr.testing.assert_identical(ds.profile, original_profile)
        xr.testing.assert_identical(ds.p_nom_max_MW, original_potential)
        ds.to_netcdf(path)
        classes.to_csv(QA / f"{family}_RESOURCE_CLASSES.csv", index=False)
        geometry_path = RESOURCE / f"{family}_RESOURCE_CLASS_GEOMETRY.geojson"
        geometry = gpd.read_file(geometry_path)
        geometry = geometry.drop(columns=[c for c in classes.columns if c != "class_id" and c in geometry.columns])
        geometry.merge(classes, on="class_id", validate="one_to_one").to_file(geometry_path, driver="GeoJSON")
        eligibility.close()
    cutout.data.close()
    receipt["native_distance_reference_exact"] = True
    receipt["distance_reference_repair"] = changes
    receipt["distance_source_logic"] = str(SOURCE_ROOT / "scripts/build_shapes.py")
    receipt["distance_source_logic_sha256"] = sha256_file(SOURCE_ROOT / "scripts/build_shapes.py")
    for item in receipt["artifacts"]:
        item["sha256"] = sha256_file(ROOT / item["path"])
    dump_json(receipt_path, receipt)
    return receipt

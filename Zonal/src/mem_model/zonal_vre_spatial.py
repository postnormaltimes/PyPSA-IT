"""Recover seven-zone VRE geometry from accepted geography and native resources.

This module neither builds an optimization model nor converts weather.  Onshore
resource polygons are intersected with the accepted market-zone land polygons.
Offshore resource polygons retain their ORIGINAL electrical-substation
attachment: the anchor is looked up by strict polygon containment, not by the
centroid of a clustered resource polygon or by a nearest-bus approximation.
Eligibility is recalculated from the existing local exclusion rasters using
the frozen PyPSA-Eur parameters; no uniform split of a coarse eligible mask is
assumed and no weather or source artifact is edited.
"""
from __future__ import annotations

import functools
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
from shapely.geometry import Point
from shapely.ops import unary_union
from shapely.wkt import loads as load_wkt

from .common import ROOT, ZONES, dump_json, sha256_file

SOURCE_ROOT = ROOT / "optional/pypsa-eur"
RESOURCE_NAME = "italy_dispatch_2013_1w_europe_local_64"
AUTHORITY_FILE = (ROOT.parent / "outputs/01a0595d-8cf1-7202-8ca1-1d3ab732e98e"
                  / "thermal_stack_phase/normalized/MEM_Province_Region_MarketZone_Crosswalk.csv")
CUTOUT_PATH = ROOT / "runtime_sources/weather/PyPSA_cutout_v1.0/europe-2019-sarah3-era5.nc"
NUTS_RELATIVE = "data/eu_nuts2021/archive/2021-01-01/ref-nuts-2021-01m.geojson/NUTS_RG_01M_2021_4326_LEVL_2.geojson"
# Administrative-code equivalence only; market-zone assignments come from the
# accepted, verified project crosswalk, never from this lookup.
ISTAT_TO_NUTS2 = {
    "01": ("ITC1",), "02": ("ITC2",), "03": ("ITC4",),
    "04": ("ITH1", "ITH2"), "05": ("ITH3",), "06": ("ITH4",),
    "07": ("ITC3",), "08": ("ITH5",), "09": ("ITI1",),
    "10": ("ITI2",), "11": ("ITI3",), "12": ("ITI4",),
    "13": ("ITF1",), "14": ("ITF2",), "15": ("ITF3",),
    "16": ("ITF4",), "17": ("ITF5",), "18": ("ITF6",),
    "19": ("ITG1",), "20": ("ITG2",),
}
FAMILIES = ("solar", "onwind", "offwind-ac")


def accepted_region_zones(authority_file: Path = AUTHORITY_FILE) -> pd.DataFrame:
    """Require exactly one verified zone per official administrative region."""
    frame = pd.read_csv(authority_file, dtype=str)
    required = {"region_code", "region_name", "market_zone", "review_status", "source_id"}
    if not required.issubset(frame.columns):
        raise ValueError("ZONE_AUTHORITY_SCHEMA_MISMATCH")
    if not frame.review_status.eq("VERIFIED_2024_EXACT_ONCE").all():
        raise ValueError("ZONE_AUTHORITY_NOT_VERIFIED")
    rows = frame[list(required)].drop_duplicates().sort_values("region_code")
    if rows.region_code.duplicated().any() or set(rows.region_code) != set(ISTAT_TO_NUTS2):
        raise ValueError("ZONE_AUTHORITY_REGION_AMBIGUITY")
    if set(rows.market_zone) != set(ZONES):
        raise ValueError("ZONE_AUTHORITY_NODESET_MISMATCH")
    return rows.reset_index(drop=True)


def zone_polygons(nuts: gpd.GeoDataFrame, authority: pd.DataFrame) -> gpd.GeoDataFrame:
    """Dissolve official NUTS2 polygons using accepted regional memberships."""
    nuts_zones = {}
    for row in authority.itertuples(index=False):
        for nuts_id in ISTAT_TO_NUTS2[row.region_code]:
            nuts_zones[nuts_id] = row.market_zone
    selected = nuts.loc[nuts.NUTS_ID.isin(nuts_zones)].copy()
    if selected.NUTS_ID.duplicated().any() or set(selected.NUTS_ID) != set(nuts_zones):
        raise ValueError("OFFICIAL_NUTS2_COVERAGE_MISMATCH")
    selected["zone"] = selected.NUTS_ID.map(nuts_zones)
    if not selected.geometry.is_valid.all():
        raise ValueError("OFFICIAL_NUTS2_INVALID_GEOMETRY")
    result = selected[["zone", "geometry"]].dissolve(by="zone").loc[list(ZONES)]
    result.index.name = "bus"
    if result.geometry.is_empty.any():
        raise ValueError("EMPTY_ZONE_GEOMETRY")
    assert_partition(unary_union(selected.geometry), list(result.geometry))
    return result


def assert_partition(footprint, parts: list, tolerance: float = 1e-9,
                     *, inherited_overlap: float = 0.0) -> dict:
    """Check area-wise topology, independently of resource/weather weighting."""
    union = unary_union(parts)
    missing = footprint.difference(union).area
    extra = union.difference(footprint).area
    overlap = max(0.0, sum(part.area for part in parts) - union.area)
    if max(missing, extra, abs(overlap - inherited_overlap)) > tolerance:
        raise ValueError(f"SPATIAL_PARTITION_FAIL: missing={missing}, extra={extra}, overlap={overlap}")
    return {"missing_area_degree2": float(missing), "extra_area_degree2": float(extra),
            "overlapping_area_degree2": float(overlap),
            "inherited_source_overlap_degree2": float(inherited_overlap), "status": "PASS"}


def strict_anchor_zone(point: Point, zones: gpd.GeoDataFrame) -> str:
    matches = zones.index[zones.geometry.covers(point)]
    if len(matches) != 1:
        raise ValueError(f"OFFSHORE_SUBSTATION_ZONE_AMBIGUITY: {point.wkt}, matches={list(matches)}")
    return str(matches[0])


def _native_regions(path: Path) -> gpd.GeoDataFrame:
    frame = gpd.read_file(path)
    frame = frame.loc[frame.country.eq("IT")].copy().set_index("name")
    frame.index.name = "native_source_id"
    if frame.empty or frame.index.duplicated().any() or not frame.geometry.is_valid.all():
        raise ValueError(f"INVALID_NATIVE_RESOURCE_GEOMETRY: {path}")
    return frame


def _area_km2(geometry) -> float:
    return float(gpd.GeoSeries([geometry], crs=4326).to_crs(3035).area.iloc[0] / 1e6)


def _resource_geometries(resource: Path, official: gpd.GeoDataFrame) -> tuple[dict, pd.DataFrame, dict]:
    """Return exact land intersections and inherited native offshore attachments."""
    onshore = _native_regions(resource / "regions_onshore.geojson")
    offshore = _native_regions(resource / "regions_offshore.geojson")
    first = pd.read_csv(resource / "busmap_base_s.csv", index_col=0).iloc[:, 0]
    second = pd.read_csv(resource / "busmap_base_s_64.csv", index_col=0).iloc[:, 0]
    first.index = first.index.astype(str)
    second.index = second.index.astype(str)
    busmap = first.map(second)
    records = []
    footprint = unary_union(onshore.geometry)
    land = official.copy()
    land["geometry"] = land.geometry.intersection(footprint)
    land_partition = assert_partition(footprint, list(land.geometry))
    for native_id, row in onshore.sort_index().iterrows():
        if native_id not in busmap or pd.isna(busmap[native_id]):
            raise ValueError(f"NATIVE_ONSHORE_CLUSTER_LINEAGE_MISSING: {native_id}")
        total = _area_km2(row.geometry)
        for zone in ZONES:
            intersection = row.geometry.intersection(land.loc[zone].geometry)
            if intersection.is_empty or intersection.area <= 1e-12:
                continue
            area = _area_km2(intersection)
            for family in ("solar", "onwind"):
                records.append({"family": family, "native_source_id": native_id,
                                "clustered_source_id": busmap[native_id], "zone": zone,
                                "mapping_method": "OFFICIAL_ZONE_POLYGON_RESOURCE_INTERSECTION",
                                "intersection_area_km2": area, "source_area_km2": total,
                                "geometry_area_fraction": area / total,
                                "resource_weight_semantics": "ELIGIBLE_AREA_AND_2019_CELL_RESOURCE_LAYOUT_NOT_BUS_MEAN"})
    offshore["zone"] = [strict_anchor_zone(Point(row.x, row.y), official)
                        for row in offshore.itertuples()]
    with xr.open_dataset(resource / "networks/base.nc") as network:
        for native_id, row in offshore.iterrows():
            if native_id not in network.buses_i.values:
                raise ValueError(f"OFFSHORE_SUBSTATION_MISSING: {native_id}")
            if (float(network.buses_x.sel(buses_i=native_id)) != row.x or
                    float(network.buses_y.sel(buses_i=native_id)) != row.y or
                    not bool(network.buses_substation_off.sel(buses_i=native_id).item())):
                raise ValueError(f"OFFSHORE_SUBSTATION_LINEAGE_MISMATCH: {native_id}")
    offshore["clustered_source_id"] = offshore.index.to_series().map(busmap)
    if offshore.clustered_source_id.isna().any():
        raise ValueError("NATIVE_OFFSHORE_CLUSTER_LINEAGE_MISSING")
    clustered = gpd.read_file(resource / "regions_offshore_base_s_64.geojson").set_index("name")
    clustered = clustered.loc[clustered.index.str.startswith("IT")]
    if set(clustered.index) != set(offshore.clustered_source_id):
        raise ValueError("OFFSHORE_CLUSTERED_RESOURCE_SET_MISMATCH")
    max_difference = 0.0
    for name, row in clustered.iterrows():
        union = unary_union(offshore.loc[offshore.clustered_source_id.eq(name)].geometry)
        difference = union.symmetric_difference(row.geometry).area
        max_difference = max(max_difference, difference)
    if max_difference > 1e-9:
        raise ValueError("OFFSHORE_NATIVE_CLUSTER_GEOMETRY_MISMATCH")
    sea = offshore[["zone", "geometry"]].dissolve(by="zone").loc[list(ZONES)]
    sea.index.name = "bus"
    # Original source polygons are not necessarily disjoint. Preserve their
    # actual attachment rather than inventing a tie-breaking maritime boundary.
    # Their known overlap is diagnosed separately, not silently repaired.
    inherited_overlap = max(0.0, sum(unary_union(group.geometry).area
                                    for _, group in offshore.groupby("zone"))
                             - unary_union(offshore.geometry).area)
    sea_partition = assert_partition(unary_union(offshore.geometry), list(sea.geometry),
                                     inherited_overlap=inherited_overlap)
    inherited_pairs = []
    for left in range(len(offshore)):
        for right in offshore.sindex.query(offshore.geometry.iloc[left], predicate="intersects"):
            if right <= left or offshore.zone.iloc[left] == offshore.zone.iloc[right]:
                continue
            intersection = offshore.geometry.iloc[left].intersection(offshore.geometry.iloc[right])
            if intersection.area <= 1e-10:
                continue
            inherited_pairs.append({"source_id_a": offshore.index[left], "source_id_b": offshore.index[right],
                                    "zone_a": offshore.zone.iloc[left], "zone_b": offshore.zone.iloc[right],
                                    "clustered_source_id_a": offshore.clustered_source_id.iloc[left],
                                    "clustered_source_id_b": offshore.clustered_source_id.iloc[right],
                                    "overlap_area_km2": _area_km2(intersection),
                                    "overlap_area_degree2": float(intersection.area),
                                    "overlap_geometry_wkt": intersection.wkt})
    for native_id, row in offshore.sort_index().iterrows():
        area = _area_km2(row.geometry)
        records.append({"family": "offwind-ac", "native_source_id": native_id,
                        "clustered_source_id": row.clustered_source_id, "zone": row.zone,
                        "mapping_method": "INHERITED_NATIVE_OFFSHORE_SUBSTATION_ATTACHMENT_STRICT_POLYGON",
                        "anchor_x": row.x, "anchor_y": row.y,
                        "intersection_area_km2": area, "source_area_km2": area,
                        "geometry_area_fraction": 1.0,
                        "resource_weight_semantics": "ELIGIBLE_AREA_AND_2019_CELL_RESOURCE_LAYOUT_NOT_BUS_MEAN"})
    return {"solar": land, "onwind": land, "offwind-ac": sea}, pd.DataFrame(records), {
        "onshore_partition": land_partition, "offshore_partition": sea_partition,
        "original_onshore_region_count": len(onshore), "original_offshore_region_count": len(offshore),
        "offshore_native_substation_coordinates_exact": True,
        "offshore_native_to_clustered_geometry_max_difference_degree2": float(max_difference),
        "offshore_native_source_count_by_zone": offshore.zone.value_counts().to_dict(),
        "offshore_inherited_native_geometry_overlap": inherited_pairs,
        "offshore_overlap_treatment": "SOURCE_NATIVE_GEOMETRY_AND_ELECTRICAL_ATTACHMENT_PRESERVED_NO_REPAIR",
        "old_clustered_offshore_overlap_area_degree2": max(0.0, sum(geometry.area for geometry in clustered.geometry)
                                                            - unary_union(clustered.geometry).area),
        "nearest_bus_assignments": 0, "proxy_crosswalks_used": 0,
    }


def _build_excluder(source_root: Path, resource: Path, params: dict):
    """Faithful eligibility logic from frozen PyPSA-Eur source script."""
    import atlite
    paths = {
        "natura": source_root / "data/natura/archive/2025-08-15/natura.tiff",
        "corine": source_root / "data/corine/archive/v18_5/corine.tif",
        "gebco": source_root / "data/gebco/archive/2014/GEBCO_2014_2D.nc",
        "ship_density": resource / "shipdensity_raster.tif",
        "country_shapes": resource / "country_shapes.geojson",
    }
    resolution = params.get("excluder_resolution", 100)
    excluder = atlite.ExclusionContainer(crs=3035, res=resolution)

    def existing(name):
        if name not in paths or not paths[name].is_file():
            raise ValueError(f"LOCAL_EXCLUSION_SOURCE_MISSING: {name}")
        return str(paths[name])

    if params["natura"]:
        excluder.add_raster(existing("natura"), nodata=0, allow_no_overlap=True)
    for dataset in ("corine", "luisa"):
        settings = params.get(dataset, {})
        if not settings:
            continue
        if isinstance(settings, list):
            settings = {"grid_codes": settings}
        kwargs = {"nodata": 0} if dataset == "luisa" else {}
        if "grid_codes" in settings:
            excluder.add_raster(existing(dataset), codes=settings["grid_codes"], invert=True,
                                crs=3035, **kwargs)
        if settings.get("distance", 0.0) > 0.0:
            excluder.add_raster(existing(dataset), codes=settings["distance_grid_codes"],
                                buffer=settings["distance"], crs=3035, **kwargs)
    if params.get("ship_threshold"):
        threshold = params["ship_threshold"] * 8760 * 6
        excluder.add_raster(existing("ship_density"), codes=functools.partial(np.less, threshold),
                            crs=4326, allow_no_overlap=True)
    if params.get("max_depth"):
        excluder.add_raster(existing("gebco"), codes=functools.partial(np.greater, -params["max_depth"]),
                            crs=4326, nodata=-1000)
    if params.get("min_depth"):
        excluder.add_raster(existing("gebco"), codes=functools.partial(np.greater, -params["min_depth"]),
                            crs=4326, nodata=-1000, invert=True)
    if params.get("min_shore_distance") is not None:
        excluder.add_geometry(existing("country_shapes"), buffer=params["min_shore_distance"])
    if params.get("max_shore_distance") is not None:
        excluder.add_geometry(existing("country_shapes"), buffer=params["max_shore_distance"], invert=True)
    return excluder, {name: str(path) for name, path in paths.items()}


def build_spatial_package(out_dir: Path, *, source_root: Path = SOURCE_ROOT,
                          authority_file: Path = AUTHORITY_FILE,
                          cutout_path: Path = CUTOUT_PATH) -> dict:
    """Generate geometry/crosswalk/eligibility ONLY; no temporal weather conversion.

    Returns availability[family] in (bus,y,x) with the canonical seven-zone order,
    crosswalk, QA, frozen conversion/excluder parameters and output paths.
    """
    import atlite
    out_dir = Path(out_dir)
    source_root = Path(source_root)
    authority_file = Path(authority_file)
    cutout_path = Path(cutout_path)
    resource = source_root / "resources" / RESOURCE_NAME
    for path in (authority_file, cutout_path, resource / "networks/base.nc", source_root / NUTS_RELATIVE):
        if not path.is_file():
            raise ValueError(f"LOCAL_SPATIAL_SOURCE_MISSING: {path}")
    authority = accepted_region_zones(authority_file)
    official = zone_polygons(gpd.read_file(source_root / NUTS_RELATIVE), authority)
    geometries, crosswalk, qa = _resource_geometries(resource, official)
    with xr.open_dataset(resource / "networks/base.nc") as network:
        parameters = json.loads(network.attrs["meta"])["renewable"]
    cutout = atlite.Cutout(cutout_path)
    if len(cutout.data.time) != 8760 or int(cutout.data.time.dt.year.min()) != 2019 or int(cutout.data.time.dt.year.max()) != 2019:
        raise ValueError("LOCAL_CUTOUT_NOT_2019_8760")
    out_dir.mkdir(parents=True, exist_ok=True)
    official.to_file(out_dir / "official_market_zone_land_polygons.geojson", driver="GeoJSON", index=True)
    crosswalk_path = out_dir / "spatial_source_market_zone_crosswalk.csv"
    crosswalk.to_csv(crosswalk_path, index=False, float_format="%.15g")
    availability = {}
    paths = {"crosswalk": str(crosswalk_path), "official_land_geometry": str(out_dir / "official_market_zone_land_polygons.geojson")}
    eligibility = {}
    for family in FAMILIES:
        print(f"Building zonal eligibility from existing local exclusion sources: {family}", flush=True)
        geometry_path = out_dir / f"zonal_resource_geometry_{family}.geojson"
        geometries[family].to_file(geometry_path, driver="GeoJSON", index=True)
        excluder, exclusion_paths = _build_excluder(source_root, resource, parameters[family])
        matrix = cutout.availabilitymatrix(geometries[family], excluder, nprocesses=1,
                                          disable_progressbar=True).sel(bus=list(ZONES))
        values = matrix.values
        if not np.isfinite(values).all() or values.min() < 0 or values.max() > 1 + 1e-12:
            raise ValueError(f"INVALID_ELIGIBILITY_MATRIX: {family}")
        eligible_area = (matrix * xr.DataArray(cutout.grid.to_crs(3035).area.values.reshape(len(cutout.data.y), len(cutout.data.x)) / 1e6,
                                             dims=("y", "x"), coords={"y": cutout.data.y, "x": cutout.data.x})).sum(("y", "x"))
        if (eligible_area <= 0).any():
            raise ValueError(f"ZONE_WITHOUT_ELIGIBLE_VRE_RESOURCE: {family}, {eligible_area.to_series().to_dict()}")
        matrix_path = out_dir / f"availability_matrix_zonal_2019_{family}.nc"
        matrix.to_netcdf(matrix_path)
        availability[family] = matrix
        paths[f"geometry_{family}"] = str(geometry_path)
        paths[f"matrix_{family}"] = str(matrix_path)
        eligibility[family] = {"eligible_area_km2_by_zone": eligible_area.to_series().to_dict(),
                               "parameters": parameters[family], "exclusion_source_paths": exclusion_paths,
                               "source_logic": str(source_root / "scripts/determine_availability_matrix.py"),
                               "source_logic_sha256": sha256_file(source_root / "scripts/determine_availability_matrix.py"),
                               "no_uniform_mask_partition": True}
        if family == "offwind-ac" and qa["offshore_inherited_native_geometry_overlap"]:
            overlap = unary_union([load_wkt(row["overlap_geometry_wkt"])
                                   for row in qa["offshore_inherited_native_geometry_overlap"]])
            overlap_frame = gpd.GeoDataFrame({"bus": ["SOURCE_NATIVE_OVERLAP"], "geometry": [overlap]},
                                             crs=4326).set_index("bus")
            overlap_matrix = cutout.availabilitymatrix(overlap_frame, excluder, nprocesses=1,
                                                       disable_progressbar=True)
            cell_area = xr.DataArray(cutout.grid.to_crs(3035).area.values.reshape(len(cutout.data.y), len(cutout.data.x)) / 1e6,
                                     dims=("y", "x"), coords={"y": cutout.data.y, "x": cutout.data.x})
            overlap_eligible = float((overlap_matrix * cell_area).sum())
            overlap_path = out_dir / "availability_matrix_inherited_offshore_overlap.nc"
            overlap_matrix.to_netcdf(overlap_path)
            paths["matrix_offshore_native_overlap"] = str(overlap_path)
            eligibility[family]["inherited_native_overlap_eligible_area_km2"] = overlap_eligible
            affected = sorted({row[key] for row in qa["offshore_inherited_native_geometry_overlap"]
                               for key in ("zone_a", "zone_b")})
            eligibility[family]["inherited_native_overlap_fraction_of_zone_eligible_area"] = {
                zone: overlap_eligible / float(eligible_area.sel(bus=zone)) for zone in affected}
    qa.update({"status": "PASS", "authority_path": str(authority_file),
               "authority_sha256": sha256_file(authority_file),
               "nuts2_path": str(source_root / NUTS_RELATIVE),
               "nuts2_sha256": sha256_file(source_root / NUTS_RELATIVE),
               "eligibility": eligibility, "weather_downloads": 0,
               "optimization_model_constructed": False, "solver_invocations": 0,
               "geometries_and_matrices": {key: {"path": path, "sha256": sha256_file(Path(path))}
                                           for key, path in paths.items()}})
    qa_path = out_dir / "spatial_mapping_eligibility_qa.json"
    dump_json(qa_path, qa)
    paths["qa"] = str(qa_path)
    return {"availability": availability, "crosswalk": crosswalk, "qa": qa,
            "parameters": {family: parameters[family] for family in FAMILIES}, "paths": paths}

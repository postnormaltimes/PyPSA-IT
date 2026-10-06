"""Non-solving tests of authority selection and spatial partition semantics."""
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

from mem_model import zonal_vre_spatial as spatial


def test_complete_existing_authority_and_corrected_boundaries():
    rows = spatial.accepted_region_zones()
    assert len(rows) == 20
    mapping = rows.set_index("region_code").market_zone
    assert mapping["15"] == "CSUD"  # Campania, not the recovered nearest-bus proxy.
    assert mapping["10"] == "CSUD"  # Umbria, not the recovered six-zone proxy.
    assert mapping["14"] == "SUD"
    assert mapping["18"] == "CALA"
    assert set(mapping) == set(spatial.ZONES)


def test_unverified_authority_refused(tmp_path):
    frame = pd.read_csv(spatial.AUTHORITY_FILE, dtype=str)
    frame.loc[0, "review_status"] = "APPROVAL_DEPENDENT_PROXY"
    path = tmp_path / "authority.csv"
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="NOT_VERIFIED"):
        spatial.accepted_region_zones(path)


def test_conflicting_region_membership_refused(tmp_path):
    frame = pd.read_csv(spatial.AUTHORITY_FILE, dtype=str)
    frame.loc[0, "market_zone"] = "SUD"
    path = tmp_path / "authority.csv"
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="REGION_AMBIGUITY"):
        spatial.accepted_region_zones(path)


def test_land_partition_rejects_gap():
    with pytest.raises(ValueError, match="SPATIAL_PARTITION_FAIL"):
        spatial.assert_partition(box(0, 0, 2, 1), [box(0, 0, 1, 1)])


def test_land_partition_rejects_overlap():
    with pytest.raises(ValueError, match="SPATIAL_PARTITION_FAIL"):
        spatial.assert_partition(box(0, 0, 2, 1), [box(0, 0, 1.5, 1), box(1, 0, 2, 1)])


def test_land_partition_rejects_extra_area():
    with pytest.raises(ValueError, match="SPATIAL_PARTITION_FAIL"):
        spatial.assert_partition(box(0, 0, 2, 1), [box(0, 0, 3, 1)])


def test_partition_exact_union_passes():
    assert spatial.assert_partition(box(0, 0, 2, 1), [box(0, 0, 1, 1), box(1, 0, 2, 1)])["status"] == "PASS"


def test_offshore_anchor_is_not_nearest_bus():
    zones = gpd.GeoDataFrame({"bus": ["CALA", "SICI"], "geometry": [box(0, 0, 1, 1), box(2, 0, 3, 1)]}, crs=4326).set_index("bus")
    assert spatial.strict_anchor_zone(Point(0.5, 0.5), zones) == "CALA"
    with pytest.raises(ValueError, match="ZONE_AMBIGUITY"):
        spatial.strict_anchor_zone(Point(1.01, 0.5), zones)


def test_boundary_anchor_refused_instead_of_choosing_zone():
    zones = gpd.GeoDataFrame({"bus": ["CSUD", "SUD"], "geometry": [box(0, 0, 1, 1), box(1, 0, 2, 1)]}, crs=4326).set_index("bus")
    with pytest.raises(ValueError, match="ZONE_AMBIGUITY"):
        spatial.strict_anchor_zone(Point(1, 0.5), zones)


def test_native_source_geometry_recovery_is_complete_without_proxies():
    authority = spatial.accepted_region_zones()
    official = spatial.zone_polygons(gpd.read_file(spatial.SOURCE_ROOT / spatial.NUTS_RELATIVE), authority)
    geometries, crosswalk, qa = spatial._resource_geometries(spatial.SOURCE_ROOT / "resources" / spatial.RESOURCE_NAME, official)
    assert qa["onshore_partition"]["status"] == "PASS"
    assert qa["offshore_partition"]["status"] == "PASS"
    assert qa["offshore_native_substation_coordinates_exact"] is True
    assert qa["original_offshore_region_count"] == 161
    assert qa["offshore_native_source_count_by_zone"]["CALA"] == 15
    assert qa["offshore_native_to_clustered_geometry_max_difference_degree2"] == 0
    overlap = qa["offshore_inherited_native_geometry_overlap"]
    assert len(overlap) == 1
    assert {overlap[0]["zone_a"], overlap[0]["zone_b"]} == {"SUD", "CALA"}
    assert overlap[0]["clustered_source_id_a"] == overlap[0]["clustered_source_id_b"] == "IT2 3"
    assert overlap[0]["overlap_area_km2"] == pytest.approx(119.79565914367873)
    assert qa["offshore_partition"]["inherited_source_overlap_degree2"] > 0
    assert qa["old_clustered_offshore_overlap_area_degree2"] == pytest.approx(0, abs=1e-9)
    assert qa["nearest_bus_assignments"] == qa["proxy_crosswalks_used"] == 0
    assert set(crosswalk.zone) == set(spatial.ZONES)
    assert not crosswalk[["family", "native_source_id", "zone"]].duplicated().any()
    assert crosswalk.loc[crosswalk.family.eq("offwind-ac"), "geometry_area_fraction"].eq(1).all()
    for frame in geometries.values():
        assert list(frame.index) == list(spatial.ZONES)


def test_eligibility_uses_existing_rasters_and_original_exclusion_parameters():
    class FakeExcluder:
        def __init__(self, **kwargs):
            self.settings = kwargs
            self.calls = []

        def add_raster(self, *args, **kwargs):
            self.calls.append(("raster", args, kwargs))

        def add_geometry(self, *args, **kwargs):
            self.calls.append(("geometry", args, kwargs))

    with patch("atlite.ExclusionContainer", FakeExcluder):
        excluder, paths = spatial._build_excluder(
            spatial.SOURCE_ROOT, spatial.SOURCE_ROOT / "resources" / spatial.RESOURCE_NAME,
            {"natura": True, "ship_threshold": 400, "max_depth": 60,
             "max_shore_distance": 30000, "excluder_resolution": 200})
    assert excluder.settings == {"crs": 3035, "res": 200}
    assert len(excluder.calls) == 4
    shipping = excluder.calls[1][2]["codes"]
    assert shipping(np.array([0, 400 * 8760 * 6 + 1])).tolist() == [False, True]
    assert excluder.calls[-1][2] == {"buffer": 30000, "invert": True}
    assert all(Path(call[1][0]).is_file() for call in excluder.calls)


def test_import_and_geometry_helpers_do_not_construct_model_or_solve():
    import inspect
    code = inspect.getsource(spatial)
    assert "import pypsa" not in code
    assert "import linopy" not in code
    assert ".prepare(" not in code
    assert ".optimize(" not in code
    assert ".solve(" not in code

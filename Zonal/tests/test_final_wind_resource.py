"""Small isolated array tests; no optimization models or solvers."""
import numpy as np
import pytest
import xarray as xr

from mem_model.final_wind_resource import resource_class_masks, fixed_mw_allocation, reported_capacity_heuristic, native_offshore_distance_reference
from mem_model import stage_b_zonal_vre as z


@pytest.fixture(autouse=True)
def prohibit_models():
    with z.no_models_or_solves():
        yield


@pytest.mark.parametrize("count", [1, 4, 8])
def test_native_bins_partition_footprint_once(count):
    cf = xr.DataArray(np.arange(12).reshape(3,4)/12, dims=["y", "x"])
    indicator = xr.DataArray(np.ones((7,3,4)), dims=["bus", "y", "x"], coords={"bus": list(z.ZONES)})
    masks, records = resource_class_masks(cf, indicator, count)
    assert len(records) == 7*count
    for zone in z.ZONES:
        names = records.loc[records.zone.eq(zone), "class_id"].to_numpy()
        np.testing.assert_array_equal(masks.sel(class_id=names).sum("class_id"), indicator.sel(bus=zone))


def test_analytic_siting_respects_capacity_and_physical_bounds():
    result = fixed_mw_allocation(12., [5.,7.,10.], [.2,.4,.3])
    np.testing.assert_array_equal(result, [0.,7.,5.])


def test_physical_potential_cannot_be_relaxed():
    with pytest.raises(RuntimeError, match="EXCEEDS_PHYSICAL"):
        fixed_mw_allocation(23., [5.,7.,10.], [.2,.4,.3])


def test_native_reported_heuristic_is_comparison_not_silent_cap_change():
    raw, violations, truncated = reported_capacity_heuristic(12., [5.,7.], [.1,.5])
    assert np.isclose(raw.sum(), 12.)
    assert violations.any()
    assert truncated.sum() == raw.sum()


def test_native_distance_simplification_does_not_alter_resource_attachment_geometry():
    from shapely.geometry import box, MultiPolygon
    source = MultiPolygon([box(0,0,2,2), box(5,5,5.1,5.1)])
    before = source.wkt
    reference = native_offshore_distance_reference(source)
    assert source.wkt == before
    assert reference.area == 4.
    assert reference.distance(box(5,5,5.1,5.1)) > 0

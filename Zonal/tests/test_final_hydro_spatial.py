"""Zonal hydrology transformation gates without weather/model/solver calls."""
import numpy as np
import pandas as pd
import pytest

from mem_model import final_hydro_spatial as hydro
from mem_model.common import ROOT, load_yaml
from mem_model.stage_b_zonal_vre import no_models_or_solves


@pytest.fixture(autouse=True)
def prohibit_models_and_solvers():
    with no_models_or_solves():
        yield


def test_explicit_resolution_uses_zone_shapes_not_catchments():
    cfg=load_yaml(hydro.CONFIG)
    assert cfg['spatial_adapter']=='ATLITE_RUNOFF_OVER_ACCEPTED_MARKET_ZONE_POLYGONS'
    assert cfg['plant_catchment_routing_required'] is False
    assert cfg['within_zone_natural_hydro_classes_share_driver'] is True
    assert cfg['runoff']['normalize_using_yearly'] is None


def test_exact_installed_native_runoff_semantics_remain_pinned():
    api=hydro.installed_runoff_api()
    assert api['weight_with_height'] is True
    assert api['smooth_hours']==168
    assert api['threshold_quantile']==.005
    assert api['aggregate_time'] is None


def test_sufficient_spill_cap_is_unchanged():
    assert hydro.spill_safety_cap(100.,99.)==100.
    assert hydro.spill_safety_cap(100.,100.)==100.


def test_spill_cap_update_adds_only_one_float_ulp_no_pump_power():
    result=hydro.spill_safety_cap(100.,200.)
    assert result==np.nextafter(200.,np.inf)
    assert result-200. == np.spacing(200.)


@pytest.mark.parametrize('invalid',[np.nan,np.inf,-1.])
def test_invalid_spill_cap_is_not_repaired(invalid):
    with pytest.raises(RuntimeError,match='NONNEGATIVE_FINITE'):
        hydro.spill_safety_cap(invalid,100.)


@pytest.fixture
def water_case(tmp_path,monkeypatch):
    monkeypatch.setattr(hydro,'QA',tmp_path)
    monkeypatch.setattr(hydro,'OUTPUT',tmp_path)
    times=pd.date_range('2019-01-01',periods=8760,freq='h',tz='UTC')
    shapes=pd.DataFrame({'snapshot':np.tile(times,7),'zone':np.repeat(hydro.ZONES,8760),
        'normalized_annual_share':np.full(8760*7,1/8760)})
    census=pd.DataFrame([{'year':2040,'scenario':'Base','zone':'NORD','hydro_class':c,
        'runtime_id':c,'annual_inflow_MWh_water':8760.,'turbine_efficiency':.9,
        'turbine_MW_NET':10.,'spill_MW_water_cap':.5} for c in ('RUN_OF_RIVER','BASIN_PONDAGE','RESERVOIR','MIXED_PHS')])
    census.to_csv(tmp_path/'HYDRO_CURRENT_LINEAGE_AND_STATE_CENSUS.csv',index=False)
    return shapes,census,tmp_path


def test_all_natural_classes_share_zone_driver_and_annual_controls_exact(water_case):
    shapes,_,directory=water_case
    receipt=hydro.candidate_water_profiles(shapes)
    assert receipt['state']=='HYDRO_ZONAL_ANNUAL_AND_ROR_POWER_QA_PASS'
    assert receipt['annual_water_controls_PASS']==4
    values=pd.read_parquet(directory/'ZONAL_NATURAL_INFLOW_CANDIDATE_2019.parquet')
    assert values.groupby('hydro_id').inflow_MW_water_equivalent.sum().eq(8760.).all()
    assert 'PURE_PHS' not in set(values.hydro_class)
    assert receipt['spill_caps_requiring_safety_update']==3


def test_overrated_ROR_is_reported_without_clipping_or_network_export(water_case):
    shapes,census,directory=water_case
    census.loc[census.hydro_class.eq('RUN_OF_RIVER'),'turbine_MW_NET']=.1
    census.to_csv(directory/'HYDRO_CURRENT_LINEAGE_AND_STATE_CENSUS.csv',index=False)
    receipt=hydro.candidate_water_profiles(shapes)
    assert receipt['state']=='HYDRO_ROR_TURBINE_COMPATIBILITY_FAIL'
    assert receipt['ROR_incompatible_hours']==8760
    assert receipt['network_exports']==0
    assert (directory/'ZONAL_ROR_POWER_COMPATIBILITY_CONFLICTS.csv').exists()


def test_native_ROR_turbine_saturation_preserves_water_with_explicit_bypass():
    water=np.array([0.,1.,20.])
    pu,accessible,bypass=hydro.ror_turbine_mapping(water,9.,.9)
    np.testing.assert_array_equal(pu,np.array([0.,.1,1.]))
    np.testing.assert_array_equal(accessible,np.array([0.,1.,10.]))
    np.testing.assert_array_equal(bypass,np.array([0.,0.,10.]))
    np.testing.assert_array_equal(accessible+bypass,water)
    # No renormalization: capped electrical potential is distinct from the
    # frozen annual natural-water authority.
    assert pu.sum()*9. != water.sum()*.9


@pytest.mark.parametrize('water,MW,eta',[([-1.],9.,.9),([np.nan],9.,.9),([1.],0.,.9),([1.],9.,0.)])
def test_native_ROR_mapping_fails_closed_on_nonphysical_inputs(water,MW,eta):
    with pytest.raises(RuntimeError,match='PHYSICAL_MAPPING_FAIL'):
        hydro.ror_turbine_mapping(water,MW,eta)

import numpy as np
import pandas as pd
import pypsa
import pytest

from mem_model.reporting.ror_water import ror_water_accounting
from mem_model.stage_b_zonal_vre import no_models_or_solves


@pytest.fixture(autouse=True)
def no_execution():
    with no_models_or_solves():
        yield


@pytest.fixture
def example():
    n=pypsa.Network(); n.set_snapshots(pd.date_range('2019-01-01',periods=3,freq='h',name='snapshot'))
    n.add('Bus','NORD'); n.add('Generator','ror',bus='NORD',carrier='hydro_run_of_river',p_nom=90,efficiency=.9)
    n.generators_t.p_max_pu['ror']=[.5,1.,.5]
    n.generators_t.p['ror']=[45.,80.,0.]
    n.snapshot_weightings.loc[:,['stores','generators']]=2.
    source=pd.DataFrame({'year':2040,'scenario':'Base','hydro_id':'ror','zone':'NORD',
        'snapshot':n.snapshots.tz_localize('UTC'),'natural_inflow_MW_water':[50.,150.,50.],
        'turbine_accessible_MW_water':[50.,100.,50.],'unavoidable_bypass_MW_water':[0.,50.,0.],
        'electrical_potential_MW':[45.,90.,45.]})
    return n,source


def test_water_bypass_generation_and_curtailment_are_distinct_and_weighted_once(example):
    n,source=example
    old=n.generators.copy(); dispatch=n.generators_t.p.copy()
    hourly,annual=ror_water_accounting(n,source,2040,'Base')
    row=annual.iloc[0]
    assert row.RAW_ROR_WATER_INFLOW_MWh_water==500
    assert row.TURBINE_CONVERTIBLE_ROR_WATER_MWh_water==400
    assert row.ROR_BYPASS_MWh_water==100
    assert row.ROR_ELECTRICAL_GENERATION_MWh==250
    assert row.ROR_ELECTRICAL_CURTAILMENT_MWh==110
    assert len(hourly)==3
    pd.testing.assert_frame_equal(old,n.generators)
    pd.testing.assert_frame_equal(dispatch,n.generators_t.p)


@pytest.mark.parametrize('mutation',['duplicate','missing','bad_water','bad_zone','bad_dispatch'])
def test_inconsistent_water_or_physical_dispatch_fails(example,mutation):
    n,source=example
    if mutation=='duplicate': source=pd.concat([source,source.iloc[:1]])
    if mutation=='missing': source=source.iloc[:-1]
    if mutation=='bad_water': source.loc[0,'natural_inflow_MW_water']=999
    if mutation=='bad_zone': source.loc[0,'zone']='SUD'
    if mutation=='bad_dispatch': n.generators_t.p.loc[n.snapshots[0],'ror']=1000
    with pytest.raises(ValueError): ror_water_accounting(n,source,2040,'Base')


def test_native_store_water_dual_survives_export_without_model_construction(tmp_path):
    n=pypsa.Network();n.set_snapshots(pd.date_range('2019-01-01',periods=3,freq='h'))
    n.add('Bus','W',carrier='water_energy');n.add('Store','S',bus='W',e_nom=100,e_cyclic=True)
    n.stores_t.mu_energy_balance['S']=[-5.,0.,7.]
    path=tmp_path/'dual_fixture.nc';n.export_to_netcdf(path)
    reloaded=pypsa.Network(path)
    np.testing.assert_array_equal(reloaded.stores_t.mu_energy_balance['S'],[-5.,0.,7.])

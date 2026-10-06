"""Final A-prime hierarchy tests; inputs are valid cached-style roots only."""
import inspect
import json
import pandas as pd
import pytest
from mem_model.reporting.marginal_setter_a_prime import (
    classify_families, resolve_attribution, APrimeError, ORDER, zone_shares,
)
from mem_model.reporting.marginal_setter_attribution import ZONES

@pytest.mark.parametrize('n,f,i,inf,category,rule',[
    (1,['VRE'],0,[],'VRE','UNIQUE_VALID_ROOT'),
    (4,['VRE'],4,['VRE'],'VRE','UNIQUE_VALID_FAMILY'),
    (2,['BESS','PHS'],2,['BESS','PHS'],'Storage','STORAGE_COMARGINAL'),
    (2,['BESS','PHS'],0,[],'Storage','STORAGE_COMARGINAL'),
    (2,['BESS','PHS'],1,['BESS'],'BESS','INTERIOR_SINGLE_FAMILY'),
    (3,['VRE','BESS','PHS'],2,['BESS','PHS'],'Storage','STORAGE_COMARGINAL'),
    (2,['VRE','BESS'],2,['VRE','BESS'],'Mixed / co-marginal','MIXED_COMARGINAL'),
    (2,['CCGT','Reservoir / pondage hydro'],0,[],'Mixed / co-marginal','MIXED_COMARGINAL'),
    (2,['CCGT','P2X'],1,['P2X'],'P2X','INTERIOR_SINGLE_FAMILY'),
    (2,['PHS','Reservoir / pondage hydro'],2,['PHS','Reservoir / pondage hydro'],'Mixed / co-marginal','MIXED_COMARGINAL'),
    (2,['External market'],2,['External market'],'External market','UNIQUE_VALID_FAMILY'),
])
def test_exact_hierarchy(n,f,i,inf,category,rule):
    r=classify_families(n,f,i,inf)
    assert r['final_setter_category']==category and r['final_resolution_rule']==rule
    assert r==classify_families(n,f[::-1],i,inf[::-1])

def fixture(remote=False):
    snapshot=pd.Timestamp('2019-01-01')
    table=pd.DataFrame([dict(model_year=2040,scenario='Base',snapshot=snapshot,zone='NORD',
        primary_attribution_class='MARKET_COUPLING' if remote else 'MULTIPLE_CANDIDATES',zonal_price_EUR_MWh=0.,represented_hours=1.)])
    roots=[]
    for technology,carrier,component,asset,p in [('BESS','battery_energy','Link','battery',20.),('PHS','water_energy','Link','pumping',80.)]:
        roots.append(dict(snapshot=snapshot,target_zone='NORD',remote=remote,zone='SUD' if remote else 'NORD',technology=technology,carrier=carrier,component=component,asset_id=asset,
            mechanism='STORAGE_HYDRO_OPPORTUNITY_VALUE',coupling_path='["first"]',native_power=p,operating_lower=0.,operating_upper=100.))
    return table,pd.DataFrame(roots)

@pytest.mark.parametrize('remote',[False,True])
def test_complete_root_identity_and_scope_no_path_order_effect(remote):
    t,a=fixture(remote);result,_=resolve_attribution(t,a)
    duplicated=pd.concat([a.iloc[::-1],a.iloc[:1].assign(coupling_path='["second"]')],ignore_index=True)
    other,_=resolve_attribution(t,duplicated)
    assert result.equals(other) and result[t.columns].equals(t)
    assert result.final_setter_category.iloc[0]=='Storage'
    assert result.valid_root_count.iloc[0]==2
    assert result.resolution_scope.iloc[0]==('COUPLED' if remote else 'LOCAL')

def test_zero_output_valid_root_is_retained_not_supply_filtered():
    t,a=fixture();a.native_power=0.
    result,_=resolve_attribution(t,a)
    assert result.final_setter_category.iloc[0]=='Storage'
    assert result.interior_root_count.iloc[0]==0

def test_full_set_retained_when_interior_subset_resolves():
    t,a=fixture();a.loc[1,'native_power']=100.
    result,_=resolve_attribution(t,a)
    assert result.final_setter_category.iloc[0]=='BESS'
    assert set(json.loads(result.valid_root_families.iloc[0]))=={'BESS','PHS'}
    assert json.loads(result.resolution_root_families.iloc[0])==['BESS']
    assert result.resolution_root_count.iloc[0]==1

@pytest.mark.parametrize('args',[(0,[],0,[]),(1,['VRE'],1,['BESS']),(1,['VRE'],1,[])])
def test_uncovered_or_invalid_set_fails_closed(args):
    with pytest.raises(APrimeError):classify_families(*args)

def test_unmapped_root_fails_closed():
    t,a=fixture();a.loc[0,'carrier']='unmapped'
    with pytest.raises(RuntimeError,match='UNMAPPED'):resolve_attribution(t,a)

def test_local_coupled_mixed_scope_fails():
    t,a=fixture();a.loc[1,'remote']=True
    with pytest.raises(APrimeError,match='mixed'):resolve_attribution(t,a)

def test_no_asset_winner_or_price_zero_rule():
    t,a=fixture();zero,_=resolve_attribution(t,a)
    t.zonal_price_EUR_MWh=9999.;t.scenario='High';other,_=resolve_attribution(t,a)
    assert zero.final_setter_category.equals(other.final_setter_category)
    assert not any('selected_marginal_asset'==x for x in other)
    code=inspect.getsource(resolve_attribution)+inspect.getsource(classify_families)
    for banned in ['select_offers','normalised_acceptance_ratio','hashlib','tie_key','gme_priority_rank']:
        assert banned not in code

def test_zone_alignment_and_time_share_denominator():
    t=pd.DataFrame([dict(zone=z,final_setter_category='BESS' if i%2 else 'Storage',represented_hours=1.) for i,z in enumerate(ZONES)])
    s=zone_shares(t)
    assert list(s.index)==list(ZONES) and list(s.columns)==list(ORDER)
    assert s.sum(axis=1).eq(100).all() and s.loc['CNORD','BESS']==100

def test_solver_network_opens_forbidden(monkeypatch):
    import pypsa
    from mem_model.reporting.uc2_postprocess import no_solver_calls
    monkeypatch.setattr(pypsa,'Network',lambda *a,**k:(_ for _ in ()).throw(AssertionError('network open')))
    with no_solver_calls() as guard:
        t,a=fixture();resolve_attribution(t,a)
    assert guard['solver_invocations']==0

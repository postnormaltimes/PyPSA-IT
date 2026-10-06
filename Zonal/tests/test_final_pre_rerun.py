"""Focused arithmetic/static successor tests. All model/solver entry points forbidden."""
import copy

import numpy as np
import pandas as pd
import pypsa
import pytest

from mem_model import final_pre_rerun as p
from mem_model.stage_b_zonal_vre import no_models_or_solves


@pytest.fixture(autouse=True)
def no_solve():
    with no_models_or_solves():
        yield


@pytest.mark.parametrize("scale", [.05,.5,1.,1.8,4.])
def test_bounded_factor_exact_with_caps(scale):
    x=np.array([0.,.01,.15,.35,.6,.9,1.])
    w=np.array([1.,2.,1.,3.,1.,2.,1.])
    target=float((np.minimum(1,scale*x)*w).sum())
    factor,y=p.bounded_factor(x,target,w)
    assert factor==pytest.approx(scale,abs=1e-12)
    assert float(np.dot(y,w))==pytest.approx(target,abs=1e-12)
    assert y.min()>=0 and y.max()<=1 and y[0]==0
    assert (np.diff(y)>=0).all()


def test_infeasible_target_fails_closed():
    with pytest.raises(RuntimeError,match="PHYSICALLY_INFEASIBLE"):
        p.bounded_factor(np.array([0.,.1]),1.1)


def test_zero_target():
    factor,result=p.bounded_factor(np.array([0.,.4]),0)
    assert factor==0 and np.array_equal(result,[0,0])


@pytest.mark.parametrize("shape_scale", [.1,.5,.8])
def test_hierarchical_temporal_energy_and_order(shape_scale):
    t=pd.date_range("2019-01-01",periods=8760,freq="h")
    x=shape_scale*(.15+.85*np.sin(np.arange(len(t))*.017)**2)
    x[::17]=0
    w=np.ones(8760)
    q=np.array([1.2,.9,.75,1.1])
    m=np.array([1.3,1.1,1.2,1.,.9,.8,.7,.75,.85,.9,1.1,1.3])
    a,b,qr,mr=p.onshore_temporal(x,t,w,q,m)
    assert a.sum()==pytest.approx(x.sum(),abs=1e-10)
    assert b.sum()==pytest.approx(x.sum(),abs=1e-10)
    for quarter in range(1,5):
        mask=t.quarter==quarter
        assert b[mask].sum()==pytest.approx(a[mask].sum(),abs=1e-10)
    assert b.max()<=1 and b.min()>=0 and np.array_equal(b[x==0],x[x==0])
    for month in range(1,13):
        mask=t.month==month
        assert (np.diff(b[mask][np.argsort(x[mask])])>=-1e-14).all()
    assert len(qr)==4 and len(mr)==12


def fixture_network():
    n=pypsa.Network()
    n.set_snapshots(pd.date_range("2019-01-01",periods=24,freq="h"))
    n.add("Bus","NORD")
    n.add("Carrier","wind_onshore")
    n.add("Generator","wind",bus="NORD",carrier="wind_onshore",p_nom=100,p_max_pu=np.linspace(0,1,24))
    n.add("Generator","gas",bus="NORD",p_nom=200,committable=True,min_up_time=4,min_down_time=6)
    n.meta={"accepted":"parent"}
    return n


def test_exact_diff_whitelist_accepts_vre_only():
    a=fixture_network();b=a.copy()
    b.generators_t.p_max_pu["wind"]*=.5
    b.meta[p.META]={"parent_sha256":"a"*64}
    assert p.exact_diff(a,b)


@pytest.mark.parametrize("drift",["capacity","cost","load","carrier","metadata","snapshot_weights","p2x","gas_availability"])
def test_exact_diff_rejects_other_changes(drift):
    a=fixture_network();b=a.copy();b.meta[p.META]={"parent_sha256":"a"*64}
    if drift=="capacity":b.generators.at["wind","p_nom"]+=1
    elif drift=="cost":b.generators.at["gas","marginal_cost"]+=1
    elif drift=="load":b.add("Load","new",bus="NORD",p_set=1)
    elif drift=="carrier":b.generators.at["wind","carrier"]="solar_pv_rooftop"
    elif drift=="metadata":b.meta["accepted"]="changed"
    elif drift=="snapshot_weights":b.snapshot_weightings.iloc[0,0]=2
    elif drift=="p2x":b.add("Generator","P2X_NORD",bus="NORD",sign=-1,p_nom=5)
    elif drift=="gas_availability":b.generators_t.p_max_pu["gas"]=.7
    with pytest.raises(RuntimeError,match="UNEXPECTED_SUCCESSOR_DRIFT"):
        p.exact_diff(a,b)


def test_exact_init_requires_named_compatible_unit():
    a=fixture_network();b=a.copy();b.meta[p.META]={"parent_sha256":"a"*64}
    b.generators.at["gas","up_time_before"]=4
    with pytest.raises(RuntimeError,match="DRIFT"):p.exact_diff(a,b)
    assert p.exact_diff(a,b,{"gas":{"up_time_before":4}})


def test_smoke_no_identity_mapping_means_no_change():
    parent=fixture_network();smoke=fixture_network()
    smoke.generators.rename(index={"gas":"different"},inplace=True)
    changes,rows=p.smoke_initialization(parent,smoke,2040,"Base")
    assert changes=={} and rows[0]["treatment"]=="RETAIN_CURRENT_INITIAL_CONDITION"


def test_smoke_compatible_duration_cap_and_p_init_retained():
    parent=fixture_network();smoke=parent.copy()
    smoke.generators_t.status=pd.DataFrame({"gas":np.ones(24)},index=smoke.snapshots)
    init,rows=p.smoke_initialization(parent,smoke,2040,"Base")
    assert init=={"gas":{"up_time_before":4,"down_time_before":0}}
    assert np.isnan(parent.generators.at["gas","p_init"])
    assert rows[0]["duration_beyond_smoke_unknown"]


def test_targets_are_source_ratios_not_solved_metrics():
    assert p.TARGETS["WIND"]==pytest.approx(.2813194580066773,abs=1e-15)
    assert p.TARGETS["SOLAR"]==pytest.approx(.15849654703951094,abs=1e-15)
    assert p.FLAGS["optimizer_invocations"]==0 and not p.FLAGS["optimization_model_constructed"]


def test_import_and_cli_require_explicit_non_solve_action():
    with pytest.raises(SystemExit):p.main([])
    with pytest.raises(SystemExit):p.main(["run-uc"])


def test_solar_user_delta_uses_unmodified_profiles_and_unit_scalar():
    n=fixture_network()
    n.add('Generator','solar',bus='NORD',carrier='solar_pv_rooftop',p_nom=100,p_max_pu=np.full(24,.2))
    factors,rows=p.derive_scalars(n)
    solar=next(row for row in rows if row['family']=='SOLAR')
    assert factors['SOLAR']==1.0
    assert solar['proposed_bounded_scalar']<1.0
    assert solar['result_CF']==solar['native_CF']
    assert solar['available_TWh_after']==solar['available_TWh_before']
    assert solar['treatment']=='RETAIN_NATIVE_SOLAR_USER_DELTA'


def test_solar_is_not_in_successor_diff_whitelist():
    a=fixture_network()
    a.add('Generator','solar',bus='NORD',carrier='solar_pv_rooftop',p_nom=100,p_max_pu=np.full(24,.2))
    b=a.copy();b.meta[p.META]={'parent_sha256':'a'*64}
    b.generators_t.p_max_pu['solar']*=.984
    with pytest.raises(RuntimeError,match='UNEXPECTED_SUCCESSOR_DRIFT'):
        p.exact_diff(a,b)


def test_static_closeout_rejects_failed_tests_before_any_preflight(tmp_path):
    report=tmp_path/'failed.xml'
    report.write_text('<testsuites><testsuite tests="1" failures="1" errors="0" skipped="0"/></testsuites>')
    with pytest.raises(RuntimeError,match='STATIC_FOCUSED_TESTS_NOT_PASS'):
        p.close_static(report)


def test_boundary_diagnostics_do_not_change_saved_ramps(tmp_path,monkeypatch):
    monkeypatch.setattr(p,'QA',tmp_path)
    original=pd.DataFrame({
        'cyclic_december_january':[True,False],
        'native_ramp_pu':[.65,.023],
        'stage0_ramp_pu':[.74,.026],
        'q2q_ramp_pu':[.55,.058],
        'final_ramp_pu':[.67,.456],
        'final_interior_max_abs_ramp_pu':[.453,.453],
        'outside_full_interior_ramp_range':[True,True]})
    original.to_csv(tmp_path/'WIND_TEMPORAL_BOUNDARY_RAMP_QA.csv',index=False)
    result=p.refresh_boundary_diagnostics()
    pd.testing.assert_frame_equal(result[original.columns],original,check_exact=True)
    assert result.inherited_non_chronological_year_wrap.tolist()==[True,False]
    assert result.adjustment.tolist()==['NO_NEW_TREATMENT_INHERITED_YEAR_WRAP_RETAINED','REVIEW_REQUIRED']

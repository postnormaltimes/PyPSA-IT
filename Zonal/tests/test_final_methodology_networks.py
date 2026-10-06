"""Final-family routing, continuous counterfactual and no-execution tests."""
import inspect
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa
import pytest

from mem_model import final_methodology_networks as final
from mem_model import stage_b_uc2 as uc2
from mem_model.stage_b_zonal_vre import no_models_or_solves
from mem_model.common import sha256_file


@pytest.fixture(autouse=True)
def prohibit_models_and_solvers():
    with no_models_or_solves():
        yield


def test_solver_frozen_and_all_six_final_cases_are_authorized():
    cfg=final.settings()
    assert cfg['solver']['options']=={'Threads':1,'Seed':0}
    assert cfg['solver']['required_version']=='13.0.3'
    assert cfg['solver']['MIPGap_override'] is None
    assert cfg['manual_authorization']=={
        '2040_Slow':True,'2040_Base':True,'2040_High':True,
        '2050_Slow':True,'2050_Base':True,'2050_High':True}
    assert cfg['no_automatic_execution'] is True
    assert cfg['water_values']['divide_by_objective_weight'] is False


def test_variant_is_explicit_task_local_and_restored():
    ordinary=uc2.job_id(2040,'Base',uc2.RUN_TYPES[1])
    with uc2.execution_variant('final_v1'):
        assert uc2.job_id(2040,'Base',uc2.RUN_TYPES[1]).startswith('FINAL_V1_UC2_')
        assert uc2.config()['result_root']=='results/final_methodology_v1'
    assert uc2.job_id(2040,'Base',uc2.RUN_TYPES[1])==ordinary
    with pytest.raises(ValueError,match='UNKNOWN_VARIANT'):
        with uc2.execution_variant('typo'):
            pass


@pytest.mark.parametrize('year,scenario',uc2.SCENARIOS)
def test_six_final_case_mappings_and_eighteen_unique_job_paths(year,scenario,monkeypatch):
    monkeypatch.setattr(final,'package',lambda y,s:{'path':f'uc/{y}/{s}.nc','reference_path':f'lp/{y}/{s}.nc',
        'sha256':'a'*64,'reference_sha256':'b'*64})
    with uc2.execution_variant('final_v1'):
        paths=[uc2.job_paths(year,scenario,k) for k in uc2.RUN_TYPES]
        assert len({p['solved'] for p in paths})==3
        assert all('final_methodology_v1' in p['directory'].parts for p in paths)
        assert paths[0]['input'].as_posix().endswith(f'lp/{year}/{scenario}.nc')
        assert paths[1]['input']==paths[2]['input']
        assert uc2._expected_hash(year,scenario,uc2.RUN_TYPES[0])=='b'*64
        assert uc2._expected_hash(year,scenario,uc2.RUN_TYPES[1])=='a'*64


def test_final_gate_never_inherits_old_manual_enable_flags(tmp_path,monkeypatch):
    state=tmp_path/'STATE.json'
    state.write_text(json.dumps({'state':'FINAL_CANONICAL_NETWORKS_PREPARED',
        'phases':{p:{'status':'PASS'} for p in 'WHNCF'},'semantic_parity':{'status':'PASS'}}))
    monkeypatch.setattr(final,'STATE',state)
    assert uc2.config()['governance']['2040_base_manual_sequence_enabled'] is True
    assert uc2.config()['governance']['2050_base_manual_enabled'] is True
    assert uc2.config()['governance']['2050_slow_high_manual_enabled'] is True
    with uc2.execution_variant('final_v1'):
        for y,s in uc2.SCENARIOS:
            assert uc2._governance_gate(y,s)==f'FINAL_V1_MANUAL_EXECUTION_{y}_{s.upper()}'


@pytest.mark.parametrize('year,scenario',uc2.SCENARIOS)
def test_disabled_individual_case_is_fail_closed(year,scenario,monkeypatch):
    cfg=final.settings()
    cfg['manual_authorization'][f'{year}_{scenario}']=False
    monkeypatch.setattr(final,'settings',lambda:cfg)
    with uc2.execution_variant('final_v1'):
        with pytest.raises(RuntimeError,match='FINAL_EXECUTION_LOCKED'):
            uc2._governance_gate(year,scenario)
        for y,s in uc2.SCENARIOS:
            if (y,s)!=(year,scenario):
                assert uc2._governance_gate(y,s)==f'FINAL_V1_MANUAL_EXECUTION_{y}_{s.upper()}'


def test_final_manual_gate_refuses_invalid_or_unprepared_case(tmp_path,monkeypatch):
    with pytest.raises(ValueError,match='FINAL_UNKNOWN_SCENARIO'):
        final.manual_gate(2050,'Invalid')
    state=tmp_path/'STATE.json'
    prepared={'state':'FINAL_CANONICAL_NETWORKS_PREPARED',
        'phases':{p:{'status':'PASS'} for p in 'WHNCF'},'semantic_parity':{'status':'PASS'}}
    monkeypatch.setattr(final,'STATE',state)
    for phase in 'WHNCF':
        candidate=json.loads(json.dumps(prepared));candidate['phases'][phase]['status']='PENDING'
        state.write_text(json.dumps(candidate))
        with pytest.raises(RuntimeError,match='FINAL_EXECUTION_LOCKED'):
            final.manual_gate(2050,'Base')
    candidate=json.loads(json.dumps(prepared));candidate['semantic_parity']['status']='PENDING'
    state.write_text(json.dumps(candidate))
    with pytest.raises(RuntimeError,match='FINAL_EXECUTION_LOCKED'):
        final.manual_gate(2050,'Base')


def test_continuous_counterfactual_keeps_calibrated_child_cost_capacity_and_system():
    network=pypsa.Network()
    network.set_snapshots(pd.date_range('2019-01-01',periods=3,freq='h'))
    network.add('Bus','NORD')
    network.add('Carrier','ccgt')
    network.add('Generator','UC__child',bus='NORD',carrier='ccgt',p_nom=123.,efficiency=.54,
        marginal_cost=95.,committable=True,p_min_pu=.45,ramp_limit_up=1.,min_up_time=4,start_up_cost=7380.)
    network.add('Generator','P2X_NORD',bus='NORD',p_nom=20.,sign=-1,committable=False)
    network.generators_t.p_max_pu['UC__child']=[.8,.9,1.]
    original=pypsa.Network(); original.add('Bus','NORD')
    original.add('Generator','parent',bus='NORD',p_nom=500.,efficiency=.6,marginal_cost=80.)
    plan=pd.DataFrame({'child_id':['UC__child'],'parent_id':['parent']})
    reference=final.continuous_counterfactual(network,original,plan)
    assert not reference.generators.committable.any()
    assert reference.generators.at['UC__child','p_min_pu']==0
    assert np.isnan(reference.generators.at['UC__child','ramp_limit_up'])
    for field in ('p_nom','efficiency','marginal_cost'):
        assert reference.generators.at['UC__child',field]==network.generators.at['UC__child',field]
    pd.testing.assert_frame_equal(reference.generators_t.p_max_pu,network.generators_t.p_max_pu)
    pd.testing.assert_series_equal(reference.generators.loc['P2X_NORD'],network.generators.loc['P2X_NORD'])
    assert network.generators.at['UC__child','committable']


def test_final_variant_cannot_redirect_prepare_or_bulk_actions():
    with pytest.raises(SystemExit):
        uc2.main(['prepare','--variant','final_v1'])


def test_same_scenario_dependency_checks_and_dual_capture_remain_in_existing_boundary():
    source=inspect.getsource(uc2._manual_solve)
    assert '_verified_previous(year, scenario, "POST_P2X_CONTINUOUS_REFERENCE")' in source
    assert '_verified_previous(year, scenario, "UC_MILP")' in source
    assert 'capture_store_energy_balance_coverage' in source
    assert source.index('capture_store_energy_balance_coverage(network') < source.index('network.export_to_netcdf')
    assert 'assign_all_duals=kind != "UC_MILP"' in source
    assert 'for year, scenario' not in source
    assert 'UC2_OVERWRITE_REFUSED' in source
    assert '_check_input_hash(year, scenario, kind)' in source
    price_source=inspect.getsource(uc2.verify_price)
    assert 'fixed_commitment_milp_sha256' in price_source
    assert 'receipt["integer_variables"] or receipt["binary_variables"]' in price_source
    assert '(8760, 7)' in price_source


@pytest.mark.parametrize('field,value',[('year',2050),('scenario','Slow'),('solved_sha256','stale')])
def test_same_case_verified_dependency_still_refuses_cross_case_or_stale_hash(field,value,monkeypatch):
    verification={'status':'PASS','year':2040,'scenario':'Base','solved_sha256':'accepted'}
    verification[field]=value
    monkeypatch.setattr(uc2,'job_paths',lambda *args:{'verification':Path('unused.json')})
    monkeypatch.setattr(uc2,'_read_json',lambda path:verification)
    monkeypatch.setattr(uc2,'_load_solved',lambda *args:(None,{'solved_sha256':'accepted'}))
    with uc2.execution_variant('final_v1'):
        with pytest.raises(RuntimeError,match='UC2_DEPENDENCY_FAIL'):
            uc2._verified_previous(2040,'Base','UC_MILP')


@pytest.mark.parametrize('flag',[['--variant','final_v1'],['--variant=final_v1']])
def test_variant_cli_reuses_non_solving_action_without_automatic_successor(monkeypatch,capsys,flag):
    calls=[]
    monkeypatch.setattr(uc2,'verify_reference',lambda y,s:calls.append((y,s,uc2.ACTIVE_VARIANT.get())) or {'status':'PASS'})
    uc2.main(['verify-reference','--year','2040','--scenario','Base',*flag])
    assert calls==[(2040,'Base','final_v1')]
    assert uc2.ACTIVE_VARIANT.get() is None
    assert json.loads(capsys.readouterr().out)=={'status':'PASS'}


def test_change_inventory_accepts_only_approved_temporal_differences():
    n=pypsa.Network();n.set_snapshots(pd.date_range('2019-01-01',periods=3,freq='h'))
    n.add('Bus','NORD');n.add('Generator','wind',bus='NORD',carrier='wind_onshore',p_nom=100)
    n.generators_t.p_max_pu['wind']=[.1,.2,.3]
    out=n.copy();out.generators_t.p_max_pu['wind']=[.2,.3,.4]
    changes=final.approved_change_inventory(n,out,2040,'Base')
    assert len(changes)==1 and changes[0]['changed_cells_or_snapshots']==3
    assert changes[0]['approved_authority'].startswith('W ')
    out.generators.at['wind','p_nom']=101
    with pytest.raises(RuntimeError,match='UNAPPROVED_STATIC'):
        final.approved_change_inventory(n,out,2040,'Base')


@pytest.mark.parametrize('mutation',[None,'missing_manifest','changed_manifest','changed_receipt'])
def test_freeze_manifest_and_receipts_are_pinned_before_preflight(tmp_path,monkeypatch,mutation):
    directory=tmp_path/'qa'; directory.mkdir()
    receipt=directory/'receipt.json';receipt.write_text('{}')
    manifest=directory/'FINAL_ARTIFACT_MANIFEST.csv'
    pd.DataFrame([{'path':'input.nc','sha256':'a'*64}]).to_csv(manifest,index=False)
    verification=directory/'FINAL_VERIFICATION.json'
    verification.write_text(json.dumps({'state':'FINAL_CANONICAL_NETWORKS_PREPARED','manifest_sha256':sha256_file(manifest)}))
    state=tmp_path/'state.json'
    state.write_text(json.dumps({'state':'FINAL_CANONICAL_NETWORKS_PREPARED','semantic_parity':{'status':'PASS'},
        'phases':{'F':{'created_artifacts':[{'path':str(p.relative_to(tmp_path)),'sha256':sha256_file(p)}
                    for p in (receipt,manifest,verification)]}}}))
    monkeypatch.setattr(final,'ROOT',tmp_path);monkeypatch.setattr(final,'STATE',state)
    monkeypatch.setattr(final,'QA',directory);monkeypatch.setattr(final,'RECEIPT',receipt)
    if mutation=='missing_manifest': manifest.unlink()
    if mutation=='changed_manifest': manifest.write_text('changed')
    if mutation=='changed_receipt': receipt.write_text('{"altered":true}')
    if mutation:
        with pytest.raises(RuntimeError,match='CONTRACT_HASH_FAIL'): final.verify_frozen_contract()
    else:
        assert final.verify_frozen_contract()=={'input.nc':'a'*64}

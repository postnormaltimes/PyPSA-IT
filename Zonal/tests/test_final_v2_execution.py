"""Execution integration only: no network build, model constructor or solver."""
import inspect
import json

import pytest

from mem_model import final_methodology_networks as final
from mem_model import stage_b_uc2 as uc2
from mem_model.stage_b_zonal_vre import no_models_or_solves


@pytest.fixture(autouse=True)
def forbid_model_and_solver(monkeypatch):
    monkeypatch.setattr(uc2, '_solver_contract', lambda: pytest.fail('solver inspected'))
    monkeypatch.setattr(uc2, '_manual_solve', lambda *a, **k: pytest.fail('solve dispatched'))
    with no_models_or_solves():
        yield


def test_all_six_enabled_and_solver_contract_unchanged():
    cfg=final.settings('final_v2')
    assert all(cfg['manual_authorization'][f'{y}_{s}'] is True for y,s in uc2.SCENARIOS)
    assert cfg['no_automatic_execution'] is True
    assert cfg['solver']==final.settings()['solver']
    assert cfg['water_values']==final.settings()['water_values']
    assert cfg['result_root']=='results/final_methodology_v2'


@pytest.mark.parametrize('year,scenario',uc2.SCENARIOS)
def test_six_cases_and_three_jobs_are_isolated(year,scenario):
    with uc2.execution_variant('final_v1'):
        v1=[uc2.job_paths(year,scenario,k) for k in uc2.RUN_TYPES]
    with uc2.execution_variant('final_v2'):
        assert uc2._governance_gate(year,scenario)==f'FINAL_V2_MANUAL_EXECUTION_{year}_{scenario.upper()}'
        jobs=[uc2.job_paths(year,scenario,k) for k in uc2.RUN_TYPES]
        assert len({p['solved'] for p in jobs})==3
        for k,old,p in zip(uc2.RUN_TYPES,v1,jobs):
            for field in ('directory','solved','receipt','verification'):
                assert p[field]!=old[field]
                assert 'final_methodology_v2' in p[field].parts
                assert p[field].is_relative_to(uc2.ROOT/'results/final_methodology_v2'/str(year)/scenario)
            assert 'final_methodology_v2' in p['input'].parts
            assert uc2._check_input_hash(year,scenario,k)==uc2._expected_hash(year,scenario,k)
        assert jobs[0]['input']!=jobs[1]['input']==jobs[2]['input']
    assert uc2.ACTIVE_VARIANT.get() is None


def test_all_eighteen_job_identifiers_and_destinations_are_unique():
    with uc2.execution_variant('final_v2'):
        jobs=[(uc2.job_id(y,s,k),uc2.job_paths(y,s,k)['solved'])
              for y,s in uc2.SCENARIOS for k in uc2.RUN_TYPES]
    assert len(jobs)==len(set(jobs))==18
    assert len({path for _,path in jobs})==18


@pytest.mark.parametrize('year,scenario',uc2.SCENARIOS)
def test_individual_disable_still_fails_closed_without_unlocking_others(year,scenario,monkeypatch):
    original=final.settings
    cfg=original('final_v2')
    cfg['manual_authorization'][f'{year}_{scenario}']=False
    monkeypatch.setattr(final,'settings',lambda variant='final_v1': cfg if variant=='final_v2' else original())
    with uc2.execution_variant('final_v2'):
        with pytest.raises(RuntimeError,match='FINAL_EXECUTION_LOCKED'):
            uc2._governance_gate(year,scenario)
        for y,s in uc2.SCENARIOS:
            if (y,s)!=(year,scenario):
                assert uc2._governance_gate(y,s).startswith('FINAL_V2_')


@pytest.mark.parametrize('flags',[['--variant','final_v2'],['--variant=final_v2']])
def test_cli_verification_keeps_variant_and_does_not_chain(flags,monkeypatch,capsys):
    calls=[]
    monkeypatch.setattr(uc2,'verify_reference',lambda y,s: calls.append((y,s,uc2.ACTIVE_VARIANT.get())) or {'status':'PASS'})
    uc2.main(['verify-reference','--year','2050','--scenario','Base',*flags])
    assert calls==[(2050,'Base','final_v2')]
    assert json.loads(capsys.readouterr().out)=={'status':'PASS'}
    assert uc2.ACTIVE_VARIANT.get() is None


@pytest.mark.parametrize('action',['prepare','reconcile-preparation','list-jobs'])
def test_no_variant_bulk_or_default_execution(action):
    with pytest.raises(SystemExit):
        uc2.main([action,'--variant','final_v2'])


def test_preflight_routes_statically_without_result_writes(monkeypatch):
    calls=[]
    monkeypatch.setattr(final,'preflight',lambda y,s,**kw: calls.append((y,s,kw)) or {'status':'PASS'})
    with uc2.execution_variant('final_v2'):
        assert uc2.preflight(2050,'Base',persist=False)['status']=='PASS'
    assert calls==[(2050,'Base',{'persist':False,'variant':'final_v2'})]


def test_tampered_accepted_control_fails_before_network_loading(monkeypatch):
    monkeypatch.setattr(final,'sha256_file',lambda p:'0'*64)
    with pytest.raises(RuntimeError,match='FINAL_V2_FROZEN_CONTROL_HASH_FAIL'):
        final.verify_v2_frozen_contract()


@pytest.mark.parametrize('field,value',[('year',2040),('scenario','Slow'),('solved_sha256','stale')])
def test_same_case_verified_dependencies_remain_mandatory(field,value,monkeypatch):
    verified={'status':'PASS','year':2050,'scenario':'Base','solved_sha256':'right'}
    verified[field]=value
    monkeypatch.setattr(uc2,'_read_json',lambda p:verified)
    monkeypatch.setattr(uc2,'_load_solved',lambda *a:(None,{'solved_sha256':'right'}))
    with uc2.execution_variant('final_v2'):
        with pytest.raises(RuntimeError,match='UC2_DEPENDENCY_FAIL'):
            uc2._verified_previous(2050,'Base','UC_MILP')


def test_final_v2_preflight_has_no_model_or_solver_entry_point():
    source=inspect.getsource(final.preflight)
    assert 'with no_models_or_solves()' in source
    assert '.optimize' not in source and 'create_model' not in source
    assert 'continuous=True' in source


def test_new_family_does_not_mutate_v1_settings():
    original=final.settings()
    with uc2.execution_variant('final_v2'):
        assert uc2.config()['result_root']=='results/final_methodology_v2'
    assert final.settings()==original
    assert uc2.config()['result_root']=='results/uc2_full_year'

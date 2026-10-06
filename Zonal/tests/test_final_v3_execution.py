"""Single-action routing only; no simulation/model or production-output creation."""
import json

import pytest

from mem_model import final_methodology_networks as final
from mem_model import stage_b_uc2 as uc2
from mem_model.stage_b_zonal_vre import no_models_or_solves


@pytest.fixture(autouse=True)
def prohibit_simulations(monkeypatch):
    monkeypatch.setattr(uc2,'_manual_solve',lambda *a,**k:pytest.fail('solve dispatched'))
    monkeypatch.setattr(uc2,'_solver_contract',lambda *a,**k:pytest.fail('solver inspected'))
    with no_models_or_solves():yield


@pytest.mark.parametrize('year,scenario',uc2.SCENARIOS)
def test_direct_sparse_successor_paths_and_hashes(year,scenario):
    with uc2.execution_variant('final_v3'):
        paths=[uc2.job_paths(year,scenario,k) for k in uc2.RUN_TYPES]
        assert len({p['solved'] for p in paths})==3
        assert paths[0]['input']!=paths[1]['input']==paths[2]['input']
        for k,p in zip(uc2.RUN_TYPES,paths):
            assert p['input'].is_relative_to(uc2.ROOT/'networks/unsolved/final_methodology_v3')
            assert p['solved'].is_relative_to(uc2.ROOT/'results/final_methodology_v3'/str(year)/scenario)
            assert uc2._check_input_hash(year,scenario,k)==uc2._expected_hash(year,scenario,k)
        inherited=final.settings('final_v2')['solver']
        runtime=uc2.config()['solver']
        assert runtime=={key:inherited[key] for key in runtime}
        assert inherited['MIPGap_override'] is None


def test_eighteen_distinct_jobs_and_no_v1_v2_destinations():
    with uc2.execution_variant('final_v3'):
        jobs=[(uc2.job_id(y,s,k),uc2.job_paths(y,s,k)['solved']) for y,s in uc2.SCENARIOS for k in uc2.RUN_TYPES]
    assert len(set(jobs))==18 and len({p for _,p in jobs})==18
    assert all(p.is_relative_to(uc2.ROOT/'results/final_methodology_v3') for _,p in jobs)
    assert all(not p.is_relative_to(uc2.ROOT/'results'/old) for _,p in jobs for old in ('final_methodology_v1','final_methodology_v2'))


@pytest.mark.parametrize('flags',[['--variant','final_v3'],['--variant=final_v3']])
def test_manual_cli_verification_variant_lifetime_and_no_next_action(flags,monkeypatch,capsys):
    calls=[]
    monkeypatch.setattr(uc2,'verify_reference',lambda y,s:calls.append((y,s,uc2.ACTIVE_VARIANT.get())) or {'status':'PASS'})
    uc2.main(['verify-reference','--year','2050','--scenario','Base',*flags])
    assert calls==[(2050,'Base','final_v3')]
    assert uc2.ACTIVE_VARIANT.get() is None
    assert json.loads(capsys.readouterr().out)=={'status':'PASS'}


def test_mandatory_same_case_verified_dependency(monkeypatch):
    monkeypatch.setattr(uc2,'_read_json',lambda p:{'status':'PASS','year':2040,'scenario':'Base','solved_sha256':'wrong'})
    monkeypatch.setattr(uc2,'_load_solved',lambda *a:(None,{'solved_sha256':'right'}))
    with uc2.execution_variant('final_v3'):
        with pytest.raises(RuntimeError,match='DEPENDENCY_FAIL'):uc2._verified_previous(2050,'Base','UC_MILP')


def test_default_and_older_variant_routes_unchanged():
    original=uc2.config()
    for variant in ('final_v1','final_v2'):
        with uc2.execution_variant(variant):assert uc2.config()['result_root']==f'results/final_methodology_{variant[-2:]}'
    assert uc2.config()==original


def test_manual_configuration_preserves_solver_and_review_sequence():
    cfg=final.settings('final_v3')
    assert all(cfg['manual_authorization'].values())
    assert cfg['no_automatic_execution'] is True
    assert cfg['solver']==final.settings('final_v2')['solver']
    assert cfg['water_values']==final.settings('final_v2')['water_values']
    assert cfg['review_sequence']['first']=='2050_Base'
    assert cfg['review_sequence']['second_after_first_review']=='2040_Base'
    assert cfg['review_sequence']['remaining_four']=='AFTER_BOTH_BASE_COMPARISONS_ACCEPTED'


def test_reporting_protected_sources_keep_final_v3_routes(monkeypatch):
    from mem_model.reporting import uc2_postprocess as post
    monkeypatch.setattr(post,'sha256_file',lambda p:'fixture-only-not-a-result')
    with uc2.execution_variant('final_v3'):
        sources=post._protected_sources(2050,'Base')
        assert sources
        assert any('final_methodology_v3' in str(p) for p in sources)
        assert not any('results/final_methodology_v1' in str(p).replace('\\','/') for p in sources)
        assert not any('results/final_methodology_v2' in str(p).replace('\\','/') for p in sources)


def test_final_static_gate_required_for_manual_execution(monkeypatch):
    def blocked(**kw):raise RuntimeError('FINAL_V3_STATIC_GATE_NOT_PASS')
    monkeypatch.setattr(final,'verify_v3_frozen_contract',blocked)
    with uc2.execution_variant('final_v3'):
        with pytest.raises(RuntimeError,match='STATIC_GATE_NOT_PASS'):uc2._governance_gate(2050,'Base')


def test_no_default_or_scenario_loop_command():
    with pytest.raises(SystemExit):uc2.main(['--variant','final_v3'])

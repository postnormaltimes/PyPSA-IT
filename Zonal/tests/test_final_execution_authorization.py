"""Per-case manual authorization and fail-closed preparation controls."""
import json
import pytest
from mem_model import final_methodology_networks as final
from mem_model import stage_b_uc2 as execution
from mem_model.stage_b_zonal_vre import no_models_or_solves


@pytest.fixture(autouse=True)
def prohibit_optimization_models_and_solves():
    with no_models_or_solves():
        yield


def test_all_six_case_flags_and_solver_contract():
    config=final.settings()
    assert config['manual_authorization']=={f'{y}_{s}':True for y,s in execution.SCENARIOS}
    assert config['solver']['required_version']=='13.0.3'
    assert config['solver']['options']=={'Threads':1,'Seed':0}
    assert config['solver']['MIPGap_override'] is None
    assert config['no_automatic_execution'] is True
    final.verify_frozen_contract()
    for y,s in execution.SCENARIOS:
        assert final.manual_gate(y,s)==f'FINAL_V1_MANUAL_EXECUTION_{y}_{s.upper()}'


@pytest.mark.parametrize('year,scenario',execution.SCENARIOS)
def test_individual_case_can_be_disabled(year,scenario,monkeypatch):
    config=final.settings()
    config['manual_authorization'][f'{year}_{scenario}']=False
    monkeypatch.setattr(final,'settings',lambda:config)
    with pytest.raises(RuntimeError,match='FINAL_EXECUTION_LOCKED'):
        final.manual_gate(year,scenario)
    for y,s in execution.SCENARIOS:
        if (y,s)!=(year,scenario):
            assert final.manual_gate(y,s)==f'FINAL_V1_MANUAL_EXECUTION_{y}_{s.upper()}'


@pytest.mark.parametrize('missing',['W','H','N','C','F','semantic_parity'])
def test_unprepared_case_is_rejected(missing,tmp_path,monkeypatch):
    state={'state':'FINAL_CANONICAL_NETWORKS_PREPARED','phases':{p:{'status':'PASS'} for p in 'WHNCF'},'semantic_parity':{'status':'PASS'}}
    if missing=='semantic_parity':state['semantic_parity']['status']='PENDING'
    else:state['phases'][missing]['status']='PENDING'
    path=tmp_path/'state.json';path.write_text(json.dumps(state))
    monkeypatch.setattr(final,'STATE',path)
    with pytest.raises(RuntimeError,match='FINAL_EXECUTION_LOCKED'):
        final.manual_gate(2050,'Base')


def test_unknown_case_and_bulk_execution_are_rejected():
    with pytest.raises(ValueError,match='FINAL_UNKNOWN_SCENARIO'):
        final.manual_gate(2050,'Invalid')
    with pytest.raises(SystemExit):
        execution.main(['prepare','--variant','final_v1'])

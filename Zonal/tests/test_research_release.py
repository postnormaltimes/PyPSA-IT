"""Solver-free public data routing and archive-boundary contracts."""
import json
from pathlib import Path
import pytest
import yaml
from mem_model.common import ROOT, sha256_file
from mem_model.reporting import publication

def test_final_release_six_case_selection():
    cfg=publication.configuration(ROOT/'config/publication_research_release.yaml')
    assert cfg['baselines'][2040]['variant']=='final_v3'
    assert cfg['baselines'][2050]['variant']=='final_v6'
    assert cfg['scenarios']==['Slow','Base','High']
    assert len(cfg['figures'])==11
    assert cfg['historical_versions'][2040][1]['status']=='PREPARED_NOT_EXECUTED'
    assert 'result_root' not in cfg['historical_versions'][2040][1]

def test_solved_networks_are_receipt_identities_not_data_dependencies():
    cfg=publication.configuration(ROOT/'config/publication_research_release.yaml')
    sources=publication._sources(cfg)
    contract=sources.contract
    assert contract['solved_binaries_required_for_selected_figures'] is False
    assert len(contract['solved_network_identities'])==30
    for path,item in contract['solved_network_identities'].items():
        assert path.startswith('data/research_results/reports/')
        assert item['distributed'] is False
        assert len(item['sha256'])==64 and item['bytes']>0

def test_public_results_identity_pin_fails_closed(tmp_path):
    cfg=publication.configuration(ROOT/'config/publication_research_release.yaml')
    cfg['results_authority']['sha256']='0'*64
    with pytest.raises(ValueError,match='AUTHORITY_HASH_MISMATCH'):publication._sources(cfg)

def test_environment_and_solver_contracts_are_preserved():
    for name in ('v3','v4'):
        cfg=yaml.safe_load((ROOT/f'config/final_methodology_execution_{name}.yaml').read_text())
        assert str(cfg['solver']['required_version'])=='13.0.3'
        assert cfg['solver']['options']=={'Threads':1,'Seed':0}
    for name in ('v5','v6'):
        cfg=yaml.safe_load((ROOT/f'config/final_methodology_execution_{name}.yaml').read_text())
        assert cfg['all_scenarios_manually_executable'] is True
        assert cfg['no_automatic_execution'] is True

def test_bootstrap_is_standard_library_only():
    import ast
    tree=ast.parse((ROOT/'scripts/bootstrap_data.py').read_text())
    imports=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):imports.extend(n.name.split('.')[0] for n in node.names)
        if isinstance(node,ast.ImportFrom):imports.append(node.module.split('.')[0])
    assert not {'pypsa','linopy','gurobipy','pandas','numpy'} & set(imports)

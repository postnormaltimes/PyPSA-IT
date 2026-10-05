"""Reporting boundaries, using isolated tables and receipts; no solves."""
from __future__ import annotations

import builtins
import json
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

import pandas as pd
import pytest
import pypsa
import xarray as xr

from mem_model import stage_b_uc2 as uc2
from mem_model.common import ZONES, sha256_file
from mem_model.reporting import canonical_results as canonical
from mem_model.reporting import marginal_regime as regime
from mem_model.reporting import uc2_diagnostics as diagnostics
from mem_model.reporting import uc2_postprocess as post
from mem_model.reporting.portable_paths import receipt_path, verify_pins
from mem_model.reporting.uc2_core_comparison import build_core_comparisons


@pytest.fixture(autouse=True)
def no_optimization():
    with post.no_solver_calls() as counts:
        yield
    assert counts['solver_invocations'] == 0


@pytest.fixture
def tmp_path(tmp_path_factory):
    # Keep Windows fixture paths short and enable its native long-path syntax.
    path = tmp_path_factory.mktemp('r')
    return Path('\\\\?\\' + str(path)) if os.name == 'nt' else path


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def test_historical_paths_are_mapped_without_changing_receipt_provenance(tmp_path):
    root = tmp_path/'relocated'
    source = root/'results'/'accepted.csv'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'accepted numerical bytes\n')
    pins = {'Z:\\frozen\\project\\results\\accepted.csv': sha256_file(source)}
    original = json.dumps(pins)
    mapping = {'Z:/frozen/project': '.'}
    verify_pins(pins, mapping, root=root)
    assert receipt_path(next(iter(pins)), mapping, root=root) == source
    assert json.dumps(pins) == original
    source.write_bytes(b'changed')
    with pytest.raises(RuntimeError, match='HASH_CONFLICT'):
        verify_pins(pins, mapping, root=root)


def test_path_map_has_no_original_tree_fallback_and_rejects_escape(tmp_path):
    root = tmp_path/'candidate'
    old = tmp_path/'old'/'accepted.json'
    old.parent.mkdir()
    old.write_text('{}')
    with pytest.raises(RuntimeError, match='ROOT_MAP_REQUIRED'):
        receipt_path(old, root=root)
    with pytest.raises(RuntimeError, match='PATH_MAP_ESCAPE'):
        receipt_path('Z:/old/../outside', {'Z:/old': '.'}, root=root)
    assert receipt_path('CANONICAL/data.csv', root=root) == root/'CANONICAL/data.csv'


def test_core_code_pins_do_not_depend_on_optional_vis(monkeypatch):
    original = post.sha256_file
    def no_optional_pin(path):
        assert 'visualization' not in Path(path).parts
        assert Path(path).name != 'vis_x1.yaml'
        return original(path)
    monkeypatch.setattr(post, 'sha256_file', no_optional_pin)
    assert post._code_hashes()
    assert not any('visualization' in name or name.endswith('vis_x1.yaml')
                   for name in regime._code_pins())


@pytest.mark.parametrize('flags,expected', [([], 'final_v1'),
    (['--variant', 'final_v1'], 'final_v1'), (['--variant=final_v1'], 'final_v1'),
    (['--variant', 'legacy'], None)])
def test_single_case_cli_defaults_to_final_and_legacy_is_explicit(monkeypatch, capsys, flags, expected):
    calls = []
    monkeypatch.setattr(uc2, 'report', lambda y, s: calls.append((uc2.ACTIVE_VARIANT.get(), y, s)) or {'status':'PASS'})
    monkeypatch.setattr(uc2, '_manual_solve', lambda *a, **k: pytest.fail('report dispatched a solve'))
    uc2.main(['report', '--year', '2040', '--scenario', 'Base', *flags])
    assert calls == [(expected, 2040, 'Base')]
    assert json.loads(capsys.readouterr().out)['status'] == 'PASS'
    assert uc2.ACTIVE_VARIANT.get() is None


def test_presentation_is_an_explicit_postsolve_cli_option(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(uc2, 'report', lambda y, s, **kw: calls.append(kw) or {'status':'PASS'})
    uc2.main(['report','--year','2040','--scenario','Base','--presentation'])
    assert calls == [{'presentation':True}]
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        uc2.main(['preflight','--year','2040','--scenario','Base','--presentation'])
    assert exc.value.code == 2


def test_final_report_does_not_discover_legacy_marginal_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(uc2, 'ROOT', tmp_path)
    monkeypatch.setattr(regime, '_report_dir', lambda y, s: uc2.case_root(y,s)/'REPORTING')
    old = tmp_path/'results'/'uc2_full_year'/'2040'/'Base'/'REPORTING'/regime.config()['root_input']
    old.mkdir(parents=True)
    for path in regime._input_paths(2040,'Base',old):
        path.write_bytes(b'historical evidence must not be used for final_v1')
    monkeypatch.setattr(regime, 'cached_report', lambda *a, **k: {'status':'PASS'})
    monkeypatch.setattr(uc2, '_report_verified', lambda *a, **k: pytest.fail('cached report reloaded networks'))
    monkeypatch.setattr(pypsa, 'Network', lambda *a, **k: pytest.fail('marginal reporting opened a network'))
    monkeypatch.setattr(xr, 'open_dataset', lambda *a, **k: pytest.fail('marginal reporting opened a dataset'))
    with uc2.execution_variant('final_v1'):
        result = uc2.report(2040,'Base')
    assert result['marginal_regime']['status'] == 'WAITING_FOR_VALIDATED_MARGINAL_ROOTS'
    assert result['marginal_regime']['network_opens'] == 0
    assert result['marginal_regime_comparison']['status'] == 'WAITING_FOR_ALL_SCENARIO_REGIMES'
    assert not (tmp_path/'results'/'final_methodology_v1').exists()


def test_immutable_historical_report_cache_is_not_repurposed(monkeypatch, tmp_path):
    report = tmp_path/'REPORTING'
    report.mkdir()
    historical = report/regime.CACHE
    historical.write_bytes(b'historical receipt bytes are immutable')
    original = historical.read_bytes()
    monkeypatch.setattr(regime, '_report_dir', lambda *a: report)
    assert regime.cached_report(2040,'Base') is None
    assert historical.read_bytes() == original


def test_final_marginal_roots_reject_legacy_job_lineage(monkeypatch, tmp_path):
    folder, report = tmp_path/'roots', tmp_path/'REPORTING'
    folder.mkdir(); report.mkdir()
    paths = regime._input_paths(2040,'Base',folder)
    for path in (paths[0],paths[1],paths[3]):
        path.write_bytes(b'fixture')
    price = report/'fixed_commitment_prices_hourly.csv'
    price.write_text('snapshot,NORD\n2019-01-01,0\n')
    qa = dict(status='PASS_FROZEN_METHOD_ATTRIBUTION', methodology_frozen=True,
        solver_invocations=0, frozen_methodology_sha256=sha256_file(paths[3]),
        artifact_hashes={p.name:sha256_file(p) for p in paths[:2]},
        source_hashes={str(price):sha256_file(price)},
        job_id='UC2_2040_BASE_8760H_FIXED_COMMITMENT_PRICE_LP',fixed_commitment_milp_sha256='legacy UC')
    _json(paths[2],qa)
    final_receipt = tmp_path/'FINAL_PRICE_RECEIPT.json'
    _json(final_receipt,{'fixed_commitment_milp_sha256':'final UC'})
    monkeypatch.setattr(regime,'ROOT',tmp_path)
    monkeypatch.setattr(uc2,'job_paths',lambda *a:{'receipt':final_receipt})
    with uc2.execution_variant('final_v1'), pytest.raises(RuntimeError,match='FINAL_LINEAGE_CONFLICT'):
        regime._validate_inputs(2040,'Base',paths,report)
    assert regime.resolve.__module__ == 'mem_model.reporting.marginal_setter_lexicographic'


@pytest.fixture
def core_report_fixture(tmp_path, monkeypatch):
    report = tmp_path/'REPORTING'
    stats = report/'CANONICAL'/'statistics'
    stats.mkdir(parents=True)
    times = pd.date_range('2019-01-01',periods=2,freq='h',name='snapshot')
    prices = pd.DataFrame({'NORD':[10.,20.]},index=times)
    dispatch = pd.DataFrame({'physical':[1.,2.]},index=times)
    stores = pd.DataFrame({'water':[3.,4.]},index=times)
    flows = pd.DataFrame({'interface':[-1.,2.]},index=times)
    network = SimpleNamespace(snapshots=times,stores_t=SimpleNamespace(e=stores),
        links_t=SimpleNamespace(p0=flows),objective=100.,meta={'network_v2b_applied':True})
    dispatch.to_parquet(stats/'dispatch_8760_by_zone.parquet')
    stores.to_parquet(report/'storage_hydro_soc_hourly.parquet')
    flows.to_parquet(report/'link_bus0_flows_hourly.parquet')
    prices.to_csv(report/'fixed_commitment_prices_hourly.csv')
    pd.DataFrame({'zone':['NORD'],'load_weighted_mean_EUR_per_MWh':[15.]}).to_csv(stats/'zonal_price_statistics.csv',index=False)
    _json(report/'economic_comparison.json',{'uc_milp_objective_EUR':100.})
    paths = {}
    for kind in uc2.RUN_TYPES:
        directory = tmp_path/kind
        directory.mkdir()
        paths[kind] = {name:directory/name for name in ('input','solved','receipt','verification')}
        for name in ('input','solved','verification'):
            paths[kind][name].write_bytes(b'isolated fixture, not a network')
        solve = dict(job_id=kind,solved_sha256=sha256_file(paths[kind]['solved']),objective_EUR=100.)
        if kind=='FIXED_COMMITMENT_PRICE_LP':
            solve['fixed_commitment_milp_sha256']=sha256_file(paths['UC_MILP']['solved'])
        _json(paths[kind]['receipt'],solve)
    original = dict(status='PASS',year=2040,scenario='Base',reporting_solver_invocations=0,
        **{key:sha256_file(paths[kind]['solved']) for kind,key in zip(uc2.RUN_TYPES,
            ('reference_verification','uc_verification','price_verification'))})
    _json(report/'MEM_UC2_REPORTING_RECEIPT.json',original)
    (report/'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json').write_bytes(b'immutable historical R1 receipt')
    monkeypatch.setattr(uc2,'ROOT',tmp_path)
    monkeypatch.setattr(uc2,'job_paths',lambda y,s,k:paths[k])
    monkeypatch.setattr(uc2,'_load_solved',lambda *a:pytest.fail('core extension loaded a fixture network'))
    monkeypatch.setattr(post,'_code_hashes',lambda **kw:{'core fixture':'stable'})
    monkeypatch.setattr(post,'read_lp_prices',lambda *a:prices)
    monkeypatch.setattr(canonical,'dispatch_8760_by_zone',lambda n:dispatch)
    tables = {'diagnostics_reconciliation':pd.DataFrame({'check':['fixture'],'passed':[True],'detail':['accepted source']}),
        'price_annual_summary':pd.DataFrame({'market':['NORD'],'load_weighted_mean_EUR_per_MWh':[15.]})}
    monkeypatch.setattr(diagnostics,'build_uc2_diagnostics',lambda *a:{k:v.copy() for k,v in tables.items()})
    return report,network


def test_core_extension_preserves_analytical_sources_and_never_imports_vis(core_report_fixture,monkeypatch):
    report, network = core_report_fixture
    protected = {p:sha256_file(p) for p in report.rglob('*') if p.is_file()}
    original_import = builtins.__import__
    def prohibit_vis(name,*a,**kw):
        assert 'visualization' not in name
        return original_import(name,*a,**kw)
    monkeypatch.setattr(builtins,'__import__',prohibit_vis)
    result = post.extend_uc2_report(2040,'Base',report,uc_network=network)
    assert result['status']=='PASS' and result['final_state']=='UC2_CORE_REPORTING_PASS'
    assert result['visualization']['status']=='NOT_REQUESTED'
    assert result['reporting_solver_invocations']==0
    assert result['diagnostics_checks_passed']==7
    assert all(sha256_file(p)==h for p,h in protected.items())
    assert (report/'MEM_UC2_CORE_REPORTING_RECEIPT.json').is_file()
    assert not (report/'VIS_X1').exists()
    monkeypatch.setattr(diagnostics,'build_uc2_diagnostics',lambda *a:pytest.fail('stable core cache regenerated tables'))
    assert post.extend_uc2_report(2040,'Base',report)==result


def test_requested_presentation_routes_to_the_retained_adapter(core_report_fixture,monkeypatch):
    report, network = core_report_fixture
    historical = (report/'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json').read_bytes()
    adapter = ModuleType('mem_model.visualization.uc2_vis_x1')
    calls = []
    adapter.render_uc2_vis=lambda *a:calls.append(a) or {'status':'PASS'}
    monkeypatch.setitem(sys.modules,adapter.__name__,adapter)
    result = post.extend_uc2_report(2040,'Base',report,uc_network=network,presentation=True)
    assert len(calls)==1 and calls[0][0] is network
    assert result['presentation_requested'] is True
    assert (report/'MEM_UC2_PRESENTATION_RECEIPT.json').is_file()
    assert (report/'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json').read_bytes()==historical


def _comparison_case(report, scenario):
    stats=report/'CANONICAL'/'statistics';stats.mkdir(parents=True)
    annual=pd.DataFrame({'market':ZONES,'load_weighted_mean_EUR_per_MWh':[15.]*7})
    for name in ('median','p5','p25','p75','p95','min','max','std'):
        annual[f'{name}_EUR_per_MWh']=15.
    annual.to_csv(stats/'uc2_price_annual_summary.csv',index=False)
    pd.DataFrame({'market':ZONES,'month':['2019-01']*7,'arithmetic_mean_EUR_per_MWh':[15.]*7}).to_csv(stats/'uc2_price_monthly_zonal_means.csv',index=False)
    pd.DataFrame({'month':['2019-01'],'load_weighted_mean_EUR_per_MWh':[15.]}).to_csv(stats/'uc2_price_monthly_national_load_weighted.csv',index=False)
    pd.DataFrame({z:[10.,20.] for z in ZONES},index=pd.date_range('2019-01-01',periods=2,freq='h')).to_csv(report/'fixed_commitment_prices_hourly.csv')
    pd.DataFrame({'spread_EUR_per_MWh':[6.,12.],'distinct_zonal_prices':[3,7]}).to_parquet(stats/'uc2_price_hourly_all_italy_spread.parquet')
    pd.DataFrame({'display_technology':['Solar'],'annual_primary_generation_TWh':[1.]}).to_csv(stats/'annual_primary_generation_national.csv',index=False)
    pd.DataFrame({'carrier':['wind_onshore'],'curtailed_MWh':[30000.]}).to_csv(report/'vre_curtailment.csv',index=False)
    pd.DataFrame({'bess_charging_TWh':[1.],'bess_discharge_TWh':[.8],'phs_charging_TWh':[2.],'phs_discharge_TWh':[1.5]}).to_csv(stats/'annual_electrical_balance_by_zone.csv',index=False)
    pd.DataFrame({'zone':['NORD'],'consumption_MWh':[1e6]}).to_csv(report/'p2x_annual_by_zone.csv',index=False)
    pd.DataFrame({'zone':['NORD'],'net_import_energy_MWh':[-2e6]}).to_csv(stats/'uc2_canonical_net_imports_summary.csv',index=False)
    pd.DataFrame({'interface_id':['A_B'],'signed_net_energy_MWh':[-3.]}).to_csv(stats/'uc2_net_interface_summary.csv',index=False)
    pd.DataFrame({'carrier':['methane_ccgt'],'actual_starts':[2],'actual_shutdowns':[1]}).to_csv(report/'commitment_events_by_zone_technology.csv',index=False)
    pd.DataFrame({'committed_MW':[100.,200.]}).to_csv(report/'hourly_online_units_and_committed_MW.csv',index=False)
    _json(report/'economic_comparison.json',{'uc_uplift_EUR':1e6,'uc_milp_objective_EUR':1e9})
    _json(report/'MEM_UC2_CORE_REPORTING_RECEIPT.json',dict(status='PASS',model_generation='UC2',year=2040,
        scenario=scenario,physical_source='UC_MILP',price_source='FIXED_COMMITMENT_PRICE_LP',reporting_solver_invocations=0,
        protected_source_hashes={},artifact_hashes={p.relative_to(report).as_posix():sha256_file(p) for p in report.rglob('*') if p.is_file()}))


def test_core_comparison_retains_prices_signed_flows_and_objective_without_vis(tmp_path,monkeypatch):
    reports={s:tmp_path/s/'REPORTING' for s in ('Slow','Base')}
    for s,p in reports.items():_comparison_case(p,s)
    monkeypatch.setattr(pypsa,'Network',lambda *a,**k:pytest.fail('comparison opened a network'))
    output=tmp_path/'COMPARISONS_CORE'
    result=build_core_comparisons(2040,reports,output)
    assert result['table_count']==10 and result['figure_count']==0 and result['network_opens']==0
    metrics=pd.read_csv(output/'data/comparison_metrics.csv')
    assert metrics.loc[metrics.metric.eq('net_imports'),'value'].tolist()==[-2.,-2.]
    assert metrics.loc[metrics.metric.eq('canonical_uc_system_objective'),'value'].tolist()==[1.,1.]
    prices=metrics.loc[metrics.metric.eq('annual_load_weighted_zonal_price')]
    assert prices.value.eq(15.).all() and prices.source_authority.eq('FIXED_COMMITMENT_PRICE_LP_DUALS').all()
    durations=pd.read_csv(output/'data/price_duration.csv')
    assert durations.loc[(durations.scenario=='Base')&(durations.market=='NORD'),'price_EUR_per_MWh'].tolist()==[20.,10.]
    assert not (output/'figures').exists()


def test_optional_external_paths_have_no_developer_default(monkeypatch,tmp_path):
    from mem_model.visualization.vis_x1 import _external_path
    monkeypatch.delenv('MEM_VIS_X1_HANDOFF',raising=False)
    with pytest.raises(RuntimeError,match='OPTIONAL_DEPENDENCY_NOT_CONFIGURED'):
        _external_path({'external_handoff':None},'external_handoff','MEM_VIS_X1_HANDOFF')
    monkeypatch.setenv('MEM_VIS_X1_HANDOFF',str(tmp_path))
    assert _external_path({'external_handoff':None},'external_handoff','MEM_VIS_X1_HANDOFF')==tmp_path


def test_core_final_qa_needs_verified_jobs_and_reporting_pass_without_vis(monkeypatch,tmp_path):
    _json(tmp_path/'REPORTING'/'MEM_UC2_REPORTING_RECEIPT.json',{'status':'PASS','year':2040,'scenario':'Base'})
    calls=[]
    monkeypatch.setattr(uc2,'_verified_previous',lambda y,s,k:calls.append(k) or {'status':'PASS'})
    monkeypatch.setattr(uc2,'case_root',lambda *a:tmp_path)
    result=uc2.final_qa(2040,'Base')
    assert calls==list(uc2.RUN_TYPES) and result['status']=='PASS'
    assert not (tmp_path/'REPORTING'/'VIS_X1').exists()


def test_2050_manual_authorization_remains_false():
    from mem_model.final_methodology_networks import settings
    assert all(value is False for key,value in settings()['manual_authorization'].items() if key.startswith('2050_'))

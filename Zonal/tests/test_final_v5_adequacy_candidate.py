"""Candidate additions and execution isolation; model construction is forbidden."""
import json

import numpy as np
import pandas as pd
import pypsa
import pytest

from mem_model import final_adequacy_candidate as v5
from mem_model import final_methodology_networks as final
from mem_model import stage_b_uc2 as uc2
from mem_model.common import ROOT, sha256_file
from mem_model.stage_b_zonal_vre import no_models_or_solves


@pytest.fixture(autouse=True)
def no_solve(monkeypatch):
    def prohibited(*args, **kwargs):
        pytest.fail('model, solver or production action called')
    monkeypatch.setattr(uc2, '_manual_solve', prohibited)
    monkeypatch.setattr(uc2, '_solver_contract', prohibited)
    with no_models_or_solves():
        yield


@pytest.fixture
def fixture_network():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2019-01-01', periods=3, freq='h'))
    for zone, power in [('NORD', 200), ('CNOR', 300)]:
        n.add('Bus', zone)
        for fuel in v5.FUELS:
            n.add('Carrier', fuel)
            name = f'2050_BASE_{zone}_{fuel.upper()}_NON_CHP'
            n.add('Generator', name, bus=zone, carrier=fuel, p_nom=power, efficiency=.43,
                  p_max_pu=pd.Series(.8, index=n.snapshots), marginal_cost=100,
                  committable=fuel == v5.FUELS[0], p_min_pu=.2 if fuel == v5.FUELS[0] else 0,
                  start_up_cost=24*power if fuel == v5.FUELS[0] else 0)
    n.meta = {'model_version': 'FINAL_METHODOLOGY_V4', 'unchanged_record': 'fixture'}
    return n


def tiny_plan(n):
    name = n.generators.index[0]
    return pd.DataFrame([{'child_id': 'UC2A__2050__Base__ADDITIONAL_NON_CHP', 'template_id': name,
                         'p_nom': 350., 'efficiency': .43, 'marginal_cost': 100., 'committable': True}])


def test_installed_and_nominal_derating_applied_once(fixture_network):
    frame = v5.nominal_capacity(fixture_network)
    assert frame.p_nom.sum() == 1000
    assert frame.derated_MW.sum() == 800
    cfg = v5.settings()
    plan, baseline = v5.increment_contract(fixture_network, cfg)
    assert plan.installed_increment_MW.sum() == pytest.approx((30000-800)/.8)
    assert plan.derated_increment_MW.sum() + baseline.derated_MW.sum() == pytest.approx(30000)
    assert plan.groupby('zone').installed_increment_MW.sum().to_dict() == pytest.approx({'CNOR': 21900, 'NORD': 14600})


def test_proportional_existing_ocgt_geography(fixture_network):
    plan, _ = v5.increment_contract(fixture_network, v5.settings(v5.VARIANTS[1]))
    shares = plan.groupby('zone').installed_increment_MW.sum() / plan.installed_increment_MW.sum()
    assert shares.to_dict() == pytest.approx({'CNOR': .6, 'NORD': .4})
    for _, block in plan.groupby('zone'):
        assert block.installed_increment_MW.iloc[0] == block.installed_increment_MW.iloc[1]


def test_variable_availability_not_silently_averaged(fixture_network):
    fixture_network.generators_t.p_max_pu.iloc[1, 0] = .5
    with pytest.raises(RuntimeError, match='DERATING_NOT_CONSTANT'):
        v5.nominal_capacity(fixture_network)


def test_sparse_successor_preserves_every_original_object(fixture_network):
    n = fixture_network
    plan = tiny_plan(n)
    out = v5.transform(n, plan, v5.settings(), 'Base', 'fixture.nc', 'fixture-hash', 'UC_INPUT')
    assert v5.exact_diff(n, out, plan, v5.settings(), 'Base', 'UC_INPUT')
    assert out.generators.at[plan.child_id[0], 'start_up_cost'] == 8400
    assert n.generators.index.size == 4
    assert 'adequacy_candidate' not in n.meta


@pytest.mark.parametrize('defect', ['old_capacity', 'availability', 'bus', 'extra_unit', 'metadata', 'new_cost'])
def test_whitelist_rejects_drift(fixture_network, defect):
    n = fixture_network
    plan = tiny_plan(n)
    out = v5.transform(n, plan, v5.settings(), 'Base', 'fixture.nc', 'fixture-hash', 'UC_INPUT')
    if defect == 'old_capacity':
        out.generators.iloc[0, out.generators.columns.get_loc('p_nom')] += 1
    elif defect == 'availability':
        out.generators_t.p_max_pu.iloc[0, 0] -= .01
    elif defect == 'bus':
        out.buses.loc['NORD', 'v_nom'] += 1
    elif defect == 'extra_unit':
        out.generators.loc['unexpected'] = out.generators.iloc[0]
    elif defect == 'metadata':
        out.meta['unapproved_field'] = True
    else:
        out.generators.at[plan.child_id[0], 'marginal_cost'] += 1
    with pytest.raises((AssertionError, RuntimeError)):
        v5.exact_diff(n, out, plan, v5.settings(), 'Base', 'UC_INPUT')


@pytest.mark.parametrize('variant', v5.VARIANTS)
def test_candidate_config_inherits_accepted_solver_and_water_contract(variant):
    cfg = final.settings(variant)
    for key in ('solver', 'water_values', 'water_mapping'):
        assert cfg[key] == final.settings('final_v4')[key]
    assert cfg['no_automatic_execution'] is True
    assert cfg['all_scenarios_manually_executable'] is True
    assert cfg['canonical_promotion'] == 'NEVER_AUTOMATIC'


@pytest.mark.parametrize('variant', v5.VARIANTS)
def test_2040_cannot_be_relabelled_as_candidate(variant):
    with uc2.execution_variant(variant), pytest.raises(ValueError, match='2050_ONLY'):
        uc2.case_root(2040, 'Base')


@pytest.mark.parametrize('variant', v5.VARIANTS)
@pytest.mark.parametrize('scenario', v5.SCENARIOS)
def test_completed_candidate_input_and_job_isolation(variant, scenario):
    cfg = v5.settings(variant)
    with uc2.execution_variant(variant):
        paths = [uc2.job_paths(2050, scenario, kind) for kind in uc2.RUN_TYPES]
        assert len({p['solved'] for p in paths}) == 3
        for p in paths:
            assert p['directory'].is_relative_to(ROOT / cfg['result_root'] / '2050' / scenario)
            assert not p['directory'].is_relative_to(ROOT / 'results/final_methodology_v4')
        for kind in uc2.RUN_TYPES:
            assert uc2._check_input_hash(2050, scenario, kind) == uc2._expected_hash(2050, scenario, kind)
    if scenario == 'Slow':
        baseline = final.package(2050, scenario, 'final_v4')
        candidate = final.package(2050, scenario, variant)
        assert candidate == baseline


@pytest.mark.parametrize('variant', v5.VARIANTS)
@pytest.mark.parametrize('scenario', v5.SCENARIOS)
def test_only_one_selected_action_is_dispatched(variant, scenario, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(uc2, 'verify_reference', lambda y,s: calls.append((y,s,uc2.ACTIVE_VARIANT.get())) or {'status':'PASS'})
    uc2.main(['verify-reference','--year','2050','--scenario',scenario,'--variant',variant])
    assert calls == [(2050, scenario, variant)]
    assert uc2.ACTIVE_VARIANT.get() is None
    assert json.loads(capsys.readouterr().out)['status'] == 'PASS'


@pytest.mark.parametrize('variant', v5.VARIANTS)
def test_reporting_version_is_not_predecessor_or_other_control(variant):
    from mem_model.reporting.uc2_postprocess import _final_model_version
    with uc2.execution_variant(variant):
        assert _final_model_version(2050) == v5.settings(variant)['model_version']


@pytest.mark.parametrize('variant', v5.VARIANTS)
def test_missing_candidate_cases_are_not_baseline_results(variant, tmp_path, monkeypatch):
    from mem_model.reporting import adequacy_comparison as comparison
    monkeypatch.setattr(comparison, 'ROOT', tmp_path)
    cfg = v5.settings(variant)
    monkeypatch.setattr(comparison, 'settings', lambda selected: {**cfg, 'result_root': 'r'})
    result = comparison.refresh(variant)
    assert result['status'] == 'WAITING_FOR_COMPLETED_CASES'
    assert len(result['coverage']) == 3
    assert all(row['missing_variants'] == 'final_v4;' + variant for row in result['coverage'])
    assert not list(tmp_path.rglob('METRICS.csv'))


def test_shortage_hours_zone_hours_and_price_authorities_are_distinct():
    from mem_model.common import ZONES
    from mem_model.reporting.adequacy_comparison import hourly_metrics
    dates = pd.date_range('2019-01-01', periods=8760, freq='h', name='snapshot')
    prices = pd.DataFrame(50., index=dates, columns=ZONES)
    prices.loc[dates[4], 'CNOR'] = 15000
    shed = pd.DataFrame(0., index=dates, columns=ZONES)
    shed.loc[dates[0], ['NORD','CNOR']] = [100, 200]
    shed.loc[dates[1], 'NORD'] = 400
    dispatch = shed.stack().rename('load_shedding_MW').rename_axis(['snapshot','zone']).reset_index()
    dispatch['gross_end_use_demand_MW'] = 1000.
    weights = pd.Series(1., index=dates)
    metrics, event = hourly_metrics(dispatch, prices, prices.mean(axis=1), weights)
    assert metrics['unserved_energy'][0] == .7
    assert metrics['unique_shortage_hours'][0] == 2
    assert metrics['zonal_shedding_hours'][0] == 3
    assert metrics['maximum_national_simultaneous_shortage'][0] == 400
    assert metrics['any_zone_at_VOLL_hours'][0] == 1
    assert metrics['at_VOLL_zone_hours'][0] == 1
    assert metrics['lost_load_penalty'][0] == 10500000
    assert len(event) == 48 * 7
    with pytest.raises(ValueError, match='CHRONOLOGY'):
        hourly_metrics(dispatch, prices.iloc[1:], prices.mean(axis=1), weights)


def test_saved_script_selects_one_case_and_stops_on_failure():
    text = (ROOT / 'scripts/run_final_v5.ps1').read_text()
    assert 'foreach ($action' in text and 'foreach ($Scenario' not in text
    assert '$stageExitCode = $LASTEXITCODE' in text
    assert '$stageExitCode -ne 0' in text and 'throw "Stopped at' in text
    assert '_local\\logs' in text
    assert 'gurobi' not in text.lower()


def test_added_unit_cost_capture_uses_UC_not_price_dispatch(fixture_network):
    from mem_model.reporting.adequacy_comparison import additional_ocgt_tables
    n = fixture_network
    plan = pd.DataFrame({'child_id': n.generators.index[:1]})
    name = plan.child_id.iloc[0]
    n.generators_t.p = pd.DataFrame({name:[0.,50.,60.]}, index=n.snapshots)
    n.generators_t.status = pd.DataFrame({name:[0.,1.,1.]}, index=n.snapshots)
    n.generators_t.start_up = pd.DataFrame({name:[0.,1.,0.]}, index=n.snapshots)
    n.generators_t.shut_down = pd.DataFrame({name:[0.,0.,0.]}, index=n.snapshots)
    annual, hourly = additional_ocgt_tables(n, plan)
    assert annual.generation_MWh.iloc[0] == 110
    assert annual.operating_cost_EUR.iloc[0] == 11000
    assert annual.start_up_EUR.iloc[0] == 4800
    assert annual.dispatch_objective_contribution_EUR.iloc[0] == 15800
    assert annual.fixed_investment_cost.iloc[0] == 'NOT_CALCULATED'
    pd.testing.assert_frame_equal(hourly, n.generators_t.p)


def test_complete_matched_collection_and_wrong_version_rejection(tmp_path_factory, monkeypatch):
    from mem_model.common import ZONES
    from mem_model.reporting import adequacy_comparison as comparison
    from mem_model.visualization import uc2_comparison as visual
    tmp_path = tmp_path_factory.mktemp('c')
    monkeypatch.setattr(comparison, 'ROOT', tmp_path)
    cfg = v5.settings()
    monkeypatch.setattr(comparison, 'settings', lambda selected: {**cfg, 'result_root':'c'})
    monkeypatch.setattr(visual, '_final_case_duration_source', lambda *args: ('validated_fixture', 'fixture_hash'))
    dates = pd.date_range('2019-01-01',periods=8760,freq='h',name='snapshot')
    dispatch = pd.MultiIndex.from_product([dates,ZONES],names=['snapshot','zone']).to_frame(index=False)
    dispatch['load_shedding_MW'] = 0.
    dispatch['gross_end_use_demand_MW'] = 1000.
    candidate_report = None
    for scenario in v5.SCENARIOS:
        for variant, root, version in [('final_v4','results/final_methodology_v4','FINAL_METHODOLOGY_V4'),
                                       ('final_v5','c','FINAL_METHODOLOGY_V5')]:
            report = tmp_path / root / '2050' / scenario / 'REPORTING'
            report.mkdir(parents=True)
            qa = report.parent / 'QA'
            qa.mkdir()
            (qa/'FINAL_SCENARIO_QA.json').write_text(json.dumps({'status':'PASS','year':2050,'scenario':scenario}))
            (qa/'VERIFY_UC.json').write_text(json.dumps({'operational_residuals':{'objective_terms':{'generators_operating_EUR':100}}}))
            files = {}
            def save(name, frame, parquet=False):
                path = report / name
                path.parent.mkdir(parents=True,exist_ok=True)
                frame.to_parquet(path) if parquet else frame.to_csv(path,index=False)
                files[name] = sha256_file(path)
            save('CANONICAL/statistics/dispatch_8760_by_zone.parquet',dispatch,True)
            prices = pd.DataFrame(50.,index=dates,columns=ZONES).reset_index()
            save('fixed_commitment_prices_hourly.csv',prices)
            save('CANONICAL/statistics/uc2_price_hourly_all_italy_spread.parquet',
                 pd.DataFrame({'load_weighted_national_EUR_per_MWh':50.},index=dates),True)
            for filename in comparison.DATASETS.values():
                save(filename,pd.DataFrame({'metric':['known_fixture'],'value':[1.]}))
            for filename in ('storage_hydro_soc_hourly.parquet','NET_FLOW_A_TO_B_MW.parquet',
                             'CANONICAL/statistics/uc2_net_interface_positive_utilization_hourly.parquet',
                             'CANONICAL/statistics/uc2_net_interface_negative_utilization_hourly.parquet'):
                save(filename,pd.DataFrame({'asset':[0.]},index=dates[:1]),True)
            if variant=='final_v5':
                save('candidate_additional_ocgt_annual.csv',pd.DataFrame({'generation_MWh':[0.]}))
                candidate_report = report
            economic = report / 'economic_comparison.json'
            economic.write_text(json.dumps({'uc_milp_objective_EUR':100.,'uc_uplift_percent':1.}))
            files[economic.name] = sha256_file(economic)
            authority = pd.DataFrame([{'parent_model_version':version,'current_role':role,'year':2050,'scenario':scenario,
                'solved_or_unsolved':'SOLVED','network_path':name,'network_sha256':name+'-hash'}
                for role,name in [('CANONICAL_PHYSICAL','UC_fixture'),('CANONICAL_PRICES_DUALS_ONLY','LP_fixture')]])
            save('uc2_VIS_RESULT_AUTHORITY_REGISTER.csv',authority)
            (report/'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json').write_text(json.dumps({
                'status':'PASS','year':2050,'scenario':scenario,'artifact_hashes':files,
                'protected_source_hashes':{'UC_fixture':'UC_fixture-hash','LP_fixture':'LP_fixture-hash'}}))
    result = comparison.refresh()
    assert result['status']=='COMPLETE'
    deltas = pd.read_csv(tmp_path/'c/2050/COMPARISONS_FINAL_V4_V5/PAIRED_DELTAS.csv')
    assert deltas.candidate_minus_final_v4.eq(0).all()
    receipt = json.loads((candidate_report/'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json').read_text())
    with pytest.raises(ValueError,match='VERSION_FAIL'):
        comparison._case_sources(candidate_report.parent,'High','FINAL_METHODOLOGY_V4')

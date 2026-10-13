"""Sentinel-only cost changes and isolated manual execution; no optimization."""
import inspect
import json

import numpy as np
import pandas as pd
import pypsa
import pytest

from mem_model import final_external_price_candidate as etx
from mem_model import final_methodology_networks as final
from mem_model import stage_b_uc2 as uc2
from mem_model.common import ROOT, PRICE_MARKETS
from mem_model.stage_b_zonal_vre import no_models_or_solves

MANUAL_SOLVE_SOURCE = inspect.getsource(uc2._manual_solve)


@pytest.fixture(autouse=True)
def forbid_optimization(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Optimizer or solver contract called during static validation')
    monkeypatch.setattr(uc2, '_manual_solve', forbidden)
    monkeypatch.setattr(uc2, '_solver_contract', forbidden)
    with no_models_or_solves():
        yield


@pytest.fixture
def network():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2019-01-01', periods=6, freq='h', name='snapshot'))
    n.add('Bus', 'NORD')
    n.add('Carrier', 'external_market')
    n.add('Carrier', 'load_shedding')
    n.add('Generator', 'SHEDDING_NORD', bus='NORD', carrier='load_shedding', p_nom=10000., marginal_cost=15000.)
    n.add('Load', 'RIGID_NORD', bus='NORD', p_set=100.)
    for market in PRICE_MARKETS:
        n.add('Bus', 'EXT_' + market)
        n.add('Generator', 'EXT_SUPPLY_' + market, bus='EXT_' + market,
              carrier='external_market', p_nom=1000.)
        n.add('Link', 'EXT_INTERFACE_' + market, bus0='EXT_' + market, bus1='NORD',
              p_nom=100., p_min_pu=-.4, p_max_pu=.7)
    frame = pd.DataFrame(90., index=n.snapshots, columns=['EXT_SUPPLY_' + m for m in PRICE_MARKETS])
    frame['EXT_SUPPLY_FR'] = [50., 15000., 15000. - 5e-7, 15000. + 5e-7, 15000. + 2e-6, 450.]
    frame.columns.name = 'name'
    n.generators_t.marginal_cost = frame
    n.meta = {'model_version': 'FINAL_METHODOLOGY_V5', 'adequacy_candidate': {'variant': 'final_v5'}, 'inherited': True}
    return n


def metadata():
    return etx.lineage_metadata(etx.settings(), 'High', 'UC_INPUT', 'parent.nc', 'parent-sha',
        'runtime_inputs/final_methodology_v6/2050/external_prices_hourly.parquet', 'derived-sha')


def test_absolute_tolerance_and_non_cap_semantics(network):
    before = etx.external_costs(network)
    after, mask = etx.normalize_costs(before)
    assert mask['EXT_SUPPLY_FR'].tolist() == [False, True, True, True, False, False]
    assert after['EXT_SUPPLY_FR'].tolist() == [50., 400., 400., 400., 15000. + 2e-6, 450.]
    assert after.loc[:, after.columns != 'EXT_SUPPLY_FR'].eq(90.).all().all()


def test_all_external_markets_scanned_without_timestamp_lists(network):
    for market in PRICE_MARKETS:
        network.generators_t.marginal_cost.loc[network.snapshots[0], 'EXT_SUPPLY_' + market] = 15000.
    out = etx.transform(network, metadata())
    assert etx.external_costs(out).iloc[0].eq(400.).all()
    assert out.generators.at['SHEDDING_NORD', 'marginal_cost'] == 15000.


@pytest.mark.parametrize('defect', ['nonfinite', 'missing_market', 'wrong_bus', 'wrong_carrier', 'missing_hourly_column'])
def test_incomplete_source_fails_closed(network, defect):
    if defect == 'nonfinite':
        network.generators_t.marginal_cost.iloc[0, 0] = np.nan
    elif defect == 'missing_market':
        network.remove('Generator', 'EXT_SUPPLY_MT')
    elif defect == 'wrong_bus':
        network.generators.at['EXT_SUPPLY_MT', 'bus'] = 'NORD'
    elif defect == 'wrong_carrier':
        network.generators.at['EXT_SUPPLY_MT', 'carrier'] = 'load_shedding'
    else:
        network.generators_t.marginal_cost = network.generators_t.marginal_cost.drop(columns='EXT_SUPPLY_MT')
    with pytest.raises(RuntimeError, match='ETX_'):
        etx.external_costs(network)


def test_sparse_transform_is_non_mutating_and_export_reload_safe(network, tmp_path):
    parent = network.copy()
    out = etx.transform(network, metadata())
    changes = etx.exact_diff(network, out, metadata())
    assert sum(c['component'] == 'generators_t' for c in changes) == 3
    final._equal(parent, network)
    path = tmp_path / 'input.nc'
    out.export_to_netcdf(path)
    loaded = pypsa.Network(path)
    assert etx.exact_diff(network, loaded, metadata()) == changes
    final._equal(out, loaded)


@pytest.mark.parametrize('defect', ['capacity', 'Italian_cost', 'unmatched_external_cost', 'availability',
    'link_limit', 'load', 'weight', 'extra_generator', 'metadata', 'lineage'])
def test_exact_diff_rejects_unrelated_drift(network, defect):
    out = etx.transform(network, metadata())
    if defect == 'capacity':
        out.generators.at['EXT_SUPPLY_FR', 'p_nom'] += 1.
    elif defect == 'Italian_cost':
        out.generators.at['SHEDDING_NORD', 'marginal_cost'] = 400.
    elif defect == 'unmatched_external_cost':
        out.generators_t.marginal_cost.iloc[0, 0] += 1.
    elif defect == 'availability':
        out.generators.at['EXT_SUPPLY_FR', 'p_max_pu'] = .9
    elif defect == 'link_limit':
        out.links.at['EXT_INTERFACE_FR', 'p_nom'] += 1.
    elif defect == 'load':
        out.loads.at['RIGID_NORD', 'p_set'] += 1.
    elif defect == 'weight':
        out.snapshot_weightings.iloc[0, 0] += 1.
    elif defect == 'extra_generator':
        out.generators.loc['UNEXPECTED'] = out.generators.iloc[0]
    elif defect == 'metadata':
        out.meta['unexpected'] = True
    else:
        out.meta['external_price_normalization']['parent_sha256'] = 'wrong'
    with pytest.raises((AssertionError, RuntimeError)):
        etx.exact_diff(network, out, metadata())


def test_solver_water_and_cohort_contracts_inherited():
    parent, child = final.settings('final_v5'), final.settings('final_v6')
    for field in ('solver', 'water_values', 'water_mapping'):
        assert child[field] == parent[field]
    assert child['incremental_unit_crosswalk'] == 'qa/final_methodology_v5/INCREMENTAL_UNIT_CROSSWALK.csv'
    assert child['canonical_promotion'] == 'NEVER_AUTOMATIC'
    assert child['all_scenarios_manually_executable'] is True
    assert child['no_automatic_execution'] is True


@pytest.mark.parametrize('scenario', etx.SCENARIOS)
def test_all_prepared_paths_hashes_and_pricing_input_isolation(scenario):
    with uc2.execution_variant('final_v6'):
        paths = [uc2.job_paths(2050, scenario, k) for k in uc2.RUN_TYPES]
        assert len({p['solved'] for p in paths}) == 3
        for kind, p in zip(uc2.RUN_TYPES, paths):
            assert p['input'].is_relative_to(ROOT / 'networks/unsolved/final_methodology_v6')
            assert p['directory'].is_relative_to(ROOT / 'results/final_methodology_v6/2050' / scenario)
            assert uc2._check_input_hash(2050, scenario, kind) == uc2._expected_hash(2050, scenario, kind)
            assert not p['solved'].exists() and not p['receipt'].exists()
        assert paths[1]['input'] == paths[2]['input']
        from mem_model.reporting.uc2_postprocess import _final_model_version
        assert _final_model_version(2050) == 'FINAL_METHODOLOGY_V6'


def test_2040_is_not_addressable_as_v6():
    with uc2.execution_variant('final_v6'), pytest.raises(ValueError, match='2050_ONLY'):
        uc2.case_root(2040, 'Base')


@pytest.mark.parametrize('action, function', [('verify-reference', 'verify_reference'), ('verify-uc', 'verify_uc'),
    ('verify-price', 'verify_price'), ('report', 'report'), ('final-qa', 'final_qa')])
def test_cli_dispatches_only_explicit_case(action, function, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(uc2, function, lambda y, s: calls.append((y, s, uc2.ACTIVE_VARIANT.get())) or {'status': 'PASS'})
    uc2.main([action, '--year', '2050', '--scenario', 'High', '--variant', 'final_v6'])
    assert calls == [(2050, 'High', 'final_v6')]
    assert uc2.ACTIVE_VARIANT.get() is None
    assert json.loads(capsys.readouterr().out)['status'] == 'PASS'


@pytest.mark.parametrize('action, kind', [('run-reference', 'POST_P2X_CONTINUOUS_REFERENCE'),
    ('run-uc', 'UC_MILP'), ('run-price', 'FIXED_COMMITMENT_PRICE_LP')])
def test_solve_action_routing_with_mocked_boundary_only(action, kind, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(uc2, '_manual_solve', lambda y, s, k, **kwargs:
        calls.append((y, s, k, uc2.ACTIVE_VARIANT.get())) or {'status': 'MOCK_ROUTE_ONLY'})
    uc2.main([action, '--year', '2050', '--scenario', 'High', '--variant', 'final_v6'])
    assert calls == [(2050, 'High', kind, 'final_v6')]
    assert uc2.ACTIVE_VARIANT.get() is None
    assert json.loads(capsys.readouterr().out)['status'] == 'MOCK_ROUTE_ONLY'


def test_existing_price_capture_and_overwrite_guards_remain_reachable():
    source = MANUAL_SOLVE_SOURCE
    assert 'UC2_OVERWRITE_REFUSED' in source
    assert 'capture_price_evidence(' in source and 'bind_price_evidence(' in source
    assert 'fixed_milp_sha = milp_receipt["solved_sha256"]' in source
    assert 'network = pypsa.Network(paths["input"])' in source


def test_ocgt_reporting_uses_inherited_unit_crosswalk_with_current_variant(tmp_path, monkeypatch):
    from mem_model.reporting import adequacy_comparison as reporting
    plan = pd.DataFrame({'scenario': ['High'], 'child_id': ['existing_v5_unit']})
    path = tmp_path / 'parent.csv'
    plan.to_csv(path, index=False)
    monkeypatch.setattr(final, 'settings', lambda v: dict(qa_root='unused', incremental_unit_crosswalk=str(path)))
    seen = []
    def tables(n, p):
        seen.extend(p.child_id)
        return pd.DataFrame({'generation_MWh': [1.]}), pd.DataFrame({'existing_v5_unit': [1.]})
    monkeypatch.setattr(reporting, 'additional_ocgt_tables', tables)
    reporting.capture_additional_ocgt(None, 'High', 'final_v6', tmp_path, 'UC-sha')
    record = json.loads((tmp_path / 'candidate_additional_ocgt_manifest.json').read_text())
    assert seen == ['existing_v5_unit']
    assert record['variant'] == 'final_v6' and record['UC_solved_sha256'] == 'UC-sha'
    assert record['unit_crosswalk'] == str(path)


def test_audit_and_counts_match_derived_input():
    cfg = etx.settings()
    audit = pd.read_csv(ROOT / cfg['qa_root'] / 'EXTERNAL_PRICE_INPUT_CHANGE_AUDIT.csv')
    counts = pd.read_csv(ROOT / cfg['qa_root'] / 'EXTERNAL_PRICE_COUNTS.csv')
    assert set(counts.external_market) == set(PRICE_MARKETS) and len(counts) == 24
    assert audit.revised_marginal_cost.eq(400.).all()
    assert (audit.original_marginal_cost.sub(15000.).abs() <= 1e-6).all()
    assert int(counts.replacements.sum()) == len(audit)
    source, _, original = etx.external_source()
    derived = pd.read_parquet(ROOT / cfg['runtime_root'] / '2050/external_prices_hourly.parquet')
    expected = original.copy()
    mask = np.abs(expected.price_EUR_per_MWh - 15000.) <= 1e-6
    expected.loc[mask, 'price_EUR_per_MWh'] = 400.
    pd.testing.assert_frame_equal(derived, expected, check_exact=True)

"""Saved-array integration of native capture, frozen roots, figures and gallery."""
import copy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pypsa
import pytest
import xarray as xr

from mem_model.common import sha256_file
from mem_model import stage_b_uc2 as uc2
from mem_model.reporting import marginal_root_pipeline as pipeline
from mem_model.reporting import marginal_setter_attribution as engine
from mem_model.reporting import marginal_regime as regime
from mem_model.reporting import inspection
from mem_model.reporting.price_evidence import capture_price_evidence, bind_price_evidence
from mem_model.stage_b_zonal_vre import no_models_or_solves
from test_lp_resource_capture import Variable, constraint


@pytest.fixture(autouse=True)
def no_optimization():
    with no_models_or_solves():
        yield


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture(scope='module')
def saved_case(tmp_path_factory):
    """Eight generators and one Store, not a production-size optimization model."""
    with no_models_or_solves():
        root = tmp_path_factory.mktemp('rootcase')
        case = root / '2040/Slow'
        report = case / 'REPORTING'
        report.mkdir(parents=True)
        directory = case / 'FIXED_COMMITMENT_PRICE_LP'
        directory.mkdir()
        snapshots = pd.date_range('2019-01-01', periods=8760, freq='h', name='snapshot')
        zones = ['CNOR' if zone == 'CNORD' else zone for zone in engine.ZONES]
        n = pypsa.Network()
        n.set_snapshots(snapshots)
        n.meta = dict(saved_array_validation_fixture=True)
        n.snapshot_weightings.objective = 2.
        for zone in zones:
            n.add('Bus', zone, carrier='AC')
            n.add('Generator', 'TG_' + zone, bus=zone, carrier='methane_ccgt', p_nom=10., marginal_cost=10.)
            n.add('Load', 'L_' + zone, bus=zone, p_set=4. if zone == 'NORD' else 5.)
        n.add('Generator', 'P2X_NORD', bus='NORD', carrier='p2x_flexible_NORD', sign=-1., p_nom=4.)
        n.add('Bus', 'BAT', carrier='battery_energy')
        n.add('Store', 'B', bus='BAT', carrier='battery_energy', e_nom=10., e_cyclic=True)
        n.add('Link', 'CH', bus0='NORD', bus1='BAT', carrier='battery_energy', p_nom=1., efficiency=1.)
        n.add('GlobalConstraint', 'P2X_ANNUAL_NORD', type='operational_limit', sense='==',
              constant=8760., carrier_attribute='p2x_flexible_NORD')
        n.generators_t.p = pd.DataFrame(5., index=snapshots, columns=n.generators.index)
        n.generators_t.p['P2X_NORD'] = 1.
        n.loads_t.p = pd.DataFrame(5., index=snapshots, columns=n.loads.index)
        n.loads_t.p['L_NORD'] = 4.
        n.links_t.p0 = pd.DataFrame(0., index=snapshots, columns=n.links.index)
        n.links_t.p1 = -n.links_t.p0
        n.stores_t.p = pd.DataFrame(0., index=snapshots, columns=n.stores.index)
        n.stores_t.e = pd.DataFrame(5., index=snapshots, columns=n.stores.index)
        n.buses_t.marginal_price = pd.DataFrame(10., index=snapshots, columns=n.buses.index)
        cursor = 0
        def labels(names):
            nonlocal cursor
            count = len(snapshots) * len(names)
            result = xr.DataArray(np.arange(cursor, cursor + count).reshape(len(snapshots), len(names)),
                coords={'snapshot': snapshots, 'name': list(names)}, dims=('snapshot', 'name'))
            cursor += count
            return result
        gen, link, power, energy = (labels(n.generators.index), labels(n.links.index),
                                     labels(n.stores.index), labels(n.stores.index))
        variables = {'Generator-p': Variable(gen, n.generators_t.p.to_numpy()),
                     'Link-p': Variable(link, 0.), 'Store-p': Variable(power, 0.), 'Store-e': Variable(energy, 5.)}
        def bounds(prefix, array, upper):
            return {prefix + '-lower': constraint(labels(array.coords['name'].values), array.to_numpy()[..., None], 1., sign='>='),
                    prefix + '-upper': constraint(labels(array.coords['name'].values), array.to_numpy()[..., None], 1., rhs=upper, sign='<=')}
        rows = {**bounds('Generator-fix-p', gen, [10.] * 7 + [4.]),
                **bounds('Link-fix-p', link, 1.), **bounds('Store-fix-e', energy, 10.)}
        bus_vars = np.full((8760, 8, 2), -1, dtype=int)
        coefficients = np.zeros((8760, 8, 2))
        for i in range(7):
            bus_vars[:, i, 0] = gen.to_numpy()[:, i]
            coefficients[:, i, 0] = 1.
        bus_vars[:, 0, 1] = gen.to_numpy()[:, 7]
        coefficients[:, 0, 1] = -1.
        # Include the charging terminal in a separate exact row term below.
        bus_vars = np.concatenate([bus_vars, np.full((8760, 8, 1), -1, dtype=int)], axis=-1)
        coefficients = np.concatenate([coefficients, np.zeros((8760, 8, 1))], axis=-1)
        bus_vars[:, 0, 2] = link.to_numpy()[:, 0]
        coefficients[:, 0, 2] = -1.
        bus_vars[:, 7, :2] = np.column_stack([link.to_numpy()[:, 0], power.to_numpy()[:, 0]])
        coefficients[:, 7, :2] = [1., 1.]
        rows['Bus-nodal_balance'] = constraint(labels(n.buses.index), bus_vars, coefficients,
            rhs=[[4.] + [5.] * 6 + [0.]] * 8760, dual=20.)
        rows['Store-energy_balance'] = constraint(labels(n.stores.index),
            np.stack([energy.to_numpy(), np.roll(energy.to_numpy(), 1, axis=0), power.to_numpy()], axis=-1),
            [-1., 1., -1.], dual=20.)
        rows['GlobalConstraint-P2X_ANNUAL_NORD'] = constraint(xr.DataArray(cursor),
            gen.to_numpy()[:, 7], 1., rhs=8760., dual=20.)
        objective = SimpleNamespace(is_linear=True, sense='min',
            vars=xr.DataArray(gen.to_numpy()[:, :7].ravel(), dims='_term'),
            coeffs=xr.DataArray(np.full(8760 * 7, 20.), dims='_term'))
        n._model = SimpleNamespace(status='ok', binaries=SimpleNamespace(nvars=0), integers=SimpleNamespace(nvars=0),
                                 variables=variables, constraints=rows, objective=objective)
        input_path = root / 'input.nc'
        input_path.write_bytes(b'fixture input identity')
        uc_path = case / 'UC_MILP/fixture_uc.nc'
        uc_path.parent.mkdir()
        uc_path.write_bytes(b'fixture UC identity; not a solved production result')
        uc_sha = sha256_file(uc_path)
        job = 'FIXTURE_2040_SLOW_FIXED_COMMITMENT_PRICE_LP'
        provenance = dict(year=2040, scenario='Slow', kind='FIXED_COMMITMENT_PRICE_LP', job_id=job,
            result_branch='final_v4', input_sha256=sha256_file(input_path), fixed_commitment_milp_sha256=uc_sha)
        capture = capture_price_evidence(n, directory, provenance, extended=True)
        network = directory / 'fixture_lp.nc'
        n.export_to_netcdf(network)
        capture = bind_price_evidence(directory, capture, sha256_file(network))
        receipt = directory / 'SOLVE_RECEIPT.json'
        lp = dict(status='PASS', year=2040, scenario='Slow', snapshots=8760, termination_condition='optimal',
            kind='FIXED_COMMITMENT_PRICE_LP', job_id=job, solved_sha256=sha256_file(network),
            fixed_commitment_milp_sha256=uc_sha, input_path='input.nc', input_sha256=sha256_file(input_path),
            final_methodology_variant='final_v4', price_constraint_evidence=capture, validation_fixture=True)
        write_json(receipt, lp)
        verify = case / 'QA/VERIFY_PRICE.json'
        write_json(verify, dict(status='PASS', year=2040, scenario='Slow', job_id=job,
                               solved_sha256=sha256_file(network), milp_solved_sha256=uc_sha))
        uc_receipt, uc_verify = uc_path.parent / 'UC_RECEIPT.json', case / 'QA/VERIFY_UC.json'
        write_json(uc_receipt, dict(status='PASS', solved_sha256=uc_sha))
        write_json(uc_verify, dict(status='PASS', year=2040, scenario='Slow', solved_sha256=uc_sha))
        write_json(case / 'QA/VERIFY_REFERENCE.json', dict(status='PASS', year=2040, scenario='Slow', solved_sha256='fixture_ref'))
        write_json(report / 'MEM_UC2_REPORTING_RECEIPT.json', dict(status='PASS', reporting_solver_invocations=0,
            year=2040, scenario='Slow', reference_verification='fixture_ref', uc_verification=uc_sha,
            price_verification=sha256_file(network)))
        write_json(report / 'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json', dict(price_source='FIXED_COMMITMENT_PRICE_LP',
            protected_source_hashes={str(network): sha256_file(network)}))
        write_json(report / 'economic_comparison.json', dict(continuous_reference_objective=613200.,
            uc_milp_objective=613200., fixed_commitment_lp_objective=613200., uc_uplift_percent=0.))
        pd.DataFrame([dict(available_MWh=0., dispatched_MWh=0., curtailed_MWh=0.)]).to_csv(
            report / 'vre_curtailment.csv', index=False)
        pd.DataFrame([dict(result_id=job, year=2040, scenario='Slow', solved_or_unsolved='SOLVED',
            network_path=str(network), network_sha256=sha256_file(network), receipt=str(receipt), manifest=str(verify)),
            dict(result_id='FIXTURE_2040_SLOW_UC_MILP', year=2040, scenario='Slow', solved_or_unsolved='SOLVED',
            network_path=str(uc_path), network_sha256=uc_sha, receipt=str(uc_receipt), manifest=str(uc_verify))]
        ).assign(UC_status='VERIFIED_UC2').to_csv(report / 'uc2_VIS_RESULT_AUTHORITY_REGISTER.csv', index=False)
        n.buses_t.marginal_price[zones].to_csv(report / 'fixed_commitment_prices_hourly.csv')
        interfaces = report / 'CANONICAL/statistics/uc2_net_interface_registry.csv'
        interfaces.parent.mkdir(parents=True)
        pd.DataFrame(columns=['interface_id', 'from_bus', 'to_bus', 'positive_link_ids', 'negative_link_ids']).to_csv(interfaces, index=False)
        hydro = root / 'pre_pypsa_inputs/MEM_Hydro_Static_Component_Mapping.csv'
        hydro.parent.mkdir()
        pd.DataFrame(columns=['zone', 'hydro_class']).to_csv(hydro, index=False)
        pd.DataFrame(columns=['figure', 'family', 'status', 'underlying_data', 'png', 'svg']).to_csv(report / 'VIS_INDEX.csv', index=False)
        vis = report / 'VIS_X1'
        vis.mkdir()
        write_json(vis / 'FIGURE_METADATA.json', dict(figure_count=0, figures=[]))
        pd.DataFrame(columns=['figure', 'family', 'status', 'underlying_data', 'png', 'svg']).to_csv(vis / 'FIGURE_INDEX.csv', index=False)
        return dict(root=root, case=case, report=report, network=network, receipt=receipt, lp=lp,
                    capture=capture, native_manifest=Path(capture['path']), n=n)


def route(monkeypatch, case):
    monkeypatch.setattr(engine, 'ROOT', case['root'])
    monkeypatch.setattr(uc2, 'case_root', lambda *_: case['case'])
    monkeypatch.setattr(uc2, 'job_paths', lambda *_: {'receipt': case['receipt']})


def test_complete_saved_capture_to_frozen_roots_figure_and_inspection(saved_case, monkeypatch):
    route(monkeypatch, saved_case)
    before = {path: sha256_file(path) for path in (saved_case['network'], saved_case['receipt'], saved_case['native_manifest'])}
    with uc2.execution_variant('final_v4'):
        result = pipeline.generate(2040, 'Slow', saved_case['report'])
        assert result['status'] == 'VALIDATED_ROOT_PACKAGE_GENERATED'
        assert result['rows'] == 61320 and result['production_validated'] is False
        output = regime.produce(2040, 'Slow', reporting_output=saved_case['report'])
    assert output['status'] == 'PASS_CANONICAL_MARGINAL_REGIME'
    shares = pd.read_csv(saved_case['report'] / 'CANONICAL/marginal_regime/data/lexicographic_regime_zonal_shares_2040_Slow.csv')
    assert shares.Thermal.eq(100.).all()
    assert {path: sha256_file(path) for path in before} == before
    gallery = inspection.build_inspection(saved_case['report'])
    assert gallery['applicable_figures'] == gallery['generated_applicable_figures'] == 1
    assert inspection.validate_inspection(saved_case['report'])['status'] == 'COMPLETE'
    assert 'lexicographic_regime_shares' in (saved_case['case'] / 'INSPECTION/index.html').read_text(encoding='utf-8')
    tables = pd.read_csv(saved_case['case'] / 'INSPECTION/TABLE_INDEX.csv')
    assert tables.canonical_source_path.str.contains('marginal_setter_candidates').any()
    assert tables.canonical_source_path.str.contains('lexicographic_regime_zonal_shares').any()
    root = saved_case['report'] / regime.config()['root_input']
    qa = json.loads((root / 'marginal_setter_QA_2040_Slow.json').read_text())
    assert qa['P2X_shadows']['P2X_NORD']['shadow'] == 20.
    assert qa['native_evidence_validation']['economic_stack_population_used'] is False
    assert all(item['changed_zone_hours'] == 0 for item in qa['tolerance_sensitivity'].values())


def test_unresolved_interpretation_is_explicit_and_never_rendered(saved_case, monkeypatch):
    route(monkeypatch, saved_case)
    folder = saved_case['report'] / regime.config()['root_input']
    paths = regime._input_paths(2040, 'Slow', folder)
    original = paths[2].read_bytes()
    qa = json.loads(original)
    qa.update(status='KKT_VALID_REGIME_INTERPRETATION_REQUIRED',
              regime_interpretation_required=[dict(reason='NO_ADMISSIBLE_KKT_ROOT', zone_hours=1)])
    write_json(paths[2], qa)
    monkeypatch.setattr(regime, 'resolve', lambda *_: pytest.fail('Unresolved evidence must not be rendered'))
    try:
        with uc2.execution_variant('final_v4'):
            result = pipeline.generate(2040, 'Slow', saved_case['report'])
            output = regime.produce(2040, 'Slow', reporting_output=saved_case['report'])
        assert result['status'] == output['status'] == 'WAITING_FOR_SUPPORTED_ROOT_INTERPRETATION'
        assert result['reason'] == qa['regime_interpretation_required']
    finally:
        paths[2].write_bytes(original)


def test_changed_frozen_record_is_rejected(tmp_path):
    path = tmp_path / 'FROZEN_METHODOLOGY.json'
    write_json(path, dict(status='FROZEN_AFTER_BASE_PASS'))
    with pytest.raises(RuntimeError, match='FROZEN_RECORD_CHANGED'):
        pipeline._freeze(path)


def test_native_stationarity_rejects_wrong_constraint_coefficient(saved_case, tmp_path):
    manifest = pipeline._read(saved_case['native_manifest'])
    directory = saved_case['network'].parent
    paths = dict(constraints=directory / manifest['table'], variables=directory / pipeline.VARIABLE_FILE,
                 terms=tmp_path / pipeline.TERM_FILE)
    terms = pd.read_parquet(directory / pipeline.TERM_FILE)
    # The Bus row dual is nonzero; altering its first term must break stationarity.
    rows = pd.read_parquet(paths['constraints'])
    label = rows.loc[rows.constraint_name.eq('Bus-nodal_balance'), 'constraint_label'].iloc[0]
    index = terms.index[terms.constraint_label.eq(label)][0]
    terms.loc[index, 'coefficient'] += 1.
    terms.to_parquet(paths['terms'], index=False)
    with pytest.raises(RuntimeError, match='NATIVE_STATIONARITY_FAILURE'):
        pipeline.restore_native_duals(pypsa.Network(saved_case['network']), manifest, paths)


def test_native_meshed_bus_families_restore_the_same_price_field(saved_case, tmp_path):
    manifest = copy.deepcopy(pipeline._read(saved_case['native_manifest']))
    directory = saved_case['network'].parent
    paths = dict(constraints=tmp_path / manifest['table'], variables=directory / pipeline.VARIABLE_FILE,
                 terms=directory / pipeline.TERM_FILE)
    rows = pd.read_parquet(directory / manifest['table'])
    old = next(item for item in manifest['constraints'] if item['constraint'] == 'Bus-nodal_balance')
    mesh = copy.deepcopy(old)
    mesh.update(constraint='Bus-meshed-30-nodal_balance', assets=['NORD'],
                coordinate_slots=8760, active_rows=8760, inactive_slots=0)
    old.update(assets=[name for name in old['assets'] if name != 'NORD'],
               coordinate_slots=7 * 8760, active_rows=7 * 8760, inactive_slots=0)
    manifest['constraints'].append(mesh)
    rows.loc[rows.constraint_name.eq('Bus-nodal_balance') & rows.asset_id.eq('NORD'), 'constraint_name'] = mesh['constraint']
    rows.to_parquet(paths['constraints'], index=False)
    n = pypsa.Network(saved_case['network'])
    result = pipeline.restore_native_duals(n, manifest, paths)
    assert result['stationarity_max_abs_native'] == 0.
    assert n.buses_t.marginal_price.eq(10.).all().all()


@pytest.mark.parametrize('field', ['job_id', 'result_branch', 'solved_sha256', 'fixed_commitment_milp_sha256'])
def test_matching_hashes_do_not_override_wrong_capture_identity(saved_case, tmp_path, field):
    original = pipeline._read(saved_case['native_manifest'])
    path = tmp_path / saved_case['native_manifest'].name
    changed = copy.deepcopy(original)
    changed['provenance'][field] = 'WRONG_SOURCE'
    write_json(path, changed)
    receipt = copy.deepcopy(saved_case['lp'])
    receipt['price_constraint_evidence']['sha256'] = sha256_file(path)
    write_json(tmp_path / 'RECEIPT.json', receipt)
    with pytest.raises(RuntimeError, match='IDENTITY_CONFLICT|SCOPE_CONFLICT'):
        pipeline._capture({'solve_receipt': tmp_path / 'RECEIPT.json', 'network': tmp_path / 'lp.nc'}, 2040, 'Slow')


def test_missing_primitives_and_partial_packages_fail_closed(saved_case, tmp_path, monkeypatch):
    route(monkeypatch, saved_case)
    manifest = copy.deepcopy(pipeline._read(saved_case['native_manifest']))
    manifest['additional_tables'].pop('terms')
    path = tmp_path / saved_case['native_manifest'].name
    write_json(path, manifest)
    receipt = copy.deepcopy(saved_case['lp'])
    receipt['price_constraint_evidence']['sha256'] = sha256_file(path)
    write_json(tmp_path / 'RECEIPT.json', receipt)
    # Source identity is checked before files are considered; place the required first table only.
    import shutil
    shutil.copyfile(saved_case['network'].parent / manifest['table'], tmp_path / manifest['table'])
    shutil.copyfile(saved_case['network'].parent / pipeline.VARIABLE_FILE, tmp_path / pipeline.VARIABLE_FILE)
    with pytest.raises(RuntimeError, match='PRIMITIVES_MISSING'):
        pipeline._capture({'solve_receipt': tmp_path / 'RECEIPT.json', 'network': tmp_path / 'lp.nc'}, 2040, 'Slow')
    empty_report = tmp_path / 'REPORTING'
    target = empty_report / regime.config()['root_input']
    target.mkdir(parents=True)
    (target / 'partial.json').write_text('{}')
    with pytest.raises(RuntimeError, match='PARTIAL_PACKAGE'):
        pipeline.generate(2040, 'Slow', empty_report)


def test_historical_result_without_capture_does_not_open_network_or_use_cache(tmp_path, monkeypatch):
    receipt = tmp_path / 'PRICE_RECEIPT.json'
    write_json(receipt, dict(status='PASS'))
    monkeypatch.setattr(uc2, 'job_paths', lambda *_: {'receipt': receipt})
    monkeypatch.setattr(pypsa, 'Network', lambda *_: pytest.fail('Historical evidence must not be fabricated'))
    monkeypatch.setattr(engine, 'discover_sources', lambda *_: pytest.fail('No historical cache fallback'))
    report = tmp_path / 'REPORTING'
    result = pipeline.generate(2040, 'Base', report)
    assert result['status'] == 'WAITING_FOR_SAVED_NATIVE_ROOT_EVIDENCE'
    assert result['production_validated'] is False and not report.exists()


@pytest.mark.parametrize('change', ['snapshot_hash', 'constraint_count', 'primal'])
def test_independent_native_checks_reject_incomplete_or_wrong_saved_values(saved_case, change):
    manifest = copy.deepcopy(pipeline._read(saved_case['native_manifest']))
    paths = dict(constraints=saved_case['network'].parent / manifest['table'],
                 variables=saved_case['network'].parent / pipeline.VARIABLE_FILE,
                 terms=saved_case['network'].parent / pipeline.TERM_FILE)
    n = pypsa.Network(saved_case['network'])
    if change == 'snapshot_hash':
        manifest['snapshot_sha256'] = '0' * 64
    elif change == 'constraint_count':
        manifest['constraints'][0]['active_rows'] += 1
    else:
        n.generators_t.p.iloc[0, 0] += 1.
    with pytest.raises(RuntimeError, match='CHRONOLOGY_CONFLICT|ROW_COVERAGE_CONFLICT|PRIMAL_CONFLICT'):
        pipeline.restore_native_duals(n, manifest, paths)

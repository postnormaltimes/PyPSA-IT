"""Tiny source-bound output projections; never render production results or solve."""
import json
import hashlib
from pathlib import Path

import pandas as pd
from PIL import Image
import pytest

from mem_model.reporting import inspection, output_registry as outputs
from mem_model.stage_b_zonal_vre import no_models_or_solves
from mem_model.visualization.uc2_comparison import SELECTED_COMPARISON_VIEWS


@pytest.fixture(autouse=True)
def no_optimization():
    with no_models_or_solves():
        yield


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def pins(folder):
    return {path.relative_to(folder).as_posix(): inspection._sha(path)
            for path in folder.rglob('*') if path.is_file()}


def tiny_figure(folder, name):
    png = folder / (name + '.png')
    png.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (1, 1), 'white').save(png)
    svg = png.with_suffix('.svg')
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding='utf-8')
    return png, svg


def make_report(root, scenario='Base', year=2050):
    report = root / str(year) / scenario / 'REPORTING'
    report.mkdir(parents=True)
    fields = dict(reference_verification='ref', uc_verification='uc', price_verification='price')
    write_json(report / 'MEM_UC2_REPORTING_RECEIPT.json',
               dict(status='PASS', year=year, scenario=scenario, reporting_solver_invocations=0, **fields))
    for kind, digest in [('REFERENCE', 'ref'), ('UC', 'uc'), ('PRICE', 'price')]:
        write_json(report.parent / f'QA/VERIFY_{kind}.json',
                   dict(status='PASS', year=year, scenario=scenario, solved_sha256=digest))
    data = report / 'VIS_X1/data/dispatch.csv'
    data.parent.mkdir(parents=True)
    data.write_text('zone,value\nNORD,1\n', encoding='utf-8')
    png, svg = tiny_figure(report / 'VIS_X1/operation', 'dispatch')
    f = dict(figure='dispatch', family='operation', status='CURRENT_ACCEPTED_RESULT',
             png=png.relative_to(report).as_posix(), svg=svg.relative_to(report).as_posix(),
             underlying_data=[data.relative_to(report).as_posix()])
    write_json(report / 'VIS_X1/FIGURE_METADATA.json', dict(figure_count=1, figures=[f]))
    pd.DataFrame([f]).to_csv(report / 'VIS_X1/FIGURE_INDEX.csv', index=False)
    write_json(report / 'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json',
               dict(status='PASS', model_generation='UC2', year=year, scenario=scenario,
                    physical_source='UC_MILP', price_source='FIXED_COMMITMENT_PRICE_LP',
                    reporting_solver_invocations=0, artifact_hashes=pins(report)))
    return report


@pytest.fixture
def report(tmp_path):
    # Keep the authentic root filenames below Windows' path-length boundary.
    fixture_root = tmp_path.parent / ('c' + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:7])
    return make_report(fixture_root)


def add_regime(report):
    from mem_model.reporting import marginal_regime as regime
    case = json.loads((report / 'MEM_UC2_REPORTING_RECEIPT.json').read_text())
    year, scenario = case['year'], case['scenario']
    cfg = regime.config()
    roots = regime._input_paths(year, scenario, report / cfg['root_input'])
    for path in roots:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == '.json':
            write_json(path, {})
        else:
            pd.DataFrame({'value': [1]}).to_parquet(path)
    data = report / cfg['data_output'] / 'share.csv'
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text('category,share\nThermal,100\n', encoding='utf-8')
    folder = report / cfg['figure_output']
    png, svg = tiny_figure(folder, 'regime')
    metadata = folder / 'LEXICOGRAPHIC_FIGURE_METADATA.json'
    write_json(metadata, [dict(figure='zonal_share', scenario=scenario,
                              files=[str(png), str(svg)], underlying_data=[str(data)])])
    write_json(report / regime.RECEIPT,
               dict(status='PASS_CANONICAL_MARGINAL_REGIME', model_year=year, scenario=scenario,
                    routine_activation=True, solver_invocations=0, network_opens=0,
                    source_hashes={str(path): inspection._sha(path) for path in roots},
                    artifact_hashes={str(path): inspection._sha(path) for path in (png, svg, data, metadata)}))


def add_dispatch(report, monkeypatch, *, include_diagnostic=True):
    from mem_model.reporting import dispatch_figures
    case = json.loads((report / 'MEM_UC2_REPORTING_RECEIPT.json').read_text())
    entries = dispatch_figures.dispatch_figure_declarations(case['year'], case['scenario'])
    if not include_diagnostic:
        entries = [entry for entry in entries if entry['required']]
    for entry in entries:
        tiny_figure(report / Path(entry['png']).parent, Path(entry['png']).stem)
        for name in entry['underlying_data']:
            (report / name).write_text('snapshot,value\n2050-01-01,1\n', encoding='utf-8')
    folder = report / 'DISPATCH_PRESENTATION'
    write_json(folder / 'FIGURE_METADATA.json', dict(year=case['year'], scenario=case['scenario'],
               figure_count=len(entries), figures=entries))
    payload = dict(status='PASS_DISPATCH_PRESENTATION', diagnostic_status='PASS',
                   year=case['year'], scenario=case['scenario'], artifact_hashes={
                       'DISPATCH_PRESENTATION/' + key: value for key, value in pins(folder).items()},
                   optional_artifacts=[name for entry in entries if not entry['required']
                                       for name in [entry['png'], entry['svg'], entry['underlying_data'][0]]],
                   source_hashes={str(report / 'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json'):
                                 inspection._sha(report / 'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json')})
    write_json(folder / 'DISPATCH_PRESENTATION_RECEIPT.json', payload)
    # The adapter's full-year arithmetic has its own fixtures. This fixture
    # isolates register projection/hash checks using the real declarations.
    monkeypatch.setattr(dispatch_figures, 'validate_dispatch_figures', lambda _: payload)
    return payload


def add_comparisons(report, scenarios, *, all_selected=False, regime_comparison=False):
    directory = report.parents[1] / 'COMPARISONS_VIS_X1'
    data = directory / 'data/comparison_metrics.csv'
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text('scenario,value\nBase,1\n', encoding='utf-8')
    retained = ['monthly_zonal_price', 'price_duration', 'interface_net_energy_MWh']
    entries = []
    for view in retained:
        png, svg = tiny_figure(directory / 'figures', view)
        entries.append(dict(figure_id=view, year=2050, scenarios=scenarios,
                            files=[path.relative_to(directory).as_posix() for path in (png, svg)],
                            underlying_data=['data/comparison_metrics.csv']))
    write_json(directory / 'MEM_UC2_COMPARISON_FIGURE_METADATA.json', entries)
    original_paths = [data, directory / 'MEM_UC2_COMPARISON_FIGURE_METADATA.json']
    original_paths += [directory / name for entry in entries for name in entry['files']]
    pd.DataFrame([dict(artifact=path.relative_to(directory).as_posix(), sha256=inspection._sha(path))
                  for path in original_paths]).to_csv(
        directory / 'MEM_UC2_COMPARISON_MANIFEST.csv', index=False)
    receipts = {scenario: inspection._sha(report.parents[1] / scenario / 'REPORTING/MEM_UC2_VIS_REPORTING_R1_RECEIPT.json')
                for scenario in scenarios}
    base = directory / 'MEM_UC2_COMPARISON_RECEIPT.json'
    write_json(base, dict(status='PASS', year=2050, scenarios=scenarios, model_generation='UC2',
                         physical_source='UC_MILP', price_source='FIXED_COMMITMENT_PRICE_LP',
                         reporting_solver_invocations=0, source_receipt_sha256=receipts, figure_count=len(entries),
                         manifest_sha256=inspection._sha(directory / 'MEM_UC2_COMPARISON_MANIFEST.csv')))
    if all_selected:
        presentation = directory / 'presentation'
        selected = [view[0] for view in SELECTED_COMPARISON_VIEWS if view[0] not in retained
                    and view[0] != 'marginal_regime_national_comparison']
        selected_entries = []
        for view in selected:
            png, svg = tiny_figure(presentation, view)
            selected_entries.append(dict(figure=view, year=2050, scenarios=scenarios,
                                         files=[str(png), str(svg)], underlying_data=[str(data)]))
        write_json(presentation / 'SELECTION_FIGURE_METADATA.json', selected_entries)
        write_json(presentation / 'COMPARISON_SELECTION_RECEIPT.json',
                   dict(status='PASS', year=2050, scenarios=scenarios, solver_invocations=0,
                        network_open_or_extraction_calls=0, source_receipt_sha256=inspection._sha(base),
                        source_hashes={str(data): inspection._sha(data)}, figure_count=len(selected_entries),
                        artifact_hashes={name: digest for name, digest in pins(presentation).items()
                                         if name != 'COMPARISON_SELECTION_RECEIPT.json'}))
    if regime_comparison:
        folder = directory / 'marginal_regime'
        png, svg = tiny_figure(folder / 'figures', 'national')
        table = folder / 'LEXICOGRAPHIC_REGIME_NATIONAL_SHARES_2050.csv'
        table.write_text('category,Slow,Base,High\nThermal,100,100,100\n', encoding='utf-8')
        write_json(folder / 'LEXICOGRAPHIC_FIGURE_METADATA.json',
                   [dict(figure='national_comparison', scenario_order=scenarios,
                         files=[str(png), str(svg)], underlying_data=[str(table)])])
        write_json(folder / 'MEM_MARGINAL_REGIME_COMPARISON_RECEIPT.json',
                   dict(status='PASS_CANONICAL_MARGINAL_REGIME_COMPARISON', model_year=2050,
                        scenarios=scenarios, routine_activation=True, solver_invocations=0, network_opens=0,
                        artifact_hashes={str(folder / key): digest for key, digest in pins(folder).items()},
                        source_hashes={str(report.parents[1] / scenario / 'REPORTING/MEM_MARGINAL_REGIME_RECEIPT.json'):
                                      inspection._sha(report.parents[1] / scenario / 'REPORTING/MEM_MARGINAL_REGIME_RECEIPT.json')
                                      for scenario in scenarios}))
    return directory


def rows_by_id(value):
    return {row['output_id']: row for row in value['rows']}


def test_readonly_preview_reuses_core_and_preserves_unknown_outputs(report):
    before = pins(report)
    result = outputs.build_output_register(report, 2050, 'Base', persist=False)
    assert pins(report) == before
    assert result['core_reporting_status'] == 'CORE_REPORTING_PASS'
    assert result['accepted_visualizations_status'] == outputs.WAITING
    assert result['status'] == outputs.WAITING
    assert result['evidence_gated_outputs_status'] == outputs.WAITING
    assert result['cross_scenario_comparisons_status'] == 'CROSS_SCENARIO_COMPARISONS_WAITING'
    assert result['core_figure_count'] == 1 and result['selected_comparison_count'] == 18
    rows = rows_by_id(result)
    assert rows['VIS_X1/dispatch']['output_type'] == 'SINGLE_CASE_FIGURE'
    assert rows['marginal_regime']['status'] == 'WAITING_FOR_VALIDATED_ROOT_EVIDENCE'
    assert rows['economic_equilibrium_stack']['required'] is False
    assert result['economic_routine_activation'] is False
    assert not any(Path(name).is_absolute() for name in result['evidence_hashes'])
    assert str(report.parents[3]) not in json.dumps(result)
    assert outputs.validate_output_register(report)['register_status'] == 'READ_ONLY_PREVIEW_NO_PERSISTED_REGISTER'
    assert pins(report) == before


def test_generated_routine_and_optional_diagnostic_missing_are_distinct(report, monkeypatch):
    add_regime(report)
    add_dispatch(report, monkeypatch, include_diagnostic=False)
    result = outputs.build_output_register(report, 2050, 'Base', persist=False)
    assert result['accepted_visualizations_status'] == 'ACCEPTED_VISUALIZATIONS_PASS'
    assert result['status'] == outputs.WAITING  # comparisons still missing
    diagnostic = rows_by_id(result)['DISPATCH_PRESENTATION/annual_dispatch_chronological_1095']
    assert diagnostic['required'] is False and diagnostic['status'] == 'WAITING_FOR_DISPATCH_PRESENTATION'


def test_persisted_register_is_hash_bound_and_explicit_refresh_only(report):
    before = pins(report)
    result = outputs.build_output_register(report, 2050, 'Base')
    assert set(pins(report)).difference(before) == {outputs.CSV_NAME, outputs.JSON_NAME}
    assert outputs.validate_output_register(report) == result
    outputs.build_output_register(report, 2050, 'Base')
    assert outputs.validate_output_register(report) == result
    assert all(pins(report)[name] == digest for name, digest in before.items())


@pytest.mark.parametrize('defect', ['csv', 'data', 'svg', 'png', 'missing_json'])
def test_declared_stale_or_malformed_outputs_fail(report, defect):
    outputs.build_output_register(report, 2050, 'Base')
    if defect == 'csv':
        (report / outputs.CSV_NAME).write_text('bad')
    elif defect == 'missing_json':
        (report / outputs.JSON_NAME).unlink()
    else:
        path = report / ('VIS_X1/data/dispatch.csv' if defect == 'data' else 'VIS_X1/operation/dispatch.' + defect)
        path.write_bytes(b'bad')
    with pytest.raises(ValueError):
        outputs.validate_output_register(report)


def test_unresolved_root_status_survives_read_only_register_validation(report, monkeypatch):
    from mem_model.reporting import marginal_regime as regime
    roots = regime._input_paths(2050, 'Base', report / regime.config()['root_input'])
    reason = [dict(reason='NO_ADMISSIBLE_KKT_ROOT', zone_hours=1)]
    for path in roots:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == '.json':
            write_json(path, dict(status='KKT_VALID_REGIME_INTERPRETATION_REQUIRED'))
        else:
            pd.DataFrame({'value': [1]}).to_parquet(path)
    calls = []
    def validated(year, scenario, paths, directory):
        assert (year, scenario, paths, directory) == (2050, 'Base', roots, report)
        calls.append('source validation')
        return dict(regime_interpretation_required=reason)
    monkeypatch.setattr(regime, '_validate_pending_interpretation', validated)
    first = outputs.build_output_register(report, 2050, 'Base',
        regime=dict(status='WAITING_FOR_SUPPORTED_ROOT_INTERPRETATION', reason=reason))
    later = outputs.validate_output_register(report)
    assert rows_by_id(first)['marginal_regime'] == rows_by_id(later)['marginal_regime']
    assert rows_by_id(later)['marginal_regime']['status'] == 'WAITING_FOR_SUPPORTED_ROOT_INTERPRETATION'
    assert len(calls) == 2


def test_core_consumed_table_must_match_accepted_receipt(report):
    (report / 'VIS_X1/data/dispatch.csv').write_text('zone,value\nNORD,9\n')
    with pytest.raises(ValueError, match='hash mismatch'):
        outputs.build_output_register(report, 2050, 'Base', persist=False)


def test_malformed_partial_root_is_not_treated_as_waiting(report):
    from mem_model.reporting import marginal_regime
    root = marginal_regime._input_paths(2050, 'Base', report / marginal_regime.config()['root_input'])[2]
    root.parent.mkdir(parents=True)
    root.write_text('not json')
    with pytest.raises(ValueError):
        outputs.build_output_register(report, 2050, 'Base', persist=False)


def test_source_tables_available_is_not_a_generated_figure(report):
    make_report(report.parents[2], 'Slow')
    folder = report.parents[1] / 'COMPARISONS_VIS_X1/data'
    folder.mkdir(parents=True)
    (folder / 'comparison_metrics.csv').write_text('scenario,value\nBase,1\n')
    result = outputs.build_output_register(report, 2050, 'Base', persist=False)
    row = rows_by_id(result)['primary_generation']
    assert row['status'] == 'WAITING_FOR_SELECTED_COMPARISON_RENDERING' and row['files'] == []
    assert row['missing_scenarios'] == ['High']


def test_two_case_selected_rendering_is_explicitly_partial(report):
    make_report(report.parents[2], 'Slow')
    add_comparisons(report, ['Slow', 'Base'], all_selected=True)
    result = outputs.build_output_register(report, 2050, 'Base', persist=False)
    assert result['comparison_status'] == 'PARTIAL_COMPARISON_COLLECTION'
    selected = [row for row in result['rows'] if row['output_type'] == 'SELECTED_COMPARISON_FIGURE']
    assert sum(row['status'] == 'GENERATED_PARTIAL_COLLECTION' for row in selected) == 17
    assert rows_by_id(result)['marginal_regime_national_comparison']['status'] == 'WAITING_FOR_COMPLETE_SCENARIO_SET'
    assert all(row['missing_scenarios'] == ['High'] for row in selected)


def test_all_eighteen_actual_declared_outputs_complete(report, monkeypatch):
    for scenario in ['Slow', 'High']:
        add_regime(make_report(report.parents[2], scenario))
    add_regime(report)
    add_dispatch(report, monkeypatch)
    add_comparisons(report, ['Slow', 'Base', 'High'], all_selected=True, regime_comparison=True)
    result = outputs.build_output_register(report, 2050, 'Base')
    assert result['status'] == 'ACCEPTED_VISUALIZATIONS_PASS'
    assert result['comparison_status'] == 'COMPARISON_VISUALIZATIONS_PASS'
    # Optional economic evidence remains separately waiting; it does not gate
    # complete routine figures or cross-scenario visualizations.
    assert result['evidence_gated_outputs_status'] == outputs.WAITING
    assert result['cross_scenario_comparisons_status'] == 'CROSS_SCENARIO_COMPARISONS_PASS'
    selected = [row for row in result['rows'] if row['output_type'] == 'SELECTED_COMPARISON_FIGURE']
    assert [row['output_id'] for row in selected] == [view[0] for view in SELECTED_COMPARISON_VIEWS]
    assert all(row['status'] == 'GENERATED' and len(row['files']) == 2 for row in selected)
    assert outputs.validate_output_register(report) == result


@pytest.mark.parametrize('defect', ['source_receipt', 'rendered_hash', 'metadata_identity', 'missing_render'])
def test_comparison_output_bindings_are_strict(report, defect):
    make_report(report.parents[2], 'Slow')
    directory = add_comparisons(report, ['Slow', 'Base'], all_selected=True)
    if defect == 'source_receipt':
        path = directory / 'MEM_UC2_COMPARISON_RECEIPT.json'
        value = json.loads(path.read_text())
        value['source_receipt_sha256']['Slow'] = 'stale'
        write_json(path, value)
    elif defect == 'rendered_hash':
        (directory / 'figures/monthly_zonal_price.svg').write_text('<svg>bad</svg>')
    elif defect == 'missing_render':
        (directory / 'presentation/primary_generation.png').unlink()
    else:
        path = directory / 'presentation/SELECTION_FIGURE_METADATA.json'
        value = json.loads(path.read_text())
        value[0]['year'] = 2040
        write_json(path, value)
        rp = directory / 'presentation/COMPARISON_SELECTION_RECEIPT.json'
        receipt = json.loads(rp.read_text())
        receipt['artifact_hashes'][path.name] = inspection._sha(path)
        write_json(rp, receipt)
    with pytest.raises(ValueError):
        outputs.build_output_register(report, 2050, 'Base', persist=False)


def test_new_case_evidence_requires_explicit_register_refresh(report):
    stored = outputs.build_output_register(report, 2050, 'Base')
    make_report(report.parents[2], 'Slow')
    current = outputs.validate_output_register(report)
    assert current['status'] == outputs.WAITING
    assert current['comparison_status'] == 'WAITING_FOR_COMPARISON_REGISTER_REFRESH'
    assert json.loads((report / outputs.JSON_NAME).read_text()) == stored
    refreshed = outputs.build_output_register(report, 2050, 'Base')
    assert outputs.validate_output_register(report) == refreshed


def test_declared_producer_pass_cannot_replace_missing_render_receipt(report):
    with pytest.raises(ValueError, match='lacks its receipt'):
        outputs.build_output_register(report, 2050, 'Base', regime={'status': 'PASS_CANONICAL_MARGINAL_REGIME'}, persist=False)


def test_new_report_required_register_cannot_be_deleted(report):
    path = report / 'MEM_UC2_REPORTING_RECEIPT.json'
    value = json.loads(path.read_text())
    value['output_register_required'] = True
    write_json(path, value)
    r1_path = report / 'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json'
    r1 = json.loads(r1_path.read_text())
    r1['artifact_hashes'][path.name] = inspection._sha(path)
    write_json(r1_path, r1)
    # Initial creation still uses the unchanged same-case source verifier.
    outputs.build_output_register(report, 2050, 'Base')
    (report / outputs.JSON_NAME).unlink()
    (report / outputs.CSV_NAME).unlink()
    with pytest.raises(ValueError, match='declared consolidated output register is missing'):
        outputs.validate_output_register(report)


def test_conflicting_original_and_extension_source_bindings_fail(report):
    path = report / 'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json'
    value = json.loads(path.read_text())
    value['original_artifact_hashes'] = {'VIS_X1\\data\\dispatch.csv': 'different'}
    write_json(path, value)
    with pytest.raises(ValueError, match='Conflicting core source hash'):
        outputs.build_output_register(report, 2050, 'Base', persist=False)


def test_inspection_projects_actual_persisted_output_register(report):
    write_json(report / 'economic_comparison.json', dict(uc_uplift_EUR=1, uc_uplift_percent=10))
    (report / 'vre_curtailment.csv').write_text('available_MWh,dispatched_MWh,curtailed_MWh\n2,1,1\n')
    result = outputs.build_output_register(report, 2050, 'Base')
    before = pins(report)
    inspection_result = inspection.build_inspection(report)
    assert inspection_result['status'] == 'COMPLETE' and pins(report) == before
    folder = report.parent / 'INSPECTION'
    manifest = json.loads((folder / 'INSPECTION_MANIFEST.json').read_text())
    assert manifest['output_status'] == {key: result[key] for key in
            ('core_reporting_status', 'accepted_visualizations_status', 'comparison_status',
             'evidence_gated_outputs_status', 'cross_scenario_comparisons_status')}
    assert manifest['output_register_sha256'] == inspection._sha(report / outputs.JSON_NAME)
    assert len(manifest['additional_visualization_status']) == 22
    assert 'Additional output availability' in (folder / 'index.html').read_text()
    assert 'Supply current validated roots' in (folder / 'index.html').read_text()
    assert outputs.validate_output_register(report) == result


def test_missing_optional_diagnostic_does_not_revoke_routine_completion(report, monkeypatch):
    add_regime(report)
    dispatch = add_dispatch(report, monkeypatch)
    outputs.build_output_register(report, 2050, 'Base')
    (report / dispatch['optional_artifacts'][0]).unlink()
    dispatch['diagnostic_status'] = 'MISSING_OR_CHANGED_OPTIONAL_DIAGNOSTIC'
    result = outputs.validate_output_register(report)
    assert result['core_reporting_status'] == 'CORE_REPORTING_PASS'
    assert result['accepted_visualizations_status'] == 'ACCEPTED_VISUALIZATIONS_PASS'
    assert result['register_status'] == 'OPTIONAL_DIAGNOSTIC_REGISTER_REFRESH_REQUIRED'
    assert rows_by_id(result)['DISPATCH_PRESENTATION/annual_dispatch_chronological_1095']['status'] == 'WAITING_FOR_DISPATCH_PRESENTATION'


def test_valid_third_case_shared_regeneration_requires_readonly_refresh(report):
    make_report(report.parents[2], 'Slow')
    add_comparisons(report, ['Slow', 'Base'], all_selected=True)
    stored = outputs.build_output_register(report, 2050, 'Base')
    same_case_before = {name: digest for name, digest in pins(report).items()
                        if name not in (outputs.CSV_NAME, outputs.JSON_NAME)}
    make_report(report.parents[2], 'High')
    add_comparisons(report, ['Slow', 'Base', 'High'], all_selected=True)
    current = outputs.validate_output_register(report)
    assert current['comparison_status'] == 'WAITING_FOR_COMPARISON_REGISTER_REFRESH'
    assert current['status'] == outputs.WAITING
    assert current['core_reporting_status'] == 'CORE_REPORTING_PASS'
    assert rows_by_id(current)['primary_generation']['status'] == 'GENERATED'
    assert json.loads((report / outputs.JSON_NAME).read_text()) == stored
    assert all(pins(report)[name] == digest for name, digest in same_case_before.items())


@pytest.mark.parametrize('defect', ['receipt_binding', 'rendered_hash', 'same_case_evidence'])
def test_shared_refresh_never_masks_stale_receipts_or_same_case_changes(report, defect):
    make_report(report.parents[2], 'Slow')
    add_comparisons(report, ['Slow', 'Base'], all_selected=True)
    outputs.build_output_register(report, 2050, 'Base')
    make_report(report.parents[2], 'High')
    directory = add_comparisons(report, ['Slow', 'Base', 'High'], all_selected=True)
    if defect == 'receipt_binding':
        path = directory / 'MEM_UC2_COMPARISON_RECEIPT.json'
        q = json.loads(path.read_text())
        q['source_receipt_sha256']['High'] = 'stale'
        write_json(path, q)
    elif defect == 'rendered_hash':
        (directory / 'presentation/primary_generation.png').write_bytes(b'bad')
    else:
        path = report.parent / 'QA/VERIFY_UC.json'
        q = json.loads(path.read_text())
        q['extra'] = 'changed same-case evidence'
        write_json(path, q)
    with pytest.raises(ValueError):
        outputs.validate_output_register(report)

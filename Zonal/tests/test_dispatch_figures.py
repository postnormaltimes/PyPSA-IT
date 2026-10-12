"""Routine dispatch figures from saved known-year UC tables; never solve."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from mem_model.common import sha256_file
from mem_model.reporting import dispatch_figures as figures
from mem_model.visualization import dispatch_presentation as presentation


def write_json(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def case(tmp_path, monkeypatch):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import pypsa
    import linopy
    def forbidden(*args, **kwargs):
        raise AssertionError('No network or optimization model may be constructed')
    monkeypatch.setattr(pypsa, 'Network', forbidden)
    monkeypatch.setattr(linopy.Model, '__init__', forbidden)
    captured = []
    def plot(data, boundaries, colors, **kwargs):
        captured.append((data.copy(), boundaries.copy(), colors, kwargs))
        return plt.figure(figsize=(2, 1)), 'DejaVu Sans'
    monkeypatch.setattr(presentation, 'plot_daily_presentation', plot)
    monkeypatch.setattr(presentation, 'plot_eight_hour_presentation', plot)
    report = tmp_path/'REPORTING'
    (report/'VIS_X1/data').mkdir(parents=True)
    (report/'CANONICAL/statistics').mkdir(parents=True)
    index = pd.date_range('2019-01-01', '2020-01-01', inclusive='left', freq='h', name='snapshot')
    net = np.tile([3., -2.], 4380)
    charge = np.tile([0., 1.], 4380)
    operation = pd.DataFrame({'snapshot': index, 'zone': 'NATIONAL', 'Solar PV - utility': 5.,
        'Nuclear': 2., 'Gas + CCS': 1., 'Bioenergy + CCS': .5, 'Gas - OCGT': .25,
        'Hydrogen - OCGT': .25, 'BESS': np.tile([2., 0.], 4380), 'PHS': 1.,
        'bess_charging_MW': charge, 'phs_charging_MW': .5, 'p2x_electrical_consumption_MW': .2,
        'net_imports_MW': net, 'load_shedding_MW': .1})
    supplies = ['Solar PV - utility', 'Nuclear', 'Gas + CCS', 'Bioenergy + CCS',
                'Gas - OCGT', 'Hydrogen - OCGT', 'BESS', 'PHS']
    operation['rigid_end_use_demand_MW'] = operation[supplies].sum(axis=1)+net+.1-charge-.5-.2
    operation['gross_end_use_demand_MW'] = operation.rigid_end_use_demand_MW+.2
    operation['storage_phs_charging_MW'] = charge+.5
    external = pd.DataFrame(0., index=index, columns=figures.reporting_config()['zone_order'])
    external['NORD'] = net
    duration = pd.concat([pd.DataFrame({'market': market, 'snapshot': index, 'weight': 1.})
                          for market in ('NORD', 'CNORD', 'CSUD', 'SUD', 'CALA', 'SICI', 'SARD')], ignore_index=True)
    uc = tmp_path/'UC_MILP_SOLVED.nc'
    uc.write_bytes(b'verified saved UC bytes; never opened')
    receipt, verification = tmp_path/'UC_RECEIPT.json', tmp_path/'VERIFY_UC.json'
    job = 'FINAL_V4_UC2_2050_BASE_8760H_UC_MILP'
    value = dict(status='PASS', year=2050, scenario='Base', kind='UC_MILP', job_id=job, solved_sha256=sha256_file(uc))
    write_json(receipt, value)
    write_json(verification, value)
    authority = pd.DataFrame([dict(current_role='CANONICAL_PHYSICAL', year=2050, scenario='Base',
        result_id=job, network_path=str(uc), network_sha256=sha256_file(uc), receipt=str(receipt),
        manifest=str(verification), solved_or_unsolved='SOLVED', UC_status='VERIFIED_UC2', visualization_allowed=True)])
    authority.to_csv(report/figures.AUTHORITY_SOURCE, index=False)
    source_receipt = dict(status='PASS', year=2050, scenario='Base', physical_source='UC_MILP',
        result_status='VERIFIED_RESULT_PENDING_ANALYTICAL_REVIEW', reporting_solver_invocations=0,
        protected_source_hashes={str(path): sha256_file(path) for path in (uc, receipt, verification)})
    def save():
        operation.to_parquet(report/figures.DISPATCH_SOURCE, index=False)
        external.to_parquet(report/figures.EXCHANGE_SOURCE)
        duration.to_parquet(report/figures.DURATION_SOURCE, index=False)
        source_receipt['artifact_hashes'] = {name: sha256_file(report/name) for name in (
            figures.DISPATCH_SOURCE, figures.EXCHANGE_SOURCE, figures.DURATION_SOURCE, figures.AUTHORITY_SOURCE)}
        write_json(report/figures.SOURCE_RECEIPT, source_receipt)
    save()
    return dict(report=report, operation=operation, external=external, duration=duration,
                source_receipt=source_receipt, uc_receipt=receipt, uc=uc, save=save, captured=captured, index=index)


def produce(case):
    return figures.produce_dispatch_figures(case['report'], 2050, 'Base')


def test_daily_principal_and_eight_hour_diagnostic_keep_full_population_and_raw_energy(case):
    originals = {path: sha256_file(path) for path in case['report'].rglob('*') if path.is_file()}
    receipt = produce(case)
    assert receipt['status'] == 'PASS_DISPATCH_PRESENTATION'
    assert receipt['raw_snapshots'] == 8760 and receipt['daily_intervals'] == 365 and receipt['eight_hour_intervals'] == 1095
    assert receipt['solver_invocations'] == receipt['network_opens'] == 0
    assert not receipt['optimization_model_constructed']
    assert receipt['source_UC_solved_sha256'] == sha256_file(case['uc'])
    assert receipt['result_status'] == 'VERIFIED_RESULT_PENDING_ANALYTICAL_REVIEW'
    raw = pd.read_csv(case['report']/figures.FOLDER/figures.RAW_FILE, index_col='snapshot', parse_dates=True)
    daily, bounds, colors, kwargs = case['captured'][0]
    assert 'Nuclear' in daily and 'Hydrogen - OCGT' in daily and 'Load shedding (nonphysical)' in daily
    assert daily.Gas.eq(1.25).all() and daily.Bioenergy.eq(.5).all()
    assert daily.Imports.eq(1.5).all() and daily.Exports.eq(-1.).all()  # Split BEFORE averaging.
    assert daily['BESS discharge'].eq(1.).all() and daily['BESS charging'].eq(-.5).all()
    assert colors['Nuclear']['color'] == figures.reporting_config()['technology_colors']['Nuclear']
    assert kwargs['result_label'] == 'Verified physical UC result'
    assert receipt['daily_energy_max_error_MWh'] < .01 and receipt['family_energy_max_error_MWh'] < .01
    assert raw.index.equals(case['index']) and raw.interval_hours.eq(1).all()
    assert all(sha256_file(path) == digest for path, digest in originals.items())
    metadata = json.loads((case['report']/figures.FOLDER/figures.METADATA).read_text())
    assert metadata['figure_count'] == 2
    for record in metadata['figures']:
        assert all(not Path(record[key]).is_absolute() for key in ('png', 'svg'))
        assert record['status'] == 'VERIFIED_RESULT_PENDING_ANALYTICAL_REVIEW'
    assert figures.validate_dispatch_figures(case['report'])['status'] == 'PASS_DISPATCH_PRESENTATION'
    assert figures.produce_dispatch_figures(case['report'], 2050, 'Base')['status'] == 'PASS_DISPATCH_PRESENTATION'
    assert len(case['captured']) == 2  # Cached call never re-renders.


@pytest.mark.parametrize('fault', ['dispatch_hash', 'exchange_hash', 'receipt_hash', 'uc_identity',
                                   'wrong_case', 'duration_hash', 'duration_market', 'duration_weights',
                                   'chronology', 'unknown_carrier', 'negative_supply', 'hourly_balance', 'exchange'])
def test_unbound_or_invalid_sources_fail_before_any_output(case, fault):
    if fault == 'dispatch_hash':
        case['operation'].assign(Nuclear=3.).to_parquet(case['report']/figures.DISPATCH_SOURCE, index=False)
    elif fault == 'exchange_hash':
        case['external'].assign(NORD=0.).to_parquet(case['report']/figures.EXCHANGE_SOURCE)
    elif fault == 'receipt_hash':
        value = json.loads(case['uc_receipt'].read_text())
        value['status'] = 'CANDIDATE'
        write_json(case['uc_receipt'], value)
    elif fault == 'uc_identity':
        authority = pd.read_csv(case['report']/figures.AUTHORITY_SOURCE)
        authority['network_sha256'] = 'a'*64
        authority.to_csv(case['report']/figures.AUTHORITY_SOURCE, index=False)
        case['save']()
    elif fault == 'wrong_case':
        case['source_receipt']['year'] = 2040
        case['save']()
    elif fault == 'duration_hash':
        case['duration'].assign(weight=2.).to_parquet(case['report']/figures.DURATION_SOURCE, index=False)
    else:
        if fault == 'duration_market':
            case['duration'].loc[0, 'market'] = 'OTHER'
        elif fault == 'duration_weights':
            case['duration'].loc[0, 'weight'] = 2.
        elif fault == 'chronology':
            case['operation'].loc[0, 'snapshot'] = pd.Timestamp('2020-01-01')
        elif fault == 'unknown_carrier':
            case['operation']['Unsupported carrier'] = 0.
        elif fault == 'negative_supply':
            case['operation'].loc[0, 'Nuclear'] = -1.
        elif fault == 'hourly_balance':
            case['operation'].loc[0, 'rigid_end_use_demand_MW'] += 1.
        elif fault == 'exchange':
            case['external'].iloc[0, 0] += 1.
        case['save']()
    with pytest.raises(ValueError, match='DISPATCH'):
        produce(case)
    assert not (case['report']/figures.FOLDER).exists()
    assert not case['captured']


def test_optional_diagnostic_absence_does_not_block_primary_readonly_validation(case):
    produce(case)
    path = case['report']/figures.dispatch_figure_declarations(2050, 'Base')[1]['png']
    path.unlink()
    receipt = figures.validate_dispatch_figures(case['report'])
    assert receipt['status'] == 'PASS_DISPATCH_PRESENTATION'
    assert receipt['diagnostic_status'] == 'MISSING_OR_CHANGED_OPTIONAL_DIAGNOSTIC'


def test_readonly_validator_checks_required_outputs_and_source_bytes(case):
    produce(case)
    before = {path: sha256_file(path) for path in case['report'].rglob('*') if path.is_file()}
    figures.validate_dispatch_figures(case['report'])
    assert all(sha256_file(path) == digest for path, digest in before.items())
    case['operation'].assign(Nuclear=3.).to_parquet(case['report']/figures.DISPATCH_SOURCE, index=False)
    with pytest.raises(ValueError, match='SOURCE_CHANGED'):
        figures.validate_dispatch_figures(case['report'])


def test_required_daily_figure_absence_fails_readonly_validation(case):
    produce(case)
    (case['report']/figures.dispatch_figure_declarations(2050, 'Base')[0]['svg']).unlink()
    with pytest.raises(ValueError, match='ARTIFACT_CHANGED'):
        figures.validate_dispatch_figures(case['report'])


def test_pass_receipt_cannot_leave_required_daily_artifact_unpinned(case):
    produce(case)
    path = case['report']/figures.FOLDER/figures.RECEIPT
    receipt = json.loads(path.read_text())
    del receipt['artifact_hashes'][figures.dispatch_figure_declarations(2050, 'Base')[0]['png']]
    write_json(path, receipt)
    with pytest.raises(ValueError, match='REQUIRED_ARTIFACT_UNPINNED'):
        figures.validate_dispatch_figures(case['report'])


@pytest.mark.parametrize('fault', ['source_pins', 'source_status', 'source_uc', 'primary_optional'])
def test_pass_receipt_cannot_bypass_source_binding_or_primary_availability(case, fault):
    produce(case)
    path = case['report']/figures.FOLDER/figures.RECEIPT
    receipt = json.loads(path.read_text())
    if fault == 'source_pins':
        receipt['source_hashes'] = {}
    elif fault == 'source_status':
        receipt['result_status'] = 'CURRENT_ACCEPTED_RESULT'
    elif fault == 'source_uc':
        receipt['source_UC_solved_sha256'] = 'a'*64
    else:
        receipt['optional_artifacts'].append(figures.dispatch_figure_declarations(2050, 'Base')[0]['png'])
    write_json(path, receipt)
    with pytest.raises(ValueError, match='DISPATCH_PRESENTATION'):
        figures.validate_dispatch_figures(case['report'])


def test_existing_unreceipted_outputs_are_not_overwritten(case):
    output = case['report']/figures.FOLDER
    output.mkdir()
    protected = output/'prior.png'
    protected.write_bytes(b'prior presentation')
    with pytest.raises(ValueError, match='OVERWRITE_REQUIRES_REVIEW'):
        produce(case)
    assert protected.read_bytes() == b'prior presentation'


def test_missing_sources_and_missing_metadata_wait_without_writes(tmp_path):
    report = tmp_path/'REPORTING'
    result = figures.produce_dispatch_figures(report, 2050, 'Slow')
    assert result['status'] == 'WAITING_FOR_CASE_BOUND_DISPATCH_SOURCES'
    assert figures.validate_dispatch_figures(report)['status'] == 'WAITING_FOR_DISPATCH_PRESENTATION'
    assert not report.exists()


def test_provided_uc_weights_require_matching_case_and_physical_weights(case):
    network = SimpleNamespace(meta=dict(uc2_year=2050, uc2_scenario='Base', uc2_kind='UC_MILP'),
        snapshots=case['index'], snapshot_weightings=pd.DataFrame({'generators': 1., 'objective': 1.}, index=case['index']))
    (case['report']/figures.DURATION_SOURCE).unlink()
    result = figures.produce_dispatch_figures(case['report'], 2050, 'Base', uc_network=network)
    assert result['duration_provenance']['kind'] == 'PROVIDED_VERIFIED_UC_NETWORK'
    assert result['source_UC_solved_sha256'] == sha256_file(case['uc'])


def test_renderer_default_unchanged_and_explicit_nuclear_shedding_population():
    import matplotlib.pyplot as plt
    index = pd.date_range('2019-01-01', periods=8760, freq='h', name='snapshot')
    raw = pd.DataFrame({'Nuclear': 2., 'Load shedding (nonphysical)': .1, 'Rigid load': 2.1}, index=index)
    detailed, bounds, _, _ = presentation.daily_time_bins(raw, pd.Series(1., index=index))
    display = figures._daily_groups(detailed)
    colors = figures._colors(figures.reporting_config())[1]
    figure, _ = presentation.plot_daily_presentation(display, bounds, colors, year=2050, scenario='Slow',
        presentation_order=figures.DAILY_POSITIVE+figures.DAILY_NEGATIVE, negative_order=figures.DAILY_NEGATIVE,
        result_label='Verified physical UC result')
    ax = figure.axes[0]
    assert len(ax.collections) == 2
    assert [text.get_text() for text in ax.get_legend().get_texts()] == ['Nuclear', 'Load shedding (nonphysical)', 'Rigid load']
    load = next(line for line in ax.lines if line.get_label() == 'Rigid load')
    assert load.get_color() == '#333333' and load.get_linewidth() == .65 and load.get_drawstyle() == 'steps-post'
    assert all(text.get_fontsize() == 9.5 for text in ax.get_legend().get_texts())
    assert all(not collection.get_hatch() for collection in ax.collections)
    assert any(text.get_text().startswith('Verified physical UC result |') for text in figure.texts)
    plt.close(figure)
    assert presentation._plot_interval_presentation.__kwdefaults__['result_label'] == 'CURRENT_ACCEPTED_RESULT'
    assert presentation.DAILY_POSITIVE_ORDER == ('Geothermal', 'Bioenergy', 'Gas', 'Hydro - reservoir/pondage',
        'Run-of-river', 'PHS discharge', 'BESS discharge', 'Imports', 'Offshore wind', 'Onshore wind', 'Solar PV')

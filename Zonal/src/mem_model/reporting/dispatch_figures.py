"""Case-bound annual UC dispatch presentation using the accepted interval renderer.

The principal chart shows 365 daily means. Hourly observations remain the
energy/statistics authority; the eight-hour chart is a separate diagnostic.
No network, optimization model or solver is constructed by this adapter.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..common import ROOT, sha256_file
from .canonical_results import reporting_config
from ..visualization import dispatch_presentation as presentation

FOLDER = 'DISPATCH_PRESENTATION'
RECEIPT = 'DISPATCH_PRESENTATION_RECEIPT.json'
METADATA = 'FIGURE_METADATA.json'
VERSION = 'MEM_CASE_BOUND_DISPATCH_PRESENTATION_V1'
SOURCE_RECEIPT = 'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json'
DISPATCH_SOURCE = 'VIS_X1/data/canonical_uc_dispatch_by_zone.parquet'
EXCHANGE_SOURCE = 'CANONICAL/statistics/uc2_canonical_external_net_imports_hourly_MW.parquet'
DURATION_SOURCE = 'VIS_X1/data/fixed_commitment_lp_price_duration.parquet'
AUTHORITY_SOURCE = 'uc2_VIS_RESULT_AUTHORITY_REGISTER.csv'
RAW_FILE = 'annual_dispatch_raw_MW.csv'
FIGURES = (
    ('annual_dispatch_chronological_daily', 'Annual chronological dispatch — daily means',
     'PRIMARY_ANNUAL_PRESENTATION', True, 'annual_dispatch_chronological_daily_MW.csv'),
    ('annual_dispatch_chronological_1095', 'Annual chronological dispatch — eight-hour diagnostic',
     'EIGHT_HOUR_DIAGNOSTIC', False, 'annual_dispatch_time_bins_8h_MW.csv'),
)
META_COLUMNS = ('rigid_end_use_demand_MW', 'p2x_electrical_consumption_MW',
    'gross_end_use_demand_MW', 'bess_charging_MW', 'phs_charging_MW',
    'storage_phs_charging_MW', 'net_imports_MW', 'load_shedding_MW')
DAILY_GROUPS = {**presentation.DAILY_GROUPS,
    'Bioenergy': (*presentation.DAILY_GROUPS['Bioenergy'], 'Bioenergy', 'Bioenergy + CCS'),
    'Gas': (*presentation.DAILY_GROUPS['Gas'], 'Gas - OCGT', 'Gas - steam thermal',
            'Gas - other thermal', 'Gas + CCS'),
    'Nuclear': ('Nuclear',), 'Hydrogen - OCGT': ('Hydrogen - OCGT',),
    'Load shedding (nonphysical)': ('Load shedding (nonphysical)',)}
DAILY_POSITIVE = ('Geothermal', 'Nuclear', 'Bioenergy', 'Gas', 'Hydrogen - OCGT',
    *presentation.DAILY_POSITIVE_ORDER[3:], 'Load shedding (nonphysical)')
DAILY_NEGATIVE = presentation.DAILY_NEGATIVE_ORDER
DETAILED_POSITIVE = ('Geothermal', 'Nuclear', 'Bioenergy', 'Bioenergy + CCS',
    *presentation.POSITIVE_PRESENTATION_ORDER[1:4], 'Gas + CCS', 'Gas - OCGT',
    'Gas - steam thermal', 'Gas - other thermal', 'Hydrogen - OCGT',
    *presentation.POSITIVE_PRESENTATION_ORDER[4:], 'Load shedding (nonphysical)')


def dispatch_figure_declarations(year, scenario):
    """Single producer-owned declaration source for consolidated registries."""
    return [dict(figure_id=identifier, title=title, family='operation', role=role,
        required=required, year=int(year), scenario=scenario,
        producer='mem_model.reporting.dispatch_figures.produce_dispatch_figures',
        png=f'{FOLDER}/{identifier}.png', svg=f'{FOLDER}/{identifier}.svg',
        underlying_data=[f'{FOLDER}/{data}', f'{FOLDER}/{RAW_FILE}'],
        source_files=[SOURCE_RECEIPT, AUTHORITY_SOURCE, DISPATCH_SOURCE, EXCHANGE_SOURCE, DURATION_SOURCE],
        limitation='Presentation means only; annual energy, extrema and statistics retain all 8,760 raw observations. '
                   'Imports/exports are split at each raw snapshot; shedding is nonphysical and distinct.')
        for identifier, title, role, required, data in FIGURES]


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2), encoding='utf-8')


def _source_path(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT/path


def _source_digest(report, receipt, path):
    relative = path.relative_to(report).as_posix()
    pins = {}
    for field in ('original_artifact_hashes', 'artifact_hashes'):
        for name, digest in receipt.get(field, {}).items():
            key = name.replace('\\', '/')
            if key in pins and pins[key] != digest:
                raise ValueError('DISPATCH_SOURCE_HASH_CONFLICT: '+key)
            pins[key] = digest
    if relative not in pins or not path.is_file() or sha256_file(path) != pins[relative]:
        raise ValueError('DISPATCH_SOURCE_UNBOUND_OR_CHANGED: '+relative)
    return pins[relative]


def _sources(report, year, scenario):
    receipt_path = report/SOURCE_RECEIPT
    receipt = _read(receipt_path)
    if (receipt.get('status') != 'PASS' or receipt.get('year') != year or
            receipt.get('scenario') != scenario or receipt.get('physical_source') != 'UC_MILP' or
            receipt.get('reporting_solver_invocations') != 0):
        raise ValueError('DISPATCH_REQUIRES_VERIFIED_CASE_BOUND_UC_REPORT')
    status = receipt.get('result_status')
    if status not in ('CURRENT_ACCEPTED_RESULT', 'VERIFIED_RESULT_PENDING_ANALYTICAL_REVIEW'):
        raise ValueError('DISPATCH_UNSUPPORTED_RESULT_STATUS')
    sources = {str(receipt_path): sha256_file(receipt_path)}
    for name in (AUTHORITY_SOURCE, DISPATCH_SOURCE, EXCHANGE_SOURCE):
        sources[str(report/name)] = _source_digest(report, receipt, report/name)
    authority = pd.read_csv(report/AUTHORITY_SOURCE)
    selected = authority.loc[authority.current_role.eq('CANONICAL_PHYSICAL')]
    if (len(selected) != 1 or not selected.year.eq(year).all() or not selected.scenario.eq(scenario).all() or
            not selected.result_id.str.endswith('_UC_MILP').all() or
            not selected.solved_or_unsolved.eq('SOLVED').all() or
            not selected.UC_status.eq('VERIFIED_UC2').all() or not selected.visualization_allowed.eq(True).all()):
        raise ValueError('DISPATCH_UC_AUTHORITY_CONFLICT')
    row = selected.iloc[0]
    protected = {str(_source_path(name).resolve()): digest for name, digest in receipt.get('protected_source_hashes', {}).items()}
    if protected.get(str(_source_path(row.network_path).resolve())) != row.network_sha256:
        raise ValueError('DISPATCH_UC_SOLVED_SOURCE_CONFLICT')
    uc_receipt = _source_path(row.receipt)
    verification = _source_path(row.manifest)
    for path in (uc_receipt, verification):
        digest = sha256_file(path)
        if protected.get(str(path.resolve())) != digest:
            raise ValueError('DISPATCH_UC_RECEIPT_SOURCE_CONFLICT')
        sources[str(path)] = digest
        value = _read(path)
        if (value.get('status') != 'PASS' or value.get('year') != year or value.get('scenario') != scenario or
                value.get('job_id') != row.result_id or value.get('solved_sha256') != row.network_sha256):
            raise ValueError('DISPATCH_UC_RECEIPT_LINEAGE_CONFLICT')
    return receipt, sources, row.network_sha256


def _raw_dispatch(report, cfg):
    operation = pd.read_parquet(report/DISPATCH_SOURCE)
    if not {'snapshot', 'zone', *META_COLUMNS}.issubset(operation):
        raise ValueError('DISPATCH_CANONICAL_COLUMNS_MISSING')
    raw = operation.loc[operation.zone.eq('NATIONAL')].set_index('snapshot').drop(columns='zone')
    raw.index = pd.DatetimeIndex(raw.index, name='snapshot')
    expected = pd.date_range('2019-01-01', '2020-01-01', freq='h', inclusive='left', name='snapshot')
    if not raw.index.equals(expected):
        raise ValueError('DISPATCH_REQUIRES_COMPLETE_SAVED_2019_HOURLY_CHRONOLOGY')
    technologies = [name for name in raw if name not in META_COLUMNS]
    if not set(technologies).issubset(cfg['technology_colors']):
        raise ValueError('DISPATCH_UNSUPPORTED_TECHNOLOGY_OR_CARRIER')
    if not np.isfinite(raw.to_numpy(dtype=float)).all():
        raise ValueError('DISPATCH_NONFINITE_CANONICAL_DATA')
    exchange = pd.read_parquet(report/EXCHANGE_SOURCE)
    if (not exchange.index.equals(raw.index) or set(exchange) != set(cfg['zone_order']) or
            not np.isfinite(exchange.to_numpy(dtype=float)).all()):
        raise ValueError('DISPATCH_EXTERNAL_EXCHANGE_CHRONOLOGY_OR_ZONE_CONFLICT')
    signed = exchange.sum(axis=1)
    if not np.allclose(signed, raw.net_imports_MW, atol=presentation.BALANCE_ATOL_MW, rtol=0):
        raise ValueError('DISPATCH_NATIONAL_EXTERNAL_EXCHANGE_CONFLICT')
    result = raw[technologies].copy()
    result['Net imports'], result['Net exports'] = signed.clip(lower=0), signed.clip(upper=0)
    for source, label in (('bess_charging_MW', 'BESS charging'), ('phs_charging_MW', 'PHS charging'),
                          ('p2x_electrical_consumption_MW', 'P2X withdrawal')):
        result[label] = -raw[source]
    result['Load shedding (nonphysical)'] = raw.load_shedding_MW
    result['Rigid load'] = raw.rigid_end_use_demand_MW
    group = _daily_groups(result)  # Raw signs and complete mapping checked before averaging.
    residual = result.drop(columns='Rigid load').sum(axis=1)-result['Rigid load']
    if residual.abs().max() > presentation.BALANCE_ATOL_MW:
        raise ValueError('DISPATCH_HOURLY_ELECTRICAL_BALANCE_CONFLICT')
    return result, group


def _daily_groups(raw):
    assigned = [source for sources in DAILY_GROUPS.values() for source in sources]
    if len(assigned) != len(set(assigned)) or set(raw)-set(assigned):
        raise ValueError('DISPATCH_UNSUPPORTED_TECHNOLOGY_OR_CARRIER')
    negative = set(presentation.NEGATIVE_PRESENTATION_ORDER)
    for name in raw:
        if (raw[name].gt(0).any() if name in negative else raw[name].lt(0).any()):
            raise ValueError('DISPATCH_RAW_SIGN_CONFLICT: '+name)
    return pd.DataFrame({family: raw[[name for name in names if name in raw]].sum(axis=1)
                         for family, names in DAILY_GROUPS.items()}, index=raw.index)


def _duration(report, receipt, index, uc_network):
    if uc_network is not None:
        meta = uc_network.meta
        if (meta.get('uc2_year'), meta.get('uc2_scenario'), meta.get('uc2_kind')) != (
                receipt['year'], receipt['scenario'], 'UC_MILP') or not uc_network.snapshots.equals(index):
            raise ValueError('DISPATCH_DURATION_UC_NETWORK_CASE_CONFLICT')
        weights = uc_network.snapshot_weightings
        if not {'generators', 'objective'}.issubset(weights) or not weights.index.equals(index):
            raise ValueError('DISPATCH_DURATION_WEIGHTS_MISSING')
        duration = weights.generators.astype(float)
        if not np.array_equal(duration, weights.objective.astype(float)):
            raise ValueError('DISPATCH_COMPONENT_WEIGHTS_REQUIRE_SEPARATE_REVIEW')
        provenance = dict(kind='PROVIDED_VERIFIED_UC_NETWORK', case_metadata={
            key: meta[key] for key in ('uc2_year', 'uc2_scenario', 'uc2_kind')},
                          generator_and_objective_weights_equal=True)
        pins = {}
    else:
        path = report/DURATION_SOURCE
        digest = _source_digest(report, receipt, path)
        frame = pd.read_parquet(path, columns=['market', 'snapshot', 'weight'])
        cfg = reporting_config()
        expected_zones = {'CNORD' if zone == 'CNOR' else zone for zone in cfg['zone_order']}
        if set(frame.market) != expected_zones:
            raise ValueError('DISPATCH_DURATION_SEVEN_UC_MARKETS_REQUIRED')
        values = []
        for _, market in frame.groupby('market'):
            market = market.set_index('snapshot').sort_index()
            if not pd.DatetimeIndex(market.index).equals(index):
                raise ValueError('DISPATCH_DURATION_CHRONOLOGY_CONFLICT')
            values.append(pd.to_numeric(market.weight, errors='raise'))
        if any(not np.array_equal(values[0].to_numpy(), value.to_numpy()) for value in values[1:]):
            raise ValueError('DISPATCH_DURATION_MARKET_WEIGHTS_CONFLICT')
        duration = values[0].set_axis(index)
        provenance = dict(kind='CASE_BOUND_VIS_UC_GENERATOR_WEIGHTS', path=DURATION_SOURCE,
                          source='uc2_vis_x1.render_uc2_vis UC generator snapshot-weight export')
        pins = {str(path): digest}
    if not np.isfinite(duration).all() or not duration.eq(1).all():
        raise ValueError('DISPATCH_REQUIRES_RECORDED_ONE_HOUR_PHYSICAL_INTERVALS')
    return duration, provenance, pins


def _colors(cfg):
    colors = {name: {'color': color} for name, color in cfg['technology_colors'].items()}
    colors.update({'Net imports': {'color': '#5A7D9A'}, 'Net exports': {'color': '#5A7D9A'},
        'BESS charging': colors['BESS'], 'PHS charging': colors['PHS'],
        'P2X withdrawal': {'color': '#42B7B0'}, 'Load shedding (nonphysical)': {'color': '#9AA1A9'},
        'Rigid load': {'color': '#222222'}})
    grouped = {family: colors[names[0]] for family, names in DAILY_GROUPS.items()}
    grouped['Bioenergy'] = colors['Bioenergy']
    grouped['Solar PV'] = colors['Solar PV - utility']
    return colors, grouped


def _code_hashes():
    return {str(path): sha256_file(path) for path in (Path(__file__), Path(presentation.__file__),
        ROOT/'config/stage_b_reporting.yaml', ROOT/'config/vis_x1.yaml')}


def validate_dispatch_figures(report):
    """Read-only source/artifact validation; optional diagnostics do not gate daily availability."""
    report = Path(report).resolve()
    output = report/FOLDER
    if not (output/RECEIPT).is_file() or not (output/METADATA).is_file():
        return dict(status='WAITING_FOR_DISPATCH_PRESENTATION', figure_count=0)
    value, metadata = _read(output/RECEIPT), _read(output/METADATA)
    if value.get('status') != 'PASS_DISPATCH_PRESENTATION' or value.get('version') != VERSION:
        raise ValueError('DISPATCH_PRESENTATION_UNVALIDATED_RECEIPT')
    declarations = {item['figure_id']: item for item in dispatch_figure_declarations(value['year'], value['scenario'])}
    entries = metadata.get('figures', [])
    identifiers = [item.get('figure_id') for item in entries]
    if (len(identifiers) != len(set(identifiers)) or not set(identifiers).issubset(declarations) or
            any(item['required'] and identifier not in identifiers for identifier, item in declarations.items()) or
            metadata.get('figure_count') != len(entries) or value.get('raw_snapshots') != 8760 or
            value.get('daily_intervals') != 365 or value.get('physical_source') != 'UC_MILP'):
        raise ValueError('DISPATCH_PRESENTATION_DECLARATION_OR_COMPLETENESS_CONFLICT')
    for entry in entries:
        declaration = declarations[entry['figure_id']]
        if any(entry.get(key) != declaration[key] for key in ('title', 'family', 'role', 'png', 'svg', 'underlying_data')):
            raise ValueError('DISPATCH_PRESENTATION_DECLARATION_CONFLICT')
        if declaration['required'] and any(name not in value['artifact_hashes']
                for name in [declaration['png'], declaration['svg'], *declaration['underlying_data'], f'{FOLDER}/{METADATA}']):
            raise ValueError('DISPATCH_PRESENTATION_REQUIRED_ARTIFACT_UNPINNED')
    if value['code_hashes'] != _code_hashes():
        raise ValueError('DISPATCH_PRESENTATION_CODE_CHANGED_REVIEW_REQUIRED')
    for name, digest in value['source_hashes'].items():
        path = Path(name)
        if not path.is_file() or sha256_file(path) != digest:
            raise ValueError('DISPATCH_PRESENTATION_SOURCE_CHANGED: '+name)
    source_receipt, expected_sources, uc_sha = _sources(report, value['year'], value['scenario'])
    weight_kind = value.get('duration_provenance', {}).get('kind')
    if weight_kind == 'CASE_BOUND_VIS_UC_GENERATOR_WEIGHTS':
        path = report/DURATION_SOURCE
        expected_sources[str(path)] = _source_digest(report, source_receipt, path)
    elif weight_kind == 'PROVIDED_VERIFIED_UC_NETWORK':
        if value['duration_provenance'].get('case_metadata') != dict(
                uc2_year=value['year'], uc2_scenario=value['scenario'], uc2_kind='UC_MILP'):
            raise ValueError('DISPATCH_PRESENTATION_DURATION_CASE_CONFLICT')
    else:
        raise ValueError('DISPATCH_PRESENTATION_DURATION_SOURCE_MISSING')
    if (value['source_hashes'] != expected_sources or
            value.get('result_status') != source_receipt['result_status'] or
            value.get('source_UC_solved_sha256') != uc_sha or value.get('solver_invocations') != 0 or
            value.get('network_opens') != 0 or value.get('optimization_model_constructed') is not False or
            any(entry.get('status') != source_receipt['result_status'] or
                entry.get('physical_source') != 'UC_MILP' or entry.get('source_UC_solved_sha256') != uc_sha
                for entry in entries)):
        raise ValueError('DISPATCH_PRESENTATION_SOURCE_BINDING_INCOMPLETE_OR_CONFLICTING')
    optional = set(value.get('optional_artifacts', []))
    optional_declaration = next(item for item in declarations.values() if not item['required'])
    allowed_optional = {optional_declaration['png'], optional_declaration['svg'],
                        optional_declaration['underlying_data'][0]}
    if not optional.issubset(allowed_optional):
        raise ValueError('DISPATCH_PRESENTATION_REQUIRED_ARTIFACT_CANNOT_BE_OPTIONAL')
    diagnostic_status = 'PASS'
    for name, digest in value['artifact_hashes'].items():
        path = report/name
        valid = path.is_file() and sha256_file(path) == digest
        if not valid and name in optional:
            diagnostic_status = 'MISSING_OR_CHANGED_OPTIONAL_DIAGNOSTIC'
        elif not valid:
            raise ValueError('DISPATCH_PRESENTATION_ARTIFACT_CHANGED: '+name)
    if (metadata.get('year'), metadata.get('scenario')) != (value['year'], value['scenario']):
        raise ValueError('DISPATCH_PRESENTATION_METADATA_CASE_CONFLICT')
    return {**value, 'diagnostic_status': diagnostic_status, 'figure_count': metadata['figure_count']}


def produce_dispatch_figures(report, year, scenario, uc_network=None):
    """Produce new case-bound daily/8h figures; existing presentations require valid cache pins."""
    from .uc2_postprocess import no_solver_calls
    report = Path(report).resolve()
    if scenario not in reporting_config()['scenario_order']:
        raise ValueError('DISPATCH_UNSUPPORTED_SCENARIO')
    output = report/FOLDER
    if (output/RECEIPT).is_file():
        cached = validate_dispatch_figures(report)
        if (cached['year'], cached['scenario']) != (year, scenario):
            raise ValueError('DISPATCH_PRESENTATION_CACHE_CASE_CONFLICT')
        return cached
    required = [SOURCE_RECEIPT, AUTHORITY_SOURCE, DISPATCH_SOURCE, EXCHANGE_SOURCE]
    if uc_network is None:
        required.append(DURATION_SOURCE)
    missing = [name for name in required if not (report/name).is_file()]
    if missing:
        return dict(status='WAITING_FOR_CASE_BOUND_DISPATCH_SOURCES', year=year, scenario=scenario,
                    missing_sources=missing, solver_invocations=0, network_opens=0)
    if output.exists() and any(output.iterdir()):
        raise ValueError('DISPATCH_PRESENTATION_OVERWRITE_REQUIRES_REVIEW')
    with no_solver_calls() as guard:
        receipt, sources, uc_sha = _sources(report, year, scenario)
        cfg = reporting_config()
        if cfg['electrical_balance_identity'] != presentation.ACCEPTED_BALANCE_IDENTITY:
            raise ValueError('DISPATCH_BALANCE_METHOD_CHANGED_REVIEW_REQUIRED')
        raw, grouped_raw = _raw_dispatch(report, cfg)
        duration, weight_provenance, weight_pins = _duration(report, receipt, raw.index, uc_network)
        sources.update(weight_pins)
        detailed, daily_bounds, exposure, allocation = presentation.daily_time_bins(raw, duration)
        daily = _daily_groups(detailed)
        eight, eight_bounds, eight_exposure = presentation.eight_hour_time_bins(raw, duration)
        energies = raw.mul(duration, axis=0).sum()
        daily_energy, eight_energy = (detailed*exposure).sum(), (eight*eight_exposure).sum()
        family_energy = grouped_raw.mul(duration, axis=0).sum()
        recovered_family_energy = daily.mul(daily_bounds.duration_hours, axis=0).sum()
        for recovered, expected in ((daily_energy, energies), (eight_energy, energies),
                                    (recovered_family_energy, family_energy)):
            if not np.allclose(recovered, expected, atol=presentation.DISPLAY_ENERGY_ATOL_MWH,
                               rtol=presentation.DISPLAY_ENERGY_RTOL):
                raise ValueError('DISPATCH_PRESENTATION_ENERGY_CONSERVATION_FAIL')
        colors, daily_colors = _colors(cfg)
        order = [name for name in DETAILED_POSITIVE if name in raw]
        order += list(presentation.NEGATIVE_PRESENTATION_ORDER)
        if len(order) != len(set(order)) or set(order) != set(raw)-{'Rigid load'}:
            raise ValueError('DISPATCH_RENDERER_POPULATION_INCOMPLETE')
        common = dict(year=year, scenario=scenario, font_family=cfg['font_preference'][0],
                      result_label='Verified physical UC result')
        figures = [presentation.plot_daily_presentation(daily, daily_bounds, daily_colors,
            presentation_order=DAILY_POSITIVE+DAILY_NEGATIVE, negative_order=DAILY_NEGATIVE, **common),
            presentation.plot_eight_hour_presentation(eight, eight_bounds, colors,
            presentation_order=order, negative_order=presentation.NEGATIVE_PRESENTATION_ORDER, **common)]
        import matplotlib
        import matplotlib.pyplot as plt
        output.mkdir(parents=True)
        raw.assign(interval_hours=duration).to_csv(output/RAW_FILE, index_label='snapshot')
        presentation.raw_power_statistics(raw, pd.DataFrame({name: duration for name in raw}, index=raw.index)).to_csv(output/'annual_statistics_from_raw.csv')
        daily.join(daily_bounds).to_csv(output/FIGURES[0][4], index_label='bin_start')
        eight.join(eight_bounds).to_csv(output/FIGURES[1][4], index_label='bin_start')
        allocation.to_csv(output/'annual_dispatch_daily_allocation_QA.csv', index_label='snapshot')
        pd.DataFrame({'raw_energy_MWh': energies, 'daily_energy_MWh': daily_energy,
            'eight_hour_energy_MWh': eight_energy}).to_csv(output/'annual_dispatch_energy_QA.csv', index_label='series')
        declarations = dispatch_figure_declarations(year, scenario)
        for declaration, (figure, font) in zip(declarations, figures):
            for extension in ('png', 'svg'):
                with matplotlib.rc_context({'svg.hashsalt': 'MEM_CHRONOLOGICAL_DISPATCH_DAILY_R3', 'svg.fonttype': 'none'}):
                    figure.savefig(report/declaration[extension], dpi=240, bbox_inches='tight', facecolor='white',
                        metadata={'Date': None} if extension == 'svg' else {'Software': 'MEM VIS-X1'})
            plt.close(figure)
            declaration.update(status=receipt['result_status'], physical_source='UC_MILP',
                               source_UC_solved_sha256=uc_sha, font_resolved=font)
        _write(output/METADATA, dict(year=year, scenario=scenario, figures=declarations, figure_count=len(declarations)))
        optional = [declarations[1][key] for key in ('png', 'svg')]+[f'{FOLDER}/{FIGURES[1][4]}']
        value = dict(status='PASS_DISPATCH_PRESENTATION', version=VERSION, year=year, scenario=scenario,
            physical_source='UC_MILP', result_status=receipt['result_status'], source_UC_solved_sha256=uc_sha,
            source_hashes=sources, code_hashes=_code_hashes(), raw_snapshots=len(raw), daily_intervals=len(daily),
            eight_hour_intervals=len(eight), presentation_groups=DAILY_GROUPS, semantic_colors=daily_colors,
            duration_provenance=weight_provenance, hourly_balance_max_abs_MW=float((raw.drop(columns='Rigid load').sum(axis=1)-raw['Rigid load']).abs().max()),
            daily_energy_max_error_MWh=float((daily_energy-energies).abs().max()),
            family_energy_max_error_MWh=float((recovered_family_energy-family_energy).abs().max()),
            eight_hour_energy_max_error_MWh=float((eight_energy-energies).abs().max()),
            annual_statistics_authority='RAW_8760_OBSERVATIONS', optional_artifacts=optional,
            solver_invocations=guard['solver_invocations'], network_opens=0, optimization_model_constructed=False,
            artifact_hashes={path.relative_to(report).as_posix(): sha256_file(path) for path in output.iterdir() if path.is_file()})
        _write(output/RECEIPT, value)
    return value

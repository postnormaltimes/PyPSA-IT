"""VIS-X1 comparisons of verified UC2 report extensions, without network loads.

UC physical statistics and fixed-commitment LP price statistics remain separate
sources. This module reads only report tables and never builds or solves models.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

from .vis_x1 import CONFIG, ROOT, _sha, _toolkit


# Selected catalog: FINAL_VISUALIZATION_INVENTORY.csv, presentation_set ==
# COMPARISON PRESENTATION (2026-10-02, including the approved regime addition).
# The 33 legacy renderer families remain distinct from these 18 selected views.
SELECTED_COMPARISON_VIEWS = (
    ('monthly_zonal_price', 'Prices', 'monthly_zonal_prices', 'FIXED_COMMITMENT_PRICE_LP_DUALS'),
    ('price_duration', 'Prices', 'price_duration', 'FIXED_COMMITMENT_PRICE_LP_DUALS'),
    ('interface_net_energy_MWh', 'Exchanges', 'net_interface_summary', 'UC_MILP'),
    ('primary_generation', 'Generation', 'comparison_metrics', 'UC_MILP'),
    ('net_imports', 'Exchanges', 'comparison_metrics', 'UC_MILP'),
    ('annual_load_weighted_zonal_prices', 'Prices', 'comparison_metrics', 'FIXED_COMMITMENT_PRICE_LP_DUALS'),
    ('storage_activity', 'Storage', 'comparison_metrics', 'UC_MILP'),
    ('p2x_electrical_consumption', 'P2X', 'comparison_metrics', 'UC_MILP'),
    ('vre_curtailment', 'Generation', 'comparison_metrics', 'UC_MILP'),
    ('actual_starts', 'Commitment', 'comparison_metrics', 'UC_MILP'),
    ('all_italy_mean_spatial_spread', 'Prices', 'comparison_metrics', 'FIXED_COMMITMENT_PRICE_LP_DUALS'),
    ('average_committed_capacity', 'Commitment', None, 'UC_MILP'),
    ('italian_zonal_spread_duration', 'Prices', 'all_italy_spatial_spread', 'FIXED_COMMITMENT_PRICE_LP_DUALS'),
    ('distinct_simultaneous_prices', 'Prices', 'distinct_simultaneous_prices', 'FIXED_COMMITMENT_PRICE_LP_DUALS'),
    ('monthly_national_load_weighted_price', 'Prices', 'monthly_national_load_weighted_price', 'FIXED_COMMITMENT_PRICE_LP_DUALS'),
    ('interface_positive_limit_hours', 'Exchanges', 'net_interface_summary', 'UC_MILP'),
    ('interface_negative_limit_hours', 'Exchanges', 'net_interface_summary', 'UC_MILP'),
    ('marginal_regime_national_comparison', 'Marginal regime', None, 'FIXED_COMMITMENT_PRICE_LP_DUALS'),
)


def selected_comparison_view_records(year: int, case_reports: dict[str, Path], output: Path, *,
                                     expected_scenarios: list[str] | None = None,
                                     model_version: str | None = None) -> list[dict]:
    """Local register inputs for the accepted views; source readiness is not rendering QA.

    Ordinary within-year comparisons support verified subsets of at least two.
    The approved marginal-regime producer requires the complete scenario set.
    Cross-year comparisons are outside this selected catalog.
    """
    scenarios = list(case_reports)
    expected = list(expected_scenarios or scenarios)
    missing = [scenario for scenario in expected if scenario not in case_reports]
    records = []
    for view, group, table, authority in SELECTED_COMPARISON_VIEWS:
        if table is not None:
            required = [Path(output) / 'data' / f'{table}.csv']
            if view == 'italian_zonal_spread_duration' and str(model_version).startswith('FINAL_METHODOLOGY_'):
                required += [Path(report) / 'VIS_X1/data/fixed_commitment_lp_price_duration.parquet'
                             for report in case_reports.values()]
        elif view == 'average_committed_capacity':
            required = [Path(report) / 'hourly_online_units_and_committed_MW.csv'
                        for report in case_reports.values()]
        else:
            required = [Path(output) / 'marginal_regime' /
                        f'LEXICOGRAPHIC_REGIME_NATIONAL_SHARES_{year}.csv']
        if len(scenarios) < 2:
            status = 'WAITING_FOR_COMPARABLE_REPORTS'
        elif view == 'marginal_regime_national_comparison' and (missing or len(scenarios) < 3):
            status = 'WAITING_FOR_COMPLETE_SCENARIO_SET'
        elif not all(path.is_file() for path in required):
            status = 'WAITING_FOR_SOURCE_TABLES'
        else:
            status = 'SOURCE_TABLES_AVAILABLE'
        records.append(dict(view_id=view, group=group, family=view, year=year,
                            model_version=model_version,
                            scenarios=scenarios, expected_scenarios=expected,
                            missing_scenarios=missing, source_authority=authority,
                            source_files=[str(path) for path in required if path.is_file()],
                            missing_source_files=[str(path) for path in required if not path.is_file()],
                            status=status, rendering_status='NOT_ASSESSED'))
    return records


def _final_case_duration_source(report: Path, receipt: dict, index: pd.DatetimeIndex):
    """Read stored UC generator weights from the case-bound VIS price-duration table.

    uc2_vis_x1 passes canonical._weights(UC network, 'generator') to the price
    duration exporter. Only its timestamps and weights are used here, not prices.
    """
    path = report / 'VIS_X1/data/fixed_commitment_lp_price_duration.parquet'
    digest = _verified_source_digest(report, receipt, path)
    frame = pd.read_parquet(path, columns=['market', 'snapshot', 'weight'])
    if frame.market.nunique() != 7:
        raise ValueError('Seven stored UC duration-weight series required')
    for _, values in frame.groupby('market'):
        timestamps = pd.DatetimeIndex(pd.to_datetime(values.snapshot, errors='raise')).sort_values()
        if (not timestamps.equals(index) or
                not pd.to_numeric(values.weight, errors='raise').eq(1).all()):
            raise ValueError('Rank hours require stored equal-duration UC weights and aligned chronology')
    return path, digest


def _comparison_style(metadata, PlotStyle):
    """Reuse accepted technology/zone colours and stable scenario colours."""
    from ..reporting.canonical_results import reporting_config
    import matplotlib as mpl
    report_cfg = reporting_config()
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    _toolkit(cfg)
    seal = ROOT / Path(cfg['external_reference'])
    manifest = json.loads((seal/'TRANSFER_MANIFEST.json').read_text(encoding='utf-8'))
    palette_source = 'adapters/pypsa_it_adapter.py'
    if _sha(seal/palette_source) != manifest['adapter_file_hashes'][palette_source]:
        raise ValueError('Accepted zone palette source changed')
    from adapters.pypsa_it_adapter import RECOVERED_R2B_ZONE_COLORS
    colours = {key: {'color': value} for key,value in report_cfg['technology_colors'].items()}
    colours.update({cfg['display_market_aliases'][key]: {'color': value}
                    for key,value in RECOVERED_R2B_ZONE_COLORS.items()})
    style = PlotStyle(carrier_style=colours)
    order = list(report_cfg["scenario_order"])
    order += sorted(set(metadata.scenario_id).difference(order))
    palette = [mpl.colors.to_hex(color) for color in mpl.colormaps["tab20"].colors]
    used = set()
    for scenario in order:
        color = style.scenario_color(scenario)
        if color in used:
            color = next(value for value in palette if value not in used)
        used.add(color)
        style.scenario_style[scenario] = {"color": color}
    return style


def _base_comparison_artifacts(output, *, include_receipts=False):
    """Only the original producer owns data/figures and its root metadata.

    Presentation and marginal-regime producers own their own manifests; their
    later legitimate refreshes must not invalidate the base comparison cache.
    """
    output = Path(output)
    files = [path for folder in ('data', 'figures') for path in (output / folder).rglob('*')
             if path.is_file()]
    names = ['MEM_UC2_COMPARISON_FIGURE_METADATA.json']
    if include_receipts:
        names += ['MEM_UC2_COMPARISON_MANIFEST.csv', 'MEM_UC2_COMPARISON_RECEIPT.json']
    files += [output / name for name in names if (output / name).is_file()]
    return sorted(files)


def _refined_presentation_artifact_hashes(entries, metadata_path):
    """Bind only this producer's declared renders and metadata."""
    paths = [Path(name) for entry in entries for name in entry['files']]
    paths.append(Path(metadata_path))
    return {path.name: _sha(path) for path in paths}


def refine_cached_comparison_presentation(comparison_dir: Path, output: Path, *,
                                         finalize_selection=False, case_reports=None) -> dict:
    """Render the established selected views from verified comparison tables.

    Correct only generation/import category colours; consolidate seven annual
    price bars using the existing grouped-horizontal-bar comparison geometry.
    No canonical statistics are recomputed and no networks are opened.
    """
    if finalize_selection:
        return _finalize_selected_comparisons(Path(comparison_dir), Path(output), case_reports)
    from ..reporting.uc2_postprocess import no_solver_calls
    comparison_dir, output = Path(comparison_dir), Path(output)
    parent_path = comparison_dir/'MEM_UC2_COMPARISON_RECEIPT.json'
    parent = json.loads(parent_path.read_text(encoding='utf-8'))
    result_status = parent.get('result_status', 'CURRENT_ACCEPTED_RESULT')
    if not (parent['status'] == 'PASS' and parent['reporting_solver_invocations'] == 0
            and parent['model_generation'] == 'UC2' and parent['physical_source'] == 'UC_MILP'
            and parent['price_source'] == 'FIXED_COMMITMENT_PRICE_LP'):
        raise ValueError('Verified cached UC2 comparison required')
    manifest_path = comparison_dir/'MEM_UC2_COMPARISON_MANIFEST.csv'
    if _sha(manifest_path) != parent['manifest_sha256']:
        raise ValueError('Cached comparison manifest changed')
    registered = pd.read_csv(manifest_path).set_index('artifact').sha256
    names = ('comparison_metrics', 'scenario_metadata')
    paths = [comparison_dir/f'data/{name}.csv' for name in names]
    if any(_sha(path) != registered[path.relative_to(comparison_dir).as_posix()] for path in paths):
        raise ValueError('Cached comparison input changed')
    original_hashes = {str(p):_sha(p) for p in _base_comparison_artifacts(comparison_dir, include_receipts=True)}
    cfg = yaml.safe_load(CONFIG.read_text(encoding='utf-8')); _toolkit(cfg)
    from visualization_toolkit import scenario_comparison as comparison
    from visualization_toolkit.styles import PlotStyle, style_context
    output.mkdir(parents=True, exist_ok=True)
    with no_solver_calls() as guard:
        metrics, metadata = (pd.read_csv(path) for path in paths)
        order = metadata.scenario_id.tolist()
        if order != parent['scenarios'] or not metadata.year.eq(parent['year']).all():
            raise ValueError('Comparison year/scenario lineage mismatch')
        if 'result_status' in metadata and _comparison_result_status(metadata) != result_status:
            raise ValueError('Cached comparison result status differs from source metadata')
        year = int(metadata.year.iloc[0]); style = _comparison_style(metadata, PlotStyle)
        prepared = comparison.prepare_scenario_metrics(metrics, metadata)
        entries = []
        def save(fig, name, question, checked_values):
            fig.text(.01, .01, f'{result_status} | UC2 {year} | physical: UC MILP; prices: fixed-commitment LP duals', fontsize=8,color='#555555')
            fig.subplots_adjust(bottom=max(fig.subplotpars.bottom,.14))
            files = []
            for extension in ('png','svg'):
                path = output/f'{name}.{extension}'
                with plt.rc_context({'svg.hashsalt':'MEM_UC2_COMPARISON_PRESENTATION_FINAL'}):
                    fig.savefig(path,dpi=240,bbox_inches='tight',facecolor='white',metadata={'Date':None} if extension=='svg' else None)
                files.append(str(path));
            plt.close(fig)
            entries.append(dict(figure=name,year=year,scenarios=order,status=result_status,
                role='COMPARISON_PRESENTATION',question=question,files=files,
                underlying_data=[str(path) for path in paths],physical_source='UC_MILP',
                price_source='FIXED_COMMITMENT_LP_DUALS',checked_values=checked_values))
        for metric,category,title in (('primary_generation','carrier','Annual primary generation'),
                                       ('net_imports','market','Annual signed zonal net imports')):
            subset = metrics.loc[metrics.metric.eq(metric)]
            if not subset.source_authority.eq('UC_MILP').all() or not subset.unit.eq('TWh').all():
                raise ValueError(f'Canonical UC physical authority/units required: {metric}')
            categories = set(subset[category])
            if not categories.issubset(style.carrier_style):
                raise ValueError(f'Unmapped presentation colours: {categories-set(style.carrier_style)}')
            pivot = subset.pivot(index='scenario_id',columns=category,values='value').reindex(order).fillna(0)
            fig = comparison.plot_scenario_comparison(prepared,metric,category=category,
                aggregation='sum',scenario_order=order,style=style,missing_category_as_zero=True)
            ax=fig.axes[0]; ax.set_title(f'{title} | UC2 {year}')
            ax.get_legend().set_title('Technology' if category == 'carrier' else 'Italian zone')
            np.testing.assert_allclose([bar.get_height() for bar in ax.patches],pivot.to_numpy().T.ravel(),atol=1e-10,rtol=0)
            save(fig,metric,title,len(ax.patches))
        values = metrics.loc[metrics.metric.eq('annual_load_weighted_zonal_price')]
        zones = list(cfg['display_market_aliases'].values())
        pivot = values.pivot(index='market',columns='scenario_id',values='value').reindex(index=zones,columns=order)
        if (pivot.isna().any().any() or not values.unit.eq('EUR/MWh').all()
                or not values.source_authority.eq('FIXED_COMMITMENT_PRICE_LP_DUALS').all()):
            raise ValueError('Seven zonal price values per scenario required')
        with style_context(style):
            fig,ax=plt.subplots(figsize=(10,6))
            pivot.plot.barh(ax=ax,color=[style.scenario_color(s) for s in order],width=.8)
            ax.invert_yaxis(); ax.set(xlabel='Annual load-weighted zonal price [EUR/MWh]',ylabel='Italian zone',title=f'Annual zonal prices | UC2 {year}')
            ax.legend(title='Scenario',frameon=False,loc='upper left',bbox_to_anchor=(1.01,1))
            ax.grid(axis='x',color='#E6E8EB',linewidth=.6); ax.set_axisbelow(True)
            ax.spines[['top','right']].set_visible(False);fig.tight_layout()
            np.testing.assert_allclose([bar.get_width() for bar in ax.patches],pivot.to_numpy().T.ravel(),atol=1e-10,rtol=0)
            save(fig,'annual_load_weighted_zonal_prices','Annual zone-by-scenario price contrast; replaces seven separate annual-price bars',len(ax.patches))
    if any(_sha(Path(p)) != digest for p,digest in original_hashes.items()):
        raise ValueError('Original comparison artifacts changed')
    (output/'FIGURE_METADATA.json').write_text(json.dumps(entries,indent=2),encoding='utf-8')
    receipt=dict(status='PASS',result_status=result_status,year=year,scenarios=order,solver_invocations=guard['solver_invocations'],
        network_open_or_extraction_calls=0,original_comparison_artifacts_unchanged=True,
        source_receipt_sha256=_sha(parent_path),input_hashes={str(p):_sha(p) for p in paths},
        figure_count=len(entries),plotted_values_checked=sum(entry['checked_values'] for entry in entries),
        semantic_colour_source='Accepted MEM technology_colors + sealed recovered GME zone palette',
        artifact_hashes=_refined_presentation_artifact_hashes(entries, output/'FIGURE_METADATA.json'))
    (output/'PRESENTATION_RECEIPT.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    return receipt


def _finalize_selected_comparisons(comparison_dir, output, case_reports):
    """Render the accepted comparison catalog without repeating the base views."""
    from ..common import ROOT
    from ..reporting.canonical_results import reporting_config
    from ..reporting.uc2_postprocess import no_solver_calls

    parent_path = comparison_dir / 'MEM_UC2_COMPARISON_RECEIPT.json'
    parent = json.loads(parent_path.read_text(encoding='utf-8'))
    if not (parent['status'] == 'PASS' and parent['reporting_solver_invocations'] == 0
            and parent['model_generation'] == 'UC2' and parent['physical_source'] == 'UC_MILP'
            and parent['price_source'] == 'FIXED_COMMITMENT_PRICE_LP'):
        raise ValueError('Verified UC2 comparison authority required')
    manifest = comparison_dir / 'MEM_UC2_COMPARISON_MANIFEST.csv'
    if _sha(manifest) != parent['manifest_sha256']:
        raise ValueError('Cached comparison manifest changed')
    registered = pd.read_csv(manifest).set_index('artifact').sha256
    original_hashes = {str(p): _sha(p) for p in _base_comparison_artifacts(comparison_dir, include_receipts=True)}
    source_hashes = {}

    def read_cached(name):
        path = comparison_dir / f'data/{name}.csv'
        digest = _sha(path)
        if digest != registered[path.relative_to(comparison_dir).as_posix()]:
            raise ValueError(f'Cached comparison source changed: {name}')
        source_hashes[str(path)] = digest
        return pd.read_csv(path)

    cfg = yaml.safe_load(CONFIG.read_text(encoding='utf-8'))
    _toolkit(cfg)
    from visualization_toolkit import scenario_comparison as comparison
    from visualization_toolkit.styles import PlotStyle, style_context

    year, order = parent['year'], parent['scenarios']
    metadata, receipts = _validate_sources(year, case_reports or {},
                                         expected_model_version=parent.get('model_version'))
    if metadata.scenario_id.tolist() != order:
        raise ValueError('Selected scenarios differ from accepted comparison order')
    if receipts != parent['source_receipt_sha256']:
        raise ValueError('Selected case receipts differ from cached comparison lineage')
    result_status = _comparison_result_status(metadata)
    if result_status != parent.get('result_status', 'CURRENT_ACCEPTED_RESULT'):
        raise ValueError('Selected result status differs from cached comparison lineage')
    output.mkdir(parents=True, exist_ok=True)
    entries, qa = [], []
    report_cfg = reporting_config()
    labels = dict(report_cfg['carrier_to_display_technology'])
    labels.update({name: name for name in ('BESS charging', 'BESS discharge', 'PHS charging', 'PHS discharge')})
    style = _comparison_style(metadata, PlotStyle)
    for key, label in labels.items():
        colour_key = {'BESS charging': 'BESS', 'BESS discharge': 'BESS',
                      'PHS charging': 'PHS', 'PHS discharge': 'PHS'}.get(key, label)
        style.carrier_style[key] = {'color': report_cfg['technology_colors'][colour_key]}

    def save(fig, name, question, sources, limitation=''):
        fig.text(.01, .01, f'{result_status} | UC2 {year} | physical: UC MILP; prices: fixed-commitment LP duals',
                 fontsize=8, color='#555555')
        fig.subplots_adjust(bottom=max(fig.subplotpars.bottom, .14))
        files = []
        for extension in ('png', 'svg'):
            path = output / f'{name}.{extension}'
            with plt.rc_context({'svg.hashsalt': 'MEM_UC2_FINAL_COMPARISON_SELECTION'}):
                fig.savefig(path, dpi=240, bbox_inches='tight', facecolor='white',
                            metadata={'Date': None} if extension == 'svg' else None)
            files.append(str(path))
        plt.close(fig)
        entries.append(dict(figure=name, year=year, scenarios=order, status=result_status,
                            role='COMPARISON_PRESENTATION', question=question, files=files,
                            underlying_data=[str(p) for p in sources], limitation=limitation,
                            physical_source='UC_MILP', price_source='FIXED_COMMITMENT_LP_DUALS'))

    def hourly_index(frame):
        index = pd.DatetimeIndex(pd.to_datetime(frame.snapshot, errors='raise'))
        expected = pd.date_range('2019-01-01', '2020-01-01', freq='h', inclusive='left')
        if not index.equals(expected):
            raise ValueError('Expected unchanged saved-2019 chronology: 8760 unique hourly observations')
        return index

    with no_solver_calls() as guard:
        metrics = read_cached('comparison_metrics')
        prepared = comparison.prepare_scenario_metrics(metrics, metadata)
        metric_path = comparison_dir / 'data/comparison_metrics.csv'
        for metric, category, title, unit in (
            ('storage_activity', 'carrier', 'Annual gross storage activity / throughput', 'TWh'),
            ('p2x_electrical_consumption', 'market', 'Annual P2X electricity consumption', 'TWh'),
            ('vre_curtailment', 'carrier', 'Annual VRE curtailed energy', 'TWh'),
            ('actual_starts', 'carrier', 'Annual generating-unit starts', 'starts'),
            ('all_italy_mean_spatial_spread', None, 'Mean simultaneous Italian zonal price spread', 'EUR/MWh'),
        ):
            values = metrics.loc[metrics.metric.eq(metric)]
            authority = 'FIXED_COMMITMENT_PRICE_LP_DUALS' if 'spatial_spread' in metric else 'UC_MILP'
            if not values.source_authority.eq(authority).all() or not values.unit.eq(unit).all():
                raise ValueError(f'Accepted authority/units required: {metric}')
            if category and not set(values[category]).issubset(style.carrier_style):
                raise ValueError(f'Unmapped semantic colour: {metric}')
            fig = comparison.plot_scenario_comparison(prepared, metric, category=category, aggregation='sum',
                scenario_order=order, style=style, missing_category_as_zero=True)
            ax = fig.axes[0]
            ax.set(title=f'{title} | {year}', ylabel=unit, xlabel='Scenario')
            ax.tick_params(axis='x', rotation=0)
            limitation = ''
            if category:
                pivot = values.pivot(index='scenario_id', columns=category, values='value').reindex(order).fillna(0)
                if metric == 'storage_activity':
                    for container in ax.containers:
                        if container.get_label() in ('BESS charging', 'PHS charging'):
                            for patch in container:
                                patch.set_hatch('//'); patch.set_edgecolor('white'); patch.set_linewidth(0)
                    ax.set_title(f'{title} | {year}\nCharging + discharge; not net electricity supply')
                    limitation = 'Gross grid-terminal charging plus discharge, both positive. Charging hatched; discharge solid. Accepted BESS/PHS classification unchanged.'
                handles, keys = ax.get_legend_handles_labels()
                ax.legend(handles, [labels[key] if category == 'carrier' else key for key in keys],
                          title='Technology' if category == 'carrier' else 'Zone', frameon=False,
                          loc='upper left', bbox_to_anchor=(1.01, 1))
                expected = pivot.to_numpy().T.ravel()
            else:
                expected = values.set_index('scenario_id').value.reindex(order).to_numpy()
            np.testing.assert_allclose([p.get_height() for p in ax.patches], expected, atol=1e-10, rtol=0)
            qa.append(dict(check=metric + '_cached_values_preserved', status='PASS', observations=len(expected)))
            fig.tight_layout()
            save(fig, metric, title, [metric_path], limitation)

        # Explicitly requested presentation derivative; the original capacity-hours table is untouched.
        averages, duration_sources = [], []
        for scenario in order:
            report = Path(case_reports[scenario])
            receipt_path = report / 'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json'
            receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
            path = report / 'hourly_online_units_and_committed_MW.csv'
            if _sha(path) != receipt['original_artifact_hashes'][path.name]:
                raise ValueError(f'Accepted hourly UC commitment changed: {scenario}')
            source_hashes[str(path)] = _sha(path)
            frame = pd.read_csv(path); index = hourly_index(frame)
            power = pd.to_numeric(frame.committed_MW, errors='raise')
            if not np.isfinite(power).all() or (power < 0).any():
                raise ValueError('Invalid canonical committed MW')
            averages.append(dict(scenario=scenario, year=year, raw_observations=len(frame),
                                 average_committed_capacity_GW=float(power.mean() / 1000),
                                 method='mean(hourly UC committed_MW) / 1000', source=str(path)))
            # Reuse verified physical durations solely to establish the ranked-hour axis.
            if str(metadata.set_index('scenario_id').loc[scenario, 'model_version']).startswith('FINAL_METHODOLOGY_'):
                duration_path, duration_digest = _final_case_duration_source(report, receipt, index)
                source_hashes[str(duration_path)] = duration_digest
                duration_sources.append(duration_path)
                continue
            raw_dir = ROOT / f'_local/outputs/visualization/UC2/{year}/{scenario}/dispatch_presentation'
            raw_path = raw_dir / 'annual_dispatch_raw_MW.csv'
            raw_receipt_path = raw_dir / 'PRESENTATION_RECEIPT.json'
            raw_receipt = json.loads(raw_receipt_path.read_text())
            if (raw_receipt['status'] != 'PASS' or raw_receipt['scenario'] != scenario
                    or raw_receipt['year'] != year or raw_receipt['solver_invocations'] != 0
                    or _sha(raw_path) != raw_receipt['artifact_hashes'][raw_path.name]):
                raise ValueError('Verified cached physical-duration source required')
            durations = pd.read_csv(raw_path, usecols=['snapshot', 'interval_hours'])
            if not hourly_index(durations).equals(index) or not durations.interval_hours.eq(1).all():
                raise ValueError('Rank hours require the accepted equal-duration 8760-hour chronology')
            source_hashes[str(raw_path)] = _sha(raw_path)
            source_hashes[str(raw_receipt_path)] = _sha(raw_receipt_path)
            duration_sources.append(raw_path)
        average = pd.DataFrame(averages)
        average_path = output / 'average_committed_capacity_GW.csv'
        average.to_csv(average_path, index=False, float_format='%.15g')
        with style_context(style):
            fig, ax = plt.subplots(figsize=(10, 5))
            ax.bar(order, average.average_committed_capacity_GW, color=[style.scenario_color(s) for s in order])
            ax.set(title=f'Average committed capacity | {year}', ylabel='GW', xlabel='Scenario')
            np.testing.assert_allclose([p.get_height() for p in ax.patches], average.average_committed_capacity_GW, atol=1e-12, rtol=0)
            fig.tight_layout()
        qa.append(dict(check='average_committed_capacity_hourly_mean', status='PASS',
                       observations=sum(row['raw_observations'] for row in averages)))
        save(fig, 'average_committed_capacity', 'Average UC committed electrical capacity',
             [average_path] + [Path(row['source']) for row in averages], 'Arithmetic mean of accepted hourly UC committed_MW / 1000; not installed capacity or generation.')

        spread = read_cached('all_italy_spatial_spread')
        ranks = []
        with style_context(style):
            fig, ax = plt.subplots(figsize=(10, 5))
            for scenario in order:
                frame = spread.loc[spread.scenario.eq(scenario)].copy()
                hourly_index(frame)
                values = pd.to_numeric(frame.spread_EUR_per_MWh, errors='raise')
                if not np.isfinite(values).all() or (values < 0).any():
                    raise ValueError('Invalid max-minus-min price spread')
                ranked = frame.sort_values('spread_EUR_per_MWh', ascending=False, kind='stable').copy()
                ranked['rank_hour'] = np.arange(1, 8761)
                ranked['represented_hours'] = 1.0
                ranked['year'] = year
                ax.stairs(ranked.spread_EUR_per_MWh, np.arange(8761), baseline=None,
                          label=scenario, color=style.scenario_color(scenario))
                np.testing.assert_array_equal(np.sort(ranked.spread_EUR_per_MWh), np.sort(values))
                qa.append(dict(check=scenario + '_spread_all_observations_and_extrema_preserved',
                               status='PASS', observations=len(ranked), zero_spread_hours=int(values.eq(0).sum()),
                               min_EUR_per_MWh=float(values.min()), max_EUR_per_MWh=float(values.max())))
                ranks.append(ranked[['scenario', 'year', 'snapshot', 'rank_hour', 'represented_hours', 'spread_EUR_per_MWh']])
            ax.set(title=f'Italian zonal price-spread duration curve | {year}', xlabel='Represented hours / rank hour',
                   ylabel='Hourly max Italian price − min Italian price [EUR/MWh]', xlim=(0, 8760))
            ax.set_ylim(bottom=0)
            ax.legend(title='Scenario', frameon=False); fig.tight_layout()
        ranked_path = output / 'italian_zonal_spread_duration.csv'
        pd.concat(ranks).to_csv(ranked_path, index=False, float_format='%.15g')
        save(fig, 'italian_zonal_spread_duration', 'Frequency and magnitude of simultaneous Italian zonal price separation',
             [ranked_path, comparison_dir / 'data/all_italy_spatial_spread.csv'] + duration_sources,
             '8760 equal physical hours per scenario; descending interval steps; all zeros and extrema retained. No clipping, filtering or interpolation.')

        distinct = read_cached('distinct_simultaneous_prices')
        pivot = distinct.pivot(index='distinct_zonal_prices', columns='scenario', values='hours').reindex(index=range(1, 8), columns=order).fillna(0)
        if not pivot.sum().eq(8760).all():
            raise ValueError('Distinct-price hour shares do not cover the year')
        with style_context(style):
            fig, ax = plt.subplots(figsize=(10, 5))
            pivot.plot.bar(ax=ax, color=[style.scenario_color(s) for s in order])
            ax.set(title=f'Distinct simultaneous Italian zonal prices | {year}', xlabel='Number of distinct zonal prices', ylabel='Hours')
            ax.tick_params(axis='x', rotation=0); ax.legend(title='Scenario', frameon=False); fig.tight_layout()
        np.testing.assert_allclose([p.get_height() for p in ax.patches], pivot.to_numpy().T.ravel(), atol=0, rtol=0)
        qa.append(dict(check='distinct_price_counts_preserved', status='PASS', observations=pivot.size))
        save(fig, 'distinct_simultaneous_prices', 'How often Italy has one or multiple simultaneous prices',
             [comparison_dir / 'data/distinct_simultaneous_prices.csv'], 'Exact numeric price equality; not tolerant near-coupling.')

        monthly = read_cached('monthly_national_load_weighted_price')
        with style_context(style):
            fig, ax = plt.subplots(figsize=(10, 5))
            for scenario in order:
                frame = monthly.loc[monthly.scenario.eq(scenario)].sort_values('month')
                if frame.month.tolist() != list(range(1, 13)):
                    raise ValueError('Exactly twelve existing monthly national prices required')
                ax.plot(frame.month, frame.load_weighted_mean_EUR_per_MWh,
                        label=scenario, color=style.scenario_color(scenario))
            ax.set(title=f'Monthly national load-weighted electricity price | {year}', xlabel='Month', ylabel='EUR/MWh',
                   xticks=range(1, 13), xticklabels=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'])
            ax.legend(title='Scenario', frameon=False); fig.tight_layout()
        qa.append(dict(check='monthly_prices_unchanged', status='PASS', observations=len(monthly)))
        save(fig, 'monthly_national_load_weighted_price', 'National load-weighted seasonal price profile',
             [comparison_dir / 'data/monthly_national_load_weighted_price.csv'],
             'Existing accepted national weighting (rigid load plus P2X) unchanged; no reaggregation.')

        interfaces = read_cached('net_interface_summary')
        for metric, title, arrow in (('positive_limit_hours', 'Positive-direction interface transfer-limit hours', '→'),
                                     ('negative_limit_hours', 'Negative-direction interface transfer-limit hours', '←')):
            pivot = interfaces.pivot(index='interface_id', columns='scenario', values=metric).reindex(columns=order)
            identity = interfaces[['interface_id', 'display_from', 'display_to']].drop_duplicates().set_index('interface_id')
            if len(pivot) != 20 or pivot.isna().any().any() or not identity.index.is_unique:
                raise ValueError('Exactly twenty consistent canonical interfaces required')
            with style_context(style):
                fig, ax = plt.subplots(figsize=(11, 8))
                pivot.plot.barh(ax=ax, color=[style.scenario_color(s) for s in order], width=.8)
                ax.set_yticklabels([f'{identity.loc[i].display_from} {arrow} {identity.loc[i].display_to}' for i in pivot.index])
                ax.set(title=f'{title} | {year}', xlabel='Hours', ylabel='Canonical interface / direction')
                ax.legend(title='Scenario', frameon=False, loc='upper left', bbox_to_anchor=(1.01, 1)); fig.tight_layout()
            np.testing.assert_array_equal([p.get_width() for p in ax.patches], pivot.to_numpy().T.ravel())
            qa.append(dict(check=metric + '_all_20_interfaces_preserved', status='PASS', observations=pivot.size))
            save(fig, 'interface_' + metric, title, [comparison_dir / 'data/net_interface_summary.csv'],
                 'Direction follows the canonical registry; signed net UC interface limits, not gross counterflow or physical AC-line loading.')

    if any(_sha(Path(p)) != digest for p, digest in original_hashes.items()):
        raise ValueError('Original comparison artifacts changed')
    for path, digest in source_hashes.items():
        if _sha(Path(path)) != digest:
            raise ValueError('Accepted cached source changed during presentation')
    metadata_path = output / 'SELECTION_FIGURE_METADATA.json'
    metadata_path.write_text(json.dumps(entries, indent=2), encoding='utf-8')
    qa_path = output / 'COMPARISON_SELECTION_QA.csv'
    pd.DataFrame(qa).to_csv(qa_path, index=False)
    receipt = dict(status='PASS', result_status=result_status, year=year, scenarios=order, figure_count=len(entries),
        checks_passed=len(qa), solver_invocations=guard['solver_invocations'], network_open_or_extraction_calls=0,
        original_comparison_artifacts_unchanged=True, source_receipt_sha256=_sha(parent_path),
        case_receipt_sha256=receipts, source_hashes=source_hashes, exact_carrier_display_mapping=labels,
        retained_diagnostic_originals=['actual_shutdowns','committed_capacity_hours','uc_uplift',
                                     'canonical_uc_system_objective','all_italy_spread_distribution'],
        artifact_hashes={Path(path).name: _sha(Path(path)) for entry in entries for path in entry['files']})
    for path in (average_path, ranked_path, metadata_path, qa_path):
        receipt['artifact_hashes'][path.name] = _sha(path)
    (output / 'COMPARISON_SELECTION_RECEIPT.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    return receipt


def _verified_source_digest(report: Path, receipt: dict, path: Path) -> str:
    """Bind a consumed table to its original or extension receipt, without networks."""
    relative = path.relative_to(report).as_posix()
    registered = {}
    for field in ("original_artifact_hashes", "artifact_hashes"):
        for name, digest in receipt.get(field, {}).items():
            name = name.replace("\\", "/")
            if name in registered and registered[name] != digest:
                raise ValueError(f"Conflicting comparison source hashes: {name}")
            registered[name] = digest
    if relative not in registered:
        raise ValueError(f"Unregistered comparison source: {relative}")
    digest = _sha(path)
    if digest != registered[relative]:
        raise ValueError(f"Comparison source changed: {relative}")
    return digest


def _comparison_result_status(metadata: pd.DataFrame) -> str:
    statuses = set(metadata.result_status)
    allowed = {"CURRENT_ACCEPTED_RESULT", "VERIFIED_RESULT_PENDING_ANALYTICAL_REVIEW"}
    if not statuses.issubset(allowed):
        raise ValueError(f"Unsupported comparison result status: {sorted(map(str, statuses))}")
    return ("VERIFIED_RESULT_PENDING_ANALYTICAL_REVIEW"
            if "VERIFIED_RESULT_PENDING_ANALYTICAL_REVIEW" in statuses
            else "CURRENT_ACCEPTED_RESULT")


def _validate_comparison_coverage(tables: dict, scenarios: list[str]) -> None:
    """Sparse categories may be zero; an absent case or metric is unknown."""
    metrics = tables['comparison_metrics']
    expected = set(scenarios)
    for metric, values in metrics.groupby('metric'):
        if set(values.scenario_id) != expected:
            raise ValueError(f"Missing whole comparison metric case: {metric}")
    if not np.isfinite(pd.to_numeric(metrics.value, errors='raise')).all():
        raise ValueError('Nonfinite comparison metric values')
    for name, values in tables.items():
        if name in ('comparison_metrics', 'scenario_metadata', 'source_provenance'):
            continue
        column = 'scenario_id' if 'scenario_id' in values else 'scenario'
        if set(values[column]) != expected:
            raise ValueError(f"Missing whole comparison table case: {name}")


def _validate_sources(year: int, case_reports: dict[str, Path], *,
                      expected_model_version: str | None = None) -> tuple[pd.DataFrame, dict]:
    if len(case_reports) < 2:
        raise ValueError("UC2 comparisons require at least two completed scenarios")
    metadata, hashes = [], {}
    for scenario, report in case_reports.items():
        path = Path(report) / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json"
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if not (
            receipt.get("status") == "PASS"
            and receipt.get("model_generation") == "UC2"
            and receipt.get("year") == year
            and receipt.get("scenario") == scenario
            and receipt.get("physical_source") == "UC_MILP"
            and receipt.get("price_source") == "FIXED_COMMITMENT_PRICE_LP"
            and receipt.get("reporting_solver_invocations") == 0
        ):
            raise ValueError(f"Incomparable or unverified UC2 report: {scenario}")
        report = Path(report)
        authority_path = report / "uc2_VIS_RESULT_AUTHORITY_REGISTER.csv"
        version = None
        if authority_path.exists() or expected_model_version is not None:
            _verified_source_digest(report, receipt, authority_path)
            authority = pd.read_csv(authority_path)
            versions = set(authority.parent_model_version)
            if len(versions) != 1:
                raise ValueError(f"Incompatible comparison model versions: {scenario}")
            version = next(iter(versions))
            if expected_model_version is not None and version != expected_model_version:
                raise ValueError(f"Unexpected comparison model version: {scenario}: {version}")
            for role, kind in (("CANONICAL_PHYSICAL", "UC_MILP"),
                               ("CANONICAL_PRICES_DUALS_ONLY", "FIXED_COMMITMENT_PRICE_LP")):
                selected = authority.loc[authority.current_role.eq(role)]
                if (len(selected) != 1 or not selected.year.eq(year).all()
                        or not selected.scenario.eq(scenario).all()
                        or not selected.solved_or_unsolved.eq("SOLVED").all()
                        or not selected.UC_status.eq("VERIFIED_UC2").all()
                        or not selected.visualization_allowed.eq(True).all()
                        or not selected.result_id.str.endswith("_" + kind).all()):
                    raise ValueError(f"Incompatible comparison source authority: {scenario}: {role}")
                protected = {name.replace('\\', '/'): digest
                             for name, digest in receipt.get('protected_source_hashes', {}).items()}
                if protected or expected_model_version is not None:
                    row = selected.iloc[0]
                    if (not {'network_path', 'network_sha256'}.issubset(selected.columns)
                            or protected.get(str(row.network_path).replace('\\', '/')) != row.network_sha256):
                        raise ValueError(f"Unbound comparison network authority: {scenario}: {role}")
        metadata.append(dict(scenario_id=scenario, display_name=scenario,
                             year=year, model_generation="UC2", model_version=version,
                             result_status=receipt.get("result_status", None if str(version).startswith(
                                 'FINAL_METHODOLOGY_') else "CURRENT_ACCEPTED_RESULT")))
        hashes[scenario] = _sha(path)
    metadata = pd.DataFrame(metadata)
    if len(set(metadata.model_version)) != 1:
        raise ValueError("Incompatible comparison model versions across scenarios")
    _comparison_result_status(metadata)
    return metadata, hashes


def build_uc2_comparisons(year: int, case_reports: dict[str, Path], output: Path, *,
                          expected_model_version: str | None = None,
                          expected_scenarios: list[str] | None = None) -> dict:
    """Compare any verified scenario list within one year and UC2 generation.

    ``case_reports`` maps metadata scenario names to their REPORTING directories.
    Outputs retain every source table hash and explicit physical/price authority.
    Negative signed interface energy is retained as signed data; gross flows
    are never promoted to a physical comparison metric.
    """
    metadata, receipt_hashes = _validate_sources(year, case_reports,
                                               expected_model_version=expected_model_version)
    result_status = _comparison_result_status(metadata)
    missing_scenarios = [s for s in (expected_scenarios or case_reports) if s not in case_reports]
    if expected_scenarios is not None and set(case_reports).difference(expected_scenarios):
        raise ValueError("Unexpected scenarios in comparison collection")
    source_receipts = {scenario: json.loads((Path(report) / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json").read_text(encoding="utf-8"))
                       for scenario, report in case_reports.items()}
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    _toolkit(cfg)  # Validate the sealed external toolkit before importing it.
    from visualization_toolkit import scenario_comparison as comparison
    from visualization_toolkit.styles import PlotStyle, style_context

    output = Path(output)
    data_dir, figure_dir = output / "data", output / "figures"
    data_dir.mkdir(parents=True, exist_ok=True)
    figure_dir.mkdir(parents=True, exist_ok=True)
    style = _comparison_style(metadata, PlotStyle)
    order = metadata.scenario_id.tolist()
    aliases = cfg["display_market_aliases"]
    metrics, monthly_frames, duration_frames, interface_frames = [], [], [], []
    national_monthly_frames, distinct_price_frames = [], []
    spread_frames, price_frames, source_rows = [], [], []

    def read(scenario, directory, name):
        path = directory / name
        digest = _verified_source_digest(Path(case_reports[scenario]), source_receipts[scenario], path)
        table = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
        source_rows.append(dict(scenario=scenario, source=str(path.resolve()), sha256=digest))
        return table

    def add(scenario, metric, value, unit, **dimensions):
        price_metric = metric.startswith("price_") or "price" in metric or "spatial_spread" in metric
        metrics.append(dict(scenario_id=scenario, metric=metric, value=float(value), unit=unit,
                            source_authority="FIXED_COMMITMENT_PRICE_LP_DUALS" if price_metric else "UC_MILP",
                            **dimensions))

    for scenario, raw_report in case_reports.items():
        report = Path(raw_report)
        stats = report / "CANONICAL/statistics"
        annual = read(scenario, stats, "uc2_price_annual_summary.csv")
        italian = annual.loc[annual.market.isin(aliases)].copy()
        if len(italian) != len(aliases) or italian.market.duplicated().any():
            raise ValueError(f"Seven unique Italian price zones required: {scenario}")
        for row in italian.itertuples(index=False):
            add(scenario, "annual_load_weighted_zonal_price", row.load_weighted_mean_EUR_per_MWh,
                "EUR/MWh", market=aliases[row.market])
            for metric in ("median", "p5", "p25", "p75", "p95", "min", "max", "std"):
                add(scenario, f"price_{metric}", getattr(row, f"{metric}_EUR_per_MWh"),
                    "EUR/MWh", market=aliases[row.market])
        monthly = read(scenario, stats, "uc2_price_monthly_zonal_means.csv")
        monthly = monthly.loc[monthly.market.isin(aliases)].copy()
        monthly.market = monthly.market.map(aliases)
        monthly.insert(0, "scenario", scenario)
        monthly_frames.append(monthly)
        national_monthly = read(scenario, stats, "uc2_price_monthly_national_load_weighted.csv")
        national_monthly.insert(0, "scenario", scenario)
        national_monthly_frames.append(national_monthly)
        hourly_prices = read(scenario, report, "fixed_commitment_prices_hourly.csv")
        for market in aliases:
            values = pd.to_numeric(hourly_prices[market], errors="raise").to_numpy()
            duration_frames.append(pd.DataFrame(dict(scenario=scenario, market=aliases[market],
                                                     rank_hour=np.arange(1, len(values) + 1),
                                                     price_EUR_per_MWh=np.sort(values)[::-1])))
            price_frames.append(pd.DataFrame(dict(scenario_id=scenario, market=aliases[market],
                                                   metric="zonal_price", value=values, unit="EUR/MWh")))
        spread = read(scenario, stats, "uc2_price_hourly_all_italy_spread.parquet")
        spread = spread.reset_index()
        spread.insert(0, "scenario", scenario)
        spread_frames.append(spread)
        distinct = spread.distinct_zonal_prices.value_counts().sort_index().rename_axis("distinct_zonal_prices").reset_index(name="hours")
        distinct["scenario"] = scenario
        distinct_price_frames.append(distinct)
        add(scenario, "all_italy_mean_spatial_spread", spread.spread_EUR_per_MWh.mean(), "EUR/MWh")
        generation = read(scenario, stats, "annual_primary_generation_national.csv")
        for row in generation.itertuples(index=False):
            add(scenario, "primary_generation", row.annual_primary_generation_TWh, "TWh",
                carrier=row.display_technology)
        curtailment = read(scenario, report, "vre_curtailment.csv")
        for carrier, group in curtailment.groupby("carrier"):
            add(scenario, "vre_curtailment", group.curtailed_MWh.sum() / 1e6, "TWh", carrier=carrier)
        balance = read(scenario, stats, "annual_electrical_balance_by_zone.csv")
        for carrier in ("bess", "phs"):
            for operation in ("charging", "discharge"):
                add(scenario, "storage_activity", balance[f"{carrier}_{operation}_TWh"].sum(), "TWh",
                    carrier=f"{carrier.upper()} {operation}")
        p2x = read(scenario, report, "p2x_annual_by_zone.csv")
        for row in p2x.itertuples(index=False):
            add(scenario, "p2x_electrical_consumption", row.consumption_MWh / 1e6,
                "TWh", market=aliases.get(row.zone, row.zone))
        imports = read(scenario, stats, "uc2_canonical_net_imports_summary.csv")
        for row in imports.itertuples(index=False):
            add(scenario, "net_imports", row.net_import_energy_MWh / 1e6,
                "TWh", market=aliases.get(row.zone, row.zone))
        interfaces = read(scenario, stats, "uc2_net_interface_summary.csv")
        interfaces.insert(0, "scenario", scenario)
        interface_frames.append(interfaces)
        events = read(scenario, report, "commitment_events_by_zone_technology.csv")
        for carrier, group in events.groupby("carrier"):
            add(scenario, "actual_starts", group.actual_starts.sum(), "starts", carrier=carrier)
            add(scenario, "actual_shutdowns", group.actual_shutdowns.sum(), "shutdowns", carrier=carrier)
        online = read(scenario, report, "hourly_online_units_and_committed_MW.csv")
        if "committed_MW" not in online:
            raise ValueError("Canonical UC hourly commitment must contain committed_MW")
        # The current UC2 report contains one national total per snapshot.
        add(scenario, "committed_capacity_hours", online.committed_MW.sum() / 1e6, "million MW h")
        economic_path = report / "economic_comparison.json"
        economic_digest = _verified_source_digest(report, source_receipts[scenario], economic_path)
        economics = json.loads(economic_path.read_text(encoding="utf-8"))
        source_rows.append(dict(scenario=scenario, source=str(economic_path.resolve()), sha256=economic_digest))
        add(scenario, "uc_uplift", economics["uc_uplift_EUR"] / 1e6, "million EUR")
        add(scenario, "canonical_uc_system_objective", economics["uc_milp_objective_EUR"] / 1e9, "billion EUR")

    tables = {
        "scenario_metadata": metadata,
        "comparison_metrics": pd.DataFrame(metrics),
        "monthly_zonal_prices": pd.concat(monthly_frames, ignore_index=True),
        "monthly_national_load_weighted_price": pd.concat(national_monthly_frames, ignore_index=True),
        "price_duration": pd.concat(duration_frames, ignore_index=True),
        "hourly_zonal_prices": pd.concat(price_frames, ignore_index=True),
        "all_italy_spatial_spread": pd.concat(spread_frames, ignore_index=True),
        "distinct_simultaneous_prices": pd.concat(distinct_price_frames, ignore_index=True),
        "net_interface_summary": pd.concat(interface_frames, ignore_index=True),
        "source_provenance": pd.DataFrame(source_rows),
    }
    _validate_comparison_coverage(tables, order)
    for name, table in tables.items():
        table.to_csv(data_dir / f"{name}.csv", index=False, float_format="%.12g")
    prepared = comparison.prepare_scenario_metrics(tables["comparison_metrics"], metadata)
    files, figure_metadata = [], []

    def save(fig, name, data, limitation=""):
        fig.text(.01, .01, f"{result_status} | UC2 {year} | physical: UC MILP; prices: fixed-commitment LP duals",
                 fontsize=8, color="#555555")
        fig.subplots_adjust(bottom=max(fig.subplotpars.bottom, .14))
        paths = []
        for extension in ("png", "svg"):
            path = figure_dir / f"{name}.{extension}"
            with plt.rc_context({"svg.hashsalt": "MEM_UC2_VIS_X1_COMPARISON"}):
                fig.savefig(path, bbox_inches="tight", metadata={"Date": None} if extension == "svg" else None)
            paths.append(str(path.relative_to(output)))
        plt.close(fig)
        files.extend(paths)
        figure_metadata.append(dict(figure_id=name, files=paths, underlying_data=[f"data/{d}.csv" for d in data],
                                    status=result_status, year=year, scenarios=order,
                                    physical_source="UC_MILP", price_source="FIXED_COMMITMENT_PRICE_LP",
                                    limitation=limitation))

    for market in aliases.values():
        fig = comparison.plot_scenario_comparison(prepared, "annual_load_weighted_zonal_price",
                                                  filters={"market": market}, aggregation="mean",
                                                  scenario_order=order, style=style)
        fig.axes[0].set_title(f"{market}: annual load-weighted price | {year}")
        save(fig, f"annual_load_weighted_price_{market}", ["comparison_metrics"])
    bar_metrics = {
        "primary_generation": "carrier", "vre_curtailment": "carrier",
        "storage_activity": "carrier", "p2x_electrical_consumption": "market",
        "net_imports": "market", "actual_starts": "carrier", "actual_shutdowns": "carrier",
        "committed_capacity_hours": None, "uc_uplift": None,
        "canonical_uc_system_objective": None, "all_italy_mean_spatial_spread": None,
    }
    for metric, category in bar_metrics.items():
        fig = comparison.plot_scenario_comparison(prepared, metric, category=category, aggregation="sum",
                                                  scenario_order=order, style=style, missing_category_as_zero=True)
        save(fig, metric, ["comparison_metrics"],
             "Signed zonal contributions retain direction; interzonal imports cancel nationally." if metric == "net_imports" else "")
    spread_metrics = tables["all_italy_spatial_spread"].rename(columns={"scenario": "scenario_id", "spread_EUR_per_MWh": "value"})
    spread_metrics["metric"], spread_metrics["unit"] = "all_italy_hourly_spatial_spread", "EUR/MWh"
    spread_prepared = comparison.prepare_scenario_metrics(spread_metrics, metadata)
    fig = comparison.plot_scenario_distribution(spread_prepared, "all_italy_hourly_spatial_spread",
                                                scenario_order=order, style=style)
    save(fig, "all_italy_spread_distribution", ["all_italy_spatial_spread"], "8760 chronological hourly observations; outliers hidden by toolkit default.")
    price_prepared = comparison.prepare_scenario_metrics(tables["hourly_zonal_prices"], metadata)
    for market in aliases.values():
        fig = comparison.plot_scenario_distribution(price_prepared, "zonal_price", filters={"market": market},
                                                     scenario_order=order, style=style)
        fig.axes[0].set_title(f"{market}: price distribution | {year}")
        save(fig, f"price_distribution_{market}", ["hourly_zonal_prices"], "Outliers hidden by toolkit default; full observations retained.")
    with style_context(style):
        fig, ax = plt.subplots(figsize=(10, 5))
        for scenario in order:
            subset = tables["monthly_national_load_weighted_price"].loc[lambda d: d.scenario.eq(scenario)]
            ax.plot(subset.month, subset.load_weighted_mean_EUR_per_MWh,
                    label=scenario, color=style.scenario_color(scenario))
        ax.set(xlabel="Month", ylabel="EUR/MWh", title=f"Monthly load-weighted national price | {year}")
        ax.legend(frameon=False)
        fig.tight_layout()
        save(fig, "monthly_national_load_weighted_price", ["monthly_national_load_weighted_price"])
        fig, ax = plt.subplots(figsize=(10, 5))
        distinct = tables["distinct_simultaneous_prices"].pivot(index="distinct_zonal_prices", columns="scenario", values="hours").reindex(index=range(1, 8), columns=order).fillna(0)
        distinct.plot.bar(ax=ax, color=[style.scenario_color(s) for s in order])
        ax.set(xlabel="Distinct simultaneous Italian zonal prices", ylabel="hours", title=f"Italian price separation | {year}")
        ax.legend(frameon=False)
        fig.tight_layout()
        save(fig, "distinct_simultaneous_prices", ["distinct_simultaneous_prices"], "Exact numeric equality; near-coupling statistics are retained in case diagnostics.")
        for table_name, x, value, name, xlabel in (
            ("monthly_zonal_prices", "month", "mean_EUR_per_MWh", "monthly_zonal_price", "Month"),
            ("price_duration", "rank_hour", "price_EUR_per_MWh", "price_duration", "Rank hour"),
        ):
            fig, axes = plt.subplots(4, 2, figsize=(13, 13))
            for ax, market in zip(axes.flat, aliases.values()):
                for scenario in order:
                    subset = tables[table_name].loc[lambda d: d.scenario.eq(scenario) & d.market.eq(market)]
                    ax.plot(subset[x], subset[value], label=scenario, color=style.scenario_color(scenario))
                ax.set(title=market, xlabel=xlabel, ylabel="EUR/MWh")
                ax.legend(frameon=False)
            axes.flat[-1].set_visible(False)
            fig.suptitle(f"{name.replace('_', ' ').title()} | UC2 {year}")
            fig.tight_layout(rect=(0, .05, 1, .97))
            save(fig, name, [table_name])
        # Grouped signed corridor values preserve individual interface identity.
        interfaces = tables["net_interface_summary"]
        for metric, factor, unit in (("net_energy_MWh", 1e6, "TWh"),
                                     ("positive_limit_hours", 1, "hours"),
                                     ("negative_limit_hours", 1, "hours")):
            pivot = interfaces.pivot(index="interface_id", columns="scenario", values=metric).reindex(columns=order)
            if pivot.isna().any().any():
                raise ValueError("UC2 scenarios have different canonical interface sets")
            fig, ax = plt.subplots(figsize=(13, max(7, .3 * len(pivot))))
            (pivot / factor).plot.barh(ax=ax, color=[style.scenario_color(s) for s in order], width=.8)
            ax.set(xlabel=unit, ylabel="Canonical net interface", title=f"{metric.replace('_', ' ').title()} | {year}")
            ax.legend(frameon=False)
            fig.tight_layout()
            save(fig, f"interface_{metric}", ["net_interface_summary"], "UC MILP signed net corridor quantities, never gross counterflows or physical reference AC branch loading.")

    (output / "MEM_UC2_COMPARISON_FIGURE_METADATA.json").write_text(json.dumps(figure_metadata, indent=2), encoding="utf-8")
    manifest = []
    for path in _base_comparison_artifacts(output):
        manifest.append(dict(artifact=path.relative_to(output).as_posix(), sha256=_sha(path)))
    manifest_path = output / "MEM_UC2_COMPARISON_MANIFEST.csv"
    pd.DataFrame(manifest).to_csv(manifest_path, index=False)
    receipt = dict(status="PASS", model_generation="UC2", year=year, scenarios=order,
                   result_status=result_status, model_version=metadata.model_version.iloc[0],
                   collection_status="PARTIAL" if missing_scenarios else "COMPLETE",
                   expected_scenarios=list(expected_scenarios or order), missing_scenarios=missing_scenarios,
                   selected_view_records=selected_comparison_view_records(
                       year, case_reports, output, expected_scenarios=expected_scenarios,
                       model_version=metadata.model_version.iloc[0]),
                   reporting_solver_invocations=0, physical_source="UC_MILP", price_source="FIXED_COMMITMENT_PRICE_LP",
                   source_receipt_sha256=receipt_hashes, figure_count=len(figure_metadata),
                   table_count=len(tables), manifest_sha256=_sha(manifest_path))
    (output / "MEM_UC2_COMPARISON_RECEIPT.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt

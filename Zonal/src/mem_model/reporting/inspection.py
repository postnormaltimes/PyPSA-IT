"""Derived local result gallery. Reads reporting tables; never opens a network."""
from __future__ import annotations

import ast
import csv
import hashlib
import html
import json
import os
from pathlib import Path
import shutil

import pandas as pd

CATEGORIES = {
    '01_summary': 'Capacity and annual results',
    '02_generation_vre': 'Generation and renewable availability',
    '03_prices': 'Fixed-commitment prices',
    '04_adequacy': 'Adequacy and scarcity',
    '05_storage_hydro': 'Storage and hydro',
    '06_p2x': 'Power-to-X withdrawal',
    '07_network_exchanges': 'Interfaces and exchanges',
    '08_commitment': 'Unit commitment',
}

def _sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''): h.update(b)
    return h.hexdigest()

def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def _relative(path, root):
    return Path(os.path.relpath(path, root)).as_posix()

def _resolve(value, report):
    """Use existing paths or a report-relative suffix after relocation."""
    p = Path(str(value))
    if p.is_absolute() and p.is_file(): return p
    q = report / str(value).replace('\\', '/')
    if q.is_file(): return q
    parts = str(value).replace('\\', '/').split('/')
    if 'REPORTING' in parts:
        q = report.joinpath(*parts[parts.index('REPORTING') + 1:])
        if q.is_file(): return q
    raise FileNotFoundError(f'Report source is missing: {value}')

def _category(name, family=''):
    """Group the accepted family, then its existing technical subject."""
    name = name.lower()
    if 'price' not in name and any(x in name for x in ('commitment', 'online_', 'cycling', 'start_up', 'shutdown')): return '08_commitment'
    if 'p2x' in name and 'residual' not in name: return '06_p2x'
    if any(x in name for x in ('scarcity', 'shedding', 'stress_diagnostic')): return '04_adequacy'
    if family == 'storage' or any(x in name for x in ('storage', 'phs', 'hydro', 'water_value', 'ror_water', 'bypass')): return '05_storage_hydro'
    if family in ('exchanges', 'network') or any(x in name for x in ('interface', 'import', 'export', 'topology', 'flow', 'external_net')): return '07_network_exchanges'
    if family == 'prices' or 'price' in name or 'marginal_regime' in name: return '03_prices'
    if family == 'summary' or any(x in name for x in ('capacity', 'balance', 'objective', 'annual_generation')): return '01_summary'
    return '02_generation_vre'

def figure_registry(report):
    """Inherit VIS metadata and the canonical reporting manifest, without rendering."""
    report = Path(report)
    metadata = _read(report / 'VIS_X1/FIGURE_METADATA.json')
    index = pd.read_csv(report / 'VIS_X1/FIGURE_INDEX.csv').fillna('')
    figures = metadata['figures']
    if metadata['figure_count'] != len(figures) or index.figure.duplicated().any():
        raise ValueError('Invalid visualization registry count or duplicate identifier')
    if set(index.figure) != {f['figure'] for f in figures} or len(figures) != len(index):
        raise ValueError('Visualization metadata/index disagreement')
    rows = []
    indexed = index.set_index('figure')
    for f in figures:
        status = f.get('status', '')
        applicable = status not in ('NOT_APPLICABLE', 'WAITING_FOR_COMPARABLE_CASES')
        row = dict(figure_id='VIS_X1/' + f['figure'], title=f['figure'].replace('_', ' ').title(),
                   category=_category(f['figure'], f['family']), applicability='APPLICABLE' if applicable else status,
                   result_role='Geographic reference only' if status == 'REFERENCE_ONLY' else 'Verified case result',
                   description=f.get('limitation', ''), sources=[], formats={})
        if applicable:
            if not f.get('png') or not f.get('underlying_data'): raise ValueError('Applicable figure lacks PNG/data contract')
            for ext in ('png', 'svg', 'pdf'):
                if bool(f.get(ext)) != bool(indexed.at[f['figure'], ext] if ext in indexed else ''):
                    raise ValueError('Visualization format contract disagreement')
                if f.get(ext):
                    p = _resolve(f[ext], report)
                    if _resolve(indexed.at[f['figure'], ext], report) != p: raise ValueError('Visualization path disagreement')
                    row['formats'][ext] = p
            listed = indexed.at[f['figure'], 'underlying_data']
            if isinstance(listed, str) and listed: listed = ast.literal_eval(listed)
            if list(listed) != f['underlying_data']: raise ValueError('Visualization data contract disagreement')
            row['sources'] = [_resolve(v, report) for v in f['underlying_data']]
        rows.append(row)
    manifest_path = report / 'CANONICAL/MEM_CANONICAL_REPORTING_MANIFEST.csv'
    if manifest_path.is_file():
        manifest = pd.read_csv(manifest_path)
        for item in manifest.itertuples():
            source = _resolve('CANONICAL/' + item.artifact, report)
            if _sha(source) != item.sha256: raise ValueError('Canonical reporting manifest hash mismatch')
        tables = [_resolve('CANONICAL/' + v, report) for v in manifest.artifact if str(v).startswith('statistics/')]
        pngs = [str(v) for v in manifest.artifact if str(v).startswith('figures/') and str(v).endswith('.png')]
        for v in pngs:
            stem = Path(v).stem
            formats = {ext: _resolve('CANONICAL/' + Path(v).with_suffix('.' + ext).as_posix(), report)
                       for ext in ('png', 'svg', 'pdf') if Path(v).with_suffix('.' + ext).as_posix() in set(manifest.artifact)}
            rows.append(dict(figure_id='CANONICAL/' + stem, title=stem.replace('_', ' ').title(),
                             category=_category(stem), applicability='APPLICABLE', result_role='Verified case result',
                             description='Source tables are listed in the canonical reporting manifest.', sources=tables, formats=formats))
    for mp in (report/'VIS_X1').rglob('LEXICOGRAPHIC_FIGURE_METADATA.json'):
        for f in _read(mp):
            formats = {Path(v).suffix[1:]: _resolve(v, report) for v in f['files']}
            if 'png' not in formats: raise ValueError('Marginal-regime figure lacks PNG')
            rows.append(dict(figure_id='MARGINAL_REGIME/' + formats['png'].stem,
                             title='Model-implied marginal price-setting regime', category='03_prices',
                             applicability='APPLICABLE', result_role='Fixed-commitment price interpretation',
                             description='Represented-time shares; native price-recovery evidence.',
                             sources=[_resolve(v, report) for v in f['underlying_data']], formats=formats))
    unavailable_declared = set()
    dispatch_metadata = report / 'DISPATCH_PRESENTATION/FIGURE_METADATA.json'
    if dispatch_metadata.is_file():
        from .dispatch_figures import validate_dispatch_figures
        dispatch = validate_dispatch_figures(report)
        metadata = _read(dispatch_metadata)
        if metadata['figure_count'] != len(metadata['figures']):
            raise ValueError('Dispatch figure metadata count disagreement')
        for f in metadata['figures']:
            if not f.get('required', True) and dispatch.get('diagnostic_status') != 'PASS':
                unavailable_declared.update((report/f[ext]).resolve() for ext in ('png', 'svg', 'pdf') if f.get(ext))
                rows.append(dict(figure_id='DISPATCH_PRESENTATION/' + f['figure_id'],
                    title=f['title'], category='02_generation_vre', applicability='WAITING_FOR_OPTIONAL_DIAGNOSTIC',
                    result_role=f['role'], description='The optional diagnostic is missing or differs from its recorded source.',
                    sources=[], formats={}))
                continue
            formats = {ext: _resolve(f[ext], report) for ext in ('png', 'svg', 'pdf') if f.get(ext)}
            if 'png' not in formats or not f.get('underlying_data'):
                raise ValueError('Dispatch presentation lacks PNG/data contract')
            rows.append(dict(figure_id='DISPATCH_PRESENTATION/' + f['figure_id'],
                title=f['title'], category='02_generation_vre', applicability='APPLICABLE',
                result_role=f['role'], description=f.get('limitation', ''),
                sources=[_resolve(v, report) for v in f['underlying_data']], formats=formats))
    ids = [r['figure_id'] for r in rows]
    if len(ids) != len(set(ids)): raise ValueError('Duplicate figure identifier')
    declared = {p.resolve() for r in rows for p in r['formats'].values()}
    rendered = {p.resolve() for p in report.rglob('*')
                if p.suffix.lower() in ('.png', '.svg', '.pdf')}
    # Additional accepted producers have their own figure manifests.
    extras = rendered - declared - unavailable_declared
    if extras: raise ValueError('Unregistered rendered figures: ' + ', '.join(_relative(p, report) for p in sorted(extras)))
    return rows

def _verified_report(report):
    receipt = _read(report / 'MEM_UC2_REPORTING_RECEIPT.json')
    if receipt.get('status') != 'PASS' or receipt.get('reporting_solver_invocations') != 0:
        raise ValueError('A verified report is required')
    for name, field in [('REFERENCE', 'reference_verification'), ('UC', 'uc_verification'), ('PRICE', 'price_verification')]:
        q = _read(report.parent / f'QA/VERIFY_{name}.json')
        if q.get('status') != 'PASS' or (q.get('year'), q.get('scenario')) != (receipt['year'], receipt['scenario']):
            raise ValueError('Same-case solve verification is missing')
        if q['solved_sha256'] != receipt[field]: raise ValueError('Report lineage does not match solve verification')
    return receipt

def _summary(report):
    rows = []
    def add(metric, value, unit, path, field):
        rows.append(dict(metric=metric, value=value, unit=unit, source_file=_relative(report/path, report.parent/'INSPECTION'), source_field=field))
    econ = _read(report / 'economic_comparison.json')
    for k, v in econ.items(): add(k, v, '%' if k.endswith('percent') else 'EUR', 'economic_comparison.json', k)
    verify = _read(report.parent / 'QA/VERIFY_UC.json')
    if 'load_shedding_MWh' in verify: add('UC unserved energy', verify['load_shedding_MWh'], 'MWh', '../QA/VERIFY_UC.json', 'load_shedding_MWh')
    specs = [
        ('CANONICAL/statistics/annual_primary_generation_national.csv', 'display_technology', 'annual_primary_generation_TWh', 'TWh'),
        ('CANONICAL/statistics/installed_capacity_national.csv', 'display_technology', 'installed_capacity_GW', 'GW'),
        ('CANONICAL/statistics/annual_net_imports_by_zone.csv', 'zone', 'annual_net_imports_TWh', 'TWh'),
        ('CANONICAL/statistics/uc2_price_annual_summary.csv', 'market', 'load_weighted_mean_EUR_per_MWh', 'EUR/MWh'),
    ]
    for path, label, value, unit in specs:
        if (report/path).is_file():
            t = pd.read_csv(report/path)
            for _, r in t.iterrows():
                if pd.notna(r[value]): add(f'{value}: {r[label]}', r[value], unit, path, f'{value}; {label}={r[label]}')
    path = 'vre_curtailment.csv'
    t = pd.read_csv(report/path)
    for col in ('available_MWh', 'dispatched_MWh', 'curtailed_MWh'):
        add('VRE ' + col, t[col].sum(), 'MWh', path, 'sum(' + col + ')')
    path = 'VIS_X1/data/uc_storage_hydro_totals.csv'
    if (report/path).is_file():
        t = pd.read_csv(report/path)
        for _, r in t.iterrows():
            for col in ('charge_throughput', 'discharge_throughput', 'minimum_energy_mwh', 'maximum_energy_mwh'):
                if pd.notna(r[col]): add(f'{r.carrier}: {col}', r[col], 'MWh', path, f'{col}; carrier={r.carrier}')
    return rows

def _write_csv(path, rows, columns):
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=columns); w.writeheader(); w.writerows(rows)

def build_inspection(report_dir):
    """Refresh only INSPECTION, using verified CSV/Parquet and rendered files."""
    report = Path(report_dir).resolve()
    receipt = _verified_report(report)
    if receipt.get('output_register_required') and not all((report/name).is_file()
            for name in ('OUTPUT_REGISTER.csv', 'OUTPUT_REGISTER.json')):
        raise ValueError('The declared consolidated output register is missing')
    registry = figure_registry(report)  # fail before writing an incomplete gallery
    output_status = None
    if (report/'OUTPUT_REGISTER.json').is_file():
        from .output_registry import validate_output_register
        output_status = validate_output_register(report)
    output = report.parent / 'INSPECTION'
    output.mkdir(exist_ok=True)
    marker = output / 'INSPECTION_MANIFEST.json'
    previous_payload = _read(marker).get('artifacts', {}) if marker.exists() else {}
    # Remove a previous derived completion marker before an interrupted refresh.
    if marker.exists(): marker.unlink()
    figure_rows, payload = [], {}
    for r in registry:
        record = {k: r[k] for k in ('figure_id', 'title', 'category', 'applicability', 'result_role', 'description')}
        record['underlying_data'] = json.dumps([_relative(p, output) for p in r['sources']])
        for ext in ('png', 'svg', 'pdf'):
            record[ext] = ''; record[ext + '_sha256'] = ''
            if ext in r['formats']:
                source = r['formats'][ext]
                prefix, stem = r['figure_id'].split('/', 1)
                target = output / 'figures' / r['category'] / f'{prefix}_{stem}.{ext}'
                record[ext + '_sha256'] = _sha(source)
                if ext != 'png' and source.stat().st_size > 2 * 1024 * 1024:
                    # Large vectors remain at their original reporting path.
                    record[ext] = _relative(source, output)
                    if target.exists():
                        owned = target.relative_to(output).as_posix()
                        if previous_payload.get(owned) != _sha(target) or _sha(target) != _sha(source):
                            raise ValueError('Cannot remove an unverified inspection duplicate')
                        target.unlink()  # Only a verified copy; original is retained.
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    record[ext] = target.relative_to(output).as_posix()
                    payload[record[ext]] = record[ext + '_sha256']
        figure_rows.append(record)
    tables = []
    for source in sorted(report.rglob('*')):
        if source.suffix.lower() not in ('.csv', '.parquet') or not source.is_file(): continue
        relative = source.relative_to(report).as_posix()
        category = _category(relative)
        target = output/'tables'/category/relative
        # Large hourly tables remain in canonical reporting; avoid duplicate storage.
        if source.stat().st_size <= 2 * 1024 * 1024:
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, target)
            link = target.relative_to(output).as_posix(); payload[link] = _sha(source)
        else: link = _relative(source, output)
        tables.append(dict(display_name=source.stem.replace('_', ' '), category=category, inspection_path=link,
                           canonical_source_path=_relative(source, output), sha256=_sha(source), format=source.suffix[1:],
                           description='Existing reporting dataset; original values and provenance retained.'))
    expected_copies = {(output/r['inspection_path']).resolve() for r in tables if r['inspection_path'].startswith('tables/')}
    source_tables = {(output/r['canonical_source_path']).resolve() for r in tables}
    removed_table_copies = []
    for old in (output/'tables').rglob('*'):
        if not old.is_file() or old.suffix not in ('.csv','.parquet') or old.resolve() in expected_copies: continue
        parts = old.relative_to(output/'tables').parts
        source = report.joinpath(*parts[1:]).resolve()
        if parts[0] not in CATEGORIES or source not in source_tables or _sha(old) != _sha(source):
            raise ValueError('Cannot remove an unverified inspection table')
        removed_table_copies.append(dict(path=old.relative_to(output).as_posix(),
            retained_source=_relative(source, output), sha256=_sha(source)))
        old.unlink()  # Verified derived duplicate; canonical table is retained.
    _write_csv(output/'FIGURE_INDEX.csv', figure_rows, list(figure_rows[0]))
    _write_csv(output/'TABLE_INDEX.csv', tables, list(tables[0]))
    summary = _summary(report)
    _write_csv(output/'RESULTS_SUMMARY.csv', summary, list(summary[0]))
    version = report.parents[2].name
    headline = f'{receipt["year"]} {receipt["scenario"]} — {version}'
    blocks = []
    for category, title in CATEGORIES.items():
        group = [r for r in figure_rows if r['category'] == category]
        if not group: continue
        items = []
        for r in group:
            links = ' '.join(f'<a href="{html.escape(r[e])}">{e.upper()}</a>' for e in ('png', 'svg', 'pdf') if r[e])
            preview = f'<a href="{html.escape(r["png"])}"><img loading="lazy" src="{html.escape(r["png"])}" alt="{html.escape(r["title"])}"></a>' if r['png'] else ''
            items.append(f'<article data-figure-id="{html.escape(r["figure_id"])}"><h3>{html.escape(r["title"])}</h3><p>{html.escape(r["result_role"])}. {html.escape(r["description"])}</p>{preview}<p>{links}</p></article>')
        blocks.append(f'<section id="{category}"><h2>{title}</h2>{"".join(items)}</section>')
    links = ' · '.join(f'<a href="#{c}">{t}</a>' for c,t in CATEGORIES.items() if any(r['category']==c for r in figure_rows))
    metrics = ''.join(f'<tr><td>{html.escape(str(r["metric"]))}</td><td>{float(r["value"]):,.4f}</td><td>{r["unit"]}</td></tr>' for r in summary)
    coverage = ''
    if output_status:
        # Technical statuses remain in JSON/CSV; prose names the missing sources.
        waiting = [r for r in output_status['rows'] if str(r.get('status', '')).startswith(('WAITING_', 'EVIDENCE_GATED'))]
        if waiting:
            coverage = '<section><h2>Additional output availability</h2><ul>' + ''.join(
                f'<li>{html.escape(str(r["output_id"]))}: {html.escape(str(r.get("remaining_action", "Required evidence is not available.")))}</li>'
                for r in waiting) + '</ul></section>'
    page = f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(headline)}</title><style>body{{font:16px system-ui,sans-serif;color:#172b3a;background:white;max-width:1200px;margin:32px auto;padding:0 24px}}a{{color:#185d89}}nav{{line-height:2}}article{{border-top:1px solid #d7dfe4;margin:28px 0}}img{{width:100%;height:auto}}table{{border-collapse:collapse}}td{{padding:6px 12px;border-bottom:1px solid #ddd}}section{{margin-top:48px}}h2{{color:#214d69}}</style><h1>{html.escape(headline)}</h1><p>Verified reporting. Physical quantities: UC MILP. Prices and water values: fixed-commitment price LP. This inspection layer is derived; original reporting remains the source of record.</p><nav>{links}</nav><p><a href="README.md">Result notes</a> · <a href="RESULTS_SUMMARY.csv">Summary CSV</a> · <a href="FIGURE_INDEX.csv">Figure index</a> · <a href="TABLE_INDEX.csv">Table index</a></p><details><summary>Annual result quantities</summary><table>{metrics}</table></details>{coverage}{"".join(blocks)}</html>'
    (output/'index.html').write_text(page, encoding='utf-8')
    prose = f'# {headline}\n\nVerified reporting, with physical operation from the UC MILP and prices/water values from the fixed-commitment LP. The continuous reference is an economic counterfactual. Original solve provenance is retained.\n\nThis derived inspection directory contains no solved network. Open [the figure gallery](index.html) in a browser. [Figure index](FIGURE_INDEX.csv), [table index](TABLE_INDEX.csv) and [summary values](RESULTS_SUMMARY.csv) retain source references.\n\n## Annual result quantities\n\n| Quantity | Value | Unit |\n| --- | ---: | --- |\n'
    # Keep the overview short; detailed capacities and state extrema stay in CSV.
    overview = [r for r in summary if r['unit'] != 'GW'
                and not any(s in r['metric'] for s in ('minimum_energy', 'maximum_energy', 'absolute_difference',
                    'fixed_commitment_lp_objective', 'VRE available_', 'VRE dispatched_'))]
    def label(metric):
        return metric.replace('annual_primary_generation_TWh:', 'Generation:').replace(
            'annual_net_imports_TWh:', 'Net imports:').replace('load_weighted_mean_EUR_per_MWh:', 'Load-weighted price:')
    prose += ''.join(f'| {label(r["metric"])} | {float(r["value"]):,.4f} | {r["unit"]} |\n' for r in overview)
    prose += '\n## Figure groups\n\n' + '\n'.join(f'- [{title}](index.html#{category})' for category,title in CATEGORIES.items() if any(r['category']==category for r in figure_rows)) + '\n\nCross-scenario comparisons require compatible completed cases and are not represented as single-case results. The geographic reference figure is context only, not a simulated physical network.\n'
    (output/'README.md').write_text(prose, encoding='utf-8')
    for name in ('FIGURE_INDEX.csv','TABLE_INDEX.csv','RESULTS_SUMMARY.csv','README.md','index.html'): payload[name] = _sha(output/name)
    manifest = dict(schema_version='MEM_RESULT_INSPECTION_V1', year=receipt['year'], scenario=receipt['scenario'],
                    actual_execution_version=version, derived_non_authoritative=True, complete=True,
                    physical_authority='UC_MILP', price_authority='FIXED_COMMITMENT_PRICE_LP',
                    reporting_receipt_sha256=_sha(report/'MEM_UC2_REPORTING_RECEIPT.json'),
                    applicable_figures=sum(r['applicability']=='APPLICABLE' for r in figure_rows),
                    generated_applicable_figures=sum(bool(r['png']) and r['applicability']=='APPLICABLE' for r in figure_rows),
                    non_applicable_figures=sum(r['applicability']!='APPLICABLE' for r in figure_rows),
                    additional_visualization_status=[
                        dict(family='cross-scenario comparisons',status='WAITING_FOR_COMPARABLE_CASES',
                             reason='Separate comparison reporting requires compatible completed scenarios.'),
                        dict(family='marginal price-setting regime',status='GENERATED' if any(r['figure_id'].startswith('MARGINAL_REGIME/') for r in figure_rows) else 'WAITING_FOR_VALIDATED_ROOT_DATA',
                             reason='Uses the existing validated-root reporting contract; no root discovery or price reinterpretation.')],
                    table_count=len(tables), artifacts=payload, removed_verified_table_copies=removed_table_copies,
                    optimization_model_constructed=False, solver_invocations=0)
    if output_status:
        manifest['output_register_sha256'] = _sha(report/'OUTPUT_REGISTER.json')
        manifest['output_status'] = {k: output_status[k] for k in
            ('core_reporting_status', 'accepted_visualizations_status', 'comparison_status',
             'evidence_gated_outputs_status', 'cross_scenario_comparisons_status')}
        manifest['additional_visualization_status'] = [
            {k: row[k] for k in ('output_id', 'status', 'remaining_action')}
            for row in output_status['rows'] if row.get('output_type') != 'SINGLE_CASE_FIGURE']
    marker.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    try:
        return validate_inspection(report)
    except Exception:
        manifest['complete'] = False
        marker.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        raise

def validate_inspection(report_dir):
    """Check original data, all registered formats and gallery/table references."""
    import re
    from PIL import Image
    report = Path(report_dir).resolve(); output = report.parent/'INSPECTION'
    receipt = _verified_report(report); registry = figure_registry(report)
    m = _read(output/'INSPECTION_MANIFEST.json')
    if not m['complete'] or m['reporting_receipt_sha256'] != _sha(report/'MEM_UC2_REPORTING_RECEIPT.json'):
        raise ValueError('Inspection is incomplete or uses stale reporting')
    output_status = None
    comparison_refresh_required = False
    if m.get('output_register_sha256'):
        if m['output_register_sha256'] != _sha(report/'OUTPUT_REGISTER.json'):
            raise ValueError('Inspection output register is stale')
        from .output_registry import validate_output_register
        output_status = validate_output_register(report)
        changed = {k for k in m['output_status'] if m['output_status'][k] != output_status[k]}
        if changed:
            # A sibling run can refresh shared comparisons without changing any
            # case figure or table. Keep that collection explicitly incomplete.
            comparison_refresh_required = (
                output_status.get('register_status') == 'STALE_COMPARISON_COLLECTION_REFRESH_REQUIRED'
                and changed.issubset({'comparison_status', 'cross_scenario_comparisons_status'}))
            if not comparison_refresh_required:
                raise ValueError('Inspection output availability changed')
    for path,digest in m['artifacts'].items():
        if _sha(output/path) != digest: raise ValueError(f'Inspection artifact changed: {path}')
    figs = pd.read_csv(output/'FIGURE_INDEX.csv').fillna('')
    if figs.figure_id.duplicated().any() or set(figs.figure_id) != {r['figure_id'] for r in registry}:
        raise ValueError('Inspection figure count or identifiers differ from registry')
    declared = set()
    for r in registry:
        q = figs.loc[figs.figure_id.eq(r['figure_id'])].iloc[0]
        if q.applicability != r['applicability']: raise ValueError('Figure applicability changed')
        for ext in ('png','svg','pdf'):
            if bool(q[ext]) != (ext in r['formats']): raise ValueError('Inspection format contract changed')
            if q[ext]:
                p = output/q[ext]
                if p.resolve().is_relative_to((output/'figures').resolve()): declared.add(p.resolve())
                elif p.resolve() != r['formats'][ext].resolve(): raise ValueError('Unexpected external figure reference')
                if _sha(p) != _sha(r['formats'][ext]): raise ValueError('Inspection figure differs from source')
                if ext=='png':
                    with Image.open(p) as im: im.verify()
        for path in json.loads(q.underlying_data):
            if not (output/path).is_file(): raise ValueError('Broken figure data reference')
    rendered = {p.resolve() for p in (output/'figures').rglob('*') if p.suffix in ('.png','.svg','.pdf')}
    if declared != rendered: raise ValueError('Orphan inspection figure')
    tables = pd.read_csv(output/'TABLE_INDEX.csv')
    if tables.canonical_source_path.duplicated().any(): raise ValueError('Duplicate table index source')
    expected_tables = {p.resolve() for p in report.rglob('*') if p.suffix in ('.csv','.parquet') and p.is_file()}
    if {(output/p).resolve() for p in tables.canonical_source_path} != expected_tables: raise ValueError('Table coverage mismatch')
    for r in tables.itertuples():
        if _sha(output/r.inspection_path) != r.sha256 or _sha(output/r.canonical_source_path) != r.sha256:
            raise ValueError('Table index hash mismatch')
    copied_tables = {(output/p).resolve() for p in tables.inspection_path if str(p).startswith('tables/')}
    actual_copies = {p.resolve() for p in (output/'tables').rglob('*') if p.is_file() and p.suffix in ('.csv','.parquet')}
    if copied_tables != actual_copies: raise ValueError('Orphan inspection table')
    pd.testing.assert_frame_equal(pd.read_csv(output/'RESULTS_SUMMARY.csv'), pd.DataFrame(_summary(report)), check_dtype=False, rtol=1e-12)
    page = (output/'index.html').read_text(encoding='utf-8')
    for link in re.findall(r'(?:src|href)="([^"]+)"',page):
        if link.startswith('#'): continue
        if '://' in link or Path(link).is_absolute() or not (output/html.unescape(link)).is_file():
            raise ValueError('Gallery requires a missing or external resource')
    displayed = re.findall(r'data-figure-id="([^"]+)"', page)
    if len(displayed)!=len(set(displayed)) or set(displayed)!=set(figs.figure_id): raise ValueError('Gallery figure coverage mismatch')
    if m['applicable_figures'] != m['generated_applicable_figures'] or m['table_count']!=len(tables):
        raise ValueError('Inspection completeness mismatch')
    result = dict(status='COMPLETE',year=receipt['year'],scenario=receipt['scenario'],inspection_path=str(output),
                applicable_figures=m['applicable_figures'],generated_applicable_figures=m['generated_applicable_figures'],
                non_applicable_figures=m['non_applicable_figures'],tables=len(tables),visualization_completeness_percent=100,
                solver_invocations=0)
    if output_status:
        result['output_status'] = {k: output_status[k] for k in m['output_status']}
        result['comparison_refresh_required'] = comparison_refresh_required
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('report_dir',type=Path);p.add_argument('--check',action='store_true')
    a=p.parse_args();print(json.dumps((validate_inspection if a.check else build_inspection)(a.report_dir),indent=2))

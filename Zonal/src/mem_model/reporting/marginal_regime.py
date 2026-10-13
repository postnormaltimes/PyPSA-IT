"""Canonical, cached-table-only producer for the approved marginal regime.

V2 identifies the complete economic roots. The unchanged lexicographic resolver
interprets them. Missing validated evidence never launches root discovery or a
solve. Review packages are provenance, never a routine input location.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
import yaml

from ..common import ROOT, sha256_file
from .canonical_results import reporting_config
from .marginal_setter_attribution import ZONES, TOLERANCE, POWER_TOLERANCE, generator_position_admissible
from .marginal_setter_resolver import IDENTITY, map_candidates
from .marginal_setter_lexicographic import ORDER, RULES, VERSION, resolve, zone_shares

CONFIG = ROOT / 'config/marginal_regime.yaml'
RECEIPT = 'MEM_MARGINAL_REGIME_RECEIPT.json'
CACHE = 'MEM_MARGINAL_REGIME_REPORT_CACHE.json'
WAITING_FOR_VALIDATED_ROOT_EVIDENCE = 'WAITING_FOR_VALIDATED_ROOT_EVIDENCE'


def config():
    return yaml.safe_load(CONFIG.read_text(encoding='utf-8'))


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2), encoding='utf-8')


def _pins(paths):
    return {str(p): sha256_file(Path(p)) for p in paths}


def _verify(pins):
    for name, digest in pins.items():
        p = Path(name)
        if not p.is_file() or sha256_file(p) != digest:
            raise RuntimeError(f'MARGINAL_REGIME_HASH_CONFLICT: {p}')


def _code_pins():
    from .uc2_postprocess import _code_hashes
    pins = {str(ROOT / p): h for p, h in _code_hashes().items()}
    pins.update(_pins([CONFIG, Path(__file__),
        ROOT/'src/mem_model/reporting/marginal_setter_lexicographic.py',
        ROOT/'src/mem_model/reporting/marginal_setter_resolver.py',
        ROOT/'src/mem_model/reporting/marginal_setter_attribution.py',
        ROOT/'src/mem_model/reporting/marginal_root_pipeline.py',
        ROOT/'src/mem_model/visualization/marginal_setter_lexicographic.py']))
    return pins


def _report_dir(year, scenario):
    from ..stage_b_uc2 import case_root
    return case_root(year, scenario) / 'REPORTING'


def _label(year, scenario):
    if scenario not in reporting_config()['scenario_order']:
        raise ValueError(f'Unsupported scenario: {scenario}')
    return f'{int(year)}_{scenario}'


def _input_paths(year, scenario, folder):
    label = _label(year, scenario)
    return [folder/f'marginal_setter_attribution_{label}.parquet',
            folder/f'marginal_setter_candidates_{label}.parquet',
            folder/f'marginal_setter_QA_{label}.json',
            folder/'FROZEN_METHODOLOGY.json']


def _source_path(value):
    path = Path(value)
    return (path if path.is_absolute() else ROOT/path).resolve()


def _validate_lp_identity(year, scenario, qa, report_dir):
    """Require the root audit and reporting prices to belong to the same LP."""
    register = report_dir/'uc2_VIS_RESULT_AUTHORITY_REGISTER.csv'
    pins = {_source_path(p): h for p, h in qa['source_hashes'].items()}
    if register.resolve() not in pins:
        raise RuntimeError('CANONICAL_REGIME_LP_IDENTITY_CONFLICT: authority register')
    entries = pd.read_csv(register)
    lp = entries.loc[entries.result_id.astype(str).str.endswith('FIXED_COMMITMENT_PRICE_LP')]
    uc = entries.loc[entries.result_id.astype(str).str.endswith('_UC_MILP')]
    if len(lp) != 1 or len(uc) != 1:
        raise RuntimeError('CANONICAL_REGIME_LP_IDENTITY_CONFLICT: nonunique result role')
    row, parent = lp.iloc[0], uc.iloc[0]
    for entry in (row, parent):
        if int(entry.year) != year or entry.scenario != scenario or entry.solved_or_unsolved != 'SOLVED':
            raise RuntimeError('CANONICAL_REGIME_LP_IDENTITY_CONFLICT: case or state')
    network, receipt, verification = (_source_path(row[k]) for k in ('network_path', 'receipt', 'manifest'))
    required = [network, receipt, verification, _source_path(parent.receipt), _source_path(parent.manifest)]
    if any(p not in pins for p in required):
        raise RuntimeError('CANONICAL_REGIME_LP_IDENTITY_CONFLICT: unpinned current LP lineage')
    solved, verified = _read(receipt), _read(verification)
    if (solved.get('status') != 'PASS' or verified.get('status') != 'PASS' or
            solved.get('kind') != 'FIXED_COMMITMENT_PRICE_LP' or
            solved.get('termination_condition') != 'optimal' or
            solved.get('year') != year or solved.get('scenario') != scenario or
            verified.get('year') != year or verified.get('scenario') != scenario or
            solved.get('snapshots') != reporting_config()['require_full_chronology_hours'] or
            not (qa.get('job_id') == row.result_id == solved.get('job_id') == verified.get('job_id')) or
            not (pins[network] == row.network_sha256 == solved.get('solved_sha256') == verified.get('solved_sha256')) or
            not (qa.get('fixed_commitment_milp_sha256') == parent.network_sha256 ==
                 solved.get('fixed_commitment_milp_sha256') == verified.get('milp_solved_sha256'))):
        raise RuntimeError('CANONICAL_REGIME_LP_IDENTITY_CONFLICT: solve or parent identity')


def _validate_root_audit(table, audit, year, scenario):
    """Validate saved selected roots without discovering or selecting resources."""
    keys = ['snapshot', 'target_zone']
    counts = ['candidate_count', 'local_candidate_count', 'remote_candidate_count',
              'root_count', 'root_group_count', 'candidate_technology_count', 'remote_root_zone_count']
    required_table = {'snapshot', 'zone', 'primary_attribution_class', *counts}
    required_audit = {'model_year', 'scenario', *keys, *IDENTITY, 'remote', 'coupling_path',
                      'position', 'p', 'cost', 'opportunity', 'recon', 'residual', 'lower',
                      'upper', 'gradient', 'efficiency', 'objective_weight', 'operating_lower',
                      'operating_upper', 'native_power', 'ramp_up_dual', 'ramp_down_dual', 'fixed_status'}
    if required_table-set(table) or required_audit-set(audit):
        raise RuntimeError('INCOMPLETE_VALIDATED_ROOT_EVIDENCE: required columns')
    if audit.empty or not audit.model_year.eq(year).all() or not audit.scenario.eq(scenario).all():
        raise RuntimeError('INCOMPLETE_VALIDATED_ROOT_EVIDENCE: case metadata or empty audit')
    if audit[[*keys, *IDENTITY, 'position', 'coupling_path']].isna().any().any():
        raise RuntimeError('INCOMPLETE_VALIDATED_ROOT_EVIDENCE: missing root identity')
    if any(not audit[field].map(lambda value: isinstance(value, str) and bool(value.strip())).all()
           for field in (*IDENTITY, 'position')):
        raise RuntimeError('INCOMPLETE_VALIDATED_ROOT_EVIDENCE: invalid root identity')
    if not audit.remote.map(lambda value: isinstance(value, (bool, np.bool_))).all():
        raise RuntimeError('INCOMPLETE_VALIDATED_ROOT_EVIDENCE: invalid scope flag')
    path_lengths = {}
    for path in audit.coupling_path.unique():
        try:
            steps = json.loads(path)
        except (TypeError, ValueError) as error:
            raise RuntimeError('INCOMPLETE_VALIDATED_ROOT_EVIDENCE: invalid coupling path') from error
        if not isinstance(steps, list) or any(not isinstance(step, str) or not step for step in steps):
            raise RuntimeError('INCOMPLETE_VALIDATED_ROOT_EVIDENCE: invalid coupling path')
        path_lengths[path] = len(steps)
    lengths = audit.coupling_path.map(path_lengths)
    normalized_zone = audit.zone.replace({'CNOR': 'CNORD'})
    if (lengths.gt(0).ne(audit.remote).any() or
            normalized_zone.eq(audit.target_zone).eq(audit.remote).any()):
        raise RuntimeError('INCOMPLETE_VALIDATED_ROOT_EVIDENCE: root scope or path')
    numeric = ['p', 'cost', 'recon', 'residual', 'lower', 'upper', 'gradient', 'efficiency',
               'objective_weight', 'operating_lower', 'operating_upper', 'native_power',
               'ramp_up_dual', 'ramp_down_dual', 'fixed_status']
    if (not np.isfinite(audit[numeric].to_numpy(dtype=float)).all() or
            audit.efficiency.le(0).any() or audit.objective_weight.le(0).any() or
            audit.residual.abs().gt(TOLERANCE).any() or
            audit[['lower', 'upper']].abs().gt(TOLERANCE).any().any() or
            not generator_position_admissible(audit.native_power, audit.operating_lower, audit.operating_upper).all()):
        raise RuntimeError('INCOMPLETE_VALIDATED_ROOT_EVIDENCE: nonfinite or invalid KKT evidence')
    # Validate explicit families using only the mapping columns; retain the
    # original potentially large primal/dual audit without additional copies.
    mapped = map_candidates(audit[['technology', 'carrier', 'component']])
    storage = mapped.root_family.isin(['PHS', 'BESS', 'Reservoir / pondage hydro'])
    demand = mapped.root_family.eq('P2X')
    intertemporal = storage | demand
    if ((audit.loc[intertemporal, 'native_power'] <= audit.loc[intertemporal, 'operating_lower']+POWER_TOLERANCE).any() or
            (audit.loc[intertemporal, 'native_power'] >= audit.loc[intertemporal, 'operating_upper']-POWER_TOLERANCE).any()):
        raise RuntimeError('INCOMPLETE_VALIDATED_ROOT_EVIDENCE: intertemporal operating position')
    if (not audit.loc[storage, 'mechanism'].eq('STORAGE_HYDRO_OPPORTUNITY_VALUE').all() or
            not audit.loc[demand, 'mechanism'].eq('P2X_FLEXIBLE_DEMAND').all() or
            not audit.loc[~intertemporal, 'mechanism'].isin(['DIRECT_MARGINAL_TECHNOLOGY', 'UC_RAMP_CONSTRAINED']).all() or
            not audit.loc[storage, 'position'].isin(['charging', 'discharging']).all()):
        raise RuntimeError('INCOMPLETE_VALIDATED_ROOT_EVIDENCE: resource mechanism or position')
    if not np.isfinite(audit.loc[intertemporal, 'opportunity'].to_numpy(dtype=float)).all():
        raise RuntimeError('INCOMPLETE_VALIDATED_ROOT_EVIDENCE: missing opportunity or shadow')
    selected = table.rename(columns={'zone': 'target_zone'}).set_index(keys)
    observed = audit.groupby(keys, sort=False)
    coverage = pd.MultiIndex.from_frame(audit[keys]).unique()
    if len(coverage.difference(selected.index)) or len(selected.index.difference(coverage)):
        raise RuntimeError('INCOMPLETE_VALIDATED_ROOT_EVIDENCE: zone-hour coverage')
    if observed.remote.nunique().gt(1).any():
        raise RuntimeError('INCOMPLETE_VALIDATED_ROOT_EVIDENCE: mixed selected scopes')
    actual = observed.agg(candidate_count=('asset_id', 'size'),
        candidate_technology_count=('technology', 'nunique'), remote=('remote', 'first'),
        mechanism=('mechanism', 'first'), root_zone_count=('zone', 'nunique'))
    actual['local_candidate_count'] = actual.candidate_count.where(~actual.remote, 0)
    actual['remote_candidate_count'] = actual.candidate_count.where(actual.remote, 0)
    actual['remote_root_zone_count'] = actual.root_zone_count.where(actual.remote, 0)
    actual['root_count'] = audit.drop_duplicates(keys+['component', 'zone', 'asset_id']).groupby(keys).size()
    groups = audit[keys+['mechanism', 'technology']].assign(collapse_zone=audit.zone.where(audit.remote, ''))
    actual['root_group_count'] = groups.drop_duplicates(keys+['mechanism', 'technology', 'collapse_zone']).groupby(keys).size()
    klass = actual.mechanism.where(actual.root_group_count.eq(1), 'MULTIPLE_CANDIDATES').where(~actual.remote, 'MARKET_COUPLING')
    actual = actual.reindex(selected.index)
    if ((selected[counts].to_numpy() != actual[counts].to_numpy()).any() or
            not selected.primary_attribution_class.eq(klass.reindex(selected.index)).all()):
        raise RuntimeError('INCOMPLETE_VALIDATED_ROOT_EVIDENCE: selected-root counts or hierarchy')
    prices = audit[keys+['recon', 'residual']].merge(selected[['zonal_price_EUR_MWh']],
        left_on=keys, right_index=True, validate='many_to_one')
    delta = prices.recon-prices.zonal_price_EUR_MWh
    if delta.abs().gt(TOLERANCE).any() or prices.residual.sub(delta).abs().gt(TOLERANCE).any():
        raise RuntimeError('INCOMPLETE_VALIDATED_ROOT_EVIDENCE: target price reconstruction')


def _validate_source_record(year, scenario, paths, report_dir, *, allowed_statuses):
    tp, ap, qp, fp = paths
    qa = _read(qp)
    if qa['status'] not in allowed_statuses:
        raise RuntimeError('UNVALIDATED_MARGINAL_ROOTS')
    if not qa.get('methodology_frozen') or qa.get('solver_invocations') != 0:
        raise RuntimeError('MARGINAL_ROOT_METHOD_NOT_FROZEN')
    if sha256_file(fp) != qa['frozen_methodology_sha256']:
        raise RuntimeError('FROZEN_ROOT_METHODOLOGY_HASH_CONFLICT')
    from . import marginal_setter_attribution as engine
    freeze = _read(fp)
    if (freeze.get('status') != 'FROZEN_AFTER_BASE_PASS' or freeze.get('methodology') != engine.RULES or
            qa.get('methodology') != freeze.get('methodology') or
            not (freeze.get('implementation_sha256') == qa.get('implementation_sha256') == sha256_file(Path(engine.__file__)))):
        raise RuntimeError('FROZEN_ROOT_METHODOLOGY_CONFLICT')
    for p in (tp, ap):
        if sha256_file(p) != qa['artifact_hashes'][p.name]:
            raise RuntimeError('VALIDATED_ROOT_TABLE_HASH_CONFLICT')
    # Verify the original accepted price/network/receipt lineage by byte hashes;
    # .nc contents are never opened as a network or dataset.
    _verify(qa['source_hashes'])
    _validate_lp_identity(year, scenario, qa, report_dir)
    price_path = report_dir/'fixed_commitment_prices_hourly.csv'
    if qa['source_hashes'].get(str(price_path)) != sha256_file(price_path):
        raise RuntimeError('CANONICAL_PRICE_SOURCE_CONFLICT')
    return qa


def _validate_pending_interpretation(year, scenario, paths, report_dir):
    """Keep supported-source attribution separate from an unresolved regime."""
    qa = _validate_source_record(year, scenario, paths, report_dir,
        allowed_statuses=('KKT_VALID_REGIME_INTERPRETATION_REQUIRED',))
    from .marginal_setter_attribution import validate_structure
    table = pd.read_parquet(paths[0])
    validate_structure(table)
    if not table.model_year.eq(year).all() or not table.scenario.eq(scenario).all():
        raise RuntimeError('CANONICAL_REGIME_CHRONOLOGY_OR_LABEL_CONFLICT')
    if not qa.get('regime_interpretation_required'):
        raise RuntimeError('MISSING_UNRESOLVED_REGIME_REASON')
    return qa


def _validate_inputs(year, scenario, paths, report_dir):
    tp, ap, qp, fp = paths
    _validate_source_record(year, scenario, paths, report_dir,
        allowed_statuses=('PASS_BASE_SEMANTIC_GATE', 'PASS_FROZEN_METHOD_ATTRIBUTION'))
    price_path = report_dir/'fixed_commitment_prices_hourly.csv'
    table = pd.read_parquet(tp)
    n = reporting_config()['require_full_chronology_hours']
    if (len(table) != n*len(ZONES) or set(table.zone) != set(ZONES) or
            table.duplicated(['snapshot', 'zone']).any() or
            not table.groupby('zone').size().eq(n).all() or
            not table.model_year.eq(year).all() or not table.scenario.eq(scenario).all()):
        raise RuntimeError('CANONICAL_REGIME_CHRONOLOGY_OR_LABEL_CONFLICT')
    prices = pd.read_csv(price_path, index_col=0, parse_dates=True).rename(columns={'CNOR':'CNORD'})
    matrix = table.pivot(index='snapshot', columns='zone', values='zonal_price_EUR_MWh').sort_index()
    if not pd.DatetimeIndex(matrix.index).equals(prices.index):
        raise RuntimeError('CANONICAL_REGIME_PRICE_CHRONOLOGY_CONFLICT')
    error = float(np.max(np.abs(matrix[list(ZONES)].to_numpy()-prices[list(ZONES)].to_numpy())))
    if not np.isfinite(error) or error > TOLERANCE:
        raise RuntimeError('CANONICAL_REGIME_PRICE_VALUE_CONFLICT')
    audit = pd.read_parquet(ap)
    _validate_root_audit(table, audit, year, scenario)
    if not np.isfinite(table.represented_hours.to_numpy(dtype=float)).all() or table.represented_hours.le(0).any():
        raise RuntimeError('INCOMPLETE_VALIDATED_ROOT_EVIDENCE: represented time')
    return table, audit, error


def produce(model_year, scenario, validated_root_input=None, reporting_output=None,
            *, approved_review=None):
    """Produce one canonical case; optional one-time promotion copies approved files.

    Input directory contains the V2 table, candidate Parquet, QA and frozen
    methodology. External inputs are copied into canonical storage first.
    Subsequent calls use only that canonical copy. No candidate rediscovery.
    """
    from .uc2_postprocess import no_solver_calls
    cfg = config(); label = _label(model_year, scenario)
    report = Path(reporting_output or _report_dir(model_year, scenario)).resolve()
    source = Path(validated_root_input or report/cfg['root_input']).resolve()
    paths = _input_paths(model_year, scenario, source)
    if not all(p.is_file() for p in paths):
        if validated_root_input is not None:
            raise RuntimeError('REQUIRED_VALIDATED_ROOT_INPUT_MISSING')
        return dict(status=WAITING_FOR_VALIDATED_ROOT_EVIDENCE, model_year=model_year,
                    scenario=scenario, solver_invocations=0, network_opens=0)
    if _read(paths[2]).get('status') == 'KKT_VALID_REGIME_INTERPRETATION_REQUIRED':
        qa = _validate_pending_interpretation(model_year, scenario, paths, report)
        return dict(status='WAITING_FOR_SUPPORTED_ROOT_INTERPRETATION', model_year=model_year,
                    scenario=scenario, reason=qa['regime_interpretation_required'],
                    solver_invocations=0, network_opens=0)
    target = report/cfg['root_input']
    with no_solver_calls() as guard:
        table, audit, price_error = _validate_inputs(model_year, scenario, paths, report)
        origin = _pins(paths)
        target.mkdir(parents=True, exist_ok=True)
        if source != target.resolve():
            for p in paths:
                dest = target/p.name
                if dest.exists() and sha256_file(dest) != sha256_file(p):
                    raise RuntimeError('CANONICAL_ROOT_REPLACEMENT_REQUIRES_REVIEW')
                if not dest.exists(): shutil.copyfile(p, dest)
        paths = _input_paths(model_year, scenario, target)
        sources = _pins(paths); sources.update(_read(paths[2])['source_hashes'])
        data = report/cfg['data_output']; figures = report/cfg['figure_output']
        data.mkdir(parents=True, exist_ok=True); figures.mkdir(parents=True, exist_ok=True)
        receipt_path = report/RECEIPT
        if receipt_path.exists():
            previous = _read(receipt_path)
            if previous['source_hashes'] == sources and previous['code_hashes'] == _code_pins():
                _verify(previous['artifact_hashes'])
                return previous
        result, evidence = resolve(table, audit)  # exact approved classifier
        shares = zone_shares(result)
        if (shares.sum(axis=1)-100).abs().max() > 1e-10:
            raise RuntimeError('CANONICAL_REGIME_SHARE_RECONCILIATION_FAIL')
        names = [f'lexicographic_regime_attribution_{label}.parquet',
                 f'lexicographic_regime_attribution_{label}.csv',
                 f'lexicographic_regime_complete_root_evidence_{label}.parquet',
                 f'lexicographic_regime_zonal_shares_{label}.csv',
                 f'lexicographic_regime_residual_{label}.csv']
        promotion = {}
        if approved_review is not None:
            review = Path(approved_review); prior = pd.read_parquet(review/names[0])
            pd.testing.assert_frame_equal(result, prior, check_exact=True)
            pd.testing.assert_frame_equal(evidence, pd.read_parquet(review/names[2]), check_exact=True)
            for name in names:
                shutil.copyfile(review/name, data/name)
            for ext in ('png','svg'):
                name = f'lexicographic_regime_shares_{label}.{ext}'
                shutil.copyfile(review/'figures'/name, figures/name)
            promotion = _pins([review/n for n in names]+list((review/'figures').glob(f'*_{label}.*')))
        else:
            result.to_parquet(data/names[0], index=False);result.to_csv(data/names[1], index=False)
            evidence.to_parquet(data/names[2], index=False);shares.to_csv(data/names[3],index_label='zone')
            residual = result.loc[result.final_regime_category.eq('Market coupling / mixed')].groupby(
                ['resolution_scope','primary_attribution_class','valid_detailed_root_families',
                 'valid_regime_families','domestic_decision_families','residual_reason']).represented_hours.sum().rename('zone_hours').reset_index()
            residual['share_of_all_zone_hours_percent'] = residual.zone_hours/result.represented_hours.sum()*100
            residual.to_csv(data/names[4], index=False)
        rules = {**RULES, 'activation':'CANONICAL_ROUTINE', 'approval':'Validated LEXICOGRAPHIC_REGIME_V1 reporting method'}
        _write(data/'METHODOLOGY.json', rules)
        qa = dict(status='PASS_CANONICAL_MARGINAL_REGIME', version=VERSION, model_year=model_year,
            scenario=scenario, rows=len(result), snapshots=result.snapshot.nunique(), zones=list(ZONES),
            accepted_price_max_error_EUR_MWh=price_error, original_V2_fields_and_roots_unchanged=True,
            share_sum_max_error_percent=float((shares.sum(axis=1)-100).abs().max()),
            source_hashes=sources, original_input_provenance=origin, approved_review_provenance=promotion,
            solver_invocations=guard['solver_invocations'], network_opens=0, routine_activation=True,
            artifact_hashes={p.name:sha256_file(p) for p in data.glob('*') if p.suffix in ('.csv','.parquet')})
        _write(data/f'lexicographic_regime_QA_{label}.json', qa)
        from ..visualization.marginal_setter_lexicographic import render_review
        render_review(model_year,figures,scenario_outputs={scenario:data},
                      figure_outputs={scenario:figures},palette=cfg['category_colors'],canonical=True)
        metadata = dict(indicator=cfg['indicator'],methodology=cfg['methodology'],status='CANONICAL / ROUTINE',
            model_year=model_year,scenario=scenario,metric=cfg['metric'],heatmaps_routine=False,
            figure_files=[str(figures/f'lexicographic_regime_shares_{label}.{e}') for e in ('png','svg')],
            underlying_data=[str(data/n) for n in names],root_evidence=[str(p) for p in paths],
            approved_geometry_palette_and_values_preserved=True,
            promotion_annotation_change='Canonical authority footer/subtitle; review originals retained')
        _write(figures/'MARGINAL_REGIME_METADATA.json',metadata)
        artifacts=[p for folder in (data,figures) for p in folder.rglob('*') if p.is_file()]
        receipt=dict(status='PASS_CANONICAL_MARGINAL_REGIME',methodology=cfg['methodology'],
            model_year=model_year,scenario=scenario,source_hashes=sources,code_hashes=_code_pins(),
            artifact_hashes=_pins(artifacts),solver_invocations=guard['solver_invocations'],network_opens=0,
            routine_activation=True,approved_review_provenance=promotion,original_input_provenance=origin)
        _write(receipt_path,receipt)
    return receipt


def compare(model_year, case_reports=None, comparison_output=None, *, approved_review=None):
    """Add only the regime comparison once all configured scenarios are available."""
    cfg=config(); scenarios=reporting_config()['scenario_order']
    reports={s:Path((case_reports or {}).get(s,_report_dir(model_year,s))).resolve() for s in scenarios}
    if not all((p/RECEIPT).is_file() for p in reports.values()):
        return dict(status='WAITING_FOR_ALL_SCENARIO_REGIMES',model_year=model_year)
    from ..stage_b_uc2 import case_root
    output=Path(comparison_output or case_root(model_year,scenarios[0]).parent/cfg['comparison_output']).resolve()
    output.mkdir(parents=True,exist_ok=True)
    sources=_pins([p/RECEIPT for p in reports.values()])
    receipt_path=output/'MEM_MARGINAL_REGIME_COMPARISON_RECEIPT.json'
    if receipt_path.exists():
        previous=_read(receipt_path)
        if previous['source_hashes']==sources and previous['code_hashes']==_code_pins():
            _verify(previous['artifact_hashes']);return previous
    national={}; folders={}
    for s,p in reports.items():
        q=_read(p/RECEIPT);_verify(q['artifact_hashes']);_verify(q['source_hashes'])
        folders[s]=p/cfg['data_output']
        t=pd.read_parquet(folders[s]/f'lexicographic_regime_attribution_{model_year}_{s}.parquet')
        national[s]=t.groupby('final_regime_category').represented_hours.sum().reindex(ORDER,fill_value=0)/t.represented_hours.sum()*100
    matrix=pd.DataFrame(national).reindex(index=ORDER,columns=scenarios)
    cp=output/f'LEXICOGRAPHIC_REGIME_NATIONAL_SHARES_{model_year}.csv'
    if approved_review is not None:
        review=Path(approved_review)
        pd.testing.assert_frame_equal(matrix,pd.read_csv(review/cp.name,index_col=0),check_exact=False,atol=1e-12,rtol=0,check_names=False)
        shutil.copyfile(review/cp.name,cp)
        (output/'figures').mkdir(exist_ok=True)
        for ext in ('png','svg'):
            name=f'lexicographic_regime_comparison_{model_year}.{ext}'
            shutil.copyfile(review/'figures'/name,output/'figures'/name)
    from ..visualization.marginal_setter_lexicographic import render_review
    render_review(model_year,output,scenario_outputs=folders,palette=cfg['category_colors'],canonical=True,only_comparison=True)
    _write(output/'MARGINAL_REGIME_METADATA.json',dict(status='CANONICAL / ROUTINE',model_year=model_year,
        scenarios=scenarios,methodology=cfg['methodology'],metric=cfg['metric'],heatmaps_routine=False,
        underlying_data=str(cp),approved_geometry_palette_and_values_preserved=True,
        promotion_annotation_change='Canonical authority footer; review originals retained'))
    receipt=dict(status='PASS_CANONICAL_MARGINAL_REGIME_COMPARISON',model_year=model_year,
        scenarios=scenarios,source_hashes=sources,code_hashes=_code_pins(),
        artifact_hashes=_pins([p for p in output.rglob('*') if p.is_file() and p!=receipt_path]),
        solver_invocations=0,network_opens=0,routine_activation=True)
    _write(receipt_path,receipt);return receipt


def install_report_cache(year,scenario,report_result):
    """Pin the accepted report, including later approved map corrections.

    Legacy R1 hashes precede those corrections. This additive receipt prevents
    the new report hook from re-rendering settled figures solely due to its CLI
    file hash change. All original reporting receipts remain untouched.
    """
    from .uc2_postprocess import _protected_sources
    report=_report_dir(year,scenario);cfg=config()
    excluded=(report/cfg['root_input'],report/cfg['data_output'],report/cfg['figure_output'])
    # Availability indexes change when sibling scenarios finish. Their own
    # validator binds current sources; they are not immutable core-report data.
    mutable_indexes = (CACHE, RECEIPT, 'OUTPUT_REGISTER.csv', 'OUTPUT_REGISTER.json')
    files=[p for p in report.rglob('*') if p.is_file() and p.name not in mutable_indexes
           and not any(p.is_relative_to(x) for x in excluded)]
    _write(report/CACHE,dict(status='ACCEPTED_REPORT_CACHE',model_year=year,scenario=scenario,
        reporting_result=report_result,code_hashes=_code_pins(),artifact_hashes=_pins(files),
        protected_source_hashes=_protected_sources(year,scenario)))


def cached_report(year,scenario):
    report=_report_dir(year,scenario);path=report/CACHE
    if not path.is_file():return None
    q=_read(path)
    if q['code_hashes']!=_code_pins():
        raise RuntimeError('ACCEPTED_REPORT_CODE_CHANGED_REVIEW_REQUIRED')
    _verify(q['artifact_hashes']);_verify(q['protected_source_hashes'])
    return q['reporting_result']


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--year',type=int,required=True)
    parser.add_argument('--scenario',choices=reporting_config()['scenario_order'],required=True)
    parser.add_argument('--validated-roots',type=Path)
    parser.add_argument('--report-dir',type=Path)
    args=parser.parse_args(argv)
    result=produce(args.year,args.scenario,args.validated_roots,args.report_dir)
    comparison=compare(args.year) if args.report_dir is None else None
    print(json.dumps(dict(regime=result,comparison=comparison),indent=2))


if __name__=='__main__':main()

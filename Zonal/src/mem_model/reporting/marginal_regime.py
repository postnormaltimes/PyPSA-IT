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
from .marginal_setter_attribution import ZONES, TOLERANCE
from .marginal_setter_lexicographic import ORDER, RULES, VERSION, resolve, zone_shares

CONFIG = ROOT / 'config/marginal_regime.yaml'
RECEIPT = 'MEM_MARGINAL_REGIME_RECEIPT.json'
CACHE = 'MEM_MARGINAL_REGIME_REPORT_CACHE.json'


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


def _validate_inputs(year, scenario, paths, report_dir):
    tp, ap, qp, fp = paths
    qa = _read(qp)
    if qa['status'] not in ('PASS_BASE_SEMANTIC_GATE', 'PASS_FROZEN_METHOD_ATTRIBUTION'):
        raise RuntimeError('UNVALIDATED_MARGINAL_ROOTS')
    if not qa.get('methodology_frozen') or qa.get('solver_invocations') != 0:
        raise RuntimeError('MARGINAL_ROOT_METHOD_NOT_FROZEN')
    if sha256_file(fp) != qa['frozen_methodology_sha256']:
        raise RuntimeError('FROZEN_ROOT_METHODOLOGY_HASH_CONFLICT')
    for p in (tp, ap):
        if sha256_file(p) != qa['artifact_hashes'][p.name]:
            raise RuntimeError('VALIDATED_ROOT_TABLE_HASH_CONFLICT')
    # Verify the original accepted price/network/receipt lineage by byte hashes;
    # .nc contents are never opened as a network or dataset.
    _verify(qa['source_hashes'])
    price_path = report_dir/'fixed_commitment_prices_hourly.csv'
    if qa['source_hashes'].get(str(price_path)) != sha256_file(price_path):
        raise RuntimeError('CANONICAL_PRICE_SOURCE_CONFLICT')
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
    if error > TOLERANCE:
        raise RuntimeError('CANONICAL_REGIME_PRICE_VALUE_CONFLICT')
    return table, pd.read_parquet(ap), error


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
        return dict(status=cfg['missing_validated_roots'], model_year=model_year,
                    scenario=scenario, solver_invocations=0, network_opens=0)
    target = report/cfg['root_input']; target.mkdir(parents=True, exist_ok=True)
    with no_solver_calls() as guard:
        table, audit, price_error = _validate_inputs(model_year, scenario, paths, report)
        origin = _pins(paths)
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
        rules = {**RULES, 'activation':'CANONICAL_ROUTINE', 'approval':'Validated LEXICOGRAPHIC_REGIME_V1'}
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
    files=[p for p in report.rglob('*') if p.is_file() and p.name not in (CACHE,RECEIPT)
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

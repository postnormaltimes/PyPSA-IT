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
from .portable_paths import portable_name, receipt_path, verify_pins

CONFIG = ROOT / 'config/marginal_regime.yaml'
RECEIPT = 'MEM_MARGINAL_REGIME_RECEIPT.json'
CACHE = 'MEM_MARGINAL_REGIME_REPORT_CACHE.json'
CORE_RECEIPT = 'MEM_MARGINAL_REGIME_CORE_RECEIPT.json'
PRESENTATION_RECEIPT = 'MEM_MARGINAL_REGIME_PRESENTATION_RECEIPT.json'


def _receipt_name(render_figures=False):
    return PRESENTATION_RECEIPT if render_figures else CORE_RECEIPT


def _cache_name(presentation=False):
    return 'MEM_UC2_PRESENTATION_REPORT_CACHE.json' if presentation else 'MEM_UC2_CORE_REPORT_CACHE.json'


def config():
    return yaml.safe_load(CONFIG.read_text(encoding='utf-8'))


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2), encoding='utf-8')


def _pins(paths):
    return {portable_name(p, root=ROOT): sha256_file(Path(p)) for p in paths}


def _verify(pins):
    verify_pins(pins, config().get('historical_root_map', {}), root=ROOT)


def _code_pins(*, presentation=False):
    from .uc2_postprocess import _code_hashes
    pins = _code_hashes(presentation=presentation)
    pins.update(_pins([CONFIG, Path(__file__),
        ROOT/'src/mem_model/reporting/marginal_setter_lexicographic.py',
        ROOT/'src/mem_model/reporting/marginal_setter_resolver.py',
        ROOT/'src/mem_model/reporting/marginal_setter_attribution.py']))
    if presentation:
        pins.update(_pins([ROOT/'src/mem_model/visualization/marginal_setter_lexicographic.py']))
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
    price_pins = [digest for name, digest in qa['source_hashes'].items()
                  if receipt_path(name, config().get('historical_root_map', {}), root=ROOT).resolve() == price_path.resolve()]
    if price_pins != [sha256_file(price_path)]:
        raise RuntimeError('CANONICAL_PRICE_SOURCE_CONFLICT')
    from .. import stage_b_uc2 as uc2
    if uc2.ACTIVE_VARIANT.get() == 'final_v1':
        # Final roots must have the final-family price job and its own UC parent.
        # Identical labels or price values cannot promote legacy UC2 evidence.
        price_receipt = _read(uc2.job_paths(year, scenario, 'FIXED_COMMITMENT_PRICE_LP')['receipt'])
        if (qa.get('job_id') != uc2.job_id(year, scenario, 'FIXED_COMMITMENT_PRICE_LP') or
                qa.get('fixed_commitment_milp_sha256') != price_receipt.get('fixed_commitment_milp_sha256')):
            raise RuntimeError('MARGINAL_ROOT_FINAL_LINEAGE_CONFLICT')
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
            *, approved_review=None, render_figures=False):
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
        data.mkdir(parents=True, exist_ok=True)
        if render_figures:
            figures.mkdir(parents=True, exist_ok=True)
        receipt_path = report/_receipt_name(render_figures)
        if receipt_path.exists():
            previous = _read(receipt_path)
            if previous['source_hashes'] == sources and previous['code_hashes'] == _code_pins(presentation=render_figures):
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
            if render_figures:
                for ext in ('png','svg'):
                    name = f'lexicographic_regime_shares_{label}.{ext}'
                    shutil.copyfile(review/'figures'/name, figures/name)
            promotion = _pins([review/n for n in names])
        else:
            result.to_parquet(data/names[0], index=False);result.to_csv(data/names[1], index=False)
            evidence.to_parquet(data/names[2], index=False);shares.to_csv(data/names[3],index_label='zone')
            residual = result.loc[result.final_regime_category.eq('Market coupling / mixed')].groupby(
                ['resolution_scope','primary_attribution_class','valid_detailed_root_families',
                 'valid_regime_families','domestic_decision_families','residual_reason']).represented_hours.sum().rename('zone_hours').reset_index()
            residual['share_of_all_zone_hours_percent'] = residual.zone_hours/result.represented_hours.sum()*100
            residual.to_csv(data/names[4], index=False)
        rules = {**RULES, 'activation':'CANONICAL_ROUTINE', 'approval':'Explicit user approval of LEXICOGRAPHIC_REGIME_V1'}
        _write(data/'METHODOLOGY.json', rules)
        qa = dict(status='PASS_CANONICAL_MARGINAL_REGIME', version=VERSION, model_year=model_year,
            scenario=scenario, rows=len(result), snapshots=result.snapshot.nunique(), zones=list(ZONES),
            accepted_price_max_error_EUR_MWh=price_error, original_V2_fields_and_roots_unchanged=True,
            share_sum_max_error_percent=float((shares.sum(axis=1)-100).abs().max()),
            source_hashes=sources, original_input_provenance=origin, approved_review_provenance=promotion,
            solver_invocations=guard['solver_invocations'], network_opens=0, routine_activation=True,
            artifact_hashes={p.name:sha256_file(p) for p in data.glob('*') if p.suffix in ('.csv','.parquet')})
        _write(data/f'lexicographic_regime_PORTABLE_QA_{label}.json', qa)
        if render_figures:
            # The accepted renderer's original QA contract is preserved. A
            # relocated successor only creates this contract when it is absent.
            renderer_qa = data/f'lexicographic_regime_QA_{label}.json'
            if not renderer_qa.exists():
                _write(renderer_qa, qa)
            from ..visualization.marginal_setter_lexicographic import render_review
            render_review(model_year,figures,scenario_outputs={scenario:data},
                          figure_outputs={scenario:figures},palette=cfg['category_colors'],canonical=True)
        metadata = dict(indicator=cfg['indicator'],methodology=cfg['methodology'],status='CANONICAL / ROUTINE',
            model_year=model_year,scenario=scenario,metric=cfg['metric'],heatmaps_routine=False,
            figure_files=[str(figures/f'lexicographic_regime_shares_{label}.{e}') for e in ('png','svg')],
            underlying_data=[str(data/n) for n in names],root_evidence=[str(p) for p in paths],
            approved_geometry_palette_and_values_preserved=True,
            promotion_annotation_change='Canonical authority footer/subtitle; review originals retained')
        if render_figures:
            _write(figures/'MARGINAL_REGIME_METADATA.json',metadata)
        artifacts=[p for folder in ((data,figures) if render_figures else (data,)) for p in folder.rglob('*') if p.is_file()]
        receipt=dict(status='PASS_CANONICAL_MARGINAL_REGIME',methodology=cfg['methodology'],
            model_year=model_year,scenario=scenario,source_hashes=sources,code_hashes=_code_pins(presentation=render_figures),
            artifact_hashes=_pins(artifacts),solver_invocations=guard['solver_invocations'],network_opens=0,
            routine_activation=True,presentation_requested=render_figures,
            approved_review_provenance=promotion,original_input_provenance=origin)
        _write(receipt_path,receipt)
    return receipt


def compare(model_year, case_reports=None, comparison_output=None, *, approved_review=None, render_figures=False):
    """Add only the regime comparison once all configured scenarios are available."""
    cfg=config(); scenarios=reporting_config()['scenario_order']
    reports={s:Path((case_reports or {}).get(s,_report_dir(model_year,s))).resolve() for s in scenarios}
    case_receipt = _receipt_name(render_figures)
    if not all((p/case_receipt).is_file() for p in reports.values()):
        return dict(status='WAITING_FOR_ALL_SCENARIO_REGIMES',model_year=model_year)
    from ..stage_b_uc2 import case_root
    output=Path(comparison_output or case_root(model_year,scenarios[0]).parent/
                (cfg['comparison_output'] if render_figures else cfg['core_comparison_output'])).resolve()
    output.mkdir(parents=True,exist_ok=True)
    sources=_pins([p/case_receipt for p in reports.values()])
    receipt_path=output/'MEM_MARGINAL_REGIME_PORTABLE_COMPARISON_RECEIPT.json'
    if receipt_path.exists():
        previous=_read(receipt_path)
        if previous['source_hashes']==sources and previous['code_hashes']==_code_pins(presentation=render_figures):
            _verify(previous['artifact_hashes']);return previous
    national={}; folders={}
    for s,p in reports.items():
        q=_read(p/case_receipt);_verify(q['artifact_hashes']);_verify(q['source_hashes'])
        folders[s]=p/cfg['data_output']
        t=pd.read_parquet(folders[s]/f'lexicographic_regime_attribution_{model_year}_{s}.parquet')
        national[s]=t.groupby('final_regime_category').represented_hours.sum().reindex(ORDER,fill_value=0)/t.represented_hours.sum()*100
    matrix=pd.DataFrame(national).reindex(index=ORDER,columns=scenarios)
    cp=output/f'LEXICOGRAPHIC_REGIME_NATIONAL_SHARES_{model_year}.csv'
    if approved_review is not None:
        review=Path(approved_review)
        pd.testing.assert_frame_equal(matrix,pd.read_csv(review/cp.name,index_col=0),check_exact=False,atol=1e-12,rtol=0,check_names=False)
        shutil.copyfile(review/cp.name,cp)
        if render_figures:
            (output/'figures').mkdir(exist_ok=True)
            for ext in ('png','svg'):
                name=f'lexicographic_regime_comparison_{model_year}.{ext}'
                shutil.copyfile(review/'figures'/name,output/'figures'/name)
    if render_figures:
        from ..visualization.marginal_setter_lexicographic import render_review
        render_review(model_year,output,scenario_outputs=folders,palette=cfg['category_colors'],canonical=True,only_comparison=True)
    _write(output/'MARGINAL_REGIME_METADATA.json',dict(status='CANONICAL / ROUTINE',model_year=model_year,
        scenarios=scenarios,methodology=cfg['methodology'],metric=cfg['metric'],heatmaps_routine=False,
        underlying_data=str(cp),approved_geometry_palette_and_values_preserved=True,
        promotion_annotation_change='Canonical authority footer; review originals retained'))
    receipt=dict(status='PASS_CANONICAL_MARGINAL_REGIME_COMPARISON',model_year=model_year,
        scenarios=scenarios,source_hashes=sources,code_hashes=_code_pins(presentation=render_figures),
        artifact_hashes=_pins([p for p in output.rglob('*') if p.is_file() and p!=receipt_path]),
        solver_invocations=0,network_opens=0,routine_activation=True,presentation_requested=render_figures)
    _write(receipt_path,receipt);return receipt


def install_report_cache(year,scenario,report_result,*,presentation=False):
    """Pin the accepted report, including later approved map corrections.

    Legacy R1 hashes precede those corrections. This additive receipt prevents
    the new report hook from re-rendering settled figures solely due to its CLI
    file hash change. All original reporting receipts remain untouched.
    """
    from .uc2_postprocess import _protected_sources
    report=_report_dir(year,scenario);cfg=config()
    excluded=(report/cfg['root_input'],report/cfg['data_output'],report/cfg['figure_output'])
    ignored = {CACHE,RECEIPT,CORE_RECEIPT,PRESENTATION_RECEIPT,
               _cache_name(False),_cache_name(True),'MEM_UC2_PRESENTATION_RECEIPT.json'}
    files=[p for p in report.rglob('*') if p.is_file() and p.name not in ignored
           and not any(p.is_relative_to(x) for x in excluded)
           and (presentation or ('VIS_X1' not in p.parts and 'figures' not in p.parts and 'R1' not in p.name))]
    _write(report/_cache_name(presentation),dict(status='ACCEPTED_REPORT_CACHE',model_year=year,scenario=scenario,
        presentation_requested=presentation, reporting_result=report_result,
        code_hashes=_code_pins(presentation=presentation),artifact_hashes=_pins(files),
        protected_source_hashes=_protected_sources(year,scenario)))


def cached_report(year,scenario,*,presentation=False):
    report=_report_dir(year,scenario);path=report/_cache_name(presentation)
    if not path.is_file():return None
    q=_read(path)
    if q['code_hashes']!=_code_pins(presentation=presentation):
        raise RuntimeError('ACCEPTED_REPORT_CODE_CHANGED_REVIEW_REQUIRED')
    _verify(q['artifact_hashes']);_verify(q['protected_source_hashes'])
    return q['reporting_result']


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--year',type=int,required=True)
    parser.add_argument('--scenario',choices=reporting_config()['scenario_order'],required=True)
    parser.add_argument('--validated-roots',type=Path)
    parser.add_argument('--report-dir',type=Path)
    parser.add_argument('--variant',choices=['final_v1','legacy'],default='final_v1')
    parser.add_argument('--presentation',action='store_true')
    args=parser.parse_args(argv)
    from ..stage_b_uc2 import execution_variant
    with execution_variant(None if args.variant=='legacy' else args.variant):
        result=produce(args.year,args.scenario,args.validated_roots,args.report_dir,render_figures=args.presentation)
        comparison=compare(args.year,render_figures=args.presentation) if args.report_dir is None else None
    print(json.dumps(dict(regime=result,comparison=comparison),indent=2))


if __name__=='__main__':main()

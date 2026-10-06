"""Explicit non-solving preparation QA; never dispatches a run-* action.

Read the pre-edit protected path/hash snapshot from stdin. Write preparation
evidence only under qa/stage_b/p2x_flex_v2, not production result directories.
"""
import argparse
import json
import sys
from pathlib import Path
from xml.etree import ElementTree

import pandas as pd

from mem_model import stage_b_uc2 as uc2
from mem_model.common import ROOT, dump_json, sha256_file
from mem_model.stage_b_zonal_vre import no_models_or_solves


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tests',type=Path,required=True)
    args=parser.parse_args()
    protected=json.load(sys.stdin)
    xml=ElementTree.parse(args.tests)
    suites=list(xml.getroot().iter('testsuite'))
    counts={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
    if not counts['tests'] or any(counts[k] for k in ('failures','errors','skipped')):
        raise RuntimeError('FINAL_V2_EXECUTION_TESTS_NOT_PASS')
    qa=ROOT/'qa/stage_b/p2x_flex_v2'
    preflights=[];jobs=[]
    with no_models_or_solves(), uc2.execution_variant('final_v2'):
        solver=uc2.config()['solver']
        for year,scenario in uc2.SCENARIOS:
            result=uc2.preflight(year,scenario,persist=False)
            assert result['manual_authorized'] is True and result['status']=='PASS'
            paths_by_kind={kind:uc2.job_paths(year,scenario,kind) for kind in uc2.RUN_TYPES}
            if len({p['solved'] for p in paths_by_kind.values()})!=3:
                raise RuntimeError('FINAL_V2_JOB_PATH_COLLISION')
            result.update({'result_directory':str(uc2.case_root(year,scenario).relative_to(ROOT)),
                'reference_solved_path':str(paths_by_kind[uc2.RUN_TYPES[0]]['solved'].relative_to(ROOT)),
                'uc_solved_path':str(paths_by_kind[uc2.RUN_TYPES[1]]['solved'].relative_to(ROOT)),
                'price_solved_path':str(paths_by_kind[uc2.RUN_TYPES[2]]['solved'].relative_to(ROOT)),
                'report_directory':str((uc2.case_root(year,scenario)/'REPORTING').relative_to(ROOT))})
            preflights.append(result)
            for kind,paths in paths_by_kind.items():
                if paths['solved'].exists() or paths['receipt'].exists():
                    raise RuntimeError('FINAL_V2_PREPARATION_FOUND_PRODUCTION_RESULT')
                jobs.append({'year':year,'scenario':scenario,'run_type':kind,
                    'job_id':uc2.job_id(year,scenario,kind),
                    **{key:str(path.relative_to(ROOT)) for key,path in paths.items()},
                    'input_sha256':uc2._check_input_hash(year,scenario,kind),
                    'manually_authorized':True,'automatic_successor':False})
    if len({r['solved'] for r in jobs})!=18 or len({r['job_id'] for r in jobs})!=18:
        raise RuntimeError('FINAL_V2_ALL_JOB_ISOLATION_FAIL')
    changed=[name for name,digest in protected.items()
             if not (ROOT/name).is_file() or sha256_file(ROOT/name)!=digest]
    if changed:
        raise RuntimeError('FINAL_V2_PROTECTED_ARTIFACT_CHANGED: '+str(changed))
    if (ROOT/'results/final_methodology_v2').exists():
        raise RuntimeError('FINAL_V2_PREPARATION_CREATED_RESULT_DIRECTORY')
    receipt={'status':'READY_FOR_MANUAL_FINAL_V2_BASE_EXECUTION','variant':'final_v2',
        'local_execution_lineage_valid':True,'V2_regeneration_required':False,
        'final_v2_registered':True,'six_cases_manually_enabled':True,'no_automatic_execution':True,
        'static_preflights':preflights,'prepared_jobs':18,'focused_tests':counts,'solver':solver,
        'protected_artifacts_checked':len(protected),'protected_hashes':protected,
        'protected_V1_unchanged':True,'existing_V2_network_hashes_unchanged':True,
        'production_results_created':0,'optimization_model_constructed':False,'optimizer_invocations':0}
    pd.DataFrame(preflights).to_csv(qa/'FINAL_V2_EXECUTION_PREFLIGHTS.csv',index=False)
    pd.DataFrame(jobs).to_csv(qa/'FINAL_V2_EXECUTION_JOB_REGISTRY.csv',index=False)
    path=qa/'FINAL_V2_EXECUTION_ENABLEMENT_RECEIPT.json'
    dump_json(path,receipt)
    files=[path,qa/'FINAL_V2_EXECUTION_PREFLIGHTS.csv',qa/'FINAL_V2_EXECUTION_JOB_REGISTRY.csv',
        args.tests.resolve(),qa/'FINAL_V2_LOCAL_EXECUTION_LINEAGE_CLARIFICATION.md',
        ROOT/'config/final_methodology_execution_v2.yaml',Path(__file__).resolve(),
        ROOT/'src/mem_model/stage_b_uc2.py',ROOT/'src/mem_model/final_methodology_networks.py',
        ROOT/'src/mem_model/reporting/uc2_postprocess.py',ROOT/'tests/test_final_v2_execution.py',
        ROOT/'tests/test_final_v1_reporting_variant.py',ROOT/'docs/MEM_FINAL_V2_MANUAL_EXECUTION_TRANSFER.md']
    pd.DataFrame([{'path':str(p.relative_to(ROOT)),'sha256':sha256_file(p)} for p in files]).to_csv(
        qa/'FINAL_V2_EXECUTION_ARTIFACT_MANIFEST.csv',index=False)
    print(json.dumps({k:v for k,v in receipt.items() if k not in ('protected_hashes','static_preflights')},indent=2))


if __name__=='__main__':
    main()

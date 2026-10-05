"""Portable, non-solving Path-A contract validation from a selected repository."""
from pathlib import Path
import argparse, csv, hashlib, json, os, sys
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()
def validate(repo):
    repo=repo.resolve();sys.path.insert(0,str(repo/'src'))
    from mem_model.common import ROOT
    if ROOT.resolve()!=repo:raise RuntimeError('MODEL_IMPORT_ROOT_IS_NOT_SELECTED_REPOSITORY')
    import pypsa
    from mem_model import stage_b_uc2 as runner
    from mem_model.stage_b_zonal_vre import no_models_or_solves
    observed=[];blocked=[]
    environment=Path(sys.prefix).resolve()
    def audit(event,args):
        if event!='open' or not args or not isinstance(args[0],(str,bytes,os.PathLike)):return
        p=Path(os.fsdecode(args[0])).resolve()
        if p.is_relative_to(repo.parent) and not (p.is_relative_to(repo) or p.is_relative_to(environment)):
            blocked.append(str(p));raise RuntimeError('UNRELATED_PROJECT_ACCESS_BLOCKED')
        observed.append(str(p))
    sys.addaudithook(audit)
    rows=list(csv.DictReader((repo/'inputs/manifests/data_package.csv').open(encoding='utf-8')))
    assets=[r for r in rows if 'PATH_A' in r['scope'].split('|')]
    hashes=[]
    for r in assets:
        p=repo/r['destination']
        if not p.is_file() or p.stat().st_size!=int(r['size_bytes']) or sha(p)!=r['sha256']:raise RuntimeError('CANONICAL_PATH_A_HASH_MISMATCH: '+r['destination'])
        hashes.append({'destination':r['destination'],'status':'PASS'})
    cfg=repo/'config/final_methodology_execution_v1.yaml'
    from mem_model.common import load_yaml
    authorization=load_yaml(cfg)['manual_authorization']
    if any(authorization[f'{y}_{s}'] is not True for y in (2040,2050) for s in ('Slow','Base','High')):raise RuntimeError('FINAL_CASE_MANUAL_AUTHORIZATION_MISMATCH')
    cases=[]
    with no_models_or_solves(),runner.execution_variant('final_v1'):
        for y in (2040,2050):
            for s in ('Slow','Base','High'):
                r=runner.preflight(y,s,persist=False)
                if r['status']!='PASS':raise RuntimeError('PATH_A_PREFLIGHT_FAILED')
                cases.append({'year':y,'scenario':s,'status':'PASS','preflight':r})
    from mem_model.reporting import canonical_results,uc2_postprocess,marginal_regime
    # Import-only core checks do not use presentation toolkit or solver objects.
    source_inspection={'core_generate_has_render_control':'render_figures' in canonical_results.generate_canonical_results.__code__.co_varnames,
                       'core_report_has_presentation_control':'presentation' in uc2_postprocess.extend_uc2_report.__code__.co_varnames}
    if not all(source_inspection.values()):raise RuntimeError('CORE_REPORTING_PORTABILITY_CONTROL_MISSING')
    visual_loaded=[k for k in sys.modules if k.startswith('mem_model.visualization.')]
    return {'status':'PASS','repository':str(repo),'cases':cases,'artifact_hash_checks':hashes,
            'production_dispatch_solves':0,'optimization_models_created':0,'2050_executed':False,'manual_authorization_by_case':authorization,
            'original_workspace_accesses':len(blocked),'audited_file_opens':len(observed),
            'optional_visualization_modules_loaded':visual_loaded,'core_reporting_controls':source_inspection,
            'limits':'No production optimization, new solved-network report or analytical acceptance was executed. Native NetCDF reads use explicitly selected repository paths; Python audit hook blocks original project reads.'}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--output',type=Path)
    a=p.parse_args()
    try:r=validate(a.repo)
    except Exception as ex:
        r={'status':'FAIL','error_type':type(ex).__name__,'error':str(ex),'production_dispatch_solves':0}
    if a.output:a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(r,indent=2,default=str)+'\n')
    print(json.dumps({k:v for k,v in r.items() if k not in ('cases','artifact_hash_checks')}))
    if r['status']!='PASS':raise SystemExit(1)

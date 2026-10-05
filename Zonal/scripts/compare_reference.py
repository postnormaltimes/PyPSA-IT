"""Compact known-case regression; never promotes analytical result acceptance."""
from pathlib import Path
import argparse, csv, hashlib, json, math
ROOT=Path(__file__).resolve().parents[1]
FINGERPRINT=ROOT/'inputs/reference_results/final_v1/2040/Base/REGRESSION_FINGERPRINT.json'
def compare():
    reference=json.loads(FINGERPRINT.read_text())
    actual=ROOT/'results/final_methodology_v1/2040/Base'
    checks=[]; deltas=[]
    for name in ('VERIFY_REFERENCE','VERIFY_UC','VERIFY_PRICE','FINAL_SCENARIO_QA'):
        file=actual/'QA'/f'{name}.json'
        if not file.exists():return {'status':'FAIL','reason':'MISSING_'+name,'solve_executed':False}
        value=json.loads(file.read_text());passed=value.get('status')=='PASS'
        checks.append({'check':name,'pass':passed})
    for name,expected in reference['verifications'].items():
        value=json.loads((actual/'QA'/f'{name}.json').read_text())
        for key in ('objective_EUR','load_shedding_MWh','synthetic_units'):
            if key not in expected:continue
            observed=value.get(key)
            tolerance=max(0.0001,abs(float(expected[key]))*1e-9) if key=='objective_EUR' else 0.0001 if key=='load_shedding_MWh' else 0
            equal=observed is not None and abs(float(observed)-float(expected[key]))<=tolerance
            deltas.append({'metric':name+'.'+key,'reference':expected[key],'observed':observed,'tolerance':tolerance,'matches':equal})
        if 'p2x_by_zone_MWh' in expected:
            for zone,energy in expected['p2x_by_zone_MWh'].items():
                observed=value.get('p2x_by_zone_MWh',{}).get(zone)
                equal=observed is not None and abs(observed-energy)<=0.0001
                deltas.append({'metric':'P2X.'+zone,'reference':energy,'observed':observed,'tolerance':0.0001,'matches':equal})
    for table in reference['tables']:
        file=actual/'REPORTING'/table['path']
        if not file.exists():checks.append({'check':table['path'],'pass':False});continue
        with file.open(encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
        expected=table['rows'];keys=table['keys']
        def index(rr):return {tuple(row[k] for k in keys):row for row in rr}
        old,new=index(expected),index(rows)
        if set(old)!=set(new):checks.append({'check':table['path']+'.keys','pass':False});continue
        checks.append({'check':table['path']+'.keys','pass':True})
        differences=[]
        for key in old:
            for column,value in old[key].items():
                if column in keys:continue
                found=new[key].get(column)
                try:
                    x,y=float(value),float(found)
                    same=(math.isnan(x) and math.isnan(y)) or abs(x-y)<=max(1e-6,abs(x)*1e-9)
                except (ValueError,TypeError):same=value==found
                if not same:differences.append({'key':key,'field':column,'reference':value,'observed':found})
        deltas.append({'table':table['path'],'differences':differences,'matches':not differences})
    technical=all(c['pass'] for c in checks)
    matches=all(d['matches'] for d in deltas)
    return {'status':'PASS' if technical and matches else 'REVIEW_REQUIRED' if technical else 'FAIL',
            'solve_executed':True,'technical_qa':'PASS' if technical else 'FAIL',
            'case':'2040_Base_final_v1','checks':checks,'reference_deltas':deltas,
            'binary_netcdf_equality_required':False,'analytical_acceptance':'UNCHANGED',
            'note':'Alternative optimal commitment/dual solutions can change generation/prices. Differences require review; this comparator never silently relaxes accepted validation or ratifies analysis.'}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path);a=p.parse_args();r=compare()
    if a.output:a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps({k:v for k,v in r.items() if k not in ('checks','reference_deltas')}))
    if r['status']!='PASS':raise SystemExit(1)

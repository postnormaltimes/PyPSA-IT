"""Validate published CSV chronology, weights and integrity without a solver."""
from __future__ import annotations
import csv
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ZONES=('NORD','CNOR','CSUD','SUD','CALA','SICI','SARD')
TIMES=[(datetime(2019,1,1)+timedelta(hours=h)).strftime('%Y-%m-%d %H:%M:%S') for h in range(8760)]

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
    return h.hexdigest()

def records(path):
    with path.open(newline='',encoding='utf-8-sig') as f:return list(csv.DictReader(f))

def validate(root=ROOT):
    release=json.loads((root/'inputs/manifests/results_assets.json').read_text())
    manifest=root/'inputs/manifests/results_package.csv'
    if sha(manifest)!=release['data_manifest_sha256']:raise ValueError('Result manifest integrity failure')
    rows=records(manifest)
    for row in rows:
        p=root/row['destination']
        if not p.is_file() or p.stat().st_size!=int(row['size_bytes']) or sha(p)!=row['sha256']:
            raise ValueError('Result member integrity failure: '+row['destination'])
    cases=[]
    for year,variant in ((2040,'final_v3'),(2050,'final_v6')):
        for scenario in ('Slow','Base','High'):
            folder=root/'data/research_results/cases'/str(year)/scenario
            prices=records(folder/'hourly_zonal_prices.csv')
            index=records(folder/'hourly_national_price_index.csv')
            dispatch=records(folder/'hourly_generation_dispatch.csv')
            if [r['snapshot'] for r in prices]!=TIMES or [r['snapshot'] for r in index]!=TIMES:
                raise ValueError('Hourly chronology failure')
            if set(prices[0])!={'snapshot',*ZONES}:raise ValueError('Price zone schema failure')
            lookup={}
            for r in dispatch:
                key=(r['snapshot'],r['zone'])
                if key in lookup:raise ValueError('Duplicate physical observation')
                lookup[key]=r
                if any(not math.isfinite(float(v)) for k,v in r.items() if k not in ('snapshot','zone')):
                    raise ValueError('Non-finite physical observation')
            if set(r['zone'] for r in dispatch)!={*ZONES,'NATIONAL'} or len(lookup)!=8760*8:
                raise ValueError('Physical zone coverage failure')
            error=0.0
            for pr,ix in zip(prices,index):
                demand=[];rigid=[];p2x=[]
                for zone in ZONES:
                    r=lookup[(pr['snapshot'],zone)]
                    d=float(r['gross_end_use_demand_MW']);a=float(r['rigid_end_use_demand_MW']);b=float(r['p2x_electrical_consumption_MW'])
                    if abs(d-a-b)>1e-6:raise ValueError('Demand decomposition failure')
                    demand.append(d);rigid.append(a);p2x.append(b)
                    if not math.isfinite(float(pr[zone])):raise ValueError('Non-finite LP price')
                denominator=sum(demand)
                if denominator<=0:raise ValueError('Invalid national price denominator')
                expected=sum(float(pr[z])*d for z,d in zip(ZONES,demand))/denominator
                err=abs(expected-float(ix['modeled_PUN_like_EUR_per_MWh']));error=max(error,err)
                if err>1e-6 or abs(denominator-float(ix['gross_demand_weight_MW']))>1e-6:
                    raise ValueError('Hourly national price reconciliation failure')
                duration=float(ix['snapshot_duration_h'])
                if duration!=1 or abs(denominator*duration-float(ix['purchasing_weight_MWh']))>1e-6:
                    raise ValueError('Physical duration/energy-weight failure')
            cases.append(dict(year=year,scenario=scenario,variant=variant,status='PASS',snapshots=8760,
                              zones=7,physical_rows=61320,national_price_max_error_EUR_per_MWh=error))
    return dict(status='PASS',cases=cases,manifest_members_verified=len(rows),optimizer_invocations=0,
                model_imports=0,source='PUBLISHED_SAVED_RESULT_EXPORTS')

if __name__=='__main__':print(json.dumps(validate(),indent=2))

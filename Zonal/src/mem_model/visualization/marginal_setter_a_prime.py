"""A-prime candidate-review figures using existing MEM style/save/grid APIs."""
from __future__ import annotations
import json
from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm, to_rgb, to_hex
from matplotlib.patches import Patch
import numpy as np
import pandas as pd
from ..common import sha256_file
from ..reporting.canonical_results import reporting_config
from ..reporting.marginal_setter_attribution import ZONES, validate_structure
from ..reporting.marginal_setter_a_prime import ORDER, VERSION, zone_shares
from .marginal_setter import _style, _save

DEFINITIONS='VRE = Wind + Solar + run-of-river hydro | Storage = co-marginal BESS + PHS'

def _heading(fig,x,title,subtitle,footer):
    fig.text(x,.965,title,fontsize=16,weight='bold',va='top')
    fig.text(x,.905,subtitle,fontsize=9,color='#697386')
    fig.text(x,.865,DEFINITIONS,fontsize=9,color='#697386')
    fig.text(x,.025,footer,fontsize=8.5,color='#697386')

def render_review(year,scenario_outputs,output):
    from ..reporting.uc2_postprocess import no_solver_calls
    output=Path(output).resolve();metadata=[];artifacts=[];sources={};national={}
    if output.name!='A_PRIME_V1':raise ValueError('PROTECTED_OUTPUT_TARGET')
    with no_solver_calls() as guard:
        style,context,grid_helper,old=_style();c=reporting_config()['technology_colors']
        colors={'VRE':'#D4B23C','PHS':c['PHS'],'BESS':c['BESS'],
            'Storage':to_hex((np.array(to_rgb(c['PHS']))+np.array(to_rgb(c['BESS'])))/2),
            'Reservoir / pondage hydro':c['Hydro - reservoir'],'P2X':old['P2X'],
            'CCGT':c['Gas - CCGT'],'Other thermal':old['Other thermal'],
            'External market':old['Market Coupling'],'Mixed / co-marginal':old['Multiple']}
        legend=[Patch(facecolor=colors[k],edgecolor='none',label=k) for k in ORDER]
        for scenario in reporting_config()['scenario_order']:
            folder=Path(scenario_outputs[scenario]).resolve();label=f'{year}_{scenario}'
            if folder.parent!=output:raise ValueError('PROTECTED_OUTPUT_TARGET')
            qp=folder/f'marginal_setter_a_prime_QA_{label}.json';q=json.loads(qp.read_text(encoding='utf-8'))
            if q['status']!='PASS_A_PRIME_REVIEW':raise ValueError('Unvalidated A-prime source')
            tp=folder/f'marginal_setter_a_prime_attribution_{label}.parquet';sp=folder/f'marginal_setter_a_prime_shares_{label}.csv'
            for p in [tp,sp]:
                if sha256_file(p)!=q['artifact_hashes'][p.name]:raise ValueError('A-prime source changed after QA')
                sources[str(p)]=sha256_file(p)
            sources[str(qp)]=sha256_file(qp)
            t=pd.read_parquet(tp);validate_structure(t)
            shares=pd.read_csv(sp,index_col='zone').reindex(index=ZONES,columns=ORDER)
            if not np.allclose(shares,zone_shares(t),atol=1e-12,rtol=0):raise ValueError('Share/data mismatch')
            national[scenario]=t.groupby('final_setter_category').represented_hours.sum().reindex(ORDER,fill_value=0)/t.represented_hours.sum()*100
            figdir=folder/'figures'
            with context(style):
                fig,ax=plt.subplots(figsize=(13,6.5));fig.subplots_adjust(left=.085,right=.985,top=.80,bottom=.23)
                left=np.zeros(7)
                for cat in ORDER:
                    values=shares[cat].to_numpy();ax.barh(range(7),values,left=left,height=.65,color=colors[cat],linewidth=0)
                    for i,value in enumerate(values):
                        if value>=7:
                            light=np.dot(to_rgb(colors[cat]),[.299,.587,.114])
                            ax.text(left[i]+value/2,i,f'{value:.1f}%',ha='center',va='center',fontsize=9,color='#222222' if light>.63 else 'white')
                    left+=values
                ax.set_yticks(range(7),ZONES);ax.invert_yaxis();ax.set_xlim(0,100);ax.set_xticks(range(0,101,20))
                ax.set_xlabel('Share of represented time [%]');ax.grid(True,axis='x',color='#E6E8EB',linewidth=.5);ax.set_axisbelow(True)
                ax.legend(handles=legend,loc='upper left',bbox_to_anchor=(0,-.16),ncol=5,frameon=False,fontsize=9)
                _heading(fig,.085,f'Model-implied marginal price-setting resource — {year} {scenario}',
                    '8,760 hours per zone | Complete fixed-commitment-LP KKT roots | A′ classification',
                    'REVIEW ONLY | Inseparable resources remain co-marginal; inspired by GME, not official ITM. V2 evidence retained.')
                font=plt.rcParams['font.family'][0];files=_save(fig,figdir/f'marginal_setter_a_prime_shares_{label}')
            metadata.append(dict(figure='zonal_share',scenario=scenario,files=files,underlying_data=[str(sp)],zone_order=list(shares.index),font_resolved=font,status='REVIEW_ONLY_NOT_ROUTINE'))
            artifacts.extend(map(Path,files))
            hourly=t[['model_year','scenario','snapshot','zone','represented_hours','primary_attribution_class','final_setter_category','final_resolution_rule','resolution_scope']].copy()
            hourly['category_code']=hourly.final_setter_category.map(dict(zip(ORDER,range(len(ORDER)))))
            hp=folder/f'marginal_setter_a_prime_heatmap_hourly_{label}.parquet';hourly.to_parquet(hp,index=False);artifacts.append(hp)
            for zone in ZONES:
                grid=grid_helper(hourly.loc[hourly.zone.eq(zone)].set_index('snapshot').category_code.to_frame(zone),zone)
                if grid.shape!=(24,365) or grid.isna().any().any():raise ValueError('Incomplete hourly category grid')
                with context(style):
                    fig,ax=plt.subplots(figsize=(13,5.8));fig.subplots_adjust(left=.07,right=.985,top=.79,bottom=.25)
                    ax.imshow(grid.to_numpy(),origin='lower',aspect='auto',interpolation='none',cmap=ListedColormap([colors[k] for k in ORDER]),norm=BoundaryNorm(np.arange(len(ORDER)+1)-.5,len(ORDER)))
                    months=pd.date_range('2019-01-01','2019-12-01',freq='MS')
                    ax.set_xticks((months-pd.Timestamp('2019-01-01')).days,months.strftime('%b'));ax.set_yticks(range(0,24,3),[f'{h:02d}:00' for h in range(0,24,3)])
                    ax.set_xlabel('Saved-weather chronology — 2019');ax.set_ylabel('Snapshot hour')
                    ax.legend(handles=legend,loc='upper left',bbox_to_anchor=(0,-.20),ncol=5,frameon=False,fontsize=8.5)
                    _heading(fig,.07,f'Marginal price-setting resource — {zone} | {year} {scenario}',
                        '24 × 365 hourly observations | Fixed-commitment-LP KKT roots | No interpolation',
                        'REVIEW ANALYTICAL DETAIL | Complete identities, local/coupled mechanisms and KKT evidence remain auditable.')
                    files=_save(fig,figdir/f'marginal_setter_a_prime_heatmap_{label}_{zone}')
                metadata.append(dict(figure='hourly_heatmap',scenario=scenario,zone=zone,files=files,underlying_data=[str(hp)],observations=8760,shape=[24,365],status='REVIEW_DETAIL_NOT_ROUTINE'));artifacts.extend(map(Path,files))
        compare=pd.DataFrame(national).reindex(index=ORDER,columns=reporting_config()['scenario_order'])
        cp=output/f'marginal_setter_a_prime_comparison_shares_{year}.csv';compare.to_csv(cp,index_label='final_setter_category');artifacts.append(cp)
        with context(style):
            fig,ax=plt.subplots(figsize=(12,7.5));fig.subplots_adjust(left=.24,right=.985,top=.77,bottom=.14)
            y=np.arange(len(ORDER))
            for i,scenario in enumerate(compare.columns):ax.barh(y+(i-1)*.22,compare[scenario],height=.20,color=style.scenario_color(scenario),linewidth=0,label=scenario)
            ax.set_yticks(y,ORDER);ax.invert_yaxis();ax.set_xlabel('Share of Italian zone-hours [%]')
            ax.grid(True,axis='x',color='#E6E8EB',linewidth=.5);ax.set_axisbelow(True)
            ax.legend(title='Scenario',loc='lower left',bbox_to_anchor=(0,1.02),ncol=3,frameon=False)
            _heading(fig,.24,f'Marginal price-setting resource — {year} Slow / Base / High',
                'Complete fixed-commitment-LP KKT roots | 61,320 zone-hours per scenario | Represented-time shares',
                'REVIEW ONLY | A′ retains economic co-marginality; no arbitrary asset winner or synthetic merit-order ranking.')
            files=_save(fig,output/'figures'/f'marginal_setter_a_prime_comparison_{year}')
        metadata.append(dict(figure='national_comparison',files=files,underlying_data=[str(cp)],scenario_order=list(compare.columns),status='REVIEW_COMPARISON_NOT_ROUTINE'));artifacts.extend(map(Path,files))
        mp=output/'A_PRIME_FIGURE_METADATA.json';mp.write_text(json.dumps(metadata,indent=2),encoding='utf-8')
        receipt=dict(status='PASS_A_PRIME_RENDER_DATA_QA',version=VERSION,figure_count=len(metadata),heatmap_count=21,category_order=ORDER,category_colors=colors,
            Storage_color_rule='Equal RGB blend of accepted PHS and BESS colours; mixed category uses accepted neutral gray',
            source_hashes=sources,solver_invocations=guard['solver_invocations'],network_opens=0,routine_activation=False,
            implementation_sha256=sha256_file(Path(__file__)),artifact_hashes={str(p):sha256_file(p) for p in [*artifacts,mp]})
        (output/'A_PRIME_RENDER_RECEIPT.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    return receipt

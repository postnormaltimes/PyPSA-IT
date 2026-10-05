"""Four aggregate review figures only; existing MEM styling/save helpers."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.colors import to_rgb
from matplotlib.patches import Patch
from ..common import sha256_file
from ..reporting.canonical_results import reporting_config
from ..reporting.marginal_setter_attribution import ZONES
from ..reporting.marginal_setter_lexicographic import ORDER,FOLDER,VERSION,zone_shares
from .marginal_setter import _style,_save

def heading(fig,x,title,subtitle,*,canonical=False):
    fig.text(x,.96,title,fontsize=16,weight='bold',va='top')
    fig.text(x,.905,subtitle,fontsize=9,color='#697386')
    fig.text(x,.865,'VRE = Wind + Solar + run-of-river hydro | Storage = BESS + PHS + reservoir/pondage hydro',fontsize=9,color='#697386')
    fig.text(x,.83,'Thermal = CCGT + other thermal | Flexible demand = P2X',fontsize=9,color='#697386')
    status='CANONICAL / ROUTINE' if canonical else 'REVIEW ONLY'
    fig.text(x,.025,status+' | Explicit economic-anchor hierarchy; mixed can be local or coupled. Complete V2 evidence retained.',fontsize=8.5,color='#697386')

def render_review(year,output,*,scenario_outputs=None,figure_outputs=None,palette=None,
                  canonical=False,only_comparison=False):
    from ..reporting.uc2_postprocess import no_solver_calls
    output=Path(output).resolve()
    if not canonical and output.name!=FOLDER:raise RuntimeError('PROTECTED_OUTPUT_TARGET')
    output.mkdir(parents=True,exist_ok=True)
    rc=reporting_config()
    folders=scenario_outputs or {s:output/s for s in rc['scenario_order']}
    scenarios=[s for s in rc['scenario_order'] if s in folders]
    metadata=[];artifacts=[];sources={};national={}
    with no_solver_calls() as guard:
        if canonical:
            from .vis_x1 import CONFIG,_toolkit
            from .uc2_comparison import _comparison_style
            import yaml
            _toolkit(yaml.safe_load(CONFIG.read_text(encoding='utf-8')))
            from visualization_toolkit.styles import PlotStyle,style_context
            style=_comparison_style(pd.DataFrame({'scenario_id':scenarios}),PlotStyle)
            context=style_context;colors=dict(palette)
        else:
            style,context,_,old=_style();c=rc['technology_colors']
            colors={'VRE':'#D4B23C','Storage':c['BESS'],'Thermal':c['Gas - CCGT'],
                'Flexible demand':old['P2X'],'External market':old['Market Coupling'],'Market coupling / mixed':old['Multiple']}
        legend=[Patch(facecolor=colors[k],edgecolor='none',label=k) for k in ORDER]
        for scenario in scenarios:
            folder=Path(folders[scenario]);label=f'{year}_{scenario}'
            qp=folder/f'lexicographic_regime_QA_{label}.json';q=json.loads(qp.read_text(encoding='utf-8'))
            if q['status'] not in ('PASS_MINIMAL_REVIEW_INTEGRITY','PASS_CANONICAL_MARGINAL_REGIME'):raise RuntimeError('UNVALIDATED_REGIME_SOURCE')
            tp=folder/f'lexicographic_regime_attribution_{label}.parquet';sp=folder/f'lexicographic_regime_zonal_shares_{label}.csv'
            for p in [tp,sp]:
                if sha256_file(p)!=q['artifact_hashes'][p.name]:raise RuntimeError('REGIME_SOURCE_HASH')
                sources[str(p)]=sha256_file(p)
            sources[str(qp)]=sha256_file(qp)
            t=pd.read_parquet(tp);shares=pd.read_csv(sp,index_col='zone').reindex(index=ZONES,columns=ORDER)
            if not np.allclose(shares,zone_shares(t),atol=1e-12,rtol=0):raise RuntimeError('REGIME_RENDER_SOURCE_MISMATCH')
            national[scenario]=t.groupby('final_regime_category').represented_hours.sum().reindex(ORDER,fill_value=0)/t.represented_hours.sum()*100
            if only_comparison:continue
            with context(style):
                fig,ax=plt.subplots(figsize=(13,6.7));fig.subplots_adjust(left=.085,right=.985,top=.77,bottom=.22)
                left=np.zeros(7)
                for cat in ORDER:
                    values=shares[cat].to_numpy();ax.barh(range(7),values,left=left,height=.65,color=colors[cat],linewidth=0)
                    for i,v in enumerate(values):
                        if v>=6:
                            light=np.dot(to_rgb(colors[cat]),[.299,.587,.114])
                            ax.text(left[i]+v/2,i,f'{v:.1f}%',ha='center',va='center',fontsize=9,color='#222222' if light>.63 else 'white')
                    left+=values
                ax.set_yticks(range(7),ZONES);ax.invert_yaxis();ax.set_xlim(0,100);ax.set_xticks(range(0,101,20));ax.set_xlabel('Share of represented time [%]')
                ax.grid(True,axis='x',color='#E6E8EB',linewidth=.5);ax.set_axisbelow(True)
                ax.legend(handles=legend,loc='upper left',bbox_to_anchor=(0,-.16),ncol=3,frameon=False,fontsize=9.5)
                heading(fig,.085,f'Model-implied marginal price-setting regime — {year} {scenario}',
                    f'{t.snapshot.nunique():,} hours per zone | Complete fixed-commitment-LP marginal roots | Lexicographic '+('economic-anchor interpretation' if canonical else 'review interpretation'),canonical=canonical)
                figure_folder=Path(figure_outputs[scenario]) if figure_outputs else folder/'figures'
                font=plt.rcParams['font.family'][0];files=_save(fig,figure_folder/f'lexicographic_regime_shares_{label}')
            metadata.append(dict(figure='zonal_share',scenario=scenario,files=files,underlying_data=[str(sp)],zone_order=list(shares.index),font_resolved=font,status='CANONICAL / ROUTINE' if canonical else 'REVIEW_ONLY_NOT_ROUTINE'));artifacts.extend(map(Path,files))
        compare=pd.DataFrame(national).reindex(index=ORDER,columns=scenarios)
        cp=output/f'LEXICOGRAPHIC_REGIME_NATIONAL_SHARES_{year}.csv';compare.to_csv(cp,index_label='final_regime_category');artifacts.append(cp)
        if scenarios==rc['scenario_order']:
          with context(style):
            fig,ax=plt.subplots(figsize=(12,6.7));fig.subplots_adjust(left=.23,right=.985,top=.70,bottom=.15)
            y=np.arange(len(ORDER))
            for i,s in enumerate(compare.columns):ax.barh(y+(i-1)*.22,compare[s],height=.20,color=style.scenario_color(s),linewidth=0,label=s)
            ax.set_yticks(y,ORDER);ax.invert_yaxis();ax.set_xlabel('Share of Italian zone-hours [%]')
            ax.grid(True,axis='x',color='#E6E8EB',linewidth=.5);ax.set_axisbelow(True)
            ax.legend(title='Scenario',loc='lower left',bbox_to_anchor=(0,1.02),ncol=3,frameon=False)
            heading(fig,.23,f'Model-implied marginal price-setting regime — {year}',
                ' / '.join(scenarios)+f' | {len(t):,} zone-hours per scenario | Represented-time shares',canonical=canonical)
            files=_save(fig,output/'figures'/f'lexicographic_regime_comparison_{year}')
          metadata.append(dict(figure='national_comparison',files=files,underlying_data=[str(cp)],scenario_order=list(compare.columns),status='CANONICAL / ROUTINE' if canonical else 'REVIEW_ONLY_NOT_ROUTINE'));artifacts.extend(map(Path,files))
        mp=output/'LEXICOGRAPHIC_FIGURE_METADATA.json';mp.write_text(json.dumps(metadata,indent=2),encoding='utf-8')
        receipt=dict(status='PASS_CANONICAL_MARGINAL_REGIME_FIGURES' if canonical else 'PASS_FOUR_AGGREGATE_REVIEW_FIGURES',version=VERSION,figure_count=len(metadata),heatmaps_generated=0,category_order=ORDER,category_colors=colors,
            source_hashes=sources,solver_invocations=guard['solver_invocations'],network_opens=0,routine_activation=canonical,tests_added=0,tests_run=0,
            implementation_sha256=sha256_file(Path(__file__)),artifact_hashes={str(p):sha256_file(p) for p in [*artifacts,mp]})
        (output/'LEXICOGRAPHIC_RENDER_RECEIPT.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    return receipt

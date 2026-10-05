"""Review-only UC3 figures from validated attribution tables, without networks."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm, to_rgb
from matplotlib.patches import Patch
import numpy as np
import pandas as pd
import yaml

from ..common import ROOT, sha256_file
from ..reporting.canonical_results import reporting_config
from ..reporting.marginal_setter_attribution import ZONES, validate_structure
from .vis_x1 import CONFIG, _toolkit
from .uc2_comparison import _comparison_style

ORDER = ('Solar','Wind','Hydro','Bioenergy','Geothermal','Nuclear','CCGT','Other thermal',
         'BESS','PHS','P2X','Market Coupling','Multiple','Other / indeterminate','Scarcity / VOLL')
TECHNOLOGY_PRESENTATION = {
    'Solar':'Solar','Wind':'Wind','Hydro':'Hydro','Hydro RoR':'Hydro',
    'Bioenergy':'Bioenergy','Geothermal':'Geothermal','Nuclear':'Nuclear','CCGT':'CCGT',
    'Gas IC':'Other thermal','Gas - OCGT':'Other thermal','Gas - steam thermal':'Other thermal',
    'Gas - other thermal':'Other thermal','Gas + CCS':'Other thermal','Hydrogen - OCGT':'Other thermal',
    'Bioenergy + CCS':'Bioenergy','BESS':'BESS','PHS':'PHS','P2X':'P2X','Load shedding / VOLL':'Scarcity / VOLL',
}


def presentation_categories(table):
    """Known CCGT stays CCGT under ramp context; never force a remote setter."""
    out = []
    mechanisms = {'MARKET_COUPLING':'Market Coupling','MULTIPLE_CANDIDATES':'Multiple',
                  'OTHER_INDETERMINATE':'Other / indeterminate'}
    for row in table.itertuples(index=False):
        if row.primary_attribution_class in mechanisms:
            out.append(mechanisms[row.primary_attribution_class])
        elif row.setter_technology in TECHNOLOGY_PRESENTATION:
            out.append(TECHNOLOGY_PRESENTATION[row.setter_technology])
        else:
            raise ValueError(f'Unmapped marginal-setter presentation identity: {row.setter_technology}')
    return pd.Series(out,index=table.index,name='presentation_category')


def _style():
    cfg = yaml.safe_load(CONFIG.read_text(encoding='utf-8'))
    _toolkit(cfg)
    from visualization_toolkit.styles import PlotStyle, style_context
    from visualization_toolkit.prices import price_day_hour_table
    rc = reporting_config(); c = rc['technology_colors']
    parent = ROOT/'outputs/visualization/UC2/2040/Base/dispatch_presentation/PRESENTATION_DAILY_RECEIPT.json'
    accepted = json.loads(parent.read_text())['semantic_colors']
    colors = {
        'Solar':c['Solar PV - utility'],'Wind':c['Onshore wind'],'Hydro':c['Hydro - reservoir'],
        'Bioenergy':c['Bioenergy'],'Geothermal':c['Geothermal'],'Nuclear':c['Nuclear'],
        'CCGT':c['Gas - CCGT'],'Other thermal':c['Gas - internal combustion'],
        'BESS':c['BESS'],'PHS':c['PHS'],'P2X':accepted['P2X withdrawal']['color'],
        'Market Coupling':accepted['Imports']['color'],
        'Multiple':'#BDBDBD','Other / indeterminate':'#E2E5E9','Scarcity / VOLL':'#B22222',
    }
    style = _comparison_style(pd.DataFrame({'scenario_id':rc['scenario_order']}),PlotStyle)
    style.carrier_style.update({k:{'color':v} for k,v in colors.items()})
    return style, style_context, price_day_hour_table, colors


def _save(fig, stem):
    stem = Path(stem); stem.parent.mkdir(parents=True,exist_ok=True)
    files = []
    for ext in ('png','svg'):
        p = stem.with_suffix('.'+ext)
        with matplotlib.rc_context({'svg.hashsalt':'MEM_UC3_MARGINAL_SETTER_V2','svg.fonttype':'none'}):
            fig.savefig(p,dpi=240,facecolor='white',bbox_inches='tight',metadata={'Date':None} if ext=='svg' else {'Software':'MEM UC3'})
        files.append(str(p))
    plt.close(fig)
    return files


def render_review(year, scenario_outputs, output):
    """All applicable scenarios must already pass; no routine registration."""
    from ..reporting.uc2_postprocess import no_solver_calls
    output = Path(output); tables = {}; source_hashes={}
    for scenario, folder in scenario_outputs.items():
        folder = Path(folder); qa_path = folder/f'marginal_setter_QA_{year}_{scenario}.json'
        qa = json.loads(qa_path.read_text())
        if qa['status'] not in ('PASS_BASE_SEMANTIC_GATE','PASS_FROZEN_METHOD_ATTRIBUTION'):
            raise ValueError(f'Unvalidated attribution source {scenario}: {qa["status"]}')
        p = folder/f'marginal_setter_attribution_{year}_{scenario}.parquet'
        if sha256_file(p) != qa['artifact_hashes'][p.name]:
            raise ValueError('Attribution table changed after QA')
        t=pd.read_parquet(p);validate_structure(t);t['presentation_category']=presentation_categories(t)
        tables[scenario]=t;source_hashes[str(p)]=sha256_file(p);source_hashes[str(qa_path)]=sha256_file(qa_path)
    order=[c for c in ORDER if any(t.presentation_category.eq(c).any() for t in tables.values())]
    metadata=[]; artifacts=[]; stats=[];share_by_scenario={}
    with no_solver_calls() as guard:
        style,context,heatmap_grid,colors=_style()
        for scenario,t in tables.items():
            scenario_dir=Path(scenario_outputs[scenario]);figdir=scenario_dir/'figures'
            shares=t.groupby(['zone','presentation_category']).represented_hours.sum().unstack(fill_value=0).reindex(index=ZONES,columns=order,fill_value=0)
            shares=shares.div(t.groupby('zone').represented_hours.sum(),axis=0)*100
            if not np.allclose(shares.sum(axis=1),100,atol=1e-10,rtol=0):raise ValueError('Setter shares do not sum to 100%')
            data=scenario_dir/f'marginal_setter_presentation_shares_{year}_{scenario}.csv'
            shares.to_csv(data,index_label='zone');artifacts.append(data)
            national=t.groupby('presentation_category').represented_hours.sum().reindex(order,fill_value=0)/t.represented_hours.sum()*100
            share_by_scenario[scenario]=national
            with context(style):
                fig,ax=plt.subplots(figsize=(13,6.5));fig.subplots_adjust(left=.085,right=.985,top=.80,bottom=.23)
                left=np.zeros(len(ZONES))
                for c in order:
                    v=shares[c].to_numpy();ax.barh(np.arange(7),v,left=left,height=.65,color=colors[c],linewidth=0,label=c)
                    for i,value in enumerate(v):
                        if value>=7:
                            light=np.dot(to_rgb(colors[c]),[.299,.587,.114])
                            ax.text(left[i]+value/2,i,f'{value:.1f}%',ha='center',va='center',fontsize=9,color='#222222' if light>.63 else 'white')
                    left+=v
                ax.set_yticks(range(7),ZONES);ax.invert_yaxis();ax.set_xlim(0,100)
                ax.set_xlabel('Share of represented time [%]');ax.set_xticks(range(0,101,20))
                ax.grid(True,axis='x',color='#E6E8EB',linewidth=.5);ax.set_axisbelow(True)
                ax.legend(loc='upper left',bbox_to_anchor=(0,-.16),ncol=5,frameon=False,fontsize=9)
                fig.text(.085,.965,f'Marginal price-setting technology/mechanism by zone — {year} {scenario}',fontsize=16,weight='bold',va='top')
                fig.text(.085,.91,'8,760 hours per zone | Fixed-commitment LP KKT attribution | Known technologies retain constraint context in audit',fontsize=9,color='#697386')
                fig.text(.085,.025,'CANDIDATE REVIEW | Coupling may contain several remote roots; Multiple denotes unresolved local competition.',fontsize=8.5,color='#697386')
                font=plt.rcParams['font.family'][0]
                files=_save(fig,figdir/f'marginal_setter_shares_{year}_{scenario}')
            metadata.append(dict(figure='marginal_setter_shares',scenario=scenario,model_year=year,files=files,underlying_data=[str(data)],status='CANDIDATE_REVIEW_NOT_ROUTINE',resolution='Italian zone',font_resolved=font))
            artifacts.extend(Path(p) for p in files)
            code={c:i for i,c in enumerate(order)}
            hourly=t[['model_year','scenario','snapshot','zone','represented_hours','primary_attribution_class','setter_technology','presentation_category']].copy()
            hourly['category_code']=hourly.presentation_category.map(code)
            hourly_path=scenario_dir/f'marginal_setter_heatmap_hourly_{year}_{scenario}.parquet';hourly.to_parquet(hourly_path,index=False);artifacts.append(hourly_path)
            for zone in ZONES:
                series=hourly.loc[hourly.zone.eq(zone)].set_index('snapshot').category_code
                grid=heatmap_grid(series.to_frame(zone),zone)
                if grid.shape!=(24,365) or grid.isna().any().any():raise ValueError('Categorical heatmap chronology is incomplete')
                with context(style):
                    fig,ax=plt.subplots(figsize=(13,5.8));fig.subplots_adjust(left=.07,right=.985,top=.79,bottom=.25)
                    cmap=ListedColormap([colors[c] for c in order]);norm=BoundaryNorm(np.arange(len(order)+1)-.5,len(order))
                    ax.imshow(grid.to_numpy(),origin='lower',aspect='auto',interpolation='none',cmap=cmap,norm=norm)
                    months=pd.date_range('2019-01-01','2019-12-01',freq='MS');positions=(months-pd.Timestamp('2019-01-01')).days
                    ax.set_xticks(positions,months.strftime('%b'));ax.set_yticks(range(0,24,3),[f'{h:02d}:00' for h in range(0,24,3)])
                    ax.set_xlabel('Saved-weather chronology — 2019');ax.set_ylabel('Snapshot hour')
                    ax.legend(handles=[Patch(facecolor=colors[c],edgecolor='none',label=c) for c in order],loc='upper left',bbox_to_anchor=(0,-.20),ncol=5,frameon=False,fontsize=8.5)
                    fig.text(.07,.965,f'Marginal-setter chronology — {zone} | {year} {scenario}',fontsize=16,weight='bold',va='top')
                    fig.text(.07,.905,'Every hourly observation retained | 24 × 365 categorical grid | No interpolation or timezone conversion',fontsize=9,color='#697386')
                    fig.text(.07,.025,'CANDIDATE ANALYTICAL DETAIL | Fixed-commitment LP KKT evidence; coupled root identity remains in the audit table.',fontsize=8.5,color='#697386')
                    files=_save(fig,figdir/f'marginal_setter_heatmap_{year}_{scenario}_{zone}')
                metadata.append(dict(figure='marginal_setter_heatmap',zone=zone,scenario=scenario,model_year=year,files=files,underlying_data=[str(hourly_path)],status='CANDIDATE_ANALYTICAL_DETAIL_NOT_ROUTINE',resolution='24 hours × 365 days'))
                artifacts.extend(Path(p) for p in files)
            stats.append(dict(scenario=scenario,share_sum_max_error_percent=float(np.abs(shares.sum(axis=1)-100).max()),heatmaps=7,heatmap_observations=61320))
        compare=pd.DataFrame(share_by_scenario).reindex(columns=reporting_config()['scenario_order'])
        compare_data=output/f'marginal_setter_comparison_shares_{year}.csv';compare.to_csv(compare_data,index_label='presentation_category');artifacts.append(compare_data)
        with context(style):
            fig,ax=plt.subplots(figsize=(12,7.5));fig.subplots_adjust(left=.18,right=.985,top=.79,bottom=.14)
            y=np.arange(len(order));scenarios=list(compare.columns)
            for i,s in enumerate(scenarios):
                ax.barh(y+(i-1)*.22,compare[s],height=.20,color=style.scenario_color(s),linewidth=0,label=s)
            ax.set_yticks(y,order);ax.invert_yaxis();ax.set_xlabel('Share of Italian zone-hours [%]')
            ax.grid(True,axis='x',color='#E6E8EB',linewidth=.5);ax.set_axisbelow(True)
            ax.legend(title='Scenario',loc='lower left',bbox_to_anchor=(0,1.02),ncol=3,frameon=False)
            fig.text(.18,.97,f'Marginal-setter shares — {year} Slow / Base / High',fontsize=16,weight='bold',va='top')
            fig.text(.18,.915,'Equal represented time for each Italian zone | 61,320 zone-hours per scenario | No load weighting',fontsize=9,color='#697386')
            fig.text(.18,.025,'CANDIDATE REVIEW | Known technologies preserved; Coupling and unresolved local competition remain separate.',fontsize=8.5,color='#697386')
            files=_save(fig,output/'figures'/f'marginal_setter_comparison_{year}')
        metadata.append(dict(figure='marginal_setter_comparison',model_year=year,scenarios=scenarios,files=files,underlying_data=[str(compare_data)],status='CANDIDATE_COMPARISON_NOT_ROUTINE',denominator='represented Italian zone-hours',scenario_colors={s:style.scenario_color(s) for s in scenarios}))
        artifacts.extend(Path(p) for p in files)
        (output/'MARGINAL_SETTER_FIGURE_METADATA.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
        qa={'status':'PASS_RENDER_DATA_QA_AWAITING_VISUAL_REVIEW','figures':len(metadata),'source_hashes':source_hashes,'category_order':order,'category_colors':colors,'presentation_mapping':TECHNOLOGY_PRESENTATION,'by_scenario':stats,'solver_invocations':guard['solver_invocations'],'existing_visualization_modifications':0,'routine_activation':False,'artifact_hashes':{str(p):sha256_file(p) for p in artifacts}}
        (output/'MARGINAL_SETTER_RENDER_RECEIPT.json').write_text(json.dumps(qa,indent=2),encoding='utf-8')
    return qa

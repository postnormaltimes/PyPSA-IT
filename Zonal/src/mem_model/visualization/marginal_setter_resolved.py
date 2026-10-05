"""New final-family review renders; the validated V2 renderer is untouched."""
from __future__ import annotations
import json
from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm, to_rgb
from matplotlib.patches import Patch
import numpy as np
import pandas as pd
from ..common import sha256_file
from ..reporting.canonical_results import reporting_config
from ..reporting.marginal_setter_attribution import ZONES, validate_structure
from ..reporting.marginal_setter_resolver import ORDER, VERSION
from .marginal_setter import _style, _save


def _zone_shares(table):
    energy=table.groupby(["zone","final_setter_category"]).represented_hours.sum().unstack(fill_value=0).reindex(index=ZONES,columns=ORDER,fill_value=0)
    # pandas arithmetic can reorder the index during labelled alignment.
    # Restore the requested zone order after division, before drawing arrays.
    return (energy.div(table.groupby("zone").represented_hours.sum(),axis=0)*100).reindex(index=ZONES,columns=ORDER)


def render_review(year, scenario_outputs, output):
    from ..reporting.uc2_postprocess import no_solver_calls
    output=Path(output); metadata=[];artifacts=[];sources={};national={};checks=[]
    with no_solver_calls() as guard:
        style,context,grid_helper,old_colors=_style()
        c=reporting_config()["technology_colors"]
        colors={"VRE":"#D4B23C","PHS":c["PHS"],"BESS":c["BESS"],
                "Reservoir / pondage hydro":c["Hydro - reservoir"],
                "P2X":old_colors["P2X"],"CCGT":c["Gas - CCGT"],"Other thermal":old_colors["Other thermal"],
                "External market":old_colors["Market Coupling"],"Market coupling / multiple":old_colors["Multiple"]}
        for scenario,folder in scenario_outputs.items():
            folder=Path(folder);label=f"{year}_{scenario}"
            qpath=folder/f"marginal_setter_final_QA_{label}.json";q=json.loads(qpath.read_text())
            if q["status"]!="PASS_FINAL_FAMILY_RESOLUTION":raise ValueError("Unvalidated final root-family table")
            paths=[folder/f"marginal_setter_final_attribution_{label}.parquet",folder/f"marginal_setter_final_shares_{label}.csv"]
            for file in paths:
                if sha256_file(file)!=q["artifact_hashes"][file.name]:raise ValueError("Resolved data changed after QA")
                sources[str(file)]=sha256_file(file)
            sources[str(qpath)]=sha256_file(qpath)
            t=pd.read_parquet(paths[0]);validate_structure(t)
            shares=pd.read_csv(paths[1],index_col="zone").reindex(index=ZONES,columns=ORDER)
            recomputed=_zone_shares(t)
            if not np.allclose(shares,recomputed,atol=1e-12,rtol=0):raise ValueError("Share figure/source mismatch")
            national[scenario]=t.groupby("final_setter_category").represented_hours.sum().reindex(ORDER,fill_value=0)/t.represented_hours.sum()*100
            figdir=folder/"figures"
            with context(style):
                fig,ax=plt.subplots(figsize=(13,6.5));fig.subplots_adjust(left=.085,right=.985,top=.80,bottom=.23)
                left=np.zeros(7)
                for category in ORDER:
                    v=shares[category].to_numpy()
                    ax.barh(range(7),v,left=left,height=.65,color=colors[category],linewidth=0,label=category)
                    for i,value in enumerate(v):
                        if value>=7:
                            light=np.dot(to_rgb(colors[category]),[.299,.587,.114])
                            ax.text(left[i]+value/2,i,f"{value:.1f}%",ha="center",va="center",fontsize=9,color="#222222" if light>.63 else "white")
                    left+=v
                ax.set_yticks(range(7),ZONES);ax.invert_yaxis();ax.set_xlim(0,100);ax.set_xticks(range(0,101,20))
                ax.set_xlabel("Share of represented time [%]");ax.grid(True,axis="x",color="#E6E8EB",linewidth=.5);ax.set_axisbelow(True)
                ax.legend(loc="upper left",bbox_to_anchor=(0,-.16),ncol=5,frameon=False,fontsize=9)
                fig.text(.085,.965,f"Marginal price-setting category by zone — {year} {scenario}",fontsize=16,weight="bold",va="top")
                fig.text(.085,.91,"8,760 hours per zone | Categories resolved from complete fixed-commitment-LP KKT marginal roots",fontsize=9,color="#697386")
                fig.text(.085,.875,"VRE = Wind + Solar + run-of-river hydro",fontsize=9,color="#697386")
                fig.text(.085,.025,"CANDIDATE REVIEW | One family resolves locally or through coupling; mixed families remain residual. V2 mechanisms retained.",fontsize=8.5,color="#697386")
                font=plt.rcParams["font.family"][0];files=_save(fig,figdir/f"marginal_setter_final_shares_{label}")
            metadata.append(dict(figure="marginal_setter_final_shares",scenario=scenario,model_year=year,files=files,underlying_data=[str(paths[1])],zone_order=list(shares.index),font_resolved=font,status="CANDIDATE_REVIEW_NOT_ROUTINE"))
            artifacts.extend(Path(f) for f in files)
            hourly=t[["model_year","scenario","snapshot","zone","represented_hours","primary_attribution_class","final_setter_category","resolution_scope","residual_origin"]].copy()
            hourly["category_code"]=hourly.final_setter_category.map({cat:i for i,cat in enumerate(ORDER)})
            hourly_path=folder/f"marginal_setter_final_heatmap_hourly_{label}.parquet";hourly.to_parquet(hourly_path,index=False);artifacts.append(hourly_path)
            for zone in ZONES:
                grid=grid_helper(hourly.loc[hourly.zone.eq(zone)].set_index("snapshot").category_code.to_frame(zone),zone)
                if grid.shape!=(24,365) or grid.isna().any().any():raise ValueError("Incomplete final heatmap")
                with context(style):
                    fig,ax=plt.subplots(figsize=(13,5.8));fig.subplots_adjust(left=.07,right=.985,top=.79,bottom=.25)
                    ax.imshow(grid.to_numpy(),origin="lower",aspect="auto",interpolation="none",cmap=ListedColormap([colors[cat] for cat in ORDER]),norm=BoundaryNorm(np.arange(len(ORDER)+1)-.5,len(ORDER)))
                    months=pd.date_range("2019-01-01","2019-12-01",freq="MS")
                    ax.set_xticks((months-pd.Timestamp("2019-01-01")).days,months.strftime("%b"))
                    ax.set_yticks(range(0,24,3),[f"{h:02d}:00" for h in range(0,24,3)])
                    ax.set_xlabel("Saved-weather chronology — 2019");ax.set_ylabel("Snapshot hour")
                    ax.legend(handles=[Patch(facecolor=colors[cat],edgecolor="none",label=cat) for cat in ORDER],loc="upper left",bbox_to_anchor=(0,-.20),ncol=5,frameon=False,fontsize=8.5)
                    fig.text(.07,.965,f"Marginal price-setting category — {zone} | {year} {scenario}",fontsize=16,weight="bold",va="top")
                    fig.text(.07,.905,"24 × 365 hourly observations | Resolved from fixed-commitment-LP KKT marginal roots | No interpolation",fontsize=9,color="#697386")
                    fig.text(.07,.865,"VRE = Wind + Solar + run-of-river hydro",fontsize=9,color="#697386")
                    fig.text(.07,.025,"CANDIDATE ANALYTICAL DETAIL | Original local/coupled mechanisms, identities and KKT evidence remain in V2.",fontsize=8.5,color="#697386")
                    files=_save(fig,figdir/f"marginal_setter_final_heatmap_{label}_{zone}")
                metadata.append(dict(figure="marginal_setter_final_heatmap",zone=zone,scenario=scenario,model_year=year,files=files,underlying_data=[str(hourly_path)],status="CANDIDATE_ANALYTICAL_DETAIL_NOT_ROUTINE"))
                artifacts.extend(Path(f) for f in files)
            checks.append(dict(scenario=scenario,observations=len(hourly),heatmaps=7,share_sum_max_error_percent=float((shares.sum(axis=1)-100).abs().max())))
        compare=pd.DataFrame(national).reindex(columns=reporting_config()["scenario_order"])
        compare_path=output/f"marginal_setter_final_comparison_shares_{year}.csv";compare.to_csv(compare_path,index_label="final_setter_category");artifacts.append(compare_path)
        with context(style):
            fig,ax=plt.subplots(figsize=(12,7.5));fig.subplots_adjust(left=.24,right=.985,top=.79,bottom=.14)
            y=np.arange(len(ORDER))
            for i,scenario in enumerate(compare.columns):
                ax.barh(y+(i-1)*.22,compare[scenario],height=.20,color=style.scenario_color(scenario),linewidth=0,label=scenario)
            ax.set_yticks(y,ORDER);ax.invert_yaxis();ax.set_xlabel("Share of Italian zone-hours [%]")
            ax.grid(True,axis="x",color="#E6E8EB",linewidth=.5);ax.set_axisbelow(True)
            ax.legend(title="Scenario",loc="lower left",bbox_to_anchor=(0,1.02),ncol=3,frameon=False)
            fig.text(.24,.97,f"Marginal price-setting categories — {year} Slow / Base / High",fontsize=16,weight="bold",va="top")
            fig.text(.24,.915,"Complete fixed-commitment-LP KKT roots | 61,320 zone-hours per scenario | No load weighting",fontsize=9,color="#697386")
            fig.text(.24,.875,"VRE = Wind + Solar + run-of-river hydro",fontsize=9,color="#697386")
            fig.text(.24,.025,"CANDIDATE REVIEW | Mixed-family residual only; multiplicity or remote origin alone does not prevent technology resolution.",fontsize=8.5,color="#697386")
            files=_save(fig,output/"figures"/f"marginal_setter_final_comparison_{year}")
        metadata.append(dict(figure="marginal_setter_final_comparison",model_year=year,files=files,underlying_data=[str(compare_path)],scenario_colors={s:style.scenario_color(s) for s in compare.columns},status="CANDIDATE_COMPARISON_NOT_ROUTINE"))
        artifacts.extend(Path(f) for f in files)
        (output/"FINAL_SETTER_FIGURE_METADATA.json").write_text(json.dumps(metadata,indent=2),encoding="utf-8")
        receipt=dict(status="PASS_RENDER_DATA_QA_AWAITING_VISUAL_REVIEW",version=VERSION,figures=len(metadata),source_hashes=sources,category_order=ORDER,category_colors=colors,VRE_color_decision="One consistent new aggregate-family gold #D4B23C; original Solar/Wind colours untouched",by_scenario=checks,solver_invocations=guard["solver_invocations"],network_opens=0,routine_activation=False,artifact_hashes={str(f):sha256_file(f) for f in artifacts},implementation_sha256=sha256_file(Path(__file__)))
        (output/"FINAL_SETTER_RENDER_RECEIPT.json").write_text(json.dumps(receipt,indent=2),encoding="utf-8")
    return receipt

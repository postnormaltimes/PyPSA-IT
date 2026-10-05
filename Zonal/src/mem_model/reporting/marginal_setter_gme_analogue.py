"""Review-only synthetic offer selector over immutable validated V2 roots.

This does not identify literal submitted GME bids or reproduce GME ITM.
Fixed-LP roots determine selection; canonical UC withdrawals only weight shares.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from ..common import sha256_file
from .marginal_setter_attribution import ZONES, TOLERANCE, POWER_TOLERANCE, normalize_zone, validate_structure
from .marginal_setter_resolver import ORDER as FAMILY_ORDER, IDENTITY, map_candidates

VERSION = "MARGINAL_SETTER_GME_ANALOGUE_V1"
ORDER = FAMILY_ORDER[:-1]
KEYS = ["snapshot", "target_zone"]
RATIO_TOLERANCE = 1e-12
BASES = ("UNIQUE_VALID_ROOT", "GME_PRIORITY_RESOLUTION", "PARTIAL_ACCEPTANCE_PROXY", "UTILISATION_PROXY", "DETERMINISTIC_EXACT_TIE")
RULES = {
    "version": VERSION,
    "root_authority": "Unchanged complete V2-selected local or coupled roots; no candidate discovery",
    "supply_preference": "Positive electrical supply first; otherwise active demand; otherwise explicitly flagged zero-output KKT price support",
    "legal_priority": "UNKNOWN without essential/portfolio/support/Capacity-Market/CHP qualifications; no carrier-based inference",
    "priority_direction": "Where fully evidenced, higher numeric rank means lower/later priority; last accepted analogue uses maximum rank",
    "partial_acceptance": "Strict native operating-range interiority, NOT a submitted-offer acceptance flag",
    "utilisation": "(native_power - operating_lower)/(operating_upper - operating_lower); minimum ratio surrogate for last partially accepted incremental offer",
    "utilisation_limitation": "Neither range nor lower/higher ratio is an official bid-merit order; alternative maximum-ratio diagnostic is retained",
    "ratio_tie_tolerance": RATIO_TOLERANCE,
    "exact_tie": "Minimum SHA256 economic-root-identity digest, no technology/locality preference; maximum digest and alternate salt sensitivity",
    "volume_proxy": "UC rigid load + UC P2X + UC BESS charging + UC PHS charging - UC shedding, multiplied by accepted physical duration",
    "volume_limitation": "Served zonal withdrawal proxy, not literal GME zonal sales/bilateral offer volumes; excludes transit and exports",
    "activation": "REVIEW_ONLY_NOT_ROUTINE",
}
COMPARISON_RULES = {
    "A": "Complete V2-selected economic roots, including demand/charging and zero-output price-support roots; no supply preference",
    "B": "V2-selected positive electrical supply roots only; no eligible supply remains unresolved in the full denominator",
    "hierarchy": "Unique root/family; independently evidenced priority (unavailable here); strict native interiority; minimum native operating-range utilisation; neutral exact tie",
    "unique_family": "Technology is resolved without synthetic tie-breaking; any representative asset choice remains separately auditable",
    "ratio_sensitivity": "Maximum utilisation is an alternative synthetic convention, not an alternative KKT identification",
    "precision_limitation": "1e-12 ratio ties do not establish economic discrimination; near-tie gaps are assessed against inherited 1e-6 MW native-power tolerance divided by operating range",
    "root_count_bias": "Hash has no explicit technology preference, but more tied economic assets give a family more opportunities to win",
    "final_methodology": "NOT_SELECTED; comparative prototypes only",
}


class AnalogueError(RuntimeError):
    pass


def validate_review_output(root, output):
    """New candidate layer only; never write into existing evidence folders."""
    allowed = Path(root).resolve()/VERSION
    target = Path(output).resolve()
    if target == allowed or not target.is_relative_to(allowed):
        raise AnalogueError("PROTECTED_OUTPUT_TARGET")


def prepare_offers(table, audit):
    """Keep complete root evidence; path multiplicity has no selection weight."""
    c = map_candidates(audit)
    c["economic_root_id"] = [hashlib.sha256(json.dumps(tuple(r), ensure_ascii=True).encode()).hexdigest()
                             for r in c[list(IDENTITY)].itertuples(index=False, name=None)]
    c["coupling_paths"] = c.coupling_path.map(lambda x: json.dumps([x]))
    duplicate = c.duplicated(KEYS+["economic_root_id"], keep=False)
    if duplicate.any():
        c.loc[duplicate,"coupling_paths"] = c.loc[duplicate].groupby(KEYS+["economic_root_id"]).coupling_path.transform(lambda x: json.dumps(sorted(set(x))))
    c = c.sort_values(KEYS+["economic_root_id", "coupling_path"]).drop_duplicates(KEYS+["economic_root_id"]).reset_index(drop=True)
    c = c.merge(table[["snapshot", "zone", "zonal_price_EUR_MWh", "primary_attribution_class", "resolution_scope"]],
                left_on=KEYS, right_on=["snapshot", "zone"], validate="many_to_one", suffixes=("", "_target"))
    if c.zonal_price_EUR_MWh.isna().any() or not (c.remote == c.resolution_scope.eq("COUPLED")).all():
        raise AnalogueError("SOURCE_AUTHORITY_CONFLICT: scope or target")
    if not np.allclose(c.recon, c.zonal_price_EUR_MWh, atol=TOLERANCE, rtol=0) or c.residual.abs().gt(TOLERANCE).any():
        raise AnalogueError("MATERIAL_KKT_RECONSTRUCTION_FAILURE")
    c["electrical_injection_MW"] = np.where(c.component.eq("Link"),
        np.where(c.position.eq("charging"), -c.native_power, c.native_power*c.efficiency),
        np.where(c.mechanism.eq("P2X_FLEXIBLE_DEMAND"), -c.native_power, c.native_power))
    c["accepted_supply_proxy"] = c.electrical_injection_MW.gt(POWER_TOLERANCE)
    c["active_demand_proxy"] = c.electrical_injection_MW.lt(-POWER_TOLERANCE)
    width = c.operating_upper-c.operating_lower
    if (width <= POWER_TOLERANCE).any() or not np.isfinite(c[["native_power", "operating_lower", "operating_upper"]]).all().all():
        raise AnalogueError("INVALID_MODEL_OPERATING_RANGE")
    if ((c.native_power < c.operating_lower-POWER_TOLERANCE) | (c.native_power > c.operating_upper+POWER_TOLERANCE)).any():
        raise AnalogueError("NATIVE_POWER_OUTSIDE_VALID_RANGE")
    c["normalised_acceptance_ratio"] = (c.native_power-c.operating_lower)/width
    c["interior_acceptance_proxy"] = (c.native_power > c.operating_lower+POWER_TOLERANCE)&(c.native_power < c.operating_upper-POWER_TOLERANCE)
    c["gme_priority_rank"] = np.nan
    c["gme_priority_status"] = "UNKNOWN_NOT_IDENTIFIABLE_FROM_MEM_ROOT_FIELDS"
    return c


def select_offers(offers, convention="HASH_MIN", utilisation_direction="LOWEST", *, approach="SUPPLY_PREFERENCE", return_frontier=False):
    """One synthetic root; stages are explicit and insensitive to row/path order."""
    if convention not in ("HASH_MIN", "HASH_MAX", "HASH_ALTERNATE") or utilisation_direction not in ("LOWEST", "HIGHEST"):
        raise ValueError("Unknown selection convention")
    if approach not in ("A", "B", "SUPPLY_PREFERENCE"):
        raise ValueError("Unknown attribution approach")
    c = offers.copy()
    valid = c.groupby(KEYS).agg(valid_root_count=("economic_root_id", "nunique"), valid_family_count=("root_family", "nunique"))
    supply = c.groupby(KEYS).accepted_supply_proxy.transform("any")
    demand = c.groupby(KEYS).active_demand_proxy.transform("any")
    c["selection_side"] = np.where(supply, "ACCEPTED_SUPPLY_PROXY", np.where(demand, "ACTIVE_DEMAND_EXTENSION", "ZERO_OUTPUT_KKT_SUPPORT_EXTENSION"))
    if approach == "B":
        c = c.loc[c.accepted_supply_proxy].copy()
        c["selection_side"] = "ACCEPTED_SUPPLY_PROXY"
    elif approach == "A":
        c["selection_side"] = np.where(c.accepted_supply_proxy,"ACCEPTED_SUPPLY_PROXY",np.where(c.active_demand_proxy,"ACTIVE_DEMAND_ECONOMIC_ROOT","ZERO_OUTPUT_KKT_PRICE_SUPPORT"))
    else:
        c = c.loc[np.where(supply,c.accepted_supply_proxy,np.where(demand,c.active_demand_proxy,True))].copy()
    eligible = c.groupby(KEYS).agg(eligible_root_count=("economic_root_id","nunique"),eligible_family_count=("root_family","nunique"))
    stages = {}
    def count(): return c.groupby(KEYS).economic_root_id.transform("size")
    def record(basis):
        n=c.groupby(KEYS).size()
        families=c.groupby(KEYS).root_family.nunique()
        resolved=n.eq(1) if approach=="SUPPLY_PREFERENCE" else families.eq(1)
        for key in n[resolved].index:
            if key not in stages:
                stages[key]="UNIQUE_VALID_FAMILY" if basis=="UNIQUE_VALID_ROOT" and n.loc[key]>1 else basis
    record("UNIQUE_VALID_ROOT")
    known = c.gme_priority_rank.notna().groupby([c.snapshot,c.target_zone]).transform("all")
    # Only complete independently evidenced priority permits legal ranking.
    maximum = c.groupby(KEYS).gme_priority_rank.transform("max")
    c = c.loc[~known | c.gme_priority_rank.eq(maximum)].copy()
    record("GME_PRIORITY_RESOLUTION")
    interior=c.groupby(KEYS).interior_acceptance_proxy.transform("any")
    c=c.loc[~interior | c.interior_acceptance_proxy].copy()
    record("PARTIAL_ACCEPTANCE_PROXY")
    ratios=c.groupby(KEYS).normalised_acceptance_ratio
    extreme=ratios.transform("min" if utilisation_direction=="LOWEST" else "max")
    c=c.loc[c.normalised_acceptance_ratio.sub(extreme).abs().le(RATIO_TOLERANCE)].copy()
    record("UTILISATION_PROXY")
    frontier=c.copy()
    tie_families=c.groupby(KEYS).root_family.nunique().rename("final_tie_family_count")
    ties=c.groupby(KEYS).size().rename("exact_tie_count")
    if convention=="HASH_ALTERNATE":
        c["tie_key"]=c.economic_root_id.map(lambda x:hashlib.sha256(("neutral-alternative-1:"+x).encode()).hexdigest())
    else:c["tie_key"]=c.economic_root_id
    c=c.sort_values(KEYS+["tie_key"],ascending=[True,True,convention!="HASH_MAX"]).drop_duplicates(KEYS)
    c=c.set_index(KEYS).join(valid).join(eligible).join(ties).join(tie_families)
    c["selection_basis"]=[stages.get(key,"DETERMINISTIC_EXACT_TIE") for key in c.index]
    c["tie_break_used"]=c.exact_tie_count.gt(1)
    c["technology_tie_break_used"]=c.final_tie_family_count.gt(1)
    c["co_marginal_flag"]=c.valid_root_count.gt(1)
    c["final_tie_convention"]=convention
    c["utilisation_direction"]=utilisation_direction
    c["approach"]=approach
    return (c.reset_index(),frontier) if return_frontier else c.reset_index()


def utilisation_gap_diagnostic(offers, approach):
    """Report cross-family minimum-ratio gaps without changing selection."""
    c = offers.copy()
    if approach == "B":
        c = c.loc[c.accepted_supply_proxy].copy()
    elif approach != "A":
        raise ValueError("Diagnostic expects A or B")
    interior = c.groupby(KEYS).interior_acceptance_proxy.transform("any")
    c = c.loc[~interior | c.interior_acceptance_proxy].copy()
    c["native_ratio_uncertainty_scale"] = POWER_TOLERANCE/(c.operating_upper-c.operating_lower)
    ranked = c.sort_values(KEYS+["normalised_acceptance_ratio", "economic_root_id"])
    ranked = ranked.drop_duplicates(KEYS+["root_family"])
    ranked["family_rank"] = ranked.groupby(KEYS).cumcount()
    fields = KEYS+["root_family", "normalised_acceptance_ratio", "native_ratio_uncertainty_scale"]
    first = ranked.loc[ranked.family_rank.eq(0), fields]
    second = ranked.loc[ranked.family_rank.eq(1), fields]
    gaps = first.merge(second, on=KEYS, suffixes=("_first", "_second"), validate="one_to_one")
    gaps["cross_family_ratio_gap"] = gaps.normalised_acceptance_ratio_second-gaps.normalised_acceptance_ratio_first
    gaps["combined_native_power_tolerance_scale"] = gaps.native_ratio_uncertainty_scale_first+gaps.native_ratio_uncertainty_scale_second
    gaps["within_native_power_noise_scale"] = gaps.cross_family_ratio_gap.le(gaps.combined_native_power_tolerance_scale)
    gaps["within_declared_exact_ratio_tie"] = gaps.cross_family_ratio_gap.le(RATIO_TOLERANCE)
    gaps.insert(0,"approach",approach)
    return gaps


def withdrawal_proxy(table, physical, durations, annual):
    """Canonical UC balance proxy; never use pricing-LP primal for volumes."""
    from .canonical_results import reporting_config
    cfg=reporting_config();p=physical.copy();p["zone"]=p.zone.map(normalize_zone)
    p=p.loc[p.zone.isin(ZONES)].copy()
    if p.duplicated(["snapshot","zone"]).any() or len(p)!=len(table):raise AnalogueError("UC_VOLUME_CHRONOLOGY_CONFLICT")
    known=set(cfg["technology_order"])
    supply_columns=[x for x in p if x in known]
    p["served_withdrawal_proxy_MW"]=p.rigid_end_use_demand_MW+p.p2x_electrical_consumption_MW+p.bess_charging_MW+p.phs_charging_MW-p.load_shedding_MW
    p["balance_residual_MW"]=p[supply_columns].sum(axis=1)+p.net_imports_MW-p.served_withdrawal_proxy_MW
    expected=table[["snapshot","zone","represented_hours"]]
    p=p.merge(expected,on=["snapshot","zone"],validate="one_to_one")
    if len(p)!=len(expected):raise AnalogueError("UC_VOLUME_CHRONOLOGY_CONFLICT")
    if not np.allclose(p.represented_hours,p.snapshot.map(durations),atol=1e-12,rtol=0):raise AnalogueError("PHYSICAL_DURATION_CONFLICT")
    if p.served_withdrawal_proxy_MW.lt(0).any():raise AnalogueError("NEGATIVE_SERVED_WITHDRAWAL_VOLUME")
    p["cleared_volume_proxy_MWh"]=p.served_withdrawal_proxy_MW*p.represented_hours
    a=annual.copy();a.zone=a.zone.map(normalize_zone);a=a.set_index("zone")
    fields=["rigid_end_use_demand_TWh","p2x_electrical_consumption_TWh","bess_charging_TWh","phs_charging_TWh"]
    target=(a[fields].sum(axis=1)-a.load_shedding_TWh).reindex(ZONES)*1e6
    computed=p.groupby("zone").cleared_volume_proxy_MWh.sum().reindex(ZONES)
    error=(computed-target).abs()
    tolerance=float(cfg["balance_tolerance_TWh"])*1e6
    if error.max()>tolerance or (p.balance_residual_MW*p.represented_hours).abs().sum()>tolerance:
        raise AnalogueError("CANONICAL_VOLUME_BALANCE_RECONCILIATION_FAILURE")
    qa={"canonical_annual_volume_max_error_MWh":float(error.max()),"balance_max_abs_residual_MW":float(p.balance_residual_MW.abs().max()),
        "weighted_absolute_balance_residual_MWh":float((p.balance_residual_MW*p.represented_hours).abs().sum()),
        "national_volume_proxy_MWh":float(p.cleared_volume_proxy_MWh.sum()),"formula":RULES["volume_proxy"],"physical_authority":"CANONICAL_UC_MILP","duration_authority":"accepted annual_dispatch_raw_MW.csv interval_hours; component weights verified equal"}
    return p[["snapshot","zone","served_withdrawal_proxy_MW","cleared_volume_proxy_MWh","balance_residual_MW"]],qa


def shares(table, weight, zonal=True):
    keys=(["zone"] if zonal else [])+["selected_marginal_family"]
    values=table.groupby(keys)[weight].sum()
    if not zonal:return values.reindex(ORDER,fill_value=0)/values.sum()*100
    frame=values.unstack(fill_value=0).reindex(index=ZONES,columns=ORDER,fill_value=0)
    return frame.div(table.groupby("zone")[weight].sum(),axis=0).reindex(ZONES)*100


def attach_selection(table, selected, volume, *, allow_unresolved=False):
    selected=selected.rename(columns={"target_zone":"zone","asset_id":"selected_marginal_asset","technology":"selected_marginal_technology","root_family":"selected_marginal_family",
        "zone":"selected_root_zone","economic_root_id":"selected_economic_root_id","coupling_paths":"selected_coupling_paths","recon":"selected_reconstructed_price_EUR_MWh","residual":"selected_KKT_residual_EUR_MWh"})
    fields=["snapshot","zone","selected_marginal_asset","selected_marginal_technology","selected_marginal_family","selected_root_zone","selected_economic_root_id","selected_coupling_paths","selected_reconstructed_price_EUR_MWh","selected_KKT_residual_EUR_MWh","selection_side","selection_basis","valid_root_count","valid_family_count","co_marginal_flag","exact_tie_count","tie_break_used","final_tie_convention","utilisation_direction","normalised_acceptance_ratio","gme_priority_status"]
    fields += [x for x in ["eligible_root_count","eligible_family_count","technology_tie_break_used","final_tie_family_count","approach"] if x in selected]
    result=table.merge(selected[fields],on=["snapshot","zone"],validate="one_to_one",how="left").merge(volume,on=["snapshot","zone"],validate="one_to_one")
    if len(result)!=len(table) or (result.selected_marginal_family.isna().any() and not allow_unresolved):raise AnalogueError("INCOMPLETE_SELECTION")
    if allow_unresolved:
        missing=result.selected_marginal_family.isna()
        result.loc[missing,"selection_basis"]="UNRESOLVED_NO_ACCEPTED_SUPPLY"
        result.loc[missing,"selection_side"]="NO_ACCEPTED_SUPPLY_ROOT"
        result.loc[missing,"valid_root_count"]=result.loc[missing,"final_root_count"]
        result.loc[missing,"valid_family_count"]=result.loc[missing,"final_root_family_count"]
        for field in ["eligible_root_count","eligible_family_count"]:
            result.loc[missing,field]=0
        result.loc[missing,"approach"]="B"

    result["original_primary_attribution_class"]=result.primary_attribution_class
    result["original_root_families"]=result.final_root_families
    return result


def run_case(year, scenario, root, report_dir, raw_dispatch, output):
    from .uc2_postprocess import no_solver_calls
    root,report_dir,output=Path(root),Path(report_dir),Path(output)
    if output.resolve() in [root.resolve(),(root/"V2").resolve(),(root/"FINAL_RESOLVER_V2").resolve()]:raise AnalogueError("PROTECTED_OUTPUT_TARGET")
    label=f"{year}_{scenario}"
    prior=root/"FINAL_RESOLVER_V2"/scenario;v2=root/"V2"/scenario
    q=json.loads((prior/f"marginal_setter_final_QA_{label}.json").read_text(encoding="utf-8"))
    rq=json.loads((report_dir/"MEM_UC2_VIS_REPORTING_R1_RECEIPT.json").read_text(encoding="utf-8"))
    if q["status"]!="PASS_FINAL_FAMILY_RESOLUTION" or rq["status"]!="PASS" or rq["scenario"]!=scenario or rq["year"]!=year:raise AnalogueError("SOURCE_AUTHORITY_CONFLICT")
    table_path=prior/f"marginal_setter_final_attribution_{label}.parquet"
    candidate_path=v2/f"marginal_setter_candidates_{label}.parquet"
    physical_path=report_dir/"VIS_X1/data/canonical_uc_dispatch_by_zone.parquet"
    annual_path=report_dir/"CANONICAL/statistics/annual_electrical_balance_by_zone.csv"
    sources=[table_path,candidate_path,physical_path,annual_path,Path(raw_dispatch),prior/f"marginal_setter_final_QA_{label}.json",report_dir/"MEM_UC2_VIS_REPORTING_R1_RECEIPT.json"]
    if sha256_file(table_path)!=q["artifact_hashes"][table_path.name] or sha256_file(candidate_path)!=q["source_hashes"][str(candidate_path)]:raise AnalogueError("SOURCE_AUTHORITY_CONFLICT: root hashes")
    known={str((report_dir/name).resolve()):digest for name,digest in rq["artifact_hashes"].items()}
    if known.get(str(physical_path.resolve()))!=sha256_file(physical_path):raise AnalogueError("SOURCE_AUTHORITY_CONFLICT: UC volume hash")
    hashes={str(p.resolve()):sha256_file(p) for p in sources}
    output.mkdir(parents=True,exist_ok=True)
    with no_solver_calls() as guard:
        table=pd.read_parquet(table_path);audit=pd.read_parquet(candidate_path);validate_structure(table)
        raw=pd.read_csv(raw_dispatch,index_col="snapshot",parse_dates=True)
        if len(raw)!=8760 or not raw.index.equals(pd.date_range("2019-01-01",periods=8760,freq="h")):raise AnalogueError("PHYSICAL_CHRONOLOGY_CONFLICT")
        if not np.allclose(raw[["generator_weight_hours","objective_weight_hours"]],raw.interval_hours.to_numpy()[:,None],atol=1e-12,rtol=0):raise AnalogueError("PHYSICAL_DURATION_CONFLICT")
        volume,vqa=withdrawal_proxy(table,pd.read_parquet(physical_path),raw.interval_hours,pd.read_csv(annual_path))
        offers=prepare_offers(table,audit)
        selected=select_offers(offers);result=attach_selection(table,selected,volume);validate_structure(result)
        if not result[table.columns].equals(table):raise AnalogueError("SOURCE_ATTRIBUTION_MUTATION")
        alternative=select_offers(prepare_offers(table,pd.concat([audit.iloc[::-1],audit.iloc[:1000].assign(coupling_path='["duplicate_path"]')],ignore_index=True)))
        cols=["snapshot","target_zone","economic_root_id","selection_basis","valid_root_count","valid_family_count","exact_tie_count"]
        if not selected[cols].reset_index(drop=True).equals(alternative[cols].reset_index(drop=True)):raise AnalogueError("ORDER_OR_PATH_SELECTION_EFFECT")
        offers.to_parquet(output/f"model_implied_marginal_offers_{label}.parquet",index=False)
        result.to_parquet(output/f"model_implied_marginal_technology_{label}.parquet",index=False)
        result.to_csv(output/f"model_implied_marginal_technology_{label}.csv",index=False)
        sensitivity=[];max_shift=0.;changed={}
        for convention in ("HASH_MIN","HASH_MAX","HASH_ALTERNATE"):
            test=attach_selection(table,select_offers(offers,convention),volume)
            count=int(test.selected_marginal_family.ne(result.selected_marginal_family).sum());changed[convention]=count
            for weight in ("represented_hours","cleared_volume_proxy_MWh"):
                national=shares(test,weight,False)
                zonal=shares(test,weight)
                shift=float((zonal-shares(result,weight)).abs().max().max());max_shift=max(max_shift,shift)
                for family,value in national.items():sensitivity.append(dict(convention=convention,weight=weight,family=family,national_share_percent=value,max_zonal_share_shift_percentage_points=shift,changed_family_zone_hours=count))
        pd.DataFrame(sensitivity).to_csv(output/f"single_setter_tie_sensitivity_{label}.csv",index=False)
        # Additional diagnostic exposes the synthetic ratio-direction assumption.
        reverse=attach_selection(table,select_offers(offers,utilisation_direction="HIGHEST"),volume)
        utilisation=pd.DataFrame({"minimum_ratio_volume_share_percent":shares(result,"cleared_volume_proxy_MWh",False),"maximum_ratio_volume_share_percent":shares(reverse,"cleared_volume_proxy_MWh",False)})
        utilisation.to_csv(output/f"utilisation_direction_sensitivity_{label}.csv",index_label="family")
        for weight,name in [("represented_hours","time"),("cleared_volume_proxy_MWh","volume")]:
            frame=shares(result,weight)
            if (frame.sum(axis=1)-100).abs().max()>1e-10:raise AnalogueError("SHARE_RECONCILIATION_FAILURE")
            frame.to_csv(output/f"single_setter_{name}_shares_{label}.csv",index_label="zone")
        basis=result.groupby("selection_basis").agg(zone_hours=("represented_hours","sum"),volume_proxy_MWh=("cleared_volume_proxy_MWh","sum"),observations=("zone","size")).reindex(BASES,fill_value=0)
        basis["time_share_percent"]=basis.zone_hours/result.represented_hours.sum()*100
        basis["volume_share_percent"]=basis.volume_proxy_MWh/result.cleared_volume_proxy_MWh.sum()*100
        basis.to_csv(output/f"selection_basis_QA_{label}.csv",index_label="selection_basis")
        report=dict(status="PASS_SYNTHETIC_SELECTION_REVIEW" if not any(changed.values()) else "FINAL_TIE_TECHNOLOGY_SENSITIVITY_REQUIRES_REVIEW",version=VERSION,rules=RULES,model_year=year,scenario=scenario,rows=len(result),hours_per_zone=result.groupby("zone").represented_hours.sum().to_dict(),selection_basis=basis.to_dict("index"),selection_side_counts=result.selection_side.value_counts().to_dict(),time_shares_percent=shares(result,"represented_hours",False).to_dict(),volume_shares_percent=shares(result,"cleared_volume_proxy_MWh",False).to_dict(),max_final_tie_share_shift_percentage_points=max_shift,final_tie_changed_family_zone_hours=changed,utilisation_direction_changed_family_zone_hours=int(reverse.selected_marginal_family.ne(result.selected_marginal_family).sum()),volume_QA=vqa,original_attribution_values_identical=True,selected_root_membership_verified=True,ordering_duplicate_paths_invariant=True,max_KKT_residual_EUR_MWh=float(result.selected_KKT_residual_EUR_MWh.abs().max()),solver_invocations=guard["solver_invocations"],network_opens=0,routine_activation=False,source_hashes=hashes,implementation_sha256=sha256_file(Path(__file__)),artifact_hashes={p.name:sha256_file(p) for p in output.glob('*') if p.suffix in ("csv",".csv",".parquet")})
        (output/f"single_setter_QA_{label}.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    return result,report


UNRESOLVED = "Unresolved: no accepted supply"
COMPARE_BASES = ("UNIQUE_VALID_ROOT", "UNIQUE_VALID_FAMILY", "GME_PRIORITY_RESOLUTION", "PARTIAL_ACCEPTANCE_PROXY", "UTILISATION_PROXY", "DETERMINISTIC_EXACT_TIE", "UNRESOLVED_NO_ACCEPTED_SUPPLY")


def comparison_shares(table, weight, zonal=False):
    t=table.copy();t["selected_marginal_family"]=t.selected_marginal_family.fillna(UNRESOLVED)
    keys=(["zone"] if zonal else [])+["selected_marginal_family"]
    total=t.groupby(keys)[weight].sum()
    order=(*ORDER,UNRESOLVED)
    if not zonal:return total.reindex(order,fill_value=0)/total.sum()*100
    frame=total.unstack(fill_value=0).reindex(index=ZONES,columns=order,fill_value=0)
    return frame.div(t.groupby("zone")[weight].sum(),axis=0).reindex(ZONES)*100


def run_comparison(year,scenario,root,report_dir,raw_dispatch,output):
    """Review both methods without selecting/activating a final methodology."""
    from .uc2_postprocess import no_solver_calls
    root,report_dir,output=Path(root).resolve(),Path(report_dir).resolve(),Path(output).resolve()
    validate_review_output(root, output)
    label=f"{year}_{scenario}";prior=root/"FINAL_RESOLVER_V2"/scenario;v2=root/"V2"/scenario
    qpath=prior/f"marginal_setter_final_QA_{label}.json";q=json.loads(qpath.read_text(encoding="utf-8"))
    rpath=report_dir/"MEM_UC2_VIS_REPORTING_R1_RECEIPT.json";rq=json.loads(rpath.read_text(encoding="utf-8"))
    tpath=prior/f"marginal_setter_final_attribution_{label}.parquet";apath=v2/f"marginal_setter_candidates_{label}.parquet"
    ppath=report_dir/"VIS_X1/data/canonical_uc_dispatch_by_zone.parquet";annual_path=report_dir/"CANONICAL/statistics/annual_electrical_balance_by_zone.csv"
    if q["status"]!="PASS_FINAL_FAMILY_RESOLUTION" or rq["status"]!="PASS" or rq["scenario"]!=scenario or rq["year"]!=year:raise AnalogueError("SOURCE_AUTHORITY_CONFLICT")
    if sha256_file(tpath)!=q["artifact_hashes"][tpath.name] or sha256_file(apath)!=q["source_hashes"][str(apath)]:raise AnalogueError("SOURCE_AUTHORITY_CONFLICT: V2 root hashes")
    expected={str((report_dir/name).resolve()):digest for name,digest in rq["artifact_hashes"].items()}
    if expected.get(str(ppath))!=sha256_file(ppath):raise AnalogueError("SOURCE_AUTHORITY_CONFLICT: canonical volume")
    hashes={str(p):sha256_file(p) for p in [tpath,apath,ppath,annual_path,Path(raw_dispatch),qpath,rpath]}
    output.mkdir(parents=True,exist_ok=True)
    qa={};basis_rows=[];share_rows=[];sensitivity_rows=[];zero_rows=[];granularity_rows=[];selections={}
    with no_solver_calls() as guard:
        table=pd.read_parquet(tpath);audit=pd.read_parquet(apath);validate_structure(table)
        if not table.model_year.eq(year).all() or not table.scenario.eq(scenario).all():raise AnalogueError("SOURCE_AUTHORITY_CONFLICT: scenario")
        raw=pd.read_csv(raw_dispatch,index_col="snapshot",parse_dates=True)
        if not raw.index.equals(pd.date_range("2019-01-01",periods=8760,freq="h")) or not np.allclose(raw[["generator_weight_hours","objective_weight_hours"]],raw.interval_hours.to_numpy()[:,None],atol=1e-12,rtol=0):raise AnalogueError("PHYSICAL_DURATION_CONFLICT")
        volume,vqa=withdrawal_proxy(table,pd.read_parquet(ppath),raw.interval_hours,pd.read_csv(annual_path))
        offers=prepare_offers(table,audit)
        repeated=prepare_offers(table,pd.concat([audit.iloc[::-1],audit.iloc[:1000].assign(coupling_path='["duplicate_path"]')],ignore_index=True))
        offers.to_parquet(output/f"complete_V2_model_root_evidence_{label}.parquet",index=False)
        for approach in ("A","B"):
            selected,frontier=select_offers(offers,approach=approach,return_frontier=True)
            result=attach_selection(table,selected,volume,allow_unresolved=approach=="B")
            validate_structure(result)
            if not result[table.columns].equals(table):raise AnalogueError("V2_VALUE_MUTATION")
            other=select_offers(repeated,approach=approach)
            checked=["snapshot","target_zone","economic_root_id","selection_basis","valid_root_count","valid_family_count","exact_tie_count"]
            if not selected[checked].reset_index(drop=True).equals(other[checked].reset_index(drop=True)):raise AnalogueError("ORDER_OR_PATH_SELECTION_EFFECT")
            # Membership checked explicitly against the original complete audit identities.
            membership=selected[KEYS+["economic_root_id"]].merge(offers[KEYS+["economic_root_id"]],on=KEYS+["economic_root_id"],how="left",indicator=True,validate="one_to_one")
            if not membership._merge.eq("both").all():raise AnalogueError("SELECTED_ROOT_OUTSIDE_V2")
            result.to_parquet(output/f"approach_{approach}_selected_roots_{label}.parquet",index=False)
            result.to_csv(output/f"approach_{approach}_selected_roots_{label}.csv",index=False)
            selections[approach]=result
            grouped=result.groupby("selection_basis").agg(zone_hours=("represented_hours","sum"),volume_proxy_MWh=("cleared_volume_proxy_MWh","sum")).reindex(COMPARE_BASES,fill_value=0)
            for basis,r in grouped.iterrows():basis_rows.append(dict(scenario=scenario,approach=approach,basis=basis,zone_hours=r.zone_hours,time_share_percent=r.zone_hours/result.represented_hours.sum()*100,volume_share_percent=r.volume_proxy_MWh/result.cleared_volume_proxy_MWh.sum()*100))
            max_shift=0.;changes={}
            for convention in ("HASH_MIN","HASH_MAX","HASH_ALTERNATE"):
                test=attach_selection(table,select_offers(offers,approach=approach,convention=convention),volume,allow_unresolved=approach=="B")
                changed=int(test.selected_marginal_family.fillna(UNRESOLVED).ne(result.selected_marginal_family.fillna(UNRESOLVED)).sum());changes[convention]=changed
                for weight in ("represented_hours","cleared_volume_proxy_MWh"):
                    values=comparison_shares(test,weight);zones=comparison_shares(test,weight,True)
                    shift=float((zones-comparison_shares(result,weight,True)).abs().max().max());max_shift=max(max_shift,shift)
                    for family,value in values.items():sensitivity_rows.append(dict(scenario=scenario,approach=approach,convention=convention,weight=weight,family=family,share_percent=value,changed_family_zone_hours=changed,max_zonal_shift_percentage_points=shift))
            for weight in ("represented_hours","cleared_volume_proxy_MWh"):
                values=comparison_shares(result,weight);zones=comparison_shares(result,weight,True)
                if (zones.sum(axis=1)-100).abs().max()>1e-10:raise AnalogueError("SHARE_SUM_FAILURE")
                for family,value in values.items():share_rows.append(dict(scenario=scenario,approach=approach,weight=weight,family=family,share_percent=value))
                zones.to_csv(output/f"approach_{approach}_{weight}_zonal_shares_{label}.csv",index_label="zone")
            zero=result.loc[result.zonal_price_EUR_MWh.abs().le(TOLERANCE)]
            z=zero.assign(category=zero.selected_marginal_family.fillna(UNRESOLVED)).groupby("category").represented_hours.sum()
            for family in (*ORDER,UNRESOLVED):zero_rows.append(dict(scenario=scenario,approach=approach,family=family,zero_price_zone_hours=float(z.get(family,0)),share_of_zero_price_hours_percent=float(z.get(family,0))/zero.represented_hours.sum()*100,total_zero_price_zone_hours=float(zero.represented_hours.sum())))
            # Counts reveal unit-level representation bias without adding/splitting roots.
            mixed=frontier.groupby(KEYS).root_family.nunique().gt(1)
            if mixed.any():
                ties=frontier.set_index(KEYS).loc[mixed[mixed].index].reset_index()
                fc=ties.groupby(KEYS+["root_family"]).economic_root_id.nunique().rename("root_count").reset_index()
                fc["total_tied_roots"]=fc.groupby(KEYS).root_count.transform("sum")
                fc["neutral_root_sampling_probability"]=fc.root_count/fc.total_tied_roots
                fc.insert(0,"approach",approach);fc.to_csv(output/f"approach_{approach}_mixed_tie_root_count_bias_{label}.csv",index=False)
            for family,g in offers.groupby("root_family"):
                granularity_rows.append(dict(scenario=scenario,approach=approach,family=family,valid_candidate_root_rows=len(g),distinct_assets=g.asset_id.nunique(),zone_hours_with_family=g[KEYS].drop_duplicates().shape[0]))
            # Explicit second proxy direction; not a GME rule or alternate KKT method.
            reverse=attach_selection(table,select_offers(offers,approach=approach,utilisation_direction="HIGHEST"),volume,allow_unresolved=approach=="B")
            reverse_shares=pd.DataFrame({"minimum_ratio_time_share_percent":comparison_shares(result,"represented_hours"),"maximum_ratio_time_share_percent":comparison_shares(reverse,"represented_hours"),"minimum_ratio_volume_share_percent":comparison_shares(result,"cleared_volume_proxy_MWh"),"maximum_ratio_volume_share_percent":comparison_shares(reverse,"cleared_volume_proxy_MWh")})
            reverse_shares.to_csv(output/f"approach_{approach}_utilisation_direction_sensitivity_{label}.csv",index_label="family")
            qa[approach]=dict(rows=len(result),unresolved_zone_hours=int(result.selected_marginal_family.isna().sum()),complete_original_V2_columns_identical=True,selected_membership_verified=True,order_duplicate_path_invariance=True,max_KKT_residual_EUR_MWh=float(result.selected_KKT_residual_EUR_MWh.abs().max()),final_tie_family_changes=changes,max_final_tie_zonal_shift_percentage_points=max_shift,ratio_direction_family_changes=int(result.selected_marginal_family.fillna(UNRESOLVED).ne(reverse.selected_marginal_family.fillna(UNRESOLVED)).sum()),selected_side_counts=result.selection_side.value_counts().to_dict(),technology_tie_zone_hours=int(result.technology_tie_break_used.fillna(False).sum()))
        differences=pd.DataFrame({"A":selections['A'].selected_marginal_family,"B":selections['B'].selected_marginal_family.fillna(UNRESOLVED),"represented_hours":table.represented_hours})
        differences.groupby(["A","B"]).represented_hours.sum().rename("zone_hours").reset_index().to_csv(output/f"A_to_B_technology_transition_{label}.csv",index=False)
        for data,name in [(basis_rows,"selection_basis"),(share_rows,"family_shares"),(sensitivity_rows,"exact_tie_sensitivity"),(zero_rows,"zero_price_attribution"),(granularity_rows,"asset_granularity")]:
            pd.DataFrame(data).to_csv(output/f"method_comparison_{name}_{label}.csv",index=False)
        report=dict(status="METHOD_COMPARISON_COMPLETE_NO_FINAL_METHODOLOGY_SELECTED",version=VERSION,scenario=scenario,model_year=year,rules={k:v for k,v in RULES.items() if k!='supply_preference'},comparison_rules=COMPARISON_RULES,approaches=qa,volume_QA=vqa,source_hashes=hashes,solver_invocations=guard["solver_invocations"],network_opens=0,figures_generated=0,routine_activation=False,implementation_sha256=sha256_file(Path(__file__)),artifact_hashes={f.name:sha256_file(f) for f in output.iterdir() if f.suffix in (".csv",".parquet")})
        (output/f"method_comparison_QA_{label}.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    return report

"""Downstream family resolution of immutable, validated V2 marginal root sets.

No networks are opened and no candidates, KKT equations or coupling paths are
recomputed. This is an explicit candidate-review API, not routine reporting.
"""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

from ..common import sha256_file
from .marginal_setter_attribution import ZONES, TOLERANCE, normalize_zone, validate_structure

VERSION = "MEM_UC3_FINAL_ROOT_FAMILY_RESOLVER_V2"
VRE_DEFINITION = "Wind + Solar + run-of-river hydro: exogenously availability-constrained, non-intertemporal renewable generation with approximately zero short-run marginal cost in MEM"
ORDER = ("VRE", "PHS", "BESS", "Reservoir / pondage hydro",
         "P2X", "CCGT", "Other thermal", "External market", "Market coupling / multiple")
RESIDUAL = ORDER[-1]
IDENTITY = ("component", "asset_id", "zone", "technology", "carrier", "mechanism")
FAMILY_CROSSWALK = {
    ("Solar", "solar_pv_rooftop", "Generator"): "VRE",
    ("Solar", "solar_pv_utility", "Generator"): "VRE",
    ("Wind", "wind_onshore", "Generator"): "VRE",
    ("Wind", "wind_offshore", "Generator"): "VRE",
    ("PHS", "water_energy", "Link"): "PHS",
    ("BESS", "battery_energy", "Link"): "BESS",
    ("Hydro", "water_energy", "Link"): "Reservoir / pondage hydro",
    ("Hydro RoR", "hydro_run_of_river", "Generator"): "VRE",
    ("CCGT", "methane_ccgt", "Generator"): "CCGT",
    ("Bioenergy", "bioenergy", "Generator"): "Other thermal",
    ("Bioenergy", "bioenergy_internal_combustion", "Generator"): "Other thermal",
    ("Bioenergy", "bioenergy_steam_other_surviving", "Generator"): "Other thermal",
    ("Bioenergy", "bioenergy_other_surviving_thermal", "Generator"): "Other thermal",
    ("Geothermal", "geothermal", "Generator"): "Other thermal",
    ("Gas IC", "methane_internal_combustion", "Generator"): "Other thermal",
    ("Gas - OCGT", "methane_gt_ocgt", "Generator"): "Other thermal",
    ("Gas - steam thermal", "methane_steam_other_surviving", "Generator"): "Other thermal",
    ("Gas - other thermal", "methane_other_surviving_thermal", "Generator"): "Other thermal",
    ("External market opportunity", "external_market", "Generator"): "External market",
}
for _zone in (*ZONES, "CNOR"):
    FAMILY_CROSSWALK[("P2X", "p2x_flexible_" + _zone, "Generator")] = "P2X"
RULES = {
    "version": VERSION,
    "source": "Complete validated V2 candidate audit, selected local or coupled scope only",
    "hierarchy": "Inherit V2 selected root set; no remote search where local roots exist",
    "identity": list(IDENTITY),
    "deduplication": "Same root identity across paths counted once; equivalent distinct assets retained",
    "resolution": "Exactly one mapped family -> that family; >1 -> Market coupling / multiple",
    "VRE_definition": VRE_DEFINITION,
    "price_rule": "Price is not a family-resolution input; zero price is verified independently",
    "storage_separation": "PHS, BESS and reservoir/pondage remain distinct intertemporal families",
    "residual_origin": "LOCAL_MULTIPLE or REMOTE_MIXED; never used for an empty root set",
    "unmapped": "Fail closed; Other thermal is explicit, not fallback; VOLL flagged separately",
    "interpretation": "Technology family of KKT-consistent supporting roots, not unique causal attribution",
    "activation": "CANDIDATE_REVIEW_NOT_ROUTINE",
}


class ResolverError(RuntimeError):
    pass


def root_family(root):
    if root["carrier"] == "load_shedding" or root["technology"] == "Load shedding / VOLL":
        raise ResolverError("LOAD_SHEDDING_SETTER_REVIEW: " + str(root))
    key = (root["technology"], root["carrier"], root["component"])
    if key not in FAMILY_CROSSWALK:
        raise ResolverError("UNMAPPED_SETTER_TECHNOLOGY: " + str(key))
    return FAMILY_CROSSWALK[key]


def family_resolution(families, scope):
    if scope not in ("LOCAL", "COUPLED"):
        raise ResolverError("SOURCE_AUTHORITY_CONFLICT: invalid resolution scope")
    families = sorted(set(families), key=ORDER.index)
    n = len(families)
    return {
        "final_setter_category": families[0] if n == 1 else RESIDUAL if n > 1 else None,
        "final_resolution_rule": "SINGLE_ROOT_FAMILY" if n == 1 else "MIXED_ROOT_FAMILIES" if n > 1 else "UNRESOLVED_NO_VALID_ROOT_SET",
        "final_root_family_count": n,
        "final_root_families": json.dumps(families),
        "resolution_scope": scope,
        "residual_origin": ("REMOTE_MIXED" if scope == "COUPLED" else "LOCAL_MULTIPLE") if n > 1 else None,
    }


def resolve_root_set(roots, scope):
    """Small pure API protecting family and path-order semantics."""
    distinct = {tuple(r[k] for k in IDENTITY): r for r in roots}
    result = family_resolution([root_family(r) for r in distinct.values()], scope)
    result.update(final_root_count=len(distinct),
                  final_root_zone_count=len({normalize_zone(r["zone"]) for r in distinct.values()}),
                  final_root_technology_count=len({r["technology"] for r in distinct.values()}))
    return result


def map_candidates(audit):
    observed = audit[["technology", "carrier", "component"]].drop_duplicates()
    mapping = {tuple(r): root_family(dict(zip(("technology", "carrier", "component"), r)))
               for r in observed.itertuples(index=False, name=None)}
    mapped = audit.copy()
    mapped["root_family"] = pd.MultiIndex.from_frame(mapped[["technology", "carrier", "component"]]).map(mapping)
    if mapped.root_family.isna().any():
        raise ResolverError("UNMAPPED_SETTER_TECHNOLOGY")
    return mapped


def resolve_attribution(table, audit):
    """Append fields without changing any V2 value, identity or ordering."""
    mapped = map_candidates(audit)
    keys = ["snapshot", "target_zone"]
    scopes = mapped.groupby(keys).remote.nunique()
    if scopes.gt(1).any():
        raise ResolverError("SOURCE_AUTHORITY_CONFLICT: mixed local/remote selected root set")
    unique = mapped.drop_duplicates(keys + list(IDENTITY))
    g = unique.groupby(keys, sort=False)
    fields = g.agg(final_root_count=("asset_id", "size"),
                   final_root_zone_count=("zone", "nunique"),
                   final_root_technology_count=("technology", "nunique"),
                   families=("root_family", lambda x: tuple(sorted(set(x), key=ORDER.index))),
                   remote=("remote", "first")).reset_index().rename(columns={"target_zone": "zone"})
    if len(fields.merge(table[["snapshot", "zone"]], on=["snapshot", "zone"], how="left", indicator=True).query("_merge == 'left_only'")):
        raise ResolverError("SOURCE_AUTHORITY_CONFLICT: orphan candidate zone-hour")
    merged = table.merge(fields, on=["snapshot", "zone"], how="left", validate="one_to_one", sort=False)
    additions = []
    for r in merged.itertuples(index=False):
        scope = "COUPLED" if r.primary_attribution_class == "MARKET_COUPLING" else "LOCAL"
        families = r.families if isinstance(r.families, tuple) else ()
        if families and bool(r.remote) != (scope == "COUPLED"):
            raise ResolverError("SOURCE_AUTHORITY_CONFLICT: V2 local-root hierarchy differs from audit")
        if families and r.primary_attribution_class == "OTHER_INDETERMINATE":
            raise ResolverError("SOURCE_AUTHORITY_CONFLICT: indeterminate V2 row has selected roots")
        resolved = family_resolution(families, scope)
        if resolved["residual_origin"] == "LOCAL_MULTIPLE" and r.primary_attribution_class != "MULTIPLE_CANDIDATES":
            raise ResolverError("SOURCE_AUTHORITY_CONFLICT: mixed local roots outside V2 Multiple")
        additions.append(resolved)
    result = merged.drop(columns=["families", "remote"])
    for key in additions[0]:
        result[key] = [r[key] for r in additions]
    for key in ("final_root_count", "final_root_zone_count", "final_root_technology_count"):
        result[key] = result[key].fillna(0).astype(int)
    if not result[table.columns].equals(table.reset_index(drop=True)):
        raise ResolverError("V2_ATTRIBUTION_MUTATION")
    return result, mapped


def diagnostics(table, mapped):
    """Weighted VRE, residual and recovery diagnostics; no causal heuristic."""
    vre = table.loc[table.final_setter_category.eq("VRE")].copy()
    roots = mapped.merge(vre[["snapshot", "zone"]].rename(columns={"zone": "target_zone"}),
                         on=["snapshot", "target_zone"], validate="many_to_one")
    sets = roots.groupby(["snapshot", "target_zone"]).technology.agg(vre_root_label)
    vre = vre.merge(sets.rename("VRE_root_set"), left_on=["snapshot", "zone"], right_index=True, validate="one_to_one").reset_index(drop=True)
    clean = roots.cost.abs().le(TOLERANCE) & roots[["lower", "upper", "gradient"]].abs().max(axis=1).le(TOLERANCE)
    roots["zero_cost_zero_scarcity_no_ramp"] = clean
    conditions = roots.groupby(["snapshot", "target_zone"]).zero_cost_zero_scarcity_no_ramp.all()
    vre = vre.merge(conditions.rename("zero_cost_zero_scarcity_no_ramp"), left_on=["snapshot", "zone"], right_index=True, validate="one_to_one").reset_index(drop=True)
    vre["approximately_zero_price"] = vre.zonal_price_EUR_MWh.abs().le(TOLERANCE)
    # Remote Link efficiency/cost transformations can legitimately change a
    # zero-cost root's target price. Keep actual V2 reconstructed price/path.
    exceptions = vre.loc[~vre.approximately_zero_price].copy()
    reasons=[]
    for r in exceptions.itertuples(index=False):
        evidence=roots.loc[roots.snapshot.eq(r.snapshot) & roots.target_zone.eq(r.zone)]
        facts=[]
        if evidence.cost.abs().gt(TOLERANCE).any():facts.append("NONZERO_MODELED_MARGINAL_COST")
        if evidence.gradient.abs().gt(TOLERANCE).any():facts.append("RETAINED_RAMP_STATIONARITY_CONTRIBUTION")
        if evidence[["lower","upper"]].abs().max().max()>TOLERANCE:facts.append("RETAINED_BOUND_CONTRIBUTION")
        if r.resolution_scope=="COUPLED":facts.append("VALIDATED_COUPLING_TARGET_PRICE_TRANSFORMATION; paths="+json.dumps(sorted(set(evidence.coupling_path))))
        reasons.append("; ".join(facts) or "UNEXPLAINED_NONZERO_VRE_TARGET_PRICE_REQUIRES_REVIEW")
    exceptions["reason"] = reasons
    if len(exceptions):
        valid = exceptions.stationarity_residual_EUR_MWh.abs().le(TOLERANCE)
        if not valid.all():
            raise ResolverError("MATERIAL_KKT_RECONSTRUCTION_FAILURE: VRE target prices")
    def row(group, zone, split, scope):
        price = group.zonal_price_EUR_MWh
        hours = float(group.represented_hours.sum())
        zone_hours=float(table.loc[table.zone.eq(zone)].represented_hours.sum() if zone != "ALL" else table.represented_hours.sum())
        return dict(zone=zone, VRE_root_set=split, resolution_scope=scope,
                    zone_hours=hours, scenario_share_percent=hours/table.represented_hours.sum()*100,
                    zone_share_percent=hours/zone_hours*100 if zone_hours else None,
                    price_min_EUR_MWh=float(price.min()), price_p5_EUR_MWh=float(price.quantile(.05)),
                    price_median_EUR_MWh=float(price.median()), price_p95_EUR_MWh=float(price.quantile(.95)),
                    price_max_EUR_MWh=float(price.max()), zero_price_zone_hours=float(group.loc[group.approximately_zero_price].represented_hours.sum()),
                    clean_zero_cost_zone_hours=float(group.loc[group.zero_cost_zero_scarcity_no_ramp].represented_hours.sum()),
                    clean_nonzero_price_zone_hours=float(group.loc[group.zero_cost_zero_scarcity_no_ramp & ~group.approximately_zero_price].represented_hours.sum()))
    # Zero pure-VRE observations are an analytical result, not missing data.
    # Export explicit zeros and null price distributions for the empty groups.
    vre_rows=[row(vre,"ALL","ALL","ALL")]
    for split in ("Wind-only","Solar-only","Solar+Wind","RoR-only","Wind+RoR","Solar+RoR","Solar+Wind+RoR"):
        vre_rows.append(row(vre.loc[vre.VRE_root_set.eq(split)],"ALL",split,"ALL"))
    for scope in ("LOCAL","COUPLED"):
        vre_rows.append(row(vre.loc[vre.resolution_scope.eq(scope)],"ALL","ALL",scope))
    for zone in ZONES:vre_rows.append(row(vre.loc[vre.zone.eq(zone)],zone,"ALL","ALL"))
    if len(vre):
        for (zone,split,scope),group in vre.groupby(["zone","VRE_root_set","resolution_scope"]):
            vre_rows.append(row(group,zone,split,scope))
    residual = table.loc[table.final_setter_category.eq(RESIDUAL)].copy()
    residual["root_family_combination"] = residual.final_root_families.map(lambda x: " + ".join(json.loads(x)))
    residual = residual.groupby(["model_year","scenario","zone","primary_attribution_class","residual_origin","root_family_combination"]).represented_hours.sum().rename("zone_hours").reset_index()
    residual["scenario_share_percent"] = residual.zone_hours/table.represented_hours.sum()*100
    totals = table.groupby("zone").represented_hours.sum()
    residual["zone_share_percent"] = residual.zone_hours/residual.zone.map(totals)*100
    recovery = table.loc[table.primary_attribution_class.isin(["MARKET_COUPLING","MULTIPLE_CANDIDATES"])].groupby(["primary_attribution_class","final_setter_category"]).represented_hours.sum().rename("zone_hours").reset_index()
    recovery["share_of_original_class_percent"] = recovery.zone_hours/recovery.groupby("primary_attribution_class").zone_hours.transform("sum")*100
    return pd.DataFrame(vre_rows), exceptions, residual, recovery


def vre_root_label(technologies):
    """Keep the actual renewable root composition in diagnostics."""
    names = {"Solar": "Solar", "Wind": "Wind", "Hydro RoR": "RoR"}
    present = set(technologies)
    if present - names.keys():
        raise ResolverError("UNMAPPED_VRE_ROOT_TECHNOLOGY: " + str(present))
    labels = [names[t] for t in names if t in present]
    return "+".join(labels) + ("-only" if len(labels) == 1 else "")


def vre_candidate_context(table, mapped):
    """Explain VRE inside mixed sets without changing the resolved category."""
    bearing=table.loc[table.final_root_families.map(lambda x:"VRE" in json.loads(x))].copy()
    roots=mapped.loc[mapped.root_family.eq("VRE")].copy()
    keys=["snapshot","target_zone"]
    roots["bound_dual_abs"]=roots[["lower","upper"]].abs().max(axis=1)
    facts=roots.groupby(keys).agg(
        VRE_underlying_root_set=("technology",vre_root_label),
        includes_run_of_river=("technology",lambda x:"Hydro RoR" in set(x)),
        VRE_root_count=("asset_id","nunique"),VRE_cost_max_abs_EUR_MWh=("cost",lambda x:float(x.abs().max())),
        VRE_bound_dual_max_abs_EUR_MWh=("bound_dual_abs","max"),
        VRE_ramp_gradient_max_abs_EUR_MWh=("gradient",lambda x:float(x.abs().max())),
        VRE_KKT_max_abs_EUR_MWh=("residual",lambda x:float(x.abs().max())),
        VRE_reconstructed_target_min_EUR_MWh=("recon","min"),VRE_reconstructed_target_max_EUR_MWh=("recon","max"))
    out=bearing[["model_year","scenario","snapshot","zone","represented_hours","zonal_price_EUR_MWh","primary_attribution_class","final_setter_category","resolution_scope","final_root_families"]].merge(facts,left_on=["snapshot","zone"],right_index=True,validate="one_to_one").reset_index(drop=True)
    out["approximately_zero_target_price"]=out.zonal_price_EUR_MWh.abs().le(TOLERANCE)
    out["diagnostic_role"]="VRE_ROOTS_WITHIN_COMPLETE_SET_NOT_VRE_ONLY_RESOLUTION"
    return out


def run_case(year, scenario, v2_folder, output, frozen_methodology):
    """Read only persisted V2 tables. No .nc opening or model invocation."""
    from .uc2_postprocess import no_solver_calls
    from . import marginal_setter_attribution as engine
    v2_folder, output = Path(v2_folder), Path(output)
    if output.resolve() == v2_folder.resolve() or v2_folder.resolve() in output.resolve().parents:
        raise ResolverError("V2_ARTIFACT_MUTATION: use a separate review layer")
    label=f"{year}_{scenario}"; output.mkdir(parents=True, exist_ok=True)
    with no_solver_calls() as guard:
        freeze_path=Path(frozen_methodology); freeze=json.loads(freeze_path.read_text())
        qa_path=v2_folder/f"marginal_setter_QA_{label}.json"; qa=json.loads(qa_path.read_text())
        if freeze["methodology"] != engine.RULES or freeze["implementation_sha256"] != sha256_file(Path(engine.__file__)):
            raise ResolverError("V2_METHODOLOGY_MODIFICATION")
        if qa["status"] not in ("PASS_BASE_SEMANTIC_GATE","PASS_FROZEN_METHOD_ATTRIBUTION") or qa["frozen_methodology_sha256"] != sha256_file(freeze_path):
            raise ResolverError("SOURCE_AUTHORITY_CONFLICT: V2 gate/freeze")
        source_files=[v2_folder/f"marginal_setter_attribution_{label}.parquet",v2_folder/f"marginal_setter_candidates_{label}.parquet"]
        for file in source_files:
            if sha256_file(file) != qa["artifact_hashes"][file.name]:
                raise ResolverError("SOURCE_AUTHORITY_CONFLICT: V2 source hash " + str(file))
        source_hashes={str(file):sha256_file(file) for file in source_files+[qa_path,freeze_path]}
        table=pd.read_parquet(source_files[0]);audit=pd.read_parquet(source_files[1])
        validate_structure(table)
        if not table.model_year.eq(year).all() or not table.scenario.eq(scenario).all():
            raise ResolverError("SOURCE_AUTHORITY_CONFLICT: case metadata")
        resolved,mapped=resolve_attribution(table,audit)
        resolved.to_parquet(output/f"marginal_setter_final_attribution_{label}.parquet",index=False)
        resolved.to_csv(output/f"marginal_setter_final_attribution_{label}.csv",index=False)
        if resolved.final_setter_category.isna().any():
            (output/f"marginal_setter_final_QA_{label}.json").write_text(json.dumps({"status":"UNRESOLVED_SETTER_ROOT_SET","zone_hours":int(resolved.final_setter_category.isna().sum()),"source_hashes":source_hashes},indent=2))
            raise ResolverError("UNRESOLVED_SETTER_ROOT_SET: retained in derived table; presentation stopped")
        if not (resolved.loc[resolved.final_setter_category.ne(RESIDUAL)].final_root_family_count.eq(1).all()
                and resolved.loc[resolved.final_setter_category.eq(RESIDUAL)].final_root_family_count.ge(2).all()):
            raise ResolverError("RESOLUTION_QA_FAILURE: residual family cardinality")
        # Full-case reversal plus duplicated paths must leave every final field
        # unchanged; no alternative root discovery or KKT rerun is involved.
        shuffled=pd.concat([audit.iloc[::-1],audit.iloc[:min(1000,len(audit))].assign(coupling_path='["duplicate_test_path"]')],ignore_index=True)
        alternate,_=resolve_attribution(table,shuffled)
        if not resolved.equals(alternate):
            raise ResolverError("RESOLUTION_QA_FAILURE: candidate ordering/path multiplicity")
        shares=resolved.groupby(["zone","final_setter_category"]).represented_hours.sum().unstack(fill_value=0).reindex(index=ZONES,columns=ORDER,fill_value=0)
        shares=shares.div(resolved.groupby("zone").represented_hours.sum(),axis=0)*100
        share_error=float((shares.sum(axis=1)-100).abs().max())
        if share_error>1e-10:raise ResolverError("RESOLUTION_QA_FAILURE: shares")
        shares.to_csv(output/f"marginal_setter_final_shares_{label}.csv",index_label="zone")
        vre,exceptions,residual,recovery=diagnostics(resolved,mapped)
        vre.insert(0,"scenario",scenario);vre.insert(0,"model_year",year)
        vre.to_csv(output/f"marginal_setter_VRE_diagnostic_{label}.csv",index=False)
        exceptions.to_csv(output/f"marginal_setter_VRE_nonzero_price_evidence_{label}.csv",index=False)
        residual.to_csv(output/f"marginal_setter_residual_diagnostic_{label}.csv",index=False)
        recovery.to_csv(output/f"marginal_setter_resolution_recovery_{label}.csv",index=False)
        context=vre_candidate_context(resolved,mapped)
        context.to_csv(output/f"marginal_setter_VRE_candidate_context_{label}.csv",index=False)
        pairs=mapped.groupby(["technology","carrier","component","root_family"],dropna=False).agg(valid_candidate_rows=("asset_id","size"),mechanisms=("mechanism",lambda x:json.dumps(sorted(set(x))))).reset_index()
        pairs["source_authority"]="Validated V2 root technology + config/stage_b_reporting.yaml + MEM_Hydro_Static_Component_Mapping.csv (inherited; not re-inferred)"
        pairs.to_csv(output/f"marginal_setter_root_family_crosswalk_{label}.csv",index=False)
        report={
            "status":"PASS_FINAL_FAMILY_RESOLUTION","version":VERSION,"rules":RULES,"model_year":year,"scenario":scenario,
            "rows":len(resolved),"zones":list(ZONES),"hours_per_zone":resolved.groupby("zone").represented_hours.sum().to_dict(),
            "category_counts":resolved.final_setter_category.value_counts().reindex(ORDER,fill_value=0).to_dict(),
            "category_shares_percent":(resolved.groupby("final_setter_category").represented_hours.sum().reindex(ORDER,fill_value=0)/resolved.represented_hours.sum()*100).to_dict(),
            "original_V2_values_identical":True,"candidate_order_and_duplicate_path_invariant":True,
            "every_candidate_explicitly_mapped":True,"unresolved_zone_hours":0,"VOLL_roots":0,
            "share_sum_max_error_percent":share_error,"VRE_nonzero_zone_hours":len(exceptions),
            "VRE_nonzero_clean_zone_hours":int((exceptions.zero_cost_zero_scarcity_no_ramp).sum()),
            "VRE_bearing_zone_hours":len(context),"VRE_bearing_with_RoR_zone_hours":int(context.includes_run_of_river.sum()),
            "VRE_bearing_zero_price_zone_hours":int(context.approximately_zero_target_price.sum()),
            "source_hashes":source_hashes,"implementation_sha256":sha256_file(Path(__file__)),
            "solver_invocations":guard["solver_invocations"],"network_opens":0,"V2_methodology_modifications":0,
            "canonical_result_modifications":0,"existing_visualization_modifications":0,"routine_activation":False,
            "artifact_hashes":{f.name:sha256_file(f) for f in output.glob(f"*{label}.*") if f.suffix in (".csv",".parquet")},
        }
        (output/f"marginal_setter_final_QA_{label}.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    return resolved,report

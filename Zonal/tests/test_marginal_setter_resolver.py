"""Final family resolution tests using already-valid root sets; never solve."""
import json
import pandas as pd
import pytest
from mem_model.reporting.marginal_setter_resolver import (
    ORDER, RESIDUAL, ResolverError, resolve_root_set, resolve_attribution, diagnostics, vre_candidate_context,
)
from mem_model.reporting.marginal_setter_attribution import ZONES


def root(technology, asset="one", zone="NORD", mechanism=None, path="[]"):
    pairs={
        "Solar":("solar_pv_utility","Generator"),
        "Wind":("wind_onshore","Generator"),
        "BESS":("battery_energy","Link"),"PHS":("water_energy","Link"),
        "Hydro":("water_energy","Link"),"Hydro RoR":("hydro_run_of_river","Generator"),
        "CCGT":("methane_ccgt","Generator"),"Bioenergy":("bioenergy_steam_other_surviving","Generator"),
        "Geothermal":("geothermal","Generator"),"Gas IC":("methane_internal_combustion","Generator"),
        "Gas - OCGT":("methane_gt_ocgt","Generator"),
        "External market opportunity":("external_market","Generator"),
        "P2X":("p2x_flexible_NORD","Generator"),
    }
    carrier,component=pairs[technology]
    return dict(technology=technology,carrier=carrier,component=component,asset_id=asset,
                zone=zone,mechanism=mechanism or "DIRECT_MARGINAL_TECHNOLOGY",coupling_path=path)


@pytest.mark.parametrize("technologies,expected",[
    (["Solar","Wind"],"VRE"),(["Wind","Wind"],"VRE"),(["Solar"],"VRE"),
    (["BESS","BESS"],"BESS"),(["PHS","PHS"],"PHS"),
    (["Hydro","Hydro"],"Reservoir / pondage hydro"),
    (["CCGT","CCGT"],"CCGT"),(["Bioenergy","Geothermal","Gas IC","Gas - OCGT"],"Other thermal"),
    (["External market opportunity","External market opportunity"],"External market"),
    (["Solar","BESS"],RESIDUAL),(["BESS","PHS"],RESIDUAL),
    (["CCGT","Hydro"],RESIDUAL),(["CCGT","External market opportunity"],RESIDUAL),
    (["Hydro RoR"],"VRE"),(["P2X"],"P2X"),
    (["Solar","Wind","Hydro RoR"],"VRE"),
    (["Wind","Hydro RoR","BESS"],RESIDUAL),
    (["Solar","Hydro RoR","PHS"],RESIDUAL),
    (["Hydro","Hydro RoR"],RESIDUAL),(["PHS","Hydro"],RESIDUAL),
])
def test_explicit_family_collapse(technologies,expected):
    result=resolve_root_set([root(t,str(i)) for i,t in enumerate(technologies)],"LOCAL")
    assert result["final_setter_category"]==expected
    assert result["final_root_family_count"]==(len(set(json.loads(result["final_root_families"]))))
    assert (result["final_root_family_count"]>1)==(expected==RESIDUAL)


def test_remote_solar_wind_and_several_same_family_zones():
    result=resolve_root_set([root("Solar","a","SUD"),root("Wind","b","CALA")],"COUPLED")
    assert result["final_setter_category"]=="VRE"
    assert result["resolution_scope"]=="COUPLED"
    assert result["final_root_zone_count"]==2 and result["residual_origin"] is None
    assert resolve_root_set([root("BESS","a","SUD"),root("BESS","b","CALA")],"COUPLED")["final_setter_category"]=="BESS"


def test_direct_and_ramp_ccgt_collapse_without_mechanism_loss():
    roots=[root("CCGT","a"),root("CCGT","b",mechanism="UC_RAMP_CONSTRAINED")]
    assert resolve_root_set(roots,"LOCAL")["final_setter_category"]=="CCGT"
    assert roots[1]["mechanism"]=="UC_RAMP_CONSTRAINED"


def test_path_duplicates_and_candidate_order_do_not_create_multiplicity():
    a=root("BESS",zone="CNOR",path='["first"]')
    b=dict(a,coupling_path='["second","third"]')
    before=resolve_root_set([a,b],"COUPLED")
    assert before==resolve_root_set([b,a,a],"COUPLED")
    assert before["final_setter_category"]=="BESS" and before["final_root_count"]==1


def test_equivalent_assets_remain_audit_roots_not_family_multiplicity():
    result=resolve_root_set([root("Wind","a"),root("Wind","b")],"LOCAL")
    assert result["final_root_count"]==2 and result["final_root_family_count"]==1


def test_residual_origins_preserved():
    roots=[root("BESS","a"),root("PHS","b")]
    assert resolve_root_set(roots,"LOCAL")["residual_origin"]=="LOCAL_MULTIPLE"
    assert resolve_root_set(roots,"COUPLED")["residual_origin"]=="REMOTE_MIXED"


def test_unmapped_valid_technology_fails_closed():
    r=root("CCGT");r["carrier"]="unknown_valid_carrier"
    with pytest.raises(ResolverError,match="UNMAPPED_SETTER_TECHNOLOGY"):
        resolve_root_set([r],"LOCAL")


def test_voll_flagged_separately_not_thermal():
    r=root("CCGT");r.update(technology="Load shedding / VOLL",carrier="load_shedding")
    with pytest.raises(ResolverError,match="LOAD_SHEDDING_SETTER_REVIEW"):
        resolve_root_set([r],"LOCAL")


def test_empty_root_set_is_unresolved_not_residual():
    result=resolve_root_set([],"LOCAL")
    assert result["final_setter_category"] is None
    assert result["final_resolution_rule"]=="UNRESOLVED_NO_VALID_ROOT_SET"
    assert result["residual_origin"] is None


def frames(klass="MARKET_COUPLING",technologies=("Solar","Wind"),remote=True):
    table=pd.DataFrame([dict(snapshot=pd.Timestamp("2019-01-01"),zone="NORD",model_year=2040,scenario="Base",
                             primary_attribution_class=klass,setter_mechanism=klass,
                             represented_hours=1.,zonal_price_EUR_MWh=0.,stationarity_residual_EUR_MWh=0.)])
    audit=pd.DataFrame([dict(root(t,str(i),"CNOR" if remote else "NORD"),
                            snapshot=table.snapshot.iloc[0],target_zone="NORD",remote=remote,
                            cost=0.,lower=0.,upper=0.,gradient=0.,objective_weight=1.,recon=0.,residual=0.)
                        for i,t in enumerate(technologies)])
    return table,audit


def test_complete_coupled_root_set_not_old_headline_mechanism():
    table,audit=frames()
    result,_=resolve_attribution(table,audit)
    assert result.final_setter_category.iloc[0]=="VRE"
    assert result.primary_attribution_class.iloc[0]=="MARKET_COUPLING"
    assert result[table.columns].equals(table)


def test_selected_local_roots_and_mixed_scope_fail_closed():
    table,audit=frames("MULTIPLE_CANDIDATES",remote=False)
    result,_=resolve_attribution(table,audit)
    assert result.final_setter_category.iloc[0]=="VRE" and result.resolution_scope.iloc[0]=="LOCAL"
    other=audit.iloc[[0]].assign(remote=True,asset_id="remote")
    with pytest.raises(ResolverError,match="mixed local/remote"):
        resolve_attribution(table,pd.concat([audit,other]))


def test_vre_zero_price_diagnostic_uses_actual_candidate_evidence():
    table,audit=frames()
    result,mapped=resolve_attribution(table,audit)
    vre,exceptions,residual,recovery=diagnostics(result,mapped)
    assert vre.iloc[0].zero_price_zone_hours==1
    assert vre.iloc[0].VRE_root_set=="ALL"
    assert "Solar+Wind" in set(vre.VRE_root_set)
    assert exceptions.empty and residual.empty


def test_nonzero_vre_price_retains_actual_ramp_reason():
    table,audit=frames("UC_RAMP_CONSTRAINED",("Wind",),False)
    table["zonal_price_EUR_MWh"]=2.
    audit["gradient"]=-2.;audit["recon"]=2.
    result,mapped=resolve_attribution(table,audit)
    vre,exceptions,_,_=diagnostics(result,mapped)
    assert len(exceptions)==1
    assert exceptions.reason.iloc[0]=="RETAINED_RAMP_STATIONARITY_CONTRIBUTION"
    assert vre.iloc[0].zero_price_zone_hours==0


def test_sparse_multi_hour_vre_diagnostic_index_is_unambiguous():
    t,a=frames()
    t2=t.assign(snapshot=pd.Timestamp("2019-02-02"))
    a2=a.assign(snapshot=pd.Timestamp("2019-02-02"))
    table=pd.concat([t,t2],ignore_index=True)
    audit=pd.concat([a,a2],ignore_index=True)
    result,mapped=resolve_attribution(table,audit)
    result.index=pd.MultiIndex.from_arrays([result.snapshot,result.zone],names=["snapshot","zone"])
    # Diagnostic accepts source columns even when a retained index shares names.
    vre,exceptions,_,_=diagnostics(result.reset_index(drop=True),mapped)
    assert vre.iloc[0].zone_hours==2 and exceptions.empty


def test_missing_zone_hour_root_remains_explicitly_unresolved():
    table,audit=frames()
    missing=table.iloc[[0]].assign(zone="SUD",primary_attribution_class="OTHER_INDETERMINATE")
    original=pd.concat([table,missing],ignore_index=True)
    result,_=resolve_attribution(original,audit)
    assert result.final_setter_category.iloc[1] is None
    assert result.final_root_count.iloc[1]==0
    assert result[original.columns].equals(original)


def test_vre_with_ror_resolves_without_losing_underlying_identity():
    table,audit=frames("MULTIPLE_CANDIDATES",("Wind","Solar","Hydro RoR"),False)
    result,mapped=resolve_attribution(table,audit)
    assert result.final_setter_category.iloc[0]=="VRE"
    assert result.primary_attribution_class.iloc[0]=="MULTIPLE_CANDIDATES"
    assert "Hydro RoR" in set(mapped.technology)
    vre,exceptions,_,_=diagnostics(result,mapped)
    assert vre.iloc[0].zone_hours==1
    assert vre.iloc[0].price_min_EUR_MWh==0
    assert "Solar+Wind+RoR" in set(vre.VRE_root_set)
    assert set(ZONES).issubset(set(vre.zone))
    evidence=vre_candidate_context(result,mapped)
    assert len(evidence)==1 and evidence.includes_run_of_river.iloc[0]
    assert evidence.approximately_zero_target_price.iloc[0]


def test_zero_solver_invocations_and_no_network_construction(monkeypatch):
    import pypsa
    from mem_model.reporting.uc2_postprocess import no_solver_calls
    def forbidden(*args,**kwargs):
        raise AssertionError("No network construction permitted in resolver")
    monkeypatch.setattr(pypsa,"Network",forbidden)
    with no_solver_calls() as guard:
        assert resolve_root_set([root("PHS")],"LOCAL")["final_setter_category"]=="PHS"
    assert guard["solver_invocations"]==0


def test_zonal_share_artist_values_remain_aligned_after_weighted_division():
    from mem_model.visualization.marginal_setter_resolved import _zone_shares
    table=pd.DataFrame([dict(zone=zone,final_setter_category=ORDER[i],represented_hours=1.)
                        for i,zone in enumerate(ZONES)])
    shares=_zone_shares(table)
    assert shares.index.tolist()==list(ZONES)
    for i,zone in enumerate(ZONES):assert shares.loc[zone,ORDER[i]]==100.


def test_nonzero_price_does_not_change_vre_family_resolution():
    table,audit=frames("MULTIPLE_CANDIDATES",("Wind","Hydro RoR"),False)
    table["zonal_price_EUR_MWh"]=25.
    result,_=resolve_attribution(table,audit)
    assert result.final_setter_category.iloc[0]=="VRE"


def test_remote_ror_and_duplicate_paths_collapse():
    roots=[root("Wind","wind","SUD"),root("Hydro RoR","ror","CALA")]
    resolved=resolve_root_set(roots,"COUPLED")
    assert resolved["final_setter_category"]=="VRE"
    assert resolved==resolve_root_set(roots+[dict(roots[1],coupling_path='["other"]')],"COUPLED")


def test_final_visible_order_has_no_separate_ror():
    assert ORDER==("VRE","PHS","BESS","Reservoir / pondage hydro","P2X","CCGT","Other thermal","External market","Market coupling / multiple")

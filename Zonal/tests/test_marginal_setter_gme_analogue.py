"""Economic/synthetic-selection regressions over cached roots; never solve."""
import json
import numpy as np
import pandas as pd
import pytest
from mem_model.reporting.marginal_setter_gme_analogue import prepare_offers, select_offers, AnalogueError, withdrawal_proxy, shares

PAIRS={"Wind":("wind_onshore","Generator"),"Hydro RoR":("hydro_run_of_river","Generator"),"BESS":("battery_energy","Link"),"PHS":("water_energy","Link"),"CCGT":("methane_ccgt","Generator"),"P2X":("p2x_flexible_NORD","Generator")}
def fixtures(technologies=("BESS","PHS"),powers=(20.,40.),positions=None,remote=False):
    stamp=pd.Timestamp("2019-01-01")
    t=pd.DataFrame([dict(snapshot=stamp,zone="NORD",zonal_price_EUR_MWh=50.,primary_attribution_class="MARKET_COUPLING" if remote else "MULTIPLE_CANDIDATES",resolution_scope="COUPLED" if remote else "LOCAL")])
    rows=[]
    for i,(tech,power) in enumerate(zip(technologies,powers)):
        carrier,component=PAIRS[tech];position=positions[i] if positions else "discharging" if component=="Link" else "interior"
        rows.append(dict(snapshot=stamp,target_zone="NORD",remote=remote,zone="CNOR" if remote else "NORD",asset_id=str(i),component=component,technology=tech,carrier=carrier,mechanism="P2X_FLEXIBLE_DEMAND" if tech=="P2X" else "STORAGE_HYDRO_OPPORTUNITY_VALUE" if component=="Link" else "DIRECT_MARGINAL_TECHNOLOGY",coupling_path="[]",native_power=power,p=power,operating_lower=0.,operating_upper=100.,efficiency=.9 if component=="Link" else 1.,position=position,cost=0.,opportunity=55.,gradient=0.,lower=0.,upper=0.,recon=50.,residual=0.))
    return t,pd.DataFrame(rows)


def test_unique_valid_root_retains_underlying_technology():
    t,a=fixtures(("Hydro RoR",),(25.,));r=select_offers(prepare_offers(t,a))
    assert r.root_family.iloc[0]=="VRE" and r.technology.iloc[0]=="Hydro RoR"
    assert r.selection_basis.iloc[0]=="UNIQUE_VALID_ROOT"


def test_normalized_interior_acceptance_proxy_not_dispatch_size():
    t,a=fixtures();a.loc[0,"native_power"]=80.;a.loc[0,"operating_upper"]=1000.
    r=select_offers(prepare_offers(t,a));assert r.technology.iloc[0]=="BESS"
    assert r.selection_basis.iloc[0]=="UTILISATION_PROXY"
    assert select_offers(prepare_offers(t,a),utilisation_direction="HIGHEST").technology.iloc[0]=="PHS"


def test_partial_acceptance_outranks_bound_with_same_unknown_priority():
    t,a=fixtures(powers=(100.,40.));r=select_offers(prepare_offers(t,a))
    assert r.technology.iloc[0]=="PHS" and r.selection_basis.iloc[0]=="PARTIAL_ACCEPTANCE_PROXY"


def test_no_technology_priority_is_inferred():
    t,a=fixtures(("Wind","CCGT"),(80.,40.));o=prepare_offers(t,a)
    assert o.gme_priority_rank.isna().all()
    assert select_offers(o).technology.iloc[0]=="CCGT"
    assert select_offers(o).selection_basis.iloc[0]=="UTILISATION_PROXY"


def test_independently_evidenced_later_priority_is_last_accepted_analogue():
    t,a=fixtures();o=prepare_offers(t,a);o["gme_priority_rank"]=[3,2]
    r=select_offers(o);assert r.technology.iloc[0]=="BESS" and r.selection_basis.iloc[0]=="GME_PRIORITY_RESOLUTION"


def test_incomplete_priority_does_not_guess_order():
    t,a=fixtures();o=prepare_offers(t,a);o.loc[0,"gme_priority_rank"]=3
    assert select_offers(o).selection_basis.iloc[0]=="UTILISATION_PROXY"


def test_supply_discharge_preferred_over_charging_root():
    t,a=fixtures(powers=(20.,40.),positions=("charging","discharging"))
    r=select_offers(prepare_offers(t,a));assert r.technology.iloc[0]=="PHS"
    assert r.selection_side.iloc[0]=="ACCEPTED_SUPPLY_PROXY" and r.valid_root_count.iloc[0]==2


def test_supply_preferred_over_p2x_but_p2x_kept_without_supply():
    t,a=fixtures(("P2X","CCGT"),(20.,40.));r=select_offers(prepare_offers(t,a))
    assert r.technology.iloc[0]=="CCGT"
    a.loc[1,"native_power"]=0.;r=select_offers(prepare_offers(t,a))
    assert r.technology.iloc[0]=="P2X" and r.selection_side.iloc[0]=="ACTIVE_DEMAND_EXTENSION"


def test_zero_output_is_explicit_extension_not_accepted_offer():
    t,a=fixtures(("Wind",),(0.,));r=select_offers(prepare_offers(t,a))
    assert r.selection_side.iloc[0]=="ZERO_OUTPUT_KKT_SUPPORT_EXTENSION"
    assert not r.accepted_supply_proxy.iloc[0]


def test_remote_root_technology_and_all_paths_retained_without_count_bias():
    t,a=fixtures(remote=True);before=select_offers(prepare_offers(t,a))
    duplicate=a.iloc[[0]].assign(coupling_path='["second","third"]')
    after=select_offers(prepare_offers(t,pd.concat([duplicate,a.iloc[::-1]],ignore_index=True)))
    assert before.economic_root_id.iloc[0]==after.economic_root_id.iloc[0]
    assert before.valid_root_count.iloc[0]==after.valid_root_count.iloc[0]==2
    assert len(json.loads(after.coupling_paths.iloc[0]))==2


@pytest.mark.parametrize("convention",["HASH_MIN","HASH_MAX","HASH_ALTERNATE"])
def test_exact_tie_is_stable_and_flagged(convention):
    t,a=fixtures(powers=(20.,20.));o=prepare_offers(t,a)
    x=select_offers(o,convention);y=select_offers(o.iloc[::-1],convention)
    assert x.economic_root_id.iloc[0]==y.economic_root_id.iloc[0]
    assert x.selection_basis.iloc[0]=="DETERMINISTIC_EXACT_TIE" and x.exact_tie_count.iloc[0]==2
    assert x.tie_break_used.iloc[0] and x.valid_family_count.iloc[0]==2


def test_neutral_inverse_tie_can_expose_distinct_family_uncertainty():
    t,a=fixtures(powers=(20.,20.));o=prepare_offers(t,a)
    assert select_offers(o,"HASH_MIN").root_family.iloc[0]!=select_offers(o,"HASH_MAX").root_family.iloc[0]


def test_prices_and_kkt_cannot_be_changed():
    t,a=fixtures();a.loc[0,"recon"]=52.
    with pytest.raises(AnalogueError,match="KKT"):prepare_offers(t,a)


def test_unknown_valid_technology_fails_closed():
    t,a=fixtures();a.loc[0,"carrier"]="unknown"
    with pytest.raises(RuntimeError,match="UNMAPPED"):prepare_offers(t,a)


def test_invalid_operating_range_fails_closed():
    t,a=fixtures();a.loc[0,"operating_upper"]=0.
    with pytest.raises(AnalogueError,match="RANGE"):prepare_offers(t,a)


def test_canonical_volume_proxy_excludes_transit_and_reconciles_balance():
    t=pd.DataFrame([dict(snapshot=pd.Timestamp("2019-01-01"),zone="NORD",represented_hours=1.)])
    physical=t[["snapshot","zone"]].assign(**{"Gas - CCGT":80.,"BESS":10.,"rigid_end_use_demand_MW":70.,"p2x_electrical_consumption_MW":10.,"bess_charging_MW":5.,"phs_charging_MW":5.,"net_imports_MW":-2.,"load_shedding_MW":2.})
    annual=pd.DataFrame([dict(zone="NORD",rigid_end_use_demand_TWh=.000070,p2x_electrical_consumption_TWh=.000010,bess_charging_TWh=.000005,phs_charging_TWh=.000005,load_shedding_TWh=.000002)])
    volume,q=withdrawal_proxy(t,physical,pd.Series([1.],index=t.snapshot),annual)
    assert volume.cleared_volume_proxy_MWh.iloc[0]==88.
    assert q["balance_max_abs_residual_MW"]==0.


def test_solver_and_network_construction_forbidden(monkeypatch):
    import pypsa
    from mem_model.reporting.uc2_postprocess import no_solver_calls
    monkeypatch.setattr(pypsa,"Network",lambda *a,**kw:(_ for _ in ()).throw(AssertionError("Network open forbidden")))
    with no_solver_calls() as guard:
        t,a=fixtures();assert len(select_offers(prepare_offers(t,a)))==1
    assert guard["solver_invocations"]==0


def test_comparison_output_cannot_target_protected_evidence():
    from pathlib import Path
    from mem_model.reporting.marginal_setter_gme_analogue import validate_review_output, VERSION
    tmp_path = Path(__file__).resolve().parent/"synthetic_review_root"
    for name in ("V2", "FINAL_RESOLVER_V1", "FINAL_RESOLVER_V2", "V2/Base"):
        with pytest.raises(AnalogueError, match="PROTECTED_OUTPUT_TARGET"):
            validate_review_output(tmp_path, tmp_path/name)
    validate_review_output(tmp_path, tmp_path/VERSION/"method_comparison/Base")


def test_comparison_receipt_has_distinct_a_b_eligibility():
    from mem_model.reporting.marginal_setter_gme_analogue import COMPARISON_RULES
    assert "no supply preference" in COMPARISON_RULES["A"]
    assert "unresolved" in COMPARISON_RULES["B"]


def test_utilisation_gap_diagnostic_does_not_overstate_precision():
    from mem_model.reporting.marginal_setter_gme_analogue import utilisation_gap_diagnostic
    t,a=fixtures(("Wind", "BESS"),(20.,20.+1e-8))
    gaps=utilisation_gap_diagnostic(prepare_offers(t,a),"A")
    assert len(gaps)==1
    assert gaps.within_native_power_noise_scale.iloc[0]
    assert not gaps.within_declared_exact_ratio_tie.iloc[0]


def test_approach_a_retains_demand_and_zero_roots_b_requires_supply():
    t,a=fixtures(("P2X","Wind"),(20.,0.))
    offers=prepare_offers(t,a)
    x=select_offers(offers,approach="A")
    assert x.technology.iloc[0]=="P2X"
    assert x.selection_basis.iloc[0]=="PARTIAL_ACCEPTANCE_PROXY"
    assert select_offers(offers,approach="B").empty


def test_single_family_is_identified_without_synthetic_technology_tie():
    t,a=fixtures(("Wind","Hydro RoR"),(100.,100.))
    r=select_offers(prepare_offers(t,a),approach="A")
    assert r.selection_basis.iloc[0]=="UNIQUE_VALID_FAMILY"
    assert r.root_family.iloc[0]=="VRE" and not r.technology_tie_break_used.iloc[0]
    assert r.tie_break_used.iloc[0]  # representative asset only, not technology


def test_approach_b_excludes_charging_and_zero_kkt_support():
    t,a=fixtures(powers=(20.,40.),positions=("charging","discharging"))
    r=select_offers(prepare_offers(t,a),approach="B")
    assert r.technology.iloc[0]=="PHS" and r.eligible_root_count.iloc[0]==1
    assert r.valid_root_count.iloc[0]==2


def test_family_resolution_after_partial_filter_does_not_force_asset_causality():
    t,a=fixtures(("BESS","BESS","PHS"),(20.,40.,100.))
    r=select_offers(prepare_offers(t,a),approach="A")
    assert r.root_family.iloc[0]=="BESS" and r.selection_basis.iloc[0]=="PARTIAL_ACCEPTANCE_PROXY"


def test_a_no_supply_preference_over_interior_economic_demand_root():
    t,a=fixtures(("P2X","CCGT"),(20.,100.))
    offers=prepare_offers(t,a)
    assert select_offers(offers,approach="A").technology.iloc[0]=="P2X"
    assert select_offers(offers,approach="B").technology.iloc[0]=="CCGT"

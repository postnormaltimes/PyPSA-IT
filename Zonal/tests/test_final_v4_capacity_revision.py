"""Static successor and single-job routing only; optimizer calls are forbidden."""
import json
import math

import pandas as pd
import pypsa
import pytest

from mem_model import final_capacity_revision as v4
from mem_model import final_methodology_networks as final
from mem_model import stage_b_uc2 as uc2
from mem_model.common import ROOT, sha256_file
from mem_model.stage_b_zonal_vre import no_models_or_solves


@pytest.fixture(autouse=True)
def no_optimization(monkeypatch):
    def prohibited(*args, **kwargs):
        pytest.fail("solver/model or production action called")
    monkeypatch.setattr(uc2, "_manual_solve", prohibited)
    monkeypatch.setattr(uc2, "_solver_contract", prohibited)
    with no_models_or_solves():
        yield


@pytest.mark.parametrize("mw,target,expected", [(6250,300,21),(3750,300,13),(825,550,2),(274,550,1)])
def test_native_half_up_per_cohort(mw,target,expected):
    assert v4.unit_count(mw,target)==expected


@pytest.mark.parametrize("scenario,expected", [
    ("Slow",(144000,36800,16000,10000)),
    ("Base",(180000,46000,20000,10000)),
    ("High",(198000,50600,22000,10000)),
])
def test_accepted_capacity_controls(scenario,expected):
    assert tuple(v4.case_contract(scenario).values())==pytest.approx(expected,abs=1e-8)


def test_static_contract_arithmetic_and_unrelated_fields():
    old=pd.read_csv(v4.STATIC)
    new=pd.read_csv(v4.RUNTIME/"2050/generators_static.csv")
    old=old.loc[old.year.eq(2050)].reset_index(drop=True)
    pd.testing.assert_frame_equal(old.drop(columns="p_nom_MW"),new.drop(columns="p_nom_MW"),check_exact=True,check_dtype=False)
    old_text=pd.read_csv(v4.STATIC,dtype=str,keep_default_na=False)
    new_text=pd.read_csv(v4.RUNTIME/"2050/generators_static.csv",dtype=str,keep_default_na=False)
    pd.testing.assert_frame_equal(old_text.loc[old_text.year.eq("2050")].reset_index(drop=True).drop(columns="p_nom_MW"),
                                  new_text.drop(columns="p_nom_MW"),check_exact=True)
    for scenario in ("Slow","Base","High"):
        a=old.loc[old.scenario.eq(scenario)]
        b=new.loc[new.scenario.eq(scenario)]
        assert b.loc[b.parent_capacity_technology.isin(v4.PROGRAM),"p_nom_MW"].sum()==pytest.approx(v4.EXPECTED_OLD_PROGRAM[scenario],abs=1e-6)
        assert a.loc[a.parent_capacity_technology.eq("GAS_CCS"),"p_nom_MW"].sum()-b.loc[b.parent_capacity_technology.eq("GAS_CCS"),"p_nom_MW"].sum()==pytest.approx(2000,abs=1e-7)
        fixed=a.parent_capacity_technology.isin(v4.PROGRAM)&~a.parent_capacity_technology.isin(["GAS_CCS","NUCLEAR"])
        pd.testing.assert_series_equal(a.loc[fixed,"p_nom_MW"],b.loc[fixed,"p_nom_MW"],check_exact=True)


@pytest.fixture(scope="module",params=["Slow","Base","High"])
def successor_pair(request):
    scenario=request.param
    lineage=pd.read_csv(v4.QA/"FINAL_V4_NETWORK_LINEAGE_AND_HASHES.csv")
    case=lineage.loc[lineage.year.eq(2050)&lineage.scenario.eq(scenario)].set_index("network_type")
    with no_models_or_solves():
        original=pypsa.Network(ROOT/case.at["UC_INPUT","parent_path"])
        network=pypsa.Network(ROOT/case.at["UC_INPUT","path"])
        reference=pypsa.Network(ROOT/case.at["CONTINUOUS_REFERENCE","path"])
    plan=pd.read_csv(v4.QA/"FINAL_V4_CHILD_CROSSWALK.csv")
    return scenario,original,network,reference,plan.loc[plan.scenario.eq(scenario)]


def test_full_static_successor_diff_and_capacity_gate(successor_pair):
    scenario,old,new,_,plan=successor_pair
    qa=v4.capacity_qa(old,new,plan,scenario)
    diff=v4.exact_diff(old,new,plan,scenario,"UC_INPUT")
    assert qa["BESS_changed_fields"]==qa["unexpected_changed_fields"]==0
    assert diff and not any(row["classification"]=="UNEXPECTED_CHANGE" for row in diff)
    assert not new.generators.committable[new.generators.carrier.isin(v4.VRE)].any()
    assert new.generators.loc[new.generators.carrier.eq("nuclear")].groupby("bus").p_nom.sum().to_dict()==pytest.approx({"NORD":6250.,"CSUD":3750.},abs=1e-7)
    assert qa["nuclear_units"]==34


def test_reference_consistency_and_no_generation_targets(successor_pair):
    _,old,new,reference,_=successor_pair
    assert not reference.generators.committable.any()
    columns=["bus","carrier","p_nom","efficiency","marginal_cost","sign"]
    pd.testing.assert_frame_equal(new.generators[columns],reference.generators[columns],check_exact=True)
    pd.testing.assert_frame_equal(new.global_constraints,old.global_constraints,check_exact=True)
    pd.testing.assert_frame_equal(new.generators_t.p_max_pu.loc[:,old.generators.index[old.generators.carrier.isin(v4.VRE)]],
                                  old.generators_t.p_max_pu.loc[:,old.generators.index[old.generators.carrier.isin(v4.VRE)]],check_exact=True)


@pytest.mark.parametrize("defect",["BESS","P2X","SOLAR_PROFILE","NUCLEAR_UC","METADATA"])
def test_diff_rejects_unrelated_or_parameter_drift(successor_pair,defect):
    scenario,old,new,_,plan=successor_pair
    changed=new.copy()
    if defect=="BESS":
        name=changed.stores.index[changed.stores.carrier.eq("battery_energy")][0]
        changed.stores.at[name,"e_nom"]+=1
    elif defect=="P2X":
        changed.generators.at["P2X_NORD","p_nom"]+=1
    elif defect=="SOLAR_PROFILE":
        name=changed.generators.index[changed.generators.carrier.eq("solar_pv_rooftop")][0]
        changed.generators_t.p_max_pu.loc[changed.snapshots[0],name]+=.001
    elif defect=="NUCLEAR_UC":
        name=changed.generators.index[changed.generators.carrier.eq("nuclear")][0]
        changed.generators.at[name,"p_min_pu"]+=.01
    else:
        changed.meta["final_v4_unapproved_assumption"]="must_fail"
    with pytest.raises((AssertionError,RuntimeError)):
        v4.exact_diff(old,changed,plan,scenario,"UC_INPUT")


@pytest.mark.parametrize("year,scenario",uc2.SCENARIOS)
def test_twelve_hash_pinned_inputs_and_eighteen_isolated_jobs(year,scenario):
    with uc2.execution_variant("final_v4"):
        paths=[uc2.job_paths(year,scenario,kind) for kind in uc2.RUN_TYPES]
        assert len({p["solved"] for p in paths})==3
        expected="v3" if year==2040 else "v4"
        for kind,path in zip(uc2.RUN_TYPES,paths):
            assert uc2._check_input_hash(year,scenario,kind)==uc2._expected_hash(year,scenario,kind)
            assert path["input"].is_relative_to(ROOT/f"networks/unsolved/final_methodology_{expected}")
            assert path["directory"].is_relative_to(ROOT/f"results/final_methodology_{expected}"/str(year)/scenario)
        assert uc2.case_root(year,scenario)==ROOT/f"results/final_methodology_{expected}"/str(year)/scenario


@pytest.mark.parametrize("scenario",["Slow","Base","High"])
def test_2050_static_preflight_read_only_after_production(scenario):
    with uc2.execution_variant("final_v4"):
        result=uc2.preflight(2050,scenario,persist=False)
    assert result["status"]=="PASS" and result["optimization_model_constructed"] is False
    assert result["manual_authorized"] is True


def test_all_six_manually_executable_without_case_permission_and_solver_contract_exact():
    cfg=final.settings("final_v4")
    assert cfg["solver"]==final.settings("final_v3")["solver"]
    assert cfg["water_values"]==final.settings("final_v3")["water_values"]
    assert cfg["no_automatic_execution"] is True
    assert cfg["all_scenarios_manually_executable"] is True
    assert "manual_authorization" not in cfg and "review_sequence" not in cfg
    with uc2.execution_variant("final_v4"):
        for year,scenario in uc2.SCENARIOS:
            assert f"{year}_{scenario.upper()}" in uc2._governance_gate(year,scenario)


def test_final_v4_still_requires_frozen_controls(monkeypatch):
    def fail_closed():
        raise RuntimeError("FINAL_V4_FROZEN_CONTROL_HASH_FAIL")
    monkeypatch.setattr(v4,"verify_frozen",fail_closed)
    with uc2.execution_variant("final_v4"):
        with pytest.raises(RuntimeError,match="FROZEN_CONTROL_HASH_FAIL"):
            uc2._governance_gate(2050,"Slow")


@pytest.mark.parametrize("scenario",["Slow","Base","High"])
def test_final_v4_single_cli_action_never_dispatches_successor(scenario,monkeypatch,capsys):
    calls=[]
    monkeypatch.setattr(uc2,"verify_uc",lambda y,s:calls.append((y,s,uc2.ACTIVE_VARIANT.get())) or {"status":"PASS"})
    uc2.main(["verify-uc","--year","2050","--scenario",scenario,"--variant","final_v4"])
    assert calls==[(2050,scenario,"final_v4")]
    assert uc2.ACTIVE_VARIANT.get() is None
    assert json.loads(capsys.readouterr().out)=={"status":"PASS"}


def test_runtime_inheritance_has_shared_horizon_members_and_exact_hashes():
    manifest=pd.read_csv(v4.RUNTIME/"FINAL_V4_RUNTIME_MANIFEST.csv")
    inherited=manifest.loc[manifest.authority.eq("BYTE_IDENTICAL_INHERITED_P2X_V2_RUNTIME")]
    assert set(inherited.path.map(lambda p: (ROOT/p).name)) >= {
        "load_hourly.parquet","p2x_contract.csv","hydro_inflow_hourly.parquet",
        "hydro_runtime_parameters.csv","external_prices_hourly.parquet","generator_availability_hourly.parquet"}
    assert inherited.scenario.eq("ALL").all()
    assert all(sha256_file(ROOT/row.path)==row.sha256 for row in manifest.itertuples())


def test_final_v4_reporting_protected_sources_route_by_variant(monkeypatch):
    from mem_model.reporting import uc2_postprocess as post
    monkeypatch.setattr(post,"sha256_file",lambda path:"ROUTING_FIXTURE_ONLY")
    with uc2.execution_variant("final_v4"):
        sources=post._protected_sources(2050,"Base")
    assert sources
    for path in sources:
        assert "final_methodology_v3/2050" not in str(path).replace("\\","/")
    assert any("final_methodology_v4" in str(path) for path in sources)


@pytest.mark.parametrize("year,expected",[(2040,"final_v3"),(2050,"final_v4")])
@pytest.mark.parametrize("spelling",["separate","equals"])
def test_cli_routes_inherited_2040_truthfully_without_next_action(year,expected,spelling,monkeypatch,capsys):
    calls=[]
    monkeypatch.setattr(uc2,"verify_reference",lambda y,s:calls.append((y,s,uc2.ACTIVE_VARIANT.get())) or {"status":"PASS"})
    flags=["--variant","final_v4"] if spelling=="separate" else ["--variant=final_v4"]
    uc2.main(["verify-reference","--year",str(year),"--scenario","Base",*flags])
    assert calls==[(year,"Base",expected)]
    assert uc2.ACTIVE_VARIANT.get() is None
    assert json.loads(capsys.readouterr().out)=={"status":"PASS"}


@pytest.mark.parametrize("kind",uc2.RUN_TYPES)
def test_same_case_hash_dependency_intact(kind,monkeypatch):
    monkeypatch.setattr(uc2,"_read_json",lambda p:{"status":"PASS","year":2050,"scenario":"Base","solved_sha256":"wrong"})
    monkeypatch.setattr(uc2,"_load_solved",lambda *a:(None,{"solved_sha256":"right"}))
    with uc2.execution_variant("final_v4"):
        with pytest.raises(RuntimeError,match="DEPENDENCY_FAIL"):
            uc2._verified_previous(2050,"Base",kind)


def test_protected_v3_artifacts_and_canonical_result_inheritance():
    table=pd.read_csv(v4.QA/"FINAL_V3_PROTECTED_HASHES.csv")
    assert all(sha256_file(ROOT/row.path)==row.sha256 for row in table.itertuples())
    results=pd.read_csv(v4.QA/"FINAL_V4_INHERITED_BASE_RESULTS.csv")
    assert len(results)==6
    assert set(results.loc[results.year.eq(2040),"role"])=={"CANONICAL_2040_BASE_RESULT_USER_ACCEPTED"}
    assert set(results.loc[results.year.eq(2050),"role"])=={"PNIEC_CAPACITY_BENCHMARK"}


def test_default_older_variants_and_no_default_solve():
    for variant in ("final_v1","final_v2","final_v3"):
        with uc2.execution_variant(variant):
            assert uc2.config()["result_root"]==f"results/final_methodology_{variant[-2:]}"
    with pytest.raises(SystemExit):
        uc2.main(["--variant","final_v4"])

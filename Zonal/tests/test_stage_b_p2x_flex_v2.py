"""Focused non-solving V2C contracts and exported sparse-successor integrity."""
import ast
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa
import pytest

from mem_model import stage_b_p2x_flex_v2 as v2
from mem_model.common import ROOT, ZONES, SCENARIOS, sha256_file


@pytest.fixture(autouse=True)
def prohibit_models_and_solvers():
    with v2.no_models_or_solves():
        yield


def test_canonical_key_exact_ratios():
    key = v2.key_table()
    v2.verify_key(key)
    assert key.zone.to_list() == list(ZONES)
    assert key.share_numerator.sum() == 74
    assert key.share_denominator.eq(74).all()
    assert np.array_equal(key.p2x_spatial_share, np.asarray(v2.NUMERATORS) / 74)


@pytest.mark.parametrize("mutation", ["share", "zone", "denominator", "status"])
def test_key_rejects_drift(mutation):
    key = v2.key_table()
    field = {"share": "p2x_spatial_share", "zone": "zone", "denominator": "share_denominator", "status": "spatial_status"}[mutation]
    key.at[0, field] = {"share": .2, "zone": "SUD", "denominator": 75, "status": "CANDIDATE"}[mutation]
    with pytest.raises(AssertionError):
        v2.verify_key(key)


def test_pniec_slow_is_corroboration_not_mem_slow_allocator():
    c = v2.slow_corroboration()
    assert c.PNIEC_Slow_GW_displayed.sum() == pytest.approx(5.2)
    assert c.PNIEC_Slow_normalized_displayed_share.sum() == pytest.approx(1)
    assert c.official_national_headline_GW.eq(5.3).all()
    assert c.difference_pp.abs().max() == pytest.approx(.987525987525993)
    assert c.iloc[c.difference_pp.abs().argmax()].zone == "SUD"
    assert not np.array_equal(c.PNIEC_Slow_normalized_displayed_share, v2.key_table().p2x_spatial_share)


@pytest.mark.parametrize("year,scenario", SCENARIOS)
def test_contract_arithmetic_and_common_key(year, scenario):
    old = v2.csv(ROOT / f"runtime_inputs/p2x_flex_v1/{year}/p2x_contract.csv")
    contract = v2.contract_for_case(year, scenario, old, v2.key_table())
    rigid = old.loc[old.scenario == scenario].set_index("zone").loc[list(ZONES), "annual_rigid_MWh"]
    assert np.array_equal(contract.annual_rigid_MWh, rigid)
    assert np.array_equal(contract.p2x_spatial_share, v2.key_table().p2x_spatial_share)
    assert np.array_equal(contract.annual_total_MWh, contract.annual_rigid_MWh + contract.annual_p2x_MWh)
    assert np.allclose(contract.annual_p2x_MWh / contract.p2x_power_MW, 27.5e6 / 7400, rtol=0, atol=1e-10)
    assert not np.allclose(contract.annual_total_MWh, contract.annual_total_MWh.sum() * contract.rigid_demand_share)
    assert "frozen_2025_share" not in contract


@pytest.mark.parametrize("year,scenario", [(2030, "Base"), (2040, "All"), (2040, "base")])
def test_unknown_case_is_not_a_fallback(year, scenario):
    with pytest.raises(ValueError):
        v2.contract_for_case(year, scenario, pd.DataFrame(), v2.key_table())


def toy_parent():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2019-01-01", periods=8760, freq="h"))
    for z in ZONES:
        n.add("Bus", z)
        n.add("Carrier", f"p2x_{z}")
        n.add("Generator", f"P2X_{z}", bus=z, carrier=f"p2x_{z}", sign=-1, p_nom=100,
              marginal_cost=0, p_min_pu=0, p_max_pu=1)
        n.add("GlobalConstraint", f"P2X_ANNUAL_{z}", type="operational_limit", sense="==",
              carrier_attribute=f"p2x_{z}", constant=1e5)
    n.add("Generator", "OTHER", bus="NORD", p_nom=10, p_max_pu=.8)
    n.add("Load", "RIGID", bus="NORD", p_set=5)
    n.meta = {"original": "immutable"}
    return n


def toy_child(n):
    child = n.copy()
    child.generators.loc[[f"P2X_{z}" for z in ZONES], "p_nom"] = 200
    child.global_constraints.loc[[f"P2X_ANNUAL_{z}" for z in ZONES], "constant"] = 2e5
    child.meta[v2.META_KEY] = {"execution_enabled": False}
    return child


def test_exact_diff_accepts_only_authorized_cells():
    n = toy_parent()
    diff = v2.exact_diff(n, toy_child(n))
    assert diff["unexpected_changed_model_fields"] == 0
    assert diff["static_component_tables_exact_except_whitelist"] == len(n.components)


@pytest.mark.parametrize("mutation", ["rigid", "non_p2x", "p2x_sign", "constraint_carrier",
                                      "weights", "time_series", "old_metadata", "other_metadata",
                                      "link", "store", "extendability"])
def test_exact_diff_fails_closed_on_unrelated_drift(mutation):
    n = toy_parent()
    child = toy_child(n)
    if mutation == "rigid":
        child.loads.at["RIGID", "p_set"] = 6
    elif mutation == "non_p2x":
        child.generators.at["OTHER", "p_nom"] = 11
    elif mutation == "p2x_sign":
        child.generators.at["P2X_NORD", "sign"] = 1
    elif mutation == "constraint_carrier":
        child.global_constraints.at["P2X_ANNUAL_NORD", "carrier_attribute"] = "wrong"
    elif mutation == "weights":
        child.snapshot_weightings.iloc[0, 1] = 2
    elif mutation == "time_series":
        child.generators_t.p_max_pu["OTHER"] = .7
    elif mutation == "old_metadata":
        child.meta["original"] = "changed"
    elif mutation == "other_metadata":
        child.meta["unlisted"] = True
    elif mutation == "link":
        child.add("Link", "EXTRA", bus0="NORD", bus1="SUD", p_nom=100)
    elif mutation == "store":
        child.add("Store", "EXTRA", bus="NORD", e_nom=100)
    else:
        child.generators.at["P2X_NORD", "p_nom_extendable"] = True
    with pytest.raises(RuntimeError, match="UNEXPECTED_SUCCESSOR_DRIFT"):
        v2.exact_diff(n, child)


def test_final_variant_is_locked_and_not_integrated():
    v2.verify_config_lock()


def test_preparation_has_no_production_or_model_call():
    source = Path(v2.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"optimize", "create_model", "solve_model", "solve", "Model", "build_network",
                 "continuous_counterfactual", "run_reference", "run_uc", "run_price", "report", "final_qa"}
    for call in [node for node in ast.walk(tree) if isinstance(node, ast.Call)]:
        name = getattr(call.func, "id", getattr(call.func, "attr", ""))
        assert name not in forbidden
    assert "choices=[\"prepare\", \"verify\"]" in source


@pytest.fixture(scope="module")
def prepared_receipt():
    return v2.read_json(v2.QA / v2.RECEIPT)


@pytest.mark.parametrize("year", [2040, 2050])
def test_exported_runtime_inherits_bytes(year, prepared_receipt):
    target = v2.RUNTIME / str(year)
    manifest = v2.csv(target / v2.MANIFEST)
    assert len(manifest) == 6
    assert sha256_file(target / v2.MANIFEST) == prepared_receipt["runtime_manifests"][str(year)]["sha256"]
    for member in v2.COPIED_MEMBERS:
        assert sha256_file(target / member) == sha256_file(ROOT / f"runtime_inputs/p2x_flex_v1/{year}" / member)


def test_exported_contract_national_reconciliation():
    contract = pd.concat([v2.csv(v2.RUNTIME / str(y) / "p2x_contract.csv") for y in (2040, 2050)], ignore_index=True)
    assert len(contract) == 42
    assert v2.numerical_qa(contract).status.eq("PASS").all()
    v2.verify_key(v2.csv(v2.KEY))


@pytest.mark.parametrize("case_index", range(6))
def test_all_exported_successors_and_uc_reference_pair(case_index, prepared_receipt):
    year, scenario = SCENARIOS[case_index]
    records = [r for r in prepared_receipt["networks"] if (r["year"], r["scenario"]) == (year, scenario)]
    assert len(records) == 3
    c = v2.csv(v2.RUNTIME / str(year) / "p2x_contract.csv")
    c = c.loc[c.scenario == scenario]
    new_pair = []
    for r in records:
        final = r["network_type"] != "P2X_FLEX"
        parent_path = ROOT / r["final_v1_parent_path" if final else "p2x_flex_v1_parent_path"]
        path = ROOT / r["final_v2_path" if final else "p2x_flex_v2_path"]
        assert sha256_file(parent_path) == r["final_v1_parent_sha256" if final else "p2x_flex_v1_parent_sha256"]
        assert sha256_file(path) == r["final_v2_sha256" if final else "p2x_flex_v2_sha256"]
        a, b = pypsa.Network(parent_path), pypsa.Network(path)
        assert v2.exact_diff(a, b)["unexpected_changed_model_fields"] == 0
        v2.verify_network_values(b, c)
        if final:
            new_pair.append(b)
    for z in ZONES:
        assert new_pair[0].generators.at[f"P2X_{z}", "p_nom"] == new_pair[1].generators.at[f"P2X_{z}", "p_nom"]
        assert new_pair[0].global_constraints.at[f"P2X_ANNUAL_{z}", "constant"] == new_pair[1].global_constraints.at[f"P2X_ANNUAL_{z}", "constant"]


def test_protected_v1_hashes(prepared_receipt):
    assert v2.verify_protected(prepared_receipt["protected_groups"]) == prepared_receipt["protected_groups"]


def test_preparation_lineage_counts_and_locks(prepared_receipt):
    records = prepared_receipt["networks"]
    assert len(records) == 18
    assert pd.Series([r["network_type"] for r in records]).value_counts().to_dict() == {
        "P2X_FLEX": 6, "UC_INPUT": 6, "CONTINUOUS_REFERENCE": 6}
    assert prepared_receipt["final_v2_execution_enabled"] is False
    assert prepared_receipt["optimization_model_constructed"] is False
    assert prepared_receipt["optimizer_invocations"] == 0
    assert prepared_receipt["allowed_metadata_fields"] == [v2.META_KEY]


def test_second_preparation_cannot_overwrite_existing_artifacts(monkeypatch):
    monkeypatch.setattr(v2, "verify_protected", lambda: {})
    monkeypatch.setattr(v2, "parent_records", lambda: [])
    with pytest.raises(RuntimeError, match="NO_OVERWRITE"):
        v2.prepare()

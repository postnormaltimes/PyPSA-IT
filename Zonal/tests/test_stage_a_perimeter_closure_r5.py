from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from mem_model.stage_a.network import MARKETS
from mem_model.stage_a.perimeter_closure_r5 import (
    EXPORTED_MARKETS,
    RESIDUAL_ENERGY_SHARE,
    R5_GENERATOR_IDS,
    _exported_market_diagnostics,
    _ordinary_hour_mask,
    _ordinary_hour_stability,
    _reachable,
    build_parser,
    load_r5_config,
    prepare_r5,
    run_b9d,
    verify_r5_inputs,
)
from mem_model.stage_a.receipts import sha256_file


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def r5_config() -> dict:
    return load_r5_config()


@pytest.fixture(scope="module")
def verified(r5_config: dict) -> dict:
    return verify_r5_inputs(r5_config)


@pytest.fixture(scope="module")
def prepared() -> dict:
    return prepare_r5(write_artifacts=False)


def test_r5_scope_and_authority_are_exact(r5_config: dict) -> None:
    sizing = r5_config["capacity_sizing"]
    assert r5_config["method_authority"]["id"] == "PERIMETER_CLOSURE_METHOD_V2_0"
    assert float(sizing["residual_energy_share"]) == RESIDUAL_ENERGY_SHARE == 0.05
    assert sizing["rule"] == "BASELINE_SHEDDING_ENERGY_EXCEEDANCE"
    assert sizing["source"] == "IMMUTABLE_ETX7B9_B_BOUNDED_BASELINE_SHEDDING_SERIES"
    assert sizing["candidate_markets"] == ["FR", "GR", "TN"]
    assert sizing["expected_values_are_authority"] is False
    assert sizing["reserve_margin"] is False
    assert sizing["rounding"] is False
    assert sizing["headroom_adder"] is False
    assert sizing["automatic_capacity_iteration"] is False
    assert r5_config["diagnostics"]["exported_markets"] == list(EXPORTED_MARKETS)


def test_exact_r5_capacities_are_derived_from_immutable_b9b(prepared: dict) -> None:
    expected = {
        "FR": 40616.26187526855,
        "GR": 8399.180086754306,
        "TN": 2896.562919647665,
    }
    contract = prepared["contract"].set_index("bus")
    shedding = prepared["baseline_shedding"]
    assert set(contract.index) == set(expected)
    for market, capacity in expected.items():
        observed = float(contract.at[market, "p_nom_MW"])
        assert observed == pytest.approx(capacity, abs=5e-9)
        series = shedding[market].clip(lower=0.0).to_numpy(dtype=float)
        residual = np.maximum(series - observed, 0.0).sum()
        assert residual / series.sum() == pytest.approx(0.05, abs=1e-12)
        positive_average = series[series > 0.0].mean()
        assert observed != pytest.approx(float(series.max()))
        assert observed != pytest.approx(float(positive_average))
    assert float(contract["p_nom_MW"].sum()) == pytest.approx(
        51912.004881670524, abs=1e-8
    )


def test_r5_cost_and_structural_delta_are_exact(prepared: dict) -> None:
    contract = prepared["contract"]
    assert set(contract["generator_id"]) == set(R5_GENERATOR_IDS.values())
    assert set(contract["bus"]) == {"FR", "GR", "TN"}
    assert set(contract["bus"]).isdisjoint({"IT", "AT", "CH", "SI", "HR", "ME", "MT"})
    assert contract["marginal_cost_EUR2025_per_MWh_el"].nunique() == 1
    assert float(contract["marginal_cost_EUR2025_per_MWh_el"].iloc[0]) == pytest.approx(
        243.122262790698, abs=1e-12
    )
    assert prepared["cost"]["source_asset_id"] == "ETX7B2_IT_2050_METHANE_GT_OCGT_NON_CHP_0AA7D5D9AF"
    structural = prepared["structural"]
    assert structural["status"] == "PASS"
    assert structural["R5_counts"]["carriers"] == structural["baseline_counts"]["carriers"] + 1
    assert structural["R5_counts"]["generators"] == structural["baseline_counts"]["generators"] + 3
    assert structural["baseline_component_preservation"] == "EXACT"
    assert prepared["LP"]["integer_variables"] == 0
    assert prepared["LP"]["binary_variables"] == 0


def test_accepted_2040_and_2050_predecessors_are_immutable(verified: dict) -> None:
    assert verified["status"] == "PASS"
    b8d = verified["accepted"]["b8d_2040_production_source"]
    b9c = verified["accepted"]["b9c_2050_r10_diagnostic"]
    assert b8d["receipt_sha256"] == "C69ACBF941261BD37426EDCEDB1C14482977C2059427FEA1968008492E05F8C0"
    assert b8d["manifest"]["sha256"] == "88C259493E5D5B8E4B4B2B3C535BCDB623924474FB23DD860D2B8673FE08DA2D"
    assert b9c["receipt_sha256"] == "1D741BD14C15E19443C04CABBC9E1A151771D0A84EEAE225F75B8A1247E758D1"
    assert b9c["manifest"]["sha256"] == "040272788BBC3CA4F880B50A58B6181CB970CA1F5C4B486D366B98D373922A4F"
    assert verified["b9b"]["receipt_sha256"] == "0A8E80FD1A46F5F63E4E8D41C0C3DAEE05F70A529ADC7EE69842FFE434FC9D36"
    assert all(item["member_failures"] == 0 for item in (
        b8d["manifest"], b9c["manifest"], verified["b9b"]["manifest"]
    ))


def test_existing_sensitivity_table_is_unchanged_and_no_new_sensitivity_exists() -> None:
    path = ROOT / "qa/stage_a/perimeter_closure/MEM_Perimeter_Closure_Residual_Energy_Sensitivity_v1.0.csv"
    assert sha256_file(path) == "B7398DAF8BD41006BB4AF97481913221BF2F85BAF43D6C0AC598599F6703D642"
    assert not list((ROOT / "qa/stage_a/etx7b9d").glob("*Sensitivity*"))


def _synthetic_cases() -> dict[str, dict]:
    snapshots = pd.date_range("2019-01-01", periods=4, freq="h")
    base_prices = pd.DataFrame(100.0, index=snapshots, columns=MARKETS)
    base_shedding = pd.DataFrame(0.0, index=snapshots, columns=MARKETS)
    base_prices.loc[snapshots[1], "FR"] = 15000.0
    base_prices.loc[snapshots[1], "CH"] = 600.0
    base_shedding.loc[snapshots[1], "FR"] = 2.0
    base_prices.loc[snapshots[2], "IT"] = 600.0
    cases: dict[str, dict] = {}
    for name, delta in (("B9B", 0.0), ("R10", 1.0), ("R5", 2.0)):
        prices = base_prices.copy()
        prices.loc[snapshots[[0, 3]], list(EXPORTED_MARKETS)] += delta
        cases[name] = {
            "prices": prices,
            "shedding": base_shedding.copy(),
            "weights": pd.Series(1.0, index=snapshots),
        }
    return cases


def test_exported_market_counts_and_unique_timestamp_counts_are_distinct() -> None:
    cases = _synthetic_cases()
    market, aggregate, unique = _exported_market_diagnostics(cases, 15000.0)
    assert len(market) == 3 * 8
    assert set(market["market"]) == set(EXPORTED_MARKETS)
    b9b_aggregate = aggregate.set_index("case").loc["B9B"]
    b9b_unique = unique.set_index("case").loc["B9B"]
    assert b9b_aggregate["total_VOLL_market_hours"] == 1
    assert b9b_aggregate["total_market_hours_above_500_EUR_per_MWh"] == 2
    assert b9b_unique["unique_hours_any_exported_market_at_VOLL"] == 1
    assert b9b_unique["unique_hours_any_exported_market_above_500_EUR_per_MWh"] == 1


def test_ordinary_mask_is_frozen_from_b9b_only_and_reused() -> None:
    cases = _synthetic_cases()
    mask, table, metadata = _ordinary_hour_mask(
        cases["B9B"]["shedding"], cases["B9B"]["prices"]
    )
    assert mask.tolist() == [True, False, False, True]
    assert table["ordinary_hour"].tolist() == mask.tolist()
    assert metadata["source"] == "IMMUTABLE_ETX7B9_B_2050_BOUNDED_BASELINE_ONLY"
    assert metadata["reused_unchanged_for_B9B_R10_R5"] is True
    assert metadata["outcome_dependent_redefinition"] is False
    stability = _ordinary_hour_stability(cases, mask)
    assert len(stability) == 8 * 3
    assert set(stability["mask_source"]) == {"IMMUTABLE_B9B_SHARED_UNCHANGED"}
    for case, expected in (("B9B", 0.0), ("R10", 1.0), ("R5", 2.0)):
        observed = stability.loc[
            (stability["market"] == "FR") & (stability["case"] == case),
            "mean_absolute_change_vs_B9B_EUR_per_MWh",
        ].item()
        assert observed == expected


def test_directional_residual_path_helper_is_not_annual_capacity_logic() -> None:
    adjacency = {"FR": {"CH"}, "CH": {"AT"}, "AT": set(), "GR": set()}
    assert _reachable(adjacency, "FR", "AT") is True
    assert _reachable(adjacency, "GR", "AT") is False
    assert _reachable(adjacency, "AT", "AT") is True


def test_historical_R5_command_is_guarded_and_result_is_immutable(r5_config: dict) -> None:
    parser = build_parser()
    args = parser.parse_args(["b9d"])
    assert args.execute is False
    with pytest.raises(SystemExit, match="R5_DIAGNOSTIC_NOT_EXECUTED"):
        run_b9d(args)
    assert r5_config["phase"]["command"] == (
        r".\.venv\Scripts\python.exe -m mem_model.stage_a.perimeter_closure_r5 b9d --execute"
    )
    assert (ROOT / r5_config["phase"]["result_directory"]).exists()
    assert (ROOT / r5_config["phase"]["receipt"]).exists()
    assert sha256_file(ROOT / r5_config["phase"]["receipt"]) == (
        "E032C63879BF294C99759C8097A7AA7BADB9F5477CA20E8992B07012C266B2E4"
    )


def test_preparation_artifacts_pass_without_production_execution() -> None:
    qa = json.loads(
        (ROOT / "qa/stage_a/etx7b9d/MEM_R5_2050_Preparation_QA_v1.0.json").read_text(
            encoding="utf-8"
        )
    )
    final = json.loads(
        (ROOT / "qa/stage_a/etx7b9d/MEM_R5_2050_Preparation_Final_Verification_v1.0.json").read_text(
            encoding="utf-8"
        )
    )
    manifest_path = ROOT / final["preparation_manifest"]
    manifest = pd.read_csv(manifest_path)
    assert qa["status"] == final["status"] == "PASS"
    assert qa["production_optimization_executed"] is False
    assert qa["production_price_acceptance"] == "PENDING_SOL_REVIEW"
    assert qa["B10_authorized"] is False
    assert qa["Stage_B_authorized"] is False
    assert final["preparation_manifest_sha256"] == sha256_file(manifest_path)
    assert final["preparation_manifest_members"] == len(manifest)
    assert all(sha256_file(ROOT / row.relative_path) == row.sha256 for row in manifest.itertuples())


def test_2040_is_accepted_while_2050_b10_and_stage_b_remain_locked() -> None:
    sources = yaml.safe_load(
        (ROOT / "config/stage_a_production_price_sources.yaml").read_text(encoding="utf-8")
    )
    gates = yaml.safe_load((ROOT / "config/approval_gates.yaml").read_text(encoding="utf-8"))
    assert sources["sources"][2040]["status"] == "ACCEPTED_PRODUCTION_PRICE_SOURCE"
    assert sources["sources"][2050]["status"] == "SOL_ACCEPTED_PENDING_PARENT_HASH_INTEGRATION"
    assert sources["sources"][2050]["phase"] == "ETX-7B9H"
    assert sources["governance"]["production_sources_resolved"] is False
    assert sources["governance"]["b10_authorized"] is False
    assert sources["governance"]["stage_b_authorized"] is False
    manual = gates["stage_a_manual_gates"]
    assert manual["b9d_authorized"] is False
    assert manual["b9e_authorized"] is False
    assert manual["b9f_authorized"] is False
    assert manual["b9g_authorized"] is False
    assert manual["b8d_authorized"] is False
    assert manual["b9c_authorized"] is False
    assert manual["b10_authorized"] is False
    assert manual["stage_b_authorized"] is False

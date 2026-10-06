from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from mem_model.stage_a.network import load_execution_config, verify_input_locks
from mem_model.stage_a.receipts import sha256_file


ROOT = Path(__file__).resolve().parents[1]
B6_RECEIPT = ROOT / "qa/stage_a/etx7b6/MEM_ETX7B6_Run_Receipt_v1.0.json"
B7_RECEIPT = ROOT / "qa/stage_a/etx7b7/MEM_ETX7B7_Run_Receipt_v1.0.json"
B7_DIAGNOSTIC = ROOT / "qa/stage_a/etx7b7/MEM_ETX7B7_2040_Base_Smoke_Diagnostic_v1.0.json"
B7_MARKETS = ROOT / "qa/stage_a/etx7b7/MEM_ETX7B7_2040_Base_Smoke_Market_Diagnostic_v1.0.csv"


def test_accepted_b6_and_b7_receipts_are_unchanged() -> None:
    assert sha256_file(B6_RECEIPT) == "FF6572D7A975E3AD1C37E51599EBE9CC0786424E788A741C94A7D44840DA31A7"
    assert sha256_file(B7_RECEIPT) == "E7C731344FEF2DC905EDBA216F56D7639956FF6DD1D7A5650BDDD6E1E29EBA70"
    receipt = json.loads(B7_RECEIPT.read_text(encoding="utf-8"))
    assert receipt["status"] == "PASS"
    assert receipt["gate"] == "ETX7B7_2040_BASE_SMOKE_COMPLETE"
    assert receipt["snapshots"] == 168
    assert receipt["qa"]["post_solve"]["accepted"] is True


def test_b1_through_b5_hashes_and_members_remain_unchanged() -> None:
    rows = verify_input_locks(load_execution_config())
    assert len(rows) == 7
    assert sum(row["member_count"] for row in rows) == 43
    assert sum(row["member_failures"] for row in rows) == 0
    assert {row["status"] for row in rows} == {"PASS"}


def test_b8_b9_history_is_complete_and_2050_production_remains_locked() -> None:
    gates = yaml.safe_load((ROOT / "config/approval_gates.yaml").read_text(encoding="utf-8"))
    full = gates["full_year_solver"]
    manual = gates["stage_a_manual_gates"]
    auxiliary = gates["auxiliary_experiments"]["etx7b8c_s2"]
    assert full["status"] == "FINAL_RESIDUAL_ME_MT_TN_2050_ISOLATED_FULL_YEAR_DIAGNOSTIC_PREPARED"
    assert full["etx7b8_execution_authorized"] is False
    assert full["authorized_gate"] is None
    assert full["authorized_horizon"] is None
    assert full["required_predecessor_gate"] is None
    assert full["required_predecessor_receipt"] is None
    assert full["execution_enabled"] is False
    assert full["unlock_token"] is None
    assert full["successor_gates_authorized"] is False
    assert full["r10_experiment_execution_enabled"] is False
    assert full["r5_diagnostic_execution_enabled"] is False
    assert full["placement_diagnostic_execution_enabled"] is False
    assert full["residual_me_mt_tn_diagnostic_execution_enabled"] is False
    assert full["authorized_experiment_commands"] == []
    assert full["production_source_promotion_automatic"] is False
    assert manual["current_gate"] == "ETX7B9H_2050_FINAL_RESIDUAL_MT_TN_CLOSURE_COMPLETE"
    assert manual["b8_status"] == "PASS"
    assert manual["b9a_status"] == "PASS"
    assert manual["b9b_status"] == "PASS"
    assert manual["authorized_next_gate"] == "NONE_PENDING_STAGE_B_2050_SOL_USER_REVIEW"
    assert manual["b9_authorized"] is False
    assert manual["b8d_authorized"] is False
    assert manual["b9c_authorized"] is False
    assert manual["b9d_authorized"] is False
    assert manual["b9e_authorized"] is False
    assert manual["b9f_authorized"] is False
    assert manual["b9g_authorized"] is False
    assert manual["b10_authorized"] is False
    assert manual["stage_b_authorized"] is False
    assert manual["b10_2050_status"] == "ETX7B10_2050_STAGE_A_TO_STAGE_B_PRICE_TRANSFER_COMPLETE"
    assert manual["stage_b_2050_preparation_status"] == "STAGE_B_2050_PREPARATION_PASS"
    assert manual["stage_b_2050_authorized"] is False
    assert auxiliary["status"] == "PASS_IMMUTABLE"
    assert auxiliary["formal_gate"] is False
    assert auxiliary["methodological_role"] == "ACCEPTED_DIAGNOSTIC_MAX_CLOSURE_EXPERIMENT"
    assert auxiliary["production_price_source"] is False


def test_existing_result_diagnostic_reconciles_without_rerun() -> None:
    diagnostic = json.loads(B7_DIAGNOSTIC.read_text(encoding="utf-8"))
    markets = pd.read_csv(B7_MARKETS)
    assert diagnostic["status"] == "PASS"
    assert diagnostic["optimization_rerun"] is False
    assert diagnostic["source_receipt_sha256"] == sha256_file(B7_RECEIPT)
    assert diagnostic["window"]["snapshots"] == 168
    assert diagnostic["window"]["contiguous_hourly"] is True
    assert diagnostic["system"]["total_shedding_MWh"] == pytest.approx(2740789.8880135813)
    assert diagnostic["system"]["total_load_MWh"] == pytest.approx(34417842.38862396)
    assert diagnostic["system"]["system_hours_with_any_shedding"] == 140
    assert diagnostic["system"]["storage_state_count"] == 33
    assert diagnostic["system"]["water_state_bus_count"] == 18
    assert diagnostic["system"]["SMOKE_STATES_CYCLIC_OVER_168H"] is True
    assert len(markets) == 10
    assert set(markets["market"]) == {"AT", "CH", "FR", "GR", "HR", "IT", "ME", "MT", "SI", "TN"}
    assert markets["shedding_MWh"].sum() == pytest.approx(diagnostic["system"]["total_shedding_MWh"])
    assert markets["price_max_EUR_per_MWh"].max() == 15000.0

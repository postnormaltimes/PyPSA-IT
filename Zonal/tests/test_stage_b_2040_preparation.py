from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from mem_model.stage_a.execution import (
    _load_accepted_production_price_sources,
    build_parser as build_stage_a_parser,
)
from mem_model.stage_b_2040_preparation import (
    EXPECTED_B8D_HASHES,
    assess_runtime_readiness,
    prepare,
    reconcile_2040_static_scenarios,
    verify_2040_price_source,
)


ROOT = Path(__file__).resolve().parents[1]


def test_2040_B8D_source_resolves_independently_and_hashes_match() -> None:
    source = verify_2040_price_source()
    assert source["status"] == "PASS"
    assert source["source_phase"] == "ETX-7B8D"
    assert source["hashes"] == EXPECTED_B8D_HASHES
    assert source["chronology_unique_hours"] == 8760
    assert source["chronology_contiguous_hourly"] is True
    assert source["required_market_coverage_exact"] is True
    assert source["2050_source_read_or_resolved"] is False


def test_2040_adapter_has_three_scenarios_eight_markets_and_no_2050() -> None:
    adapter = pd.read_parquet(ROOT / "stage_a_results/price_transfer/2040/external_prices_hourly.parquet")
    assert len(adapter) == 3 * 8760 * 8
    assert set(adapter["year"].astype(int)) == {2040}
    assert set(adapter["scenario"]) == {"Slow", "Base", "High"}
    assert set(adapter["external_market"]) == {"FR", "CH", "AT", "SI", "ME", "GR", "MT", "TN"}
    assert "CORS" not in set(adapter["external_market"])


def test_legacy_two_horizon_B10_remains_fail_closed() -> None:
    with pytest.raises(RuntimeError, match="PENDING_TWO_ACCEPTED"):
        _load_accepted_production_price_sources()


def test_2040_slow_base_high_static_contracts_reconcile() -> None:
    result = reconcile_2040_static_scenarios()
    assert len(result) == 3
    assert set(result["scenario"]) == {"Slow", "Base", "High"}
    assert result["status"].eq("PASS").all()
    assert result["zone_count"].eq(7).all()
    assert result["external_fleet_rows"].eq(0).all()
    assert result["phs_contract_exact"].all()
    assert result["hydro_contract_exact"].all()
    assert result["internal_network_fixed"].all()
    assert result["external_interface_contract_exact"].all()
    authorization = result.set_index("scenario")["production_solve_authorized"].to_dict()
    assert authorization == {"Slow": False, "Base": False, "High": False}


def test_stage_B_2040_runtime_is_accepted_and_no_rerun_is_ready() -> None:
    readiness = assess_runtime_readiness()
    assert readiness["b10_2040_ready_to_execute"] is False
    assert readiness["b10_2040_status"] == "COMPLETE"
    assert readiness["stage_b_2040_static_contracts_ready"] is True
    assert readiness["stage_b_2040_ready_to_run"] is False
    assert readiness["stage_b_2040_production_complete"] is True
    assert readiness["stage_b_2040_authorized_scenarios"] == []
    assert readiness["stage_b_2040_unauthorized_scenarios"] == ["Slow", "Base", "High"]
    assert readiness["stage_b_2040_base_acceptance"] == "ACCEPTED_IMMUTABLE"
    assert readiness["stage_b_2040_authorized_solver"] is None
    assert readiness["accepted_runtime_bundle_complete"] is True
    assert readiness["accepted_runtime_files_missing"] == []
    assert readiness["accepted_runtime_manifest_hash_verified"] is True
    assert readiness["stage_b_2040_unsolved_network_build_ready"] is True
    assert readiness["candidate_assumptions_remaining"] == 0
    assert readiness["candidate_runtime_assumptions_accepted_as_authority"] is False
    assert readiness["stage_b_2040_blockers"] == []


def test_2040_preparation_QA_and_horizon_governance() -> None:
    qa = prepare(write_artifacts=False)
    assert qa["status"] == "PASS"
    assert qa["runtime_readiness"]["stage_b_2040_ready_to_run"] is False
    governance = qa["governance"]
    assert governance["b10_2040_authorized"] is False
    assert governance["b10_2040_status"] == "ETX7B10_2040_STAGE_A_TO_STAGE_B_PRICE_TRANSFER_COMPLETE"
    assert governance["b10_2050_authorized"] == yaml.safe_load((ROOT / "config/approval_gates.yaml").read_text(encoding="utf-8"))["stage_a_manual_gates"]["b10_2050_authorized"]
    assert governance["stage_b_2040_authorized"] is False
    assert governance["stage_b_2040_authorized_scenarios"] == []
    assert governance["stage_b_2040_authorized_solver"] == "gurobi"
    assert governance["stage_b_2050_authorized"] is False
    assert governance["global_b10_authorized"] is False
    assert governance["global_stage_b_authorized"] is False
    final_path = ROOT / "qa/stage_b/pre_2040/MEM_STAGE_B_2040_PREPARATION_FINAL_VERIFICATION_v1.0.json"
    final = json.loads(final_path.read_text(encoding="utf-8"))
    assert final["status"] == "PASS"
    assert final["b10_2040_ready_to_execute"] is True  # Historical pre-Runtime-1 receipt.
    assert final["stage_b_2040_ready_to_run"] is False  # Historical pre-Runtime-1 receipt.
    assert final["b10_2050_locked"] is True
    assert final["stage_b_2050_locked"] is True


def test_B10_2040_cli_exists_but_no_execution_occurs_in_test() -> None:
    parser = build_stage_a_parser()
    args = parser.parse_args(["b10-2040"])
    assert args.command == "b10-2040"
    assert args.execute is False
    gates = yaml.safe_load((ROOT / "config/approval_gates.yaml").read_text(encoding="utf-8"))
    assert gates["stage_a_manual_gates"]["b10_2040_authorized"] is False
    assert gates["stage_a_manual_gates"]["b10_2040_authorized_command"] is None
    assert gates["stage_a_manual_gates"]["stage_b_2050_authorized"] is False
    assert gates["stage_a_manual_gates"]["stage_b_2040_authorized_commands"] == {}

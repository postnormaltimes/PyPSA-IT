from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa
import pytest

from mem_model.stage_a.b8_diagnostics import verify_b8_immutability
from mem_model.stage_a.perimeter_closure import (
    EXPECTED_VIRTUAL_IDS,
    EXPECTED_VIRTUAL_MARKETS,
    VIRTUAL_CARRIER,
    _recover_cost_proxy,
    add_virtual_supply,
    build_parser,
    build_virtual_supply_contract,
    create_and_validate_linopy_model,
    load_s2_config,
    run_s2,
    validate_lp_static,
    validate_structural_delta,
    verify_s2_predecessors,
)
from mem_model.stage_a.receipts import sha256_file


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def s2_config() -> dict:
    return load_s2_config()


@pytest.fixture(scope="module")
def predecessor_verification(s2_config: dict) -> dict:
    return verify_s2_predecessors(s2_config)


@pytest.fixture(scope="module")
def virtual_contract(s2_config: dict, predecessor_verification: dict) -> pd.DataFrame:
    contract, _ = build_virtual_supply_contract(s2_config, predecessor_verification)
    return contract


@pytest.fixture(scope="module")
def short_networks(
    predecessor_verification: dict,
    virtual_contract: pd.DataFrame,
) -> tuple[pypsa.Network, pypsa.Network]:
    baseline = pypsa.Network(predecessor_verification["b6_network_path"])
    baseline.set_snapshots(baseline.snapshots[:6])
    return baseline, add_virtual_supply(baseline, virtual_contract)


def test_b1_through_b8_immutable_controls_pass(
    s2_config: dict,
    predecessor_verification: dict,
) -> None:
    identities = predecessor_verification["identities"]
    assert len(identities["b1_b5_input_locks"]) == 7
    assert {row["status"] for row in identities["b1_b5_input_locks"]} == {"PASS"}
    assert identities["b6_receipt"]["sha256"] == s2_config["accepted_inputs"]["b6_receipt"]["sha256"]
    assert identities["b6_2040_unsolved_network"]["sha256"] == s2_config["accepted_inputs"]["b6_2040_unsolved_network"]["sha256"]
    assert identities["b7_receipt"]["sha256"] == s2_config["accepted_inputs"]["b7_receipt"]["sha256"]
    assert identities["b8_receipt"]["sha256"] == s2_config["accepted_inputs"]["b8_receipt"]["sha256"]
    assert identities["b8_manifest"]["sha256"] == s2_config["accepted_inputs"]["b8_result_manifest"]["sha256"]
    assert identities["b8_manifest"]["member_count"] == 9


def test_b8_receipt_manifest_and_members_remain_byte_identical(s2_config: dict) -> None:
    verified = verify_b8_immutability()
    assert verified["receipt_sha256"] == s2_config["accepted_inputs"]["b8_receipt"]["sha256"]
    assert verified["manifest_sha256"] == s2_config["accepted_inputs"]["b8_result_manifest"]["sha256"]
    assert len(verified["members"]) == 9
    assert all(sha256_file(ROOT / row["path"]) == row["sha256"] for row in verified["members"])


def test_read_only_b8_annual_diagnostic_reconciles_to_accepted_result() -> None:
    diagnostic = json.loads(
        (ROOT / "qa/stage_a/etx7b8/MEM_ETX7B8_2040_Base_Annual_Diagnostic_v1.0.json").read_text(
            encoding="utf-8"
        )
    )
    system = diagnostic["system"]
    assert diagnostic["status"] == "PASS"
    assert diagnostic["optimization_rerun"] is False
    assert diagnostic["source_manifest_members_verified"] == 9
    assert system["total_system_load_MWh"] == pytest.approx(1448141533.3333333)
    assert system["total_system_load_shedding_MWh"] == pytest.approx(1823459.4540542054)
    assert system["load_shedding_percent_of_total_load"] == pytest.approx(0.12591721265372213)
    assert system["system_hours_with_any_shedding_above_1e_6_MW"] == 519
    assert system["storage_state_count"] == 33
    assert system["water_state_bus_count"] == 18
    assert system["cyclic_state_count"] == 33
    assert system["ANNUAL_STATES_CYCLIC_OVER_8760H"] is True


def test_b8_diagnostic_manifest_and_interface_evidence_are_complete() -> None:
    manifest_path = ROOT / "qa/stage_a/etx7b8/MEM_ETX7B8_2040_Base_Annual_Diagnostic_Manifest_v1.0.csv"
    manifest = pd.read_csv(manifest_path)
    assert sha256_file(manifest_path) == "70DCC93B178C4336153040BBFC30B80750C3E251A10E2422834E86F05EBADCCD"
    assert len(manifest) == 5
    assert all(sha256_file(ROOT / row.relative_path) == row.sha256 for row in manifest.itertuples())
    interfaces = pd.read_csv(
        ROOT / "qa/stage_a/etx7b8/MEM_ETX7B8_2040_Base_Scarcity_Interface_Diagnostic_v1.0.csv"
    )
    aggregates = interfaces.loc[interfaces["record_type"].eq("AFFECTED_MARKET_SCARCITY_AGGREGATE")]
    assert set(aggregates["affected_market"]) == set(EXPECTED_VIRTUAL_MARKETS)
    assert np.allclose(aggregates["share_shedding_hours_all_inbound_saturated"], 1.0)
    assert np.allclose(aggregates["share_shedding_MWh_all_inbound_saturated"], 1.0)


def test_exact_fr_tn_gr_capacity_derivation_and_no_other_virtual_supply(
    virtual_contract: pd.DataFrame,
) -> None:
    expected = {
        "FR": 25199.225303080686,
        "TN": 1643.2154545427973,
        "GR": 3624.529283896116,
    }
    assert len(virtual_contract) == 3
    assert tuple(virtual_contract["bus"]) == EXPECTED_VIRTUAL_MARKETS
    assert tuple(virtual_contract["generator_id"]) == EXPECTED_VIRTUAL_IDS
    assert "MT" not in set(virtual_contract["bus"])
    assert set(virtual_contract["carrier"]) == {VIRTUAL_CARRIER}
    for row in virtual_contract.itertuples(index=False):
        assert float(row.p_nom_MW) == expected[row.bus]
        assert row.capacity_rule == "EXACT_MAX_HOURLY_B8_LOAD_SHEDDING_MW"


def test_unique_frozen_ocgt_proxy_and_common_exact_marginal_cost(s2_config: dict) -> None:
    provenance = _recover_cost_proxy(s2_config)
    assert provenance["source_asset_id"] == "ETX7B2_IT_2040_METHANE_GT_OCGT_NON_CHP_5D69468168"
    assert provenance["technology"] == "METHANE_GT_OCGT_NON_CHP"
    assert provenance["CHP_flag"] is False
    assert provenance["efficiency_el"] == 0.42
    assert provenance["fuel_price_EUR2025_per_MWh_th"] == 38.0563456
    assert provenance["CO2_price_EUR2025_per_t"] == 104.5504
    assert provenance["chargeable_CO2_t_per_MWh_th"] == 0.198
    assert provenance["VOM_EUR2025_per_MWh_el"] == 6.0111
    assert provenance["marginal_cost_EUR2025_per_MWh_el"] == pytest.approx(145.909492380952, abs=1e-12)


def test_only_allowed_structural_delta_and_baseline_preservation(
    short_networks: tuple[pypsa.Network, pypsa.Network],
    virtual_contract: pd.DataFrame,
) -> None:
    baseline, s2 = short_networks
    result = validate_structural_delta(baseline, s2, virtual_contract)
    assert result["status"] == "PASS"
    assert result["s2_counts"]["carriers"] == result["baseline_counts"]["carriers"] + 1
    assert result["s2_counts"]["generators"] == result["baseline_counts"]["generators"] + 3
    for component in ("buses", "loads", "links", "stores", "market_buses", "interconnectors"):
        assert result["s2_counts"][component] == result["baseline_counts"][component]
    assert result["MT_virtual_supply"] == "NONE"


def test_all_s2_capacity_is_fixed_and_unit_commitment_is_disabled(
    short_networks: tuple[pypsa.Network, pypsa.Network],
) -> None:
    _, s2 = short_networks
    result = validate_lp_static(s2)
    assert result["status"] == "PASS"
    assert result["capacity_expansion"] is False
    assert result["unit_commitment"] is False
    for generator in EXPECTED_VIRTUAL_IDS:
        row = s2.generators.loc[generator]
        assert not bool(row.p_nom_extendable)
        assert not bool(row.committable)
        assert row.p_min_pu == 0.0
        assert row.p_max_pu == 1.0
        assert row.efficiency == 1.0


def test_short_accepted_network_fixture_has_no_integer_or_binary_variables(
    short_networks: tuple[pypsa.Network, pypsa.Network],
) -> None:
    _, s2 = short_networks
    result = create_and_validate_linopy_model(s2)
    assert result["linopy_model_assembled"] is True
    assert result["integer_variables"] == 0
    assert result["binary_variables"] == 0
    assert result["continuous_linear_program_only"] is True


def test_prepared_artifacts_match_contract_and_record_no_production_solve(
    s2_config: dict,
    virtual_contract: pd.DataFrame,
) -> None:
    contract_path = ROOT / s2_config["artifacts"]["virtual_supply_contract"]
    provenance_path = ROOT / s2_config["artifacts"]["cost_provenance"]
    qa_path = ROOT / s2_config["artifacts"]["structural_QA"]
    pd.testing.assert_frame_equal(
        pd.read_csv(contract_path, float_precision="round_trip"),
        virtual_contract,
        check_dtype=False,
        check_exact=True,
    )
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    assert provenance["research_performed"] is False
    assert qa["production_S2_solve_executed"] is False
    assert qa["short_unsolved_LP_fixture"]["integer_variables"] == 0
    assert qa["short_unsolved_LP_fixture"]["binary_variables"] == 0


def test_output_namespace_isolated_and_formal_successors_remain_locked(s2_config: dict) -> None:
    artifacts = s2_config["artifacts"]
    assert artifacts["result_directory"] == "stage_a_results/2040_perimeter_closure_s2"
    assert all(
        not str(value).startswith("stage_a_results/2040_full/")
        for value in artifacts.values()
        if isinstance(value, str)
    )
    assert artifacts["receipt"] != "qa/stage_a/etx7b8/MEM_ETX7B8_Run_Receipt_v1.0.json"
    assert s2_config["governance"]["formal_successor_gate_status"] == "LOCKED_PENDING_S2_REVIEW_AND_SOL_DECISION"
    assert s2_config["governance"]["stage_b_status"] == "LOCKED"


def test_manual_entrypoint_is_guarded_and_canonical(s2_config: dict) -> None:
    parser = build_parser()
    args = parser.parse_args(["s2"])
    assert args.execute is False
    with pytest.raises(SystemExit, match="S2_EXPERIMENT_NOT_EXECUTED"):
        run_s2(args)
    assert s2_config["manual_command"] == (
        ".\\.venv\\Scripts\\python.exe -m mem_model.stage_a.perimeter_closure s2 --execute"
    )


def test_predecessor_hash_check_fails_closed(s2_config: dict) -> None:
    altered = copy.deepcopy(s2_config)
    altered["accepted_inputs"]["b6_2040_unsolved_network"]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="B6_2040_NETWORK_HASH_MISMATCH"):
        verify_s2_predecessors(altered)


def test_s2_solved_result_and_receipt_are_frozen_diagnostic_evidence(s2_config: dict) -> None:
    result_dir = ROOT / s2_config["artifacts"]["result_directory"]
    receipt = ROOT / s2_config["artifacts"]["receipt"]
    solved = result_dir / "MEM_ETX7B8C_S2_2040_BASE_SOLVED.nc"
    assert solved.exists()
    assert receipt.exists()
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert sha256_file(receipt) == "4B515D39145E31D93F1BADFBAA35E1B946C128D2B62438B0DD43BA5202999C01"
    assert payload["gate"] == "ETX7B8C_S2_EXPERIMENT_COMPLETE"
    assert payload["status"] == "PASS"
    assert payload["qa"]["formal_B9_authorized"] is False

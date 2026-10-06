"""Read-only controls for the prepared, unsolved 2050 Stage-B scenarios."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from mem_model.common import ACCEPTED_RUNTIME_2050, CONFIG, ROOT, sha256_file
from mem_model.stage_b_2050_runtime import (
    MANIFEST_NAME,
    PREP_DIR,
    SCENARIOS,
    verify_2040_unchanged,
    verify_b9h_immutable,
    verify_b10_2050,
    verify_prepared_runtime_manifest,
)


def test_b10_2050_is_b9h_only_and_immutable() -> None:
    b9h = verify_b9h_immutable()
    assert b9h["status"] == "PASS" and b9h["manifest_members"] == 27
    control = verify_b10_2050()
    assert control["status"] == "PASS"
    assert control["source"] == "ETX-7B9H"
    assert control["manifest_members"] == 3
    assert control["rows"] == 210240
    assert control["manifest_sha256"] == "ac3f242338a289da740801a6b16f924e3fecf45ed17fe4f55487d356b8852fa5"


def test_2040_runtime_and_2050_runtime_bundles_are_independently_locked() -> None:
    old = verify_2040_unchanged()
    new = verify_prepared_runtime_manifest()
    assert old["manifest_sha256"] == "5baae9bc065221463a422c2835a09dbfb4bc7b1e8b97902f34502d4d7ea9a63b"
    assert len(old["members"]) == 5
    assert new["status"] == "PASS"
    assert new["manifest_sha256"] == "e7f224d4d33fe8b7262e3974db1cb85d250279febccbc689a13d16acf472a993"
    assert len(new["members"]) == 5
    assert (ACCEPTED_RUNTIME_2050 / MANIFEST_NAME).is_file()


def test_2050_runtime_semantic_and_static_reconciliation_pass() -> None:
    qa = pd.read_csv(ROOT / "qa/stage_b/runtime_2050/MEM_STAGE_B_2050_RUNTIME_CHECKS_v1.0.csv")
    reconciliation = pd.read_csv(ROOT / "qa/stage_b/runtime_2050/MEM_STAGE_B_2050_RUNTIME_RECONCILIATION_v1.0.csv")
    final = json.loads((ROOT / "qa/stage_b/runtime_2050/MEM_STAGE_B_2050_RUNTIME_FINAL_VERIFICATION_v1.0.json").read_text())
    assert len(qa) == 79 and qa.status.eq("PASS").all()
    assert len(reconciliation) == 384 and reconciliation.status.eq("PASS").all()
    assert final["deterministic_rebuild"] is True
    assert final["production_optimization_executed"] is False


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_each_2050_network_is_structurally_prepared_but_unsolved(scenario: str) -> None:
    path = PREP_DIR / f"MEM_STAGE_B_2050_{scenario.upper()}_Preparation_Receipt_v1.0.json"
    receipt = json.loads(path.read_text())
    checks = pd.read_csv(ROOT / receipt["structural_checks_path"])
    assert sha256_file(ROOT / receipt["structural_checks_path"]) == receipt["structural_checks_sha256"]
    assert len(checks) == 49 and checks.status.eq("PASS").all()
    assert receipt["status"] == "READY_NOT_EXECUTED"
    assert receipt["snapshots"] == 8760
    assert receipt["zone_count"] == 7
    assert receipt["hydro_state_count"] == 20
    assert receipt["nuclear_MW"] == 8000
    assert receipt["internal_directional_links"] == 20
    assert receipt["external_interface_contract_rows"] == 20
    assert receipt["external_price_source"] == "ETX-7B9H_VIA_B10_2050"
    assert receipt["integer_variables"] == receipt["binary_variables"] == 0
    assert receipt["production_optimization_executed"] is False
    assert sha256_file(ROOT / receipt["unsolved_network"]) == receipt["unsolved_network_sha256"]


def test_cross_scenario_controls_and_no_unexplained_drift() -> None:
    comparison = pd.read_csv(PREP_DIR / "MEM_STAGE_B_2050_Cross_Scenario_Comparison_v1.0.csv")
    final = json.loads((PREP_DIR / "MEM_STAGE_B_2050_PREPARATION_FINAL_VERIFICATION_v1.0.json").read_text())
    assert len(comparison) == 23
    assert set(comparison.classification) == {"EXPECTED_SCENARIO_DIFFERENCE", "SHARED_ACCEPTED_CONTROL"}
    assert final["unexpected_implementation_drift"] == 0
    assert final["scenarios"] == {scenario: "READY_NOT_EXECUTED" for scenario in SCENARIOS}
    assert final["production_optimization_executed"] is False
    assert final["production_execution_authorized"] is False


def test_pre_p2x_2050_production_gate_is_diagnostic_and_locked() -> None:
    gates = yaml.safe_load((CONFIG / "approval_gates.yaml").read_text(encoding="utf-8"))
    gate = gates["stage_b_2050_production"]
    assert gate["status"] == "PRE_P2X_FLEX_DIAGNOSTIC_BASELINE"
    assert gate["execution_enabled"] is False
    assert gate["scenario_authorization"] == {"Slow": False, "Base": False, "High": False}
    assert gate["required_source"] == "ETX-7B9H"
    assert gates["stage_a_manual_gates"]["b10_2050_status"] == "ETX7B10_2050_STAGE_A_TO_STAGE_B_PRICE_TRANSFER_COMPLETE"
    assert gates["stage_a_manual_gates"]["stage_b_2050_authorized"] is False

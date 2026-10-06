from __future__ import annotations

import json

import pandas as pd
import pytest

from mem_model import solve_scenario
from mem_model.common import CONFIG, ROOT, load_yaml, sha256_file
from mem_model.stage_b_2040_closure import RUNTIME_MANIFEST_HASH, SCENARIOS, SOLVED_HASHES, run_dir, solved_path


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_all_completed_2040_scenarios_refused_before_network_construction(
    scenario: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Network construction or optimization must not be reached")

    monkeypatch.setattr(solve_scenario, "build_network", forbidden)
    monkeypatch.setattr(solve_scenario, "_invoke_solver", forbidden)
    with pytest.raises(SystemExit, match="production complete; rerun not authorized"):
        solve_scenario.main(["--year", "2040", "--scenario", scenario, "--execute-full-year"])


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_2050_remains_separately_locked(scenario: str) -> None:
    with pytest.raises(SystemExit, match="STAGE_B_2050_PRODUCTION_SOLVE_REFUSED: pending validation of historical inputs"):
        solve_scenario.main(["--year", "2050", "--scenario", scenario, "--execute-full-year"])


def test_completed_governance_is_horizon_scoped_and_fail_closed() -> None:
    gates = load_yaml(CONFIG / "approval_gates.yaml")
    gate = gates["stage_b_2040_production"]
    assert gate["status"] == "STAGE_B_2040_THREE_SCENARIO_PRODUCTION_COMPLETE"
    assert gate["execution_enabled"] is False
    assert gate["scenario_authorization"] == {"Slow": False, "Base": False, "High": False}
    assert gate["accepted_scenarios"] == list(SCENARIOS)
    assert gate["authorized_commands"] == {}
    assert gate["required_runtime_manifest_sha256"].lower() == RUNTIME_MANIFEST_HASH
    assert gate["solver"]["name"] == "gurobi"
    assert gate["solver"]["options"] == {"Threads": 1, "Seed": 0}
    assert gate["solver"]["include_objective_constant"] is False
    assert gates["stage_b_2050_production"]["execution_enabled"] is False
    assert gates["stage_a_manual_gates"]["stage_b_2050_authorized"] is False


def test_acceptance_hashes_and_all_reporting_members_are_pinned() -> None:
    gate = load_yaml(CONFIG / "approval_gates.yaml")["stage_b_2040_production"]
    assert solve_scenario.verify_base_acceptance(gate)["status"] == "ACCEPTED_IMMUTABLE"
    for scenario in SCENARIOS:
        run = run_dir(scenario)
        accepted = run / f"MEM_STAGE_B_2040_{scenario.upper()}_ACCEPTANCE_v1.0.json"
        receipt = json.loads(accepted.read_text(encoding="utf-8"))
        assert receipt["status"] == "ACCEPTED_IMMUTABLE"
        assert receipt["solved_network_sha256"].lower() == SOLVED_HASHES[scenario]
        assert sha256_file(solved_path(scenario)) == SOLVED_HASHES[scenario]
        assert receipt["accepted_runtime_manifest_sha256"].lower() == RUNTIME_MANIFEST_HASH
        assert receipt["solver"] == "gurobi"
        assert receipt["solver_options"] == {"Threads": 1, "Seed": 0, "include_objective_constant": False}
        assert receipt["termination_condition"] == "optimal"
        assert receipt["snapshots"] == 8760
        assert receipt["national_load_shedding_TWh"] == 0.0
        assert receipt["electrical_balance_status"] == "PASS"
        assert receipt["reporting_manifest_sha256"].lower() == sha256_file(run / "MEM_CANONICAL_REPORTING_MANIFEST.csv")
        assert receipt["reporting_receipt_sha256"].lower() == sha256_file(run / "MEM_CANONICAL_REPORTING_RECEIPT.json")
        manifest = pd.read_csv(run / "MEM_CANONICAL_REPORTING_MANIFEST.csv")
        for member in manifest.itertuples(index=False):
            assert sha256_file(run / member.artifact) == member.sha256


def test_accepted_base_receipt_mismatch_fails() -> None:
    gate = load_yaml(CONFIG / "approval_gates.yaml")["stage_b_2040_production"]
    gate["accepted_base_receipt_sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="receipt hash differs"):
        solve_scenario.verify_base_acceptance(gate)


def test_solver_contract_remains_gurobi_without_invoking_solver() -> None:
    observed: dict[str, object] = {}

    class FakeNetwork:
        def optimize(self, **kwargs: object) -> tuple[str, str]:
            observed.update(kwargs)
            return "ok", "optimal"

    assert solve_scenario._invoke_solver(FakeNetwork()) == ("ok", "optimal")
    assert observed == {
        "solver_name": "gurobi",
        "solver_options": {"Threads": 1, "Seed": 0},
        "include_objective_constant": False,
        "log_to_console": True,
    }


def test_real_comparison_package_has_source_manifest_and_no_solver_call() -> None:
    output = ROOT / "results" / "MEM_2040_THREE_SCENARIO_COMPARISON"
    receipt = json.loads((output / "MEM_CANONICAL_CROSS_SCENARIO_REPORTING_RECEIPT.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "PASS"
    assert receipt["reporting_solver_invocations"] == 0
    assert receipt["table_count"] == 8
    assert receipt["figure_file_count"] == 16
    manifest_path = output / "MEM_CANONICAL_CROSS_SCENARIO_REPORTING_MANIFEST.csv"
    assert receipt["manifest_sha256"] == sha256_file(manifest_path)
    manifest = pd.read_csv(manifest_path)
    assert len(manifest) == 24
    for member in manifest.itertuples(index=False):
        assert sha256_file(output / member.artifact) == member.sha256
    for scenario in SCENARIOS:
        accepted = run_dir(scenario) / f"MEM_STAGE_B_2040_{scenario.upper()}_ACCEPTANCE_v1.0.json"
        assert receipt["source_acceptance_sha256"][scenario] == sha256_file(accepted)

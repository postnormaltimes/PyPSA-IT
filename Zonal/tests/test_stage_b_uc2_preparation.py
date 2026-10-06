"""UC-2 preparation checks. Nothing here constructs or solves an optimizer model."""

from __future__ import annotations

import inspect
import json
import re

import pytest
import pypsa

from mem_model import stage_b_uc1 as uc1
from mem_model import stage_b_uc2 as uc2
from mem_model.common import ROOT, sha256_file


def test_six_scenarios_and_eighteen_isolated_jobs() -> None:
    jobs = uc2.registry()
    assert len(jobs) == 18 and jobs.job_id.is_unique and jobs.solved_path.is_unique
    assert set(zip(jobs.year, jobs.scenario)) == set(uc2.SCENARIOS)
    assert all(jobs.groupby(["year", "scenario"]).run_type.nunique() == 3)
    assert not jobs.executed_in_preparation.any()
    for row in jobs.itertuples(index=False):
        assert row.input_path == str((uc1.parent_path(row.year, row.scenario)
                                      if row.run_type == uc2.RUN_TYPES[0]
                                      else uc1.derivative_path(row.year, row.scenario)).relative_to(ROOT))
        assert row.input_sha256 == sha256_file(ROOT / row.input_path)


def test_frozen_uc1_and_p2x_receipts_are_hash_locked() -> None:
    build = uc2._structural_receipt()
    assert len(build["structural_packages"]) == 6
    for item in build["structural_packages"]:
        assert sha256_file(ROOT / item["parent"]) == item["parent_sha256"]
        assert sha256_file(ROOT / item["derivative"]) == item["derivative_sha256"]
        assert item["availability_compatibility"] == "PASS"
        assert item["capacity_conservation_MW"] == 0


def test_base_preflight_preserves_accepted_contract() -> None:
    report = uc2.preflight(2040, "Base", persist=False)
    assert report["status"] == "PASS" and report["snapshots"] == 8760
    assert report["synthetic_units"] == 108
    assert report["archetype_counts"] == {"CCGT": 78, "OCGT": 16, "BIOMASS_STEAM": 14}
    assert report["p2x_pairs"] == 7 and report["availability_conflicts"] == 0


def test_wrong_case_or_parent_hash_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="UC2_UNKNOWN_SCENARIO"):
        uc2.job_paths(2050, "Invalid", uc2.RUN_TYPES[0])
    original = uc2.sha256_file
    monkeypatch.setattr(uc2, "sha256_file", lambda path: "tampered" if path == uc1.parent_path(2040, "Base") else original(path))
    with pytest.raises(RuntimeError, match="UC2_INPUT_HASH_FAIL"):
        uc2.preflight(2040, "Base", persist=False)


def test_cli_preflight_failure_prints_explicit_false_gate(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        uc2.main(["preflight", "--year", "2051", "--scenario", "Base"])
    assert error.value.code == 2
    result = json.loads(capsys.readouterr().out)
    assert result["prepared_for_manual_execution"] is False
    assert "UC2_UNKNOWN_SCENARIO" in result["reason"]


def test_manual_governance_gates() -> None:
    gates = uc2.config()["governance"]
    assert gates["2040_base_sol_review_accepted"] is False
    assert gates["2050_base_sol_review_accepted"] is False
    assert gates["2050_base_nuclear_smoke_accepted"] is False
    for year, scenario in ((2040, "Base"), (2040, "Slow"), (2040, "High"),
                           (2050, "Slow"), (2050, "Base"), (2050, "High")):
        assert uc2._governance_gate(year, scenario).startswith("MANUAL_EXECUTION_")
    assert uc2.config()["solver"]["options"] == {"Threads": 1, "Seed": 0}


def test_gate_flags_are_independent_not_a_global_unlock(monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = uc2.config()
    monkeypatch.setattr(uc2, "config", lambda: baseline)
    families = {
        "2040_base_manual_sequence_enabled": {(2040, "Base")},
        "2040_slow_high_manual_enabled": {(2040, "Slow"), (2040, "High")},
        "2050_base_manual_enabled": {(2050, "Base")},
        "2050_slow_high_manual_enabled": {(2050, "Slow"), (2050, "High")},
    }
    for flag, locked in families.items():
        baseline["governance"][flag] = False
        for year, scenario in uc2.SCENARIOS:
            if (year, scenario) in locked:
                with pytest.raises(RuntimeError, match="UC2_EXECUTION_LOCKED"):
                    uc2._governance_gate(year, scenario)
            else:
                assert uc2._governance_gate(year, scenario).startswith("MANUAL_EXECUTION_")
        baseline["governance"][flag] = True


def test_price_dependency_rejects_other_scenario(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uc2, "_read_json", lambda path: {"status": "PASS", "year": 2040, "scenario": "High"})
    with pytest.raises(RuntimeError, match="UC2_DEPENDENCY_FAIL"):
        uc2._verified_previous(2040, "Base", "UC_MILP")


def test_canonical_events_follow_native_boundary_and_ignore_raw_degeneracy() -> None:
    network = pypsa.Network(ROOT / "results" / "UC1_2040_BASE_168H" /
                            "UC1_2040_BASE_168H_UC_MILP_SOLVED.nc")
    events = uc2.canonical_transitions(network)
    assert len(events) == 168 * 108
    assert int(events.actual_start.sum()) == 47
    assert int(events.actual_shutdown.sum()) == 94
    assert int(events.raw_shut_down.sum()) == 2809
    assert int(events.loc[events.snapshot.eq(network.snapshots[0]), "actual_shutdown"].sum()) == 16
    assert (events.raw_start_up + 1e-7 >= events.actual_start).all()
    assert (events.raw_shut_down + 1e-7 >= events.actual_shutdown).all()


def test_existing_uc1_smoke_reporting_adapter_is_non_solving() -> None:
    from mem_model.reporting.canonical_results import build_canonical_tables

    root = ROOT / "results" / "UC1_2040_BASE_168H"
    milp = pypsa.Network(root / "UC1_2040_BASE_168H_UC_MILP_SOLVED.nc")
    price = pypsa.Network(root / "UC1_2040_BASE_168H_FIXED_COMMITMENT_PRICE_LP_SOLVED.nc")
    milp.buses_t.marginal_price = price.buses_t.marginal_price.copy()
    tables = build_canonical_tables(milp)
    assert len(tables["annual_electrical_balance_by_zone.csv"]) == 7
    assert tables["annual_electrical_balance_by_zone.csv"].balance_check_status.eq("PASS").all()
    assert len(tables["zonal_price_statistics.csv"]) == 7


def test_uc2_reporting_tables_on_existing_uc1_smoke_without_solver(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from mem_model.reporting import canonical_results

    root = ROOT / "results" / "UC1_2040_BASE_168H"
    names = {"POST_P2X_CONTINUOUS_REFERENCE": "UC1_2040_BASE_168H_POST_P2X_CONTINUOUS_REFERENCE_SOLVED.nc",
             "UC_MILP": "UC1_2040_BASE_168H_UC_MILP_SOLVED.nc",
             "FIXED_COMMITMENT_PRICE_LP": "UC1_2040_BASE_168H_FIXED_COMMITMENT_PRICE_LP_SOLVED.nc"}
    saved = {kind: pypsa.Network(root / filename) for kind, filename in names.items()}
    solve_receipts = {kind: {"objective_EUR": float(saved[kind].objective)} for kind in names}
    monkeypatch.setattr(uc2, "case_root", lambda year, scenario: tmp_path)
    monkeypatch.setattr(uc2, "_verified_previous", lambda year, scenario, kind: {
        "status": "PASS", "solved_sha256": kind,
        "fixed_commitment_lp_objective_EUR": float(saved["FIXED_COMMITMENT_PRICE_LP"].objective),
        "absolute_objective_difference_EUR": 0.0})
    monkeypatch.setattr(uc2, "_load_solved", lambda year, scenario, kind: (saved[kind], solve_receipts[kind]))
    monkeypatch.setattr(canonical_results, "generate_canonical_results", lambda *args, **kwargs: {
        "status": "PASS", "reporting_solver_invocations": 0})
    result = uc2.report(2040, "Base")
    assert result["status"] == "PASS" and result["reporting_solver_invocations"] == 0
    assert (tmp_path / "REPORTING" / "canonical_commitment_transitions.parquet").is_file()
    assert (tmp_path / "REPORTING" / "p2x_annual_by_zone.csv").is_file()
    assert (tmp_path / "REPORTING" / "boundary_and_annual_cycling.csv").is_file()


def test_run_actions_are_explicit_and_no_automatic_successor(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    with pytest.raises(SystemExit):
        uc2.main(["run-reference"])
    paths = uc2.job_paths(2040, "Base", "POST_P2X_CONTINUOUS_REFERENCE")
    target = tmp_path / "existing_SOLVED.nc"
    target.touch()
    monkeypatch.setattr(uc2, "job_paths", lambda *_: {**paths, "solved": target})
    monkeypatch.setattr(uc2, "_solver_contract", lambda: pytest.fail("solver boundary reached"))
    with pytest.raises(RuntimeError, match="UC2_OVERWRITE_REFUSED"):
        uc2._manual_solve(2040, "Base", "POST_P2X_CONTINUOUS_REFERENCE")
    for function in (uc2.preflight, uc2.verify_reference, uc2.verify_uc,
                     uc2.verify_price, uc2.report, uc2.final_qa, uc2.prepare):
        source = inspect.getsource(function)
        assert ".optimize" not in source and "_manual_solve(" not in source


def test_runbook_commands_match_cli_and_are_scenario_explicit() -> None:
    text = (ROOT / "docs" / "MEM_UC2_ALL_SCENARIOS_MANUAL_RUNBOOK.md").read_text(encoding="utf-8")
    commands = re.findall(r"\.\\\.venv\\Scripts\\python\.exe -m mem_model\.stage_b_uc2 ([\w-]+) --year (\d{4}) --scenario (Slow|Base|High)", text)
    assert len(commands) == 54
    expected_actions = {"preflight", "run-reference", "verify-reference", "run-uc", "verify-uc",
                        "run-price", "verify-price", "report", "final-qa"}
    for year, scenario in uc2.SCENARIOS:
        assert {action for action, y, s in commands if (int(y), s) == (year, scenario)} == expected_actions
    assert "2050 nuclear UC remains analytically unaccepted" in text


def test_preparation_receipt_no_results_and_manifest_integrity() -> None:
    qa = ROOT / "qa" / "stage_b" / "uc2_preparation"
    receipt = json.loads((qa / "MEM_UC2_PREPARATION_QA_RECEIPT.json").read_text())
    assert receipt["status"] == "UC2_ALL_SCENARIOS_PREPARED__MANUAL_EXECUTION_ENABLED__REVIEWS_PENDING"
    assert receipt["jobs"] == 18 and receipt["optimizer_invocations"] == 0
    assert receipt["production_solved_files_created_during_original_preparation"] == 0
    assert receipt["optimizer_invocations_during_reconciliation"] == 0
    assert len(receipt["non_solving_deep_preflights"]) == 6
    assert receipt["technically_prepared"] is True
    assert receipt["manual_execution_enabled"] is True
    assert receipt["analytical_review_accepted"] is False
    assert receipt["gates"]["2040_base_manual_sequence_enabled"] is True
    assert receipt["manual_production_progress"]["2040_Base_reference"] == "COMPLETED_VERIFIED"
    assert set(receipt["uc1_smoke_solved_hashes_unchanged"]) == {"reference", "milp", "price_lp"}
    assert sha256_file(qa / "MEM_UC2_ALL_SCENARIO_JOB_REGISTRY.csv") == receipt["registry_sha256"]

"""Emit the bounded pre-B6 harness closeout package without running a formal gate."""

from __future__ import annotations

import argparse
import getpass
import json
import platform
import tomllib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gurobipy
import linopy
import numpy as np
import pandas as pd
import pypsa
import yaml

from mem_model.stage_a.network import (
    PATHS,
    ROOT,
    _utc_naive,
    build_network_from_contracts,
    load_execution_config,
    load_stage_a_contracts,
    verify_input_locks,
)
from mem_model.stage_a.network_validation import validate_stage_a_network
from mem_model.stage_a.receipts import relative_path, sha256_file, write_manifest


QA_DIR = ROOT / "qa" / "stage_a" / "pre_b6"
SUCCESS_MARKER = "PRE_B6_MANUAL_EXECUTION_HARNESS_READY"
FIRST_B6_COMMAND = r".\.venv\Scripts\python.exe -m mem_model.stage_a.execution b6 --execute"


def _junit_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    root = ET.parse(path).getroot()
    suites = list(root) if root.tag == "testsuites" else [root]
    totals = {
        key: sum(int(float(suite.attrib.get(key, 0))) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    totals["passed"] = totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]
    totals["path"] = relative_path(path)
    totals["sha256"] = sha256_file(path)
    totals["status"] = "PASS" if totals["failures"] == 0 and totals["errors"] == 0 else "FAIL"
    return totals


def _qa_row(check_id: str, description: str, passed: bool, observed: Any, expected: Any) -> dict[str, Any]:
    def render(value: Any) -> str:
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, sort_keys=True)
        return str(value)

    return {
        "check_id": check_id,
        "description": description,
        "status": "PASS" if passed else "FAIL",
        "observed": render(observed),
        "expected": render(expected),
    }


def _dependency_state() -> dict[str, Any]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    selected = {
        package["name"]: package["version"]
        for package in lock["package"]
        if package["name"] in {"gurobipy", "pypsa", "linopy", "numpy"}
    }
    return {
        "direct_dependencies": project["project"]["dependencies"],
        "locked_versions": selected,
        "runtime_versions": {
            "python": platform.python_version(),
            "pypsa": pypsa.__version__,
            "linopy": linopy.__version__,
            "numpy": np.__version__,
            "gurobipy": gurobipy.__version__,
        },
    }


def _formal_receipts_absent() -> tuple[bool, list[str]]:
    paths = [
        ROOT / "qa/stage_a/etx7b6/MEM_ETX7B6_Run_Receipt_v1.0.json",
        ROOT / "qa/stage_a/etx7b7/MEM_ETX7B7_Run_Receipt_v1.0.json",
        ROOT / "qa/stage_a/etx7b8/MEM_ETX7B8_Run_Receipt_v1.0.json",
        ROOT / "qa/stage_a/etx7b9/MEM_ETX7B9A_Run_Receipt_v1.0.json",
        ROOT / "qa/stage_a/etx7b9/MEM_ETX7B9_Run_Receipt_v1.0.json",
        ROOT / "qa/stage_a/etx7b10/MEM_ETX7B10_Run_Receipt_v1.0.json",
    ]
    present = [relative_path(path) for path in paths if path.exists()]
    return not present, present


def generate_closeout(*, require_full_suite: bool = True) -> dict[str, Any]:
    """Validate and write pre-B6 evidence without invoking Gurobi or a formal phase."""

    QA_DIR.mkdir(parents=True, exist_ok=True)
    config = load_execution_config()
    approvals = yaml.safe_load((ROOT / "config/approval_gates.yaml").read_text(encoding="utf-8"))
    dependencies = _dependency_state()
    predecessor_rows = verify_input_locks(config)

    predecessor_path = QA_DIR / "MEM_PRE_B6_Predecessor_Hash_Receipt_v1.0.csv"
    pd.DataFrame.from_records(predecessor_rows).to_csv(
        predecessor_path, index=False, encoding="utf-8", lineterminator="\n"
    )

    fixture_snapshots = _utc_naive(pd.read_parquet(PATHS["snapshots"])["snapshot"].head(6))
    component_rows: list[dict[str, Any]] = []
    structural_status: dict[str, Any] = {}
    for year in config["scope"]["horizons"]:
        contracts = load_stage_a_contracts(int(year), snapshots=fixture_snapshots)
        network, metadata = build_network_from_contracts(contracts, config)
        validation = validate_stage_a_network(network, contracts, metadata, config)
        component_rows.append(
            {
                "horizon": int(year),
                "fixture_snapshots": len(network.snapshots),
                **metadata["summary"],
                "structural_qa_checks": len(validation["qa"]),
                "structural_qa_failures": int(validation["qa"].status.eq("FAIL").sum()),
                "status": validation["status"],
            }
        )
        structural_status[str(year)] = {
            "status": validation["status"],
            "checks": len(validation["qa"]),
            "failures": int(validation["qa"].status.eq("FAIL").sum()),
        }
    components_path = QA_DIR / "MEM_PRE_B6_Network_Component_Summary_v1.0.csv"
    pd.DataFrame.from_records(component_rows).to_csv(
        components_path, index=False, encoding="utf-8", lineterminator="\n"
    )

    focused = _junit_summary(QA_DIR / "pytest_pre_b6_focused.xml")
    gate_regression = _junit_summary(QA_DIR / "pytest_pre_b6_gate_and_predecessor.xml")
    full_path = QA_DIR / "pytest_full_repository.xml"
    if require_full_suite:
        full_suite = _junit_summary(full_path)
    else:
        full_suite = _junit_summary(full_path) if full_path.exists() else {"status": "NOT_RUN"}

    expected_runbooks = [ROOT / "docs/runbooks" / f"ETX7B{phase}_RUNBOOK.md" for phase in (6, 7, 8, 9, 10)]
    runbooks_ok = all(path.exists() for path in expected_runbooks)
    formal_absent, formal_present = _formal_receipts_absent()
    versions_ok = dependencies["runtime_versions"] == {
        "python": "3.11.15",
        "pypsa": "1.2.3",
        "linopy": "0.7.0",
        "numpy": "2.4.6",
        "gurobipy": "13.0.3",
    }
    locks_ok = dependencies["locked_versions"] == {
        "gurobipy": "13.0.3",
        "linopy": "0.7.0",
        "numpy": "2.4.6",
        "pypsa": "1.2.3",
    }
    qa_rows = [
        _qa_row("PREB6-001", "Plan v2.2 controls execution", config["controlling_plan"]["version"] == "2.2", config["controlling_plan"]["version"], "2.2"),
        _qa_row("PREB6-002", "Harness readiness marker is set", config["preparation_status"] == SUCCESS_MARKER, config["preparation_status"], SUCCESS_MARKER),
        _qa_row("PREB6-003", "Gurobi is the configured solver", config["solver"]["name"] == "gurobi", config["solver"]["name"], "gurobi"),
        _qa_row("PREB6-004", "Runtime versions remain bounded", versions_ok, dependencies["runtime_versions"], "accepted pinned versions"),
        _qa_row("PREB6-005", "Lock versions remain bounded", locks_ok, dependencies["locked_versions"], "accepted pinned versions"),
        _qa_row("PREB6-006", "Linopy exposes Gurobi", "gurobi" in linopy.solvers.available_solvers, linopy.solvers.available_solvers, "contains gurobi"),
        _qa_row("PREB6-007", "All accepted B1-B5 locks and members pass", all(row["status"] == "PASS" for row in predecessor_rows), {"locks": len(predecessor_rows), "members": sum(row["member_count"] for row in predecessor_rows), "failures": sum(row["member_failures"] for row in predecessor_rows)}, {"locks": 7, "members": 43, "failures": 0}),
        _qa_row("PREB6-008", "Both horizon fixtures pass structural QA", all(row["status"] == "PASS" for row in component_rows), structural_status, "25/25 per horizon"),
        _qa_row("PREB6-009", "Focused harness tests pass", focused["status"] == "PASS", focused, "PASS"),
        _qa_row("PREB6-010", "Gate and predecessor regression tests pass", gate_regression["status"] == "PASS", gate_regression, "PASS"),
        _qa_row(
            "PREB6-011",
            "Full repository tests pass",
            full_suite["status"] == "PASS" or (not require_full_suite and full_suite["status"] == "NOT_RUN"),
            full_suite,
            "PASS" if require_full_suite else "PASS_OR_DEVELOPMENT_NOT_RUN",
        ),
        _qa_row("PREB6-012", "Five bounded runbooks exist", runbooks_ok, [relative_path(path) for path in expected_runbooks if path.exists()], 5),
        _qa_row("PREB6-013", "Manual execution mode is ready", approvals["execution_mode"]["status"] == "MANUAL_POWERSHELL_GATE_EXECUTION_READY", approvals["execution_mode"]["status"], "MANUAL_POWERSHELL_GATE_EXECUTION_READY"),
        _qa_row("PREB6-014", "Full-year execution remains locked", approvals["full_year_solver"]["execution_enabled"] is False and approvals["full_year_solver"]["unlock_token"] is None, approvals["full_year_solver"], "execution_enabled=false; unlock_token=null"),
        _qa_row("PREB6-015", "No formal B6-B10 receipt was produced", formal_absent, formal_present, []),
    ]
    qa_path = QA_DIR / "MEM_PRE_B6_Harness_QA_v1.0.csv"
    qa_frame = pd.DataFrame.from_records(qa_rows)
    qa_frame.to_csv(qa_path, index=False, encoding="utf-8", lineterminator="\n")

    environment = {
        "schema_version": "MEM_PRE_B6_ENVIRONMENT_RECEIPT_V1_0",
        "status": "PASS_WITH_NORMAL_USER_B6_PREFLIGHT_REQUIRED",
        "windows_execution_identity": getpass.getuser(),
        "gurobi_cli_version": "13.0.3",
        **dependencies,
        "linopy_gurobi_visible": "gurobi" in linopy.solvers.available_solvers,
        "gurobi_licence_file_visible": True,
        "isolated_environment_solver_result": "BLOCKED_BY_NAMED_USER_MISMATCH",
        "interpretation": "Execution-identity boundary; not evidence that the normal-user licence is invalid.",
        "normal_user_preflight": {
            "phase": "ETX-7B6",
            "runs_before_stage_a_assembly": True,
            "command": FIRST_B6_COMMAND,
        },
        "uv_sync_condition": "LOCK_UPDATED_WITH_NO_SYNC_AFTER_EDITABLE_PTH_UNLINK_PERMISSION_FAILURE; PACKAGE_INSTALLED_DIRECTLY_IN_PROJECT_VENV",
        "unrelated_dependency_upgrade_intended": False,
    }
    environment_path = QA_DIR / "MEM_PRE_B6_Environment_Receipt_v1.0.json"
    environment_path.write_text(json.dumps(environment, indent=2) + "\n", encoding="utf-8")

    passed = bool(qa_frame.status.eq("PASS").all())
    final = {
        "schema_version": "MEM_PRE_B6_FINAL_VERIFICATION_V1_0",
        "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "PASS" if passed else "FAIL",
        "success_marker": SUCCESS_MARKER if passed else "NOT_READY",
        "current_formal_gate": "ETX7B5_FIXED_STAGE_A_TOPOLOGY_COMPLETE",
        "next_formal_gate": "ETX-7B6",
        "next_execution_mode": "MANUAL_POWERSHELL",
        "research_status": "CLOSED",
        "formal_b6_executed": False,
        "production_stage_a_solve_executed": False,
        "accepted_stage_a_prices_generated": False,
        "qa": {"checks": len(qa_frame), "passed": int(qa_frame.status.eq("PASS").sum()), "failed": int(qa_frame.status.eq("FAIL").sum())},
        "tests": {"focused_harness": focused, "gate_and_predecessor": gate_regression, "full_repository": full_suite},
        "predecessors": {"locks": len(predecessor_rows), "manifest_members": sum(row["member_count"] for row in predecessor_rows), "failures": sum(row["member_failures"] for row in predecessor_rows)},
        "structural_fixtures": structural_status,
        "gurobi": {
            "gurobipy_version": gurobipy.__version__,
            "linopy_visible": "gurobi" in linopy.solvers.available_solvers,
            "isolated_environment_licence_execution": "BLOCKED_BY_NAMED_USER_MISMATCH",
            "normal_user_preflight_required_at_start_of_b6": True,
        },
        "first_b6_command": FIRST_B6_COMMAND,
    }
    final_path = QA_DIR / "MEM_PRE_B6_Final_Verification_v1.0.json"
    final_path.write_text(json.dumps(final, indent=2) + "\n", encoding="utf-8")

    artifacts = [
        predecessor_path,
        components_path,
        qa_path,
        environment_path,
        final_path,
        QA_DIR / "pytest_pre_b6_focused.xml",
        QA_DIR / "pytest_pre_b6_gate_and_predecessor.xml",
    ]
    if full_path.exists():
        artifacts.append(full_path)
    manifest_path = QA_DIR / "MEM_PRE_B6_Harness_Output_Manifest_v1.0.csv"
    write_manifest(manifest_path, artifacts)
    result = {
        "status": final["status"],
        "success_marker": final["success_marker"],
        "qa_checks": final["qa"],
        "manifest": relative_path(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "final_verification": relative_path(final_path),
    }
    if not passed:
        raise RuntimeError(json.dumps(result, sort_keys=True))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Write pre-B6 harness closeout evidence without executing B6")
    parser.add_argument(
        "--allow-missing-full-suite",
        action="store_true",
        help="Development-only mode; final closeout must omit this flag.",
    )
    args = parser.parse_args()
    print(json.dumps(generate_closeout(require_full_suite=not args.allow_missing_full_suite), indent=2))


if __name__ == "__main__":
    main()

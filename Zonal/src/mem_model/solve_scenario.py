from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .common import ACCEPTED_RUNTIME, ACCEPTED_RUNTIME_2050, CONFIG, ROOT, load_yaml, sha256_file
from .network import build_network
from .reporting.canonical_results import generate_canonical_results
from .stage_b_2040_runtime import (
    MANIFEST_NAME,
    validate_runtime,
    verify_accepted_runtime_manifest,
)


CANONICAL_MODE = "FULL_YEAR_CANONICAL"
PRODUCTION_GATE = "stage_b_2040_production"
GATE_STATUS = "2040_BASE_ACCEPTED_SLOW_HIGH_READY_FOR_MANUAL_GUROBI_EXECUTION"
BASE_ACCEPTANCE = "results/MEM_2040_BASE_CANONICAL/MEM_STAGE_B_2040_BASE_ACCEPTANCE_v1.0.json"
BASE_NETWORK = "results/MEM_2040_BASE_CANONICAL/MEM_2040_BASE_8760h_SOLVED.nc"
BASE_REPORTING_MANIFEST = "results/MEM_2040_BASE_CANONICAL/MEM_CANONICAL_REPORTING_MANIFEST.csv"
BASE_REPORTING_RECEIPT = "results/MEM_2040_BASE_CANONICAL/MEM_CANONICAL_REPORTING_RECEIPT.json"


def verify_base_acceptance(gate: dict[str, Any]) -> dict[str, Any]:
    """Verify the accepted Base result and every chart artifact before successor runs."""

    if gate.get("accepted_base_receipt") != BASE_ACCEPTANCE:
        raise RuntimeError("Accepted Base receipt pointer is inconsistent")
    receipt_path = ROOT / BASE_ACCEPTANCE
    if not receipt_path.is_file():
        raise FileNotFoundError(f"Accepted Base receipt missing: {receipt_path}")
    receipt_hash = sha256_file(receipt_path)
    if receipt_hash.lower() != str(gate.get("accepted_base_receipt_sha256", "")).lower():
        raise RuntimeError("Accepted Base receipt hash differs from governance")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not (
        receipt.get("status") == "ACCEPTED_IMMUTABLE"
        and receipt.get("horizon") == 2040
        and receipt.get("scenario") == "Base"
        and receipt.get("mode") == CANONICAL_MODE
        and receipt.get("solver") == "gurobi"
        and receipt.get("solver_version") == "13.0.3"
        and receipt.get("solver_options")
        == {"Threads": 1, "Seed": 0, "include_objective_constant": False}
        and receipt.get("solver_status") == "ok"
        and receipt.get("termination_condition") == "optimal"
        and receipt.get("snapshots") == 8760
        and receipt.get("electrical_balance_status") == "PASS"
        and receipt.get("national_load_shedding_TWh") == 0.0
        and receipt.get("base_rerun_authorized") is False
    ):
        raise RuntimeError("Accepted Base receipt semantics are inconsistent")
    if receipt.get("solved_network") != BASE_NETWORK:
        raise RuntimeError("Accepted Base solved-network pointer is inconsistent")
    solved_path = ROOT / BASE_NETWORK
    if not solved_path.is_file():
        raise FileNotFoundError(f"Accepted Base solved network missing: {solved_path}")
    expected_solved_hash = str(gate.get("accepted_base_solved_network_sha256", ""))
    if (
        sha256_file(solved_path).lower() != expected_solved_hash.lower()
        or str(receipt.get("solved_network_sha256", "")).lower() != expected_solved_hash.lower()
    ):
        raise RuntimeError("Accepted Base solved-network hash differs from governance")
    if receipt.get("accepted_runtime_manifest_sha256", "").lower() != str(
        gate.get("required_runtime_manifest_sha256", "")
    ).lower():
        raise RuntimeError("Accepted Base Runtime-1 reference differs from governance")
    if receipt.get("reporting_manifest") != BASE_REPORTING_MANIFEST or receipt.get("reporting_receipt") != BASE_REPORTING_RECEIPT:
        raise RuntimeError("Accepted Base reporting pointers are inconsistent")
    reporting_manifest = ROOT / BASE_REPORTING_MANIFEST
    reporting_receipt = ROOT / BASE_REPORTING_RECEIPT
    if not reporting_manifest.is_file() or not reporting_receipt.is_file():
        raise FileNotFoundError("Accepted Base reporting package is incomplete")
    if sha256_file(reporting_receipt).lower() != str(receipt.get("reporting_receipt_sha256", "")).lower():
        raise RuntimeError("Accepted Base reporting receipt hash differs from acceptance")
    expected_reporting_hash = str(gate.get("accepted_base_reporting_manifest_sha256", ""))
    if (
        sha256_file(reporting_manifest).lower() != expected_reporting_hash.lower()
        or str(receipt.get("reporting_manifest_sha256", "")).lower() != expected_reporting_hash.lower()
    ):
        raise RuntimeError("Accepted Base reporting manifest hash differs from governance")
    reporting = json.loads(reporting_receipt.read_text(encoding="utf-8"))
    if not (
        reporting.get("status") == "PASS"
        and reporting.get("horizon") == 2040
        and reporting.get("scenario") == "Base"
        and reporting.get("snapshots") == 8760
        and reporting.get("reporting_solver_invocations") == 0
        and reporting.get("electrical_balance_status") == "PASS"
        and str(reporting.get("manifest_sha256", "")).lower() == expected_reporting_hash.lower()
    ):
        raise RuntimeError("Accepted Base reporting receipt is inconsistent")
    manifest = pd.read_csv(reporting_manifest, dtype=str)
    if manifest.empty or manifest["artifact"].duplicated().any():
        raise RuntimeError("Accepted Base reporting manifest is empty or duplicated")
    run_dir = (ROOT / "results" / "MEM_2040_BASE_CANONICAL").resolve()
    for row in manifest.itertuples(index=False):
        member = (run_dir / str(row.artifact)).resolve()
        if not member.is_relative_to(run_dir) or not member.is_file():
            raise RuntimeError(f"Accepted Base reporting member is missing or outside run: {row.artifact}")
        if sha256_file(member).lower() != str(row.sha256).lower():
            raise RuntimeError(f"Accepted Base reporting member hash differs: {row.artifact}")
    return receipt


def authorize_production_request(
    year: int,
    scenario: str,
    *,
    execute_full_year: bool,
    mode: str = CANONICAL_MODE,
    runtime_dir: Path = ACCEPTED_RUNTIME,
    gates_path: Path | None = None,
) -> dict[str, Any]:
    """Authorize only a scenario explicitly unlocked by its horizon gate."""

    if int(year) == 2050:
        return authorize_2050_production_request(
            scenario,
            execute_full_year=execute_full_year,
            mode=mode,
            runtime_dir=ACCEPTED_RUNTIME_2050 if runtime_dir == ACCEPTED_RUNTIME else runtime_dir,
            gates_path=gates_path,
        )

    if not execute_full_year:
        raise SystemExit("STAGE_B_PRODUCTION_SOLVE_REFUSED: --execute-full-year is required.")

    gates = load_yaml(gates_path or (CONFIG / "approval_gates.yaml"))
    gate = gates.get(PRODUCTION_GATE)
    if not isinstance(gate, dict):
        raise SystemExit(f"STAGE_B_PRODUCTION_SOLVE_REFUSED: missing {PRODUCTION_GATE} governance.")
    if gate.get("status") == "STAGE_B_2040_THREE_SCENARIO_PRODUCTION_COMPLETE":
        if not (
            gate.get("execution_enabled") is False
            and gate.get("scenario_authorization") == {"Slow": False, "Base": False, "High": False}
            and gate.get("accepted_scenarios") == ["Slow", "Base", "High"]
            and gate.get("authorized_commands") == {}
        ):
            raise SystemExit("STAGE_B_PRODUCTION_SOLVE_REFUSED: completed 2040 governance is inconsistent.")
        raise SystemExit("STAGE_B_PRODUCTION_SOLVE_REFUSED: 2040 three-scenario production complete; rerun not authorized.")
    if gate.get("status") != GATE_STATUS:
        raise SystemExit("STAGE_B_PRODUCTION_SOLVE_REFUSED: the 2040 Slow/High gate is not ready.")
    if gate.get("execution_enabled") is not True:
        raise SystemExit("STAGE_B_PRODUCTION_SOLVE_REFUSED: the 2040 Slow/High gate is disabled.")
    if gate.get("required_b10_gate") != "ETX7B10_2040_STAGE_A_TO_STAGE_B_PRICE_TRANSFER_COMPLETE":
        raise SystemExit("STAGE_B_PRODUCTION_SOLVE_REFUSED: B10-2040 completion authority is inconsistent.")
    if gate.get("required_runtime_gate") != "STAGE_B_2040_RUNTIME_1_PASS":
        raise SystemExit("STAGE_B_PRODUCTION_SOLVE_REFUSED: Runtime-1 authority is inconsistent.")
    if gate.get("required_runtime_manifest") != (
        "runtime_inputs/accepted/MEM_STAGE_B_2040_ACCEPTED_RUNTIME_MANIFEST_v1.0.csv"
    ):
        raise SystemExit("STAGE_B_PRODUCTION_SOLVE_REFUSED: accepted runtime manifest pointer is inconsistent.")
    if gate.get("chronology") != "C2019_PREFERRED_NEWER_2019_UTC_8760":
        raise SystemExit("STAGE_B_PRODUCTION_SOLVE_REFUSED: accepted chronology authority is inconsistent.")
    solver = gate.get("solver", {})
    if not (
        solver.get("name") == "gurobi"
        and solver.get("required_gurobipy_version") == "13.0.3"
        and solver.get("options") == {"Threads": 1, "Seed": 0}
        and solver.get("include_objective_constant") is False
    ):
        raise SystemExit("STAGE_B_PRODUCTION_SOLVE_REFUSED: canonical Gurobi configuration is inconsistent.")
    manual = gates.get("stage_a_manual_gates", {})
    if manual.get("b10_2040_status") != "ETX7B10_2040_STAGE_A_TO_STAGE_B_PRICE_TRANSFER_COMPLETE":
        raise SystemExit("STAGE_B_PRODUCTION_SOLVE_REFUSED: B10-2040 is not recorded complete.")
    if manual.get("stage_b_2050_authorized") is not False:
        raise SystemExit("STAGE_B_PRODUCTION_SOLVE_REFUSED: Stage-B 2050 must remain locked.")
    if not (
        manual.get("stage_b_2040_authorized_scenarios") == ["Slow", "High"]
        and manual.get("stage_b_2040_unauthorized_scenarios") == ["Base"]
        and manual.get("stage_b_2040_status") == GATE_STATUS
        and manual.get("stage_b_2040_base_status") == "ACCEPTED_CANONICAL_PRODUCTION_RUN_IMMUTABLE"
        and manual.get("stage_b_2040_base_acceptance") == BASE_ACCEPTANCE
    ):
        raise SystemExit("STAGE_B_PRODUCTION_SOLVE_REFUSED: shared 2040 governance is inconsistent.")

    if gate.get("scenario_authorization") != {"Slow": True, "Base": False, "High": True}:
        raise SystemExit("STAGE_B_PRODUCTION_SOLVE_REFUSED: scenario authorization map is inconsistent.")
    if gate.get("accepted_scenarios") != ["Base"] or gate.get("base_production_acceptance") != "ACCEPTED_IMMUTABLE":
        raise SystemExit("STAGE_B_PRODUCTION_SOLVE_REFUSED: Base acceptance governance is inconsistent.")
    if gate.get("authorized_commands") != {
        "Slow": r".\.venv\Scripts\python.exe -m mem_model.solve_scenario --year 2040 --scenario Slow --execute-full-year",
        "High": r".\.venv\Scripts\python.exe -m mem_model.solve_scenario --year 2040 --scenario High --execute-full-year",
    }:
        raise SystemExit("STAGE_B_PRODUCTION_SOLVE_REFUSED: authorized command map is inconsistent.")
    if int(year) != 2040 or str(scenario) not in ("Slow", "High") or str(mode) != CANONICAL_MODE:
        raise SystemExit("STAGE_B_PRODUCTION_SOLVE_REFUSED: only 2040 Slow/High FULL_YEAR_CANONICAL is authorized.")
    if gate.get("stage_b_2050_authorized") is not False:
        raise SystemExit("STAGE_B_PRODUCTION_SOLVE_REFUSED: Stage-B 2050 must remain locked.")
    if gate.get("production_optimization_executed") is not True:
        raise SystemExit("STAGE_B_PRODUCTION_SOLVE_REFUSED: accepted Base production execution is not recorded.")
    verify_base_acceptance(gate)
    run_directory = ROOT / "results" / f"MEM_{int(year)}_{str(scenario).upper()}_CANONICAL"
    if (run_directory / f"MEM_{int(year)}_{str(scenario).upper()}_8760h_SOLVED.nc").exists() or (
        run_directory / "MEM_CANONICAL_REPORTING_RECEIPT.json"
    ).exists():
        raise SystemExit(f"STAGE_B_PRODUCTION_SOLVE_REFUSED: canonical {scenario} output already exists.")

    manifest_path = runtime_dir / MANIFEST_NAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"Accepted runtime manifest missing: {manifest_path}")
    expected_manifest_hash = str(gate.get("required_runtime_manifest_sha256", ""))
    observed_manifest_hash = sha256_file(manifest_path)
    if not expected_manifest_hash or observed_manifest_hash.lower() != expected_manifest_hash.lower():
        raise RuntimeError("Accepted runtime manifest hash differs from the authorized 2040 gate")

    verification = verify_accepted_runtime_manifest(runtime_dir, expected_year=int(year))
    runtime_qa = validate_runtime(runtime_dir)
    failures = runtime_qa.loc[runtime_qa["status"].astype(str).ne("PASS")]
    if not failures.empty:
        failed_ids = ", ".join(failures["check_id"].astype(str).tolist())
        raise RuntimeError(f"Accepted runtime semantic/chronology QA failed: {failed_ids}")
    return {
        "status": "AUTHORIZED",
        "year": int(year),
        "scenario": str(scenario),
        "mode": str(mode),
        "runtime_manifest_sha256": observed_manifest_hash,
        "runtime_member_hashes": verification["members"],
        "runtime_qa_checks": int(len(runtime_qa)),
        "base_acceptance_sha256": str(gate["accepted_base_receipt_sha256"]),
    }


def authorize_2050_production_request(
    scenario: str,
    *,
    execute_full_year: bool,
    mode: str = CANONICAL_MODE,
    runtime_dir: Path = ACCEPTED_RUNTIME_2050,
    gates_path: Path | None = None,
) -> dict[str, Any]:
    """Future manual 2050 entrypoint; remains locked pending explicit review."""

    if not execute_full_year or mode != CANONICAL_MODE or scenario not in ("Slow", "Base", "High"):
        raise SystemExit("STAGE_B_2050_PRODUCTION_SOLVE_REFUSED: invalid mode or scenario")
    gates = load_yaml(gates_path or (CONFIG / "approval_gates.yaml"))
    gate = gates.get("stage_b_2050_production", {})
    if not isinstance(gate, dict) or gate.get("execution_enabled") is not True:
        raise SystemExit("STAGE_B_2050_PRODUCTION_SOLVE_REFUSED: pending validation of historical inputs")
    if gate.get("status") != "2050_SLOW_BASE_HIGH_AUTHORIZED_AFTER_SOL_USER_REVIEW":
        raise SystemExit("STAGE_B_2050_PRODUCTION_SOLVE_REFUSED: approval status invalid")
    if gate.get("scenario_authorization", {}).get(scenario) is not True:
        raise SystemExit("STAGE_B_2050_PRODUCTION_SOLVE_REFUSED: scenario not authorized")
    if gate.get("required_source") != "ETX-7B9H" or gate.get("required_b10_gate") != "ETX7B10_2050_STAGE_A_TO_STAGE_B_PRICE_TRANSFER_COMPLETE":
        raise SystemExit("STAGE_B_2050_PRODUCTION_SOLVE_REFUSED: source identity invalid")
    if runtime_dir.resolve() != ACCEPTED_RUNTIME_2050.resolve():
        raise SystemExit("STAGE_B_2050_PRODUCTION_SOLVE_REFUSED: runtime path invalid")
    from .stage_b_2050_runtime import MANIFEST_NAME as MANIFEST_2050, PREP_DIR, verify_b9h_immutable, verify_b10_2050, verify_prepared_runtime_manifest

    verify_b9h_immutable()
    b10 = verify_b10_2050()
    verified = verify_prepared_runtime_manifest(runtime_dir, expected_year=2050)
    if (
        verified["manifest_sha256"].lower() != str(gate.get("required_runtime_manifest_sha256", "")).lower()
        or b10["manifest_sha256"].lower() != str(gate.get("required_b10_manifest_sha256", "")).lower()
        or gate.get("required_runtime_manifest") != f"runtime_inputs/accepted_2050/{MANIFEST_2050}"
    ):
        raise SystemExit("STAGE_B_2050_PRODUCTION_SOLVE_REFUSED: immutable input identity mismatch")
    final = json.loads((PREP_DIR / "MEM_STAGE_B_2050_PREPARATION_FINAL_VERIFICATION_v1.0.json").read_text(encoding="utf-8"))
    receipt_path = PREP_DIR / f"MEM_STAGE_B_2050_{scenario.upper()}_Preparation_Receipt_v1.0.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not (
        final.get("status") == "PASS"
        and final.get("production_optimization_executed") is False
        and final.get("scenarios", {}).get(scenario) == "READY_NOT_EXECUTED"
        and receipt.get("status") == "READY_NOT_EXECUTED"
        and receipt.get("external_price_source") == "ETX-7B9H_VIA_B10_2050"
        and sha256_file(ROOT / receipt["unsolved_network"]) == receipt["unsolved_network_sha256"]
    ):
        raise SystemExit("STAGE_B_2050_PRODUCTION_SOLVE_REFUSED: preparation receipt invalid")
    solver = gate.get("solver", {})
    if not (
        solver.get("name") == "gurobi"
        and solver.get("required_gurobipy_version") == "13.0.3"
        and solver.get("options") == {"Threads": 1, "Seed": 0}
        and solver.get("include_objective_constant") is False
    ):
        raise SystemExit("STAGE_B_2050_PRODUCTION_SOLVE_REFUSED: solver contract invalid")
    run_directory = ROOT / "results" / f"MEM_2050_{scenario.upper()}_CANONICAL"
    if run_directory.exists():
        raise SystemExit("STAGE_B_2050_PRODUCTION_SOLVE_REFUSED: result path already exists")
    return {"status": "AUTHORIZED", "year": 2050, "scenario": scenario, "runtime_manifest_sha256": verified["manifest_sha256"]}


def _invoke_solver(network: Any, year: int = 2040) -> tuple[Any, Any]:
    """Single interception point used by pre-solve command-path tests."""

    gate_name = PRODUCTION_GATE if year == 2040 else "stage_b_2050_production"
    gate = load_yaml(CONFIG / "approval_gates.yaml")[gate_name]
    solver = gate.get("solver", {})
    if not (
        solver.get("name") == "gurobi"
        and solver.get("required_gurobipy_version") == "13.0.3"
        and solver.get("options") == {"Threads": 1, "Seed": 0}
        and solver.get("include_objective_constant") is False
    ):
        raise RuntimeError("STAGE_B_PRODUCTION_SOLVE_REFUSED: canonical Gurobi contract changed before invocation")
    try:
        import gurobipy as gp
    except ImportError as exc:
        raise RuntimeError("STAGE_B_PRODUCTION_SOLVE_REFUSED: gurobipy is not installed") from exc
    observed_version = ".".join(str(value) for value in gp.gurobi.version())
    if observed_version != str(solver.get("required_gurobipy_version")):
        raise RuntimeError(
            "STAGE_B_PRODUCTION_SOLVE_REFUSED: "
            f"gurobipy version {observed_version} does not match {solver.get('required_gurobipy_version')}"
        )
    return network.optimize(
        solver_name="gurobi",
        solver_options=solver.get("options", {}),
        include_objective_constant=bool(solver.get("include_objective_constant", False)),
        log_to_console=bool(solver.get("log_to_console", True)),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Guarded full-year canonical MEM production solve entry point")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--scenario", required=True, choices=["Slow", "Base", "High"])
    parser.add_argument("--execute-full-year", action="store_true")
    args = parser.parse_args(argv)

    authorize_production_request(
        args.year,
        args.scenario,
        execute_full_year=args.execute_full_year,
    )
    runtime_dir = ACCEPTED_RUNTIME if args.year == 2040 else ACCEPTED_RUNTIME_2050
    network, _ = build_network(runtime_dir.resolve(), args.year, args.scenario)
    status, condition = _invoke_solver(network) if args.year == 2040 else _invoke_solver(network, args.year)
    if str(status).lower() != "ok" or str(condition).lower() != "optimal":
        raise RuntimeError(f"Stage-B production solve did not terminate optimally: {status}/{condition}")
    run_directory = ROOT / "results" / f"MEM_{args.year}_{args.scenario.upper()}_CANONICAL"
    solved_path = run_directory / f"MEM_{args.year}_{args.scenario.upper()}_8760h_SOLVED.nc"
    if solved_path.exists() or (run_directory / "MEM_CANONICAL_REPORTING_RECEIPT.json").exists():
        raise RuntimeError("Canonical output appeared during solve; refusing to overwrite it")
    run_directory.mkdir(parents=True, exist_ok=True)
    network.export_to_netcdf(solved_path)
    generate_canonical_results(network, args.year, args.scenario, run_directory)


if __name__ == "__main__":
    main()

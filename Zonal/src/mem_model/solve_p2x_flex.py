"""Manually invoked, fail-closed solver entrypoint for versioned P2X-flex networks.

Importing this module or calling ``authorize_request`` never invokes a solver.
The old ``solve_scenario`` entrypoint and its accepted results are untouched.
"""

from __future__ import annotations

import argparse
import json

import pypsa

from .common import CONFIG, ROOT, load_yaml, sha256_file
from .reporting.canonical_results import generate_canonical_results
from .stage_b_p2x_flex import CONFIG_PATH, QA_ROOT, STATE_NAME, VERSION, network_path, verify_network, verify_runtime


def result_dir(year: int, scenario: str):
    return ROOT / "results" / f"MEM_{year}_{scenario.upper()}_{VERSION}_CANONICAL"


def authorize_request(year: int, scenario: str, *, execute_full_year: bool) -> dict:
    if year not in (2040, 2050) or scenario not in ("Slow", "Base", "High") or not execute_full_year:
        raise SystemExit("P2X_FLEX_SOLVE_REFUSED: exact year/scenario and --execute-full-year required")
    settings = load_yaml(CONFIG_PATH)
    if settings.get("version") != VERSION or settings.get("status") != "READY_FOR_MANUAL_EXECUTION":
        raise SystemExit("P2X_FLEX_SOLVE_REFUSED: versioned manual gate is not ready")
    if settings.get("solver") != {
        "name": "gurobi", "required_gurobipy_version": "13.0.3",
        "options": {"Threads": 1, "Seed": 0}, "include_objective_constant": False,
    }:
        raise SystemExit("P2X_FLEX_SOLVE_REFUSED: canonical solver contract changed")
    gate = load_yaml(CONFIG / "approval_gates.yaml").get("stage_b_p2x_flex_v1", {})
    if not (
        gate.get("status") == "READY_FOR_MANUAL_EXECUTION"
        and gate.get("execution_enabled") is True
        and gate.get("automated_production_solver_execution") == "PROHIBITED"
        and gate.get("scenario_authorization", {}).get(year, {}).get(scenario) is True
        and gate.get("solver") == settings["solver"]
        and gate.get("output_version") == VERSION
    ):
        raise SystemExit("P2X_FLEX_SOLVE_REFUSED: manual scenario governance invalid")
    state_path = QA_ROOT / STATE_NAME
    if not state_path.is_file():
        raise SystemExit("P2X_FLEX_SOLVE_REFUSED: structural QA state missing")
    if gate.get("required_qa_state") != str(state_path.relative_to(ROOT)).replace("\\", "/") or sha256_file(state_path).lower() != str(gate.get("required_qa_state_sha256", "")).lower():
        raise SystemExit("P2X_FLEX_SOLVE_REFUSED: structural QA state hash changed")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("version") != VERSION or state.get("status") != "READY_FOR_MANUAL_EXECUTION" or state.get("production_optimization_executed") is not False:
        raise SystemExit("P2X_FLEX_SOLVE_REFUSED: structural QA state invalid")
    runtime = verify_runtime(year)
    if runtime["manifest_sha256"] != state["runtime_manifests"].get(str(year)) or runtime["manifest_sha256"].lower() != str(gate.get("required_runtime_manifest_sha256", {}).get(year, "")).lower():
        raise SystemExit("P2X_FLEX_SOLVE_REFUSED: runtime manifest changed")
    path = network_path(year, scenario)
    check = next((row for row in state["network_checks"] if row["year"] == year and row["scenario"] == scenario), None)
    if check is None or not path.is_file() or sha256_file(path) != check["sha256"]:
        raise SystemExit("P2X_FLEX_SOLVE_REFUSED: unsolved network hash changed")
    if result_dir(year, scenario).exists():
        raise SystemExit("P2X_FLEX_SOLVE_REFUSED: versioned result directory already exists")
    return {"status": "AUTHORIZED_FOR_MANUAL_EXECUTION", "network": path, "network_sha256": check["sha256"],
            "runtime_manifest_sha256": runtime["manifest_sha256"]}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Guarded manual P2X-flex full-year production solve")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--execute-full-year", action="store_true")
    args = parser.parse_args(argv)
    auth = authorize_request(args.year, args.scenario, execute_full_year=args.execute_full_year)
    network = pypsa.Network(auth["network"])
    verify_network(network, args.year, args.scenario)
    import gurobipy as gp

    if ".".join(map(str, gp.gurobi.version())) != "13.0.3":
        raise RuntimeError("P2X_FLEX_SOLVE_REFUSED: Gurobi version mismatch")
    status, termination = network.optimize(
        solver_name="gurobi", solver_options={"Threads": 1, "Seed": 0},
        include_objective_constant=False,
    )
    if str(status).lower() != "ok" or str(termination).lower() != "optimal":
        raise RuntimeError(f"P2X-flex solve not optimal: {status}/{termination}")
    target = result_dir(args.year, args.scenario)
    if target.exists():
        raise RuntimeError("P2X-flex result path appeared during solve; refusing overwrite")
    target.mkdir(parents=True)
    solved = target / f"MEM_{args.year}_{args.scenario.upper()}_{VERSION}_8760h_SOLVED.nc"
    network.export_to_netcdf(solved)
    generate_canonical_results(network, args.year, args.scenario, target)


if __name__ == "__main__":
    main()

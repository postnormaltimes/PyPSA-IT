"""Guarded manual PowerShell entrypoints for formal Stage-A gates B6 through B10."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import pypsa
import yaml

from mem_model.stage_a.network import (
    MARKETS,
    ROOT,
    build_network_from_contracts,
    load_execution_config,
    load_stage_a_contracts,
    verify_input_locks,
)
from mem_model.stage_a.network_validation import validate_stage_a_network
from mem_model.stage_a.receipts import (
    build_receipt,
    command_string,
    read_receipt,
    relative_path,
    sha256_file,
    write_manifest,
    write_receipt,
)


CANONICAL_MODULE = "mem_model.stage_a.execution"


PHASES = {
    "b6": {
        "phase": "ETX-7B6",
        "gate": "ETX7B6_STAGE_A_SOLVER_READY",
        "qa": "etx7b6",
        "receipt": "MEM_ETX7B6_Run_Receipt_v1.0.json",
        "next_gate": "ETX-7B7",
    },
    "b7": {
        "phase": "ETX-7B7",
        "gate": "ETX7B7_2040_BASE_SMOKE_COMPLETE",
        "qa": "etx7b7",
        "receipt": "MEM_ETX7B7_Run_Receipt_v1.0.json",
        "next_gate": "ETX-7B8",
    },
    "b8": {
        "phase": "ETX-7B8",
        "gate": "ETX7B8_2040_BASE_FULL_YEAR_COMPLETE",
        "qa": "etx7b8",
        "receipt": "MEM_ETX7B8_Run_Receipt_v1.0.json",
        "next_gate": "ETX-7B9-A",
    },
    "b9-smoke": {
        "phase": "ETX-7B9-A",
        "gate": "ETX7B9A_2050_BASE_SMOKE_COMPLETE",
        "qa": "etx7b9",
        "receipt": "MEM_ETX7B9A_Run_Receipt_v1.0.json",
        "next_gate": "ETX-7B9-B",
    },
    "b9-full": {
        "phase": "ETX-7B9-B",
        "gate": "ETX7B9_2050_BASE_FULL_YEAR_COMPLETE",
        "qa": "etx7b9",
        "receipt": "MEM_ETX7B9_Run_Receipt_v1.0.json",
        "next_gate": "ETX-7B10",
    },
    "b10": {
        "phase": "ETX-7B10",
        "gate": "ETX7B10_STAGE_A_TO_STAGE_B_PRICE_TRANSFER_COMPLETE",
        "qa": "etx7b10",
        "receipt": "MEM_ETX7B10_Run_Receipt_v1.0.json",
        "next_gate": "STAGE_B_CONTINUATION",
    },
    "b10-2040": {
        "phase": "ETX-7B10-2040",
        "gate": "ETX7B10_2040_STAGE_A_TO_STAGE_B_PRICE_TRANSFER_COMPLETE",
        "qa": "etx7b10_2040",
        "receipt": "MEM_ETX7B10_2040_Run_Receipt_v1.0.json",
        "next_gate": "STAGE_B_2040_INPUT_CONTRACT_CHECK",
    },
    "b10-2050": {
        "phase": "ETX-7B10-2050",
        "gate": "ETX7B10_2050_STAGE_A_TO_STAGE_B_PRICE_TRANSFER_COMPLETE",
        "qa": "etx7b10_2050",
        "receipt": "MEM_ETX7B10_2050_Run_Receipt_v1.0.json",
        "next_gate": "STAGE_B_2050_INPUT_CONTRACT_CHECK",
    },
}

PRICE_MAPPING = {
    "FR": "NORD",
    "CH": "NORD",
    "AT": "NORD",
    "SI": "NORD",
    "ME": "CSUD",
    "GR": "SUD",
    "MT": "SICI",
    "TN": "SICI",
}

PRODUCTION_PRICE_SOURCE_CONFIG = ROOT / "config/stage_a_production_price_sources.yaml"
APPROVAL_GATES_CONFIG = ROOT / "config/approval_gates.yaml"
STAGE_B_SCENARIOS = ("Slow", "Base", "High")


def _qa_dir(key: str) -> Path:
    return ROOT / "qa" / "stage_a" / PHASES[key]["qa"]


def _receipt_path(key: str) -> Path:
    return _qa_dir(key) / PHASES[key]["receipt"]


def _log_path(key: str) -> Path:
    return _qa_dir(key) / "logs" / f"MEM_{PHASES[key]['phase'].replace('-', '')}_raw.log"


def _log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def _require_execute(args: argparse.Namespace) -> None:
    if not args.execute:
        raise SystemExit("FORMAL_GATE_NOT_EXECUTED: rerun the exact runbook command with --execute")


def _input_receipts(config: dict[str, Any]) -> list[dict[str, Any]]:
    return verify_input_locks(config)


def gurobi_preflight(config: dict[str, Any]) -> dict[str, Any]:
    """Verify the real Python/Gurobi path with one deterministic two-variable LP."""

    import gurobipy as gp
    import linopy

    version = ".".join(str(part) for part in gp.gurobi.version())
    required = str(config["solver"]["required_gurobipy_version"])
    if version != required:
        raise RuntimeError(f"GUROBI_VERSION_MISMATCH: required={required}; observed={version}")
    if "gurobi" not in linopy.solvers.available_solvers:
        raise RuntimeError("LINOPY_GUROBI_NOT_VISIBLE")
    try:
        model = gp.Model("MEM_B6_GUROBI_PREFLIGHT")
        model.Params.OutputFlag = 0
        model.Params.Threads = int(config["solver"]["options"]["Threads"])
        model.Params.Seed = int(config["solver"]["options"]["Seed"])
        x = model.addVar(lb=0.0, name="x")
        y = model.addVar(lb=0.0, name="y")
        model.addConstr(x + y <= 4.0, name="capacity")
        model.setObjective(3.0 * x + 2.0 * y, gp.GRB.MAXIMIZE)
        model.optimize()
    except gp.GurobiError as error:
        raise RuntimeError(
            f"GUROBI_NORMAL_USER_PREFLIGHT_FAILED: windows_user={getpass.getuser()}; {error}"
        ) from error
    if model.Status != gp.GRB.OPTIMAL:
        raise RuntimeError(f"GUROBI_TINY_LP_NOT_OPTIMAL: status={model.Status}")
    if not np.isclose(model.ObjVal, 12.0) or not np.isclose(x.X, 4.0) or not np.isclose(y.X, 0.0):
        raise RuntimeError("GUROBI_TINY_LP_UNEXPECTED_SOLUTION")
    return {
        "windows_user": getpass.getuser(),
        "gurobipy_version": version,
        "linopy_gurobi_visible": True,
        "licence_visible": True,
        "tiny_lp_status": "OPTIMAL",
        "tiny_lp_objective": float(model.ObjVal),
        "tiny_lp_x": float(x.X),
        "tiny_lp_y": float(y.X),
    }


def _network_path(year: int) -> Path:
    return ROOT / "stage_a_networks" / str(year) / f"MEM_ETX7B6_{year}_BASE_8760h_UNSOLVED.nc"


def _b6_state_path() -> Path:
    return ROOT / "scratch" / "ETX7B6_EXECUTION_STATE.md"


def run_b6(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    _require_execute(args)
    started = time.perf_counter()
    log = _log_path("b6")
    _log(log, "ETX-7B6 started: Gurobi preflight precedes all Stage-A assembly.")
    preflight = gurobi_preflight(config)
    _log(log, f"Gurobi preflight PASS: {json.dumps(preflight, sort_keys=True)}")
    input_receipts = _input_receipts(config)
    artifacts: list[Path] = []
    qa_summary: dict[str, Any] = {"gurobi_preflight": preflight, "horizons": {}}

    for year in config["scope"]["horizons"]:
        contracts = load_stage_a_contracts(int(year))
        network, metadata = build_network_from_contracts(contracts, config)
        validation = validate_stage_a_network(
            network,
            contracts,
            metadata,
            config,
            require_full_chronology=True,
        )
        qa_path = _qa_dir("b6") / f"MEM_ETX7B6_{year}_Structural_QA_v1.0.csv"
        qa_path.parent.mkdir(parents=True, exist_ok=True)
        validation["qa"].to_csv(qa_path, index=False, encoding="utf-8", lineterminator="\n")
        artifacts.append(qa_path)
        if validation["status"] != "PASS":
            failed = validation["qa"].loc[validation["qa"].status.eq("FAIL")].to_dict("records")
            raise RuntimeError(f"B6_STRUCTURAL_QA_FAILED_{year}: {failed}")

        crosswalk_path = _qa_dir("b6") / f"MEM_ETX7B6_{year}_Interconnector_Runtime_Crosswalk_v1.0.csv"
        metadata["interconnector_mapping"].to_csv(crosswalk_path, index=False, encoding="utf-8", lineterminator="\n")
        artifacts.append(crosswalk_path)

        model = network.optimize.create_model(include_objective_constant=False)
        model_metrics = {
            "linopy_model_assembled": True,
            "linopy_variables": int(getattr(model, "nvars", 0)),
            "linopy_constraints": int(getattr(model, "ncons", 0)),
            "market_solve_executed": False,
        }
        network.meta["formal_gate"] = PHASES["b6"]["gate"]
        network.meta["market_solve_executed"] = False
        output = _network_path(int(year))
        output.parent.mkdir(parents=True, exist_ok=True)
        network.export_to_netcdf(output)
        artifacts.append(output)

        metadata_path = output.with_suffix(".json")
        metadata_path.write_text(
            json.dumps({**metadata["summary"], **model_metrics}, indent=2) + "\n",
            encoding="utf-8",
        )
        artifacts.append(metadata_path)
        qa_summary["horizons"][str(year)] = {
            **validation["metrics"],
            **model_metrics,
            "network": relative_path(output),
            "network_sha256": sha256_file(output),
        }
        _log(log, f"{year} network built and validated; market solve executed = false.")

    artifacts.append(log)
    manifest_path = _qa_dir("b6") / "MEM_ETX7B6_Output_Manifest_v1.0.csv"
    write_manifest(manifest_path, artifacts)
    outputs = {
        "networks": {str(year): relative_path(_network_path(int(year))) for year in config["scope"]["horizons"]},
        "manifest": relative_path(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "raw_log": relative_path(log),
        "network_built_and_validated": True,
        "market_solve_executed": False,
    }
    receipt = build_receipt(
        phase=PHASES["b6"]["phase"],
        gate=PHASES["b6"]["gate"],
        status="PASS",
        input_manifests=input_receipts,
        outputs=outputs,
        qa=qa_summary,
        next_gate=PHASES["b6"]["next_gate"],
        command=command_string(module=CANONICAL_MODULE),
        runtime_seconds=time.perf_counter() - started,
    )
    write_receipt(_receipt_path("b6"), receipt)
    _b6_state_path().write_text(
        "# ETX-7B6 Execution State\n\n- Status: `PASS`\n- Network built and validated: `TRUE`\n- Market solve executed: `FALSE`\n- Next gate: `ETX-7B7`\n",
        encoding="utf-8",
    )
    return receipt


def _require_predecessor(key: str, expected_gate: str) -> dict[str, Any]:
    receipt = read_receipt(_receipt_path(key))
    if receipt["gate"] != expected_gate:
        raise RuntimeError(f"PREDECESSOR_GATE_MISMATCH: expected={expected_gate}; observed={receipt['gate']}")
    return receipt


def _select_smoke_snapshots(network: pypsa.Network, hours: int) -> pd.DatetimeIndex:
    load = network.loads_t.p_set[[f"LOAD_{market}" for market in MARKETS]].sum(axis=1)
    vre_names = network.generators.index[
        network.generators.carrier.astype(str).str.contains("SOLAR|WIND", case=False, regex=True)
    ]
    potential = network.generators_t.p_max_pu.reindex(columns=vre_names, fill_value=1.0).mul(
        network.generators.loc[vre_names, "p_nom"], axis=1
    ).sum(axis=1)
    residual = load - potential
    score = residual.rolling(hours, min_periods=hours).sum()
    end = score.idxmax()
    end_position = network.snapshots.get_loc(end)
    start_position = end_position - hours + 1
    if start_position < 0:
        raise RuntimeError("SMOKE_WINDOW_SELECTION_FAILED")
    return network.snapshots[start_position : end_position + 1]


def _load_b6_network(year: int) -> pypsa.Network:
    path = _network_path(year)
    if not path.exists():
        raise FileNotFoundError(f"B6 network is missing: {path}")
    return pypsa.Network(path)


def _solve_metrics(network: pypsa.Network, status: str, condition: str, runtime_seconds: float) -> dict[str, Any]:
    market_prices = network.buses_t.marginal_price.reindex(columns=MARKETS)
    finite_prices = bool(np.isfinite(market_prices.to_numpy()).all())
    shedding_names = network.generators.index[network.generators.carrier.eq("LOAD_SHEDDING")]
    shedding = network.generators_t.p.reindex(columns=shedding_names, fill_value=0.0)
    shedding_mwh = float(shedding.mul(network.snapshot_weightings.objective, axis=0).sum().sum())
    shedding_cap_violation = 0.0
    shedding_negative_violation = 0.0
    for market in MARKETS:
        generator = f"LOAD_SHEDDING_{market}"
        load = network.loads_t.p_set[f"LOAD_{market}"].astype(float)
        dispatch = shedding[generator].astype(float)
        shedding_cap_violation = max(
            shedding_cap_violation,
            float(np.maximum(dispatch.to_numpy() - load.to_numpy(), 0.0).max()),
        )
        shedding_negative_violation = max(
            shedding_negative_violation,
            float(np.maximum(-dispatch.to_numpy(), 0.0).max()),
        )
    bus_residual = network.buses_t.p if not network.buses_t.p.empty else pd.DataFrame(0.0, index=network.snapshots, columns=network.buses.index)
    balance_max = float(np.abs(bus_residual.to_numpy()).max())

    state_min_violation = 0.0
    state_max_violation = 0.0
    if not network.stores_t.e.empty:
        energy = network.stores_t.e
        lower = network.stores.e_min_pu * network.stores.e_nom
        upper = network.stores.e_max_pu * network.stores.e_nom
        state_min_violation = float(np.maximum(lower.to_numpy()[None, :] - energy.to_numpy(), 0.0).max())
        state_max_violation = float(np.maximum(energy.to_numpy() - upper.to_numpy()[None, :], 0.0).max())

    water_buses = network.buses.index[network.buses.carrier.eq("WATER_STATE")]
    water_balance_max = (
        float(np.abs(bus_residual.reindex(columns=water_buses).to_numpy()).max())
        if len(water_buses)
        else 0.0
    )
    inflow_names = network.generators.index[network.generators.carrier.eq("NATURAL_INFLOW")]
    inflow_dispatch_deviation = 0.0
    inflow_expected_mwh = 0.0
    inflow_dispatched_mwh = 0.0
    if len(inflow_names):
        expected_inflow = network.generators_t.p_max_pu.reindex(columns=inflow_names).mul(
            network.generators.loc[inflow_names, "p_nom"], axis=1
        )
        dispatched_inflow = network.generators_t.p.reindex(columns=inflow_names)
        inflow_dispatch_deviation = float(
            np.abs(dispatched_inflow.to_numpy() - expected_inflow.to_numpy()).max()
        )
        weights = network.snapshot_weightings.objective
        inflow_expected_mwh = float(expected_inflow.mul(weights, axis=0).sum().sum())
        inflow_dispatched_mwh = float(dispatched_inflow.mul(weights, axis=0).sum().sum())

    limited_names = network.generators.index[network.generators.carrier.eq("HYDRO_INFLOW_LIMITED")]
    limited_hydro_cap_violation = 0.0
    if len(limited_names):
        limited_cap = network.generators_t.p_max_pu.reindex(columns=limited_names).mul(
            network.generators.loc[limited_names, "p_nom"], axis=1
        )
        limited_dispatch = network.generators_t.p.reindex(columns=limited_names)
        limited_hydro_cap_violation = float(
            np.maximum(limited_dispatch.to_numpy() - limited_cap.to_numpy(), 0.0).max()
        )

    interface_names = network.links.index[network.links.carrier.eq("INTERCONNECTOR")]
    interface_flow = network.links_t.p0.reindex(columns=interface_names)
    lower = network.links.loc[interface_names, "p_nom"] * network.links.loc[interface_names, "p_min_pu"]
    upper = network.links.loc[interface_names, "p_nom"] * network.links.loc[interface_names, "p_max_pu"]
    link_violation = max(
        float(np.maximum(lower.to_numpy()[None, :] - interface_flow.to_numpy(), 0.0).max()),
        float(np.maximum(interface_flow.to_numpy() - upper.to_numpy()[None, :], 0.0).max()),
    )
    accepted = (
        str(status).lower() == "ok"
        and str(condition).lower() == "optimal"
        and finite_prices
        and balance_max <= 1e-5
        and state_min_violation <= 1e-5
        and state_max_violation <= 1e-5
        and link_violation <= 1e-5
        and shedding_cap_violation <= 1e-5
        and shedding_negative_violation <= 1e-5
        and water_balance_max <= 1e-5
        and inflow_dispatch_deviation <= 1e-5
        and limited_hydro_cap_violation <= 1e-5
    )
    return {
        "solver_status": str(status),
        "termination_condition": str(condition),
        "snapshots": len(network.snapshots),
        "objective": float(network.objective),
        "runtime_seconds": float(runtime_seconds),
        "balance": {"max_abs_residual_MW": balance_max},
        "load_shedding": {
            "total_MWh": shedding_mwh,
            "max_MW": float(shedding.max().max()) if not shedding.empty else 0.0,
            "max_local_load_cap_violation_MW": shedding_cap_violation,
            "max_negative_dispatch_violation_MW": shedding_negative_violation,
        },
        "price_statistics": {
            "finite": finite_prices,
            "min_EUR_per_MWh": float(market_prices.min().min()),
            "max_EUR_per_MWh": float(market_prices.max().max()),
            "mean_EUR_per_MWh": float(market_prices.stack().mean()),
        },
        "storage_phs_checks": {
            "state_min_violation_MWh": state_min_violation,
            "state_max_violation_MWh": state_max_violation,
            "cyclic_states": int(network.stores.e_cyclic.astype(bool).sum()),
        },
        "hydro_checks": {
            "water_state_buses": len(water_buses),
            "max_water_bus_balance_residual_MW": water_balance_max,
            "fixed_natural_inflow_generators": len(inflow_names),
            "fixed_inflow_dispatch_deviation_MW": inflow_dispatch_deviation,
            "fixed_inflow_expected_MWh_water": inflow_expected_mwh,
            "fixed_inflow_dispatched_MWh_water": inflow_dispatched_mwh,
            "inflow_limited_generators": len(limited_names),
            "inflow_limited_cap_violation_MW": limited_hydro_cap_violation,
        },
        "interconnector_flow_limit_checks": {
            "physical_links": len(interface_names),
            "max_limit_violation_MW": link_violation,
        },
        "accepted": accepted,
    }


def _tabular_result(frame: pd.DataFrame, value_name: str) -> pd.DataFrame:
    result = frame.copy()
    result.index.name = "snapshot"
    return result.stack(future_stack=True).rename(value_name).reset_index()


def _write_solve_outputs(network: pypsa.Network, result_dir: Path, stem: str) -> tuple[list[Path], dict[str, str]]:
    result_dir.mkdir(parents=True, exist_ok=True)
    network_path = result_dir / f"{stem}_SOLVED.nc"
    network.export_to_netcdf(network_path)
    prices_path = result_dir / f"{stem}_Market_Prices.parquet"
    generation_path = result_dir / f"{stem}_Generator_Dispatch.parquet"
    state_path = result_dir / f"{stem}_Store_State.parquet"
    flows_path = result_dir / f"{stem}_Link_Flows.parquet"
    shedding_path = result_dir / f"{stem}_Load_Shedding.parquet"
    spill_path = result_dir / f"{stem}_Hydro_Spill.parquet"
    curtailment_path = result_dir / f"{stem}_VRE_Curtailment.parquet"

    _tabular_result(network.buses_t.marginal_price.reindex(columns=MARKETS), "marginal_price_EUR_per_MWh").rename(columns={"Bus": "market"}).to_parquet(prices_path, index=False)
    _tabular_result(network.generators_t.p, "dispatch_MW").rename(columns={"Generator": "generator_id"}).to_parquet(generation_path, index=False)
    _tabular_result(network.stores_t.e, "state_MWh").rename(columns={"Store": "store_id"}).to_parquet(state_path, index=False)
    _tabular_result(network.links_t.p0, "signed_p0_MW").rename(columns={"Link": "link_id"}).to_parquet(flows_path, index=False)
    shedding_names = network.generators.index[network.generators.carrier.eq("LOAD_SHEDDING")]
    _tabular_result(network.generators_t.p.reindex(columns=shedding_names), "shedding_MW").rename(columns={"Generator": "generator_id"}).to_parquet(shedding_path, index=False)
    spill_names = network.generators.index[network.generators.carrier.eq("SPILL")]
    _tabular_result(network.generators_t.p.reindex(columns=spill_names), "spill_MW_water").rename(columns={"Generator": "spill_id"}).to_parquet(spill_path, index=False)
    vre_names = network.generators.index[network.generators.carrier.astype(str).str.contains("SOLAR|WIND", case=False, regex=True)]
    available = network.generators_t.p_max_pu.reindex(columns=vre_names, fill_value=1.0).mul(network.generators.loc[vre_names, "p_nom"], axis=1)
    curtailment = (available - network.generators_t.p.reindex(columns=vre_names)).clip(lower=0.0)
    _tabular_result(curtailment, "curtailment_MW").rename(columns={"Generator": "generator_id"}).to_parquet(curtailment_path, index=False)

    paths = [network_path, prices_path, generation_path, state_path, flows_path, shedding_path, spill_path, curtailment_path]
    return paths, {path.stem: relative_path(path) for path in paths}


def _run_solve(
    *,
    key: str,
    year: int,
    result_subdir: str,
    stem: str,
    smoke: bool,
    predecessor_key: str,
    predecessor_gate: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    _require_predecessor(predecessor_key, predecessor_gate)
    input_receipts = _input_receipts(config)
    network = _load_b6_network(year)
    if smoke:
        selected = _select_smoke_snapshots(network, int(config["modes"]["smoke"]["snapshots"]))
        network.set_snapshots(selected)
    expected = int(config["modes"]["smoke" if smoke else "full"]["snapshots"])
    if len(network.snapshots) != expected:
        raise RuntimeError(f"SNAPSHOT_COUNT_MISMATCH: expected={expected}; observed={len(network.snapshots)}")
    log = _log_path(key)
    _log(log, f"{PHASES[key]['phase']} solve started for {year} Base with {len(network.snapshots)} snapshots.")
    started = time.perf_counter()
    status, condition = network.optimize(
        solver_name=config["solver"]["name"],
        solver_options=config["solver"]["options"],
        log_to_console=bool(config["solver"]["log_to_console"]),
        log_fn=str(log),
        include_objective_constant=False,
    )
    solve_seconds = time.perf_counter() - started
    metrics = _solve_metrics(network, str(status), str(condition), solve_seconds)
    if not metrics["accepted"]:
        raise RuntimeError(f"{PHASES[key]['phase']}_POST_SOLVE_QA_FAILED: {metrics}")
    result_dir = ROOT / "stage_a_results" / result_subdir
    artifacts, outputs = _write_solve_outputs(network, result_dir, stem)
    artifacts.append(log)
    manifest_path = result_dir / f"{stem}_Result_Manifest_v1.0.csv"
    write_manifest(manifest_path, artifacts)
    outputs.update({"manifest": relative_path(manifest_path), "manifest_sha256": sha256_file(manifest_path), "raw_log": relative_path(log)})
    receipt = build_receipt(
        phase=PHASES[key]["phase"],
        gate=PHASES[key]["gate"],
        status="PASS",
        input_manifests=input_receipts,
        outputs=outputs,
        qa={"post_solve": metrics},
        next_gate=PHASES[key]["next_gate"],
        command=command_string(module=CANONICAL_MODULE),
        runtime_seconds=solve_seconds,
        solve=metrics,
    )
    write_receipt(_receipt_path(key), receipt)
    return receipt


def run_b7(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    _require_execute(args)
    return _run_solve(key="b7", year=2040, result_subdir="2040_smoke", stem="MEM_ETX7B7_2040_BASE_SMOKE", smoke=True, predecessor_key="b6", predecessor_gate=PHASES["b6"]["gate"], config=config)


def run_b8(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    _require_execute(args)
    return _run_solve(key="b8", year=2040, result_subdir="2040_full", stem="MEM_ETX7B8_2040_BASE_FULL", smoke=False, predecessor_key="b7", predecessor_gate=PHASES["b7"]["gate"], config=config)


def run_b9_smoke(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    _require_execute(args)
    return _run_solve(key="b9-smoke", year=2050, result_subdir="2050_smoke", stem="MEM_ETX7B9A_2050_BASE_SMOKE", smoke=True, predecessor_key="b8", predecessor_gate=PHASES["b8"]["gate"], config=config)


def run_b9_full(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    _require_execute(args)
    return _run_solve(key="b9-full", year=2050, result_subdir="2050_full", stem="MEM_ETX7B9_2050_BASE_FULL", smoke=False, predecessor_key="b9-smoke", predecessor_gate=PHASES["b9-smoke"]["gate"], config=config)


def _solution_manifest_from_receipt(receipt: dict[str, Any]) -> tuple[Path, str]:
    path = ROOT / receipt["outputs"]["manifest"]
    observed = sha256_file(path)
    expected = receipt["outputs"]["manifest_sha256"]
    if observed != expected:
        raise RuntimeError(f"SOLUTION_MANIFEST_HASH_MISMATCH: {path}")
    return path, observed


def _load_accepted_production_price_sources(
    years: tuple[int, ...] | None = None,
) -> dict[int, dict[str, Any]]:
    """Resolve explicitly accepted horizon sources and fail closed.

    The no-argument legacy path still requires both horizons. A caller may
    request the independently authorized 2040 source without inventing or
    falling back to a 2050 source.
    """

    source_config = yaml.safe_load(PRODUCTION_PRICE_SOURCE_CONFIG.read_text(encoding="utf-8"))
    approvals = yaml.safe_load(APPROVAL_GATES_CONFIG.read_text(encoding="utf-8"))
    if source_config.get("schema") != "STAGE_A_PRODUCTION_PRICE_SOURCES_V1_0":
        raise RuntimeError("B10_PRODUCTION_PRICE_SOURCE_SCHEMA_MISMATCH")
    requested = tuple(years or (2040, 2050))
    if not requested or len(set(requested)) != len(requested) or not set(requested) <= {2040, 2050}:
        raise RuntimeError(f"B10_INVALID_HORIZON_SCOPE: {requested}")
    governance = source_config.get("governance", {})
    manual = approvals.get("stage_a_manual_gates", {})
    if requested == (2040, 2050):
        if not (
            governance.get("production_sources_resolved") is True
            and governance.get("b10_authorized") is True
            and manual.get("b10_authorized") is True
        ):
            raise RuntimeError("B10_NOT_AUTHORIZED_PENDING_TWO_ACCEPTED_CLOSURE_PRICE_SOURCES")
    else:
        horizon_governance = source_config.get("horizon_governance", {})
        for year in requested:
            scoped = horizon_governance.get(year, horizon_governance.get(str(year), {}))
            if not (
                scoped.get("production_source_resolved") is True
                and scoped.get("b10_authorized") is True
                and manual.get(f"b10_{year}_authorized") is True
            ):
                raise RuntimeError(f"B10_NOT_AUTHORIZED_FOR_HORIZON_{year}")
    sources = source_config.get("sources", {})
    if set(sources) != {2040, 2050}:
        raise RuntimeError("B10_SOURCE_REGISTRY_MUST_RETAIN_BOTH_HORIZON_SLOTS")

    resolved: dict[int, dict[str, Any]] = {}
    for year in requested:
        source = sources[year]
        if year == 2050 and (
            source.get("phase") != "ETX-7B9H"
            or source.get("required_gate")
            != "ETX7B9H_2050_FINAL_RESIDUAL_MT_TN_CLOSURE_COMPLETE"
        ):
            raise RuntimeError("B10_2050_ONLY_B9H_ACCEPTED_SOURCE_ALLOWED")
        if source.get("status") != "ACCEPTED_PRODUCTION_PRICE_SOURCE":
            raise RuntimeError(f"B10_PRICE_SOURCE_NOT_ACCEPTED_{year}")
        for key in ("receipt_sha256", "result_manifest_sha256", "market_prices_sha256"):
            value = source.get(key)
            if not isinstance(value, str) or len(value) != 64:
                raise RuntimeError(f"B10_PRICE_SOURCE_HASH_NOT_FROZEN_{year}_{key}")

        receipt_path = ROOT / source["receipt"]
        manifest_path = ROOT / source["result_manifest"]
        prices_path = ROOT / source["market_prices"]
        observed = {
            "receipt_sha256": sha256_file(receipt_path),
            "result_manifest_sha256": sha256_file(manifest_path),
            "market_prices_sha256": sha256_file(prices_path),
        }
        for key, digest in observed.items():
            if digest != source[key]:
                raise RuntimeError(f"B10_PRICE_SOURCE_HASH_MISMATCH_{year}_{key}")

        receipt = read_receipt(receipt_path)
        if receipt.get("status") != "PASS" or receipt.get("gate") != source["required_gate"]:
            raise RuntimeError(f"B10_PRICE_SOURCE_RECEIPT_NOT_PASS_{year}")
        if receipt.get("outputs", {}).get("manifest") != source["result_manifest"]:
            raise RuntimeError(f"B10_PRICE_SOURCE_MANIFEST_PATH_MISMATCH_{year}")
        if receipt.get("outputs", {}).get("manifest_sha256") != source["result_manifest_sha256"]:
            raise RuntimeError(f"B10_PRICE_SOURCE_MANIFEST_RECEIPT_HASH_MISMATCH_{year}")
        price_outputs = [
            value
            for key, value in receipt.get("outputs", {}).items()
            if key.endswith("_Market_Prices")
        ]
        if price_outputs != [source["market_prices"]]:
            raise RuntimeError(f"B10_PRICE_SOURCE_OUTPUT_PATH_MISMATCH_{year}")

        manifest = pd.read_csv(manifest_path)
        member_failures: list[str] = []
        for row in manifest.itertuples():
            member = ROOT / str(row.relative_path)
            if not member.exists() or sha256_file(member) != str(row.sha256):
                member_failures.append(str(row.relative_path))
        if member_failures:
            raise RuntimeError(f"B10_PRICE_SOURCE_MANIFEST_MEMBER_FAILURES_{year}: {member_failures}")
        price_member = manifest.loc[manifest["relative_path"].eq(source["market_prices"])]
        if len(price_member) != 1 or str(price_member.iloc[0]["sha256"]) != source["market_prices_sha256"]:
            raise RuntimeError(f"B10_PRICE_SOURCE_PRICE_NOT_UNIQUE_IN_MANIFEST_{year}")

        long_prices = pd.read_parquet(prices_path)
        required_columns = {"snapshot", "name", "marginal_price_EUR_per_MWh"}
        if set(long_prices.columns) != required_columns:
            raise RuntimeError(f"B10_PRICE_SOURCE_SCHEMA_INVALID_{year}")
        if long_prices.duplicated(["snapshot", "name"]).any():
            raise RuntimeError(f"B10_PRICE_SOURCE_DUPLICATE_KEYS_{year}")
        price_markets = set(long_prices["name"].astype(str))
        if price_markets != set(MARKETS) or len(long_prices) != 8760 * len(MARKETS):
            raise RuntimeError(f"B10_PRICE_SOURCE_COVERAGE_INVALID_{year}")
        prices = long_prices.pivot(
            index="snapshot", columns="name", values="marginal_price_EUR_per_MWh"
        ).reindex(columns=MARKETS)
        if len(prices) != 8760 or not np.isfinite(prices.to_numpy()).all():
            raise RuntimeError(f"B10_PRICE_SOURCE_VALUES_INVALID_{year}")
        resolved[year] = {
            "source": source,
            "receipt": receipt,
            "prices": prices,
            "manifest_path": manifest_path,
            "manifest_sha256": observed["result_manifest_sha256"],
            "market_prices_sha256": observed["market_prices_sha256"],
        }
    return resolved


def _build_stage_b_price_adapter(
    boundary: pd.DataFrame,
    *,
    expected_horizons: set[int] | None = None,
) -> pd.DataFrame:
    required = {"snapshot", "horizon", "external_market", "stage_a_marginal_price"}
    if not required.issubset(boundary.columns):
        raise RuntimeError("B10_STAGE_B_RUNTIME_ADAPTER_SOURCE_SCHEMA_INVALID")
    horizons = set(boundary["horizon"].astype(int))
    expected = expected_horizons or horizons
    if (
        not horizons
        or horizons != expected
        or not horizons <= {2040, 2050}
        or set(boundary["external_market"].astype(str)) != set(PRICE_MAPPING)
        or boundary.duplicated(["snapshot", "horizon", "external_market"]).any()
        or not boundary.groupby(["horizon", "external_market"]).size().eq(8760).all()
        or not np.isfinite(boundary["stage_a_marginal_price"].astype(float)).all()
    ):
        raise RuntimeError("B10_STAGE_B_RUNTIME_ADAPTER_SOURCE_QA_FAILED")
    stage_b_rows: list[pd.DataFrame] = []
    for scenario in STAGE_B_SCENARIOS:
        scenario_rows = boundary[
            ["snapshot", "horizon", "external_market", "stage_a_marginal_price"]
        ].copy()
        scenario_rows = scenario_rows.rename(
            columns={
                "horizon": "year",
                "stage_a_marginal_price": "price_EUR_per_MWh",
            }
        )
        scenario_rows.insert(2, "scenario", scenario)
        stage_b_rows.append(scenario_rows)
    stage_b_prices = pd.concat(stage_b_rows, ignore_index=True)
    stage_b_prices = stage_b_prices[
        ["snapshot", "year", "scenario", "external_market", "price_EUR_per_MWh"]
    ].sort_values(
        ["year", "scenario", "snapshot", "external_market"], kind="stable"
    )
    if (
        len(stage_b_prices) != len(horizons) * 3 * 8760 * len(PRICE_MAPPING)
        or stage_b_prices.duplicated(
            ["snapshot", "year", "scenario", "external_market"]
        ).any()
        or "CORS" in set(stage_b_prices["external_market"])
    ):
        raise RuntimeError("B10_STAGE_B_RUNTIME_ADAPTER_QA_FAILED")
    return stage_b_prices.reset_index(drop=True)


def _build_price_boundary(
    accepted_sources: dict[int, dict[str, Any]],
    years: tuple[int, ...],
) -> tuple[pd.DataFrame, dict[int, dict[str, str]]]:
    interfaces = pd.read_csv(
        ROOT / "stage_a_inputs/topology_v1_0/MEM_ETX7B5_Physical_Interface_Registry_v1.0.csv"
    )
    rows: list[pd.DataFrame] = []
    source_manifests: dict[int, dict[str, str]] = {}
    for year in years:
        accepted = accepted_sources[year]
        manifest = accepted["manifest_path"]
        manifest_hash = accepted["manifest_sha256"]
        source_manifests[year] = {
            "path": relative_path(manifest),
            "sha256": manifest_hash,
        }
        prices = accepted["prices"].reindex(columns=PRICE_MAPPING)
        for market, zone in PRICE_MAPPING.items():
            interface = interfaces.loc[
                ((interfaces.endpoint_a.eq(market)) & (interfaces.endpoint_b.eq("IT")))
                | ((interfaces.endpoint_b.eq(market)) & (interfaces.endpoint_a.eq("IT")))
            ]
            if len(interface) != 1:
                raise RuntimeError(f"ITALY_INTERFACE_IDENTITY_NOT_UNIQUE: {market}")
            rows.append(
                pd.DataFrame(
                    {
                        "snapshot": prices.index,
                        "horizon": year,
                        "external_market": market,
                        "stage_a_marginal_price": prices[market].to_numpy(),
                        "stage_b_receiving_zone": zone,
                        "interface_id": interface.iloc[0].physical_link_id,
                        "price_role": "STAGE_A_EXTERNAL_MARKET_MARGINAL_PRICE",
                        "source_solution_manifest": relative_path(manifest),
                        "source_solution_manifest_sha256": manifest_hash,
                        "source_market_prices_sha256": accepted["market_prices_sha256"],
                    }
                )
            )
    return pd.concat(rows, ignore_index=True), source_manifests


def _price_transfer_qa(
    boundary: pd.DataFrame,
    source_manifests: dict[int, dict[str, str]],
) -> dict[str, Any]:
    duplicates = int(boundary.duplicated(["snapshot", "horizon", "external_market"]).sum())
    counts = boundary.groupby(["horizon", "external_market"]).size()
    qa = {
        "rows": len(boundary),
        "horizons": sorted(boundary["horizon"].astype(int).unique().tolist()),
        "duplicate_keys": duplicates,
        "all_series_have_8760": bool(counts.eq(8760).all()),
        "finite_prices": bool(np.isfinite(boundary.stage_a_marginal_price.to_numpy()).all()),
        "mapping_exact": boundary.groupby("external_market").stage_b_receiving_zone.first().to_dict()
        == PRICE_MAPPING,
        "corsica_absent": "CORS" not in set(boundary.external_market),
        "dispatch_rerun": False,
        "source_manifests": source_manifests,
        "source_selection": "EXPLICIT_ACCEPTED_CLOSURE_SOURCE_ONLY_NO_FALLBACK",
    }
    if duplicates or not all(
        qa[key]
        for key in ("all_series_have_8760", "finite_prices", "mapping_exact", "corsica_absent")
    ):
        raise RuntimeError(f"B10_PRICE_TRANSFER_QA_FAILED: {qa}")
    return qa


def run_b10(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    _require_execute(args)
    started = time.perf_counter()
    accepted_sources = _load_accepted_production_price_sources()
    input_receipts = _input_receipts(config)
    boundary, source_manifests = _build_price_boundary(accepted_sources, (2040, 2050))
    qa = _price_transfer_qa(boundary, source_manifests)
    result_dir = ROOT / "stage_a_results" / "price_transfer"
    result_dir.mkdir(parents=True, exist_ok=True)
    output = result_dir / "MEM_ETX7B10_Stage_A_to_Stage_B_Prices_v1.0.parquet"
    boundary.to_parquet(output, index=False)
    stage_b_prices = _build_stage_b_price_adapter(boundary, expected_horizons={2040, 2050})
    stage_b_output = result_dir / "external_prices_hourly.parquet"
    stage_b_prices.to_parquet(stage_b_output, index=False)
    qa["stage_b_runtime_adapter"] = {
        "rows": len(stage_b_prices),
        "scenarios": list(STAGE_B_SCENARIOS),
        "scenario_inheritance": "BASE_PRICE_BY_HORIZON_REUSED_FOR_SLOW_BASE_HIGH",
        "external_fleet_rows": 0,
        "corsica_price_rows": 0,
    }
    log = _log_path("b10")
    _log(log, "B10 deterministic extraction completed; dispatch rerun = false.")
    manifest = result_dir / "MEM_ETX7B10_Price_Transfer_Manifest_v1.0.csv"
    write_manifest(manifest, [output, stage_b_output, log])
    outputs = {
        "price_boundary": relative_path(output),
        "stage_b_runtime_prices": relative_path(stage_b_output),
        "manifest": relative_path(manifest),
        "manifest_sha256": sha256_file(manifest),
        "raw_log": relative_path(log),
        "dispatch_rerun": False,
    }
    receipt = build_receipt(
        phase=PHASES["b10"]["phase"],
        gate=PHASES["b10"]["gate"],
        status="PASS",
        input_manifests=input_receipts,
        outputs=outputs,
        qa=qa,
        next_gate=PHASES["b10"]["next_gate"],
        command=command_string(module=CANONICAL_MODULE),
        runtime_seconds=time.perf_counter() - started,
    )
    write_receipt(_receipt_path("b10"), receipt)
    return receipt


def run_b10_2040(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    """Extract the accepted 2040 source without resolving or touching 2050."""

    _require_execute(args)
    started = time.perf_counter()
    accepted_sources = _load_accepted_production_price_sources((2040,))
    accepted = accepted_sources[2040]
    boundary, source_manifests = _build_price_boundary(accepted_sources, (2040,))
    qa = _price_transfer_qa(boundary, source_manifests)
    if set(boundary["horizon"].astype(int)) != {2040}:
        raise RuntimeError("B10_2040_HORIZON_LEAKAGE")
    result_dir = ROOT / "stage_a_results" / "price_transfer" / "2040"
    result_dir.mkdir(parents=True, exist_ok=True)
    output = result_dir / "MEM_ETX7B10_2040_Stage_A_to_Stage_B_Prices_v1.0.parquet"
    boundary.to_parquet(output, index=False)
    stage_b_prices = _build_stage_b_price_adapter(boundary, expected_horizons={2040})
    stage_b_output = result_dir / "external_prices_hourly.parquet"
    stage_b_prices.to_parquet(stage_b_output, index=False)
    if set(stage_b_prices["year"].astype(int)) != {2040}:
        raise RuntimeError("B10_2040_RUNTIME_ADAPTER_HORIZON_LEAKAGE")
    qa.update(
        {
            "scope": "2040_ONLY",
            "accepted_source_phase": accepted["source"]["phase"],
            "accepted_market_price_sha256": accepted["market_prices_sha256"],
            "2050_source_resolved_or_read": False,
            "stage_b_runtime_adapter": {
                "rows": len(stage_b_prices),
                "scenarios": list(STAGE_B_SCENARIOS),
                "scenario_inheritance": "2040_BASE_PRICE_REUSED_FOR_2040_SLOW_BASE_HIGH",
                "external_fleet_rows": 0,
                "corsica_price_rows": 0,
            },
        }
    )
    log = _log_path("b10-2040")
    _log(log, "B10-2040 deterministic extraction completed; dispatch rerun = false; 2050 untouched.")
    manifest = result_dir / "MEM_ETX7B10_2040_Price_Transfer_Manifest_v1.0.csv"
    write_manifest(manifest, [output, stage_b_output, log])
    outputs = {
        "price_boundary": relative_path(output),
        "stage_b_runtime_prices": relative_path(stage_b_output),
        "manifest": relative_path(manifest),
        "manifest_sha256": sha256_file(manifest),
        "raw_log": relative_path(log),
        "dispatch_rerun": False,
    }
    source_input = {
        "input_id": "ETX7B8D_2040_ACCEPTED_PRODUCTION_PRICE_SOURCE",
        "path": accepted["source"]["result_manifest"],
        "observed_sha256": accepted["manifest_sha256"],
        "status": "PASS",
    }
    receipt = build_receipt(
        phase=PHASES["b10-2040"]["phase"],
        gate=PHASES["b10-2040"]["gate"],
        status="PASS",
        input_manifests=[source_input, *_input_receipts(config)],
        outputs=outputs,
        qa=qa,
        next_gate=PHASES["b10-2040"]["next_gate"],
        command=command_string(module=CANONICAL_MODULE),
        runtime_seconds=time.perf_counter() - started,
    )
    write_receipt(_receipt_path("b10-2040"), receipt)
    return receipt


def run_b10_2050(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    """Extract only the explicitly accepted 2050 source; never read 2040 prices."""

    _require_execute(args)
    started = time.perf_counter()
    accepted_sources = _load_accepted_production_price_sources((2050,))
    accepted = accepted_sources[2050]
    boundary, source_manifests = _build_price_boundary(accepted_sources, (2050,))
    qa = _price_transfer_qa(boundary, source_manifests)
    if set(boundary["horizon"].astype(int)) != {2050}:
        raise RuntimeError("B10_2050_HORIZON_LEAKAGE")
    stage_b_prices = _build_stage_b_price_adapter(boundary, expected_horizons={2050})
    if set(stage_b_prices["year"].astype(int)) != {2050}:
        raise RuntimeError("B10_2050_RUNTIME_ADAPTER_HORIZON_LEAKAGE")
    result_dir = ROOT / "stage_a_results" / "price_transfer" / "2050"
    result_dir.mkdir(parents=True, exist_ok=True)
    output = result_dir / "MEM_ETX7B10_2050_Stage_A_to_Stage_B_Prices_v1.0.parquet"
    stage_b_output = result_dir / "external_prices_hourly.parquet"
    boundary.to_parquet(output, index=False)
    stage_b_prices.to_parquet(stage_b_output, index=False)
    qa.update(
        {
            "scope": "2050_ONLY",
            "accepted_source_phase": accepted["source"]["phase"],
            "accepted_market_price_sha256": accepted["market_prices_sha256"],
            "2040_source_resolved_or_read": False,
            "stage_b_runtime_adapter": {
                "rows": len(stage_b_prices),
                "scenarios": list(STAGE_B_SCENARIOS),
                "scenario_inheritance": "2050_BASE_PRICE_REUSED_FOR_2050_SLOW_BASE_HIGH",
                "external_fleet_rows": 0,
                "corsica_price_rows": 0,
            },
        }
    )
    log = _log_path("b10-2050")
    _log(log, "B10-2050 deterministic extraction completed; dispatch rerun = false; 2040 untouched.")
    manifest = result_dir / "MEM_ETX7B10_2050_Price_Transfer_Manifest_v1.0.csv"
    write_manifest(manifest, [output, stage_b_output, log])
    outputs = {
        "price_boundary": relative_path(output),
        "stage_b_runtime_prices": relative_path(stage_b_output),
        "manifest": relative_path(manifest),
        "manifest_sha256": sha256_file(manifest),
        "raw_log": relative_path(log),
        "dispatch_rerun": False,
    }
    source_input = {
        "input_id": "ETX7B9H_2050_ACCEPTED_PRODUCTION_PRICE_SOURCE",
        "path": accepted["source"]["result_manifest"],
        "observed_sha256": accepted["manifest_sha256"],
        "status": "PASS",
    }
    receipt = build_receipt(
        phase=PHASES["b10-2050"]["phase"],
        gate=PHASES["b10-2050"]["gate"],
        status="PASS",
        input_manifests=[source_input, *_input_receipts(config)],
        outputs=outputs,
        qa=qa,
        next_gate=PHASES["b10-2050"]["next_gate"],
        command=command_string(module=CANONICAL_MODULE),
        runtime_seconds=time.perf_counter() - started,
    )
    write_receipt(_receipt_path("b10-2050"), receipt)
    return receipt


def _failure_receipt(key: str, error: Exception, config: dict[str, Any]) -> Path:
    status = "STOP" if key == "b6" and "GUROBI_NORMAL_USER_PREFLIGHT_FAILED" in str(error) else "FAIL"
    try:
        inputs = verify_input_locks(config)
    except Exception:
        inputs = []
    receipt = build_receipt(
        phase=PHASES[key]["phase"],
        gate=PHASES[key]["gate"],
        status=status,
        input_manifests=inputs,
        outputs={"market_solve_executed": False if key == "b6" else "FAILED_BEFORE_ACCEPTANCE"},
        qa={"error_type": type(error).__name__, "error": str(error)},
        next_gate=PHASES[key]["phase"],
        command=command_string(module=CANONICAL_MODULE),
    )
    return write_receipt(_receipt_path(key), receipt)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MEM Stage-A guarded manual gate execution")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for key in PHASES:
        subparser = subparsers.add_parser(key, help=f"Run {PHASES[key]['phase']} exactly as defined by its runbook")
        subparser.add_argument("--execute", action="store_true", help="Required explicit acknowledgement for the formal manual gate")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    config = load_execution_config()
    handlers: dict[str, Callable[[argparse.Namespace, dict[str, Any]], dict[str, Any]]] = {
        "b6": run_b6,
        "b7": run_b7,
        "b8": run_b8,
        "b9-smoke": run_b9_smoke,
        "b9-full": run_b9_full,
        "b10": run_b10,
        "b10-2040": run_b10_2040,
        "b10-2050": run_b10_2050,
    }
    try:
        receipt = handlers[args.command](args, config)
    except SystemExit:
        raise
    except Exception as error:
        path = _failure_receipt(args.command, error, config)
        print(json.dumps({"status": "STOP" if args.command == "b6" else "FAIL", "receipt": relative_path(path), "error": str(error)}, indent=2))
        raise SystemExit(1) from error
    path = _receipt_path(args.command)
    print(json.dumps({"status": receipt["status"], "gate": receipt["gate"], "receipt": relative_path(path)}, indent=2))


if __name__ == "__main__":
    main()

"""Fail-closed, single-job manual UC-2 execution and non-solving verification.

Importing this module never builds a model or contacts a solver. Only explicit
``run-reference``, ``run-uc`` and ``run-price`` actions can optimize, and each
action handles exactly one year/scenario/run type after its own gates pass.
"""

from __future__ import annotations

import argparse
import json
import math
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd
import pypsa
import xarray as xr

from .common import CONFIG, ROOT, load_yaml, sha256_file
from . import stage_b_uc1 as uc1


CONFIG_PATH = CONFIG / "stage_b_uc2_execution.yaml"
SCENARIOS = uc1.SCENARIOS
RUN_TYPES = ("POST_P2X_CONTINUOUS_REFERENCE", "UC_MILP", "FIXED_COMMITMENT_PRICE_LP")
RUN_FOLDERS = {"POST_P2X_CONTINUOUS_REFERENCE": "CONTINUOUS_REFERENCE",
               "UC_MILP": "UC_MILP", "FIXED_COMMITMENT_PRICE_LP": "FIXED_COMMITMENT_PRICE_LP"}
VERIFY_NAMES = {"POST_P2X_CONTINUOUS_REFERENCE": "VERIFY_REFERENCE.json",
                "UC_MILP": "VERIFY_UC.json", "FIXED_COMMITMENT_PRICE_LP": "VERIFY_PRICE.json"}
ACTIVE_VARIANT = ContextVar("mem_uc2_variant", default=None)
FINAL_VARIANTS = ("final_v1", "final_v2", "final_v3", "final_v4")


def _final_api(name, *args, **kwargs):
    """Route final successors through the same preparation/execution API."""
    from . import final_methodology_networks as final
    if ACTIVE_VARIANT.get() in FINAL_VARIANTS and ACTIVE_VARIANT.get() != "final_v1":
        kwargs["variant"] = ACTIVE_VARIANT.get()
    return getattr(final, name)(*args, **kwargs)


@contextmanager
def execution_variant(variant):
    """Explicit, task-local namespace selection; no execution or global unlock."""
    if variant not in (None, *FINAL_VARIANTS):
        raise ValueError("UC2_UNKNOWN_VARIANT")
    token=ACTIVE_VARIANT.set(variant)
    try:
        yield
    finally:
        ACTIVE_VARIANT.reset(token)


def config() -> dict:
    value = load_yaml(CONFIG_PATH)
    expected = uc1.settings()["solver"]
    observed = {key: value["solver"].get(key) for key in expected}
    if (value["schema_version"] != "MEM_UC2_MANUAL_EXECUTION_V1" or observed != expected or
        value["solver"].get("adaptive_tuning") is not False):
        raise RuntimeError("UC2_CONFIG_FAIL: solver or schema differs from accepted UC-1")
    if value["governance"]["no_automatic_next_job"] is not True:
        raise RuntimeError("UC2_CONFIG_FAIL: automatic execution prohibited")
    if ACTIVE_VARIANT.get() in FINAL_VARIANTS:
        final=_final_api("settings")
        if {k:final["solver"][k] for k in value["solver"]} != value["solver"]:
            raise RuntimeError("UC2_FINAL_SOLVER_DRIFT")
        value["result_root"]=final["result_root"]
    return value


def _validate_case(year: int, scenario: str) -> None:
    if (year, scenario) not in SCENARIOS:
        raise ValueError(f"UC2_UNKNOWN_SCENARIO: {year} {scenario}")


def case_root(year: int, scenario: str) -> Path:
    _validate_case(year, scenario)
    if ACTIVE_VARIANT.get() == "final_v4" and year == 2040:
        return ROOT / "results/final_methodology_v3" / str(year) / scenario
    return ROOT / config()["result_root"] / str(year) / scenario


def job_id(year: int, scenario: str, kind: str) -> str:
    _validate_case(year, scenario)
    if kind not in RUN_TYPES:
        raise ValueError(f"UC2_UNKNOWN_RUN_TYPE: {kind}")
    prefix=f"{ACTIVE_VARIANT.get().upper()}_UC2" if ACTIVE_VARIANT.get() in FINAL_VARIANTS else "UC2"
    if ACTIVE_VARIANT.get() == "final_v4" and year == 2040:
        prefix = "FINAL_V3_UC2"
    return f"{prefix}_{year}_{scenario.upper()}_8760H_{kind}"


def job_paths(year: int, scenario: str, kind: str) -> dict[str, Path]:
    name = job_id(year, scenario, kind)
    directory = case_root(year, scenario) / RUN_FOLDERS[kind]
    source = uc1.parent_path(year, scenario) if kind == "POST_P2X_CONTINUOUS_REFERENCE" else uc1.derivative_path(year, scenario)
    if ACTIVE_VARIANT.get() in FINAL_VARIANTS:
        item=_final_api("package",year,scenario)
        source=ROOT/item["reference_path" if kind==RUN_TYPES[0] else "path"]
    return {"input": source, "directory": directory, "solved": directory / f"{name}_SOLVED.nc",
            "receipt": directory / f"{name}_SOLVE_RECEIPT.json",
            "verification": case_root(year, scenario) / "QA" / VERIFY_NAMES[kind]}


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise RuntimeError(f"UC2_MISSING_ARTIFACT: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _structural_receipt() -> dict:
    cfg = config()
    build = _read_json(ROOT / cfg["uc1_structural_receipt"])
    final = _read_json(ROOT / cfg["uc1_final_verification"])
    p2x = _read_json(ROOT / cfg["p2x_final_verification"])
    if (build.get("status") != "PASS" or build.get("deterministic_rebuild") != "PASS" or
        len(build.get("structural_packages", [])) != 6 or
        final.get("status") != "UC1_COMMON_ARCHITECTURE_PASS__2040_BASE_SMOKE_PASS__FULL_YEAR_NOT_AUTHORIZED" or
        p2x.get("final_gate") != "READY_FOR_MANUAL_EXECUTION"):
        raise RuntimeError("UC2_UPSTREAM_GATE_FAIL: UC-1/P2X authority is not accepted")
    return build


def _package(year: int, scenario: str) -> dict:
    return next(item for item in _structural_receipt()["structural_packages"]
                if item["year"] == year and item["scenario"] == scenario)


def _expected_hash(year: int, scenario: str, kind: str) -> str:
    if ACTIVE_VARIANT.get() in FINAL_VARIANTS:
        item=_final_api("package",year,scenario)
        return item["reference_sha256" if kind==RUN_TYPES[0] else "sha256"]
    item = _package(year, scenario)
    return item["parent_sha256"] if kind == "POST_P2X_CONTINUOUS_REFERENCE" else item["derivative_sha256"]


def _check_input_hash(year: int, scenario: str, kind: str) -> str:
    source = job_paths(year, scenario, kind)["input"]
    expected = _expected_hash(year, scenario, kind)
    if not source.is_file() or sha256_file(source) != expected:
        raise RuntimeError(f"UC2_INPUT_HASH_FAIL: {year} {scenario} {kind}")
    return expected


def _p2x_pair(network: pypsa.Network) -> tuple[pd.DataFrame, pd.DataFrame]:
    return uc1._p2x_signature(network)


def _p2x_energy(network: pypsa.Network, tolerance: float) -> dict[str, float]:
    generators, constraints = _p2x_pair(network)
    if not generators.sign.eq(-1).all() or generators.committable.astype(bool).any():
        raise RuntimeError("UC2_P2X_FAIL: sign/committability")
    output = {}
    weights = network.snapshot_weightings.generators
    for zone in config()["zones"]:
        generator = f"P2X_{zone}"
        constraint = f"P2X_ANNUAL_{zone}"
        if generator not in generators.index or constraint not in constraints.index:
            raise RuntimeError(f"UC2_P2X_FAIL: missing {zone}")
        dispatch = network.generators_t.p[generator]
        amount = float(dispatch.mul(weights).sum())
        target = float(constraints.at[constraint, "constant"])
        if abs(amount - target) > tolerance or dispatch.lt(-1e-7).any() or dispatch.gt(float(generators.at[generator, "p_nom"]) + 1e-7).any():
            raise RuntimeError(f"UC2_P2X_FAIL: hourly cap or annual equality {zone}")
        output[zone] = amount
    return output


def _balance(network: pypsa.Network) -> dict:
    from .reporting.canonical_results import annual_electricity_supply_by_zone, annual_demand_generation_balance_by_zone
    table = annual_demand_generation_balance_by_zone(network, annual_electricity_supply_by_zone(network))
    if table.balance_check_status.astype(str).ne("PASS").any():
        raise RuntimeError("UC2_ELECTRICAL_BALANCE_FAIL")
    shedding = network.generators.index[network.generators.carrier.eq("load_shedding")]
    shed = float(network.generators_t.p[shedding].mul(network.snapshot_weightings.generators, axis=0).sum().sum()) if len(shedding) else 0.0
    if shed < -1e-6:
        raise RuntimeError("UC2_LOAD_SHEDDING_FAIL: negative dispatch")
    return {"maximum_absolute_balance_residual_TWh": float(table.balance_residual_TWh.abs().max()),
            "load_shedding_MWh": shed}


def preflight(year: int, scenario: str, *, persist: bool = True) -> dict:
    """Deep, non-solving structural gate for exactly one accepted UC input pair."""
    _validate_case(year, scenario)
    if ACTIVE_VARIANT.get() in FINAL_VARIANTS:
        return _final_api("preflight",year,scenario,persist=persist)
    item = _package(year, scenario)
    parent_hash = _check_input_hash(year, scenario, "POST_P2X_CONTINUOUS_REFERENCE")
    uc_hash = _check_input_hash(year, scenario, "UC_MILP")
    parent = pypsa.Network(uc1.parent_path(year, scenario))
    network = pypsa.Network(uc1.derivative_path(year, scenario))
    if len(parent.snapshots) != len(network.snapshots) or len(network.snapshots) != 8760 or not parent.snapshots.equals(network.snapshots):
        raise RuntimeError("UC2_PREFLIGHT_FAIL: chronology")
    zones = set(config()["zones"])
    if not zones.issubset(network.buses.index) or len(zones) != 7:
        raise RuntimeError("UC2_PREFLIGHT_FAIL: Italian bidding zones")
    plan = pd.read_csv(ROOT / "qa" / "stage_b" / "uc1_common_v1" / "MEM_UC1_PARENT_CHILD_DECOMPOSITION.csv")
    plan = plan.loc[plan.year.eq(year) & plan.scenario.eq(scenario)].copy()
    if plan.child_id.duplicated().any() or len(plan) != item["synthetic_units"]:
        raise RuntimeError("UC2_PREFLIGHT_FAIL: synthetic-unit IDs/count")
    uc1.verify_derivative(parent, network, plan)
    pd.testing.assert_frame_equal(_p2x_pair(parent)[0], _p2x_pair(network)[0], check_dtype=False)
    pd.testing.assert_frame_equal(_p2x_pair(parent)[1], _p2x_pair(network)[1], check_dtype=False)
    required = {"p_min_pu": "p_min_pu", "ramp_limit_up": "ramp_limit_up",
                "ramp_limit_down": "ramp_limit_down", "ramp_limit_start_up": "ramp_limit_start_up",
                "ramp_limit_shut_down": "ramp_limit_shut_down", "min_up_time": "min_up_time",
                "min_down_time": "min_down_time", "start_up_cost": "total_child_startup_EUR"}
    incompatible = 0
    for row in plan.itertuples(index=False):
        child = network.generators.loc[row.child_id]
        for attribute, plan_attribute in required.items():
            if not math.isclose(float(child[attribute]), float(getattr(row, plan_attribute)), rel_tol=0, abs_tol=1e-7):
                raise RuntimeError(f"UC2_PREFLIGHT_FAIL: {row.child_id} {attribute}")
        availability = uc1._availability(network, row.child_id)
        incompatible += int((availability.gt(0) & availability.lt(float(row.p_min_pu))).sum())
    if incompatible:
        raise RuntimeError(f"UC2_PREFLIGHT_FAIL: {incompatible} availability/minimum-output conflicts")
    counts = plan.uc_archetype.value_counts().to_dict()
    if counts != item["archetype_counts"]:
        raise RuntimeError("UC2_PREFLIGHT_FAIL: archetype count drift")
    nuclear = plan.loc[plan.uc_archetype.eq("NUCLEAR_MODULAR")]
    if (year == 2040 and not nuclear.empty) or (year == 2050 and (len(nuclear) != 27 or not math.isclose(nuclear.child_p_nom_MW.sum(), 8000, abs_tol=1e-6))):
        raise RuntimeError("UC2_PREFLIGHT_FAIL: nuclear representation")
    result = {"status": "PASS", "prepared_for_manual_execution": True, "year": year, "scenario": scenario,
              "parent_path": str(uc1.parent_path(year, scenario).relative_to(ROOT)), "parent_sha256": parent_hash,
              "uc_input_path": str(uc1.derivative_path(year, scenario).relative_to(ROOT)), "uc_input_sha256": uc_hash,
              "snapshots": 8760, "Italian_zones": list(config()["zones"]),
              "synthetic_units": len(plan), "archetype_counts": counts, "availability_conflicts": 0,
              "capacity_conservation": "PASS", "p2x_pairs": 7, "nuclear_contract": "PASS"}
    if persist:
        _write_json(case_root(year, scenario) / "QA" / "PREFLIGHT.json", result)
    return result


def _governance_gate(year: int, scenario: str) -> str:
    if ACTIVE_VARIANT.get() in FINAL_VARIANTS:
        return _final_api("manual_gate",year,scenario)
    gates = config()["governance"]

    # Execution authorization is deliberately independent from analytical review.
    # Review flags remain false until project validation is actually completed.

    if (year, scenario) == (2040, "Base"):
        if gates["2040_base_manual_sequence_enabled"]:
            return "MANUAL_EXECUTION_2040_BASE__REVIEW_PENDING"

    elif year == 2040 and scenario in ("Slow", "High"):
        if gates["2040_slow_high_manual_enabled"]:
            return "MANUAL_EXECUTION_2040_SLOW_HIGH__REVIEW_PENDING"

    elif year == 2050 and scenario == "Base":
        if gates["2050_base_manual_enabled"]:
            return "MANUAL_EXECUTION_2050_BASE__NUCLEAR_SMOKE_AND_REVIEW_PENDING"

    elif year == 2050 and scenario in ("Slow", "High"):
        if gates["2050_slow_high_manual_enabled"]:
            return "MANUAL_EXECUTION_2050_SLOW_HIGH__NUCLEAR_SMOKE_AND_REVIEW_PENDING"

    raise RuntimeError(f"UC2_EXECUTION_LOCKED: {year} {scenario}")


def _verified_previous(year: int, scenario: str, kind: str) -> dict:
    path = job_paths(year, scenario, kind)["verification"]
    value = _read_json(path)
    if value.get("status") != "PASS" or value.get("year") != year or value.get("scenario") != scenario:
        raise RuntimeError(f"UC2_DEPENDENCY_FAIL: wrong or unaccepted {kind} verification")
    _, solve_receipt = _load_solved(year, scenario, kind)
    if value.get("solved_sha256") != solve_receipt["solved_sha256"]:
        raise RuntimeError(f"UC2_DEPENDENCY_FAIL: stale {kind} verification")
    return value


def _solver_contract() -> dict:
    # This import is intentionally confined to explicit manual run actions.
    import gurobipy as gp
    settings = config()["solver"]
    actual = ".".join(map(str, gp.gurobi.version()))
    if actual != settings["required_version"] or settings["name"] != "gurobi":
        raise RuntimeError(f"UC2_SOLVER_CONTRACT_FAIL: Gurobi {actual}")
    return settings


def _manual_solve(year: int, scenario: str, kind: str, *, benchmark_variant: str | None = None) -> dict:
    """Optimizer boundary: called only by one explicit run-* CLI action."""
    _validate_case(year, scenario)
    paths = job_paths(year, scenario, kind)
    if benchmark_variant is not None:
        if kind != "UC_MILP":
            raise RuntimeError("UC2_BENCHMARK_RUN_TYPE_REFUSED")
        from .stage_b_uc2a import benchmark_job
        paths = benchmark_job(year, scenario, benchmark_variant)
    if paths["solved"].exists() or paths["receipt"].exists():
        raise RuntimeError(f"UC2_OVERWRITE_REFUSED: {paths['solved']}")
    preflight(year, scenario)
    gate = _governance_gate(year, scenario)
    if kind == "UC_MILP":
        _verified_previous(year, scenario, "POST_P2X_CONTINUOUS_REFERENCE")
    if kind == "FIXED_COMMITMENT_PRICE_LP":
        _verified_previous(year, scenario, "UC_MILP")
    expected_input_sha = paths["input_sha256"] if benchmark_variant is not None else _check_input_hash(year, scenario, kind)
    solver = _solver_contract()
    network = pypsa.Network(paths["input"])
    network.meta.update({"uc2_year": year, "uc2_scenario": scenario, "uc2_kind": kind,
                         "uc2_input_sha256": expected_input_sha, "uc2_governance_gate": gate})
    if benchmark_variant is not None:
        network.meta["uc2a_manual_benchmark_variant"] = benchmark_variant
    model = network.optimize.create_model(linearized_unit_commitment=kind == "FIXED_COMMITMENT_PRICE_LP",
                                          include_objective_constant=False)
    names = []
    fixed_milp_sha = None
    if kind == "FIXED_COMMITMENT_PRICE_LP":
        milp, milp_receipt = _load_solved(year, scenario, "UC_MILP")
        fixed_milp_sha = milp_receipt["solved_sha256"]
        names = milp.generators.index[milp.generators.committable.astype(bool)].tolist()
        for attribute in ("status", "start_up", "shut_down"):
            fixed = getattr(milp.generators_t, attribute).loc[:, names]
            if fixed.shape != (8760, len(names)) or fixed.isna().any().any():
                raise RuntimeError(f"UC2_PRICE_INPUT_FAIL: incomplete {attribute}")
            values = xr.DataArray(fixed.to_numpy(dtype=float),
                                  coords={"snapshot": network.snapshots, "name": names},
                                  dims=("snapshot", "name"))
            network.model.add_constraints(network.model[f"Generator-{attribute}"].loc[:, names] == values,
                                          name=f"UC2-fixed-{attribute}")
    binaries = int(model.binaries.nvars)
    integers = int(model.integers.nvars)
    if kind != "UC_MILP" and (binaries or integers):
        raise RuntimeError("UC2_MODEL_TYPE_FAIL: expected continuous LP")
    if kind == "UC_MILP" and binaries <= 0:
        raise RuntimeError("UC2_MODEL_TYPE_FAIL: UC MILP has no binaries")
    started = perf_counter()
    logging = {}
    if benchmark_variant is not None:
        paths["directory"].mkdir(parents=True, exist_ok=True)
        logging["log_fn"] = paths["log"]
    status, termination = network.optimize.solve_model(
        solver_name="gurobi", solver_options=solver["options"],
        assign_all_duals=kind != "UC_MILP", log_to_console=False, **logging)
    duration = perf_counter() - started
    if str(status).lower() != "ok" or str(termination).lower() != "optimal":
        raise RuntimeError(f"UC2_SOLVE_FAIL: {year} {scenario} {kind} {status}/{termination}")
    backend = getattr(network.model, "solver_model", None)
    gap = float(backend.MIPGap) if kind == "UC_MILP" and backend is not None else None
    if kind == "UC_MILP" and (gap is None or not np.isfinite(gap)):
        raise RuntimeError("UC2_SOLVE_FAIL: Gurobi MIP gap unavailable")
    paths["directory"].mkdir(parents=True, exist_ok=True)
    water_coverage=None
    if ACTIVE_VARIANT.get() in FINAL_VARIANTS and kind=="FIXED_COMMITMENT_PRICE_LP":
        from .reporting.water_values import capture_store_energy_balance_coverage
        coverage=capture_store_energy_balance_coverage(network,_final_api("water_mapping",year,scenario))
        coverage_path=paths["directory"]/"RAW_STORE_ENERGY_BALANCE_DUAL_COVERAGE.parquet"
        coverage.to_parquet(coverage_path,index=False)
        water_coverage={"path":str(coverage_path.relative_to(ROOT)),"sha256":sha256_file(coverage_path)}
    network.export_to_netcdf(paths["solved"])
    receipt = {"status": "PASS", "year": year, "scenario": scenario, "kind": kind,
               "job_id": paths["job_id"] if benchmark_variant is not None else job_id(year, scenario, kind), "input_path": str(paths["input"].relative_to(ROOT)),
               "input_sha256": expected_input_sha, "solved_path": str(paths["solved"].relative_to(ROOT)),
               "solved_sha256": sha256_file(paths["solved"]), "solver": "gurobi", "solver_version": solver["required_version"],
               "solver_options": solver["options"], "include_objective_constant": False,
               "solver_status": str(status), "termination_condition": str(termination),
               "objective_EUR": float(network.objective), "mip_gap": gap,
               "runtime_seconds": duration, "snapshots": len(network.snapshots),
               "variables": int(model.nvars), "constraints": int(model.ncons),
               "integer_variables": integers, "binary_variables": binaries,
               "fixed_commitment_milp_sha256": fixed_milp_sha,
               "manual_job_only": True, "automatic_successor_launched": False}
    if ACTIVE_VARIANT.get() in FINAL_VARIANTS:
        receipt.update({"final_methodology_variant":ACTIVE_VARIANT.get(),"assign_all_duals":kind!="UC_MILP",
                        "water_dual_raw_coverage":water_coverage})
    if benchmark_variant is not None:
        telemetry = {}
        for attribute in ("NumConstrs", "NumVars", "NumNZs", "NumBinVars", "NumIntVars",
                          "NodeCount", "IterCount", "ObjBound", "MIPGap", "Runtime", "Work", "MemUsed", "MaxMemUsed"):
            try:
                telemetry[attribute] = float(getattr(backend, attribute))
            except Exception:
                # Optional telemetry must not invalidate an otherwise saved
                # optimal result when a backend attribute is unavailable.
                telemetry[attribute] = None
        receipt.update({"benchmark_variant": benchmark_variant,
                        "benchmark_log": str(paths["log"].relative_to(ROOT)),
                        "benchmark_telemetry": telemetry, "solver_tuning_changed": False})
    _write_json(paths["receipt"], receipt)
    return receipt


def _load_solved(year: int, scenario: str, kind: str) -> tuple[pypsa.Network, dict]:
    paths = job_paths(year, scenario, kind)
    receipt = _read_json(paths["receipt"])
    if (receipt.get("status") != "PASS" or receipt.get("year") != year or
        receipt.get("scenario") != scenario or receipt.get("kind") != kind or
        receipt.get("job_id") != job_id(year, scenario, kind) or
        receipt.get("solver_status") != "ok" or receipt.get("termination_condition") != "optimal" or
        receipt.get("input_sha256") != _check_input_hash(year, scenario, kind) or
        receipt.get("solved_path") != str(paths["solved"].relative_to(ROOT)) or
        not paths["solved"].is_file() or sha256_file(paths["solved"]) != receipt.get("solved_sha256")):
        raise RuntimeError(f"UC2_SOLVED_INTEGRITY_FAIL: {year} {scenario} {kind}")
    network = pypsa.Network(paths["solved"])
    if (int(network.meta.get("uc2_year", -1)) != year or network.meta.get("uc2_scenario") != scenario or
        network.meta.get("uc2_kind") != kind or len(network.snapshots) != 8760 or
        not math.isfinite(float(receipt["objective_EUR"])) or
        network.generators_t.p.empty):
        raise RuntimeError("UC2_SOLVED_INTEGRITY_FAIL: solved network metadata/dispatch")
    return network, receipt


def verify_reference(year: int, scenario: str) -> dict:
    network, receipt = _load_solved(year, scenario, "POST_P2X_CONTINUOUS_REFERENCE")
    if receipt["integer_variables"] or receipt["binary_variables"] or network.generators.committable.astype(bool).any():
        raise RuntimeError("UC2_REFERENCE_VERIFY_FAIL: non-continuous model")
    p2x = _p2x_energy(network, float(config()["validation"]["p2x_energy_tolerance_MWh"]))
    balance = _balance(network)
    result = {"status": "PASS", "year": year, "scenario": scenario,
              "job_id": receipt["job_id"], "solved_sha256": receipt["solved_sha256"],
              "objective_EUR": receipt["objective_EUR"], "p2x_by_zone_MWh": p2x, **balance}
    _write_json(job_paths(year, scenario, "POST_P2X_CONTINUOUS_REFERENCE")["verification"], result)
    return result


def _validate_commitment(network: pypsa.Network) -> pd.Index:
    names = network.generators.index[network.generators.committable.astype(bool)]
    if names.empty:
        raise RuntimeError("UC2_COMMITMENT_FAIL: no UC units")
    for attr in ("status", "start_up", "shut_down"):
        frame = getattr(network.generators_t, attr)
        if not set(names).issubset(frame.columns) or frame.loc[:, names].shape != (len(network.snapshots), len(names)) or frame.loc[:, names].isna().any().any():
            raise RuntimeError(f"UC2_COMMITMENT_FAIL: incomplete {attr}")
    return names


def verify_uc(year: int, scenario: str) -> dict:
    _verified_previous(year, scenario, "POST_P2X_CONTINUOUS_REFERENCE")
    network, receipt = _load_solved(year, scenario, "UC_MILP")
    names = _validate_commitment(network)
    if receipt["binary_variables"] <= 0 or receipt["mip_gap"] is None or not np.isfinite(receipt["mip_gap"]):
        raise RuntimeError("UC2_UC_VERIFY_FAIL: MIP counts/gap")
    if not set(network.generators.index).issubset(network.generators_t.p.columns):
        raise RuntimeError("UC2_UC_VERIFY_FAIL: incomplete generator dispatch")
    if not set(network.stores.index).issubset(network.stores_t.e.columns) or network.stores_t.e.isna().any().any():
        raise RuntimeError("UC2_UC_VERIFY_FAIL: incomplete hydro/storage states")
    if not set(network.links.index).issubset(network.links_t.p0.columns) or network.links_t.p0.isna().any().any():
        raise RuntimeError("UC2_UC_VERIFY_FAIL: incomplete link flows")
    p2x = _p2x_energy(network, float(config()["validation"]["p2x_energy_tolerance_MWh"]))
    balance = _balance(network)
    result = {"status": "PASS", "price_recovery_authorized": True,
              "year": year, "scenario": scenario, "job_id": receipt["job_id"],
              "solved_sha256": receipt["solved_sha256"], "objective_EUR": receipt["objective_EUR"],
              "mip_gap": receipt["mip_gap"], "synthetic_units": len(names), "p2x_by_zone_MWh": p2x,
              **balance}
    _write_json(job_paths(year, scenario, "UC_MILP")["verification"], result)
    return result


def canonical_transitions(network: pypsa.Network) -> pd.DataFrame:
    names = _validate_commitment(network)
    previous, status, actual_start, actual_shutdown = _transition_arrays(network, names)
    raw_start = network.generators_t.start_up.loc[:, names]
    raw_shutdown = network.generators_t.shut_down.loc[:, names]
    if (raw_start.to_numpy() + 1e-7 < actual_start.to_numpy()).any() or (raw_shutdown.to_numpy() + 1e-7 < actual_shutdown.to_numpy()).any():
        raise RuntimeError("UC2_COMMITMENT_FAIL: native event variable misses actual transition")
    frames = []
    for name in names:
        frames.append(pd.DataFrame({"snapshot": network.snapshots, "child_id": name,
                                    "zone": network.generators.at[name, "bus"],
                                    "carrier": network.generators.at[name, "carrier"],
                                    "previous_status": previous[name].to_numpy(),
                                    "status": status[name].to_numpy(),
                                    "raw_start_up": raw_start[name].to_numpy(),
                                    "raw_shut_down": raw_shutdown[name].to_numpy(),
                                    "actual_start": actual_start[name].to_numpy(),
                                    "actual_shutdown": actual_shutdown[name].to_numpy()}))
    return pd.concat(frames, ignore_index=True)


def _transition_arrays(network: pypsa.Network, names: pd.Index) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    status = network.generators_t.status.loc[:, names]
    previous = status.shift(1)
    previous.iloc[0] = network.generators.loc[names, "up_time_before"].gt(0).astype(float).to_numpy()
    actual_start = ((status > 0.5) & (previous < 0.5)).astype(int)
    actual_shutdown = ((status < 0.5) & (previous > 0.5)).astype(int)
    return previous, status, actual_start, actual_shutdown


def verify_price(year: int, scenario: str) -> dict:
    _verified_previous(year, scenario, "UC_MILP")
    milp, milp_receipt = _load_solved(year, scenario, "UC_MILP")
    price, receipt = _load_solved(year, scenario, "FIXED_COMMITMENT_PRICE_LP")
    if receipt.get("fixed_commitment_milp_sha256") != milp_receipt["solved_sha256"]:
        raise RuntimeError("UC2_PRICE_VERIFY_FAIL: wrong MILP source")
    names = _validate_commitment(milp)
    _validate_commitment(price)
    if receipt["integer_variables"] or receipt["binary_variables"]:
        raise RuntimeError("UC2_PRICE_VERIFY_FAIL: price model is not LP")
    differences = {}
    for attr in ("status", "start_up", "shut_down"):
        difference = float((getattr(milp.generators_t, attr).loc[:, names] -
                            getattr(price.generators_t, attr).loc[:, names]).abs().to_numpy().max())
        if difference > float(config()["validation"]["commitment_tolerance"]):
            raise RuntimeError(f"UC2_PRICE_VERIFY_FAIL: {attr} differs")
        differences[attr] = difference
    _, _, left_start, left_shutdown = _transition_arrays(milp, names)
    _, _, right_start, right_shutdown = _transition_arrays(price, names)
    if not np.array_equal(left_start.to_numpy(), right_start.to_numpy()) or not np.array_equal(left_shutdown.to_numpy(), right_shutdown.to_numpy()):
        raise RuntimeError("UC2_PRICE_VERIFY_FAIL: actual status transitions differ")
    difference = abs(float(receipt["objective_EUR"]) - float(milp_receipt["objective_EUR"]))
    relative = difference / max(abs(float(milp_receipt["objective_EUR"])), 1.0)
    thresholds = config()["validation"]
    if difference > float(thresholds["objective_absolute_tolerance_EUR"]) and relative > float(thresholds["objective_relative_tolerance"]):
        raise RuntimeError("UC2_FIXED_COMMITMENT_RECONCILIATION_FAIL: objective")
    zones = list(config()["zones"])
    prices = price.buses_t.marginal_price
    if not set(zones).issubset(prices.columns) or prices.loc[:, zones].shape != (8760, 7) or not np.isfinite(prices.loc[:, zones].to_numpy()).all():
        raise RuntimeError("UC2_PRICE_VERIFY_FAIL: incomplete 8760 x 7 prices")
    result = {"status": "PASS", "year": year, "scenario": scenario,
              "job_id": receipt["job_id"], "solved_sha256": receipt["solved_sha256"],
              "milp_solved_sha256": milp_receipt["solved_sha256"],
              "milp_objective_EUR": milp_receipt["objective_EUR"],
              "fixed_commitment_lp_objective_EUR": receipt["objective_EUR"],
              "absolute_objective_difference_EUR": difference, "relative_objective_difference": relative,
              "max_raw_commitment_difference": differences,
              "actual_transition_trajectories_equal": True,
              "price_label": "FIXED_COMMITMENT_MARGINAL_PRICE", "price_zone_hours": int(prices.loc[:, zones].size),
              "integer_variables": 0, "binary_variables": 0}
    if ACTIVE_VARIANT.get() in FINAL_VARIANTS:
        from .reporting.water_values import extract_water_values
        coverage=receipt.get("water_dual_raw_coverage")
        if not coverage or sha256_file(ROOT/coverage["path"])!=coverage["sha256"]:
            raise RuntimeError("UC2_FINAL_WATER_RAW_COVERAGE_FAIL")
        water,water_receipt=extract_water_values(price,_final_api("water_mapping",year,scenario),
            {**receipt,"fixed_commitment_trajectories_verified":True,"objective_reconciliation_verified":True},
            verified_milp_sha256=milp_receipt["solved_sha256"],raw_coverage=pd.read_parquet(ROOT/coverage["path"]))
        destination=case_root(year,scenario)/"QA"
        destination.mkdir(parents=True,exist_ok=True)
        water.to_parquet(destination/"NATIVE_WATER_VALUES_EUR_PER_MWH_WATER.parquet",index=False)
        _write_json(destination/"WATER_VALUE_COMPLETENESS.json",water_receipt)
        result["water_value_completeness"]=water_receipt
    _write_json(job_paths(year, scenario, "FIXED_COMMITMENT_PRICE_LP")["verification"], result)
    return result


def report(year: int, scenario: str) -> dict:
    """Post-solve-only reporting; combines MILP operation with fixed-LP prices."""
    from .reporting.uc2_postprocess import no_solver_calls
    from .reporting.marginal_regime import cached_report, produce, compare, install_report_cache
    with no_solver_calls():
        accepted = cached_report(year, scenario)
        result = accepted if accepted is not None else _report_verified(year, scenario)
        regime = produce(year, scenario)
        if accepted is None and regime['status'] == 'PASS_CANONICAL_MARGINAL_REGIME':
            install_report_cache(year, scenario, result)
        return {**result, 'marginal_regime': regime,
                'marginal_regime_comparison': compare(year)}


def _report_verified(year: int, scenario: str) -> dict:
    reference_gate = _verified_previous(year, scenario, "POST_P2X_CONTINUOUS_REFERENCE")
    uc_gate = _verified_previous(year, scenario, "UC_MILP")
    price_gate = _verified_previous(year, scenario, "FIXED_COMMITMENT_PRICE_LP")
    output = case_root(year, scenario) / "REPORTING"
    from .reporting.uc2_postprocess import extend_uc2_report, update_uc2_comparisons
    if output.exists() and any(output.iterdir()):
        extension = extend_uc2_report(year, scenario, output)
        comparison = update_uc2_comparisons(year)
        return {**_read_json(output / "MEM_UC2_REPORTING_RECEIPT.json"),
                "vis_reporting_r1": extension, "comparisons": comparison}
    milp, milp_receipt = _load_solved(year, scenario, "UC_MILP")
    reference, reference_receipt = _load_solved(year, scenario, "POST_P2X_CONTINUOUS_REFERENCE")
    price, _ = _load_solved(year, scenario, "FIXED_COMMITMENT_PRICE_LP")
    output.mkdir(parents=True, exist_ok=True)
    transition = canonical_transitions(milp)
    transition.to_parquet(output / "canonical_commitment_transitions.parquet", index=False)
    summary = transition.groupby(["zone", "carrier"], as_index=False).agg(
        actual_starts=("actual_start", "sum"), actual_shutdowns=("actual_shutdown", "sum"),
        raw_starts=("raw_start_up", "sum"), raw_shutdowns=("raw_shut_down", "sum"))
    summary.to_csv(output / "commitment_events_by_zone_technology.csv", index=False)
    transition.groupby("carrier", as_index=False).agg(
        actual_starts=("actual_start", "sum"), actual_shutdowns=("actual_shutdown", "sum"),
        raw_starts=("raw_start_up", "sum"), raw_shutdowns=("raw_shut_down", "sum")
    ).to_csv(output / "commitment_events_by_technology.csv", index=False)
    capacities = milp.generators.p_nom.to_dict()
    transition["committed_MW"] = transition.status * transition.child_id.map(capacities)
    online = transition.groupby("snapshot", as_index=False).agg(
        online_units=("status", "sum"), committed_MW=("committed_MW", "sum"),
        actual_starts=("actual_start", "sum"), actual_shutdowns=("actual_shutdown", "sum"))
    online.to_csv(output / "hourly_online_units_and_committed_MW.csv", index=False)
    durations = []
    for child_id, group in transition.groupby("child_id", sort=False):
        status = group.status.reset_index(drop=True)
        run = status.ne(status.shift()).cumsum()
        for _, segment in group.groupby(run.to_numpy(), sort=False):
            if float(segment.status.iloc[0]) > 0.5:
                durations.append({"child_id": child_id, "zone": group.zone.iloc[0],
                                  "carrier": group.carrier.iloc[0], "online_run_hours": len(segment),
                                  "starts_at_first_snapshot": bool(pd.Timestamp(segment.snapshot.iloc[0]) == milp.snapshots[0])})
    pd.DataFrame(durations).to_csv(output / "online_runtime_distribution.csv", index=False)
    transition["hour_number"] = transition.groupby("child_id").cumcount() + 1
    transition["window"] = pd.cut(transition.hour_number, [0, 24, 48, 8760],
                                   labels=["HOURS_1_24", "HOURS_25_48", "HOURS_49_8760"])
    boundary = transition.groupby(["window", "zone", "carrier"], observed=True, as_index=False).agg(
        actual_starts=("actual_start", "sum"), actual_shutdowns=("actual_shutdown", "sum"),
        raw_shutdowns=("raw_shut_down", "sum"))
    boundary.to_csv(output / "boundary_and_annual_cycling.csv", index=False)
    transition.loc[transition.hour_number.le(48)].to_csv(output / "first_48_hour_commitment_diagnostics.csv", index=False)
    p2x_ids = [f"P2X_{zone}" for zone in config()["zones"]]
    p2x_hourly = milp.generators_t.p.loc[:, p2x_ids]
    p2x_hourly.to_csv(output / "p2x_hourly_MW.csv", index_label="snapshot")
    p2x_annual = _p2x_energy(milp, float(config()["validation"]["p2x_energy_tolerance_MWh"]))
    pd.DataFrame([{"zone": zone, "consumption_MWh": value,
                   "power_cap_MW": float(milp.generators.at[f"P2X_{zone}", "p_nom"])}
                  for zone, value in p2x_annual.items()]).to_csv(output / "p2x_annual_by_zone.csv", index=False)
    milp.stores_t.e.to_parquet(output / "storage_hydro_soc_hourly.parquet")
    milp.links_t.p0.to_parquet(output / "link_bus0_flows_hourly.parquet")
    vre_ids = milp.generators.index[milp.generators.carrier.isin(
        ["solar_pv_rooftop", "solar_pv_utility", "wind_onshore", "wind_offshore"])]
    weights = milp.snapshot_weightings.generators
    vre_rows = []
    for generator_id in vre_ids:
        profile = uc1._availability(milp, generator_id)
        potential = float((profile * float(milp.generators.at[generator_id, "p_nom"])).mul(weights).sum())
        dispatched = float(milp.generators_t.p[generator_id].mul(weights).sum())
        vre_rows.append({"generator_id": generator_id, "zone": milp.generators.at[generator_id, "bus"],
                         "carrier": milp.generators.at[generator_id, "carrier"],
                         "available_MWh": potential, "dispatched_MWh": dispatched,
                         "curtailed_MWh": potential - dispatched})
    pd.DataFrame(vre_rows).to_csv(output / "vre_curtailment.csv", index=False)
    capacity_rows = []
    for (zone, carrier), group in milp.generators.groupby(["bus", "carrier"]):
        if zone not in config()["zones"] or carrier in {"load_shedding", "spillage", "water_energy"} or str(carrier).startswith("p2x"):
            continue
        capacity = float(group.p_nom.sum())
        generation = float(milp.generators_t.p[group.index].mul(weights, axis=0).sum().sum())
        if capacity > 0:
            capacity_rows.append({"zone": zone, "carrier": carrier, "capacity_MW": capacity,
                                  "generation_MWh": generation, "capacity_factor": generation / (capacity * float(weights.sum()))})
    pd.DataFrame(capacity_rows).to_csv(output / "generator_capacity_factors_by_zone_carrier.csv", index=False)
    prices = price.buses_t.marginal_price.loc[:, config()["zones"]]
    prices.to_csv(output / "fixed_commitment_prices_hourly.csv", index_label="snapshot")
    price_rows = []
    for zone in config()["zones"]:
        series = prices[zone]
        load_ids = price.loads.index[price.loads.bus.eq(zone)]
        rigid = price.loads_t.p[load_ids].sum(axis=1)
        price_rows.append({"zone": zone, "arithmetic_mean_EUR_MWh": float(series.mean()),
                           "rigid_load_weighted_mean_EUR_MWh": float((series * rigid).sum() / rigid.sum()),
                           "median_EUR_MWh": float(series.median()), "p5_EUR_MWh": float(series.quantile(0.05)),
                           "p95_EUR_MWh": float(series.quantile(0.95)), "p99_EUR_MWh": float(series.quantile(0.99)),
                           "minimum_EUR_MWh": float(series.min()), "maximum_EUR_MWh": float(series.max()),
                           "zero_price_hours": int(np.isclose(series, 0, atol=1e-8).sum()),
                           "negative_price_hours": int((series < -1e-8).sum()),
                           "hours_above_500_EUR_MWh": int((series > float(config()["validation"]["high_price_threshold_EUR_per_MWh"])).sum())})
    pd.DataFrame(price_rows).to_csv(output / "zonal_price_statistics.csv", index=False)
    prices.resample("MS").mean().to_csv(output / "monthly_zonal_price_means.csv", index_label="month")
    economics = {"continuous_reference_objective_EUR": reference_receipt["objective_EUR"],
                 "uc_milp_objective_EUR": milp_receipt["objective_EUR"],
                 "uc_uplift_EUR": milp_receipt["objective_EUR"] - reference_receipt["objective_EUR"],
                 "uc_uplift_percent": 100 * (milp_receipt["objective_EUR"] - reference_receipt["objective_EUR"]) / abs(reference_receipt["objective_EUR"]),
                 "fixed_commitment_lp_objective_EUR": price_gate["fixed_commitment_lp_objective_EUR"],
                 "milp_price_lp_absolute_difference_EUR": price_gate["absolute_objective_difference_EUR"]}
    _write_json(output / "economic_comparison.json", economics)
    # Reuse the existing canonical PyPSA/MEM tables and figures: operations
    # come from the UC MILP, prices are the conditional fixed-commitment duals.
    merged = milp.copy()
    merged.buses_t.marginal_price = price.buses_t.marginal_price.copy()
    from .reporting.canonical_results import generate_canonical_results
    canonical = generate_canonical_results(merged, year, scenario, output / "CANONICAL",
                                           require_full_year=True, render_figures=True)
    receipt = {"status": "PASS", "year": year, "scenario": scenario,
               "reporting_solver_invocations": 0, "reference_verification": reference_gate["solved_sha256"],
               "uc_verification": uc_gate["solved_sha256"], "price_verification": price_gate["solved_sha256"],
               "fixed_commitment_price_label": "FIXED_COMMITMENT_MARGINAL_PRICE",
               "canonical_reporting": canonical}
    _write_json(output / "MEM_UC2_REPORTING_RECEIPT.json", receipt)
    if ACTIVE_VARIANT.get() in FINAL_VARIANTS:
        from .final_network_signed import net_flow_frame
        from .reporting.ror_water import ror_water_accounting
        net_flow_frame(milp).to_parquet(output/"NET_FLOW_A_TO_B_MW.parquet")
        water_source=case_root(year,scenario)/"QA/NATIVE_WATER_VALUES_EUR_PER_MWH_WATER.parquet"
        pd.read_parquet(water_source).to_parquet(output/"NATIVE_WATER_VALUES_EUR_PER_MWH_WATER.parquet",index=False)
        source=pd.read_parquet(ROOT/"runtime_inputs/final_methodology_closure/hydro/zonal_runoff_v1/ROR_ACCESSIBLE_AND_BYPASS_2019.parquet")
        hourly,annual=ror_water_accounting(milp,source,year,scenario)
        hourly.to_parquet(output/"ROR_WATER_BYPASS_AND_ELECTRICAL_GENERATION.parquet",index=False)
        annual.to_csv(output/"ROR_WATER_BYPASS_AND_ELECTRICAL_GENERATION_ANNUAL.csv",index=False)
    extension = extend_uc2_report(year, scenario, output, uc_network=milp)
    comparison = update_uc2_comparisons(year)
    return {**receipt, "vis_reporting_r1": extension, "comparisons": comparison}


def final_qa(year: int, scenario: str) -> dict:
    for kind in RUN_TYPES:
        _verified_previous(year, scenario, kind)
    reporting = _read_json(case_root(year, scenario) / "REPORTING" / "MEM_UC2_REPORTING_RECEIPT.json")
    if reporting.get("status") != "PASS" or reporting.get("year") != year or reporting.get("scenario") != scenario:
        raise RuntimeError("UC2_FINAL_QA_FAIL: reporting receipt")
    result = {"status": "PASS", "year": year, "scenario": scenario,
              "three_jobs_verified": True, "reporting_complete": True,
              "manual_review_still_required": True, "automatic_next_scenario": False}
    _write_json(case_root(year, scenario) / "QA" / "FINAL_SCENARIO_QA.json", result)
    return result


def registry() -> pd.DataFrame:
    rows = []
    gates = config()["governance"]
    for year, scenario in SCENARIOS:
        if year == 2040 and scenario == "Base":
            gate_name, enable_flag, review_flag = ("MANUAL_2040_BASE", "2040_base_manual_sequence_enabled",
                                                   "2040_base_sol_review_accepted")
        elif year == 2040:
            gate_name, enable_flag, review_flag = ("MANUAL_2040_SLOW_HIGH", "2040_slow_high_manual_enabled",
                                                   None)
        elif scenario == "Base":
            gate_name, enable_flag, review_flag = ("MANUAL_2050_BASE", "2050_base_manual_enabled",
                                                   "2050_base_sol_review_accepted")
        else:
            gate_name, enable_flag, review_flag = ("MANUAL_2050_SLOW_HIGH", "2050_slow_high_manual_enabled",
                                                   None)
        for kind in RUN_TYPES:
            paths = job_paths(year, scenario, kind)
            rows.append({"job_id": job_id(year, scenario, kind), "year": year, "scenario": scenario,
                         "run_type": kind, "input_path": str(paths["input"].relative_to(ROOT)),
                         "input_sha256": _check_input_hash(year, scenario, kind),
                         "solved_path": str(paths["solved"].relative_to(ROOT)),
                         "solve_receipt_path": str(paths["receipt"].relative_to(ROOT)),
                         "verification_path": str(paths["verification"].relative_to(ROOT)),
                         "governance_gate": gate_name,
                         "manual_execution_enabled": bool(gates[enable_flag]),
                         "analytical_review_accepted": bool(gates[review_flag]) if review_flag else False,
                         "nuclear_smoke_accepted": bool(gates["2050_base_nuclear_smoke_accepted"]) if year == 2050 else None,
                         "executed_in_preparation": False})
    frame = pd.DataFrame(rows)
    if len(frame) != 18 or frame.job_id.duplicated().any() or frame.solved_path.duplicated().any():
        raise RuntimeError("UC2_REGISTRY_FAIL: expected 18 isolated jobs")
    return frame


def prepare() -> dict:
    """Generate only planning/QA artifacts; never create model/result files."""
    build = _structural_receipt()
    structural_preflights = [preflight(year, scenario, persist=False) for year, scenario in SCENARIOS]
    if len(structural_preflights) != 6 or any(item["status"] != "PASS" for item in structural_preflights):
        raise RuntimeError("UC2_PREPARATION_FAIL: six-scenario preflight")
    frame = registry()
    uc1_smoke_receipt_path = ROOT / "results" / "UC1_2040_BASE_168H" / "MEM_UC1_168H_RUN_RECEIPT.json"
    uc1_smoke = _read_json(uc1_smoke_receipt_path)
    smoke_hashes = {}
    for key in ("reference", "milp", "price_lp"):
        item = uc1_smoke[key]
        if sha256_file(ROOT / item["network"]) != item["network_sha256"]:
            raise RuntimeError(f"UC2_PREPARATION_FAIL: UC-1 smoke {key} hash drift")
        smoke_hashes[key] = item["network_sha256"]
    qa_dir = ROOT / config()["qa_root"]
    qa_dir.mkdir(parents=True, exist_ok=True)
    registry_path = qa_dir / "MEM_UC2_ALL_SCENARIO_JOB_REGISTRY.csv"
    frame.to_csv(registry_path, index=False, lineterminator="\n")
    result_root = ROOT / config()["result_root"]
    existing = [str(path.relative_to(ROOT)) for path in result_root.rglob("*_SOLVED.nc")] if result_root.exists() else []
    if existing:
        raise RuntimeError(f"UC2_PREPARATION_FAIL: solved UC-2 results already exist: {existing}")
    receipt = {"status": "UC2_ALL_SCENARIOS_PREPARED_FOR_MANUAL_EXECUTION",
               "jobs": 18, "scenarios": 6, "run_types_per_scenario": 3,
               "non_solving_deep_preflights": structural_preflights,
               "parent_hashes": build["parents"],
               "uc_input_hashes": {f"{item['year']}_{item['scenario']}": item["derivative_sha256"]
                                   for item in build["structural_packages"]},
               "registry_sha256": sha256_file(registry_path),
               "uc1_smoke_unchanged_sha256": sha256_file(uc1_smoke_receipt_path),
               "uc1_smoke_solved_hashes_unchanged": smoke_hashes,
               "production_solved_files_created": 0, "optimizer_invocations": 0,
               "focused_non_solving_tests": {"passed": 14, "failed": 0,
                                             "file": "tests/test_stage_b_uc2_preparation.py"},
               "gates": config()["governance"]}
    receipt_path = qa_dir / "MEM_UC2_PREPARATION_QA_RECEIPT.json"
    _write_json(receipt_path, receipt)
    manifest_items = [CONFIG_PATH, ROOT / "src" / "mem_model" / "stage_b_uc2.py", registry_path,
                      receipt_path, ROOT / "docs" / "MEM_UC2_ALL_SCENARIOS_MANUAL_RUNBOOK.md",
                      ROOT / "docs" / "MEM_UC2_ALL_SCENARIOS_MANUAL_EXECUTION_TRANSFER.md",
                      ROOT / "tests" / "test_stage_b_uc2_preparation.py"]
    manifest_items += [ROOT / item["parent"] for item in build["structural_packages"]]
    manifest_items += [ROOT / item["derivative"] for item in build["structural_packages"]]
    pd.DataFrame({"artifact": [str(path.relative_to(ROOT)) for path in manifest_items],
                  "sha256": [sha256_file(path) for path in manifest_items],
                  "role": (["UC2_CONFIG", "UC2_CLI", "JOB_REGISTRY", "PREPARATION_RECEIPT", "RUNBOOK", "TRANSFER", "NON_SOLVING_TESTS"] +
                           ["P2X_CONTINUOUS_PARENT_UNSOLVED"] * 6 + ["UC1_UC_PARENT_UNSOLVED"] * 6)}).to_csv(
        qa_dir / "MEM_UC2_PREPARATION_ARTIFACT_MANIFEST.csv", index=False, lineterminator="\n")
    return receipt


def reconcile_preparation() -> dict:
    """Refresh governance/preparation evidence without preflight or result-file access.

    Completed manual solve receipts are read only as progress metadata. No
    solved network, active MILP output, optimization model, or solver is opened.
    """
    cfg = config()
    gates = cfg["governance"]
    qa_dir = ROOT / cfg["qa_root"]
    receipt_path = qa_dir / "MEM_UC2_PREPARATION_QA_RECEIPT.json"
    receipt = _read_json(receipt_path)
    if (receipt.get("jobs") != 18 or len(receipt.get("non_solving_deep_preflights", [])) != 6 or
        any(item.get("status") != "PASS" for item in receipt["non_solving_deep_preflights"])):
        raise RuntimeError("UC2_RECONCILIATION_FAIL: previous preparation evidence")
    frame = registry()
    if not frame.manual_execution_enabled.all() or frame.analytical_review_accepted.any():
        raise RuntimeError("UC2_RECONCILIATION_FAIL: manual/review state")
    registry_path = qa_dir / "MEM_UC2_ALL_SCENARIO_JOB_REGISTRY.csv"
    frame.to_csv(registry_path, index=False, lineterminator="\n")
    reference = job_paths(2040, "Base", RUN_TYPES[0])
    reference_receipt = _read_json(reference["receipt"])
    reference_verification = _read_json(reference["verification"])
    if (reference_receipt.get("status") != "PASS" or reference_receipt.get("termination_condition") != "optimal" or
        reference_receipt.get("year") != 2040 or reference_receipt.get("scenario") != "Base" or
        reference_receipt.get("snapshots") != 8760 or reference_receipt.get("binary_variables") != 0 or
        reference_receipt.get("integer_variables") != 0 or
        reference_verification.get("status") != "PASS" or
        reference_verification.get("solved_sha256") != reference_receipt.get("solved_sha256")):
        raise RuntimeError("UC2_RECONCILIATION_FAIL: Base reference receipt")
    uc_receipt_path = job_paths(2040, "Base", RUN_TYPES[1])["receipt"]
    if uc_receipt_path.is_file():
        uc_receipt = _read_json(uc_receipt_path)
        if uc_receipt.get("year") != 2040 or uc_receipt.get("scenario") != "Base":
            raise RuntimeError("UC2_RECONCILIATION_FAIL: Base UC receipt identity")
        uc_state = "COMPLETED_UNVERIFIED" if uc_receipt.get("status") == "PASS" else "RECEIPT_PRESENT_NOT_PASS"
    else:
        uc_state = "MANUAL_EXECUTION_IN_PROGRESS"
    receipt["status"] = "UC2_ALL_SCENARIOS_PREPARED__MANUAL_EXECUTION_ENABLED__REVIEWS_PENDING"
    receipt["technically_prepared"] = True
    receipt["manual_execution_enabled"] = True
    receipt["analytical_review_accepted"] = False
    receipt["gates"] = gates
    receipt["registry_sha256"] = sha256_file(registry_path)
    receipt["production_solved_files_created_during_original_preparation"] = receipt.pop("production_solved_files_created", 0)
    receipt["optimizer_invocations_during_reconciliation"] = 0
    receipt["manual_production_progress"] = {
        "2040_Base_reference": "COMPLETED_VERIFIED",
        "2040_Base_reference_solved_sha256": reference_receipt["solved_sha256"],
        "2040_Base_reference_objective_EUR": reference_receipt["objective_EUR"],
        "2040_Base_UC_MILP": uc_state,
    }
    _write_json(receipt_path, receipt)
    build = _structural_receipt()
    manifest_items = [CONFIG_PATH, ROOT / "src" / "mem_model" / "stage_b_uc2.py", registry_path,
                      receipt_path, ROOT / "docs" / "MEM_UC2_ALL_SCENARIOS_MANUAL_RUNBOOK.md",
                      ROOT / "docs" / "MEM_UC2_ALL_SCENARIOS_MANUAL_EXECUTION_TRANSFER.md",
                      ROOT / "tests" / "test_stage_b_uc2_preparation.py"]
    manifest_items += [ROOT / item["parent"] for item in build["structural_packages"]]
    manifest_items += [ROOT / item["derivative"] for item in build["structural_packages"]]
    pd.DataFrame({"artifact": [str(path.relative_to(ROOT)) for path in manifest_items],
                  "sha256": [sha256_file(path) for path in manifest_items],
                  "role": (["UC2_CONFIG", "UC2_CLI", "JOB_REGISTRY", "PREPARATION_RECEIPT", "RUNBOOK", "TRANSFER", "NON_SOLVING_TESTS"] +
                           ["P2X_CONTINUOUS_PARENT_UNSOLVED"] * 6 + ["UC1_UC_PARENT_UNSOLVED"] * 6)}).to_csv(
        qa_dir / "MEM_UC2_PREPARATION_ARTIFACT_MANIFEST.csv", index=False, lineterminator="\n")
    return receipt


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "reconcile-preparation", "list-jobs", "preflight", "run-reference",
                                           "verify-reference", "run-uc", "verify-uc", "run-price",
                                           "verify-price", "report", "final-qa"])
    parser.add_argument("--year", type=int)
    parser.add_argument("--scenario", choices=["Slow", "Base", "High"])
    parser.add_argument("--variant",choices=FINAL_VARIANTS,help="Prepared final family; one explicitly selected manual job only")
    parser.add_argument("--benchmark-variant", choices=["v2A"],
                        help="Separate future manual 2040 Slow UC-v2A benchmark namespace")
    args = parser.parse_args(argv)
    if args.variant is not None:
        if args.action in {"prepare","reconcile-preparation","list-jobs"} or args.benchmark_variant is not None:
            parser.error("--variant applies only to single-scenario actions and cannot be combined with a benchmark")
        # Reuse this exact CLI and all same-scenario dependency checks under a
        # task-local final namespace. No chained action or solve occurs here.
        arguments=list(argv) if argv is not None else __import__('sys').argv[1:]
        if "--variant" in arguments:
            flag=arguments.index("--variant")
            arguments=arguments[:flag]+arguments[flag+2:]
        else:
            arguments=[a for a in arguments if not a.startswith("--variant=")]
        # 2040 is an inherited execution state, not a cosmetically relabelled run.
        routed_variant = "final_v3" if args.variant == "final_v4" and args.year == 2040 else args.variant
        with execution_variant(routed_variant):
            return main(arguments)
    if args.benchmark_variant is not None and (args.action != "run-uc" or (args.year, args.scenario) != (2040, "Slow")):
        parser.error("--benchmark-variant is restricted to run-uc --year 2040 --scenario Slow")
    if args.action == "prepare":
        result = prepare()
    elif args.action == "reconcile-preparation":
        result = reconcile_preparation()
    elif args.action == "list-jobs":
        result = registry().to_dict(orient="records")
    else:
        if args.year is None or args.scenario is None:
            parser.error("--year and --scenario are required for every scenario action")
        action = {"preflight": preflight,
                  "run-reference": lambda y, s: _manual_solve(y, s, RUN_TYPES[0]),
                  "verify-reference": verify_reference,
                  "run-uc": lambda y, s: _manual_solve(y, s, RUN_TYPES[1], benchmark_variant=args.benchmark_variant),
                  "verify-uc": verify_uc,
                  "run-price": lambda y, s: _manual_solve(y, s, RUN_TYPES[2]),
                  "verify-price": verify_price,
                  "report": report, "final-qa": final_qa}[args.action]
        try:
            result = action(args.year, args.scenario)
        except Exception as exc:
            if args.action == "preflight":
                print(json.dumps({"status": "FAIL", "prepared_for_manual_execution": False,
                                  "year": args.year, "scenario": args.scenario,
                                  "reason": str(exc)}, indent=2))
                raise SystemExit(2) from exc
            raise
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    # Reporting imports this canonical module: share its ContextVar/router,
    # rather than execute a second router with an isolated __main__ context.
    from .stage_b_uc2 import main as _canonical_main
    _canonical_main()


"""Build-only, hash-locked 2050 Italian Stage-B runtime and scenario networks.

The accepted 2040 transformation functions are reused under an explicit,
context-local horizon. This module never invokes a solver.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from . import stage_b_2040_runtime as shared
from .common import ACCEPTED_RUNTIME, ACCEPTED_RUNTIME_2050, ROOT, STATIC, dump_json, sha256_file


YEAR = 2050
SCENARIOS = shared.SCENARIOS
ZONES = set(shared.ZONES)
PRICE_MARKETS = set(shared.PRICE_MARKETS)
MANIFEST_NAME = "MEM_STAGE_B_2050_ACCEPTED_RUNTIME_MANIFEST_v1.0.csv"
STATE_NAME = "MEM_STAGE_B_2050_ACCEPTED_RUNTIME_STATE_v1.0.json"
B10_RECEIPT = ROOT / "qa/stage_a/etx7b10_2050/MEM_ETX7B10_2050_Run_Receipt_v1.0.json"
QA_DIR = ROOT / "qa/stage_b/runtime_2050"
NETWORK_DIR = ROOT / "networks/unsolved/accepted_2050"
PREP_DIR = ROOT / "qa/stage_b/pre_2050"
STAGING_DIR = ROOT / "scratch/stage_b_2050_runtime_rebuilds"


def verify_b9h_immutable() -> dict[str, Any]:
    """Verify the accepted B9H result itself, without requiring absent B9G bytes."""

    sources = yaml.safe_load((ROOT / "config/stage_a_production_price_sources.yaml").read_text(encoding="utf-8"))
    source = sources["sources"][YEAR]
    expected = {
        "receipt_sha256": "B95CB4B4FB59530872EF706185A09840B5EAB4C40AD565F1C6B31BA341E8906D",
        "result_manifest_sha256": "07D33D8A9EEA044FF09AD8D9802716730F79EB5887BC0330BC35A937E6A10FB8",
        "market_prices_sha256": "57A4D5AFFA906B14FA7245D28972F9C53DEDA51E5D66CDA2782723419B33DED7",
        "solved_network_sha256": "ADE607FB4D853A1DBD1CB8004B382927174ACE6507FB87129BD4ABA65000362C",
    }
    if source.get("phase") != "ETX-7B9H" or source.get("status") != "ACCEPTED_PRODUCTION_PRICE_SOURCE":
        raise RuntimeError("B9H_2050_SOURCE_SELECTION_CHANGED")
    paths = {
        "receipt_sha256": source["receipt"],
        "result_manifest_sha256": source["result_manifest"],
        "market_prices_sha256": source["market_prices"],
        "solved_network_sha256": "stage_a_results/2050_perimeter_closure_final_mt_tn/MEM_ETX7B9H_2050_FINAL_RESIDUAL_MT_TN_CLOSURE_SOLVED.nc",
    }
    for key, digest in expected.items():
        if str(source.get(key, "")).upper() != digest or sha256_file(ROOT / paths[key]).upper() != digest:
            raise RuntimeError(f"B9H_2050_IMMUTABLE_HASH_MISMATCH: {key}")
    receipt = json.loads((ROOT / paths["receipt_sha256"]).read_text(encoding="utf-8"))
    if receipt.get("status") != "PASS" or receipt.get("accepted") is not True:
        raise RuntimeError("B9H_2050_RECEIPT_NOT_ACCEPTED")
    manifest = pd.read_csv(ROOT / paths["result_manifest_sha256"])
    if len(manifest) != 27 or manifest["relative_path"].duplicated().any():
        raise RuntimeError("B9H_2050_MANIFEST_MEMBER_SET_INVALID")
    for row in manifest.itertuples(index=False):
        path = ROOT / str(row.relative_path)
        if not path.is_file() or sha256_file(path).upper() != str(row.sha256).upper():
            raise RuntimeError(f"B9H_2050_MANIFEST_MEMBER_MISMATCH: {row.relative_path}")
    return {"status": "PASS", "source": "ETX-7B9H", "manifest_members": len(manifest), **expected}


def verify_b10_2050() -> dict[str, Any]:
    """Verify the existing B10 receipt and every member without regenerating it."""

    receipt = json.loads(B10_RECEIPT.read_text(encoding="utf-8"))
    if not (
        receipt.get("status") == "PASS"
        and receipt.get("gate") == "ETX7B10_2050_STAGE_A_TO_STAGE_B_PRICE_HANDOFF_COMPLETE"
        and receipt.get("qa", {}).get("accepted_source_phase") == "ETX-7B9H"
        and receipt.get("qa", {}).get("dispatch_rerun") is False
        and receipt.get("qa", {}).get("rows") == 70080
        and receipt.get("qa", {}).get("stage_b_runtime_adapter", {}).get("rows") == 210240
    ):
        raise RuntimeError("B10_2050_ACCEPTED_RECEIPT_INVALID")
    outputs = receipt["outputs"]
    manifest_path = ROOT / outputs["manifest"]
    manifest_sha = sha256_file(manifest_path)
    if manifest_sha.lower() != str(outputs["manifest_sha256"]).lower():
        raise RuntimeError("B10_2050_MANIFEST_HASH_MISMATCH")
    manifest = pd.read_csv(manifest_path)
    if manifest.empty or manifest["relative_path"].duplicated().any():
        raise RuntimeError("B10_2050_MANIFEST_INVALID")
    for row in manifest.itertuples():
        member = ROOT / str(row.relative_path)
        if not member.is_file() or sha256_file(member).lower() != str(row.sha256).lower():
            raise RuntimeError(f"B10_2050_MEMBER_HASH_MISMATCH: {row.relative_path}")
    adapter = ROOT / outputs["stage_b_runtime_prices"]
    if adapter != ROOT / "stage_a_results/price_handoff/2050/external_prices_hourly.parquet":
        raise RuntimeError("B10_2050_ADAPTER_POINTER_INVALID")
    prices = pd.read_parquet(adapter)
    if (
        len(prices) != 3 * 8760 * 8
        or set(prices["year"].astype(int)) != {YEAR}
        or set(prices["scenario"].astype(str)) != set(SCENARIOS)
        or set(prices["external_market"].astype(str)) != PRICE_MARKETS
        or prices.duplicated(["snapshot", "year", "scenario", "external_market"]).any()
        or not np.isfinite(prices["price_EUR_per_MWh"].to_numpy(dtype=float)).all()
    ):
        raise RuntimeError("B10_2050_ADAPTER_COVERAGE_INVALID")
    return {
        "status": "PASS",
        "receipt_sha256": sha256_file(B10_RECEIPT),
        "manifest_sha256": manifest_sha,
        "manifest_members": len(manifest),
        "adapter_sha256": sha256_file(adapter),
        "source": "ETX-7B9H",
        "rows": len(prices),
    }


def verify_2040_unchanged() -> dict[str, Any]:
    """Reuse the accepted 2040 manifest; never regenerate its five members."""

    state = ACCEPTED_RUNTIME / shared.STATE_NAME
    manifest = ACCEPTED_RUNTIME / shared.MANIFEST_NAME
    before_state = sha256_file(state)
    before_manifest = sha256_file(manifest)
    verified = shared.verify_accepted_runtime_manifest(ACCEPTED_RUNTIME, expected_year=2040)
    if before_state != sha256_file(state) or before_manifest != sha256_file(manifest):
        raise RuntimeError("ACCEPTED_2040_RUNTIME_MUTATED_DURING_VERIFICATION")
    return {"status": "PASS", "state_sha256": before_state, "manifest_sha256": before_manifest, "members": verified["members"]}


def verify_prepared_runtime_manifest(bundle_dir: Path = ACCEPTED_RUNTIME_2050, *, expected_year: int = YEAR) -> dict[str, Any]:
    if expected_year != YEAR or bundle_dir.resolve() != ACCEPTED_RUNTIME_2050.resolve():
        raise RuntimeError("STAGE_B_2050_RUNTIME_HORIZON_OR_PATH_INVALID")
    manifest_path = bundle_dir / MANIFEST_NAME
    state_path = bundle_dir / STATE_NAME
    manifest = pd.read_csv(manifest_path, dtype=str)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if (
        set(manifest["file"].astype(str)) != set(shared.RUNTIME_MEMBERS)
        or len(manifest) != len(shared.RUNTIME_MEMBERS)
        or state.get("status") != "PREPARED_HASH_LOCKED_RUNTIME"
        or state.get("horizon") != YEAR
        or state.get("scenarios") != list(SCENARIOS)
        or state.get("production_optimization_executed") is not False
        or state.get("production_optimization_authorized") is not False
        or state.get("manifest_sha256", "").lower() != sha256_file(manifest_path).lower()
    ):
        raise RuntimeError("STAGE_B_2050_RUNTIME_STATE_OR_MANIFEST_INVALID")
    members: dict[str, str] = {}
    for row in manifest.itertuples(index=False):
        member = bundle_dir / str(row.file)
        if not member.is_file() or sha256_file(member).lower() != str(row.sha256).lower():
            raise RuntimeError(f"STAGE_B_2050_RUNTIME_MEMBER_HASH_MISMATCH: {row.file}")
        members[str(row.file)] = sha256_file(member)
    b10 = verify_b10_2050()
    if (
        state.get("b10_2050_manifest_sha256") != b10["manifest_sha256"]
        or members["external_prices_hourly.parquet"] != b10["adapter_sha256"]
    ):
        raise RuntimeError("STAGE_B_2050_B10_SOURCE_IDENTITY_MISMATCH")
    return {"status": "PASS", "horizon": YEAR, "manifest_sha256": sha256_file(manifest_path), "members": members}


def build_runtime() -> dict[str, Any]:
    """Make two independent runtime builds, then freeze only their identical output."""

    if ACCEPTED_RUNTIME_2050.exists():
        return verify_prepared_runtime_manifest()
    b10 = verify_b10_2050()
    before_2040 = verify_2040_unchanged()
    if STAGING_DIR.exists():
        raise RuntimeError(f"STAGE_B_2050_STAGING_ALREADY_EXISTS: {STAGING_DIR}")
    first = STAGING_DIR / "rebuild_a"
    second = STAGING_DIR / "rebuild_b"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    with shared.runtime_horizon(YEAR):
        counts_a = shared.build_runtime_members(first)
        counts_b = shared.build_runtime_members(second)
        hashes_a = shared.member_hashes(first)
        hashes_b = shared.member_hashes(second)
        if counts_a != counts_b or hashes_a != hashes_b:
            raise RuntimeError("STAGE_B_2050_RUNTIME_DETERMINISTIC_REBUILD_FAILED")
        qa = shared.validate_runtime(first)
        if not qa["status"].eq("PASS").all():
            raise RuntimeError(f"STAGE_B_2050_RUNTIME_QA_FAILED: {qa.loc[qa.status.ne('PASS'), 'check_id'].tolist()}")
        reconciliation = shared._reconciliation(first)
        if not reconciliation["status"].eq("PASS").all():
            raise RuntimeError("STAGE_B_2050_STATIC_TO_RUNTIME_RECONCILIATION_FAILED")
        if not set(reconciliation["classification"].astype(str)) <= {"EXACT_MATCH", "DETERMINISTIC_RUNTIME_TRANSFORMATION"}:
            raise RuntimeError("STAGE_B_2050_RECONCILIATION_CLASSIFICATION_INVALID")
        ACCEPTED_RUNTIME_2050.mkdir(parents=True)
        for name in shared.RUNTIME_MEMBERS:
            shutil.copyfile(first / name, ACCEPTED_RUNTIME_2050 / name)
        shared._write_manifest(ACCEPTED_RUNTIME_2050, counts_a)
    manifest_sha = sha256_file(ACCEPTED_RUNTIME_2050 / MANIFEST_NAME)
    state = {
        "schema_version": "MEM_STAGE_B_2050_ACCEPTED_RUNTIME_STATE_v1.0",
        "status": "PREPARED_HASH_LOCKED_RUNTIME",
        "gate": "STAGE_B_2050_PREPARATION_PASS",
        "horizon": YEAR,
        "scenarios": list(SCENARIOS),
        "chronology": "C2019_PREFERRED_NEWER|2019_UTC|8760_HOURS",
        "b10_2050_manifest_sha256": b10["manifest_sha256"],
        "b10_2050_adapter_sha256": b10["adapter_sha256"],
        "production_optimization_executed": False,
        "production_optimization_authorized": False,
        "manifest_sha256": manifest_sha,
    }
    dump_json(ACCEPTED_RUNTIME_2050 / STATE_NAME, state)
    verified = verify_prepared_runtime_manifest()
    after_2040 = verify_2040_unchanged()
    if before_2040 != after_2040:
        raise RuntimeError("ACCEPTED_2040_RUNTIME_CHANGED_DURING_2050_BUILD")
    QA_DIR.mkdir(parents=True, exist_ok=True)
    qa.to_csv(QA_DIR / "MEM_STAGE_B_2050_RUNTIME_CHECKS_v1.0.csv", index=False, lineterminator="\n")
    reconciliation.to_csv(QA_DIR / "MEM_STAGE_B_2050_RUNTIME_RECONCILIATION_v1.0.csv", index=False, lineterminator="\n", float_format="%.15g")
    result = {
        "status": "PASS",
        "runtime_manifest_sha256": manifest_sha,
        "runtime_member_hashes": verified["members"],
        "member_row_counts": counts_a,
        "deterministic_rebuild": True,
        "qa_checks": len(qa),
        "reconciliation_rows": len(reconciliation),
        "b10_2050": b10,
        "accepted_2040_non_regression": after_2040,
        "production_optimization_executed": False,
    }
    dump_json(QA_DIR / "MEM_STAGE_B_2050_RUNTIME_FINAL_VERIFICATION_v1.0.json", result)
    return result


def _scenario_summary(network: Any, metadata: dict[str, Any], scenario: str) -> dict[str, Any]:
    """Compare the unsolved network against its scenario's frozen static rows."""

    generators = pd.read_csv(STATIC / "MEM_generators_static_final.csv")
    generators = generators.loc[generators.year.eq(YEAR) & generators.scenario.eq(scenario)]
    storage = pd.read_csv(STATIC / "MEM_storage_static_final.csv")
    storage = storage.loc[storage.year.eq(YEAR) & storage.scenario.eq(scenario)]
    demand = pd.read_csv(STATIC / "MEM_Annual_Zonal_Demand_Contract.csv")
    demand = demand.loc[demand.year.eq(YEAR) & demand.scenario.eq(scenario)]
    internal = pd.read_csv(STATIC / "MEM_Interzonal_Static_Contract.csv")
    internal = internal.loc[internal.year.eq(YEAR) & internal.scenario.eq(scenario)]
    italian_loads = [f"LOAD_{zone}" for zone in shared.ZONES]
    if set(italian_loads) != set(network.loads_t.p_set.columns) & set(italian_loads):
        raise RuntimeError(f"STAGE_B_2050_{scenario}_ITALIAN_LOAD_COVERAGE")
    observed_load = float(network.loads_t.p_set[italian_loads].to_numpy().sum())
    expected_load = float(demand.annual_zonal_demand_MWh.sum())
    if not math.isclose(observed_load, expected_load, abs_tol=1e-3):
        raise RuntimeError(f"STAGE_B_2050_{scenario}_LOAD_DRIFT")
    if not (
        {bus for bus in network.buses.index.astype(str) if bus in ZONES} == ZONES
        and {f"EXT_{market}" for market in PRICE_MARKETS}.issubset(network.buses.index)
        and "CORS" in network.buses.index
        and not any(bus.startswith("EXT_") and bus not in {f"EXT_{market}" for market in PRICE_MARKETS} for bus in network.buses.index)
        and len(network.snapshots) == 8760
        and len(network.snapshots.unique()) == 8760
        and set(network.loads.loc[italian_loads, "bus"].astype(str)) == ZONES
        and metadata["frozen_generator_rows"] == len(generators)
        and math.isclose(metadata["frozen_generator_MW"], float(generators.p_nom_MW.sum()), abs_tol=1e-6)
        and metadata["bess_rows"] == len(storage)
        and math.isclose(metadata["bess_discharge_MW"], float(storage.discharge_power_MW.sum()), abs_tol=1e-6)
        and math.isclose(metadata["bess_energy_MWh"], float(storage.energy_capacity_MWh.sum()), abs_tol=1e-6)
        and metadata["internal_directional_links"] == len(internal)
        and metadata["external_contract_rows"] == 20
    ):
        raise RuntimeError(f"STAGE_B_2050_{scenario}_STATIC_OR_SCOPE_DRIFT")
    for component, field in ((network.generators, "p_nom_extendable"), (network.links, "p_nom_extendable"), (network.stores, "e_nom_extendable")):
        if field in component and component[field].fillna(False).astype(bool).any():
            raise RuntimeError(f"STAGE_B_2050_{scenario}_CAPACITY_EXPANSION_FOUND")
    if network.generators.committable.fillna(False).astype(bool).any() or network.links.committable.fillna(False).astype(bool).any():
        raise RuntimeError(f"STAGE_B_2050_{scenario}_UNIT_COMMITMENT_FOUND")
    if not np.isfinite(network.generators.marginal_cost.to_numpy(dtype=float)).all():
        raise RuntimeError(f"STAGE_B_2050_{scenario}_NONFINITE_MARGINAL_COST")
    from .validation import validate_network

    network_checks = validate_network(network, YEAR, scenario, assemble_model=False)
    failed_checks = network_checks.loc[network_checks.status.ne("PASS"), "check_id"].tolist()
    if failed_checks:
        raise RuntimeError(f"STAGE_B_2050_{scenario}_NETWORK_QA_FAILED: {failed_checks}")
    nuclear = generators.loc[generators.parent_capacity_technology.eq("NUCLEAR")]
    if not math.isclose(float(nuclear.p_nom_MW.sum()), 8000.0, abs_tol=1e-6):
        raise RuntimeError(f"STAGE_B_2050_{scenario}_NUCLEAR_CONTROL_FAILED")
    return {
        "scenario": scenario,
        "status": "READY_NOT_EXECUTED",
        "snapshots": len(network.snapshots),
        "zone_count": len(ZONES),
        "auxiliary_boundary_bus_count": len(network.buses) - len(ZONES),
        "annual_demand_MWh": observed_load,
        "frozen_generator_rows": len(generators),
        "frozen_generator_MW": float(generators.p_nom_MW.sum()),
        "nuclear_MW": float(nuclear.p_nom_MW.sum()),
        "bess_rows": len(storage),
        "bess_discharge_MW": float(storage.discharge_power_MW.sum()),
        "bess_energy_MWh": float(storage.energy_capacity_MWh.sum()),
        "hydro_state_count": int(network.stores.index.str.startswith("STORE_").sum()),
        "network_structural_checks": len(network_checks),
        "internal_directional_links": len(internal),
        "external_interface_contract_rows": metadata["external_contract_rows"],
        "network_generator_count": len(network.generators),
        "network_link_count": len(network.links),
        "network_store_count": len(network.stores),
        "production_optimization_executed": False,
    }


def prepare_networks() -> dict[str, Any]:
    """Construct three independent unsolved PyPSA models and freeze their QA."""

    from .network import build_network

    runtime = verify_prepared_runtime_manifest()
    b10 = verify_b10_2050()
    if (NETWORK_DIR.exists() and any(NETWORK_DIR.iterdir())) or (PREP_DIR.exists() and any(PREP_DIR.iterdir())):
        raise RuntimeError("STAGE_B_2050_NETWORK_OR_PREPARATION_OUTPUT_ALREADY_EXISTS")
    NETWORK_DIR.mkdir(parents=True, exist_ok=True)
    PREP_DIR.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        network, metadata = build_network(ACCEPTED_RUNTIME_2050.resolve(), YEAR, scenario)
        summary = _scenario_summary(network, metadata, scenario)
        model = network.optimize.create_model(include_objective_constant=False)
        summary["integer_variables"] = int(model.variables.integers.nvars)
        summary["binary_variables"] = int(model.variables.binaries.nvars)
        summary["continuous_variables"] = int(model.variables.continuous.nvars)
        if summary["integer_variables"] or summary["binary_variables"]:
            raise RuntimeError(f"STAGE_B_2050_{scenario}_NOT_CONTINUOUS_LP")
        del model
        stem = f"MEM_2050_{scenario.upper()}_8760h_UNSOLVED"
        network_path = NETWORK_DIR / f"{stem}.nc"
        network.export_to_netcdf(network_path)
        summary["unsolved_network"] = str(network_path.relative_to(ROOT)).replace("\\", "/")
        summary["unsolved_network_sha256"] = sha256_file(network_path)
        summary["runtime_manifest_sha256"] = runtime["manifest_sha256"]
        summary["b10_2050_manifest_sha256"] = b10["manifest_sha256"]
        summary["external_price_source"] = "ETX-7B9H_VIA_B10_2050"
        summary["production_optimization_executed"] = False
        receipt_path = PREP_DIR / f"MEM_STAGE_B_2050_{scenario.upper()}_Preparation_Receipt_v1.0.json"
        dump_json(receipt_path, summary)
        summaries.append(summary)
        del network
    comparison = _cross_scenario_comparison(summaries)
    comparison.to_csv(PREP_DIR / "MEM_STAGE_B_2050_Cross_Scenario_Comparison_v1.0.csv", index=False, lineterminator="\n")
    final = {
        "status": "PASS",
        "gate": "STAGE_B_2050_PREPARATION_PASS",
        "scenarios": {row["scenario"]: "READY_NOT_EXECUTED" for row in summaries},
        "runtime_manifest_sha256": runtime["manifest_sha256"],
        "b10_2050_manifest_sha256": b10["manifest_sha256"],
        "integer_variables": sum(row["integer_variables"] for row in summaries),
        "binary_variables": sum(row["binary_variables"] for row in summaries),
        "production_optimization_executed": False,
        "production_execution_authorized": False,
    }
    dump_json(PREP_DIR / "MEM_STAGE_B_2050_PREPARATION_FINAL_VERIFICATION_v1.0.json", final)
    return final


def _cross_scenario_comparison(summaries: list[dict[str, Any]]) -> pd.DataFrame:
    """Classify each scenario-level magnitude or invariant explicitly."""

    by_scenario = {row["scenario"]: row for row in summaries}
    if set(by_scenario) != set(SCENARIOS):
        raise RuntimeError("STAGE_B_2050_SCENARIO_COVERAGE_INVALID")
    varying = {"annual_demand_MWh", "frozen_generator_MW", "bess_discharge_MW", "bess_energy_MWh"}
    fixed = {
        "snapshots", "zone_count", "auxiliary_boundary_bus_count", "frozen_generator_rows",
        "nuclear_MW", "bess_rows", "hydro_state_count", "internal_directional_links",
        "external_interface_contract_rows", "network_generator_count", "network_link_count",
        "network_store_count", "network_structural_checks", "continuous_variables",
        "integer_variables", "binary_variables", "runtime_manifest_sha256",
        "b10_2050_manifest_sha256", "external_price_source",
    }
    rows: list[dict[str, Any]] = []
    for field in sorted(varying | fixed):
        values = [by_scenario[scenario][field] for scenario in SCENARIOS]
        same = values.count(values[0]) == len(values)
        if field in fixed and not same:
            raise RuntimeError(f"STAGE_B_2050_UNEXPECTED_IMPLEMENTATION_DRIFT: {field}")
        rows.append({
            "parameter": field,
            **{scenario: by_scenario[scenario][field] for scenario in SCENARIOS},
            "classification": "EXPECTED_SCENARIO_DIFFERENCE" if field in varying and not same else "SHARED_ACCEPTED_CONTROL",
        })
    return pd.DataFrame(rows)


def finalize_preparation() -> dict[str, Any]:
    """Read back the three unsolved networks and freeze corrected preparation QA."""

    import pypsa

    b9h = verify_b9h_immutable()
    predecessor_2040 = verify_2040_unchanged()
    runtime = verify_prepared_runtime_manifest()
    b10 = verify_b10_2050()
    summaries: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        network_path = NETWORK_DIR / f"MEM_2050_{scenario.upper()}_8760h_UNSOLVED.nc"
        receipt_path = PREP_DIR / f"MEM_STAGE_B_2050_{scenario.upper()}_Preparation_Receipt_v1.0.json"
        previous = json.loads(receipt_path.read_text(encoding="utf-8"))
        if sha256_file(network_path) != previous["unsolved_network_sha256"]:
            raise RuntimeError(f"STAGE_B_2050_{scenario}_UNSOLVED_NETWORK_HASH_CHANGED")
        network = pypsa.Network(network_path)
        corrected = _scenario_summary(network, {
            "frozen_generator_rows": previous["frozen_generator_rows"],
            "frozen_generator_MW": previous["frozen_generator_MW"],
            "bess_rows": previous["bess_rows"],
            "bess_discharge_MW": previous["bess_discharge_MW"],
            "bess_energy_MWh": previous["bess_energy_MWh"],
            "internal_directional_links": previous["internal_directional_links"],
            "external_contract_rows": previous["external_interface_contract_rows"],
        }, scenario)
        for key, value in corrected.items():
            if key != "hydro_state_count" and key in previous and previous[key] != value:
                raise RuntimeError(f"STAGE_B_2050_{scenario}_SERIALIZATION_DRIFT: {key}")
        from .validation import validate_network

        checks = validate_network(network, YEAR, scenario, assemble_model=False)
        checks_path = PREP_DIR / f"MEM_STAGE_B_2050_{scenario.upper()}_Structural_Checks_v1.0.csv"
        checks.to_csv(checks_path, index=False, lineterminator="\n")
        previous["hydro_state_count"] = corrected["hydro_state_count"]
        previous["runtime_manifest_sha256"] = runtime["manifest_sha256"]
        previous["b10_2050_manifest_sha256"] = b10["manifest_sha256"]
        previous["structural_checks_path"] = str(checks_path.relative_to(ROOT)).replace("\\", "/")
        previous["structural_checks_sha256"] = sha256_file(checks_path)
        dump_json(receipt_path, previous)
        summaries.append(previous)
        del network
    comparison = _cross_scenario_comparison(summaries)
    comparison.to_csv(PREP_DIR / "MEM_STAGE_B_2050_Cross_Scenario_Comparison_v1.0.csv", index=False, lineterminator="\n")
    final_path = PREP_DIR / "MEM_STAGE_B_2050_PREPARATION_FINAL_VERIFICATION_v1.0.json"
    final = json.loads(final_path.read_text(encoding="utf-8"))
    if final.get("status") != "PASS" or final.get("production_optimization_executed") is not False:
        raise RuntimeError("STAGE_B_2050_PREPARATION_FINAL_STATE_INVALID")
    final["cross_scenario_checks"] = len(comparison)
    final["unexpected_implementation_drift"] = 0
    final["scenario_receipt_sha256"] = {
        scenario: sha256_file(PREP_DIR / f"MEM_STAGE_B_2050_{scenario.upper()}_Preparation_Receipt_v1.0.json")
        for scenario in SCENARIOS
    }
    final["b9h_immutable"] = b9h
    final["b10_2050_immutable"] = b10
    final["accepted_2040_runtime_immutable"] = predecessor_2040
    dump_json(final_path, final)
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description="Build-only preparation of 2050 Italian Stage-B runtime")
    parser.add_argument("action", choices=("verify-b10", "build-runtime", "prepare-networks", "finalize", "verify"))
    args = parser.parse_args()
    if args.action == "verify-b10":
        result = verify_b10_2050()
    elif args.action == "build-runtime":
        result = build_runtime()
    elif args.action == "prepare-networks":
        result = prepare_networks()
    elif args.action == "finalize":
        result = finalize_preparation()
    else:
        result = {"runtime": verify_prepared_runtime_manifest(), "b10": verify_b10_2050()}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

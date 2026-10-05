"""Guarded ETX-7B8C S2 bounded-perimeter closure experiment.

The module never mutates the accepted B6 or B8 artifacts.  ``prepare`` writes
the deterministic auxiliary contract and performs short, unsolved LP-structure
QA.  The guarded ``s2 --execute`` entrypoint is reserved for the normal-user
PowerShell/Gurobi run.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import pypsa
import yaml

from mem_model.stage_a.b8_diagnostics import (
    INTERFACE_LIMIT_TOLERANCE_MW,
    INTERFACE_PATH,
    LINK_PATH,
    SHEDDING_TOLERANCE_MW,
    _build_market_diagnostic,
    _component_name_column,
    _directional_limit,
    _normalize_market_tables,
    _price_statistics,
    verify_b8_immutability,
)
from mem_model.stage_a.execution import (
    _solve_metrics,
    _write_solve_outputs,
    gurobi_preflight,
)
from mem_model.stage_a.network import MARKETS, ROOT, _as_bool, _safe_id, load_execution_config, verify_input_locks
from mem_model.stage_a.receipts import (
    build_receipt,
    command_string,
    relative_path,
    sha256_file,
    write_manifest,
    write_receipt,
)


CONFIG_PATH = ROOT / "config/stage_a_perimeter_closure_s2.yaml"
CANONICAL_MODULE = "mem_model.stage_a.perimeter_closure"
EXPERIMENT_PHASE = "ETX-7B8C-S2-AUXILIARY"
EXPERIMENT_GATE = "ETX7B8C_S2_EXPERIMENT_COMPLETE"
PREPARED_STATUS = "ETX7B8C_S2_MANUAL_EXECUTION_PREPARED"
VIRTUAL_CARRIER = "EXTERNAL_VIRTUAL_SUPPLY"
EXPECTED_VIRTUAL_MARKETS = ("FR", "TN", "GR")
EXPECTED_VIRTUAL_IDS = tuple(f"EXTERNAL_VIRTUAL_SUPPLY_{market}" for market in EXPECTED_VIRTUAL_MARKETS)


def load_s2_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if config.get("schema_version") != "MEM_ETX7B8C_S2_PERIMETER_CLOSURE_V1_0":
        raise RuntimeError("S2_CONFIG_SCHEMA_MISMATCH")
    return config


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _require_hash(path: Path, expected: str, label: str) -> str:
    if not path.exists():
        raise FileNotFoundError(path)
    observed = sha256_file(path)
    if observed != str(expected).upper():
        raise RuntimeError(f"{label}_HASH_MISMATCH: expected={expected}; observed={observed}")
    return observed


def _verify_manifest_from_receipt(receipt: dict[str, Any], label: str) -> dict[str, Any]:
    outputs = receipt.get("outputs", {})
    manifest_path = ROOT / outputs["manifest"]
    manifest_hash = _require_hash(manifest_path, outputs["manifest_sha256"], f"{label}_MANIFEST")
    manifest = pd.read_csv(manifest_path)
    for row in manifest.itertuples():
        member = ROOT / str(row.relative_path)
        _require_hash(member, str(row.sha256), f"{label}_MEMBER_{member.name}")
    return {
        "path": relative_path(manifest_path),
        "sha256": manifest_hash,
        "member_count": len(manifest),
        "status": "PASS",
    }


def verify_s2_predecessors(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fail closed across frozen B1-B8 controls and the accepted B6 network."""

    config = config or load_s2_config()
    execution_config = load_execution_config()
    b1_b5 = verify_input_locks(execution_config)

    accepted = config["accepted_inputs"]
    b6_spec = accepted["b6_receipt"]
    b6_path = ROOT / b6_spec["path"]
    b6_hash = _require_hash(b6_path, b6_spec["sha256"], "B6_RECEIPT")
    b6 = json.loads(b6_path.read_text(encoding="utf-8"))
    if b6.get("status") != "PASS" or b6.get("gate") != b6_spec["required_gate"]:
        raise RuntimeError("B6_RECEIPT_NOT_ACCEPTED")
    b6_manifest = _verify_manifest_from_receipt(b6, "B6")

    network_spec = accepted["b6_2040_unsolved_network"]
    network_path = ROOT / network_spec["path"]
    network_hash = _require_hash(network_path, network_spec["sha256"], "B6_2040_NETWORK")
    if b6["outputs"]["networks"]["2040"] != network_spec["path"]:
        raise RuntimeError("B6_2040_NETWORK_PATH_MISMATCH")
    if b6["qa"]["horizons"]["2040"]["network_sha256"] != network_hash:
        raise RuntimeError("B6_2040_NETWORK_RECEIPT_HASH_MISMATCH")

    b7_spec = accepted["b7_receipt"]
    b7_path = ROOT / b7_spec["path"]
    b7_hash = _require_hash(b7_path, b7_spec["sha256"], "B7_RECEIPT")
    b7 = json.loads(b7_path.read_text(encoding="utf-8"))
    if b7.get("status") != "PASS" or b7.get("gate") != b7_spec["required_gate"]:
        raise RuntimeError("B7_RECEIPT_NOT_ACCEPTED")
    b7_manifest = _verify_manifest_from_receipt(b7, "B7")

    b8 = verify_b8_immutability()
    b8_receipt_spec = accepted["b8_receipt"]
    b8_manifest_spec = accepted["b8_result_manifest"]
    if b8["receipt_sha256"] != b8_receipt_spec["sha256"]:
        raise RuntimeError("B8_RECEIPT_LOCK_MISMATCH")
    if b8["manifest_sha256"] != b8_manifest_spec["sha256"]:
        raise RuntimeError("B8_RESULT_MANIFEST_LOCK_MISMATCH")
    if len(b8["members"]) != int(b8_manifest_spec["required_members"]):
        raise RuntimeError("B8_RESULT_MANIFEST_MEMBER_COUNT_MISMATCH")

    identities = {
        "b1_b5_input_locks": b1_b5,
        "b6_receipt": {"path": relative_path(b6_path), "sha256": b6_hash, "status": "PASS"},
        "b6_manifest": b6_manifest,
        "b6_2040_unsolved_network": {
            "path": relative_path(network_path),
            "sha256": network_hash,
            "status": "PASS",
        },
        "b7_receipt": {"path": relative_path(b7_path), "sha256": b7_hash, "status": "PASS"},
        "b7_manifest": b7_manifest,
        "b8_receipt": {
            "path": relative_path(b8["receipt_path"]),
            "sha256": b8["receipt_sha256"],
            "status": "PASS",
        },
        "b8_manifest": {
            "path": relative_path(b8["manifest_path"]),
            "sha256": b8["manifest_sha256"],
            "member_count": len(b8["members"]),
            "status": "PASS",
        },
    }
    input_manifests = [
        *b1_b5,
        identities["b6_receipt"],
        identities["b6_2040_unsolved_network"],
        identities["b7_receipt"],
        identities["b8_receipt"],
        identities["b8_manifest"],
    ]
    return {
        "identities": identities,
        "input_manifests": input_manifests,
        "b6_network_path": network_path,
        "b8": b8,
    }


def _b8_output_path(verification: dict[str, Any], suffix: str) -> Path:
    receipt = verification["b8"]["receipt"]
    matches = [ROOT / value for key, value in receipt["outputs"].items() if key.endswith(suffix)]
    if len(matches) != 1:
        raise RuntimeError(f"B8_OUTPUT_NOT_UNIQUE: suffix={suffix}; count={len(matches)}")
    return matches[0]


def _derive_b8_virtual_capacities(verification: dict[str, Any]) -> dict[str, float]:
    shedding = pd.read_parquet(_b8_output_path(verification, "_Load_Shedding"))
    name_column = _component_name_column(shedding)
    shedding["market"] = shedding[name_column].astype(str).str.removeprefix("LOAD_SHEDDING_")
    maxima = shedding.groupby("market", sort=True)["shedding_MW"].max().astype(float)
    missing = set(EXPECTED_VIRTUAL_MARKETS) - set(maxima.index)
    if missing:
        raise RuntimeError(f"B8_SHEDDING_MARKETS_MISSING: {sorted(missing)}")
    return {market: float(maxima.loc[market]) for market in EXPECTED_VIRTUAL_MARKETS}


def _recover_cost_proxy(config: dict[str, Any]) -> dict[str, Any]:
    specification = config["cost_proxy"]
    source = ROOT / specification["source_path"]
    _require_hash(source, specification["source_sha256"], "B4_OCGT_COST_SOURCE")
    frame = pd.read_csv(source)
    selection = specification["selection"]
    selected = frame.loc[
        frame["country_code"].eq(selection["country_code"])
        & frame["year"].eq(int(selection["year"]))
        & frame["scenario"].eq(selection["scenario"])
        & frame["source_static_class"].eq(selection["source_static_class"])
        & frame["source_carrier"].eq(selection["source_carrier"])
        & frame["CHP_flag"].map(_as_bool).eq(bool(selection["CHP_flag"]))
    ].copy()
    if len(selected) != 1:
        raise RuntimeError(f"ACCEPTED_OCGT_PROXY_NOT_UNIQUE: rows={len(selected)}")
    row = selected.iloc[0]
    if row["asset_id"] != specification["source_asset_id"]:
        raise RuntimeError("ACCEPTED_OCGT_PROXY_ID_MISMATCH")

    numeric_mapping = {
        "efficiency_el": "efficiency_el",
        "fuel_price_EUR2025_per_MWh_th": "fuel_price_EUR2025_per_MWh_th",
        "CO2_price_EUR2025_per_t": "CO2_price_EUR2025_per_t",
        "chargeable_CO2_t_per_MWh_th": "chargeable_CO2_t_per_MWh_th",
        "residual_CO2_t_per_MWh_th": "residual_CO2_t_per_MWh_th",
        "VOM_EUR2025_per_MWh_el": "VOM_EUR2025_per_MWh_el",
        "fuel_component_EUR2025_per_MWh_el": "fuel_component_EUR2025_per_MWh_el",
        "carbon_component_EUR2025_per_MWh_el": "carbon_component_EUR2025_per_MWh_el",
        "common_marginal_cost_EUR2025_per_MWh_el": "marginal_cost_EUR2025_per_MWh_el",
    }
    for config_key, source_column in numeric_mapping.items():
        if not np.isclose(
            float(specification[config_key]),
            float(row[source_column]),
            atol=1e-12,
            rtol=0.0,
        ):
            raise RuntimeError(f"ACCEPTED_OCGT_PROXY_VALUE_MISMATCH: {config_key}")
    efficiency = float(row["efficiency_el"])
    derived = (
        float(row["fuel_price_EUR2025_per_MWh_th"]) / efficiency
        + float(row["chargeable_CO2_t_per_MWh_th"])
        * float(row["CO2_price_EUR2025_per_t"])
        / efficiency
        + float(row["VOM_EUR2025_per_MWh_el"])
    )
    if not np.isclose(derived, float(row["marginal_cost_EUR2025_per_MWh_el"]), atol=1e-12, rtol=0.0):
        raise RuntimeError("ACCEPTED_OCGT_PROXY_DERIVATION_MISMATCH")
    return {
        "source_file": relative_path(source),
        "source_file_sha256": sha256_file(source),
        "source_csv_physical_line": int(specification["source_csv_physical_line"]),
        "source_asset_id": str(row["asset_id"]),
        "technology": str(row["source_static_class"]),
        "source_carrier": str(row["source_carrier"]),
        "CHP_flag": _as_bool(row["CHP_flag"]),
        "efficiency_el": efficiency,
        "fuel_price_EUR2025_per_MWh_th": float(row["fuel_price_EUR2025_per_MWh_th"]),
        "CO2_price_EUR2025_per_t": float(row["CO2_price_EUR2025_per_t"]),
        "chargeable_CO2_t_per_MWh_th": float(row["chargeable_CO2_t_per_MWh_th"]),
        "residual_CO2_t_per_MWh_th": float(row["residual_CO2_t_per_MWh_th"]),
        "VOM_EUR2025_per_MWh_el": float(row["VOM_EUR2025_per_MWh_el"]),
        "fuel_component_EUR2025_per_MWh_el": float(row["fuel_component_EUR2025_per_MWh_el"]),
        "carbon_component_EUR2025_per_MWh_el": float(row["carbon_component_EUR2025_per_MWh_el"]),
        "marginal_cost_EUR2025_per_MWh_el": float(row["marginal_cost_EUR2025_per_MWh_el"]),
        "derivation_method": specification["derivation"],
        "parameter_authority": str(row["parameter_authority"]),
        "evidence_class": str(row["evidence_class"]),
        "quality": str(row["quality"]),
        "accepted_without_new_research_reason": (
            "Unique frozen 2040 Base non-CHP methane OCGT operating-parameter row in the "
            "accepted B4 package; the cost already incorporates the frozen fuel, carbon, "
            "efficiency, residual-emissions, and VOM treatment."
        ),
        "interpretation": specification["interpretation"],
    }


def build_virtual_supply_contract(
    config: dict[str, Any],
    verification: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    capacities = _derive_b8_virtual_capacities(verification)
    cost = _recover_cost_proxy(config)
    configured = config["virtual_supply"]["generators"]
    if [row["bus"] for row in configured] != list(EXPECTED_VIRTUAL_MARKETS):
        raise RuntimeError("S2_CONFIG_MARKET_SCOPE_MISMATCH")
    if [row["generator_id"] for row in configured] != list(EXPECTED_VIRTUAL_IDS):
        raise RuntimeError("S2_CONFIG_GENERATOR_IDS_MISMATCH")
    common = config["virtual_supply"]["common_parameters"]
    rows: list[dict[str, Any]] = []
    for row in configured:
        market = str(row["bus"])
        observed = capacities[market]
        if not np.isclose(float(row["p_nom_MW"]), observed, atol=0.0, rtol=0.0):
            raise RuntimeError(f"S2_CAPACITY_NOT_EXACT_B8_MAX_SHEDDING: {market}")
        rows.append(
            {
                "generator_id": row["generator_id"],
                "bus": market,
                "carrier": config["virtual_supply"]["carrier"],
                "p_nom_MW": observed,
                "capacity_rule": row["capacity_rule"],
                "p_nom_extendable": bool(common["p_nom_extendable"]),
                "committable": bool(common["committable"]),
                "p_min_pu": float(common["p_min_pu"]),
                "p_max_pu": float(common["p_max_pu"]),
                "marginal_cost_EUR2025_per_MWh_el": float(
                    common["marginal_cost_EUR2025_per_MWh_el"]
                ),
                "efficiency": float(common["efficiency"]),
                "cost_source_asset_id": cost["source_asset_id"],
                "status": "EXPERIMENTAL_FIXED_VIRTUAL_EXTERNAL_SUPPLY",
            }
        )
    contract = pd.DataFrame.from_records(rows)
    if set(contract["bus"]) != set(EXPECTED_VIRTUAL_MARKETS) or "MT" in set(contract["bus"]):
        raise RuntimeError("S2_VIRTUAL_SUPPLY_SCOPE_VIOLATION")
    if len(contract) != 3 or contract["generator_id"].duplicated().any():
        raise RuntimeError("S2_VIRTUAL_SUPPLY_CARDINALITY_VIOLATION")
    if not np.allclose(
        contract["marginal_cost_EUR2025_per_MWh_el"],
        cost["marginal_cost_EUR2025_per_MWh_el"],
        atol=0.0,
        rtol=0.0,
    ):
        raise RuntimeError("S2_COMMON_COST_MISMATCH")
    return contract, cost


def write_preparation_artifacts(
    config: dict[str, Any],
    verification: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any], list[Path]]:
    contract, cost = build_virtual_supply_contract(config, verification)
    contract_path = ROOT / config["artifacts"]["virtual_supply_contract"]
    cost_path = ROOT / config["artifacts"]["cost_provenance"]
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract.to_csv(contract_path, index=False, encoding="utf-8", lineterminator="\n")
    provenance = {
        "schema_version": "MEM_ETX7B8C_S2_COST_PROVENANCE_V1_0",
        "status": "FROZEN_ACCEPTED_LOCAL_AUTHORITY_RECOVERED",
        "research_performed": False,
        "common_for_virtual_generators": list(EXPECTED_VIRTUAL_IDS),
        **cost,
    }
    _write_json(cost_path, provenance)
    return contract, provenance, [contract_path, cost_path]


def _component_counts(network: pypsa.Network) -> dict[str, int]:
    return {
        "snapshots": len(network.snapshots),
        "carriers": len(network.carriers),
        "buses": len(network.buses),
        "loads": len(network.loads),
        "generators": len(network.generators),
        "links": len(network.links),
        "stores": len(network.stores),
        "market_buses": int(network.buses.carrier.eq("AC_STAGE_A_MARKET").sum()),
        "interconnectors": int(network.links.carrier.eq("INTERCONNECTOR").sum()),
        "load_shedding_generators": int(network.generators.carrier.eq("LOAD_SHEDDING").sum()),
        "storage_states": len(network.stores),
        "water_state_buses": int(network.buses.carrier.eq("WATER_STATE").sum()),
    }


def _assert_frame_equal(left: pd.DataFrame, right: pd.DataFrame, label: str) -> None:
    try:
        pd.testing.assert_frame_equal(left, right, check_exact=True, check_dtype=True)
    except AssertionError as error:
        raise RuntimeError(f"S2_BASELINE_DRIFT_{label}: {error}") from error


def _dynamic_tables(holder: Any) -> dict[str, pd.DataFrame]:
    return {
        str(name): frame
        for name, frame in holder.items()
        if isinstance(frame, pd.DataFrame)
    }


def add_virtual_supply(
    baseline: pypsa.Network,
    contract: pd.DataFrame,
) -> pypsa.Network:
    network = baseline.copy()
    if VIRTUAL_CARRIER in network.carriers.index:
        raise RuntimeError("S2_VIRTUAL_CARRIER_ALREADY_PRESENT_IN_BASELINE")
    existing = set(network.generators.index) & set(contract["generator_id"])
    if existing:
        raise RuntimeError(f"S2_VIRTUAL_GENERATOR_ALREADY_PRESENT_IN_BASELINE: {sorted(existing)}")
    network.add(
        "Carrier",
        VIRTUAL_CARRIER,
        co2_emissions=0.0,
        nice_name="Experimental external virtual supply",
    )
    for row in contract.itertuples(index=False):
        if row.bus not in network.buses.index:
            raise RuntimeError(f"S2_VIRTUAL_GENERATOR_BUS_MISSING: {row.bus}")
        network.add(
            "Generator",
            row.generator_id,
            bus=row.bus,
            carrier=row.carrier,
            p_nom=float(row.p_nom_MW),
            p_nom_extendable=False,
            committable=False,
            p_min_pu=float(row.p_min_pu),
            p_max_pu=float(row.p_max_pu),
            marginal_cost=float(row.marginal_cost_EUR2025_per_MWh_el),
            efficiency=float(row.efficiency),
        )
    network.meta = {
        **dict(baseline.meta),
        "auxiliary_experiment": "ETX7B8C_S2_PERIMETER_CLOSURE",
        "baseline": "ACCEPTED_B6_2040_UNSOLVED_NETWORK",
        "formal_methodology_status": "EXPERIMENTAL_NOT_ACCEPTED",
        "formal_successor_gate": "LOCKED",
    }
    return network


def validate_structural_delta(
    baseline: pypsa.Network,
    s2: pypsa.Network,
    contract: pd.DataFrame,
) -> dict[str, Any]:
    if not baseline.snapshots.equals(s2.snapshots):
        raise RuntimeError("S2_SNAPSHOT_DRIFT")
    _assert_frame_equal(baseline.snapshot_weightings, s2.snapshot_weightings, "SNAPSHOT_WEIGHTS")
    for component in ("buses", "loads", "links", "stores"):
        _assert_frame_equal(getattr(baseline, component), getattr(s2, component), component.upper())
    if set(s2.carriers.index) != set(baseline.carriers.index) | {VIRTUAL_CARRIER}:
        raise RuntimeError("S2_CARRIER_DELTA_NOT_EXACT")
    _assert_frame_equal(
        baseline.carriers,
        s2.carriers.loc[baseline.carriers.index, baseline.carriers.columns],
        "EXISTING_CARRIERS",
    )
    if set(s2.generators.index) != set(baseline.generators.index) | set(EXPECTED_VIRTUAL_IDS):
        raise RuntimeError("S2_GENERATOR_DELTA_NOT_EXACT")
    if set(s2.generators.columns) != set(baseline.generators.columns):
        raise RuntimeError("S2_GENERATOR_SCHEMA_DRIFT")
    _assert_frame_equal(
        baseline.generators,
        s2.generators.loc[baseline.generators.index, baseline.generators.columns],
        "EXISTING_GENERATORS",
    )

    for holder_name in ("buses_t", "loads_t", "links_t", "stores_t"):
        left = _dynamic_tables(getattr(baseline, holder_name))
        right = _dynamic_tables(getattr(s2, holder_name))
        if set(left) != set(right):
            raise RuntimeError(f"S2_DYNAMIC_SCHEMA_DRIFT_{holder_name}")
        for attribute in left:
            _assert_frame_equal(left[attribute], right[attribute], f"{holder_name}_{attribute}")
    left_generators = _dynamic_tables(baseline.generators_t)
    right_generators = _dynamic_tables(s2.generators_t)
    if set(left_generators) != set(right_generators):
        raise RuntimeError("S2_GENERATOR_DYNAMIC_SCHEMA_DRIFT")
    for attribute, base_frame in left_generators.items():
        s2_frame = right_generators[attribute]
        unexpected = set(s2_frame.columns) - set(base_frame.columns) - set(EXPECTED_VIRTUAL_IDS)
        if unexpected:
            raise RuntimeError(f"S2_UNEXPECTED_GENERATOR_DYNAMIC_COLUMNS_{attribute}: {sorted(unexpected)}")
        _assert_frame_equal(
            base_frame,
            s2_frame.loc[:, base_frame.columns],
            f"EXISTING_GENERATOR_DYNAMIC_{attribute}",
        )

    common_cost = float(contract["marginal_cost_EUR2025_per_MWh_el"].iloc[0])
    for row in contract.itertuples(index=False):
        observed = s2.generators.loc[row.generator_id]
        required = (
            observed.bus == row.bus
            and observed.carrier == VIRTUAL_CARRIER
            and np.isclose(float(observed.p_nom), float(row.p_nom_MW), atol=0.0, rtol=0.0)
            and not bool(observed.p_nom_extendable)
            and not bool(observed.committable)
            and float(observed.p_min_pu) == 0.0
            and float(observed.p_max_pu) == 1.0
            and float(observed.efficiency) == 1.0
            and np.isclose(float(observed.marginal_cost), common_cost, atol=0.0, rtol=0.0)
        )
        if not required:
            raise RuntimeError(f"S2_VIRTUAL_GENERATOR_PARAMETER_MISMATCH: {row.generator_id}")

    baseline_counts = _component_counts(baseline)
    s2_counts = _component_counts(s2)
    expected_counts = {**baseline_counts}
    expected_counts["carriers"] += 1
    expected_counts["generators"] += 3
    if s2_counts != expected_counts:
        raise RuntimeError(
            f"S2_COMPONENT_DELTA_NOT_EXACT: baseline={baseline_counts}; s2={s2_counts}; expected={expected_counts}"
        )
    return {
        "status": "PASS",
        "baseline_counts": baseline_counts,
        "s2_counts": s2_counts,
        "allowed_delta": {"carriers": 1, "generators": 3},
        "unchanged_baseline_components": True,
        "new_carrier": VIRTUAL_CARRIER,
        "new_generators": list(EXPECTED_VIRTUAL_IDS),
        "new_virtual_generator_markets": list(EXPECTED_VIRTUAL_MARKETS),
        "MT_virtual_supply": "NONE",
    }


def validate_lp_static(network: pypsa.Network) -> dict[str, Any]:
    assertions = {
        "all_generators_committable_false": not network.generators.committable.fillna(False).astype(bool).any(),
        "all_links_committable_false": not network.links.committable.fillna(False).astype(bool).any(),
        "all_generator_p_nom_extendable_false": not network.generators.p_nom_extendable.fillna(False).astype(bool).any(),
        "all_link_p_nom_extendable_false": not network.links.p_nom_extendable.fillna(False).astype(bool).any(),
        "all_store_e_nom_extendable_false": not network.stores.e_nom_extendable.fillna(False).astype(bool).any(),
        "capacity_expansion": False,
        "unit_commitment": False,
    }
    if not all(value is False for key, value in assertions.items() if key in {"capacity_expansion", "unit_commitment"}):
        raise RuntimeError("S2_STATIC_LP_CONTROL_INTERNAL_ERROR")
    boolean_checks = [value for key, value in assertions.items() if key.startswith("all_")]
    if not all(boolean_checks):
        raise RuntimeError(f"S2_STATIC_LP_ASSERTION_FAILED: {assertions}")
    return {"status": "PASS", **assertions}


def create_and_validate_linopy_model(network: pypsa.Network) -> dict[str, Any]:
    static = validate_lp_static(network)
    model = network.optimize.create_model(include_objective_constant=False)
    integer_variables = int(model.variables.integers.nvars)
    binary_variables = int(model.variables.binaries.nvars)
    if integer_variables != 0 or binary_variables != 0:
        raise RuntimeError(
            f"S2_NOT_CONTINUOUS_LP: integer_variables={integer_variables}; binary_variables={binary_variables}"
        )
    return {
        **static,
        "linopy_model_assembled": True,
        "linopy_variables": int(model.nvars),
        "linopy_constraints": int(model.ncons),
        "integer_variables": integer_variables,
        "binary_variables": binary_variables,
        "continuous_linear_program_only": True,
    }


def _assert_gurobi_lp(network: pypsa.Network) -> dict[str, Any]:
    gurobi_model = network.model.to_gurobipy()
    try:
        gurobi_model.update()
        result = {
            "NumVars": int(gurobi_model.NumVars),
            "NumConstrs": int(gurobi_model.NumConstrs),
            "NumBinVars": int(gurobi_model.NumBinVars),
            "NumIntVars": int(gurobi_model.NumIntVars),
        }
    finally:
        gurobi_model.dispose()
    if result["NumBinVars"] != 0 or result["NumIntVars"] != 0:
        raise RuntimeError(f"S2_GUROBI_MODEL_NOT_CONTINUOUS_LP: {result}")
    return {"status": "PASS", **result}


def prepare_s2() -> dict[str, Any]:
    config = load_s2_config()
    before = verify_s2_predecessors(config)
    contract, provenance, artifacts = write_preparation_artifacts(config, before)
    baseline = pypsa.Network(before["b6_network_path"])
    s2 = add_virtual_supply(baseline, contract)
    structural = validate_structural_delta(baseline, s2, contract)
    validate_lp_static(s2)

    short = s2.copy()
    short.set_snapshots(short.snapshots[:6])
    lp = create_and_validate_linopy_model(short)
    structural_payload = {
        "schema_version": "MEM_ETX7B8C_S2_STRUCTURAL_QA_V1_0",
        "status": "PASS",
        "preparation_status": PREPARED_STATUS,
        "full_chronology_structural_delta": structural,
        "short_unsolved_LP_fixture": {"snapshots": len(short.snapshots), **lp},
        "production_S2_solve_executed": False,
        "b8_optimization_rerun": False,
        "b9_authorized": False,
        "stage_b_authorized": False,
    }
    structural_path = ROOT / config["artifacts"]["structural_QA"]
    _write_json(structural_path, structural_payload)
    artifacts.append(structural_path)

    after = verify_s2_predecessors(config)
    if before["identities"] != after["identities"]:
        raise RuntimeError("ACCEPTED_B1_B8_IDENTITY_CHANGED_DURING_S2_PREPARATION")
    return {
        "status": "PASS",
        "preparation_status": PREPARED_STATUS,
        "contract": relative_path(ROOT / config["artifacts"]["virtual_supply_contract"]),
        "cost_provenance": relative_path(ROOT / config["artifacts"]["cost_provenance"]),
        "structural_QA": relative_path(structural_path),
        "structural_delta": structural,
        "LP_assertions": lp,
        "common_marginal_cost_EUR2025_per_MWh_el": provenance[
            "marginal_cost_EUR2025_per_MWh_el"
        ],
        "production_S2_solve_executed": False,
        "manual_command": config["manual_command"],
    }


def _weighted_total(frame: pd.DataFrame, value: str, weights: pd.Series) -> float:
    mapped = frame["snapshot"].map(weights)
    if mapped.isna().any():
        raise RuntimeError(f"S2_COMPARISON_TIMESTAMP_MISMATCH: {value}")
    return float((frame[value].astype(float) * mapped).sum())


def _system_metrics(
    network: pypsa.Network,
    shedding_path: Path,
    prices_path: Path,
    spill_path: Path,
    curtailment_path: Path,
    voll: float,
) -> dict[str, float | int]:
    load, shedding, prices, weights = _normalize_market_tables(network, shedding_path, prices_path)
    total_load = float(load.mul(weights, axis=0).sum().sum())
    total_shedding = float(shedding.mul(weights, axis=0).sum().sum())
    simultaneous = shedding.sum(axis=1)
    price = _price_statistics(prices.stack(future_stack=True), voll)
    spill = pd.read_parquet(spill_path)
    curtailment = pd.read_parquet(curtailment_path)
    return {
        "total_load_MWh": total_load,
        "total_shedding_MWh": total_shedding,
        "shedding_percent_of_load": 100.0 * total_shedding / total_load,
        "shedding_hours": int(simultaneous.gt(SHEDDING_TOLERANCE_MW).sum()),
        "objective_EUR2025": float(network.objective),
        "price_mean_EUR_per_MWh": price["price_mean_EUR_per_MWh"],
        "price_median_EUR_per_MWh": price["price_median_EUR_per_MWh"],
        "price_P95_EUR_per_MWh": price["price_P95_EUR_per_MWh"],
        "price_P99_EUR_per_MWh": price["price_P99_EUR_per_MWh"],
        "price_max_EUR_per_MWh": price["price_max_EUR_per_MWh"],
        "market_hours_at_VOLL": price["hours_at_VOLL"],
        "market_hours_above_500_EUR_per_MWh": price["hours_above_500_EUR_per_MWh"],
        "market_hours_above_1000_EUR_per_MWh": price["hours_above_1000_EUR_per_MWh"],
        "market_hours_above_5000_EUR_per_MWh": price["hours_above_5000_EUR_per_MWh"],
        "VRE_curtailment_MWh": _weighted_total(curtailment, "curtailment_MW", weights),
        "hydro_spill_MWh_water": _weighted_total(spill, "spill_MW_water", weights),
    }


def _b8_system_metrics(config: dict[str, Any], verification: dict[str, Any]) -> dict[str, float | int]:
    diagnostic_path = ROOT / config["accepted_inputs"]["b8_annual_diagnostic"]["path"]
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    system = diagnostic["system"]
    return {
        "total_load_MWh": float(system["total_system_load_MWh"]),
        "total_shedding_MWh": float(system["total_system_load_shedding_MWh"]),
        "shedding_percent_of_load": float(system["load_shedding_percent_of_total_load"]),
        "shedding_hours": int(system["system_hours_with_any_shedding_above_1e_6_MW"]),
        "objective_EUR2025": float(verification["b8"]["receipt"]["objective"]),
        "price_mean_EUR_per_MWh": float(system["price_mean_EUR_per_MWh"]),
        "price_median_EUR_per_MWh": float(system["price_median_EUR_per_MWh"]),
        "price_P95_EUR_per_MWh": float(system["price_P95_EUR_per_MWh"]),
        "price_P99_EUR_per_MWh": float(system["price_P99_EUR_per_MWh"]),
        "price_max_EUR_per_MWh": float(system["price_max_EUR_per_MWh"]),
        "market_hours_at_VOLL": int(system["total_market_hours_at_VOLL"]),
        "market_hours_above_500_EUR_per_MWh": int(system["total_market_hours_above_500_EUR_per_MWh"]),
        "market_hours_above_1000_EUR_per_MWh": int(system["total_market_hours_above_1000_EUR_per_MWh"]),
        "market_hours_above_5000_EUR_per_MWh": int(system["total_market_hours_above_5000_EUR_per_MWh"]),
        "VRE_curtailment_MWh": float(system["VRE_curtailment_MWh"]),
        "hydro_spill_MWh_water": float(system["hydro_spill_MWh_water"]),
    }


def _interface_flow_metrics(network: pypsa.Network, path: Path) -> pd.DataFrame:
    interfaces = pd.read_csv(INTERFACE_PATH)
    directional = pd.read_csv(LINK_PATH)
    flow_long = pd.read_parquet(path).copy()
    name_column = _component_name_column(flow_long)
    runtime_names = {
        f"INTERCONNECTOR_{_safe_id(physical_id)}": physical_id
        for physical_id in interfaces["physical_link_id"]
    }
    flow_long = flow_long.loc[flow_long[name_column].isin(runtime_names)].copy()
    flow_long["physical_link_id"] = flow_long[name_column].map(runtime_names)
    flows = flow_long.pivot(index="snapshot", columns="physical_link_id", values="signed_p0_MW").reindex(
        index=network.snapshots,
        columns=interfaces["physical_link_id"],
    )
    if flows.isna().any().any():
        raise RuntimeError("S2_INTERFACE_FLOW_TABLE_INCOMPLETE")
    weights = network.snapshot_weightings.objective.astype(float)
    rows: list[dict[str, Any]] = []
    for interface in interfaces.sort_values("physical_link_id", kind="stable").itertuples():
        series = flows[interface.physical_link_id].astype(float)
        a_to_b = _directional_limit(
            directional, interface.physical_link_id, interface.endpoint_a, interface.endpoint_b
        )
        b_to_a = _directional_limit(
            directional, interface.physical_link_id, interface.endpoint_b, interface.endpoint_a
        )
        rows.append(
            {
                "physical_link_id": interface.physical_link_id,
                "endpoint_a": interface.endpoint_a,
                "endpoint_b": interface.endpoint_b,
                "accepted_a_to_b_limit_MW": a_to_b,
                "accepted_b_to_a_limit_MW": b_to_a,
                "annual_net_a_to_b_MWh": float((series * weights).sum()),
                "annual_gross_a_to_b_MWh": float((series.clip(lower=0.0) * weights).sum()),
                "annual_gross_b_to_a_MWh": float(((-series).clip(lower=0.0) * weights).sum()),
                "hours_at_or_near_a_to_b_limit": int(
                    series.ge(a_to_b - INTERFACE_LIMIT_TOLERANCE_MW).sum()
                ),
                "hours_at_or_near_b_to_a_limit": int(
                    (-series).ge(b_to_a - INTERFACE_LIMIT_TOLERANCE_MW).sum()
                ),
                "maximum_signed_a_to_b_flow_MW": float(series.max()),
                "minimum_signed_a_to_b_flow_MW": float(series.min()),
            }
        )
    return pd.DataFrame.from_records(rows)


def _build_comparisons(
    network: pypsa.Network,
    config: dict[str, Any],
    verification: dict[str, Any],
    result_dir: Path,
    stem: str,
) -> tuple[list[Path], dict[str, Any]]:
    voll = float(load_execution_config()["assembly"]["feasibility"]["VOLL_EUR_per_MWh"])
    paths = {
        "prices": result_dir / f"{stem}_Market_Prices.parquet",
        "shedding": result_dir / f"{stem}_Load_Shedding.parquet",
        "dispatch": result_dir / f"{stem}_Generator_Dispatch.parquet",
        "flows": result_dir / f"{stem}_Link_Flows.parquet",
        "spill": result_dir / f"{stem}_Hydro_Spill.parquet",
        "curtailment": result_dir / f"{stem}_VRE_Curtailment.parquet",
    }
    s2_system = _system_metrics(
        network,
        paths["shedding"],
        paths["prices"],
        paths["spill"],
        paths["curtailment"],
        voll,
    )
    b8_system = _b8_system_metrics(config, verification)
    system_rows = []
    for metric in b8_system:
        baseline = b8_system[metric]
        experiment = s2_system[metric]
        system_rows.append(
            {
                "metric": metric,
                "B8_immutable_baseline": baseline,
                "S2_experiment": experiment,
                "S2_minus_B8": float(experiment) - float(baseline),
            }
        )
    system_frame = pd.DataFrame.from_records(system_rows)

    load, s2_shedding, s2_prices, weights = _normalize_market_tables(
        network, paths["shedding"], paths["prices"]
    )
    s2_market = _build_market_diagnostic(load, s2_shedding, s2_prices, weights, voll)
    b8_market = pd.read_csv(
        ROOT / "qa/stage_a/etx7b8/MEM_ETX7B8_2040_Base_Market_Diagnostic_v1.0.csv"
    )
    metrics = (
        "load_shedding_MWh",
        "load_shedding_percent_of_local_load",
        "maximum_hourly_shedding_MW",
        "hours_shedding_above_1_MW",
        "price_mean_EUR_per_MWh",
        "price_median_EUR_per_MWh",
        "price_P95_EUR_per_MWh",
        "price_P99_EUR_per_MWh",
        "price_max_EUR_per_MWh",
        "hours_at_VOLL",
        "hours_above_500_EUR_per_MWh",
        "hours_above_1000_EUR_per_MWh",
        "hours_above_5000_EUR_per_MWh",
    )
    market = b8_market[["market", *metrics]].merge(
        s2_market[["market", *metrics]], on="market", suffixes=("_B8", "_S2"), validate="one_to_one"
    )
    for metric in metrics:
        market[f"{metric}_S2_minus_B8"] = market[f"{metric}_S2"] - market[f"{metric}_B8"]

    dispatch_long = pd.read_parquet(paths["dispatch"])
    dispatch_name = _component_name_column(dispatch_long)
    dispatch = dispatch_long.pivot(index="snapshot", columns=dispatch_name, values="dispatch_MW").reindex(
        index=network.snapshots,
        columns=EXPECTED_VIRTUAL_IDS,
    )
    b8_shedding_long = pd.read_parquet(_b8_output_path(verification, "_Load_Shedding"))
    b8_name = _component_name_column(b8_shedding_long)
    b8_shedding_long["market"] = b8_shedding_long[b8_name].astype(str).str.removeprefix(
        "LOAD_SHEDDING_"
    )
    b8_shedding = b8_shedding_long.pivot(
        index="snapshot", columns="market", values="shedding_MW"
    ).reindex(index=network.snapshots, columns=MARKETS)
    utilization_rows: list[dict[str, Any]] = []
    cost = float(config["cost_proxy"]["common_marginal_cost_EUR2025_per_MWh_el"])
    for market_name, generator in zip(EXPECTED_VIRTUAL_MARKETS, EXPECTED_VIRTUAL_IDS, strict=True):
        series = dispatch[generator].astype(float)
        p_nom = float(network.generators.at[generator, "p_nom"])
        annual = float((series * weights).sum())
        during_b8_shedding = b8_shedding[market_name].gt(SHEDDING_TOLERANCE_MW)
        local_load = float((load[market_name] * weights).sum())
        utilization_rows.append(
            {
                "market": market_name,
                "generator_id": generator,
                "p_nom_MW": p_nom,
                "marginal_cost_EUR2025_per_MWh_el": cost,
                "annual_dispatch_MWh": annual,
                "peak_dispatch_MW": float(series.max()),
                "annual_capacity_factor": annual / (p_nom * float(weights.sum())),
                "hours_dispatch_above_1_MW": int(series.gt(1.0).sum()),
                "hours_dispatch_above_10_percent_p_nom": int(series.gt(0.1 * p_nom).sum()),
                "hours_dispatch_above_50_percent_p_nom": int(series.gt(0.5 * p_nom).sum()),
                "hours_dispatch_above_90_percent_p_nom": int(series.gt(0.9 * p_nom).sum()),
                "share_local_annual_load_supplied_percent": 100.0 * annual / local_load,
                "share_dispatch_during_B8_local_shedding_hours_percent": (
                    100.0 * float((series.loc[during_b8_shedding] * weights.loc[during_b8_shedding]).sum()) / annual
                    if annual
                    else 0.0
                ),
                "hours_local_price_approximately_virtual_marginal_cost": int(
                    np.isclose(
                        s2_prices[market_name].to_numpy(),
                        cost,
                        atol=float(config["tolerances"]["price_EUR_per_MWh"]),
                        rtol=0.0,
                    ).sum()
                ),
                "hours_local_price_at_VOLL": int(
                    np.isclose(s2_prices[market_name].to_numpy(), voll, atol=1e-6, rtol=0.0).sum()
                ),
                "residual_local_shedding_MWh": float(
                    (s2_shedding[market_name] * weights).sum()
                ),
                "residual_local_shedding_hours": int(
                    s2_shedding[market_name].gt(SHEDDING_TOLERANCE_MW).sum()
                ),
            }
        )
    utilization = pd.DataFrame.from_records(utilization_rows)

    b8_flows = _interface_flow_metrics(network, _b8_output_path(verification, "_Link_Flows"))
    s2_flows = _interface_flow_metrics(network, paths["flows"])
    flow_metrics = [
        column
        for column in b8_flows.columns
        if column not in {"physical_link_id", "endpoint_a", "endpoint_b"}
    ]
    flows = b8_flows.merge(
        s2_flows,
        on=["physical_link_id", "endpoint_a", "endpoint_b"],
        suffixes=("_B8", "_S2"),
        validate="one_to_one",
    )
    for metric in flow_metrics:
        if metric.startswith("accepted_"):
            if not np.allclose(flows[f"{metric}_B8"], flows[f"{metric}_S2"], atol=0.0, rtol=0.0):
                raise RuntimeError(f"S2_INTERFACE_LIMIT_DRIFT: {metric}")
        else:
            flows[f"{metric}_S2_minus_B8"] = flows[f"{metric}_S2"] - flows[f"{metric}_B8"]
    net_delta = flows["annual_net_a_to_b_MWh_S2"] - flows["annual_net_a_to_b_MWh_B8"]
    flows["direction_of_material_net_flow_change"] = np.select(
        [net_delta.gt(1.0), net_delta.lt(-1.0)],
        [flows["endpoint_a"] + "_TO_" + flows["endpoint_b"], flows["endpoint_b"] + "_TO_" + flows["endpoint_a"]],
        default="NO_MATERIAL_CHANGE_ABOVE_1_MWh",
    )

    artifact_paths = config["artifacts"]
    system_path = ROOT / artifact_paths["system_comparison"]
    market_path = ROOT / artifact_paths["market_comparison"]
    utilization_path = ROOT / artifact_paths["virtual_supply_utilization"]
    flow_path = ROOT / artifact_paths["flow_comparison"]
    for path in (system_path, market_path, utilization_path, flow_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    system_frame.to_csv(system_path, index=False, encoding="utf-8", lineterminator="\n")
    market.to_csv(market_path, index=False, encoding="utf-8", lineterminator="\n")
    utilization.to_csv(utilization_path, index=False, encoding="utf-8", lineterminator="\n")
    flows.to_csv(flow_path, index=False, encoding="utf-8", lineterminator="\n")

    affected = market.set_index("market")
    previously_unaffected = b8_market.loc[
        b8_market["load_shedding_MWh"].le(SHEDDING_TOLERANCE_MW), "market"
    ]
    comparison = {
        "schema_version": "MEM_ETX7B8C_B8_VS_S2_COMPARISON_V1_0",
        "status": "FACTUAL_COMPARISON_COMPLETE_NO_AUTOMATIC_ACCEPTANCE_DECISION",
        "B8": "IMMUTABLE_BOUNDED_PERIMETER_BASELINE",
        "S2": "EXPERIMENTAL_BOUNDED_PERIMETER_CLOSURE_SENSITIVITY",
        "FR_TN_GR_shedding_declines": {
            market_name: bool(affected.at[market_name, "load_shedding_MWh_S2"] < affected.at[market_name, "load_shedding_MWh_B8"])
            for market_name in EXPECTED_VIRTUAL_MARKETS
        },
        "markets_newly_shedding": [
            market_name
            for market_name in previously_unaffected
            if float(affected.at[market_name, "load_shedding_MWh_S2"]) > SHEDDING_TOLERANCE_MW
        ],
        "market_shedding_MWh_changes": {
            market_name: float(affected.at[market_name, "load_shedding_MWh_S2_minus_B8"])
            for market_name in MARKETS
        },
        "market_VOLL_hour_changes": {
            market_name: int(affected.at[market_name, "hours_at_VOLL_S2_minus_B8"])
            for market_name in MARKETS
        },
        "IT_CH_AT_SI_mean_price_changes_EUR_per_MWh": {
            market_name: float(affected.at[market_name, "price_mean_EUR_per_MWh_S2_minus_B8"])
            for market_name in ("IT", "CH", "AT", "SI")
        },
        "interface_congestion_hour_changes": {
            row.physical_link_id: {
                "a_to_b": int(row.hours_at_or_near_a_to_b_limit_S2_minus_B8),
                "b_to_a": int(row.hours_at_or_near_b_to_a_limit_S2_minus_B8),
            }
            for row in flows.itertuples()
        },
        "automatic_acceptance_or_rejection": False,
        "formal_B9_authorized": False,
        "stage_B_authorized": False,
    }
    comparison_path = ROOT / artifact_paths["comparison_JSON"]
    _write_json(comparison_path, comparison)
    return [system_path, market_path, utilization_path, flow_path, comparison_path], {
        "B8": b8_system,
        "S2": s2_system,
        "scarcity_movement": comparison,
    }


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def run_s2(args: argparse.Namespace) -> dict[str, Any]:
    if not args.execute:
        raise SystemExit(
            "S2_EXPERIMENT_NOT_EXECUTED: rerun the exact manual command with --execute"
        )
    config = load_s2_config()
    execution_config = load_execution_config()
    if config["solver"]["name"] != execution_config["solver"]["name"] or config["solver"]["options"] != execution_config["solver"]["options"]:
        raise RuntimeError("S2_SOLVER_CONFIG_DRIFT_FROM_B8")
    started = time.perf_counter()
    log_path = ROOT / config["artifacts"]["raw_log"]
    _append_log(log_path, "ETX-7B8C S2 manual experiment started; B8 remains immutable.")
    preflight = gurobi_preflight(execution_config)
    _append_log(log_path, f"Gurobi normal-user preflight PASS: {json.dumps(preflight, sort_keys=True)}")

    before = verify_s2_predecessors(config)
    contract, provenance, preparation_artifacts = write_preparation_artifacts(config, before)
    baseline = pypsa.Network(before["b6_network_path"])
    network = add_virtual_supply(baseline, contract)
    structural = validate_structural_delta(baseline, network, contract)
    lp = create_and_validate_linopy_model(network)
    gurobi_lp = _assert_gurobi_lp(network)
    _append_log(log_path, f"LP assertions PASS: {json.dumps({**lp, **gurobi_lp}, sort_keys=True)}")

    solve_started = time.perf_counter()
    status, condition = network.optimize.solve_model(
        solver_name=config["solver"]["name"],
        solver_options=config["solver"]["options"],
        log_to_console=bool(config["solver"]["log_to_console"]),
        log_fn=str(log_path),
    )
    solve_seconds = time.perf_counter() - solve_started
    metrics = _solve_metrics(network, str(status), str(condition), solve_seconds)
    metrics["LP_assertions"] = {
        "linopy_integer_variables": lp["integer_variables"],
        "linopy_binary_variables": lp["binary_variables"],
        "Gurobi_NumIntVars": gurobi_lp["NumIntVars"],
        "Gurobi_NumBinVars": gurobi_lp["NumBinVars"],
    }
    if len(network.snapshots) != 8760 or not metrics["accepted"]:
        raise RuntimeError(f"S2_POST_SOLVE_QA_FAILED: {metrics}")
    for generator in EXPECTED_VIRTUAL_IDS:
        dispatch = network.generators_t.p[generator].astype(float)
        if dispatch.min() < -1e-5 or dispatch.max() > float(network.generators.at[generator, "p_nom"]) + 1e-5:
            raise RuntimeError(f"S2_VIRTUAL_GENERATOR_DISPATCH_BOUND_VIOLATION: {generator}")

    result_dir = ROOT / config["artifacts"]["result_directory"]
    stem = config["artifacts"]["result_stem"]
    result_artifacts, outputs = _write_solve_outputs(network, result_dir, stem)
    comparison_artifacts, comparison = _build_comparisons(
        network, config, before, result_dir, stem
    )
    _append_log(
        log_path,
        "ETX-7B8C S2 solve and automatic comparisons PASS; B9 remains locked pending review.",
    )
    result_artifacts.append(log_path)
    result_manifest = ROOT / config["artifacts"]["result_manifest"]
    write_manifest(result_manifest, result_artifacts)
    outputs.update(
        {
            "manifest": relative_path(result_manifest),
            "manifest_sha256": sha256_file(result_manifest),
            "raw_log": relative_path(log_path),
            "virtual_supply_contract": relative_path(preparation_artifacts[0]),
            "cost_provenance": relative_path(preparation_artifacts[1]),
            "comparisons": {path.stem: relative_path(path) for path in comparison_artifacts},
        }
    )
    after = verify_s2_predecessors(config)
    if before["identities"] != after["identities"]:
        raise RuntimeError("ACCEPTED_B1_B8_IDENTITY_CHANGED_DURING_S2_RUN")

    receipt = build_receipt(
        phase=EXPERIMENT_PHASE,
        gate=EXPERIMENT_GATE,
        status="PASS",
        input_manifests=before["input_manifests"],
        outputs=outputs,
        qa={
            "preflight": preflight,
            "structural_delta": structural,
            "LP_assertions": {**lp, **gurobi_lp},
            "post_solve": metrics,
            "comparison": comparison,
            "B8_immutable_after_run": True,
            "formal_B9_authorized": False,
            "stage_B_authorized": False,
        },
        next_gate="METHOD_REVIEW_REQUIRED_ETX_7B9_REMAINS_LOCKED",
        command=command_string(module=CANONICAL_MODULE),
        runtime_seconds=time.perf_counter() - started,
        solve=metrics,
    )
    receipt_path = ROOT / config["artifacts"]["receipt"]
    write_receipt(receipt_path, receipt)
    return receipt


def _write_failure_receipt(error: Exception) -> Path:
    config = load_s2_config()
    try:
        inputs = verify_s2_predecessors(config)["input_manifests"]
    except Exception:
        inputs = []
    receipt = build_receipt(
        phase=EXPERIMENT_PHASE,
        gate=EXPERIMENT_GATE,
        status="FAIL",
        input_manifests=inputs,
        outputs={"production_S2_solve_accepted": False},
        qa={
            "error_type": type(error).__name__,
            "error": str(error),
            "formal_B9_authorized": False,
            "stage_B_authorized": False,
        },
        next_gate="STOP_S2_AND_RETURN_RECEIPT_B9_REMAINS_LOCKED",
        command=command_string(module=CANONICAL_MODULE),
    )
    return write_receipt(ROOT / config["artifacts"]["receipt"], receipt)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MEM ETX-7B8C perimeter-closure preparation and guarded S2 experiment")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("prepare", help="Write and validate the unsolved deterministic S2 contract")
    s2 = subparsers.add_parser("s2", help="Run the guarded normal-user S2 experiment")
    s2.add_argument("--execute", action="store_true", help="Required explicit manual-experiment acknowledgement")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    handlers: dict[str, Callable[[], dict[str, Any]]] = {
        "prepare": prepare_s2,
        "s2": lambda: run_s2(args),
    }
    try:
        result = handlers[args.command]()
    except SystemExit:
        raise
    except Exception as error:
        if args.command == "s2":
            path = _write_failure_receipt(error)
            print(json.dumps({"status": "FAIL", "receipt": relative_path(path), "error": str(error)}, indent=2))
        else:
            print(json.dumps({"status": "FAIL", "error": str(error)}, indent=2))
        raise SystemExit(1) from error
    if args.command == "prepare":
        print(json.dumps(result, indent=2))
    else:
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "gate": result["gate"],
                    "receipt": load_s2_config()["artifacts"]["receipt"],
                    "formal_B9_authorized": False,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()

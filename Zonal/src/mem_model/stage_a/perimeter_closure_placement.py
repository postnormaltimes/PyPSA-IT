"""Prepare and run the isolated ETX-7B9E 2050 placement diagnostic.

The diagnostic reconstructs the accepted B6 2050 network, retains the exact
R10 FR/GR/TN virtual-supply capacities, and adds AT/SI proxies sized from the
immutable B9C residual-shedding distributions. A technical solve PASS never
selects a production price source.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pypsa
import yaml

from mem_model.stage_a.b8_diagnostics import (
    INTERFACE_LIMIT_TOLERANCE_MW,
    INTERFACE_PATH,
    LINK_PATH,
    SHEDDING_TOLERANCE_MW,
    VOLL_TOLERANCE_EUR_PER_MWH,
    _component_name_column,
    _normalize_market_tables,
    _price_statistics,
)
from mem_model.stage_a.execution import (
    _solve_metrics,
    _write_solve_outputs,
    gurobi_preflight,
)
from mem_model.stage_a.network import MARKETS, ROOT, load_execution_config
from mem_model.stage_a.perimeter_closure import (
    _assert_frame_equal,
    _assert_gurobi_lp,
    _component_counts,
    _dynamic_tables,
    create_and_validate_linopy_model,
    validate_lp_static,
)
from mem_model.stage_a.perimeter_closure_r10 import (
    VIRTUAL_CARRIER,
    _baseline_result_paths,
    _directional_limit_for_year,
    _flow_wide,
    _require_hash,
    _result_paths,
    _system_metrics_from_outputs,
    _verify_manifest,
    _write_json,
    load_r10_config,
    recover_cost_proxy,
    residual_energy_capacity,
    verify_immutable_history,
)
from mem_model.stage_a.perimeter_closure_r5 import (
    _dispatch_wide,
    _exported_market_diagnostics,
    _ordinary_hour_mask,
    _verify_phase,
)
from mem_model.stage_a.receipts import (
    build_receipt,
    command_string,
    relative_path,
    sha256_file,
    write_manifest,
    write_receipt,
)


CONFIG_PATH = ROOT / "config/stage_a_perimeter_closure_placement_2050.yaml"
CANONICAL_MODULE = "mem_model.stage_a.perimeter_closure_placement"
YEAR = 2050
RESIDUAL_ENERGY_SHARE = 0.10
RETAINED_MARKETS = ("FR", "GR", "TN")
PLACEMENT_MARKETS = ("AT", "SI")
PROXY_MARKETS = RETAINED_MARKETS + PLACEMENT_MARKETS
EXPORTED_MARKETS = ("FR", "CH", "AT", "SI", "ME", "GR", "MT", "TN")
PLACEMENT_TARGETS = ("AT", "SI", "IT")
PLACEMENT_GENERATOR_IDS = {
    "FR": "EXTERNAL_VIRTUAL_SUPPLY_FR_R10_2050",
    "GR": "EXTERNAL_VIRTUAL_SUPPLY_GR_R10_2050",
    "TN": "EXTERNAL_VIRTUAL_SUPPLY_TN_R10_2050",
    "AT": "EXTERNAL_VIRTUAL_SUPPLY_AT_PLACEMENT_R10_2050",
    "SI": "EXTERNAL_VIRTUAL_SUPPLY_SI_PLACEMENT_R10_2050",
}


def load_placement_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if config.get("schema") != "PERIMETER_CLOSURE_2050_PLACEMENT_DIAGNOSTIC_V1_0":
        raise RuntimeError("PLACEMENT_CONFIG_SCHEMA_MISMATCH")
    if config.get("status") != "PREPARED_NOT_EXECUTED":
        raise RuntimeError("PLACEMENT_CONFIG_NOT_PREPARED")
    design = config["capacity_design"]
    if tuple(design["retained_R10_markets"]) != RETAINED_MARKETS:
        raise RuntimeError("PLACEMENT_RETAINED_SCOPE_MISMATCH")
    if tuple(design["placement_markets"]) != PLACEMENT_MARKETS:
        raise RuntimeError("PLACEMENT_NEW_PROXY_SCOPE_MISMATCH")
    if float(design["placement_sizing"]["residual_energy_share"]) != RESIDUAL_ENERGY_SHARE:
        raise RuntimeError("PLACEMENT_RESIDUAL_SHARE_MISMATCH")
    diagnostics = config["diagnostics"]
    if tuple(diagnostics["comparison_cases"]) != ("B9B", "R10", "R5", "PLACEMENT"):
        raise RuntimeError("PLACEMENT_COMPARISON_CASE_SCOPE_MISMATCH")
    if tuple(diagnostics["exported_markets"]) != EXPORTED_MARKETS:
        raise RuntimeError("PLACEMENT_EXPORTED_MARKET_SCOPE_MISMATCH")
    if tuple(diagnostics["placement_targets"]) != PLACEMENT_TARGETS:
        raise RuntimeError("PLACEMENT_TARGET_SCOPE_MISMATCH")
    return config


def verify_placement_inputs(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_placement_config()
    method = config["method_authority"]
    _require_hash(ROOT / method["path"], method["sha256"], "PLACEMENT_METHOD_AUTHORITY")
    r10_config = load_r10_config(ROOT / method["path"])
    base = verify_immutable_history(r10_config)

    accepted: dict[str, Any] = {}
    for key in (
        "b8d_2040_production_source",
        "b9c_2050_R10_diagnostic",
        "b9d_2050_R5_diagnostic",
    ):
        accepted[key] = _verify_phase(config["accepted_inputs"][key], key.upper())

    b9b_spec = config["accepted_inputs"]["b9b_2050_bounded_baseline"]
    b9b = base["history"]["b9b_2050_bounded_baseline"]
    if (
        b9b["receipt_sha256"] != b9b_spec["receipt_sha256"]
        or b9b["manifest"]["sha256"] != b9b_spec["result_manifest_sha256"]
        or b9b["receipt"].get("gate") != b9b_spec["required_gate"]
    ):
        raise RuntimeError("PLACEMENT_B9B_IDENTITY_MISMATCH")

    network_spec = config["accepted_inputs"]["b6_2050_unsolved_network"]
    network_path = ROOT / network_spec["path"]
    network_sha = _require_hash(
        network_path, network_spec["sha256"], "PLACEMENT_B6_2050_NETWORK"
    )
    if base["networks"][YEAR]["sha256"] != network_sha:
        raise RuntimeError("PLACEMENT_B6_BASE_VERIFICATION_MISMATCH")

    for key, label in (
        ("R10_virtual_supply_contract", "PLACEMENT_R10_CONTRACT"),
        ("R5_virtual_supply_contract", "PLACEMENT_R5_CONTRACT"),
    ):
        spec = config["accepted_inputs"][key]
        _require_hash(ROOT / spec["path"], spec["sha256"], label)

    mask_spec = config["accepted_inputs"]["immutable_ordinary_hour_mask"]
    _require_hash(ROOT / mask_spec["path"], mask_spec["sha256"], "PLACEMENT_MASK")
    _require_hash(
        ROOT / mask_spec["metadata_path"],
        mask_spec["metadata_sha256"],
        "PLACEMENT_MASK_METADATA",
    )
    mask_metadata = json.loads((ROOT / mask_spec["metadata_path"]).read_text(encoding="utf-8"))
    if not (
        mask_metadata.get("source") == "IMMUTABLE_ETX7B9_B_2050_BOUNDED_BASELINE_ONLY"
        and mask_metadata.get("reused_unchanged_for_B9B_R10_R5") is True
        and mask_metadata.get("outcome_dependent_redefinition") is False
    ):
        raise RuntimeError("PLACEMENT_IMMUTABLE_MASK_METADATA_MISMATCH")

    cost_spec = config["cost_proxy"]
    _require_hash(ROOT / cost_spec["source_path"], cost_spec["source_sha256"], "PLACEMENT_B4_COST")
    return {
        "status": "PASS",
        "r10_config": r10_config,
        "base": base,
        "accepted": accepted,
        "b9b": b9b,
        "b6_network_path": network_path,
        "b6_network_sha256": network_sha,
        "mask_metadata": mask_metadata,
    }


def _case_paths(receipt: dict[str, Any]) -> dict[str, Path]:
    return _baseline_result_paths(receipt)


def _case_tables(network: pypsa.Network, paths: dict[str, Path]) -> dict[str, Any]:
    load, shedding, prices, weights = _normalize_market_tables(
        network, paths["shedding"], paths["prices"]
    )
    return {
        "network": network,
        "paths": paths,
        "load": load,
        "shedding": shedding,
        "prices": prices,
        "weights": weights,
    }


def derive_placement_contract(
    config: dict[str, Any],
    verification: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], dict[str, Any]]:
    design = config["capacity_design"]
    r10_spec = config["accepted_inputs"]["R10_virtual_supply_contract"]
    r10_contract = pd.read_csv(ROOT / r10_spec["path"])
    if tuple(r10_contract["bus"]) != RETAINED_MARKETS or len(r10_contract) != 3:
        raise RuntimeError("PLACEMENT_R10_CONTRACT_SCOPE_FAILURE")

    configured_retained = design["retained_R10_capacities_MW"]
    for row in r10_contract.itertuples(index=False):
        if (
            row.generator_id != PLACEMENT_GENERATOR_IDS[row.bus]
            or not np.isclose(
                float(row.p_nom_MW),
                float(configured_retained[row.bus]),
                atol=0.0,
                rtol=0.0,
            )
        ):
            raise RuntimeError(f"PLACEMENT_RETAINED_R10_CAPACITY_MISMATCH_{row.bus}")

    b9c = verification["accepted"]["b9c_2050_R10_diagnostic"]["receipt"]
    b9c_paths = _case_paths(b9c)
    b9c_network = pypsa.Network(b9c_paths["solved"])
    b9c_tables = _case_tables(b9c_network, b9c_paths)
    expected_new = design["placement_sizing"]["expected_QA_capacities_MW"]
    derivation_rows: list[dict[str, Any]] = []
    derived: dict[str, float] = {}
    for market in PLACEMENT_MARKETS:
        result = residual_energy_capacity(
            b9c_tables["shedding"][market].to_numpy(dtype=float),
            RESIDUAL_ENERGY_SHARE,
        )
        capacity = float(result["capacity_MW"])
        if not np.isclose(capacity, float(expected_new[market]), atol=5e-9, rtol=0.0):
            raise RuntimeError(f"PLACEMENT_CAPACITY_QA_MISMATCH_{market}")
        if not np.isclose(
            float(result["verified_residual_energy_share"]),
            RESIDUAL_ENERGY_SHARE,
            atol=1e-12,
            rtol=0.0,
        ):
            raise RuntimeError(f"PLACEMENT_MECHANICAL_IDENTITY_FAILURE_{market}")
        derived[market] = capacity
        derivation_rows.append(
            {
                "market": market,
                "source_case": "ETX7B9C_2050_R10",
                "capacity_sizing_rule": design["placement_sizing"]["rule"],
                "residual_energy_share": RESIDUAL_ENERGY_SHARE,
                "derived_virtual_capacity_MW": capacity,
                **{key: value for key, value in result.items() if key != "capacity_MW"},
            }
        )

    cost = recover_cost_proxy(YEAR, verification["r10_config"])
    cost_spec = config["cost_proxy"]
    if (
        cost["source_asset_id"] != cost_spec["source_asset_id"]
        or not np.isclose(
            float(cost["marginal_cost_EUR2025_per_MWh_el"]),
            float(cost_spec["marginal_cost_EUR2025_per_MWh_el"]),
            atol=1e-12,
            rtol=0.0,
        )
    ):
        raise RuntimeError("PLACEMENT_COST_PROXY_MISMATCH")

    capacities = {**{market: float(configured_retained[market]) for market in RETAINED_MARKETS}, **derived}
    rows: list[dict[str, Any]] = []
    for market in PROXY_MARKETS:
        source = "RETAINED_IMMUTABLE_R10_CAPACITY" if market in RETAINED_MARKETS else "DERIVED_FROM_IMMUTABLE_B9C_R10_RESIDUAL_SHEDDING"
        rows.append(
            {
                "generator_id": PLACEMENT_GENERATOR_IDS[market],
                "bus": market,
                "carrier": VIRTUAL_CARRIER,
                "p_nom_MW": capacities[market],
                "capacity_source": source,
                "capacity_sizing_rule": (
                    "RETAIN_EXACT_ETX7B9C_R10_VALUE"
                    if market in RETAINED_MARKETS
                    else design["placement_sizing"]["rule"]
                ),
                "mechanical_source_residual_energy_share": (
                    np.nan if market in RETAINED_MARKETS else RESIDUAL_ENERGY_SHARE
                ),
                "p_nom_extendable": False,
                "committable": False,
                "p_min_pu": 0.0,
                "p_max_pu": 1.0,
                "efficiency": 1.0,
                "marginal_cost_EUR2025_per_MWh_el": float(
                    cost["marginal_cost_EUR2025_per_MWh_el"]
                ),
                "cost_source_asset_id": cost["source_asset_id"],
                "interpretation": "RESIDUAL_FIRM_EXTERNAL_SUPPLY_FROM_OMITTED_SYSTEMS",
                "status": "PLACEMENT_DIAGNOSTIC_FIXED_VIRTUAL_EXTERNAL_SUPPLY",
            }
        )
    contract = pd.DataFrame.from_records(rows)
    if tuple(contract["bus"]) != PROXY_MARKETS or len(contract) != 5:
        raise RuntimeError("PLACEMENT_CONTRACT_SCOPE_FAILURE")

    placement_total = float(contract["p_nom_MW"].sum())
    r5_contract = pd.read_csv(
        ROOT / config["accepted_inputs"]["R5_virtual_supply_contract"]["path"]
    )
    r5_total = float(r5_contract["p_nom_MW"].sum())
    difference = placement_total - r5_total
    percent = 100.0 * difference / r5_total
    controls = {
        "classification": design["classification"],
        "placement_total_virtual_capacity_MW": placement_total,
        "R5_total_virtual_capacity_MW": r5_total,
        "absolute_difference_MW": difference,
        "percentage_difference": percent,
        "exact_total_rescaling_applied": False,
    }
    expected_controls = {
        "placement_total_virtual_capacity_MW": design["expected_QA_total_virtual_capacity_MW"],
        "R5_total_virtual_capacity_MW": design["R5_total_virtual_capacity_MW"],
        "absolute_difference_MW": design["expected_QA_difference_vs_R5_MW"],
        "percentage_difference": design["expected_QA_difference_vs_R5_percent"],
    }
    for key, expected in expected_controls.items():
        if not np.isclose(float(controls[key]), float(expected), atol=1e-9, rtol=0.0):
            raise RuntimeError(f"PLACEMENT_TOTAL_CAPACITY_CONTROL_FAILURE_{key}")
    return contract, pd.DataFrame.from_records(derivation_rows), cost, controls


def add_placement_virtual_supply(
    baseline: pypsa.Network,
    contract: pd.DataFrame,
) -> pypsa.Network:
    network = baseline.copy()
    if VIRTUAL_CARRIER in network.carriers.index:
        raise RuntimeError("PLACEMENT_VIRTUAL_CARRIER_ALREADY_PRESENT")
    if set(network.generators.index) & set(contract["generator_id"]):
        raise RuntimeError("PLACEMENT_VIRTUAL_GENERATOR_ALREADY_PRESENT")
    network.add(
        "Carrier",
        VIRTUAL_CARRIER,
        co2_emissions=0.0,
        nice_name="Placement diagnostic residual firm external virtual supply",
    )
    for row in contract.itertuples(index=False):
        network.add(
            "Generator",
            row.generator_id,
            bus=row.bus,
            carrier=VIRTUAL_CARRIER,
            p_nom=float(row.p_nom_MW),
            p_nom_extendable=False,
            committable=False,
            p_min_pu=0.0,
            p_max_pu=1.0,
            marginal_cost=float(row.marginal_cost_EUR2025_per_MWh_el),
            efficiency=1.0,
        )
    network.meta = {
        **dict(baseline.meta),
        "auxiliary_experiment": "PLACEMENT_PERIMETER_CLOSURE_2050",
        "method": "PERIMETER_CLOSURE_METHOD_V2_0",
        "placement_capacity_source": "IMMUTABLE_B9C_R10_RESIDUAL_SHEDDING",
        "placement_residual_energy_share": RESIDUAL_ENERGY_SHARE,
        "technical_status": "PREPARED_NOT_EXECUTED",
        "production_price_acceptance": "PENDING_METHOD_REVIEW",
    }
    return network


def validate_placement_structural_delta(
    baseline: pypsa.Network,
    network: pypsa.Network,
    contract: pd.DataFrame,
) -> dict[str, Any]:
    new_ids = set(contract["generator_id"])
    if not baseline.snapshots.equals(network.snapshots):
        raise RuntimeError("PLACEMENT_SNAPSHOT_DRIFT")
    _assert_frame_equal(baseline.snapshot_weightings, network.snapshot_weightings, "PLACEMENT_WEIGHTS")
    for component in ("buses", "loads", "links", "stores"):
        _assert_frame_equal(
            getattr(baseline, component),
            getattr(network, component),
            f"PLACEMENT_{component.upper()}",
        )
    if set(network.carriers.index) != set(baseline.carriers.index) | {VIRTUAL_CARRIER}:
        raise RuntimeError("PLACEMENT_CARRIER_DELTA_NOT_EXACT")
    _assert_frame_equal(
        baseline.carriers,
        network.carriers.loc[baseline.carriers.index, baseline.carriers.columns],
        "PLACEMENT_EXISTING_CARRIERS",
    )
    if set(network.generators.index) != set(baseline.generators.index) | new_ids:
        raise RuntimeError("PLACEMENT_GENERATOR_DELTA_NOT_EXACT")
    if set(network.generators.columns) != set(baseline.generators.columns):
        raise RuntimeError("PLACEMENT_GENERATOR_SCHEMA_DRIFT")
    _assert_frame_equal(
        baseline.generators,
        network.generators.loc[baseline.generators.index, baseline.generators.columns],
        "PLACEMENT_EXISTING_GENERATORS",
    )
    for holder_name in ("buses_t", "loads_t", "links_t", "stores_t"):
        left = _dynamic_tables(getattr(baseline, holder_name))
        right = _dynamic_tables(getattr(network, holder_name))
        if set(left) != set(right):
            raise RuntimeError(f"PLACEMENT_DYNAMIC_SCHEMA_DRIFT_{holder_name}")
        for attribute in left:
            _assert_frame_equal(
                left[attribute], right[attribute], f"PLACEMENT_{holder_name}_{attribute}"
            )
    left_generators = _dynamic_tables(baseline.generators_t)
    right_generators = _dynamic_tables(network.generators_t)
    if set(left_generators) != set(right_generators):
        raise RuntimeError("PLACEMENT_GENERATOR_DYNAMIC_SCHEMA_DRIFT")
    for attribute, base_frame in left_generators.items():
        observed = right_generators[attribute]
        unexpected = set(observed.columns) - set(base_frame.columns) - new_ids
        if unexpected:
            raise RuntimeError(f"PLACEMENT_UNEXPECTED_DYNAMIC_COLUMNS_{attribute}: {sorted(unexpected)}")
        _assert_frame_equal(
            base_frame,
            observed.loc[:, base_frame.columns],
            f"PLACEMENT_EXISTING_GENERATOR_DYNAMIC_{attribute}",
        )
    for row in contract.itertuples(index=False):
        observed = network.generators.loc[row.generator_id]
        if not (
            observed.bus == row.bus
            and observed.carrier == VIRTUAL_CARRIER
            and np.isclose(float(observed.p_nom), float(row.p_nom_MW), atol=0.0, rtol=0.0)
            and not bool(observed.p_nom_extendable)
            and not bool(observed.committable)
            and float(observed.p_min_pu) == 0.0
            and float(observed.p_max_pu) == 1.0
            and float(observed.efficiency) == 1.0
            and np.isclose(
                float(observed.marginal_cost),
                float(row.marginal_cost_EUR2025_per_MWh_el),
                atol=0.0,
                rtol=0.0,
            )
        ):
            raise RuntimeError(f"PLACEMENT_VIRTUAL_PARAMETER_FAILURE_{row.generator_id}")
    baseline_counts = _component_counts(baseline)
    observed_counts = _component_counts(network)
    expected_counts = dict(baseline_counts)
    expected_counts["carriers"] += 1
    expected_counts["generators"] += 5
    if observed_counts != expected_counts:
        raise RuntimeError("PLACEMENT_COMPONENT_COUNT_DRIFT")
    market_buses = set(network.buses.index[network.buses.carrier.eq("AC_STAGE_A_MARKET")])
    if market_buses != set(MARKETS):
        raise RuntimeError("PLACEMENT_MARKET_BUS_SCOPE_FAILURE")
    if set(contract["bus"]) != set(PROXY_MARKETS):
        raise RuntimeError("PLACEMENT_PROXY_SCOPE_FAILURE")
    if set(contract["bus"]) & {"IT", "CH", "HR", "ME", "MT"}:
        raise RuntimeError("PLACEMENT_EXCLUDED_PROXY_PRESENT")
    return {
        "status": "PASS",
        "year": YEAR,
        "baseline_counts": baseline_counts,
        "placement_counts": observed_counts,
        "allowed_delta": {"carriers": 1, "generators": 5},
        "new_generators": list(contract["generator_id"]),
        "proxy_markets": list(contract["bus"]),
        "excluded_market_virtual_supply_count": 0,
        "baseline_component_preservation": "EXACT",
    }


def _preparation_paths(config: dict[str, Any]) -> dict[str, Path]:
    qa = ROOT / config["phase"]["qa_directory"]
    return {
        "contract": qa / "MEM_Placement_2050_Virtual_Supply_Contract_v1.0.csv",
        "derivation": qa / "MEM_Placement_2050_AT_SI_Capacity_Derivation_v1.0.csv",
        "cost": qa / "MEM_Placement_2050_Cost_Provenance_v1.0.json",
        "capacity_control": qa / "MEM_Placement_2050_Near_Constant_Capacity_Control_v1.0.json",
        "qa": qa / "MEM_Placement_2050_Preparation_QA_v1.0.json",
        "manifest": qa / "MEM_Placement_2050_Preparation_Manifest_v1.0.csv",
        "verification": qa / "MEM_Placement_2050_Preparation_Final_Verification_v1.0.json",
    }


def prepare_placement(*, write_artifacts: bool = True) -> dict[str, Any]:
    config = load_placement_config()
    verification = verify_placement_inputs(config)
    contract, derivation, cost, capacity_control = derive_placement_contract(
        config, verification
    )
    baseline = pypsa.Network(verification["b6_network_path"])
    if len(baseline.snapshots) != 8760 or int(baseline.meta.get("horizon", YEAR)) != YEAR:
        raise RuntimeError("PLACEMENT_B6_2050_NETWORK_CHRONOLOGY_FAILURE")
    network = add_placement_virtual_supply(baseline, contract)
    structural = validate_placement_structural_delta(baseline, network, contract)
    static_lp = validate_lp_static(network)
    short = network.copy()
    short.set_snapshots(short.snapshots[:6])
    short_lp = create_and_validate_linopy_model(short)
    payload = {
        "schema_version": "MEM_PLACEMENT_2050_PREPARATION_QA_V1_0",
        "status": "PASS",
        "phase": config["phase"]["id"],
        "gate": config["phase"]["gate"],
        "classification": config["capacity_design"]["classification"],
        "capacities_MW": contract.set_index("bus")["p_nom_MW"].to_dict(),
        "capacity_control": capacity_control,
        "marginal_cost_EUR2025_per_MWh_el": float(
            contract["marginal_cost_EUR2025_per_MWh_el"].iloc[0]
        ),
        "structural_delta": structural,
        "static_LP": static_lp,
        "short_unsolved_LP_fixture": {"snapshots": 6, **short_lp},
        "immutable_B8D_2040_verified": True,
        "immutable_B9B_2050_verified": True,
        "immutable_B9C_R10_verified": True,
        "immutable_B9D_R5_verified": True,
        "production_optimization_executed": False,
        "technical_diagnostic_status": "PREPARED_NOT_EXECUTED",
        "production_price_acceptance": "PENDING_METHOD_REVIEW",
        "B10_authorized": False,
        "Stage_B_authorized": False,
    }
    paths = _preparation_paths(config)
    artifacts: list[Path] = []
    if write_artifacts:
        paths["contract"].parent.mkdir(parents=True, exist_ok=True)
        contract.to_csv(paths["contract"], index=False, encoding="utf-8", lineterminator="\n")
        derivation.to_csv(
            paths["derivation"], index=False, encoding="utf-8", lineterminator="\n"
        )
        _write_json(
            paths["cost"],
            {"schema_version": "MEM_PLACEMENT_2050_COST_PROVENANCE_V1_0", **cost},
        )
        _write_json(
            paths["capacity_control"],
            {
                "schema_version": "MEM_PLACEMENT_2050_NEAR_CONSTANT_CAPACITY_CONTROL_V1_0",
                **capacity_control,
            },
        )
        _write_json(paths["qa"], payload)
        artifacts = [
            paths["contract"],
            paths["derivation"],
            paths["cost"],
            paths["capacity_control"],
            paths["qa"],
        ]
        members = [
            CONFIG_PATH,
            ROOT / "src/mem_model/stage_a/perimeter_closure_placement.py",
            ROOT / "docs/runbooks/ETX7B9E_RUNBOOK.md",
            *artifacts,
        ]
        manifest = write_manifest(paths["manifest"], members)
        _write_json(
            paths["verification"],
            {
                "schema_version": "MEM_PLACEMENT_2050_PREPARATION_FINAL_VERIFICATION_V1_0",
                "status": "PASS",
                "preparation_manifest": relative_path(paths["manifest"]),
                "preparation_manifest_sha256": sha256_file(paths["manifest"]),
                "preparation_manifest_members": len(manifest),
                "B8D_2040_receipt_sha256": verification["accepted"][
                    "b8d_2040_production_source"
                ]["receipt_sha256"],
                "B9B_2050_receipt_sha256": verification["b9b"]["receipt_sha256"],
                "B9C_R10_receipt_sha256": verification["accepted"][
                    "b9c_2050_R10_diagnostic"
                ]["receipt_sha256"],
                "B9D_R5_receipt_sha256": verification["accepted"][
                    "b9d_2050_R5_diagnostic"
                ]["receipt_sha256"],
                "B6_2050_network_sha256": verification["b6_network_sha256"],
                "capacities_MW": payload["capacities_MW"],
                "capacity_control": capacity_control,
                "production_optimization_executed": False,
                "production_price_acceptance": "PENDING_METHOD_REVIEW",
                "B10_authorized": False,
                "Stage_B_authorized": False,
            },
        )
        artifacts.extend([paths["manifest"], paths["verification"]])
    return {
        "config": config,
        "verification": verification,
        "baseline": baseline,
        "network": network,
        "contract": contract,
        "derivation": derivation,
        "cost": cost,
        "capacity_control": capacity_control,
        "structural": structural,
        "LP": short_lp,
        "payload": payload,
        "artifacts": artifacts,
    }


def _load_solved_case(
    network: pypsa.Network,
    paths: dict[str, Path],
    objective: float,
    voll: float,
) -> dict[str, Any]:
    system, load, shedding, prices, weights = _system_metrics_from_outputs(
        network, paths, voll, objective
    )
    return {
        "network": network,
        "paths": paths,
        "system": system,
        "load": load,
        "shedding": shedding,
        "prices": prices,
        "weights": weights,
    }


def _comparison_cases(
    placement_network: pypsa.Network,
    prepared: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    verification = prepared["verification"]
    config = prepared["config"]
    receipts = {
        "B9B": verification["b9b"]["receipt"],
        "R10": verification["accepted"]["b9c_2050_R10_diagnostic"]["receipt"],
        "R5": verification["accepted"]["b9d_2050_R5_diagnostic"]["receipt"],
    }
    voll = float(config["diagnostics"]["VOLL_EUR_per_MWh"])
    cases: dict[str, dict[str, Any]] = {}
    for name, receipt in receipts.items():
        paths = _case_paths(receipt)
        network = pypsa.Network(paths["solved"])
        cases[name] = _load_solved_case(
            network, paths, float(receipt["objective"]), voll
        )
    placement_paths = _result_paths(
        ROOT / config["phase"]["result_directory"],
        config["phase"]["result_stem"],
    )
    cases["PLACEMENT"] = _load_solved_case(
        placement_network,
        placement_paths,
        float(placement_network.objective),
        voll,
    )
    reference = cases["B9B"]
    for name, case in cases.items():
        if not reference["network"].snapshots.equals(case["network"].snapshots):
            raise RuntimeError(f"PLACEMENT_FOUR_WAY_SNAPSHOT_DRIFT_{name}")
        if not np.allclose(reference["load"], case["load"], atol=0.0, rtol=0.0):
            raise RuntimeError(f"PLACEMENT_FOUR_WAY_LOAD_DRIFT_{name}")
    return cases


def _system_comparison(cases: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for name in ("B9B", "R10", "R5", "PLACEMENT"):
        rows.append({"case": name, **cases[name]["system"]})
    return pd.DataFrame.from_records(rows)


def _load_immutable_ordinary_mask(
    cases: dict[str, dict[str, Any]],
    config: dict[str, Any],
) -> tuple[pd.Series, dict[str, Any]]:
    spec = config["accepted_inputs"]["immutable_ordinary_hour_mask"]
    frame = pd.read_csv(ROOT / spec["path"])
    if set(frame.columns) != {
        "snapshot",
        "ordinary_hour",
        "B9B_total_ten_market_shedding_MW",
        "B9B_max_ten_market_price_EUR_per_MWh",
    }:
        raise RuntimeError("PLACEMENT_IMMUTABLE_MASK_SCHEMA_MISMATCH")
    frame["snapshot"] = pd.to_datetime(frame["snapshot"])
    frame = frame.set_index("snapshot").reindex(cases["B9B"]["network"].snapshots)
    if frame.isna().any().any():
        raise RuntimeError("PLACEMENT_IMMUTABLE_MASK_TIMESTAMP_MISMATCH")
    raw = frame["ordinary_hour"]
    if raw.dtype == bool:
        mask = raw.astype(bool)
    else:
        mask = raw.astype(str).str.casefold().map({"true": True, "false": False})
        if mask.isna().any():
            raise RuntimeError("PLACEMENT_IMMUTABLE_MASK_BOOLEAN_PARSE_FAILURE")
    derived, _, derived_metadata = _ordinary_hour_mask(
        cases["B9B"]["shedding"], cases["B9B"]["prices"]
    )
    if not mask.equals(derived):
        raise RuntimeError("PLACEMENT_IMMUTABLE_MASK_CONTENT_MISMATCH")
    original_metadata = json.loads(
        (ROOT / spec["metadata_path"]).read_text(encoding="utf-8")
    )
    if derived_metadata["mask_boolean_sha256"] != original_metadata["mask_boolean_sha256"]:
        raise RuntimeError("PLACEMENT_IMMUTABLE_MASK_DIGEST_MISMATCH")
    metadata = {
        "schema_version": "MEM_PLACEMENT_2050_ORDINARY_HOUR_MASK_REUSE_V1_0",
        "status": "PASS_REUSED_WITHOUT_REGENERATION",
        "source_path": spec["path"],
        "source_sha256": spec["sha256"],
        "source_metadata_path": spec["metadata_path"],
        "source_metadata_sha256": spec["metadata_sha256"],
        "definition": config["diagnostics"]["ordinary_hour_mask"]["definition"],
        "ordinary_hours": int(mask.sum()),
        "excluded_hours": int((~mask).sum()),
        "mask_boolean_sha256": original_metadata["mask_boolean_sha256"],
        "applied_unchanged_to": ["B9B", "R10", "R5", "PLACEMENT"],
        "placement_outcome_used_to_define_mask": False,
    }
    return mask.astype(bool), metadata


def _ordinary_hour_comparisons(
    cases: dict[str, dict[str, Any]],
    mask: pd.Series,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    baseline = cases["B9B"]["prices"]
    rows: list[dict[str, Any]] = []
    direct_rows: list[dict[str, Any]] = []
    for market in EXPORTED_MARKETS:
        baseline_values = baseline.loc[mask, market].astype(float)
        for name in ("B9B", "R10", "R5", "PLACEMENT"):
            values = cases[name]["prices"].loc[mask, market].astype(float)
            difference = values - baseline_values
            rows.append(
                {
                    "market": market,
                    "case": name,
                    "ordinary_hour_count": int(mask.sum()),
                    "price_mean_EUR_per_MWh": float(values.mean()),
                    "price_median_EUR_per_MWh": float(values.median()),
                    "price_P95_EUR_per_MWh": float(values.quantile(0.95)),
                    "mean_absolute_change_vs_B9B_EUR_per_MWh": float(
                        difference.abs().mean()
                    ),
                    "maximum_absolute_change_vs_B9B_EUR_per_MWh": float(
                        difference.abs().max()
                    ),
                    "mask_source": "IMMUTABLE_B9D_B9B_DERIVED_MASK_REUSED_UNCHANGED",
                }
            )
        placement = cases["PLACEMENT"]["prices"].loc[mask, market].astype(float)
        for comparator in ("R10", "R5"):
            reference = cases[comparator]["prices"].loc[mask, market].astype(float)
            pointwise = placement - reference
            direct_rows.append(
                {
                    "market": market,
                    "comparison": f"PLACEMENT_MINUS_{comparator}",
                    "ordinary_hour_count": int(mask.sum()),
                    "mean_price_difference_EUR_per_MWh": float(
                        placement.mean() - reference.mean()
                    ),
                    "median_price_difference_EUR_per_MWh": float(
                        placement.median() - reference.median()
                    ),
                    "P95_price_difference_EUR_per_MWh": float(
                        placement.quantile(0.95) - reference.quantile(0.95)
                    ),
                    "mean_pointwise_price_change_EUR_per_MWh": float(pointwise.mean()),
                    "mean_absolute_pointwise_price_change_EUR_per_MWh": float(
                        pointwise.abs().mean()
                    ),
                    "maximum_absolute_pointwise_price_change_EUR_per_MWh": float(
                        pointwise.abs().max()
                    ),
                    "mask_source": "IMMUTABLE_B9D_B9B_DERIVED_MASK_REUSED_UNCHANGED",
                }
            )
    return pd.DataFrame.from_records(rows), pd.DataFrame.from_records(direct_rows)


def _virtual_supply_utilization(
    cases: dict[str, dict[str, Any]],
    prepared: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    config = prepared["config"]
    contracts = {
        "R10": pd.read_csv(
            ROOT / config["accepted_inputs"]["R10_virtual_supply_contract"]["path"]
        ),
        "R5": pd.read_csv(
            ROOT / config["accepted_inputs"]["R5_virtual_supply_contract"]["path"]
        ),
        "PLACEMENT": prepared["contract"].copy(),
    }
    snapshots = cases["PLACEMENT"]["network"].snapshots
    dispatches: dict[str, pd.DataFrame] = {}
    for name, contract in contracts.items():
        dispatches[name] = _dispatch_wide(
            cases[name]["paths"]["dispatch"],
            snapshots,
            contract["generator_id"],
        )

    rows: list[dict[str, Any]] = []
    for name in ("R10", "R5", "PLACEMENT"):
        contract = contracts[name]
        weights = cases[name]["weights"]
        for row in contract.itertuples(index=False):
            market = str(row.bus)
            series = dispatches[name][row.generator_id].astype(float)
            p_nom = float(row.p_nom_MW)
            annual = float((series * weights).sum())
            b9b_scarcity = cases["B9B"]["shedding"][market].gt(SHEDDING_TOLERANCE_MW)
            r10_scarcity = cases["R10"]["shedding"][market].gt(SHEDDING_TOLERANCE_MW)
            b9b_dispatch = float((series.loc[b9b_scarcity] * weights.loc[b9b_scarcity]).sum())
            r10_dispatch = float((series.loc[r10_scarcity] * weights.loc[r10_scarcity]).sum())
            local_load = float(
                (cases[name]["load"][market] * weights).sum()
            )
            rows.append(
                {
                    "case": name,
                    "market": market,
                    "generator_id": row.generator_id,
                    "p_nom_MW": p_nom,
                    "annual_dispatch_MWh": annual,
                    "capacity_factor": annual / (p_nom * float(weights.sum())),
                    "peak_dispatch_MW": float(series.max()),
                    "hours_above_1_MW": int(series.gt(1.0).sum()),
                    "hours_above_10_percent_p_nom": int(series.gt(0.10 * p_nom).sum()),
                    "hours_above_50_percent_p_nom": int(series.gt(0.50 * p_nom).sum()),
                    "hours_above_90_percent_p_nom": int(series.gt(0.90 * p_nom).sum()),
                    "dispatch_during_B9B_local_scarcity_MWh": b9b_dispatch,
                    "dispatch_outside_B9B_local_scarcity_MWh": annual - b9b_dispatch,
                    "share_dispatch_during_B9B_local_scarcity_percent": (
                        100.0 * b9b_dispatch / annual if annual else 0.0
                    ),
                    "dispatch_during_R10_local_scarcity_MWh": r10_dispatch,
                    "dispatch_outside_R10_local_scarcity_MWh": annual - r10_dispatch,
                    "share_dispatch_during_R10_local_scarcity_percent": (
                        100.0 * r10_dispatch / annual if annual else 0.0
                    ),
                    "share_local_annual_demand_percent": (
                        100.0 * annual / local_load if local_load else 0.0
                    ),
                    "automatic_utilization_interpretation": False,
                }
            )
    utilization = pd.DataFrame.from_records(rows)
    metrics = [
        "p_nom_MW",
        "annual_dispatch_MWh",
        "capacity_factor",
        "peak_dispatch_MW",
        "hours_above_1_MW",
        "hours_above_10_percent_p_nom",
        "hours_above_50_percent_p_nom",
        "hours_above_90_percent_p_nom",
        "dispatch_during_B9B_local_scarcity_MWh",
        "dispatch_outside_B9B_local_scarcity_MWh",
    ]
    comparison_rows: list[dict[str, Any]] = []
    for market in RETAINED_MARKETS:
        selected = utilization.loc[utilization["market"].eq(market)].set_index("case")
        record: dict[str, Any] = {"market": market}
        for metric in metrics:
            for name in ("R10", "R5", "PLACEMENT"):
                record[f"{name}_{metric}"] = selected.at[name, metric]
            record[f"PLACEMENT_minus_R10_{metric}"] = (
                selected.at["PLACEMENT", metric] - selected.at["R10", metric]
            )
            record[f"PLACEMENT_minus_R5_{metric}"] = (
                selected.at["PLACEMENT", metric] - selected.at["R5", metric]
            )
        comparison_rows.append(record)
    placement_dispatch = dispatches["PLACEMENT"]
    return utilization, pd.DataFrame.from_records(comparison_rows), placement_dispatch


def _reachable(adjacency: dict[str, set[str]], source: str, target: str) -> bool:
    if source == target:
        return True
    queue: deque[str] = deque([source])
    visited = {source}
    while queue:
        node = queue.popleft()
        for neighbor in adjacency.get(node, set()):
            if neighbor == target:
                return True
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return False


def _placement_delivery_diagnostics(
    placement_case: dict[str, Any],
    contract: pd.DataFrame,
    dispatch: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    network = placement_case["network"]
    flows = _flow_wide(network, placement_case["paths"]["flows"])
    interfaces = pd.read_csv(INTERFACE_PATH).sort_values("physical_link_id", kind="stable")
    directional = pd.read_csv(LINK_PATH)
    p_nom = contract.set_index("bus")["p_nom_MW"].astype(float).to_dict()
    generator_ids = contract.set_index("bus")["generator_id"].to_dict()
    weights = placement_case["weights"]
    headroom_rows: list[dict[str, Any]] = []
    interface_rows: list[dict[str, Any]] = []
    target_summaries: dict[str, Any] = {}

    for target in PLACEMENT_TARGETS:
        scarcity = placement_case["shedding"][target].gt(SHEDDING_TOLERANCE_MW)
        target_records: list[dict[str, Any]] = []
        binding_counts: dict[str, int] = {}
        source_counts = {
            source: {"headroom_hours": 0, "path_hours": 0, "headroom_and_path_hours": 0}
            for source in PROXY_MARKETS
        }
        for snapshot in scarcity.index[scarcity]:
            adjacency: dict[str, set[str]] = {market: set() for market in MARKETS}
            link_records: list[dict[str, Any]] = []
            for interface in interfaces.itertuples(index=False):
                link_id = interface.physical_link_id
                flow = float(flows.at[snapshot, link_id])
                a_to_b = _directional_limit_for_year(
                    directional, link_id, interface.endpoint_a, interface.endpoint_b, YEAR
                )
                b_to_a = _directional_limit_for_year(
                    directional, link_id, interface.endpoint_b, interface.endpoint_a, YEAR
                )
                residual_a_to_b = max(a_to_b - flow, 0.0)
                residual_b_to_a = max(b_to_a + flow, 0.0)
                binding_a_to_b = flow >= a_to_b - INTERFACE_LIMIT_TOLERANCE_MW
                binding_b_to_a = -flow >= b_to_a - INTERFACE_LIMIT_TOLERANCE_MW
                if residual_a_to_b > INTERFACE_LIMIT_TOLERANCE_MW:
                    adjacency[interface.endpoint_a].add(interface.endpoint_b)
                if residual_b_to_a > INTERFACE_LIMIT_TOLERANCE_MW:
                    adjacency[interface.endpoint_b].add(interface.endpoint_a)
                if binding_a_to_b:
                    key = f"{link_id}:{interface.endpoint_a}->{interface.endpoint_b}"
                    binding_counts[key] = binding_counts.get(key, 0) + 1
                if binding_b_to_a:
                    key = f"{link_id}:{interface.endpoint_b}->{interface.endpoint_a}"
                    binding_counts[key] = binding_counts.get(key, 0) + 1
                link_records.append(
                    {
                        "target_market": target,
                        "snapshot": snapshot,
                        "physical_link_id": link_id,
                        "endpoint_a": interface.endpoint_a,
                        "endpoint_b": interface.endpoint_b,
                        "signed_flow_endpoint_a_to_b_MW": flow,
                        "accepted_a_to_b_limit_MW": a_to_b,
                        "accepted_b_to_a_limit_MW": b_to_a,
                        "incremental_a_to_b_headroom_MW": residual_a_to_b,
                        "incremental_b_to_a_headroom_MW": residual_b_to_a,
                        "a_to_b_binding": bool(binding_a_to_b),
                        "b_to_a_binding": bool(binding_b_to_a),
                        "evidence_scope": "ALL_12_RETAINED_INTERFACES_AT_SAME_PLACEMENT_SCARCITY_HOUR",
                    }
                )
            record: dict[str, Any] = {
                "target_market": target,
                "snapshot": snapshot,
                "residual_placement_shedding_MW": float(
                    placement_case["shedding"].at[snapshot, target]
                ),
            }
            any_headroom = False
            any_path = False
            any_headroom_and_path = False
            total_headroom = 0.0
            for source in PROXY_MARKETS:
                proxy_dispatch = float(dispatch.at[snapshot, generator_ids[source]])
                headroom = max(float(p_nom[source]) - proxy_dispatch, 0.0)
                path_exists = _reachable(adjacency, source, target)
                has_headroom = headroom > 1.0
                source_counts[source]["headroom_hours"] += int(has_headroom)
                source_counts[source]["path_hours"] += int(path_exists)
                source_counts[source]["headroom_and_path_hours"] += int(
                    has_headroom and path_exists
                )
                record.update(
                    {
                        f"{source}_virtual_dispatch_MW": proxy_dispatch,
                        f"{source}_virtual_headroom_MW": headroom,
                        f"{source}_directional_residual_path_to_target": bool(path_exists),
                        f"{source}_simultaneous_headroom_and_path": bool(
                            has_headroom and path_exists
                        ),
                    }
                )
                total_headroom += headroom
                any_headroom |= has_headroom
                any_path |= path_exists
                any_headroom_and_path |= has_headroom and path_exists
            record.update(
                {
                    "total_virtual_headroom_MW": total_headroom,
                    "all_five_proxies_near_capacity": bool(not any_headroom),
                    "any_proxy_headroom_above_1_MW": bool(any_headroom),
                    "any_proxy_directional_residual_path": bool(any_path),
                    "any_proxy_simultaneous_headroom_and_path": bool(
                        any_headroom_and_path
                    ),
                    "path_test_interpretation": "DIRECTIONAL_RESIDUAL_GRAPH_EVIDENCE_NOT_PHYSICAL_TRANSFER_PROOF",
                }
            )
            target_records.append(record)
            headroom_rows.append(record)
            interface_rows.extend(link_records)

        records = pd.DataFrame.from_records(target_records)
        residual_hours = int(scarcity.sum())
        target_summaries[target] = {
            "residual_scarcity_hours": residual_hours,
            "residual_shedding_MWh": float(
                (placement_case["shedding"].loc[scarcity, target] * weights.loc[scarcity]).sum()
            ),
            "hours_all_five_proxies_near_capacity": int(
                records["all_five_proxies_near_capacity"].sum()
            ) if not records.empty else 0,
            "hours_any_proxy_headroom_above_1_MW": int(
                records["any_proxy_headroom_above_1_MW"].sum()
            ) if not records.empty else 0,
            "hours_any_proxy_directional_residual_path": int(
                records["any_proxy_directional_residual_path"].sum()
            ) if not records.empty else 0,
            "hours_any_proxy_simultaneous_headroom_and_path": int(
                records["any_proxy_simultaneous_headroom_and_path"].sum()
            ) if not records.empty else 0,
            "hours_proxy_headroom_without_any_residual_path": int(
                (
                    records["any_proxy_headroom_above_1_MW"]
                    & ~records["any_proxy_directional_residual_path"]
                ).sum()
            ) if not records.empty else 0,
            "source_simultaneous_counts": source_counts,
            "binding_direction_counts_during_target_scarcity": binding_counts,
        }

    headroom_columns = [
        "target_market",
        "snapshot",
        "residual_placement_shedding_MW",
        *[
            f"{source}_{suffix}"
            for source in PROXY_MARKETS
            for suffix in (
                "virtual_dispatch_MW",
                "virtual_headroom_MW",
                "directional_residual_path_to_target",
                "simultaneous_headroom_and_path",
            )
        ],
        "total_virtual_headroom_MW",
        "all_five_proxies_near_capacity",
        "any_proxy_headroom_above_1_MW",
        "any_proxy_directional_residual_path",
        "any_proxy_simultaneous_headroom_and_path",
        "path_test_interpretation",
    ]
    interface_columns = [
        "target_market",
        "snapshot",
        "physical_link_id",
        "endpoint_a",
        "endpoint_b",
        "signed_flow_endpoint_a_to_b_MW",
        "accepted_a_to_b_limit_MW",
        "accepted_b_to_a_limit_MW",
        "incremental_a_to_b_headroom_MW",
        "incremental_b_to_a_headroom_MW",
        "a_to_b_binding",
        "b_to_a_binding",
        "evidence_scope",
    ]
    headroom = pd.DataFrame.from_records(headroom_rows, columns=headroom_columns)
    interface = pd.DataFrame.from_records(interface_rows, columns=interface_columns)
    summary = {
        "schema_version": "MEM_PLACEMENT_2050_DELIVERY_SUMMARY_V1_0",
        "status": "FACTUAL_DIAGNOSTIC_NO_AUTOMATIC_INTERPRETATION",
        "targets": target_summaries,
        "simultaneous_evidence_rule": "HEADROOM_AND_ALL_INTERFACE_STATES_MEASURED_AT_SAME_PLACEMENT_RESIDUAL_SCARCITY_TIMESTAMP",
        "residual_graph_interpretation": "DIRECTIONAL_NETWORK_HEADROOM_EVIDENCE_ONLY_NOT_PHYSICAL_TRANSFER_PROOF",
        "automatic_A_to_D_classification": False,
        "production_price_acceptance": "PENDING_METHOD_REVIEW",
    }
    return headroom, interface, summary


def _scarcity_geography(
    cases: dict[str, dict[str, Any]],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    annual: dict[str, pd.Series] = {}
    for name in ("B9B", "R10", "R5", "PLACEMENT"):
        shedding = cases[name]["shedding"]
        weights = cases[name]["weights"]
        totals = shedding.mul(weights, axis=0).sum(axis=0).reindex(MARKETS)
        annual[name] = totals
        ranks = totals.rank(method="min", ascending=False).astype(int)
        system_total = float(totals.sum())
        for market in MARKETS:
            series = shedding[market]
            rows.append(
                {
                    "case": name,
                    "market": market,
                    "annual_shedding_MWh": float(totals[market]),
                    "share_of_case_shedding_percent": (
                        100.0 * float(totals[market]) / system_total if system_total else 0.0
                    ),
                    "shedding_rank": int(ranks[market]),
                    "peak_shedding_MW": float(series.max()),
                    "hours_above_1e_6_MW": int(series.gt(SHEDDING_TOLERANCE_MW).sum()),
                    "hours_above_1_MW": int(series.gt(1.0).sum()),
                }
            )
    placement = annual["PLACEMENT"]
    comparisons: dict[str, Any] = {}
    for reference_name in ("B9B", "R10", "R5"):
        reference = annual[reference_name]
        comparisons[f"PLACEMENT_vs_{reference_name}"] = {
            "markets_with_reduced_shedding": [
                market for market in MARKETS if placement[market] < reference[market] - 1e-6
            ],
            "markets_with_increased_shedding": [
                market for market in MARKETS if placement[market] > reference[market] + 1e-6
            ],
            "newly_shedding_markets": [
                market
                for market in MARKETS
                if reference[market] <= 1e-6 and placement[market] > 1e-6
            ],
            "markets_where_shedding_disappears": [
                market
                for market in MARKETS
                if reference[market] > 1e-6 and placement[market] <= 1e-6
            ],
        }
    summary = {
        "schema_version": "MEM_PLACEMENT_2050_SCARCITY_GEOGRAPHY_V1_0",
        "status": "FACTUAL_COMPARISON_NO_AUTOMATIC_MAJOR_LOCATION_CLASSIFICATION",
        "comparisons": comparisons,
        "complete_market_ranks_provided": True,
        "major_scarcity_location_threshold": None,
        "major_location_shift_determination": "PENDING_METHOD_REVIEW",
        "automatic_interpretation": False,
    }
    return pd.DataFrame.from_records(rows), summary


def _near_constant_comparison(
    cases: dict[str, dict[str, Any]],
    utilization: pd.DataFrame,
    aggregates: pd.DataFrame,
    unique: pd.DataFrame,
    capacity_control: dict[str, Any],
) -> pd.DataFrame:
    r5_dispatch = float(
        utilization.loc[utilization["case"].eq("R5"), "annual_dispatch_MWh"].sum()
    )
    placement_dispatch = float(
        utilization.loc[
            utilization["case"].eq("PLACEMENT"), "annual_dispatch_MWh"
        ].sum()
    )
    aggregate = aggregates.set_index("case")
    unique_indexed = unique.set_index("case")
    rows: list[dict[str, Any]] = []

    def add(metric: str, r5_value: float, placement_value: float, unit: str) -> None:
        rows.append(
            {
                "metric": metric,
                "unit": unit,
                "R5": r5_value,
                "PLACEMENT": placement_value,
                "PLACEMENT_minus_R5": placement_value - r5_value,
                "PLACEMENT_minus_R5_percent_of_R5": (
                    100.0 * (placement_value - r5_value) / r5_value if r5_value else 0.0
                ),
            }
        )

    add(
        "total_virtual_capacity",
        float(capacity_control["R5_total_virtual_capacity_MW"]),
        float(capacity_control["placement_total_virtual_capacity_MW"]),
        "MW",
    )
    add("annual_virtual_dispatch", r5_dispatch, placement_dispatch, "MWh")
    add(
        "system_shedding",
        float(cases["R5"]["system"]["total_shedding_MWh"]),
        float(cases["PLACEMENT"]["system"]["total_shedding_MWh"]),
        "MWh",
    )
    for column in (
        "total_VOLL_market_hours",
        "total_market_hours_above_500_EUR_per_MWh",
        "total_market_hours_above_1000_EUR_per_MWh",
        "total_market_hours_above_5000_EUR_per_MWh",
    ):
        add(
            f"exported_{column}",
            float(aggregate.at["R5", column]),
            float(aggregate.at["PLACEMENT", column]),
            "market-hours",
        )
    for column in (
        "unique_hours_any_exported_market_at_VOLL",
        "unique_hours_any_exported_market_above_500_EUR_per_MWh",
        "unique_hours_any_exported_market_above_1000_EUR_per_MWh",
        "unique_hours_any_exported_market_above_5000_EUR_per_MWh",
    ):
        add(
            f"exported_{column}",
            float(unique_indexed.at["R5", column]),
            float(unique_indexed.at["PLACEMENT", column]),
            "unique-hours",
        )
    result = pd.DataFrame.from_records(rows)
    result["comparison_purpose"] = "DISTINGUISH_MORE_CAPACITY_FROM_BETTER_LOCATION"
    result["automatic_interpretation"] = False
    return result


def write_post_solve_diagnostics(
    network: pypsa.Network,
    prepared: dict[str, Any],
) -> tuple[list[Path], dict[str, Any]]:
    config = prepared["config"]
    cases = _comparison_cases(network, prepared)
    system = _system_comparison(cases)
    market, aggregate, unique = _exported_market_diagnostics(
        cases, float(config["diagnostics"]["VOLL_EUR_per_MWh"])
    )
    mask, mask_metadata = _load_immutable_ordinary_mask(cases, config)
    ordinary, ordinary_direct = _ordinary_hour_comparisons(cases, mask)
    utilization, utilization_comparison, placement_dispatch = _virtual_supply_utilization(
        cases, prepared
    )
    headroom, interfaces, delivery_summary = _placement_delivery_diagnostics(
        cases["PLACEMENT"], prepared["contract"], placement_dispatch
    )
    geography, geography_summary = _scarcity_geography(cases)
    near_constant = _near_constant_comparison(
        cases,
        utilization,
        aggregate,
        unique,
        prepared["capacity_control"],
    )
    interpretation = {
        "schema_version": "MEM_PLACEMENT_2050_INTERPRETATION_FRAMEWORK_V1_0",
        "status": "EVIDENCE_ONLY_PENDING_METHOD_REVIEW",
        "A_PLACEMENT_HYPOTHESIS_SUPPORTED": "METHOD_REVIEW_ONLY",
        "B_PLACEMENT_HELPS_BUT_DOES_NOT_RESOLVE": "METHOD_REVIEW_ONLY",
        "C_BROADER_ADEQUACY_OR_MODEL_LIMITATION": "METHOD_REVIEW_ONLY",
        "D_PLACEMENT_PROXY_TOO_INTERVENTIONIST": "METHOD_REVIEW_ONLY",
        "numerical_auto_pass_thresholds": None,
        "automatic_classification": False,
        "automatic_successor_experiment": False,
        "production_price_acceptance": "PENDING_METHOD_REVIEW",
        "B10_authorized": False,
        "Stage_B_authorized": False,
    }

    qa = ROOT / config["phase"]["qa_directory"]
    outputs = {
        "system": qa / "MEM_Placement_2050_Four_Way_System_Comparison_v1.0.csv",
        "market": qa / "MEM_Placement_2050_Exported_Market_Four_Way_Comparison_v1.0.csv",
        "aggregate": qa / "MEM_Placement_2050_Exported_Market_Aggregates_v1.0.csv",
        "unique": qa / "MEM_Placement_2050_Exported_Market_Unique_Hours_v1.0.csv",
        "mask": qa / "MEM_Placement_2050_Ordinary_Hour_Mask_Reuse_v1.0.json",
        "ordinary": qa / "MEM_Placement_2050_Ordinary_Hour_Stability_v1.0.csv",
        "ordinary_direct": qa / "MEM_Placement_2050_Ordinary_Hour_Direct_Comparison_v1.0.csv",
        "utilization": qa / "MEM_Placement_2050_Virtual_Supply_Utilization_v1.0.csv",
        "utilization_comparison": qa / "MEM_Placement_2050_FR_GR_TN_Utilization_Comparison_v1.0.csv",
        "headroom": qa / "MEM_Placement_2050_Residual_Scarcity_Headroom_v1.0.csv",
        "interfaces": qa / "MEM_Placement_2050_Residual_Scarcity_Interface_Delivery_v1.0.csv",
        "delivery": qa / "MEM_Placement_2050_Delivery_Summary_v1.0.json",
        "geography": qa / "MEM_Placement_2050_Scarcity_Geography_v1.0.csv",
        "geography_summary": qa / "MEM_Placement_2050_Scarcity_Geography_Summary_v1.0.json",
        "near_constant": qa / "MEM_Placement_2050_R5_vs_Placement_Near_Constant_Capacity_v1.0.csv",
        "interpretation": qa / "MEM_Placement_2050_Interpretation_Framework_v1.0.json",
    }
    qa.mkdir(parents=True, exist_ok=True)
    for key, frame in (
        ("system", system),
        ("market", market),
        ("aggregate", aggregate),
        ("unique", unique),
        ("ordinary", ordinary),
        ("ordinary_direct", ordinary_direct),
        ("utilization", utilization),
        ("utilization_comparison", utilization_comparison),
        ("headroom", headroom),
        ("interfaces", interfaces),
        ("geography", geography),
        ("near_constant", near_constant),
    ):
        frame.to_csv(outputs[key], index=False, encoding="utf-8", lineterminator="\n")
    _write_json(outputs["mask"], mask_metadata)
    _write_json(outputs["delivery"], delivery_summary)
    _write_json(outputs["geography_summary"], geography_summary)
    _write_json(outputs["interpretation"], interpretation)
    return list(outputs.values()), {
        "system_rows": len(system),
        "exported_market_rows": len(market),
        "aggregate_rows": len(aggregate),
        "unique_hour_rows": len(unique),
        "ordinary_hour_mask": mask_metadata,
        "ordinary_rows": len(ordinary),
        "ordinary_direct_rows": len(ordinary_direct),
        "virtual_supply_rows": len(utilization),
        "placement_headroom_rows": len(headroom),
        "placement_interface_rows": len(interfaces),
        "near_constant_rows": len(near_constant),
        "automatic_interpretation": False,
        "technical_diagnostic_status": "PASS",
        "production_price_acceptance": "PENDING_METHOD_REVIEW",
    }


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def run_b9e(args: argparse.Namespace) -> dict[str, Any]:
    if not args.execute:
        raise SystemExit(
            "PLACEMENT_DIAGNOSTIC_NOT_EXECUTED: rerun the exact prepared command with --execute"
        )
    prepared = prepare_placement(write_artifacts=True)
    config = prepared["config"]
    execution_config = load_execution_config()
    if (
        config["solver"]["name"] != execution_config["solver"]["name"]
        or config["solver"]["options"] != execution_config["solver"]["options"]
    ):
        raise RuntimeError("PLACEMENT_SOLVER_CONFIG_DRIFT")
    started = time.perf_counter()
    qa = ROOT / config["phase"]["qa_directory"]
    log = qa / "logs/MEM_ETX7B9E_raw.log"
    _append_log(
        log,
        "ETX-7B9E placement diagnostic started; production acceptance remains pending.",
    )
    preflight = gurobi_preflight(execution_config)
    network = prepared["network"]
    linopy = create_and_validate_linopy_model(network)
    gurobi_lp = _assert_gurobi_lp(network)
    solve_started = time.perf_counter()
    status, condition = network.optimize.solve_model(
        solver_name=config["solver"]["name"],
        solver_options=config["solver"]["options"],
        log_to_console=bool(config["solver"]["log_to_console"]),
        log_fn=str(log),
    )
    solve_seconds = time.perf_counter() - solve_started
    metrics = _solve_metrics(network, str(status), str(condition), solve_seconds)
    metrics["LP_assertions"] = {
        "linopy_integer_variables": linopy["integer_variables"],
        "linopy_binary_variables": linopy["binary_variables"],
        "Gurobi_NumIntVars": gurobi_lp["NumIntVars"],
        "Gurobi_NumBinVars": gurobi_lp["NumBinVars"],
    }
    if len(network.snapshots) != 8760 or not metrics["accepted"]:
        raise RuntimeError(f"PLACEMENT_POST_SOLVE_TECHNICAL_QA_FAILED: {metrics}")
    for row in prepared["contract"].itertuples(index=False):
        dispatch = network.generators_t.p[row.generator_id].astype(float)
        if dispatch.min() < -1e-5 or dispatch.max() > float(row.p_nom_MW) + 1e-5:
            raise RuntimeError(f"PLACEMENT_DISPATCH_BOUND_FAILURE_{row.generator_id}")

    result_dir = ROOT / config["phase"]["result_directory"]
    result_artifacts, outputs = _write_solve_outputs(
        network,
        result_dir,
        config["phase"]["result_stem"],
    )
    diagnostics, diagnostic_qa = write_post_solve_diagnostics(network, prepared)
    _append_log(
        log,
        "ETX-7B9E technical placement diagnostic PASS; production-price acceptance remains pending analytical review.",
    )
    result_artifacts.extend(prepared["artifacts"])
    result_artifacts.extend(diagnostics)
    result_artifacts.append(log)
    manifest = result_dir / (
        f"{config['phase']['result_stem']}_Result_Manifest_v1.0.csv"
    )
    write_manifest(manifest, result_artifacts)
    outputs.update(
        {
            "manifest": relative_path(manifest),
            "manifest_sha256": sha256_file(manifest),
            "raw_log": relative_path(log),
            "preparation_artifacts": [
                relative_path(path) for path in prepared["artifacts"]
            ],
            "diagnostics": {
                path.stem: relative_path(path) for path in diagnostics
            },
        }
    )
    after = verify_placement_inputs(config)
    receipt = build_receipt(
        phase=config["phase"]["id"],
        gate=config["phase"]["gate"],
        status="PASS",
        input_manifests=[
            {
                "input_id": "B8D_2040_ACCEPTED_SOURCE",
                "path": relative_path(
                    after["accepted"]["b8d_2040_production_source"]["receipt_path"]
                ),
                "observed_sha256": after["accepted"][
                    "b8d_2040_production_source"
                ]["receipt_sha256"],
                "status": "PASS",
            },
            {
                "input_id": "B9B_2050_BOUNDED_BASELINE",
                "path": relative_path(after["b9b"]["receipt_path"]),
                "observed_sha256": after["b9b"]["receipt_sha256"],
                "status": "PASS",
            },
            {
                "input_id": "B9C_2050_R10_DIAGNOSTIC",
                "path": relative_path(
                    after["accepted"]["b9c_2050_R10_diagnostic"]["receipt_path"]
                ),
                "observed_sha256": after["accepted"][
                    "b9c_2050_R10_diagnostic"
                ]["receipt_sha256"],
                "status": "PASS",
            },
            {
                "input_id": "B9D_2050_R5_DIAGNOSTIC",
                "path": relative_path(
                    after["accepted"]["b9d_2050_R5_diagnostic"]["receipt_path"]
                ),
                "observed_sha256": after["accepted"][
                    "b9d_2050_R5_diagnostic"
                ]["receipt_sha256"],
                "status": "PASS",
            },
            {
                "input_id": "B6_2050_UNSOLVED_NETWORK",
                "path": config["accepted_inputs"]["b6_2050_unsolved_network"]["path"],
                "observed_sha256": after["b6_network_sha256"],
                "status": "PASS",
            },
        ],
        outputs=outputs,
        qa={
            "preflight": preflight,
            "structural_delta": prepared["structural"],
            "capacity_control": prepared["capacity_control"],
            "LP_assertions": {**linopy, **gurobi_lp},
            "post_solve": metrics,
            "comparisons": diagnostic_qa,
            "technical_diagnostic_status": "TECHNICAL_PLACEMENT_DIAGNOSTIC_PASS",
            "economic_methodological_acceptance": "PENDING_METHOD_REVIEW",
            "production_price_source": False,
            "automatic_capacity_iteration": False,
            "automatic_successor_experiment": False,
            "B10_authorized": False,
            "Stage_B_authorized": False,
        },
        next_gate="METHOD_REVIEW_REQUIRED_NO_AUTOMATIC_PRODUCTION_PROMOTION",
        command=command_string(module=CANONICAL_MODULE),
        runtime_seconds=time.perf_counter() - started,
        solve=metrics,
    )
    write_receipt(ROOT / config["phase"]["receipt"], receipt)
    return receipt


def _failure_receipt(error: Exception) -> Path:
    config = load_placement_config()
    receipt = build_receipt(
        phase=config["phase"]["id"],
        gate=config["phase"]["gate"],
        status="FAIL",
        input_manifests=[],
        outputs={"production_result_accepted": False},
        qa={
            "error_type": type(error).__name__,
            "error": str(error),
            "technical_diagnostic_status": "FAIL",
            "production_price_acceptance": "NOT_REACHED",
            "B10_authorized": False,
            "Stage_B_authorized": False,
        },
        next_gate="STOP_AND_RETURN_RECEIPT",
        command=command_string(module=CANONICAL_MODULE),
    )
    return write_receipt(ROOT / config["phase"]["receipt"], receipt)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MEM Stage-A 2050 perimeter-placement diagnostic"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "prepare", help="Run unsolved placement preparation and structural QA"
    )
    run = subparsers.add_parser(
        "b9e", help="Run the guarded 2050 placement full-year diagnostic"
    )
    run.add_argument(
        "--execute",
        action="store_true",
        help="Required explicit manual execution acknowledgement",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "prepare":
        try:
            prepared = prepare_placement(write_artifacts=True)
        except Exception as error:
            print(json.dumps({"status": "FAIL", "error": str(error)}, indent=2))
            raise SystemExit(1) from error
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "phase": prepared["config"]["phase"]["id"],
                    "classification": prepared["config"]["capacity_design"][
                        "classification"
                    ],
                    "capacities_MW": prepared["payload"]["capacities_MW"],
                    "capacity_control": prepared["capacity_control"],
                    "marginal_cost_EUR2025_per_MWh_el": prepared["payload"][
                        "marginal_cost_EUR2025_per_MWh_el"
                    ],
                    "production_optimization_executed": False,
                    "manual_command": prepared["config"]["phase"]["command"],
                    "B10_authorized": False,
                    "Stage_B_authorized": False,
                },
                indent=2,
            )
        )
        return
    try:
        receipt = run_b9e(args)
    except SystemExit:
        raise
    except Exception as error:
        path = _failure_receipt(error)
        print(
            json.dumps(
                {"status": "FAIL", "receipt": relative_path(path), "error": str(error)},
                indent=2,
            )
        )
        raise SystemExit(1) from error
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "gate": receipt["gate"],
                "receipt": load_placement_config()["phase"]["receipt"],
                "technical_diagnostic_status": "TECHNICAL_PLACEMENT_DIAGNOSTIC_PASS",
                "production_price_acceptance": "PENDING_METHOD_REVIEW",
                "B10_authorized": False,
                "Stage_B_authorized": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

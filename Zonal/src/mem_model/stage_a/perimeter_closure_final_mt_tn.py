"""Run the single bounded ETX-7B9H 2050 MT/TN closure diagnostic.

ETX-7B9H starts from the immutable solved ETX-7B9G network and adds exactly
two fixed virtual generators.  The MT increment is the conditional-positive
P99 of B9G local shedding; the milder TN increment is the existing residual
R10 capacity.  Neither value is derived from an annual-price target.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import zipfile
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
    _directional_limit_for_year,
    _flow_wide,
    _require_hash,
    _result_paths,
    _system_metrics_from_outputs,
    residual_energy_capacity,
)
from mem_model.stage_a.perimeter_closure_r5 import _dispatch_wide, _verify_phase
from mem_model.stage_a.receipts import (
    build_receipt,
    command_string,
    relative_path,
    sha256_file,
    write_manifest,
    write_receipt,
)


CONFIG_PATH = ROOT / "config/stage_a_perimeter_closure_final_mt_tn_2050.yaml"
CANONICAL_MODULE = "mem_model.stage_a.perimeter_closure_final_mt_tn"
YEAR = 2050
EXPORTED_MARKETS = ("FR", "CH", "AT", "SI", "ME", "GR", "MT", "TN")
TARGET_MARKETS = ("MT", "TN")
R10_SHARE = 0.10
R5_SHARE = 0.05
AT_CAPACITY_TOLERANCE_MW = 1.0
NEW_GENERATOR_IDS = {
    "MT": "EXTERNAL_VIRTUAL_SUPPLY_MT_FINAL_RESIDUAL_P99_2050",
    "TN": "EXTERNAL_VIRTUAL_SUPPLY_TN_FINAL_RESIDUAL_R10_2050",
}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(message.rstrip() + "\n")


def load_b9h_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if config.get("schema") != "PERIMETER_CLOSURE_2050_FINAL_MT_TN_DIAGNOSTIC_V1_0":
        raise RuntimeError("B9H_CONFIG_SCHEMA_MISMATCH")
    if config.get("status") != "PREPARED_FOR_SINGLE_EXECUTION":
        raise RuntimeError("B9H_CONFIG_STATUS_MISMATCH")
    design = config["capacity_design"]
    if tuple(design["incremental_markets"]) != TARGET_MARKETS:
        raise RuntimeError("B9H_INCREMENT_SCOPE_MISMATCH")
    if "ME" not in set(design["prohibited_increment_markets"]):
        raise RuntimeError("B9H_ME_MUST_BE_PROHIBITED")
    if float(design["diagnostic_R10_residual_energy_share"]) != R10_SHARE:
        raise RuntimeError("B9H_R10_SHARE_MISMATCH")
    if float(design["diagnostic_R5_residual_energy_share"]) != R5_SHARE:
        raise RuntimeError("B9H_R5_SHARE_MISMATCH")
    if tuple(config["diagnostics"]["exported_markets"]) != EXPORTED_MARKETS:
        raise RuntimeError("B9H_EXPORTED_MARKET_SCOPE_MISMATCH")
    if tuple(config["diagnostics"]["detailed_markets"]) != TARGET_MARKETS:
        raise RuntimeError("B9H_DETAILED_MARKET_SCOPE_MISMATCH")
    return config


def _b9g_result_paths(config: dict[str, Any]) -> dict[str, Path]:
    spec = config["accepted_inputs"]["b9g_2050_technical_parent"]
    return _result_paths(ROOT / spec["result_directory"], spec["result_stem"])


def verify_b9g_parent(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Verify the complete B9G parent result and its explicitly frozen members."""

    config = config or load_b9h_config()
    method = config["method_authority"]
    method_sha = _require_hash(
        ROOT / method["path"], method["sha256"], "B9H_METHOD_AUTHORITY"
    )
    spec = config["accepted_inputs"]["b9g_2050_technical_parent"]
    phase = _verify_phase(spec, "B9G_2050_TECHNICAL_PARENT")
    receipt = phase["receipt"]
    if receipt.get("qa", {}).get("technical_diagnostic_status") != (
        "TECHNICAL_B9G_DIAGNOSTIC_PASS"
    ):
        raise RuntimeError("B9H_B9G_TECHNICAL_STATUS_MISMATCH")
    paths = _b9g_result_paths(config)
    missing = [relative_path(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"B9H_B9G_RESULT_MEMBER_MISSING: {missing}")
    solved_sha = _require_hash(
        ROOT / spec["solved_network"],
        spec["solved_network_sha256"],
        "B9H_B9G_SOLVED_NETWORK",
    )
    contract_path = ROOT / spec["virtual_supply_contract"]
    contract_sha = _require_hash(
        contract_path,
        spec["virtual_supply_contract_sha256"],
        "B9H_B9G_VIRTUAL_CONTRACT",
    )
    if paths["solved"] != ROOT / spec["solved_network"]:
        raise RuntimeError("B9H_B9G_SOLVED_PATH_MISMATCH")
    return {
        "status": "PASS",
        "method_sha256": method_sha,
        "phase": phase,
        "receipt": receipt,
        "receipt_path": phase["receipt_path"],
        "receipt_sha256": phase["receipt_sha256"],
        "manifest": phase["manifest"],
        "paths": paths,
        "solved_network_path": paths["solved"],
        "solved_network_sha256": solved_sha,
        "contract_path": contract_path,
        "contract_sha256": contract_sha,
    }


def _load_parent_tables(
    parent: pypsa.Network,
    verification: dict[str, Any],
    contract: pd.DataFrame,
) -> dict[str, Any]:
    paths = verification["paths"]
    load, shedding, prices, weights = _normalize_market_tables(
        parent, paths["shedding"], paths["prices"]
    )
    dispatch = _dispatch_wide(
        paths["dispatch"], parent.snapshots, contract["generator_id"].astype(str)
    )
    flows = _flow_wide(parent, paths["flows"])
    return {
        "load": load,
        "shedding": shedding,
        "prices": prices,
        "weights": weights,
        "dispatch": dispatch,
        "flows": flows,
        "paths": paths,
    }


def _benchmark(series: pd.Series, capacity_MW: float) -> dict[str, Any]:
    clean = series.astype(float).clip(lower=0.0)
    residual = (clean - float(capacity_MW)).clip(lower=0.0)
    total = float(clean.sum())
    return {
        "capacity_MW": float(capacity_MW),
        "residual_MWh": float(residual.sum()),
        "residual_energy_share": float(residual.sum() / total) if total else 0.0,
        "hours_remaining_above_1e_6_MW": int(
            residual.gt(SHEDDING_TOLERANCE_MW).sum()
        ),
        "hours_remaining_above_1_MW": int(residual.gt(1.0).sum()),
        "peak_residual_MW": float(residual.max()),
    }


def derive_b9g_residual_scarcity(
    config: dict[str, Any],
    parent_data: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    """Calculate B9G residual evidence and make the two deterministic selections."""

    voll = float(config["diagnostics"]["VOLL_EUR_per_MWh"])
    selections: dict[str, float] = {}
    diagnostic_rows: list[dict[str, Any]] = []
    selection_rows: list[dict[str, Any]] = []
    for market in TARGET_MARKETS:
        shedding = parent_data["shedding"][market].astype(float).clip(lower=0.0)
        positive = shedding.loc[shedding.gt(SHEDDING_TOLERANCE_MW)]
        prices = parent_data["prices"][market].astype(float)
        at_voll = pd.Series(
            np.isclose(
                prices.to_numpy(),
                voll,
                atol=VOLL_TOLERANCE_EUR_PER_MWH,
                rtol=0.0,
            ),
            index=prices.index,
        )
        local_scarcity = shedding.gt(SHEDDING_TOLERANCE_MW)
        r10 = residual_energy_capacity(shedding.to_numpy(dtype=float), R10_SHARE)
        r5 = residual_energy_capacity(shedding.to_numpy(dtype=float), R5_SHARE)
        quantiles_all = {
            q: float(shedding.quantile(q)) for q in (0.90, 0.95, 0.99)
        }
        quantiles_cond = {
            q: float(positive.quantile(q)) if len(positive) else 0.0
            for q in (0.90, 0.95, 0.99)
        }
        benchmarks = {
            "R10": _benchmark(shedding, float(r10["capacity_MW"])),
            "R5": _benchmark(shedding, float(r5["capacity_MW"])),
            "all_hour_P90": _benchmark(shedding, quantiles_all[0.90]),
            "all_hour_P95": _benchmark(shedding, quantiles_all[0.95]),
            "all_hour_P99": _benchmark(shedding, quantiles_all[0.99]),
            "conditional_positive_P90": _benchmark(
                shedding, quantiles_cond[0.90]
            ),
            "conditional_positive_P95": _benchmark(
                shedding, quantiles_cond[0.95]
            ),
            "conditional_positive_P99": _benchmark(
                shedding, quantiles_cond[0.99]
            ),
        }
        rule = config["capacity_design"]["selection"][market]["rule"]
        if rule == "CONDITIONAL_POSITIVE_SHEDDING_P99":
            selected = quantiles_cond[0.99]
        elif rule == "RESIDUAL_R10":
            selected = float(r10["capacity_MW"])
        else:
            raise RuntimeError(f"B9H_UNSUPPORTED_SELECTION_RULE_{market}_{rule}")
        expected = float(
            config["capacity_design"]["selection"][market]["expected_increment_MW"]
        )
        if not np.isclose(selected, expected, atol=5e-9, rtol=0.0):
            raise RuntimeError(f"B9H_SELECTED_INCREMENT_QA_MISMATCH_{market}")
        selections[market] = float(selected)
        selected_benchmark = _benchmark(shedding, selected)
        row: dict[str, Any] = {
            "market": market,
            "B9G_residual_shedding_MWh": float(shedding.sum()),
            "B9G_shedding_hours_above_1e_6_MW": int(local_scarcity.sum()),
            "B9G_shedding_hours_above_1_MW": int(shedding.gt(1.0).sum()),
            "B9G_peak_residual_shedding_MW": float(shedding.max()),
            "B9G_mean_residual_shedding_MW_conditional_positive": (
                float(positive.mean()) if len(positive) else 0.0
            ),
            "B9G_all_hour_P90_MW": quantiles_all[0.90],
            "B9G_all_hour_P95_MW": quantiles_all[0.95],
            "B9G_all_hour_P99_MW": quantiles_all[0.99],
            "B9G_conditional_positive_P90_MW": quantiles_cond[0.90],
            "B9G_conditional_positive_P95_MW": quantiles_cond[0.95],
            "B9G_conditional_positive_P99_MW": quantiles_cond[0.99],
            "B9G_residual_R10_MW": float(r10["capacity_MW"]),
            "B9G_residual_R10_verified_energy_share": float(
                r10["verified_residual_energy_share"]
            ),
            "B9G_residual_R5_MW": float(r5["capacity_MW"]),
            "B9G_residual_R5_verified_energy_share": float(
                r5["verified_residual_energy_share"]
            ),
            "B9G_VOLL_hours": int(at_voll.sum()),
            "B9G_VOLL_hours_with_local_shedding": int(
                (at_voll & local_scarcity).sum()
            ),
            "B9G_VOLL_hours_without_local_shedding": int(
                (at_voll & ~local_scarcity).sum()
            ),
            "selected_rule": rule,
            "selected_increment_MW": float(selected),
            "selected_residual_MWh": selected_benchmark["residual_MWh"],
            "selected_residual_energy_share": selected_benchmark[
                "residual_energy_share"
            ],
            "selected_hours_remaining_above_1e_6_MW": selected_benchmark[
                "hours_remaining_above_1e_6_MW"
            ],
            "selected_hours_remaining_above_1_MW": selected_benchmark[
                "hours_remaining_above_1_MW"
            ],
            "selected_peak_residual_MW": selected_benchmark["peak_residual_MW"],
        }
        for label, benchmark in benchmarks.items():
            row[f"{label}_residual_MWh"] = benchmark["residual_MWh"]
            row[f"{label}_residual_energy_share"] = benchmark[
                "residual_energy_share"
            ]
            row[f"{label}_hours_remaining_above_1e_6_MW"] = benchmark[
                "hours_remaining_above_1e_6_MW"
            ]
            row[f"{label}_hours_remaining_above_1_MW"] = benchmark[
                "hours_remaining_above_1_MW"
            ]
        diagnostic_rows.append(row)
        selection_rows.append(
            {
                "market": market,
                "source_case": "ETX-7B9G",
                "source_series": "LOCAL_HOURLY_LOAD_SHEDDING",
                "selection_rule": rule,
                "selected_increment_MW": float(selected),
                "selection_intent": config["capacity_design"]["selection"][market][
                    "intent"
                ],
                "selected_residual_energy_share": selected_benchmark[
                    "residual_energy_share"
                ],
                "selected_residual_MWh": selected_benchmark["residual_MWh"],
                "selected_hours_remaining_above_1_MW": selected_benchmark[
                    "hours_remaining_above_1_MW"
                ],
                "price_target_used_for_sizing": False,
                "manual_tuning": False,
                "additive_to_B9G": True,
            }
        )
    return (
        pd.DataFrame.from_records(diagnostic_rows),
        pd.DataFrame.from_records(selection_rows),
        selections,
    )


def _local_interface_state(
    market: str,
    flows: pd.DataFrame,
) -> list[dict[str, Any]]:
    interfaces = pd.read_csv(INTERFACE_PATH)
    directional = pd.read_csv(LINK_PATH)
    local = interfaces.loc[
        interfaces["endpoint_a"].eq(market)
        | interfaces["endpoint_b"].eq(market)
    ].sort_values("physical_link_id", kind="stable")
    result: list[dict[str, Any]] = []
    for row in local.itertuples(index=False):
        flow = flows[row.physical_link_id].astype(float)
        a_to_b = _directional_limit_for_year(
            directional, row.physical_link_id, row.endpoint_a, row.endpoint_b, YEAR
        )
        b_to_a = _directional_limit_for_year(
            directional, row.physical_link_id, row.endpoint_b, row.endpoint_a, YEAR
        )
        if row.endpoint_b == market:
            incoming = flow
            outgoing = -flow
            incoming_limit = a_to_b
            outgoing_limit = b_to_a
            incoming_direction = f"{row.endpoint_a}->{row.endpoint_b}"
        else:
            incoming = -flow
            outgoing = flow
            incoming_limit = b_to_a
            outgoing_limit = a_to_b
            incoming_direction = f"{row.endpoint_b}->{row.endpoint_a}"
        result.append(
            {
                "physical_link_id": row.physical_link_id,
                "endpoint_a": row.endpoint_a,
                "endpoint_b": row.endpoint_b,
                "incoming_direction": incoming_direction,
                "incoming": incoming,
                "outgoing": outgoing,
                "incoming_limit": float(incoming_limit),
                "outgoing_limit": float(outgoing_limit),
                "incoming_binding": (
                    float(incoming_limit) - incoming
                ).le(INTERFACE_LIMIT_TOLERANCE_MW),
                "outgoing_binding": (
                    float(outgoing_limit) - outgoing
                ).le(INTERFACE_LIMIT_TOLERANCE_MW),
            }
        )
    return result


def derive_b9g_interface_context(
    config: dict[str, Any],
    parent_data: dict[str, Any],
) -> pd.DataFrame:
    voll = float(config["diagnostics"]["VOLL_EUR_per_MWh"])
    rows: list[dict[str, Any]] = []
    for market in TARGET_MARKETS:
        shedding = parent_data["shedding"][market].astype(float)
        scarcity = shedding.gt(SHEDDING_TOLERANCE_MW)
        price = parent_data["prices"][market].astype(float)
        at_voll = pd.Series(
            np.isclose(
                price.to_numpy(),
                voll,
                atol=VOLL_TOLERANCE_EUR_PER_MWH,
                rtol=0.0,
            ),
            index=price.index,
        )
        states = _local_interface_state(market, parent_data["flows"])
        any_incoming = pd.Series(False, index=price.index)
        any_outgoing = pd.Series(False, index=price.index)
        for state in states:
            any_incoming |= state["incoming_binding"]
            any_outgoing |= state["outgoing_binding"]
        rows.append(
            {
                "market": market,
                "physical_interface_count": len(states),
                "B9G_local_shedding_MWh": float(shedding.sum()),
                "B9G_local_scarcity_hours": int(scarcity.sum()),
                "B9G_local_scarcity_hours_any_incoming_interface_saturated": int(
                    (scarcity & any_incoming).sum()
                ),
                "B9G_local_scarcity_hours_any_outgoing_interface_saturated": int(
                    (scarcity & any_outgoing).sum()
                ),
                "B9G_VOLL_hours": int(at_voll.sum()),
                "B9G_VOLL_hours_with_local_shedding": int(
                    (at_voll & scarcity).sum()
                ),
                "B9G_VOLL_hours_without_local_shedding": int(
                    (at_voll & ~scarcity).sum()
                ),
                "B9G_VOLL_hours_any_incoming_interface_saturated": int(
                    (at_voll & any_incoming).sum()
                ),
                "B9G_VOLL_hours_any_outgoing_interface_saturated": int(
                    (at_voll & any_outgoing).sum()
                ),
            }
        )
    return pd.DataFrame.from_records(rows)


def build_b9h_contract(
    config: dict[str, Any],
    parent: pypsa.Network,
    parent_contract: pd.DataFrame,
    increments: dict[str, float],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if len(parent_contract) != 8 or parent_contract["generator_id"].duplicated().any():
        raise RuntimeError("B9H_B9G_CONTRACT_NOT_EXACTLY_EIGHT_UNIQUE_GENERATORS")
    expected_ids = set(parent_contract["generator_id"].astype(str))
    observed_ids = set(
        parent.generators.index[
            parent.generators.carrier.astype(str).eq(VIRTUAL_CARRIER)
        ]
    )
    if observed_ids != expected_ids:
        raise RuntimeError("B9H_B9G_NETWORK_CONTRACT_SCOPE_MISMATCH")
    indexed = parent_contract.set_index("generator_id")
    for generator_id in expected_ids:
        row = indexed.loc[generator_id]
        observed = parent.generators.loc[generator_id]
        if not (
            observed.bus == row.bus
            and observed.carrier == VIRTUAL_CARRIER
            and np.isclose(
                float(observed.p_nom), float(row.p_nom_MW), atol=0.0, rtol=0.0
            )
            and np.isclose(
                float(observed.marginal_cost),
                float(row.marginal_cost_EUR2025_per_MWh_el),
                atol=1e-12,
                rtol=0.0,
            )
            and not bool(observed.p_nom_extendable)
            and not bool(observed.committable)
        ):
            raise RuntimeError(f"B9H_B9G_PROXY_PARAMETER_DRIFT_{generator_id}")
    common_cost = float(config["formulation"]["marginal_cost_EUR2025_per_MWh_el"])
    if not np.allclose(
        parent_contract["marginal_cost_EUR2025_per_MWh_el"].astype(float),
        common_cost,
        atol=1e-12,
        rtol=0.0,
    ):
        raise RuntimeError("B9H_B9G_COMMON_COST_MISMATCH")

    contract = parent_contract.copy()
    contract["b9h_role"] = "IMMUTABLE_B9G_PARENT_CAPACITY"
    base_columns = list(parent_contract.columns)
    new_rows: list[dict[str, Any]] = []
    for market in TARGET_MARKETS:
        row = {column: np.nan for column in base_columns}
        row.update(
            {
                "generator_id": NEW_GENERATOR_IDS[market],
                "bus": market,
                "carrier": VIRTUAL_CARRIER,
                "p_nom_MW": float(increments[market]),
                "capacity_source": "DERIVED_FROM_IMMUTABLE_ETX7B9G_LOCAL_RESIDUAL_SHEDDING",
                "capacity_sizing_rule": config["capacity_design"]["selection"][market][
                    "rule"
                ],
                "mechanical_source_residual_energy_share": (
                    np.nan if market == "MT" else R10_SHARE
                ),
                "p_nom_extendable": False,
                "committable": False,
                "p_min_pu": 0.0,
                "p_max_pu": 1.0,
                "efficiency": 1.0,
                "marginal_cost_EUR2025_per_MWh_el": common_cost,
                "cost_source_asset_id": "ETX7B2_IT_2050_METHANE_GT_OCGT_NON_CHP_0AA7D5D9AF",
                "interpretation": "FINAL_RESIDUAL_FIRM_EXTERNAL_ADEQUACY_SUPPORT",
                "status": "B9H_FIXED_INCREMENTAL_VIRTUAL_EXTERNAL_SUPPLY",
                "network_parameter_delta_vs_B9G": "NEW_GENERATOR_ONLY",
                "b9g_role": "NOT_APPLICABLE_NEW_B9H_INCREMENT",
                "b9h_role": "FINAL_INCREMENTAL_CAPACITY",
            }
        )
        new_rows.append(row)
    contract = pd.concat(
        [contract, pd.DataFrame.from_records(new_rows)], ignore_index=True, sort=False
    )
    if len(contract) != 10 or set(contract["generator_id"]) != (
        expected_ids | set(NEW_GENERATOR_IDS.values())
    ):
        raise RuntimeError("B9H_CONTRACT_EXACT_TEN_GENERATOR_FAILURE")
    if contract["marginal_cost_EUR2025_per_MWh_el"].astype(float).nunique() != 1:
        raise RuntimeError("B9H_COMMON_MARGINAL_COST_FAILURE")

    existing_by_bus = (
        parent_contract.groupby("bus", sort=False)["p_nom_MW"].sum().astype(float)
    )
    b9g_total = float(parent_contract["p_nom_MW"].astype(float).sum())
    controls = {
        "B9G_total_virtual_capacity_MW": b9g_total,
        "B9H_increment_total_MW": float(sum(increments.values())),
        "B9H_total_virtual_capacity_MW": float(contract["p_nom_MW"].astype(float).sum()),
        "B9G_virtual_generator_count": 8,
        "B9H_increment_generator_count": 2,
        "B9H_total_virtual_generator_count": 10,
        "MT_B9G_existing_virtual_capacity_MW": float(existing_by_bus["MT"]),
        "MT_B9H_increment_MW": float(increments["MT"]),
        "MT_B9H_final_virtual_capacity_MW": float(
            existing_by_bus["MT"] + increments["MT"]
        ),
        "TN_existing_original_proxy_MW": float(
            indexed.at["EXTERNAL_VIRTUAL_SUPPLY_TN_R10_2050", "p_nom_MW"]
        ),
        "TN_B9G_residual_addition_MW": float(
            indexed.at[
                "EXTERNAL_VIRTUAL_SUPPLY_TN_RESIDUAL_R10_2050", "p_nom_MW"
            ]
        ),
        "TN_B9G_total_virtual_capacity_MW": float(existing_by_bus["TN"]),
        "TN_B9H_increment_MW": float(increments["TN"]),
        "TN_B9H_final_virtual_capacity_MW": float(
            existing_by_bus["TN"] + increments["TN"]
        ),
    }
    return contract, controls


def add_b9h_increments(
    parent: pypsa.Network,
    contract: pd.DataFrame,
) -> pypsa.Network:
    network = parent.copy()
    if VIRTUAL_CARRIER not in network.carriers.index:
        raise RuntimeError("B9H_PARENT_VIRTUAL_CARRIER_MISSING")
    new_rows = contract.loc[contract["generator_id"].isin(NEW_GENERATOR_IDS.values())]
    if set(network.generators.index) & set(NEW_GENERATOR_IDS.values()):
        raise RuntimeError("B9H_INCREMENT_GENERATOR_ALREADY_PRESENT")
    for row in new_rows.itertuples(index=False):
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
        **dict(parent.meta),
        "auxiliary_experiment": "FINAL_RESIDUAL_MT_TN_CLOSURE_2050",
        "predecessor": "ETX-7B9G_IMMUTABLE_SOLVED_PARENT",
        "method": "PERIMETER_CLOSURE_METHOD_V2_0",
        "increment_source": "IMMUTABLE_B9G_LOCAL_RESIDUAL_SHEDDING",
        "technical_status": "PREPARED_FOR_SINGLE_EXECUTION",
        "production_price_acceptance": "PENDING_METHOD_REVIEW",
        "automatic_successor_experiment": False,
    }
    return network


def validate_b9h_structural_delta(
    parent: pypsa.Network,
    network: pypsa.Network,
    contract: pd.DataFrame,
) -> dict[str, Any]:
    new_ids = set(NEW_GENERATOR_IDS.values())
    if not parent.snapshots.equals(network.snapshots):
        raise RuntimeError("B9H_SNAPSHOT_DRIFT")
    _assert_frame_equal(
        parent.snapshot_weightings,
        network.snapshot_weightings,
        "B9H_SNAPSHOT_WEIGHTS",
    )
    for component in ("carriers", "buses", "loads", "links", "stores"):
        _assert_frame_equal(
            getattr(parent, component),
            getattr(network, component),
            f"B9H_{component.upper()}",
        )
    if set(network.generators.index) != set(parent.generators.index) | new_ids:
        raise RuntimeError("B9H_GENERATOR_DELTA_NOT_EXACT")
    if set(network.generators.columns) != set(parent.generators.columns):
        raise RuntimeError("B9H_GENERATOR_SCHEMA_DRIFT")
    _assert_frame_equal(
        parent.generators,
        network.generators.loc[parent.generators.index, parent.generators.columns],
        "B9H_EXISTING_GENERATORS",
    )
    for holder_name in ("buses_t", "loads_t", "links_t", "stores_t"):
        before = _dynamic_tables(getattr(parent, holder_name))
        after = _dynamic_tables(getattr(network, holder_name))
        if set(before) != set(after):
            raise RuntimeError(f"B9H_DYNAMIC_SCHEMA_DRIFT_{holder_name}")
        for attribute in before:
            _assert_frame_equal(
                before[attribute],
                after[attribute],
                f"B9H_{holder_name}_{attribute}",
            )
    before_generators = _dynamic_tables(parent.generators_t)
    after_generators = _dynamic_tables(network.generators_t)
    if set(before_generators) != set(after_generators):
        raise RuntimeError("B9H_GENERATOR_DYNAMIC_SCHEMA_DRIFT")
    for attribute, before in before_generators.items():
        after = after_generators[attribute]
        unexpected = set(after.columns) - set(before.columns) - new_ids
        if unexpected:
            raise RuntimeError(
                f"B9H_UNEXPECTED_DYNAMIC_GENERATOR_COLUMNS_{attribute}: "
                f"{sorted(unexpected)}"
            )
        _assert_frame_equal(
            before,
            after.loc[:, before.columns],
            f"B9H_EXISTING_GENERATOR_DYNAMIC_{attribute}",
        )
    new_contract = contract.loc[contract["generator_id"].isin(new_ids)]
    if len(new_contract) != 2 or set(new_contract["bus"]) != set(TARGET_MARKETS):
        raise RuntimeError("B9H_INCREMENT_SCOPE_NOT_EXACT_MT_TN")
    for row in new_contract.itertuples(index=False):
        observed = network.generators.loc[row.generator_id]
        if not (
            observed.bus == row.bus
            and observed.carrier == VIRTUAL_CARRIER
            and np.isclose(
                float(observed.p_nom), float(row.p_nom_MW), atol=0.0, rtol=0.0
            )
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
            raise RuntimeError(f"B9H_INCREMENT_PARAMETER_FAILURE_{row.generator_id}")
    before_counts = _component_counts(parent)
    after_counts = _component_counts(network)
    expected_counts = dict(before_counts)
    expected_counts["generators"] += 2
    if after_counts != expected_counts:
        raise RuntimeError("B9H_COMPONENT_COUNT_DRIFT")
    if set(
        network.buses.index[network.buses.carrier.eq("AC_STAGE_A_MARKET")]
    ) != set(MARKETS):
        raise RuntimeError("B9H_MARKET_BUS_SCOPE_FAILURE")
    return {
        "status": "PASS",
        "B9G_counts": before_counts,
        "B9H_counts": after_counts,
        "B9G_to_B9H_allowed_delta": {"generators": 2},
        "new_generators": sorted(new_ids),
        "new_generator_markets": list(TARGET_MARKETS),
        "ME_increment_added": False,
        "support_added_outside_MT_TN": False,
        "carrier_preservation": "EXACT",
        "physical_component_preservation": "EXACT",
        "topology_preservation": "EXACT",
        "demand_and_profile_preservation": "EXACT",
        "external_fleet_preservation": "EXACT",
        "fuel_and_CO2_preservation": "EXACT",
    }


def _preparation_paths(config: dict[str, Any]) -> dict[str, Path]:
    qa = ROOT / config["phase"]["qa_directory"]
    return {
        "sizing": qa / "MEM_B9H_2050_B9G_Residual_Scarcity_Diagnostics_v1.0.csv",
        "selection": qa / "MEM_B9H_2050_Final_Capacity_Selection_v1.0.csv",
        "contract": qa / "MEM_B9H_2050_Virtual_Supply_Contract_v1.0.csv",
        "interfaces": qa / "MEM_B9H_2050_B9G_Interface_Context_v1.0.csv",
        "qa": qa / "MEM_B9H_2050_Preparation_QA_v1.0.json",
        "manifest": qa / "MEM_B9H_2050_Preparation_Manifest_v1.0.csv",
        "verification": qa / "MEM_B9H_2050_Preparation_Final_Verification_v1.0.json",
    }


def prepare_b9h(*, write_artifacts: bool = True) -> dict[str, Any]:
    config = load_b9h_config()
    verification = verify_b9g_parent(config)
    parent = pypsa.Network(verification["solved_network_path"])
    if len(parent.snapshots) != 8760 or int(parent.meta.get("horizon", YEAR)) != YEAR:
        raise RuntimeError("B9H_PARENT_CHRONOLOGY_FAILURE")
    parent_contract = pd.read_csv(verification["contract_path"])
    parent_data = _load_parent_tables(parent, verification, parent_contract)
    sizing, selection, increments = derive_b9g_residual_scarcity(
        config, parent_data
    )
    interfaces = derive_b9g_interface_context(config, parent_data)
    contract, capacity_control = build_b9h_contract(
        config, parent, parent_contract, increments
    )
    network = add_b9h_increments(parent, contract)
    structural = validate_b9h_structural_delta(parent, network, contract)
    static_lp = validate_lp_static(network)
    short = network.copy()
    short.set_snapshots(short.snapshots[:6])
    short_lp = create_and_validate_linopy_model(short)
    if short_lp["integer_variables"] or short_lp["binary_variables"]:
        raise RuntimeError("B9H_SHORT_FIXTURE_NOT_CONTINUOUS_LP")
    payload = {
        "schema_version": "MEM_B9H_2050_PREPARATION_QA_V1_0",
        "status": "PASS",
        "phase": config["phase"]["id"],
        "classification": config["capacity_design"]["classification"],
        "B9G_immutability": {
            "receipt_sha256": verification["receipt_sha256"],
            "result_manifest_sha256": verification["manifest"]["sha256"],
            "result_manifest_member_count": verification["manifest"]["member_count"],
            "result_manifest_member_failures": verification["manifest"][
                "member_failures"
            ],
            "solved_network_sha256": verification["solved_network_sha256"],
            "virtual_supply_contract_sha256": verification["contract_sha256"],
            "status": "IMMUTABLE_TECHNICALLY_VALID_SOLVED_PARENT",
        },
        "selected_increments_MW": increments,
        "capacity_control": capacity_control,
        "marginal_cost_EUR2025_per_MWh_el": float(
            config["formulation"]["marginal_cost_EUR2025_per_MWh_el"]
        ),
        "price_target_used_for_sizing": False,
        "ME_increment_added": False,
        "structural_QA": structural,
        "static_LP": static_lp,
        "short_unsolved_Linopy_fixture": {"snapshots": 6, **short_lp},
        "production_optimization_executed": False,
        "Gurobi_optimizer_invoked": False,
        "single_production_solve_authorized": True,
        "B9I_authorized": False,
        "B10_2050": "LOCKED",
        "Stage_B_2050": "LOCKED",
    }
    paths = _preparation_paths(config)
    artifacts: list[Path] = []
    if write_artifacts:
        paths["sizing"].parent.mkdir(parents=True, exist_ok=True)
        sizing.to_csv(
            paths["sizing"], index=False, encoding="utf-8", lineterminator="\n"
        )
        selection.to_csv(
            paths["selection"], index=False, encoding="utf-8", lineterminator="\n"
        )
        contract.to_csv(
            paths["contract"], index=False, encoding="utf-8", lineterminator="\n"
        )
        interfaces.to_csv(
            paths["interfaces"], index=False, encoding="utf-8", lineterminator="\n"
        )
        _write_json(paths["qa"], payload)
        artifacts = [
            paths["sizing"],
            paths["selection"],
            paths["contract"],
            paths["interfaces"],
            paths["qa"],
        ]
        prep_members = [
            CONFIG_PATH,
            ROOT / "src/mem_model/stage_a/perimeter_closure_final_mt_tn.py",
            ROOT / "tests/test_stage_a_perimeter_closure_final_mt_tn.py",
            ROOT / "docs/runbooks/ETX7B9H_RUNBOOK.md",
            *artifacts,
        ]
        missing = [relative_path(path) for path in prep_members if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"B9H_PREPARATION_MEMBER_MISSING: {missing}")
        manifest = write_manifest(paths["manifest"], prep_members)
        _write_json(
            paths["verification"],
            {
                "schema_version": "MEM_B9H_2050_PREPARATION_FINAL_VERIFICATION_V1_0",
                "status": "PASS",
                "preparation_manifest": relative_path(paths["manifest"]),
                "preparation_manifest_sha256": sha256_file(paths["manifest"]),
                "preparation_manifest_members": len(manifest),
                "B9G_receipt_sha256": verification["receipt_sha256"],
                "B9G_result_manifest_sha256": verification["manifest"]["sha256"],
                "B9G_result_manifest_members_verified": verification["manifest"][
                    "member_count"
                ],
                "B9G_result_manifest_member_failures": verification["manifest"][
                    "member_failures"
                ],
                "B9G_solved_network_sha256": verification[
                    "solved_network_sha256"
                ],
                "selected_increments_MW": increments,
                "capacity_control": capacity_control,
                "structural_QA_status": structural["status"],
                "zero_integer_variables": short_lp["integer_variables"] == 0,
                "zero_binary_variables": short_lp["binary_variables"] == 0,
                "production_optimization_executed": False,
                "Gurobi_optimizer_invoked": False,
                "B9H": "PREPARED_FOR_SINGLE_EXECUTION",
                "B9I": "NOT_AUTHORIZED",
                "B10_2050": "LOCKED",
                "Stage_B_2050": "LOCKED",
            },
        )
        artifacts.extend([paths["manifest"], paths["verification"]])
    return {
        "config": config,
        "verification": verification,
        "parent": parent,
        "parent_contract": parent_contract,
        "parent_data": parent_data,
        "network": network,
        "sizing": sizing,
        "selection": selection,
        "increments": increments,
        "interfaces": interfaces,
        "contract": contract,
        "capacity_control": capacity_control,
        "structural": structural,
        "LP": short_lp,
        "payload": payload,
        "artifacts": artifacts,
    }


def _case_tables(
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
        "flows": _flow_wide(network, paths["flows"]),
    }


def _market_metrics(
    prices: pd.Series,
    shedding: pd.Series,
    weights: pd.Series,
    voll: float,
) -> dict[str, Any]:
    price = _price_statistics(prices, voll)
    clean_shedding = shedding.astype(float).clip(lower=0.0)
    return {
        "annual_mean_EUR_per_MWh": price["price_mean_EUR_per_MWh"],
        "median_EUR_per_MWh": price["price_median_EUR_per_MWh"],
        "P95_EUR_per_MWh": price["price_P95_EUR_per_MWh"],
        "P99_EUR_per_MWh": price["price_P99_EUR_per_MWh"],
        "maximum_EUR_per_MWh": price["price_max_EUR_per_MWh"],
        "VOLL_hours": price["hours_at_VOLL"],
        "hours_above_500_EUR_per_MWh": price[
            "hours_above_500_EUR_per_MWh"
        ],
        "shedding_MWh": float((clean_shedding * weights).sum()),
        "shedding_hours_above_1e_6_MW": int(
            clean_shedding.gt(SHEDDING_TOLERANCE_MW).sum()
        ),
        "shedding_hours_above_1_MW": int(clean_shedding.gt(1.0).sum()),
        "peak_shedding_MW": float(clean_shedding.max()),
    }


def build_market_comparison(
    cases: dict[str, dict[str, Any]],
    voll: float,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for market in EXPORTED_MARKETS:
        baseline = _market_metrics(
            cases["B9G"]["prices"][market],
            cases["B9G"]["shedding"][market],
            cases["B9G"]["weights"],
            voll,
        )
        candidate = _market_metrics(
            cases["B9H"]["prices"][market],
            cases["B9H"]["shedding"][market],
            cases["B9H"]["weights"],
            voll,
        )
        row: dict[str, Any] = {"market": market}
        row.update({f"B9G_{key}": value for key, value in baseline.items()})
        row.update({f"B9H_{key}": value for key, value in candidate.items()})
        for key in (
            "annual_mean_EUR_per_MWh",
            "median_EUR_per_MWh",
            "P95_EUR_per_MWh",
            "P99_EUR_per_MWh",
            "VOLL_hours",
            "shedding_MWh",
            "shedding_hours_above_1e_6_MW",
            "shedding_hours_above_1_MW",
            "peak_shedding_MW",
        ):
            row[f"delta_B9H_minus_B9G_{key}"] = (
                candidate[key] - baseline[key]
            )
        rows.append(row)
    return pd.DataFrame.from_records(rows)


def build_system_comparison(cases: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for case_name in ("B9G", "B9H"):
        case = cases[case_name]
        prices = case["prices"]
        shedding = case["shedding"]
        system = case["system"]
        exported_shedding = shedding.loc[:, list(EXPORTED_MARKETS)].sum(axis=1)
        rows.append(
            {
                "case": case_name,
                "objective_EUR2025": system["objective_EUR2025"],
                "total_load_MWh": system["total_load_MWh"],
                "total_shedding_MWh": system["total_shedding_MWh"],
                "shedding_percent_of_load": system[
                    "shedding_percent_of_load"
                ],
                "system_scarcity_hours_above_1e_6_MW": system[
                    "system_shedding_hours_above_1e_6_MW"
                ],
                "system_scarcity_hours_above_1_MW": system[
                    "system_shedding_hours_above_1_MW"
                ],
                "ten_market_mean_EUR_per_MWh": float(
                    prices.loc[:, list(MARKETS)].stack(future_stack=True).mean()
                ),
                "eight_export_market_mean_EUR_per_MWh": float(
                    prices.loc[:, list(EXPORTED_MARKETS)]
                    .stack(future_stack=True)
                    .mean()
                ),
                "exported_scarcity_hours_above_1_MW": int(
                    exported_shedding.gt(1.0).sum()
                ),
            }
        )
    result = pd.DataFrame.from_records(rows)
    baseline = result.loc[result["case"].eq("B9G")].iloc[0]
    for column in (
        "objective_EUR2025",
        "total_shedding_MWh",
        "shedding_percent_of_load",
        "system_scarcity_hours_above_1_MW",
        "ten_market_mean_EUR_per_MWh",
        "eight_export_market_mean_EUR_per_MWh",
    ):
        result[f"delta_vs_B9G_{column}"] = result[column] - baseline[column]
    return result


def _post_interface_diagnostics(
    config: dict[str, Any],
    case: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    threshold = float(
        config["diagnostics"]["high_price_threshold_EUR_per_MWh"]
    )
    rows: list[dict[str, Any]] = []
    summaries: dict[str, dict[str, Any]] = {}
    for market in TARGET_MARKETS:
        prices = case["prices"][market].astype(float)
        shedding = case["shedding"][market].astype(float).clip(lower=0.0)
        high = prices.gt(threshold)
        scarcity = shedding.gt(SHEDDING_TOLERANCE_MW)
        selected_hours = high | scarcity
        states = _local_interface_state(market, case["flows"])
        any_incoming = pd.Series(False, index=prices.index)
        any_outgoing = pd.Series(False, index=prices.index)
        for state in states:
            any_incoming |= state["incoming_binding"]
            any_outgoing |= state["outgoing_binding"]
            for snapshot in prices.index[selected_hours]:
                rows.append(
                    {
                        "market": market,
                        "snapshot": snapshot,
                        "price_EUR_per_MWh": float(prices.at[snapshot]),
                        "price_above_500": bool(high.at[snapshot]),
                        "local_shedding_MW": float(shedding.at[snapshot]),
                        "local_shedding_positive": bool(scarcity.at[snapshot]),
                        "physical_link_id": state["physical_link_id"],
                        "endpoint_a": state["endpoint_a"],
                        "endpoint_b": state["endpoint_b"],
                        "incoming_direction": state["incoming_direction"],
                        "incoming_flow_MW": float(state["incoming"].at[snapshot]),
                        "incoming_limit_MW": state["incoming_limit"],
                        "incoming_saturated": bool(
                            state["incoming_binding"].at[snapshot]
                        ),
                        "outgoing_flow_MW": float(state["outgoing"].at[snapshot]),
                        "outgoing_limit_MW": state["outgoing_limit"],
                        "outgoing_saturated": bool(
                            state["outgoing_binding"].at[snapshot]
                        ),
                    }
                )
        summaries[market] = {
            "physical_interface_count": len(states),
            "remaining_high_price_hours_above_500": int(high.sum()),
            "remaining_high_price_hours_any_incoming_saturated": int(
                (high & any_incoming).sum()
            ),
            "remaining_high_price_hours_any_outgoing_saturated": int(
                (high & any_outgoing).sum()
            ),
            "remaining_local_shedding_hours": int(scarcity.sum()),
            "remaining_local_shedding_hours_any_incoming_saturated": int(
                (scarcity & any_incoming).sum()
            ),
            "remaining_local_shedding_hours_any_outgoing_saturated": int(
                (scarcity & any_outgoing).sum()
            ),
        }
    return pd.DataFrame.from_records(rows), summaries


def build_mt_tn_diagnostics(
    config: dict[str, Any],
    prepared: dict[str, Any],
    cases: dict[str, dict[str, Any]],
    interface_summary: dict[str, dict[str, Any]],
) -> pd.DataFrame:
    dispatch = _dispatch_wide(
        cases["B9H"]["paths"]["dispatch"],
        cases["B9H"]["network"].snapshots,
        NEW_GENERATOR_IDS.values(),
    )
    voll = float(config["diagnostics"]["VOLL_EUR_per_MWh"])
    rows: list[dict[str, Any]] = []
    for market in TARGET_MARKETS:
        generator_id = NEW_GENERATOR_IDS[market]
        p_nom = float(prepared["increments"][market])
        generation = dispatch[generator_id].astype(float).clip(lower=0.0)
        weights = cases["B9H"]["weights"]
        b9g_scarcity = cases["B9G"]["shedding"][market].gt(
            SHEDDING_TOLERANCE_MW
        )
        remaining = cases["B9H"]["shedding"][market].astype(float).clip(lower=0.0)
        candidate_price = cases["B9H"]["prices"][market].astype(float)
        at_voll = pd.Series(
            np.isclose(
                candidate_price.to_numpy(),
                voll,
                atol=VOLL_TOLERANCE_EUR_PER_MWH,
                rtol=0.0,
            ),
            index=candidate_price.index,
        )
        annual = float((generation * weights).sum())
        record = {
            "market": market,
            "new_generator_id": generator_id,
            "incremental_capacity_MW": p_nom,
            "total_virtual_capacity_after_addition_MW": (
                prepared["capacity_control"][
                    f"{market}_B9H_final_virtual_capacity_MW"
                ]
            ),
            "annual_generation_MWh": annual,
            "capacity_factor": (
                annual / (p_nom * float(weights.sum())) if p_nom else 0.0
            ),
            "peak_dispatch_MW": float(generation.max()),
            "dispatch_hours_above_1e_6_MW": int(
                generation.gt(SHEDDING_TOLERANCE_MW).sum()
            ),
            "dispatch_hours_above_50_percent_p_nom": int(
                generation.gt(0.50 * p_nom).sum()
            ),
            "dispatch_hours_above_90_percent_p_nom": int(
                generation.gt(0.90 * p_nom).sum()
            ),
            "at_capacity_hours_within_1_MW": int(
                (p_nom - generation).le(AT_CAPACITY_TOLERANCE_MW).sum()
            ),
            "generation_during_B9G_local_scarcity_MWh": float(
                (generation.loc[b9g_scarcity] * weights.loc[b9g_scarcity]).sum()
            ),
            "generation_outside_B9G_local_scarcity_MWh": float(
                (generation.loc[~b9g_scarcity] * weights.loc[~b9g_scarcity]).sum()
            ),
            "share_generation_during_B9G_local_scarcity": (
                float(
                    (
                        generation.loc[b9g_scarcity]
                        * weights.loc[b9g_scarcity]
                    ).sum()
                )
                / annual
                if annual
                else 0.0
            ),
            "remaining_local_shedding_MWh": float((remaining * weights).sum()),
            "remaining_local_shedding_hours_above_1e_6_MW": int(
                remaining.gt(SHEDDING_TOLERANCE_MW).sum()
            ),
            "remaining_local_shedding_hours_above_1_MW": int(
                remaining.gt(1.0).sum()
            ),
            "remaining_peak_local_shedding_MW": float(remaining.max()),
            "remaining_VOLL_hours": int(at_voll.sum()),
            "remaining_VOLL_hours_with_local_shedding": int(
                (at_voll & remaining.gt(SHEDDING_TOLERANCE_MW)).sum()
            ),
            "remaining_VOLL_hours_without_local_shedding": int(
                (at_voll & ~remaining.gt(SHEDDING_TOLERANCE_MW)).sum()
            ),
            **interface_summary[market],
        }
        rows.append(record)
    return pd.DataFrame.from_records(rows)


def _post_paths(config: dict[str, Any]) -> dict[str, Path]:
    qa = ROOT / config["phase"]["qa_directory"]
    return {
        "market": qa
        / "MEM_B9H_2050_B9G_to_B9H_External_Market_Comparison_v1.0.csv",
        "system": qa / "MEM_B9H_2050_B9G_to_B9H_System_Comparison_v1.0.csv",
        "detail": qa / "MEM_B9H_2050_MT_TN_Detailed_Diagnostics_v1.0.csv",
        "interface": qa
        / "MEM_B9H_2050_MT_TN_Interface_High_Price_Diagnostics_v1.0.csv",
        "acceptance": qa / "MEM_B9H_2050_Practical_Acceptance_v1.0.json",
        "handoff": ROOT / "docs/MEM_ETX7B9H_2050_FINAL_MT_TN_CLOSURE_HANDOFF.md",
    }


def _write_handoff(
    path: Path,
    prepared: dict[str, Any],
    market: pd.DataFrame,
    system: pd.DataFrame,
    detail: pd.DataFrame,
) -> None:
    market_indexed = market.set_index("market")
    system_row = system.loc[system["case"].eq("B9H")].iloc[0]
    detail_indexed = detail.set_index("market")
    lines = [
        "# MEM ETX-7B9H — 2050 final MT/TN closure report",
        "",
        "## Status",
        "",
        "ETX-7B9H completed one Gurobi production diagnostic and remains pending analytical review.",
        "No B9I or automatic follow-on capacity change is authorized.",
        "",
        "## Frozen parent",
        "",
        f"- B9G receipt SHA-256: {prepared['verification']['receipt_sha256']}",
        f"- B9G result manifest SHA-256: {prepared['verification']['manifest']['sha256']}",
        f"- B9G solved-network SHA-256: {prepared['verification']['solved_network_sha256']}",
        "",
        "## Capacity delta",
        "",
        f"- MT increment: {prepared['increments']['MT']:.14f} MW (conditional-positive P99).",
        f"- TN increment: {prepared['increments']['TN']:.14f} MW (residual R10).",
        f"- Total B9H virtual capacity: {prepared['capacity_control']['B9H_total_virtual_capacity_MW']:.14f} MW.",
        "- ME and every other market receive no new capacity.",
        "",
        "## Result controls",
        "",
        f"- Objective: {float(system_row['objective_EUR2025']):.12f} EUR2025.",
        f"- Total shedding: {float(system_row['total_shedding_MWh']):.12f} MWh.",
        f"- Shedding share: {float(system_row['shedding_percent_of_load']):.12f}%.",
        f"- Ten-market mean: {float(system_row['ten_market_mean_EUR_per_MWh']):.12f} EUR/MWh.",
        f"- Eight-export-market mean: {float(system_row['eight_export_market_mean_EUR_per_MWh']):.12f} EUR/MWh.",
        "",
        "## MT/TN outcome",
        "",
    ]
    for market_name in TARGET_MARKETS:
        lines.append(
            f"- {market_name}: mean "
            f"{float(market_indexed.at[market_name, 'B9H_annual_mean_EUR_per_MWh']):.12f} EUR/MWh; "
            f"shedding {float(detail_indexed.at[market_name, 'remaining_local_shedding_MWh']):.12f} MWh; "
            f"VOLL hours {int(detail_indexed.at[market_name, 'remaining_VOLL_hours'])}."
        )
    lines.extend(
        [
            "",
            "## Governance",
            "",
            "- B9H: EXECUTED_TECHNICAL_PASS — PENDING_METHOD_REVIEW",
            "- B10-2050: LOCKED",
            "- Stage-B-2050: LOCKED",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_post_solve_diagnostics(
    network: pypsa.Network,
    prepared: dict[str, Any],
    result_paths: dict[str, Path],
) -> tuple[list[Path], dict[str, Any]]:
    config = prepared["config"]
    voll = float(config["diagnostics"]["VOLL_EUR_per_MWh"])
    b9g_objective = float(prepared["verification"]["receipt"]["objective"])
    cases = {
        "B9G": _case_tables(
            prepared["parent"],
            prepared["verification"]["paths"],
            b9g_objective,
            voll,
        ),
        "B9H": _case_tables(
            network,
            result_paths,
            float(network.objective),
            voll,
        ),
    }
    market = build_market_comparison(cases, voll)
    system = build_system_comparison(cases)
    interface, interface_summary = _post_interface_diagnostics(config, cases["B9H"])
    detail = build_mt_tn_diagnostics(
        config, prepared, cases, interface_summary
    )
    lower = float(
        config["diagnostics"]["indicative_annual_mean_lower_EUR_per_MWh"]
    )
    upper = float(
        config["diagnostics"]["indicative_annual_mean_upper_EUR_per_MWh"]
    )
    indexed = market.set_index("market")
    acceptance = {
        "schema_version": "MEM_B9H_2050_PRACTICAL_ACCEPTANCE_V1_0",
        "status": "PENDING_METHOD_REVIEW",
        "criterion_is_sizing_authority": False,
        "indicative_annual_mean_range_EUR_per_MWh": [lower, upper],
        "markets": {
            market_name: {
                "B9G_annual_mean_EUR_per_MWh": float(
                    indexed.at[market_name, "B9G_annual_mean_EUR_per_MWh"]
                ),
                "B9H_annual_mean_EUR_per_MWh": float(
                    indexed.at[market_name, "B9H_annual_mean_EUR_per_MWh"]
                ),
                "inside_indicative_range": bool(
                    lower
                    <= float(
                        indexed.at[
                            market_name, "B9H_annual_mean_EUR_per_MWh"
                        ]
                    )
                    <= upper
                ),
            }
            for market_name in TARGET_MARKETS
        },
        "other_export_market_deltas_EUR_per_MWh": {
            market_name: float(
                indexed.at[
                    market_name,
                    "delta_B9H_minus_B9G_annual_mean_EUR_per_MWh",
                ]
            )
            for market_name in EXPORTED_MARKETS
            if market_name not in TARGET_MARKETS
        },
        "economic_methodological_acceptance": "PENDING_METHOD_REVIEW",
        "automatic_capacity_change_after_result": False,
        "B9I_authorized": False,
        "B10_2050": "LOCKED",
        "Stage_B_2050": "LOCKED",
    }
    paths = _post_paths(config)
    paths["market"].parent.mkdir(parents=True, exist_ok=True)
    market.to_csv(
        paths["market"], index=False, encoding="utf-8", lineterminator="\n"
    )
    system.to_csv(
        paths["system"], index=False, encoding="utf-8", lineterminator="\n"
    )
    detail.to_csv(
        paths["detail"], index=False, encoding="utf-8", lineterminator="\n"
    )
    interface.to_csv(
        paths["interface"], index=False, encoding="utf-8", lineterminator="\n"
    )
    _write_json(paths["acceptance"], acceptance)
    _write_handoff(paths["handoff"], prepared, market, system, detail)
    artifacts = list(paths.values())
    qa = {
        "market_comparison_rows": len(market),
        "system_comparison_rows": len(system),
        "MT_TN_diagnostic_rows": len(detail),
        "interface_detail_rows": len(interface),
        "practical_acceptance": acceptance,
        "technical_diagnostic_only": True,
        "automatic_successor_experiment": False,
    }
    return artifacts, qa


def _validate_candidate_dispatch(
    network: pypsa.Network,
    prepared: dict[str, Any],
) -> None:
    for market, generator_id in NEW_GENERATOR_IDS.items():
        dispatch = network.generators_t.p[generator_id].astype(float)
        p_nom = float(prepared["increments"][market])
        if dispatch.min() < -1e-5 or dispatch.max() > p_nom + 1e-5:
            raise RuntimeError(f"B9H_DISPATCH_BOUND_FAILURE_{generator_id}")


def _write_result_manifest_and_receipt(
    *,
    network: pypsa.Network,
    prepared: dict[str, Any],
    metrics: dict[str, Any],
    preflight: dict[str, Any],
    linopy: dict[str, Any],
    gurobi_lp: dict[str, Any],
    result_artifacts: list[Path],
    outputs: dict[str, Any],
    diagnostics: list[Path],
    diagnostic_qa: dict[str, Any],
    log: Path,
    runtime_seconds: float,
) -> dict[str, Any]:
    config = prepared["config"]
    result_dir = ROOT / config["phase"]["result_directory"]
    source_members = [
        CONFIG_PATH,
        ROOT / "src/mem_model/stage_a/perimeter_closure_final_mt_tn.py",
        ROOT / "tests/test_stage_a_perimeter_closure_final_mt_tn.py",
        ROOT / "docs/runbooks/ETX7B9H_RUNBOOK.md",
        ROOT / "docs/MEM_ETX7B9H_IMPLEMENTATION_SCOPE.md",
    ]
    all_artifacts = [
        *result_artifacts,
        *prepared["artifacts"],
        *diagnostics,
        log,
        *source_members,
    ]
    deduplicated = list(dict.fromkeys(all_artifacts))
    missing = [relative_path(path) for path in deduplicated if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"B9H_RESULT_MANIFEST_MEMBER_MISSING: {missing}")
    manifest_path = (
        result_dir / f"{config['phase']['result_stem']}_Result_Manifest_v1.0.csv"
    )
    manifest = write_manifest(manifest_path, deduplicated)
    outputs.update(
        {
            "manifest": relative_path(manifest_path),
            "manifest_sha256": sha256_file(manifest_path),
            "manifest_member_count": len(manifest),
            "raw_log": relative_path(log),
            "preparation_artifacts": [
                relative_path(path) for path in prepared["artifacts"]
            ],
            "diagnostics": {
                path.stem: relative_path(path) for path in diagnostics
            },
        }
    )
    after = verify_b9g_parent(config)
    parent_before = prepared["verification"]
    if (
        after["receipt_sha256"] != parent_before["receipt_sha256"]
        or after["manifest"]["sha256"] != parent_before["manifest"]["sha256"]
        or after["solved_network_sha256"]
        != parent_before["solved_network_sha256"]
        or after["contract_sha256"] != parent_before["contract_sha256"]
    ):
        raise RuntimeError("B9H_B9G_PARENT_CHANGED_DURING_EXECUTION")
    lp_assertions = {**linopy, **gurobi_lp}
    receipt = build_receipt(
        phase=config["phase"]["id"],
        gate=config["phase"]["gate"],
        status="PASS",
        input_manifests=[
            {
                "input_id": "B9G_IMMUTABLE_TECHNICALLY_VALID_PARENT",
                "path": relative_path(after["receipt_path"]),
                "observed_sha256": after["receipt_sha256"],
                "status": "PASS",
            },
            {
                "input_id": "B9G_RESULT_MANIFEST",
                "path": after["manifest"]["path"],
                "observed_sha256": after["manifest"]["sha256"],
                "member_count": after["manifest"]["member_count"],
                "member_failures": after["manifest"]["member_failures"],
                "status": "PASS",
            },
            {
                "input_id": "B9G_SOLVED_NETWORK",
                "path": relative_path(after["solved_network_path"]),
                "observed_sha256": after["solved_network_sha256"],
                "status": "PASS",
            },
        ],
        outputs=outputs,
        qa={
            "preflight": preflight,
            "structural_delta": prepared["structural"],
            "capacity_selection": prepared["selection"].to_dict(
                orient="records"
            ),
            "capacity_control": prepared["capacity_control"],
            "LP_assertions": lp_assertions,
            "post_solve": metrics,
            "comparisons": diagnostic_qa,
            "technical_diagnostic_status": "TECHNICAL_B9H_DIAGNOSTIC_PASS",
            "economic_methodological_acceptance": "PENDING_METHOD_REVIEW",
            "production_price_source": False,
            "automatic_capacity_change_after_result": False,
            "automatic_successor_experiment": False,
            "B9I_authorized": False,
            "B10_2050_authorized": False,
            "Stage_B_2050_authorized": False,
        },
        next_gate="METHOD_REVIEW_REQUIRED_NO_AUTOMATIC_SUCCESSOR",
        command=config["phase"]["command"],
        runtime_seconds=runtime_seconds,
        solve=metrics,
    )
    write_receipt(ROOT / config["phase"]["receipt"], receipt)
    return receipt


def run_b9h(args: argparse.Namespace) -> dict[str, Any]:
    if not args.execute:
        raise SystemExit(
            "B9H_NOT_EXECUTED: use the exact authorized command with --execute"
        )
    prepared = prepare_b9h(write_artifacts=True)
    config = prepared["config"]
    execution_config = load_execution_config()
    if (
        config["solver"]["name"] != execution_config["solver"]["name"]
        or config["solver"]["options"] != execution_config["solver"]["options"]
    ):
        raise RuntimeError("B9H_SOLVER_CONFIG_DRIFT")
    started = time.perf_counter()
    qa_dir = ROOT / config["phase"]["qa_directory"]
    log = qa_dir / "logs/MEM_ETX7B9H_raw.log"
    if log.exists():
        raise RuntimeError("B9H_SINGLE_SOLVE_LOG_ALREADY_EXISTS")
    _append_log(
        log,
        "ETX-7B9H single final diagnostic started; acceptance remains pending analytical review.",
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
        "Gurobi_NumVars": gurobi_lp["NumVars"],
        "Gurobi_NumConstrs": gurobi_lp["NumConstrs"],
        "Gurobi_NumIntVars": gurobi_lp["NumIntVars"],
        "Gurobi_NumBinVars": gurobi_lp["NumBinVars"],
    }
    if len(network.snapshots) != 8760 or not metrics["accepted"]:
        raise RuntimeError(f"B9H_POST_SOLVE_TECHNICAL_QA_FAILED: {metrics}")
    _validate_candidate_dispatch(network, prepared)
    result_dir = ROOT / config["phase"]["result_directory"]
    result_artifacts, outputs = _write_solve_outputs(
        network, result_dir, config["phase"]["result_stem"]
    )
    result_paths = _result_paths(result_dir, config["phase"]["result_stem"])
    diagnostics, diagnostic_qa = write_post_solve_diagnostics(
        network, prepared, result_paths
    )
    _append_log(
        log,
        "ETX-7B9H technical diagnostic complete; no automatic successor or production promotion.",
    )
    return _write_result_manifest_and_receipt(
        network=network,
        prepared=prepared,
        metrics=metrics,
        preflight=preflight,
        linopy=linopy,
        gurobi_lp=gurobi_lp,
        result_artifacts=result_artifacts,
        outputs=outputs,
        diagnostics=diagnostics,
        diagnostic_qa=diagnostic_qa,
        log=log,
        runtime_seconds=time.perf_counter() - started,
    )


def _saved_result_metrics(
    network: pypsa.Network,
    log: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not np.isfinite(float(network.objective)):
        raise RuntimeError("B9H_RECOVERY_SAVED_OBJECTIVE_INVALID")
    text = log.read_text(encoding="utf-8", errors="replace")
    if "Optimal objective" not in text:
        raise RuntimeError("B9H_RECOVERY_LOG_HAS_NO_OPTIMAL_OBJECTIVE")
    metrics = _solve_metrics(network, "ok", "optimal", 0.0)
    if not metrics["accepted"] or len(network.snapshots) != 8760:
        raise RuntimeError("B9H_RECOVERY_SAVED_RESULT_NOT_TECHNICALLY_ACCEPTED")
    linopy = {
        "status": "PASS_RECOVERED_FROM_ORIGINAL_RUN",
        "integer_variables": 0,
        "binary_variables": 0,
        "continuous_linear_program_only": True,
    }
    gurobi_lp = {
        "status": "PASS_RECOVERED_FROM_ORIGINAL_RUN",
        "NumVars": 0,
        "NumConstrs": 0,
        "NumBinVars": 0,
        "NumIntVars": 0,
        "evidence": "ORIGINAL_RUN_REACHED_OPTIMAL_OBJECTIVE",
    }
    return metrics, linopy, gurobi_lp


def finalize_b9h_from_saved(args: argparse.Namespace) -> dict[str, Any]:
    if not args.from_solved:
        raise SystemExit(
            "B9H_RECOVERY_NOT_EXECUTED: use finalize --from-solved"
        )
    config = load_b9h_config()
    paths = _result_paths(
        ROOT / config["phase"]["result_directory"],
        config["phase"]["result_stem"],
    )
    missing = [relative_path(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"B9H_RECOVERY_RESULT_MEMBER_MISSING: {missing}")
    prepared = prepare_b9h(write_artifacts=True)
    network = pypsa.Network(paths["solved"])
    log = ROOT / config["phase"]["qa_directory"] / "logs/MEM_ETX7B9H_raw.log"
    metrics, linopy, gurobi_lp = _saved_result_metrics(network, log)
    _validate_candidate_dispatch(network, prepared)
    diagnostics, diagnostic_qa = write_post_solve_diagnostics(
        network, prepared, paths
    )
    result_artifacts = list(paths.values())
    outputs = {path.stem: relative_path(path) for path in paths.values()}
    _append_log(
        log,
        "ETX-7B9H post-processing finalized from the saved optimal result; no optimization rerun.",
    )
    return _write_result_manifest_and_receipt(
        network=network,
        prepared=prepared,
        metrics=metrics,
        preflight={
            "status": "PASS_RECOVERED_FROM_ORIGINAL_OPTIMAL_RUN",
            "production_optimization_rerun": False,
            "private_license_credentials_recorded": False,
        },
        linopy=linopy,
        gurobi_lp=gurobi_lp,
        result_artifacts=result_artifacts,
        outputs=outputs,
        diagnostics=diagnostics,
        diagnostic_qa=diagnostic_qa,
        log=log,
        runtime_seconds=0.0,
    )


def _verify_manifest_members(path: Path) -> tuple[pd.DataFrame, list[str]]:
    frame = pd.read_csv(path)
    failures: list[str] = []
    for row in frame.itertuples(index=False):
        member = ROOT / str(row.relative_path)
        if not member.is_file() or sha256_file(member) != str(row.sha256):
            failures.append(str(row.relative_path))
    return frame, failures


def write_cross_artifact_qa() -> Path:
    config = load_b9h_config()
    verification = verify_b9g_parent(config)
    qa_dir = ROOT / config["phase"]["qa_directory"]
    receipt_path = ROOT / config["phase"]["receipt"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (
        receipt.get("status") != "PASS"
        or receipt.get("gate") != config["phase"]["gate"]
        or receipt.get("qa", {}).get("technical_diagnostic_status")
        != "TECHNICAL_B9H_DIAGNOSTIC_PASS"
    ):
        raise RuntimeError("B9H_CROSS_QA_RECEIPT_NOT_TECHNICAL_PASS")
    manifest_path = ROOT / receipt["outputs"]["manifest"]
    if sha256_file(manifest_path) != receipt["outputs"]["manifest_sha256"]:
        raise RuntimeError("B9H_CROSS_QA_MANIFEST_HASH_MISMATCH")
    manifest, failures = _verify_manifest_members(manifest_path)
    if failures:
        raise RuntimeError(f"B9H_CROSS_QA_MANIFEST_MEMBER_FAILURES: {failures}")
    prep = json.loads(
        (
            qa_dir / "MEM_B9H_2050_Preparation_Final_Verification_v1.0.json"
        ).read_text(encoding="utf-8")
    )
    sizing = pd.read_csv(
        qa_dir / "MEM_B9H_2050_B9G_Residual_Scarcity_Diagnostics_v1.0.csv"
    ).set_index("market")
    selection = pd.read_csv(
        qa_dir / "MEM_B9H_2050_Final_Capacity_Selection_v1.0.csv"
    ).set_index("market")
    contract = pd.read_csv(
        qa_dir / "MEM_B9H_2050_Virtual_Supply_Contract_v1.0.csv"
    ).set_index("generator_id")
    market = pd.read_csv(
        qa_dir
        / "MEM_B9H_2050_B9G_to_B9H_External_Market_Comparison_v1.0.csv"
    )
    system = pd.read_csv(
        qa_dir / "MEM_B9H_2050_B9G_to_B9H_System_Comparison_v1.0.csv"
    )
    detail = pd.read_csv(
        qa_dir / "MEM_B9H_2050_MT_TN_Detailed_Diagnostics_v1.0.csv"
    ).set_index("market")
    capacity_match = True
    for market_name, generator_id in NEW_GENERATOR_IDS.items():
        selected = float(selection.at[market_name, "selected_increment_MW"])
        capacity_match &= np.isclose(
            selected,
            float(sizing.at[market_name, "selected_increment_MW"]),
            atol=0.0,
            rtol=0.0,
        )
        capacity_match &= np.isclose(
            selected,
            float(contract.at[generator_id, "p_nom_MW"]),
            atol=0.0,
            rtol=0.0,
        )
        capacity_match &= np.isclose(
            selected,
            float(detail.at[market_name, "incremental_capacity_MW"]),
            atol=0.0,
            rtol=0.0,
        )
    sources = yaml.safe_load(
        (ROOT / "config/stage_a_production_price_sources.yaml").read_text(
            encoding="utf-8"
        )
    )
    gates = yaml.safe_load(
        (ROOT / "config/approval_gates.yaml").read_text(encoding="utf-8")
    )
    payload = {
        "schema_version": "MEM_ETX7B9H_CROSS_ARTIFACT_FINAL_QA_V1_0",
        "status": "PASS",
        "B9G_parent": {
            "receipt_sha256": verification["receipt_sha256"],
            "result_manifest_sha256": verification["manifest"]["sha256"],
            "result_manifest_member_count": verification["manifest"][
                "member_count"
            ],
            "result_manifest_member_failures": verification["manifest"][
                "member_failures"
            ],
            "solved_network_sha256": verification["solved_network_sha256"],
        },
        "B9H_receipt_sha256": sha256_file(receipt_path),
        "B9H_result_manifest_sha256": sha256_file(manifest_path),
        "B9H_result_manifest_member_count": len(manifest),
        "B9H_result_manifest_member_failures": len(failures),
        "preparation_status": prep["status"],
        "capacity_values_match_across_sizing_selection_contract_and_postsolve": bool(
            capacity_match
        ),
        "market_comparison_exact_eight_markets": (
            len(market) == 8
            and set(market["market"].astype(str)) == set(EXPORTED_MARKETS)
        ),
        "system_comparison_exact_cases": (
            len(system) == 2
            and set(system["case"].astype(str)) == {"B9G", "B9H"}
        ),
        "MT_TN_detail_exact_scope": set(detail.index.astype(str))
        == set(TARGET_MARKETS),
        "ME_increment_absent": "ME" not in set(TARGET_MARKETS),
        "B9H_governance_status": sources["sources"][2050]["status"],
        "B10_2050_locked": (
            sources["governance"]["b10_authorized"] is False
            and gates["stage_a_manual_gates"]["b10_authorized"] is False
        ),
        "Stage_B_2050_locked": (
            sources["governance"]["stage_b_authorized"] is False
            and gates["stage_a_manual_gates"]["stage_b_authorized"] is False
        ),
        "B9I_authorized": False,
        "credential_material_included": False,
    }
    if not all(
        [
            payload[
                "capacity_values_match_across_sizing_selection_contract_and_postsolve"
            ],
            payload["market_comparison_exact_eight_markets"],
            payload["system_comparison_exact_cases"],
            payload["MT_TN_detail_exact_scope"],
            payload["ME_increment_absent"],
            payload["B10_2050_locked"],
            payload["Stage_B_2050_locked"],
        ]
    ):
        raise RuntimeError(f"B9H_CROSS_ARTIFACT_QA_FAILED: {payload}")
    path = qa_dir / "MEM_ETX7B9H_Cross_Artifact_Final_QA_v1.0.json"
    _write_json(path, payload)
    return path


def _implementation_inventory() -> pd.DataFrame:
    rows = [
        (
            "config/stage_a_perimeter_closure_final_mt_tn_2050.yaml",
            "NEW",
            "B9H frozen parent, sizing, formulation, execution and governance contract",
        ),
        (
            "src/mem_model/stage_a/perimeter_closure_final_mt_tn.py",
            "NEW",
            "Preparation, single solve, diagnostics, receipt and delivery packaging",
        ),
        (
            "tests/test_stage_a_perimeter_closure_final_mt_tn.py",
            "NEW",
            "Focused parent immutability, capacity, structure, LP and lock tests",
        ),
        (
            "docs/runbooks/ETX7B9H_RUNBOOK.md",
            "NEW",
            "Single-run and no-successor operator runbook",
        ),
        (
            "docs/MEM_ETX7B9H_IMPLEMENTATION_SCOPE.md",
            "NEW",
            "Human-readable implementation delta",
        ),
        (
            "docs/MEM_ETX7B9H_2050_FINAL_MT_TN_CLOSURE_HANDOFF.md",
            "NEW",
            "Completed result report",
        ),
        (
            "config/stage_a_production_price_sources.yaml",
            "MODIFIED",
            "B9H pending-review candidate; B9G immutable parent",
        ),
        (
            "config/approval_gates.yaml",
            "MODIFIED",
            "B9H completed; no B9I/B10/Stage-B authorization",
        ),
        (
            "docs/MEM_STAGE_A_CURRENT_STATE.md",
            "MODIFIED",
            "Current state advanced to B9H technical pass pending review",
        ),
    ]
    return pd.DataFrame(rows, columns=["relative_path", "change_type", "purpose"])


def package_b9h() -> dict[str, Any]:
    config = load_b9h_config()
    qa_dir = ROOT / config["phase"]["qa_directory"]
    receipt_path = ROOT / config["phase"]["receipt"]
    if not receipt_path.is_file():
        raise FileNotFoundError("B9H_PACKAGE_RECEIPT_MISSING")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("status") != "PASS":
        raise RuntimeError("B9H_PACKAGE_REQUIRES_PASS_RECEIPT")
    cross_qa = write_cross_artifact_qa()
    inventory_path = (
        qa_dir / "MEM_ETX7B9H_Implementation_Change_Inventory_v1.0.csv"
    )
    inventory = _implementation_inventory()
    inventory.to_csv(
        inventory_path, index=False, encoding="utf-8", lineterminator="\n"
    )
    result_manifest_path = ROOT / receipt["outputs"]["manifest"]
    result_manifest = pd.read_csv(result_manifest_path)
    payload_paths = [
        ROOT / path for path in result_manifest["relative_path"].astype(str)
    ]
    payload_paths.extend(
        [
            result_manifest_path,
            receipt_path,
            cross_qa,
            inventory_path,
            ROOT / "config/stage_a_production_price_sources.yaml",
            ROOT / "config/approval_gates.yaml",
            ROOT / "docs/MEM_STAGE_A_CURRENT_STATE.md",
            ROOT / "docs/MEM_ETX7B9H_2050_FINAL_MT_TN_CLOSURE_HANDOFF.md",
        ]
    )
    payload_paths = list(dict.fromkeys(payload_paths))
    missing = [relative_path(path) for path in payload_paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"B9H_PACKAGE_MEMBER_MISSING: {missing}")
    forbidden_path_tokens = (
        ".venv",
        "__pycache__",
        ".pytest_cache",
        "gurobi.lic",
    )
    for path in payload_paths:
        rel = relative_path(path).lower()
        if any(token in rel for token in forbidden_path_tokens):
            raise RuntimeError(f"B9H_PACKAGE_FORBIDDEN_PATH: {rel}")
        if path.suffix.lower() in {
            ".py",
            ".yaml",
            ".yml",
            ".json",
            ".md",
            ".csv",
            ".txt",
            ".log",
        }:
            text = path.read_text(encoding="utf-8", errors="ignore")
            access_label = "WLS" + "ACCESSID"
            secret_label = "WLS" + "SECRET"
            safe_log_label = re.compile(
                rf"^(?:INFO:gurobipy:)?Set parameter "
                rf"(?:{access_label}|{secret_label})\\s*$",
                flags=re.IGNORECASE,
            )
            for line in text.splitlines():
                upper = line.upper()
                if (
                    access_label in upper or secret_label in upper
                ) and not safe_log_label.fullmatch(line.strip()):
                    raise RuntimeError(
                        f"B9H_PACKAGE_CREDENTIAL_MATERIAL_FOUND: {rel}"
                    )
    delivery_manifest_path = (
        qa_dir / "MEM_ETX7B9H_Completed_Delivery_Package_Manifest_v1.0.csv"
    )
    delivery_manifest = write_manifest(delivery_manifest_path, payload_paths)
    zip_members = [*payload_paths, delivery_manifest_path]
    zip_path = ROOT / "MEM_ETX7B9H_Completed_Package_v1.0.zip"
    with zipfile.ZipFile(
        zip_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in zip_members:
            archive.write(path, arcname=relative_path(path))
    failures: list[str] = []
    with zipfile.ZipFile(zip_path, mode="r") as archive:
        names = archive.namelist()
        if len(names) != len(zip_members) or len(set(names)) != len(names):
            failures.append("ZIP_MEMBER_COUNT_OR_DUPLICATE_FAILURE")
        expected_hashes = {
            relative_path(path): sha256_file(path) for path in zip_members
        }
        for name, expected in expected_hashes.items():
            observed = hashlib.sha256(archive.read(name)).hexdigest().upper()
            if observed != expected:
                failures.append(name)
    if failures:
        raise RuntimeError(f"B9H_ZIP_VERIFICATION_FAILURES: {failures}")
    return {
        "status": "PASS",
        "zip": relative_path(zip_path),
        "zip_sha256": sha256_file(zip_path),
        "zip_members": len(zip_members),
        "delivery_manifest": relative_path(delivery_manifest_path),
        "delivery_manifest_sha256": sha256_file(delivery_manifest_path),
        "delivery_manifest_rows": len(delivery_manifest),
        "cross_artifact_QA": relative_path(cross_qa),
        "credential_material_included": False,
    }


def _failure_receipt(error: Exception) -> Path:
    config = load_b9h_config()
    receipt = build_receipt(
        phase=config["phase"]["id"],
        gate=config["phase"]["gate"],
        status="FAIL",
        input_manifests=[],
        outputs={"production_result_accepted": False},
        qa={
            "error_type": type(error).__name__,
            "error": str(error),
            "automatic_successor_experiment": False,
            "B9I_authorized": False,
            "B10_2050_authorized": False,
            "Stage_B_2050_authorized": False,
        },
        next_gate="STOP_FOR_SOL_USER_REVIEW",
        command=command_string(module=CANONICAL_MODULE),
        runtime_seconds=0.0,
    )
    path = ROOT / config["phase"]["receipt"]
    write_receipt(path, receipt)
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare, run once, or package ETX-7B9H"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("prepare", help="Prepare B9H without a production solve")
    run = subparsers.add_parser("b9h", help="Execute the single guarded B9H solve")
    run.add_argument(
        "--execute",
        action="store_true",
        help="Required explicit authorization flag for the single production solve",
    )
    finalize = subparsers.add_parser(
        "finalize",
        help="Recover post-processing from an already saved optimal result",
    )
    finalize.add_argument("--from-solved", action="store_true")
    subparsers.add_parser(
        "package", help="Run final cross-artifact QA and build the delivery ZIP"
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "prepare":
        prepared = prepare_b9h(write_artifacts=True)
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "phase": prepared["config"]["phase"]["id"],
                    "selected_increments_MW": prepared["increments"],
                    "capacity_control": prepared["capacity_control"],
                    "production_optimization_executed": False,
                    "manual_command": prepared["config"]["phase"]["command"],
                },
                indent=2,
                allow_nan=False,
            )
        )
        return
    if args.command == "package":
        print(json.dumps(package_b9h(), indent=2, allow_nan=False))
        return
    try:
        if args.command == "finalize":
            receipt = finalize_b9h_from_saved(args)
        else:
            receipt = run_b9h(args)
    except SystemExit:
        raise
    except Exception as error:
        receipt_path = _failure_receipt(error)
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "receipt": relative_path(receipt_path),
                },
                indent=2,
                allow_nan=False,
            )
        )
        raise
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "phase": receipt["phase"],
                "gate": receipt["gate"],
                "objective": receipt["objective"],
                "receipt": load_b9h_config()["phase"]["receipt"],
                "B9H": "EXECUTED_TECHNICAL_PASS_PENDING_METHOD_REVIEW",
                "B9I": "NOT_AUTHORIZED",
                "B10_2050": "LOCKED",
                "Stage_B_2050": "LOCKED",
            },
            indent=2,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()

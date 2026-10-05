"""Prepare and optionally run the bounded ETX-7B9G 2050 diagnostic.

The preparation starts from the immutable B6 unsolved physical network,
recreates the exact five ETX-7B9F virtual generators, and adds only three
ME/MT/TN residual R10 increments derived from ETX-7B9F local load shedding.
The ``prepare`` command never calls a solver.  The guarded ``b9g --execute``
entry point is reserved for the later manual production diagnostic.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

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
    _baseline_result_paths,
    _directional_limit_for_year,
    _flow_wide,
    _require_hash,
    _result_paths,
    _system_metrics_from_outputs,
    _write_json,
    load_r10_config,
    recover_cost_proxy,
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


CONFIG_PATH = ROOT / "config/stage_a_perimeter_closure_residual_me_mt_tn_2050.yaml"
CANONICAL_MODULE = "mem_model.stage_a.perimeter_closure_residual_me_mt_tn"
YEAR = 2050
RESIDUAL_ENERGY_SHARE = 0.10
R5_DIAGNOSTIC_SHARE = 0.05
EXPORTED_MARKETS = ("FR", "CH", "AT", "SI", "ME", "GR", "MT", "TN")
RESIDUAL_MARKETS = ("ME", "MT", "TN")
PROTECTED_B9F_MARKETS = ("FR", "CH", "AT", "SI", "GR")
COMPARISON_CASES = ("B9B", "R10", "R5", "B9E", "B9F", "B9G")
AT_CAPACITY_TOLERANCE_MW = 1.0

B9F_GENERATOR_IDS = {
    "FR": "EXTERNAL_VIRTUAL_SUPPLY_FR_R10_2050",
    "GR": "EXTERNAL_VIRTUAL_SUPPLY_GR_R10_2050",
    "TN": "EXTERNAL_VIRTUAL_SUPPLY_TN_R10_2050",
    "AT": "EXTERNAL_VIRTUAL_SUPPLY_AT_PLACEMENT_R10_2050",
    "SI": "EXTERNAL_VIRTUAL_SUPPLY_SI_PLACEMENT_R10_2050",
}
B9F_CAPACITIES_MW = {
    "FR": 40616.26187526855,
    "GR": 6905.030474826701,
    "TN": 2433.2947267101813,
    "AT": 5506.614447347679,
    "SI": 3017.094676349047,
}
RESIDUAL_GENERATOR_IDS = {
    "ME": "EXTERNAL_VIRTUAL_SUPPLY_ME_RESIDUAL_R10_2050",
    "MT": "EXTERNAL_VIRTUAL_SUPPLY_MT_RESIDUAL_R10_2050",
    "TN": "EXTERNAL_VIRTUAL_SUPPLY_TN_RESIDUAL_R10_2050",
}


def load_b9g_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if config.get("schema") != "PERIMETER_CLOSURE_2050_RESIDUAL_ME_MT_TN_DIAGNOSTIC_V1_0":
        raise RuntimeError("B9G_CONFIG_SCHEMA_MISMATCH")
    if config.get("status") != "PREPARED_NOT_EXECUTED":
        raise RuntimeError("B9G_CONFIG_NOT_PREPARED")
    design = config["capacity_design"]
    if tuple(design["residual_increment_markets"]) != RESIDUAL_MARKETS:
        raise RuntimeError("B9G_RESIDUAL_SCOPE_MISMATCH")
    if float(design["residual_energy_share"]) != RESIDUAL_ENERGY_SHARE:
        raise RuntimeError("B9G_R10_SHARE_MISMATCH")
    if float(design["diagnostic_residual_energy_share_R5"]) != R5_DIAGNOSTIC_SHARE:
        raise RuntimeError("B9G_R5_SHARE_MISMATCH")
    diagnostics = config["diagnostics"]
    if tuple(diagnostics["comparison_cases"]) != COMPARISON_CASES:
        raise RuntimeError("B9G_COMPARISON_SCOPE_MISMATCH")
    if tuple(diagnostics["exported_markets"]) != EXPORTED_MARKETS:
        raise RuntimeError("B9G_EXPORTED_MARKET_SCOPE_MISMATCH")
    if tuple(diagnostics["protected_B9F_markets"]) != PROTECTED_B9F_MARKETS:
        raise RuntimeError("B9G_PROTECTED_MARKET_SCOPE_MISMATCH")
    return config


def _b9f_result_paths(config: dict[str, Any]) -> dict[str, Path]:
    spec = config["accepted_inputs"]["b9f_2050_hybrid_diagnostic"]
    return _result_paths(ROOT / spec["result_directory"], spec["result_stem"])


def verify_b9g_inputs(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Verify only immutable inputs required for preparation and comparisons."""

    config = config or load_b9g_config()
    method = config["method_authority"]
    _require_hash(ROOT / method["path"], method["sha256"], "B9G_METHOD_AUTHORITY")

    phases: dict[str, Any] = {}
    for key in (
        "b9b_2050_bounded_baseline",
        "b9c_2050_R10_diagnostic",
        "b9d_2050_R5_diagnostic",
        "b9e_2050_placement_diagnostic",
        "b9f_2050_hybrid_diagnostic",
    ):
        phases[key] = _verify_phase(config["accepted_inputs"][key], key.upper())

    b9f_spec = config["accepted_inputs"]["b9f_2050_hybrid_diagnostic"]
    b9f_receipt = phases["b9f_2050_hybrid_diagnostic"]["receipt"]
    if b9f_receipt.get("qa", {}).get("technical_diagnostic_status") != (
        "TECHNICAL_FR_R5_HYBRID_DIAGNOSTIC_PASS"
    ):
        raise RuntimeError("B9G_B9F_TECHNICAL_STATUS_MISMATCH")
    b9f_paths = _b9f_result_paths(config)
    missing = [relative_path(path) for path in b9f_paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"B9G_B9F_RESULT_MEMBER_MISSING: {missing}")
    contract_path = ROOT / b9f_spec["virtual_supply_contract"]
    contract_sha = _require_hash(
        contract_path,
        b9f_spec["virtual_supply_contract_sha256"],
        "B9G_B9F_VIRTUAL_CONTRACT",
    )

    network_spec = config["accepted_inputs"]["b6_2050_unsolved_network"]
    network_path = ROOT / network_spec["path"]
    network_sha = _require_hash(
        network_path, network_spec["sha256"], "B9G_B6_2050_UNSOLVED_NETWORK"
    )

    mask_spec = config["accepted_inputs"]["immutable_ordinary_hour_mask"]
    mask_sha = _require_hash(ROOT / mask_spec["path"], mask_spec["sha256"], "B9G_MASK")
    mask_meta_sha = _require_hash(
        ROOT / mask_spec["metadata_path"],
        mask_spec["metadata_sha256"],
        "B9G_MASK_METADATA",
    )
    cost_spec = config["cost_proxy"]
    cost_sha = _require_hash(
        ROOT / cost_spec["source_path"], cost_spec["source_sha256"], "B9G_COST_SOURCE"
    )
    return {
        "status": "PASS",
        "phases": phases,
        "b9f_receipt": b9f_receipt,
        "b9f_paths": b9f_paths,
        "b9f_contract_path": contract_path,
        "b9f_contract_sha256": contract_sha,
        "b6_network_path": network_path,
        "b6_network_sha256": network_sha,
        "ordinary_mask_sha256": mask_sha,
        "ordinary_mask_metadata_sha256": mask_meta_sha,
        "cost_source_sha256": cost_sha,
    }


def _load_b9f_tables(
    baseline: pypsa.Network,
    verification: dict[str, Any],
    contract: pd.DataFrame,
) -> dict[str, Any]:
    paths = verification["b9f_paths"]
    load, shedding, prices, weights = _normalize_market_tables(
        baseline, paths["shedding"], paths["prices"]
    )
    if not np.allclose(weights.to_numpy(dtype=float), 1.0, atol=0.0, rtol=0.0):
        raise RuntimeError("B9G_REQUIRES_EXACT_ONE_HOUR_SNAPSHOT_WEIGHTS")
    dispatch = _dispatch_wide(paths["dispatch"], baseline.snapshots, contract["generator_id"])
    flows = _flow_wide(baseline, paths["flows"])
    return {
        "paths": paths,
        "load": load,
        "shedding": shedding,
        "prices": prices,
        "weights": weights,
        "dispatch": dispatch,
        "flows": flows,
    }


def _benchmark(series: pd.Series, capacity_MW: float) -> dict[str, Any]:
    residual = (series.clip(lower=0.0) - float(capacity_MW)).clip(lower=0.0)
    total = float(series.clip(lower=0.0).sum())
    return {
        "capacity_MW": float(capacity_MW),
        "residual_MWh": float(residual.sum()),
        "residual_energy_share": float(residual.sum() / total) if total else 0.0,
        "hours_remaining_above_1e_6_MW": int(residual.gt(SHEDDING_TOLERANCE_MW).sum()),
        "hours_remaining_above_1_MW": int(residual.gt(1.0).sum()),
    }


def derive_residual_sizing_diagnostics(
    config: dict[str, Any],
    b9f: dict[str, Any],
    b9f_contract: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, float], pd.DataFrame, dict[str, Any]]:
    """Derive R10 increments and all required evidence from immutable B9F."""

    contract_by_bus = b9f_contract.set_index("bus")
    rows: list[dict[str, Any]] = []
    increments: dict[str, float] = {}
    tn_rows: list[dict[str, Any]] = []
    tn_summary: dict[str, Any] = {}

    for market in RESIDUAL_MARKETS:
        shedding = b9f["shedding"][market].astype(float).clip(lower=0.0)
        positive = shedding.loc[shedding.gt(SHEDDING_TOLERANCE_MW)]
        conditional_gt_1 = shedding.loc[shedding.gt(1.0)]
        r10 = residual_energy_capacity(shedding.to_numpy(dtype=float), RESIDUAL_ENERGY_SHARE)
        r5 = residual_energy_capacity(shedding.to_numpy(dtype=float), R5_DIAGNOSTIC_SHARE)
        p95_all = float(shedding.quantile(0.95))
        p99_all = float(shedding.quantile(0.99))
        p95_conditional = (
            float(conditional_gt_1.quantile(0.95)) if len(conditional_gt_1) else 0.0
        )
        p99_conditional = (
            float(conditional_gt_1.quantile(0.99)) if len(conditional_gt_1) else 0.0
        )
        increments[market] = float(r10["capacity_MW"])

        proxy_present = market in contract_by_bus.index
        proxy_id = str(contract_by_bus.at[market, "generator_id"]) if proxy_present else ""
        proxy_p_nom = float(contract_by_bus.at[market, "p_nom_MW"]) if proxy_present else 0.0
        if proxy_present:
            proxy_dispatch = b9f["dispatch"][proxy_id].astype(float)
            proxy_headroom = (proxy_p_nom - proxy_dispatch).clip(lower=0.0)
            annual_dispatch = float((proxy_dispatch * b9f["weights"]).sum())
            capacity_factor = annual_dispatch / (proxy_p_nom * float(b9f["weights"].sum()))
            peak_dispatch = float(proxy_dispatch.max())
            at_near = proxy_headroom.le(AT_CAPACITY_TOLERANCE_MW)
            local_scarcity = shedding.gt(SHEDDING_TOLERANCE_MW)
            at_near_hours = int((local_scarcity & at_near).sum())
            headroom_hours = int(
                (local_scarcity & proxy_headroom.gt(AT_CAPACITY_TOLERANCE_MW)).sum()
            )
        else:
            proxy_dispatch = pd.Series(0.0, index=shedding.index)
            proxy_headroom = pd.Series(0.0, index=shedding.index)
            annual_dispatch = 0.0
            capacity_factor = 0.0
            peak_dispatch = 0.0
            at_near_hours = 0
            headroom_hours = 0

        benchmarks = {
            "R10": _benchmark(shedding, float(r10["capacity_MW"])),
            "R5": _benchmark(shedding, float(r5["capacity_MW"])),
            "all_hour_P95": _benchmark(shedding, p95_all),
            "all_hour_P99": _benchmark(shedding, p99_all),
            "conditional_gt_1_MW_P95": _benchmark(shedding, p95_conditional),
            "conditional_gt_1_MW_P99": _benchmark(shedding, p99_conditional),
        }
        row: dict[str, Any] = {
            "market": market,
            "B9F_annual_shedding_MWh": float(shedding.sum()),
            "B9F_shedding_hours_above_1e_6_MW": int(
                shedding.gt(SHEDDING_TOLERANCE_MW).sum()
            ),
            "B9F_shedding_hours_above_1_MW": int(shedding.gt(1.0).sum()),
            "B9F_peak_shedding_MW": float(shedding.max()),
            "B9F_mean_shedding_MW_conditional_on_shedding": (
                float(positive.mean()) if len(positive) else 0.0
            ),
            "B9F_median_shedding_MW_conditional_on_shedding": (
                float(positive.median()) if len(positive) else 0.0
            ),
            "existing_local_proxy_present": bool(proxy_present),
            "existing_local_proxy_generator_id": proxy_id,
            "existing_local_proxy_MW": proxy_p_nom,
            "existing_local_proxy_annual_dispatch_MWh": annual_dispatch,
            "existing_local_proxy_capacity_factor": capacity_factor,
            "existing_local_proxy_peak_dispatch_MW": peak_dispatch,
            "existing_proxy_hours_at_or_near_p_nom_during_local_scarcity": at_near_hours,
            "existing_proxy_hours_with_more_than_1_MW_headroom_during_local_scarcity": headroom_hours,
            "residual_R10_increment_MW": float(r10["capacity_MW"]),
            "residual_R5_increment_MW_diagnostic_only": float(r5["capacity_MW"]),
            "all_hour_residual_P95_MW_diagnostic_only": p95_all,
            "all_hour_residual_P99_MW_diagnostic_only": p99_all,
            "conditional_above_1_MW_residual_P95_MW_diagnostic_only": p95_conditional,
            "conditional_above_1_MW_residual_P99_MW_diagnostic_only": p99_conditional,
        }
        for label, metrics in benchmarks.items():
            row[f"{label}_residual_energy_share"] = metrics["residual_energy_share"]
            row[f"{label}_residual_MWh"] = metrics["residual_MWh"]
            row[f"{label}_hours_remaining_above_1e_6_MW"] = metrics[
                "hours_remaining_above_1e_6_MW"
            ]
            row[f"{label}_hours_remaining_above_1_MW"] = metrics[
                "hours_remaining_above_1_MW"
            ]
        rows.append(row)

        if market == "TN":
            local_scarcity = shedding.gt(SHEDDING_TOLERANCE_MW)
            at_near = proxy_headroom.le(AT_CAPACITY_TOLERANCE_MW)
            for snapshot in shedding.index[local_scarcity]:
                tn_rows.append(
                    {
                        "snapshot": snapshot,
                        "TN_shedding_MW": float(shedding.at[snapshot]),
                        "TN_price_EUR_per_MWh": float(b9f["prices"].at[snapshot, "TN"]),
                        "existing_TN_proxy_dispatch_MW": float(proxy_dispatch.at[snapshot]),
                        "existing_TN_proxy_headroom_MW": float(proxy_headroom.at[snapshot]),
                        "existing_TN_proxy_at_or_near_p_nom": bool(at_near.at[snapshot]),
                    }
                )
            scarcity_energy = float(shedding.loc[local_scarcity].sum())
            at_energy = float(shedding.loc[local_scarcity & at_near].sum())
            headroom_hours = int(
                (local_scarcity & proxy_headroom.gt(AT_CAPACITY_TOLERANCE_MW)).sum()
            )
            tn_summary = {
                "existing_proxy_generator_id": proxy_id,
                "existing_proxy_p_nom_MW": proxy_p_nom,
                "TN_shedding_hours": int(local_scarcity.sum()),
                "TN_shedding_MWh": scarcity_energy,
                "existing_proxy_dispatch_during_TN_scarcity_MWh": float(
                    (proxy_dispatch.loc[local_scarcity] * b9f["weights"].loc[local_scarcity]).sum()
                ),
                "existing_proxy_peak_dispatch_during_TN_scarcity_MW": float(
                    proxy_dispatch.loc[local_scarcity].max()
                ),
                "existing_proxy_maximum_headroom_during_TN_scarcity_MW": float(
                    proxy_headroom.loc[local_scarcity].max()
                ),
                "existing_proxy_mean_headroom_during_TN_scarcity_MW": float(
                    proxy_headroom.loc[local_scarcity].mean()
                ),
                "fraction_TN_shedding_hours_existing_proxy_at_or_near_p_nom": float(
                    (local_scarcity & at_near).sum() / local_scarcity.sum()
                ),
                "fraction_TN_shedding_MWh_existing_proxy_at_or_near_p_nom": (
                    at_energy / scarcity_energy if scarcity_energy else 0.0
                ),
                "TN_shedding_hours_with_more_than_1_MW_existing_proxy_headroom": headroom_hours,
                "capacity_binding_result": (
                    "JUSTIFIED" if headroom_hours == 0 else "NOT JUSTIFIED"
                ),
            }

    expected = config["capacity_design"]["expected_QA_residual_increments_MW"]
    for market, capacity in increments.items():
        if not np.isclose(capacity, float(expected[market]), atol=5e-9, rtol=0.0):
            raise RuntimeError(f"B9G_RESIDUAL_CAPACITY_QA_MISMATCH_{market}")
    if tn_summary.get("capacity_binding_result") != "JUSTIFIED":
        raise RuntimeError(
            config["TN_capacity_binding_precondition"]["stop_code"]
        )
    return (
        pd.DataFrame.from_records(rows),
        increments,
        pd.DataFrame.from_records(tn_rows),
        tn_summary,
    )


def derive_me_mt_interface_diagnostics(
    config: dict[str, Any],
    b9f: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    interfaces = pd.read_csv(INTERFACE_PATH).sort_values("physical_link_id", kind="stable")
    directional = pd.read_csv(LINK_PATH)
    rows: list[dict[str, Any]] = []
    summaries: dict[str, Any] = {}
    voll = float(config["diagnostics"]["VOLL_EUR_per_MWh"])
    for market in ("ME", "MT"):
        shedding = b9f["shedding"][market].astype(float)
        scarcity = shedding.gt(SHEDDING_TOLERANCE_MW)
        local_interfaces = interfaces.loc[
            interfaces["endpoint_a"].eq(market) | interfaces["endpoint_b"].eq(market)
        ]
        incoming_binding_any = pd.Series(False, index=shedding.index)
        outgoing_binding_any = pd.Series(False, index=shedding.index)
        for interface in local_interfaces.itertuples(index=False):
            link_id = interface.physical_link_id
            flow = b9f["flows"][link_id].astype(float)
            a_to_b = _directional_limit_for_year(
                directional, link_id, interface.endpoint_a, interface.endpoint_b, YEAR
            )
            b_to_a = _directional_limit_for_year(
                directional, link_id, interface.endpoint_b, interface.endpoint_a, YEAR
            )
            if interface.endpoint_b == market:
                incoming_flow = flow
                incoming_limit = a_to_b
                outgoing_flow = -flow
                outgoing_limit = b_to_a
                incoming_direction = f"{interface.endpoint_a}->{interface.endpoint_b}"
            else:
                incoming_flow = -flow
                incoming_limit = b_to_a
                outgoing_flow = flow
                outgoing_limit = a_to_b
                incoming_direction = f"{interface.endpoint_b}->{interface.endpoint_a}"
            incoming_headroom = (incoming_limit - incoming_flow).clip(lower=0.0)
            outgoing_headroom = (outgoing_limit - outgoing_flow).clip(lower=0.0)
            incoming_binding = incoming_headroom.le(INTERFACE_LIMIT_TOLERANCE_MW)
            outgoing_binding = outgoing_headroom.le(INTERFACE_LIMIT_TOLERANCE_MW)
            incoming_binding_any |= incoming_binding
            outgoing_binding_any |= outgoing_binding
            for snapshot in shedding.index[scarcity]:
                rows.append(
                    {
                        "market": market,
                        "snapshot": snapshot,
                        "B9F_local_shedding_MW": float(shedding.at[snapshot]),
                        "B9F_local_price_EUR_per_MWh": float(
                            b9f["prices"].at[snapshot, market]
                        ),
                        "B9F_local_price_at_VOLL": bool(
                            np.isclose(
                                b9f["prices"].at[snapshot, market],
                                voll,
                                atol=VOLL_TOLERANCE_EUR_PER_MWH,
                                rtol=0.0,
                            )
                        ),
                        "physical_link_id": link_id,
                        "endpoint_a": interface.endpoint_a,
                        "endpoint_b": interface.endpoint_b,
                        "signed_flow_endpoint_a_to_b_MW": float(flow.at[snapshot]),
                        "incoming_direction_to_local_market": incoming_direction,
                        "incoming_flow_to_local_market_MW": float(incoming_flow.at[snapshot]),
                        "accepted_incoming_limit_MW": float(incoming_limit),
                        "incoming_headroom_MW": float(incoming_headroom.at[snapshot]),
                        "incoming_interface_saturated": bool(incoming_binding.at[snapshot]),
                        "outgoing_flow_from_local_market_MW": float(outgoing_flow.at[snapshot]),
                        "accepted_outgoing_limit_MW": float(outgoing_limit),
                        "outgoing_headroom_MW": float(outgoing_headroom.at[snapshot]),
                        "outgoing_interface_saturated": bool(outgoing_binding.at[snapshot]),
                    }
                )
        price = b9f["prices"][market].astype(float)
        at_voll = pd.Series(
            np.isclose(
                price.to_numpy(), voll, atol=VOLL_TOLERANCE_EUR_PER_MWH, rtol=0.0
            ),
            index=price.index,
        )
        summaries[market] = {
            "B9F_local_shedding_MWh": float(shedding.sum()),
            "B9F_local_scarcity_hours": int(scarcity.sum()),
            "local_interface_count": int(len(local_interfaces)),
            "local_scarcity_hours_with_any_incoming_interface_saturated": int(
                (scarcity & incoming_binding_any).sum()
            ),
            "fraction_local_scarcity_hours_with_any_incoming_interface_saturated": float(
                (scarcity & incoming_binding_any).sum() / scarcity.sum()
            ),
            "local_scarcity_hours_with_any_outgoing_interface_saturated": int(
                (scarcity & outgoing_binding_any).sum()
            ),
            "VOLL_hours": int(at_voll.sum()),
            "VOLL_hours_with_local_shedding": int((at_voll & scarcity).sum()),
            "VOLL_hours_without_local_shedding": int((at_voll & ~scarcity).sum()),
        }
    return pd.DataFrame.from_records(rows), summaries


def derive_b9g_contract(
    config: dict[str, Any],
    verification: dict[str, Any],
    b9f_contract: pd.DataFrame,
    increments: dict[str, float],
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    if len(b9f_contract) != 5 or set(b9f_contract["generator_id"]) != set(
        B9F_GENERATOR_IDS.values()
    ):
        raise RuntimeError("B9G_B9F_CONTRACT_SCOPE_FAILURE")
    indexed = b9f_contract.set_index("bus")
    for market, generator_id in B9F_GENERATOR_IDS.items():
        if str(indexed.at[market, "generator_id"]) != generator_id or not np.isclose(
            float(indexed.at[market, "p_nom_MW"]),
            B9F_CAPACITIES_MW[market],
            atol=0.0,
            rtol=0.0,
        ):
            raise RuntimeError(f"B9G_B9F_PROXY_IDENTITY_FAILURE_{market}")

    method = config["method_authority"]
    r10_config = load_r10_config(ROOT / method["path"])
    cost = recover_cost_proxy(YEAR, r10_config)
    cost_spec = config["cost_proxy"]
    if cost["source_asset_id"] != cost_spec["source_asset_id"] or not np.isclose(
        float(cost["marginal_cost_EUR2025_per_MWh_el"]),
        float(cost_spec["marginal_cost_EUR2025_per_MWh_el"]),
        atol=1e-12,
        rtol=0.0,
    ):
        raise RuntimeError("B9G_COST_PROXY_MISMATCH")

    contract = b9f_contract.copy()
    contract["b9g_role"] = "IMMUTABLE_B9F_DIAGNOSTIC_PREDECESSOR"
    new_rows: list[dict[str, Any]] = []
    base_columns = list(b9f_contract.columns)
    for market in RESIDUAL_MARKETS:
        row = {column: np.nan for column in base_columns}
        row.update(
            {
                "generator_id": RESIDUAL_GENERATOR_IDS[market],
                "bus": market,
                "carrier": VIRTUAL_CARRIER,
                "p_nom_MW": float(increments[market]),
                "capacity_source": "DERIVED_FROM_IMMUTABLE_ETX7B9F_LOCAL_RESIDUAL_SHEDDING",
                "capacity_sizing_rule": config["capacity_design"]["sizing_rule"],
                "mechanical_source_residual_energy_share": RESIDUAL_ENERGY_SHARE,
                "p_nom_extendable": False,
                "committable": False,
                "p_min_pu": 0.0,
                "p_max_pu": 1.0,
                "efficiency": 1.0,
                "marginal_cost_EUR2025_per_MWh_el": float(
                    cost["marginal_cost_EUR2025_per_MWh_el"]
                ),
                "cost_source_asset_id": cost["source_asset_id"],
                "interpretation": "RESIDUAL_FIRM_EXTERNAL_SUPPLY_FROM_OMITTED_SYSTEMS_OR_ADEQUACY_RESOURCES",
                "status": "B9G_RESIDUAL_R10_FIXED_VIRTUAL_EXTERNAL_SUPPLY",
                "network_parameter_delta_vs_B9E": "NEW_RESIDUAL_GENERATOR_ONLY",
                "b9g_role": "RESIDUAL_R10_INCREMENT",
            }
        )
        new_rows.append(row)
    contract = pd.concat(
        [contract, pd.DataFrame.from_records(new_rows)], ignore_index=True, sort=False
    )
    if len(contract) != 8 or set(contract["generator_id"]) != (
        set(B9F_GENERATOR_IDS.values()) | set(RESIDUAL_GENERATOR_IDS.values())
    ):
        raise RuntimeError("B9G_CONTRACT_EXACT_EIGHT_GENERATOR_FAILURE")
    if contract["marginal_cost_EUR2025_per_MWh_el"].nunique() != 1:
        raise RuntimeError("B9G_COMMON_MARGINAL_COST_FAILURE")
    controls = {
        "B9F_total_virtual_capacity_MW": float(sum(B9F_CAPACITIES_MW.values())),
        "B9G_residual_increment_total_MW": float(sum(increments.values())),
        "B9G_total_virtual_capacity_MW": float(contract["p_nom_MW"].sum()),
        "B9F_generator_count": 5,
        "B9G_residual_generator_count": 3,
        "B9G_total_virtual_generator_count": 8,
    }
    return contract, cost, controls


def add_b9g_virtual_supply(
    baseline: pypsa.Network,
    contract: pd.DataFrame,
) -> pypsa.Network:
    network = baseline.copy()
    if VIRTUAL_CARRIER in network.carriers.index:
        raise RuntimeError("B9G_VIRTUAL_CARRIER_ALREADY_PRESENT_IN_B6")
    if set(network.generators.index) & set(contract["generator_id"]):
        raise RuntimeError("B9G_VIRTUAL_GENERATOR_ALREADY_PRESENT_IN_B6")
    network.add(
        "Carrier",
        VIRTUAL_CARRIER,
        co2_emissions=0.0,
        nice_name="B9G fixed residual firm external virtual supply",
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
        "auxiliary_experiment": "FINAL_RESIDUAL_ME_MT_TN_R10_CLOSURE_2050",
        "predecessor": "ETX-7B9F_IMMUTABLE_DIAGNOSTIC",
        "method": "PERIMETER_CLOSURE_METHOD_V2_0",
        "residual_capacity_source": "IMMUTABLE_B9F_LOCAL_SHEDDING",
        "residual_energy_share": RESIDUAL_ENERGY_SHARE,
        "technical_status": "PREPARED_NOT_EXECUTED",
        "production_price_acceptance": "PENDING_EXECUTION_AND_METHOD_REVIEW",
    }
    return network


def validate_b9g_structural_delta(
    baseline: pypsa.Network,
    network: pypsa.Network,
    contract: pd.DataFrame,
) -> dict[str, Any]:
    all_new_ids = set(contract["generator_id"])
    if not baseline.snapshots.equals(network.snapshots):
        raise RuntimeError("B9G_SNAPSHOT_DRIFT")
    _assert_frame_equal(baseline.snapshot_weightings, network.snapshot_weightings, "B9G_WEIGHTS")
    for component in ("buses", "loads", "links", "stores"):
        _assert_frame_equal(
            getattr(baseline, component),
            getattr(network, component),
            f"B9G_{component.upper()}",
        )
    if set(network.carriers.index) != set(baseline.carriers.index) | {VIRTUAL_CARRIER}:
        raise RuntimeError("B9G_CARRIER_DELTA_NOT_EXACT")
    _assert_frame_equal(
        baseline.carriers,
        network.carriers.loc[baseline.carriers.index, baseline.carriers.columns],
        "B9G_EXISTING_CARRIERS",
    )
    if set(network.generators.index) != set(baseline.generators.index) | all_new_ids:
        raise RuntimeError("B9G_GENERATOR_DELTA_NOT_EXACT")
    if set(network.generators.columns) != set(baseline.generators.columns):
        raise RuntimeError("B9G_GENERATOR_SCHEMA_DRIFT")
    _assert_frame_equal(
        baseline.generators,
        network.generators.loc[baseline.generators.index, baseline.generators.columns],
        "B9G_EXISTING_GENERATORS",
    )
    for holder_name in ("buses_t", "loads_t", "links_t", "stores_t"):
        before = _dynamic_tables(getattr(baseline, holder_name))
        after = _dynamic_tables(getattr(network, holder_name))
        if set(before) != set(after):
            raise RuntimeError(f"B9G_DYNAMIC_SCHEMA_DRIFT_{holder_name}")
        for attribute in before:
            _assert_frame_equal(before[attribute], after[attribute], f"B9G_{holder_name}_{attribute}")
    before_generators = _dynamic_tables(baseline.generators_t)
    after_generators = _dynamic_tables(network.generators_t)
    if set(before_generators) != set(after_generators):
        raise RuntimeError("B9G_GENERATOR_DYNAMIC_SCHEMA_DRIFT")
    for attribute, before in before_generators.items():
        after = after_generators[attribute]
        unexpected = set(after.columns) - set(before.columns) - all_new_ids
        if unexpected:
            raise RuntimeError(f"B9G_UNEXPECTED_DYNAMIC_GENERATOR_COLUMNS_{attribute}")
        _assert_frame_equal(
            before, after.loc[:, before.columns], f"B9G_EXISTING_GENERATOR_DYNAMIC_{attribute}"
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
            raise RuntimeError(f"B9G_VIRTUAL_PARAMETER_FAILURE_{row.generator_id}")

    b9f_rows = contract.loc[contract["generator_id"].isin(B9F_GENERATOR_IDS.values())]
    residual_rows = contract.loc[contract["generator_id"].isin(RESIDUAL_GENERATOR_IDS.values())]
    if len(b9f_rows) != 5 or len(residual_rows) != 3:
        raise RuntimeError("B9G_B9F_TO_B9G_DELTA_NOT_EXACTLY_THREE_GENERATORS")
    if set(residual_rows["bus"]) != set(RESIDUAL_MARKETS):
        raise RuntimeError("B9G_RESIDUAL_MARKET_SCOPE_FAILURE")
    residual_elsewhere = residual_rows.loc[~residual_rows["bus"].isin(RESIDUAL_MARKETS)]
    if not residual_elsewhere.empty:
        raise RuntimeError("B9G_RESIDUAL_SUPPORT_OUTSIDE_ME_MT_TN")

    baseline_counts = _component_counts(baseline)
    observed_counts = _component_counts(network)
    expected_counts = dict(baseline_counts)
    expected_counts["carriers"] += 1
    expected_counts["generators"] += 8
    if observed_counts != expected_counts:
        raise RuntimeError("B9G_COMPONENT_COUNT_DRIFT")
    market_buses = set(network.buses.index[network.buses.carrier.eq("AC_STAGE_A_MARKET")])
    if market_buses != set(MARKETS):
        raise RuntimeError("B9G_MARKET_BUS_SCOPE_FAILURE")
    return {
        "status": "PASS",
        "year": YEAR,
        "baseline_counts": baseline_counts,
        "B9G_counts": observed_counts,
        "B6_to_B9G_allowed_delta": {"carriers": 1, "generators": 8},
        "B9F_to_B9G_allowed_delta": {"generators": 3},
        "B9F_generators_recreated_exactly": list(b9f_rows["generator_id"]),
        "residual_generators_added": list(residual_rows["generator_id"]),
        "residual_markets": list(residual_rows["bus"]),
        "support_added_outside_ME_MT_TN": False,
        "physical_component_preservation": "EXACT",
        "topology_preservation": "EXACT",
        "demand_and_profile_preservation": "EXACT",
    }


def _preparation_paths(config: dict[str, Any]) -> dict[str, Path]:
    qa = ROOT / config["phase"]["qa_directory"]
    return {
        "sizing": qa / "MEM_B9G_2050_Residual_Sizing_Diagnostics_v1.0.csv",
        "contract": qa / "MEM_B9G_2050_Residual_Virtual_Supply_Contract_v1.0.csv",
        "tn": qa / "MEM_B9G_2050_TN_Capacity_Binding_Diagnostics_v1.0.csv",
        "interfaces": qa / "MEM_B9G_2050_ME_MT_Interface_Scarcity_Diagnostics_v1.0.csv",
        "qa": qa / "MEM_B9G_2050_Preparation_QA_v1.0.json",
        "manifest": qa / "MEM_B9G_2050_Preparation_Manifest_v1.0.csv",
        "verification": qa / "MEM_B9G_2050_Preparation_Final_Verification_v1.0.json",
    }


def prepare_b9g(*, write_artifacts: bool = True) -> dict[str, Any]:
    config = load_b9g_config()
    verification = verify_b9g_inputs(config)
    baseline = pypsa.Network(verification["b6_network_path"])
    if len(baseline.snapshots) != 8760 or int(baseline.meta.get("horizon", YEAR)) != YEAR:
        raise RuntimeError("B9G_B6_2050_NETWORK_CHRONOLOGY_FAILURE")
    b9f_contract = pd.read_csv(verification["b9f_contract_path"])
    b9f = _load_b9f_tables(baseline, verification, b9f_contract)
    sizing, increments, tn_table, tn_summary = derive_residual_sizing_diagnostics(
        config, b9f, b9f_contract
    )
    interfaces, interface_summary = derive_me_mt_interface_diagnostics(config, b9f)
    contract, cost, capacity_control = derive_b9g_contract(
        config, verification, b9f_contract, increments
    )
    network = add_b9g_virtual_supply(baseline, contract)
    structural = validate_b9g_structural_delta(baseline, network, contract)
    static_lp = validate_lp_static(network)
    short = network.copy()
    short.set_snapshots(short.snapshots[:6])
    short_lp = create_and_validate_linopy_model(short)
    if short_lp["integer_variables"] != 0 or short_lp["binary_variables"] != 0:
        raise RuntimeError("B9G_SHORT_FIXTURE_NOT_CONTINUOUS_LP")

    receipt_hash = verification["phases"]["b9f_2050_hybrid_diagnostic"][
        "receipt_sha256"
    ]
    manifest = verification["phases"]["b9f_2050_hybrid_diagnostic"]["manifest"]
    payload = {
        "schema_version": "MEM_B9G_2050_PREPARATION_QA_V1_0",
        "status": "PASS",
        "phase": config["phase"]["id"],
        "classification": config["capacity_design"]["classification"],
        "B9F_immutability": {
            "receipt_sha256": receipt_hash,
            "result_manifest_sha256": manifest["sha256"],
            "result_manifest_member_count": manifest["member_count"],
            "result_manifest_member_failures": manifest["member_failures"],
            "status": "IMMUTABLE_DIAGNOSTIC_PREDECESSOR",
        },
        "TN_capacity_binding_precondition": tn_summary,
        "ME_MT_interface_scarcity_summary": interface_summary,
        "residual_R10_increments_MW": increments,
        "capacity_control": capacity_control,
        "marginal_cost_EUR2025_per_MWh_el": float(
            contract["marginal_cost_EUR2025_per_MWh_el"].iloc[0]
        ),
        "structural_QA": structural,
        "static_LP": static_lp,
        "short_unsolved_Linopy_fixture": {"snapshots": 6, **short_lp},
        "production_optimization_executed": False,
        "Gurobi_invoked": False,
        "B9G": "PREPARED_NOT_EXECUTED",
        "B10_2050": "LOCKED",
        "Stage_B_2050": "LOCKED",
        "production_price_acceptance": "PENDING_EXECUTION_AND_METHOD_REVIEW",
    }
    paths = _preparation_paths(config)
    artifacts: list[Path] = []
    if write_artifacts:
        paths["sizing"].parent.mkdir(parents=True, exist_ok=True)
        sizing.to_csv(paths["sizing"], index=False, encoding="utf-8", lineterminator="\n")
        contract.to_csv(paths["contract"], index=False, encoding="utf-8", lineterminator="\n")
        tn_table.to_csv(paths["tn"], index=False, encoding="utf-8", lineterminator="\n")
        interfaces.to_csv(
            paths["interfaces"], index=False, encoding="utf-8", lineterminator="\n"
        )
        _write_json(paths["qa"], payload)
        artifacts = [
            paths["sizing"],
            paths["contract"],
            paths["tn"],
            paths["interfaces"],
            paths["qa"],
        ]
        members = [
            CONFIG_PATH,
            ROOT / "config/stage_a_production_price_sources.yaml",
            ROOT / "config/approval_gates.yaml",
            ROOT / "src/mem_model/stage_a/perimeter_closure_residual_me_mt_tn.py",
            ROOT / "tests/test_stage_a_perimeter_closure_residual_me_mt_tn.py",
            ROOT / "docs/runbooks/ETX7B9G_RUNBOOK.md",
            ROOT / "docs/MEM_ETX7B9G_2050_RESIDUAL_CLOSURE_PREPARATION_HANDOFF.md",
            ROOT / "docs/MEM_STAGE_A_CURRENT_STATE.md",
            *artifacts,
        ]
        missing_members = [relative_path(path) for path in members if not path.exists()]
        if missing_members:
            raise FileNotFoundError(f"B9G_PREPARATION_MANIFEST_MEMBER_MISSING: {missing_members}")
        manifest_frame = write_manifest(paths["manifest"], members)
        _write_json(
            paths["verification"],
            {
                "schema_version": "MEM_B9G_2050_PREPARATION_FINAL_VERIFICATION_V1_0",
                "status": "PASS",
                "preparation_manifest": relative_path(paths["manifest"]),
                "preparation_manifest_sha256": sha256_file(paths["manifest"]),
                "preparation_manifest_members": len(manifest_frame),
                "B9F_receipt_sha256": receipt_hash,
                "B9F_result_manifest_sha256": manifest["sha256"],
                "B9F_result_manifest_members_verified": manifest["member_count"],
                "B9F_result_manifest_member_failures": manifest["member_failures"],
                "B6_2050_unsolved_network_sha256": verification["b6_network_sha256"],
                "TN_capacity_binding_result": tn_summary["capacity_binding_result"],
                "residual_R10_increments_MW": increments,
                "capacity_control": capacity_control,
                "structural_QA_status": structural["status"],
                "zero_integer_variables": short_lp["integer_variables"] == 0,
                "zero_binary_variables": short_lp["binary_variables"] == 0,
                "production_optimization_executed": False,
                "Gurobi_invoked": False,
                "B9G": "PREPARED_NOT_EXECUTED",
                "B10_2050": "LOCKED",
                "Stage_B_2050": "LOCKED",
            },
        )
        artifacts.extend([paths["manifest"], paths["verification"]])
    return {
        "config": config,
        "verification": verification,
        "baseline": baseline,
        "network": network,
        "b9f": b9f,
        "b9f_contract": b9f_contract,
        "sizing": sizing,
        "increments": increments,
        "tn_table": tn_table,
        "tn_summary": tn_summary,
        "interfaces": interfaces,
        "interface_summary": interface_summary,
        "contract": contract,
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
    b9g_network: pypsa.Network,
    prepared: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    verification = prepared["verification"]
    config = prepared["config"]
    phase_keys = {
        "B9B": "b9b_2050_bounded_baseline",
        "R10": "b9c_2050_R10_diagnostic",
        "R5": "b9d_2050_R5_diagnostic",
        "B9E": "b9e_2050_placement_diagnostic",
        "B9F": "b9f_2050_hybrid_diagnostic",
    }
    voll = float(config["diagnostics"]["VOLL_EUR_per_MWh"])
    cases: dict[str, dict[str, Any]] = {}
    for name, key in phase_keys.items():
        receipt = verification["phases"][key]["receipt"]
        paths = _baseline_result_paths(receipt)
        solved = pypsa.Network(paths["solved"])
        cases[name] = _load_solved_case(solved, paths, float(receipt["objective"]), voll)
    b9g_paths = _result_paths(
        ROOT / config["phase"]["result_directory"], config["phase"]["result_stem"]
    )
    cases["B9G"] = _load_solved_case(
        b9g_network, b9g_paths, float(b9g_network.objective), voll
    )
    reference = cases["B9B"]
    for name, case in cases.items():
        if not reference["network"].snapshots.equals(case["network"].snapshots):
            raise RuntimeError(f"B9G_SIX_WAY_SNAPSHOT_DRIFT_{name}")
        if not np.allclose(reference["load"], case["load"], atol=0.0, rtol=0.0):
            raise RuntimeError(f"B9G_SIX_WAY_LOAD_DRIFT_{name}")
    return cases


def _load_ordinary_mask(
    config: dict[str, Any], snapshots: pd.Index
) -> tuple[pd.Series, dict[str, Any]]:
    spec = config["accepted_inputs"]["immutable_ordinary_hour_mask"]
    frame = pd.read_csv(ROOT / spec["path"])
    frame["snapshot"] = pd.to_datetime(frame["snapshot"])
    frame = frame.set_index("snapshot").reindex(snapshots)
    if frame.isna().any().any():
        raise RuntimeError("B9G_ORDINARY_MASK_TIMESTAMP_MISMATCH")
    raw = frame["ordinary_hour"]
    if raw.dtype == bool:
        mask = raw.astype(bool)
    else:
        mask = raw.astype(str).str.casefold().map({"true": True, "false": False})
    if mask.isna().any():
        raise RuntimeError("B9G_ORDINARY_MASK_BOOLEAN_PARSE_FAILURE")
    metadata = json.loads((ROOT / spec["metadata_path"]).read_text(encoding="utf-8"))
    return mask.astype(bool), {
        "status": "PASS_REUSED_WITHOUT_REGENERATION",
        "source_path": spec["path"],
        "source_sha256": spec["sha256"],
        "ordinary_hours": int(mask.sum()),
        "excluded_hours": int((~mask).sum()),
        "mask_boolean_sha256": metadata["mask_boolean_sha256"],
        "applied_unchanged_to": list(COMPARISON_CASES),
        "B9G_outcome_used_to_define_mask": False,
    }


def _six_way_diagnostics(
    cases: dict[str, dict[str, Any]],
    mask: pd.Series,
    voll: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    market_rows: list[dict[str, Any]] = []
    system_rows: list[dict[str, Any]] = []
    for name in COMPARISON_CASES:
        case = cases[name]
        exported_prices = case["prices"].loc[:, EXPORTED_MARKETS]
        exported_shedding = case["shedding"].loc[:, EXPORTED_MARKETS]
        for market in EXPORTED_MARKETS:
            prices = exported_prices[market].astype(float)
            shedding = exported_shedding[market].astype(float)
            stats = _price_statistics(prices, voll)
            at_voll = pd.Series(
                np.isclose(
                    prices.to_numpy(), voll, atol=VOLL_TOLERANCE_EUR_PER_MWH, rtol=0.0
                ),
                index=prices.index,
            )
            market_rows.append(
                {
                    "case": name,
                    "market": market,
                    **stats,
                    "shedding_MWh": float((shedding * case["weights"]).sum()),
                    "shedding_hours_above_1_MW": int(shedding.gt(1.0).sum()),
                    "peak_shedding_MW": float(shedding.max()),
                    "mean_excluding_VOLL_EUR_per_MWh": float(prices.loc[~at_voll].mean()),
                    "mean_excluding_above_5000_EUR_per_MWh": float(
                        prices.loc[prices.le(5000.0)].mean()
                    ),
                    "mean_excluding_above_1000_EUR_per_MWh": float(
                        prices.loc[prices.le(1000.0)].mean()
                    ),
                    "ordinary_hour_mean_EUR_per_MWh": float(prices.loc[mask].mean()),
                    "ordinary_hour_count": int(mask.sum()),
                }
            )
        exported_values = exported_prices.to_numpy(dtype=float)
        system_scarcity = case["shedding"].loc[:, MARKETS].sum(axis=1).gt(1.0)
        system_rows.append(
            {
                "case": name,
                "ten_market_mean_EUR_per_MWh": float(
                    case["prices"].loc[:, MARKETS].stack().mean()
                ),
                "eight_export_market_mean_EUR_per_MWh": float(
                    exported_prices.stack().mean()
                ),
                "total_system_load_MWh": float(case["system"]["total_load_MWh"]),
                "total_system_shedding_MWh": float(case["system"]["total_shedding_MWh"]),
                "system_shedding_share_percent": float(
                    case["system"]["shedding_share_percent"]
                ),
                "system_scarcity_hours_above_1_MW": int(system_scarcity.sum()),
                "exported_VOLL_market_hours": int(
                    np.isclose(
                        exported_values,
                        voll,
                        atol=VOLL_TOLERANCE_EUR_PER_MWH,
                        rtol=0.0,
                    ).sum()
                ),
                "unique_exported_scarcity_hours": int(
                    exported_shedding.sum(axis=1).gt(1.0).sum()
                ),
                "objective_EUR2025": float(case["system"]["objective_EUR2025"]),
            }
        )
    return pd.DataFrame.from_records(market_rows), pd.DataFrame.from_records(system_rows)


def _residual_proxy_utilization(
    cases: dict[str, dict[str, Any]], prepared: dict[str, Any]
) -> pd.DataFrame:
    case = cases["B9G"]
    residual = prepared["contract"].loc[
        prepared["contract"]["generator_id"].isin(RESIDUAL_GENERATOR_IDS.values())
    ]
    dispatch = _dispatch_wide(
        case["paths"]["dispatch"], case["network"].snapshots, residual["generator_id"]
    )
    b9f_system_scarcity = prepared["b9f"]["shedding"].sum(axis=1).gt(
        SHEDDING_TOLERANCE_MW
    )
    rows: list[dict[str, Any]] = []
    for row in residual.itertuples(index=False):
        series = dispatch[row.generator_id].astype(float)
        p_nom = float(row.p_nom_MW)
        weights = case["weights"]
        annual = float((series * weights).sum())
        local_b9f_scarcity = prepared["b9f"]["shedding"][row.bus].gt(
            SHEDDING_TOLERANCE_MW
        )
        dispatch_during_local = float(
            (series.loc[local_b9f_scarcity] * weights.loc[local_b9f_scarcity]).sum()
        )
        dispatch_outside_system = float(
            (series.loc[~b9f_system_scarcity] * weights.loc[~b9f_system_scarcity]).sum()
        )
        total_local = float(
            prepared["contract"].loc[
                prepared["contract"]["bus"].eq(row.bus), "p_nom_MW"
            ].sum()
        )
        capacity_factor = annual / (p_nom * float(weights.sum()))
        outside_share = dispatch_outside_system / annual if annual else 0.0
        rows.append(
            {
                "market": row.bus,
                "generator_id": row.generator_id,
                "residual_p_nom_MW": p_nom,
                "total_local_virtual_p_nom_after_addition_MW": total_local,
                "annual_dispatch_MWh": annual,
                "capacity_factor": capacity_factor,
                "peak_dispatch_MW": float(series.max()),
                "dispatch_hours_above_1e_6_MW": int(
                    series.gt(SHEDDING_TOLERANCE_MW).sum()
                ),
                "hours_above_50_percent_p_nom": int(series.gt(0.50 * p_nom).sum()),
                "hours_above_90_percent_p_nom": int(series.gt(0.90 * p_nom).sum()),
                "at_capacity_hours_within_1_MW": int(
                    (p_nom - series).le(AT_CAPACITY_TOLERANCE_MW).sum()
                ),
                "dispatch_during_B9F_local_scarcity_MWh": dispatch_during_local,
                "share_annual_dispatch_during_B9F_local_scarcity": (
                    dispatch_during_local / annual if annual else 0.0
                ),
                "dispatch_outside_all_B9F_system_scarcity_hours_MWh": dispatch_outside_system,
                "share_dispatch_outside_all_B9F_system_scarcity_hours": outside_share,
                "structural_baseload_style_review_flag": bool(
                    capacity_factor > 0.50 or outside_share > 0.50
                ),
                "automatic_failure": False,
            }
        )
    return pd.DataFrame.from_records(rows)


def _b9f_success_preservation(
    market_diagnostics: pd.DataFrame,
) -> pd.DataFrame:
    indexed = market_diagnostics.set_index(["case", "market"])
    rows: list[dict[str, Any]] = []
    for market in PROTECTED_B9F_MARKETS:
        b9f = indexed.loc[("B9F", market)]
        b9g = indexed.loc[("B9G", market)]
        rows.append(
            {
                "market": market,
                "B9F_mean_EUR_per_MWh": float(b9f["price_mean_EUR_per_MWh"]),
                "B9G_mean_EUR_per_MWh": float(b9g["price_mean_EUR_per_MWh"]),
                "mean_change_EUR_per_MWh": float(
                    b9g["price_mean_EUR_per_MWh"] - b9f["price_mean_EUR_per_MWh"]
                ),
                "B9F_ordinary_hour_mean_EUR_per_MWh": float(
                    b9f["ordinary_hour_mean_EUR_per_MWh"]
                ),
                "B9G_ordinary_hour_mean_EUR_per_MWh": float(
                    b9g["ordinary_hour_mean_EUR_per_MWh"]
                ),
                "ordinary_hour_mean_change_EUR_per_MWh": float(
                    b9g["ordinary_hour_mean_EUR_per_MWh"]
                    - b9f["ordinary_hour_mean_EUR_per_MWh"]
                ),
                "new_shedding_MWh": max(
                    float(b9g["shedding_MWh"] - b9f["shedding_MWh"]), 0.0
                ),
                "new_VOLL_hours": max(
                    int(b9g["hours_at_VOLL"] - b9f["hours_at_VOLL"]), 0
                ),
                "deterioration_review_flag": bool(
                    b9g["shedding_MWh"] > b9f["shedding_MWh"] + 1e-6
                    or b9g["hours_at_VOLL"] > b9f["hours_at_VOLL"]
                    or b9g["price_mean_EUR_per_MWh"]
                    > b9f["price_mean_EUR_per_MWh"] + 1e-6
                    or b9g["ordinary_hour_mean_EUR_per_MWh"]
                    > b9f["ordinary_hour_mean_EUR_per_MWh"] + 1e-6
                ),
                "automatic_compensation": False,
            }
        )
    return pd.DataFrame.from_records(rows)


def _italy_post_solve_control(cases: dict[str, dict[str, Any]], voll: float) -> pd.DataFrame:
    """Report Italy as a control only; it is never a B9G sizing authority."""

    rows: list[dict[str, Any]] = []
    for name in ("B9F", "B9G"):
        case = cases[name]
        shedding = case["shedding"]["IT"].astype(float)
        prices = case["prices"]["IT"].astype(float)
        rows.append(
            {
                "case": name,
                "IT_shedding_MWh": float((shedding * case["weights"]).sum()),
                "IT_shedding_hours_above_1e_6_MW": int(
                    shedding.gt(SHEDDING_TOLERANCE_MW).sum()
                ),
                "IT_shedding_hours_above_1_MW": int(shedding.gt(1.0).sum()),
                "IT_peak_shedding_MW": float(shedding.max()),
                "IT_price_mean_EUR_per_MWh": float(prices.mean()),
                "IT_VOLL_hours": int(
                    np.isclose(
                        prices.to_numpy(),
                        voll,
                        atol=VOLL_TOLERANCE_EUR_PER_MWH,
                        rtol=0.0,
                    ).sum()
                ),
                "IT_used_as_ME_MT_TN_sizing_authority": False,
                "IT_virtual_proxy_added": False,
                "Italy_facing_NTC_changed": False,
                "interpretation": "POST_SOLVE_CONTROL_ONLY_REDUCED_TOPOLOGY_INTERFACE_DIAGNOSTIC",
            }
        )
    return pd.DataFrame.from_records(rows)


def write_post_solve_diagnostics(
    network: pypsa.Network, prepared: dict[str, Any]
) -> tuple[list[Path], dict[str, Any]]:
    config = prepared["config"]
    cases = _comparison_cases(network, prepared)
    mask, mask_metadata = _load_ordinary_mask(config, network.snapshots)
    voll = float(config["diagnostics"]["VOLL_EUR_per_MWh"])
    market, system = _six_way_diagnostics(cases, mask, voll)
    utilization = _residual_proxy_utilization(cases, prepared)
    protection = _b9f_success_preservation(market)
    italy = _italy_post_solve_control(cases, voll)
    b9g_market = market.loc[market["case"].eq("B9G")].copy()
    b9f_market = market.loc[market["case"].eq("B9F")].set_index("market")
    b9g_market_indexed = b9g_market.set_index("market")
    newly_shedding_markets = [
        market_name
        for market_name in EXPORTED_MARKETS
        if float(b9f_market.at[market_name, "shedding_MWh"]) <= 1e-6
        and float(b9g_market_indexed.at[market_name, "shedding_MWh"]) > 1e-6
    ]
    acceptance_limit = float(
        config["diagnostics"]["practical_annual_mean_reasonableness_EUR_per_MWh"]
    )
    acceptance = {
        "schema_version": "MEM_B9G_2050_PRACTICAL_ACCEPTANCE_V1_0",
        "status": "PENDING_METHOD_REVIEW",
        "criterion_is_sizing_authority": False,
        "annual_mean_reasonableness_EUR_per_MWh": acceptance_limit,
        "markets": {
            row.market: {
                "annual_mean_EUR_per_MWh": float(row.price_mean_EUR_per_MWh),
                "broadly_at_or_below_300": bool(
                    row.price_mean_EUR_per_MWh <= acceptance_limit
                ),
            }
            for row in b9g_market.itertuples(index=False)
        },
        "all_eight_at_or_below_300": bool(
            b9g_market["price_mean_EUR_per_MWh"].le(acceptance_limit).all()
        ),
        "newly_shedding_exported_markets_vs_B9F": newly_shedding_markets,
        "no_new_shedding_exported_market": not newly_shedding_markets,
        "automatic_follow_on_sensitivity": False,
        "production_price_acceptance": "PENDING_METHOD_REVIEW",
    }
    qa = ROOT / config["phase"]["qa_directory"]
    outputs = {
        "market": qa / "MEM_B9G_2050_Six_Way_Exported_Market_Comparison_v1.0.csv",
        "system": qa / "MEM_B9G_2050_Six_Way_System_Comparison_v1.0.csv",
        "mask": qa / "MEM_B9G_2050_Ordinary_Hour_Mask_Reuse_v1.0.json",
        "utilization": qa / "MEM_B9G_2050_Residual_Proxy_Utilization_v1.0.csv",
        "protection": qa / "MEM_B9G_2050_B9F_Success_Preservation_v1.0.csv",
        "italy": qa / "MEM_B9G_2050_IT_Post_Solve_Control_v1.0.csv",
        "acceptance": qa / "MEM_B9G_2050_Practical_Acceptance_v1.0.json",
    }
    qa.mkdir(parents=True, exist_ok=True)
    market.to_csv(outputs["market"], index=False, encoding="utf-8", lineterminator="\n")
    system.to_csv(outputs["system"], index=False, encoding="utf-8", lineterminator="\n")
    utilization.to_csv(
        outputs["utilization"], index=False, encoding="utf-8", lineterminator="\n"
    )
    protection.to_csv(
        outputs["protection"], index=False, encoding="utf-8", lineterminator="\n"
    )
    italy.to_csv(outputs["italy"], index=False, encoding="utf-8", lineterminator="\n")
    _write_json(outputs["mask"], mask_metadata)
    _write_json(outputs["acceptance"], acceptance)
    return list(outputs.values()), {
        "comparison_cases": list(COMPARISON_CASES),
        "exported_market_rows": len(market),
        "system_rows": len(system),
        "ordinary_hour_mask": mask_metadata,
        "residual_proxy_utilization_rows": len(utilization),
        "B9F_protected_market_rows": len(protection),
        "Italy_post_solve_control_rows": len(italy),
        "practical_acceptance": acceptance,
        "automatic_economic_acceptance": False,
        "B10_2050": "LOCKED",
        "Stage_B_2050": "LOCKED",
    }


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def run_b9g(args: argparse.Namespace) -> dict[str, Any]:
    if not args.execute:
        raise SystemExit(
            "B9G_NOT_EXECUTED: rerun the exact prepared manual command with --execute"
        )
    prepared = prepare_b9g(write_artifacts=True)
    config = prepared["config"]
    execution_config = load_execution_config()
    if (
        config["solver"]["name"] != execution_config["solver"]["name"]
        or config["solver"]["options"] != execution_config["solver"]["options"]
    ):
        raise RuntimeError("B9G_SOLVER_CONFIG_DRIFT")
    started = time.perf_counter()
    qa = ROOT / config["phase"]["qa_directory"]
    log = qa / "logs/MEM_ETX7B9G_raw.log"
    _append_log(log, "ETX-7B9G manual diagnostic started; acceptance remains pending.")
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
        raise RuntimeError(f"B9G_POST_SOLVE_TECHNICAL_QA_FAILED: {metrics}")
    for row in prepared["contract"].itertuples(index=False):
        dispatch = network.generators_t.p[row.generator_id].astype(float)
        if dispatch.min() < -1e-5 or dispatch.max() > float(row.p_nom_MW) + 1e-5:
            raise RuntimeError(f"B9G_DISPATCH_BOUND_FAILURE_{row.generator_id}")

    result_dir = ROOT / config["phase"]["result_directory"]
    result_artifacts, outputs = _write_solve_outputs(
        network, result_dir, config["phase"]["result_stem"]
    )
    diagnostics, diagnostic_qa = write_post_solve_diagnostics(network, prepared)
    _append_log(
        log,
        "ETX-7B9G technical diagnostic complete; production-source acceptance remains pending analytical review.",
    )
    result_artifacts.extend(prepared["artifacts"])
    result_artifacts.extend(diagnostics)
    result_artifacts.append(log)
    manifest_path = result_dir / f"{config['phase']['result_stem']}_Result_Manifest_v1.0.csv"
    write_manifest(manifest_path, result_artifacts)
    outputs.update(
        {
            "manifest": relative_path(manifest_path),
            "manifest_sha256": sha256_file(manifest_path),
            "raw_log": relative_path(log),
            "preparation_artifacts": [relative_path(path) for path in prepared["artifacts"]],
            "diagnostics": {path.stem: relative_path(path) for path in diagnostics},
        }
    )
    after = verify_b9g_inputs(config)
    receipt = build_receipt(
        phase=config["phase"]["id"],
        gate=config["phase"]["gate"],
        status="PASS",
        input_manifests=[
            {
                "input_id": "B9F_IMMUTABLE_DIAGNOSTIC_PREDECESSOR",
                "path": relative_path(
                    after["phases"]["b9f_2050_hybrid_diagnostic"]["receipt_path"]
                ),
                "observed_sha256": after["phases"]["b9f_2050_hybrid_diagnostic"][
                    "receipt_sha256"
                ],
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
            "TN_capacity_binding_precondition": prepared["tn_summary"],
            "LP_assertions": {**linopy, **gurobi_lp},
            "post_solve": metrics,
            "comparisons": diagnostic_qa,
            "technical_diagnostic_status": "TECHNICAL_B9G_DIAGNOSTIC_PASS",
            "economic_methodological_acceptance": "PENDING_METHOD_REVIEW",
            "production_price_source": False,
            "automatic_successor_experiment": False,
            "B10_2050_authorized": False,
            "Stage_B_2050_authorized": False,
        },
        next_gate="METHOD_REVIEW_REQUIRED_NO_AUTOMATIC_PRODUCTION_PROMOTION",
        command=command_string(module=CANONICAL_MODULE),
        runtime_seconds=time.perf_counter() - started,
        solve=metrics,
    )
    write_receipt(ROOT / config["phase"]["receipt"], receipt)
    return receipt


def _failure_receipt(error: Exception) -> Path:
    config = load_b9g_config()
    receipt = build_receipt(
        phase=config["phase"]["id"],
        gate=config["phase"]["gate"],
        status="FAIL",
        input_manifests=[],
        outputs={"production_result_accepted": False},
        qa={
            "error_type": type(error).__name__,
            "error": str(error),
            "production_price_acceptance": "NOT_REACHED",
            "B10_2050_authorized": False,
            "Stage_B_2050_authorized": False,
        },
        next_gate="STOP_AND_RETURN_RECEIPT",
        command=command_string(module=CANONICAL_MODULE),
    )
    return write_receipt(ROOT / config["phase"]["receipt"], receipt)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MEM Stage-A ETX-7B9G final residual ME/MT/TN diagnostic"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("prepare", help="Prepare B9G without a production solve")
    run = subparsers.add_parser("b9g", help="Run the guarded manual B9G diagnostic")
    run.add_argument(
        "--execute",
        action="store_true",
        help="Required explicit acknowledgement for the later manual production solve",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "prepare":
        try:
            prepared = prepare_b9g(write_artifacts=True)
        except Exception as error:
            print(json.dumps({"status": "FAIL", "error": str(error)}, indent=2))
            raise SystemExit(1) from error
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "phase": prepared["config"]["phase"]["id"],
                    "TN_capacity_binding_result": prepared["tn_summary"][
                        "capacity_binding_result"
                    ],
                    "residual_R10_increments_MW": prepared["increments"],
                    "total_virtual_capacity_MW": prepared["capacity_control"][
                        "B9G_total_virtual_capacity_MW"
                    ],
                    "production_optimization_executed": False,
                    "Gurobi_invoked": False,
                    "manual_command": prepared["config"]["phase"]["command"],
                    "B9G": "PREPARED_NOT_EXECUTED",
                    "B10_2050": "LOCKED",
                    "Stage_B_2050": "LOCKED",
                },
                indent=2,
            )
        )
        return
    try:
        receipt = run_b9g(args)
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
                "receipt": load_b9g_config()["phase"]["receipt"],
                "production_price_acceptance": "PENDING_METHOD_REVIEW",
                "B10_2050": "LOCKED",
                "Stage_B_2050": "LOCKED",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

"""Prepare and run the isolated 2050 R5 perimeter-closure diagnostic.

R5 applies the accepted V2.0 residual-energy exceedance algorithm to the
immutable B9-B shedding series with a 0.05 residual-energy share. A technical
solve PASS never promotes the result to a production price source.
"""

from __future__ import annotations

import argparse
import hashlib
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
    _assert_gurobi_lp,
    create_and_validate_linopy_model,
    validate_lp_static,
)
from mem_model.stage_a.perimeter_closure_r10 import (
    CANDIDATE_MARKETS,
    VIRTUAL_CARRIER,
    _directional_limit_for_year,
    _flow_wide,
    _output_path,
    _require_hash,
    _result_paths,
    _verify_manifest,
    _write_json,
    derive_horizon_capacities,
    load_r10_config,
    recover_cost_proxy,
    validate_r10_structural_delta,
    verify_immutable_history,
)
from mem_model.stage_a.receipts import (
    build_receipt,
    command_string,
    relative_path,
    sha256_file,
    write_manifest,
    write_receipt,
)


CONFIG_PATH = ROOT / "config/stage_a_perimeter_closure_r5_2050.yaml"
CANONICAL_MODULE = "mem_model.stage_a.perimeter_closure_r5"
YEAR = 2050
RESIDUAL_ENERGY_SHARE = 0.05
EXPORTED_MARKETS = ("FR", "CH", "AT", "SI", "ME", "GR", "MT", "TN")
PLACEMENT_TARGETS = ("AT", "SI", "IT")
R5_GENERATOR_IDS = {
    market: f"EXTERNAL_VIRTUAL_SUPPLY_{market}_R5_2050"
    for market in CANDIDATE_MARKETS
}


def load_r5_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if config.get("schema") != "PERIMETER_CLOSURE_2050_R5_DIAGNOSTIC_V1_0":
        raise RuntimeError("R5_CONFIG_SCHEMA_MISMATCH")
    if config.get("status") != "PREPARED_NOT_EXECUTED":
        raise RuntimeError("R5_CONFIG_NOT_PREPARED")
    sizing = config["capacity_sizing"]
    if float(sizing["residual_energy_share"]) != RESIDUAL_ENERGY_SHARE:
        raise RuntimeError("R5_RESIDUAL_SHARE_MISMATCH")
    if tuple(sizing["candidate_markets"]) != CANDIDATE_MARKETS:
        raise RuntimeError("R5_CANDIDATE_SCOPE_MISMATCH")
    if tuple(config["diagnostics"]["exported_markets"]) != EXPORTED_MARKETS:
        raise RuntimeError("R5_EXPORTED_MARKET_SCOPE_MISMATCH")
    if tuple(config["diagnostics"]["placement_targets"]) != PLACEMENT_TARGETS:
        raise RuntimeError("R5_PLACEMENT_TARGET_SCOPE_MISMATCH")
    return config


def _verify_phase(spec: dict[str, Any], label: str) -> dict[str, Any]:
    receipt_path = ROOT / spec["receipt"]
    receipt_sha = _require_hash(receipt_path, spec["receipt_sha256"], f"R5_{label}_RECEIPT")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    required = {
        "gate": spec["required_gate"],
        "status": "PASS",
        "solver_status": "ok",
        "termination_condition": "optimal",
        "snapshots": 8760,
        "accepted": True,
    }
    if {key: receipt.get(key) for key in required} != required:
        raise RuntimeError(f"R5_{label}_RECEIPT_CONTENT_MISMATCH")
    manifest_path = ROOT / spec["result_manifest"]
    manifest = _verify_manifest(
        manifest_path,
        spec["result_manifest_sha256"],
        f"R5_{label}",
    )
    if receipt["outputs"].get("manifest") != spec["result_manifest"]:
        raise RuntimeError(f"R5_{label}_MANIFEST_PATH_MISMATCH")
    if receipt["outputs"].get("manifest_sha256") != manifest["sha256"]:
        raise RuntimeError(f"R5_{label}_MANIFEST_RECEIPT_HASH_MISMATCH")
    return {
        "receipt": receipt,
        "receipt_path": receipt_path,
        "receipt_sha256": receipt_sha,
        "manifest": manifest,
    }


def verify_r5_inputs(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_r5_config()
    method = config["method_authority"]
    _require_hash(ROOT / method["path"], method["sha256"], "R5_METHOD_AUTHORITY")
    r10_config = load_r10_config(ROOT / method["path"])
    base = verify_immutable_history(r10_config)

    accepted: dict[str, Any] = {}
    for key in (
        "b8d_2040_production_source",
        "b9c_2050_r10_diagnostic",
    ):
        accepted[key] = _verify_phase(
            config["accepted_inputs"][key],
            key.upper(),
        )

    b9b_spec = config["accepted_inputs"]["b9b_2050_bounded_baseline"]
    b9b = base["history"]["b9b_2050_bounded_baseline"]
    if (
        b9b["receipt_sha256"] != b9b_spec["receipt_sha256"]
        or b9b["manifest"]["sha256"] != b9b_spec["result_manifest_sha256"]
        or b9b["receipt"].get("gate") != b9b_spec["required_gate"]
    ):
        raise RuntimeError("R5_B9B_IDENTITY_MISMATCH")

    network_spec = config["accepted_inputs"]["b6_2050_unsolved_network"]
    network = ROOT / network_spec["path"]
    network_sha = _require_hash(network, network_spec["sha256"], "R5_B6_2050_NETWORK")
    if base["networks"][YEAR]["sha256"] != network_sha:
        raise RuntimeError("R5_B6_2050_NETWORK_BASE_VERIFICATION_MISMATCH")

    cost_spec = config["cost_proxy"]
    _require_hash(ROOT / cost_spec["source_path"], cost_spec["source_sha256"], "R5_B4_COST")
    return {
        "status": "PASS",
        "r10_config": r10_config,
        "base": base,
        "accepted": accepted,
        "b9b": b9b,
        "b6_network_path": network,
        "b6_network_sha256": network_sha,
    }


def derive_r5_contract(
    config: dict[str, Any],
    verification: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    capacities, shedding = derive_horizon_capacities(
        YEAR,
        verification["r10_config"],
        verification["base"],
        residual_energy_share=RESIDUAL_ENERGY_SHARE,
    )
    expected = config["capacity_sizing"]["expected_QA_capacities_MW"]
    for row in capacities.itertuples(index=False):
        if not np.isclose(
            float(row.derived_virtual_capacity_MW),
            float(expected[row.market]),
            atol=5e-9,
            rtol=0.0,
        ):
            raise RuntimeError(f"R5_CAPACITY_QA_MISMATCH_{row.market}")
        if not np.isclose(
            float(row.mechanical_residual_energy_share),
            RESIDUAL_ENERGY_SHARE,
            atol=1e-12,
            rtol=0.0,
        ):
            raise RuntimeError(f"R5_MECHANICAL_IDENTITY_FAILURE_{row.market}")
    total = float(capacities["derived_virtual_capacity_MW"].sum())
    if not np.isclose(
        total,
        float(config["capacity_sizing"]["expected_QA_total_MW"]),
        atol=1e-8,
        rtol=0.0,
    ):
        raise RuntimeError("R5_TOTAL_CAPACITY_QA_MISMATCH")

    cost = recover_cost_proxy(YEAR, verification["r10_config"])
    if (
        cost["source_asset_id"] != config["cost_proxy"]["source_asset_id"]
        or not np.isclose(
            float(cost["marginal_cost_EUR2025_per_MWh_el"]),
            float(config["cost_proxy"]["marginal_cost_EUR2025_per_MWh_el"]),
            atol=1e-12,
            rtol=0.0,
        )
    ):
        raise RuntimeError("R5_2050_COST_PROXY_MISMATCH")

    rows: list[dict[str, Any]] = []
    for row in capacities.itertuples(index=False):
        rows.append(
            {
                "generator_id": R5_GENERATOR_IDS[row.market],
                "bus": row.market,
                "carrier": VIRTUAL_CARRIER,
                "p_nom_MW": float(row.derived_virtual_capacity_MW),
                "baseline_peak_shedding_MW": float(row.baseline_peak_shedding_MW),
                "capacity_sizing_rule": config["capacity_sizing"]["rule"],
                "mechanical_baseline_residual_energy_share": float(
                    row.mechanical_residual_energy_share
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
                "status": "R5_DIAGNOSTIC_FIXED_VIRTUAL_EXTERNAL_SUPPLY",
            }
        )
    contract = pd.DataFrame.from_records(rows)
    if tuple(contract["bus"]) != CANDIDATE_MARKETS or len(contract) != 3:
        raise RuntimeError("R5_CONTRACT_SCOPE_FAILURE")
    return contract, cost, shedding


def add_r5_virtual_supply(
    baseline: pypsa.Network,
    contract: pd.DataFrame,
) -> pypsa.Network:
    network = baseline.copy()
    if VIRTUAL_CARRIER in network.carriers.index:
        raise RuntimeError("R5_VIRTUAL_CARRIER_ALREADY_PRESENT")
    if set(network.generators.index) & set(contract["generator_id"]):
        raise RuntimeError("R5_VIRTUAL_GENERATOR_ALREADY_PRESENT")
    network.add(
        "Carrier",
        VIRTUAL_CARRIER,
        co2_emissions=0.0,
        nice_name="R5 residual firm external virtual supply",
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
        "auxiliary_experiment": "R5_PERIMETER_CLOSURE_2050",
        "method": "PERIMETER_CLOSURE_METHOD_V2_0",
        "mechanical_baseline_residual_energy_share": RESIDUAL_ENERGY_SHARE,
        "technical_status": "PREPARED_NOT_EXECUTED",
        "production_price_acceptance": "PENDING_METHOD_REVIEW",
    }
    return network


def _validate_r5_structural_delta(
    baseline: pypsa.Network,
    network: pypsa.Network,
    contract: pd.DataFrame,
) -> dict[str, Any]:
    result = validate_r10_structural_delta(baseline, network, contract, YEAR)
    result["R5_counts"] = result.pop("R10_counts")
    result["method"] = "PERIMETER_CLOSURE_METHOD_V2_0_R5_DIAGNOSTIC"
    result["mechanical_baseline_residual_energy_share"] = RESIDUAL_ENERGY_SHARE
    return result


def _preparation_paths(config: dict[str, Any]) -> dict[str, Path]:
    qa = ROOT / config["phase"]["qa_directory"]
    return {
        "contract": qa / "MEM_R5_2050_Virtual_Supply_Contract_v1.0.csv",
        "cost": qa / "MEM_R5_2050_Cost_Provenance_v1.0.json",
        "qa": qa / "MEM_R5_2050_Preparation_QA_v1.0.json",
        "manifest": qa / "MEM_R5_2050_Preparation_Manifest_v1.0.csv",
        "verification": qa / "MEM_R5_2050_Preparation_Final_Verification_v1.0.json",
    }


def prepare_r5(*, write_artifacts: bool = True) -> dict[str, Any]:
    config = load_r5_config()
    verification = verify_r5_inputs(config)
    contract, cost, baseline_shedding = derive_r5_contract(config, verification)
    baseline = pypsa.Network(verification["b6_network_path"])
    if len(baseline.snapshots) != 8760 or int(baseline.meta.get("horizon", YEAR)) != YEAR:
        raise RuntimeError("R5_B6_2050_NETWORK_CHRONOLOGY_FAILURE")
    network = add_r5_virtual_supply(baseline, contract)
    structural = _validate_r5_structural_delta(baseline, network, contract)
    static_lp = validate_lp_static(network)
    short = network.copy()
    short.set_snapshots(short.snapshots[:6])
    short_lp = create_and_validate_linopy_model(short)
    payload = {
        "schema_version": "MEM_R5_2050_PREPARATION_QA_V1_0",
        "status": "PASS",
        "phase": config["phase"]["id"],
        "gate": config["phase"]["gate"],
        "method": "PERIMETER_CLOSURE_METHOD_V2_0",
        "mechanical_baseline_residual_energy_share": RESIDUAL_ENERGY_SHARE,
        "capacities_MW": contract.set_index("bus")["p_nom_MW"].to_dict(),
        "total_capacity_MW": float(contract["p_nom_MW"].sum()),
        "marginal_cost_EUR2025_per_MWh_el": float(
            contract["marginal_cost_EUR2025_per_MWh_el"].iloc[0]
        ),
        "structural_delta": structural,
        "static_LP": static_lp,
        "short_unsolved_LP_fixture": {"snapshots": 6, **short_lp},
        "immutable_B8D_2040_verified": True,
        "immutable_B9B_2050_verified": True,
        "immutable_B9C_R10_verified": True,
        "production_optimization_executed": False,
        "technical_diagnostic_status": "PREPARED_NOT_EXECUTED",
        "production_price_acceptance": "PENDING_METHOD_REVIEW",
        "B10_authorized": False,
        "Stage_B_authorized": False,
        "additional_sensitivities_created": False,
    }
    paths = _preparation_paths(config)
    artifacts: list[Path] = []
    if write_artifacts:
        paths["contract"].parent.mkdir(parents=True, exist_ok=True)
        contract.to_csv(paths["contract"], index=False, encoding="utf-8", lineterminator="\n")
        _write_json(
            paths["cost"],
            {"schema_version": "MEM_R5_2050_COST_PROVENANCE_V1_0", **cost},
        )
        _write_json(paths["qa"], payload)
        artifacts = [paths["contract"], paths["cost"], paths["qa"]]
        prep_members = [
            CONFIG_PATH,
            ROOT / "src/mem_model/stage_a/perimeter_closure_r5.py",
            ROOT / "docs/runbooks/ETX7B9D_RUNBOOK.md",
            *artifacts,
        ]
        manifest = write_manifest(paths["manifest"], prep_members)
        _write_json(
            paths["verification"],
            {
                "schema_version": "MEM_R5_2050_PREPARATION_FINAL_VERIFICATION_V1_0",
                "status": "PASS",
                "preparation_manifest": relative_path(paths["manifest"]),
                "preparation_manifest_sha256": sha256_file(paths["manifest"]),
                "preparation_manifest_members": len(manifest),
                "B8D_2040_receipt_sha256": verification["accepted"][
                    "b8d_2040_production_source"
                ]["receipt_sha256"],
                "B9B_2050_receipt_sha256": verification["b9b"]["receipt_sha256"],
                "B9C_R10_receipt_sha256": verification["accepted"][
                    "b9c_2050_r10_diagnostic"
                ]["receipt_sha256"],
                "B6_2050_network_sha256": verification["b6_network_sha256"],
                "capacities_MW": payload["capacities_MW"],
                "total_capacity_MW": payload["total_capacity_MW"],
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
        "cost": cost,
        "baseline_shedding": baseline_shedding,
        "structural": structural,
        "LP": short_lp,
        "payload": payload,
        "artifacts": artifacts,
    }


def _case_result_paths(receipt: dict[str, Any]) -> dict[str, Path]:
    return {
        "solved": _output_path(receipt, "_SOLVED"),
        "prices": _output_path(receipt, "_Market_Prices"),
        "dispatch": _output_path(receipt, "_Generator_Dispatch"),
        "flows": _output_path(receipt, "_Link_Flows"),
        "shedding": _output_path(receipt, "_Load_Shedding"),
    }


def _load_case_tables(
    network: pypsa.Network,
    paths: dict[str, Path],
) -> dict[str, Any]:
    load, shedding, prices, weights = _normalize_market_tables(
        network,
        paths["shedding"],
        paths["prices"],
    )
    return {
        "network": network,
        "paths": paths,
        "load": load,
        "shedding": shedding,
        "prices": prices,
        "weights": weights,
    }


def _exported_market_diagnostics(
    cases: dict[str, dict[str, Any]],
    voll: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    market_rows: list[dict[str, Any]] = []
    aggregate_rows: list[dict[str, Any]] = []
    unique_rows: list[dict[str, Any]] = []
    for case_name, case in cases.items():
        prices = case["prices"].loc[:, EXPORTED_MARKETS]
        shedding = case["shedding"].loc[:, EXPORTED_MARKETS]
        weights = case["weights"]
        for market in EXPORTED_MARKETS:
            stats = _price_statistics(prices[market], voll)
            market_rows.append(
                {
                    "case": case_name,
                    "market": market,
                    "price_mean_EUR_per_MWh": stats["price_mean_EUR_per_MWh"],
                    "price_median_EUR_per_MWh": stats["price_median_EUR_per_MWh"],
                    "price_P95_EUR_per_MWh": stats["price_P95_EUR_per_MWh"],
                    "price_P99_EUR_per_MWh": stats["price_P99_EUR_per_MWh"],
                    "VOLL_price_hours": stats["hours_at_VOLL"],
                    "hours_above_500_EUR_per_MWh": stats["hours_above_500_EUR_per_MWh"],
                    "hours_above_1000_EUR_per_MWh": stats["hours_above_1000_EUR_per_MWh"],
                    "hours_above_5000_EUR_per_MWh": stats["hours_above_5000_EUR_per_MWh"],
                    "shedding_MWh": float((shedding[market] * weights).sum()),
                    "shedding_hours_above_1e_6_MW": int(
                        shedding[market].gt(SHEDDING_TOLERANCE_MW).sum()
                    ),
                    "shedding_hours_above_1_MW": int(shedding[market].gt(1.0).sum()),
                }
            )
        price_values = prices.to_numpy(dtype=float)
        aggregate_rows.append(
            {
                "case": case_name,
                "exported_market_count": len(EXPORTED_MARKETS),
                "total_VOLL_market_hours": int(
                    np.isclose(
                        price_values,
                        voll,
                        atol=VOLL_TOLERANCE_EUR_PER_MWH,
                        rtol=0.0,
                    ).sum()
                ),
                "total_market_hours_above_500_EUR_per_MWh": int((price_values > 500.0).sum()),
                "total_market_hours_above_1000_EUR_per_MWh": int((price_values > 1000.0).sum()),
                "total_market_hours_above_5000_EUR_per_MWh": int((price_values > 5000.0).sum()),
            }
        )
        unique_rows.append(
            {
                "case": case_name,
                "unique_hours_any_exported_market_at_VOLL": int(
                    np.isclose(
                        price_values,
                        voll,
                        atol=VOLL_TOLERANCE_EUR_PER_MWH,
                        rtol=0.0,
                    ).any(axis=1).sum()
                ),
                "unique_hours_any_exported_market_above_500_EUR_per_MWh": int(
                    (price_values > 500.0).any(axis=1).sum()
                ),
                "unique_hours_any_exported_market_above_1000_EUR_per_MWh": int(
                    (price_values > 1000.0).any(axis=1).sum()
                ),
                "unique_hours_any_exported_market_above_5000_EUR_per_MWh": int(
                    (price_values > 5000.0).any(axis=1).sum()
                ),
            }
        )
    return (
        pd.DataFrame.from_records(market_rows),
        pd.DataFrame.from_records(aggregate_rows),
        pd.DataFrame.from_records(unique_rows),
    )


def _ordinary_hour_mask(
    baseline_shedding: pd.DataFrame,
    baseline_prices: pd.DataFrame,
) -> tuple[pd.Series, pd.DataFrame, dict[str, Any]]:
    system_shedding = baseline_shedding.loc[:, MARKETS].sum(axis=1)
    maximum_price = baseline_prices.loc[:, MARKETS].max(axis=1)
    mask = system_shedding.le(SHEDDING_TOLERANCE_MW) & maximum_price.le(500.0)
    table = pd.DataFrame(
        {
            "snapshot": mask.index,
            "ordinary_hour": mask.to_numpy(dtype=bool),
            "B9B_total_ten_market_shedding_MW": system_shedding.to_numpy(dtype=float),
            "B9B_max_ten_market_price_EUR_per_MWh": maximum_price.to_numpy(dtype=float),
        }
    )
    digest = hashlib.sha256(mask.to_numpy(dtype=np.uint8).tobytes()).hexdigest().upper()
    metadata = {
        "source": "IMMUTABLE_ETX7B9_B_2050_BOUNDED_BASELINE_ONLY",
        "definition": "B9B_TOTAL_TEN_MARKET_SHEDDING_LE_1E_6_MW_AND_B9B_ALL_TEN_MARKET_PRICES_LE_500_EUR_PER_MWH",
        "system_shedding_tolerance_MW": SHEDDING_TOLERANCE_MW,
        "extreme_price_threshold_EUR_per_MWh": 500.0,
        "total_hours": len(mask),
        "ordinary_hours": int(mask.sum()),
        "excluded_hours": int((~mask).sum()),
        "mask_boolean_sha256": digest,
        "reused_unchanged_for_B9B_R10_R5": True,
        "outcome_dependent_redefinition": False,
    }
    return mask, table, metadata


def _ordinary_hour_stability(
    cases: dict[str, dict[str, Any]],
    mask: pd.Series,
) -> pd.DataFrame:
    baseline = cases["B9B"]["prices"]
    rows: list[dict[str, Any]] = []
    for market in EXPORTED_MARKETS:
        baseline_values = baseline.loc[mask, market].astype(float)
        for case_name in ("B9B", "R10", "R5"):
            values = cases[case_name]["prices"].loc[mask, market].astype(float)
            difference = values - baseline_values
            rows.append(
                {
                    "market": market,
                    "case": case_name,
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
                    "mask_source": "IMMUTABLE_B9B_SHARED_UNCHANGED",
                }
            )
    return pd.DataFrame.from_records(rows)


def _dispatch_wide(
    path: Path,
    snapshots: pd.Index,
    generator_ids: Iterable[str],
) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    name = _component_name_column(frame)
    ids = list(generator_ids)
    wide = frame.loc[frame[name].isin(ids)].pivot(
        index="snapshot", columns=name, values="dispatch_MW"
    ).reindex(index=snapshots, columns=ids)
    if wide.isna().any().any():
        raise RuntimeError("R5_VIRTUAL_DISPATCH_TABLE_INCOMPLETE")
    return wide.astype(float)


def _virtual_supply_diagnostics(
    cases: dict[str, dict[str, Any]],
    r10_contract: pd.DataFrame,
    r5_contract: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    r10_ids = r10_contract.set_index("bus")["generator_id"].to_dict()
    r5_ids = r5_contract.set_index("bus")["generator_id"].to_dict()
    snapshots = cases["R5"]["network"].snapshots
    r10_dispatch = _dispatch_wide(cases["R10"]["paths"]["dispatch"], snapshots, r10_ids.values())
    r5_dispatch = _dispatch_wide(cases["R5"]["paths"]["dispatch"], snapshots, r5_ids.values())
    weights = cases["R5"]["weights"]
    rows: list[dict[str, Any]] = []
    for market in CANDIDATE_MARKETS:
        r10_nom = float(r10_contract.set_index("bus").at[market, "p_nom_MW"])
        r5_nom = float(r5_contract.set_index("bus").at[market, "p_nom_MW"])
        baseline_scarcity = cases["B9B"]["shedding"][market].gt(SHEDDING_TOLERANCE_MW)
        record: dict[str, Any] = {
            "market": market,
            "R10_p_nom_MW": r10_nom,
            "R5_p_nom_MW": r5_nom,
            "incremental_R5_minus_R10_MW": r5_nom - r10_nom,
        }
        for case_name, dispatch, generator_id, p_nom in (
            ("R10", r10_dispatch, r10_ids[market], r10_nom),
            ("R5", r5_dispatch, r5_ids[market], r5_nom),
        ):
            series = dispatch[generator_id]
            annual = float((series * weights).sum())
            record.update(
                {
                    f"{case_name}_annual_dispatch_MWh": annual,
                    f"{case_name}_capacity_factor": annual / (p_nom * float(weights.sum())),
                    f"{case_name}_peak_dispatch_MW": float(series.max()),
                    f"{case_name}_hours_above_10_percent_p_nom": int(
                        series.gt(0.10 * p_nom).sum()
                    ),
                    f"{case_name}_hours_above_50_percent_p_nom": int(
                        series.gt(0.50 * p_nom).sum()
                    ),
                    f"{case_name}_hours_above_90_percent_p_nom": int(
                        series.gt(0.90 * p_nom).sum()
                    ),
                    f"{case_name}_dispatch_during_original_B9B_local_scarcity_MWh": float(
                        (series.loc[baseline_scarcity] * weights.loc[baseline_scarcity]).sum()
                    ),
                    f"{case_name}_dispatch_outside_original_B9B_local_scarcity_MWh": float(
                        (series.loc[~baseline_scarcity] * weights.loc[~baseline_scarcity]).sum()
                    ),
                }
            )
        rows.append(record)
    return pd.DataFrame.from_records(rows), r5_dispatch


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
    r5_case: dict[str, Any],
    r5_contract: pd.DataFrame,
    r5_dispatch: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    network = r5_case["network"]
    flows = _flow_wide(network, r5_case["paths"]["flows"])
    interfaces = pd.read_csv(INTERFACE_PATH).sort_values("physical_link_id", kind="stable")
    directional = pd.read_csv(LINK_PATH)
    p_nom = r5_contract.set_index("bus")["p_nom_MW"].astype(float).to_dict()
    generator_ids = r5_contract.set_index("bus")["generator_id"].to_dict()
    headroom_rows: list[dict[str, Any]] = []
    interface_rows: list[dict[str, Any]] = []
    target_summaries: dict[str, Any] = {}
    weights = r5_case["weights"]

    for target in PLACEMENT_TARGETS:
        scarcity = r5_case["shedding"][target].gt(SHEDDING_TOLERANCE_MW)
        target_records: list[dict[str, Any]] = []
        binding_counts: dict[str, int] = {}
        source_counts = {
            source: {"headroom_hours": 0, "path_hours": 0, "headroom_and_path_hours": 0}
            for source in CANDIDATE_MARKETS
        }
        for snapshot in scarcity.index[scarcity]:
            adjacency: dict[str, set[str]] = {market: set() for market in MARKETS}
            link_records: list[dict[str, Any]] = []
            for interface in interfaces.itertuples(index=False):
                link_id = interface.physical_link_id
                flow = float(flows.at[snapshot, link_id])
                a_to_b = _directional_limit_for_year(
                    directional,
                    link_id,
                    interface.endpoint_a,
                    interface.endpoint_b,
                    YEAR,
                )
                b_to_a = _directional_limit_for_year(
                    directional,
                    link_id,
                    interface.endpoint_b,
                    interface.endpoint_a,
                    YEAR,
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
                        "evidence_scope": "ALL_12_RETAINED_INTERFACES_AT_THE_SAME_RESIDUAL_SCARCITY_HOUR",
                    }
                )
            record: dict[str, Any] = {
                "target_market": target,
                "snapshot": snapshot,
                "residual_R5_shedding_MW": float(r5_case["shedding"].at[snapshot, target]),
            }
            any_headroom = False
            any_path = False
            any_headroom_and_path = False
            total_headroom = 0.0
            for source in CANDIDATE_MARKETS:
                dispatch = float(r5_dispatch.at[snapshot, generator_ids[source]])
                headroom = max(float(p_nom[source]) - dispatch, 0.0)
                path_exists = _reachable(adjacency, source, target)
                has_headroom = headroom > 1.0
                source_counts[source]["headroom_hours"] += int(has_headroom)
                source_counts[source]["path_hours"] += int(path_exists)
                source_counts[source]["headroom_and_path_hours"] += int(
                    has_headroom and path_exists
                )
                record.update(
                    {
                        f"{source}_virtual_dispatch_MW": dispatch,
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
                    "any_proxy_headroom_above_1_MW": bool(any_headroom),
                    "any_proxy_directional_residual_path": bool(any_path),
                    "any_proxy_simultaneous_headroom_and_path": bool(any_headroom_and_path),
                    "all_proxy_capacity_near_saturated": bool(not any_headroom),
                    "path_test_interpretation": "STRUCTURAL_DIRECTIONAL_RESIDUAL_PATH_NOT_POWER_TRANSFER_PROOF",
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
                (r5_case["shedding"].loc[scarcity, target] * weights.loc[scarcity]).sum()
            ),
            "hours_all_proxy_capacity_near_saturated": int(
                records["all_proxy_capacity_near_saturated"].sum()
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
            "hours_headroom_without_any_directional_residual_path": int(
                (
                    records["any_proxy_headroom_above_1_MW"]
                    & ~records["any_proxy_directional_residual_path"]
                ).sum()
            ) if not records.empty else 0,
            "source_simultaneous_counts": source_counts,
            "binding_direction_counts_during_target_scarcity": binding_counts,
            "non_decision_evidence": {
                "A_insufficient_proxy_quantity_indicator": "hours_all_proxy_capacity_near_saturated",
                "B_placement_delivery_indicator": "hours_headroom_without_any_directional_residual_path",
                "C_broader_internal_adequacy_indicator": "hours_any_proxy_simultaneous_headroom_and_path_with_residual_scarcity",
                "D_acceptable_residual_scarcity": "METHOD_REVIEW_ONLY_NO_AUTOMATIC_THRESHOLD",
            },
        }

    headroom_columns = [
        "target_market", "snapshot", "residual_R5_shedding_MW",
        *[
            f"{source}_{suffix}"
            for source in CANDIDATE_MARKETS
            for suffix in (
                "virtual_dispatch_MW",
                "virtual_headroom_MW",
                "directional_residual_path_to_target",
                "simultaneous_headroom_and_path",
            )
        ],
        "total_virtual_headroom_MW", "any_proxy_headroom_above_1_MW",
        "any_proxy_directional_residual_path", "any_proxy_simultaneous_headroom_and_path",
        "all_proxy_capacity_near_saturated", "path_test_interpretation",
    ]
    interface_columns = [
        "target_market", "snapshot", "physical_link_id", "endpoint_a", "endpoint_b",
        "signed_flow_endpoint_a_to_b_MW", "accepted_a_to_b_limit_MW",
        "accepted_b_to_a_limit_MW", "incremental_a_to_b_headroom_MW",
        "incremental_b_to_a_headroom_MW", "a_to_b_binding", "b_to_a_binding",
        "evidence_scope",
    ]
    headroom = pd.DataFrame.from_records(headroom_rows, columns=headroom_columns)
    interface = pd.DataFrame.from_records(interface_rows, columns=interface_columns)
    summary = {
        "schema_version": "MEM_R5_2050_PLACEMENT_DELIVERY_SUMMARY_V1_0",
        "status": "FACTUAL_DIAGNOSTIC_NO_AUTOMATIC_A_TO_D_CLASSIFICATION",
        "targets": target_summaries,
        "simultaneous_evidence_rule": "HEADROOM_AND_INTERFACE_STATE_ARE_MEASURED_AT_THE_SAME_R5_RESIDUAL_SCARCITY_TIMESTAMP",
        "annual_unused_capacity_used_as_delivery_evidence": False,
        "automatic_classification": False,
        "production_price_acceptance": "PENDING_METHOD_REVIEW",
    }
    return headroom, interface, summary


def write_post_solve_diagnostics(
    network: pypsa.Network,
    prepared: dict[str, Any],
) -> tuple[list[Path], dict[str, Any]]:
    config = prepared["config"]
    verification = prepared["verification"]
    baseline_receipt = verification["b9b"]["receipt"]
    r10_receipt = verification["accepted"]["b9c_2050_r10_diagnostic"]["receipt"]
    baseline_paths = _case_result_paths(baseline_receipt)
    r10_paths = _case_result_paths(r10_receipt)
    r5_paths = _result_paths(
        ROOT / config["phase"]["result_directory"],
        config["phase"]["result_stem"],
    )
    cases = {
        "B9B": _load_case_tables(pypsa.Network(baseline_paths["solved"]), baseline_paths),
        "R10": _load_case_tables(pypsa.Network(r10_paths["solved"]), r10_paths),
        "R5": _load_case_tables(network, r5_paths),
    }
    reference = cases["B9B"]
    for case_name, case in cases.items():
        if not reference["network"].snapshots.equals(case["network"].snapshots):
            raise RuntimeError(f"R5_THREE_WAY_SNAPSHOT_DRIFT_{case_name}")
        if not np.allclose(reference["load"], case["load"], atol=0.0, rtol=0.0):
            raise RuntimeError(f"R5_THREE_WAY_LOAD_DRIFT_{case_name}")

    voll = float(config["diagnostics"]["VOLL_EUR_per_MWh"])
    market, aggregate, unique = _exported_market_diagnostics(cases, voll)
    mask, mask_table, mask_metadata = _ordinary_hour_mask(
        cases["B9B"]["shedding"],
        cases["B9B"]["prices"],
    )
    ordinary = _ordinary_hour_stability(cases, mask)
    r10_contract = pd.read_csv(
        ROOT
        / config["accepted_inputs"]["b9c_2050_r10_diagnostic"]["virtual_supply_contract"]
    )
    utilization, r5_dispatch = _virtual_supply_diagnostics(
        cases,
        r10_contract,
        prepared["contract"],
    )
    headroom, interface, placement = _placement_delivery_diagnostics(
        cases["R5"],
        prepared["contract"],
        r5_dispatch,
    )

    qa = ROOT / config["phase"]["qa_directory"]
    outputs = {
        "market": qa / "MEM_R5_2050_Exported_Market_Three_Way_Comparison_v1.0.csv",
        "aggregate": qa / "MEM_R5_2050_Exported_Market_Aggregates_v1.0.csv",
        "unique": qa / "MEM_R5_2050_Exported_Market_Unique_Hours_v1.0.csv",
        "mask_table": qa / "MEM_R5_2050_Ordinary_Hour_Mask_v1.0.csv",
        "mask_metadata": qa / "MEM_R5_2050_Ordinary_Hour_Mask_Metadata_v1.0.json",
        "ordinary": qa / "MEM_R5_2050_Ordinary_Hour_Stability_v1.0.csv",
        "utilization": qa / "MEM_R5_2050_Virtual_Supply_R10_vs_R5_v1.0.csv",
        "headroom": qa / "MEM_R5_2050_Residual_Scarcity_Headroom_v1.0.csv",
        "interfaces": qa / "MEM_R5_2050_Residual_Scarcity_Interface_Delivery_v1.0.csv",
        "placement": qa / "MEM_R5_2050_Placement_Delivery_Summary_v1.0.json",
    }
    qa.mkdir(parents=True, exist_ok=True)
    market.to_csv(outputs["market"], index=False, encoding="utf-8", lineterminator="\n")
    aggregate.to_csv(outputs["aggregate"], index=False, encoding="utf-8", lineterminator="\n")
    unique.to_csv(outputs["unique"], index=False, encoding="utf-8", lineterminator="\n")
    mask_table.to_csv(outputs["mask_table"], index=False, encoding="utf-8", lineterminator="\n")
    _write_json(outputs["mask_metadata"], mask_metadata)
    ordinary.to_csv(outputs["ordinary"], index=False, encoding="utf-8", lineterminator="\n")
    utilization.to_csv(outputs["utilization"], index=False, encoding="utf-8", lineterminator="\n")
    headroom.to_csv(outputs["headroom"], index=False, encoding="utf-8", lineterminator="\n")
    interface.to_csv(outputs["interfaces"], index=False, encoding="utf-8", lineterminator="\n")
    _write_json(outputs["placement"], placement)
    return list(outputs.values()), {
        "exported_market_rows": len(market),
        "aggregate_rows": len(aggregate),
        "unique_hour_rows": len(unique),
        "ordinary_hour_mask": mask_metadata,
        "virtual_supply_rows": len(utilization),
        "placement_headroom_rows": len(headroom),
        "placement_interface_rows": len(interface),
        "automatic_economic_acceptance": False,
        "technical_diagnostic_status": "PASS",
        "production_price_acceptance": "PENDING_METHOD_REVIEW",
    }


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def run_b9d(args: argparse.Namespace) -> dict[str, Any]:
    if not args.execute:
        raise SystemExit("R5_DIAGNOSTIC_NOT_EXECUTED: rerun the exact prepared command with --execute")
    prepared = prepare_r5(write_artifacts=True)
    config = prepared["config"]
    execution_config = load_execution_config()
    if (
        config["solver"]["name"] != execution_config["solver"]["name"]
        or config["solver"]["options"] != execution_config["solver"]["options"]
    ):
        raise RuntimeError("R5_SOLVER_CONFIG_DRIFT")
    started = time.perf_counter()
    qa = ROOT / config["phase"]["qa_directory"]
    log = qa / "logs/MEM_ETX7B9D_raw.log"
    _append_log(log, "ETX-7B9D R5 diagnostic started; production acceptance remains pending.")
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
        raise RuntimeError(f"R5_POST_SOLVE_TECHNICAL_QA_FAILED: {metrics}")
    for row in prepared["contract"].itertuples(index=False):
        dispatch = network.generators_t.p[row.generator_id].astype(float)
        if dispatch.min() < -1e-5 or dispatch.max() > float(row.p_nom_MW) + 1e-5:
            raise RuntimeError(f"R5_VIRTUAL_DISPATCH_BOUND_FAILURE_{row.generator_id}")

    result_dir = ROOT / config["phase"]["result_directory"]
    result_artifacts, outputs = _write_solve_outputs(
        network,
        result_dir,
        config["phase"]["result_stem"],
    )
    diagnostics, diagnostic_qa = write_post_solve_diagnostics(network, prepared)
    _append_log(
        log,
        "ETX-7B9D technical diagnostic PASS; production-price acceptance remains pending analytical review.",
    )
    result_artifacts.extend(prepared["artifacts"])
    result_artifacts.extend(diagnostics)
    result_artifacts.append(log)
    manifest = result_dir / f"{config['phase']['result_stem']}_Result_Manifest_v1.0.csv"
    write_manifest(manifest, result_artifacts)
    outputs.update(
        {
            "manifest": relative_path(manifest),
            "manifest_sha256": sha256_file(manifest),
            "raw_log": relative_path(log),
            "preparation_artifacts": [relative_path(path) for path in prepared["artifacts"]],
            "diagnostics": {path.stem: relative_path(path) for path in diagnostics},
        }
    )
    after = verify_r5_inputs(config)
    receipt = build_receipt(
        phase=config["phase"]["id"],
        gate=config["phase"]["gate"],
        status="PASS",
        input_manifests=[
            {
                "input_id": "B9B_2050_BOUNDED_BASELINE",
                "path": relative_path(after["b9b"]["receipt_path"]),
                "observed_sha256": after["b9b"]["receipt_sha256"],
                "status": "PASS",
            },
            {
                "input_id": "B9C_2050_R10_DIAGNOSTIC",
                "path": relative_path(
                    after["accepted"]["b9c_2050_r10_diagnostic"]["receipt_path"]
                ),
                "observed_sha256": after["accepted"]["b9c_2050_r10_diagnostic"][
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
            "LP_assertions": {**linopy, **gurobi_lp},
            "post_solve": metrics,
            "comparisons": diagnostic_qa,
            "technical_diagnostic_status": "TECHNICAL_DIAGNOSTIC_PASS",
            "economic_methodological_acceptance": "PENDING_METHOD_REVIEW",
            "production_price_source": False,
            "automatic_capacity_iteration": False,
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
    config = load_r5_config()
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
    parser = argparse.ArgumentParser(description="MEM Stage-A 2050 R5 diagnostic")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("prepare", help="Run unsolved R5 preparation and structural QA")
    run = subparsers.add_parser("b9d", help="Run the guarded 2050 R5 full-year diagnostic")
    run.add_argument("--execute", action="store_true", help="Required explicit manual execution acknowledgement")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "prepare":
        try:
            prepared = prepare_r5(write_artifacts=True)
        except Exception as error:
            print(json.dumps({"status": "FAIL", "error": str(error)}, indent=2))
            raise SystemExit(1) from error
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "phase": prepared["config"]["phase"]["id"],
                    "capacities_MW": prepared["payload"]["capacities_MW"],
                    "total_capacity_MW": prepared["payload"]["total_capacity_MW"],
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
        receipt = run_b9d(args)
    except SystemExit:
        raise
    except Exception as error:
        path = _failure_receipt(error)
        print(json.dumps({"status": "FAIL", "receipt": relative_path(path), "error": str(error)}, indent=2))
        raise SystemExit(1) from error
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "gate": receipt["gate"],
                "receipt": load_r5_config()["phase"]["receipt"],
                "technical_diagnostic_status": "TECHNICAL_DIAGNOSTIC_PASS",
                "production_price_acceptance": "PENDING_METHOD_REVIEW",
                "B10_authorized": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

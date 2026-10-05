"""Read-only diagnostics for the accepted ETX-7B7 2040 smoke result."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pypsa

from mem_model.stage_a.network import MARKETS, ROOT, load_execution_config
from mem_model.stage_a.receipts import relative_path, sha256_file


RECEIPT_PATH = ROOT / "qa/stage_a/etx7b7/MEM_ETX7B7_Run_Receipt_v1.0.json"
QA_DIR = ROOT / "qa/stage_a/etx7b7"
EXPECTED_GATE = "ETX7B7_2040_BASE_SMOKE_COMPLETE"
SHEDDING_HOUR_THRESHOLD_MW = 1.0
SYSTEM_SHEDDING_TOLERANCE_MW = 1e-6
VOLL_TOLERANCE_EUR_PER_MWH = 1e-6


def _single_output(receipt: dict[str, Any], suffix: str) -> Path:
    matches = [ROOT / value for key, value in receipt["outputs"].items() if key.endswith(suffix)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one B7 output ending {suffix!r}; found {len(matches)}")
    if not matches[0].exists():
        raise FileNotFoundError(matches[0])
    return matches[0]


def _verify_manifest_member(manifest: pd.DataFrame, path: Path) -> str:
    rel = relative_path(path)
    selected = manifest.loc[manifest["relative_path"].eq(rel)]
    if len(selected) != 1:
        raise RuntimeError(f"B7 manifest does not contain exactly one row for {rel}")
    observed = sha256_file(path)
    expected = str(selected.iloc[0]["sha256"])
    if observed != expected:
        raise RuntimeError(f"B7 output hash mismatch for {rel}")
    return observed


def _component_name_column(frame: pd.DataFrame) -> str:
    for candidate in ("name", "generator_id", "market"):
        if candidate in frame.columns:
            return candidate
    raise ValueError(f"No component-name column in {list(frame.columns)}")


def generate_b7_diagnostic() -> dict[str, Any]:
    """Generate diagnostics from existing B7 files; never builds or solves a model."""

    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    if receipt.get("status") != "PASS" or receipt.get("gate") != EXPECTED_GATE:
        raise RuntimeError("The authoritative B7 receipt is not an accepted PASS")
    if int(receipt.get("snapshots", 0)) != 168:
        raise RuntimeError("The accepted B7 receipt does not contain 168 snapshots")

    result_manifest_path = ROOT / receipt["outputs"]["manifest"]
    if sha256_file(result_manifest_path) != receipt["outputs"]["manifest_sha256"]:
        raise RuntimeError("The accepted B7 result manifest hash changed")
    result_manifest = pd.read_csv(result_manifest_path)

    solved_path = _single_output(receipt, "_SOLVED")
    shedding_path = _single_output(receipt, "_Load_Shedding")
    prices_path = _single_output(receipt, "_Market_Prices")
    source_hashes = {
        relative_path(path): _verify_manifest_member(result_manifest, path)
        for path in (solved_path, shedding_path, prices_path)
    }

    network = pypsa.Network(solved_path)
    if len(network.snapshots) != 168 or network.snapshots.has_duplicates:
        raise RuntimeError("Existing B7 solved network does not contain 168 unique snapshots")
    if not (network.snapshots.to_series().diff().dropna() == pd.Timedelta(hours=1)).all():
        raise RuntimeError("Existing B7 snapshots are not a contiguous hourly window")
    weights = network.snapshot_weightings.objective.astype(float)
    if not np.allclose(weights.to_numpy(), 1.0, atol=0.0, rtol=0.0):
        raise RuntimeError("Existing B7 snapshot weights are not exactly one hour")

    shedding = pd.read_parquet(shedding_path).copy()
    shedding_name = _component_name_column(shedding)
    shedding["market"] = shedding[shedding_name].astype(str).str.removeprefix("LOAD_SHEDDING_")
    if set(shedding["market"]) != set(MARKETS):
        raise RuntimeError("Existing B7 shedding output does not cover the exact ten markets")
    shedding["weight"] = shedding["snapshot"].map(weights)
    if shedding["weight"].isna().any():
        raise RuntimeError("B7 shedding timestamps do not match solved-network weights")
    shedding["shedding_MWh"] = shedding["shedding_MW"].astype(float) * shedding["weight"]

    prices = pd.read_parquet(prices_path).copy()
    price_name = _component_name_column(prices)
    prices["market"] = prices[price_name].astype(str)
    if set(prices["market"]) != set(MARKETS):
        raise RuntimeError("Existing B7 price output does not cover the exact ten markets")

    config = load_execution_config()
    voll = float(config["assembly"]["feasibility"]["VOLL_EUR_per_MWh"])
    market_rows: list[dict[str, Any]] = []
    for market in MARKETS:
        market_shedding = shedding.loc[shedding["market"].eq(market)]
        market_prices = prices.loc[prices["market"].eq(market), "marginal_price_EUR_per_MWh"].astype(float)
        if len(market_shedding) != 168 or len(market_prices) != 168:
            raise RuntimeError(f"B7 diagnostic series is not 168 hours for {market}")
        market_rows.append(
            {
                "market": market,
                "shedding_MWh": float(market_shedding["shedding_MWh"].sum()),
                "max_shedding_MW": float(market_shedding["shedding_MW"].max()),
                "hours_shedding_gt_1_MW": int(
                    market_shedding["shedding_MW"].gt(SHEDDING_HOUR_THRESHOLD_MW).sum()
                ),
                "price_min_EUR_per_MWh": float(market_prices.min()),
                "price_mean_EUR_per_MWh": float(market_prices.mean()),
                "price_max_EUR_per_MWh": float(market_prices.max()),
                "hours_price_at_VOLL": int(
                    np.isclose(
                        market_prices.to_numpy(),
                        voll,
                        atol=VOLL_TOLERANCE_EUR_PER_MWH,
                        rtol=0.0,
                    ).sum()
                ),
            }
        )
    market_diagnostic = pd.DataFrame.from_records(market_rows)

    shedding_pivot = shedding.pivot(index="snapshot", columns="market", values="shedding_MW").reindex(
        columns=MARKETS
    )
    system_hours_any_shedding = int(
        shedding_pivot.sum(axis=1).gt(SYSTEM_SHEDDING_TOLERANCE_MW).sum()
    )
    total_shedding_mwh = float(market_diagnostic["shedding_MWh"].sum())
    receipt_shedding = float(receipt["qa"]["post_solve"]["load_shedding"]["total_MWh"])
    if not np.isclose(total_shedding_mwh, receipt_shedding, atol=1e-6, rtol=0.0):
        raise RuntimeError("Computed B7 shedding does not reconcile to the accepted receipt")

    load = network.loads_t.p_set.astype(float)
    total_load_mwh = float(load.mul(weights, axis=0).sum().sum())
    store_count = int(len(network.stores))
    water_state_count = int(network.buses.carrier.eq("WATER_STATE").sum())
    all_states_cyclic = bool(network.stores.e_cyclic.astype(bool).all())
    if store_count != 33 or water_state_count != 18 or not all_states_cyclic:
        raise RuntimeError("Accepted B7 cyclic state controls do not reconcile to 33 stores / 18 water buses")

    market_path = QA_DIR / "MEM_ETX7B7_2040_Base_Smoke_Market_Diagnostic_v1.0.csv"
    market_diagnostic.to_csv(market_path, index=False, encoding="utf-8", lineterminator="\n")
    diagnostic = {
        "schema_version": "MEM_ETX7B7_2040_BASE_SMOKE_DIAGNOSTIC_V1_0",
        "status": "PASS",
        "source_gate": EXPECTED_GATE,
        "source_receipt": relative_path(RECEIPT_PATH),
        "source_receipt_sha256": sha256_file(RECEIPT_PATH),
        "source_result_manifest": relative_path(result_manifest_path),
        "source_result_manifest_sha256": sha256_file(result_manifest_path),
        "source_output_hashes": source_hashes,
        "optimization_rerun": False,
        "economic_interpretation": "DEFERRED_TO_ETX7B8_ANNUAL_RESULT",
        "adequacy_conclusion": "NOT_DRAWN_FROM_SMOKE_RESULT",
        "window": {
            "selection": "PEAK_RESIDUAL_LOAD_CONTIGUOUS_WINDOW",
            "snapshots": 168,
            "start": network.snapshots[0].isoformat(),
            "end": network.snapshots[-1].isoformat(),
            "contiguous_hourly": True,
            "snapshot_weight_hours": 1.0,
        },
        "system": {
            "total_shedding_MWh": total_shedding_mwh,
            "system_hours_with_any_shedding": system_hours_any_shedding,
            "system_any_shedding_tolerance_MW": SYSTEM_SHEDDING_TOLERANCE_MW,
            "total_load_MWh": total_load_mwh,
            "shedding_percent_of_total_load": 100.0 * total_shedding_mwh / total_load_mwh,
            "storage_state_count": store_count,
            "water_state_bus_count": water_state_count,
            "all_storage_states_cyclic": all_states_cyclic,
            "SMOKE_STATES_CYCLIC_OVER_168H": True,
        },
        "thresholds": {
            "market_shedding_hour_threshold_MW": SHEDDING_HOUR_THRESHOLD_MW,
            "price_at_VOLL_tolerance_EUR_per_MWh": VOLL_TOLERANCE_EUR_PER_MWH,
            "VOLL_EUR_per_MWh": voll,
        },
        "markets": market_rows,
    }
    diagnostic_path = QA_DIR / "MEM_ETX7B7_2040_Base_Smoke_Diagnostic_v1.0.json"
    diagnostic_path.write_text(json.dumps(diagnostic, indent=2) + "\n", encoding="utf-8")

    manifest_path = QA_DIR / "MEM_ETX7B7_2040_Base_Smoke_Diagnostic_Manifest_v1.0.csv"
    manifest = pd.DataFrame.from_records(
        [
            {
                "relative_path": relative_path(path),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "status": "EXISTING_RESULT_DIAGNOSTIC",
            }
            for path in sorted((diagnostic_path, market_path), key=relative_path)
        ]
    )
    manifest.to_csv(manifest_path, index=False, encoding="utf-8", lineterminator="\n")
    return {
        "status": "PASS",
        "diagnostic": relative_path(diagnostic_path),
        "market_diagnostic": relative_path(market_path),
        "manifest": relative_path(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "optimization_rerun": False,
    }


def main() -> None:
    print(json.dumps(generate_b7_diagnostic(), indent=2))


if __name__ == "__main__":
    main()

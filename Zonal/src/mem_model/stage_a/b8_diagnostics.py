"""Immutable, read-only diagnostics for the accepted ETX-7B8 annual result."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pypsa

from mem_model.stage_a.network import MARKETS, ROOT, _safe_id, load_execution_config
from mem_model.stage_a.receipts import relative_path, sha256_file


RECEIPT_PATH = ROOT / "qa/stage_a/etx7b8/MEM_ETX7B8_Run_Receipt_v1.0.json"
QA_DIR = ROOT / "qa/stage_a/etx7b8"
EXPECTED_GATE = "ETX7B8_2040_BASE_FULL_YEAR_COMPLETE"
INTERFACE_PATH = ROOT / "stage_a_inputs/topology_v1_0/MEM_ETX7B5_Physical_Interface_Registry_v1.0.csv"
LINK_PATH = ROOT / "stage_a_inputs/topology_v1_0/MEM_ETX7B5_Directional_Link_Registry_v1.0.csv"
AFFECTED_MARKETS = ("FR", "TN", "GR")
SHEDDING_TOLERANCE_MW = 1e-6
INTERFACE_LIMIT_TOLERANCE_MW = 1e-6
VOLL_TOLERANCE_EUR_PER_MWH = 1e-6


def _component_name_column(frame: pd.DataFrame) -> str:
    for candidate in ("name", "generator_id", "market", "link_id"):
        if candidate in frame.columns:
            return candidate
    raise ValueError(f"No component-name column in {list(frame.columns)}")


def _single_output(receipt: dict[str, Any], suffix: str) -> Path:
    matches = [ROOT / value for key, value in receipt["outputs"].items() if key.endswith(suffix)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one B8 output ending {suffix!r}; found {len(matches)}")
    if not matches[0].exists():
        raise FileNotFoundError(matches[0])
    return matches[0]


def verify_b8_immutability() -> dict[str, Any]:
    """Require the accepted receipt, manifest, and every result member to match."""

    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    required = {
        "status": "PASS",
        "gate": EXPECTED_GATE,
        "snapshots": 8760,
        "solver_status": "ok",
        "termination_condition": "optimal",
    }
    observed = {key: receipt.get(key) for key in required}
    if observed != required:
        raise RuntimeError(f"B8_RECEIPT_NOT_ACCEPTED: observed={observed}; required={required}")
    manifest_path = ROOT / receipt["outputs"]["manifest"]
    manifest_hash = sha256_file(manifest_path)
    if manifest_hash != receipt["outputs"]["manifest_sha256"]:
        raise RuntimeError("B8_RESULT_MANIFEST_HASH_MISMATCH")
    manifest = pd.read_csv(manifest_path)
    member_rows: list[dict[str, Any]] = []
    for row in manifest.itertuples():
        path = ROOT / row.relative_path
        if not path.exists():
            raise FileNotFoundError(path)
        member_hash = sha256_file(path)
        if member_hash != str(row.sha256):
            raise RuntimeError(f"B8_RESULT_MEMBER_HASH_MISMATCH: {row.relative_path}")
        member_rows.append({"path": row.relative_path, "sha256": member_hash})
    return {
        "receipt": receipt,
        "receipt_path": RECEIPT_PATH,
        "receipt_sha256": sha256_file(RECEIPT_PATH),
        "manifest_path": manifest_path,
        "manifest_sha256": manifest_hash,
        "manifest": manifest,
        "members": member_rows,
    }


def _manifest_member_hash(verification: dict[str, Any], path: Path) -> str:
    relative = relative_path(path)
    matches = [row["sha256"] for row in verification["members"] if row["path"] == relative]
    if len(matches) != 1:
        raise RuntimeError(f"B8 manifest member not unique: {relative}")
    return matches[0]


def _price_statistics(values: pd.Series, voll: float) -> dict[str, Any]:
    numeric = values.astype(float)
    return {
        "price_min_EUR_per_MWh": float(numeric.min()),
        "price_mean_EUR_per_MWh": float(numeric.mean()),
        "price_median_EUR_per_MWh": float(numeric.median()),
        "price_P95_EUR_per_MWh": float(numeric.quantile(0.95)),
        "price_P99_EUR_per_MWh": float(numeric.quantile(0.99)),
        "price_max_EUR_per_MWh": float(numeric.max()),
        "hours_at_VOLL": int(
            np.isclose(numeric.to_numpy(), voll, atol=VOLL_TOLERANCE_EUR_PER_MWH, rtol=0.0).sum()
        ),
        "hours_above_500_EUR_per_MWh": int(numeric.gt(500.0).sum()),
        "hours_above_1000_EUR_per_MWh": int(numeric.gt(1000.0).sum()),
        "hours_above_5000_EUR_per_MWh": int(numeric.gt(5000.0).sum()),
    }


def _normalize_market_tables(
    network: pypsa.Network,
    shedding_path: Path,
    prices_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Series]:
    weights = network.snapshot_weightings.objective.astype(float)
    if not np.allclose(weights.to_numpy(), 1.0, atol=0.0, rtol=0.0):
        raise RuntimeError("B8 snapshot weights are not exactly one hour")

    load = network.loads_t.p_set[[f"LOAD_{market}" for market in MARKETS]].copy()
    load.columns = [str(column).removeprefix("LOAD_") for column in load.columns]
    load = load.reindex(index=network.snapshots, columns=MARKETS)

    shedding_long = pd.read_parquet(shedding_path).copy()
    shedding_name = _component_name_column(shedding_long)
    shedding_long["market"] = shedding_long[shedding_name].astype(str).str.removeprefix("LOAD_SHEDDING_")
    shedding = shedding_long.pivot(index="snapshot", columns="market", values="shedding_MW").reindex(
        index=network.snapshots, columns=MARKETS
    )

    prices_long = pd.read_parquet(prices_path).copy()
    price_name = _component_name_column(prices_long)
    prices_long["market"] = prices_long[price_name].astype(str)
    prices = prices_long.pivot(
        index="snapshot", columns="market", values="marginal_price_EUR_per_MWh"
    ).reindex(index=network.snapshots, columns=MARKETS)
    for label, frame in (("load", load), ("shedding", shedding), ("prices", prices)):
        if frame.isna().any().any() or not np.isfinite(frame.to_numpy(dtype=float)).all():
            raise RuntimeError(f"B8 {label} table is incomplete or non-finite")
    return load, shedding, prices, weights


def _build_market_diagnostic(
    load: pd.DataFrame,
    shedding: pd.DataFrame,
    prices: pd.DataFrame,
    weights: pd.Series,
    voll: float,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for market in MARKETS:
        load_mwh = float((load[market] * weights).sum())
        shedding_mwh = float((shedding[market] * weights).sum())
        rows.append(
            {
                "market": market,
                "annual_load_MWh": load_mwh,
                "load_shedding_MWh": shedding_mwh,
                "load_shedding_percent_of_local_load": 100.0 * shedding_mwh / load_mwh,
                "maximum_hourly_shedding_MW": float(shedding[market].max()),
                "hours_shedding_above_1_MW": int(shedding[market].gt(1.0).sum()),
                **_price_statistics(prices[market], voll),
            }
        )
    return pd.DataFrame.from_records(rows)


def _build_monthly_diagnostic(
    load: pd.DataFrame,
    shedding: pd.DataFrame,
    prices: pd.DataFrame,
    weights: pd.Series,
    voll: float,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    months = pd.Index(load.index.to_period("M").astype(str), name="month")
    for market in MARKETS:
        frame = pd.DataFrame(
            {
                "month": months,
                "load_MW": load[market].to_numpy(),
                "shedding_MW": shedding[market].to_numpy(),
                "price": prices[market].to_numpy(),
                "weight": weights.to_numpy(),
            },
            index=load.index,
        )
        for month, selected in frame.groupby("month", sort=True):
            price_stats = _price_statistics(selected["price"], voll)
            rows.append(
                {
                    "market": market,
                    "month": month,
                    "load_MWh": float((selected["load_MW"] * selected["weight"]).sum()),
                    "shedding_MWh": float((selected["shedding_MW"] * selected["weight"]).sum()),
                    "maximum_shedding_MW": float(selected["shedding_MW"].max()),
                    "hours_shedding_above_1_MW": int(selected["shedding_MW"].gt(1.0).sum()),
                    "price_mean_EUR_per_MWh": price_stats["price_mean_EUR_per_MWh"],
                    "price_median_EUR_per_MWh": price_stats["price_median_EUR_per_MWh"],
                    "price_P95_EUR_per_MWh": price_stats["price_P95_EUR_per_MWh"],
                    "price_P99_EUR_per_MWh": price_stats["price_P99_EUR_per_MWh"],
                    "hours_at_VOLL": price_stats["hours_at_VOLL"],
                }
            )
    return pd.DataFrame.from_records(rows)


def _directional_limit(
    directional: pd.DataFrame,
    physical_link_id: str,
    origin: str,
    destination: str,
) -> float:
    selected = directional.loc[
        directional["physical_link_id"].eq(physical_link_id)
        & directional["from_market"].eq(origin)
        & directional["to_market"].eq(destination)
        & directional["year"].eq(2040)
        & directional["scenario"].eq("Base")
    ]
    if len(selected) != 1:
        raise RuntimeError(f"Directional B5 limit not unique: {physical_link_id} {origin}->{destination}")
    return float(selected.iloc[0]["capacity_MW"])


def _build_interface_diagnostics(
    network: pypsa.Network,
    shedding: pd.DataFrame,
    prices: pd.DataFrame,
    weights: pd.Series,
    flows_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    interfaces = pd.read_csv(INTERFACE_PATH)
    directional = pd.read_csv(LINK_PATH)
    flow_long = pd.read_parquet(flows_path).copy()
    name_column = _component_name_column(flow_long)
    interface_names = {
        f"INTERCONNECTOR_{_safe_id(physical_id)}": physical_id
        for physical_id in interfaces["physical_link_id"]
    }
    flow_long = flow_long.loc[flow_long[name_column].isin(interface_names)].copy()
    flow_long["physical_link_id"] = flow_long[name_column].map(interface_names)
    flows = flow_long.pivot(
        index="snapshot", columns="physical_link_id", values="signed_p0_MW"
    ).reindex(index=network.snapshots, columns=interfaces["physical_link_id"])
    if flows.isna().any().any() or len(flows.columns) != 12:
        raise RuntimeError("B8 signed interconnector flows are incomplete")

    annual_rows: list[dict[str, Any]] = []
    limits: dict[str, dict[str, Any]] = {}
    for interface in interfaces.sort_values("physical_link_id", kind="stable").itertuples():
        a_to_b = _directional_limit(
            directional, interface.physical_link_id, interface.endpoint_a, interface.endpoint_b
        )
        b_to_a = _directional_limit(
            directional, interface.physical_link_id, interface.endpoint_b, interface.endpoint_a
        )
        series = flows[interface.physical_link_id].astype(float)
        limits[interface.physical_link_id] = {
            "endpoint_a": interface.endpoint_a,
            "endpoint_b": interface.endpoint_b,
            "a_to_b": a_to_b,
            "b_to_a": b_to_a,
        }
        annual_rows.append(
            {
                "record_type": "PHYSICAL_INTERFACE_ANNUAL",
                "physical_link_id": interface.physical_link_id,
                "affected_market": "",
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
                "retained_inbound_interface_count": np.nan,
                "shedding_hours": np.nan,
                "shedding_MWh": np.nan,
                "all_inbound_saturated_hours": np.nan,
                "share_shedding_hours_all_inbound_saturated": np.nan,
                "shedding_MWh_all_inbound_saturated": np.nan,
                "share_shedding_MWh_all_inbound_saturated": np.nan,
            }
        )

    detail_rows: list[dict[str, Any]] = []
    aggregate_rows: list[dict[str, Any]] = []
    for market in AFFECTED_MARKETS:
        attached = interfaces.loc[
            interfaces["endpoint_a"].eq(market) | interfaces["endpoint_b"].eq(market)
        ].sort_values("physical_link_id", kind="stable")
        shedding_hours = shedding.index[shedding[market].gt(SHEDDING_TOLERANCE_MW)]
        hour_rows: list[dict[str, Any]] = []
        for snapshot in shedding_hours:
            saturation_flags: list[bool] = []
            for interface in attached.itertuples():
                data = limits[interface.physical_link_id]
                signed_flow = float(flows.at[snapshot, interface.physical_link_id])
                if market == data["endpoint_b"]:
                    source = data["endpoint_a"]
                    inbound_flow = signed_flow
                    inbound_limit = float(data["a_to_b"])
                else:
                    source = data["endpoint_b"]
                    inbound_flow = -signed_flow
                    inbound_limit = float(data["b_to_a"])
                saturated = inbound_flow >= inbound_limit - INTERFACE_LIMIT_TOLERANCE_MW
                saturation_flags.append(bool(saturated))
                detail_rows.append(
                    {
                        "snapshot": snapshot,
                        "affected_market": market,
                        "physical_link_id": interface.physical_link_id,
                        "inbound_from_market": source,
                        "local_shedding_MW": float(shedding.at[snapshot, market]),
                        "local_price_EUR_per_MWh": float(prices.at[snapshot, market]),
                        "inbound_flow_MW": inbound_flow,
                        "inbound_accepted_limit_MW": inbound_limit,
                        "inbound_interface_saturated": bool(saturated),
                    }
                )
            hour_rows.append(
                {
                    "snapshot": snapshot,
                    "shedding_MW": float(shedding.at[snapshot, market]),
                    "all_inbound_saturated": bool(saturation_flags) and all(saturation_flags),
                }
            )
        hours = pd.DataFrame.from_records(hour_rows)
        shedding_mwh = float((shedding.loc[shedding_hours, market] * weights.loc[shedding_hours]).sum())
        if hours.empty:
            saturated_hours = 0
            saturated_mwh = 0.0
        else:
            saturated_hours = int(hours["all_inbound_saturated"].sum())
            saturated_index = pd.DatetimeIndex(hours.loc[hours["all_inbound_saturated"], "snapshot"])
            saturated_mwh = float(
                (shedding.loc[saturated_index, market] * weights.loc[saturated_index]).sum()
            )
        aggregate = {
            "market": market,
            "retained_inbound_interface_count": len(attached),
            "shedding_hours": len(shedding_hours),
            "shedding_MWh": shedding_mwh,
            "all_inbound_saturated_hours": saturated_hours,
            "share_shedding_hours_all_inbound_saturated": (
                saturated_hours / len(shedding_hours) if len(shedding_hours) else 0.0
            ),
            "shedding_MWh_all_inbound_saturated": saturated_mwh,
            "share_shedding_MWh_all_inbound_saturated": (
                saturated_mwh / shedding_mwh if shedding_mwh else 0.0
            ),
        }
        aggregate_rows.append(aggregate)
        annual_rows.append(
            {
                "record_type": "AFFECTED_MARKET_SCARCITY_AGGREGATE",
                "physical_link_id": "",
                "affected_market": market,
                "endpoint_a": "",
                "endpoint_b": "",
                "accepted_a_to_b_limit_MW": np.nan,
                "accepted_b_to_a_limit_MW": np.nan,
                "annual_net_a_to_b_MWh": np.nan,
                "annual_gross_a_to_b_MWh": np.nan,
                "annual_gross_b_to_a_MWh": np.nan,
                "hours_at_or_near_a_to_b_limit": np.nan,
                "hours_at_or_near_b_to_a_limit": np.nan,
                **{key: value for key, value in aggregate.items() if key != "market"},
            }
        )
    return (
        pd.DataFrame.from_records(annual_rows),
        pd.DataFrame.from_records(detail_rows),
        aggregate_rows,
    )


def generate_b8_diagnostic() -> dict[str, Any]:
    """Write the annual diagnostic without building or optimizing a model."""

    before = verify_b8_immutability()
    receipt = before["receipt"]
    solved_path = _single_output(receipt, "_SOLVED")
    prices_path = _single_output(receipt, "_Market_Prices")
    shedding_path = _single_output(receipt, "_Load_Shedding")
    flows_path = _single_output(receipt, "_Link_Flows")
    spill_path = _single_output(receipt, "_Hydro_Spill")
    curtailment_path = _single_output(receipt, "_VRE_Curtailment")

    network = pypsa.Network(solved_path)
    if len(network.snapshots) != 8760 or network.snapshots.has_duplicates:
        raise RuntimeError("B8 solved network does not contain 8,760 unique snapshots")
    if not (network.snapshots.to_series().diff().dropna() == pd.Timedelta(hours=1)).all():
        raise RuntimeError("B8 solved network chronology is not contiguous hourly")
    load, shedding, prices, weights = _normalize_market_tables(
        network, shedding_path, prices_path
    )
    config = load_execution_config()
    voll = float(config["assembly"]["feasibility"]["VOLL_EUR_per_MWh"])

    market_diagnostic = _build_market_diagnostic(load, shedding, prices, weights, voll)
    monthly_diagnostic = _build_monthly_diagnostic(load, shedding, prices, weights, voll)
    interface_diagnostic, interface_detail, affected_aggregates = _build_interface_diagnostics(
        network, shedding, prices, weights, flows_path
    )

    total_load_mwh = float(load.mul(weights, axis=0).sum().sum())
    total_shedding_mwh = float(shedding.mul(weights, axis=0).sum().sum())
    simultaneous_shedding = shedding.sum(axis=1)
    price_stats = _price_statistics(prices.stack(future_stack=True), voll)

    curtailment = pd.read_parquet(curtailment_path)
    curtailment["weight"] = curtailment["snapshot"].map(weights)
    spill = pd.read_parquet(spill_path)
    spill["weight"] = spill["snapshot"].map(weights)
    if curtailment["weight"].isna().any() or spill["weight"].isna().any():
        raise RuntimeError("B8 curtailment/spill timestamps do not match the accepted chronology")
    total_curtailment_mwh = float(
        (curtailment["curtailment_MW"].astype(float) * curtailment["weight"]).sum()
    )
    total_spill_mwh_water = float(
        (spill["spill_MW_water"].astype(float) * spill["weight"]).sum()
    )
    storage_count = int(len(network.stores))
    water_state_count = int(network.buses.carrier.eq("WATER_STATE").sum())
    cyclic_count = int(network.stores.e_cyclic.astype(bool).sum())
    all_states_cyclic = cyclic_count == storage_count

    system = {
        "total_system_load_MWh": total_load_mwh,
        "total_system_load_shedding_MWh": total_shedding_mwh,
        "load_shedding_percent_of_total_load": 100.0 * total_shedding_mwh / total_load_mwh,
        "system_hours_with_any_shedding_above_1e_6_MW": int(
            simultaneous_shedding.gt(SHEDDING_TOLERANCE_MW).sum()
        ),
        "system_hours_with_shedding_above_1_MW": int(simultaneous_shedding.gt(1.0).sum()),
        "maximum_simultaneous_system_shedding_MW": float(simultaneous_shedding.max()),
        **{
            "price_min_EUR_per_MWh": price_stats["price_min_EUR_per_MWh"],
            "price_mean_EUR_per_MWh": price_stats["price_mean_EUR_per_MWh"],
            "price_median_EUR_per_MWh": price_stats["price_median_EUR_per_MWh"],
            "price_P95_EUR_per_MWh": price_stats["price_P95_EUR_per_MWh"],
            "price_P99_EUR_per_MWh": price_stats["price_P99_EUR_per_MWh"],
            "price_max_EUR_per_MWh": price_stats["price_max_EUR_per_MWh"],
            "total_market_hours_at_VOLL": price_stats["hours_at_VOLL"],
            "total_market_hours_above_500_EUR_per_MWh": price_stats[
                "hours_above_500_EUR_per_MWh"
            ],
            "total_market_hours_above_1000_EUR_per_MWh": price_stats[
                "hours_above_1000_EUR_per_MWh"
            ],
            "total_market_hours_above_5000_EUR_per_MWh": price_stats[
                "hours_above_5000_EUR_per_MWh"
            ],
        },
        "VRE_curtailment_MWh": total_curtailment_mwh,
        "hydro_spill_MWh_water": total_spill_mwh_water,
        "storage_state_count": storage_count,
        "water_state_bus_count": water_state_count,
        "cyclic_state_count": cyclic_count,
        "ANNUAL_STATES_CYCLIC_OVER_8760H": all_states_cyclic,
    }
    receipt_shedding = float(receipt["qa"]["post_solve"]["load_shedding"]["total_MWh"])
    if not np.isclose(total_shedding_mwh, receipt_shedding, atol=1e-6, rtol=0.0):
        raise RuntimeError("B8 diagnostic shedding does not reconcile to the accepted receipt")

    QA_DIR.mkdir(parents=True, exist_ok=True)
    market_path = QA_DIR / "MEM_ETX7B8_2040_Base_Market_Diagnostic_v1.0.csv"
    monthly_path = QA_DIR / "MEM_ETX7B8_2040_Base_Scarcity_Monthly_v1.0.csv"
    interface_path = QA_DIR / "MEM_ETX7B8_2040_Base_Scarcity_Interface_Diagnostic_v1.0.csv"
    detail_path = QA_DIR / "MEM_ETX7B8_2040_Base_Affected_Market_Shedding_Interface_Hour_v1.0.parquet"
    market_diagnostic.to_csv(market_path, index=False, encoding="utf-8", lineterminator="\n")
    monthly_diagnostic.to_csv(monthly_path, index=False, encoding="utf-8", lineterminator="\n")
    interface_diagnostic.to_csv(interface_path, index=False, encoding="utf-8", lineterminator="\n")
    interface_detail.to_parquet(detail_path, index=False)

    diagnostic = {
        "schema_version": "MEM_ETX7B8_2040_BASE_ANNUAL_DIAGNOSTIC_V1_0",
        "status": "PASS",
        "baseline_identity": "IMMUTABLE_BOUNDED_TEN_MARKET_ANNUAL_BASELINE",
        "source_receipt": relative_path(RECEIPT_PATH),
        "source_receipt_sha256": before["receipt_sha256"],
        "source_result_manifest": relative_path(before["manifest_path"]),
        "source_result_manifest_sha256": before["manifest_sha256"],
        "source_manifest_members_verified": len(before["members"]),
        "source_output_hashes": {
            relative_path(path): _manifest_member_hash(before, path)
            for path in (
                solved_path,
                prices_path,
                shedding_path,
                flows_path,
                spill_path,
                curtailment_path,
            )
        },
        "optimization_rerun": False,
        "system": system,
        "affected_market_interface_saturation": affected_aggregates,
        "thresholds": {
            "shedding_tolerance_MW": SHEDDING_TOLERANCE_MW,
            "interface_limit_tolerance_MW": INTERFACE_LIMIT_TOLERANCE_MW,
            "VOLL_EUR_per_MWh": voll,
            "VOLL_tolerance_EUR_per_MWh": VOLL_TOLERANCE_EUR_PER_MWH,
        },
        "interpretation_boundary": {
            "B8": "IMMUTABLE_BOUNDED_TEN_MARKET_ANNUAL_BASELINE",
            "computational_acceptance_distinct_from_economic_boundary_methodology_acceptance": True,
            "scarcity_may_reflect_domestic_capacity_and_truncated_external_perimeter": True,
            "assumptions_changed_by_diagnostic": False,
            "S2_production_methodology_decided_by_diagnostic": False,
            "adequacy_conclusion_automatically_drawn": False,
        },
    }
    diagnostic_path = QA_DIR / "MEM_ETX7B8_2040_Base_Annual_Diagnostic_v1.0.json"
    diagnostic_path.write_text(json.dumps(diagnostic, indent=2) + "\n", encoding="utf-8")

    after = verify_b8_immutability()
    if (
        after["receipt_sha256"] != before["receipt_sha256"]
        or after["manifest_sha256"] != before["manifest_sha256"]
        or after["members"] != before["members"]
    ):
        raise RuntimeError("B8 immutable source changed during diagnostic generation")

    manifest_path = QA_DIR / "MEM_ETX7B8_2040_Base_Annual_Diagnostic_Manifest_v1.0.csv"
    artifacts = [diagnostic_path, market_path, monthly_path, interface_path, detail_path]
    manifest = pd.DataFrame.from_records(
        [
            {
                "relative_path": relative_path(path),
                "bytes": path.stat().st_size,
                "rows": (
                    len(pd.read_csv(path))
                    if path.suffix.lower() == ".csv"
                    else len(pd.read_parquet(path))
                    if path.suffix.lower() == ".parquet"
                    else None
                ),
                "sha256": sha256_file(path),
                "status": "IMMUTABLE_B8_EXISTING_RESULT_DIAGNOSTIC",
            }
            for path in sorted(artifacts, key=relative_path)
        ]
    )
    manifest.to_csv(manifest_path, index=False, encoding="utf-8", lineterminator="\n")
    return {
        "status": "PASS",
        "diagnostic": relative_path(diagnostic_path),
        "market_diagnostic": relative_path(market_path),
        "monthly_diagnostic": relative_path(monthly_path),
        "interface_diagnostic": relative_path(interface_path),
        "interface_hour_detail": relative_path(detail_path),
        "manifest": relative_path(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "optimization_rerun": False,
        "system": system,
    }


def main() -> None:
    print(json.dumps(generate_b8_diagnostic(), indent=2))


if __name__ == "__main__":
    main()

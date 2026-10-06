"""Prepare and run the horizon-specific Stage-A R10 closure experiments.

The capacity rule is applied to immutable bounded-baseline shedding series.
Preparation is solver-free except for a short unsolved Linopy structure check;
the guarded ``b8d`` and ``b9c`` commands are reserved for normal-user Gurobi
execution and never promote their results to production automatically.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable, Iterable

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
    _normalize_market_tables,
    _price_statistics,
)
from mem_model.stage_a.execution import _solve_metrics, _write_solve_outputs, gurobi_preflight
from mem_model.stage_a.network import MARKETS, ROOT, _as_bool, _safe_id, load_execution_config, verify_input_locks
from mem_model.stage_a.perimeter_closure import (
    _assert_frame_equal,
    _assert_gurobi_lp,
    _component_counts,
    _dynamic_tables,
    create_and_validate_linopy_model,
    validate_lp_static,
)
from mem_model.stage_a.receipts import (
    build_receipt,
    command_string,
    relative_path,
    sha256_file,
    write_manifest,
    write_receipt,
)


CONFIG_PATH = ROOT / "config/stage_a_perimeter_closure_method_v2_0.yaml"
CANONICAL_MODULE = "mem_model.stage_a.perimeter_closure_r10"
VIRTUAL_CARRIER = "EXTERNAL_VIRTUAL_SUPPLY"
CANDIDATE_MARKETS = ("FR", "GR", "TN")
SENSITIVITY_SHARES = (0.15, 0.10, 0.075, 0.05)
NEGATIVE_NOISE_TOLERANCE_MW = 1e-6
IDENTITY_TOLERANCE = 1e-12
EXPECTED_MARKET_SET = set(MARKETS)


def load_r10_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if config.get("schema") != "PERIMETER_CLOSURE_METHOD_V2_0":
        raise RuntimeError("R10_METHOD_SCHEMA_MISMATCH")
    if config.get("status") != "CENTRAL_METHOD_PREPARED_PENDING_FULL_YEAR_VALIDATION":
        raise RuntimeError("R10_METHOD_NOT_IN_PREPARED_STATE")
    if tuple(config["method"]["candidate_markets"]) != CANDIDATE_MARKETS:
        raise RuntimeError("R10_CANDIDATE_MARKET_SCOPE_MISMATCH")
    if float(config["method"]["residual_energy_share"]) != 0.10:
        raise RuntimeError("R10_CENTRAL_RESIDUAL_SHARE_MISMATCH")
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


def _row_count(path: Path) -> int | None:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return len(pd.read_csv(path))
    if suffix in {".parquet", ".pq"}:
        return len(pd.read_parquet(path))
    return None


def _verify_manifest(path: Path, expected_hash: str, label: str) -> dict[str, Any]:
    manifest_hash = _require_hash(path, expected_hash, f"{label}_MANIFEST")
    manifest = pd.read_csv(path)
    failures: list[str] = []
    for row in manifest.itertuples():
        member = ROOT / str(row.relative_path)
        try:
            _require_hash(member, str(row.sha256), f"{label}_MEMBER_{member.name}")
        except (FileNotFoundError, RuntimeError):
            failures.append(str(row.relative_path))
    if failures:
        raise RuntimeError(f"{label}_MANIFEST_MEMBER_FAILURES: {failures}")
    return {
        "path": relative_path(path),
        "sha256": manifest_hash,
        "member_count": len(manifest),
        "member_failures": 0,
        "members": manifest.to_dict(orient="records"),
        "status": "PASS",
    }


def _verify_history_item(key: str, spec: dict[str, Any]) -> dict[str, Any]:
    receipt_path = ROOT / spec["receipt"]
    receipt_hash = _require_hash(receipt_path, spec["receipt_sha256"], f"{key}_RECEIPT")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("status") != "PASS" or receipt.get("gate") != spec["required_gate"]:
        raise RuntimeError(f"{key}_RECEIPT_NOT_ACCEPTED")
    manifest_path = ROOT / spec["result_manifest"]
    manifest = _verify_manifest(manifest_path, spec["result_manifest_sha256"], key.upper())
    if receipt["outputs"]["manifest"] != spec["result_manifest"]:
        raise RuntimeError(f"{key}_RECEIPT_MANIFEST_PATH_MISMATCH")
    if receipt["outputs"]["manifest_sha256"] != manifest["sha256"]:
        raise RuntimeError(f"{key}_RECEIPT_MANIFEST_HASH_MISMATCH")
    return {
        "key": key,
        "receipt": receipt,
        "receipt_path": receipt_path,
        "receipt_sha256": receipt_hash,
        "manifest": manifest,
    }


def verify_immutable_history(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Verify B1-B9B history and both accepted B6 unsolved networks."""

    config = config or load_r10_config()
    locks = verify_input_locks(load_execution_config())
    if len(locks) != 7 or sum(int(row["member_failures"]) for row in locks) != 0:
        raise RuntimeError("R10_B1_B5_INPUT_LOCK_FAILURE")
    items: dict[str, Any] = {}
    for key in (
        "b6_network_readiness",
        "b8_2040_bounded_baseline",
        "b8c_s2_max_closure",
        "b9a_2050_smoke",
        "b9b_2050_bounded_baseline",
    ):
        items[key] = _verify_history_item(key, config["accepted_history"][key])

    b6 = items["b6_network_readiness"]["receipt"]
    networks: dict[int, dict[str, Any]] = {}
    for year in (2040, 2050):
        spec = config["accepted_b6_networks"][year]
        path = ROOT / spec["path"]
        observed = _require_hash(path, spec["sha256"], f"B6_{year}_UNSOLVED_NETWORK")
        if b6["outputs"]["networks"][str(year)] != spec["path"]:
            raise RuntimeError(f"B6_{year}_NETWORK_PATH_MISMATCH")
        if b6["qa"]["horizons"][str(year)]["network_sha256"] != observed:
            raise RuntimeError(f"B6_{year}_NETWORK_RECEIPT_HASH_MISMATCH")
        networks[year] = {"path": path, "relative_path": spec["path"], "sha256": observed}

    s2 = items["b8c_s2_max_closure"]["receipt"]
    required_s2 = {
        "phase": "ETX-7B8C-S2-AUXILIARY",
        "gate": "ETX7B8C_S2_EXPERIMENT_COMPLETE",
        "status": "PASS",
        "solver_status": "ok",
        "termination_condition": "optimal",
        "snapshots": 8760,
        "accepted": True,
    }
    if {key: s2.get(key) for key in required_s2} != required_s2:
        raise RuntimeError("S2_HISTORICAL_RECEIPT_CONTENT_MISMATCH")
    if s2["qa"].get("formal_B9_authorized") is not False:
        raise RuntimeError("S2_HISTORICAL_FORMAL_B9_FLAG_CHANGED")

    return {
        "input_locks": locks,
        "history": items,
        "networks": networks,
        "status": "PASS",
    }


def residual_energy_capacity(
    shedding_MW: Iterable[float],
    residual_energy_share: float,
    *,
    identity_tolerance: float = IDENTITY_TOLERANCE,
) -> dict[str, Any]:
    """Solve the exact piecewise exceedance-capacity equation."""

    values = np.asarray(list(shedding_MW), dtype=float)
    if values.ndim != 1 or not np.isfinite(values).all():
        raise ValueError("Shedding series must be one-dimensional and finite")
    if not 0.0 <= float(residual_energy_share) <= 1.0:
        raise ValueError("Residual energy share must lie in [0, 1]")
    minimum = float(values.min()) if values.size else 0.0
    if minimum < -NEGATIVE_NOISE_TOLERANCE_MW:
        raise ValueError(f"Materially negative shedding value: {minimum}")
    clean = np.clip(values, 0.0, None)
    total = float(clean.sum())
    peak = float(clean.max()) if clean.size else 0.0
    positive = np.sort(clean[clean > 0.0])[::-1]
    if total == 0.0:
        return {
            "capacity_MW": 0.0,
            "baseline_shedding_MWh": 0.0,
            "baseline_peak_shedding_MW": 0.0,
            "target_residual_MWh": 0.0,
            "verified_residual_MWh": 0.0,
            "verified_residual_energy_share": 0.0,
            "active_segment_hours": 0,
            "positive_hours": 0,
        }
    target = float(residual_energy_share) * total
    cumulative = np.cumsum(positive)
    capacity: float | None = None
    active_segment = 0
    segment_tolerance = max(identity_tolerance, np.finfo(float).eps * max(peak, 1.0) * 16.0)
    for k in range(1, len(positive) + 1):
        candidate = float((cumulative[k - 1] - target) / k)
        upper = float(positive[k - 1])
        lower = float(positive[k]) if k < len(positive) else 0.0
        if lower - segment_tolerance <= candidate <= upper + segment_tolerance:
            capacity = max(candidate, 0.0)
            active_segment = k
            break
    if capacity is None:
        raise RuntimeError("R10_CAPACITY_SEGMENT_NOT_FOUND")
    residual = float(np.maximum(clean - capacity, 0.0).sum())
    verified_share = residual / total
    if not np.isclose(verified_share, residual_energy_share, atol=identity_tolerance, rtol=0.0):
        raise RuntimeError(
            f"R10_RESIDUAL_IDENTITY_FAILED: target={residual_energy_share}; observed={verified_share}"
        )
    return {
        "capacity_MW": capacity,
        "baseline_shedding_MWh": total,
        "baseline_peak_shedding_MW": peak,
        "target_residual_MWh": target,
        "verified_residual_MWh": residual,
        "verified_residual_energy_share": verified_share,
        "active_segment_hours": active_segment,
        "positive_hours": int((clean > 0.0).sum()),
    }


def _output_path(receipt: dict[str, Any], suffix: str) -> Path:
    matches = [ROOT / value for key, value in receipt["outputs"].items() if key.endswith(suffix)]
    if len(matches) != 1:
        raise RuntimeError(f"BASELINE_OUTPUT_NOT_UNIQUE: suffix={suffix}; count={len(matches)}")
    return matches[0]


def _baseline_key(year: int, config: dict[str, Any]) -> str:
    return str(config["horizons"][year]["baseline_key"])


def _baseline_receipt(year: int, verification: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    return verification["history"][_baseline_key(year, config)]["receipt"]


def _shedding_wide(year: int, verification: dict[str, Any], config: dict[str, Any]) -> pd.DataFrame:
    receipt = _baseline_receipt(year, verification, config)
    path = _output_path(receipt, "_Load_Shedding")
    frame = pd.read_parquet(path).copy()
    name = _component_name_column(frame)
    frame["market"] = frame[name].astype(str).str.removeprefix("LOAD_SHEDDING_")
    wide = frame.pivot(index="snapshot", columns="market", values="shedding_MW").reindex(columns=MARKETS)
    if wide.isna().any().any() or not np.isfinite(wide.to_numpy(dtype=float)).all():
        raise RuntimeError(f"BASELINE_SHEDDING_INCOMPLETE_{year}")
    if len(wide) != 8760 or wide.index.has_duplicates:
        raise RuntimeError(f"BASELINE_SHEDDING_CHRONOLOGY_INVALID_{year}")
    if float(wide.min().min()) < -NEGATIVE_NOISE_TOLERANCE_MW:
        raise RuntimeError(f"BASELINE_SHEDDING_MATERIALLY_NEGATIVE_{year}")
    return wide.clip(lower=0.0)


def derive_horizon_capacities(
    year: int,
    config: dict[str, Any] | None = None,
    verification: dict[str, Any] | None = None,
    *,
    residual_energy_share: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    config = config or load_r10_config()
    verification = verification or verify_immutable_history(config)
    share = (
        float(config["method"]["residual_energy_share"])
        if residual_energy_share is None
        else float(residual_energy_share)
    )
    shedding = _shedding_wide(year, verification, config)
    rows: list[dict[str, Any]] = []
    for market in CANDIDATE_MARKETS:
        result = residual_energy_capacity(shedding[market].to_numpy(), share)
        rows.append(
            {
                "horizon": year,
                "residual_energy_share": share,
                "market": market,
                "derived_virtual_capacity_MW": result["capacity_MW"],
                "baseline_peak_shedding_MW": result["baseline_peak_shedding_MW"],
                "capacity_as_percent_of_peak": (
                    100.0 * result["capacity_MW"] / result["baseline_peak_shedding_MW"]
                    if result["baseline_peak_shedding_MW"]
                    else 0.0
                ),
                "baseline_shedding_MWh": result["baseline_shedding_MWh"],
                "mechanical_residual_shedding_MWh": result["verified_residual_MWh"],
                "mechanical_residual_energy_share": result["verified_residual_energy_share"],
                "active_segment_hours": result["active_segment_hours"],
                "positive_hours": result["positive_hours"],
            }
        )
    capacities = pd.DataFrame.from_records(rows)
    if share == float(config["method"]["residual_energy_share"]):
        expected = config["horizons"][year]["expected_capacities_MW"]
        for row in capacities.itertuples(index=False):
            if not np.isclose(
                float(row.derived_virtual_capacity_MW),
                float(expected[row.market]),
                atol=5e-9,
                rtol=0.0,
            ):
                raise RuntimeError(
                    f"R10_EXPECTED_CAPACITY_MISMATCH_{year}_{row.market}: "
                    f"expected={expected[row.market]}; observed={row.derived_virtual_capacity_MW}"
                )
        observed_total = float(capacities["derived_virtual_capacity_MW"].sum())
        if not np.isclose(
            observed_total,
            float(config["horizons"][year]["expected_total_capacity_MW"]),
            atol=1e-8,
            rtol=0.0,
        ):
            raise RuntimeError(f"R10_EXPECTED_TOTAL_CAPACITY_MISMATCH_{year}")
    return capacities, shedding


def recover_cost_proxy(year: int, config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_r10_config()
    authority = config["cost_authority"]
    source = ROOT / authority["source_path"]
    _require_hash(source, authority["source_sha256"], "R10_B4_COST_AUTHORITY")
    frame = pd.read_csv(source)
    selection = authority["selection"]
    selected = frame.loc[
        frame["country_code"].eq(selection["country_code"])
        & frame["year"].eq(year)
        & frame["scenario"].eq(selection["scenario"])
        & frame["source_static_class"].eq(selection["source_static_class"])
        & frame["source_carrier"].eq(selection["source_carrier"])
        & frame["CHP_flag"].map(_as_bool).eq(bool(selection["CHP_flag"]))
    ]
    if len(selected) != 1:
        raise RuntimeError(f"R10_OCGT_PROXY_NOT_UNIQUE_{year}: rows={len(selected)}")
    row = selected.iloc[0]
    expected = authority["horizons"][year]
    if row["asset_id"] != expected["source_asset_id"]:
        raise RuntimeError(f"R10_OCGT_PROXY_ASSET_MISMATCH_{year}")
    fields = (
        "efficiency_el",
        "fuel_price_EUR2025_per_MWh_th",
        "CO2_price_EUR2025_per_t",
        "chargeable_CO2_t_per_MWh_th",
        "VOM_EUR2025_per_MWh_el",
        "marginal_cost_EUR2025_per_MWh_el",
    )
    for field in fields:
        if not np.isclose(float(row[field]), float(expected[field]), atol=1e-12, rtol=0.0):
            raise RuntimeError(f"R10_OCGT_PROXY_VALUE_MISMATCH_{year}_{field}")
    efficiency = float(row["efficiency_el"])
    derived = (
        float(row["fuel_price_EUR2025_per_MWh_th"]) / efficiency
        + float(row["chargeable_CO2_t_per_MWh_th"])
        * float(row["CO2_price_EUR2025_per_t"])
        / efficiency
        + float(row["VOM_EUR2025_per_MWh_el"])
    )
    if not np.isclose(derived, float(row["marginal_cost_EUR2025_per_MWh_el"]), atol=1e-12, rtol=0.0):
        raise RuntimeError(f"R10_OCGT_PROXY_DERIVATION_MISMATCH_{year}")
    return {
        "horizon": year,
        "source_file": relative_path(source),
        "source_file_sha256": sha256_file(source),
        "source_asset_id": str(row["asset_id"]),
        "technology": str(row["source_static_class"]),
        "efficiency_el": efficiency,
        "fuel_price_EUR2025_per_MWh_th": float(row["fuel_price_EUR2025_per_MWh_th"]),
        "CO2_price_EUR2025_per_t": float(row["CO2_price_EUR2025_per_t"]),
        "chargeable_CO2_t_per_MWh_th": float(row["chargeable_CO2_t_per_MWh_th"]),
        "residual_CO2_t_per_MWh_th": float(row["residual_CO2_t_per_MWh_th"]),
        "VOM_EUR2025_per_MWh_el": float(row["VOM_EUR2025_per_MWh_el"]),
        "fuel_component_EUR2025_per_MWh_el": float(row["fuel_component_EUR2025_per_MWh_el"]),
        "carbon_component_EUR2025_per_MWh_el": float(row["carbon_component_EUR2025_per_MWh_el"]),
        "marginal_cost_EUR2025_per_MWh_el": float(row["marginal_cost_EUR2025_per_MWh_el"]),
        "derivation": "FUEL_DIVIDED_BY_EFFICIENCY_PLUS_CHARGEABLE_CO2_TIMES_CO2_PRICE_DIVIDED_BY_EFFICIENCY_PLUS_VOM",
        "parameter_authority": str(row["parameter_authority"]),
        "evidence_class": str(row["evidence_class"]),
        "quality": str(row["quality"]),
        "research_performed": False,
    }


def build_contract(
    year: int,
    config: dict[str, Any],
    verification: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    capacities, shedding = derive_horizon_capacities(year, config, verification)
    cost = recover_cost_proxy(year, config)
    horizon = config["horizons"][year]
    rows: list[dict[str, Any]] = []
    for record in capacities.itertuples(index=False):
        rows.append(
            {
                "generator_id": horizon["generator_ids"][record.market],
                "bus": record.market,
                "carrier": VIRTUAL_CARRIER,
                "p_nom_MW": float(record.derived_virtual_capacity_MW),
                "capacity_sizing_rule": config["method"]["capacity_sizing_rule"],
                "mechanical_baseline_residual_energy_share": float(
                    record.mechanical_residual_energy_share
                ),
                "p_nom_extendable": False,
                "committable": False,
                "p_min_pu": 0.0,
                "p_max_pu": 1.0,
                "efficiency": 1.0,
                "marginal_cost_EUR2025_per_MWh_el": cost[
                    "marginal_cost_EUR2025_per_MWh_el"
                ],
                "cost_source_asset_id": cost["source_asset_id"],
                "interpretation": config["method"]["interpretation"],
                "status": "CENTRAL_R10_EXPERIMENTAL_FIXED_VIRTUAL_EXTERNAL_SUPPLY",
            }
        )
    contract = pd.DataFrame.from_records(rows)
    if tuple(contract["bus"]) != CANDIDATE_MARKETS or len(contract) != 3:
        raise RuntimeError(f"R10_CONTRACT_SCOPE_FAILURE_{year}")
    if set(contract["bus"]) & set(config["method"]["excluded_default_markets"]):
        raise RuntimeError(f"R10_EXCLUDED_MARKET_VIRTUAL_SUPPLY_{year}")
    return contract, cost, shedding


def add_r10_virtual_supply(
    baseline: pypsa.Network,
    contract: pd.DataFrame,
    year: int,
) -> pypsa.Network:
    network = baseline.copy()
    if VIRTUAL_CARRIER in network.carriers.index:
        raise RuntimeError(f"R10_VIRTUAL_CARRIER_ALREADY_PRESENT_{year}")
    expected_ids = set(contract["generator_id"])
    if set(network.generators.index) & expected_ids:
        raise RuntimeError(f"R10_VIRTUAL_GENERATOR_ALREADY_PRESENT_{year}")
    network.add(
        "Carrier",
        VIRTUAL_CARRIER,
        co2_emissions=0.0,
        nice_name="R10 residual firm external virtual supply",
    )
    for row in contract.itertuples(index=False):
        network.add(
            "Generator",
            row.generator_id,
            bus=row.bus,
            carrier=row.carrier,
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
        "auxiliary_experiment": f"R10_PERIMETER_CLOSURE_{year}",
        "method": "PERIMETER_CLOSURE_METHOD_V2_0",
        "mechanical_baseline_residual_energy_share": 0.10,
        "production_methodology_status": "PENDING_SOL_REVIEW",
    }
    return network


def validate_r10_structural_delta(
    baseline: pypsa.Network,
    network: pypsa.Network,
    contract: pd.DataFrame,
    year: int,
) -> dict[str, Any]:
    new_ids = set(contract["generator_id"])
    if not baseline.snapshots.equals(network.snapshots):
        raise RuntimeError(f"R10_SNAPSHOT_DRIFT_{year}")
    _assert_frame_equal(baseline.snapshot_weightings, network.snapshot_weightings, f"R10_{year}_WEIGHTS")
    for component in ("buses", "loads", "links", "stores"):
        _assert_frame_equal(
            getattr(baseline, component),
            getattr(network, component),
            f"R10_{year}_{component.upper()}",
        )
    if set(network.carriers.index) != set(baseline.carriers.index) | {VIRTUAL_CARRIER}:
        raise RuntimeError(f"R10_CARRIER_DELTA_NOT_EXACT_{year}")
    _assert_frame_equal(
        baseline.carriers,
        network.carriers.loc[baseline.carriers.index, baseline.carriers.columns],
        f"R10_{year}_EXISTING_CARRIERS",
    )
    if set(network.generators.index) != set(baseline.generators.index) | new_ids:
        raise RuntimeError(f"R10_GENERATOR_DELTA_NOT_EXACT_{year}")
    if set(network.generators.columns) != set(baseline.generators.columns):
        raise RuntimeError(f"R10_GENERATOR_SCHEMA_DRIFT_{year}")
    _assert_frame_equal(
        baseline.generators,
        network.generators.loc[baseline.generators.index, baseline.generators.columns],
        f"R10_{year}_EXISTING_GENERATORS",
    )
    for holder_name in ("buses_t", "loads_t", "links_t", "stores_t"):
        left = _dynamic_tables(getattr(baseline, holder_name))
        right = _dynamic_tables(getattr(network, holder_name))
        if set(left) != set(right):
            raise RuntimeError(f"R10_DYNAMIC_SCHEMA_DRIFT_{year}_{holder_name}")
        for attribute in left:
            _assert_frame_equal(left[attribute], right[attribute], f"R10_{year}_{holder_name}_{attribute}")
    left_generators = _dynamic_tables(baseline.generators_t)
    right_generators = _dynamic_tables(network.generators_t)
    if set(left_generators) != set(right_generators):
        raise RuntimeError(f"R10_GENERATOR_DYNAMIC_SCHEMA_DRIFT_{year}")
    for attribute, base_frame in left_generators.items():
        observed = right_generators[attribute]
        unexpected = set(observed.columns) - set(base_frame.columns) - new_ids
        if unexpected:
            raise RuntimeError(f"R10_UNEXPECTED_DYNAMIC_COLUMNS_{year}_{attribute}: {sorted(unexpected)}")
        _assert_frame_equal(
            base_frame,
            observed.loc[:, base_frame.columns],
            f"R10_{year}_EXISTING_GENERATOR_DYNAMIC_{attribute}",
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
            raise RuntimeError(f"R10_VIRTUAL_GENERATOR_PARAMETER_FAILURE_{year}_{row.generator_id}")
    baseline_counts = _component_counts(baseline)
    observed_counts = _component_counts(network)
    expected_counts = dict(baseline_counts)
    expected_counts["carriers"] += 1
    expected_counts["generators"] += 3
    if observed_counts != expected_counts:
        raise RuntimeError(f"R10_COMPONENT_COUNT_DRIFT_{year}")
    if set(network.buses.index[network.buses.carrier.eq("AC_STAGE_A_MARKET")]) != EXPECTED_MARKET_SET:
        raise RuntimeError(f"R10_MARKET_BUS_SCOPE_FAILURE_{year}")
    return {
        "status": "PASS",
        "year": year,
        "baseline_counts": baseline_counts,
        "R10_counts": observed_counts,
        "allowed_delta": {"carriers": 1, "generators": 3},
        "new_generators": list(contract["generator_id"]),
        "candidate_markets": list(contract["bus"]),
        "excluded_market_virtual_supply_count": 0,
        "baseline_component_preservation": "EXACT",
    }


def prepare_horizon(
    year: int,
    config: dict[str, Any],
    verification: dict[str, Any],
    *,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    contract, cost, shedding = build_contract(year, config, verification)
    baseline = pypsa.Network(verification["networks"][year]["path"])
    if int(baseline.meta.get("horizon", year)) != year or len(baseline.snapshots) != 8760:
        raise RuntimeError(f"R10_B6_NETWORK_HORIZON_OR_CHRONOLOGY_FAILURE_{year}")
    network = add_r10_virtual_supply(baseline, contract, year)
    structural = validate_r10_structural_delta(baseline, network, contract, year)
    static_lp = validate_lp_static(network)
    short = network.copy()
    short.set_snapshots(short.snapshots[:6])
    linopy = create_and_validate_linopy_model(short)
    payload = {
        "schema_version": f"MEM_R10_{year}_PREPARATION_QA_V1_0",
        "status": "PASS",
        "method": "PERIMETER_CLOSURE_METHOD_V2_0",
        "mechanical_baseline_residual_energy_share": 0.10,
        "structural_delta": structural,
        "static_LP": static_lp,
        "short_unsolved_LP_fixture": {"snapshots": 6, **linopy},
        "production_optimization_executed": False,
        "automatic_method_acceptance": False,
        "B10_authorized": False,
        "Stage_B_authorized": False,
    }
    paths: list[Path] = []
    if write_artifacts:
        qa_dir = ROOT / config["horizons"][year]["qa_directory"]
        qa_dir.mkdir(parents=True, exist_ok=True)
        contract_path = qa_dir / f"MEM_R10_{year}_Virtual_Supply_Contract_v1.0.csv"
        cost_path = qa_dir / f"MEM_R10_{year}_Cost_Provenance_v1.0.json"
        qa_path = qa_dir / f"MEM_R10_{year}_Preparation_QA_v1.0.json"
        contract.to_csv(contract_path, index=False, encoding="utf-8", lineterminator="\n")
        _write_json(cost_path, {"schema_version": f"MEM_R10_{year}_COST_PROVENANCE_V1_0", **cost})
        _write_json(qa_path, payload)
        paths = [contract_path, cost_path, qa_path]
    return {
        "year": year,
        "baseline": baseline,
        "network": network,
        "contract": contract,
        "cost": cost,
        "baseline_shedding": shedding,
        "structural": structural,
        "LP": linopy,
        "artifacts": paths,
        "payload": payload,
    }


def write_sensitivity_table(config: dict[str, Any], verification: dict[str, Any]) -> Path:
    frames: list[pd.DataFrame] = []
    for year in (2040, 2050):
        for share in SENSITIVITY_SHARES:
            frame, _ = derive_horizon_capacities(
                year,
                config,
                verification,
                residual_energy_share=share,
            )
            frames.append(
                frame[
                    [
                        "horizon",
                        "residual_energy_share",
                        "market",
                        "derived_virtual_capacity_MW",
                        "baseline_peak_shedding_MW",
                        "capacity_as_percent_of_peak",
                        "baseline_shedding_MWh",
                    ]
                ]
            )
    output = ROOT / config["shared_artifacts"]["sensitivity_table"]
    output.parent.mkdir(parents=True, exist_ok=True)
    pd.concat(frames, ignore_index=True).to_csv(
        output,
        index=False,
        encoding="utf-8",
        lineterminator="\n",
    )
    return output


def write_historical_s2_audit_manifest(
    config: dict[str, Any],
    verification: dict[str, Any],
) -> Path:
    s2 = verification["history"]["b8c_s2_max_closure"]
    controls = [
        ROOT / "qa/stage_a/etx7b8c/MEM_ETX7B8C_S2_Virtual_Supply_Contract_v1.0.csv",
        ROOT / "qa/stage_a/etx7b8c/MEM_ETX7B8C_S2_Cost_Provenance_v1.0.json",
        ROOT / "qa/stage_a/etx7b8c/MEM_ETX7B8C_S2_Structural_QA_v1.0.json",
        ROOT / "qa/stage_a/etx7b8c/MEM_ETX7B8C_B8_vs_S2_System_Comparison_v1.0.csv",
        ROOT / "qa/stage_a/etx7b8c/MEM_ETX7B8C_B8_vs_S2_Market_Comparison_v1.0.csv",
        ROOT / "qa/stage_a/etx7b8c/MEM_ETX7B8C_S2_Virtual_Supply_Utilization_v1.0.csv",
        ROOT / "qa/stage_a/etx7b8c/MEM_ETX7B8C_B8_vs_S2_Flow_Comparison_v1.0.csv",
        ROOT / "qa/stage_a/etx7b8c/MEM_ETX7B8C_B8_vs_S2_Comparison_v1.0.json",
    ]
    rows: list[dict[str, Any]] = []

    def add(category: str, path: Path, expected_hash: str | None = None) -> None:
        if not path.exists():
            raise FileNotFoundError(path)
        observed = sha256_file(path)
        if expected_hash is not None and observed != str(expected_hash):
            raise RuntimeError(f"S2_AUDIT_HASH_MISMATCH: {path}")
        rows.append(
            {
                "category": category,
                "relative_path": relative_path(path),
                "bytes": path.stat().st_size,
                "rows": _row_count(path),
                "sha256": observed,
                "methodological_role": "ACCEPTED_DIAGNOSTIC_MAX_CLOSURE_EXPERIMENT",
                "production_price_source": False,
                "status": "FROZEN_VERIFIED",
            }
        )

    add("S2_RUN_RECEIPT", s2["receipt_path"], s2["receipt_sha256"])
    manifest_path = ROOT / s2["manifest"]["path"]
    add("S2_RESULT_MANIFEST", manifest_path, s2["manifest"]["sha256"])
    for member in s2["manifest"]["members"]:
        add("S2_RESULT_MEMBER", ROOT / str(member["relative_path"]), str(member["sha256"]))
    for path in controls:
        add("S2_CONTROL_ARTIFACT", path)
    output = ROOT / config["shared_artifacts"]["historical_s2_audit_manifest"]
    output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame.from_records(rows).sort_values(
        ["category", "relative_path"], kind="stable"
    ).to_csv(output, index=False, encoding="utf-8", lineterminator="\n")
    return output


def write_preparation_closeout(
    config: dict[str, Any],
    verification: dict[str, Any],
    prepared_by_year: dict[int, dict[str, Any]],
    sensitivity: Path,
    s2_audit: Path,
) -> tuple[Path, Path]:
    artifacts = [
        CONFIG_PATH,
        ROOT / "config/stage_a_perimeter_closure_method_v1_0.yaml",
        ROOT / "config/stage_a_production_price_sources.yaml",
        ROOT / "config/stage_b_price_input_contract.yaml",
        ROOT / "config/approval_gates.yaml",
        ROOT / "src/mem_model/stage_a/perimeter_closure_r10.py",
        ROOT / "src/mem_model/stage_a/execution.py",
        ROOT / "docs/MEM_STAGE_A_CURRENT_STATE.md",
        ROOT / "docs/runbooks/ETX7B8D_RUNBOOK.md",
        ROOT / "docs/runbooks/ETX7B9C_RUNBOOK.md",
        ROOT / "docs/runbooks/ETX7B10_RUNBOOK.md",
        ROOT / "docs/runbooks/STAGE_B_INPUT_TRANSFER_RUNBOOK.md",
        sensitivity,
        s2_audit,
    ]
    for prepared in prepared_by_year.values():
        artifacts.extend(prepared["artifacts"])
    manifest_path = ROOT / config["shared_artifacts"]["preparation_manifest"]
    manifest = write_manifest(manifest_path, artifacts)
    final_path = ROOT / config["shared_artifacts"]["final_verification"]
    payload = {
        "schema_version": "MEM_R10_PERIMETER_CLOSURE_PREPARATION_FINAL_VERIFICATION_V1_0",
        "status": "PASS",
        "method": "PERIMETER_CLOSURE_METHOD_V2_0",
        "central_residual_energy_share": 0.10,
        "preparation_manifest": relative_path(manifest_path),
        "preparation_manifest_sha256": sha256_file(manifest_path),
        "preparation_manifest_members": len(manifest),
        "immutable_history": {
            key: {
                "receipt_sha256": value["receipt_sha256"],
                "result_manifest_sha256": value["manifest"]["sha256"],
                "result_manifest_members": value["manifest"]["member_count"],
                "member_failures": value["manifest"]["member_failures"],
            }
            for key, value in verification["history"].items()
        },
        "accepted_b6_networks": {
            str(year): {
                "path": record["relative_path"],
                "sha256": record["sha256"],
            }
            for year, record in verification["networks"].items()
        },
        "horizons": {
            str(year): {
                "status": "PREPARED_NOT_EXECUTED",
                "capacities_MW": prepared["contract"].set_index("bus")["p_nom_MW"].to_dict(),
                "marginal_cost_EUR2025_per_MWh_el": prepared["cost"][
                    "marginal_cost_EUR2025_per_MWh_el"
                ],
                "structural_status": prepared["structural"]["status"],
                "allowed_delta": prepared["structural"]["allowed_delta"],
                "linopy_integer_variables": prepared["LP"]["integer_variables"],
                "linopy_binary_variables": prepared["LP"]["binary_variables"],
                "manual_command": config["horizons"][year]["command"],
            }
            for year, prepared in prepared_by_year.items()
        },
        "sensitivity_rows": _row_count(sensitivity),
        "sensitivity_sha256": sha256_file(sensitivity),
        "historical_s2_audit_rows": _row_count(s2_audit),
        "historical_s2_audit_sha256": sha256_file(s2_audit),
        "production_optimizations_executed": False,
        "production_sources_resolved": False,
        "B10_authorized": False,
        "Stage_B_authorized": False,
        "research_performed": False,
    }
    _write_json(final_path, payload)
    return manifest_path, final_path


def prepare_all() -> dict[str, Any]:
    config = load_r10_config()
    before = verify_immutable_history(config)
    sensitivity = write_sensitivity_table(config, before)
    s2_audit = write_historical_s2_audit_manifest(config, before)
    horizons: dict[str, Any] = {}
    prepared_by_year: dict[int, dict[str, Any]] = {}
    for year in (2040, 2050):
        prepared = prepare_horizon(year, config, before, write_artifacts=True)
        prepared_by_year[year] = prepared
        horizons[str(year)] = {
            "capacities_MW": prepared["contract"].set_index("bus")["p_nom_MW"].to_dict(),
            "marginal_cost_EUR2025_per_MWh_el": prepared["cost"][
                "marginal_cost_EUR2025_per_MWh_el"
            ],
            "structural": prepared["structural"],
            "LP": prepared["LP"],
            "artifacts": [relative_path(path) for path in prepared["artifacts"]],
            "manual_command": config["horizons"][year]["command"],
        }
    after = verify_immutable_history(config)
    before_identity = {
        key: (
            value["receipt_sha256"],
            value["manifest"]["sha256"],
            value["manifest"]["member_count"],
        )
        for key, value in before["history"].items()
    }
    after_identity = {
        key: (
            value["receipt_sha256"],
            value["manifest"]["sha256"],
            value["manifest"]["member_count"],
        )
        for key, value in after["history"].items()
    }
    if before_identity != after_identity:
        raise RuntimeError("R10_IMMUTABLE_HISTORY_CHANGED_DURING_PREPARATION")
    preparation_manifest, final_verification = write_preparation_closeout(
        config,
        after,
        prepared_by_year,
        sensitivity,
        s2_audit,
    )
    return {
        "status": "PASS",
        "method": "PERIMETER_CLOSURE_METHOD_V2_0",
        "central_residual_energy_share": 0.10,
        "horizons": horizons,
        "sensitivity_table": relative_path(sensitivity),
        "historical_s2_audit_manifest": relative_path(s2_audit),
        "preparation_manifest": relative_path(preparation_manifest),
        "final_verification": relative_path(final_verification),
        "production_optimizations_executed": False,
        "B10_authorized": False,
        "Stage_B_authorized": False,
    }


def _weighted_total(frame: pd.DataFrame, column: str, weights: pd.Series) -> float:
    mapped = frame["snapshot"].map(weights)
    if mapped.isna().any():
        raise RuntimeError(f"R10_RESULT_TIMESTAMP_MISMATCH_{column}")
    return float((frame[column].astype(float) * mapped).sum())


def _system_metrics_from_outputs(
    network: pypsa.Network,
    paths: dict[str, Path],
    voll: float,
    objective: float,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Series]:
    load, shedding, prices, weights = _normalize_market_tables(
        network, paths["shedding"], paths["prices"]
    )
    total_load = float(load.mul(weights, axis=0).sum().sum())
    total_shedding = float(shedding.mul(weights, axis=0).sum().sum())
    simultaneous = shedding.sum(axis=1)
    price = _price_statistics(prices.stack(future_stack=True), voll)
    curtailment = pd.read_parquet(paths["curtailment"])
    spill = pd.read_parquet(paths["spill"])
    metrics = {
        "total_load_MWh": total_load,
        "total_shedding_MWh": total_shedding,
        "shedding_percent_of_load": 100.0 * total_shedding / total_load,
        "system_shedding_hours_above_1e_6_MW": int(
            simultaneous.gt(SHEDDING_TOLERANCE_MW).sum()
        ),
        "system_shedding_hours_above_1_MW": int(simultaneous.gt(1.0).sum()),
        "maximum_simultaneous_shedding_MW": float(simultaneous.max()),
        "objective_EUR2025": float(objective),
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
    return metrics, load, shedding, prices, weights


def _result_paths(result_dir: Path, stem: str) -> dict[str, Path]:
    return {
        "solved": result_dir / f"{stem}_SOLVED.nc",
        "prices": result_dir / f"{stem}_Market_Prices.parquet",
        "dispatch": result_dir / f"{stem}_Generator_Dispatch.parquet",
        "states": result_dir / f"{stem}_Store_State.parquet",
        "flows": result_dir / f"{stem}_Link_Flows.parquet",
        "shedding": result_dir / f"{stem}_Load_Shedding.parquet",
        "spill": result_dir / f"{stem}_Hydro_Spill.parquet",
        "curtailment": result_dir / f"{stem}_VRE_Curtailment.parquet",
    }


def _baseline_result_paths(receipt: dict[str, Any]) -> dict[str, Path]:
    return {
        "solved": _output_path(receipt, "_SOLVED"),
        "prices": _output_path(receipt, "_Market_Prices"),
        "dispatch": _output_path(receipt, "_Generator_Dispatch"),
        "states": _output_path(receipt, "_Store_State"),
        "flows": _output_path(receipt, "_Link_Flows"),
        "shedding": _output_path(receipt, "_Load_Shedding"),
        "spill": _output_path(receipt, "_Hydro_Spill"),
        "curtailment": _output_path(receipt, "_VRE_Curtailment"),
    }


def _baseline_distribution(
    load: pd.DataFrame,
    shedding: pd.DataFrame,
    capacities: dict[str, float],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for market in MARKETS:
        series = shedding[market].clip(lower=0.0)
        hours = series.gt(1.0)
        peak_snapshot = series.idxmax()
        peak = float(series.loc[peak_snapshot])
        same_hour_load = float(load.at[peak_snapshot, market])
        candidate = market in capacities
        rows.append(
            {
                "market": market,
                "closure_candidate": candidate,
                "baseline_peak_shedding_MW": peak,
                "average_shedding_MW_during_hours_above_1_MW": (
                    float(series.loc[hours].mean()) if hours.any() else 0.0
                ),
                "average_local_demand_MW_during_hours_above_1_MW": (
                    float(load.loc[hours, market].mean()) if hours.any() else 0.0
                ),
                "local_demand_MW_at_peak_shedding_hour": same_hour_load,
                "peak_shedding_percent_of_same_hour_demand": (
                    100.0 * peak / same_hour_load if same_hour_load else 0.0
                ),
                "baseline_shedding_hours_above_1_MW": int(hours.sum()),
                "implemented_R10_capacity_MW": float(capacities.get(market, 0.0)),
                "implemented_capacity_percent_of_baseline_peak": (
                    100.0 * float(capacities.get(market, 0.0)) / peak if peak else 0.0
                ),
            }
        )
    return pd.DataFrame.from_records(rows)


def _flow_wide(network: pypsa.Network, path: Path) -> pd.DataFrame:
    interfaces = pd.read_csv(INTERFACE_PATH)
    frame = pd.read_parquet(path).copy()
    name = _component_name_column(frame)
    mapping = {
        f"INTERCONNECTOR_{_safe_id(link_id)}": link_id
        for link_id in interfaces["physical_link_id"]
    }
    frame = frame.loc[frame[name].isin(mapping)].copy()
    frame["physical_link_id"] = frame[name].map(mapping)
    wide = frame.pivot(index="snapshot", columns="physical_link_id", values="signed_p0_MW").reindex(
        index=network.snapshots,
        columns=interfaces["physical_link_id"],
    )
    if wide.isna().any().any():
        raise RuntimeError("R10_INTERFACE_FLOW_TABLE_INCOMPLETE")
    return wide


def _directional_limit_for_year(
    directional: pd.DataFrame,
    physical_link_id: str,
    origin: str,
    destination: str,
    year: int,
) -> float:
    selected = directional.loc[
        directional["physical_link_id"].eq(physical_link_id)
        & directional["from_market"].eq(origin)
        & directional["to_market"].eq(destination)
        & directional["year"].eq(year)
        & directional["scenario"].eq("Base")
    ]
    if len(selected) != 1:
        raise RuntimeError(
            f"R10_DIRECTIONAL_LIMIT_NOT_UNIQUE_{year}: "
            f"{physical_link_id} {origin}->{destination}; rows={len(selected)}"
        )
    return float(selected.iloc[0]["capacity_MW"])


def _interface_effects(
    year: int,
    network: pypsa.Network,
    baseline_flows_path: Path,
    closure_flows_path: Path,
    baseline_shedding: pd.DataFrame,
    closure_shedding: pd.DataFrame,
) -> pd.DataFrame:
    interfaces = pd.read_csv(INTERFACE_PATH)
    directional = pd.read_csv(LINK_PATH)
    baseline = _flow_wide(network, baseline_flows_path)
    closure = _flow_wide(network, closure_flows_path)
    weights = network.snapshot_weightings.objective.astype(float)
    baseline_scarcity = baseline_shedding.sum(axis=1).gt(SHEDDING_TOLERANCE_MW)
    closure_scarcity = closure_shedding.sum(axis=1).gt(SHEDDING_TOLERANCE_MW)
    rows: list[dict[str, Any]] = []
    for interface in interfaces.sort_values("physical_link_id", kind="stable").itertuples():
        link_id = interface.physical_link_id
        a_to_b = _directional_limit_for_year(
            directional, link_id, interface.endpoint_a, interface.endpoint_b, year
        )
        b_to_a = _directional_limit_for_year(
            directional, link_id, interface.endpoint_b, interface.endpoint_a, year
        )
        b = baseline[link_id].astype(float)
        c = closure[link_id].astype(float)
        b_ab = b.ge(a_to_b - INTERFACE_LIMIT_TOLERANCE_MW)
        b_ba = (-b).ge(b_to_a - INTERFACE_LIMIT_TOLERANCE_MW)
        c_ab = c.ge(a_to_b - INTERFACE_LIMIT_TOLERANCE_MW)
        c_ba = (-c).ge(b_to_a - INTERFACE_LIMIT_TOLERANCE_MW)
        rows.append(
            {
                "physical_link_id": link_id,
                "endpoint_a": interface.endpoint_a,
                "endpoint_b": interface.endpoint_b,
                "accepted_a_to_b_limit_MW": a_to_b,
                "accepted_b_to_a_limit_MW": b_to_a,
                "baseline_annual_net_a_to_b_MWh": float((b * weights).sum()),
                "closure_annual_net_a_to_b_MWh": float((c * weights).sum()),
                "change_annual_net_a_to_b_MWh": float(((c - b) * weights).sum()),
                "baseline_gross_a_to_b_MWh": float((b.clip(lower=0.0) * weights).sum()),
                "closure_gross_a_to_b_MWh": float((c.clip(lower=0.0) * weights).sum()),
                "baseline_gross_b_to_a_MWh": float(((-b).clip(lower=0.0) * weights).sum()),
                "closure_gross_b_to_a_MWh": float(((-c).clip(lower=0.0) * weights).sum()),
                "baseline_congestion_hours_a_to_b": int(b_ab.sum()),
                "closure_congestion_hours_a_to_b": int(c_ab.sum()),
                "change_congestion_hours_a_to_b": int(c_ab.sum() - b_ab.sum()),
                "baseline_congestion_hours_b_to_a": int(b_ba.sum()),
                "closure_congestion_hours_b_to_a": int(c_ba.sum()),
                "change_congestion_hours_b_to_a": int(c_ba.sum() - b_ba.sum()),
                "baseline_a_to_b_congestion_during_baseline_scarcity_hours": int(
                    (b_ab & baseline_scarcity).sum()
                ),
                "closure_a_to_b_congestion_during_baseline_scarcity_hours": int(
                    (c_ab & baseline_scarcity).sum()
                ),
                "baseline_b_to_a_congestion_during_baseline_scarcity_hours": int(
                    (b_ba & baseline_scarcity).sum()
                ),
                "closure_b_to_a_congestion_during_baseline_scarcity_hours": int(
                    (c_ba & baseline_scarcity).sum()
                ),
                "closure_a_to_b_congestion_during_residual_scarcity_hours": int(
                    (c_ab & closure_scarcity).sum()
                ),
                "closure_b_to_a_congestion_during_residual_scarcity_hours": int(
                    (c_ba & closure_scarcity).sum()
                ),
            }
        )
    return pd.DataFrame.from_records(rows)


def write_post_solve_comparisons(
    year: int,
    network: pypsa.Network,
    config: dict[str, Any],
    verification: dict[str, Any],
    contract: pd.DataFrame,
) -> tuple[list[Path], dict[str, Any]]:
    horizon = config["horizons"][year]
    result_dir = ROOT / horizon["result_directory"]
    stem = horizon["result_stem"]
    closure_paths = _result_paths(result_dir, stem)
    baseline_receipt = _baseline_receipt(year, verification, config)
    baseline_paths = _baseline_result_paths(baseline_receipt)
    baseline_network = pypsa.Network(baseline_paths["solved"])
    voll = float(load_execution_config()["assembly"]["feasibility"]["VOLL_EUR_per_MWh"])
    baseline_system, baseline_load, baseline_shedding, baseline_prices, weights = _system_metrics_from_outputs(
        baseline_network,
        baseline_paths,
        voll,
        float(baseline_receipt["objective"]),
    )
    closure_system, closure_load, closure_shedding, closure_prices, closure_weights = _system_metrics_from_outputs(
        network,
        closure_paths,
        voll,
        float(network.objective),
    )
    if not np.allclose(baseline_load, closure_load, atol=0.0, rtol=0.0):
        raise RuntimeError(f"R10_POST_SOLVE_LOAD_DRIFT_{year}")
    system = pd.DataFrame.from_records(
        [
            {
                "metric": metric,
                "bounded_baseline": baseline_system[metric],
                "R10_closure": closure_system[metric],
                "R10_minus_baseline": float(closure_system[metric]) - float(baseline_system[metric]),
            }
            for metric in baseline_system
        ]
    )
    baseline_market = _build_market_diagnostic(
        baseline_load, baseline_shedding, baseline_prices, weights, voll
    )
    closure_market = _build_market_diagnostic(
        closure_load, closure_shedding, closure_prices, closure_weights, voll
    )
    market = baseline_market.merge(
        closure_market,
        on=["market", "annual_load_MWh"],
        suffixes=("_baseline", "_closure"),
        validate="one_to_one",
    )
    for column in baseline_market.columns:
        if column not in {"market", "annual_load_MWh"}:
            market[f"{column}_closure_minus_baseline"] = (
                market[f"{column}_closure"] - market[f"{column}_baseline"]
            )

    dispatch_long = pd.read_parquet(closure_paths["dispatch"])
    dispatch_name = _component_name_column(dispatch_long)
    generator_ids = list(contract["generator_id"])
    dispatch = dispatch_long.pivot(
        index="snapshot", columns=dispatch_name, values="dispatch_MW"
    ).reindex(index=network.snapshots, columns=generator_ids)
    utilization_rows: list[dict[str, Any]] = []
    for row in contract.itertuples(index=False):
        series = dispatch[row.generator_id].astype(float)
        annual = float((series * closure_weights).sum())
        p_nom = float(row.p_nom_MW)
        baseline_scarcity = baseline_shedding[row.bus].gt(SHEDDING_TOLERANCE_MW)
        local_load = float((closure_load[row.bus] * closure_weights).sum())
        utilization_rows.append(
            {
                "market": row.bus,
                "generator_id": row.generator_id,
                "p_nom_MW": p_nom,
                "marginal_cost_EUR2025_per_MWh_el": float(row.marginal_cost_EUR2025_per_MWh_el),
                "annual_dispatch_MWh": annual,
                "peak_dispatch_MW": float(series.max()),
                "annual_capacity_factor": annual / (p_nom * float(closure_weights.sum())),
                "hours_dispatch_above_1_MW": int(series.gt(1.0).sum()),
                "hours_dispatch_above_10_percent_p_nom": int(series.gt(0.1 * p_nom).sum()),
                "hours_dispatch_above_50_percent_p_nom": int(series.gt(0.5 * p_nom).sum()),
                "hours_dispatch_above_90_percent_p_nom": int(series.gt(0.9 * p_nom).sum()),
                "share_local_annual_load_supplied_percent": 100.0 * annual / local_load,
                "share_dispatch_during_baseline_local_shedding_hours_percent": (
                    100.0
                    * float((series.loc[baseline_scarcity] * closure_weights.loc[baseline_scarcity]).sum())
                    / annual
                    if annual
                    else 0.0
                ),
                "hours_local_price_approximately_virtual_MC": int(
                    np.isclose(
                        closure_prices[row.bus].to_numpy(),
                        float(row.marginal_cost_EUR2025_per_MWh_el),
                        atol=1e-6,
                        rtol=0.0,
                    ).sum()
                ),
                "residual_local_shedding_MWh": float(
                    (closure_shedding[row.bus] * closure_weights).sum()
                ),
                "residual_local_shedding_hours": int(
                    closure_shedding[row.bus].gt(SHEDDING_TOLERANCE_MW).sum()
                ),
            }
        )
    utilization = pd.DataFrame.from_records(utilization_rows)
    capacity_map = contract.set_index("bus")["p_nom_MW"].astype(float).to_dict()
    distribution = _baseline_distribution(baseline_load, baseline_shedding, capacity_map)
    interfaces = _interface_effects(
        year,
        network,
        baseline_paths["flows"],
        closure_paths["flows"],
        baseline_shedding,
        closure_shedding,
    )
    baseline_by_market = baseline_market.set_index("market")
    closure_by_market = closure_market.set_index("market")
    baseline_shed = baseline_by_market["load_shedding_MWh"]
    closure_shed = closure_by_market["load_shedding_MWh"]
    optimized_share = (
        closure_system["total_shedding_MWh"] / baseline_system["total_shedding_MWh"]
        if baseline_system["total_shedding_MWh"]
        else 0.0
    )
    movement = {
        "schema_version": f"MEM_R10_{year}_SCARCITY_MOVEMENT_V1_0",
        "status": "FACTUAL_DIAGNOSTIC_NO_AUTOMATIC_ECONOMIC_ACCEPTANCE",
        "method": "PERIMETER_CLOSURE_METHOD_V2_0",
        "MECHANICAL_BASELINE_RESIDUAL_ENERGY_SHARE": 0.10,
        "OPTIMIZED_RESIDUAL_SHEDDING_SHARE": optimized_share,
        "markets_with_reduced_shedding": [
            market_name
            for market_name in MARKETS
            if closure_shed[market_name] < baseline_shed[market_name] - 1e-6
        ],
        "markets_with_increased_shedding": [
            market_name
            for market_name in MARKETS
            if closure_shed[market_name] > baseline_shed[market_name] + 1e-6
        ],
        "newly_shedding_markets": [
            market_name
            for market_name in MARKETS
            if baseline_shed[market_name] <= 1e-6 and closure_shed[market_name] > 1e-6
        ],
        "markets_where_shedding_disappears": [
            market_name
            for market_name in MARKETS
            if baseline_shed[market_name] > 1e-6 and closure_shed[market_name] <= 1e-6
        ],
        "IT_CH_AT_SI_mean_price_changes_EUR_per_MWh": {
            market_name: float(
                closure_by_market.at[market_name, "price_mean_EUR_per_MWh"]
                - baseline_by_market.at[market_name, "price_mean_EUR_per_MWh"]
            )
            for market_name in ("IT", "CH", "AT", "SI")
        },
        "residual_shedding_MWh_by_market": {
            market_name: float(closure_shed[market_name]) for market_name in MARKETS
        },
        "automatic_method_acceptance": False,
        "production_price_source_status": "PENDING_SOL_REVIEW",
        "B10_authorized": False,
        "Stage_B_authorized": False,
    }
    qa_dir = ROOT / horizon["qa_directory"]
    qa_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "system": qa_dir / f"MEM_R10_{year}_Bounded_vs_Closure_System_Comparison_v1.0.csv",
        "market": qa_dir / f"MEM_R10_{year}_Bounded_vs_Closure_Market_Comparison_v1.0.csv",
        "utilization": qa_dir / f"MEM_R10_{year}_Virtual_Supply_Utilization_v1.0.csv",
        "distribution": qa_dir / f"MEM_R10_{year}_Baseline_Distribution_Diagnostic_v1.0.csv",
        "interfaces": qa_dir / f"MEM_R10_{year}_Interface_Effects_v1.0.csv",
        "movement": qa_dir / f"MEM_R10_{year}_Scarcity_Movement_v1.0.json",
    }
    system.to_csv(outputs["system"], index=False, encoding="utf-8", lineterminator="\n")
    market.to_csv(outputs["market"], index=False, encoding="utf-8", lineterminator="\n")
    utilization.to_csv(outputs["utilization"], index=False, encoding="utf-8", lineterminator="\n")
    distribution.to_csv(outputs["distribution"], index=False, encoding="utf-8", lineterminator="\n")
    interfaces.to_csv(outputs["interfaces"], index=False, encoding="utf-8", lineterminator="\n")
    _write_json(outputs["movement"], movement)
    return list(outputs.values()), {
        "baseline_system": baseline_system,
        "closure_system": closure_system,
        "MECHANICAL_BASELINE_RESIDUAL_ENERGY_SHARE": 0.10,
        "OPTIMIZED_RESIDUAL_SHEDDING_SHARE": optimized_share,
        "scarcity_movement": movement,
    }


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def run_horizon(year: int, args: argparse.Namespace) -> dict[str, Any]:
    if not args.execute:
        raise SystemExit("R10_NOT_EXECUTED: rerun the exact prepared command with --execute")
    config = load_r10_config()
    horizon = config["horizons"][year]
    execution_config = load_execution_config()
    if config["solver"]["name"] != execution_config["solver"]["name"] or config["solver"]["options"] != execution_config["solver"]["options"]:
        raise RuntimeError("R10_SOLVER_CONFIG_DRIFT")
    started = time.perf_counter()
    qa_dir = ROOT / horizon["qa_directory"]
    log = qa_dir / "logs" / f"MEM_{horizon['phase'].replace('-', '')}_raw.log"
    _append_log(log, f"{horizon['phase']} R10 full-year experiment started; production acceptance remains pending.")
    preflight = gurobi_preflight(execution_config)
    verification = verify_immutable_history(config)
    prepared = prepare_horizon(year, config, verification, write_artifacts=True)
    network = prepared["network"]
    linopy = create_and_validate_linopy_model(network)
    gurobi_lp = _assert_gurobi_lp(network)
    _append_log(log, f"LP assertions PASS: {json.dumps({**linopy, **gurobi_lp}, sort_keys=True)}")
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
        raise RuntimeError(f"R10_POST_SOLVE_QA_FAILED_{year}: {metrics}")
    for row in prepared["contract"].itertuples(index=False):
        dispatch = network.generators_t.p[row.generator_id].astype(float)
        if dispatch.min() < -1e-5 or dispatch.max() > float(row.p_nom_MW) + 1e-5:
            raise RuntimeError(f"R10_VIRTUAL_DISPATCH_BOUND_FAILURE_{year}_{row.generator_id}")
    result_dir = ROOT / horizon["result_directory"]
    result_artifacts, outputs = _write_solve_outputs(network, result_dir, horizon["result_stem"])
    comparisons, comparison_qa = write_post_solve_comparisons(
        year,
        network,
        config,
        verification,
        prepared["contract"],
    )
    _append_log(
        log,
        f"{horizon['phase']} solver/structural PASS; economic and production acceptance remain subject to historical result validation.",
    )
    result_artifacts.extend(prepared["artifacts"])
    result_artifacts.extend(comparisons)
    result_artifacts.append(log)
    manifest = result_dir / f"{horizon['result_stem']}_Result_Manifest_v1.0.csv"
    write_manifest(manifest, result_artifacts)
    outputs.update(
        {
            "manifest": relative_path(manifest),
            "manifest_sha256": sha256_file(manifest),
            "raw_log": relative_path(log),
            "preparation_artifacts": [relative_path(path) for path in prepared["artifacts"]],
            "diagnostics": {path.stem: relative_path(path) for path in comparisons},
        }
    )
    after = verify_immutable_history(config)
    receipt = build_receipt(
        phase=horizon["phase"],
        gate=horizon["gate"],
        status="PASS",
        input_manifests=[
            *verification["input_locks"],
            {
                "input_id": _baseline_key(year, config),
                "path": relative_path(
                    verification["history"][_baseline_key(year, config)]["receipt_path"]
                ),
                "observed_sha256": verification["history"][_baseline_key(year, config)][
                    "receipt_sha256"
                ],
                "status": "PASS",
            },
            {
                "input_id": f"b6_{year}_unsolved_network",
                "path": verification["networks"][year]["relative_path"],
                "observed_sha256": verification["networks"][year]["sha256"],
                "status": "PASS",
            },
        ],
        outputs=outputs,
        qa={
            "preflight": preflight,
            "structural_delta": prepared["structural"],
            "LP_assertions": {**linopy, **gurobi_lp},
            "post_solve": metrics,
            "comparisons": comparison_qa,
            "immutable_history_after_run": after["status"] == "PASS",
            "solver_structural_status": "PASS",
            "economic_methodological_acceptance": "PENDING_SOL_REVIEW",
            "production_price_source": False,
            "B10_authorized": False,
            "Stage_B_authorized": False,
        },
        next_gate="SOL_REVIEW_REQUIRED_NO_AUTOMATIC_PRODUCTION_PROMOTION",
        command=command_string(module=CANONICAL_MODULE),
        runtime_seconds=time.perf_counter() - started,
        solve=metrics,
    )
    write_receipt(ROOT / horizon["receipt"], receipt)
    return receipt


def _failure_receipt(year: int, error: Exception) -> Path:
    config = load_r10_config()
    horizon = config["horizons"][year]
    receipt = build_receipt(
        phase=horizon["phase"],
        gate=horizon["gate"],
        status="FAIL",
        input_manifests=[],
        outputs={"production_result_accepted": False},
        qa={
            "error_type": type(error).__name__,
            "error": str(error),
            "economic_methodological_acceptance": "NOT_REACHED",
            "B10_authorized": False,
            "Stage_B_authorized": False,
        },
        next_gate="STOP_AND_RETURN_RECEIPT",
        command=command_string(module=CANONICAL_MODULE),
    )
    return write_receipt(ROOT / horizon["receipt"], receipt)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MEM Stage-A V2.0 R10 perimeter-closure preparation and runs")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("prepare", help="Prepare both unsolved R10 experiments and diagnostics")
    for command, year in (("b8d", 2040), ("b9c", 2050)):
        child = subparsers.add_parser(command, help=f"Run the guarded {year} R10 full-year experiment")
        child.add_argument("--execute", action="store_true", help="Required explicit manual execution acknowledgement")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "prepare":
        try:
            result = prepare_all()
        except Exception as error:
            print(json.dumps({"status": "FAIL", "error": str(error)}, indent=2))
            raise SystemExit(1) from error
        print(json.dumps(result, indent=2))
        return
    year = 2040 if args.command == "b8d" else 2050
    try:
        receipt = run_horizon(year, args)
    except SystemExit:
        raise
    except Exception as error:
        path = _failure_receipt(year, error)
        print(json.dumps({"status": "FAIL", "receipt": relative_path(path), "error": str(error)}, indent=2))
        raise SystemExit(1) from error
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "gate": receipt["gate"],
                "receipt": load_r10_config()["horizons"][year]["receipt"],
                "economic_methodological_acceptance": "PENDING_SOL_REVIEW",
                "B10_authorized": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

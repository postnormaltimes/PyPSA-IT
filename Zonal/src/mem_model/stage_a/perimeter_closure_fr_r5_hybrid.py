"""Prepare and manually run ETX-7B9F, the 2050 France-only R5 hybrid.

The diagnostic starts from the immutable B6 network, reproduces the B9E
five-proxy placement, and changes only the French virtual-supply ``p_nom`` to
the already-solved R5 quantity. Preparation never invokes the production
solver and a technical solve PASS never selects a 2050 production price.
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
    SHEDDING_TOLERANCE_MW,
    VOLL_TOLERANCE_EUR_PER_MWH,
    _price_statistics,
)
from mem_model.stage_a.execution import _solve_metrics, _write_solve_outputs, gurobi_preflight
from mem_model.stage_a.network import ROOT, load_execution_config
from mem_model.stage_a.perimeter_closure import (
    _assert_gurobi_lp,
    create_and_validate_linopy_model,
    validate_lp_static,
)
from mem_model.stage_a.perimeter_closure_placement import (
    EXPORTED_MARKETS,
    PLACEMENT_GENERATOR_IDS,
    PROXY_MARKETS,
    _case_paths,
    _load_solved_case,
    _placement_delivery_diagnostics,
    validate_placement_structural_delta,
)
from mem_model.stage_a.perimeter_closure_r10 import (
    VIRTUAL_CARRIER,
    _require_hash,
    _result_paths,
    _write_json,
    load_r10_config,
    recover_cost_proxy,
    verify_immutable_history,
)
from mem_model.stage_a.perimeter_closure_r5 import (
    _dispatch_wide,
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


CONFIG_PATH = ROOT / "config/stage_a_perimeter_closure_fr_r5_hybrid_2050.yaml"
CANONICAL_MODULE = "mem_model.stage_a.perimeter_closure_fr_r5_hybrid"
YEAR = 2050
CASE_ORDER = ("B9B", "R10", "R5", "B9E", "B9F")
AUTHORIZED_FR_CAPACITY_MW = 40616.26187526855
EXPECTED_TOTAL_MW = 58478.29620050216


def load_hybrid_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if config.get("schema") != "PERIMETER_CLOSURE_2050_FR_R5_HYBRID_V1_0":
        raise RuntimeError("B9F_CONFIG_SCHEMA_MISMATCH")
    if config.get("status") != "PREPARED_NOT_EXECUTED":
        raise RuntimeError("B9F_CONFIG_NOT_PREPARED")
    design = config["design"]
    if tuple(design["proxy_markets"]) != PROXY_MARKETS:
        raise RuntimeError("B9F_PROXY_SCOPE_MISMATCH")
    if tuple(config["diagnostics"]["comparison_cases"]) != CASE_ORDER:
        raise RuntimeError("B9F_COMPARISON_SCOPE_MISMATCH")
    if tuple(config["diagnostics"]["exported_markets"]) != EXPORTED_MARKETS:
        raise RuntimeError("B9F_EXPORTED_MARKET_SCOPE_MISMATCH")
    capacities = {market: float(value) for market, value in design["capacities_MW"].items()}
    if set(capacities) != set(PROXY_MARKETS):
        raise RuntimeError("B9F_CAPACITY_SCOPE_MISMATCH")
    if not np.isclose(capacities["FR"], AUTHORIZED_FR_CAPACITY_MW, atol=0.0, rtol=0.0):
        raise RuntimeError("B9F_FR_CAPACITY_MISMATCH")
    if not np.isclose(sum(capacities.values()), EXPECTED_TOTAL_MW, atol=1e-9, rtol=0.0):
        raise RuntimeError("B9F_TOTAL_CAPACITY_MISMATCH")
    return config


def verify_hybrid_inputs(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_hybrid_config()
    method = config["method_authority"]
    _require_hash(ROOT / method["path"], method["sha256"], "B9F_METHOD_AUTHORITY")
    r10_config = load_r10_config(ROOT / method["path"])
    base = verify_immutable_history(r10_config)

    accepted: dict[str, Any] = {}
    for key in (
        "b8d_2040_production_source",
        "b9c_2050_R10_diagnostic",
        "b9d_2050_R5_diagnostic",
        "b9e_2050_placement_diagnostic",
    ):
        accepted[key] = _verify_phase(config["accepted_inputs"][key], key.upper())

    b9b_spec = config["accepted_inputs"]["b9b_2050_bounded_baseline"]
    b9b = base["history"]["b9b_2050_bounded_baseline"]
    if not (
        b9b["receipt_sha256"] == b9b_spec["receipt_sha256"]
        and b9b["manifest"]["sha256"] == b9b_spec["result_manifest_sha256"]
        and b9b["receipt"].get("gate") == b9b_spec["required_gate"]
    ):
        raise RuntimeError("B9F_B9B_IDENTITY_MISMATCH")

    network_spec = config["accepted_inputs"]["b6_2050_unsolved_network"]
    network_path = ROOT / network_spec["path"]
    network_sha = _require_hash(network_path, network_spec["sha256"], "B9F_B6_NETWORK")
    if base["networks"][YEAR]["sha256"] != network_sha:
        raise RuntimeError("B9F_B6_NETWORK_BASE_MISMATCH")

    for key, label in (
        ("B9E_virtual_supply_contract", "B9F_B9E_CONTRACT"),
        ("R5_virtual_supply_contract", "B9F_R5_CONTRACT"),
    ):
        spec = config["accepted_inputs"][key]
        _require_hash(ROOT / spec["path"], spec["sha256"], label)
    mask = config["accepted_inputs"]["immutable_ordinary_hour_mask"]
    _require_hash(ROOT / mask["path"], mask["sha256"], "B9F_ORDINARY_MASK")
    _require_hash(
        ROOT / mask["metadata_path"], mask["metadata_sha256"], "B9F_ORDINARY_MASK_METADATA"
    )
    cost = config["cost_proxy"]
    _require_hash(ROOT / cost["source_path"], cost["source_sha256"], "B9F_B4_COST")
    return {
        "status": "PASS",
        "r10_config": r10_config,
        "base": base,
        "accepted": accepted,
        "b9b": b9b,
        "b6_network_path": network_path,
        "b6_network_sha256": network_sha,
    }


def _baseline_fr_shedding(verification: dict[str, Any]) -> pd.Series:
    path = _case_paths(verification["b9b"]["receipt"])["shedding"]
    frame = pd.read_parquet(path)
    required = {"snapshot", "name", "shedding_MW"}
    if set(frame.columns) != required:
        raise RuntimeError("B9F_B9B_SHEDDING_SCHEMA_MISMATCH")
    selected = frame.loc[frame["name"].astype(str).eq("LOAD_SHEDDING_FR")].copy()
    selected["snapshot"] = pd.to_datetime(selected["snapshot"])
    if len(selected) != 8760 or selected["snapshot"].duplicated().any():
        raise RuntimeError("B9F_B9B_FR_SHEDDING_CHRONOLOGY_FAILURE")
    values = pd.to_numeric(selected["shedding_MW"], errors="raise")
    if not np.isfinite(values).all() or float(values.min()) < -1e-6:
        raise RuntimeError("B9F_B9B_FR_SHEDDING_VALUES_INVALID")
    return pd.Series(
        values.clip(lower=0.0).to_numpy(dtype=float),
        index=pd.DatetimeIndex(selected["snapshot"]),
        name="B9B_FR_shedding_MW",
    ).sort_index()


def derive_fr_percentile_benchmarks(
    config: dict[str, Any], verification: dict[str, Any]
) -> pd.DataFrame:
    shedding = _baseline_fr_shedding(verification)
    values = shedding.to_numpy(dtype=float)
    conditional = values[values > 1.0]
    if not len(conditional):
        raise RuntimeError("B9F_B9B_FR_CONDITIONAL_SHEDDING_EMPTY")
    b9e_contract = pd.read_csv(
        ROOT / config["accepted_inputs"]["B9E_virtual_supply_contract"]["path"]
    )
    r5_contract = pd.read_csv(
        ROOT / config["accepted_inputs"]["R5_virtual_supply_contract"]["path"]
    )
    r10 = float(b9e_contract.loc[b9e_contract["bus"].eq("FR"), "p_nom_MW"].iloc[0])
    r5 = float(r5_contract.loc[r5_contract["bus"].eq("FR"), "p_nom_MW"].iloc[0])
    candidates = [
        ("R10", r10, "IMMUTABLE_ETX7B9C_R10_QUANTITY", False),
        ("R5", r5, "IMMUTABLE_ETX7B9D_R5_QUANTITY", True),
        ("ALL_HOUR_P95", float(np.quantile(values, 0.95, method="linear")), "DIAGNOSTIC_ONLY", False),
        ("ALL_HOUR_P99", float(np.quantile(values, 0.99, method="linear")), "DIAGNOSTIC_ONLY", False),
        (
            "CONDITIONAL_GT1MW_P95",
            float(np.quantile(conditional, 0.95, method="linear")),
            "DIAGNOSTIC_ONLY",
            False,
        ),
        (
            "CONDITIONAL_GT1MW_P99",
            float(np.quantile(conditional, 0.99, method="linear")),
            "DIAGNOSTIC_ONLY",
            False,
        ),
    ]
    total_energy = float(values.sum())
    rows: list[dict[str, Any]] = []
    for name, capacity, source, authorized in candidates:
        residual = float(np.maximum(values - capacity, 0.0).sum())
        rows.append(
            {
                "benchmark": name,
                "capacity_MW": capacity,
                "source_role": source,
                "authorized_B9F_capacity": authorized,
                "baseline_FR_shedding_MWh": total_energy,
                "baseline_peak_FR_shedding_MW": float(values.max()),
                "baseline_hours_shedding_above_capacity": int((values > capacity).sum()),
                "baseline_shedding_energy_remaining_above_capacity_MWh": residual,
                "baseline_shedding_energy_remaining_share": residual / total_energy,
                "baseline_shedding_energy_coverage_share": 1.0 - residual / total_energy,
                "difference_vs_R10_MW": capacity - r10,
                "difference_vs_R5_MW": capacity - r5,
                "percentile_values_are_authorized_replacements": False,
            }
        )
    result = pd.DataFrame.from_records(rows)
    observed = result.set_index("benchmark")
    if not np.isclose(
        float(observed.at["R10", "baseline_shedding_energy_remaining_share"]),
        0.10,
        atol=1e-12,
        rtol=0.0,
    ):
        raise RuntimeError("B9F_FR_R10_BENCHMARK_IDENTITY_FAILURE")
    if not np.isclose(
        float(observed.at["R5", "baseline_shedding_energy_remaining_share"]),
        0.05,
        atol=1e-12,
        rtol=0.0,
    ):
        raise RuntimeError("B9F_FR_R5_BENCHMARK_IDENTITY_FAILURE")
    return result


def derive_hybrid_contract(
    config: dict[str, Any], verification: dict[str, Any]
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    placement = pd.read_csv(
        ROOT / config["accepted_inputs"]["B9E_virtual_supply_contract"]["path"]
    )
    r5 = pd.read_csv(ROOT / config["accepted_inputs"]["R5_virtual_supply_contract"]["path"])
    if tuple(placement["bus"]) != PROXY_MARKETS or len(placement) != 5:
        raise RuntimeError("B9F_B9E_CONTRACT_SCOPE_FAILURE")
    if set(placement["generator_id"]) != set(PLACEMENT_GENERATOR_IDS.values()):
        raise RuntimeError("B9F_B9E_GENERATOR_IDENTITY_FAILURE")
    r5_fr = r5.loc[r5["bus"].eq("FR")]
    if len(r5_fr) != 1:
        raise RuntimeError("B9F_R5_FR_SOURCE_NOT_UNIQUE")
    r5_fr_capacity = float(r5_fr.iloc[0]["p_nom_MW"])
    if not np.isclose(r5_fr_capacity, AUTHORIZED_FR_CAPACITY_MW, atol=0.0, rtol=0.0):
        raise RuntimeError("B9F_R5_FR_SOURCE_CAPACITY_MISMATCH")

    contract = placement.copy(deep=True)
    old_fr = float(contract.loc[contract["bus"].eq("FR"), "p_nom_MW"].iloc[0])
    contract.loc[contract["bus"].eq("FR"), "p_nom_MW"] = r5_fr_capacity
    contract.loc[contract["bus"].eq("FR"), "capacity_source"] = (
        "EXISTING_IMMUTABLE_ETX7B9D_R5_FRANCE_QUANTITY"
    )
    contract.loc[contract["bus"].eq("FR"), "capacity_sizing_rule"] = (
        "RETAIN_EXACT_ETX7B9D_R5_FRANCE_VALUE"
    )
    contract["status"] = "FRANCE_R5_HYBRID_FIXED_VIRTUAL_EXTERNAL_SUPPLY"
    contract["network_parameter_delta_vs_B9E"] = np.where(
        contract["bus"].eq("FR"), "P_NOM_ONLY", "NONE"
    )

    configured = {market: float(value) for market, value in config["design"]["capacities_MW"].items()}
    for row in contract.itertuples(index=False):
        if not np.isclose(float(row.p_nom_MW), configured[str(row.bus)], atol=0.0, rtol=0.0):
            raise RuntimeError(f"B9F_CONFIGURED_CAPACITY_MISMATCH_{row.bus}")
    cost = recover_cost_proxy(YEAR, verification["r10_config"])
    expected_cost = float(config["cost_proxy"]["marginal_cost_EUR2025_per_MWh_el"])
    if not np.isclose(
        float(cost["marginal_cost_EUR2025_per_MWh_el"]), expected_cost, atol=1e-12, rtol=0.0
    ):
        raise RuntimeError("B9F_COST_PROXY_MISMATCH")
    if not np.allclose(
        contract["marginal_cost_EUR2025_per_MWh_el"].astype(float), expected_cost, atol=0.0, rtol=0.0
    ):
        raise RuntimeError("B9F_CONTRACT_COST_DRIFT")

    total = float(contract["p_nom_MW"].sum())
    control = {
        "classification": config["design"]["classification"],
        "B9E_total_virtual_capacity_MW": float(placement["p_nom_MW"].sum()),
        "B9F_total_virtual_capacity_MW": total,
        "B9E_FR_capacity_MW": old_fr,
        "B9F_FR_capacity_MW": r5_fr_capacity,
        "FR_delta_vs_B9E_MW": r5_fr_capacity - old_fr,
        "changed_network_parameter_count": 1,
        "changed_market": "FR",
        "changed_parameter": "p_nom_MW",
        "near_constant_capacity_test": False,
    }
    if not np.isclose(total, EXPECTED_TOTAL_MW, atol=1e-9, rtol=0.0):
        raise RuntimeError("B9F_TOTAL_CAPACITY_CONTROL_FAILURE")
    if not np.isclose(
        control["FR_delta_vs_B9E_MW"],
        float(config["design"]["expected_FR_delta_vs_B9E_MW"]),
        atol=1e-9,
        rtol=0.0,
    ):
        raise RuntimeError("B9F_FR_DELTA_CONTROL_FAILURE")
    return contract, cost, control


def add_hybrid_virtual_supply(
    baseline: pypsa.Network, contract: pd.DataFrame
) -> pypsa.Network:
    network = baseline.copy()
    if VIRTUAL_CARRIER in network.carriers.index:
        raise RuntimeError("B9F_VIRTUAL_CARRIER_ALREADY_PRESENT")
    if set(network.generators.index) & set(contract["generator_id"]):
        raise RuntimeError("B9F_VIRTUAL_GENERATOR_ALREADY_PRESENT")
    network.add(
        "Carrier",
        VIRTUAL_CARRIER,
        co2_emissions=0.0,
        nice_name="France R5 hybrid residual firm external virtual supply",
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
        "auxiliary_experiment": "FRANCE_R5_HYBRID_PLACEMENT_CLOSURE_2050",
        "method": "PERIMETER_CLOSURE_METHOD_V2_0",
        "changed_parameter_vs_B9E": "FR_p_nom_only",
        "technical_status": "PREPARED_NOT_EXECUTED",
        "production_price_acceptance": "PENDING_SOL_REVIEW",
    }
    return network


def validate_hybrid_structural_delta(
    baseline: pypsa.Network,
    network: pypsa.Network,
    contract: pd.DataFrame,
    b9e_contract: pd.DataFrame,
) -> dict[str, Any]:
    structural = validate_placement_structural_delta(baseline, network, contract)
    comparison = contract.set_index("bus")["p_nom_MW"].astype(float) - b9e_contract.set_index(
        "bus"
    )["p_nom_MW"].astype(float)
    changed = comparison.loc[~np.isclose(comparison, 0.0, atol=0.0, rtol=0.0)]
    if list(changed.index) != ["FR"]:
        raise RuntimeError(f"B9F_NOT_FR_ONLY_DELTA: {changed.to_dict()}")
    if not np.isclose(float(changed.iloc[0]), 6266.11878575798, atol=1e-9, rtol=0.0):
        raise RuntimeError("B9F_FR_ONLY_DELTA_VALUE_FAILURE")
    structural.update(
        {
            "diagnostic": "ETX-7B9F_FRANCE_ONLY_R5_HYBRID",
            "B6_vs_B9F_component_delta": {"carriers": 1, "generators": 5},
            "B9E_vs_B9F_virtual_parameter_delta": {
                "market": "FR",
                "parameter": "p_nom_MW",
                "delta_MW": float(changed.iloc[0]),
            },
            "all_other_B9E_virtual_parameters_preserved": True,
        }
    )
    return structural


def _preparation_paths(config: dict[str, Any]) -> dict[str, Path]:
    qa = ROOT / config["phase"]["qa_directory"]
    return {
        "contract": qa / "MEM_FR_R5_Hybrid_2050_Virtual_Supply_Contract_v1.0.csv",
        "benchmarks": qa / "MEM_FR_R5_Hybrid_2050_FR_Percentile_Benchmarks_v1.0.csv",
        "qa": qa / "MEM_FR_R5_Hybrid_2050_Preparation_QA_v1.0.json",
        "manifest": qa / "MEM_FR_R5_Hybrid_2050_Preparation_Manifest_v1.0.csv",
        "verification": qa / "MEM_FR_R5_Hybrid_2050_Preparation_Final_Verification_v1.0.json",
    }


def prepare_hybrid(*, write_artifacts: bool = True) -> dict[str, Any]:
    config = load_hybrid_config()
    verification = verify_hybrid_inputs(config)
    contract, cost, capacity_control = derive_hybrid_contract(config, verification)
    benchmarks = derive_fr_percentile_benchmarks(config, verification)
    baseline = pypsa.Network(verification["b6_network_path"])
    if len(baseline.snapshots) != 8760 or int(baseline.meta.get("horizon", YEAR)) != YEAR:
        raise RuntimeError("B9F_B6_CHRONOLOGY_FAILURE")
    b9e_contract = pd.read_csv(
        ROOT / config["accepted_inputs"]["B9E_virtual_supply_contract"]["path"]
    )
    network = add_hybrid_virtual_supply(baseline, contract)
    structural = validate_hybrid_structural_delta(baseline, network, contract, b9e_contract)
    static_lp = validate_lp_static(network)
    short = network.copy()
    short.set_snapshots(short.snapshots[:6])
    short_lp = create_and_validate_linopy_model(short)
    payload = {
        "schema_version": "MEM_FR_R5_HYBRID_2050_PREPARATION_QA_V1_0",
        "status": "PASS",
        "phase": config["phase"]["id"],
        "gate": config["phase"]["gate"],
        "classification": config["design"]["classification"],
        "capacities_MW": contract.set_index("bus")["p_nom_MW"].astype(float).to_dict(),
        "capacity_control": capacity_control,
        "marginal_cost_EUR2025_per_MWh_el": float(
            contract["marginal_cost_EUR2025_per_MWh_el"].iloc[0]
        ),
        "France_percentile_benchmark_rows": len(benchmarks),
        "structural_delta": structural,
        "static_LP": static_lp,
        "short_unsolved_LP_fixture": {"snapshots": 6, **short_lp},
        "immutable_B8D_B9B_B9C_B9D_B9E_verified": True,
        "production_optimization_executed": False,
        "production_price_acceptance": "PENDING_SOL_REVIEW",
        "B10_2050_authorized": False,
        "Stage_B_2050_authorized": False,
    }
    paths = _preparation_paths(config)
    artifacts: list[Path] = []
    if write_artifacts:
        paths["contract"].parent.mkdir(parents=True, exist_ok=True)
        contract.to_csv(paths["contract"], index=False, encoding="utf-8", lineterminator="\n")
        benchmarks.to_csv(
            paths["benchmarks"], index=False, encoding="utf-8", lineterminator="\n"
        )
        _write_json(paths["qa"], payload)
        artifacts = [paths["contract"], paths["benchmarks"], paths["qa"]]
        members = [
            CONFIG_PATH,
            ROOT / "src/mem_model/stage_a/perimeter_closure_fr_r5_hybrid.py",
            ROOT / "docs/runbooks/ETX7B9F_RUNBOOK.md",
            ROOT / "docs/MEM_ETX7B9F_2050_FR_R5_HYBRID_PREPARATION_TRANSFER.md",
            *artifacts,
        ]
        manifest = write_manifest(paths["manifest"], members)
        _write_json(
            paths["verification"],
            {
                "schema_version": "MEM_FR_R5_HYBRID_2050_PREPARATION_FINAL_VERIFICATION_V1_0",
                "status": "PASS",
                "preparation_manifest": relative_path(paths["manifest"]),
                "preparation_manifest_sha256": sha256_file(paths["manifest"]),
                "preparation_manifest_members": len(manifest),
                "B8D_receipt_sha256": verification["accepted"][
                    "b8d_2040_production_source"
                ]["receipt_sha256"],
                "B9B_receipt_sha256": verification["b9b"]["receipt_sha256"],
                "B9C_receipt_sha256": verification["accepted"][
                    "b9c_2050_R10_diagnostic"
                ]["receipt_sha256"],
                "B9D_receipt_sha256": verification["accepted"][
                    "b9d_2050_R5_diagnostic"
                ]["receipt_sha256"],
                "B9E_receipt_sha256": verification["accepted"][
                    "b9e_2050_placement_diagnostic"
                ]["receipt_sha256"],
                "B6_2050_network_sha256": verification["b6_network_sha256"],
                "capacities_MW": payload["capacities_MW"],
                "capacity_control": capacity_control,
                "production_optimization_executed": False,
                "production_price_acceptance": "PENDING_SOL_REVIEW",
                "B10_2050_authorized": False,
                "Stage_B_2050_authorized": False,
            },
        )
        artifacts.extend([paths["manifest"], paths["verification"]])
    return {
        "config": config,
        "verification": verification,
        "baseline": baseline,
        "network": network,
        "contract": contract,
        "benchmarks": benchmarks,
        "cost": cost,
        "capacity_control": capacity_control,
        "structural": structural,
        "LP": short_lp,
        "payload": payload,
        "artifacts": artifacts,
    }


def _comparison_cases(
    hybrid_network: pypsa.Network, prepared: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    verification = prepared["verification"]
    config = prepared["config"]
    receipts = {
        "B9B": verification["b9b"]["receipt"],
        "R10": verification["accepted"]["b9c_2050_R10_diagnostic"]["receipt"],
        "R5": verification["accepted"]["b9d_2050_R5_diagnostic"]["receipt"],
        "B9E": verification["accepted"]["b9e_2050_placement_diagnostic"]["receipt"],
    }
    voll = float(config["diagnostics"]["VOLL_EUR_per_MWh"])
    cases: dict[str, dict[str, Any]] = {}
    for name, receipt in receipts.items():
        paths = _case_paths(receipt)
        network = pypsa.Network(paths["solved"])
        cases[name] = _load_solved_case(network, paths, float(receipt["objective"]), voll)
    hybrid_paths = _result_paths(
        ROOT / config["phase"]["result_directory"], config["phase"]["result_stem"]
    )
    cases["B9F"] = _load_solved_case(
        hybrid_network, hybrid_paths, float(hybrid_network.objective), voll
    )
    reference = cases["B9B"]
    for name, case in cases.items():
        if not reference["network"].snapshots.equals(case["network"].snapshots):
            raise RuntimeError(f"B9F_FIVE_CASE_SNAPSHOT_DRIFT_{name}")
        if not np.allclose(reference["load"], case["load"], atol=0.0, rtol=0.0):
            raise RuntimeError(f"B9F_FIVE_CASE_LOAD_DRIFT_{name}")
    return cases


def _immutable_ordinary_mask(
    cases: dict[str, dict[str, Any]], config: dict[str, Any]
) -> tuple[pd.Series, dict[str, Any]]:
    spec = config["accepted_inputs"]["immutable_ordinary_hour_mask"]
    source = pd.read_csv(ROOT / spec["path"])
    source["snapshot"] = pd.to_datetime(source["snapshot"])
    source = source.set_index("snapshot").reindex(cases["B9B"]["network"].snapshots)
    raw = source["ordinary_hour"]
    mask = (
        raw.astype(bool)
        if raw.dtype == bool
        else raw.astype(str).str.casefold().map({"true": True, "false": False})
    )
    if mask.isna().any():
        raise RuntimeError("B9F_ORDINARY_MASK_PARSE_FAILURE")
    derived, _, metadata = _ordinary_hour_mask(
        cases["B9B"]["shedding"], cases["B9B"]["prices"]
    )
    if not mask.astype(bool).equals(derived):
        raise RuntimeError("B9F_ORDINARY_MASK_CONTENT_DRIFT")
    expected = config["diagnostics"]["ordinary_hour_mask"]
    if (
        int(mask.sum()) != int(expected["expected_hours"])
        or metadata["mask_boolean_sha256"] != expected["expected_boolean_sha256"]
    ):
        raise RuntimeError("B9F_ORDINARY_MASK_IDENTITY_FAILURE")
    return mask.astype(bool), {
        "status": "PASS_REUSED_WITHOUT_REGENERATION",
        "definition": expected["definition"],
        "ordinary_hours": int(mask.sum()),
        "mask_boolean_sha256": metadata["mask_boolean_sha256"],
        "applied_unchanged_to": list(CASE_ORDER),
        "B9F_outcome_used_to_define_mask": False,
    }


def _five_case_system(cases: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for name in CASE_ORDER:
        record = {"case": name, **cases[name]["system"]}
        exported = cases[name]["prices"].loc[:, EXPORTED_MARKETS].to_numpy(dtype=float)
        record["eight_export_market_mean_EUR_per_MWh"] = float(exported.mean())
        rows.append(record)
    return pd.DataFrame.from_records(rows)


def _five_case_exported_market_diagnostics(
    cases: dict[str, dict[str, Any]], mask: pd.Series, voll: float
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    aggregates: list[dict[str, Any]] = []
    unique: list[dict[str, Any]] = []
    for name in CASE_ORDER:
        case = cases[name]
        price_table = case["prices"].loc[:, EXPORTED_MARKETS]
        shedding = case["shedding"].loc[:, EXPORTED_MARKETS]
        weights = case["weights"]
        for market in EXPORTED_MARKETS:
            values = price_table[market].astype(float)
            stats = _price_statistics(values, voll)
            at_voll = np.isclose(
                values.to_numpy(), voll, atol=VOLL_TOLERANCE_EUR_PER_MWH, rtol=0.0
            )
            rows.append(
                {
                    "case": name,
                    "market": market,
                    **stats,
                    "shedding_MWh": float((shedding[market] * weights).sum()),
                    "shedding_hours_above_1_MW": int(shedding[market].gt(1.0).sum()),
                    "mean_excluding_VOLL_hours_EUR_per_MWh": float(values.loc[~at_voll].mean()),
                    "mean_excluding_above_5000_hours_EUR_per_MWh": float(
                        values.loc[values.le(5000.0)].mean()
                    ),
                    "mean_excluding_above_1000_hours_EUR_per_MWh": float(
                        values.loc[values.le(1000.0)].mean()
                    ),
                    "ordinary_hour_mean_EUR_per_MWh": float(values.loc[mask].mean()),
                    "ordinary_hour_count": int(mask.sum()),
                }
            )
        array = price_table.to_numpy(dtype=float)
        aggregates.append(
            {
                "case": name,
                "total_VOLL_market_hours": int(
                    np.isclose(array, voll, atol=VOLL_TOLERANCE_EUR_PER_MWH, rtol=0.0).sum()
                ),
                "total_market_hours_above_500_EUR_per_MWh": int((array > 500.0).sum()),
                "total_market_hours_above_1000_EUR_per_MWh": int((array > 1000.0).sum()),
                "total_market_hours_above_5000_EUR_per_MWh": int((array > 5000.0).sum()),
            }
        )
        unique.append(
            {
                "case": name,
                "unique_hours_any_exported_market_at_VOLL": int(
                    np.isclose(array, voll, atol=VOLL_TOLERANCE_EUR_PER_MWH, rtol=0.0)
                    .any(axis=1)
                    .sum()
                ),
                "unique_hours_any_exported_market_above_500_EUR_per_MWh": int(
                    (array > 500.0).any(axis=1).sum()
                ),
                "unique_hours_any_exported_market_above_1000_EUR_per_MWh": int(
                    (array > 1000.0).any(axis=1).sum()
                ),
                "unique_hours_any_exported_market_above_5000_EUR_per_MWh": int(
                    (array > 5000.0).any(axis=1).sum()
                ),
            }
        )
    return (
        pd.DataFrame.from_records(rows),
        pd.DataFrame.from_records(aggregates),
        pd.DataFrame.from_records(unique),
    )


def _ordinary_direct_comparison(
    cases: dict[str, dict[str, Any]], mask: pd.Series
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    hybrid = cases["B9F"]["prices"].loc[mask]
    for market in EXPORTED_MARKETS:
        for comparator in ("B9E", "R5"):
            reference = cases[comparator]["prices"].loc[mask, market].astype(float)
            values = hybrid[market].astype(float)
            difference = values - reference
            rows.append(
                {
                    "market": market,
                    "comparison": f"B9F_MINUS_{comparator}",
                    "ordinary_hour_count": int(mask.sum()),
                    "mean_price_difference_EUR_per_MWh": float(values.mean() - reference.mean()),
                    "median_price_difference_EUR_per_MWh": float(
                        values.median() - reference.median()
                    ),
                    "P95_price_difference_EUR_per_MWh": float(
                        values.quantile(0.95) - reference.quantile(0.95)
                    ),
                    "mean_absolute_pointwise_change_EUR_per_MWh": float(
                        difference.abs().mean()
                    ),
                    "maximum_absolute_pointwise_change_EUR_per_MWh": float(
                        difference.abs().max()
                    ),
                    "mask_source": "IMMUTABLE_B9B_3020_HOUR_MASK",
                }
            )
    return pd.DataFrame.from_records(rows)


def _fr_utilization(
    cases: dict[str, dict[str, Any]], prepared: dict[str, Any]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    config = prepared["config"]
    placement = pd.read_csv(
        ROOT / config["accepted_inputs"]["B9E_virtual_supply_contract"]["path"]
    )
    r5 = pd.read_csv(ROOT / config["accepted_inputs"]["R5_virtual_supply_contract"]["path"])
    contracts = {
        "R10": placement,
        "R5": r5,
        "B9E": placement,
        "B9F": prepared["contract"],
    }
    b9b_fr_scarcity = cases["B9B"]["shedding"]["FR"].gt(SHEDDING_TOLERANCE_MW)
    b9e_fr_scarcity = cases["B9E"]["shedding"]["FR"].gt(SHEDDING_TOLERANCE_MW)
    b9b_system_scarcity = cases["B9B"]["shedding"].sum(axis=1).gt(SHEDDING_TOLERANCE_MW)
    rows: list[dict[str, Any]] = []
    for name in ("R10", "R5", "B9E", "B9F"):
        row = contracts[name].loc[contracts[name]["bus"].eq("FR")].iloc[0]
        dispatch = _dispatch_wide(
            cases[name]["paths"]["dispatch"],
            cases[name]["network"].snapshots,
            [row["generator_id"]],
        )[row["generator_id"]].astype(float)
        weights = cases[name]["weights"]
        p_nom = float(row["p_nom_MW"])
        annual = float((dispatch * weights).sum())
        during_b9b = float((dispatch.loc[b9b_fr_scarcity] * weights.loc[b9b_fr_scarcity]).sum())
        during_b9e = float((dispatch.loc[b9e_fr_scarcity] * weights.loc[b9e_fr_scarcity]).sum())
        outside_all_b9b = float(
            (dispatch.loc[~b9b_system_scarcity] * weights.loc[~b9b_system_scarcity]).sum()
        )
        rows.append(
            {
                "case": name,
                "generator_id": row["generator_id"],
                "p_nom_MW": p_nom,
                "annual_dispatch_MWh": annual,
                "annual_capacity_factor": annual / (p_nom * float(weights.sum())),
                "peak_dispatch_MW": float(dispatch.max()),
                "hours_dispatch_above_1_MW": int(dispatch.gt(1.0).sum()),
                "hours_dispatch_above_50_percent_p_nom": int(dispatch.gt(0.5 * p_nom).sum()),
                "hours_dispatch_above_90_percent_p_nom": int(dispatch.gt(0.9 * p_nom).sum()),
                "hours_dispatch_at_or_near_p_nom": int(
                    np.isclose(dispatch.to_numpy(), p_nom, atol=1e-6, rtol=0.0).sum()
                ),
                "dispatch_during_B9B_FR_scarcity_MWh": during_b9b,
                "share_dispatch_during_B9B_FR_scarcity": during_b9b / annual if annual else 0.0,
                "dispatch_during_B9E_FR_scarcity_MWh": during_b9e,
                "dispatch_outside_all_B9B_system_scarcity_hours_MWh": outside_all_b9b,
            }
        )
    utilization = pd.DataFrame.from_records(rows)
    indexed = utilization.set_index("case")
    metrics = [column for column in utilization.columns if column not in {"case", "generator_id"}]
    comparison: dict[str, Any] = {"market": "FR", "purpose": "R10_R5_B9E_B9F_COMPARISON"}
    for metric in metrics:
        for name in ("R10", "R5", "B9E", "B9F"):
            comparison[f"{name}_{metric}"] = indexed.at[name, metric]
        comparison[f"B9F_minus_B9E_{metric}"] = indexed.at["B9F", metric] - indexed.at["B9E", metric]
        comparison[f"B9F_minus_R5_{metric}"] = indexed.at["B9F", metric] - indexed.at["R5", metric]
    return utilization, pd.DataFrame([comparison])


def _it_diagnostics(cases: dict[str, dict[str, Any]], voll: float) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for name in CASE_ORDER:
        prices = cases[name]["prices"]["IT"].astype(float)
        shedding = cases[name]["shedding"]["IT"].astype(float)
        weights = cases[name]["weights"]
        rows.append(
            {
                "case": name,
                **_price_statistics(prices, voll),
                "shedding_MWh": float((shedding * weights).sum()),
                "shedding_hours_above_1e_6_MW": int(
                    shedding.gt(SHEDDING_TOLERANCE_MW).sum()
                ),
                "shedding_hours_above_1_MW": int(shedding.gt(1.0).sum()),
                "maximum_local_shedding_MW": float(shedding.max()),
            }
        )
    return pd.DataFrame.from_records(rows)


def _other_market_effects(market: pd.DataFrame) -> pd.DataFrame:
    metrics = [
        "price_mean_EUR_per_MWh",
        "price_median_EUR_per_MWh",
        "price_P95_EUR_per_MWh",
        "price_P99_EUR_per_MWh",
        "hours_at_VOLL",
        "hours_above_500_EUR_per_MWh",
        "hours_above_1000_EUR_per_MWh",
        "hours_above_5000_EUR_per_MWh",
        "shedding_MWh",
        "ordinary_hour_mean_EUR_per_MWh",
    ]
    rows: list[dict[str, Any]] = []
    for target in ("AT", "SI", "GR", "CH", "ME", "MT", "TN"):
        selected = market.loc[market["market"].eq(target)].set_index("case")
        record: dict[str, Any] = {"market": target, "automatic_materiality_decision": False}
        for metric in metrics:
            record[f"B9F_{metric}"] = selected.at["B9F", metric]
            record[f"B9F_minus_B9E_{metric}"] = selected.at["B9F", metric] - selected.at[
                "B9E", metric
            ]
            record[f"B9F_minus_R5_{metric}"] = selected.at["B9F", metric] - selected.at[
                "R5", metric
            ]
        rows.append(record)
    return pd.DataFrame.from_records(rows)


def write_post_solve_diagnostics(
    network: pypsa.Network, prepared: dict[str, Any]
) -> tuple[list[Path], dict[str, Any]]:
    config = prepared["config"]
    cases = _comparison_cases(network, prepared)
    mask, mask_metadata = _immutable_ordinary_mask(cases, config)
    voll = float(config["diagnostics"]["VOLL_EUR_per_MWh"])
    system = _five_case_system(cases)
    market, aggregate, unique = _five_case_exported_market_diagnostics(cases, mask, voll)
    ordinary_direct = _ordinary_direct_comparison(cases, mask)
    utilization, utilization_comparison = _fr_utilization(cases, prepared)
    it = _it_diagnostics(cases, voll)
    effects = _other_market_effects(market)
    hybrid_dispatch = _dispatch_wide(
        cases["B9F"]["paths"]["dispatch"],
        cases["B9F"]["network"].snapshots,
        prepared["contract"]["generator_id"],
    )
    headroom, interfaces, delivery = _placement_delivery_diagnostics(
        cases["B9F"], prepared["contract"], hybrid_dispatch
    )
    interpretation = {
        "schema_version": "MEM_FR_R5_HYBRID_2050_INTERPRETATION_CONTROL_V1_0",
        "status": "EVIDENCE_ONLY_PENDING_SOL_REVIEW",
        "question": "Whether restoring only France to the existing R5 quantity preserves B9E placement improvements and removes the main remaining external-price distortion",
        "practical_100_to_200_EUR_per_MWh_band_is_diagnostic_only": True,
        "automatic_economic_acceptance": False,
        "automatic_successor_experiment": False,
        "production_price_acceptance": "PENDING_SOL_REVIEW",
        "B10_2050_authorized": False,
        "Stage_B_2050_authorized": False,
    }
    qa_dir = ROOT / config["phase"]["qa_directory"]
    outputs = {
        "system": qa_dir / "MEM_FR_R5_Hybrid_2050_Five_Way_System_Comparison_v1.0.csv",
        "market": qa_dir / "MEM_FR_R5_Hybrid_2050_Exported_Market_Five_Way_Comparison_v1.0.csv",
        "aggregate": qa_dir / "MEM_FR_R5_Hybrid_2050_Exported_Market_Aggregates_v1.0.csv",
        "unique": qa_dir / "MEM_FR_R5_Hybrid_2050_Exported_Market_Unique_Hours_v1.0.csv",
        "mask": qa_dir / "MEM_FR_R5_Hybrid_2050_Ordinary_Hour_Mask_Reuse_v1.0.json",
        "ordinary_direct": qa_dir / "MEM_FR_R5_Hybrid_2050_Ordinary_Hour_Direct_Comparison_v1.0.csv",
        "utilization": qa_dir / "MEM_FR_R5_Hybrid_2050_FR_Utilization_v1.0.csv",
        "utilization_comparison": qa_dir / "MEM_FR_R5_Hybrid_2050_FR_Utilization_Comparison_v1.0.csv",
        "it": qa_dir / "MEM_FR_R5_Hybrid_2050_IT_Diagnostic_v1.0.csv",
        "effects": qa_dir / "MEM_FR_R5_Hybrid_2050_Other_Market_Effects_v1.0.csv",
        "headroom": qa_dir / "MEM_FR_R5_Hybrid_2050_Residual_Scarcity_Headroom_v1.0.csv",
        "interfaces": qa_dir / "MEM_FR_R5_Hybrid_2050_Residual_Scarcity_Interface_States_v1.0.csv",
        "delivery": qa_dir / "MEM_FR_R5_Hybrid_2050_Delivery_Summary_v1.0.json",
        "interpretation": qa_dir / "MEM_FR_R5_Hybrid_2050_Interpretation_Control_v1.0.json",
    }
    qa_dir.mkdir(parents=True, exist_ok=True)
    for key, frame in (
        ("system", system),
        ("market", market),
        ("aggregate", aggregate),
        ("unique", unique),
        ("ordinary_direct", ordinary_direct),
        ("utilization", utilization),
        ("utilization_comparison", utilization_comparison),
        ("it", it),
        ("effects", effects),
        ("headroom", headroom),
        ("interfaces", interfaces),
    ):
        frame.to_csv(outputs[key], index=False, encoding="utf-8", lineterminator="\n")
    _write_json(outputs["mask"], mask_metadata)
    _write_json(outputs["delivery"], delivery)
    _write_json(outputs["interpretation"], interpretation)
    return list(outputs.values()), {
        "comparison_cases": list(CASE_ORDER),
        "system_rows": len(system),
        "exported_market_rows": len(market),
        "ordinary_hour_mask": mask_metadata,
        "France_utilization_rows": len(utilization),
        "IT_diagnostic_rows": len(it),
        "automatic_economic_acceptance": False,
        "production_price_acceptance": "PENDING_SOL_REVIEW",
    }


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def run_b9f(args: argparse.Namespace) -> dict[str, Any]:
    if not args.execute:
        raise SystemExit(
            "FR_R5_HYBRID_NOT_EXECUTED: rerun the exact prepared command with --execute"
        )
    prepared = prepare_hybrid(write_artifacts=True)
    config = prepared["config"]
    execution_config = load_execution_config()
    if (
        config["solver"]["name"] != execution_config["solver"]["name"]
        or config["solver"]["options"] != execution_config["solver"]["options"]
    ):
        raise RuntimeError("B9F_SOLVER_CONFIG_DRIFT")
    started = time.perf_counter()
    qa_dir = ROOT / config["phase"]["qa_directory"]
    log = qa_dir / "logs/MEM_ETX7B9F_raw.log"
    _append_log(log, "ETX-7B9F started; economic acceptance remains subject to historical result validation.")
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
        raise RuntimeError(f"B9F_POST_SOLVE_TECHNICAL_QA_FAILED: {metrics}")
    for row in prepared["contract"].itertuples(index=False):
        dispatch = network.generators_t.p[row.generator_id].astype(float)
        if dispatch.min() < -1e-5 or dispatch.max() > float(row.p_nom_MW) + 1e-5:
            raise RuntimeError(f"B9F_DISPATCH_BOUND_FAILURE_{row.generator_id}")

    result_dir = ROOT / config["phase"]["result_directory"]
    result_artifacts, outputs = _write_solve_outputs(
        network, result_dir, config["phase"]["result_stem"]
    )
    diagnostics, diagnostic_qa = write_post_solve_diagnostics(network, prepared)
    _append_log(
        log,
        "ETX-7B9F technical diagnostic PASS; no production source selected and no successor experiment authorized.",
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
    after = verify_hybrid_inputs(config)
    inputs = [
        {
            "input_id": key,
            "path": relative_path(value["receipt_path"]),
            "observed_sha256": value["receipt_sha256"],
            "status": "PASS",
        }
        for key, value in after["accepted"].items()
    ]
    inputs.extend(
        [
            {
                "input_id": "B9B_2050_BOUNDED_BASELINE",
                "path": relative_path(after["b9b"]["receipt_path"]),
                "observed_sha256": after["b9b"]["receipt_sha256"],
                "status": "PASS",
            },
            {
                "input_id": "B6_2050_UNSOLVED_NETWORK",
                "path": config["accepted_inputs"]["b6_2050_unsolved_network"]["path"],
                "observed_sha256": after["b6_network_sha256"],
                "status": "PASS",
            },
        ]
    )
    receipt = build_receipt(
        phase=config["phase"]["id"],
        gate=config["phase"]["gate"],
        status="PASS",
        input_manifests=inputs,
        outputs=outputs,
        qa={
            "preflight": preflight,
            "structural_delta": prepared["structural"],
            "capacity_control": prepared["capacity_control"],
            "LP_assertions": {**linopy, **gurobi_lp},
            "post_solve": metrics,
            "comparisons": diagnostic_qa,
            "technical_diagnostic_status": "TECHNICAL_FR_R5_HYBRID_DIAGNOSTIC_PASS",
            "economic_methodological_acceptance": "PENDING_SOL_REVIEW",
            "production_price_source": False,
            "automatic_successor_experiment": False,
            "B10_2050_authorized": False,
            "Stage_B_2050_authorized": False,
        },
        next_gate="SOL_REVIEW_REQUIRED_NO_AUTOMATIC_PRODUCTION_PROMOTION",
        command=command_string(module=CANONICAL_MODULE),
        runtime_seconds=time.perf_counter() - started,
        solve=metrics,
    )
    write_receipt(ROOT / config["phase"]["receipt"], receipt)
    return receipt


def _failure_receipt(error: Exception) -> Path:
    config = load_hybrid_config()
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
            "B10_2050_authorized": False,
            "Stage_B_2050_authorized": False,
        },
        next_gate="STOP_AND_RETURN_RECEIPT",
        command=command_string(module=CANONICAL_MODULE),
    )
    return write_receipt(ROOT / config["phase"]["receipt"], receipt)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MEM ETX-7B9F France-only R5 hybrid")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("prepare", help="Run unsolved B9F preparation and structural QA")
    run = subparsers.add_parser("b9f", help="Run the guarded full-year B9F diagnostic")
    run.add_argument(
        "--execute", action="store_true", help="Required explicit manual execution acknowledgement"
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "prepare":
        try:
            prepared = prepare_hybrid(write_artifacts=True)
        except Exception as error:
            print(json.dumps({"status": "FAIL", "error": str(error)}, indent=2))
            raise SystemExit(1) from error
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "phase": prepared["config"]["phase"]["id"],
                    "capacities_MW": prepared["payload"]["capacities_MW"],
                    "capacity_control": prepared["capacity_control"],
                    "production_optimization_executed": False,
                    "manual_command": prepared["config"]["phase"]["command"],
                    "B10_2050_authorized": False,
                    "Stage_B_2050_authorized": False,
                },
                indent=2,
            )
        )
        return
    try:
        receipt = run_b9f(args)
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
                "receipt": load_hybrid_config()["phase"]["receipt"],
                "production_price_acceptance": "PENDING_SOL_REVIEW",
                "B10_2050_authorized": False,
                "Stage_B_2050_authorized": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

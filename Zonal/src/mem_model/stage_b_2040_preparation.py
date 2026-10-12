"""Read-only preparation QA for the independent 2040 Stage-B path.

This module verifies the accepted ETX-7B8D price source and the frozen Italian
Slow/Base/High static contracts.  It deliberately does not create missing
hourly Italian inputs, build a production network, or invoke an optimizer.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from mem_model.common import ACCEPTED_RUNTIME, PRICE_MARKETS, STATIC, ZONES, parse_bool
from mem_model.runtime_bundle import RUNTIME_FILES
from mem_model.stage_b_2040_runtime import verify_accepted_runtime_manifest
from mem_model.stage_a.execution import PRICE_MAPPING
from mem_model.stage_a.network import ROOT
from mem_model.stage_a.receipts import relative_path, sha256_file


QA_DIR = ROOT / "qa/stage_b/pre_2040"
SOURCE_VERIFICATION = QA_DIR / "MEM_STAGE_B_2040_PRICE_SOURCE_VERIFICATION_v1.0.json"
SCENARIO_RECONCILIATION = QA_DIR / "MEM_STAGE_B_2040_SCENARIO_INPUT_RECONCILIATION_v1.0.csv"
PREPARATION_QA = QA_DIR / "MEM_STAGE_B_2040_PREPARATION_QA_v1.0.json"
FINAL_VERIFICATION = QA_DIR / "MEM_STAGE_B_2040_PREPARATION_FINAL_VERIFICATION_v1.0.json"
PRICE_CONTRACT = ROOT / "config/stage_b_price_input_contract.yaml"
PRODUCTION_SOURCES = ROOT / "config/stage_a_production_price_sources.yaml"
APPROVAL_GATES = ROOT / "config/approval_gates.yaml"
EXPECTED_B8D_HASHES = {
    "receipt_sha256": "C69ACBF941261BD37426EDCEDB1C14482977C2059427FEA1968008492E05F8C0",
    "result_manifest_sha256": "88C259493E5D5B8E4B4B2B3C535BCDB623924474FB23DD860D2B8673FE08DA2D",
    "market_prices_sha256": "595D6719DD6BB8EC16F665D67C5344C7B3E1279B0445CEF6AFFBD71D97A514A4",
}
SCENARIOS = ("Slow", "Base", "High")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            default=lambda value: value.item() if isinstance(value, np.generic) else str(value),
        )
        + "\n",
        encoding="utf-8",
    )


def verify_2040_price_source() -> dict[str, Any]:
    """Verify B8D and the one-horizon price-input contract without reading 2050."""

    sources = yaml.safe_load(PRODUCTION_SOURCES.read_text(encoding="utf-8"))
    source = sources["sources"][2040]
    if source["phase"] != "ETX-7B8D" or source["status"] != "ACCEPTED_PRODUCTION_PRICE_SOURCE":
        raise RuntimeError("STAGE_B_2040_SOURCE_IS_NOT_ETX7B8D")
    observed_hashes = {
        "receipt_sha256": sha256_file(ROOT / source["receipt"]),
        "result_manifest_sha256": sha256_file(ROOT / source["result_manifest"]),
        "market_prices_sha256": sha256_file(ROOT / source["market_prices"]),
    }
    if observed_hashes != EXPECTED_B8D_HASHES:
        raise RuntimeError(
            f"STAGE_B_2040_B8D_HASH_MISMATCH: expected={EXPECTED_B8D_HASHES}; observed={observed_hashes}"
        )

    raw = pd.read_parquet(ROOT / source["market_prices"])
    timestamps = pd.to_datetime(raw["snapshot"], utc=True, errors="raise")
    unique_timestamps = pd.DatetimeIndex(timestamps.drop_duplicates().sort_values())
    contiguous = bool(
        len(unique_timestamps) == 8760
        and unique_timestamps.is_unique
        and (unique_timestamps.to_series().diff().dropna() == pd.Timedelta(hours=1)).all()
    )
    required_markets = set(PRICE_MARKETS)
    observed_markets = set(raw["name"].astype(str))
    required = raw.loc[raw["name"].astype(str).isin(required_markets)].copy()
    finite = bool(np.isfinite(pd.to_numeric(required["marginal_price_EUR_per_MWh"]).to_numpy()).all())
    unique_keys = not required.assign(snapshot=timestamps.loc[required.index]).duplicated(
        ["snapshot", "name"]
    ).any()
    exact_coverage = bool(
        observed_markets == set(PRICE_MARKETS) | {"IT", "HR"}
        and set(required["name"].astype(str)) == required_markets
        and len(required) == 8760 * len(required_markets)
        and required.groupby("name")["snapshot"].nunique().eq(8760).all()
    )

    contract = yaml.safe_load(PRICE_CONTRACT.read_text(encoding="utf-8"))
    mapping = contract["mapping"]
    mapping_exact = mapping == PRICE_MAPPING
    corsica = contract["corsica"]
    corsica_ok = corsica["price_series_required"] is False and corsica["stage_a_node"] is False
    inherited = contract["horizon_governance"][2040]["inherited_by_scenarios"]
    inherited_ok = inherited == ["2040_Slow", "2040_Base", "2040_High"]
    passed = all((contiguous, finite, unique_keys, exact_coverage, mapping_exact, corsica_ok, inherited_ok))
    if not passed:
        raise RuntimeError("STAGE_B_2040_PRICE_SOURCE_QA_FAILED")
    return {
        "status": "PASS",
        "source_phase": "ETX-7B8D",
        "source_gate": source["required_gate"],
        "source_market_price_path": source["market_prices"],
        "hashes": observed_hashes,
        "hourly_observations_per_market": 8760,
        "chronology_unique_hours": len(unique_timestamps),
        "chronology_contiguous_hourly": contiguous,
        "chronology_timezone": "UTC_NORMALIZED_FROM_ACCEPTED_SOURCE_TIMESTAMPS",
        "duplicate_required_market_timestamp_keys": 0 if unique_keys else 1,
        "required_external_markets": sorted(required_markets),
        "observed_source_markets": sorted(observed_markets),
        "required_market_coverage_exact": exact_coverage,
        "finite_prices": finite,
        "market_to_zone_mapping": mapping,
        "market_to_zone_mapping_exact": mapping_exact,
        "corsica_has_no_price_series": corsica_ok,
        "scenario_inheritance": inherited,
        "scenario_inheritance_exact": inherited_ok,
        "source_read_only": True,
        "2050_source_read_or_resolved": False,
    }


def _subset(frame: pd.DataFrame, scenario: str) -> pd.DataFrame:
    return frame.loc[
        frame["year"].astype(int).eq(2040)
        & frame["scenario"].astype(str).str.casefold().eq(scenario.casefold())
    ].copy()


def reconcile_2040_static_scenarios() -> pd.DataFrame:
    """Reconcile the frozen seven-zone static contracts for all 2040 scenarios."""

    demand = pd.read_csv(STATIC / "MEM_Annual_Zonal_Demand_Contract.csv")
    generators = pd.read_csv(STATIC / "MEM_generators_static_final.csv")
    storage = pd.read_csv(STATIC / "MEM_storage_static_final.csv")
    phs = pd.read_csv(STATIC / "MEM_PHS_Static_Runtime_Contract.csv")
    hydro = pd.read_csv(STATIC / "MEM_Hydro_Static_Component_Mapping.csv")
    internal = pd.read_csv(STATIC / "MEM_Interzonal_Static_Contract.csv")
    external = pd.read_csv(STATIC / "MEM_External_Interface_Static_Contract.csv")

    phs_values = {
        "phs_discharge_MW": float(phs["discharge_power_MW_NET"].sum()),
        "phs_pump_MW": float(phs["pump_power_MW"].sum()),
        "phs_operational_energy_MWh": float(phs["operational_energy_MWh"].sum()),
    }
    phs_ok = (
        math.isclose(phs_values["phs_discharge_MW"], 7252.3, abs_tol=1e-6)
        and math.isclose(phs_values["phs_pump_MW"], 6400.0, abs_tol=1e-6)
        and math.isclose(phs_values["phs_operational_energy_MWh"], 53000.0, abs_tol=1e-6)
    )
    hydro_ok = len(hydro) == 35 and math.isclose(float(hydro["p_nom_MW_NET"].sum()), 23294.0, abs_tol=1e-6)
    external_price_rows = external.loc[external["price_series_required"].astype(str).str.casefold().eq("true")]
    external_ok = (
        set(external_price_rows["external_market"].astype(str)) == set(PRICE_MARKETS)
        and external_price_rows.groupby("external_market").size().eq(2).all()
        and len(external.loc[external["external_market"].eq("CORS")]) == 4
    )

    rows: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        d = _subset(demand, scenario)
        g = _subset(generators, scenario)
        s = _subset(storage, scenario)
        n = _subset(internal, scenario)
        annual_mwh = float(d["annual_zonal_demand_MWh"].sum())
        annual_control_mwh = float(d["annual_national_demand_TWh"].iloc[0]) * 1_000_000.0
        zones_ok = all(set(frame["zone"].astype(str)) == set(ZONES) for frame in (d, g, s))
        fixed_generation = not g["p_nom_extendable"].map(parse_bool).any()
        fixed_storage = not s[["p_nom_extendable", "e_nom_extendable"]].map(parse_bool).any().any()
        internal_fixed = len(n) == 20 and n["directional_flag"].map(parse_bool).all()
        no_external_fleet = set(g["zone"].astype(str)) <= set(ZONES)
        nuclear = g.loc[g["carrier"].astype(str).str.contains("nuclear", case=False, na=False)]
        passed = all(
            (
                len(d) == 7,
                math.isclose(annual_mwh, annual_control_mwh, abs_tol=1e-3),
                zones_ok,
                len(g) > 0,
                fixed_generation,
                no_external_fleet,
                len(s) > 0,
                fixed_storage,
                phs_ok,
                hydro_ok,
                internal_fixed,
                external_ok,
            )
        )
        rows.append(
            {
                "year": 2040,
                "scenario": scenario,
                "status": "PASS" if passed else "FAIL",
                "italian_zones": "|".join(sorted(ZONES)),
                "zone_count": len(set(d["zone"].astype(str))),
                "annual_demand_MWh": annual_mwh,
                "annual_demand_control_MWh": annual_control_mwh,
                "generator_rows": len(g),
                "generator_capacity_MW": float(g["p_nom_MW"].sum()),
                "generator_capacity_fixed": fixed_generation,
                "external_fleet_rows": int((~g["zone"].astype(str).isin(ZONES)).sum()),
                "nuclear_rows": len(nuclear),
                "nuclear_capacity_MW": float(nuclear["p_nom_MW"].sum()),
                "bess_rows": len(s),
                "bess_discharge_MW": float(s["discharge_power_MW"].sum()),
                "bess_energy_MWh": float(s["energy_capacity_MWh"].sum()),
                "bess_capacity_fixed": fixed_storage,
                **phs_values,
                "phs_contract_exact": phs_ok,
                "hydro_mapping_rows": len(hydro),
                "hydro_turbine_MW": float(hydro["p_nom_MW_NET"].sum()),
                "hydro_contract_exact": hydro_ok,
                "internal_directional_link_rows": len(n),
                "internal_network_fixed": internal_fixed,
                "external_interface_rows": len(external),
                "external_price_markets": "|".join(sorted(PRICE_MARKETS)),
                "external_interface_contract_exact": external_ok,
                "co2_price_values_EUR2025_per_t": "|".join(
                    str(value) for value in sorted(g["CO2_price_EUR2025_per_t"].astype(float).unique())
                ),
                "chronology_runtime_status": "ACCEPTED_C2019_UTC_8760_HASH_LOCKED_RUNTIME",
                "production_solve_authorized": bool(
                    yaml.safe_load(APPROVAL_GATES.read_text(encoding="utf-8"))["stage_b_2040_production"]
                    ["scenario_authorization"].get(scenario, False)
                ),
            }
        )
    result = pd.DataFrame(rows)
    if not result["status"].eq("PASS").all():
        raise RuntimeError(f"STAGE_B_2040_STATIC_RECONCILIATION_FAILED: {result.to_dict('records')}")
    return result


def assess_runtime_readiness() -> dict[str, Any]:
    missing = [filename for filename in RUNTIME_FILES.values() if not (ACCEPTED_RUNTIME / filename).exists()]
    candidate_assumptions = ROOT / "config/runtime_assumptions_candidates.yaml"
    b10_receipt_path = ROOT / "qa/stage_a/etx7b10_2040/MEM_ETX7B10_2040_Run_Receipt_v1.0.json"
    b10_receipt_pass = False
    if b10_receipt_path.exists():
        receipt = json.loads(b10_receipt_path.read_text(encoding="utf-8"))
        b10_receipt_pass = (
            receipt.get("status") == "PASS"
            and receipt.get("gate") == "ETX7B10_2040_STAGE_A_TO_STAGE_B_PRICE_HANDOFF_COMPLETE"
        )
    manifest_verified = False
    manifest_error = ""
    if not missing:
        try:
            verify_accepted_runtime_manifest(ACCEPTED_RUNTIME, expected_year=2040)
            manifest_verified = True
        except (FileNotFoundError, RuntimeError) as exc:
            manifest_error = str(exc)
    blockers: list[str] = []
    if missing:
        blockers.append("ACCEPTED_SEVEN_ZONE_HOURLY_RUNTIME_BUNDLE_IS_INCOMPLETE")
    elif not manifest_verified:
        blockers.append("ACCEPTED_RUNTIME_MANIFEST_OR_MEMBER_HASH_FAILED")
    if not b10_receipt_pass:
        blockers.append("ETX7B10_2040_RECEIPT_NOT_YET_EXECUTED")
    approvals = yaml.safe_load(APPROVAL_GATES.read_text(encoding="utf-8"))
    production_gate = approvals.get("stage_b_2040_production", {})
    successors_authorized = (
        production_gate.get("status") == "2040_BASE_ACCEPTED_SLOW_HIGH_READY_FOR_MANUAL_GUROBI_EXECUTION"
        and production_gate.get("execution_enabled") is True
        and int(production_gate.get("authorized_year", -1)) == 2040
        and production_gate.get("authorized_mode") == "FULL_YEAR_CANONICAL"
        and production_gate.get("solver", {}).get("name") == "gurobi"
        and production_gate.get("scenario_authorization") == {"Slow": True, "Base": False, "High": True}
        and production_gate.get("base_production_acceptance") == "ACCEPTED_IMMUTABLE"
        and production_gate.get("stage_b_2050_authorized") is False
    )
    closure_complete = (
        production_gate.get("status") == "STAGE_B_2040_THREE_SCENARIO_PRODUCTION_COMPLETE"
        and production_gate.get("execution_enabled") is False
        and production_gate.get("scenario_authorization") == {"Slow": False, "Base": False, "High": False}
        and production_gate.get("accepted_scenarios") == ["Slow", "Base", "High"]
        and production_gate.get("stage_b_2050_authorized") is False
    )
    if not successors_authorized and not closure_complete:
        blockers.append("STAGE_B_2040_SLOW_HIGH_PRODUCTION_AUTHORIZATION_NOT_EXACT")
    ready = bool(not missing and manifest_verified and b10_receipt_pass and successors_authorized)
    return {
        "accepted_runtime_directory": relative_path(ACCEPTED_RUNTIME),
        "accepted_runtime_files_required": list(RUNTIME_FILES.values()),
        "accepted_runtime_files_missing": missing,
        "accepted_runtime_bundle_complete": not missing,
        "accepted_runtime_manifest_hash_verified": manifest_verified,
        "accepted_runtime_manifest_error": manifest_error,
        "candidate_runtime_assumptions_path": relative_path(candidate_assumptions),
        "candidate_runtime_assumptions_accepted_as_authority": False,
        "candidate_assumptions_remaining": 0,
        "b10_2040_receipt": relative_path(b10_receipt_path),
        "b10_2040_receipt_pass": b10_receipt_pass,
        "b10_2040_ready_to_execute": False,
        "b10_2040_status": "COMPLETE" if b10_receipt_pass else "INCOMPLETE",
        "stage_b_2040_static_contracts_ready": True,
        "stage_b_2040_unsolved_network_build_ready": bool(not missing and manifest_verified and b10_receipt_pass),
        "stage_b_2040_ready_to_run": ready,
        "stage_b_2040_authorized_scenarios": ["Slow", "High"] if successors_authorized else [],
        "stage_b_2040_unauthorized_scenarios": ["Base"] if successors_authorized else ["Slow", "Base", "High"],
        "stage_b_2040_base_acceptance": "ACCEPTED_IMMUTABLE" if successors_authorized or closure_complete else None,
        "stage_b_2040_authorized_mode": "FULL_YEAR_CANONICAL" if successors_authorized else None,
        "stage_b_2040_authorized_solver": "gurobi" if successors_authorized else None,
        "stage_b_2040_production_complete": closure_complete,
        "stage_b_2040_blockers": blockers,
        "stage_b_2050_authorized": False,
        "production_optimization_executed": True,
    }


def prepare(*, write_artifacts: bool = False) -> dict[str, Any]:
    if write_artifacts:
        raise RuntimeError("The pre-2040 preparation receipts are historical and immutable; use read-only verification")
    source = verify_2040_price_source()
    scenarios = reconcile_2040_static_scenarios()
    readiness = assess_runtime_readiness()
    approvals = yaml.safe_load(APPROVAL_GATES.read_text(encoding="utf-8"))
    manual = approvals["stage_a_manual_gates"]
    production_gate = approvals["stage_b_2040_production"]
    successor_governance = (
        manual["b10_2040_status"] == "ETX7B10_2040_STAGE_A_TO_STAGE_B_PRICE_HANDOFF_COMPLETE"
        and manual["stage_b_2040_authorized"] is True
        and manual["stage_b_2040_authorized_scenarios"] == ["Slow", "High"]
        and manual["stage_b_2050_authorized"] is False
        and production_gate["execution_enabled"] is True
        and production_gate["authorized_year"] == 2040
        and production_gate["scenario_authorization"] == {"Slow": True, "Base": False, "High": True}
        and production_gate["base_production_acceptance"] == "ACCEPTED_IMMUTABLE"
        and production_gate["authorized_mode"] == "FULL_YEAR_CANONICAL"
        and production_gate["solver"]["name"] == "gurobi"
    )
    closure_governance = (
        manual["b10_2040_status"] == "ETX7B10_2040_STAGE_A_TO_STAGE_B_PRICE_HANDOFF_COMPLETE"
        and manual["stage_b_2040_authorized"] is False
        and manual["stage_b_2040_authorized_scenarios"] == []
        and manual["stage_b_2050_authorized"] is False
        and production_gate["status"] == "STAGE_B_2040_THREE_SCENARIO_PRODUCTION_COMPLETE"
        and production_gate["execution_enabled"] is False
        and production_gate["scenario_authorization"] == {"Slow": False, "Base": False, "High": False}
        and production_gate["accepted_scenarios"] == ["Slow", "Base", "High"]
        and production_gate["solver"]["name"] == "gurobi"
    )
    governance_ok = successor_governance or closure_governance
    qa = {
        "status": "PASS" if governance_ok else "FAIL",
        "scope": "2040_ONLY_CURRENT_PRODUCTION_GATE_NO_NEW_SOLVE",
        "price_source": source,
        "static_scenarios": {
            "rows": len(scenarios),
            "all_pass": bool(scenarios["status"].eq("PASS").all()),
            "scenarios": scenarios["scenario"].tolist(),
        },
        "runtime_readiness": readiness,
        "governance": {
            "b10_2040_authorized": manual["b10_2040_authorized"],
            "b10_2040_status": manual["b10_2040_status"],
            "b10_2050_authorized": manual["b10_2050_authorized"],
            "stage_b_2040_authorized": manual["stage_b_2040_authorized"],
            "stage_b_2040_authorized_scenarios": manual["stage_b_2040_authorized_scenarios"],
            "stage_b_2040_authorized_mode": production_gate["authorized_mode"],
            "stage_b_2040_authorized_solver": production_gate["solver"]["name"],
            "stage_b_2050_authorized": manual["stage_b_2050_authorized"],
            "global_b10_authorized": manual["b10_authorized"],
            "global_stage_b_authorized": manual["stage_b_authorized"],
            "horizon_scoping_pass": governance_ok,
        },
        "production_optimization_executed": True,
    }
    if qa["status"] != "PASS":
        raise RuntimeError(f"STAGE_B_2040_PREPARATION_GOVERNANCE_FAILED: {qa['governance']}")
    return qa


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify the independent 2040 Stage-B preparation path")
    parser.add_argument("command", choices=["verify"])
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "verify":
        result = prepare(write_artifacts=False)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "b10_2040_ready_to_execute": False,
                    "accepted_runtime_manifest_hash_verified": result["runtime_readiness"]["accepted_runtime_manifest_hash_verified"],
                    "stage_b_2040_ready_to_run": result["runtime_readiness"]["stage_b_2040_ready_to_run"],
                    "authorized_scenarios": result["runtime_readiness"]["stage_b_2040_authorized_scenarios"],
                    "base_status": "ACCEPTED_IMMUTABLE",
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()

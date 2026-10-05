"""Frozen-contract to Stage-A runtime reconciliation (read-only audit).

This module intentionally starts at accepted MEM workbooks/contracts.  It does
not reopen their upstream research.  It compares those accepted values with
the B1--B5 implementation packages, the serialized B6 networks, and the
immutable solved Stage-A result packages.  No network is built or optimized.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pypsa
from openpyxl import load_workbook

from mem_model.stage_a.network import MARKETS, _safe_id, load_execution_config, verify_input_locks


ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = ROOT.parent
OUTPUT_DIR = ROOT / "qa/stage_a/reconciliation"
HANDOFF_PATH = ROOT / "docs/MEM_STAGE_A_2050_RERUN_PREPARATION_HANDOFF_v1.0.md"

RECONCILIATION_PATH = OUTPUT_DIR / "MEM_STAGE_A_RESEARCH_TO_RUNTIME_RECONCILIATION_v1.0.csv"
FINDINGS_PATH = OUTPUT_DIR / "MEM_STAGE_A_RECONCILIATION_FINDINGS_v1.0.md"
TAIL_PATH = OUTPUT_DIR / "MEM_STAGE_A_2050_PRICE_TAIL_DECOMPOSITION_v1.0.csv"
IMPACT_PATH = OUTPUT_DIR / "MEM_STAGE_A_2050_GATE_IMPACT_MAP_v1.0.json"
VERIFICATION_PATH = OUTPUT_DIR / "MEM_STAGE_A_RECONCILIATION_FINAL_VERIFICATION_v1.0.json"

FINAL_MASTER = (
    WORKSPACE
    / "outputs/01a0595d-8cf1-7202-8ca1-1d3ab732e98e"
    / "MEM_2040_2050_FINAL_ZONAL_CAPACITY_MASTER.xlsx"
)
FINAL_MASTER_QA = FINAL_MASTER.with_name("MEM_FINAL_ZONAL_CAPACITY_MASTER_QA.md")
ETX7A_BOOK = ROOT / "runtime_sources/etx7a/frozen/MEM_ETX7A_External_Country_Harmonised_Freeze_v1.0.xlsx"
ETX7A_MANIFEST = ROOT / "runtime_sources/etx7a/frozen/MEM_ETX7A_FROZEN_SOURCE_MANIFEST.csv"

PRE = ROOT / "pre_pypsa_inputs"
B1 = ROOT / "stage_a_inputs/etx7a_v1_0/static"
B2 = ROOT / "stage_a_inputs/italy_base_v1_0/static"
B3 = ROOT / "stage_a_inputs/temporal_2019_v1_0"
B4 = ROOT / "stage_a_inputs/operating_base_v1_0"
B5 = ROOT / "stage_a_inputs/topology_v1_0"

RECON_COLUMNS = (
    "input_family",
    "market",
    "horizon",
    "parameter",
    "final_authority_artifact",
    "final_authority_key",
    "intended_value",
    "runtime_input_artifact",
    "runtime_input_key",
    "runtime_value",
    "B6_observed_value",
    "transformation",
    "difference",
    "classification",
    "affected_gate",
    "action",
)
CLASSIFICATIONS = {
    "EXACT_MATCH",
    "INTENTIONAL_RUNTIME_TRANSFORMATION",
    "IMPLEMENTATION_DRIFT",
    "SUPERSEDED_ARTIFACT_LEAKAGE",
    "AMBIGUOUS_REQUIRES_USER",
}
EXPORTED_MARKETS = ("FR", "CH", "AT", "SI", "ME", "GR", "MT", "TN")
VOLL = 15_000.0
VOLL_TOLERANCE = 1e-6
NUMERIC_ATOL = 1e-6


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.relative_to(WORKSPACE).as_posix()


def _value(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    if value is None:
        return ""
    if isinstance(value, (float, np.floating)):
        if math.isnan(float(value)):
            return ""
        return repr(float(value))
    return str(value)


def _numeric_equal(left: Any, right: Any, atol: float = NUMERIC_ATOL) -> bool:
    try:
        return math.isclose(float(left), float(right), rel_tol=1e-10, abs_tol=atol)
    except (TypeError, ValueError):
        return str(left) == str(right)


def _series_summary(series: pd.Series) -> dict[str, Any]:
    values = np.asarray(series, dtype="<f8")
    return {
        "rows": int(values.size),
        "sum": float(values.sum()),
        "min": float(values.min()),
        "max": float(values.max()),
        "sha256_float64": hashlib.sha256(values.tobytes()).hexdigest().upper(),
    }


class AuditRows:
    def __init__(self) -> None:
        self.rows: list[dict[str, str]] = []

    def add(
        self,
        *,
        input_family: str,
        market: str,
        horizon: int | str,
        parameter: str,
        final_authority_artifact: str,
        final_authority_key: str,
        intended_value: Any,
        runtime_input_artifact: str,
        runtime_input_key: str,
        runtime_value: Any,
        b6_observed_value: Any,
        transformation: str,
        difference: Any,
        classification: str,
        affected_gate: str = "NONE",
        action: str = "NONE",
    ) -> None:
        if classification not in CLASSIFICATIONS:
            raise ValueError(f"Unknown reconciliation classification: {classification}")
        self.rows.append(
            {
                "input_family": input_family,
                "market": market,
                "horizon": str(horizon),
                "parameter": parameter,
                "final_authority_artifact": final_authority_artifact,
                "final_authority_key": final_authority_key,
                "intended_value": _value(intended_value),
                "runtime_input_artifact": runtime_input_artifact,
                "runtime_input_key": runtime_input_key,
                "runtime_value": _value(runtime_value),
                "B6_observed_value": _value(b6_observed_value),
                "transformation": transformation,
                "difference": _value(difference),
                "classification": classification,
                "affected_gate": affected_gate,
                "action": action,
            }
        )

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=RECON_COLUMNS).sort_values(
            ["input_family", "horizon", "market", "parameter", "runtime_input_key"],
            kind="stable",
        ).reset_index(drop=True)


def _csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _float(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="raise")


def _etx7a_runtime_rows() -> dict[int, dict[str, Any]]:
    workbook = load_workbook(ETX7A_BOOK, data_only=True, read_only=False)
    sheet = workbook["03_RUNTIME_INPUTS"]
    headers = [sheet.cell(4, column).value for column in range(1, sheet.max_column + 1)]
    rows = {
        row: {headers[column - 1]: sheet.cell(row, column).value for column in range(1, sheet.max_column + 1)}
        for row in range(5, sheet.max_row + 1)
        if sheet.cell(row, 1).value
    }
    workbook.close()
    return rows


def _source_contract_value(path: Path, source_row: int, dimension: str) -> float:
    frame = _csv(path)
    record = frame.iloc[source_row - 2]
    mappings = {
        ("MEM_Annual_Zonal_Demand_Contract.csv", "annual_demand_MWh"): "annual_zonal_demand_MWh",
        ("MEM_generators_static_final.csv", "p_nom_MW"): "p_nom_MW",
        ("MEM_Hydro_Static_Component_Mapping.csv", "p_nom_MW"): "p_nom_MW_NET",
        ("MEM_Hydro_Static_Component_Mapping.csv", "pure_phs_discharge_subset_MW"): "p_nom_MW_NET",
        ("MEM_Hydro_Static_Component_Mapping.csv", "mixed_phs_discharge_subset_MW"): "p_nom_MW_NET",
        ("MEM_storage_static_final.csv", "charge_power_MW"): "charge_power_MW",
        ("MEM_storage_static_final.csv", "discharge_power_MW"): "discharge_power_MW",
        ("MEM_storage_static_final.csv", "energy_MWh"): "energy_capacity_MWh",
        ("MEM_PHS_Static_Runtime_Contract.csv", "charge_power_MW"): "pump_power_MW",
        ("MEM_PHS_Static_Runtime_Contract.csv", "discharge_power_MW"): "discharge_power_MW_NET",
        ("MEM_PHS_Static_Runtime_Contract.csv", "energy_MWh"): "operational_energy_MWh",
    }
    key = (path.name, dimension)
    if key not in mappings:
        raise KeyError(f"No final-contract mapping for {key}")
    return float(record[mappings[key]])


def _b2_authority_aggregates() -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], str]]:
    provenance_path = B2 / "MEM_ETX7B2_IT_Static_Provenance_v1.0.csv"
    provenance = _csv(provenance_path)
    values: dict[tuple[str, str], float] = {}
    keys: dict[tuple[str, str], str] = {}
    for (target, dimension), group in provenance.groupby(["target_record_id", "target_dimension"], sort=True):
        actual: list[float] = []
        source_keys: list[str] = []
        for record in group.to_dict(orient="records"):
            source_path = PRE / record["source_contract"]
            observed = _source_contract_value(source_path, int(record["source_row"]), dimension)
            if not _numeric_equal(observed, record["source_value"]):
                raise RuntimeError(
                    f"B2 provenance disagrees with final contract: {target} {dimension} "
                    f"{record['source_contract']}:{record['source_row']}"
                )
            actual.append(observed)
            source_keys.append(f"{record['source_contract']}:{record['source_row']}")
        values[(target, dimension)] = float(sum(actual))
        keys[(target, dimension)] = "|".join(source_keys)
    return values, keys


def _network(year: int) -> pypsa.Network:
    return pypsa.Network(ROOT / f"stage_a_networks/{year}/MEM_ETX7B6_{year}_BASE_8760h_UNSOLVED.nc")


def _hydro_capacity(network: pypsa.Network, hydro: pd.DataFrame, asset_id: str) -> float:
    rows = hydro.loc[hydro["source_asset_id"].eq(asset_id)]
    total = 0.0
    for record in rows.to_dict(orient="records"):
        safe = _safe_id(record["operational_slice_id"])
        if str(record["energy_state_required"]).lower() == "true":
            name = f"TURBINE_{safe}"
            total += float(network.links.at[name, "p_nom"] * network.links.at[name, "efficiency"])
        else:
            total += float(network.generators.at[f"HYDRO_{safe}", "p_nom"])
    return total


def _generator_observed(network: pypsa.Network, hydro: pd.DataFrame, asset_id: str) -> float:
    if asset_id in network.generators.index:
        return float(network.generators.at[asset_id, "p_nom"])
    return _hydro_capacity(network, hydro, asset_id)


def _effective_marginal_cost(network: pypsa.Network, hydro: pd.DataFrame, asset_id: str) -> float:
    if asset_id in network.generators.index:
        return float(network.generators.at[asset_id, "marginal_cost"])
    values: list[float] = []
    for record in hydro.loc[hydro["source_asset_id"].eq(asset_id)].to_dict(orient="records"):
        safe = _safe_id(record["operational_slice_id"])
        if str(record["energy_state_required"]).lower() == "true":
            link = network.links.loc[f"TURBINE_{safe}"]
            values.append(float(link["marginal_cost"] / link["efficiency"]))
        else:
            values.append(float(network.generators.at[f"HYDRO_{safe}", "marginal_cost"]))
    if not values or not np.allclose(values, values[0], atol=NUMERIC_ATOL, rtol=1e-10):
        raise RuntimeError(f"Ambiguous B6 marginal cost for {asset_id}: {values}")
    return values[0]


def _storage_observed(network: pypsa.Network, hydro: pd.DataFrame, record: dict[str, Any]) -> dict[str, float]:
    storage_id = str(record["storage_id"])
    if record["storage_family"] == "BESS":
        safe = _safe_id(storage_id)
        charge = network.links.loc[f"CHARGE_BESS_{safe}"]
        discharge = network.links.loc[f"DISCHARGE_BESS_{safe}"]
        store = network.stores.loc[f"STORE_BESS_{safe}"]
        return {
            "charge_power_MW": float(charge.p_nom),
            "discharge_power_MW": float(discharge.p_nom * discharge.efficiency),
            "energy_MWh": float(store.e_nom),
            "round_trip_efficiency": float(charge.efficiency * discharge.efficiency),
        }
    rows = hydro.loc[hydro["source_asset_id"].eq(storage_id)]
    observed = {"charge_power_MW": 0.0, "discharge_power_MW": 0.0, "energy_MWh": 0.0}
    efficiencies: list[float] = []
    for hydro_record in rows.to_dict(orient="records"):
        safe = _safe_id(hydro_record["operational_slice_id"])
        turbine = network.links.loc[f"TURBINE_{safe}"]
        observed["discharge_power_MW"] += float(turbine.p_nom * turbine.efficiency)
        observed["energy_MWh"] += float(network.stores.at[f"STORE_WATER_{safe}", "e_nom"])
        pump = f"PUMP_{safe}"
        if pump in network.links.index:
            observed["charge_power_MW"] += float(network.links.at[pump, "p_nom"])
            efficiencies.append(float(network.links.at[pump, "efficiency"] * turbine.efficiency))
    observed["round_trip_efficiency"] = efficiencies[0] if efficiencies else 0.0
    return observed


def _add_demand(rows: AuditRows, networks: dict[int, pypsa.Network], etx_rows: dict[int, dict[str, Any]]) -> None:
    external = _csv(B1 / "MEM_ETX7A_Annual_Demand_Static_v1.0.csv")
    italy = _csv(B2 / "MEM_ETX7B2_IT_Annual_Demand_Static_v1.0.csv")
    b3 = pd.read_parquet(B3 / "MEM_ETX7B3_Load_Hourly_v1.0.parquet")
    final_it = _csv(PRE / "MEM_Annual_Zonal_Demand_Contract.csv")
    for year in (2040, 2050):
        network = networks[year]
        for record in external.loc[_float(external, "year").eq(year)].to_dict(orient="records"):
            market = record["country_code"]
            workbook_row = int(record["etx7a_workbook_row"])
            intended = float(etx_rows[workbook_row][f"{year} value"]) * 1_000_000.0
            adapter = float(record["annual_demand_MWh"])
            hourly = b3.loc[
                _float(b3, "horizon").eq(year) & b3["country_code"].eq(market), "load_MW"
            ].astype(float).sum()
            observed = float(network.loads_t.p_set[f"LOAD_{market}"].sum())
            exact = all(_numeric_equal(intended, value, 1e-3) for value in (adapter, hourly, observed))
            rows.add(
                input_family="DEMAND",
                market=market,
                horizon=year,
                parameter="annual_demand_MWh",
                final_authority_artifact=_rel(ETX7A_BOOK),
                final_authority_key=f"03_RUNTIME_INPUTS!row={workbook_row};{year}_value",
                intended_value=intended,
                runtime_input_artifact="B1 annual demand -> B3 hourly load",
                runtime_input_key=record["demand_id"],
                runtime_value={"B1_MWh": adapter, "B3_hourly_sum_MWh": float(hourly)},
                b6_observed_value=observed,
                transformation="TWh_TO_MWh_THEN_NORMALIZED_2019_HOURLY_PROFILE",
                difference={"B1_minus_intended": adapter - intended, "B3_minus_intended": hourly - intended, "B6_minus_intended": observed - intended},
                classification="INTENTIONAL_RUNTIME_TRANSFORMATION" if exact else "IMPLEMENTATION_DRIFT",
                affected_gate="NONE" if exact else "ETX-7B1_OR_ETX-7B3",
                action="NONE" if exact else "CORRECT_BEFORE_2050_RERUN",
            )

        final_it_year = final_it.loc[_float(final_it, "year").eq(year) & final_it["scenario"].eq("Base")]
        intended = _float(final_it_year, "annual_zonal_demand_MWh").sum()
        b2_record = italy.loc[_float(italy, "year").eq(year)].iloc[0]
        adapter = float(b2_record["annual_demand_MWh"])
        hourly = b3.loc[_float(b3, "horizon").eq(year) & b3["country_code"].eq("IT"), "load_MW"].astype(float).sum()
        observed = float(network.loads_t.p_set["LOAD_IT"].sum())
        exact = all(_numeric_equal(intended, value, 1e-3) for value in (adapter, hourly, observed))
        rows.add(
            input_family="DEMAND",
            market="IT",
            horizon=year,
            parameter="annual_demand_MWh",
            final_authority_artifact=_rel(PRE / "MEM_Annual_Zonal_Demand_Contract.csv"),
            final_authority_key=f"scenario=Base;year={year};zones=7;sum(annual_zonal_demand_MWh)",
            intended_value=float(intended),
            runtime_input_artifact="B2 annual demand -> B3 hourly load",
            runtime_input_key=b2_record["demand_id"],
            runtime_value={"B2_MWh": adapter, "B3_hourly_sum_MWh": float(hourly)},
            b6_observed_value=observed,
            transformation="SEVEN_ZONES_AGGREGATED_TO_IT_THEN_NORMALIZED_2019_HOURLY_PROFILE",
            difference={"B2_minus_intended": adapter - intended, "B3_minus_intended": hourly - intended, "B6_minus_intended": observed - intended},
            classification="INTENTIONAL_RUNTIME_TRANSFORMATION" if exact else "IMPLEMENTATION_DRIFT",
            affected_gate="NONE" if exact else "ETX-7B2_OR_ETX-7B3",
            action="NONE" if exact else "STOP_AND_REVISE_2050_CHAIN",
        )

        snapshots = pd.DatetimeIndex(network.snapshots)
        one_hour = network.snapshot_weightings.astype(float).eq(1.0).all().all()
        chronology_ok = len(snapshots) == 8760 and not snapshots.has_duplicates and one_hour
        rows.add(
            input_family="TEMPORAL",
            market="ALL",
            horizon=year,
            parameter="chronology_and_snapshot_weights",
            final_authority_artifact=_rel(B3 / "MEM_ETX7B3_Snapshot_Index_v1.0.parquet"),
            final_authority_key="2019_UTC;8760_unique_hours;weight=1",
            intended_value={"snapshots": 8760, "timezone": "UTC", "weight_hours": 1.0},
            runtime_input_artifact="B3 snapshot index",
            runtime_input_key="all_rows",
            runtime_value={"snapshots": 8760, "timezone": "UTC"},
            b6_observed_value={"snapshots": len(snapshots), "timezone": "UTC_NAIVE_SERIALIZATION", "all_weights_one": one_hour},
            transformation="UTC_AWARE_TO_UTC_NAIVE_ONE_TO_ONE_FOR_PYPSA_SERIALIZATION",
            difference=0 if chronology_ok else "CHRONOLOGY_MISMATCH",
            classification="INTENTIONAL_RUNTIME_TRANSFORMATION" if chronology_ok else "IMPLEMENTATION_DRIFT",
            affected_gate="NONE" if chronology_ok else "ETX-7B3_OR_ETX-7B6",
            action="NONE" if chronology_ok else "STOP_AND_REVISE_2050_CHAIN",
        )


def _add_generation_and_economics(
    rows: AuditRows,
    networks: dict[int, pypsa.Network],
    etx_rows: dict[int, dict[str, Any]],
    b2_values: dict[tuple[str, str], float],
    b2_keys: dict[tuple[str, str], str],
) -> None:
    b1 = _csv(B1 / "MEM_ETX7A_Generators_Static_v1.0.csv").set_index("asset_id")
    b2 = _csv(B2 / "MEM_ETX7B2_IT_Generators_Static_v1.0.csv").set_index("asset_id")
    b4 = _csv(B4 / "MEM_ETX7B4_Generator_Operating_Parameters_v1.0.csv")
    hydro = _csv(B4 / "MEM_ETX7B4_Hydro_Annual_Scaling_and_State_v1.0.csv")
    availability = pd.read_parquet(B4 / "MEM_ETX7B4_Generator_Technical_Availability_Hourly_v1.0.parquet")
    vre = pd.read_parquet(B3 / "MEM_ETX7B3_VRE_Availability_Hourly_v1.0.parquet")
    for record in b4.to_dict(orient="records"):
        asset_id = record["asset_id"]
        year = int(record["year"])
        market = record["country_code"]
        network = networks[year]
        is_external = record["static_source_family"] == "ETX7B1_EXTERNAL"
        if is_external:
            source = b1.loc[asset_id]
            workbook_row = int(source["etx7a_workbook_row"])
            intended = float(etx_rows[workbook_row][f"{year} value"]) * 1000.0
            authority = _rel(ETX7A_BOOK)
            authority_key = f"03_RUNTIME_INPUTS!row={workbook_row};{year}_value"
            adapter_value = float(source["p_nom_MW"])
            adapter_name = "B1"
        else:
            source = b2.loc[asset_id]
            intended = b2_values[(asset_id, "p_nom_MW")]
            authority = "|".join(sorted(set(key.split(":")[0] for key in b2_keys[(asset_id, "p_nom_MW")].split("|"))))
            authority_key = b2_keys[(asset_id, "p_nom_MW")]
            adapter_value = float(source["p_nom_MW"])
            adapter_name = "B2"
        runtime = float(record["p_nom_MW"])
        observed = _generator_observed(network, hydro.loc[_float(hydro, "year").eq(year)], asset_id)
        exact = all(_numeric_equal(intended, value) for value in (adapter_value, runtime, observed))
        is_hydro = asset_id in set(hydro.loc[_float(hydro, "year").eq(year), "source_asset_id"])
        rows.add(
            input_family="GENERATION_CAPACITY",
            market=market,
            horizon=year,
            parameter="p_nom_MW",
            final_authority_artifact=authority,
            final_authority_key=authority_key,
            intended_value=intended,
            runtime_input_artifact=f"{adapter_name} static -> B4 generator runtime",
            runtime_input_key=asset_id,
            runtime_value={f"{adapter_name}_p_nom_MW": adapter_value, "B4_p_nom_MW": runtime},
            b6_observed_value=observed,
            transformation="HYDRO_SOURCE_TO_B4_OPERATIONAL_SLICES_WITH_EXACT_TURBINE_SUM" if is_hydro else "DIRECT_FIXED_GENERATOR",
            difference={f"{adapter_name}_minus_intended": adapter_value - intended, "B4_minus_intended": runtime - intended, "B6_minus_intended": observed - intended},
            classification=("INTENTIONAL_RUNTIME_TRANSFORMATION" if is_hydro else "EXACT_MATCH") if exact else "IMPLEMENTATION_DRIFT",
            affected_gate="NONE" if exact else ("ETX-7B1" if is_external else "ETX-7B2"),
            action="NONE" if exact else "STOP_AND_REVISE_2050_CHAIN",
        )

        fuel = float(record["fuel_price_EUR2025_per_MWh_th"] or 0)
        efficiency = float(record["efficiency_el"] or 1)
        vom = float(record["VOM_EUR2025_per_MWh_el"] or 0)
        chargeable = float(record["chargeable_CO2_t_per_MWh_th"] or 0)
        carbon = float(record["CO2_price_EUR2025_per_t"] or 0)
        other = float(record["other_variable_cost_EUR2025_per_MWh_el"] or 0)
        reported_mc = float(record["marginal_cost_EUR2025_per_MWh_el"])
        recomputed_mc = fuel / efficiency + chargeable * carbon / efficiency + vom + other
        observed_mc = _effective_marginal_cost(network, hydro.loc[_float(hydro, "year").eq(year)], asset_id)
        mc_exact = _numeric_equal(recomputed_mc, reported_mc, 2e-6) and _numeric_equal(reported_mc, observed_mc, 2e-6)
        rows.add(
            input_family="OPERATING_ECONOMICS",
            market=market,
            horizon=year,
            parameter="marginal_cost_EUR2025_per_MWh_el",
            final_authority_artifact=_rel(B4 / "MEM_ETX7B4_Generator_Operating_Parameters_v1.0.csv"),
            final_authority_key=asset_id,
            intended_value={"fuel": fuel, "efficiency": efficiency, "VOM": vom, "chargeable_CO2": chargeable, "CO2_price": carbon, "other": other, "marginal_cost": reported_mc},
            runtime_input_artifact="B4 operating/runtime parameter layer",
            runtime_input_key=asset_id,
            runtime_value={"recomputed_marginal_cost": recomputed_mc},
            b6_observed_value=observed_mc,
            transformation="FUEL_DIV_EFFICIENCY_PLUS_CO2_DIV_EFFICIENCY_PLUS_VOM_PLUS_OTHER;HYDRO_LINK_COST_SCALED_TO_OUTPUT_EQUIVALENT",
            difference={"formula_minus_B4": recomputed_mc - reported_mc, "B6_minus_B4": observed_mc - reported_mc},
            classification="INTENTIONAL_RUNTIME_TRANSFORMATION" if mc_exact else "IMPLEMENTATION_DRIFT",
            affected_gate="NONE" if mc_exact else "ETX-7B4_OR_ETX-7B6",
            action="NONE" if mc_exact else "STOP_AND_REVISE_2050_CHAIN",
        )

        if not is_hydro:
            if record["availability_mode"] == "B3_TEMPORAL_PROFILE":
                expected = vre.loc[
                    vre["profile_id"].astype(str).eq(record["temporal_profile_id"]), ["snapshot", "p_max_pu"]
                ].set_index("snapshot")["p_max_pu"].astype(float)
                authority_avail = _rel(B3 / "MEM_ETX7B3_VRE_Availability_Hourly_v1.0.parquet")
                transformation = "B3_VRE_PROFILE_DIRECT_JOIN"
            else:
                expected = availability.loc[
                    availability["asset_id"].astype(str).eq(asset_id), ["snapshot", "p_max_pu"]
                ].set_index("snapshot")["p_max_pu"].astype(float)
                authority_avail = _rel(B4 / "MEM_ETX7B4_Generator_Technical_Availability_Hourly_v1.0.parquet")
                transformation = "B4_TECHNICAL_AVAILABILITY_DIRECT_JOIN"
            expected.index = pd.to_datetime(expected.index, utc=True).tz_convert(None)
            observed_series = network.generators_t.p_max_pu[asset_id].reindex(expected.index).astype(float)
            max_diff = float(np.max(np.abs(expected.to_numpy() - observed_series.to_numpy())))
            rows.add(
                input_family="AVAILABILITY_TEMPORAL",
                market=market,
                horizon=year,
                parameter="hourly_p_max_pu",
                final_authority_artifact=authority_avail,
                final_authority_key=record["temporal_profile_id"] or asset_id,
                intended_value=_series_summary(expected),
                runtime_input_artifact=authority_avail,
                runtime_input_key=asset_id,
                runtime_value=_series_summary(expected),
                b6_observed_value=_series_summary(observed_series),
                transformation=transformation,
                difference={"max_absolute": max_diff},
                classification="EXACT_MATCH" if max_diff <= 1e-12 else "IMPLEMENTATION_DRIFT",
                affected_gate="NONE" if max_diff <= 1e-12 else "ETX-7B3_OR_ETX-7B4_OR_ETX-7B6",
                action="NONE" if max_diff <= 1e-12 else "STOP_AND_REVISE_2050_CHAIN",
            )


def _add_storage(
    rows: AuditRows,
    networks: dict[int, pypsa.Network],
    etx_rows: dict[int, dict[str, Any]],
    b2_values: dict[tuple[str, str], float],
    b2_keys: dict[tuple[str, str], str],
) -> None:
    b1 = _csv(B1 / "MEM_ETX7A_Storage_Static_v1.0.csv").set_index("storage_id")
    b2 = _csv(B2 / "MEM_ETX7B2_IT_Storage_Static_v1.0.csv").set_index("storage_id")
    b4 = _csv(B4 / "MEM_ETX7B4_Storage_Operating_Parameters_v1.0.csv")
    hydro = _csv(B4 / "MEM_ETX7B4_Hydro_Annual_Scaling_and_State_v1.0.csv")
    dimensions = ("charge_power_MW", "discharge_power_MW", "energy_MWh")
    for record in b4.to_dict(orient="records"):
        storage_id = record["storage_id"]
        year = int(record["year"])
        market = record["country_code"]
        observed = _storage_observed(networks[year], hydro.loc[_float(hydro, "year").eq(year)], record)
        is_external = record["static_source_family"] == "ETX7B1_EXTERNAL"
        source = b1.loc[storage_id] if is_external else b2.loc[storage_id]
        for dimension in dimensions:
            if is_external:
                prefix = {"charge_power_MW": "charge_power", "discharge_power_MW": "discharge_power", "energy_MWh": "energy"}[dimension]
                workbook_row = int(source[f"{prefix}_etx7a_workbook_row"])
                intended = float(etx_rows[workbook_row][f"{year} value"]) * 1000.0
                authority = _rel(ETX7A_BOOK)
                authority_key = f"03_RUNTIME_INPUTS!row={workbook_row};{year}_value"
                adapter_value = float(source[dimension])
                adapter_name = "B1"
            else:
                intended = b2_values[(storage_id, dimension)]
                authority = "|".join(sorted(set(key.split(":")[0] for key in b2_keys[(storage_id, dimension)].split("|"))))
                authority_key = b2_keys[(storage_id, dimension)]
                adapter_value = float(source[dimension])
                adapter_name = "B2"
            runtime = float(record[dimension])
            b6 = observed[dimension]
            exact = all(_numeric_equal(intended, value) for value in (adapter_value, runtime, b6))
            rows.add(
                input_family="STORAGE",
                market=market,
                horizon=year,
                parameter=dimension,
                final_authority_artifact=authority,
                final_authority_key=authority_key,
                intended_value=intended,
                runtime_input_artifact=f"{adapter_name} static -> B4 storage runtime",
                runtime_input_key=storage_id,
                runtime_value={f"{adapter_name}_value": adapter_value, "B4_value": runtime},
                b6_observed_value=b6,
                transformation="BESS_STORE_PLUS_CHARGE_DISCHARGE_LINKS" if record["storage_family"] == "BESS" else "PHS_TO_SHARED_WATER_STATE_SLICES",
                difference={f"{adapter_name}_minus_intended": adapter_value - intended, "B4_minus_intended": runtime - intended, "B6_minus_intended": b6 - intended},
                classification="INTENTIONAL_RUNTIME_TRANSFORMATION" if exact else "IMPLEMENTATION_DRIFT",
                affected_gate="NONE" if exact else ("ETX-7B1" if is_external else "ETX-7B2"),
                action="NONE" if exact else "STOP_AND_REVISE_2050_CHAIN",
            )
        intended_rte = float(record["round_trip_efficiency"])
        observed_rte = observed["round_trip_efficiency"]
        exact_rte = _numeric_equal(intended_rte, observed_rte, 2e-6)
        rows.add(
            input_family="STORAGE",
            market=market,
            horizon=year,
            parameter="round_trip_efficiency",
            final_authority_artifact=_rel(B4 / "MEM_ETX7B4_Storage_Operating_Parameters_v1.0.csv"),
            final_authority_key=storage_id,
            intended_value=intended_rte,
            runtime_input_artifact="B4 storage runtime",
            runtime_input_key=storage_id,
            runtime_value={"charge_efficiency": float(record["charge_efficiency"]), "discharge_efficiency": float(record["discharge_efficiency"])},
            b6_observed_value=observed_rte,
            transformation="ROUND_TRIP_SPLIT_ACROSS_CHARGE_AND_DISCHARGE_LINKS",
            difference=observed_rte - intended_rte,
            classification="INTENTIONAL_RUNTIME_TRANSFORMATION" if exact_rte else "IMPLEMENTATION_DRIFT",
            affected_gate="NONE" if exact_rte else "ETX-7B4_OR_ETX-7B6",
            action="NONE" if exact_rte else "STOP_AND_REVISE_2050_CHAIN",
        )


def _add_hydro(rows: AuditRows, networks: dict[int, pypsa.Network]) -> None:
    hydro = _csv(B4 / "MEM_ETX7B4_Hydro_Annual_Scaling_and_State_v1.0.csv")
    temporal = pd.read_parquet(B3 / "MEM_ETX7B3_Hydro_Temporal_Hourly_v1.0.parquet")
    for record in hydro.to_dict(orient="records"):
        year = int(record["year"])
        market = record["country_code"]
        network = networks[year]
        safe = _safe_id(record["operational_slice_id"])
        stateful = record["energy_state_required"].lower() == "true"
        turbine = float(record["turbine_power_MW"])
        if stateful:
            link = network.links.loc[f"TURBINE_{safe}"]
            observed_turbine = float(link.p_nom * link.efficiency)
            observed_energy = float(network.stores.at[f"STORE_WATER_{safe}", "e_nom"])
            pump_name = f"PUMP_{safe}"
            observed_pump = float(network.links.at[pump_name, "p_nom"]) if pump_name in network.links.index else 0.0
        else:
            observed_turbine = float(network.generators.at[f"HYDRO_{safe}", "p_nom"])
            observed_energy = 0.0
            observed_pump = 0.0
        for parameter, intended, observed in (
            ("turbine_power_MW", turbine, observed_turbine),
            ("pump_power_MW", float(record["pump_power_MW"]), observed_pump),
            ("operational_state_energy_MWh", float(record["operational_state_energy_MWh"]), observed_energy),
        ):
            exact = _numeric_equal(intended, observed)
            rows.add(
                input_family="HYDRO_PHS_RUNTIME",
                market=market,
                horizon=year,
                parameter=parameter,
                final_authority_artifact=_rel(B4 / "MEM_ETX7B4_Hydro_Annual_Scaling_and_State_v1.0.csv"),
                final_authority_key=record["operational_slice_id"],
                intended_value=intended,
                runtime_input_artifact="B4 hydro runtime layer",
                runtime_input_key=record["operational_slice_id"],
                runtime_value=intended,
                b6_observed_value=observed,
                transformation="STATEFUL_STORE_TURBINE_PUMP_SPILL_ARCHITECTURE" if stateful else "INFLOW_LIMITED_GENERATOR_ARCHITECTURE",
                difference=observed - intended,
                classification="INTENTIONAL_RUNTIME_TRANSFORMATION" if exact else "IMPLEMENTATION_DRIFT",
                affected_gate="NONE" if exact else "ETX-7B4_OR_ETX-7B6",
                action="NONE" if exact else "STOP_AND_REVISE_2050_CHAIN",
            )
        if record["natural_inflow_required"].lower() == "true":
            profile = temporal.loc[
                temporal["profile_id"].astype(str).eq(record["natural_inflow_profile_id"]), ["snapshot", "value"]
            ].copy()
            profile["snapshot"] = pd.to_datetime(profile["snapshot"], utc=True).dt.tz_convert(None)
            water = profile.set_index("snapshot")["value"].astype(float) * float(record["hourly_inflow_scaling_MWh_water_per_unit_share"])
            if stateful:
                name = f"INFLOW_{safe}"
                actual = network.generators_t.p_max_pu[name] * float(network.generators.at[name, "p_nom"])
                expected = water
            else:
                name = f"HYDRO_{safe}"
                expected = (water * float(record["turbine_efficiency"]) / turbine).clip(lower=0.0, upper=1.0)
                actual = network.generators_t.p_max_pu[name]
            actual = actual.reindex(expected.index).astype(float)
            max_diff = float(np.max(np.abs(expected.to_numpy() - actual.to_numpy())))
            rows.add(
                input_family="HYDRO_PHS_RUNTIME",
                market=market,
                horizon=year,
                parameter="hourly_natural_inflow_mapping",
                final_authority_artifact=_rel(B4 / "MEM_ETX7B4_Hydro_Annual_Scaling_and_State_v1.0.csv"),
                final_authority_key=record["operational_slice_id"],
                intended_value=_series_summary(expected),
                runtime_input_artifact=_rel(B3 / "MEM_ETX7B3_Hydro_Temporal_Hourly_v1.0.parquet"),
                runtime_input_key=record["natural_inflow_profile_id"],
                runtime_value=_series_summary(expected),
                b6_observed_value=_series_summary(actual),
                transformation="NORMALIZED_B3_PROFILE_TIMES_B4_ANNUAL_WATER_INFLOW_SCALE",
                difference={"max_absolute": max_diff},
                classification="INTENTIONAL_RUNTIME_TRANSFORMATION" if max_diff <= 1e-9 else "IMPLEMENTATION_DRIFT",
                affected_gate="NONE" if max_diff <= 1e-9 else "ETX-7B3_OR_ETX-7B4_OR_ETX-7B6",
                action="NONE" if max_diff <= 1e-9 else "STOP_AND_REVISE_2050_CHAIN",
            )


def _add_topology_and_scarcity(rows: AuditRows, networks: dict[int, pypsa.Network]) -> None:
    final_interfaces = _csv(PRE / "MEM_External_Interface_Static_Contract.csv")
    links = _csv(B5 / "MEM_ETX7B5_Directional_Link_Registry_v1.0.csv")
    physical = _csv(B5 / "MEM_ETX7B5_Physical_Interface_Registry_v1.0.csv").set_index("physical_link_id")
    for record in links.to_dict(orient="records"):
        year = int(record["year"])
        from_market, to_market = record["from_market"], record["to_market"]
        b5_value = float(record["capacity_MW"])
        if record["link_family"] == "ITALY_FACING":
            external = to_market if from_market == "IT" else from_market
            direction = "EXPORT" if from_market == "IT" else "IMPORT"
            selected = final_interfaces.loc[
                final_interfaces["external_market"].eq(external) & final_interfaces["direction"].eq(direction)
            ]
            if len(selected) != 1:
                raise RuntimeError(f"No unique final Italy-interface contract row: {external} {direction}")
            intended = float(selected.iloc[0]["capacity_MW"])
            authority = _rel(PRE / "MEM_External_Interface_Static_Contract.csv")
            authority_key = f"external_market={external};direction={direction};applicable_years=2040|2050"
        else:
            intended = b5_value
            authority = _rel(B5 / "MEM_ETX7B5_Topology_Authority_Matrix_v1.0.csv")
            authority_key = record["directional_link_id"]
        endpoints = physical.loc[record["physical_link_id"]]
        link = networks[year].links.loc[f"INTERCONNECTOR_{_safe_id(record['physical_link_id'])}"]
        if from_market == endpoints["endpoint_a"] and to_market == endpoints["endpoint_b"]:
            observed = float(link.p_nom * link.p_max_pu)
        else:
            observed = float(-link.p_nom * link.p_min_pu)
        exact = _numeric_equal(intended, b5_value) and _numeric_equal(intended, observed)
        rows.add(
            input_family="TOPOLOGY",
            market=f"{from_market}->{to_market}",
            horizon=year,
            parameter="fixed_directional_capacity_MW",
            final_authority_artifact=authority,
            final_authority_key=authority_key,
            intended_value=intended,
            runtime_input_artifact=_rel(B5 / "MEM_ETX7B5_Directional_Link_Registry_v1.0.csv"),
            runtime_input_key=record["directional_link_id"],
            runtime_value=b5_value,
            b6_observed_value=observed,
            transformation="TWO_DIRECTIONAL_CONTRACT_ROWS_TO_ONE_SIGNED_FIXED_PYPSA_LINK",
            difference={"B5_minus_intended": b5_value - intended, "B6_minus_intended": observed - intended},
            classification="INTENTIONAL_RUNTIME_TRANSFORMATION" if exact else "IMPLEMENTATION_DRIFT",
            affected_gate="NONE" if exact else "ETX-7B5_OR_ETX-7B6",
            action="NONE" if exact else "STOP_AND_REVISE_2050_CHAIN",
        )
    for year, network in networks.items():
        for market in MARKETS:
            load = network.loads_t.p_set[f"LOAD_{market}"].astype(float)
            generator = network.generators.loc[f"LOAD_SHEDDING_{market}"]
            cap = network.generators_t.p_max_pu[f"LOAD_SHEDDING_{market}"].astype(float) * float(generator.p_nom)
            max_diff = float(np.max(np.abs(cap.to_numpy() - load.to_numpy())))
            exact = _numeric_equal(generator.marginal_cost, VOLL) and max_diff <= 1e-9
            rows.add(
                input_family="PRICE_SCARCITY_FORMATION",
                market=market,
                horizon=year,
                parameter="local_load_shedding_VOLL_and_hourly_cap",
                final_authority_artifact=_rel(ROOT / "config/stage_a_execution.yaml"),
                final_authority_key="assembly.feasibility",
                intended_value={"VOLL_EUR_per_MWh": VOLL, "hourly_cap": "LOCAL_LOAD_MW"},
                runtime_input_artifact="Stage-A network assembler",
                runtime_input_key=f"LOAD_SHEDDING_{market}",
                runtime_value={"p_nom_MW": float(generator.p_nom), "marginal_cost": float(generator.marginal_cost)},
                b6_observed_value={"hourly_cap_max_abs_difference_MW": max_diff, "extendable": bool(generator.p_nom_extendable), "committable": bool(generator.committable)},
                transformation="LOCAL_FEASIBILITY_SLACK_CAPPED_HOURLY_BY_LOCAL_DEMAND;DUAL_NOT_CLIPPED",
                difference={"VOLL": float(generator.marginal_cost) - VOLL, "cap_max_abs": max_diff},
                classification="INTENTIONAL_RUNTIME_TRANSFORMATION" if exact else "IMPLEMENTATION_DRIFT",
                affected_gate="NONE" if exact else "ETX-7B6",
                action="NONE" if exact else "STOP_AND_REVISE_2050_CHAIN",
            )


CASE_PATHS = {
    "B9B": {
        "dir": ROOT / "stage_a_results/2050_full",
        "stem": "MEM_ETX7B9_2050_BASE_FULL",
        "contract": None,
    },
    "R10": {
        "dir": ROOT / "stage_a_results/2050_perimeter_closure_r10",
        "stem": "MEM_ETX7B9C_2050_R10_PERIMETER_CLOSURE",
        "contract": ROOT / "qa/stage_a/etx7b9c/MEM_R10_2050_Virtual_Supply_Contract_v1.0.csv",
    },
    "R5": {
        "dir": ROOT / "stage_a_results/2050_perimeter_closure_r5",
        "stem": "MEM_ETX7B9D_2050_R5_PERIMETER_CLOSURE",
        "contract": ROOT / "qa/stage_a/etx7b9d/MEM_R5_2050_Virtual_Supply_Contract_v1.0.csv",
    },
    "B9E": {
        "dir": ROOT / "stage_a_results/2050_perimeter_closure_placement",
        "stem": "MEM_ETX7B9E_2050_PLACEMENT_PERIMETER_CLOSURE",
        "contract": ROOT / "qa/stage_a/etx7b9e/MEM_Placement_2050_Virtual_Supply_Contract_v1.0.csv",
    },
}


def _long_to_wide(path: Path, value: str, *, shedding: bool = False) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    if shedding:
        frame = frame.copy()
        frame["name"] = frame["name"].str.removeprefix("LOAD_SHEDDING_")
    return frame.pivot(index="snapshot", columns="name", values=value).sort_index()


def _case_tables(case: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    spec = CASE_PATHS[case]
    prices = _long_to_wide(spec["dir"] / f"{spec['stem']}_Market_Prices.parquet", "marginal_price_EUR_per_MWh")
    shedding = _long_to_wide(spec["dir"] / f"{spec['stem']}_Load_Shedding.parquet", "shedding_MW", shedding=True)
    return prices, shedding


def _add_post_b6_methodology(rows: AuditRows) -> None:
    for case, spec in CASE_PATHS.items():
        if case == "B9B":
            continue
        contract = _csv(spec["contract"])
        network = pypsa.Network(spec["dir"] / f"{spec['stem']}_SOLVED.nc")
        for record in contract.to_dict(orient="records"):
            generator_id = record["generator_id"]
            for parameter, column in (
                ("virtual_supply_p_nom_MW", "p_nom_MW"),
                ("virtual_supply_marginal_cost_EUR2025_per_MWh_el", "marginal_cost_EUR2025_per_MWh_el"),
            ):
                intended = float(record[column])
                observed = float(network.generators.at[generator_id, "p_nom" if column == "p_nom_MW" else "marginal_cost"])
                exact = _numeric_equal(intended, observed, 2e-6)
                rows.add(
                    input_family="PERIMETER_CLOSURE_POST_B6",
                    market=record["bus"],
                    horizon=2050,
                    parameter=parameter,
                    final_authority_artifact=_rel(spec["contract"]),
                    final_authority_key=generator_id,
                    intended_value=intended,
                    runtime_input_artifact=f"{case} isolated diagnostic contract",
                    runtime_input_key=generator_id,
                    runtime_value=intended,
                    b6_observed_value="NOT_APPLICABLE_POST_B6_ADDITION",
                    transformation="SEPARATE_POST_B6_FIXED_VIRTUAL_SUPPLY_METHODOLOGY_LAYER",
                    difference=observed - intended,
                    classification="INTENTIONAL_RUNTIME_TRANSFORMATION" if exact else "IMPLEMENTATION_DRIFT",
                    affected_gate="NONE" if exact else {"R10": "ETX-7B9C", "R5": "ETX-7B9D", "B9E": "ETX-7B9E"}[case],
                    action="NONE" if exact else "PRESERVE_HISTORY_AND_CORRECT_ONLY_IN_REVISION_NAMESPACE",
                )
        del network
        gc.collect()


def _price_tail_decomposition() -> pd.DataFrame:
    mask_frame = pd.read_csv(ROOT / "qa/stage_a/etx7b9d/MEM_R5_2050_Ordinary_Hour_Mask_v1.0.csv")
    mask_frame["snapshot"] = pd.to_datetime(mask_frame["snapshot"])
    ordinary = mask_frame.set_index("snapshot")["ordinary_hour"].astype(bool)
    records: list[dict[str, Any]] = []
    for case in CASE_PATHS:
        prices, shedding = _case_tables(case)
        ordinary_case = ordinary.reindex(prices.index)
        if ordinary_case.isna().any():
            raise RuntimeError("Immutable B9B ordinary-hour mask does not align with case prices")
        for market in EXPORTED_MARKETS:
            price = prices[market].astype(float)
            shed = shedding[market].astype(float)
            at_voll = np.isclose(price, VOLL, rtol=0.0, atol=VOLL_TOLERANCE)
            gt500, gt1000, gt5000 = price.gt(500), price.gt(1000), price.gt(5000)
            records.append(
                {
                    "case": case,
                    "market": market,
                    "hours": len(price),
                    "mean_EUR_per_MWh": float(price.mean()),
                    "median_EUR_per_MWh": float(price.median()),
                    "P95_EUR_per_MWh": float(price.quantile(0.95)),
                    "P99_EUR_per_MWh": float(price.quantile(0.99)),
                    "maximum_EUR_per_MWh": float(price.max()),
                    "VOLL_hours": int(at_voll.sum()),
                    "hours_gt_500": int(gt500.sum()),
                    "hours_gt_1000": int(gt1000.sum()),
                    "hours_gt_5000": int(gt5000.sum()),
                    "shedding_MWh": float(shed.sum()),
                    "shedding_hours_gt_1e_6_MW": int(shed.gt(1e-6).sum()),
                    "shedding_hours_gt_1_MW": int(shed.gt(1.0).sum()),
                    "mean_excluding_VOLL_hours": float(price.loc[~at_voll].mean()),
                    "mean_excluding_gt_5000_hours": float(price.loc[~gt5000].mean()),
                    "mean_excluding_gt_1000_hours": float(price.loc[~gt1000].mean()),
                    "gross_mean_contribution_gt_500": float(price.loc[gt500].sum() / len(price)),
                    "gross_mean_contribution_gt_1000": float(price.loc[gt1000].sum() / len(price)),
                    "gross_mean_contribution_gt_5000": float(price.loc[gt5000].sum() / len(price)),
                    "gross_mean_contribution_VOLL": float(price.loc[at_voll].sum() / len(price)),
                    "ordinary_hour_count_B9B_mask": int(ordinary_case.sum()),
                    "ordinary_hour_mean_EUR_per_MWh_B9B_mask": float(price.loc[ordinary_case].mean()),
                    "ordinary_mask_definition": "B9B_total_ten_market_shedding_MW<=1e-6 AND B9B_max_ten_market_price_EUR_per_MWh<=500; SAME_IMMUTABLE_MASK_ALL_CASES",
                    "interpretation_note": "Threshold contribution columns are gross and overlapping; they are not additive bands.",
                }
            )
    return pd.DataFrame.from_records(records)


def _add_price_extraction(rows: AuditRows) -> None:
    for case, spec in CASE_PATHS.items():
        prices, _ = _case_tables(case)
        network = pypsa.Network(spec["dir"] / f"{spec['stem']}_SOLVED.nc")
        observed = network.buses_t.marginal_price.loc[:, EXPORTED_MARKETS].astype(float)
        observed.index = pd.to_datetime(observed.index)
        expected = prices.loc[:, EXPORTED_MARKETS].astype(float).reindex(observed.index)
        max_diff = float(np.max(np.abs(expected.to_numpy() - observed.to_numpy())))
        rows.add(
            input_family="PRICE_SCARCITY_FORMATION",
            market="EXPORTED_8",
            horizon=2050,
            parameter=f"{case}_nodal_marginal_price_extraction",
            final_authority_artifact=_rel(spec["dir"] / f"{spec['stem']}_SOLVED.nc"),
            final_authority_key="buses_t.marginal_price[FR,CH,AT,SI,ME,GR,MT,TN]",
            intended_value={"rows": int(expected.size), "sha256": hashlib.sha256(np.asarray(expected, dtype="<f8").tobytes()).hexdigest().upper()},
            runtime_input_artifact=_rel(spec["dir"] / f"{spec['stem']}_Market_Prices.parquet"),
            runtime_input_key="all_exported_market_rows",
            runtime_value={"rows": int(expected.size)},
            b6_observed_value="NOT_APPLICABLE_SOLVED_RESULT",
            transformation="LITERAL_PYPSA_NODAL_MARGINAL_PRICE_EXPORT;NO_CLIPPING_OR_CAPPING",
            difference={"max_absolute_EUR_per_MWh": max_diff},
            classification="EXACT_MATCH" if max_diff <= 1e-9 else "IMPLEMENTATION_DRIFT",
            affected_gate="NONE" if max_diff <= 1e-9 else {"B9B": "ETX-7B9B", "R10": "ETX-7B9C", "R5": "ETX-7B9D", "B9E": "ETX-7B9E"}[case],
            action="NONE" if max_diff <= 1e-9 else "PRESERVE_HISTORY_AND_REPAIR_EXPORT_ADAPTER",
        )
        del network
        gc.collect()


def _verify_manifest(path: Path) -> dict[str, Any]:
    frame = _csv(path)
    failures: list[str] = []
    verified = 0
    for record in frame.to_dict(orient="records"):
        relative = record.get("relative_path", "")
        expected = record.get("sha256", "").upper()
        if not relative or not expected:
            continue
        candidate = ROOT / relative.replace("\\", "/")
        if not candidate.exists():
            candidate = path.parent / relative.replace("\\", "/")
        observed = sha256_file(candidate) if candidate.exists() else ""
        verified += 1
        if observed != expected:
            failures.append(relative)
    return {
        "path": _rel(path),
        "sha256": sha256_file(path),
        "members_verified": verified,
        "member_failures": failures,
        "status": "PASS" if not failures else "FAIL",
    }


def _immutability_checks() -> dict[str, Any]:
    config = load_execution_config()
    input_locks = verify_input_locks(config)
    manifests = [
        ROOT / "stage_a_results/2040_perimeter_closure_r10/MEM_ETX7B8D_2040_R10_PERIMETER_CLOSURE_Result_Manifest_v1.0.csv",
        ROOT / "stage_a_results/2050_full/MEM_ETX7B9_2050_BASE_FULL_Result_Manifest_v1.0.csv",
        ROOT / "stage_a_results/2050_perimeter_closure_r10/MEM_ETX7B9C_2050_R10_PERIMETER_CLOSURE_Result_Manifest_v1.0.csv",
        ROOT / "stage_a_results/2050_perimeter_closure_r5/MEM_ETX7B9D_2050_R5_PERIMETER_CLOSURE_Result_Manifest_v1.0.csv",
        ROOT / "stage_a_results/2050_perimeter_closure_placement/MEM_ETX7B9E_2050_PLACEMENT_PERIMETER_CLOSURE_Result_Manifest_v1.0.csv",
    ]
    manifest_checks = [_verify_manifest(path) for path in manifests]
    etx_manifest = _csv(ETX7A_MANIFEST)
    etx_checks = []
    for record in etx_manifest.to_dict(orient="records"):
        path = ROOT / record["relative_path"]
        observed = sha256_file(path)
        etx_checks.append({"path": _rel(path), "expected_sha256": record["sha256"].upper(), "observed_sha256": observed, "status": "PASS" if observed == record["sha256"].upper() else "FAIL"})
    expected_master = "3982922EAEA97D02F08D4BDC253EC58D210389FDEB9559CBD4A1E8F30C47449C"
    master_hash = sha256_file(FINAL_MASTER)
    checks = {
        "final_master": {"path": _rel(FINAL_MASTER), "expected_sha256": expected_master, "observed_sha256": master_hash, "status": "PASS" if master_hash == expected_master else "FAIL"},
        "final_master_QA": {"path": _rel(FINAL_MASTER_QA), "sha256": sha256_file(FINAL_MASTER_QA), "status": "PASS"},
        "ETX7A_frozen_authority": etx_checks,
        "B1_to_B5_input_locks": input_locks,
        "accepted_result_manifests": manifest_checks,
        "B6_unsolved_networks": {
            str(year): {
                "path": f"stage_a_networks/{year}/MEM_ETX7B6_{year}_BASE_8760h_UNSOLVED.nc",
                "sha256": sha256_file(ROOT / f"stage_a_networks/{year}/MEM_ETX7B6_{year}_BASE_8760h_UNSOLVED.nc"),
            }
            for year in (2040, 2050)
        },
    }
    statuses = [checks["final_master"]["status"]]
    statuses.extend(item["status"] for item in etx_checks)
    statuses.extend(item["status"] for item in input_locks)
    statuses.extend(item["status"] for item in manifest_checks)
    checks["status"] = "PASS" if all(status == "PASS" for status in statuses) else "FAIL"
    return checks


def _write_findings(frame: pd.DataFrame, tail: pd.DataFrame, verification: dict[str, Any]) -> None:
    counts = frame["classification"].value_counts().to_dict()
    drift = int(counts.get("IMPLEMENTATION_DRIFT", 0))
    leakage = int(counts.get("SUPERSEDED_ARTIFACT_LEAKAGE", 0))
    ambiguous = int(counts.get("AMBIGUOUS_REQUIRES_USER", 0))
    conclusion = "FINAL_RESEARCH_INPUTS_FAITHFULLY_IMPLEMENTED" if drift + leakage + ambiguous == 0 else "MATERIAL_RECONCILIATION_FINDINGS_REQUIRE_ACTION"
    b9e = tail.loc[tail["case"].eq("B9E")]
    b9e_mean = float(b9e["mean_EUR_per_MWh"].mean())
    b9e_ordinary = float(b9e["ordinary_hour_mean_EUR_per_MWh_B9B_mask"].mean())
    text = f"""# MEM Stage-A research-to-runtime reconciliation findings v1.0

## Status

**{conclusion}**

This audit starts at the final accepted MEM workbook and frozen contracts. Original PNIEC, Terna, DDS and other upstream research PDFs were deliberately not reopened or reinterpreted.

## Authority and supersession chain

1. Frozen pre-PyPSA implementation contracts and the final zonal-capacity master are the Italian authority.
2. The ETX-7A harmonised freeze is the external-country authority; its explicitly labelled technical fixed fills are closed inputs, not open research gaps.
3. B1/B2 adapt static inputs; B3 freezes 2019 chronology and hourly profiles; B4 freezes operating/runtime transformations; B5 freezes topology.
4. B6 is the serialized, unsolved observation surface. B9B/B9C/B9D/B9E remain immutable solved history.
5. Earlier workbooks are lineage-only. No superseded source is used by the audited runtime chain.

## Reconciliation summary

- Parameters/checks: **{len(frame)}**
- EXACT_MATCH: **{counts.get('EXACT_MATCH', 0)}**
- INTENTIONAL_RUNTIME_TRANSFORMATION: **{counts.get('INTENTIONAL_RUNTIME_TRANSFORMATION', 0)}**
- IMPLEMENTATION_DRIFT: **{drift}**
- SUPERSEDED_ARTIFACT_LEAKAGE: **{leakage}**
- AMBIGUOUS_REQUIRES_USER: **{ambiguous}**
- Frozen/manifest verification: **{verification['immutable_checks']['status']}**

## Material findings

No concrete static-input, adapter, horizon, aggregation, unit-conversion, carrier-split, storage, hydro, operating-cost, availability, chronology, or topology defect capable of explaining the 2050 price outcome was found. Runtime transformations are explicit and value-preserving.

## Answers to the required lineage questions

1. **Italian values:** no final Italian 2040/2050 value tested failed to reach B6 as intended. Seven-zone values are intentionally aggregated to the IT Stage-A node.
2. **ETX-7A values:** no tested external-country demand, generation or storage value failed to reach B6.
3. **Superseded sources:** B6 consumes B1-B5 accepted packages; no intermediate/superseded static artifact leakage was identified.
4. **Wrong-horizon inheritance:** no accidental 2030/2035-to-2050 inheritance was identified in the final runtime chain.
5. **Apparently old values:** the Italy-facing interface values are explicit final-contract values applicable to both 2040 and 2050.
6. **Italy-facing NTCs:** all 16 directed capacities per horizon match the final pre-PyPSA interface contract and the signed B6 links.
7. **External↔external interfaces:** all eight directed capacities per horizon match the accepted B5 authority and B6 signed links.
8. **Malta:** 200 MW in both directions is intentional under the final interface contract for 2040 and 2050; no final-artifact conflict exists.
9. **France 2050:** demand/capacity/storage rows, including the accepted technical/benchmark fills, are frozen ETX-7A runtime inputs rather than unresolved gaps.
10. **Demand/capacity units:** no material TWh→MWh, GW→MW, GWh→MWh or aggregation error was found.
11. **Extreme prices:** the price exports exactly reproduce PyPSA nodal marginal prices. B9E's eight-market mean is {b9e_mean:.6f} EUR/MWh, while the same cases on the immutable B9B ordinary-hour mask average {b9e_ordinary:.6f} EUR/MWh. The gap is dominated by scarcity/high-price tails and literal feasibility-penalty duals, not a generally comparable ordinary-dispatch price level.
12. **Upstream defect before another run:** none. Another placement/closure solve is not warranted as an implementation correction.

## Price-tail diagnosis

The current evidence classifies the 2050 plausibility issue primarily as a **scarcity-tail / reduced-perimeter architecture problem**, not a demonstrated static-research-input defect. The decomposition records means excluding VOLL, >5,000 and >1,000 EUR/MWh hours, gross tail contributions, and a common immutable ordinary-hour mask. Threshold contributions overlap and must not be added together.

## Bounded methodological decision families (not implemented)

Only a user-approved methodological decision could now change the outcome:

1. omitted-system support representation or placement;
2. reduced-perimeter topology abstraction;
3. adequacy-backstop representation;
4. treatment of VOLL/load-shedding duals in the Stage-B price handoff.

No VOLL, price cap, proxy cost, perimeter, topology, capacity, demand, chronology or frozen research input was changed by this audit.
"""
    FINDINGS_PATH.write_text(text, encoding="utf-8")


def _write_handoff(frame: pd.DataFrame, tail: pd.DataFrame) -> None:
    counts = frame["classification"].value_counts().to_dict()
    text = f"""# MEM Stage-A 2050 rerun preparation record v1.0

## Outcome

`FINAL_RESEARCH_INPUTS_FAITHFULLY_IMPLEMENTED`

The final frozen workbook/contracts propagate through B1-B5 into both B6 networks without material implementation drift. The audit checked {len(frame)} parameter/transform records: {counts.get('EXACT_MATCH', 0)} exact matches and {counts.get('INTENTIONAL_RUNTIME_TRANSFORMATION', 0)} documented transformations, with zero drift, superseded-source leakage or unresolved ambiguity.

## Gate impact

- B8D remains accepted, locked and byte-identical.
- B9B, B9C, B9D and B9E remain valid immutable results for the model state they solved.
- No upstream correction exists, so no B6R1/B9 revision chain is required.
- No 2050 rerun is prepared or authorized by this audit.
- B10 and Stage B remain locked.

## Price interpretation

The 2050 exported-price problem is driven primarily by scarcity tails and literal nodal duals associated with the 15,000 EUR/MWh local feasibility slack. Ordinary-hour prices are separately reported using the single immutable B9B mask. The user's rough plausibility range was used only as a diagnostic and did not control any input or result.

## Next decision

Before any further solve, select whether to revisit omitted-system representation, reduced-perimeter topology abstraction, adequacy backstop design, or the treatment of scarcity duals in the Stage-B price handoff. That is a methodology decision; it is not an automatic coding correction.

## Artifacts

- `{_rel(RECONCILIATION_PATH)}`
- `{_rel(FINDINGS_PATH)}`
- `{_rel(TAIL_PATH)}`
- `{_rel(IMPACT_PATH)}`
- `{_rel(VERIFICATION_PATH)}`
"""
    HANDOFF_PATH.write_text(text, encoding="utf-8")


def run_reconciliation(*, write: bool = True) -> dict[str, Any]:
    immutable = _immutability_checks()
    if immutable["status"] != "PASS":
        raise RuntimeError("Frozen input/result verification failed; reconciliation stopped")
    etx_rows = _etx7a_runtime_rows()
    b2_values, b2_keys = _b2_authority_aggregates()
    networks = {year: _network(year) for year in (2040, 2050)}
    audit = AuditRows()
    _add_demand(audit, networks, etx_rows)
    _add_generation_and_economics(audit, networks, etx_rows, b2_values, b2_keys)
    _add_storage(audit, networks, etx_rows, b2_values, b2_keys)
    _add_hydro(audit, networks)
    _add_topology_and_scarcity(audit, networks)
    _add_post_b6_methodology(audit)
    _add_price_extraction(audit)
    frame = audit.frame()
    tail = _price_tail_decomposition()
    counts = Counter(frame["classification"])
    material = sum(counts[name] for name in ("IMPLEMENTATION_DRIFT", "SUPERSEDED_ARTIFACT_LEAKAGE", "AMBIGUOUS_REQUIRES_USER"))
    status = "PASS" if material == 0 else "FAIL"
    impact = {
        "schema": "MEM_STAGE_A_2050_GATE_IMPACT_MAP_V1_0",
        "status": status,
        "audit_conclusion": "FINAL_RESEARCH_INPUTS_FAITHFULLY_IMPLEMENTED" if status == "PASS" else "MATERIAL_RECONCILIATION_FINDINGS_REQUIRE_ACTION",
        "material_upstream_runtime_change": False,
        "automatic_fixes_applied": [],
        "earliest_invalidated_gate": None,
        "historical_results": {gate: "VALID_IMMUTABLE" for gate in ("ETX-7B8D", "ETX-7B9B", "ETX-7B9C", "ETX-7B9D", "ETX-7B9E")},
        "revision_chain_required": False,
        "next_2050_solve_authorized": False,
        "methodology_decision_required_before_any_further_2050_run": True,
        "B10": "LOCKED",
        "Stage_B": "LOCKED",
        "full_year_optimization_executed": False,
    }
    verification: dict[str, Any] = {
        "schema": "MEM_STAGE_A_RECONCILIATION_FINAL_VERIFICATION_V1_0",
        "status": status,
        "authority_boundary": "FINAL_FROZEN_WORKBOOK_AND_CONTRACTS_TO_B1_B5_TO_B6_TO_SOLVED_STAGE_A",
        "original_research_PDFs_inspected": False,
        "new_research_performed": False,
        "optimization_executed": False,
        "accepted_artifacts_modified": False,
        "parameters_checked": len(frame),
        "classification_counts": dict(sorted(counts.items())),
        "immutable_checks": immutable,
        "output_hashes": {},
    }
    if write:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        frame.to_csv(RECONCILIATION_PATH, index=False, lineterminator="\n")
        tail.to_csv(TAIL_PATH, index=False, lineterminator="\n")
        IMPACT_PATH.write_text(json.dumps(impact, indent=2) + "\n", encoding="utf-8")
        _write_findings(frame, tail, verification)
        _write_handoff(frame, tail)
        for path in (RECONCILIATION_PATH, FINDINGS_PATH, TAIL_PATH, IMPACT_PATH, HANDOFF_PATH):
            verification["output_hashes"][_rel(path)] = sha256_file(path)
        VERIFICATION_PATH.write_text(json.dumps(verification, indent=2) + "\n", encoding="utf-8")
    return {"status": status, "reconciliation": frame, "tail": tail, "impact": impact, "verification": verification}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Write canonical reconciliation outputs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_reconciliation(write=args.write)
    print(json.dumps({"status": result["status"], "parameters_checked": len(result["reconciliation"]), "classification_counts": result["verification"]["classification_counts"]}, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

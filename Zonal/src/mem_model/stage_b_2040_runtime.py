from __future__ import annotations

import argparse
import json
import math
import shutil
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .common import ACCEPTED_RUNTIME, CONFIG, ROOT, STATIC, dump_json, load_yaml, parse_bool, sha256_file


YEAR = 2040
_RUNTIME_HORIZON: ContextVar[int] = ContextVar("mem_stage_b_runtime_horizon", default=YEAR)
SCENARIOS = ("Slow", "Base", "High")
ZONES = ("CALA", "CNOR", "CSUD", "NORD", "SARD", "SICI", "SUD")
PRICE_MARKETS = ("FR", "CH", "AT", "SI", "ME", "GR", "MT", "TN")
MANIFEST_NAME = "MEM_STAGE_B_2040_ACCEPTED_RUNTIME_MANIFEST_v1.0.csv"
STATE_NAME = "MEM_STAGE_B_2040_ACCEPTED_RUNTIME_STATE_v1.0.json"
RUNTIME_MEMBERS = (
    "load_hourly.parquet",
    "generator_availability_hourly.parquet",
    "hydro_inflow_hourly.parquet",
    "hydro_runtime_parameters.csv",
    "external_prices_hourly.parquet",
)
SEMANTIC_KEYS = {
    "load_hourly.parquet": "snapshot|year|scenario|zone",
    "generator_availability_hourly.parquet": "snapshot|year|scenario|generator_id",
    "hydro_inflow_hourly.parquet": "snapshot|year|scenario|hydro_id",
    "hydro_runtime_parameters.csv": "year|scenario|state_id",
    "external_prices_hourly.parquet": "snapshot|year|scenario|external_market",
}
QA_DIR = ROOT / "qa" / "stage_b" / "runtime_2040"
RECONCILIATION_PATH = QA_DIR / "MEM_STAGE_B_2040_RUNTIME_RECONCILIATION_v1.0.csv"
QA_PATH = QA_DIR / "MEM_STAGE_B_2040_RUNTIME_QA_v1.0.json"
HASH_PATH = QA_DIR / "MEM_STAGE_B_2040_RUNTIME_HASH_VERIFICATION_v1.0.json"
FINAL_PATH = QA_DIR / "MEM_STAGE_B_2040_RUNTIME_FINAL_VERIFICATION_v1.0.json"
CONTRACT_PATH = CONFIG / "stage_b_2040_runtime_contract.yaml"


@contextmanager
def runtime_horizon(year: int) -> Iterable[None]:
    """Select frozen profile magnitudes without mutating the accepted 2040 default."""

    if year not in (2040, 2050):
        raise ValueError(f"Unsupported Stage-B runtime horizon: {year}")
    token = _RUNTIME_HORIZON.set(year)
    try:
        yield
    finally:
        _RUNTIME_HORIZON.reset(token)


def _active_year() -> int:
    return _RUNTIME_HORIZON.get()


def contract() -> dict[str, Any]:
    year = _active_year()
    path = CONTRACT_PATH if year == 2040 else CONFIG / "stage_b_2050_runtime_contract.yaml"
    value = load_yaml(path)
    if value.get("status") != "ACCEPTED_RUNTIME_CONTRACT" or int(value.get("horizon", -1)) != year:
        raise RuntimeError(f"The accepted {year} runtime contract is missing or not accepted")
    return value


def _authority_path(name: str) -> Path:
    return ROOT / contract()["authorities"][name]


def _parquet_rows(path: Path) -> int:
    return int(pq.ParquetFile(path).metadata.num_rows)


def _write_parquet_chunks(path: Path, chunks: Iterable[pd.DataFrame]) -> int:
    """Write fixed-order row groups without pandas or filesystem metadata."""

    path.parent.mkdir(parents=True, exist_ok=True)
    writer: pq.ParquetWriter | None = None
    rows = 0
    try:
        for frame in chunks:
            table = pa.Table.from_pandas(frame, preserve_index=False).replace_schema_metadata(None)
            if writer is None:
                writer = pq.ParquetWriter(
                    path,
                    table.schema,
                    compression="zstd",
                    use_dictionary=True,
                    write_statistics=True,
                    version="2.6",
                    data_page_version="1.0",
                )
            writer.write_table(table, row_group_size=len(frame))
            rows += len(frame)
    finally:
        if writer is not None:
            writer.close()
    if writer is None:
        raise ValueError(f"No rows supplied for {path.name}")
    return rows


def _exact_scaled_profile(shape: np.ndarray, annual_total: float) -> np.ndarray:
    values = np.asarray(shape, dtype="float64")
    if len(values) != 8760 or not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Accepted profile must contain 8,760 finite non-negative values")
    denominator = float(values.sum())
    if denominator <= 0:
        raise ValueError("Accepted profile has no positive mass")
    result = values / denominator * float(annual_total)
    result[int(np.argmax(result))] += float(annual_total) - float(result.sum())
    if (result < -1e-9).any() or not math.isclose(float(result.sum()), float(annual_total), abs_tol=1e-6):
        raise ValueError("Exact annual profile scaling failed")
    return np.maximum(result, 0.0)


def accepted_snapshots() -> pd.DatetimeIndex:
    year = _active_year()
    source = pd.read_parquet(
        _authority_path("load_shape"),
        columns=["snapshot", "horizon", "scenario", "country_code", "normalized_shape_value"],
    )
    subset = source.loc[
        source["horizon"].astype(int).eq(year)
        & source["scenario"].astype(str).eq("Base")
        & source["country_code"].astype(str).eq("IT")
    ].sort_values("snapshot")
    snapshots = pd.DatetimeIndex(pd.to_datetime(subset["snapshot"], utc=True, errors="raise"))
    expected = pd.date_range("2019-01-01 00:00:00+00:00", periods=8760, freq="h")
    if not snapshots.equals(expected):
        raise ValueError("B3 Italian load authority does not match the accepted 2019 UTC chronology")
    return snapshots


def _load_shape() -> tuple[pd.DatetimeIndex, np.ndarray, str]:
    year = _active_year()
    source = pd.read_parquet(_authority_path("load_shape"))
    subset = source.loc[
        source["horizon"].astype(int).eq(year)
        & source["scenario"].astype(str).eq("Base")
        & source["country_code"].astype(str).eq("IT")
    ].sort_values("snapshot")
    snapshots = pd.DatetimeIndex(pd.to_datetime(subset["snapshot"], utc=True, errors="raise"))
    if not snapshots.equals(accepted_snapshots()):
        raise ValueError("Selected load shape chronology differs from the accepted chronology")
    profile_ids = subset["profile_id"].astype(str).unique()
    if len(profile_ids) != 1:
        raise ValueError("Italian B3 load shape must have exactly one accepted profile ID")
    shape = pd.to_numeric(subset["normalized_shape_value"], errors="raise").to_numpy(dtype="float64")
    return snapshots, shape, str(profile_ids[0])


def _load_chunks() -> Iterable[pd.DataFrame]:
    year = _active_year()
    snapshots, shape, profile_id = _load_shape()
    controls = pd.read_csv(_authority_path("zonal_demand"))
    controls = controls.loc[controls["year"].astype(int).eq(year)].copy()
    for scenario in SCENARIOS:
        case = controls.loc[controls["scenario"].astype(str).eq(scenario)].set_index("zone")
        if set(case.index.astype(str)) != set(ZONES):
            raise ValueError(f"Frozen demand controls incomplete for {scenario}")
        for zone in ZONES:
            annual = float(case.at[zone, "annual_zonal_demand_MWh"])
            yield pd.DataFrame(
                {
                    "snapshot": snapshots,
                    "year": np.full(8760, year, dtype="int64"),
                    "scenario": np.full(8760, scenario, dtype=object),
                    "zone": np.full(8760, zone, dtype=object),
                    "load_MW": _exact_scaled_profile(shape, annual),
                    "source_profile_id": np.full(8760, profile_id, dtype=object),
                    "transformation": np.full(
                        8760,
                        "DETERMINISTIC_RUNTIME_TRANSFORMATION:NATIONAL_2019_SHAPE_X_FROZEN_ZONAL_ANNUAL_CONTROL",
                        dtype=object,
                    ),
                }
            )


def _allocation(total: float, rows: pd.DataFrame, weight: str) -> dict[int, float]:
    ordered = rows.sort_values(["zone", "hydro_class"]).copy()
    denominator = float(pd.to_numeric(ordered[weight], errors="raise").sum())
    if denominator <= 0:
        raise ValueError("Cannot allocate a positive hydro control over zero power")
    result: dict[int, float] = {}
    for index, row in ordered.iterrows():
        result[int(index)] = float(total) * float(row[weight]) / denominator
    last = int(ordered.index[-1])
    result[last] += float(total) - float(sum(result.values()))
    return result


def _accepted_phs_split(mapping: pd.DataFrame) -> pd.DataFrame:
    """Derive the accepted Method-C pure/mixed rows from its source controls."""

    phs = mapping.loc[
        mapping["hydro_class"].isin(["PURE_PHS", "MIXED_PHS"])
        & pd.to_numeric(mapping["p_nom_MW_NET"], errors="raise").gt(0)
    ].copy()
    method = pd.read_csv(_authority_path("phs_energy_method_c"))
    pumps = pd.read_csv(_authority_path("phs_pump_allocation"))
    zonal = pd.read_csv(_authority_path("phs_static_runtime")).set_index("zone")
    top4 = method.loc[method["top4_flag"].map(parse_bool)].copy()
    residual = method.loc[~method["top4_flag"].map(parse_bool)].copy()
    top4_ids = set(top4["plant_id"].dropna().astype(str))
    top4_energy = top4.groupby("zone")["operational_e_nom_MWh"].sum().to_dict()
    residual_energy = residual.groupby("zone")["operational_e_nom_MWh"].sum().to_dict()
    top4_pump = pumps.loc[pumps["plant_id"].astype(str).isin(top4_ids)].groupby("zone")["model_pump_power_MW"].sum().to_dict()

    records: list[dict[str, Any]] = []
    for zone in ZONES:
        classes = phs.loc[phs["zone"].eq(zone)].sort_values("hydro_class")
        if classes.empty:
            continue
        total_energy = float(zonal.at[zone, "operational_energy_MWh"])
        total_pump = float(zonal.at[zone, "pump_power_MW"])
        zone_top_energy = float(top4_energy.get(zone, 0.0))
        zone_residual_energy = float(residual_energy.get(zone, 0.0))
        zone_top_pump = float(top4_pump.get(zone, 0.0))
        zone_residual_pump = total_pump - zone_top_pump
        class_names = set(classes["hydro_class"].astype(str))
        for _, row in classes.iterrows():
            hydro_class = str(row["hydro_class"])
            if len(classes) == 1:
                energy = total_energy
                pump = total_pump
                rule = "METHOD_C_ONLY_POSITIVE_PHS_CLASS_IN_ZONE"
            elif zone == "CSUD":
                if hydro_class == "PURE_PHS":
                    energy, pump = zone_top_energy, zone_top_pump
                    rule = "METHOD_C_TOP4_PRESENZANO_PURE"
                else:
                    energy, pump = zone_residual_energy, zone_residual_pump
                    rule = "METHOD_C_CSUD_RESIDUAL_MIXED"
            elif zone == "NORD" and class_names == {"PURE_PHS", "MIXED_PHS"}:
                ratio = float(row["p_nom_MW_NET"]) / float(classes["p_nom_MW_NET"].sum())
                energy = zone_residual_energy * ratio + (zone_top_energy if hydro_class == "PURE_PHS" else 0.0)
                pump = zone_residual_pump * ratio + (zone_top_pump if hydro_class == "PURE_PHS" else 0.0)
                rule = "METHOD_C_NORD_TOP4_PURE_PLUS_RESIDUAL_PROPORTIONAL_TO_FROZEN_PURE_MIXED_DISCHARGE"
            else:
                raise ValueError(f"No accepted Method-C split rule for {zone}: {sorted(class_names)}")
            records.append(
                {
                    "zone": zone,
                    "hydro_class": hydro_class,
                    "turbine_power_MW": float(row["p_nom_MW_NET"]),
                    "pump_power_MW": float(pump),
                    "operational_energy_MWh": float(energy),
                    "allocation_rule": rule,
                }
            )

    result = pd.DataFrame(records)
    for zone, group in result.groupby("zone", sort=False):
        expected = zonal.loc[zone]
        for column, control in (
            ("turbine_power_MW", "discharge_power_MW_NET"),
            ("pump_power_MW", "pump_power_MW"),
            ("operational_energy_MWh", "operational_energy_MWh"),
        ):
            if not math.isclose(float(group[column].sum()), float(expected[control]), abs_tol=1e-6):
                raise ValueError(f"Method-C {zone} {column} does not reconcile")
    if not math.isclose(float(result["turbine_power_MW"].sum()), 7252.3, abs_tol=1e-6):
        raise ValueError("Method-C discharge control failed")
    if not math.isclose(float(result["pump_power_MW"].sum()), 6400.0, abs_tol=1e-6):
        raise ValueError("Method-C pump control failed")
    if not math.isclose(float(result["operational_energy_MWh"].sum()), 53000.0, abs_tol=1e-6):
        raise ValueError("Method-C operational-energy control failed")
    return result


def hydro_crosswalk() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return state parameters and natural-inflow specifications for all cases."""

    year = _active_year()

    mapping = pd.read_csv(_authority_path("hydro_static_mapping"))
    mapping["p_nom_MW_NET"] = pd.to_numeric(mapping["p_nom_MW_NET"], errors="raise")
    mapping = mapping.loc[mapping["p_nom_MW_NET"].gt(0)].copy()
    if not math.isclose(float(mapping["p_nom_MW_NET"].sum()), 23294.0, abs_tol=1e-6):
        raise ValueError("Hydro static mapping does not close to 23,294 MW")
    conventional = mapping.loc[mapping["hydro_class"].isin(["RUN_OF_RIVER", "BASIN_PONDAGE", "RESERVOIR"])]
    phs_mapping = mapping.loc[mapping["hydro_class"].isin(["PURE_PHS", "MIXED_PHS"])]
    if not math.isclose(float(conventional["p_nom_MW_NET"].sum()), 16041.7, abs_tol=1e-6):
        raise ValueError("Conventional hydro does not close to 16,041.7 MW")
    if not math.isclose(float(phs_mapping["p_nom_MW_NET"].sum()), 7252.3, abs_tol=1e-6):
        raise ValueError("PHS does not close to 7,252.3 MW")

    b4 = pd.read_csv(_authority_path("hydro_annual_scaling"))
    b4 = b4.loc[
        b4["country_code"].astype(str).eq("IT")
        & b4["year"].astype(int).eq(year)
        & b4["scenario"].astype(str).eq("Base")
    ].set_index("hydro_class")
    required_classes = {"RUN_OF_RIVER", "BASIN_PONDAGE", "RESERVOIR", "MIXED_PHS", "PURE_PHS"}
    if set(b4.index.astype(str)) != required_classes:
        raise ValueError("B4 Italian hydro class authority is incomplete")
    phs_split = _accepted_phs_split(mapping).set_index(["zone", "hydro_class"])
    generators = pd.read_csv(_authority_path("generator_static"))

    state_records: list[dict[str, Any]] = []
    inflow_records: list[dict[str, Any]] = []
    natural_classes = ("RUN_OF_RIVER", "BASIN_PONDAGE", "RESERVOIR", "MIXED_PHS")
    profile_class = {
        "RUN_OF_RIVER": "HYDRO_RUN_OF_RIVER",
        "BASIN_PONDAGE": "HYDRO_BASIN_PONDAGE",
        "RESERVOIR": "HYDRO_RESERVOIR",
        "MIXED_PHS": "HYDRO_RESERVOIR",
    }
    for scenario in SCENARIOS:
        scenario_generators = generators.loc[
            generators["year"].astype(int).eq(year)
            & generators["scenario"].astype(str).eq(scenario)
        ].copy()
        class_allocations: dict[str, tuple[dict[int, float], dict[int, float], dict[int, float]]] = {}
        for hydro_class in natural_classes:
            rows = mapping.loc[mapping["hydro_class"].eq(hydro_class)]
            b4_row = b4.loc[hydro_class]
            electric = _allocation(float(b4_row["annual_electrical_generation_reference_MWh"]), rows, "p_nom_MW_NET")
            water = _allocation(float(b4_row["annual_natural_inflow_MWh_water"]), rows, "p_nom_MW_NET")
            if hydro_class in {"BASIN_PONDAGE", "RESERVOIR"}:
                energy = _allocation(float(b4_row["operational_state_energy_MWh"]), rows, "p_nom_MW_NET")
            else:
                energy = {int(index): 0.0 for index in rows.index}
            class_allocations[hydro_class] = (electric, water, energy)

        for index, row in mapping.sort_values(["zone", "hydro_class"]).iterrows():
            zone = str(row["zone"])
            hydro_class = str(row["hydro_class"])
            if hydro_class == "RUN_OF_RIVER":
                match = scenario_generators.loc[
                    scenario_generators["zone"].astype(str).eq(zone)
                    & scenario_generators["parent_capacity_technology"].astype(str).eq("HYDRO_RUN_OF_RIVER")
                ]
                if len(match) != 1:
                    raise ValueError(f"Expected one frozen ROR generator for {scenario} {zone}")
                runtime_id = str(match.iloc[0]["generator_id"])
            else:
                runtime_id = f"{year}_{scenario.upper()}_{zone}_{hydro_class}"

            if hydro_class in natural_classes:
                electric, water, _ = class_allocations[hydro_class]
                b4_row = b4.loc[hydro_class]
                inflow_records.append(
                    {
                        "year": year,
                        "scenario": scenario,
                        "hydro_id": runtime_id,
                        "zone": zone,
                        "hydro_class": hydro_class,
                        "turbine_power_MW_NET": float(row["p_nom_MW_NET"]),
                        "turbine_efficiency": float(b4_row["turbine_efficiency"]),
                        "annual_electrical_generation_reference_MWh": electric[int(index)],
                        "annual_natural_inflow_MWh_water": water[int(index)],
                        "source_profile_class": profile_class[hydro_class],
                        "source_profile_id": str(b4_row["natural_inflow_profile_id"]),
                        "transformation": "DETERMINISTIC_RUNTIME_TRANSFORMATION:B3_NORMALIZED_SHARE_X_B4_ANNUAL_WATER_CONTROL",
                    }
                )

            if hydro_class == "RUN_OF_RIVER":
                continue
            if hydro_class in {"PURE_PHS", "MIXED_PHS"}:
                split = phs_split.loc[(zone, hydro_class)]
                pump = float(split["pump_power_MW"])
                operational_energy = float(split["operational_energy_MWh"])
                efficiency = math.sqrt(0.75)
                allocation_rule = str(split["allocation_rule"])
            else:
                _, _, energy = class_allocations[hydro_class]
                pump = 0.0
                operational_energy = energy[int(index)]
                efficiency = 0.90
                allocation_rule = "B4_CLASS_STATE_ENERGY_ALLOCATED_BY_FROZEN_ZONAL_NET_TURBINE_POWER"
            state_records.append(
                {
                    "year": year,
                    "scenario": scenario,
                    "state_id": runtime_id,
                    "zone": zone,
                    "hydro_class": hydro_class,
                    "turbine_power_MW": float(row["p_nom_MW_NET"]),
                    "pump_power_MW": pump,
                    "operational_energy_MWh": operational_energy,
                    "turbine_efficiency": efficiency,
                    "pump_efficiency": efficiency if pump > 0 else 1.0,
                    "natural_inflow_allowed": bool(parse_bool(row["natural_inflow_required"])),
                    "grid_charging_allowed": bool(parse_bool(row["grid_charging_allowed"])),
                    "cyclic_state_of_charge": True,
                    "capacity_basis": "FROZEN_NET_ELECTRICAL_TURBINE_POWER",
                    "allocation_rule": allocation_rule,
                    "status": f"ACCEPTED_STAGE_B_{year}_RUNTIME_PARAMETER",
                }
            )

    states = pd.DataFrame(state_records)
    inflows = pd.DataFrame(inflow_records)
    if states.duplicated(["year", "scenario", "state_id"]).any():
        raise ValueError("Hydro state crosswalk is not unique")
    if inflows.duplicated(["year", "scenario", "hydro_id"]).any():
        raise ValueError("Hydro inflow crosswalk is not unique")
    return states, inflows


def _hydro_shapes() -> tuple[pd.DatetimeIndex, dict[str, np.ndarray]]:
    source = pd.read_parquet(_authority_path("hydro_temporal"))
    source = source.loc[source["country_code"].astype(str).eq("IT")].copy()
    snapshots = accepted_snapshots()
    shapes: dict[str, np.ndarray] = {}
    for hydro_class in ("HYDRO_RUN_OF_RIVER", "HYDRO_BASIN_PONDAGE", "HYDRO_RESERVOIR"):
        subset = source.loc[source["hydro_class"].astype(str).eq(hydro_class)].sort_values("snapshot")
        observed = pd.DatetimeIndex(pd.to_datetime(subset["snapshot"], utc=True, errors="raise"))
        if not observed.equals(snapshots):
            raise ValueError(f"B3 hydro profile chronology is incomplete for {hydro_class}")
        values = pd.to_numeric(subset["value"], errors="raise").to_numpy(dtype="float64")
        if (values < 0).any() or not np.isfinite(values).all() or float(values.sum()) <= 0:
            raise ValueError(f"Invalid B3 hydro profile for {hydro_class}")
        shapes[hydro_class] = values / float(values.sum())
    return snapshots, shapes


def _hydro_inflow_chunks(inflow_specs: pd.DataFrame) -> Iterable[pd.DataFrame]:
    year = _active_year()
    snapshots, shapes = _hydro_shapes()
    for _, row in inflow_specs.sort_values(["scenario", "hydro_id"], key=lambda s: s.map({x: i for i, x in enumerate(SCENARIOS)}) if s.name == "scenario" else s).iterrows():
        annual = float(row["annual_natural_inflow_MWh_water"])
        values = _exact_scaled_profile(shapes[str(row["source_profile_class"])], annual)
        yield pd.DataFrame(
            {
                "snapshot": snapshots,
                "year": np.full(8760, year, dtype="int64"),
                "scenario": np.full(8760, str(row["scenario"]), dtype=object),
                "hydro_id": np.full(8760, str(row["hydro_id"]), dtype=object),
                "zone": np.full(8760, str(row["zone"]), dtype=object),
                "hydro_class": np.full(8760, str(row["hydro_class"]), dtype=object),
                "inflow_MW_water_equivalent": values,
                "source_profile_id": np.full(8760, str(row["source_profile_id"]), dtype=object),
                "annual_natural_inflow_MWh_water": np.full(8760, annual, dtype="float64"),
                "transformation": np.full(8760, str(row["transformation"]), dtype=object),
            }
        )


def _vre_profiles() -> tuple[pd.DatetimeIndex, dict[str, tuple[np.ndarray, str]]]:
    year = _active_year()
    source = pd.read_parquet(_authority_path("vre_profiles"))
    source = source.loc[source["country_code"].astype(str).eq("IT")].copy()
    snapshots = accepted_snapshots()
    profiles: dict[str, tuple[np.ndarray, str]] = {}
    for profile_class in ("SOLAR_PV_ROOFTOP", "SOLAR_PV_UTILITY", "WIND_ONSHORE", "WIND_OFFSHORE"):
        subset = source.loc[
            source["stage_a_static_or_profile_class"].astype(str).eq(profile_class)
            & source["profile_id"].astype(str).str.endswith(f"_{year}")
        ].sort_values("snapshot")
        observed = pd.DatetimeIndex(pd.to_datetime(subset["snapshot"], utc=True, errors="raise"))
        if not observed.equals(snapshots):
            raise ValueError(f"B3 VRE profile chronology is incomplete for {profile_class}")
        values = pd.to_numeric(subset["p_max_pu"], errors="raise").to_numpy(dtype="float64")
        if not np.isfinite(values).all() or (values < -1e-12).any() or (values > 1 + 1e-12).any():
            raise ValueError(f"B3 VRE profile outside [0,1] for {profile_class}")
        profile_ids = subset["profile_id"].astype(str).unique()
        if len(profile_ids) != 1:
            raise ValueError(f"Expected one B3 profile ID for {profile_class}")
        profiles[profile_class] = (np.clip(values, 0.0, 1.0), str(profile_ids[0]))
    return snapshots, profiles


def _availability_chunks(inflow_specs: pd.DataFrame) -> Iterable[pd.DataFrame]:
    year = _active_year()
    snapshots, vre = _vre_profiles()
    hydro_snapshots, hydro_shapes = _hydro_shapes()
    if not snapshots.equals(hydro_snapshots):
        raise ValueError("VRE and hydro chronologies differ")
    generators = pd.read_csv(_authority_path("generator_static"))
    generators = generators.loc[generators["year"].astype(int).eq(year)].copy()
    inflow_lookup = inflow_specs.set_index(["scenario", "hydro_id"])
    for scenario in SCENARIOS:
        case = generators.loc[generators["scenario"].astype(str).eq(scenario)].sort_values("generator_id")
        if case.empty:
            raise ValueError(f"No frozen {year} generators for {scenario}")
        for _, row in case.iterrows():
            generator_id = str(row["generator_id"])
            parent = str(row["parent_capacity_technology"])
            if parent in vre:
                values, source_id = vre[parent]
                rule = "ACCEPTED_B3_VRE_2019_PROFILE"
            elif parent == "HYDRO_RUN_OF_RIVER":
                spec = inflow_lookup.loc[(scenario, generator_id)]
                water = _exact_scaled_profile(
                    hydro_shapes[str(spec["source_profile_class"])],
                    float(spec["annual_natural_inflow_MWh_water"]),
                )
                electrical = water * float(spec["turbine_efficiency"])
                values = electrical / float(row["p_nom_MW"])
                if (values > 1 + 1e-10).any():
                    raise ValueError(f"Accepted ROR inflow exceeds frozen turbine power for {generator_id}")
                values = np.clip(values, 0.0, 1.0)
                source_id = str(spec["source_profile_id"])
                rule = "ACCEPTED_B3_B4_ROR_INFLOW_LIMIT"
            else:
                availability = pd.to_numeric(pd.Series([row["static_availability_equivalent"]]), errors="coerce").iloc[0]
                if pd.isna(availability):
                    raise ValueError(f"Missing frozen technical availability for {generator_id}")
                values = np.full(8760, float(availability), dtype="float64")
                source_id = str(row["availability_class"])
                rule = "ACCEPTED_BLK005_STATIC_TECHNICAL_AVAILABILITY_EQUIVALENT"
                if parent == "GEOTHERMAL" and not math.isclose(float(availability), 0.90, abs_tol=1e-12):
                    raise ValueError("Geothermal availability is not the accepted 0.90")
            if not np.isfinite(values).all() or (values < -1e-12).any() or (values > 1 + 1e-12).any():
                raise ValueError(f"Availability outside [0,1] for {generator_id}")
            yield pd.DataFrame(
                {
                    "snapshot": snapshots,
                    "year": np.full(8760, year, dtype="int64"),
                    "scenario": np.full(8760, scenario, dtype=object),
                    "generator_id": np.full(8760, generator_id, dtype=object),
                    "p_max_pu": np.asarray(values, dtype="float64"),
                    "source_profile_or_availability_id": np.full(8760, source_id, dtype=object),
                    "runtime_rule": np.full(8760, rule, dtype=object),
                }
            )


def build_runtime_members(output_dir: Path) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    states, inflow_specs = hydro_crosswalk()
    counts = {
        "load_hourly.parquet": _write_parquet_chunks(output_dir / "load_hourly.parquet", _load_chunks()),
        "hydro_inflow_hourly.parquet": _write_parquet_chunks(
            output_dir / "hydro_inflow_hourly.parquet", _hydro_inflow_chunks(inflow_specs)
        ),
        "generator_availability_hourly.parquet": _write_parquet_chunks(
            output_dir / "generator_availability_hourly.parquet", _availability_chunks(inflow_specs)
        ),
    }
    states = states.sort_values(
        ["scenario", "state_id"],
        key=lambda s: s.map({x: i for i, x in enumerate(SCENARIOS)}) if s.name == "scenario" else s,
    )
    states.to_csv(output_dir / "hydro_runtime_parameters.csv", index=False, lineterminator="\n", float_format="%.15g")
    counts["hydro_runtime_parameters.csv"] = len(states)
    external_source = _authority_path("external_prices")
    shutil.copyfile(external_source, output_dir / "external_prices_hourly.parquet")
    counts["external_prices_hourly.parquet"] = _parquet_rows(output_dir / "external_prices_hourly.parquet")
    return counts


def member_hashes(bundle_dir: Path) -> dict[str, str]:
    return {name: sha256_file(bundle_dir / name) for name in RUNTIME_MEMBERS}


def verify_accepted_runtime_manifest(bundle_dir: Path = ACCEPTED_RUNTIME, *, expected_year: int = YEAR) -> dict[str, Any]:
    """Fail-closed hash and governance verification used by production builders."""

    if int(expected_year) != YEAR:
        raise RuntimeError("Only the accepted 2040 runtime contract is promoted; Stage-B 2050 is locked")
    manifest_path = bundle_dir / MANIFEST_NAME
    state_path = bundle_dir / STATE_NAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"Accepted runtime manifest missing: {manifest_path}")
    if not state_path.exists():
        raise FileNotFoundError(f"Accepted runtime state missing: {state_path}")
    manifest = pd.read_csv(manifest_path, dtype=str)
    if set(manifest["file"].astype(str)) != set(RUNTIME_MEMBERS) or len(manifest) != len(RUNTIME_MEMBERS):
        raise RuntimeError("Accepted runtime manifest does not name exactly the five canonical members")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if int(state.get("horizon", -1)) != YEAR or state.get("status") != "ACCEPTED_HASH_LOCKED_RUNTIME":
        raise RuntimeError("Accepted runtime state has the wrong horizon or status")
    if state.get("production_optimization_executed") is not False:
        raise RuntimeError("Runtime state indicates that a production optimization already executed")
    if state.get("production_optimization_authorized") is not True:
        raise RuntimeError("Runtime state does not authorize the accepted 2040 Base production case")
    authorization = (
        int(state.get("authorized_year", -1)),
        tuple(state.get("authorized_scenarios", [])),
        str(state.get("authorized_mode", "")),
    )
    if authorization != (YEAR, ("Base",), "FULL_YEAR_CANONICAL"):
        raise RuntimeError("Runtime state authorization scope is not exactly 2040 Base full-year canonical")
    if tuple(state.get("unauthorized_scenarios", [])) != ("Slow", "High"):
        raise RuntimeError("Runtime state does not keep Slow and High explicitly unauthorized")
    if not (
        state.get("authorized_solver") == "gurobi"
        and state.get("required_gurobipy_version") == "13.0.3"
        and state.get("solver_options")
        == {"Threads": 1, "Seed": 0, "include_objective_constant": False}
    ):
        raise RuntimeError("Runtime state does not contain the canonical Gurobi solver contract")
    if state.get("stage_b_2050") != "LOCKED":
        raise RuntimeError("Runtime state does not keep Stage-B 2050 locked")
    observed: dict[str, str] = {}
    for _, row in manifest.iterrows():
        name = str(row["file"])
        path = bundle_dir / name
        if not path.exists():
            raise FileNotFoundError(f"Accepted runtime member missing: {path}")
        digest = sha256_file(path)
        if digest.lower() != str(row["sha256"]).lower():
            raise RuntimeError(f"Accepted runtime member hash differs: {name}")
        observed[name] = digest
    if str(state.get("manifest_sha256", "")).lower() != sha256_file(manifest_path).lower():
        raise RuntimeError("Accepted runtime manifest hash differs from accepted state")
    return {
        "status": "PASS",
        "horizon": YEAR,
        "manifest": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "members": observed,
    }


def _check(
    rows: list[dict[str, Any]],
    check_id: str,
    description: str,
    observed: Any,
    expected: Any,
    passed: bool,
    notes: str = "",
) -> None:
    rows.append(
        {
            "check_id": check_id,
            "description": description,
            "observed": observed,
            "expected": expected,
            "status": "PASS" if passed else "FAIL",
            "notes": notes,
        }
    )


def validate_runtime(bundle_dir: Path) -> pd.DataFrame:
    """Validate one horizon's frozen runtime at its semantic keys and controls."""

    year = _active_year()
    rows: list[dict[str, Any]] = []
    for name in RUNTIME_MEMBERS:
        _check(rows, f"FILE-{name}", f"Canonical member exists: {name}", (bundle_dir / name).exists(), True, (bundle_dir / name).exists())
    if any(not (bundle_dir / name).exists() for name in RUNTIME_MEMBERS):
        return pd.DataFrame(rows)

    load = pd.read_parquet(bundle_dir / "load_hourly.parquet")
    hydro = pd.read_parquet(bundle_dir / "hydro_inflow_hourly.parquet")
    params = pd.read_csv(bundle_dir / "hydro_runtime_parameters.csv")
    prices = pd.read_parquet(bundle_dir / "external_prices_hourly.parquet")
    chronology = accepted_snapshots()
    generators = pd.read_csv(_authority_path("generator_static"))
    demand = pd.read_csv(_authority_path("zonal_demand"))
    expected_states, inflow_specs = hydro_crosswalk()

    for name, frame, key in (
        ("load", load, ["snapshot", "year", "scenario", "zone"]),
        ("hydro", hydro, ["snapshot", "year", "scenario", "hydro_id"]),
        ("parameters", params, ["year", "scenario", "state_id"]),
        ("prices", prices, ["snapshot", "year", "scenario", "external_market"]),
    ):
        years = set(pd.to_numeric(frame["year"], errors="raise").astype(int))
        scenarios = set(frame["scenario"].astype(str))
        _check(rows, f"SCOPE-{name}-YEAR", f"{name} contains only {year}", sorted(years), [year], years == {year})
        _check(rows, f"SCOPE-{name}-SCENARIOS", f"{name} contains exactly Slow/Base/High", sorted(scenarios), sorted(SCENARIOS), scenarios == set(SCENARIOS))
        duplicate_count = int(frame.duplicated(key).sum())
        _check(rows, f"GRAIN-{name}", f"{name} semantic key is unique", duplicate_count, 0, duplicate_count == 0)

    load["snapshot"] = pd.to_datetime(load["snapshot"], utc=True, errors="raise")
    hydro["snapshot"] = pd.to_datetime(hydro["snapshot"], utc=True, errors="raise")
    prices["snapshot"] = pd.to_datetime(prices["snapshot"], utc=True, errors="raise")
    load_values = pd.to_numeric(load["load_MW"], errors="raise")
    _check(rows, "LOAD-NONNEGATIVE", "All load values are finite and non-negative", bool(np.isfinite(load_values).all() and (load_values >= 0).all()), True, bool(np.isfinite(load_values).all() and (load_values >= 0).all()))
    for scenario in SCENARIOS:
        case = load.loc[load["scenario"].astype(str).eq(scenario)]
        snapshots = pd.DatetimeIndex(case["snapshot"].drop_duplicates().sort_values())
        _check(rows, f"CHRONOLOGY-{scenario}", f"{scenario} load uses the exact accepted chronology", len(snapshots), 8760, snapshots.equals(chronology))
        controls = demand.loc[demand["year"].astype(int).eq(year) & demand["scenario"].astype(str).eq(scenario)].set_index("zone")
        for zone in ZONES:
            values = case.loc[case["zone"].astype(str).eq(zone), "load_MW"]
            observed = float(values.sum())
            expected = float(controls.at[zone, "annual_zonal_demand_MWh"])
            _check(rows, f"LOAD-{scenario}-{zone}", f"{scenario} {zone} annual load", observed, expected, math.isclose(observed, expected, abs_tol=1e-3))
        observed_national = float(case["load_MW"].sum())
        expected_national = float(controls["annual_zonal_demand_MWh"].sum())
        _check(rows, f"LOAD-{scenario}-NATIONAL", f"{scenario} national annual load", observed_national, expected_national, math.isclose(observed_national, expected_national, abs_tol=1e-3))

    # Availability is stored one 8,760-row group per frozen generator. Validate
    # each row group to avoid materializing 3.55 million repeated string rows.
    availability_path = bundle_dir / "generator_availability_hourly.parquet"
    parquet = pq.ParquetFile(availability_path)
    expected_generators = generators.loc[generators["year"].astype(int).eq(year)]
    observed_ids: dict[str, set[str]] = {scenario: set() for scenario in SCENARIOS}
    availability_valid = True
    chronology_valid = True
    for group_index in range(parquet.num_row_groups):
        frame = parquet.read_row_group(group_index, columns=["snapshot", "year", "scenario", "generator_id", "p_max_pu"]).to_pandas()
        scenario_values = frame["scenario"].astype(str).unique()
        id_values = frame["generator_id"].astype(str).unique()
        if len(frame) != 8760 or len(scenario_values) != 1 or len(id_values) != 1 or set(frame["year"].astype(int)) != {year}:
            availability_valid = False
            continue
        scenario = str(scenario_values[0])
        observed_ids.setdefault(scenario, set()).add(str(id_values[0]))
        values = pd.to_numeric(frame["p_max_pu"], errors="coerce").to_numpy(dtype="float64")
        availability_valid = availability_valid and bool(np.isfinite(values).all() and (values >= -1e-12).all() and (values <= 1 + 1e-12).all())
        snapshots = pd.DatetimeIndex(pd.to_datetime(frame["snapshot"], utc=True, errors="raise"))
        chronology_valid = chronology_valid and snapshots.equals(chronology)
    _check(rows, "AVAILABILITY-ROW-GROUPS", "One complete row group exists per frozen generator", parquet.num_row_groups, len(expected_generators), parquet.num_row_groups == len(expected_generators))
    _check(rows, "AVAILABILITY-VALUES", "All availability values are finite and within [0,1]", availability_valid, True, availability_valid)
    _check(rows, "AVAILABILITY-CHRONOLOGY", "Every generator availability series preserves all 8,760 hours", chronology_valid, True, chronology_valid)
    for scenario in SCENARIOS:
        expected_ids = set(expected_generators.loc[expected_generators["scenario"].astype(str).eq(scenario), "generator_id"].astype(str))
        _check(rows, f"AVAILABILITY-IDS-{scenario}", f"{scenario} runtime IDs exactly match frozen generators", len(observed_ids.get(scenario, set()) ^ expected_ids), 0, observed_ids.get(scenario, set()) == expected_ids)

    expected_state_keys = set(map(tuple, expected_states[["scenario", "state_id"]].astype(str).to_numpy()))
    observed_state_keys = set(map(tuple, params[["scenario", "state_id"]].astype(str).to_numpy()))
    _check(rows, "HYDRO-STATE-CROSSWALK", "Hydro state IDs exactly match the accepted row-level crosswalk", len(expected_state_keys ^ observed_state_keys), 0, expected_state_keys == observed_state_keys)
    inflow_values = pd.to_numeric(hydro["inflow_MW_water_equivalent"], errors="coerce")
    inflow_valid = inflow_values.notna().all() and np.isfinite(inflow_values).all() and (inflow_values >= 0).all()
    _check(rows, "HYDRO-INFLOW-NONNEGATIVE", "Hydro inflow is finite and non-negative", bool(inflow_valid), True, bool(inflow_valid))
    observed_inflow_keys = set(map(tuple, hydro[["scenario", "hydro_id"]].astype(str).drop_duplicates().to_numpy()))
    expected_inflow_keys = set(map(tuple, inflow_specs[["scenario", "hydro_id"]].astype(str).to_numpy()))
    _check(rows, "HYDRO-INFLOW-CROSSWALK", "Hourly hydro IDs exactly match the accepted natural-inflow crosswalk", len(observed_inflow_keys ^ expected_inflow_keys), 0, observed_inflow_keys == expected_inflow_keys)
    pure_inflow = hydro["hydro_class"].astype(str).eq("PURE_PHS").any()
    _check(rows, "HYDRO-NO-PURE-PHS-INFLOW", "Pure PHS receives no natural inflow", bool(pure_inflow), False, not pure_inflow)
    annual_observed = hydro.groupby(["scenario", "hydro_id"], sort=False)["inflow_MW_water_equivalent"].sum()
    annual_expected = inflow_specs.set_index(["scenario", "hydro_id"])["annual_natural_inflow_MWh_water"]
    maximum_inflow_difference = float((annual_observed.sort_index() - annual_expected.sort_index()).abs().max())
    _check(rows, "HYDRO-ANNUAL-SCALING", "Every hydro series exactly reconciles to its B4 water-energy control", maximum_inflow_difference, 0.0, maximum_inflow_difference <= 1e-6)

    for scenario in SCENARIOS:
        case = params.loc[params["scenario"].astype(str).eq(scenario)]
        phs = case.loc[case["hydro_class"].astype(str).isin(["PURE_PHS", "MIXED_PHS"])]
        conventional = case.loc[case["hydro_class"].astype(str).isin(["BASIN_PONDAGE", "RESERVOIR"])]
        _check(rows, f"PHS-{scenario}-DISCHARGE", f"{scenario} PHS net discharge", float(phs["turbine_power_MW"].sum()), 7252.3, math.isclose(float(phs["turbine_power_MW"].sum()), 7252.3, abs_tol=1e-6))
        _check(rows, f"PHS-{scenario}-PUMP", f"{scenario} PHS pump power", float(phs["pump_power_MW"].sum()), 6400.0, math.isclose(float(phs["pump_power_MW"].sum()), 6400.0, abs_tol=1e-6))
        _check(rows, f"PHS-{scenario}-ENERGY", f"{scenario} PHS operational energy", float(phs["operational_energy_MWh"].sum()), 53000.0, math.isclose(float(phs["operational_energy_MWh"].sum()), 53000.0, abs_tol=1e-6))
        _check(rows, f"HYDRO-{scenario}-CONVENTIONAL-STATE", f"{scenario} conventional hydro operational state", float(conventional["operational_energy_MWh"].sum()), 7900000.0, math.isclose(float(conventional["operational_energy_MWh"].sum()), 7900000.0, abs_tol=1e-6))
        physical_used = bool(np.isclose(pd.to_numeric(case["operational_energy_MWh"], errors="raise"), 626262.056948).any())
        _check(rows, f"PHS-{scenario}-PHYSICAL-EXCLUDED", "626.262 GWh physical evidence is not operational e_nom", physical_used, False, not physical_used)

    price_hash = sha256_file(bundle_dir / "external_prices_hourly.parquet")
    source_hash = sha256_file(_authority_path("external_prices"))
    _check(rows, "EXTERNAL-PRICE-HASH", f"External prices are byte-identical to B10-{year}", price_hash, source_hash, price_hash == source_hash)
    for scenario in SCENARIOS:
        case = prices.loc[prices["scenario"].astype(str).eq(scenario)]
        market_set = set(case["external_market"].astype(str))
        price_snapshots = pd.DatetimeIndex(case["snapshot"].drop_duplicates().sort_values())
        _check(rows, f"PRICE-{scenario}-MARKETS", f"{scenario} prices cover exactly eight accepted markets", sorted(market_set), sorted(PRICE_MARKETS), market_set == set(PRICE_MARKETS))
        _check(rows, f"PRICE-{scenario}-CHRONOLOGY", f"{scenario} prices preserve 8,760 hours", len(price_snapshots), 8760, price_snapshots.equals(chronology))
    inherited = prices.pivot_table(index=["snapshot", "external_market"], columns="scenario", values="price_EUR_per_MWh", aggfunc="first")
    same_prices = bool(inherited[list(SCENARIOS)].nunique(axis=1).eq(1).all())
    _check(rows, "PRICE-SCENARIO-INHERITANCE", "Slow/Base/High inherit the same accepted B10 price series", same_prices, True, same_prices)
    return pd.DataFrame(rows)


def _write_manifest(bundle_dir: Path, row_counts: dict[str, int]) -> pd.DataFrame:
    year = _active_year()
    records = []
    authorities = {
        "load_hourly.parquet": f"B3_2019_ITALY_LOAD_SHAPE|FROZEN_{year}_ZONAL_DEMAND",
        "generator_availability_hourly.parquet": "B3_VRE|B4_BLK005|B4_HYDRO",
        "hydro_inflow_hourly.parquet": "B3_HYDRO_TEMPORAL|B4_HYDRO_ANNUAL_SCALING",
        "hydro_runtime_parameters.csv": "HYDRO_STATIC|PHS_METHOD_C|B4_HYDRO_RUNTIME_MAPPING",
        "external_prices_hourly.parquet": f"ETX7B10_{year}_STAGE_A_TO_STAGE_B_PRICE_HANDOFF_COMPLETE",
    }
    for name in RUNTIME_MEMBERS:
        path = bundle_dir / name
        records.append(
            {
                "file": name,
                "rows": int(row_counts[name]),
                "semantic_key": SEMANTIC_KEYS[name],
                "sha256": sha256_file(path),
                "bytes": int(path.stat().st_size),
                "authority": authorities[name],
                "status": "ACCEPTED_HASH_LOCKED",
            }
        )
    manifest = pd.DataFrame(records)
    manifest_name = MANIFEST_NAME if year == 2040 else "MEM_STAGE_B_2050_ACCEPTED_RUNTIME_MANIFEST_v1.0.csv"
    manifest.to_csv(bundle_dir / manifest_name, index=False, lineterminator="\n")
    return manifest


def _reconciliation(bundle_dir: Path) -> pd.DataFrame:
    year = _active_year()
    records: list[dict[str, Any]] = []

    def add(
        family: str,
        scenario: str,
        zone: str,
        runtime_id: str,
        parameter: str,
        unit: str,
        authority: str,
        authority_key: str,
        accepted: Any,
        artifact: str,
        runtime_key: str,
        observed: Any,
        transformation: str,
        tolerance: float = 0.0,
        notes: str = "",
    ) -> None:
        if isinstance(accepted, (int, float, np.integer, np.floating)) and isinstance(observed, (int, float, np.integer, np.floating)):
            difference: Any = float(observed) - float(accepted)
            passed = abs(float(difference)) <= float(tolerance)
        else:
            difference = ""
            passed = str(observed) == str(accepted)
        records.append(
            {
                "runtime_family": family,
                "scenario": scenario,
                "zone": zone,
                "runtime_id": runtime_id,
                "parameter": parameter,
                "unit": unit,
                "accepted_authority_artifact": authority,
                "accepted_authority_key": authority_key,
                "accepted_value": accepted,
                "runtime_artifact": artifact,
                "runtime_key": runtime_key,
                "runtime_value": observed,
                "transformation": transformation,
                "difference": difference,
                "tolerance": tolerance,
                "classification": transformation,
                "status": "PASS" if passed else "FAIL",
                "notes": notes,
            }
        )

    load = pd.read_parquet(bundle_dir / "load_hourly.parquet")
    availability = pd.read_parquet(bundle_dir / "generator_availability_hourly.parquet", columns=["scenario", "generator_id"])
    hydro = pd.read_parquet(bundle_dir / "hydro_inflow_hourly.parquet")
    params = pd.read_csv(bundle_dir / "hydro_runtime_parameters.csv")
    demand = pd.read_csv(_authority_path("zonal_demand"))
    generators = pd.read_csv(_authority_path("generator_static"))
    storage = pd.read_csv(_authority_path("storage_static"))
    hydro_mapping = pd.read_csv(_authority_path("hydro_static_mapping"))
    internal = pd.read_csv(_authority_path("internal_topology"))
    external = pd.read_csv(_authority_path("external_interfaces"))
    _, inflow_specs = hydro_crosswalk()

    for scenario in SCENARIOS:
        case_load = load.loc[load["scenario"].astype(str).eq(scenario)]
        controls = demand.loc[demand["year"].astype(int).eq(year) & demand["scenario"].astype(str).eq(scenario)]
        for _, row in controls.iterrows():
            zone = str(row["zone"])
            observed = float(case_load.loc[case_load["zone"].astype(str).eq(zone), "load_MW"].sum())
            add("LOAD", scenario, zone, f"LOAD_{zone}", "annual_load", "MWh", "MEM_Annual_Zonal_Demand_Contract.csv", f"{year}|{scenario}|{zone}", float(row["annual_zonal_demand_MWh"]), "load_hourly.parquet", f"{year}|{scenario}|{zone}", observed, "DETERMINISTIC_RUNTIME_TRANSFORMATION", 1e-3, "Common accepted national 2019 shape scaled to the frozen zonal annual control")
        add("LOAD", scenario, "ITALY", "NATIONAL_LOAD", "annual_load", "MWh", "MEM_Annual_Zonal_Demand_Contract.csv", f"{year}|{scenario}|ALL_ZONES", float(controls["annual_zonal_demand_MWh"].sum()), "load_hourly.parquet", f"{year}|{scenario}|ALL_ZONES", float(case_load["load_MW"].sum()), "DETERMINISTIC_RUNTIME_TRANSFORMATION", 1e-3)

        case_generators = generators.loc[generators["year"].astype(int).eq(year) & generators["scenario"].astype(str).eq(scenario)]
        runtime_ids = set(availability.loc[availability["scenario"].astype(str).eq(scenario), "generator_id"].astype(str))
        for (zone, technology), group in case_generators.groupby(["zone", "parent_capacity_technology"], sort=True):
            covered = group.loc[group["generator_id"].astype(str).isin(runtime_ids)]
            add("GENERATOR_CAPACITY", scenario, str(zone), str(technology), "installed_net_capacity", "MW", "MEM_generators_static_final.csv", f"{year}|{scenario}|{zone}|{technology}", float(group["p_nom_MW"].sum()), "generator_availability_hourly.parquet", "runtime_ID_crosswalk_to_frozen_static", float(covered["p_nom_MW"].sum()), "EXACT_MATCH", 1e-6, "Hourly availability constrains the frozen p_nom; it does not create capacity")
        add("CO2", scenario, "ITALY", "STATIC_GENERATOR_CO2_CONTRACT", "covered_generator_rows", "rows", "MEM_generators_static_final.csv", f"{year}|{scenario}|CO2_FIELDS", len(case_generators), "generator_availability_hourly.parquet", "generator_id", len(runtime_ids), "EXACT_MATCH", 0.0, "CO2 rates and prices remain exclusively in the frozen static generator contract")

        case_storage = storage.loc[storage["year"].astype(int).eq(year) & storage["scenario"].astype(str).eq(scenario)]
        for field, unit in (("charge_power_MW", "MW"), ("discharge_power_MW", "MW"), ("energy_capacity_MWh", "MWh")):
            total = float(case_storage[field].sum())
            add("BESS", scenario, "ITALY", "BESS", field, unit, "MEM_storage_static_final.csv", f"{year}|{scenario}|ALL", total, "hydro_runtime_parameters.csv", "NOT_APPLICABLE_STATIC_ONLY", total, "EXACT_MATCH", 1e-6, "BESS capacity remains in the frozen static contract")

        case_params = params.loc[params["scenario"].astype(str).eq(scenario)]
        for _, row in case_params.iterrows():
            zone = str(row["zone"])
            hydro_class = str(row["hydro_class"])
            source = hydro_mapping.loc[hydro_mapping["zone"].astype(str).eq(zone) & hydro_mapping["hydro_class"].astype(str).eq(hydro_class)].iloc[0]
            add("HYDRO_PHS", scenario, zone, str(row["state_id"]), "net_turbine_power", "MW", "MEM_Hydro_Static_Component_Mapping.csv", f"{zone}|{hydro_class}", float(source["p_nom_MW_NET"]), "hydro_runtime_parameters.csv", str(row["state_id"]), float(row["turbine_power_MW"]), "EXACT_MATCH", 1e-6)
        phs = case_params.loc[case_params["hydro_class"].astype(str).isin(["PURE_PHS", "MIXED_PHS"])]
        for field, expected, unit in (("turbine_power_MW", 7252.3, "MW"), ("pump_power_MW", 6400.0, "MW"), ("operational_energy_MWh", 53000.0, "MWh")):
            add("PHS", scenario, "ITALY", "PHS_METHOD_C", field, unit, "MEM_PHS_Static_Runtime_Contract.csv|MEM_PHS_Operational_Energy_Allocation_MethodC.csv", "NATIONAL", expected, "hydro_runtime_parameters.csv", f"{scenario}|PHS", float(phs[field].sum()), "DETERMINISTIC_RUNTIME_TRANSFORMATION", 1e-6)

        case_hydro = hydro.loc[hydro["scenario"].astype(str).eq(scenario)]
        specs = inflow_specs.loc[inflow_specs["scenario"].astype(str).eq(scenario)].set_index("hydro_id")
        for hydro_id, group in case_hydro.groupby("hydro_id", sort=True):
            spec = specs.loc[str(hydro_id)]
            add("HYDRO_INFLOW", scenario, str(spec["zone"]), str(hydro_id), "annual_natural_inflow", "MWh_water", "MEM_ETX7B4_Hydro_Runtime_Inflow_Mapping_v1.0.csv|MEM_Hydro_Static_Component_Mapping.csv", f"{spec['zone']}|{spec['hydro_class']}", float(spec["annual_natural_inflow_MWh_water"]), "hydro_inflow_hourly.parquet", str(hydro_id), float(group["inflow_MW_water_equivalent"].sum()), "DETERMINISTIC_RUNTIME_TRANSFORMATION", 1e-6)

        case_internal = internal.loc[internal["year"].astype(int).eq(year) & internal["scenario"].astype(str).eq(scenario)]
        add("INTERNAL_TOPOLOGY", scenario, "ITALY", "DIRECTIONAL_LINKS", "row_count", "rows", "MEM_Interzonal_Static_Contract.csv", f"{year}|{scenario}", 20, "builder_static_contract", f"{year}|{scenario}", len(case_internal), "EXACT_MATCH", 0.0)
        add("CHRONOLOGY", scenario, "ITALY", "C2019_PREFERRED_NEWER", "hour_count", "hours", "MEM_ETX7B3_Chronology_Selection_v1.0.csv", "2019|UTC", 8760, "all_hourly_runtime_members", f"{year}|{scenario}", int(case_load["snapshot"].nunique()), "EXACT_MATCH", 0.0)

    price_contract = external.loc[external["external_market"].isin(PRICE_MARKETS)]
    add("EXTERNAL_INTERFACE", "ALL", "ITALY", "PRICE_TAKING_INTERFACES", "directional_contract_rows", "rows", "MEM_External_Interface_Static_Contract.csv", "8_MARKETS_X_2_DIRECTIONS", 16, "builder_static_contract", "8_MARKETS_X_2_DIRECTIONS", len(price_contract), "EXACT_MATCH", 0.0)
    cors = external.loc[external["external_market"].astype(str).eq("CORS")]
    add("EXTERNAL_INTERFACE", "ALL", "CORS", "CORS_ZERO_INJECTION_HUB", "directional_contract_rows", "rows", "MEM_External_Interface_Static_Contract.csv", "CORS", 4, "builder_static_contract", "CORS", len(cors), "EXACT_MATCH", 0.0)
    add("EXTERNAL_PRICES", "ALL", "ITALY", f"B10_{year}", "sha256_identity", "sha256", f"stage_a_results/price_handoff/{year}/external_prices_hourly.parquet", "FILE", sha256_file(_authority_path("external_prices")), "external_prices_hourly.parquet", "FILE", sha256_file(bundle_dir / "external_prices_hourly.parquet"), "EXACT_MATCH", 0.0)
    return pd.DataFrame(records)


def promote_and_freeze() -> dict[str, Any]:
    staging_root = ROOT / "tmp" / "stage_b_2040_runtime_1"
    first = staging_root / "rebuild_a"
    second = staging_root / "rebuild_b"
    first_counts = build_runtime_members(first)
    second_counts = build_runtime_members(second)
    first_hashes = member_hashes(first)
    second_hashes = member_hashes(second)
    deterministic = first_hashes == second_hashes and first_counts == second_counts
    if not deterministic:
        raise RuntimeError("Deterministic rebuild verification failed")
    source_hash = sha256_file(_authority_path("external_prices"))
    external_identity = first_hashes["external_prices_hourly.parquet"] == source_hash
    if not external_identity:
        raise RuntimeError("External-price byte identity failed")
    qa_first = validate_runtime(first)
    qa_second = validate_runtime(second)
    if not qa_first["status"].eq("PASS").all() or not qa_second["status"].eq("PASS").all():
        failures = pd.concat([qa_first.loc[qa_first["status"].ne("PASS")], qa_second.loc[qa_second["status"].ne("PASS")]])
        raise RuntimeError(f"Runtime QA failed before promotion:\n{failures.to_string(index=False)}")

    ACCEPTED_RUNTIME.mkdir(parents=True, exist_ok=True)
    for name in RUNTIME_MEMBERS:
        shutil.copyfile(first / name, ACCEPTED_RUNTIME / name)
    manifest = _write_manifest(ACCEPTED_RUNTIME, first_counts)
    now = datetime.now(timezone.utc).isoformat()
    state = {
        "schema_version": "MEM_STAGE_B_2040_ACCEPTED_RUNTIME_STATE_v1.0",
        "status": "ACCEPTED_HASH_LOCKED_RUNTIME",
        "gate": "STAGE_B_2040_BASE_READY_FOR_MANUAL_PRODUCTION_EXECUTION",
        "horizon": YEAR,
        "scenarios": list(SCENARIOS),
        "chronology": "C2019_PREFERRED_NEWER|2019_UTC|8760_HOURS",
        "accepted_runtime_bundle_complete": True,
        "candidate_assumptions_remaining": 0,
        "implementation_drift": 0,
        "external_price_hash_identity": "PASS",
        "production_optimization_executed": False,
        "production_optimization_authorized": True,
        "production_authorization": "2040_BASE_FULL_YEAR_CANONICAL_ONLY",
        "authorized_year": YEAR,
        "authorized_scenarios": ["Base"],
        "unauthorized_scenarios": ["Slow", "High"],
        "authorized_mode": "FULL_YEAR_CANONICAL",
        "authorized_solver": "gurobi",
        "required_gurobipy_version": "13.0.3",
        "solver_options": {"Threads": 1, "Seed": 0, "include_objective_constant": False},
        "authorized_command": r".\.venv\Scripts\python.exe -m mem_model.solve_scenario --year 2040 --scenario Base --execute-full-year",
        "canonical_reporting_pipeline_prepared": True,
        "canonical_reporting_production_figures_generated": False,
        "stage_b_2050": "LOCKED",
        "manifest": MANIFEST_NAME,
        "manifest_sha256": sha256_file(ACCEPTED_RUNTIME / MANIFEST_NAME).upper(),
        "created_at_utc": now,
    }
    dump_json(ACCEPTED_RUNTIME / STATE_NAME, state)
    manifest_verification = verify_accepted_runtime_manifest(ACCEPTED_RUNTIME)
    final_qa = validate_runtime(ACCEPTED_RUNTIME)
    reconciliation = _reconciliation(ACCEPTED_RUNTIME)
    if not final_qa["status"].eq("PASS").all():
        raise RuntimeError("Accepted runtime QA failed after promotion")
    if not reconciliation["status"].eq("PASS").all():
        raise RuntimeError("Static-to-runtime reconciliation failed")
    allowed = {"EXACT_MATCH", "DETERMINISTIC_RUNTIME_TRANSFORMATION"}
    if not set(reconciliation["classification"].astype(str)) <= allowed:
        raise RuntimeError("Reconciliation contains a prohibited classification")
    QA_DIR.mkdir(parents=True, exist_ok=True)
    reconciliation.to_csv(RECONCILIATION_PATH, index=False, lineterminator="\n", float_format="%.15g")
    dump_json(
        QA_PATH,
        {
            "schema_version": "MEM_STAGE_B_2040_RUNTIME_QA_v1.0",
            "status": "PASS",
            "generated_at_utc": now,
            "checks": final_qa.to_dict(orient="records"),
            "pass_count": int(final_qa["status"].eq("PASS").sum()),
            "fail_count": int(final_qa["status"].ne("PASS").sum()),
            "production_optimization_executed": False,
        },
    )
    dump_json(
        HASH_PATH,
        {
            "schema_version": "MEM_STAGE_B_2040_RUNTIME_HASH_VERIFICATION_v1.0",
            "status": "PASS",
            "generated_at_utc": now,
            "byte_identical_deterministic_rebuild": deterministic,
            "rebuild_a": first_hashes,
            "rebuild_b": second_hashes,
            "accepted": member_hashes(ACCEPTED_RUNTIME),
            "external_price_source_sha256": source_hash,
            "external_price_hash_identity": "PASS",
            "manifest_verification": manifest_verification,
        },
    )
    final = {
        "schema_version": "MEM_STAGE_B_2040_RUNTIME_FINAL_VERIFICATION_v1.0",
        "status": "PASS",
        "gate": "STAGE_B_2040_BASE_READY_FOR_MANUAL_PRODUCTION_EXECUTION",
        "generated_at_utc": now,
        "accepted_runtime_bundle_complete": True,
        "runtime_manifest_hash_verified": True,
        "runtime_manifest_sha256": sha256_file(ACCEPTED_RUNTIME / MANIFEST_NAME).upper(),
        "static_to_runtime_reconciliation": "PASS",
        "candidate_assumptions_remaining": 0,
        "implementation_drift": 0,
        "2050_rows": 0,
        "external_price_hash_identity": "PASS",
        "production_optimization_executed": False,
        "canonical_reporting_pipeline_prepared": True,
        "canonical_reporting_production_figures_generated": False,
        "production_authorization": "2040_BASE_FULL_YEAR_CANONICAL_ONLY",
        "authorized_scenarios": ["Base"],
        "unauthorized_scenarios": ["Slow", "High"],
        "authorized_mode": "FULL_YEAR_CANONICAL",
        "authorized_solver": "gurobi",
        "required_gurobipy_version": "13.0.3",
        "b10_2040": "COMPLETE",
        "b10_2050": "LOCKED",
        "stage_b_2050": "LOCKED",
        "runtime_member_count": len(manifest),
        "qa_check_count": len(final_qa),
        "reconciliation_row_count": len(reconciliation),
    }
    dump_json(FINAL_PATH, final)
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and hash-freeze the accepted 2040 Stage-B runtime bundle")
    parser.add_argument("action", choices=("build", "verify"), nargs="?", default="verify")
    args = parser.parse_args()
    if args.action == "build":
        payload = promote_and_freeze()
    else:
        payload = verify_accepted_runtime_manifest(ACCEPTED_RUNTIME)
        qa = validate_runtime(ACCEPTED_RUNTIME)
        if not qa["status"].eq("PASS").all():
            raise SystemExit(qa.loc[qa["status"].ne("PASS")].to_string(index=False))
        payload["qa_status"] = "PASS"
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()

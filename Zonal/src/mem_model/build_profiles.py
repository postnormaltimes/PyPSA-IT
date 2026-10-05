from __future__ import annotations

import argparse
import hashlib
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .common import (
    ACCEPTED_RUNTIME,
    CONFIG,
    FIXTURE_RUNTIME,
    PRICE_MARKETS,
    SCENARIOS,
    STATIC,
    ZONES,
    assert_gate,
    dump_json,
    ensure_output_dirs,
    load_yaml,
    sha256_file,
)
from .runtime_bundle import RUNTIME_FILES, read_runtime_bundle, validate_runtime_bundle


PHASE4B = (
    Path(__file__).resolve().parents[3]
    / "outputs"
    / "01a0595d-8cf1-7202-8ca1-1d3ab732e98e"
    / "thermal_stack_phase"
    / "static_solver_phase4b"
)
AVAILABILITY_ASSUMPTIONS = PHASE4B / "MEM_Generator_Availability_Assumptions.csv"
NUCLEAR_PROFILE = Path(__file__).resolve().parents[3] / "06_PYPSA" / "nuclear_pmax_2050_central.csv"


def _timestamps(frame: pd.DataFrame) -> pd.DatetimeIndex:
    if "snapshot" not in frame:
        raise ValueError("Hourly source table lacks snapshot")
    values = pd.to_datetime(frame["snapshot"], utc=True, errors="raise")
    return pd.DatetimeIndex(values)


def validate_index(index: pd.DatetimeIndex, *, require_complete_year: bool) -> None:
    if not index.is_unique:
        raise ValueError("Duplicate snapshots are prohibited")
    if not index.is_monotonic_increasing:
        raise ValueError("Snapshots must be monotonic")
    if len(index) > 1 and not (index.to_series().diff().dropna() == pd.Timedelta(hours=1)).all():
        raise ValueError("Snapshots are not contiguous hourly observations")
    if require_complete_year and len(index) not in {8760, 8784}:
        raise ValueError(f"A complete year must contain 8760 or 8784 hours, observed {len(index)}")


def maintenance_profile(generator_id: str, outside: float, hours: int, index: pd.DatetimeIndex) -> np.ndarray:
    """Stable, reproducible outage staggering independent of row ordering."""
    result = np.full(len(index), float(outside), dtype=np.float64)
    if hours <= 0:
        return result
    seed = int(hashlib.sha256(generator_id.encode("utf-8")).hexdigest()[:16], 16)
    quarter_starts = np.linspace(0, len(index), 5, dtype=int)[:-1]
    quarter = seed % 4
    span = max(quarter_starts[min(quarter + 1, 3)] - quarter_starts[quarter], 1) if quarter < 3 else max(len(index) - quarter_starts[quarter], 1)
    start = int(quarter_starts[quarter] + (seed // 4) % span)
    outage = (start + np.arange(int(hours))) % len(index)
    result[outage] = 0.0
    return result


def _assumption_lookup(year: int, zone: str, availability_class: str, assumptions: pd.DataFrame) -> pd.Series | None:
    match = assumptions.loc[
        assumptions["year"].astype(int).eq(year)
        & assumptions["zone"].eq(zone)
        & assumptions["availability_class"].eq(availability_class)
    ]
    if match.empty:
        match = assumptions.loc[
            assumptions["year"].astype(int).eq(year)
            & assumptions["availability_class"].eq(availability_class)
        ]
    return None if match.empty else match.iloc[0]


def _synthetic_base_profiles(index: pd.DatetimeIndex) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    hour = np.arange(len(index), dtype=float)
    day_hour = hour % 24
    solar = np.maximum(np.sin(np.pi * (day_hour - 6.0) / 12.0), 0.0)
    wind = np.clip(0.42 + 0.18 * np.sin(2 * np.pi * hour / (24 * 4.7)) + 0.1 * np.cos(2 * np.pi * hour / 31), 0.03, 0.88)
    offshore = np.clip(0.52 + 0.14 * np.sin(2 * np.pi * hour / (24 * 6.2)), 0.12, 0.90)
    runoff = np.clip(0.45 + 0.18 * np.cos(2 * np.pi * hour / (24 * 9.0)), 0.10, 0.80)
    frames: dict[str, pd.DataFrame] = {}
    for name, values in (("solar", solar), ("onwind", wind), ("offwind", offshore), ("ror", runoff)):
        frames[name] = pd.DataFrame(
            {zone: np.clip(values * (0.94 + 0.02 * position), 0.0, 1.0) for position, zone in enumerate(ZONES)},
            index=index,
        )
    load_shape = 1.0 + 0.16 * np.sin(2 * np.pi * (day_hour - 7) / 24) + 0.06 * np.cos(2 * np.pi * hour / (24 * 7))
    return frames, pd.DataFrame({zone: load_shape * (0.98 + 0.006 * i) for i, zone in enumerate(ZONES)}, index=index)


def _nuclear_for_index(index: pd.DatetimeIndex) -> np.ndarray:
    if NUCLEAR_PROFILE.exists():
        source = pd.read_csv(NUCLEAR_PROFILE)
        values = pd.to_numeric(source["fleet_p_max_pu"], errors="raise").to_numpy(dtype=float)
        if len(values) == 8760:
            positions = np.arange(len(index)) % 8760
            return values[positions]
    return np.full(len(index), 0.90)


def _availability_fixture(index: pd.DatetimeIndex, profiles: dict[str, pd.DataFrame]) -> pd.DataFrame:
    generators = pd.read_csv(STATIC / "MEM_generators_static_final.csv")
    assumptions = pd.read_csv(AVAILABILITY_ASSUMPTIONS)
    nuclear = _nuclear_for_index(index)
    rows = []
    for _, generator in generators.iterrows():
        year = int(generator["year"])
        technology = str(generator["solver_subtechnology"])
        zone = str(generator["zone"])
        availability_class = str(generator["availability_class"])
        if technology in {"SOLAR_PV_ROOFTOP", "SOLAR_PV_UTILITY"}:
            values = profiles["solar"][zone].to_numpy()
        elif technology == "WIND_ONSHORE":
            values = profiles["onwind"][zone].to_numpy()
        elif technology == "WIND_OFFSHORE":
            values = profiles["offwind"][zone].to_numpy()
        elif technology == "HYDRO_RUN_OF_RIVER":
            values = profiles["ror"][zone].to_numpy()
        elif technology == "NUCLEAR":
            values = nuclear
        elif availability_class == "GEOTHERMAL_CONSTANT_0P90":
            values = np.full(len(index), 0.90)
        else:
            assumption = _assumption_lookup(year, zone, availability_class, assumptions)
            if assumption is not None and pd.notna(assumption.get("outside_maintenance_p_max_pu")):
                values = maintenance_profile(
                    str(generator["generator_id"]),
                    float(assumption["outside_maintenance_p_max_pu"]),
                    int(round(float(assumption["planned_maintenance_hours"]))),
                    index,
                )
            elif pd.notna(generator.get("static_availability_equivalent")):
                values = np.full(len(index), float(generator["static_availability_equivalent"]))
            else:
                values = np.ones(len(index))
        if not np.isfinite(values).all() or (values < -1e-12).any() or (values > 1 + 1e-12).any():
            raise ValueError(f"Invalid fixture availability for {generator['generator_id']}")
        rows.append(
            pd.DataFrame(
                {
                    "snapshot": index,
                    "year": year,
                    "scenario": generator["scenario"],
                    "generator_id": generator["generator_id"],
                    "p_max_pu": values,
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def _fixture_load(index: pd.DatetimeIndex, load_shapes: pd.DataFrame) -> pd.DataFrame:
    controls = pd.read_csv(STATIC / "MEM_Annual_Zonal_Demand_Contract.csv")
    rows = []
    for _, control in controls.iterrows():
        zone = control["zone"]
        annual_average = float(control["annual_zonal_demand_MWh"]) / 8760.0
        shape = load_shapes[zone].to_numpy(dtype=float)
        shape = shape / shape.mean()
        rows.append(
            pd.DataFrame(
                {
                    "snapshot": index,
                    "year": int(control["year"]),
                    "scenario": control["scenario"],
                    "zone": zone,
                    "load_MW": annual_average * shape,
                    "source_status": "SYNTHETIC_FIXTURE_NOT_MODEL_INPUT",
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def _fixture_hydro(index: pd.DatetimeIndex, profiles: dict[str, pd.DataFrame]) -> pd.DataFrame:
    mapping = pd.read_csv(STATIC / "MEM_Hydro_Static_Component_Mapping.csv")
    rows = []
    for year, scenario in SCENARIOS:
        for _, item in mapping.iterrows():
            hydro_class = str(item["hydro_class"])
            if hydro_class not in {"BASIN_PONDAGE", "RESERVOIR", "MIXED_PHS"}:
                continue
            zone = str(item["zone"])
            capacity = float(item["p_nom_MW_NET"])
            values = profiles["ror"][zone].to_numpy() * capacity * (0.35 if hydro_class != "MIXED_PHS" else 0.08)
            rows.append(
                pd.DataFrame(
                    {
                        "snapshot": index,
                        "year": year,
                        "scenario": scenario,
                        "hydro_id": f"{zone}_{hydro_class}",
                        "zone": zone,
                        "hydro_class": hydro_class,
                        "inflow_MW_water_equivalent": values,
                        "source_status": "SYNTHETIC_FIXTURE_NOT_MODEL_INPUT",
                    }
                )
            )
    return pd.concat(rows, ignore_index=True)


def _fixture_external_prices(index: pd.DatetimeIndex) -> pd.DataFrame:
    hour = np.arange(len(index), dtype=float)
    rows = []
    for year, scenario in SCENARIOS:
        for position, market in enumerate(PRICE_MARKETS):
            values = 70.0 + 3.0 * position + 12.0 * np.sin(2 * np.pi * (hour - 7.0) / 24.0)
            rows.append(
                pd.DataFrame(
                    {
                        "snapshot": index,
                        "year": year,
                        "scenario": scenario,
                        "external_market": market,
                        "price_EUR_per_MWh": values,
                        "source_status": "SYNTHETIC_FIXTURE_NOT_MODEL_INPUT",
                    }
                )
            )
    return pd.concat(rows, ignore_index=True)


def _fixture_hydro_parameters() -> pd.DataFrame:
    mapping = pd.read_csv(STATIC / "MEM_Hydro_Static_Component_Mapping.csv")
    phs = pd.read_csv(STATIC / "MEM_PHS_Static_Runtime_Contract.csv")
    rows = []
    for year, scenario in SCENARIOS:
        for _, item in mapping.iterrows():
            hydro_class = str(item["hydro_class"])
            zone = str(item["zone"])
            power = float(item["p_nom_MW_NET"])
            if hydro_class == "RUN_OF_RIVER":
                continue
            if hydro_class in {"BASIN_PONDAGE", "RESERVOIR"}:
                max_hours = 24.0 if hydro_class == "BASIN_PONDAGE" else 168.0
                rows.append(
                    {
                        "year": year,
                        "scenario": scenario,
                        "state_id": f"{zone}_{hydro_class}",
                        "zone": zone,
                        "hydro_class": hydro_class,
                        "turbine_power_MW": power,
                        "pump_power_MW": 0.0,
                        "operational_energy_MWh": power * max_hours,
                        "turbine_efficiency": 0.90,
                        "pump_efficiency": None,
                        "natural_inflow_allowed": True,
                        "grid_charging_allowed": False,
                        "cyclic_state_of_charge": True,
                        "source_status": "SYNTHETIC_FIXTURE_NOT_MODEL_INPUT",
                    }
                )
        for _, item in phs.iterrows():
            zone = str(item["zone"])
            class_rows = mapping.loc[
                mapping["zone"].eq(zone) & mapping["hydro_class"].isin(["PURE_PHS", "MIXED_PHS"])
            ]
            total_discharge = float(class_rows["p_nom_MW_NET"].sum())
            if total_discharge <= 0:
                continue
            for _, class_row in class_rows.iterrows():
                power = float(class_row["p_nom_MW_NET"])
                if power <= 0:
                    continue
                share = power / total_discharge
                hydro_class = str(class_row["hydro_class"])
                rows.append(
                    {
                        "year": year,
                        "scenario": scenario,
                        "state_id": f"{zone}_{hydro_class}",
                        "zone": zone,
                        "hydro_class": hydro_class,
                        "turbine_power_MW": power,
                        "pump_power_MW": float(item["pump_power_MW"]) * share,
                        "operational_energy_MWh": float(item["operational_energy_MWh"]) * share,
                        "turbine_efficiency": float(item["one_way_efficiency_candidate"]),
                        "pump_efficiency": float(item["one_way_efficiency_candidate"]),
                        "natural_inflow_allowed": hydro_class == "MIXED_PHS",
                        "grid_charging_allowed": True,
                        "cyclic_state_of_charge": True,
                        "source_status": "SYNTHETIC_FIXTURE_NOT_MODEL_INPUT; PURE_MIXED_SPLIT_CANDIDATE_PROPORTIONAL_TO_FROZEN_DISCHARGE_POWER",
                    }
                )
    return pd.DataFrame(rows)


def build_fixture(hours: int = 168) -> Path:
    if hours not in {24, 168}:
        raise ValueError("Synthetic fixtures are limited to 24 or 168 hours")
    ensure_output_dirs()
    out = FIXTURE_RUNTIME / f"h{hours}"
    out.mkdir(parents=True, exist_ok=True)
    index = pd.date_range("2013-03-01 00:00:00", periods=hours, freq="h", tz="UTC", name="snapshot")
    validate_index(index, require_complete_year=False)
    profiles, load_shapes = _synthetic_base_profiles(index)
    tables = {
        "load_hourly.parquet": _fixture_load(index, load_shapes),
        "generator_availability_hourly.parquet": _availability_fixture(index, profiles),
        "hydro_inflow_hourly.parquet": _fixture_hydro(index, profiles),
        "external_prices_hourly.parquet": _fixture_external_prices(index),
    }
    for filename, table in tables.items():
        table.to_parquet(out / filename, index=False, compression="zstd")
    _fixture_hydro_parameters().to_csv(out / "hydro_runtime_parameters.csv", index=False)
    manifest = []
    for path in sorted(out.iterdir()):
        if path.is_file() and path.name != "runtime_input_manifest.json":
            manifest.append({"file": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    dump_json(
        out / "runtime_input_manifest.json",
        {
            "status": "SYNTHETIC_FIXTURE_NOT_MODEL_INPUT",
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "snapshots": hours,
            "timezone": "UTC",
            "chronology_gate_bypassed_for_testing": True,
            "external_price_gate_bypassed_for_testing": True,
            "files": manifest,
        },
    )
    return out


def promote_approved_source_bundle(source_bundle: Path) -> Path:
    """Validate and promote an already-generated approved source bundle.

    Source-specific atlite/PyPSA-Eur generation remains intentionally outside this
    adapter until the two explicit method choices are approved.
    """
    chronology_id = assert_gate("chronology")
    price_id = assert_gate("external_prices")
    assumptions_id = assert_gate("runtime_assumptions")
    required = set(RUNTIME_FILES.values())
    missing = sorted(name for name in required if not (source_bundle / name).exists())
    if missing:
        raise FileNotFoundError(f"Approved source bundle is incomplete: {missing}")
    qa = validate_runtime_bundle(source_bundle, require_complete_year=True)
    if qa["status"].eq("FAIL").any():
        failures = qa.loc[qa["status"].eq("FAIL")]
        raise ValueError("Runtime source bundle failed promotion QA:\n" + failures.to_string(index=False))
    target = ACCEPTED_RUNTIME / f"{chronology_id}__{price_id}"
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"Refusing to overwrite accepted runtime bundle: {target}")
    target.mkdir(parents=True, exist_ok=True)
    # Preserve upstream bytes exactly; QA and lineage sidecars are added separately.
    for name in sorted(required):
        source = source_bundle / name
        destination = target / name
        shutil.copyfile(source, destination)
    qa.to_csv(target / "MEM_RUNTIME_INPUT_QA.csv", index=False)
    manifest = []
    tables = read_runtime_bundle(target)
    for key, filename in RUNTIME_FILES.items():
        path = target / filename
        manifest.append(
            {
                "logical_table": key,
                "file": filename,
                "bytes": path.stat().st_size,
                "rows": len(tables[key]),
                "sha256": sha256_file(path),
            }
        )
    dump_json(
        target / "runtime_input_manifest.json",
        {
            "status": "ACCEPTED_RUNTIME_INPUT_BUNDLE",
            "promoted_utc": datetime.now(timezone.utc).isoformat(),
            "chronology_id": chronology_id,
            "external_price_method_id": price_id,
            "runtime_assumption_bundle_id": assumptions_id,
            "qa_checks": len(qa),
            "qa_failures": 0,
            "files": manifest,
        },
    )
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or promote MEM hourly input bundles")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fixture", action="store_true", help="Create a non-promotable deterministic fixture")
    mode.add_argument("--source-bundle", type=Path, help="Promote a complete bundle after both explicit approvals")
    parser.add_argument("--hours", type=int, default=168, choices=[24, 168])
    args = parser.parse_args()
    if args.fixture:
        print(build_fixture(args.hours))
    else:
        print(promote_approved_source_bundle(args.source_bundle.resolve()))


if __name__ == "__main__":
    main()

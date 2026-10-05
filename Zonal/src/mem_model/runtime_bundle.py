from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .common import PRICE_MARKETS, SCENARIOS, STATIC, ZONES, parse_bool


RUNTIME_FILES = {
    "load": "load_hourly.parquet",
    "availability": "generator_availability_hourly.parquet",
    "hydro_inflow": "hydro_inflow_hourly.parquet",
    "external_prices": "external_prices_hourly.parquet",
    "hydro_parameters": "hydro_runtime_parameters.csv",
}

REQUIRED_COLUMNS = {
    "load": {"snapshot", "year", "scenario", "zone", "load_MW"},
    "availability": {"snapshot", "year", "scenario", "generator_id", "p_max_pu"},
    "hydro_inflow": {
        "snapshot",
        "year",
        "scenario",
        "hydro_id",
        "zone",
        "hydro_class",
        "inflow_MW_water_equivalent",
    },
    "external_prices": {"snapshot", "year", "scenario", "external_market", "price_EUR_per_MWh"},
    "hydro_parameters": {
        "year",
        "scenario",
        "state_id",
        "zone",
        "hydro_class",
        "turbine_power_MW",
        "pump_power_MW",
        "operational_energy_MWh",
        "turbine_efficiency",
        "pump_efficiency",
        "natural_inflow_allowed",
        "grid_charging_allowed",
        "cyclic_state_of_charge",
    },
}

GRAINS = {
    "load": ["snapshot", "year", "scenario", "zone"],
    "availability": ["snapshot", "year", "scenario", "generator_id"],
    "hydro_inflow": ["snapshot", "year", "scenario", "hydro_id"],
    "external_prices": ["snapshot", "year", "scenario", "external_market"],
    "hydro_parameters": ["year", "scenario", "state_id"],
}


def read_runtime_bundle(bundle_dir: Path) -> dict[str, pd.DataFrame]:
    tables: dict[str, pd.DataFrame] = {}
    for key, filename in RUNTIME_FILES.items():
        path = bundle_dir / filename
        if not path.exists():
            continue
        table = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_parquet(path)
        if "snapshot" in table:
            table = table.copy()
            table["snapshot"] = pd.to_datetime(table["snapshot"], utc=True, errors="raise")
        tables[key] = table
    return tables


def _add(
    rows: list[dict[str, Any]],
    check_id: str,
    description: str,
    observed: Any,
    expected: Any,
    passed: bool,
    notes: str = "",
) -> None:
    difference: Any = ""
    if isinstance(observed, (int, float, np.integer, np.floating)) and isinstance(
        expected, (int, float, np.integer, np.floating)
    ):
        difference = float(observed) - float(expected)
    rows.append(
        {
            "check_id": check_id,
            "description": description,
            "observed": observed,
            "expected": expected,
            "difference": difference,
            "status": "PASS" if passed else "FAIL",
            "notes": notes,
        }
    )


def _scenario_subset(table: pd.DataFrame, year: int, scenario: str) -> pd.DataFrame:
    return table.loc[
        table["year"].astype(int).eq(year)
        & table["scenario"].astype(str).str.casefold().eq(scenario.casefold())
    ].copy()


def _snapshot_index(table: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(table["snapshot"].drop_duplicates().sort_values())


def validate_runtime_bundle(bundle_dir: Path, *, require_complete_year: bool = True) -> pd.DataFrame:
    """Validate a six-scenario runtime bundle without constructing a PyPSA network.

    The function is intentionally strict at the declared table grain. It is used
    before immutable promotion to ``runtime_inputs/accepted`` and can also check
    the short synthetic fixtures with ``require_complete_year=False``.
    """

    rows: list[dict[str, Any]] = []
    tables = read_runtime_bundle(bundle_dir)
    for key, filename in RUNTIME_FILES.items():
        exists = key in tables
        _add(rows, f"RB-{key}-FILE", f"Required runtime file {filename} exists", exists, True, exists)
        if not exists:
            continue
        missing = sorted(REQUIRED_COLUMNS[key] - set(tables[key].columns))
        _add(rows, f"RB-{key}-SCHEMA", f"{filename} contains the required schema", "|".join(missing), "", not missing)
        if missing:
            continue
        duplicated = int(tables[key].duplicated(GRAINS[key]).sum())
        _add(rows, f"RB-{key}-GRAIN", f"{filename} is unique at its canonical grain", duplicated, 0, duplicated == 0)

    if len(tables) != len(RUNTIME_FILES) or any(
        REQUIRED_COLUMNS[key] - set(tables[key].columns) for key in tables
    ):
        return pd.DataFrame(rows)

    generators = pd.read_csv(STATIC / "MEM_generators_static_final.csv")
    demand = pd.read_csv(STATIC / "MEM_Annual_Zonal_Demand_Contract.csv")
    phs_control = pd.read_csv(STATIC / "MEM_PHS_Static_Runtime_Contract.csv").set_index("zone")
    chronology_reference: pd.DatetimeIndex | None = None

    for year, scenario in SCENARIOS:
        label = f"{year}-{scenario.upper()}"
        subsets = {key: _scenario_subset(table, year, scenario) for key, table in tables.items()}
        indices = {key: _snapshot_index(table) for key, table in subsets.items() if "snapshot" in table}
        base = indices["load"]
        expected_hours = 8784 if (len(base) and base[0].is_leap_year) else 8760
        expected_hours = expected_hours if require_complete_year else len(base)
        contiguous = len(base) <= 1 or bool((base.to_series().diff().dropna() == pd.Timedelta(hours=1)).all())
        _add(rows, f"RB-{label}-HOURS", "Chronology has the required number of hourly snapshots", len(base), expected_hours, len(base) == expected_hours)
        _add(rows, f"RB-{label}-CONTIGUOUS", "Chronology is contiguous and unique", contiguous and base.is_unique, True, contiguous and base.is_unique)
        aligned = all(base.equals(index) for index in indices.values())
        _add(rows, f"RB-{label}-ALIGNED", "All hourly tables share one snapshot index", aligned, True, aligned)
        if chronology_reference is None:
            chronology_reference = base
        same_chronology = chronology_reference.equals(base)
        _add(rows, f"RB-{label}-COMMON-CHRONOLOGY", "All scenarios use the same weather chronology", same_chronology, True, same_chronology)

        load = subsets["load"]
        observed_zones = set(load["zone"].astype(str))
        _add(rows, f"RB-{label}-LOAD-ZONES", "Load covers exactly seven canonical zones", "|".join(sorted(observed_zones)), "|".join(sorted(ZONES)), observed_zones == set(ZONES))
        load_values = pd.to_numeric(load["load_MW"], errors="coerce")
        load_ok = load_values.notna().all() and np.isfinite(load_values).all() and (load_values >= 0).all()
        _add(rows, f"RB-{label}-LOAD-VALUES", "Hourly load is finite and non-negative", bool(load_ok), True, bool(load_ok))
        _add(rows, f"RB-{label}-LOAD-ROWS", "Load contains one row per snapshot and zone", len(load), len(base) * len(ZONES), len(load) == len(base) * len(ZONES))
        controls = _scenario_subset(demand, year, scenario).set_index("zone")
        for zone in ZONES:
            observed = float(load.loc[load["zone"].eq(zone), "load_MW"].sum())
            expected = float(controls.at[zone, "annual_zonal_demand_MWh"])
            if not require_complete_year:
                expected *= len(base) / 8760.0
            _add(rows, f"RB-{label}-LOAD-{zone}", f"{zone} annual demand reconciles", observed, expected, math.isclose(observed, expected, abs_tol=1e-3))

        availability = subsets["availability"]
        expected_generators = set(
            _scenario_subset(generators, year, scenario)["generator_id"].astype(str)
        )
        observed_generators = set(availability["generator_id"].astype(str))
        _add(rows, f"RB-{label}-GENERATOR-IDS", "Availability covers every frozen generator exactly", len(expected_generators ^ observed_generators), 0, expected_generators == observed_generators)
        _add(rows, f"RB-{label}-AVAILABILITY-ROWS", "Availability contains one row per snapshot and frozen generator", len(availability), len(base) * len(expected_generators), len(availability) == len(base) * len(expected_generators))
        availability_values = pd.to_numeric(availability["p_max_pu"], errors="coerce")
        availability_ok = (
            availability_values.notna().all()
            and np.isfinite(availability_values).all()
            and (availability_values >= -1e-12).all()
            and (availability_values <= 1 + 1e-12).all()
        )
        _add(rows, f"RB-{label}-AVAILABILITY", "Generator availability is finite and within [0,1]", bool(availability_ok), True, bool(availability_ok))

        prices = subsets["external_prices"]
        markets = set(prices["external_market"].astype(str))
        _add(rows, f"RB-{label}-PRICE-MARKETS", "External prices cover exactly eight price-taking markets", "|".join(sorted(markets)), "|".join(sorted(PRICE_MARKETS)), markets == set(PRICE_MARKETS))
        _add(rows, f"RB-{label}-PRICE-ROWS", "External prices contain one row per snapshot and market", len(prices), len(base) * len(PRICE_MARKETS), len(prices) == len(base) * len(PRICE_MARKETS))
        price_values = pd.to_numeric(prices["price_EUR_per_MWh"], errors="coerce")
        price_ok = price_values.notna().all() and np.isfinite(price_values).all()
        _add(rows, f"RB-{label}-PRICE-VALUES", "External prices are finite", bool(price_ok), True, bool(price_ok))

        parameters = subsets["hydro_parameters"]
        hydro_classes = parameters["hydro_class"].astype(str).str.upper()
        aggregate_states = int(hydro_classes.str.startswith("PHS_AGGREGATE").sum())
        _add(
            rows,
            f"RB-{label}-PHS-SEPARATION",
            "Accepted annual bundle separates pure and mixed PHS states",
            aggregate_states,
            0,
            aggregate_states == 0 or not require_complete_year,
            "Aggregate PHS is allowed only in explicitly non-model synthetic fixtures.",
        )
        phs_mask = hydro_classes.isin(["PURE_PHS", "MIXED_PHS"]) | hydro_classes.str.startswith("PHS_AGGREGATE")
        phs = parameters.loc[phs_mask]
        phs_energy = float(pd.to_numeric(phs["operational_energy_MWh"], errors="coerce").sum())
        phs_pump = float(pd.to_numeric(phs["pump_power_MW"], errors="coerce").sum())
        phs_discharge = float(pd.to_numeric(phs["turbine_power_MW"], errors="coerce").sum())
        _add(rows, f"RB-{label}-PHS-ENERGY", "PHS operational energy reconciles", phs_energy, 53000.0, math.isclose(phs_energy, 53000.0, abs_tol=1e-6))
        _add(rows, f"RB-{label}-PHS-PUMP", "PHS pump power reconciles", phs_pump, 6400.0, math.isclose(phs_pump, 6400.0, abs_tol=1e-6))
        _add(rows, f"RB-{label}-PHS-DISCHARGE", "PHS discharge power reconciles", phs_discharge, 7252.3, math.isclose(phs_discharge, 7252.3, abs_tol=1e-6))
        _add(rows, f"RB-{label}-PHS-626-EXCLUDED", "626.262 GWh physical evidence is not operational e_nom", bool(np.isclose(pd.to_numeric(parameters["operational_energy_MWh"], errors="coerce"), 626262.056948).any()), False, not np.isclose(pd.to_numeric(parameters["operational_energy_MWh"], errors="coerce"), 626262.056948).any())

        for zone in ZONES:
            zone_phs = phs.loc[phs["zone"].eq(zone)]
            for field, control_field, suffix in (
                ("operational_energy_MWh", "operational_energy_MWh", "ENERGY"),
                ("pump_power_MW", "pump_power_MW", "PUMP"),
                ("turbine_power_MW", "discharge_power_MW_NET", "DISCHARGE"),
            ):
                observed = float(pd.to_numeric(zone_phs[field], errors="coerce").sum())
                expected = float(phs_control.at[zone, control_field])
                _add(rows, f"RB-{label}-PHS-{zone}-{suffix}", f"{zone} PHS {suffix.lower()} matches the frozen contract", observed, expected, math.isclose(observed, expected, abs_tol=1e-6))

        pure = parameters.loc[hydro_classes.eq("PURE_PHS")]
        mixed = parameters.loc[hydro_classes.eq("MIXED_PHS")]
        pure_inflow_ok = pure.empty or not pure["natural_inflow_allowed"].map(parse_bool).any()
        mixed_topology_ok = mixed.empty or (
            mixed["natural_inflow_allowed"].map(parse_bool).all()
            and mixed["grid_charging_allowed"].map(parse_bool).all()
        )
        conventional = parameters.loc[hydro_classes.isin(["BASIN_PONDAGE", "RESERVOIR"])]
        conventional_no_pump = conventional.empty or (
            ~conventional["grid_charging_allowed"].map(parse_bool)
        ).all()
        _add(rows, f"RB-{label}-PURE-PHS-INFLOW", "Pure PHS has no natural inflow", bool(pure_inflow_ok), True, bool(pure_inflow_ok))
        _add(rows, f"RB-{label}-MIXED-PHS-TOPOLOGY", "Mixed PHS has natural inflow and grid pumping on one state", bool(mixed_topology_ok), True, bool(mixed_topology_ok))
        _add(rows, f"RB-{label}-CONVENTIONAL-NO-PUMP", "Conventional basin/reservoir hydro cannot grid-charge", bool(conventional_no_pump), True, bool(conventional_no_pump))

        inflow = subsets["hydro_inflow"]
        required_states = set(parameters.loc[parameters["natural_inflow_allowed"].map(parse_bool), "state_id"].astype(str))
        observed_states = set(inflow["hydro_id"].astype(str))
        missing_states = required_states - observed_states
        _add(rows, f"RB-{label}-HYDRO-INFLOW-COVERAGE", "Every naturally charged state has an hourly inflow series", len(missing_states), 0, not missing_states, "|".join(sorted(missing_states)))
        incomplete_states = {
            state
            for state, count in inflow.loc[inflow["hydro_id"].astype(str).isin(required_states)]
            .groupby(inflow["hydro_id"].astype(str))["snapshot"]
            .nunique()
            .items()
            if int(count) != len(base)
        }
        _add(rows, f"RB-{label}-HYDRO-INFLOW-ROWS", "Each naturally charged state has one value per snapshot", len(incomplete_states), 0, not incomplete_states, "|".join(sorted(incomplete_states)))
        inflow_values = pd.to_numeric(inflow["inflow_MW_water_equivalent"], errors="coerce")
        inflow_ok = inflow_values.notna().all() and np.isfinite(inflow_values).all() and (inflow_values >= 0).all()
        _add(rows, f"RB-{label}-HYDRO-INFLOW-VALUES", "Hydro inflow is finite and non-negative", bool(inflow_ok), True, bool(inflow_ok))

    return pd.DataFrame(rows)

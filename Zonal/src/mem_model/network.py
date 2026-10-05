from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pypsa

from .common import ACCEPTED_RUNTIME, ACCEPTED_RUNTIME_2050, PRICE_MARKETS, STATIC, ZONES, parse_bool, safe_id
from .runtime_bundle import GRAINS
from .stage_b_2040_runtime import contract as accepted_runtime_contract
from .stage_b_2040_runtime import runtime_horizon
from .stage_b_2040_runtime import verify_accepted_runtime_manifest


HOURLY_FILES = {
    "load": "load_hourly.parquet",
    "availability": "generator_availability_hourly.parquet",
    "hydro_inflow": "hydro_inflow_hourly.parquet",
    "external_prices": "external_prices_hourly.parquet",
    "hydro_parameters": "hydro_runtime_parameters.csv",
}


def _read_hourly(path: Path, year: int, scenario: str) -> pd.DataFrame:
    frame = pd.read_parquet(
        path,
        filters=[("year", "==", int(year)), ("scenario", "==", str(scenario).title())],
    )
    if "snapshot" not in frame:
        raise ValueError(f"{path.name} lacks snapshot")
    source = pd.to_datetime(frame["snapshot"], utc=True, errors="raise")
    frame = frame.copy()
    frame["snapshot"] = source.dt.tz_convert(None)
    return frame


def load_runtime_bundle(runtime_dir: Path, year: int, scenario: str) -> dict[str, pd.DataFrame]:
    if runtime_dir.resolve() == ACCEPTED_RUNTIME.resolve():
        verify_accepted_runtime_manifest(runtime_dir, expected_year=year)
    elif runtime_dir.resolve() == ACCEPTED_RUNTIME_2050.resolve():
        from .stage_b_2050_runtime import verify_prepared_runtime_manifest

        verify_prepared_runtime_manifest(runtime_dir, expected_year=year)
    missing = [filename for filename in HOURLY_FILES.values() if not (runtime_dir / filename).exists()]
    if missing:
        raise FileNotFoundError(f"Runtime bundle is incomplete: {missing}")
    bundle: dict[str, pd.DataFrame] = {}
    for key, filename in HOURLY_FILES.items():
        path = runtime_dir / filename
        frame = pd.read_csv(path) if path.suffix == ".csv" else _read_hourly(path, year, scenario)
        if {"year", "scenario"}.issubset(frame.columns):
            frame = frame.loc[frame["year"].astype(int).eq(year) & frame["scenario"].astype(str).str.casefold().eq(scenario.casefold())]
        grain = [column for column in GRAINS[key] if column in frame.columns]
        duplicated = int(frame.duplicated(grain).sum()) if grain else 0
        if duplicated:
            raise ValueError(f"{filename} contains {duplicated} duplicate rows at grain {grain}")
        bundle[key] = frame.copy()
    return bundle


def _snapshots(bundle: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    sets = []
    for key in ("load", "availability", "hydro_inflow", "external_prices"):
        values = pd.DatetimeIndex(bundle[key]["snapshot"].drop_duplicates().sort_values())
        sets.append(values)
    first = sets[0]
    if not all(first.equals(candidate) for candidate in sets[1:]):
        raise ValueError("Runtime tables do not share an identical snapshot index")
    if len(first) > 1 and not (first.to_series().diff().dropna() == pd.Timedelta(hours=1)).all():
        raise ValueError("Snapshot index is not contiguous hourly")
    return first


def _add_carriers(network: pypsa.Network) -> None:
    carriers = pd.read_csv(STATIC / "MEM_carriers_static_final.csv")
    for _, row in carriers.iterrows():
        raw = pd.to_numeric(pd.Series([row.get("direct_CO2_t_per_MWh_th")]), errors="coerce").iloc[0]
        network.add(
            "Carrier",
            str(row["carrier"]),
            co2_emissions=0.0 if pd.isna(raw) else float(raw),
        )
    for name in (
        "AC",
        "battery_energy",
        "water_energy",
        "internal_transfer",
        "external_market",
        "external_trade",
        "corsica_hub",
        "load_shedding",
        "spillage",
    ):
        if name not in network.carriers.index:
            network.add("Carrier", name)


def _add_static_generators(
    network: pypsa.Network,
    year: int,
    scenario: str,
    availability: pd.DataFrame,
) -> pd.DataFrame:
    source = pd.read_csv(STATIC / "MEM_generators_static_final.csv")
    subset = source.loc[
        source["year"].astype(int).eq(year)
        & source["scenario"].astype(str).str.casefold().eq(scenario.casefold())
    ].copy()
    if subset.empty:
        raise ValueError(f"No frozen generators for {year} {scenario}")
    if subset["generator_id"].duplicated().any():
        raise ValueError("Frozen generator IDs are not unique")
    for _, row in subset.iterrows():
        if parse_bool(row["p_nom_extendable"]):
            raise ValueError(f"Extendable frozen generator prohibited: {row['generator_id']}")
        efficiency = pd.to_numeric(pd.Series([row.get("efficiency")]), errors="coerce").iloc[0]
        network.add(
            "Generator",
            str(row["generator_id"]),
            bus=str(row["zone"]),
            carrier=str(row["carrier"]),
            p_nom=float(row["p_nom_MW"]),
            p_nom_extendable=False,
            committable=False,
            efficiency=1.0 if pd.isna(efficiency) else float(efficiency),
            marginal_cost=float(row["marginal_cost_EUR2025_per_MWh_el"]),
        )
    pivot = availability.pivot(index="snapshot", columns="generator_id", values="p_max_pu")
    expected = set(subset["generator_id"].astype(str))
    observed = set(pivot.columns.astype(str))
    if expected != observed:
        raise ValueError(f"Availability generator mismatch: missing={sorted(expected-observed)[:5]}, extra={sorted(observed-expected)[:5]}")
    pivot = pivot.reindex(index=network.snapshots, columns=subset["generator_id"].astype(str))
    if pivot.isna().any().any() or not np.isfinite(pivot.to_numpy()).all():
        raise ValueError("Generator availability has missing or non-finite values")
    if (pivot.to_numpy() < -1e-12).any() or (pivot.to_numpy() > 1 + 1e-12).any():
        raise ValueError("Generator availability outside [0,1]")
    network.generators_t.p_max_pu = pivot
    return subset


def _add_loads(network: pypsa.Network, load: pd.DataFrame) -> None:
    pivot = load.pivot(index="snapshot", columns="zone", values="load_MW").reindex(index=network.snapshots, columns=ZONES)
    if pivot.isna().any().any() or (pivot.to_numpy() < 0).any():
        raise ValueError("Zonal load is incomplete or negative")
    for zone in ZONES:
        network.add("Load", f"LOAD_{zone}", bus=zone)
    network.loads_t.p_set = pivot.rename(columns={zone: f"LOAD_{zone}" for zone in ZONES})


def _add_bess(network: pypsa.Network, year: int, scenario: str) -> pd.DataFrame:
    source = pd.read_csv(STATIC / "MEM_storage_static_final.csv")
    subset = source.loc[
        source["year"].astype(int).eq(year)
        & source["scenario"].astype(str).str.casefold().eq(scenario.casefold())
    ].copy()
    for position, row in subset.iterrows():
        if parse_bool(row["p_nom_extendable"]) or parse_bool(row["e_nom_extendable"]):
            raise ValueError("Extendable BESS capacity is prohibited")
        stem = safe_id(f"{year}_{scenario}_{row['zone']}_{row['technology']}_{position}")
        energy_bus = f"BESS_BUS_{stem}"
        store = f"BESS_STORE_{stem}"
        network.add("Bus", energy_bus, carrier="battery_energy")
        network.add(
            "Store",
            store,
            bus=energy_bus,
            carrier="battery_energy",
            e_nom=float(row["energy_capacity_MWh"]),
            e_nom_extendable=False,
            e_cyclic=True,
            standing_loss=float(row["standing_loss_per_hour"]),
        )
        network.add(
            "Link",
            f"BESS_CHARGE_{stem}",
            bus0=str(row["zone"]),
            bus1=energy_bus,
            carrier="battery_energy",
            p_nom=float(row["charge_power_MW"]),
            p_nom_extendable=False,
            p_min_pu=0.0,
            efficiency=float(row["charge_efficiency"]),
        )
        network.add(
            "Link",
            f"BESS_DISCHARGE_{stem}",
            bus0=energy_bus,
            bus1=str(row["zone"]),
            carrier="battery_energy",
            # PyPSA Link p_nom is measured at bus0.  The frozen contract is
            # net electrical discharge at bus1, so divide by efficiency.
            p_nom=float(row["discharge_power_MW"]) / float(row["discharge_efficiency"]),
            p_nom_extendable=False,
            p_min_pu=0.0,
            efficiency=float(row["discharge_efficiency"]),
        )
    return subset


def _hydro_inflow_for_state(
    inflow: pd.DataFrame,
    state_id: str,
    zone: str,
    hydro_class: str,
) -> pd.Series | None:
    exact = inflow.loc[inflow["hydro_id"].astype(str).eq(state_id)]
    if not exact.empty:
        return exact.set_index("snapshot")["inflow_MW_water_equivalent"].sort_index()
    # Retained solely for the explicitly non-model synthetic fixtures.  The
    # accepted annual bundle always resolves by exact state ID above.
    if hydro_class.startswith("PHS_AGGREGATE"):
        candidates = inflow.loc[inflow["zone"].eq(zone) & inflow["hydro_class"].eq("MIXED_PHS")]
    else:
        candidates = inflow.loc[inflow["zone"].eq(zone) & inflow["hydro_class"].eq(hydro_class)]
    if candidates.empty:
        return None
    return candidates.groupby("snapshot")["inflow_MW_water_equivalent"].sum().reindex(inflow["snapshot"].drop_duplicates().sort_values())


def _add_hydro_states(network: pypsa.Network, parameters: pd.DataFrame, inflow: pd.DataFrame) -> None:
    for _, row in parameters.iterrows():
        state_id = safe_id(row["state_id"])
        zone = str(row["zone"])
        hydro_class = str(row["hydro_class"])
        bus = f"WATER_{state_id}"
        network.add("Bus", bus, carrier="water_energy")
        network.add(
            "Store",
            f"STORE_{state_id}",
            bus=bus,
            carrier="water_energy",
            e_nom=float(row["operational_energy_MWh"]),
            e_nom_extendable=False,
            e_cyclic=parse_bool(row["cyclic_state_of_charge"]),
        )
        turbine_efficiency = float(row["turbine_efficiency"])
        network.add(
            "Link",
            f"TURBINE_{state_id}",
            bus0=bus,
            bus1=zone,
            carrier="water_energy",
            # Frozen hydro capacity is NET electrical output.  Link p_nom is
            # water input at bus0, therefore the input rating must be grossed
            # up so p_nom * efficiency equals the accepted net control.
            p_nom=float(row["turbine_power_MW"]) / turbine_efficiency,
            p_nom_extendable=False,
            p_min_pu=0.0,
            efficiency=turbine_efficiency,
        )
        pump_power = float(row.get("pump_power_MW", 0.0) or 0.0)
        if pump_power > 0:
            if not parse_bool(row["grid_charging_allowed"]):
                raise ValueError(f"Pump power assigned to non-grid-chargeable hydro state {state_id}")
            network.add(
                "Link",
                f"PUMP_{state_id}",
                bus0=zone,
                bus1=bus,
                carrier="water_energy",
                p_nom=pump_power,
                p_nom_extendable=False,
                p_min_pu=0.0,
                efficiency=float(row["pump_efficiency"]),
            )
        series = _hydro_inflow_for_state(inflow, str(row["state_id"]), zone, hydro_class)
        if parse_bool(row["natural_inflow_allowed"]):
            if series is None or series.isna().any():
                raise ValueError(f"Natural inflow required but missing for {state_id}")
            series.index = pd.DatetimeIndex(series.index)
            series = series.reindex(network.snapshots)
            maximum = max(float(series.max()), 1e-9)
            generator = f"INFLOW_{state_id}"
            network.add("Generator", generator, bus=bus, carrier="water_energy", p_nom=maximum, p_nom_extendable=False)
            network.generators_t.p_max_pu[generator] = series / maximum
            network.generators_t.p_min_pu[generator] = series / maximum
        elif series is not None and float(series.sum()) > 1e-9:
            raise ValueError(f"Pure pumped state unexpectedly received natural inflow: {state_id}")
        if not hydro_class.startswith("PHS_AGGREGATE") or parse_bool(row["natural_inflow_allowed"]):
            spill_capacity = max(float(row["turbine_power_MW"]) + pump_power, 1.0)
            network.add(
                "Generator",
                f"SPILL_{state_id}",
                bus=bus,
                carrier="spillage",
                sign=-1.0,
                p_nom=spill_capacity,
                p_nom_extendable=False,
                marginal_cost=0.0,
            )


def _add_internal_links(network: pypsa.Network, year: int, scenario: str) -> pd.DataFrame:
    source = pd.read_csv(STATIC / "MEM_Interzonal_Static_Contract.csv")
    subset = source.loc[
        source["year"].astype(int).eq(year)
        & source["scenario"].astype(str).str.casefold().eq(scenario.casefold())
    ].copy()
    for _, row in subset.iterrows():
        network.add(
            "Link",
            safe_id(f"INTERNAL_{row['from_zone']}_TO_{row['to_zone']}"),
            bus0=str(row["from_zone"]),
            bus1=str(row["to_zone"]),
            carrier="internal_transfer",
            p_nom=float(row["capacity_MW"]),
            p_nom_extendable=False,
            p_min_pu=0.0,
            efficiency=1.0,
        )
    return subset


def _add_external_markets(
    network: pypsa.Network,
    external_prices: pd.DataFrame,
    assumptions: dict[str, Any],
) -> tuple[pd.DataFrame, float]:
    contract = pd.read_csv(STATIC / "MEM_External_Interface_Static_Contract.csv")
    toll = float(assumptions["accepted_rules"]["external_transaction_cost_EUR_per_MWh"])
    external_efficiency = float(assumptions["accepted_rules"]["external_link_efficiency"])
    dynamic_costs: dict[str, pd.Series] = {}
    objective_baseline = 0.0
    price_contract = contract.loc[contract["price_series_required"].astype(str).str.lower().eq("true")].copy()
    for market in PRICE_MARKETS:
        group = price_contract.loc[price_contract["external_market"].eq(market)]
        if len(group) != 2:
            raise ValueError(f"Expected import and export contracts for {market}")
        import_row = group.loc[group["direction"].eq("IMPORT")].iloc[0]
        export_row = group.loc[group["direction"].eq("EXPORT")].iloc[0]
        import_capacity = float(import_row["capacity_MW"])
        export_capacity = float(export_row["capacity_MW"])
        bus = f"EXT_{market}"
        generator = f"EXT_SUPPLY_{market}"
        network.add("Bus", bus, carrier="external_market")
        network.add("Load", f"EXT_BASELINE_LOAD_{market}", bus=bus, p_set=export_capacity)
        network.add(
            "Generator",
            generator,
            bus=bus,
            carrier="external_market",
            p_nom=import_capacity + export_capacity,
            p_nom_extendable=False,
        )
        prices = (
            external_prices.loc[external_prices["external_market"].eq(market)]
            .set_index("snapshot")["price_EUR_per_MWh"]
            .reindex(network.snapshots)
        )
        if prices.isna().any() or not np.isfinite(prices.to_numpy()).all():
            raise ValueError(f"External prices incomplete for {market}")
        dynamic_costs[generator] = prices
        objective_baseline += float((prices * export_capacity).sum())
        network.add(
            "Link",
            f"IMPORT_{market}_TO_{import_row['Italian_zone']}",
            bus0=bus,
            bus1=str(import_row["Italian_zone"]),
            carrier="external_trade",
            p_nom=import_capacity,
            p_nom_extendable=False,
            p_min_pu=0.0,
            efficiency=external_efficiency,
            marginal_cost=toll,
        )
        network.add(
            "Link",
            f"EXPORT_{export_row['Italian_zone']}_TO_{market}",
            bus0=str(export_row["Italian_zone"]),
            bus1=bus,
            carrier="external_trade",
            p_nom=export_capacity,
            p_nom_extendable=False,
            p_min_pu=0.0,
            efficiency=external_efficiency,
            marginal_cost=toll,
        )
    if dynamic_costs:
        price_frame = pd.DataFrame(dynamic_costs, index=network.snapshots)
        for column in price_frame:
            network.generators_t.marginal_cost[column] = price_frame[column]

    corsica = contract.loc[contract["external_market"].eq("CORS")].copy()
    if len(corsica) != 4:
        raise ValueError("Frozen Corsica contract must contain four directional hub legs")
    network.add("Bus", "CORS", carrier="corsica_hub")
    for _, row in corsica.iterrows():
        zone = str(row["Italian_zone"])
        if row["direction"] == "EXPORT":
            bus0, bus1 = zone, "CORS"
        else:
            bus0, bus1 = "CORS", zone
        network.add(
            "Link",
            safe_id(f"CORS_{zone}_{row['direction']}"),
            bus0=bus0,
            bus1=bus1,
            carrier="corsica_hub",
            p_nom=float(row["capacity_MW"]),
            p_nom_extendable=False,
            p_min_pu=0.0,
            efficiency=1.0,
        )
    return contract, objective_baseline


def _add_load_shedding(network: pypsa.Network, assumptions: dict[str, Any]) -> None:
    voll = float(assumptions["accepted_rules"]["load_shedding_VOLL_EUR_per_MWh"])
    for zone in ZONES:
        load_name = f"LOAD_{zone}"
        peak = float(network.loads_t.p_set[load_name].max())
        if peak <= 0:
            raise ValueError(f"Cannot construct load-shedding cap for zero-load zone {zone}")
        generator = f"LOAD_SHEDDING_{zone}"
        network.add(
            "Generator",
            generator,
            bus=zone,
            carrier="load_shedding",
            p_nom=peak,
            p_nom_extendable=False,
            marginal_cost=voll,
        )
        network.generators_t.p_max_pu[generator] = network.loads_t.p_set[load_name] / peak


def build_network(runtime_dir: Path, year: int, scenario: str) -> tuple[pypsa.Network, dict[str, Any]]:
    scenario = scenario.title()
    manifest_verification: dict[str, Any] | None = None
    if runtime_dir.resolve() == ACCEPTED_RUNTIME.resolve():
        manifest_verification = verify_accepted_runtime_manifest(runtime_dir, expected_year=year)
    elif runtime_dir.resolve() == ACCEPTED_RUNTIME_2050.resolve():
        from .stage_b_2050_runtime import verify_prepared_runtime_manifest

        manifest_verification = verify_prepared_runtime_manifest(runtime_dir, expected_year=year)
    bundle = load_runtime_bundle(runtime_dir, year, scenario)
    snapshots = _snapshots(bundle)
    with runtime_horizon(year):
        assumptions = accepted_runtime_contract()
    network = pypsa.Network()
    network.set_snapshots(snapshots)
    _add_carriers(network)
    for zone in ZONES:
        network.add("Bus", zone, carrier="AC")
    frozen_generators = _add_static_generators(network, year, scenario, bundle["availability"])
    _add_loads(network, bundle["load"])
    bess = _add_bess(network, year, scenario)
    _add_hydro_states(network, bundle["hydro_parameters"], bundle["hydro_inflow"])
    internal = _add_internal_links(network, year, scenario)
    external, objective_baseline = _add_external_markets(network, bundle["external_prices"], assumptions)
    _add_load_shedding(network, assumptions)
    network.meta = {
        "model": "MEM_INDEPENDENT_SEVEN_ZONE_FIXED_CAPACITY",
        "year": int(year),
        "scenario": scenario,
        "runtime_bundle": str(runtime_dir),
        "runtime_manifest_sha256": None if manifest_verification is None else manifest_verification["manifest_sha256"],
        "runtime_authority": (f"ACCEPTED_HASH_LOCKED_{year}" if manifest_verification is not None else "SYNTHETIC_FIXTURE_NOT_MODEL_INPUT"),
        "external_auxiliary_objective_baseline_EUR": objective_baseline,
        "external_net_trade_cost_rule": "subtract auxiliary fixed-load baseline from raw external objective terms",
        "full_year_solve_authorized": False,
        "production_authorization": "PENDING_METHOD_REVIEW",
    }
    metadata = {
        "year": year,
        "scenario": scenario,
        "snapshots": len(snapshots),
        "frozen_generator_rows": len(frozen_generators),
        "frozen_generator_MW": float(frozen_generators["p_nom_MW"].sum()),
        "bess_rows": len(bess),
        "bess_discharge_MW": float(bess["discharge_power_MW"].sum()),
        "bess_energy_MWh": float(bess["energy_capacity_MWh"].sum()),
        "internal_directional_links": len(internal),
        "external_contract_rows": len(external),
        "external_auxiliary_objective_baseline_EUR": objective_baseline,
    }
    return network, metadata

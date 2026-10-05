from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import pypsa

from ..common import CONFIG, dump_json, load_yaml, sha256_file


REPORTING_CONFIG = CONFIG / "stage_b_reporting.yaml"


def reporting_config() -> dict[str, Any]:
    config = load_yaml(REPORTING_CONFIG)
    if config.get("status") not in {"PREPARED_NOT_EXECUTED_ON_PRODUCTION_RESULTS", "PRODUCTION_REPORTING_ACTIVE_2040"}:
        raise RuntimeError("Canonical reporting configuration has an unsupported state")
    return config


def _network(value: pypsa.Network | str | Path) -> pypsa.Network:
    return value if isinstance(value, pypsa.Network) else pypsa.Network(Path(value))


def _zone_order() -> tuple[str, ...]:
    return tuple(str(zone) for zone in reporting_config()["zone_order"])


def _weights(network: pypsa.Network, kind: str) -> pd.Series:
    column = {
        "generator": "generators",
        "load": "objective",
        "link": "objective",
    }[kind]
    if column in network.snapshot_weightings:
        return pd.to_numeric(network.snapshot_weightings[column], errors="raise").reindex(network.snapshots)
    return pd.Series(1.0, index=network.snapshots)


def _dynamic_or_static(
    dynamic: pd.DataFrame,
    static: pd.DataFrame,
    column: str,
    names: pd.Index,
    snapshots: pd.Index,
) -> pd.DataFrame:
    result = dynamic.reindex(index=snapshots, columns=names).copy()
    for name in names:
        if name not in result or result[name].isna().all():
            result[name] = float(static.at[name, column])
        elif result[name].isna().any():
            result[name] = result[name].fillna(float(static.at[name, column]))
    return result.reindex(columns=names)


def _display_generator(carrier: str) -> str:
    mapping = reporting_config()["carrier_to_display_technology"]
    if carrier not in mapping:
        raise ValueError(f"No canonical reporting crosswalk for generator carrier: {carrier}")
    return str(mapping[carrier])


def _display_link(name: str) -> str:
    if name.startswith("BESS_DISCHARGE_"):
        return "BESS"
    if name.startswith("TURBINE_"):
        for hydro_class, display in reporting_config()["hydro_class_to_display_technology"].items():
            if str(hydro_class) in name:
                return str(display)
        raise ValueError(f"Cannot infer accepted hydro class from turbine link: {name}")
    raise ValueError(f"Link is not a canonical generation/discharge component: {name}")


def _technology_order(observed: set[str] | None = None) -> list[str]:
    order = [str(value) for value in reporting_config()["technology_order"]]
    if observed is None:
        return order
    missing = observed - set(order)
    if missing:
        raise ValueError(f"Technologies absent from canonical order: {sorted(missing)}")
    return [value for value in order if value in observed]


def _ensure_solved(network: pypsa.Network, require_full_year: bool) -> None:
    zones = _zone_order()
    if require_full_year and len(network.snapshots) != int(reporting_config()["require_full_chronology_hours"]):
        raise ValueError("Canonical production reporting requires all 8,760 accepted hourly snapshots")
    if not network.snapshots.is_unique:
        raise ValueError("Reporting refuses a duplicated snapshot index")
    if len(network.snapshots) > 1:
        difference = pd.Series(pd.DatetimeIndex(network.snapshots)).diff().dropna()
        if not difference.eq(pd.Timedelta(hours=1)).all():
            raise ValueError("Reporting requires a contiguous hourly chronology")
    missing_buses = set(zones) - set(network.buses.index.astype(str))
    if missing_buses:
        raise ValueError(f"Network lacks canonical Italian buses: {sorted(missing_buses)}")
    if network.generators_t.p.empty or not set(network.generators.index).intersection(network.generators_t.p.columns):
        raise ValueError("Solved generator dispatch is absent; reporting never solves the network")
    if network.buses_t.marginal_price.empty or not set(zones).issubset(network.buses_t.marginal_price.columns):
        raise ValueError("Solved zonal marginal prices are absent")
    if len(network.links) and (network.links_t.p0.empty or network.links_t.p1.empty):
        raise ValueError("Solved link flows are absent")


def installed_capacity_by_zone(network: pypsa.Network) -> pd.DataFrame:
    zones = _zone_order()
    records: list[dict[str, Any]] = []
    excluded = {"load_shedding", "spillage", "external_market", "water_energy"}
    generators = network.generators.loc[network.generators["bus"].astype(str).isin(zones)].copy()
    for name, row in generators.iterrows():
        carrier = str(row["carrier"])
        if carrier in excluded or carrier.startswith("p2x_flexible_") or str(name).startswith(("LOAD_SHEDDING_", "INFLOW_", "SPILL_")):
            continue
        records.append(
            {
                "zone": str(row["bus"]),
                "display_technology": _display_generator(carrier),
                "installed_capacity_GW": float(row["p_nom"]) / 1000.0,
                "component": "Generator",
                "asset_id": str(name),
            }
        )
    for name, row in network.links.iterrows():
        if not str(name).startswith(("BESS_DISCHARGE_", "TURBINE_")) or str(row["bus1"]) not in zones:
            continue
        records.append(
            {
                "zone": str(row["bus1"]),
                "display_technology": _display_link(str(name)),
                "installed_capacity_GW": float(row["p_nom"]) * float(row["efficiency"]) / 1000.0,
                "component": "Link delivered at bus1",
                "asset_id": str(name),
            }
        )
    detail = pd.DataFrame(records)
    if detail.empty:
        raise ValueError("No canonical Italian generation capacity was found")
    result = detail.groupby(["zone", "display_technology"], as_index=False, sort=False)["installed_capacity_GW"].sum()
    result["zone"] = pd.Categorical(result["zone"], zones, ordered=True)
    result["display_technology"] = pd.Categorical(
        result["display_technology"], _technology_order(set(result["display_technology"].astype(str))), ordered=True
    )
    return result.sort_values(["zone", "display_technology"]).reset_index(drop=True)


def _link_supply_role(name: str) -> str:
    if name.startswith("BESS_DISCHARGE_"):
        return "BESS_DISCHARGE"
    if name.startswith("TURBINE_"):
        if "PURE_PHS" in name or "MIXED_PHS" in name:
            return "PHS_DISCHARGE"
        return "PRIMARY_GENERATION"
    raise ValueError(f"Link is not a canonical electricity-supply component: {name}")


def annual_electricity_supply_by_zone(network: pypsa.Network) -> pd.DataFrame:
    """Electrical supply delivered to each Italian bus, split by accounting role."""

    zones = _zone_order()
    weights = _weights(network, "generator")
    records: list[dict[str, Any]] = []
    excluded = {"load_shedding", "spillage", "external_market", "water_energy"}
    for name, row in network.generators.loc[network.generators["bus"].astype(str).isin(zones)].iterrows():
        carrier = str(row["carrier"])
        if carrier in excluded or carrier.startswith("p2x_flexible_") or str(name).startswith(("LOAD_SHEDDING_", "INFLOW_", "SPILL_")):
            continue
        dispatch = pd.to_numeric(network.generators_t.p[str(name)], errors="raise").reindex(network.snapshots)
        records.append(
            {
                "zone": str(row["bus"]),
                "display_technology": _display_generator(carrier),
                "supply_role": "PRIMARY_GENERATION",
                "annual_electricity_supply_TWh": float((dispatch.clip(lower=0.0) * weights).sum()) / 1_000_000.0,
                "component": "Generator dispatch",
            }
        )
    link_weights = _weights(network, "link")
    for name, row in network.links.iterrows():
        if not str(name).startswith(("BESS_DISCHARGE_", "TURBINE_")) or str(row["bus1"]) not in zones:
            continue
        delivered = -pd.to_numeric(network.links_t.p1[str(name)], errors="raise").reindex(network.snapshots)
        records.append(
            {
                "zone": str(row["bus1"]),
                "display_technology": _display_link(str(name)),
                "supply_role": _link_supply_role(str(name)),
                "annual_electricity_supply_TWh": float((delivered.clip(lower=0.0) * link_weights).sum()) / 1_000_000.0,
                "component": "Link discharge delivered at bus1 (-p1)",
            }
        )
    detail = pd.DataFrame(records)
    result = detail.groupby(["zone", "display_technology", "supply_role"], as_index=False, sort=False)[
        "annual_electricity_supply_TWh"
    ].sum()
    result["zone"] = pd.Categorical(result["zone"], zones, ordered=True)
    result["display_technology"] = pd.Categorical(
        result["display_technology"], _technology_order(set(result["display_technology"].astype(str))), ordered=True
    )
    return result.sort_values(["zone", "display_technology", "supply_role"]).reset_index(drop=True)


def annual_generation_by_zone(network: pypsa.Network) -> pd.DataFrame:
    """Primary electricity generation only; storage and PHS discharge are excluded."""

    supply = annual_electricity_supply_by_zone(network)
    primary = supply.loc[supply["supply_role"].astype(str).eq("PRIMARY_GENERATION")].copy()
    primary = primary.rename(columns={"annual_electricity_supply_TWh": "annual_primary_generation_TWh"})
    return primary.drop(columns=["supply_role", "component"], errors="ignore").reset_index(drop=True)


def annual_storage_charging_by_zone(network: pypsa.Network) -> pd.DataFrame:
    """Charging withdrawals at Italian buses using the PyPSA Link p0 convention."""

    zones = _zone_order()
    weights = _weights(network, "link")
    records: list[dict[str, Any]] = []
    for name, row in network.links.iterrows():
        link_name = str(name)
        zone = str(row["bus0"])
        if zone not in zones or not link_name.startswith(("BESS_CHARGE_", "PUMP_")):
            continue
        withdrawal = pd.to_numeric(network.links_t.p0[link_name], errors="raise").reindex(network.snapshots)
        records.append(
            {
                "zone": zone,
                "charging_role": "BESS_CHARGING" if link_name.startswith("BESS_CHARGE_") else "PHS_CHARGING",
                "annual_charging_TWh": float((withdrawal.clip(lower=0.0) * weights).sum()) / 1_000_000.0,
                "component": "Link charging withdrawal at bus0 (p0)",
            }
        )
    if not records:
        return pd.DataFrame(columns=["zone", "charging_role", "annual_charging_TWh", "component"])
    result = pd.DataFrame(records).groupby(["zone", "charging_role", "component"], as_index=False, sort=False)[
        "annual_charging_TWh"
    ].sum()
    result["zone"] = pd.Categorical(result["zone"], zones, ordered=True)
    return result.sort_values(["zone", "charging_role"]).reset_index(drop=True)


def annual_load_shedding_by_zone(network: pypsa.Network) -> pd.DataFrame:
    """Positive load-shedding dispatch, reported separately from generation."""

    zones = _zone_order()
    weights = _weights(network, "generator")
    values = {zone: 0.0 for zone in zones}
    for name, row in network.generators.loc[network.generators["bus"].astype(str).isin(zones)].iterrows():
        if str(row["carrier"]) != "load_shedding" and not str(name).startswith("LOAD_SHEDDING_"):
            continue
        dispatch = pd.to_numeric(network.generators_t.p[str(name)], errors="raise").reindex(network.snapshots)
        values[str(row["bus"])] += float((dispatch.clip(lower=0.0) * weights).sum()) / 1_000_000.0
    return pd.DataFrame(
        {
            "zone": pd.Categorical(zones, zones, ordered=True),
            "annual_load_shedding_TWh": [values[zone] for zone in zones],
        }
    )


def _load_by_zone(network: pypsa.Network) -> dict[str, pd.Series]:
    zones = _zone_order()
    names = network.loads.index[
        network.loads["bus"].astype(str).isin(zones) & network.loads.index.to_series().astype(str).str.startswith("LOAD_")
    ]
    p_set = _dynamic_or_static(network.loads_t.p_set, network.loads, "p_set", names, network.snapshots)
    result: dict[str, pd.Series] = {}
    for zone in zones:
        zone_names = names[network.loads.loc[names, "bus"].astype(str).eq(zone)]
        result[zone] = p_set[zone_names].sum(axis=1) if len(zone_names) else pd.Series(0.0, index=network.snapshots)
    return result


def _p2x_by_zone(network: pypsa.Network) -> dict[str, pd.Series]:
    """Positive electrical withdrawal of controllable P2X, separate from fixed Load."""

    result = {zone: pd.Series(0.0, index=network.snapshots) for zone in _zone_order()}
    for name, row in network.generators.iterrows():
        if not str(row["carrier"]).startswith("p2x_flexible_"):
            continue
        zone = str(row["bus"])
        if zone not in result or float(row["sign"]) != -1.0:
            raise ValueError(f"Invalid P2X electrical withdrawal: {name}")
        dispatch = pd.to_numeric(network.generators_t.p[str(name)], errors="raise").reindex(network.snapshots)
        if dispatch.isna().any() or (dispatch < -1e-5).any() or (dispatch > float(row["p_nom"]) + 1e-5).any():
            raise ValueError(f"P2X hourly power is outside its physical cap: {name}")
        constraint_name = f"P2X_ANNUAL_{zone}"
        if constraint_name not in network.global_constraints.index:
            raise ValueError(f"P2X annual energy equality is absent: {name}")
        target = float(network.global_constraints.at[constraint_name, "constant"])
        observed = float((dispatch * _weights(network, "generator")).sum())
        if not math.isclose(observed, target, abs_tol=1e-2):
            raise ValueError(f"P2X annual electricity use differs from its target: {name}")
        result[zone] = result[zone] + dispatch.clip(lower=0.0)
    return result


def hourly_net_imports_by_zone(network: pypsa.Network) -> pd.DataFrame:
    zones = _zone_order()
    flows = pd.DataFrame(0.0, index=network.snapshots, columns=zones)
    if network.meta.get("network_v2b_applied", False):
        from ..final_network_signed import FLOW_METRIC, net_flow_frame
        canonical = net_flow_frame(network)
        for (_, a, b), group in canonical.groupby(["carrier", "bus_a", "bus_b"], sort=False):
            flow = group.set_index("snapshot")[FLOW_METRIC].reindex(network.snapshots)
            if a in zones:
                flows[a] -= flow
            if b in zones:
                flows[b] += flow
        flows.index.name = "snapshot"
        return flows
    included = network.links.loc[network.links["carrier"].astype(str).isin(["internal_transfer", "external_trade", "corsica_hub"])]
    for name, row in included.iterrows():
        bus0, bus1 = str(row["bus0"]), str(row["bus1"])
        p0 = pd.to_numeric(network.links_t.p0[str(name)], errors="raise").reindex(network.snapshots)
        p1 = pd.to_numeric(network.links_t.p1[str(name)], errors="raise").reindex(network.snapshots)
        if bus0 in zones:
            flows[bus0] -= p0
        if bus1 in zones:
            flows[bus1] -= p1
    flows.index.name = "snapshot"
    return flows


def annual_net_imports_by_zone(network: pypsa.Network) -> pd.DataFrame:
    hourly = hourly_net_imports_by_zone(network)
    weights = _weights(network, "link")
    values = hourly.mul(weights, axis=0).sum(axis=0) / 1_000_000.0
    return pd.DataFrame(
        {
            "zone": pd.Categorical(values.index.astype(str), _zone_order(), ordered=True),
            "annual_net_imports_TWh": values.to_numpy(dtype="float64"),
        }
    ).sort_values("zone").reset_index(drop=True)


def annual_demand_generation_balance_by_zone(
    network: pypsa.Network,
    supply: pd.DataFrame | None = None,
) -> pd.DataFrame:
    supply = annual_electricity_supply_by_zone(network) if supply is None else supply
    loads = _load_by_zone(network)
    p2x = _p2x_by_zone(network)
    weights = _weights(network, "load")
    rigid_demand = {
        zone: float((series.reindex(network.snapshots) * weights).sum()) / 1_000_000.0
        for zone, series in loads.items()
    }
    p2x_demand = {
        zone: float((series.reindex(network.snapshots) * weights).sum()) / 1_000_000.0
        for zone, series in p2x.items()
    }
    role_totals = supply.groupby(["zone", "supply_role"], observed=False)["annual_electricity_supply_TWh"].sum()
    charging = annual_storage_charging_by_zone(network)
    charging_totals = (
        charging.groupby(["zone", "charging_role"], observed=False)["annual_charging_TWh"].sum()
        if not charging.empty
        else pd.Series(dtype="float64")
    )
    imports = annual_net_imports_by_zone(network).set_index("zone")["annual_net_imports_TWh"]
    shedding = annual_load_shedding_by_zone(network).set_index("zone")["annual_load_shedding_TWh"]
    tolerance = float(reporting_config().get("balance_tolerance_TWh", 1e-6))

    def role(zone: str, name: str) -> float:
        return float(role_totals.get((zone, name), 0.0))

    def charge(zone: str, name: str) -> float:
        return float(charging_totals.get((zone, name), 0.0))

    records = []
    for zone in _zone_order():
        primary = role(zone, "PRIMARY_GENERATION")
        bess_discharge = role(zone, "BESS_DISCHARGE")
        phs_discharge = role(zone, "PHS_DISCHARGE")
        bess_charging = charge(zone, "BESS_CHARGING")
        phs_charging = charge(zone, "PHS_CHARGING")
        net_imports = float(imports.get(zone, 0.0))
        load_shedding = float(shedding.get(zone, 0.0))
        residual = (
            primary
            + bess_discharge
            + phs_discharge
            + net_imports
            + load_shedding
            - rigid_demand[zone]
            - p2x_demand[zone]
            - bess_charging
            - phs_charging
        )
        records.append(
            {
                "zone": zone,
                "primary_generation_TWh": primary,
                "bess_discharge_TWh": bess_discharge,
                "phs_discharge_TWh": phs_discharge,
                "storage_phs_discharge_TWh": bess_discharge + phs_discharge,
                "net_imports_TWh": net_imports,
                "load_shedding_TWh": load_shedding,
                "rigid_end_use_demand_TWh": rigid_demand[zone],
                "p2x_electrical_consumption_TWh": p2x_demand[zone],
                "gross_end_use_demand_TWh": rigid_demand[zone] + p2x_demand[zone],
                "bess_charging_TWh": bess_charging,
                "phs_charging_TWh": phs_charging,
                "storage_phs_charging_TWh": bess_charging + phs_charging,
                "balance_residual_TWh": residual,
                "balance_tolerance_TWh": tolerance,
                "balance_check_status": "PASS" if abs(residual) <= tolerance else "FAIL",
                "link_efficiency_convention": "DISCHARGE_DELIVERED_AT_BUS1_MINUS_P1|CHARGING_WITHDRAWAL_AT_BUS0_P0",
            }
        )
    return pd.DataFrame(records)


def zonal_price_statistics(network: pypsa.Network) -> pd.DataFrame:
    loads = _load_by_zone(network)
    p2x = _p2x_by_zone(network)
    snapshot_weights = _weights(network, "load")
    records = []
    for zone in _zone_order():
        price = pd.to_numeric(network.buses_t.marginal_price[zone], errors="raise").reindex(network.snapshots)
        if price.isna().any() or not np.isfinite(price.to_numpy()).all():
            raise ValueError(f"Non-finite marginal price in {zone}")
        load = loads[zone].reindex(network.snapshots) + p2x[zone].reindex(network.snapshots)
        denominator = float((load * snapshot_weights).sum())
        if denominator <= 0:
            raise ValueError(f"Cannot compute load-weighted price for zero-load zone {zone}")
        records.append(
            {
                "zone": zone,
                "arithmetic_mean_EUR_per_MWh": float(price.mean()),
                "load_weighted_mean_EUR_per_MWh": float((price * load * snapshot_weights).sum() / denominator),
                "median_EUR_per_MWh": float(price.median()),
                "p5_EUR_per_MWh": float(price.quantile(0.05)),
                "p95_EUR_per_MWh": float(price.quantile(0.95)),
                "p99_EUR_per_MWh": float(price.quantile(0.99)),
                "minimum_EUR_per_MWh": float(price.min()),
                "maximum_EUR_per_MWh": float(price.max()),
                "negative_price_hours": int(price.lt(0.0).sum()),
                "zero_price_hours": int(np.isclose(price.to_numpy(dtype="float64"), 0.0, atol=1e-12).sum()),
                "hours_above_500_EUR_per_MWh": int(price.gt(500.0).sum()),
                "price_clipping_applied": False,
            }
        )
    return pd.DataFrame(records)


def dispatch_8760_by_zone(network: pypsa.Network) -> pd.DataFrame:
    zones = _zone_order()
    technologies: dict[str, pd.DataFrame] = {
        zone: pd.DataFrame(index=network.snapshots, dtype="float64") for zone in zones
    }
    excluded = {"load_shedding", "spillage", "external_market", "water_energy"}
    for name, row in network.generators.loc[network.generators["bus"].astype(str).isin(zones)].iterrows():
        carrier = str(row["carrier"])
        if carrier in excluded or carrier.startswith("p2x_flexible_") or str(name).startswith(("LOAD_SHEDDING_", "INFLOW_", "SPILL_")):
            continue
        zone, technology = str(row["bus"]), _display_generator(carrier)
        values = pd.to_numeric(network.generators_t.p[str(name)], errors="raise").reindex(network.snapshots).clip(lower=0.0)
        technologies[zone][technology] = technologies[zone].get(technology, pd.Series(0.0, index=network.snapshots)) + values
    bess_charging = {zone: pd.Series(0.0, index=network.snapshots) for zone in zones}
    phs_charging = {zone: pd.Series(0.0, index=network.snapshots) for zone in zones}
    for name, row in network.links.iterrows():
        link_name = str(name)
        if link_name.startswith(("BESS_DISCHARGE_", "TURBINE_")) and str(row["bus1"]) in zones:
            zone, technology = str(row["bus1"]), _display_link(link_name)
            values = -pd.to_numeric(network.links_t.p1[link_name], errors="raise").reindex(network.snapshots)
            technologies[zone][technology] = technologies[zone].get(technology, pd.Series(0.0, index=network.snapshots)) + values.clip(lower=0.0)
        elif link_name.startswith(("BESS_CHARGE_", "PUMP_")) and str(row["bus0"]) in zones:
            zone = str(row["bus0"])
            values = pd.to_numeric(network.links_t.p0[link_name], errors="raise").reindex(network.snapshots).clip(lower=0.0)
            target = bess_charging if link_name.startswith("BESS_CHARGE_") else phs_charging
            target[zone] = target[zone] + values
    load_shedding = {zone: pd.Series(0.0, index=network.snapshots) for zone in zones}
    for name, row in network.generators.loc[network.generators["bus"].astype(str).isin(zones)].iterrows():
        if str(row["carrier"]) != "load_shedding" and not str(name).startswith("LOAD_SHEDDING_"):
            continue
        zone = str(row["bus"])
        values = pd.to_numeric(network.generators_t.p[str(name)], errors="raise").reindex(network.snapshots)
        load_shedding[zone] = load_shedding[zone] + values.clip(lower=0.0)
    loads = _load_by_zone(network)
    p2x = _p2x_by_zone(network)
    imports = hourly_net_imports_by_zone(network)
    observed_technologies = {column for table in technologies.values() for column in table.columns}
    ordered = _technology_order(observed_technologies)
    records = []
    for zone in zones:
        frame = technologies[zone].reindex(index=network.snapshots, columns=ordered, fill_value=0.0).copy()
        frame["rigid_end_use_demand_MW"] = loads[zone]
        frame["p2x_electrical_consumption_MW"] = p2x[zone]
        frame["gross_end_use_demand_MW"] = loads[zone] + p2x[zone]
        frame["bess_charging_MW"] = bess_charging[zone]
        frame["phs_charging_MW"] = phs_charging[zone]
        frame["storage_phs_charging_MW"] = bess_charging[zone] + phs_charging[zone]
        frame["net_imports_MW"] = imports[zone]
        frame["load_shedding_MW"] = load_shedding[zone]
        frame.insert(0, "zone", zone)
        frame.insert(0, "snapshot", network.snapshots)
        records.append(frame.reset_index(drop=True))
    zonal = pd.concat(records, ignore_index=True)
    numeric_columns = [column for column in zonal.columns if column not in {"snapshot", "zone"}]
    national = zonal.groupby("snapshot", as_index=False, sort=False)[numeric_columns].sum()
    national.insert(1, "zone", "NATIONAL")
    return pd.concat([zonal, national], ignore_index=True)


def topology_interfaces(network: pypsa.Network) -> pd.DataFrame:
    if network.meta.get("network_v2b_applied", False):
        from ..final_network_signed import directional_capacity_table
        return directional_capacity_table(network)
    carriers = {"internal_transfer", "external_trade", "corsica_hub"}
    subset = network.links.loc[network.links["carrier"].astype(str).isin(carriers)].copy()
    return pd.DataFrame(
        {
            "interface_id": subset.index.astype(str),
            "from_bus": subset["bus0"].astype(str).to_numpy(),
            "to_bus": subset["bus1"].astype(str).to_numpy(),
            "carrier": subset["carrier"].astype(str).to_numpy(),
            "capacity_MW": pd.to_numeric(subset["p_nom"], errors="raise").to_numpy(dtype="float64"),
            "efficiency": pd.to_numeric(subset["efficiency"], errors="raise").to_numpy(dtype="float64"),
            "directional": True,
            "layout_semantics": "SCHEMATIC_NOT_GEOGRAPHIC",
        }
    ).sort_values(["carrier", "interface_id"]).reset_index(drop=True)


def _write_table(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".parquet":
        frame.to_parquet(path, index=False, engine="pyarrow", compression="zstd")
    else:
        frame.to_csv(path, index=False, lineterminator="\n", float_format="%.12g")


def _export_pypsa_statistics(network: pypsa.Network, statistics_dir: Path) -> dict[str, str]:
    outputs: dict[str, str] = {}
    functions = {
        "pypsa_installed_capacity.csv": network.statistics.installed_capacity,
        "pypsa_energy_balance.csv": network.statistics.energy_balance,
    }
    for filename, function in functions.items():
        value = function()
        if isinstance(value, pd.Series):
            frame = value.rename("value").reset_index()
        elif isinstance(value, pd.DataFrame):
            frame = value.reset_index()
        else:
            frame = pd.DataFrame(value)
        path = statistics_dir / filename
        _write_table(path, frame)
        outputs[filename] = sha256_file(path)
    return outputs


def build_canonical_tables(network: pypsa.Network) -> dict[str, pd.DataFrame]:
    capacity = installed_capacity_by_zone(network)
    supply = annual_electricity_supply_by_zone(network)
    generation = annual_generation_by_zone(network)
    charging = annual_storage_charging_by_zone(network)
    balance = annual_demand_generation_balance_by_zone(network, supply)
    tables = {
        "installed_capacity_by_zone.csv": capacity,
        "installed_capacity_national.csv": capacity.groupby("display_technology", observed=False, as_index=False)["installed_capacity_GW"].sum().query("installed_capacity_GW > 0"),
        "annual_primary_generation_by_zone.csv": generation,
        "annual_primary_generation_national.csv": generation.groupby("display_technology", observed=False, as_index=False)["annual_primary_generation_TWh"].sum().query("annual_primary_generation_TWh != 0"),
        "annual_electricity_supply_by_zone.csv": supply,
        "annual_electricity_supply_national.csv": supply.groupby(["display_technology", "supply_role"], observed=False, as_index=False)["annual_electricity_supply_TWh"].sum().query("annual_electricity_supply_TWh != 0"),
        "annual_storage_phs_charging_by_zone.csv": charging,
        "annual_electrical_balance_by_zone.csv": balance,
        "zonal_price_statistics.csv": zonal_price_statistics(network),
        "annual_net_imports_by_zone.csv": annual_net_imports_by_zone(network),
        "network_topology_interfaces.csv": topology_interfaces(network),
        "dispatch_8760_by_zone.parquet": dispatch_8760_by_zone(network),
    }
    if network.meta.get("network_v2b_applied", False):
        from ..final_network_signed import net_flow_frame
        tables["interface_net_flows_hourly.parquet"] = net_flow_frame(network)
    return tables


def generate_canonical_results(
    network_or_path: pypsa.Network | str | Path,
    horizon: int,
    scenario: str,
    output_directory: str | Path,
    *,
    require_full_year: bool = True,
    render_figures: bool = True,
) -> dict[str, Any]:
    """Generate deterministic tables and figures from an already-solved network.

    This function never calls ``optimize`` or a solver.  It fails if solved
    dispatch, link flows, or zonal marginal prices are absent.
    """

    network = _network(network_or_path)
    scenario = str(scenario).title()
    if scenario not in reporting_config()["scenario_order"]:
        raise ValueError(f"Unknown canonical scenario: {scenario}")
    _ensure_solved(network, require_full_year=require_full_year)
    output = Path(output_directory)
    statistics_dir = output / "statistics"
    figures_dir = output / "figures"
    statistics_dir.mkdir(parents=True, exist_ok=True)
    if render_figures:
        figures_dir.mkdir(parents=True, exist_ok=True)
    tables = build_canonical_tables(network)
    balance = tables["annual_electrical_balance_by_zone.csv"]
    failed_balance = balance.loc[balance["balance_check_status"].astype(str).ne("PASS")]
    if not failed_balance.empty:
        detail = failed_balance[["zone", "balance_residual_TWh"]].to_dict(orient="records")
        raise ValueError(f"Canonical electrical-balance check failed: {detail}")
    table_hashes: dict[str, str] = {}
    for filename, table in tables.items():
        path = statistics_dir / filename
        _write_table(path, table)
        table_hashes[filename] = sha256_file(path)
    pypsa_hashes = _export_pypsa_statistics(network, statistics_dir)
    figure_files: list[str] = []
    if render_figures:
        from .plots import render_canonical_figures

        figure_files = render_canonical_figures(
            network,
            tables,
            horizon=int(horizon),
            scenario=scenario,
            output_directory=figures_dir,
        )
    manifest_rows = [
        {"artifact": f"statistics/{name}", "sha256": digest, "role": "CANONICAL_CHART_SOURCE_TABLE"}
        for name, digest in {**table_hashes, **pypsa_hashes}.items()
    ]
    for filename in figure_files:
        manifest_rows.append(
            {
                "artifact": f"figures/{filename}",
                "sha256": sha256_file(figures_dir / filename),
                "role": "CANONICAL_FIGURE",
            }
        )
    manifest = pd.DataFrame(manifest_rows)
    _write_table(output / "MEM_CANONICAL_REPORTING_MANIFEST.csv", manifest)
    receipt = {
        "schema_version": "MEM_CANONICAL_SOLVED_NETWORK_REPORTING_v1.0",
        "status": "PASS",
        "horizon": int(horizon),
        "scenario": scenario,
        "snapshots": len(network.snapshots),
        "network_solved_before_reporting": True,
        "reporting_solver_invocations": 0,
        "canonical_price_metric": reporting_config()["canonical_price_metric"],
        "electrical_balance_status": "PASS",
        "electrical_balance_max_abs_residual_TWh": float(balance["balance_residual_TWh"].abs().max()),
        "table_count": len(table_hashes) + len(pypsa_hashes),
        "figure_file_count": len(figure_files),
        "manifest_sha256": sha256_file(output / "MEM_CANONICAL_REPORTING_MANIFEST.csv"),
    }
    dump_json(output / "MEM_CANONICAL_REPORTING_RECEIPT.json", receipt)
    return receipt


def generate_cross_scenario_comparison(
    run_directories: Mapping[str, str | Path],
    horizon: int,
    output_directory: str | Path,
    *,
    render_figures: bool = True,
    require_accepted: bool = False,
) -> dict[str, Any]:
    """Combine three completed canonical reporting packages without solving."""

    expected = tuple(str(value) for value in reporting_config()["scenario_order"])
    if set(run_directories) != set(expected):
        raise ValueError(f"Cross-scenario comparison requires exactly {expected}")
    acceptance_hashes: dict[str, str] = {}
    if require_accepted:
        for scenario in expected:
            run = Path(run_directories[scenario])
            accepted = run / f"MEM_STAGE_B_{horizon}_{scenario.upper()}_ACCEPTANCE_v1.0.json"
            receipt_path = run / "MEM_CANONICAL_REPORTING_RECEIPT.json"
            manifest_path = run / "MEM_CANONICAL_REPORTING_MANIFEST.csv"
            if not all(path.is_file() for path in (accepted, receipt_path, manifest_path)):
                raise FileNotFoundError(f"Accepted canonical package incomplete: {scenario}")
            acceptance = json.loads(accepted.read_text(encoding="utf-8"))
            reporting = json.loads(receipt_path.read_text(encoding="utf-8"))
            network_path = run / f"MEM_{horizon}_{scenario.upper()}_8760h_SOLVED.nc"
            if not (
                acceptance.get("status") == "ACCEPTED_IMMUTABLE"
                and acceptance.get("scenario") == scenario
                and acceptance.get("horizon") == horizon
                and acceptance.get("solved_network_sha256", "").lower() == sha256_file(network_path)
                and acceptance.get("reporting_manifest_sha256", "").lower() == sha256_file(manifest_path)
                and acceptance.get("reporting_receipt_sha256", "").lower() == sha256_file(receipt_path)
                and reporting.get("status") == "PASS"
                and reporting.get("manifest_sha256", "").lower() == sha256_file(manifest_path)
            ):
                raise ValueError(f"Canonical accepted source identity failed: {scenario}")
            manifest = pd.read_csv(manifest_path, dtype=str)
            if manifest["artifact"].duplicated().any():
                raise ValueError(f"Duplicate reporting manifest member: {scenario}")
            for member in manifest.itertuples(index=False):
                path = (run / str(member.artifact)).resolve()
                if not path.is_relative_to(run.resolve()) or not path.is_file() or sha256_file(path) != str(member.sha256).lower():
                    raise ValueError(f"Canonical reporting member identity failed: {scenario}/{member.artifact}")
            acceptance_hashes[scenario] = sha256_file(accepted)
    output = Path(output_directory)
    statistics_dir = output / "statistics"
    figures_dir = output / "figures"
    statistics_dir.mkdir(parents=True, exist_ok=True)
    if render_figures:
        figures_dir.mkdir(parents=True, exist_ok=True)
    specifications = {
        "installed_capacity_by_technology_and_scenario.csv": ("installed_capacity_national.csv", "installed_capacity_GW"),
        "annual_primary_generation_by_technology_and_scenario.csv": ("annual_primary_generation_national.csv", "annual_primary_generation_TWh"),
        "annual_electricity_supply_by_technology_and_scenario.csv": ("annual_electricity_supply_national.csv", "annual_electricity_supply_TWh"),
        "national_electrical_balance_by_scenario.csv": ("annual_electrical_balance_by_zone.csv", None),
        "average_zonal_prices_by_scenario.csv": ("zonal_price_statistics.csv", None),
        "annual_net_imports_by_scenario.csv": ("annual_net_imports_by_zone.csv", None),
        "annual_storage_phs_operation_by_scenario.csv": ("annual_electrical_balance_by_zone.csv", None),
    }
    combined: dict[str, pd.DataFrame] = {}
    for output_name, (source_name, metric) in specifications.items():
        frames = []
        for scenario in expected:
            source = Path(run_directories[scenario]) / "statistics" / source_name
            if not source.exists():
                raise FileNotFoundError(source)
            frame = pd.read_csv(source)
            if output_name in {"national_electrical_balance_by_scenario.csv", "annual_storage_phs_operation_by_scenario.csv"}:
                numeric = [
                    column
                    for column in frame.select_dtypes(include=[np.number]).columns
                    if column != "balance_tolerance_TWh"
                ]
                frame = pd.DataFrame({column: [float(frame[column].sum())] for column in numeric})
                if output_name == "annual_storage_phs_operation_by_scenario.csv":
                    frame = frame[["bess_discharge_TWh", "phs_discharge_TWh", "bess_charging_TWh", "phs_charging_TWh"]]
            frame.insert(0, "scenario", scenario)
            if metric is not None and metric not in frame:
                raise ValueError(f"Missing comparison metric {metric} in {source}")
            frames.append(frame)
        combined[output_name] = pd.concat(frames, ignore_index=True)
        _write_table(statistics_dir / output_name, combined[output_name])
    figure_files: list[str] = []
    if render_figures:
        from .plots import render_comparison_figures

        figure_files = render_comparison_figures(combined, int(horizon), figures_dir)
    from .topology import collapse_reciprocal_interfaces, edge_key, validate_accepted_2040_interfaces

    normalized_edge_sets = []
    for scenario in expected:
        interfaces = pd.read_csv(Path(run_directories[scenario]) / "statistics" / "network_topology_interfaces.csv")
        if require_accepted and int(horizon) == 2040:
            validate_accepted_2040_interfaces(interfaces, scenario)
        edges = collapse_reciprocal_interfaces(interfaces, require_reciprocal=require_accepted)
        normalized_edge_sets.append({edge_key(row.carrier, row.bus_a, row.bus_b) for row in edges.itertuples(index=False)})
    if not normalized_edge_sets[0] == normalized_edge_sets[1] == normalized_edge_sets[2]:
        raise ValueError("Cross-scenario topology is not invariant")
    topology = pd.DataFrame(sorted(normalized_edge_sets[0]), columns=["carrier", "bus_a", "bus_b"])
    _write_table(statistics_dir / "topology_schematic_edges.csv", topology)
    combined["topology_schematic_edges.csv"] = topology
    if render_figures and require_accepted:
        for suffix in ("png", "svg"):
            files = [Path(run_directories[s]) / "figures" / f"network_topology.{suffix}" for s in expected]
            if len({sha256_file(file) for file in files}) != 1:
                raise ValueError("Accepted scenario topology figures are not one authoritative schematic")
            shutil.copyfile(files[0], figures_dir / f"network_topology.{suffix}")
            figure_files.append(f"network_topology.{suffix}")
    manifest_rows = [
        {"artifact": f"statistics/{name}", "sha256": sha256_file(statistics_dir / name), "role": "CANONICAL_COMPARISON_SOURCE_TABLE"}
        for name in combined
    ]
    manifest_rows.extend(
        {"artifact": f"figures/{name}", "sha256": sha256_file(figures_dir / name), "role": "CANONICAL_COMPARISON_FIGURE"}
        for name in figure_files
    )
    _write_table(output / "MEM_CANONICAL_CROSS_SCENARIO_REPORTING_MANIFEST.csv", pd.DataFrame(manifest_rows))
    receipt = {
        "schema_version": "MEM_CANONICAL_CROSS_SCENARIO_REPORTING_v1.0",
        "status": "PASS",
        "horizon": int(horizon),
        "scenarios": list(expected),
        "reporting_solver_invocations": 0,
        "table_count": len(combined),
        "figure_file_count": len(figure_files),
        "source_acceptance_sha256": acceptance_hashes,
        "manifest_sha256": sha256_file(output / "MEM_CANONICAL_CROSS_SCENARIO_REPORTING_MANIFEST.csv"),
    }
    dump_json(output / "MEM_CANONICAL_CROSS_SCENARIO_REPORTING_RECEIPT.json", receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate canonical MEM reporting from an already-solved PyPSA network")
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--horizon", type=int, required=True)
    parser.add_argument("--scenario", choices=["Slow", "Base", "High"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = generate_canonical_results(args.network, args.horizon, args.scenario, args.output)
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()

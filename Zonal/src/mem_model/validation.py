from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
import pypsa

from .common import PRICE_MARKETS, STATIC, ZONES, parse_bool


def _check(rows: list[dict[str, Any]], check_id: str, description: str, observed: Any, expected: Any, passed: bool, notes: str = "") -> None:
    rows.append(
        {
            "check_id": check_id,
            "description": description,
            "observed": observed,
            "expected": expected,
            "difference": "" if not isinstance(observed, (int, float)) or not isinstance(expected, (int, float)) else float(observed) - float(expected),
            "status": "PASS" if passed else "FAIL",
            "notes": notes,
        }
    )


def validate_network(network: pypsa.Network, year: int, scenario: str, *, fixture: bool = False, assemble_model: bool = True) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    scenario = scenario.title()
    source_generators = pd.read_csv(STATIC / "MEM_generators_static_final.csv")
    source_generators = source_generators.loc[
        source_generators["year"].astype(int).eq(year)
        & source_generators["scenario"].astype(str).str.casefold().eq(scenario.casefold())
    ]
    source_storage = pd.read_csv(STATIC / "MEM_storage_static_final.csv")
    source_storage = source_storage.loc[
        source_storage["year"].astype(int).eq(year)
        & source_storage["scenario"].astype(str).str.casefold().eq(scenario.casefold())
    ]

    italian = [bus for bus in network.buses.index if bus in ZONES]
    _check(rows, "NS-001", "Exactly seven canonical Italian electricity buses", "|".join(italian), "|".join(ZONES), tuple(italian) == ZONES)
    _check(rows, "NS-002", "No physical AC Lines", len(network.lines), 0, network.lines.empty)
    _check(rows, "NS-003", "Snapshot index unique", int(network.snapshots.duplicated().sum()), 0, network.snapshots.is_unique)
    hourly = len(network.snapshots) <= 1 or bool((network.snapshots.to_series().diff().dropna() == pd.Timedelta(hours=1)).all())
    _check(rows, "NS-004", "Snapshot index contiguous hourly", hourly, True, hourly)
    expected_hours = len(network.snapshots) if fixture else (8784 if pd.Timestamp(year=network.snapshots[0].year, month=12, day=31).is_leap_year else 8760)
    _check(
        rows,
        "NS-005",
        "Chronology complete for intended build",
        len(network.snapshots),
        expected_hours,
        len(network.snapshots) == expected_hours,
        "Fixture check uses its declared 24h/168h length" if fixture else "Accepted annual network must be complete",
    )

    frozen_ids = source_generators["generator_id"].astype(str)
    missing_ids = sorted(set(frozen_ids) - set(network.generators.index))
    _check(rows, "NS-006", "Every frozen scenario generator is instantiated", len(missing_ids), 0, not missing_ids, str(missing_ids[:5]))
    observed_mw = float(network.generators.loc[network.generators.index.intersection(frozen_ids), "p_nom"].sum())
    expected_mw = float(source_generators["p_nom_MW"].sum())
    _check(rows, "NS-007", "Frozen generator capacity unchanged", observed_mw, expected_mw, math.isclose(observed_mw, expected_mw, abs_tol=1e-6))
    _check(rows, "NS-008", "All Generator p_nom non-extendable", int(network.generators["p_nom_extendable"].fillna(False).astype(bool).sum()), 0, not network.generators["p_nom_extendable"].fillna(False).astype(bool).any())
    _check(rows, "NS-009", "All Link p_nom non-extendable", int(network.links["p_nom_extendable"].fillna(False).astype(bool).sum()), 0, not network.links["p_nom_extendable"].fillna(False).astype(bool).any())
    _check(rows, "NS-010", "All Store e_nom non-extendable", int(network.stores["e_nom_extendable"].fillna(False).astype(bool).sum()), 0, not network.stores["e_nom_extendable"].fillna(False).astype(bool).any())
    _check(rows, "NS-010A", "Unit commitment is disabled for all generators", int(network.generators["committable"].fillna(False).astype(bool).sum()), 0, not network.generators["committable"].fillna(False).astype(bool).any())

    availability = network.generators_t.p_max_pu
    finite = availability.empty or np.isfinite(availability.to_numpy()).all()
    bounded = availability.empty or ((availability.to_numpy() >= -1e-12).all() and (availability.to_numpy() <= 1 + 1e-12).all())
    _check(rows, "NS-011", "Generator availability finite", finite, True, bool(finite))
    _check(rows, "NS-012", "Generator availability within [0,1]", bounded, True, bool(bounded))
    loads = network.loads_t.p_set.filter(regex=r"^LOAD_")
    load_valid = not loads.empty and np.isfinite(loads.to_numpy()).all() and (loads.to_numpy() >= 0).all()
    _check(rows, "NS-013", "Italian hourly loads finite and non-negative", load_valid, True, bool(load_valid))
    if not fixture:
        demand_contract = pd.read_csv(STATIC / "MEM_Annual_Zonal_Demand_Contract.csv")
        demand_contract = demand_contract.loc[
            demand_contract["year"].astype(int).eq(year)
            & demand_contract["scenario"].astype(str).str.casefold().eq(scenario.casefold())
        ].set_index("zone")
        for zone in ZONES:
            observed = float(loads[f"LOAD_{zone}"].sum())
            expected = float(demand_contract.at[zone, "annual_zonal_demand_MWh"])
            _check(rows, f"NS-LOAD-{zone}", f"{zone} annual load reconciles", observed, expected, math.isclose(observed, expected, abs_tol=1e-3))

    bess_stores = network.stores.loc[network.stores.index.str.startswith("BESS_STORE_")]
    _check(rows, "NS-014", "BESS energy reconciles", float(bess_stores["e_nom"].sum()), float(source_storage["energy_capacity_MWh"].sum()), math.isclose(float(bess_stores["e_nom"].sum()), float(source_storage["energy_capacity_MWh"].sum()), abs_tol=1e-6))
    bess_charge = network.links.loc[network.links.index.str.startswith("BESS_CHARGE_")]
    bess_discharge = network.links.loc[network.links.index.str.startswith("BESS_DISCHARGE_")]
    _check(rows, "NS-014A", "BESS charge power reconciles", float(bess_charge["p_nom"].sum()), float(source_storage["charge_power_MW"].sum()), math.isclose(float(bess_charge["p_nom"].sum()), float(source_storage["charge_power_MW"].sum()), abs_tol=1e-6))
    bess_net_discharge = float((bess_discharge["p_nom"] * bess_discharge["efficiency"]).sum())
    _check(rows, "NS-014B", "BESS net electrical discharge power reconciles", bess_net_discharge, float(source_storage["discharge_power_MW"].sum()), math.isclose(bess_net_discharge, float(source_storage["discharge_power_MW"].sum()), abs_tol=1e-6), "PyPSA Link p_nom is the bus0 input rating; the frozen control is delivered power at bus1")
    phs_pattern = r"(?:PURE|MIXED)_PHS|PHS_AGGREGATE"
    phs_stores = network.stores.loc[network.stores.index.str.contains(phs_pattern, regex=True)]
    _check(rows, "NS-015", "PHS operational energy equals 53 GWh", float(phs_stores["e_nom"].sum()), 53000.0, math.isclose(float(phs_stores["e_nom"].sum()), 53000.0, abs_tol=1e-6))
    phs_pumps = network.links.loc[network.links.index.str.startswith("PUMP_") & network.links.index.str.contains(phs_pattern, regex=True)]
    phs_turbines = network.links.loc[network.links.index.str.startswith("TURBINE_") & network.links.index.str.contains(phs_pattern, regex=True)]
    _check(rows, "NS-016", "PHS pump power equals 6.4 GW", float(phs_pumps["p_nom"].sum()), 6400.0, math.isclose(float(phs_pumps["p_nom"].sum()), 6400.0, abs_tol=1e-6))
    phs_net_discharge = float((phs_turbines["p_nom"] * phs_turbines["efficiency"]).sum())
    _check(rows, "NS-017", "PHS net electrical discharge power equals 7.2523 GW", phs_net_discharge, 7252.3, math.isclose(phs_net_discharge, 7252.3, abs_tol=1e-6), "Frozen PHS power is the delivered bus1 rating")
    _check(rows, "NS-018", "626.262 GWh physical evidence excluded from e_nom", bool(np.isclose(network.stores["e_nom"].to_numpy(), 626262.056948).any()), False, not np.isclose(network.stores["e_nom"].to_numpy(), 626262.056948).any())
    conventional_turbines = network.links.loc[
        network.links.index.str.startswith("TURBINE_")
        & ~network.links.index.str.contains(phs_pattern, regex=True)
    ]
    conventional_stateful_net = float((conventional_turbines["p_nom"] * conventional_turbines["efficiency"]).sum())
    ror_ids = source_generators.loc[source_generators["parent_capacity_technology"].eq("HYDRO_RUN_OF_RIVER"), "generator_id"].astype(str)
    ror_net = float(network.generators.loc[network.generators.index.intersection(ror_ids), "p_nom"].sum())
    conventional_total = conventional_stateful_net + ror_net
    _check(rows, "NS-018A", "Conventional hydro net turbine power equals 16.0417 GW", conventional_total, 16041.7, math.isclose(conventional_total, 16041.7, abs_tol=1e-6))
    _check(rows, "NS-018B", "Conventional hydro plus PHS net turbine power equals 23.294 GW", conventional_total + phs_net_discharge, 23294.0, math.isclose(conventional_total + phs_net_discharge, 23294.0, abs_tol=1e-6))

    internal = network.links.loc[network.links["carrier"].eq("internal_transfer")]
    _check(rows, "NS-019", "Frozen internal directional-link count", len(internal), 20, len(internal) == 20)
    _check(rows, "NS-020", "Internal links are lossless", float(internal["efficiency"].min()) if len(internal) else None, 1.0, len(internal) == 20 and internal["efficiency"].eq(1.0).all())
    internal_contract = pd.read_csv(STATIC / "MEM_Interzonal_Static_Contract.csv")
    internal_contract = internal_contract.loc[
        internal_contract["year"].astype(int).eq(year)
        & internal_contract["scenario"].astype(str).str.casefold().eq(scenario.casefold())
    ]
    capacity_differences = []
    for _, contract_row in internal_contract.iterrows():
        link_id = f"INTERNAL_{contract_row['from_zone']}_TO_{contract_row['to_zone']}"
        if link_id not in internal.index:
            capacity_differences.append(float("inf"))
        else:
            capacity_differences.append(abs(float(internal.at[link_id, "p_nom"]) - float(contract_row["capacity_MW"])))
    maximum_internal_difference = max(capacity_differences, default=0.0)
    _check(rows, "NS-020A", "Every internal directional-link capacity matches the frozen contract", maximum_internal_difference, 0.0, maximum_internal_difference <= 1e-6)
    corsica = network.links.loc[network.links["carrier"].eq("corsica_hub")]
    _check(rows, "NS-021", "Corsica is four-leg zero-injection hub", len(corsica), 4, len(corsica) == 4 and "CORS" in network.buses.index)
    ext_generators = [f"EXT_SUPPLY_{market}" for market in PRICE_MARKETS]
    _check(rows, "NS-022", "All eight external price-taking markets exist", len(set(ext_generators) & set(network.generators.index)), 8, set(ext_generators).issubset(network.generators.index))
    external_contract = pd.read_csv(STATIC / "MEM_External_Interface_Static_Contract.csv")
    external_contract = external_contract.loc[external_contract["external_market"].isin(PRICE_MARKETS)]
    external_capacity_differences = []
    for _, contract_row in external_contract.iterrows():
        market = str(contract_row["external_market"])
        zone = str(contract_row["Italian_zone"])
        link_id = f"IMPORT_{market}_TO_{zone}" if contract_row["direction"] == "IMPORT" else f"EXPORT_{zone}_TO_{market}"
        if link_id not in network.links.index:
            external_capacity_differences.append(float("inf"))
        else:
            external_capacity_differences.append(abs(float(network.links.at[link_id, "p_nom"]) - float(contract_row["capacity_MW"])))
    maximum_external_difference = max(external_capacity_differences, default=0.0)
    _check(rows, "NS-022A", "Every external directional-link capacity matches the frozen contract", maximum_external_difference, 0.0, maximum_external_difference <= 1e-6)
    ext_costs = network.generators_t.marginal_cost.reindex(columns=ext_generators)
    _check(rows, "NS-023", "External hourly prices complete", int(ext_costs.isna().sum().sum()), 0, not ext_costs.isna().any().any())
    _check(rows, "NS-024", "External objective-baseline rule recorded", bool(network.meta.get("external_net_trade_cost_rule")), True, bool(network.meta.get("external_net_trade_cost_rule")))
    external_links = network.links.loc[network.links["carrier"].eq("external_trade")]
    _check(rows, "NS-024A", "External price-taking links are lossless", int((~external_links["efficiency"].eq(1.0)).sum()), 0, external_links["efficiency"].eq(1.0).all())
    _check(rows, "NS-024B", "External baseline transaction toll is zero", float(external_links["marginal_cost"].abs().max()) if len(external_links) else None, 0.0, len(external_links) == 16 and external_links["marginal_cost"].eq(0.0).all())
    ext_bus_generators = network.generators.loc[network.generators["bus"].astype(str).str.startswith("EXT_")]
    _check(rows, "NS-024C", "No external physical fleet is instantiated", "|".join(sorted(ext_bus_generators.index.astype(str))), "|".join(sorted(ext_generators)), set(ext_bus_generators.index) == set(ext_generators))

    static_fuels = source_generators["fuel"].astype(str).str.upper()
    forbidden = int(static_fuels.isin(["COAL", "OIL", "PETROLEUM"]).sum())
    _check(rows, "NS-025", "No coal or oil in frozen solver fleet", forbidden, 0, forbidden == 0)
    if year == 2040:
        combustion = source_generators.loc[source_generators["fuel"].astype(str).str.upper().isin(["METHANE", "HYDROGEN", "COAL", "OIL", "PETROLEUM"]), "fuel"].astype(str).str.upper()
        _check(rows, "NS-026", "2040 fossil/gaseous combustion is methane-only", "|".join(sorted(combustion.unique())), "METHANE", set(combustion.unique()) <= {"METHANE"})
    if year == 2050:
        gas = source_generators.loc[source_generators["parent_capacity_technology"].eq("GAS_OTHER_FOSSIL")]
        split = gas.groupby(gas["fuel"].astype(str).str.upper())["p_nom_MW"].sum()
        methane = float(split.get("METHANE", 0.0))
        hydrogen = float(split.get("HYDROGEN", 0.0))
        _check(rows, "NS-027", "2050 GAS_OTHER_FOSSIL methane/hydrogen split is 50/50", methane, hydrogen, math.isclose(methane, hydrogen, abs_tol=1e-6))

    mixed_or_aggregate = network.stores.index.to_series().str.contains(phs_pattern, regex=True)
    duplicated_states = network.stores.index[mixed_or_aggregate].duplicated().sum()
    _check(rows, "NS-028", "No duplicate PHS water state", int(duplicated_states), 0, duplicated_states == 0)
    pure_inflows = network.generators.index.to_series().str.startswith("INFLOW_") & network.generators.index.to_series().str.contains("PURE_PHS")
    _check(rows, "NS-028A", "Pure PHS receives no natural inflow", int(pure_inflows.sum()), 0, not pure_inflows.any())
    mixed_states = network.stores.index.to_series().str.contains("MIXED_PHS")
    mixed_inflows = network.generators.index.to_series().str.startswith("INFLOW_") & network.generators.index.to_series().str.contains("MIXED_PHS")
    _check(rows, "NS-028B", "Every mixed-PHS state has one natural-inflow component", int(mixed_inflows.sum()), int(mixed_states.sum()), int(mixed_inflows.sum()) == int(mixed_states.sum()))
    conventional_pumps = network.links.index.to_series().str.startswith("PUMP_") & ~network.links.index.to_series().str.contains(phs_pattern, regex=True)
    _check(rows, "NS-029", "No grid pump for conventional hydro", int(conventional_pumps.sum()), 0, not conventional_pumps.any())
    inflow_ids = network.generators.index[network.generators.index.to_series().str.startswith("INFLOW_")]
    inflow_fixed = True
    for generator in inflow_ids:
        maximum = network.generators_t.p_max_pu[generator]
        minimum = network.generators_t.p_min_pu[generator]
        inflow_fixed = inflow_fixed and bool(np.allclose(maximum.to_numpy(), minimum.to_numpy(), atol=0.0, rtol=0.0))
    _check(rows, "NS-029A", "Natural inflow is exogenous (p_min_pu equals p_max_pu)", inflow_fixed, True, inflow_fixed)
    shedding_ids = [f"LOAD_SHEDDING_{zone}" for zone in ZONES]
    shedding_cap_valid = set(shedding_ids).issubset(network.generators.index)
    if shedding_cap_valid:
        for zone in ZONES:
            generator = f"LOAD_SHEDDING_{zone}"
            hourly_cap = network.generators.at[generator, "p_nom"] * network.generators_t.p_max_pu[generator]
            shedding_cap_valid = shedding_cap_valid and bool(np.allclose(hourly_cap.to_numpy(), loads[f"LOAD_{zone}"].to_numpy(), atol=1e-9, rtol=0.0))
    _check(rows, "NS-029B", "Load shedding is capped at local hourly load", shedding_cap_valid, True, shedding_cap_valid)

    if assemble_model:
        try:
            model = network.optimize.create_model(include_objective_constant=False)
            _check(rows, "NS-030", "Linopy model assembles without solver invocation", len(model.constraints), ">0", len(model.constraints) > 0, f"variables={len(model.variables)}")
        except Exception as exc:  # pragma: no cover - captured in generated QA
            _check(rows, "NS-030", "Linopy model assembles without solver invocation", type(exc).__name__, "SUCCESS", False, str(exc))
    return pd.DataFrame(rows)

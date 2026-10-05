"""Structural validation for accepted-contract Stage-A PyPSA networks."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pypsa

from mem_model.stage_a.network import (
    MARKETS,
    StageAContracts,
    _as_bool,
    _hydro_profile,
    _safe_id,
    _series_for_profile,
)


def _check(rows: list[dict[str, Any]], check_id: str, description: str, passed: bool, observed: Any, expected: Any) -> None:
    rows.append(
        {
            "check_id": check_id,
            "description": description,
            "observed": observed,
            "expected": expected,
            "status": "PASS" if bool(passed) else "FAIL",
        }
    )


def _all_finite(frame: pd.DataFrame) -> bool:
    if frame.empty:
        return True
    numeric = frame.select_dtypes(include=[np.number])
    return bool(np.isfinite(numeric.to_numpy()).all())


def validate_stage_a_network(
    network: pypsa.Network,
    contracts: StageAContracts,
    metadata: dict[str, Any],
    config: dict[str, Any],
    *,
    require_full_chronology: bool = False,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    market_carrier = config["assembly"]["market_bus_carrier"]

    market_buses = set(network.buses.index[network.buses.carrier.eq(market_carrier)])
    _check(checks, "NET-001", "Exactly the ten accepted market buses are present", market_buses == set(MARKETS), sorted(market_buses), list(MARKETS))
    _check(checks, "NET-002", "CORS is absent from every bus and link endpoint", "CORS" not in set(network.buses.index) and "CORS" not in set(network.links.bus0) and "CORS" not in set(network.links.bus1), "CORS" in set(network.buses.index), False)

    snapshots_equal = network.snapshots.equals(contracts.snapshots) and not network.snapshots.has_duplicates
    _check(checks, "NET-003", "Network snapshots are a one-to-one UTC-naive representation of the accepted B3 selection", snapshots_equal, len(network.snapshots), len(contracts.snapshots))
    contiguous = len(network.snapshots) < 2 or bool((network.snapshots.to_series().diff().dropna() == pd.Timedelta(hours=1)).all())
    _check(checks, "NET-004", "Chronology is contiguous hourly", contiguous, contiguous, True)
    full_ok = not require_full_chronology or len(network.snapshots) == int(config["scope"]["chronology_hours"])
    _check(checks, "NET-005", "Formal structural mode contains exactly 8,760 snapshots", full_ok, len(network.snapshots), 8760 if require_full_chronology else len(network.snapshots))
    weights_ok = np.allclose(network.snapshot_weightings.to_numpy(dtype=float), 1.0, atol=0, rtol=0)
    _check(checks, "NET-006", "All snapshot weights are exactly one hour", weights_ok, sorted(np.unique(network.snapshot_weightings.to_numpy(dtype=float)).tolist()), [1.0])

    load_names = {f"LOAD_{market}" for market in MARKETS}
    load_ok = set(network.loads.index) == load_names
    _check(checks, "NET-007", "There is exactly one load per accepted market", load_ok, sorted(network.loads.index), sorted(load_names))
    load_values_ok = True
    for market in MARKETS:
        expected = contracts.load.loc[contracts.load.country_code.eq(market)].set_index("snapshot").load_MW.astype(float).reindex(contracts.snapshots)
        observed = network.loads_t.p_set[f"LOAD_{market}"].astype(float).reindex(contracts.snapshots)
        load_values_ok &= np.allclose(observed, expected, atol=1e-9, rtol=0)
    _check(checks, "NET-008", "Hourly loads equal the B3 Base contract", load_values_ok, load_values_ok, True)
    if require_full_chronology:
        observed_annual = {
            market: float(network.loads_t.p_set[f"LOAD_{market}"].sum()) for market in MARKETS
        }
        expected_annual = contracts.demand_controls.set_index("country_code").annual_demand_MWh.astype(float).to_dict()
        annual_ok = set(expected_annual) == set(MARKETS) and all(
            np.isclose(observed_annual[market], expected_annual[market], atol=1e-3, rtol=0) for market in MARKETS
        )
    else:
        observed_annual = {market: float(network.loads_t.p_set[f"LOAD_{market}"].sum()) for market in MARKETS}
        annual_ok = True
    _check(checks, "NET-009", "Full chronology annual load reconciles to B1/B2 controls", annual_ok, observed_annual if require_full_chronology else "SUBSET_NOT_APPLICABLE", "B1_B2_CONTROLS" if require_full_chronology else "SUBSET_NOT_APPLICABLE")

    hydro_assets = set(contracts.hydro.source_asset_id.astype(str))
    ordinary = contracts.generators.loc[~contracts.generators.asset_id.isin(hydro_assets)].copy()
    ordinary_ids = set(ordinary.asset_id.astype(str))
    ordinary_present = ordinary_ids.issubset(set(network.generators.index))
    capacities_ok = ordinary_present and all(
        np.isclose(float(network.generators.at[row.asset_id, "p_nom"]), float(row.p_nom_MW), atol=1e-9, rtol=0)
        for row in ordinary.itertuples()
    )
    _check(checks, "NET-010", "Every non-hydro B4 generator is instantiated exactly once at frozen capacity", capacities_ok, len(ordinary_ids & set(network.generators.index)), len(ordinary_ids))
    no_hydro_duplicate = hydro_assets.isdisjoint(set(network.generators.index))
    _check(checks, "NET-011", "Hydro source assets are not duplicated as ordinary generators", no_hydro_duplicate, sorted(hydro_assets & set(network.generators.index)), [])

    availability_ok = True
    marginal_cost_ok = True
    vre_joins = 0
    technical_joins = 0
    for row in ordinary.itertuples():
        observed_profile = network.generators_t.p_max_pu[row.asset_id].astype(float).reindex(contracts.snapshots)
        if row.availability_mode == "B3_TEMPORAL_PROFILE":
            expected_profile = _series_for_profile(contracts.vre, row.temporal_profile_id, "p_max_pu", contracts.snapshots)
            vre_joins += 1
        else:
            expected_profile = (
                contracts.technical_availability.loc[contracts.technical_availability.asset_id.eq(row.asset_id)]
                .set_index("snapshot").p_max_pu.astype(float).reindex(contracts.snapshots)
            )
            technical_joins += 1
        availability_ok &= np.allclose(observed_profile, expected_profile, atol=1e-12, rtol=0)
        marginal_cost_ok &= np.isclose(
            float(network.generators.at[row.asset_id, "marginal_cost"]),
            float(row.marginal_cost_EUR2025_per_MWh_el),
            atol=1e-12,
            rtol=0,
        )
    _check(checks, "NET-012", "VRE and technical-availability profiles join exactly by accepted IDs", availability_ok, {"vre": vre_joins, "technical": technical_joins}, "ALL_EXACT")
    _check(checks, "NET-013", "B4 marginal costs are assigned exactly", marginal_cost_ok, marginal_cost_ok, True)

    bess = contracts.storage.loc[contracts.storage.storage_family.eq("BESS")]
    bess_ok = True
    for row in bess.itertuples():
        safe = _safe_id(row.storage_id)
        store = f"STORE_BESS_{safe}"
        charge = f"CHARGE_BESS_{safe}"
        discharge = f"DISCHARGE_BESS_{safe}"
        bess_ok &= store in network.stores.index and charge in network.links.index and discharge in network.links.index
        if bess_ok:
            bess_ok &= np.isclose(float(network.stores.at[store, "e_nom"]), float(row.energy_MWh), atol=1e-9, rtol=0)
            bess_ok &= np.isclose(float(network.links.at[charge, "p_nom"]), float(row.charge_power_MW), atol=1e-9, rtol=0)
            bess_ok &= np.isclose(float(network.links.at[discharge, "p_nom"] * network.links.at[discharge, "efficiency"]), float(row.discharge_power_MW), atol=1e-9, rtol=0)
    _check(checks, "NET-014", "BESS power, energy, and state appear once from the B4 runtime contract", bess_ok, len(bess), len(bess))

    hydro_ok = True
    inflow_ok = True
    phs_ok = True
    for record in contracts.hydro.to_dict(orient="records"):
        safe = _safe_id(record["operational_slice_id"])
        if _as_bool(record["energy_state_required"]):
            store = f"STORE_WATER_{safe}"
            turbine = f"TURBINE_{safe}"
            hydro_ok &= store in network.stores.index and turbine in network.links.index
            if store in network.stores.index and turbine in network.links.index:
                hydro_ok &= np.isclose(float(network.stores.at[store, "e_nom"]), float(record["operational_state_energy_MWh"]), atol=1e-8, rtol=0)
                hydro_ok &= np.isclose(float(network.links.at[turbine, "p_nom"] * network.links.at[turbine, "efficiency"]), float(record["turbine_power_MW"]), atol=1e-8, rtol=0)
            pump = f"PUMP_{safe}"
            if float(record["pump_power_MW"]) > 0:
                phs_ok &= pump in network.links.index and np.isclose(float(network.links.at[pump, "p_nom"]), float(record["pump_power_MW"]), atol=1e-8, rtol=0)
            else:
                phs_ok &= pump not in network.links.index
        else:
            name = f"HYDRO_{safe}"
            hydro_ok &= name in network.generators.index
        if _as_bool(record["natural_inflow_required"]):
            expected_inflow = _hydro_profile(contracts, record)
            if _as_bool(record["energy_state_required"]):
                name = f"INFLOW_{safe}"
                inflow_ok &= name in network.generators.index
                if name in network.generators.index:
                    observed_min = network.generators_t.p_min_pu[name] * float(network.generators.at[name, "p_nom"])
                    observed_max = network.generators_t.p_max_pu[name] * float(network.generators.at[name, "p_nom"])
                    inflow_ok &= np.allclose(observed_min, expected_inflow, atol=1e-8, rtol=0)
                    inflow_ok &= np.allclose(observed_max, expected_inflow, atol=1e-8, rtol=0)
            else:
                name = f"HYDRO_{safe}"
                expected_electric = (expected_inflow * float(record["turbine_efficiency"])).clip(upper=float(record["turbine_power_MW"]))
                observed = network.generators_t.p_max_pu[name] * float(network.generators.at[name, "p_nom"])
                inflow_ok &= np.allclose(observed, expected_electric, atol=1e-8, rtol=0)
    _check(checks, "NET-015", "Every B4 hydro runtime slice has exactly its accepted state/archetype", hydro_ok, len(contracts.hydro), len(contracts.hydro))
    _check(checks, "NET-016", "Natural inflow is mapped once with exact B3 shape and B4 scaling", inflow_ok, inflow_ok, True)

    phs_rows = contracts.hydro.loc[contracts.hydro.hydro_class.isin(["PURE_PHS", "MIXED_PHS"])]
    generic_phs_store_names = [name for name in network.stores.index if "PHS" in name and name.startswith("STORE_BESS_")]
    phs_ok &= not generic_phs_store_names
    italy_phs = phs_rows.loc[phs_rows.country_code.eq("IT")]
    italy_ok = (
        len(italy_phs) == 2
        and np.isclose(italy_phs.turbine_power_MW.astype(float).sum(), 7252.3, atol=1e-8, rtol=0)
        and np.isclose(italy_phs.pump_power_MW.astype(float).sum(), 6400.0, atol=1e-8, rtol=0)
        and np.isclose(italy_phs.operational_state_energy_MWh.astype(float).sum(), 53000.0, atol=1e-8, rtol=0)
    )
    mixed_ok = True
    for row in phs_rows.loc[phs_rows.hydro_class.eq("MIXED_PHS")].itertuples():
        safe = _safe_id(row.operational_slice_id)
        mixed_ok &= sum(name == f"STORE_WATER_{safe}" for name in network.stores.index) == 1
        mixed_ok &= f"PUMP_{safe}" in network.links.index and f"TURBINE_{safe}" in network.links.index and f"SPILL_{safe}" in network.generators.index
    _check(checks, "NET-017", "PHS is instantiated only through the accepted hydro/PHS state mapping", phs_ok, len(phs_rows), len(phs_rows))
    _check(checks, "NET-018", "Italy PHS retains the exact pure/mixed 7,252.3/6,400/53,000 controls", italy_ok, {"rows": len(italy_phs), "discharge_MW": italy_phs.turbine_power_MW.astype(float).sum(), "pump_MW": italy_phs.pump_power_MW.astype(float).sum(), "energy_MWh": italy_phs.operational_state_energy_MWh.astype(float).sum()}, {"rows": 2, "discharge_MW": 7252.3, "pump_MW": 6400.0, "energy_MWh": 53000.0})
    _check(checks, "NET-019", "Each mixed PHS slice has one shared water state, pump, turbine, inflow and spill", mixed_ok, mixed_ok, True)

    interconnectors = network.links.loc[network.links.carrier.eq("INTERCONNECTOR")]
    interconnector_ok = len(interconnectors) == 12
    for interface in contracts.interfaces.itertuples():
        name = f"INTERCONNECTOR_{_safe_id(interface.physical_link_id)}"
        rows = contracts.links.loc[contracts.links.physical_link_id.eq(interface.physical_link_id)]
        a_to_b = float(rows.loc[rows.from_market.eq(interface.endpoint_a) & rows.to_market.eq(interface.endpoint_b), "capacity_MW"].iloc[0])
        b_to_a = float(rows.loc[rows.from_market.eq(interface.endpoint_b) & rows.to_market.eq(interface.endpoint_a), "capacity_MW"].iloc[0])
        interconnector_ok &= name in interconnectors.index
        if name in interconnectors.index:
            link = interconnectors.loc[name]
            interconnector_ok &= link.bus0 == interface.endpoint_a and link.bus1 == interface.endpoint_b
            interconnector_ok &= np.isclose(float(link.p_nom * link.p_max_pu), a_to_b, atol=1e-9, rtol=0)
            interconnector_ok &= np.isclose(float(-link.p_nom * link.p_min_pu), b_to_a, atol=1e-9, rtol=0)
            interconnector_ok &= float(link.efficiency) == 1.0 and float(link.marginal_cost) == 0.0
            interconnector_ok &= not bool(link.p_nom_extendable) and not bool(link.committable)
    _check(checks, "NET-020", "Exactly 12 single signed interconnectors preserve both B5 directional limits", interconnector_ok, len(interconnectors), 12)

    fixed_ok = (
        not network.generators.p_nom_extendable.astype(bool).any()
        and not network.links.p_nom_extendable.astype(bool).any()
        and not network.stores.e_nom_extendable.astype(bool).any()
        and not network.generators.committable.astype(bool).any()
        and not network.links.committable.astype(bool).any()
    )
    _check(checks, "NET-021", "All capacities are fixed and unit commitment is disabled", fixed_ok, fixed_ok, True)

    shedding_ok = True
    voll = float(config["assembly"]["feasibility"]["VOLL_EUR_per_MWh"])
    for market in MARKETS:
        name = f"LOAD_SHEDDING_{market}"
        load = network.loads_t.p_set[f"LOAD_{market}"].astype(float)
        cap = network.generators_t.p_max_pu[name].astype(float) * float(network.generators.at[name, "p_nom"])
        shedding_ok &= np.allclose(cap, load, atol=1e-9, rtol=0)
        shedding_ok &= float(network.generators.at[name, "marginal_cost"]) == voll
        shedding_ok &= float(cap.min()) >= 0
    _check(checks, "NET-022", "Local load shedding is bounded hourly by local load at VOLL 15,000", shedding_ok, shedding_ok, True)

    nuclear = contracts.generators.loc[contracts.generators.source_carrier.str.contains("NUCLEAR", case=False, na=False)]
    krsko_ok = nuclear.loc[nuclear.country_code.eq("HR")].empty and not nuclear.loc[nuclear.country_code.eq("SI")].empty
    _check(checks, "NET-023", "Krsko remains physically on SI with no HR nuclear duplicate", krsko_ok, sorted(nuclear.country_code.unique()), ["SI", "other_non_HR_hosts_allowed"])

    required_static = (
        network.generators[["p_nom", "marginal_cost", "efficiency"]],
        network.links[["p_nom", "p_min_pu", "p_max_pu", "efficiency", "marginal_cost"]],
        network.stores[["e_nom", "e_min_pu", "e_max_pu", "standing_loss"]],
    )
    finite_ok = all(_all_finite(frame) for frame in required_static)
    for table in (
        network.loads_t.p_set,
        network.generators_t.p_min_pu,
        network.generators_t.p_max_pu,
    ):
        finite_ok &= _all_finite(table)
    _check(checks, "NET-024", "All populated static and hourly numeric fields are finite", finite_ok, finite_ok, True)

    metadata_ok = metadata["summary"]["market_buses"] == 10 and metadata["summary"]["physical_interconnectors"] == 12 and metadata["summary"]["production_solve_run"] is False
    _check(checks, "NET-025", "Deterministic build metadata records no production solve", metadata_ok, metadata["summary"], "10 markets; 12 interfaces; solve false")

    registered_carriers = set(network.carriers.index.astype(str))
    referenced_by_component: dict[str, list[str]] = {}
    for component, frame in (
        ("Bus", network.buses),
        ("Generator", network.generators),
        ("Link", network.links),
        ("Store", network.stores),
    ):
        referenced = sorted(
            {
                label
                for value in frame["carrier"].dropna()
                if (label := str(value).strip())
            }
        )
        missing = sorted(set(referenced) - registered_carriers)
        if missing:
            referenced_by_component[component] = missing
    missing_carriers = sorted(
        {label for labels in referenced_by_component.values() for label in labels}
    )
    _check(
        checks,
        "NET-026",
        "Every non-empty Bus, Generator, Link, and Store carrier is explicitly registered",
        not missing_carriers,
        {"missing_carriers": missing_carriers, "missing_by_component": referenced_by_component},
        {"missing_carriers": [], "missing_by_component": {}},
    )

    qa = pd.DataFrame.from_records(checks)
    return {
        "qa": qa,
        "status": "PASS" if qa.status.eq("PASS").all() else "FAIL",
        "metrics": {
            **metadata["summary"],
            "qa_checks": len(qa),
            "qa_failures": int(qa.status.eq("FAIL").sum()),
            "ordinary_generator_contract_rows": len(ordinary),
            "vre_profile_joins": vre_joins,
            "technical_availability_joins": technical_joins,
            "bess_states": len(bess),
            "phs_states": len(phs_rows),
        },
    }

"""Stage-A PyPSA network assembly from accepted ETX-7B1 through ETX-7B5 contracts.

The module only assembles fixed-capacity networks. Formal B6 serialization and
all optimization are controlled by :mod:`mem_model.stage_a.execution`.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pypsa
import yaml


ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "config" / "stage_a_execution.yaml"
MARKETS = ("AT", "CH", "FR", "GR", "HR", "IT", "ME", "MT", "SI", "TN")

PATHS = {
    "snapshots": ROOT / "stage_a_inputs/temporal_2019_v1_0/MEM_ETX7B3_Snapshot_Index_v1.0.parquet",
    "load": ROOT / "stage_a_inputs/temporal_2019_v1_0/MEM_ETX7B3_Load_Hourly_v1.0.parquet",
    "vre": ROOT / "stage_a_inputs/temporal_2019_v1_0/MEM_ETX7B3_VRE_Availability_Hourly_v1.0.parquet",
    "hydro_temporal": ROOT / "stage_a_inputs/temporal_2019_v1_0/MEM_ETX7B3_Hydro_Temporal_Hourly_v1.0.parquet",
    "generators": ROOT / "stage_a_inputs/operating_base_v1_0/MEM_ETX7B4_Generator_Operating_Parameters_v1.0.csv",
    "technical_availability": ROOT / "stage_a_inputs/operating_base_v1_0/MEM_ETX7B4_Generator_Technical_Availability_Hourly_v1.0.parquet",
    "storage": ROOT / "stage_a_inputs/operating_base_v1_0/MEM_ETX7B4_Storage_Operating_Parameters_v1.0.csv",
    "hydro": ROOT / "stage_a_inputs/operating_base_v1_0/MEM_ETX7B4_Hydro_Annual_Scaling_and_State_v1.0.csv",
    "external_demand": ROOT / "stage_a_inputs/etx7a_v1_0/static/MEM_ETX7A_Annual_Demand_Static_v1.0.csv",
    "italy_demand": ROOT / "stage_a_inputs/italy_base_v1_0/static/MEM_ETX7B2_IT_Annual_Demand_Static_v1.0.csv",
    "interfaces": ROOT / "stage_a_inputs/topology_v1_0/MEM_ETX7B5_Physical_Interface_Registry_v1.0.csv",
    "links": ROOT / "stage_a_inputs/topology_v1_0/MEM_ETX7B5_Directional_Link_Registry_v1.0.csv",
}


@dataclass(frozen=True)
class StageAContracts:
    year: int
    snapshots: pd.DatetimeIndex
    snapshot_weights: pd.Series
    load: pd.DataFrame
    vre: pd.DataFrame
    hydro_temporal: pd.DataFrame
    generators: pd.DataFrame
    technical_availability: pd.DataFrame
    storage: pd.DataFrame
    hydro: pd.DataFrame
    demand_controls: pd.DataFrame
    interfaces: pd.DataFrame
    links: pd.DataFrame


def load_execution_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _safe_id(value: Any) -> str:
    text = str(value).strip()
    return "".join(character if character.isalnum() or character in "_-" else "_" for character in text)


def _utc_naive(values: Iterable[Any]) -> pd.DatetimeIndex:
    index = pd.DatetimeIndex(pd.to_datetime(values, utc=True, errors="raise"))
    return index.tz_convert(None)


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _manifest_member_receipts(path: Path) -> list[dict[str, Any]]:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    if not {"relative_path", "sha256"}.issubset(frame.columns):
        return []
    rows: list[dict[str, Any]] = []
    for record in frame.to_dict(orient="records"):
        relative = Path(record["relative_path"].replace("\\", "/"))
        candidate = ROOT / relative
        if not candidate.exists():
            candidate = path.parent / relative
        expected = record["sha256"].upper()
        observed = sha256_file(candidate) if candidate.exists() else ""
        rows.append(
            {
                "relative_path": candidate.relative_to(ROOT).as_posix() if candidate.exists() else relative.as_posix(),
                "expected_sha256": expected,
                "observed_sha256": observed,
                "status": "PASS" if observed == expected else "FAIL",
            }
        )
    return rows


def verify_input_locks(config: dict[str, Any]) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for input_id, lock in config["input_locks"].items():
        path = ROOT / lock["path"]
        observed = sha256_file(path) if path.exists() else ""
        expected = str(lock["sha256"]).upper()
        members = _manifest_member_receipts(path) if path.suffix.lower() == ".csv" else []
        member_status = all(row["status"] == "PASS" for row in members)
        status = "PASS" if observed == expected and member_status else "FAIL"
        if path.suffix.lower() == ".json" and path.exists():
            import json

            payload = json.loads(path.read_text(encoding="utf-8"))
            accepted = str(payload.get("status", "")).endswith("COMPLETE")
            status = "PASS" if status == "PASS" and accepted else "FAIL"
        receipts.append(
            {
                "input_id": input_id,
                "path": path.relative_to(ROOT).as_posix(),
                "expected_sha256": expected,
                "observed_sha256": observed,
                "member_count": len(members),
                "member_failures": sum(row["status"] != "PASS" for row in members),
                "status": status,
            }
        )
    if any(row["status"] != "PASS" for row in receipts):
        failures = [row for row in receipts if row["status"] != "PASS"]
        raise RuntimeError(f"Accepted Stage-A input lock failed: {failures}")
    return receipts


def _filter_snapshots(frame: pd.DataFrame, snapshots: pd.DatetimeIndex) -> pd.DataFrame:
    result = frame.copy()
    result["snapshot"] = _utc_naive(result["snapshot"])
    return result.loc[result["snapshot"].isin(snapshots)].copy()


def load_stage_a_contracts(
    year: int,
    *,
    snapshots: pd.DatetimeIndex | None = None,
) -> StageAContracts:
    if year not in {2040, 2050}:
        raise ValueError("Stage A supports only 2040 Base and 2050 Base")
    snapshot_frame = pd.read_parquet(PATHS["snapshots"])
    full_snapshots = _utc_naive(snapshot_frame["snapshot"])
    selected = full_snapshots if snapshots is None else pd.DatetimeIndex(snapshots)
    if not selected.isin(full_snapshots).all() or selected.has_duplicates:
        raise ValueError("Requested snapshots are not a unique subset of the accepted B3 chronology")
    weights = pd.Series(
        snapshot_frame.set_index(full_snapshots)["snapshot_weight_hours"].astype(float).reindex(selected).to_numpy(),
        index=selected,
        name="objective",
    )

    load = pd.read_parquet(PATHS["load"])
    load = load.loc[load["horizon"].astype(int).eq(year) & load["scenario"].eq("Base")]
    vre = pd.read_parquet(PATHS["vre"])
    hydro_temporal = pd.read_parquet(PATHS["hydro_temporal"])
    availability = pd.read_parquet(PATHS["technical_availability"])
    availability = availability.loc[availability["year"].astype(int).eq(year)]
    generators = _read_table(PATHS["generators"])
    generators = generators.loc[generators["year"].astype(int).eq(year) & generators["scenario"].eq("Base")].copy()
    storage = _read_table(PATHS["storage"])
    storage = storage.loc[storage["year"].astype(int).eq(year) & storage["scenario"].eq("Base")].copy()
    hydro = _read_table(PATHS["hydro"])
    hydro = hydro.loc[hydro["year"].astype(int).eq(year) & hydro["scenario"].eq("Base")].copy()
    external_demand = _read_table(PATHS["external_demand"])
    external_demand = external_demand.loc[external_demand["year"].astype(int).eq(year), ["country_code", "annual_demand_MWh"]].copy()
    italy_demand = _read_table(PATHS["italy_demand"])
    italy_demand = italy_demand.loc[italy_demand["year"].astype(int).eq(year), ["country_code", "annual_demand_MWh"]].copy()
    demand_controls = pd.concat([external_demand, italy_demand], ignore_index=True)
    interfaces = _read_table(PATHS["interfaces"])
    links = _read_table(PATHS["links"])
    links = links.loc[links["year"].astype(int).eq(year) & links["scenario"].eq("Base")].copy()

    return StageAContracts(
        year=year,
        snapshots=selected,
        snapshot_weights=weights,
        load=_filter_snapshots(load, selected),
        vre=_filter_snapshots(vre, selected),
        hydro_temporal=_filter_snapshots(hydro_temporal, selected),
        generators=generators,
        technical_availability=_filter_snapshots(availability, selected),
        storage=storage,
        hydro=hydro,
        demand_controls=demand_controls,
        interfaces=interfaces,
        links=links,
    )


def _series_for_profile(
    frame: pd.DataFrame,
    profile_id: str,
    value_column: str,
    snapshots: pd.DatetimeIndex,
) -> pd.Series:
    selected = frame.loc[frame["profile_id"].astype(str).eq(profile_id), ["snapshot", value_column]].copy()
    if selected["snapshot"].duplicated().any():
        raise ValueError(f"Duplicate hourly rows for profile {profile_id}")
    series = selected.set_index("snapshot")[value_column].astype(float).reindex(snapshots)
    if series.isna().any() or not np.isfinite(series.to_numpy()).all():
        raise ValueError(f"Incomplete/non-finite hourly profile {profile_id}")
    return series


def _append_time_series(existing: pd.DataFrame, additions: dict[str, pd.Series], index: pd.DatetimeIndex) -> pd.DataFrame:
    if not additions:
        return existing
    added = pd.DataFrame(additions, index=index)
    if existing.empty:
        return added
    overlap = set(existing.columns) & set(added.columns)
    if overlap:
        raise ValueError(f"Duplicate time-series assignment: {sorted(overlap)}")
    return pd.concat([existing, added], axis=1)


def _add_carriers(network: pypsa.Network, contracts: StageAContracts, config: dict[str, Any]) -> None:
    def non_empty(values: pd.Series) -> set[str]:
        return {
            label
            for value in values.dropna()
            if (label := str(value).strip())
        }

    names = {
        config["assembly"]["market_bus_carrier"],
        *config["assembly"]["auxiliary_state_bus_carriers"],
        "HYDRO_INFLOW_LIMITED",
        "INTERCONNECTOR",
        "LOAD_SHEDDING",
        "NATURAL_INFLOW",
        "SPILL",
    }
    names.update(non_empty(contracts.generators["source_carrier"]))
    names.update(non_empty(contracts.storage["storage_family"]))
    names.update(non_empty(contracts.hydro["hydro_class"]))
    for name in sorted(item for item in names if item):
        network.add("Carrier", name)


def _add_market_buses_and_loads(
    network: pypsa.Network,
    contracts: StageAContracts,
    config: dict[str, Any],
) -> dict[str, pd.Series]:
    for market in MARKETS:
        network.add("Bus", market, carrier=config["assembly"]["market_bus_carrier"])
    pivot = contracts.load.pivot(index="snapshot", columns="country_code", values="load_MW")
    pivot = pivot.reindex(index=contracts.snapshots, columns=MARKETS).astype(float)
    if pivot.isna().any().any() or not np.isfinite(pivot.to_numpy()).all() or (pivot < 0).any().any():
        raise ValueError("B3 load is incomplete, non-finite, or negative")
    series: dict[str, pd.Series] = {}
    for market in MARKETS:
        name = f"LOAD_{market}"
        network.add("Load", name, bus=market)
        network.loads_t.p_set[name] = pivot[market]
        series[market] = pivot[market]
    return series


def _add_generators(network: pypsa.Network, contracts: StageAContracts) -> pd.DataFrame:
    hydro_assets = set(contracts.hydro["source_asset_id"].astype(str))
    generators = contracts.generators.loc[~contracts.generators["asset_id"].isin(hydro_assets)].copy()
    profiles: dict[str, pd.Series] = {}
    for record in generators.to_dict(orient="records"):
        name = str(record["asset_id"])
        if _as_bool(record["p_nom_extendable"]):
            raise ValueError(f"Extendable generator is prohibited: {name}")
        efficiency = float(record["efficiency_el"]) if str(record["efficiency_el"]).strip() else 1.0
        network.add(
            "Generator",
            name,
            bus=str(record["country_code"]),
            carrier=str(record["source_carrier"]),
            p_nom=float(record["p_nom_MW"]),
            p_nom_extendable=False,
            committable=False,
            efficiency=efficiency,
            marginal_cost=float(record["marginal_cost_EUR2025_per_MWh_el"]),
        )
        mode = str(record["availability_mode"])
        if mode == "B3_TEMPORAL_PROFILE":
            profile_id = str(record["temporal_profile_id"])
            profile = _series_for_profile(contracts.vre, profile_id, "p_max_pu", contracts.snapshots)
        else:
            selected = contracts.technical_availability.loc[
                contracts.technical_availability["asset_id"].astype(str).eq(name),
                ["snapshot", "p_max_pu"],
            ]
            if selected["snapshot"].duplicated().any():
                raise ValueError(f"Duplicate technical-availability rows for {name}")
            profile = selected.set_index("snapshot")["p_max_pu"].astype(float).reindex(contracts.snapshots)
            if profile.isna().any():
                raise ValueError(f"Technical availability missing for {name}")
        if not np.isfinite(profile.to_numpy()).all() or (profile < 0).any() or (profile > 1).any():
            raise ValueError(f"Availability outside [0,1] for {name}")
        profiles[name] = profile
    network.generators_t.p_max_pu = _append_time_series(
        network.generators_t.p_max_pu,
        profiles,
        contracts.snapshots,
    )
    return generators


def _add_bess(network: pypsa.Network, contracts: StageAContracts) -> pd.DataFrame:
    bess = contracts.storage.loc[contracts.storage["storage_family"].eq("BESS")].copy()
    for record in bess.to_dict(orient="records"):
        storage_id = str(record["storage_id"])
        if _as_bool(record["p_nom_extendable"]) or _as_bool(record["e_nom_extendable"]):
            raise ValueError(f"Extendable storage is prohibited: {storage_id}")
        state_bus = f"STATE_BESS_{_safe_id(storage_id)}"
        store = f"STORE_BESS_{_safe_id(storage_id)}"
        charge = f"CHARGE_BESS_{_safe_id(storage_id)}"
        discharge = f"DISCHARGE_BESS_{_safe_id(storage_id)}"
        charge_efficiency = float(record["charge_efficiency"])
        discharge_efficiency = float(record["discharge_efficiency"])
        network.add("Bus", state_bus, carrier="BATTERY_STATE")
        network.add(
            "Store",
            store,
            bus=state_bus,
            carrier="BATTERY_STATE",
            e_nom=float(record["energy_MWh"]),
            e_nom_extendable=False,
            e_min_pu=float(record["soc_min_pu"]),
            e_max_pu=float(record["soc_max_pu"]),
            e_cyclic=str(record["terminal_state_rule"]) == "CYCLIC_ANNUAL",
            standing_loss=float(record["standing_loss_per_hour"]),
        )
        network.add(
            "Link",
            charge,
            bus0=str(record["country_code"]),
            bus1=state_bus,
            carrier="BESS",
            p_nom=float(record["charge_power_MW"]),
            p_nom_extendable=False,
            committable=False,
            p_min_pu=0.0,
            efficiency=charge_efficiency,
        )
        network.add(
            "Link",
            discharge,
            bus0=state_bus,
            bus1=str(record["country_code"]),
            carrier="BESS",
            p_nom=float(record["discharge_power_MW"]) / discharge_efficiency,
            p_nom_extendable=False,
            committable=False,
            p_min_pu=0.0,
            efficiency=discharge_efficiency,
        )
    return bess


def _hydro_profile(contracts: StageAContracts, record: dict[str, Any]) -> pd.Series:
    profile_id = str(record["natural_inflow_profile_id"])
    normalized = _series_for_profile(
        contracts.hydro_temporal,
        profile_id,
        "value",
        contracts.snapshots,
    )
    return normalized * float(record["hourly_inflow_scaling_MWh_water_per_unit_share"])


def _source_marginal_cost(contracts: StageAContracts, source_asset_id: str) -> float:
    selected = contracts.generators.loc[contracts.generators["asset_id"].eq(source_asset_id)]
    if selected.empty:
        return 0.0
    values = selected["marginal_cost_EUR2025_per_MWh_el"].astype(float).unique()
    if len(values) != 1:
        raise ValueError(f"Ambiguous hydro marginal cost for {source_asset_id}")
    return float(values[0])


def _add_hydro(network: pypsa.Network, contracts: StageAContracts) -> pd.DataFrame:
    p_max_profiles: dict[str, pd.Series] = {}
    p_min_profiles: dict[str, pd.Series] = {}
    for record in contracts.hydro.to_dict(orient="records"):
        slice_id = str(record["operational_slice_id"])
        safe = _safe_id(slice_id)
        market = str(record["country_code"])
        turbine_power = float(record["turbine_power_MW"])
        efficiency = float(record["turbine_efficiency"])
        marginal_cost = _source_marginal_cost(contracts, str(record["source_asset_id"]))
        inflow = _hydro_profile(contracts, record) if _as_bool(record["natural_inflow_required"]) else None
        if not _as_bool(record["energy_state_required"]):
            if inflow is None:
                raise ValueError(f"Inflow-limited hydro has no inflow: {slice_id}")
            electrical = inflow * efficiency
            profile = (electrical / turbine_power).clip(lower=0.0, upper=1.0)
            name = f"HYDRO_{safe}"
            network.add(
                "Generator",
                name,
                bus=market,
                carrier="HYDRO_INFLOW_LIMITED",
                p_nom=turbine_power,
                p_nom_extendable=False,
                committable=False,
                marginal_cost=marginal_cost,
            )
            p_max_profiles[name] = profile
            continue

        state_bus = f"STATE_WATER_{safe}"
        store = f"STORE_WATER_{safe}"
        turbine = f"TURBINE_{safe}"
        network.add("Bus", state_bus, carrier="WATER_STATE")
        network.add(
            "Store",
            store,
            bus=state_bus,
            carrier="WATER_STATE",
            e_nom=float(record["operational_state_energy_MWh"]),
            e_nom_extendable=False,
            e_min_pu=0.0,
            e_max_pu=1.0,
            e_cyclic=str(record["terminal_state_rule"]) == "CYCLIC_ANNUAL",
        )
        network.add(
            "Link",
            turbine,
            bus0=state_bus,
            bus1=market,
            carrier=str(record["hydro_class"]),
            p_nom=turbine_power / efficiency,
            p_nom_extendable=False,
            committable=False,
            p_min_pu=0.0,
            efficiency=efficiency,
            marginal_cost=marginal_cost * efficiency,
        )
        pump_power = float(record["pump_power_MW"])
        if pump_power > 0:
            if not _as_bool(record["grid_charging_allowed"]):
                raise ValueError(f"Pump assigned to non-chargeable hydro state: {slice_id}")
            network.add(
                "Link",
                f"PUMP_{safe}",
                bus0=market,
                bus1=state_bus,
                carrier=str(record["hydro_class"]),
                p_nom=pump_power,
                p_nom_extendable=False,
                committable=False,
                p_min_pu=0.0,
                efficiency=efficiency,
            )
        if inflow is not None:
            peak = float(inflow.max())
            if peak <= 0 or not math.isfinite(peak):
                raise ValueError(f"Natural inflow is non-positive/non-finite for {slice_id}")
            inflow_name = f"INFLOW_{safe}"
            fixed_profile = inflow / peak
            network.add(
                "Generator",
                inflow_name,
                bus=state_bus,
                carrier="NATURAL_INFLOW",
                p_nom=peak,
                p_nom_extendable=False,
                committable=False,
                marginal_cost=0.0,
            )
            p_min_profiles[inflow_name] = fixed_profile
            p_max_profiles[inflow_name] = fixed_profile
            if _as_bool(record["spill_allowed"]):
                network.add(
                    "Generator",
                    f"SPILL_{safe}",
                    bus=state_bus,
                    carrier="SPILL",
                    sign=-1.0,
                    p_nom=peak,
                    p_nom_extendable=False,
                    committable=False,
                    marginal_cost=0.0,
                )
    network.generators_t.p_min_pu = _append_time_series(
        network.generators_t.p_min_pu,
        p_min_profiles,
        contracts.snapshots,
    )
    network.generators_t.p_max_pu = _append_time_series(
        network.generators_t.p_max_pu,
        p_max_profiles,
        contracts.snapshots,
    )
    return contracts.hydro.copy()


def add_physical_interconnectors(
    network: pypsa.Network,
    interfaces: pd.DataFrame,
    directional_links: pd.DataFrame,
    config: dict[str, Any],
) -> pd.DataFrame:
    rule = config["assembly"]["interconnectors"]
    efficiency = float(rule["efficiency"])
    toll = float(rule["marginal_cost_EUR_per_MWh"])
    if efficiency != 1.0:
        raise ValueError("Single signed Stage-A interconnectors require the accepted unity-efficiency convention")
    if toll != 0.0 or _as_bool(rule["numerical_epsilon_enabled"]):
        raise ValueError("The accepted Stage-A interconnector convention is zero toll with no epsilon")
    emitted: list[dict[str, Any]] = []
    for interface in interfaces.sort_values("physical_link_id", kind="stable").to_dict(orient="records"):
        physical_id = str(interface["physical_link_id"])
        endpoint_a = str(interface["endpoint_a"])
        endpoint_b = str(interface["endpoint_b"])
        rows = directional_links.loc[directional_links["physical_link_id"].eq(physical_id)]
        if len(rows) != 2:
            raise ValueError(f"Expected two B5 directional rows for {physical_id}")
        a_to_b = rows.loc[rows["from_market"].eq(endpoint_a) & rows["to_market"].eq(endpoint_b)]
        b_to_a = rows.loc[rows["from_market"].eq(endpoint_b) & rows["to_market"].eq(endpoint_a)]
        if len(a_to_b) != 1 or len(b_to_a) != 1:
            raise ValueError(f"B5 directions do not match physical endpoints for {physical_id}")
        capacity_a_to_b = float(a_to_b.iloc[0]["capacity_MW"])
        capacity_b_to_a = float(b_to_a.iloc[0]["capacity_MW"])
        nominal = max(capacity_a_to_b, capacity_b_to_a)
        if nominal <= 0:
            raise ValueError(f"Physical interface {physical_id} has no positive directional capacity")
        link_name = f"INTERCONNECTOR_{_safe_id(physical_id)}"
        p_max_pu = capacity_a_to_b / nominal
        p_min_pu = -capacity_b_to_a / nominal
        network.add(
            "Link",
            link_name,
            bus0=endpoint_a,
            bus1=endpoint_b,
            carrier="INTERCONNECTOR",
            p_nom=nominal,
            p_nom_extendable=False,
            committable=False,
            p_min_pu=p_min_pu,
            p_max_pu=p_max_pu,
            efficiency=efficiency,
            marginal_cost=toll,
        )
        emitted.append(
            {
                "physical_link_id": physical_id,
                "pypsa_link": link_name,
                "bus0": endpoint_a,
                "bus1": endpoint_b,
                "capacity_bus0_to_bus1_MW": capacity_a_to_b,
                "capacity_bus1_to_bus0_MW": capacity_b_to_a,
                "p_nom_MW": nominal,
                "p_max_pu": p_max_pu,
                "p_min_pu": p_min_pu,
                "efficiency": efficiency,
                "marginal_cost_EUR_per_MWh": toll,
                "counterflow_degeneracy": "IMPOSSIBLE_SINGLE_SIGNED_FLOW_VARIABLE",
            }
        )
    return pd.DataFrame.from_records(emitted)


def _add_load_shedding(
    network: pypsa.Network,
    load_by_market: dict[str, pd.Series],
    config: dict[str, Any],
) -> None:
    feasibility = config["assembly"]["feasibility"]
    if not _as_bool(feasibility["load_shedding_enabled"]):
        return
    voll = float(feasibility["VOLL_EUR_per_MWh"])
    profiles: dict[str, pd.Series] = {}
    for market, load in load_by_market.items():
        peak = float(load.max())
        name = f"LOAD_SHEDDING_{market}"
        network.add(
            "Generator",
            name,
            bus=market,
            carrier="LOAD_SHEDDING",
            p_nom=peak,
            p_nom_extendable=False,
            committable=False,
            marginal_cost=voll,
        )
        profiles[name] = load / peak
    network.generators_t.p_max_pu = _append_time_series(
        network.generators_t.p_max_pu,
        profiles,
        network.snapshots,
    )


def build_network_from_contracts(
    contracts: StageAContracts,
    config: dict[str, Any] | None = None,
) -> tuple[pypsa.Network, dict[str, Any]]:
    config = config or load_execution_config()
    if tuple(config["scope"]["markets"]) != MARKETS:
        raise ValueError("Execution config does not contain the exact accepted market ordering")
    network = pypsa.Network()
    network.set_snapshots(contracts.snapshots)
    network.snapshot_weightings.loc[:, :] = np.asarray(contracts.snapshot_weights)[:, None]
    _add_carriers(network, contracts, config)
    load_by_market = _add_market_buses_and_loads(network, contracts, config)
    conventional_generators = _add_generators(network, contracts)
    bess = _add_bess(network, contracts)
    hydro = _add_hydro(network, contracts)
    interconnectors = add_physical_interconnectors(
        network,
        contracts.interfaces,
        contracts.links,
        config,
    )
    _add_load_shedding(network, load_by_market, config)
    network.meta = {
        "model": "MEM_STAGE_A_FIXED_TEN_MARKET_DISPATCH",
        "horizon": contracts.year,
        "scenario": "Base",
        "chronology": "2019_UTC",
        "capacity_expansion": False,
        "unit_commitment": False,
        "interconnector_representation": config["assembly"]["interconnectors"]["representation"],
        "formal_gate": "NOT_YET_ACCEPTED",
    }
    metadata = {
        "horizon": contracts.year,
        "snapshots": len(contracts.snapshots),
        "market_buses": len(MARKETS),
        "auxiliary_state_buses": len(network.buses) - len(MARKETS),
        "loads": len(network.loads),
        "conventional_and_vre_generators": len(conventional_generators),
        "bess_states": len(bess),
        "hydro_and_phs_slices": len(hydro),
        "physical_interconnectors": len(interconnectors),
        "load_shedding_generators": int(network.generators.carrier.eq("LOAD_SHEDDING").sum()),
        "production_solve_run": False,
    }
    return network, {"summary": metadata, "interconnector_mapping": interconnectors}


def build_stage_a_network(
    year: int,
    *,
    snapshots: pd.DatetimeIndex | None = None,
    config_path: Path = CONFIG_PATH,
) -> tuple[pypsa.Network, dict[str, Any]]:
    config = load_execution_config(config_path)
    verify_input_locks(config)
    contracts = load_stage_a_contracts(year, snapshots=snapshots)
    return build_network_from_contracts(contracts, config)

"""Exact schematic edge crosswalk for the accepted Stage-B interface network.

One plotted edge represents a reciprocal pair of directed PyPSA Links. The
schematic conveys connectivity, not physical line routes or annual flow.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterable

import pandas as pd
import pypsa

from ..common import ROOT


INTERFACE_CARRIERS = frozenset({"internal_transfer", "external_trade", "corsica_hub"})
ITALIAN_ZONES = frozenset({"CALA", "CNOR", "CSUD", "NORD", "SARD", "SICI", "SUD"})
EXTERNAL_MARKETS = frozenset({"EXT_FR", "EXT_CH", "EXT_AT", "EXT_SI", "EXT_ME", "EXT_GR", "EXT_MT", "EXT_TN"})


def edge_key(carrier: str, from_bus: str, to_bus: str) -> tuple[str, str, str]:
    a, b = sorted((str(from_bus), str(to_bus)))
    return str(carrier), a, b


def _directional_signature(frame: pd.DataFrame) -> Counter[tuple[str, str, str, float]]:
    return Counter(
        (
            str(row.carrier),
            str(row.from_bus),
            str(row.to_bus),
            round(float(row.capacity_MW), 6),
        )
        for row in frame.itertuples(index=False)
    )


def validate_model_interface_table(network: pypsa.Network, table: pd.DataFrame) -> None:
    """Require exact link IDs, endpoints, carriers and capacities in the backing table."""

    required = {"interface_id", "from_bus", "to_bus", "carrier", "capacity_MW"}
    if not required.issubset(table.columns):
        raise ValueError(f"Topology interface table lacks columns: {sorted(required - set(table.columns))}")
    if table["interface_id"].duplicated().any():
        raise ValueError("Topology interface table contains duplicate Link IDs")
    if network.meta.get("network_v2b_applied", False):
        from ..final_network_signed import directional_capacity_table
        expected = directional_capacity_table(network).set_index("interface_id")
        actual = table.set_index("interface_id")
        if set(actual.index) != set(expected.index):
            raise ValueError("Topology interface table direction IDs differ from signed network bounds")
        for name, row in expected.iterrows():
            value = actual.loc[name]
            if any(str(value[key]) != str(row[key]) for key in ("from_bus", "to_bus", "carrier", "modeled_link", "representation")):
                raise ValueError(f"Topology interface table differs from signed Link direction {name}")
            if any(abs(float(value[key]) - float(row[key])) > 1e-6 for key in ("capacity_MW", "minimum_capacity_MW", "efficiency")):
                raise ValueError(f"Topology interface table differs from signed Link bounds {name}")
        return
    modeled = network.links.loc[network.links["carrier"].astype(str).isin(INTERFACE_CARRIERS)]
    if set(table["interface_id"].astype(str)) != set(modeled.index.astype(str)):
        raise ValueError("Topology interface table Link IDs differ from solved network")
    for row in table.itertuples(index=False):
        link = modeled.loc[str(row.interface_id)]
        if (
            str(link["bus0"]) != str(row.from_bus)
            or str(link["bus1"]) != str(row.to_bus)
            or str(link["carrier"]) != str(row.carrier)
            or abs(float(link["p_nom"]) - float(row.capacity_MW)) > 1e-6
        ):
            raise ValueError(f"Topology interface table differs from solved Link {row.interface_id}")


def accepted_2040_interface_table(scenario: str, root: Path = ROOT) -> pd.DataFrame:
    """Build expected directional rows from the frozen 2040 static contracts."""

    internal = pd.read_csv(root / "pre_pypsa_inputs" / "MEM_Interzonal_Static_Contract.csv")
    internal = internal.loc[
        internal["year"].astype(int).eq(2040)
        & internal["scenario"].astype(str).eq(str(scenario))
    ]
    if len(internal) != 20:
        raise ValueError(f"Accepted 2040 {scenario} internal topology must contain 20 directional rows")
    rows = [
        {
            "from_bus": str(row.from_zone),
            "to_bus": str(row.to_zone),
            "carrier": "internal_transfer",
            "capacity_MW": float(row.capacity_MW),
        }
        for row in internal.itertuples(index=False)
    ]
    external = pd.read_csv(root / "pre_pypsa_inputs" / "MEM_External_Interface_Static_Contract.csv")
    for row in external.itertuples(index=False):
        market = str(row.external_market)
        zone = str(row.Italian_zone)
        direction = str(row.direction)
        if market == "CORS":
            bus0, bus1 = (zone, "CORS") if direction == "EXPORT" else ("CORS", zone)
            carrier = "corsica_hub"
        else:
            boundary = f"EXT_{market}"
            bus0, bus1 = (boundary, zone) if direction == "IMPORT" else (zone, boundary)
            carrier = "external_trade"
        rows.append({"from_bus": bus0, "to_bus": bus1, "carrier": carrier, "capacity_MW": float(row.capacity_MW)})
    return pd.DataFrame(rows)


def validate_accepted_2040_interfaces(table: pd.DataFrame, scenario: str, root: Path = ROOT) -> None:
    expected = accepted_2040_interface_table(scenario, root)
    actual = _directional_signature(table)
    frozen = _directional_signature(expected)
    if actual != frozen:
        missing = list((frozen - actual).elements())
        extra = list((actual - frozen).elements())
        raise ValueError(f"Topology interfaces differ from accepted 2040 contract: missing={missing}, extra={extra}")
    if "minimum_capacity_MW" in table.columns and ((table["capacity_MW"] - table["minimum_capacity_MW"]).abs() > 1e-6).any():
        raise ValueError("Topology time-dependent limits differ from accepted 2040 static contract")
    buses = set(table["from_bus"].astype(str)) | set(table["to_bus"].astype(str))
    if buses != ITALIAN_ZONES | EXTERNAL_MARKETS | {"CORS"}:
        raise ValueError(f"Topology bus set differs from accepted seven-zone/boundary set: {sorted(buses)}")


def collapse_reciprocal_interfaces(table: pd.DataFrame, *, require_reciprocal: bool = True) -> pd.DataFrame:
    """Return one schematic edge per physical/commercial connection."""

    groups: dict[tuple[str, str, str], list[tuple[str, str, float]]] = {}
    for row in table.itertuples(index=False):
        key = edge_key(row.carrier, row.from_bus, row.to_bus)
        if key[1] == key[2]:
            raise ValueError(f"Topology self-loop is not an accepted interface: {key}")
        groups.setdefault(key, []).append((str(row.from_bus), str(row.to_bus), float(row.capacity_MW)))
    rows = []
    for key, directions in sorted(groups.items()):
        if len(directions) > 2:
            raise ValueError(f"Duplicate reciprocal topology edge: {key}")
        if require_reciprocal and len(directions) != 2:
            raise ValueError(f"Missing reciprocal directed Link for topology edge: {key}")
        if len(directions) == 2 and {(a, b) for a, b, _ in directions} != {(key[1], key[2]), (key[2], key[1])}:
            raise ValueError(f"Duplicate or wrong directed Link orientation for topology edge: {key}")
        rows.append(
            {
                "carrier": key[0],
                "bus_a": key[1],
                "bus_b": key[2],
                "directional_link_count": len(directions),
                "capacity_a_to_b_MW": next((capacity for a, b, capacity in directions if (a, b) == (key[1], key[2])), float("nan")),
                "capacity_b_to_a_MW": next((capacity for a, b, capacity in directions if (a, b) == (key[2], key[1])), float("nan")),
                "maximum_directional_capacity_MW": max(capacity for _, _, capacity in directions),
            }
        )
    return pd.DataFrame(rows)


def validate_plotted_edge_set(table: pd.DataFrame, plotted_keys: Iterable[tuple[str, str, str]]) -> None:
    """Catch omissions, additions and duplicate reciprocal drawing operations."""

    expected = Counter(edge_key(row.carrier, row.from_bus, row.to_bus) for row in table.itertuples(index=False))
    expected = Counter({key: 1 for key in expected})
    observed = Counter(tuple(key) for key in plotted_keys)
    if observed != expected:
        missing = list((expected - observed).elements())
        extra = list((observed - expected).elements())
        raise ValueError(f"Plotted topology edge set differs from canonical interface table: missing={missing}, extra={extra}")

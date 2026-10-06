from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from mem_model.common import ROOT
from mem_model.reporting.topology import (
    EXTERNAL_MARKETS,
    ITALIAN_ZONES,
    collapse_reciprocal_interfaces,
    edge_key,
    validate_accepted_2040_interfaces,
    validate_plotted_edge_set,
)


def _table(scenario: str) -> pd.DataFrame:
    return pd.read_csv(ROOT / "results" / f"MEM_2040_{scenario.upper()}_CANONICAL" / "statistics" / "network_topology_interfaces.csv")


@pytest.mark.parametrize("scenario", ["Slow", "Base", "High"])
def test_exact_frozen_topology_and_directional_capacities(scenario: str) -> None:
    table = _table(scenario)
    validate_accepted_2040_interfaces(table, scenario)
    edges = collapse_reciprocal_interfaces(table)
    assert len(table) == 40
    assert len(edges) == 20
    assert edges.groupby("carrier").size().to_dict() == {"corsica_hub": 2, "external_trade": 8, "internal_transfer": 10}
    assert edges["directional_link_count"].eq(2).all()
    assert edges[["capacity_a_to_b_MW", "capacity_b_to_a_MW"]].notna().all().all()
    assert set(table["from_bus"]) | set(table["to_bus"]) == ITALIAN_ZONES | EXTERNAL_MARKETS | {"CORS"}
    plotted = [edge_key(row.carrier, row.bus_a, row.bus_b) for row in edges.itertuples(index=False)]
    validate_plotted_edge_set(table, plotted)


def test_schematic_connectivity_invariant_across_scenarios() -> None:
    keys = []
    for scenario in ("Slow", "Base", "High"):
        edges = collapse_reciprocal_interfaces(_table(scenario))
        keys.append({edge_key(row.carrier, row.bus_a, row.bus_b) for row in edges.itertuples(index=False)})
    assert keys[0] == keys[1] == keys[2]


def test_missing_extra_duplicate_wrong_attachment_and_capacity_fail() -> None:
    source = _table("Base")
    edge = collapse_reciprocal_interfaces(source)
    plotted = [edge_key(row.carrier, row.bus_a, row.bus_b) for row in edge.itertuples(index=False)]
    with pytest.raises(ValueError, match="missing"):
        validate_plotted_edge_set(source, plotted[:-1])
    with pytest.raises(ValueError, match="extra"):
        validate_plotted_edge_set(source, plotted + [("internal_transfer", "CALA", "NORD")])
    with pytest.raises(ValueError, match="extra"):
        validate_plotted_edge_set(source, plotted + [plotted[0]])
    changed = source.copy()
    i = changed.index[changed["interface_id"].astype(str).str.contains("FR_TO_NORD")][0]
    changed.at[i, "to_bus"] = "CNOR"
    with pytest.raises(ValueError, match="accepted 2040 contract"):
        validate_accepted_2040_interfaces(changed, "Base")
    changed = source.copy()
    changed.at[0, "capacity_MW"] = float(changed.at[0, "capacity_MW"]) + 1.0
    with pytest.raises(ValueError, match="accepted 2040 contract"):
        validate_accepted_2040_interfaces(changed, "Base")


def test_missing_reciprocal_link_fails() -> None:
    with pytest.raises(ValueError, match="Missing reciprocal"):
        collapse_reciprocal_interfaces(_table("Base").iloc[1:])

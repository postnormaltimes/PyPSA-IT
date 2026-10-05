"""PyPSA-IT read-only adapter for plotting historical saved artifacts.

Italy/GME semantics remain here. The generic plotting package never imports
this adapter. Current project governance and result status still determine
whether an artifact may be used as evidence.
"""

from __future__ import annotations

from pathlib import Path

from .mem_adapter_template import AdaptedCase, MEMCaseConfig, open_mem_case
from visualization_toolkit.network_maps import bundled_geography_path


# Exact categorical colours visible in the archived R2B 42-bus SVG. The
# generic toolkit receives these as data; it contains no GME categories.
RECOVERED_R2B_ZONE_COLORS = {
    "NORD": "#4c78a8", "CNOR": "#54a24b", "CSUD": "#f58518",
    "SUD": "#e45756", "CALA": "#b279a2", "SICI": "#eeca3b",
    "SARD": "#72b7b2",
}


def open_pypsa_it_case(
    solved_network_path: str | Path,
    *,
    scenario_id: str,
    output_directory: str | Path,
    bus_to_market: dict[str, str] | None = None,
    declared_bus_market_column: str | None = "gme_zone",
    carrier_mapping: dict[str, str] | None = None,
    result_tables: dict[str, str | Path] | None = None,
    year: int | str = 2013,
    status: str = "historical_visual_example",
    group_colors: dict[str, str] | None = None,
    link_classification: dict[str, str] | None = None,
) -> AdaptedCase:
    """Open a saved PyPSA-IT network; choose GME zone or country explicitly."""
    return open_mem_case(
        MEMCaseConfig(
            solved_network_path=solved_network_path,
            scenario_id=scenario_id,
            year=year,
            scenario_group="PyPSA-IT",
            output_directory=output_directory,
            market_mapping=dict(bus_to_market or {}),
            market_column=declared_bus_market_column,
            carrier_mapping=dict(carrier_mapping or {}),
            result_tables=dict(result_tables or {}),
            metadata={"source_project": "PyPSA-IT", "source_status": status},
            map={
                "geography": str(bundled_geography_path()),
                "extent": [6.0, 19.5, 35.0, 48.5],
                "group_colors": dict(group_colors or RECOVERED_R2B_ZONE_COLORS),
                "link_classification": dict(link_classification or {}),
                "show_group_labels": True,
                "show_bus_labels": False,
            },
        )
    )

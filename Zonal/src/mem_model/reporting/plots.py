from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pypsa
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch

from .canonical_results import reporting_config
from .topology import (
    ITALIAN_ZONES,
    collapse_reciprocal_interfaces,
    edge_key,
    validate_accepted_2040_interfaces,
    validate_model_interface_table,
    validate_plotted_edge_set,
)


def _style() -> tuple[list[str], dict[str, str], str]:
    config = reporting_config()
    available = {font.name for font in font_manager.fontManager.ttflist}
    font = next((name for name in config["font_preference"] if name in available), "DejaVu Sans")
    plt.rcParams.update(
        {
            "font.family": font,
            "axes.facecolor": "white",
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": False,
            "legend.frameon": False,
            "figure.dpi": 120,
        }
    )
    order = [str(value) for value in config["technology_order"]]
    colors = {str(key): str(value) for key, value in config["technology_colors"].items()}
    return order, colors, font


def _save(figure: plt.Figure, directory: Path, stem: str, title: str) -> list[str]:
    config = reporting_config()["outputs"]
    outputs: list[str] = []
    metadata = {
        "Title": title,
        "Creator": "MEM canonical PyPSA reporting pipeline",
        "Description": "Source values are exported in the sibling statistics directory.",
    }
    if config.get("save_png", True):
        filename = f"{stem}.png"
        figure.savefig(directory / filename, dpi=int(config.get("png_dpi", 180)), bbox_inches="tight", metadata=metadata)
        outputs.append(filename)
    if config.get("save_svg", True):
        filename = f"{stem}.svg"
        figure.savefig(directory / filename, bbox_inches="tight", metadata=metadata)
        outputs.append(filename)
    plt.close(figure)
    return outputs


def _stacked_bar(
    table: pd.DataFrame,
    index: str,
    category: str,
    metric: str,
    x_order: list[str],
    title: str,
    ylabel: str,
    directory: Path,
    stem: str,
) -> list[str]:
    technology_order, colors, _ = _style()
    pivot = table.pivot_table(index=index, columns=category, values=metric, aggfunc="sum", observed=False).fillna(0.0)
    pivot = pivot.reindex(index=x_order, fill_value=0.0)
    columns = [column for column in technology_order if column in pivot.columns and not np.isclose(pivot[column], 0.0).all()]
    pivot = pivot.reindex(columns=columns, fill_value=0.0)
    figure, axis = plt.subplots(figsize=(11.5, 6.5))
    bottom = np.zeros(len(pivot))
    positions = np.arange(len(pivot))
    for column in columns:
        values = pivot[column].to_numpy(dtype="float64")
        axis.bar(positions, values, bottom=bottom, label=column, color=colors[column], width=0.72)
        bottom += values
    axis.set_xticks(positions, pivot.index.astype(str))
    axis.set_ylabel(ylabel)
    axis.set_title(title, loc="left", fontweight="semibold")
    axis.legend(ncol=3, loc="upper left", bbox_to_anchor=(0, -0.13))
    axis.margins(x=0.02)
    return _save(figure, directory, stem, title)


def _national_bar(
    table: pd.DataFrame,
    category: str,
    metric: str,
    title: str,
    ylabel: str,
    directory: Path,
    stem: str,
) -> list[str]:
    order, colors, _ = _style()
    values = table.set_index(category)[metric]
    technologies = [technology for technology in order if technology in values.index and not np.isclose(values[technology], 0.0)]
    figure, axis = plt.subplots(figsize=(12, 6))
    positions = np.arange(len(technologies))
    axis.bar(positions, [values[technology] for technology in technologies], color=[colors[t] for t in technologies])
    axis.set_xticks(positions, technologies, rotation=40, ha="right")
    axis.set_ylabel(ylabel)
    axis.set_title(title, loc="left", fontweight="semibold")
    return _save(figure, directory, stem, title)


def _demand_generation(
    table: pd.DataFrame,
    horizon: int,
    scenario: str,
    directory: Path,
) -> list[str]:
    _style()
    zones = [str(value) for value in reporting_config()["zone_order"]]
    frame = table.set_index("zone").reindex(zones)
    positions = np.arange(len(zones))
    series = [
        ("gross_end_use_demand_TWh", "Gross end-use demand", "#333333", 1.0),
        ("primary_generation_TWh", "Primary generation", "#4C78A8", 1.0),
        ("storage_phs_discharge_TWh", "BESS/PHS discharge", "#7E57C2", 1.0),
        ("net_imports_TWh", "Net imports (+) / exports (-)", "#F58518", 1.0),
        ("storage_phs_charging_TWh", "BESS/PHS charging", "#9E77ED", -1.0),
    ]
    if not np.isclose(frame["load_shedding_TWh"].to_numpy(dtype="float64"), 0.0).all():
        series.append(("load_shedding_TWh", "Load shedding", "#D62728", 1.0))
    width = min(0.16, 0.78 / len(series))
    figure, axis = plt.subplots(figsize=(11.5, 6.2))
    offsets = (np.arange(len(series)) - (len(series) - 1) / 2.0) * width
    for offset, (column, label, color, sign) in zip(offsets, series):
        axis.bar(positions + offset, frame[column] * sign, width, label=label, color=color)
    axis.axhline(0.0, color="#666666", linewidth=0.8)
    axis.set_xticks(positions, zones)
    axis.set_ylabel("TWh")
    title = f"Annual electrical supply, demand and charging — {horizon} {scenario}"
    axis.set_title(title, loc="left", fontweight="semibold")
    axis.legend(ncol=3, loc="upper left", bbox_to_anchor=(0, -0.10))
    return _save(figure, directory, "annual_demand_vs_generation_by_zone", title)


def _prices(table: pd.DataFrame, horizon: int, scenario: str, directory: Path) -> list[str]:
    _style()
    zones = [str(value) for value in reporting_config()["zone_order"]]
    metric = str(reporting_config()["canonical_price_metric"])
    frame = table.set_index("zone").reindex(zones)
    figure, axis = plt.subplots(figsize=(10.5, 5.8))
    positions = np.arange(len(zones))
    axis.bar(positions, frame[metric], color="#4C78A8", width=0.68)
    axis.set_xticks(positions, zones)
    axis.set_ylabel("EUR/MWh")
    title = f"Load-weighted average zonal wholesale price — {horizon} {scenario}"
    axis.set_title(title, loc="left", fontweight="semibold")
    axis.text(0.0, -0.13, "No scarcity-price clipping applied", transform=axis.transAxes, color="#555555")
    return _save(figure, directory, "average_price_by_zone", title)


def _dispatch_figures(table: pd.DataFrame, horizon: int, scenario: str, directory: Path) -> list[str]:
    order, colors, _ = _style()
    outputs: list[str] = []
    zones = [str(value) for value in reporting_config()["zone_order"]] + ["NATIONAL"]
    fixed = {
        "snapshot",
        "zone",
        "gross_end_use_demand_MW",
        "bess_charging_MW",
        "phs_charging_MW",
        "storage_phs_charging_MW",
        "net_imports_MW",
        "load_shedding_MW",
    }
    technology_columns = [technology for technology in order if technology in table.columns and technology not in fixed]
    for zone in zones:
        frame = table.loc[table["zone"].astype(str).eq(zone)].sort_values("snapshot")
        if frame.empty:
            raise ValueError(f"Dispatch source table lacks {zone}")
        active = [column for column in technology_columns if not np.isclose(frame[column].to_numpy(dtype="float64"), 0.0).all()]
        x = np.arange(len(frame))
        figure, axis = plt.subplots(figsize=(15, 6.6))
        if active:
            axis.stackplot(
                x,
                *[frame[column].to_numpy(dtype="float64") for column in active],
                labels=active,
                colors=[colors[column] for column in active],
                alpha=0.92,
                linewidth=0.0,
            )
        axis.plot(x, frame["gross_end_use_demand_MW"].to_numpy(dtype="float64"), color="#111111", linewidth=0.75, label="Gross end-use demand")
        charging = frame["storage_phs_charging_MW"].to_numpy(dtype="float64")
        if not np.isclose(charging, 0.0).all():
            axis.fill_between(x, -charging, 0.0, color="#6A51A3", alpha=0.35, label="BESS/PHS charging")
        shedding = frame["load_shedding_MW"].to_numpy(dtype="float64")
        if not np.isclose(shedding, 0.0).all():
            axis.plot(x, shedding, color="#D62728", linewidth=0.75, label="Load shedding")
        axis.axhline(0.0, color="#666666", linewidth=0.5)
        ticks = np.linspace(0, len(frame) - 1, 13, dtype=int)
        labels = pd.to_datetime(frame.iloc[ticks]["snapshot"]).dt.strftime("%b")
        axis.set_xticks(ticks, labels)
        axis.set_xlim(0, len(frame) - 1)
        axis.set_ylabel("MW")
        title = f"8,760-hour electricity supply and demand — {zone}, {horizon} {scenario}"
        axis.set_title(title, loc="left", fontweight="semibold")
        axis.legend(ncol=4, loc="upper left", bbox_to_anchor=(0, -0.12))
        outputs.extend(_save(figure, directory, f"dispatch_8760_{zone}", title))
    return outputs


def _net_imports(table: pd.DataFrame, horizon: int, scenario: str, directory: Path) -> list[str]:
    _style()
    zones = [str(value) for value in reporting_config()["zone_order"]]
    frame = table.set_index("zone").reindex(zones)
    values = frame["annual_net_imports_TWh"].to_numpy(dtype="float64")
    figure, axis = plt.subplots(figsize=(10.5, 5.7))
    positions = np.arange(len(zones))
    axis.bar(positions, values, color=np.where(values >= 0, "#4C78A8", "#E45756"))
    axis.axhline(0.0, color="#555555", linewidth=0.8)
    axis.set_xticks(positions, zones)
    axis.set_ylabel("TWh (+ imports, − exports)")
    title = f"Annual net imports by zone — {horizon} {scenario}"
    axis.set_title(title, loc="left", fontweight="semibold")
    return _save(figure, directory, "annual_net_imports_by_zone", title)


def _topology(network: pypsa.Network, table: pd.DataFrame, horizon: int, scenario: str, directory: Path) -> list[str]:
    _style()
    config = reporting_config()
    validate_model_interface_table(network, table)
    production_2040 = int(horizon) == 2040 and len(network.snapshots) == 8760
    if production_2040:
        validate_accepted_2040_interfaces(table, scenario)
    edges = collapse_reciprocal_interfaces(table, require_reciprocal=production_2040)
    coordinates = {str(key): tuple(value) for key, value in config["schematic_coordinates"].items()}
    buses = sorted(set(table["from_bus"].astype(str)) | set(table["to_bus"].astype(str)))
    missing = set(buses) - set(coordinates)
    if missing:
        raise ValueError(f"Schematic topology layout lacks buses: {sorted(missing)}")
    if production_2040 and set(buses) & ITALIAN_ZONES != ITALIAN_ZONES:
        raise ValueError("Canonical topology figure does not contain exactly seven Italian zones")
    colors = {"internal_transfer": "#557A95", "external_trade": "#C47B37", "corsica_hub": "#7F65A8"}
    styles = {"internal_transfer": "solid", "external_trade": "dashed", "corsica_hub": "dotted"}
    curvature = config.get("schematic_edge_curvature", {})
    figure, axis = plt.subplots(figsize=(12, 10))
    plotted_keys: list[tuple[str, str, str]] = []
    for row in edges.itertuples(index=False):
        key = edge_key(row.carrier, row.bus_a, row.bus_b)
        x0, y0 = coordinates[row.bus_a]
        x1, y1 = coordinates[row.bus_b]
        radius = float(curvature.get("|".join((row.bus_a, row.bus_b)), 0.0))
        segment = FancyArrowPatch(
            (float(x0), float(y0)),
            (float(x1), float(y1)),
            arrowstyle="-",
            connectionstyle=f"arc3,rad={radius}",
            linewidth=2.1 if row.carrier == "internal_transfer" else 1.65,
            linestyle=styles[row.carrier],
            color=colors[row.carrier],
            shrinkA=12,
            shrinkB=12,
            zorder=1,
        )
        segment.set_gid("|".join(key))
        axis.add_patch(segment)
        plotted_keys.append(key)
    validate_plotted_edge_set(table, plotted_keys)
    for bus in buses:
        x, y = (float(value) for value in coordinates[bus])
        if bus in ITALIAN_ZONES:
            marker, color, size, label = "o", "#2F6690", 190, bus
        elif bus == "CORS":
            marker, color, size, label = "D", "#7F65A8", 135, "CORS\n0 injection · no price"
        else:
            marker, color, size, label = "s", "#C47B37", 115, bus.removeprefix("EXT_")
        axis.scatter([x], [y], s=size, marker=marker, color=color, edgecolor="white", linewidth=1.4, zorder=3)
        label_dx, label_dy = (-0.05, 0.36) if bus == "CORS" else (0.17, 0.18)
        axis.text(
            x + label_dx,
            y + label_dy,
            label,
            fontsize=9.4,
            fontweight="semibold" if bus in ITALIAN_ZONES else "normal",
            color="#263542",
            zorder=4,
            ha="center" if bus == "CORS" else "left",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.92, "pad": 1.5},
        )
    title = f"Stage-B {horizon} interface topology — schematic"
    axis.set_title(title, loc="left", fontweight="semibold")
    axis.text(
        0.0,
        -0.045,
        "One line per modeled corridor; directional bounds from actual Links. Lines show connectivity, not physical routes or annual flow.\n"
        "Directional capacities are retained in network_topology_interfaces.csv and may differ by scenario.",
        transform=axis.transAxes,
        ha="left",
        va="top",
        fontsize=8.5,
        color="#53616D",
    )
    axis.legend(
        handles=[
            Line2D([0], [0], color=colors["internal_transfer"], linewidth=2.1, label="Italian interzonal interface"),
            Line2D([0], [0], color=colors["external_trade"], linewidth=1.65, linestyle="dashed", label="Price-taking external boundary"),
            Line2D([0], [0], color=colors["corsica_hub"], linewidth=1.65, linestyle="dotted", label="CORS zero-injection hub leg"),
        ],
        loc="lower center",
        bbox_to_anchor=(0.5, -0.17),
        ncol=3,
        fontsize=8.8,
    )
    xs = [float(coordinates[bus][0]) for bus in buses]
    ys = [float(coordinates[bus][1]) for bus in buses]
    axis.set_xlim(min(xs) - 0.7, max(xs) + 1.1)
    axis.set_ylim(min(ys) - 0.8, max(ys) + 0.6)
    axis.set_aspect("equal", adjustable="box")
    axis.set_axis_off()
    return _save(figure, directory, "network_topology", title)


def render_canonical_figures(
    network: pypsa.Network,
    tables: Mapping[str, pd.DataFrame],
    *,
    horizon: int,
    scenario: str,
    output_directory: Path,
) -> list[str]:
    output_directory.mkdir(parents=True, exist_ok=True)
    zones = [str(value) for value in reporting_config()["zone_order"]]
    outputs: list[str] = []
    outputs.extend(
        _stacked_bar(
            tables["installed_capacity_by_zone.csv"],
            "zone",
            "display_technology",
            "installed_capacity_GW",
            zones,
            f"Installed generation and discharge capacity by zone — {horizon} {scenario}",
            "GW",
            output_directory,
            "installed_capacity_by_zone",
        )
    )
    outputs.extend(
        _national_bar(
            tables["installed_capacity_national.csv"],
            "display_technology",
            "installed_capacity_GW",
            f"National installed generation and discharge capacity — {horizon} {scenario}",
            "GW",
            output_directory,
            "installed_capacity_national",
        )
    )
    outputs.extend(
        _stacked_bar(
            tables["annual_electricity_supply_by_zone.csv"],
            "zone",
            "display_technology",
            "annual_electricity_supply_TWh",
            zones,
            f"Annual electricity supply by technology and zone — {horizon} {scenario}",
            "TWh",
            output_directory,
            "annual_electricity_supply_by_zone",
        )
    )
    outputs.extend(_demand_generation(tables["annual_electrical_balance_by_zone.csv"], horizon, scenario, output_directory))
    outputs.extend(_prices(tables["zonal_price_statistics.csv"], horizon, scenario, output_directory))
    outputs.extend(_dispatch_figures(tables["dispatch_8760_by_zone.parquet"], horizon, scenario, output_directory))
    outputs.extend(_topology(network, tables["network_topology_interfaces.csv"], horizon, scenario, output_directory))
    outputs.extend(_net_imports(tables["annual_net_imports_by_zone.csv"], horizon, scenario, output_directory))
    return outputs


def _grouped(
    table: pd.DataFrame,
    index: str,
    columns: str,
    metric: str,
    title: str,
    ylabel: str,
    directory: Path,
    stem: str,
) -> list[str]:
    _style()
    pivot = table.pivot_table(index=index, columns=columns, values=metric, aggfunc="sum", observed=False).fillna(0.0)
    scenario_order = [str(value) for value in reporting_config()["scenario_order"]]
    if columns == "scenario":
        pivot = pivot.reindex(columns=scenario_order)
    if index == "scenario":
        pivot = pivot.reindex(index=scenario_order)
    figure, axis = plt.subplots(figsize=(12, 6.5))
    palette = ["#7A9CC6", "#2F6690", "#E07A5F", "#6A51A3", "#59A14F", "#9C755F"]
    pivot.plot(kind="bar", ax=axis, color=palette[: len(pivot.columns)], width=0.78)
    axis.set_xlabel("")
    axis.set_ylabel(ylabel)
    axis.set_title(title, loc="left", fontweight="semibold")
    axis.tick_params(axis="x", rotation=40)
    axis.legend(title="Scenario", ncol=3, loc="upper left", bbox_to_anchor=(0, -0.16))
    return _save(figure, directory, stem, title)


def render_comparison_figures(
    tables: Mapping[str, pd.DataFrame],
    horizon: int,
    output_directory: Path,
) -> list[str]:
    output_directory.mkdir(parents=True, exist_ok=True)
    outputs: list[str] = []
    outputs.extend(
        _grouped(
            tables["installed_capacity_by_technology_and_scenario.csv"],
            "display_technology",
            "scenario",
            "installed_capacity_GW",
            f"Installed capacity by technology and scenario — {horizon}",
            "GW",
            output_directory,
            "comparison_installed_capacity_by_technology",
        )
    )
    outputs.extend(
        _grouped(
            tables["annual_primary_generation_by_technology_and_scenario.csv"],
            "display_technology",
            "scenario",
            "annual_primary_generation_TWh",
            f"Annual primary generation by technology and scenario — {horizon}",
            "TWh",
            output_directory,
            "comparison_annual_primary_generation_by_technology",
        )
    )
    outputs.extend(
        _grouped(
            tables["annual_electricity_supply_by_technology_and_scenario.csv"],
            "display_technology",
            "scenario",
            "annual_electricity_supply_TWh",
            f"Annual electricity supply by technology and scenario — {horizon}",
            "TWh",
            output_directory,
            "comparison_annual_electricity_supply_by_technology",
        )
    )
    demand = tables["national_electrical_balance_by_scenario.csv"]
    demand_long = demand.melt(
        id_vars="scenario",
        value_vars=[
            "gross_end_use_demand_TWh",
            "primary_generation_TWh",
            "storage_phs_discharge_TWh",
            "storage_phs_charging_TWh",
            "net_imports_TWh",
            "load_shedding_TWh",
        ],
        var_name="metric",
        value_name="TWh",
    )
    outputs.extend(
        _grouped(
            demand_long,
            "scenario",
            "metric",
            "TWh",
            f"National electrical balance by scenario — {horizon}",
            "TWh",
            output_directory,
            "comparison_national_electrical_balance",
        )
    )
    outputs.extend(
        _grouped(
            tables["average_zonal_prices_by_scenario.csv"],
            "zone",
            "scenario",
            "load_weighted_mean_EUR_per_MWh",
            f"Load-weighted zonal prices by scenario — {horizon}",
            "EUR/MWh",
            output_directory,
            "comparison_average_zonal_prices",
        )
    )
    outputs.extend(
        _grouped(
            tables["annual_net_imports_by_scenario.csv"],
            "zone",
            "scenario",
            "annual_net_imports_TWh",
            f"Annual net imports by zone and scenario — {horizon}",
            "TWh (+ imports, − exports)",
            output_directory,
            "comparison_annual_net_imports",
        )
    )
    storage = tables["annual_storage_phs_operation_by_scenario.csv"].melt(
        id_vars="scenario",
        value_vars=["bess_discharge_TWh", "phs_discharge_TWh", "bess_charging_TWh", "phs_charging_TWh"],
        var_name="metric",
        value_name="TWh",
    )
    outputs.extend(
        _grouped(
            storage,
            "scenario",
            "metric",
            "TWh",
            f"BESS and PHS annual charging and discharge — {horizon}",
            "TWh",
            output_directory,
            "comparison_storage_phs_operation",
        )
    )
    return outputs

"""Storage and hydro operation from saved PyPSA results.

Positive ``p`` means discharge/injection for StorageUnit and Store. The
functions preserve separate charge and discharge before aggregation, so
simultaneous operation is not cancelled. State of energy is MWh; power and
StorageUnit inflow are MW. No storage technology is inferred by name.

Lineage: PyPSA-Eur ``scripts/plot_heatmap_timeseries.py:187-205`` uses Store
``e`` divided by ``e_nom_opt`` for a SOC heatmap. This module extends the
extraction to StorageUnits and keeps raw state of energy for comparison.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .io import as_source
from .styles import PlotStyle


def _series(source, component: str, attribute: str) -> pd.DataFrame:
    try:
        result = source.series(component, attribute)
    except (AttributeError, KeyError, ValueError):
        return pd.DataFrame()
    return result if isinstance(result, pd.DataFrame) else pd.DataFrame()


def _component(source, name: str) -> pd.DataFrame:
    try:
        result = source.component(name)
    except (AttributeError, KeyError, ValueError):
        return pd.DataFrame()
    return result if isinstance(result, pd.DataFrame) else pd.DataFrame()


def _group(
    metadata: pd.DataFrame,
    data: pd.DataFrame,
    *,
    buses: Sequence[str] | None,
    carrier_mapping: Mapping[str, str],
) -> pd.DataFrame:
    if metadata.empty or data.empty:
        return pd.DataFrame(index=data.index)
    columns = data.columns.intersection(metadata.index)
    if buses is not None:
        if "bus" not in metadata:
            raise ValueError("bus filtering requires storage bus metadata")
        columns = columns.intersection(metadata.index[metadata.bus.isin(buses)])
    if columns.empty:
        return pd.DataFrame(index=data.index)
    carriers = metadata.reindex(columns).get("carrier", pd.Series("unspecified", index=columns))
    carriers = carriers.fillna("unspecified").astype(str).replace(carrier_mapping)
    return data[columns].T.groupby(carriers, sort=False).sum().T


def storage_operation_table(
    source,
    *,
    buses: Sequence[str] | None = None,
    carrier_mapping: Mapping[str, str] | None = None,
    include_stores: bool = True,
) -> pd.DataFrame:
    """Return wide snapshot x (carrier, metric) storage operation table.

    Metrics: ``charge_mw``, ``discharge_mw``, ``energy_mwh`` and, when
    available, ``inflow_mw``. A Store and StorageUnit with the same mapped
    carrier are added. Tables without network metadata can be supplied via a
    ResultSource implementation exposing the same component/series contract.
    """
    src = as_source(source)
    mapping = carrier_mapping or {}
    components = [("storage_units", "state_of_charge")]
    if include_stores:
        components.append(("stores", "e"))
    grouped: dict[str, pd.DataFrame] = {}
    index = pd.Index([])
    for component, energy_attr in components:
        meta = _component(src, component)
        power = _series(src, component, "p")
        energy = _series(src, component, energy_attr)
        if power.empty and energy.empty:
            continue
        if index.empty:
            index = power.index if not power.empty else energy.index
        if not power.empty:
            grouped[f"{component}:charge_mw"] = _group(meta, power.clip(upper=0).abs(), buses=buses, carrier_mapping=mapping)
            grouped[f"{component}:discharge_mw"] = _group(meta, power.clip(lower=0), buses=buses, carrier_mapping=mapping)
        if not energy.empty:
            grouped[f"{component}:energy_mwh"] = _group(meta, energy, buses=buses, carrier_mapping=mapping)
        if component == "storage_units":
            inflow = _series(src, component, "inflow")
            if not inflow.empty:
                grouped[f"{component}:inflow_mw"] = _group(meta, inflow, buses=buses, carrier_mapping=mapping)
    if index.empty:
        raise ValueError("no StorageUnit or Store operation time series available")
    metrics = ("charge_mw", "discharge_mw", "energy_mwh", "inflow_mw")
    carriers = sorted({carrier for frame in grouped.values() for carrier in frame.columns})
    columns = pd.MultiIndex.from_product([carriers, metrics], names=["carrier", "metric"])
    result = pd.DataFrame(0.0, index=index, columns=columns)
    for key, frame in grouped.items():
        metric = key.split(":", 1)[1]
        frame = frame.reindex(index)
        if frame.isna().any().any():
            raise ValueError(f"{key} does not cover every storage snapshot")
        for carrier in frame:
            result[(carrier, metric)] += frame[carrier]
    result = result.drop(columns=[(c, "inflow_mw") for c in carriers if not any(k.endswith("inflow_mw") and c in frame for k, frame in grouped.items())])
    result.attrs["power_unit"] = "MW"
    result.attrs["energy_unit"] = "MWh"
    return result


def plot_storage_operation(
    source_or_table,
    carrier: str,
    *,
    buses: Sequence[str] | None = None,
    carrier_mapping: Mapping[str, str] | None = None,
    style: PlotStyle | None = None,
    show_inflow: bool = True,
):
    """Plot charge/discharge and state of energy for one explicit carrier."""
    table = (
        source_or_table
        if isinstance(source_or_table, pd.DataFrame)
        else storage_operation_table(source_or_table, buses=buses, carrier_mapping=carrier_mapping)
    )
    if not isinstance(table.columns, pd.MultiIndex) or carrier not in table.columns.get_level_values(0):
        raise ValueError(f"storage carrier unavailable: {carrier}")
    data = table[carrier]
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True, layout="constrained")
    style = style or PlotStyle()
    color = style.color(carrier)
    axes[0].fill_between(table.index, 0, data.get("discharge_mw", pd.Series(0, index=table.index)), color=color, alpha=0.8, label="Discharge")
    axes[0].fill_between(table.index, 0, -data.get("charge_mw", pd.Series(0, index=table.index)), color=color, alpha=0.35, label="Charge")
    if show_inflow and "inflow_mw" in data:
        axes[0].plot(table.index, data.inflow_mw, color="tab:blue", linewidth=1, label="Inflow")
    axes[0].axhline(0, color="0.4", linewidth=0.7)
    axes[0].set_ylabel("Power [MW]")
    axes[0].legend(frameon=False, ncol=3)
    axes[1].plot(table.index, data.get("energy_mwh", pd.Series(0, index=table.index)), color=color, linewidth=1.3)
    axes[1].set_ylabel("State of energy [MWh]")
    axes[1].set_xlabel("Snapshot")
    for ax in axes:
        ax.grid(axis="y", color="0.9")
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle(carrier)
    fig.autofmt_xdate()
    return fig


def plot_hydro_operation(source_or_table, carrier: str, **kwargs):
    """Hydro view with inflow when saved; caller explicitly chooses carrier."""
    return plot_storage_operation(source_or_table, carrier, show_inflow=True, **kwargs)


def storage_summary(
    table: pd.DataFrame,
    *,
    weights: pd.Series | None = None,
    energy_capacity_mwh: Mapping[str, float] | None = None,
) -> pd.DataFrame:
    """Return charge/discharge throughput and SOC range by carrier.

    Pass snapshot-duration weights in hours to obtain MWh throughput. With no
    weights, power is summed across snapshots and marked as MW-snapshots.
    Equivalent full cycles need both hour weights and explicit usable energy
    capacity by carrier; they are discharge MWh / capacity MWh.
    """
    if not isinstance(table.columns, pd.MultiIndex):
        raise ValueError("storage table needs (carrier, metric) columns")
    w = pd.Series(1.0, index=table.index) if weights is None else pd.to_numeric(weights.reindex(table.index), errors="coerce")
    if w.isna().any() or (w < 0).any():
        raise ValueError("weights must be finite and nonnegative for every snapshot")
    rows = []
    capacity = energy_capacity_mwh or {}
    for carrier in table.columns.get_level_values(0).unique():
        data = table[carrier]
        charge = data.get("charge_mw", pd.Series(0.0, index=table.index))
        discharge = data.get("discharge_mw", pd.Series(0.0, index=table.index))
        energy = data.get("energy_mwh", pd.Series(np.nan, index=table.index))
        charge_total = float((charge * w).sum())
        discharge_total = float((discharge * w).sum())
        e_capacity = capacity.get(carrier)
        cycles = discharge_total / e_capacity if weights is not None and e_capacity is not None and e_capacity > 0 else np.nan
        rows.append((carrier, charge_total, discharge_total, float(energy.min()), float(energy.max()), cycles))
    result = pd.DataFrame(rows, columns=["carrier", "charge_throughput", "discharge_throughput", "minimum_energy_mwh", "maximum_energy_mwh", "equivalent_full_cycles"]).set_index("carrier")
    result.attrs["throughput_unit"] = "MWh" if weights is not None else "MW-snapshots"
    return result


def storage_duration_table(
    table: pd.DataFrame,
    carrier: str,
    *,
    metric: str = "discharge_mw",
    weights: pd.Series | None = None,
) -> pd.DataFrame:
    """Return a descending duration table for charge, discharge or SOC.

    ``weights`` should represent snapshot duration in hours to label the
    x-axis as weighted hours. No hourly assumption is made otherwise.
    """
    if (carrier, metric) not in table:
        raise ValueError(f"storage metric unavailable: {(carrier, metric)}")
    w = pd.Series(1.0, index=table.index) if weights is None else pd.to_numeric(weights.reindex(table.index), errors="coerce")
    if w.isna().any() or (w < 0).any():
        raise ValueError("weights must be finite and nonnegative for every snapshot")
    result = pd.DataFrame({"value": table[(carrier, metric)], "weight": w}).dropna()
    result = result.sort_values("value", ascending=False, kind="stable")
    result["x_end"] = result.weight.cumsum()
    result["x"] = result.x_end - result.weight
    result.insert(0, "snapshot", result.index)
    result = result.reset_index(drop=True)
    result.attrs.update(carrier=carrier, metric=metric, x_unit="weighted hours" if weights is not None else "snapshots")
    return result


def plot_storage_duration(
    table: pd.DataFrame,
    carrier: str,
    *,
    metric: str = "discharge_mw",
    weights: pd.Series | None = None,
    ax=None,
):
    """Draw a scenario-neutral storage operation duration curve."""
    duration = storage_duration_table(table, carrier, metric=metric, weights=weights)
    if ax is None:
        fig, ax = plt.subplots(figsize=(8, 3.5), layout="constrained")
    else:
        fig = ax.figure
    if not duration.empty:
        x = np.r_[duration.x.to_numpy(), duration.x_end.iloc[-1]]
        y = np.r_[duration.value.to_numpy(), duration.value.iloc[-1]]
        ax.step(x, y, where="post")
    ax.set_xlabel("Weighted hours" if weights is not None else "Snapshots [count]")
    ax.set_ylabel(f"{metric} [{'MWh' if metric == 'energy_mwh' else 'MW'}]")
    ax.set_title(carrier)
    ax.grid(axis="y", color="0.9")
    ax.spines[["top", "right"]].set_visible(False)
    return fig


def plot_soc_distribution(
    table: pd.DataFrame,
    carrier: str,
    *,
    energy_capacity_mwh: float | None = None,
    weights: pd.Series | None = None,
    bins: int = 30,
    ax=None,
):
    """Draw raw MWh state distribution or percent SOC if capacity is supplied."""
    if (carrier, "energy_mwh") not in table:
        raise ValueError(f"storage energy unavailable for {carrier}")
    if energy_capacity_mwh is not None and energy_capacity_mwh <= 0:
        raise ValueError("energy_capacity_mwh must be positive")
    energy = pd.to_numeric(table[(carrier, "energy_mwh")], errors="coerce").dropna()
    w = pd.Series(1.0, index=table.index) if weights is None else pd.to_numeric(weights.reindex(table.index), errors="coerce")
    if w.isna().any() or (w < 0).any():
        raise ValueError("weights must be finite and nonnegative for every snapshot")
    values = energy if energy_capacity_mwh is None else energy / energy_capacity_mwh * 100
    if ax is None:
        fig, ax = plt.subplots(figsize=(7, 3.5), layout="constrained")
    else:
        fig = ax.figure
    ax.hist(values, bins=bins, weights=w.reindex(energy.index), color="tab:blue", edgecolor="white", linewidth=0.3)
    ax.set_xlabel("State of energy [MWh]" if energy_capacity_mwh is None else "State of charge [%]")
    ax.set_ylabel("Weighted hours" if weights is not None else "Snapshots [count]")
    ax.set_title(carrier)
    ax.grid(axis="y", color="0.9")
    ax.spines[["top", "right"]].set_visible(False)
    return fig

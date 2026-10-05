"""Generic electricity dispatch, residual load and snapshot selection.

Inputs are saved PyPSA networks or ResultSource objects. All power values are
MW; positive values inject power into the selected buses, negative values
withdraw it. External exchanges must be supplied explicitly as signed bus
injections, avoiding any project-specific link or border convention.

Lineage: the signed, separate positive/negative stack and time-axis treatment
follow PyPSA-Eur ``scripts/plot_balance_timeseries.py:25-147`` (MIT). The
extraction here is new and deliberately excludes PyPSA-Eur carrier grouping.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .io import as_source
from .styles import PlotStyle


def _wide(source, component: str, attribute: str) -> pd.DataFrame:
    try:
        result = source.series(component, attribute)
    except (AttributeError, KeyError, ValueError):
        return pd.DataFrame()
    return result if isinstance(result, pd.DataFrame) else pd.DataFrame()


def _static(source, component: str) -> pd.DataFrame:
    try:
        result = source.component(component)
    except (AttributeError, KeyError, ValueError):
        return pd.DataFrame()
    return result if isinstance(result, pd.DataFrame) else pd.DataFrame()


def _selected_columns(static: pd.DataFrame, wide: pd.DataFrame, buses) -> pd.Index:
    columns = wide.columns.intersection(static.index)
    if buses is not None:
        if "bus" not in static:
            raise ValueError("bus filtering requires a bus column in component metadata")
        columns = columns.intersection(static.index[static.bus.isin(buses)])
    return columns


def _aggregate_by_carrier(
    static: pd.DataFrame,
    wide: pd.DataFrame,
    buses,
    carrier_mapping: Mapping[str, str],
) -> pd.DataFrame:
    if static.empty or wide.empty:
        return pd.DataFrame(index=wide.index)
    columns = _selected_columns(static, wide, buses)
    if columns.empty:
        return pd.DataFrame(index=wide.index)
    carriers = static.reindex(columns).get("carrier", pd.Series("unspecified", index=columns))
    carriers = carriers.fillna("unspecified").astype(str).replace(carrier_mapping)
    return wide[columns].T.groupby(carriers, sort=False).sum().T


def dispatch_table(
    source,
    *,
    buses: Sequence[str] | None = None,
    carrier_mapping: Mapping[str, str] | None = None,
    exchange: pd.Series | pd.DataFrame | None = None,
    require_demand: bool = True,
) -> pd.DataFrame:
    """Return signed MW by carrier, with demand as a negative ``demand`` column.

    ``exchange`` is a signed MW Series or a table of signed injections at the
    selected market perimeter. It is never inferred from Link names or p0.
    Positive StorageUnit/Store p is discharge; negative p is charging.
    """
    src = as_source(source)
    mapping = carrier_mapping or {}
    generators = _aggregate_by_carrier(
        _static(src, "generators"), _wide(src, "generators", "p"), buses, mapping
    )
    index = generators.index
    for component in ("storage_units", "stores", "loads"):
        series = _wide(src, component, "p")
        if index.empty and not series.empty:
            index = series.index
    if index.empty:
        raise ValueError("No dispatch time series are available")
    result = generators.reindex(index).fillna(0.0)
    for component in ("storage_units", "stores"):
        part = _aggregate_by_carrier(
            _static(src, component), _wide(src, component, "p"), buses, mapping
        ).reindex(index).fillna(0.0)
        result = result.add(part, fill_value=0.0)
    loads = _wide(src, "loads", "p")
    load_static = _static(src, "loads")
    if loads.empty:
        loads = _wide(src, "loads", "p_set")
    if loads.empty and not load_static.empty and "p_set" in load_static:
        loads = pd.DataFrame(
            np.broadcast_to(load_static.p_set.to_numpy(dtype=float), (len(index), len(load_static))),
            index=index,
            columns=load_static.index,
        )
    if not loads.empty and not load_static.empty:
        cols = _selected_columns(load_static, loads, buses)
        selected_load = loads.reindex(index)[cols]
        if selected_load.isna().any().any():
            raise ValueError("load p/p_set does not cover every selected snapshot")
        result["demand"] = -selected_load.sum(axis=1)
    elif require_demand:
        raise ValueError("demand requested but Loads p, p_set and static p_set are unavailable")
    if exchange is not None:
        exchange_df = exchange.to_frame("exchange") if isinstance(exchange, pd.Series) else exchange
        if not isinstance(exchange_df, pd.DataFrame):
            raise TypeError("exchange must be a Series or DataFrame")
        if not exchange_df.index.equals(index):
            exchange_df = exchange_df.reindex(index)
        if exchange_df.isna().any().any():
            raise ValueError("exchange does not cover every dispatch snapshot")
        result = result.add(exchange_df, fill_value=0.0)
    result = result.reindex(sorted(result.columns), axis=1)
    result.attrs["unit"] = "MW"
    result.attrs["sign"] = "positive injection; negative withdrawal"
    return result


def plot_stacked_dispatch(
    source_or_table,
    *,
    buses: Sequence[str] | None = None,
    carrier_mapping: Mapping[str, str] | None = None,
    exchange: pd.Series | pd.DataFrame | None = None,
    carrier_order: Sequence[str] | None = None,
    style: PlotStyle | None = None,
    show_demand_line: bool = True,
    ax=None,
):
    """Draw positive and negative signed dispatch stacks; return the Figure."""
    table = (
        source_or_table
        if isinstance(source_or_table, pd.DataFrame)
        else dispatch_table(
            source_or_table,
            buses=buses,
            carrier_mapping=carrier_mapping,
            exchange=exchange,
        )
    )
    if table.empty:
        raise ValueError("dispatch table is empty")
    columns = list(carrier_order or []) + sorted(set(table.columns) - set(carrier_order or []))
    columns = [c for c in columns if c in table]
    table = table[columns].fillna(0.0)
    style = style or PlotStyle()
    if ax is None:
        fig, ax = plt.subplots(figsize=(11, 4.5), layout="constrained")
    else:
        fig = ax.figure
    x = table.index
    legend = {}
    for positive in (True, False):
        base = np.zeros(len(table), dtype=float)
        for carrier in table.columns:
            values = table[carrier].clip(lower=0 if positive else None, upper=None if positive else 0).to_numpy(dtype=float)
            if not np.any(values):
                continue
            color = style.color(carrier)
            artist = ax.fill_between(x, base, base + values, step="post", color=color, linewidth=0)
            legend.setdefault(carrier, artist)
            base += values
    demand_artist = None
    if show_demand_line and "demand" in table:
        demand_artist, = ax.plot(x, -table["demand"], color="black", linewidth=1.2, label="Demand")
    ax.axhline(0, color="0.4", linewidth=0.7)
    ax.set_ylabel("Power [MW]")
    ax.set_xlabel("Snapshot")
    ax.grid(axis="y", color="0.9", linewidth=0.6)
    ax.spines[["top", "right"]].set_visible(False)
    handles = list(legend.values())
    labels = list(legend)
    if demand_artist is not None:
        handles.append(demand_artist)
        labels.append("Demand")
    if handles:
        ax.legend(handles, labels, loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False)
    fig.autofmt_xdate()
    return fig


def residual_load_table(
    source,
    *,
    vre_carriers: Sequence[str],
    buses: Sequence[str] | None = None,
    carrier_mapping: Mapping[str, str] | None = None,
    vre_basis: str = "dispatched",
) -> pd.DataFrame:
    """Return demand, VRE and residual load MW.

    ``vre_basis='available'`` requires generator p_max_pu and p_nom_opt/p_nom.
    The explicit VRE set prevents project-specific carrier-name inference.
    """
    if not vre_carriers:
        raise ValueError("vre_carriers must be supplied explicitly")
    if vre_basis not in {"dispatched", "available"}:
        raise ValueError("vre_basis must be 'dispatched' or 'available'")
    src = as_source(source)
    mapping = carrier_mapping or {}
    dispatch = dispatch_table(src, buses=buses, carrier_mapping=mapping)
    demand = -dispatch.get("demand", pd.Series(0.0, index=dispatch.index))
    if vre_basis == "dispatched":
        vre = dispatch.reindex(columns=list(vre_carriers), fill_value=0).sum(axis=1)
    else:
        static = _static(src, "generators")
        pmax = _wide(src, "generators", "p_max_pu")
        if static.empty or pmax.empty:
            raise ValueError("available VRE requires generator metadata and p_max_pu")
        cols = _selected_columns(static, pmax, buses)
        carrier = static.reindex(cols).carrier.astype(str).replace(mapping)
        cols = cols[carrier.isin(vre_carriers)]
        capacity = static.reindex(cols).get("p_nom_opt")
        fallback = static.reindex(cols).get("p_nom")
        if capacity is None:
            capacity = fallback
        elif fallback is not None:
            capacity = capacity.fillna(fallback)
        if capacity is None or capacity.isna().any():
            raise ValueError("available VRE requires p_nom_opt or p_nom")
        vre = pmax.reindex(dispatch.index)[cols].mul(capacity, axis=1).sum(axis=1)
    result = pd.DataFrame({"demand_mw": demand, "vre_mw": vre})
    result["residual_load_mw"] = result.demand_mw - result.vre_mw
    result.attrs["vre_basis"] = vre_basis
    result.attrs["vre_carriers"] = list(vre_carriers)
    return result


def plot_residual_load(table: pd.DataFrame, *, ax=None):
    """Plot the explicit demand minus VRE calculation; return the Figure."""
    required = {"demand_mw", "vre_mw", "residual_load_mw"}
    if not required.issubset(table):
        raise ValueError(f"residual-load table needs {sorted(required)}")
    if ax is None:
        fig, ax = plt.subplots(figsize=(10, 4), layout="constrained")
    else:
        fig = ax.figure
    ax.plot(table.index, table.demand_mw, color="0.3", linewidth=1, label="Demand")
    ax.plot(table.index, table.vre_mw, color="tab:green", linewidth=1, label=f"VRE ({table.attrs.get('vre_basis', 'specified')})")
    ax.plot(table.index, table.residual_load_mw, color="tab:blue", linewidth=1.5, label="Residual load")
    ax.axhline(0, color="0.5", linewidth=0.7)
    ax.set_ylabel("Power [MW]")
    ax.grid(axis="y", color="0.9")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False)
    fig.autofmt_xdate()
    return fig


def select_interesting_hours(
    source,
    *,
    vre_carriers: Sequence[str] = (),
    carrier_mapping: Mapping[str, str] | None = None,
    prices: pd.Series | None = None,
    exchange: pd.Series | None = None,
    utilization: pd.DataFrame | None = None,
    congestion_threshold: float = 0.9,
    shedding_generator_ids: Sequence[str] = (),
) -> pd.DataFrame:
    """Select reproducible extremes, first snapshot wins ties.

    Prices, signed exchange, and utilization must already have unambiguous
    market/perimeter and branch-capacity semantics. System stress uses branch
    count, summed threshold excess, maximum utilization, absolute p0 exposure,
    then source snapshot order. Persist the full metric table separately with
    ``congestion.compute_system_stress_table``.
    Missing metrics are omitted.
    The output records each reason, metric and unit, including duplicate hours.
    """
    src = as_source(source)
    table = dispatch_table(src, carrier_mapping=carrier_mapping)
    rows = []

    def add(reason: str, metric: str, series: pd.Series, mode: str, unit: str):
        s = pd.to_numeric(series, errors="coerce").dropna()
        if s.empty:
            return
        extreme = s.max() if mode == "max" else s.min()
        snapshot = s.index[s.eq(extreme)][0]
        rows.append((reason, snapshot, metric, float(extreme), unit))

    if "demand" in table:
        add("system_peak", "demand", -table.demand, "max", "MW")
    if vre_carriers:
        vre = table.reindex(columns=list(vre_carriers), fill_value=0).sum(axis=1)
        add("renewable_peak", "vre_dispatch", vre, "max", "MW")
        add("renewable_minimum", "vre_dispatch", vre, "min", "MW")
    if prices is not None:
        add("highest_price", "price", prices, "max", "currency/MWh")
    if exchange is not None:
        add("maximum_import", "signed_exchange", exchange, "max", "MW")
        add("maximum_export", "signed_exchange", exchange, "min", "MW")
    if utilization is not None and not utilization.empty:
        from .congestion import compute_system_stress_table
        util = utilization.reindex(table.index)
        if util.notna().any().any():
            score = compute_system_stress_table(src, threshold=congestion_threshold, utilization=util)
            selected = score.index[score.stress_rank.eq(1)][0]
            rows.append(("most_stressed_snapshot", selected,
                         f"branches_ge_{congestion_threshold:.2f}pu",
                         float(score.loc[selected, "branches_at_or_above_threshold"]), "branches"))
    storage = _wide(src, "storage_units", "p")
    if not storage.empty:
        add("maximum_storage_discharge", "storage_discharge", storage.clip(lower=0).sum(axis=1), "max", "MW")
    if shedding_generator_ids:
        generation = _wide(src, "generators", "p")
        missing = sorted(set(shedding_generator_ids) - set(generation.columns))
        if missing:
            raise ValueError(f"shedding generator IDs absent from dispatch: {missing[:8]}")
        shedding = generation[list(shedding_generator_ids)].clip(lower=0).sum(axis=1)
        add("maximum_shedding", "shedding", shedding, "max", "MW")
    return pd.DataFrame(rows, columns=["reason", "snapshot", "metric", "value", "unit"])

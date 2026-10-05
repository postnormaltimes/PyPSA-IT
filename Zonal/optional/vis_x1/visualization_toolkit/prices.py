"""Market price extraction and presentation without market-name assumptions.

Lineage: the day/hour heatmap layout is adapted from PyPSA-Eur
``scripts/plot_heatmap_timeseries.py:22-85`` (MIT). Market aggregation,
duration curves and distributions are new. Native bus prices come from
``buses_t.marginal_price``; they are not interpreted as settlement prices.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .io import as_source


def _market_buses(mapping: Mapping, available: pd.Index) -> dict[str, list[str]]:
    """Accept explicit bus->market or market->bus-list mapping."""
    if not mapping:
        raise ValueError("market_mapping is required")
    if all(isinstance(value, str) for value in mapping.values()):
        result: dict[str, list[str]] = {}
        for bus, market in mapping.items():
            result.setdefault(str(market), []).append(str(bus))
    else:
        result = {str(market): list(buses) for market, buses in mapping.items()}
    missing = sorted({bus for buses in result.values() for bus in buses} - set(available))
    if missing:
        raise ValueError(f"market mapping refers to absent price buses: {missing[:8]}")
    if not result or any(not buses for buses in result.values()):
        raise ValueError("each market needs at least one bus")
    return result


def market_price_table(
    source,
    *,
    market_mapping: Mapping,
    aggregation: str = "mean",
) -> pd.DataFrame:
    """Return snapshot x market marginal prices in network currency/MWh.

    ``mean`` is the unweighted mean of mapped bus prices per snapshot.
    ``load_weighted`` uses mapped Load ``p`` at each bus and snapshot; zero
    demand yields NaN rather than inventing a price. Time weighting is separate.
    """
    if aggregation not in {"mean", "load_weighted"}:
        raise ValueError("aggregation must be 'mean' or 'load_weighted'")
    src = as_source(source)
    prices = src.series("buses", "marginal_price")
    if prices.empty:
        raise ValueError("bus marginal prices are unavailable")
    markets = _market_buses(market_mapping, prices.columns)
    if aggregation == "mean":
        result = pd.DataFrame({market: prices[buses].mean(axis=1) for market, buses in markets.items()})
    else:
        loads = src.component("loads")
        load_p = src.series("loads", "p")
        if loads.empty or load_p.empty or "bus" not in loads:
            raise ValueError("load_weighted prices require Loads metadata and p time series")
        load_p = load_p.reindex(prices.index)
        result = pd.DataFrame(index=prices.index)
        for market, buses in markets.items():
            cols = load_p.columns.intersection(loads.index[loads.bus.isin(buses)])
            demand_by_bus = load_p[cols].T.groupby(loads.reindex(cols).bus, sort=False).sum().T
            denominator = demand_by_bus.sum(axis=1).replace(0, np.nan)
            result[market] = prices[demand_by_bus.columns].mul(demand_by_bus).sum(axis=1) / denominator
    result.attrs.update(unit="currency/MWh", aggregation=aggregation, market_mapping={k: list(v) for k, v in markets.items()})
    return result


def _weights(weights: pd.Series | None, index: pd.Index) -> pd.Series:
    if weights is None:
        return pd.Series(1.0, index=index)
    result = pd.to_numeric(weights.reindex(index), errors="coerce")
    if result.isna().any() or (result < 0).any():
        raise ValueError("weights must be finite and nonnegative for every snapshot")
    return result


def price_summary(prices: pd.DataFrame, *, weights: pd.Series | None = None) -> pd.DataFrame:
    """Return mean, median, minimum, maximum and valid count per market.

    Mean is time-weighted when weights are supplied; median remains the
    unweighted snapshot median and is labelled accordingly.
    """
    w = _weights(weights, prices.index)
    rows = []
    for market in prices.columns:
        s = pd.to_numeric(prices[market], errors="coerce").dropna()
        valid_w = w.reindex(s.index)
        mean = float((s * valid_w).sum() / valid_w.sum()) if valid_w.sum() else np.nan
        rows.append((market, mean, float(s.median()) if not s.empty else np.nan, float(s.min()) if not s.empty else np.nan, float(s.max()) if not s.empty else np.nan, len(s)))
    frame = pd.DataFrame(rows, columns=["market", "mean", "snapshot_median", "min", "max", "valid_snapshots"]).set_index("market")
    frame.attrs["mean_weighting"] = "provided weights" if weights is not None else "equal snapshots"
    return frame


def plot_price_timeseries(prices: pd.DataFrame, *, markets: Sequence[str] | None = None, ax=None):
    """Draw selected market price traces; return the Figure."""
    markets = list(markets or prices.columns)
    if not set(markets).issubset(prices):
        raise ValueError("unknown markets requested")
    if ax is None:
        fig, ax = plt.subplots(figsize=(10, 4), layout="constrained")
    else:
        fig = ax.figure
    for market in markets:
        ax.plot(prices.index, prices[market], linewidth=1, label=market)
    ax.set_ylabel("Marginal price [network currency/MWh]")
    ax.set_xlabel("Snapshot")
    ax.grid(axis="y", color="0.9")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(1.01, 1))
    fig.autofmt_xdate()
    return fig


def price_duration_table(prices: pd.DataFrame, *, weights: pd.Series | None = None) -> pd.DataFrame:
    """Return sorted price observations with cumulative snapshot/weight x-axis."""
    w = _weights(weights, prices.index)
    pieces = []
    for market in prices:
        frame = pd.DataFrame({"price": pd.to_numeric(prices[market], errors="coerce"), "weight": w}).dropna()
        frame = frame.sort_values("price", ascending=False, kind="stable")
        frame["x_end"] = frame.weight.cumsum()
        frame["x"] = frame.x_end - frame.weight
        frame.insert(0, "market", market)
        frame.insert(1, "snapshot", frame.index)
        pieces.append(frame.reset_index(drop=True))
    result = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame(columns=["market", "snapshot", "price", "weight", "x", "x_end"])
    result.attrs["x_unit"] = "weighted hours" if weights is not None else "snapshots"
    return result


def plot_price_duration(
    prices: pd.DataFrame,
    *,
    weights: pd.Series | None = None,
    weight_unit: str = "weighted hours",
    ax=None,
):
    """Draw price duration curves. Pass hour weights for an hours x-axis."""
    table = price_duration_table(prices, weights=weights)
    if ax is None:
        fig, ax = plt.subplots(figsize=(9, 4), layout="constrained")
    else:
        fig = ax.figure
    for market, group in table.groupby("market", sort=False):
        x = np.r_[group.x.to_numpy(), group.x_end.iloc[-1]]
        y = np.r_[group.price.to_numpy(), group.price.iloc[-1]]
        ax.step(x, y, where="post", label=market)
    ax.set_xlabel(weight_unit if weights is not None else "Snapshots [count]")
    ax.set_ylabel("Marginal price [network currency/MWh]")
    ax.grid(axis="both", color="0.9")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(1.01, 1))
    return fig


def plot_price_distribution(
    prices: pd.DataFrame,
    *,
    weights: pd.Series | None = None,
    bins: int | Sequence[float] = 40,
    ax=None,
):
    """Draw aligned-bin price histograms, weighted if explicitly supplied."""
    w = _weights(weights, prices.index)
    if ax is None:
        fig, ax = plt.subplots(figsize=(9, 4), layout="constrained")
    else:
        fig = ax.figure
    values = prices.apply(pd.to_numeric, errors="coerce").to_numpy().ravel()
    values = values[np.isfinite(values)]
    if len(values) == 0:
        raise ValueError("no finite prices")
    edges = np.histogram_bin_edges(values, bins=bins)
    for market in prices:
        s = pd.to_numeric(prices[market], errors="coerce").dropna()
        ax.hist(s, bins=edges, weights=w.reindex(s.index), histtype="step", linewidth=1.5, label=market)
    ax.set_xlabel("Marginal price [network currency/MWh]")
    ax.set_ylabel("Weighted hours" if weights is not None else "Snapshots [count]")
    ax.grid(axis="y", color="0.9")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False)
    return fig


def price_day_hour_table(prices: pd.DataFrame, market: str) -> pd.DataFrame:
    """Return an hour x date price grid without filling unmodelled hours."""
    if market not in prices:
        raise ValueError(f"unknown market: {market}")
    if not isinstance(prices.index, pd.DatetimeIndex):
        raise TypeError("day/hour heatmap requires a DatetimeIndex")
    s = prices[market]
    table = pd.DataFrame({"date": s.index.normalize(), "hour": s.index.hour, "price": s.to_numpy()})
    if table.duplicated(["date", "hour"]).any():
        raise ValueError("multiple snapshots share a date/hour; choose a timezone convention first")
    return table.pivot(index="hour", columns="date", values="price").reindex(range(24))


def plot_price_heatmap(prices: pd.DataFrame, market: str, *, cmap: str = "RdYlBu_r", ax=None):
    """Draw a day/hour price heatmap; absent snapshots remain blank."""
    grid = price_day_hour_table(prices, market)
    if ax is None:
        fig, ax = plt.subplots(figsize=(10, 4), layout="constrained")
    else:
        fig = ax.figure
    array = np.ma.masked_invalid(grid.to_numpy(dtype=float))
    artist = ax.imshow(array, origin="lower", aspect="auto", interpolation="none", cmap=cmap)
    ax.set_yticks(range(0, 24, 3))
    ax.set_ylabel("Hour of day")
    if len(grid.columns) <= 14:
        positions = list(range(len(grid.columns)))
    else:
        positions = sorted(set(np.linspace(0, len(grid.columns) - 1, min(9, len(grid.columns)), dtype=int)))
    ax.set_xticks(positions, [grid.columns[i].strftime("%d %b") for i in positions], rotation=0)
    ax.set_xlabel("Date")
    ax.set_title(market)
    fig.colorbar(artist, ax=ax, label="Marginal price [network currency/MWh]")
    return fig

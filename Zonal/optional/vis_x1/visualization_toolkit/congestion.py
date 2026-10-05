"""Branch loading, congestion frequency and stress plots.

These are diagnostic measures derived from saved dispatch. Threshold exceedance
is not proof that a network constraint was binding. AC active-only results are
labelled as proxies unless q0 is available for apparent-power loading.
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
from matplotlib import colors as mcolors
from matplotlib.cm import ScalarMappable
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd

from .io import as_source
from .styles import PlotStyle
from .network_maps import BRANCHES, _axes, _coordinates, _rating, _segments, _series, _style, _styled_plot, _table


def _limit_factor(source: Any, component: str, index: pd.Index, snapshots: pd.Index) -> pd.DataFrame:
    """AC snapshot limit multiplier, with static value when no dynamic value exists."""
    static = _table(source, component)
    base = pd.to_numeric(static.get("s_max_pu", pd.Series(1.0, index=index)), errors="coerce")
    base = base.reindex(index).fillna(1.0)
    factor = pd.DataFrame(np.broadcast_to(base.to_numpy(), (len(snapshots), len(index))).copy(),
                          index=snapshots, columns=index)
    dynamic = _series(source, component, "s_max_pu")
    if not dynamic.empty:
        dynamic = dynamic.reindex(index=snapshots, columns=index).apply(pd.to_numeric, errors="coerce")
        factor.update(dynamic)
    return factor.where(factor > 0)


def compute_loading_timeseries(source: Any, *, components: tuple[str, ...] = BRANCHES,
                               use_dynamic_line_limits: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(utilization, branch_metadata)`` from saved p0/q0 time series.

    Utilization columns are a two-level ``(component, branch_id)`` index.
    Lines/transformers use sqrt(p0²+q0²)/(s_nom*s_max_pu) where q0 exists,
    otherwise |p0|/(s_nom*s_max_pu), explicitly marked active-power proxy.
    Links use |p0|/p_nom and do not imply a binding dispatch constraint.
    Nonpositive or absent nameplates remain NaN rather than being invented.
    """
    src = as_source(source)
    frames: list[pd.DataFrame] = []
    metadata = []
    for component in components:
        static = _table(src, component)
        p0 = _series(src, component, "p0")
        if static.empty or p0.empty:
            continue
        columns = static.index.intersection(p0.columns)
        if len(columns) == 0:
            continue
        p0 = p0.reindex(columns=columns).apply(pd.to_numeric, errors="coerce")
        rating = _rating(static.loc[columns], component)
        numerator = p0.abs()
        bases = pd.Series("active_power_proxy", index=columns)
        if component in {"lines", "transformers"}:
            q0 = _series(src, component, "q0")
            if not q0.empty:
                q0 = q0.reindex(index=p0.index, columns=columns).apply(pd.to_numeric, errors="coerce")
                has_q = q0.notna().any(axis=0)
                if has_q.any():
                    numerator.loc[:, has_q] = np.hypot(p0.loc[:, has_q], q0.loc[:, has_q])
                    bases.loc[has_q] = "apparent_power"
            factor = _limit_factor(src, component, columns, p0.index) if use_dynamic_line_limits else 1.0
            denominator = factor.mul(rating, axis=1) if isinstance(factor, pd.DataFrame) else rating
            unit = "MVA"
        else:
            bases[:] = "link_active_power"
            denominator = rating
            unit = "MW"
        loading = numerator.div(denominator).replace([np.inf, -np.inf], np.nan)
        loading.columns = pd.MultiIndex.from_tuples(
            [(component, str(c)) for c in columns], names=["component", "branch_id"]
        )
        frames.append(loading)
        for branch in columns:
            item = static.loc[branch]
            metadata.append({"component": component, "branch_id": str(branch),
                             "bus0": item.get("bus0"), "bus1": item.get("bus1"),
                             "rating": rating.loc[branch], "rating_unit": unit,
                             "loading_basis": bases.loc[branch]})
    if not frames:
        raise ValueError("No branch p0 time series with static branch tables found")
    utilization = pd.concat(frames, axis=1)
    meta = pd.DataFrame(metadata).set_index(["component", "branch_id"])
    return utilization, meta


def _weights(source: Any, snapshots: pd.Index) -> pd.Series:
    src = as_source(source)
    try:
        weights = src.weights("generators")
    except (AttributeError, KeyError, ValueError):
        weights = pd.Series(1.0, index=snapshots)
    weights = pd.to_numeric(weights, errors="coerce").reindex(snapshots)
    if weights.isna().any() or (weights < 0).any():
        raise ValueError("Snapshot weights must be complete and nonnegative")
    return weights


def compute_congestion_metrics(source: Any, *, threshold: float = .9,
                               components: tuple[str, ...] = BRANCHES,
                               use_dynamic_line_limits: bool = True) -> pd.DataFrame:
    """Rank branches by weighted hours at or above a utilization threshold.

    Weights are ``ResultSource.weights('generators')`` and are assumed to
    represent hours; the returned field also states the denominator explicitly.
    Flow direction share excludes zero-flow snapshots. Missing values are not
    counted as uncongested observations.
    """
    if threshold <= 0:
        raise ValueError("threshold must be positive")
    src = as_source(source)
    utilization, meta = compute_loading_timeseries(
        src, components=components, use_dynamic_line_limits=use_dynamic_line_limits
    )
    weights = _weights(src, utilization.index)
    valid = utilization.notna()
    weighted = utilization.mul(weights, axis=0)
    weight_denominator = valid.mul(weights, axis=0).sum(axis=0)
    result = meta.copy()
    result["threshold"] = threshold
    result["hours_at_or_above_threshold"] = (utilization.ge(threshold) & valid).mul(weights, axis=0).sum(axis=0)
    result["observed_weighted_hours"] = weight_denominator
    result["max_utilization"] = utilization.max(axis=0)
    result["mean_utilization"] = weighted.sum(axis=0).div(weight_denominator.where(weight_denominator > 0))
    result["observed_snapshots"] = valid.sum(axis=0)
    result["flow_direction_positive_share"] = np.nan
    for component in components:
        p0 = _series(src, component, "p0")
        if p0.empty:
            continue
        for branch in p0.columns:
            key = (component, str(branch))
            if key not in result.index:
                continue
            flow = pd.to_numeric(p0[branch], errors="coerce").reindex(utilization.index)
            nonzero = flow.notna() & flow.abs().gt(1e-9)
            denominator = weights.loc[nonzero].sum()
            if denominator > 0:
                result.loc[key, "flow_direction_positive_share"] = float(weights.loc[nonzero & flow.gt(0)].sum() / denominator)
    return result.sort_values(["hours_at_or_above_threshold", "max_utilization"], ascending=False)


def compute_system_stress_table(source: Any, *, threshold: float = .9,
                                utilization: pd.DataFrame | None = None) -> pd.DataFrame:
    """Rank saved snapshots by system branch stress, without optimizing.

    Lexicographic priority: number of AC/Link branches at or above threshold,
    summed excess utilization above threshold, maximum branch utilization,
    total absolute bus0 active-flow exposure (MW), then source snapshot order.
    Missing utilization is excluded, not interpreted as zero loading. The
    flow-exposure measure is a deterministic tie-break, not energy or welfare.
    """
    if not 0 < threshold <= 1:
        raise ValueError("threshold must be in (0, 1]")
    src = as_source(source)
    util = compute_loading_timeseries(src)[0] if utilization is None else utilization
    util = util.apply(pd.to_numeric, errors="coerce")
    exposure = pd.Series(0.0, index=util.index)
    for component in BRANCHES:
        p0 = _series(src, component, "p0")
        if not p0.empty:
            exposure = exposure.add(p0.reindex(util.index).apply(pd.to_numeric, errors="coerce").abs().sum(axis=1), fill_value=0)
    result = pd.DataFrame(index=util.index)
    result.index.name = "snapshot"
    result["branches_at_or_above_threshold"] = util.ge(threshold).sum(axis=1).astype(int)
    result["summed_excess_pu"] = util.sub(threshold).clip(lower=0).sum(axis=1)
    result["maximum_utilization_pu"] = util.max(axis=1)
    result["absolute_p0_exposure_mw"] = exposure
    result["observed_branches"] = util.notna().sum(axis=1).astype(int)
    result["source_order"] = np.arange(len(result))
    result["threshold_pu"] = threshold
    result["stress_rank"] = (result.sort_values(
        ["branches_at_or_above_threshold", "summed_excess_pu", "maximum_utilization_pu",
         "absolute_p0_exposure_mw", "source_order"],
        ascending=[False, False, False, False, True]).assign(_rank=lambda x: np.arange(1, len(x) + 1))
        ["_rank"].reindex(result.index))
    return result


@_styled_plot
def plot_congestion_map(source: Any, *, threshold: float = .9,
                        metrics: pd.DataFrame | None = None, style: PlotStyle | None = None,
                        title: str | None = None, labels_top_n: int = 0,
                        geography=None, extent=None,
                        figsize: tuple[float, float] = (11, 8)):
    """Map weighted threshold hours for lines, links and transformers.

    Returns ``(figure, metrics)``; labels make clear that the map shows
    observed loading frequency, not proven binding constraints.
    """
    src = as_source(source)
    style = _style(style)
    xy = _coordinates(src)
    metrics = compute_congestion_metrics(src, threshold=threshold) if metrics is None else metrics
    if metrics.empty:
        raise ValueError("No congestion metrics")
    fig, ax = _axes(xy, title=title or f"Branch loading ≥ {threshold:.0%}", figsize=figsize,
                    geography=geography, extent=extent)
    geometry_rows = []
    for (component, branch_id), metric in metrics.iterrows():
        geometry_rows.append({"component": component, "branch_id": branch_id,
                              "bus0": metric.bus0, "bus1": metric.bus1,
                              "hours": metric.hours_at_or_above_threshold})
    geometry = pd.DataFrame(geometry_rows)
    segments, geometry = _segments(geometry, xy)
    values = pd.to_numeric(geometry.hours, errors="coerce").fillna(0).to_numpy()
    cmap = plt.get_cmap(style.line_style.get("congestion_cmap", "magma"))
    vmax = max(float(values.max()) if len(values) else 0, 1.0)
    norm = mcolors.Normalize(vmin=0, vmax=vmax)
    line_styles = ["--" if c == "links" else "-" for c in geometry.component]
    ax.add_collection(LineCollection(segments, colors="#59666d", linestyles=line_styles,
                                     linewidths=np.where(values > 0, 3.2, 1.2), zorder=1.8))
    ax.add_collection(LineCollection(segments, colors=cmap(norm(values)),
                                     linestyles=line_styles,
                                     linewidths=np.where(values > 0, 2.2, .8), zorder=2))
    ax.scatter(xy.x, xy.y, color=style.map_style.get("bus_color", "#23364b"),
               s=12, edgecolor="white", linewidth=.3, zorder=3)
    if labels_top_n:
        top = geometry.sort_values("hours", ascending=False).head(labels_top_n)
        for _, row in top.iterrows():
            a, b = xy.loc[row.bus0], xy.loc[row.bus1]
            ax.annotate(str(row.branch_id), ((a.x+b.x)/2, (a.y+b.y)/2),
                        fontsize=7, color="#172334")
    fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=ax, shrink=.6,
                 label="Weighted hours at/above threshold")
    ax.legend(handles=[Line2D([], [], color="#55616b", linewidth=2, label="AC / transformer"),
                       Line2D([], [], color="#55616b", linewidth=2, linestyle="--", label="Link / HVDC")],
              loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False, fontsize=8)
    fig.text(.03, .025, "Loading threshold is a diagnostic; binding status needs constraint evidence.",
             fontsize=8, color="#46576b")
    return fig, metrics


@_styled_plot
def plot_congestion_ranking(source: Any, *, threshold: float = .9, top_n: int = 20,
                            metrics: pd.DataFrame | None = None,
                            style: PlotStyle | None = None,
                            figsize: tuple[float, float] = (9, 7)):
    """Rank top stressed branches by weighted threshold hours."""
    metrics = compute_congestion_metrics(source, threshold=threshold) if metrics is None else metrics
    if top_n < 1:
        raise ValueError("top_n must be positive")
    shown = metrics.head(top_n).iloc[::-1]
    labels = [f"{comp}: {branch}" for comp, branch in shown.index]
    fig, ax = plt.subplots(figsize=figsize, layout="constrained")
    fig.patch.set_facecolor("white")
    ax.barh(labels, shown.hours_at_or_above_threshold, color="#436b8a")
    ax.set_xlabel("Weighted hours at/above threshold")
    ax.set_title(f"Most loaded branches (≥ {threshold:.0%})", loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", alpha=.18)
    ax.set_axisbelow(True)
    return fig, shown.iloc[::-1]


@_styled_plot
def plot_utilization_duration(source: Any, *, threshold: float = .9, top_n: int = 8,
                              components: tuple[str, ...] = BRANCHES,
                              style: PlotStyle | None = None,
                              figsize: tuple[float, float] = (10, 6)):
    """Plot branch utilization sorted by descending value against weighted hours."""
    src = as_source(source)
    utilization, _ = compute_loading_timeseries(src, components=components)
    metrics = compute_congestion_metrics(src, threshold=threshold, components=components)
    weights = _weights(src, utilization.index)
    fig, ax = plt.subplots(figsize=figsize, layout="constrained")
    fig.patch.set_facecolor("white")
    for key in metrics.head(top_n).index:
        values = pd.DataFrame({"utilization": utilization[key], "weight": weights}).dropna()
        values = values.sort_values("utilization", ascending=False)
        if values.empty:
            continue
        ax.step(values.weight.cumsum(), values.utilization, where="post",
                label=f"{key[0]}: {key[1]}", linewidth=1.5)
    ax.axhline(threshold, color="#b44236", linestyle="--", linewidth=1,
               label=f"{threshold:.0%} threshold")
    ax.set_xlabel("Cumulative weighted hours")
    ax.set_ylabel("Utilization of nominal / available rating")
    ax.set_title("Branch utilization duration", loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(alpha=.18)
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False, fontsize=8)
    return fig, metrics.head(top_n)

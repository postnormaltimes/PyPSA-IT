"""Geographic plots from a solved PyPSA network or equivalent result tables.

Provenance: uses the installed PyPSA 1.2.3 ``Network.plot`` for the base map;
all other extraction and presentation in this module is new. No model code is
imported. Branch flow sign is PyPSA p0: positive bus0 -> bus1.
"""

from __future__ import annotations

import importlib.util
from functools import wraps
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
from matplotlib import colors as mcolors
from matplotlib.cm import ScalarMappable
from matplotlib.collections import LineCollection
from matplotlib.patches import FancyArrowPatch, Wedge
from matplotlib.lines import Line2D
from matplotlib.patheffects import withStroke
import numpy as np
import pandas as pd

from .io import as_source
from .styles import PlotStyle, style_context


BRANCHES = ("lines", "links", "transformers")


def bundled_geography_path() -> Path:
    """Return the packaged Natural Earth context layer, independent of cwd."""
    path = Path(__file__).resolve().parent / "data" / "naturalearth_lowres.geojson"
    if not path.is_file():
        raise FileNotFoundError(f"Bundled geography is missing: {path}")
    return path


def _styled_plot(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        with style_context(kwargs.get("style")):
            return func(*args, **kwargs)
    return wrapper


def _style(style: PlotStyle | None) -> PlotStyle:
    return style if style is not None else PlotStyle()


def _table(source: Any, name: str) -> pd.DataFrame:
    try:
        value = source.component(name)
    except (AttributeError, KeyError, ValueError):
        return pd.DataFrame()
    return value.copy() if value is not None else pd.DataFrame()


def _series(source: Any, name: str, attr: str) -> pd.DataFrame:
    try:
        value = source.series(name, attr)
    except (AttributeError, KeyError, ValueError):
        return pd.DataFrame()
    return value.copy() if value is not None else pd.DataFrame()


def _coordinates(source: Any) -> pd.DataFrame:
    buses = _table(source, "buses")
    if not {"x", "y"}.issubset(buses.columns):
        raise ValueError("A map requires buses with numeric x and y coordinates")
    xy = buses[["x", "y"]].apply(pd.to_numeric, errors="coerce")
    xy = xy.replace([np.inf, -np.inf], np.nan).dropna()
    if xy.empty:
        raise ValueError("No buses have usable x/y coordinates")
    return xy


def _branches(source: Any) -> pd.DataFrame:
    parts = []
    for component in BRANCHES:
        frame = _table(source, component)
        if frame.empty or not {"bus0", "bus1"}.issubset(frame.columns):
            continue
        frame = frame.copy()
        frame["component"] = component
        frame["branch_id"] = frame.index.astype(str)
        parts.append(frame.reset_index(drop=True))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _segments(frame: pd.DataFrame, xy: pd.DataFrame) -> tuple[np.ndarray, pd.DataFrame]:
    if frame.empty:
        return np.empty((0, 2, 2)), frame
    valid = frame.bus0.isin(xy.index) & frame.bus1.isin(xy.index)
    frame = frame.loc[valid].copy()
    if frame.empty:
        return np.empty((0, 2, 2)), frame
    start = xy.loc[frame.bus0, ["x", "y"]].to_numpy(dtype=float)
    end = xy.loc[frame.bus1, ["x", "y"]].to_numpy(dtype=float)
    return np.stack([start, end], axis=1), frame


def _axes(xy: pd.DataFrame, *, title: str = "", figsize: tuple[float, float] = (11, 8),
          geography=None, extent=None):
    fig, ax = plt.subplots(figsize=figsize, layout="constrained")
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    xr = float(xy.x.max() - xy.x.min()) or 1.0
    yr = float(xy.y.max() - xy.y.min()) or 1.0
    ax.set_aspect("equal", adjustable="box")
    ax.set_axis_off()
    _draw_geography(ax, geography)
    ax.set_xlim(*(extent[:2] if extent is not None else (float(xy.x.min() - xr * .08), float(xy.x.max() + xr * .08))))
    ax.set_ylim(*(extent[2:] if extent is not None else (float(xy.y.min() - yr * .08), float(xy.y.max() + yr * .08))))
    if title:
        ax.set_title(title, loc="left", pad=12)
    return fig, ax


def _draw_geography(ax, geography) -> None:
    """Draw optional country/coastline context in lon/lat (EPSG:4326)."""
    if geography is None:
        return
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise ImportError("geography requires geopandas; omit it for coordinate-only maps") from exc
    geo = gpd.read_file(geography) if isinstance(geography, (str, __import__('pathlib').Path)) else geography
    if geo.crs is None:
        raise ValueError("Geography must declare its CRS")
    geo = geo.to_crs("EPSG:4326")
    # Polygon boundaries supply both coastlines and country borders. They are
    # context only and carry no model-constraint or market meaning.
    geo.plot(ax=ax, facecolor="#f5f7f7", edgecolor="none", zorder=-3)
    geo.boundary.plot(ax=ax, color="#b7c2c8", linewidth=.55, zorder=-2)


def _group_colors(groups: pd.Series, group_colors, style: PlotStyle) -> dict[str, str]:
    unique = sorted(groups.dropna().astype(str).unique())
    specified = dict(group_colors or {})
    cmap = plt.get_cmap(style.map_style.get("group_cmap", "tab20"))
    return {group: specified.get(group, mcolors.to_hex(cmap(i / max(len(unique), 1))))
            for i, group in enumerate(unique)}


def _draw_group_buses(ax, xy: pd.DataFrame, groups: pd.Series, colors: dict[str, str],
                      *, show_group_labels=True, show_bus_labels=False):
    groups = groups.reindex(xy.index).astype("string")
    for group, ids in groups.groupby(groups, dropna=False).groups.items():
        color = colors.get(str(group), "#748291") if pd.notna(group) else "#748291"
        pts = xy.loc[list(ids)]
        ax.scatter(pts.x, pts.y, s=30, color=color, edgecolor="white", linewidth=.55, zorder=5)
        if show_group_labels and pd.notna(group):
            center = pts[["x", "y"]].mean()
            rgb = mcolors.to_rgb(color)
            label_color = "#263444" if sum(a * b for a, b in zip(rgb, (.2126, .7152, .0722))) > .65 else color
            ax.annotate(str(group), (center.x, center.y), xytext=(5, 5),
                        textcoords="offset points", fontsize=8, weight="bold",
                        color=label_color, zorder=6,
                        path_effects=[withStroke(linewidth=2.5, foreground="white")])
    if show_bus_labels:
        for bus, point in xy.iterrows():
            ax.annotate(str(bus), (point.x, point.y), xytext=(3, 3),
                        textcoords="offset points", fontsize=6, color="#263444", zorder=7)


@_styled_plot
def plot_zonal_physical_network(source: Any, bus_grouping: dict | pd.Series | str, *,
                                group_colors=None, geography=None, line_classification=None,
                                link_classification=None, extent=None, show_group_labels=True,
                                show_bus_labels=False, style: PlotStyle | None = None,
                                title="Physical network by market or region", figsize=(11, 8)):
    """Map actual buses and branches with explicit categorical grouping.

    ``bus_grouping`` is a bus-id mapping/Series or a declared Buses column.
    Unmapped buses are neutral gray. Branch classification maps IDs to
    ``internal`` or ``external``; defaults keep Lines/Transformers passive AC
    and Links controllable. Geography is a CRS-declared GeoDataFrame or path.
    Returns figure and one-row-per-plotted-branch QA table.
    """
    src = as_source(source)
    style = _style(style)
    xy = _coordinates(src)
    buses = _table(src, "buses")
    if isinstance(bus_grouping, str):
        if bus_grouping not in buses:
            raise KeyError(f"Buses has no grouping column {bus_grouping!r}")
        groups = buses[bus_grouping]
    else:
        groups = pd.Series(bus_grouping)
    groups = groups.reindex(xy.index)
    colors = _group_colors(groups, group_colors or style.map_style.get("group_colors"), style)
    fig, ax = _axes(xy, title=title, figsize=figsize, geography=geography, extent=extent)
    rows = []
    for component in BRANCHES:
        frame = _table(src, component)
        if frame.empty:
            continue
        segments, valid = _segments(frame.assign(component=component, branch_id=frame.index.astype(str)), xy)
        classifications = link_classification if component == "links" else line_classification
        classifications = classifications or {}
        for seg, (_, row) in zip(segments, valid.iterrows()):
            category = classifications.get(str(row.branch_id), "internal")
            is_link = component == "links"
            color = (style.line_style.get("boundary_color", "#ad6036") if category == "external"
                     else style.line_style.get("link_color", "#295f80") if is_link
                     else style.line_style.get("ac_color", "#87949a"))
            ax.plot(seg[:, 0], seg[:, 1], color=color, linewidth=2.0 if is_link else .8,
                    linestyle="--" if is_link or category == "external" else "-", alpha=.9 if is_link else .7,
                    zorder=3 if is_link else 1)
            rows.append({"component": component, "branch_id": str(row.branch_id),
                         "bus0": row.bus0, "bus1": row.bus1, "classification": category,
                         "plotted": True})
    _draw_group_buses(ax, xy, groups, colors, show_group_labels=show_group_labels,
                      show_bus_labels=show_bus_labels)
    zone_handles = [Line2D([], [], marker="o", linestyle="", markerfacecolor=color,
                           markeredgecolor="white", markersize=7, label=group)
                    for group, color in colors.items()]
    if groups.isna().any():
        zone_handles.append(Line2D([], [], marker="o", linestyle="", color="#748291", label="Unmapped / external"))
    network_handles = [Line2D([], [], color=style.line_style.get("ac_color", "#87949a"), linewidth=1.2, label="AC line / transformer"),
                       Line2D([], [], color=style.line_style.get("link_color", "#295f80"), linewidth=2, linestyle="--", label="Controllable Link / HVDC")]
    if any(r["classification"] == "external" for r in rows):
        network_handles.append(Line2D([], [], color=style.line_style.get("boundary_color", "#ad6036"), linewidth=2, linestyle="--", label="External / boundary branch"))
    first = ax.legend(handles=zone_handles, title="Bus group", loc="upper left",
                      bbox_to_anchor=(1.01, 1), frameon=False, fontsize=8)
    ax.add_artist(first)
    ax.legend(handles=network_handles, title="Physical branch", loc="lower left",
              bbox_to_anchor=(1.01, 0), frameon=False, fontsize=8)
    return fig, pd.DataFrame(rows)


def _draw_base(ax, source: Any, xy: pd.DataFrame, *, style: PlotStyle | None = None,
               show_buses: bool = True, labels: bool = False) -> None:
    style = _style(style)
    branches = _branches(source)
    segment, branches = _segments(branches, xy)
    if len(segment):
        palette = {"lines": style.line_style.get("line_color", style.line_style.get("ac_color", "#adb5bd")),
                   "links": style.line_style.get("link_color", "#586d84"),
                   "transformers": style.line_style.get("transformer_color", "#d8893d")}
        palette.update(style.line_style.get("component_colors", {}))
        collection = LineCollection(segment,
                                    colors=[palette.get(c, "#adb5bd") for c in branches.component],
                                    linewidths=[.8 if c == "lines" else 1.8 for c in branches.component],
                                    linestyles=["--" if c == "links" else "-" for c in branches.component],
                                    alpha=.8, zorder=1)
        ax.add_collection(collection)
    if show_buses:
        ax.scatter(xy.x, xy.y, s=14, color=style.map_style.get("bus_color", "#23364b"),
                   edgecolor="white", linewidth=.4, zorder=4)
    if labels:
        for bus, row in xy.iterrows():
            ax.annotate(str(bus), (row.x, row.y), xytext=(3, 3), textcoords="offset points",
                        fontsize=7, color="#263444", zorder=5)


def _branch_legend(ax, source: Any, style: PlotStyle) -> None:
    specs = (("lines", "AC lines", "ac_color", "#adb5bd"),
             ("links", "Links", "link_color", "#586d84"),
             ("transformers", "Transformers", "transformer_color", "#d8893d"))
    handles = [plt.Line2D([], [], color=style.line_style.get(color_key, fallback),
                          linewidth=2, linestyle="--" if component == "links" else "-", label=label)
               for component, label, color_key, fallback in specs
               if not _table(source, component).empty]
    if handles:
        ax.legend(handles=handles, loc="lower left", frameon=False, fontsize=8)


@_styled_plot
def plot_network_map(source: Any, *, style: PlotStyle | None = None,
                     labels: bool = False, title: str = "Transmission network",
                     geomap: bool = False, geography=None, extent=None,
                     figsize: tuple[float, float] = (11, 8)):
    """Draw buses, AC lines, links and transformers; return a Matplotlib Figure.

    Native PyPSA plotting is used for Network input. ``geomap=False`` works
    without Cartopy or external map data. Table input uses the same bus/branch
    component schema and a Matplotlib fallback.
    """
    src = as_source(source)
    xy = _coordinates(src)
    style = _style(style)
    fig, ax = _axes(xy, title=title, figsize=figsize, geography=geography, extent=extent)
    if getattr(src, "network", None) is not None and geography is None:
        native_geomap = bool(geomap and importlib.util.find_spec("cartopy"))
        if native_geomap:
            import cartopy.crs as ccrs
            fig.delaxes(ax)
            ax = fig.add_subplot(111, projection=ccrs.PlateCarree())
            ax.set_title(title, loc="left", pad=12)
        src.network.plot(ax=ax, geomap=native_geomap,
                         bus_size=style.map_style.get("native_bus_size", .015),
                         bus_color=style.map_style.get("bus_color", "#23364b"),
                         line_color=style.line_style.get("line_color", "#adb5bd"),
                         link_color=style.line_style.get("link_color", "#586d84"),
                         transformer_color=style.line_style.get("transformer_color", "#d8893d"),
                         line_width=style.line_style.get("line_width", .8),
                         link_width=style.line_style.get("link_width", 1.8),
                         transformer_width=style.line_style.get("transformer_width", 1.8))
        if labels:
            for bus, row in xy.iterrows():
                ax.annotate(str(bus), (row.x, row.y), xytext=(3, 3),
                            textcoords="offset points", fontsize=7)
    else:
        _draw_base(ax, src, xy, style=style, labels=labels)
    _branch_legend(ax, src, style)
    return fig


def _capacity_rows(source: Any, kind: str) -> tuple[pd.DataFrame, str]:
    kind = kind.lower()
    specs = {
        "generation": ("generators", "p_nom_opt", "p_nom", "MW"),
        "storage_power": ("storage_units", "p_nom_opt", "p_nom", "MW"),
        "storage_energy": ("storage_units", "p_nom_opt", "p_nom", "MWh"),
        "store_energy": ("stores", "e_nom_opt", "e_nom", "MWh"),
        "link_capacity": ("links", "p_nom_opt", "p_nom", "MW"),
        "peak_load": ("loads", "p_set", "p_set", "MW"),
    }
    if kind not in specs:
        raise ValueError(f"Unknown capacity kind {kind!r}; choose {list(specs)}")
    component, preferred, fallback, unit = specs[kind]
    df = _table(source, component)
    if df.empty or "bus" not in df.columns and kind != "link_capacity":
        raise ValueError(f"No {component} table with bus assignment")
    if kind == "link_capacity":
        if "bus0" not in df:
            raise ValueError("Link capacity needs bus0")
        df["bus"] = df.bus0
    if kind == "peak_load":
        timeseries = _series(source, "loads", "p")
        if timeseries.empty:
            timeseries = _series(source, "loads", "p_set")
        if not timeseries.empty:
            df["value"] = timeseries.max().reindex(df.index)
        else:
            df["value"] = pd.to_numeric(df.get("p_set"), errors="coerce")
    else:
        nominal = pd.to_numeric(df.get(preferred), errors="coerce") if preferred in df else None
        base = pd.to_numeric(df.get(fallback), errors="coerce") if fallback in df else None
        if nominal is None and base is None:
            raise ValueError(f"{component} needs {preferred} or {fallback}")
        df["value"] = nominal.fillna(base) if nominal is not None and base is not None else (nominal if nominal is not None else base)
        if kind == "storage_energy":
            if "max_hours" not in df:
                raise ValueError("storage_energy needs storage_units.max_hours")
            df["value"] *= pd.to_numeric(df.max_hours, errors="coerce")
    if "carrier" not in df:
        df["carrier"] = kind
    return df[["bus", "carrier", "value"]].dropna().query("value > 0"), unit


@_styled_plot
def plot_capacity_map(source: Any, *, kind: str = "generation", style: PlotStyle | None = None,
                      title: str | None = None, max_radius_fraction: float = .035,
                      geography=None, extent=None,
                      figsize: tuple[float, float] = (11, 8)):
    """Plot proportional bus pies; energy and power capacity remain separate.

    ``kind`` is generation, storage_power, storage_energy, store_energy,
    link_capacity or peak_load. Returns ``(figure, bus_carrier_values)``.
    """
    src = as_source(source)
    style = _style(style)
    xy = _coordinates(src)
    assets, unit = _capacity_rows(src, kind)
    values = assets.groupby(["bus", "carrier"], observed=True).value.sum()
    values = values[values.index.get_level_values("bus").isin(xy.index)]
    if values.empty:
        raise ValueError(f"No positive {kind} values at geocoded buses")
    fig, ax = _axes(xy, title=title or f"{kind.replace('_', ' ').title()} ({unit})", figsize=figsize,
                    geography=geography, extent=extent)
    _draw_base(ax, src, xy, style=style, show_buses=False)
    span = max(float(np.ptp(xy.x)), float(np.ptp(xy.y)), 1.0)
    scale = max_radius_fraction * span
    totals = values.groupby(level="bus").sum()
    maximum = float(totals.max())
    carriers = style.ordered(list(dict.fromkeys(values.index.get_level_values("carrier"))))
    for bus, total in totals.items():
        center = xy.loc[bus]
        radius = scale * np.sqrt(float(total) / maximum)
        by_carrier = values.loc[bus]
        if isinstance(by_carrier, np.number):
            by_carrier = pd.Series({carriers[0]: float(by_carrier)})
        by_carrier = by_carrier.reindex(carriers).dropna()
        angle = 90.0
        for carrier, value in by_carrier.items():
            next_angle = angle + 360.0 * float(value) / float(total)
            ax.add_patch(Wedge((center.x, center.y), radius, angle, next_angle,
                               facecolor=style.color(str(carrier), getattr(src, "network", None)),
                               edgecolor="white", linewidth=.35, zorder=5))
            angle = next_angle
        ax.add_patch(plt.Circle((center.x, center.y), radius, fill=False,
                                edgecolor="#2f3c4d", linewidth=.5, zorder=6))
    handles = [plt.Line2D([], [], color=style.color(str(c), getattr(src, "network", None)),
                          marker="o", linestyle="", markersize=7, label=str(c)) for c in carriers]
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.01, 1),
              frameon=False, title="Carrier", fontsize=8, ncol=max(1, (len(handles) + 17) // 18))
    fig.text(.03, .025, f"Largest circle = {maximum:,.0f} {unit}; circle area ∝ {unit}",
             fontsize=8, color="#46576b")
    return fig, values.rename(f"{kind}_{unit.lower()}")


def _snapshot_row(frame: pd.DataFrame, snapshot: Any, label: str) -> pd.Series:
    if frame.empty:
        raise ValueError(f"{label} time series are unavailable")
    if snapshot not in frame.index:
        raise KeyError(f"Snapshot {snapshot!r} is not in {label} time series")
    return pd.to_numeric(frame.loc[snapshot], errors="coerce")


def _rating(frame: pd.DataFrame, component: str) -> pd.Series:
    prefix = "s" if component in {"lines", "transformers"} else "p"
    opt, base = f"{prefix}_nom_opt", f"{prefix}_nom"
    a = pd.to_numeric(frame[opt], errors="coerce") if opt in frame else pd.Series(np.nan, index=frame.index)
    b = pd.to_numeric(frame[base], errors="coerce") if base in frame else pd.Series(np.nan, index=frame.index)
    return a.fillna(b).where(lambda x: x > 0)


@_styled_plot
def plot_flow_map(source: Any, snapshot: Any, *, style: PlotStyle | None = None,
                  components: tuple[str, ...] = BRANCHES, labels: bool = False,
                  geography=None, extent=None,
                  title: str | None = None, figsize: tuple[float, float] = (11, 8)):
    """Draw signed MW p0 arrows and active-power/nameplate loading at a snapshot.

    AC loading uses saved q0 and dynamic line limits where available;
    otherwise it is an active-power proxy. Link loading uses p0/p_nom.
    Returns ``(figure, branch_metrics)``.
    """
    src = as_source(source)
    style = _style(style)
    xy = _coordinates(src)
    rows = []
    for component in components:
        frame = _table(src, component)
        if frame.empty or not {"bus0", "bus1"}.issubset(frame):
            continue
        p0 = _snapshot_row(_series(src, component, "p0"), snapshot, f"{component}.p0")
        rating = _rating(frame, component)
        item = frame[["bus0", "bus1"]].copy()
        item["component"] = component
        item["branch_id"] = item.index.astype(str)
        item["p0_mw"] = p0.reindex(item.index)
        item["rating"] = rating
        item["rating_unit"] = "MVA" if component in {"lines", "transformers"} else "MW"
        item["loading_basis"] = "active_power_proxy" if component in {"lines", "transformers"} else "link_active_power"
        item["utilization"] = item.p0_mw.abs() / item.rating
        rows.append(item.reset_index(drop=True))
    if not rows:
        raise ValueError("No branch p0 flows and ratings available")
    metrics = pd.concat(rows, ignore_index=True)
    # Keep the selected-hour colors consistent with the horizon-wide loading
    # calculation, including q0 and dynamic AC limit multipliers when saved.
    from .congestion import compute_loading_timeseries
    loading, loading_meta = compute_loading_timeseries(src, components=components)
    if snapshot not in loading.index:
        raise KeyError(f"Snapshot {snapshot!r} is not in branch loading")
    metrics["utilization"] = [loading.at[snapshot, (r.component, r.branch_id)]
                              if (r.component, r.branch_id) in loading.columns else np.nan
                              for r in metrics.itertuples(index=False)]
    metrics["loading_basis"] = [loading_meta.at[(r.component, r.branch_id), "loading_basis"]
                                if (r.component, r.branch_id) in loading_meta.index else "unavailable"
                                for r in metrics.itertuples(index=False)]
    segment, metrics = _segments(metrics, xy)
    fig, ax = _axes(xy, title=title or f"Physical flow at {snapshot}", figsize=figsize,
                    geography=geography, extent=extent)
    utilization = metrics.utilization.replace([np.inf, -np.inf], np.nan)
    cmap = plt.get_cmap(style.line_style.get("utilization_cmap", "viridis"))
    norm = mcolors.Normalize(vmin=0, vmax=max(1.0, float(utilization.max(skipna=True)) if utilization.notna().any() else 1.0))
    widths = np.clip(1.0 + 3.0 * utilization.fillna(0).to_numpy(), 1.0, 6.0)
    ax.add_collection(LineCollection(segment, linewidths=widths,
                                     linestyles=["--" if c == "links" else "-" for c in metrics.component],
                                     colors=cmap(norm(utilization.fillna(0).to_numpy())), zorder=2))
    span = max(float(np.ptp(xy.x)), float(np.ptp(xy.y)), 1.0)
    for seg, p in zip(segment, metrics.p0_mw):
        if pd.isna(p) or abs(p) < 1e-8:
            continue
        a, b = (seg[0], seg[1]) if p > 0 else (seg[1], seg[0])
        midpoint = (a + b) / 2
        direction = b - a
        length = float(np.hypot(*direction))
        if length < span * .006:
            continue
        direction /= length
        start = midpoint - direction * min(length * .13, span * .025)
        end = midpoint + direction * min(length * .13, span * .025)
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=8,
                                     linewidth=.8, color="#162636", zorder=3))
    ax.scatter(xy.x, xy.y, s=13, color=style.map_style.get("bus_color", "#23364b"),
               edgecolor="white", linewidth=.4, zorder=4)
    if labels:
        for bus, row in xy.iterrows():
            ax.annotate(str(bus), (row.x, row.y), xytext=(3, 3), textcoords="offset points", fontsize=7)
    fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=ax, shrink=.6,
                 label="Branch utilization / available rating (see data-table basis)")
    ax.legend(handles=[Line2D([], [], color="#55616b", linewidth=2, label="AC / transformer"),
                       Line2D([], [], color="#55616b", linewidth=2, linestyle="--", label="Link / HVDC")],
              loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False, fontsize=8)
    fig.text(.03, .025, "Arrow follows signed p0; positive: bus0 → bus1. Width follows loading.",
             fontsize=8, color="#46576b")
    return fig, metrics


@_styled_plot
def plot_price_map(source: Any, *, snapshot: Any | None = None,
                   aggregate: str = "mean", style: PlotStyle | None = None,
                   title: str | None = None, unit: str = "currency/MWh",
                   geography=None, extent=None,
                   figsize: tuple[float, float] = (11, 8)):
    """Draw bus marginal prices for one hour or an unweighted time statistic.

    Price aggregation is deliberately explicit. This is not a market average;
    market mapping and weighting belong in a project adapter. Returns
    ``(figure, bus_price_series)``.
    """
    src = as_source(source)
    style = _style(style)
    xy = _coordinates(src)
    prices = _series(src, "buses", "marginal_price")
    if snapshot is not None:
        values = _snapshot_row(prices, snapshot, "buses.marginal_price")
        subtitle = str(snapshot)
    elif aggregate in {"mean", "median", "min", "max"} and not prices.empty:
        values = getattr(prices, aggregate)(axis=0)
        subtitle = f"{aggregate} across snapshots"
    else:
        raise ValueError("Prices require a snapshot or an available mean/median/min/max series")
    values = pd.to_numeric(values, errors="coerce").reindex(xy.index).dropna()
    if values.empty:
        raise ValueError("No marginal prices at geocoded buses")
    fig, ax = _axes(xy, title=title or f"Bus marginal price — {subtitle}", figsize=figsize,
                    geography=geography, extent=extent)
    _draw_base(ax, src, xy, style=style, show_buses=False)
    collection = ax.scatter(xy.loc[values.index, "x"], xy.loc[values.index, "y"],
                            c=values, cmap=style.map_style.get("price_cmap", "coolwarm"),
                            s=65, edgecolor="white", linewidth=.6, zorder=5)
    fig.colorbar(collection, ax=ax, shrink=.6, label=unit)
    return fig, values.rename("marginal_price")

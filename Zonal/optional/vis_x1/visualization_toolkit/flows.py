"""Cross-market physical branch flows with an explicit bus-to-market ontology.

``flow_mw`` retains PyPSA's bus0 terminal ``p0`` for compatibility. PyPSA
terminal powers are positive when leaving their respective buses: bus0 market
exchange uses ``p0`` and bus1 market exchange uses ``p1``. This captures
two-terminal losses when both are available. A missing ``p1`` is replaced by
``-p0`` and explicitly flagged as a lossless transfer proxy. Multi-terminal
link outputs beyond bus1 are outside this two-terminal view. These are physical
flows, not market schedules.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .io import as_source
from .styles import PlotStyle, style_context


BRANCH_COMPONENTS = ("lines", "transformers", "links")


def _component(src, name: str) -> pd.DataFrame:
    try:
        return src.component(name)
    except KeyError:
        return pd.DataFrame()


def _nominal(frame: pd.DataFrame, component: str) -> pd.Series:
    field = "p_nom" if component == "links" else "s_nom"
    optimal = f"{field}_opt"
    if optimal in frame:
        values = pd.to_numeric(frame[optimal], errors="coerce")
        if field in frame:
            values = values.fillna(pd.to_numeric(frame[field], errors="coerce"))
    elif field in frame:
        values = pd.to_numeric(frame[field], errors="coerce")
    else:
        values = pd.Series(np.nan, index=frame.index)
    return values


def extract_intermarket_flows(
    source,
    bus_market_map: Mapping[str, str],
    *,
    components: Iterable[str] = BRANCH_COMPONENTS,
    branch_ids: Mapping[str, Iterable[str]] | None = None,
) -> pd.DataFrame:
    """Return snapshot/branch flows crossing explicitly mapped markets.

    Every endpoint of every selected branch must have a market mapping. The
    optional ``branch_ids`` selects physical exchange assets by exact ID, not
    by carrier or name heuristic. Missing ``p0`` makes that component absent.
    ``p1_mw`` is the bus1 terminal power; ``terminal1_basis`` distinguishes
    observed values from the lossless ``-p0`` proxy. AC line/transformer
    ``rating_mva`` is separate from link ``capacity_mw``. AC ``utilization`` is
    an active-power/MVA-rating proxy, not apparent-power thermal loading.
    """
    src = as_source(source)
    frames: list[pd.DataFrame] = []
    columns = ["snapshot", "component", "branch_id", "bus0", "bus1", "market0", "market1", "flow_mw", "p1_mw", "terminal1_basis", "capacity_mw", "rating_mva", "utilization", "utilization_basis"]
    for component in components:
        if component not in BRANCH_COMPONENTS:
            raise ValueError(f"Unsupported branch component: {component}")
        branches = _component(src, component)
        if branches.empty:
            continue
        selected = list(branch_ids[component]) if branch_ids is not None and component in branch_ids else list(branches.index)
        missing_ids = set(selected).difference(branches.index)
        if missing_ids:
            raise KeyError(f"Unknown {component} IDs: {sorted(missing_ids)}")
        branches = branches.loc[selected]
        if not {"bus0", "bus1"}.issubset(branches):
            raise ValueError(f"{component} requires bus0 and bus1")
        buses = set(branches.bus0).union(branches.bus1)
        unmapped = buses.difference(bus_market_map)
        if unmapped:
            raise ValueError(f"Unmapped buses in {component}: {sorted(unmapped)}")
        market0 = branches.bus0.map(bus_market_map)
        market1 = branches.bus1.map(bus_market_map)
        branches = branches.loc[market0.ne(market1)]
        if branches.empty:
            continue
        try:
            flows = src.series(component, "p0")
        except KeyError:
            flows = pd.DataFrame()
        if flows.empty:
            continue
        missing_flow = branches.index.difference(flows.columns)
        if len(missing_flow):
            raise ValueError(f"Missing {component}_t.p0 for {list(missing_flow)}")
        flows = flows.loc[:, branches.index]
        try:
            terminal1 = src.series(component, "p1")
        except KeyError:
            terminal1 = pd.DataFrame()
        terminal1 = terminal1.reindex(index=flows.index, columns=flows.columns)
        long = flows.rename_axis("snapshot").stack(future_stack=True).rename("flow_mw").reset_index()
        long.columns = ["snapshot", "branch_id", "flow_mw"]
        p1_long = terminal1.rename_axis("snapshot").stack(future_stack=True).rename("observed_p1_mw").reset_index()
        p1_long.columns = ["snapshot", "branch_id", "observed_p1_mw"]
        long = long.merge(p1_long, on=["snapshot", "branch_id"], validate="one_to_one")
        long["terminal1_basis"] = np.where(long.observed_p1_mw.notna(), "observed_p1", "lossless_p0_proxy")
        long["p1_mw"] = long.observed_p1_mw.fillna(-long.flow_mw)
        long = long.drop(columns="observed_p1_mw")
        long["component"] = component
        long["bus0"] = long.branch_id.map(branches.bus0)
        long["bus1"] = long.branch_id.map(branches.bus1)
        long["market0"] = long.bus0.map(bus_market_map)
        long["market1"] = long.bus1.map(bus_market_map)
        nominal = long.branch_id.map(_nominal(branches, component))
        if component == "links":
            long["capacity_mw"] = nominal
            long["rating_mva"] = np.nan
            long["utilization"] = long.flow_mw.abs().div(nominal.where(nominal.gt(0)))
            long["utilization_basis"] = "link_p0_over_p_nom"
        else:
            long["capacity_mw"] = np.nan
            long["rating_mva"] = nominal
            long["utilization"] = long[["flow_mw", "p1_mw"]].abs().max(axis=1).div(nominal.where(nominal.gt(0)))
            long["utilization_basis"] = "active_power_over_apparent_rating_proxy"
        frames.append(long[columns])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns)


def market_exchange_timeseries(flows: pd.DataFrame) -> pd.DataFrame:
    """Return terminal-accurate gross import/export and net import MW.

    A legacy table without ``p1_mw`` uses ``-flow_mw`` at bus1 and marks the
    affected market observations as proxy-based.
    """
    required = {"snapshot", "market0", "market1", "flow_mw"}
    if not required.issubset(flows):
        raise ValueError(f"Flow table requires {sorted(required)}")
    if flows.empty:
        return pd.DataFrame(columns=["snapshot", "market", "import_mw", "export_mw", "net_import_mw", "uses_proxy_terminal"])
    clean = flows.dropna(subset=["flow_mw"])
    p1 = clean["p1_mw"] if "p1_mw" in clean else pd.Series(np.nan, index=clean.index)
    proxy = p1.isna()
    p1 = p1.fillna(-clean.flow_mw)
    if "terminal1_basis" in clean:
        proxy = proxy | clean.terminal1_basis.ne("observed_p1")
    forward = pd.DataFrame({"snapshot": clean.snapshot, "market": clean.market0, "import_mw": (-clean.flow_mw).clip(lower=0), "export_mw": clean.flow_mw.clip(lower=0), "uses_proxy_terminal": False})
    reverse = pd.DataFrame({"snapshot": clean.snapshot, "market": clean.market1, "import_mw": (-p1).clip(lower=0), "export_mw": p1.clip(lower=0), "uses_proxy_terminal": proxy})
    result = pd.concat([forward, reverse]).groupby(["snapshot", "market"], as_index=False).agg(import_mw=("import_mw", "sum"), export_mw=("export_mw", "sum"), uses_proxy_terminal=("uses_proxy_terminal", "max"))
    result["net_import_mw"] = result.import_mw - result.export_mw
    return result.sort_values(["market", "snapshot"]).reset_index(drop=True)


def summarize_market_exchange(source, flows: pd.DataFrame, *, weight_kind: str = "generators") -> pd.DataFrame:
    """Integrate gross import/export using declared snapshot weights.

    Snapshot weights are obtained from the source and must represent elapsed
    hours for MWh labels; representative-period weights require interpretation
    from the originating model. Observed p0/p1 preserve two-terminal losses;
    rows flagged ``uses_proxy_terminal`` rely on a lossless bus1 proxy.
    """
    src = as_source(source)
    hourly = market_exchange_timeseries(flows)
    if hourly.empty:
        return pd.DataFrame(columns=["market", "gross_import_mwh", "gross_export_mwh", "net_import_mwh", "uses_proxy_terminal"])
    weights = src.weights(weight_kind)
    hourly["weight"] = hourly.snapshot.map(weights)
    if hourly.weight.isna().any():
        raise ValueError("Snapshot weights missing for one or more flow snapshots")
    hourly["gross_import_mwh"] = hourly.import_mw * hourly.weight
    hourly["gross_export_mwh"] = hourly.export_mw * hourly.weight
    result = hourly.groupby("market", as_index=False).agg(gross_import_mwh=("gross_import_mwh", "sum"), gross_export_mwh=("gross_export_mwh", "sum"), uses_proxy_terminal=("uses_proxy_terminal", "max"))
    result["net_import_mwh"] = result.gross_import_mwh - result.gross_export_mwh
    return result


def plot_market_exchange(timeseries: pd.DataFrame, market: str, *, title: str | None = None, style=None):
    """Plot gross import and export for one explicitly named market."""
    data = timeseries.loc[timeseries.market.eq(market)].sort_values("snapshot")
    if data.empty:
        raise ValueError(f"No exchange observations for market {market}")
    style = style or PlotStyle()
    with style_context(style):
        fig, ax = plt.subplots(figsize=(11, 4.8))
        ax.plot(data.snapshot, data.import_mw / 1e3, label="Imports", color=style.line_style.get("import_color", "#176b9a"), lw=1.5)
        ax.plot(data.snapshot, -data.export_mw / 1e3, label="Exports", color=style.line_style.get("export_color", "#b25836"), lw=1.5)
        ax.axhline(0, color="0.35", lw=0.7)
        ax.set(ylabel="Power (GW)", title=title or f"{market} cross-market exchange")
        ax.legend(frameon=False, ncol=2)
        fig.autofmt_xdate()
        fig.tight_layout()
    return fig


def plot_flow_duration(flows: pd.DataFrame, *, branch_ids: Iterable[str] | None = None, top_n: int = 10):
    """Plot absolute branch flow duration curves for selected or top-mean branches."""
    if flows.empty:
        raise ValueError("Flow table is empty")
    data = flows.copy()
    if branch_ids is None:
        order = data.groupby(["component", "branch_id"]).flow_mw.apply(lambda s: s.abs().mean()).nlargest(top_n).index
    else:
        order = data.loc[data.branch_id.isin(branch_ids), ["component", "branch_id"]].drop_duplicates().itertuples(index=False, name=None)
    with style_context():
        fig, ax = plt.subplots(figsize=(9, 5))
        count = 0
        for component, branch_id in order:
            values = data.loc[data.component.eq(component) & data.branch_id.eq(branch_id), "flow_mw"].dropna().abs().sort_values(ascending=False).to_numpy()
            if len(values):
                ax.plot(np.arange(1, len(values) + 1), values / 1e3, lw=1.3, label=f"{component}: {branch_id}")
                count += 1
        if not count:
            raise ValueError("No selected branch observations")
        ax.set(xlabel="Ranked snapshot", ylabel="Absolute flow (GW)", title="Branch flow duration")
        ax.legend(frameon=False, fontsize=8, bbox_to_anchor=(1.02, 1), loc="upper left")
        fig.tight_layout()
    return fig

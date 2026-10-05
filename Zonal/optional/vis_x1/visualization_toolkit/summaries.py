"""Generic, explicitly scoped PyPSA result summaries and presentation plots.

Energy integrations use the source's generator snapshot weights. A weight must
represent hours for the output to be interpreted as MWh. Cost rows are
illustrative accounting terms, not an objective reconciliation.
"""

from __future__ import annotations

from collections.abc import Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .io import as_source
from .styles import PlotStyle, style_context


def _component(src, name: str) -> pd.DataFrame:
    try:
        return src.component(name)
    except KeyError:
        return pd.DataFrame()


def _series(src, component: str, attribute: str) -> pd.DataFrame:
    try:
        return src.series(component, attribute)
    except (AttributeError, KeyError):
        return pd.DataFrame()


def _weights(src, index: pd.Index) -> pd.Series:
    weights = src.weights("generators").reindex(index)
    if weights.isna().any():
        raise ValueError("Generator snapshot weights are missing for observations")
    return weights.astype(float)


def _nominal(frame: pd.DataFrame, field: str) -> pd.Series:
    if f"{field}_opt" in frame:
        result = pd.to_numeric(frame[f"{field}_opt"], errors="coerce")
        if field in frame:
            result = result.fillna(pd.to_numeric(frame[field], errors="coerce"))
        return result
    return pd.to_numeric(frame[field], errors="coerce") if field in frame else pd.Series(np.nan, index=frame.index)


def extract_capacity_mix(source) -> pd.DataFrame:
    """Return capacity by component/carrier; preserve MW versus MWh units."""
    src = as_source(source)
    rows = []
    for component, field, unit in (("generators", "p_nom", "MW"), ("storage_units", "p_nom", "MW"), ("stores", "e_nom", "MWh")):
        frame = _component(src, component)
        if frame.empty:
            continue
        values = _nominal(frame, field)
        carriers = frame.carrier if "carrier" in frame else pd.Series("unspecified", index=frame.index)
        item = pd.DataFrame({"component": component, "carrier": carriers, "unit": unit, "capacity": values})
        rows.append(item)
    if not rows:
        return pd.DataFrame(columns=["component", "carrier", "unit", "capacity"])
    return pd.concat(rows).groupby(["component", "carrier", "unit"], as_index=False).capacity.sum()


def extract_signed_energy_balance(source, *, exchange=None, carrier_mapping=None) -> pd.DataFrame:
    """Integrate positive injections and negative withdrawals separately.

    Generator, storage and load series come from generic dispatch extraction.
    Exchange is included only when the caller supplies a signed perimeter
    series. Values are MWh when snapshot generator weights represent hours.
    """
    from .dispatch import dispatch_table
    src = as_source(source)
    power = dispatch_table(src, exchange=exchange, carrier_mapping=carrier_mapping)
    weights = _weights(src, power.index)
    positive = power.clip(lower=0).mul(weights, axis=0).sum()
    negative = power.clip(upper=0).mul(weights, axis=0).sum()
    result = pd.DataFrame({"carrier": power.columns,
                           "injection_mwh": positive.reindex(power.columns).to_numpy(),
                           "withdrawal_mwh": negative.reindex(power.columns).to_numpy()})
    return result.loc[result.injection_mwh.ne(0) | result.withdrawal_mwh.ne(0)].reset_index(drop=True)


def plot_signed_energy_balance(table: pd.DataFrame, *, style=None):
    """Diverging energy balance; positive supply and negative withdrawal."""
    if not {"carrier", "injection_mwh", "withdrawal_mwh"}.issubset(table):
        raise ValueError("energy table requires carrier, injection_mwh, withdrawal_mwh")
    style = style or PlotStyle()
    shown = table.loc[table.injection_mwh.ne(0) | table.withdrawal_mwh.ne(0)].copy()
    shown = shown.sort_values("carrier")
    with style_context(style):
        fig, ax = plt.subplots(figsize=(10, max(4, .32 * len(shown) + 1)), layout="constrained")
        y = np.arange(len(shown))
        color = [style.color(c) for c in shown.carrier]
        ax.barh(y, shown.injection_mwh / 1e6, color=color, label="Injection")
        ax.barh(y, shown.withdrawal_mwh / 1e6, color=color, alpha=.45, label="Withdrawal")
        ax.set_yticks(y, shown.carrier)
        ax.axvline(0, color=".3", lw=.7)
        ax.set_xlabel("Signed horizon energy [TWh]")
        ax.set_title("Signed energy balance by carrier", loc="left")
        ax.grid(axis="x", alpha=.18)
        ax.legend(frameon=False, ncol=2)
    return fig


def extract_generation_mix(source, *, include_storage_discharge: bool = False) -> pd.DataFrame:
    """Return weighted positive generator energy, optionally storage discharge.

    Storage discharge is shown separately and must not be interpreted as
    primary generation. Charging and link conversion are excluded.
    """
    src = as_source(source)
    rows = []
    for component in ("generators", "storage_units") if include_storage_discharge else ("generators",):
        assets = _component(src, component)
        power = _series(src, component, "p")
        if assets.empty or power.empty:
            continue
        ids = assets.index.intersection(power.columns)
        if len(ids) != len(assets):
            raise ValueError(f"Missing {component}_t.p columns for {list(assets.index.difference(ids))}")
        weighted = power[ids].clip(lower=0).mul(_weights(src, power.index), axis=0).sum(axis=0)
        carriers = assets.loc[ids, "carrier"] if "carrier" in assets else pd.Series("unspecified", index=ids)
        rows.append(pd.DataFrame({"component": component, "carrier": carriers, "energy_mwh": weighted}).reset_index(drop=True))
    if not rows:
        return pd.DataFrame(columns=["component", "carrier", "energy_mwh"])
    return pd.concat(rows).groupby(["component", "carrier"], as_index=False).energy_mwh.sum()


def extract_generation_vs_capacity(source) -> pd.DataFrame:
    """Compare generator energy and installed power, with capacity factors."""
    src = as_source(source)
    assets = _component(src, "generators")
    dispatch = _series(src, "generators", "p")
    columns = ["carrier", "capacity_mw", "generation_mwh", "capacity_factor"]
    if assets.empty or dispatch.empty:
        return pd.DataFrame(columns=columns)
    ids = assets.index.intersection(dispatch.columns)
    if len(ids) != len(assets):
        raise ValueError(f"Missing generators_t.p columns for {list(assets.index.difference(ids))}")
    weights = _weights(src, dispatch.index)
    cap = _nominal(assets.loc[ids], "p_nom")
    energy = dispatch[ids].clip(lower=0).mul(weights, axis=0).sum(axis=0)
    result = pd.DataFrame({"carrier": assets.loc[ids, "carrier"], "capacity_mw": cap, "generation_mwh": energy})
    result = result.groupby("carrier", as_index=False)[["capacity_mw", "generation_mwh"]].sum()
    result["capacity_factor"] = result.generation_mwh.div(result.capacity_mw.mul(weights.sum()).where(result.capacity_mw.gt(0)))
    return result[columns]


def extract_curtailment(source, generator_ids: Iterable[str]) -> pd.DataFrame:
    """Available minus dispatched energy for explicitly listed generators.

    Requires p_max_pu (time varying or static) and an installed p_nom. Negative
    residuals are retained as evidence of inconsistent source quantities.
    """
    src = as_source(source)
    assets = _component(src, "generators")
    ids = list(generator_ids)
    missing = set(ids).difference(assets.index)
    if missing:
        raise KeyError(f"Unknown generator IDs: {sorted(missing)}")
    dispatch = _series(src, "generators", "p")
    if dispatch.empty or not set(ids).issubset(dispatch.columns):
        raise ValueError("Dispatch missing for selected curtailment generators")
    availability = _series(src, "generators", "p_max_pu")
    missing_availability = set(ids).difference(availability.columns)
    if missing_availability and "p_max_pu" not in assets:
        raise ValueError(f"Availability missing for {sorted(missing_availability)}")
    availability = availability.reindex(index=dispatch.index, columns=ids)
    if "p_max_pu" in assets:
        availability = availability.fillna(assets.loc[ids, "p_max_pu"].astype(float))
    if availability.isna().any().any():
        raise ValueError("Availability contains missing values for selected generators")
    weights = _weights(src, dispatch.index)
    potential = availability.mul(_nominal(assets.loc[ids], "p_nom"), axis=1)
    out = pd.DataFrame({
        "generator_id": ids,
        "carrier": assets.loc[ids, "carrier"].to_numpy(),
        "available_mwh": potential.mul(weights, axis=0).sum().to_numpy(),
        "dispatched_mwh": dispatch[ids].mul(weights, axis=0).sum().to_numpy(),
    })
    out["curtailment_mwh_signed"] = out.available_mwh - out.dispatched_mwh
    return out


def extract_load_shedding(source, generator_ids: Iterable[str], *, threshold_mw: float = 1e-6) -> pd.DataFrame:
    """Report shedding only for caller-identified generator assets."""
    src = as_source(source)
    ids = list(generator_ids)
    assets = _component(src, "generators")
    missing = set(ids).difference(assets.index)
    if missing:
        raise KeyError(f"Unknown shedding generator IDs: {sorted(missing)}")
    dispatch = _series(src, "generators", "p")
    if dispatch.empty or not set(ids).issubset(dispatch.columns):
        raise ValueError("Dispatch missing for selected shedding generators")
    power = dispatch[ids].clip(lower=0)
    weights = _weights(src, power.index)
    return pd.DataFrame({
        "generator_id": ids,
        "carrier": assets.loc[ids, "carrier"].to_numpy(),
        "shedding_mwh": power.mul(weights, axis=0).sum().to_numpy(),
        "scarcity_weighted_hours": power.gt(threshold_mw).mul(weights, axis=0).sum().to_numpy(),
        "max_shedding_mw": power.max().to_numpy(),
    })


def extract_curtailment_duration(source, generator_ids: Iterable[str]) -> pd.DataFrame:
    """Chronological curtailment evidence for explicit generator IDs.

    The signed residual is preserved; the positive column is a diagnostic
    duration-curve value, not a silent correction to an inconsistent network.
    """
    src = as_source(source)
    ids = list(generator_ids)
    assets = _component(src, "generators")
    missing = set(ids).difference(assets.index)
    if missing:
        raise KeyError(f"Unknown generator IDs: {sorted(missing)}")
    dispatch = _series(src, "generators", "p")
    if dispatch.empty or not set(ids).issubset(dispatch.columns):
        raise ValueError("Dispatch missing for selected curtailment generators")
    availability = _series(src, "generators", "p_max_pu").reindex(index=dispatch.index, columns=ids)
    if "p_max_pu" in assets:
        availability = availability.fillna(assets.loc[ids, "p_max_pu"].astype(float))
    if availability.isna().any().any():
        raise ValueError("Availability missing for selected curtailment generators")
    potential = availability.mul(_nominal(assets.loc[ids], "p_nom"), axis=1)
    if potential.isna().any().any():
        raise ValueError("Installed capacity missing for selected curtailment generators")
    weights = _weights(src, dispatch.index)
    parts = []
    for asset_id in ids:
        signed = potential[asset_id] - dispatch[asset_id]
        parts.append(pd.DataFrame({
            "snapshot": dispatch.index,
            "generator_id": asset_id,
            "carrier": assets.at[asset_id, "carrier"],
            "available_mw": potential[asset_id].to_numpy(),
            "dispatched_mw": dispatch[asset_id].to_numpy(),
            "curtailment_mw_signed": signed.to_numpy(),
            "positive_curtailment_mw": signed.clip(lower=0).to_numpy(),
            "snapshot_weight_hours": weights.to_numpy(),
        }))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["snapshot", "generator_id", "carrier", "available_mw", "dispatched_mw", "curtailment_mw_signed", "positive_curtailment_mw", "snapshot_weight_hours"])


def extract_shedding_duration(source, generator_ids: Iterable[str]) -> pd.DataFrame:
    """Chronological shedding observations for explicit generator IDs."""
    src = as_source(source)
    ids = list(generator_ids)
    assets = _component(src, "generators")
    missing = set(ids).difference(assets.index)
    if missing:
        raise KeyError(f"Unknown shedding generator IDs: {sorted(missing)}")
    dispatch = _series(src, "generators", "p")
    if dispatch.empty or not set(ids).issubset(dispatch.columns):
        raise ValueError("Dispatch missing for selected shedding generators")
    weights = _weights(src, dispatch.index)
    parts = []
    for asset_id in ids:
        parts.append(pd.DataFrame({
            "snapshot": dispatch.index,
            "generator_id": asset_id,
            "carrier": assets.at[asset_id, "carrier"],
            "shedding_mw": dispatch[asset_id].clip(lower=0).to_numpy(),
            "snapshot_weight_hours": weights.to_numpy(),
        }))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["snapshot", "generator_id", "carrier", "shedding_mw", "snapshot_weight_hours"])


def _plot_weighted_duration(table: pd.DataFrame, value: str, title: str, *, style=None):
    if table.empty or value not in table:
        raise ValueError(f"No {value} observations")
    by_snapshot = table.groupby("snapshot", as_index=False).agg(power_mw=(value, "sum"), snapshot_weight_hours=("snapshot_weight_hours", "first"))
    if by_snapshot.snapshot_weight_hours.le(0).any():
        raise ValueError("Duration weights must be positive")
    ordered = by_snapshot.sort_values("power_mw", ascending=False)
    hours = np.r_[0.0, ordered.snapshot_weight_hours.cumsum().to_numpy()]
    power = np.r_[ordered.power_mw.to_numpy() / 1e3, 0.0]
    with style_context(style):
        fig, ax = plt.subplots(figsize=(9, 4.8))
        ax.step(hours, power, where="post", color="#4477aa", lw=1.6)
        ax.set(xlabel="Cumulative weighted hours", ylabel="Power (GW)", title=title)
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
    return fig


def plot_curtailment_duration(table: pd.DataFrame, *, style=None):
    """Positive curtailment duration, retaining signed residual in the table."""
    return _plot_weighted_duration(table, "positive_curtailment_mw", "Curtailment duration", style=style)


def plot_shedding_duration(table: pd.DataFrame, *, style=None):
    """Shedding duration from explicitly identified generator assets."""
    return _plot_weighted_duration(table, "shedding_mw", "Load shedding duration", style=style)


def extract_cost_breakdown(source) -> pd.DataFrame:
    """Return inspectable indicative capex/dispatch costs and objective.

    Objective is a separate row, never the sum of returned cost terms. Terms
    can differ from the model objective due to investment periods, offsets,
    time-varying costs, reserve/constraint penalties, and omitted components.
    """
    src = as_source(source)
    rows: list[dict] = []
    for component, nominal in (("generators", "p_nom"), ("storage_units", "p_nom"), ("stores", "e_nom"), ("links", "p_nom"), ("lines", "s_nom"), ("transformers", "s_nom")):
        frame = _component(src, component)
        if frame.empty or "capital_cost" not in frame:
            continue
        capacity = _nominal(frame, nominal)
        costs = capacity.mul(pd.to_numeric(frame.capital_cost, errors="coerce"))
        carriers = frame.carrier if "carrier" in frame else pd.Series("unspecified", index=frame.index)
        for carrier, value in costs.groupby(carriers).sum().items():
            rows.append({"metric": "estimated_capital_cost", "component": component, "carrier": str(carrier), "value_eur": float(value), "basis": f"{nominal}_opt or {nominal} × capital_cost"})
    for component, dispatch_attr in (("generators", "p"), ("links", "p0"), ("storage_units", "p")):
        frame = _component(src, component)
        dispatch = _series(src, component, dispatch_attr)
        if frame.empty or dispatch.empty or "marginal_cost" not in frame:
            continue
        ids = frame.index.intersection(dispatch.columns)
        weights = _weights(src, dispatch.index)
        energy = dispatch[ids].mul(weights, axis=0).sum()
        costs = energy.mul(pd.to_numeric(frame.loc[ids, "marginal_cost"], errors="coerce"))
        carriers = frame.loc[ids, "carrier"] if "carrier" in frame else pd.Series("unspecified", index=ids)
        for carrier, value in costs.groupby(carriers).sum().items():
            rows.append({"metric": "estimated_dispatch_cost", "component": component, "carrier": str(carrier), "value_eur": float(value), "basis": f"weighted {dispatch_attr} × static marginal_cost"})
    objective = getattr(src.network, "objective", None) if getattr(src, "network", None) is not None else None
    if objective is not None and pd.notna(objective):
        rows.append({"metric": "network_objective", "component": "network", "carrier": "all", "value_eur": float(objective), "basis": "saved Network.objective; separate from estimated terms"})
    return pd.DataFrame(rows, columns=["metric", "component", "carrier", "value_eur", "basis"])


def plot_mix(table: pd.DataFrame, *, value: str, title: str, unit: str, style=None):
    """Plot a carrier mix table without imposing a project carrier taxonomy."""
    if table.empty or value not in table:
        raise ValueError(f"No {value} data")
    values = table.groupby("carrier")[value].sum().sort_values()
    # Omit only exact zeros; small or negative signed activity is substantive.
    values = values.loc[values.ne(0)]
    if values.empty:
        raise ValueError(f"No nonzero {value} values")
    style = style or PlotStyle()
    with style_context(style):
        fig, ax = plt.subplots(figsize=(9, max(4, 0.38 * len(values) + 1.4)))
        colors = [style.color(carrier) for carrier in values.index]
        ax.barh(values.index.astype(str), values.values, color=colors)
        largest = float(values.abs().max())
        for i, amount in enumerate(values.values):
            if amount != 0 and abs(amount) < .01 * largest:
                ax.annotate(f"{amount:,.3g} {unit}", (amount, i), xytext=(5 if amount > 0 else -5, 0),
                            textcoords="offset points", ha="left" if amount > 0 else "right",
                            va="center", fontsize=8, color="#3c4a56")
        ax.set(xlabel=unit, title=title)
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
    return fig


def plot_capacity_mix(capacity: pd.DataFrame, *, unit: str = "MW", style=None):
    """Plot MW or MWh capacities separately to prevent unit mixing."""
    selected = capacity.loc[capacity.unit.eq(unit)]
    return plot_mix(selected, value="capacity", title=f"Installed capacity ({unit})", unit=unit, style=style)


def plot_generation_mix(generation: pd.DataFrame, *, style=None):
    return plot_mix(generation, value="energy_mwh", title="Dispatched energy", unit="MWh", style=style)


def plot_curtailment(curtailment: pd.DataFrame, *, style=None):
    """Plot signed available-minus-dispatched energy by carrier."""
    if curtailment.empty:
        raise ValueError("Curtailment table is empty")
    return plot_mix(curtailment, value="curtailment_mwh_signed", title="Available minus dispatched energy", unit="MWh", style=style)


def plot_load_shedding(shedding: pd.DataFrame, *, style=None):
    """Plot identified shedding generator output by carrier."""
    if shedding.empty:
        raise ValueError("Shedding table is empty")
    return plot_mix(shedding, value="shedding_mwh", title="Load shedding", unit="MWh", style=style)


def plot_generation_vs_capacity(summary: pd.DataFrame, *, style=None):
    """Plot generator power and energy in separate aligned panels."""
    if summary.empty:
        raise ValueError("Generation versus capacity summary is empty")
    data = summary.sort_values("generation_mwh")
    style = style or PlotStyle()
    with style_context(style):
        fig, axes = plt.subplots(1, 2, figsize=(12, max(4, 0.35 * len(data) + 1.3)), sharey=True)
        colors = [style.color(c) for c in data.carrier]
        axes[0].barh(data.carrier, data.capacity_mw / 1e3, color=colors)
        axes[1].barh(data.carrier, data.generation_mwh / 1e6, color=colors)
        axes[0].set(xlabel="Installed power (GW)")
        axes[1].set(xlabel="Generation (TWh)")
        for ax in axes:
            ax.spines[["top", "right"]].set_visible(False)
        fig.suptitle("Generation and installed power")
        fig.tight_layout()
    return fig


def plot_cost_breakdown(costs: pd.DataFrame):
    """Plot estimated terms; annotate the saved objective distinctly."""
    terms = costs.loc[costs.metric.ne("network_objective")].copy()
    if terms.empty:
        raise ValueError("No estimated cost terms")
    grouped = terms.groupby(["metric", "component"]).value_eur.sum().sort_values()
    objective = costs.loc[costs.metric.eq("network_objective"), "value_eur"]
    with style_context():
        fig, ax = plt.subplots(figsize=(9, max(4, 0.4 * len(grouped) + 1)))
        ax.barh([f"{metric}: {component}" for metric, component in grouped.index], grouped.values / 1e6, color="#4477aa")
        ax.set(xlabel="Estimated cost (million EUR)", title="Indicative cost terms")
        if not objective.empty:
            ax.text(1, 0, f"Saved objective: {objective.iloc[0] / 1e6:,.1f} million EUR", transform=ax.transAxes, ha="right", va="bottom")
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
    return fig

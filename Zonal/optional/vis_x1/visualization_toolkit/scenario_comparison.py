"""Scenario comparisons from explicit metadata and tidy result metrics.

No scenario labels, years, market names, or technology classifications are
inferred from file or asset names. The caller supplies those as data.
"""

from __future__ import annotations

from collections.abc import Mapping

import matplotlib.pyplot as plt
import pandas as pd

from .styles import PlotStyle, style_context


def prepare_scenario_metrics(metrics: pd.DataFrame, scenarios: pd.DataFrame) -> pd.DataFrame:
    """Join a tidy metric table to one metadata row per scenario.

    ``metrics`` requires scenario_id, metric, value, unit and may include
    market, carrier, component, snapshot, or any other grouping dimensions.
    ``scenarios`` requires scenario_id and may include year, scenario_group,
    display_name and arbitrary extra descriptive fields.
    """
    required_metrics = {"scenario_id", "metric", "value", "unit"}
    required_scenarios = {"scenario_id"}
    if not required_metrics.issubset(metrics):
        raise ValueError(f"Metric table requires {sorted(required_metrics)}")
    if not required_scenarios.issubset(scenarios):
        raise ValueError("Scenario metadata requires scenario_id")
    if scenarios.scenario_id.isna().any() or scenarios.scenario_id.duplicated().any():
        raise ValueError("Scenario metadata must have unique non-null scenario_id")
    missing = set(metrics.scenario_id).difference(scenarios.scenario_id)
    if missing:
        raise ValueError(f"Missing metadata for scenarios: {sorted(missing)}")
    merged = metrics.merge(scenarios, on="scenario_id", how="left", validate="many_to_one", suffixes=("", "_scenario"))
    merged["scenario_label"] = merged["display_name"] if "display_name" in merged else merged.scenario_id
    merged["scenario_label"] = merged.scenario_label.fillna(merged.scenario_id).astype(str)
    merged["value"] = pd.to_numeric(merged.value, errors="raise")
    return merged


def metric_table(
    prepared: pd.DataFrame,
    metric: str,
    *,
    filters: Mapping[str, object] | None = None,
    category: str | None = None,
    aggregation: str = "sum",
) -> pd.DataFrame:
    """Aggregate a metric for plotting; refuse mixed units or implicit averaging."""
    data = prepared.loc[prepared.metric.eq(metric)].copy()
    for column, value in (filters or {}).items():
        if column not in data:
            raise KeyError(column)
        data = data.loc[data[column].eq(value)]
    if data.empty:
        raise ValueError(f"No observations for metric {metric}")
    if data.unit.nunique() != 1:
        raise ValueError(f"Metric {metric} has mixed units: {sorted(data.unit.unique())}")
    if category is not None and category not in data:
        raise KeyError(category)
    if aggregation not in {"sum", "mean", "median", "max", "min"}:
        raise ValueError("Choose sum, mean, median, max or min explicitly")
    groups = ["scenario_id", "scenario_label"] + ([category] if category else [])
    result = data.groupby(groups, as_index=False, dropna=False).value.agg(aggregation)
    result["metric"] = metric
    result["unit"] = data.unit.iloc[0]
    return result


def plot_scenario_comparison(
    prepared: pd.DataFrame,
    metric: str,
    *,
    filters: Mapping[str, object] | None = None,
    category: str | None = None,
    aggregation: str = "sum",
    scenario_order: list[str] | None = None,
    style=None,
    missing_category_as_zero: bool = False,
):
    """Bar comparison for any scenario list and one commensurate metric.

    Prices ordinarily call for ``aggregation='mean'`` or ``'median'``;
    energy/capacity totals ordinarily call for ``'sum'``. The choice is
    explicit so no metric name silently controls the calculation.
    """
    table = metric_table(prepared, metric, filters=filters, category=category, aggregation=aggregation)
    order = scenario_order or prepared.scenario_id.drop_duplicates().tolist()
    unknown = set(order).difference(table.scenario_id)
    if unknown:
        raise ValueError(f"Requested scenarios have no metric data: {sorted(unknown)}")
    table = table.loc[table.scenario_id.isin(order)]
    labels = table.drop_duplicates("scenario_id").set_index("scenario_id").scenario_label.reindex(order)
    if labels.isna().any():
        raise ValueError("Scenario order includes missing values")
    style = style or PlotStyle()
    with style_context(style):
        fig, ax = plt.subplots(figsize=(max(8, 1.25 * len(order)), 5))
        if category:
            pivot = table.pivot(index="scenario_id", columns=category, values="value").reindex(order)
            if pivot.isna().any().any():
                if not missing_category_as_zero:
                    raise ValueError("Missing scenario/category metrics; set missing_category_as_zero=True only when absence means zero")
                pivot = pivot.fillna(0)
            colors = [style.color(str(c)) for c in pivot.columns]
            pivot.plot.bar(ax=ax, stacked=True, width=0.75, color=colors)
            ax.legend(title=category, frameon=False, bbox_to_anchor=(1.02, 1), loc="upper left")
        else:
            values = table.set_index("scenario_id").value.reindex(order)
            ax.bar(range(len(order)), values, color=[style.scenario_color(sid) for sid in order])
        ax.set_xticks(range(len(order)), labels, rotation=30, ha="right")
        ax.set(xlabel="", ylabel=table.unit.iloc[0], title=metric.replace("_", " ").title())
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
    return fig


def plot_scenario_distribution(
    prepared: pd.DataFrame,
    metric: str,
    *,
    filters: Mapping[str, object] | None = None,
    scenario_order: list[str] | None = None,
    show_outliers: bool = False,
    style=None,
):
    """Box distributions for raw per-snapshot or per-asset observations.

    This function does not infer weight/representative-hour probabilities; for
    those, provide suitably weighted quantiles from the source model instead.
    """
    data = prepared.loc[prepared.metric.eq(metric)].copy()
    for column, value in (filters or {}).items():
        if column not in data:
            raise KeyError(column)
        data = data.loc[data[column].eq(value)]
    if data.empty or data.unit.nunique() != 1:
        raise ValueError("Distribution requires observations for one unit")
    order = scenario_order or data.scenario_id.drop_duplicates().tolist()
    samples = [data.loc[data.scenario_id.eq(sid), "value"].dropna().to_numpy() for sid in order]
    if any(len(sample) == 0 for sample in samples):
        raise ValueError("Every selected scenario requires observations")
    labels = data.drop_duplicates("scenario_id").set_index("scenario_id").scenario_label.reindex(order)
    style = style or PlotStyle()
    with style_context(style):
        fig, ax = plt.subplots(figsize=(max(8, 1.2 * len(order)), 5))
        box = ax.boxplot(samples, tick_labels=labels.tolist(), showfliers=show_outliers, patch_artist=True, medianprops={"color": "#1f4e79"})
        for patch, sid in zip(box["boxes"], order):
            patch.set_facecolor(style.scenario_color(sid))
        ax.set(ylabel=data.unit.iloc[0], title=f"{metric.replace('_', ' ').title()} distribution")
        ax.tick_params(axis="x", rotation=30)
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
    return fig

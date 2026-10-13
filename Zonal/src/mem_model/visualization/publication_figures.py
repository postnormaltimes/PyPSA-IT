"""Publication coordination of saved MEM reports; no model or network entrypoint.

``render`` returns an unsaved matplotlib figure, its long-form plotted data,
JSON-compatible numerical QA, a caption, and the exact tables read.  The caller
owns case selection, receipt/pin verification and export.  Source definitions
remain those of canonical UC physical reporting and fixed-commitment LP prices.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import Normalize, TwoSlopeNorm
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, FancyArrowPatch
import numpy as np
import pandas as pd
import yaml

from .vis_x1 import CONFIG, ROOT, ZONE_ORDER, _sha, _toolkit
from .uc2_comparison import _comparison_style
from ..reporting.canonical_results import reporting_config
from ..reporting.marginal_setter_lexicographic import ORDER as REGIMES, VERSION, zone_shares

SCENARIOS = ("Slow", "Base", "High")
ZONES = tuple("CNORD" if z == "CNOR" else z for z in ZONE_ORDER)
PRICE_WEIGHT = "UC rigid end-use load + UC P2X electrical withdrawal"
PRICE_AUTHORITY = "FIXED_COMMITMENT_PRICE_LP_DUALS"
PHYSICAL_AUTHORITY = "UC_MILP"
LINESTYLES = {"Slow": "-", "Base": "--", "High": ":"}
MARKERS = {"Slow": "o", "Base": "s", "High": "^"}


def _alias(value):
    return "CNORD" if value == "CNOR" else value


class _Inputs:
    def __init__(self):
        self.sources = []
        self.checks = []
        self.gaps = []

    def read(self, path):
        path = Path(path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Required publication source missing: {path}")
        if path not in self.sources:
            self.sources.append(path)
        if path.suffix == ".parquet":
            return pd.read_parquet(path)
        if path.suffix == ".json":
            return json.loads(path.read_text(encoding="utf-8"))
        return pd.read_csv(path, float_precision="round_trip")

    def stat(self, case, name):
        return self.read(Path(case["report"]) / "CANONICAL/statistics" / name)

    def check(self, name, detail, **values):
        self.checks.append({"check": name, "status": "PASS", "detail": detail, **values})


def _ordered(cases):
    if not cases:
        raise ValueError("At least one caller-verified publication case required")
    result = sorted(cases, key=lambda c: (int(c["year"]), SCENARIOS.index(c["scenario"])))
    keys = [(int(c["year"]), c["scenario"]) for c in result]
    if len(set(keys)) != len(keys):
        raise ValueError("Publication cases must have unique year/scenario keys")
    return result


def _scenarios(data, year=None):
    values = data if year is None else data.loc[data.year.eq(year)]
    return [s for s in SCENARIOS if s in set(values.scenario)]


@lru_cache(maxsize=1)
def _style():
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    _toolkit(cfg)
    from visualization_toolkit.styles import PlotStyle
    style = _comparison_style(pd.DataFrame({"scenario_id": SCENARIOS}), PlotStyle)
    fonts = {font.name for font in font_manager.fontManager.ttflist}
    font = "Aptos" if "Aptos" in fonts else "DejaVu Sans"
    return cfg, style, font


def _decorate(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#E6E8EB", linewidth=.55)
    ax.set_axisbelow(True)


def _header(fig, title, subtitle, *, top=.88, bottom=.14):
    fig.suptitle(title, x=.055, ha="left", y=.98, fontsize=16, fontweight="bold")
    fig.text(.055, .935, subtitle, fontsize=9.5, color="#586574", va="top")
    # Reserve physical space for the subtitle, axis title and title padding.
    # A fractional gap alone shrinks when a singleton panel has a short canvas.
    safe_top = min(top, .935 - .52 / fig.get_figheight())
    fig.subplots_adjust(left=.07, right=.96, top=safe_top, bottom=bottom, wspace=.28, hspace=.38)


def _legend(fig, style, *, y=.035, scenarios=SCENARIOS):
    handles = [Line2D([], [], color=style.scenario_color(s), marker=MARKERS[s],
                      linestyle=LINESTYLES[s], linewidth=1.9, label=s) for s in scenarios]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(.5, y),
               ncol=3, frameon=False)


def _row(case, metric, value, unit, **dimensions):
    price_metric = "price" in metric or "regime" in metric or metric == "any_Italian_zone_at_15000"
    dimensions.setdefault("source_authority", PRICE_AUTHORITY if price_metric else PHYSICAL_AUTHORITY)
    return dict(year=int(case["year"]), scenario=case["scenario"],
                variant=str(case.get("variant", "")), metric=metric,
                value=float(value), unit=unit, **dimensions)


def _annual(case, inp):
    monthly = inp.stat(case, "uc2_price_monthly_national_load_weighted.csv")
    if (len(monthly) != 12 or set(monthly.month) != set(range(1, 13)) or
            monthly.month.duplicated().any()):
        raise ValueError("Exactly twelve unique canonical national monthly prices required")
    weights = pd.to_numeric(monthly.rigid_load_plus_P2X_MWh, errors="raise")
    prices = pd.to_numeric(monthly.load_weighted_mean_EUR_per_MWh, errors="raise")
    if not np.isfinite(weights).all() or not weights.gt(0).all() or not np.isfinite(prices).all():
        raise ValueError("Finite canonical prices and positive gross end-use weights required")
    value = float(np.average(prices, weights=weights))
    inp.check("annual_gross_end_use_weighting", "Weighted twelve saved monthly numerators/denominators",
              year=int(case["year"]), scenario=case["scenario"],
              annual_EUR_per_MWh=value, denominator_MWh=float(weights.sum()))
    return value, monthly


def _hourly_prices(case, inp):
    frame = inp.read(Path(case["report"]) / "fixed_commitment_prices_hourly.csv")
    if "snapshot" not in frame:
        raise ValueError("Saved fixed-LP price timestamps required")
    index = pd.DatetimeIndex(pd.to_datetime(frame.snapshot, errors="raise"))
    if len(index) != 8760 or not index.is_unique or not index.is_monotonic_increasing:
        raise ValueError("8760 chronological, unique fixed-LP hours required")
    if not np.all(np.diff(index.asi8) == pd.Timedelta(hours=1).value):
        raise ValueError("Continuous one-hour chronology required")
    values = frame[list(ZONE_ORDER)].apply(pd.to_numeric, errors="raise")
    values.columns = ZONES
    values.index = index
    if not np.isfinite(values.to_numpy()).all():
        raise ValueError("Nonfinite fixed-LP prices")
    return values


def _duration_weights(case, inp, index):
    frame = inp.read(Path(case["report"]) / "VIS_X1/data/fixed_commitment_lp_price_duration.parquet")
    if frame.market.nunique() != 7:
        raise ValueError("Seven stored UC generator duration weight series required")
    for _, group in frame.groupby("market"):
        timestamps = pd.DatetimeIndex(pd.to_datetime(group.snapshot)).sort_values()
        if not timestamps.equals(index) or not pd.to_numeric(group.weight).eq(1).all():
            raise ValueError("Price-duration hours require aligned, equal one-hour UC generator weights")
    inp.check("full_year_one_hour_duration", "Seven aligned stored UC duration series", hours=8760,
              year=int(case["year"]), scenario=case["scenario"])


def _history(cases, historical, year):
    source = historical if historical is not None else cases
    return sorted([c for c in source if int(c["year"]) == year],
                  key=lambda c: (str(c.get("variant", "")), SCENARIOS.index(c["scenario"])))


def _evolution(cases, historical, inp, style, year, three_panels=False):
    selected = _history(cases, historical, year)
    variants = sorted({str(c.get("variant", "")) for c in selected
                       if c.get("report") is not None and c.get("available") is not False})
    if not variants:
        raise ValueError(f"Historical case descriptors required for {year}")
    rows = []
    for case in selected:
        missing = case.get("report") is None or case.get("available") is False
        metrics = [("annual_national_price", "EUR/MWh")]
        if three_panels:
            metrics += [("UC_unserved_energy", "GWh"), ("any_Italian_zone_at_15000", "hours")]
        else:
            metrics += [("annual_gross_end_use_demand", "TWh"), ("VRE_curtailed_energy", "TWh"),
                        ("wind_available_energy", "TWh"), ("wind_dispatched_energy", "TWh"), ("wind_curtailed_energy", "TWh")]
        if missing:
            inp.gaps.append(dict(year=year, scenario=case["scenario"], variant=case.get("variant"),
                                 reason=case.get("unavailable_reason", "Historical solved report unavailable")))
            for metric, unit in metrics:
                rows.append(_row(case, metric, np.nan, unit, status="UNAVAILABLE"))
            continue
        price, monthly = _annual(case, inp)
        rows.append(_row(case, "annual_national_price", price, "EUR/MWh", status="AVAILABLE"))
        if not three_panels:
            curtailment = inp.read(Path(case["report"]) / "vre_curtailment.csv")
            curtailed = pd.to_numeric(curtailment.curtailed_MWh, errors="raise")
            if not np.isfinite(curtailed).all() or (curtailed < -1e-5).any():
                raise ValueError("Finite nonnegative saved VRE curtailed energy required")
            demand_TWh = float(monthly.rigid_load_plus_P2X_MWh.sum()/1e6)
            curtailed_TWh = float(curtailed.sum()/1e6)
            rows.extend([_row(case, "annual_gross_end_use_demand", demand_TWh, "TWh", status="AVAILABLE",
                              definition=PRICE_WEIGHT),
                         _row(case, "VRE_curtailed_energy", curtailed_TWh, "TWh", status="AVAILABLE")])
            wind = curtailment.loc[curtailment.carrier.isin(["wind_onshore", "wind_offshore"])]
            for column, metric in (("available_MWh", "wind_available_energy"),
                                   ("dispatched_MWh", "wind_dispatched_energy"), ("curtailed_MWh", "wind_curtailed_energy")):
                rows.append(_row(case, metric, wind[column].sum()/1e6, "TWh", status="AVAILABLE"))
            inp.check("evolution_supporting_saved_indicators", "Saved monthly gross demand denominator and saved VRE curtailed MWh converted to TWh",
                      variant=str(case.get("variant")), scenario=case["scenario"],
                      annual_gross_end_use_demand_TWh=demand_TWh, VRE_curtailed_energy_TWh=curtailed_TWh)
        if three_panels:
            balance = inp.stat(case, "annual_electrical_balance_by_zone.csv")
            zones = balance.zone.map(_alias)
            if set(zones) != set(ZONES) or zones.duplicated().any():
                raise ValueError("Seven unique Italian physical balance zones required")
            shed = float(balance.load_shedding_TWh.sum() * 1000)
            prices = _hourly_prices(case, inp)
            count = int(np.isclose(prices.to_numpy(), 15000., atol=1e-6, rtol=0).any(axis=1).sum())
            rows.extend([_row(case, "UC_unserved_energy", shed, "GWh", status="AVAILABLE"),
                         _row(case, "any_Italian_zone_at_15000", count, "hours", status="AVAILABLE")])
            inp.check("scarcity_unique_hours", "Any Italian zone at EUR 15000; unique snapshots, not zone-hours",
                      variant=str(case.get("variant")), scenario=case["scenario"], hours=count)
    data = pd.DataFrame(rows)
    metric_order = ["annual_national_price", "UC_unserved_energy", "any_Italian_zone_at_15000"] if three_panels else ["annual_national_price"]
    fig, axes = plt.subplots(1, len(metric_order), figsize=(15, 6.1) if three_panels else (10.5, 8.5), squeeze=False)
    titles = ["National weighted LP price", "UC physical unserved energy", "Unique Italian scarcity hours"]
    for ax, metric, title in zip(axes[0], metric_order, titles):
        for scenario in SCENARIOS:
            values = data.loc[data.scenario.eq(scenario) & data.metric.eq(metric)].set_index("variant").value.reindex(variants)
            ax.plot(range(len(variants)), values, color=style.scenario_color(scenario),
                    marker=MARKERS[scenario], linestyle=LINESTYLES[scenario], linewidth=2)
            for i, value in enumerate(values):
                if np.isfinite(value) and metric != "annual_national_price" and not (three_panels and value == 0):
                    # Price curves in the compact three-panel view intentionally
                    # use the axis rather than crowded near-equal point labels.
                    # The exact values remain in its linked CSV. Coincident
                    # all-zero adequacy/scarcity outcomes are labelled once below.
                    ax.annotate(f"{value:.1f}" if metric == "annual_national_price" else f"{value:.3g}",
                                (i, value), xytext=(0, 6 + SCENARIOS.index(scenario)*14 if value == 0 else 6),
                                textcoords="offset points", ha="center", fontsize=9,
                                color=style.scenario_color(scenario))
        ax.set_xticks(range(len(variants)), [v.replace("final_", "") for v in variants])
        ax.set_title(title, fontsize=12)
        ax.set_ylabel("EUR/MWh" if metric == "annual_national_price" else "GWh" if metric == "UC_unserved_energy" else "Unique hours/year")
        if metric != "annual_national_price":
            ax.set_ylim(bottom=0)
            if data.loc[data.metric.eq(metric), "value"].max() == 0:
                ax.set_ylim(0, 1)
                ax.text(.5, .55, "All scenarios: 0", transform=ax.transAxes, ha="center", fontsize=11)
        else:
            if three_panels:
                ax.margins(y=.22)
            else:
                ax.set_ylim(0, data.loc[data.metric.eq(metric), "value"].max()*1.17)
        _decorate(ax)
        for i, variant in enumerate(variants):
            outcomes = data.loc[data.variant.eq(variant) & data.metric.eq(metric), "value"]
            if three_panels and metric != "annual_national_price" and outcomes.notna().all() and outcomes.eq(0).all():
                ax.annotate("All: 0", (i, 0), xytext=(0, 7), textcoords="offset points",
                            ha="center", fontsize=9, color="#586574")
            if data.loc[data.variant.eq(variant) & data.metric.eq(metric), "value"].isna().all():
                ax.axvspan(i-.12, i+.12, color="#F0F1F3", zorder=0)
                ax.text(i, .15, "N/A\nsource unavailable", transform=ax.get_xaxis_transform(),
                        ha="center", fontsize=9, color="#586574")
    note = ("v4 → v5: OCGT reinforcement.  v5 → v6: external market input at EUR 400/MWh; no Italian price cap."
            if three_panels else
            "v1 → v3 jointly includes P2X relocation plus wind availability/productivity and curtailment correction.")
    _header(fig, f"{year} model evolution", f"Annual national prices weighted by {PRICE_WEIGHT}", bottom=.23 if three_panels else .44)
    if not three_panels:
        table_ax = fig.add_axes([.12, .20, .80, .16])
        table_ax.axis("off")
        available = data.loc[data.status.eq("AVAILABLE")]
        table_rows = []
        table_columns = ["v1 price\n[EUR/MWh]", "v3 price\n[EUR/MWh]", "Price change\n[EUR/MWh]", "Price change\n[%]",
                         "Wind available\nv1 → v3 [TWh]", "VRE curtailed\nv1 → v3 [TWh]"]
        scenario_order = _scenarios(available)
        for scenario in scenario_order:
            first = available.loc[available.scenario.eq(scenario) & available.variant.eq("final_v1")].set_index("metric").value
            last = available.loc[available.scenario.eq(scenario) & available.variant.eq("final_v3")].set_index("metric").value
            old, new = first.get("annual_national_price", np.nan), last.get("annual_national_price", np.nan)
            delta = new-old
            percent = 100*delta/old if np.isfinite(old) and old != 0 else np.nan
            values = [f"{v:.2f}" if np.isfinite(v) else "N/A" for v in (old, new, delta, percent)]
            for metric in ("wind_available_energy", "VRE_curtailed_energy"):
                values.append(f"{first.get(metric, np.nan):.1f} → {last.get(metric, np.nan):.1f}")
            table_rows.append(values)
            for metric, value, unit in (("price_change_v1_to_v3", delta, "EUR/MWh"), ("price_change_percent_v1_to_v3", percent, "%")):
                rows.append(_row(dict(year=year, scenario=scenario, variant="final_v1_to_final_v3"), metric, value, unit, status="DERIVED_COMPARISON"))
        data = pd.DataFrame(rows)
        table = table_ax.table(cellText=table_rows, colLabels=table_columns, rowLabels=scenario_order,
                               cellLoc="center", rowLoc="center", bbox=[0, 0, 1, 1])
        table.auto_set_font_size(False)
        table.set_fontsize(9)
        for (row, column), cell in table.get_celld().items():
            cell.set_edgecolor("#E6E8EB")
            cell.set_linewidth(.5)
            if row == 0:
                cell.set_facecolor("#F3F5F7")
                cell.set_text_props(fontweight="bold")
        fig.text(.12, .38, "Absolute prices use a zero-origin axis; exact changes and physical wind/curtailment evidence are shown below.", fontsize=9, color="#586574")
        fig.text(.055, .165, "v2 P2X relocation milestone: prepared, not executed; no numerical result.", fontsize=9, color="#586574")
    fig.text(.055, .13, note, fontsize=9)
    fig.text(.055, .10, "Version differences show joint model changes; they do not isolate causal contributions.", fontsize=9, color="#586574")
    _legend(fig, style, y=.01, scenarios=_scenarios(data))
    caption = (f"{year} annual national fixed-commitment LP prices use gross end-use electricity (UC rigid load plus P2X) as weights. "
               + note + " Version differences are joint effects, not causal attribution. Missing historical sources remain gaps.")
    if not three_panels:
        caption += " The zero-origin price axis and table show absolute/percentage price changes, physical wind availability and VRE curtailment. Gross demand weights and wind dispatch/curtailment remain in the supporting CSV; v2 was prepared but not executed and has no numerical values."
    return fig, data, caption


def _heatmaps(cases, inp):
    rows = []
    for case in cases:
        annual = inp.stat(case, "uc2_price_annual_summary.csv")
        annual = annual.loc[annual.market.isin(ZONE_ORDER)].copy()
        annual["zone"] = annual.market.map(_alias)
        if set(annual.zone) != set(ZONES) or annual.zone.duplicated().any():
            raise ValueError("Exactly seven zonal prices required")
        for row in annual.itertuples():
            rows.append(_row(case, "annual_zonal_gross_load_weighted_price", row.load_weighted_mean_EUR_per_MWh,
                             "EUR/MWh", zone=row.zone, source_authority=PRICE_AUTHORITY))
    data = pd.DataFrame(rows)
    years = sorted(data.year.unique())
    fig, axes = plt.subplots(1, len(years), figsize=(13, 5.5), squeeze=False)
    norm = Normalize(data.value.min(), data.value.max())
    for ax, year in zip(axes[0], years):
        scenario_order = _scenarios(data, year)
        pivot = data.loc[data.year.eq(year)].pivot(index="scenario", columns="zone", values="value").reindex(index=scenario_order, columns=ZONES)
        image = ax.imshow(pivot, cmap="YlOrRd", norm=norm, aspect="auto")
        ax.set_xticks(range(7), ZONES)
        ax.set_yticks(range(len(scenario_order)), scenario_order)
        ax.set_title(str(year), fontsize=13)
        for i in range(len(scenario_order)):
            for j in range(7):
                value = float(pivot.iloc[i, j])
                extreme = np.isclose(value, pivot.to_numpy().min()) or np.isclose(value, pivot.to_numpy().max())
                ax.text(j, i, f"{value:.1f}", ha="center", va="center", fontsize=10,
                        fontweight="bold" if extreme else "normal", color="white" if norm(value) > .58 else "#263442")
        ax.text(.0, -.23, f"Min {pivot.min().min():.1f} · Max {pivot.max().max():.1f} EUR/MWh", transform=ax.transAxes, fontsize=9)
    _header(fig, "Annual zonal electricity prices", "Gross end-use load weighted fixed-commitment LP prices · common colour scale", bottom=.22)
    fig.colorbar(image, ax=list(axes[0]), shrink=.7, pad=.025, label="EUR/MWh")
    inp.check("zonal_heatmap_shared_scale", "Two horizons share the complete minimum/maximum colour domain",
              minimum=float(data.value.min()), maximum=float(data.value.max()), rows=len(data))
    return fig, data, "Annual zonal fixed-commitment LP prices, weighted by UC rigid electricity load plus P2X. All seven zones and Slow/Base/High scenarios share one colour scale; bold cells identify each horizon's extrema."


def _monthly(cases, inp, style, layout="shared"):
    from .publication_price_atlas import monthly_price_statistics
    statistics = monthly_price_statistics(cases)
    for path in statistics["sources"]:
        if path not in inp.sources:
            inp.sources.append(path)
    inp.checks.extend(statistics["qa"]["checks"])
    data = statistics["data"].copy()
    data["metric"] = "monthly_national_price"
    data["value"] = data.weighted_mean_EUR_per_MWh
    data["unit"] = "EUR/MWh"
    data["source_authority"] = PRICE_AUTHORITY
    years = sorted(data.year.unique())
    if layout not in {"shared", "scenario_panels"}:
        raise ValueError("MONTHLY_PUBLICATION_LAYOUT_UNKNOWN")
    separate = layout == "scenario_panels"
    scenarios = _scenarios(data)
    fig, axes = plt.subplots(len(years) if separate else 1, len(scenarios) if separate else len(years),
        figsize=(14, 4.1*len(years)+1) if separate else (13, 5.6), squeeze=False, sharey=True)
    panels = [(axes[i, j], year, [scenario]) for i, year in enumerate(years) for j, scenario in enumerate(scenarios)] if separate else [(ax, year, scenarios) for ax, year in zip(axes[0], years)]
    for ax, year, panel_scenarios in panels:
        for scenario in panel_scenarios:
            group = data.loc[data.year.eq(year) & data.scenario.eq(scenario)].sort_values("month")
            ax.fill_between(group.month, group.p25_EUR_per_MWh, group.p75_EUR_per_MWh,
                            color=style.scenario_color(scenario), alpha=.12, linewidth=0)
            ax.plot(group.month, group.value, color=style.scenario_color(scenario), marker=MARKERS[scenario],
                    linestyle=LINESTYLES[scenario], linewidth=1.9, markersize=4)
        ax.set(title=f"{year} · {panel_scenarios[0]}" if separate else str(year), ylabel="EUR/MWh", xlabel="Month")
        ax.set_xticks(range(1, 13), ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
        _decorate(ax)
    _header(fig, "Monthly national electricity prices", "Lines: volume-weighted purchasing price · bands: P25–P75 of hourly national weighted-index observations")
    if not separate:
        _legend(fig, style, scenarios=scenarios)
    else:
        fig.text(.055, .035, "Same vertical scale in every panel · hourly index percentiles are observation spread, not uncertainty", fontsize=9, color="#586574")
    inp.check("monthly_hourly_distribution_envelope", "Percentiles use contemporaneously load-weighted national hourly observations; canonical purchasing means remain volume weighted; P5/P50/P95 retained in CSV", interval="P25–P75; observation spread, not uncertainty")
    return fig, data, "Lines preserve canonical monthly purchasing prices: zonal fixed-LP prices weighted by contemporaneous UC rigid electricity load plus P2X and physical duration. Bands show the P25–P75 distribution of the hourly national load-weighted index, not zonal averages or confidence intervals. P5/P50/P95 and time-weighted means remain in the CSV; time-weighted index means differ from volume-weighted purchasing means."


def _duration(cases, inp, style):
    rows = []
    extrema = []
    for case in cases:
        table = inp.stat(case, "uc2_price_hourly_all_italy_spread.parquet")
        if "snapshot" in table:
            table = table.set_index("snapshot")
        index = pd.DatetimeIndex(pd.to_datetime(table.index))
        if len(index) != 8760 or not index.is_unique or not index.is_monotonic_increasing:
            raise ValueError("Full national 8760-hour chronology required")
        _duration_weights(case, inp, index)
        values = pd.to_numeric(table.load_weighted_national_EUR_per_MWh, errors="raise").to_numpy()
        if not np.isfinite(values).all():
            raise ValueError("All hourly national prices must be finite")
        ranked = np.sort(values)[::-1]
        for rank, value in enumerate(ranked, 1):
            rows.append(_row(case, "national_price_duration", value, "EUR/MWh", rank_hour=rank))
        extrema.append(dict(year=int(case["year"]), scenario=case["scenario"],
                            minimum=float(values.min()), maximum=float(values.max()), negative_hours=int((values < 0).sum())))
    data = pd.DataFrame(rows)
    years = sorted(data.year.unique())
    fig, axes = plt.subplots(1, len(years), figsize=(14, 6.5), squeeze=False)
    low, high = float(data.value.min()), float(data.value.max())
    for ax, year in zip(axes[0], years):
        # Upper-right space above the low-price tail; the inset does not mask
        # main curves. Verify the clearance rather than assuming it.
        rectangle = [.53, .62, .43, .29]
        inset = ax.inset_axes(rectangle)
        for scenario in _scenarios(data, year):
            group = data.loc[data.year.eq(year) & data.scenario.eq(scenario)]
            kwargs = dict(color=style.scenario_color(scenario), linestyle=LINESTYLES[scenario], linewidth=1.8)
            ax.plot(group.rank_hour, group.value, **kwargs)
            tail = group.loc[group.rank_hour.le(200)]
            inset.plot(tail.rank_hour, tail.value, **kwargs)
            ax.plot([1, 8760], [group.value.iloc[0], group.value.iloc[-1]], linestyle="", marker=MARKERS[scenario],
                    markersize=4, color=style.scenario_color(scenario))
        local = data.loc[data.year.eq(year)]
        lo, hi = float(local.value.min()), float(local.value.max())
        lower = min(0., lo*1.12)
        upper = max(175., hi*1.18)
        ax.set_xlim(1, 8760)
        ax.set_ylim(lower, upper)
        ticks = [v for v in (0, 10, 30, 60, 100, 150, 200, 250, 300, 350, 400) if lower <= v <= upper]
        if lower < 0:
            ticks = sorted(set([lower] + ticks))
        ax.set_yticks(ticks)
        ax.set_xticks([1, 2000, 4000, 6000, 8760])
        ax.set(title=str(year), xlabel="Descending rank · all 8,760 hours", ylabel="EUR/MWh · linear scale")
        covered = local.loc[local.rank_hour.between(1+rectangle[0]*8759, 1+(rectangle[0]+rectangle[2])*8759)]
        inset_bottom = lower+rectangle[1]*(upper-lower)
        if covered.value.max() >= inset_bottom:
            raise ValueError("PRICE_DURATION_INSET_REQUIRES_UNUSED_SPACE: main curve would be covered")
        inp.check("price_duration_inset_clearance", "Inset placed above every main-curve observation under its horizontal footprint",
                  year=int(year), curve_max_EUR_per_MWh=float(covered.value.max()), inset_bottom_EUR_per_MWh=inset_bottom)
        ax.axhline(0, color="#697386", linewidth=.6)
        inset.set(title="Highest 200 hours · linear EUR/MWh", xlim=(1, 200))
        inset.set_xticks([1, 100, 200])
        inset.tick_params(labelsize=8, pad=2)
        inset.set_title("Highest 200 hours · linear EUR/MWh", fontsize=8)
        inset.grid(color="#E6E8EB", linewidth=.5)
        _decorate(ax)
        notes = [f"{r['scenario']}: {r['minimum']:.1f} to {r['maximum']:.1f}" for r in extrema if r["year"] == year]
        ax.text(0, -.19, "Range [EUR/MWh]  " + " · ".join(notes), transform=ax.transAxes, fontsize=8)
    _header(fig, "National electricity price duration", "Full hourly national gross end-use load weighted LP prices · negative and positive extrema retained", bottom=.22)
    _legend(fig, style, y=.015, scenarios=_scenarios(data))
    inp.check("duration_full_series_extrema", "All 8760 ranked values plotted; no trimming or winsorisation", extrema=extrema,
              vertical_scale="Linear EUR/MWh; horizon-specific limits explicitly shown; linear highest-200-hour inset")
    return fig, data, "All 8,760 hourly national fixed-commitment LP prices are weighted across Italian zones by contemporaneous UC rigid load plus P2X. Descending duration curves retain every observation and all extrema. Main axes are linear with explicitly displayed horizon-specific limits; insets show the highest 200 hours and lie in verified unused plot space."


def _stacked(ax, data, categories, colors, metric, year, ylabel):
    group = data.loc[data.year.eq(year) & data.metric.eq(metric)]
    scenarios = _scenarios(data, year)
    pivot = group.pivot(index="scenario", columns="category", values="value").reindex(index=scenarios, columns=categories).fillna(0)
    positive = np.zeros(len(scenarios)); negative = np.zeros(len(scenarios))
    for category in categories:
        values = pivot[category].to_numpy()
        bottoms = np.where(values >= 0, positive, negative)
        ax.bar(range(len(scenarios)), values, bottom=bottoms, color=colors[category], width=.66, linewidth=.25, edgecolor="white")
        positive += np.maximum(values, 0); negative += np.minimum(values, 0)
    ax.set_xticks(range(len(scenarios)), scenarios)
    ax.set_ylabel(ylabel)
    ax.axhline(0, color="#697386", linewidth=.6)
    _decorate(ax)


def _capacity_generation(cases, inp, style):
    rc = reporting_config(); rows = []
    for case in cases:
        for filename, column, metric, unit in (
                ("installed_capacity_national.csv", "installed_capacity_GW", "installed_capacity", "GW"),
                ("annual_primary_generation_national.csv", "annual_primary_generation_TWh", "primary_generation", "TWh")):
            frame = inp.stat(case, filename)
            if frame.display_technology.duplicated().any():
                raise ValueError("Unique canonical national technology totals required")
            if metric == "primary_generation" and frame.display_technology.isin(["BESS", "PHS"]).any():
                raise ValueError("Storage discharge must not enter primary generation")
            for row in frame.itertuples():
                rows.append(_row(case, metric, getattr(row, column), unit, category=row.display_technology,
                                 source_authority=PHYSICAL_AUTHORITY))
        balance = inp.stat(case, "annual_electrical_balance_by_zone.csv")
        for category, col in (("BESS discharge", "bess_discharge_TWh"), ("PHS discharge", "phs_discharge_TWh"), ("Net imports", "net_imports_TWh")):
            rows.append(_row(case, "nonprimary_supply", balance[col].sum(), "TWh", category=category, source_authority=PHYSICAL_AUTHORITY))
        primary = sum(r["value"] for r in rows if r["year"] == int(case["year"]) and r["scenario"] == case["scenario"] and r["metric"] == "primary_generation")
        np.testing.assert_allclose(primary, balance.primary_generation_TWh.sum(), atol=1e-6, rtol=0)
        inp.check("primary_generation_storage_separation", "Primary technology totals reconcile to UC balance; storage and imports separated",
                  year=int(case["year"]), scenario=case["scenario"], primary_generation_TWh=float(primary))
    data = pd.DataFrame(rows)
    technologies = [t for t in rc["technology_order"] if t in set(data.loc[data.metric.ne("nonprimary_supply"), "category"])]
    missing = set(data.loc[data.metric.ne("nonprimary_supply"), "category"]) - set(technologies)
    if missing:
        raise ValueError(f"Unmapped publication technology palette: {missing}")
    extra = {"BESS discharge": rc["technology_colors"]["BESS"], "PHS discharge": rc["technology_colors"]["PHS"], "Net imports": "#7b8993"}
    years = sorted(data.year.unique())
    fig, axes = plt.subplots(len(years), 3, figsize=(15, 10), squeeze=False, sharey="col")
    for axes_row, year in zip(axes, years):
        for ax, metric, title, unit in zip(axes_row, ["installed_capacity", "primary_generation", "nonprimary_supply"],
                                         ["Installed electrical capacity", "Primary electricity generation", "Storage / imports supply contribution"], ["GW", "TWh/year", "TWh/year · net imports signed"]):
            _stacked(ax, data, list(extra) if metric == "nonprimary_supply" else technologies,
                     extra if metric == "nonprimary_supply" else rc["technology_colors"], metric, year, unit)
            ax.set_title(f"{year} · {title}", fontsize=11)
    _header(fig, "Capacity and annual electricity supply", "UC physical results · primary generation excludes BESS/PHS discharge and international trade", top=.87, bottom=.23)
    # Organize the legend, retaining every individual technology and colour.
    # This is a label hierarchy; it does not aggregate the underlying bars.
    groups = [
        ("Solar / wind", ["Solar PV - rooftop", "Solar PV - utility", "Onshore wind", "Offshore wind"]),
        ("Hydro / storage", ["Hydro - run of river", "Hydro - basin/pondage", "Hydro - reservoir", "PHS", "BESS"]),
        ("Thermal", ["Gas - CCGT", "Gas - OCGT", "Gas - internal combustion", "Gas - steam thermal", "Gas - other thermal", "Gas + CCS", "Hydrogen - OCGT"]),
        ("Other primary generation", ["Nuclear", "Bioenergy", "Bioenergy - internal combustion", "Bioenergy - steam thermal", "Bioenergy - other thermal", "Bioenergy + CCS", "Geothermal"]),
    ]
    display_labels = {"Gas - internal combustion": "Gas · internal combustion", "Gas - steam thermal": "Gas · steam thermal",
        "Gas - other thermal": "Gas · other thermal", "Bioenergy - internal combustion": "Bioenergy · internal combustion",
        "Bioenergy - steam thermal": "Bioenergy · steam thermal", "Bioenergy - other thermal": "Bioenergy · other thermal"}
    listed = []
    fig.subplots_adjust(bottom=.26)
    for x, (title, keys) in zip((.06, .27, .51, .76), groups):
        active = [key for key in keys if key in technologies]
        listed.extend(active)
        legend = fig.legend(handles=[Patch(color=rc["technology_colors"][key], label=display_labels.get(key, key)) for key in active],
            title=title, loc="upper left", bbox_to_anchor=(x, .235), fontsize=9.1, title_fontsize=10,
            frameon=False, handlelength=1.35, handletextpad=.55, borderaxespad=0)
        legend.get_title().set_fontweight("bold")
    if set(listed) != set(technologies) or len(listed) != len(technologies):
        raise ValueError("PUBLICATION_LEGEND_HIERARCHY_UNMAPPED_TECHNOLOGY")
    fig.legend(handles=[Patch(color=extra["Net imports"], label="Net imports · signed international trade")],
               loc="lower center", bbox_to_anchor=(.50, .018), fontsize=9.1, frameon=False)
    inp.check("legend_hierarchy_content_preserved", "Every original technology remains separately plotted/labelled; nuclear and hydrogen OCGT are explicit", technologies=technologies)
    return fig, data, "Installed electrical capacity (GW), annual primary generation (TWh), and separate gross storage discharge/net import supply contributions. BESS and PHS are not primary generation; imports retain their signed net convention. UC physical quantities come from saved canonical tables."


def _flexibility(cases, inp, style):
    rows = []; missing_cycles = []
    rc = reporting_config()
    for case in cases:
        balance = inp.stat(case, "annual_electrical_balance_by_zone.csv")
        totals = inp.read(Path(case["report"]) / "VIS_X1/data/uc_storage_hydro_totals.csv").set_index("carrier")
        for category, col, carrier, op in (
                ("BESS charge", "bess_charging_TWh", "BESS", "charge"),
                ("BESS discharge", "bess_discharge_TWh", "BESS", "discharge"),
                ("PHS charge", "phs_charging_TWh", "PHS", "charge"),
                ("PHS discharge", "phs_discharge_TWh", "PHS", "discharge")):
            value = float(balance[col].sum())
            np.testing.assert_allclose(value, float(totals.at[carrier, f"{op}_throughput"]) / 1e6, atol=1e-6, rtol=0)
            rows.append(_row(case, "gross_storage_activity", value, "TWh", category=category, source_authority=PHYSICAL_AUTHORITY))
        for carrier in ("BESS", "PHS"):
            value = totals.at[carrier, "equivalent_full_cycles"]
            rows.append(_row(case, "accepted_equivalent_full_cycles", value, "cycles/year", category=carrier,
                             status="UNAVAILABLE_IN_CANONICAL_SOURCE" if pd.isna(value) else "AVAILABLE"))
            if pd.isna(value):
                missing_cycles.append(f"{case['year']} {case['scenario']} {carrier}")
        rows.append(_row(case, "flexible_demand_and_curtailment", balance.p2x_electrical_consumption_TWh.sum(), "TWh", category="P2X electrical use"))
        curtail = inp.read(Path(case["report"]) / "vre_curtailment.csv")
        if not np.isfinite(curtail[["available_MWh", "dispatched_MWh", "curtailed_MWh"]].to_numpy()).all():
            raise ValueError("Finite saved VRE accounting required")
        np.testing.assert_allclose(curtail.curtailed_MWh, curtail.available_MWh-curtail.dispatched_MWh, atol=1e-2, rtol=0)
        rows.append(_row(case, "flexible_demand_and_curtailment", curtail.curtailed_MWh.sum()/1e6, "TWh", category="VRE curtailment"))
        rows.append(_row(case, "VRE_curtailment_share", 100*curtail.curtailed_MWh.sum()/curtail.available_MWh.sum(), "% of available VRE", category="VRE curtailment"))
        inp.check("storage_gross_throughput_reconciliation", "Charge and discharge each reconcile to saved storage totals", year=int(case["year"]), scenario=case["scenario"])
    data = pd.DataFrame(rows)
    years = sorted(data.year.unique())
    fig, axes = plt.subplots(len(years), 2, figsize=(14, 8.5), squeeze=False, sharey="col")
    categories = ["BESS charge", "BESS discharge", "PHS charge", "PHS discharge"]
    for axes_row, year in zip(axes, years):
        for ax, metric, labels in zip(axes_row, ["gross_storage_activity", "flexible_demand_and_curtailment"], [categories, ["P2X electrical use", "VRE curtailment"]]):
            scenarios = _scenarios(data, year)
            for i, scenario in enumerate(scenarios):
                group = data.loc[data.year.eq(year) & data.scenario.eq(scenario) & data.metric.eq(metric)].set_index("category").value.reindex(labels)
                ax.bar(np.arange(len(labels)) + (i-(len(scenarios)-1)/2)*.23, group, width=.21,
                       color=style.scenario_color(scenario), hatch=["", "//", ".."][SCENARIOS.index(scenario)])
            ax.set_xticks(range(len(labels)), labels)
            ax.set_ylabel("TWh/year")
            ax.set_title(f"{year} · " + ("Gross storage charging and discharge" if metric == "gross_storage_activity" else "P2X electricity and VRE curtailment"), fontsize=11)
            _decorate(ax)
        shares = data.loc[data.year.eq(year) & data.metric.eq("VRE_curtailment_share")]
        axes_row[1].text(.0, -.19, "Curtailed / available VRE: " + " · ".join(f"{r.scenario} {r.value:.1f}%" for r in shares.itertuples()), transform=axes_row[1].transAxes, fontsize=9)
    _header(fig, "Storage, flexible demand and curtailment", "UC gross activity · charging is withdrawal; discharge is delivered electricity", top=.86, bottom=.19)
    if missing_cycles:
        fig.text(.055, .075, "Equivalent full cycles: unavailable in saved canonical storage summaries; no inferred cycle denominator.", fontsize=9, color="#586574")
    fig.legend(handles=[Patch(color=style.scenario_color(s), hatch=["", "//", ".."][SCENARIOS.index(s)], label=s)
                        for s in _scenarios(data)], loc="lower center", bbox_to_anchor=(.5, .01), ncol=3, frameon=False)
    inp.check("canonical_utilization_missing_preserved", "Empty accepted equivalent_full_cycles values retained as unavailable", missing=missing_cycles)
    return fig, data, "Gross UC BESS/PHS charging and discharge are reported separately; P2X electrical consumption and VRE curtailment use saved physical accounting. Curtailment shares divide curtailed energy by available VRE energy. Empty canonical equivalent-full-cycle fields are preserved as unavailable. PHS discharge can also use natural inflows; gross discharge is not a round-trip-efficiency measure."


def _exchanges(cases, inp, style, cfg, geographic=False):
    from .uc2_vis_x1 import _stage_b_graph_tables
    rows = []; registries = []
    for case in cases:
        registry = inp.stat(case, "uc2_net_interface_registry.csv")
        summary = inp.stat(case, "uc2_net_interface_summary.csv")
        if len(registry) != 20 or registry.interface_id.duplicated().any() or set(summary.interface_id) != set(registry.interface_id):
            raise ValueError("Canonical unique 20-interface registry/summary binding required")
        bound = registry.set_index("interface_id")
        columns = ["from_bus", "to_bus", "classification", "positive_capacity_MW", "negative_capacity_MW"]
        pd.testing.assert_frame_equal(bound[columns].sort_index(), summary.set_index("interface_id")[columns].sort_index(), check_dtype=False)
        registries.append(registry)
        for r in summary.itertuples():
            if not 0 <= r.positive_limit_hours+r.negative_limit_hours <= 8760:
                raise ValueError("Interface saturation hours outside the full-year bound")
            rows.append(_row(case, "interface_binding_hours", r.positive_limit_hours+r.negative_limit_hours, "hours/year",
                             category=r.interface_id, positive_hours=int(r.positive_limit_hours), negative_hours=int(r.negative_limit_hours),
                             from_bus=r.from_bus, to_bus=r.to_bus, classification=r.classification))
            if r.classification == "EXTERNAL_MARKET_INTERFACE":
                if r.from_bus in ZONE_ORDER:
                    country = r.display_to; imports = -r.net_energy_MWh/1e6
                elif r.to_bus in ZONE_ORDER:
                    country = r.display_from; imports = r.net_energy_MWh/1e6
                else:
                    raise ValueError("External exchange must join an Italian zone and external market")
                rows.append(_row(case, "international_net_imports", imports, "TWh", category=country, interface_id=r.interface_id))
        inp.check("canonical_interfaces_binding", "All 20 registry endpoints/capacities/classifications match summary; positive+negative directional limit hours",
                  year=int(case["year"]), scenario=case["scenario"], interface_count=20)
    edge_columns = ["interface_id", "display_from", "display_to", "classification"]
    for registry in registries[1:]:
        pd.testing.assert_frame_equal(registries[0][edge_columns].sort_values("interface_id").reset_index(drop=True),
                                      registry[edge_columns].sort_values("interface_id").reset_index(drop=True))
    representative = next((c for c in cases if c["scenario"] == "Base"), cases[0])
    anchors = inp.read(Path(representative["report"]) / "VIS_X1/data/visualization_only_zone_anchors.csv").set_index("market")
    registry = registries[cases.index(representative)]
    corridors = registry.rename(columns={"display_from": "bus0", "display_to": "bus1"}).copy()
    annual = inp.stat(representative, "uc2_price_annual_summary.csv")
    nodes, links, _ = _stage_b_graph_tables(registry, anchors, corridors, annual, cfg)
    data = pd.DataFrame(rows)
    fig = plt.figure(figsize=(16, 12))
    grid = fig.add_gridspec(2, 2, width_ratios=[1.05, 1.5], height_ratios=[.75, 1.4])
    topology = fig.add_subplot(grid[:, 0]); exchange_ax = fig.add_subplot(grid[0, 1]); binding_ax = fig.add_subplot(grid[1, 1])
    for identifier, edge in links.iterrows():
        a, b = nodes.loc[edge.bus0], nodes.loc[edge.bus1]
        internal = edge.classification == "ITALIAN_INTERZONAL"
        arc = .15 if "CORS" in (edge.bus0, edge.bus1) else -.12 if (edge.bus0, edge.bus1) in (("NORD", "SUD"), ("CSUD", "SICI")) else 0
        topology.add_patch(FancyArrowPatch((a.x, a.y), (b.x, b.y), arrowstyle="-", connectionstyle=f"arc3,rad={arc}",
                                           color="#295f80" if internal else "#ad6036", linewidth=1.6 if internal else 1.3, zorder=1))
    for name, node in nodes.iterrows():
        color = style.carrier_style[name]["color"] if name in ZONES else "#9b73a6" if name == "CORS" else "#7b8993"
        topology.scatter([node.x], [node.y], color=color, s=70 if name in ZONES else 40, edgecolor="white", linewidth=.8, zorder=3)
        topology.annotate(name+"\n(no price)" if name == "CORS" else name, (node.x, node.y), xytext=(5, 5),
                          textcoords="offset points", fontsize=10 if name in ZONES else 9, fontweight="bold" if name in ZONES else "normal")
    topology.set_xlim(nodes.x.min()-2, nodes.x.max()+3)
    topology.set_ylim(nodes.y.min()-2, nodes.y.max()+2)
    topology.set_aspect("equal", adjustable="box")
    topology.axis("off")
    topology.set_title("MEM market topology\n7 Italian zones · 8 external markets · CORS hub", fontsize=12)
    topology.legend(handles=[Line2D([], [], color="#295f80", label="Italian interzonal interface"), Line2D([], [], color="#ad6036", label="External / CORS interface")],
                    loc="lower center", frameon=False, fontsize=9)
    if geographic:
        from .publication_geography import draw_context
        topology.clear()
        for path in draw_context(topology, nodes, links, cfg):
            if path not in inp.sources:
                inp.sources.append(path)
    column_order = [(int(c["year"]), c["scenario"]) for c in cases]
    exchange = data.loc[data.metric.eq("international_net_imports")].pivot_table(index="category", columns=["year", "scenario"], values="value", aggfunc="sum").reindex(columns=pd.MultiIndex.from_tuples(column_order))
    countries = [v for v in ("FR", "CH", "AT", "SI", "ME", "GR", "TN", "MT") if v in exchange.index]
    exchange = exchange.reindex(countries)
    limit = max(float(np.abs(exchange.to_numpy()).max()), .01)
    im = exchange_ax.imshow(exchange, cmap="PuOr", norm=TwoSlopeNorm(vmin=-limit, vcenter=0, vmax=limit), aspect="auto")
    exchange_ax.set_yticks(range(len(countries)), countries)
    exchange_ax.set_xticks(range(len(cases)), [f"{y}\n{s}" for y, s in column_order], fontsize=9)
    exchange_ax.set_title("International net imports [TWh/year] · positive into Italy", fontsize=11)
    for i in range(len(countries)):
        for j in range(len(cases)):
            value = exchange.iloc[i, j]
            exchange_ax.text(j, i, f"{value:+.1f}", ha="center", va="center", fontsize=9, color="white" if abs(value)>limit*.65 else "#263442")
    fig.colorbar(im, ax=exchange_ax, shrink=.8, pad=.025, label="TWh/year")
    binding = data.loc[data.metric.eq("interface_binding_hours")].pivot(index="category", columns=["year", "scenario"], values="value").reindex(columns=pd.MultiIndex.from_tuples(column_order))
    registry_order = registry.loc[registry.classification.eq("ITALIAN_INTERZONAL"), "interface_id"].tolist() + registry.loc[~registry.classification.eq("ITALIAN_INTERZONAL"), "interface_id"].tolist()
    binding = binding.reindex(registry_order)
    im = binding_ax.imshow(binding, cmap="YlOrRd", vmin=0, vmax=8760, aspect="auto")
    binding_ax.set_yticks(range(20), [v.replace("__", " ↔ ") for v in binding.index], fontsize=9)
    binding_ax.set_xticks(range(len(cases)), [f"{y}\n{s}" for y, s in column_order], fontsize=9)
    binding_ax.set_title("Model interface capacity saturation [unique hours/year]", fontsize=11)
    for i in range(20):
        for j in range(len(cases)):
            value = binding.iloc[i, j]
            binding_ax.text(j, i, f"{value:,.0f}", ha="center", va="center", fontsize=8.5, color="white" if value>5200 else "#263442")
    binding_ax.axhline(9.5, color="white", linewidth=2)
    fig.colorbar(im, ax=binding_ax, shrink=.75, pad=.025, label="Hours/year")
    _header(fig, "International exchange and model interface saturation", "UC signed net exchanges · saturation is a model-bound diagnostic, not measured physical transmission congestion", top=.87, bottom=.10)
    fig.text(.055, .025, "CORS is a no-price serial hub: its two 400 MW legs describe one path and must not be added as independent congestion hours.", fontsize=9, color="#586574")
    inp.check("topology_accepted_anchor_reuse", "Existing stage-B graph helper validates the 16 nodes / 20 Links; geography is display context", nodes=len(nodes), interfaces=len(links), AC_branches=0)
    return fig, data, "International net exchanges use the UC canonical signed interface orientation, converted to positive net imports into Italy. All 20 interfaces show exact directional-limit hours, not the separate >=90% utilization metric. Model-bound saturation need not create LP price separation and is not measured physical AC congestion. CORS has no price; its two serial 400 MW legs describe one path, so their saturation counts are not additive. The topology uses seven Italian zones, eight external markets and CORS, with no reference AC branches. Geographic coordinates, where shown, are market-display anchors rather than physical substations or cable routes."


def _regime(cases, inp, style):
    rc = reporting_config()
    colors = {"VRE": "#D4B23C", "Storage": rc["technology_colors"]["BESS"], "Thermal": rc["technology_colors"]["Gas - CCGT"],
              "Flexible demand": "#42B7B0", "External market": "#7b8993", "Market coupling / mixed": "#b9a8c7"}
    rows = []; versions = []; frozen = []
    for case in cases:
        label = f"{case['year']}_{case['scenario']}"
        attribution_path = Path(case.get("regime_data") or (Path(case["report"]) / f"CANONICAL/marginal_regime/data/lexicographic_regime_attribution_{label}.parquet"))
        if attribution_path.is_dir():
            attribution_path = attribution_path / f"lexicographic_regime_attribution_{label}.parquet"
        folder = attribution_path.parent
        q = inp.read(folder / f"lexicographic_regime_QA_{label}.json")
        if q.get("status") != "PASS_CANONICAL_MARGINAL_REGIME" or q.get("version") != VERSION:
            raise ValueError("Validated unchanged LEXICOGRAPHIC_REGIME_V1 data required")
        table = inp.read(attribution_path)
        share_path = folder / f"lexicographic_regime_zonal_shares_{label}.csv"
        saved = inp.read(share_path).set_index("zone").reindex(index=ZONES, columns=REGIMES)
        for path in (attribution_path, share_path):
            if _sha(path) != q["artifact_hashes"][path.name]:
                raise ValueError("Marginal-regime source hash differs from validated QA")
        method = inp.read(folder / "METHODOLOGY.json")
        if method.get("version") != VERSION:
            raise ValueError("Regime hierarchy version mismatch")
        versions.append(method)
        frozen_path = folder.parent / "validated_roots/FROZEN_METHODOLOGY.json"
        frozen.append(inp.read(frozen_path))
        native_q = inp.read(folder.parent / f"validated_roots/marginal_setter_QA_{label}.json")
        if not str(native_q.get("status", "")).startswith("PASS"):
            raise ValueError("Fixed-LP native roots lack passing source QA")
        receipt = inp.read(folder.parents[2] / "MEM_MARGINAL_REGIME_RECEIPT.json")
        if (receipt.get("status") != "PASS_CANONICAL_MARGINAL_REGIME" or
                int(receipt.get("model_year", 0)) != int(case["year"]) or
                receipt.get("scenario") != case["scenario"]):
            raise ValueError("Case-bound marginal-regime receipt required")
        for source in (frozen_path, folder.parent / f"validated_roots/marginal_setter_QA_{label}.json",
                       Path(case["report"]) / "fixed_commitment_prices_hourly.csv"):
            source = source.resolve()
            pins = {(Path(name) if Path(name).is_absolute() else ROOT / Path(name)).resolve(): digest
                    for name, digest in receipt.get("source_hashes", {}).items()}
            if pins.get(source) != _sha(source):
                raise ValueError(f"Marginal-regime receipt binding differs for {source}")
        if table.duplicated(["snapshot", "zone"]).any() or len(table) != 8760*7 or set(table.zone) != set(ZONES):
            raise ValueError("Complete, unique 61320 zone-hours required")
        if not table.groupby("zone").snapshot.nunique().eq(8760).all() or not table.represented_hours.eq(1).all():
            raise ValueError("Full one-hour represented chronology required")
        if not table.final_regime_category.isin(REGIMES).all():
            raise ValueError("Unknown regime category")
        calculated = zone_shares(table).reindex(index=ZONES, columns=REGIMES)
        np.testing.assert_allclose(saved, calculated, atol=1e-10, rtol=0)
        np.testing.assert_allclose(saved.sum(axis=1), 100., atol=1e-10, rtol=0)
        if not np.isfinite(saved.to_numpy()).all() or (saved < 0).any().any():
            raise ValueError("Finite, complete nonnegative regime shares required")
        price = _hourly_prices(case, inp)
        table_index = pd.DatetimeIndex(pd.to_datetime(table.snapshot))
        target = np.array([price.at[t, z] for t, z in zip(table_index, table.zone)], dtype=float)
        error = float(np.max(np.abs(target - table.zonal_price_EUR_MWh.to_numpy())))
        if error > 1e-7:
            raise ValueError("Regime table is not bound to selected case fixed-LP prices")
        for zone in ZONES:
            for category in REGIMES:
                rows.append(_row(case, "zonal_regime_time_share", saved.at[zone, category], "% of represented zone time", zone=zone, category=category))
        national = table.groupby("final_regime_category").represented_hours.sum().reindex(REGIMES, fill_value=0) / table.represented_hours.sum() * 100
        for category, value in national.items():
            rows.append(_row(case, "national_regime_time_share", value, "% of Italian represented zone-hours", zone="NATIONAL", category=category))
        inp.check("complete_regime_share_price_binding", "Saved zone shares equal unchanged category/time aggregation; 100% per zone; fixed-LP prices match selected report",
                  year=int(case["year"]), scenario=case["scenario"], rows=len(table), price_max_error_EUR_MWh=error,
                  hierarchy_version=VERSION, override=bool(case.get("regime_data")))
    data = pd.DataFrame(rows)
    methods_equal = all(m == versions[0] for m in versions[1:])
    frozen_equal = all(m == frozen[0] for m in frozen[1:])
    years = sorted(data.year.unique())
    fig, axes = plt.subplots(1, len(years), figsize=(16, 10), sharex=True, squeeze=False)
    for ax, year in zip(axes[0], years):
        local = data.loc[data.year.eq(year) & data.metric.eq("zonal_regime_time_share")]
        index = pd.MultiIndex.from_product([_scenarios(data, year), ZONES], names=["scenario", "zone"])
        shares = local.pivot(index=["scenario", "zone"], columns="category", values="value").reindex(index=index, columns=REGIMES)
        left = np.zeros(len(shares))
        positions = np.arange(len(shares)) + np.repeat(np.arange(len(_scenarios(data, year)))*2, len(ZONES))
        for category in REGIMES:
            values = shares[category].to_numpy()
            ax.barh(positions, values, left=left, color=colors[category], height=.79, linewidth=0)
            for i, value in enumerate(values):
                if value >= 12:
                    ax.text(left[i]+value/2, positions[i], f"{value:.0f}", va="center", ha="center", fontsize=8.5,
                            color="white" if category in ("Storage", "Thermal") else "#263442")
            left += values
        ax.set_yticks(positions, [z for s, z in shares.index], fontsize=9)
        ax.set_ylim(positions[-1]+.7, -1.7)
        for i, scenario in enumerate(_scenarios(data, year)):
            ax.text(0, positions[i*7]-.95, scenario, transform=ax.get_yaxis_transform(), ha="left", va="center",
                    fontsize=10, fontweight="bold", color=style.scenario_color(scenario))
        ax.set_xlim(0, 100)
        ax.set_xticks(range(0, 101, 20))
        ax.set_xlabel("Share of represented zone time [%]")
        ax.set_title(str(year), fontsize=13)
        ax.spines[["top", "right"]].set_visible(False)
    _header(fig, "Marginal price-setting regimes by Italian zone", "Model-implied economic-anchor categories · fixed-commitment LP evidence", top=.87, bottom=.13)
    fig.legend(handles=[Patch(color=colors[k], label=k) for k in REGIMES], ncol=3, frameon=False,
               loc="lower center", bbox_to_anchor=(.5, .015), fontsize=10)
    inp.check("regime_method_comparability", "Horizons remain separate; frozen native-root methodology and hierarchy compared",
              hierarchy_identical=methods_equal, frozen_native_methodology_identical=frozen_equal)
    caption = ("Time shares use the unchanged six-category LEXICOGRAPHIC_REGIME_V1 economic-anchor hierarchy over complete validated fixed-commitment LP KKT roots, not utilization or a merit-order ranking. "
               "VRE includes wind, solar and run-of-river hydro; Storage includes BESS, PHS and reservoir/pondage hydro; Flexible demand is P2X. Market coupling / mixed can contain local or coupled co-marginal roots and does not imply a network cause. Separate horizon panels preserve method boundaries.")
    return fig, data, caption


def render(figure_id, cases, historical=None, presentation=None):
    """Build figures 1–5, 6A, and 7–9 from caller-verified report descriptors.

    Cases: ``year, scenario, variant, report: Path`` plus optional
    ``regime_data: Path``. Historical descriptors may explicitly set
    ``report=None`` / ``available=False`` to preserve unavailable version gaps.
    No output files, receipts, datasets or accepted state are written.
    """
    figure_id = str(figure_id).upper()
    cases = _ordered(list(cases))
    inp = _Inputs()
    cfg, style, font = _style()
    with plt.rc_context({"font.family": font, "font.size": 10, "axes.labelsize": 10,
                         "axes.titlesize": 12, "figure.facecolor": "white", "axes.facecolor": "white",
                         "text.color": "#263442", "axes.labelcolor": "#263442", "svg.fonttype": "none"}):
        if figure_id == "1":
            fig, data, caption = _evolution(cases, historical, inp, style, 2040)
        elif figure_id == "2":
            fig, data, caption = _evolution(cases, historical, inp, style, 2050, three_panels=True)
        elif figure_id == "3":
            fig, data, caption = _heatmaps(cases, inp)
        elif figure_id == "4":
            fig, data, caption = _monthly(cases, inp, style, (presentation or {}).get("monthly_layout", "shared"))
        elif figure_id == "5":
            fig, data, caption = _duration(cases, inp, style)
        elif figure_id == "6A":
            fig, data, caption = _capacity_generation(cases, inp, style)
        elif figure_id == "7":
            fig, data, caption = _flexibility(cases, inp, style)
        elif figure_id == "8":
            fig, data, caption = _exchanges(cases, inp, style, cfg, (presentation or {}).get("network_layout") == "geographic")
        elif figure_id == "9":
            fig, data, caption = _regime(cases, inp, style)
        else:
            raise ValueError(f"Unsupported publication figure {figure_id!r}; use 1,2,3,4,5,6A,7,8,9")
    qa = dict(status="PASS_NUMERICAL_SOURCE_BINDING", figure_id=figure_id, plotted_data_rows=len(data),
              physical_authority=PHYSICAL_AUTHORITY, price_authority=PRICE_AUTHORITY,
              canonical_price_weight=PRICE_WEIGHT, font_resolved=font, scenario_order=list(SCENARIOS),
              display_zone_order=list(ZONES), solver_invocations=0, network_opens=0,
              historical_gaps=inp.gaps, checks=inp.checks,
              source_hashes={str(path): _sha(path) for path in inp.sources},
              rendered_visual_QA="CALLER_REQUIRED_ON_SAVED_PNG_AND_SVG")
    return dict(figure=fig, data=data, qa=qa, caption=caption, sources=inp.sources)

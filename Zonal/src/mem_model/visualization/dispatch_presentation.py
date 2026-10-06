"""Chronological presentation from accepted UC2 tables; no model or canonical writes.

Method/provenance: docs/MEM_UC2_VIS_REPORTING_R1_TRANSFER_20260930.md.
The sealed VIS-X1 renderer owns the stacks/style. Binning is a display adapter.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

POSITIVE_PRESENTATION_ORDER = (
    "Geothermal", "Bioenergy - steam thermal", "Bioenergy - other thermal",
    "Bioenergy - internal combustion", "Gas - CCGT", "Gas - internal combustion",
    "Hydro - reservoir", "Hydro - basin/pondage", "Hydro - run of river",
    "PHS", "BESS", "Net imports", "Offshore wind", "Onshore wind",
    "Solar PV - rooftop", "Solar PV - utility",
)
NEGATIVE_PRESENTATION_ORDER = ("BESS charging", "PHS charging", "P2X withdrawal", "Net exports")
PRESENTATION_LABELS = {"PHS": "PHS discharge", "BESS": "BESS discharge"}


def chronological_time_bins(values, interval_hours, weights, n_bins=1000):
    """Return means, boundaries and per-column exposure in equal elapsed-time bins.

    Input observations are constant interval-average values on [start, end).
    Split intervals at bin boundaries. Contribution = snapshot weight * overlap
    / interval duration. Neither sorting by values nor smoothing is performed.
    weights is a DataFrame: canonical generator and Link/load weights can differ.
    Gaps, duplicate timestamps, nonfinite values and missingness are rejected.
    """
    index = values.index
    if not isinstance(index, pd.DatetimeIndex) or not index.is_unique or not index.is_monotonic_increasing or values.empty:
        raise ValueError("Unique, increasing nonempty timestamp index required")
    if not values.columns.is_unique or not interval_hours.index.equals(index) or not weights.index.equals(index) or not weights.columns.equals(values.columns):
        raise ValueError("Values, durations and weights must align exactly")
    v, d, w = values.to_numpy(float), interval_hours.to_numpy(float), weights.to_numpy(float)
    if not np.isfinite(v).all() or not np.isfinite(d).all() or not np.isfinite(w).all() or (d <= 0).any() or (w <= 0).any():
        raise ValueError("Missing/nonfinite data or nonpositive duration/weight")
    if not isinstance(n_bins, int) or n_bins < 1:
        raise ValueError("Bin count must be a positive integer")
    ends = np.cumsum(d)
    starts = np.r_[0.0, ends[:-1]]
    elapsed = (index - index[0]).total_seconds().to_numpy() / 3600
    if not np.allclose(elapsed, starts, atol=1e-8, rtol=0):
        raise ValueError("Intervals must cover a continuous chronology; no gap filling")
    edges = np.linspace(0, ends[-1], n_bins + 1)
    positions = np.minimum(np.searchsorted(ends, edges, side="right"), len(d) - 1)

    def integral_at_edges(increments):
        cumulative = np.vstack([np.zeros(increments.shape[1]), np.cumsum(increments, axis=0)])
        fraction = (edges - starts[positions]) / d[positions]
        return cumulative[positions] + increments[positions] * fraction[:, None]

    exposure = np.diff(integral_at_edges(w), axis=0)
    means = np.diff(integral_at_edges(v * w), axis=0) / exposure
    bins = pd.DatetimeIndex(index[0] + pd.to_timedelta(edges[:-1], unit="h"), name="bin_start")
    boundaries = pd.DataFrame({"bin_end": index[0] + pd.to_timedelta(edges[1:], unit="h"),
                               "elapsed_start_hours": edges[:-1], "elapsed_end_hours": edges[1:],
                               "duration_hours": np.diff(edges)}, index=bins)
    return pd.DataFrame(means, index=bins, columns=values.columns), boundaries, pd.DataFrame(exposure, index=bins, columns=values.columns)


def eight_hour_time_bins(values, interval_hours):
    """Fixed 00:00/08:00/16:00 blocks; interval-overlap MWh / represented hours.

    Durations are the common physical-time measure for all plotted series.
    A long snapshot can span several display blocks. No interpolated MW values.
    """
    start = values.index[0]
    total = float(interval_hours.sum())
    if start != start.normalize() or not np.isclose(total / 8, round(total / 8), atol=1e-10, rtol=0):
        raise ValueError("Eight-hour display requires midnight start and complete blocks")
    weights = pd.DataFrame({c: interval_hours for c in values}, index=values.index)
    means, boundaries, exposure = chronological_time_bins(values, interval_hours, weights, int(round(total / 8)))
    if not boundaries.duration_hours.eq(8).all() or not set(boundaries.index.hour).issubset({0, 8, 16}):
        raise ValueError("Display boundaries must be 00:00, 08:00 and 16:00")
    return means, boundaries, exposure


def presentation_stack_order(values):
    """Explicit stable-to-variable order, never alphabetical/source order."""
    allowed = set(POSITIVE_PRESENTATION_ORDER + NEGATIVE_PRESENTATION_ORDER) | {"Rigid load", "Load shedding (nonphysical)"}
    unknown = [c for c in values if c not in allowed and values[c].abs().max() > 1e-9]
    if unknown:
        raise ValueError(f"Active technologies need explicit presentation ordering: {unknown}")
    order = [c for c in POSITIVE_PRESENTATION_ORDER if c in values]
    if "Load shedding (nonphysical)" in values and values["Load shedding (nonphysical)"].abs().max() > 1e-9:
        order.append("Load shedding (nonphysical)")
    order.extend(c for c in NEGATIVE_PRESENTATION_ORDER if c in values)
    for c in order:
        if c in NEGATIVE_PRESENTATION_ORDER and (values[c] > 1e-9).any():
            raise ValueError(f"Positive value in withdrawal series: {c}")
        if c not in NEGATIVE_PRESENTATION_ORDER and (values[c] < -1e-9).any():
            raise ValueError(f"Negative value in supply series: {c}")
    return order


def _plot_interval_presentation(display, boundaries, colors, *, year, scenario, font_family="Aptos", daily=False):
    """Use VIS-X1 continuous signed polygons; all lines use the same block grid."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import yaml
    from mem_model.visualization.vis_x1 import CONFIG, _toolkit
    _, dispatch, *_ = _toolkit(yaml.safe_load(CONFIG.read_text(encoding="utf-8")))
    from visualization_toolkit.styles import PlotStyle, style_context
    order = list(DAILY_POSITIVE_ORDER + DAILY_NEGATIVE_ORDER) if daily else presentation_stack_order(display)
    active = [c for c in order if display[c].abs().max() > 1e-9]
    style = PlotStyle(carrier_style=colors, carrier_order=order, font_family=font_family)
    with style_context(style), matplotlib.rc_context({"hatch.linewidth": .35}):
        fig, ax = plt.subplots(figsize=(16, 8))
        fig.subplots_adjust(left=.065, right=.985, top=.70, bottom=.18)
        end = boundaries.bin_end.iloc[-1]
        chart = pd.concat([display[active], pd.DataFrame([display[active].iloc[-1]], index=[end])]) / 1000
        dispatch.plot_stacked_dispatch(chart, carrier_order=active, style=style, show_demand_line=False, ax=ax)
        legend = ax.get_legend()
        handles = list(legend.legend_handles)
        labels = [text.get_text() for text in legend.get_texts()]
        drawn = [c for c in active if display[c].max() > 0] + [c for c in active if display[c].min() < 0]
        for artist, carrier in zip(ax.collections, drawn):
            artist.set_linewidth(0)
            if daily:
                artist.set_hatch(None)
                artist.set_facecolor(style.color(carrier))
            if not daily and (carrier in NEGATIVE_PRESENTATION_ORDER or carrier == "Load shedding (nonphysical)"):
                artist.set_hatch("///")
                artist.set_edgecolor(style.color(carrier))
                artist.set_facecolor(matplotlib.colors.to_rgba(style.color(carrier), .30))
            else:
                artist.set_edgecolor("none")
        for handle, label in zip(handles, labels):
            handle.set_linewidth(0)
            if not daily and (label in NEGATIVE_PRESENTATION_ORDER or label == "Load shedding (nonphysical)"):
                handle.set_hatch("///"); handle.set_edgecolor(style.color(label))
                handle.set_facecolor(matplotlib.colors.to_rgba(style.color(label), .30))
            else:
                handle.set_edgecolor("none")
        load = pd.concat([display["Rigid load"], pd.Series([display["Rigid load"].iloc[-1]], index=[end])]) / 1000
        load_line, = ax.plot(load.index, load, color="#333333" if daily else "#222222",
                            linewidth=.65 if daily else .8, drawstyle="steps-post", label="Rigid load")
        negative_order = DAILY_NEGATIVE_ORDER if daily else NEGATIVE_PRESENTATION_ORDER
        positive = display[[c for c in active if c not in negative_order]].sum(axis=1) / 1000
        negative = display[[c for c in active if c in negative_order]].sum(axis=1) / 1000
        ax.set(xlim=(display.index[0], end), ylim=(min(-1, negative.min()*1.10), max(positive.max(), load.max())*1.06),
               xlabel="Chronological saved-weather year 2019 — no price ranking", ylabel="Power [GW]")
        ticks = list(pd.date_range(display.index[0], end, freq="MS"))
        tick_labels = [t.strftime("%b") for t in ticks]
        tick_labels[0], tick_labels[-1] = ticks[0].strftime("%b\n%Y"), ticks[-1].strftime("%b\n%Y")
        ax.set_xticks(ticks); ax.set_xticklabels(tick_labels); ax.tick_params(axis="x", rotation=0)
        final_legend = ax.legend(handles + [load_line], [PRESENTATION_LABELS.get(c, c) for c in labels] + ["Rigid load"],
                  loc="lower left", bbox_to_anchor=(0, 1.025), ncol=4, frameon=False, fontsize=8.5)
        if daily:
            # Exact accepted Variant D: enlarge text without changing legend geometry settings.
            for text in final_legend.get_texts():
                text.set_fontsize(9.5)
        fig.text(.065, .97, f"MEM UC2 {year} {scenario} — Annual chronological dispatch", fontsize=18, weight="bold", va="top")
        subtitle = "Saved 2019 weather chronology | 365 daily duration-weighted means from 8,760 raw snapshots" if daily else "Saved 2019 weather chronology | 1,095 fixed 8-hour blocks | 3/day: 00:00–08:00, 08:00–16:00, 16:00–24:00"
        fig.text(.065, .918, subtitle, fontsize=10, color="#697386")
        fig.text(.065, .10, "Presentation aggregation only; annual energy, extrema and statistics use raw snapshots." if daily else "Charging, P2X withdrawal and net exports below zero; load and supply use the same eight-hour means.", fontsize=9)
        fig.text(.065, .058, "CURRENT_ACCEPTED_RESULT | Verified UC MILP | No smoothing; annual statistics and extrema remain from raw snapshots.", fontsize=9, color="#697386")
        if daily:
            ax.grid(False, axis="x")
            ax.grid(True, axis="y", color="#E6E8EB", linewidth=.6)
            ax.axhline(0, color="#333333", linewidth=.8, zorder=4)
        resolved_font = plt.rcParams["font.family"][0]
    return fig, resolved_font


def plot_eight_hour_presentation(display, boundaries, colors, **kwargs):
    """Retained diagnostic renderer; its eight-hour presentation is unchanged."""
    return _plot_interval_presentation(display, boundaries, colors, **kwargs)


def refine_existing_presentation(year=2040, scenario="Base"):
    """Display-only delta from the retained raw CSV. Never extract/open a network."""
    import matplotlib.pyplot as plt
    import matplotlib
    from mem_model.common import ROOT, sha256_file
    from mem_model.reporting.uc2_postprocess import no_solver_calls
    out = ROOT / "outputs/visualization/UC2" / str(year) / scenario / "dispatch_presentation"
    parent_path = out / "PRESENTATION_RECEIPT.json"
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    if (parent["status"], parent["result_status"], parent["physical_source"], parent["year"], parent["scenario"]) != ("PASS", "CURRENT_ACCEPTED_RESULT", "UC_MILP", year, scenario):
        raise ValueError("Accepted cached presentation required")
    names = ("annual_dispatch_raw_MW.csv", "annual_statistics_from_raw.csv", "annual_dispatch_raw_audit.png", "annual_dispatch_raw_audit.svg")
    for name in names:
        if sha256_file(out / name) != parent["artifact_hashes"][name]:
            raise ValueError(f"Cached raw/audit artifact differs from accepted receipt: {name}")
    # Hash verification only. No NetCDF/xarray/PyPSA reading or extraction.
    preserved = dict(parent["input_hashes"])
    if any(sha256_file(Path(p)) != digest for p, digest in preserved.items()):
        raise ValueError("Existing source artifact changed since prior presentation")
    report = ROOT / "results/uc2_full_year" / str(year) / scenario / "REPORTING"
    r1_path = report / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json"
    if r1_path.is_file():
        preserved.update(json.loads(r1_path.read_text(encoding="utf-8"))["protected_source_hashes"])
        if any(sha256_file(Path(p)) != digest for p, digest in preserved.items()):
            raise ValueError("Protected canonical/model source hash changed")
    if report.is_dir():
        preserved.update({str(p): sha256_file(p) for p in report.rglob("*") if p.is_file()})
    preserved.update({str(p): sha256_file(p) for p in out.iterdir() if p.is_file() and p.name not in {
        "annual_dispatch_chronological_1095.png", "annual_dispatch_chronological_1095.svg",
        "annual_dispatch_time_bins_8h_MW.csv", "presentation_8h_QA.csv", "PRESENTATION_8H_RECEIPT.json"}})
    preserved.update({str(p): sha256_file(p) for s in ("Slow", "High") if s != scenario
                      for p in (out.parents[1] / s / "dispatch_presentation").rglob("*") if p.is_file()})
    if any(sha256_file(Path(p)) != digest for p, digest in preserved.items()):
        raise ValueError("Existing source artifact changed since prior presentation")
    with no_solver_calls() as guard:
        cached = pd.read_csv(out / names[0], index_col="snapshot", parse_dates=True)
        duration = cached.pop("interval_hours")
        for column in ("generator_weight_hours", "objective_weight_hours"):
            if not np.allclose(cached.pop(column), duration, atol=1e-12, rtol=0):
                raise ValueError("Cached case snapshot weights differ from physical durations; requires separate review")
        raw = cached
        display, bounds, exposure = eight_hour_time_bins(raw, duration)
        if len(display) != 1095 or display.index[0] != pd.Timestamp("2019-01-01") or bounds.bin_end.iloc[-1] != pd.Timestamp("2020-01-01"):
            raise ValueError("Primary display requires complete non-leap 2019 chronology")
        if not display.index.equals(bounds.index) or not display.index.equals(exposure.index) or not exposure.eq(8).all().all():
            raise ValueError("Display series do not share the same eight-hour boundaries/exposure")
        stats = pd.read_csv(out / names[1], index_col="series")
        energy = (display * exposure).sum()
        errors = energy - stats.energy_MWh
        if not np.allclose(energy, stats.energy_MWh, atol=.01, rtol=1e-10):
            raise ValueError("Eight-hour display does not reconcile retained raw energy")
        order = presentation_stack_order(raw)
        positive = [c for c in order if c not in NEGATIVE_PRESENTATION_ORDER]
        negative = [c for c in NEGATIVE_PRESENTATION_ORDER if c in raw]
        sign_errors = {name: float(((display[cols]*exposure[cols]).sum() - (raw[cols].mul(duration, axis=0)).sum()).abs().max())
                       for name, cols in (("positive", positive), ("negative", negative))}
        if any(value > .01 for value in sign_errors.values()):
            raise ValueError("Positive/negative display energy mismatch")
        fig, font = plot_eight_hour_presentation(display, bounds, parent["semantic_colors"], year=year, scenario=scenario,
                                                font_family=parent["font_requested"])
        artifacts = []
        for ext in ("png", "svg"):
            path = out / f"annual_dispatch_chronological_1095.{ext}"
            with matplotlib.rc_context({"svg.hashsalt": "MEM_CHRONOLOGICAL_DISPATCH_8H", "svg.fonttype": "none", "hatch.linewidth": .35}):
                fig.savefig(path, dpi=240, bbox_inches="tight", facecolor="white", metadata={"Date": None} if ext == "svg" else {"Software": "MEM VIS-X1"})
            artifacts.append(path)
        plt.close(fig)
        csv = out / "annual_dispatch_time_bins_8h_MW.csv"
        display.join(bounds).join(exposure.add_suffix("__weight_hours")).to_csv(csv, lineterminator="\n")
        artifacts.append(csv)
    if any(sha256_file(Path(p)) != digest for p, digest in preserved.items()):
        raise ValueError("Presentation delta modified retained source/audit/canonical/model artifacts")
    checks = {"complete_2019_1095_intervals": len(display), "eight_hour_contiguous_00_08_16_grid": True,
              "all_series_and_load_share_boundaries": True, "technology_raw_energy_max_error_MWh": float(errors.abs().max()),
              "positive_energy_max_error_MWh": sign_errors["positive"], "negative_energy_max_error_MWh": sign_errors["negative"],
              "raw_audit_and_statistics_unchanged": True, "required_stack_order": order,
              "canonical_and_model_hashes_unchanged": True, "Slow_High_unchanged": True,
              "network_open_or_extraction_calls": 0, "solver_invocations": guard["solver_invocations"]}
    qa = out / "presentation_8h_QA.csv"
    pd.DataFrame([{"check": name, "status": "PASS", "detail": str(value)} for name, value in checks.items()]).to_csv(qa, index=False)
    artifacts.append(qa)
    result = {"status": "NUMERICAL_PASS_AWAITING_USER_VISUAL_REVIEW", "year": year, "scenario": scenario,
              "result_status": "CURRENT_ACCEPTED_RESULT", "physical_source": "UC_MILP", "display_intervals": 1095,
              "bin_method": "FIXED_8H_00_08_16_INTERVAL_OVERLAP_DURATION_WEIGHTED", "smoothing": "NONE_PIECEWISE_CONSTANT",
              "positive_stack_order": [c for c in order if c not in NEGATIVE_PRESENTATION_ORDER],
              "positive_stack_display_labels": [PRESENTATION_LABELS.get(c, c) for c in order if c not in NEGATIVE_PRESENTATION_ORDER],
              "negative_stack_order": list(NEGATIVE_PRESENTATION_ORDER), "font_requested": parent["font_requested"], "font_resolved": font,
              "figure_unit": "GW", "CSV_unit": "MW", "raw_extrema_statistics": "UNCHANGED_RAW_SNAPSHOT_AUTHORITY",
              "parent_receipt_sha256": sha256_file(parent_path), "preserved_source_hashes": preserved,
              "solver_invocations": guard["solver_invocations"], "network_open_or_extraction_calls": 0,
              "qa_checks": len(checks), "code_sha256": sha256_file(Path(__file__)),
              "artifact_hashes": {p.name: sha256_file(p) for p in artifacts}}
    (out / "PRESENTATION_8H_RECEIPT.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def raw_power_statistics(values, weights):
    """Energy, means, extrema and sign hours ONLY from raw MW observations."""
    if not weights.index.equals(values.index) or not weights.columns.equals(values.columns):
        raise ValueError("Raw statistics require exactly aligned weights")
    if not np.isfinite(values.to_numpy(float)).all() or not np.isfinite(weights.to_numpy(float)).all() or (weights.to_numpy(float) <= 0).any():
        raise ValueError("Raw statistics reject missing/nonfinite data or nonpositive weights")
    return pd.DataFrame({"energy_MWh": (values * weights).sum(),
                         "weighted_mean_MW": (values * weights).sum() / weights.sum(),
                         "minimum_raw_MW": values.min(), "maximum_raw_MW": values.max(),
                         "positive_weighted_hours": weights.where(values > 0, 0).sum(),
                         "negative_weighted_hours": weights.where(values < 0, 0).sum()},
                        index=values.columns).rename_axis("series")


DAILY_GROUPS = {
    "Geothermal": ("Geothermal",),
    "Bioenergy": ("Bioenergy - steam thermal", "Bioenergy - other thermal", "Bioenergy - internal combustion"),
    "Gas": ("Gas - CCGT", "Gas - internal combustion"),
    "Hydro - reservoir/pondage": ("Hydro - reservoir", "Hydro - basin/pondage"),
    "Run-of-river": ("Hydro - run of river",),
    "PHS discharge": ("PHS",), "BESS discharge": ("BESS",),
    "Imports": ("Net imports",), "Offshore wind": ("Offshore wind",),
    "Onshore wind": ("Onshore wind",), "Solar PV": ("Solar PV - rooftop", "Solar PV - utility"),
    "BESS charging": ("BESS charging",), "PHS charging": ("PHS charging",),
    "P2X withdrawal": ("P2X withdrawal",), "Exports": ("Net exports",),
    "Rigid load": ("Rigid load",),
}
DAILY_POSITIVE_ORDER = ("Geothermal", "Bioenergy", "Gas", "Hydro - reservoir/pondage", "Run-of-river",
                        "PHS discharge", "BESS discharge", "Imports", "Offshore wind", "Onshore wind", "Solar PV")
DAILY_NEGATIVE_ORDER = ("BESS charging", "PHS charging", "P2X withdrawal", "Exports")
DAILY_ZERO_ONLY_EXCLUSIONS = {
    "Gas - OCGT": "Zero-output detailed category omitted by existing annual presentation; activation requires explicit review",
    "Gas - steam thermal": "Zero-output detailed category omitted by existing annual presentation; activation requires explicit review",
    "Gas - other thermal": "Zero-output detailed category omitted by existing annual presentation; activation requires explicit review",
    "Load shedding (nonphysical)": "Not physical generation; retained in raw balance/statistics; daily stack exclusion allowed only at exactly zero",
}
DAILY_METHOD = "MEM_DAILY_R3_CALENDAR_24H_PHYSICAL_INTERVAL_OVERLAP"
# Same tolerances as the verified eight-hour adapter and original hourly check.
DISPLAY_ENERGY_ATOL_MWH = .01
DISPLAY_ENERGY_RTOL = 1e-10
BALANCE_ATOL_MW = .001
ACCEPTED_BALANCE_IDENTITY = (
    "PRIMARY_GENERATION_PLUS_BESS_DISCHARGE_PLUS_PHS_DISCHARGE_PLUS_NET_IMPORTS_PLUS_LOAD_SHEDDING"
    "_MINUS_RIGID_END_USE_DEMAND_MINUS_P2X_ELECTRICAL_CONSUMPTION_MINUS_BESS_CHARGING_MINUS_PHS_CHARGING"
)


def daily_time_bins(values, interval_hours):
    """Calendar-day specialization of the accepted interval-overlap integrator.

    Physical hours are supplied explicitly, not chosen from a PyPSA column.
    Naive saved 2019 time is required; no timezone conversion or DST repair.
    Return an allocation audit for each raw interval as well as the shared grid.
    """
    index = values.index
    if (not isinstance(index, pd.DatetimeIndex) or index.tz is not None or values.empty
            or index[0] != pd.Timestamp("2019-01-01")
            or not np.isclose(interval_hours.sum(), 8760, atol=1e-8, rtol=0)):
        raise ValueError("Daily presentation requires complete naive saved 2019 chronology")
    weights = pd.DataFrame({c: interval_hours for c in values}, index=index)
    detailed, bounds, exposure = chronological_time_bins(values, interval_hours, weights, 365)
    expected = pd.date_range("2019-01-01", "2020-01-01", freq="D", inclusive="left", name="bin_start")
    if (not detailed.index.equals(expected) or not bounds.duration_hours.eq(24).all()
            or bounds.bin_end.iloc[-1] != pd.Timestamp("2020-01-01")
            or not np.allclose(exposure, 24, atol=1e-8, rtol=0)):
        raise ValueError("Daily intervals must cover all 365 calendar days exactly")
    # Use the exact calendar interval measure after validating computed exposure.
    # Recover the integrator's energy, then divide by exactly 24 physical hours.
    detailed = detailed * exposure / 24
    exposure = pd.DataFrame(24., index=detailed.index, columns=detailed.columns)
    # Audit allocation independently: intersect every raw interval with day edges.
    edges = np.arange(366, dtype=float) * 24
    ends = interval_hours.cumsum().to_numpy()
    starts = np.r_[0., ends[:-1]]
    allocated = np.zeros(len(index))
    overlaps = np.zeros(len(index), dtype=int)
    for i, (start, end) in enumerate(zip(starts, ends)):
        first = np.searchsorted(edges, start, side="right") - 1
        last = np.searchsorted(edges, end, side="left")
        contributions = np.minimum(end, edges[first+1:last+1]) - np.maximum(start, edges[first:last])
        allocated[i] = contributions.sum()
        overlaps[i] = len(contributions)
    if not np.allclose(allocated, interval_hours, atol=1e-8, rtol=0):
        raise ValueError("Raw interval duration lost or duplicated in daily allocation")
    allocation = pd.DataFrame({"represented_hours": interval_hours, "allocated_hours": allocated,
                               "display_days_contributed": overlaps}, index=index)
    return detailed, bounds, exposure, allocation


def group_daily_presentation(detailed):
    """Exact canonical-key mapping only; no fuzzy matching or implicit Other."""
    sources = [c for columns in DAILY_GROUPS.values() for c in columns]
    if len(sources) != len(set(sources)):
        raise ValueError("Detailed series mapped more than once")
    expected = set(sources) | set(DAILY_ZERO_ONLY_EXCLUSIONS)
    if set(detailed) != expected:
        raise ValueError(f"Daily mapping mismatch: unmapped={sorted(set(detailed)-expected)}, missing={sorted(expected-set(detailed))}")
    for c in DAILY_ZERO_ONLY_EXCLUSIONS:
        if not detailed[c].eq(0).all():
            raise ValueError(f"Zero-only presentation exclusion became active: {c}")
    for family, columns in DAILY_GROUPS.items():
        if family in DAILY_NEGATIVE_ORDER:
            if (detailed[list(columns)] > 0).any().any():
                raise ValueError(f"Positive withdrawal/export sign: {family}")
        elif (detailed[list(columns)] < 0).any().any():
            raise ValueError(f"Negative supply/load sign: {family}")
    return pd.DataFrame({family: detailed[list(columns)].sum(axis=1) for family, columns in DAILY_GROUPS.items()}, index=detailed.index)


def plot_daily_presentation(display, boundaries, colors, **kwargs):
    """365 piecewise-constant daily areas and load, using the VIS-X1 renderer."""
    if not display.index.equals(boundaries.index) or len(display) != 365 or not boundaries.duration_hours.eq(24).all():
        raise ValueError("Daily plot and load require the same 365 daily interval boundaries")
    return _plot_interval_presentation(display, boundaries, colors, daily=True, **kwargs)


def refine_daily_case(year=2040, scenario="Base"):
    """Accepted Variant D from cached 2040 observations; no network extraction."""
    if year != 2040 or scenario not in ("Slow", "Base", "High"):
        raise ValueError("Daily promotion is restricted to accepted 2040 Slow/Base/High")
    import matplotlib
    import matplotlib.pyplot as plt
    import yaml
    from mem_model.common import ROOT, sha256_file
    from mem_model.reporting.uc2_postprocess import no_solver_calls
    out = ROOT / "outputs/visualization/UC2" / str(year) / scenario / "dispatch_presentation"
    parent_path = out / "PRESENTATION_RECEIPT.json"
    diagnostic_path = out / "PRESENTATION_8H_RECEIPT.json"
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    # Slow/High have accepted raw receipts but no eight-hour diagnostic. Do not
    # manufacture that extra output merely to feed the same daily integrator.
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8")) if diagnostic_path.is_file() else None
    for receipt in (parent, *([diagnostic] if diagnostic else [])):
        if (receipt["result_status"], receipt["physical_source"], receipt["year"], receipt["scenario"]) != ("CURRENT_ACCEPTED_RESULT", "UC_MILP", year, scenario):
            raise ValueError("Accepted cached UC physical authority required")
    if parent["status"] != "PASS" or (diagnostic and diagnostic["status"] != "NUMERICAL_PASS_AWAITING_USER_VISUAL_REVIEW"):
        raise ValueError("Prior raw and eight-hour QA receipts required")
    for name in ("annual_dispatch_raw_MW.csv", "annual_statistics_from_raw.csv", "annual_dispatch_raw_audit.png", "annual_dispatch_raw_audit.svg"):
        if sha256_file(out / name) != parent["artifact_hashes"][name]:
            raise ValueError(f"Accepted raw/audit artifact changed: {name}")
    for name, digest in (diagnostic["artifact_hashes"] if diagnostic else {}).items():
        if sha256_file(out / name) != digest:
            raise ValueError(f"Retained eight-hour artifact changed: {name}")
    preserved = {**parent["input_hashes"], **(diagnostic["preserved_source_hashes"] if diagnostic else {})}
    if any(sha256_file(Path(p)) != digest for p, digest in preserved.items()):
        raise ValueError("Accepted source/canonical/model hashes changed")
    new_names = {"annual_dispatch_chronological_daily.png", "annual_dispatch_chronological_daily.svg",
                 "annual_dispatch_chronological_daily_MW.csv", "annual_dispatch_daily_energy_QA.csv",
                 "annual_dispatch_daily_allocation_QA.csv", "presentation_daily_QA.csv", "PRESENTATION_DAILY_RECEIPT.json"}
    preserved.update({str(p): sha256_file(p) for p in out.iterdir() if p.is_file() and p.name not in new_names})
    preserved.update({str(p): sha256_file(p) for s in ("Slow", "Base", "High") if s != scenario
                      for p in (out.parents[1] / s / "dispatch_presentation").rglob("*") if p.is_file()})
    cfg_path = ROOT / "config/stage_b_reporting.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if cfg["electrical_balance_identity"] != ACCEPTED_BALANCE_IDENTITY:
        raise ValueError("Governing balance identity changed; presentation requires review")
    preserved[str(cfg_path)] = sha256_file(cfg_path)
    with no_solver_calls() as guard:
        raw = pd.read_csv(out / "annual_dispatch_raw_MW.csv", index_col="snapshot", parse_dates=True)
        duration = raw.pop("interval_hours")
        for column in ("generator_weight_hours", "objective_weight_hours"):
            if not np.allclose(raw.pop(column), duration, atol=1e-12, rtol=0):
                raise ValueError("Canonical component weight conflicts with physical interval_hours")
        if len(raw) != 8760:
            raise ValueError("Cached case must contain its accepted 8,760 raw snapshots")
        group_daily_presentation(raw)  # Fail closed before averaging; check raw signs/exclusions.
        detailed, bounds, exposure, allocation = daily_time_bins(raw, duration)
        display = group_daily_presentation(detailed)
        raw_energy = raw.mul(duration, axis=0).sum()
        daily_energy = (detailed * exposure).sum()
        stats = pd.read_csv(out / "annual_statistics_from_raw.csv", index_col="series")
        weights = pd.DataFrame({c: duration for c in raw}, index=raw.index)
        current_stats = raw_power_statistics(raw, weights)
        if not stats.index.equals(current_stats.index) or not stats.columns.equals(current_stats.columns):
            raise ValueError("Retained raw statistic schema changed")
        if not np.allclose(stats, current_stats, atol=DISPLAY_ENERGY_ATOL_MWH, rtol=DISPLAY_ENERGY_RTOL):
            raise ValueError("Retained raw statistics disagree with source observations")
        if not np.allclose(daily_energy, raw_energy, atol=DISPLAY_ENERGY_ATOL_MWH, rtol=DISPLAY_ENERGY_RTOL):
            raise ValueError("Daily detailed-source energy does not reconcile")
        family_raw_energy = pd.Series({f: raw_energy[list(cols)].sum() for f, cols in DAILY_GROUPS.items()})
        family_energy = display.mul(bounds.duration_hours, axis=0).sum()
        if not np.allclose(family_energy, family_raw_energy, atol=DISPLAY_ENERGY_ATOL_MWH, rtol=DISPLAY_ENERGY_RTOL):
            raise ValueError("Daily family energy does not reconcile")
        # Materialize the SAME signed identity used in the accepted initial adapter
        # and canonical_results.annual_demand_generation_balance_by_zone.
        raw_residual = raw.drop(columns="Rigid load").sum(axis=1) - raw["Rigid load"]
        daily_residual = detailed.drop(columns="Rigid load").sum(axis=1) - detailed["Rigid load"]
        balance_error = float(daily_residual.mul(bounds.duration_hours).sum() - raw_residual.mul(duration).sum())
        if raw_residual.abs().max() > BALANCE_ATOL_MW or daily_residual.abs().max() > BALANCE_ATOL_MW or abs(balance_error) > DISPLAY_ENERGY_ATOL_MWH:
            raise ValueError("Daily integration failed the accepted electrical balance")
        # Family colours select existing semantic colours, never reference-project assumptions.
        color_key = {f: cols[0] for f, cols in DAILY_GROUPS.items()}
        colors = {f: {"color": "#222222"} if f == "Rigid load" else parent["semantic_colors"][key]
                  for f, key in color_key.items()}
        colors["Bioenergy"] = {"color": cfg["technology_colors"]["Bioenergy"]}
        colors["Solar PV"] = parent["semantic_colors"]["Solar PV - utility"]
        fig, font = plot_daily_presentation(display, bounds, colors, year=year, scenario=scenario, font_family=parent["font_requested"])
        artifacts = []
        for ext in ("png", "svg"):
            path = out / f"annual_dispatch_chronological_daily.{ext}"
            with matplotlib.rc_context({"svg.hashsalt": "MEM_CHRONOLOGICAL_DISPATCH_DAILY_R3", "svg.fonttype": "none"}):
                fig.savefig(path, dpi=240, bbox_inches="tight", facecolor="white", metadata={"Date": None} if ext == "svg" else {"Software": "MEM VIS-X1"})
            artifacts.append(path)
        plt.close(fig)
        csv = out / "annual_dispatch_chronological_daily_MW.csv"
        display.join(bounds).assign(represented_hours=bounds.duration_hours, aggregation_method=DAILY_METHOD,
                                   grouping_receipt="PRESENTATION_DAILY_RECEIPT.json#presentation_groups").to_csv(csv, lineterminator="\n")
        artifacts.append(csv)
        energy_qa = pd.concat([
            pd.DataFrame({"level": "DETAILED_SOURCE", "raw_energy_MWh": raw_energy, "daily_energy_MWh": daily_energy}),
            pd.DataFrame({"level": "PRESENTATION_FAMILY", "raw_energy_MWh": family_raw_energy, "daily_energy_MWh": family_energy})])
        energy_qa["error_MWh"] = energy_qa.daily_energy_MWh - energy_qa.raw_energy_MWh
        energy_qa.index.name = "series"
        for frame, name in ((energy_qa, "annual_dispatch_daily_energy_QA.csv"), (allocation, "annual_dispatch_daily_allocation_QA.csv")):
            path = out / name; frame.to_csv(path, lineterminator="\n"); artifacts.append(path)
    if any(sha256_file(Path(p)) != digest for p, digest in preserved.items()):
        raise ValueError("Daily presentation modified an existing source/audit/diagnostic/model artifact")
    directional = {f: float(family_energy[f] - family_raw_energy[f]) for f in (
        "Imports", "Exports", "BESS charging", "BESS discharge", "PHS charging", "PHS discharge", "P2X withdrawal")}
    checks = {
        "365_unique_ordered_calendar_days_2019": len(display), "24h_contiguous_shared_interval_edges": True,
        "represented_duration_raw_and_daily_hours": [float(duration.sum()), float(bounds.duration_hours.sum())],
        "each_raw_interval_allocation_max_error_hours": float((allocation.allocated_hours-duration).abs().max()),
        "detailed_energy_max_error_MWh": float((daily_energy-raw_energy).abs().max()),
        "group_energy_max_error_MWh": float((family_energy-family_raw_energy).abs().max()),
        "independent_directional_energy_errors_MWh": directional,
        "load_energy_error_MWh": float(family_energy["Rigid load"]-family_raw_energy["Rigid load"]),
        "raw_balance_max_abs_MW": float(raw_residual.abs().max()),
        "daily_balance_max_abs_MW": float(daily_residual.abs().max()), "weighted_balance_preservation_error_MWh": balance_error,
        "explicit_mapping_and_zero_only_exclusions": True, "piecewise_constant_solid_borderless_rendering": True,
        "PNG_SVG_CSV_same_daily_dataframe": True, "raw_statistics_and_canonical_KPIs_unchanged": True,
        "raw_audit_and_8h_diagnostic_unchanged": True, "canonical_inputs_results_and_solved_networks_unchanged": True,
        "other_scenarios_unchanged": True, "solver_invocations": guard["solver_invocations"], "network_open_or_extraction_calls": 0,
    }
    qa = out / "presentation_daily_QA.csv"
    pd.DataFrame([{"check": k, "status": "PASS", "detail": str(v)} for k, v in checks.items()]).to_csv(qa, index=False)
    artifacts.append(qa)
    result = {
        "status": "PASS", "year": year, "scenario": scenario,
        "result_status": "CURRENT_ACCEPTED_RESULT", "physical_source": "UC_MILP", "raw_snapshots": 8760,
        "display_intervals": 365, "presentation_role": "PRIMARY_ANNUAL_PRESENTATION",
        "visual_variant": "D_USER_ACCEPTED_20261002",
        "visual_settings": {"rigid_load_linewidth_pt": .65, "rigid_load_color": "#333333", "legend_text_size_pt": 9.5},
        "aggregation_method": DAILY_METHOD, "smoothing": "NONE_PIECEWISE_CONSTANT_DAILY_EDGES",
        "physical_duration_source": "annual_dispatch_raw_MW.csv#interval_hours; verified prior full hourly extraction and accepted overlap adapter; component generator/objective weights must equal these durations",
        "source_timestamp_semantics": parent["source_timestamp_semantics"], "presentation_groups": DAILY_GROUPS,
        "zero_only_exclusions": DAILY_ZERO_ONLY_EXCLUSIONS, "hydro_exception": "ROR retained separately: canonical Generator/no Store versus reservoir/pondage delivered turbine Link output",
        "positive_stack_order": DAILY_POSITIVE_ORDER, "negative_stack_order": DAILY_NEGATIVE_ORDER,
        "semantic_colors": colors, "font_requested": parent["font_requested"], "font_resolved": font,
        "figure_unit": "GW", "CSV_unit": "MW", "energy_unit": "MWh", "raw_statistics_authority": "UNCHANGED_RAW_SNAPSHOTS",
        "energy_tolerance": {"atol_MWh": DISPLAY_ENERGY_ATOL_MWH, "rtol": DISPLAY_ENERGY_RTOL,
                             "source": "unchanged verified eight-hour adapter", "canonical_balance_tolerance_MWh": cfg["balance_tolerance_TWh"]*1e6},
        "balance_identity": cfg["electrical_balance_identity"], "balance_pointwise_tolerance_MW": BALANCE_ATOL_MW,
        "balance_source": "canonical_results.annual_demand_generation_balance_by_zone and retained initial presentation adapter",
        "checks": checks, "qa_checks": len(checks), "solver_invocations": guard["solver_invocations"], "network_open_or_extraction_calls": 0,
        "parent_receipt_sha256": sha256_file(parent_path), "eight_hour_receipt_sha256": sha256_file(diagnostic_path) if diagnostic else None,
        "prior_eight_hour_diagnostic_present": diagnostic is not None,
        "reference_provenance": "outputs/visualization_reference_inspection/SOURCE_REFERENCES_20261001.csv; existing R1 transfer presentation references reused",
        "preserved_source_hashes": preserved, "code_sha256": sha256_file(Path(__file__)),
        "artifact_hashes": {p.name: sha256_file(p) for p in artifacts},
    }
    (out / "PRESENTATION_DAILY_RECEIPT.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def refine_daily_base(year=2040, scenario="Base"):
    """Retained Base entrypoint; use refine_daily_case for approved promotion."""
    if (year, scenario) != (2040, "Base"):
        raise ValueError("Base entrypoint is restricted to 2040 Base")
    return refine_daily_case(year, scenario)


def render_case(year, scenario, n_bins=365):
    """Primary R3 annual display; cached eight-hour diagnostic remains available."""
    if n_bins != 365:
        raise ValueError("Primary annual presentation requires 365 calendar-day intervals")
    return refine_daily_case(year, scenario)


def _render_initial_case_legacy(year, scenario, n_bins=1000):
    """Legacy initial extraction/audit producer; cached presentation uses 8h delta."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import xarray as xr
    import yaml
    from mem_model.common import ROOT, sha256_file
    from mem_model.reporting.uc2_postprocess import no_solver_calls
    from mem_model.visualization.vis_x1 import CONFIG, _toolkit

    report = ROOT / "results/uc2_full_year" / str(year) / scenario / "REPORTING"
    receipt_path = report / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (receipt["status"], receipt["result_status"], receipt["physical_source"], receipt["year"], receipt["scenario"]) != ("PASS", "CURRENT_ACCEPTED_RESULT", "UC_MILP", year, scenario):
        raise ValueError("Accepted UC2 case/physical authority required")
    source_paths = [receipt_path, ROOT / "config/stage_b_reporting.yaml",
                    report / "VIS_X1/data/canonical_uc_dispatch_by_zone.parquet",
                    report / "CANONICAL/statistics/uc2_canonical_net_imports_hourly_MW.parquet",
                    report / "CANONICAL/statistics/annual_electricity_supply_national.csv",
                    report / "CANONICAL/statistics/annual_electrical_balance_by_zone.csv"]
    accepted = {str(report / k): v for k, v in {**receipt["artifact_hashes"], **receipt["original_artifact_hashes"]}.items()}
    for path in source_paths[2:]:
        if sha256_file(path) != accepted[str(path)]:
            raise ValueError(f"Canonical source hash changed: {path}")
    protected = receipt["protected_source_hashes"]
    if any(sha256_file(Path(p)) != digest for p, digest in protected.items()):
        raise ValueError("Accepted model/result lineage hashes changed")
    network_path = next(Path(p) for p in protected if p.endswith("UC_MILP_SOLVED.nc"))
    source_paths.append(network_path)
    before = {str(p): sha256_file(p) for p in source_paths}
    # Preserve the complete existing reporting package, including the map delta.
    preserved = {str(p): sha256_file(p) for p in report.rglob("*") if p.is_file()}
    cfg = yaml.safe_load((ROOT / "config/stage_b_reporting.yaml").read_text(encoding="utf-8"))
    vis_cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    _, dispatch, *_ = _toolkit(vis_cfg)
    from visualization_toolkit.styles import PlotStyle, style_context

    with no_solver_calls() as guard:
        operation = pd.read_parquet(source_paths[2])
        national = operation.loc[operation.zone.eq("NATIONAL")].set_index("snapshot").drop(columns="zone")
        national.index = pd.DatetimeIndex(national.index, name="snapshot")
        net = pd.read_parquet(source_paths[3])
        if not net.index.equals(national.index):
            raise ValueError("UC canonical exchange/dispatch timestamp mismatch")
        if len(national) != cfg["require_full_chronology_hours"] or not np.all(np.diff(national.index.asi8) == 3600 * 10**9):
            raise ValueError("Case adapter requires the accepted full hourly chronology")
        with xr.open_dataset(network_path) as ds:
            snapshots = pd.DatetimeIndex(ds.snapshots_snapshot.values)
            if not snapshots.equals(national.index):
                raise ValueError("UC snapshot weights and canonical dispatch misaligned")
            objective = pd.Series(ds.snapshots_objective.values, index=national.index)
            generators = pd.Series(ds.snapshots_generators.values, index=national.index)
        durations = pd.Series(1.0, index=national.index)
        meta_columns = [c for c in national if c.endswith("_MW")]
        technology_columns = [c for c in national if c not in meta_columns]
        if set(technology_columns) - set(cfg["technology_colors"]):
            raise ValueError("Technology missing from accepted MEM colour dictionary")
        raw = national[technology_columns].copy()
        signed_net = net.sum(axis=1)
        if not np.allclose(signed_net, national.net_imports_MW, atol=1e-5, rtol=0):
            raise ValueError("Canonical net-interface accounting differs from UC national exchange")
        # Split signs BEFORE binning, preventing import/export cancellation.
        raw["Net imports"] = signed_net.clip(lower=0)
        raw["Net exports"] = signed_net.clip(upper=0)
        raw["BESS charging"] = -national.bess_charging_MW
        raw["PHS charging"] = -national.phs_charging_MW
        raw["P2X withdrawal"] = -national.p2x_electrical_consumption_MW
        raw["Load shedding (nonphysical)"] = national.load_shedding_MW
        raw["Rigid load"] = national.rigid_end_use_demand_MW
        if not np.isfinite(raw.to_numpy()).all():
            raise ValueError("Raw missingness/nonfinite values; no zero filling allowed")
        if (raw[technology_columns + ["Rigid load", "Net imports", "Load shedding (nonphysical)"]] < 0).any().any():
            raise ValueError("Physical supply/load must follow accepted positive sign convention")
        link_technologies = {"BESS", "PHS", cfg["hydro_class_to_display_technology"]["BASIN_PONDAGE"],
                             cfg["hydro_class_to_display_technology"]["RESERVOIR"]}
        weights = pd.DataFrame({c: generators if c in technology_columns and c not in link_technologies else objective for c in raw}, index=raw.index)
        stats = raw_power_statistics(raw, weights)
        display, boundaries, exposure = chronological_time_bins(raw, durations, weights, n_bins)
        recovered = (display * exposure).sum()
        if not np.allclose(recovered, stats.energy_MWh, atol=0.01, rtol=1e-10):
            raise ValueError("Display binning does not conserve raw weighted energy")
        supply = pd.read_csv(source_paths[4]).set_index("display_technology").annual_electricity_supply_TWh
        if not np.allclose(stats.loc[supply.index, "energy_MWh"] / 1e6, supply, atol=cfg["balance_tolerance_TWh"], rtol=0):
            raise ValueError("Raw technology energy differs from accepted canonical supply")
        balance = pd.read_csv(source_paths[5]).set_index("zone")
        if set(balance.index) != set(cfg["zone_order"]):
            raise ValueError("Annual balance must contain seven Italian zones once")
        comparisons = {"Rigid load": "rigid_end_use_demand_TWh", "P2X withdrawal": "p2x_electrical_consumption_TWh",
                       "BESS charging": "bess_charging_TWh", "PHS charging": "phs_charging_TWh",
                       "Load shedding (nonphysical)": "load_shedding_TWh"}
        for series, column in comparisons.items():
            energy = stats.loc[series, "energy_MWh"] / 1e6
            if series.endswith(("charging", "withdrawal")):
                energy = -energy
            if abs(energy - balance[column].sum()) > cfg["balance_tolerance_TWh"]:
                raise ValueError(f"Canonical annual energy mismatch: {series}")
        net_energy = stats.loc[["Net imports", "Net exports"], "energy_MWh"].sum() / 1e6
        if abs(net_energy - balance.net_imports_TWh.sum()) > cfg["balance_tolerance_TWh"]:
            raise ValueError("Canonical signed net import energy mismatch")
        residual = raw.drop(columns="Rigid load").sum(axis=1) - raw["Rigid load"]
        if residual.abs().max() > .001:
            raise ValueError("Raw hourly electrical balance does not reconcile")

        out = ROOT / "outputs/visualization/UC2" / str(year) / scenario / "dispatch_presentation"
        out.mkdir(parents=True, exist_ok=True)
        raw_export = raw.assign(interval_hours=durations, generator_weight_hours=generators, objective_weight_hours=objective)
        raw_export.to_csv(out / "annual_dispatch_raw_MW.csv", lineterminator="\n")
        display.join(boundaries).join(exposure.add_suffix("__weight_hours")).to_csv(out / "annual_dispatch_time_bins_MW.csv", lineterminator="\n")
        stats.to_csv(out / "annual_statistics_from_raw.csv", lineterminator="\n")
        colors = {k: {"color": v} for k, v in cfg["technology_colors"].items()}
        colors.update({"Net imports": {"color": "#5A7D9A"}, "Net exports": {"color": "#5A7D9A"},
                       "BESS charging": {"color": cfg["technology_colors"]["BESS"]},
                       "PHS charging": {"color": cfg["technology_colors"]["PHS"]},
                       "P2X withdrawal": {"color": "#42B7B0"}, "Load shedding (nonphysical)": {"color": "#9AA1A9"}})
        order = [c for c in cfg["technology_order"] if c in raw] + ["Net imports", "Load shedding (nonphysical)", "BESS charging", "PHS charging", "P2X withdrawal", "Net exports"]
        style = PlotStyle(carrier_style=colors, carrier_order=order, font_family=cfg["font_preference"][0])
        active = [c for c in order if raw[c].abs().max() > 1e-9]
        ylim = (raw[[c for c in active if raw[c].min() < 0]].sum(axis=1).min() / 1000 * 1.12,
                raw[[c for c in active if raw[c].max() > 0]].sum(axis=1).max() / 1000 * 1.08)
        artifacts = []
        with style_context(style):
            resolved_font = plt.rcParams["font.family"][0]
            for frame, name in [(raw, "annual_dispatch_raw_audit"), (display, f"annual_dispatch_chronological_{n_bins}")]:
                fig, ax = plt.subplots(figsize=(16, 8))
                fig.subplots_adjust(left=.065, right=.985, top=.69, bottom=.18)
                end = boundaries.bin_end.iloc[-1]
                chart = pd.concat([frame[active], pd.DataFrame([frame[active].iloc[-1]], index=[end])]) / 1000
                dispatch.plot_stacked_dispatch(chart, carrier_order=active, style=style, show_demand_line=False, ax=ax)
                legend = ax.get_legend()
                handles = list(legend.legend_handles)
                labels = [text.get_text() for text in legend.get_texts()]
                for handle, label in zip(handles, labels):
                    if label in {"BESS charging", "PHS charging", "P2X withdrawal", "Net exports", "Load shedding (nonphysical)"}:
                        handle.set_hatch("///"); handle.set_edgecolor(style.color(label)); handle.set_facecolor(matplotlib.colors.to_rgba(style.color(label), .3))
                for artist, c in zip(ax.collections, [c for c in active if raw[c].max() > 0] + [c for c in active if raw[c].min() < 0]):
                    if c in {"BESS charging", "PHS charging", "P2X withdrawal", "Net exports", "Load shedding (nonphysical)"}:
                        artist.set_hatch("///"); artist.set_edgecolor(style.color(c)); artist.set_facecolor(matplotlib.colors.to_rgba(style.color(c), .3))
                load = pd.concat([frame["Rigid load"], pd.Series([frame["Rigid load"].iloc[-1]], index=[end])]) / 1000
                load_line, = ax.plot(load.index, load, color="#222222", linewidth=.8, drawstyle="steps-post", label="Rigid load")
                ax.set(xlim=(raw.index[0], end), ylim=ylim, xlabel="Chronological UC snapshots — saved weather year; no price ranking", ylabel="Power [GW]")
                ticks = list(pd.date_range(raw.index[0], end, freq="MS"))
                ax.set_xticks(ticks)
                tick_labels = [t.strftime("%b") for t in ticks]
                tick_labels[0] = ticks[0].strftime("%b\n%Y")
                tick_labels[-1] = ticks[-1].strftime("%b\n%Y")
                ax.set_xticklabels(tick_labels)
                ax.tick_params(axis="x", rotation=0)
                ax.legend(handles + [load_line], labels + ["Rigid load"], loc="lower left", bbox_to_anchor=(0, 1.025), ncol=4, frameon=False, fontsize=8.5)
                note = f"{len(raw):,} raw snapshots; no temporal averaging" if frame is raw else f"{n_bins:,} equal elapsed-time bins; {durations.sum()/n_bins:g} hours/bin; snapshot-weighted means"
                fig.text(.065, .97, f"MEM UC2 {year} {scenario} — Annual chronological dispatch", fontsize=18, weight="bold", va="top")
                fig.text(.065, .918, f"Saved {raw.index[0].year} weather chronology | {note}", fontsize=10, color="#697386")
                fig.text(.065, .10, "Charging, P2X withdrawal and net exports below zero; load shedding is nonphysical and separately labelled.", fontsize=9)
                fig.text(.065, .058, "CURRENT_ACCEPTED_RESULT | Physical source: verified UC MILP | Statistics and extrema use raw data, not display bins.", fontsize=9, color="#697386")
                for ext in ("png", "svg"):
                    path = out / f"{name}.{ext}"
                    with matplotlib.rc_context({"svg.hashsalt": "MEM_CHRONOLOGICAL_DISPATCH", "svg.fonttype": "none"}):
                        fig.savefig(path, dpi=240, bbox_inches="tight", metadata={"Date": None} if ext == "svg" else {"Software": "MEM VIS-X1"})
                    artifacts.append(path)
                plt.close(fig)
        if any(sha256_file(Path(p)) != digest for p, digest in {**preserved, **protected, **before}.items()):
            raise ValueError("Presentation modified an existing report/model/source artifact")
        qa = pd.DataFrame([{"check": k, "status": "PASS", "detail": str(v)} for k, v in {
            "accepted_UC_physical_authority": receipt["physical_source"], "timestamp_alignment_and_no_missingness": len(raw),
            "equal_time_bins_preserve_chronology": n_bins, "raw_weighted_energy_conserved": float((recovered-stats.energy_MWh).abs().max()),
            "canonical_technology_energy_reconciled": len(supply), "canonical_load_storage_P2X_exchange_reconciled": True,
            "raw_hourly_electrical_balance_MW": residual.abs().max(), "existing_reports_and_models_unchanged": len(preserved),
            "solver_invocations": guard["solver_invocations"]}.items()])
        qa.to_csv(out / "presentation_QA.csv", index=False)
        result = {"status": "PASS", "year": year, "scenario": scenario, "result_status": "CURRENT_ACCEPTED_RESULT",
                  "physical_source": "UC_MILP", "price_source": "NOT_USED_NO_PRICE_OVERLAY", "raw_snapshots": len(raw),
                  "display_bins": n_bins, "bin_method": "EQUAL_ELAPSED_TIME_SNAPSHOT_WEIGHTED_OVERLAP",
                  "source_timestamp_semantics": "Unchanged naive UC snapshot chronology; weather year is not model year; no timezone conversion",
                  "stack_order": order, "semantic_colors": colors, "font_requested": style.font_family,
                  "font_resolved": resolved_font, "figure_power_unit": "GW", "CSV_power_unit": "MW",
                  "raw_energy_unit": "MWh", "underlying_data": [str(out / name) for name in (
                      "annual_dispatch_raw_MW.csv", "annual_dispatch_time_bins_MW.csv", "annual_statistics_from_raw.csv")],
                  "solver_invocations": guard["solver_invocations"], "input_hashes": before,
                  "code_sha256": sha256_file(Path(__file__)), "outputs": [str(p) for p in artifacts]}
        result["artifact_hashes"] = {p.name: sha256_file(p) for p in out.iterdir() if p.is_file() and p.name != "PRESENTATION_RECEIPT.json"}
        (out / "PRESENTATION_RECEIPT.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--scenario", required=True)
    args = parser.parse_args()
    print(json.dumps(render_case(args.year, args.scenario), indent=2))

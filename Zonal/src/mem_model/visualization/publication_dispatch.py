"""Publication composition of the accepted dispatch producer, without source writes.

The daily means, signs, grouping and colours remain producer owned. The only
new analytical transformation is the explicitly defined peak residual week.
The caller owns saving the returned Figure and long data/QA/caption artifacts.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from ..common import sha256_file
from ..reporting import dispatch_figures as producer
from . import dispatch_presentation as presentation

SCENARIOS = ("Slow", "Base", "High")
YEARS = (2040, 2050)
RESIDUAL_SOURCE = "VIS_X1/data/residual_load_including_P2X_MW.csv"
SUPPORT = ("PHS discharge", "BESS discharge", "Imports", "Load shedding (nonphysical)")
VRE = ("Offshore wind", "Onshore wind", "Solar PV")
BOUNDARIES = ("bin_end", "elapsed_start_hours", "elapsed_end_hours", "duration_hours")


def _ordered_cases(cases):
    lookup = {}
    for value in cases:
        case = dict(value)
        case["year"] = int(case["year"])
        case["report"] = Path(case["report"]).resolve()
        if not isinstance(case.get("variant"), str) or not case["variant"].strip():
            raise ValueError("PUBLICATION_DISPATCH_VARIANT_REQUIRED")
        key = (case["year"], case["scenario"])
        if key in lookup:
            raise ValueError("PUBLICATION_DISPATCH_DUPLICATE_CASE")
        lookup[key] = case
    expected = {(year, scenario) for year in YEARS for scenario in SCENARIOS}
    if not lookup or not set(lookup).issubset(expected):
        raise ValueError("PUBLICATION_DISPATCH_REQUIRES_SUPPORTED_NONEMPTY_CASE_SELECTION")
    return [lookup[year, scenario] for scenario in SCENARIOS for year in YEARS
            if (year, scenario) in lookup]


def _load_case(case):
    report = case["report"]
    receipt, sources, uc_sha = producer._sources(report, case["year"], case["scenario"])
    cfg = producer.reporting_config()
    if cfg["electrical_balance_identity"] != presentation.ACCEPTED_BALANCE_IDENTITY:
        raise ValueError("PUBLICATION_DISPATCH_BALANCE_METHOD_CONFLICT")
    raw, grouped = producer._raw_dispatch(report, cfg)
    duration, provenance, pins = producer._duration(report, receipt, raw.index, None)
    sources.update(pins)
    return dict(case=case, receipt=receipt, sources=sources, raw=raw, grouped=grouped,
                duration=duration, duration_provenance=provenance, uc_sha=uc_sha)


def _producer_daily(loaded):
    """Run the actual producer in its validated cache or a disposable data stage."""
    case = loaded["case"]
    report = case["report"]
    artifact_names = (producer.RECEIPT, producer.METADATA, producer.RAW_FILE,
                      producer.FIGURES[0][4])
    if case.get("public_saved_tables_only"):
        daily_path = report / producer.FOLDER / producer.FIGURES[0][4]
        if daily_path.is_file():
            receipt_path = report / producer.FOLDER / producer.RECEIPT
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if (receipt.get("status") != "PASS_DISPATCH_PRESENTATION"
                    or receipt.get("year") != case["year"] or receipt.get("scenario") != case["scenario"]):
                raise ValueError("PUBLIC_RELEASE_DAILY_CASE_AUTHORITY")
            pins = receipt.get("artifact_hashes", {})
            expected = pins.get(producer.FOLDER + "/" + daily_path.name, pins.get(daily_path.name))
            if receipt.get("source_UC_solved_sha256") != loaded["uc_sha"]:
                raise ValueError("PUBLIC_RELEASE_DAILY_UC_SOURCE")
            if expected is None or sha256_file(daily_path) != expected:
                raise ValueError("PUBLIC_RELEASE_DAILY_TABLE_HASH")
            table = pd.read_csv(daily_path, index_col="bin_start", parse_dates=["bin_start", "bin_end"])
            for path in (daily_path, receipt_path):
                loaded["sources"][str(path)] = sha256_file(path)
            kind = "ACCEPTED_PUBLIC_NUMERIC_DAILY_TABLE_WITH_RECONCILIATION"
        else:
            detailed, bounds, _, allocation = presentation.daily_time_bins(loaded["raw"], loaded["duration"])
            table = pd.concat([producer._daily_groups(detailed), bounds], axis=1)
            receipt = None
            kind = "ACCEPTED_DISPLAY_GROUPING_FROM_PUBLIC_SAVED_TABLES"
    elif (report / producer.FOLDER / producer.RECEIPT).is_file():
        receipt = producer.produce_dispatch_figures(report, case["year"], case["scenario"])
        table = pd.read_csv(report / producer.FOLDER / producer.FIGURES[0][4],
                            index_col="bin_start", parse_dates=["bin_start", "bin_end"])
        for name in artifact_names:
            path = report / producer.FOLDER / name
            loaded["sources"][str(path)] = sha256_file(path)
        kind = "VALIDATED_EXISTING_ANNUAL_DISPATCH_CHRONOLOGICAL_DAILY"
    else:
        # Copy only source tables and their immutable receipt. Authority-register
        # paths still reference original saved receipts, never a network open.
        # The managed Windows sandbox can expose an unwritable system TMP.
        # Project scratch is disposable workspace state, outside source reports.
        temp_base = (producer.ROOT / "outputs/figure_cache").resolve()
        temp_base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="mem_publication_dispatch_", dir=temp_base) as folder:
            stage = Path(folder).resolve()
            if stage == temp_base or not stage.is_relative_to(temp_base):
                raise ValueError("PUBLICATION_DISPATCH_TEMPORARY_STAGE_OUTSIDE_TEMP_BASE")
            for name in (producer.SOURCE_RECEIPT, producer.AUTHORITY_SOURCE,
                         producer.DISPATCH_SOURCE, producer.EXCHANGE_SOURCE,
                         producer.DURATION_SOURCE):
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(report / name, target)
            receipt = producer.produce_dispatch_figures(stage, case["year"], case["scenario"])
            table = pd.read_csv(stage / producer.FOLDER / producer.FIGURES[0][4],
                                index_col="bin_start", parse_dates=["bin_start", "bin_end"])
        kind = "ACTUAL_PRODUCER_DISPOSABLE_DATA_ONLY_STAGE"
    if receipt is not None and receipt.get("status") != "PASS_DISPATCH_PRESENTATION":
        raise ValueError("PUBLICATION_DISPATCH_DAILY_PRODUCER_NOT_VALIDATED")
    daily = table.drop(columns=list(BOUNDARIES))
    bounds = table[list(BOUNDARIES)]
    detailed, expected_bounds, _, allocation = presentation.daily_time_bins(
        loaded["raw"], loaded["duration"])
    expected = producer._daily_groups(detailed)
    if (not daily.index.equals(expected.index) or not daily.columns.equals(expected.columns)
            or not np.allclose(daily, expected, atol=1e-8, rtol=1e-12)
            or not bounds.index.equals(expected_bounds.index)
            or not bounds.bin_end.equals(expected_bounds.bin_end)
            or not np.allclose(bounds[list(BOUNDARIES[1:])],
                               expected_bounds[list(BOUNDARIES[1:])], atol=1e-8, rtol=0)):
        raise ValueError("PUBLICATION_DISPATCH_DAILY_PRODUCER_RECONCILIATION_FAIL")
    energy_error = (daily.mul(bounds.duration_hours, axis=0).sum()
                    - loaded["grouped"].mul(loaded["duration"], axis=0).sum()).abs()
    if not np.allclose(energy_error, 0, atol=presentation.DISPLAY_ENERGY_ATOL_MWH,
                       rtol=0):
        raise ValueError("PUBLICATION_DISPATCH_DAILY_ENERGY_FAIL")
    loaded.update(display=daily, bounds=bounds)
    return dict(daily_source=kind, daily_intervals=len(daily),
                producer="mem_model.reporting.dispatch_figures.produce_dispatch_figures",
                producer_figure_id="annual_dispatch_chronological_daily",
                aggregation_method=presentation.DAILY_METHOD,
                family_energy_max_error_MWh=float(energy_error.max()),
                allocated_hours=float(allocation.allocated_hours.sum()),
                diagnostic_graphics_retained=False)


def _rigid_residual(loaded):
    """Reconcile gross canonical residual to rigid demand minus dispatched VRE."""
    path = loaded["case"]["report"] / RESIDUAL_SOURCE
    digest = producer._source_digest(loaded["case"]["report"], loaded["receipt"], path)
    residual = pd.read_csv(path, index_col=0, parse_dates=True)
    required = ("demand_mw", "vre_mw", "residual_load_mw")
    if (not residual.index.equals(loaded["raw"].index)
            or not set(required).issubset(residual)
            or not np.isfinite(residual[list(required)].to_numpy(float)).all()):
        raise ValueError("PUBLICATION_DISPATCH_CANONICAL_RESIDUAL_CHRONOLOGY_OR_COLUMNS")
    raw, grouped = loaded["raw"], loaded["grouped"]
    rigid, p2x = raw["Rigid load"], -raw["P2X withdrawal"]
    vre = grouped[list(VRE)].sum(axis=1)
    checks = {
        "gross_demand_MW": residual.demand_mw - (rigid + p2x),
        "dispatched_VRE_MW": residual.vre_mw - vre,
        "canonical_identity_MW": residual.residual_load_mw - (residual.demand_mw - residual.vre_mw),
        "rigid_residual_MW": residual.residual_load_mw - p2x - (rigid - vre),
    }
    errors = {key: float(value.abs().max()) for key, value in checks.items()}
    if max(errors.values()) > presentation.BALANCE_ATOL_MW:
        raise ValueError("PUBLICATION_DISPATCH_CANONICAL_RESIDUAL_SEMANTIC_CONFLICT")
    loaded["sources"][str(path)] = digest
    return rigid - vre, dict(source=RESIDUAL_SOURCE,
        source_definition="Rigid demand plus UC P2X withdrawal minus dispatched VRE",
        selection_definition="Rigid demand minus dispatched wind and solar; P2X excluded",
        reconciliation_max_abs_errors=errors)


def _select_peak_week(residual):
    """Highest cumulative positive residual over 168 consecutive saved hours.

    The earliest start resolves an exact tie. No wrapping, calendar alignment,
    gap filling or smoothing is used.
    """
    expected = pd.date_range("2019-01-01", "2020-01-01", freq="h", inclusive="left")
    if not residual.index.equals(expected) or not np.isfinite(residual.to_numpy(float)).all():
        raise ValueError("PUBLICATION_DISPATCH_PEAK_REQUIRES_8760_HOURLY_CHRONOLOGY")
    scores = residual.clip(lower=0).rolling(168, min_periods=168).sum().iloc[167:]
    end_position = int(np.argmax(scores.to_numpy())) + 167
    start_position = end_position - 167
    timestamps = residual.index[start_position:end_position + 1]
    return timestamps, dict(start=timestamps[0].isoformat(),
        end_exclusive=(timestamps[-1] + pd.Timedelta(hours=1)).isoformat(),
        hours=168, positive_residual_MWh=float(scores.iloc[end_position - 167]),
        candidate_windows=len(scores), tie_break="EARLIEST_START_EXACT_TIE",
        exact_tied_maximum_windows=int(scores.eq(scores.max()).sum()))


def _category(series):
    if series == "Rigid load":
        return "rigid_demand"
    if series == "Rigid residual demand":
        return "rigid_residual_demand"
    if series == "Load shedding (nonphysical)":
        return "nonphysical_shedding"
    if series in ("PHS discharge", "BESS discharge"):
        return "storage_discharge"
    if series == "Imports":
        return "external_imports"
    if series in producer.DAILY_NEGATIVE:
        return "signed_withdrawal_or_export"
    return "primary_generation"


def _long(loaded, *, residual=None):
    display = loaded["display"].copy()
    if residual is not None:
        display["Rigid residual demand"] = residual.loc[display.index]
    table = display.rename_axis("snapshot").reset_index().melt(
        id_vars="snapshot", var_name="series", value_name="value_MW")
    bounds = loaded["bounds"]
    table["interval_end"] = table.snapshot.map(bounds.bin_end)
    table["interval_hours"] = table.snapshot.map(bounds.duration_hours)
    case = loaded["case"]
    for key in ("year", "scenario", "variant"):
        table[key] = case[key]
    table["category"] = table.series.map(_category)
    return table[["year", "scenario", "variant", "snapshot", "interval_end",
                  "interval_hours", "series", "category", "value_MW"]]


def _compose(loaded_cases, *, daily):
    """Compose selected axes with the sealed signed-stack renderer."""
    import matplotlib
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import yaml
    from .vis_x1 import CONFIG, _toolkit
    _, dispatch, *_ = _toolkit(yaml.safe_load(CONFIG.read_text(encoding="utf-8")))
    from visualization_toolkit.styles import PlotStyle, style_context
    _, colors = producer._colors(producer.reporting_config())
    order = list(producer.DAILY_POSITIVE)
    if not daily:
        order = [name for name in order if name not in SUPPORT] + list(SUPPORT)
    order += list(producer.DAILY_NEGATIVE)
    years = [year for year in YEARS if any(item["case"]["year"] == year for item in loaded_cases)]
    scenarios = [scenario for scenario in SCENARIOS
                 if any(item["case"]["scenario"] == scenario for item in loaded_cases)]
    active = [name for name in order if any(
        item["display"][name].abs().max() > 1e-9 for item in loaded_cases)]
    style = PlotStyle(carrier_style=colors, carrier_order=order, font_family="Aptos")
    positive = max(float(item["display"][list(producer.DAILY_POSITIVE)].sum(axis=1).max())
                   for item in loaded_cases) / 1000
    negative = min(float(item["display"][list(producer.DAILY_NEGATIVE)].sum(axis=1).min())
                   for item in loaded_cases) / 1000
    with style_context(style), matplotlib.rc_context({"hatch.linewidth": .35}):
        rows, columns = len(scenarios), len(years)
        height = 6.2 if rows == 1 else 3.1 * rows + 2.0
        width = 11.2 if columns == 1 else 14.4
        figure, axes = plt.subplots(rows, columns, figsize=(width, height),
                                   sharey=True, squeeze=False)
        for ax in axes.flat:
            ax.set_visible(False)
        legend_items = {}
        for item in loaded_cases:
            display, bounds, case = item["display"], item["bounds"], item["case"]
            row, column = scenarios.index(case["scenario"]), years.index(case["year"])
            ax = axes[row, column]
            ax.set_visible(True)
            end = bounds.bin_end.iloc[-1]
            chart = pd.concat([display[active], pd.DataFrame(
                [display[active].iloc[-1]], index=[end])]) / 1000
            dispatch.plot_stacked_dispatch(chart, carrier_order=active, style=style,
                                          show_demand_line=False, ax=ax)
            legend = ax.get_legend()
            labels = [text.get_text() for text in legend.get_texts()]
            for carrier, artist in zip(labels, ax.collections):
                artist.set_linewidth(0)
                artist.set_edgecolor("none")
                if not daily and carrier in (*SUPPORT, *producer.DAILY_NEGATIVE):
                    artist.set_hatch("///")
                    artist.set_edgecolor(style.color(carrier))
                    artist.set_facecolor(matplotlib.colors.to_rgba(style.color(carrier), .45))
                legend_items.setdefault(carrier, artist)
            legend.remove()
            load = pd.concat([display["Rigid load"], pd.Series(
                [display["Rigid load"].iloc[-1]], index=[end])]) / 1000
            line, = ax.plot(load.index, load, color="#333333", linewidth=.65,
                            drawstyle="steps-post", label="Rigid demand")
            legend_items["Rigid demand"] = line
            ax.set(xlim=(display.index[0], end), ylim=(min(-1, negative * 1.1), positive * 1.06),
                   title=f"{case['year']} · {case['scenario']}",
                   xlabel="", ylabel="Power [GW]" if column == 0 else "")
            ax.grid(False, axis="x")
            ax.grid(True, axis="y", color="#E6E8EB", linewidth=.6)
            ax.axhline(0, color="#333333", linewidth=.8, zorder=4)
            if daily:
                # Display Jan--Dec once; the closing Jan 2020 edge is not a
                # thirteenth month label in the saved 2019 chronology.
                ax.set_xticks(pd.date_range(display.index[0], periods=12, freq="MS"))
            else:
                ax.xaxis.set_major_locator(mdates.DayLocator())
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b") if daily else mdates.DateFormatter("%d %b"))
            ax.tick_params(axis="x", labelsize=9, labelbottom=True, rotation=0)
            ax.tick_params(axis="y", labelsize=9)
            for text in ax.get_xticklabels():
                text.set_visible(True)
                text.set_horizontalalignment("center")
            if row == rows - 1:
                ax.set_xlabel("Saved 2019 weather chronology", fontsize=9)
        # plot_stacked_dispatch calls Figure.autofmt_xdate(), which rewrites
        # subplot margins. Restore final geometry after every shared helper.
        figure.subplots_adjust(left=.065, right=.985, top=1 - 1.25 / height,
                               bottom=2.0 / height, hspace=.42, wspace=.09)
        for ax in axes.flat:
            if ax.get_visible():
                ax.tick_params(axis="x", labelbottom=True, rotation=0)
                for text in ax.get_xticklabels():
                    text.set_visible(True)
                    text.set_horizontalalignment("center")
        labels = [name for name in active if name in legend_items] + ["Rigid demand"]
        figure.legend([legend_items[name] for name in labels], labels,
                      loc="lower center", bbox_to_anchor=(.52, .48 / height),
                      ncol=5, frameon=False, fontsize=9.5)
        title = "Annual chronological dispatch — daily means" if daily else "Peak residual-demand week"
        subtitle = ("365 daily means per case · 8,760 hourly observations retain statistical authority"
                    if daily else "168 consecutive hours · High selects the shared window per horizon")
        figure.suptitle(title, y=1 - .20 / height, fontsize=16, weight="bold", va="top")
        figure.text(.5, 1 - .70 / height, subtitle, ha="center", va="top",
                    fontsize=9, color="#697386")
        figure.text(.5, .17 / height,
                    "Annual energy, extrema and statistics retain all 8,760 raw hourly observations.",
                    ha="center", va="center", fontsize=9, color="#697386")
        font = plt.rcParams["font.family"][0]
    return figure, font


def _case_qa(loaded):
    case = loaded["case"]
    raw = loaded["raw"]
    return dict(year=case["year"], scenario=case["scenario"], variant=case["variant"],
        result_status=loaded["receipt"]["result_status"], source_UC_solved_sha256=loaded["uc_sha"],
        raw_snapshots=len(raw), raw_represented_hours=float(loaded["duration"].sum()),
        hourly_balance_max_abs_MW=float((raw.drop(columns="Rigid load").sum(axis=1)
                                        - raw["Rigid load"]).abs().max()),
        raw_annual_energy_MWh_by_series={name: float(value) for name, value in
            raw.mul(loaded["duration"], axis=0).sum().items()},
        raw_minimum_MW_by_series={name: float(value) for name, value in raw.min().items()},
        raw_maximum_MW_by_series={name: float(value) for name, value in raw.max().items()},
        annual_statistics_authority="RAW_8760_OBSERVATIONS",
        duration_provenance=loaded["duration_provenance"])


def _result(loaded, figure, data, qa, caption, font):
    sources = {path: digest for item in loaded for path, digest in item["sources"].items()}
    for path in (Path(__file__), Path(producer.__file__), Path(presentation.__file__)):
        sources[str(path)] = sha256_file(path)
    sources.update(producer._code_hashes())
    for path, digest in sources.items():
        if sha256_file(Path(path)) != digest:
            raise ValueError("PUBLICATION_DISPATCH_SOURCE_CHANGED_DURING_RENDER: " + path)
    qa.update(status="PASS_PUBLICATION_DISPATCH", solver_invocations=0, network_opens=0,
              optimization_model_constructed=False, source_hashes=sources,
              font_requested="Aptos", font_resolved=font, figure_power_unit="GW",
              data_power_unit="MW", source_timestamp_semantics="NAIVE_SAVED_2019_WEATHER_HOURS",
              presentation_groups=producer.DAILY_GROUPS,
              annual_statistics_authority="RAW_8760_OBSERVATIONS")
    json.dumps(qa)  # The public contract requires a JSON-serializable receipt.
    return dict(figure=figure, data=data, qa=qa, caption=caption,
                sources=[Path(name) for name in sorted(sources)])


def render_daily(cases):
    """Return coordinated selected-case daily Figure, long MW data and source QA."""
    loaded = [_load_case(case) for case in _ordered_cases(cases)]
    qa_cases = [{**_case_qa(item), **_producer_daily(item)} for item in loaded]
    figure, font = _compose(loaded, daily=True)
    scope = ", ".join(f"{item['case']['year']} {item['case']['scenario']}" for item in loaded)
    caption = ("Figure 6B. Annual chronological national dispatch for " + scope + ". "
        "The accepted annual_dispatch_chronological_daily producer supplies 365 calendar-day interval means "
        "using physical interval overlap, its technology groups, semantic colours and Variant D signed areas. "
        "Imports and exports are split before averaging; charging, P2X and exports appear below zero. "
        "Storage discharge and nonphysical shedding remain distinct from primary generation. "
        "The black line is rigid demand. Means are presentation only; annual energy, extrema and statistics "
        "retain all 8,760 saved 2019 weather hours. Final publication baselines: 2040 final_v3 and 2050 final_v6.")
    return _result(loaded, figure, pd.concat([_long(item) for item in loaded], ignore_index=True),
                   dict(figure_id="Figure_6B", cases=qa_cases, smoothing="NONE", style="VARIANT_D"),
                   caption, font)


def render_peak_week(cases, reference_cases=None):
    """Return 168 hourly intervals per case, sharing High's window within a year.

    A selected Slow/Base case may receive its High selector through separate
    reference_cases. Reference source pins are returned even when not plotted.
    """
    loaded = [_load_case(case) for case in _ordered_cases(cases)]
    residuals, residual_qa, windows = {}, {}, {}
    for item in loaded:
        key = (item["case"]["year"], item["case"]["scenario"])
        residuals[key], residual_qa[key] = _rigid_residual(item)
    years = sorted({item["case"]["year"] for item in loaded})
    references = []
    available = {(item["case"]["year"], item["case"]["scenario"]): item for item in loaded}
    reference_lookup = {(case["year"], case["scenario"]): case
                        for case in _ordered_cases(reference_cases)} if reference_cases else {}
    for year in years:
        if (year, "High") not in available:
            case = reference_lookup.get((year, "High"))
            if case is None:
                raise ValueError(f"WAITING_FOR_HIGH_PEAK_RESIDUAL_REFERENCE_CASE_{year}")
            reference = _load_case(case)
            references.append(reference)
            residuals[year, "High"], residual_qa[year, "High"] = _rigid_residual(reference)
        timestamps, receipt = _select_peak_week(residuals[year, "High"])
        windows[year] = (timestamps, dict(**receipt, selector_scenario="High"))
    tables, qa_cases = [], []
    for item in loaded:
        case = item["case"]
        key = case["year"], case["scenario"]
        timestamps, window_qa = windows[case["year"]]
        item["display"] = item["grouped"].loc[timestamps]
        item["bounds"] = pd.DataFrame({"bin_end": timestamps + pd.Timedelta(hours=1),
                                      "duration_hours": 1.}, index=timestamps)
        tables.append(_long(item, residual=residuals[key]))
        qa_cases.append({**_case_qa(item), "window": window_qa,
                         "residual_reconciliation": residual_qa[key],
                         "selected_interval_energy_MWh_by_series": {
                             name: float(value) for name, value in item["display"].sum().items()},
                         "selected_hourly_balance_max_abs_MW": float((
                             item["display"].drop(columns="Rigid load").sum(axis=1)
                             - item["display"]["Rigid load"]).abs().max()),
                         "selected_case_positive_residual_MWh": float(
                             residuals[key].loc[timestamps].clip(lower=0).sum())})
    figure, font = _compose(loaded, daily=False)
    dates = "; ".join(f"{year}: {windows[year][1]['start']} to {windows[year][1]['end_exclusive']} (end exclusive)"
                      for year in years)
    caption = ("Figure 6C. Peak residual-demand week, showing 168 consecutive saved hourly intervals. "
        "For each horizon, High selects the window with the highest cumulative positive rigid residual demand "
        "[max(rigid demand − dispatched wind and solar, 0) × 1 hour]; Slow and Base use identical timestamps. "
        "Exact ties select the earliest start. The canonical residual including P2X is reconciled and its P2X "
        "withdrawal removed for selection. This is a residual-demand diagnostic, not an adequacy-risk ranking. "
        "Solid areas are primary generation; hatched support areas separately show PHS/BESS discharge, imports "
        "and nonphysical shedding. Signed charging, P2X withdrawal and exports remain below zero. "
        "The black line is rigid demand. Windows: " + dates + ". Final publication baselines: 2040 final_v3 and 2050 final_v6.")
    return _result(loaded + references, figure, pd.concat(tables, ignore_index=True),
                   dict(figure_id="Figure_6C", cases=qa_cases,
                        reference_cases=[_case_qa(item) for item in references],
                        selector="MAX_168H_CUMULATIVE_POSITIVE_RIGID_RESIDUAL_HIGH_PER_HORIZON",
                        shared_timestamps_per_horizon=True, smoothing="NONE"), caption, font)

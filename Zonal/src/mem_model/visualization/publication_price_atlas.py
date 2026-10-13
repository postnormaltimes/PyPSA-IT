"""Unsaved zonal price atlas views from caller-verified publication reports.

The publication controller owns baseline selection, source pin verification and
exports. This adapter reads saved tables only. Duration and day/hour rendering
remain owned by the sealed VIS-X1 toolkit; their figures are styled here for
scenario comparison. No accepted reports or old VIS-X1 figures are written.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
import numpy as np
import pandas as pd

from . import publication_figures as publication
from .vis_x1 import _sha, _toolkit

SCENARIOS = publication.SCENARIOS
ZONES = publication.ZONES
PRICE_WEIGHT = publication.PRICE_WEIGHT
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _zone(zone):
    value = publication._alias(str(zone))
    if value not in ZONES:
        raise ValueError(f"Unknown Italian atlas zone: {zone}")
    return value


def _matrix(frame, column, index):
    values = frame.pivot(index="snapshot", columns="zone", values=column).reindex(columns=ZONES)
    if not values.index.equals(index) or values.isna().any().any():
        raise ValueError("PRICE_ATLAS_UC_PRICE_CHRONOLOGY_CONFLICT")
    values = values.apply(pd.to_numeric, errors="raise")
    if not np.isfinite(values.to_numpy()).all():
        raise ValueError("PRICE_ATLAS_NONFINITE_UC_WEIGHT")
    return values


def _near(observed, expected, label, *, atol=1e-8):
    a, b = np.asarray(observed, dtype=float), np.asarray(expected, dtype=float)
    if a.shape != b.shape or not np.isfinite(b).all() or not np.allclose(a, b, atol=atol, rtol=1e-10):
        raise ValueError(f"PRICE_ATLAS_CANONICAL_RECONCILIATION_FAILED: {label}")
    return float(np.max(np.abs(a - b))) if a.size else 0.0


def _qa(inp, **values):
    return dict(status="PASS_NUMERICAL_SOURCE_BINDING", checks=inp.checks,
                physical_authority=publication.PHYSICAL_AUTHORITY,
                price_authority=publication.PRICE_AUTHORITY,
                canonical_price_weight=PRICE_WEIGHT,
                percentile_definition="Unweighted hourly price observations; pandas linear quantiles",
                hourly_national_definition="Sum(zone LP price * contemporaneous UC gross end-use MW) / sum(UC gross end-use MW)",
                volume_mean_definition="Sum(hourly price * UC rigid-plus-P2X MW * represented duration h) / sum(UC rigid-plus-P2X MW * represented duration h)",
                time_mean_definition="Sum(hourly price * represented duration h) / sum(represented duration h); equals arithmetic hourly mean for these one-hour snapshots",
                band_definition="P25–P75 spread of hourly observations; not a confidence interval",
                solver_invocations=0, network_opens=0,
                source_hashes={str(path): _sha(path) for path in inp.sources}, **values)


def hourly_price_data(case, inputs=None):
    """Return aligned saved LP prices and UC price weights, with reconciliation.

    Keys are ``prices`` (seven display zones), ``rigid_load_mw``, ``p2x_mw``,
    ``gross_load_mw``, ``duration_h`` (Series), ``national_price`` (Series),
    ``sources`` and ``qa``. The caller may cache this value under
    ``(int(case['year']), case['scenario'])`` for all atlas views.
    """
    inp = inputs if inputs is not None else publication._Inputs()
    prices = publication._hourly_prices(case, inp)
    index = prices.index
    stored = inp.read(Path(case["report"]) / "VIS_X1/data/fixed_commitment_lp_price_duration.parquet")
    stored = stored.copy()
    stored["market"] = stored.market.map(publication._alias)
    stored["snapshot"] = pd.to_datetime(stored.snapshot, errors="raise")
    if (set(stored.market) != set(ZONES) or len(stored) != len(index) * 7 or
            stored.duplicated(["snapshot", "market"]).any()):
        raise ValueError("PRICE_ATLAS_SEVEN_DURATION_SERIES_REQUIRED")
    duration = None
    for zone, group in stored.groupby("market", sort=False):
        group = group.sort_values("snapshot")
        if not pd.DatetimeIndex(group.snapshot).equals(index):
            raise ValueError("PRICE_ATLAS_DURATION_CHRONOLOGY_CONFLICT")
        weights = pd.to_numeric(group.weight, errors="raise").to_numpy()
        if not np.isfinite(weights).all() or not np.equal(weights, 1.0).all():
            raise ValueError("PRICE_ATLAS_ONE_HOUR_UC_DURATION_REQUIRED")
        duration = pd.Series(weights, index=index, name="duration_h")
    inp.check("atlas_UC_represented_duration", "Seven saved UC generator weight series match all 8760 one-hour prices",
              year=int(case["year"]), scenario=case["scenario"], represented_hours=float(duration.sum()))

    dispatch = inp.stat(case, "dispatch_8760_by_zone.parquet").copy()
    dispatch["snapshot"] = pd.to_datetime(dispatch.snapshot, errors="raise")
    dispatch["zone"] = dispatch.zone.map(publication._alias)
    zonal = dispatch.loc[dispatch.zone.isin(ZONES)]
    if len(zonal) != 7 * len(index) or zonal.duplicated(["snapshot", "zone"]).any():
        raise ValueError("PRICE_ATLAS_COMPLETE_UC_ZONAL_LOAD_REQUIRED")
    rigid = _matrix(zonal, "rigid_end_use_demand_MW", index)
    p2x = _matrix(zonal, "p2x_electrical_consumption_MW", index)
    gross = _matrix(zonal, "gross_end_use_demand_MW", index)
    if (rigid.to_numpy() < -1e-7).any() or (p2x.to_numpy() < -1e-7).any() or not gross.gt(0).all().all():
        raise ValueError("PRICE_ATLAS_NONPOSITIVE_UC_GROSS_LOAD")
    residual = _near(gross, rigid + p2x, "UC gross load equals rigid plus P2X", atol=1e-7)
    national_rows = dispatch.loc[dispatch.zone.eq("NATIONAL")].set_index("snapshot").sort_index()
    if len(national_rows) != len(index) or not national_rows.index.equals(index):
        raise ValueError("PRICE_ATLAS_COMPLETE_UC_NATIONAL_LOAD_REQUIRED")
    for field, matrix in (("rigid_end_use_demand_MW", rigid), ("p2x_electrical_consumption_MW", p2x),
                          ("gross_end_use_demand_MW", gross)):
        _near(matrix.sum(axis=1), national_rows[field], "UC national " + field, atol=1e-7)
    national = (prices * gross).sum(axis=1) / gross.sum(axis=1)
    national.name = "load_weighted_national_EUR_per_MWh"
    saved = inp.stat(case, "uc2_price_hourly_all_italy_spread.parquet")
    if "snapshot" in saved:
        saved = saved.set_index("snapshot")
    saved.index = pd.DatetimeIndex(pd.to_datetime(saved.index, errors="raise"))
    if not saved.index.equals(index):
        raise ValueError("PRICE_ATLAS_SAVED_NATIONAL_PRICE_CHRONOLOGY_CONFLICT")
    error = _near(national, saved.load_weighted_national_EUR_per_MWh, "hourly national price")
    inp.check("atlas_hourly_price_weight_reconciliation", "Saved UC rigid and P2X volumes reproduce all canonical hourly national prices",
              year=int(case["year"]), scenario=case["scenario"], UC_gross_load_residual_MW=residual,
              maximum_national_price_residual_EUR_per_MWh=error)
    result = dict(prices=prices, rigid_load_mw=rigid, p2x_mw=p2x, gross_load_mw=gross,
                  duration_h=duration, national_price=national, sources=list(inp.sources))
    result["case_identity"] = dict(year=int(case["year"]), scenario=case["scenario"],
                                   variant=case.get("variant", ""), report=str(Path(case["report"]).resolve()))
    result["qa"] = _qa(inp, year=int(case["year"]), scenario=case["scenario"], observations=8760)
    return result


def _hourly(case, inp, hourly_by_case):
    key = (int(case["year"]), case["scenario"])
    if hourly_by_case is None or key not in hourly_by_case:
        return hourly_price_data(case, inputs=inp)
    value = hourly_by_case[key]
    expected = dict(year=int(case["year"]), scenario=case["scenario"], variant=case.get("variant", ""),
                    report=str(Path(case["report"]).resolve()))
    if value.get("case_identity") != expected:
        raise ValueError("PRICE_ATLAS_CACHED_CASE_IDENTITY_CONFLICT")
    for path in value["sources"]:
        if Path(path) not in inp.sources:
            inp.sources.append(Path(path))
    inp.checks.extend(value["qa"]["checks"])
    return value


def monthly_price_statistics(cases, zone=None, *, hourly_by_case=None):
    """Return weighted monthly means and hourly observation percentiles.

    ``zone=None`` uses hourly national prices; otherwise use an Italian zone.
    The line statistic is volume weighted. P5/P25/P50/P75/P95 and the time mean
    are calculated over the individual hourly observations in each month.
    Canonical monthly and annual weighted means are checked, never replaced.
    """
    cases = publication._ordered(list(cases))
    zone = None if zone is None else _zone(zone)
    inp, rows, annual = publication._Inputs(), [], []
    for case in cases:
        values = _hourly(case, inp, hourly_by_case)
        hours = values["duration_h"]
        gross = values["gross_load_mw"]
        prices = values["national_price"] if zone is None else values["prices"][zone]
        load = gross.sum(axis=1) if zone is None else gross[zone]
        weight = load * hours
        if zone is None:
            canonical = inp.stat(case, "uc2_price_monthly_national_load_weighted.csv")
        else:
            canonical = inp.stat(case, "uc2_price_monthly_zonal_means.csv")
            canonical = canonical.loc[canonical.market.map(publication._alias).eq(zone)]
        if len(canonical) != 12 or set(canonical.month) != set(range(1, 13)) or canonical.month.duplicated().any():
            raise ValueError("PRICE_ATLAS_TWELVE_CANONICAL_MONTHS_REQUIRED")
        canonical = canonical.set_index("month").sort_index()
        calculated, denominators = [], []
        for month in range(1, 13):
            p, w, h = prices.loc[prices.index.month == month], weight.loc[weight.index.month == month], hours.loc[hours.index.month == month]
            mean = float((p * w).sum() / w.sum())
            quantiles = p.quantile([.05, .25, .5, .75, .95], interpolation="linear")
            row = dict(year=int(case["year"]), scenario=case["scenario"], variant=case.get("variant", ""),
                       zone=zone or "NATIONAL", month=month, weighted_mean_EUR_per_MWh=mean,
                       arithmetic_mean_EUR_per_MWh=float(p.mean()), time_mean_EUR_per_MWh=float((p * h).sum() / h.sum()),
                       weight_MWh=float(w.sum()), price_load_numerator_EUR=float((p * w).sum()),
                       observations=len(p), represented_hours=float(h.sum()), negative_hours=int(p.lt(0).sum()),
                       minimum_EUR_per_MWh=float(p.min()), maximum_EUR_per_MWh=float(p.max()),
                       source_authority=publication.PRICE_AUTHORITY, weight_authority=publication.PHYSICAL_AUTHORITY,
                       unit="EUR/MWh")
            row.update({f"p{int(q * 100)}_EUR_per_MWh": float(value) for q, value in quantiles.items()})
            rows.append(row)
            calculated.append(mean)
            denominators.append(float(w.sum()))
        monthly_error = _near(calculated, canonical.load_weighted_mean_EUR_per_MWh, "monthly weighted price")
        if zone is None:
            _near(denominators, canonical.rigid_load_plus_P2X_MWh, "monthly UC MWh denominator", atol=1e-5)
            expected_annual = float(np.average(canonical.load_weighted_mean_EUR_per_MWh, weights=canonical.rigid_load_plus_P2X_MWh))
        else:
            _near([row["arithmetic_mean_EUR_per_MWh"] for row in rows[-12:]], canonical.mean_EUR_per_MWh, "monthly arithmetic zonal price")
            saved_annual = inp.stat(case, "uc2_price_annual_summary.csv")
            saved_annual = saved_annual.loc[saved_annual.market.map(publication._alias).eq(zone)]
            if len(saved_annual) != 1:
                raise ValueError("PRICE_ATLAS_ONE_CANONICAL_ANNUAL_ZONE_REQUIRED")
            expected_annual = float(saved_annual.load_weighted_mean_EUR_per_MWh.iloc[0])
            _near(float(prices.mean()), float(saved_annual.arithmetic_mean_EUR_per_MWh.iloc[0]), "annual arithmetic zonal price")
        volume_mean = float((prices * weight).sum() / weight.sum())
        annual_error = _near(volume_mean, expected_annual, "annual weighted price")
        time_mean = float((prices * hours).sum() / hours.sum())
        _near(time_mean, float(prices.mean()), "one-hour time versus arithmetic annual mean")
        summary = dict(year=int(case["year"]), scenario=case["scenario"], zone=zone or "NATIONAL",
                       annual_volume_weighted_mean_EUR_per_MWh=volume_mean,
                       canonical_annual_volume_weighted_mean_EUR_per_MWh=expected_annual,
                       annual_time_mean_EUR_per_MWh=time_mean, annual_arithmetic_mean_EUR_per_MWh=float(prices.mean()),
                       volume_minus_time_mean_EUR_per_MWh=volume_mean - time_mean,
                       annual_UC_weight_MWh=float(weight.sum()), monthly_maximum_price_residual_EUR_per_MWh=monthly_error,
                       annual_weighted_price_residual_EUR_per_MWh=annual_error)
        annual.append(summary)
        inp.check("atlas_monthly_and_annual_weighted_price_reconciliation", "Hourly price and UC volumes reproduce saved monthly and annual weighted means", **summary)
    data = pd.DataFrame(rows)
    return dict(data=data, sources=inp.sources, qa=_qa(inp, zone=zone or "NATIONAL", plotted_data_rows=len(data), annual_means=annual))


def _style_duration(ax, style):
    for line in ax.lines:
        scenario = line.get_label()
        if scenario in SCENARIOS:
            line.set(color=style.scenario_color(scenario), linestyle=publication.LINESTYLES[scenario], linewidth=1.8)
    if ax.get_legend() is not None:
        ax.get_legend().remove()
    publication._decorate(ax)
    ax.grid(False, axis="x")


def _duration(cases, zone, inp, style, toolkit, hourly_by_case):
    rows, extrema = [], []
    years = sorted({int(case["year"]) for case in cases})
    fig, axes = plt.subplots(1, len(years), figsize=(9 * len(years), 6.2), squeeze=False, sharey=True)
    all_prices = []
    for ax, year in zip(axes[0], years):
        selected = [case for case in cases if int(case["year"]) == year]
        hourly = [_hourly(case, inp, hourly_by_case) for case in selected]
        index = hourly[0]["prices"].index
        if any(not value["prices"].index.equals(index) for value in hourly):
            raise ValueError("PRICE_ATLAS_SCENARIO_CHRONOLOGY_CONFLICT")
        series = pd.DataFrame({case["scenario"]: value["prices"][zone] for case, value in zip(selected, hourly)})
        duration = hourly[0]["duration_h"]
        toolkit.plot_price_duration(series, weights=duration, weight_unit="Represented hours · descending prices", ax=ax)
        table = toolkit.price_duration_table(series, weights=duration).rename(columns={"market": "scenario", "price": "value"})
        table["rank_hour"] = table.groupby("scenario", sort=False).cumcount() + 1
        table["year"], table["zone"], table["unit"] = year, zone, "EUR/MWh"
        variants = {case["scenario"]: case.get("variant", "") for case in selected}
        table["variant"] = table.scenario.map(variants)
        table["source_authority"] = publication.PRICE_AUTHORITY
        rows.append(table)
        _style_duration(ax, style)
        rectangle = [.53, .66, .43, .24]
        inset = ax.inset_axes(rectangle)
        tail = series.where(series.rank(method="first", ascending=False).le(200))
        toolkit.plot_price_duration(tail, weights=duration, weight_unit="Represented hours", ax=inset)
        _style_duration(inset, style)
        inset.set(title="Highest 200 hours · linear EUR/MWh", xlabel="", ylabel="", xlim=(0, 200))
        inset.set_title("Highest 200 hours · linear EUR/MWh", fontsize=8)
        inset.tick_params(labelsize=8)
        ax.set(title=str(year), xlim=(0, 8760), ylabel="EUR/MWh")
        ax.set_yscale("linear")
        ax.set_xticks([0, 2000, 4000, 6000, 8760])
        ax.axhline(0, color="#697386", linewidth=.6)
        for scenario in series:
            price = series[scenario]
            ranked = table.loc[table.scenario.eq(scenario), "value"]
            if len(ranked) != 8760 or not ranked.is_monotonic_decreasing:
                raise ValueError("PRICE_ATLAS_COMPLETE_MONOTONE_DURATION_REQUIRED")
            _near([ranked.iloc[-1], ranked.iloc[0]], [price.min(), price.max()], "duration extrema")
            extrema.append(dict(year=year, scenario=scenario, minimum=float(price.min()), maximum=float(price.max()), negative_hours=int(price.lt(0).sum())))
        all_prices.append(series.to_numpy().ravel())
    full = np.concatenate(all_prices)
    low, high = float(full.min()), float(full.max())
    lower, upper = min(0., low*1.15), max(175., high*1.20)
    for ax, year in zip(axes[0], years):
        ax.set_ylim(lower, upper)
        data = next(table for table in rows if table.year.eq(year).all())
        covered = data.loc[data.x_end.between(rectangle[0]*8760, (rectangle[0]+rectangle[2])*8760)]
        inset_bottom = lower + rectangle[1]*(upper-lower)
        if covered.value.max() >= inset_bottom:
            raise ValueError("PRICE_ATLAS_DURATION_INSET_REQUIRES_UNUSED_SPACE")
        inp.check("atlas_duration_inset_clearance", "Linear duration inset lies above every main-curve observation under its footprint",
            year=int(year), curve_max_EUR_per_MWh=float(covered.value.max()), inset_bottom_EUR_per_MWh=inset_bottom)
    publication._header(fig, f"{zone} electricity price duration", "All hourly fixed-commitment LP prices · complete positive and negative extrema", bottom=.16)
    publication._legend(fig, style, scenarios=[s for s in SCENARIOS if any(case["scenario"] == s for case in cases)])
    inp.check("atlas_complete_price_duration", "Sealed toolkit draws all 8760 observations per case; full extrema retained", extrema=extrema,
              renderer="visualization_toolkit.prices.plot_price_duration", clipped_observations=0)
    caption = "Zonal fixed-commitment LP price duration with saved UC represented-hour weights. Each curve retains all 8,760 hourly observations and extrema. Main axes and highest-200-hour insets use linear EUR/MWh scales; inset clearance is verified against the main curves."
    return fig, pd.concat(rows, ignore_index=True), caption


def _monthly(cases, zone, inp, style, hourly_by_case):
    stats = monthly_price_statistics(cases, zone=zone, hourly_by_case=hourly_by_case)
    inp.sources.extend(path for path in stats["sources"] if path not in inp.sources)
    inp.checks.extend(stats["qa"]["checks"])
    data = stats["data"]
    years = sorted(data.year.unique())
    scenarios = [scenario for scenario in SCENARIOS if scenario in set(data.scenario)]
    fig, axes = plt.subplots(len(years), len(scenarios), figsize=(5*len(scenarios), 4.1*len(years)+1), squeeze=False, sharey=True)
    for i, year in enumerate(years):
        for j, scenario in enumerate(scenarios):
            ax = axes[i, j]
            group = data.loc[data.year.eq(year) & data.scenario.eq(scenario)].sort_values("month")
            if group.empty:
                continue
            color = style.scenario_color(scenario)
            ax.fill_between(group.month, group.p25_EUR_per_MWh, group.p75_EUR_per_MWh, color=color, alpha=.14, linewidth=0)
            ax.plot(group.month, group.weighted_mean_EUR_per_MWh, color=color, linestyle=publication.LINESTYLES[scenario], marker=publication.MARKERS[scenario], linewidth=1.9, markersize=4)
            ax.set(title=f"{year} · {scenario}", xlabel="Month", ylabel="EUR/MWh")
            ax.set_xticks(range(1, 13), MONTHS)
            publication._decorate(ax)
    publication._header(fig, f"{zone} monthly electricity prices", "Line: UC rigid + P2X volume-weighted mean · band: hourly observation P25–P75", bottom=.15)
    fig.text(.055, .035, "Same vertical scale across scenarios · hourly price percentiles describe observation spread, not uncertainty", fontsize=9, color="#586574")
    inp.check("atlas_monthly_percentile_semantics", "P25–P75 band uses hourly zonal observations; P5/P50/P95 retained in plotted-data export", percentile_observations="hourly zonal LP prices", mean_weight="UC rigid plus P2X MWh")
    caption = "Lines show each month's UC rigid-load plus P2X volume-weighted zonal LP price. Shaded bands show P25–P75 of the month's individual hourly zonal prices; they describe observation spread. P5, P50 and P95, monthly denominators, extrema and observation counts are retained in the data."
    return fig, data, caption


def _heatmap(cases, zone, inp, style, toolkit, hourly_by_case):
    years = {int(case["year"]) for case in cases}
    if len(years) != 1 or {case["scenario"] for case in cases} != set(SCENARIOS):
        raise ValueError("PRICE_ATLAS_HEATMAP_REQUIRES_ONE_HORIZON_AND_ALL_THREE_SCENARIOS")
    year = next(iter(years))
    values = [(case, _hourly(case, inp, hourly_by_case)) for case in cases]
    low = min(float(hourly["prices"][zone].min()) for _, hourly in values)
    high = max(float(hourly["prices"][zone].max()) for _, hourly in values)
    padding = max(abs(low) * .01, 1.0) if low == high else 0.0
    norm = Normalize(vmin=low - padding, vmax=high + padding, clip=False)
    fig, axes = plt.subplots(3, 1, figsize=(14, 9.4), squeeze=False)
    rows, extrema = [], []
    for ax, (case, hourly) in zip(axes[:, 0], values):
        prices = hourly["prices"][[zone]]
        grid = toolkit.price_day_hour_table(prices, zone)
        if grid.shape != (24, 365) or grid.isna().any().any():
            raise ValueError("PRICE_ATLAS_COMPLETE_DAY_HOUR_GRID_REQUIRED")
        toolkit.plot_price_heatmap(prices, zone, ax=ax)
        artist = ax.images[-1]
        artist.set_norm(norm)
        if artist.colorbar is not None:
            artist.colorbar.update_normal(artist)
            artist.colorbar.set_label("EUR/MWh · shared full range")
        ax.set_title("")
        ax.set_title(case["scenario"], loc="left", color=style.scenario_color(case["scenario"]))
        extrema.append(dict(scenario=case["scenario"], minimum=float(prices[zone].min()), maximum=float(prices[zone].max()), negative_hours=int(prices[zone].lt(0).sum())))
        frame = pd.DataFrame(dict(snapshot=prices.index, value=prices[zone].to_numpy()))
        frame["date"], frame["hour_of_day"] = prices.index.normalize(), prices.index.hour
        frame["year"], frame["scenario"], frame["variant"], frame["zone"] = year, case["scenario"], case.get("variant", ""), zone
        frame["unit"], frame["source_authority"] = "EUR/MWh", publication.PRICE_AUTHORITY
        rows.append(frame)
        _near(np.sort(np.asarray(artist.get_array()).ravel()), np.sort(prices[zone].to_numpy()), "heatmap retains hourly prices")
    publication._header(fig, f"{year} {zone} hourly electricity prices", "One colour scale across Slow / Base / High · full zonal scenario extrema", top=.88, bottom=.08)
    inp.check("atlas_heatmap_common_full_extrema", "Existing toolkit heatmaps share the complete zone/horizon extrema; no clipping or negative-price removal",
              minimum_EUR_per_MWh=low, maximum_EUR_per_MWh=high, color_domain=[norm.vmin, norm.vmax],
              scenario_extrema=extrema, renderer="visualization_toolkit.prices.plot_price_heatmap", clipped_observations=0)
    caption = "The existing VIS-X1 day/hour renderer is reused for all three scenarios. This comparison alternative shares one linear colour domain containing the complete zonal extrema across this horizon. Original independently scaled single-scenario heatmaps remain available separately. Negative prices and all hourly observations are retained."
    return fig, pd.concat(rows, ignore_index=True), caption


def render(view, cases, zone, *, year=None, hourly_by_case=None):
    """Return ``figure/data/sources/qa/caption`` without saving any output.

    Views: ``duration``, ``monthly`` and ``comparison_heatmap``. Duration and
    monthly can include both horizons. A heatmap comparison requires one
    horizon with Slow, Base and High. Baselines come from the caller's verified
    ``config/publication.yaml`` selection.
    """
    cases = publication._ordered(list(cases))
    if year is not None:
        cases = [case for case in cases if int(case["year"]) == int(year)]
        if not cases:
            raise ValueError("PRICE_ATLAS_SELECTED_HORIZON_UNAVAILABLE")
    zone = _zone(zone)
    inp = publication._Inputs()
    cfg, style, font = publication._style()
    toolkit = _toolkit(cfg)[5]
    with plt.rc_context({"font.family": font, "font.size": 10, "axes.labelsize": 10,
                         "axes.titlesize": 12, "figure.facecolor": "white", "axes.facecolor": "white",
                         "text.color": "#263442", "axes.labelcolor": "#263442", "svg.fonttype": "none"}):
        if view == "duration":
            fig, data, caption = _duration(cases, zone, inp, style, toolkit, hourly_by_case)
        elif view == "monthly":
            fig, data, caption = _monthly(cases, zone, inp, style, hourly_by_case)
        elif view == "comparison_heatmap":
            fig, data, caption = _heatmap(cases, zone, inp, style, toolkit, hourly_by_case)
        else:
            raise ValueError(f"Unknown atlas view: {view!r}")
    fig._suptitle.set_color(style.carrier_style[zone]["color"])
    return dict(figure=fig, data=data, sources=inp.sources, caption=caption,
                qa=_qa(inp, atlas_view=view, zone=zone, font_resolved=font, plotted_data_rows=len(data),
                       scenario_order=list(SCENARIOS), display_zone_order=list(ZONES),
                       zone_color=style.carrier_style[zone]["color"],
                       rendered_visual_QA="CALLER_REQUIRED_ON_SAVED_PNG_AND_SVG"))


def existing_heatmap_options(cases, zone=None):
    """Link existing independently scaled images and their source metadata.

    This is a read-only catalog; it never regenerates the original heatmaps.
    The caller's Sources verifier should verify the returned source paths.
    """
    rows = []
    for case in publication._ordered(list(cases)):
        for market in ([_zone(zone)] if zone is not None else ZONES):
            stem = Path(case["report"]) / "VIS_X1/prices" / (market.lower() + "_price_heatmap")
            metadata_path = stem.with_suffix(".metadata.json")
            inp = publication._Inputs()
            metadata = inp.read(metadata_path)
            if (int(metadata["year"]) != int(case["year"]) or metadata["scenario"] != case["scenario"] or
                    metadata["price_source"] != "FIXED_COMMITMENT_LP_DUALS"):
                raise ValueError("PRICE_ATLAS_EXISTING_HEATMAP_IDENTITY_CONFLICT")
            paths = [Path(metadata[key]) for key in ("png", "svg") if key in metadata]
            source_paths = [Path(value) for value in metadata["underlying_data"]]
            if not all(path.is_file() for path in [*paths, *source_paths]):
                raise FileNotFoundError("Existing atlas heatmap or source missing")
            rows.append(dict(year=int(case["year"]), scenario=case["scenario"], variant=case.get("variant", ""),
                             zone=market, scale="INDEPENDENT_SINGLE_SCENARIO_FULL_EXTREMA", metadata=metadata_path,
                             png=stem.with_suffix(".png"), svg=stem.with_suffix(".svg"),
                             sources=[metadata_path, *source_paths], status="EXISTING_PRESERVED_OPTION"))
    return rows

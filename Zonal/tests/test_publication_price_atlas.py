"""Atlas semantics and sealed renderer integration; never solve or open a network."""
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest

from mem_model.visualization import publication_price_atlas as atlas
from mem_model.visualization import publication_figures as publication
from mem_model.visualization.vis_x1 import ZONE_ORDER


@pytest.fixture
def case(tmp_path):
    report = tmp_path / "REPORTING"
    stats = report / "CANONICAL/statistics"
    data = report / "VIS_X1/data"
    stats.mkdir(parents=True)
    data.mkdir(parents=True)
    index = pd.date_range("2019-01-01", periods=8760, freq="h", name="snapshot")
    phase = np.arange(8760) % 2
    prices = pd.DataFrame({zone: phase * 100.0 + offset * 10.0 for offset, zone in enumerate(ZONE_ORDER)}, index=index)
    prices.to_csv(report / "fixed_commitment_prices_hourly.csv")
    duration = pd.concat([pd.DataFrame({"market": publication._alias(zone), "snapshot": index, "price": prices[zone], "weight": 1.0}) for zone in ZONE_ORDER], ignore_index=True)
    duration.to_parquet(data / "fixed_commitment_lp_price_duration.parquet", index=False)
    dispatch, monthly, annual = [], [], []
    rigid, p2x = {}, {}
    for offset, zone in enumerate(ZONE_ORDER):
        rigid[zone] = np.full(8760, offset + 1.0)
        p2x[zone] = phase * 9.0
        load = rigid[zone] + p2x[zone]
        dispatch.append(pd.DataFrame({"snapshot": index, "zone": zone,
            "rigid_end_use_demand_MW": rigid[zone], "p2x_electrical_consumption_MW": p2x[zone], "gross_end_use_demand_MW": load}))
        for month in range(1, 13):
            mask = index.month == month
            monthly.append(dict(market=zone, month=month, mean_EUR_per_MWh=prices[zone].loc[mask].mean(),
                load_weighted_mean_EUR_per_MWh=np.average(prices[zone].loc[mask], weights=load[mask])))
        annual.append(dict(market=zone, arithmetic_mean_EUR_per_MWh=prices[zone].mean(),
            load_weighted_mean_EUR_per_MWh=np.average(prices[zone], weights=load)))
    d = pd.concat(dispatch, ignore_index=True)
    national_load = d.groupby("snapshot")[list(d.columns[2:])].sum().reset_index()
    national_load["zone"] = "NATIONAL"
    pd.concat([d, national_load], ignore_index=True).to_parquet(stats / "dispatch_8760_by_zone.parquet", index=False)
    gross = pd.DataFrame(rigid, index=index) + pd.DataFrame(p2x, index=index)
    hourly_national = (prices * gross).sum(axis=1) / gross.sum(axis=1)
    pd.DataFrame({"load_weighted_national_EUR_per_MWh": hourly_national}).to_parquet(stats / "uc2_price_hourly_all_italy_spread.parquet")
    national_monthly = []
    for month in range(1, 13):
        mask = index.month == month
        weight = gross.sum(axis=1).loc[mask]
        national_monthly.append(dict(month=month, load_weighted_mean_EUR_per_MWh=np.average(hourly_national.loc[mask], weights=weight), rigid_load_plus_P2X_MWh=weight.sum()))
    pd.DataFrame(national_monthly).to_csv(stats / "uc2_price_monthly_national_load_weighted.csv", index=False)
    pd.DataFrame(monthly).to_csv(stats / "uc2_price_monthly_zonal_means.csv", index=False)
    pd.DataFrame(annual).to_csv(stats / "uc2_price_annual_summary.csv", index=False)
    return dict(year=2040, scenario="Base", variant="final_v3", report=report)


def test_monthly_volume_mean_is_distinct_from_hourly_observation_percentiles(case):
    observed = atlas.monthly_price_statistics([case], "NORD")
    january = observed["data"].iloc[0]
    assert january.weighted_mean_EUR_per_MWh == pytest.approx(1000 / 11)
    assert january.arithmetic_mean_EUR_per_MWh == 50
    assert january.p25_EUR_per_MWh == 0
    assert january.p50_EUR_per_MWh == 50
    assert january.p75_EUR_per_MWh == 100
    assert january.observations == 744
    assert set(observed["data"].month) == set(range(1, 13))
    assert observed["qa"]["annual_means"][0]["annual_volume_weighted_mean_EUR_per_MWh"] == pytest.approx(1000 / 11)


def test_national_hourly_index_precedes_observation_quantiles(case):
    value = atlas.monthly_price_statistics([case])
    january = value["data"].iloc[0]
    assert january.zone == "NATIONAL"
    assert january.p25_EUR_per_MWh == 40
    assert january.p75_EUR_per_MWh == pytest.approx(12110 / 91)
    assert january.p50_EUR_per_MWh == pytest.approx((40 + 12110 / 91) / 2)
    assert january.weighted_mean_EUR_per_MWh == pytest.approx((1120 + 12110) / (28 + 91))
    assert value["qa"]["annual_means"][0]["volume_minus_time_mean_EUR_per_MWh"] > 0


def test_mutated_UC_gross_load_or_canonical_price_fails(case):
    path = Path(case["report"]) / "CANONICAL/statistics/dispatch_8760_by_zone.parquet"
    data = pd.read_parquet(path)
    data.loc[0, "gross_end_use_demand_MW"] += 10
    data.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="gross load equals rigid plus P2X"):
        atlas.hourly_price_data(case)


def test_cache_cannot_cross_report_or_variant_identity(case):
    value = atlas.hourly_price_data(case)
    other = dict(case, variant="final_v1")
    with pytest.raises(ValueError, match="CACHED_CASE_IDENTITY_CONFLICT"):
        atlas.monthly_price_statistics([other], hourly_by_case={(2040, "Base"): value})


def test_duration_reuses_toolkit_and_keeps_all_observations(case, monkeypatch):
    cfg, _, _ = publication._style()
    toolkit = atlas._toolkit(cfg)[5]
    original = toolkit.plot_price_duration
    calls = []
    def record(*args, **kwargs):
        calls.append(len(args[0]))
        return original(*args, **kwargs)
    monkeypatch.setattr(toolkit, "plot_price_duration", record)
    value = atlas.render("duration", [case], "NORD")
    assert calls == [8760, 8760]
    assert len(value["data"]) == 8760
    assert value["data"].value.is_monotonic_decreasing
    assert value["data"].x_end.iloc[-1] == 8760
    plt.close(value["figure"])


def test_heatmap_reuses_toolkit_with_shared_full_negative_domain(case, monkeypatch):
    cfg, _, _ = publication._style()
    toolkit = atlas._toolkit(cfg)[5]
    original = toolkit.plot_price_heatmap
    calls = []
    def record(*args, **kwargs):
        calls.append(args[1])
        return original(*args, **kwargs)
    monkeypatch.setattr(toolkit, "plot_price_heatmap", record)
    hourly = atlas.hourly_price_data(case)
    cases, cache = [], {}
    for scenario, shift in (("Slow", -150), ("Base", 0), ("High", 300)):
        current = dict(case, scenario=scenario)
        value = dict(hourly, prices=hourly["prices"] + shift)
        value["case_identity"] = dict(hourly["case_identity"], scenario=scenario)
        cache[(2040, scenario)] = value
        cases.append(current)
    value = atlas.render("comparison_heatmap", cases, "NORD", hourly_by_case=cache)
    assert calls == ["NORD"] * 3
    assert len(value["data"]) == 8760 * 3
    artists = [ax.images[0] for ax in value["figure"].axes if ax.images]
    assert len(artists) == 3
    assert all(image.norm.vmin == -150 and image.norm.vmax == 400 and not image.norm.clip for image in artists)
    assert value["data"].value.min() == -150
    plt.close(value["figure"])


def test_heatmap_requires_three_scenarios(case):
    with pytest.raises(ValueError, match="ALL_THREE_SCENARIOS"):
        atlas.render("comparison_heatmap", [case], "NORD")


def test_principal_monthly_layouts_preserve_identical_statistics(case):
    shared = publication.render("4", [case], presentation={"monthly_layout": "shared"})
    separate = publication.render("4", [case], presentation={"monthly_layout": "scenario_panels"})
    pd.testing.assert_frame_equal(shared["data"], separate["data"])
    assert {"p5_EUR_per_MWh", "p25_EUR_per_MWh", "p50_EUR_per_MWh", "p75_EUR_per_MWh", "p95_EUR_per_MWh"}.issubset(shared["data"].columns)
    plt.close(shared["figure"])
    plt.close(separate["figure"])


def test_principal_duration_is_linear_and_inset_does_not_cover_curve(case):
    value = publication.render("5", [case])
    assert value["figure"].axes[0].get_yscale() == "linear"
    assert len(value["data"]) == 8760
    check = next(row for row in value["qa"]["checks"] if row["check"] == "price_duration_inset_clearance")
    assert check["curve_max_EUR_per_MWh"] < check["inset_bottom_EUR_per_MWh"]
    plt.close(value["figure"])

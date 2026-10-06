import numpy as np
import pandas as pd
import pytest

from mem_model.visualization.dispatch_presentation import chronological_time_bins, raw_power_statistics
from mem_model.visualization.dispatch_presentation import (
    eight_hour_time_bins, presentation_stack_order, plot_eight_hour_presentation,
    POSITIVE_PRESENTATION_ORDER, NEGATIVE_PRESENTATION_ORDER, PRESENTATION_LABELS,
    refine_existing_presentation,
)


def test_unequal_intervals_and_component_weights_conserve_energy():
    index = pd.DatetimeIndex(["2019-01-01", "2019-01-01 00:15", "2019-01-01 01:15"])
    values = pd.DataFrame({"generation": [12., 3., 9.], "withdrawal": [-8., -1., -5.]}, index=index)
    durations = pd.Series([.25, 1., .75], index=index)
    weights = pd.DataFrame({"generation": [.5, 2., 1.5], "withdrawal": [.25, 1., .75]}, index=index)
    original = values.copy(deep=True)
    means, boundaries, exposure = chronological_time_bins(values, durations, weights, 2)
    assert means.generation.iloc[0] == pytest.approx(5.25)
    assert means.withdrawal.iloc[0] == pytest.approx(-2.75)
    np.testing.assert_allclose((means * exposure).sum(), (values * weights).sum())
    assert boundaries.duration_hours.eq(1.).all()
    assert boundaries.bin_end.iloc[-1] == pd.Timestamp("2019-01-01 02:00")
    pd.testing.assert_frame_equal(values, original)
    repeated = chronological_time_bins(values, durations, weights, 2)
    for first, second in zip((means, boundaries, exposure), repeated):
        pd.testing.assert_frame_equal(first, second)


def test_full_year_bins_are_time_bins_and_extrema_remain_raw():
    index = pd.date_range("2019-01-01", periods=8760, freq="h")
    raw = pd.DataFrame({"MW": np.arange(8760.)}, index=index)
    weights = pd.DataFrame(1., index=index, columns=raw.columns)
    display, bounds, exposure = chronological_time_bins(raw, pd.Series(1., index=index), weights)
    assert len(display) == 1000 and bounds.duration_hours.sum() == pytest.approx(8760)
    np.testing.assert_allclose(bounds.duration_hours, 8.76)
    assert display.MW.is_monotonic_increasing
    assert display.MW.max() < raw.MW.max()
    stats = raw_power_statistics(raw, weights)
    assert stats.loc["MW", "maximum_raw_MW"] == 8759
    assert stats.loc["MW", "energy_MWh"] == raw.MW.sum()
    assert (display * exposure).MW.sum() == pytest.approx(raw.MW.sum())


def test_import_export_signs_split_before_averaging():
    index = pd.date_range("2019-01-01", periods=2, freq="h")
    net = pd.Series([10., -10.], index=index)
    values = pd.DataFrame({"import": net.clip(lower=0), "export": net.clip(upper=0)})
    means, _, _ = chronological_time_bins(values, pd.Series(1., index=index), values * 0 + 1, 1)
    assert means.iloc[0].tolist() == [5., -5.]


@pytest.mark.parametrize("fault", ["missing", "duplicate", "gap", "misalignment", "zero_weight"])
def test_invalid_evidence_is_rejected(fault):
    index = pd.date_range("2019-01-01", periods=3, freq="h")
    values = pd.DataFrame({"MW": [1., 2., 3.]}, index=index)
    durations = pd.Series(1., index=index)
    weights = values * 0 + 1
    if fault == "missing":
        values.iloc[1, 0] = np.nan
    elif fault == "duplicate":
        values.index = pd.DatetimeIndex([index[0], index[0], index[2]])
    elif fault == "gap":
        durations.iloc[0] = .5
    elif fault == "misalignment":
        weights.index = index + pd.Timedelta(hours=1)
    else:
        weights.iloc[1, 0] = 0
    with pytest.raises(ValueError):
        chronological_time_bins(values, durations, weights, 2)


def test_eight_hour_full_year_grid_overlay_and_independent_energy():
    index = pd.date_range("2019-01-01", periods=8760, freq="h")
    raw = pd.DataFrame({"Geothermal": np.full(8760, 2.), "Solar PV - utility": np.arange(8760.),
                        "BESS charging": -np.arange(8760.), "Rigid load": np.full(8760, 10.)}, index=index)
    duration = pd.Series(1., index=index)
    weights = raw * 0 + 1
    before_stats = raw_power_statistics(raw, weights)
    display, bounds, exposure = eight_hour_time_bins(raw, duration)
    assert len(display) == 1095
    assert bounds.duration_hours.eq(8).all()
    assert display.index.equals(bounds.index) and display.index.equals(exposure.index)
    assert display.index.equals(pd.date_range("2019-01-01", "2020-01-01", freq="8h", inclusive="left", name="bin_start"))
    assert bounds.bin_end.iloc[-1] == pd.Timestamp("2020-01-01")
    assert set(display.index.hour) == {0, 8, 16}
    for columns in (["Geothermal", "Solar PV - utility"], ["BESS charging"], ["Rigid load"]):
        np.testing.assert_allclose((display[columns]*exposure[columns]).sum(), (raw[columns]*weights[columns]).sum(), atol=.01, rtol=1e-10)
    assert display["Solar PV - utility"].iloc[0] == 3.5
    assert before_stats.loc["Solar PV - utility", "maximum_raw_MW"] == 8759
    pd.testing.assert_frame_equal(before_stats, raw_power_statistics(raw, weights))


def test_eight_hour_unequal_durations_split_crossing_snapshot():
    index = pd.DatetimeIndex(["2019-01-01 00:00", "2019-01-01 07:30", "2019-01-01 10:00"])
    raw = pd.DataFrame({"positive": [2., 12., 4.], "negative": [-1., -8., -2.]}, index=index)
    duration = pd.Series([7.5, 2.5, 14.], index=index)
    display, bounds, exposure = eight_hour_time_bins(raw, duration)
    # Middle observation overlaps the 08:00 edge; the last spans the 16:00 edge.
    np.testing.assert_allclose(display.positive, [21/8, 48/8, 4])
    np.testing.assert_allclose(display.negative, [-11.5/8, -28/8, -2])
    np.testing.assert_allclose((display*exposure).sum(), raw.mul(duration, axis=0).sum())
    assert bounds.duration_hours.eq(8).all()


def test_eight_hour_coarse_snapshot_spans_many_display_bins():
    raw = pd.DataFrame({"MW": [5.]}, index=pd.DatetimeIndex(["2019-01-01"]))
    display, _, exposure = eight_hour_time_bins(raw, pd.Series([8760.], index=raw.index))
    assert len(display) == 1095
    np.testing.assert_allclose(display.MW, 5, atol=1e-10, rtol=0)
    assert (display*exposure).MW.sum() == pytest.approx(43800)


def test_required_presentation_stack_order_regression():
    expected = ["Geothermal", "Bioenergy - steam thermal", "Bioenergy - other thermal", "Bioenergy - internal combustion",
                "Gas - CCGT", "Gas - internal combustion", "Hydro - reservoir", "Hydro - basin/pondage", "Hydro - run of river",
                "PHS", "BESS", "Net imports", "Offshore wind", "Onshore wind", "Solar PV - rooftop", "Solar PV - utility"]
    assert list(POSITIVE_PRESENTATION_ORDER) == expected
    raw = pd.DataFrame({c: [1.] for c in reversed(expected)})
    for c in NEGATIVE_PRESENTATION_ORDER:
        raw[c] = -1.
    raw["Rigid load"] = 10.
    assert presentation_stack_order(raw) == expected + list(NEGATIVE_PRESENTATION_ORDER)
    assert PRESENTATION_LABELS == {"PHS": "PHS discharge", "BESS": "BESS discharge"}


def test_primary_renderer_uses_cached_daily_delta(monkeypatch):
    from mem_model.visualization import dispatch_presentation as module
    calls = []
    def cached(year, scenario):
        calls.append((year, scenario))
        return "cached display"
    def forbidden(*args, **kwargs):
        raise AssertionError("Initial extraction must not run")
    monkeypatch.setattr(module, "refine_daily_case", cached)
    monkeypatch.setattr(module, "_render_initial_case_legacy", forbidden)
    assert module.render_case(2040, "Base") == "cached display"
    assert calls == [(2040, "Base")]
    with pytest.raises(ValueError):
        module.render_case(2040, "Base", n_bins=1000)


def test_eight_hour_renderer_has_borderless_areas_and_shared_load_grid():
    import matplotlib.pyplot as plt
    from matplotlib import dates
    index = pd.date_range("2019-01-01", periods=24, freq="h")
    raw = pd.DataFrame({"Geothermal": 2., "Solar PV - utility": np.arange(24.), "BESS charging": -1., "Rigid load": 10.}, index=index)
    display, bounds, _ = eight_hour_time_bins(raw, pd.Series(1., index=index))
    colors = {c: {"color": color} for c, color in zip(raw, ("#C65D21", "#E6A700", "#9E77ED", "#222222"))}
    fig, _ = plot_eight_hour_presentation(display, bounds, colors, year=2040, scenario="Base")
    ax = fig.axes[0]
    assert len(ax.collections) == 3  # One continuous polygon per active component.
    for collection in ax.collections[:2]:
        assert len(collection.get_paths()) == 1
        assert not collection.get_edgecolors().size
        assert np.all(collection.get_linewidths() == 0)
    load = next(line for line in ax.lines if line.get_label() == "Rigid load")
    expected_index = display.index.append(pd.DatetimeIndex([bounds.bin_end.iloc[-1]]))
    assert list(pd.DatetimeIndex(load.get_xdata())) == list(expected_index)
    np.testing.assert_allclose(load.get_ydata()[:-1], display["Rigid load"] / 1000)
    # Every polygon uses exactly these boundaries; no raw hourly load overlay.
    for collection in ax.collections:
        x = np.unique(collection.get_paths()[0].vertices[:, 0])
        np.testing.assert_allclose(x, dates.date2num(expected_index))
    plt.close(fig)


def test_eight_hour_delta_preserves_audit_canonical_network_and_never_opens_model(tmp_path, monkeypatch):
    import json
    import hashlib
    import matplotlib.pyplot as plt
    import xarray as xr
    import pypsa
    from mem_model import common
    from mem_model.visualization import dispatch_presentation as module
    monkeypatch.setattr(common, "ROOT", tmp_path)
    out = tmp_path / "outputs/visualization/UC2/2040/Base/dispatch_presentation"
    out.mkdir(parents=True)
    index = pd.date_range("2019-01-01", periods=8760, freq="h", name="snapshot")
    raw = pd.DataFrame({"Geothermal": 10., "Rigid load": 8., "BESS charging": -2.}, index=index)
    raw.assign(interval_hours=1., generator_weight_hours=1., objective_weight_hours=1.).to_csv(out / "annual_dispatch_raw_MW.csv")
    raw_power_statistics(raw, raw * 0 + 1).to_csv(out / "annual_statistics_from_raw.csv")
    for ext in ("png", "svg"):
        (out / f"annual_dispatch_raw_audit.{ext}").write_bytes(b"retained audit")
    report = tmp_path / "results/uc2_full_year/2040/Base/REPORTING"
    report.mkdir(parents=True)
    canonical = report / "canonical.csv"; canonical.write_bytes(b"accepted canonical table")
    network = tmp_path / "SOLVED.nc"; network.write_bytes(b"never open as a model")
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    originals = {p: digest(p) for p in [canonical, network, *out.iterdir()]}
    parent = {"status": "PASS", "result_status": "CURRENT_ACCEPTED_RESULT", "physical_source": "UC_MILP", "year": 2040, "scenario": "Base",
              "font_requested": "Aptos", "semantic_colors": {"Geothermal": {"color": "#C65D21"}, "BESS charging": {"color": "#9E77ED"}},
              "input_hashes": {str(p): digest(p) for p in (canonical, network)},
              "artifact_hashes": {p.name: digest(p) for p in out.iterdir()}}
    (out / "PRESENTATION_RECEIPT.json").write_text(json.dumps(parent))
    def forbidden(*args, **kwargs):
        raise AssertionError("Model opening/processing forbidden in presentation delta")
    monkeypatch.setattr(xr, "open_dataset", forbidden)
    monkeypatch.setattr(pypsa.Network, "__init__", forbidden)
    # The renderer is tested above; isolate artifact/solver boundaries here.
    monkeypatch.setattr(module, "plot_eight_hour_presentation", lambda *a, **k: (plt.figure(), "DejaVu Sans"))
    receipt = refine_existing_presentation()
    assert receipt["display_intervals"] == 1095
    assert receipt["solver_invocations"] == receipt["network_open_or_extraction_calls"] == 0
    assert all(digest(p) == value for p, value in originals.items())


def daily_raw_fixture(index):
    from mem_model.visualization.dispatch_presentation import DAILY_GROUPS, DAILY_ZERO_ONLY_EXCLUSIONS
    columns = [c for sources in DAILY_GROUPS.values() for c in sources] + list(DAILY_ZERO_ONLY_EXCLUSIONS)
    return pd.DataFrame(0., index=index, columns=columns)


def test_daily_2019_coverage_shared_grid_raw_extrema_and_energy():
    from mem_model.visualization.dispatch_presentation import daily_time_bins, group_daily_presentation
    index = pd.date_range('2019-01-01', periods=8760, freq='h', name='snapshot')
    raw = daily_raw_fixture(index)
    raw['Solar PV - utility'] = np.arange(8760.)
    raw['Rigid load'] = raw['Solar PV - utility']
    duration = pd.Series(1., index=index)
    before = raw_power_statistics(raw, raw * 0 + 1)
    detailed, bounds, exposure, allocation = daily_time_bins(raw, duration)
    grouped = group_daily_presentation(detailed)
    assert len(grouped) == 365 and grouped.index.is_unique and grouped.index.is_monotonic_increasing
    assert grouped.index.equals(pd.date_range('2019-01-01', periods=365, freq='D', name='bin_start'))
    assert bounds.bin_end.iloc[-1] == pd.Timestamp('2020-01-01')
    assert bounds.duration_hours.eq(24).all() and exposure.eq(24).all().all()
    assert grouped.index.equals(bounds.index) and grouped.index.equals(exposure.index)
    assert bounds.duration_hours.sum() == duration.sum() == 8760
    np.testing.assert_allclose(allocation.allocated_hours, duration, atol=1e-8, rtol=0)
    np.testing.assert_allclose((detailed * exposure).sum(), before.energy_MWh, atol=.01, rtol=1e-10)
    assert grouped['Solar PV'].iloc[0] == 11.5
    assert grouped['Solar PV'].iloc[-1] == 8747.5
    pd.testing.assert_frame_equal(before, raw_power_statistics(raw, raw * 0 + 1))
    assert before.loc['Solar PV - utility', 'maximum_raw_MW'] == 8759


def test_daily_cross_midnight_and_multiday_snapshot_allocation():
    from mem_model.visualization.dispatch_presentation import daily_time_bins
    index = pd.DatetimeIndex(['2019-01-01', '2019-01-01 23:30', '2019-01-02 02:00'])
    raw = pd.DataFrame({'supply': [2., 12., 4.], 'withdrawal': [-1., -8., -2.]}, index=index)
    duration = pd.Series([23.5, 2.5, 8734.], index=index)
    display, _, exposure, allocation = daily_time_bins(raw, duration)
    np.testing.assert_allclose(display.supply.iloc[:2], [53/24, 112/24])
    np.testing.assert_allclose(display.withdrawal.iloc[:2], [-27.5/24, -60/24])
    np.testing.assert_allclose((display * exposure).sum(), raw.mul(duration, axis=0).sum(), atol=.01, rtol=1e-10)
    np.testing.assert_allclose(allocation.allocated_hours, duration, atol=1e-8, rtol=0)
    assert allocation.display_days_contributed.iloc[1] == 2
    assert allocation.display_days_contributed.iloc[2] == 364


def test_daily_directions_do_not_cancel_and_groups_preserve_balance():
    from mem_model.visualization.dispatch_presentation import daily_time_bins, group_daily_presentation, DAILY_GROUPS
    index = pd.date_range('2019-01-01', periods=8760, freq='h')
    raw = daily_raw_fixture(index)
    alternating = np.tile([10., -10.], 4380)
    raw['Net imports'] = np.maximum(alternating, 0)
    raw['Net exports'] = np.minimum(alternating, 0)
    for discharge, charge in [('BESS', 'BESS charging'), ('PHS', 'PHS charging')]:
        raw[discharge] = np.maximum(alternating, 0)
        raw[charge] = np.minimum(alternating, 0)
    raw['P2X withdrawal'] = -2.
    raw['Geothermal'] = 100.
    raw['Rigid load'] = raw.drop(columns='Rigid load').sum(axis=1)
    duration = pd.Series(1., index=index)
    detailed, bounds, exposure, _ = daily_time_bins(raw, duration)
    display = group_daily_presentation(detailed)
    for family in ['Imports', 'BESS discharge', 'PHS discharge']:
        assert display[family].eq(5).all()
    for family in ['Exports', 'BESS charging', 'PHS charging']:
        assert display[family].eq(-5).all()
    assert display['P2X withdrawal'].eq(-2).all()
    for family, sources in DAILY_GROUPS.items():
        assert (display[family]*bounds.duration_hours).sum() == pytest.approx(raw[list(sources)].mul(duration, axis=0).sum().sum())
    raw_residual = raw.drop(columns='Rigid load').sum(axis=1)-raw['Rigid load']
    daily_residual = display.drop(columns='Rigid load').sum(axis=1)-display['Rigid load']
    np.testing.assert_allclose(daily_residual, 0, atol=.001, rtol=0)
    assert (daily_residual*bounds.duration_hours).sum() == pytest.approx((raw_residual*duration).sum(), abs=.01)


@pytest.mark.parametrize('fault', ['unexpected', 'missing', 'active_exclusion', 'sign'])
def test_daily_exact_mapping_fails_closed(fault):
    from mem_model.visualization.dispatch_presentation import group_daily_presentation
    raw = daily_raw_fixture(pd.date_range('2019-01-01', periods=2, freq='h'))
    if fault == 'unexpected': raw['Unmapped even at zero'] = 0.
    elif fault == 'missing': raw = raw.drop(columns='Geothermal')
    elif fault == 'active_exclusion': raw['Gas - OCGT'] = 1.
    else: raw['P2X withdrawal'] = 1.
    with pytest.raises(ValueError): group_daily_presentation(raw)


@pytest.mark.parametrize('fault', ['timezone', 'shifted_year', 'missing_hour'])
def test_daily_chronology_discrepancies_fail_closed(fault):
    from mem_model.visualization.dispatch_presentation import daily_time_bins
    index = pd.date_range('2019-01-01', periods=8760, freq='h')
    if fault == 'timezone': index = index.tz_localize('UTC')
    elif fault == 'shifted_year': index = index + pd.Timedelta(days=1)
    else: index = index.delete(100)
    raw = pd.DataFrame({'MW': 1.}, index=index)
    with pytest.raises(ValueError): daily_time_bins(raw, pd.Series(1., index=index))


def test_daily_fixed_order_and_interval_rendering_no_hatches_or_diagonals():
    import matplotlib.pyplot as plt
    from matplotlib import dates
    from mem_model.visualization.dispatch_presentation import (
        daily_time_bins, group_daily_presentation, plot_daily_presentation, DAILY_POSITIVE_ORDER, DAILY_NEGATIVE_ORDER)
    assert list(DAILY_POSITIVE_ORDER) == ['Geothermal', 'Bioenergy', 'Gas', 'Hydro - reservoir/pondage', 'Run-of-river',
        'PHS discharge', 'BESS discharge', 'Imports', 'Offshore wind', 'Onshore wind', 'Solar PV']
    assert list(DAILY_NEGATIVE_ORDER) == ['BESS charging', 'PHS charging', 'P2X withdrawal', 'Exports']
    index = pd.date_range('2019-01-01', periods=8760, freq='h')
    raw = daily_raw_fixture(index)
    raw['Geothermal'] = np.repeat(np.arange(365.) + 10, 24)
    raw['BESS charging'] = -2.
    raw['Rigid load'] = raw['Geothermal'] - 2.
    detailed, bounds, _, _ = daily_time_bins(raw, pd.Series(1., index=index))
    display = group_daily_presentation(detailed)
    colors = {c: {'color': '#9E77ED' if c in DAILY_NEGATIVE_ORDER else '#C65D21'} for c in display}
    fig, _ = plot_daily_presentation(display, bounds, colors, year=2040, scenario='Base')
    ax = fig.axes[0]
    assert len(ax.collections) == 2
    edges = display.index.append(pd.DatetimeIndex([bounds.bin_end.iloc[-1]]))
    for collection in ax.collections:
        assert not collection.get_hatch()
        assert not collection.get_edgecolors().size
        assert np.all(collection.get_linewidths() == 0)
        vertices = collection.get_paths()[0].vertices
        np.testing.assert_allclose(np.unique(vertices[:, 0]), dates.date2num(edges))
        difference = np.diff(vertices, axis=0)
        assert np.all((difference[:, 0] == 0) | (difference[:, 1] == 0))  # No diagonal interpolation.
    load = next(line for line in ax.lines if line.get_label() == 'Rigid load')
    assert load.get_drawstyle() == 'steps-post'
    assert load.get_linewidth() == .65
    assert load.get_color() == '#333333'
    assert all(text.get_fontsize() == 9.5 for text in ax.get_legend().get_texts())
    assert pd.DatetimeIndex(load.get_xdata()).equals(edges)
    np.testing.assert_allclose(load.get_ydata()[:-1], display['Rigid load']/1000)
    plt.close(fig)


@pytest.mark.parametrize('scenario', ['Base', 'Slow', 'High'])
def test_daily_cached_artifacts_no_models_no_solves_and_no_other_scenario_mutation(tmp_path, monkeypatch, scenario):
    import json, hashlib
    import matplotlib.pyplot as plt
    import xarray as xr
    import pypsa
    import linopy
    from mem_model import common
    from mem_model.visualization import dispatch_presentation as module
    monkeypatch.setattr(common, 'ROOT', tmp_path)
    out = tmp_path/'outputs/visualization/UC2/2040'/scenario/'dispatch_presentation'
    out.mkdir(parents=True)
    index = pd.date_range('2019-01-01', periods=8760, freq='h', name='snapshot')
    raw = daily_raw_fixture(index)
    raw['Geothermal'] = 10.; raw['Rigid load'] = 8.; raw['BESS charging'] = -2.
    raw.assign(interval_hours=1., generator_weight_hours=1., objective_weight_hours=1.).to_csv(out/'annual_dispatch_raw_MW.csv')
    raw_power_statistics(raw, raw*0+1).to_csv(out/'annual_statistics_from_raw.csv')
    for name in ['annual_dispatch_raw_audit.png','annual_dispatch_raw_audit.svg',
                 'annual_dispatch_chronological_1095.png','annual_dispatch_time_bins_8h_MW.csv']:
        (out/name).write_bytes(b'accepted retained artifact')
    network=tmp_path/'SOLVED.nc';network.write_bytes(b'never open as a model')
    canonical=tmp_path/'CANONICAL.csv';canonical.write_bytes(b'accepted canonical data')
    cfg=tmp_path/'config/stage_b_reporting.yaml';cfg.parent.mkdir()
    cfg.write_text('electrical_balance_identity: '+module.ACCEPTED_BALANCE_IDENTITY+'\nbalance_tolerance_TWh: 0.000001\ntechnology_colors:\n  Bioenergy: "#4E9A51"\n')
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    parent={'status':'PASS','year':2040,'scenario':scenario,'result_status':'CURRENT_ACCEPTED_RESULT','physical_source':'UC_MILP',
            'source_timestamp_semantics':'naive saved 2019','font_requested':'Aptos',
            'semantic_colors':{c:{'color':'#C65D21'} for c in raw},
            'input_hashes':{str(p):digest(p) for p in [network,canonical]},
            'artifact_hashes':{p.name:digest(p) for p in out.iterdir()}}
    (out/'PRESENTATION_RECEIPT.json').write_text(json.dumps(parent))
    diagnostic={**parent,'status':'NUMERICAL_PASS_AWAITING_USER_VISUAL_REVIEW',
                'preserved_source_hashes':parent['input_hashes'],
                'artifact_hashes':{name:digest(out/name) for name in ['annual_dispatch_chronological_1095.png','annual_dispatch_time_bins_8h_MW.csv']}}
    if scenario == 'Base':
        (out/'PRESENTATION_8H_RECEIPT.json').write_text(json.dumps(diagnostic))
    originals={p:digest(p) for p in [network,canonical,cfg,*out.iterdir()]}
    def forbidden(*a, **k): raise AssertionError('Model processing or solver forbidden')
    monkeypatch.setattr(xr,'open_dataset',forbidden)
    monkeypatch.setattr(pypsa.Network,'__init__',forbidden)
    monkeypatch.setattr(linopy.Model,'solve',forbidden)
    captured=[]
    def plot(frame,*a,**k): captured.append(frame.copy()); return plt.figure(),'DejaVu Sans'
    monkeypatch.setattr(module,'plot_daily_presentation',plot)
    receipt=module.refine_daily_case(2040, scenario)
    assert receipt['scenario'] == scenario
    assert receipt['prior_eight_hour_diagnostic_present'] == (scenario == 'Base')
    assert receipt['display_intervals']==365
    assert receipt['solver_invocations']==receipt['network_open_or_extraction_calls']==0
    assert all(digest(p)==value for p,value in originals.items())
    exported=pd.read_csv(out/'annual_dispatch_chronological_daily_MW.csv',index_col='bin_start',parse_dates=True)
    pd.testing.assert_frame_equal(exported[captured[0].columns],captured[0],check_freq=False)
    assert exported.represented_hours.eq(24).all()
    assert len(receipt['checks']['independent_directional_energy_errors_MWh'])==7
    for scenario in ['Slow','High']:
        with pytest.raises(ValueError,match='restricted'): module.refine_daily_base(2040,scenario)

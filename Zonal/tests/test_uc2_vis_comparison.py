"""Source gates for comparisons must preserve UC2 generation and split authority."""
import json

import pytest
import pandas as pd
import yaml

from mem_model.visualization.uc2_comparison import _validate_sources


def test_scenario_colours_are_distinct_and_stable_for_subsets():
    from mem_model.visualization.vis_x1 import CONFIG, _toolkit
    from mem_model.visualization.uc2_comparison import _comparison_style
    _toolkit(yaml.safe_load(CONFIG.read_text(encoding="utf-8")))
    from visualization_toolkit.styles import PlotStyle
    complete = _comparison_style(pd.DataFrame({"scenario_id": ["Slow", "Base", "High"]}), PlotStyle)
    subset = _comparison_style(pd.DataFrame({"scenario_id": ["Base", "High"]}), PlotStyle)
    assert len({complete.scenario_color(s) for s in ("Slow", "Base", "High")}) == 3
    assert all(complete.scenario_color(s) == subset.scenario_color(s) for s in ("Base", "High"))


def test_comparison_reuses_accepted_technology_and_zone_colours():
    from mem_model.visualization.vis_x1 import CONFIG, _toolkit
    from mem_model.visualization.uc2_comparison import _comparison_style
    from mem_model.reporting.canonical_results import reporting_config
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    _toolkit(cfg)
    from visualization_toolkit.styles import PlotStyle
    from adapters.pypsa_it_adapter import RECOVERED_R2B_ZONE_COLORS
    style = _comparison_style(pd.DataFrame({"scenario_id": ["Slow", "Base", "High"]}), PlotStyle)
    for technology, colour in reporting_config()["technology_colors"].items():
        assert style.carrier_style[technology]["color"] == colour
    for zone, colour in RECOVERED_R2B_ZONE_COLORS.items():
        assert style.carrier_style[cfg["display_market_aliases"][zone]]["color"] == colour


def test_cached_comparison_polish_preserves_sources_and_never_opens_or_solves(tmp_path, monkeypatch):
    import shutil
    import linopy
    import pypsa
    import xarray
    from mem_model.common import ROOT
    from mem_model.visualization.vis_x1 import _sha
    from mem_model.visualization.uc2_comparison import refine_cached_comparison_presentation
    source = ROOT / "results/uc2_full_year/2040/COMPARISONS_VIS_X1"
    if not source.exists():
        pytest.skip("Accepted cached comparison artifacts are not installed")
    cached = tmp_path / "cached"
    (cached / "data").mkdir(parents=True)
    for relative in ("data/comparison_metrics.csv", "data/scenario_metadata.csv",
                     "MEM_UC2_COMPARISON_MANIFEST.csv", "MEM_UC2_COMPARISON_RECEIPT.json"):
        shutil.copyfile(source / relative, cached / relative)
    before = {p: _sha(p) for p in cached.rglob("*") if p.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail("Cached presentation must not open networks or call a solver")

    monkeypatch.setattr(pypsa.Network, "__init__", forbidden)
    monkeypatch.setattr(linopy.Model, "solve", forbidden)
    monkeypatch.setattr(xarray, "open_dataset", forbidden)
    output = tmp_path / "presentation"
    receipt = refine_cached_comparison_presentation(cached, output)
    assert receipt["status"] == "PASS"
    assert receipt["solver_invocations"] == receipt["network_open_or_extraction_calls"] == 0
    assert {p: _sha(p) for p in before} == before
    assert receipt["plotted_values_checked"] == 81
    assert len(list(output.glob("*.png"))) == len(list(output.glob("*.svg"))) == 3
    for name, digest in receipt["artifact_hashes"].items():
        assert _sha(output / name) == digest


def test_final_selection_from_cached_tables_preserves_duration_values_and_authority(tmp_path, monkeypatch):
    import numpy as np
    import pypsa
    import xarray
    from mem_model.common import ROOT
    from mem_model.visualization.vis_x1 import _sha
    from mem_model.visualization.uc2_comparison import refine_cached_comparison_presentation
    from mem_model.reporting.canonical_results import reporting_config
    source = ROOT / 'results/uc2_full_year/2040/COMPARISONS_VIS_X1'
    if not source.exists():
        pytest.skip('Accepted cached comparison artifacts are not installed')
    before = {p: _sha(p) for p in source.rglob('*') if p.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail('Final comparison selection must never open a network')

    monkeypatch.setattr(pypsa.Network, '__init__', forbidden)
    monkeypatch.setattr(xarray, 'open_dataset', forbidden)
    scenarios = ['Slow', 'Base', 'High']
    reports = {s: ROOT / f'results/uc2_full_year/2040/{s}/REPORTING' for s in scenarios}
    output = tmp_path / 'selected'
    receipt = refine_cached_comparison_presentation(source, output, finalize_selection=True, case_reports=reports)
    assert receipt['status'] == 'PASS' and receipt['figure_count'] == 11
    assert receipt['solver_invocations'] == receipt['network_open_or_extraction_calls'] == 0
    assert before == {p: _sha(p) for p in before}
    average = pd.read_csv(output / 'average_committed_capacity_GW.csv').set_index('scenario')
    for s in scenarios:
        hourly = pd.read_csv(reports[s] / 'hourly_online_units_and_committed_MW.csv')
        np.testing.assert_allclose(average.loc[s, 'average_committed_capacity_GW'], hourly.committed_MW.mean() / 1000,
                                   atol=1e-12, rtol=0)
    original = pd.read_csv(source / 'data/all_italy_spatial_spread.csv')
    ranked = pd.read_csv(output / 'italian_zonal_spread_duration.csv')
    assert not ranked.duplicated(['scenario', 'snapshot']).any()
    for s in scenarios:
        raw = original.loc[original.scenario.eq(s)]
        displayed = ranked.loc[ranked.scenario.eq(s)]
        assert len(displayed) == 8760
        assert displayed.represented_hours.eq(1).all() and displayed.represented_hours.sum() == 8760
        np.testing.assert_array_equal(displayed.rank_hour, np.arange(1, 8761))
        assert displayed.spread_EUR_per_MWh.is_monotonic_decreasing
        np.testing.assert_allclose(np.sort(displayed.spread_EUR_per_MWh), np.sort(raw.spread_EUR_per_MWh), atol=1e-12, rtol=0)
        assert displayed.spread_EUR_per_MWh.eq(0).sum() == raw.spread_EUR_per_MWh.eq(0).sum()
    colours = reporting_config()['technology_colors']
    for name, technologies in {'storage_activity': ['BESS', 'PHS'],
                               'vre_curtailment': ['Solar PV - rooftop', 'Solar PV - utility', 'Onshore wind', 'Offshore wind'],
                               'actual_starts': ['Bioenergy - steam thermal', 'Gas - CCGT', 'Gas - OCGT']}.items():
        svg = (output / f'{name}.svg').read_text().lower()
        assert all(colours[t].lower() in svg for t in technologies)
        assert 'technology' in svg
    assert not (output / 'uc_uplift.png').exists()
    assert not (output / 'actual_shutdowns.png').exists()
    assert len(list(output.glob('*.png'))) == len(list(output.glob('*.svg'))) == 11
    for name, digest in receipt['artifact_hashes'].items():
        assert _sha(output / name) == digest


def reports(tmp_path, **change):
    output = {}
    for scenario in ("Different-A", "Different-B"):
        path = tmp_path / scenario
        path.mkdir()
        receipt = dict(status="PASS", model_generation="UC2", year=2050, scenario=scenario,
                       physical_source="UC_MILP", price_source="FIXED_COMMITMENT_PRICE_LP",
                       reporting_solver_invocations=0)
        if scenario == "Different-B":
            receipt.update(change)
        (path / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json").write_text(json.dumps(receipt))
        output[scenario] = path
    return output


def test_comparison_scenarios_and_year_come_from_verified_metadata(tmp_path):
    metadata, hashes = _validate_sources(2050, reports(tmp_path))
    assert metadata.display_name.tolist() == ["Different-A", "Different-B"]
    assert metadata.year.eq(2050).all()
    assert set(hashes) == set(metadata.scenario_id)
    assert all(len(value) == 64 for value in hashes.values())


@pytest.mark.parametrize("change", [
    {"status": "PRE_P2X_DIAGNOSTIC_BASELINE"},
    {"model_generation": "PRE_P2X"},
    {"year": 2040},
    {"physical_source": "FIXED_COMMITMENT_PRICE_LP"},
    {"price_source": "UC_MILP"},
    {"reporting_solver_invocations": 1},
    {"scenario": "Wrong"},
])
def test_comparison_refuses_incompatible_generation_or_authority(tmp_path, change):
    with pytest.raises(ValueError, match="Incomparable or unverified"):
        _validate_sources(2050, reports(tmp_path, **change))


def test_comparison_requires_completed_multiple_scenarios(tmp_path):
    paths = reports(tmp_path)
    with pytest.raises(ValueError, match="at least two"):
        _validate_sources(2050, {"Different-A": paths["Different-A"]})

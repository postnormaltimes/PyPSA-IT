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


@pytest.mark.parametrize("year", [2040, 2050])
def test_current_variant_partial_collection_is_not_zero_filled(year, tmp_path, monkeypatch):
    from mem_model import stage_b_uc2 as uc2
    from mem_model.reporting.uc2_postprocess import update_uc2_comparisons
    from mem_model.visualization import uc2_comparison

    version = "final_methodology_v3" if year == 2040 else "final_methodology_v4"
    monkeypatch.setattr(uc2, "case_root", lambda y, scenario: tmp_path / version / str(y) / scenario)
    report = uc2.case_root(year, "Base") / "REPORTING"
    report.mkdir(parents=True)
    (report / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json").write_text("{}")
    monkeypatch.setattr(uc2_comparison, "build_uc2_comparisons",
                        lambda *_: pytest.fail("Incomplete cases must not be rendered as zero"))
    with uc2.execution_variant("final_v4"):
        result = update_uc2_comparisons(year)
    assert result['status'] == 'WAITING_FOR_COMPARABLE_REPORTS' and result['cases'] == 1
    assert set(result['missing_scenarios']) == {'Slow', 'High'}
    assert len(result['selected_views']) == 18
    assert all(view['status'] == 'WAITING_FOR_COMPARABLE_REPORTS' for view in result['selected_views'])


def versioned_reports(tmp_path, version="FINAL_METHODOLOGY_V4", year=2050):
    from mem_model.visualization.vis_x1 import _sha
    paths = reports(tmp_path)
    for scenario, report in paths.items():
        authority = pd.DataFrame([
            dict(year=year, scenario=scenario, parent_model_version=version,
                 current_role=role, result_id=f"{version}_{year}_{scenario}_{kind}",
                 solved_or_unsolved="SOLVED", UC_status="VERIFIED_UC2",
                 visualization_allowed=True, network_path=str(report.parent / kind / "SOLVED.nc"),
                 network_sha256=("a" if kind == "UC_MILP" else "b") * 64)
            for role, kind in (("CANONICAL_PHYSICAL", "UC_MILP"),
                               ("CANONICAL_PRICES_DUALS_ONLY", "FIXED_COMMITMENT_PRICE_LP"))
        ])
        authority_path = report / "uc2_VIS_RESULT_AUTHORITY_REGISTER.csv"
        authority.to_csv(authority_path, index=False)
        receipt_path = report / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json"
        receipt = json.loads(receipt_path.read_text())
        receipt.update(year=year, result_status="VERIFIED_RESULT_PENDING_ANALYTICAL_REVIEW",
                       artifact_hashes={authority_path.name: _sha(authority_path)},
                       protected_source_hashes=dict(zip(authority.network_path, authority.network_sha256)))
        receipt_path.write_text(json.dumps(receipt))
    return paths


@pytest.mark.parametrize("year,version", [(2040, "FINAL_METHODOLOGY_V3"),
                                          (2050, "FINAL_METHODOLOGY_V4")])
def test_comparison_explicitly_binds_inherited_or_current_model_version(tmp_path, year, version):
    from mem_model.visualization.uc2_comparison import _comparison_result_status
    paths = versioned_reports(tmp_path, version=version, year=year)
    metadata, _ = _validate_sources(year, paths, expected_model_version=version)
    assert metadata.model_version.eq(version).all()
    assert _comparison_result_status(metadata) == "VERIFIED_RESULT_PENDING_ANALYTICAL_REVIEW"


def test_comparison_rejects_pniec_benchmark_as_current_2050(tmp_path):
    paths = versioned_reports(tmp_path, version="FINAL_METHODOLOGY_V3")
    with pytest.raises(ValueError, match="Unexpected comparison model version"):
        _validate_sources(2050, paths, expected_model_version="FINAL_METHODOLOGY_V4")


def test_final_comparison_cannot_infer_analytical_acceptance_from_pass(tmp_path):
    paths = versioned_reports(tmp_path)
    receipt_path = paths['Different-B'] / 'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json'
    receipt = json.loads(receipt_path.read_text())
    del receipt['result_status']
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match='Unsupported comparison result status'):
        _validate_sources(2050, paths, expected_model_version='FINAL_METHODOLOGY_V4')


def test_comparison_rejects_mixed_versions_even_without_expected_version(tmp_path):
    from mem_model.visualization.vis_x1 import _sha
    paths = versioned_reports(tmp_path)
    report = paths["Different-B"]
    registry = report / "uc2_VIS_RESULT_AUTHORITY_REGISTER.csv"
    authority = pd.read_csv(registry)
    authority.parent_model_version = "FINAL_METHODOLOGY_V3"
    authority.to_csv(registry, index=False)
    receipt_path = report / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["artifact_hashes"][registry.name] = _sha(registry)
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="model versions across scenarios"):
        _validate_sources(2050, paths)


def test_comparison_binds_registered_authority_file_hash(tmp_path):
    paths = versioned_reports(tmp_path)
    registry = paths["Different-A"] / "uc2_VIS_RESULT_AUTHORITY_REGISTER.csv"
    registry.write_text(registry.read_text() + "\n")
    with pytest.raises(ValueError, match="Comparison source changed"):
        _validate_sources(2050, paths, expected_model_version="FINAL_METHODOLOGY_V4")


def test_comparison_binds_uc_and_lp_network_hashes_without_opening_networks(tmp_path):
    paths = versioned_reports(tmp_path)
    receipt_path = paths["Different-B"] / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text())
    receipt['protected_source_hashes'] = {key: 'c' * 64 for key in receipt['protected_source_hashes']}
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="Unbound comparison network authority"):
        _validate_sources(2050, paths, expected_model_version="FINAL_METHODOLOGY_V4")


@pytest.mark.parametrize("field,value", [
    ("result_id", "FINAL_METHODOLOGY_V4_POST_P2X_CONTINUOUS_REFERENCE"),
    ("current_role", "UC_UPLIFT_REFERENCE_ONLY"),
    ("year", 2040), ("scenario", "Wrong"), ("solved_or_unsolved", "UNSOLVED"),
    ("UC_status", "CONTINUOUS_REFERENCE"), ("visualization_allowed", False),
])
def test_comparison_refuses_incompatible_registered_physical_source(tmp_path, field, value):
    from mem_model.visualization.vis_x1 import _sha
    paths = versioned_reports(tmp_path)
    report = paths["Different-B"]
    registry = report / "uc2_VIS_RESULT_AUTHORITY_REGISTER.csv"
    authority = pd.read_csv(registry)
    authority.loc[0, field] = value
    authority.to_csv(registry, index=False)
    receipt_path = report / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["artifact_hashes"][registry.name] = _sha(registry)
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="Incompatible comparison source authority"):
        _validate_sources(2050, paths, expected_model_version="FINAL_METHODOLOGY_V4")


def test_comparison_refuses_cross_year_case_mixing(tmp_path):
    paths = reports(tmp_path, year=2040)
    with pytest.raises(ValueError, match="Incomparable or unverified"):
        _validate_sources(2050, paths)


def test_comparison_source_hash_normalizes_windows_receipt_paths(tmp_path):
    from mem_model.visualization.uc2_comparison import _verified_source_digest
    from mem_model.visualization.vis_x1 import _sha
    path = tmp_path / "CANONICAL/statistics/value.csv"
    path.parent.mkdir(parents=True)
    path.write_text("value\n1\n")
    receipt = {"artifact_hashes": {"CANONICAL\\statistics\\value.csv": _sha(path)}}
    assert _verified_source_digest(tmp_path, receipt, path) == _sha(path)
    with pytest.raises(ValueError, match="Unregistered comparison source"):
        _verified_source_digest(tmp_path, {}, path)
    path.write_text("value\n2\n")
    with pytest.raises(ValueError, match="Comparison source changed"):
        _verified_source_digest(tmp_path, receipt, path)


def test_comparison_missing_whole_metric_case_is_never_zero_filled():
    from mem_model.visualization.uc2_comparison import _validate_comparison_coverage
    tables = {"comparison_metrics": pd.DataFrame([
        dict(scenario_id="Base", metric="primary_generation", value=1),
        dict(scenario_id="High", metric="primary_generation", value=2),
        dict(scenario_id="Base", metric="net_imports", value=3),
    ])}
    with pytest.raises(ValueError, match="Missing whole comparison metric case: net_imports"):
        _validate_comparison_coverage(tables, ["Base", "High"])


def test_comparison_missing_whole_table_case_is_never_zero_filled():
    from mem_model.visualization.uc2_comparison import _validate_comparison_coverage
    tables = {"comparison_metrics": pd.DataFrame([
        dict(scenario_id=s, metric="primary_generation", value=1) for s in ("Base", "High")]),
        "monthly_zonal_prices": pd.DataFrame([dict(scenario="Base", month=1, value=1)])}
    with pytest.raises(ValueError, match="Missing whole comparison table case"):
        _validate_comparison_coverage(tables, ["Base", "High"])


@pytest.mark.parametrize("complete", [False, True])
def test_selected_view_records_preserve_18_ids_and_partial_collection_contract(tmp_path, complete):
    from mem_model.visualization.uc2_comparison import selected_comparison_view_records
    scenarios = ["Slow", "Base", "High"] if complete else ["Base", "High"]
    paths = {}
    for scenario in scenarios:
        report = tmp_path / "reports" / scenario
        report.mkdir(parents=True)
        (report / "hourly_online_units_and_committed_MW.csv").write_text("committed_MW\n1\n")
        paths[scenario] = report
    output = tmp_path / "comparisons"
    (output / "data").mkdir(parents=True)
    for table in ("monthly_zonal_prices", "price_duration", "net_interface_summary", "comparison_metrics",
                  "all_italy_spatial_spread", "distinct_simultaneous_prices", "monthly_national_load_weighted_price"):
        (output / "data" / f"{table}.csv").write_text("value\n1\n")
    (output / "marginal_regime").mkdir()
    (output / "marginal_regime/LEXICOGRAPHIC_REGIME_NATIONAL_SHARES_2050.csv").write_text("value\n1\n")
    records = selected_comparison_view_records(2050, paths, output,
                                              expected_scenarios=["Slow", "Base", "High"])
    assert len(records) == 18
    assert [r["view_id"] for r in records] == [
        "monthly_zonal_price", "price_duration", "interface_net_energy_MWh", "primary_generation",
        "net_imports", "annual_load_weighted_zonal_prices", "storage_activity", "p2x_electrical_consumption",
        "vre_curtailment", "actual_starts", "all_italy_mean_spatial_spread", "average_committed_capacity",
        "italian_zonal_spread_duration", "distinct_simultaneous_prices", "monthly_national_load_weighted_price",
        "interface_positive_limit_hours", "interface_negative_limit_hours", "marginal_regime_national_comparison"]
    assert all(r["status"] == "SOURCE_TABLES_AVAILABLE" for r in records[:17])
    assert records[-1]["status"] == ("SOURCE_TABLES_AVAILABLE" if complete else "WAITING_FOR_COMPLETE_SCENARIO_SET")
    assert all(r["rendering_status"] == "NOT_ASSESSED" for r in records)
    assert all(r["missing_scenarios"] == ([] if complete else ["Slow"]) for r in records)


@pytest.mark.parametrize('fault', [None, 'weight', 'timestamp', 'zone', 'hash'])
def test_final_spread_duration_uses_stored_case_bound_uc_weights(tmp_path, fault):
    from mem_model.visualization.uc2_comparison import _final_case_duration_source
    from mem_model.visualization.vis_x1 import _sha
    index = pd.date_range('2019-01-01', periods=4, freq='h')
    frame = pd.DataFrame([dict(market=zone, snapshot=snapshot, weight=1.0)
                          for zone in ('NORD', 'CNOR', 'CSUD', 'SUD', 'CALA', 'SICI', 'SARD')
                          for snapshot in index])
    if fault == 'weight':
        frame.loc[0, 'weight'] = 2.0
    elif fault == 'timestamp':
        frame.loc[0, 'snapshot'] = index[1]
    elif fault == 'zone':
        frame = frame.loc[frame.market.ne('SARD')]
    path = tmp_path / 'VIS_X1/data/fixed_commitment_lp_price_duration.parquet'
    path.parent.mkdir(parents=True)
    frame.to_parquet(path, index=False)
    receipt = {'artifact_hashes': {path.relative_to(tmp_path).as_posix(): _sha(path)}}
    if fault == 'hash':
        receipt['artifact_hashes'][path.relative_to(tmp_path).as_posix()] = '0' * 64
    if fault is None:
        assert _final_case_duration_source(tmp_path, receipt, index) == (path, _sha(path))
    else:
        with pytest.raises(ValueError):
            _final_case_duration_source(tmp_path, receipt, index)


def test_final_spread_view_waits_for_case_bound_duration_source(tmp_path):
    from mem_model.visualization.uc2_comparison import selected_comparison_view_records
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/all_italy_spatial_spread.csv').write_text('spread\n1\n')
    paths = {scenario: tmp_path / scenario for scenario in ('Base', 'High')}
    records = selected_comparison_view_records(2050, paths, tmp_path,
                                               model_version='FINAL_METHODOLOGY_V4')
    spread = next(record for record in records if record['view_id'] == 'italian_zonal_spread_duration')
    assert spread['status'] == 'WAITING_FOR_SOURCE_TABLES'
    assert len(spread['source_files']) == 1 and len(spread['missing_source_files']) == 2
    regime = next(record for record in records if record['view_id'] == 'marginal_regime_national_comparison')
    assert regime['status'] == 'WAITING_FOR_COMPLETE_SCENARIO_SET'


@pytest.mark.parametrize('year', [2040, 2050])
@pytest.mark.parametrize('count', [2, 3])
def test_current_comparison_discovery_forwards_case_roots_and_versions(tmp_path, monkeypatch, year, count):
    from mem_model import stage_b_uc2 as uc2
    from mem_model.reporting.uc2_postprocess import update_uc2_comparisons
    from mem_model.visualization import uc2_comparison
    inherited = tmp_path / 'final_methodology_v3'
    current = tmp_path / 'final_methodology_v4'
    selected_root = inherited if year == 2040 else current
    monkeypatch.setattr(uc2, 'case_root', lambda y, scenario: selected_root / str(y) / scenario)
    expected = [scenario for y, scenario in uc2.SCENARIOS if y == year]
    for scenario in expected[:count]:
        report = selected_root / str(year) / scenario / 'REPORTING'
        report.mkdir(parents=True)
        (report / 'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json').write_text('{}')
    # Complete older PNIEC reports must never supply missing current cases.
    for scenario in expected:
        benchmark = tmp_path / 'PNIEC' / str(year) / scenario / 'REPORTING'
        benchmark.mkdir(parents=True)
        (benchmark / 'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json').write_text('{}')
    calls = []
    def build(*args, **kwargs):
        calls.append((args, kwargs))
        return {'status': 'PASS'}
    monkeypatch.setattr(uc2_comparison, 'build_uc2_comparisons', build)
    presentation_calls = []
    def refine(*args, **kwargs):
        presentation_calls.append((args, kwargs))
        return {'status': 'PASS'}
    monkeypatch.setattr(uc2_comparison, 'refine_cached_comparison_presentation', refine)
    with uc2.execution_variant('final_v4'):
        result = update_uc2_comparisons(year)
    assert result == dict(status='PASS', comparison_dir=str(selected_root / str(year) / 'COMPARISONS_VIS_X1'),
                         presentation_dir=str(selected_root / str(year) / 'COMPARISONS_VIS_X1/presentation'),
                         presentation_status='PASS', selected_presentation_status='PASS')
    (actual_year, cases, output), options = calls[0]
    assert actual_year == year
    assert list(cases) == expected[:count]
    assert all(report == selected_root / str(year) / scenario / 'REPORTING'
               for scenario, report in cases.items())
    assert output == selected_root / str(year) / 'COMPARISONS_VIS_X1'
    assert options == dict(expected_model_version=f'FINAL_METHODOLOGY_V{3 if year == 2040 else 4}',
                           expected_scenarios=expected)
    assert presentation_calls == [((output, output / 'presentation'), {}),
                                   ((output, output / 'presentation'), dict(finalize_selection=True, case_reports=cases))]


def test_base_manifest_and_unchanged_checks_exclude_other_producer_outputs(tmp_path):
    from mem_model.visualization.uc2_comparison import _base_comparison_artifacts
    owned = ['data/metrics.csv', 'figures/monthly.svg', 'MEM_UC2_COMPARISON_FIGURE_METADATA.json']
    receipts = ['MEM_UC2_COMPARISON_MANIFEST.csv', 'MEM_UC2_COMPARISON_RECEIPT.json']
    others = ['presentation/primary_generation.svg', 'presentation/PRESENTATION_RECEIPT.json',
              'marginal_regime/figures/national.svg', 'marginal_regime/MEM_MARGINAL_REGIME_COMPARISON_RECEIPT.json']
    for name in owned + receipts + others:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
    assert {path.relative_to(tmp_path).as_posix() for path in _base_comparison_artifacts(tmp_path)} == set(owned)
    assert {path.relative_to(tmp_path).as_posix() for path in
            _base_comparison_artifacts(tmp_path, include_receipts=True)} == set(owned + receipts)


def test_refined_receipt_owns_only_its_declared_renders_and_metadata(tmp_path):
    from mem_model.visualization.uc2_comparison import _refined_presentation_artifact_hashes
    entries = []
    own_names = ['FIGURE_METADATA.json']
    for view in ('primary_generation', 'net_imports', 'annual_load_weighted_zonal_prices'):
        files = [tmp_path / (view + '.' + extension) for extension in ('png', 'svg')]
        for path in files:
            path.write_text('owned render fixture')
            own_names.append(path.name)
        entries.append(dict(files=[str(path) for path in files]))
    metadata = tmp_path / 'FIGURE_METADATA.json'
    metadata.write_text(json.dumps(entries))
    other_names = ['PRESENTATION_RECEIPT.json', 'average_committed_capacity.png',
                   'SELECTION_FIGURE_METADATA.json', 'COMPARISON_SELECTION_RECEIPT.json']
    for name in other_names:
        (tmp_path / name).write_text('previous selected producer output')
    initial = _refined_presentation_artifact_hashes(entries, metadata)
    assert set(initial) == set(own_names)
    # A second selected-view producer refresh cannot stale this receipt.
    for name in other_names:
        (tmp_path / name).write_text('refreshed selected producer output')
    assert _refined_presentation_artifact_hashes(entries, metadata) == initial

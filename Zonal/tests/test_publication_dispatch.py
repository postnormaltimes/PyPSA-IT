"""Objective checks for the new publication window and residual semantics."""
import numpy as np
import pandas as pd
import pytest

from mem_model.visualization import publication_dispatch as publication


def hourly(values):
    return pd.Series(values, index=pd.date_range(
        "2019-01-01", "2020-01-01", freq="h", inclusive="left"))


def test_peak_week_clips_negative_hours_before_cumulative_selection():
    residual = hourly(np.zeros(8760))
    residual.iloc[:168] = -1000.
    residual.iloc[:84] = 10.
    residual.iloc[400:568] = 4.
    window, qa = publication._select_peak_week(residual)
    assert window.equals(residual.index[:168])
    assert qa["positive_residual_MWh"] == 840.
    assert qa["candidate_windows"] == 8760 - 168 + 1
    assert qa["hours"] == 168


def test_peak_week_earliest_exact_tie_and_last_hour_no_wrapping():
    residual = hourly(np.zeros(8760))
    window, qa = publication._select_peak_week(residual)
    assert window.equals(residual.index[:168])
    assert qa["exact_tied_maximum_windows"] == 8593
    residual.iloc[-168:] = 2.
    window, qa = publication._select_peak_week(residual)
    assert window.equals(residual.index[-168:])
    assert qa["end_exclusive"] == "2020-01-01T00:00:00"
    assert qa["positive_residual_MWh"] == 336.


def test_peak_week_rejects_gap_and_nonfinite_values():
    residual = hourly(np.zeros(8760))
    with pytest.raises(ValueError, match="8760_HOURLY_CHRONOLOGY"):
        publication._select_peak_week(residual.drop(residual.index[10]))
    residual.iloc[10] = np.nan
    with pytest.raises(ValueError, match="8760_HOURLY_CHRONOLOGY"):
        publication._select_peak_week(residual)


def test_canonical_residual_reconciliation_excludes_p2x(tmp_path, monkeypatch):
    index = hourly(np.zeros(8760)).index
    grouped = pd.DataFrame({"Offshore wind": 1., "Onshore wind": 2., "Solar PV": 3.}, index=index)
    raw = pd.DataFrame({"Rigid load": 10., "P2X withdrawal": -20.}, index=index)
    path = tmp_path / publication.RESIDUAL_SOURCE
    path.parent.mkdir(parents=True)
    canonical = pd.DataFrame({"demand_mw": 30., "vre_mw": 6., "residual_load_mw": 24.}, index=index)
    canonical.to_csv(path)
    monkeypatch.setattr(publication.producer, "_source_digest", lambda *args: "test-pin")
    loaded = dict(case={"report": tmp_path}, receipt={}, raw=raw, grouped=grouped, sources={})
    residual, qa = publication._rigid_residual(loaded)
    assert residual.eq(4.).all()
    assert max(qa["reconciliation_max_abs_errors"].values()) == 0.
    canonical["vre_mw"] = 7.
    canonical.to_csv(path)
    with pytest.raises(ValueError, match="SEMANTIC_CONFLICT"):
        publication._rigid_residual(loaded)


def test_accepts_supported_unique_selection_and_orders_six_cases(tmp_path):
    cases = [dict(year=year, scenario=scenario, variant="final", report=tmp_path)
             for year in publication.YEARS for scenario in publication.SCENARIOS]
    ordered = publication._ordered_cases(cases)
    assert [(case["year"], case["scenario"]) for case in ordered] == [
        (year, scenario) for scenario in publication.SCENARIOS for year in publication.YEARS]
    with pytest.raises(ValueError, match="DUPLICATE_CASE"):
        publication._ordered_cases(cases + cases[:1])
    assert len(publication._ordered_cases(cases[:1])) == 1
    with pytest.raises(ValueError, match="SUPPORTED_NONEMPTY_CASE_SELECTION"):
        publication._ordered_cases([])


def test_support_and_nonphysical_categories_remain_separate():
    assert publication._category("Gas") == "primary_generation"
    assert publication._category("BESS discharge") == "storage_discharge"
    assert publication._category("Imports") == "external_imports"
    assert publication._category("Load shedding (nonphysical)") == "nonphysical_shedding"
    assert publication._category("P2X withdrawal") == "signed_withdrawal_or_export"

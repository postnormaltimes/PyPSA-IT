"""Cached physical/profile QA fails closed without building any model."""
import numpy as np
import pandas as pd
import pytest
import xarray as xr

from mem_model import final_wind_cells
from mem_model import final_wind_cell_verification as verification
from mem_model.common import ROOT, dump_json, load_yaml
from mem_model.final_wind_cell_verification import expected_case_keys, validate_allocation, verify_effective_profile
from mem_model.stage_b_zonal_vre import no_models_or_solves


@pytest.fixture(autouse=True)
def no_optimizer():
    with no_models_or_solves():
        yield


@pytest.fixture
def allocation():
    cells = pd.DataFrame({"site_id":["A","B"],"zone":["NORD"]*2,
        "technology":["WIND_ONSHORE"]*2,"p_nom_max_MW":[10.,20.],
        "annual_CF":[.2,.4],"hourly_column":[0,1]})
    selected = cells.copy()
    selected["selected_MW"] = [10.,5.]
    return cells, selected


def test_capacity_and_partial_cell_reconcile(allocation):
    cells, selected = allocation
    assert validate_allocation(cells,selected,15.) == 0.


def test_case_grain_is_six_scenarios_times_seven_zones_times_two_families():
    keys = expected_case_keys()
    assert len(keys) == 84
    assert {row[1] for row in keys} == {"Slow","Base","High"}
    assert (2040,"Base","NORD","WIND_ONSHORE") in keys
    assert (2050,"High","CALA","WIND_OFFSHORE") in keys


def test_unknown_cell_refused(allocation):
    cells, selected = allocation
    selected.loc[0,"site_id"] = "foreign"
    with pytest.raises(RuntimeError,match="UNKNOWN_SITE"):
        validate_allocation(cells,selected,15.)


def test_cross_zone_allocation_refused(allocation):
    cells, selected = allocation
    selected.loc[0,"zone"] = "CNOR"
    with pytest.raises(RuntimeError,match="ZONE_OR_TECHNOLOGY"):
        validate_allocation(cells,selected,15.)


def test_duplicated_cell_refused(allocation):
    cells, selected = allocation
    with pytest.raises(RuntimeError,match="DUPLICATE_SITE"):
        validate_allocation(cells,pd.concat([selected,selected.iloc[:1]]),25.)


def test_physical_cap_exceeded_refused(allocation):
    cells, selected = allocation
    selected.loc[0,"selected_MW"] = 11.
    with pytest.raises(RuntimeError,match="PHYSICAL_BOUND"):
        validate_allocation(cells,selected,16.)


def test_frozen_capacity_change_refused(allocation):
    cells, selected = allocation
    with pytest.raises(RuntimeError,match="FROZEN_MW"):
        validate_allocation(cells,selected,14.)


@pytest.fixture
def profiles(allocation):
    _, selected = allocation
    time = pd.date_range("2019-01-01",periods=8760,freq="h")
    hourly = xr.Dataset({"p_max_pu":(("time","hourly_column"),np.tile([.2,.4],(8760,1)))},
        coords={"time":time,"hourly_column":[0,1]})
    effective = pd.DataFrame({"snapshot":time.tz_localize("UTC"),"p_max_pu":(.2*10+.4*5)/15})
    return effective, selected, hourly


def test_full_8760_selected_profile_reconstruction(profiles):
    assert verify_effective_profile(*profiles,15.) < 1e-15


def test_missing_hour_refused(profiles):
    effective, selected, hourly = profiles
    with pytest.raises(RuntimeError,match="CHRONOLOGY"):
        verify_effective_profile(effective.iloc[1:],selected,hourly,15.)


def test_incorrect_aggregation_refused(profiles):
    effective, selected, hourly = profiles
    effective.loc[0,"p_max_pu"] += .1
    with pytest.raises(AssertionError):
        verify_effective_profile(effective,selected,hourly,15.)


def test_invalid_profile_bound_refused(profiles):
    effective, selected, hourly = profiles
    effective.loc[0,"p_max_pu"] = 1.1
    with pytest.raises(RuntimeError,match="PROFILE_BOUND"):
        verify_effective_profile(effective,selected,hourly,15.)


def test_blocked_siting_cannot_export_successors(tmp_path,monkeypatch):
    monkeypatch.setattr(final_wind_cells,"QA",tmp_path)
    dump_json(tmp_path / "NATIVE_CELL_SITING_RECEIPT.json",{"state":"WIND_SITE_COST_ROBUSTNESS_BLOCKED"})
    with pytest.raises(RuntimeError,match="REQUIRES_SITING_QA_PASS"):
        final_wind_cells.promote_wind_successors()


def test_cost_materiality_does_not_relax_old_class_limits():
    wind = load_yaml(ROOT / "config/final_methodology_closure.yaml")["wind"]
    assert wind["site_cost_robustness_tolerances_declared_before_stress"] == wind["convergence_tolerances_declared_before_results"]
    assert wind["class_resolution_status"] == "RESOURCE_CLASS_DISCRETIZATION_NOT_USED_AS_FINAL_SITING_AUTHORITY"


@pytest.mark.parametrize("state,independent_count",[("PASS",84),("WIND_RESOURCE_SITING_BLOCKED",0)])
def test_checkpoint_rejects_wrong_phase_result_or_incomplete_independent_QA(tmp_path,monkeypatch,state,independent_count):
    monkeypatch.setattr(verification,"QA",tmp_path)
    monkeypatch.setattr(verification,"STATE",tmp_path / "STATE.json")
    dump_json(tmp_path / "FINAL_WIND_VERIFICATION.json",{"state":state,"case_checks_PASS":84,"production_solver_invocations":0})
    dump_json(tmp_path / "NATIVE_CELL_TEST_RESULTS.json",{"status":"PASS","tests_failed":0,"solver_invocations":0})
    dump_json(tmp_path / "INDEPENDENT_NATIVE_CELL_REVIEW.json",{"independent_verification":{"frozen_cases_checked":independent_count}})
    dump_json(tmp_path / "STATE.json",{})
    with pytest.raises(RuntimeError,match="CHECKPOINT_PRECONDITION_FAIL"):
        verification.checkpoint_native_wind_blocked()

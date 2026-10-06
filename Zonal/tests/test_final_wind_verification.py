"""Cached array verification and W5 diagnostics, no model construction."""
import numpy as np
import pandas as pd
import pytest
import xarray as xr

from mem_model.common import ZONES
from mem_model.final_wind_diagnostics import profile_convergence
from mem_model.final_wind_verification import check_resource_dataset
from mem_model import final_wind_verification as verification
from mem_model.common import ROOT, load_yaml, dump_json
from mem_model.stage_b_zonal_vre import no_models_or_solves


TOLERANCES = {"annual_CF_absolute": .002, "hourly_MAE": .005, "hourly_max_absolute": .05}


@pytest.fixture(autouse=True)
def no_optimizer():
    with no_models_or_solves():
        yield


@pytest.fixture
def dataset():
    records = [{"class_id": f"{zone}__RC{count}__C{number:02d}", "zone": zone,
        "resource_classes": count, "annual_CF": 0., "p_nom_max_MW": 100. if number == 1 else 0.,
        "average_distance_km": 2.} for zone in ZONES for count in (1,4,8) for number in range(1,count+1)]
    classes = pd.DataFrame(records)
    masks = np.zeros((len(classes), 1, 1), dtype=bool)
    masks[classes.p_nom_max_MW.gt(0).to_numpy(), 0, 0] = True
    ds = xr.Dataset({"profile": (("time", "class_id"), np.zeros((8760,91))),
        "p_nom_max_MW": (("class_id",), classes.p_nom_max_MW.to_numpy()),
        "average_distance_km": (("class_id",), classes.average_distance_km.to_numpy()),
        "class_masks": (("class_id", "y", "x"), masks)},
        coords={"time": pd.date_range("2019-01-01", periods=8760, freq="h"), "class_id": classes.class_id})
    return ds, classes


def test_identical_convergence_profiles_pass():
    assert profile_convergence(np.zeros(8760), np.zeros(8760), TOLERANCES)["status"] == "PASS"


def test_declared_convergence_tolerance_is_not_relaxed():
    result = profile_convergence(np.full(8760, .15), np.full(8760, .17), TOLERANCES)
    assert result["status"] == "NOT_CONVERGED"
    assert result["annual_CF_absolute"] > TOLERANCES["annual_CF_absolute"]


def test_mismatched_convergence_chronology_rejected():
    with pytest.raises(ValueError, match="PROFILE_INVALID"):
        profile_convergence(np.zeros(168), np.zeros(8760), TOLERANCES)


def test_valid_cached_arrays_pass(dataset):
    ds, classes = dataset
    assert check_resource_dataset(ds, classes)["snapshots"] == 8760


def test_short_chronology_rejected(dataset):
    ds, classes = dataset
    with pytest.raises(RuntimeError, match="CHRONOLOGY_FAIL"):
        check_resource_dataset(ds.isel(time=slice(0,168)), classes)


def test_nan_profile_rejected(dataset):
    ds, classes = dataset
    ds.profile.values[0,0] = np.nan
    with pytest.raises(RuntimeError, match="PROFILE_INVALID"):
        check_resource_dataset(ds, classes)


def test_duplicate_resource_membership_rejected(dataset):
    ds, classes = dataset
    ds.class_masks.values[2,0,0] = True
    with pytest.raises(RuntimeError, match="MASK_DUPLICATE_FAIL"):
        check_resource_dataset(ds, classes)


def test_wrong_resource_crosswalk_rejected(dataset):
    ds, classes = dataset
    with pytest.raises(RuntimeError, match="CROSSWALK_FAIL"):
        check_resource_dataset(ds, classes.iloc[::-1])


def test_native_cell_resolution_is_explicit_not_a_silent_default():
    cfg = load_yaml(ROOT / "config/final_methodology_closure.yaml")["wind"]
    assert cfg["offshore_fixed_MW_economic_criterion"] == "LOWEST_SITE_LCOE"
    assert cfg["offshore_criterion_status"] == "ACCEPTED_USER_RESOLUTION"
    assert cfg["production_spatial_authority"] == "NATIVE_ELIGIBLE_CUTOUT_CELLS"
    assert cfg["no_output_price_assumption"] is True
    assert cfg["class_resolution_status"] == "RESOURCE_CLASS_DISCRETIZATION_NOT_USED_AS_FINAL_SITING_AUTHORITY"
    assert cfg["convergence_tolerances_declared_before_results"] == TOLERANCES


def test_blocked_checkpoint_fails_closed_on_changed_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(verification, "QA", tmp_path)
    monkeypatch.setattr(verification, "verify_cached_wind_evidence", lambda: {})
    dump_json(tmp_path / "FOCUSED_TEST_RESULTS.json", {"status": "PASS"})
    dump_json(tmp_path / "CONVERGENCE_DIAGNOSTIC_RECEIPT.json", {"onshore_not_converged": 38})
    pd.DataFrame(columns=["zone"]).to_csv(tmp_path / "OFFSHORE_ECONOMIC_TRADEOFFS.csv", index=False)
    pd.DataFrame(columns=["zone"]).to_csv(tmp_path / "SITING_CRITERION_COMPARISON.csv", index=False)
    with pytest.raises(RuntimeError, match="PRECONDITION_DRIFT"):
        verification.checkpoint_wind_blocked()
    assert not (tmp_path / "WIND_RESOURCE_SITING_GATE_RECEIPT.json").exists()

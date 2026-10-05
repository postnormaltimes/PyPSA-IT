from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from mem_model.reporting.water_values import (
    WaterValueError,
    capture_store_energy_balance_coverage,
    extract_water_values,
)


@pytest.fixture(autouse=True)
def prohibit_models_and_solves(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Water-value tests must never construct an optimization model or solve")
    from pypsa.optimization.optimize import OptimizationAccessor
    monkeypatch.setattr(OptimizationAccessor, "__call__", forbidden)
    monkeypatch.setattr(OptimizationAccessor, "create_model", forbidden)
    monkeypatch.setattr(OptimizationAccessor, "solve_model", forbidden)


@pytest.fixture
def fixture():
    snapshots = pd.date_range("2019-01-01", periods=3, freq="h", name="snapshot")
    stores = ["HYDRO_NORD", "PHS_SARD"]
    values = pd.DataFrame([[0., -20.], [30., 15.], [25., 0.]], index=snapshots, columns=stores)
    # This fixture is plain data, not a PyPSA optimization model.
    network = SimpleNamespace(
        snapshots=snapshots,
        buses=pd.DataFrame({"carrier": ["water_energy", "water_energy", "battery_energy"]},
                           index=["WATER_NORD", "WATER_SARD", "BATTERY"]),
        stores=pd.DataFrame({"bus": ["WATER_NORD", "WATER_SARD", "BATTERY"],
                             "carrier": ["water_energy", "water_energy", "battery_energy"],
                             "e_cyclic": [True, True, True]}, index=stores + ["BESS"]),
        stores_t=SimpleNamespace(mu_energy_balance=values),
        snapshot_weightings=pd.DataFrame({"objective": [2., 3., 4.]}, index=snapshots),
        model=None,
    )
    mapping = pd.DataFrame({"store_id": stores, "state_id": ["STATE_NORD", "STATE_SARD"],
                            "zone": ["NORD", "SARD"], "hydro_class": ["RESERVOIR", "PURE_PHS"]})
    provenance = {"kind": "FIXED_COMMITMENT_PRICE_LP", "solver_status": "ok",
                  "termination_condition": "optimal", "integer_variables": 0, "binary_variables": 0,
                  "solved_sha256": "1" * 64, "fixed_commitment_milp_sha256": "2" * 64,
                  "assign_all_duals": True, "fixed_commitment_trajectories_verified": True,
                  "objective_reconciliation_verified": True}
    coverage = pd.DataFrame({"snapshot": np.repeat(snapshots.to_numpy(), 2),
                             "store_id": np.tile(stores, 3), "constraint_name": "Store-energy_balance",
                             "constraint_label": np.arange(6),
                             "raw_dual": values.to_numpy().reshape(-1)})
    return network, mapping, provenance, coverage


def extract(fixture, **kwargs):
    network, mapping, provenance, coverage = fixture
    return extract_water_values(network, mapping, provenance, verified_milp_sha256="2" * 64,
                                raw_coverage=coverage, **kwargs)


def test_raw_negative_and_zero_duals_preserve_sign_scale_without_weight_division(fixture):
    table, receipt = extract(fixture)
    assert table.water_value_EUR_per_MWh_water.tolist() == [0., -20., 30., 15., 25., 0.]
    assert receipt["units"] == "EUR/MWh_water"
    assert receipt["objective_snapshot_weight_division"] is False
    assert receipt["stores_count"] == 2
    assert receipt["finite_count"] == 6
    assert receipt["production_solver_invocations"] == 0
    assert "Final output state precedes first snapshot" in receipt["cyclic_predecessor"]


@pytest.mark.parametrize("key,value", [("kind", "UC_MILP"), ("solver_status", "warning"),
    ("termination_condition", "infeasible"), ("integer_variables", 1), ("binary_variables", 1),
    ("assign_all_duals", False), ("fixed_commitment_trajectories_verified", False),
    ("objective_reconciliation_verified", False), ("fixed_commitment_milp_sha256", "3" * 64),
    ("solved_sha256", None)])
def test_rejects_invalid_price_lp_provenance(fixture, key, value):
    fixture[2][key] = value
    with pytest.raises(WaterValueError):
        extract(fixture)


def test_assigned_zero_without_raw_constraint_evidence_is_not_coverage(fixture):
    network, mapping, provenance, _ = fixture
    network.stores_t.mu_energy_balance.loc[:, :] = 0.
    with pytest.raises(WaterValueError, match="no live raw constraint evidence"):
        extract_water_values(network, mapping, provenance, verified_milp_sha256="2" * 64)


@pytest.mark.parametrize("change", ["missing", "duplicate", "extra", "inactive", "nan", "inf", "label_duplicate", "wrong_constraint"])
def test_rejects_incomplete_or_inactive_raw_coverage(fixture, change):
    network, mapping, provenance, coverage = fixture
    if change == "missing":
        coverage = coverage.iloc[:-1]
    elif change == "duplicate":
        coverage = pd.concat([coverage, coverage.iloc[:1]], ignore_index=True)
    elif change == "extra":
        coverage = coverage.copy()
        coverage.loc[0, "store_id"] = "BESS"
    elif change == "inactive":
        coverage.loc[0, "constraint_label"] = -1
    elif change in {"nan", "inf"}:
        coverage.loc[0, "raw_dual"] = np.nan if change == "nan" else np.inf
    elif change == "wrong_constraint":
        coverage.loc[0, "constraint_name"] = "Bus-nodal_balance"
    else:
        coverage.loc[1, "constraint_label"] = coverage.loc[0, "constraint_label"]
    with pytest.raises(WaterValueError):
        extract_water_values(network, mapping, provenance, verified_milp_sha256="2" * 64, raw_coverage=coverage)


@pytest.mark.parametrize("change", ["missing_state", "missing_hour", "wrong_order", "nan", "wrong_scale"])
def test_rejects_incomplete_or_scaled_assigned_values(fixture, change):
    network = fixture[0]
    values = network.stores_t.mu_energy_balance
    if change == "missing_state":
        network.stores_t.mu_energy_balance = values.iloc[:, :1]
    elif change == "missing_hour":
        network.stores_t.mu_energy_balance = values.iloc[:-1]
    elif change == "wrong_order":
        network.stores_t.mu_energy_balance = values.iloc[::-1]
    elif change == "nan":
        values.iloc[0, 0] = np.nan
    else:
        network.stores_t.mu_energy_balance = values.div(network.snapshot_weightings.objective, axis=0)
    with pytest.raises(WaterValueError):
        extract(fixture)


@pytest.mark.parametrize("change", ["missing_state", "battery", "bad_zone", "duplicate_state", "empty_class"])
def test_state_mapping_must_cover_only_every_water_store(fixture, change):
    network, mapping, provenance, coverage = fixture
    mapping = mapping.copy()
    if change == "missing_state":
        mapping = mapping.iloc[:1]
    elif change == "battery":
        mapping.loc[0, "store_id"] = "BESS"
    elif change == "bad_zone":
        mapping.loc[0, "zone"] = "IT"
    elif change == "empty_class":
        mapping.loc[0, "hydro_class"] = ""
    else:
        mapping.loc[1, "state_id"] = mapping.loc[0, "state_id"]
    with pytest.raises(WaterValueError):
        extract_water_values(network, mapping, provenance, verified_milp_sha256="2" * 64, raw_coverage=coverage)


def test_live_capture_uses_raw_labels_and_duals_without_constructing_model(fixture):
    network, mapping, provenance, coverage = fixture
    values = network.stores_t.mu_energy_balance
    coords = {"snapshot": network.snapshots, "name": values.columns}
    constraint = SimpleNamespace(
        dual=xr.DataArray(values.to_numpy(), dims=("snapshot", "name"), coords=coords),
        labels=xr.DataArray(np.arange(6).reshape(3, 2), dims=("snapshot", "name"), coords=coords),
    )
    network.model = SimpleNamespace(constraints={"Store-energy_balance": constraint})
    captured = capture_store_energy_balance_coverage(network, mapping)
    pd.testing.assert_frame_equal(captured, coverage)
    table, receipt = extract_water_values(network, mapping, provenance, verified_milp_sha256="2" * 64)
    assert len(table) == 6
    assert receipt["active_raw_constraint_coverage_verified"] is True


def test_exact_expected_chronology_required(fixture):
    with pytest.raises(WaterValueError, match="exact ordered network snapshots"):
        extract(fixture, expected_snapshots=fixture[0].snapshots[::-1])


def test_extraction_is_read_only(fixture):
    before = fixture[0].stores_t.mu_energy_balance.copy(deep=True)
    extract(fixture)
    pd.testing.assert_frame_equal(before, fixture[0].stores_t.mu_energy_balance)

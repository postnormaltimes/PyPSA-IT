"""Constraint capture from plain saved arrays; no optimization construction."""
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from mem_model.reporting.price_evidence import capture_price_evidence, verify_price_evidence


@pytest.fixture(autouse=True)
def forbid_optimization(monkeypatch):
    from pypsa.optimization.optimize import OptimizationAccessor
    from linopy import Model

    def forbidden(*args, **kwargs):
        pytest.fail("Constraint capture must not construct a model or call a solver")

    for method in ("__call__", "create_model", "solve_model"):
        monkeypatch.setattr(OptimizationAccessor, method, forbidden)
    monkeypatch.setattr(Model, "__init__", forbidden)


@pytest.fixture
def arrays():
    snapshots = pd.date_range("2019-01-01", periods=3, freq="h", name="snapshot")
    coords = {"snapshot": snapshots, "name": ["NORD"]}
    labels = xr.DataArray([[10], [-1], [12]], coords=coords, dims=("snapshot", "name"))
    data = xr.Dataset({"labels": labels, "dual": xr.full_like(labels, 0., dtype=float),
                       "sign": xr.full_like(labels, "=", dtype="U2"),
                       "rhs": xr.full_like(labels, 2., dtype=float),
                       "coeffs": (("snapshot", "name", "_term"), np.ones((3, 1, 1))),
                       "vars": (("snapshot", "name", "_term"), np.arange(3).reshape(3, 1, 1))})
    data.dual[1] = np.nan
    variables = {"Generator-p": SimpleNamespace(
        labels=xr.DataArray([0, 1, 2], dims="snapshot"),
        solution=xr.DataArray([2., 2., 2.], dims="snapshot"))}
    model = SimpleNamespace(status="ok", binaries=SimpleNamespace(nvars=0),
                            integers=SimpleNamespace(nvars=0), variables=variables,
                            constraints={"Bus-nodal_balance": SimpleNamespace(data=data)})
    n = SimpleNamespace(snapshots=snapshots, model=model,
                        generators=pd.DataFrame({"committable": pd.Series(dtype=bool)}),
                        links=pd.DataFrame(), stores=pd.DataFrame(),
                        global_constraints=pd.DataFrame())
    provenance = {"year": 2050, "scenario": "Slow", "kind": "FIXED_COMMITMENT_PRICE_LP",
                  "input_sha256": "1" * 64, "fixed_commitment_milp_sha256": "2" * 64}
    return n, provenance


def test_raw_active_zero_duals_matched_with_primal_and_masks(arrays, tmp_path):
    n, provenance = arrays
    evidence = capture_price_evidence(n, tmp_path, provenance, chunk_hours=1)
    table = pd.read_parquet(evidence["table_path"])
    assert table.constraint_label.tolist() == [10, 12]
    assert table.raw_dual.tolist() == [0., 0.]
    assert table.activity.tolist() == [2., 2.]
    assert table.slack.eq(0).all()
    manifest = json.loads((tmp_path / "RAW_PRICE_CONSTRAINT_EVIDENCE.json").read_text())
    assert manifest["constraints"][0]["coordinate_slots"] == 3
    assert manifest["constraints"][0]["inactive_slots"] == 1
    assert manifest["optimization_model_constructed_by_capture"] is False
    receipt = {**provenance, "solved_sha256": "3" * 64}
    assert verify_price_evidence(tmp_path, evidence, receipt)["source_solved_sha256"] == "3" * 64
    assert n.model.constraints["Bus-nodal_balance"].data.dual[1].isnull().all()


def test_scalar_p2x_annual_constraint_is_preserved(arrays, tmp_path):
    n, provenance = arrays
    n.global_constraints = pd.DataFrame(index=["P2X_ANNUAL_NORD"])
    data = xr.Dataset({"labels": xr.DataArray(15), "dual": xr.DataArray(-4.),
                       "sign": xr.DataArray("="), "rhs": xr.DataArray(6.),
                       "coeffs": ("_term", [1., 1., 1.]), "vars": ("_term", [0, 1, 2])})
    n.model.constraints["GlobalConstraint-P2X_ANNUAL_NORD"] = SimpleNamespace(data=data)
    evidence = capture_price_evidence(n, tmp_path, provenance)
    table = pd.read_parquet(evidence["table_path"])
    scalar = table.loc[table.asset_id.eq("P2X_ANNUAL_NORD")].iloc[0]
    assert pd.isna(scalar.snapshot)
    assert scalar.raw_dual == -4.
    assert scalar.rhs == scalar.activity == 6.


@pytest.mark.parametrize("defect,expected", [
    ("integer", "CONTINUOUS_LP"), ("unsolved", "SOLVED_MODEL"),
    ("wrong_source", "WRONG_SOURCE"), ("wrong_input", "MISSING_LINEAGE"),
    ("missing", "MISSING_CONSTRAINT"), ("nan_dual", "NONFINITE_CONSTRAINT"),
    ("nan_primal", "NONFINITE_PRIMAL"),
])
def test_rejects_missing_or_wrong_evidence(arrays, tmp_path, defect, expected):
    n, provenance = arrays
    if defect == "integer":
        n.model.integers.nvars = 1
    elif defect == "unsolved":
        n.model.status = "warning"
    elif defect == "wrong_source":
        provenance["kind"] = "UC_MILP"
    elif defect == "wrong_input":
        provenance["input_sha256"] = "unknown"
    elif defect == "missing":
        n.model.constraints.clear()
    elif defect == "nan_dual":
        n.model.constraints["Bus-nodal_balance"].data.dual[0] = np.nan
    else:
        n.model.variables["Generator-p"].solution[0] = np.nan
    with pytest.raises(RuntimeError, match=expected):
        capture_price_evidence(n, tmp_path, provenance)


def test_capture_refuses_overwrite_and_wrong_case_or_modified_file(arrays, tmp_path):
    n, provenance = arrays
    evidence = capture_price_evidence(n, tmp_path, provenance)
    with pytest.raises(RuntimeError, match="OVERWRITE_REFUSED"):
        capture_price_evidence(n, tmp_path, provenance)
    receipt = {**provenance, "solved_sha256": "3" * 64, "scenario": "High"}
    with pytest.raises(RuntimeError, match="LINEAGE_MISMATCH"):
        verify_price_evidence(tmp_path, evidence, receipt)
    (tmp_path / "RAW_PRICE_CONSTRAINT_EVIDENCE.json").write_text("{}")
    with pytest.raises(RuntimeError, match="HASH_MISMATCH"):
        verify_price_evidence(tmp_path, evidence, {**provenance, "solved_sha256": "3" * 64})


def test_existing_price_path_captures_before_export_and_verifies_capture():
    import inspect
    from mem_model import stage_b_uc2 as uc2

    source = inspect.getsource(uc2._manual_solve)
    assert source.index("capture_price_evidence(network") < source.index("network.export_to_netcdf")
    assert source.index("existing pricing evidence") < source.index("create_model(")
    assert '"price_constraint_evidence":price_evidence' in source
    assert "verify_price_evidence(" in inspect.getsource(uc2.verify_price)


def test_existing_capture_prevents_an_expensive_duplicate_solve(tmp_path, monkeypatch):
    from mem_model import stage_b_uc2 as uc2

    (tmp_path / "RAW_PRICE_CONSTRAINT_EVIDENCE.json").write_text("{}")
    monkeypatch.setattr(uc2, "job_paths", lambda *_: {
        "directory": tmp_path, "solved": tmp_path / "absent.nc", "receipt": tmp_path / "absent.json"})
    monkeypatch.setattr(uc2, "preflight", lambda *_: pytest.fail("Existing evidence must stop before preparation or solving"))
    with uc2.execution_variant("final_v4"), pytest.raises(RuntimeError, match="OVERWRITE_REFUSED"):
        uc2._manual_solve(2050, "Slow", "FIXED_COMMITMENT_PRICE_LP")

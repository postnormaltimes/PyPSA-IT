"""One-sided certified-gap reconciliation of saved fixed-commitment solutions."""
import numpy as np
import pandas as pd
import pypsa
import pytest

from mem_model import stage_b_uc2 as uc2
from mem_model.reporting.operational_checks import verify_operational_residuals
from mem_model.stage_b_zonal_vre import no_models_or_solves


LIMITS = {"objective_absolute_tolerance_EUR": 1e-4, "objective_relative_tolerance": 1e-9}


@pytest.fixture(autouse=True)
def no_optimizer():
    with no_models_or_solves():
        yield


def bounds(incumbent, price, gap=0.01, **changes):
    uc = {"objective_EUR": incumbent, "mip_gap": gap, "solver": "gurobi",
          "status": "PASS", "solver_status": "ok", "termination_condition": "optimal",
          "include_objective_constant": False, "binary_variables": 1, "integer_variables": 0}
    lp = {"objective_EUR": price, "include_objective_constant": False}
    uc.update(changes)
    return uc2._fixed_commitment_objective_bounds(uc, lp, LIMITS)


def test_actual_saved_2050_high_bounds():
    result = bounds(12545640262.629103, 12545571650.619438, 8.863852686357063e-5)
    assert result["classification"] == "GAP_BOUNDED_FIXED_COMMITMENT_REDISPATCH"
    assert result["redispatch_improvement_EUR"] == pytest.approx(68612.00966453552)
    assert result["certified_gap_allowance_EUR"] == pytest.approx(1112027.071439743)
    assert result["inferred_milp_lower_bound_EUR"] == pytest.approx(12544528235.557663)


@pytest.mark.parametrize("uc,lp,gap", [(1000, 990, .01), (-1000, -1010, .01), (0.1, .099, .01)])
def test_improvement_at_lower_bound_uses_absolute_native_incumbent(uc, lp, gap):
    result = bounds(uc, lp, gap)
    assert result["certified_gap_allowance_EUR"] == pytest.approx(gap * abs(uc))


@pytest.mark.parametrize("lp", [1000.001, 1001, 1010])
def test_more_expensive_lp_is_not_excused_by_mip_gap(lp):
    with pytest.raises(RuntimeError, match="RECONCILIATION_FAIL: objective"):
        bounds(1000, lp)


def test_improvement_below_certified_lower_bound_rejected():
    with pytest.raises(RuntimeError, match="RECONCILIATION_FAIL: objective"):
        bounds(1000, 989.999)


@pytest.mark.parametrize("gap", [None, np.nan, np.inf, 1e100, -1, 0])
def test_missing_invalid_or_zero_gap_cannot_admit_material_improvement(gap):
    with pytest.raises(RuntimeError, match="RECONCILIATION_FAIL"):
        bounds(1000, 999, gap)


@pytest.mark.parametrize("uc,lp", [(np.nan, 1), (1, np.nan), (np.inf, 1), (1, -np.inf), (0, -1)])
def test_nonfinite_or_zero_incumbent_not_given_gap_extension(uc, lp):
    with pytest.raises(RuntimeError, match="RECONCILIATION_FAIL"):
        bounds(uc, lp)


@pytest.mark.parametrize("gap", [None, np.nan, 0])
def test_original_numerical_parity_does_not_require_gap_extension(gap):
    assert bounds(1000, 1000.00005, gap)["classification"] == "NUMERICAL_OBJECTIVE_PARITY"


@pytest.mark.parametrize("changes", [{"solver": "unknown"}, {"status": "FAIL"},
    {"solver_status": "warning"}, {"termination_condition": "interrupted"},
    {"include_objective_constant": True}, {"binary_variables": 0}])
def test_uncertified_receipt_cannot_admit_gap_extension(changes):
    with pytest.raises(RuntimeError, match="RECONCILIATION_FAIL"):
        bounds(1000, 995, **changes)


def saved(dispatch):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2019-01-01", periods=2, freq="h"))
    n.add("Bus", "NORD")
    n.add("Generator", "G", bus="NORD", p_nom=20, committable=True,
          marginal_cost=10, start_up_cost=100, shut_down_cost=0, up_time_before=5)
    n.add("Generator", "H", bus="NORD", p_nom=20, marginal_cost=20)
    n.add("Load", "L", bus="NORD", p_set=20)
    n.generators_t.p = pd.DataFrame({"G": dispatch, "H": 20 - np.array(dispatch)}, index=n.snapshots)
    n.loads_t.p = pd.DataFrame({"L": 20.}, index=n.snapshots)
    for attr in ("status", "start_up", "shut_down"):
        setattr(n.generators_t, attr, pd.DataFrame({"G": 1. if attr == "status" else 0.}, index=n.snapshots))
    n._objective = float((n.generators_t.p * n.generators.marginal_cost).sum().sum())
    return n


def test_saved_known_answer_redispatch_keeps_both_objectives_and_feasibility_strict():
    uc, lp = saved([10, 10]), saved([12, 12])
    assert verify_operational_residuals(uc)["status"] == "PASS"
    assert verify_operational_residuals(lp)["status"] == "PASS"
    assert bounds(uc.objective, lp.objective, .1)["classification"] == "GAP_BOUNDED_FIXED_COMMITMENT_REDISPATCH"
    assert uc2._validate_redispatch_model_parity(uc, lp)["model_inputs_equal"] is True
    lp._objective -= 1
    with pytest.raises(RuntimeError, match="OPERATIONAL_RESIDUAL_FAIL"):
        verify_operational_residuals(lp)


@pytest.mark.parametrize("defect", ["cost", "weights", "timestamp", "ramp", "capacity", "load", "extra_equal_cost_uc", "quadratic", "storage_cost"])
def test_changed_or_unsupported_formulation_cannot_use_gap(defect):
    uc, lp = saved([10, 10]), saved([12, 12])
    if defect == "cost":
        lp.generators.loc["G", "marginal_cost"] += 1
    elif defect == "weights":
        lp.snapshot_weightings.loc[lp.snapshots[0], "objective"] = 2
    elif defect == "timestamp":
        lp.set_snapshots(lp.snapshots + pd.Timedelta(hours=1))
    elif defect == "ramp":
        lp.generators.loc["G", "ramp_limit_up"] = .1
    elif defect == "capacity":
        lp.generators.loc["G", "p_nom"] += 1
    elif defect == "load":
        lp.loads_t.p_set["L"] = [20, 21]
    elif defect == "extra_equal_cost_uc":
        for n in (uc, lp):
            n.generators.loc["G", "shut_down_cost"] = 100
    elif defect == "quadratic":
        for n in (uc, lp):
            n.generators.loc["G", "marginal_cost_quadratic"] = 1
    elif defect == "storage_cost":
        for n in (uc, lp):
            n.add("Store", "S", bus="NORD", e_nom=10, marginal_cost_storage=1)
    with pytest.raises(RuntimeError, match="UC2_FIXED_COMMITMENT_RECONCILIATION_FAIL"):
        uc2._validate_redispatch_model_parity(uc, lp)


def test_feasibility_failure_is_not_excused_by_gap():
    lp = saved([12, 12])
    lp.generators_t.p.loc[lp.snapshots[0], "G"] = 100
    lp._objective = float((lp.generators_t.p * lp.generators.marginal_cost).sum().sum())
    with pytest.raises(RuntimeError, match="OPERATIONAL_RESIDUAL_FAIL"):
        verify_operational_residuals(lp)


def test_native_dual_outputs_are_not_model_inputs():
    uc, lp = saved([10, 10]), saved([12, 12])
    lp.generators_t["mu_up"] = pd.DataFrame({"G": [1., 2.]}, index=lp.snapshots)
    assert uc2._validate_redispatch_model_parity(uc, lp)["model_inputs_equal"]
    lp.generators_t["custom_input"] = pd.DataFrame({"G": [1., 2.]}, index=lp.snapshots)
    with pytest.raises(RuntimeError, match="model inputs differ"):
        uc2._validate_redispatch_model_parity(uc, lp)


@pytest.mark.parametrize("defect", [None, "status", "start_up", "shut_down", "source", "integer", "receipt_objective", "physical_balance"])
def test_verify_price_retains_source_commitment_and_feasibility_guards(monkeypatch, defect):
    uc, lp = saved([10, 10]), saved([12, 12])
    uc_receipt = {"objective_EUR": uc.objective, "mip_gap": .1, "solver": "gurobi",
        "status": "PASS", "solver_status": "ok", "termination_condition": "optimal",
        "include_objective_constant": False, "binary_variables": 1, "solved_sha256": "1" * 64}
    price_receipt = {"objective_EUR": lp.objective, "fixed_commitment_milp_sha256": "1" * 64,
        "include_objective_constant": False, "integer_variables": 0, "binary_variables": 0,
        "solved_sha256": "2" * 64, "job_id": "price"}
    zones = ["NORD", "CNOR", "CSUD", "SUD", "CALA", "SICI", "SARD"]
    # This is an orchestration unit test: the price-grid contract is supplied
    # separately from the two-hour known-answer physical solution fixture.
    lp.buses_t.marginal_price = pd.DataFrame(10., index=pd.date_range("2019-01-01", periods=8760, freq="h"), columns=zones)
    if defect in ("status", "start_up", "shut_down"):
        frame = getattr(lp.generators_t, defect)
        frame.iloc[0, 0] = 0. if defect == "status" else 1.
    elif defect == "source":
        price_receipt["fixed_commitment_milp_sha256"] = "3" * 64
    elif defect == "integer":
        price_receipt["integer_variables"] = 1
    elif defect == "receipt_objective":
        price_receipt["objective_EUR"] -= 1
    elif defect == "physical_balance":
        lp.generators_t.p.iloc[0, 0] += 1
        lp._objective += 10
        price_receipt["objective_EUR"] = lp.objective
    monkeypatch.setattr(uc2, "config", lambda: {"zones": zones, "validation": {**LIMITS, "commitment_tolerance": 1e-7}})
    monkeypatch.setattr(uc2, "_verified_previous", lambda *args: {"status": "PASS"})
    monkeypatch.setattr(uc2, "_load_solved", lambda y, s, k: (uc, uc_receipt) if k == "UC_MILP" else (lp, price_receipt))
    checks, writes = [], []
    def physical(network):
        checks.append(network)
        return {"operational_residuals": verify_operational_residuals(network)}
    monkeypatch.setattr(uc2, "_operational_verification", physical)
    monkeypatch.setattr(uc2, "job_paths", lambda *args: {"verification": "isolated.json"})
    monkeypatch.setattr(uc2, "_write_json", lambda path, value: writes.append(value))
    with uc2.execution_variant(None):
        if defect is not None:
            with pytest.raises(RuntimeError):
                uc2.verify_price(2050, "High")
            assert not writes
        else:
            result = uc2.verify_price(2050, "High")
            assert result["objective_reconciliation"]["classification"] == "GAP_BOUNDED_FIXED_COMMITMENT_REDISPATCH"
            assert checks == [uc, lp]
            assert writes == [result]

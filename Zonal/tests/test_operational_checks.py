"""Known-answer saved-result checks, without constructing an optimization model."""
import numpy as np
import pandas as pd
import pypsa
import pytest

from mem_model.reporting.operational_checks import input_scope, verify_operational_residuals


@pytest.fixture(autouse=True)
def no_optimization(monkeypatch):
    from pypsa.optimization.optimize import OptimizationAccessor

    def forbidden(*args, **kwargs):
        pytest.fail("Numerical residual tests must not construct a model or solve")

    for method in ("__call__", "create_model", "solve_model"):
        monkeypatch.setattr(OptimizationAccessor, method, forbidden)


@pytest.fixture
def saved():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2019-01-01", periods=4, freq="h", name="snapshot"))
    n.snapshot_weightings.objective = [2., 3., 4., 5.]
    n.snapshot_weightings.stores = 2.
    n.add("Bus", "electric", carrier="AC")
    n.add("Bus", "water", carrier="water_energy")
    n.add("Generator", "thermal", bus="electric", p_nom=10., committable=True,
          p_min_pu=.2, p_max_pu=.8, marginal_cost=20.,
          min_up_time=2, min_down_time=2, up_time_before=2,
          ramp_limit_up=.2, ramp_limit_down=.2,
          ramp_limit_start_up=.8, ramp_limit_shut_down=.8)
    n.add("Generator", "inflow", bus="water", p_nom=.5, p_min_pu=1., p_max_pu=1.)
    n.add("Link", "turbine", bus0="water", bus1="electric", p_nom=1., efficiency=.8)
    n.add("Store", "reservoir", bus="water", e_nom=3., e_cyclic=True)
    n.add("Load", "demand", bus="electric", p_set=5.)
    n.generators_t.p = pd.DataFrame({"thermal": [5., 4.2, 5., 4.2], "inflow": .5}, index=n.snapshots)
    n.generators_t.status = pd.DataFrame({"thermal": 1.}, index=n.snapshots)
    n.generators_t.start_up = pd.DataFrame({"thermal": 0.}, index=n.snapshots)
    n.generators_t.shut_down = pd.DataFrame({"thermal": 0.}, index=n.snapshots)
    n.links_t.p0 = pd.DataFrame({"turbine": [0., 1., 0., 1.]}, index=n.snapshots)
    n.links_t.p1 = -.8 * n.links_t.p0
    n.stores_t.p = pd.DataFrame({"reservoir": [-.5, .5, -.5, .5]}, index=n.snapshots)
    n.stores_t.e = pd.DataFrame({"reservoir": [2., 1., 2., 1.]}, index=n.snapshots)
    n.loads_t.p = pd.DataFrame({"demand": 5.}, index=n.snapshots)
    n._objective = float((n.generators_t.p.thermal * 20. * n.snapshot_weightings.objective).sum())
    return n


def test_known_answer_water_and_electrical_units_with_cyclic_predecessor(saved):
    before = saved.stores_t.e.copy(deep=True)
    result = verify_operational_residuals(saved)
    assert result["status"] == "PASS"
    assert saved.stores_t.e.iloc[0, 0] != saved.stores_t.e.iloc[-1, 0]
    assert result["scope"]["buses"] == 2
    assert result["solver_invocations"] == 0
    pd.testing.assert_frame_equal(before, saved.stores_t.e)


@pytest.mark.parametrize("defect,expected", [
    ("sign", "native_all_bus_balance"),
    ("efficiency", "link_terminal_conversion"),
    ("recurrence", "store_energy_recurrence"),
    ("capacity", "generator_upper_bound"),
    ("ramp", "generators_ramp_up"),
    ("duration", "UC_min_up_time"),
    ("objective", "objective_reconstruction"),
])
def test_detects_independent_numerical_defects(saved, defect, expected):
    if defect == "sign":
        saved.generators.at["inflow", "sign"] = -1.
    elif defect == "efficiency":
        saved.links.at["turbine", "efficiency"] = .7
    elif defect == "recurrence":
        saved.stores_t.e.iloc[0, 0] += .1
    elif defect == "capacity":
        saved.generators.at["thermal", "p_nom"] = 5.
    elif defect == "ramp":
        saved.generators.at["thermal", "ramp_limit_up"] = .01
    elif defect == "duration":
        saved.generators_t.start_up.loc[:, "thermal"] = [1., 1., 0., 0.]
    else:
        saved._objective += 10.
    with pytest.raises(RuntimeError, match=expected):
        verify_operational_residuals(saved)


def test_missing_timestamp_is_not_filled(saved):
    saved.links_t.p0 = saved.links_t.p0.iloc[:-1]
    with pytest.raises(RuntimeError, match="DATA_ALIGNMENT"):
        verify_operational_residuals(saved)


def test_startup_costs_are_event_euros_not_weighted_energy(saved):
    saved.generators.loc["thermal", ["min_up_time", "min_down_time", "up_time_before", "down_time_before"]] = [1, 1, 0, 1]
    saved.generators.at["thermal", "start_up_cost"] = 7.
    saved.generators.at["thermal", "ramp_limit_down"] = 1.
    saved.generators_t.status.loc[:, "thermal"] = [0., 1., 1., 1.]
    saved.generators_t.start_up.loc[:, "thermal"] = [0., 1., 0., 0.]
    saved.generators_t.p.loc[saved.snapshots[0], "thermal"] = 0.
    saved.loads.at["demand", "p_set"] = 0.
    saved.loads_t.p_set = pd.DataFrame({"demand": [0., 5., 5., 5.]}, index=saved.snapshots)
    saved.loads_t.p = saved.loads_t.p_set.copy()
    saved._objective = float((saved.generators_t.p.thermal * 20. * saved.snapshot_weightings.objective).sum()) + 7.
    result = verify_operational_residuals(saved)
    assert result["objective_terms"]["start_up_EUR"] == 7.
    assert result["status"] == "PASS"


def test_noncyclic_initial_energy_is_native_rhs_without_first_step_loss(saved):
    saved.stores.loc["reservoir", ["e_cyclic", "e_initial", "standing_loss"]] = [False, 1., .1]
    retained = .9 ** 2
    e = []
    previous = 1.
    for index, p in enumerate(saved.stores_t.p.reservoir):
        previous = (1. if index == 0 else retained) * previous - 2. * p
        e.append(previous)
    saved.stores_t.e.loc[:, "reservoir"] = e
    assert verify_operational_residuals(saved)["status"] == "PASS"


def test_initial_dwell_and_no_arbitrary_last_snapshot_extension(saved):
    saved.generators.loc["thermal", ["min_up_time", "up_time_before"]] = [4, 1]
    assert verify_operational_residuals(saved)["status"] == "PASS"
    saved.generators_t.status.iloc[1, 0] = 0.
    with pytest.raises(RuntimeError, match="UC_initial_up_time_before"):
        verify_operational_residuals(saved)


def test_nonfinite_costs_cannot_be_silently_skipped(saved):
    saved.generators.at["thermal", "marginal_cost"] = np.nan
    with pytest.raises(RuntimeError, match="DATA_FINITE"):
        verify_operational_residuals(saved)


def test_scope_rejects_unchecked_endogenous_capacity(saved):
    saved.links.at["turbine", "p_nom_extendable"] = True
    with pytest.raises(RuntimeError, match="OPERATIONAL_SCOPE"):
        input_scope(saved)


def test_current_verification_path_uses_residuals_without_new_execution(monkeypatch, saved):
    import inspect
    from mem_model import stage_b_uc2 as uc2
    from mem_model.reporting import operational_checks

    calls = []
    original = operational_checks.verify_operational_residuals

    def record(network, **kwargs):
        calls.append(network)
        return original(network, **kwargs)

    monkeypatch.setattr(operational_checks, "verify_operational_residuals", record)
    with uc2.execution_variant("final_v4"):
        assert uc2._operational_verification(saved)["operational_residuals"]["status"] == "PASS"
    with uc2.execution_variant(None):
        assert uc2._operational_verification(saved) == {}
    assert calls == [saved]
    for function, variable in ((uc2.verify_reference, "network"), (uc2.verify_uc, "network"), (uc2.verify_price, "price")):
        assert "_operational_verification(" + variable + ")" in inspect.getsource(function)


def test_terminal_start_is_not_forced_to_complete_duration_beyond_horizon(saved):
    saved.generators.loc["thermal", ["min_up_time", "up_time_before", "min_down_time", "down_time_before"]] = [3, 0, 1, 3]
    saved.generators.loc["thermal", ["ramp_limit_up", "ramp_limit_down"]] = [1., 1.]
    saved.generators_t.status.loc[:, "thermal"] = [0., 0., 0., 1.]
    saved.generators_t.start_up.loc[:, "thermal"] = [0., 0., 0., 1.]
    saved.generators_t.p.loc[saved.snapshots[:3], "thermal"] = 0.
    saved.loads.at["demand", "p_set"] = 0.
    saved.loads_t.p_set = pd.DataFrame({"demand": [0., .8, 0., 5.]}, index=saved.snapshots)
    saved.loads_t.p = saved.loads_t.p_set.copy()
    saved._objective = float((saved.generators_t.p.thermal * 20. * saved.snapshot_weightings.objective).sum())
    assert verify_operational_residuals(saved)["status"] == "PASS"

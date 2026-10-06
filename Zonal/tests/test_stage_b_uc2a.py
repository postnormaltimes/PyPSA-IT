"""UC-v2A mathematics and structural transformations; solvers are forbidden."""
from __future__ import annotations

import json
import numpy as np
import pandas as pd
import pypsa
import pytest

from mem_model import stage_b_uc1 as uc1
from mem_model import stage_b_uc2a as v2a
from mem_model import uc_heterogeneity as hetero
from mem_model.common import ROOT, sha256_file


@pytest.fixture(autouse=True)
def prohibit_optimizer(monkeypatch):
    from pypsa.optimization.optimize import OptimizationAccessor
    from linopy import Model
    def forbidden(*args, **kwargs):
        pytest.fail("UC-v2A tests must not construct a model or invoke a solver")
    for attr in ("__call__", "create_model", "solve_model"):
        monkeypatch.setattr(OptimizationAccessor, attr, forbidden)
    monkeypatch.setattr(Model, "solve", forbidden)


def observations(count=18, country="Italy", offset=0):
    return pd.DataFrame({"plant_id": [f"plant_{offset+i:03d}" for i in range(count)],
                         "country": country, "technology": "CCGT",
                         "capacity_MW": np.tile([100., 300., 700.], count // 3),
                         "efficiency": np.tile([.6, .5, .4], count // 3),
                         "capacity_observed": True, "efficiency_observed": True})


def test_terciles_keep_value_ties_and_ignore_row_order():
    values = pd.Series([1., 1., 1., 2., 2., 2., 3., 3., 3.])
    labels, boundaries = hetero.terciles(values)
    assert set(labels) == {0, 1, 2}
    assert labels.iloc[:3].eq(0).all()
    reversed_labels, repeat = hetero.terciles(values.iloc[::-1])
    pd.testing.assert_series_equal(labels, reversed_labels.sort_index())
    assert boundaries == repeat


def test_largest_remainder_ties_use_fixed_cell_order():
    result = hetero.largest_remainder(4, np.ones(3))
    assert result.tolist() == [2, 1, 1]
    assert hetero.CELL_ORDER[0] == ("ETA_LOW", "SZ_SMALL")
    assert hetero.CELL_ORDER[-1] == ("ETA_HIGH", "SZ_LARGE")


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 17, 33])
def test_allocation_capacity_heat_rate_and_efficiency(count):
    model = hetero.reference_classes(observations(), "CCGT")
    children = hetero.normalize_children(2400., .59, count, model)
    assert len(children) == count
    assert children.p_nom.sum() == pytest.approx(2400., abs=1e-8)
    assert np.average(children.heat_rate, weights=children.p_nom) == pytest.approx(1/.59, abs=1e-12)
    np.testing.assert_allclose(children.efficiency, 1/children.heat_rate)
    assert children.efficiency.between(0, 1, inclusive="right").all()
    assert np.isin(children.efficiency, .59 * hetero.EFFICIENCY_MULTIPLIERS).all()
    if count < 3:
        assert children.efficiency.eq(.59).all()
        assert children.allocation_mode.eq("MID_ONLY_SMALL_COHORT").all()
    else:
        counts = hetero.efficiency_unit_counts(count)
        for label, share, expected_count in zip(hetero.EFFICIENCY_CLASSES, hetero.EFFICIENCY_CAPACITY_SHARES, counts):
            subset = children.loc[children.efficiency_class.eq(label)]
            assert len(subset) == expected_count
            assert subset.p_nom.sum() == pytest.approx(2400. * share, abs=1e-8)
    assert children.beta_HR.eq(1.).all()


def test_three_children_use_exact_MW_envelopes_not_equal_unit_MW():
    model = hetero.reference_classes(observations(), "CCGT")
    children = hetero.normalize_children(1000., .59, 3, model)
    assert hetero.efficiency_unit_counts(3).tolist() == [1, 1, 1]
    assert children.p_nom.tolist() == pytest.approx([150., 500., 350.])
    assert children.efficiency.tolist() == [.59 * .90, .59, .59 * 1.05]
    assert children.allocation_mode.eq("EXACT_15_50_35_MW_ENVELOPES").all()


@pytest.mark.parametrize("count", [3, 4, 5, 7, 17, 99])
def test_efficiency_allocation_keeps_all_three_classes_and_exact_unit_count(count):
    counts = hetero.efficiency_unit_counts(count)
    assert counts.sum() == count
    assert (counts >= 1).all()


def test_capacity_reference_never_requires_efficiency_observations():
    data = observations()
    expected = hetero.reference_classes(data, "CCGT")
    data.efficiency = np.nan
    data.efficiency_observed = False
    actual = hetero.reference_classes(data, "CCGT")
    assert v2a._jsonable(actual) == v2a._jsonable(expected)
    assert actual["capacity_rows"] == 18
    assert actual["empirical_efficiency_used"] is False
    assert not any(key in actual for key in ("cell_shares", "hr_multipliers", "heat_rate_boundaries"))


def test_size_and_controlled_efficiency_are_independent():
    size = observations()
    size["efficiency_observed"] = False
    hr = observations(offset=100)
    hr["capacity_observed"] = False
    model = hetero.reference_classes(pd.concat([size, hr]), "CCGT")
    assert model["mode"] == "INDEPENDENT_SIZE_CONTROLLED_EFFICIENCY"
    assert model["capacity_rows"] == 18
    np.testing.assert_allclose(model["size_shares"], np.full(3, 1/3))
    children = hetero.normalize_children(1000., .5, 20, model)
    for label in hetero.EFFICIENCY_CLASSES:
        group = children.loc[children.efficiency_class.eq(label)]
        assert group.p_nom.sum() / 1000. == pytest.approx(group.efficiency_group_capacity_share.iloc[0])


def test_italy_then_europe_fallback():
    data = pd.concat([observations(6), observations(18, "France", 100)])
    model = hetero.reference_classes(data, "CCGT")
    assert model["size_geography"] == "EUROPE"
    assert model["capacity_rows"] == 24


def test_insufficient_size_reference_fails_without_fabricating_variance():
    data = observations()
    data["efficiency_observed"] = False
    model = hetero.reference_classes(data, "CCGT")
    assert model["mode"] == "INDEPENDENT_SIZE_CONTROLLED_EFFICIENCY"
    data.capacity_observed = False
    assert hetero.reference_classes(data, "CCGT")["mode"] == "HOMOGENEOUS_FALLBACK"


def test_sparse_or_invalid_reference_and_efficiency_rejection():
    model = hetero.reference_classes(observations(6), "CCGT")
    assert model["mode"] == "HOMOGENEOUS_FALLBACK"
    data = observations()
    data.efficiency = 2.
    assert hetero.reference_classes(data, "CCGT")["mode"] == "INDEPENDENT_SIZE_CONTROLLED_EFFICIENCY"
    model = hetero.reference_classes(observations(), "CCGT")
    with pytest.raises(ValueError, match="V2A_INVALID_PARENT"):
        hetero.normalize_children(100., 0., 3, model)
    with pytest.raises(ValueError, match="V2A_INVALID_CHILD_EFFICIENCY"):
        hetero.normalize_children(100., .99, 9, model)


def test_reference_model_is_deterministic_under_input_permutation():
    data = observations()
    left = hetero.reference_classes(data, "CCGT")
    right = hetero.reference_classes(data.iloc[::-1], "CCGT")
    assert v2a._jsonable(left) == v2a._jsonable(right)


def test_mc_reconstruction_and_weighted_mean_preservation():
    source = uc1._static_source(2050, "Base")
    row = source.loc[source.carrier.eq("methane_ccs")].iloc[0]
    coefficient = v2a.cost_coefficient(row)
    assert coefficient == pytest.approx(30.6778)
    model = hetero.reference_classes(observations(), "CCGT")
    children = hetero.normalize_children(3000., float(row.efficiency), 8, model)
    mc = hetero.heat_rate_cost(float(row.marginal_cost_EUR2025_per_MWh_el), 1/row.efficiency,
                             children.heat_rate, coefficient)
    assert np.average(mc, weights=children.p_nom) == pytest.approx(row.marginal_cost_EUR2025_per_MWh_el, abs=1e-10)
    values = [float(mc.loc[children.efficiency_class.eq(label)].iloc[0]) for label in hetero.EFFICIENCY_CLASSES]
    assert values[0] > values[1] == row.marginal_cost_EUR2025_per_MWh_el > values[2]
    assert children.loc[children.efficiency_class.eq("ETA_MID"), "efficiency"].eq(row.efficiency).all()
    bad = row.copy()
    bad.marginal_cost_EUR2025_per_MWh_el += 1
    with pytest.raises(RuntimeError, match="COST_DECOMPOSITION_UNRESOLVED"):
        v2a.cost_coefficient(bad)


def test_known_time_cost_coefficient_is_preserved_snapshot_by_snapshot():
    snapshots = pd.date_range("2019-01-01", periods=4, freq="h")
    coefficient = pd.Series([20.,30.,40.,50.], index=snapshots)
    parent_hr = 2.
    baseline = 5 + coefficient * parent_hr
    power = np.array([1.,2.])
    rates = np.array([1.,2.5])
    costs = pd.concat([hetero.heat_rate_cost(baseline, parent_hr, hr, coefficient) for hr in rates], axis=1)
    np.testing.assert_allclose(np.average(costs, axis=1, weights=power), baseline)
    with pytest.raises(ValueError, match="TIME_COST_ALIGNMENT_FAIL"):
        hetero.heat_rate_cost(baseline, parent_hr, 1., coefficient.iloc[::-1])


@pytest.fixture
def v1_fixture(monkeypatch):
    network = pypsa.Network()
    network.set_snapshots(pd.date_range("2019-01-01", periods=8760, freq="h"))
    for zone in ("CALA", "CNOR", "CSUD", "NORD", "SARD", "SICI", "SUD"):
        network.add("Bus", zone)
        network.add("Carrier", f"p2x_{zone}")
        network.add("Load", f"load_{zone}", bus=zone, p_set=1.)
        network.add("Generator", f"P2X_{zone}", bus=zone, carrier=f"p2x_{zone}", sign=-1, p_nom=5.)
        network.add("GlobalConstraint", f"P2X_ANNUAL_{zone}", type="operational_limit",
                    carrier_attribute=f"p2x_{zone}", sense="==", constant=100.)
    network.add("Carrier", "methane_ccgt")
    for name in ("old_A", "old_B"):
        network.add("Generator", name, bus="NORD", carrier="methane_ccgt", p_nom=50., efficiency=.5,
                    marginal_cost=45., committable=True, p_min_pu=.45, ramp_limit_up=1., ramp_limit_down=1.,
                    ramp_limit_start_up=.5, ramp_limit_shut_down=.5, min_up_time=4, min_down_time=2, start_up_cost=3000.)
        network.generators_t.p_max_pu[name] = .8
    network.add("Carrier", "solar")
    network.add("Generator", "solar", bus="NORD", carrier="solar", p_nom=10.)
    frozen = pd.DataFrame({"parent_id": "cohort", "child_id": ["old_A", "old_B"],
                           "uc_archetype": "CCGT", "startup_cost_provenance": "PYPSA_EUR_BASELINE_ASSUMPTION"})
    source = pd.DataFrame([{"generator_id": "cohort", "solver_subtechnology": "METHANE_CCGT_NON_CHP",
                            "carrier": "methane_ccgt", "p_nom_MW": 100., "efficiency": .5,
                            "marginal_cost_EUR2025_per_MWh_el": 45., "VOM_EUR2025_per_MWh_el": 5.,
                            "fuel_price_EUR2025_per_MWh_th": 20., "chargeable_CO2_t_per_MWh_th": 0.,
                            "CO2_price_EUR2025_per_t": 400., "other_variable_cost_EUR2025_per_MWh_el": 0.,
                            "source_parameters": "fixture", "availability_class": "fixture_profile"}]).set_index("generator_id")
    monkeypatch.setattr(uc1, "_static_source", lambda *_: source)
    models = {"CCGT": hetero.reference_classes(observations(), "CCGT")}
    plan = v2a.plan_children(network, frozen, 2040, "Base", models)
    return network, plan


def test_hook_startup_cost_scaling_and_frozen_non_target_preservation(v1_fixture):
    network, plan = v1_fixture
    child = v2a.derive(network, plan)
    qa = v2a.verify_structure(network, child, plan)
    assert qa["status"] == "PASS" and qa["theoretical_uc_binaries_v1"] == qa["theoretical_uc_binaries_v2A"]
    units = child.generators.loc[plan.child_id]
    np.testing.assert_allclose(units.start_up_cost, units.p_nom * 60.)
    assert units.p_nom.nunique() == 2
    for row in plan.itertuples():
        pd.testing.assert_series_equal(child.generators_t.p_max_pu[row.child_id], network.generators_t.p_max_pu[row.v1_child_id], check_names=False)
    assert all(name.startswith("UC2A__2040__Base__cohort__SZ_") for name in plan.child_id)


def test_availability_gate_and_non_target_drift_fail_closed(v1_fixture):
    network, plan = v1_fixture
    child = v2a.derive(network, plan)
    child.generators_t.p_max_pu.loc[child.snapshots[0], plan.child_id.iloc[0]] = .2
    # Exact inheritance fails before the compatibility gate can be weakened.
    with pytest.raises(AssertionError):
        v2a.verify_structure(network, child, plan)
    network.generators_t.p_max_pu.loc[network.snapshots[0], plan.v1_child_id.iloc[0]] = .2
    with pytest.raises(RuntimeError, match="AVAILABILITY_COMPATIBILITY_FAIL"):
        v2a.verify_structure(network, v2a.derive(network, plan), plan)
    network.generators_t.p_max_pu.loc[network.snapshots[0], plan.v1_child_id.iloc[0]] = .8
    child = v2a.derive(network, plan)
    child.generators.at["solar", "p_nom"] = 11
    with pytest.raises(AssertionError):
        v2a.verify_structure(network, child, plan)


def test_deterministic_child_plan_and_native_export_reload(v1_fixture, tmp_path):
    network, plan = v1_fixture
    assert pypsa.__version__ == "1.2.3"
    path = tmp_path / "v2a_fixture_UNSOLVED.nc"
    child = v2a.derive(network, plan)
    child.export_to_netcdf(path)
    reloaded = pypsa.Network(path)
    assert v2a.verify_structure(network, reloaded, plan)["status"] == "PASS"
    repeat = v2a.derive(network, plan.copy())
    pd.testing.assert_frame_equal(child.generators, repeat.generators)
    assert v2a._digest(plan) == v2a._digest(plan.copy())


def test_hook_refuses_p2x_and_duplicate_ids(v1_fixture):
    network, plan = v1_fixture
    updates = plan.set_index("v1_child_id")[["child_id", *v2a.OVERRIDE_FIELDS]]
    bad = updates.copy()
    bad.loc["old_B", "child_id"] = bad.loc["old_A", "child_id"]
    with pytest.raises(RuntimeError, match="INVALID_ID_OR_SCOPE"):
        uc1.apply_child_static_overrides(network, bad)
    with pytest.raises(RuntimeError, match="INVALID_ID_OR_SCOPE"):
        uc1.apply_child_static_overrides(network, updates.rename(index={"old_A": "P2X_NORD"}))


def test_nonzero_event_cost_scaling_and_zero_availability(v1_fixture):
    network, previous_plan = v1_fixture
    for name in previous_plan.v1_child_id:
        network.generators.at[name, "shut_down_cost"] = 5 * network.generators.at[name, "p_nom"]
        network.generators.at[name, "stand_by_cost"] = network.generators.at[name, "p_nom"]
        network.generators_t.p_max_pu.loc[network.snapshots[0], name] = 0.
    frozen = previous_plan[["parent_id", "v1_child_id", "prime_mover", "startup_cost_provenance"]].rename(
        columns={"v1_child_id": "child_id", "prime_mover": "uc_archetype"})
    plan = v2a.plan_children(network, frozen, 2040, "Base", {"CCGT": hetero.reference_classes(observations(), "CCGT")})
    child = v2a.derive(network, plan)
    assert v2a.verify_structure(network, child, plan)["availability_compatibility"] == "PASS"
    units = child.generators.loc[plan.child_id]
    np.testing.assert_allclose(units.shut_down_cost, units.p_nom * 5)
    np.testing.assert_allclose(units.stand_by_cost, units.p_nom)
    child.generators.at[plan.child_id.iloc[0], "ramp_limit_up"] = .75
    with pytest.raises(AssertionError):
        v2a.verify_structure(network, child, plan)


def test_unexplained_time_varying_target_cost_fails_closed(v1_fixture):
    network, previous_plan = v1_fixture
    network.generators_t.marginal_cost[previous_plan.v1_child_id.iloc[0]] = 45.
    frozen = previous_plan[["parent_id", "v1_child_id", "prime_mover", "startup_cost_provenance"]].rename(
        columns={"v1_child_id": "child_id", "prime_mover": "uc_archetype"})
    with pytest.raises(RuntimeError, match="TIME_COST_OR_EFFICIENCY_DECOMPOSITION_UNRESOLVED"):
        v2a.plan_children(network, frozen, 2040, "Base", {"CCGT": hetero.reference_classes(observations(), "CCGT")})


def test_preparation_cli_has_no_solver_action():
    with pytest.raises(SystemExit):
        v2a.main(["run-uc"])


def test_local_reference_provenance_and_explicit_fallback():
    data, models = v2a.load_reference(v2a.settings())
    assert models["CCGT"]["mode"] == "INDEPENDENT_SIZE_CONTROLLED_EFFICIENCY"
    assert models["CCGT"]["capacity_rows"] == 95
    assert models["CCGT"]["size_geography"] == "ITALY"
    assert models["OCGT"]["size_geography"] == "EUROPE"
    assert models["OCGT"]["capacity_rows"] == 130
    assert models["BIOMASS_STEAM"]["mode"] == "HOMOGENEOUS_FALLBACK"
    eligible = data.loc[data.efficiency_observed]
    assert eligible.projectID.str.contains("'GEO':", regex=False).all()
    assert not eligible.projectID.str.contains("'OPSD':", regex=False).any()


def test_exported_six_network_artifact_integrity():
    cfg = v2a.settings()
    receipt = json.loads((ROOT / cfg["qa_directory"] / "MEM_UC2A_PREPARATION_RECEIPT.json").read_text())
    assert receipt["status"] == v2a.SUCCESS and receipt["solver_invocations"] == 0
    assert len(receipt["structural_packages"]) == 6
    assert receipt["calibration_revision"] == v2a.CALIBRATION
    for item in receipt["structural_packages"]:
        assert item["units_v1"] == item["units_v2A"]
        assert item["theoretical_uc_binaries_v1"] == item["theoretical_uc_binaries_v2A"]
        assert item["serialization_reimport"] == item["deterministic_rebuild"] == "PASS"
        assert item["controlled_efficiency_multipliers_only"] and item["efficiency_MW_envelopes"] == "PASS"
        assert item["HR_recentering_applied"] is False
        assert sha256_file(ROOT / item["output"]) == item["output_sha256"]
    for path, digest in receipt["protected_artifact_hashes"].items():
        assert sha256_file(ROOT / path) == digest


def test_exported_benchmark_paths_are_isolated_and_wrong_case_refused(monkeypatch):
    job = v2a.benchmark_job(2040, "Slow", "v2A")
    assert job["input"] == v2a.output_path(2040, "Slow")
    assert "uc2a_manual_benchmark" in str(job["directory"])
    with pytest.raises(RuntimeError, match="BENCHMARK_CASE_REFUSED"):
        v2a.benchmark_job(2040, "Slow", "v1")
    with pytest.raises(RuntimeError, match="BENCHMARK_CASE_REFUSED"):
        v2a.benchmark_job(2050, "Base", "v2A")
    jobs = pd.read_csv(ROOT / v2a.settings()["qa_directory"] / "MEM_UC2A_FUTURE_MANUAL_BENCHMARK_JOBS.csv")
    assert len(jobs) == 1 and jobs.variant.eq("v2A").all()
    from mem_model import stage_b_uc2 as uc2
    monkeypatch.setattr(uc2, "_manual_solve", lambda *args, **kwargs: pytest.fail("No benchmark execution permitted"))
    with pytest.raises(SystemExit):
        uc2.main(["run-uc", "--year", "2040", "--scenario", "Slow", "--benchmark-variant", "v1"])


def test_exported_2050_nuclear_is_identical_and_ocgt_is_calibrated():
    cfg = v2a.settings()
    crosswalk = pd.read_csv(ROOT / cfg["qa_directory"] / "MEM_UC2A_CHILD_CROSSWALK.csv", float_precision="round_trip")
    nuclear = crosswalk.loc[crosswalk.prime_mover.eq("NUCLEAR_MODULAR")]
    assert nuclear.v1_child_id.equals(nuclear.child_id)
    assert nuclear.groupby(["year", "scenario"]).size().eq(27).all()
    ocgt = crosswalk.loc[crosswalk.prime_mover.eq("OCGT")]
    assert ocgt.reference_capacity_rows.eq(130).all()
    assert ocgt.reference_heat_rate_rows.eq(0).all()
    assert ocgt.joint_mode.eq("INDEPENDENT_SIZE_CONTROLLED_EFFICIENCY").all()
    assert ocgt.efficiency.eq(ocgt.parent_efficiency * ocgt.efficiency_multiplier).all()
    before = pypsa.Network(uc1.derivative_path(2050, "Base"))
    after = pypsa.Network(v2a.output_path(2050, "Base"))
    names = before.generators.index[before.generators.carrier.eq("nuclear")]
    pd.testing.assert_frame_equal(before.generators.loc[names], after.generators.loc[names])

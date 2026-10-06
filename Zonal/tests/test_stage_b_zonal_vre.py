"""Non-solving adapter tests. Optimization constructors are intercepted."""
import numpy as np
import pandas as pd
import pypsa
import pytest
from mem_model import stage_b_zonal_vre as zonal


@pytest.fixture(autouse=True)
def forbidden_models():
    with zonal.no_models_or_solves():
        yield


@pytest.fixture
def profiles():
    rows = []
    rng = np.random.default_rng(13)
    for family in zonal.FAMILIES.values():
        for zone in zonal.ZONES:
            rows.append(pd.DataFrame({"snapshot": zonal.SNAPSHOTS.tz_localize("UTC"),
                                     "zone": zone, "technology": family,
                                     "p_max_pu": rng.uniform(0, .8, 8760)}))
    return pd.concat(rows, ignore_index=True)


@pytest.fixture
def network():
    n = pypsa.Network()
    n.set_snapshots(zonal.SNAPSHOTS)
    for zone in zonal.ZONES:
        n.add("Bus", zone)
        for carrier in zonal.CARRIERS:
            n.add("Generator", f"{zone}_{carrier}", bus=zone, carrier=carrier, p_nom=123., p_max_pu=np.full(8760, .15))
        n.add("Generator", f"{zone}_P2X", bus=zone, carrier=f"P2X_{zone}", sign=-1, p_nom=40., p_max_pu=1.)
        n.add("GlobalConstraint", f"{zone}_P2X_annual", type="operational_limit", carrier_attribute=f"P2X_{zone}", sense="==", constant=1e4)
        n.add("Load", f"{zone}_rigid", bus=zone, p_set=51.)
    n.meta = {"uc2a_calibration_revision": "CONTROLLED_ETA_15_50_35_V2", "parent": "fixture"}
    return n


def test_complete_profiles_are_21_full_year_series(profiles):
    summary, pairs = zonal.validate_profiles(profiles)
    assert len(summary) == 21 and len(pairs) == 63
    assert summary.snapshots.eq(8760).all()
    assert not pairs.exact_duplicate.any()


@pytest.mark.parametrize("bad", ["duplicate", "missing", "wrong_zone", "negative", "over_one", "nan", "inf", "wrong_time", "tiled", "identical_zone"])
def test_bad_profile_fails_closed(profiles, bad):
    if bad == "duplicate":
        profiles = pd.concat([profiles, profiles.iloc[:1]])
    elif bad == "missing":
        profiles = profiles.iloc[1:]
    elif bad == "wrong_zone":
        profiles.loc[profiles.zone.eq("CALA"), "zone"] = "OTHER"
    elif bad in {"negative", "over_one", "nan", "inf"}:
        profiles.iloc[0, profiles.columns.get_loc("p_max_pu")] = {"negative": -.1, "over_one": 1.1, "nan": np.nan, "inf": np.inf}[bad]
    elif bad == "wrong_time":
        profiles.iloc[0, profiles.columns.get_loc("snapshot")] = pd.Timestamp("2018-01-01", tz="UTC")
    elif bad == "tiled":
        mask = profiles.zone.eq("CALA") & profiles.technology.eq("SOLAR_PV")
        profiles.loc[mask, "p_max_pu"] = np.resize(np.linspace(0, .8, 24), 8760)
    elif bad == "identical_zone":
        mask = profiles.technology.eq("SOLAR_PV")
        profiles.loc[mask & profiles.zone.eq("CALA"), "p_max_pu"] = profiles.loc[mask & profiles.zone.eq("CSUD"), "p_max_pu"].to_numpy()
    with pytest.raises(RuntimeError):
        zonal.validate_profiles(profiles)


def test_only_vre_dynamic_values_change(network, profiles):
    result = zonal.apply_profiles(network, profiles)
    qa, coverage = zonal.verify_derivative(network, result, profiles)
    assert qa["status"] == "PASS" and len(coverage) == 28
    assert qa["changed_VRE_columns"] == 28
    for zone in zonal.ZONES:
        np.testing.assert_array_equal(result.generators_t.p_max_pu[f"{zone}_solar_pv_rooftop"], result.generators_t.p_max_pu[f"{zone}_solar_pv_utility"])
    pd.testing.assert_frame_equal(network.global_constraints, result.global_constraints)
    pd.testing.assert_frame_equal(network.generators, result.generators)


@pytest.mark.parametrize("field", ["capacity", "UC", "P2X", "load", "non_VRE_availability", "target_availability", "metadata"])
def test_other_drift_refused(network, profiles, field):
    result = zonal.apply_profiles(network, profiles)
    if field == "capacity": result.generators.loc["CALA_solar_pv_rooftop", "p_nom"] += 1
    if field == "UC": result.generators.loc["CALA_solar_pv_rooftop", "committable"] = True
    if field == "P2X": result.global_constraints.loc["CALA_P2X_annual", "constant"] += 1
    if field == "load": result.loads.loc["CALA_rigid", "p_set"] += 1
    if field == "non_VRE_availability": result.generators_t.p_max_pu["CALA_P2X"] = .9
    if field == "target_availability": result.generators_t.p_max_pu.loc[result.snapshots[0], "CALA_solar_pv_rooftop"] = .99
    if field == "metadata": result.meta["parent"] = "wrong"
    with pytest.raises((RuntimeError, AssertionError)):
        zonal.verify_derivative(network, result, profiles)


@pytest.mark.parametrize("year,scenario", zonal.SCENARIOS)
def test_new_paths_do_not_overwrite_parents(year, scenario):
    path = zonal.output_path(year, scenario)
    assert "uc2a_zonal_vre_v1" in str(path) and path.name.endswith("UNSOLVED.nc")
    assert "results" not in path.parts


def test_execution_or_unknown_scenario_impossible():
    with pytest.raises(SystemExit): zonal.main(["solve"])
    with pytest.raises(ValueError): zonal.output_path(2040, "base")
    with pytest.raises(ValueError): zonal.output_path(2060, "Base")


def test_model_guard_blocks_even_constructor():
    from linopy import Model
    with pytest.raises(RuntimeError, match="MODEL_OR_SOLVER_FORBIDDEN"):
        Model()


@pytest.mark.parametrize("bad", ["missing", "duplicate", "extra", "wrong_path", "failed", "reload_fail"])
def test_six_package_coverage_cannot_be_silently_underreported(bad):
    packages = [{"year": year, "scenario": scenario, "output": str(zonal.output_path(year, scenario).relative_to(zonal.ROOT)),
                 "status": "PASS", "serialization_reimport": "PASS"} for year, scenario in zonal.SCENARIOS]
    if bad == "missing": packages.pop()
    if bad == "duplicate": packages[-1] = dict(packages[0])
    if bad == "extra": packages.append(dict(packages[0]))
    if bad == "wrong_path": packages[0]["output"] = packages[1]["output"]
    if bad == "failed": packages[0]["status"] = "FAIL"
    if bad == "reload_fail": packages[0]["serialization_reimport"] = "FAIL"
    with pytest.raises(RuntimeError): zonal.validate_package_coverage({"structural_packages": packages})


@pytest.mark.parametrize("bad", [None, "missing", "duplicate", "wrong_carrier", "wrong_technology"])
def test_capacity_weather_summary_uses_exact_frozen_mw(profiles, bad):
    summary, _ = zonal.validate_profiles(profiles)
    capacities = pd.DataFrame([{"year": year, "scenario": scenario, "zone": zone,
        "carrier": carrier, "technology": family, "p_nom_MW": 123.}
        for year, scenario in zonal.SCENARIOS for zone in zonal.ZONES
        for carrier, family in zonal.CARRIERS.items()])
    weights = summary[["zone", "technology"]].assign(eligible_resource_potential_MW=1000.)
    if bad == "missing": capacities = capacities.iloc[1:]
    if bad == "duplicate": capacities = pd.concat([capacities, capacities.iloc[:1]])
    if bad == "wrong_carrier": capacities.loc[0, "carrier"] = "nuclear"
    if bad == "wrong_technology": capacities.loc[0, "technology"] = "WIND_ONSHORE"
    if bad:
        with pytest.raises(RuntimeError): zonal.profile_capacity_qa(summary, capacities, weights)
        return
    result = zonal.profile_capacity_qa(summary, capacities, weights)
    assert len(result) == 126
    assert result.loc[result.technology.eq("SOLAR_PV"), "p_nom_MW"].eq(246.).all()
    assert result.loc[result.technology.ne("SOLAR_PV"), "p_nom_MW"].eq(123.).all()
    np.testing.assert_allclose(result.potential_TWh, result.p_nom_MW * result.availability_CF * 8760 / 1e6)

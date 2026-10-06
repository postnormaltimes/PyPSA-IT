"""No-solver verification of the versioned 2040/2050 P2X-flex correction."""

from __future__ import annotations

import math

import pandas as pd
import pypsa
import pytest

from mem_model.common import ROOT, sha256_file
from mem_model import solve_p2x_flex
from mem_model.solve_p2x_flex import authorize_request, result_dir
from mem_model.solve_scenario import authorize_production_request
from mem_model.stage_b_p2x_flex import (
    QA_ROOT,
    STATE_NAME,
    UNCHANGED_MEMBERS,
    baseline_runtime,
    network_path,
    runtime_dir,
    scenario_totals,
    verify_network,
    verify_runtime,
)


@pytest.mark.parametrize("year,base_total,base_rigid,base_p2x,base_power", [
    (2040, 439.0, 411.5, 27.5, 7400.0),
    (2050, 583.1, 501.6, 81.5, 21930.90909090909),
])
def test_frozen_split_and_high_multiplier(year, base_total, base_rigid, base_p2x, base_power):
    for scenario, factor in (("Slow", 1.0), ("Base", 1.0), ("High", 1.1)):
        values = scenario_totals(year, scenario)
        assert values["total_TWh"] == pytest.approx(base_total * factor)
        assert values["rigid_TWh"] == pytest.approx(base_rigid * factor)
        assert values["p2x_TWh"] == pytest.approx(base_p2x * factor)
        assert values["p2x_power_MW"] == pytest.approx(base_power * factor)
        assert values["rigid_TWh"] + values["p2x_TWh"] == pytest.approx(values["total_TWh"])
        if year == 2050:
            assert values["power_authority"] == "DERIVED_2040_UTILISATION_CARRY_FORWARD"


@pytest.mark.parametrize("year", [2040, 2050])
def test_versioned_runtime_is_split_and_other_members_byte_identical(year):
    verified = verify_runtime(year)
    assert verified["status"] == "PASS"
    assert len(verified["checks"]) == 21
    for member in UNCHANGED_MEMBERS:
        assert sha256_file(runtime_dir(year) / member) == sha256_file(baseline_runtime(year) / member)
    contract = pd.read_csv(runtime_dir(year) / "p2x_contract.csv")
    assert contract.groupby("scenario").annual_rigid_MWh.sum().div(1e6).to_dict() == pytest.approx(
        {scenario: scenario_totals(year, scenario)["rigid_TWh"] for scenario in ("Slow", "Base", "High")}
    )
    assert contract.groupby("scenario").annual_p2x_MWh.sum().div(1e6).to_dict() == pytest.approx(
        {scenario: scenario_totals(year, scenario)["p2x_TWh"] for scenario in ("Slow", "Base", "High")}
    )


@pytest.mark.parametrize("year,scenario", [(y, s) for y in (2040, 2050) for s in ("Slow", "Base", "High")])
def test_exported_unsolved_network_has_exact_p2x_equalities_and_no_non_demand_drift(year, scenario):
    network = pypsa.Network(network_path(year, scenario))
    qa = verify_network(network, year, scenario)
    assert qa["status"] == "PASS"
    assert qa["p2x_energy_equalities"] == 7
    assert qa["integer_variables"] == qa["binary_variables"] == qa["optimizer_invocations"] == 0
    assert len(network.snapshots) == 8760
    assert not result_dir(year, scenario).exists()


def test_native_pypsa_linopy_equality_is_continuous_without_solving():
    network = pypsa.Network()
    network.set_snapshots(pd.date_range("2019-01-01", periods=2, freq="h"))
    network.add("Carrier", "AC")
    network.add("Carrier", "p2x_flexible_NORD")
    network.add("Bus", "NORD", carrier="AC")
    network.add("Generator", "SUPPLY", bus="NORD", p_nom=10, marginal_cost=1)
    network.add("Generator", "P2X_NORD", bus="NORD", carrier="p2x_flexible_NORD", sign=-1,
                p_nom=5, p_min_pu=0, p_max_pu=1)
    network.add("GlobalConstraint", "P2X_ANNUAL_NORD", type="operational_limit",
                carrier_attribute="p2x_flexible_NORD", sense="==", constant=6)
    model = network.optimize.create_model(include_objective_constant=False)
    assert "GlobalConstraint-P2X_ANNUAL_NORD" in model.constraints
    assert all(not model.variables[key].attrs.get("binary") and not model.variables[key].attrs.get("integer") for key in model.variables)
    assert network.generators.at["P2X_NORD", "sign"] == -1
    assert math.isclose(network.global_constraints.at["P2X_ANNUAL_NORD", "constant"], 6)


def test_manual_authorization_is_versioned_and_never_touches_old_results(monkeypatch):
    for year in (2040, 2050):
        for scenario in ("Slow", "Base", "High"):
            assert authorize_request(year, scenario, execute_full_year=True)["status"] == "AUTHORIZED_FOR_MANUAL_EXECUTION"
            with pytest.raises(SystemExit, match="REFUSED"):
                authorize_request(year, scenario, execute_full_year=False)
    with pytest.raises(SystemExit, match="REFUSED"):
        authorize_request(2060, "Base", execute_full_year=True)
    with pytest.raises(SystemExit, match="REFUSED"):
        authorize_request(2040, "Other", execute_full_year=True)
    assert (QA_ROOT / STATE_NAME).is_file()
    assert all((ROOT / "results" / f"MEM_{year}_{scenario.upper()}_CANONICAL" / f"MEM_{year}_{scenario.upper()}_8760h_SOLVED.nc").is_file()
               for year in (2040, 2050) for scenario in ("Slow", "Base", "High"))
    with pytest.raises(SystemExit, match="REFUSED"):
        authorize_production_request(2040, "Base", execute_full_year=True)
    with pytest.raises(SystemExit, match="REFUSED"):
        authorize_production_request(2050, "Base", execute_full_year=True)


def test_manual_gate_refuses_changed_qa_hash_without_touching_files(monkeypatch):
    original_hash = solve_p2x_flex.sha256_file

    def changed_hash(path):
        return "0" * 64 if path == QA_ROOT / STATE_NAME else original_hash(path)

    monkeypatch.setattr(solve_p2x_flex, "sha256_file", changed_hash)
    with pytest.raises(SystemExit, match="QA state hash changed"):
        authorize_request(2040, "Base", execute_full_year=True)

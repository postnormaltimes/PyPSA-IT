"""Pinned horizon-cost authority and unit conversion, with no models/solvers."""
import numpy as np
import pandas as pd
import pytest

from mem_model import final_wind_horizon_cost as costs
from mem_model.common import ROOT, load_yaml
from mem_model.stage_b_zonal_vre import no_models_or_solves


@pytest.fixture(autouse=True)
def no_optimizer():
    with no_models_or_solves():
        yield


def test_direct_2040_authority_matches_independent_native_coefficients(monkeypatch):
    monkeypatch.setattr(costs,"dump_json",lambda *args:None)
    annual = costs.recover_2040_authority()
    assert np.isclose(annual["offwind"],201054.93354524614)
    assert np.isclose(annual["offwind-ac-station"],70207.12832141604)
    assert np.isclose(annual["offwind-ac-connection-submarine"],413.0377435480106)


def test_2040_is_not_silently_2050_proxy():
    annual2040,_ = costs.annual_costs(costs.SOURCE / "costs_2040.csv")
    annual2050,_ = costs.annual_costs(costs.SOURCE_ROOT / "data/costs/archive/v0.14.0/costs_2050.csv")
    assert annual2040["offwind"] != annual2050["offwind"]
    assert annual2040["offwind-ac-station"] != annual2050["offwind-ac-station"]
    assert annual2040["offwind-ac-connection-submarine"] != annual2050["offwind-ac-connection-submarine"]


def test_current_config_records_horizon_authority_and_diagnostic_stress():
    wind = load_yaml(ROOT / "config/final_methodology_closure.yaml")["wind"]
    assert wind["offshore_cost_authorities"][2040]["sha256"] == costs.COST_2040_SHA
    assert wind["offshore_cost_authorities"][2050]["sha256"] == costs.COST_2050_SHA
    assert wind["common_generation_cost_stress_is_diagnostic_not_authority_gate"] is True
    assert wind["effective_published_cost_currency_basis"] == "EUR2025"


def test_cost_unit_conversion_and_annualization_are_once():
    annual,rows = costs.annual_costs(costs.SOURCE / "costs_2040.csv")
    generation = next(row for row in rows if row["technology"] == "offwind")
    assert generation["investment_EUR_per_MW_or_MW_km"] == 1964416.9
    factor = .07/(1-1.07**-30)
    assert annual["offwind"] == (factor+2.1762/100)*1964416.9
    cable = next(row for row in rows if row["technology"] == "offwind-ac-connection-submarine")
    assert cable["investment_EUR_per_MW_or_MW_km"] == 4130.


def test_changed_cost_authority_fails_closed(monkeypatch):
    monkeypatch.setattr(costs,"sha256_file",lambda path:"changed")
    with pytest.raises(RuntimeError,match="AUTHORITY_HASH_FAIL"):
        costs.recover_2040_authority()


def test_duplicate_parameter_fails_closed(tmp_path):
    path = tmp_path / "costs.csv"
    pd.DataFrame({"technology":["offwind"]*2,"parameter":["investment"]*2,"value":[100.,100.]}).to_csv(path,index=False)
    with pytest.raises(RuntimeError,match="DUPLICATE_PARAMETER"):
        costs.annual_costs(path)

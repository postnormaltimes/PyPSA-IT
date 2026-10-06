"""Read-only hydro shape and dimensional balance QA; no optimization models."""
import numpy as np
import pandas as pd
import pytest

from mem_model.final_hydro_audit import state_step, validate_shape
from mem_model.stage_b_zonal_vre import no_models_or_solves


@pytest.fixture(autouse=True)
def prohibit_models_and_solvers():
    with no_models_or_solves():
        yield


def test_shape_keeps_accepted_2019_chronology_and_annual_unit_mass():
    shape = validate_shape(np.ones(8760),pd.date_range("2019-01-01",periods=8760,freq="h",tz="UTC"))
    assert np.isclose(shape.sum(),1)
    assert len(shape)==8760


@pytest.mark.parametrize("bad",[-1.,np.nan,np.inf])
def test_negative_or_missing_runoff_cannot_be_silently_repaired(bad):
    values = np.ones(8760)
    values[100]=bad
    with pytest.raises(RuntimeError,match="NONNEGATIVE_FINITE"):
        validate_shape(values,pd.date_range("2019-01-01",periods=8760,freq="h"))


def test_short_or_wrong_year_profile_fails_closed():
    with pytest.raises(RuntimeError,match="CHRONOLOGY"):
        validate_shape(np.ones(8760),pd.date_range("2020-01-01",periods=8760,freq="h"))


def test_zero_annual_runoff_cannot_be_normalized():
    with pytest.raises(RuntimeError,match="NONNEGATIVE_FINITE"):
        validate_shape(np.zeros(8760),pd.date_range("2019-01-01",periods=8760,freq="h"))


def test_water_and_electric_units_loss_and_weight_are_applied_once():
    result = state_step(100.,hours=2.,standing_loss=.01,inflow_MW_water=5.,
        pump_MW_electric=10.,pump_efficiency=.8,turbine_MW_water=4.,spill_MW_water=1.)
    assert result == pytest.approx(.99**2*100+2*(5+.8*10-4-1))


def test_cyclic_wrap_uses_last_state_not_first_hour_output():
    first_output=state_step(100.,hours=1.,standing_loss=0.,inflow_MW_water=5.,
        pump_MW_electric=0.,pump_efficiency=1.,turbine_MW_water=3.,spill_MW_water=0.)
    assert first_output == 102.
    assert first_output != 100.

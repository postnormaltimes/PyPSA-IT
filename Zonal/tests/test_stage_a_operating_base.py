from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from mem_model.stage_a.operating_base import (
    DEFAULT_OUTPUT_DIR,
    OUTPUT_FILES,
    build_operating_base_package,
    sha256_file,
)
from mem_model.stage_a.operating_validation import validate_operating_base_package


def _read(key: str, directory: Path = DEFAULT_OUTPUT_DIR) -> pd.DataFrame:
    path = directory / OUTPUT_FILES[key]
    return pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path, dtype=str, keep_default_na=False)


def test_generator_coverage_and_common_carbon() -> None:
    generators = _read("generators")
    assert len(generators) == 165
    assert generators.asset_id.is_unique
    assert set(generators.country_code) == {"IT", "FR", "CH", "AT", "SI", "HR", "ME", "GR", "MT", "TN"}
    assert generators.p_nom_extendable.str.lower().eq("false").all()
    assert set(pd.to_numeric(generators.loc[generators.year.eq("2040"), "CO2_price_EUR2025_per_t"])) == {104.5504}
    assert set(pd.to_numeric(generators.loc[generators.year.eq("2050"), "CO2_price_EUR2025_per_t"])) == {400.0}


def test_marginal_cost_arithmetic_and_no_fixed_costs() -> None:
    generators = _read("generators")
    for column in ["efficiency_el", "fuel_price_EUR2025_per_MWh_th", "VOM_EUR2025_per_MWh_el", "chargeable_CO2_t_per_MWh_th", "CO2_price_EUR2025_per_t", "other_variable_cost_EUR2025_per_MWh_el", "marginal_cost_EUR2025_per_MWh_el"]:
        generators[column] = pd.to_numeric(generators[column], errors="coerce")
    expected = np.where(
        generators.efficiency_el.notna(),
        generators.fuel_price_EUR2025_per_MWh_th.fillna(0) / generators.efficiency_el
        + generators.VOM_EUR2025_per_MWh_el
        + generators.CO2_price_EUR2025_per_t * generators.chargeable_CO2_t_per_MWh_th / generators.efficiency_el
        + generators.other_variable_cost_EUR2025_per_MWh_el,
        generators.VOM_EUR2025_per_MWh_el + generators.other_variable_cost_EUR2025_per_MWh_el,
    )
    assert np.allclose(expected, generators.marginal_cost_EUR2025_per_MWh_el, atol=1e-9, rtol=0)
    assert generators.CAPEX_in_marginal_cost.str.lower().eq("false").all()
    assert generators.fixed_OPEX_in_marginal_cost.str.lower().eq("false").all()


def test_hourly_availability_is_complete_bounded_and_uses_b3_index() -> None:
    availability = _read("availability")
    assert availability.asset_id.nunique() == 85
    assert availability.groupby("asset_id").size().eq(8760).all()
    values = availability.p_max_pu.to_numpy(float)
    assert np.isfinite(values).all()
    assert values.min() >= 0
    assert values.max() <= 1
    assert availability.historical_capacity_factor_used.astype(str).str.lower().eq("false").all()
    assert availability.slow_CDP_factor_used.astype(str).str.lower().eq("false").all()


def test_storage_runtime_parameters_preserve_capacity_and_efficiencies() -> None:
    storage = _read("storage")
    assert len(storage) == 43
    assert storage.p_nom_extendable.str.lower().eq("false").all()
    assert storage.e_nom_extendable.str.lower().eq("false").all()
    bess = storage.loc[storage.storage_family.eq("BESS")]
    phs = storage.loc[storage.storage_family.eq("PHS")]
    assert np.allclose(pd.to_numeric(bess.round_trip_efficiency), 0.9)
    assert np.allclose(pd.to_numeric(bess.charge_efficiency), math.sqrt(0.9))
    assert np.allclose(pd.to_numeric(phs.round_trip_efficiency), 0.75)
    assert np.allclose(pd.to_numeric(phs.charge_efficiency), math.sqrt(0.75))
    assert set(storage.terminal_state_rule) == {"CYCLIC_ANNUAL"}


def test_italy_phs_controls_and_single_mixed_state() -> None:
    phs = _read("italy_phs")
    assert len(phs) == 4
    assert phs.state_id.is_unique
    for _, group in phs.groupby("year"):
        assert math.isclose(pd.to_numeric(group.discharge_power_MW).sum(), 7252.3, abs_tol=1e-9)
        assert math.isclose(pd.to_numeric(group.charge_power_MW).sum(), 6400.0, abs_tol=1e-9)
        assert math.isclose(pd.to_numeric(group.operational_energy_MWh).sum(), 53000.0, abs_tol=1e-9)
    mixed = phs.loc[phs.hydro_class.eq("MIXED_PHS")]
    assert mixed.shared_water_state_count.astype(int).eq(1).all()
    assert mixed.state_receives.eq("NATURAL_INFLOW_AND_ELECTRICAL_PUMPING").all()


def test_hydro_conservation_and_physical_inventory_exclusion() -> None:
    hydro = _read("hydro")
    natural = hydro.loc[hydro.natural_inflow_required.str.lower().eq("true")]
    assert np.allclose(
        pd.to_numeric(natural.annual_natural_inflow_MWh_water) * pd.to_numeric(natural.turbine_efficiency),
        pd.to_numeric(natural.annual_electrical_generation_reference_MWh),
        atol=1e-8,
        rtol=0,
    )
    assert pd.to_numeric(hydro.physical_HPHS_energy_MWh_used).eq(0).all()
    assert pd.to_numeric(hydro.physical_HDAM_energy_MWh_used).eq(0).all()
    conventional = hydro.loc[~hydro.hydro_class.isin(["PURE_PHS", "MIXED_PHS"])]
    assert conventional.grid_charging_allowed.str.lower().eq("false").all()


def test_b3_erratum_is_sidecar_only() -> None:
    erratum = _read("b3_erratum")
    assert set(erratum.profile_id) == {"ETX7B3_MT_SOLAR_PV_2019", "ETX7B3_TN_CSP_2019"}
    assert erratum.prospective_corrected_classification.eq("OFFICIAL_TYNDP_PECD_2019").all()
    assert erratum.b3_file_changed.str.lower().eq("false").all()


def test_b4_rebuild_is_byte_deterministic(tmp_path: Path) -> None:
    rebuilt = tmp_path / "operating_base"
    build_operating_base_package(rebuilt)
    for filename in OUTPUT_FILES.values():
        assert (DEFAULT_OUTPUT_DIR / filename).read_bytes() == (rebuilt / filename).read_bytes()


def test_full_b4_validation_passes_without_network_or_solver() -> None:
    result = validate_operating_base_package(DEFAULT_OUTPUT_DIR, write_reports=False, run_deterministic_rebuild=False)
    assert result["qa"].status.eq("PASS").all()
    assert result["verification"]["scope"]["topology"] == "NOT_BUILT"
    assert result["verification"]["scope"]["pypsa_network"] == "NOT_INSTANTIATED"
    assert result["verification"]["scope"]["optimization"] == "NOT_RUN"

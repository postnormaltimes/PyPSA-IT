from __future__ import annotations

import pytest

from mem_model.build_profiles import build_fixture, maintenance_profile
from mem_model.common import SCENARIOS
from mem_model.network import build_network
from mem_model.runtime_bundle import validate_runtime_bundle
from mem_model.validation import validate_network


@pytest.fixture(scope="session")
def fixture_bundle():
    return build_fixture(24)


@pytest.mark.parametrize("year,scenario", SCENARIOS)
def test_all_six_scenarios_instantiate_without_solver(fixture_bundle, year: int, scenario: str) -> None:
    network, metadata = build_network(fixture_bundle, year, scenario)
    qa = validate_network(network, year, scenario, fixture=True, assemble_model=False)
    assert set(qa["status"]) == {"PASS"}, qa.loc[qa["status"].eq("FAIL")].to_string(index=False)
    assert metadata["year"] == year
    assert metadata["scenario"] == scenario


def test_2040_base_linopy_model_assembles_without_solver(fixture_bundle) -> None:
    network, _ = build_network(fixture_bundle, 2040, "Base")
    qa = validate_network(network, 2040, "Base", fixture=True, assemble_model=True)
    assert set(qa["status"]) == {"PASS"}, qa.loc[qa["status"].eq("FAIL")].to_string(index=False)
    assert "NS-030" in set(qa["check_id"])


def test_fixture_bundle_passes_content_level_schema_and_grain_qa(fixture_bundle) -> None:
    qa = validate_runtime_bundle(fixture_bundle, require_complete_year=False)
    assert set(qa["status"]) == {"PASS"}, qa.loc[qa["status"].eq("FAIL")].to_string(index=False)


def test_fixture_uses_separate_pure_and_mixed_phs_states(fixture_bundle) -> None:
    import pandas as pd

    parameters = pd.read_csv(fixture_bundle / "hydro_runtime_parameters.csv")
    classes = set(parameters["hydro_class"])
    assert "PURE_PHS" in classes
    assert "MIXED_PHS" in classes
    assert not any(str(value).startswith("PHS_AGGREGATE") for value in classes)
    pure = parameters.loc[parameters["hydro_class"].eq("PURE_PHS")]
    mixed = parameters.loc[parameters["hydro_class"].eq("MIXED_PHS")]
    assert not pure["natural_inflow_allowed"].astype(bool).any()
    assert mixed["natural_inflow_allowed"].astype(bool).all()


def test_maintenance_staggering_is_deterministic_and_id_specific() -> None:
    import pandas as pd

    index = pd.date_range("2019-01-01", periods=8760, freq="h", tz="UTC")
    first = maintenance_profile("A", 0.9, 240, index)
    repeat = maintenance_profile("A", 0.9, 240, index)
    second = maintenance_profile("B", 0.9, 240, index)
    assert (first == repeat).all()
    assert not (first == second).all()
    assert (first == 0).sum() == 240

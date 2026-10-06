from __future__ import annotations

from pathlib import Path

import pandas as pd

from mem_model.stage_a.static_inputs import OUTPUT_FILES, build_static_package
from mem_model.stage_a.validation import validate_static_package


def _build(tmp_path: Path) -> dict:
    return build_static_package(tmp_path / "static")


def test_static_contract_counts_and_domains(tmp_path: Path) -> None:
    package = _build(tmp_path)
    frames = package["frames"]
    assert len(frames["canonical"]) == 207
    assert len(frames["runtime"]) == 161
    assert len(frames["demand"]) == 18
    assert len(frames["zeros"]) == 98
    assert set(frames["demand"]["year"]) == {2040, 2050}
    assert set(frames["demand"]["country_code"]) == {
        "FR", "CH", "AT", "SI", "HR", "ME", "GR", "MT", "TN"
    }
    assert frames["generators"]["p_nom_MW"].gt(0).all()
    assert not frames["generators"]["p_nom_extendable"].any()


def test_nuclear_and_storage_governance(tmp_path: Path) -> None:
    frames = _build(tmp_path)["frames"]
    generators = frames["generators"]
    storage = frames["storage"]
    nuclear = generators.loc[generators["harmonised_group"].isin(["nuclear", "nuclear_smr"])]
    assert not nuclear["country_code"].eq("HR").any()
    assert nuclear["country_code"].eq("SI").any()
    assert storage.groupby(["country_code", "year", "storage_class"]).size().eq(1).all()
    assert storage[["charge_power_MW", "discharge_power_MW", "energy_MWh"]].gt(0).all().all()
    assert set(storage["distributed_vs_grid_flag"]) == {
        "PUMPED_HYDRO", "GRID_SCALE", "DISTRIBUTED_SMALL_SCALE"
    }


def test_fr_and_gr_phs_mapping_is_explicit(tmp_path: Path) -> None:
    storage = _build(tmp_path)["frames"]["storage"]
    fr = storage.loc[(storage["country_code"] == "FR") & (storage["storage_class"] == "PHS")]
    gr = storage.loc[(storage["country_code"] == "GR") & (storage["storage_class"] == "PHS")]
    assert len(fr) == 2 and len(gr) == 2
    assert fr["charge_power_mapping"].eq(
        "TECHNICAL_SYMMETRIC_MAPPING_FROM_FROZEN_PHS_POWER"
    ).all()
    assert gr["charge_power_mapping"].eq(
        "ETX7A_APPROVED_TECHNICAL_SYMMETRIC_MAPPING_FROM_OFFICIAL_PHS_POWER"
    ).all()
    assert (fr["charge_power_MW"] == fr["discharge_power_MW"]).all()
    assert (gr["charge_power_MW"] == gr["discharge_power_MW"]).all()


def test_adapter_is_byte_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    build_static_package(first)
    build_static_package(second)
    assert sorted(path.name for path in first.glob("*.csv")) == sorted(
        path.name for path in second.glob("*.csv")
    )
    for filename in OUTPUT_FILES.values():
        assert (first / filename).read_bytes() == (second / filename).read_bytes()


def test_full_etx7b1_validation_and_stage_b_regression(tmp_path: Path) -> None:
    output_dir = tmp_path / "static"
    build_static_package(output_dir)
    validation = validate_static_package(output_dir, write_reports=False)
    assert set(validation["qa"]["status"]) == {"PASS"}
    assert validation["verification"]["stage_b_frozen_hashes_unchanged"] is True
    assert validation["verification"]["scope_confirmation"] == {
        "italy_aggregation": "NOT_STARTED_BY_PHASE_SCOPE",
        "hourly_profiles": "NOT_STARTED_BY_PHASE_SCOPE",
        "topology_or_network": "NOT_STARTED_BY_PHASE_SCOPE",
        "pypsa_network": "NOT_INSTANTIATED",
        "optimization": "NOT_RUN",
    }

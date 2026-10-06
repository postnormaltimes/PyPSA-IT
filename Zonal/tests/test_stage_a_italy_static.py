from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pandas as pd

from mem_model.stage_a.italy_static import (
    OUTPUT_FILES,
    ROOT,
    build_italy_static_package,
    sha256_file,
    verify_frozen_input_receipts,
)
from mem_model.stage_a.italy_validation import validate_italy_static_package


def _build(tmp_path: Path) -> dict:
    return build_italy_static_package(tmp_path / "italy_static")


def _sum(frame, year: int, column: str) -> Decimal:
    selected = frame.loc[frame["year"].astype(int).eq(year), column]
    return sum((Decimal(str(value)) for value in selected), Decimal("0"))


def test_project_plan_v2_is_the_only_active_authority() -> None:
    manifest = pd.read_csv(
        ROOT / "docs" / "project_plan" / "MEM_PROJECT_PLAN_MANIFEST.csv",
        dtype=str,
        keep_default_na=False,
    )
    assert manifest["authority_status"].value_counts().to_dict() == {
        "ACTIVE": 1,
        "SUPERSEDED": 1,
    }
    for record in manifest.to_dict(orient="records"):
        path = ROOT / record["relative_path"]
        assert path.exists()
        assert path.stat().st_size == int(record["bytes"])
        assert sha256_file(path) == record["sha256"]


def test_frozen_stage_b_and_etx7b1_receipts_pass() -> None:
    receipts = verify_frozen_input_receipts()
    assert len(receipts) == 16
    assert receipts["status"].eq("PASS").all()
    assert receipts.groupby("receipt_family").size().to_dict() == {
        "FROZEN_ETX7B1_STATIC_OUTPUT": 7,
        "FROZEN_STAGE_B_STATIC_CONTRACT": 9,
    }


def test_italy_demand_is_exact_and_base_only(tmp_path: Path) -> None:
    frames = _build(tmp_path)["frames"]
    demand = frames["demand"]
    assert len(demand) == 2
    assert set(demand["year"].astype(int)) == {2040, 2050}
    assert set(demand["scenario"]) == {"Base"}
    assert set(demand["country_code"]) == {"IT"}
    assert set(demand["contributing_zone_count"].astype(int)) == {7}
    assert _sum(demand, 2040, "annual_demand_MWh") == Decimal("439000000")
    assert _sum(demand, 2050, "annual_demand_MWh") == Decimal("583100000")


def test_generation_reconciles_and_phs_does_not_leak(tmp_path: Path) -> None:
    generators = _build(tmp_path)["frames"]["generators"]
    source_rows = generators.loc[
        generators["accounting_origin"].eq("STAGE_B_GENERATOR_CONTRACT")
    ]
    hydro_rows = generators.loc[
        generators["accounting_origin"].eq("HYDRO_STATIC_COMPONENT_MAPPING")
    ]
    assert _sum(source_rows, 2040, "p_nom_MW") == Decimal("231439.000000004005")
    assert _sum(source_rows, 2050, "p_nom_MW") == Decimal("332239.000000003001")
    assert _sum(hydro_rows, 2040, "p_nom_MW") == Decimal("9802.700000002")
    assert _sum(hydro_rows, 2050, "p_nom_MW") == Decimal("9802.700000002")
    assert generators["asset_id"].is_unique
    assert generators["p_nom_extendable"].astype(str).str.lower().eq("false").all()
    assert not generators[
        ["parent_capacity_technology", "stage_a_static_class", "carrier"]
    ].apply(lambda column: column.str.contains("PHS|PUMPED", case=False, regex=True)).any().any()


def test_storage_reconciles_and_preserves_phs_governance(tmp_path: Path) -> None:
    storage = _build(tmp_path)["frames"]["storage"]
    bess = storage.loc[storage["storage_family"].eq("BESS")]
    phs = storage.loc[storage["storage_family"].eq("PHS")]
    assert len(storage) == 7
    assert len(bess.loc[bess["year"].astype(int).eq(2040)]) == 4
    assert len(bess.loc[bess["year"].astype(int).eq(2050)]) == 1
    assert _sum(bess, 2040, "charge_power_MW") == Decimal("27266.666666666668")
    assert _sum(bess, 2040, "energy_MWh") == Decimal("166400")
    assert _sum(bess, 2050, "charge_power_MW") == Decimal("43653.061224489793")
    assert _sum(bess, 2050, "energy_MWh") == Decimal("237666.666666666667")
    assert len(phs) == 2
    assert set(phs["charge_power_MW"]) == {"6400"}
    assert set(phs["discharge_power_MW"]) == {"7252.3"}
    assert set(phs["energy_MWh"]) == {"53000"}
    assert set(phs["pure_phs_discharge_subset_MW"]) == {"3969.57561"}
    assert set(phs["mixed_phs_discharge_subset_MW"]) == {"3282.72439"}
    assert storage["p_nom_extendable"].astype(str).str.lower().eq("false").all()
    assert storage["e_nom_extendable"].astype(str).str.lower().eq("false").all()


def test_crosswalk_and_provenance_cover_every_output(tmp_path: Path) -> None:
    frames = _build(tmp_path)["frames"]
    crosswalk = frames["crosswalk"]
    provenance = frames["provenance"]
    target_ids = (
        set(frames["demand"]["demand_id"])
        | set(frames["generators"]["asset_id"])
        | set(frames["storage"]["storage_id"])
    )
    assert len(crosswalk) == len(frames["generators"]) + len(frames["storage"])
    assert crosswalk["crosswalk_id"].is_unique
    assert crosswalk["operating_parameters_status"].eq("NOT_ASSIGNED_BY_ETX7B2").all()
    assert target_ids <= set(provenance["target_record_id"])
    assert provenance["provenance_id"].is_unique


def test_italy_adapter_is_byte_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    build_italy_static_package(first)
    build_italy_static_package(second)
    for filename in OUTPUT_FILES.values():
        assert (first / filename).read_bytes() == (second / filename).read_bytes()


def test_full_etx7b2_validation_and_scope(tmp_path: Path) -> None:
    output_dir = tmp_path / "italy_static"
    build_italy_static_package(output_dir)
    result = validate_italy_static_package(output_dir, write_reports=False)
    assert len(result["qa"]) == 25
    assert result["qa"]["status"].eq("PASS").all()
    assert result["verification"]["status"] == (
        "ETX7B2_ITALY_ONE_NODE_STATIC_AGGREGATION_COMPLETE"
    )
    assert result["verification"]["scope_confirmation"] == {
        "hourly_profiles": "NOT_STARTED_BY_PHASE_SCOPE",
        "topology_or_interconnectors": "NOT_STARTED_BY_PHASE_SCOPE",
        "operating_costs_or_efficiencies": "NOT_ASSIGNED_BY_PHASE_SCOPE",
        "storage_soc": "NOT_CONFIGURED_BY_PHASE_SCOPE",
        "pypsa_network": "NOT_INSTANTIATED",
        "optimization": "NOT_RUN",
    }

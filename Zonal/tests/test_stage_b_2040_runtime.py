from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from mem_model.common import ACCEPTED_RUNTIME, ROOT, sha256_file
from mem_model.stage_b_2040_runtime import (
    FINAL_PATH,
    HASH_PATH,
    MANIFEST_NAME,
    QA_PATH,
    RECONCILIATION_PATH,
    RUNTIME_MEMBERS,
    STATE_NAME,
    contract,
    hydro_crosswalk,
    verify_accepted_runtime_manifest,
)


def test_accepted_contract_is_2040_only_and_locked() -> None:
    value = contract()
    assert value["horizon"] == 2040
    assert value["chronology"]["snapshots"] == 8760
    assert value["governance"]["production_optimization_authorized"] is False
    assert value["governance"]["authorized_scenarios"] == []
    assert value["governance"]["unauthorized_scenarios"] == ["Slow", "Base", "High"]
    assert value["governance"]["production_authorization"] == "THREE_SCENARIO_PRODUCTION_COMPLETE_NO_RERUN_AUTHORIZED"
    assert value["governance"]["authorized_mode"] == "FULL_YEAR_CANONICAL"
    assert value["governance"]["authorized_solver"] == "gurobi"
    assert value["governance"]["required_gurobipy_version"] == "13.0.3"
    assert value["governance"]["stage_b_2050"] == "LOCKED"


def test_hydro_static_perimeter_closes_without_double_counting() -> None:
    mapping = pd.read_csv(ROOT / "pre_pypsa_inputs" / "MEM_Hydro_Static_Component_Mapping.csv")
    conventional = mapping.loc[mapping["hydro_class"].isin(["RUN_OF_RIVER", "BASIN_PONDAGE", "RESERVOIR"])]
    phs = mapping.loc[mapping["hydro_class"].isin(["PURE_PHS", "MIXED_PHS"])]
    assert conventional["p_nom_MW_NET"].sum() == pytest.approx(16041.7, abs=1e-6)
    assert phs["p_nom_MW_NET"].sum() == pytest.approx(7252.3, abs=1e-6)
    assert mapping["p_nom_MW_NET"].sum() == pytest.approx(23294.0, abs=1e-6)


def test_method_c_row_level_split_is_exact() -> None:
    states, _ = hydro_crosswalk()
    case = states.loc[states["scenario"].eq("Base") & states["hydro_class"].isin(["PURE_PHS", "MIXED_PHS"])]
    grouped = case.groupby("hydro_class")[["turbine_power_MW", "pump_power_MW", "operational_energy_MWh"]].sum()
    assert grouped.at["PURE_PHS", "turbine_power_MW"] == pytest.approx(3969.57561, abs=1e-6)
    assert grouped.at["MIXED_PHS", "turbine_power_MW"] == pytest.approx(3282.72439, abs=1e-6)
    assert grouped.at["PURE_PHS", "pump_power_MW"] == pytest.approx(5061.436367, abs=1e-6)
    assert grouped.at["MIXED_PHS", "pump_power_MW"] == pytest.approx(1338.563633, abs=1e-6)
    assert grouped.at["PURE_PHS", "operational_energy_MWh"] == pytest.approx(45633.243516, abs=1e-6)
    assert grouped.at["MIXED_PHS", "operational_energy_MWh"] == pytest.approx(7366.756484, abs=1e-6)


def test_pure_phs_has_no_inflow_crosswalk() -> None:
    states, inflows = hydro_crosswalk()
    pure = states.loc[states["hydro_class"].eq("PURE_PHS")]
    assert not pure["natural_inflow_allowed"].any()
    assert "PURE_PHS" not in set(inflows["hydro_class"])


def test_manifest_verification_passes() -> None:
    result = verify_accepted_runtime_manifest(ACCEPTED_RUNTIME)
    assert result["status"] == "PASS"
    assert set(result["members"]) == set(RUNTIME_MEMBERS)


def test_manifest_names_exactly_five_members() -> None:
    manifest = pd.read_csv(ACCEPTED_RUNTIME / MANIFEST_NAME)
    assert tuple(manifest["file"]) == RUNTIME_MEMBERS
    assert manifest["status"].eq("ACCEPTED_HASH_LOCKED").all()


def test_runtime_scope_contains_no_2050_rows() -> None:
    for name in RUNTIME_MEMBERS:
        path = ACCEPTED_RUNTIME / name
        if path.suffix == ".parquet":
            years = set(pq.read_table(path, columns=["year"]).column("year").to_pylist())
        else:
            years = set(pd.read_csv(path, usecols=["year"])["year"])
        assert years == {2040}


def test_load_rows_and_exact_national_controls() -> None:
    load = pd.read_parquet(ACCEPTED_RUNTIME / "load_hourly.parquet")
    assert len(load) == 3 * 7 * 8760
    observed = load.groupby("scenario")["load_MW"].sum() / 1_000_000.0
    assert observed["Slow"] == pytest.approx(439.0, abs=1e-9)
    assert observed["Base"] == pytest.approx(439.0, abs=1e-9)
    assert observed["High"] == pytest.approx(482.9, abs=1e-9)


def test_availability_is_not_a_capacity_table_and_covers_frozen_ids() -> None:
    path = ACCEPTED_RUNTIME / "generator_availability_hourly.parquet"
    schema_names = set(pq.ParquetFile(path).schema_arrow.names)
    assert "p_nom_MW" not in schema_names
    assert "capacity_MW" not in schema_names
    assert pq.ParquetFile(path).metadata.num_rows == 3 * 135 * 8760
    static = pd.read_csv(ROOT / "pre_pypsa_inputs" / "MEM_generators_static_final.csv")
    static = static.loc[static["year"].eq(2040)]
    runtime = pd.read_parquet(path, columns=["scenario", "generator_id"]).drop_duplicates()
    for scenario in ("Slow", "Base", "High"):
        expected = set(static.loc[static["scenario"].eq(scenario), "generator_id"])
        observed = set(runtime.loc[runtime["scenario"].eq(scenario), "generator_id"])
        assert observed == expected


def test_hydro_hourly_rows_are_complete_and_nonnegative() -> None:
    hydro = pd.read_parquet(ACCEPTED_RUNTIME / "hydro_inflow_hourly.parquet")
    assert len(hydro) == 3 * 24 * 8760
    assert hydro["inflow_MW_water_equivalent"].ge(0).all()
    assert hydro.groupby(["scenario", "hydro_id"])["snapshot"].nunique().eq(8760).all()
    assert not hydro["hydro_class"].eq("PURE_PHS").any()


def test_external_price_file_is_b10_byte_identical() -> None:
    source = ROOT / "stage_a_results" / "price_transfer" / "2040" / "external_prices_hourly.parquet"
    accepted = ACCEPTED_RUNTIME / "external_prices_hourly.parquet"
    assert sha256_file(accepted) == sha256_file(source)


def test_external_prices_are_inherited_across_scenarios() -> None:
    prices = pd.read_parquet(ACCEPTED_RUNTIME / "external_prices_hourly.parquet")
    pivot = prices.pivot(index=["snapshot", "external_market"], columns="scenario", values="price_EUR_per_MWh")
    assert pivot[["Slow", "Base", "High"]].nunique(axis=1).eq(1).all()
    assert set(prices["external_market"]) == {"FR", "CH", "AT", "SI", "ME", "GR", "MT", "TN"}
    assert "CORS" not in set(prices["external_market"])


def test_hash_receipt_proves_byte_identical_rebuilds() -> None:
    receipt = json.loads(HASH_PATH.read_text(encoding="utf-8"))
    assert receipt["status"] == "PASS"
    assert receipt["byte_identical_deterministic_rebuild"] is True
    assert receipt["rebuild_a"] == receipt["rebuild_b"] == receipt["accepted"]
    assert receipt["external_price_hash_identity"] == "PASS"


def test_reconciliation_has_only_allowed_pass_classifications() -> None:
    frame = pd.read_csv(RECONCILIATION_PATH)
    assert frame["status"].eq("PASS").all()
    assert set(frame["classification"]) <= {"EXACT_MATCH", "DETERMINISTIC_RUNTIME_TRANSFORMATION"}
    prohibited = {"UNRESOLVED", "CANDIDATE_ASSUMPTION", "IMPLEMENTATION_DRIFT", "SUPERSEDED_SOURCE", "MISSING_AUTHORITY"}
    assert not set(frame["classification"]) & prohibited


def test_final_gate_authorizes_only_2040_base() -> None:
    final = json.loads(FINAL_PATH.read_text(encoding="utf-8"))
    state = json.loads((ACCEPTED_RUNTIME / STATE_NAME).read_text(encoding="utf-8"))
    assert final["status"] == "PASS"
    assert final["gate"] == "STAGE_B_2040_BASE_READY_FOR_MANUAL_PRODUCTION_EXECUTION"
    assert final["production_optimization_executed"] is False
    assert final["canonical_reporting_pipeline_prepared"] is True
    assert final["canonical_reporting_production_figures_generated"] is False
    assert state["production_optimization_authorized"] is True
    assert state["authorized_scenarios"] == ["Base"]
    assert state["unauthorized_scenarios"] == ["Slow", "High"]
    assert state["authorized_mode"] == "FULL_YEAR_CANONICAL"
    assert state["authorized_solver"] == "gurobi"
    assert state["solver_options"] == {
        "Threads": 1,
        "Seed": 0,
        "include_objective_constant": False,
    }
    assert final["stage_b_2050"] == "LOCKED"


def test_qa_receipt_has_zero_failures() -> None:
    payload = json.loads(QA_PATH.read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    assert payload["fail_count"] == 0


def test_production_builder_has_no_candidate_fallback() -> None:
    text = (ROOT / "src" / "mem_model" / "network.py").read_text(encoding="utf-8")
    assert "runtime_assumptions_candidates.yaml" not in text
    text = (ROOT / "src" / "mem_model" / "build_network.py").read_text(encoding="utf-8")
    assert "runtime-dir" not in text


def test_missing_member_fails_closed(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_NAME).write_bytes((ACCEPTED_RUNTIME / MANIFEST_NAME).read_bytes())
    (tmp_path / STATE_NAME).write_bytes((ACCEPTED_RUNTIME / STATE_NAME).read_bytes())
    with pytest.raises(FileNotFoundError, match="member missing"):
        verify_accepted_runtime_manifest(tmp_path)


def test_hash_difference_fails_closed(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_NAME).write_bytes((ACCEPTED_RUNTIME / MANIFEST_NAME).read_bytes())
    (tmp_path / STATE_NAME).write_bytes((ACCEPTED_RUNTIME / STATE_NAME).read_bytes())
    (tmp_path / RUNTIME_MEMBERS[0]).write_bytes(b"not the accepted parquet")
    with pytest.raises(RuntimeError, match="hash differs"):
        verify_accepted_runtime_manifest(tmp_path)


def test_2050_verification_is_locked() -> None:
    with pytest.raises(RuntimeError, match="2050 is locked"):
        verify_accepted_runtime_manifest(ACCEPTED_RUNTIME, expected_year=2050)

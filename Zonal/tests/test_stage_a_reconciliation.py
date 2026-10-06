from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from mem_model.stage_a.reconciliation import (
    CLASSIFICATIONS,
    EXPORTED_MARKETS,
    RECON_COLUMNS,
    RECONCILIATION_PATH,
    TAIL_PATH,
    VERIFICATION_PATH,
    run_reconciliation,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def audit() -> dict:
    return run_reconciliation(write=False)


def test_reconciliation_passes_without_drift_or_ambiguity(audit: dict) -> None:
    frame = audit["reconciliation"]
    assert audit["status"] == "PASS"
    assert len(frame) == 963
    assert set(frame.columns) == set(RECON_COLUMNS)
    assert set(frame["classification"]) <= CLASSIFICATIONS
    assert not frame["classification"].isin(
        {
            "IMPLEMENTATION_DRIFT",
            "SUPERSEDED_ARTIFACT_LEAKAGE",
            "AMBIGUOUS_REQUIRES_USER",
        }
    ).any()
    assert frame["classification"].value_counts().to_dict() == {
        "INTENTIONAL_RUNTIME_TRANSFORMATION": 685,
        "EXACT_MATCH": 278,
    }


def test_canonical_outputs_equal_independent_read_only_rebuild(audit: dict) -> None:
    written = pd.read_csv(RECONCILIATION_PATH, dtype=str, keep_default_na=False)
    rebuilt = audit["reconciliation"].astype(str)
    pd.testing.assert_frame_equal(written, rebuilt, check_dtype=False)
    written_tail = pd.read_csv(TAIL_PATH)
    pd.testing.assert_frame_equal(written_tail, audit["tail"], check_dtype=False)


def test_all_material_families_and_horizons_are_covered(audit: dict) -> None:
    frame = audit["reconciliation"]
    assert set(frame["horizon"]) == {"2040", "2050"}
    assert {
        "DEMAND",
        "GENERATION_CAPACITY",
        "STORAGE",
        "HYDRO_PHS_RUNTIME",
        "OPERATING_ECONOMICS",
        "AVAILABILITY_TEMPORAL",
        "TOPOLOGY",
        "PRICE_SCARCITY_FORMATION",
        "PERIMETER_CLOSURE_POST_B6",
    } <= set(frame["input_family"])
    assert len(frame.loc[frame["input_family"].eq("DEMAND")]) == 20
    assert len(frame.loc[frame["input_family"].eq("GENERATION_CAPACITY")]) == 165
    assert len(frame.loc[frame["input_family"].eq("TOPOLOGY")]) == 48


def test_italy_and_external_static_values_reach_b6(audit: dict) -> None:
    frame = audit["reconciliation"]
    static = frame.loc[
        frame["input_family"].isin({"DEMAND", "GENERATION_CAPACITY", "STORAGE"})
    ]
    assert set(static["market"]) >= set(("AT", "CH", "FR", "GR", "HR", "IT", "ME", "MT", "SI", "TN"))
    assert not static["classification"].eq("IMPLEMENTATION_DRIFT").any()
    fr_2050 = static.loc[static["market"].eq("FR") & static["horizon"].eq("2050")]
    assert len(fr_2050) > 0
    assert not fr_2050["classification"].isin(
        {"IMPLEMENTATION_DRIFT", "AMBIGUOUS_REQUIRES_USER"}
    ).any()


def test_topology_and_malta_final_contract_are_exact(audit: dict) -> None:
    topology = audit["reconciliation"].loc[
        audit["reconciliation"]["input_family"].eq("TOPOLOGY")
    ]
    for year in (2040, 2050):
        horizon = topology.loc[topology["horizon"].eq(str(year))]
        assert len(horizon) == 24
        assert set(horizon["market"]) >= {"MT->IT", "IT->MT"}
        malta = horizon.loc[horizon["market"].isin({"MT->IT", "IT->MT"})]
        assert malta["intended_value"].astype(float).tolist() == [200.0, 200.0]
        assert malta["runtime_value"].astype(float).tolist() == [200.0, 200.0]
        assert malta["B6_observed_value"].astype(float).tolist() == [200.0, 200.0]
    assert topology["classification"].eq("INTENTIONAL_RUNTIME_TRANSFORMATION").all()


def test_price_exports_are_literal_nodal_duals_without_clipping(audit: dict) -> None:
    frame = audit["reconciliation"]
    extraction = frame.loc[frame["parameter"].str.endswith("nodal_marginal_price_extraction")]
    assert set(extraction["parameter"]) == {
        "B9B_nodal_marginal_price_extraction",
        "R10_nodal_marginal_price_extraction",
        "R5_nodal_marginal_price_extraction",
        "B9E_nodal_marginal_price_extraction",
    }
    assert extraction["classification"].eq("EXACT_MATCH").all()
    assert extraction["transformation"].str.contains("NO_CLIPPING_OR_CAPPING").all()


def test_price_tail_decomposition_uses_one_immutable_ordinary_mask(audit: dict) -> None:
    tail = audit["tail"]
    assert len(tail) == 4 * len(EXPORTED_MARKETS)
    assert set(tail["case"]) == {"B9B", "R10", "R5", "B9E"}
    assert set(tail["market"]) == set(EXPORTED_MARKETS)
    assert tail["ordinary_hour_count_B9B_mask"].nunique() == 1
    assert tail["ordinary_mask_definition"].nunique() == 1
    b9e = tail.loc[tail["case"].eq("B9E")].set_index("market")
    for market in ("FR", "ME", "MT", "TN"):
        assert b9e.at[market, "mean_EUR_per_MWh"] > b9e.at[
            market, "mean_excluding_gt_5000_hours"
        ]
        assert b9e.at[market, "VOLL_hours"] > 0
    assert b9e["maximum_EUR_per_MWh"].max() == pytest.approx(15000.0)


def test_frozen_inputs_and_historical_results_verify_unchanged(audit: dict) -> None:
    immutable = audit["verification"]["immutable_checks"]
    assert immutable["status"] == "PASS"
    assert immutable["final_master"]["status"] == "PASS"
    assert all(row["status"] == "PASS" for row in immutable["ETX7A_frozen_authority"])
    assert all(row["status"] == "PASS" for row in immutable["B1_to_B5_input_locks"])
    assert all(row["status"] == "PASS" for row in immutable["accepted_result_manifests"])
    assert all(not row["member_failures"] for row in immutable["accepted_result_manifests"])


def test_gate_impact_is_fail_closed_and_no_solve_was_run(audit: dict) -> None:
    impact = audit["impact"]
    assert impact["audit_conclusion"] == "FINAL_RESEARCH_INPUTS_FAITHFULLY_IMPLEMENTED"
    assert impact["material_upstream_runtime_change"] is False
    assert impact["revision_chain_required"] is False
    assert impact["next_2050_solve_authorized"] is False
    assert impact["B10"] == "LOCKED"
    assert impact["Stage_B"] == "LOCKED"
    assert impact["full_year_optimization_executed"] is False
    verification = json.loads(VERIFICATION_PATH.read_text(encoding="utf-8"))
    assert verification["original_research_PDFs_inspected"] is False
    assert verification["new_research_performed"] is False
    assert verification["optimization_executed"] is False
    assert verification["accepted_artifacts_modified"] is False

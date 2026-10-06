"""UC-1 non-solving contract and artifact regressions."""

from __future__ import annotations

import json
import math

import pandas as pd
import pytest
import pypsa

from mem_model import stage_b_uc1 as uc
from mem_model.common import ROOT, sha256_file


def test_uc_contract_and_biomass_normalization() -> None:
    cfg = uc.settings()
    bio = cfg["archetypes"]["BIOMASS_STEAM"]
    assert bio["source_startup_EUR2020_per_MW_start"] == 102.4
    assert bio["normalization_factor_2020_to_2025"] == 1.19360954008345
    assert math.isclose(bio["startup_EUR2025_per_MW_start"], 122.225616904545, abs_tol=1e-12)
    nuclear = cfg["archetypes"]["NUCLEAR_MODULAR"]
    assert nuclear["target_block_MW"] == 300
    assert nuclear["startup_EUR2025_per_MW_start"] == 250
    assert nuclear["custom_3h_dwell"] is False
    assert cfg["full_year_optimization_authorized"] is False


def test_prime_mover_scope_fails_closed() -> None:
    source = uc._static_source(2050, "Base")
    ccs = source.loc[source.carrier.eq("methane_ccs")]
    assert not ccs.empty and {uc._archetype(row) for _, row in ccs.iterrows()} == {"CCGT"}
    generic_bio = source.loc[source.carrier.eq("bioenergy")]
    assert not generic_bio.empty and all(uc._archetype(row) is None for _, row in generic_bio.iterrows())
    bad = ccs.iloc[0].copy()
    bad["solver_subtechnology"] = "METHANE_CCS_GENERIC"
    with pytest.raises(RuntimeError, match="UC1_BLOCKED_CCUS_PRIME_MOVER_MAPPING"):
        uc._archetype(bad)


def test_six_derivative_receipts_and_parent_hashes() -> None:
    receipt = json.loads((ROOT / "qa/stage_b/uc1_common_v1/MEM_UC1_DETERMINISTIC_BUILD_RECEIPT.json").read_text())
    assert receipt["status"] == "PASS" and receipt["deterministic_rebuild"] == "PASS"
    assert receipt["full_year_optimization_invocations"] == 0
    assert len(receipt["structural_packages"]) == 6
    for item in receipt["structural_packages"]:
        assert item["status"] == "PASS" and item["snapshots"] == 8760
        assert item["availability_compatibility"] == "PASS"
        assert item["p2x_equalities"] == 7
        assert sha256_file(ROOT / item["parent"]) == item["parent_sha256"]
        assert sha256_file(ROOT / item["derivative"]) == item["derivative_sha256"]


def test_nuclear_round_half_up_and_capacity_conservation() -> None:
    plan = pd.read_csv(ROOT / "qa/stage_b/uc1_common_v1/MEM_UC1_PARENT_CHILD_DECOMPOSITION.csv")
    assert not plan.child_id.duplicated().any()
    for (year, scenario, parent), block in plan.groupby(["year", "scenario", "parent_id"]):
        assert math.isclose(block.child_p_nom_MW.sum(), block.parent_p_nom_MW.iloc[0], abs_tol=1e-7)
        target = block.target_block_MW.iloc[0]
        assert len(block) == max(1, math.floor(block.parent_p_nom_MW.iloc[0] / target + 0.5))
        assert all(x.startswith(f"UC__{year}__{scenario}__{parent}__U") for x in block.child_id)
    nuclear = plan.loc[plan.uc_archetype.eq("NUCLEAR_MODULAR")]
    for scenario in ("Slow", "Base", "High"):
        rows = nuclear.loc[nuclear.scenario.eq(scenario)]
        assert len(rows) == 27
        assert len(rows.loc[rows.zone.eq("NORD")]) == 17
        assert len(rows.loc[rows.zone.eq("CSUD")]) == 10
        assert math.isclose(rows.child_p_nom_MW.sum(), 8000, abs_tol=1e-7)


def test_smoke_inputs_preserve_p2x_and_chronology() -> None:
    inputs = [pypsa.Network(uc._smoke_input(kind)) for kind in
              ("POST_P2X_CONTINUOUS_REFERENCE", "UC_MILP")]
    left, right = inputs
    assert len(left.snapshots) == len(right.snapshots) == 168
    assert left.snapshots.equals(right.snapshots)
    pd.testing.assert_frame_equal(uc._p2x_signature(left)[0], uc._p2x_signature(right)[0], check_dtype=False)
    pd.testing.assert_frame_equal(uc._p2x_signature(left)[1], uc._p2x_signature(right)[1], check_dtype=False)
    assert left.meta["uc1_smoke_p2x_rule"] == "SMOKE_HORIZON_PRORATED_P2X_ENERGY_ONLY"
    assert math.isclose(left.global_constraints.constant.sum(), 27.5e6 * 168 / 8760, abs_tol=1e-6)
    assert not left.generators.committable.astype(bool).any()
    assert right.generators.committable.astype(bool).sum() == 108


def test_smoke_solved_receipts_hashes_and_no_rerun() -> None:
    receipt = json.loads((uc._smoke_root() / "MEM_UC1_168H_RUN_RECEIPT.json").read_text())
    assert receipt["status"] == "UC1_COMMON_ARCHITECTURE_PASS__2040_BASE_SMOKE_PASS__FULL_YEAR_NOT_AUTHORIZED"
    assert receipt["smoke_solver_invocations"] == 3 and receipt["full_year_solver_invocations"] == 0
    assert receipt["reference"]["binary_variables"] == 0
    assert receipt["milp"]["binary_variables"] == 54432
    assert receipt["price_lp"]["binary_variables"] == 0
    assert receipt["price_lp"]["fixed_trajectories_equal_milp"] is True
    for kind in uc.SMOKE_KINDS:
        assert sha256_file(uc._smoke_solved(kind)) == next(
            receipt[key]["network_sha256"] for key in ("reference", "milp", "price_lp")
            if receipt[key]["kind"] == kind
        )
    assert sha256_file(ROOT / receipt["manifest"]) == receipt["manifest_sha256"]
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        uc.run_reference()


def test_smoke_diagnostic_tables() -> None:
    root = uc._smoke_root()
    balance = pd.read_csv(root / "MEM_UC1_168H_ZONAL_BALANCE.csv")
    prices = pd.read_csv(root / "MEM_UC1_168H_ZONAL_PRICE_STATISTICS.csv")
    water = pd.read_csv(root / "MEM_UC1_168H_HYDRO_WATER_BALANCE.csv")
    soc = pd.read_csv(root / "MEM_UC1_168H_STORAGE_SOC_QA.csv")
    events = pd.read_csv(root / "MEM_UC1_168H_COMMITTED_CAPACITY_BY_ARCHETYPE.csv")
    transitions = pd.read_csv(root / "MEM_UC1_168H_CANONICAL_COMMITMENT_TRANSITIONS.csv")
    fixed = json.loads((root / "MEM_UC1_168H_FIXED_COMMITMENT_RECONCILIATION.json").read_text())
    p2x = pd.read_csv(root / "MEM_UC1_168H_P2X_BY_ZONE.csv")
    assert len(balance) == 21 and balance.electrical_balance_residual_MWh.abs().max() < 1e-5
    assert balance.load_shedding_MWh.sum() == 0
    assert len(prices) == 14 and prices.hours_above_500_EUR_MWh.sum() == 0
    assert water.cyclic_water_balance_residual_MWh.abs().max() < 1e-5
    assert soc.cyclic_energy_residual_MWh.abs().max() < 1e-5
    assert (soc.min_e_MWh >= -1e-5).all() and (soc.max_e_MWh <= soc.e_nom_MWh + 1e-5).all()
    milp = events.loc[events.model.eq("UC_MILP")]
    assert milp.actual_start.sum() == 47
    assert milp.actual_shutdown.sum() == 94
    assert milp.native_shut_down_flags.sum() > milp.actual_shutdown.sum()
    assert milp.loc[milp.snapshot.eq(str(pd.Timestamp("2019-01-01"))),
                    "boundary_previous_status_source"].eq("PYPSA_UP_TIME_BEFORE").all()
    assert len(transitions) == 168 * 108
    assert (transitions.raw_start_up + 1e-7 >= transitions.actual_start).all()
    assert (transitions.raw_shut_down + 1e-7 >= transitions.actual_shutdown).all()
    assert fixed["status"] == "PASS" and fixed["fixed_commitment_price_zone_hours"] == 168 * 7
    assert fixed["price_lp_binary_variables"] == 0
    assert len(p2x) == 21 and p2x.energy_residual_MWh.abs().max() < 1e-5
    assert all(math.isclose(group.target_MWh.sum(), 27.5e6 * 168 / 8760, abs_tol=1e-6)
               for _, group in p2x.groupby("model"))

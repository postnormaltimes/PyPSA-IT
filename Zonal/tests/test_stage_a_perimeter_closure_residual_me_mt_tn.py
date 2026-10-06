from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

import mem_model.stage_a.perimeter_closure_residual_me_mt_tn as b9g_module
from mem_model.stage_a.execution import _load_accepted_production_price_sources
from mem_model.stage_a.perimeter_closure_r10 import residual_energy_capacity
from mem_model.stage_a.perimeter_closure_residual_me_mt_tn import (
    B9F_CAPACITIES_MW,
    B9F_GENERATOR_IDS,
    RESIDUAL_GENERATOR_IDS,
    RESIDUAL_MARKETS,
    build_parser,
    load_b9g_config,
    prepare_b9g,
    run_b9g,
    verify_b9g_inputs,
)
from mem_model.stage_a.receipts import sha256_file


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def config() -> dict:
    return load_b9g_config()


@pytest.fixture(scope="module")
def verified(config: dict) -> dict:
    return verify_b9g_inputs(config)


@pytest.fixture(scope="module")
def prepared() -> dict:
    return prepare_b9g(write_artifacts=False)


def test_B9F_receipt_manifest_and_members_are_immutable(verified: dict) -> None:
    phase = verified["phases"]["b9f_2050_hybrid_diagnostic"]
    assert phase["receipt_sha256"] == (
        "501A717D61D1E07AF811E46BBF1E9894C3712D508AE262853509F934B1A93412"
    )
    assert phase["manifest"]["sha256"] == (
        "DB802FBDB02B6DB69701A605FD5CA03F078161BCEFFF3B707B011FEC78AD79B0"
    )
    assert phase["manifest"]["member_count"] == 28
    assert phase["manifest"]["member_failures"] == 0
    assert phase["receipt"]["gate"] == (
        "ETX7B9F_2050_FR_R5_HYBRID_PLACEMENT_CLOSURE_COMPLETE"
    )
    assert phase["receipt"]["status"] == "PASS"


def test_B6_network_is_hash_immutable_before_and_after_preparation(
    config: dict, prepared: dict
) -> None:
    path = ROOT / config["accepted_inputs"]["b6_2050_unsolved_network"]["path"]
    expected = "06CC25DB50AD717AE27332A57168CB4DBFF38765703022CB05D3E5BA9943E4DE"
    assert sha256_file(path) == expected
    assert prepared["verification"]["b6_network_sha256"] == expected
    assert sha256_file(path) == expected


def test_exact_five_B9F_proxies_are_recreated_without_parameter_change(
    prepared: dict,
) -> None:
    contract = prepared["contract"].set_index("generator_id")
    network = prepared["network"]
    for market, generator_id in B9F_GENERATOR_IDS.items():
        assert generator_id in contract.index
        assert float(contract.at[generator_id, "p_nom_MW"]) == pytest.approx(
            B9F_CAPACITIES_MW[market], abs=0.0
        )
        observed = network.generators.loc[generator_id]
        assert observed.bus == market
        assert float(observed.p_nom) == pytest.approx(B9F_CAPACITIES_MW[market], abs=0.0)
        assert not bool(observed.p_nom_extendable)
        assert not bool(observed.committable)


def test_B9G_adds_exactly_three_separately_identified_generators(
    prepared: dict,
) -> None:
    contract = prepared["contract"]
    residual = contract.loc[contract["b9g_role"].eq("RESIDUAL_R10_INCREMENT")]
    assert len(contract) == 8
    assert len(residual) == 3
    assert set(residual["generator_id"]) == set(RESIDUAL_GENERATOR_IDS.values())
    assert set(residual["bus"]) == set(RESIDUAL_MARKETS)
    assert prepared["structural"]["B6_to_B9G_allowed_delta"] == {
        "carriers": 1,
        "generators": 8,
    }
    assert prepared["structural"]["B9F_to_B9G_allowed_delta"] == {"generators": 3}


def test_residual_support_scope_is_exactly_ME_MT_TN_and_nowhere_else(
    prepared: dict,
) -> None:
    residual = prepared["contract"].loc[
        prepared["contract"]["generator_id"].isin(RESIDUAL_GENERATOR_IDS.values())
    ]
    assert set(residual["bus"]) == {"ME", "MT", "TN"}
    assert set(residual["bus"]).isdisjoint({"FR", "CH", "AT", "SI", "GR", "IT", "HR"})
    assert prepared["structural"]["support_added_outside_ME_MT_TN"] is False


def test_residual_R10_capacities_satisfy_exact_energy_identities(
    prepared: dict,
) -> None:
    expected = {
        "ME": 94.82765688597348,
        "MT": 270.4231791609745,
        "TN": 1233.791813968554,
    }
    sizing = prepared["sizing"].set_index("market")
    for market, capacity in expected.items():
        independent = residual_energy_capacity(
            prepared["b9f"]["shedding"][market].to_numpy(dtype=float), 0.10
        )
        assert independent["capacity_MW"] == pytest.approx(capacity, abs=5e-9)
        assert independent["verified_residual_energy_share"] == pytest.approx(
            0.10, abs=1e-12
        )
        assert sizing.at[market, "residual_R10_increment_MW"] == pytest.approx(
            capacity, abs=5e-9
        )
        assert sizing.at[market, "R10_residual_energy_share"] == pytest.approx(
            0.10, abs=1e-12
        )


def test_TN_capacity_binding_precondition_is_physically_justified(
    prepared: dict,
) -> None:
    summary = prepared["tn_summary"]
    table = prepared["tn_table"]
    assert summary["existing_proxy_p_nom_MW"] == pytest.approx(
        2433.2947267101813, abs=0.0
    )
    assert summary["TN_shedding_hours"] == 278
    assert len(table) == 278
    assert summary["existing_proxy_maximum_headroom_during_TN_scarcity_MW"] == 0.0
    assert summary["fraction_TN_shedding_hours_existing_proxy_at_or_near_p_nom"] == 1.0
    assert summary["fraction_TN_shedding_MWh_existing_proxy_at_or_near_p_nom"] == 1.0
    assert summary["TN_shedding_hours_with_more_than_1_MW_existing_proxy_headroom"] == 0
    assert summary["capacity_binding_result"] == "JUSTIFIED"
    assert table["existing_TN_proxy_at_or_near_p_nom"].astype(bool).all()


def test_common_marginal_cost_and_fixed_continuous_formulation(prepared: dict) -> None:
    contract = prepared["contract"]
    assert contract["marginal_cost_EUR2025_per_MWh_el"].nunique() == 1
    assert float(contract["marginal_cost_EUR2025_per_MWh_el"].iloc[0]) == pytest.approx(
        243.122262790698, abs=1e-12
    )
    assert not contract["p_nom_extendable"].astype(bool).any()
    assert not contract["committable"].astype(bool).any()
    assert np.allclose(contract["p_min_pu"].astype(float), 0.0)
    assert np.allclose(contract["p_max_pu"].astype(float), 1.0)


def test_no_physical_topology_demand_or_profile_drift_and_zero_integer_variables(
    prepared: dict,
) -> None:
    structural = prepared["structural"]
    assert structural["status"] == "PASS"
    assert structural["physical_component_preservation"] == "EXACT"
    assert structural["topology_preservation"] == "EXACT"
    assert structural["demand_and_profile_preservation"] == "EXACT"
    assert prepared["LP"]["integer_variables"] == 0
    assert prepared["LP"]["binary_variables"] == 0
    assert prepared["LP"]["continuous_linear_program_only"] is True
    network = prepared["network"]
    assert not network.generators["p_nom_extendable"].fillna(False).astype(bool).any()
    assert not network.generators["committable"].fillna(False).astype(bool).any()
    assert not network.links["p_nom_extendable"].fillna(False).astype(bool).any()
    assert not network.links["committable"].fillna(False).astype(bool).any()
    assert not network.stores["e_nom_extendable"].fillna(False).astype(bool).any()


def test_prepare_does_not_invoke_gurobi_or_production_solve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        b9g_module,
        "gurobi_preflight",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Gurobi must not be invoked by prepare")
        ),
    )
    observed = prepare_b9g(write_artifacts=False)
    assert observed["payload"]["production_optimization_executed"] is False
    assert observed["payload"]["Gurobi_invoked"] is False
    args = build_parser().parse_args(["b9g"])
    assert args.execute is False
    with pytest.raises(SystemExit, match="B9G_NOT_EXECUTED"):
        run_b9g(args)


def test_preparation_artifacts_and_manifest_are_self_consistent() -> None:
    qa_dir = ROOT / "qa/stage_a/etx7b9g"
    qa = json.loads((qa_dir / "MEM_B9G_2050_Preparation_QA_v1.0.json").read_text())
    final = json.loads(
        (qa_dir / "MEM_B9G_2050_Preparation_Final_Verification_v1.0.json").read_text()
    )
    manifest_path = ROOT / final["preparation_manifest"]
    manifest = pd.read_csv(manifest_path)
    assert qa["status"] == final["status"] == "PASS"
    assert qa["production_optimization_executed"] is False
    assert qa["Gurobi_invoked"] is False
    assert final["preparation_manifest_sha256"] == sha256_file(manifest_path)
    assert final["preparation_manifest_members"] == len(manifest)
    # This historical preparation manifest included live gate metadata and its
    # own test file. Those four paths necessarily move as successors are
    # accepted; every frozen input, code and analytical member remains strict.
    mutable_admin = {
        "config/approval_gates.yaml",
        "config/stage_a_production_price_sources.yaml",
        "docs/MEM_STAGE_A_CURRENT_STATE.md",
        "tests/test_stage_a_perimeter_closure_residual_me_mt_tn.py",
    }
    immutable = manifest.loc[~manifest["relative_path"].isin(mutable_admin)]
    assert len(immutable) == len(manifest) - len(mutable_admin)
    assert all(
        sha256_file(ROOT / row.relative_path) == row.sha256
        for row in immutable.itertuples(index=False)
    )


def test_B9G_is_no_longer_executable_and_B10_2050_and_Stage_B_2050_remain_locked() -> None:
    sources = yaml.safe_load(
        (ROOT / "config/stage_a_production_price_sources.yaml").read_text(encoding="utf-8")
    )
    gates = yaml.safe_load((ROOT / "config/approval_gates.yaml").read_text(encoding="utf-8"))
    assert sources["sources"][2050]["phase"] == "ETX-7B9H"
    assert sources["sources"][2050]["status"] == "SOL_ACCEPTED_PENDING_PARENT_HASH_INTEGRATION"
    assert sources["sources"][2050]["method_role"] == (
        "SOL_SELECTED_2050_PRICE_SOURCE_NOT_YET_EXECUTABLE_IN_MASTER"
    )
    assert sources["immutable_diagnostics"]["2050_B9F"]["role"] == (
        "IMMUTABLE_DIAGNOSTIC_PREDECESSOR"
    )
    assert sources["governance"]["b10_authorized"] is False
    assert sources["governance"]["stage_b_authorized"] is False
    manual = gates["stage_a_manual_gates"]
    assert manual["b9f_authorized"] is False
    assert manual["b9g_authorized"] is False
    assert sources["immutable_diagnostics"]["2050_B9G"]["package_present_in_master"] is False
    assert manual["b10_authorized"] is False
    assert manual["stage_b_authorized"] is False
    with pytest.raises(RuntimeError, match="B10_NOT_AUTHORIZED"):
        _load_accepted_production_price_sources()

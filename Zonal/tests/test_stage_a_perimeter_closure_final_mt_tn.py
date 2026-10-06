from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

import mem_model.stage_a.perimeter_closure_final_mt_tn as b9h_module
from mem_model.stage_a.execution import _load_accepted_production_price_sources
from mem_model.stage_a.perimeter_closure_final_mt_tn import (
    NEW_GENERATOR_IDS,
    build_parser,
    load_b9h_config,
    prepare_b9h,
    run_b9h,
    verify_b9g_parent,
)
from mem_model.stage_a.perimeter_closure_r10 import residual_energy_capacity
from mem_model.stage_a.receipts import sha256_file


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def config() -> dict:
    return load_b9h_config()


@pytest.fixture(scope="module")
def verified(config: dict) -> dict:
    return verify_b9g_parent(config)


@pytest.fixture(scope="module")
def prepared() -> dict:
    return prepare_b9h(write_artifacts=False)


def test_B9G_receipt_manifest_solved_network_and_members_are_immutable(
    verified: dict,
) -> None:
    assert verified["receipt_sha256"] == (
        "384A79C03289E0D612FAE1568B3271D9BB15F00603F4719A4A2AA1C5337276CF"
    )
    assert verified["manifest"]["sha256"] == (
        "FCBD33F69A716D34FE898EC3BB478B24E7AE3E67D19A333DD9F3EC4FEB91BB9C"
    )
    assert verified["solved_network_sha256"] == (
        "C24AD78015BD203A1AE7C07DD6EDCA90321BCA73556193C83DC7371A6D6D2011"
    )
    assert verified["contract_sha256"] == (
        "116F9DA82F7424276F5A979F8729F02F812F7EE01C075F9BFC7B0BB746AB6032"
    )
    assert verified["manifest"]["member_failures"] == 0
    assert verified["receipt"]["status"] == "PASS"
    assert verified["receipt"]["qa"]["technical_diagnostic_status"] == (
        "TECHNICAL_B9G_DIAGNOSTIC_PASS"
    )


def test_B9G_residual_scarcity_and_same_R10_R5_method_are_reproduced(
    prepared: dict,
) -> None:
    sizing = prepared["sizing"].set_index("market")
    expected = {
        "MT": {
            "MWh": 1229.9312699569612,
            "hours": 28,
            "peak": 91.16561159297117,
            "R10": 56.69953655144692,
            "R5": 66.33106493317653,
        },
        "TN": {
            "MWh": 12665.831063532454,
            "hours": 28,
            "peak": 1321.5158568310999,
            "R10": 803.6744852849702,
            "R5": 956.5138693932932,
        },
    }
    for market, values in expected.items():
        source = prepared["parent_data"]["shedding"][market]
        r10 = residual_energy_capacity(source.to_numpy(dtype=float), 0.10)
        r5 = residual_energy_capacity(source.to_numpy(dtype=float), 0.05)
        assert sizing.at[market, "B9G_residual_shedding_MWh"] == pytest.approx(
            values["MWh"], abs=1e-9
        )
        assert int(sizing.at[market, "B9G_shedding_hours_above_1e_6_MW"]) == (
            values["hours"]
        )
        assert sizing.at[market, "B9G_peak_residual_shedding_MW"] == pytest.approx(
            values["peak"], abs=1e-9
        )
        assert r10["capacity_MW"] == pytest.approx(values["R10"], abs=5e-9)
        assert r5["capacity_MW"] == pytest.approx(values["R5"], abs=5e-9)
        assert r10["verified_residual_energy_share"] == pytest.approx(
            0.10, abs=1e-12
        )
        assert r5["verified_residual_energy_share"] == pytest.approx(
            0.05, abs=1e-12
        )


def test_capacity_selection_is_additive_physical_and_not_price_fitted(
    prepared: dict,
) -> None:
    sizing = prepared["sizing"].set_index("market")
    assert prepared["increments"]["MT"] == pytest.approx(
        90.73324497366548, abs=5e-9
    )
    assert prepared["increments"]["TN"] == pytest.approx(
        803.6744852849702, abs=5e-9
    )
    assert sizing.at["MT", "selected_rule"] == (
        "CONDITIONAL_POSITIVE_SHEDDING_P99"
    )
    assert sizing.at["TN", "selected_rule"] == "RESIDUAL_R10"
    assert sizing.at["MT", "selected_residual_energy_share"] < 0.001
    assert sizing.at["TN", "selected_residual_energy_share"] == pytest.approx(
        0.10, abs=1e-12
    )
    assert not prepared["selection"]["price_target_used_for_sizing"].astype(bool).any()
    assert not prepared["selection"]["manual_tuning"].astype(bool).any()
    assert prepared["selection"]["additive_to_B9G"].astype(bool).all()


def test_capacity_totals_preserve_B9G_and_add_only_MT_TN(prepared: dict) -> None:
    control = prepared["capacity_control"]
    assert control["B9G_total_virtual_capacity_MW"] == pytest.approx(
        60077.33885051766, abs=5e-9
    )
    assert control["MT_B9G_existing_virtual_capacity_MW"] == pytest.approx(
        270.4231791609745, abs=0.0
    )
    assert control["MT_B9H_final_virtual_capacity_MW"] == pytest.approx(
        361.15642413464, abs=5e-9
    )
    assert control["TN_existing_original_proxy_MW"] == pytest.approx(
        2433.2947267101813, abs=0.0
    )
    assert control["TN_B9G_residual_addition_MW"] == pytest.approx(
        1233.791813968554, abs=0.0
    )
    assert control["TN_B9H_final_virtual_capacity_MW"] == pytest.approx(
        4470.761025963705, abs=5e-9
    )
    assert control["B9H_total_virtual_capacity_MW"] == pytest.approx(
        60971.74658077629, abs=5e-9
    )


def test_contract_and_network_add_exactly_two_generators_and_no_ME(
    prepared: dict,
) -> None:
    contract = prepared["contract"]
    new = contract.loc[contract["b9h_role"].eq("FINAL_INCREMENTAL_CAPACITY")]
    assert len(contract) == 10
    assert len(new) == 2
    assert set(new["generator_id"]) == set(NEW_GENERATOR_IDS.values())
    assert set(new["bus"]) == {"MT", "TN"}
    assert "ME" not in set(new["bus"])
    assert prepared["structural"]["B9G_to_B9H_allowed_delta"] == {
        "generators": 2
    }
    assert prepared["structural"]["ME_increment_added"] is False
    assert prepared["structural"]["support_added_outside_MT_TN"] is False


def test_common_cost_fixed_formulation_and_no_UC_or_expansion(
    prepared: dict,
) -> None:
    new = prepared["contract"].loc[
        prepared["contract"]["generator_id"].isin(NEW_GENERATOR_IDS.values())
    ]
    assert np.allclose(
        new["marginal_cost_EUR2025_per_MWh_el"].astype(float),
        243.122262790698,
        atol=1e-12,
        rtol=0.0,
    )
    assert not new["p_nom_extendable"].astype(bool).any()
    assert not new["committable"].astype(bool).any()
    assert np.allclose(new["p_min_pu"].astype(float), 0.0)
    assert np.allclose(new["p_max_pu"].astype(float), 1.0)
    assert np.allclose(new["efficiency"].astype(float), 1.0)
    assert prepared["LP"]["integer_variables"] == 0
    assert prepared["LP"]["binary_variables"] == 0
    assert prepared["LP"]["continuous_linear_program_only"] is True


def test_no_physical_topology_demand_profile_external_fleet_or_cost_drift(
    prepared: dict,
) -> None:
    structural = prepared["structural"]
    assert structural["status"] == "PASS"
    for key in (
        "carrier_preservation",
        "physical_component_preservation",
        "topology_preservation",
        "demand_and_profile_preservation",
        "external_fleet_preservation",
        "fuel_and_CO2_preservation",
    ):
        assert structural[key] == "EXACT"
    parent_hash = sha256_file(prepared["verification"]["solved_network_path"])
    assert parent_hash == prepared["verification"]["solved_network_sha256"]


def test_prepare_never_invokes_Gurobi_or_a_production_solve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        b9h_module,
        "gurobi_preflight",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Gurobi must not be invoked by prepare")
        ),
    )
    observed = prepare_b9h(write_artifacts=False)
    assert observed["payload"]["production_optimization_executed"] is False
    assert observed["payload"]["Gurobi_optimizer_invoked"] is False
    args = build_parser().parse_args(["b9h"])
    assert args.execute is False
    with pytest.raises(SystemExit, match="B9H_NOT_EXECUTED"):
        run_b9h(args)


def test_preparation_interface_context_reconciles_local_scarcity(
    prepared: dict,
) -> None:
    context = prepared["interfaces"].set_index("market")
    assert int(context.at["MT", "B9G_local_scarcity_hours"]) == 28
    assert (
        int(
            context.at[
                "MT",
                "B9G_local_scarcity_hours_any_incoming_interface_saturated",
            ]
        )
        == 0
    )
    assert (
        int(
            context.at[
                "MT",
                "B9G_local_scarcity_hours_any_outgoing_interface_saturated",
            ]
        )
        == 28
    )
    assert int(context.at["TN", "B9G_local_scarcity_hours"]) == 28
    assert (
        int(
            context.at[
                "TN",
                "B9G_local_scarcity_hours_any_incoming_interface_saturated",
            ]
        )
        == 17
    )


def test_B10_and_Stage_B_remain_fail_closed_without_B9I() -> None:
    sources = yaml.safe_load(
        (ROOT / "config/stage_a_production_price_sources.yaml").read_text(
            encoding="utf-8"
        )
    )
    gates = yaml.safe_load(
        (ROOT / "config/approval_gates.yaml").read_text(encoding="utf-8")
    )
    assert sources["governance"]["b10_authorized"] is False
    assert sources["governance"]["stage_b_authorized"] is False
    assert gates["stage_a_manual_gates"]["b10_authorized"] is False
    assert gates["stage_a_manual_gates"]["stage_b_authorized"] is False
    assert load_b9h_config()["governance"]["B9I_authorized"] is False
    with pytest.raises(RuntimeError, match="B10_NOT_AUTHORIZED"):
        _load_accepted_production_price_sources()


def test_completed_receipt_is_self_consistent_when_present() -> None:
    path = ROOT / "qa/stage_a/etx7b9h/MEM_ETX7B9H_Run_Receipt_v1.0.json"
    if not path.exists():
        pytest.skip("B9H has not yet been executed")
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if receipt.get("status") != "PASS":
        pytest.skip("B9H PASS receipt is not yet available")
    assert receipt["gate"] == (
        "ETX7B9H_2050_FINAL_RESIDUAL_MT_TN_CLOSURE_COMPLETE"
    )
    assert receipt["solver_status"] == "ok"
    assert receipt["termination_condition"] == "optimal"
    assert receipt["snapshots"] == 8760
    assert receipt["accepted"] is True
    assert receipt["qa"]["technical_diagnostic_status"] == (
        "TECHNICAL_B9H_DIAGNOSTIC_PASS"
    )
    manifest = ROOT / receipt["outputs"]["manifest"]
    assert sha256_file(manifest) == receipt["outputs"]["manifest_sha256"]
    rows = pd.read_csv(manifest)
    assert all(
        sha256_file(ROOT / row.relative_path) == row.sha256
        for row in rows.itertuples(index=False)
    )

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa
import pytest
import yaml

from mem_model.stage_a.execution import (
    PRICE_MAPPING,
    _build_stage_b_price_adapter,
    _load_accepted_production_price_sources,
)
from mem_model.stage_a.perimeter_closure import create_and_validate_linopy_model
from mem_model.stage_a.perimeter_closure_r10 import (
    CANDIDATE_MARKETS,
    VIRTUAL_CARRIER,
    _directional_limit_for_year,
    add_r10_virtual_supply,
    build_contract,
    build_parser,
    derive_horizon_capacities,
    load_r10_config,
    recover_cost_proxy,
    residual_energy_capacity,
    run_horizon,
    validate_r10_structural_delta,
    verify_immutable_history,
)
from mem_model.stage_a.receipts import sha256_file


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def r10_config() -> dict:
    return load_r10_config()


@pytest.fixture(scope="module")
def immutable_history(r10_config: dict) -> dict:
    return verify_immutable_history(r10_config)


@pytest.fixture(scope="module")
def horizon_contracts(r10_config: dict, immutable_history: dict) -> dict[int, pd.DataFrame]:
    return {
        year: build_contract(year, r10_config, immutable_history)[0]
        for year in (2040, 2050)
    }


def test_exact_piecewise_capacity_algorithm_and_mechanical_identity() -> None:
    series = np.array([10.0, 5.0, 1.0, 0.0])
    result = residual_energy_capacity(series, 0.10)
    assert result["capacity_MW"] == pytest.approx(8.4, abs=1e-12)
    assert np.maximum(series - result["capacity_MW"], 0.0).sum() == pytest.approx(
        0.10 * series.sum(), abs=1e-12
    )
    assert result["verified_residual_energy_share"] == pytest.approx(0.10, abs=1e-12)


def test_all_zero_shedding_returns_zero_capacity() -> None:
    result = residual_energy_capacity(np.zeros(8760), 0.10)
    assert result["capacity_MW"] == 0.0
    assert result["baseline_shedding_MWh"] == 0.0
    assert result["verified_residual_MWh"] == 0.0


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (2040, {"FR": 12894.294620664465, "GR": 2221.0046298019824, "TN": 804.0075525605123}),
        (2050, {"FR": 34350.14308951057, "GR": 6905.030474826701, "TN": 2433.2947267101813}),
    ],
)
def test_exact_horizon_r10_capacities_from_immutable_baselines(
    year: int,
    expected: dict[str, float],
    r10_config: dict,
    immutable_history: dict,
) -> None:
    capacities, shedding = derive_horizon_capacities(year, r10_config, immutable_history)
    assert tuple(capacities["market"]) == CANDIDATE_MARKETS
    assert len(shedding) == 8760
    for row in capacities.itertuples(index=False):
        assert row.derived_virtual_capacity_MW == pytest.approx(expected[row.market], abs=5e-9)
        assert row.mechanical_residual_energy_share == pytest.approx(0.10, abs=1e-12)
        positive_average = shedding[row.market].loc[shedding[row.market].gt(0.0)].mean()
        assert row.derived_virtual_capacity_MW != pytest.approx(row.baseline_peak_shedding_MW)
        assert row.derived_virtual_capacity_MW != pytest.approx(positive_average)


def test_horizon_specific_frozen_cost_recovery(r10_config: dict) -> None:
    cost_2040 = recover_cost_proxy(2040, r10_config)
    cost_2050 = recover_cost_proxy(2050, r10_config)
    assert cost_2040["marginal_cost_EUR2025_per_MWh_el"] == pytest.approx(
        145.909492380952, abs=1e-12
    )
    assert cost_2050["source_asset_id"] == "ETX7B2_IT_2050_METHANE_GT_OCGT_NON_CHP_0AA7D5D9AF"
    assert cost_2050["efficiency_el"] == 0.43
    assert cost_2050["fuel_price_EUR2025_per_MWh_th"] == 22.7578
    assert cost_2050["CO2_price_EUR2025_per_t"] == 400.0
    assert cost_2050["chargeable_CO2_t_per_MWh_th"] == 0.198
    assert cost_2050["VOM_EUR2025_per_MWh_el"] == 6.0111
    assert cost_2050["marginal_cost_EUR2025_per_MWh_el"] == pytest.approx(
        243.122262790698, abs=1e-12
    )


def test_interface_diagnostic_uses_horizon_specific_directional_rows() -> None:
    directional = pd.read_csv(
        ROOT / "stage_a_inputs/topology_v1_0/MEM_ETX7B5_Directional_Link_Registry_v1.0.csv"
    )
    for row in directional.itertuples(index=False):
        observed = _directional_limit_for_year(
            directional,
            row.physical_link_id,
            row.from_market,
            row.to_market,
            int(row.year),
        )
        assert observed == float(row.capacity_MW)


@pytest.mark.parametrize("year", [2040, 2050])
def test_exact_structural_delta_scope_and_continuous_lp(
    year: int,
    r10_config: dict,
    immutable_history: dict,
    horizon_contracts: dict[int, pd.DataFrame],
) -> None:
    baseline = pypsa.Network(immutable_history["networks"][year]["path"])
    baseline.set_snapshots(baseline.snapshots[:6])
    contract = horizon_contracts[year]
    closure = add_r10_virtual_supply(baseline, contract, year)
    structural = validate_r10_structural_delta(baseline, closure, contract, year)
    lp = create_and_validate_linopy_model(closure)
    assert structural["status"] == "PASS"
    assert structural["R10_counts"]["carriers"] == structural["baseline_counts"]["carriers"] + 1
    assert structural["R10_counts"]["generators"] == structural["baseline_counts"]["generators"] + 3
    assert set(contract["bus"]) == {"FR", "GR", "TN"}
    assert set(contract["bus"]).isdisjoint({"IT", "AT", "CH", "SI", "HR", "ME", "MT"})
    assert set(closure.generators.loc[contract["generator_id"], "carrier"]) == {VIRTUAL_CARRIER}
    assert not closure.generators["p_nom_extendable"].fillna(False).astype(bool).any()
    assert not closure.generators["committable"].fillna(False).astype(bool).any()
    assert lp["integer_variables"] == 0
    assert lp["binary_variables"] == 0


def test_immutable_history_receipts_manifests_and_members_pass(
    r10_config: dict,
    immutable_history: dict,
) -> None:
    assert immutable_history["status"] == "PASS"
    assert len(immutable_history["input_locks"]) == 7
    assert sum(row["member_failures"] for row in immutable_history["input_locks"]) == 0
    for item in immutable_history["history"].values():
        assert item["manifest"]["member_failures"] == 0
        assert item["receipt_sha256"] == sha256_file(item["receipt_path"])
    assert r10_config["accepted_history"]["b8c_s2_max_closure"]["production_price_source"] is False


def test_sensitivity_and_s2_audit_artifacts_are_complete() -> None:
    sensitivity = pd.read_csv(
        ROOT / "qa/stage_a/perimeter_closure/MEM_Perimeter_Closure_Residual_Energy_Sensitivity_v1.0.csv"
    )
    assert len(sensitivity) == 24
    assert set(sensitivity["horizon"]) == {2040, 2050}
    assert set(sensitivity["residual_energy_share"]) == {0.15, 0.10, 0.075, 0.05}
    assert set(sensitivity["market"]) == {"FR", "GR", "TN"}
    audit = pd.read_csv(
        ROOT / "qa/stage_a/etx7b8c/MEM_ETX7B8C_S2_Diagnostic_Audit_Manifest_v1.0.csv"
    )
    assert len(audit) >= 11
    assert set(audit["methodological_role"]) == {"ACCEPTED_DIAGNOSTIC_MAX_CLOSURE_EXPERIMENT"}
    assert not audit["production_price_source"].astype(bool).any()
    assert all(sha256_file(ROOT / row.relative_path) == row.sha256 for row in audit.itertuples())
    manifest_path = (
        ROOT
        / "qa/stage_a/perimeter_closure/MEM_R10_Perimeter_Closure_Preparation_Manifest_v1.0.csv"
    )
    final = json.loads(
        (
            ROOT
            / "qa/stage_a/perimeter_closure/MEM_R10_Perimeter_Closure_Preparation_Final_Verification_v1.0.json"
        ).read_text(encoding="utf-8")
    )
    manifest = pd.read_csv(manifest_path)
    assert final["status"] == "PASS"
    assert final["preparation_manifest_sha256"] == sha256_file(manifest_path)
    assert final["preparation_manifest_members"] == len(manifest)
    assert final["production_optimizations_executed"] is False
    assert final["B10_authorized"] is False
    assert final["Stage_B_authorized"] is False
    # The preparation manifest is a historical snapshot. Later accepted gate
    # promotion legitimately evolves these live governance/code surfaces;
    # every other preparation member remains byte-identical.
    evolving = {
            "config/approval_gates.yaml",
            "config/stage_a_production_price_sources.yaml",
            "config/stage_b_price_input_contract.yaml",
        "docs/MEM_STAGE_A_CURRENT_STATE.md",
        "docs/runbooks/ETX7B10_RUNBOOK.md",
        "src/mem_model/stage_a/execution.py",
    }
    immutable = manifest.loc[~manifest["relative_path"].isin(evolving)]
    assert all(
        sha256_file(ROOT / row.relative_path) == row.sha256
        for row in immutable.itertuples()
    )


def test_historical_r10_commands_are_guarded_and_results_are_immutable(r10_config: dict) -> None:
    parser = build_parser()
    for command, year in (("b8d", 2040), ("b9c", 2050)):
        args = parser.parse_args([command])
        assert args.execute is False
        with pytest.raises(SystemExit, match="R10_NOT_EXECUTED"):
            run_horizon(year, args)
        horizon = r10_config["horizons"][year]
        assert horizon["command"].endswith(f" {command} --execute")
        assert (ROOT / horizon["receipt"]).exists()
        assert (ROOT / horizon["result_directory"]).exists()
    assert sha256_file(ROOT / r10_config["horizons"][2040]["receipt"]) == (
        "C69ACBF941261BD37426EDCEDB1C14482977C2059427FEA1968008492E05F8C0"
    )
    assert sha256_file(ROOT / r10_config["horizons"][2050]["receipt"]) == (
        "1D741BD14C15E19443C04CABBC9E1A151771D0A84EEAE225F75B8A1247E758D1"
    )


def test_b10_and_stage_b_fail_closed_pending_explicit_acceptance() -> None:
    prices = yaml.safe_load(
        (ROOT / "config/stage_a_production_price_sources.yaml").read_text(encoding="utf-8")
    )
    gates = yaml.safe_load((ROOT / "config/approval_gates.yaml").read_text(encoding="utf-8"))
    stage_b = yaml.safe_load(
        (ROOT / "config/stage_b_price_input_contract.yaml").read_text(encoding="utf-8")
    )
    assert prices["governance"]["production_sources_resolved"] is False
    assert prices["governance"]["b10_authorized"] is False
    assert prices["governance"]["stage_b_authorized"] is False
    assert prices["governance"]["automatic_acceptance"] is False
    assert prices["governance"]["acceptance_action_required"] == (
        "INTEGRATE_AND_VERIFY_COMPLETED_B9G_PARENT_PACKAGE_THEN_FREEZE_B9H_IN_MASTER"
    )
    assert prices["governance"]["fallback_allowed"] is False
    assert prices["sources"][2040]["status"] == "ACCEPTED_PRODUCTION_PRICE_SOURCE"
    assert prices["sources"][2040]["receipt_sha256"] == (
        "C69ACBF941261BD37426EDCEDB1C14482977C2059427FEA1968008492E05F8C0"
    )
    assert prices["sources"][2050]["status"] == "SOL_ACCEPTED_PENDING_PARENT_HASH_INTEGRATION"
    assert prices["sources"][2050]["phase"] == "ETX-7B9H"
    assert all(len(prices["sources"][2050][key]) == 64 for key in (
        "receipt_sha256", "result_manifest_sha256", "market_prices_sha256"
    ))
    assert gates["stage_a_manual_gates"]["b10_authorized"] is False
    assert gates["stage_a_manual_gates"]["stage_b_authorized"] is False
    assert stage_b["governance"]["b10_authorized"] is False
    assert stage_b["governance"]["stage_b_authorized"] is False
    assert stage_b["mapping"] == {
        "FR": "NORD", "CH": "NORD", "AT": "NORD", "SI": "NORD",
        "ME": "CSUD", "GR": "SUD", "MT": "SICI", "TN": "SICI",
    }
    assert stage_b["corsica"]["price_series_required"] is False
    assert stage_b["stage_a_fleet_transfer"] is False
    with pytest.raises(RuntimeError, match="B10_NOT_AUTHORIZED"):
        _load_accepted_production_price_sources()


def test_future_b10_stage_b_adapter_reuses_base_prices_without_fleet_transfer() -> None:
    snapshots = pd.date_range("2019-01-01", periods=8760, freq="h")
    rows = []
    for year in (2040, 2050):
        for market_index, market in enumerate(PRICE_MAPPING):
            rows.append(
                pd.DataFrame(
                    {
                        "snapshot": snapshots,
                        "horizon": year,
                        "external_market": market,
                        "stage_a_marginal_price": float(year + market_index),
                    }
                )
            )
    adapter = _build_stage_b_price_adapter(pd.concat(rows, ignore_index=True))
    assert list(adapter.columns) == [
        "snapshot", "year", "scenario", "external_market", "price_EUR_per_MWh"
    ]
    assert len(adapter) == 2 * 3 * 8760 * 8
    assert set(adapter["scenario"]) == {"Slow", "Base", "High"}
    assert set(adapter["external_market"]) == set(PRICE_MAPPING)
    assert "CORS" not in set(adapter["external_market"])
    assert not any("fleet" in column.casefold() for column in adapter.columns)
    for (year, market), group in adapter.groupby(["year", "external_market"]):
        assert group.groupby("scenario")["price_EUR_per_MWh"].first().nunique() == 1


def test_v1_is_superseded_and_only_v2_is_active() -> None:
    v1 = yaml.safe_load(
        (ROOT / "config/stage_a_perimeter_closure_method_v1_0.yaml").read_text(encoding="utf-8")
    )
    v2 = yaml.safe_load(
        (ROOT / "config/stage_a_perimeter_closure_method_v2_0.yaml").read_text(encoding="utf-8")
    )
    assert v1["status"] == "SUPERSEDED_UNACCEPTED_DRAFT"
    assert v1["authoritative_for_execution"] is False
    assert v2["schema"] == "PERIMETER_CLOSURE_METHOD_V2_0"
    assert v2["method"]["capacity_sizing_rule"] == "BASELINE_SHEDDING_ENERGY_EXCEEDANCE"
    assert v2["method"]["automatic_capacity_iteration"] is False
    assert v2["method"]["automatic_method_acceptance"] is False

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa
import pytest
import yaml

import mem_model.stage_a.perimeter_closure_placement as placement_module
from mem_model.stage_a.execution import _load_accepted_production_price_sources
from mem_model.stage_a.network import MARKETS
from mem_model.stage_a.perimeter_closure_r10 import residual_energy_capacity
from mem_model.stage_a.perimeter_closure_placement import (
    EXPORTED_MARKETS,
    PLACEMENT_GENERATOR_IDS,
    PROXY_MARKETS,
    RESIDUAL_ENERGY_SHARE,
    _case_paths,
    _case_tables,
    _exported_market_diagnostics,
    _near_constant_comparison,
    _ordinary_hour_comparisons,
    _ordinary_hour_mask,
    _placement_delivery_diagnostics,
    _reachable,
    build_parser,
    load_placement_config,
    prepare_placement,
    run_b9e,
    verify_placement_inputs,
)
from mem_model.stage_a.receipts import sha256_file


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def placement_config() -> dict:
    return load_placement_config()


@pytest.fixture(scope="module")
def verified(placement_config: dict) -> dict:
    return verify_placement_inputs(placement_config)


@pytest.fixture(scope="module")
def prepared() -> dict:
    return prepare_placement(write_artifacts=False)


def test_AT_SI_capacities_derive_from_immutable_B9C_R10(
    prepared: dict,
    verified: dict,
) -> None:
    expected = {"AT": 5506.614447347679, "SI": 3017.094676349047}
    receipt = verified["accepted"]["b9c_2050_R10_diagnostic"]["receipt"]
    paths = _case_paths(receipt)
    network = pypsa.Network(paths["solved"])
    shedding = _case_tables(network, paths)["shedding"]
    derivation = prepared["derivation"].set_index("market")
    for market, expected_capacity in expected.items():
        independently_derived = residual_energy_capacity(
            shedding[market].to_numpy(dtype=float), RESIDUAL_ENERGY_SHARE
        )
        assert independently_derived["capacity_MW"] == pytest.approx(
            expected_capacity, abs=5e-9
        )
        assert independently_derived["verified_residual_energy_share"] == pytest.approx(
            0.10, abs=1e-12
        )
        assert derivation.at[market, "source_case"] == "ETX7B9C_2050_R10"
        assert derivation.at[market, "derived_virtual_capacity_MW"] == pytest.approx(
            expected_capacity, abs=5e-9
        )


def test_R10_quantities_retained_and_near_constant_total(prepared: dict) -> None:
    contract = prepared["contract"].set_index("bus")
    expected = {
        "FR": 34350.14308951057,
        "GR": 6905.030474826701,
        "TN": 2433.2947267101813,
        "AT": 5506.614447347679,
        "SI": 3017.094676349047,
    }
    assert tuple(prepared["contract"]["bus"]) == PROXY_MARKETS
    for market, capacity in expected.items():
        assert float(contract.at[market, "p_nom_MW"]) == pytest.approx(capacity, abs=5e-9)
    control = prepared["capacity_control"]
    assert control["placement_total_virtual_capacity_MW"] == pytest.approx(
        52212.17741474419, abs=1e-9
    )
    assert control["R5_total_virtual_capacity_MW"] == pytest.approx(
        51912.004881670524, abs=1e-9
    )
    assert control["absolute_difference_MW"] == pytest.approx(
        300.1725330736663, abs=1e-9
    )
    assert control["percentage_difference"] == pytest.approx(
        0.5782333657848253, abs=1e-12
    )
    assert control["exact_total_rescaling_applied"] is False


def test_proxy_scope_and_structural_delta_are_exact(prepared: dict) -> None:
    contract = prepared["contract"]
    assert set(contract["generator_id"]) == set(PLACEMENT_GENERATOR_IDS.values())
    assert set(contract["bus"]) == {"FR", "GR", "TN", "AT", "SI"}
    assert set(contract["bus"]).isdisjoint({"IT", "CH", "HR", "ME", "MT"})
    structural = prepared["structural"]
    assert structural["status"] == "PASS"
    assert structural["allowed_delta"] == {"carriers": 1, "generators": 5}
    assert structural["placement_counts"]["carriers"] == structural["baseline_counts"]["carriers"] + 1
    assert structural["placement_counts"]["generators"] == structural["baseline_counts"]["generators"] + 5
    assert structural["baseline_component_preservation"] == "EXACT"
    assert prepared["LP"]["integer_variables"] == 0
    assert prepared["LP"]["binary_variables"] == 0
    assert prepared["LP"]["continuous_linear_program_only"] is True
    assert not prepared["network"].generators["p_nom_extendable"].fillna(False).astype(bool).any()
    assert not prepared["network"].generators["committable"].fillna(False).astype(bool).any()
    assert not prepared["network"].links["p_nom_extendable"].fillna(False).astype(bool).any()
    assert not prepared["network"].links["committable"].fillna(False).astype(bool).any()
    assert not prepared["network"].stores["e_nom_extendable"].fillna(False).astype(bool).any()


def test_frozen_cost_is_common_to_all_five_proxies(prepared: dict) -> None:
    contract = prepared["contract"]
    assert contract["marginal_cost_EUR2025_per_MWh_el"].nunique() == 1
    assert float(contract["marginal_cost_EUR2025_per_MWh_el"].iloc[0]) == pytest.approx(
        243.122262790698, abs=1e-12
    )
    assert prepared["cost"]["source_asset_id"] == (
        "ETX7B2_IT_2050_METHANE_GT_OCGT_NON_CHP_0AA7D5D9AF"
    )


def test_B8D_B9B_B9C_B9D_are_immutable(verified: dict) -> None:
    expected = {
        "b8d_2040_production_source": (
            "C69ACBF941261BD37426EDCEDB1C14482977C2059427FEA1968008492E05F8C0",
            "88C259493E5D5B8E4B4B2B3C535BCDB623924474FB23DD860D2B8673FE08DA2D",
        ),
        "b9c_2050_R10_diagnostic": (
            "1D741BD14C15E19443C04CABBC9E1A151771D0A84EEAE225F75B8A1247E758D1",
            "040272788BBC3CA4F880B50A58B6181CB970CA1F5C4B486D366B98D373922A4F",
        ),
        "b9d_2050_R5_diagnostic": (
            "E032C63879BF294C99759C8097A7AA7BADB9F5477CA20E8992B07012C266B2E4",
            "B025D54E3E68E8EE23C613A0B8CED4F4CA6EBDD441F115B2D97D0FF90AD79D9B",
        ),
    }
    for key, (receipt_hash, manifest_hash) in expected.items():
        item = verified["accepted"][key]
        assert item["receipt_sha256"] == receipt_hash
        assert item["manifest"]["sha256"] == manifest_hash
        assert item["manifest"]["member_failures"] == 0
    assert verified["b9b"]["receipt_sha256"] == (
        "0A8E80FD1A46F5F63E4E8D41C0C3DAEE05F70A529ADC7EE69842FFE434FC9D36"
    )
    assert verified["b9b"]["manifest"]["sha256"] == (
        "1F62F96DB5A89016AFEFBC6210D9BAA37DA670754382A97F6595ADA1DAB3F991"
    )
    assert verified["b9b"]["manifest"]["member_failures"] == 0


def test_immutable_B9D_ordinary_mask_matches_B9B_and_is_not_redefined(
    placement_config: dict,
    verified: dict,
) -> None:
    spec = placement_config["accepted_inputs"]["immutable_ordinary_hour_mask"]
    assert sha256_file(ROOT / spec["path"]) == spec["sha256"]
    receipt = verified["b9b"]["receipt"]
    paths = _case_paths(receipt)
    network = pypsa.Network(paths["solved"])
    tables = _case_tables(network, paths)
    derived, _, metadata = _ordinary_hour_mask(tables["shedding"], tables["prices"])
    stored = pd.read_csv(ROOT / spec["path"])
    stored["snapshot"] = pd.to_datetime(stored["snapshot"])
    observed = stored.set_index("snapshot")["ordinary_hour"].astype(bool).reindex(network.snapshots)
    assert observed.equals(derived)
    source_metadata = json.loads((ROOT / spec["metadata_path"]).read_text(encoding="utf-8"))
    assert metadata["mask_boolean_sha256"] == source_metadata["mask_boolean_sha256"]
    assert source_metadata["outcome_dependent_redefinition"] is False


def _synthetic_four_cases() -> dict[str, dict]:
    snapshots = pd.date_range("2019-01-01", periods=4, freq="h")
    base_prices = pd.DataFrame(100.0, index=snapshots, columns=MARKETS)
    base_shedding = pd.DataFrame(0.0, index=snapshots, columns=MARKETS)
    base_prices.loc[snapshots[1], "FR"] = 15000.0
    base_prices.loc[snapshots[1], "CH"] = 600.0
    base_shedding.loc[snapshots[1], "FR"] = 2.0
    base_prices.loc[snapshots[2], "IT"] = 600.0
    cases: dict[str, dict] = {}
    for name, delta in (("B9B", 0.0), ("R10", 1.0), ("R5", 2.0), ("PLACEMENT", 3.0)):
        prices = base_prices.copy()
        prices.loc[snapshots[[0, 3]], list(EXPORTED_MARKETS)] += delta
        cases[name] = {
            "prices": prices,
            "shedding": base_shedding.copy(),
            "weights": pd.Series(1.0, index=snapshots),
            "load": pd.DataFrame(10.0, index=snapshots, columns=MARKETS),
            "system": {"total_shedding_MWh": float(base_shedding.sum().sum())},
        }
    return cases


def test_four_way_exported_schema_and_unique_hour_counting() -> None:
    cases = _synthetic_four_cases()
    market, aggregate, unique = _exported_market_diagnostics(cases, 15000.0)
    assert len(market) == 4 * 8
    assert set(market["case"]) == {"B9B", "R10", "R5", "PLACEMENT"}
    assert set(market["market"]) == set(EXPORTED_MARKETS)
    b9b_aggregate = aggregate.set_index("case").loc["B9B"]
    b9b_unique = unique.set_index("case").loc["B9B"]
    assert b9b_aggregate["total_VOLL_market_hours"] == 1
    assert b9b_aggregate["total_market_hours_above_500_EUR_per_MWh"] == 2
    assert b9b_unique["unique_hours_any_exported_market_at_VOLL"] == 1
    assert b9b_unique["unique_hours_any_exported_market_above_500_EUR_per_MWh"] == 1


def test_four_way_ordinary_hour_schema_and_direct_comparisons() -> None:
    cases = _synthetic_four_cases()
    mask, _, _ = _ordinary_hour_mask(cases["B9B"]["shedding"], cases["B9B"]["prices"])
    ordinary, direct = _ordinary_hour_comparisons(cases, mask)
    assert len(ordinary) == 8 * 4
    assert len(direct) == 8 * 2
    assert set(direct["comparison"]) == {"PLACEMENT_MINUS_R10", "PLACEMENT_MINUS_R5"}
    assert set(ordinary["mask_source"]) == {
        "IMMUTABLE_B9D_B9B_DERIVED_MASK_REUSED_UNCHANGED"
    }
    observed = direct.loc[
        (direct["market"] == "FR")
        & (direct["comparison"] == "PLACEMENT_MINUS_R5"),
        "mean_price_difference_EUR_per_MWh",
    ].item()
    assert observed == 1.0


def test_simultaneous_headroom_and_residual_path_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshots = pd.date_range("2019-01-01", periods=1, freq="h")
    network = pypsa.Network()
    network.set_snapshots(snapshots)
    interfaces = pd.read_csv(placement_module.INTERFACE_PATH)
    fake_flows = pd.DataFrame(
        0.0, index=snapshots, columns=interfaces["physical_link_id"]
    )
    monkeypatch.setattr(placement_module, "_flow_wide", lambda *_: fake_flows)
    shedding = pd.DataFrame(0.0, index=snapshots, columns=MARKETS)
    shedding.at[snapshots[0], "AT"] = 2.0
    case = {
        "network": network,
        "paths": {"flows": ROOT / "unused.parquet"},
        "shedding": shedding,
        "weights": pd.Series(1.0, index=snapshots),
    }
    contract = pd.DataFrame(
        {
            "bus": list(PROXY_MARKETS),
            "generator_id": [PLACEMENT_GENERATOR_IDS[m] for m in PROXY_MARKETS],
            "p_nom_MW": [100.0] * len(PROXY_MARKETS),
        }
    )
    dispatch = pd.DataFrame(
        0.0,
        index=snapshots,
        columns=[PLACEMENT_GENERATOR_IDS[m] for m in PROXY_MARKETS],
    )
    headroom, link_rows, summary = _placement_delivery_diagnostics(
        case, contract, dispatch
    )
    assert len(headroom) == 1
    assert len(link_rows) == 12
    assert headroom.iloc[0]["any_proxy_headroom_above_1_MW"]
    assert headroom.iloc[0]["any_proxy_directional_residual_path"]
    assert headroom.iloc[0]["any_proxy_simultaneous_headroom_and_path"]
    assert summary["targets"]["AT"]["hours_any_proxy_simultaneous_headroom_and_path"] == 1
    assert summary["automatic_A_to_D_classification"] is False
    assert _reachable({"FR": {"CH"}, "CH": {"AT"}}, "FR", "AT")


def test_near_constant_comparison_reports_capacity_dispatch_and_tails() -> None:
    cases = _synthetic_four_cases()
    utilization = pd.DataFrame(
        {
            "case": ["R5", "PLACEMENT"],
            "annual_dispatch_MWh": [100.0, 90.0],
        }
    )
    aggregates = pd.DataFrame(
        {
            "case": ["R5", "PLACEMENT"],
            "total_VOLL_market_hours": [10, 4],
            "total_market_hours_above_500_EUR_per_MWh": [20, 8],
            "total_market_hours_above_1000_EUR_per_MWh": [15, 6],
            "total_market_hours_above_5000_EUR_per_MWh": [12, 5],
        }
    )
    unique = pd.DataFrame(
        {
            "case": ["R5", "PLACEMENT"],
            "unique_hours_any_exported_market_at_VOLL": [5, 2],
            "unique_hours_any_exported_market_above_500_EUR_per_MWh": [8, 3],
            "unique_hours_any_exported_market_above_1000_EUR_per_MWh": [7, 3],
            "unique_hours_any_exported_market_above_5000_EUR_per_MWh": [6, 2],
        }
    )
    cases["R5"]["system"]["total_shedding_MWh"] = 20.0
    cases["PLACEMENT"]["system"]["total_shedding_MWh"] = 10.0
    comparison = _near_constant_comparison(
        cases,
        utilization,
        aggregates,
        unique,
        {
            "R5_total_virtual_capacity_MW": 51912.004881670524,
            "placement_total_virtual_capacity_MW": 52212.17741474419,
        },
    )
    assert {
        "total_virtual_capacity",
        "annual_virtual_dispatch",
        "system_shedding",
    }.issubset(set(comparison["metric"]))
    assert set(comparison["comparison_purpose"]) == {
        "DISTINGUISH_MORE_CAPACITY_FROM_BETTER_LOCATION"
    }
    assert not comparison["automatic_interpretation"].astype(bool).any()


def test_manual_command_is_guarded_and_completed_B9E_result_is_immutable_history(
    placement_config: dict,
) -> None:
    args = build_parser().parse_args(["b9e"])
    assert args.execute is False
    with pytest.raises(SystemExit, match="PLACEMENT_DIAGNOSTIC_NOT_EXECUTED"):
        run_b9e(args)
    assert placement_config["phase"]["command"] == (
        r".\.venv\Scripts\python.exe -m mem_model.stage_a.perimeter_closure_placement b9e --execute"
    )
    result_directory = ROOT / placement_config["phase"]["result_directory"]
    receipt_path = ROOT / placement_config["phase"]["receipt"]
    manifest_path = result_directory / (
        f"{placement_config['phase']['result_stem']}_Result_Manifest_v1.0.csv"
    )
    assert result_directory.is_dir()
    assert receipt_path.is_file()
    assert manifest_path.is_file()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["gate"] == "ETX7B9E_2050_PLACEMENT_PERIMETER_CLOSURE_COMPLETE"
    assert receipt["status"] == "PASS"
    receipt_qa = receipt.get("QA_metrics", receipt.get("qa", {}))
    assert receipt_qa.get(
        "production_price_acceptance",
        receipt_qa.get("economic_methodological_acceptance"),
    ) == "PENDING_SOL_REVIEW"
    assert receipt_qa.get("B10", "LOCKED" if not receipt_qa.get("B10_2050_authorized") else "AUTHORIZED") == "LOCKED"
    assert receipt_qa.get("Stage_B", "LOCKED" if not receipt_qa.get("Stage_B_2050_authorized") else "AUTHORIZED") == "LOCKED"


def test_preparation_artifacts_pass_without_production_execution() -> None:
    qa = json.loads(
        (ROOT / "qa/stage_a/etx7b9e/MEM_Placement_2050_Preparation_QA_v1.0.json").read_text(
            encoding="utf-8"
        )
    )
    final = json.loads(
        (
            ROOT
            / "qa/stage_a/etx7b9e/MEM_Placement_2050_Preparation_Final_Verification_v1.0.json"
        ).read_text(encoding="utf-8")
    )
    manifest_path = ROOT / final["preparation_manifest"]
    manifest = pd.read_csv(manifest_path)
    assert qa["status"] == final["status"] == "PASS"
    assert qa["production_optimization_executed"] is False
    assert qa["production_price_acceptance"] == "PENDING_SOL_REVIEW"
    assert qa["B10_authorized"] is False
    assert qa["Stage_B_authorized"] is False
    assert final["preparation_manifest_sha256"] == sha256_file(manifest_path)
    assert final["preparation_manifest_members"] == len(manifest)
    assert all(
        sha256_file(ROOT / row.relative_path) == row.sha256
        for row in manifest.itertuples()
    )


def test_B10_and_Stage_B_remain_locked_with_2050_source_unresolved() -> None:
    sources = yaml.safe_load(
        (ROOT / "config/stage_a_production_price_sources.yaml").read_text(encoding="utf-8")
    )
    gates = yaml.safe_load((ROOT / "config/approval_gates.yaml").read_text(encoding="utf-8"))
    assert sources["sources"][2040]["status"] == "ACCEPTED_PRODUCTION_PRICE_SOURCE"
    assert sources["sources"][2050]["status"] == "SOL_ACCEPTED_PENDING_PARENT_HASH_INTEGRATION"
    assert sources["sources"][2050]["phase"] == "ETX-7B9H"
    assert sources["sources"][2050]["method_role"] == (
        "SOL_SELECTED_2050_PRICE_SOURCE_NOT_YET_EXECUTABLE_IN_MASTER"
    )
    assert sources["governance"]["production_sources_resolved"] is False
    assert sources["governance"]["b10_authorized"] is False
    assert sources["governance"]["stage_b_authorized"] is False
    manual = gates["stage_a_manual_gates"]
    assert manual["b9d_authorized"] is False
    assert manual["b9e_authorized"] is False
    assert manual["b9f_authorized"] is False
    assert manual["b9g_authorized"] is False
    assert manual["b10_authorized"] is False
    assert manual["stage_b_authorized"] is False
    assert gates["full_year_solver"]["authorized_experiment_commands"] == []
    with pytest.raises(RuntimeError, match="B10_NOT_AUTHORIZED"):
        _load_accepted_production_price_sources()
    execution_source = (
        ROOT / "src/mem_model/stage_a/execution.py"
    ).read_text(encoding="utf-8")
    assert "EXPLICIT_ACCEPTED_CLOSURE_SOURCE_ONLY_NO_FALLBACK" in execution_source
    assert "EXPLICIT_ACCEPTED_R10_ONLY_NO_FALLBACK" not in execution_source

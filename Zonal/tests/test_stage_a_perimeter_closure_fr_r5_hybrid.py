from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from mem_model.stage_a.perimeter_closure_fr_r5_hybrid import (
    AUTHORIZED_FR_CAPACITY_MW,
    EXPECTED_TOTAL_MW,
    build_parser,
    load_hybrid_config,
    prepare_hybrid,
    verify_hybrid_inputs,
)
from mem_model.stage_a.receipts import sha256_file


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def prepared() -> dict:
    return prepare_hybrid(write_artifacts=False)


def test_B9F_exact_five_proxy_vector_and_france_only_delta(prepared: dict) -> None:
    expected = {
        "FR": 40616.26187526855,
        "GR": 6905.030474826701,
        "TN": 2433.2947267101813,
        "AT": 5506.614447347679,
        "SI": 3017.094676349047,
    }
    contract = prepared["contract"].set_index("bus")
    assert set(contract.index) == set(expected)
    assert set(contract.index).isdisjoint({"ME", "MT", "CH", "IT", "HR"})
    for market, capacity in expected.items():
        assert float(contract.at[market, "p_nom_MW"]) == pytest.approx(capacity, abs=5e-9)
    assert float(contract["p_nom_MW"].sum()) == pytest.approx(EXPECTED_TOTAL_MW, abs=1e-9)
    control = prepared["capacity_control"]
    assert control["B9F_FR_capacity_MW"] == AUTHORIZED_FR_CAPACITY_MW
    assert control["FR_delta_vs_B9E_MW"] == pytest.approx(6266.11878575798, abs=1e-9)
    assert control["changed_network_parameter_count"] == 1
    assert control["changed_market"] == "FR"
    assert control["near_constant_capacity_test"] is False


def test_B9F_france_percentiles_are_diagnostic_not_capacity_fallbacks(prepared: dict) -> None:
    benchmarks = prepared["benchmarks"].set_index("benchmark")
    expected = {
        "R10": (34350.14308951057, 0.10),
        "R5": (40616.26187526855, 0.05),
        "ALL_HOUR_P95": (25798.286742318498, 0.21565446650017014),
        "ALL_HOUR_P99": (45295.32633494766, 0.02812565226055126),
        "CONDITIONAL_GT1MW_P95": (49545.25992875735, 0.016229392569449497),
        "CONDITIONAL_GT1MW_P99": (61767.95532884312, 0.0020356747450140816),
    }
    for name, (capacity, residual_share) in expected.items():
        assert float(benchmarks.at[name, "capacity_MW"]) == pytest.approx(capacity, abs=5e-9)
        assert float(benchmarks.at[name, "baseline_shedding_energy_remaining_share"]) == pytest.approx(
            residual_share, abs=1e-12
        )
    assert benchmarks["authorized_B9F_capacity"].sum() == 1
    assert bool(benchmarks.at["R5", "authorized_B9F_capacity"])
    assert not benchmarks.loc[benchmarks.index.str.contains("P95|P99"), "authorized_B9F_capacity"].any()
    assert not benchmarks["percentile_values_are_authorized_replacements"].any()


def test_B9F_is_exact_B6_plus_one_carrier_five_fixed_generators_and_lp_only(prepared: dict) -> None:
    structural = prepared["structural"]
    assert structural["status"] == "PASS"
    assert structural["B6_vs_B9F_component_delta"] == {"carriers": 1, "generators": 5}
    assert structural["B9E_vs_B9F_virtual_parameter_delta"]["market"] == "FR"
    assert structural["all_other_B9E_virtual_parameters_preserved"] is True
    network = prepared["network"]
    assert len(network.snapshots) == 8760
    assert not network.generators["p_nom_extendable"].fillna(False).astype(bool).any()
    assert not network.generators["committable"].fillna(False).astype(bool).any()
    assert not network.links["p_nom_extendable"].fillna(False).astype(bool).any()
    assert not network.links["committable"].fillna(False).astype(bool).any()
    assert not network.stores["e_nom_extendable"].fillna(False).astype(bool).any()
    assert prepared["LP"]["integer_variables"] == 0
    assert prepared["LP"]["binary_variables"] == 0
    assert prepared["LP"]["continuous_linear_program_only"] is True


def test_B9F_cost_and_immutable_predecessors_are_exact(prepared: dict) -> None:
    assert prepared["cost"]["marginal_cost_EUR2025_per_MWh_el"] == pytest.approx(
        243.122262790698, abs=1e-12
    )
    assert np.allclose(
        prepared["contract"]["marginal_cost_EUR2025_per_MWh_el"].astype(float),
        243.122262790698,
        atol=0.0,
        rtol=0.0,
    )
    verified = verify_hybrid_inputs(load_hybrid_config())
    assert verified["accepted"]["b8d_2040_production_source"]["receipt_sha256"] == (
        "C69ACBF941261BD37426EDCEDB1C14482977C2059427FEA1968008492E05F8C0"
    )
    assert verified["accepted"]["b9e_2050_placement_diagnostic"]["receipt_sha256"] == (
        "98AE0FA50CF9E61C2F9EDA7018DEA434BD87DE547D7B8898FD5345D1B6D032C0"
    )


def test_B9F_governance_and_preparation_artifacts_fail_closed() -> None:
    config = load_hybrid_config()
    assert config["governance"]["B10_2050_authorized"] is False
    assert config["governance"]["Stage_B_2050_authorized"] is False
    gates = yaml.safe_load((ROOT / "config/approval_gates.yaml").read_text(encoding="utf-8"))
    manual = gates["stage_a_manual_gates"]
    assert manual["b9f_authorized"] is True
    assert manual["b10_2050_authorized"] is False
    assert manual["stage_b_2050_authorized"] is False
    final = json.loads(
        (ROOT / "qa/stage_a/etx7b9f/MEM_FR_R5_Hybrid_2050_Preparation_Final_Verification_v1.0.json").read_text(
            encoding="utf-8"
        )
    )
    assert final["status"] == "PASS"
    assert final["production_optimization_executed"] is False
    assert final["production_price_acceptance"] == "PENDING_SOL_REVIEW"
    assert final["B10_2050_authorized"] is False
    assert final["Stage_B_2050_authorized"] is False
    manifest_path = ROOT / final["preparation_manifest"]
    manifest = pd.read_csv(manifest_path)
    assert final["preparation_manifest_sha256"] == sha256_file(manifest_path)
    assert final["preparation_manifest_members"] == len(manifest)
    assert all(
        sha256_file(ROOT / row.relative_path) == row.sha256
        for row in manifest.itertuples()
    )


def test_B9F_cli_requires_the_explicit_execute_guard() -> None:
    parser = build_parser()
    args = parser.parse_args(["b9f"])
    assert args.execute is False

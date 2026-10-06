from __future__ import annotations

import json
import inspect
from pathlib import Path

import gurobipy
import linopy
import numpy as np
import pandas as pd
import pypsa
import pytest
import yaml

from mem_model.stage_a.network import (
    MARKETS,
    PATHS,
    _safe_id,
    _utc_naive,
    build_network_from_contracts,
    load_execution_config,
    load_stage_a_contracts,
    verify_input_locks,
)
from mem_model.stage_a.network_validation import validate_stage_a_network
from mem_model.stage_a.execution import PHASES, _require_execute, _select_smoke_snapshots, build_parser, run_b6
from mem_model.stage_a.receipts import build_receipt, command_string, read_receipt, validate_receipt, write_receipt


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def config() -> dict:
    return load_execution_config()


@pytest.fixture(scope="module")
def fixture_networks(config: dict) -> dict[int, tuple]:
    snapshots = _utc_naive(pd.read_parquet(PATHS["snapshots"])["snapshot"].head(6))
    result = {}
    for year in (2040, 2050):
        contracts = load_stage_a_contracts(year, snapshots=snapshots)
        network, metadata = build_network_from_contracts(contracts, config)
        result[year] = (contracts, network, metadata)
    return result


def test_runtime_versions_and_dependency_pin_are_bounded(config: dict) -> None:
    assert pypsa.__version__ == "1.2.3"
    assert linopy.__version__ == "0.7.0"
    assert np.__version__ == "2.4.6"
    assert gurobipy.__version__ == "13.0.3"
    assert config["solver"]["required_gurobipy_version"] == "13.0.3"
    assert "gurobi" in linopy.solvers.available_solvers


def test_b1_through_b5_input_locks_and_members_pass(config: dict) -> None:
    receipt = verify_input_locks(config)
    assert len(receipt) == 7
    assert {row["status"] for row in receipt} == {"PASS"}
    assert sum(row["member_count"] for row in receipt) == 43


@pytest.mark.parametrize("year", [2040, 2050])
def test_full_contract_chronology_and_annual_load_reconciliation(year: int) -> None:
    contracts = load_stage_a_contracts(year)
    assert len(contracts.snapshots) == 8760
    assert not contracts.snapshots.has_duplicates
    assert contracts.snapshots.tz is None
    assert (contracts.snapshots.to_series().diff().dropna() == pd.Timedelta(hours=1)).all()
    assert np.allclose(contracts.snapshot_weights, 1.0, atol=0, rtol=0)
    observed = contracts.load.groupby("country_code").load_MW.sum().astype(float).to_dict()
    expected = contracts.demand_controls.set_index("country_code").annual_demand_MWh.astype(float).to_dict()
    assert set(observed) == set(expected) == set(MARKETS)
    for market in MARKETS:
        assert observed[market] == pytest.approx(expected[market], abs=1e-3)


@pytest.mark.parametrize("year", [2040, 2050])
def test_narrow_accepted_contract_fixture_passes_all_structural_checks(
    year: int,
    fixture_networks: dict[int, tuple],
    config: dict,
) -> None:
    contracts, network, metadata = fixture_networks[year]
    result = validate_stage_a_network(network, contracts, metadata, config)
    assert result["status"] == "PASS", result["qa"].loc[result["qa"].status.eq("FAIL")].to_dict("records")
    assert len(result["qa"]) == 26


@pytest.mark.parametrize("year", [2040, 2050])
def test_all_component_carriers_are_explicitly_registered(
    year: int,
    fixture_networks: dict[int, tuple],
) -> None:
    _, network, _ = fixture_networks[year]
    registered = set(network.carriers.index.astype(str))
    missing: dict[str, list[str]] = {}
    for component, frame in (
        ("Bus", network.buses),
        ("Generator", network.generators),
        ("Link", network.links),
        ("Store", network.stores),
    ):
        referenced = {
            label
            for value in frame["carrier"].dropna()
            if (label := str(value).strip())
        }
        undefined = sorted(referenced - registered)
        if undefined:
            missing[component] = undefined
    assert missing == {}


def test_exact_market_scope_and_no_hidden_stage_b_objects(fixture_networks: dict[int, tuple]) -> None:
    _, network, _ = fixture_networks[2040]
    market_buses = set(network.buses.index[network.buses.carrier.eq("AC_STAGE_A_MARKET")])
    assert market_buses == set(MARKETS)
    assert "CORS" not in set(network.buses.index)
    assert not network.buses.carrier.astype(str).str.contains("external_market|corsica", case=False).any()


def test_generator_partition_profiles_costs_and_krsko(fixture_networks: dict[int, tuple]) -> None:
    contracts, network, _ = fixture_networks[2040]
    hydro_assets = set(contracts.hydro.source_asset_id)
    ordinary = contracts.generators.loc[~contracts.generators.asset_id.isin(hydro_assets)]
    assert set(ordinary.asset_id).issubset(network.generators.index)
    assert hydro_assets.isdisjoint(network.generators.index)
    for row in ordinary.itertuples():
        assert network.generators.at[row.asset_id, "p_nom"] == pytest.approx(float(row.p_nom_MW))
        assert network.generators.at[row.asset_id, "marginal_cost"] == pytest.approx(float(row.marginal_cost_EUR2025_per_MWh_el))
    nuclear = contracts.generators.loc[contracts.generators.source_carrier.str.contains("NUCLEAR", case=False, na=False)]
    assert nuclear.loc[nuclear.country_code.eq("HR")].empty
    assert not nuclear.loc[nuclear.country_code.eq("SI")].empty


def test_bess_and_phs_are_not_duplicated_and_italy_controls_reconcile(fixture_networks: dict[int, tuple]) -> None:
    contracts, network, _ = fixture_networks[2040]
    bess = contracts.storage.loc[contracts.storage.storage_family.eq("BESS")]
    assert sum(name.startswith("STORE_BESS_") for name in network.stores.index) == len(bess)
    assert not any("PHS" in name for name in network.stores.index if name.startswith("STORE_BESS_"))
    italy_phs = contracts.hydro.loc[contracts.hydro.country_code.eq("IT") & contracts.hydro.hydro_class.isin(["PURE_PHS", "MIXED_PHS"])]
    assert len(italy_phs) == 2
    assert italy_phs.turbine_power_MW.astype(float).sum() == pytest.approx(7252.3)
    assert italy_phs.pump_power_MW.astype(float).sum() == pytest.approx(6400.0)
    assert italy_phs.operational_state_energy_MWh.astype(float).sum() == pytest.approx(53000.0)
    mixed = italy_phs.loc[italy_phs.hydro_class.eq("MIXED_PHS")].iloc[0]
    safe = _safe_id(mixed.operational_slice_id)
    assert f"STORE_WATER_{safe}" in network.stores.index
    assert f"PUMP_{safe}" in network.links.index
    assert f"TURBINE_{safe}" in network.links.index
    assert f"INFLOW_{safe}" in network.generators.index
    assert f"SPILL_{safe}" in network.generators.index


def test_hydro_inflow_is_fixed_once_for_stateful_slices(fixture_networks: dict[int, tuple]) -> None:
    contracts, network, _ = fixture_networks[2040]
    stateful = contracts.hydro.loc[
        contracts.hydro.energy_state_required.astype(str).str.lower().eq("true")
        & contracts.hydro.natural_inflow_required.astype(str).str.lower().eq("true")
    ]
    assert len(stateful) > 0
    for row in stateful.itertuples():
        name = f"INFLOW_{_safe_id(row.operational_slice_id)}"
        assert name in network.generators.index
        assert np.allclose(network.generators_t.p_min_pu[name], network.generators_t.p_max_pu[name])
        assert f"SPILL_{_safe_id(row.operational_slice_id)}" in network.generators.index


@pytest.mark.parametrize("year", [2040, 2050])
def test_signed_interconnectors_preserve_asymmetric_b5_limits(
    year: int,
    fixture_networks: dict[int, tuple],
) -> None:
    contracts, network, metadata = fixture_networks[year]
    runtime = network.links.loc[network.links.carrier.eq("INTERCONNECTOR")]
    assert len(runtime) == 12
    assert len(metadata["interconnector_mapping"]) == 12
    for interface in contracts.interfaces.itertuples():
        name = f"INTERCONNECTOR_{_safe_id(interface.physical_link_id)}"
        link = runtime.loc[name]
        rows = contracts.links.loc[contracts.links.physical_link_id.eq(interface.physical_link_id)]
        a_to_b = float(rows.loc[rows.from_market.eq(interface.endpoint_a), "capacity_MW"].iloc[0])
        b_to_a = float(rows.loc[rows.from_market.eq(interface.endpoint_b), "capacity_MW"].iloc[0])
        assert link.p_nom * link.p_max_pu == pytest.approx(a_to_b)
        assert -link.p_nom * link.p_min_pu == pytest.approx(b_to_a)
        assert link.efficiency == 1.0
        assert link.marginal_cost == 0.0
        assert not bool(link.p_nom_extendable)
        assert not bool(link.committable)


def test_every_capacity_is_fixed_and_local_shedding_is_hourly_load_bounded(fixture_networks: dict[int, tuple]) -> None:
    _, network, _ = fixture_networks[2040]
    assert not network.generators.p_nom_extendable.astype(bool).any()
    assert not network.links.p_nom_extendable.astype(bool).any()
    assert not network.stores.e_nom_extendable.astype(bool).any()
    assert not network.generators.committable.astype(bool).any()
    assert not network.links.committable.astype(bool).any()
    for market in MARKETS:
        shedding = f"LOAD_SHEDDING_{market}"
        hourly_cap = network.generators_t.p_max_pu[shedding] * network.generators.at[shedding, "p_nom"]
        local_load = network.loads_t.p_set[f"LOAD_{market}"]
        assert np.allclose(hourly_cap, local_load)
        assert (hourly_cap >= 0).all()
        assert network.generators.at[shedding, "marginal_cost"] == 15000.0


def test_narrow_network_build_is_deterministic(fixture_networks: dict[int, tuple], config: dict) -> None:
    contracts, first, first_meta = fixture_networks[2040]
    second, second_meta = build_network_from_contracts(contracts, config)
    pd.testing.assert_frame_equal(first.buses, second.buses)
    pd.testing.assert_frame_equal(first.generators, second.generators)
    pd.testing.assert_frame_equal(first.links, second.links)
    pd.testing.assert_frame_equal(first.stores, second.stores)
    pd.testing.assert_frame_equal(first.generators_t.p_max_pu, second.generators_t.p_max_pu)
    assert first_meta["summary"] == second_meta["summary"]


def test_receipt_schema_round_trip(tmp_path: Path) -> None:
    receipt = build_receipt(
        phase="SYNTHETIC_PRE_B6_TEST",
        gate="SYNTHETIC_ONLY",
        status="PASS",
        input_manifests=[],
        outputs={"fixture": "none"},
        qa={"checks": 1, "failures": 0},
        next_gate="NONE",
        command="synthetic-test",
        runtime_seconds=0.0,
        solve={"termination_condition": "optimal", "snapshots": 2, "objective": 20.0},
    )
    validate_receipt(receipt)
    path = write_receipt(tmp_path / "receipt.json", receipt)
    loaded = read_receipt(path)
    assert loaded["phase"] == "SYNTHETIC_PRE_B6_TEST"
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "PASS"


def test_canonical_module_command_is_preserved_for_future_receipts() -> None:
    command = command_string(
        ["b8", "--execute"],
        module="mem_model.stage_a.execution",
    )
    assert "-m mem_model.stage_a.execution b8 --execute" in command


def test_manual_entrypoint_parser_and_execution_guard(fixture_networks: dict[int, tuple]) -> None:
    parser = build_parser()
    for command in PHASES:
        parsed = parser.parse_args([command])
        assert parsed.command == command
        assert parsed.execute is False
        with pytest.raises(SystemExit, match="FORMAL_GATE_NOT_EXECUTED"):
            _require_execute(parsed)
    _, network, _ = fixture_networks[2040]
    selected = _select_smoke_snapshots(network, 3)
    assert len(selected) == 3
    assert (selected.to_series().diff().dropna() == pd.Timedelta(hours=1)).all()


def test_b6_preflight_precedes_contract_verification_and_network_assembly() -> None:
    source = inspect.getsource(run_b6)
    assert source.index("preflight = gurobi_preflight(config)") < source.index("input_receipts = _input_receipts(config)")
    assert source.index("preflight = gurobi_preflight(config)") < source.index("load_stage_a_contracts")


def test_manual_gate_metadata_and_runbooks_are_fail_closed(config: dict) -> None:
    approvals = yaml.safe_load((ROOT / "config/approval_gates.yaml").read_text(encoding="utf-8"))
    assert config["preparation_status"] == "PRE_B6_MANUAL_EXECUTION_HARNESS_READY"
    assert approvals["execution_mode"]["status"] == "MANUAL_POWERSHELL_GATE_EXECUTION_READY"
    assert approvals["full_year_solver"]["execution_enabled"] is False
    assert approvals["full_year_solver"]["unlock_token"] is None
    assert approvals["full_year_solver"]["status"] == "FINAL_RESIDUAL_ME_MT_TN_2050_ISOLATED_FULL_YEAR_DIAGNOSTIC_PREPARED"
    assert approvals["full_year_solver"]["etx7b8_execution_authorized"] is False
    assert approvals["full_year_solver"]["successor_gates_authorized"] is False
    assert approvals["full_year_solver"]["r10_experiment_execution_enabled"] is False
    assert approvals["full_year_solver"]["r5_diagnostic_execution_enabled"] is False
    assert approvals["full_year_solver"]["placement_diagnostic_execution_enabled"] is False
    assert approvals["full_year_solver"]["residual_me_mt_tn_diagnostic_execution_enabled"] is False
    assert approvals["full_year_solver"]["production_source_promotion_automatic"] is False
    assert approvals["stage_a_manual_gates"]["current_gate"] == "ETX7B9H_2050_FINAL_RESIDUAL_MT_TN_CLOSURE_COMPLETE"
    assert approvals["stage_a_manual_gates"]["b8_status"] == "PASS"
    assert approvals["stage_a_manual_gates"]["b9a_status"] == "PASS"
    assert approvals["stage_a_manual_gates"]["b9b_status"] == "PASS"
    assert approvals["stage_a_manual_gates"]["b9_authorized"] is False
    assert approvals["stage_a_manual_gates"]["b8d_authorized"] is False
    assert approvals["stage_a_manual_gates"]["b9c_authorized"] is False
    assert approvals["stage_a_manual_gates"]["b9d_authorized"] is False
    assert approvals["stage_a_manual_gates"]["b9e_authorized"] is False
    assert approvals["stage_a_manual_gates"]["b9f_authorized"] is False
    assert approvals["stage_a_manual_gates"]["b9g_authorized"] is False
    assert approvals["stage_a_manual_gates"]["b10_authorized"] is False
    assert approvals["stage_a_manual_gates"]["stage_b_authorized"] is False
    auxiliary = approvals["auxiliary_experiments"]["etx7b8c_s2"]
    assert auxiliary["status"] == "PASS_IMMUTABLE"
    assert auxiliary["formal_gate"] is False
    assert auxiliary["methodological_role"] == "ACCEPTED_DIAGNOSTIC_MAX_CLOSURE_EXPERIMENT"
    assert auxiliary["production_price_source"] is False

    expected = {
        "ETX7B6_RUNBOOK.md": ("ETX7B5_FIXED_STAGE_A_TOPOLOGY_COMPLETE", "b6"),
        "ETX7B7_RUNBOOK.md": ("ETX7B6_STAGE_A_SOLVER_READY", "b7"),
        "ETX7B8_RUNBOOK.md": ("ETX7B7_2040_BASE_SMOKE_COMPLETE", "b8"),
        "ETX7B9_RUNBOOK.md": ("ETX7B8_2040_BASE_FULL_YEAR_COMPLETE", "b9-smoke"),
        "ETX7B10_RUNBOOK.md": ("EXPLICIT_SOL_ACCEPTANCE_OF_ETX7B8D_AND_ETX7B9E_PRODUCTION_SOURCES", "b10"),
    }
    for filename, (predecessor, command) in expected.items():
        text = (ROOT / "docs/runbooks" / filename).read_text(encoding="utf-8")
        assert "MODE: IMPLEMENTATION_ONLY" in text
        assert "RESEARCH_ALLOWED: FALSE" in text
        assert "NEW_ASSUMPTIONS_ALLOWED: FALSE" in text
        assert f"PREDECESSOR_GATE: {predecessor}" in text
        assert f".\\.venv\\Scripts\\python.exe -m mem_model.stage_a.execution {command} --execute" in text

    r10_expected = {
        "ETX7B8D_RUNBOOK.md": ("ETX7B8_2040_BASE_FULL_YEAR_COMPLETE", "b8d"),
        "ETX7B9C_RUNBOOK.md": ("ETX7B9_2050_BASE_FULL_YEAR_COMPLETE", "b9c"),
    }
    for filename, (predecessor, command) in r10_expected.items():
        text = (ROOT / "docs/runbooks" / filename).read_text(encoding="utf-8")
        assert "MODE: IMPLEMENTATION_ONLY" in text
        assert "RESEARCH_ALLOWED: FALSE" in text
        assert "NEW_ASSUMPTIONS_ALLOWED: FALSE" in text
        assert f"PREDECESSOR_GATE: {predecessor}" in text
        assert (
            f".\\.venv\\Scripts\\python.exe -m mem_model.stage_a.perimeter_closure_r10 "
            f"{command} --execute"
        ) in text

    r5 = (ROOT / "docs/runbooks/ETX7B9D_RUNBOOK.md").read_text(encoding="utf-8")
    assert "MODE: IMPLEMENTATION_ONLY" in r5
    assert "RESEARCH_ALLOWED: FALSE" in r5
    assert "NEW_ASSUMPTIONS_ALLOWED: FALSE" in r5
    assert "PREDECESSOR_GATE: ETX7B9_2050_BASE_FULL_YEAR_COMPLETE" in r5
    assert (
        ".\\.venv\\Scripts\\python.exe -m mem_model.stage_a.perimeter_closure_r5 "
        "b9d --execute"
    ) in r5

    placement = (ROOT / "docs/runbooks/ETX7B9E_RUNBOOK.md").read_text(encoding="utf-8")
    assert "MODE: IMPLEMENTATION_ONLY" in placement
    assert "RESEARCH_ALLOWED: FALSE" in placement
    assert "NEW_ASSUMPTIONS_ALLOWED: FALSE" in placement
    assert "PREDECESSOR_GATE: ETX7B9D_2050_R5_PERIMETER_CLOSURE_COMPLETE" in placement
    assert (
        ".\\.venv\\Scripts\\python.exe -m mem_model.stage_a.perimeter_closure_placement "
        "b9e --execute"
    ) in placement


def test_pypsa_linopy_synthetic_dual_and_marginal_price_api() -> None:
    network = pypsa.Network()
    network.set_snapshots(pd.date_range("2019-01-01", periods=2, freq="h"))
    network.add("Bus", "A")
    network.add("Bus", "B")
    network.add("Generator", "SUPPLY", bus="A", p_nom=100.0, marginal_cost=10.0)
    network.add("Load", "DEMAND", bus="B")
    network.loads_t.p_set["DEMAND"] = pd.Series([5.0, 6.0], index=network.snapshots)
    network.add("Link", "A_TO_B", bus0="A", bus1="B", p_nom=100.0, efficiency=1.0)
    status, condition = network.optimize(
        solver_name="highs",
        log_to_console=False,
        include_objective_constant=False,
    )
    assert str(status).lower() == "ok"
    assert str(condition).lower() == "optimal"
    assert set(network.buses_t.marginal_price.columns) == {"A", "B"}
    assert np.isfinite(network.buses_t.marginal_price.to_numpy()).all()
    assert np.allclose(network.buses_t.marginal_price["B"], 10.0)

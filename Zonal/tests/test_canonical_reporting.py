from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pypsa
import pytest

from mem_model.reporting.canonical_results import (
    annual_demand_generation_balance_by_zone,
    annual_electricity_supply_by_zone,
    annual_generation_by_zone,
    annual_storage_charging_by_zone,
    annual_net_imports_by_zone,
    dispatch_8760_by_zone,
    generate_canonical_results,
    generate_cross_scenario_comparison,
    installed_capacity_by_zone,
    zonal_price_statistics,
)


ZONES = ("CALA", "CNOR", "CSUD", "NORD", "SARD", "SICI", "SUD")


@pytest.fixture(scope="module")
def solved_fixture() -> pypsa.Network:
    network = pypsa.Network()
    snapshots = pd.date_range("2019-01-01", periods=24, freq="h")
    network.set_snapshots(snapshots)
    network.snapshot_weightings.loc[:, "generators"] = 1.0
    network.snapshot_weightings.loc[:, "objective"] = 1.0
    network.snapshot_weightings.iloc[0, network.snapshot_weightings.columns.get_loc("generators")] = 2.0
    network.snapshot_weightings.iloc[0, network.snapshot_weightings.columns.get_loc("objective")] = 2.0
    for carrier in (
        "AC",
        "solar_pv_utility",
        "methane_ccgt",
        "battery_energy",
        "water_energy",
        "internal_transfer",
        "external_trade",
        "corsica_hub",
        "boundary",
        "load_shedding",
    ):
        network.add("Carrier", carrier)
    for zone in ZONES:
        network.add("Bus", zone, carrier="AC")
        network.add("Load", f"LOAD_{zone}", bus=zone)
    network.loads_t.p_set = pd.DataFrame(
        {f"LOAD_{zone}": np.full(24, 10.0 + index) for index, zone in enumerate(ZONES)},
        index=snapshots,
    )
    network.add("Generator", "SOLAR_NORD", bus="NORD", carrier="solar_pv_utility", p_nom=100.0)
    network.add("Generator", "CCGT_SICI", bus="SICI", carrier="methane_ccgt", p_nom=80.0)
    for zone, dispatch in {"CALA": 10.0, "CNOR": 1.0, "CSUD": 12.0, "SARD": 14.0, "SUD": 16.0}.items():
        network.add("Generator", f"LOAD_SHEDDING_{zone}", bus=zone, carrier="load_shedding", p_nom=100.0)
    network.add("Bus", "BESS_STATE", carrier="battery_energy")
    network.add("Link", "BESS_DISCHARGE_FIXTURE", bus0="BESS_STATE", bus1="NORD", carrier="battery_energy", p_nom=10.0 / 0.9, efficiency=0.9)
    network.add("Link", "BESS_CHARGE_FIXTURE", bus0="NORD", bus1="BESS_STATE", carrier="battery_energy", p_nom=10.0, efficiency=0.9)
    network.add("Bus", "WATER_STATE", carrier="water_energy")
    network.add("Link", "TURBINE_2040_BASE_NORD_RESERVOIR", bus0="WATER_STATE", bus1="NORD", carrier="water_energy", p_nom=20.0 / 0.9, efficiency=0.9)
    network.add("Link", "TURBINE_2040_BASE_NORD_PURE_PHS", bus0="WATER_STATE", bus1="NORD", carrier="water_energy", p_nom=2.0 / 0.9, efficiency=0.9)
    network.add("Link", "PUMP_2040_BASE_NORD_PURE_PHS", bus0="NORD", bus1="WATER_STATE", carrier="water_energy", p_nom=5.0, efficiency=0.9)
    network.add("Link", "INTERNAL_NORD_TO_CNOR", bus0="NORD", bus1="CNOR", carrier="internal_transfer", p_nom=30.0, efficiency=1.0)
    network.add("Bus", "EXT_FR", carrier="boundary")
    network.add("Link", "IMPORT_FR_TO_NORD", bus0="EXT_FR", bus1="NORD", carrier="external_trade", p_nom=20.0, efficiency=1.0)
    network.add("Link", "EXPORT_NORD_TO_FR", bus0="NORD", bus1="EXT_FR", carrier="external_trade", p_nom=15.0, efficiency=1.0)
    network.add("Bus", "CORS", carrier="corsica_hub")
    network.add("Link", "CORS_CNOR_IMPORT", bus0="CORS", bus1="CNOR", carrier="corsica_hub", p_nom=4.0, efficiency=1.0)

    generator_dispatch = {
        "SOLAR_NORD": np.full(24, 13.0),
        "CCGT_SICI": np.full(24, 15.0),
    }
    generator_dispatch.update(
        {
            f"LOAD_SHEDDING_{zone}": np.full(24, dispatch)
            for zone, dispatch in {"CALA": 10.0, "CNOR": 1.0, "CSUD": 12.0, "SARD": 14.0, "SUD": 16.0}.items()
        }
    )
    network.generators_t.p = pd.DataFrame(generator_dispatch, index=snapshots)
    network.links_t.p0 = pd.DataFrame(0.0, index=snapshots, columns=network.links.index)
    network.links_t.p1 = pd.DataFrame(0.0, index=snapshots, columns=network.links.index)
    flows = {
        "BESS_DISCHARGE_FIXTURE": (5.0, -4.5),
        "BESS_CHARGE_FIXTURE": (2.0, -1.8),
        "TURBINE_2040_BASE_NORD_RESERVOIR": (3.0, -2.7),
        "TURBINE_2040_BASE_NORD_PURE_PHS": (2.0, -1.8),
        "PUMP_2040_BASE_NORD_PURE_PHS": (1.0, -0.9),
        "INTERNAL_NORD_TO_CNOR": (10.0, -10.0),
        "IMPORT_FR_TO_NORD": (5.0, -5.0),
        "EXPORT_NORD_TO_FR": (1.0, -1.0),
        "CORS_CNOR_IMPORT": (0.0, 0.0),
    }
    for name, (p0, p1) in flows.items():
        network.links_t.p0.loc[:, name] = p0
        network.links_t.p1.loc[:, name] = p1
    prices = {}
    for index, zone in enumerate(ZONES):
        values = np.arange(24, dtype="float64") + 40.0 + index
        if zone == "CALA":
            values[0] = -5.0
            values[1] = 0.0
            values[-1] = 600.0
        prices[zone] = values
    network.buses_t.marginal_price = pd.DataFrame(prices, index=snapshots)
    return network


def test_installed_capacity_uses_gw_and_delivered_link_power(solved_fixture: pypsa.Network) -> None:
    table = installed_capacity_by_zone(solved_fixture)
    nord = table.loc[table["zone"].astype(str).eq("NORD")].set_index("display_technology")
    assert nord.at["Solar PV - utility", "installed_capacity_GW"] == pytest.approx(0.1)
    assert nord.at["BESS", "installed_capacity_GW"] == pytest.approx(0.01)
    assert nord.at["Hydro - reservoir", "installed_capacity_GW"] == pytest.approx(0.02)


def test_supply_roles_and_primary_generation_are_not_double_counted(solved_fixture: pypsa.Network) -> None:
    supply = annual_electricity_supply_by_zone(solved_fixture)
    nord = supply.loc[supply["zone"].astype(str).eq("NORD")].set_index("display_technology")
    assert nord.at["Solar PV - utility", "annual_electricity_supply_TWh"] == pytest.approx((25 * 13.0) / 1_000_000.0)
    assert nord.at["BESS", "annual_electricity_supply_TWh"] == pytest.approx((25 * 4.5) / 1_000_000.0)
    assert nord.at["PHS", "annual_electricity_supply_TWh"] == pytest.approx((25 * 1.8) / 1_000_000.0)
    assert nord.at["Hydro - reservoir", "annual_electricity_supply_TWh"] == pytest.approx((25 * 2.7) / 1_000_000.0)
    assert nord.at["BESS", "supply_role"] == "BESS_DISCHARGE"
    assert nord.at["PHS", "supply_role"] == "PHS_DISCHARGE"
    assert nord.at["Hydro - reservoir", "supply_role"] == "PRIMARY_GENERATION"

    generation = annual_generation_by_zone(solved_fixture)
    primary = generation.loc[generation["zone"].astype(str).eq("NORD")].set_index("display_technology")
    assert "BESS" not in primary.index
    assert "PHS" not in primary.index
    assert primary.at["Hydro - reservoir", "annual_primary_generation_TWh"] == pytest.approx((25 * 2.7) / 1_000_000.0)


def test_demand_uses_snapshot_weights_once(solved_fixture: pypsa.Network) -> None:
    table = annual_demand_generation_balance_by_zone(solved_fixture)
    cala = table.set_index("zone").loc["CALA"]
    assert cala["gross_end_use_demand_TWh"] == pytest.approx((25 * 10.0) / 1_000_000.0)


def test_storage_charging_and_electrical_balance_are_explicit(solved_fixture: pypsa.Network) -> None:
    charging = annual_storage_charging_by_zone(solved_fixture)
    nord = charging.loc[charging["zone"].astype(str).eq("NORD")].set_index("charging_role")
    assert nord.at["BESS_CHARGING", "annual_charging_TWh"] == pytest.approx((25 * 2.0) / 1_000_000.0)
    assert nord.at["PHS_CHARGING", "annual_charging_TWh"] == pytest.approx((25 * 1.0) / 1_000_000.0)

    balance = annual_demand_generation_balance_by_zone(solved_fixture)
    assert balance["balance_check_status"].eq("PASS").all()
    assert balance["balance_residual_TWh"].abs().max() <= 1e-12
    nord_balance = balance.set_index("zone").loc["NORD"]
    expected = (
        nord_balance["primary_generation_TWh"]
        + nord_balance["bess_discharge_TWh"]
        + nord_balance["phs_discharge_TWh"]
        + nord_balance["net_imports_TWh"]
        + nord_balance["load_shedding_TWh"]
        - nord_balance["gross_end_use_demand_TWh"]
        - nord_balance["bess_charging_TWh"]
        - nord_balance["phs_charging_TWh"]
    )
    assert nord_balance["balance_residual_TWh"] == pytest.approx(expected)


def test_flexible_p2x_is_demand_not_generation_and_preserves_balance(solved_fixture: pypsa.Network) -> None:
    network = solved_fixture.copy()
    network.add("Carrier", "p2x_flexible_NORD")
    network.add("Generator", "P2X_NORD", bus="NORD", carrier="p2x_flexible_NORD",
                sign=-1, p_nom=2.0, p_min_pu=0.0, p_max_pu=1.0)
    network.add("GlobalConstraint", "P2X_ANNUAL_NORD", type="operational_limit",
                carrier_attribute="p2x_flexible_NORD", sense="==", constant=27.0)
    dispatch = pd.Series(1.0, index=network.snapshots)
    dispatch.iloc[0] = 2.0
    network.generators_t.p["P2X_NORD"] = dispatch
    network.generators_t.p["SOLAR_NORD"] += dispatch
    balance = annual_demand_generation_balance_by_zone(network).set_index("zone")
    assert balance.at["NORD", "p2x_electrical_consumption_TWh"] == pytest.approx(27.0 / 1e6)
    assert balance.at["NORD", "rigid_end_use_demand_TWh"] == pytest.approx(25.0 * 13.0 / 1e6)
    assert balance.at["NORD", "gross_end_use_demand_TWh"] == pytest.approx((25.0 * 13.0 + 27.0) / 1e6)
    assert balance["balance_check_status"].eq("PASS").all()
    assert not annual_electricity_supply_by_zone(network)["display_technology"].astype(str).str.contains("P2X").any()
    pd.testing.assert_frame_equal(installed_capacity_by_zone(network), installed_capacity_by_zone(solved_fixture))
    hourly = dispatch_8760_by_zone(network).query("zone == 'NORD'")
    assert hourly["p2x_electrical_consumption_MW"].sum() == pytest.approx(25.0)


def test_load_weighted_price_and_unclipped_tail_statistics(solved_fixture: pypsa.Network) -> None:
    table = zonal_price_statistics(solved_fixture).set_index("zone")
    values = solved_fixture.buses_t.marginal_price["CALA"]
    weights = solved_fixture.snapshot_weightings["objective"]
    loads = solved_fixture.loads_t.p_set["LOAD_CALA"]
    expected = float((values * weights * loads).sum() / (weights * loads).sum())
    assert table.at["CALA", "load_weighted_mean_EUR_per_MWh"] == pytest.approx(expected)
    assert table.at["CALA", "maximum_EUR_per_MWh"] == 600.0
    assert table.at["CALA", "negative_price_hours"] == 1
    assert table.at["CALA", "zero_price_hours"] == 1
    assert table.at["CALA", "hours_above_500_EUR_per_MWh"] == 1
    assert not table.at["CALA", "price_clipping_applied"]


def test_interzonal_flow_sign_convention(solved_fixture: pypsa.Network) -> None:
    table = annual_net_imports_by_zone(solved_fixture).set_index("zone")
    link_weight_sum = solved_fixture.snapshot_weightings["objective"].sum()
    assert table.at["NORD", "annual_net_imports_TWh"] == pytest.approx((-10.0 + 5.0 - 1.0) * link_weight_sum / 1_000_000.0)
    assert table.at["CNOR", "annual_net_imports_TWh"] == pytest.approx(10.0 * link_weight_sum / 1_000_000.0)


def test_dispatch_preserves_every_hour_without_duplicate_aggregation(solved_fixture: pypsa.Network) -> None:
    table = dispatch_8760_by_zone(solved_fixture)
    assert len(table) == 8 * 24
    assert not table.duplicated(["snapshot", "zone"]).any()
    assert table.groupby("zone")["snapshot"].nunique().eq(24).all()
    nord = table.loc[table["zone"].eq("NORD")]
    assert nord["storage_phs_charging_MW"].eq(3.0).all()
    assert nord["bess_charging_MW"].eq(2.0).all()
    assert nord["phs_charging_MW"].eq(1.0).all()


def test_full_year_guard_rejects_fixture(solved_fixture: pypsa.Network, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="8,760"):
        generate_canonical_results(solved_fixture, 2040, "Base", tmp_path, require_full_year=True, render_figures=False)


def test_exported_source_tables_equal_computed_tables(solved_fixture: pypsa.Network, tmp_path: Path) -> None:
    receipt = generate_canonical_results(
        solved_fixture,
        2040,
        "Base",
        tmp_path,
        require_full_year=False,
        render_figures=False,
    )
    exported = pd.read_csv(tmp_path / "statistics" / "zonal_price_statistics.csv")
    expected = zonal_price_statistics(solved_fixture)
    pd.testing.assert_frame_equal(exported, expected, check_exact=False, check_dtype=False, rtol=1e-10, atol=1e-10)
    exported_supply = pd.read_csv(tmp_path / "statistics" / "annual_electricity_supply_by_zone.csv")
    expected_supply = annual_electricity_supply_by_zone(solved_fixture)
    pd.testing.assert_frame_equal(
        exported_supply,
        expected_supply,
        check_exact=False,
        check_dtype=False,
        check_categorical=False,
        rtol=1e-10,
        atol=1e-10,
    )
    exported_balance = pd.read_csv(tmp_path / "statistics" / "annual_electrical_balance_by_zone.csv")
    assert exported_balance["balance_check_status"].eq("PASS").all()
    assert receipt["reporting_solver_invocations"] == 0
    assert receipt["electrical_balance_status"] == "PASS"
    assert receipt["figure_file_count"] == 0


def test_rendered_figures_have_png_and_svg_backing(solved_fixture: pypsa.Network, tmp_path: Path) -> None:
    receipt = generate_canonical_results(
        solved_fixture,
        2040,
        "Base",
        tmp_path,
        require_full_year=False,
        render_figures=True,
    )
    assert receipt["figure_file_count"] >= 28
    assert (tmp_path / "figures" / "installed_capacity_by_zone.png").exists()
    assert (tmp_path / "figures" / "installed_capacity_by_zone.svg").exists()
    assert (tmp_path / "figures" / "dispatch_8760_NORD.png").exists()
    assert (tmp_path / "figures" / "dispatch_8760_NATIONAL.svg").exists()
    assert (tmp_path / "figures" / "network_topology.png").exists()
    assert (tmp_path / "statistics" / "network_topology_interfaces.csv").exists()
    assert (tmp_path / "figures" / "annual_electricity_supply_by_zone.png").exists()
    assert (tmp_path / "statistics" / "annual_electrical_balance_by_zone.csv").exists()


def test_cross_scenario_tables_are_prepared_without_solving(solved_fixture: pypsa.Network, tmp_path: Path) -> None:
    run_directories = {}
    for scenario in ("Slow", "Base", "High"):
        run = tmp_path / scenario
        generate_canonical_results(
            solved_fixture,
            2040,
            scenario,
            run,
            require_full_year=False,
            render_figures=False,
        )
        run_directories[scenario] = run
    receipt = generate_cross_scenario_comparison(run_directories, 2040, tmp_path / "comparison", render_figures=True)
    assert receipt["status"] == "PASS"
    assert receipt["reporting_solver_invocations"] == 0
    assert receipt["table_count"] == 8
    assert receipt["figure_file_count"] == 14
    assert (tmp_path / "comparison" / "MEM_CANONICAL_CROSS_SCENARIO_REPORTING_MANIFEST.csv").is_file()
    primary = pd.read_csv(tmp_path / "comparison" / "statistics" / "annual_primary_generation_by_technology_and_scenario.csv")
    assert set(primary["scenario"]) == {"Slow", "Base", "High"}
    assert "BESS" not in set(primary["display_technology"])
    assert "PHS" not in set(primary["display_technology"])
    for name in (
        "comparison_installed_capacity_by_technology",
        "comparison_annual_primary_generation_by_technology",
        "comparison_annual_electricity_supply_by_technology",
        "comparison_national_electrical_balance",
        "comparison_average_zonal_prices",
        "comparison_annual_net_imports",
        "comparison_storage_phs_operation",
    ):
        assert (tmp_path / "comparison" / "figures" / f"{name}.png").is_file()
        assert (tmp_path / "comparison" / "figures" / f"{name}.svg").is_file()

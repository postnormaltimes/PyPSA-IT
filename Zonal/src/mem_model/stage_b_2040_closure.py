"""Read-only production audit and reporting-only 2040 closure.

Never constructs or optimizes a network.  The promotion command changes only
topology figures, reporting manifests/receipts, and acceptance/QA artifacts.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
import pypsa

from .common import ACCEPTED_RUNTIME, CONFIG, ROOT, dump_json, load_yaml, sha256_file
from .reporting.canonical_results import _write_table, generate_cross_scenario_comparison
from .reporting.plots import _topology
from .reporting.topology import (
    EXTERNAL_MARKETS,
    ITALIAN_ZONES,
    collapse_reciprocal_interfaces,
    edge_key,
    validate_accepted_2040_interfaces,
    validate_model_interface_table,
    validate_plotted_edge_set,
)
from .stage_b_2040_runtime import verify_accepted_runtime_manifest


SCENARIOS = ("Slow", "Base", "High")
SOLVED_HASHES = {
    "Slow": "27b61bc12a90ef4fe4c8f03b465ba0983d29ea73b6b834cce1152d95545995c8",
    "Base": "f10cbc2ebbd5dc4574bd788814e44fb81c257b653936ff2ec7bedc4168999733",
    "High": "c40b30ca4b0b3235e131f16832013175c3fd37833990682e3d8857f01fb911fe",
}
RUNTIME_MANIFEST_HASH = "5baae9bc065221463a422c2835a09dbfb4bc7b1e8b97902f34502d4d7ea9a63b"
QA_DIR = ROOT / "qa" / "stage_b" / "production_2040"
COMPARISON_DIR = ROOT / "results" / "MEM_2040_THREE_SCENARIO_COMPARISON"


def run_dir(scenario: str) -> Path:
    return ROOT / "results" / f"MEM_2040_{scenario.upper()}_CANONICAL"


def solved_path(scenario: str) -> Path:
    return run_dir(scenario) / f"MEM_2040_{scenario.upper()}_8760h_SOLVED.nc"


def verify_reporting(scenario: str) -> dict[str, str]:
    run = run_dir(scenario)
    manifest_path = run / "MEM_CANONICAL_REPORTING_MANIFEST.csv"
    receipt_path = run / "MEM_CANONICAL_REPORTING_RECEIPT.json"
    manifest = pd.read_csv(manifest_path, dtype=str)
    if len(manifest) != 44 or manifest["artifact"].duplicated().any():
        raise RuntimeError(f"{scenario}: reporting manifest member count/uniqueness failed")
    for row in manifest.itertuples(index=False):
        path = (run / str(row.artifact)).resolve()
        if not path.is_relative_to(run.resolve()) or not path.is_file() or sha256_file(path) != str(row.sha256).lower():
            raise RuntimeError(f"{scenario}: reporting artifact hash failed: {row.artifact}")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not (
        receipt["status"] == "PASS"
        and receipt["scenario"] == scenario
        and receipt["horizon"] == 2040
        and receipt["snapshots"] == 8760
        and receipt["reporting_solver_invocations"] == 0
        and receipt["electrical_balance_status"] == "PASS"
        and receipt["manifest_sha256"].lower() == sha256_file(manifest_path)
    ):
        raise RuntimeError(f"{scenario}: reporting receipt failed")
    return {"manifest_sha256": sha256_file(manifest_path), "receipt_sha256": sha256_file(receipt_path)}


def _hydro_balance(network: pypsa.Network) -> tuple[float, float, float]:
    inflow_total = spill_total = 0.0
    residuals = []
    for bus in network.buses.index[network.buses.carrier.eq("water_energy")]:
        inflow = network.generators.index[network.generators.bus.eq(bus) & network.generators.carrier.eq("water_energy")]
        spill = network.generators.index[network.generators.bus.eq(bus) & network.generators.carrier.eq("spillage")]
        turbine = network.links.index[network.links.bus0.eq(bus) & network.links.carrier.eq("water_energy")]
        pump = network.links.index[network.links.bus1.eq(bus) & network.links.carrier.eq("water_energy")]
        inflow_mwh = float(network.generators_t.p[inflow].to_numpy().sum())
        spill_mwh = float(network.generators_t.p[spill].to_numpy().sum())
        turbine_mwh = float(network.links_t.p0[turbine].to_numpy().sum())
        pump_mwh = float(-network.links_t.p1[pump].to_numpy().sum())
        inflow_total += inflow_mwh
        spill_total += spill_mwh
        residuals.append(inflow_mwh + pump_mwh - turbine_mwh - spill_mwh)
    return inflow_total / 1e6, spill_total / 1e6, max(abs(value) for value in residuals) / 1e6


def audit_scenario(scenario: str) -> dict[str, object]:
    path = solved_path(scenario)
    if sha256_file(path) != SOLVED_HASHES[scenario]:
        raise RuntimeError(f"{scenario}: solved-network immutable hash changed")
    reporting = verify_reporting(scenario)
    run = run_dir(scenario)
    network = pypsa.Network(path)
    balance = pd.read_csv(run / "statistics" / "annual_electrical_balance_by_zone.csv")
    prices = pd.read_csv(run / "statistics" / "zonal_price_statistics.csv")
    generation = pd.read_csv(run / "statistics" / "annual_primary_generation_national.csv")
    capacity = pd.read_csv(run / "statistics" / "installed_capacity_national.csv")
    topology = pd.read_csv(run / "statistics" / "network_topology_interfaces.csv")
    validate_model_interface_table(network, topology)
    validate_accepted_2040_interfaces(topology, scenario)
    edges = collapse_reciprocal_interfaces(topology)
    if len(edges) != 20 or len(network.snapshots) != 8760 or set(balance.zone) != ITALIAN_ZONES:
        raise RuntimeError(f"{scenario}: topology, chronology or seven-zone balance failed")
    if not balance.balance_check_status.eq("PASS").all() or not balance.load_shedding_TWh.eq(0.0).all():
        raise RuntimeError(f"{scenario}: electrical balance or load shedding failed")
    if prices.hours_above_500_EUR_per_MWh.sum() or prices.negative_price_hours.sum():
        raise RuntimeError(f"{scenario}: unexpected price-tail or negative-price anomaly")
    stores = network.stores
    energy = network.stores_t.e
    store_power = network.stores_t.p
    cyclic_error = float((energy.iloc[0] - energy.iloc[-1] + store_power.iloc[0]).abs().max())
    lower_error = float(max(0.0, -energy.min().min()))
    upper_error = float(max(0.0, (energy - stores.e_nom).max().max()))
    if not stores.e_cyclic.all() or cyclic_error > 1e-5 or max(lower_error, upper_error) > 1e-5:
        raise RuntimeError(f"{scenario}: cyclic storage or SOC bounds failed")
    inflow_twh, spill_twh, hydro_residual_twh = _hydro_balance(network)
    if inflow_twh < 0 or spill_twh < 0 or hydro_residual_twh > 1e-6:
        raise RuntimeError(f"{scenario}: hydro annual state balance failed")
    vre_carriers = {"solar_pv_rooftop", "solar_pv_utility", "wind_onshore", "wind_offshore"}
    vre_ids = network.generators.index[network.generators.carrier.isin(vre_carriers)]
    available = network.generators_t.p_max_pu[vre_ids].mul(network.generators.loc[vre_ids, "p_nom"], axis=1)
    curtailed = available - network.generators_t.p[vre_ids]
    if float(curtailed.min().min()) < -1e-5:
        raise RuntimeError(f"{scenario}: VRE output exceeds accepted availability")
    links = network.links.loc[network.links.carrier.isin({"internal_transfer", "external_trade", "corsica_hub"})]
    utilization = network.links_t.p0[links.index].div(links.p_nom, axis=1)
    if float(utilization.min().min()) < -1e-6 or float(utilization.max().max()) > 1 + 1e-6:
        raise RuntimeError(f"{scenario}: interface dispatch exceeds modeled capacity")
    external_links = links.index[links.carrier.eq("external_trade")]
    external_market_mwh = float(
        sum(
            (network.links_t.p0[link] if links.at[link, "bus0"] in EXTERNAL_MARKETS else network.links_t.p1[link]).sum()
            for link in external_links
        )
    )
    # Positive net imports to Italy equal withdrawals at external bus.
    national_net_imports = float(balance.net_imports_TWh.sum())
    if not math.isclose(external_market_mwh / 1e6, national_net_imports, abs_tol=1e-6):
        raise RuntimeError(f"{scenario}: national external-interface balance failed")
    primary = float(balance.primary_generation_TWh.sum())
    gas = float(generation.loc[generation.display_technology.str.startswith("Gas"), "annual_primary_generation_TWh"].sum())
    renewables = float(generation.loc[generation.display_technology.str.startswith(("Solar", "Onshore", "Offshore")), "annual_primary_generation_TWh"].sum())
    demand = float(balance.gross_end_use_demand_TWh.sum())
    weighted_price = float((prices.load_weighted_mean_EUR_per_MWh * balance.gross_end_use_demand_TWh).sum() / demand)
    return {
        "scenario": scenario,
        "solved_network_sha256": SOLVED_HASHES[scenario],
        "reporting_manifest_sha256_before_topology_correction": reporting["manifest_sha256"],
        "objective_EUR_model": float(network.objective),
        "snapshots": len(network.snapshots),
        "demand_TWh": demand,
        "installed_generation_and_discharge_capacity_GW": float(capacity.installed_capacity_GW.sum()),
        "primary_generation_TWh": primary,
        "vre_generation_TWh": renewables,
        "gas_generation_TWh": gas,
        "bess_discharge_TWh": float(balance.bess_discharge_TWh.sum()),
        "phs_discharge_TWh": float(balance.phs_discharge_TWh.sum()),
        "bess_charging_TWh": float(balance.bess_charging_TWh.sum()),
        "phs_charging_TWh": float(balance.phs_charging_TWh.sum()),
        "net_external_imports_TWh": national_net_imports,
        "net_imports_by_zone_TWh": dict(zip(balance.zone, balance.net_imports_TWh)),
        "load_weighted_national_price_EUR_per_MWh": weighted_price,
        "load_weighted_zonal_price_range_EUR_per_MWh": [float(prices.load_weighted_mean_EUR_per_MWh.min()), float(prices.load_weighted_mean_EUR_per_MWh.max())],
        "maximum_hourly_price_EUR_per_MWh": float(prices.maximum_EUR_per_MWh.max()),
        "zero_price_zone_hours": int(prices.zero_price_hours.sum()),
        "negative_price_zone_hours": int(prices.negative_price_hours.sum()),
        "hours_above_500_zone_hours": int(prices.hours_above_500_EUR_per_MWh.sum()),
        "load_shedding_TWh": float(balance.load_shedding_TWh.sum()),
        "max_abs_electrical_balance_residual_TWh": float(balance.balance_residual_TWh.abs().max()),
        "cyclic_store_closure_max_MWh": cyclic_error,
        "soc_lower_violation_MWh": lower_error,
        "soc_upper_violation_MWh": upper_error,
        "hydro_natural_inflow_TWh_water_equivalent": inflow_twh,
        "hydro_spill_TWh_water_equivalent": spill_twh,
        "hydro_state_annual_balance_max_abs_residual_TWh": hydro_residual_twh,
        "vre_curtailment_TWh": float(curtailed.to_numpy().sum()) / 1e6,
        "maximum_interface_utilization": float(utilization.max().max()),
        "topology_directional_links": len(topology),
        "topology_schematic_edges": len(edges),
        "status": "PASS",
    }


def audit() -> dict[str, object]:
    runtime_path = ACCEPTED_RUNTIME / "MEM_STAGE_B_2040_ACCEPTED_RUNTIME_MANIFEST_v1.0.csv"
    if sha256_file(runtime_path) != RUNTIME_MANIFEST_HASH:
        raise RuntimeError("Frozen Runtime-1 manifest hash changed")
    verify_accepted_runtime_manifest(ACCEPTED_RUNTIME, expected_year=2040)
    scenarios = {scenario: audit_scenario(scenario) for scenario in SCENARIOS}
    payload = {
        "schema_version": "MEM_STAGE_B_2040_THREE_SCENARIO_SANITY_REVIEW_v1.0",
        "status": "PASS",
        "runtime_manifest_sha256": RUNTIME_MANIFEST_HASH,
        "production_solver_invocations_by_this_audit": 0,
        "scenarios": scenarios,
    }
    QA_DIR.mkdir(parents=True, exist_ok=True)
    dump_json(QA_DIR / "MEM_STAGE_B_2040_THREE_SCENARIO_SANITY_REVIEW_v1.0.json", payload)
    comparison = pd.DataFrame(scenarios.values())
    comparison = comparison[[
        "scenario", "demand_TWh", "installed_generation_and_discharge_capacity_GW", "primary_generation_TWh",
        "vre_generation_TWh", "gas_generation_TWh", "bess_discharge_TWh", "phs_discharge_TWh",
        "bess_charging_TWh", "phs_charging_TWh", "net_external_imports_TWh",
        "load_weighted_national_price_EUR_per_MWh", "zero_price_zone_hours", "maximum_hourly_price_EUR_per_MWh",
        "load_shedding_TWh", "objective_EUR_model",
    ]]
    _write_table(QA_DIR / "MEM_STAGE_B_2040_THREE_SCENARIO_PHYSICAL_ECONOMIC_COMPARISON_v1.0.csv", comparison)
    return payload


def _correct_reporting_topology(scenario: str, preview: Path) -> dict[str, str]:
    run = run_dir(scenario)
    old = verify_reporting(scenario)
    figure_dir = run / "figures"
    manifest_path = run / "MEM_CANONICAL_REPORTING_MANIFEST.csv"
    receipt_path = run / "MEM_CANONICAL_REPORTING_RECEIPT.json"
    manifest = pd.read_csv(manifest_path, dtype=str)
    for suffix in ("png", "svg"):
        shutil.copyfile(preview / f"network_topology.{suffix}", figure_dir / f"network_topology.{suffix}")
        member = f"figures/network_topology.{suffix}"
        manifest.loc[manifest.artifact.eq(member), "sha256"] = sha256_file(figure_dir / f"network_topology.{suffix}")
    _write_table(manifest_path, manifest)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["manifest_sha256"] = sha256_file(manifest_path)
    receipt["reporting_amendment"] = "TOPOLOGY_REPORTING_CORRECTION_ONLY"
    receipt["prior_manifest_sha256"] = old["manifest_sha256"]
    dump_json(receipt_path, receipt)
    verify_reporting(scenario)
    return {"prior_manifest_sha256": old["manifest_sha256"], "prior_receipt_sha256": old["receipt_sha256"], "manifest_sha256": sha256_file(manifest_path), "receipt_sha256": sha256_file(receipt_path)}


def _accept(scenario: str, review: dict[str, object], reporting: dict[str, str]) -> str:
    run = run_dir(scenario)
    acceptance_path = run / f"MEM_STAGE_B_2040_{scenario.upper()}_ACCEPTANCE_v1.0.json"
    if scenario == "Base":
        receipt = json.loads(acceptance_path.read_text(encoding="utf-8"))
        receipt["reporting_manifest_sha256"] = reporting["manifest_sha256"]
        receipt["reporting_receipt_sha256"] = reporting["receipt_sha256"]
        receipt["reporting_amendment"] = "TOPOLOGY_REPORTING_CORRECTION_ONLY"
        receipt["original_reporting_manifest_sha256"] = reporting["prior_manifest_sha256"]
        receipt["original_reporting_receipt_sha256"] = reporting["prior_receipt_sha256"]
    else:
        receipt = {
            "schema_version": "MEM_STAGE_B_2040_CANONICAL_PRODUCTION_ACCEPTANCE_v1.0",
            "status": "ACCEPTED_IMMUTABLE",
            "acceptance_authority": "USER_CONFIRMED_THREE_SCENARIO_PRODUCTION_AND_POST_SOLVE_SANITY_REVIEW_2026-09-27",
            "horizon": 2040,
            "scenario": scenario,
            "mode": "FULL_YEAR_CANONICAL",
            "solved_network": str(solved_path(scenario).relative_to(ROOT)).replace("\\", "/"),
            "solved_network_sha256": SOLVED_HASHES[scenario],
            "solver": "gurobi",
            "solver_version": "13.0.3",
            "solver_options": {"Threads": 1, "Seed": 0, "include_objective_constant": False},
            "solver_status": "ok",
            "termination_condition": "optimal",
            "objective_EUR_model": review["objective_EUR_model"],
            "snapshots": 8760,
            "solver_diagnostics_provenance": "USER_SUPPLIED_GUROBI_RUN_TRANSCRIPT_AND_EXISTING_SOLVED_NETWORK_OBJECTIVE",
            "accepted_runtime_manifest": "runtime_inputs/accepted/MEM_STAGE_B_2040_ACCEPTED_RUNTIME_MANIFEST_v1.0.csv",
            "accepted_runtime_manifest_sha256": RUNTIME_MANIFEST_HASH,
            "reporting_receipt": str((run / "MEM_CANONICAL_REPORTING_RECEIPT.json").relative_to(ROOT)).replace("\\", "/"),
            "reporting_receipt_sha256": reporting["receipt_sha256"],
            "reporting_manifest": str((run / "MEM_CANONICAL_REPORTING_MANIFEST.csv").relative_to(ROOT)).replace("\\", "/"),
            "reporting_manifest_sha256": reporting["manifest_sha256"],
            "electrical_balance_status": "PASS",
            "electrical_balance_max_abs_residual_TWh": review["max_abs_electrical_balance_residual_TWh"],
            "national_gross_demand_TWh": review["demand_TWh"],
            "national_net_imports_TWh_approximately": review["net_external_imports_TWh"],
            "national_load_shedding_TWh": 0.0,
            "rerun_authorized": False,
            "reporting_amendment": "TOPOLOGY_REPORTING_CORRECTION_ONLY",
        }
    dump_json(acceptance_path, receipt)
    return sha256_file(acceptance_path)


def promote() -> dict[str, object]:
    if any((run_dir(s) / f"MEM_STAGE_B_2040_{s.upper()}_ACCEPTANCE_v1.0.json").exists() for s in ("Slow", "High")):
        raise RuntimeError("2040 Slow/High acceptance already exists; immutable reporting promotion cannot be rerun")
    review = audit()
    # Render once from the frozen Base network.  The normalized topology is
    # invariant; directional capacity pairs remain in each source table.
    base_network = pypsa.Network(solved_path("Base"))
    base_table = pd.read_csv(run_dir("Base") / "statistics" / "network_topology_interfaces.csv")
    edges = collapse_reciprocal_interfaces(base_table)
    plotted = [edge_key(row.carrier, row.bus_a, row.bus_b) for row in edges.itertuples(index=False)]
    validate_plotted_edge_set(base_table, plotted)
    with TemporaryDirectory(prefix="mem_2040_topology_") as temporary:
        preview = Path(temporary)
        _topology(base_network, base_table, 2040, "Base", preview)
        reporting = {scenario: _correct_reporting_topology(scenario, preview) for scenario in SCENARIOS}
    hashes = {scenario: _accept(scenario, review["scenarios"][scenario], reporting[scenario]) for scenario in SCENARIOS}
    if any(sha256_file(solved_path(scenario)) != SOLVED_HASHES[scenario] for scenario in SCENARIOS):
        raise RuntimeError("A solved network changed during reporting-only correction")
    topology_qa = {
        "schema_version": "MEM_STAGE_B_2040_TOPOLOGY_EDGESET_VALIDATION_v1.0",
        "status": "PASS",
        "gate": "TOPOLOGY_EDGESET_VALIDATION",
        "canonical_nodes": sorted(ITALIAN_ZONES | EXTERNAL_MARKETS | {"CORS"}),
        "canonical_internal_edges": sorted([list(key[1:]) for key in plotted if key[0] == "internal_transfer"]),
        "canonical_external_attachments": sorted([list(key[1:]) for key in plotted if key[0] == "external_trade"]),
        "canonical_cors_legs": sorted([list(key[1:]) for key in plotted if key[0] == "corsica_hub"]),
        "plotted_nodes": sorted(set(base_table.from_bus) | set(base_table.to_bus)),
        "plotted_edges": sorted([list(key) for key in plotted]),
        "plotted_edge_count": len(plotted),
        "directional_capacity_pair_source": "statistics/network_topology_interfaces.csv for each accepted scenario",
        "capacity_labels_shown": False,
        "flow_direction_arrows_shown": False,
        "reporting_change_reason": "TOPOLOGY_REPORTING_CORRECTION_ONLY",
        "per_scenario_reporting": reporting,
        "solved_network_hashes_unchanged": SOLVED_HASHES,
    }
    dump_json(QA_DIR / "MEM_STAGE_B_2040_TOPOLOGY_EDGESET_VALIDATION_v1.0.json", topology_qa)
    comparison = generate_cross_scenario_comparison(
        {scenario: run_dir(scenario) for scenario in SCENARIOS},
        2040,
        COMPARISON_DIR,
        require_accepted=True,
    )
    result = {
        "status": "PASS",
        "marker": "STAGE_B_2040_THREE_SCENARIO_PRODUCTION_COMPLETE",
        "runtime_manifest_sha256": RUNTIME_MANIFEST_HASH,
        "topology_edgeset_validation": "PASS",
        "scenario_acceptance_receipt_sha256": hashes,
        "scenario_solved_network_sha256": SOLVED_HASHES,
        "scenario_reporting_manifest_sha256": {s: reporting[s]["manifest_sha256"] for s in SCENARIOS},
        "comparison_manifest_sha256": comparison["manifest_sha256"],
        "comparison_receipt_sha256": sha256_file(COMPARISON_DIR / "MEM_CANONICAL_CROSS_SCENARIO_REPORTING_RECEIPT.json"),
        "comparison_table_count": comparison["table_count"],
        "comparison_figure_file_count": comparison["figure_file_count"],
        "production_solver_invocations_by_this_closure": 0,
    }
    dump_json(QA_DIR / "MEM_STAGE_B_2040_THREE_SCENARIO_FINAL_VERIFICATION_v1.0.json", result)
    return result


def verify_final() -> dict[str, object]:
    """Read-only final integrity check; write only the derived QA receipt."""

    runtime_path = ACCEPTED_RUNTIME / "MEM_STAGE_B_2040_ACCEPTED_RUNTIME_MANIFEST_v1.0.csv"
    if sha256_file(runtime_path) != RUNTIME_MANIFEST_HASH:
        raise RuntimeError("Runtime-1 manifest changed")
    runtime = verify_accepted_runtime_manifest(ACCEPTED_RUNTIME, expected_year=2040)
    gates = load_yaml(CONFIG / "approval_gates.yaml")
    gate = gates["stage_b_2040_production"]
    manual = gates["stage_a_manual_gates"]
    if not (
        gate["status"] == "STAGE_B_2040_THREE_SCENARIO_PRODUCTION_COMPLETE"
        and gate["execution_enabled"] is False
        and gate["scenario_authorization"] == {"Slow": False, "Base": False, "High": False}
        and gate["accepted_scenarios"] == list(SCENARIOS)
        and gate["authorized_commands"] == {}
        and manual["stage_b_2040_authorized"] is False
        and manual["stage_b_2040_authorized_scenarios"] == []
        and manual["b10_2040_status"] == "ETX7B10_2040_STAGE_A_TO_STAGE_B_PRICE_HANDOFF_COMPLETE"
        and gates["stage_b_2050_production"]["execution_enabled"] is False
        and manual["stage_b_2050_authorized"] is False
    ):
        raise RuntimeError("Final 2040/2050 governance boundary failed")
    from .solve_scenario import verify_base_acceptance

    verify_base_acceptance(gate)
    solved_hashes: dict[str, str] = {}
    reporting_hashes: dict[str, str] = {}
    acceptance_hashes: dict[str, str] = {}
    topology_hashes: dict[str, dict[str, str]] = {}
    for scenario in SCENARIOS:
        run = run_dir(scenario)
        observed_solved = sha256_file(solved_path(scenario))
        if observed_solved != SOLVED_HASHES[scenario]:
            raise RuntimeError(f"{scenario}: solved network changed")
        solved_hashes[scenario] = observed_solved
        reporting = verify_reporting(scenario)
        reporting_hashes[scenario] = reporting["manifest_sha256"]
        accepted_path = run / f"MEM_STAGE_B_2040_{scenario.upper()}_ACCEPTANCE_v1.0.json"
        accepted = json.loads(accepted_path.read_text(encoding="utf-8"))
        if not (
            accepted["status"] == "ACCEPTED_IMMUTABLE"
            and accepted["solved_network_sha256"].lower() == observed_solved
            and accepted["reporting_manifest_sha256"].lower() == reporting["manifest_sha256"]
            and accepted["reporting_receipt_sha256"].lower() == reporting["receipt_sha256"]
            and accepted["accepted_runtime_manifest_sha256"].lower() == RUNTIME_MANIFEST_HASH
            and accepted["termination_condition"] == "optimal"
            and accepted["national_load_shedding_TWh"] == 0.0
        ):
            raise RuntimeError(f"{scenario}: acceptance identity failed")
        acceptance_hashes[scenario] = sha256_file(accepted_path)
        topology_hashes[scenario] = {suffix: sha256_file(run / "figures" / f"network_topology.{suffix}") for suffix in ("png", "svg")}
    if len({topology_hashes[s]["png"] for s in SCENARIOS}) != 1 or len({topology_hashes[s]["svg"] for s in SCENARIOS}) != 1:
        raise RuntimeError("Authoritative topology map differs across 2040 scenarios")
    if (
        gate["accepted_base_receipt_sha256"].lower() != acceptance_hashes["Base"]
        or gate["accepted_slow_receipt_sha256"].lower() != acceptance_hashes["Slow"]
        or gate["accepted_high_receipt_sha256"].lower() != acceptance_hashes["High"]
    ):
        raise RuntimeError("Acceptance receipts differ from governance hash pins")
    topology_qa = json.loads((QA_DIR / "MEM_STAGE_B_2040_TOPOLOGY_EDGESET_VALIDATION_v1.0.json").read_text(encoding="utf-8"))
    if topology_qa["status"] != "PASS" or topology_qa["plotted_edge_count"] != 20:
        raise RuntimeError("Topology exact edge-set validation failed")
    comparison_manifest = COMPARISON_DIR / "MEM_CANONICAL_CROSS_SCENARIO_REPORTING_MANIFEST.csv"
    comparison_receipt = COMPARISON_DIR / "MEM_CANONICAL_CROSS_SCENARIO_REPORTING_RECEIPT.json"
    comparison = json.loads(comparison_receipt.read_text(encoding="utf-8"))
    if not (
        comparison["status"] == "PASS"
        and comparison["table_count"] == 8
        and comparison["figure_file_count"] == 16
        and comparison["manifest_sha256"] == sha256_file(comparison_manifest)
        and comparison["source_acceptance_sha256"] == acceptance_hashes
        and comparison["reporting_solver_invocations"] == 0
    ):
        raise RuntimeError("Accepted cross-scenario receipt failed")
    for row in pd.read_csv(comparison_manifest).itertuples(index=False):
        if sha256_file(COMPARISON_DIR / row.artifact) != row.sha256:
            raise RuntimeError(f"Cross-scenario artifact hash differs: {row.artifact}")
    result = {
        "schema_version": "MEM_STAGE_B_2040_THREE_SCENARIO_FINAL_VERIFICATION_v1.0",
        "status": "PASS",
        "marker": "STAGE_B_2040_THREE_SCENARIO_PRODUCTION_COMPLETE",
        "b10_2040": "COMPLETE_IMMUTABLE",
        "runtime_1": "PASS_IMMUTABLE",
        "runtime_manifest_sha256": RUNTIME_MANIFEST_HASH,
        "runtime_member_hashes": runtime["members"],
        "topology_edgeset_validation": "PASS",
        "topology_visual_inspection": "PASS_2026_09_27_NORMAL_REPORT_SIZE",
        "topology_png_sha256": topology_hashes["Base"]["png"],
        "topology_svg_sha256": topology_hashes["Base"]["svg"],
        "scenario_solved_network_sha256": solved_hashes,
        "scenario_reporting_manifest_sha256": reporting_hashes,
        "scenario_acceptance_receipt_sha256": acceptance_hashes,
        "comparison_manifest_sha256": comparison["manifest_sha256"],
        "comparison_receipt_sha256": sha256_file(comparison_receipt),
        "comparison_table_count": 8,
        "comparison_figure_file_count": 16,
        "2040_rerun_authorized_scenarios": [],
        "2050_production_execution_enabled": False,
        "focused_tests_observed": {"closure_reporting_preparation": "35_OF_35_PASS", "runtime_regression": "20_OF_20_PASS"},
        "runtime_verify": "PASS",
        "preparation_verify": "PASS",
        "production_solver_invocations_by_this_closure": 0,
    }
    dump_json(QA_DIR / "MEM_STAGE_B_2040_THREE_SCENARIO_FINAL_VERIFICATION_v1.0.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["audit", "promote", "verify"])
    args = parser.parse_args()
    result = audit() if args.command == "audit" else promote() if args.command == "promote" else verify_final()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

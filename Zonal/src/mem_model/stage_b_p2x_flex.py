"""Versioned, build-only correction of Stage-B electric P2X demand.

The v1 accepted runtime bundles and all pre-correction solved results are
immutable diagnostic baselines. This module never invokes an optimizer.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pypsa

from .common import (
    ACCEPTED_RUNTIME,
    ACCEPTED_RUNTIME_2050,
    CONFIG,
    ROOT,
    SCENARIOS,
    STATIC,
    ZONES,
    dump_json,
    load_yaml,
    sha256_file,
)
from .network import build_network
from .stage_b_2040_runtime import _exact_scaled_profile, verify_accepted_runtime_manifest
from .stage_b_2050_runtime import verify_prepared_runtime_manifest


VERSION = "P2X_FLEX_V1"
CONFIG_PATH = CONFIG / "stage_b_p2x_flex_v1.yaml"
RUNTIME_ROOT = ROOT / "runtime_inputs" / "p2x_flex_v1"
NETWORK_ROOT = ROOT / "networks" / "unsolved" / "p2x_flex_v1"
QA_ROOT = ROOT / "qa" / "stage_b" / "p2x_flex_v1"
MANIFEST_NAME = "MEM_STAGE_B_P2X_FLEX_V1_RUNTIME_MANIFEST.csv"
STATE_NAME = "MEM_STAGE_B_P2X_FLEX_V1_STATE.json"
MEMBERS = (
    "load_hourly.parquet",
    "p2x_contract.csv",
    "generator_availability_hourly.parquet",
    "hydro_inflow_hourly.parquet",
    "hydro_runtime_parameters.csv",
    "external_prices_hourly.parquet",
)
UNCHANGED_MEMBERS = MEMBERS[2:]


def baseline_runtime(year: int) -> Path:
    if year == 2040:
        return ACCEPTED_RUNTIME
    if year == 2050:
        return ACCEPTED_RUNTIME_2050
    raise ValueError(f"Unsupported P2X-flex horizon: {year}")


def runtime_dir(year: int) -> Path:
    baseline_runtime(year)
    return RUNTIME_ROOT / str(year)


def _settings() -> dict[str, Any]:
    settings = load_yaml(CONFIG_PATH)
    if settings.get("version") != VERSION or settings.get("high_multiplier") != 1.10:
        raise RuntimeError("P2X-flex scenario contract changed")
    return settings


def scenario_totals(year: int, scenario: str) -> dict[str, float | str]:
    if scenario not in ("Slow", "Base", "High"):
        raise ValueError(f"Unsupported P2X-flex scenario: {scenario}")
    settings = _settings()
    base = settings["base_and_slow"][year]
    factor = float(settings["high_multiplier"]) if scenario == "High" else 1.0
    # DDS-2024 2040 DE-IT: 27.5 TWh / 7.4 GW = 3716.216216... h.
    full_load_hours = float(settings["base_and_slow"][2040]["p2x_TWh"]) * 1e6 / float(
        settings["base_and_slow"][2040]["p2x_power_MW"]
    )
    base_power = (
        float(base["p2x_power_MW"])
        if year == 2040
        else float(base["p2x_TWh"]) * 1e6 / full_load_hours
    )
    result: dict[str, float | str] = {
        "total_TWh": float(base["total_TWh"]) * factor,
        "rigid_TWh": float(base["rigid_TWh"]) * factor,
        "p2x_TWh": float(base["p2x_TWh"]) * factor,
        "p2x_power_MW": base_power * factor,
        "equivalent_full_load_hours": full_load_hours,
        "power_authority": (
            "DDS_2024_DE_IT_2040_ELECTROLYZER_CAPACITY"
            if year == 2040
            else "DERIVED_2040_UTILISATION_CARRY_FORWARD"
        ),
    }
    if not math.isclose(float(result["rigid_TWh"]) + float(result["p2x_TWh"]), float(result["total_TWh"]), abs_tol=1e-9):
        raise RuntimeError("Rigid plus P2X does not reconcile to scenario demand")
    return result


def _verify_baseline(year: int) -> dict[str, Any]:
    return (
        verify_accepted_runtime_manifest(ACCEPTED_RUNTIME, expected_year=2040)
        if year == 2040
        else verify_prepared_runtime_manifest(ACCEPTED_RUNTIME_2050, expected_year=2050)
    )


def _controls(year: int) -> pd.DataFrame:
    controls = pd.read_csv(STATIC / "MEM_Annual_Zonal_Demand_Contract.csv")
    controls = controls.loc[controls["year"].astype(int).eq(year)].copy()
    if len(controls) != 3 * len(ZONES) or controls.duplicated(["scenario", "zone"]).any():
        raise RuntimeError(f"Frozen {year} zonal demand controls are incomplete")
    return controls


def _corrected_load_and_contract(year: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    original = pd.read_parquet(baseline_runtime(year) / "load_hourly.parquet")
    corrected = original.copy()
    controls = _controls(year).set_index(["scenario", "zone"])
    records: list[dict[str, Any]] = []
    for scenario in ("Slow", "Base", "High"):
        totals = scenario_totals(year, scenario)
        expected_total = float(totals["total_TWh"]) * 1e6
        for zone in ZONES:
            row = controls.loc[(scenario, zone)]
            share = float(row["frozen_2025_share"])
            original_total = float(row["annual_zonal_demand_MWh"])
            if not math.isclose(original_total, expected_total * share, abs_tol=1e-5):
                raise RuntimeError(f"Frozen total demand/share mismatch: {year} {scenario} {zone}")
            mask = corrected["scenario"].eq(scenario) & corrected["zone"].eq(zone)
            chunk = corrected.loc[mask].sort_values("snapshot")
            if len(chunk) != 8760 or not math.isclose(float(chunk["load_MW"].sum()), original_total, abs_tol=1e-3):
                raise RuntimeError(f"Accepted baseline hourly load mismatch: {year} {scenario} {zone}")
            rigid = float(totals["rigid_TWh"]) * 1e6 * share
            corrected.loc[chunk.index, "load_MW"] = _exact_scaled_profile(chunk["load_MW"].to_numpy(), rigid)
            records.append(
                {
                    "year": year,
                    "scenario": scenario,
                    "zone": zone,
                    "frozen_2025_share": share,
                    "annual_total_MWh": original_total,
                    "annual_rigid_MWh": rigid,
                    "annual_p2x_MWh": float(totals["p2x_TWh"]) * 1e6 * share,
                    "p2x_power_MW": float(totals["p2x_power_MW"]) * share,
                    "equivalent_full_load_hours": float(totals["equivalent_full_load_hours"]),
                    "p2x_power_authority": str(totals["power_authority"]),
                    "formulation": "NEGATIVE_SIGN_GENERATOR|OPERATIONAL_LIMIT_EQUALITY",
                }
            )
    corrected["transformation"] = "P2X_FLEX_V1:ACCEPTED_2019_SHAPE_X_RIGID_ZONAL_CONTROL"
    return corrected, pd.DataFrame(records)


def verify_runtime(year: int) -> dict[str, Any]:
    baseline = _verify_baseline(year)
    folder = runtime_dir(year)
    manifest_path = folder / MANIFEST_NAME
    manifest = pd.read_csv(manifest_path, dtype=str)
    if len(manifest) != len(MEMBERS) or set(manifest["file"]) != set(MEMBERS) or manifest["file"].duplicated().any():
        raise RuntimeError(f"P2X-flex runtime manifest member set invalid: {year}")
    hashes: dict[str, str] = {}
    for row in manifest.itertuples(index=False):
        path = folder / str(row.file)
        if not path.is_file() or sha256_file(path).lower() != str(row.sha256).lower():
            raise RuntimeError(f"P2X-flex runtime hash mismatch: {year} {row.file}")
        hashes[str(row.file)] = sha256_file(path)
    for member in UNCHANGED_MEMBERS:
        if hashes[member].lower() != sha256_file(baseline_runtime(year) / member).lower():
            raise RuntimeError(f"Non-demand runtime member changed: {year} {member}")
    load = pd.read_parquet(folder / "load_hourly.parquet")
    contract = pd.read_csv(folder / "p2x_contract.csv")
    original = pd.read_parquet(baseline_runtime(year) / "load_hourly.parquet")
    if len(load) != 3 * 7 * 8760 or len(contract) != 21 or load.duplicated(["snapshot", "scenario", "zone"]).any():
        raise RuntimeError(f"P2X-flex load/contract grain invalid: {year}")
    expected_keys = {(scenario, zone) for scenario in ("Slow", "Base", "High") for zone in ZONES}
    if set(zip(contract["scenario"], contract["zone"])) != expected_keys or contract.duplicated(["scenario", "zone"]).any():
        raise RuntimeError(f"P2X-flex scenario-zone crosswalk invalid: {year}")
    if set(load["year"].astype(int)) != {year} or set(contract["year"].astype(int)) != {year}:
        raise RuntimeError(f"P2X-flex runtime horizon leakage: {year}")
    controls = _controls(year).set_index(["scenario", "zone"])
    checks = []
    for row in contract.itertuples(index=False):
        totals = scenario_totals(year, row.scenario)
        share = float(controls.at[(row.scenario, row.zone), "frozen_2025_share"])
        if not (
            math.isclose(float(row.frozen_2025_share), share, abs_tol=1e-12)
            and math.isclose(float(row.annual_total_MWh), float(totals["total_TWh"]) * 1e6 * share, abs_tol=1e-5)
            and math.isclose(float(row.annual_rigid_MWh), float(totals["rigid_TWh"]) * 1e6 * share, abs_tol=1e-5)
            and math.isclose(float(row.annual_p2x_MWh), float(totals["p2x_TWh"]) * 1e6 * share, abs_tol=1e-5)
            and math.isclose(float(row.p2x_power_MW), float(totals["p2x_power_MW"]) * share, abs_tol=1e-7)
            and row.p2x_power_authority == totals["power_authority"]
        ):
            raise RuntimeError(f"P2X-flex frozen scenario/share control mismatch: {year} {row.scenario} {row.zone}")
        key = (load["scenario"].eq(row.scenario) & load["zone"].eq(row.zone))
        old = original.loc[key].sort_values("snapshot")
        new = load.loc[key].sort_values("snapshot")
        if not old["snapshot"].reset_index(drop=True).equals(new["snapshot"].reset_index(drop=True)):
            raise RuntimeError("Accepted 2019 chronology changed")
        observed = float(new["load_MW"].sum())
        expected = float(row.annual_rigid_MWh)
        ratio = new["load_MW"].to_numpy() / old["load_MW"].to_numpy()
        if (
            len(new) != 8760
            or not math.isclose(observed, expected, abs_tol=1e-5)
            or not math.isclose(expected + float(row.annual_p2x_MWh), float(row.annual_total_MWh), abs_tol=1e-5)
            or not np.allclose(ratio, expected / float(row.annual_total_MWh), atol=1e-10, rtol=1e-10)
            or float(row.annual_p2x_MWh) / float(row.p2x_power_MW) > 8760
        ):
            raise RuntimeError(f"P2X-flex demand/cap/shape QA failed: {year} {row.scenario} {row.zone}")
        checks.append({"year": year, "scenario": row.scenario, "zone": row.zone, "rigid_MWh": observed,
                       "p2x_MWh": float(row.annual_p2x_MWh), "power_MW": float(row.p2x_power_MW), "status": "PASS"})
    return {"status": "PASS", "year": year, "baseline_manifest_sha256": baseline["manifest_sha256"],
            "manifest_sha256": sha256_file(manifest_path), "members": hashes, "checks": checks}


def build_runtime(year: int) -> dict[str, Any]:
    baseline = _verify_baseline(year)
    folder = runtime_dir(year)
    if folder.exists():
        return verify_runtime(year)
    corrected, contract = _corrected_load_and_contract(year)
    folder.mkdir(parents=True)
    corrected.to_parquet(folder / "load_hourly.parquet", index=False)
    contract.to_csv(folder / "p2x_contract.csv", index=False, lineterminator="\n", float_format="%.15g")
    for member in UNCHANGED_MEMBERS:
        shutil.copyfile(baseline_runtime(year) / member, folder / member)
    records = []
    for member in MEMBERS:
        path = folder / member
        records.append({"file": member, "sha256": sha256_file(path),
                        "authority": "P2X_FLEX_V1_DERIVED" if member in MEMBERS[:2] else "BYTE_IDENTICAL_ACCEPTED_BASELINE"})
    pd.DataFrame(records).to_csv(folder / MANIFEST_NAME, index=False, lineterminator="\n")
    verified = verify_runtime(year)
    if baseline["manifest_sha256"] != _verify_baseline(year)["manifest_sha256"]:
        raise RuntimeError("Accepted baseline manifest changed during P2X build")
    return verified


def _old_unsolved_path(year: int, scenario: str) -> Path:
    folder = "accepted" if year == 2040 else "accepted_2050"
    return ROOT / "networks" / "unsolved" / folder / f"MEM_{year}_{scenario.upper()}_8760h_UNSOLVED.nc"


def network_path(year: int, scenario: str) -> Path:
    return NETWORK_ROOT / f"MEM_{year}_{scenario.upper()}_P2X_FLEX_V1_8760h_UNSOLVED.nc"


def _add_p2x(network: pypsa.Network, year: int, scenario: str) -> None:
    contract = pd.read_csv(runtime_dir(year) / "p2x_contract.csv")
    contract = contract.loc[contract["scenario"].eq(scenario)]
    if len(contract) != len(ZONES) or set(contract["zone"]) != set(ZONES):
        raise RuntimeError("P2X scenario-zone contract incomplete")
    for row in contract.itertuples(index=False):
        carrier = f"p2x_flexible_{row.zone}"
        network.add("Carrier", carrier)
        network.add("Generator", f"P2X_{row.zone}", bus=row.zone, carrier=carrier,
                    sign=-1.0, p_nom=float(row.p2x_power_MW), p_nom_extendable=False,
                    committable=False, p_min_pu=0.0, p_max_pu=1.0, marginal_cost=0.0)
        network.add("GlobalConstraint", f"P2X_ANNUAL_{row.zone}", type="operational_limit",
                    carrier_attribute=carrier, sense="==", constant=float(row.annual_p2x_MWh))


def build_p2x_network(year: int, scenario: str) -> tuple[pypsa.Network, dict[str, Any]]:
    verified = verify_runtime(year)
    network, metadata = build_network(runtime_dir(year).resolve(), year, scenario)
    _add_p2x(network, year, scenario)
    network.meta.update({"model_version": VERSION, "p2x_runtime_manifest_sha256": verified["manifest_sha256"],
                         "baseline_status": "PRE_P2X_FLEX_DIAGNOSTIC_BASELINE", "p2x_formulation": "NEGATIVE_SIGN_GENERATOR_PER_ZONE_OPERATIONAL_LIMIT_EQUALITY",
                         "full_year_solve_authorized": False, "production_authorization": "MANUAL_ONLY_AFTER_BUILD_QA"})
    metadata.update({"model_version": VERSION, "p2x_runtime_manifest_sha256": verified["manifest_sha256"],
                     "p2x_zones": len(ZONES), "p2x_annual_TWh": sum(float(r["p2x_MWh"]) for r in verified["checks"] if r["scenario"] == scenario) / 1e6,
                     "optimizer_invocations": 0})
    return network, metadata


def verify_network(network: pypsa.Network, year: int, scenario: str) -> dict[str, Any]:
    old = pypsa.Network(_old_unsolved_path(year, scenario))
    expected = pd.read_csv(runtime_dir(year) / "p2x_contract.csv").query("scenario == @scenario").set_index("zone")
    if len(network.snapshots) != 8760 or not network.snapshots.equals(old.snapshots):
        raise RuntimeError("P2X network chronology changed")
    if not network.snapshot_weightings["generators"].eq(1.0).all():
        raise RuntimeError("P2X annual energy constraint requires the accepted unit-hour weighting")
    # ``sub_network`` is a derived label assigned when a network is prepared
    # for optimization, not a frozen bus or interface attribute.
    bus_columns = old.buses.columns.difference(["sub_network"])
    if not network.buses[bus_columns].equals(old.buses[bus_columns]) or set(network.links.index) != set(old.links.index) or set(network.stores.index) != set(old.stores.index):
        raise RuntimeError("Non-demand network topology or storage changed")
    if set(network.loads.index) != set(old.loads.index) or set(network.generators.index) != set(old.generators.index) | {f"P2X_{z}" for z in ZONES}:
        raise RuntimeError("P2X network component set changed unexpectedly")
    physical_generators = old.generators.index[~old.generators.index.astype(str).str.startswith("LOAD_SHEDDING_")]
    if not network.links.equals(old.links) or not network.stores.equals(old.stores) or not network.generators.loc[physical_generators].equals(old.generators.loc[physical_generators]):
        raise RuntimeError("Non-demand static components changed")
    if not network.snapshot_weightings.equals(old.snapshot_weightings) or not network.carriers.loc[old.carriers.index].equals(old.carriers):
        raise RuntimeError("Non-demand snapshot weighting or carrier authority changed")
    old_availability = old.generators_t.p_max_pu.columns.difference(
        [f"LOAD_SHEDDING_{zone}" for zone in ZONES]
    )
    if not network.generators_t.p_max_pu[old_availability].equals(old.generators_t.p_max_pu[old_availability]):
        raise RuntimeError("Accepted generator availability changed")
    for attribute in ("p_min_pu", "marginal_cost"):
        old_dynamic = getattr(old.generators_t, attribute)
        new_dynamic = getattr(network.generators_t, attribute)
        if not new_dynamic.reindex(columns=old_dynamic.columns).equals(old_dynamic):
            raise RuntimeError(f"Non-demand generator {attribute} changed")
    for attribute in ("p_max_pu", "p_min_pu", "marginal_cost"):
        old_dynamic = getattr(old.links_t, attribute)
        new_dynamic = getattr(network.links_t, attribute)
        if not new_dynamic.reindex(columns=old_dynamic.columns).equals(old_dynamic):
            raise RuntimeError(f"Non-demand link {attribute} changed")
    for zone in ZONES:
        name = f"P2X_{zone}"
        row = expected.loc[zone]
        gen = network.generators.loc[name]
        constraint = network.global_constraints.loc[f"P2X_ANNUAL_{zone}"]
        if not (
            gen.bus == zone and gen.carrier == f"p2x_flexible_{zone}" and gen.sign == -1.0
            and math.isclose(float(gen.p_nom), float(row.p2x_power_MW), abs_tol=1e-8)
            and gen.p_nom_extendable == False and gen.committable == False
            and gen.p_min_pu == 0 and gen.p_max_pu == 1
            and constraint.type == "operational_limit" and constraint.sense == "=="
            and constraint.carrier_attribute == gen.carrier
            and math.isclose(float(constraint.constant), float(row.annual_p2x_MWh), abs_tol=1e-6)
            and math.isclose(float(network.loads_t.p_set[f"LOAD_{zone}"].sum()), float(row.annual_rigid_MWh), abs_tol=1e-5)
        ):
            raise RuntimeError(f"P2X network constraint/rigid demand mismatch: {year} {scenario} {zone}")
    if network.generators.committable.any() or network.generators.p_nom_extendable.any() or network.links.p_nom_extendable.any() or network.stores.e_nom_extendable.any():
        raise RuntimeError("Corrected network is not a fixed-capacity continuous LP")
    return {"status": "PASS", "year": year, "scenario": scenario, "snapshots": 8760,
            "p2x_components": len(ZONES), "p2x_energy_equalities": len(ZONES), "integer_variables": 0,
            "binary_variables": 0, "non_demand_components_unchanged": True, "optimizer_invocations": 0}


def build_all() -> dict[str, Any]:
    previous_solved: dict[str, str] = {}
    for year, scenario in SCENARIOS:
        path = ROOT / "results" / f"MEM_{year}_{scenario.upper()}_CANONICAL" / f"MEM_{year}_{scenario.upper()}_8760h_SOLVED.nc"
        if not path.is_file():
            raise RuntimeError(f"Pre-P2X diagnostic baseline missing: {path}")
        previous_solved[f"{year}_{scenario}"] = sha256_file(path)
    runtimes = {str(year): build_runtime(year) for year in (2040, 2050)}
    NETWORK_ROOT.mkdir(parents=True, exist_ok=True)
    network_checks = []
    for year, scenario in SCENARIOS:
        path = network_path(year, scenario)
        if path.exists():
            raise RuntimeError(f"Versioned unsolved network already exists; refusing overwrite: {path}")
        network, metadata = build_p2x_network(year, scenario)
        check = verify_network(network, year, scenario)
        network.export_to_netcdf(path)
        if sha256_file(path) == "":
            raise RuntimeError("Unsolved network export failed")
        metadata.update(check)
        metadata["network_sha256"] = sha256_file(path)
        dump_json(path.with_suffix(".json"), metadata)
        network_checks.append({**check, "network": str(path.relative_to(ROOT)), "sha256": metadata["network_sha256"]})
    for year, scenario in SCENARIOS:
        path = ROOT / "results" / f"MEM_{year}_{scenario.upper()}_CANONICAL" / f"MEM_{year}_{scenario.upper()}_8760h_SOLVED.nc"
        if sha256_file(path) != previous_solved[f"{year}_{scenario}"]:
            raise RuntimeError("Pre-P2X solved baseline changed during build")
    QA_ROOT.mkdir(parents=True, exist_ok=True)
    state = {"version": VERSION, "status": "READY_FOR_MANUAL_EXECUTION", "production_optimization_executed": False,
             "legacy_solved_status": "PRE_P2X_FLEX_DIAGNOSTIC_BASELINE", "legacy_solved_sha256": previous_solved,
             "runtime_manifests": {year: data["manifest_sha256"] for year, data in runtimes.items()},
             "runtime_baseline_manifests": {year: data["baseline_manifest_sha256"] for year, data in runtimes.items()},
             "network_checks": network_checks, "manual_only": True}
    dump_json(QA_ROOT / STATE_NAME, state)
    return state


def verify_all() -> dict[str, Any]:
    """Read-only final check of both corrected bundles and all six networks."""

    state = json.loads((QA_ROOT / STATE_NAME).read_text(encoding="utf-8"))
    if state.get("version") != VERSION or state.get("status") != "READY_FOR_MANUAL_EXECUTION" or state.get("production_optimization_executed") is not False:
        raise RuntimeError("P2X-flex final state invalid")
    runtimes = {str(year): verify_runtime(year) for year in (2040, 2050)}
    for year, data in runtimes.items():
        if data["manifest_sha256"] != state["runtime_manifests"].get(year):
            raise RuntimeError(f"Corrected runtime manifest changed: {year}")
    observed = []
    for year, scenario in SCENARIOS:
        key = f"{year}_{scenario}"
        old_solved = ROOT / "results" / f"MEM_{year}_{scenario.upper()}_CANONICAL" / f"MEM_{year}_{scenario.upper()}_8760h_SOLVED.nc"
        if sha256_file(old_solved) != state["legacy_solved_sha256"].get(key):
            raise RuntimeError(f"Pre-P2X solved baseline changed: {key}")
        path = network_path(year, scenario)
        pinned = next((row for row in state["network_checks"] if row["year"] == year and row["scenario"] == scenario), None)
        if pinned is None or sha256_file(path) != pinned["sha256"]:
            raise RuntimeError(f"Corrected unsolved network changed: {key}")
        check = verify_network(pypsa.Network(path), year, scenario)
        if check != {field: pinned[field] for field in check}:
            raise RuntimeError(f"Corrected unsolved network QA changed: {key}")
        observed.append({"year": year, "scenario": scenario, "status": "READY_FOR_MANUAL_EXECUTION"})
    return {"status": "PASS", "version": VERSION, "runtime_manifests": state["runtime_manifests"],
            "scenarios": observed, "legacy_solved_unchanged": True, "optimizer_invocations": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build-only Stage-B P2X-flex v1 runtime and unsolved networks")
    parser.add_argument("action", choices=("build", "verify"))
    args = parser.parse_args()
    if args.action == "build":
        print(json.dumps(build_all(), indent=2))
    else:
        print(json.dumps(verify_all(), indent=2))


if __name__ == "__main__":
    main()

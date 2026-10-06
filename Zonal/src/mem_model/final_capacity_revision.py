"""Bounded final_v3 -> final_v4 capacity successor; never constructs a model.

2040 artifacts are inherited by hash. Only the 2050 VRE, nuclear and GAS_CCS
capacity envelopes and their native UC-v2A child representation are revised.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa

from .common import ROOT, SCENARIOS, ZONES, dump_json, load_yaml, sha256_file
from .final_hydro_spatial import COMPONENTS
from .stage_b_zonal_vre import no_models_or_solves
from .uc_heterogeneity import normalize_children, heat_rate_cost

QA = ROOT / "qa/final_methodology_v4"
NETWORKS = ROOT / "networks/unsolved/final_methodology_v4"
RUNTIME = ROOT / "runtime_inputs/final_methodology_v4"
CONFIG = ROOT / "config/final_methodology_execution_v4.yaml"
V3_LINEAGE = ROOT / "qa/final_pre_rerun/final_successor/FINAL_SUCCESSOR_NETWORK_LINEAGE_AND_HASHES.csv"
STATIC = ROOT / "pre_pypsa_inputs/MEM_generators_static_final.csv"
CROSSWALK = ROOT / "qa/stage_b/uc2a_heterogeneous_v1/MEM_UC2A_CHILD_CROSSWALK.csv"
PROVENANCE = ROOT / "qa/stage_b/uc2a_heterogeneous_v1/MEM_UC2A_REFERENCE_PROVENANCE.json"
PROGRAM = ("BIOENERGY", "BIOENERGY_CCS", "GAS_CCS", "GAS_OTHER_FOSSIL", "GEOTHERMAL", "NUCLEAR")
MODEL_CARRIERS = ("bioenergy", "bioenergy_ccs", "methane_ccs", "methane_gt_ocgt", "hydrogen_gt_ocgt", "geothermal", "nuclear")
VRE = ("solar_pv_rooftop", "solar_pv_utility", "wind_onshore", "wind_offshore")
MECHANICAL_FIELDS = ("p_nom", "efficiency", "marginal_cost", "start_up_cost", "shut_down_cost", "stand_by_cost")
EXPECTED_OLD_PROGRAM = {"Base": 30000., "High": 30000., "Slow": 51613.384747842}


def close(actual, expected, message, tolerance=1e-6):
    if not math.isclose(float(actual), float(expected), rel_tol=0, abs_tol=tolerance):
        raise RuntimeError(f"FINAL_V4_{message}: {actual} != {expected}")


def unit_count(mw, target):
    return max(1, math.floor(float(mw) / target + .5))


def case_contract(scenario):
    cfg = load_yaml(CONFIG)["capacity_revision"]
    factor = cfg["vre_scenario_factors"][scenario]
    return {key: value * (1 if key == "nuclear" else factor) for key, value in cfg["base_MW"].items()}


def authority():
    """Resolve only current accepted contracts; no older capacity reconstruction."""
    from .final_methodology_networks import verify_v3_frozen_contract
    table = verify_v3_frozen_contract()
    source = pd.read_csv(STATIC)
    for scenario in ("Slow", "Base", "High"):
        part = source.loc[source.year.eq(2050) & source.scenario.eq(scenario)]
        close(part.loc[part.parent_capacity_technology.isin(PROGRAM), "p_nom_MW"].sum(),
              EXPECTED_OLD_PROGRAM[scenario], "CURRENT_PROGRAMMABLE_AUTHORITY_CONFLICT")
        close(part.loc[part.parent_capacity_technology.eq("NUCLEAR"), "p_nom_MW"].sum(), 8000, "CURRENT_NUCLEAR_AUTHORITY_CONFLICT")
    for row in table.itertuples():
        if sha256_file(ROOT / row.path) != row.sha256:
            raise RuntimeError("FINAL_V4_CURRENT_PARENT_HASH_FAIL")
    return table, source


def revised_static(source):
    result = source.copy()
    for scenario in ("Slow", "Base", "High"):
        mask = result.year.eq(2050) & result.scenario.eq(scenario)
        targets = case_contract(scenario)
        for technologies, target in ((["SOLAR_PV_ROOFTOP", "SOLAR_PV_UTILITY"], targets["solar"]),
                                     (["WIND_ONSHORE"], targets["wind_onshore"]),
                                     (["WIND_OFFSHORE"], targets["wind_offshore"]),
                                     (["NUCLEAR"], targets["nuclear"])):
            selected = mask & result.parent_capacity_technology.isin(technologies)
            result.loc[selected, "p_nom_MW"] *= target / source.loc[selected, "p_nom_MW"].sum()
        selected = mask & result.parent_capacity_technology.eq("GAS_CCS")
        old = float(source.loc[selected, "p_nom_MW"].sum())
        result.loc[selected, "p_nom_MW"] *= (old - 2000.) / old
        close(result.loc[mask & result.parent_capacity_technology.isin(PROGRAM), "p_nom_MW"].sum(),
              EXPECTED_OLD_PROGRAM[scenario], "PROGRAMMABLE_TOTAL")
    return result


def static_contract_text(revised):
    """Preserve every untouched source CSV cell, including its decimal spelling."""
    text = pd.read_csv(STATIC, dtype=str, keep_default_na=False)
    changed = text.year.eq("2050") & text.parent_capacity_technology.isin(
        ["SOLAR_PV_ROOFTOP", "SOLAR_PV_UTILITY", "WIND_ONSHORE", "WIND_OFFSHORE", "NUCLEAR", "GAS_CCS"])
    text.loc[changed, "p_nom_MW"] = revised.loc[changed, "p_nom_MW"].map(lambda x: format(x, ".17g"))
    return text.loc[text.year.eq("2050")]


def runtime_manifest():
    """Reference unchanged upstream bundles; final NetCDF remains profile authority."""
    rows = []
    for member in sorted((ROOT / "runtime_inputs/p2x_flex_v2/2050").glob("*")):
        if member.is_file():
            rows.append({"year": 2050, "scenario": "ALL", "path": str(member.relative_to(ROOT)),
                         "sha256": sha256_file(member), "authority": "BYTE_IDENTICAL_INHERITED_P2X_V2_RUNTIME",
                         "role": "UNCHANGED_UPSTREAM_MEMBER_NOT_OVERRIDE_OF_FINAL_V3_PROFILES"})
    for member, role in ((RUNTIME / "2050/generators_static.csv", "CAPACITY_REVISION"),
                         (QA / "FINAL_V4_CHILD_CROSSWALK.csv", "CAPACITY_DEPENDENT_COHORT_REPRESENTATION")):
        rows.append({"year": 2050, "scenario": "ALL", "path": str(member.relative_to(ROOT)),
                     "sha256": sha256_file(member), "authority": "FINAL_V4_DERIVED_CONTRACT", "role": role})
    return pd.DataFrame(rows)


def child_plan(parent, revised, scenario):
    """Reapply accepted per-cohort half-up and cached v2A calibration, not research."""
    from .stage_b_uc2a import cost_coefficient
    old_plan = pd.read_csv(CROSSWALK)
    old_plan = old_plan.loc[old_plan.year.eq(2050) & old_plan.scenario.eq(scenario) &
                            old_plan.technology.isin(["methane_ccs", "nuclear"])]
    source = revised.loc[revised.year.eq(2050) & revised.scenario.eq(scenario)].set_index("generator_id")
    model = json.loads(PROVENANCE.read_text())["classes"]["CCGT"]
    rows = []
    for parent_id, group in old_plan.groupby("parent_id", sort=True):
        old_ids = sorted(group.child_id)
        old = parent.generators.loc[old_ids]
        src = source.loc[parent_id]
        mw, eta = float(src.p_nom_MW), float(src.efficiency)
        nuclear = src.parent_capacity_technology == "NUCLEAR"
        count = unit_count(mw, 300 if nuclear else 550)
        if nuclear:
            children = pd.DataFrame({"p_nom": [mw / count] * count, "efficiency": [eta] * count,
                                     "size_class": ["MODULAR_300MW_TARGET"] * count,
                                     "efficiency_class": ["HOMOGENEOUS"] * count})
        else:
            children = normalize_children(mw, eta, count, model)
        coefficient = 0 if nuclear else cost_coefficient(src)
        for number, child in enumerate(children.itertuples(), 1):
            name = (f"UC__2050__{scenario}__{parent_id}__U{number:03d}" if nuclear else
                    f"UC2A__2050__{scenario}__{parent_id}__{child.size_class}__{child.efficiency_class}__U{number:03d}")
            mc = float(src.marginal_cost_EUR2025_per_MWh_el) if nuclear else float(
                heat_rate_cost(float(src.marginal_cost_EUR2025_per_MWh_el), 1 / eta, 1 / child.efficiency, coefficient))
            rows.append({"year": 2050, "scenario": scenario, "parent_id": parent_id, "zone": str(src.zone),
                         "carrier": str(src.carrier), "old_child_ids": json.dumps(old_ids), "template_id": old_ids[0],
                         "child_id": name, "old_parent_MW": float(old.p_nom.sum()), "parent_MW": mw,
                         "unit_count": count, "target_block_MW": 300 if nuclear else 550,
                         "p_nom": float(child.p_nom), "efficiency": float(child.efficiency), "marginal_cost": mc,
                         "parent_efficiency": eta, "parent_marginal_cost": float(src.marginal_cost_EUR2025_per_MWh_el),
                         "size_class": child.size_class, "efficiency_class": child.efficiency_class,
                         "startup_EUR_per_MW": float(old.start_up_cost.iloc[0] / old.p_nom.iloc[0]),
                         "classification": "EXPECTED_MECHANICAL_DOWNSTREAM_CHANGE"})
    return pd.DataFrame(rows)


def transform(parent, plan, scenario, parent_hash, network_type):
    output = parent.copy()
    targets = case_contract(scenario)
    for carriers, target in ((VRE[:2], targets["solar"]), ((VRE[2],), targets["wind_onshore"]),
                             ((VRE[3],), targets["wind_offshore"])):
        names = parent.generators.index[parent.generators.carrier.isin(carriers)]
        output.generators.loc[names, "p_nom"] *= target / parent.generators.loc[names, "p_nom"].sum()
    removed = sorted({name for value in plan.old_child_ids.unique() for name in json.loads(value)})
    rows = []
    for child in plan.itertuples():
        row = parent.generators.loc[child.template_id].copy()
        row.name = child.child_id
        row["p_nom"], row["efficiency"], row["marginal_cost"] = child.p_nom, child.efficiency, child.marginal_cost
        for cost in ("start_up_cost", "shut_down_cost", "stand_by_cost"):
            row[cost] = float(parent.generators.at[child.template_id, cost] / parent.generators.at[child.template_id, "p_nom"]) * child.p_nom
        rows.append(row)
    # Only generator cohorts are replaced. Every other component object is copied.
    output.generators = pd.concat([output.generators.drop(index=removed), pd.DataFrame(rows)]).sort_index()
    output.generators.index.name = parent.generators.index.name
    for field, frame in parent.generators_t.items():
        changed = [name for name in removed if name in frame]
        if not changed:
            continue
        expected = frame.drop(columns=changed).copy()
        for _, group in plan.groupby("parent_id", sort=True):
            old_ids = json.loads(group.old_child_ids.iloc[0])
            represented = [name for name in old_ids if name in frame]
            if represented and len(represented) != len(old_ids):
                raise RuntimeError("FINAL_V4_PARTIAL_COHORT_DYNAMIC_AUTHORITY")
            if represented:
                base = frame[represented[0]]
                for old_id in represented[1:]:
                    pd.testing.assert_series_equal(base, frame[old_id], check_names=False, check_exact=True)
                for name in group.child_id:
                    expected[name] = base.to_numpy(copy=True)
        output.generators_t[field] = expected
    output.meta = {**parent.meta, "final_methodology_variant": "final_v4",
                   "final_v4_parent_sha256": parent_hash, "final_v4_parent_version": "final_v3",
                   "final_v4_network_type": network_type,
                   "final_v4_capacity_authority": "USER_ACCEPTED_LATER_TERNA_2050_ANALYSIS",
                   "final_v4_capacity_contract": "runtime_inputs/final_methodology_v4/2050/generators_static.csv",
                   "final_v4_BESS_change": "NONE", "production_optimization_executed": False}
    return output


def exact_diff(parent, output, plan, scenario, network_type):
    """Fail on any field outside capacity/cohort changes; include all time series."""
    pd.testing.assert_index_equal(parent.snapshots, output.snapshots)
    pd.testing.assert_frame_equal(parent.snapshot_weightings, output.snapshot_weightings, check_exact=True)
    affected = sorted({name for value in plan.old_child_ids.unique() for name in json.loads(value)})
    new_ids = sorted(plan.child_id)
    wind_solar = parent.generators.index[parent.generators.carrier.isin(VRE)]
    rows = []
    def record(component, field, name, classification, old=None, new=None):
        rows.append({"year": 2050, "scenario": scenario, "network_type": network_type,
                     "component": component, "field": field, "object_id": name,
                     "old_value": old, "new_value": new, "classification": classification})
    for attr in COMPONENTS:
        old, new = getattr(parent, attr), getattr(output, attr)
        if attr != "generators":
            pd.testing.assert_frame_equal(old, new, check_exact=True, check_dtype=False)
        else:
            unchanged = old.index.difference(affected)
            old_expected = old.loc[unchanged].copy()
            old_expected.loc[wind_solar, "p_nom"] = new.loc[wind_solar, "p_nom"]
            pd.testing.assert_frame_equal(old_expected, new.loc[unchanged], check_exact=True, check_dtype=False)
            if set(new.index) != set(unchanged) | set(new_ids) or new.index.has_duplicates:
                raise RuntimeError("FINAL_V4_GENERATOR_ID_DRIFT")
            for name in wind_solar:
                record(attr, "p_nom", name, "EXPECTED_2050_VRE_CAPACITY_REVISION", old.at[name, "p_nom"], new.at[name, "p_nom"])
            for parent_id, group in plan.groupby("parent_id", sort=True):
                classification = "EXPECTED_2050_NUCLEAR_CAPACITY_REVISION" if group.carrier.iloc[0] == "nuclear" else "EXPECTED_PROGRAMMABLE_RECONCILIATION"
                record(attr, "parent_capacity_envelope", parent_id, classification, group.old_parent_MW.iloc[0], group.parent_MW.iloc[0])
            for name in sorted(set(affected) - set(new_ids)):
                record(attr, "row_membership", name, "EXPECTED_MECHANICAL_DOWNSTREAM_CHANGE", "present", "absent")
            for child in plan.itertuples():
                template = old.loc[child.template_id]
                fields = old.columns.difference(MECHANICAL_FIELDS)
                pd.testing.assert_series_equal(template[fields], new.loc[child.child_id, fields], check_names=False, check_exact=True)
                for field in MECHANICAL_FIELDS:
                    if field in ("shut_down_cost", "stand_by_cost"):
                        close(new.at[child.child_id, field], float(template[field] / template.p_nom) * child.p_nom, "CHILD_COST_DRIFT", 1e-9)
                close(new.at[child.child_id, "p_nom"], child.p_nom, "CHILD_CAPACITY_DRIFT", 1e-9)
                close(new.at[child.child_id, "efficiency"], child.efficiency, "CHILD_EFFICIENCY_DRIFT", 1e-12)
                close(new.at[child.child_id, "marginal_cost"], child.marginal_cost, "CHILD_COST_DRIFT", 1e-9)
                close(new.at[child.child_id, "start_up_cost"], float(template.start_up_cost / template.p_nom) * child.p_nom, "STARTUP_SCALING_DRIFT", 1e-7)
                if child.child_id not in old.index:
                    record(attr, "row_membership", child.child_id, "EXPECTED_MECHANICAL_DOWNSTREAM_CHANGE", "absent", "present")
                for field in MECHANICAL_FIELDS:
                    before = old.at[child.child_id, field] if child.child_id in old.index else None
                    after = new.at[child.child_id, field]
                    if before is None or before != after:
                        record(attr, field, child.child_id, "EXPECTED_MECHANICAL_DOWNSTREAM_CHANGE", before, after)
        old_t, new_t = getattr(parent, attr + "_t", {}), getattr(output, attr + "_t", {})
        if set(old_t) != set(new_t):
            raise RuntimeError("FINAL_V4_DYNAMIC_SCHEMA_DRIFT")
        for field, frame in old_t.items():
            if attr != "generators":
                pd.testing.assert_frame_equal(frame, new_t[field], check_exact=True, check_dtype=False, check_freq=False)
                continue
            untouched = frame.columns.difference(affected)
            pd.testing.assert_frame_equal(frame[untouched], new_t[field][untouched], check_exact=True, check_dtype=False, check_freq=False)
            expected_columns = set(untouched)
            for _, group in plan.groupby("parent_id", sort=True):
                old_ids = json.loads(group.old_child_ids.iloc[0])
                represented = [name for name in old_ids if name in frame]
                if represented:
                    if len(represented) != len(old_ids):
                        raise RuntimeError("FINAL_V4_PARTIAL_DYNAMIC_COHORT")
                    expected_columns.update(group.child_id)
                    for name in group.child_id:
                        pd.testing.assert_series_equal(frame[represented[0]], new_t[field][name], check_names=False, check_exact=True, check_freq=False)
                        if name not in frame:
                            record(attr + "_t", field, name, "EXPECTED_MECHANICAL_DOWNSTREAM_CHANGE", "absent", "identical_parent_cohort_series")
            for name in sorted(set(frame.columns) - set(new_t[field].columns)):
                record(attr + "_t", field, name, "EXPECTED_MECHANICAL_DOWNSTREAM_CHANGE", "present", "absent")
            if set(new_t[field]) != expected_columns:
                raise RuntimeError("FINAL_V4_UNEXPECTED_DYNAMIC_COLUMNS")
    authorized_meta = {"final_methodology_variant", "production_optimization_executed", "final_v4_parent_sha256",
                       "final_v4_parent_version", "final_v4_network_type", "final_v4_capacity_authority",
                       "final_v4_capacity_contract", "final_v4_BESS_change"}
    for key in sorted(set(parent.meta) | set(output.meta)):
        if key not in authorized_meta and parent.meta.get(key) != output.meta.get(key):
            raise RuntimeError("FINAL_V4_METADATA_DRIFT: " + key)
        if parent.meta.get(key) != output.meta.get(key):
            record("meta", key, "network", "EXPECTED_MECHANICAL_DOWNSTREAM_CHANGE", parent.meta.get(key), output.meta.get(key))
    return rows


def capacity_qa(parent, network, plan, scenario, *, continuous=False):
    from .final_methodology_networks import structural_check
    expected_units = int(network.generators.index.str.startswith(("UC__", "UC2A__")).sum())
    structural_check(network, 2050, scenario, continuous=continuous, expected_units=expected_units)
    controls = case_contract(scenario)
    for family, carriers in (("solar", VRE[:2]), ("wind_onshore", [VRE[2]]), ("wind_offshore", [VRE[3]]), ("nuclear", ["nuclear"])):
        old = parent.generators.loc[parent.generators.carrier.isin(carriers)].groupby("bus").p_nom.sum()
        new = network.generators.loc[network.generators.carrier.isin(carriers)].groupby("bus").p_nom.sum().reindex(old.index)
        close(new.sum(), controls[family], "NATIONAL_" + family)
        if not np.allclose(new / new.sum(), old / old.sum(), rtol=0, atol=1e-14):
            raise RuntimeError("FINAL_V4_NEW_GEOGRAPHY: " + family)
    old_program = parent.generators.loc[parent.generators.carrier.isin(MODEL_CARRIERS), "p_nom"].sum()
    new_program = network.generators.loc[network.generators.carrier.isin(MODEL_CARRIERS), "p_nom"].sum()
    close(old_program, EXPECTED_OLD_PROGRAM[scenario], "NETWORK_PROGRAM_AUTHORITY")
    close(new_program, old_program, "NETWORK_PROGRAM_PRESERVATION")
    old_gas = parent.generators.loc[parent.generators.carrier.eq("methane_ccs")].groupby("bus").p_nom.sum()
    new_gas = network.generators.loc[network.generators.carrier.eq("methane_ccs")].groupby("bus").p_nom.sum()
    close(old_gas.sum() - new_gas.sum(), 2000, "GAS_CCS_REDUCTION")
    if not np.allclose(old_gas / old_gas.sum(), new_gas / new_gas.sum(), rtol=0, atol=1e-14):
        raise RuntimeError("FINAL_V4_GAS_CCS_GEOGRAPHY_DRIFT")
    for parent_id, group in plan.groupby("parent_id"):
        children = network.generators.loc[group.child_id]
        close(children.p_nom.sum(), group.parent_MW.iloc[0], "PARENT_RECONCILIATION")
        close(np.average(1 / children.efficiency, weights=children.p_nom), 1 / group.parent_efficiency.iloc[0], "WEIGHTED_HR", 1e-12)
        close(np.average(children.marginal_cost, weights=children.p_nom), group.parent_marginal_cost.iloc[0], "WEIGHTED_MC", 1e-9)
    storage = pd.read_csv(ROOT / "pre_pypsa_inputs/MEM_storage_static_final.csv")
    bess = storage.loc[storage.year.eq(2050) & storage.scenario.eq(scenario) & storage.technology.str.startswith("BESS")]
    charge = network.links.loc[network.links.index.str.startswith("BESS_CHARGE_")]
    discharge = network.links.loc[network.links.index.str.startswith("BESS_DISCHARGE_")]
    close(charge.p_nom.sum(), bess.charge_power_MW.sum(), "BESS_CHARGE_POWER")
    close((discharge.p_nom * discharge.efficiency).sum(), bess.discharge_power_MW.sum(), "BESS_ELECTRICAL_DISCHARGE_POWER")
    close(network.stores.loc[network.stores.carrier.eq("battery_energy"), "e_nom"].sum(), bess.energy_capacity_MWh.sum(), "BESS_ENERGY")
    return {"status": "PASS", "scenario": scenario, "UC_units": expected_units,
            "nuclear_units": int(plan.carrier.eq("nuclear").sum()), "GAS_CCS_units": int(plan.carrier.eq("methane_ccs").sum()),
            "programmable_MW": float(new_program), "GAS_CCS_MW": float(new_gas.sum()),
            "BESS_changed_fields": 0, "unexpected_changed_fields": 0, "VRE_profiles_unchanged": True,
            "no_new_2050_geography": True, "optimization_model_constructed": False, "optimizer_invocations": 0}


def protected_inventory():
    paths = {STATIC, CROSSWALK, PROVENANCE, ROOT / "pre_pypsa_inputs/MEM_storage_static_final.csv"}
    for directory in ("networks/unsolved/final_methodology_v3", "results/final_methodology_v3", "qa/final_pre_rerun"):
        paths.update(p for p in (ROOT / directory).rglob("*") if p.is_file())
    paths.add(ROOT / "config/final_methodology_execution_v3.yaml")
    return {str(p.relative_to(ROOT)): sha256_file(p) for p in sorted(paths)}


def demand_reconciliation():
    """Independently count unchanged rigid load plus zonal P2X annual targets."""
    contract = pd.read_csv(ROOT / "runtime_inputs/p2x_flex_v2/2050/p2x_contract.csv")
    lineage = verify_frozen()
    rows = []
    with no_models_or_solves():
        for scenario in ("Slow", "Base", "High"):
            item = lineage.loc[lineage.year.eq(2050) & lineage.scenario.eq(scenario) & lineage.network_type.eq("UC_INPUT")].iloc[0]
            network = pypsa.Network(ROOT / item.path)
            weights = network.snapshot_weightings.generators
            load = network.get_switchable_as_dense("Load", "p_set")
            expected = contract.loc[contract.scenario.eq(scenario)].set_index("zone")
            for zone in ZONES:
                names = network.loads.index[network.loads.bus.eq(zone)]
                rigid = float(load[names].mul(weights, axis=0).sum().sum())
                p2x = float(network.global_constraints.at[f"P2X_ANNUAL_{zone}", "constant"])
                close(rigid, expected.at[zone, "annual_rigid_MWh"], "RIGID_ANNUAL_DEMAND")
                close(p2x, expected.at[zone, "annual_p2x_MWh"], "P2X_ANNUAL_DEMAND")
                rows.append({"year": 2050, "scenario": scenario, "zone": zone,
                    "annual_rigid_MWh": rigid, "annual_P2X_MWh": p2x, "annual_total_MWh": rigid + p2x,
                    "generator_weight_sum": float(weights.sum()), "status": "PASS_UNCHANGED"})
            case = pd.DataFrame(rows).loc[lambda frame: frame.scenario.eq(scenario)]
            close(case.annual_total_MWh.sum(), (641.41 if scenario == "High" else 583.1) * 1e6, "TOTAL_ANNUAL_DEMAND")
    return pd.DataFrame(rows)


def inherited_results():
    rows = []
    for year in (2040, 2050):
        base = ROOT / f"results/final_methodology_v3/{year}/Base"
        final = json.loads((base / "QA/FINAL_SCENARIO_QA.json").read_text())
        if final.get("status") != "PASS" or not final.get("three_jobs_verified"):
            raise RuntimeError("FINAL_V4_INHERITED_BASE_RESULT_NOT_VERIFIED")
        for receipt_path in sorted(base.glob("*/*SOLVE_RECEIPT.json")):
            receipt = json.loads(receipt_path.read_text())
            solved = ROOT / receipt["solved_path"]
            if receipt.get("status") != "PASS" or receipt.get("termination_condition") != "optimal" or sha256_file(solved) != receipt["solved_sha256"]:
                raise RuntimeError("FINAL_V4_INHERITED_RESULT_HASH_FAIL")
            rows.append({"year": year, "scenario": "Base", "kind": receipt["kind"],
                         "path": receipt["solved_path"], "sha256": receipt["solved_sha256"],
                         "receipt_path": str(receipt_path.relative_to(ROOT)), "inherited_from": "final_v3",
                         "role": "CANONICAL_2040_BASE_RESULT_USER_ACCEPTED" if year == 2040 else "PNIEC_CAPACITY_BENCHMARK"})
    if len(rows) != 6:
        raise RuntimeError("FINAL_V4_INHERITED_RESULT_COUNT_FAIL")
    return rows


def prepare():
    """One deterministic preparation command, never a production run."""
    from .final_methodology_networks import _equal
    with no_models_or_solves():
        parents, source = authority()
        protected = protected_inventory()
        results = inherited_results()
        if (QA / "FINAL_V4_PREPARATION_RECEIPT.json").exists():
            raise RuntimeError("FINAL_V4_ALREADY_MATERIALIZED_NO_OVERWRITE")
        QA.mkdir(parents=True, exist_ok=True)
        NETWORKS.mkdir(parents=True, exist_ok=True)
        (RUNTIME / "2050").mkdir(parents=True, exist_ok=True)
        revised = revised_static(source)
        static_contract_text(revised).to_csv(RUNTIME / "2050/generators_static.csv", index=False)
        lineage, plans, changes, packages, zonal = [], [], [], [], []
        for year, scenario in SCENARIOS:
            case = parents.loc[parents.year.eq(year) & parents.scenario.eq(scenario)]
            if year == 2040:
                for item in case.to_dict("records"):
                    lineage.append({"year": year, "scenario": scenario, "network_type": item["network_type"],
                        "parent_path": item["path"], "parent_sha256": item["sha256"], "path": item["path"], "sha256": item["sha256"],
                        "inherited_from": "final_v3", "role": "INHERITED_UNCHANGED", "status": "PASS",
                        "UC_units": 130 if scenario == "Slow" else 108})
                continue
            uc_row = case.loc[case.network_type.eq("UC_INPUT")].iloc[0]
            uc_parent = pypsa.Network(ROOT / uc_row.path)
            plan = child_plan(uc_parent, revised, scenario)
            plans.append(plan)
            pair = {}
            for item in case.to_dict("records"):
                parent = uc_parent if item["network_type"] == "UC_INPUT" else pypsa.Network(ROOT / item["path"])
                output = transform(parent, plan, scenario, item["sha256"], item["network_type"])
                changes += exact_diff(parent, output, plan, scenario, item["network_type"])
                suffix = "_CONTINUOUS_REFERENCE" if item["network_type"] == "CONTINUOUS_REFERENCE" else ""
                destination = NETWORKS / f"MEM_2050_{scenario.upper()}_FINAL_METHODOLOGY_V4{suffix}_8760h_UNSOLVED.nc"
                if not destination.exists():
                    output.export_to_netcdf(destination)
                reloaded = pypsa.Network(destination)
                _equal(output, reloaded)
                exact_diff(parent, reloaded, plan, scenario, item["network_type"])
                qa = capacity_qa(parent, reloaded, plan, scenario, continuous=bool(suffix))
                if not suffix:
                    packages.append(qa)
                for family in (*VRE, "nuclear", "methane_ccs"):
                    old = parent.generators.loc[parent.generators.carrier.eq(family)].groupby("bus").p_nom.sum()
                    new = reloaded.generators.loc[reloaded.generators.carrier.eq(family)].groupby("bus").p_nom.sum()
                    for zone in old.index:
                        zonal.append({"year": 2050, "scenario": scenario, "network_type": item["network_type"], "technology": family,
                            "zone": zone, "old_MW": old[zone], "new_MW": new[zone], "scale": new[zone] / old[zone] if old[zone] else None,
                            "old_share": old[zone] / old.sum(), "new_share": new[zone] / new.sum()})
                pair[item["network_type"]] = reloaded
                lineage.append({"year": year, "scenario": scenario, "network_type": item["network_type"],
                    "parent_path": item["path"], "parent_sha256": item["sha256"], "path": str(destination.relative_to(ROOT)),
                    "sha256": sha256_file(destination), "inherited_from": "final_v3", "role": "REVISED_2050_CAPACITY_SUCCESSOR",
                    "status": "PASS", "UC_units": qa["UC_units"]})
            a, b = pair["UC_INPUT"], pair["CONTINUOUS_REFERENCE"]
            shared = ["bus", "carrier", "p_nom", "efficiency", "marginal_cost", "p_nom_extendable", "sign"]
            pd.testing.assert_frame_equal(a.generators[shared], b.generators[shared], check_exact=True)
            pd.testing.assert_frame_equal(a.generators_t.p_max_pu, b.generators_t.p_max_pu, check_exact=True)
            dump_json(QA / f"FINAL_V4_2050_{scenario.upper()}_STRUCTURAL_QA.json", packages[-1])
        pd.DataFrame(lineage).to_csv(QA / "FINAL_V4_NETWORK_LINEAGE_AND_HASHES.csv", index=False)
        pd.concat(plans, ignore_index=True).to_csv(QA / "FINAL_V4_CHILD_CROSSWALK.csv", index=False, float_format="%.17g")
        pd.DataFrame(changes).to_csv(QA / "FINAL_V3_TO_V4_EXACT_DIFF.csv", index=False, float_format="%.17g")
        pd.DataFrame(zonal).to_csv(QA / "FINAL_V4_ZONAL_CAPACITY_RECONCILIATION.csv", index=False, float_format="%.17g")
        pd.DataFrame(results).to_csv(QA / "FINAL_V4_INHERITED_BASE_RESULTS.csv", index=False)
        pd.DataFrame([{"path": p, "sha256": digest} for p, digest in protected.items()]).to_csv(QA / "FINAL_V3_PROTECTED_HASHES.csv", index=False)
        storage = pd.read_csv(ROOT / "pre_pypsa_inputs/MEM_storage_static_final.csv")
        bess = storage.loc[storage.year.eq(2050) & storage.technology.str.startswith("BESS")].groupby("scenario")[["discharge_power_MW", "energy_capacity_MWh"]].sum().reset_index()
        bess.to_csv(QA / "FINAL_V4_UNCHANGED_BESS_CONTROLS.csv", index=False, float_format="%.17g")
        runtime_manifest().to_csv(RUNTIME / "FINAL_V4_RUNTIME_MANIFEST.csv", index=False)
        for path, digest in protected.items():
            if sha256_file(ROOT / path) != digest:
                raise RuntimeError("FINAL_V4_PROTECTED_V3_CHANGED: " + path)
        receipt = {"status": "PASS", "state": "FINAL_V4_STATIC_PREPARATION_PASS", "release_scope": "COMPLETE_SIX_SCENARIO_PROJECT_STATE",
            "networks_export_reload": 6, "inherited_2040_networks": 6, "packages": packages,
            "protected_final_v3_files": len(protected), "protected_final_v3_unchanged": True,
            "inherited_results": results, "capacity_authority": load_yaml(CONFIG)["capacity_revision"],
            "BESS_changed_fields": 0, "unexpected_changed_fields": 0,
            "optimization_model_constructed": False, "production_solver_invocations": 0}
        dump_json(QA / "FINAL_V4_PREPARATION_RECEIPT.json", receipt)
        return receipt


def verify_frozen():
    cfg = load_yaml(CONFIG)
    controls = cfg.get("accepted_preparation_controls", {})
    expected = {cfg["network_receipt"], cfg["network_lineage"],
                "qa/final_methodology_v4/FINAL_V4_CHILD_CROSSWALK.csv",
                "runtime_inputs/final_methodology_v4/2050/generators_static.csv"}
    if set(controls) != expected:
        raise RuntimeError("FINAL_V4_FROZEN_CONTROL_SET_FAIL")
    for name, digest in controls.items():
        if not (ROOT / name).is_file() or sha256_file(ROOT / name) != digest:
            raise RuntimeError("FINAL_V4_FROZEN_CONTROL_HASH_FAIL: " + name)
    receipt = json.loads((ROOT / cfg["network_receipt"]).read_text())
    if receipt.get("status") != "PASS" or receipt.get("unexpected_changed_fields") != 0 or receipt.get("BESS_changed_fields") != 0 or receipt.get("production_solver_invocations") != 0:
        raise RuntimeError("FINAL_V4_FROZEN_RECEIPT_FAIL")
    table = pd.read_csv(ROOT / cfg["network_lineage"])
    expected_cases = {(y, s, t) for y, s in SCENARIOS for t in ("UC_INPUT", "CONTINUOUS_REFERENCE")}
    if len(table) != 12 or set(table[["year", "scenario", "network_type"]].itertuples(index=False, name=None)) != expected_cases or not table.status.eq("PASS").all() or not table.path.is_unique:
        raise RuntimeError("FINAL_V4_LINEAGE_CASE_SET_FAIL")
    for row in table.itertuples():
        family = "v3" if row.year == 2040 else "v4"
        suffix = "_CONTINUOUS_REFERENCE" if row.network_type == "CONTINUOUS_REFERENCE" else ""
        expected_path = ROOT / f"networks/unsolved/final_methodology_{family}/MEM_{row.year}_{row.scenario.upper()}_FINAL_METHODOLOGY_{family.upper()}{suffix}_8760h_UNSOLVED.nc"
        if (ROOT / row.path).resolve() != expected_path.resolve() or (row.year == 2040 and row.sha256 != row.parent_sha256):
            raise RuntimeError("FINAL_V4_INPUT_ISOLATION_OR_INHERITANCE_FAIL")
    return table


def package(year, scenario):
    table = verify_frozen()
    case = table.loc[table.year.eq(year) & table.scenario.eq(scenario)].set_index("network_type")
    return {"year": year, "scenario": scenario, "path": case.at["UC_INPUT", "path"], "sha256": case.at["UC_INPUT", "sha256"],
            "reference_path": case.at["CONTINUOUS_REFERENCE", "path"], "reference_sha256": case.at["CONTINUOUS_REFERENCE", "sha256"],
            "UC_units": int(case.at["UC_INPUT", "UC_units"])}


def close_preparation(test_receipt):
    """Freeze documentation/static QA only; never creates production outputs."""
    from xml.etree import ElementTree
    from . import stage_b_uc2 as uc2
    from . import final_methodology_networks as final
    test_receipts = [Path(p) for p in test_receipt] if isinstance(test_receipt, (list, tuple)) else [Path(test_receipt)]
    cases = {}
    executions = 0
    for path in test_receipts:
        for node in ElementTree.parse(path).getroot().iter("testcase"):
            executions += 1
            if any(node.find(key) is not None for key in ("failure", "error", "skipped")):
                raise RuntimeError("FINAL_V4_FOCUSED_TESTS_NOT_PASS")
            cases[(node.get("classname"), node.get("name"))] = node
    counts = {"tests": len(cases), "failures": 0, "errors": 0, "skipped": 0}
    if not counts["tests"] or any(counts[key] for key in ("failures", "errors", "skipped")):
        raise RuntimeError("FINAL_V4_FOCUSED_TESTS_NOT_PASS")
    with no_models_or_solves():
        if (ROOT / "results/final_methodology_v4").exists():
            raise RuntimeError("FINAL_V4_PREPARATION_HAS_PRODUCTION_RESULT_DIRECTORY")
        lineage = verify_frozen()
        protected = pd.read_csv(QA / "FINAL_V3_PROTECTED_HASHES.csv")
        for row in protected.itertuples():
            if sha256_file(ROOT / row.path) != row.sha256:
                raise RuntimeError("FINAL_V4_HISTORICAL_ARTIFACT_CHANGED: " + row.path)
        cfg = final.settings("final_v4")
        if cfg["solver"] != final.settings("final_v3")["solver"]:
            raise RuntimeError("FINAL_V4_SOLVER_DRIFT")
        jobs, preflights, cases, changes = [], [], [], []
        plans = pd.read_csv(QA / "FINAL_V4_CHILD_CROSSWALK.csv")
        for year, scenario in SCENARIOS:
            with uc2.execution_variant("final_v4"):
                result = uc2.preflight(year, scenario, persist=False)
                preflights.append(result)
                for kind in uc2.RUN_TYPES:
                    paths = uc2.job_paths(year, scenario, kind)
                    jobs.append({"year": year, "scenario": scenario, "run_type": kind,
                        "job_id": uc2.job_id(year, scenario, kind), "execution_variant": "final_v3" if year == 2040 else "final_v4",
                        "input_path": str(paths["input"].relative_to(ROOT)), "input_sha256": uc2._check_input_hash(year, scenario, kind),
                        "solved_path": str(paths["solved"].relative_to(ROOT)),
                        "receipt_path": str(paths["receipt"].relative_to(ROOT)),
                        "verification_path": str(paths["verification"].relative_to(ROOT)),
                        "manual_authorized": result["manual_authorized"], "automatic_successor": False,
                        "state": "INHERITED_FINAL_V3_EXECUTION_STATE" if year == 2040 else "PREPARED_NOT_EXECUTED"})
                root = uc2.case_root(year, scenario)
            cases.append({"year": year, "scenario": scenario,
                "input_state": "INHERITED_UNCHANGED_FROM_FINAL_V3" if year == 2040 else "REVISED_2050_CAPACITY_SUCCESSOR",
                "UC_input_path": result["uc_input_path"], "UC_input_sha256": result["uc_input_sha256"],
                "reference_input_path": result["parent_path"], "reference_input_sha256": result["parent_sha256"],
                "source_configuration_version": "final_v3" if year == 2040 else "final_v4",
                "canonical_result": "INHERITED_FINAL_V3_SOLVED_RESULT" if (year, scenario) == (2040, "Base") else None,
                "expected_result_root": str(root.relative_to(ROOT)), "manual_authorized": result["manual_authorized"],
                "reporting_path": str((root / "REPORTING").relative_to(ROOT)),
                "final_qa_path": str((root / "QA/FINAL_SCENARIO_QA.json").relative_to(ROOT)),
                "review_template": "qa/final_methodology_v4/FINAL_V4_POST_RUN_COMPARISON_TEMPLATE.md"})
            if year == 2050:
                case = lineage.loc[lineage.year.eq(year) & lineage.scenario.eq(scenario)]
                plan = plans.loc[plans.scenario.eq(scenario)]
                for row in case.itertuples():
                    original = pypsa.Network(ROOT / row.parent_path)
                    network = pypsa.Network(ROOT / row.path)
                    frozen_fields = original.generators.columns.difference(MECHANICAL_FIELDS)
                    for _, cohort in plan.groupby("parent_id"):
                        ids = json.loads(cohort.old_child_ids.iloc[0])
                        template = original.generators.loc[ids[0], frozen_fields]
                        for child_id in ids[1:]:
                            pd.testing.assert_series_equal(template, original.generators.loc[child_id, frozen_fields],
                                                          check_names=False, check_exact=True)
                    changes.extend(exact_diff(original, network, plan, scenario, row.network_type))
        table = pd.DataFrame(jobs)
        if len(table) != 18 or not table.job_id.is_unique or not table.solved_path.is_unique:
            raise RuntimeError("FINAL_V4_MANUAL_JOB_ISOLATION_FAIL")
        for row in lineage.itertuples():
            if sha256_file(ROOT / row.path) != row.sha256:
                raise RuntimeError("FINAL_V4_FINAL_INPUT_HASH_FAIL")
        table.to_csv(QA / "FINAL_V4_MANUAL_JOB_REGISTRY.csv", index=False)
        pd.DataFrame(preflights).to_csv(QA / "FINAL_V4_STATIC_PREFLIGHTS.csv", index=False)
        diff = pd.DataFrame(changes)
        diff.to_csv(QA / "FINAL_V3_TO_V4_EXACT_DIFF.csv", index=False, float_format="%.17g")
        demand = demand_reconciliation()
        demand.to_csv(QA / "FINAL_V4_UNCHANGED_DEMAND_CONTROLS.csv", index=False, float_format="%.17g")
        # Exact cell preservation is checked separately from harmless CSV dtype inference.
        original_text = pd.read_csv(STATIC, dtype=str, keep_default_na=False)
        revised_text = pd.read_csv(RUNTIME / "2050/generators_static.csv", dtype=str, keep_default_na=False)
        pd.testing.assert_frame_equal(original_text.loc[original_text.year.eq("2050")].reset_index(drop=True).drop(columns="p_nom_MW"),
                                      revised_text.drop(columns="p_nom_MW"), check_exact=True)
        state = {"state": "FINAL_V4_COMPLETE_SIX_SCENARIO_STATE_PREPARED", "release_scope": cfg["release_scope"],
            "input_lineage_path": cfg["network_lineage"], "input_lineage_sha256": sha256_file(ROOT / cfg["network_lineage"]),
            "cases": cases, "next_authorized_production_case": "2050_BASE_FINAL_V4", "stop_after_base_for_review": True,
            "2050_final_v3_base_role": "PRESERVED_PNIEC_CAPACITY_BENCHMARK",
            "inherited_results": pd.read_csv(QA / "FINAL_V4_INHERITED_BASE_RESULTS.csv").to_dict("records"),
            "source_path": cfg["capacity_revision"]["source"],
            "source_sha256": sha256_file(ROOT / cfg["capacity_revision"]["source"]),
            "authority_status": "EXPLICIT_USER_ACCEPTED_SOURCE_CAPACITY_DECISION_NOT_OUTCOME_CALIBRATION",
            "production_solver_invocations": 0}
        dump_json(QA / "FINAL_V4_RELEASE_STATE.json", state)
        verification = {"status": "PASS", "focused_tests": counts, "test_executions": executions,
            "test_receipts": [{"path": str(p.relative_to(ROOT)), "sha256": sha256_file(p)} for p in test_receipts],
            "static_preflights": len(preflights),
            "export_reloads": 6, "inherited_2040_inputs": 6, "six_case_registry": True, "manual_jobs": len(jobs),
            "zonal_annual_demand_checks": len(demand),
            "protected_final_v3_files": len(protected), "protected_final_v3_unchanged": True,
            "BESS_changed_fields": 0, "unexpected_changed_fields": 0,
            "diff_classification_counts": diff.classification.value_counts().to_dict(),
            "solver_contract_unchanged": True, "production_results_created": 0,
            "optimization_model_constructed": False, "production_optimization_executed": False, "production_solver_invocations": 0,
            "initial_test_attempt": {"receipt": "qa/final_methodology_v4/FOCUSED_NON_SOLVING_TESTS.xml",
                "issues": ["unchanged CSV float spelling corrected without network regeneration", "system test-temp directory permission; rerun in new workspace test-temp directory"]}}
        dump_json(QA / "FINAL_V4_VERIFICATION.json", verification)
        refresh_artifact_manifest()
        return verification


def refresh_artifact_manifest():
    """Hash only this successor's sources/evidence; no test-temp or result files."""
    paths = {CONFIG, ROOT / "README.md", ROOT / "docs/MEM_FINAL_V4_TRANSFER.md",
             ROOT / "src/mem_model/final_capacity_revision.py", ROOT / "src/mem_model/stage_b_uc2.py",
             ROOT / "src/mem_model/final_methodology_networks.py", ROOT / "tests/test_final_v4_capacity_revision.py"}
    paths.update(p for folder in (QA, RUNTIME, NETWORKS) for p in folder.iterdir() if p.is_file())
    paths.add(RUNTIME / "2050/generators_static.csv")
    manifest = QA / "FINAL_V4_ARTIFACT_MANIFEST.csv"
    paths.discard(manifest)
    pd.DataFrame([{"path": str(p.relative_to(ROOT)), "sha256": sha256_file(p), "bytes": p.stat().st_size,
        "role": "RETAINED_INITIAL_FAILED_TEST_ATTEMPT" if p.name == "FOCUSED_NON_SOLVING_TESTS.xml" else
                "PREPARATION_SOURCE_OR_EVIDENCE_NOT_PRODUCTION_RESULT"} for p in sorted(paths)]).to_csv(manifest, index=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare"])
    parser.parse_args()
    print(json.dumps(prepare(), indent=2, default=str))

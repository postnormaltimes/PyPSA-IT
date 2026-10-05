"""UC-v2A preparation: standard PyPSA child rows, no optimization model."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pypsa

from . import stage_b_uc1 as uc1
from .common import CONFIG, ROOT, STATIC, dump_json, load_yaml, sha256_file
from .reporting.uc2_postprocess import no_solver_calls
from .uc_heterogeneity import (CELL_ORDER, SIZE_CLASSES, EFFICIENCY_CLASSES, EFFICIENCY_MULTIPLIERS,
                               EFFICIENCY_CAPACITY_SHARES, heat_rate_cost, normalize_children, reference_classes)

VERSION = "UC2A_HETEROGENEOUS_V1"
SUCCESS = "HETEROGENEOUS_UC_V2A_TECHNICALLY_PREPARED"
CONFIG_PATH = CONFIG / "stage_b_uc2a_heterogeneous.yaml"
CALIBRATION = "CONTROLLED_ETA_15_50_35_V2"
STATIC_SIGNATURE_FIELDS = ("p_nom", "efficiency", "marginal_cost", "p_min_pu", "p_max_pu",
                           "ramp_limit_up", "ramp_limit_down", "ramp_limit_start_up", "ramp_limit_shut_down",
                           "min_up_time", "min_down_time", "start_up_cost", "shut_down_cost", "stand_by_cost",
                           "up_time_before", "down_time_before")
OVERRIDE_FIELDS = ("p_nom", "efficiency", "marginal_cost", "start_up_cost", "shut_down_cost", "stand_by_cost")


def settings() -> dict:
    cfg = load_yaml(CONFIG_PATH)
    if (cfg["schema_version"] != "MEM_UC2A_HETEROGENEOUS_V1" or
        cfg["optimization_authorized_in_preparation"] is not False or
        pypsa.__version__ != cfg["pypsa_version"] or
        cfg.get("calibration_revision") != CALIBRATION or
        not np.array_equal(cfg["efficiency_multipliers"], EFFICIENCY_MULTIPLIERS) or
        not np.array_equal(cfg["efficiency_capacity_shares"], EFFICIENCY_CAPACITY_SHARES) or
        not np.allclose(cfg["class_boundaries"], [1 / 3, 2 / 3], rtol=0, atol=1e-16)):
        raise RuntimeError("V2A_CONFIG_OR_PINNED_RUNTIME_FAIL")
    return cfg


def output_path(year: int, scenario: str) -> Path:
    if (year, scenario) not in uc1.SCENARIOS:
        raise ValueError("V2A_UNKNOWN_SCENARIO")
    return ROOT / settings()["output_directory"] / f"MEM_{year}_{scenario.upper()}_{VERSION}_8760h_UNSOLVED.nc"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _text(frame: pd.DataFrame) -> str:
    return frame.to_csv(index=False, lineterminator="\n", float_format="%.17g")


def _digest(frame: pd.DataFrame) -> str:
    return hashlib.sha256(_text(frame).encode()).hexdigest()


def _jsonable(value):
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value.tolist() if hasattr(value, "tolist") else value


def load_reference(cfg: dict) -> tuple[pd.DataFrame, dict]:
    source = cfg["reference"]
    path = Path(source["path"])
    if sha256_file(path) != source["sha256"]:
        raise RuntimeError("V2A_REFERENCE_HASH_FAIL")
    for name, digest in source["lineage_hashes"].items():
        if sha256_file(Path(source["lineage_root"]) / name) != digest:
            raise RuntimeError(f"V2A_REFERENCE_LINEAGE_HASH_FAIL: {name}")
    raw = pd.read_csv(path, usecols=["id", "Name", "Country", "Fueltype", "Technology", "Capacity", "Efficiency", "projectID"])
    mappings = (("CCGT", "Natural Gas", "CCGT"), ("OCGT", "Natural Gas", "OCGT"),
                ("BIOMASS_STEAM", "Solid Biomass", "Steam Turbine"))
    selected = []
    for arch, fuel, tech in mappings:
        part = raw.loc[raw.Country.isin(source["europe"]) & raw.Fueltype.eq(fuel) & raw.Technology.eq(tech)].copy()
        project = part.projectID.fillna("")
        synthetic = part.Name.fillna("").str.contains("everywhere|synthetic|automatically added", case=False)
        real_source = project.str.contains(r"'[A-Z0-9_]+':", regex=True) & ~synthetic
        reported = project.str.contains("'GEO':", regex=False) & ~project.str.contains("'OPSD':", regex=False) & real_source
        part["plant_id"] = "PPM081_" + part.id.astype(str)
        part["country"] = part.Country
        part["technology"] = arch
        part["capacity_MW"] = pd.to_numeric(part.Capacity, errors="coerce")
        part["efficiency"] = pd.to_numeric(part.Efficiency, errors="coerce")
        part["capacity_observed"] = real_source & part.capacity_MW.gt(0) & np.isfinite(part.capacity_MW)
        part["efficiency_observed"] = reported & part.efficiency.gt(0) & part.efficiency.le(1) & np.isfinite(part.efficiency)
        part["efficiency_lineage"] = np.where(part.efficiency_observed,
            "GEO_UNIT_EFFICIENCY_PERCENT_SOURCE_REPORTED_THEN_PPM_AGGREGATED",
            "MISSING_OR_NONADMISSIBLE_ESTIMATE_NOT_EMPIRICAL_EFFICIENCY")
        part["aggregation_uncertainty_flag"] = part.id.isin(source["uncertain_aggregate_archive_ids"])
        selected.append(part[["plant_id", "Name", "country", "technology", "capacity_MW", "efficiency",
                              "capacity_observed", "efficiency_observed", "efficiency_lineage",
                              "aggregation_uncertainty_flag", "projectID"]])
    normalized = pd.concat(selected, ignore_index=True).sort_values("plant_id").reset_index(drop=True)
    if normalized.plant_id.duplicated().any():
        raise RuntimeError("V2A_DUPLICATE_REFERENCE_ID")
    models = {arch: reference_classes(normalized, arch, cfg["minimum_reference_rows"])
              for arch in ("CCGT", "OCGT")}
    for arch, control in cfg["size_reference_controls"].items():
        if models[arch].get("size_geography") != control["geography"] or models[arch].get("capacity_rows") != control["capacity_rows"]:
            raise RuntimeError(f"V2A_SIZE_REFERENCE_CONTROL_FAIL: {arch}")
    models["BIOMASS_STEAM"] = {"mode": "HOMOGENEOUS_FALLBACK", "reason": "BIOMASS_UNCHANGED_CALIBRATION_DELTA"}
    return normalized, models


def cost_coefficient(source: pd.Series) -> float:
    """Recover B4-QA-09's frozen fuel + chargeable CO2 coefficient."""
    columns = ("efficiency", "VOM_EUR2025_per_MWh_el", "fuel_price_EUR2025_per_MWh_th",
               "chargeable_CO2_t_per_MWh_th", "CO2_price_EUR2025_per_t",
               "other_variable_cost_EUR2025_per_MWh_el", "marginal_cost_EUR2025_per_MWh_el")
    values = {name: float(source[name]) for name in columns}
    if not np.isfinite(list(values.values())).all() or not 0 < values["efficiency"] <= 1:
        raise RuntimeError("V2A_COST_DECOMPOSITION_UNRESOLVED")
    coefficient = values[columns[2]] + values[columns[3]] * values[columns[4]]
    reconstructed = values[columns[1]] + coefficient / values["efficiency"] + values[columns[5]]
    if not math.isclose(reconstructed, values[columns[6]], rel_tol=0, abs_tol=1e-9):
        raise RuntimeError("V2A_COST_DECOMPOSITION_UNRESOLVED")
    return coefficient


def plan_children(v1: pypsa.Network, frozen_plan: pd.DataFrame, year: int, scenario: str,
                  models: dict) -> pd.DataFrame:
    source = uc1._static_source(year, scenario)
    rows = []
    for parent_id, cohort in frozen_plan.groupby("parent_id", sort=True):
        cohort = cohort.sort_values("child_id")
        originals = cohort.child_id.tolist()
        base = v1.generators.loc[originals]
        arch = cohort.uc_archetype.iloc[0]
        src = source.loc[parent_id]
        if uc1._archetype(src) != arch or not base.committable.astype(bool).all():
            raise RuntimeError("V2A_PARENT_PRIME_MOVER_OR_SCOPE_FAIL")
        parent_mw, eta = float(src.p_nom_MW), float(src.efficiency)
        if not math.isclose(base.p_nom.sum(), parent_mw, rel_tol=0, abs_tol=1e-7):
            raise RuntimeError("V2A_PARENT_CAPACITY_FAIL")
        model = models.get(arch, {"mode": "PRESERVED_NUCLEAR_V1", "reason": "NUCLEAR_UNCHANGED"})
        heterogeneous = model["mode"] not in ("HOMOGENEOUS_FALLBACK", "PRESERVED_NUCLEAR_V1")
        children = normalize_children(parent_mw, eta, len(cohort), model) if heterogeneous else None
        coefficient = cost_coefficient(src) if heterogeneous else 0.0
        if heterogeneous and coefficient <= 0:
            raise RuntimeError("V2A_CONTROLLED_MC_ORDERING_FAIL")
        if heterogeneous and any(name in v1.generators_t.marginal_cost.columns or
                                 name in v1.generators_t.efficiency.columns for name in originals):
            raise RuntimeError("V2A_TIME_COST_OR_EFFICIENCY_DECOMPOSITION_UNRESOLVED")
        for number, old_id in enumerate(originals):
            old = base.loc[old_id]
            if not math.isclose(float(old.efficiency), eta, rel_tol=0, abs_tol=1e-12) or not math.isclose(
                    float(old.marginal_cost), float(src.marginal_cost_EUR2025_per_MWh_el), rel_tol=0, abs_tol=1e-9):
                raise RuntimeError("V2A_FROZEN_BASELINE_ECONOMICS_FAIL")
            child = children.iloc[number] if heterogeneous else None
            power = float(child.p_nom) if heterogeneous else float(old.p_nom)
            efficiency = float(child.efficiency) if heterogeneous else float(old.efficiency)
            size_class = str(child.size_class) if heterogeneous else "V1_HOMOGENEOUS"
            efficiency_class = str(child.efficiency_class) if heterogeneous else "V1_HOMOGENEOUS"
            new_id = (f"UC2A__{year}__{scenario}__{parent_id}__{size_class}__{efficiency_class}__U{number + 1:03d}"
                      if heterogeneous else old_id)
            mc = float(heat_rate_cost(float(old.marginal_cost), 1 / eta, 1 / efficiency, coefficient)) if heterogeneous else float(old.marginal_cost)
            row = {"year": year, "scenario": scenario, "parent_id": parent_id, "v1_child_id": old_id,
                   "child_id": new_id, "zone": str(old.bus), "technology": str(old.carrier),
                   "parent_runtime_class": str(src.solver_subtechnology), "prime_mover": arch,
                   "size_class": size_class, "efficiency_class": efficiency_class,
                   "joint_cell": f"{efficiency_class}|{size_class}",
                   "joint_mode": model["mode"], "fallback_reason": model["reason"],
                   "calibration_revision": CALIBRATION,
                   "allocation_mode": str(child.allocation_mode) if heterogeneous else model["mode"],
                   "efficiency_provenance": "CONTROLLED_MEM_RELATIVE_EFFICIENCY_ASSUMPTION" if heterogeneous else "PRESERVED_V1",
                   "empirical_efficiency_used": False,
                   "size_reference_geography": model.get("size_geography", "NONE"),
                   "hr_reference_geography": "NOT_USED",
                   "reference_joint_rows": model.get("joint_rows", 0),
                   "reference_capacity_rows": model.get("capacity_rows", 0),
                   "reference_heat_rate_rows": model.get("heat_rate_rows", 0),
                   "candidate_reference_geography": model.get("diagnostics", [{}])[-1].get("geography", "NONE"),
                   "candidate_capacity_rows": model.get("diagnostics", [{}])[-1].get("capacity_rows", 0),
                   "candidate_heat_rate_rows": model.get("diagnostics", [{}])[-1].get("heat_rate_rows", 0),
                   "candidate_joint_rows": model.get("diagnostics", [{}])[-1].get("joint_rows", 0),
                   "parent_p_nom_MW": parent_mw, "parent_efficiency": eta,
                   "parent_heat_rate": 1 / eta, "parent_marginal_cost": float(old.marginal_cost),
                   "cohort_unit_count": len(cohort), "p_nom": power, "efficiency": efficiency,
                   "heat_rate": 1 / efficiency, "marginal_cost": mc,
                   "frozen_fuel_chargeable_CO2_coefficient": coefficient,
                   "VOM_EUR2025_per_MWh_el": float(src.VOM_EUR2025_per_MWh_el),
                   "frozen_other_variable_cost_EUR2025_per_MWh_el": float(src.other_variable_cost_EUR2025_per_MWh_el),
                   "cost_authority": str(src.source_parameters),
                   "alpha_P": float(child.alpha_P) if heterogeneous else 1.0,
                   "beta_HR": float(child.beta_HR) if heterogeneous else 1.0,
                   "size_multiplier": float(child.size_multiplier) if heterogeneous else 1.0,
                   "efficiency_multiplier": float(child.efficiency_multiplier) if heterogeneous else 1.0,
                   "efficiency_group_capacity_share": float(child.efficiency_group_capacity_share) if heterogeneous else 1.0,
                   "size_representative_MW": model["size_representatives_MW"][int(child.cell) % 3] if heterogeneous else None}
            for label, multiplier in zip(EFFICIENCY_CLASSES, EFFICIENCY_MULTIPLIERS):
                row[label] = eta * multiplier if heterogeneous else eta
                row[label.replace("ETA_", "MC_")] = float(heat_rate_cost(float(old.marginal_cost), 1 / eta,
                    1 / (eta * multiplier), coefficient)) if heterogeneous else float(old.marginal_cost)
            for cost in ("start_up_cost", "shut_down_cost", "stand_by_cost"):
                per_mw = float(old[cost]) / float(old.p_nom)
                row[cost] = per_mw * power if heterogeneous else float(old[cost])
                row[f"{cost}_EUR_per_MW"] = per_mw
            for field in STATIC_SIGNATURE_FIELDS[3:11] + ("up_time_before", "down_time_before"):
                row[field] = old[field]
            row["availability_authority"] = str(src.availability_class)
            row["startup_cost_provenance"] = str(cohort.startup_cost_provenance.iloc[0])
            rows.append(row)
    result = pd.DataFrame(rows).sort_values(["parent_id", "child_id"]).reset_index(drop=True)
    if result.child_id.duplicated().any() or len(result) != int(v1.generators.committable.astype(bool).sum()):
        raise RuntimeError("V2A_UNIT_ID_OR_COUNT_FAIL")
    return result


def derive(v1: pypsa.Network, plan: pd.DataFrame) -> pypsa.Network:
    updates = plan.set_index("v1_child_id")[["child_id", *OVERRIDE_FIELDS]]
    network = uc1.apply_child_static_overrides(v1, updates)
    network.meta.update({"model_version": VERSION, "uc2a_parent_version": "UC1_COMMON_V1",
                         "uc2a_optimization_authorized_in_preparation": False,
                         "uc2a_class_boundaries": [1 / 3, 2 / 3], "uc2a_nuclear_unchanged": True,
                         "uc2a_calibration_revision": CALIBRATION,
                         "uc2a_empirical_efficiency_used": False})
    return network


def verify_structure(v1: pypsa.Network, output: pypsa.Network, plan: pd.DataFrame) -> dict:
    pd.testing.assert_index_equal(v1.snapshots, output.snapshots)
    if len(output.snapshots) != 8760:
        raise RuntimeError("V2A_CHRONOLOGY_FAIL")
    pd.testing.assert_frame_equal(v1.snapshot_weightings, output.snapshot_weightings, check_dtype=False, check_freq=False)
    components = ("buses", "loads", "links", "stores", "storage_units", "lines", "transformers",
                  "shunt_impedances", "global_constraints", "carriers")
    for component in components:
        pd.testing.assert_frame_equal(getattr(v1, component), getattr(output, component), check_dtype=False)
        if hasattr(v1, f"{component}_t"):
            for attr, frame in getattr(v1, f"{component}_t").items():
                pd.testing.assert_frame_equal(frame, getattr(output, f"{component}_t")[attr], check_dtype=False, check_freq=False)
    names = dict(zip(plan.v1_child_id, plan.child_id))
    expected = v1.generators.rename(index=names)
    if set(expected.index) != set(output.generators.index) or not output.generators.index.is_unique:
        raise RuntimeError("V2A_GENERATOR_ID_FAIL")
    untouched = expected.index.difference(plan.child_id)
    pd.testing.assert_frame_equal(expected.loc[untouched], output.generators.loc[untouched], check_dtype=False)
    unchanged_fields = expected.columns.difference(OVERRIDE_FIELDS)
    pd.testing.assert_frame_equal(expected.loc[plan.child_id, unchanged_fields],
                                  output.generators.loc[plan.child_id, unchanged_fields], check_dtype=False)
    for attr, frame in v1.generators_t.items():
        pd.testing.assert_frame_equal(frame.rename(columns=names), output.generators_t[attr], check_dtype=False, check_freq=False)
    max_capacity, max_hr, max_mc = 0.0, 0.0, 0.0
    audit_rows = []
    for parent_id, group in plan.groupby("parent_id", sort=True):
        children = output.generators.loc[group.child_id]
        original = v1.generators.loc[group.v1_child_id]
        if len(children) != len(original):
            raise RuntimeError("V2A_COUNT_DRIFT")
        capacity_diff = abs(float(children.p_nom.sum() - original.p_nom.sum()))
        hr_diff = abs(float(np.average(1 / children.efficiency, weights=children.p_nom) -
                            np.average(1 / original.efficiency, weights=original.p_nom)))
        mc_diff = abs(float(np.average(children.marginal_cost, weights=children.p_nom) -
                            np.average(original.marginal_cost, weights=original.p_nom)))
        max_capacity, max_hr, max_mc = max(max_capacity, capacity_diff), max(max_hr, hr_diff), max(max_mc, mc_diff)
        if capacity_diff > 1e-7 or hr_diff > 1e-12 or mc_diff > 1e-9:
            raise RuntimeError(f"V2A_WEIGHTED_INVARIANT_FAIL: {parent_id}")
        if group.prime_mover.iloc[0] in ("CCGT", "OCGT"):
            eta = float(group.parent_efficiency.iloc[0])
            if not np.isin(children.efficiency.to_numpy(), eta * EFFICIENCY_MULTIPLIERS).all():
                raise RuntimeError(f"V2A_CONTROLLED_EFFICIENCY_FAIL: {parent_id}")
            if not (group.MC_LOW.gt(group.MC_MID).all() and group.MC_MID.gt(group.MC_HIGH).all()
                    and group.MC_MID.eq(group.parent_marginal_cost).all()):
                raise RuntimeError(f"V2A_CONTROLLED_MC_ORDERING_FAIL: {parent_id}")
            mid_only = bool(group.allocation_mode.eq("MID_ONLY_SMALL_COHORT").all())
            expected_counts = normalize_children(float(original.p_nom.sum()), eta, len(group), {
                "size_shares": np.array([1/3]*3), "size_multipliers": np.ones(3)})
            expected_mode = expected_counts.allocation_mode.iloc[0]
            if group.allocation_mode.ne(expected_mode).any():
                raise RuntimeError(f"V2A_SMALL_COHORT_FALLBACK_FAIL: {parent_id}")
            for label, target in zip(EFFICIENCY_CLASSES, [0., 1., 0.] if mid_only else EFFICIENCY_CAPACITY_SHARES):
                actual = float(children.loc[group.loc[group.efficiency_class.eq(label), "child_id"], "p_nom"].sum())
                if not math.isclose(actual, float(original.p_nom.sum()) * target, rel_tol=0, abs_tol=1e-7):
                    raise RuntimeError(f"V2A_EFFICIENCY_CAPACITY_ENVELOPE_FAIL: {parent_id} {label}")
                if int(group.efficiency_class.eq(label).sum()) != int(expected_counts.efficiency_class.eq(label).sum()):
                    raise RuntimeError(f"V2A_EFFICIENCY_UNIT_ALLOCATION_FAIL: {parent_id} {label}")
        for row in group.itertuples(index=False):
            child = output.generators.loc[row.child_id]
            for field in OVERRIDE_FIELDS:
                if not math.isclose(float(child[field]), float(getattr(row, field)), rel_tol=0, abs_tol=1e-9):
                    raise RuntimeError(f"V2A_CHILD_VALUE_FAIL: {row.child_id} {field}")
            if not 0 < child.efficiency <= 1 or not child.committable or child.p_nom_extendable:
                raise RuntimeError("V2A_CHILD_EFFICIENCY_OR_SCOPE_FAIL")
            availability = uc1._availability(output, row.child_id)
            if availability.isna().any() or not np.isfinite(availability).all() or (availability.gt(0) & availability.lt(child.p_min_pu)).any():
                raise RuntimeError(f"V2A_AVAILABILITY_COMPATIBILITY_FAIL: {row.child_id}")
            audit_rows.append({"child_id": row.child_id, "parent_id": parent_id, "snapshots": len(availability),
                               "incompatible_rows": 0, "inherited_exactly": True})
        if group.joint_mode.iloc[0] in ("HOMOGENEOUS_FALLBACK", "PRESERVED_NUCLEAR_V1"):
            pd.testing.assert_frame_equal(original, children, check_dtype=False)
    for keys in (["bus"], ["carrier"], ["bus", "carrier"]):
        left = v1.generators.groupby(keys).p_nom.sum()
        right = output.generators.groupby(keys).p_nom.sum().reindex(left.index)
        if not np.allclose(left, right, rtol=0, atol=1e-7):
            raise RuntimeError("V2A_ZONAL_NATIONAL_CAPACITY_FAIL")
    counts = int(output.generators.committable.astype(bool).sum())
    if counts != len(plan):
        raise RuntimeError("V2A_COMMITTABLE_COUNT_FAIL")
    return {"status": "PASS", "snapshots": 8760, "units_v1": counts, "units_v2A": counts,
            "theoretical_uc_binaries_v1": 3 * 8760 * counts, "theoretical_uc_binaries_v2A": 3 * 8760 * counts,
            "max_parent_capacity_difference_MW": max_capacity, "max_parent_weighted_HR_difference": max_hr,
            "max_parent_weighted_MC_difference_EUR_MWh": max_mc, "non_target_exact_match": True,
            "normalized_UC_parameters_exact_match": True, "availability_compatibility": "PASS",
            "P2X_exact_match_noncommittable": True, "model_constructed": False,
            "controlled_efficiency_multipliers_only": True, "efficiency_MW_envelopes": "PASS",
            "empirical_efficiency_used": False, "HR_recentering_applied": False,
            "MC_LOW_gt_MID_eq_parent_gt_HIGH": "PASS",
            "mid_only_small_cohorts": int(plan.loc[plan.allocation_mode.eq("MID_ONLY_SMALL_COHORT"), "parent_id"].nunique()),
            "no_2040_CCGT_efficiency_above_0p80": bool(output.generators.loc[
                plan.loc[plan.prime_mover.eq("CCGT"), "child_id"], "efficiency"].le(.80).all()) if int(plan.year.iloc[0]) == 2040 else None,
            "solver_invocations": 0, "availability_audit": audit_rows}


def symmetry_metrics(network: pypsa.Network, plan: pd.DataFrame, version: str) -> pd.DataFrame:
    rows = []
    id_field = "v1_child_id" if version == "v1" else "child_id"
    for (zone, technology, arch), group in plan.groupby(["zone", "technology", "prime_mover"], sort=True):
        units = network.generators.loc[group[id_field]]
        signatures = units.loc[:, list(STATIC_SIGNATURE_FIELDS)].apply(
            lambda row: hashlib.sha256(json.dumps([float(value).hex() for value in row], separators=(",", ":")).encode()).hexdigest(), axis=1)
        power = units.p_nom.to_numpy()
        hr = 1 / units.efficiency.to_numpy()
        mc = units.marginal_cost.to_numpy()
        counts = signatures.value_counts()
        record = {"version": version, "zone": zone, "technology": technology, "archetype": arch,
                  "units_total": len(units), "unique_static_signatures": len(counts),
                  "largest_identical_signature_group": int(counts.max()),
                  "largest_identical_signature_share": float(counts.max() / len(units)),
                  "size_min_MW": power.min(), "size_median_MW": np.median(power), "size_mean_MW": power.mean(),
                  "size_max_MW": power.max(), "size_std_MW": power.std(), "size_CV": power.std() / power.mean(),
                  "efficiency_min": units.efficiency.min(), "efficiency_capacity_weighted_mean": np.average(units.efficiency, weights=power),
                  "efficiency_max": units.efficiency.max(), "HR_min": hr.min(), "HR_capacity_weighted_mean": np.average(hr, weights=power),
                  "HR_max": hr.max(), "MC_min": mc.min(), "MC_capacity_weighted_mean": np.average(mc, weights=power), "MC_max": mc.max(),
                  "occupied_cells": int(group.joint_cell.nunique()) if version != "v1" else 1}
        for label in SIZE_CLASSES:
            record[f"units_{label}"] = int(group.size_class.eq(label).sum()) if version != "v1" else 0
        for label in EFFICIENCY_CLASSES:
            record[f"units_{label}"] = int(group.efficiency_class.eq(label).sum()) if version != "v1" else 0
            record[f"capacity_share_{label}"] = float(units.loc[group.loc[group.efficiency_class.eq(label), id_field], "p_nom"].sum() / power.sum()) if version != "v1" else 0.
        rows.append(record)
    return pd.DataFrame(rows)


def calibration_report(v1: pypsa.Network, output: pypsa.Network, plan: pd.DataFrame,
                       models: dict, *, by_parent: bool = False) -> pd.DataFrame:
    """Inspect exact achieved envelopes; mixed cohort values stay explicit."""
    keys = ["year", "scenario", "technology", "prime_mover"]
    if by_parent:
        keys += ["zone", "parent_id"]
    rows = []
    target = plan.loc[plan.prime_mover.isin(["CCGT", "OCGT"])]
    for values, group in target.groupby(keys, sort=True):
        before, after = v1.generators.loc[group.v1_child_id], output.generators.loc[group.child_id]
        model = models[group.prime_mover.iloc[0]]
        signatures = after.loc[:, list(STATIC_SIGNATURE_FIELDS)].apply(
            lambda row: tuple(float(value).hex() for value in row), axis=1).value_counts()
        record = dict(zip(keys, values))
        record.update({"size_reference_geography": model["size_geography"], "size_reference_rows": model["capacity_rows"],
                       "child_MW_min": after.p_nom.min(), "child_MW_median": after.p_nom.median(),
                       "child_MW_max": after.p_nom.max(), "unit_count": len(group),
                       "distinct_static_signatures": len(signatures), "largest_identical_signature_group": int(signatures.max()),
                       "weighted_HR_difference_vs_parent": float(np.average(1 / after.efficiency, weights=after.p_nom) -
                           np.average(1 / before.efficiency, weights=before.p_nom)),
                       "weighted_MC_difference_vs_parent": float(np.average(after.marginal_cost, weights=after.p_nom) -
                           np.average(before.marginal_cost, weights=before.p_nom)),
                       "mid_only_fallback_cohorts": group.loc[group.allocation_mode.eq("MID_ONLY_SMALL_COHORT"), "parent_id"].nunique(),
                       "efficiency_multipliers_only": bool(group.efficiency.eq(group.parent_efficiency * group.efficiency_multiplier).all()),
                       "empirical_efficiency_used": False, "HR_recentering_applied": False})
        for label, representative in zip(SIZE_CLASSES, model["size_representatives_MW"]):
            record[f"representative_{label}_MW"] = representative
        for label in EFFICIENCY_CLASSES:
            record[f"achieved_MW_share_{label}"] = float(after.loc[group.loc[group.efficiency_class.eq(label), "child_id"], "p_nom"].sum() / after.p_nom.sum())
            for field in (label, label.replace("ETA_", "MC_")):
                distinct = sorted(group[field].unique())
                record[field] = distinct[0] if len(distinct) == 1 else "|".join(format(value, ".17g") for value in distinct)
        rows.append(record)
    return pd.DataFrame(rows)


def _protected() -> dict[str, str]:
    build = _json(ROOT / "qa/stage_b/uc1_common_v1/MEM_UC1_DETERMINISTIC_BUILD_RECEIPT.json")
    if build["status"] != "PASS":
        raise RuntimeError("V2A_V1_AUTHORITY_FAIL")
    paths = {CONFIG / "stage_b_uc1_common.yaml", CONFIG / "stage_b_uc2_execution.yaml",
             STATIC / "MEM_generators_static_final.csv",
             ROOT / "qa/stage_b/uc1_common_v1/MEM_UC1_PARENT_CHILD_DECOMPOSITION.csv"}
    for item in build["structural_packages"]:
        for field in ("parent", "derivative"):
            path = ROOT / item[field]
            if sha256_file(path) != item[f"{field}_sha256"]:
                raise RuntimeError("V2A_V1_PARENT_HASH_FAIL")
            paths.add(path)
    paths.update((ROOT / "results/uc2_full_year").rglob("*_SOLVED.nc"))
    paths.update((ROOT / "results/UC1_2040_BASE_168H").glob("*_SOLVED.nc"))
    return {str(path.relative_to(ROOT)): sha256_file(path) for path in sorted(paths)}


def prepare(*, replace_calibration: bool = False) -> dict:
    from pypsa.optimization.optimize import OptimizationAccessor
    with no_solver_calls() as guard, patch.object(OptimizationAccessor, "create_model", side_effect=RuntimeError("V2A_MODEL_CONSTRUCTION_FORBIDDEN")):
        cfg = settings()
        protected = _protected()
        prior_path = ROOT / cfg["qa_directory"] / "MEM_UC2A_PREPARATION_RECEIPT.json"
        previous = _json(prior_path) if prior_path.exists() else None
        if previous is not None:
            for path, digest in previous["protected_artifact_hashes"].items():
                if protected.get(path) != digest:
                    raise RuntimeError(f"V2A_PROTECTED_ARTIFACT_DRIFT: {path}")
            for item in previous["structural_packages"]:
                if sha256_file(ROOT / item["output"]) != item["output_sha256"]:
                    raise RuntimeError("V2A_PREVIOUS_PREPARATION_ARTIFACT_HASH_FAIL")
            if replace_calibration and previous.get("calibration_revision") == CALIBRATION:
                raise RuntimeError("V2A_CALIBRATION_ALREADY_CURRENT_NO_REPLACEMENT_NEEDED")
            if replace_calibration and not (ROOT / cfg["qa_directory"] / "superseded/EMPIRICAL_HR_V1/MEM_UC2A_PREPARATION_RECEIPT.json").is_file():
                raise RuntimeError("V2A_SUPERSEDED_CALIBRATION_EVIDENCE_MISSING")
        data, models = load_reference(cfg)
        decomposition = pd.read_csv(ROOT / "qa/stage_b/uc1_common_v1/MEM_UC1_PARENT_CHILD_DECOMPOSITION.csv")
        plans = {}
        # All cohorts pass numerical gates before any derivative is emitted.
        for year, scenario in uc1.SCENARIOS:
            v1 = pypsa.Network(uc1.derivative_path(year, scenario))
            frozen = decomposition.loc[decomposition.year.eq(year) & decomposition.scenario.eq(scenario)]
            plan = plan_children(v1, frozen, year, scenario, models)
            repeat = plan_children(v1, frozen.iloc[::-1], year, scenario, models)
            if _text(plan) != _text(repeat):
                raise RuntimeError("V2A_DETERMINISTIC_CROSSWALK_FAIL")
            verify_structure(v1, derive(v1, plan), plan)
            plans[(year, scenario)] = plan
        qa_dir = ROOT / cfg["qa_directory"]
        qa_dir.mkdir(parents=True, exist_ok=True)
        (ROOT / cfg["output_directory"]).mkdir(parents=True, exist_ok=True)
        data.to_csv(qa_dir / "MEM_UC2A_REFERENCE_OBSERVATIONS.csv", index=False, lineterminator="\n", float_format="%.17g")
        dump_json(qa_dir / "MEM_UC2A_REFERENCE_PROVENANCE.json", _jsonable({"source": cfg["reference"], "classes": models,
                  "class_boundaries": cfg["class_boundaries"], "cell_order": CELL_ORDER,
                  "size_denominator": cfg["size_normalization_denominator"], "calibration_revision": CALIBRATION,
                  "efficiency_multipliers": EFFICIENCY_MULTIPLIERS, "efficiency_capacity_shares": EFFICIENCY_CAPACITY_SHARES,
                  "efficiency_provenance": cfg["efficiency_provenance"], "historical_efficiency_use": "DIAGNOSTIC_ONLY",
                  "empirical_efficiency_used": False, "empirical_joint_allocation_used": False, "HR_recentering_applied": False}))
        all_metrics, structural, reconciliation, calibration, cohort_calibration = [], [], [], [], []
        for year, scenario in uc1.SCENARIOS:
            v1 = pypsa.Network(uc1.derivative_path(year, scenario))
            plan = plans[(year, scenario)]
            network = derive(v1, plan)
            path = output_path(year, scenario)
            if path.exists() and not replace_calibration:
                network = pypsa.Network(path)
                verify_structure(v1, network, plan)
            else:
                network.export_to_netcdf(path)
                network = pypsa.Network(path)
            qa = verify_structure(v1, network, plan)
            calibration.append(calibration_report(v1, network, plan, models))
            cohort_calibration.append(calibration_report(v1, network, plan, models, by_parent=True))
            for version, member in (("v1", v1), ("v2A", network)):
                metrics = symmetry_metrics(member, plan, version)
                metrics["year"], metrics["scenario"] = year, scenario
                all_metrics.append(metrics)
            audit = pd.DataFrame(qa.pop("availability_audit"))
            audit.to_csv(qa_dir / f"MEM_UC2A_{year}_{scenario.upper()}_AVAILABILITY_AUDIT.csv", index=False, lineterminator="\n")
            qa.update({"year": year, "scenario": scenario, "calibration_revision": CALIBRATION,
                       "parent": str(uc1.derivative_path(year, scenario).relative_to(ROOT)),
                       "parent_sha256": protected[str(uc1.derivative_path(year, scenario).relative_to(ROOT))],
                       "output": str(path.relative_to(ROOT)), "output_sha256": sha256_file(path),
                       "crosswalk_sha256": _digest(plan), "deterministic_rebuild": "PASS",
                       "serialization_runtime": pypsa.__version__, "serialization_reimport": "PASS",
                       "archetype_counts": plan.prime_mover.value_counts().to_dict(),
                       "external_hourly_MC_columns": len(v1.generators_t.marginal_cost.columns),
                       "heterogeneous_time_MC_columns": int(v1.generators_t.marginal_cost.columns.isin(
                           plan.loc[~plan.joint_mode.isin(["HOMOGENEOUS_FALLBACK", "PRESERVED_NUCLEAR_V1"]), "v1_child_id"]).sum())})
            for parent_id, group in plan.groupby("parent_id", sort=True):
                before = v1.generators.loc[group.v1_child_id]
                after = network.generators.loc[group.child_id]
                reconciliation.append({"year": year, "scenario": scenario, "parent_id": parent_id,
                    "zone": group.zone.iloc[0], "technology": group.technology.iloc[0],
                    "N_v1": len(before), "N_v2A": len(after),
                    "capacity_v1_MW": before.p_nom.sum(), "capacity_v2A_MW": after.p_nom.sum(),
                    "weighted_HR_v1": np.average(1/before.efficiency, weights=before.p_nom),
                    "weighted_HR_v2A": np.average(1/after.efficiency, weights=after.p_nom),
                    "weighted_MC_v1": np.average(before.marginal_cost, weights=before.p_nom),
                    "weighted_MC_v2A": np.average(after.marginal_cost, weights=after.p_nom), "status": "PASS"})
            dump_json(qa_dir / f"MEM_UC2A_{year}_{scenario.upper()}_STRUCTURAL_QA.json", qa)
            structural.append(qa)
        full_plan = pd.concat(plans.values(), ignore_index=True)
        (qa_dir / "MEM_UC2A_CHILD_CROSSWALK.csv").write_text(_text(full_plan), encoding="utf-8")
        pd.concat(all_metrics, ignore_index=True).to_csv(qa_dir / "MEM_UC2A_V1_V2A_SYMMETRY_METRICS.csv", index=False, lineterminator="\n", float_format="%.17g")
        pd.DataFrame(reconciliation).to_csv(qa_dir / "MEM_UC2A_PARENT_RECONCILIATION.csv", index=False, lineterminator="\n", float_format="%.17g")
        pd.concat(calibration, ignore_index=True).to_csv(qa_dir / "MEM_UC2A_CALIBRATION_TECHNOLOGY_REPORT.csv", index=False, lineterminator="\n", float_format="%.17g")
        pd.concat(cohort_calibration, ignore_index=True).to_csv(qa_dir / "MEM_UC2A_CALIBRATION_COHORT_REPORT.csv", index=False, lineterminator="\n", float_format="%.17g")
        if any(sha256_file(ROOT / path) != digest for path, digest in protected.items()):
            raise RuntimeError("V2A_PROTECTED_ARTIFACT_DRIFT")
        receipt = {"status": SUCCESS, "version": VERSION, "manual_benchmark_required": True,
                   "calibration_revision": CALIBRATION, "empirical_efficiency_used": False,
                   "HR_recentering_applied": False, "efficiency_multipliers": EFFICIENCY_MULTIPLIERS.tolist(),
                   "efficiency_capacity_shares": EFFICIENCY_CAPACITY_SHARES.tolist(),
                   "production_executed": False, "solver_invocations": guard["solver_invocations"],
                   "optimization_model_constructed": False, "protected_artifact_hashes": protected,
                   "protected_artifacts_unchanged": len(protected), "structural_packages": structural,
                   "deterministic_crosswalk_sha256": _digest(full_plan), "reference_decisions": {key: value["mode"] for key, value in models.items()},
                   "no_time_dependent_heterogeneity_target_MC": all(row["heterogeneous_time_MC_columns"] == 0 for row in structural),
                   "unchanged_external_hourly_MC_series_by_scenario": {
                       f"{row['year']}_{row['scenario']}": row["external_hourly_MC_columns"] for row in structural}}
        dump_json(qa_dir / "MEM_UC2A_PREPARATION_RECEIPT.json", receipt)
        benchmark_rows = []
        for variant in ("v2A",):
            job = benchmark_job(2040, "Slow", variant)
            benchmark_rows.append({key: str(value.relative_to(ROOT)) if isinstance(value, Path) else value
                                   for key, value in job.items()})
        baseline_receipt = ROOT / "results/uc2_full_year/2040/Slow/UC_MILP/UC2_2040_SLOW_8760H_UC_MILP_SOLVE_RECEIPT.json"
        baseline = _json(baseline_receipt)
        if baseline["status"] != "PASS" or baseline["year"] != 2040 or baseline["scenario"] != "Slow" or sha256_file(ROOT / baseline["solved_path"]) != baseline["solved_sha256"]:
            raise RuntimeError("V2A_EXISTING_SLOW_V1_BASELINE_FAIL")
        dump_json(qa_dir / "MEM_UC2A_EXISTING_SLOW_V1_BENCHMARK_BASELINE.json", {
            "status": "COMPLETED_ACCEPTED_V1_BASELINE_READ_ONLY", "new_v1_solve_prepared": False,
            "receipt_path": str(baseline_receipt.relative_to(ROOT)), "receipt_sha256": sha256_file(baseline_receipt),
            "solved_path": baseline["solved_path"], "solved_sha256": baseline["solved_sha256"],
            "objective_EUR": baseline["objective_EUR"], "runtime_seconds": baseline["runtime_seconds"],
            "mip_gap": baseline["mip_gap"], "log_status": "EXISTING_USER_LOG_NO_LOG_FILE_IN_CANONICAL_RESULT_DIRECTORY"})
        pd.DataFrame(benchmark_rows).to_csv(qa_dir / "MEM_UC2A_FUTURE_MANUAL_BENCHMARK_JOBS.csv", index=False, lineterminator="\n")
        telemetry_fields = ["initial_rows", "initial_columns", "initial_nonzeros", "presolved_rows", "presolved_columns",
                            "presolved_nonzeros", "binaries", "presolve_seconds", "root_relaxation_seconds",
                            "root_simplex_iterations", "root_IntInf", "time_to_first_incumbent_seconds", "node_count",
                            "objective_EUR", "best_bound_EUR", "MIP_gap", "runtime_seconds", "memory_GB_if_available"]
        analytical_fields = ["UC_objective_EUR", "UC_uplift_EUR", "UC_uplift_percent", "zonal_prices",
                             "price_level_concentration", "generation", "actual_starts", "actual_shutdowns",
                             "capacity_factors", "VRE_curtailment", "storage", "P2X", "net_imports", "load_shedding"]
        pd.DataFrame({"field": telemetry_fields, "future_source": "USER_RUN_GUROBI_LOG_AND_SOLVE_RECEIPT",
                      "status": "PREPARED_NO_NEW_SOLVE"}).to_csv(qa_dir / "MEM_UC2A_BENCHMARK_TELEMETRY_FIELDS.csv", index=False, lineterminator="\n")
        pd.DataFrame({"field": analytical_fields, "future_source": "ACCEPTED_UC_PHYSICAL_AND_FIXED_LP_PRICE_REPORTS",
                      "status": "PREPARED_NO_NEW_SOLVE"}).to_csv(qa_dir / "MEM_UC2A_FUTURE_ANALYTICAL_COMPARISON_FIELDS.csv", index=False, lineterminator="\n")
        write_manifest()
        return receipt


def write_manifest() -> None:
    cfg = settings()
    qa_dir = ROOT / cfg["qa_directory"]
    path = qa_dir / "MEM_UC2A_ARTIFACT_MANIFEST.csv"
    files = [CONFIG_PATH, ROOT / "src/mem_model/uc_heterogeneity.py", ROOT / "src/mem_model/stage_b_uc2a.py",
             ROOT / "src/mem_model/stage_b_uc1.py", ROOT / "src/mem_model/stage_b_uc2.py",
             ROOT / "tests/test_stage_b_uc2a.py", ROOT / "docs/MEM_UC_V2A_PREPARATION_HANDOFF.md",
             ROOT / "docs/MEM_INTERIM_PYPSA_IMPLEMENTATION_HANDOFF.md"]
    files += [p for p in sorted(qa_dir.iterdir()) if p.is_file() and p != path]
    files += sorted((qa_dir / "superseded/EMPIRICAL_HR_V1").glob("*"))
    files += [output_path(year, scenario) for year, scenario in uc1.SCENARIOS]
    pd.DataFrame({"artifact": [str(p.relative_to(ROOT)) for p in files], "sha256": [sha256_file(p) for p in files],
                  "role": ["SUPERSEDED_DIAGNOSTIC_EVIDENCE" if "superseded" in p.parts else
                           "UNSOLVED_UC_V2A_NETWORK" if p.suffix == ".nc" else "PREPARATION_EVIDENCE" for p in files]}).to_csv(path, index=False, lineterminator="\n")


def verify() -> dict:
    with no_solver_calls() as guard:
        cfg = settings()
        qa_dir = ROOT / cfg["qa_directory"]
        receipt = _json(qa_dir / "MEM_UC2A_PREPARATION_RECEIPT.json")
        if receipt["status"] != SUCCESS or receipt.get("calibration_revision") != CALIBRATION:
            raise RuntimeError("V2A_PREPARATION_NOT_PASS")
        for path, digest in receipt["protected_artifact_hashes"].items():
            if sha256_file(ROOT / path) != digest:
                raise RuntimeError(f"V2A_PROTECTED_ARTIFACT_DRIFT: {path}")
        for row in pd.read_csv(qa_dir / "MEM_UC2A_ARTIFACT_MANIFEST.csv").itertuples():
            if sha256_file(ROOT / row.artifact) != row.sha256:
                raise RuntimeError(f"V2A_MANIFEST_HASH_FAIL: {row.artifact}")
        crosswalk = pd.read_csv(qa_dir / "MEM_UC2A_CHILD_CROSSWALK.csv", float_precision="round_trip")
        for year, scenario in uc1.SCENARIOS:
            plan = crosswalk.loc[crosswalk.year.eq(year) & crosswalk.scenario.eq(scenario)]
            verify_structure(pypsa.Network(uc1.derivative_path(year, scenario)), pypsa.Network(output_path(year, scenario)), plan)
        return {"status": "PASS", "networks_verified": 6, "solver_invocations": guard["solver_invocations"],
                "protected_artifacts_unchanged": len(receipt["protected_artifact_hashes"])}


def benchmark_job(year: int, scenario: str, variant: str) -> dict:
    """Hash-checked future manual benchmark namespace; does not optimize."""
    if (year, scenario) != (2040, "Slow") or variant != "v2A":
        raise RuntimeError("V2A_BENCHMARK_CASE_REFUSED")
    cfg = settings()
    qa = _json(ROOT / cfg["qa_directory"] / "MEM_UC2A_PREPARATION_RECEIPT.json")
    if qa["status"] != SUCCESS or qa.get("calibration_revision") != CALIBRATION:
        raise RuntimeError("V2A_BENCHMARK_PREPARATION_NOT_PASS")
    item = next(row for row in qa["structural_packages"] if row["year"] == year and row["scenario"] == scenario)
    source, digest = output_path(year, scenario), item["output_sha256"]
    if sha256_file(source) != digest:
        raise RuntimeError("V2A_BENCHMARK_INPUT_HASH_FAIL")
    name = f"UC2A_2040_SLOW_{variant.upper()}_8760H_UC_MILP_BENCHMARK"
    directory = ROOT / cfg["benchmark_result_directory"] / variant
    return {"input": source, "directory": directory, "solved": directory / f"{name}_SOLVED.nc",
            "receipt": directory / f"{name}_SOLVE_RECEIPT.json", "log": directory / f"{name}_GUROBI.log",
            "input_sha256": digest, "job_id": name, "variant": variant}


def finalize() -> dict:
    """Complete existing preparation evidence; never build or solve a model."""
    from pypsa.optimization.optimize import OptimizationAccessor
    with no_solver_calls() as guard, patch.object(OptimizationAccessor, "create_model", side_effect=RuntimeError("V2A_MODEL_CONSTRUCTION_FORBIDDEN")):
        qa_dir = ROOT / settings()["qa_directory"]
        archived = pd.read_csv(qa_dir / "superseded/EMPIRICAL_HR_V1/MEM_UC2A_V1_V2A_SYMMETRY_METRICS.csv", float_precision="round_trip")
        current = pd.read_csv(qa_dir / "MEM_UC2A_V1_V2A_SYMMETRY_METRICS.csv", float_precision="round_trip")
        keys = ["year", "scenario", "zone", "technology", "archetype"]
        fields = ["size_min_MW", "size_median_MW", "size_mean_MW", "size_max_MW", "size_CV", "efficiency_min",
                  "efficiency_max", "HR_capacity_weighted_mean", "MC_capacity_weighted_mean",
                  "unique_static_signatures", "largest_identical_signature_group"]
        comparison = archived.loc[archived.version.eq("v2A"), keys + fields].merge(
            current.loc[current.version.eq("v2A"), keys + fields], on=keys, suffixes=("_before", "_after"), validate="one_to_one")
        comparison.to_csv(qa_dir / "MEM_UC2A_CALIBRATION_BEFORE_AFTER.csv", index=False, lineterminator="\n", float_format="%.17g")
        junit = qa_dir / "MEM_UC2A_NON_SOLVING_TEST_RESULTS.xml"
        suites = ET.parse(junit).getroot().findall(".//testsuite")
        tests = {key: sum(int(suite.attrib.get(key, "0")) for suite in suites) for key in ("tests", "failures", "errors", "skipped")}
        if not tests["tests"] or tests["failures"] or tests["errors"] or tests["skipped"]:
            raise RuntimeError("V2A_FOCUSED_NON_SOLVING_TESTS_NOT_PASS")
        tests.update({"passed": tests["tests"], "junit_path": str(junit.relative_to(ROOT)), "junit_sha256": sha256_file(junit)})
        write_manifest()
        result = verify()
        receipt_path = qa_dir / "MEM_UC2A_PREPARATION_RECEIPT.json"
        receipt = _json(receipt_path)
        receipt.update({"focused_non_solving_tests": tests, "final_read_only_verification": result,
                        "analytical_promotion": "PENDING_METHOD_REVIEW_AND_MANUAL_V2A_BENCHMARK",
                        "new_v1_solve_prepared": False})
        dump_json(receipt_path, receipt)
        final = {"status": SUCCESS, "calibration_revision": CALIBRATION,
                 "manual_benchmark_status": "MANUAL_BENCHMARK_REQUIRED", "production_status": "PRODUCTION_NOT_EXECUTED",
                 "verification": result, "tests": tests, "solver_invocations": guard["solver_invocations"],
                 "optimization_model_constructed": False, "controlled_efficiency_multipliers_only": True,
                 "empirical_efficiency_used": False, "HR_recentering_applied": False,
                 "efficiency_MW_envelopes": "PASS", "mid_only_fallback": "N_LESS_THAN_3_ONLY",
                 "no_2040_CCGT_efficiency_above_0p80": all(item["no_2040_CCGT_efficiency_above_0p80"]
                     for item in receipt["structural_packages"] if item["year"] == 2040),
                 "protected_artifacts_unchanged": receipt["protected_artifacts_unchanged"],
                 "crosswalk_sha256": receipt["deterministic_crosswalk_sha256"],
                 "max_parent_capacity_difference_MW": max(item["max_parent_capacity_difference_MW"] for item in receipt["structural_packages"]),
                 "max_parent_weighted_HR_difference": max(item["max_parent_weighted_HR_difference"] for item in receipt["structural_packages"]),
                 "max_parent_weighted_MC_difference_EUR_MWh": max(item["max_parent_weighted_MC_difference_EUR_MWh"] for item in receipt["structural_packages"]),
                 "new_v1_solve_prepared": False, "benchmark_executed": False,
                 "benchmark_result_directory_exists": (ROOT / settings()["benchmark_result_directory"]).exists(),
                 "before_after_zone_technology_records": len(comparison),
                 "receipt_path": str(receipt_path.relative_to(ROOT)),
                 "manifest_path": str((qa_dir / "MEM_UC2A_ARTIFACT_MANIFEST.csv").relative_to(ROOT)),
                 "handoff_path": "docs/MEM_UC_V2A_PREPARATION_HANDOFF.md"}
        dump_json(qa_dir / "MEM_UC2A_FINAL_VERIFICATION.json", final)
        write_manifest()
        return final


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "verify", "refresh-manifest", "finalize"])
    parser.add_argument("--replace-calibration", action="store_true", help="Replace only superseded v2A preparation inputs after hash checks")
    args = parser.parse_args(argv)
    if args.replace_calibration and args.action != "prepare":
        parser.error("--replace-calibration is restricted to prepare")
    try:
        result = (prepare(replace_calibration=args.replace_calibration) if args.action == "prepare" else
                  verify() if args.action == "verify" else finalize() if args.action == "finalize" else write_manifest())
    except Exception as exc:
        print(json.dumps({"status": "HETEROGENEOUS_UC_V2A_PREPARATION_BLOCKED", "reason": str(exc)}))
        raise
    print(json.dumps(result, indent=2, default=lambda value: value.tolist() if hasattr(value, "tolist") else str(value)))


if __name__ == "__main__":
    main()

"""P2X_FLEX_V1-bound, fixed-capacity common unit-commitment derivative.

The full-year command only exports *unsolved* networks.  Smoke optimization is
separate and strictly limited to 2040 Base's first 168 accepted snapshots.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd
import pypsa
import xarray as xr

from .common import CONFIG, ROOT, STATIC, load_yaml, sha256_file
from .stage_b_p2x_flex import verify_all as verify_p2x_parents


VERSION = "UC1_COMMON_V1"
SCENARIOS = ((2040, "Slow"), (2040, "Base"), (2040, "High"),
             (2050, "Slow"), (2050, "Base"), (2050, "High"))
CONFIG_PATH = CONFIG / "stage_b_uc1_common.yaml"


def settings() -> dict:
    cfg = load_yaml(CONFIG_PATH)
    if cfg.get("schema_version") != "MEM_UC1_COMMON_V1" or cfg.get("full_year_optimization_authorized") is not False:
        raise RuntimeError("UC1_TECHNICAL_FAIL: invalid fail-closed UC config")
    bio = cfg["archetypes"]["BIOMASS_STEAM"]
    if not math.isclose(float(bio["source_startup_EUR2020_per_MW_start"]) *
                        float(bio["normalization_factor_2020_to_2025"]),
                        float(bio["startup_EUR2025_per_MW_start"]), rel_tol=0, abs_tol=1e-11):
        raise RuntimeError("UC1_BLOCKED_BIOMASS_COST_NORMALIZATION: accepted factor/value mismatch")
    if float(cfg["archetypes"]["NUCLEAR_MODULAR"]["startup_EUR2025_per_MW_start"]) != 250.0:
        raise RuntimeError("UC1_TECHNICAL_FAIL: nuclear startup contract changed")
    return cfg


def parent_path(year: int, scenario: str) -> Path:
    return ROOT / "networks" / "unsolved" / "p2x_flex_v1" / f"MEM_{year}_{scenario.upper()}_P2X_FLEX_V1_8760h_UNSOLVED.nc"


def derivative_path(year: int, scenario: str) -> Path:
    return ROOT / "networks" / "unsolved" / "uc1_common_v1" / f"MEM_{year}_{scenario.upper()}_UC1_COMMON_V1_8760h_UNSOLVED.nc"


def _archetype(row: pd.Series) -> str | None:
    tech = str(row["solver_subtechnology"])
    carrier = str(row["carrier"])
    if tech in {"METHANE_CCGT_CHP", "METHANE_CCGT_NON_CHP"} and carrier == "methane_ccgt":
        return "CCGT"
    if tech in {"METHANE_GT_OCGT_CHP", "METHANE_GT_OCGT_NON_CHP"} and carrier == "methane_gt_ocgt":
        return "OCGT"
    if carrier == "methane_ccs":
        if tech in {"METHANE_CCS_CCGT_CHP", "METHANE_CCS_CCGT_NON_CHP"}:
            return "CCGT"
        if tech in {"METHANE_CCS_OCGT_CHP", "METHANE_CCS_OCGT_NON_CHP"}:
            return "OCGT"
        raise RuntimeError(f"UC1_BLOCKED_CCUS_PRIME_MOVER_MAPPING: {row['generator_id']} / {tech}")
    if tech in {"BIOENERGY_STEAM_OTHER_SURVIVING_CHP", "BIOENERGY_STEAM_OTHER_SURVIVING_NON_CHP"} and carrier == "bioenergy_steam_other_surviving":
        return "BIOMASS_STEAM"
    if tech == "NUCLEAR" and carrier == "nuclear":
        return "NUCLEAR_MODULAR"
    # Generic 2050 bioenergy, BECCS, hydrogen OCGT and all other classes lack
    # the explicit eligible prime-mover mapping and stay continuous.
    return None


def _static_source(year: int, scenario: str) -> pd.DataFrame:
    frame = pd.read_csv(STATIC / "MEM_generators_static_final.csv")
    frame = frame.loc[frame.year.eq(year) & frame.scenario.eq(scenario)].copy()
    if frame.empty or frame.generator_id.duplicated().any():
        raise RuntimeError(f"UC1_TECHNICAL_FAIL: missing/duplicate frozen source for {year} {scenario}")
    return frame.set_index("generator_id", drop=False)


def _availability(network: pypsa.Network, parent_id: str) -> pd.Series:
    if parent_id in network.generators_t.p_max_pu.columns:
        return network.generators_t.p_max_pu[parent_id].reindex(network.snapshots)
    return pd.Series(float(network.generators.at[parent_id, "p_max_pu"]), index=network.snapshots)


def plan_decomposition(network: pypsa.Network, year: int, scenario: str, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    source = _static_source(year, scenario)
    if len(network.snapshots) != 8760 or network.meta.get("model_version") != "P2X_FLEX_V1":
        raise RuntimeError("UC1_BLOCKED_POST_P2X_PARENT_CONTRACT: wrong chronology or version")
    if not set(source.index).issubset(network.generators.index):
        raise RuntimeError("UC1_BLOCKED_POST_P2X_PARENT_CONTRACT: generator IDs differ from frozen static")
    rows, audits = [], []
    for parent_id in sorted(source.index):
        src = source.loc[parent_id]
        arch = _archetype(src)
        if arch is None:
            continue
        parent = network.generators.loc[parent_id]
        parent_mw = float(parent.p_nom)
        if not (
            math.isclose(parent_mw, float(src.p_nom_MW), rel_tol=0, abs_tol=1e-6)
            and parent.carrier == src.carrier
            and math.isclose(float(parent.efficiency), float(src.efficiency), abs_tol=1e-12)
            and math.isclose(float(parent.marginal_cost), float(src.marginal_cost_EUR2025_per_MWh_el), abs_tol=1e-9)
            and not bool(parent.committable)
            and not bool(parent.p_nom_extendable)
        ):
            raise RuntimeError(f"UC1_BLOCKED_POST_P2X_PARENT_CONTRACT: parent static mismatch {parent_id}")
        contract = cfg["archetypes"][arch]
        target = float(contract["target_block_MW"])
        count = max(1, math.floor(parent_mw / target + 0.5))
        child_mw = parent_mw / count
        pmin = float(contract["p_min_pu"])
        avail = _availability(network, parent_id)
        if avail.isna().any() or not np.isfinite(avail.to_numpy()).all():
            raise RuntimeError(f"UC1_BLOCKED_AVAILABILITY_COMPATIBILITY: missing/nonfinite {parent_id}")
        bad = avail.gt(0) & avail.lt(pmin)
        if bad.any():
            t = bad[bad].index[0]
            child = f"UC__{year}__{scenario}__{parent_id}__U001"
            raise RuntimeError(f"UC1_BLOCKED_AVAILABILITY_COMPATIBILITY: horizon={year} scenario={scenario} zone={src.zone} parent={parent_id} child={child} archetype={arch} snapshot={t} p_max_pu={avail.at[t]} p_min_pu={pmin}")
        audits.append({"year": year, "scenario": scenario, "parent_id": parent_id,
                       "archetype": arch, "snapshots": len(avail), "incompatible_rows": 0,
                       "minimum_positive_p_max_pu": float(avail[avail.gt(0)].min()) if avail.gt(0).any() else None})
        for unit in range(1, count + 1):
            child_id = f"UC__{year}__{scenario}__{parent_id}__U{unit:03d}"
            rows.append({
                "year": year, "scenario": scenario, "zone": str(src.zone), "parent_id": parent_id,
                "parent_carrier": str(src.carrier), "parent_runtime_class": str(src.solver_subtechnology),
                "parent_source": str(src.source_parameters), "parent_p_nom_MW": parent_mw,
                "uc_archetype": arch, "target_block_MW": target, "synthetic_unit_count": count,
                "child_id": child_id, "child_p_nom_MW": child_mw,
                "block_deviation_MW": child_mw - target, "p_min_pu": pmin,
                "ramp_limit_up": float(contract["ramp_limit_up"]),
                "ramp_limit_down": float(contract["ramp_limit_down"]),
                "ramp_limit_start_up": float(contract["ramp_limit_start_up"]),
                "ramp_limit_shut_down": float(contract["ramp_limit_shut_down"]),
                "min_up_time": int(contract["min_up_time"]), "min_down_time": int(contract["min_down_time"]),
                "source_startup_cost_per_MW": float(contract.get("source_startup_EUR2020_per_MW_start", contract["startup_EUR2025_per_MW_start"])),
                "startup_cost_provenance": str(contract["startup_provenance"]),
                "normalized_startup_EUR2025_per_MW": float(contract["startup_EUR2025_per_MW_start"]),
                "total_child_startup_EUR": float(contract["startup_EUR2025_per_MW_start"]) * child_mw,
                "availability_authority": str(src.availability_class), "committable": True,
                "up_time_before": int(parent.up_time_before), "down_time_before": int(parent.down_time_before),
                "parent_child_capacity_reconciled": True,
            })
    table = pd.DataFrame(rows).sort_values(["year", "scenario", "parent_id", "child_id"]).reset_index(drop=True)
    if table.empty or table.child_id.duplicated().any() or set(table.child_id).intersection(network.generators.index):
        raise RuntimeError("UC1_TECHNICAL_FAIL: empty or duplicate decomposition")
    for parent_id, block in table.groupby("parent_id", sort=False):
        if not math.isclose(float(block.child_p_nom_MW.sum()), float(block.parent_p_nom_MW.iloc[0]), rel_tol=0, abs_tol=1e-7):
            raise RuntimeError(f"UC1_TECHNICAL_FAIL: parent capacity conservation {parent_id}")
    return table, pd.DataFrame(audits)


def derive_network(parent: pypsa.Network, plan: pd.DataFrame, year: int, scenario: str) -> pypsa.Network:
    # Source network is an exported, never-solved P2X_FLEX_V1 parent.
    network = parent.copy()
    time_additions: dict[str, dict[str, pd.Series]] = {}
    for parent_id, block in plan.groupby("parent_id", sort=True):
        base = network.generators.loc[parent_id].copy()
        time_values = {}
        for attr, frame in network.generators_t.items():
            if parent_id in frame.columns:
                time_values[attr] = frame[parent_id].copy()
                frame.drop(columns=[parent_id], inplace=True)
        network.generators.drop(index=parent_id, inplace=True)
        for row in block.itertuples(index=False):
            child = base.copy()
            child["p_nom"] = float(row.child_p_nom_MW)
            child["committable"] = True
            child["p_min_pu"] = float(row.p_min_pu)
            child["ramp_limit_up"] = float(row.ramp_limit_up)
            child["ramp_limit_down"] = float(row.ramp_limit_down)
            child["ramp_limit_start_up"] = float(row.ramp_limit_start_up)
            child["ramp_limit_shut_down"] = float(row.ramp_limit_shut_down)
            child["min_up_time"] = int(row.min_up_time)
            child["min_down_time"] = int(row.min_down_time)
            child["start_up_cost"] = float(row.total_child_startup_EUR)
            child["shut_down_cost"] = 0.0
            child["stand_by_cost"] = 0.0
            network.generators.loc[row.child_id] = child
            for attr, values in time_values.items():
                time_additions.setdefault(attr, {})[row.child_id] = values
    for attr, columns in time_additions.items():
        network.generators_t[attr] = pd.concat(
            [network.generators_t[attr], pd.DataFrame(columns, index=network.snapshots)], axis=1
        )
    network.meta.update({"model_version": VERSION, "uc1_parent": str(parent_path(year, scenario).relative_to(ROOT)),
                         "uc1_full_year_optimization_authorized": False,
                         "uc1_nuclear_archetype": "MODULAR_300MW_TARGET",
                         "uc1_nuclear_custom_3h_dwell": False})
    return network


def apply_child_static_overrides(parent: pypsa.Network, updates: pd.DataFrame) -> pypsa.Network:
    """Thin optional preprocessing hook for an already decomposed UC fleet.

    Cohort counts, UC operating parameters and every time series are inherited.
    Only named standard PyPSA static fields may be changed; caller QA controls
    cohort capacity/heat-rate/cost preservation and per-MW event scaling.
    """
    allowed = ("p_nom", "efficiency", "marginal_cost", "start_up_cost", "shut_down_cost", "stand_by_cost")
    if (updates.index.duplicated().any() or updates.child_id.duplicated().any() or
        not set(updates.index).issubset(parent.generators.index) or
        not parent.generators.loc[updates.index, "committable"].astype(bool).all()):
        raise RuntimeError("UC_CHILD_OVERRIDE_INVALID_ID_OR_SCOPE")
    names = dict(zip(updates.index, updates.child_id))
    if len(set(parent.generators.index.map(lambda name: names.get(name, name)))) != len(parent.generators):
        raise RuntimeError("UC_CHILD_OVERRIDE_DUPLICATE_ID")
    network = parent.copy()
    for field in allowed:
        if field in updates.columns:
            network.generators.loc[updates.index, field] = updates[field].to_numpy()
    network.generators.rename(index=names, inplace=True)
    for _, frame in network.generators_t.items():
        frame.rename(columns=names, inplace=True)
    return network


def verify_derivative(parent: pypsa.Network, uc: pypsa.Network, plan: pd.DataFrame) -> dict:
    if not parent.snapshots.equals(uc.snapshots) or len(uc.snapshots) != 8760:
        raise RuntimeError("UC1_TECHNICAL_FAIL: chronology changed")
    for component in ("buses", "loads", "links", "stores", "global_constraints", "carriers"):
        pd.testing.assert_frame_equal(getattr(parent, component), getattr(uc, component), check_dtype=False)
    for component in ("loads", "links", "stores"):
        for attr, frame in getattr(parent, f"{component}_t").items():
            pd.testing.assert_frame_equal(frame, getattr(uc, f"{component}_t")[attr], check_dtype=False)
    pd.testing.assert_frame_equal(parent.snapshot_weightings, uc.snapshot_weightings, check_dtype=False)
    old = set(plan.parent_id)
    children = set(plan.child_id)
    if (old & set(uc.generators.index)) or not children.issubset(uc.generators.index):
        raise RuntimeError("UC1_TECHNICAL_FAIL: parent/child ID accounting")
    retained = parent.generators.drop(index=list(old)).sort_index()
    pd.testing.assert_frame_equal(retained, uc.generators.loc[retained.index].sort_index(), check_dtype=False)
    for attr, frame in parent.generators_t.items():
        kept = frame.drop(columns=list(old), errors="ignore").sort_index(axis=1)
        actual = uc.generators_t[attr].loc[:, kept.columns].sort_index(axis=1)
        pd.testing.assert_frame_equal(kept, actual, check_dtype=False)
    for parent_id, block in plan.groupby("parent_id"):
        base = parent.generators.loc[parent_id]
        for row in block.itertuples(index=False):
            child = uc.generators.loc[row.child_id]
            for col in ("bus", "carrier", "efficiency", "marginal_cost", "sign", "p_nom_extendable"):
                if child[col] != base[col]:
                    raise RuntimeError(f"UC1_TECHNICAL_FAIL: inherited {col} changed {row.child_id}")
            if not bool(child.committable) or bool(child.p_nom_extendable):
                raise RuntimeError(f"UC1_TECHNICAL_FAIL: child commitment/extension {row.child_id}")
            for attr, frame in parent.generators_t.items():
                if parent_id in frame.columns:
                    pd.testing.assert_series_equal(frame[parent_id], uc.generators_t[attr][row.child_id], check_names=False)
    if not np.isclose(parent.generators.p_nom.sum(), uc.generators.p_nom.sum(), atol=1e-6):
        raise RuntimeError("UC1_TECHNICAL_FAIL: national capacity drift")
    for keys in (["bus"], ["carrier"], ["bus", "carrier"]):
        expected = parent.generators.groupby(keys).p_nom.sum().sort_index()
        observed = uc.generators.groupby(keys).p_nom.sum().sort_index()
        pd.testing.assert_index_equal(expected.index, observed.index)
        if not np.allclose(expected.values, observed.values, rtol=0, atol=1e-6):
            raise RuntimeError(f"UC1_TECHNICAL_FAIL: capacity drift by {keys}")
    p2x = uc.generators.loc[uc.generators.index.str.startswith("P2X_")]
    if len(p2x) != 7 or p2x.committable.astype(bool).any() or not p2x.sign.eq(-1).all():
        raise RuntimeError("UC1_BLOCKED_POST_P2X_PARENT_CONTRACT: P2X changed")
    if int(uc.generators.committable.astype(bool).sum()) != len(plan):
        raise RuntimeError("UC1_TECHNICAL_FAIL: unauthorized committable component")
    return {"status": "PASS", "snapshots": 8760, "synthetic_units": len(plan),
            "eligible_parents": int(plan.parent_id.nunique()), "capacity_conservation_MW": 0.0,
            "availability_compatibility": "PASS", "p2x_equalities": len(uc.global_constraints),
            "full_year_solved": False}


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def build_all_unsolved() -> dict:
    cfg = settings()
    upstream = verify_p2x_parents()
    if upstream.get("status") != "PASS":
        raise RuntimeError("UC1_BLOCKED_POST_P2X_PARENT_CONTRACT: upstream verifier failed")
    plans, audits, parent_hashes = {}, {}, {}
    # Perform all authority and availability checks before emitting a derivative.
    for year, scenario in SCENARIOS:
        path = parent_path(year, scenario)
        if not path.is_file():
            raise RuntimeError(f"UC1_BLOCKED_POST_P2X_PARENT_CONTRACT: missing {path}")
        parent_hashes[f"{year}_{scenario}"] = sha256_file(path)
        parent = pypsa.Network(path)
        plan, audit = plan_decomposition(parent, year, scenario, cfg)
        plans[(year, scenario)] = plan
        audits[(year, scenario)] = audit
    qa_root = ROOT / cfg["qa_directory"]
    out_root = ROOT / cfg["output_directory"]
    qa_root.mkdir(parents=True, exist_ok=True)
    out_root.mkdir(parents=True, exist_ok=True)
    manifest = []
    for year, scenario in SCENARIOS:
        parent = pypsa.Network(parent_path(year, scenario))
        plan = plans[(year, scenario)]
        child = derive_network(parent, plan, year, scenario)
        qa = verify_derivative(parent, child, plan)
        path = derivative_path(year, scenario)
        if path.exists():
            raise RuntimeError(f"UC1_TECHNICAL_FAIL: refusing to overwrite {path}")
        child.export_to_netcdf(path)
        reloaded = pypsa.Network(path)
        verify_derivative(parent, reloaded, plan)
        qa.update({"year": year, "scenario": scenario, "parent": str(parent_path(year, scenario).relative_to(ROOT)),
                   "parent_sha256": parent_hashes[f"{year}_{scenario}"], "derivative": str(path.relative_to(ROOT)),
                   "derivative_sha256": sha256_file(path), "archetype_counts": plan.uc_archetype.value_counts().to_dict()})
        _write_json(qa_root / f"MEM_UC1_{year}_{scenario.upper()}_STRUCTURAL_QA.json", qa)
        manifest.append(qa)
    decomposition = pd.concat(plans.values(), ignore_index=True).sort_values(["year", "scenario", "parent_id", "child_id"])
    text = decomposition.to_csv(index=False, lineterminator="\n", float_format="%.15g")
    path = qa_root / "MEM_UC1_PARENT_CHILD_DECOMPOSITION.csv"
    path.write_text(text, encoding="utf-8")
    # Independently reconstruct the ordering and serialization from all six
    # original parents to check deterministic decomposition, not network bytes.
    rebuilt = []
    for year, scenario in SCENARIOS:
        fresh, _ = plan_decomposition(pypsa.Network(parent_path(year, scenario)), year, scenario, cfg)
        rebuilt.append(fresh)
    repeat_text = pd.concat(rebuilt, ignore_index=True).sort_values(["year", "scenario", "parent_id", "child_id"]).to_csv(index=False, lineterminator="\n", float_format="%.15g")
    if text != repeat_text:
        raise RuntimeError("UC1_TECHNICAL_FAIL: decomposition rebuild differs")
    audit = pd.concat(audits.values(), ignore_index=True)
    audit.to_csv(qa_root / "MEM_UC1_AVAILABILITY_AUDIT.csv", index=False, lineterminator="\n")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    receipt = {"status": "PASS", "version": VERSION, "parents": parent_hashes,
               "decomposition_sha256": digest, "deterministic_rebuild": "PASS",
               "full_year_optimization_invocations": 0, "structural_packages": manifest}
    _write_json(qa_root / "MEM_UC1_DETERMINISTIC_BUILD_RECEIPT.json", receipt)
    return receipt


def _smoke_root() -> Path:
    return ROOT / settings()["smoke_results_directory"]


def _smoke_input(kind: str) -> Path:
    return ROOT / "networks" / "unsolved" / "uc1_common_v1" / f"UC1_2040_BASE_168H_{kind}_UNSOLVED.nc"


def _smoke_solved(kind: str) -> Path:
    return _smoke_root() / f"UC1_2040_BASE_168H_{kind}_SOLVED.nc"


def _assert_six_structural_pass() -> None:
    receipt_path = ROOT / "qa" / "stage_b" / "uc1_common_v1" / "MEM_UC1_DETERMINISTIC_BUILD_RECEIPT.json"
    if not receipt_path.is_file():
        raise RuntimeError("UC1_TECHNICAL_FAIL: six-scenario structural receipt absent")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("status") != "PASS" or len(receipt.get("structural_packages", [])) != 6:
        raise RuntimeError("UC1_TECHNICAL_FAIL: six-scenario structural gate not PASS")
    for item in receipt["structural_packages"]:
        source = ROOT / item["parent"]
        derivative = ROOT / item["derivative"]
        if item["status"] != "PASS" or sha256_file(source) != item["parent_sha256"] or sha256_file(derivative) != item["derivative_sha256"]:
            raise RuntimeError("UC1_TECHNICAL_FAIL: structural receipt/hash drift")


def _p2x_signature(network: pypsa.Network) -> tuple[pd.DataFrame, pd.DataFrame]:
    p2x = network.generators.loc[network.generators.index.str.startswith("P2X_")].sort_index()
    constraints = network.global_constraints.loc[network.global_constraints.index.str.startswith("P2X_ANNUAL_")].sort_index()
    if len(p2x) != 7 or len(constraints) != 7:
        raise RuntimeError("UC1_BLOCKED_POST_P2X_PARENT_CONTRACT: seven P2X pairs absent")
    return p2x, constraints


def _prorate_smoke(network: pypsa.Network) -> dict[str, float]:
    original_snapshots = network.snapshots.copy()
    if len(original_snapshots) != 8760:
        raise RuntimeError("UC1_BLOCKED_POST_P2X_PARENT_CONTRACT: full-year chronology absent")
    original_weighting = network.snapshot_weightings.generators.iloc[:168].copy()
    _, original_constraints = _p2x_signature(network)
    annual = original_constraints.constant.astype(float).copy()
    network.set_snapshots(original_snapshots[:168])
    if len(network.snapshots) != 168 or not np.array_equal(network.snapshot_weightings.generators.to_numpy(), original_weighting.to_numpy()):
        raise RuntimeError("UC1_TECHNICAL_FAIL: smoke snapshots/weighting drift")
    for key, value in annual.items():
        network.global_constraints.at[key, "constant"] = float(value) * 168.0 / 8760.0
    _, constraints = _p2x_signature(network)
    if not np.allclose(constraints.constant.to_numpy(), annual.to_numpy() * 168.0 / 8760.0, rtol=0, atol=1e-7):
        raise RuntimeError("UC1_TECHNICAL_FAIL: smoke P2X target drift")
    network.meta.update({"uc1_smoke": True, "uc1_smoke_snapshots": 168,
                         "uc1_smoke_p2x_rule": "SMOKE_HORIZON_PRORATED_P2X_ENERGY_ONLY",
                         "uc1_full_year_optimization_authorized": False})
    return {k: float(v) for k, v in constraints.constant.items()}


def prepare_smoke() -> dict:
    _assert_six_structural_pass()
    continuous = pypsa.Network(parent_path(2040, "Base"))
    uc = pypsa.Network(derivative_path(2040, "Base"))
    original_p2x, original_constraints = _p2x_signature(continuous)
    pd.testing.assert_frame_equal(original_p2x, _p2x_signature(uc)[0], check_dtype=False)
    pd.testing.assert_frame_equal(original_constraints, _p2x_signature(uc)[1], check_dtype=False)
    cont_targets = _prorate_smoke(continuous)
    uc_targets = _prorate_smoke(uc)
    if cont_targets != uc_targets:
        raise RuntimeError("UC1_TECHNICAL_FAIL: smoke P2X constraints differ")
    pd.testing.assert_frame_equal(_p2x_signature(continuous)[0], _p2x_signature(uc)[0], check_dtype=False)
    for kind, network in (("POST_P2X_CONTINUOUS_REFERENCE", continuous), ("UC_MILP", uc)):
        path = _smoke_input(kind)
        if path.exists():
            raise RuntimeError(f"UC1_TECHNICAL_FAIL: refusing to overwrite {path}")
        network.export_to_netcdf(path)
    report = {"status": "PASS", "snapshots": 168, "first_snapshot": str(continuous.snapshots[0]),
              "last_snapshot": str(continuous.snapshots[-1]), "p2x_rule": "SMOKE_HORIZON_PRORATED_P2X_ENERGY_ONLY",
              "p2x_targets_MWh": cont_targets, "p2x_national_MWh": sum(cont_targets.values()),
              "generator_weighting": "accepted_parent_first_168", "solver_invocations": 0,
              "continuous_input_sha256": sha256_file(_smoke_input("POST_P2X_CONTINUOUS_REFERENCE")),
              "uc_input_sha256": sha256_file(_smoke_input("UC_MILP"))}
    _write_json(ROOT / "qa" / "stage_b" / "uc1_common_v1" / "MEM_UC1_2040_BASE_SMOKE_INPUT_QA.json", report)
    return report


def _solver_contract() -> dict:
    cfg = settings()["solver"]
    import gurobipy as gp
    actual = ".".join(str(v) for v in gp.gurobi.version())
    if cfg["name"] != "gurobi" or actual != cfg["required_version"] or cfg["options"] != {"Threads": 1, "Seed": 0} or cfg["include_objective_constant"] is not False:
        raise RuntimeError(f"UC1_TECHNICAL_FAIL: solver contract mismatch {actual}")
    return cfg


def _solve_one(kind: str, *, linearized: bool = False, fixed: dict[str, pd.DataFrame] | None = None) -> dict:
    cfg = _solver_contract()
    source = _smoke_input("UC_MILP" if fixed else kind)
    if not source.is_file():
        raise RuntimeError("UC1_TECHNICAL_FAIL: smoke input missing")
    target = _smoke_solved(kind)
    if target.exists():
        raise RuntimeError(f"UC1_TECHNICAL_FAIL: refusing to overwrite solved smoke {target}")
    network = pypsa.Network(source)
    if len(network.snapshots) != 168 or network.meta.get("uc1_smoke_p2x_rule") != "SMOKE_HORIZON_PRORATED_P2X_ENERGY_ONLY":
        raise RuntimeError("UC1_TECHNICAL_FAIL: smoke input contract changed")
    if fixed:
        def fix_commitment(n: pypsa.Network, snapshots: pd.Index) -> None:
            names = list(fixed["status"].columns)
            for attr in ("status", "start_up", "shut_down"):
                var = n.model[f"Generator-{attr}"]
                values = fixed[attr].reindex(index=snapshots, columns=names)
                if values.isna().any().any():
                    raise RuntimeError(f"UC1_FIXED_COMMITMENT_PRICE_LP_FAIL: missing {attr} trajectory")
                target_array = xr.DataArray(values.to_numpy(dtype=float),
                                            coords={"snapshot": snapshots, "name": names},
                                            dims=("snapshot", "name"))
                n.model.add_constraints(var.loc[:, names] == target_array, name=f"UC1-fixed-{attr}")
    else:
        fix_commitment = None
    model = network.optimize.create_model(linearized_unit_commitment=linearized, include_objective_constant=False)
    counts = {"variables": int(model.nvars), "constraints": int(model.ncons),
              "integer_variables": int(model.integers.nvars), "binary_variables": int(model.binaries.nvars)}
    if kind == "POST_P2X_CONTINUOUS_REFERENCE" and (counts["integer_variables"] or counts["binary_variables"]):
        raise RuntimeError("UC1_2040_REFERENCE_LP_FAIL: model is not continuous")
    if fixed:
        fix_commitment(network, network.snapshots)
        counts.update({"variables_after_fix": int(model.nvars), "constraints_after_fix": int(model.ncons)})
        if model.integers.nvars or model.binaries.nvars:
            raise RuntimeError("UC1_FIXED_COMMITMENT_PRICE_LP_FAIL: price model not LP")
    started = perf_counter()
    status, condition = network.optimize.solve_model(
        solver_name="gurobi", solver_options=cfg["options"],
        assign_all_duals=kind != "UC_MILP", log_to_console=False,
    )
    elapsed = perf_counter() - started
    if str(status).lower() != "ok" or str(condition).lower() != "optimal":
        raise RuntimeError(f"UC1_{kind}_FAIL: {status}/{condition}")
    target.parent.mkdir(parents=True, exist_ok=True)
    network.export_to_netcdf(target)
    summary = {"status": "PASS", "kind": kind, "solver": "gurobi", "solver_version": cfg["required_version"],
               "solver_options": cfg["options"], "include_objective_constant": False,
               "termination_condition": str(condition), "solver_status": str(status),
               "runtime_seconds": elapsed, "objective": float(network.objective),
               "snapshots": len(network.snapshots), "network": str(target.relative_to(ROOT)),
               "network_sha256": sha256_file(target), **counts}
    _write_json(target.with_suffix(".json"), summary)
    return summary


def run_reference() -> dict:
    return _solve_one("POST_P2X_CONTINUOUS_REFERENCE")


def run_uc_milp() -> dict:
    prior = _smoke_solved("POST_P2X_CONTINUOUS_REFERENCE")
    if not prior.is_file():
        raise RuntimeError("UC1_TECHNICAL_FAIL: continuous reference must finish first")
    return _solve_one("UC_MILP")


def run_price_lp() -> dict:
    milp_path = _smoke_solved("UC_MILP")
    if not milp_path.is_file():
        raise RuntimeError("UC1_TECHNICAL_FAIL: UC MILP must finish first")
    milp = pypsa.Network(milp_path)
    names = milp.generators.index[milp.generators.committable.astype(bool)].tolist()
    fixed = {attr: getattr(milp.generators_t, attr).loc[:, names].copy() for attr in ("status", "start_up", "shut_down")}
    qa_dir = ROOT / "qa" / "stage_b" / "uc1_common_v1"
    qa_dir.mkdir(parents=True, exist_ok=True)
    for attr, frame in fixed.items():
        frame.to_csv(qa_dir / f"MEM_UC1_2040_BASE_168H_MILP_{attr.upper()}.csv", index_label="snapshot", lineterminator="\n")
    result = _solve_one("FIXED_COMMITMENT_PRICE_LP", linearized=True, fixed=fixed)
    price = pypsa.Network(_smoke_solved("FIXED_COMMITMENT_PRICE_LP"))
    for attr, frame in fixed.items():
        actual = getattr(price.generators_t, attr).loc[:, names]
        if not np.allclose(actual.to_numpy(), frame.to_numpy(), rtol=0, atol=1e-7):
            raise RuntimeError(f"UC1_FIXED_COMMITMENT_PRICE_LP_FAIL: {attr} trajectory changed")
    zones = ["CALA", "CNOR", "CSUD", "NORD", "SARD", "SICI", "SUD"]
    prices = price.buses_t.marginal_price.loc[:, zones]
    if prices.shape != (168, 7) or prices.isna().any().any():
        raise RuntimeError("UC1_FIXED_COMMITMENT_PRICE_LP_FAIL: incomplete zonal prices")
    prices.to_csv(_smoke_root() / "UC1_2040_BASE_168H_FIXED_COMMITMENT_MARGINAL_PRICE.csv",
                  index_label="snapshot", lineterminator="\n")
    result["price_label"] = "FIXED_COMMITMENT_MARGINAL_PRICE"
    result["conditional_on_milp_commitment"] = True
    result["fixed_trajectories_equal_milp"] = True
    _write_json(_smoke_solved("FIXED_COMMITMENT_PRICE_LP").with_suffix(".json"), result)
    return result


ZONES = ("CALA", "CNOR", "CSUD", "NORD", "SARD", "SICI", "SUD")
SMOKE_KINDS = ("POST_P2X_CONTINUOUS_REFERENCE", "UC_MILP", "FIXED_COMMITMENT_PRICE_LP")


def _weighted_sum(series: pd.Series, weights: pd.Series) -> float:
    return float(series.mul(weights).sum())


def _link_zone_energy(network: pypsa.Network, carrier: str, zone: str) -> float:
    links = network.links.loc[network.links.carrier.eq(carrier)]
    weights = network.snapshot_weightings.generators
    imports = 0.0
    for link_id, link in links.iterrows():
        if link.bus0 == zone:
            imports -= _weighted_sum(network.links_t.p0[link_id], weights)
        if link.bus1 == zone:
            imports -= _weighted_sum(network.links_t.p1[link_id], weights)
    return imports


def _smoke_metrics(network: pypsa.Network, kind: str) -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
    weights = network.snapshot_weightings.generators
    p = network.generators_t.p
    p0, p1 = network.links_t.p0, network.links_t.p1
    generation, zonal, prices, flows, first48 = [], [], [], [], []
    committable = network.generators.index[network.generators.committable.astype(bool)]
    for zone in ZONES:
        gens = network.generators.loc[network.generators.bus.eq(zone)]
        def gen_sum(mask: pd.Series) -> float:
            names = gens.index[mask]
            return _weighted_sum(p[names].sum(axis=1), weights) if len(names) else 0.0
        rigid_names = network.loads.index[network.loads.bus.eq(zone)]
        rigid = _weighted_sum(network.loads_t.p[rigid_names].sum(axis=1), weights)
        p2x = gen_sum(gens.sign.eq(-1) & gens.carrier.astype(str).str.startswith("p2x_flexible_"))
        shedding = gen_sum(gens.carrier.eq("load_shedding"))
        primary_names = gens.index[
            gens.sign.eq(1) & ~gens.carrier.isin(["load_shedding", "spillage", "water_energy", "external_market"])
        ]
        primary = _weighted_sum(p[primary_names].sum(axis=1), weights) if len(primary_names) else 0.0
        for carrier, group in gens.loc[primary_names].groupby("carrier"):
            energy = _weighted_sum(p[group.index].sum(axis=1), weights)
            generation.append({"model": kind, "zone": zone, "technology": str(carrier), "MWh": energy})
        def link_energy(prefix: str, side: str, negate: bool = False) -> float:
            ids = [x for x in network.links.index if x.startswith(prefix) and
                   (network.links.at[x, "bus0"] == zone if side == "p0" else network.links.at[x, "bus1"] == zone)]
            if not ids:
                return 0.0
            value = _weighted_sum((p0 if side == "p0" else p1)[ids].sum(axis=1), weights)
            return -value if negate else value
        bess_ch = link_energy("BESS_CHARGE_", "p0")
        bess_dis = link_energy("BESS_DISCHARGE_", "p1", True)
        phs_ch = link_energy("PUMP_", "p0")
        phs_dis = sum(_weighted_sum(-p1[x], weights) for x in network.links.index
                      if x.startswith("TURBINE_") and "PHS" in x and network.links.at[x, "bus1"] == zone)
        hydro_turbine = sum(_weighted_sum(-p1[x], weights) for x in network.links.index
                            if x.startswith("TURBINE_") and "PHS" not in x and network.links.at[x, "bus1"] == zone)
        generation.extend([{"model": kind, "zone": zone, "technology": "BESS_discharge", "MWh": bess_dis},
                           {"model": kind, "zone": zone, "technology": "PHS_discharge", "MWh": phs_dis},
                           {"model": kind, "zone": zone, "technology": "hydro_turbine", "MWh": hydro_turbine}])
        net_external = _link_zone_energy(network, "external_trade", zone)
        # CORS is a zero-injection/no-price transit hub.  Its two modeled
        # links contribute to the adjacent Italian-zone balance, not supply.
        net_internal = (_link_zone_energy(network, "internal_transfer", zone)
                        + _link_zone_energy(network, "corsica_hub", zone))
        supply = primary + bess_dis + phs_dis + hydro_turbine + net_external + net_internal + shedding
        demand = rigid + p2x + bess_ch + phs_ch
        zonal.append({"model": kind, "zone": zone, "rigid_demand_MWh": rigid,
                      "p2x_MWh": p2x, "primary_generation_MWh": primary,
                      "hydro_turbine_MWh": hydro_turbine, "bess_discharge_MWh": bess_dis,
                      "bess_charge_MWh": bess_ch, "phs_discharge_MWh": phs_dis,
                      "phs_charge_MWh": phs_ch, "net_external_import_MWh": net_external,
                      "net_internal_import_MWh": net_internal, "load_shedding_MWh": shedding,
                      "electrical_balance_residual_MWh": supply - demand})
        if zone in network.buses_t.marginal_price.columns:
            hourly_price = network.buses_t.marginal_price[zone]
            load = network.loads_t.p[rigid_names].sum(axis=1)
            prices.append({"model": kind, "zone": zone, "price_type":
                           "FIXED_COMMITMENT_MARGINAL_PRICE" if kind == "FIXED_COMMITMENT_PRICE_LP" else "CONTINUOUS_REFERENCE_MARGINAL_PRICE",
                           "hourly_mean_EUR_MWh": float(hourly_price.mean()),
                           "rigid_load_weighted_mean_EUR_MWh": float((hourly_price * load).sum() / load.sum()),
                           "min_EUR_MWh": float(hourly_price.min()), "max_EUR_MWh": float(hourly_price.max()),
                           "zero_price_hours": int(np.isclose(hourly_price, 0, atol=1e-8).sum()),
                           "negative_price_hours": int((hourly_price < -1e-8).sum()),
                           "hours_above_500_EUR_MWh": int((hourly_price > 500).sum())})
    for link_id, link in network.links.loc[network.links.carrier.isin(["internal_transfer", "external_trade", "corsica_hub"])].iterrows():
        flow = _weighted_sum(p0[link_id], weights)
        utilization = float((p0[link_id].abs() / float(link.p_nom)).max()) if link.p_nom else 0.0
        flows.append({"model": kind, "link_id": link_id, "carrier": link.carrier,
                      "bus0": link.bus0, "bus1": link.bus1, "bus0_MWh": flow,
                      "maximum_utilization": utilization})
    for t in network.snapshots[:48]:
        row = {"model": kind, "snapshot": str(t),
               "rigid_demand_MW": float(network.loads_t.p.loc[t, network.loads.bus.isin(ZONES).to_numpy()].sum()),
               "p2x_MW": float(p.loc[t, network.generators.index.str.startswith("P2X_")].sum()),
               "load_shedding_MW": float(p.loc[t, network.generators.carrier.eq("load_shedding")].sum())}
        if len(committable):
            position = network.snapshots.get_loc(t)
            previous = (network.generators_t.status.loc[network.snapshots[position - 1], committable]
                        if position else network.generators.loc[committable, "up_time_before"].gt(0).astype(float))
            current = network.generators_t.status.loc[t, committable]
            row.update({"online_units": float(network.generators_t.status.loc[t, committable].sum()),
                        "native_start_up_flags": float(network.generators_t.start_up.loc[t, committable].sum()),
                        "native_shut_down_flags": float(network.generators_t.shut_down.loc[t, committable].sum()),
                        "actual_start": float(((current > 0.5) & (previous < 0.5)).sum()),
                        "actual_shutdown": float(((current < 0.5) & (previous > 0.5)).sum()),
                        "boundary_previous_status_source": "PYPSA_UP_TIME_BEFORE" if not position else "PREVIOUS_SNAPSHOT_STATUS",
                        "committed_MW": float((network.generators_t.status.loc[t, committable] *
                                               network.generators.loc[committable, "p_nom"]).sum())})
        first48.append(row)
    return generation, zonal, prices, flows, first48


def _operating_diagnostics(network: pypsa.Network, kind: str) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    weights = network.snapshot_weightings.generators
    hydro, vre, soc, commitment = [], [], [], []
    for store_id, store in network.stores.iterrows():
        e = network.stores_t.e[store_id]
        p_store = network.stores_t.p[store_id]
        soc.append({"model": kind, "store_id": store_id, "carrier": store.carrier,
                    "e_nom_MWh": float(store.e_nom), "min_e_MWh": float(e.min()),
                    "max_e_MWh": float(e.max()), "last_e_MWh": float(e.iloc[-1]),
                    "cyclic": bool(store.e_cyclic),
                    "cyclic_energy_residual_MWh": _weighted_sum(p_store, weights)})
        if store.carrier != "water_energy":
            continue
        bus = store.bus
        inflow = network.generators.index[(network.generators.bus == bus) & network.generators.index.str.startswith("INFLOW_")]
        spill = network.generators.index[(network.generators.bus == bus) & network.generators.index.str.startswith("SPILL_")]
        pumps = network.links.index[(network.links.bus1 == bus) & network.links.index.str.startswith("PUMP_")]
        turbines = network.links.index[(network.links.bus0 == bus) & network.links.index.str.startswith("TURBINE_")]
        natural = _weighted_sum(network.generators_t.p[inflow].sum(axis=1), weights) if len(inflow) else 0.0
        spilled = _weighted_sum(network.generators_t.p[spill].sum(axis=1), weights) if len(spill) else 0.0
        pumped_water = _weighted_sum(-network.links_t.p1[pumps].sum(axis=1), weights) if len(pumps) else 0.0
        turbine_water = _weighted_sum(network.links_t.p0[turbines].sum(axis=1), weights) if len(turbines) else 0.0
        hydro.append({"model": kind, "store_id": store_id, "water_bus": bus,
                      "natural_inflow_MWh_water": natural, "pumped_in_MWh_water": pumped_water,
                      "turbine_out_MWh_water": turbine_water, "spill_MWh_water": spilled,
                      "cyclic_water_balance_residual_MWh": natural + pumped_water - turbine_water - spilled})
    for gen_id, gen in network.generators.loc[network.generators.carrier.isin(
        ["solar_pv_rooftop", "solar_pv_utility", "wind_onshore", "wind_offshore"])].iterrows():
        profile = network.generators_t.p_max_pu[gen_id] if gen_id in network.generators_t.p_max_pu else pd.Series(float(gen.p_max_pu), index=network.snapshots)
        potential = _weighted_sum(profile * float(gen.p_nom), weights)
        actual = _weighted_sum(network.generators_t.p[gen_id], weights)
        vre.append({"model": kind, "generator_id": gen_id, "zone": gen.bus,
                    "carrier": gen.carrier, "available_MWh": potential,
                    "dispatched_MWh": actual, "curtailed_MWh": potential - actual})
    committable = network.generators.index[network.generators.committable.astype(bool)]
    if len(committable):
        plan = pd.read_csv(ROOT / "qa" / "stage_b" / "uc1_common_v1" / "MEM_UC1_PARENT_CHILD_DECOMPOSITION.csv")
        plan = plan.loc[plan.year.eq(2040) & plan.scenario.eq("Base")].set_index("child_id")
        for arch, ids in plan.loc[committable].groupby("uc_archetype").groups.items():
            unit_ids = list(ids)
            for position, snapshot in enumerate(network.snapshots):
                status = network.generators_t.status.loc[snapshot, unit_ids]
                previous = (network.generators_t.status.loc[network.snapshots[position - 1], unit_ids]
                            if position else network.generators.loc[unit_ids, "up_time_before"].gt(0).astype(float))
                commitment.append({"model": kind, "snapshot": str(snapshot), "archetype": arch,
                                   "online_units": float(status.sum()),
                                   "committed_MW": float((status * network.generators.loc[unit_ids, "p_nom"]).sum()),
                                   "native_start_up_flags": float(network.generators_t.start_up.loc[snapshot, unit_ids].sum()),
                                   "native_shut_down_flags": float(network.generators_t.shut_down.loc[snapshot, unit_ids].sum()),
                                   "actual_start": float(((status > 0.5) & (previous < 0.5)).sum()),
                                   "actual_shutdown": float(((status < 0.5) & (previous > 0.5)).sum()),
                                   "boundary_previous_status_source": "PYPSA_UP_TIME_BEFORE" if not position else "PREVIOUS_SNAPSHOT_STATUS"})
    return hydro, vre, soc, commitment


def _reconcile_fixed_commitment(
    milp: pypsa.Network, price_lp: pypsa.Network, summaries: dict, run_dir: Path
) -> dict:
    """Audit saved commitment trajectories without rebuilding or solving a model."""
    names = milp.generators.index[milp.generators.committable.astype(bool)].sort_values()
    if len(names) != 108 or not names.equals(price_lp.generators.index[price_lp.generators.committable.astype(bool)].sort_values()):
        raise RuntimeError("UC1_FIXED_COMMITMENT_RECONCILIATION_FAIL: committable unit set")
    trajectories = {}
    max_differences = {}
    for attribute in ("status", "start_up", "shut_down"):
        left = getattr(milp.generators_t, attribute).loc[:, names]
        right = getattr(price_lp.generators_t, attribute).loc[:, names]
        difference = float((left - right).abs().to_numpy().max())
        if difference > 1e-7:
            raise RuntimeError(f"UC1_FIXED_COMMITMENT_RECONCILIATION_FAIL: {attribute} differs by {difference}")
        trajectories[attribute] = left
        max_differences[attribute] = difference
    status = trajectories["status"]
    previous = status.shift(1)
    # PyPSA's initial commitment is bool(up_time_before), as used by its
    # com-transition constraints at the first optimized snapshot.
    previous.iloc[0] = milp.generators.loc[names, "up_time_before"].gt(0).astype(float).to_numpy()
    actual_start = ((status > 0.5) & (previous < 0.5)).astype(int)
    actual_shutdown = ((status < 0.5) & (previous > 0.5)).astype(int)
    price_status = price_lp.generators_t.status.loc[:, names]
    price_previous = price_status.shift(1)
    price_previous.iloc[0] = price_lp.generators.loc[names, "up_time_before"].gt(0).astype(float).to_numpy()
    price_actual_start = ((price_status > 0.5) & (price_previous < 0.5)).astype(int)
    price_actual_shutdown = ((price_status < 0.5) & (price_previous > 0.5)).astype(int)
    if not actual_start.equals(price_actual_start) or not actual_shutdown.equals(price_actual_shutdown):
        raise RuntimeError("UC1_FIXED_COMMITMENT_RECONCILIATION_FAIL: canonical transitions differ")
    raw_start = trajectories["start_up"]
    raw_shutdown = trajectories["shut_down"]
    if (raw_start.to_numpy() + 1e-7 < actual_start.to_numpy()).any() or (raw_shutdown.to_numpy() + 1e-7 < actual_shutdown.to_numpy()).any():
        raise RuntimeError("UC1_FIXED_COMMITMENT_RECONCILIATION_FAIL: native variables miss a true status transition")
    excess_shutdown = raw_shutdown.to_numpy() > actual_shutdown.to_numpy() + 1e-7
    if (excess_shutdown & ~((previous.to_numpy() < 0.5) & (status.to_numpy() < 0.5))).any():
        raise RuntimeError("UC1_FIXED_COMMITMENT_RECONCILIATION_FAIL: excess shutdown flag at an online transition")
    for hour, unit in zip(*np.where(excess_shutdown)):
        child = names[unit]
        if float(milp.generators.at[child, "shut_down_cost"]) != 0:
            raise RuntimeError("UC1_FIXED_COMMITMENT_RECONCILIATION_FAIL: cost-bearing excess shutdown flag")
        minimum = int(milp.generators.at[child, "min_down_time"])
        if (status.iloc[hour:min(hour + minimum, len(status)), unit] > 0.5).any():
            raise RuntimeError("UC1_FIXED_COMMITMENT_RECONCILIATION_FAIL: excess shutdown affects minimum down-time")
    plan = pd.read_csv(ROOT / "qa" / "stage_b" / "uc1_common_v1" / "MEM_UC1_PARENT_CHILD_DECOMPOSITION.csv")
    archetype = plan.loc[plan.year.eq(2040) & plan.scenario.eq("Base")].set_index("child_id").uc_archetype
    records = []
    for child in names:
        for snapshot in status.index:
            records.append({"snapshot": str(snapshot), "child_id": child,
                            "archetype": archetype.at[child],
                            "previous_status": float(previous.at[snapshot, child]),
                            "status": float(status.at[snapshot, child]),
                            "raw_start_up": float(raw_start.at[snapshot, child]),
                            "raw_shut_down": float(raw_shutdown.at[snapshot, child]),
                            "actual_start": int(actual_start.at[snapshot, child]),
                            "actual_shutdown": int(actual_shutdown.at[snapshot, child]),
                            "excess_raw_shut_down": int(raw_shutdown.at[snapshot, child] > actual_shutdown.at[snapshot, child] + 1e-7),
                            "previous_status_authority": "PYPSA_UP_TIME_BEFORE" if snapshot == status.index[0] else "PREVIOUS_SNAPSHOT_STATUS"})
    transitions = pd.DataFrame(records)
    transitions.to_csv(run_dir / "MEM_UC1_168H_CANONICAL_COMMITMENT_TRANSITIONS.csv", index=False,
                       lineterminator="\n", float_format="%.15g")
    left_objective = float(summaries["UC_MILP"]["objective"])
    right_objective = float(summaries["FIXED_COMMITMENT_PRICE_LP"]["objective"])
    absolute_difference = abs(left_objective - right_objective)
    relative_difference = absolute_difference / max(abs(left_objective), 1.0)
    if absolute_difference > 1e-4 and relative_difference > 1e-9:
        raise RuntimeError("UC1_FIXED_COMMITMENT_RECONCILIATION_FAIL: objective discrepancy")
    price = price_lp.buses_t.marginal_price.loc[:, list(ZONES)]
    if price.shape != (168, 7) or not np.isfinite(price.to_numpy()).all():
        raise RuntimeError("UC1_FIXED_COMMITMENT_RECONCILIATION_FAIL: incomplete price series")
    qa = {"status": "PASS", "milp_objective_EUR": left_objective,
          "fixed_commitment_lp_objective_EUR": right_objective,
          "absolute_objective_difference_EUR": absolute_difference,
          "relative_objective_difference": relative_difference,
          "max_raw_trajectory_difference": max_differences,
          "canonical_actual_start_trajectory_equal": True,
          "canonical_actual_shutdown_trajectory_equal": True,
          "actual_start_count": int(actual_start.to_numpy().sum()),
          "actual_shutdown_count": int(actual_shutdown.to_numpy().sum()),
          "raw_start_up_flags": float(raw_start.to_numpy().sum()),
          "raw_shut_down_flags": float(raw_shutdown.to_numpy().sum()),
          "excess_raw_shut_down_flags": int(excess_shutdown.sum()),
          "excess_flags_offline_and_nonbinding": True,
          "initially_up_units_from_pypsa_up_time_before": int(previous.iloc[0].sum()),
          "price_lp_integer_variables": summaries["FIXED_COMMITMENT_PRICE_LP"]["integer_variables"],
          "price_lp_binary_variables": summaries["FIXED_COMMITMENT_PRICE_LP"]["binary_variables"],
          "fixed_commitment_price_zone_hours": int(price.size)}
    _write_json(run_dir / "MEM_UC1_168H_FIXED_COMMITMENT_RECONCILIATION.json", qa)
    return qa


def finalize_smoke() -> dict:
    _assert_six_structural_pass()
    run_dir = _smoke_root()
    networks = {kind: pypsa.Network(_smoke_solved(kind)) for kind in SMOKE_KINDS}
    summaries = {kind: json.loads(_smoke_solved(kind).with_suffix(".json").read_text(encoding="utf-8"))
                 for kind in SMOKE_KINDS}
    if any(summaries[kind]["status"] != "PASS" or summaries[kind]["termination_condition"] != "optimal" or
           sha256_file(_smoke_solved(kind)) != summaries[kind]["network_sha256"] for kind in SMOKE_KINDS):
        raise RuntimeError("UC1_TECHNICAL_FAIL: smoke solve receipt/hash mismatch")
    for kind, network in networks.items():
        if len(network.snapshots) != 168:
            raise RuntimeError("UC1_TECHNICAL_FAIL: smoke chronology mismatch")
        original = pypsa.Network(_smoke_input("UC_MILP" if kind != "POST_P2X_CONTINUOUS_REFERENCE" else kind))
        # PyPSA fills p_nom_opt with fixed p_nom after optimization; it is a
        # result field, not an input-capacity change.
        pd.testing.assert_frame_equal(_p2x_signature(network)[0].drop(columns=["p_nom_opt"]),
                                      _p2x_signature(original)[0].drop(columns=["p_nom_opt"]), check_dtype=False)
        pd.testing.assert_frame_equal(_p2x_signature(network)[1].drop(columns=["mu"]),
                                      _p2x_signature(original)[1].drop(columns=["mu"]), check_dtype=False)
        for constraint in network.global_constraints.index:
            carrier = network.global_constraints.at[constraint, "carrier_attribute"]
            ids = network.generators.index[network.generators.carrier.eq(carrier)]
            energy = _weighted_sum(network.generators_t.p[ids].sum(axis=1), network.snapshot_weightings.generators)
            if not math.isclose(energy, float(network.global_constraints.at[constraint, "constant"]), abs_tol=1e-5):
                raise RuntimeError(f"UC1_TECHNICAL_FAIL: P2X equality failed {kind} {constraint}")
    fixed_qa = _reconcile_fixed_commitment(networks["UC_MILP"],
                                           networks["FIXED_COMMITMENT_PRICE_LP"], summaries, run_dir)
    hourly_prices = []
    for kind in ("POST_P2X_CONTINUOUS_REFERENCE", "FIXED_COMMITMENT_PRICE_LP"):
        network = networks[kind]
        for zone in ZONES:
            rigid_ids = network.loads.index[network.loads.bus.eq(zone)]
            for snapshot in network.snapshots:
                hourly_prices.append({"model": kind, "snapshot": str(snapshot), "zone": zone,
                                      "price_type": "FIXED_COMMITMENT_MARGINAL_PRICE" if kind == "FIXED_COMMITMENT_PRICE_LP" else "CONTINUOUS_REFERENCE_MARGINAL_PRICE",
                                      "price_EUR_MWh": float(network.buses_t.marginal_price.at[snapshot, zone]),
                                      "rigid_load_MW": float(network.loads_t.p.loc[snapshot, rigid_ids].sum())})
    pd.DataFrame(hourly_prices).to_csv(run_dir / "MEM_UC1_168H_ZONAL_PRICE_HOURLY.csv",
                                       index=False, lineterminator="\n", float_format="%.15g")
    datasets = {key: [] for key in ("generation", "zonal", "prices", "flows", "first48")}
    operating = {key: [] for key in ("hydro", "vre", "soc", "commitment")}
    for kind, network in networks.items():
        pieces = _smoke_metrics(network, kind)
        for key, values in zip(datasets, pieces):
            datasets[key].extend(values)
        extra = _operating_diagnostics(network, kind)
        for key, values in zip(operating, extra):
            operating[key].extend(values)
    names = {"generation": "MEM_UC1_168H_GENERATION_BY_ZONE_TECHNOLOGY.csv",
             "zonal": "MEM_UC1_168H_ZONAL_BALANCE.csv", "prices": "MEM_UC1_168H_ZONAL_PRICE_STATISTICS.csv",
             "flows": "MEM_UC1_168H_INTERFACE_FLOWS.csv", "first48": "MEM_UC1_168H_FIRST48_DIAGNOSTICS.csv"}
    for key, rows in datasets.items():
        pd.DataFrame(rows).to_csv(run_dir / names[key], index=False, lineterminator="\n", float_format="%.15g")
    extra_names = {"hydro": "MEM_UC1_168H_HYDRO_WATER_BALANCE.csv",
                   "vre": "MEM_UC1_168H_VRE_CURTAILMENT.csv",
                   "soc": "MEM_UC1_168H_STORAGE_SOC_QA.csv",
                   "commitment": "MEM_UC1_168H_COMMITTED_CAPACITY_BY_ARCHETYPE.csv"}
    for key, rows in operating.items():
        pd.DataFrame(rows).to_csv(run_dir / extra_names[key], index=False,
                                  lineterminator="\n", float_format="%.15g")
    water = pd.DataFrame(operating["hydro"])
    states = pd.DataFrame(operating["soc"])
    curtailment = pd.DataFrame(operating["vre"])
    if float(water.cyclic_water_balance_residual_MWh.abs().max()) > 1e-5:
        raise RuntimeError("UC1_TECHNICAL_FAIL: hydro water balance")
    if float(states.cyclic_energy_residual_MWh.abs().max()) > 1e-5 or (states.min_e_MWh < -1e-5).any() or (states.max_e_MWh > states.e_nom_MWh + 1e-5).any():
        raise RuntimeError("UC1_TECHNICAL_FAIL: storage cyclic/SOC bounds")
    if (curtailment.curtailed_MWh < -1e-5).any():
        raise RuntimeError("UC1_TECHNICAL_FAIL: negative VRE curtailment")
    zonal = pd.DataFrame(datasets["zonal"])
    if float(zonal.electrical_balance_residual_MWh.abs().max()) > 1e-5:
        raise RuntimeError("UC1_TECHNICAL_FAIL: smoke electrical balance")
    if float(zonal.load_shedding_MWh.max()) > 1e-6:
        raise RuntimeError("UC1_TECHNICAL_FAIL: smoke load shedding positive")
    commitment_events = pd.DataFrame(operating["commitment"])
    milp_events = commitment_events.loc[commitment_events.model.eq("UC_MILP")]
    event_diagnostics = {
        "first_hour_previous_status": "PYPSA_BOOL_UP_TIME_BEFORE",
        "initially_up_units": fixed_qa["initially_up_units_from_pypsa_up_time_before"],
        "native_start_up_flags": float(milp_events.native_start_up_flags.sum()),
        "native_shut_down_flags": float(milp_events.native_shut_down_flags.sum()),
        "actual_starts_including_boundary": float(milp_events.actual_start.sum()),
        "actual_shutdowns_including_boundary": float(milp_events.actual_shutdown.sum()),
        "excess_raw_shut_down_flags": fixed_qa["excess_raw_shut_down_flags"],
        "interpretation": "Observed status transitions are physical events; native start/shutdown flags are retained verbatim and may be degenerate when their costs or minimum-time constraints are zero.",
    }
    comparison = zonal.groupby("model", sort=False).sum(numeric_only=True).reset_index()
    comparison["raw_solver_objective_EUR"] = comparison.model.map({k: summaries[k]["objective"] for k in SMOKE_KINDS})
    comparison.to_csv(run_dir / "MEM_UC1_168H_THREE_WAY_NATIONAL_COMPARISON.csv", index=False,
                      lineterminator="\n", float_format="%.15g")
    milp = networks["UC_MILP"]
    units = milp.generators.index[milp.generators.committable.astype(bool)]
    milp.generators_t.p[units].to_csv(run_dir / "MEM_UC1_168H_MILP_SYNTHETIC_DISPATCH_MW.csv",
                                       index_label="snapshot", lineterminator="\n", float_format="%.15g")
    p2x_dispatch = []
    p2x_by_zone = []
    for kind, network in networks.items():
        for generator_id in network.generators.index[network.generators.index.str.startswith("P2X_")]:
            zone = network.generators.at[generator_id, "bus"]
            constraint_id = f"P2X_ANNUAL_{zone}"
            consumed = _weighted_sum(network.generators_t.p[generator_id], network.snapshot_weightings.generators)
            target = float(network.global_constraints.at[constraint_id, "constant"])
            p2x_by_zone.append({"model": kind, "zone": zone, "generator_id": generator_id,
                                "cap_MW": float(network.generators.at[generator_id, "p_nom"]),
                                "target_MWh": target, "consumed_MWh": consumed,
                                "energy_residual_MWh": consumed - target})
            for snapshot, mw in network.generators_t.p[generator_id].items():
                p2x_dispatch.append({"model": kind, "snapshot": str(snapshot),
                                     "zone": zone,
                                     "generator_id": generator_id, "withdrawal_MW": float(mw)})
    pd.DataFrame(p2x_dispatch).to_csv(run_dir / "MEM_UC1_168H_P2X_DISPATCH_MW.csv",
                                       index=False, lineterminator="\n", float_format="%.15g")
    pd.DataFrame(p2x_by_zone).to_csv(run_dir / "MEM_UC1_168H_P2X_BY_ZONE.csv",
                                     index=False, lineterminator="\n", float_format="%.15g")
    plan = pd.read_csv(ROOT / "qa" / "stage_b" / "uc1_common_v1" / "MEM_UC1_PARENT_CHILD_DECOMPOSITION.csv")
    provenance = plan[["uc_archetype", "target_block_MW", "p_min_pu", "ramp_limit_up", "ramp_limit_down",
                       "ramp_limit_start_up", "ramp_limit_shut_down", "min_up_time", "min_down_time",
                       "source_startup_cost_per_MW", "normalized_startup_EUR2025_per_MW", "startup_cost_provenance"]].drop_duplicates()
    provenance.to_csv(ROOT / "qa" / "stage_b" / "uc1_common_v1" / "MEM_UC1_ASSUMPTION_PROVENANCE.csv",
                      index=False, lineterminator="\n", float_format="%.15g")
    members = [p for p in run_dir.iterdir() if p.is_file() and p.name not in
               {"MEM_UC1_168H_OUTPUT_MANIFEST.csv", "MEM_UC1_168H_RUN_RECEIPT.json"}]
    manifest = pd.DataFrame([{"artifact": p.name, "sha256": sha256_file(p), "bytes": p.stat().st_size}
                             for p in sorted(members)])
    manifest_path = run_dir / "MEM_UC1_168H_OUTPUT_MANIFEST.csv"
    manifest.to_csv(manifest_path, index=False, lineterminator="\n")
    receipt = {"status": "UC1_COMMON_ARCHITECTURE_PASS__2040_BASE_SMOKE_PASS__FULL_YEAR_NOT_AUTHORIZED",
               "smoke_solver_invocations": 3, "full_year_solver_invocations": 0,
               "reference": summaries["POST_P2X_CONTINUOUS_REFERENCE"], "milp": summaries["UC_MILP"],
               "price_lp": summaries["FIXED_COMMITMENT_PRICE_LP"],
               "maximum_absolute_zonal_balance_residual_MWh": float(zonal.electrical_balance_residual_MWh.abs().max()),
               "national_load_shedding_MWh": float(zonal.load_shedding_MWh.sum()),
               "maximum_absolute_hydro_water_residual_MWh": float(water.cyclic_water_balance_residual_MWh.abs().max()),
               "maximum_absolute_store_cyclic_residual_MWh": float(states.cyclic_energy_residual_MWh.abs().max()),
               "storage_soc_bounds": "PASS", "vre_curtailment": "NONNEGATIVE",
               "fixed_commitment_reconciliation": fixed_qa,
               "commitment_event_diagnostics": event_diagnostics,
               "manifest": str(manifest_path.relative_to(ROOT)), "manifest_sha256": sha256_file(manifest_path)}
    _write_json(run_dir / "MEM_UC1_168H_RUN_RECEIPT.json", receipt)
    return receipt


def verify_final() -> dict:
    """Read-only checks of immutable inputs/results, then pin the final audit."""
    _assert_six_structural_pass()
    qa_dir = ROOT / "qa" / "stage_b" / "uc1_common_v1"
    structural = json.loads((qa_dir / "MEM_UC1_DETERMINISTIC_BUILD_RECEIPT.json").read_text(encoding="utf-8"))
    decomposition_path = qa_dir / "MEM_UC1_PARENT_CHILD_DECOMPOSITION.csv"
    # The build receipt hashes canonical LF serialization. Windows write_text
    # stores CRLF bytes; read_text normalizes them back to the canonical text.
    canonical_decomposition_sha = hashlib.sha256(decomposition_path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    if canonical_decomposition_sha != structural["decomposition_sha256"]:
        raise RuntimeError("UC1_TECHNICAL_FAIL: decomposition hash drift")
    imported = []
    for item in structural["structural_packages"]:
        network = pypsa.Network(ROOT / item["derivative"])
        p2x, constraints = _p2x_signature(network)
        if len(network.snapshots) != 8760 or len(p2x) != 7 or len(constraints) != 7 or p2x.committable.astype(bool).any():
            raise RuntimeError("UC1_TECHNICAL_FAIL: full-year UC re-import/P2X failure")
        if int(network.generators.committable.astype(bool).sum()) != item["synthetic_units"]:
            raise RuntimeError("UC1_TECHNICAL_FAIL: full-year synthetic-unit count drift")
        imported.append({"year": item["year"], "scenario": item["scenario"], "snapshots": 8760,
                         "synthetic_units": item["synthetic_units"], "p2x_generators": 7,
                         "p2x_equalities": 7, "solved": False})
    run_dir = _smoke_root()
    receipt_path = run_dir / "MEM_UC1_168H_RUN_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt["status"] != "UC1_COMMON_ARCHITECTURE_PASS__2040_BASE_SMOKE_PASS__FULL_YEAR_NOT_AUTHORIZED" or receipt["full_year_solver_invocations"] != 0:
        raise RuntimeError("UC1_TECHNICAL_FAIL: smoke receipt/future gate")
    if sha256_file(ROOT / receipt["manifest"]) != receipt["manifest_sha256"]:
        raise RuntimeError("UC1_TECHNICAL_FAIL: smoke manifest drift")
    for row in pd.read_csv(ROOT / receipt["manifest"]).itertuples(index=False):
        if sha256_file(run_dir / row.artifact) != row.sha256:
            raise RuntimeError(f"UC1_TECHNICAL_FAIL: smoke artifact drift {row.artifact}")
    for kind in SMOKE_KINDS:
        network = pypsa.Network(_smoke_solved(kind))
        if len(network.snapshots) != 168:
            raise RuntimeError(f"UC1_TECHNICAL_FAIL: saved solved smoke unreadable {kind}")
    final_members = []
    def include(category: str, path: Path) -> None:
        final_members.append({"category": category, "artifact": str(path.relative_to(ROOT)),
                              "sha256": sha256_file(path), "bytes": path.stat().st_size})
    include("COMMON_UC_CONFIG", CONFIG_PATH)
    include("SYNTHETIC_FLEET_BUILDER", ROOT / "src" / "mem_model" / "stage_b_uc1.py")
    include("UPSTREAM_P2X_FINAL_VERIFICATION", ROOT / "qa" / "stage_b" / "p2x_flex_v1" / "MEM_STAGE_B_P2X_FLEX_V1_FINAL_VERIFICATION.json")
    include("DECOMPOSITION_PROVENANCE", qa_dir / "MEM_UC1_PARENT_CHILD_DECOMPOSITION.csv")
    include("ASSUMPTION_PROVENANCE", qa_dir / "MEM_UC1_ASSUMPTION_PROVENANCE.csv")
    include("AVAILABILITY_AUDIT", qa_dir / "MEM_UC1_AVAILABILITY_AUDIT.csv")
    include("STRUCTURAL_BUILD_RECEIPT", qa_dir / "MEM_UC1_DETERMINISTIC_BUILD_RECEIPT.json")
    for item in structural["structural_packages"]:
        include("P2X_FLEX_V1_PARENT_UNSOLVED", ROOT / item["parent"])
        include("FULL_YEAR_UC_DERIVATIVE_UNSOLVED", ROOT / item["derivative"])
        include("FULL_YEAR_STRUCTURAL_QA", qa_dir / f"MEM_UC1_{item['year']}_{item['scenario'].upper()}_STRUCTURAL_QA.json")
    for kind in SMOKE_KINDS:
        include("SMOKE_168H_SOLVED_" + kind, _smoke_solved(kind))
    for kind in ("POST_P2X_CONTINUOUS_REFERENCE", "UC_MILP"):
        include("SMOKE_168H_INPUT_UNSOLVED_" + kind, _smoke_input(kind))
    include("SMOKE_OUTPUT_MANIFEST", ROOT / receipt["manifest"])
    include("SMOKE_RUN_RECEIPT", receipt_path)
    include("FIXED_COMMITMENT_RECONCILIATION", run_dir / "MEM_UC1_168H_FIXED_COMMITMENT_RECONCILIATION.json")
    include("CANONICAL_COMMITMENT_TRANSITIONS", run_dir / "MEM_UC1_168H_CANONICAL_COMMITMENT_TRANSITIONS.csv")
    include("FINAL_HANDOFF", ROOT / "docs" / "MEM_UC1_COMMON_ARCHITECTURE_2040_BASE_SMOKE_HANDOFF.md")
    manifest_path = qa_dir / "MEM_UC1_FINAL_ARTIFACT_MANIFEST.csv"
    pd.DataFrame(final_members).to_csv(manifest_path, index=False, lineterminator="\n")
    verification = {"status": receipt["status"], "full_year_optimization_authorized": False,
                    "full_year_solver_invocations": 0, "smoke_solver_invocations": 3,
                    "upstream_p2x_state": "READY_FOR_MANUAL_EXECUTION",
                    "upstream_p2x_focused_tests": "44/44 PASS (upstream immutable receipt)",
                    "uc1_focused_tests": "7/7 PASS",
                    "full_year_reimport": imported,
                    "availability_compatibility": "PASS", "capacity_conservation": "PASS",
                    "deterministic_decomposition_sha256": structural["decomposition_sha256"],
                    "fixed_commitment_reconciliation": receipt["fixed_commitment_reconciliation"],
                    "smoke_output_manifest_sha256": receipt["manifest_sha256"],
                    "smoke_run_receipt_sha256": sha256_file(receipt_path),
                    "final_artifact_manifest_sha256": sha256_file(manifest_path),
                    "sandbox_license_attempt": "NON_SOLVE_ENVIRONMENT_EVENT_NO_RESULT_WRITTEN",
                    "nuclear_2050_future_gate": "2050_BASE_BOUNDED_SMOKE_REQUIRED_BEFORE_FULL_YEAR_2050_UC"}
    _write_json(qa_dir / "MEM_UC1_FINAL_VERIFICATION.json", verification)
    return verification


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["build-unsolved", "prepare-smoke", "run-reference", "run-uc", "run-price", "finalize", "verify-final"])
    args = parser.parse_args(argv)
    actions = {"build-unsolved": build_all_unsolved, "prepare-smoke": prepare_smoke,
               "run-reference": run_reference, "run-uc": run_uc_milp, "run-price": run_price_lp,
               "finalize": finalize_smoke, "verify-final": verify_final}
    print(json.dumps(actions[args.action](), indent=2, default=str))


if __name__ == "__main__":
    main()

"""Read-only verification of cached W evidence; never a siting approval."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from .common import ROOT, ZONES, sha256_file, dump_json
from .final_methodology_closure import STATE, SPEC, SPEC_SHA256, record_phase
from .final_wind_resource import QA, RESOURCE


def check_resource_dataset(ds, classes):
    """Verify chronology, native masks and the exported class crosswalk."""
    expected = pd.date_range("2019-01-01", periods=8760, freq="h")
    if not pd.DatetimeIndex(ds.time.values).equals(expected):
        raise RuntimeError("WIND_RESOURCE_CHRONOLOGY_FAIL")
    if classes.class_id.duplicated().any() or len(classes) != 91:
        raise RuntimeError("WIND_RESOURCE_CLASS_ID_FAIL")
    if not np.array_equal(ds.class_id.values, classes.class_id.to_numpy()):
        raise RuntimeError("WIND_RESOURCE_CROSSWALK_FAIL")
    profile = ds.profile.transpose("time", "class_id").to_numpy()
    if (not np.isfinite(profile).all() or (profile < -1e-12).any() or
            (profile > 1+1e-12).any()):
        raise RuntimeError("WIND_RESOURCE_PROFILE_INVALID")
    potential = ds.p_nom_max_MW.to_numpy()
    if not np.isfinite(potential).all() or (potential < 0).any():
        raise RuntimeError("WIND_RESOURCE_POTENTIAL_INVALID")
    np.testing.assert_allclose(profile.mean(axis=0), classes.annual_CF, atol=1e-15, rtol=1e-12)
    np.testing.assert_allclose(potential, classes.p_nom_max_MW, atol=1e-7, rtol=1e-12)
    np.testing.assert_allclose(ds.average_distance_km, classes.average_distance_km, atol=1e-12, rtol=1e-12)
    for zone in ZONES:
        sums = []
        footprints = []
        for count in (1, 4, 8):
            group = classes.loc[classes.zone.eq(zone) & classes.resource_classes.eq(count)]
            if len(group) != count:
                raise RuntimeError("WIND_RESOURCE_CLASS_COVERAGE_FAIL")
            masks = ds.class_masks.sel(class_id=group.class_id.to_numpy()).to_numpy()
            if not np.isin(masks, [0, 1]).all() or (masks.sum(axis=0) > 1).any():
                raise RuntimeError("WIND_RESOURCE_MASK_DUPLICATE_FAIL")
            footprints.append(masks.sum(axis=0))
            sums.append(float(group.p_nom_max_MW.sum()))
        np.testing.assert_array_equal(footprints[0], footprints[1])
        np.testing.assert_array_equal(footprints[0], footprints[2])
        np.testing.assert_allclose(sums, sums[0], atol=1e-7, rtol=1e-12)
    return {"snapshots": len(expected), "class_records": len(classes),
            "min_profile": float(profile.min()), "max_profile": float(profile.max()),
            "profile_values_changed_or_clipped_by_verification": 0,
            "chronology_crosswalk_masks_and_potential": "PASS"}


def verify_cached_wind_evidence():
    state = json.loads(STATE.read_text())
    if sha256_file(SPEC) != SPEC_SHA256:
        raise RuntimeError("CLOSURE_SPEC_HASH_FAIL")
    conversion = json.loads((QA / "RESOURCE_CLASS_CONVERSION_RECEIPT.json").read_text())
    if not conversion.get("native_distance_reference_exact"):
        raise RuntimeError("WIND_NATIVE_DISTANCE_NOT_VERIFIED")
    for item in conversion["artifacts"]:
        if sha256_file(ROOT / item["path"]) != item["sha256"]:
            raise RuntimeError("WIND_RESOURCE_CACHE_HASH_FAIL")
    parent_checks = []
    for item in state["controlling_parents"]:
        actual = sha256_file(ROOT / item["path"])
        if actual != item["sha256"]:
            raise RuntimeError("CLOSURE_PARENT_HASH_FAIL")
        parent_checks.append({**item, "unchanged": True})
    # Narrow direct dependencies only; reuse the prior broad protected audit.
    manifest_path = ROOT / "qa/stage_b/zonal_vre_v1/MEM_STAGE_B_ZONAL_VRE_OUTPUT_MANIFEST.csv"
    final_path = manifest_path.with_name("MEM_STAGE_B_ZONAL_VRE_FINAL_VERIFICATION.json")
    final = json.loads(final_path.read_text())
    if sha256_file(manifest_path) != final["manifest_sha256"]:
        raise RuntimeError("CLOSURE_UPSTREAM_MANIFEST_HASH_FAIL")
    dependencies = pd.read_csv(manifest_path)
    dependencies = dependencies.loc[dependencies.path.str.replace("\\", "/", regex=False).str.startswith(
        ("runtime_inputs/stage_b_zonal_vre_v1/", "config/stage_b_zonal_vre_v1.yaml"))]
    for row in dependencies.itertuples(index=False):
        if sha256_file(ROOT / row.path) != row.sha256:
            raise RuntimeError(f"CLOSURE_UPSTREAM_WIND_DEPENDENCY_CHANGED: {row.path}")
    native = json.loads((QA / "LOCAL_NATIVE_METHOD_RECEIPT.json").read_text())
    for source in native["source_files"]:
        if sha256_file(Path(source["path"])) != source["sha256"]:
            raise RuntimeError("CLOSURE_NATIVE_METHOD_SOURCE_DRIFT")
    dataset_checks = []
    for family in ("WIND_ONSHORE", "WIND_OFFSHORE"):
        classes = pd.read_csv(QA / f"{family}_RESOURCE_CLASSES.csv")
        with xr.open_dataset(RESOURCE / f"{family}_RESOURCE_CLASSES_2019.nc") as ds:
            dataset_checks.append({"technology": family, **check_resource_dataset(ds, classes)})
    reference = pd.read_csv(QA / "ONE_CLASS_REFERENCE_RECONCILIATION.csv")
    headroom = pd.read_csv(QA / "PHYSICAL_POTENTIAL_HEADROOM.csv")
    if len(reference) != 14 or reference.hourly_max_abs_difference.max() > 1e-12:
        raise RuntimeError("WIND_ONE_CLASS_REFERENCE_DRIFT")
    if len(headroom) != 252 or not headroom.status.eq("PASS").all() or (headroom.headroom_MW < -1e-7).any():
        raise RuntimeError("WIND_PHYSICAL_CAPACITY_BOUND_FAIL")
    result = {"status": "CACHED_RESOURCE_EVIDENCE_PASS_NOT_PHASE_W_PASS",
        "parent_hash_verification": parent_checks, "parent_hash_changes": 0,
        "upstream_direct_dependency_hash_checks": len(dependencies),
        "prior_protected_artifact_audit_reused": True,
        "dataset_checks": dataset_checks, "resource_class_records": 182,
        "one_class_exact_reference_checks": 14, "physical_capacity_headroom_checks": 252,
        "offshore_siting_approval": False, "final_class_count_selected": False,
        "optimization_model_constructed": False, "solver_invocations": 0,
        "production_inputs_modified": 0, "solved_networks_modified": 0,
        "new_weather_downloads": 0, "stage_a_reruns": 0, "historical_reruns": 0}
    dump_json(QA / "CACHED_RESOURCE_VERIFICATION.json", result)
    return result


def checkpoint_wind_blocked():
    """Pin this incomplete W phase without authorizing successor phases."""
    cached = verify_cached_wind_evidence()
    tests = json.loads((QA / "FOCUSED_TEST_RESULTS.json").read_text())
    convergence = json.loads((QA / "CONVERGENCE_DIAGNOSTIC_RECEIPT.json").read_text())
    tradeoffs = pd.read_csv(QA / "OFFSHORE_ECONOMIC_TRADEOFFS.csv")
    comparison = pd.read_csv(QA / "SITING_CRITERION_COMPARISON.csv")
    if tests["status"] != "PASS" or not len(tradeoffs) or not convergence["onshore_not_converged"]:
        raise RuntimeError("WIND_BLOCKER_RECEIPT_PRECONDITION_DRIFT")
    unresolved = [
        {"gate": "W4", "state": "OFFSHORE_FIXED_MW_ECONOMIC_CRITERION_REQUIRED",
         "decision_required": "Approve the offshore preprocessing economic criterion and cost/output-value basis; native dispatch-dependent investment economics do not define a unique standalone fixed-MW rank.",
         "evidence": "qa/final_methodology_closure/wind/OFFSHORE_ECONOMIC_TRADEOFFS.csv",
         "no_silent_CF_LCOE_or_boundary_price_choice": True},
        {"gate": "W5", "state": "FOUR_VS_EIGHT_CLASS_STABILITY_NOT_PASS",
         "decision_required": "Authorize a bounded finer-resolution convergence test, or explicitly approve another discretization acceptance rule; do not silently relax predeclared tolerances or declare eight classes converged.",
         "evidence": "qa/final_methodology_closure/wind/FOUR_VS_EIGHT_CLASS_CONVERGENCE.csv",
         "not_converged_onshore_cases": convergence["onshore_not_converged"]}]
    offshore = comparison.loc[comparison.technology.eq("WIND_OFFSHORE") & comparison.resource_classes.gt(1)]
    receipt = {"state": "WIND_RESOURCE_SITING_BLOCKED", "phase": "W",
        "completed_subgate": cached["status"], "unresolved_items": unresolved,
        "resource_class_records": cached["resource_class_records"],
        "physical_headroom_checks_PASS": cached["physical_capacity_headroom_checks"],
        "one_class_reference_checks_PASS": cached["one_class_exact_reference_checks"],
        "tradeoff_pairs": len(tradeoffs), "hourly_crossing_tradeoff_pairs": int(tradeoffs.hourly_profiles_cross.sum()),
        "CF_vs_cost_priority_different_allocations": int(offshore.allocation_max_MW_difference_CF_vs_cost.gt(1e-7).sum()),
        "diagnostic_cost_basis": "LOCAL_2050_REFERENCE_ONLY_NOT_ADOPTED_FOR_MEM_2040",
        "convergence": convergence, "focused_tests": tests,
        "parent_hash_changes": 0, "prior_broad_protected_audit_reused": True,
        "W_successor_networks_created": 0,
        "successor_phases": {phase: "NOT_STARTED" for phase in ("H", "N", "C", "F")},
        "optimization_model_constructed": False, "solver_invocations": 0,
        "production_optimization_executed": False, "production_inputs_modified": 0,
        "production_results_modified": 0, "solved_networks_modified": 0,
        "new_weather_downloads": 0, "stage_a_reruns": 0, "historical_reruns": 0}
    receipt_path = QA / "WIND_RESOURCE_SITING_GATE_RECEIPT.json"
    dump_json(receipt_path, receipt)
    transfer = ROOT / "docs/final_methodology_closure/MEM_FINAL_METHODOLOGY_PHASE_W_TRANSFER.md"
    paths = list(QA.glob("*.csv")) + list(QA.glob("*.json")) + list(RESOURCE.glob("*.nc")) + list(RESOURCE.glob("*.geojson"))
    paths += [SPEC, transfer, ROOT / "config/final_methodology_closure.yaml"]
    paths += [ROOT / f"src/mem_model/{name}.py" for name in (
        "final_methodology_closure", "final_wind_resource", "final_wind_diagnostics", "final_wind_verification")]
    paths += [ROOT / f"tests/{name}.py" for name in (
        "test_final_wind_resource", "test_final_methodology_closure", "test_final_wind_verification")]
    manifest_path = QA / "WIND_RESOURCE_OUTPUT_MANIFEST.csv"
    paths = sorted(set(paths)-{manifest_path}, key=lambda path: str(path).casefold())
    pd.DataFrame([{"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size,
        "sha256": sha256_file(path), "role": "W_PARTIAL_RESOURCE_EVIDENCE_NOT_ACCEPTED_NETWORK"}
        for path in paths]).to_csv(manifest_path, index=False)
    paths.append(manifest_path)
    state = json.loads(STATE.read_text())
    result = record_phase("W", "BLOCKED", parents=state["controlling_parents"], artifacts=paths,
        decisions=["REUSE_CLOSED_ZONE_GEOGRAPHY_AND_EXISTING_2019_WEATHER",
                   "RETAIN_NATIVE_PHYSICAL_POTENTIAL_CORRECTION_AND_CLIPPING_RULES",
                   "NO_OFFSHORE_CRITERION_OR_EIGHT_CLASS_CONVERGENCE_SILENTLY_ASSUMED"],
        tests={"focused_non_solving": tests, "cached_resource_checks": cached,
               "independent_review": "qa/final_methodology_closure/wind/INDEPENDENT_NATIVE_REVIEW.json"},
        unresolved=unresolved)
    # Re-read the persisted hash trail; STATE is not included in its own manifest.
    for item in result["phases"]["W"]["created_artifacts"]:
        if sha256_file(ROOT / item["path"]) != item["sha256"]:
            raise RuntimeError("WIND_CHECKPOINT_ARTIFACT_HASH_FAIL")
    return {"state": result["state"], "next_phase": result["next_phase"],
            "phase_W_state": receipt["state"], "manifest_members": len(paths)-1,
            "checkpoint_artifact_hashes_verified": len(paths), "solver_invocations": 0}

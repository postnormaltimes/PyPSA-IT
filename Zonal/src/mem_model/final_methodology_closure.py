"""Persistent, fail-closed state for sequential non-production methodology QA."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .common import ROOT, SCENARIOS, dump_json, sha256_file
from .stage_b_zonal_vre import read_json, validate_package_coverage

DIRECTORY = ROOT / "qa/final_methodology_closure"
STATE = DIRECTORY / "STATE.json"
SPEC = ROOT / "docs/final_methodology_closure/MEM_FINAL_PREPRODUCTION_METHODOLOGY_CLOSURE_MASTER.md"
SPEC_SHA256 = "5571714ef41bd9e385b99d44e188ccf6881a217e31d703f263774344f752e390"
PHASES = ("W", "H", "N", "C", "F")


def initialise():
    if STATE.exists():
        state = read_json(STATE)
        if state["controlling_spec_sha256"] != SPEC_SHA256:
            raise RuntimeError("CLOSURE_SPEC_DRIFT")
        return state
    if sha256_file(SPEC) != SPEC_SHA256:
        raise RuntimeError("CLOSURE_SPEC_HASH_FAIL")
    qa = ROOT / "qa/stage_b/zonal_vre_v1"
    final = read_json(qa / "MEM_STAGE_B_ZONAL_VRE_FINAL_VERIFICATION.json")
    receipt_path = qa / "MEM_STAGE_B_ZONAL_VRE_PREPARATION_RECEIPT.json"
    receipt = read_json(receipt_path)
    if final["state"] != "STAGE_B_ZONAL_VRE_CORRECTION_PASS":
        raise RuntimeError("CLOSURE_UPSTREAM_FAIL")
    if sha256_file(qa / "MEM_STAGE_B_ZONAL_VRE_OUTPUT_MANIFEST.csv") != final["manifest_sha256"]:
        raise RuntimeError("CLOSURE_UPSTREAM_MANIFEST_HASH_FAIL")
    packages = validate_package_coverage(receipt)
    parents = []
    for item in packages:
        if sha256_file(ROOT / item["output"]) != item["output_sha256"]:
            raise RuntimeError("CLOSURE_PARENT_HASH_FAIL")
        parents.append({"year": item["year"], "scenario": item["scenario"],
                        "path": item["output"], "sha256": item["output_sha256"]})
    state = {"schema_version": "MEM_FINAL_METHODOLOGY_CLOSURE_V1",
             "state": "IN_PROGRESS", "controlling_spec": str(SPEC.relative_to(ROOT)),
             "controlling_spec_sha256": SPEC_SHA256,
             "upstream_receipt": str(receipt_path.relative_to(ROOT)),
             "upstream_receipt_sha256": sha256_file(receipt_path),
             "upstream_final_verification_sha256": sha256_file(qa / "MEM_STAGE_B_ZONAL_VRE_FINAL_VERIFICATION.json"),
             "controlling_parents": parents, "next_phase": "W",
             "phases": {phase: {"status": "NOT_STARTED"} for phase in PHASES},
             "accepted_upstream_audits_reused": True,
             "production_optimization_executed": False, "production_solver_invocations": 0,
             "new_weather_downloads": 0, "stage_a_reruns": 0, "historical_reruns": 0}
    dump_json(STATE, state)
    return state


def record_phase(phase, status, *, parents, artifacts, decisions, tests, unresolved):
    state = initialise()
    if phase != state["next_phase"] or status not in {"PASS", "BLOCKED"}:
        raise RuntimeError("CLOSURE_SEQUENTIAL_PHASE_GATE")
    previous = PHASES[:PHASES.index(phase)]
    if any(state["phases"][p]["status"] != "PASS" for p in previous):
        raise RuntimeError("CLOSURE_PREDECESSOR_NOT_PASS")
    state["phases"][phase] = {"status": status, "controlling_parent_hashes": parents,
        "created_artifacts": [{"path": str(Path(p).relative_to(ROOT)), "sha256": sha256_file(p)} for p in artifacts],
        "accepted_methodological_decisions": decisions, "tests": tests, "unresolved_items": unresolved,
        "updated_UTC": datetime.now(timezone.utc).isoformat(),
        "production_optimization_executed": False, "production_solver_invocations": 0}
    state["state"] = "BLOCKED" if status == "BLOCKED" else "IN_PROGRESS"
    state["next_phase"] = phase if status == "BLOCKED" else (
        PHASES[PHASES.index(phase)+1] if phase != "F" else None)
    if phase == "F" and status == "PASS":
        state["state"] = "FINAL_CANONICAL_NETWORKS_PREPARED"
        state["production_state"] = "PRODUCTION_NOT_EXECUTED"
    dump_json(STATE, state)
    return state


def record_progress(phase, activity, tests):
    state = initialise()
    if phase != state["next_phase"]:
        raise RuntimeError("CLOSURE_SEQUENTIAL_PHASE_GATE")
    state["state"] = "IN_PROGRESS"
    state["production_state"] = "PRODUCTION_NOT_EXECUTED"
    state["phases"][phase].update({"status": "IN_PROGRESS", "activity": activity,
        "tests": tests, "updated_UTC": datetime.now(timezone.utc).isoformat(),
        "production_optimization_executed": False, "production_solver_invocations": 0})
    dump_json(STATE, state)
    return state


def accept_wind_resolution():
    """Preserve the accepted diagnostic checkpoint; supersede only its gates."""
    state = initialise()
    if state["next_phase"] != "W":
        raise RuntimeError("CLOSURE_SEQUENTIAL_PHASE_GATE")
    archive = DIRECTORY / "wind/W_CLASS_DIAGNOSTIC_CHECKPOINT.json"
    if not archive.exists():
        dump_json(archive, state["phases"]["W"])
    authority = ROOT / "docs/final_methodology_closure/PHASE_W_NATIVE_CELL_RESOLUTION.md"
    state["wind_resolution_authority"] = {"path": str(authority.relative_to(ROOT)),
        "sha256": sha256_file(authority), "accepted_by": "EXPLICIT_USER_PROJECT_DECISION"}
    state["phases"]["W"]["unresolved_items"] = []
    state["phases"]["W"]["superseded_block"] = str(archive.relative_to(ROOT))
    state["phases"]["W"]["resource_class_discretization_final_authority"] = False
    dump_json(STATE, state)
    return record_progress("W", "Accepted native-cell common site-LCOE resolution; pinned-cost robustness QA in progress",
        {"prior_accepted_focused_tests": 21, "new_native_cell_tests": "PENDING"})

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from .common import ACCEPTED_RUNTIME, NETWORKS, dump_json, ensure_output_dirs
from .network import build_network
from .stage_b_2040_runtime import FINAL_PATH, QA_DIR, verify_accepted_runtime_manifest
from .validation import validate_network


def main() -> None:
    runtime_dir = ACCEPTED_RUNTIME.resolve()
    manifest = verify_accepted_runtime_manifest(runtime_dir, expected_year=2040)
    ensure_output_dirs()
    output_dir = NETWORKS / "accepted"
    output_dir.mkdir(parents=True, exist_ok=True)
    qa_frames: list[pd.DataFrame] = []
    summaries = []
    for year, scenario in ((2040, "Slow"), (2040, "Base"), (2040, "High")):
        network, metadata = build_network(runtime_dir, year, scenario)
        qa = validate_network(network, year, scenario, fixture=False, assemble_model=False)
        qa.insert(0, "scenario", scenario)
        qa.insert(0, "year", year)
        qa_frames.append(qa)
        metadata.update(
            {
                "linopy_model_assembled": False,
                "solver_invoked": False,
                "qa_checks": len(qa),
                "qa_failures": int(qa["status"].eq("FAIL").sum()),
            }
        )
        stem = f"MEM_{year}_{scenario.upper()}_{len(network.snapshots)}h_UNSOLVED"
        network.export_to_netcdf(output_dir / f"{stem}.nc")
        dump_json(output_dir / f"{stem}.json", metadata)
        summaries.append(metadata)
    combined = pd.concat(qa_frames, ignore_index=True)
    QA_DIR.mkdir(parents=True, exist_ok=True)
    combined.to_csv(QA_DIR / "MEM_STAGE_B_2040_UNSOLVED_NETWORK_QA_v1.0.csv", index=False)
    dump_json(
        QA_DIR / "MEM_STAGE_B_2040_UNSOLVED_NETWORK_FINAL_VERIFICATION_v1.0.json",
        {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "chronology_id": "C2019_PREFERRED_NEWER",
            "runtime_manifest_sha256": manifest["manifest_sha256"],
            "annual_networks_built": len(summaries),
            "linopy_models_assembled": 0,
            "solver_invocations": 0,
            "production_optimization_executed": False,
            "status": "PASS" if combined["status"].eq("PASS").all() else "FAIL",
            "scenario_summaries": summaries,
        },
    )
    if combined["status"].eq("FAIL").any():
        raise SystemExit(1)
    final = json.loads(FINAL_PATH.read_text(encoding="utf-8"))
    final.update(
        {
            "unsolved_network_structural_qa": "PASS",
            "unsolved_networks_built": 3,
            "unsolved_network_qa_checks": len(combined),
            "unsolved_network_qa_failures": 0,
            "linopy_models_assembled": 0,
            "solver_invocations": 0,
            "production_optimization_executed": False,
        }
    )
    dump_json(FINAL_PATH, final)
    print(f"PASS: built {len(summaries)} 2040 annual networks; solver_invocations=0")


if __name__ == "__main__":
    main()

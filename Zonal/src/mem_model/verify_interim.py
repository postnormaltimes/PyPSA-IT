from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from .build_profiles import build_fixture
from .common import CONFIG, QA, ROOT, SCENARIOS, dump_json, ensure_output_dirs, load_yaml, sha256_file
from .network import build_network
from .runtime_bundle import validate_runtime_bundle
from .validation import validate_network


def run() -> pd.DataFrame:
    ensure_output_dirs()
    fixture = build_fixture(24)
    runtime_qa = validate_runtime_bundle(fixture, require_complete_year=False)
    runtime_qa.to_csv(QA / "MEM_SYNTHETIC_FIXTURE_RUNTIME_BUNDLE_QA.csv", index=False)
    if runtime_qa["status"].eq("FAIL").any():
        raise AssertionError(runtime_qa.loc[runtime_qa["status"].eq("FAIL")].to_string(index=False))
    frames = []
    scenario_summaries = []
    for year, scenario in SCENARIOS:
        network, metadata = build_network(fixture, year, scenario)
        checks = validate_network(
            network,
            year,
            scenario,
            fixture=True,
            assemble_model=(year, scenario) == (2040, "Base"),
        )
        checks.insert(0, "scenario", scenario)
        checks.insert(0, "year", year)
        frames.append(checks)
        scenario_summaries.append(
            {
                **metadata,
                "qa_checks": len(checks),
                "qa_failures": int(checks["status"].eq("FAIL").sum()),
                "network_instantiated": True,
                "solver_invoked": False,
                "input_status": "SYNTHETIC_FIXTURE_NOT_MODEL_INPUT",
            }
        )
    combined = pd.concat(frames, ignore_index=True)
    combined.to_csv(QA / "MEM_SYNTHETIC_FIXTURE_ALL_SIX_STRUCTURAL_QA.csv", index=False)
    manifest_paths = [ROOT / "pyproject.toml"]
    manifest_paths.extend(sorted((ROOT / "config").glob("*.yaml")))
    manifest_paths.extend(sorted((ROOT / "src" / "mem_model").glob("*.py")))
    manifest_paths.extend(sorted((ROOT / "tests").glob("*.py")))
    pd.DataFrame(
        [
            {
                "relative_path": str(path.relative_to(ROOT)),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "role": "INTERIM_IMPLEMENTATION_CODE_OR_CONFIGURATION",
            }
            for path in manifest_paths
        ]
    ).to_csv(QA / "MEM_INTERIM_IMPLEMENTATION_FILE_MANIFEST.csv", index=False)
    gates = load_yaml(CONFIG / "approval_gates.yaml")
    dump_json(
        QA / "MEM_INTERIM_IMPLEMENTATION_VERIFICATION.json",
        {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "implementation": "INDEPENDENT_SEVEN_ZONE_PYPSA_CODE_PATH_COMPLETE",
            "frozen_static_capacity_status": "UNCHANGED",
            "synthetic_fixture_status": "PASS" if combined["status"].eq("PASS").all() else "FAIL",
            "synthetic_fixture_runtime_input_qa_checks": len(runtime_qa),
            "synthetic_fixture_runtime_input_qa_failures": int(runtime_qa["status"].eq("FAIL").sum()),
            "fixture_networks_instantiated": len(scenario_summaries),
            "fixture_linopy_models_assembled": 1,
            "solver_invocations": 0,
            "annual_networks_built": 0,
            "accepted_hourly_files": 0,
            "chronology_gate": gates["chronology"]["status"],
            "external_price_gate": gates["external_prices"]["status"],
            "full_year_solver_status": gates["full_year_solver"]["status"],
            "scenario_summaries": scenario_summaries,
            "next_gate": "USER_APPROVAL_OF_CHRONOLOGY_EXTERNAL_PRICE_AND_RUNTIME_ASSUMPTION_BUNDLE",
        },
    )
    if combined["status"].eq("FAIL").any():
        raise AssertionError(combined.loc[combined["status"].eq("FAIL")].to_string(index=False))
    return combined


def main() -> None:
    result = run()
    print(f"PASS: {len(result)} structural checks across six fixture networks; solver_invocations=0")


if __name__ == "__main__":
    main()

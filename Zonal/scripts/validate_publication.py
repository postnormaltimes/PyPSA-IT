"""Run the prepared-input publication checks without models or solves."""
import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SUITES = ["test_publication_inputs", "test_final_v4_capacity_revision",
          "test_final_v1_reporting_variant", "test_canonical_reporting",
          "test_core_price_visualization", "test_dispatch_presentation",
          "test_uc2_diagnostics", "test_uc2_reporting_integration",
          "test_uc2_vis_comparison", "test_uc2_vis_x1",
          "test_ror_water_reporting", "test_water_values"]
EXCLUDED = {
    "test_reporting_guard_blocks_pypsa_and_linopy_without_solving":
        "Deliberately calls optimisation entry points; publication validation prohibits these calls.",
    "test_protected_v3_artifacts_and_canonical_result_inheritance":
        "Requires the private historical result store; original-result preservation is verified separately.",
    "test_runtime_inheritance_has_shared_horizon_members_and_exact_hashes":
        "Checks source-reconstruction runtime members already embedded in prepared networks; distributed members have separate hash tests.",
    "test_stage_b_map_binds_only_canonical_MEM_nodes_interfaces_and_prices":
        "Requires historical solved diagnostic reports not distributed with prepared inputs.",
    "test_stage_b_map_rejects_noncanonical_edge_and_duplicate_price":
        "Requires the same historical solved diagnostic reports; fixture-only flow/price tests remain included.",
}


class Receipt:
    def __init__(self):
        self.reports = []
        self.exclusions = []

    def pytest_collection_modifyitems(self, config, items):
        keep = []
        for item in items:
            name = item.originalname or item.name
            if name in EXCLUDED:
                self.exclusions.append({"test": item.nodeid, "reason": EXCLUDED[name],
                                        "status": "EXCLUDED", "applicability": "SOURCE_OR_HISTORICAL_WORKFLOW"})
            else:
                keep.append(item)
        removed = [item for item in items if item not in keep]
        items[:] = keep
        config.hook.pytest_deselected(items=removed)

    def pytest_runtest_logreport(self, report):
        if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
            self.reports.append({"test": report.nodeid, "status": report.outcome.upper()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import pytest
    from mem_model.stage_b_zonal_vre import no_models_or_solves
    receipt = Receipt()
    args.output.mkdir(parents=True, exist_ok=True)
    with no_models_or_solves():
        code = pytest.main([*[str(ROOT / "tests" / (suite + ".py")) for suite in SUITES],
                            "-q", "-p", "no:cacheprovider", "--basetemp", str(args.output / "test_tmp")], plugins=[receipt])
    for filename, rows in (("PUBLICATION_TEST_RESULTS.csv", receipt.reports),
                           ("PUBLICATION_EXCLUDED_TESTS.csv", receipt.exclusions)):
        with (args.output / filename).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
            writer.writeheader(); writer.writerows(rows)
    summary = {"status": "PASS" if code == 0 else "FAIL", "tests_passed": sum(r["status"] == "PASSED" for r in receipt.reports),
               "tests_skipped": sum(r["status"] == "SKIPPED" for r in receipt.reports),
               "tests_excluded": len(receipt.exclusions), "test_suites": SUITES,
               "optimization_model_constructed": False, "production_solver_invocations": 0}
    (args.output / "PUBLICATION_TEST_RECEIPT.json").write_text(json.dumps(summary, indent=2) + "\n")
    raise SystemExit(code)


if __name__ == "__main__":
    main()

"""Validate only completed UC2 R1 reporting artifacts; never load/solve models."""
import argparse
import json
from pathlib import Path

import pandas as pd

from mem_model.common import ROOT, dump_json, sha256_file
from mem_model.stage_b_uc2 import SCENARIOS, case_root


def map_delta(report, original_receipt_path, receipt):
    """Validate authorized map replacements without changing R1 or comparison pins."""
    path = report / "MEM_UC2_STAGE_B_MAP_CORRECTION_RECEIPT.json"
    if not path.is_file():
        return {}
    delta = json.loads(path.read_text(encoding="utf-8"))
    assert delta["status"] == "UC2_VIS_R1_STAGE_B_MAP_CORRECTION_PASS"
    assert delta["year"] == receipt["year"] and delta["scenario"] == receipt["scenario"]
    assert delta["base_R1_receipt_sha256"] == sha256_file(original_receipt_path)
    assert delta["solver_invocations"] == 0 and delta["figure_count"] == 3
    stems = ("MEM_ITALY_42BUS_ZONAL_PRICE", "MEM_ITALY_42BUS_INTERZONAL_FLOW", "MEM_ITALY_42BUS_UTILIZATION_CONTEXT")
    mutable = {f"VIS_X1/network/{stem}.{ext}" for stem in stems for ext in ("png", "svg", "pdf", "metadata.json")}
    mutable.update({"VIS_X1/FIGURE_METADATA.json", "VIS_X1/FIGURE_INDEX.csv"})
    added = {f"VIS_X1/data/MEM_STAGE_B_{name}.csv" for name in ("GRAPH_NODES", "GRAPH_INTERFACES", "PRICE_MARKERS")}
    added.add("VIS_X1/MEM_STAGE_B_MAP_QA.csv")
    assert {name.replace("\\", "/") for name in delta["artifact_hashes"]} == mutable | added
    for key in ("artifact_hashes", "input_hashes", "preserved_non_map_hashes"):
        for artifact, digest in delta[key].items():
            assert sha256_file(report / artifact) == digest, artifact
    for artifact, digest in delta["comparison_hashes"].items():
        assert sha256_file(Path(artifact)) == digest, artifact
    delta["code_hashes"] = {name.replace("\\", "/"): digest for name, digest in delta["code_hashes"].items()}
    assert set(delta["code_hashes"]) == {"src/mem_model/visualization/uc2_vis_x1.py",
                                         "src/mem_model/visualization/vis_x1_reference_42bus.py"}
    for artifact, digest in delta["code_hashes"].items():
        assert sha256_file(ROOT / artifact) == digest, artifact
    assert delta["selected_stress_snapshot"] == receipt["visualization"]["selected_stress_snapshot"]
    return delta


def validate(year: int) -> dict:
    cases = []
    for case_year, scenario in SCENARIOS:
        if case_year != year:
            continue
        report = case_root(year, scenario) / "REPORTING"
        path = report / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json"
        receipt = json.loads(path.read_text(encoding="utf-8"))
        assert receipt["status"] == "PASS" and receipt["year"] == year and receipt["scenario"] == scenario
        assert receipt["model_generation"] == "UC2" and receipt["physical_source"] == "UC_MILP"
        assert receipt["price_source"] == "FIXED_COMMITMENT_PRICE_LP"
        assert receipt["reporting_solver_invocations"] == 0
        assert receipt["production_networks_unchanged"] and receipt["original_reports_unchanged"]
        delta = map_delta(report, path, receipt)
        for group in ("artifact_hashes", "original_artifact_hashes"):
            for artifact, digest in receipt[group].items():
                expected = delta.get("artifact_hashes", {}).get(artifact, digest)
                assert sha256_file(report / artifact) == expected, artifact
        for artifact, digest in receipt["protected_source_hashes"].items():
            assert sha256_file(Path(artifact)) == digest, artifact
        for artifact, digest in receipt["code_hashes"].items():
            assert sha256_file(ROOT / artifact) == delta.get("code_hashes", {}).get(artifact, digest), artifact
        checks = pd.read_csv(report / "CANONICAL/statistics/uc2_diagnostics_reconciliation.csv")
        map_checks = pd.read_csv(report / "VIS_X1" / ("MEM_STAGE_B_MAP_QA.csv" if delta else "MEM_42BUS_REFERENCE_MAP_QA.csv"))
        assert checks.passed.all() and map_checks.status.eq("PASS").all()
        figures = (json.loads((report / "VIS_X1/FIGURE_METADATA.json").read_text(encoding="utf-8"))["figures"]
                   if delta else receipt["visualization"]["figures"])
        for figure in figures:
            assert figure["status"] in {"CURRENT_ACCEPTED_RESULT", "REFERENCE_ONLY"}
            assert all(Path(source).is_file() for source in figure["underlying_data"])
            if figure["family"] == "network":
                if delta and figure["status"] == "CURRENT_ACCEPTED_RESULT":
                    assert figure["spatial_scaffold"] == "MEM_STAGE_B_7ZONE_EXTERNAL_GRAPH"
                    assert figure["mem_result_resolution"] == "STAGE_B_ZONAL"
                    assert figure["reference_AC_branch_count"] == 0 and figure["Italian_node_count"] == 7
                    assert figure["external_node_count"] == 8 and figure["CORS_node_count"] == 1
                    assert figure["interface_count"] == 20
                else:
                    assert figure["spatial_scaffold"] == "PYPSA_IT_42BUS_REFERENCE_TOPOLOGY"
                    assert figure["physical_branch_results"] == "NOT_FROM_MEM"
        interfaces = pd.read_csv(report / "CANONICAL/statistics/uc2_net_interface_registry.csv")
        prices = pd.read_csv(report / "CANONICAL/statistics/uc2_price_annual_summary.csv")
        cases.append({"scenario": scenario, "receipt": str(path.relative_to(ROOT)), "receipt_sha256": sha256_file(path),
                      "figures": len(figures), "diagnostic_checks": len(checks), "map_checks": len(map_checks),
                      "original_artifacts_unchanged": len(receipt["original_artifact_hashes"]),
                      "protected_artifacts_unchanged": len(receipt["protected_source_hashes"]),
                      "registered_artifacts_valid": len(receipt["artifact_hashes"]),
                      "interfaces": interfaces.classification.value_counts().to_dict(),
                      "price_markets": len(prices), "stress_snapshot": receipt["visualization"]["selected_stress_snapshot"],
                      "map_correction_status": delta.get("status", "NOT_APPLIED")})
    comparison_dir = ROOT / "results/uc2_full_year" / str(year) / "COMPARISONS_VIS_X1"
    comparison = json.loads((comparison_dir / "MEM_UC2_COMPARISON_RECEIPT.json").read_text(encoding="utf-8"))
    assert comparison["status"] == "PASS" and comparison["model_generation"] == "UC2"
    assert set(comparison["scenarios"]) == {case["scenario"] for case in cases}
    assert comparison["reporting_solver_invocations"] == 0
    for case in cases:
        assert comparison["source_receipt_sha256"][case["scenario"]] == case["receipt_sha256"]
    manifest = pd.read_csv(comparison_dir / "MEM_UC2_COMPARISON_MANIFEST.csv")
    assert sha256_file(comparison_dir / "MEM_UC2_COMPARISON_MANIFEST.csv") == comparison["manifest_sha256"]
    for row in manifest.itertuples(index=False):
        assert sha256_file(comparison_dir / row.artifact) == row.sha256, row.artifact
    result = {"status": "UC2_VIS_AND_REPORTING_INTEGRATION_PASS", "year": year, "cases": cases,
              "solver_invocations": 0, "production_networks_modified": 0, "original_reporting_artifacts_modified": 0,
              "comparison_figures": comparison["figure_count"], "comparison_tables": comparison["table_count"],
              "comparison_registered_artifacts_valid": len(manifest),
              "comparison_receipt_sha256": sha256_file(comparison_dir / "MEM_UC2_COMPARISON_RECEIPT.json")}
    if all(case["map_correction_status"] == "UC2_VIS_R1_STAGE_B_MAP_CORRECTION_PASS" for case in cases):
        result["map_correction_status"] = "UC2_VIS_R1_STAGE_B_MAP_CORRECTION_PASS"
    dump_json(ROOT / "qa/stage_b/uc2_reporting_r1" / f"MEM_UC2_VIS_REPORTING_R1_{year}_QA.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    print(json.dumps(validate(parser.parse_args().year), indent=2))

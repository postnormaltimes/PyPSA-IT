"""Read-only publication planning for the accepted final-v4 six-case scope.

This is a source/selection plan, not an archive writer or a release command.
Original private receipts and solved networks remain local. Their identities
are projected into a small portable manifest; no network is opened or hashed.
Technical completeness does not establish analytical interpretation or
redistribution suitability.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from ..common import ROOT, sha256_file

SCHEMA = "MEM_RESULT_COLLECTION_PLAN_V1"
CASES = ((2040, "Slow"), (2040, "Base"), (2040, "High"),
         (2050, "Slow"), (2050, "Base"), (2050, "High"))
KINDS = ("POST_P2X_CONTINUOUS_REFERENCE", "UC_MILP", "FIXED_COMMITMENT_PRICE_LP")
LIGHTWEIGHT = {".csv", ".parquet", ".png", ".svg", ".pdf"}
MAINTAINED = ("config/final_methodology_execution_v3.yaml",
              "config/final_methodology_execution_v4.yaml",
              "config/stage_b_reporting.yaml", "config/marginal_regime.yaml",
              "docs/releases/FINAL_V4.md", "docs/releases/SOURCES.md",
              "docs/releases/REPRODUCIBILITY.md", "docs/releases/RESULT_INSPECTION.md")
DENIED = ("raw_price", "raw_water_constraint", "raw_constraint", "price_constraint_evidence",
          "lp_variables", "lp_terms", "electrical_population", "sparse_terms",
          "validated_roots", "economic_stack", "economic_offer_stack", "credential",
          "transcript", "console", "gurobi", "license", "licence", "solve_receipt",
          "verify_", "authority_register")


def _case_paths(year, scenario):
    """Use the execution API, including the unchanged final-v3 2040 namespace."""
    from .. import stage_b_uc2 as uc2
    with uc2.execution_variant("final_v4"):
        package = uc2._final_api("package", year, scenario)
        return dict(root=uc2.case_root(year, scenario),
                    jobs={kind: dict(uc2.job_paths(year, scenario, kind),
                                     job_id=uc2.job_id(year, scenario, kind),
                                     input_expected_sha256=package["reference_sha256" if kind == KINDS[0] else "sha256"])
                          for kind in KINDS})


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _source_id(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise ValueError("Source is outside the maintained project")
    return path.relative_to(ROOT.resolve()).as_posix()


def _inside(path, folder):
    path = Path(path).resolve()
    if not path.is_relative_to(Path(folder).resolve()):
        raise ValueError("Artifact escapes its declared output directory")
    return path


def _public(path):
    value = str(path).replace("\\", "/").lower()
    return Path(path).suffix.lower() in LIGHTWEIGHT and not any(token in value for token in DENIED)


class _Plan:
    def __init__(self):
        self.files = {}
        self.hashes = {}

    def sha(self, path):
        path = Path(path).resolve()
        if path.suffix.lower() == ".nc":
            raise ValueError("Solved/input networks are recorded by receipt hash only, never read for collection")
        if path not in self.hashes:
            self.hashes[path] = sha256_file(path)
        return self.hashes[path]

    def source(self, path, role):
        return dict(source_id=_source_id(path), sha256=self.sha(path), source_role=role,
                    original_exported=False)

    def check(self, path, expected):
        if not _digest(expected) or self.sha(path) != expected:
            raise ValueError("Artifact SHA-256 differs: " + _source_id(path))

    def select(self, path, role):
        path = Path(path).resolve()
        if not _public(path):
            return
        source_id = _source_id(path)
        row = dict(source_id=source_id, sha256=self.sha(path), source_role=role,
                   destination=source_id, bytes=path.stat().st_size)
        previous = self.files.get(source_id)
        if previous and previous["sha256"] != row["sha256"]:
            raise ValueError("Conflicting selected artifact")
        self.files.setdefault(source_id, row)

    def pins(self, hashes, base, *, role, select=False):
        if not isinstance(hashes, dict) or not hashes:
            raise ValueError("Non-empty artifact/source hash map required")
        for value, digest in hashes.items():
            path = Path(value)
            path = path if path.is_absolute() else Path(base) / path
            # Private external provenance cannot become a portable source ID.
            _source_id(path)
            self.check(path, digest)
            if select:
                self.select(_inside(path, base), role)

    def manifest(self, path, base, expected, role):
        self.check(path, expected)
        with Path(path).open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        if not rows or not {"artifact", "sha256"}.issubset(rows[0]):
            raise ValueError("Non-empty reporting manifest required")
        if len({row["artifact"] for row in rows}) != len(rows):
            raise ValueError("Duplicate reporting manifest artifact")
        for row in rows:
            item = _inside(Path(base) / row["artifact"], base)
            self.check(item, row["sha256"])
            self.select(item, row.get("role") or role)
        self.select(path, "REPORTING_SOURCE_MANIFEST")
        return rows


def _same_case(value, year, scenario, *, year_field="year", status="PASS"):
    if value.get("status") != status or value.get(year_field) != year or value.get("scenario") != scenario:
        raise ValueError("Wrong-case or non-PASS source receipt")


def _load_source(plan, path, role, sources, missing):
    path = Path(path)
    if not path.is_file():
        missing.append(_source_id(path))
        return None
    value = _read(path)
    sources.append(plan.source(path, role))
    return value


def _register(report):
    from .output_registry import validate_output_register
    return validate_output_register(report)


def _figures(report):
    from .inspection import figure_registry
    return figure_registry(report)


def _inspection(report):
    from .inspection import validate_inspection
    return validate_inspection(report)


def _case(plan, year, scenario):
    paths = _case_paths(year, scenario)
    root, jobs = Path(paths["root"]), paths["jobs"]
    report = root / "REPORTING"
    missing, invalid, sources, results = [], [], [], {}
    before = set(plan.files)
    row = dict(year=year, scenario=scenario, result_path_variant="final_v3" if year == 2040 else "final_v4",
               requested_execution_namespace="final_v4", actual_invocation_variants=[],
               inherited_configuration_branch="FINAL_METHODOLOGY_V3" if year == 2040 else None,
               case_source_id=_source_id(root), expected_final_qa_status="PASS", missing=missing,
               invalid=invalid, source_manifest=dict(schema_version="MEM_PORTABLE_RESULT_SOURCES_V1",
               sources=sources, results=results, physical_authority="UC_MILP",
               price_authority="FIXED_COMMITMENT_PRICE_LP_DUALS", pricing_LP_primal="NOT_CANONICAL"),
               output_register=None, inspection_indexes=None)
    try:
        for kind, item in jobs.items():
            solve = _load_source(plan, item["receipt"], "ORIGINAL_SOLVE_RECEIPT", sources, missing)
            verify = _load_source(plan, item["verification"], "ORIGINAL_VERIFICATION_RECEIPT", sources, missing)
            if solve is None or verify is None:
                continue
            _same_case(solve, year, scenario); _same_case(verify, year, scenario)
            allowed_variants = {"final_v3", "final_v4"} if year == 2040 else {"final_v4"}
            if (solve.get("kind") != kind or solve.get("job_id") != item["job_id"] or
                    verify.get("job_id") != item["job_id"] or
                    solve.get("final_methodology_variant") not in allowed_variants or
                    solve.get("snapshots") != 8760 or solve.get("solver_status") != "ok" or
                    solve.get("termination_condition") != "optimal" or
                    not _digest(solve.get("input_sha256")) or not _digest(solve.get("solved_sha256")) or
                    solve.get("input_sha256") != item["input_expected_sha256"] or
                    verify.get("solved_sha256") != solve["solved_sha256"] or
                    str(solve.get("input_path")).replace("\\", "/") != _source_id(item["input"]) or
                    str(solve.get("solved_path")).replace("\\", "/") != _source_id(item["solved"])):
                raise ValueError("Solve/verification identity or accepted branch differs: " + kind)
            if kind == "FIXED_COMMITMENT_PRICE_LP" and (solve.get("binary_variables") != 0 or
                    solve.get("integer_variables") != 0 or solve.get("assign_all_duals") is not True or
                    verify.get("price_label") != "FIXED_COMMITMENT_MARGINAL_PRICE"):
                raise ValueError("Price source is not a continuous fixed-commitment LP")
            results[kind] = dict(job_id=solve["job_id"], input_source_id=_source_id(item["input"]),
                                 actual_invocation_variant=solve["final_methodology_variant"],
                                 input_sha256=solve["input_sha256"], solved_source_id=_source_id(item["solved"]),
                                 solved_sha256=solve["solved_sha256"], solved_network_exported=False)
            results[kind]["_solve"] = solve
            results[kind]["_verify"] = verify
        if len(results) != len(KINDS):
            row["status"] = "WAITING_FOR_VERIFIED_CASE"
            return row
        row["actual_invocation_variants"] = sorted({r["actual_invocation_variant"] for r in results.values()})
        uc, lp = results["UC_MILP"], results["FIXED_COMMITMENT_PRICE_LP"]
        if (lp["_solve"].get("fixed_commitment_milp_sha256") != uc["solved_sha256"] or
                lp["_verify"].get("milp_solved_sha256") != uc["solved_sha256"]):
            raise ValueError("Fixed-commitment price LP is not bound to this UC physical solution")
        original = _load_source(plan, report / "MEM_UC2_REPORTING_RECEIPT.json", "ORIGINAL_REPORT_RECEIPT", sources, missing)
        r1 = _load_source(plan, report / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json", "VIS_REPORT_RECEIPT", sources, missing)
        final = _load_source(plan, root / "QA/FINAL_SCENARIO_QA.json", "FINAL_SCENARIO_QA", sources, missing)
        if original is None or r1 is None or final is None:
            row["status"] = "WAITING_FOR_CORE_REPORTING"
            return row
        _same_case(original, year, scenario); _same_case(r1, year, scenario); _same_case(final, year, scenario)
        if original.get("reporting_solver_invocations") != 0 or r1.get("reporting_solver_invocations") != 0:
            raise ValueError("Reporting source invoked optimization")
        for kind, field in zip(KINDS, ("reference_verification", "uc_verification", "price_verification")):
            if original.get(field) != results[kind]["solved_sha256"]:
                raise ValueError("Original report source lineage differs: " + kind)
        if (r1.get("physical_source") != "UC_MILP" or r1.get("price_source") != "FIXED_COMMITMENT_PRICE_LP" or
                final.get("three_jobs_verified") is not True or final.get("reporting_complete") is not True):
            raise ValueError("Reporting/final-QA authority or completeness differs")
        authority = r1.get("source_authority", {})
        if (authority.get("physical") != "UC_MILP" or authority.get("prices") != "FIXED_COMMITMENT_LP_DUALS" or
                authority.get("pricing_LP_primal") != "NOT_CANONICAL"):
            raise ValueError("Reporting conflates UC physical operation with price-LP primal quantities")
        # Pin original receipts, not their potentially private path-bearing contents.
        protected = r1.get("protected_source_hashes", {})
        for kind, item in jobs.items():
            for field in ("receipt", "verification", "input", "solved"):
                p = Path(item[field]).resolve()
                matching = [digest for value, digest in protected.items() if Path(value).resolve() == p]
                expected = (results[kind][field + "_sha256"] if field in ("input", "solved") else plan.sha(p))
                if matching != [expected]:
                    raise ValueError("Reporting protected original receipt differs: " + field)
        # A later accepted cache may pin approved figure corrections. Never use
        # today's code hash to invalidate an immutable historical Base report.
        cache_path = report / "MEM_MARGINAL_REGIME_REPORT_CACHE.json"
        cache = _load_source(plan, cache_path, "ACCEPTED_REPORT_CACHE", sources, []) if cache_path.is_file() else None
        if cache:
            _same_case(cache, year, scenario, year_field="model_year", status="ACCEPTED_REPORT_CACHE")
            plan.pins(cache["artifact_hashes"], report, role="ACCEPTED_REPORT_ARTIFACT", select=True)
        else:
            plan.pins(r1.get("artifact_hashes"), report, role="UC2_R1_REPORTING_EXTENSION", select=True)
            plan.pins(r1.get("original_artifact_hashes"), report, role="ORIGINAL_REPORT_ARTIFACT", select=True)
        canonical = original.get("canonical_reporting", {})
        _same_case(canonical, year, scenario, year_field="horizon")
        if (canonical.get("snapshots") != 8760 or canonical.get("reporting_solver_invocations") != 0 or
                canonical.get("electrical_balance_status") != "PASS"):
            raise ValueError("Canonical full-year reporting validation is incomplete")
        manifests = plan.manifest(report / "CANONICAL/MEM_CANONICAL_REPORTING_MANIFEST.csv",
                                  report / "CANONICAL", canonical.get("manifest_sha256"), "CANONICAL_ARTIFACT")
        table_count = sum(r["artifact"].startswith("statistics/") for r in manifests)
        figure_count = sum(r["artifact"].startswith("figures/") for r in manifests)
        if (canonical.get("table_count") != table_count or canonical.get("figure_file_count") != figure_count):
            raise ValueError("Canonical table/figure manifest is incomplete")
        row["canonical_statistics"] = dict(table_count=table_count, figure_file_count=figure_count)
        figures = _figures(report)
        for figure in figures:
            if "ECONOMIC" in figure["figure_id"].upper():
                continue
            for p in figure["formats"].values(): plan.select(_inside(p, report), "DECLARED_CASE_FIGURE")
            for p in figure["sources"]: plan.select(_inside(p, report), "DECLARED_FIGURE_SOURCE_TABLE")
        row["declared_figures"] = [dict(figure_id=f["figure_id"], applicability=f["applicability"]) for f in figures]
        marker = root / "INSPECTION/INSPECTION_MANIFEST.json"
        inspection = _load_source(plan, marker, "DERIVED_INSPECTION_MANIFEST", sources, missing)
        if inspection:
            if ((inspection.get("year"), inspection.get("scenario")) != (year, scenario) or
                    inspection.get("schema_version") != "MEM_RESULT_INSPECTION_V1" or not inspection.get("complete") or
                    inspection.get("reporting_receipt_sha256") != plan.sha(report / "MEM_UC2_REPORTING_RECEIPT.json")):
                raise ValueError("Stale/incomplete inspection source")
            plan.pins(inspection["artifacts"], root / "INSPECTION", role="DERIVED_INSPECTION_ARTIFACT")
            _inspection(report)
            row["inspection_indexes"] = [plan.source(root / "INSPECTION" / name, "LOCAL_INSPECTION_INDEX")
                                         for name in ("FIGURE_INDEX.csv", "TABLE_INDEX.csv", "RESULTS_SUMMARY.csv")]
            # Original indexes can refer to private QA receipts. They are retained
            # as local evidence; the public projection uses selected relative IDs.
        regime_path = report / "MEM_MARGINAL_REGIME_RECEIPT.json"
        regime = _load_source(plan, regime_path, "CANONICAL_MARGINAL_REGIME_RECEIPT", sources, missing)
        if regime:
            _same_case(regime, year, scenario, year_field="model_year", status="PASS_CANONICAL_MARGINAL_REGIME")
            if (regime.get("methodology") != "LEXICOGRAPHIC_REGIME_V1" or
                    regime.get("solver_invocations") != 0 or regime.get("network_opens") != 0):
                raise ValueError("Marginal-regime authority differs")
            plan.pins(regime["source_hashes"], ROOT, role="PRIVATE_VALIDATED_ROOT_PROVENANCE")
            plan.pins(regime["artifact_hashes"], report, role="CANONICAL_MARGINAL_REGIME", select=True)
        output_path = report / "OUTPUT_REGISTER.json"
        output = _load_source(plan, output_path, "CURRENT_OUTPUT_REGISTER", sources, missing)
        if output:
            _register(report)
            if (output.get("schema_version") != "MEM_CURRENT_OUTPUT_REGISTER_V1" or
                    (output.get("year"), output.get("scenario")) != (year, scenario) or
                    output.get("solver_invocations") != 0 or output.get("network_opens") != 0 or
                    output.get("economic_routine_activation") is not False or
                    output.get("core_reporting_status") != "CORE_REPORTING_PASS"):
                raise ValueError("Current output register schema/core status differs")
            row["output_register"] = {k: output.get(k) for k in ("status", "core_reporting_status",
                                      "accepted_visualizations_status", "comparison_status")}
            row["output_register"]["rows"] = [{k: r.get(k) for k in ("output_id", "required", "status", "remaining_action")}
                                               for r in output.get("rows", [])]
            for record in output.get("rows", []):
                if record.get("required") and record.get("status") not in ("GENERATED", "NOT_APPLICABLE"):
                    missing.append("required_output:" + str(record.get("output_id")))
                if record.get("status") == "GENERATED" and record.get("output_type") != "OPTIONAL_REVIEW_FIGURE":
                    for name in record.get("files", []):
                        p = _inside(report / name, root.parent)
                        plan.check(p, output.get("evidence_hashes", {}).get(name))
                        plan.select(p, "REGISTERED_" + str(record.get("output_type")))
            if output.get("accepted_visualizations_status") != "ACCEPTED_VISUALIZATIONS_PASS":
                missing.append("accepted_visualizations_complete")
            if output.get("comparison_status") != "COMPARISON_VISUALIZATIONS_PASS":
                missing.append("accepted_selected_comparisons_complete")
        row["core_reporting_status"] = "CORE_REPORTING_VERIFIED"
        row["status"] = ("LEGACY_CORE_REPORTING_VERIFIED" if output is None and scenario == "Base"
                         else "WAITING_FOR_REQUIRED_OUTPUTS" if missing else "VERIFIED_COMPLETE")
    except (KeyError, ValueError, TypeError, OSError) as exc:
        invalid.append(str(exc)); row["status"] = "INVALID_SOURCE_EVIDENCE"
        for key in set(plan.files) - before: plan.files.pop(key)
    finally:
        for value in results.values():
            value.pop("_solve", None); value.pop("_verify", None)
    return row


def _comparisons(plan, year):
    root = Path(_case_paths(year, "Slow")["root"]).parent / "COMPARISONS_VIS_X1"
    sources, missing, invalid = [], [], []
    before = set(plan.files)
    row = dict(year=year, expected_scenarios=["Slow", "Base", "High"], missing=missing, invalid=invalid,
               sources=sources, economic_stack_prototype="EXCLUDED_EVIDENCE_GATED_OPTIONAL_REVIEW")
    try:
        ordinary = _load_source(plan, root / "MEM_UC2_COMPARISON_RECEIPT.json", "ORDINARY_COMPARISON_RECEIPT", sources, missing)
        regime = _load_source(plan, root / "marginal_regime/MEM_MARGINAL_REGIME_COMPARISON_RECEIPT.json",
                              "MARGINAL_REGIME_COMPARISON_RECEIPT", sources, missing)
        if ordinary:
            expected_version = "FINAL_METHODOLOGY_V3" if year == 2040 else "FINAL_METHODOLOGY_V4"
            if (ordinary.get("status") != "PASS" or ordinary.get("year") != year or
                    ordinary.get("scenarios") != row["expected_scenarios"] or
                    ordinary.get("collection_status") != "COMPLETE" or ordinary.get("missing_scenarios") != [] or
                    ordinary.get("model_version") != expected_version or
                    ordinary.get("physical_source") != "UC_MILP" or
                    ordinary.get("price_source") != "FIXED_COMMITMENT_PRICE_LP" or
                    ordinary.get("reporting_solver_invocations") != 0):
                raise ValueError("Ordinary comparisons are partial or incompatible")
            expected = {s: plan.sha(Path(_case_paths(year, s)["root"]) / "REPORTING/MEM_UC2_VIS_REPORTING_R1_RECEIPT.json")
                        for s in row["expected_scenarios"]}
            if ordinary.get("source_receipt_sha256") != expected:
                raise ValueError("Ordinary comparison case lineage differs")
            plan.manifest(root / "MEM_UC2_COMPARISON_MANIFEST.csv", root,
                          ordinary.get("manifest_sha256"), "CANONICAL_COMPARISON_ARTIFACT")
        if regime:
            if (regime.get("status") != "PASS_CANONICAL_MARGINAL_REGIME_COMPARISON" or
                    regime.get("model_year") != year or regime.get("scenarios") != row["expected_scenarios"] or
                    regime.get("solver_invocations") != 0 or regime.get("network_opens") != 0):
                raise ValueError("Regime comparison is incomplete or incompatible")
            expected_paths = [Path(_case_paths(year, s)["root"]) / "REPORTING/MEM_MARGINAL_REGIME_RECEIPT.json"
                              for s in row["expected_scenarios"]]
            expected = {p.resolve(): plan.sha(p) for p in expected_paths}
            observed = {Path(p).resolve(): digest for p, digest in regime.get("source_hashes", {}).items()}
            if observed != expected:
                raise ValueError("Regime comparison case lineage differs")
            plan.pins(regime["artifact_hashes"], root / "marginal_regime", role="CANONICAL_REGIME_COMPARISON", select=True)
        row["status"] = "WAITING_FOR_ALL_ACCEPTED_COMPARISONS" if missing else "VERIFIED_COMPLETE"
    except (KeyError, ValueError, TypeError, OSError) as exc:
        invalid.append(str(exc)); row["status"] = "INVALID_SOURCE_EVIDENCE"
        for key in set(plan.files) - before: plan.files.pop(key)
    return row


def plan_result_collection():
    """Return a deterministic, JSON-compatible plan without writing any files.

    Missing sources remain explicit. Original receipt hashes are retained only
    in projected provenance; NetCDF, native sparse sidecars, logs, credentials,
    root-input evidence and the optional economic prototype are never selected.
    """
    plan = _Plan()
    cases = [_case(plan, year, scenario) for year, scenario in CASES]
    comparisons = [_comparisons(plan, year) for year in (2040, 2050)]
    maintained_missing = []
    for name in MAINTAINED:
        p = ROOT / name
        if not p.is_file():
            maintained_missing.append(name)
            continue
        plan.files[name] = dict(source_id=name, sha256=plan.sha(p), source_role="MAINTAINED_CONFIG_OR_PROVENANCE",
                                destination=name, bytes=p.stat().st_size)
    ready = (all(r["status"] == "VERIFIED_COMPLETE" for r in cases + comparisons) and not maintained_missing)
    return dict(schema_version=SCHEMA, status="READY_FOR_PUBLICATION_REVIEW" if ready else "WAITING_FOR_COMPLETE_CANONICAL_COLLECTION",
                publication_ready=ready, publication_authorized=False, filesystem_mutated=False,
                expected_cases=[dict(year=y, scenario=s) for y, s in CASES], cases=cases, comparisons=comparisons,
                maintained_missing=maintained_missing, selected_files=[plan.files[k] for k in sorted(plan.files)],
                exclusions=["SOLVED_NETCDF", "RAW_CONSTRAINT_AND_NATIVE_VARIABLE_SIDECARS", "PRIVATE_RECEIPTS",
                            "PRIVATE_LOGS_TRANSCRIPTS_CREDENTIALS", "VALIDATED_ROOT_INPUTS", "ECONOMIC_STACK_PROTOTYPE"],
                portable_manifest_policy="Project relative source IDs and pinned result/receipt hashes; original private receipts are not exported.",
                solved_hash_validation="Existing same-case solve/verification/report receipt anchors only; actual NetCDF bytes are not rehashed by this planner.",
                inspection_policy="Existing index hashes retained locally; portable index projection references selected relative source IDs only.")

"""Small source-manifest fixtures only; no networks, models, solves or exports."""
import csv
import hashlib
import json

import pytest

from mem_model.reporting import result_collection as collection


def _put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if isinstance(value, dict) else value, encoding="utf-8")
    return path


def _sha(path):
    assert path.suffix != ".nc", "Collection planner must not read or hash networks"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    return path


@pytest.fixture(autouse=True)
def no_network_or_model_calls(monkeypatch):
    import pypsa
    import xarray as xr
    from linopy import Model
    from pypsa.optimization.optimize import OptimizationAccessor

    def forbidden(*args, **kwargs):
        pytest.fail("Collection planning cannot load networks, construct models or solve")

    monkeypatch.setattr(pypsa.Network, "__init__", forbidden)
    monkeypatch.setattr(Model, "__init__", forbidden)
    monkeypatch.setattr(xr, "open_dataset", forbidden)
    for method in ("__call__", "create_model", "solve_model"):
        monkeypatch.setattr(OptimizationAccessor, method, forbidden)
    monkeypatch.setattr(collection, "sha256_file", _sha)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    # Keep fixed reporting suffixes within the Windows path limit; only this
    # synthetic root is shortened, never the real execution namespaces.
    tmp_path = tmp_path.parent / hashlib.sha256(tmp_path.name.encode()).hexdigest()[:6]
    tmp_path.mkdir()
    monkeypatch.setattr(collection, "ROOT", tmp_path)
    maintained = ("config/final_methodology_execution_v4.yaml", "docs/releases/SOURCES.md")
    monkeypatch.setattr(collection, "MAINTAINED", maintained)
    for name in maintained: _put(tmp_path / name, "SYNTHETIC_FIXTURE_NOT_PROJECT_EVIDENCE\n")
    paths = {}
    for year, scenario in collection.CASES:
        variant = "final_v3" if year == 2040 else "final_v4"
        root = tmp_path / f"r/{variant[-2:]}/{year}/{scenario}"
        jobs, protected, results = {}, {}, {}
        for number, kind in enumerate(collection.KINDS, 1):
            job_id = f"{variant.upper()}_UC2_{year}_{scenario.upper()}_8760H_{kind}"
            directory = root / str(number)
            item = dict(input=tmp_path / f"networks/{variant}/{year}_{scenario}_{kind}.nc",
                        solved=directory / "solved.nc", receipt=directory / "solve.json",
                        verification=root / "QA" / f"VERIFY_{number}.json", job_id=job_id, input_expected_sha256="a" * 64)
            digest = hashlib.sha256(f"{year}{scenario}{kind}".encode()).hexdigest()
            results[kind] = digest
            solve = dict(status="PASS", year=year, scenario=scenario, kind=kind, job_id=job_id,
                         final_methodology_variant=variant, snapshots=8760, solver_status="ok",
                         termination_condition="optimal", input_sha256="a" * 64, solved_sha256=digest,
                         input_path=item["input"].relative_to(tmp_path).as_posix(),
                         solved_path=item["solved"].relative_to(tmp_path).as_posix(),
                         binary_variables=0 if kind != "UC_MILP" else 1, integer_variables=0)
            verify = dict(status="PASS", year=year, scenario=scenario, job_id=job_id, solved_sha256=digest)
            if kind == "FIXED_COMMITMENT_PRICE_LP":
                solve["fixed_commitment_milp_sha256"] = results["UC_MILP"]
                solve["assign_all_duals"] = True
                verify["milp_solved_sha256"] = results["UC_MILP"]
                verify["price_label"] = "FIXED_COMMITMENT_MARGINAL_PRICE"
            for p, value in ((item["receipt"], solve), (item["verification"], verify)):
                _put(p, value); protected[str(p)] = _sha(p)
            protected[str(item["input"])] = solve["input_sha256"]
            protected[str(item["solved"])] = solve["solved_sha256"]
            jobs[kind] = item
        paths[(year, scenario)] = dict(root=root, jobs=jobs)
        report = root / "REPORTING"
        table = _put(report / "CANONICAL/statistics/generation.csv", "value\n1\n")
        figure = _put(report / "CANONICAL/figures/generation.png", "tiny synthetic figure fixture\n")
        manifest = _csv(report / "CANONICAL/MEM_CANONICAL_REPORTING_MANIFEST.csv", [
            dict(artifact="statistics/generation.csv", sha256=_sha(table), role="CANONICAL_CHART_SOURCE_TABLE"),
            dict(artifact="figures/generation.png", sha256=_sha(figure), role="CANONICAL_FIGURE")])
        canonical = dict(status="PASS", horizon=year, scenario=scenario, table_count=1, figure_file_count=1,
                         snapshots=8760, reporting_solver_invocations=0, electrical_balance_status="PASS",
                         manifest_sha256=_sha(manifest))
        original = dict(status="PASS", year=year, scenario=scenario, reporting_solver_invocations=0,
                        reference_verification=results[collection.KINDS[0]], uc_verification=results["UC_MILP"],
                        price_verification=results["FIXED_COMMITMENT_PRICE_LP"], canonical_reporting=canonical)
        original_path = _put(report / "MEM_UC2_REPORTING_RECEIPT.json", original)
        r1 = dict(status="PASS", year=year, scenario=scenario, reporting_solver_invocations=0,
                  physical_source="UC_MILP", price_source="FIXED_COMMITMENT_PRICE_LP",
                  source_authority=dict(physical="UC_MILP", prices="FIXED_COMMITMENT_LP_DUALS", pricing_LP_primal="NOT_CANONICAL"),
                  protected_source_hashes=protected, artifact_hashes={"CANONICAL/figures/generation.png": _sha(figure)},
                  original_artifact_hashes={"CANONICAL/statistics/generation.csv": _sha(table)})
        _put(report / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json", r1)
        _put(root / "QA/FINAL_SCENARIO_QA.json", dict(status="PASS", year=year, scenario=scenario,
                                                    three_jobs_verified=True, reporting_complete=True))
        artifacts = {}
        for name in ("FIGURE_INDEX.csv", "TABLE_INDEX.csv", "RESULTS_SUMMARY.csv"):
            p = _put(root / "INSPECTION" / name, "synthetic_index\n1\n"); artifacts[name] = _sha(p)
        _put(root / "INSPECTION/INSPECTION_MANIFEST.json", dict(schema_version="MEM_RESULT_INSPECTION_V1",
             year=year, scenario=scenario, complete=True, reporting_receipt_sha256=_sha(original_path), artifacts=artifacts))
        roots = _put(report / "CANONICAL/marginal_regime/validated_roots/ROOT_QA.json", dict(status="SYNTHETIC"))
        regime_table = _put(report / "CANONICAL/marginal_regime/data/shares.csv", "share\n100\n")
        _put(report / "MEM_MARGINAL_REGIME_RECEIPT.json", dict(status="PASS_CANONICAL_MARGINAL_REGIME",
             model_year=year, scenario=scenario, methodology="LEXICOGRAPHIC_REGIME_V1", solver_invocations=0,
             network_opens=0, source_hashes={str(roots): _sha(roots)}, artifact_hashes={str(regime_table): _sha(regime_table)}))
        selected = root.parent / "COMPARISONS_VIS_X1/presentation/selected.png"
        _put(selected, "selected comparison fixture\n")
        relative = "../../COMPARISONS_VIS_X1/presentation/selected.png"
        output = dict(schema_version="MEM_CURRENT_OUTPUT_REGISTER_V1", year=year, scenario=scenario,
                      status="ACCEPTED_VISUALIZATIONS_PASS", core_reporting_status="CORE_REPORTING_PASS",
                      accepted_visualizations_status="ACCEPTED_VISUALIZATIONS_PASS",
                      comparison_status="COMPARISON_VISUALIZATIONS_PASS", solver_invocations=0, network_opens=0,
                      economic_routine_activation=False, evidence_hashes={relative: _sha(selected)}, rows=[
            dict(output_id="accepted_comparison", output_type="COMPARISON_FIGURE", required=True,
                 status="GENERATED", files=[relative], remaining_action="None"),
            dict(output_id="economic_equilibrium_stack", output_type="OPTIONAL_REVIEW_FIGURE", required=False,
                 status="EVIDENCE_GATED", files=[], remaining_action="Complete supported evidence")])
        _put(report / "OUTPUT_REGISTER.json", output)
    for year in (2040, 2050):
        root = paths[(year, "Slow")]["root"].parent / "COMPARISONS_VIS_X1"
        table = _put(root / "data/comparison_metrics.csv", "value\n1\n")
        figure = _put(root / "figures/comparison.png", "tiny comparison fixture\n")
        manifest = _csv(root / "MEM_UC2_COMPARISON_MANIFEST.csv", [
            dict(artifact="data/comparison_metrics.csv", sha256=_sha(table)),
            dict(artifact="figures/comparison.png", sha256=_sha(figure))])
        hashes = {s: _sha(paths[(year, s)]["root"] / "REPORTING/MEM_UC2_VIS_REPORTING_R1_RECEIPT.json")
                  for s in ("Slow", "Base", "High")}
        _put(root / "MEM_UC2_COMPARISON_RECEIPT.json", dict(status="PASS", year=year,
             scenarios=["Slow", "Base", "High"], collection_status="COMPLETE", missing_scenarios=[],
             model_version="FINAL_METHODOLOGY_V3" if year == 2040 else "FINAL_METHODOLOGY_V4",
             physical_source="UC_MILP", price_source="FIXED_COMMITMENT_PRICE_LP", reporting_solver_invocations=0,
             source_receipt_sha256=hashes, manifest_sha256=_sha(manifest)))
        regime_table = _put(root / f"marginal_regime/LEXICOGRAPHIC_REGIME_NATIONAL_SHARES_{year}.csv", "share\n100\n")
        sources = {str(paths[(year, s)]["root"] / "REPORTING/MEM_MARGINAL_REGIME_RECEIPT.json"):
                   _sha(paths[(year, s)]["root"] / "REPORTING/MEM_MARGINAL_REGIME_RECEIPT.json")
                   for s in ("Slow", "Base", "High")}
        _put(root / "marginal_regime/MEM_MARGINAL_REGIME_COMPARISON_RECEIPT.json", dict(
            status="PASS_CANONICAL_MARGINAL_REGIME_COMPARISON", model_year=year, scenarios=["Slow", "Base", "High"],
            solver_invocations=0, network_opens=0, source_hashes=sources, artifact_hashes={str(regime_table): _sha(regime_table)}))
    monkeypatch.setattr(collection, "_case_paths", lambda year, scenario: paths[(year, scenario)])
    monkeypatch.setattr(collection, "_register", lambda report: json.loads((report / "OUTPUT_REGISTER.json").read_text()))
    monkeypatch.setattr(collection, "_inspection", lambda report: dict(status="PASS"))
    monkeypatch.setattr(collection, "_figures", lambda report: [dict(figure_id="CANONICAL/generation", applicability="APPLICABLE",
        formats={"png": report / "CANONICAL/figures/generation.png"}, sources=[report / "CANONICAL/statistics/generation.csv"])])
    return tmp_path, paths


def _rewrite(path, **updates):
    value = json.loads(path.read_text()); value.update(updates); _put(path, value)


def test_complete_plan_is_read_only_json_compatible_and_excludes_optional_economics(workspace):
    root, _ = workspace
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in root.rglob("*") if p.is_file()}
    plan = collection.plan_result_collection()
    assert plan["publication_ready"] is True
    assert plan["publication_authorized"] is False
    assert plan["filesystem_mutated"] is False
    assert len(plan["cases"]) == 6
    assert [c["result_path_variant"] for c in plan["cases"]] == ["final_v3"] * 3 + ["final_v4"] * 3
    assert [c["actual_invocation_variants"] for c in plan["cases"]] == [["final_v3"]] * 3 + [["final_v4"]] * 3
    assert json.loads(json.dumps(plan)) == plan
    assert before == {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in root.rglob("*") if p.is_file()}
    selected = plan["selected_files"]
    assert any("presentation/selected.png" in r["destination"] for r in selected)
    assert all({"sha256", "source_role", "destination", "source_id"}.issubset(r) for r in selected)
    assert all(not r["destination"].endswith((".nc", ".json")) for r in selected)
    assert all(not r["destination"].startswith(str(root)) for r in selected)


def test_absent_case_is_explicit_waiting_not_zero(workspace):
    _, paths = workspace
    p = paths[(2050, "High")]["jobs"]["UC_MILP"]["receipt"]
    p.unlink()
    plan = collection.plan_result_collection()
    row = next(c for c in plan["cases"] if (c["year"], c["scenario"]) == (2050, "High"))
    assert row["status"] == "WAITING_FOR_VERIFIED_CASE"
    assert "canonical_statistics" not in row
    assert row["missing"] and plan["publication_ready"] is False


def test_legacy_base_is_verified_core_but_not_complete_collection(workspace):
    _, paths = workspace
    (paths[(2040, "Base")]["root"] / "REPORTING/OUTPUT_REGISTER.json").unlink()
    row = collection._case(collection._Plan(), 2040, "Base")
    assert row["status"] == "LEGACY_CORE_REPORTING_VERIFIED"
    assert row["core_reporting_status"] == "CORE_REPORTING_VERIFIED"
    assert row["output_register"] is None and row["missing"]


@pytest.mark.parametrize("field,value", [("final_methodology_variant", "final_v3"), ("year", 2040),
                                       ("job_id", "FINAL_V3_BENCHMARK"), ("solved_path", "results/benchmark.nc")])
def test_2050_benchmark_or_wrong_case_never_becomes_canonical(workspace, field, value):
    _, paths = workspace
    _rewrite(paths[(2050, "Base")]["jobs"]["UC_MILP"]["receipt"], **{field: value})
    row = collection._case(collection._Plan(), 2050, "Base")
    assert row["status"] == "INVALID_SOURCE_EVIDENCE"


def test_fixed_lp_parent_must_equal_canonical_uc(workspace):
    _, paths = workspace
    _rewrite(paths[(2050, "Base")]["jobs"]["FIXED_COMMITMENT_PRICE_LP"]["receipt"], fixed_commitment_milp_sha256="f" * 64)
    assert any("bound to this UC" in error for error in collection._case(collection._Plan(), 2050, "Base")["invalid"])


def test_original_receipt_byte_change_invalidates_reporting_pin(workspace):
    _, paths = workspace
    path = paths[(2040, "Slow")]["jobs"]["UC_MILP"]["verification"]
    _rewrite(path, new_annotation="still PASS but original receipt changed")
    assert any("protected original receipt" in error for error in collection._case(collection._Plan(), 2040, "Slow")["invalid"])


def test_hash_mismatch_removes_invalid_case_from_selection(workspace):
    _, paths = workspace
    report = paths[(2040, "Slow")]["root"] / "REPORTING"
    _put(report / "CANONICAL/statistics/generation.csv", "value\n2\n")
    plan = collection._Plan()
    assert collection._case(plan, 2040, "Slow")["status"] == "INVALID_SOURCE_EVIDENCE"
    assert not plan.files


def test_path_escape_in_manifest_is_rejected(workspace):
    _, paths = workspace
    report = paths[(2040, "Slow")]["root"] / "REPORTING"
    private = _put(report / "private.csv", "private\n")
    manifest = _csv(report / "CANONICAL/MEM_CANONICAL_REPORTING_MANIFEST.csv", [
        dict(artifact="../private.csv", sha256=_sha(private))])
    original_path = report / "MEM_UC2_REPORTING_RECEIPT.json"
    original = json.loads(original_path.read_text())
    original["canonical_reporting"]["manifest_sha256"] = _sha(manifest); _put(original_path, original)
    assert any("escapes" in error for error in collection._case(collection._Plan(), 2040, "Slow")["invalid"])


def test_private_sidecars_logs_and_authority_register_not_selected(workspace):
    _, paths = workspace
    report = paths[(2040, "Slow")]["root"] / "REPORTING"
    receipt = report / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json"
    q = json.loads(receipt.read_text())
    for name in ("RAW_PRICE_VARIABLES.parquet", "raw_constraint_terms.parquet", "console_transcript.csv",
                 "uc2_VIS_RESULT_AUTHORITY_REGISTER.csv", "economic_stack_review.png"):
        p = _put(report / name, "private fixture\n"); q["artifact_hashes"][name] = _sha(p)
    _put(receipt, q)
    plan = collection._Plan()
    collection._case(plan, 2040, "Slow")
    selected = list(plan.files.values())
    assert not any(any(token in r["source_id"].lower() for token in collection.DENIED) for r in selected)


def test_partial_ordinary_comparison_or_missing_regime_blocks_publication(workspace):
    _, paths = workspace
    root = paths[(2050, "Slow")]["root"].parent / "COMPARISONS_VIS_X1"
    _rewrite(root / "MEM_UC2_COMPARISON_RECEIPT.json", collection_status="PARTIAL", scenarios=["Base", "High"], missing_scenarios=["Slow"])
    (root / "marginal_regime/MEM_MARGINAL_REGIME_COMPARISON_RECEIPT.json").unlink()
    row = collection._comparisons(collection._Plan(), 2050)
    assert row["missing"]
    assert row["status"] == "INVALID_SOURCE_EVIDENCE"


def test_required_waiting_registry_output_keeps_collection_waiting(workspace):
    _, paths = workspace
    report = paths[(2050, "Slow")]["root"] / "REPORTING"
    q = json.loads((report / "OUTPUT_REGISTER.json").read_text())
    q["rows"][0]["status"] = "WAITING_FOR_SELECTED_COMPARISON_RENDERING"
    _put(report / "OUTPUT_REGISTER.json", q)
    row = collection._case(collection._Plan(), 2050, "Slow")
    assert row["status"] == "WAITING_FOR_REQUIRED_OUTPUTS"
    assert "required_output:accepted_comparison" in row["missing"]


def test_windows_receipt_path_strings_are_normalized(workspace):
    _, paths = workspace
    item = paths[(2050, "Slow")]["jobs"]["UC_MILP"]
    q = json.loads(item["receipt"].read_text())
    for field in ("input_path", "solved_path"): q[field] = q[field].replace("/", "\\")
    _put(item["receipt"], q)
    # Refresh only the synthetic original source pin; preserve its semantics.
    report = paths[(2050, "Slow")]["root"] / "REPORTING"
    r1 = json.loads((report / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json").read_text())
    r1["protected_source_hashes"][str(item["receipt"])] = _sha(item["receipt"])
    _put(report / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json", r1)
    row = collection._case(collection._Plan(), 2050, "Slow")
    assert row["status"] == "VERIFIED_COMPLETE"


def test_default_path_adapter_reuses_final_v4_namespace_and_inherited_2040(tmp_path, monkeypatch):
    from mem_model import stage_b_uc2 as uc2
    monkeypatch.setattr(uc2, "ROOT", tmp_path)
    monkeypatch.setattr(uc2, "config", lambda: {"result_root": "results/final_methodology_v4"})
    monkeypatch.setattr(uc2.uc1, "parent_path", lambda *args: tmp_path / "parent.nc")
    monkeypatch.setattr(uc2.uc1, "derivative_path", lambda *args: tmp_path / "input.nc")
    monkeypatch.setattr(uc2, "_final_api", lambda *args, **kwargs: {"path": "input.nc", "reference_path": "reference.nc",
                                                               "sha256": "a" * 64, "reference_sha256": "b" * 64})
    inherited = collection._case_paths(2040, "Base")
    revised = collection._case_paths(2050, "Base")
    assert inherited["root"] == tmp_path / "results/final_methodology_v3/2040/Base"
    assert revised["root"] == tmp_path / "results/final_methodology_v4/2050/Base"
    assert inherited["jobs"]["UC_MILP"]["job_id"].startswith("FINAL_V3_UC2_2040")
    assert revised["jobs"]["UC_MILP"]["job_id"].startswith("FINAL_V4_UC2_2050")
    assert uc2.ACTIVE_VARIANT.get() is None


def test_future_2040_invocation_keeps_inherited_model_branch(workspace):
    _, paths = workspace
    report = paths[(2040, "Slow")]["root"] / "REPORTING"
    r1 = json.loads((report / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json").read_text())
    for item in paths[(2040, "Slow")]["jobs"].values():
        _rewrite(item["receipt"], final_methodology_variant="final_v4")
        r1["protected_source_hashes"][str(item["receipt"])] = _sha(item["receipt"])
    _put(report / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json", r1)
    row = collection._case(collection._Plan(), 2040, "Slow")
    assert row["status"] == "VERIFIED_COMPLETE"
    assert row["result_path_variant"] == "final_v3"
    assert row["inherited_configuration_branch"] == "FINAL_METHODOLOGY_V3"
    assert row["actual_invocation_variants"] == ["final_v4"]

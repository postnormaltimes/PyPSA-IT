"""CLI -m reporting routing; fixture-only, no model construction or solver."""
import ast
import inspect
import json
import os
from pathlib import Path
import runpy
import sys

import pytest

from mem_model import final_methodology_networks as final
from mem_model import stage_b_uc2 as uc2
from mem_model.reporting import uc2_postprocess as post
from mem_model.stage_b_zonal_vre import no_models_or_solves


HASHES = {
    "POST_P2X_CONTINUOUS_REFERENCE": "f60ee7ed3477acbd33760df1a4ba024f791168703b1e0d1630ba17f84b99685f",
    "UC_MILP": "828bf1eb7353d5e9652e2ccc5d5dbd0451067ef16ea144fb504008f86d40e103",
    "FIXED_COMMITMENT_PRICE_LP": "cd313232c713bb864c78d98a7c06b748bac34d54d8ccca79fc889ba203016574",
}
KEYS = {
    "POST_P2X_CONTINUOUS_REFERENCE": "reference_verification",
    "UC_MILP": "uc_verification",
    "FIXED_COMMITMENT_PRICE_LP": "price_verification",
}


@pytest.fixture(autouse=True)
def prohibit_model_and_solver_calls():
    with no_models_or_solves():
        yield


@pytest.fixture
def report_cases(tmp_path, monkeypatch):
    if os.name == "nt":
        tmp_path = Path("\\\\?\\" + str(tmp_path))
    monkeypatch.setattr(uc2, "ROOT", tmp_path)
    monkeypatch.setattr(final, "package", lambda y, s, **kwargs: {
        "path": f"inputs/{y}_{s}_final_UC.nc",
        "reference_path": f"inputs/{y}_{s}_final_reference.nc",
    })
    monkeypatch.setattr(uc2.uc1, "parent_path", lambda y, s: tmp_path / f"inputs/{y}_{s}_ordinary_reference.nc")
    monkeypatch.setattr(uc2.uc1, "derivative_path", lambda y, s: tmp_path / f"inputs/{y}_{s}_ordinary_UC.nc")
    monkeypatch.setattr(uc2, "_manual_solve", lambda *a, **k: pytest.fail("solver action dispatched"))
    monkeypatch.setattr(uc2, "final_qa", lambda *a: pytest.fail("final-qa must remain manual"))
    monkeypatch.setattr(uc2, "_load_solved", lambda *a: pytest.fail("cached extension must not load fixture networks"))
    monkeypatch.setattr(post, "update_uc2_comparisons", lambda year: {"status": "WAITING_FOR_COMPARABLE_REPORTS"})

    outputs = {}
    for variant in (None, "final_v1", "final_v2"):
        with uc2.execution_variant(variant):
            output = uc2.case_root(2040, "Base") / "REPORTING"
            output.mkdir(parents=True)
            outputs[variant] = output
            for kind in uc2.RUN_TYPES:
                paths = uc2.job_paths(2040, "Base", kind)
                for key in ("solved", "input"):
                    paths[key].parent.mkdir(parents=True, exist_ok=True)
                    paths[key].write_bytes(b"test fixture, not a PyPSA network")
                receipt = {"status": "PASS", "year": 2040, "scenario": "Base",
                           "solved_sha256": HASHES[kind]}
                if kind == "FIXED_COMMITMENT_PRICE_LP":
                    receipt["fixed_commitment_milp_sha256"] = HASHES["UC_MILP"]
                for key in ("receipt", "verification"):
                    paths[key].parent.mkdir(parents=True, exist_ok=True)
                    paths[key].write_text(json.dumps(receipt))
            original = {"status": "PASS", "year": 2040, "scenario": "Base",
                        "reporting_solver_invocations": 0,
                        **{KEYS[k]: h for k, h in HASHES.items()}}
            (output / "MEM_UC2_REPORTING_RECEIPT.json").write_text(json.dumps(original))
            cached = {"status": "PASS", "code_hashes": post._code_hashes(),
                      "protected_source_hashes": post._protected_sources(2040, "Base"),
                      "artifact_hashes": {}, "original_artifact_hashes": {}}
            (output / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json").write_text(json.dumps(cached))

    def verified(year, scenario, kind):
        value = uc2._read_json(uc2.job_paths(year, scenario, kind)["verification"])
        assert value["year"] == year and value["scenario"] == scenario
        assert value["solved_sha256"] == HASHES[kind]
        return value
    monkeypatch.setattr(uc2, "_verified_previous", verified)
    return outputs


@pytest.mark.parametrize("flags", [
    ["--variant", "final_v1"], ["--variant=final_v1"],
    ["--variant", "final_v2"], ["--variant=final_v2"], [],
])
@pytest.mark.filterwarnings("ignore:.*mem_model.stage_b_uc2.*:RuntimeWarning")
def test_actual_module_cli_preserves_context_through_extension_and_all_job_paths(
        report_cases, monkeypatch, capsys, flags):
    expected = flags[-1].split("=")[-1] if flags else None
    calls, extension_contexts, protected_contexts = [], [], []
    original_paths, original_extend, original_protected = uc2.job_paths, post.extend_uc2_report, post._protected_sources

    def traced_paths(year, scenario, kind):
        result = original_paths(year, scenario, kind)
        calls.append((uc2.ACTIVE_VARIANT.get(), kind, result))
        return result

    def traced_extend(*args, **kwargs):
        extension_contexts.append(uc2.ACTIVE_VARIANT.get())
        assert args[2] == report_cases[expected]
        return original_extend(*args, **kwargs)

    def traced_protected(*args):
        protected_contexts.append(uc2.ACTIVE_VARIANT.get())
        return original_protected(*args)

    monkeypatch.setattr(uc2, "job_paths", traced_paths)
    monkeypatch.setattr(post, "extend_uc2_report", traced_extend)
    monkeypatch.setattr(post, "_protected_sources", traced_protected)
    monkeypatch.setattr(sys, "argv", ["mem_model.stage_b_uc2", "report", "--year", "2040",
                                     "--scenario", "Base", *flags])
    runpy.run_module("mem_model.stage_b_uc2", run_name="__main__")
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "PASS"
    assert extension_contexts == [expected]
    assert protected_contexts == [expected]
    assert calls and {v for v, _, _ in calls} == {expected}
    root_name = f"final_methodology_{expected[-2:]}" if expected else "uc2_full_year"
    assert {kind for _, kind, _ in calls} == set(uc2.RUN_TYPES)
    for _, kind, paths in calls:
        assert root_name in paths["solved"].parts
        assert root_name in paths["receipt"].parts
        solve = uc2._read_json(paths["receipt"])
        verification = uc2._read_json(paths["verification"])
        assert result[KEYS[kind]] == solve["solved_sha256"] == verification["solved_sha256"] == HASHES[kind]
    assert uc2.ACTIVE_VARIANT.get() is None


@pytest.mark.parametrize("kind", uc2.RUN_TYPES)
def test_final_lineage_guard_still_rejects_each_stale_run_type(report_cases, kind):
    with uc2.execution_variant("final_v1"):
        path = report_cases["final_v1"] / "MEM_UC2_REPORTING_RECEIPT.json"
        receipt = json.loads(path.read_text())
        receipt[KEYS[kind]] = "stale"
        path.write_text(json.dumps(receipt))
        with pytest.raises(RuntimeError, match="UC2_REPORT_EXTENSION_STALE_LINEAGE"):
            post.extend_uc2_report(2040, "Base", report_cases["final_v1"])


def test_price_parent_lineage_guard_is_not_bypassed(report_cases):
    with uc2.execution_variant("final_v1"):
        path = uc2.job_paths(2040, "Base", "FIXED_COMMITMENT_PRICE_LP")["receipt"]
        receipt = json.loads(path.read_text())
        receipt["fixed_commitment_milp_sha256"] = "different MILP"
        path.write_text(json.dumps(receipt))
        with pytest.raises(RuntimeError, match="UC2_PRICE_PARENT_LINEAGE_FAIL"):
            post.extend_uc2_report(2040, "Base", report_cases["final_v1"])


def test_report_verified_has_one_definition_and_two_intentional_extension_branches():
    source = Path(uc2.__file__).read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    assert sum(isinstance(node, ast.FunctionDef) and node.name == "_report_verified"
               for node in tree.body) == 1
    implementation = inspect.getsource(uc2._report_verified)
    assert "extend_uc2_report(year, scenario, output)" in implementation
    assert "extend_uc2_report(year, scenario, output, uc_network=milp)" in implementation

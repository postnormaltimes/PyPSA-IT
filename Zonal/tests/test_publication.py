"""Focused publication lifecycle/authority guards; no production fixture solve."""
import json
from pathlib import Path

import pytest

from mem_model.reporting import publication as p


def test_register_and_explicit_unexecuted_milestone():
    cfg = p.configuration()
    assert len(cfg["figures"]) == 11
    assert cfg["baselines"][2040]["variant"] == "final_v3"
    assert cfg["baselines"][2050]["variant"] == "final_v6"
    assert cfg["historical_versions"][2040][1]["status"] == "PREPARED_NOT_EXECUTED"
    assert len(list(p._expected(cfg))) == 83
    assert len(list(p._atlas_expected(cfg))) == 48


def test_source_hash_mismatch_and_network_read_fail_closed(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("accepted")
    sources = p.Sources()
    sources.pin(source, p.sha256_file(source))
    source.write_text("changed")
    with pytest.raises(ValueError, match="SOURCE_HASH_MISMATCH"):
        sources.verify(source)
    with pytest.raises(ValueError, match="NETWORK_READ_FORBIDDEN"):
        sources.sha(tmp_path / "solved.nc")


def test_conflicting_lineage_pins_fail_closed(tmp_path):
    sources = p.Sources()
    source = tmp_path / "price.csv"
    sources.pin(source, "a" * 64)
    with pytest.raises(ValueError, match="CONFLICTING_SOURCE_PINS"):
        sources.pin(source, "b" * 64)


def test_check_is_read_only_and_counts_every_scope(tmp_path, monkeypatch):
    cfg = p.configuration()
    cfg["_owner"] = tmp_path
    monkeypatch.setattr(p, "available_cases", lambda *a: ([], [dict(year=2040, scenario="Slow")]))
    monkeypatch.setattr(p, "_code_pins", lambda *a: {})
    value = p.check(cfg)
    assert value["status"] == "INCOMPLETE"
    assert value["expected_rendered_assets"] == 83
    assert not list(tmp_path.iterdir())


def test_missing_source_cannot_leave_complete_receipt(tmp_path, monkeypatch):
    cfg = p.configuration()
    cfg["_owner"] = tmp_path
    p._json(tmp_path / "PUBLICATION_RECEIPT.json", {"status": "COMPLETE"})
    monkeypatch.setattr(p, "available_cases", lambda *a: ([], [dict(year=2050, scenario="High")]))
    monkeypatch.setattr(p, "_code_pins", lambda *a: {})
    monkeypatch.setattr(p, "_render", lambda *a: pytest.fail("Partial source must not render"))
    value = p._build(cfg, "combined")
    assert value["status"] == "PARTIAL_WAITING_FOR_FINAL_QA"
    assert p._read(tmp_path / "PUBLICATION_RECEIPT.json")["status"] == "INCOMPLETE"


def test_numerical_qa_pass_survives_publication_failure(tmp_path, monkeypatch):
    from mem_model import stage_b_uc2 as uc2
    cfg = p.configuration()
    cfg["_owner"] = tmp_path
    numerical = {"status": "PASS", "year": 2040, "scenario": "Base"}
    monkeypatch.setattr(p, "configuration", lambda *a: cfg)
    monkeypatch.setattr(uc2, "case_root", lambda *a: p.ROOT / cfg["baselines"][2040]["result_root"] / "2040/Base")
    monkeypatch.setattr(uc2, "final_qa", lambda *a: numerical)
    def fail(*args):
        raise ValueError("render failed")
    monkeypatch.setattr(p, "after_successful_qa", fail)
    value = p.complete(2040, "Base", "final_v4")
    assert value["status"] == "FINAL_QA_PASS_PUBLICATION_FAILED"
    assert value["numerical"] == numerical
    assert p._read(tmp_path / "PUBLICATION_RECEIPT.json")["status"] == "INCOMPLETE"


def test_complete_rejects_unselected_namespace_before_qa(tmp_path, monkeypatch):
    from mem_model import stage_b_uc2 as uc2
    cfg = p.configuration()
    monkeypatch.setattr(p, "configuration", lambda *a: cfg)
    monkeypatch.setattr(uc2, "case_root", lambda *a: tmp_path / "other_version")
    monkeypatch.setattr(uc2, "final_qa", lambda *a: pytest.fail("Wrong variant must not run QA"))
    with pytest.raises(ValueError, match="SELECTED_BASELINE_NAMESPACE"):
        p.complete(2040, "Base", "final_v1")


def test_reporting_guard_forbids_network_and_solver_calls():
    import pypsa
    from linopy import Model
    with p.reporting_guard() as (networks, solvers):
        with pytest.raises(RuntimeError, match="NETWORK_OPEN_FORBIDDEN"):
            pypsa.Network()
        assert networks["network_opens"] == 1
        with pytest.raises(RuntimeError, match="SOLVER_CALL_FORBIDDEN"):
            Model.solve(object())  # Exercise the patched boundary without constructing a model.
        assert solvers["solver_invocations"] == 1


def test_rendered_identity_and_gallery_links_match_real_exports(tmp_path, monkeypatch):
    import matplotlib.pyplot as plt
    import pandas as pd
    cfg = p.configuration()
    cfg["_owner"] = tmp_path
    monkeypatch.setattr(p, "_code_pins", lambda *a: {})
    monkeypatch.setattr(p, "_environment", lambda: {})
    figure, axis = plt.subplots()
    axis.plot([0, 1], [0, 1])
    monkeypatch.setattr(p, "_render", lambda *a: dict(figure=figure,
        data=pd.DataFrame({"x": [0, 1]}), sources=[], qa={}, caption="Fixture"))
    definition = p._figure_def(cfg, "3")
    value, generated = p._save(cfg, p.Sources(), tmp_path / "combined", definition, [])
    assert generated and value["name"] == definition["name"]
    p._json(tmp_path / "editorial_review/previews/alternative.json", value)
    p._json(tmp_path / "zonal_atlas/alternative.json", value)
    monkeypatch.setattr(p, "check", lambda *a: dict(status="COMPLETE", figures=[
        dict(scope="combined", name=definition["name"], status="COMPLETE")]))
    rows = p._index(cfg)
    assert len(rows) == 1  # Editorial alternatives cannot become principal assets.
    assert rows[0]["status"] == "COMPLETE"
    for key in ("png", "svg", "data", "metadata"):
        assert (tmp_path / rows[0][key]).exists()
    assert ".csv.png" not in (tmp_path / "index.html").read_text(encoding="utf-8")

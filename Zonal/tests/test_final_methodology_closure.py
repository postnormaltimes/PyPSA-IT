"""Persistent sequential preparation gates; no optimization required."""
import pytest
from mem_model import final_methodology_closure as closure
from mem_model.common import dump_json
from mem_model.stage_b_zonal_vre import no_models_or_solves


@pytest.fixture(autouse=True)
def no_optimizer():
    with no_models_or_solves():
        yield


@pytest.fixture
def state(tmp_path, monkeypatch):
    path = tmp_path / "STATE.json"
    monkeypatch.setattr(closure, "STATE", path)
    state = {"controlling_spec_sha256": closure.SPEC_SHA256, "next_phase": "W",
             "phases": {p: {"status": "NOT_STARTED"} for p in closure.PHASES}}
    dump_json(path, state)
    return state


def test_phase_cannot_skip_wind(state):
    with pytest.raises(RuntimeError, match="SEQUENTIAL"):
        closure.record_phase("H", "PASS", parents=[], artifacts=[], decisions=[], tests={}, unresolved=[])


def test_pass_advances_exactly_one_phase(state):
    result = closure.record_phase("W", "PASS", parents=[], artifacts=[], decisions=[], tests={}, unresolved=[])
    assert result["next_phase"] == "H"
    assert result["phases"]["W"]["status"] == "PASS"


def test_block_preserves_resume_phase_without_authorizing_successor(state):
    result = closure.record_phase("W", "BLOCKED", parents=[], artifacts=[], decisions=[], tests={}, unresolved=["decision"])
    assert result["next_phase"] == "W" and result["state"] == "BLOCKED"
    assert result["phases"]["H"]["status"] == "NOT_STARTED"


def test_changed_specification_cannot_silently_reuse_state(state):
    state["controlling_spec_sha256"] = "wrong"
    dump_json(closure.STATE, state)
    with pytest.raises(RuntimeError, match="SPEC_DRIFT"):
        closure.initialise()

"""Single-case recovery guards with isolated files; no mathematical model."""
import json

import pytest

from mem_model import stage_b_uc2 as uc2
from mem_model.stage_b_zonal_vre import no_models_or_solves


@pytest.fixture(autouse=True)
def no_optimizer():
    with no_models_or_solves():
        yield


@pytest.mark.parametrize("existing", ["solved", "receipt", "evidence"])
def test_price_refuses_existing_success_or_capture_before_model(tmp_path, monkeypatch, existing):
    from mem_model.reporting.price_evidence import CAPTURE_FILES
    paths = {"directory": tmp_path, "solved": tmp_path / "SOLVED.nc", "receipt": tmp_path / "RECEIPT.json"}
    destination = tmp_path / CAPTURE_FILES[0] if existing == "evidence" else paths[existing]
    destination.write_bytes(b"preserved")
    monkeypatch.setattr(uc2, "job_paths", lambda *_: paths)
    monkeypatch.setattr(uc2, "preflight", lambda *_: pytest.fail("Overwrite refusal must precede preflight"))
    monkeypatch.setattr(uc2, "_solver_contract", lambda: pytest.fail("No solver access"))
    with uc2.execution_variant("final_v4"), pytest.raises(RuntimeError, match="UC2_OVERWRITE_REFUSED"):
        uc2._manual_solve(2040, "High", "FIXED_COMMITMENT_PRICE_LP")
    assert destination.read_bytes() == b"preserved"


def test_empty_price_directory_reuses_same_case_verified_uc_before_solver(tmp_path, monkeypatch):
    paths = {"directory": tmp_path, "solved": tmp_path / "SOLVED.nc", "receipt": tmp_path / "RECEIPT.json"}
    calls = []
    monkeypatch.setattr(uc2, "job_paths", lambda *_: paths)
    monkeypatch.setattr(uc2, "preflight", lambda y, s: calls.append(("preflight", y, s)))
    monkeypatch.setattr(uc2, "_governance_gate", lambda *_: "MANUAL_EXECUTION")
    monkeypatch.setattr(uc2, "_verified_previous", lambda y, s, k: calls.append(("verified", y, s, k)))
    monkeypatch.setattr(uc2, "_check_input_hash", lambda y, s, k: calls.append(("input", y, s, k)) or "1" * 64)
    class BeforeSolver(Exception):
        pass
    def stop():
        raise BeforeSolver
    monkeypatch.setattr(uc2, "_solver_contract", stop)
    with uc2.execution_variant("final_v4"), pytest.raises(BeforeSolver):
        uc2._manual_solve(2040, "High", "FIXED_COMMITMENT_PRICE_LP")
    assert calls == [("preflight", 2040, "High"), ("verified", 2040, "High", "UC_MILP"),
                     ("input", 2040, "High", "FIXED_COMMITMENT_PRICE_LP")]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("defect", ["wrong_case", "stale_hash", "none"])
def test_verified_uc_dependency_remains_case_and_hash_bound(tmp_path, monkeypatch, defect):
    value = {"status": "PASS", "year": 2040, "scenario": "High", "solved_sha256": "2" * 64}
    if defect == "wrong_case":
        value["scenario"] = "Base"
    elif defect == "stale_hash":
        value["solved_sha256"] = "3" * 64
    verification = tmp_path / "VERIFY_UC.json"
    verification.write_text(json.dumps(value))
    monkeypatch.setattr(uc2, "job_paths", lambda *_: {"verification": verification})
    def load(year, scenario, kind):
        assert (year, scenario, kind) == (2040, "High", "UC_MILP")
        return None, {"solved_sha256": "2" * 64}
    monkeypatch.setattr(uc2, "_load_solved", load)
    if defect == "none":
        assert uc2._verified_previous(2040, "High", "UC_MILP") == value
    else:
        with pytest.raises(RuntimeError, match="UC2_DEPENDENCY_FAIL"):
            uc2._verified_previous(2040, "High", "UC_MILP")
    assert json.loads(verification.read_text()) == value

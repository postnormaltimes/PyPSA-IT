"""Reporting boundary tests: no optimizer or production network mutation."""
import json
from pathlib import Path

import pandas as pd
import pytest
import xarray as xr

from mem_model import stage_b_uc2 as uc2
from mem_model.reporting.uc2_postprocess import no_solver_calls, read_lp_prices


def test_reporting_guard_blocks_pypsa_and_linopy_without_solving():
    import pypsa
    from linopy import Model
    network = pypsa.Network()
    with no_solver_calls() as counters:
        with pytest.raises(RuntimeError, match="SOLVER_CALL_FORBIDDEN"):
            network.optimize()
        with pytest.raises(RuntimeError, match="SOLVER_CALL_FORBIDDEN"):
            network.optimize.solve_model()
        with pytest.raises(RuntimeError, match="SOLVER_CALL_FORBIDDEN"):
            Model().solve()
    assert counters["solver_invocations"] == 3


def test_lp_reader_uses_only_price_duals_and_preserves_file(tmp_path):
    snapshots = pd.date_range("2040-01-01", periods=2, freq="h")
    path = tmp_path / "pricing.nc"
    xr.Dataset({"buses_t_marginal_price": (("snapshots", "market"), [[1., 3.], [2., 4.]]),
                "generators_t_p": (("snapshots", "generator"), [[999999.], [999999.]]),
                "links_t_p0": (("snapshots", "link"), [[999999.], [999999.]])},
               coords={"snapshots": snapshots, "market": ["NORD", "EXT_FR"],
                       "generator": ["poison_primal"], "link": ["poison_flow"]}).to_netcdf(path)
    original = path.read_bytes()
    result = read_lp_prices(path, snapshots)
    assert list(result.columns) == ["NORD", "EXT_FR"]
    assert result.to_numpy().tolist() == [[1., 3.], [2., 4.]]
    assert path.read_bytes() == original


def test_existing_report_routes_only_to_extension(monkeypatch, tmp_path):
    from mem_model.reporting import uc2_postprocess as post
    output = tmp_path / "REPORTING"
    output.mkdir()
    receipt = {"status": "PASS", "year": 2040, "scenario": "Base"}
    (output / "MEM_UC2_REPORTING_RECEIPT.json").write_text(json.dumps(receipt))
    monkeypatch.setattr(uc2, "case_root", lambda *_: tmp_path)
    monkeypatch.setattr(uc2, "_verified_previous", lambda *_: {"status": "PASS"})
    monkeypatch.setattr(uc2, "_load_solved", lambda *_: pytest.fail("Legacy reporting must not be regenerated"))
    calls = []
    monkeypatch.setattr(post, "extend_uc2_report", lambda *args: calls.append(args) or {"status": "PASS"})
    monkeypatch.setattr(post, "update_uc2_comparisons", lambda year: {"status": "PASS"})
    result = uc2.report(2040, "Base")
    assert result["vis_reporting_r1"]["status"] == "PASS"
    assert len(calls) == 1


def test_cli_reporting_has_no_manual_run_dispatch(monkeypatch):
    monkeypatch.setattr(uc2, "_manual_solve", lambda *_: pytest.fail("manual solver called"))
    monkeypatch.setattr(uc2, "report", lambda year, scenario: {"status": "PASS"})
    assert uc2.main(["report", "--year", "2040", "--scenario", "Base"]) is None

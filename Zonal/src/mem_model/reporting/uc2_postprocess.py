"""Verified UC2 reporting extension. This module has no model-building path."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import xarray as xr

from ..common import ROOT, dump_json, sha256_file


@contextmanager
def no_solver_calls():
    """Fail before any solve and record attempted invocations for reporting QA."""
    from pypsa.optimization.optimize import OptimizationAccessor
    from linopy import Model
    counts = {"solver_invocations": 0}
    def forbidden(*args, **kwargs):
        counts["solver_invocations"] += 1
        raise RuntimeError("UC2_REPORTING_SOLVER_CALL_FORBIDDEN")
    with patch.object(OptimizationAccessor, "__call__", forbidden), patch.object(
            OptimizationAccessor, "solve_model", forbidden), patch.object(Model, "solve", forbidden):
        yield counts


def read_lp_prices(path: Path, snapshots: pd.Index) -> pd.DataFrame:
    """Read only dual prices; LP primal variables never enter this adapter."""
    with xr.open_dataset(path) as dataset:
        frame = dataset["buses_t_marginal_price"].to_pandas()
        if "snapshots_snapshot" in dataset:
            frame.index = pd.DatetimeIndex(dataset["snapshots_snapshot"].values)
    frame.index = pd.DatetimeIndex(frame.index, name="snapshot")
    frame.columns = frame.columns.astype(str)
    from .canonical_results import _zone_order
    markets = [column for column in frame.columns if column in _zone_order() or column.startswith("EXT_")]
    frame = frame.loc[:, markets]
    if not frame.index.equals(pd.DatetimeIndex(snapshots)):
        raise RuntimeError("UC2_PRICE_SNAPSHOT_ALIGNMENT_FAIL")
    if not np.isfinite(frame.to_numpy()).all():
        raise RuntimeError("UC2_PRICE_FINITE_FAIL")
    return frame


def _protected_sources(year, scenario):
    from .. import stage_b_uc2 as uc2
    paths = set()
    for kind in uc2.RUN_TYPES:
        item = uc2.job_paths(year, scenario, kind)
        paths.update(item[key] for key in ("solved", "input", "receipt", "verification"))
    return {str(path): sha256_file(path) for path in sorted(paths)}


def _code_hashes():
    names = ["src/mem_model/stage_b_uc2.py", "src/mem_model/reporting/uc2_postprocess.py",
             "src/mem_model/reporting/uc2_diagnostics.py", "src/mem_model/visualization/uc2_vis_x1.py",
             "config/vis_x1.yaml", "config/stage_b_reporting.yaml"]
    return {name: sha256_file(ROOT / name) for name in names}


def extend_uc2_report(year: int, scenario: str, report_dir: Path, uc_network=None) -> dict:
    """Append new diagnostics and VIS assets to an accepted canonical report."""
    from .. import stage_b_uc2 as uc2
    from .uc2_diagnostics import build_uc2_diagnostics
    from ..visualization.uc2_vis_x1 import render_uc2_vis

    original = uc2._read_json(report_dir / "MEM_UC2_REPORTING_RECEIPT.json")
    if (original.get("status") != "PASS" or original.get("year") != year or
            original.get("scenario") != scenario or original.get("reporting_solver_invocations") != 0):
        raise RuntimeError("UC2_REPORT_EXTENSION_UNVERIFIED_REPORT")
    sources = _protected_sources(year, scenario)
    solve_receipts = {kind: uc2._read_json(uc2.job_paths(year, scenario, kind)["receipt"])
                      for kind in uc2.RUN_TYPES}
    for kind, receipt in solve_receipts.items():
        key = {"UC_MILP": "uc_verification", "FIXED_COMMITMENT_PRICE_LP": "price_verification",
               "POST_P2X_CONTINUOUS_REFERENCE": "reference_verification"}[kind]
        if original.get(key) != receipt["solved_sha256"]:
            raise RuntimeError("UC2_REPORT_EXTENSION_STALE_LINEAGE")
    if solve_receipts["FIXED_COMMITMENT_PRICE_LP"]["fixed_commitment_milp_sha256"] != solve_receipts["UC_MILP"]["solved_sha256"]:
        raise RuntimeError("UC2_PRICE_PARENT_LINEAGE_FAIL")

    receipt_path = report_dir / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json"
    code_hashes = _code_hashes()
    if receipt_path.exists():
        previous = uc2._read_json(receipt_path)
        if previous.get("status") == "PASS" and previous.get("code_hashes") == code_hashes and previous.get("protected_source_hashes") == sources:
            if all((report_dir / name).is_file() and sha256_file(report_dir / name) == digest
                   for name, digest in {**previous.get("artifact_hashes", {}), **previous.get("original_artifact_hashes", {})}.items()):
                return previous

    # Existing canonical outputs/receipts remain byte-for-byte unchanged.
    preserved = {str(path.relative_to(report_dir)): sha256_file(path)
                 for path in report_dir.rglob("*") if path.is_file() and
                 not path.name.startswith("uc2_") and "VIS_X1" not in path.parts and
                 "R1" not in path.name}
    with no_solver_calls() as guard:
        if uc_network is None:
            uc_network, _ = uc2._load_solved(year, scenario, "UC_MILP")
        prices = read_lp_prices(uc2.job_paths(year, scenario, "FIXED_COMMITMENT_PRICE_LP")["solved"], uc_network.snapshots)
        tables = build_uc2_diagnostics(uc_network, prices)
        reconciliation = tables["diagnostics_reconciliation"]
        from . import canonical_results as canonical
        # Reconcile the new binding with the verified, unchanged canonical report.
        saved_dispatch = pd.read_parquet(report_dir / "CANONICAL/statistics/dispatch_8760_by_zone.parquet")
        fresh_dispatch = canonical.dispatch_8760_by_zone(uc_network)
        pd.testing.assert_frame_equal(saved_dispatch, fresh_dispatch, check_exact=False, atol=1e-6, rtol=1e-10)
        pd.testing.assert_frame_equal(pd.read_parquet(report_dir / "storage_hydro_soc_hourly.parquet"),
                                      uc_network.stores_t.e, check_exact=False, atol=1e-6, rtol=1e-10)
        pd.testing.assert_frame_equal(pd.read_parquet(report_dir / "link_bus0_flows_hourly.parquet"),
                                      uc_network.links_t.p0, check_exact=False, atol=1e-6, rtol=1e-10)
        saved_prices = pd.read_csv(report_dir / "fixed_commitment_prices_hourly.csv", index_col=0, parse_dates=True)
        if not np.allclose(saved_prices.to_numpy(), prices[saved_prices.columns].to_numpy(), atol=1e-8, rtol=1e-10):
            raise RuntimeError("UC2_ORIGINAL_LP_PRICE_RECONCILIATION_FAIL")
        saved_stats = pd.read_csv(report_dir / "CANONICAL/statistics/zonal_price_statistics.csv").set_index("zone")
        fresh_stats = tables["price_annual_summary"].set_index("market").loc[saved_stats.index]
        if not np.allclose(saved_stats.load_weighted_mean_EUR_per_MWh,
                           fresh_stats.load_weighted_mean_EUR_per_MWh, atol=1e-7, rtol=1e-10):
            raise RuntimeError("UC2_CANONICAL_PRICE_WEIGHTING_RECONCILIATION_FAIL")
        economics = uc2._read_json(report_dir / "economic_comparison.json")
        if not np.isclose(economics["uc_milp_objective_EUR"], float(uc_network.objective), atol=1e-4, rtol=1e-10):
            raise RuntimeError("UC2_CANONICAL_OBJECTIVE_RECONCILIATION_FAIL")
        reconciliation = pd.concat([reconciliation, pd.DataFrame([
            {"check": name, "passed": True, "detail": detail} for name, detail in [
                ("physical_generation_load_P2X_storage_dispatch", "UC dispatch equals original canonical hourly table"),
                ("storage_hydro_energy_trajectories", "UC Store energy equals original report"),
                ("UC_interface_flow_source", "UC Link p0 equals original report; gross table remains QA source"),
                ("LP_zonal_price_source", "Selective LP dual extraction equals original verified hourly prices"),
                ("canonical_price_weighting", "Rigid load plus P2X withdrawal from UC; accepted canonical metric preserved"),
                ("canonical_UC_objective", "UC network objective equals original economic report")]]
        )], ignore_index=True)
        tables["diagnostics_reconciliation"] = reconciliation
        if not reconciliation["passed"].all():
            raise RuntimeError(f"UC2_DIAGNOSTICS_RECONCILIATION_FAIL: {reconciliation.to_dict(orient='records')}")
        stats = report_dir / "CANONICAL" / "statistics"
        stats.mkdir(parents=True, exist_ok=True)
        artifacts = []
        for name, table in tables.items():
            indexed = isinstance(table.index, pd.DatetimeIndex)
            path = stats / (f"uc2_{name}.parquet" if indexed else f"uc2_{name}.csv")
            if indexed:
                table.to_parquet(path)
            else:
                table.to_csv(path, index=False, lineterminator="\n")
            artifacts.append(path)
        authority = []
        for kind, solve in solve_receipts.items():
            item = uc2.job_paths(year, scenario, kind)
            authority.append({"result_id": solve["job_id"], "year": year, "scenario": scenario,
                              "network_path": str(item["solved"]), "network_sha256": solve["solved_sha256"],
                              "solved_or_unsolved": "SOLVED", "parent_model_version": f"FINAL_METHODOLOGY_{uc2.ACTIVE_VARIANT.get()[-2:].upper()}" if uc2.ACTIVE_VARIANT.get() in uc2.FINAL_VARIANTS else "P2X_FLEX_V1 / UC1_COMMON_V1",
                              "P2X_status": "CORRECTED_SOLVED", "UC_status": "VERIFIED_UC2" if kind != "POST_P2X_CONTINUOUS_REFERENCE" else "CONTINUOUS_REFERENCE",
                              "receipt": str(item["receipt"]), "manifest": str(item["verification"]),
                              "current_role": {"UC_MILP": "CANONICAL_PHYSICAL", "FIXED_COMMITMENT_PRICE_LP": "CANONICAL_PRICES_DUALS_ONLY",
                                               "POST_P2X_CONTINUOUS_REFERENCE": "UC_UPLIFT_REFERENCE_ONLY"}[kind],
                              "visualization_allowed": True,
                              "limitation": "LP primal quantities excluded" if kind == "FIXED_COMMITMENT_PRICE_LP" else "Stage-B zonal/interface resolution"})
        authority_path = report_dir / "uc2_VIS_RESULT_AUTHORITY_REGISTER.csv"
        pd.DataFrame(authority).to_csv(authority_path, index=False, lineterminator="\n")
        artifacts.append(authority_path)
        contract_path = report_dir / "uc2_REPORTING_AUTHORITY_CONTRACT.json"
        dump_json(contract_path, {
            "physical_source": "VERIFIED_UC_MILP", "price_source": "VERIFIED_FIXED_COMMITMENT_LP_DUALS",
            "pricing_LP_primal": "NOT_CANONICAL",
            "canonical_interface_quantity": "NET_FLOW_A_TO_B_MW = signed Link.p0" if uc_network.meta.get('network_v2b_applied',False) else "net_A_to_B = UC_flow_A_to_B - UC_flow_B_to_A",
            "canonical_price_weight": "UC rigid electrical load + UC P2X electrical withdrawal, snapshot-weighted",
            "artifact_roles": {
                "link_bus0_flows_hourly.parquet": {"status": "ENGINEERING / QA ONLY",
                    "limitation": "Gross directional UC Link variables; auxiliary paths and simultaneous counterflow are not canonical physical interface exchanges"},
                "CANONICAL/statistics/network_topology_interfaces.csv": {"status": "ENGINEERING / QA ONLY",
                    "limitation": "Directional model objects; canonical corridors in uc2_net_interface_registry.csv"},
                "CANONICAL/statistics/uc2_net_interface_hourly_MW.parquet": {"status": "CURRENT_ACCEPTED_RESULT", "resolution": "ZONAL_INTERFACE"},
                "CANONICAL/statistics/uc2_gross_counterflow_QA_only.csv": {"status": "ENGINEERING / QA ONLY"}}})
        artifacts.append(contract_path)
        visual = render_uc2_vis(uc_network, prices, year, scenario, report_dir, tables)
        artifacts.extend(path for path in (report_dir / "VIS_X1").rglob("*") if path.is_file())

    unchanged_sources = sources == _protected_sources(year, scenario)
    unchanged_original = all(sha256_file(report_dir / name) == digest for name, digest in preserved.items())
    if not unchanged_sources or not unchanged_original:
        raise RuntimeError("UC2_REPORTING_IMMUTABILITY_FAIL")
    manifest = report_dir / "CANONICAL" / "MEM_CANONICAL_REPORTING_R1_MANIFEST.csv"
    pd.DataFrame([{"artifact": str(path.relative_to(report_dir)), "sha256": sha256_file(path),
                   "role": "GROSS_COUNTERFLOW_QA_ONLY" if "gross_counterflow" in path.name else "UC2_R1_REPORTING_EXTENSION"}
                  for path in sorted(set(artifacts))]).to_csv(manifest, index=False, lineterminator="\n")
    artifacts.append(manifest)
    receipt = {"status": "PASS", "final_state": "UC2_VIS_AND_REPORTING_INTEGRATION_PASS",
               "model_generation": "UC2", "year": year, "scenario": scenario,
               "result_status": "VERIFIED_RESULT_PENDING_ANALYTICAL_REVIEW" if uc2.ACTIVE_VARIANT.get() in uc2.FINAL_VARIANTS else "CURRENT_ACCEPTED_RESULT", "acceptance_basis": f"verified {uc2.ACTIVE_VARIANT.get()} solve/verification receipts; analytical acceptance tracked separately" if uc2.ACTIVE_VARIANT.get() in uc2.FINAL_VARIANTS else "verified solve/verification receipts and completed 2040 review authorized by user",
               "physical_source": "UC_MILP", "price_source": "FIXED_COMMITMENT_PRICE_LP",
               "source_authority": {"physical": "UC_MILP", "prices": "FIXED_COMMITMENT_LP_DUALS",
                                    "pricing_LP_primal": "NOT_CANONICAL", "gross_counterflow": "ENGINEERING_QA_ONLY"},
               "reporting_solver_invocations": guard["solver_invocations"], "production_networks_unchanged": unchanged_sources,
               "original_reports_unchanged": unchanged_original, "protected_source_hashes": sources,
               "original_artifact_hashes": preserved, "code_hashes": code_hashes,
               "diagnostics_checks_passed": int(reconciliation.passed.sum()), "visualization": visual,
               "canonical_objective_EUR": solve_receipts["UC_MILP"]["objective_EUR"],
               "artifact_hashes": {str(path.relative_to(report_dir)): sha256_file(path) for path in sorted(set(artifacts))}}
    dump_json(receipt_path, receipt)
    return receipt


def update_uc2_comparisons(year: int) -> dict:
    from .. import stage_b_uc2 as uc2
    from ..visualization.uc2_comparison import build_uc2_comparisons
    cases = {scenario: uc2.case_root(year, scenario) / "REPORTING"
             for case_year, scenario in uc2.SCENARIOS if case_year == year and
             (uc2.case_root(year, scenario) / "REPORTING" / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json").is_file()}
    if len(cases) < 2:
        return {"status": "WAITING_FOR_COMPARABLE_REPORTS", "cases": len(cases)}
    with no_solver_calls():
        return build_uc2_comparisons(year, cases, ROOT / uc2.config()["result_root"] / str(year) / "COMPARISONS_VIS_X1")

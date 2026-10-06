"""Sparse, build-only P2X geography successors. No execution-path integration.

P2X_FLEX_V2 copies P2X_FLEX_V1; final_v2 independently copies final_v1 UC
and reference inputs. Only seven P2X p_nom values and seven annual constants
change. All inherited metadata remain historical parent provenance; the sole
new metadata key explicitly identifies the locked successor and its lineage.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import pypsa

from .common import ROOT, ZONES, SCENARIOS, dump_json, load_yaml, sha256_file
from .stage_b_p2x_flex import scenario_totals  # Pure accepted national arithmetic only.

VERSION = "P2X_FLEX_V2"
CFG = ROOT / "config/stage_b_p2x_flex_v2.yaml"
FINAL_CFG = ROOT / "config/final_methodology_execution_v2.yaml"
KEY = ROOT / "pre_pypsa_inputs/MEM_P2X_Zonal_Spatial_Key_V2.csv"
DERIVATION = ROOT / "research/derivations/italy/p2x_spatial_v2"
QA = ROOT / "qa/stage_b/p2x_flex_v2"
RUNTIME = ROOT / "runtime_inputs/p2x_flex_v2"
NETWORKS = ROOT / "networks/unsolved/p2x_flex_v2"
FINAL_NETWORKS = ROOT / "networks/unsolved/final_methodology_v2"
FINAL_AUTHORITY = ROOT / "qa/final_methodology_closure/final"
NUMERATORS = (10, 3, 6, 32, 7, 13, 3)
COPIED_MEMBERS = ("load_hourly.parquet", "generator_availability_hourly.parquet",
                  "hydro_inflow_hourly.parquet", "hydro_runtime_parameters.csv",
                  "external_prices_hourly.parquet")
MANIFEST = "MEM_STAGE_B_P2X_FLEX_V2_RUNTIME_MANIFEST.csv"
RECEIPT = "MEM_STAGE_B_P2X_FLEX_V2_PREPARATION_RECEIPT.json"
VERIFICATION = "MEM_STAGE_B_P2X_FLEX_V2_FINAL_VERIFICATION.json"
META_KEY = "p2x_spatial_v2"
ALLOWED_CHANGES = ("P2X_GENERATOR_P_NOM", "P2X_ANNUAL_CONSTRAINT_CONSTANT",
                   "VERSION_LINEAGE_METADATA_ONLY")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def csv(path):
    return pd.read_csv(path, float_precision="round_trip")


def relative(path):
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    return str(path.relative_to(ROOT))


@contextmanager
def no_models_or_solves():
    from linopy import Model
    from pypsa.optimization.optimize import OptimizationAccessor
    def forbidden(*args, **kwargs):
        raise RuntimeError("P2X_V2_MODEL_OR_SOLVER_FORBIDDEN")
    with patch.object(Model, "__init__", forbidden), patch.object(Model, "solve", forbidden), \
         patch.object(OptimizationAccessor, "__call__", forbidden), \
         patch.object(OptimizationAccessor, "create_model", forbidden), \
         patch.object(OptimizationAccessor, "solve_model", forbidden):
        yield


def key_table():
    cfg = load_yaml(CFG)
    if (cfg["version"] != VERSION or cfg["share_denominator"] != 74 or
        tuple(cfg["share_numerators"][z] for z in ZONES) != NUMERATORS or
        any(cfg[f] for f in ("model_construction_authorized", "optimization_authorized",
                             "execution_integration_authorized"))):
        raise RuntimeError("P2X_V2_CONFIG_CONTRACT_FAIL")
    source = csv(ROOT / cfg["source_transcription"]).set_index("zone").loc[list(ZONES)]
    displayed = np.asarray(NUMERATORS, dtype=float) / 10
    if (not np.array_equal(source.DE_IT_2040_GW, displayed) or
        not np.array_equal(source.GA_IT_2040_GW, displayed)):
        raise RuntimeError("P2X_V2_SOURCE_TRANSCRIPTION_CONFLICT")
    return pd.DataFrame({
        "zone": ZONES, "source_capacity_GW_displayed": displayed,
        "share_numerator": NUMERATORS, "share_denominator": 74,
        "p2x_spatial_share": np.asarray(NUMERATORS, dtype=float) / 74,
        "source_id": "TERNA_SNAM_DDS_2024",
        "source_locator": "Figure 30; printed page 62; PDF page 64; 2040 DE-IT/GA-IT",
        "spatial_status": "ACCEPTED_P2X_SPATIAL_V2",
        "application_scope": "COMMON_ALL_SIX_MEM_CASES; SAME_SHARE_FOR_MW_AND_MWH"})


def verify_key(table):
    pd.testing.assert_frame_equal(table, key_table(), check_dtype=False, check_exact=True)
    share = table.p2x_spatial_share.to_numpy()
    assert np.isfinite(share).all() and (share >= 0).all()
    assert abs(share.sum() - 1) <= 1e-15


def slow_corroboration():
    source = csv(DERIVATION / "terna_zonal_electrolyser_authority.csv").set_index("zone").loc[list(ZONES)]
    values = source.PNIEC_Slow_2040_GW_displayed.to_numpy()
    assert np.array_equal(values, np.array([.7, .2, .4, 2.3, .5, .9, .2]))
    share = values / values.sum()
    delta = 100 * (share - np.asarray(NUMERATORS) / 74)
    assert abs(values.sum() - 5.2) < 1e-14
    assert np.max(np.abs(delta)) < 1 and ZONES[int(np.argmax(np.abs(delta)))] == "SUD"
    return pd.DataFrame({"zone": ZONES, "PNIEC_Slow_GW_displayed": values,
        "PNIEC_Slow_normalized_displayed_share": share, "accepted_DE_GA_share": np.asarray(NUMERATORS) / 74,
        "difference_pp": delta, "status": "TERNA_2040_SPATIAL_PATTERN_CROSS_SCENARIO_CORROBORATED",
        "displayed_sum_GW_not_official_headline": 5.2, "official_national_headline_GW": 5.3})


def protected_groups():
    groups = {
        "P2X_V1_CONFIG": [ROOT / "config/stage_b_p2x_flex_v1.yaml", ROOT / "src/mem_model/stage_b_p2x_flex.py"],
        "FINAL_V1_CONFIG": [ROOT / "config/final_methodology_execution_v1.yaml"]}
    for name, folder in {
        "P2X_V1_RUNTIME": "runtime_inputs/p2x_flex_v1", "P2X_V1_NETWORKS": "networks/unsolved/p2x_flex_v1",
        "P2X_V1_QA": "qa/stage_b/p2x_flex_v1", "FINAL_V1_INPUTS": "networks/unsolved/final_methodology_v1",
        "FINAL_V1_CLOSURE": "qa/final_methodology_closure"}.items():
        groups[name] = [p for p in (ROOT / folder).rglob("*") if p.is_file()]
    completed = []
    for p in (ROOT / "results/final_methodology_v1").rglob("*SOLVE_RECEIPT.json"):
        receipt = read_json(p)
        if receipt.get("status") == "PASS" and receipt.get("termination_condition") == "optimal":
            completed.extend([p, ROOT / receipt["solved_path"]])
    groups["FINAL_V1_COMPLETED_SOLVES"] = completed
    result = {}
    for name, paths in groups.items():
        rows = [(relative(p), sha256_file(p)) for p in sorted(set(paths), key=str)]
        result[name] = {"files": len(rows), "sha256": hashlib.sha256(
            json.dumps(rows, separators=(",", ":")).encode()).hexdigest()}
    return result


def verify_protected(expected=None):
    expected = expected or read_json(DERIVATION / "QA.json")["protection"]["current_groups"]
    observed = protected_groups()
    if observed != expected:
        raise RuntimeError("PROTECTED_V1_HASH_CONFLICT")
    return observed


def parent_records():
    records = csv(FINAL_AUTHORITY / "FINAL_NETWORK_LINEAGE_AND_HASHES.csv")
    matrix = csv(FINAL_AUTHORITY / "EXACT_PRESERVATION_MATRIX.csv")
    p2x = matrix.loc[matrix.object == "P2X"]
    assert len(records) == len(p2x) == 6
    assert set(zip(records.year, records.scenario)) == set(SCENARIOS)
    assert set(zip(p2x.year, p2x.scenario)) == set(SCENARIOS)
    assert p2x.status.eq("EXACT_PRESERVED").all()
    packages = read_json(FINAL_AUTHORITY / "FINAL_NETWORK_PREPARATION_RECEIPT.json")["packages"]
    for row in records.to_dict("records"):
        package = next(p for p in packages if (p["year"], p["scenario"]) == (row["year"], row["scenario"]))
        for field in ("path", "sha256", "reference_path", "reference_sha256", "parent", "parent_sha256",
                      "continuous_operating_bound_authority", "continuous_operating_bound_authority_sha256"):
            assert row[field] == package[field]
        for field, hash_field in (("path", "sha256"), ("reference_path", "reference_sha256"),
            ("continuous_operating_bound_authority", "continuous_operating_bound_authority_sha256")):
            assert sha256_file(ROOT / row[field]) == row[hash_field]
    return records.to_dict("records")


def assert_p2x_structure(n):
    ids = [f"P2X_{z}" for z in ZONES]
    constraints = [f"P2X_ANNUAL_{z}" for z in ZONES]
    assert set(n.generators.index[n.generators.index.str.startswith("P2X_")]) == set(ids)
    assert set(n.global_constraints.index[n.global_constraints.index.str.startswith("P2X_ANNUAL_")]) == set(constraints)
    g, c = n.generators.loc[ids], n.global_constraints.loc[constraints]
    assert g.bus.to_list() == list(ZONES)
    for field, value in {"sign": -1, "p_nom_extendable": False, "committable": False,
                         "p_min_pu": 0, "p_max_pu": 1, "marginal_cost": 0}.items():
        assert g[field].eq(value).all(), field
    assert c.type.eq("operational_limit").all() and c.sense.eq("==").all()
    assert c.carrier_attribute.to_list() == g.carrier.to_list()
    assert len(n.snapshots) == 8760


def direct_parent_gate(row, authority, uc, reference):
    for n in (authority, uc, reference):
        assert_p2x_structure(n)
        assert n.meta["year"] == row["year"] and n.meta["scenario"] == row["scenario"]
    for n in (uc, reference):
        for zone in ZONES:
            assert n.generators.at[f"P2X_{zone}", "p_nom"] == authority.generators.at[f"P2X_{zone}", "p_nom"]
            assert n.global_constraints.at[f"P2X_ANNUAL_{zone}", "constant"] == authority.global_constraints.at[f"P2X_ANNUAL_{zone}", "constant"]


def contract_for_case(year, scenario, old, key):
    if (year, scenario) not in SCENARIOS:
        raise ValueError("P2X_V2_UNKNOWN_CASE")
    old = old.loc[(old.year == year) & (old.scenario == scenario)].set_index("zone").loc[list(ZONES)]
    totals = scenario_totals(year, scenario)
    share = key.set_index("zone").loc[list(ZONES), "p2x_spatial_share"].to_numpy()
    # Keep rigid CSV values exactly; V1 stores its accepted controls at 15 significant digits.
    rigid = old.annual_rigid_MWh.to_numpy()
    energy = float(totals["p2x_TWh"]) * 1e6 * share
    power = float(totals["p2x_power_MW"]) * share
    authority = load_yaml(CFG)[f"spatial_authority_{year}"]
    return pd.DataFrame({"year": year, "scenario": scenario, "zone": ZONES,
        "rigid_demand_share": old.frozen_2025_share.to_numpy(), "p2x_spatial_share": share,
        "annual_rigid_MWh": rigid, "annual_p2x_MWh": energy, "annual_total_MWh": rigid + energy,
        "p2x_power_MW": power, "equivalent_full_load_hours": energy / power,
        "p2x_power_authority": totals["power_authority"], "p2x_spatial_authority": authority,
        "formulation": "NEGATIVE_SIGN_GENERATOR|OPERATIONAL_LIMIT_EQUALITY"})


def numerical_qa(contract):
    rows = []
    for year, scenario in SCENARIOS:
        c = contract.loc[(contract.year == year) & (contract.scenario == scenario)].set_index("zone").loc[list(ZONES)]
        old = csv(ROOT / f"runtime_inputs/p2x_flex_v1/{year}/p2x_contract.csv")
        old = old.loc[old.scenario == scenario].set_index("zone").loc[list(ZONES)]
        t = scenario_totals(year, scenario)
        assert np.array_equal(c.annual_rigid_MWh, old.annual_rigid_MWh)
        assert np.array_equal(c.rigid_demand_share, old.frozen_2025_share)
        assert np.array_equal(c.p2x_spatial_share, np.asarray(NUMERATORS) / 74)
        assert np.allclose(c.annual_rigid_MWh, float(t["rigid_TWh"]) * 1e6 * c.rigid_demand_share, rtol=0, atol=1e-6)
        assert np.array_equal(c.annual_total_MWh, c.annual_rigid_MWh + c.annual_p2x_MWh)
        assert np.allclose(c.annual_p2x_MWh / c.p2x_power_MW, float(t["equivalent_full_load_hours"]), rtol=0, atol=1e-10)
        values = {"rigid_MWh": c.annual_rigid_MWh.sum(), "P2X_MWh": c.annual_p2x_MWh.sum(),
                  "total_MWh": c.annual_total_MWh.sum(), "P2X_MW": c.p2x_power_MW.sum()}
        expected = {"rigid_MWh": float(t["rigid_TWh"]) * 1e6, "P2X_MWh": float(t["p2x_TWh"]) * 1e6,
                    "total_MWh": float(t["total_TWh"]) * 1e6, "P2X_MW": float(t["p2x_power_MW"])}
        assert all(abs(values[k] - expected[k]) <= 1e-6 for k in values)
        rows.append({"year": year, "scenario": scenario, **values,
                     "EFLH": float(t["equivalent_full_load_hours"]),
                     "maximum_national_absolute_difference": max(abs(values[k]-expected[k]) for k in values), "status": "PASS"})
    return pd.DataFrame(rows)


def successor(parent, contract, parent_path, network_type):
    assert_p2x_structure(parent)
    n = parent.copy()
    c = contract.set_index("zone").loc[list(ZONES)]
    n.generators.loc[[f"P2X_{z}" for z in ZONES], "p_nom"] = c.p2x_power_MW.to_numpy()
    n.global_constraints.loc[[f"P2X_ANNUAL_{z}" for z in ZONES], "constant"] = c.annual_p2x_MWh.to_numpy()
    if META_KEY in n.meta:
        raise RuntimeError("P2X_V2_PARENT_ALREADY_TRANSFORMED")
    n.meta[META_KEY] = {
        "version": VERSION, "variant": "final_v2" if network_type != "P2X_FLEX" else "p2x_flex_v2",
        "network_type": network_type, "parent_path": relative(parent_path), "parent_sha256": sha256_file(parent_path),
        "canonical_key_path": relative(KEY), "canonical_key_sha256": sha256_file(KEY),
        "spatial_authority": c.p2x_spatial_authority.iloc[0],
        "annual_energy_allocation": "ACCEPTED_MEM_SAME_SHARE_FOR_MW_AND_MWH_CLOSURE_ASSUMPTION",
        "scope_2050": "ELECTROLYSER_DERIVED_SPATIAL_PROXY_FOR_BROADER_P2X",
        "inherited_metadata_role": "HISTORICAL_PARENT_PROVENANCE_NOT_SUCCESSOR_EXECUTION_AUTHORITY",
        "execution_enabled": False, "optimization_model_constructed": False, "optimizer_invocations": 0}
    return n


def exact_diff(parent, child):
    """Exhaustive native component comparison; no tolerance for unchanged values."""
    def frame(a, b):
        pd.testing.assert_frame_equal(a, b, check_dtype=False, check_exact=True, check_freq=False)
    try:
        pd.testing.assert_index_equal(parent.snapshots, child.snapshots)
        frame(parent.snapshot_weightings, child.snapshot_weightings)
        pd.testing.assert_index_equal(parent.investment_periods, child.investment_periods)
        frame(parent.investment_period_weightings, child.investment_period_weightings)
        assert parent.name == child.name and parent.srid == child.srid
        assert set(parent.components.keys()) == set(child.components.keys())
        static_count = dynamic_count = 0
        for component in parent.components.values():
            other = child.components[component.name]
            a, b = component.static, other.static.copy()
            if component.name == "Generator":
                ids = [f"P2X_{z}" for z in ZONES]
                b.loc[ids, "p_nom"] = a.loc[ids, "p_nom"]
            elif component.name == "GlobalConstraint":
                ids = [f"P2X_ANNUAL_{z}" for z in ZONES]
                b.loc[ids, "constant"] = a.loc[ids, "constant"]
            frame(a, b)
            static_count += 1
            assert set(component.dynamic) == set(other.dynamic)
            for key in component.dynamic:
                frame(component.dynamic[key], other.dynamic[key])
                dynamic_count += 1
        assert set(child.meta) == set(parent.meta) | {META_KEY}
        assert {k: v for k, v in child.meta.items() if k != META_KEY} == parent.meta
        assert child.meta[META_KEY]["execution_enabled"] is False
    except (AssertionError, KeyError, ValueError) as error:
        raise RuntimeError("UNEXPECTED_SUCCESSOR_DRIFT") from error
    return {"static_component_tables_exact_except_whitelist": static_count,
            "dynamic_component_tables_exact": dynamic_count, "unexpected_changed_model_fields": 0,
            "authorized_model_field_cells": 14, "metadata_added_key": META_KEY, "status": "PASS"}


def verify_network_values(n, contract):
    assert_p2x_structure(n)
    c = contract.set_index("zone").loc[list(ZONES)]
    assert np.array_equal(n.generators.loc[[f"P2X_{z}" for z in ZONES], "p_nom"], c.p2x_power_MW)
    assert np.array_equal(n.global_constraints.loc[[f"P2X_ANNUAL_{z}" for z in ZONES], "constant"], c.annual_p2x_MWh)


def verify_config_lock():
    cfg = load_yaml(FINAL_CFG)
    old = load_yaml(ROOT / "config/final_methodology_execution_v1.yaml")
    assert cfg["variant"] == "final_v2" and cfg["no_automatic_execution"] is True
    assert cfg["schema_version"] == "MEM_FINAL_METHODOLOGY_EXECUTION_V2"
    assert cfg["result_root"] == "results/final_methodology_v2"
    assert cfg["execution_path_integrated"] is False
    assert cfg["manual_authorization"] == {f"{y}_{s}": False for y, s in SCENARIOS}
    assert cfg["solver"] == old["solver"] and cfg["water_values"] == old["water_values"]
    # Inspect the accepted registry without importing its solver execution module.
    source = (ROOT / "src/mem_model/stage_b_uc2.py").read_text(encoding="utf-8")
    assert 'choices=["final_v1"]' in source and 'if variant not in (None,"final_v1"):' in source
    assert not (ROOT / cfg["result_root"]).exists()


def prepare():
    with no_models_or_solves():
        protection = verify_protected()
        records = parent_records()
        verify_config_lock()
        if KEY.exists() or any(p.exists() for p in (RUNTIME, NETWORKS, FINAL_NETWORKS, QA)):
            raise RuntimeError("P2X_V2_OUTPUT_ALREADY_EXISTS_NO_OVERWRITE")
        # Verify every parent before creating any output artifact.
        for row in records:
            direct_parent_gate(row, pypsa.Network(ROOT / row["continuous_operating_bound_authority"]),
                pypsa.Network(ROOT / row["path"]), pypsa.Network(ROOT / row["reference_path"]))
        key = key_table()
        KEY.parent.mkdir(parents=True, exist_ok=True)
        key.to_csv(KEY, index=False)
        verify_key(csv(KEY))
        QA.mkdir(parents=True)
        slow_corroboration().to_csv(QA / "TERNA_PNIEC_SLOW_SPATIAL_CORROBORATION.csv", index=False)
        contracts, manifests = [], {}
        for year in (2040, 2050):
            parent = ROOT / f"runtime_inputs/p2x_flex_v1/{year}"
            target = RUNTIME / str(year)
            target.mkdir(parents=True)
            manifest_rows = []
            for member in COPIED_MEMBERS:
                shutil.copyfile(parent / member, target / member)
                assert sha256_file(parent / member) == sha256_file(target / member)
                manifest_rows.append({"file": member, "sha256": sha256_file(target / member),
                    "authority": "BYTE_IDENTICAL_P2X_FLEX_V1_PARENT", "parent_path": relative(parent / member),
                    "parent_sha256": sha256_file(parent / member)})
            old = csv(parent / "p2x_contract.csv")
            contract = pd.concat([contract_for_case(year, s, old, key) for s in ("Slow", "Base", "High")], ignore_index=True)
            contract.to_csv(target / "p2x_contract.csv", index=False)
            contracts.append(csv(target / "p2x_contract.csv"))
            manifest_rows.append({"file": "p2x_contract.csv", "sha256": sha256_file(target / "p2x_contract.csv"),
                "authority": "P2X_FLEX_V2_DERIVED", "parent_path": relative(parent / "p2x_contract.csv"),
                "parent_sha256": sha256_file(parent / "p2x_contract.csv")})
            pd.DataFrame(manifest_rows).to_csv(target / MANIFEST, index=False)
            manifests[str(year)] = {"path": relative(target / MANIFEST), "sha256": sha256_file(target / MANIFEST)}
        contract = pd.concat(contracts, ignore_index=True)
        numerical_qa(contract).to_csv(QA / "NATIONAL_DEMAND_RECONCILIATION.csv", index=False)
        lineage, diff_rows, comparison = [], [], []
        NETWORKS.mkdir(parents=True)
        FINAL_NETWORKS.mkdir(parents=True)
        for row in records:
            year, scenario = row["year"], row["scenario"]
            c = contract.loc[(contract.year == year) & (contract.scenario == scenario)]
            old = csv(ROOT / f"runtime_inputs/p2x_flex_v1/{year}/p2x_contract.csv")
            if scenario == "Base":
                a = old.loc[old.scenario == scenario].set_index("zone").loc[list(ZONES)]
                b = c.set_index("zone").loc[list(ZONES)]
                comparison.append(pd.DataFrame({"year": year, "scenario": scenario, "zone": ZONES,
                    "rigid_MWh_V1": a.annual_rigid_MWh.to_numpy(), "rigid_MWh_V2": b.annual_rigid_MWh.to_numpy(),
                    "P2X_MWh_V1": a.annual_p2x_MWh.to_numpy(), "P2X_MWh_V2": b.annual_p2x_MWh.to_numpy(),
                    "P2X_MW_V1": a.p2x_power_MW.to_numpy(), "P2X_MW_V2": b.p2x_power_MW.to_numpy(),
                    "total_MWh_V1": a.annual_total_MWh.to_numpy(), "total_MWh_V2": b.annual_total_MWh.to_numpy()}))
            for kind, field in (("P2X_FLEX", "continuous_operating_bound_authority"),
                                ("UC_INPUT", "path"), ("CONTINUOUS_REFERENCE", "reference_path")):
                parent_path = ROOT / row[field]
                parent = pypsa.Network(parent_path)
                child = successor(parent, c, parent_path, kind)
                root = NETWORKS if kind == "P2X_FLEX" else FINAL_NETWORKS
                suffix = "" if kind != "CONTINUOUS_REFERENCE" else "_CONTINUOUS_REFERENCE"
                version = VERSION if kind == "P2X_FLEX" else "FINAL_METHODOLOGY_V2"
                path = root / f"MEM_{year}_{scenario.upper()}_{version}{suffix}_8760h_UNSOLVED.nc"
                child.export_to_netcdf(path)
                loaded = pypsa.Network(path)
                verify_network_values(loaded, c)
                diff = exact_diff(parent, loaded)
                diff_rows.append({"year": year, "scenario": scenario, "network_type": kind, **diff})
                lineage.append({"year": year, "scenario": scenario, "network_type": kind,
                    "final_v1_parent_path" if kind != "P2X_FLEX" else "p2x_flex_v1_parent_path": relative(parent_path),
                    "final_v1_parent_sha256" if kind != "P2X_FLEX" else "p2x_flex_v1_parent_sha256": sha256_file(parent_path),
                    "canonical_p2x_key_path": relative(KEY), "canonical_p2x_key_sha256": sha256_file(KEY),
                    "final_v2_path" if kind != "P2X_FLEX" else "p2x_flex_v2_path": relative(path),
                    "final_v2_sha256" if kind != "P2X_FLEX" else "p2x_flex_v2_sha256": sha256_file(path),
                    "authorized_changes": "|".join(ALLOWED_CHANGES), "status": "PASS"})
        pd.DataFrame([r for r in lineage if r["network_type"] != "P2X_FLEX"]).to_csv(QA / "FINAL_V2_NETWORK_LINEAGE_AND_HASHES.csv", index=False)
        pd.DataFrame([r for r in lineage if r["network_type"] == "P2X_FLEX"]).to_csv(QA / "P2X_FLEX_V2_NETWORK_LINEAGE_AND_HASHES.csv", index=False)
        pd.DataFrame(diff_rows).to_csv(QA / "EXACT_SUCCESSOR_DIFF_QA.csv", index=False)
        pd.concat(comparison, ignore_index=True).to_csv(QA / "BASE_ALLOCATION_COMPARISON.csv", index=False)
        receipt = {"phase": "P2X-SPATIAL-V2C", "status": "PASS", "state": "MATERIALIZED_EXECUTION_LOCKED",
            "canonical_key_path": relative(KEY), "canonical_key_sha256": sha256_file(KEY),
            "runtime_manifests": manifests, "networks": lineage, "protected_groups": protection,
            "direct_successor_gate": {"status": "PASS", "cases": 6, "P2X_scalar_equalities": 168},
            "allowed_model_fields": ["generators[P2X_<zone>].p_nom", "global_constraints[P2X_ANNUAL_<zone>].constant"],
            "allowed_metadata_fields": [META_KEY], "unexpected_changed_model_fields": 0,
            "final_v2_materialized": True, "final_v2_execution_enabled": False,
            "optimization_model_constructed": False, "optimizer_invocations": 0}
        verify_protected(protection)
        dump_json(QA / RECEIPT, receipt)
        return receipt


def verify(test_results=None):
    with no_models_or_solves():
        receipt = read_json(QA / RECEIPT)
        verify_key(csv(KEY))
        verify_config_lock()
        assert sha256_file(KEY) == receipt["canonical_key_sha256"]
        verify_protected(receipt["protected_groups"])
        contracts = []
        for year in (2040, 2050):
            target = RUNTIME / str(year)
            manifest = csv(target / MANIFEST)
            assert len(manifest) == 6 and manifest.file.nunique() == 6
            assert sha256_file(target / MANIFEST) == receipt["runtime_manifests"][str(year)]["sha256"]
            for row in manifest.to_dict("records"):
                assert sha256_file(target / row["file"]) == row["sha256"]
                assert sha256_file(ROOT / row["parent_path"]) == row["parent_sha256"]
                if row["file"] in COPIED_MEMBERS:
                    assert row["authority"] == "BYTE_IDENTICAL_P2X_FLEX_V1_PARENT" and row["sha256"] == row["parent_sha256"]
                else:
                    assert row["authority"] == "P2X_FLEX_V2_DERIVED"
            contracts.append(csv(target / "p2x_contract.csv"))
        contract = pd.concat(contracts, ignore_index=True)
        numerical_qa(contract)
        for r in receipt["networks"]:
            final = r["network_type"] != "P2X_FLEX"
            parent_path = ROOT / r["final_v1_parent_path" if final else "p2x_flex_v1_parent_path"]
            path = ROOT / r["final_v2_path" if final else "p2x_flex_v2_path"]
            assert sha256_file(parent_path) == r["final_v1_parent_sha256" if final else "p2x_flex_v1_parent_sha256"]
            assert sha256_file(path) == r["final_v2_sha256" if final else "p2x_flex_v2_sha256"]
            parent, n = pypsa.Network(parent_path), pypsa.Network(path)
            exact_diff(parent, n)
            c = contract.loc[(contract.year == r["year"]) & (contract.scenario == r["scenario"])]
            verify_network_values(n, c)
        # Each exact parent->child diff proves preservation of every old UC/reference
        # difference. Independently compare all 14 P2X fields in each new pair.
        for year, scenario in SCENARIOS:
            pair = [r for r in receipt["networks"] if (r["year"], r["scenario"]) == (year, scenario) and r["network_type"] != "P2X_FLEX"]
            a, b = (pypsa.Network(ROOT / r["final_v2_path"]) for r in pair)
            for zone in ZONES:
                assert a.generators.at[f"P2X_{zone}", "p_nom"] == b.generators.at[f"P2X_{zone}", "p_nom"]
                assert a.global_constraints.at[f"P2X_ANNUAL_{zone}", "constant"] == b.global_constraints.at[f"P2X_ANNUAL_{zone}", "constant"]
        tests = None
        if test_results:
            suites = ET.parse(test_results).getroot()
            totals = {k: sum(int(s.get(k, 0)) for s in suites.iter("testsuite")) for k in ("tests", "failures", "errors", "skipped")}
            assert totals["tests"] > 0 and totals["failures"] == totals["errors"] == totals["skipped"] == 0
            tests = {**totals, "path": relative(test_results), "sha256": sha256_file(test_results)}
        result = {"phase": "P2X-SPATIAL-V2C", "status": "PASS", "networks_export_reload_verified": 18,
            "P2X_FLEX_V2_networks": 6, "final_v2_UC_inputs": 6, "final_v2_continuous_references": 6,
            "UC_reference_P2X_scalar_equalities": 84, "non_P2X_parent_pair_differences_preserved": True,
            "unexpected_changed_model_fields": 0, "protected_V1": "PASS", "focused_tests": tests,
            "PNIEC_Slow_max_abs_difference_pp": float(slow_corroboration().difference_pp.abs().max()),
            "source_robustness": "TERNA_2040_SPATIAL_PATTERN_CROSS_SCENARIO_CORROBORATED",
            "final_v2_materialized": True, "final_v2_execution_enabled": False,
            "normal_production_registry_unchanged_and_excludes_final_v2": True,
            "optimization_model_constructed": False, "optimizer_invocations": 0}
        dump_json(QA / VERIFICATION, result)
        paths = [KEY, CFG, FINAL_CFG, Path(__file__), ROOT / "tests/test_stage_b_p2x_flex_v2.py"]
        paths += [p for p in QA.rglob("*") if p.is_file() and p.name != "MEM_STAGE_B_P2X_FLEX_V2_ARTIFACT_MANIFEST.csv"]
        paths += [p for p in RUNTIME.rglob("*") if p.is_file()]
        paths += list(NETWORKS.glob("*.nc")) + list(FINAL_NETWORKS.glob("*.nc"))
        pd.DataFrame([{"path": relative(p), "sha256": sha256_file(p)} for p in sorted(set(paths), key=str)]).to_csv(
            QA / "MEM_STAGE_B_P2X_FLEX_V2_ARTIFACT_MANIFEST.csv", index=False)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "verify"])
    parser.add_argument("--test-results", type=Path)
    args = parser.parse_args(argv)
    if args.action == "prepare":
        if args.test_results:
            parser.error("--test-results belongs only to non-solving verification")
        result = prepare()
        print(json.dumps({k: result[k] for k in ("state", "final_v2_materialized", "final_v2_execution_enabled", "optimizer_invocations")}, indent=2))
    else:
        print(json.dumps(verify(args.test_results), indent=2))


if __name__ == "__main__":
    main()

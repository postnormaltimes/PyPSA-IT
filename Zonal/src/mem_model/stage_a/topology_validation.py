"""Validation for the deterministic ETX-7B5 topology input contract."""

from __future__ import annotations

import argparse
import ast
import json
import math
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from mem_model.stage_a.topology import (
    CONFIG_PATH,
    DEFAULT_OUTPUT_DIR,
    HORIZONS,
    MARKETS,
    OUTPUT_FILES,
    ROOT,
    build_topology_package,
    load_config,
    sha256_file,
    validate_config,
)


QA_DIR = ROOT / "qa" / "stage_a" / "etx7b5"


def _read(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _rows(path: Path) -> int:
    return len(_read(path))


def _add(
    rows: list[dict[str, Any]],
    check_id: str,
    description: str,
    observed: object,
    expected: object,
    passed: bool,
    notes: str = "",
) -> None:
    difference: object = ""
    try:
        difference = float(observed) - float(expected)
    except (TypeError, ValueError):
        pass
    rows.append(
        {
            "check_id": check_id,
            "description": description,
            "observed": observed,
            "expected": expected,
            "difference": difference,
            "status": "PASS" if bool(passed) else "FAIL",
            "notes": notes,
        }
    )


def _contains_forbidden_runtime_calls(paths: tuple[Path, ...]) -> bool:
    """Return whether B5 code imports solver stacks or invokes solve methods."""

    forbidden_modules = {"pypsa", "gurobipy"}
    forbidden_methods = {"optimize", "lopf"}
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name.split(".", 1)[0] in forbidden_modules for alias in node.names):
                    return True
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".", 1)[0] in forbidden_modules:
                    return True
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in forbidden_methods:
                    return True
    return False


def _verify_manifest_members(manifest_path: Path, family: str) -> pd.DataFrame:
    manifest = _read(manifest_path)
    rows: list[dict[str, Any]] = []
    for record in manifest.to_dict(orient="records"):
        relative = Path(record["relative_path"].replace("\\", "/"))
        root_candidate = ROOT / relative
        path = root_candidate if root_candidate.exists() else manifest_path.parent / relative
        expected_hash = str(record.get("expected_sha256") or record.get("sha256")).upper()
        expected_rows = int(record.get("expected_rows") or record.get("rows"))
        expected_bytes = int(record["bytes"]) if record.get("bytes") else None
        exists = path.exists()
        observed_hash = sha256_file(path) if exists else ""
        observed_rows = _rows(path) if exists else ""
        observed_bytes = path.stat().st_size if exists else ""
        passed = (
            exists
            and observed_hash == expected_hash
            and int(observed_rows) == expected_rows
            and (expected_bytes is None or int(observed_bytes) == expected_bytes)
        )
        rows.append(
            {
                "receipt_family": family,
                "receipt_type": "MANIFEST_MEMBER",
                "artifact": record.get("artifact", path.name),
                "relative_path": relative.as_posix(),
                "expected_bytes": "" if expected_bytes is None else expected_bytes,
                "observed_bytes": observed_bytes,
                "expected_rows": expected_rows,
                "observed_rows": observed_rows,
                "expected_sha256": expected_hash,
                "observed_sha256": observed_hash,
                "status": "PASS" if passed else "FAIL",
            }
        )
    return pd.DataFrame.from_records(rows)


def predecessor_receipts(config: dict[str, Any]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for key, lock in config["predecessor_locks"].items():
        path = ROOT / lock["path"]
        observed_hash = sha256_file(path) if path.exists() else ""
        lock_row = {
            "receipt_family": key.upper(),
            "receipt_type": "LOCK_FILE",
            "artifact": path.name,
            "relative_path": lock["path"],
            "expected_bytes": "",
            "observed_bytes": path.stat().st_size if path.exists() else "",
            "expected_rows": "",
            "observed_rows": "",
            "expected_sha256": lock["sha256"],
            "observed_sha256": observed_hash,
            "status": "PASS" if path.exists() and observed_hash == lock["sha256"] else "FAIL",
        }
        frames.append(pd.DataFrame.from_records([lock_row]))
        if key.endswith("_manifest"):
            frames.append(_verify_manifest_members(path, key.upper()))
    return pd.concat(frames, ignore_index=True)


def output_receipts(output_dir: Path) -> pd.DataFrame:
    manifest = _read(output_dir / OUTPUT_FILES["manifest"])
    rows = []
    for record in manifest.to_dict(orient="records"):
        path = output_dir / Path(record["relative_path"]).name
        exists = path.exists()
        observed_hash = sha256_file(path) if exists else ""
        observed_rows = _rows(path) if exists else ""
        observed_bytes = path.stat().st_size if exists else ""
        passed = (
            exists
            and int(observed_bytes) == int(record["bytes"])
            and int(observed_rows) == int(record["rows"])
            and observed_hash == record["sha256"]
        )
        rows.append(
            {
                "artifact": record["artifact"],
                "relative_path": record["relative_path"],
                "expected_bytes": record["bytes"],
                "observed_bytes": observed_bytes,
                "expected_rows": record["rows"],
                "observed_rows": observed_rows,
                "expected_sha256": record["sha256"],
                "observed_sha256": observed_hash,
                "status": "PASS" if passed else "FAIL",
            }
        )
    return pd.DataFrame.from_records(rows)


def deterministic_receipt(output_dir: Path) -> pd.DataFrame:
    with tempfile.TemporaryDirectory(prefix="etx7b5_rebuild_") as temporary:
        rebuilt = Path(temporary) / "topology"
        build_topology_package(rebuilt)
        rows = []
        for filename in OUTPUT_FILES.values():
            accepted = output_dir / filename
            candidate = rebuilt / filename
            same = accepted.read_bytes() == candidate.read_bytes()
            rows.append(
                {
                    "artifact": filename,
                    "accepted_sha256": sha256_file(accepted),
                    "rebuilt_sha256": sha256_file(candidate),
                    "byte_identical": same,
                    "status": "PASS" if same else "FAIL",
                }
            )
    return pd.DataFrame.from_records(rows)


def _pytest_receipt(filename: str) -> dict[str, Any]:
    path = QA_DIR / filename
    if not path.exists():
        return {"status": "NOT_RUN", "path": path.relative_to(ROOT).as_posix()}
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return {"status": "INVALID_JUNIT", "path": path.relative_to(ROOT).as_posix()}
    tests = int(suite.attrib.get("tests", 0))
    failures = int(suite.attrib.get("failures", 0))
    errors = int(suite.attrib.get("errors", 0))
    return {
        "status": "PASS" if failures == 0 and errors == 0 else "FAIL",
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": int(suite.attrib.get("skipped", 0)),
        "seconds": float(suite.attrib.get("time", 0)),
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": sha256_file(path),
    }


def _expected_capacity_map(config: dict[str, Any]) -> dict[tuple[int, str, str], float]:
    expected: dict[tuple[int, str, str], float] = {}
    for interface in config["italy_interfaces"]:
        for year in HORIZONS:
            expected[(year, interface["external_market"], "IT")] = float(interface["external_to_it_MW"])
            expected[(year, "IT", interface["external_market"])] = float(interface["it_to_external_MW"])
    for connection in config["external_connections"]:
        for year in HORIZONS:
            expected[(year, connection["endpoint_a"], connection["endpoint_b"])] = float(connection["capacity_2040_a_to_b_MW"])
            expected[(year, connection["endpoint_b"], connection["endpoint_a"])] = float(connection["capacity_2040_b_to_a_MW"])
    return expected


def _source_provenance_ok(provenance: pd.DataFrame) -> bool:
    for record in provenance.to_dict(orient="records"):
        path = Path(record["path"])
        path = path if path.is_absolute() else ROOT / path
        if not path.exists() and len(str(path.resolve())) < 240:
            return False
        try:
            if sha256_file(path) != record["sha256"]:
                return False
        except OSError:
            return False
    return True


def validate_topology_package(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    write_reports: bool = True,
    run_deterministic_rebuild: bool = True,
) -> dict[str, Any]:
    config = load_config(CONFIG_PATH)
    validate_config(config)
    nodes = _read(output_dir / OUTPUT_FILES["nodes"])
    interfaces = _read(output_dir / OUTPUT_FILES["interfaces"])
    links = _read(output_dir / OUTPUT_FILES["links"])
    cap_2040 = _read(output_dir / OUTPUT_FILES["capacities_2040"])
    cap_2050 = _read(output_dir / OUTPUT_FILES["capacities_2050"])
    authority = _read(output_dir / OUTPUT_FILES["authority_matrix"])
    provenance = _read(output_dir / OUTPUT_FILES["provenance"])
    carry = _read(output_dir / OUTPUT_FILES["carry_forward"])
    no_links = _read(output_dir / OUTPUT_FILES["no_links"])
    excluded = _read(output_dir / OUTPUT_FILES["excluded_markets"])
    predecessors = predecessor_receipts(config)
    outputs = output_receipts(output_dir)
    deterministic = (
        deterministic_receipt(output_dir)
        if run_deterministic_rebuild
        else pd.DataFrame([{"artifact": "SKIPPED", "status": "SKIPPED"}])
    )

    links["year_int"] = pd.to_numeric(links["year"], errors="coerce")
    links["capacity_num"] = pd.to_numeric(links["capacity_MW"], errors="coerce")
    checks: list[dict[str, Any]] = []

    pred_ok = predecessors["status"].eq("PASS").all()
    _add(checks, "B5-QA-01", "Stage-B and B1-B4 predecessor locks and manifest members remain byte-identical", int(predecessors.status.eq("PASS").sum()), len(predecessors), pred_ok)

    exact_nodes = len(nodes) == 10 and nodes["market"].is_unique and set(nodes["market"]) == set(MARKETS)
    _add(checks, "B5-QA-02", "Stage-A node registry is exactly the accepted ten-market set", f"count={len(nodes)}; markets={sorted(nodes.market)}", f"count=10; markets={list(MARKETS)}", exact_nodes)
    excluded_scope = set(nodes["market"]).isdisjoint(set(config["excluded_markets"]))
    _add(checks, "B5-QA-03", "No CORS or cancelled wider-perimeter market is a Stage-A node", excluded_scope, True, excluded_scope)
    node_fixed = nodes["capacity_expansion_allowed"].str.lower().eq("false").all()
    _add(checks, "B5-QA-04", "All Stage-A nodes remain fixed-capacity dispatch-only identities", node_fixed, True, node_fixed)

    link_scope = set(links["from_market"]) | set(links["to_market"])
    scope_ok = link_scope == set(MARKETS) and set(links["year_int"].dropna().astype(int)) == set(HORIZONS)
    _add(checks, "B5-QA-05", "Directional links use only the ten accepted markets and two horizons", f"markets={sorted(link_scope)}; years={sorted(set(links.year_int.dropna().astype(int)))}", "exact ten markets; 2040/2050", scope_ok)
    counts_ok = len(links) == 48 and links["year_int"].value_counts().to_dict() == {2040: 24, 2050: 24}
    _add(checks, "B5-QA-06", "Directional registry contains 24 fixed rows per horizon", links["year_int"].value_counts().sort_index().to_dict(), {2040: 24, 2050: 24}, counts_ok)
    family_counts = links.groupby(["year_int", "link_family"]).size().to_dict()
    expected_family_counts = {(2040, "ITALY_FACING"): 16, (2050, "ITALY_FACING"): 16, (2040, "EXTERNAL_EXTERNAL"): 8, (2050, "EXTERNAL_EXTERNAL"): 8}
    _add(checks, "B5-QA-07", "Italy-facing and external-external row counts are complete", family_counts, expected_family_counts, family_counts == expected_family_counts)

    interface_ok = len(interfaces) == 12 and interfaces["physical_link_id"].is_unique and links["physical_link_id"].nunique() == 12
    _add(checks, "B5-QA-08", "Exactly 12 unique physical interface identities cover all directional rows", f"registry={len(interfaces)}; links={links.physical_link_id.nunique()}", 12, interface_ok)
    directional_unique = links["directional_link_id"].is_unique and not links.duplicated(["physical_link_id", "year", "from_market", "to_market"]).any()
    _add(checks, "B5-QA-09", "Every physical interface/direction/horizon row is unique", directional_unique, True, directional_unique)
    key_set = set(zip(links.physical_link_id, links.year, links.from_market, links.to_market))
    reverse_ok = all((row.physical_link_id, row.year, row.to_market, row.from_market) in key_set for row in links.itertuples())
    group_sizes = links.groupby(["physical_link_id", "year"]).size()
    reverse_ok = reverse_ok and group_sizes.eq(2).all()
    _add(checks, "B5-QA-10", "Each physical interface has one and only one explicit reverse row per horizon", reverse_ok, True, reverse_ok)

    capacities_ok = links["capacity_num"].notna().all() and np.isfinite(links["capacity_num"]).all() and links["capacity_num"].ge(0).all()
    _add(checks, "B5-QA-11", "All directional capacities are numeric, finite and non-negative", f"min={links.capacity_num.min()}; finite={np.isfinite(links.capacity_num).all()}", "finite and >=0", capacities_ok)
    unit_horizon_ok = links["unit"].eq("MW").all() and links["year_int"].notna().all()
    _add(checks, "B5-QA-12", "Every capacity has explicit MW units and a horizon", unit_horizon_ok, True, unit_horizon_ok)
    fixed_ok = links["p_nom_extendable"].str.lower().eq("false").all() and links["fixed_capacity"].str.lower().eq("true").all()
    _add(checks, "B5-QA-13", "No link is extendable and every capacity is fixed", fixed_ok, True, fixed_ok)
    forbidden_fields = {"p_nom_min", "p_nom_max", "capital_cost", "build_year", "lifetime", "s_nom_extendable", "optimization_variable"}
    hidden_ok = not (set(links.columns) & forbidden_fields)
    _add(checks, "B5-QA-14", "Directional contracts contain no hidden capacity-expansion field", sorted(set(links.columns) & forbidden_fields), [], hidden_ok)
    object_ok = links["interconnector_asset_type"].eq("NETWORK_LINK_NOT_GENERATOR").all()
    _add(checks, "B5-QA-15", "Every interconnector is classified as a network link, never a generator", object_ok, True, object_ok)

    expected_capacity = _expected_capacity_map(config)
    observed_capacity = {(int(row.year_int), row.from_market, row.to_market): float(row.capacity_num) for row in links.itertuples()}
    capacity_map_ok = observed_capacity == expected_capacity
    max_difference = max((abs(observed_capacity[key] - value) for key, value in expected_capacity.items()), default=0.0) if set(observed_capacity) == set(expected_capacity) else math.inf
    _add(checks, "B5-QA-16", "All directed capacities exactly reconcile to the locked B5 authority matrix", max_difference, 0.0, capacity_map_ok)
    asymmetric_pairs = links.groupby(["physical_link_id", "year_int"])["capacity_num"].nunique().gt(1)
    asymmetric_physical = set(asymmetric_pairs.loc[asymmetric_pairs].index.get_level_values(0))
    asymmetry_ok = asymmetric_physical == {"IT_FR", "IT_CH", "IT_AT", "IT_SI", "EXT_CH_FR"} and links.loc[links.physical_link_id.isin(asymmetric_physical), "asymmetry_treatment"].eq("PRESERVE_EXPLICIT_DIRECTIONAL_ASYMMETRY").all()
    _add(checks, "B5-QA-17", "All five accepted asymmetric physical interfaces preserve explicit directionality", sorted(asymmetric_physical), ["EXT_CH_FR", "IT_AT", "IT_CH", "IT_FR", "IT_SI"], asymmetry_ok)
    convention_ok = links["direction_convention"].eq("POSITIVE_FROM_MARKET_TO_TO_MARKET").all()
    _add(checks, "B5-QA-18", "The from/to direction convention is unambiguous", convention_ok, True, convention_ok)

    ext_2040 = links.loc[links.link_family.eq("EXTERNAL_EXTERNAL") & links.year_int.eq(2040)]
    ext_2050 = links.loc[links.link_family.eq("EXTERNAL_EXTERNAL") & links.year_int.eq(2050)]
    horizon_ok = ext_2040.status.eq("DIRECT_ACCEPTED_HORIZON_VALUE").all() and ext_2040.direct_vs_carry_forward.eq("DIRECT_2040_CONSTRUCTION").all() and ext_2050.status.eq("ACCEPTED_2040_CARRY_FORWARD_TO_2050").all() and ext_2050.direct_vs_carry_forward.eq("CARRY_FORWARD_FROM_2040").all()
    _add(checks, "B5-QA-19", "2040 external values are direct accepted constructions and 2050 values are explicit carry-forwards", horizon_ok, True, horizon_ok)
    no_2030 = not links.year_int.eq(2030).any() and not links.status.str.contains("2030_CARRY", regex=False).any()
    _add(checks, "B5-QA-20", "No 2030-only MW row is promoted automatically", no_2030, True, no_2030)
    carry_ok = len(carry) == 8 and carry["rule"].eq("2040_NTC_CARRY_FORWARD_TO_2050").all() and carry["source_year"].eq("2040").all() and carry["target_year"].eq("2050").all() and pd.to_numeric(carry.source_capacity_MW).equals(pd.to_numeric(carry.target_capacity_MW)) and carry.interpolation_used.str.lower().eq("false").all() and carry.extrapolation_used.str.lower().eq("false").all()
    _add(checks, "B5-QA-21", "All eight 2040-to-2050 carry-forward directions are explicit and value-preserving", len(carry), 8, carry_ok)

    italy = links.loc[links.link_family.eq("ITALY_FACING")]
    italy_markets = (set(italy.from_market) | set(italy.to_market)) - {"IT"}
    italy_ok = italy_markets == {"FR", "CH", "AT", "SI", "ME", "GR", "MT", "TN"} and italy.status.eq("FROZEN_MEM_ITALY_INTERFACE").all()
    _add(checks, "B5-QA-22", "All eight Italy-facing markets reconcile to the frozen MEM interface contract", sorted(italy_markets), ["AT", "CH", "FR", "GR", "ME", "MT", "SI", "TN"], italy_ok)
    mapping = italy[["from_market", "to_market", "stage_b_receiving_zone"]].copy()
    observed_mapping: dict[str, set[str]] = {}
    for record in mapping.to_dict(orient="records"):
        external = record["to_market"] if record["from_market"] == "IT" else record["from_market"]
        observed_mapping.setdefault(external, set()).add(record["stage_b_receiving_zone"])
    expected_mapping = {"FR": {"NORD"}, "CH": {"NORD"}, "AT": {"NORD"}, "SI": {"NORD"}, "ME": {"CSUD"}, "GR": {"SUD"}, "MT": {"SICI"}, "TN": {"SICI"}}
    _add(checks, "B5-QA-23", "Italy-facing identities remain compatible with the later Stage-A-to-Stage-B price handoff", observed_mapping, expected_mapping, observed_mapping == expected_mapping)
    upstream_not_overwrite = observed_capacity[(2040, "MT", "IT")] == 200.0 and observed_capacity[(2040, "IT", "MT")] == 200.0 and observed_capacity[(2040, "TN", "IT")] == 600.0 and observed_capacity[(2040, "IT", "TN")] == 600.0
    _add(checks, "B5-QA-24", "Upstream TYNDP MT/TN controls do not overwrite frozen MEM Italy interfaces", upstream_not_overwrite, True, upstream_not_overwrite)

    expected_ext_pairs = {("AT", "CH"), ("AT", "SI"), ("CH", "FR"), ("HR", "SI")}
    observed_ext_pairs = {tuple(sorted((row.from_market, row.to_market))) for row in ext_2040.itertuples()}
    _add(checks, "B5-QA-25", "External-external topology is exactly the four retained physical adjacencies", sorted(observed_ext_pairs), sorted(expected_ext_pairs), observed_ext_pairs == expected_ext_pairs)
    graph_nodes = set(interfaces.endpoint_a) | set(interfaces.endpoint_b)
    _add(checks, "B5-QA-26", "No required Stage-A market is isolated", sorted(graph_nodes), list(MARKETS), graph_nodes == set(MARKETS))
    physical_pairs = {tuple(sorted((row.endpoint_a, row.endpoint_b))) for row in interfaces.itertuples()}
    no_link_pairs = {tuple(sorted((row.endpoint_a, row.endpoint_b))) for row in no_links.itertuples()}
    all_pairs = {tuple(pair) for pair in __import__("itertools").combinations(MARKETS, 2)}
    no_link_ok = len(no_links) == 33 and physical_pairs.isdisjoint(no_link_pairs) and physical_pairs | no_link_pairs == all_pairs and pd.to_numeric(no_links.capacity_MW).eq(0).all()
    _add(checks, "B5-QA-27", "Every non-physical retained-market pair has one explicit zero/no-link disposition", len(no_links), 33, no_link_ok)

    cors_absent = "CORS" not in link_scope and "CORS" not in set(nodes.market) and "CORS" in set(excluded.market)
    _add(checks, "B5-QA-28", "CORS is absent from Stage A and retained only as an explicit exclusion", cors_absent, True, cors_absent)
    excluded_ok = set(excluded.market) == set(config["excluded_markets"]) and excluded.stage_a_bus_allowed.str.lower().eq("false").all() and excluded.stage_a_link_endpoint_allowed.str.lower().eq("false").all() and set(excluded.market).isdisjoint(link_scope)
    _add(checks, "B5-QA-29", "All named wider-perimeter markets are explicitly rejected as buses and link endpoints", excluded_ok, True, excluded_ok)

    krsko_hosts = set(nodes.loc[nodes.krsko_physical_nuclear_host.str.lower().eq("true"), "market"])
    b1 = _read(ROOT / "stage_a_inputs/etx7a_v1_0/static/MEM_ETX7A_Generators_Static_v1.0.csv")
    nuclear = b1.loc[b1.mapped_carrier.str.contains("NUCLEAR", case=False, na=False)]
    krsko_ok = krsko_hosts == {"SI"} and nuclear.loc[nuclear.country_code.eq("HR")].empty and pd.to_numeric(nuclear.loc[nuclear.country_code.eq("SI"), "p_nom_MW"]).gt(0).any()
    _add(checks, "B5-QA-30", "Krsko physical nuclear capacity remains on SI only with no HR duplicate", sorted(krsko_hosts), ["SI"], krsko_ok)

    authority_keys = set(zip(authority.year, authority.from_market, authority.to_market))
    link_keys = set(zip(links.year, links.from_market, links.to_market))
    authority_ok = len(authority) == 48 and authority_keys == link_keys and authority.status.ne("").all() and not authority.status.str.contains("PENDING", case=False).any()
    _add(checks, "B5-QA-31", "Every directional topology row has one implementation-ready authority disposition", len(authority), 48, authority_ok)
    provenance_ok = len(provenance) >= 6 and provenance.source_id.is_unique and _source_provenance_ok(provenance)
    _add(checks, "B5-QA-32", "All topology authorities and predecessor locks have traceable matching provenance hashes", provenance_ok, True, provenance_ok)
    output_ok = outputs.status.eq("PASS").all()
    _add(checks, "B5-QA-33", "Every B5 artifact matches its deterministic output manifest", int(outputs.status.eq("PASS").sum()), len(outputs), output_ok)
    if run_deterministic_rebuild:
        deterministic_ok = deterministic.status.eq("PASS").all()
        _add(checks, "B5-QA-34", "Independent B5 rebuild is byte-identical", int(deterministic.status.eq("PASS").sum()), len(deterministic), deterministic_ok)

    plan_manifest = _read(ROOT / "docs/project_plan/MEM_PROJECT_PLAN_MANIFEST.csv")
    active = plan_manifest.loc[plan_manifest.authority_status.eq("ACTIVE")]
    plan_ok = len(active) == 1 and active.iloc[0].plan_version == "2.2" and sha256_file(ROOT / config["controlling_plan"]["path"]) == config["controlling_plan"]["sha256"]
    _add(checks, "B5-QA-35", "Plan v2.2 remains the sole active B5 execution authority", plan_ok, True, plan_ok)
    scope_code_ok = not _contains_forbidden_runtime_calls(
        (
            ROOT / "src/mem_model/stage_a/topology.py",
            ROOT / "src/mem_model/stage_a/topology_validation.py",
        )
    )
    _add(checks, "B5-QA-36", "B5 code contains no PyPSA network construction, Gurobi setup, or optimization execution", scope_code_ok, True, scope_code_ok)
    approval = yaml.safe_load((ROOT / "config/approval_gates.yaml").read_text(encoding="utf-8"))
    solver_disabled = approval["full_year_solver"]["execution_enabled"] is False
    _add(checks, "B5-QA-37", "The full-year solver remains disabled", solver_disabled, True, solver_disabled)
    package_formats_ok = all(path.suffix.lower() == ".csv" for path in output_dir.iterdir() if path.is_file())
    _add(checks, "B5-QA-38", "B5 emits input contracts only and no network/price artifact", package_formats_ok, True, package_formats_ok)

    qa = pd.DataFrame.from_records(checks)
    passed = qa.status.eq("PASS").all()
    status = "ETX7B5_FIXED_STAGE_A_TOPOLOGY_COMPLETE" if passed else "ETX7B5_VALIDATION_FAILED"
    verification = {
        "phase": "ETX-7B5",
        "status": status,
        "schema_version": "ETX7B5_TOPOLOGY_V1_0",
        "plan": {"version": "2.2", "sha256": config["controlling_plan"]["sha256"]},
        "counts": {
            "nodes": len(nodes),
            "physical_connections": len(interfaces),
            "directed_rows": len(links),
            "directed_rows_2040": int(links.year_int.eq(2040).sum()),
            "directed_rows_2050": int(links.year_int.eq(2050).sum()),
            "italy_facing_rows": int(links.link_family.eq("ITALY_FACING").sum()),
            "external_external_rows": int(links.link_family.eq("EXTERNAL_EXTERNAL").sum()),
            "asymmetric_physical_connections": len(asymmetric_physical),
            "carry_forward_rows": len(carry),
            "explicit_no_link_controls": len(no_links),
            "qa_checks": len(qa),
            "qa_failures": int(qa.status.eq("FAIL").sum()),
        },
        "authority_closure": {
            "retained_physical_connections": 12,
            "direct_horizon_rows": 40,
            "carry_forward_rows": 8,
            "unresolved_conflicts": 0,
        },
        "predecessor_regression": {
            family: ("PASS" if group.status.eq("PASS").all() else "FAIL")
            for family, group in predecessors.groupby("receipt_family")
        },
        "scope": {
            "markets": list(MARKETS),
            "horizons": list(HORIZONS),
            "scenario": "Base",
            "corsica": "EXCLUDED",
            "krsko_physical_host": "SI",
            "pypsa_network": "NOT_INSTANTIATED",
            "gurobi_configuration": "NOT_PERFORMED",
            "optimization": "NOT_RUN",
            "market_prices": "NOT_PRODUCED",
            "solver_execution_enabled": False,
        },
        "tests": {
            "focused": _pytest_receipt("pytest_etx7b5_focused.xml"),
            "full_repository": _pytest_receipt("pytest_full_repository.xml"),
        },
        "next_gate": "PRE_B6_BOUNDED_EXECUTION_HARNESS_PREPARATION",
        "decisions_needed": "NONE" if passed else "REVIEW_FAILED_QA_ONLY",
    }

    if write_reports:
        QA_DIR.mkdir(parents=True, exist_ok=True)
        predecessors.to_csv(QA_DIR / "MEM_ETX7B5_Frozen_Predecessor_Hash_Receipt_v1.0.csv", index=False, encoding="utf-8", lineterminator="\n")
        outputs.to_csv(QA_DIR / "MEM_ETX7B5_Output_Manifest_Receipt_v1.0.csv", index=False, encoding="utf-8", lineterminator="\n")
        deterministic.to_csv(QA_DIR / "MEM_ETX7B5_Deterministic_Rebuild_Receipt_v1.0.csv", index=False, encoding="utf-8", lineterminator="\n")
        qa.to_csv(QA_DIR / "MEM_ETX7B5_Topology_QA_v1.0.csv", index=False, encoding="utf-8", lineterminator="\n")
        (QA_DIR / "MEM_ETX7B5_Final_Verification_v1.0.json").write_text(json.dumps(verification, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "qa": qa,
        "verification": verification,
        "predecessor_receipt": predecessors,
        "output_receipt": outputs,
        "deterministic_receipt": deterministic,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate deterministic ETX-7B5 Stage-A topology contracts")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--skip-deterministic-rebuild", action="store_true")
    args = parser.parse_args()
    result = validate_topology_package(
        args.output_dir,
        write_reports=True,
        run_deterministic_rebuild=not args.skip_deterministic_rebuild,
    )
    print(json.dumps(result["verification"], indent=2))
    if not result["qa"].status.eq("PASS").all():
        raise SystemExit(1)


if __name__ == "__main__":
    main()

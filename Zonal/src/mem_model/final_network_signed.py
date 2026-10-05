"""Exact, non-solving v2B transport adapter and canonical net-flow semantics.

The accepted contract determines connectivity and directional MW. This module
only replaces an eligible nonnegative reciprocal pair by its signed net flow.
No optimization model, dispatch calculation or capacity decision is performed.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa
from pandas.testing import assert_frame_equal

from .common import ROOT, SCENARIOS, dump_json, safe_id, sha256_file
from .stage_b_zonal_vre import no_models_or_solves, read_json

INTERFACE_CARRIERS = frozenset({"internal_transfer", "external_trade", "corsica_hub"})
QA = ROOT / "qa/final_methodology_closure/network_v2b"
OUTPUT = ROOT / "networks/unsolved/final_wind_hydro_signed_v1"
FLOW_METRIC = "NET_FLOW_A_TO_B_MW"
REPRESENTATION = "NATIVE_SIGNED_LOSSLESS_TRANSPORT_V1"


def native_api_receipt() -> dict:
    """Verify the installed schema/constraint source, without building a model."""
    from pypsa.optimization.constraints import define_operational_constraints_for_non_extendables
    if pypsa.__version__ != "1.2.3":
        raise RuntimeError("NETWORK_V2B_INSTALLED_API_VERSION_DRIFT")
    network = pypsa.Network()
    defaults = network.components.links.defaults
    description = str(defaults.loc["p_min_pu", "description"])
    source = inspect.getsource(define_operational_constraints_for_non_extendables)
    if "Can also be negative" not in description or "min_pu, max_pu = c.get_bounds_pu" not in source:
        raise RuntimeError("NETWORK_V2B_NATIVE_SIGNED_BOUNDS_NOT_VERIFIED")
    path = Path(inspect.getfile(define_operational_constraints_for_non_extendables))
    schema = Path(pypsa.__file__).parent / "data/component_attrs/links.csv"
    return {"pypsa_version": pypsa.__version__, "negative_Link_p_min_pu_supported": True,
        "schema_path": str(schema), "schema_sha256": sha256_file(schema),
        "operational_constraints_path": str(path), "operational_constraints_sha256": sha256_file(path),
        "constraint_semantics": "p_nom * p_min_pu <= Link-p <= p_nom * p_max_pu",
        "optimization_model_constructed": False, "production_solver_invocations": 0}


def _series(network, name: str, attribute: str) -> pd.Series:
    frame = network.links_t[attribute]
    if name in frame.columns:
        result = pd.to_numeric(frame[name], errors="raise").reindex(network.snapshots)
    else:
        result = pd.Series(network.links.at[name, attribute], index=network.snapshots)
    return result.astype(float)


def _constant(series, value) -> bool:
    array = np.asarray(series, dtype=float)
    return bool(np.all(np.isnan(array))) if pd.isna(value) else bool(np.all(array == value))


def _eligibility(network, ids: list[str]) -> list[str]:
    """Conservative whitelist: unknown/nondefault semantics are never discarded."""
    reasons = []
    defaults = network.components.links.defaults
    flexible = {"bus0", "bus1", "carrier", "p_nom", "p_min_pu", "p_max_pu"}
    output = set(defaults.index[defaults.status.astype(str).eq("Output")])
    for name in ids:
        row = network.links.loc[name]
        for attr in network.links.columns:
            if attr in flexible or attr in output:
                continue
            if attr not in defaults.index:
                reasons.append(f"{name}:UNKNOWN_ATTRIBUTE:{attr}")
                continue
            value, expected = row[attr], defaults.at[attr, "default"]
            if not ((pd.isna(value) and pd.isna(expected)) or value == expected):
                reasons.append(f"{name}:NONDEFAULT_SEMANTICS:{attr}")
        for attr, frame in network.links_t.items():
            if name not in frame.columns:
                continue
            if attr in {"p_min_pu", "p_max_pu"}:
                continue
            if attr in output:
                if not frame[name].empty:
                    reasons.append(f"{name}:SOLVED_OUTPUT:{attr}")
                continue
            if attr not in defaults.index or not _constant(frame[name], defaults.at[attr, "default"]):
                reasons.append(f"{name}:DIRECTION_SPECIFIC_SERIES:{attr}")
        nominal = float(row.p_nom)
        lower, upper = _series(network, name, "p_min_pu"), _series(network, name, "p_max_pu")
        if not np.isfinite(nominal) or nominal < 0:
            reasons.append(f"{name}:INVALID_NOMINAL_CAPACITY")
        if not np.isfinite(lower).all() or not np.isfinite(upper).all() or (lower != 0).any() or (upper < 0).any():
            reasons.append(f"{name}:NOT_NONNEGATIVE_DIRECTIONAL_BOUNDS")
    carriers = set(network.links.loc[ids, "carrier"].astype(str))
    for constraint_id, row in network.global_constraints.iterrows():
        # Existing P2X operational-limit constraints act on Generators, not Links.
        if str(row.type) == "operational_limit" and str(row.carrier_attribute).startswith("p2x_flexible_"):
            continue
        if str(row.carrier_attribute) in carriers or str(row.type).startswith("transmission"):
            reasons.append(f"GLOBAL_CONSTRAINT_REFERENCE:{constraint_id}")
    if len(network.investment_periods):
        reasons.append("MULTI_INVESTMENT_PERIOD_NETWORK")
    return sorted(set(reasons))


def audit_interfaces(network) -> pd.DataFrame:
    """Audit all transport pairs; ineligible pairs are retained, not repaired."""
    subset = network.links.loc[network.links.carrier.astype(str).isin(INTERFACE_CARRIERS)]
    groups = {}
    for name, row in subset.iterrows():
        a, b = sorted((str(row.bus0), str(row.bus1)))
        groups.setdefault((str(row.carrier), a, b), []).append(str(name))
    rows = []
    for (carrier, a, b), ids in sorted(groups.items()):
        reasons = []
        forward = [name for name in ids if (str(subset.at[name, "bus0"]), str(subset.at[name, "bus1"])) == (a, b)]
        reverse = [name for name in ids if (str(subset.at[name, "bus0"]), str(subset.at[name, "bus1"])) == (b, a)]
        if a == b or len(forward) != 1 or len(reverse) != 1 or len(ids) != 2:
            reasons.append("MISSING_OR_AMBIGUOUS_RECIPROCAL_PAIR")
        else:
            reasons.extend(_eligibility(network, ids))
        category = ("CORS_LEG" if carrier == "corsica_hub" else
                    "EXTERNAL_BOUNDARY" if carrier == "external_trade" else
                    "INTERNAL_LONG_DISTANCE" if {a, b} in ({"NORD", "SUD"}, {"NORD", "CSUD"}, {"CSUD", "SICI"}, {"SARD", "SICI"})
                    else "INTERNAL_INTERZONAL")
        eligible = not reasons
        f, r = forward[0] if len(forward) == 1 else "", reverse[0] if len(reverse) == 1 else ""
        positive = _series(network, f, "p_max_pu") * float(subset.at[f, "p_nom"]) if f else pd.Series(dtype=float)
        negative = _series(network, r, "p_max_pu") * float(subset.at[r, "p_nom"]) if r else pd.Series(dtype=float)
        nominal = max(float(positive.max()), float(negative.max())) if len(positive) and len(negative) else np.nan
        if eligible and nominal <= 0:
            eligible = False
            reasons.append("ZERO_CAPACITY_PAIR_RETAINED")
        rows.append({"carrier": carrier, "bus_a": a, "bus_b": b, "corridor_class": category,
            "forward_link": f, "reverse_link": r,
            "signed_link": safe_id(f"SIGNED__{carrier}__{a}__TO__{b}"),
            "directional_link_count": len(ids), "eligible": eligible,
            "reason": ";".join(reasons) if reasons else "EXACT_LOSSLESS_ZERO_COST_NET_PROJECTION",
            "signed_p_nom_MW": nominal,
            "capacity_a_to_b_MW": float(positive.max()) if len(positive) else np.nan,
            "capacity_b_to_a_MW": float(negative.max()) if len(negative) else np.nan,
            "snapshot_count": len(network.snapshots), "orientation_rule": "LEXICOGRAPHIC_ENDPOINTS",
            "net_flow_semantics": FLOW_METRIC})
    return pd.DataFrame(rows)


def convert_interfaces(parent) -> tuple[pypsa.Network, pd.DataFrame]:
    """Return a new UNSOLVED network. Never mutate the accepted parent."""
    audit = audit_interfaces(parent)
    if getattr(parent, "_objective", None) is not None or getattr(parent, "_model", None) is not None:
        raise RuntimeError("NETWORK_V2B_PARENT_MUST_BE_UNSOLVED")
    successor = parent.copy()
    metadata = []
    for row in audit.loc[audit.eligible].itertuples(index=False):
        ids = [row.forward_link, row.reverse_link]
        if row.signed_link in parent.links.index:
            raise RuntimeError("NETWORK_V2B_SIGNED_ID_COLLISION")
        upper = _series(parent, row.forward_link, "p_max_pu") * float(parent.links.at[row.forward_link, "p_nom"])
        lower = -_series(parent, row.reverse_link, "p_max_pu") * float(parent.links.at[row.reverse_link, "p_nom"])
        dynamic = any(name in parent.links_t[attr].columns for name in ids for attr in ("p_min_pu", "p_max_pu"))
        successor.remove("Link", ids)
        successor.add("Link", row.signed_link, bus0=row.bus_a, bus1=row.bus_b, carrier=row.carrier,
            p_nom=row.signed_p_nom_MW, p_nom_extendable=False, committable=False,
            efficiency=1.0, marginal_cost=0.0,
            p_min_pu=lower / row.signed_p_nom_MW if dynamic else float(lower.iloc[0]) / row.signed_p_nom_MW,
            p_max_pu=upper / row.signed_p_nom_MW if dynamic else float(upper.iloc[0]) / row.signed_p_nom_MW)
        metadata.append({key: getattr(row, key) for key in ("carrier", "bus_a", "bus_b", "forward_link", "reverse_link", "signed_link")})
    successor.meta = dict(parent.meta)
    successor.meta.update({"network_v2b_applied": True, "network_v2b_representation": REPRESENTATION,
        "network_v2b_orientation_rule": "LEXICOGRAPHIC_ENDPOINTS", "network_v2b_corridors": metadata,
        "production_executed_by_network_v2b": False})
    validate_conversion(parent, successor, audit)
    return successor, audit


def validate_conversion(parent, successor, audit: pd.DataFrame) -> dict:
    """Exact non-Link preservation and snapshot-level net-interval equality."""
    selected = audit.loc[audit.eligible]
    removed = set(selected.forward_link) | set(selected.reverse_link)
    added = set(selected.signed_link)
    if set(successor.links.index) != (set(parent.links.index) - removed) | added:
        raise RuntimeError("NETWORK_V2B_LINK_ID_SET_DRIFT")
    # NetCDF does not persist pandas' optional index-frequency annotation.
    assert_frame_equal(parent.snapshot_weightings, successor.snapshot_weightings, check_exact=True, check_freq=False)
    if not parent.snapshots.equals(successor.snapshots):
        raise RuntimeError("NETWORK_V2B_CHRONOLOGY_DRIFT")
    for component in parent.components:
        other = successor.components[component.name]
        excluded_parent = removed if component.name == "Link" else set()
        excluded_output = added if component.name == "Link" else set()
        assert_frame_equal(component.static.drop(index=list(excluded_parent), errors="ignore"),
            other.static.drop(index=list(excluded_output), errors="ignore"), check_exact=True, check_freq=False)
        if set(component.dynamic) != set(other.dynamic):
            raise RuntimeError("NETWORK_V2B_DYNAMIC_ATTRIBUTE_DRIFT")
        for attribute, frame in component.dynamic.items():
            assert_frame_equal(frame.drop(columns=list(excluded_parent), errors="ignore"),
                other.dynamic[attribute].drop(columns=list(excluded_output), errors="ignore"), check_exact=True, check_freq=False)
    maximum = 0.0
    for row in selected.itertuples(index=False):
        link = successor.links.loc[row.signed_link]
        if (str(link.bus0), str(link.bus1), str(link.carrier)) != (row.bus_a, row.bus_b, row.carrier):
            raise RuntimeError("NETWORK_V2B_ENDPOINT_OR_CARRIER_DRIFT")
        if link.p_nom_extendable or link.committable or link.efficiency != 1 or link.marginal_cost != 0:
            raise RuntimeError("NETWORK_V2B_SIGNED_SEMANTICS_DRIFT")
        expected_upper = _series(parent, row.forward_link, "p_max_pu") * float(parent.links.at[row.forward_link, "p_nom"])
        expected_lower = -_series(parent, row.reverse_link, "p_max_pu") * float(parent.links.at[row.reverse_link, "p_nom"])
        actual_upper = _series(successor, row.signed_link, "p_max_pu") * float(link.p_nom)
        actual_lower = _series(successor, row.signed_link, "p_min_pu") * float(link.p_nom)
        difference = max(float(np.max(np.abs(actual_upper - expected_upper))), float(np.max(np.abs(actual_lower - expected_lower))))
        maximum = max(maximum, difference)
        if difference > 1e-9:
            raise RuntimeError("NETWORK_V2B_DIRECTIONAL_INTERVAL_DRIFT")
    return {"status": "PASS", "converted_corridors": len(selected), "retained_corridors": len(audit) - len(selected),
        "legacy_links_removed": len(removed), "signed_links_added": len(added),
        "non_target_model_objects": "EXACT_MATCH", "interval_snapshots_checked": len(selected) * len(parent.snapshots),
        "maximum_absolute_directional_MW_difference": maximum,
        "CORS_bus_and_attachments_preserved": "CORS" not in parent.buses.index or "CORS" in successor.buses.index,
        "gross_counterflow_degree_removed": True, "optimization_model_constructed": False,
        "production_optimization_executed": False, "production_solver_invocations": 0}


def corridor_table(network) -> pd.DataFrame:
    """One canonical physical/commercial corridor, independent of representation."""
    metadata = network.meta.get("network_v2b_corridors", [])
    signed = {str(row["signed_link"]): row for row in metadata}
    rows = []
    covered = set()
    for name, item in sorted(signed.items()):
        if name not in network.links.index:
            raise RuntimeError("NETWORK_V2B_METADATA_LINK_MISSING")
        link = network.links.loc[name]
        if (str(link.bus0), str(link.bus1), str(link.carrier)) != (item["bus_a"], item["bus_b"], item["carrier"]):
            raise RuntimeError("NETWORK_V2B_METADATA_ENDPOINT_DRIFT")
        upper = _series(network, name, "p_max_pu") * float(link.p_nom)
        lower = -_series(network, name, "p_min_pu") * float(link.p_nom)
        rows.append(dict(item, representation="SIGNED", capacity_a_to_b_MW=float(upper.max()),
            capacity_b_to_a_MW=float(lower.max()), directional_link_count=1))
        covered.add(name)
    legacy = network.copy()
    if covered:
        legacy.remove("Link", list(covered))
    for row in audit_interfaces(legacy).itertuples(index=False):
        if not row.forward_link or not row.reverse_link or row.directional_link_count != 2:
            raise RuntimeError("NETWORK_V2B_REPORTING_AMBIGUOUS_CORRIDOR")
        rows.append({key: getattr(row, key) for key in ("carrier", "bus_a", "bus_b", "forward_link", "reverse_link", "signed_link",
            "capacity_a_to_b_MW", "capacity_b_to_a_MW", "directional_link_count") } | {"representation": "LEGACY"})
    return pd.DataFrame(rows).sort_values(["carrier", "bus_a", "bus_b"]).reset_index(drop=True)


def net_flow_frame(network) -> pd.DataFrame:
    """Canonical positive A->B net flow. No p1 sum, gross-flow double count or solve."""
    rows = []
    for row in corridor_table(network).itertuples(index=False):
        if row.representation == "SIGNED":
            flow = network.links_t.p0[row.signed_link]
        else:
            flow = network.links_t.p0[row.forward_link] - network.links_t.p0[row.reverse_link]
        flow = flow.reindex(network.snapshots)
        if not np.isfinite(flow).all():
            raise RuntimeError("NETWORK_V2B_REPORTING_FLOW_COVERAGE_FAIL")
        rows.append(pd.DataFrame({"snapshot": network.snapshots, "carrier": row.carrier,
            "bus_a": row.bus_a, "bus_b": row.bus_b, "interface_id": row.signed_link,
            "representation": row.representation, FLOW_METRIC: flow.to_numpy(dtype=float)}))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["snapshot", "carrier", "bus_a", "bus_b", "interface_id", "representation", FLOW_METRIC])


def directional_capacity_table(network) -> pd.DataFrame:
    """Expand signed intervals into directional contract rows, not fictitious Links.

    ``interface_id`` is a unique backing-table direction identifier; ``modeled_link``
    is the actual PyPSA Link. Static capacities are accompanied by their minimum
    across snapshots so a contract validator can detect time-dependent reductions.
    """
    records = []
    for row in corridor_table(network).itertuples(index=False):
        for a, b, forward in ((row.bus_a, row.bus_b, True), (row.bus_b, row.bus_a, False)):
            if row.representation == "SIGNED":
                modeled = row.signed_link
                limit = (_series(network, modeled, "p_max_pu") if forward else -_series(network, modeled, "p_min_pu")) * float(network.links.at[modeled, "p_nom"])
                direction_id = f"{modeled}__{'A_TO_B' if forward else 'B_TO_A'}"
            else:
                modeled = row.forward_link if forward else row.reverse_link
                direction_id = modeled
                limit = _series(network, modeled, "p_max_pu") * float(network.links.at[modeled, "p_nom"])
            records.append({"interface_id": direction_id, "modeled_link": modeled,
                "from_bus": a, "to_bus": b, "carrier": row.carrier,
                "capacity_MW": float(limit.max()), "minimum_capacity_MW": float(limit.min()),
                "efficiency": float(network.links.at[modeled, "efficiency"]),
                "directional": True, "representation": row.representation,
                "layout_semantics": "SCHEMATIC_NOT_GEOGRAPHIC"})
    return pd.DataFrame(records).sort_values(["carrier", "interface_id"]).reset_index(drop=True)


def promote_phase_n(parents: list[dict], *, test_results: dict) -> dict:
    """Export Phase-N successors only after accepted H PASS; caller owns STATE."""
    state = read_json(ROOT / "qa/final_methodology_closure/STATE.json")
    if state["phases"]["H"]["status"] != "PASS" or state["next_phase"] != "N":
        raise RuntimeError("NETWORK_V2B_H_PREDECESSOR_NOT_PASS")
    if {(int(row["year"]), str(row["scenario"])) for row in parents} != set(SCENARIOS) or len(parents) != 6:
        raise RuntimeError("NETWORK_V2B_PARENT_COVERAGE_FAIL")
    QA.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    audits, packages = [], []
    with no_models_or_solves():
        api = native_api_receipt()
        for item in sorted(parents, key=lambda row: (int(row["year"]), str(row["scenario"]))):
            path = ROOT / item["path"]
            if path.parent != ROOT / "networks/unsolved/final_wind_hydro_v1" or sha256_file(path) != item["sha256"]:
                raise RuntimeError("NETWORK_V2B_PARENT_IDENTITY_FAIL")
            parent = pypsa.Network(path)
            if len(parent.snapshots) != 8760 or int(parent.meta["year"]) != int(item["year"]) or parent.meta["scenario"] != item["scenario"]:
                raise RuntimeError("NETWORK_V2B_PARENT_CHRONOLOGY_OR_SCENARIO_FAIL")
            successor, audit = convert_interfaces(parent)
            destination = OUTPUT / f"MEM_{item['year']}_{item['scenario'].upper()}_FINAL_WIND_HYDRO_SIGNED_V1_8760h_UNSOLVED.nc"
            if destination.exists():
                loaded = pypsa.Network(destination)
            else:
                successor.export_to_netcdf(destination)
                loaded = pypsa.Network(destination)
            check = validate_conversion(parent, loaded, audit)
            if loaded.meta != successor.meta:
                raise RuntimeError("NETWORK_V2B_RELOAD_METADATA_DRIFT")
            if sha256_file(path) != item["sha256"]:
                raise RuntimeError("NETWORK_V2B_PARENT_CHANGED")
            audit.insert(0, "scenario", item["scenario"])
            audit.insert(0, "year", int(item["year"]))
            audits.append(audit)
            packages.append({"year": int(item["year"]), "scenario": item["scenario"],
                "parent": item["path"], "parent_sha256": item["sha256"],
                "path": str(destination.relative_to(ROOT)), "sha256": sha256_file(destination), "QA": check})
    audit_path = QA / "SIGNED_INTERFACE_ELIGIBILITY_AND_EQUIVALENCE.csv"
    pd.concat(audits, ignore_index=True).to_csv(audit_path, index=False)
    dump_json(QA / "NATIVE_SIGNED_LINK_API_RECEIPT.json", api)
    receipt = {"state": "NETWORK_V2B_PASS", "packages": packages, "tests": test_results,
        "proof": "0<=f_AB<=F_AB;0<=f_BA<=F_BA projects exactly to -F_BA<=net=f_AB-f_BA<=F_AB; reverse lifting is max(net,0),max(-net,0).",
        "canonical_flow": FLOW_METRIC, "canonical_orientation": "LEXICOGRAPHIC_ENDPOINTS",
        "eligible_constraints_removed": 0, "accepted_directional_MW_changed": False,
        "CORS_retained": True, "optimization_model_constructed": False,
        "production_optimization_executed": False, "production_solver_invocations": 0}
    receipt_path = QA / "NETWORK_V2B_FINAL_VERIFICATION.json"
    dump_json(receipt_path, receipt)
    artifacts = [audit_path, QA / "NATIVE_SIGNED_LINK_API_RECEIPT.json", receipt_path,
        ROOT / "src/mem_model/final_network_signed.py", ROOT / "tests/test_final_network_signed.py",
        ROOT / "src/mem_model/reporting/topology.py", ROOT / "src/mem_model/reporting/canonical_results.py",
        ROOT / "docs/final_methodology_closure/PHASE_N_SIGNED_INTERFACE_HANDOFF.md"]
    pd.DataFrame([{"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path)} for path in artifacts]
        + [{"path": row["path"], "sha256": row["sha256"]} for row in packages]).to_csv(QA / "NETWORK_V2B_OUTPUT_MANIFEST.csv", index=False)
    return receipt

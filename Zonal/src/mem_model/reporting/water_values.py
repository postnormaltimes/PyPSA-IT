"""Non-solving native hydro Store-dual extraction for verified price LPs.

Assigned PyPSA dynamic tables alone are insufficient evidence: native dual
assignment can fill absent entries with zero.  Save the active constraint
labels and raw duals while the solved model is available, then supply that
coverage alongside a serialized network and its verified fixed-LP receipt.
No operation in this module constructs a model, solves, or changes a network.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping

import numpy as np
import pandas as pd


CONSTRAINT = "Store-energy_balance"
SOURCE_API = "n.stores_t.mu_energy_balance"
UNITS = "EUR/MWh_water"
ZONES = frozenset({"NORD", "CNOR", "CSUD", "SUD", "CALA", "SICI", "SARD"})
MAPPING_COLUMNS = ("store_id", "state_id", "zone", "hydro_class")
COVERAGE_COLUMNS = ("snapshot", "store_id", "constraint_name", "constraint_label", "raw_dual")


class WaterValueError(ValueError):
    """Fail-closed water-value provenance, coverage, or dimensional failure."""


def _fail(message: str) -> None:
    raise WaterValueError(f"WATER_VALUE_COMPLETENESS_FAIL: {message}")


def _mapping(network: Any, state_mapping: pd.DataFrame) -> pd.DataFrame:
    if not set(MAPPING_COLUMNS).issubset(state_mapping.columns):
        _fail("state mapping requires store_id, state_id, zone and hydro_class")
    mapping = state_mapping.loc[:, MAPPING_COLUMNS].copy()
    if mapping.empty or mapping.isna().any().any():
        _fail("empty or missing state mapping values")
    for column in MAPPING_COLUMNS:
        if mapping[column].map(lambda value: not isinstance(value, str) or not value.strip()).any():
            _fail(f"missing or invalid mapped {column}")
    if mapping["store_id"].duplicated().any() or mapping["state_id"].duplicated().any():
        _fail("duplicate mapped Store or state")
    if not set(mapping["zone"]).issubset(ZONES):
        _fail("noncanonical Italian zone in state mapping")
    if network.stores.index.has_duplicates or network.buses.index.has_duplicates:
        _fail("duplicate network Store or bus IDs")
    water_buses = network.buses.index[network.buses["carrier"].eq("water_energy")]
    water_stores = network.stores.index[
        network.stores["bus"].isin(water_buses)
        & network.stores["carrier"].eq("water_energy")
    ]
    if set(mapping["store_id"]) != set(water_stores):
        _fail("mapped states do not match every hydro/PHS water Store exactly")
    # Accepted mapping, not Store-name inference, supplies class and zone.
    return mapping.sort_values("store_id", kind="stable").reset_index(drop=True)


def _snapshots(network: Any, expected_snapshots: pd.Index | None) -> pd.Index:
    snapshots = network.snapshots if expected_snapshots is None else expected_snapshots
    snapshots = pd.Index(snapshots, name="snapshot")
    if snapshots.empty or snapshots.has_duplicates or snapshots.hasnans:
        _fail("empty, duplicate or missing snapshots")
    if not snapshots.equals(pd.Index(network.snapshots, name="snapshot")):
        _fail("expected chronology differs from the exact ordered network snapshots")
    return snapshots


def capture_store_energy_balance_coverage(
    network: Any,
    state_mapping: pd.DataFrame,
    *,
    expected_snapshots: pd.Index | None = None,
) -> pd.DataFrame:
    """Read existing live raw constraint labels/duals; never create a model.

    The returned evidence can be persisted by the caller before export.  An
    inactive constraint label (-1), missing dual, or missing state fails here;
    zero raw duals are valid only when their active labels are present.
    """
    mapping = _mapping(network, state_mapping)
    snapshots = _snapshots(network, expected_snapshots)
    model = getattr(network, "model", None)
    if model is None:
        _fail("no live raw constraint evidence; supply saved active raw coverage")
    try:
        constraint = model.constraints[CONSTRAINT]
        raw = constraint.dual.to_pandas()
        labels = constraint.labels.to_pandas()
    except (AttributeError, KeyError, ValueError) as exc:
        _fail(f"native {CONSTRAINT} raw duals or active labels unavailable: {exc}")
    stores = mapping["store_id"].tolist()
    for frame, name in ((raw, "raw duals"), (labels, "constraint labels")):
        if not isinstance(frame, pd.DataFrame):
            _fail(f"{name} are not a snapshot-by-Store table")
        if frame.index.has_duplicates or frame.columns.has_duplicates:
            _fail(f"duplicate {name} labels")
        if not snapshots.isin(frame.index).all() or not set(stores).issubset(frame.columns):
            _fail(f"incomplete {name} coverage")
    raw = raw.loc[snapshots, stores]
    labels = labels.loc[snapshots, stores]
    table = pd.DataFrame({
        "snapshot": np.repeat(snapshots.to_numpy(), len(stores)),
        "store_id": np.tile(stores, len(snapshots)),
        "constraint_name": CONSTRAINT,
        "constraint_label": labels.to_numpy().reshape(-1),
        "raw_dual": raw.to_numpy().reshape(-1),
    })
    return _coverage(table, snapshots, stores)


def _coverage(table: pd.DataFrame, snapshots: pd.Index, stores: list[str]) -> pd.DataFrame:
    if not set(COVERAGE_COLUMNS).issubset(table.columns):
        _fail("raw coverage requires snapshot, store_id, constraint_name, constraint_label and raw_dual")
    table = table.loc[:, COVERAGE_COLUMNS].copy()
    if table.isna().any().any() or table.duplicated(["snapshot", "store_id"]).any():
        _fail("missing values or duplicate raw coverage keys")
    if not table["constraint_name"].eq(CONSTRAINT).all():
        _fail("raw coverage does not identify native Store-energy_balance constraints")
    expected = pd.MultiIndex.from_product([snapshots, stores], names=["snapshot", "store_id"])
    actual = pd.MultiIndex.from_frame(table[["snapshot", "store_id"]])
    if len(actual) != len(expected) or not expected.isin(actual).all():
        _fail("raw active constraint coverage is not the exact snapshot/state product")
    try:
        labels = table["constraint_label"].to_numpy(dtype=float)
        raw = table["raw_dual"].to_numpy(dtype=float)
    except (TypeError, ValueError):
        _fail("nonnumeric raw duals or constraint labels")
    if (not np.isfinite(labels).all() or (labels < 0).any()
            or not np.equal(labels, np.floor(labels)).all()
            or table["constraint_label"].duplicated().any()):
        _fail("inactive, noninteger or duplicate native constraint labels")
    if not np.isfinite(raw).all():
        _fail("nonfinite raw native Store duals")
    # Reindex only after completeness; no filling or interpolation is permitted.
    return table.set_index(["snapshot", "store_id"]).loc[expected].reset_index()


def _provenance(provenance: Mapping[str, Any], verified_milp_sha256: str) -> None:
    if provenance.get("kind") != "FIXED_COMMITMENT_PRICE_LP":
        _fail("only FIXED_COMMITMENT_PRICE_LP has canonical water-value authority")
    if provenance.get("solver_status") != "ok" or provenance.get("termination_condition") != "optimal":
        _fail("fixed-commitment LP was not solved successfully")
    if provenance.get("integer_variables") != 0 or provenance.get("binary_variables") != 0:
        _fail("water-value source must have verified zero integer/binary variables")
    for key in ("solved_sha256", "fixed_commitment_milp_sha256"):
        value = provenance.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdefABCDEF" for c in value):
            _fail(f"missing or invalid pinned {key}")
    if not isinstance(verified_milp_sha256, str) or provenance["fixed_commitment_milp_sha256"].lower() != verified_milp_sha256.lower():
        _fail("fixed LP does not reference the verified UC MILP hash")
    for key in ("assign_all_duals", "fixed_commitment_trajectories_verified", "objective_reconciliation_verified"):
        if provenance.get(key) is not True:
            _fail(f"explicit audited provenance missing: {key}")


def extract_water_values(
    network: Any,
    state_mapping: pd.DataFrame,
    provenance: Mapping[str, Any],
    *,
    verified_milp_sha256: str,
    raw_coverage: pd.DataFrame | None = None,
    expected_snapshots: pd.Index | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return long-form raw EUR/MWh_water and a non-solving QA receipt.

    ``provenance`` combines an integrity-verified fixed-LP solve receipt with
    audited dual assignment and existing price-verification outcomes.  The
    caller is responsible for verifying the pinned solved-file hashes before
    loading the Network.  A saved raw-coverage table is mandatory for a
    serialized network without its live solved model.
    """
    _provenance(provenance, verified_milp_sha256)
    mapping = _mapping(network, state_mapping)
    snapshots = _snapshots(network, expected_snapshots)
    stores = mapping["store_id"].tolist()
    if raw_coverage is None:
        raw_coverage = capture_store_energy_balance_coverage(network, mapping, expected_snapshots=snapshots)
    coverage = _coverage(raw_coverage, snapshots, stores)
    assigned = getattr(network.stores_t, "mu_energy_balance", None)
    if not isinstance(assigned, pd.DataFrame) or assigned.index.has_duplicates or assigned.columns.has_duplicates:
        _fail("missing or duplicate assigned native water-value table")
    if not assigned.index.equals(snapshots) or not set(stores).issubset(assigned.columns):
        _fail("incomplete or misordered assigned snapshot/state water values")
    try:
        values = assigned.loc[snapshots, stores].to_numpy(dtype=float)
    except (TypeError, ValueError):
        _fail("nonnumeric assigned Store water values")
    if not np.isfinite(values).all():
        _fail("nonfinite assigned Store water values")
    raw = coverage["raw_dual"].to_numpy(dtype=float).reshape(len(snapshots), len(stores))
    if not np.array_equal(values, raw):
        _fail("assigned Store values differ from raw native active duals")
    table = coverage[["snapshot", "store_id"]].copy()
    table["water_value_EUR_per_MWh_water"] = values.reshape(-1)
    table = table.merge(mapping, on="store_id", validate="many_to_one", sort=False)
    table = table[["snapshot", "store_id", "state_id", "zone", "hydro_class", "water_value_EUR_per_MWh_water"]]
    coverage_hash = sha256(coverage.to_csv(index=False, float_format="%.17g").encode("utf-8")).hexdigest()
    receipt = {
        "schema_version": "MEM_NATIVE_HYDRO_WATER_VALUES_V1",
        "status": "PASS",
        "fixed_commitment_LP_solved_sha256": provenance["solved_sha256"],
        "UC_MILP_solved_sha256": verified_milp_sha256,
        "source_api": SOURCE_API,
        "raw_constraint": CONSTRAINT,
        "raw_coverage_sha256": coverage_hash,
        "units": UNITS,
        "sign_convention": "RAW_NATIVE_STORE_ENERGY_BALANCE_DUAL_SIGN_PRESERVED",
        "objective_snapshot_weight_division": False,
        "stores_count": len(stores),
        "snapshot_count": len(snapshots),
        "finite_count": int(values.size),
        "assignment_flag": "assign_all_duals=true",
        "active_raw_constraint_coverage_verified": True,
        "assigned_values_equal_raw_duals": True,
        "missing_values_filled": False,
        "cyclic_predecessor": "Final output state precedes first snapshot; first and last output states need not equal.",
        "physical_operation_authority": "UC_MILP",
        "water_value_authority": "FIXED_COMMITMENT_PRICE_LP",
        "optimization_model_constructed": False,
        "production_optimization_executed": False,
        "production_solver_invocations": 0,
    }
    return table, receipt

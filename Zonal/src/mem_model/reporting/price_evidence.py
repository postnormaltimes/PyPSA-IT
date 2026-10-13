"""Retain scoped primal/dual constraint evidence from an already solved price LP.

Active labels distinguish genuine zero duals from inactive assigned table slots.
Capture is chunked, reads no solver backend, and never constructs a matrix or
optimization model. Existing solved results do not require retrospective capture.
"""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..common import sha256_file


FIELDS = ("constraint_name", "snapshot", "asset_id", "constraint_label",
          "raw_dual", "sign", "rhs", "activity", "slack")
SCHEMA = pa.schema([
    ("constraint_name", pa.string()), ("snapshot", pa.timestamp("ns")),
    ("asset_id", pa.string()), ("constraint_label", pa.int64()),
    ("raw_dual", pa.float64()), ("sign", pa.string()), ("rhs", pa.float64()),
    ("activity", pa.float64()), ("slack", pa.float64()),
])
RAMPS = ("Generator-p-ramp_limit_up", "Generator-p-ramp_limit_down")
CAPTURE_FILES = ("RAW_PRICE_CONSTRAINT_EVIDENCE.parquet", "RAW_PRICE_CONSTRAINT_EVIDENCE.json",
                 "RAW_PRICE_VARIABLE_EVIDENCE.parquet", "RAW_PRICE_TERM_EVIDENCE.parquet",
                 "RAW_PRICE_ELECTRICAL_POPULATION.parquet")


def _families(n):
    required = ["Bus-nodal_balance"]
    for prefix, count in (("Generator-fix-p", (~n.generators.committable).sum()),
                          ("Generator-com-p", n.generators.committable.sum()),
                          ("Link-fix-p", len(n.links)), ("Store-fix-e", len(n.stores))):
        if count:
            required.extend(prefix + "-" + bound for bound in ("lower", "upper"))
    if len(n.stores):
        required.append("Store-energy_balance")
    if n.generators.committable.any():
        required.extend("UC2-fixed-" + attr for attr in ("status", "start_up", "shut_down"))
    required.extend("GlobalConstraint-" + str(name) for name in n.global_constraints.index
                    if str(name).startswith("P2X_ANNUAL_"))
    return required


def _solution_vector(model):
    """Read native variable label/solution arrays, avoiding matrices.flat_vars."""
    largest = max((int(variable.labels.max().item()) for _, variable in model.variables.items()), default=-1)
    values = np.full(largest + 1, np.nan)
    seen = np.zeros(largest + 1, dtype=bool)
    for _, variable in model.variables.items():
        labels = variable.labels.to_numpy().reshape(-1)
        solved = variable.solution.to_numpy().reshape(-1)
        active = labels >= 0
        ids = labels[active].astype(int)
        if len(np.unique(ids)) != len(ids) or seen[ids].any():
            raise RuntimeError("PRICE_EVIDENCE_DUPLICATE_VARIABLE_LABEL")
        if not np.isfinite(solved[active]).all():
            raise RuntimeError("PRICE_EVIDENCE_NONFINITE_PRIMAL")
        values[ids] = solved[active]
        seen[ids] = True
    return values


def _rows(name, data, solution):
    labels = data.labels
    if set(labels.dims) - {"snapshot", "name"}:
        raise RuntimeError("PRICE_EVIDENCE_UNSUPPORTED_DIMENSIONS: " + name)
    terms = set(data.vars.dims) - set(labels.dims)
    if len(terms) != 1:
        raise RuntimeError("PRICE_EVIDENCE_UNSUPPORTED_TERMS: " + name)
    term = next(iter(terms))
    ids = data.vars.transpose(*labels.dims, term).to_numpy().astype(int)
    coefficients = data.coeffs.transpose(*labels.dims, term).to_numpy()
    active_terms = ids >= 0
    if active_terms.any() and ids[active_terms].max() >= len(solution):
        raise RuntimeError("PRICE_EVIDENCE_UNBOUND_TERM: " + name)
    if not np.isfinite(coefficients[active_terms]).all():
        raise RuntimeError("PRICE_EVIDENCE_NONFINITE_COEFFICIENT: " + name)
    primal = np.zeros_like(coefficients, dtype=float)
    primal[active_terms] = solution[ids[active_terms]]
    activity = (np.where(active_terms, coefficients, 0.) * primal).sum(axis=-1)
    if labels.ndim:
        frame = labels.to_dataframe(name="constraint_label").reset_index()
    else:
        frame = pd.DataFrame({"constraint_label": [int(labels.item())]})
    frame["constraint_name"] = name
    if "snapshot" not in frame:
        frame["snapshot"] = pd.NaT
    frame["asset_id"] = frame.pop("name").astype(str) if "name" in frame else name.removeprefix("GlobalConstraint-")
    for column in ("dual", "sign", "rhs"):
        frame["raw_dual" if column == "dual" else column] = data[column].broadcast_like(labels).transpose(*labels.dims).to_numpy().reshape(-1)
    frame["activity"] = activity.reshape(-1)
    frame = frame.loc[frame.constraint_label.ge(0)].copy()
    if not frame.sign.isin(["=", ">=", "<="]).all():
        raise RuntimeError("PRICE_EVIDENCE_UNKNOWN_SIGN: " + name)
    if not np.isfinite(frame[["raw_dual", "rhs", "activity"]].to_numpy()).all():
        raise RuntimeError("PRICE_EVIDENCE_NONFINITE_CONSTRAINT: " + name)
    frame["slack"] = np.where(frame.sign.eq("<="), frame.rhs - frame.activity,
                               frame.activity - frame.rhs)
    return frame.loc[:, FIELDS]


def _scoped_constraint_names(model, required, solution_size, chunk_hours):
    """Include every native family touching dispatch/state, not only a whitelist."""
    scoped = np.zeros(solution_size, dtype=bool)
    for name, variable in model.variables.items():
        if name in ("Generator-p", "Link-p", "Store-p", "Store-e"):
            labels = variable.labels.to_numpy().reshape(-1).astype(int)
            scoped[labels[labels >= 0]] = True
    included, excluded = [], []
    for name in model.constraints:
        if name in required or name in RAMPS:
            included.append(name)
            continue
        data = model.constraints[name].data
        chunks = range(0, data.labels.sizes["snapshot"], chunk_hours) if "snapshot" in data.labels.dims else [0]
        relevant = False
        for start in chunks:
            part = data.isel(snapshot=slice(start, start + chunk_hours)) if "snapshot" in data.labels.dims else data
            dimensions = set(part.vars.dims) - set(part.labels.dims)
            if len(dimensions) != 1:
                raise RuntimeError("PRICE_EVIDENCE_UNSUPPORTED_TERMS: " + name)
            term = next(iter(dimensions))
            ids = part.vars.transpose(*part.labels.dims, term).to_numpy().astype(int)
            active = (ids >= 0) & (part.labels.to_numpy()[..., None] >= 0)
            if active.any() and ids[active].max() >= len(scoped):
                raise RuntimeError("PRICE_EVIDENCE_UNBOUND_TERM: " + name)
            if active.any() and scoped[ids[active]].any():
                relevant = True
                break
        (included if relevant else excluded).append(name)
    return included, excluded


def capture_price_evidence(n, directory: Path, provenance: dict, *, chunk_hours=256,
                           extended=False, supporting_inputs=None):
    """Save matched raw LP activity/dual evidence before native NetCDF export."""
    model = getattr(n, "model", None)
    if model is None or model.status != "ok":
        raise RuntimeError("PRICE_EVIDENCE_REQUIRES_SOLVED_MODEL")
    if int(model.binaries.nvars) or int(model.integers.nvars):
        raise RuntimeError("PRICE_EVIDENCE_REQUIRES_CONTINUOUS_LP")
    if provenance.get("kind") != "FIXED_COMMITMENT_PRICE_LP":
        raise RuntimeError("PRICE_EVIDENCE_WRONG_SOURCE")
    for field in ("input_sha256", "fixed_commitment_milp_sha256"):
        value = provenance.get(field, "")
        if len(value) != 64 or any(c not in "0123456789abcdefABCDEF" for c in value):
            raise RuntimeError("PRICE_EVIDENCE_MISSING_LINEAGE: " + field)
    if chunk_hours < 1:
        raise ValueError("chunk_hours must be positive")
    directory = Path(directory)
    table_path = directory / "RAW_PRICE_CONSTRAINT_EVIDENCE.parquet"
    manifest_path = directory / "RAW_PRICE_CONSTRAINT_EVIDENCE.json"
    if any((directory / name).exists() for name in CAPTURE_FILES):
        raise RuntimeError("PRICE_EVIDENCE_OVERWRITE_REFUSED")
    required = _families(n)
    missing = set(required) - set(model.constraints)
    if missing:
        raise RuntimeError("PRICE_EVIDENCE_MISSING_CONSTRAINT: " + str(sorted(missing)))
    solution = _solution_vector(model)
    names = required + [name for name in RAMPS if name in model.constraints]
    excluded = []
    if extended:
        for field in ("job_id", "result_branch"):
            if not provenance.get(field):
                raise RuntimeError("PRICE_EVIDENCE_MISSING_LINEAGE: " + field)
        names, excluded = _scoped_constraint_names(model, required, len(solution), chunk_hours)
    directory.mkdir(parents=True, exist_ok=True)
    summaries = []
    from contextlib import ExitStack
    from .lp_resource_capture import (TERM_FILE, TERM_SCHEMA, term_rows, capture_variables,
                                      capture_population, objective_coefficients)
    objective = objective_coefficients(model, len(solution)) if extended else None
    gradient = np.zeros(len(solution)) if extended else None
    term_count = 0
    with ExitStack() as context:
        writer = context.enter_context(pq.ParquetWriter(table_path, SCHEMA, compression="zstd"))
        term_writer = context.enter_context(pq.ParquetWriter(directory / TERM_FILE, TERM_SCHEMA, compression="zstd")) if extended else None
        for name in names:
            data = model.constraints[name].data
            labels = data.labels
            total = active = 0
            chunks = range(0, labels.sizes["snapshot"], chunk_hours) if "snapshot" in labels.dims else [0]
            for start in chunks:
                part = data.isel(snapshot=slice(start, start + chunk_hours)) if "snapshot" in labels.dims else data
                frame = _rows(name, part, solution)
                total += part.labels.size
                active += len(frame)
                writer.write_table(pa.Table.from_pandas(frame, schema=SCHEMA, preserve_index=False))
                if extended:
                    terms = term_rows(part)
                    matched = terms.merge(frame[["constraint_label", "raw_dual"]],
                                          on="constraint_label", validate="many_to_one")
                    np.add.at(gradient, matched.variable_label.to_numpy(int),
                              (matched.coefficient * matched.raw_dual).to_numpy())
                    term_count += len(terms)
                    term_writer.write_table(pa.Table.from_pandas(terms, schema=TERM_SCHEMA, preserve_index=False))
            summaries.append({"constraint": name, "coordinate_slots": total,
                              "active_rows": active, "inactive_slots": total - active,
                              "dimensions": list(labels.dims),
                              "assets": labels.coords["name"].values.astype(str).tolist() if "name" in labels.dims else [],
                              "status": "CAPTURED"})
        for name in RAMPS:
            if name not in names:
                summaries.append({"constraint": name, "status": "NOT_APPLICABLE",
                                  "reason": "No native constraint was constructed for this ramp direction"})
    extra = {}
    if extended:
        extra["variables"] = capture_variables(model, directory, solution, chunk_hours=chunk_hours,
                                               coefficients=objective)
        extra["terms"] = {"path": TERM_FILE, "sha256": sha256_file(directory / TERM_FILE), "rows": term_count}
        extra["electrical_population"] = capture_population(n, directory, chunk_hours=chunk_hours)
    manifest = {"schema_version": "MEM_FIXED_LP_CONSTRAINT_EVIDENCE_V2" if extended else "MEM_FIXED_LP_CONSTRAINT_EVIDENCE_V1",
                "provenance": provenance, "snapshot_count": len(n.snapshots),
                "snapshot_start": str(n.snapshots[0]), "snapshot_end": str(n.snapshots[-1]),
                "snapshot_sha256": sha256(n.snapshots.asi8.tobytes()).hexdigest(),
                "table": table_path.name, "table_sha256": sha256_file(table_path),
                "rows": sum(item.get("active_rows", 0) for item in summaries),
                "constraints": summaries, "inactive_entries_filled": False,
                "dual_scaling": "Raw native constraint sign and scale; Bus price normalization remains native PyPSA",
                "authority": "Matched fixed-commitment LP primal and dual; not UC physical reporting",
                "optimization_model_constructed_by_capture": False, "solver_invocations_by_capture": 0}
    if extended:
        residuals = {}
        for name in ("Generator-p", "Link-p", "Store-p", "Store-e"):
            if name not in model.variables:
                continue
            ids = model.variables[name].labels.to_numpy().reshape(-1).astype(int)
            ids = ids[ids >= 0]
            residuals[name] = {"native_variables": len(ids),
                "max_absolute_stationarity_residual": float(np.abs(objective[ids] - gradient[ids]).max(initial=0.))}
        manifest.update(additional_tables=extra, supporting_inputs=supporting_inputs or {},
                        stationarity_scope="ALL_NATIVE_CONSTRAINT_FAMILIES_WITH_DISPATCH_OR_STORE_STATE_INCIDENCE",
                        excluded_constraint_families=excluded,
                        excluded_family_reason="No active incidence on Generator-p, Link-p, Store-p or Store-e",
                        economic_decomposition_status="NATIVE_PRIMITIVES_RETAINED_NOT_A_VALIDATED_ECONOMIC_STACK",
                        stationarity_diagnostics=residuals,
                        stationarity_convention="Actual objective coefficient minus sum(native coefficient * raw dual); no dual rescaling",
                        chronology_convention="Accepted UTC hourly snapshots; naive timestamps retain the original network convention")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"path": str(manifest_path), "sha256": sha256_file(manifest_path),
            "table_path": str(table_path), "table_sha256": manifest["table_sha256"], "rows": manifest["rows"],
            "extended_resource_capture": extended}


def bind_price_evidence(directory: Path, evidence: dict, solved_sha256: str):
    """Bind new pre-export capture to its actual exported solved-network bytes."""
    path = Path(directory) / Path(evidence["path"]).name
    if sha256_file(path) != evidence["sha256"] or len(solved_sha256) != 64:
        raise RuntimeError("PRICE_EVIDENCE_BINDING_CONFLICT")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest["provenance"].get("solved_sha256"):
        raise RuntimeError("PRICE_EVIDENCE_ALREADY_BOUND")
    manifest["provenance"]["solved_sha256"] = solved_sha256
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**evidence, "sha256": sha256_file(path)}


def verify_price_evidence(directory: Path, evidence: dict, receipt: dict):
    """Validate same-solve evidence hashes; old receipts without capture remain valid."""
    manifest_path = Path(directory) / Path(evidence["path"]).name
    table_path = Path(directory) / Path(evidence["table_path"]).name
    if sha256_file(manifest_path) != evidence["sha256"] or sha256_file(table_path) != evidence["table_sha256"]:
        raise RuntimeError("PRICE_EVIDENCE_HASH_MISMATCH")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    provenance = manifest["provenance"]
    for field in ("year", "scenario", "kind", "input_sha256", "fixed_commitment_milp_sha256"):
        if provenance.get(field) != receipt.get(field):
            raise RuntimeError("PRICE_EVIDENCE_LINEAGE_MISMATCH: " + field)
    if manifest["table_sha256"] != evidence["table_sha256"] or pq.ParquetFile(table_path).metadata.num_rows != manifest["rows"]:
        raise RuntimeError("PRICE_EVIDENCE_ROW_MISMATCH")
    if manifest.get("schema_version") == "MEM_FIXED_LP_CONSTRAINT_EVIDENCE_V2":
        for field, expected in (("job_id", receipt.get("job_id")),
                                ("result_branch", receipt.get("final_methodology_variant")),
                                ("solved_sha256", receipt.get("solved_sha256"))):
            if not expected or provenance.get(field) != expected:
                raise RuntimeError("PRICE_EVIDENCE_LINEAGE_MISMATCH: " + field)
        if set(manifest.get("additional_tables", {})) != {"variables", "terms", "electrical_population"}:
            raise RuntimeError("PRICE_EVIDENCE_INCOMPLETE_RESOURCE_CAPTURE")
        for item in manifest["additional_tables"].values():
            sidecar = Path(directory) / item["path"]
            if (Path(item["path"]).name != item["path"] or sha256_file(sidecar) != item["sha256"]
                    or pq.ParquetFile(sidecar).metadata.num_rows != item["rows"]):
                raise RuntimeError("PRICE_EVIDENCE_SIDECAR_CONFLICT")
    return {"status": "VERIFIED", "rows": manifest["rows"], "manifest_sha256": evidence["sha256"],
            "source_solved_sha256": receipt["solved_sha256"],
            "resource_capture": "COMPLETE_NATIVE_PRIMITIVES" if manifest.get("additional_tables") else "CONSTRAINT_ROWS_ONLY"}

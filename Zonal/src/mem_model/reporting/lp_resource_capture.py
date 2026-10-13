"""Chunked native LP variables and electrical attachments for economic analysis.

These are solved-point primitives, not submitted offers or inferred economic
values. No model is constructed, matrix assembled, or solver backend read.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..common import sha256_file


VARIABLE_FILE = "RAW_PRICE_VARIABLE_EVIDENCE.parquet"
TERM_FILE = "RAW_PRICE_TERM_EVIDENCE.parquet"
POPULATION_FILE = "RAW_PRICE_ELECTRICAL_POPULATION.parquet"
VARIABLE_SCHEMA = pa.schema([
    ("variable_label", pa.int64()), ("variable_name", pa.string()),
    ("snapshot", pa.timestamp("ns")), ("asset_id", pa.string()),
    ("solution", pa.float64()), ("native_lower", pa.float64()),
    ("native_upper", pa.float64()), ("objective_coefficient", pa.float64()),
])
TERM_SCHEMA = pa.schema([
    ("constraint_label", pa.int64()), ("variable_label", pa.int64()),
    ("coefficient", pa.float64()),
])
POPULATION_SCHEMA = pa.schema([
    ("snapshot", pa.timestamp("ns")), ("zone", pa.string()),
    ("component", pa.string()), ("asset_id", pa.string()),
    ("terminal", pa.string()), ("native_variable_name", pa.string()),
    ("native_variable_label", pa.int64()), ("native_power", pa.float64()),
    ("electrical_coefficient", pa.float64()), ("electrical_injection_MW", pa.float64()),
    ("native_operating_lower", pa.float64()), ("native_operating_upper", pa.float64()),
    ("operating_lower_MW", pa.float64()), ("operating_upper_MW", pa.float64()),
    ("marginal_cost_native", pa.float64()), ("fixed_status", pa.float64()),
    ("objective_weight", pa.float64()), ("generator_weight", pa.float64()),
    ("store_weight", pa.float64()), ("quantity_status", pa.string()),
])
MARKETS = ("NORD", "CNOR", "CSUD", "SUD", "CALA", "SICI", "SARD")
STATIC_FIELDS = ("bus", "bus0", "bus1", "carrier", "sign", "p_nom", "e_nom",
                 "committable", "efficiency", "p_nom_extendable", "e_nom_extendable",
                 "e_cyclic", "e_initial", "standing_loss", "marginal_cost")


def objective_coefficients(model, size):
    """Read the actual linear objective; absent native terms are known zeros."""
    objective = model.objective
    if not objective.is_linear or objective.sense != "min":
        raise RuntimeError("PRICE_EVIDENCE_REQUIRES_LINEAR_MIN_OBJECTIVE")
    labels = objective.vars.to_numpy().reshape(-1).astype(int)
    coefficients = objective.coeffs.to_numpy().reshape(-1)
    active = labels >= 0
    if active.any() and (labels[active].max() >= size or
                         not np.isfinite(coefficients[active]).all()):
        raise RuntimeError("PRICE_EVIDENCE_INVALID_OBJECTIVE_TERM")
    result = np.zeros(size, dtype=float)
    np.add.at(result, labels[active], coefficients[active])
    return result


def variable_rows(name, variable, coefficients):
    labels = variable.labels
    if set(labels.dims) - {"snapshot", "name"}:
        raise RuntimeError("PRICE_EVIDENCE_UNSUPPORTED_VARIABLE_DIMENSIONS: " + name)
    if labels.ndim:
        frame = labels.to_dataframe(name="variable_label").reset_index()
    else:
        frame = pd.DataFrame({"variable_label": [int(labels.item())]})
    if "snapshot" not in frame:
        frame["snapshot"] = pd.NaT
    frame["asset_id"] = frame.pop("name").astype(str) if "name" in frame else ""
    frame["variable_name"] = name
    for field, attribute in (("solution", "solution"), ("native_lower", "lower"), ("native_upper", "upper")):
        frame[field] = getattr(variable, attribute).broadcast_like(labels).transpose(*labels.dims).to_numpy().reshape(-1)
    frame = frame.loc[frame.variable_label.ge(0)].copy()
    if frame.variable_label.duplicated().any() or not np.isfinite(frame.solution.to_numpy()).all():
        raise RuntimeError("PRICE_EVIDENCE_INVALID_VARIABLE_POPULATION")
    frame["objective_coefficient"] = coefficients[frame.variable_label.to_numpy(int)]
    return frame[list(VARIABLE_SCHEMA.names)]


def term_rows(data):
    """Original native incidence; inactive rows/terms and exact zeros omitted."""
    labels = data.labels
    dimensions = set(data.vars.dims) - set(labels.dims)
    if len(dimensions) != 1:
        raise RuntimeError("PRICE_EVIDENCE_UNSUPPORTED_TERMS")
    term = next(iter(dimensions))
    variables = data.vars.transpose(*labels.dims, term).to_numpy().astype(int)
    coefficients = data.coeffs.transpose(*labels.dims, term).to_numpy()
    rows = np.broadcast_to(labels.to_numpy()[..., None], variables.shape)
    active = (rows >= 0) & (variables >= 0)
    if not np.isfinite(coefficients[active]).all():
        raise RuntimeError("PRICE_EVIDENCE_NONFINITE_COEFFICIENT")
    active &= coefficients != 0
    return pd.DataFrame({"constraint_label": rows[active].astype(int),
                         "variable_label": variables[active], "coefficient": coefficients[active]})


def capture_variables(model, directory, solution, *, chunk_hours, coefficients=None):
    if coefficients is None:
        coefficients = objective_coefficients(model, len(solution))
    path = Path(directory) / VARIABLE_FILE
    count = 0
    families = []
    with pq.ParquetWriter(path, VARIABLE_SCHEMA, compression="zstd") as writer:
        for name, variable in model.variables.items():
            labels = variable.labels
            active = 0
            chunks = range(0, labels.sizes["snapshot"], chunk_hours) if "snapshot" in labels.dims else [0]
            for start in chunks:
                part = variable.isel(snapshot=slice(start, start + chunk_hours)) if "snapshot" in labels.dims else variable
                frame = variable_rows(name, part, coefficients)
                writer.write_table(pa.Table.from_pandas(frame, schema=VARIABLE_SCHEMA, preserve_index=False))
                active += len(frame)
            families.append({"variable": name, "active_rows": active,
                             "coordinate_slots": labels.size, "dimensions": list(labels.dims)})
            count += active
    return {"path": path.name, "sha256": sha256_file(path), "rows": count,
            "families": families, "objective_sense": model.objective.sense,
            "objective_type": "LINEAR", "absent_objective_terms": "NATIVE_PROVEN_ZERO"}


def _dense(n, table, field, name, snapshots, *, output=False, inactive_zero=False):
    static = getattr(n, table)
    frame = getattr(n, table + "_t").get(field)
    if frame is not None and name in frame:
        if not frame.index.equals(n.snapshots):
            raise RuntimeError("PRICE_EVIDENCE_RESOURCE_CHRONOLOGY")
        result = frame.loc[snapshots, name].to_numpy(float)
    elif output and inactive_zero:
        # A missing native variable proves an inactive attachment, not dispatch.
        result = np.zeros(len(snapshots), dtype=float)
    elif output:
        raise RuntimeError("PRICE_EVIDENCE_RESOURCE_PRIMAL_MISSING: " + field)
    elif field in static:
        result = np.full(len(snapshots), float(static.at[name, field]))
    else:
        raise RuntimeError("PRICE_EVIDENCE_RESOURCE_ATTRIBUTE_MISSING: " + field)
    if not np.isfinite(result).all():
        raise RuntimeError("PRICE_EVIDENCE_NONFINITE_RESOURCE")
    return result


def _variable_labels(n, family, name, snapshots):
    if family not in n.model.variables:
        return np.full(len(snapshots), -1, dtype=int)
    labels = n.model.variables[family].labels
    if "name" not in labels.coords or name not in labels.coords["name"]:
        return np.full(len(snapshots), -1, dtype=int)
    return labels.sel(snapshot=snapshots, name=name).to_numpy().astype(int)


def enumerate_population(n):
    """Enumerate every Italian electrical attachment before any root selection."""
    attachments = []
    for component, table in (("Generator", "generators"), ("Load", "loads"), ("Store", "stores")):
        for name, row in getattr(n, table).iterrows():
            if str(row.bus) in MARKETS:
                attachments.append({"component": component, "table": table, "asset_id": str(name),
                                    "zone": str(row.bus), "terminal": "bus"})
    for name, row in n.links.iterrows():
        if any(str(row.get(port, "")) for port in ("bus2", "bus3", "bus4")):
            raise RuntimeError("PRICE_EVIDENCE_UNSUPPORTED_MULTIPORT_LINK")
        for port in ("bus0", "bus1"):
            if str(row[port]) in MARKETS:
                attachments.append({"component": "Link", "table": "links", "asset_id": str(name),
                                    "zone": str(row[port]), "terminal": port})
    if not attachments:
        raise RuntimeError("PRICE_EVIDENCE_EMPTY_ELECTRICAL_POPULATION")
    return attachments


def capture_population(n, directory, *, chunk_hours):
    attachments = enumerate_population(n)
    weights = n.snapshot_weightings.loc[:, ["objective", "generators", "stores"]]
    if (not weights.index.equals(n.snapshots) or not np.isfinite(weights.to_numpy()).all()
            or not weights.gt(0).all().all()):
        raise RuntimeError("PRICE_EVIDENCE_INVALID_WEIGHTS")
    path = Path(directory) / POPULATION_FILE
    count = 0
    with pq.ParquetWriter(path, POPULATION_SCHEMA, compression="zstd") as writer:
        for start in range(0, len(n.snapshots), chunk_hours):
            snapshots = n.snapshots[start:start + chunk_hours]
            frames = []
            for attachment in attachments:
                component, table, name = (attachment[k] for k in ("component", "table", "asset_id"))
                row = getattr(n, table).loc[name]
                status = np.ones(len(snapshots))
                if component == "Link":
                    family = "Link-p"
                    labels = _variable_labels(n, family, name, snapshots)
                    power = _dense(n, table, "p0", name, snapshots, output=True,
                                   inactive_zero=bool((labels < 0).all()))
                    lo = float(row.p_nom) * _dense(n, table, "p_min_pu", name, snapshots)
                    hi = float(row.p_nom) * _dense(n, table, "p_max_pu", name, snapshots)
                    factor = -np.ones(len(snapshots)) if attachment["terminal"] == "bus0" else _dense(n, table, "efficiency", name, snapshots)
                    cost = _dense(n, table, "marginal_cost", name, snapshots)
                else:
                    family = component + "-p" if component != "Load" else ""
                    labels = _variable_labels(n, family, name, snapshots)
                    power = _dense(n, table, "p", name, snapshots, output=True,
                                   inactive_zero=component != "Load" and bool((labels < 0).all()))
                    factor = np.full(len(snapshots), float(row.sign))
                    if component == "Generator":
                        if bool(row.committable):
                            status = _dense(n, table, "status", name, snapshots, output=True)
                        lo = float(row.p_nom) * _dense(n, table, "p_min_pu", name, snapshots) * status
                        hi = float(row.p_nom) * _dense(n, table, "p_max_pu", name, snapshots) * status
                        cost = _dense(n, table, "marginal_cost", name, snapshots)
                    elif component == "Load":
                        lo = hi = _dense(n, table, "p_set", name, snapshots)
                        if not np.array_equal(power, lo):
                            raise RuntimeError("PRICE_EVIDENCE_FIXED_LOAD_PRIMAL_MISMATCH")
                        cost = np.full(len(snapshots), np.nan)
                    else:
                        # Store power has no native rating; no invented offer bound.
                        lo, hi = np.full(len(snapshots), -np.inf), np.full(len(snapshots), np.inf)
                        cost = _dense(n, table, "marginal_cost", name, snapshots)
                if component != "Load" and (labels >= 0).any():
                    solved = n.model.variables[family].solution.sel(snapshot=snapshots, name=name).to_numpy()
                    active = labels >= 0
                    if not np.array_equal(power[active], solved[active]):
                        raise RuntimeError("PRICE_EVIDENCE_RESOURCE_PRIMAL_MISMATCH")
                lower, upper = np.minimum(lo * factor, hi * factor), np.maximum(lo * factor, hi * factor)
                frame = pd.DataFrame({"snapshot": snapshots, "zone": attachment["zone"],
                    "component": component, "asset_id": name, "terminal": attachment["terminal"],
                    "native_variable_name": family, "native_variable_label": labels,
                    "native_power": power, "electrical_coefficient": factor, "electrical_injection_MW": power * factor,
                    "native_operating_lower": lo, "native_operating_upper": hi,
                    "operating_lower_MW": lower, "operating_upper_MW": upper,
                    "marginal_cost_native": cost, "fixed_status": status,
                    "objective_weight": weights.loc[snapshots, "objective"].to_numpy(),
                    "generator_weight": weights.loc[snapshots, "generators"].to_numpy(),
                    "store_weight": weights.loc[snapshots, "stores"].to_numpy(),
                    "quantity_status": "FIXED_EXOGENOUS_WITHDRAWAL" if component == "Load" else "NATIVE_TERMINAL_PRIMAL"})
                frames.append(frame)
            frame = pd.concat(frames, ignore_index=True)
            writer.write_table(pa.Table.from_pandas(frame, schema=POPULATION_SCHEMA, preserve_index=False))
            count += len(frame)
    metadata = {}
    for table in ("buses", "generators", "loads", "links", "stores", "global_constraints"):
        data = getattr(n, table)
        fields = [field for field in (*STATIC_FIELDS, "type", "sense", "constant", "carrier_attribute") if field in data]
        # Preserve names/physical parameters, not local path or execution metadata.
        metadata[table] = json.loads(data[fields].to_json(orient="index"))
    return {"path": path.name, "sha256": sha256_file(path), "rows": count,
            "attachments": [{k: v for k, v in a.items() if k != "table"} for a in attachments],
            "market_zones": list(MARKETS), "population_basis": "INDEPENDENT_NATIVE_MARKET_BUS_ATTACHMENT_ENUMERATION",
            "includes_zero_dispatch": True, "directional_quantities": "SUPPLY=max(injection,0); DEMAND=max(-injection,0); shared native variable",
            "operating_bounds": "Native fixed-status bounds; not submitted offer quantities",
            "metadata": metadata,
            "snapshot_weights": {str(k): weights[k].to_list() for k in weights},
            "economic_values": "NOT_INFERRED; native primitives require validated decomposition"}

"""Complete fixed-LP population plus same-run price/root/regime annotations.

No network loader, KKT inference or candidate rediscovery. Upstream reporting
must supply accepted electrical decompositions for all relevant asset-hours,
including zero dispatch and both supply and demand. Root-only caches are never
population evidence. Input-contract V2 is not a model-methodology version;
this optional consumer is not activated by routine execution or reporting.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

from ..common import ROOT, sha256_file
from .marginal_setter_attribution import POWER_TOLERANCE, TOLERANCE, ZONES, normalize_zone
from .marginal_setter_resolver import IDENTITY

VERSION = "MEM_ECONOMIC_STACK_INPUT_CONTRACT_V2"
BUNDLE_SCHEMA = "MEM_FIXED_LP_ECONOMIC_EVIDENCE_BUNDLE_V1"
CONFIG = ROOT / "config/economic_offer_stack.yaml"
STATUS = "REVIEW / REFERENCE ONLY | NOT CANONICAL | NOT ROUTINE | COMPLETE SUPPORTED FIXED-LP EVIDENCE REQUIRED"
SOURCE_FIELDS = ("model_year", "scenario", "source_fixed_lp_id", "source_fixed_lp_hash", "source_result_branch")
RESOURCE_KEYS = ("model_year", "scenario", "snapshot", "zone", "asset_id", "component", "side")
RESOURCE_SCHEMA = (*RESOURCE_KEYS, "technology", "carrier", "economic_group",
    "dispatched_MW", "operating_lower_MW", "operating_upper_MW", "available_MW",
    "marginal_cost_EUR_MWh", "marginal_cost_basis", "direct_cost_contribution_EUR_MWh",
    "opportunity_value_EUR_MWh", "constraint_value_EUR_MWh", "effective_marginal_value_EUR_MWh",
    "source_fixed_lp_id", "source_fixed_lp_hash", "source_result_branch",
    "quantity_status", "derivation_method", "evidence_status", "unresolved_reason")
PRICE_SCHEMA = ("snapshot", "zone", "zonal_price_EUR_MWh", *SOURCE_FIELDS)
ROOT_SCHEMA = ("snapshot", "target_zone", *IDENTITY, "remote", "coupling_path",
    "position", "p", "cost", "opportunity", "recon", "residual", "native_power",
    "operating_lower", "operating_upper", "efficiency", "objective_weight", *SOURCE_FIELDS)
REGIME_SCHEMA = ("snapshot", "zone", "primary_attribution_class", "final_regime_category", "resolution_scope", "valid_root_count", *SOURCE_FIELDS)
ECONOMIC_FIELDS = ("direct_cost_contribution_EUR_MWh", "opportunity_value_EUR_MWh",
                   "constraint_value_EUR_MWh", "effective_marginal_value_EUR_MWh")
EVIDENCE_STATUSES = ("SUPPORTED", "UNRESOLVED", "NOT_APPLICABLE")
SOURCE_EVIDENCE_LAYERS = ("fixed_lp_solve_receipt", "fixed_lp_verification",
                        "native_price_evidence",
                        "validated_root_qa", "validated_root_candidates", "frozen_root_methodology",
                        "marginal_regime_receipt", "marginal_regime_data", "population_enumeration",
                        "decomposition_validation")
_VERIFIED = object()


class EvidenceContractError(ValueError):
    pass


def fail(code, detail):
    raise EvidenceContractError(f"{code}: {detail}")


def configuration(path=CONFIG):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _require_columns(frame, columns, layer):
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED" if layer in ("resources", "population_index") else "MISSING_MATCHING_EVIDENCE",
             f"{layer}: missing {missing}")


def _check_identity(identity, expected, layer):
    if any(key not in identity or identity[key] is None for key in SOURCE_FIELDS):
        fail("MISSING_MATCHING_EVIDENCE", f"{layer}: explicit fixed-LP identity required")
    if isinstance(identity["model_year"], bool) or not isinstance(identity["model_year"], (int, np.integer)):
        fail("MISSING_MATCHING_EVIDENCE", f"{layer}: integer model_year required")
    for key in ("scenario", "source_fixed_lp_id", "source_fixed_lp_hash", "source_result_branch"):
        if not isinstance(identity[key], str) or not identity[key].strip():
            fail("MISSING_MATCHING_EVIDENCE", f"{layer}: nonempty {key} required")
    digest = identity["source_fixed_lp_hash"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        fail("MISSING_MATCHING_EVIDENCE", f"{layer}: fixed-LP SHA256 required")
    if any(identity[key] != expected[key] for key in SOURCE_FIELDS):
        fail("SOURCE_LINEAGE_MISMATCH", f"{layer}: fixed-LP id/hash/branch/year/scenario differ")


def _check_frame_identity(frame, expected, layer):
    _require_columns(frame, SOURCE_FIELDS, layer)
    for key in SOURCE_FIELDS:
        if frame[key].isna().any() or not frame[key].eq(expected[key]).all():
            fail("SOURCE_LINEAGE_MISMATCH", f"{layer}: inconsistent row-level {key}")


def _prepare_frame(frame):
    result = frame.copy()
    _require_columns(result, ["snapshot"], "evidence")
    result["snapshot"] = pd.to_datetime(result.snapshot, errors="raise")
    if result.snapshot.isna().any() or result.snapshot.dt.tz is not None:
        fail("MISSING_MATCHING_EVIDENCE", "nonnull UTC-naive snapshots required")
    for key in ("zone", "target_zone"):
        if key in result:
            result[key] = result[key].map(normalize_zone)
    return result


def _keys(frame):
    return pd.MultiIndex.from_frame(frame[list(RESOURCE_KEYS)])


def _numeric(frame, columns, layer, *, nullable=False):
    try:
        values = frame[list(columns)].to_numpy(dtype=float)
    except (TypeError, ValueError):
        fail("INVALID_ECONOMIC_EVIDENCE", f"{layer}: numeric {list(columns)} required")
    valid = np.isfinite(values) | (np.isnan(values) if nullable else False)
    if not valid.all():
        fail("INVALID_ECONOMIC_EVIDENCE", f"{layer}: nonfinite {list(columns)}")
    return values


def _nonempty_labels(frame, columns, layer):
    for column in columns:
        if not frame[column].map(lambda value: isinstance(value, str) and bool(value.strip())).all():
            fail("INVALID_ECONOMIC_EVIDENCE", f"{layer}: explicit nonempty {column} required")


def _supported_decomposition(resources):
    return resources.evidence_status.eq("SUPPORTED") & resources[list(ECONOMIC_FIELDS)].notna().all(axis=1)


def _validate_population(resources, inventory):
    _require_columns(resources, RESOURCE_SCHEMA, "resources")
    _require_columns(inventory, RESOURCE_KEYS, "population_index")
    if resources.empty or inventory.empty or resources[list(RESOURCE_KEYS)].isna().any().any() or inventory[list(RESOURCE_KEYS)].isna().any().any():
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "empty/null asset-hour population")
    if _keys(resources).has_duplicates or _keys(inventory).has_duplicates:
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "duplicated asset-hour keys")
    if len(resources) != len(inventory) or not _keys(resources).sort_values().equals(_keys(inventory).sort_values()):
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "resource rows differ from independent full asset-hour inventory")
    if not resources.side.isin(["SUPPLY", "DEMAND"]).all():
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "electrical directions must be explicit upstream")
    if not resources.zone.isin(ZONES).all() or not inventory.zone.isin(ZONES).all():
        fail("INVALID_ECONOMIC_EVIDENCE", "resource population must use MEM market zones")
    labels = ["asset_id", "component", "technology", "carrier", "economic_group",
              "marginal_cost_basis", "quantity_status", "derivation_method", "evidence_status"]
    _nonempty_labels(resources, labels, "resources")
    _nonempty_labels(inventory, ["asset_id", "component", "side"], "population_index")
    quantities = _numeric(resources, ("dispatched_MW", "operating_lower_MW", "operating_upper_MW", "available_MW"), "resources")
    p, lower, upper, available = quantities.T
    if ((quantities < 0).any() or (lower > upper + POWER_TOLERANCE).any()
            or (p < lower - POWER_TOLERANCE).any() or (p > upper + POWER_TOLERANCE).any()
            or (p > available + POWER_TOLERANCE).any()
            or (np.abs(available - upper) > POWER_TOLERANCE).any()):
        fail("INVALID_ECONOMIC_EVIDENCE", "directional dispatch/bounds/operating availability inconsistent")
    values = _numeric(resources, ECONOMIC_FIELDS, "resources", nullable=True)
    cost = _numeric(resources, ("marginal_cost_EUR_MWh",), "resources", nullable=True)[:, 0]
    if not resources.evidence_status.isin(EVIDENCE_STATUSES).all():
        fail("INVALID_ECONOMIC_EVIDENCE", f"evidence_status must be one of {EVIDENCE_STATUSES}")
    supported = resources.evidence_status.eq("SUPPORTED").to_numpy()
    if not np.isfinite(values[supported]).all() or not np.isfinite(cost[supported]).all():
        fail("INVALID_ECONOMIC_EVIDENCE", "SUPPORTED rows require complete finite costs and decomposition")
    if np.any(np.abs(values[supported, :3].sum(axis=1) - values[supported, 3]) > TOLERANCE):
        fail("INVALID_ECONOMIC_EVIDENCE", "direct + opportunity + constraint differs from effective marginal value")
    unresolved = ~supported
    reasons = resources.unresolved_reason.map(lambda value: isinstance(value, str) and bool(value.strip())).to_numpy()
    if not reasons[unresolved].all():
        fail("INVALID_ECONOMIC_EVIDENCE", "unresolved/not-applicable economics require an explicit unresolved_reason")
    fixed = resources.component.eq("Load")
    if (fixed & (~resources.side.eq("DEMAND") | ~resources.evidence_status.eq("NOT_APPLICABLE"))).any():
        fail("INVALID_ECONOMIC_EVIDENCE", "fixed Loads require DEMAND/NOT_APPLICABLE economics")
    if np.isfinite(values[fixed.to_numpy()]).any() or np.isfinite(cost[fixed.to_numpy()]).any():
        fail("INVALID_ECONOMIC_EVIDENCE", "fixed Load willingness-to-pay or marginal costs must remain unknown")
    fixed_mask = fixed.to_numpy()
    if (np.any(np.abs(lower[fixed_mask] - p[fixed_mask]) > POWER_TOLERANCE)
            or np.any(np.abs(upper[fixed_mask] - p[fixed_mask]) > POWER_TOLERANCE)):
        fail("INVALID_ECONOMIC_EVIDENCE", "fixed Load lower/upper quantities must equal its fixed withdrawal")
    if (resources.evidence_status.eq("NOT_APPLICABLE") & ~fixed & resources.dispatched_MW.gt(0)).any():
        fail("INVALID_ECONOMIC_EVIDENCE", "active optimized resource economics cannot be NOT_APPLICABLE")
    # Unresolved idle/optimized rows retain supplied nulls. They cannot become
    # supported merely because an effective value or a root annotation exists.


@dataclass
class EconomicEvidenceBundle:
    resources: pd.DataFrame
    prices: pd.DataFrame
    roots: pd.DataFrame
    regimes: pd.DataFrame
    population_index: pd.DataFrame
    source_identity: dict
    provenance: dict
    _verified: object = None


@dataclass
class ZoneHourStack:
    bundle: EconomicEvidenceBundle
    records: pd.DataFrame
    root_evidence: pd.DataFrame
    snapshot: pd.Timestamp
    zone: str


def _validate_frames(frames, identity):
    for name, schema in (("resources", RESOURCE_SCHEMA), ("prices", PRICE_SCHEMA), ("roots", ROOT_SCHEMA), ("regimes", REGIME_SCHEMA)):
        _require_columns(frames[name], schema, name)
        _check_frame_identity(frames[name], identity, name)
    _check_frame_identity(frames["population_index"], identity, "population_index")
    _validate_population(frames["resources"], frames["population_index"])
    for name in ("prices", "regimes"):
        if frames[name].empty or frames[name].duplicated(["snapshot", "zone"]).any():
            fail("MISSING_MATCHING_EVIDENCE", f"{name}: empty/duplicate zone-hour")
        if not frames[name].zone.isin(ZONES).all():
            fail("INVALID_ECONOMIC_EVIDENCE", f"{name}: invalid market zone")
    if frames["roots"].empty:
        fail("MISSING_MATCHING_EVIDENCE", "empty selected-root layer")
    _numeric(frames["prices"], ("zonal_price_EUR_MWh",), "prices")
    p = frames["prices"].set_index(["snapshot", "zone"]).zonal_price_EUR_MWh
    chronology = pd.DatetimeIndex(frames["prices"].snapshot.drop_duplicates()).sort_values()
    if any(not pd.DatetimeIndex(group.snapshot).sort_values().equals(chronology)
           for _, group in frames["prices"].groupby("zone")):
        fail("MISSING_MATCHING_EVIDENCE", "price zones do not share the same snapshot universe")
    for name in ("resources", "regimes", "population_index"):
        keys = pd.MultiIndex.from_frame(frames[name][["snapshot", "zone"]].drop_duplicates())
        if not keys.sort_values().equals(p.index.sort_values()):
            fail("MISSING_MATCHING_EVIDENCE", f"{name}: zone-hour coverage differs from prices")
    counts = frames["prices"].groupby("zone").snapshot.nunique()
    inventory_counts = frames["population_index"].groupby(["zone", "asset_id", "component", "side"]).snapshot.nunique()
    expected = inventory_counts.index.get_level_values("zone").map(counts).to_numpy()
    if not np.array_equal(inventory_counts.to_numpy(), expected):
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "independent inventory omits asset-hours, including idle rows")
    resources = frames["resources"]
    balance = resources.assign(signed_MW=np.where(resources.side.eq("SUPPLY"), resources.dispatched_MW, -resources.dispatched_MW))
    if balance.groupby(["snapshot", "zone"]).signed_MW.sum().abs().gt(POWER_TOLERANCE).any():
        fail("INVALID_ECONOMIC_EVIDENCE", "complete directional supply and withdrawals do not balance")
    supported = resources.loc[_supported_decomposition(resources)]
    resource_prices = pd.MultiIndex.from_frame(supported[["snapshot", "zone"]]).map(p).to_numpy(float)
    if np.any(np.abs(supported.effective_marginal_value_EUR_MWh.to_numpy(float) - resource_prices) > TOLERANCE):
        fail("INVALID_ECONOMIC_EVIDENCE", "supported decomposition differs from same-LP zonal price")
    if "zonal_price_EUR_MWh" in frames["regimes"]:
        g = frames["regimes"].set_index(["snapshot", "zone"]).zonal_price_EUR_MWh
        if not np.allclose(g, p.reindex(g.index), atol=TOLERANCE, rtol=0):
            fail("SOURCE_LINEAGE_MISMATCH", "regime prices differ from accepted price layer")
    _nonempty_labels(frames["regimes"], ["primary_attribution_class", "final_regime_category", "resolution_scope"], "regimes")
    root_counts = _numeric(frames["regimes"], ("valid_root_count",), "regimes")[:, 0]
    if (root_counts < 1).any() or not np.equal(root_counts, np.floor(root_counts)).all():
        fail("INVALID_ECONOMIC_EVIDENCE", "positive integer valid_root_count required")
    if not frames["regimes"].resolution_scope.isin(("LOCAL", "COUPLED")).all():
        fail("INVALID_ECONOMIC_EVIDENCE", "unknown root resolution scope")
    if not np.array_equal(frames["regimes"].primary_attribution_class.eq("MARKET_COUPLING"), frames["regimes"].resolution_scope.eq("COUPLED")):
        fail("SOURCE_LINEAGE_MISMATCH", "primary attribution and local/coupled scope differ")
    roots = frames["roots"]
    _nonempty_labels(roots, [*IDENTITY, "position"], "roots")
    if not roots.remote.map(lambda value: isinstance(value, (bool, np.bool_))).all():
        fail("INVALID_ECONOMIC_EVIDENCE", "root remote flag must be boolean")
    root_keys = pd.MultiIndex.from_frame(roots[["snapshot", "target_zone"]].drop_duplicates())
    if not root_keys.sort_values().equals(p.index.sort_values()):
        fail("MISSING_MATCHING_EVIDENCE", "selected roots do not cover the exact price/regime zone-hours")
    numbers = ("p", "cost", "recon", "residual", "native_power", "operating_lower", "operating_upper", "efficiency", "objective_weight")
    _numeric(roots, numbers, "roots")
    _numeric(roots, ("opportunity",), "roots", nullable=True)
    if (roots.p.lt(0).any() or roots.efficiency.le(0).any() or roots.objective_weight.le(0).any()
            or roots.operating_upper.sub(roots.operating_lower).le(POWER_TOLERANCE).any()
            or roots.native_power.lt(roots.operating_lower - POWER_TOLERANCE).any()
            or roots.native_power.gt(roots.operating_upper + POWER_TOLERANCE).any()):
        fail("INVALID_ECONOMIC_EVIDENCE", "root quantity/bounds/conversion/weight inconsistent")
    expected_scope = pd.MultiIndex.from_frame(roots[["snapshot", "target_zone"]]).map(frames["regimes"].set_index(["snapshot", "zone"]).resolution_scope)
    if not np.array_equal(roots.remote.to_numpy(), expected_scope == "COUPLED"):
        fail("SOURCE_LINEAGE_MISMATCH", "root selected scope differs from regime")
    if ((~roots.remote & roots.zone.ne(roots.target_zone)) | (roots.remote & roots.zone.eq(roots.target_zone))).any():
        fail("SOURCE_LINEAGE_MISMATCH", "local/remote root bus differs from declared scope")
    target_prices = pd.MultiIndex.from_frame(roots[["snapshot", "target_zone"]]).map(p).to_numpy(float)
    differences = roots.recon.to_numpy(float) - target_prices
    if (np.any(np.abs(differences) > TOLERANCE)
            or np.any(np.abs(roots.residual.to_numpy(float)) > TOLERANCE)
            or np.any(np.abs(differences - roots.residual.to_numpy(float)) > TOLERANCE)):
        fail("INVALID_ECONOMIC_EVIDENCE", "root reconstruction/residual differs from matching fixed-LP price")
    for row in roots.itertuples(index=False):
        try:
            path = json.loads(row.coupling_path)
        except (TypeError, ValueError):
            fail("INVALID_ECONOMIC_EVIDENCE", "coupling_path must be a JSON list")
        if not isinstance(path, list) or not all(isinstance(edge, str) and edge.strip() for edge in path) or bool(path) != row.remote:
            fail("INVALID_ECONOMIC_EVIDENCE", "coupling path must match local/remote scope")
    unique_roots = deduplicate_roots(roots)
    actual_counts = unique_roots.groupby(["snapshot", "target_zone"]).size()
    actual_counts.index = actual_counts.index.set_names(["snapshot", "zone"])
    expected_counts = frames["regimes"].set_index(["snapshot", "zone"]).valid_root_count
    if not actual_counts.sort_index().eq(expected_counts.sort_index()).all():
        fail("MISSING_MATCHING_EVIDENCE", "complete selected root identities differ from valid_root_count")
    local = roots.loc[~roots.remote].copy()
    local["side"] = np.where(local.position.eq("charging") | local.mechanism.eq("P2X_FLEXIBLE_DEMAND"), "DEMAND", "SUPPLY")
    keys = ["snapshot", "zone", "component", "asset_id", "side"]
    matched = local.merge(resources[keys + ["dispatched_MW", "operating_lower_MW", "operating_upper_MW"]], on=keys, how="left", validate="many_to_one")
    if matched.dispatched_MW.isna().any() or matched.p.sub(matched.dispatched_MW).abs().gt(POWER_TOLERANCE).any():
        fail("SOURCE_LINEAGE_MISMATCH", "local root absent from matching electrical side or dispatch differs")
    scale = np.where(matched.component.eq("Link") & matched.position.eq("discharging"), matched.efficiency, 1.)
    if (np.any(np.abs(matched.native_power.to_numpy(float) * scale - matched.p.to_numpy(float)) > POWER_TOLERANCE)
            or np.any(np.abs(matched.operating_lower.to_numpy(float) * scale - matched.operating_lower_MW.to_numpy(float)) > POWER_TOLERANCE)
            or np.any(np.abs(matched.operating_upper.to_numpy(float) * scale - matched.operating_upper_MW.to_numpy(float)) > POWER_TOLERANCE)):
        fail("SOURCE_LINEAGE_MISMATCH", "local root native/electrical power or operating bounds differ from population")


def _validate_source_evidence(manifest, folder, frames, hashes):
    """Verify pinned receipt lineage without loading a network or discovering roots.

    Complete numeric tables alone remain a candidate input. These existing
    source receipts and a future upstream decomposition receipt must demonstrate
    the accepted source and method before an opt-in review can render them.
    """
    specs = manifest.get("source_evidence", {})
    missing = [name for name in SOURCE_EVIDENCE_LAYERS if not specs.get(name)]
    if missing:
        return {"status": "EVIDENCE_GATED", "missing": missing}
    identity = manifest["source_identity"]
    documents = {}
    source_tables = {}
    source_paths = {}
    for name in SOURCE_EVIDENCE_LAYERS:
        spec = specs[name]
        _check_identity(spec.get("source_identity", {}), identity, name)
        path = Path(spec.get("path", ""))
        path = (folder / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_file():
            fail("MISSING_MATCHING_EVIDENCE", f"{name}: pinned source artifact absent")
        digest = sha256_file(path)
        if digest != spec.get("sha256"):
            fail("SOURCE_LINEAGE_MISMATCH", f"{name}: source receipt digest differs")
        hashes[str(path)] = digest
        source_paths[name] = path
        if name in ("validated_root_candidates", "marginal_regime_data"):
            if path.suffix.lower() == ".parquet":
                table = pd.read_parquet(path)
            elif path.suffix.lower() == ".csv":
                table = pd.read_csv(path)
            else:
                fail("MISSING_MATCHING_EVIDENCE", f"{name}: cached CSV/Parquet required")
            source_tables[name] = _prepare_frame(table)
        else:
            if path.suffix.lower() != ".json":
                fail("MISSING_MATCHING_EVIDENCE", f"{name}: JSON source receipt required")
            documents[name] = json.loads(path.read_text(encoding="utf-8"))
    solve = documents["fixed_lp_solve_receipt"]
    expected = {"status": "PASS", "year": identity["model_year"], "scenario": identity["scenario"],
                "kind": "FIXED_COMMITMENT_PRICE_LP", "job_id": identity["source_fixed_lp_id"],
                "solved_sha256": identity["source_fixed_lp_hash"], "solver_status": "ok",
                "termination_condition": "optimal", "integer_variables": 0, "binary_variables": 0}
    if any(solve.get(key) != value for key, value in expected.items()):
        fail("SOURCE_LINEAGE_MISMATCH", "fixed_lp_solve_receipt: unsuccessful/wrong fixed LP")
    for key in ("input_sha256", "fixed_commitment_milp_sha256"):
        digest = solve.get(key, "")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            fail("SOURCE_LINEAGE_MISMATCH", f"fixed_lp_solve_receipt: missing {key}")
    prices = frames["prices"]
    if set(prices.zone) != set(ZONES) or not prices.groupby("zone").snapshot.nunique().eq(solve.get("snapshots")).all():
        fail("MISSING_MATCHING_EVIDENCE", "complete fixed-LP representative-hour universe differs from solve receipt")
    verification = documents["fixed_lp_verification"]
    if any(verification.get(key) != expected[key] for key in ("status", "year", "scenario", "job_id", "solved_sha256")):
        fail("SOURCE_LINEAGE_MISMATCH", "fixed_lp_verification: fixed LP was not verified")
    native = documents["native_price_evidence"]
    native_digest = specs["native_price_evidence"]["sha256"]
    native_provenance = {key: solve[key] for key in ("year", "scenario", "kind", "job_id", "input_sha256", "fixed_commitment_milp_sha256", "solved_sha256")}
    native_provenance["result_branch"] = identity["source_result_branch"]
    if (native.get("schema_version") != "MEM_FIXED_LP_CONSTRAINT_EVIDENCE_V2"
            or any(native.get("provenance", {}).get(key) != value for key, value in native_provenance.items())
            or solve.get("final_methodology_variant") != identity["source_result_branch"]
            or solve.get("price_constraint_evidence", {}).get("sha256") != native_digest
            or native.get("stationarity_scope") != "ALL_NATIVE_CONSTRAINT_FAMILIES_WITH_DISPATCH_OR_STORE_STATE_INCIDENCE"
            or native.get("economic_decomposition_status") != "NATIVE_PRIMITIVES_RETAINED_NOT_A_VALIDATED_ECONOMIC_STACK"):
        fail("SOURCE_LINEAGE_MISMATCH", "native_price_evidence: complete bound native primitive capture required")
    capture_verified = verification.get("price_constraint_evidence", {})
    if (capture_verified.get("status") != "VERIFIED" or capture_verified.get("manifest_sha256") != native_digest
            or capture_verified.get("resource_capture") != "COMPLETE_NATIVE_PRIMITIVES"):
        fail("MISSING_MATCHING_EVIDENCE", "native_price_evidence: fixed-LP verification did not verify complete primitives")
    chronology = pd.DatetimeIndex(prices.snapshot.drop_duplicates()).sort_values()
    if native.get("snapshot_count") != len(chronology) or native.get("snapshot_sha256") != sha256(chronology.asi8.tobytes()).hexdigest():
        fail("SOURCE_LINEAGE_MISMATCH", "native_price_evidence: exact snapshot universe differs")
    additional = native.get("additional_tables", {})
    if set(additional) != {"variables", "terms", "electrical_population"}:
        fail("MISSING_MATCHING_EVIDENCE", "native_price_evidence: variables/coefficients/population sidecars required")
    native_tables = {**additional, "constraints": {"path": native.get("table"), "sha256": native.get("table_sha256"), "rows": native.get("rows")}}
    import pyarrow.parquet as pq
    for name, spec in native_tables.items():
        relative = spec.get("path", "")
        if not isinstance(relative, str) or not relative or Path(relative).name != relative:
            fail("SOURCE_LINEAGE_MISMATCH", f"native_price_evidence.{name}: capture-local sidecar required")
        sidecar = source_paths["native_price_evidence"].parent / relative
        if not sidecar.is_file() or sha256_file(sidecar) != spec.get("sha256"):
            fail("SOURCE_LINEAGE_MISMATCH", f"native_price_evidence.{name}: sidecar digest differs")
        if pq.ParquetFile(sidecar).metadata.num_rows != spec.get("rows"):
            fail("SOURCE_LINEAGE_MISMATCH", f"native_price_evidence.{name}: sidecar row count differs")
        hashes[str(sidecar)] = spec["sha256"]
    qa = documents["validated_root_qa"]
    if (qa.get("status") not in ("PASS_BASE_SEMANTIC_GATE", "PASS_FROZEN_METHOD_ATTRIBUTION")
            or qa.get("methodology_frozen") is not True or qa.get("solver_invocations") != 0
            or qa.get("job_id") != identity["source_fixed_lp_id"]
            or qa.get("fixed_commitment_milp_sha256") != solve["fixed_commitment_milp_sha256"]
            or identity["source_fixed_lp_hash"] not in qa.get("source_hashes", {}).values()
            or qa.get("frozen_methodology_sha256") != specs["frozen_root_methodology"]["sha256"]
            or specs["validated_root_candidates"]["sha256"] not in qa.get("artifact_hashes", {}).values()):
        fail("SOURCE_LINEAGE_MISMATCH", "validated_root_qa: missing frozen matching root lineage")
    regime = documents["marginal_regime_receipt"]
    if (regime.get("status") != "PASS_CANONICAL_MARGINAL_REGIME"
            or regime.get("model_year") != identity["model_year"] or regime.get("scenario") != identity["scenario"]
            or regime.get("methodology") != "LEXICOGRAPHIC_REGIME_V1"
            or specs["validated_root_qa"]["sha256"] not in regime.get("source_hashes", {}).values()
            or specs["marginal_regime_data"]["sha256"] not in regime.get("artifact_hashes", {}).values()):
        fail("SOURCE_LINEAGE_MISMATCH", "marginal_regime_receipt: approved same-LP regime evidence required")
    for layer, name, schema, keys in (
        ("roots", "validated_root_candidates", ROOT_SCHEMA, ["snapshot", "target_zone", *IDENTITY, "coupling_path"]),
        ("regimes", "marginal_regime_data", REGIME_SCHEMA, ["snapshot", "zone"])):
        required = [column for column in schema if column not in SOURCE_FIELDS]
        source = source_tables[name]
        _require_columns(source, required, name)
        # Carry the complete original audit/context, not just the fields used
        # for plotting. No accepted root or regime field is silently dropped.
        columns = [column for column in source.columns if column not in SOURCE_FIELDS]
        _require_columns(frames[layer], columns, layer)
        supplied = frames[layer][columns].sort_values(keys, kind="stable").reset_index(drop=True)
        accepted = source[columns].sort_values(keys, kind="stable").reset_index(drop=True)
        try:
            pd.testing.assert_frame_equal(supplied, accepted, check_dtype=False, check_exact=True)
        except AssertionError:
            fail("SOURCE_LINEAGE_MISMATCH", f"{layer}: complete values/identities differ from validated cached source")
    population = documents["population_enumeration"]
    decomposition = documents["decomposition_validation"]
    for name, document in (("population_enumeration", population), ("decomposition_validation", decomposition)):
        _check_identity(document.get("source_identity", {}), identity, name)
        if document.get("status") != "PASS":
            fail("MISSING_MATCHING_EVIDENCE", f"{name}: upstream verification did not pass")
    index_hash = manifest["layers"]["resources"]["population_index"]["sha256"]
    if (population.get("basis") != "INDEPENDENT_FIXED_LP_ASSET_ENUMERATION"
            or population.get("population_index_sha256") != index_hash
            or population.get("rows") != len(frames["population_index"])):
        fail("SOURCE_LINEAGE_MISMATCH", "population_enumeration: independent exact inventory proof required")
    pins = {"resources_sha256": manifest["layers"]["resources"]["sha256"],
            "prices_sha256": manifest["layers"]["prices"]["sha256"],
            "population_index_sha256": index_hash,
            "native_price_evidence_sha256": native_digest,
            "frozen_methodology_sha256": specs["frozen_root_methodology"]["sha256"]}
    if any(decomposition.get(key) != value for key, value in pins.items()):
        fail("SOURCE_LINEAGE_MISMATCH", "decomposition_validation: input or frozen-method pins differ")
    active = frames["resources"].dispatched_MW.gt(0) & frames["resources"].component.ne("Load")
    if (decomposition.get("complete_active_resource_decomposition") is not True
            or decomposition.get("active_resource_rows") != int(active.sum())
            or not _supported_decomposition(frames["resources"]).loc[active].all()):
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "supported decomposition missing for active optimized resource rows")
    return {"status": "VERIFIED", "missing": [], "source_layers": list(SOURCE_EVIDENCE_LAYERS)}


def load_cached_evidence(bundle_manifest=None, *, model_year, scenario):
    """Only explicit self-contained four-layer same-run bundles; no fallback.

    resources.coverage must attest independent fixed-LP asset enumeration,
    zero-dispatch rows and supply/demand coverage. A separately hash-pinned
    population index must match resource keys exactly. Every layer AND every
    row declares LP id/hash/result branch. A source .nc is never opened.
    """
    if bundle_manifest is None:
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "complete resource bundle required; root cache cannot populate stack")
    path = Path(bundle_manifest).resolve()
    if not path.is_file():
        fail("MISSING_MATCHING_EVIDENCE", f"manifest absent: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    layers = manifest.get("layers", {})
    if "resources" not in layers:
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "no complete resource layer")
    if manifest.get("schema_version") != BUNDLE_SCHEMA:
        fail("MISSING_MATCHING_EVIDENCE", "unsupported bundle contract")
    identity = manifest.get("source_identity", {})
    _check_identity(identity, identity, "bundle")
    digest = identity["source_fixed_lp_hash"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        fail("MISSING_MATCHING_EVIDENCE", "fixed-LP SHA256 required")
    if identity["model_year"] != int(model_year) or identity["scenario"] != scenario:
        fail("SOURCE_LINEAGE_MISMATCH", "requested year/scenario differs")
    coverage = layers["resources"].get("coverage", {})
    if coverage.get("population") != "ALL_RELEVANT_FIXED_LP_RESOURCES" or coverage.get("basis") != "INDEPENDENT_FIXED_LP_ASSET_ENUMERATION" or coverage.get("includes_zero_dispatch") is not True or coverage.get("includes_supply_and_demand") is not True:
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "independent full population attestation required")
    specs = {**layers, "population_index": layers["resources"].get("population_index")}
    frames, hashes = {}, {str(path): sha256_file(path)}
    for name in ("resources", "prices", "roots", "regimes", "population_index"):
        spec = specs.get(name)
        if not spec:
            fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED" if name == "population_index" else "MISSING_MATCHING_EVIDENCE", f"{name}: descriptor absent")
        _check_identity(spec.get("source_identity", {}), identity, name)
        relative = Path(spec.get("path", ""))
        file = (path.parent / relative).resolve()
        if relative.is_absolute() or path.parent not in file.parents:
            fail("SOURCE_LINEAGE_MISMATCH", f"{name}: bundle-local files required, no branch fallback")
        if not file.is_file():
            fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED" if name in ("resources", "population_index") else "MISSING_MATCHING_EVIDENCE", f"{name}: file absent")
        hashes[str(file)] = sha256_file(file)
        if hashes[str(file)] != spec.get("sha256"):
            fail("SOURCE_LINEAGE_MISMATCH", f"{name}: artifact digest differs")
        if file.suffix == ".parquet":
            frame = pd.read_parquet(file)
        elif file.suffix == ".csv":
            frame = pd.read_csv(file)
        else:
            fail("MISSING_MATCHING_EVIDENCE", f"{name}: cached CSV/Parquet required; no networks")
        frames[name] = _prepare_frame(frame)
    _validate_frames(frames, identity)
    source_validation = _validate_source_evidence(manifest, path.parent, frames, hashes)
    return EconomicEvidenceBundle(**frames, source_identity=identity,
        provenance={"manifest": str(path), "artifact_hashes": hashes, "coverage": coverage,
                    "source_evidence_validation": source_validation}, _verified=_VERIFIED)


def persist_evidence_bundle(*, resources, prices, roots, regimes, population_index,
                            source_identity, population_attestation, output, source_evidence=None):
    """Future upstream boundary; accepts ALREADY decomposed same-LP tables.

    Call while accepted fixed-LP reporting context is available. Each input
    already declares its own source id/hash/branch: unknown historical data is
    never stamped with a new identity. Accepted upstream KKT/conversion helpers
    supply all values. This function neither extracts networks nor infers costs.
    population_index must be independently enumerated before marginal filtering.
    """
    _check_identity(source_identity, source_identity, "producer")
    if population_attestation.get("population") != "ALL_RELEVANT_FIXED_LP_RESOURCES" or population_attestation.get("basis") != "INDEPENDENT_FIXED_LP_ASSET_ENUMERATION" or population_attestation.get("includes_zero_dispatch") is not True or population_attestation.get("includes_supply_and_demand") is not True:
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "upstream independent full-population attestation required")
    frames = {name: _prepare_frame(frame) for name, frame in dict(resources=resources, prices=prices,
        roots=roots, regimes=regimes, population_index=population_index).items()}
    _validate_frames(frames, source_identity)
    folder = Path(output).resolve()
    if folder.exists() and any(folder.iterdir()):
        raise ValueError("PROTECTED_OUTPUT_TARGET: new bundle directory required")
    folder.mkdir(parents=True, exist_ok=True)
    specs = {}
    for name, frame in frames.items():
        file = folder / f"{name}.parquet"
        frame.to_parquet(file, index=False)
        specs[name] = {"path": file.name, "sha256": sha256_file(file), "source_identity": dict(source_identity)}
    specs["resources"].update(coverage=dict(population_attestation), population_index=specs.pop("population_index"))
    manifest = folder / "bundle_manifest.json"
    content = {"schema_version": BUNDLE_SCHEMA, "source_identity": source_identity,
        "layers": specs, "decomposition_authority": "UPSTREAM_ACCEPTED_FIXED_LP_KKT_HELPERS; NO_INFERENCE_HERE"}
    if source_evidence is not None:
        content["source_evidence"] = source_evidence
    manifest.write_text(json.dumps(content, indent=2), encoding="utf-8")
    return manifest


def economic_stack_readiness(bundle_manifest=None, *, model_year, scenario):
    """Non-producing integration check; raw capture is not a decomposed bundle.

    EVIDENCE_GATED identifies missing population/decomposition/source layers.
    Successful validation permits only the existing optional review consumer;
    it never activates canonical reporting or invents missing economic values.
    """
    result = {"status": "EVIDENCE_GATED", "input_contract_version": VERSION,
              "model_year": int(model_year), "scenario": scenario,
              "routine_activation": False, "representative_hour_ready": False,
              "missing_evidence": [], "invalid_evidence": [],
              "renderer_limitations": ["SUPPORTING_ROOTS_RETAINED_IN_TABLES_NOT_MARKED_IN_FIGURE"]}
    layers = ("resources", "prices", "roots", "regimes", "population_index")
    if bundle_manifest is None or not Path(bundle_manifest).is_file():
        result["missing_evidence"] = ["bundle_manifest", *layers, "complete_supported_economic_decomposition",
                                      *["source_evidence." + name for name in SOURCE_EVIDENCE_LAYERS]]
        return result
    try:
        path = Path(bundle_manifest)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        specs = manifest.get("layers", {})
        result["missing_evidence"].extend(name for name in layers[:-1] if not specs.get(name))
        if not specs.get("resources", {}).get("population_index"):
            result["missing_evidence"].append("population_index")
        result["missing_evidence"].extend("source_evidence." + name for name in SOURCE_EVIDENCE_LAYERS
                                          if not manifest.get("source_evidence", {}).get(name))
        if any(name in result["missing_evidence"] for name in layers):
            result["missing_evidence"].append("complete_supported_economic_decomposition")
            return result
        bundle = load_cached_evidence(path, model_year=model_year, scenario=scenario)
        active = bundle.resources.dispatched_MW.gt(0) & bundle.resources.component.ne("Load")
        unresolved = bundle.resources.loc[active & ~_supported_decomposition(bundle.resources), list(RESOURCE_KEYS) + ["evidence_status", "unresolved_reason"]]
        if not unresolved.empty:
            result["missing_evidence"].append("complete_supported_economic_decomposition")
            result["unresolved_active_resource_rows"] = len(unresolved)
            result["unresolved_active_resources"] = unresolved.head(20).assign(snapshot=unresolved.head(20).snapshot.astype(str)).to_dict("records")
            result["unresolved_active_resources_truncated"] = len(unresolved) > 20
        result["evidence_scope"] = {"zone_hours": len(bundle.prices), "snapshots": bundle.prices.snapshot.nunique(),
                                    "zones": sorted(bundle.prices.zone.unique().tolist())}
        if result["missing_evidence"]:
            return result
        snapshot, zone, selection = select_representative_hour(bundle)
        result.update(status="READY_FOR_OPT_IN_REVIEW", representative_hour_ready=True,
                      representative_hour={"snapshot": str(snapshot), "zone": zone, **selection})
        from .canonical_results import reporting_config
        rc = reporting_config()
        active_supply = bundle.resources.loc[bundle.resources.snapshot.eq(snapshot) & bundle.resources.zone.eq(zone)
                                           & bundle.resources.side.eq("SUPPLY") & bundle.resources.dispatched_MW.gt(0)]
        family_labels = {"BESS": "BESS", "PHS": "PHS", "Reservoir / pondage hydro": "Hydro - reservoir"}
        missing_styles = sorted({str(row.carrier) for row in active_supply.itertuples(index=False)
                                 if family_labels.get(row.economic_group, rc["carrier_to_display_technology"].get(row.carrier))
                                 not in rc["technology_colors"]})
        if missing_styles:
            result["renderer_limitations"].append("MISSING_EXPLICIT_SUPPLY_STYLES: " + ", ".join(missing_styles))
        result["renderer_complete"] = False
    except (EvidenceContractError, ValueError, KeyError, TypeError, OSError) as exc:
        result["status"] = "EVIDENCE_GATED"
        result["representative_hour_ready"] = False
        if "supported decomposition missing" in str(exc):
            result["missing_evidence"].append("complete_supported_economic_decomposition")
        result["invalid_evidence"].append(str(exc))
    return result


def deduplicate_roots(roots):
    """Dedup annotation identities, NEVER population or resource quantities."""
    keys = ["snapshot", "target_zone", *IDENTITY]
    for col in set(ROOT_SCHEMA) - set(keys) - {"coupling_path"}:
        if col in roots and roots.groupby(keys, dropna=False)[col].nunique(dropna=False).gt(1).any():
            fail("SOURCE_LINEAGE_MISMATCH", f"conflicting duplicate root: {col}")
    out = roots.sort_values(keys + ["coupling_path"], kind="stable").drop_duplicates(keys).copy()
    paths = roots.groupby(keys, dropna=False).coupling_path.agg(lambda s: json.dumps(sorted(set(s))))
    out["all_coupling_paths"] = pd.MultiIndex.from_frame(out[keys]).map(paths)
    return out


def require_complete_stack(stack):
    """Aggregation AND rendering guard: raw root DataFrames are not accepted."""
    if not isinstance(stack, ZoneHourStack) or stack.bundle._verified is not _VERIFIED:
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "validated complete bundle/ZoneHourStack required")
    if stack.bundle.provenance.get("source_evidence_validation", {}).get("status") != "VERIFIED":
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "source/decomposition evidence remains EVIDENCE_GATED")
    expected = stack.bundle.resources.loc[stack.bundle.resources.snapshot.eq(stack.snapshot) & stack.bundle.resources.zone.eq(stack.zone)]
    inventory = stack.bundle.population_index.loc[stack.bundle.population_index.snapshot.eq(stack.snapshot) & stack.bundle.population_index.zone.eq(stack.zone)]
    _check_frame_identity(stack.records, stack.bundle.source_identity, "resources")
    _validate_population(stack.records, inventory)
    # Annotation joins must not replace primal or economic-value fields.
    left = stack.records[list(RESOURCE_SCHEMA)].sort_values(list(RESOURCE_KEYS)).reset_index(drop=True)
    right = expected[list(RESOURCE_SCHEMA)].sort_values(list(RESOURCE_KEYS)).reset_index(drop=True)
    if not left.equals(right):
        fail("SOURCE_LINEAGE_MISMATCH", "stack values differ from full fixed-LP resource input")
    return stack.records


def construct_zone_hour_records(bundle, *, model_year, scenario, snapshot, zone):
    if not isinstance(bundle, EconomicEvidenceBundle) or bundle._verified is not _VERIFIED:
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "root-only evidence cannot populate a stack")
    if bundle.source_identity["model_year"] != int(model_year) or bundle.source_identity["scenario"] != scenario:
        fail("SOURCE_LINEAGE_MISMATCH", "requested result differs from bundle")
    snapshot, zone = pd.Timestamp(snapshot), normalize_zone(zone)
    resources = bundle.resources.loc[bundle.resources.snapshot.eq(snapshot) & bundle.resources.zone.eq(zone)].copy()
    if resources.empty:
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "missing complete zone-hour population")
    price = bundle.prices.loc[bundle.prices.snapshot.eq(snapshot) & bundle.prices.zone.eq(zone)]
    regime = bundle.regimes.loc[bundle.regimes.snapshot.eq(snapshot) & bundle.regimes.zone.eq(zone)]
    roots = deduplicate_roots(bundle.roots.loc[bundle.roots.snapshot.eq(snapshot) & bundle.roots.target_zone.eq(zone)])
    if len(price) != 1 or len(regime) != 1 or roots.empty:
        fail("MISSING_MATCHING_EVIDENCE", "matching price/regime/selected roots required")
    scope = regime.iloc[0].resolution_scope
    if scope not in ("LOCAL", "COUPLED") or not roots.remote.eq(scope == "COUPLED").all():
        fail("SOURCE_LINEAGE_MISMATCH", "root selection scope differs from validated regime")
    # Complete supply/demand rows FIRST; roots add annotation fields ONLY.
    resources["zonal_price_EUR_MWh"] = float(price.iloc[0].zonal_price_EUR_MWh)
    resources["marginal_regime"] = regime.iloc[0].final_regime_category
    resources["resolution_scope"] = scope
    resources["primary_attribution_class"] = regime.iloc[0].primary_attribution_class
    local = roots.loc[~roots.remote]
    local = local.copy()
    local["side"] = np.where(local.position.eq("charging") | local.mechanism.eq("P2X_FLEXIBLE_DEMAND"), "DEMAND", "SUPPLY")
    local_keys = set(zip(local.component, local.asset_id, local.side))
    if not local_keys.issubset(set(zip(resources.component, resources.asset_id, resources.side))):
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "local validated root absent from resource population")
    resources["marginal_root"] = [(c, a, s) in local_keys for c, a, s in zip(resources.component, resources.asset_id, resources.side)]
    audit = {key: g.to_json(orient="records", date_format="iso") for key, g in local.groupby(["component", "asset_id", "side"])}
    mechanisms = {key: json.dumps(sorted(set(g.mechanism))) for key, g in local.groupby(["component", "asset_id", "side"])}
    resources["root_audit_metadata"] = [audit.get((c, a, s)) for c, a, s in zip(resources.component, resources.asset_id, resources.side)]
    resources["root_mechanisms"] = [mechanisms.get((c, a, s)) for c, a, s in zip(resources.component, resources.asset_id, resources.side)]
    resources["headroom_MW"] = resources.available_MW - resources.dispatched_MW
    resources["unused_capacity_valuation"] = "UNRESOLVED_NOT_EXTRAPOLATED"
    # Remote root dispatch NEVER becomes additional target-zone supply/import.
    stack = ZoneHourStack(bundle, resources, roots, snapshot, zone)
    require_complete_stack(stack)
    return stack


def distinguish_quantities(stack):
    result = require_complete_stack(stack).copy()
    result["cleared_supply_block_MW"] = np.where(result.side.eq("SUPPLY"), result.dispatched_MW, 0.)
    result["cleared_withdrawal_MW"] = np.where(result.side.eq("DEMAND"), result.dispatched_MW, 0.)
    return result


def aggregate_technology(stack):
    """Complete cleared supply; distinct accepted values are never averaged."""
    data = distinguish_quantities(stack)
    data = data.loc[data.cleared_supply_block_MW.gt(0)].copy()
    if not _supported_decomposition(data).all():
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "unresolved active supply decomposition; no partial render")
    keys = ["economic_group", "technology", "carrier", "effective_marginal_value_EUR_MWh",
            "direct_cost_contribution_EUR_MWh", "opportunity_value_EUR_MWh", "constraint_value_EUR_MWh"]
    return data.groupby(keys, dropna=False, sort=True).agg(cleared_MW=("cleared_supply_block_MW", "sum"),
        available_MW=("available_MW", lambda s: s.sum(min_count=len(s))),
        headroom_MW=("headroom_MW", lambda s: s.sum(min_count=len(s))),
        asset_ids=("asset_id", lambda s: json.dumps(sorted(set(s))))).reset_index()


def cumulative_blocks(stack):
    blocks = aggregate_technology(stack)
    if blocks.empty:
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "no cleared supply; no root substitute")
    blocks = blocks.sort_values(["effective_marginal_value_EUR_MWh", "economic_group", "technology", "carrier"], kind="stable")
    blocks["end_MW"] = blocks.cleared_MW.cumsum()
    blocks["start_MW"] = blocks.end_MW - blocks.cleared_MW
    return blocks.reset_index(drop=True)


def select_representative_hour(bundle, *, config=None):
    if not isinstance(bundle, EconomicEvidenceBundle) or bundle._verified is not _VERIFIED:
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "complete same-run bundle required")
    if bundle.provenance.get("source_evidence_validation", {}).get("status") != "VERIFIED":
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "representative hour requires verified source/decomposition evidence")
    selection = (config or configuration())["selection"]
    if selection["representative"] != "NEAREST_ELIGIBLE_REGIME_MEDIAN_PRICE_THEN_TIMESTAMP_ZONE" or selection["scope"] != "LOCAL":
        raise ValueError("Unsupported configured review-hour selection")
    supply = bundle.resources.loc[bundle.resources.side.eq("SUPPLY") & bundle.resources.dispatched_MW.gt(0)]
    clean = supply.assign(supported=_supported_decomposition(supply)).groupby(["snapshot", "zone"]).supported.all()
    eligible = bundle.regimes.merge(bundle.prices[["snapshot", "zone", "zonal_price_EUR_MWh"]], on=["snapshot", "zone"], suffixes=("_regime", ""), validate="one_to_one")
    eligible = eligible.loc[eligible.resolution_scope.eq("LOCAL")].copy()
    eligible = eligible.loc[pd.MultiIndex.from_frame(eligible[["snapshot", "zone"]]).map(clean).fillna(False)]
    for regime in selection["preferred_regimes"]:
        pool = eligible.loc[eligible.final_regime_category.eq(regime)].copy()
        if pool.empty:
            continue
        median = float(pool.zonal_price_EUR_MWh.median())
        pool["distance"] = (pool.zonal_price_EUR_MWh - median).abs()
        chosen = pool.sort_values(["distance", "snapshot", "zone"], kind="stable").iloc[0]
        return chosen.snapshot, chosen.zone, {"regime": regime, "eligible_zone_hours": len(pool),
            "eligible_median_price_EUR_MWh": median, "method": selection["representative"],
            "price_distance_EUR_MWh": float(chosen.distance), "universe_zone_hours": len(bundle.prices)}
    fail("MISSING_MATCHING_EVIDENCE", "no complete clean Storage/Thermal zone-hour")


def produce_review(*, model_year, scenario, bundle_manifest, output, fixture_label, config_path=CONFIG):
    """Render an opt-in representation from a verified full-resource bundle."""
    from .uc2_postprocess import no_solver_calls
    from unittest.mock import patch
    import pypsa
    import xarray as xr
    output = Path(output).resolve()
    if ROOT / "_local/outputs/economic_offer_stack_review" not in output.parents or (output.exists() and any(output.iterdir())):
        raise ValueError("PROTECTED_OUTPUT_TARGET: empty review-only directory required")
    def forbidden(*args, **kwargs):
        raise RuntimeError("ECONOMIC_STACK_NETWORK_OPEN_FORBIDDEN")
    with no_solver_calls() as guard, patch.object(pypsa.Network, "__init__", forbidden), patch.object(xr, "open_dataset", forbidden):
        bundle = load_cached_evidence(bundle_manifest, model_year=model_year, scenario=scenario)
        snapshot, zone, selection = select_representative_hour(bundle, config=configuration(config_path))
        stack = construct_zone_hour_records(bundle, model_year=model_year, scenario=scenario, snapshot=snapshot, zone=zone)
        blocks = cumulative_blocks(stack)
        from ..visualization.economic_offer_stack import render_review
        output.mkdir(parents=True, exist_ok=True)
        image, font = render_review(stack, output / "economic_equilibrium_stack_review.png", fixture_label=fixture_label, config=configuration(config_path))
        stack.records.to_parquet(output / "economic_records.parquet", index=False)
        stack.records.to_csv(output / "economic_records.csv", index=False)
        stack.root_evidence.to_parquet(output / "root_annotations.parquet", index=False)
        blocks.to_csv(output / "cleared_economic_blocks.csv", index=False)
    for file, digest in bundle.provenance["artifact_hashes"].items():
        if sha256_file(Path(file)) != digest:
            raise RuntimeError("PROTECTED_ARTIFACT_MUTATION")
    result = dict(version=VERSION, authority=STATUS, source=bundle.source_identity,
        bundle_provenance=bundle.provenance, model_year=model_year, scenario=scenario,
        snapshot=str(snapshot), zone=zone, selection=selection, image=str(image), font=font,
        solver_calls=guard["solver_invocations"], network_opens=0, routine_canonical_activation=0)
    (output / "ECONOMIC_STACK_REVIEW_RECEIPT.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-year", type=int, required=True)
    for name in ("scenario", "bundle-manifest", "output", "fixture-label"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--config-path", default=str(CONFIG))
    result = produce_review(**vars(parser.parse_args()))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

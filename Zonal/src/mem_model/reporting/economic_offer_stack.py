"""Complete fixed-LP population plus same-run price/root/regime annotations.

No network loader, KKT inference or candidate rediscovery. Upstream reporting
must supply accepted electrical decompositions for all relevant asset-hours,
including zero dispatch and both supply and demand. Root-only caches are never
population evidence. This contract is not wired into final-V2 execution.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

from ..common import ROOT, sha256_file
from .marginal_setter_attribution import TOLERANCE, normalize_zone
from .marginal_setter_resolver import IDENTITY

VERSION = "MEM_ECONOMIC_STACK_INPUT_CONTRACT_V2"
BUNDLE_SCHEMA = "MEM_FIXED_LP_ECONOMIC_EVIDENCE_BUNDLE_V1"
CONFIG = ROOT / "config/economic_offer_stack.yaml"
STATUS = "REVIEW / REFERENCE ONLY | NOT CANONICAL | NOT ROUTINE | SUBJECT TO FINAL V2 METHODOLOGY"
SOURCE_FIELDS = ("model_year", "scenario", "source_fixed_lp_id", "source_fixed_lp_hash", "source_result_branch")
RESOURCE_KEYS = ("model_year", "scenario", "snapshot", "zone", "asset_id", "component", "side")
RESOURCE_SCHEMA = (*RESOURCE_KEYS, "technology", "carrier", "economic_group",
    "dispatched_MW", "operating_lower_MW", "operating_upper_MW", "available_MW",
    "marginal_cost_EUR_MWh", "marginal_cost_basis", "direct_cost_contribution_EUR_MWh",
    "opportunity_value_EUR_MWh", "constraint_value_EUR_MWh", "effective_marginal_value_EUR_MWh",
    "source_fixed_lp_id", "source_fixed_lp_hash", "source_result_branch",
    "quantity_status", "derivation_method", "evidence_status")
PRICE_SCHEMA = ("snapshot", "zone", "zonal_price_EUR_MWh", *SOURCE_FIELDS)
ROOT_SCHEMA = ("snapshot", "target_zone", *IDENTITY, "remote", "coupling_path",
    "position", "p", "cost", "opportunity", "recon", "residual", "native_power",
    "operating_lower", "operating_upper", "efficiency", "objective_weight", *SOURCE_FIELDS)
REGIME_SCHEMA = ("snapshot", "zone", "primary_attribution_class", "final_regime_category", "resolution_scope", *SOURCE_FIELDS)
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
    for key in ("zone", "target_zone"):
        if key in result:
            result[key] = result[key].map(normalize_zone)
    return result


def _keys(frame):
    return pd.MultiIndex.from_frame(frame[list(RESOURCE_KEYS)])


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
    if not np.isfinite(resources.dispatched_MW.to_numpy(float)).all() or resources.dispatched_MW.lt(0).any():
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "missing/invalid directional electrical MW")
    labels = ["technology", "carrier", "economic_group", "marginal_cost_basis", "quantity_status", "derivation_method", "evidence_status"]
    if resources[labels].isna().any().any():
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "explicit resource semantics required")
    # Null economic values stay unresolved; no imputation or KKT inference here.


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
    if frames["roots"].empty:
        fail("MISSING_MATCHING_EVIDENCE", "empty selected-root layer")
    if not np.isfinite(frames["prices"].zonal_price_EUR_MWh.to_numpy(float)).all():
        fail("MISSING_MATCHING_EVIDENCE", "nonfinite fixed-LP price")
    p = frames["prices"].set_index(["snapshot", "zone"]).zonal_price_EUR_MWh
    for name in ("resources", "regimes"):
        keys = pd.MultiIndex.from_frame(frames[name][["snapshot", "zone"]])
        if not keys.isin(p.index).all():
            fail("MISSING_MATCHING_EVIDENCE", f"{name}: uncovered price zone-hour")
    if "zonal_price_EUR_MWh" in frames["regimes"]:
        g = frames["regimes"].set_index(["snapshot", "zone"]).zonal_price_EUR_MWh
        if not np.allclose(g, p.reindex(g.index), atol=TOLERANCE, rtol=0):
            fail("SOURCE_LINEAGE_MISMATCH", "regime prices differ from accepted price layer")


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
    return EconomicEvidenceBundle(**frames, source_identity=identity,
        provenance={"manifest": str(path), "artifact_hashes": hashes, "coverage": coverage}, _verified=_VERIFIED)


def persist_evidence_bundle(*, resources, prices, roots, regimes, population_index,
                            source_identity, population_attestation, output):
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
    manifest.write_text(json.dumps({"schema_version": BUNDLE_SCHEMA, "source_identity": source_identity,
        "layers": specs, "decomposition_authority": "UPSTREAM_ACCEPTED_FIXED_LP_KKT_HELPERS; NO_INFERENCE_HERE"}, indent=2), encoding="utf-8")
    return manifest


def deduplicate_roots(roots):
    """Dedup annotation identities, NEVER population or resource quantities."""
    keys = ["snapshot", "target_zone", *IDENTITY]
    for col in ("p", "cost", "opportunity", "recon", "residual", "remote"):
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
    local_keys = set(zip(local.component, local.asset_id))
    if not local_keys.issubset(set(zip(resources.component, resources.asset_id))):
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "local validated root absent from resource population")
    resources["marginal_root"] = [(c, a) in local_keys for c, a in zip(resources.component, resources.asset_id)]
    audit = {key: g.to_json(orient="records", date_format="iso") for key, g in local.groupby(["component", "asset_id"])}
    mechanisms = {key: json.dumps(sorted(set(g.mechanism))) for key, g in local.groupby(["component", "asset_id"])}
    resources["root_audit_metadata"] = [audit.get((c, a)) for c, a in zip(resources.component, resources.asset_id)]
    resources["root_mechanisms"] = [mechanisms.get((c, a)) for c, a in zip(resources.component, resources.asset_id)]
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
    if not np.isfinite(data.effective_marginal_value_EUR_MWh.to_numpy(float)).all():
        fail("COMPLETE_FIXED_LP_ECONOMIC_EVIDENCE_REQUIRED", "unresolved active supply value; no partial render")
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
    selection = (config or configuration())["selection"]
    if selection["representative"] != "NEAREST_ELIGIBLE_REGIME_MEDIAN_PRICE_THEN_TIMESTAMP_ZONE" or selection["scope"] != "LOCAL":
        raise ValueError("Unsupported configured review-hour selection")
    supply = bundle.resources.loc[bundle.resources.side.eq("SUPPLY") & bundle.resources.dispatched_MW.gt(0)]
    clean = supply.groupby(["snapshot", "zone"]).effective_marginal_value_EUR_MWh.agg(lambda s: np.isfinite(s.to_numpy(float)).all())
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
            "eligible_median_price_EUR_MWh": median, "method": selection["representative"]}
    fail("MISSING_MATCHING_EVIDENCE", "no complete clean Storage/Thermal zone-hour")


def produce_review(*, model_year, scenario, bundle_manifest, output, fixture_label, config_path=CONFIG):
    """Future opt-in consumer, not called by this correction or routine pipeline."""
    from .uc2_postprocess import no_solver_calls
    from unittest.mock import patch
    import pypsa
    import xarray as xr
    output = Path(output).resolve()
    if ROOT / "outputs/economic_offer_stack_review" not in output.parents or (output.exists() and any(output.iterdir())):
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

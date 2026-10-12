"""Synthetic cached-table contract checks; no MEM results, networks or solves."""
import json

import numpy as np
import pandas as pd
import pytest

from mem_model.common import sha256_file
from mem_model.reporting import economic_offer_stack as stack


@pytest.fixture(autouse=True)
def forbid_networks_and_optimization(monkeypatch):
    import pypsa
    import xarray as xr
    from linopy import Model
    from pypsa.optimization.optimize import OptimizationAccessor

    def forbidden(*args, **kwargs):
        pytest.fail("Economic-stack contract must not open networks, build models or solve")

    monkeypatch.setattr(pypsa.Network, "__init__", forbidden)
    monkeypatch.setattr(xr, "open_dataset", forbidden)
    monkeypatch.setattr(Model, "__init__", forbidden)
    for method in ("__call__", "create_model", "solve_model"):
        monkeypatch.setattr(OptimizationAccessor, method, forbidden)


@pytest.fixture
def identity():
    return {"model_year": 2040, "scenario": "Base", "source_fixed_lp_id": "FIXTURE_LP",
            "source_fixed_lp_hash": "1" * 64,
            "source_result_branch": "SYNTHETIC_FIXTURE_NOT_MODEL_INPUT"}


@pytest.fixture
def frames(identity):
    resources, prices, roots, regimes = [], [], [], []
    for snapshot, price in zip(pd.date_range("2019-01-01", periods=2, freq="h"), (50., 60.)):
        for zone in stack.ZONES:
            for name, p, upper, cost, status in (("ROOT", 5., 20., price, "SUPPORTED"),
                                                ("BOUND", 5., 5., 10., "SUPPORTED"),
                                                ("IDLE", 0., 30., 100., "UNRESOLVED")):
                supported = status == "SUPPORTED"
                resources.append({**identity, "snapshot": snapshot, "zone": zone,
                    "asset_id": f"{name}_{zone}", "component": "Generator", "side": "SUPPLY",
                    "technology": "CCGT", "carrier": "methane_ccgt", "economic_group": "CCGT",
                    "dispatched_MW": p, "operating_lower_MW": 0., "operating_upper_MW": upper,
                    "available_MW": upper, "marginal_cost_EUR_MWh": cost,
                    "marginal_cost_basis": "ELECTRICAL_EUR_PER_MWH", "quantity_status": "VERIFIED",
                    "direct_cost_contribution_EUR_MWh": cost if supported else np.nan,
                    "opportunity_value_EUR_MWh": 0. if supported else np.nan,
                    "constraint_value_EUR_MWh": price - cost if supported else np.nan,
                    "effective_marginal_value_EUR_MWh": price if supported else np.nan,
                    "derivation_method": "SYNTHETIC_ACCEPTED_DECOMPOSITION_FIXTURE",
                    "evidence_status": status,
                    "unresolved_reason": None if supported else "IDLE_RESOURCE_NOT_EVALUATED"})
            resources.append({**identity, "snapshot": snapshot, "zone": zone,
                "asset_id": f"LOAD_{zone}", "component": "Load", "side": "DEMAND",
                "technology": "Fixed end-use demand", "carrier": "AC", "economic_group": "Fixed demand",
                "dispatched_MW": 10., "operating_lower_MW": 10., "operating_upper_MW": 10.,
                "available_MW": 10., "marginal_cost_EUR_MWh": np.nan,
                "marginal_cost_basis": "EXOGENOUS_FIXED_DEMAND", "quantity_status": "VERIFIED",
                **{field: np.nan for field in stack.ECONOMIC_FIELDS},
                "derivation_method": "FIXED_LOAD_PSET", "evidence_status": "NOT_APPLICABLE",
                "unresolved_reason": "FIXED_LOAD_HAS_NO_OPTIMIZED_MARGINAL_VALUE"})
            prices.append({**identity, "snapshot": snapshot, "zone": zone, "zonal_price_EUR_MWh": price})
            regimes.append({**identity, "snapshot": snapshot, "zone": zone,
                "primary_attribution_class": "DIRECT_MARGINAL_TECHNOLOGY",
                "final_regime_category": "Thermal", "resolution_scope": "LOCAL", "valid_root_count": 1})
            roots.append({**identity, "snapshot": snapshot, "target_zone": zone, "zone": zone,
                "asset_id": f"ROOT_{zone}", "component": "Generator", "technology": "CCGT",
                "carrier": "methane_ccgt", "mechanism": "DIRECT_MARGINAL_TECHNOLOGY",
                "remote": False, "coupling_path": "[]", "position": "interior", "p": 5.,
                "cost": price, "opportunity": np.nan, "recon": price, "residual": 0.,
                "native_power": 5., "operating_lower": 0., "operating_upper": 20.,
                "efficiency": 1., "objective_weight": 1.})
    result = {name: pd.DataFrame(rows) for name, rows in
              (("resources", resources), ("prices", prices), ("roots", roots), ("regimes", regimes))}
    result["population_index"] = result["resources"][list(dict.fromkeys((*stack.RESOURCE_KEYS, *stack.SOURCE_FIELDS)))].copy()
    return result


def persist(frames, identity, folder):
    return stack.persist_evidence_bundle(**frames, source_identity=identity, output=folder,
        population_attestation={"population": "ALL_RELEVANT_FIXED_LP_RESOURCES",
            "basis": "INDEPENDENT_FIXED_LP_ASSET_ENUMERATION", "includes_zero_dispatch": True,
            "includes_supply_and_demand": True})


def write_document(folder, name, document, identity):
    path = folder / f"{name}.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return {"path": path.name, "sha256": sha256_file(path), "source_identity": identity.copy()}


def attach_sources(path, frames, identity):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    folder = path.parent
    sources = {}
    sources["frozen_root_methodology"] = write_document(folder, "frozen_root_methodology",
        {"fixture": "SYNTHETIC_FIXED_LP_TABLES_NOT_MODEL_INPUT"}, identity)
    for role, layer, schema in (("validated_root_candidates", "roots", stack.ROOT_SCHEMA),
                                ("marginal_regime_data", "regimes", stack.REGIME_SCHEMA)):
        source_path = folder / f"{role}.parquet"
        frames[layer][[column for column in schema if column not in stack.SOURCE_FIELDS]].to_parquet(source_path, index=False)
        sources[role] = {"path": source_path.name, "sha256": sha256_file(source_path), "source_identity": identity.copy()}
    solve = {"status": "PASS", "year": identity["model_year"], "scenario": identity["scenario"],
        "kind": "FIXED_COMMITMENT_PRICE_LP", "job_id": identity["source_fixed_lp_id"],
        "solved_sha256": identity["source_fixed_lp_hash"], "solver_status": "ok",
        "termination_condition": "optimal", "integer_variables": 0, "binary_variables": 0,
        "input_sha256": "2" * 64, "fixed_commitment_milp_sha256": "3" * 64, "snapshots": 2,
        "final_methodology_variant": identity["source_result_branch"]}
    native_tables = {}
    for name, table in (
        ("constraints", pd.DataFrame({"constraint_label": [0], "raw_dual": [50.]})),
        ("variables", pd.DataFrame({"variable_label": [0], "solution": [5.], "objective_coefficient": [50.]})),
        ("terms", pd.DataFrame({"constraint_label": [0], "variable_label": [0], "coefficient": [1.]})),
        ("electrical_population", frames["resources"][[*stack.RESOURCE_KEYS, "dispatched_MW", "operating_lower_MW", "operating_upper_MW"]])):
        table_path = folder / f"native_{name}.parquet"
        table.to_parquet(table_path, index=False)
        native_tables[name] = {"path": table_path.name, "sha256": sha256_file(table_path), "rows": len(table)}
    native = {"schema_version": "MEM_FIXED_LP_CONSTRAINT_EVIDENCE_V2",
        "provenance": {key: solve[key] for key in ("year", "scenario", "kind", "job_id", "input_sha256", "fixed_commitment_milp_sha256", "solved_sha256")},
        "snapshot_count": 2, "snapshot_sha256": stack.sha256(pd.date_range("2019-01-01", periods=2, freq="h").asi8.tobytes()).hexdigest(),
        "table": native_tables["constraints"]["path"], "table_sha256": native_tables["constraints"]["sha256"], "rows": 1,
        "additional_tables": {name: spec for name, spec in native_tables.items() if name != "constraints"},
        "stationarity_scope": "ALL_NATIVE_CONSTRAINT_FAMILIES_WITH_DISPATCH_OR_STORE_STATE_INCIDENCE",
        "economic_decomposition_status": "NATIVE_PRIMITIVES_RETAINED_NOT_A_VALIDATED_ECONOMIC_STACK"}
    native["provenance"]["result_branch"] = identity["source_result_branch"]
    sources["native_price_evidence"] = write_document(folder, "native_price_evidence", native, identity)
    solve["price_constraint_evidence"] = {"sha256": sources["native_price_evidence"]["sha256"]}
    sources["fixed_lp_solve_receipt"] = write_document(folder, "fixed_lp_solve_receipt", solve, identity)
    verification = {**solve, "price_constraint_evidence": {"status": "VERIFIED",
        "manifest_sha256": sources["native_price_evidence"]["sha256"], "resource_capture": "COMPLETE_NATIVE_PRIMITIVES"}}
    sources["fixed_lp_verification"] = write_document(folder, "fixed_lp_verification", verification, identity)
    qa = {"status": "PASS_FROZEN_METHOD_ATTRIBUTION", "methodology_frozen": True,
        "solver_invocations": 0, "job_id": identity["source_fixed_lp_id"],
        "fixed_commitment_milp_sha256": solve["fixed_commitment_milp_sha256"],
        "source_hashes": {"synthetic_solved_source": identity["source_fixed_lp_hash"]},
        "frozen_methodology_sha256": sources["frozen_root_methodology"]["sha256"],
        "artifact_hashes": {"synthetic_candidates": sources["validated_root_candidates"]["sha256"]}}
    sources["validated_root_qa"] = write_document(folder, "validated_root_qa", qa, identity)
    sources["marginal_regime_receipt"] = write_document(folder, "marginal_regime_receipt",
        {"status": "PASS_CANONICAL_MARGINAL_REGIME", "model_year": identity["model_year"],
         "scenario": identity["scenario"], "methodology": "LEXICOGRAPHIC_REGIME_V1",
         "source_hashes": {"synthetic_root_QA": sources["validated_root_qa"]["sha256"]},
         "artifact_hashes": {"synthetic_regime_data": sources["marginal_regime_data"]["sha256"]}}, identity)
    index_hash = manifest["layers"]["resources"]["population_index"]["sha256"]
    sources["population_enumeration"] = write_document(folder, "population_enumeration",
        {"status": "PASS", "source_identity": identity, "basis": "INDEPENDENT_FIXED_LP_ASSET_ENUMERATION",
         "population_index_sha256": index_hash, "rows": len(frames["population_index"])}, identity)
    active = frames["resources"].dispatched_MW.gt(0) & frames["resources"].component.ne("Load")
    sources["decomposition_validation"] = write_document(folder, "decomposition_validation",
        {"status": "PASS", "source_identity": identity, "resources_sha256": manifest["layers"]["resources"]["sha256"],
         "prices_sha256": manifest["layers"]["prices"]["sha256"], "population_index_sha256": index_hash,
         "native_price_evidence_sha256": sources["native_price_evidence"]["sha256"],
         "frozen_methodology_sha256": sources["frozen_root_methodology"]["sha256"],
         "complete_active_resource_decomposition": True, "active_resource_rows": int(active.sum())}, identity)
    manifest["source_evidence"] = sources
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def complete_manifest(frames, identity, tmp_path):
    return attach_sources(persist(frames, identity, tmp_path / "bundle"), frames, identity)


def test_complete_bundle_keeps_idle_and_fixed_load_unknowns_and_physical_widths(complete_manifest):
    bundle = stack.load_cached_evidence(complete_manifest, model_year=2040, scenario="Base")
    snapshot, zone, selection = stack.select_representative_hour(bundle)
    assert (snapshot, zone) == (pd.Timestamp("2019-01-01"), "CALA")
    assert selection["eligible_median_price_EUR_MWh"] == 55.
    assert selection["eligible_zone_hours"] == 14
    records = stack.construct_zone_hour_records(bundle, model_year=2040, scenario="Base", snapshot=snapshot, zone=zone)
    assert len(records.records) == 4
    assert records.records.loc[records.records.component.eq("Load"), list(stack.ECONOMIC_FIELDS)].isna().all().all()
    assert records.records.loc[records.records.asset_id.eq("IDLE_CALA"), list(stack.ECONOMIC_FIELDS)].isna().all().all()
    assert records.records.marginal_root.sum() == 1
    blocks = stack.cumulative_blocks(records)
    assert blocks.cleared_MW.sum() == blocks.end_MW.max() == 10.
    assert blocks.available_MW.sum() == 25.  # Idle capacity is retained in records, not cleared blocks.
    assert set(blocks.effective_marginal_value_EUR_MWh) == {50.}
    ready = stack.economic_stack_readiness(complete_manifest, model_year=2040, scenario="Base")
    assert ready["status"] == "READY_FOR_OPT_IN_REVIEW"
    assert ready["representative_hour_ready"] and not ready["routine_activation"]
    assert not ready["renderer_complete"] and ready["renderer_limitations"]


@pytest.mark.parametrize("defect,expected", [
    ("negative_dispatch", "dispatch/bounds"), ("nonfinite_bounds", "nonfinite"),
    ("above_bound", "dispatch/bounds"), ("availability", "dispatch/bounds"),
    ("infinite_economics", "nonfinite"), ("partial_supported", "complete finite"),
    ("arithmetic", "direct \\+ opportunity"), ("price", "same-LP zonal price"),
    ("unknown_status", "evidence_status"), ("missing_reason", "unresolved_reason"),
    ("load_valuation", "willingness-to-pay"), ("flexible_load_bounds", "fixed Load lower/upper"),
])
def test_rejects_bad_quantities_or_economic_evidence(frames, identity, tmp_path, defect, expected):
    r = frames["resources"]
    if defect == "negative_dispatch": r.loc[0, "dispatched_MW"] = -1.
    elif defect == "nonfinite_bounds": r.loc[0, "operating_upper_MW"] = np.inf
    elif defect == "above_bound": r.loc[0, "dispatched_MW"] = 21.
    elif defect == "availability": r.loc[0, "available_MW"] = 21.
    elif defect == "infinite_economics": r.loc[0, "opportunity_value_EUR_MWh"] = np.inf
    elif defect == "partial_supported": r.loc[0, "opportunity_value_EUR_MWh"] = np.nan
    elif defect == "arithmetic": r.loc[0, "constraint_value_EUR_MWh"] = 1.
    elif defect == "price": r.loc[0, ["direct_cost_contribution_EUR_MWh", "effective_marginal_value_EUR_MWh"]] = 51.
    elif defect == "unknown_status": r.loc[0, "evidence_status"] = "UNVERIFIED_BUT_FINITE"
    elif defect == "missing_reason": r.loc[2, "unresolved_reason"] = ""
    elif defect == "load_valuation": r.loc[3, "marginal_cost_EUR_MWh"] = 0.
    elif defect == "flexible_load_bounds": r.loc[3, "operating_lower_MW"] = 0.
    with pytest.raises(stack.EvidenceContractError, match=expected):
        persist(frames, identity, tmp_path / "bad")


@pytest.mark.parametrize("defect,expected", [
    ("root_price", "root reconstruction"), ("root_residual", "root reconstruction"),
    ("root_scope", "local/remote root bus"), ("root_side", "matching electrical side"),
    ("root_quantity", "dispatch differs"), ("root_path", "coupling path"),
    ("root_boolean", "boolean"), ("root_duplicate", "conflicting duplicate root"),
    ("root_missing", "selected roots"), ("root_orphan", "selected roots"),
])
def test_rejects_bad_root_context(frames, identity, tmp_path, defect, expected):
    r = frames["roots"]
    if defect == "root_price": r.loc[0, "recon"] = 51.
    elif defect == "root_residual": r.loc[0, "residual"] = .1
    elif defect == "root_scope": r.loc[0, "zone"] = "SUD"
    elif defect == "root_side": r.loc[0, "position"] = "charging"
    elif defect == "root_quantity": r.loc[0, "p"] = 6.
    elif defect == "root_path": r.loc[0, "coupling_path"] = '["EDGE"]'
    elif defect == "root_boolean": r["remote"] = r.remote.astype(object); r.loc[0, "remote"] = "False"
    elif defect == "root_duplicate":
        extra = r.iloc[[0]].copy(); extra["efficiency"] = 2.
        frames["roots"] = pd.concat([r, extra], ignore_index=True)
    elif defect == "root_missing": frames["roots"] = r.iloc[1:].copy()
    elif defect == "root_orphan": r.loc[0, "snapshot"] = pd.Timestamp("2020-01-01")
    with pytest.raises(stack.EvidenceContractError, match=expected):
        persist(frames, identity, tmp_path / "bad")


def test_population_proof_cannot_omit_matching_idle_asset_hours(frames, identity, tmp_path):
    frames["resources"] = frames["resources"].drop(index=2)
    frames["population_index"] = frames["population_index"].drop(index=2)
    with pytest.raises(stack.EvidenceContractError, match="omits asset-hours"):
        persist(frames, identity, tmp_path / "bad")


def test_selected_root_count_must_match_complete_regime_root_set(frames, identity, tmp_path):
    frames["regimes"].loc[0, "valid_root_count"] = 2
    with pytest.raises(stack.EvidenceContractError, match="complete selected root identities"):
        persist(frames, identity, tmp_path / "bad")


def test_rejects_incomplete_withdrawals_even_when_inventory_matches(frames, identity, tmp_path):
    for column in ("dispatched_MW", "operating_lower_MW", "operating_upper_MW", "available_MW"):
        frames["resources"].loc[3, column] = 11.
    with pytest.raises(stack.EvidenceContractError, match="do not balance"):
        persist(frames, identity, tmp_path / "bad")


def test_numeric_tables_without_source_proof_stay_gated(frames, identity, tmp_path):
    manifest = persist(frames, identity, tmp_path / "partial")
    bundle = stack.load_cached_evidence(manifest, model_year=2040, scenario="Base")
    ready = stack.economic_stack_readiness(manifest, model_year=2040, scenario="Base")
    assert ready["status"] == "EVIDENCE_GATED"
    assert set(ready["missing_evidence"]) == {"source_evidence." + name for name in stack.SOURCE_EVIDENCE_LAYERS}
    with pytest.raises(stack.EvidenceContractError, match="source/decomposition"):
        stack.select_representative_hour(bundle)


def test_missing_and_root_only_inputs_report_exact_missing_layers(tmp_path):
    missing = stack.economic_stack_readiness(model_year=2040, scenario="Base")
    assert {"resources", "population_index", "complete_supported_economic_decomposition"}.issubset(missing["missing_evidence"])
    path = tmp_path / "root_only.json"
    path.write_text(json.dumps({"layers": {"roots": {"path": "root.parquet"}}}), encoding="utf-8")
    ready = stack.economic_stack_readiness(path, model_year=2040, scenario="Base")
    assert ready["status"] == "EVIDENCE_GATED" and "resources" in ready["missing_evidence"]
    assert "roots" not in ready["missing_evidence"]
    with pytest.raises(stack.EvidenceContractError, match="no complete resource layer"):
        stack.load_cached_evidence(path, model_year=2040, scenario="Base")


def test_unresolved_active_supply_never_becomes_renderable(frames, identity, tmp_path):
    r = frames["resources"]
    r.loc[1, list(stack.ECONOMIC_FIELDS)] = np.nan
    r.loc[1, "evidence_status"] = "UNRESOLVED"
    r.loc[1, "unresolved_reason"] = "NATIVE_TERM_COVERAGE_MISSING"
    manifest = persist(frames, identity, tmp_path / "partial")
    ready = stack.economic_stack_readiness(manifest, model_year=2040, scenario="Base")
    assert "complete_supported_economic_decomposition" in ready["missing_evidence"]
    assert ready["unresolved_active_resource_rows"] == 1
    attach_sources(manifest, frames, identity)
    ready = stack.economic_stack_readiness(manifest, model_year=2040, scenario="Base")
    assert ready["status"] == "EVIDENCE_GATED" and ready["invalid_evidence"]
    assert "complete_supported_economic_decomposition" in ready["missing_evidence"]


@pytest.mark.parametrize("defect", ["solve_identity", "root_method", "resource_pin", "population_pin", "source_file_hash"])
def test_rejects_source_lineage_or_decomposition_proof_conflicts(complete_manifest, defect):
    manifest = json.loads(complete_manifest.read_text(encoding="utf-8"))
    role = {"solve_identity": "fixed_lp_solve_receipt", "root_method": "validated_root_qa",
            "resource_pin": "decomposition_validation", "population_pin": "population_enumeration",
            "source_file_hash": "fixed_lp_verification"}[defect]
    spec = manifest["source_evidence"][role]
    path = complete_manifest.parent / spec["path"]
    document = json.loads(path.read_text(encoding="utf-8"))
    if defect == "solve_identity": document["solved_sha256"] = "4" * 64
    elif defect == "root_method": document["methodology_frozen"] = False
    elif defect == "resource_pin": document["resources_sha256"] = "4" * 64
    elif defect == "population_pin": document["population_index_sha256"] = "4" * 64
    elif defect == "source_file_hash": document["status"] = "ALTERED"
    path.write_text(json.dumps(document), encoding="utf-8")
    if defect != "source_file_hash": spec["sha256"] = sha256_file(path)
    complete_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    ready = stack.economic_stack_readiness(complete_manifest, model_year=2040, scenario="Base")
    assert ready["status"] == "EVIDENCE_GATED" and ready["invalid_evidence"]


@pytest.mark.parametrize("layer", ["roots", "regimes"])
def test_bundle_cannot_replace_validated_root_identities_or_regime_values(complete_manifest, layer):
    manifest = json.loads(complete_manifest.read_text(encoding="utf-8"))
    spec = manifest["layers"][layer]
    path = complete_manifest.parent / spec["path"]
    table = pd.read_parquet(path)
    if layer == "roots":
        table.loc[0, "asset_id"] = table.loc[0, "asset_id"].replace("ROOT", "BOUND")
        table.loc[0, "operating_upper"] = 5.
    else:
        table.loc[0, "final_regime_category"] = "Storage"
    table.to_parquet(path, index=False)
    spec["sha256"] = sha256_file(path)
    complete_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    ready = stack.economic_stack_readiness(complete_manifest, model_year=2040, scenario="Base")
    assert ready["status"] == "EVIDENCE_GATED"
    assert any("differ from validated cached source" in message for message in ready["invalid_evidence"])


def test_complete_native_primitives_do_not_replace_decomposition_validation(complete_manifest):
    manifest = json.loads(complete_manifest.read_text(encoding="utf-8"))
    del manifest["source_evidence"]["decomposition_validation"]
    complete_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    ready = stack.economic_stack_readiness(complete_manifest, model_year=2040, scenario="Base")
    assert ready["status"] == "EVIDENCE_GATED"
    assert "source_evidence.decomposition_validation" in ready["missing_evidence"]
    assert not ready["representative_hour_ready"]


@pytest.mark.parametrize("defect", ["legacy_rows_only", "missing_terms", "wrong_source", "wrong_snapshot", "sidecar", "raw_claims_compiled"])
def test_native_capture_capabilities_lineage_and_sidecar_integrity_are_required(complete_manifest, defect):
    manifest = json.loads(complete_manifest.read_text(encoding="utf-8"))
    spec = manifest["source_evidence"]["native_price_evidence"]
    path = complete_manifest.parent / spec["path"]
    native = json.loads(path.read_text(encoding="utf-8"))
    if defect == "legacy_rows_only": native["schema_version"] = "MEM_FIXED_LP_CONSTRAINT_EVIDENCE_V1"
    elif defect == "missing_terms": del native["additional_tables"]["terms"]
    elif defect == "wrong_source": native["provenance"]["input_sha256"] = "4" * 64
    elif defect == "wrong_snapshot": native["snapshot_sha256"] = "4" * 64
    elif defect == "raw_claims_compiled": native["economic_decomposition_status"] = "SUPPORTED_COMPLETE"
    elif defect == "sidecar":
        sidecar = complete_manifest.parent / native["additional_tables"]["variables"]["path"]
        pd.DataFrame({"variable_label": [999]}).to_parquet(sidecar, index=False)
    path.write_text(json.dumps(native), encoding="utf-8")
    spec["sha256"] = sha256_file(path)
    for role, column in (("fixed_lp_solve_receipt", "sha256"), ("fixed_lp_verification", "manifest_sha256")):
        source_spec = manifest["source_evidence"][role]
        source_path = complete_manifest.parent / source_spec["path"]
        document = json.loads(source_path.read_text(encoding="utf-8"))
        document["price_constraint_evidence"][column] = spec["sha256"]
        source_path.write_text(json.dumps(document), encoding="utf-8")
        source_spec["sha256"] = sha256_file(source_path)
    decomposition_spec = manifest["source_evidence"]["decomposition_validation"]
    decomposition_path = complete_manifest.parent / decomposition_spec["path"]
    decomposition = json.loads(decomposition_path.read_text(encoding="utf-8"))
    decomposition["native_price_evidence_sha256"] = spec["sha256"]
    decomposition_path.write_text(json.dumps(decomposition), encoding="utf-8")
    decomposition_spec["sha256"] = sha256_file(decomposition_path)
    complete_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    ready = stack.economic_stack_readiness(complete_manifest, model_year=2040, scenario="Base")
    assert ready["status"] == "EVIDENCE_GATED"
    assert any("native_price_evidence" in detail for detail in ready["invalid_evidence"])


def test_representative_selection_is_order_independent(frames, identity, tmp_path):
    paths = []
    for index, reverse in enumerate((False, True)):
        sample = {name: frame.iloc[::-1].reset_index(drop=True) if reverse else frame.copy() for name, frame in frames.items()}
        paths.append(attach_sources(persist(sample, identity, tmp_path / str(index)), sample, identity))
    selected = [stack.select_representative_hour(stack.load_cached_evidence(path, model_year=2040, scenario="Base")) for path in paths]
    assert selected[0] == selected[1]


def test_storage_preference_uses_existing_selection_rule(frames, identity, tmp_path):
    snapshot = frames["prices"].snapshot.min()
    mask = frames["resources"].asset_id.eq("ROOT_NORD")
    r = frames["resources"]
    r.loc[mask, ["component", "technology", "carrier", "economic_group"]] = ["Link", "BESS", "battery_energy", "BESS"]
    r.loc[mask, ["marginal_cost_EUR_MWh", "direct_cost_contribution_EUR_MWh"]] = 0.
    r.loc[mask, "opportunity_value_EUR_MWh"] = r.loc[mask, "effective_marginal_value_EUR_MWh"]
    frames["population_index"].loc[mask, "component"] = "Link"
    roots = frames["roots"]
    m = roots.asset_id.eq("ROOT_NORD")
    roots.loc[m, ["component", "technology", "carrier", "mechanism", "position"]] = ["Link", "BESS", "battery_energy", "STORAGE_HYDRO_OPPORTUNITY_VALUE", "discharging"]
    roots.loc[m, "cost"] = 0.
    roots.loc[m, "opportunity"] = roots.loc[m, "recon"]
    g = frames["regimes"]
    m = g.zone.eq("NORD")
    g.loc[m, ["primary_attribution_class", "final_regime_category"]] = ["STORAGE_HYDRO_OPPORTUNITY_VALUE", "Storage"]
    path = attach_sources(persist(frames, identity, tmp_path / "storage"), frames, identity)
    chosen_snapshot, zone, selection = stack.select_representative_hour(stack.load_cached_evidence(path, model_year=2040, scenario="Base"))
    assert (chosen_snapshot, zone, selection["regime"]) == (snapshot, "NORD", "Storage")


def test_annotation_mutation_cannot_change_resource_values(complete_manifest):
    bundle = stack.load_cached_evidence(complete_manifest, model_year=2040, scenario="Base")
    records = stack.construct_zone_hour_records(bundle, model_year=2040, scenario="Base", snapshot="2019-01-01", zone="CALA")
    records.records.loc[records.records.component.eq("Generator"), "marginal_cost_EUR_MWh"] += 1.
    with pytest.raises(stack.EvidenceContractError, match="stack values differ"):
        stack.cumulative_blocks(records)


def test_flexible_demand_root_is_not_added_to_supply_width(frames, identity, tmp_path):
    resources, roots = [], []
    for snapshot, price in zip(pd.date_range("2019-01-01", periods=2, freq="h"), (50., 60.)):
        row = frames["resources"].loc[frames["resources"].snapshot.eq(snapshot) & frames["resources"].asset_id.eq("ROOT_NORD")].iloc[0].to_dict()
        row.update(asset_id="P2X_NORD", side="DEMAND", technology="P2X", carrier="p2x_flexible_NORD",
                   economic_group="P2X", dispatched_MW=5., operating_upper_MW=10., available_MW=10.,
                   marginal_cost_EUR_MWh=70. - price, direct_cost_contribution_EUR_MWh=price - 70.,
                   opportunity_value_EUR_MWh=70., constraint_value_EUR_MWh=0.)
        resources.append(row)
        root = frames["roots"].loc[frames["roots"].snapshot.eq(snapshot) & frames["roots"].asset_id.eq("ROOT_NORD")].iloc[0].to_dict()
        root.update(asset_id="P2X_NORD", technology="P2X", carrier="p2x_flexible_NORD",
                    mechanism="P2X_FLEXIBLE_DEMAND", cost=70. - price, opportunity=70., operating_upper=10.)
        roots.append(root)
    mask = frames["resources"].asset_id.eq("ROOT_NORD")
    frames["resources"].loc[mask, "dispatched_MW"] = 10.
    mask = frames["roots"].asset_id.eq("ROOT_NORD")
    frames["roots"].loc[mask, ["p", "native_power"]] = 10.
    frames["resources"] = pd.concat([frames["resources"], pd.DataFrame(resources)], ignore_index=True)
    frames["population_index"] = frames["resources"][list(dict.fromkeys((*stack.RESOURCE_KEYS, *stack.SOURCE_FIELDS)))].copy()
    frames["roots"] = pd.concat([frames["roots"], pd.DataFrame(roots)], ignore_index=True)
    mask = frames["regimes"].zone.eq("NORD")
    frames["regimes"].loc[mask, "primary_attribution_class"] = "MULTIPLE_CANDIDATES"
    frames["regimes"].loc[mask, "valid_root_count"] = 2
    path = attach_sources(persist(frames, identity, tmp_path / "demand"), frames, identity)
    bundle = stack.load_cached_evidence(path, model_year=2040, scenario="Base")
    records = stack.construct_zone_hour_records(bundle, model_year=2040, scenario="Base", snapshot="2019-01-01", zone="NORD")
    demand = records.records.loc[records.records.asset_id.eq("P2X_NORD")].iloc[0]
    assert demand.side == "DEMAND" and demand.marginal_root
    assert demand.direct_cost_contribution_EUR_MWh == -20.
    assert stack.cumulative_blocks(records).cleared_MW.sum() == 15.
    assert records.records.loc[records.records.side.eq("DEMAND"), "dispatched_MW"].sum() == 15.


def test_readiness_reports_missing_interface_style_without_inventing_one(frames, identity, tmp_path):
    mask = frames["resources"].asset_id.eq("BOUND_NORD")
    r = frames["resources"]
    r.loc[mask, ["asset_id", "component", "technology", "carrier", "economic_group"]] = ["IMPORT_NORD", "Link", "Imports", "external_trade", "Imports"]
    r.loc[mask, ["marginal_cost_EUR_MWh", "direct_cost_contribution_EUR_MWh", "constraint_value_EUR_MWh"]] = 0.
    r.loc[mask, "opportunity_value_EUR_MWh"] = r.loc[mask, "effective_marginal_value_EUR_MWh"]
    mask = r.asset_id.eq("ROOT_NORD")
    r.loc[mask, ["component", "technology", "carrier", "economic_group"]] = ["Link", "BESS", "battery_energy", "BESS"]
    r.loc[mask, ["marginal_cost_EUR_MWh", "direct_cost_contribution_EUR_MWh"]] = 0.
    r.loc[mask, "opportunity_value_EUR_MWh"] = r.loc[mask, "effective_marginal_value_EUR_MWh"]
    roots = frames["roots"]
    mask = roots.asset_id.eq("ROOT_NORD")
    roots.loc[mask, ["component", "technology", "carrier", "mechanism", "position"]] = ["Link", "BESS", "battery_energy", "STORAGE_HYDRO_OPPORTUNITY_VALUE", "discharging"]
    roots.loc[mask, "cost"] = 0.
    roots.loc[mask, "opportunity"] = roots.loc[mask, "recon"]
    mask = frames["regimes"].zone.eq("NORD")
    frames["regimes"].loc[mask, ["primary_attribution_class", "final_regime_category"]] = ["STORAGE_HYDRO_OPPORTUNITY_VALUE", "Storage"]
    frames["population_index"] = r[list(dict.fromkeys((*stack.RESOURCE_KEYS, *stack.SOURCE_FIELDS)))].copy()
    path = attach_sources(persist(frames, identity, tmp_path / "imports"), frames, identity)
    ready = stack.economic_stack_readiness(path, model_year=2040, scenario="Base")
    assert ready["status"] == "READY_FOR_OPT_IN_REVIEW"
    assert not ready["renderer_complete"]
    assert ready["representative_hour"]["zone"] == "NORD"
    assert "MISSING_EXPLICIT_SUPPLY_STYLES: external_trade" in ready["renderer_limitations"]

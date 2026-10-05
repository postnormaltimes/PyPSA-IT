"""Bounded read-only census of accepted hydro lineage and water-state units.

This module never constructs a model, converts weather, or exports a network.
It is not a substitute for the still-required plant/catchment temporal mapping.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pypsa

from .common import ROOT, SCENARIOS, dump_json, parse_bool, safe_id, sha256_file
from .final_methodology_closure import STATE, record_phase
from .stage_b_2040_runtime import hydro_crosswalk, runtime_horizon
from .stage_b_zonal_vre import no_models_or_solves

QA = ROOT / "qa/final_methodology_closure/hydro"


def validate_shape(values, snapshots):
    expected = pd.date_range("2019-01-01", periods=8760, freq="h", tz="UTC")
    observed = pd.DatetimeIndex(pd.to_datetime(snapshots, utc=True))
    array = np.asarray(values, dtype=float)
    if not observed.equals(expected) or array.shape != (8760,):
        raise RuntimeError("HYDRO_AUDIT_CHRONOLOGY_FAIL")
    if not np.isfinite(array).all() or (array < 0).any() or array.sum() <= 0:
        raise RuntimeError("HYDRO_AUDIT_NONNEGATIVE_FINITE_FAIL")
    return array / array.sum()


def state_step(previous_MWh, *, hours, standing_loss, inflow_MW_water,
               pump_MW_electric, pump_efficiency, turbine_MW_water, spill_MW_water):
    """Native Store balance: all bus power terms weighted exactly once."""
    return ((1 - standing_loss) ** hours * previous_MWh + hours *
            (inflow_MW_water + pump_efficiency * pump_MW_electric
             - turbine_MW_water - spill_MW_water))


def _close(actual, expected, label, tolerance=1e-6):
    if not np.isfinite(actual) or abs(actual - expected) > tolerance:
        raise RuntimeError(f"HYDRO_AUDIT_{label}: {actual} != {expected}")


def _dense(network, attribute, component_id):
    return network.get_switchable_as_dense("Generator", attribute)[component_id].to_numpy()


def audit_existing_hydro():
    state = json.loads(STATE.read_text(encoding="utf-8"))
    if state["phases"]["W"].get("phase_result") != "WIND_RESOURCE_SITING_PASS":
        raise RuntimeError("HYDRO_REQUIRES_WIND_PASS")
    parents = state["phases"]["W"]["successor_parents"]
    if {(p["year"], p["scenario"]) for p in parents} != set(SCENARIOS):
        raise RuntimeError("HYDRO_AUDIT_PARENT_COVERAGE_FAIL")
    temporal = pd.read_parquet(ROOT / "stage_a_inputs/temporal_2019_v1_0/MEM_ETX7B3_Hydro_Temporal_Hourly_v1.0.parquet")
    shapes = {}
    for profile_id, group in temporal.loc[temporal.country_code.eq("IT")].groupby("profile_id"):
        group = group.sort_values("snapshot")
        shapes[profile_id] = validate_shape(group.value, group.snapshot)
    cache = pd.read_parquet(ROOT / "stage_a_inputs/temporal_2019_v1_0/cache/MEM_ETX7B3_PyPSA_Eur_Runoff_2019_Cache_v1.0.parquet")
    records, summaries, duplicates = [], [], []
    with no_models_or_solves():
        for year in (2040, 2050):
            runtime = ROOT / "runtime_inputs" / ("accepted" if year == 2040 else "accepted_2050")
            parameters = pd.read_csv(runtime / "hydro_runtime_parameters.csv")
            inflow = pd.read_parquet(runtime / "hydro_inflow_hourly.parquet")
            with runtime_horizon(year):
                _, specs = hydro_crosswalk()
            for parent in (p for p in parents if p["year"] == year):
                path = ROOT / parent["path"]
                if sha256_file(path) != parent["sha256"]:
                    raise RuntimeError("HYDRO_AUDIT_WIND_PARENT_HASH_FAIL")
                network = pypsa.Network(path)
                scenario = parent["scenario"]
                validate_shape(np.ones(8760), network.snapshots)
                for column in ("generators", "stores", "objective"):
                    if not np.array_equal(network.snapshot_weightings[column], np.ones(8760)):
                        raise RuntimeError("HYDRO_AUDIT_WEIGHTING_FAIL")
                states = parameters.loc[parameters.scenario.eq(scenario)].set_index("state_id")
                water_specs = specs.loc[specs.scenario.eq(scenario)].set_index("hydro_id")
                current = inflow.loc[inflow.scenario.eq(scenario)]
                normalized = {}
                for hydro_id, spec in water_specs.iterrows():
                    group = current.loc[current.hydro_id.eq(hydro_id)].sort_values("snapshot")
                    values = group.inflow_MW_water_equivalent.to_numpy()
                    shape = validate_shape(values, group.snapshot)
                    reference = shapes[spec.source_profile_id]
                    diff = float(np.max(np.abs(shape - reference)))
                    if diff > 1e-15:
                        raise RuntimeError("HYDRO_AUDIT_IT2_IT4_LINEAGE_FAIL")
                    annual = float(values.sum())
                    _close(annual, float(spec.annual_natural_inflow_MWh_water), "ANNUAL_CONTROL_FAIL")
                    normalized[hydro_id] = (str(spec.zone), str(spec.hydro_class), shape)
                    base = {"year": year, "scenario": scenario, "zone": spec.zone,
                        "hydro_class": spec.hydro_class, "runtime_id": hydro_id,
                        "current_source_profile": spec.source_profile_id,
                        "source_geography": "FROZEN_CLASS_CAPACITY_WEIGHTED_IT2_IT4_RUNOFF",
                        "annual_inflow_MWh_water": annual,
                        "annual_electric_reference_MWh": spec.annual_electrical_generation_reference_MWh,
                        "turbine_MW_NET": spec.turbine_power_MW_NET,
                        "turbine_efficiency": spec.turbine_efficiency,
                        "normalized_source_max_abs_difference": diff,
                        "hourly_min_MW_water": float(values.min()), "hourly_mean_MW_water": float(values.mean()),
                        "hourly_median_MW_water": float(np.median(values)),
                        "hourly_p5_MW_water": float(np.quantile(values,.05)),
                        "hourly_p95_MW_water": float(np.quantile(values,.95)),
                        "hourly_max_MW_water": float(values.max()), "zero_hours": int((values==0).sum()),
                        "chronology_hours": len(values), "normalized_annual_mass": float(shape.sum())}
                    if spec.hydro_class == "RUN_OF_RIVER":
                        expected_pu = values * float(spec.turbine_efficiency) / float(spec.turbine_power_MW_NET)
                        actual = _dense(network, "p_max_pu", hydro_id)
                        np.testing.assert_allclose(actual, expected_pu, atol=1e-12, rtol=0)
                        _close(float(network.generators.at[hydro_id,"p_nom"]),float(spec.turbine_power_MW_NET),"ROR_MW_FAIL")
                        base.update({"component_archetype":"INFLOW_LIMITED_GENERATOR","Store_id":"",
                            "store_energy_MWh":0.,"pump_MW":0.,"grid_charging_allowed":False,
                            "network_hourly_max_abs_difference":float(np.max(np.abs(actual-expected_pu))),
                            "annual_electric_potential_MWh":float(np.sum(actual)*spec.turbine_power_MW_NET),"status":"PASS"})
                        records.append(base)
                    else:
                        generator = "INFLOW_" + safe_id(hydro_id)
                        actual = _dense(network,"p_max_pu",generator)*network.generators.at[generator,"p_nom"]
                        np.testing.assert_allclose(actual, values, atol=1e-9, rtol=0)
                        np.testing.assert_array_equal(_dense(network,"p_min_pu",generator),_dense(network,"p_max_pu",generator))
                        base["network_hourly_max_abs_difference"] = float(np.max(np.abs(actual-values)))
                        records.append(base)
                for state_id, parameter in states.iterrows():
                    sid = safe_id(state_id)
                    store, turbine, pump, spill = "STORE_"+sid,"TURBINE_"+sid,"PUMP_"+sid,"SPILL_"+sid
                    sr, tr = network.stores.loc[store], network.links.loc[turbine]
                    _close(float(sr.e_nom),float(parameter.operational_energy_MWh),"STATE_ENERGY_FAIL")
                    _close(float(tr.p_nom*tr.efficiency),float(parameter.turbine_power_MW),"NET_TURBINE_MW_FAIL")
                    _close(float(tr.efficiency),float(parameter.turbine_efficiency),"EFFICIENCY_FAIL",1e-12)
                    if bool(sr.e_cyclic) != parse_bool(parameter.cyclic_state_of_charge):
                        raise RuntimeError("HYDRO_AUDIT_CYCLIC_FAIL")
                    pump_MW = float(parameter.pump_power_MW)
                    if pump_MW:
                        _close(float(network.links.at[pump,"p_nom"]),pump_MW,"PUMP_MW_FAIL")
                        _close(float(network.links.at[pump,"efficiency"]),float(parameter.pump_efficiency),"PUMP_EFFICIENCY_FAIL",1e-12)
                    elif pump in network.links.index:
                        raise RuntimeError("HYDRO_AUDIT_UNEXPECTED_PUMP")
                    natural = parse_bool(parameter.natural_inflow_allowed)
                    if ("INFLOW_"+sid in network.generators.index) != natural:
                        raise RuntimeError("HYDRO_AUDIT_NATURAL_STATE_MAPPING_FAIL")
                    if natural:
                        base = next(r for r in records if r["year"]==year and r["scenario"]==scenario and r["runtime_id"]==state_id)
                    else:
                        base = {"year":year,"scenario":scenario,"zone":parameter.zone,"hydro_class":parameter.hydro_class,
                            "runtime_id":state_id,"current_source_profile":"NO_NATURAL_INFLOW",
                            "annual_inflow_MWh_water":0.,"turbine_MW_NET":parameter.turbine_power_MW,
                            "turbine_efficiency":parameter.turbine_efficiency}
                        records.append(base)
                    base.update({"component_archetype":"SHARED_WATER_STORE_PUMP_TURBINE" if pump_MW else "NATURALLY_CHARGED_WATER_STORE_TURBINE",
                        "Store_id":store,"store_energy_MWh":float(sr.e_nom),"e_cyclic":bool(sr.e_cyclic),
                        "e_initial_MWh":float(sr.e_initial),"e_min_pu":float(sr.e_min_pu),"e_max_pu":float(sr.e_max_pu),
                        "standing_loss_per_hour":float(sr.standing_loss),"pump_MW":pump_MW,
                        "pump_efficiency":float(parameter.pump_efficiency),"natural_inflow_allowed":natural,
                        "grid_charging_allowed":parse_bool(parameter.grid_charging_allowed),
                        "spill_component":spill if spill in network.generators.index else "",
                        "spill_MW_water_cap":float(network.generators.at[spill,"p_nom"]) if spill in network.generators.index else 0.,
                        "status":"PASS"})
                keys = sorted(normalized)
                for i, a in enumerate(keys):
                    za, ca, va = normalized[a]
                    for b in keys[i+1:]:
                        zb, cb, vb = normalized[b]
                        difference = float(np.max(np.abs(va-vb)))
                        if za != zb and ca == cb:
                            duplicates.append({"year":year,"scenario":scenario,"hydro_class":ca,"zone_A":za,"zone_B":zb,
                                "max_abs_difference":difference,"same_shape_to_1e_minus_15":difference<=1e-15})
                phs = states.loc[states.hydro_class.isin(["PURE_PHS","MIXED_PHS"])]
                ror = water_specs.loc[water_specs.hydro_class.eq("RUN_OF_RIVER")]
                total = float(states.turbine_power_MW.sum()+ror.turbine_power_MW_NET.sum())
                for actual, expected, label in ((total,23294.,"TOTAL_NET_MW"),(phs.turbine_power_MW.sum(),7252.3,"PHS_NET_MW"),
                        (phs.pump_power_MW.sum(),6400.,"PHS_PUMP_MW"),(phs.operational_energy_MWh.sum(),53000.,"PHS_ENERGY_MWH")):
                    _close(float(actual),expected,label)
                summaries.append({"year":year,"scenario":scenario,"parent_path":parent["path"],"parent_sha256":parent["sha256"],
                    "hydro_total_MW_NET":total,"PHS_discharge_MW_NET":float(phs.turbine_power_MW.sum()),
                    "PHS_pump_MW":float(phs.pump_power_MW.sum()),"PHS_operational_energy_MWh":float(phs.operational_energy_MWh.sum()),
                    "natural_inflow_series":len(water_specs),"water_states":len(states),"snapshots":len(network.snapshots),
                    "all_stores_cyclic":bool(network.stores.loc[["STORE_"+safe_id(x) for x in states.index],"e_cyclic"].all()),
                    "status":"PASS"})
    QA.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(records).to_csv(QA/"HYDRO_CURRENT_LINEAGE_AND_STATE_CENSUS.csv",index=False)
    pd.DataFrame(duplicates).to_csv(QA/"HYDRO_CURRENT_CROSS_ZONE_SHAPE_EQUALITY.csv",index=False)
    receipt = {"state":"HYDRO_CURRENT_PARENT_LINEAGE_AND_CONTROL_AUDIT_PASS",
        "packages":summaries,"state_and_ROR_records":len(records),"natural_inflow_profile_comparisons":sum(s["natural_inflow_series"] for s in summaries),
        "runoff_cache_regions":sorted(cache.source_region.unique().tolist()),
        "hydro_source_profile_ids":sorted(shapes),"IT2_IT4_inheritance_confirmed":True,
        "spatial_correction_implemented":False,
        "cyclic_rule":"CYCLIC_ANNUAL_SOC_RETAINED_AS_NEUTRAL_NO_NET_WATER_CONDITION",
        "first_snapshot_predecessor":"LAST_SNAPSHOT_STATE_NOT_FIRST_OUTPUT_STATE",
        "spill_interpretation":"EXPLICIT_NONNEGATIVE_WATER_BUS_WITHDRAWAL_NOT_ELECTRIC_GENERATION",
        "conservation":"e_t=(1-loss)^w*e_prev+w*(forced_inflow+eta_pump*pump_p0-turbine_p0-spill_p)",
        "generation":"electrical_generation=-turbine_p1=eta_turbine*turbine_p0",
        "optimization_model_constructed":False,"production_solver_invocations":0,
        "production_optimization_executed":False,"production_inputs_modified":0,"production_results_modified":0}
    dump_json(QA/"HYDRO_CURRENT_LINEAGE_AUDIT_RECEIPT.json",receipt)
    return receipt


def checkpoint_spatial_authority_gap(test_result):
    """Persist a source-evidence checkpoint, without pretending H has passed."""
    state = json.loads(STATE.read_text(encoding="utf-8"))
    census = json.loads((QA/"HYDRO_CURRENT_LINEAGE_AUDIT_RECEIPT.json").read_text())
    recovery = json.loads((QA/"HYDRO_SPATIAL_SOURCE_RECOVERY.json").read_text())
    api = json.loads((QA/"HYDRO_NATIVE_WATER_VALUE_API_RECEIPT.json").read_text())
    if (census["state"] != "HYDRO_CURRENT_PARENT_LINEAGE_AND_CONTROL_AUDIT_PASS"
            or len(census["packages"]) != 6 or api["status"] != "NATIVE_API_SOURCE_VERIFIED"
            or not recovery["residual_requirements"]):
        raise RuntimeError("HYDRO_AUTHORITY_CHECKPOINT_EVIDENCE_FAIL")
    tests_path = QA/"FINAL_AFFECTED_NON_SOLVING_TEST_RESULTS.json"
    dump_json(tests_path,test_result)
    residuals = recovery["residual_requirements"][:3]
    parents = state["phases"]["W"]["successor_parents"]
    receipt_path = QA/"HYDRO_SPATIAL_CLOSURE_CHECKPOINT.json"
    manifest = QA/"HYDRO_SPATIAL_CHECKPOINT_MANIFEST.csv"
    handoff = ROOT/"docs/final_methodology_closure/MEM_FINAL_METHODOLOGY_PHASE_H_SPATIAL_AUTHORITY_HANDOFF.md"
    inputs = [QA/"HYDRO_CURRENT_LINEAGE_AUDIT_RECEIPT.json",
              QA/"HYDRO_SPATIAL_SOURCE_RECOVERY.json",QA/"HYDRO_NATIVE_WATER_VALUE_API_RECEIPT.json"]
    receipt = {"state":"HYDRO_SPATIAL_MAPPING_AUTHORITY_REQUIRED","phase":"H","status":"BLOCKED",
        "wind_phase":"WIND_RESOURCE_SITING_PASS","parents":parents,
        "source_evidence":[{"path":str(p.relative_to(ROOT)),"sha256":sha256_file(p)} for p in inputs],
        "completed_checks":{"six_read_only_parent_control_packages_PASS":6,
            "current_state_and_ROR_records":census["state_and_ROR_records"],
            "source_runtime_network_inflow_comparisons_PASS":census["natural_inflow_profile_comparisons"],
            "IT2_IT4_inheritance_confirmed":True,"native_water_state_API_verified":True,
            "frozen_capacity_and_annual_water_controls_unchanged":True},
        "unresolved_items":residuals,
        "source_row_caveat":recovery["hydro_point_membership"]["source_row_geography_conflicts"],
        "cyclic_rule":census["cyclic_rule"],"tests":test_result,
        "hydro_successor_networks_created":0,"hydro_weather_conversions":0,
        "next_phase":"H","N_C_F":"NOT_STARTED_REQUIRES_H_PASS",
        "handoff":str(handoff.relative_to(ROOT)),"manifest":str(manifest.relative_to(ROOT)),
        "optimization_model_constructed":False,"production_optimization_executed":False,
        "production_solver_invocations":0,"production_results_modified":0,"production_state":"PRODUCTION_NOT_EXECUTED"}
    dump_json(receipt_path,receipt)
    artifacts = [*inputs,QA/"HYDRO_CURRENT_LINEAGE_AND_STATE_CENSUS.csv",
        QA/"HYDRO_CURRENT_CROSS_ZONE_SHAPE_EQUALITY.csv",tests_path,receipt_path,handoff,
        ROOT/"src/mem_model/final_hydro_audit.py",ROOT/"tests/test_final_hydro_audit.py"]
    pd.DataFrame([{"path":str(p.relative_to(ROOT)),"bytes":p.stat().st_size,
        "sha256":sha256_file(p)} for p in artifacts]).to_csv(manifest,index=False)
    state = record_phase("H","BLOCKED",parents=parents,artifacts=[*artifacts,manifest],
        decisions=["W_PASS_REUSED_WITHOUT_RECONSTRUCTION","CURRENT_HYDRO_CAPACITY_AND_ANNUAL_WATER_CONTROLS_PRESERVED",
            census["cyclic_rule"],"FIXED_COMMITMENT_LP_NATIVE_STORE_DUAL_AUTHORITY_NO_EXOGENOUS_WATER_COST",
            "NO_UNAPPROVED_PLANT_CATCHMENT_OR_CLASS_TEMPORAL_PROXY"],tests=test_result,unresolved=residuals)
    state["phases"]["H"].update({"phase_result":receipt["state"],"current_handoff":str(handoff.relative_to(ROOT)),
        "manifest":str(manifest.relative_to(ROOT)),"receipt":str(receipt_path.relative_to(ROOT))})
    dump_json(STATE,state)
    return receipt

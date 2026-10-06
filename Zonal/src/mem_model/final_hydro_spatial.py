"""Accepted zonal Atlite runoff adapter; versioned preparation only."""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import atlite
import geopandas as gpd
import numpy as np
import pandas as pd

from .common import ROOT, ZONES, SCENARIOS, safe_id, dump_json, load_yaml, sha256_file
from .final_hydro_audit import validate_shape
from .final_methodology_closure import STATE, record_progress
from .stage_b_2040_runtime import _exact_scaled_profile
from .stage_b_zonal_vre import no_models_or_solves

CONFIG = ROOT / "config/final_hydro_spatial_v1.yaml"
QA = ROOT / "qa/final_methodology_closure/hydro"
OUTPUT = ROOT / "runtime_inputs/final_methodology_closure/hydro/zonal_runoff_v1"
AUTHORITY = ROOT / "docs/final_methodology_closure/PHASE_H_ZONAL_RUNOFF_RESOLUTION.md"


def spill_safety_cap(existing_MW_water, maximum_inflow_MW_water):
    if not np.isfinite([existing_MW_water,maximum_inflow_MW_water]).all() or min(existing_MW_water,maximum_inflow_MW_water)<0:
        raise RuntimeError("HYDRO_SPILL_CAP_NONNEGATIVE_FINITE_FAIL")
    return (float(existing_MW_water) if existing_MW_water>=maximum_inflow_MW_water
            else float(np.nextafter(maximum_inflow_MW_water,np.inf)))


def accept_resolution():
    state = json.loads(STATE.read_text())
    if state["next_phase"] != "H" or state["phases"]["W"]["status"] != "PASS":
        raise RuntimeError("HYDRO_SEQUENTIAL_RESOLUTION_GATE")
    archive = QA / "H_PRE_ZONAL_RESOLUTION_CHECKPOINT.json"
    if not archive.exists():
        dump_json(archive,state["phases"]["H"])
    state["hydro_zonal_resolution"] = {"path":str(AUTHORITY.relative_to(ROOT)),
        "sha256":sha256_file(AUTHORITY),"accepted_by":"EXPLICIT_USER_PROJECT_DECISION"}
    state["phases"]["H"]["unresolved_items"] = []
    state["phases"]["H"]["superseded_source_block"] = str(archive.relative_to(ROOT))
    dump_json(STATE,state)
    return record_progress("H","Accepted independent zonal runoff conversion and forced-inflow spill safety rule",
        {"accepted_prior_non_solving_tests":45,"new_transformation_tests":"PENDING"})


def installed_runoff_api():
    import atlite.convert as convert
    path = Path(inspect.getsourcefile(convert))
    recovery = json.loads((QA / "HYDRO_SPATIAL_SOURCE_RECOVERY.json").read_text())
    accepted = next(x for x in recovery["sources"] if x["id"] == "INSTALLED_ATLITE_RUNOFF_AND_HYDRO")
    if sha256_file(path) != accepted["sha256"]:
        raise RuntimeError("HYDRO_INSTALLED_RUNOFF_SOURCE_DRIFT")
    source = inspect.getsource(convert.runoff)
    if (inspect.signature(convert.convert_runoff).parameters["weight_with_height"].default is not True
            or "smooth = 24 * 7" not in source or "lower_threshold_quantile = 5e-3" not in source):
        raise RuntimeError("HYDRO_NATIVE_RUNOFF_SEMANTICS_DRIFT")
    return {"installed_atlite_version":atlite.__version__,"path":str(path),"sha256":accepted["sha256"],
        "weight_with_height":True,"smooth_hours":168,"threshold_quantile":.005,
        "threshold_scope":"NATIVE_FLATTENED_AGGREGATED_RESULT_FOR_SEVEN_SHAPES",
        "aggregate_time":None,"normalize_using_yearly":None}


def generate_runoff():
    cfg = load_yaml(CONFIG)
    geometry = ROOT / cfg["geometry"]
    api = installed_runoff_api()
    profile = OUTPUT / "ZONAL_RUNOFF_2019.parquet"
    receipt_path = QA / "ZONAL_RUNOFF_CONVERSION_RECEIPT.json"
    key = {"config_sha256":sha256_file(CONFIG),"geometry_sha256":sha256_file(geometry),
        "authority_sha256":sha256_file(AUTHORITY),"installed_runoff_source_sha256":api["sha256"]}
    if receipt_path.exists():
        old = json.loads(receipt_path.read_text())
        if old["controls"] != key or sha256_file(profile) != old["profile_sha256"]:
            raise RuntimeError("HYDRO_CACHED_ZONAL_RUNOFF_DRIFT")
        return pd.read_parquet(profile)
    shapes = gpd.read_file(geometry)
    columns = [c for c in ("bus","zone","market_zone","name") if c in shapes.columns]
    if len(columns) != 1:
        raise RuntimeError("HYDRO_ZONE_GEOMETRY_KEY_AMBIGUOUS")
    zone_column = columns[0]
    shapes = shapes.set_index(zone_column)
    if set(shapes.index) != set(ZONES) or not shapes.index.is_unique:
        raise RuntimeError("HYDRO_ZONE_GEOMETRY_COVERAGE_FAIL")
    shapes = shapes.loc[list(ZONES)].to_crs(4326)
    if not shapes.is_valid.all() or shapes.geometry.is_empty.any():
        raise RuntimeError("HYDRO_ZONE_GEOMETRY_INVALID")
    with no_models_or_solves():
        cutout = atlite.Cutout(ROOT / cfg["cutout"])
        # Read only cells overlapping the supplied geometries, plus a full cell
        # margin. This is a view of the same cutout, never a new weather file.
        x0,y0,x1,y1 = shapes.total_bounds
        cutout.data = cutout.data.sel(x=slice(x0-.6,x1+.6),y=slice(y0-.6,y1+.6))
        runoff = cutout.runoff(shapes=shapes.geometry,weight_with_height=True,
            smooth=True,lower_threshold_quantile=True,aggregate_time=None,
            normalize_using_yearly=None,show_progress=False,
            dask_kwargs={"scheduler":"threads","num_workers":4})
    dimension = next(d for d in runoff.dims if d != "time")
    runoff = runoff.rename({dimension:"zone"}).transpose("time","zone")
    observed = pd.DatetimeIndex(runoff.time.values).tz_localize("UTC")
    frames, stats, pairwise, old_compare = [], [], [], []
    normalized = {}
    old = pd.read_parquet(ROOT / "stage_a_inputs/temporal_2019_v1_0/cache/MEM_ETX7B3_PyPSA_Eur_Runoff_2019_Cache_v1.0.parquet")
    for zone in ZONES:
        values = runoff.sel(zone=zone).to_numpy()
        normalized[zone] = validate_shape(values,observed)
        frames.append(pd.DataFrame({"snapshot":observed,"zone":zone,"raw_runoff_proxy":values,
            "normalized_annual_share":normalized[zone]}))
        stats.append({"zone":zone,"snapshots":len(values),"min":float(values.min()),"mean":float(values.mean()),
            "median":float(np.median(values)),"p5":float(np.quantile(values,.05)),"p95":float(np.quantile(values,.95)),
            "max":float(values.max()),"zero_hours":int((values==0).sum()),"NaN_inf_count":int((~np.isfinite(values)).sum()),
            "annual_raw_runoff_sum":float(values.sum()),"normalized_sum":float(normalized[zone].sum()),
            "source_geometry":zone,"source_geometry_sha256":key["geometry_sha256"],"status":"PASS"})
        for region in ("IT_MAINLAND_CLUSTER","IT_SARDINIA_CLUSTER"):
            prior = old.loc[old.source_region.eq(region)].sort_values("snapshot")
            reference = validate_shape(prior.raw_runoff_value,prior.snapshot)
            old_compare.append({"zone":zone,"old_source_region":region,
                "normalized_hourly_MAE":float(np.mean(np.abs(normalized[zone]-reference))),
                "normalized_hourly_max_abs":float(np.max(np.abs(normalized[zone]-reference))),
                "correlation":float(np.corrcoef(normalized[zone],reference)[0,1])})
    for i,a in enumerate(ZONES):
        for b in ZONES[i+1:]:
            pairwise.append({"zone_A":a,"zone_B":b,"correlation":float(np.corrcoef(normalized[a],normalized[b])[0,1]),
                "max_abs_normalized_difference":float(np.max(np.abs(normalized[a]-normalized[b]))),
                "exact_duplicate":bool(np.array_equal(normalized[a],normalized[b]))})
    if any(p["exact_duplicate"] for p in pairwise):
        raise RuntimeError("HYDRO_UNEXPECTED_CROSS_ZONE_EXACT_DUPLICATE")
    OUTPUT.mkdir(parents=True,exist_ok=True)
    frame = pd.concat(frames,ignore_index=True)
    frame.to_parquet(profile,index=False)
    pd.DataFrame(stats).to_csv(QA/"ZONAL_RUNOFF_TEMPORAL_QA.csv",index=False)
    pd.DataFrame(pairwise).to_csv(QA/"ZONAL_RUNOFF_PAIRWISE_QA.csv",index=False)
    pd.DataFrame(old_compare).to_csv(QA/"ZONAL_RUNOFF_VS_IT2_IT4.csv",index=False)
    receipt = {"state":"SEVEN_ZONE_NATIVE_RUNOFF_TEMPORAL_QA_PASS","controls":key,"native_API":api,
        "cutout_path":cfg["cutout"],"accepted_cutout_hash_reused":"e6cf2b4c9d463d64b4dfb3a613ef20c3c69e99624daadeee6ee9c76cf74e9d1f",
        "read_view_bounds_lon_lat":[float(x0-.6),float(y0-.6),float(x1+.6),float(y1+.6)],
        "spatial_adapter":cfg["spatial_adapter"],"PLANT_CATCHMENT_ROUTING_REQUIRED":False,
        "WITHIN_ZONE_NATURAL_HYDRO_CLASSES_SHARE_RUNOFF_DRIVER":True,
        "IT2_IT4_SINGLE_SHAPE_INHERITANCE_REMOVED":True,"geometries_are_watersheds":False,
        "independent_geometry_aggregations":7,"exact_duplicate_pairs":0,"snapshots_per_zone":8760,
        "profile_path":str(profile.relative_to(ROOT)),"profile_sha256":sha256_file(profile),
        "raw_runoff_units":"HEIGHT_WEIGHTED_GEOGRAPHIC_RUNOFF_PROXY_NOT_MW_OR_MWH_WATER",
        "no_EIA_annual_normalization":True,"optimization_model_constructed":False,
        "production_optimization_executed":False,"production_solver_invocations":0,"new_weather_downloads":0}
    dump_json(receipt_path,receipt)
    return frame


def candidate_water_profiles(runoff):
    """Scale accepted annual controls; report, never repair, physical conflicts."""
    shapes = {z:runoff.loc[runoff.zone.eq(z)].sort_values("snapshot").normalized_annual_share.to_numpy() for z in ZONES}
    census = pd.read_csv(QA/"HYDRO_CURRENT_LINEAGE_AND_STATE_CENSUS.csv")
    census = census.loc[census.annual_inflow_MWh_water.gt(0)]
    records, chunks, conflicts = [], [], []
    for row in census.itertuples(index=False):
        values = _exact_scaled_profile(shapes[row.zone],row.annual_inflow_MWh_water)
        residual = float(values.sum()-row.annual_inflow_MWh_water)
        if abs(residual)>1e-6:
            raise RuntimeError("HYDRO_ZONAL_ANNUAL_WATER_CONTROL_FAIL")
        maximum = float(values.max())
        if row.hydro_class == "RUN_OF_RIVER":
            pu = values*row.turbine_efficiency/row.turbine_MW_NET
            if float(pu.max())>1+1e-10:
                for index in np.flatnonzero(pu>1+1e-10):
                    conflicts.append({"year":row.year,"scenario":row.scenario,"zone":row.zone,
                        "hydro_id":row.runtime_id,"snapshot":str(runoff.loc[runoff.zone.eq(row.zone)].sort_values("snapshot").snapshot.iloc[index]),
                        "natural_inflow_MW_water":float(values[index]),"electrical_equivalent_MW":float(values[index]*row.turbine_efficiency),
                        "frozen_NET_turbine_MW":row.turbine_MW_NET,"required_p_max_pu":float(pu[index])})
        chunks.append(pd.DataFrame({"snapshot":runoff.loc[runoff.zone.eq(row.zone)].sort_values("snapshot").snapshot.to_numpy(),
            "year":row.year,"scenario":row.scenario,"zone":row.zone,"hydro_class":row.hydro_class,
            "hydro_id":row.runtime_id,"inflow_MW_water_equivalent":values,
            "annual_natural_inflow_MWh_water":row.annual_inflow_MWh_water}))
        records.append({"year":row.year,"scenario":row.scenario,"zone":row.zone,"hydro_class":row.hydro_class,
            "hydro_id":row.runtime_id,"annual_natural_inflow_MWh_water":row.annual_inflow_MWh_water,
            "annual_residual_MWh":residual,"max_forced_inflow_MW_water":maximum,
            "max_electrical_equivalent_MW":maximum*row.turbine_efficiency,"frozen_NET_turbine_MW":row.turbine_MW_NET,
            "existing_spill_MW_water":row.spill_MW_water_cap if row.hydro_class!='RUN_OF_RIVER' else 0.,
            "spill_cap_insufficient":bool(row.hydro_class!='RUN_OF_RIVER' and row.spill_MW_water_cap<maximum),
            "ROR_required_max_pu":maximum*row.turbine_efficiency/row.turbine_MW_NET if row.hydro_class=='RUN_OF_RIVER' else None})
    pd.DataFrame(records).to_csv(QA/"ZONAL_HYDRO_ANNUAL_WATER_AND_POWER_QA.csv",index=False)
    if conflicts:
        pd.DataFrame(conflicts).to_csv(QA/"ZONAL_ROR_POWER_COMPATIBILITY_CONFLICTS.csv",index=False)
    pd.concat(chunks,ignore_index=True).to_parquet(OUTPUT/"ZONAL_NATURAL_INFLOW_CANDIDATE_2019.parquet",index=False)
    receipt = {"state":"HYDRO_ROR_TURBINE_COMPATIBILITY_FAIL" if conflicts else "HYDRO_ZONAL_ANNUAL_AND_ROR_POWER_QA_PASS",
        "natural_inflow_cases":len(records),"annual_water_controls_PASS":len(records),
        "ROR_incompatible_hours":len(conflicts),"ROR_incompatible_cases":len({(r['year'],r['scenario'],r['zone']) for r in conflicts}),
        "spill_caps_requiring_safety_update":sum(r['spill_cap_insufficient'] for r in records),
        "network_exports":0,"optimization_model_constructed":False,"production_solver_invocations":0}
    dump_json(QA/"ZONAL_HYDRO_CANDIDATE_WATER_QA_RECEIPT.json",receipt)
    return receipt


def ror_turbine_mapping(water, net_MW, efficiency):
    """Native fixed-turbine saturation; never renormalize annual natural water."""
    water = np.asarray(water, dtype=float)
    if not np.isfinite(water).all() or (water < 0).any() or net_MW <= 0 or not 0 < efficiency <= 1:
        raise RuntimeError("HYDRO_ROR_PHYSICAL_MAPPING_FAIL")
    accessible = np.minimum(water, net_MW / efficiency)
    bypass = water - accessible
    pu = np.minimum(water * efficiency / net_MW, 1.)
    np.testing.assert_allclose(accessible + bypass, water, atol=1e-12, rtol=0)
    np.testing.assert_allclose(pu * net_MW, accessible * efficiency, atol=1e-9, rtol=0)
    return pu, accessible, bypass


COMPONENTS = ("buses", "carriers", "loads", "generators", "links", "stores", "storage_units",
              "lines", "transformers", "shunt_impedances", "global_constraints", "line_types", "transformer_types")


def verify_hydro_derivative(parent, output, case, census):
    """Exact permitted-object comparison plus annual water/unit boundary checks."""
    pd.testing.assert_index_equal(parent.snapshots, output.snapshots)
    validate_shape(np.ones(len(output.snapshots)), output.snapshots)
    pd.testing.assert_frame_equal(parent.snapshot_weightings, output.snapshot_weightings,
                                  check_dtype=False, check_freq=False, check_exact=True)
    names = set(case.hydro_id)
    ror = set(case.loc[case.hydro_class.eq("RUN_OF_RIVER"), "hydro_id"])
    inflow = {"INFLOW_" + safe_id(i) for i in names-ror}
    spill = {"SPILL_" + safe_id(i) for i in names-ror}
    for attr in COMPONENTS:
        old, new = getattr(parent, attr), getattr(output, attr)
        if attr == "generators":
            pd.testing.assert_frame_equal(old.drop(columns="p_nom"),new.drop(columns="p_nom"),
                                          check_dtype=False,check_exact=True)
            untouched = old.index.difference(list(spill))
            pd.testing.assert_series_equal(old.loc[untouched,"p_nom"],new.loc[untouched,"p_nom"],
                                           check_dtype=False,check_exact=True)
        else:
            pd.testing.assert_frame_equal(old,new,check_dtype=False,check_exact=True)
        old_t, new_t = getattr(parent,attr+"_t",{}),getattr(output,attr+"_t",{})
        if set(old_t) != set(new_t):
            raise RuntimeError("HYDRO_DYNAMIC_SCHEMA_DRIFT")
        for field, values in old_t.items():
            permitted = (ror|inflow if field=="p_max_pu" else inflow) if attr=="generators" and field in {"p_min_pu","p_max_pu"} else set()
            pd.testing.assert_index_equal(values.columns,new_t[field].columns)
            unchanged = values.columns.difference(list(permitted))
            pd.testing.assert_frame_equal(values[unchanged],new_t[field][unchanged],
                                          check_dtype=False,check_freq=False,check_exact=True)
    changes, checks, ror_summary, ror_hours = [], [], [], []
    for hydro_id, rows in case.groupby("hydro_id",sort=True):
        rows = rows.sort_values("snapshot")
        times = pd.DatetimeIndex(pd.to_datetime(rows.snapshot,utc=True)).tz_localize(None)
        if not times.equals(output.snapshots):
            raise RuntimeError("HYDRO_TARGET_CHRONOLOGY_FAIL")
        water = rows.inflow_MW_water_equivalent.to_numpy()
        row = census.loc[census.runtime_id.eq(hydro_id)].iloc[0]
        annual = float(np.dot(water,output.snapshot_weightings.generators))
        if abs(annual-row.annual_inflow_MWh_water)>1e-6:
            raise RuntimeError("HYDRO_ANNUAL_WATER_DRIFT")
        if row.hydro_class=="RUN_OF_RIVER":
            pu, accessible, bypass = ror_turbine_mapping(water,row.turbine_MW_NET,row.turbine_efficiency)
            np.testing.assert_array_equal(output.generators_t.p_max_pu[hydro_id],pu)
            if output.generators.at[hydro_id,"p_nom"] != parent.generators.at[hydro_id,"p_nom"]:
                raise RuntimeError("HYDRO_ROR_NET_MW_DRIFT")
            ror_summary.append({"hydro_id":hydro_id,"zone":row.zone,"natural_MWh_water":annual,
                "turbine_accessible_MWh_water":float(accessible.sum()),"unavoidable_bypass_MWh_water":float(bypass.sum()),
                "turbine_accessible_electrical_MWh":float((pu*row.turbine_MW_NET).sum()),
                "overflow_hours":int((bypass>0).sum()),"uncapped_max_pu":float((water*row.turbine_efficiency/row.turbine_MW_NET).max()),
                "status":"PASS_NATIVE_TURBINE_SATURATION_NOT_ANNUAL_RENORMALIZATION"})
            ror_hours.append(rows[["snapshot","year","scenario","zone","hydro_id"]].assign(
                natural_inflow_MW_water=water,turbine_accessible_MW_water=accessible,
                unavoidable_bypass_MW_water=bypass,electrical_potential_MW=pu*row.turbine_MW_NET))
        else:
            generator, spill_id = "INFLOW_"+safe_id(hydro_id),"SPILL_"+safe_id(hydro_id)
            pmax = output.generators_t.p_max_pu[generator].to_numpy()
            np.testing.assert_array_equal(pmax,output.generators_t.p_min_pu[generator])
            actual = pmax*output.generators.at[generator,"p_nom"]
            np.testing.assert_allclose(actual,water,atol=1e-9,rtol=0)
            if abs(float(actual.sum())-row.annual_inflow_MWh_water)>1e-6:
                raise RuntimeError("HYDRO_EXPORTED_FORCED_ANNUAL_WATER_DRIFT")
            maximum = max(float(water.max()),float(actual.max()))
            old_cap = float(parent.generators.at[spill_id,"p_nom"])
            expected = spill_safety_cap(old_cap,maximum)
            if output.generators.at[spill_id,"p_nom"] != expected:
                raise RuntimeError("HYDRO_SPILL_SAFETY_CAP_DRIFT")
            if old_cap!=expected:
                changes.append({"spill_id":spill_id,"zone":row.zone,"hydro_class":row.hydro_class,
                    "old_MW_water":old_cap,"max_forced_inflow_MW_water":maximum,"new_MW_water":expected,
                    "numerical_tolerance_MW_water":expected-maximum,"pump_MW_included":False,
                    "role":"NON_BINDING_FORCED_INFLOW_SAFETY_BOUND"})
        checks.append({"hydro_id":hydro_id,"zone":row.zone,"hydro_class":row.hydro_class,
                       "annual_MWh_water":annual,"annual_residual_MWh":annual-row.annual_inflow_MWh_water,"status":"PASS"})
    # Pure PHS and shared mixed states are protected by the exact static and
    # non-natural dynamic comparison, including Store e_nom/cyclicity and links.
    for row in census.loc[census.hydro_class.eq("PURE_PHS")].itertuples():
        if "INFLOW_"+safe_id(row.runtime_id) in output.generators.index:
            raise RuntimeError("HYDRO_PURE_PHS_NATURAL_INFLOW_FAIL")
    for key,value in parent.meta.items():
        if output.meta.get(key)!=value:
            raise RuntimeError(f"HYDRO_PARENT_METADATA_DRIFT: {key}")
    return changes,checks,ror_summary,ror_hours


def promote_hydro():
    import pypsa
    state = json.loads(STATE.read_text())
    if state["next_phase"]!="H" or state["phases"]["W"]["status"]!="PASS":
        raise RuntimeError("HYDRO_PROMOTION_SEQUENTIAL_GATE")
    review = json.loads((QA/"ROR_TURBINE_SATURATION_AUTHORITY_REVIEW.json").read_text())
    if review["status"]!="NATIVE_PHYSICAL_MAPPING_ALREADY_AUTHORIZED":
        raise RuntimeError("HYDRO_ROR_AUTHORITY_FAIL")
    candidate = OUTPUT/"ZONAL_NATURAL_INFLOW_CANDIDATE_2019.parquet"
    data = pd.read_parquet(candidate)
    census_all = pd.read_csv(QA/"HYDRO_CURRENT_LINEAGE_AND_STATE_CENSUS.csv")
    parents = state["phases"]["W"]["successor_parents"]
    if {(p["year"],p["scenario"]) for p in parents}!=set(SCENARIOS):
        raise RuntimeError("HYDRO_PARENT_COVERAGE_FAIL")
    destination = ROOT/"networks/unsolved/final_wind_hydro_v1"
    destination.mkdir(parents=True,exist_ok=True)
    packages, all_changes, all_checks, all_ror, all_hours, mapping = [],[],[],[],[],[]
    with no_models_or_solves():
        for p in parents:
            source = ROOT/p["path"]
            if sha256_file(source)!=p["sha256"]:
                raise RuntimeError("HYDRO_PARENT_HASH_FAIL")
            parent = pypsa.Network(source)
            output = parent.copy()
            case = data.loc[data.year.eq(p["year"]) & data.scenario.eq(p["scenario"])]
            census = census_all.loc[census_all.year.eq(p["year"]) & census_all.scenario.eq(p["scenario"])]
            if case.hydro_id.nunique()!=24 or len(case)!=24*8760:
                raise RuntimeError("HYDRO_NATURAL_PROFILE_COVERAGE_FAIL")
            for row in census.loc[census.annual_inflow_MWh_water.gt(0)].itertuples():
                water = case.loc[case.hydro_id.eq(row.runtime_id)].sort_values("snapshot").inflow_MW_water_equivalent.to_numpy()
                if row.hydro_class=="RUN_OF_RIVER":
                    output.generators_t.p_max_pu[row.runtime_id] = ror_turbine_mapping(water,row.turbine_MW_NET,row.turbine_efficiency)[0]
                else:
                    generator = "INFLOW_"+safe_id(row.runtime_id)
                    pu = water/parent.generators.at[generator,"p_nom"]
                    output.generators_t.p_min_pu[generator] = pu
                    output.generators_t.p_max_pu[generator] = pu
                    maximum = max(float(water.max()),float((pu*parent.generators.at[generator,"p_nom"]).max()))
                    spill = "SPILL_"+safe_id(row.runtime_id)
                    output.generators.at[spill,"p_nom"] = spill_safety_cap(parent.generators.at[spill,"p_nom"],maximum)
            output.meta = {**parent.meta,"final_hydro_spatial_v1":{
                "adapter":"ATLITE_RUNOFF_OVER_ACCEPTED_MARKET_ZONE_POLYGONS","weather_year":2019,
                "annual_water_control_unchanged":True,"ROR_mapping":"NATIVE_FIXED_NET_TURBINE_SATURATION_WITH_EXPLICIT_BYPASS",
                "water_value":"NATIVE_STORE_ENERGY_BALANCE_DUAL_FIXED_COMMITMENT_LP","assign_all_duals_required":True,
                "within_zone_natural_classes_share_driver":True,"plant_catchment_routing_required":False,
                "parent_sha256":p["sha256"],"runoff_sha256":sha256_file(OUTPUT/"ZONAL_RUNOFF_2019.parquet"),
                "spill_role":"NON_BINDING_FORCED_INFLOW_SAFETY_BOUND","production_executed":False}}
            filename=f"MEM_{p['year']}_{p['scenario'].upper()}_FINAL_WIND_HYDRO_V1_8760h_UNSOLVED.nc"
            path=destination/filename
            if path.exists():
                raise RuntimeError("HYDRO_SUCCESSOR_OVERWRITE_REFUSED")
            verify_hydro_derivative(parent,output,case,census)
            output.export_to_netcdf(path)
            reloaded=pypsa.Network(path)
            changes,checks,ror,hours=verify_hydro_derivative(parent,reloaded,case,census)
            stamp={"year":p["year"],"scenario":p["scenario"]}
            all_changes.extend([{**stamp,**r} for r in changes])
            all_checks.extend([{**stamp,**r} for r in checks])
            all_ror.extend([{**stamp,**r} for r in ror]); all_hours.extend(hours)
            for row in census.loc[census.hydro_class.ne("RUN_OF_RIVER")].itertuples():
                mapping.append({**stamp,"store_id":row.Store_id,"state_id":row.runtime_id,"zone":row.zone,"hydro_class":row.hydro_class})
            packages.append({**stamp,"parent":p["path"],"parent_sha256":p["sha256"],
                "path":str(path.relative_to(ROOT)),"sha256":sha256_file(path),"status":"PASS",
                "snapshots":8760,"natural_inflow_controls":len(checks),"spill_rows_changed":len(changes),
                "non_H_objects_exact":True,"export_reload_exact":True,"water_states":20,
                "hydro_NET_MW":23294.,"PHS_discharge_MW":7252.3,"PHS_pump_MW":6400.,"PHS_e_nom_MWh":53000.})
            if sha256_file(source)!=p["sha256"]:
                raise RuntimeError("HYDRO_PARENT_MODIFIED")
    pd.DataFrame(all_changes).to_csv(QA/"SPILL_SAFETY_CAP_CORRECTIONS.csv",index=False)
    pd.DataFrame(all_checks).to_csv(QA/"EXPORTED_ANNUAL_WATER_RECONCILIATION.csv",index=False)
    pd.DataFrame(all_ror).to_csv(QA/"ROR_TURBINE_ACCESSIBLE_AND_BYPASS_QA.csv",index=False)
    pd.concat(all_hours,ignore_index=True).to_parquet(OUTPUT/"ROR_ACCESSIBLE_AND_BYPASS_2019.parquet",index=False)
    pd.DataFrame(mapping).to_csv(QA/"WATER_VALUE_STATE_MAPPING.csv",index=False)
    receipt={"state":"HYDRO_SPATIAL_AND_WATER_VALUE_PASS","packages":packages,
        "annual_water_controls_PASS":len(all_checks),"spill_rows_changed":len(all_changes),
        "raw_ROR_overrating_diagnostic_retained":True,"ROR_turbine_mapping_native":True,
        "within_zone_natural_hydro_classes_share_runoff_driver":True,"plant_catchment_routing_required":False,
        "IT2_IT4_SINGLE_SHAPE_INHERITANCE_REMOVED":True,"HYDRO_SPATIAL_TEMPORAL_ADAPTER":"ATLITE_RUNOFF_OVER_ACCEPTED_MARKET_ZONE_POLYGONS",
        "WATER_VALUE_METHOD":"NATIVE_STORE_ENERGY_BALANCE_DUAL_FIXED_COMMITMENT_LP",
        "CYCLIC_ANNUAL_SOC_RETAINED_AS_NEUTRAL_NO_NET_WATER_CONDITION":True,
        "state_balance":"e_t=(1-standing_loss)^w*e_previous+w*(forced_inflow+eta_pump*pump_p0-turbine_p0-spill_p)",
        "electric_generation":"-turbine_p1=eta_turbine*turbine_p0",
        "first_snapshot_predecessor":"LAST_OUTPUT_STATE_NOT_FIRST_OUTPUT_STATE",
        "optimization_model_constructed":False,"production_optimization_executed":False,"production_solver_invocations":0}
    dump_json(QA/"HYDRO_SUCCESSOR_STRUCTURAL_QA.json",receipt)
    return receipt


def close_phase_h(test_result):
    from .final_methodology_closure import record_phase
    structural=json.loads((QA/"HYDRO_SUCCESSOR_STRUCTURAL_QA.json").read_text())
    if structural["state"]!="HYDRO_SPATIAL_AND_WATER_VALUE_PASS" or len(structural["packages"])!=6:
        raise RuntimeError("HYDRO_CLOSURE_STRUCTURAL_FAIL")
    transfer=ROOT/"docs/final_methodology_closure/MEM_FINAL_METHODOLOGY_PHASE_H_ZONAL_RUNOFF_TRANSFER.md"
    if not transfer.exists():
        raise RuntimeError("HYDRO_TRANSFER_MISSING")
    verification=QA/"HYDRO_FINAL_VERIFICATION.json"
    successors=[{k:p[k] for k in ("year","scenario","path","sha256")} for p in structural["packages"]]
    dump_json(verification,{**structural,"tests":test_result,"successor_parents":successors,
        "superseded_block":"H_PRE_ZONAL_RESOLUTION_CHECKPOINT.json",
        "uncapped_ROR_conflict_receipt":"ZONAL_HYDRO_CANDIDATE_WATER_QA_RECEIPT.json",
        "ROR_resolution":"ROR_TURBINE_SATURATION_AUTHORITY_REVIEW.json"})
    artifacts=[CONFIG,AUTHORITY,transfer,Path(__file__),ROOT/"src/mem_model/reporting/water_values.py",
        ROOT/"tests/test_final_hydro_spatial.py",ROOT/"tests/test_water_values.py",verification]
    artifacts+=list(OUTPUT.glob("*"))
    artifacts+=[QA/n for n in ("ZONAL_RUNOFF_CONVERSION_RECEIPT.json","ZONAL_RUNOFF_TEMPORAL_QA.csv",
        "ZONAL_RUNOFF_PAIRWISE_QA.csv","ZONAL_RUNOFF_VS_IT2_IT4.csv","ZONAL_HYDRO_ANNUAL_WATER_AND_POWER_QA.csv",
        "ZONAL_ROR_POWER_COMPATIBILITY_CONFLICTS.csv","ZONAL_HYDRO_CANDIDATE_WATER_QA_RECEIPT.json",
        "ROR_TURBINE_SATURATION_AUTHORITY_REVIEW.json","SPILL_SAFETY_CAP_CORRECTIONS.csv",
        "EXPORTED_ANNUAL_WATER_RECONCILIATION.csv","ROR_TURBINE_ACCESSIBLE_AND_BYPASS_QA.csv",
        "WATER_VALUE_STATE_MAPPING.csv","HYDRO_SUCCESSOR_STRUCTURAL_QA.json")]
    artifacts += [ROOT/p["path"] for p in successors]
    manifest=QA/"HYDRO_FINAL_OUTPUT_MANIFEST.csv"
    pd.DataFrame([{"path":str(p.relative_to(ROOT)),"bytes":p.stat().st_size,"sha256":sha256_file(p)}
                  for p in sorted(set(artifacts))]).to_csv(manifest,index=False)
    state=json.loads(STATE.read_text())
    state=record_phase("H","PASS",parents=state["phases"]["W"]["successor_parents"],
        artifacts=[*artifacts,manifest],decisions=["SEVEN_ZONE_NATIVE_ATLITE_RUNOFF_ADAPTER",
            "EXACT_ANNUAL_NATURAL_WATER_CONTROLS","NATIVE_FIXED_ROR_TURBINE_SATURATION_EXPLICIT_BYPASS",
            "NONBINDING_FORCED_INFLOW_SPILL_SAFETY_CAP_NO_PUMP_ALLOWANCE",
            "NATIVE_STORE_WATER_VALUE_DUAL_FIXED_COMMITMENT_LP","CYCLIC_ANNUAL_SOC_RETAINED_AS_NEUTRAL_NO_NET_WATER_CONDITION"],
        tests=test_result,unresolved=[])
    state["phases"]["H"].update({"phase_result":"HYDRO_SPATIAL_AND_WATER_VALUE_PASS",
        "successor_parents":successors,"manifest":str(manifest.relative_to(ROOT)),
        "receipt":str(verification.relative_to(ROOT)),"current_transfer":str(transfer.relative_to(ROOT))})
    state["optimization_model_constructed"]=False
    dump_json(STATE,state)
    return {"state":state["phases"]["H"]["phase_result"],"next_phase":state["next_phase"],"successors":successors}

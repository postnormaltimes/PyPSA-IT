"""Final-family preparation and integrity gates. No model/solver entry point."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa

from .common import ROOT, SCENARIOS, ZONES, dump_json, load_yaml, sha256_file
from .final_methodology_closure import STATE, record_phase
from .final_hydro_spatial import COMPONENTS
from .stage_b_zonal_vre import no_models_or_solves

CONFIG = ROOT/"config/final_methodology_execution_v1.yaml"
QA = ROOT/"qa/final_methodology_closure/final"
NETWORKS = ROOT/"networks/unsolved/final_methodology_v1"
RECEIPT = QA/"FINAL_NETWORK_PREPARATION_RECEIPT.json"
CONTINUOUS_FIELDS = ("committable","p_min_pu","ramp_limit_up","ramp_limit_down",
    "ramp_limit_start_up","ramp_limit_shut_down","min_up_time","min_down_time",
    "start_up_cost","shut_down_cost","stand_by_cost","up_time_before","down_time_before")
EXPECTED_COUNTS = {(2040,"Slow"):130,(2040,"Base"):108,(2040,"High"):108,
                   (2050,"Slow"):86,(2050,"Base"):59,(2050,"High"):59}


def settings():
    value=load_yaml(CONFIG)
    if value['schema_version']!='MEM_FINAL_METHODOLOGY_EXECUTION_V1' or value['variant']!='final_v1' or value['no_automatic_execution'] is not True:
        raise RuntimeError('FINAL_EXECUTION_CONFIG_FAIL')
    expected={'name':'gurobi','required_version':'13.0.3','options':{'Threads':1,'Seed':0},
              'include_objective_constant':False,'adaptive_tuning':False,'MIPGap_override':None}
    if value['solver']!=expected or set(value['manual_authorization'])!={f'{y}_{s}' for y,s in SCENARIOS}:
        raise RuntimeError('FINAL_SOLVER_OR_AUTHORIZATION_CONFIG_FAIL')
    return value


def package(year,scenario):
    if (year,scenario) not in SCENARIOS:
        raise ValueError('FINAL_UNKNOWN_SCENARIO')
    receipt=json.loads(RECEIPT.read_text())
    if receipt['status']!='PASS' or len(receipt['packages'])!=6:
        raise RuntimeError('FINAL_PACKAGE_RECEIPT_FAIL')
    return next(p for p in receipt['packages'] if (p['year'],p['scenario'])==(year,scenario))


def water_mapping(year,scenario):
    table=pd.read_csv(ROOT/settings()['water_mapping'])
    return table.loc[table.year.eq(year)&table.scenario.eq(scenario),['store_id','state_id','zone','hydro_class']].copy()


def manual_gate(year,scenario):
    if (year,scenario) not in SCENARIOS:
        raise ValueError('FINAL_UNKNOWN_SCENARIO')
    state=json.loads(STATE.read_text())
    if state['state']!='FINAL_CANONICAL_NETWORKS_PREPARED' or any(state['phases'][p]['status']!='PASS' for p in 'WHNCF'):
        raise RuntimeError('FINAL_EXECUTION_LOCKED: closure not PASS')
    if state.get('semantic_parity',{}).get('status')!='PASS':
        raise RuntimeError('FINAL_EXECUTION_LOCKED: native semantic parity not PASS')
    if settings()['manual_authorization'][f'{year}_{scenario}'] is not True:
        raise RuntimeError('FINAL_EXECUTION_LOCKED: requires explicit case manual authorization')
    return f'FINAL_V1_MANUAL_EXECUTION_{year}_{scenario.upper()}'


def _equal(parent,output,*,reference_names=()):
    pd.testing.assert_index_equal(parent.snapshots,output.snapshots)
    pd.testing.assert_frame_equal(parent.snapshot_weightings,output.snapshot_weightings,check_dtype=False,check_exact=True,check_freq=False)
    for attr in COMPONENTS:
        old,new=getattr(parent,attr),getattr(output,attr)
        if attr=='generators' and len(reference_names):
            pd.testing.assert_frame_equal(old.drop(columns=list(CONTINUOUS_FIELDS)),new.drop(columns=list(CONTINUOUS_FIELDS)),check_dtype=False,check_exact=True)
            untouched=old.index.difference(reference_names)
            pd.testing.assert_frame_equal(old.loc[untouched,list(CONTINUOUS_FIELDS)],new.loc[untouched,list(CONTINUOUS_FIELDS)],check_dtype=False,check_exact=True)
        else:
            pd.testing.assert_frame_equal(old,new,check_dtype=False,check_exact=True)
        old_t,new_t=getattr(parent,attr+'_t',{}),getattr(output,attr+'_t',{})
        if set(old_t)!=set(new_t):
            raise RuntimeError('FINAL_DYNAMIC_SCHEMA_DRIFT')
        for field,values in old_t.items():
            excluded=reference_names if attr=='generators' and field in CONTINUOUS_FIELDS else []
            columns=values.columns.difference(excluded)
            pd.testing.assert_frame_equal(values[columns],new_t[field][columns],check_dtype=False,check_exact=True,check_freq=False)
    for k,v in parent.meta.items():
        if output.meta.get(k)!=v:
            raise RuntimeError('FINAL_INHERITED_METADATA_DRIFT: '+k)


def structural_check(network,year,scenario,*,continuous=False):
    times=pd.date_range('2019-01-01',periods=8760,freq='h',name='snapshot')
    if not network.snapshots.equals(times) or not set(ZONES).issubset(network.buses.index):
        raise RuntimeError('FINAL_CHRONOLOGY_OR_ZONE_FAIL')
    for attr in COMPONENTS:
        frame=getattr(network,attr)
        if frame.index.has_duplicates:
            raise RuntimeError('FINAL_DUPLICATE_ID_FAIL')
        for field in ('p_nom_extendable','e_nom_extendable','s_nom_extendable'):
            if field in frame and frame[field].astype(bool).any():
                raise RuntimeError('FINAL_EXTENDABLE_CAPACITY_FAIL')
    generators=network.generators
    for field in ('p_nom','efficiency','marginal_cost','sign'):
        if not np.isfinite(generators[field].to_numpy(dtype=float)).all():
            raise RuntimeError('FINAL_STATIC_GENERATOR_FINITE_FAIL: '+field)
    for component in ('Generator','Link','Store','Load'):
        fields={'Generator':('p_min_pu','p_max_pu','marginal_cost'),
                'Link':('p_min_pu','p_max_pu','efficiency','marginal_cost'),
                'Store':('e_min_pu','e_max_pu','standing_loss'),'Load':('p_set',)}[component]
        for field in fields:
            if not np.isfinite(network.get_switchable_as_dense(component,field).to_numpy()).all():
                raise RuntimeError(f'FINAL_DYNAMIC_FINITE_FAIL: {component}/{field}')
    names=generators.index[generators.committable]
    if len(names)!=(0 if continuous else EXPECTED_COUNTS[(year,scenario)]):
        raise RuntimeError('FINAL_UC_COUNT_FAIL')
    if len(names):
        maximum=network.get_switchable_as_dense('Generator','p_max_pu')[names]
        incompatible=(maximum.gt(0)&maximum.lt(generators.loc[names,'p_min_pu'],axis=1))
        if incompatible.any().any():
            raise RuntimeError('FINAL_UC_AVAILABILITY_FAIL')
        if not all(str(n).startswith(('UC__','UC2A__')) for n in names):
            raise RuntimeError('FINAL_UNEXPECTED_COMMITTABLE_FAIL')
    p2x=generators.loc[[f'P2X_{z}' for z in ZONES]]
    if not p2x.sign.eq(-1).all() or p2x.committable.any() or p2x.p_nom.le(0).any():
        raise RuntimeError('FINAL_P2X_ARCHITECTURE_FAIL')
    constraints=network.global_constraints.loc[[f'P2X_ANNUAL_{z}' for z in ZONES]]
    if not constraints.sense.eq('==').all() or not constraints.type.eq('operational_limit').all() or constraints.constant.le(0).any():
        raise RuntimeError('FINAL_P2X_ANNUAL_CONSTRAINT_FAIL')
    if getattr(network,'objective',None) is not None or not network.generators_t.p.empty:
        raise RuntimeError('FINAL_INPUT_MUST_BE_UNSOLVED')
    return {'snapshots':8760,'UC_units':len(names),'P2X_generators':7,'extendable_assets':0,
            'availability_conflicts':0,'status':'PASS','optimization_model_constructed':False}


def continuous_counterfactual(final,original,plan):
    """Retain calibrated child size/efficiency/cost; restore native LP controls.

    Aggregating children back into a homogeneous thermal cohort would erase
    accepted v2A heat-rate heterogeneity. Original P2X parents supply only the
    pre-UC operational-bound attributes, never weather or system assumptions.
    """
    output=final.copy()
    names=final.generators.index[final.generators.committable]
    indexed=plan.set_index('child_id')
    if set(names)!=set(indexed.index):
        raise RuntimeError('FINAL_REFERENCE_CROSSWALK_FAIL')
    for child in names:
        parent=indexed.at[child,'parent_id']
        for field in CONTINUOUS_FIELDS:
            output.generators.at[child,field]=original.generators.at[parent,field]
            dynamic=getattr(output.generators_t,field,None)
            original_dynamic=getattr(original.generators_t,field,None)
            if dynamic is not None and child in dynamic:
                dynamic[child]=(original_dynamic[parent] if original_dynamic is not None and parent in original_dynamic
                                else original.generators.at[parent,field])
    if output.generators.committable.any():
        raise RuntimeError('FINAL_REFERENCE_NOT_CONTINUOUS')
    output.meta={**final.meta,'final_continuous_counterfactual':
        'CALIBRATED_V2A_CHILDREN_WITH_ORIGINAL_CONTINUOUS_OPERATING_BOUNDS'}
    _equal(final,output,reference_names=names)
    return output


def approved_change_inventory(baseline, final, year, scenario):
    """Enumerate every changed input object against the accepted zonal-v2A parent.

    All other cells/series must be exact. N/C already independently prove
    transport interval equivalence and the frozen directional MW authority.
    """
    pd.testing.assert_index_equal(baseline.snapshots,final.snapshots)
    pd.testing.assert_frame_equal(baseline.snapshot_weightings,final.snapshot_weightings,check_exact=True,check_dtype=False)
    changes=[]
    def add(component,attribute,name,count,maximum,authority):
        changes.append({'year':year,'scenario':scenario,'component':component,'attribute':attribute,
            'object_id':str(name),'changed_cells_or_snapshots':int(count),'max_abs_difference':maximum,
            'approved_authority':authority,'status':'AUTHORIZED_TRANSFORMATION'})
    for attr in COMPONENTS:
        old,new=getattr(baseline,attr),getattr(final,attr)
        if attr=='links':
            transport={'internal_transfer','external_trade','corsica_hub'}
            old_ids=old.index[old.carrier.isin(transport)]
            new_ids=new.index[new.carrier.isin(transport)]
            pd.testing.assert_frame_equal(old.drop(old_ids),new.drop(new_ids),check_exact=True,check_dtype=False)
            for name in old_ids: add(attr,'removed_directional_Link',name,1,None,'N paired-to-signed exact net interval')
            for name in new_ids: add(attr,'added_signed_Link',name,1,None,'N exact net interval; C independent directional authority')
        elif attr=='generators':
            pd.testing.assert_index_equal(old.index,new.index)
            for field in old.columns:
                changed=~(old[field].eq(new[field])|(old[field].isna()&new[field].isna()))
                ids=old.index[changed]
                if len(ids):
                    permitted=(field=='p_nom' and all(str(i).startswith('SPILL_') for i in ids))
                    if not permitted: raise RuntimeError('FINAL_UNAPPROVED_STATIC_GENERATOR_DRIFT: '+field)
                    for name in ids:
                        add(attr,field,name,1,float(abs(new.at[name,field]-old.at[name,field])),'H explicit forced-inflow safety-cap correction')
        else:
            pd.testing.assert_frame_equal(old,new,check_exact=True,check_dtype=False)
        old_t,new_t=getattr(baseline,attr+'_t',{}),getattr(final,attr+'_t',{})
        if set(old_t)!=set(new_t): raise RuntimeError('FINAL_UNAPPROVED_DYNAMIC_SCHEMA_DRIFT')
        for field,table in old_t.items():
            output=new_t[field]
            if attr=='links':
                pd.testing.assert_frame_equal(table.drop(columns=old_ids,errors='ignore'),output.drop(columns=new_ids,errors='ignore'),
                    check_exact=True,check_dtype=False,check_freq=False)
            elif attr=='generators':
                pd.testing.assert_index_equal(table.columns,output.columns)
                for name in table:
                    if table[name].equals(output[name]): continue
                    carrier=old.at[name,'carrier']
                    if field=='p_max_pu' and carrier in {'wind_onshore','wind_offshore'}: authority='W native-cell fixed-capacity site-LCOE'
                    elif field=='p_max_pu' and carrier=='hydro_run_of_river': authority='H zonal runoff; turbine saturation/bypass'
                    elif field in {'p_min_pu','p_max_pu'} and str(name).startswith('INFLOW_'): authority='H zonal runoff; same annual water control'
                    else: raise RuntimeError(f'FINAL_UNAPPROVED_DYNAMIC_GENERATOR_DRIFT: {field}/{name}')
                    delta=(table[name]-output[name]).abs()
                    add(attr,field,name,int(delta.ne(0).sum()),float(delta.max()),authority)
            else:
                pd.testing.assert_frame_equal(table,output,check_exact=True,check_dtype=False,check_freq=False)
    return changes


def protected_artifact_verification(state):
    """Reuse accepted phase receipts; hash checks only, no W/H recomputation."""
    controls=list(state['controlling_parents'])
    for phase in 'WHNC':
        controls+=state['phases'][phase].get('created_artifacts',[])
        controls+=state['phases'][phase].get('controlling_parent_hashes',[])
    unique={r['path']:r['sha256'] for r in controls}
    for path,digest in unique.items():
        if sha256_file(ROOT/path)!=digest:
            raise RuntimeError('FINAL_ACCEPTED_PREDECESSOR_HASH_FAIL: '+path)
    return {'status':'PASS','accepted_artifacts_hash_checked':len(unique),
            'accepted_W_H_QA_reused_not_recomputed':True,'production_results_modified':0}


def build_final():
    from .stage_b_uc1 import parent_path
    state=json.loads(STATE.read_text())
    if state['next_phase']!='F' or any(state['phases'][p]['status']!='PASS' for p in 'WHNC'):
        raise RuntimeError('FINAL_PREDECESSORS_NOT_PASS')
    parity=state.get('semantic_parity',{})
    if parity.get('status')!='PASS' or parity.get('phase_result')!='NATIVE_PYPSA_SEMANTIC_PARITY_AUDIT_PASS':
        raise RuntimeError('FINAL_NATIVE_SEMANTIC_PARITY_NOT_PASS')
    for artifact in parity['artifacts']:
        if sha256_file(ROOT/artifact['path'])!=artifact['sha256']:
            raise RuntimeError('FINAL_NATIVE_SEMANTIC_PARITY_ARTIFACT_DRIFT')
    protected=protected_artifact_verification(state)
    parents=state['phases']['N']['successor_parents']
    corrections=state['phases']['C'].get('successor_parents')
    if corrections:
        parents=corrections
    plan_all=pd.read_csv(ROOT/'qa/stage_b/uc2a_heterogeneous_v1/MEM_UC2A_CHILD_CROSSWALK.csv')
    uc1_build=json.loads((ROOT/'qa/stage_b/uc1_common_v1/MEM_UC1_DETERMINISTIC_BUILD_RECEIPT.json').read_text())
    NETWORKS.mkdir(parents=True,exist_ok=True); QA.mkdir(parents=True,exist_ok=True)
    packages,preservation,changed_objects=[],[],[]
    with no_models_or_solves():
        for p in parents:
            year,scenario=p['year'],p['scenario']
            path=ROOT/p['path']
            if sha256_file(path)!=p['sha256']:
                raise RuntimeError('FINAL_N_C_PARENT_HASH_FAIL')
            parent=pypsa.Network(path)
            baseline_entry=next(r for r in state['controlling_parents'] if (r['year'],r['scenario'])==(year,scenario))
            baseline=pypsa.Network(ROOT/baseline_entry['path'])
            changed_objects+=approved_change_inventory(baseline,parent,year,scenario)
            network=parent.copy()
            network.meta={**parent.meta,'final_methodology_variant':'final_v1',
                          'final_methodology_parent_sha256':p['sha256'],'production_optimization_executed':False}
            structural=structural_check(network,year,scenario)
            final_path=NETWORKS/f'MEM_{year}_{scenario.upper()}_FINAL_METHODOLOGY_V1_8760h_UNSOLVED.nc'
            reference_path=NETWORKS/f'MEM_{year}_{scenario.upper()}_FINAL_METHODOLOGY_V1_CONTINUOUS_REFERENCE_8760h_UNSOLVED.nc'
            if not final_path.exists():
                network.export_to_netcdf(final_path)
            reloaded=pypsa.Network(final_path)
            _equal(network,reloaded); structural_check(reloaded,year,scenario)
            original_path=parent_path(year,scenario)
            expected=next(r for r in uc1_build['structural_packages'] if (r['year'],r['scenario'])==(year,scenario))['parent_sha256']
            if sha256_file(original_path)!=expected:
                raise RuntimeError('FINAL_CONTINUOUS_AUTHORITY_HASH_FAIL')
            original=pypsa.Network(original_path)
            plan=plan_all.loc[plan_all.year.eq(year)&plan_all.scenario.eq(scenario)]
            reference=continuous_counterfactual(reloaded,original,plan)
            if not reference_path.exists():
                reference.export_to_netcdf(reference_path)
            ref_reload=pypsa.Network(reference_path)
            _equal(reference,ref_reload); structural_check(ref_reload,year,scenario,continuous=True)
            if sha256_file(path)!=p['sha256'] or sha256_file(original_path)!=expected:
                raise RuntimeError('FINAL_PROTECTED_PARENT_CHANGED')
            packages.append({'year':year,'scenario':scenario,'path':str(final_path.relative_to(ROOT)),
                'sha256':sha256_file(final_path),'reference_path':str(reference_path.relative_to(ROOT)),
                'reference_sha256':sha256_file(reference_path),'parent':p['path'],'parent_sha256':p['sha256'],
                'continuous_operating_bound_authority':str(original_path.relative_to(ROOT)),
                'continuous_operating_bound_authority_sha256':expected,**structural,
                'export_reload_exact':True,'all_N_C_model_objects_exact':True})
            for family in ('snapshots_weights','rigid_load','P2X','thermal_size_efficiency_cost_UC',
                           'solar','BESS','hydro_PHS_controls','external_prices','topology_capacity_contract'):
                preservation.append({'year':year,'scenario':scenario,'object':family,'status':'EXACT_PRESERVED',
                    'scope_evidence':'W/H/N/C exact transformation receipts + F exact parent comparison'})
            preservation.extend([{'year':year,'scenario':scenario,'object':family,'status':status,
                'scope_evidence':evidence} for family,status,evidence in (
                ('wind_p_max_pu','AUTHORIZED_TEMPORAL_CHANGE','W native-cell fixed-MW siting'),
                ('natural_hydro_ROR_p_max_pu','AUTHORIZED_TEMPORAL_CHANGE','H seven-zone runoff; native fixed-turbine cap and bypass'),
                ('forced_natural_inflow','AUTHORIZED_TEMPORAL_CHANGE','H exact annual MWh-water budgets'),
                ('spill_p_nom_safety','AUTHORIZED_NUMERICAL_BOUND_CHANGE','H explicit insufficient-cap rows; no pump allowance'),
                ('transport_link_representation','EXACT_NET_INTERVAL_TRANSFORMATION','N signed lossless intervals'))])
    receipt={'status':'PASS','state':'FINAL_CANONICAL_NETWORKS_PREPARED_PENDING_FINAL_QA',
        'packages':packages,'UC_networks':6,'continuous_counterfactuals':6,'protected_artifact_verification':protected,
        'reference_rule':'Calibrated child MW/eta/MC retained; pre-UC operating bounds restored from hash-locked same-case P2X parent only.',
        'optimization_model_constructed':False,'production_optimization_executed':False,
        'production_solver_invocations':0,'production_results_modified':0}
    dump_json(RECEIPT,receipt)
    pd.DataFrame(preservation).to_csv(QA/'EXACT_PRESERVATION_MATRIX.csv',index=False)
    pd.DataFrame(packages).to_csv(QA/'FINAL_NETWORK_LINEAGE_AND_HASHES.csv',index=False)
    pd.DataFrame(changed_objects).to_csv(QA/'APPROVED_MODEL_OBJECT_CHANGE_INVENTORY.csv',index=False)
    return receipt


def verify_frozen_contract():
    """Fail closed on missing/changed freeze receipts or manifest before a model.

    Manual-enable flags are separate mutable governance; this checks the
    immutable package receipt, final verification and their pinned manifest.
    """
    state=json.loads(STATE.read_text())
    if state.get('state')!='FINAL_CANONICAL_NETWORKS_PREPARED' or state.get('semantic_parity',{}).get('status')!='PASS':
        raise RuntimeError('FINAL_PREFLIGHT_VERIFICATION_FAIL')
    controls={r['path']:r['sha256'] for r in state['phases']['F']['created_artifacts']}
    verification=QA/'FINAL_VERIFICATION.json'
    manifest=QA/'FINAL_ARTIFACT_MANIFEST.csv'
    for path in (RECEIPT,verification,manifest):
        expected=controls.get(str(path.relative_to(ROOT)))
        if not expected or not path.is_file() or sha256_file(path)!=expected:
            raise RuntimeError('FINAL_FROZEN_CONTRACT_HASH_FAIL: '+path.name)
    final=json.loads(verification.read_text())
    if final.get('state')!='FINAL_CANONICAL_NETWORKS_PREPARED' or final.get('manifest_sha256')!=sha256_file(manifest):
        raise RuntimeError('FINAL_FROZEN_MANIFEST_FAIL')
    table=pd.read_csv(manifest)
    if table.path.duplicated().any():
        raise RuntimeError('FINAL_FROZEN_MANIFEST_DUPLICATE')
    return table.set_index('path').sha256.to_dict()


def preflight(year,scenario,*,persist=True):
    frozen=verify_frozen_contract()
    item=package(year,scenario)
    with no_models_or_solves():
        for field,hash_field in (('path','sha256'),('reference_path','reference_sha256')):
            source=ROOT/item[field]
            if not source.is_file() or frozen.get(item[field])!=item[hash_field] or sha256_file(source)!=item[hash_field]:
                raise RuntimeError('FINAL_INPUT_HASH_FAIL')
        network=pypsa.Network(ROOT/item['path'])
        structural_check(network,year,scenario)
    result={'status':'PASS','prepared_for_manual_execution':True,'year':year,'scenario':scenario,
        'variant':'final_v1','uc_input_path':item['path'],'uc_input_sha256':item['sha256'],
        'parent_path':item['reference_path'],'parent_sha256':item['reference_sha256'],
        'snapshots':8760,'synthetic_units':item['UC_units'],'optimization_model_constructed':False,
        'production_solver_invocations':0,'manual_authorized':settings()['manual_authorization'][f'{year}_{scenario}']}
    if persist:
        dump_json(ROOT/settings()['result_root']/str(year)/scenario/'QA/PREFLIGHT.json',result)
    return result


def prepared_jobs():
    """Print/table-only registry using the existing single-job UC2 path API."""
    from . import stage_b_uc2 as uc2
    rows=[]
    with uc2.execution_variant('final_v1'):
        for year,scenario in SCENARIOS:
            for kind in uc2.RUN_TYPES:
                paths=uc2.job_paths(year,scenario,kind)
                if paths['solved'].exists() or paths['receipt'].exists():
                    raise RuntimeError('FINAL_PREPARATION_RESULT_ALREADY_EXISTS')
                rows.append({'year':year,'scenario':scenario,'run_type':kind,
                    'job_id':uc2.job_id(year,scenario,kind),
                    'input_path':str(paths['input'].relative_to(ROOT)),
                    'input_sha256':uc2._check_input_hash(year,scenario,kind),
                    'solved_path':str(paths['solved'].relative_to(ROOT)),
                    'receipt_path':str(paths['receipt'].relative_to(ROOT)),
                    'verification_path':str(paths['verification'].relative_to(ROOT)),
                    'technically_prepared':True,'manual_execution_authorized':settings()['manual_authorization'][f'{year}_{scenario}'],
                    'production_executed':False,'automatic_successor':False})
    table=pd.DataFrame(rows)
    if len(table)!=18 or not table.job_id.is_unique or not table.solved_path.is_unique:
        raise RuntimeError('FINAL_MANUAL_JOB_ISOLATION_FAIL')
    table.to_csv(QA/'FINAL_MANUAL_JOB_REGISTRY.csv',index=False)
    return table


def close_final(tests):
    receipt=json.loads(RECEIPT.read_text())
    state=json.loads(STATE.read_text())
    if len(receipt['packages'])!=6 or any(state['phases'][p]['status']!='PASS' for p in 'WHNC'):
        raise RuntimeError('FINAL_CLOSURE_GATE_FAIL')
    if state.get('semantic_parity',{}).get('status')!='PASS':
        raise RuntimeError('FINAL_NATIVE_SEMANTIC_PARITY_NOT_PASS')
    handoff=ROOT/'docs/final_methodology_closure/MEM_FINAL_CANONICAL_NETWORKS_HANDOFF.md'
    if not handoff.is_file():
        raise RuntimeError('FINAL_HANDOFF_MISSING')
    protected=protected_artifact_verification(state)
    artifacts=[CONFIG,Path(__file__),ROOT/'src/mem_model/stage_b_uc2.py',
        ROOT/'src/mem_model/reporting/uc2_postprocess.py',ROOT/'tests/test_final_methodology_networks.py',
        handoff,RECEIPT,QA/'EXACT_PRESERVATION_MATRIX.csv',QA/'FINAL_NETWORK_LINEAGE_AND_HASHES.csv',
        QA/'APPROVED_MODEL_OBJECT_CHANGE_INVENTORY.csv',
        QA/'FINAL_MANUAL_JOB_REGISTRY.csv',
        ROOT/'docs/final_methodology_closure/MEM_FINAL_CANONICAL_NETWORKS_POWERSHELL_RUNBOOK.md',
        ROOT/'src/mem_model/final_methodology_checkpoints.py',ROOT/'src/mem_model/final_network_contract.py',
        ROOT/'tests/test_final_network_contract.py',ROOT/'src/mem_model/reporting/uc2_diagnostics.py',
        ROOT/'src/mem_model/reporting/plots.py',ROOT/'src/mem_model/reporting/ror_water.py',
        ROOT/'tests/test_ror_water_reporting.py',QA/'AFFECTED_NON_SOLVING_TEST_RESULTS.xml']
    artifacts += [ROOT/'config/stage_b_reporting.yaml',ROOT/'tests/test_uc2_diagnostics.py']
    artifacts += [ROOT/'src/mem_model/visualization/uc2_vis_x1.py']
    artifacts += [QA/'FREEZE_INTEGRITY_TEST_RESULTS.xml',
        ROOT/'qa/stage_b/uc2a_heterogeneous_v1/MEM_UC2A_CHILD_CROSSWALK.csv']
    artifacts += [ROOT/a['path'] for a in state['semantic_parity']['artifacts']]
    artifacts += [ROOT/state['phases'][p][key] for p in 'WHNC' for key in ('receipt','manifest') if key in state['phases'][p]]
    artifacts += [ROOT/'qa/final_methodology_closure/wind/HORIZON_SPECIFIC_FINAL_WIND_VERIFICATION.json']
    for p in receipt['packages']:
        for path_field,hash_field in (('path','sha256'),('reference_path','reference_sha256')):
            path=ROOT/p[path_field]
            if sha256_file(path)!=p[hash_field]:
                raise RuntimeError('FINAL_CLOSE_NETWORK_HASH_FAIL')
            artifacts.append(path)
    manifest=QA/'FINAL_ARTIFACT_MANIFEST.csv'
    pd.DataFrame([{'path':str(p.relative_to(ROOT)),'bytes':p.stat().st_size,'sha256':sha256_file(p)}
                  for p in sorted(set(artifacts))]).to_csv(manifest,index=False)
    verification={'state':'FINAL_CANONICAL_NETWORKS_PREPARED','production_state':'PRODUCTION_NOT_EXECUTED',
        'phase_results':{p:state['phases'][p]['phase_result'] for p in 'WHNC'},'tests':tests,
        'semantic_parity':state['semantic_parity']['phase_result'],
        'packages':receipt['packages'],'manifest':str(manifest.relative_to(ROOT)),
        'manifest_sha256':sha256_file(manifest),'technical_preparation_only':True,
        'prepared_manual_jobs':18,'production_results_created':0,
        'manual_execution_authorized':False,'optimization_model_constructed':False,
        'production_optimization_executed':False,'production_solver_invocations':0,
        'historical_and_upstream_inputs_unchanged':True,'water_value_coverage_hook_ready':True,
        'RoR_water_bypass_generation_reporting_ready':True,'protected_artifact_verification':protected}
    final=QA/'FINAL_VERIFICATION.json'; dump_json(final,verification)
    state=record_phase('F','PASS',parents=state['phases']['N']['successor_parents'],
        artifacts=[*artifacts,manifest,final],decisions=['FINAL_SIX_UNSOLVED_FAMILY_WITH_SAME_SYSTEM_CONTINUOUS_COUNTERFACTUALS',
        'EXISTING_UC2_PATH_FINAL_V1_VARIANT_NO_SOLVER_OR_MODEL_CALL','FUTURE_NATIVE_WATER_DUAL_COMPLETENESS_GATE',
        'FINAL_METHOD_REVIEW_REQUIRED_BEFORE_MANUAL_EXECUTION'],tests=tests,unresolved=[])
    state['phases']['F'].update({'phase_result':'FINAL_CANONICAL_NETWORKS_PREPARED',
        'successor_parents':[{k:p[k] for k in ('year','scenario','path','sha256')} for p in receipt['packages']],
        'manifest':str(manifest.relative_to(ROOT)),'receipt':str(final.relative_to(ROOT)),
        'current_handoff':str(handoff.relative_to(ROOT))})
    state['optimization_model_constructed']=False; dump_json(STATE,state)
    return verification

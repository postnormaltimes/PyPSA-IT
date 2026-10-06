"""Bounded installed-source and structural parity audit; never creates a model."""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa

from .common import ROOT, ZONES, dump_json, safe_id, sha256_file
from .final_methodology_closure import STATE
from .stage_b_zonal_vre import no_models_or_solves

QA=ROOT/'qa/final_methodology_closure/semantic_parity'
CLASSIFICATIONS={'MATCH_NATIVE_SEMANTICS','INTENTIONAL_MEM_DEVIATION','PREVIOUS_BUG_FIXED','UNRESOLVED'}


def source_receipt():
    from pypsa.optimization import constraints,global_constraints,optimize,variables
    from . import stage_b_uc2 as uc2
    if pypsa.__version__!='1.2.3':
        raise RuntimeError('PARITY_INSTALLED_PYPSA_VERSION_FAIL')
    sources={m.__name__:Path(inspect.getsourcefile(m)) for m in (constraints,global_constraints,optimize,variables)}
    code=inspect.getsource(constraints.define_store_constraints)
    if not all(token in code for token in ('snapshot_weightings.stores','(-eh, p)','roll(snapshot=1)','e_cyclic')):
        raise RuntimeError('PARITY_NATIVE_STORE_SOURCE_FAIL')
    bounds=inspect.getsource(constraints.define_operational_constraints_for_committables)
    if not all(token in bounds for token in ('get_bounds_pu','start_up','shut_down','min_up_time','min_down_time')):
        raise RuntimeError('PARITY_NATIVE_UC_SOURCE_FAIL')
    sign=inspect.getsource(constraints.define_nodal_balance_constraints)
    if 'sign = sign * c.da.sign' not in sign:
        raise RuntimeError('PARITY_NATIVE_WITHDRAWAL_SIGN_FAIL')
    operational=inspect.getsource(global_constraints.define_operational_limit)
    if 'weightings_filtered.generators' not in operational or 'carrier == @glc.carrier_attribute' not in operational:
        raise RuntimeError('PARITY_P2X_NATIVE_ANNUAL_LIMIT_FAIL')
    duals=inspect.getsource(optimize.OptimizationAccessor.assign_duals)
    processing=inspect.getsource(optimize.OptimizationAccessor.post_processing)
    if 'assign_all_duals' not in duals or 'divide(weightings, axis=0)' not in processing:
        raise RuntimeError('PARITY_NATIVE_DUAL_ASSIGNMENT_FAIL')
    path=ROOT/'src/mem_model/stage_b_uc2.py'
    future=inspect.getsource(uc2._manual_solve)
    if not all(t in future for t in ('linearized_unit_commitment=kind == "FIXED_COMMITMENT_PRICE_LP"',
        'for attribute in ("status", "start_up", "shut_down")','assign_all_duals=kind != "UC_MILP"',
        'capture_store_energy_balance_coverage','if kind != "UC_MILP" and (binaries or integers)')):
        raise RuntimeError('PARITY_FUTURE_FIXED_PRICE_PATH_FAIL')
    # Exact local checkout already pinned by W/H; obtain its path from accepted
    # H recovery rather than discovering another checkout or version.
    recovery=json.loads((ROOT/'qa/final_methodology_closure/hydro/HYDRO_SPATIAL_SOURCE_RECOVERY.json').read_text())
    local_sources=[]
    for row in recovery['sources']:
        if row.get('base')=='PYPSA_EUR':
            source=Path(recovery['path_bases']['PYPSA_EUR'])/row['sourcepath']
            if source.is_file():
                if sha256_file(source)!=row['sha256']:
                    raise RuntimeError('PARITY_LOCAL_PYPSA_EUR_SOURCE_DRIFT')
                local_sources.append({'path':str(source),'sha256':row['sha256'],'authority':'ACCEPTED_H_LOCAL_SOURCE'})
    if not local_sources:
        raise RuntimeError('PARITY_LOCAL_METHOD_SOURCE_RECEIPT_MISSING')
    return {'pypsa_version':pypsa.__version__,'native_sources':[
        {'module':name,'path':str(p),'sha256':sha256_file(p)} for name,p in sources.items()],
        'local_PyPSA_Eur_sources':local_sources,'MEM_manual_execution_source':{'path':str(path.relative_to(ROOT)),'sha256':sha256_file(path)},
        'API_checked_without_model_construction':True,'future_fixed_price_path_inspected_not_executed':True,
        'native_Store_balance':'e_t=(1-standing_loss)^w*e_previous-w*Store_p',
        'Bus_dual_scaling':'post_processing divides Bus marginal_price by objective weights',
        'Store_dual_scaling':'assign_duals copies raw Store-energy_balance dual; no Bus weight division',
        'optimization_model_constructed':False,'production_solver_invocations':0}


def matrix():
    rows=[]
    def add(obj,feature,native,implementation,classification,rationale,impact='MODEL_CRITICAL'):
        rows.append({'object':obj,'feature':feature,'native_source':native,'MEM_implementation':implementation,
            'classification':classification,'rationale':rationale,'model_impact':impact,
            'action_required':'NONE; future solved-result gates remain mandatory','status':'PASS_STATIC_API_STRUCTURAL_OR_PREPARED_PATH'})
    bounds='installed PyPSA1.2.3 constraints.py:44-130/205-596; data/component_attrs'
    store='installed PyPSA1.2.3 constraints.py:1790-1963'
    balance='installed PyPSA1.2.3 constraints.py:985-1177'
    local='local PyPSA-Eur add_electricity.py:499-571/795-810; accepted W/H receipts'
    add('VRE','shared national trace replaced by differentiated zonal geography',local,'stage_b_zonal_vre.py + W receipts','PREVIOUS_BUG_FIXED','Stage A national weather aggregation is not inherited as seven identical Stage-B shapes.')
    add('VRE','fixed zonal MW/native-cell site LCOE',local,'final_wind_cells.py; final_wind_horizon_cost.py','INTENTIONAL_MEM_DEVIATION','Native spatial profiles and potential/economics reused for exogenous fixed zonal future MW; not endogenous capacity investment.')
    add('VRE','p_max_pu bounds and correction exactly once',bounds,'W 84 physical cases and effective-profile lineage','MATCH_NATIVE_SEMANTICS','Accepted turbine conversion/eligibility retained; no post-siting annual/profile rescaling.')
    add('VRE','horizon-specific offshore cost; no cost leakage',local,'direct v0.14.0 2040/2050 W authority; N exact non-Link preservation','INTENTIONAL_MEM_DEVIATION','Site CAPEX/FOM rank within-zone only; fixed MW and dispatch marginal costs unchanged.')
    add('ROR','fixed NET turbine saturation; separate raw water and bypass',local,'final_hydro_spatial.ror_turbine_mapping; ROR_ACCESSIBLE_AND_BYPASS_2019.parquet','PREVIOUS_BUG_FIXED','Native cap<=1 limits turbine throughput; full annual natural MWh-water retained separately. No annual generation forcing.')
    add('ROR','no Store/no pump/efficiency once',balance,'H exported RoR comparison','MATCH_NATIVE_SEMANTICS','Electrical potential is accessible water times accepted eta; bypass is neither electrical curtailment nor reservoir spill.')
    add('natural hydro','market-zone runoff adapter',local,'final_hydro_spatial.py; explicit H zonal decision','INTENTIONAL_MEM_DEVIATION','Accepted polygons are seven-zone aggregate modelling geography, not catchments; no EIA annual normalization.')
    add('basin/reservoir/mixed PHS','forced inflow and water-bus generator signs',balance,'network.py:water buses; H exact forced p_min_pu=p_max_pu','MATCH_NATIVE_SEMANTICS','Positive exogenous water injection is mandatory; negative spill withdrawal and turbine input remove water.')
    add('basin/reservoir/mixed PHS','water/electrical units and Link efficiencies',store+'; '+balance,'H water conservation + NET rating/efficiency checks','MATCH_NATIVE_SEMANTICS','Store-p positive withdrawal; electric=-turbine_p1=eta*turbine_p0, pump injection=eta*pump_p0; weighted once.')
    add('hydro/PHS Store','cyclic last-state predecessor',store,'H cyclic API receipt and dimensional tests','MATCH_NATIVE_SEMANTICS','Native roll(snapshot=1) makes last output predecessor of first; first output need not equal last; no invented initial level.')
    add('mixed PHS','one shared water state',store,'H state census, exact Store and pump/turbine crosswalk','MATCH_NATIVE_SEMANTICS','Natural inflow and pumping feed the same accepted state; no duplicate reservoirs.')
    add('pure PHS','no natural inflow; MW/MWh/pump/turbine preserved',balance+'; '+store,'H census and exact N non-Link protection','MATCH_NATIVE_SEMANTICS','No INFLOW component; accepted 7252.3/6400/53000 controls/Method-C unchanged.')
    add('spill','nonbinding forced-inflow safety cap',bounds,'H SPILL_SAFETY_CAP_CORRECTIONS.csv','PREVIOUS_BUG_FIXED','Electrical MW-derived bound was insufficient for water peaks; replace proven rows with peak natural water+ULP, no pump allowance.')
    add('BESS','Store + charge/discharge Link/state/ratings',balance+'; '+store,'accepted network.py BESS; H/N exact preservation','MATCH_NATIVE_SEMANTICS','Accepted signs, efficiencies, state energy/power, cyclic bounds unchanged; no expansion.')
    add('transmission','signed asymmetric Link bounds and zero-cost/lossless net interval',bounds,'final_network_signed.py; N export-reload QA','PREVIOUS_BUG_FIXED','Two nonnegative reciprocal variables project exactly onto one signed interval; redundant gross counterflow removed, no topology/MW change.')
    add('transmission','NET_FLOW_A_TO_B_MW/utilization/congestion/topology',balance,'final_network_signed; canonical_results; topology; uc2_diagnostics','MATCH_NATIVE_SEMANTICS','Native signed p0 used once; legacy p0_AB-p0_BA; p1=-p0 is not another corridor flow.')
    add('Generator','installed ratings, sign, availability and cost bounds',bounds+'; '+balance,'H/N current component static and dynamic audits','MATCH_NATIVE_SEMANTICS','Fixed p_nom; thermal status applies to bounds; VRE/ROR availability separate; inflow normalization p_nom is not installed electric capacity.')
    add('P2X','negative-sign noncommittable withdrawal and exact annual energy','installed global_constraints.py:483-657; '+balance,'accepted P2X native operational_limit + seven Generators','MATCH_NATIVE_SEMANTICS','Native dispatch remains nonnegative; Generator.sign=-1 affects nodal withdrawal, carrier operational limit weights electrical consumption once.')
    add('UC','status/start/shutdown/min times/ramps/cost per actual child',bounds,'stage_b_uc1; calibrated v2A crosswalk; N immutable fleet','MATCH_NATIVE_SEMANTICS','Native fixed-unit UC; capacity conserved within parent; no custom nuclear dwell, native boundary treatment.')
    add('thermal v2A','controlled eta/size/MC and startup scaling',bounds,'accepted CONTROLLED_ETA_15_50_35_V2 crosswalk','INTENTIONAL_MEM_DEVIATION','Synthetic operating cohorts are project representation assumptions; native parameter interpretation unchanged; no recalibration.')
    add('UC events','raw shutdown flags versus status transitions',bounds,'stage_b_uc2.canonical_transitions','MATCH_NATIVE_SEMANTICS','Raw OCGT flags can be degenerate with zero down-time/shutdown cost; physical event counts use status changes and native initial status.')
    add('fixed price LP','linearized UC; all three raw trajectories fixed','installed optimize.py:create_model; variables.py; '+bounds,'stage_b_uc2._manual_solve; verify_price','MATCH_NATIVE_SEMANTICS','Future model must have zero integer/binaries; dispatch/P2X/storage/flows free; source same-case verified MILP; objective gate retained. Not executed in this audit.')
    add('zonal prices','LP bus energy-balance dual / objective weighting','installed optimize.py:assign_duals/post_processing','stage_b_uc2.verify_price; canonical_results prices','MATCH_NATIVE_SEMANTICS','Only fixed-commitment LP Bus marginal_price is economic authority; MILP physical results only.')
    add('water values','raw Store dual coverage and sign/scale','installed optimize.py:1011-1129; '+store,'reporting/water_values.py; final price capture/verify hook','PREVIOUS_BUG_FIXED','Explicit assign_all_duals=true and active raw coverage required; never divide Store dual by objective weights or use filled zeros as coverage evidence.')
    add('snapshot weights','objective/generator/Store accounting','installed optimize.py:246-280; global_constraints.py:483-657; '+store,'annual statistics + P2X constraints + water accounting','MATCH_NATIVE_SEMANTICS','All accepted hourly weights are1; costs use objective, generation/P2X use generators, storage balance uses stores; no duplicate time aggregation.')
    add('costs','fuel+CO2+VOM once; siting CAPEX excluded','installed optimize.py:140-359; native dispatch marginal_cost','stage_b_uc2a.heat_rate_cost; preserved parent/child MC','MATCH_NATIVE_SEMANTICS','Frozen MEM cost coefficient and other costs retained; no independent MC buckets, transport MC0, no exogenous water-value charge.')
    add('capacity','no hidden extendability','installed PyPSA component schemas/operational bounds','N/H static comparison + all component extendability audit','MATCH_NATIVE_SEMANTICS','No p_nom/e_nom/s_nom expansion or modular endogenous capacity. UC binaries express operation only.')
    return pd.DataFrame(rows)


def validate_matrix(table):
    if not set(table.classification).issubset(CLASSIFICATIONS) or table.empty:
        raise RuntimeError('PARITY_MATRIX_SCHEMA_FAIL')
    if table.classification.eq('UNRESOLVED').any() or table.status.astype(str).str.startswith('FAIL').any():
        raise RuntimeError('NATIVE_SEMANTIC_UNRESOLVED_BLOCKS_F')


def profile_bounds(values):
    """Accept arithmetic roundoff only; never clip or rewrite accepted inputs."""
    values=np.asarray(values,dtype=float)
    if not np.isfinite(values).all() or (values < -1e-12).any() or (values > 1+1e-12).any():
        raise RuntimeError('PARITY_VRE_ROR_PHYSICAL_BOUNDS_FAIL')
    return int(((values<0)|(values>1)).sum())


def audit_network(network,year,scenario):
    from .final_network_contract import audit_signed_network
    from .final_network_signed import directional_capacity_table
    weights=network.snapshot_weightings
    if len(network.snapshots)!=8760 or not weights[['objective','generators','stores']].eq(1).all().all():
        raise RuntimeError('PARITY_SNAPSHOT_WEIGHT_OR_LENGTH_FAIL')
    for component in network.components:
        for field in ('p_nom_extendable','e_nom_extendable','s_nom_extendable'):
            if field in component.static and component.static[field].astype(bool).any():
                raise RuntimeError('PARITY_UNINTENDED_CAPACITY_EXPANSION')
    generators=network.generators
    vre=generators.index[generators.carrier.isin(['wind_onshore','wind_offshore','solar_pv_rooftop','solar_pv_utility'])]
    ror=generators.index[generators.carrier.eq('hydro_run_of_river')]
    pmax=network.get_switchable_as_dense('Generator','p_max_pu')
    arithmetic_roundoff_values=0
    for ids in (vre,ror):
        values=pmax[ids].to_numpy()
        arithmetic_roundoff_values+=profile_bounds(values)
    committable=generators.index[generators.committable]
    values=pmax[committable]
    if (values.gt(0)&values.lt(generators.loc[committable,'p_min_pu'],axis=1)).any().any():
        raise RuntimeError('PARITY_UC_AVAILABILITY_MINIMUM_FAIL')
    fleet=pd.read_csv(ROOT/'qa/stage_b/uc2a_heterogeneous_v1/MEM_UC2A_CHILD_CROSSWALK.csv')
    expected=fleet.loc[fleet.year.eq(year)&fleet.scenario.eq(scenario),'child_id']
    if set(committable)!=set(expected):
        raise RuntimeError('PARITY_COMM_SCOPE_FAIL')
    inflow=generators.index[generators.index.str.startswith('INFLOW_')]
    pmin=network.get_switchable_as_dense('Generator','p_min_pu')
    np.testing.assert_array_equal(pmin[inflow],pmax[inflow])
    if not generators.loc[inflow,'sign'].eq(1).all():
        raise RuntimeError('PARITY_FORCED_INFLOW_SIGN_FAIL')
    spill=generators.index[generators.index.str.startswith('SPILL_')]
    if not generators.loc[spill,'sign'].eq(-1).all() or not pmin[spill].eq(0).all().all():
        raise RuntimeError('PARITY_WATER_SPILL_SIGN_FAIL')
    natural_census=pd.read_csv(ROOT/'qa/final_methodology_closure/hydro/HYDRO_CURRENT_LINEAGE_AND_STATE_CENSUS.csv')
    census=natural_census.loc[natural_census.year.eq(year)&natural_census.scenario.eq(scenario)&natural_census.hydro_class.ne('RUN_OF_RIVER')]
    for row in census.itertuples():
        sid=safe_id(row.runtime_id)
        store=network.stores.loc['STORE_'+sid]
        turbine=network.links.loc['TURBINE_'+sid]
        if store.bus!=turbine.bus0 or turbine.bus1!=row.zone or abs(store.e_nom-row.store_energy_MWh)>1e-6 or not store.e_cyclic:
            raise RuntimeError('PARITY_HYDRO_STATE_ORIENTATION_FAIL')
        if abs(turbine.p_nom*turbine.efficiency-row.turbine_MW_NET)>1e-6 or abs(turbine.efficiency-row.turbine_efficiency)>1e-12:
            raise RuntimeError('PARITY_HYDRO_NET_EFFICIENCY_FAIL')
        if row.hydro_class=='PURE_PHS' and 'INFLOW_'+sid in generators.index:
            raise RuntimeError('PARITY_PURE_PHS_NATURAL_WATER_FAIL')
        if row.pump_MW>0:
            pump=network.links.loc['PUMP_'+sid]
            if pump.bus0!=row.zone or pump.bus1!=store.bus or abs(pump.p_nom-row.pump_MW)>1e-6 or abs(pump.efficiency-row.pump_efficiency)>1e-12:
                raise RuntimeError('PARITY_PHS_PUMP_ORIENTATION_FAIL')
        if turbine.marginal_cost!=0 or store.marginal_cost!=0:
            raise RuntimeError('PARITY_EXOGENOUS_WATER_VALUE_COST_FAIL')
    p2x=generators.loc[[f'P2X_{z}' for z in ZONES]]
    if not p2x.sign.eq(-1).all() or p2x.committable.any():
        raise RuntimeError('PARITY_P2X_SIGN_UC_FAIL')
    bess=network.stores.loc[network.stores.carrier.eq('battery_energy')]
    for name,row in bess.iterrows():
        charge=network.links.loc[network.links.bus1.eq(row.bus)&network.links.index.str.startswith('BESS_CHARGE_')]
        discharge=network.links.loc[network.links.bus0.eq(row.bus)&network.links.index.str.startswith('BESS_DISCHARGE_')]
        if len(charge)!=1 or len(discharge)!=1 or not row.e_cyclic or row.e_nom<0:
            raise RuntimeError('PARITY_BESS_STATE_ORIENTATION_FAIL')
        if charge.iloc[0].bus0!=discharge.iloc[0].bus1 or charge.iloc[0].bus0 not in ZONES:
            raise RuntimeError('PARITY_BESS_ELECTRICAL_ENDPOINT_FAIL')
        for link in (charge.iloc[0],discharge.iloc[0]):
            if link.p_min_pu!=0 or not 0<link.efficiency<=1 or link.p_nom<0 or link.p_nom_extendable:
                raise RuntimeError('PARITY_BESS_EFFICIENCY_BOUND_FAIL')
        if row.e_nom==0 and (charge.iloc[0].p_nom!=0 or discharge.iloc[0].p_nom!=0):
            raise RuntimeError('PARITY_INACTIVE_BESS_POWER_FAIL')
    authority=pd.read_csv(ROOT/'qa/final_methodology_closure/network_contract/DIRECTIONAL_AUTHORITY_STATIC_RECONCILIATION.csv')
    directional=audit_signed_network(network,year,scenario,authority)
    return {'year':year,'scenario':scenario,'status':'PASS','snapshots':8760,
        'VRE_profiles':len(vre),'ROR_profiles':len(ror),'forced_inflow_generators':len(inflow),
        'water_states':len(census),'UC_units':len(committable),'directional_rows':len(directional),
        'hidden_extendable_assets':0,'directional_table_rows':len(directional_capacity_table(network)),
        'accepted_inactive_zero_capacity_BESS_rows':int(bess.e_nom.eq(0).sum()),
        'arithmetic_roundoff_boundary_values':arithmetic_roundoff_values,
        'profile_bound_roundoff_tolerance':1e-12,'profiles_modified':False}


def run_audit(tests):
    state=json.loads(STATE.read_text())
    if state['next_phase']!='F' or any(state['phases'][p]['status']!='PASS' for p in 'WHNC'):
        raise RuntimeError('PARITY_REQUIRES_W_H_N_C_PASS')
    QA.mkdir(parents=True,exist_ok=True)
    source=source_receipt(); table=matrix(); validate_matrix(table)
    checks=[]
    with no_models_or_solves():
        for p in state['phases']['N']['successor_parents']:
            path=ROOT/p['path']
            if sha256_file(path)!=p['sha256']:
                raise RuntimeError('PARITY_PARENT_HASH_FAIL')
            checks.append(audit_network(pypsa.Network(path),p['year'],p['scenario']))
    table.to_csv(QA/'NATIVE_SEMANTIC_PARITY_MATRIX.csv',index=False)
    dump_json(QA/'NATIVE_SEMANTIC_SOURCE_RECEIPT.json',source)
    transfer=QA/'MEM_FINAL_METHODOLOGY_NATIVE_SEMANTIC_PARITY_TRANSFER.md'
    if not transfer.is_file():
        raise RuntimeError('PARITY_TRANSFER_MISSING')
    receipt={'state':'NATIVE_PYPSA_SEMANTIC_PARITY_AUDIT_PASS','status':'PASS',
        'matrix_rows':len(table),'classification_counts':table.classification.value_counts().to_dict(),
        'model_critical_UNRESOLVED':0,'packages':checks,'tests':tests,
        'scope':'INSTALLED_API_SOURCE_AND_UNSOLVED_STRUCTURAL_PARITY; FUTURE_SOLVED_GATES_PREPARED_NOT_EXECUTED',
        'W_H_artifacts_unchanged':True,'optimization_model_constructed':False,
        'production_optimization_executed':False,'production_solver_invocations':0}
    verification=QA/'NATIVE_SEMANTIC_PARITY_VERIFICATION.json'; dump_json(verification,receipt)
    paths=[QA/'NATIVE_SEMANTIC_PARITY_MATRIX.csv',QA/'NATIVE_SEMANTIC_SOURCE_RECEIPT.json',verification,transfer,
        Path(__file__),ROOT/'tests/test_final_semantic_parity.py']
    state['semantic_parity']={'status':'PASS','phase_result':receipt['state'],
        'artifacts':[{'path':str(p.relative_to(ROOT)),'sha256':sha256_file(p)} for p in paths],
        'tests':tests,'model_critical_UNRESOLVED':0,'production_solver_invocations':0}
    state['optimization_model_constructed']=False; dump_json(STATE,state)
    return receipt

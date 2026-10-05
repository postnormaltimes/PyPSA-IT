"""Review-only lexicographic regimes over unchanged complete V2 roots.

The explicit requested hierarchy is an interpretation, not a new KKT method.
No networks, candidate discovery, tests, rankings or selected asset winners.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from ..common import sha256_file
from .marginal_setter_attribution import ZONES, TOLERANCE
from .marginal_setter_resolver import IDENTITY, map_candidates

VERSION='MEM_UC3_LEXICOGRAPHIC_MARGINAL_REGIME_V1'
FOLDER='LEXICOGRAPHIC_REGIME_V1'
ORDER=('VRE','Storage','Thermal','Flexible demand','External market','Market coupling / mixed')
CROSSWALK={'VRE':'VRE','BESS':'Storage','PHS':'Storage',
    'Reservoir / pondage hydro':'Storage','CCGT':'Thermal','Other thermal':'Thermal',
    'P2X':'Flexible demand','External market':'External market'}
RULES=dict(version=VERSION,root_authority='Complete unchanged V2-selected local roots, otherwise validated coupled roots',
    grouping=CROSSWALK,external='External-only retained; external roots removed from decision if any domestic root exists, but preserved in evidence',
    thermal='At least one domestic thermal root with cost > inherited tolerance and positive reconstructed target price; all domestic root reconstructions agree with target price. Ramp/bound context retained.',
    VRE='VRE-only retained; mixed VRE/storage/demand requires approximately zero target price and zero non-VRE opportunity/shadow contributions, with all root reconstructions consistent',
    storage_contribution='charging: efficiency * native Store opportunity value; discharging: native Store opportunity value / efficiency',
    P2X_contribution='native annual shadow * accepted represented_hours / objective_weight; require native shadow approximately zero as well',
    tolerance_EUR_MWh=TOLERANCE,remaining='Pure intertemporal roots -> Storage; P2X-only -> Flexible demand; other unresolved domestic combinations -> Market coupling / mixed',
    interpretation='User-requested economic-anchor hierarchy; preserves genuine co-marginal evidence. Residual may be LOCAL or COUPLED; not necessarily network-caused.',
    prohibited='No interior filtering, utilisation/merit ranking, asset/hash winner, size/shortest-path rule, or inferred legal priority',
    weight='Represented time',activation='REVIEW_ONLY_NOT_ROUTINE')

def resolve(table,audit):
    mapped=map_candidates(audit)
    mapped['regime_root_family']=mapped.root_family.map(CROSSWALK)
    if mapped.regime_root_family.isna().any():raise RuntimeError('UNMAPPED_REGIME_ROOT')
    evidence=mapped.merge(table[['snapshot','zone','represented_hours','zonal_price_EUR_MWh']].rename(columns={'zone':'target_zone'}),on=['snapshot','target_zone'],validate='many_to_one')
    evidence['price_consistent_evidence']=evidence.recon.sub(evidence.zonal_price_EUR_MWh).abs().le(TOLERANCE)&evidence.residual.abs().le(TOLERANCE)
    evidence['opportunity_shadow_contribution_EUR_MWh']=np.nan
    storage=evidence.regime_root_family.eq('Storage')
    evidence.loc[storage,'opportunity_shadow_contribution_EUR_MWh']=np.where(evidence.loc[storage,'position'].eq('charging'),
        evidence.loc[storage,'opportunity']*evidence.loc[storage,'efficiency'],
        evidence.loc[storage,'opportunity']/evidence.loc[storage,'efficiency'])
    demand=evidence.regime_root_family.eq('Flexible demand')
    evidence.loc[demand,'opportunity_shadow_contribution_EUR_MWh']=evidence.loc[demand,'opportunity']*evidence.loc[demand,'represented_hours']/evidence.loc[demand,'objective_weight']
    evidence['zero_opportunity_shadow_evidence']=evidence.opportunity_shadow_contribution_EUR_MWh.abs().le(TOLERANCE)
    evidence.loc[demand,'zero_opportunity_shadow_evidence'] &= evidence.loc[demand,'opportunity'].abs().le(TOLERANCE)
    evidence['thermal_positive_cost_anchor']=evidence.regime_root_family.eq('Thermal')&evidence.cost.gt(TOLERANCE)&evidence.recon.gt(TOLERANCE)&evidence.price_consistent_evidence
    evidence['excluded_external_from_decision']=evidence.regime_root_family.eq('External market')&~evidence.groupby(['snapshot','target_zone']).regime_root_family.transform(lambda x:x.eq('External market').all())
    unique=evidence.drop_duplicates(['snapshot','target_zone',*IDENTITY])
    records=[]
    for (snapshot,zone),g in unique.groupby(['snapshot','target_zone'],sort=False):
        families=set(g.regime_root_family)
        domestic=g.loc[~g.excluded_external_from_decision]
        remaining=set(domestic.regime_root_family)
        consistent=bool(domestic.price_consistent_evidence.all())
        thermal_anchor=bool(domestic.thermal_positive_cost_anchor.any())
        zero_price=bool(abs(float(g.zonal_price_EUR_MWh.iloc[0]))<=TOLERANCE)
        nonvre=domestic.loc[domestic.regime_root_family.ne('VRE')]
        zero_shadow=bool(nonvre.zero_opportunity_shadow_evidence.all()) if len(nonvre) else True
        if remaining=={'External market'}:category,rule,reason='External market','EXTERNAL_ONLY',None
        elif 'Thermal' in remaining:
            if thermal_anchor and consistent:category,rule,reason='Thermal','THERMAL_ONLY' if remaining=={'Thermal'} else 'THERMAL_COST_ANCHOR',None
            else:category,rule,reason='Market coupling / mixed','MARKET_COUPLING_MIXED','THERMAL_POSITIVE_COST_ANCHOR_NOT_SUPPORTED'
        elif remaining=={'VRE'}:category,rule,reason='VRE','VRE_ONLY',None
        elif 'VRE' in remaining:
            if remaining.issubset({'VRE','Storage','Flexible demand'}) and zero_price and zero_shadow and consistent:
                category,rule,reason='VRE','VRE_ZERO_PRICE_ANCHOR',None
            else:category,rule,reason='Market coupling / mixed','MARKET_COUPLING_MIXED','VRE_MIXED_NONZERO_PRICE_OR_OPPORTUNITY_SHADOW'
        elif remaining=={'Storage'}:category,rule,reason='Storage','STORAGE_ONLY',None
        elif remaining=={'Flexible demand'}:category,rule,reason='Flexible demand','FLEXIBLE_DEMAND_ONLY',None
        elif remaining:category,rule,reason='Market coupling / mixed','MARKET_COUPLING_MIXED','STORAGE_AND_FLEXIBLE_DEMAND_WITHOUT_PRIMITIVE_ANCHOR'
        else:raise RuntimeError('UNCOVERED_EMPTY_VALID_ROOT_SET')
        scope='COUPLED' if bool(g.remote.iloc[0]) else 'LOCAL'
        records.append(dict(snapshot=snapshot,zone=zone,final_regime_category=category,applied_rule=rule,
            valid_root_count=len(g),valid_regime_families=json.dumps(sorted(families,key=ORDER.index)),
            valid_detailed_root_families=json.dumps(sorted(set(g.root_family))),
            domestic_decision_families=json.dumps(sorted(remaining,key=ORDER.index)),resolution_scope=scope,
            external_root_count=int(g.regime_root_family.eq('External market').sum()),
            external_removed_from_decision=bool(g.excluded_external_from_decision.any()),
            positive_thermal_anchor_supported=thermal_anchor,all_domestic_price_conditions_consistent=consistent,
            approximately_zero_target_price=zero_price,all_non_VRE_zero_opportunity_shadow=zero_shadow,
            residual_reason=reason))
    fields=pd.DataFrame(records)
    if set(fields).intersection(table)-{'snapshot','zone'}:raise RuntimeError('ORIGINAL_V2_FIELD_COLLISION')
    result=table.merge(fields,on=['snapshot','zone'],validate='one_to_one',sort=False)
    if len(result)!=61320 or result.duplicated(['snapshot','zone']).any() or set(result.zone)!=set(ZONES):raise RuntimeError('ZONE_HOUR_INTEGRITY')
    if not result[table.columns].equals(table):raise RuntimeError('V2_VALUE_MUTATION')
    result['original_primary_attribution_class']=result.primary_attribution_class
    evidence=evidence.merge(result[['snapshot','zone','final_regime_category','applied_rule']].rename(columns={'zone':'target_zone'}),on=['snapshot','target_zone'],validate='many_to_one')
    if not evidence[audit.columns].equals(audit):raise RuntimeError('ORIGINAL_ROOT_EVIDENCE_MUTATION')
    return result,evidence

def zone_shares(table):
    x=table.groupby(['zone','final_regime_category']).represented_hours.sum().unstack(fill_value=0).reindex(index=ZONES,columns=ORDER,fill_value=0)
    return x.div(table.groupby('zone').represented_hours.sum(),axis=0).reindex(index=ZONES,columns=ORDER)*100

def run_case(year,scenario,root,output):
    from .uc2_postprocess import no_solver_calls
    root=Path(root).resolve();output=Path(output).resolve()
    if output.parent!=root/FOLDER:raise RuntimeError('PROTECTED_OUTPUT_TARGET')
    label=f'{year}_{scenario}';source=root/'V2'/scenario
    tp=source/f'marginal_setter_attribution_{label}.parquet';ap=source/f'marginal_setter_candidates_{label}.parquet';qp=source/f'marginal_setter_QA_{label}.json'
    qa=json.loads(qp.read_text(encoding='utf-8'))
    for p in [tp,ap]:
        if sha256_file(p)!=qa['artifact_hashes'][p.name]:raise RuntimeError('V2_SOURCE_HASH_CONFLICT')
    output.mkdir(parents=True,exist_ok=True)
    with no_solver_calls() as guard:
        table=pd.read_parquet(tp);audit=pd.read_parquet(ap)
        if not table.model_year.eq(year).all() or not table.scenario.eq(scenario).all():raise RuntimeError('SOURCE_SCENARIO_CONFLICT')
        result,evidence=resolve(table,audit);shares=zone_shares(result)
        if (shares.sum(axis=1)-100).abs().max()>1e-10:raise RuntimeError('SHARE_SUM_INTEGRITY')
        result.to_parquet(output/f'lexicographic_regime_attribution_{label}.parquet',index=False)
        result.to_csv(output/f'lexicographic_regime_attribution_{label}.csv',index=False)
        evidence.to_parquet(output/f'lexicographic_regime_complete_root_evidence_{label}.parquet',index=False)
        shares.to_csv(output/f'lexicographic_regime_zonal_shares_{label}.csv',index_label='zone')
        remaining=result.loc[result.final_regime_category.eq('Market coupling / mixed')].groupby(['resolution_scope','primary_attribution_class','valid_detailed_root_families','valid_regime_families','domestic_decision_families','residual_reason']).represented_hours.sum().rename('zone_hours').reset_index()
        remaining['share_of_all_zone_hours_percent']=remaining.zone_hours/61320*100
        remaining.to_csv(output/f'lexicographic_regime_residual_{label}.csv',index=False)
        report=dict(status='PASS_MINIMAL_REVIEW_INTEGRITY',version=VERSION,scenario=scenario,model_year=year,rules=RULES,rows=len(result),
            category_shares_percent=(result.groupby('final_regime_category').represented_hours.sum().reindex(ORDER,fill_value=0)/result.represented_hours.sum()*100).to_dict(),
            applied_rule_zone_hours=result.applied_rule.value_counts().to_dict(),share_sum_max_error_percent=float((shares.sum(axis=1)-100).abs().max()),
            original_V2_fields_and_root_evidence_identical=True,solver_invocations=guard['solver_invocations'],network_opens=0,tests_added=0,tests_run=0,routine_activation=False,
            source_hashes={str(p):sha256_file(p) for p in [tp,ap,qp]},implementation_sha256=sha256_file(Path(__file__)),
            artifact_hashes={p.name:sha256_file(p) for p in output.iterdir() if p.suffix in ('.csv','.parquet')})
        (output/f'lexicographic_regime_QA_{label}.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report

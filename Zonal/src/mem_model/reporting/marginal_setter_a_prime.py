"""Review-only A-prime classification of complete immutable V2 marginal roots.

No winner asset, synthetic ranking, price rule, network access or root discovery.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from ..common import sha256_file
from .marginal_setter_attribution import ZONES, TOLERANCE, POWER_TOLERANCE, validate_structure
from .marginal_setter_resolver import IDENTITY, map_candidates, ORDER as SOURCE_ORDER

VERSION = 'MEM_UC3_MARGINAL_SETTER_A_PRIME_V1'
ORDER = ('VRE','PHS','BESS','Storage','Reservoir / pondage hydro','P2X','CCGT',
         'Other thermal','External market','Mixed / co-marginal')
RULE_ORDER = ('UNIQUE_VALID_ROOT','UNIQUE_VALID_FAMILY','INTERIOR_SINGLE_FAMILY',
              'STORAGE_COMARGINAL','MIXED_COMARGINAL')
KEYS = ['snapshot','target_zone']
RULES = dict(version=VERSION,source='Complete V2-selected local roots, otherwise validated coupled roots',
    identity=list(IDENTITY),deduplication='Economic identity counted once regardless coupling paths; full path evidence retained',
    interior=f'native_power > operating_lower + {POWER_TOLERANCE} and native_power < operating_upper - {POWER_TOLERANCE}',
    hierarchy='Unique root; unique family; unique strict-interior family; remaining exact BESS+PHS -> Storage; other heterogeneous sets -> Mixed / co-marginal',
    remaining_set='For initially heterogeneous sets, nonempty strict-interior subset, otherwise complete valid set',
    VRE='Wind + Solar + non-intertemporal run-of-river hydro',
    Storage='Specifically unresolved co-marginal BESS + PHS; separate PHS/BESS remain where identified',
    indicator='Model-implied marginal price-setting resource',
    interpretation='Technology/resource class supporting the zonal marginal price; inseparable distinct resources remain co-marginal',
    market_reference='Inspired by GME marginal-technology concept; model dispatch/prices/constraints, not observed bids or official GME ITM',
    prohibited='No utilisation ranking, merit order, asset winner/hash, dispatch/capacity priority, shortest path, zero-price preference or inferred legal priority',
    primary_weight='Accepted represented time; volume proxy secondary diagnostic only',activation='REVIEW_ONLY_NOT_ROUTINE')

class APrimeError(RuntimeError):
    pass

def _families(values):
    return tuple(sorted(set(values),key=SOURCE_ORDER.index))

def classify_families(root_count, families, interior_count, interior_families):
    """No asset-level winner; all sets explicit and independently testable."""
    families=_families(families);interior_families=_families(interior_families)
    if root_count<1 or not families or not set(interior_families).issubset(families):
        raise APrimeError('UNRESOLVED_OR_INCONSISTENT_ROOT_SET')
    if bool(interior_count)!=bool(interior_families):
        raise APrimeError('INCONSISTENT_INTERIOR_SET')
    remaining=interior_families if len(families)>1 and interior_count else families
    if root_count==1:category,rule=families[0],'UNIQUE_VALID_ROOT'
    elif len(families)==1:category,rule=families[0],'UNIQUE_VALID_FAMILY'
    elif len(interior_families)==1:category,rule=interior_families[0],'INTERIOR_SINGLE_FAMILY'
    elif set(remaining)=={'BESS','PHS'}:category,rule='Storage','STORAGE_COMARGINAL'
    elif len(remaining)>1:category,rule='Mixed / co-marginal','MIXED_COMARGINAL'
    else:raise APrimeError('UNCOVERED_RESOLUTION_CASE')
    return dict(final_setter_category=category,final_resolution_rule=rule,
        selected_family_if_unique=category if rule in RULE_ORDER[:3] else None,
        resolution_root_families=json.dumps(remaining),resolution_family_count=len(remaining),
        resolution_root_count=interior_count if len(families)>1 and interior_count else root_count)

def resolve_attribution(table,audit):
    mapped=map_candidates(audit)
    if mapped.groupby(KEYS).remote.nunique().gt(1).any():raise APrimeError('SOURCE_AUTHORITY_CONFLICT: local/coupled roots mixed')
    width=mapped.operating_upper-mapped.operating_lower
    if width.le(POWER_TOLERANCE).any() or not np.isfinite(mapped[['native_power','operating_lower','operating_upper']]).all().all():
        raise APrimeError('INVALID_VALIDATED_OPERATING_RANGE')
    if (mapped.native_power.lt(mapped.operating_lower-POWER_TOLERANCE)|mapped.native_power.gt(mapped.operating_upper+POWER_TOLERANCE)).any():
        raise APrimeError('VALIDATED_ROOT_OUTSIDE_RANGE')
    mapped['a_prime_strict_interior']=(mapped.native_power>mapped.operating_lower+POWER_TOLERANCE)&(mapped.native_power<mapped.operating_upper-POWER_TOLERANCE)
    unique=mapped.drop_duplicates(KEYS+list(IDENTITY))
    fields=unique.groupby(KEYS,sort=False).agg(valid_root_count=('asset_id','size'),
        families=('root_family',_families),remote=('remote','first')).reset_index()
    inside=unique.loc[unique.a_prime_strict_interior].groupby(KEYS).agg(interior_root_count=('asset_id','size'),interior_families=('root_family',_families)).reset_index()
    fields=fields.merge(inside,on=KEYS,how='left',validate='one_to_one').rename(columns={'target_zone':'zone'})
    if not fields[['snapshot','zone']].merge(table[['snapshot','zone']],on=['snapshot','zone'],how='left',indicator=True)._merge.eq('both').all():
        raise APrimeError('ORPHAN_VALID_ROOT')
    additions=table[['snapshot','zone','primary_attribution_class']].merge(fields,on=['snapshot','zone'],how='left',validate='one_to_one',sort=False)
    records=[]
    for r in additions.itertuples(index=False):
        if not isinstance(r.families,tuple):raise APrimeError('UNRESOLVED_VALID_ROOT_SET')
        scope='COUPLED' if bool(r.remote) else 'LOCAL'
        if (r.primary_attribution_class=='MARKET_COUPLING')!=(scope=='COUPLED'):raise APrimeError('SOURCE_AUTHORITY_CONFLICT: V2 scope')
        if r.primary_attribution_class=='OTHER_INDETERMINATE':raise APrimeError('UNRESOLVED_V2_OBSERVATION')
        ic=0 if pd.isna(r.interior_root_count) else int(r.interior_root_count)
        inf=r.interior_families if isinstance(r.interior_families,tuple) else ()
        rec=classify_families(int(r.valid_root_count),r.families,ic,inf)
        rec.update(valid_root_count=int(r.valid_root_count),valid_family_count=len(r.families),
            valid_root_families=json.dumps(r.families),resolution_scope=scope,
            original_primary_attribution_class=r.primary_attribution_class,
            interior_root_count=ic,interior_family_count=len(inf),interior_root_families=json.dumps(inf),
            co_marginal_flag=int(r.valid_root_count)>1)
        records.append(rec)
    new=pd.DataFrame(records,index=table.index)
    if set(new).intersection(table):raise APrimeError('ORIGINAL_V2_FIELD_COLLISION')
    result=pd.concat([table.copy(),new],axis=1)
    if not result[table.columns].equals(table):raise APrimeError('V2_VALUE_MUTATION')
    return result,mapped

def zone_shares(table,weight='represented_hours'):
    values=table.groupby(['zone','final_setter_category'])[weight].sum().unstack(fill_value=0).reindex(index=ZONES,columns=ORDER,fill_value=0)
    return values.div(table.groupby('zone')[weight].sum(),axis=0).reindex(index=ZONES,columns=ORDER)*100

def run_case(year,scenario,root,output):
    from .uc2_postprocess import no_solver_calls
    root=Path(root).resolve();output=Path(output).resolve()
    if not output.is_relative_to(root/'A_PRIME_V1') or output==root/'A_PRIME_V1':raise APrimeError('PROTECTED_OUTPUT_TARGET')
    label=f'{year}_{scenario}';source=root/'V2'/scenario
    tp=source/f'marginal_setter_attribution_{label}.parquet';ap=source/f'marginal_setter_candidates_{label}.parquet'
    qp=source/f'marginal_setter_QA_{label}.json';q=json.loads(qp.read_text(encoding='utf-8'))
    if q['status'] not in ('PASS_BASE_SEMANTIC_GATE','PASS_FROZEN_METHOD_ATTRIBUTION'):raise APrimeError('UNVALIDATED_SOURCE')
    for p in [tp,ap]:
        if sha256_file(p)!=q['artifact_hashes'][p.name]:raise APrimeError('SOURCE_AUTHORITY_CONFLICT: V2 artifact hash')
    hashes={str(p):sha256_file(p) for p in [tp,ap,qp]}
    output.mkdir(parents=True,exist_ok=True)
    with no_solver_calls() as guard:
        table=pd.read_parquet(tp);audit=pd.read_parquet(ap);validate_structure(table)
        if not table.model_year.eq(year).all() or not table.scenario.eq(scenario).all():raise APrimeError('SOURCE_AUTHORITY_CONFLICT: label')
        result,mapped=resolve_attribution(table,audit);validate_structure(result)
        repeat=pd.concat([audit.iloc[::-1],audit.iloc[:1000].assign(coupling_path='["duplicate_QA_path"]')],ignore_index=True)
        alternative,_=resolve_attribution(table,repeat)
        if not result.equals(alternative):raise APrimeError('ORDER_OR_PATH_MULTIPLICITY_EFFECT')
        evidence=mapped.merge(result[['snapshot','zone','final_setter_category','final_resolution_rule','resolution_root_families','valid_family_count','interior_root_count']].rename(columns={'zone':'target_zone'}),on=KEYS,validate='many_to_one')
        evidence['in_resolution_family_set']=[f in json.loads(s) for f,s in zip(evidence.root_family,evidence.resolution_root_families)]
        evidence['in_resolution_root_set']=np.where(evidence.valid_family_count.gt(1)&evidence.interior_root_count.gt(0),evidence.a_prime_strict_interior,True)
        if mapped.residual.abs().max()>TOLERANCE:raise APrimeError('V2_KKT_EVIDENCE_FAILURE')
        price=mapped.merge(table[['snapshot','zone','zonal_price_EUR_MWh']].rename(columns={'zone':'target_zone'}),on=KEYS,validate='many_to_one')
        price_error=float((price.recon-price.zonal_price_EUR_MWh).abs().max())
        if price_error>TOLERANCE:raise APrimeError('V2_PRICE_EVIDENCE_FAILURE')
        shares=zone_shares(result)
        if (shares.sum(axis=1)-100).abs().max()>1e-10:raise APrimeError('SHARE_SUM_FAILURE')
        storage=result.loc[result.final_setter_category.eq('Storage')]
        if not storage.resolution_root_families.map(lambda x:set(json.loads(x))=={'BESS','PHS'}).all():raise APrimeError('STORAGE_SET_FAILURE')
        mixed=result.loc[result.final_setter_category.eq('Mixed / co-marginal')]
        if not mixed.resolution_root_families.map(lambda x:len(json.loads(x))>=2 and set(json.loads(x))!={'BESS','PHS'}).all():raise APrimeError('MIXED_SET_FAILURE')
        result.to_parquet(output/f'marginal_setter_a_prime_attribution_{label}.parquet',index=False)
        result.to_csv(output/f'marginal_setter_a_prime_attribution_{label}.csv',index=False)
        evidence.to_parquet(output/f'marginal_setter_a_prime_root_evidence_{label}.parquet',index=False)
        shares.to_csv(output/f'marginal_setter_a_prime_shares_{label}.csv',index_label='zone')
        summary=result.groupby(['final_setter_category','final_resolution_rule']).represented_hours.sum().rename('zone_hours').reset_index()
        summary['share_percent']=summary.zone_hours/result.represented_hours.sum()*100
        summary.to_csv(output/f'marginal_setter_a_prime_summary_{label}.csv',index=False)
        mixtures=result.loc[result.final_resolution_rule.isin(RULE_ORDER[3:])].groupby(['final_setter_category','resolution_root_families','valid_root_families','resolution_scope']).represented_hours.sum().rename('zone_hours').reset_index()
        mixtures['share_percent']=mixtures.zone_hours/result.represented_hours.sum()*100
        mixtures.to_csv(output/f'marginal_setter_a_prime_co_marginal_diagnostic_{label}.csv',index=False)
        zero=result.loc[result.zonal_price_EUR_MWh.abs().le(TOLERANCE)].groupby('final_setter_category').represented_hours.sum().reindex(ORDER,fill_value=0).rename('zero_price_zone_hours').to_frame()
        zero['share_of_zero_price_hours_percent']=zero.zero_price_zone_hours/zero.zero_price_zone_hours.sum()*100
        zero.to_csv(output/f'marginal_setter_a_prime_zero_price_diagnostic_{label}.csv',index_label='final_setter_category')
        # Reuse already verified UC withdrawal-volume weights, not LP primals.
        proxy=root/'MARGINAL_SETTER_GME_ANALOGUE_V1/method_comparison'/scenario/f'approach_A_selected_roots_{label}.parquet'
        pq=proxy.parent/f'method_comparison_QA_{label}.json';pqa=json.loads(pq.read_text(encoding='utf-8'))
        if sha256_file(proxy)!=pqa['artifact_hashes'][proxy.name]:raise APrimeError('VOLUME_PROXY_SOURCE_HASH')
        weights=pd.read_parquet(proxy,columns=['snapshot','zone','cleared_volume_proxy_MWh'])
        weighted=result.merge(weights,on=['snapshot','zone'],validate='one_to_one')
        zone_shares(weighted,'cleared_volume_proxy_MWh').to_csv(output/f'marginal_setter_a_prime_volume_proxy_diagnostic_{label}.csv',index_label='zone')
        hashes.update({str(proxy):sha256_file(proxy),str(pq):sha256_file(pq)})
        prior_families=root/'FINAL_RESOLVER_V2'/scenario/f'marginal_setter_final_attribution_{label}.parquet'
        before=pd.read_parquet(prior_families,columns=['snapshot','zone','final_root_families'])
        transition=result[['snapshot','zone','final_setter_category']].merge(before,on=['snapshot','zone'],validate='one_to_one')
        old_pair=transition.final_root_families.map(lambda x:set(json.loads(x))=={'BESS','PHS'})
        recovery=transition.loc[old_pair].final_setter_category.value_counts().to_dict()
        hashes[str(prior_families)]=sha256_file(prior_families)
        report=dict(status='PASS_A_PRIME_REVIEW',version=VERSION,rules=RULES,model_year=year,scenario=scenario,
            rows=len(result),hours_per_zone=result.groupby('zone').represented_hours.sum().to_dict(),
            category_shares_percent=(result.groupby('final_setter_category').represented_hours.sum().reindex(ORDER,fill_value=0)/result.represented_hours.sum()*100).to_dict(),
            resolution_rule_shares_percent=(result.groupby('final_resolution_rule').represented_hours.sum().reindex(RULE_ORDER,fill_value=0)/result.represented_hours.sum()*100).to_dict(),
            former_BESS_PHS_pair_recovery_zone_hours=recovery,original_V2_columns_identical=True,
            complete_V2_selected_roots_only=True,candidate_order_duplicate_path_invariant=True,
            share_sum_max_error_percent=float((shares.sum(axis=1)-100).abs().max()),
            max_KKT_residual_EUR_MWh=float(mapped.residual.abs().max()),max_reconstruction_error_EUR_MWh=price_error,
            solver_invocations=guard['solver_invocations'],network_opens=0,routine_activation=False,source_hashes=hashes,
            implementation_sha256=sha256_file(Path(__file__)),artifact_hashes={p.name:sha256_file(p) for p in output.iterdir() if p.suffix in ('.csv','.parquet')})
        (output/f'marginal_setter_a_prime_QA_{label}.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report

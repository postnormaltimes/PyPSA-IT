"""Matched final_v4/candidate reporting collection; missing cases remain unknown."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..common import ROOT, ZONES, dump_json, sha256_file
from ..final_adequacy_candidate import SCENARIOS, VARIANTS, settings
from ..visualization.uc2_comparison import _verified_source_digest

DATASETS = {
    'generation': 'CANONICAL/statistics/annual_primary_generation_by_zone.csv',
    'utilization': 'generator_capacity_factors_by_zone_carrier.csv',
    'curtailment': 'vre_curtailment.csv',
    'storage_totals': 'VIS_X1/data/uc_storage_hydro_totals.csv',
    'monthly_zonal_prices': 'CANONICAL/statistics/uc2_price_monthly_zonal_means.csv',
    'monthly_national_prices': 'CANONICAL/statistics/uc2_price_monthly_national_load_weighted.csv',
    'interface_limits': 'CANONICAL/statistics/uc2_net_interface_summary.csv',
    'net_imports': 'CANONICAL/statistics/annual_net_imports_by_zone.csv',
}


def additional_ocgt_tables(network, plan):
    """Read incremental-unit UC dispatch and native objective contributions."""
    hourly = network.generators_t.p.loc[:, plan.child_id].copy()
    energy_weights = network.snapshot_weightings.generators
    objective_weights = network.snapshot_weightings.objective
    mc = network.get_switchable_as_dense('Generator', 'marginal_cost')
    available = network.get_switchable_as_dense('Generator', 'p_max_pu')
    rows = []
    for r in plan.itertuples():
        power = hourly[r.child_id]
        if not np.isfinite(power.to_numpy()).all():
            raise ValueError('ADEQUACY_INCREMENTAL_DISPATCH_NONFINITE')
        row = network.generators.loc[r.child_id]
        operating = float((power * mc[r.child_id] * objective_weights).sum())
        events = {}
        for field in ('start_up', 'shut_down'):
            events[field + '_EUR'] = float((getattr(network.generators_t, field)[r.child_id] * row[field + '_cost']).sum()) if row.committable else 0.
        standby = float((network.generators_t.status[r.child_id] * row.stand_by_cost * objective_weights).sum()) if row.committable else 0.
        energy = float(power.mul(energy_weights).sum())
        rows.append({'generator_id':r.child_id,'zone':row.bus,'carrier':row.carrier,'installed_MW':row.p_nom,
            'available_energy_MWh':float((available[r.child_id]*row.p_nom*energy_weights).sum()),
            'generation_MWh':energy,'equivalent_full_load_hours':energy/row.p_nom,
            'capacity_factor':energy/(row.p_nom*energy_weights.sum()),
            'operating_cost_EUR':operating, **events,'stand_by_EUR':standby,
            'dispatch_objective_contribution_EUR':operating+sum(events.values())+standby,
            'source_authority':'UC_MILP','fixed_investment_cost':'NOT_CALCULATED'})
    columns = ['generator_id','zone','carrier','installed_MW','available_energy_MWh','generation_MWh',
               'equivalent_full_load_hours','capacity_factor','operating_cost_EUR','start_up_EUR',
               'shut_down_EUR','stand_by_EUR','dispatch_objective_contribution_EUR','source_authority','fixed_investment_cost']
    return pd.DataFrame(rows, columns=columns), hourly


def capture_additional_ocgt(network, scenario, variant, report, solved_sha):
    from ..final_methodology_networks import settings as execution_settings
    cfg = execution_settings(variant)
    crosswalk = cfg.get('incremental_unit_crosswalk', cfg['qa_root'] + '/INCREMENTAL_UNIT_CROSSWALK.csv')
    plan = pd.read_csv(ROOT / crosswalk, float_precision='round_trip')
    plan = plan.loc[plan.scenario.eq(scenario)]
    annual, hourly = additional_ocgt_tables(network, plan)
    annual.to_csv(report / 'candidate_additional_ocgt_annual.csv', index=False)
    hourly.to_parquet(report / 'candidate_additional_ocgt_hourly_MW.parquet')
    dump_json(report / 'candidate_additional_ocgt_manifest.json', {
        'variant':variant,'year':2050,'scenario':scenario,'UC_solved_sha256':solved_sha,
        'source_authority':'UC_MILP','solver_invocations':0,
        'unit_crosswalk':crosswalk, 'unit_crosswalk_sha256':sha256_file(ROOT / crosswalk),
        'artifacts':{name:sha256_file(report/name) for name in
                     ('candidate_additional_ocgt_annual.csv','candidate_additional_ocgt_hourly_MW.parquet')}})


def hourly_metrics(dispatch, prices, national_prices, weights, *, tolerance_MW=1e-4):
    """Preserve UC gross end-use weighting and distinguish hours from zone-hours."""
    expected = pd.date_range('2019-01-01', periods=8760, freq='h', name='snapshot')
    dispatch = dispatch.loc[dispatch.zone.isin(ZONES)].copy()
    dispatch['snapshot'] = pd.to_datetime(dispatch.snapshot)
    shed = dispatch.pivot(index='snapshot', columns='zone', values='load_shedding_MW').reindex(columns=ZONES)
    energy = dispatch.pivot(index='snapshot', columns='zone', values='gross_end_use_demand_MW').reindex(columns=ZONES)
    if not (shed.index.equals(expected) and prices.index.equals(expected) and national_prices.index.equals(expected) and weights.index.equals(expected)):
        raise ValueError('ADEQUACY_COMPARISON_CHRONOLOGY_FAIL')
    if not np.isfinite(shed.to_numpy()).all() or not np.isfinite(prices[list(ZONES)].to_numpy()).all():
        raise ValueError('ADEQUACY_COMPARISON_NONFINITE_SOURCE')
    if not weights.eq(1).all():
        raise ValueError('ADEQUACY_COMPARISON_REQUIRES_ACCEPTED_ONE_HOUR_WEIGHTS')
    shortage = shed.gt(tolerance_MW)
    national = shed.sum(axis=1)
    price_scarcity = np.isclose(prices[list(ZONES)], 15000., rtol=0, atol=1e-6)
    numerators = (prices[list(ZONES)] * energy * weights.to_numpy()[:, None]).sum(axis=1)
    denominator = float(energy.mul(weights, axis=0).sum().sum())
    national_weighted = numerators / energy.mul(weights, axis=0).sum(axis=1)
    np.testing.assert_allclose(national_weighted, national_prices, atol=1e-6, rtol=1e-10)
    any_scarcity = price_scarcity.any(axis=1)
    metrics = {
        'unserved_energy': (float(national.mul(weights).sum()) / 1000, 'GWh', 'UC_MILP'),
        'unique_shortage_hours': (int(shortage.any(axis=1).sum()), 'hours', 'UC_MILP'),
        'zonal_shedding_hours': (int(shortage.to_numpy().sum()), 'zone-hours', 'UC_MILP'),
        'maximum_national_simultaneous_shortage': (float(national.max()), 'MW', 'UC_MILP'),
        'maximum_zonal_shortage': (float(shed.to_numpy().max()), 'MW', 'UC_MILP'),
        'national_load_weighted_price': (float(numerators.sum() / denominator), 'EUR/MWh', 'FIXED_COMMITMENT_PRICE_LP'),
        'hourly_national_weighted_price_median': (float(national_prices.median()), 'EUR/MWh', 'FIXED_COMMITMENT_PRICE_LP'),
        'any_zone_at_VOLL_hours': (int(any_scarcity.sum()), 'hours', 'FIXED_COMMITMENT_PRICE_LP'),
        'at_VOLL_zone_hours': (int(price_scarcity.sum()), 'zone-hours', 'FIXED_COMMITMENT_PRICE_LP'),
        'VOLL_hour_contribution_to_annual_mean': (float(numerators.loc[any_scarcity].sum() / denominator), 'EUR/MWh', 'FIXED_COMMITMENT_PRICE_LP'),
        'lost_load_penalty': (float(national.mul(weights).sum()) * 15000, 'EUR', 'UC_MILP'),
    }
    for zone in ('NORD', 'CNOR'):
        metrics[f'{zone}_unserved_energy'] = (float(shed[zone].mul(weights).sum()) / 1000, 'GWh', 'UC_MILP')
        metrics[f'{zone}_shortage_hours'] = (int(shortage[zone].sum()), 'hours', 'UC_MILP')
    for label, months in (('winter_DJF', (12, 1, 2)), ('summer_JJA', (6, 7, 8))):
        mask = expected.month.isin(months)
        metrics[label + '_unserved_energy'] = (float(national.loc[mask].mul(weights.loc[mask]).sum()) / 1000, 'GWh', 'UC_MILP')
    event = dispatch.loc[dispatch.snapshot.between('2019-07-25', '2019-07-26 23:00')]
    return metrics, event


def _case_sources(root, scenario, version):
    """A candidate is never discovered through the accepted-result registry."""
    report = root / 'REPORTING'
    qa = root / 'QA/FINAL_SCENARIO_QA.json'
    receipt_path = report / 'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json'
    if not qa.exists() or not receipt_path.exists():
        return None
    final = json.loads(qa.read_text())
    receipt = json.loads(receipt_path.read_text())
    if any(r.get('status') != 'PASS' or r.get('year') != 2050 or r.get('scenario') != scenario for r in (final, receipt)):
        raise ValueError('ADEQUACY_UNVERIFIED_COMPARISON_CASE')
    authority_path = report / 'uc2_VIS_RESULT_AUTHORITY_REGISTER.csv'
    _verified_source_digest(report, receipt, authority_path)
    authority = pd.read_csv(authority_path)
    if set(authority.parent_model_version) != {version}:
        raise ValueError('ADEQUACY_COMPARISON_VERSION_FAIL')
    protected = {k.replace('\\', '/'): v for k,v in receipt['protected_source_hashes'].items()}
    for role in ('CANONICAL_PHYSICAL', 'CANONICAL_PRICES_DUALS_ONLY'):
        selected = authority.loc[authority.current_role.eq(role)]
        if (len(selected) != 1 or not selected.year.eq(2050).all() or not selected.scenario.eq(scenario).all() or
            not selected.solved_or_unsolved.eq('SOLVED').all() or
            protected.get(str(selected.network_path.iloc[0]).replace('\\','/')) != selected.network_sha256.iloc[0]):
            raise ValueError('ADEQUACY_COMPARISON_SOURCE_BINDING_FAIL')
    return report, receipt


def refresh(variant='final_v5'):
    """Reuses registered canonical tables after final QA; never executes a case."""
    cfg = settings(variant)
    output = ROOT / cfg['result_root'] / '2050/COMPARISONS_FINAL_V4_V5'
    output.mkdir(parents=True, exist_ok=True)
    coverage, metrics, source_index, tables, events = [], [], [], {}, []
    for scenario in SCENARIOS:
        sources = {}
        for label, root, version in (
            ('final_v4', ROOT / 'results/final_methodology_v4/2050' / scenario, 'FINAL_METHODOLOGY_V4'),
            (variant, ROOT / cfg['result_root'] / '2050' / scenario, cfg['model_version'])):
            sources[label] = _case_sources(root, scenario, version)
        complete = all(sources.values())
        coverage.append({'year':2050, 'scenario':scenario, 'baseline_variant':'final_v4', 'candidate_variant':variant,
            'status':'COMPLETE_MATCHED_PAIR' if complete else 'WAITING_FOR_VERIFIED_CANDIDATE',
            'missing_variants':';'.join(k for k,v in sources.items() if v is None)})
        if not complete:
            continue
        for label, (report, receipt) in sources.items():
            def read(name, parquet=False):
                path = report / name
                digest = _verified_source_digest(report, receipt, path)
                source_index.append({'scenario':scenario,'variant':label,'path':str(path.relative_to(ROOT)), 'sha256':digest})
                return pd.read_parquet(path) if parquet else pd.read_csv(path)
            dispatch = read('CANONICAL/statistics/dispatch_8760_by_zone.parquet', True)
            price = read('fixed_commitment_prices_hourly.csv')
            price = price.set_index(pd.DatetimeIndex(price.iloc[:,0], name='snapshot')).loc[:, list(ZONES)]
            spread = read('CANONICAL/statistics/uc2_price_hourly_all_italy_spread.parquet', True)
            from ..visualization.uc2_comparison import _final_case_duration_source
            _final_case_duration_source(report, receipt, price.index)
            weights = pd.Series(1., index=price.index)
            values, event = hourly_metrics(dispatch, price, spread.load_weighted_national_EUR_per_MWh, weights)
            for metric, (value, unit, authority) in values.items():
                metrics.append({'year':2050,'scenario':scenario,'variant':label,'metric':metric,'value':value,
                                'unit':unit,'source_authority':authority})
            event = event.assign(variant=label, scenario=scenario)
            events.append(event)
            for area, filename in DATASETS.items():
                frame = read(filename).assign(variant=label, scenario=scenario)
                tables.setdefault(area, []).append(frame)
            if label == variant:
                tables.setdefault('additional_ocgt', []).append(read('candidate_additional_ocgt_annual.csv').assign(variant=label,scenario=scenario))
            for filename in ('storage_hydro_soc_hourly.parquet', 'NET_FLOW_A_TO_B_MW.parquet',
                'CANONICAL/statistics/uc2_net_interface_positive_utilization_hourly.parquet',
                'CANONICAL/statistics/uc2_net_interface_negative_utilization_hourly.parquet'):
                path = report / filename
                digest = _verified_source_digest(report, receipt, path)
                source_index.append({'scenario':scenario,'variant':label,'path':str(path.relative_to(ROOT)), 'sha256':digest})
            economics = report / 'economic_comparison.json'
            _verified_source_digest(report, receipt, economics)
            for key, value in json.loads(economics.read_text()).items():
                metrics.append({'year':2050,'scenario':scenario,'variant':label,'metric':key,'value':value,
                                'unit':'percent' if key.endswith('percent') else 'EUR','source_authority':'VERIFIED_SOLVE_RECEIPTS'})
            qa = json.loads((report.parent / 'QA/VERIFY_UC.json').read_text())
            for key, value in qa.get('operational_residuals',{}).get('objective_terms',{}).items():
                metrics.append({'year':2050,'scenario':scenario,'variant':label,'metric':key,'value':value,
                                'unit':'EUR','source_authority':'UC_MILP_OBJECTIVE_RECONSTRUCTION'})
    pd.DataFrame(coverage).to_csv(output / 'COLLECTION_STATUS.csv', index=False)
    files = [output / 'COLLECTION_STATUS.csv']
    if metrics:
        frame = pd.DataFrame(metrics)
        frame.to_csv(output / 'METRICS.csv', index=False)
        paired = frame.pivot(index=['scenario','metric','unit','source_authority'], columns='variant', values='value')
        paired['candidate_minus_final_v4'] = paired[variant] - paired['final_v4']
        paired.reset_index().to_csv(output / 'PAIRED_DELTAS.csv', index=False)
        pd.concat(events).to_parquet(output / 'JULY_25_26_UC_OPERATION.parquet', index=False)
        files += [output / n for n in ('METRICS.csv','PAIRED_DELTAS.csv','JULY_25_26_UC_OPERATION.parquet')]
        for area, frames in tables.items():
            path = output / f'{area}.csv'
            pd.concat(frames, ignore_index=True).to_csv(path, index=False)
            files.append(path)
    pd.DataFrame(source_index, columns=['scenario','variant','path','sha256']).to_csv(output / 'SOURCE_MANIFEST.csv', index=False)
    files.append(output / 'SOURCE_MANIFEST.csv')
    limitations = [
        {'output':'scarcity_hours_excluded_diagnostic','status':'UNRESOLVED_PRIOR_DEFINITION',
         'reason':'The prior exclusion definition was not located; no substitute exclusion rule is introduced.'},
        {'output':'fixed_cost_implications','status':'NOT_IN_DISPATCH_OBJECTIVE',
         'reason':'Additional installed MW is pinned in the build controls; no capital-investment objective or unsupported fixed-cost estimate is calculated.'},
        {'output':'marginal_regimes','status':'USE_VALIDATED_SAME_LP_ROOTS_ONLY',
         'reason':'The unresolved VOLL-related lexicographic crosswalk is preserved; roots and regimes are not imputed.'},
    ]
    pd.DataFrame(limitations).to_csv(output / 'LIMITATIONS.csv', index=False)
    files.append(output / 'LIMITATIONS.csv')
    result = {'status':'COMPLETE' if all(r['status']=='COMPLETE_MATCHED_PAIR' for r in coverage) else 'WAITING_FOR_COMPLETED_CASES',
              'variant':variant, 'canonical':False,'coverage':coverage,'solver_invocations':0,
              'artifacts':{p.name:sha256_file(p) for p in files}}
    dump_json(output / 'COMPARISON_MANIFEST.json', result)
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variant', choices=VARIANTS, default='final_v5')
    print(json.dumps(refresh(parser.parse_args().variant), indent=2))

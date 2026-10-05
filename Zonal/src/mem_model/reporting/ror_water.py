"""Post-solve RoR water/energy accounting, without changing the network."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ror_water_accounting(network, prepared: pd.DataFrame, year: int, scenario: str):
    """Join accepted H water provenance to UC-MILP physical dispatch only.

    Bypass is the unavoidable excess over frozen turbine throughput, not
    electrical curtailment or reservoir spill. All four quantities retain
    their dimensional identities and are weighted once in annual tables.
    """
    source=prepared.loc[prepared.year.eq(year)&prepared.scenario.eq(scenario)].copy()
    required={'snapshot','hydro_id','zone','natural_inflow_MW_water',
              'turbine_accessible_MW_water','unavoidable_bypass_MW_water','electrical_potential_MW'}
    if not required.issubset(source) or source.empty:
        raise ValueError('ROR_WATER_SOURCE_SCHEMA_OR_COVERAGE_FAIL')
    source['snapshot']=pd.to_datetime(source.snapshot,utc=True).dt.tz_localize(None)
    ids=network.generators.index[network.generators.carrier.eq('hydro_run_of_river')]
    if set(source.hydro_id)!=set(ids) or source.duplicated(['snapshot','hydro_id']).any():
        raise ValueError('ROR_WATER_SOURCE_ID_OR_DUPLICATE_FAIL')
    profiles=network.get_switchable_as_dense('Generator','p_max_pu')
    hourly,annual=[],[]
    for name in sorted(ids):
        rows=source.loc[source.hydro_id.eq(name)].sort_values('snapshot').set_index('snapshot')
        if not rows.index.equals(network.snapshots) or not rows.zone.eq(network.generators.at[name,'bus']).all():
            raise ValueError('ROR_WATER_CHRONOLOGY_OR_ZONE_FAIL')
        columns=['natural_inflow_MW_water','turbine_accessible_MW_water','unavoidable_bypass_MW_water','electrical_potential_MW']
        values=rows[columns].to_numpy()
        if not np.isfinite(values).all() or (values<0).any():
            raise ValueError('ROR_WATER_NONNEGATIVE_FINITE_FAIL')
        raw,convertible,bypass,potential=[rows[c] for c in columns]
        eta=float(network.generators.at[name,'efficiency'])
        capacity=float(network.generators.at[name,'p_nom'])
        if (raw-convertible-bypass).abs().max()>1e-8 or (potential-convertible*eta).abs().max()>1e-8:
            raise ValueError('ROR_WATER_DIMENSIONAL_RECONCILIATION_FAIL')
        if (potential-profiles[name]*capacity).abs().max()>1e-8:
            raise ValueError('ROR_WATER_NETWORK_POTENTIAL_FAIL')
        if name not in network.generators_t.p:
            raise ValueError('ROR_ACTUAL_UC_DISPATCH_MISSING')
        actual=network.generators_t.p[name].reindex(network.snapshots)
        if not np.isfinite(actual).all() or actual.lt(-1e-4).any() or (actual-potential).gt(1e-4).any():
            raise ValueError('ROR_ACTUAL_DISPATCH_BOUNDS_FAIL')
        table=pd.DataFrame({'snapshot':network.snapshots,'hydro_id':name,'zone':rows.zone.to_numpy(),
            'RAW_ROR_WATER_INFLOW_MW_water':raw.to_numpy(),
            'TURBINE_CONVERTIBLE_ROR_WATER_MW_water':convertible.to_numpy(),
            'ROR_BYPASS_MW_water':bypass.to_numpy(),
            'ROR_ELECTRICAL_POTENTIAL_MW':potential.to_numpy(),
            'ROR_ELECTRICAL_GENERATION_MW':actual.to_numpy(),
            'ROR_ELECTRICAL_CURTAILMENT_MW':(potential-actual).clip(lower=0).to_numpy()})
        hourly.append(table)
        water_weights=network.snapshot_weightings.stores
        electrical_weights=network.snapshot_weightings.generators
        annual.append({'year':year,'scenario':scenario,'hydro_id':name,'zone':rows.zone.iloc[0],
            'RAW_ROR_WATER_INFLOW_MWh_water':float(raw.mul(water_weights).sum()),
            'TURBINE_CONVERTIBLE_ROR_WATER_MWh_water':float(convertible.mul(water_weights).sum()),
            'ROR_BYPASS_MWh_water':float(bypass.mul(water_weights).sum()),
            'ROR_ELECTRICAL_POTENTIAL_MWh':float(potential.mul(electrical_weights).sum()),
            'ROR_ELECTRICAL_GENERATION_MWh':float(actual.mul(electrical_weights).sum()),
            'ROR_ELECTRICAL_CURTAILMENT_MWh':float((potential-actual).clip(lower=0).mul(electrical_weights).sum())})
    return pd.concat(hourly,ignore_index=True),pd.DataFrame(annual)

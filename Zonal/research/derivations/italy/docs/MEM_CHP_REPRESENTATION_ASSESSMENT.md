# MEM CHP Representation Assessment

Date: 2026-09-01  
Decision status: **RESOLVED FOR PRIMARY MEM IMPLEMENTATION**

## Evidence now available

The Terna 2024 thermoelectric capacity and reconstructed NET electricity production are joined to the official produced-heat series at `zone × CHP technology`.

| CHP technology | NET capacity MW | NET electricity GWh | Produced heat GWh | Electric CF | Electricity/heat ratio |
|---|---:|---:|---:|---:|---:|
| CCGT CHP | 18,365.17 | 57,097.435184 | 20,126.576573 | 0.354909432 | 2.836917395 |
| Gas turbine CHP | 1,392.37038 | 5,438.871768 | 8,510.886439 | 0.445912798 | 0.639048800 |
| Internal combustion CHP | 4,194.18249 | 19,371.544071 | 14,226.24982 | 0.527245413 | 1.361676079 |
| Extraction/condensing CHP | 1,520.945 | 5,088.558827 | 5,471.286841 | 0.381924206 | 0.930047898 |
| Back-pressure CHP | 508.08 | 888.915138 | 3,384.532454 | 0.199721168 | 0.262640453 |
| Fuel-cell CHP | 0.8 | 2.634873 | 1.234335 | 0.375980736 | 2.134649832 |

All six national rows have matched capacity, electricity and heat evidence. No output-with-zero-capacity anomaly occurs in the joined national/zone table.

The variation is economically meaningful. A generic CHP bucket would erase material differences in electrical utilization and heat dependence.

## Candidate treatments

### 1. Merge CHP with non-CHP equivalents

Not recommended. It would erase the observed distinction between CCGT CHP and non-CHP, GT CHP and non-CHP, and engine CHP and non-CHP. It would also lose the heat-coupling evidence just acquired.

### 2. Electricity-only CHP-specific generator bands

Recommended for the first MEM implementation.

Each CHP conversion technology remains a separate generator or cost/availability band. Heat output is retained as calibration metadata and can inform:

- availability or maintenance assumptions;
- marginal-cost bands where heat credits are defensible;
- optional minimum-output or must-run sensitivities;
- interpretation of historical CF differences.

This treatment fits the stated thesis scope: an electricity spot-market economic dispatch model, not a coupled electricity-and-heat system model.

### 3. Exogenous CHP electrical profiles or minimum-output constraints

Suitable as a sensitivity, not as the default. It can represent heat-led operation but risks prescribing historical behavior and suppressing endogenous market response. Any minimum-output or profile constraint needs an explicit future heat-demand rationale.

### 4. Coupled electricity-and-heat co-optimization

Technically possible, but not justified yet. It would require additional evidence that is currently absent:

- hourly heat demand by zone/site;
- boiler and alternative heat-supply costs;
- fuel input and efficiency surfaces;
- heat storage and network boundaries;
- heat-market or contractual constraints;
- consistent treatment of heat credits and emissions.

Adding heat buses/loads without these inputs would create false precision and expand the model beyond the frozen MEM scope.

## Frozen primary recommendation

> Implement CHP initially as electricity-only, CHP-specific generator bands, preserving CCGT CHP, GT CHP, internal-combustion CHP, extraction/condensing CHP, back-pressure CHP and fuel-cell CHP separately. Retain produced heat and electricity/heat ratios as empirical calibration evidence. Permit an exogenous heat-led sensitivity, but do not make heat co-optimization part of the primary MEM model unless a complete heat-demand and alternative-supply data perimeter is approved.

This is now the controlling first-implementation choice. The primary MEM model will use electricity-only CHP-specific generator bands and will not build a coupled heat system.

A later heat-led or must-run CHP sensitivity is mandatory after the base model operates. That sensitivity must be explicitly evidenced and must not overwrite the unconstrained primary dispatch result.

The canonical historical data remain neutral: they preserve capacity, NET electricity, produced heat, CF and ratios, so a later coupled-heat representation remains possible without rebuilding the evidence layer.

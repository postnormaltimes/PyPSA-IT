# MEM v2.9 model architecture, thermal stack and adequacy method

## Release decision

MEM is frozen as a fresh, independent model with this preferred thesis description:

> A seven-zone, hourly, fixed-capacity economic dispatch model of Italy with transport-constrained internal exchanges and exogenous price-taking neighbouring markets.

The seven Italian buses are `NORD`, `CNOR`, `CSUD`, `SUD`, `CALA`, `SICI`, and `SARD`. External-market buses are additional boundary components and are not Italian buses. The model will use fixed generation and storage capacities, hourly chronological dispatch, and fixed directional commercial transfer limits represented by PyPSA `Link` components.

The scope excludes capacity expansion, a physical AC transmission model, redispatch, unit commitment, reserve co-optimization, Stage 1/Stage 2 machinery, and any PyPSA-IT runtime dependency. This is a methodology and architecture freeze, not a declaration that the empirical model inputs are complete.

## Status vocabulary

| Status | Meaning in v2.9 |
|---|---|
| RESOLVED | Controlling architecture or interpretation has been decided. |
| DERIVED | Reproducible arithmetic from stated evidence and assumptions. |
| INTERIM | A disclosed project assumption that must remain replaceable or sensitivity-tested. |
| REQUIRES DATA | The authoritative empirical input is not yet frozen. |
| RUNTIME GATE | The answer must come from a calibrated chronological solve, not workbook arithmetic. |

Evidence is labelled with one of these classes: `DIRECT SOURCE CAPACITY`, `DIRECT SOURCE ENERGY`, `PROJECT DERIVATION`, `INTERIM PROJECT ASSUMPTION`, `ENERGY-IMPLIED DIAGNOSTIC`, `TERNA CAPACITY CONTROL`, `TERNA ADEQUACY BENCHMARK`, `GEM PLANT RECONCILIATION`, `PYPSA IMPLEMENTATION MAPPING`, `RUNTIME VALIDATION RESULT`, or `QA / RECONCILIATION ONLY`.

## Frozen architecture

### Italian system

- Exactly seven Italian market-zone buses.
- One complete, monotonic, unique hourly UTC calendar year: 8,760 snapshots in a normal year or 8,784 in a leap year.
- Fixed generators, storage power and storage energy. Every extendability flag is false.
- Internal exchange uses one non-negative directional `Link` per allowed direction. Opposite directions are separate links and may have different capacities.
- Italian `Line` components are absent. Commercial limits are not interpreted as impedance-based physical flows.
- Load is attached with `p_set`, not reconstructed from solved PyPSA-IT load dispatch.
- Explicit high-penalty load-shedding generators provide a diagnostic feasibility backstop. Energy not served and scarcity hours are reported by zone and hour.
- Italian zonal prices are the balance-constraint duals of the seven buses; no nodal-to-zonal averaging is needed.

### Generation granularity

Variable renewable generation is aggregated by `zone x technology`. Thermal generation should remain plant/unit-level where the evidence supports it. If unit evidence is incomplete, use multiple `zone x technology x marginal-cost-band` generators rather than one homogeneous CCGT or OCGT per zone. Preserve capacity, technology, fuel, efficiency, VOM, CO2 factor, commissioning year, status, availability, and CHP flag where available.

## Current thermoelectric stack: controlling source hierarchy

The hierarchy is:

1. **Terna 2024 NET efficient thermoelectric capacity** — canonical MW control.
2. **Global Energy Monitor, Global Oil and Gas Plant Tracker, August 2026** — plant/unit identity, status, technology, fuel, CHP, commissioning/retirement, coordinates, and ownership reconciliation.
3. **PyPSA/powerplantmatching/PyPSA-Eur plant data** — non-authoritative coordinates, bus mapping, metadata-gap and implementation support.

The official [Terna Thermoelectric Capacity API](https://developer.terna.it/docs/read/apis_catalog/generation/Thermoelectric_Capacity) returns consolidated annual efficient capacity by year, capacity type, region, province, category, subcategory, and MW. The canonical extract is `year=2024`, `capacityType=Netta`, with region, province, category, and subcategory unfiltered. The normalized project output will use `capacity_type=NET` while preserving the original Italian API value. Terna's API requires OAuth 2.0 credentials and a short-lived bearer token, as documented in [Terna's access-token instructions](https://developer.terna.it/docs/read/Access_Token).

The authoritative matrix is:

`province -> region -> market zone -> Terna technology/subcategory -> NET efficient MW`

National QA controls identified in the research are approximately 60.33 GW net thermoelectric capacity, 48.02 GW natural-gas capacity by fuel, 41.74 GW combined-cycle-like capacity including CHP, and 3.62 GW gas-turbine-like capacity including CHP. These are reconciliation controls, not substitutes for the detailed extract. Technology and fuel are separate dimensions: `Ciclo combinato` is CCGT-like and `Turbine a gas` is OCGT-like, but multi-fuel and CHP exceptions are not silently relabelled.

The official [GEM Global Oil and Gas Plant Tracker](https://globalenergymonitor.org/projects/global-oil-gas-plant-tracker) states that the current release is August 2026, uses plant and unit records, measures capacity in gross MW, and applies an EU/UK inclusion threshold of 20 MW at a location. These gross/net, threshold, and scope differences explain why GEM totals must not overwrite Terna totals. GEM is used to identify the physical units behind Terna aggregates and to evidence material 2025-H1 2026 changes.

The current physical stack can be frozen only after a reconciliation table records, at minimum: Terna technology, Terna zone, Terna net MW, GEM plant, GEM unit, GEM technology, GEM fuel, GEM status, GEM gross MW, commissioning year, coordinates, mapped zone, reconciliation status, and notes.

## 2040 thermoelectric method

The approximately 55 GW Slow/Base/High value is a `TERNA CAPACITY CONTROL`: a national net-efficient thermoelectric envelope. It is not 55 GW of gas, not a source-specified CCGT/OCGT split, and not automatically final PyPSA `p_nom`. The envelope includes categories such as biomass and geothermal.

The method is:

`Terna 2024 zone x technology baseline -> GEM-reconciled physical units -> evidenced retirements, conversions and additions -> 2040 zone x technology/plant fleet -> national reconciliation to the ~55 GW envelope`

No CCGT/OCGT split is invented merely to close the envelope. Biomass and geothermal must not be added a second time above a CCGT+OCGT total already set to 55 GW.

## 2050 energy-implied diagnostics

The selected PNIEC nuclear-scenario annual energy values and project assumptions produce these diagnostics:

| Technology | Energy | Capacity factor / basis | Derived or direct capacity | Classification |
|---|---:|---:|---:|---|
| Bioenergy | 10.6 TWh | 0.47 | 2.5746 GW | PROJECT DERIVATION |
| Bioenergy + CCS | 6.0 TWh | 0.47 | 1.4573 GW | PROJECT DERIVATION |
| Gas + CCS | 4.0 TWh | 0.20 interim | 2.2831 GW | INTERIM PROJECT ASSUMPTION |
| Gas + other fossil | 2.1 TWh | 0.20 interim | 1.1986 GW | INTERIM PROJECT ASSUMPTION |
| Nuclear | 64.2 TWh | direct 8.0 GW | 8.0000 GW | DIRECT SOURCE CAPACITY + ENERGY |
| Total | 86.9 TWh | mixed | 15.5136 GW | ENERGY-IMPLIED DIAGNOSTIC |

For the energy-derived rows, `GW = TWh / (8.76 x CF)`. The gas CFs are interim annual energy-to-capacity conversion assumptions; they are not final realized fleet-wide dispatch CFs.

The 15.5136 GW total is not a PNIEC installed-capacity target, an adequacy target, the complete programmable fleet, or a direct PyPSA `p_nom` total. The approximately 30 GW Terna efficient-programmable quantity is an independent `TERNA ADEQUACY BENCHMARK`. Subtracting 15.5136 from 30 does not create a 14.4864 GW missing, standby, gas, or adequacy requirement.

## Slow CDP benchmark and scenario asymmetry

The project retains the external CDP diagnostic:

- PV ratio: `32 / 180`
- Wind ratio: `29 / 66`
- BESS ratio: `12 / 36`
- Applying a 20% Slow reduction to the accepted 2050 Base PV/wind/BESS portfolio gives lost CDP of approximately **16.1031 GW**.
- Dividing by an illustrative 85% programmable-availability equivalent gives approximately **18.9449 GW**.
- `30 + 18.9449 = 48.9449 GW` is a communication and adequacy diagnostic only.

None of these values sets `IT2050_SLOW` installed `p_nom`. The calibrated Base and Slow runs must determine any retained or additional capacity by type and location. The model-derived Slow delta may be compared with 18.9449 GW, but equality is not imposed.

High does not mechanically retire thermal capacity. Additional FER/BESS may reduce thermal utilization, increase redundancy or curtailment, and change prices. Slow does not mechanically add thermal capacity before the chronological stress test.

## External markets

Each neighbouring bidding zone uses:

- an external bus;
- a fixed virtual generator with the hourly external electricity price as marginal cost;
- a fixed virtual load/export sink;
- a fixed non-extendable import `Link`;
- a fixed non-extendable export `Link`.

Trade is endogenous; historical flow schedules are not prescribed. Each interface contract must state the Italian zone, external bidding-zone ID, import MW, export MW, annual-static or hourly NTC/ATC basis, sending/receiving-side convention, optional loss, and optional small transaction cost.

The provisional architecture is FR/CH/AT/SI to NORD, GR to SUD, and ME to CSUD. Existing v2.8 Tunisia and Malta rows remain candidate scenario evidence only. Every mapping and capacity must be verified against the official 2040/2050 bidding-zone topology and project COD assumptions before freeze.

The virtual generator/load construction adds a removable objective baseline. Reported external economics must be reconciled as net import purchases minus export revenue, with tolls and losses reported separately. Raw auxiliary virtual-generator objective terms are not trade cost.

## Deterministic-dispatch boundary

The annual dispatch may report load shedding, energy not served, scarcity hours, storage depletion, residual-load peaks, saturated transport links, curtailment, and zonal marginal prices. It does not reproduce formal LOLE, probabilistic capacity credit, Terna adequacy methodology, N-1 security, reserve deliverability, or unit-commitment constraints.

## Implementation gates

Before the first calibrated solve, the runtime must reject any input set that fails one of these controls:

- missing, duplicated, non-hourly, non-monotonic, or timezone-naive snapshots;
- any year other than 8,760 or 8,784 complete UTC hours;
- an Italian bus set different from the seven frozen zones;
- any extendable Generator, Link, StorageUnit, or Store;
- any Italian physical AC Line;
- missing or negative loads, invalid availability bounds, non-positive efficiencies, or missing capacity units;
- non-conservative storage state equations or an unstated initial/terminal state rule;
- ambiguous directional-link capacity side or an unfrozen external interface mapping;
- a thermal capacity row whose canonical MW evidence is only powerplantmatching;
- unreconciled external objective accounting.

The open empirical gates and their exact acquisition requirements are maintained in `MEM_v2.9_EMPIRICAL_GAP_AND_ACQUISITION_REGISTER.csv`. Until those gates are closed, v2.9 is implementation-ready in architecture and methodology, but not a calibrated final model input set.

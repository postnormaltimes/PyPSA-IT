# MEM Scenario Capacity Package V2 — method and results

## Outcome

SCENARIO_CAPACITY_PACKAGE_V2 is frozen as the static installed-capacity source. Base/High capacities are unchanged at every year × scenario × technology × zone row. The accepted total Slow increments are unchanged; only their technology composition is revised. The V1 proportional-all-resources Slow rule is superseded.

## Controlling CDP derivation

The corrected Terna Figure-55 ratios are PV = 32/180, wind = 17/66 and electrochemical storage = 29/36. Slow retains 80% of Base PV/wind/BESS. The separate adopted programmable installed-to-available conversion is 0.85. These inputs produce 11.231326599327 GW lost CDP and 13.213325410972 GW additional dispatchable capacity in 2040; and 18.371377035663 GW lost CDP and 21.613384747839 GW additional dispatchable capacity in 2050.

## Slow fixed-resource rule

Bioenergy, bioenergy+CCS, geothermal and nuclear remain at Base capacity in Slow. Their increments are exactly zero. The 2040 increment is allocated only across Base methane technologies. The 2050 increment is allocated only across GAS_CCS and GAS_OTHER_FOSSIL. Frozen geography vectors are unchanged.

### 2040 national Slow composition (GW)

| Technology | Base | Increment | Slow | Rule |
|---|---:|---:|---:|---|
| CCGT | 42.348268607 | 10.320785134 | 52.669053741 | SCALABLE_IN_SLOW |
| GT_OCGT | 3.354651039 | 0.817569022 | 4.172220061 | SCALABLE_IN_SLOW |
| INTERNAL_COMBUSTION | 5.329903452 | 1.298961920 | 6.628865372 | SCALABLE_IN_SLOW |
| STEAM_OTHER_SURVIVING | 2.894547404 | 0.705436203 | 3.599983607 | SCALABLE_IN_SLOW |
| OTHER_SURVIVING_THERMAL | 0.289575830 | 0.070573131 | 0.360148962 | SCALABLE_IN_SLOW |
| GEOTHERMAL | 0.783053668 | 0.000000000 | 0.783053668 | FIXED_IN_SLOW |

### 2050 national Slow composition (GW)

| Technology | Base | Increment | Slow | Rule |
|---|---:|---:|---:|---|
| BIOENERGY | 7.273930047 | 0.000000000 | 7.273930047 | FIXED_IN_SLOW |
| BIOENERGY_CCS | 4.117318894 | 0.000000000 | 4.117318894 | FIXED_IN_SLOW |
| GAS_CCS | 6.450466268 | 14.172711310 | 20.623177578 | SCALABLE_IN_SLOW |
| GAS_OTHER_FOSSIL | 3.386494791 | 7.440673438 | 10.827168228 | SCALABLE_IN_SLOW |
| GEOTHERMAL | 0.771790000 | 0.000000000 | 0.771790000 | FIXED_IN_SLOW |
| NUCLEAR | 8.000000000 | 0.000000000 | 8.000000000 | FIXED_IN_SLOW |

## Fuel contract

All 2040 combustion capacity is methane; geothermal is separate non-combustion capacity. In 2050, GAS_CCS is interpreted as methane+CCS. GAS_OTHER_FOSSIL is a scenario source category decomposed into methane/hydrogen solver-fuel candidates without adding capacity. The conservative static candidate is 100% methane pending approval; 25% and 50% hydrogen-substitution sensitivities are preserved. No hydrogen CCS is assumed. STATIC_SOLVER_FUEL_SPLIT_STATUS remains PARTIAL.

## Residual materiality

Oil residual status: ACCEPTED_BASELINE_PROJECT_ALLOCATION_WITH_BOUNDED_RUNTIME_SENSITIVITY; the worst tested scaled zone × technology displacement is 778.621127 MW and the largest tested national technology-share displacement is 1.093239 percentage points. Coal residual status: ACCEPTED_NON_MATERIAL_CONTROL_RESIDUAL; the worst tested scaled cell displacement is 161.097269 MW and the largest zone-share displacement is 0.292904 percentage points. These are bounding checks, not fabricated plant assignments.

## Static solver contract

All additive capacity maps once to a named component pattern and carrier, with p_nom_extendable=false. Capacity, zone, carrier and assumption-class references are ready. Numerical efficiency, fuel price, carbon price, marginal cost, hourly availability and operational storage/hydro fields remain deliberately unresolved references. Hydro/storage component patterns remain in the readiness table and are not forced into generators.csv.

## QA and remaining gates

The package passes 151/151 capacity, fuel, geography, taxonomy and workbook checks, including a zero-match formula-error scan. Hydro stays fixed at 23.294 GW in all scenarios. No full PyPSA run was executed. The canonical v2.9 workbook was not mutated and its separate provenance/promotion gate remains BLOCKED.

# MEM Phase 4B — static dispatch economics and solver-carrier closure

## Outcome

SCENARIO_CAPACITY_PACKAGE_V2 remains byte-pinned and unchanged at the parent-capacity layer. Phase 4B creates exclusive fuel × CHP solver sub-bands inside those parents and attaches numerical static dispatch parameters. No hourly input file and no PyPSA run were created.

## 2040 fuel accounting

The final additive fuels are methane, bioenergy and geothermal. The 2024 Terna thermoelectric fuel-use bioenergy control of 3,586 MW is scaled by the common 55-GW anchor factor 1.014594213339, producing a 3638.334849032-MW 2040 bioenergy sub-band. It is allocated by renewable-source bioenergy geography and compatible ICE/steam/other parent shares, and subtracted from those parents. It is fixed at Base in Slow; the full Slow increment remains methane.

## CHP

CHP splitting occurs inside every frozen zone. Zone × conversion-technology observed 2024 shares are preferred; sparse bands use the national conversion share. The primary model is electricity-only. No heat credit or must-run condition is embedded.

## 2050 fuel accounting

GAS_OTHER_FOSSIL is split 50% methane and 50% hydrogen in every scenario × zone. Hydrogen is substitutional and adds zero MW. GAS_CCS is methane+CCS; no hydrogen CCS is created. Optional later H2 capacity-share sensitivities are 0%, 25%, 75% and 100%.

## Soft annual energy controls

PNIEC/Terna source outputs (bioenergy 10.6 TWh, bioenergy+CCS 6.0 TWh, gas+CCS 4.0 TWh, gas+other fossil 2.1 TWh and nuclear 64.2 TWh) are retained only for ex-post validation. All rows have NONE_HARD; there is no e_sum_min, e_sum_max or fixed annual generation.

## Marginal-cost convention

For combustion generators, MC = fuel price / electrical efficiency + VOM + CO2 price × chargeable direct CO2 / electrical efficiency + other variable cost. CCS-specific efficiency is used once, capture rate is used once, and BECCS receives no negative-emissions credit. Methane prices are 38.0563456 EUR2025/MWh_th in 2040 and 22.7578 in 2050; bioenergy is 9.3506; hydrogen is a transparent 42 EUR2025/MWh_th engineering assumption requiring price sensitivity. CO2 prices are 104.5504 EUR2025/t in 2040 and 400 in 2050.

### Dispatchable marginal costs

| Year | Solver subtechnology | Efficiency | EUR2025/MWh_el |
|---:|---|---:|---:|
| 2040 | METHANE_CCGT_CHP | 0.590000 | 105.065486 |
| 2040 | METHANE_CCGT_NON_CHP | 0.590000 | 105.065486 |
| 2040 | METHANE_GT_OCGT_CHP | 0.420000 | 145.375192 |
| 2040 | METHANE_GT_OCGT_NON_CHP | 0.420000 | 145.909492 |
| 2040 | METHANE_INTERNAL_COMBUSTION_CHP | 0.440000 | 140.039375 |
| 2040 | METHANE_INTERNAL_COMBUSTION_NON_CHP | 0.440000 | 140.039375 |
| 2040 | BIOENERGY_INTERNAL_COMBUSTION_CHP | 0.300300 | 33.942429 |
| 2040 | BIOENERGY_INTERNAL_COMBUSTION_NON_CHP | 0.468000 | 22.784815 |
| 2040 | METHANE_STEAM_OTHER_SURVIVING_CHP | 0.360000 | 168.714791 |
| 2040 | METHANE_STEAM_OTHER_SURVIVING_NON_CHP | 0.400000 | 150.893312 |
| 2040 | BIOENERGY_STEAM_OTHER_SURVIVING_CHP | 0.300300 | 33.942429 |
| 2040 | BIOENERGY_STEAM_OTHER_SURVIVING_NON_CHP | 0.468000 | 22.784815 |
| 2040 | METHANE_OTHER_SURVIVING_THERMAL_CHP | 0.350000 | 173.878071 |
| 2040 | METHANE_OTHER_SURVIVING_THERMAL_NON_CHP | 0.350000 | 173.878071 |
| 2040 | BIOENERGY_OTHER_SURVIVING_THERMAL_CHP | 0.300300 | 33.942429 |
| 2040 | BIOENERGY_OTHER_SURVIVING_THERMAL_NON_CHP | 0.468000 | 22.784815 |
| 2040 | GEOTHERMAL | 1.000000 | 6.090500 |
| 2050 | BIOENERGY_CHP | 0.300300 | 33.942429 |
| 2050 | BIOENERGY_NON_CHP | 0.468000 | 22.784815 |
| 2050 | BIOENERGY_CCS_CHP | 0.267205 | 41.896994 |
| 2050 | BIOENERGY_CCS_NON_CHP | 0.434905 | 26.823037 |
| 2050 | METHANE_CCS_CCGT_CHP | 0.520000 | 82.844354 |
| 2050 | METHANE_CCS_CCGT_NON_CHP | 0.520000 | 82.844354 |
| 2050 | METHANE_GT_OCGT_CHP | 0.430000 | 242.454363 |
| 2050 | METHANE_GT_OCGT_NON_CHP | 0.430000 | 243.122263 |
| 2050 | HYDROGEN_GT_OCGT_CHP | 0.430000 | 103.017619 |
| 2050 | HYDROGEN_GT_OCGT_NON_CHP | 0.430000 | 103.685519 |
| 2050 | GEOTHERMAL | 1.000000 | 5.802300 |
| 2050 | NUCLEAR | 0.360000 | 25.163444 |

## CCS

Methane CCGT+CCS uses a 0.52 CCS-specific net electrical efficiency, 90% capture and the accepted Terna 54 EUR/t-captured OPEX component as a project dispatch assumption; the carbon price applies only to residual stack CO2. Bioenergy+CCS uses 95% capture and subtracts the technology-data capture+compression electricity input once from the uncaptured electricity efficiency. Captured biogenic CO2 is reported but not remunerated in the objective.

## Availability

BLK-005 maintenance hours and Terna zonal outside-maintenance deratings define the thermal profile classes. Static annual-equivalent values are fallbacks; Phase 4C must stagger maintenance. The Slow 0.85 adequacy conversion is never used as hourly availability. Nuclear references the existing BLK-007 profile with mean 0.9245.

## Storage and hydro

BESS power, energy and duration are copied from the accepted scenario table; one-way efficiency is sqrt(0.90), standing loss is a transparent zero baseline, and terminal SOC is deferred. Hydro p_nom remains frozen. Hydro turbine efficiency 0.90 and symmetric PHS sqrt(0.75) remain candidates; national PHS power/53-GWh controls are not silently allocated zonally, so hydro static operation remains PARTIAL.

## Residual oil

Oil-source research is stopped. The frozen baseline allocation is retained under ACCEPTED_BASELINE_PROJECT_ALLOCATION_WITH_BOUNDED_RUNTIME_SENSITIVITY; one bounded alternative remains for later congestion/price sensitivity. Coal residual status is unchanged.

# Current methodology

## Scope and economic formulation

Stage B represents NORD, CNOR, CSUD, SUD, CALA, SICI and SARD for 2040/2050 Slow/Base/High. Installed capacity is fixed. No endogenous expansion is permitted. The final model supersedes the initial continuous dispatch-only production family: physical dispatch uses heterogeneous thermal synthetic units and UC; a same-system continuous reference and a fixed-commitment price LP retain distinct analytical roles. No alternative family is silently substituted.

The controlling execution/configuration is config/final_methodology_execution_v1.yaml and qa/final_methodology_closure/STATE.json with W/H/N/C/F PASS and native semantic parity PASS. Config/stage_b_uc1_common.yaml and the accepted UC2A calibrated crosswalk determine thermal unit and heat-rate construction. Static controls remain pre_pypsa_inputs; annual demand, fuel/carbon and availability are inherited from accepted horizon runtime tables. CAPEX does not enter dispatch marginal costs. 2040 applicable fossil gas remains methane; 2050 GAS_OTHER_FOSSIL follows the accepted methane/H2 capacity-envelope split, while GAS_CCS retains methane+CCS.

## Chronology, resources and flexible demand

Snapshots are 8760 synchronized UTC hours in 2019. Baseline accepted hourly controls preserve load and technical-availability chronology, while final horizon magnitudes and replacements are explicit. Zonal VRE governs solar; final horizon-specific native-cell wind profiles replace earlier wind. Final hydro zonal natural inflow and state census replace earlier geographic inflow descendants. Derived frozen artifacts are supplied; execution does not regenerate weather.

BESS/PHS retain accepted storage power/energy, cyclic boundaries and loss accounting. BESS RTE is 0.90, PHS 0.75. Italian PHS operational energy 53 GWh, pumping 6.4 GW and discharge 7.2523 GW remain controlling; physical HPHS reservoir-energy estimates are not substituted. PURE/MIXED states remain distinct where applicable; each mixed system has one shared water state for pumping and natural inflow. Final hydro transforms conserve annual water quantities and use the accepted RoR turbine/bypass mapping and spill controls. Prices for water are recovered from native fixed-commitment LP duals, with explicit state mapping.

P2X adds seven flexible electrical-withdrawal generators, hourly power limits and seven annual energy equalities. The same-case pre-UC P2X parent supplies only original continuous operating-bound fields when rebuilding the reference; calibrated child size, efficiency and marginal cost remain heterogeneous.

## Interfaces and external prices

Final signed interfaces represent 20 corridors / 40 directions: 10 Italian internal, 8 external and 2 CORS. Directional contract limits are preserved; internal links are lossless and no Italian AC Lines are introduced. FR/CH/AT/SI/ME/GR/MT/TN prices are frozen B10 inputs, with CORS a zero-injection commercial hub without its own price. Accepted 2040 B8D/R10 and 2050 B9H boundary packages are horizon authorities; no scenario automatically reruns Stage A.

## Results and reporting

Reference LP, UC MILP and fixed-commitment price LP have separate receipts and verification gates. Core reporting retains electrical balance, annual generation/trade, storage/hydro, P2X, commitment diagnostics, price statistics and water values. LEXICOGRAPHIC_REGIME_V1 is canonical routine reporting under config/marginal_regime.yaml and the accepted lexicographic reporting specification; matching validated roots are required. Older V2/final-resolver analyses are separate lineage/candidate work. VIS-X1 rendering is optional. Analytical acceptance and permission for the next horizon remain project decisions.

See DATA_PROVENANCE.md, MODEL_ARCHITECTURE.md and METHODOLOGICAL_PROGRESSION.md for source-to-model links and retained rationale. This document summarizes accepted methodology; the documented derivations determine the assumptions.

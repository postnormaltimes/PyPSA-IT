# Final numerical results

| Year | Scenario | Version | Purchasing price (EUR/MWh) | Rigid load (TWh) | P2X (TWh) | Physical ENS (MWh) |
|---|---|---|---:|---:|---:|---:|
| 2040 | Slow | final_v3 | 92.639185 | 411.500000 | 27.500000 | 0 |
| 2040 | Base | final_v3 | 83.896313 | 411.500000 | 27.500000 | 0 |
| 2040 | High | final_v3 | 85.690691 | 452.650000 | 30.250000 | 0 |
| 2050 | Slow | final_v6 | 74.093807 | 501.600000 | 81.500000 | 0 |
| 2050 | Base | final_v6 | 58.414647 | 501.600000 | 81.500000 | 0 |
| 2050 | High | final_v6 | 69.322920 | 551.760000 | 89.650000 | 0 |

All six cases have saved full-year solve, verification and final-QA PASS evidence. The numerical release includes 8,760 synchronized UTC hours per case; 61,320 Italian zone-hours for prices/physical dispatch, with an additional national dispatch aggregation. The core exports preserve full numerical precision.

`hourly_zonal_prices.csv` contains verified fixed-commitment LP prices. `hourly_national_price_index.csv` includes rigid/P2X weights, physical duration and the modeled national index. `hourly_generation_dispatch.csv` exports canonical physical UC dispatch without using pricing-LP primal operation. Additional Parquet/CSV tables cover capacities, generation, electricity balances, exchanges, interface utilization, storage, hydro, curtailment, commitment, price distributions and accepted marginal regimes.

The annual purchasing-price column above is electricity-volume weighted across zones and hours. It is not the time-weighted mean of hourly national index values. The data dictionary gives both definitions. These are simulated prices, not observed GME prices.

Historical comparison evidence is separately labelled. 2040 v2 has no numerical results. Comparing versions reflects their combined changes; it does not identify a single causal effect unless specifically controlled. The 2050 external EUR 400 replacement is an input treatment, not a price cap. Physical ENS and scarcity-price hours measure different phenomena.

Read [data definitions](DATA_DICTIONARY.md), [version evolution](FINAL_VERSION_EVOLUTION.md), [limitations](LIMITATIONS.md) and [figure sources](FIGURES.md). Full solved networks are not distributed: the published tables suffice for the advertised analyses and selected figures, but do not reconstruct every solver/KKT diagnostic or every historical experimental visualization.

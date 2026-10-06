# final_v4 capacity configuration

The revision adopts the later Terna 2050 system-analysis capacities rather than
the earlier PNIEC-derived totals. It is a change of scenario source, not a
repair of the earlier model.

| Technology | Earlier 2050 Base GW | Slow GW | Base GW | High GW |
|---|---:|---:|---:|---:|
| Solar PV | 245 | 144 | 180 | 198 |
| Onshore wind | 36 | 36.8 | 46 | 50.6 |
| Offshore wind | 15 | 16 | 20 | 22 |
| Nuclear | 8 | 10 | 10 | 10 |

VRE uses Base × 0.80 for Slow and Base × 1.10 for High. Nuclear is fixed.
Each revised technology retains its previous zonal capacity shares; nuclear
remains NORD 6.25 GW and CSUD 3.75 GW. Hourly availability is unchanged.

The additional 2 GW nuclear replaces GAS_CCS proportionally across its existing
zonal vector. Programmable capacity remains 30 GW in Base/High and
51.613384747842 GW in Slow. Other fixed programmable resources are unchanged.

| BESS, unchanged | Discharge GW | Energy GWh |
|---|---:|---:|
| Slow | 34.922448979592 | 190.133333333333 |
| Base | 43.653061224490 | 237.666666666667 |
| High | 48.018367346939 | 261.433333333333 |

Demand, P2X, hydro/PHS, thermal operating parameters, network capacities,
external prices and solver settings retain their previous definitions. The
2040 model is unchanged; inherited results keep their original version and
hashes. The 2050 Base result is validated. Slow/High are defined inputs and are
not represented as solved cases.

Terna annual generation and utilisation figures are comparison benchmarks,
not additional hard dispatch constraints. See [validation](VALIDATION.md).

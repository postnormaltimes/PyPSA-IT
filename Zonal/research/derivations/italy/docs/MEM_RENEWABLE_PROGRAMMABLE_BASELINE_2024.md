# MEM Renewable and Programmable Baseline 2024

Date: 2026-09-01  
Evidence status: 2024 source controls acquired; full historical freeze not yet reached

## 2024 national controls

| Technology/source | NET MW | NET GWh | Observed CF | MEM role |
|---|---:|---:|---:|---|
| Bioenergy | 3,800.0923 | 15,699.033862 | 0.471600954 | Programmable renewable source subset; non-additive to thermoelectric total |
| Geothermal | 771.79 | 5,275.5733 | 0.780308627 | Separate programmable renewable technology/carrier |
| Hydro renewable-source total | 19,324.42439 | 52,391.704319 | 0.309493729 | Total hydro MW and additive annual renewable-hydro energy control |
| Solar PV | 37,002.141 | 35,398.16138 | 0.109150930 | Variable renewable |
| Wind aggregate control | 12,959.811 | 22,087.791683 | 0.194546746 | Variable renewable source control; capacity components split, SUD 2022-2024 component energy retained as controlled residual |

Observed CF values are historical characterization only. They do not replace future scenario assumptions or constrain future PyPSA realized CF.

## Zone results: bioenergy

| Zone | NET MW | NET GWh | Observed CF |
|---|---:|---:|---:|
| NORD | 2,373.011 | 9,793.006103 | 0.471098984 |
| CNOR | 179.3273 | 446.486938 | 0.284222361 |
| CSUD | 445.849 | 1,577.289415 | 0.403849441 |
| SUD | 446.074 | 2,225.749339 | 0.569593778 |
| CALA | 175.447 | 1,074.513704 | 0.699136387 |
| SICI | 69.439 | 158.312394 | 0.260259956 |
| SARD | 110.945 | 423.675969 | 0.435935253 |

Bioenergy is a source/fuel cross-classification. These MW and GWh are not added to the thermoelectric totals.

## Geothermal

All 2024 geothermal capacity and generation map to CNOR/Tuscany: 771.79 MW and 5,275.5733 GWh.

Geothermal is not inferred from `Condensazione`. The source-specific Terna dataset is controlling.

## Hydro

| Zone | Total NET MW | Renewable-source NET GWh | Observed CF |
|---|---:|---:|---:|
| NORD | 14,715.18231 | 46,499.315973 | 0.360725473 |
| CNOR | 623.19755 | 1,103.552858 | 0.202145136 |
| CSUD | 2,330.13938 | 3,466.332896 | 0.169818212 |
| SUD | 225.32915 | 360.381613 | 0.182574929 |
| CALA | 833.364 | 798.620165 | 0.109395997 |
| SICI | 133.793 | 35.214924 | 0.030046179 |
| SARD | 463.419 | 128.28589 | 0.031601009 |

The detailed hydric type total is broader because `Serbatoio` includes eventual pumping. The 2024 national type-minus-renewable difference is 1,583.805583 GWh. It is retained as a national pumping-discharge perimeter proxy only.

Type-specific MW are not inferred from production shares.

Pumped hydro is now represented separately in the historical storage layer: 7,252.3 MW total NET discharge, of which 3,969.57561 MW is pure pumping outside the renewable-source hydro capacity control and 3,282.72439 MW is mixed pumping inside it. Physical inventory evidence supplies 6,809.3 MW charge power and 626,262.056948 MWh energy for the 2024 zonal allocation. Roundtrip efficiency remains a later model assumption.

## Thermal/DDS comparison perimeter

| Item | 2024 NET MW | Operation |
|---|---:|---|
| Terna thermoelectric raw source sum | 60,331.82927 | QA only |
| Exact-duplicate-normalized thermoelectric total | 60,330.37427 | Base |
| Bioenergy source subset | 3,800.0923 | No add |
| Geothermal source technology | 771.79 | Add outside 2024 thermoelectric extract |
| Current DDS-comparable perimeter | 61,102.16427 | Result |
| Hydro total | 19,324.42439 | Excluded from thermoelectric envelope |
| DDS 2040 thermoelectric control | approximately 55,000 | Future national benchmark only |

No CCGT/OCGT split is forced to close the 55-GW benchmark.

## Historical coverage

Actual source observations are normalized for 2019–2024. The 2025 Terna endpoint responses were empty on the retrieval date and are not treated as data.

Wind capacity is split using the 30 MW Beleolico evidence from 2022. Renewable-capacity blanks and the small unobserved offshore generation component have been accepted as controlled non-material residuals. The storage layer exists and the 2024 pumped-hydro overlap is reconciled.

The historical freeze remains incomplete because material thermal fuel/plant attribution and the current-2026 unit/status bridge require the absent complete GEM GOGPT August-2026 release.

# MEM v2.9 — Workbook QA Report

## Result

**PASS for the workbook revision.** Physical PyPSA fleet integration and chronological adequacy runs remain explicit future runtime gates, not failed workbook QA.

## Integrity

| Item | Result |
|---|---|
| Canonical v2.8 SHA-256 | `e339c55c5996ed6a23fcee18e9f7ee51b27d29464d9211028332f8c4ebdeabf2` |
| v2.9 SHA-256 | `0985185aa565033907318496e8a0e4597067f1255907cf6fc28d5dc09db20d65` |
| Worksheet count | 14, unchanged |
| Formula count after import | 641 |
| Workbook QA gates | 44 / 44 PASS |
| Independent QA checks | 24 / 24 PASS |
| Excel error tokens | 0 |
| Sheets visually inspected | 14 / 14 |

Error scan covered `#REF!`, `#DIV/0!`, `#VALUE!`, `#NAME?`, `#N/A`, `#NUM!` and `#NULL!`.

## Independent calculation checks

| Check | Workbook result | Independent result | Status |
|---|---:|---:|---|
| Bioenergy capacity | 2.574565 GW | 10.6 / (8.76 × 0.47) = 2.574565 GW | PASS |
| Bioenergy+CCS capacity | 1.457301 GW | 6.0 / (8.76 × 0.47) = 1.457301 GW | PASS |
| Gas+CCS interim tranche | 2.283105 GW | 4.0 / (8.76 × 0.20) = 2.283105 GW | PASS |
| Gas+other fossil interim tranche | 1.198630 GW | 2.1 / (8.76 × 0.20) = 1.198630 GW | PASS |
| Energy-implied total incl. nuclear | 15.513601 GW | 15.513601 GW | PASS |
| Nuclear implied annual CF | 91.609589% | 64.2 / (8 × 8.76) | PASS |
| Terna adequacy benchmark | 30.000000 GW | Independent control cell | PASS |
| Slow lost CDP | 16.103133 GW | Formula recomputation | PASS |
| Slow programmable-equivalent proxy | 18.944863 GW | 16.103133 / 0.85 | PASS |
| Indicative benchmark + proxy | 48.944863 GW | 30 + 18.944863 | PASS |

## Interpretation and double-counting gates

- No formula creates a `30 − 15.5136` or 14.4864 GW required-capacity residual.
- 48.9449 GW is tagged `DIAGNOSTIC ONLY — NOT FIXED P_NOM`.
- 18.9449 GW appears only in diagnostic/control and QA locations.
- Zonal CCGT/OCGT physical-capacity cells remain blank pending the real PyPSA fleet.
- No national CCGT:OCGT ratio was invented.
- The 55 GW 2040 control explicitly includes non-gas thermoelectric categories and is not added to separate biomass/geothermal rows.
- The 30 GW adequacy benchmark is not added to nuclear or the energy-implied technology table.

## Preservation checks

Exact value/formula comparisons passed for the full used ranges of:

- `01_SOURCES`;
- `03_ZONE_MAP`;
- `05_STORAGE_BY_ZONE`;
- `06_DEMAND_BY_ZONE`;
- `07_INTERZONAL_BY_ZONE`;
- `08_FOREIGN_BY_ZONE`;
- `10_NUCLEAR`.

Protected-range comparisons also passed for the accepted scenario rows, FER/nuclear table, original technical/cost block, existing PyPSA mapping rows, original QA gates and v2.8 changelog. This includes preservation of the user-edited nuclear efficiency of 0.36 in `09_TECH_COSTS`.

## Visual QA

All 14 rendered worksheets were inspected. No clipped titles, broken tables/charts, overlap or unreadable error output was found. The new `04_GENERATION_BY_ZONE` and `11_PYPSA_IMPLEMENTATION` sections were widened/reflowed after an initial layout review.

## Runtime gates that remain open

The workspace does not contain the actual PyPSA-Eur repository/commit, prepared zonal network NetCDF, executed conventional plant build, actual bus mapping, weather-year snapshots or solver/run outputs. Consequently:

- physical CCGT/OCGT MW and zones are intentionally not populated;
- ambiguous natural-gas plants, duplicate/missing units, gross/net differences and custom amendments cannot yet be quantified;
- Base/Slow scarcity, ENS/load shedding and model-derived adequacy delta have not been calculated.

These are PyPSA integration/runtime gates and require the real project network and run configuration. They do not justify substituting proportional Terna zonal shares or treating the CDP proxy as installed capacity.


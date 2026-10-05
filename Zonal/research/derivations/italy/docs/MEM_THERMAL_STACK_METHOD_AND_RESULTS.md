# MEM thermal stack method and results

## Release status

This note is the empirical thermal-stack addendum to the accepted MEM v2.9 architecture/methodology freeze. It closes the Terna 2024 capacity and production acquisition gates, but it does **not** close the GEM August-2026 plant/unit reconciliation, the proposed 2026 physical fleet, or the 2040/2050 thermal-fleet bridge.

Accordingly:

- the v2.9 workbook remains unchanged and controlling;
- no v2.9.1 workbook has been created;
- no full PyPSA model has been built or run;
- `MEM_Current_Thermal_Stack_2026.csv` and `MEM_Thermal_Fleet_Evolution_2026_2040_2050.csv` remain data-gated rather than being populated with placeholders.

## Controlling raw files

Both files were identified from their sheet structure and headers, not from their timestamps.

| Role | Uploaded filename | Sheet/range | Raw rows | Bytes | SHA-256 |
|---|---|---:|---:|---:|---|
| Thermoelectric capacity | `Export-DownloadCenterFile-20260901-085433.xlsx` | `Export!A1:G1196` | 1,194 data rows plus header/footer | 53,327 | `b85a7e2d18ac78401ece1ecd2a7fe1b4eaa8ed80862c272a7f8330cee5e3537b` |
| Thermoelectric production | `Export-DownloadCenterFile-20260901-085449.xlsx` | `Export!A1:F1176` | 1,174 data rows plus header/footer | 51,410 | `b3c69b97eafdda36a5f19ef31d0cfcaaec519266b5780933d7aa74ba2750e1e4` |

The binaries are preserved unchanged under `raw/terna/`. Terna is the publisher, the Download Center is the controlling acquisition route, and the acquisition date is 2026-09-01. The official [Terna Download Center](https://dati.terna.it/en/download-center) XLSX files are immutable raw evidence for this phase.

Both workbooks contain one visible `Export` sheet, no formulas, no hidden rows, no hidden columns, and no active OOXML filter criteria. Their footer states `Applied filters: Year is 2024; Month is gennaio;`. This metadata is retained as a Download Center anomaly: the values are consolidated annual observations, and the capacity row multiset matches the separately archived official API response exactly. The month token is not silently discarded, but there is no evidence of an XLSX row truncation.

## Numeric parsing

All 2,368 measurement cells in the two XLSX files are typed numeric Excel cells. They are accepted as typed finite numbers, with the raw value and cell type retained.

The reusable parser also supports Terna text payloads while refusing ambiguous punctuation:

- typed finite numeric cells are accepted directly;
- a comma or point is accepted as a decimal separator only when the payload provides unambiguous convention evidence;
- mixed comma/point strings are rejected;
- separator-only three-digit groupings are rejected rather than treated as thousands separators;
- punctuation is never removed blindly;
- the raw string is retained beside the normalized number.

The parser unit suite includes a `0,002` decimal-comma case and ambiguity-rejection cases.

## Capacity normalization

The capacity export contains both `Lorda` and `Netta`, 597 rows each. The canonical extract therefore explicitly filters:

`Anno = 2024 AND Tipo capacità = Netta`

The resulting Netta source coverage is:

- 20 regions;
- 107 provinces;
- 2 categories: `Cogenerative`, `Non cogenerative`;
- 14 returned subcategories;
- 597 official Netta source rows.

The returned subcategories are:

1. Altro genere
2. Celle combustibili
3. Celle combustibili con cogenerazione
4. Ciclo combinato
5. Ciclo Combinato Con Produzione Di Calore
6. Combustione Esterna
7. Combustione interna
8. Combustione Interna Con Produzione Di Calore
9. Condensazione
10. Condensazione e spillamento
11. Contropressione
12. Turbine a gas
13. Turbine A Gas Con Produzione Di Calore
14. Turbo espansione

No separately labelled geothermal subcategory is returned. Geothermal fuel must therefore not be inferred from `Condensazione` or any other technology label; the later fuel reconciliation remains separate.

### Source-grain QA exception

At the visible Terna grain `year × capacity type × region × province × category × subcategory`, the official file contains 19 exact repeated Netta groups, all in fuel-cell subcategories. The repeated-row sensitivity is 1.455 MW.

The canonical treatment is explicit:

- preserve every raw source row and its row number in lineage fields;
- aggregate repeated official rows at the canonical visible grain;
- retain the official summed national control;
- publish the hypothetical “keep one repeated row” sensitivity separately;
- never silently delete a row.

This produces 578 unique canonical capacity grains. The official summed total is 60,331.82927 MW; the one-row deduplication sensitivity total would be 60,330.37427 MW. The difference is immaterial to the 60.33 GW national control but remains documented.

## Province-region-market-zone crosswalk

The crosswalk combines official [Istat 2024 administrative geography](https://demo.istat.it/app/?a=2024&i=P02&l=en) with Terna's official zonal topology in Table 3 of the [zonal-configuration review](https://download.terna.it/terna/0000/1033/91.PDF), corroborated by the GME glossary.

Every Terna province maps exactly once. The canonical zones are:

`NORD, CNOR, CSUD, SUD, CALA, SICI, SARD`

`CNORD` is ingestion-only and is never emitted. In particular, Toscana and Marche map to CNOR; Umbria, Lazio, Abruzzo and Campania map to CSUD.

## Exact 2024 capacity results

| National control | Exact result | QA result |
|---|---:|---|
| Total Netta thermoelectric capacity | 60,331.82927 MW | Passes approximately 60.33 GW control |
| CCGT-like including CHP | 41,739.11900 MW | Passes approximately 41.74 GW technology control |
| Gas-turbine-like including CHP | 3,619.37798 MW | Passes approximately 3.62 GW technology control |
| Natural-gas capacity by fuel | Not derivable | 48.02 GW fuel control remains pending fuel reconciliation |

Technology and fuel remain separate dimensions. `Ciclo combinato` is provisionally CCGT-like and `Turbine a gas` is gas-turbine-like; neither mapping asserts that every MW is natural gas.

### First zonal technology view

This compact view highlights combined-cycle and gas-turbine capacity while preserving all other technologies in `Other`. The complete 7 × 14 Terna-subcategory matrix is the controlling output.

| Zone | CCGT non-CHP MW | CCGT CHP MW | Gas turbine non-CHP MW | Gas turbine CHP MW | Other thermal MW | Total NET MW |
|---|---:|---:|---:|---:|---:|---:|
| NORD | 12,026.855 | 10,726.618 | 167.67660 | 740.92438 | 5,656.44893 | 29,318.52291 |
| CNOR | 452.626 | 1,123.387 | 57.87000 | 459.39900 | 507.23806 | 2,600.52006 |
| CSUD | 5,571.190 | 670.765 | 725.21600 | 104.68100 | 2,911.16000 | 9,983.01200 |
| SUD | 2,157.278 | 2,204.229 | 251.58500 | 24.72700 | 3,018.50800 | 7,656.32700 |
| CALA | 1,582.000 | 1,607.335 | 227.04000 | 6.24100 | 197.35100 | 3,619.96700 |
| SICI | 1,548.000 | 1,441.836 | 647.62000 | 56.39800 | 1,284.70630 | 4,978.56030 |
| SARD | 36.000 | 591.000 | 150.00000 | 0.00000 | 1,397.92000 | 2,174.92000 |
| **Italy** | **23,373.949** | **18,365.170** | **2,227.00760** | **1,392.37038** | **14,973.33229** | **60,331.82927** |

## Production reconstruction

The production XLSX omits a visible `Lorda/Netta` column, but every otherwise-identical key

`year × region × province × category × subcategory`

occurs exactly twice. The controlling reconstruction uses the Download Center workbook alone:

- higher value = `Lorda`;
- lower value = `Netta`;
- if values are equal, retain `AMBIGUOUS_EQUAL_VALUES_NO_ROW_ORDER_ASSIGNED`; do not assign either raw row to a basis;
- if a group has one row, more than two rows, or cannot satisfy `Lorda >= Netta`, exclude it and place it in the exception register.

The physical identity is net production = gross production minus plant auxiliary consumption, so net cannot exceed gross for the same perimeter.

QA result:

- 587 exactly-two-row groups;
- 491 unequal inferred pairs;
- 96 equal-value pairs retained as ambiguous/equal;
- 0 group-count exceptions;
- 0 `Lorda < Netta` violations;
- exact reconstructed Netta production: 146,360.808172 GWh;
- exact reconstructed Lorda production: 152,080.223994 GWh;
- gross-minus-net difference: 5,719.415822 GWh.

An archived API payload is retained only as an optional shape/aggregate spot-check and possible future exception-resolution source. It is not a row-by-row derivation dependency.

## Observed 2024 capacity factors

Observed CF is calculated only with matched bases:

`CF = Netta production GWh / (Netta capacity MW × 8.76)`

The output contains 157 rows across:

- national × normalized technology;
- zone × normalized technology;
- national × normalized technology × CHP status;
- zone × normalized technology × CHP status.

There are no calculated CF values above 1. Selected national technology × CHP results are:

| Technology | CHP class | Netta MW | Netta GWh | Observed CF |
|---|---|---:|---:|---:|
| CCGT-like | CHP | 18,365.170 | 57,097.435184 | 0.35491 |
| CCGT-like | non-CHP | 23,373.949 | 45,910.935576 | 0.22422 |
| Gas-turbine-like | CHP | 1,392.37038 | 5,438.871768 | 0.44591 |
| Gas-turbine-like | non-CHP | 2,227.00760 | 477.007185 | 0.02445 |
| Internal combustion | CHP | 4,194.18249 | 19,371.544071 | 0.52725 |
| Internal combustion | non-CHP | 1,556.32180 | 3,295.339977 | 0.24171 |
| Condensing steam | non-CHP | 6,897.341 | 8,221.063139 | 0.13606 |

These are present-day utilization diagnostics. They can inform later merit-order bands and current-fleet characterization, but they do not replace the accepted 0.47 bioenergy assumption, the interim 0.20 gas+CCS/gas+other-fossil conversion assumptions, or any 2040/2050 realized fleet-wide CF.

## Implications for the future-fleet bridge

The empirical Terna baseline is now available, but the next step remains:

`Terna 2024 NET zone × technology controls -> GEM August-2026 plant/unit reconciliation -> 2025-H1 2026 change register -> proposed 2026 hybrid physical stack -> evidenced 2040/2050 evolution`

The future bridge retains all v2.9 guardrails:

- approximately 55 GW in 2040 is a national net-efficient thermoelectric envelope, not 55 GW gas and not a forced CCGT+OCGT total;
- the envelope includes relevant non-gas thermoelectric categories;
- 15.5136 GW remains an energy-implied 2050 diagnostic under project CF assumptions;
- approximately 30 GW remains an independent adequacy benchmark;
- no 14.4864 GW residual is created;
- 18.9449 GW remains an external Slow CDP comparison benchmark, not installed capacity;
- High does not mechanically retire thermal capacity;
- Slow does not mechanically add thermal capacity before the deterministic stress test;
- generic lifetime assumptions are not treated as announced retirements;
- no hydrogen-turbine fleet is invented without physical project evidence.

## Network-loss guardrail

The DDS electricity-demand perimeter includes network losses. Therefore primary MEM internal commercial transport Links remain lossless with `efficiency=1.0`, unless those source losses are first removed from demand and reconstructed endogenously. External HVDC/interconnector losses require a separate perimeter decision. This prevents simultaneous retention of source demand losses and addition of modelled internal transfer losses.

## Remaining external gate

The complete [GEM Global Oil and Gas Plant Tracker](https://globalenergymonitor.org/projects/global-oil-gas-plant-tracker) August-2026 workbook is not available locally. The official release page confirms gross-MW capacity, the EU/UK 20 MW threshold and a sub-threshold dataset. The download form requires user identity/contact/use/license submission, so this phase cannot complete that action autonomously.

Terna remains the canonical MW control after GEM arrives. GEM will supply identity, fuel, status, CHP/captive role, commissioning/retirement, coordinates and material 2025-H1 2026 changes. A complete one-to-one MW match is neither required nor expected.

## Historical-baseline extension

The later Terna bioenergy, geothermal, hydro, hydric-type and produced-heat integration, together with the actual 2019–2024 API history, is documented in `MEM_HISTORICAL_BASELINE_METHOD_AND_RESULTS_20260901.md`. The controlling historical gate is **HISTORICAL BASELINE INCOMPLETE**. This original thermal note remains valid for the 2024 thermoelectric conversion-technology layer, but it must be read with the historical addendum for geothermal perimeter drift, bioenergy non-additivity, hydro/pumping accounting, CHP heat characterization and workbook-promotion status.

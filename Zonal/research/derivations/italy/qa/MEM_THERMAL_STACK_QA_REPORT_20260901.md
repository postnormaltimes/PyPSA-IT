# MEM thermal-stack QA report — 2026-09-01

## Overall result

**Terna 2024 capacity:** `ACQUIRED / VALIDATED WITH SOURCE QA EXCEPTION`

**Terna 2024 production:** `ACQUIRED / VALIDATED WITH DOCUMENTED BASIS INFERENCE`

**GEM August-2026 reconciliation:** `REQUIRES USER DOWNLOAD`

**Proposed 2026 physical stack and 2040/2050 bridge:** `REQUIRES GEM AND FUTURE-FLEET EVIDENCE`

The accepted v2.9 workbook remains unchanged at SHA-256 `4a59c80bdf4da33d825ff94ea5617c03c325ca6fbc26233e7ed633ed6f910640`. No v2.9.1 workbook was created because the empirical physical stack is not yet genuinely closed.

## Raw binary integrity

| Dataset | Uploaded file | Bytes | SHA-256 | Archive result |
|---|---|---:|---|---|
| Capacity | `Export-DownloadCenterFile-20260901-085433.xlsx` | 53,327 | `b85a7e2d18ac78401ece1ecd2a7fe1b4eaa8ed80862c272a7f8330cee5e3537b` | Byte-identical raw copy verified |
| Production | `Export-DownloadCenterFile-20260901-085449.xlsx` | 51,410 | `b3c69b97eafdda36a5f19ef31d0cfcaaec519266b5780933d7aa74ba2750e1e4` | Byte-identical raw copy verified |

Publisher is Terna S.p.A.; acquisition route is the official [Terna Download Center](https://dati.terna.it/en/download-center); acquisition date is 2026-09-01.

## Workbook structure and coverage

| Check | Capacity | Production | Result |
|---|---:|---:|---|
| Sheet | `Export` | `Export` | PASS |
| Used range | `A1:G1196` | `A1:F1176` | PASS |
| Data rows | 1,194 | 1,174 | PASS |
| Formula cells | 0 | 0 | PASS |
| Hidden rows/columns | 0 / 0 | 0 / 0 | PASS |
| OOXML filter criteria | 0 | 0 | PASS |
| Year | 2024 | 2024 | PASS |

Both footers state `Year is 2024; Month is gennaio`. This is retained as a portal metadata anomaly. The annual-scale values, full geographic coverage and independent capacity API multiset match do not indicate row truncation.

## Capacity QA

- The source contains 597 `Lorda` and 597 `Netta` rows; Netta is filtered explicitly.
- Netta coverage is 20 regions, 107 provinces, 2 categories and 14 subcategories.
- All numeric measurement cells are typed, finite Excel numbers; no punctuation guessing was needed.
- The independent official Terna API payload has zero row-multiset mismatches with the Download Center capacity data.
- The raw Netta source has 19 exact repeated visible-grain groups, all fuel-cell categories. The reported sensitivity is 1.455 MW.
- Repeated source rows are aggregated with complete lineage, producing 578 unique canonical grains.
- Exact national Netta capacity is 60,331.82927 MW.
- Exact CCGT-like capacity including CHP is 41,739.11900 MW.
- Exact gas-turbine-like capacity including CHP is 3,619.37798 MW.
- The 48.02 GW natural-gas fuel control is not testable from this technology-only extract.
- No explicit geothermal subcategory is present, so no geothermal fuel label is inferred.
- The complete 7 × 14 zone/subcategory/CHP matrix sums exactly to the national total.

Capacity result: **PASS with one documented official-source uniqueness exception.** No MW was modified to force a control.

## Geographic mapping QA

- The official Istat geography contains 107 provinces.
- All 107 Terna province names map exactly once.
- All seven canonical MEM zones are present.
- `CNORD` is not emitted.
- Umbria maps to CSUD; Toscana and Marche map to CNOR.

Geographic result: **PASS.**

## Production reconstruction QA

The controlling XLSX has 587 visible production dimension keys and exactly two rows for every key.

Strict rule outcome:

- 491 unequal pairs: higher value classified Lorda, lower value classified Netta;
- 96 equal-value pairs: retained as ambiguous/equal with no raw-row ordering;
- 0 one-row groups;
- 0 groups with more than two rows;
- 0 `Lorda < Netta` violations;
- 0 pair exceptions.

Exact totals:

- Netta: 146,360.808172 GWh;
- Lorda: 152,080.223994 GWh;
- auxiliary/gross-minus-net difference: 5,719.415822 GWh.

The optional API payload agrees on row count and aggregate magnitude within floating serialization tolerance. It is not a row-by-row derivation dependency.

Production result: **PASS with documented physical-rule inference.**

## Capacity-factor QA

- Netta production is divided only by Netta efficient capacity.
- Four aggregation families were produced: national/zone × technology, with and without CHP split.
- 157 output rows were generated.
- No calculated CF exceeds 1.
- Missing-perimeter cases are not forced.
- Results are labelled current-fleet diagnostics only.

Capacity-factor result: **PASS.** These values do not overwrite future scenario CF assumptions.

## Methodology guardrails retained

| Guardrail | Result |
|---|---|
| Terna remains canonical MW source | PASS |
| GEM gross MW cannot overwrite Terna net MW | PASS at specification stage |
| Technology and fuel remain separate | PASS |
| 2040 ~55 GW is not 55 GW gas | PASS |
| No biomass/geothermal double count | PASS at methodology stage; fuel bridge pending |
| 15.5136 GW remains energy-implied diagnostic | PASS |
| ~30 GW remains independent adequacy benchmark | PASS |
| No 14.4864 GW residual | PASS |
| 18.9449 GW remains Slow comparison benchmark | PASS |
| High does not mechanically retire thermal | PASS |
| Slow does not mechanically add thermal before runtime test | PASS |
| Internal Links remain efficiency 1.0 under DDS loss-inclusive demand | PASS |
| No deterministic dispatch claim of LOLE/N-1 | PASS |

## Artifact integrity

All canonical CSVs and registers were authored and reopened with the spreadsheet artifact runtime. Reopened row and column extents were verified. The machine-readable QA file contains 22 checks and the output hash manifest.

Key output hashes:

| Output | SHA-256 |
|---|---|
| `Terna_Thermoelectric_Capacity_2024_Canonical.csv` | `82d5823e74b1fda54003555472b758e70e0e68ef131d9e51bc5fa230c1f60e72` |
| `Terna_Thermoelectric_Production_2024_Canonical.csv` | `48d26c298388e9df12de127a4e5de5f9a5a8ca0a457a0272e0f0deeba676396a` |
| `MEM_Province_Region_MarketZone_Crosswalk.csv` | `33b7a5b1013091ba9261db15777488ad8e5b1219aef16305a74bfb0abb8333f4` |
| Zone × technology × CHP matrix | `272c8fc146b088c8b31cabf200fd8e3c21c8bc23825795186d9c51d7d5ad51c7` |
| Observed CF diagnostics | `6ce378a00bf856f3c01227248d5f5bd8c0676ba568eca8c2adcc839784767e38` |

## Remaining blocker

The complete [GEM Global Oil and Gas Plant Tracker](https://globalenergymonitor.org/projects/global-oil-gas-plant-tracker) August-2026 workbook still requires the user's official form submission. Until that file is archived and reconciled, the 2026 unit-level physical stack and future fleet evolution register must not be presented as resolved.

## Release conclusion

The Terna empirical acquisition gate is closed for the 2024 zonal technology baseline and current utilization diagnostics. The thermal-stack phase as a whole is not complete: GEM and future-fleet evidence remain the controlling external gate. The project is therefore `implementation-ready for the next reconciliation step`, not `final model-ready`.

# MEM Final Zonal Capacity Master — QA

## Delivery

- Workbook: `MEM_2040_2050_FINAL_ZONAL_CAPACITY_MASTER.xlsx`
- SHA-256: `3982922eaea97d02f08d4bdc253ec58d210389fdeb9559cbd4a1e8f30c47449c`
- Size: 231,441 bytes
- Worksheets: 14

## Coverage

- Controlling generator rows represented: **723 / 723**
- Additional fixed hydro-static rows represented: **168**
- Detailed solver-view rows: **891**
- Parent technologies: **20**
- Solver subtechnologies: **35**
- Scenario-zone combinations: **42 / 42** (six scenarios × seven zones)
- Parent-capacity rows: **840**

## Reconciliation

- Embedded checks passed: **19 / 19**
- Formula-error scan: **0** occurrences of `#REF!`, `#DIV/0!`, `#VALUE!`, `#NAME?`, or `#N/A`
- Maximum parent-versus-V2 reconciliation difference: **1.023 × 10⁻¹² MW** (floating-point noise only)
- All installed capacities remain non-extendable.
- No final coal/oil solver capacity is present.
- 2040 combustion-fuel and 2050 methane/hydrogen substitution checks pass.
- Nuclear, hydro and all national programmable/thermal-envelope controls pass.

## Storage and hydro controls

- PHS discharge power: **7,252.3 MW**
- PHS pump power: **6,400 MW**
- PHS operational energy: **53,000 MWh**
- Physical 626.262-GWh HPHS reservoir-energy evidence is explicitly excluded from operational `e_nom`.
- BESS power, energy, duration and RTE are reproduced from the frozen storage contract without alteration.

## Presentation QA

- All 14 worksheets were reopened and rendered for visual inspection.
- Scenario sheets retain the required zone order and use formulas for Italy totals.
- Parent subtotals and additive solver rows are visually distinguished.
- Storage power is not added to generation-capacity totals.
- No hidden parent/child, hydro/PHS or fuel-decomposition double counting was found.

## Scope confirmation

No capacity, technology allocation, CHP split, fuel split, scenario assumption, or frozen CSV input was changed. No PyPSA, hourly-profile, chronology, optimization, or new-research workflow was entered.

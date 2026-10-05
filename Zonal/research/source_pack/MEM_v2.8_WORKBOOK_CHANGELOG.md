# MEM v2.8 workbook changelog

## Successor created

`Fase_5_PyPSA_Eur_Italia_2040_2050_Model_Ready_v2.8_SCENARIOS_FINALIZED.xlsx` was created from the v2.7 analytical workbook. The v2.7 workbook, v2.6 evidence archive, and final DOCX/PDF narrative baselines were not modified.

## Controlling changes

| ID | Sheet / artifact | Change | Evidence classification |
|---|---|---|---|
| V28-001 | `02_SCENARIOS` | Replaced the prior mixed scenario interface with exactly six headline physical scenarios: `IT2040_SLOW`, `IT2040_BASE`, `IT2040_HIGH`, `IT2050_SLOW`, `IT2050_BASE`, `IT2050_HIGH`. Slow demand now equals Base; High network now equals Base. | User-defined architecture |
| V28-002 | `06_DEMAND_BY_ZONE` | Replaced the blank runtime-share interface with the accepted 2025 allocation: CALA 11.1 TWh, CNORD/CNOR 30.2, CSUD 42.9, NORD 173.3, SARD 8.0, SICI 22.3, SUD 30.2; total 318.0 TWh. | Direct user input |
| V28-003 | `06_DEMAND_BY_ZONE` and `02_SCENARIOS` | Applied the same frozen shares to total, rigid and P2X demand in both horizons and all headline cases. | Formula-derived from direct user shares |
| V28-004 | `06_DEMAND_BY_ZONE` | Reconciled the direct 2040 controls exactly: rigid = 439.0 total - 27.5 P2X = 411.5 TWh. The legacy 412 TWh value remains visible only as a rounded reference. | Transparent reconciliation |
| V28-005 | `04_GENERATION_BY_ZONE` | Added formula-driven Slow/Base/High columns by zone and technology. FER Slow = Base × 0.80; High = Base × 1.10. Nuclear is excluded from the FER multiplier and remains 8 GW in all 2050 headline cases. | Derived from Base and user multipliers |
| V28-006 | `04_GENERATION_BY_ZONE` | Exposed dispatchable evidence controls: 55 GW aggregate thermoelectric envelope in all 2040 headline cases and an approximate ≥30 GW all-programmable adequacy floor in all 2050 headline cases. Technology and zonal MW gaps are explicit. | Direct approximate / derived approximate / absent |
| V28-007 | `05_STORAGE_BY_ZONE` | Added formula-driven Slow/Base/High charging power, discharging power and energy. Slow = Base × 0.80; High = Base × 1.10; duration and efficiencies unchanged. MACSE remains a labelled subset. | Derived from Base and user multipliers |
| V28-008 | `07_INTERZONAL_BY_ZONE` | Added explicit directional Slow/Base/High matrices and a 49-row project-effect trace. Slow subtracts only delayed effects from 546-P, 563-P and 732-P; 2050 Slow equals 2040 Slow. | Direct Base / project-derived Slow |
| V28-009 | `08_FOREIGN_BY_ZONE` | Added explicit directional matrices for all six scenarios. The approved 2030 floor is unchanged; embedded increments are not counted twice; vague planned increments remain flagged overlays. | Direct floor / evidence-bound treatment |
| V28-010 | `10_NUCLEAR` | Kept 8 GW with the approved 5 GW NORD / 3 GW CSUD project allocation in all three 2050 headline cases. Availability sensitivities remain secondary overlays. | Direct national / approved project allocation |
| V28-011 | `11_PYPSA_IMPLEMENTATION` | Added implementation mappings for scenario IDs, fixed demand shares, FER/BESS multipliers, network selection and dispatchable controls. | Governance / implementation map |
| V28-012 | `12_ASSUMPTIONS_QA` | Added 29 formula-driven QA gates and explicit evidence-bound limitations. | Formula QA / limitations register |
| V28-013 | `00_README`, `01_SOURCES`, `13_CHANGELOG` | Updated version guidance, appended v2.8 source/register records and embedded the workbook change record. | Governance / provenance |

## Formula architecture

- Scenario multipliers are visible in `02_SCENARIOS`; derived scenario numbers reference those cells.
- Demand formulas use the fixed share cell for each zone and the appropriate national total/component control.
- Zonal FER formulas multiply Base capacity by the controlling 0.80 or 1.10 scenario parameter.
- Storage power and energy formulas multiply Base by the controlling 0.80 or 1.10 parameter; duration is derived as energy divided by discharging power.
- Internal Slow formulas subtract the sum of eligible delayed project effects by direction key; High directly equals Base.
- Slow 2050 internal values directly equal Slow 2040 values.
- Foreign Slow and High values directly equal the approved Base floor because no separate project increment met the non-embedded, directional and cutoff-resolving evidence test.

## Explicitly unchanged

- `03_ZONE_MAP` and `09_TECH_COSTS` remain structurally unchanged from v2.7.
- GA-IT, old PNIEC Slow, no-nuclear, CO2, nuclear-availability and weather cases remain secondary benchmarks/overlays.
- No Terna transmission project was accelerated or invented.
- No CCGT, OCGT, gas+CCS, bioenergy, bioenergy+CCS or hydrogen-turbine installed capacity was fabricated.
- The final DOCX/PDF report files remain narrative baselines only.

## Companion audit artifacts

- `MEM_v2.8_IMPLEMENTATION_PLAN.md`
- `MEM_v2.8_SCENARIO_RULES.csv`
- `MEM_v2.8_NETWORK_DELAY_REGISTER.csv`
- `MEM_v2.8_DISPATCHABLE_CAPACITY_EVIDENCE.csv`
- `MEM_v2.8_QA_REPORT.md`


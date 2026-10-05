# MEM Historical Technology Baseline Phase 3 QA Report

Updated: 2026-09-02  
Automated result: **PASS WITH EXTERNAL GATES**  
Historical technology baseline: **FROZEN**  
Hydro model-parameter status: **PARTIAL**  
Current 2026 physical fleet: **BLOCKED**  
Workbook promotion: **BLOCKED**

## Executive result

All historical measurement checks pass. The 2019-2024 tables remain internally consistent at 758 capacity rows, 881 generation rows and 19 storage rows. Taxonomy integrity remains 47 groups with zero failures. The Phase-3 hydro reframe preserves every open-data value while preventing any exact open MW or reservoir-energy value from becoming a canonical control or model-ready parameter.

The 626,262.056948-MWh HPHS and 5,748,189.091752-MWh HDAM values are retained as physical/electrical-equivalent reservoir-energy evidence. Terna's approximately 53 GWh remains a separate national system-operational PHS control. It is not allocated to zones; all seven zonal 2024 PHS `energy_capacity_MWh` fields are blank. No roundtrip efficiency is invented.

The physical thermal branch requires both the complete GOGPT August-2026 and GCPT July-2026 releases. Workbook promotion remains a separate provenance gate.

## Controlling numerical checks

| Check | Result | Status |
|---|---:|---|
| 2024 thermoelectric raw source sum | 60,331.82927 MW | LINEAGE / QA ONLY |
| 2024 exact-duplicate-normalized thermoelectric NET | 60,330.37427 MW | PASS |
| 2024 geothermal NET | 771.79 MW | PASS |
| DDS-comparable thermoelectric + geothermal | 61,102.16427 MW | PASS |
| Total hydro NET | 23,294.0 MW | PASS |
| Renewable-source hydro NET | 19,324.42439 MW | PASS |
| Pure pumped hydro | 3,969.57561 MW | PASS |
| Mixed pumped hydro | 3,282.72439 MW | PASS |
| Pumped-hydro discharge NET | 7,252.3 MW | PASS |
| HPHS open-inventory charge-power sum | 6,809.3 MW | PHYSICAL/ALLOCATION EVIDENCE ONLY |
| HPHS open-inventory energy | 626,262.056948 MWh | PHYSICAL RESERVOIR ENERGY EVIDENCE ONLY |
| HDAM open-inventory energy | 5,748,189.091752 MWh | PHYSICAL RESERVOIR ENERGY EVIDENCE ONLY |
| Terna PHS maximum absorption | approximately 6,400 MW | OPERATIONAL CONTROL / NATIONAL MODEL CANDIDATE |
| Terna PHS operational energy | approximately 53,000 MWh | OPERATIONAL CONTROL / NATIONAL MODEL CANDIDATE |

## Hydro physical-inventory audit

The archived 196-row physical workbook was read without editing:

| Class | Plants | Installed/discharge evidence | Charge power | Physical energy estimate |
|---|---:|---:|---:|---:|
| HPHS | 23 | 7,929.2 MW | 6,809.3 MW | 626,262.056948 MWh |
| HDAM | 173 | 6,993.928 MW | 0 MW | 5,748,189.091752 MWh |
| Other/unclassified | 0 | 0 MW | 0 MW | 0 MWh |
| All programmable hydro | 196 | 14,923.128 MW | 6,809.3 MW | 6,374,451.148700 MWh |

Terna controls the 7,252.3-MW NET pumped-discharge total and provides approximate 7.3-GW generation, 6.4-GW absorption and 53-GWh system-operational controls for 22 relevant plants. The open database is used only for identity, geography, classification, physical plausibility and provisional allocation evidence.

The open turbine sum exceeds the Terna NET pumped-discharge control by 676.9 MW; the open pump sum exceeds Terna's approximate coincident maximum absorption by 409.3 MW. These differences are retained because the bases differ. The 626.262-GWh physical reservoir-energy sum exceeds the 53-GWh operational figure by 573.262 GWh, but that arithmetic difference has no capacity-gap interpretation.

Storage QA confirms that the seven 2024 PHS zone rows sum to 7,252.3 MW discharge. Canonical zonal `charge_power_MW` and `energy_capacity_MWh` are blank; the 6,809.3-MW open pump-power sum and former 53-GWh allocation remain only in explicitly non-model physical/superseded lineage fields. The national row holds the approximately 6,400-MW and 53,000-MWh Terna controls. Every 2024 PHS roundtrip-efficiency field remains blank.

`MEM_Hydro_Model_Parameter_Candidates.csv` contains 673 rows: 21 `CANONICAL_CONTROL`, 3 `MODEL_READY`, 23 `MODEL_CANDIDATE`, 417 `PHYSICAL_EVIDENCE_ONLY`, 20 `PROJECT_ASSUMPTION_REQUIRED` and 189 `REQUIRES_FURTHER_VALIDATION`. The three `MODEL_READY` rows are direct/same-table NORD turbine-power parameters: 5,227.9 MW run-of-river, 3,512.0 MW basin and 3,669.1 MW non-pumped reservoir. Zero open-database numerical rows are classified `CANONICAL_CONTROL` or `MODEL_READY`.

## Phase 3.1 cross-artifact and parameter QA

`MEM_PHASE3_1_CROSS_ARTIFACT_CONSISTENCY_QA.csv` has 17 checks, all PASS:

- zero canonical zonal PHS operational-energy values in both the pumping bridge and storage table;
- zero canonical zonal PHS pump-power values in both tables;
- one 53,000-MWh national operational-energy control preserved;
- no active historical gap says tracker absence blocks the frozen historical technology baseline;
- no `NONE_QA_CONTROL` row claims a solver component is instantiated;
- 758 capacity, 881 generation and 19 storage rows unchanged;
- all three PHS energy-allocation methods reconcile to 53,000 MWh;
- both PHS charge-power allocation methods reconcile to 6,400 MW;
- the national five-class hydro power bridge reconciles exactly to 23,294.0 MW;
- the NORD class bridge reconciles to 17,108.7 MW at the published 0.1-MW grain;
- the parameter register uses only the approved six statuses;
- exactly the three supported NORD turbine-power rows are `MODEL_READY`.

Terna Table 14 national class controls are 6,239.0 MW run-of-river, 5,019.9 MW basin, 12,035.1 MW reservoir including PHS and 7,252.3 MW PHS. The same-table non-pumped reservoir difference is 4,782.8 MW. These form a non-overlapping five-class bridge when pure and mixed PHS replace the PHS-inclusive reservoir subset.

The Terna 70-75% PHS round-trip range is retained as `MODEL_CANDIDATE`; no efficiency point or separate pump/turbine loss split is approved. The open database's 87% physical-energy reconstruction coefficient remains excluded from operating-efficiency inputs.

## Bioenergy perimeter QA

| Statistical view | 2024 NET MW | Treatment |
|---|---:|---|
| Renewable-source bioenergy | 3,800.0923 | Cross-classification, non-additive, not subtractable from thermal total |
| Thermoelectric-fuel bioenergy | 3,586.0 | Cross-classification under Terna fuel-use rules |
| Thermoelectric conversion total | 60,330.37427 | Controlling conversion-technology population |

The 214.0923 MW arithmetic difference is QA-only. It is not a capacity residual and is not used to infer a fossil/other thermal remainder.

## Taxonomy and historical tables

- Capacity: 758 rows.
- Generation: 881 rows.
- Storage: 19 rows.
- Taxonomy: 40 rows, version `V2026_3`.
- Taxonomy-integrity QA: 47 dataset-technology groups, zero failures.
- Canonical zones: exactly `NORD`, `CNOR`, `CSUD`, `SUD`, `CALA`, `SICI`, `SARD`.
- No `_NON_CHP` technology has `chp_flag=true`.
- CHP-specific technologies remain separate.
- All eight fuel-cell production exceptions remain closed.
- Renewable blank semantics remain accepted controlled non-material residuals.
- The additive renewable-hydro total remains distinct from non-additive Hydric type detail.

## 2024 analyst workbook QA

`MEM_2024_Installed_Capacity_By_Zone_Detailed.xlsx` contains:

1. `2024 Capacity by Zone` — detailed seven-zone matrix with 31 analytical rows;
2. `2024 Capacity Reconciliation` — explicit included/excluded accounting bridge.

The workbook has formulas for Italy totals and the reconciliation sheet, zero formula-error values, frozen headers, readable number formats and explicit accounting-role fields. The controlled generation-capacity reconciliation is 134.358115271 GW and excludes BESS, bioenergy cross-classifications, pumped-hydro subsets and duplicate control rows.

This number is an accounting reconciliation of installed generation perimeters, not a scenario target or model adequacy metric.

## GEM and 2026 fleet QA

The Phase-3.1 one-pass MEM-workspace search on 2026-09-02 found no complete official GOGPT August-2026 or GCPT July-2026 workbook. Source-page HTML or form metadata does not qualify as a release. Accordingly:

- `GEM_GOGPT_Aug2026_Italy_units.csv` was not fabricated;
- `Terna_GEM_Thermal_Reconciliation.csv` was not fabricated;
- `MEM_Current_Thermal_Stack_2026.csv` was not fabricated;
- `MEM_Thermal_Fleet_Evolution_2026_2040_2050.csv` was not fabricated.

Status: `GOGPT_AUG2026_REQUIRES_USER_DOWNLOAD` and `GCPT_JUL2026_REQUIRES_USER_DOWNLOAD`.

## v2.9 provenance QA

- Accepted SHA-256: `4a59c80bdf4da33d825ff94ea5617c03c325ca6fbc26233e7ed633ed6f910640`.
- Current local SHA-256: `c4caab305b3269c7b318025e715b4d1b4108cdcac023a27209d3808e2bc8490a`.
- Accepted-hash copy found: no.
- Populated sheet regions: 14/14 match pre-modification evidence.
- Table-value matrices: 14/14 match, zero cell differences.
- Accepted formula anchors: 611/611 match by direct OOXML inspection.
- Physical formula cells: 1,031.
- QA-030 through QA-044: 15/15 PASS.
- Comment content: 4/4 match.
- Conditional-formatting logic: 3/3 match.
- Presentation differences: 264 cells across three sheets; 264 font-family changes, 55 font-size changes and five number-format changes.

Conclusion: content consistency is strong, but binary and formatting provenance remain unresolved. Do not promote from the current copy without explicit approval or restoration of the accepted binary.

## Gate results

| Gate | Result |
|---|---|
| `HISTORICAL_TECHNOLOGY_BASELINE_STATUS` | **FROZEN** — accepted 2019-2024 observed technology measurements remain valid. |
| `HYDRO_MODEL_PARAMETER_STATUS` | **PARTIAL** — architecture is resolved; final active/usable energy, zonal allocation and efficiencies are not. |
| `THERMAL_FUEL_PLANT_ATTRIBUTION_STATUS` | **INCOMPLETE** — both plant-tracker releases are absent. |
| `CURRENT_2026_PHYSICAL_FLEET_STATUS` | **BLOCKED** — GOGPT and GCPT are absent. |
| `WORKBOOK_PROMOTION_STATUS` | **BLOCKED** — v2.9 provenance remains unresolved. |

No v2.9.1 workbook exists or was created.

## Machine-readable verification

The Phase-2.5 verification remains valid for historical measurements. The controlling Phase-3 evidence register is `MEM_PHASE3_GATE_STATUS.csv`; it freezes the historical technology layer while keeping hydro parameters, thermal plant attribution, the 2026 fleet and workbook promotion as independent gates.

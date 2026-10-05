# MEM Historical Baseline Phase 2 QA Report

> **Supersession notice:** this Phase-2 report is retained for lineage. Phase-2.5 hydro-energy, bioenergy-perimeter and workbook-provenance conclusions are controlled by `MEM_HISTORICAL_BASELINE_QA_REPORT.md`. In particular, the 626,262.056948 MWh HPHS physical-inventory estimate is not the canonical PHS energy capacity; the controlling historical value is Terna's approximately 53 GWh statement.

Date: 2026-09-01  
Outcome: **HISTORICAL BASELINE INCOMPLETE**  
Workbook successor authorized: **No**

## Integrity controls

- Accepted v2.9 expected SHA-256 is `4a59c80bdf4da33d825ff94ea5617c03c325ca6fbc26233e7ed633ed6f910640`.
- The present local binary hashes to `c4caab305b3269c7b318025e715b4d1b4108cdcac023a27209d3808e2bc8490a`. This phase did not edit it. The accepted binary must be restored or re-supplied before a later workbook promotion.
- Read-only semantic checks on the present binary still find 14 expected sheets, 1,031 formulas, zero formula/cached errors and PASS for QA decisions 030-044. This does not waive the controlling binary-hash requirement.
- All seven new Download Center XLSX files were copied byte-for-byte and re-hashed.
- Forty-two Terna API payloads were archived for six endpoint families and seven requested years (2019–2025). Credentials and tokens were not persisted.
- Every non-empty 2019–2024 provincial observation maps through the reviewed 107-province crosswalk.
- Output zones are exactly `NORD`, `CNOR`, `CSUD`, `SUD`, `CALA`, `SICI`, `SARD`.
- No `CNORD` output exists.

## 2024 aggregate checks

| Check | Exact result | Status |
|---|---:|---|
| Bioenergy NET capacity | 3,800.0923 MW | PASS |
| Bioenergy NET generation | 15,699.033862 GWh | PASS |
| Geothermal NET capacity | 771.79 MW | PASS |
| Geothermal NET generation | 5,275.5733 GWh | PASS |
| Hydro NET capacity | 19,324.42439 MW | PASS |
| Renewable-source hydro NET generation | 52,391.704319 GWh | PASS |
| Detailed hydric type total | 53,975.509902 GWh | PASS |
| National pumping-discharge perimeter proxy | 1,583.805583 GWh | PASS |
| Produced CHP heat | 51,720.766462 GWh | PASS within Download Center display precision |
| Thermoelectric raw source sum | 60,331.82927 MW | QA ONLY |
| Thermoelectric exact-duplicate-normalized capacity | 60,330.37427 MW | PASS |
| Independent published thermoelectric control | 60,330.4 MW | PASS at published precision |
| DDS-comparable current thermoelectric + geothermal | 61,102.16427 MW | PASS |

Bioenergy is non-additive; hydro is outside the thermoelectric envelope. No 55-GW double count is introduced.

## Historical-series QA

- Usable actual annual window: 2019–2024.
- All six 2025 endpoint payloads returned zero rows; 2025 is flagged unavailable rather than fabricated.
- Integrated machine-readable outputs contain 758 capacity rows, 881 generation rows and 19 storage rows.
- Thermoelectric production two-row reconstruction:
  - 2019: 591 inferred/equal groups, 71 equal pairs, 0 exceptions;
  - 2020: 587 inferred/equal groups, 79 equal pairs, 0 exceptions;
  - 2021: 590 inferred/equal groups, 89 equal pairs, 0 exceptions;
  - 2022: 588 inferred/equal groups, 91 equal pairs, 0 exceptions;
  - 2023: 584 valid pairs, 78 equal pairs, 0 exceptions;
  - 2024: 587 valid pairs, 91 equal pairs, 0 exceptions.
- All eight four-row fuel-cell groups pass the two-zero/two-positive structural rule; inferred Netta totals 1.1487043 GWh.
- 326 official visible-grain multiplicity groups are retained and aggregated with lineage.
- Eleven hydric `Serbatoio` visible-grain multiplicity groups occur in every year and remain documented.
- Renewable-capacity blanks remain raw blanks. Twenty all-blank groups with positive generation total 0.043666 GWh and are accepted as controlled non-material residuals.

## Perimeter checks

- Geothermal exact overlap is resolved year by year: inside the thermoelectric capacity extract in 2019–2022, outside in 2023–2024.
- Renewable-source hydro is the additive annual energy control.
- Hydric types are non-additive analytical detail pending a physical pumping split.
- Pumped hydro now has 2019-2024 national NET controls and a 2024 zonal discharge/charge/energy reconciliation. It remains a subset of total hydro.
- Wind capacity is split using the direct 30 MW Beleolico evidence from 2022; observed component energy is not fabricated.
- Historical CF is marked diagnostic only.

## CHP checks

- CCGT CHP, GT CHP, internal-combustion CHP, extraction/condensing CHP, back-pressure CHP and fuel-cell CHP remain separate.
- Capacity, NET electricity and produced heat join successfully at zone/technology level.
- No output-with-zero-capacity anomaly occurs in the 2024 joined table.
- Primary CHP treatment is frozen as electricity-only CHP-specific generator bands; no PyPSA model was implemented.

## Final automated verification

`HISTORICAL_BASELINE_PHASE2_FINAL_VERIFICATION.json` is the controlling final verification. Taxonomy, arithmetic, zone, additivity and no-premature-workbook checks pass; the freeze gate remains incomplete because material thermal fuel attribution fails and the accepted v2.9 hash is not presently available locally.

The verification reopened every required CSV through the spreadsheet artifact runtime and checked raw byte sizes/hashes, the v2.9 expected-versus-current hash mismatch, actual 2019–2024 coverage, exact seven-zone codes, national energy/capacity controls, hydro and thermal perimeter arithmetic, required technology/fuel taxonomy codes, closed fuel-cell exceptions, documentation presence and absence of a premature v2.9.1 workbook.

## Remaining blockers to `HISTORICAL BASELINE FROZEN`

1. Acquire the complete official GEM Global Oil and Gas Plant Tracker August-2026 release, including sub-threshold data, to resolve material thermal fuel/plant attribution and the 2026 physical fleet.
2. Restore or re-supply the accepted v2.9 workbook binary with SHA-256 `4a59c80bdf4da33d825ff94ea5617c03c325ca6fbc26233e7ed633ed6f910640` before any later workbook promotion.

## Workbook gate

The complete material technology history has not yet passed the freeze rule. Therefore:

- this phase makes no workbook edits and does not accept the current mismatched v2.9 binary as the controlling binary;
- no `v2.9.1_HISTORICAL_TECH_BASELINE_INTEGRATED.xlsx` was created;
- workbook promotion remains a later controlled action after `HISTORICAL BASELINE FROZEN`.

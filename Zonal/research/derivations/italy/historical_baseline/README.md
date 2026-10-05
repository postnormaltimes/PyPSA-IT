# MEM historical baseline — artifact index

Status: **HISTORICAL BASELINE INCOMPLETE**  
Coverage: actual Terna observations for 2019–2024; 2025 endpoint responses were empty on 2026-09-01

## Source-of-truth tables

- `normalized/MEM_Historical_Capacity_By_Zone_Technology.csv`
- `normalized/MEM_Historical_Generation_By_Zone_Technology.csv`
- `normalized/MEM_Historical_Storage_By_Zone_Technology.csv`
- `normalized/MEM_HISTORICAL_TECHNOLOGY_TAXONOMY.csv`

## 2024 controls

- bioenergy, geothermal and hydro NET capacity/production tables under `normalized/`;
- zone summaries, hydric type energy, CHP electricity/heat, thermal-perimeter and hydro/pumping reconciliations under `analysis/`.

## Governance

- `manifests/MEM_HISTORICAL_BASELINE_SOURCE_MANIFEST.csv`
- `manifests/MEM_HISTORICAL_BASELINE_DERIVATION_MANIFEST.csv`
- `analysis/MEM_HISTORICAL_BASELINE_COMPLETION_STATUS.csv`
- `../docs/MEM_HISTORICAL_BASELINE_METHOD_AND_RESULTS.md`
- `../qa/MEM_HISTORICAL_BASELINE_QA_REPORT_20260901.md`
- `qa/MEM_HISTORICAL_TAXONOMY_INTEGRITY_QA.csv`
- `qa/MEM_HISTORICAL_ARTIFACT_SUPERSESSION_REGISTER.csv`

## Promotion rule

Do not create or populate a new canonical workbook until the completion matrix is explicitly approved as `HISTORICAL BASELINE FROZEN`. The expected v2.9 hash is not currently present locally; see the QA report before any later promotion.

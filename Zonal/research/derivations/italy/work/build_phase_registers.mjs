import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { Workbook } from "@oai/artifact-tool";

const phaseRoot = path.resolve("..");
const releaseRoot = path.resolve("../..");
const docsDir = path.join(phaseRoot, "docs");
await fs.mkdir(docsDir, { recursive: true });

const csvEscape = (value) => {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
};
const serializeCsv = (headers, values) =>
  `${[headers, ...values]
    .map((row) => row.map(csvEscape).join(","))
    .join("\r\n")}\r\n`;
const columnLetters = (count) => {
  let value = count;
  let result = "";
  while (value > 0) {
    value -= 1;
    result = String.fromCharCode(65 + (value % 26)) + result;
    value = Math.floor(value / 26);
  }
  return result;
};
const sha256File = async (filePath) =>
  crypto.createHash("sha256").update(await fs.readFile(filePath)).digest("hex");

async function authorCsv(rows, sheetName, outputPath) {
  const headers = Object.keys(rows[0]);
  const values = rows.map((row) => headers.map((header) => row[header] ?? null));
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add(sheetName);
  const allValues = [headers, ...values];
  sheet.getRangeByIndexes(0, 0, allValues.length, headers.length).values = allValues;
  const range = `A1:${columnLetters(headers.length)}${allValues.length}`;
  const authored = await workbook.inspect({
    kind: "table",
    sheetId: sheetName,
    range,
    tableMaxRows: 4,
    tableMaxCols: Math.min(headers.length, 20),
    maxChars: 6000,
  });
  await fs.writeFile(outputPath, serializeCsv(headers, values), "utf8");
  const reopened = await Workbook.fromCSV(await fs.readFile(outputPath, "utf8"), {
    sheetName,
  });
  const verified = await reopened.inspect({
    kind: "table",
    sheetId: sheetName,
    range,
    tableMaxRows: 3,
    tableMaxCols: Math.min(headers.length, 20),
    maxChars: 5000,
  });
  return {
    path: outputPath,
    rows: rows.length,
    columns: headers.length,
    sha256: await sha256File(outputPath),
    authored: authored.ndjson,
    verified: verified.ndjson,
  };
}

const baselineGapPath = path.join(
  releaseRoot,
  "MEM_v2.9_EMPIRICAL_GAP_AND_ACQUISITION_REGISTER.csv",
);
const baselineGapWorkbook = await Workbook.fromCSV(
  await fs.readFile(baselineGapPath, "utf8"),
  { sheetName: "Gaps" },
);
const gapValues = baselineGapWorkbook.worksheets.getItem("Gaps").getUsedRange().values;
const gapHeaders = gapValues[0];
const gaps = gapValues.slice(1).map((values) =>
  Object.fromEntries(gapHeaders.map((header, index) => [header, values[index] ?? ""])),
);
const byGapId = new Map(gaps.map((row) => [row.gap_id, row]));

Object.assign(byGapId.get("GAP-001"), {
  status: "ACQUIRED / VALIDATED WITH SOURCE QA EXCEPTION",
  authoritative_source:
    "Terna Download Center official XLSX export; API retained as independent capacity row-multiset cross-check",
  local_search_result:
    "Export-DownloadCenterFile-20260901-085433.xlsx identified from headers/content as 2024 thermoelectric capacity; immutable raw copy archived; SHA-256 B85A7E2D18AC78401ECE1ECD2A7FE1B4EAA8ED80862C272A7F8330CEE5E3537B",
  exact_acquisition_or_decision:
    "Completed 2026-09-01: explicitly isolate Tipo capacità=Netta; retain the full technology stack; aggregate multiple official rows at the visible grain while preserving source-row lineage",
  required_fields_or_controls:
    "578 unique canonical grains from 597 Netta source rows; 20 regions; 107 provinces; 2 CHP categories; 14 subcategories; exact national total 60,331.82927 MW",
  acceptance_test:
    "PASS with source QA exception: 19 exact duplicate fuel-cell groups (1.455 MW sensitivity) are documented; canonical grain is unique; 60.33/41.74/3.62 GW controls pass; 48.02 GW fuel control remains not testable from this technology-only source",
  blocks: "No longer blocks the 2024 Terna zone x technology baseline; GEM still blocks the 2026 physical unit reconciliation",
});
Object.assign(byGapId.get("GAP-002"), {
  status: "ACQUIRED / VALIDATED",
  local_search_result:
    "Versioned 107-province crosswalk built from official ISTAT 2024 geography and official Terna/GME market-zone topology",
  exact_acquisition_or_decision:
    "Completed as MEM_Province_Region_MarketZone_Crosswalk.csv; canonical zones NORD, CNOR, CSUD, SUD, CALA, SICI, SARD",
  required_fields_or_controls:
    "All specified fields present; effective dates 2024-01-01 to 2024-12-31; source and review status retained",
  acceptance_test:
    "PASS: every Terna capacity and production province maps exactly once; all seven zones emitted; no CNORD emitted",
  blocks: "No longer blocks the 2024 zonal matrix",
});
Object.assign(byGapId.get("GAP-003"), {
  status: "REQUIRES USER DOWNLOAD",
  local_search_result:
    "No complete August-2026 GOGPT workbook found locally; official GEM release page and form bundle archived",
  exact_acquisition_or_decision:
    "User must complete the official GEM GOGPT Download data form and save the complete original August-2026 workbook, including the Sub-threshold and IRP sheets where supplied, into the MEM folder; do not pre-filter Italy",
  blocks: "Plant/unit identity, gross-MW/status/fuel/coordinates reconciliation and the proposed 2026 physical thermal stack",
});
Object.assign(byGapId.get("GAP-004"), {
  status: "REQUIRES GEM DATA",
  local_search_result:
    "Terna 2024 canonical zone x technology x CHP controls are now acquired; GEM input remains absent",
  exact_acquisition_or_decision:
    "After GAP-003, reconcile GEM units to the frozen Terna control without requiring a complete one-to-one MW match",
});
Object.assign(byGapId.get("GAP-005"), {
  status: "REQUIRES GEM DATA",
  local_search_result:
    "Terna 2024 capacity and production are now acquired; material 2025-H1 2026 plant changes remain unassembled",
});
Object.assign(byGapId.get("GAP-015"), {
  status: "PARTIALLY UNBLOCKED / REQUIRES GEM AND FUTURE-FLEET EVIDENCE",
  local_search_result:
    "The authoritative 2024 zonal technology baseline and current utilization diagnostics are now available; plant/unit status and 2025-H1 2026 bridge are still absent",
  exact_acquisition_or_decision:
    "Complete GEM reconciliation first, then evidence RETAIN/RETIRE/REFURBISH/CONVERT/CCS/NEW_BUILD classifications and reconcile 2040 to the approximately 55 GW national net-efficient envelope",
});

gaps.push({
  gap_id: "GAP-021",
  status: "ACQUIRED / VALIDATED WITH DOCUMENTED BASIS INFERENCE",
  artifact_or_decision: "Terna_Thermoelectric_Production_2024_Canonical.csv",
  evidence_class: "DIRECT SOURCE ENERGY / PROJECT DERIVATION",
  authoritative_source: "Terna Download Center official XLSX export",
  local_search_result:
    "Export-DownloadCenterFile-20260901-085449.xlsx identified from headers/content as 2024 thermoelectric production; immutable raw copy archived; SHA-256 B3C69B97EAFDDA36A5F19EF31D0CFCAAEC519266B5780933D7AA74BA2750E1E4",
  exact_acquisition_or_decision:
    "For each year x region x province x category x subcategory group, require exactly two rows; higher value=Lorda and lower value=Netta; equal pairs remain ambiguous/equal without raw-row ordering",
  required_fields_or_controls:
    "587 groups; 491 unequal inferred pairs; 96 equal-value pairs; 0 group-count exceptions; 0 Lorda<Netta violations; exact Netta 146,360.808172 GWh",
  acceptance_test:
    "PASS: no exceptions; Netta production is used only with Netta efficient capacity for observed 2024 CF diagnostics",
  blocks: "No longer blocks present-day technology/CHP utilization diagnostics",
});

const gapOutput = path.join(
  docsDir,
  "MEM_v2.9_EMPIRICAL_GAP_AND_ACQUISITION_REGISTER_UPDATED_20260901.csv",
);

const decisions = [
  {
    decision_id: "D-TS-001",
    status: "RESOLVED",
    decision: "Terna capacity controlling raw evidence",
    evidence_class: "DIRECT SOURCE CAPACITY / TERNA CAPACITY CONTROL",
    rule:
      "The manually downloaded official Terna XLSX is controlling; the API is a capacity cross-check, not a runtime dependency.",
    value_or_artifact: "Export-DownloadCenterFile-20260901-085433.xlsx",
    model_effect: "Closes the 2024 Terna capacity acquisition gate.",
  },
  {
    decision_id: "D-TS-002",
    status: "RESOLVED",
    decision: "Capacity basis",
    evidence_class: "DIRECT SOURCE CAPACITY",
    rule: "Filter Tipo capacità=Netta explicitly because the export contains both Lorda and Netta.",
    value_or_artifact: "capacity_basis=NET",
    model_effect: "All present thermal MW controls use net efficient capacity.",
  },
  {
    decision_id: "D-TS-003",
    status: "RESOLVED WITH SOURCE QA EXCEPTION",
    decision: "Repeated capacity source grains",
    evidence_class: "QA / RECONCILIATION ONLY",
    rule:
      "Aggregate the 19 exact repeated fuel-cell source groups at the canonical grain while retaining source row numbers and values; preserve the official national total and publish the 1.455 MW deduplicate-one-row sensitivity.",
    value_or_artifact: "578 canonical grains from 597 Netta rows",
    model_effect: "Unique canonical keys without silently deleting official MW.",
  },
  {
    decision_id: "D-TS-004",
    status: "RESOLVED",
    decision: "Province-to-zone mapping",
    evidence_class: "PYPSA IMPLEMENTATION MAPPING",
    rule: "Every 2024 province maps exactly once; CNOR is canonical and CNORD is ingestion-only.",
    value_or_artifact: "MEM_Province_Region_MarketZone_Crosswalk.csv",
    model_effect: "Authoritative seven-zone 2024 matrix available.",
  },
  {
    decision_id: "D-TS-005",
    status: "RESOLVED",
    decision: "Technology and fuel remain separate",
    evidence_class: "QA / RECONCILIATION ONLY",
    rule:
      "Ciclo combinato maps to CCGT-like and Turbine a gas to gas-turbine-like; neither mapping assigns natural gas without fuel evidence.",
    value_or_artifact: "41,739.119 MW CCGT-like; 3,619.37798 MW gas-turbine-like",
    model_effect: "Prevents false 48.02 GW fuel reconciliation.",
  },
  {
    decision_id: "D-TS-006",
    status: "RESOLVED",
    decision: "Production gross/net reconstruction",
    evidence_class: "PROJECT DERIVATION",
    rule:
      "For every exactly-two-row production key, higher=Lorda and lower=Netta; the XLSX is the controlling derivation source.",
    value_or_artifact: "491 unequal inferred pairs",
    model_effect: "Netta production can be paired with Netta capacity.",
  },
  {
    decision_id: "D-TS-007",
    status: "RESOLVED",
    decision: "Equal production pairs",
    evidence_class: "QA / RECONCILIATION ONLY",
    rule:
      "When the two values are equal, retain AMBIGUOUS_EQUAL and do not assign either raw row to Lorda or Netta; the same numeric value is valid for both fields.",
    value_or_artifact: "96 equal-value pairs",
    model_effect: "No invented raw-row ordering.",
  },
  {
    decision_id: "D-TS-008",
    status: "RESOLVED",
    decision: "Observed 2024 capacity factors",
    evidence_class: "QA / RECONCILIATION ONLY",
    rule:
      "Use Netta production / (Netta MW x 8.76) only where definitions align; treat outputs as present-day diagnostics.",
    value_or_artifact: "Terna_Thermoelectric_2024_Observed_Capacity_Factors.csv",
    model_effect:
      "Does not replace the approved 0.47 bioenergy or interim 0.20 future gas conversion assumptions and is not extrapolated to 2040/2050.",
  },
  {
    decision_id: "D-TS-009",
    status: "REQUIRES DATA",
    decision: "GEM August-2026 reconciliation",
    evidence_class: "GEM PLANT RECONCILIATION",
    rule:
      "GEM provides plant/unit identity, gross MW, status, fuel, CHP, coordinates, commissioning/retirement and material 2025-H1 2026 changes; it never overwrites Terna net MW.",
    value_or_artifact: "Complete original GOGPT August-2026 workbook required",
    model_effect: "Blocks final 2026 physical stack and future fleet bridge.",
  },
  {
    decision_id: "D-TS-010",
    status: "RUNTIME GATE",
    decision: "Slow programmable capacity calibration",
    evidence_class: "RUNTIME VALIDATION RESULT",
    rule:
      "Use an outer deterministic tested-fleet search after the physical baseline is frozen; do not make 18.9449 GW a solver equality.",
    value_or_artifact: "Acceptance metrics require user approval before execution",
    model_effect: "No capacity search is run in this phase.",
  },
  {
    decision_id: "D-TS-011",
    status: "RESOLVED",
    decision: "Internal network-loss accounting",
    evidence_class: "PROJECT DERIVATION",
    rule:
      "Because the DDS demand perimeter includes network losses, primary internal commercial Links use efficiency=1.0 unless demand is first stripped of source losses and losses are reconstructed endogenously.",
    value_or_artifact: "internal_link_efficiency_pu=1.0",
    model_effect: "Prevents double-counting network losses.",
  },
  {
    decision_id: "D-TS-012",
    status: "RESOLVED",
    decision: "Benchmark non-additivity",
    evidence_class: "QA / RECONCILIATION ONLY",
    rule:
      "The 2040 ~55 GW envelope includes all relevant thermoelectric categories; 15.5136 GW remains energy-implied diagnostic; ~30 GW remains independent adequacy benchmark; no 14.4864 GW residual; 18.9449 GW remains Slow comparison benchmark.",
    value_or_artifact: "Methodology guardrail",
    model_effect: "Prevents mechanical or double-counted thermal capacity insertion.",
  },
];

const dataStatus = [
  {
    artifact: "Terna_Thermoelectric_Capacity_2024_Canonical.csv",
    status: "ACQUIRED_VALIDATED_WITH_SOURCE_QA_EXCEPTION",
    role: "Canonical 2024 NET MW control",
    blocking_issue: "None for zonal technology baseline; 19 repeated source groups documented",
  },
  {
    artifact: "MEM_Province_Region_MarketZone_Crosswalk.csv",
    status: "ACQUIRED_VALIDATED",
    role: "2024 province-region-zone mapping",
    blocking_issue: "None for 2024 baseline",
  },
  {
    artifact: "Terna_Thermoelectric_Production_2024_Canonical.csv",
    status: "ACQUIRED_VALIDATED_WITH_DOCUMENTED_BASIS_INFERENCE",
    role: "Gross/net production pair table and CF input",
    blocking_issue: "No pair exceptions; equal pairs remain explicitly ambiguous/equal",
  },
  {
    artifact: "GEM_GOGPT_Aug2026_complete_release",
    status: "REQUIRES_USER_DOWNLOAD",
    role: "Plant/unit identity, gross MW, fuel, status and coordinates",
    blocking_issue: "Official form requires user identity/contact/use/license submission",
  },
  {
    artifact: "Terna_GEM_Thermal_Reconciliation.csv",
    status: "REQUIRES_GEM_DATA",
    role: "Hybrid unit/residual reconciliation",
    blocking_issue: "GEM August-2026 workbook absent",
  },
  {
    artifact: "MEM_Current_Thermal_Stack_2026.csv",
    status: "REQUIRES_GEM_DATA",
    role: "Proposed current physical stack",
    blocking_issue: "Cannot defensibly translate 2025-H1 2026 gross-MW changes into Terna-net capacity",
  },
  {
    artifact: "MEM_Thermal_Fleet_Evolution_2026_2040_2050.csv",
    status: "REQUIRES_GEM_AND_FUTURE_FLEET_EVIDENCE",
    role: "Plant/band actions and candidate future configurations",
    blocking_issue: "Physical unit/status bridge not closed",
  },
  {
    artifact: "v2.9.1 workbook successor",
    status: "NOT_CREATED_BY_DESIGN",
    role: "Targeted workbook empirical integration",
    blocking_issue: "Empirical physical stack is not genuinely closed until GEM reconciliation",
  },
];

const outputs = [
  await authorCsv(gaps, "GapRegister", gapOutput),
  await authorCsv(
    decisions,
    "DecisionRegister",
    path.join(docsDir, "MEM_v2.9_THERMAL_STACK_DECISION_REGISTER_20260901.csv"),
  ),
  await authorCsv(
    dataStatus,
    "DataStatus",
    path.join(phaseRoot, "DATA_STATUS.csv"),
  ),
];

await fs.writeFile(
  path.join(phaseRoot, "qa", "phase_registers_artifact_tool_verification.json"),
  `${JSON.stringify(outputs, null, 2)}\n`,
  "utf8",
);
console.log(JSON.stringify(outputs.map(({ path, rows, columns, sha256 }) => ({ path, rows, columns, sha256 })), null, 2));

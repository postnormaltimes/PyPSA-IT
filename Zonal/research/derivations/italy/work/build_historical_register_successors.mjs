import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { Workbook } from "@oai/artifact-tool";

const phaseRoot = path.resolve("..");
const docsDir = path.join(phaseRoot, "docs");
const hbRoot = path.join(phaseRoot, "historical_baseline");
const sha256File = async (file) => crypto.createHash("sha256").update(await fs.readFile(file)).digest("hex");

async function readCsv(file, sheetName) {
  const workbook = await Workbook.fromCSV(await fs.readFile(file, "utf8"), { sheetName });
  const values = workbook.worksheets.getItem(sheetName).getUsedRange().values;
  const headers = values[0].map(String);
  return values.slice(1).filter((row) => row.some((v) => v !== null && v !== "")).map((row) => Object.fromEntries(headers.map((header, index) => [header, row[index]])));
}

const sourceOld = await readCsv(path.join(phaseRoot, "SOURCE_MANIFEST.csv"), "SourceOld");
const sourceHist = await readCsv(path.join(hbRoot, "manifests", "MEM_HISTORICAL_BASELINE_SOURCE_MANIFEST.csv"), "SourceHist");
const sourceMap = new Map();
for (const row of [...sourceOld, ...sourceHist]) sourceMap.set(String(row.source_id), row);
const sourceSuccessor = [...sourceMap.values()];

const derivationOld = await readCsv(path.join(phaseRoot, "DERIVATION_MANIFEST.csv"), "DerOld");
const derivationHist = await readCsv(path.join(hbRoot, "manifests", "MEM_HISTORICAL_BASELINE_DERIVATION_MANIFEST.csv"), "DerHist");
const derivationMap = new Map();
for (const row of [...derivationOld, ...derivationHist]) derivationMap.set(String(row.derivation_id), row);
const derivationSuccessor = [...derivationMap.values()];

const statusOld = await readCsv(path.join(phaseRoot, "DATA_STATUS.csv"), "StatusOld");
const statusAdd = [
  { artifact: "Terna_Bioenergy_Capacity_2024_NET.csv", status: "ACQUIRED_VALIDATED", role: "2024 NET bioenergy source subset capacity by province/zone", blocking_issue: "Non-additive cross-classification; conversion technology/CHP allocation remains open" },
  { artifact: "Terna_Bioenergy_Production_2024_NET.csv", status: "ACQUIRED_VALIDATED", role: "2024 NET bioenergy source subset generation and observed CF", blocking_issue: "Observed CF is historical only; approved future 0.47 remains unchanged" },
  { artifact: "Terna_Geothermal_Capacity_2024_NET.csv", status: "ACQUIRED_VALIDATED", role: "2024 NET geothermal capacity by province/zone", blocking_issue: "None for 2024 technology total; 2019-2022 overlap with thermo label must remain year-specific" },
  { artifact: "Terna_Geothermal_Production_2024_NET.csv", status: "ACQUIRED_VALIDATED", role: "2024 NET geothermal generation and observed CF", blocking_issue: "Historical CF is diagnostic only" },
  { artifact: "Terna_Hydro_Capacity_2024_NET.csv", status: "ACQUIRED_VALIDATED", role: "2024 total NET hydro MW control", blocking_issue: "Type-specific MW and pumped-storage asset allocation unresolved" },
  { artifact: "Terna_Hydro_Production_2024_NET.csv", status: "ACQUIRED_VALIDATED_API_DIRECT_SOURCE", role: "2024 additive renewable-source NET hydro annual energy control", blocking_issue: "Detailed hydric type total has broader pumping-inclusive perimeter" },
  { artifact: "Terna_Hydro_2024_Zone_Type_Production.csv", status: "ACQUIRED_VALIDATED_NONADDITIVE_DETAIL", role: "2024 Fluente/Bacino/Serbatoio NET generation detail", blocking_issue: "Serbatoio includes eventual pumping; no type-specific MW" },
  { artifact: "Terna_Thermoelectric_Heat_2024_Canonical.csv", status: "ACQUIRED_VALIDATED", role: "2024 produced heat by CHP technology/province/zone", blocking_issue: "Final CHP representation requires user approval" },
  { artifact: "MEM_Historical_Capacity_By_Zone_Technology.csv", status: "PARTIAL_VALIDATED_2019_2024", role: "Machine-readable zonal historical capacity by detailed technology/source layer", blocking_issue: "Renewable API blanks retained; thermal fuel bridge, storage and hydro type MW unresolved" },
  { artifact: "MEM_Historical_Generation_By_Zone_Technology.csv", status: "PARTIAL_VALIDATED_2019_2024", role: "Machine-readable zonal historical NET generation, CF and CHP heat indicators", blocking_issue: "Eight 2019-2022 fuel-cell gross/net pair exceptions; pumped hydro physical split unresolved" },
  { artifact: "HISTORICAL_BASELINE_FREEZE_GATE", status: "HISTORICAL BASELINE INCOMPLETE", role: "Authorization gate for canonical workbook successor", blocking_issue: "Workbook successor not authorized" },
];
const statusMap = new Map(statusOld.map((row) => [String(row.artifact), row]));
for (const row of statusAdd) statusMap.set(row.artifact, row);
const statusSuccessor = [...statusMap.values()];

const gapOld = await readCsv(path.join(docsDir, "MEM_v2.9_EMPIRICAL_GAP_AND_ACQUISITION_REGISTER_UPDATED_20260901.csv"), "GapOld");
const gaps = [
  { gap_id: "GAP-HIST-001", status: "ACQUIRED / VALIDATED", artifact_or_decision: "2024 bioenergy capacity and generation", evidence_class: "DIRECT SOURCE CAPACITY / DIRECT SOURCE ENERGY / QA RECONCILIATION ONLY", authoritative_source: "Terna Download Center official XLSX exports", local_search_result: "Immutable originals archived and hashed; 107 explicit Netta province rows in each series", exact_acquisition_or_decision: "Use as a fuel/source subset of the thermoelectric population; never add to thermoelectric totals", required_fields_or_controls: "zone; NET MW; NET GWh; observed CF; source lineage", acceptance_test: "All provinces map exactly once; national 3,800.0923 MW and 15,699.033862 GWh", blocks: "No longer blocks 2024 bioenergy characterization; CHP/technology allocation remains open" },
  { gap_id: "GAP-HIST-002", status: "ACQUIRED / VALIDATED", artifact_or_decision: "2024 geothermal capacity and generation", evidence_class: "DIRECT SOURCE CAPACITY / DIRECT SOURCE ENERGY", authoritative_source: "Terna Download Center official XLSX exports", local_search_result: "Immutable originals archived and hashed; geothermal occurs only in CNOR/Tuscany", exact_acquisition_or_decision: "Represent geothermal as its own technology/carrier", required_fields_or_controls: "zone; NET MW; NET GWh; observed CF", acceptance_test: "National 771.79 MW and 5,275.5733 GWh; CF 0.780308627", blocks: "No longer blocks 2024 geothermal baseline" },
  { gap_id: "GAP-HIST-003", status: "ENERGY PERIMETER RESOLVED / PHYSICAL STORAGE REQUIRES DATA", artifact_or_decision: "Hydro renewable/source versus hydric type/pumping perimeter", evidence_class: "DIRECT SOURCE ENERGY + PROJECT DERIVATION", authoritative_source: "Terna renewable-source production API; Terna Download Center hydric type export", local_search_result: "2024 renewable-source NET hydro 52,391.704319 GWh; hydric type total 53,975.509902 GWh", exact_acquisition_or_decision: "Use renewable-source hydro as additive annual control; keep hydric types non-additive until pumping split", required_fields_or_controls: "pumped discharge MW; charge MW; energy MWh; efficiency; physical asset/type allocation", acceptance_test: "National difference 1,583.805583 GWh retained as statistical pumping-discharge proxy; no zonal allocation inferred", blocks: "Pumped-hydro physical model inputs and hydro type-specific capacity" },
  { gap_id: "GAP-HIST-004", status: "ACQUIRED / VALIDATED", artifact_or_decision: "2024 CHP produced heat and electricity indicators", evidence_class: "DIRECT SOURCE ENERGY / PROJECT DERIVATION", authoritative_source: "Terna Download Center heat export plus canonical thermoelectric capacity/electricity", local_search_result: "Six CHP technologies mapped; 51,720.766462 GWh produced heat", exact_acquisition_or_decision: "Preserve CHP technologies separately; defer final PyPSA representation", required_fields_or_controls: "capacity; NET electricity; heat; electric CF; electricity/heat ratio", acceptance_test: "No output-with-zero-capacity anomalies; all six national indicators calculated", blocks: "Final CHP modelling choice only" },
  { gap_id: "GAP-HIST-005", status: "PARTIAL / VALIDATED 2019-2024", artifact_or_decision: "Historical zonal capacity and generation series", evidence_class: "DIRECT SOURCE CAPACITY / DIRECT SOURCE ENERGY", authoritative_source: "Terna official API and controlling 2024 Download Center exports", local_search_result: "Six endpoint families archived for 2019-2025; all 2025 responses returned zero rows", exact_acquisition_or_decision: "Use actual 2019-2024 observations; do not backcast 2024 shares", required_fields_or_controls: "year; zone; detailed technology; fuel/source; CHP; NET MW/GWh; CF; provenance", acceptance_test: "674 capacity rows; 805 generation rows; seven zones only; 2024 manual/API controls reconcile", blocks: "Historical baseline freeze remains open" },
  { gap_id: "GAP-HIST-006", status: "REQUIRES EXCEPTION RESOLUTION", artifact_or_decision: "2019-2022 fuel-cell CHP thermoelectric production pairs", evidence_class: "QA / RECONCILIATION ONLY", authoritative_source: "Terna thermoelectric-production API or replacement Download Center extracts", local_search_result: "Two visible keys per year contain four values rather than two; eight groups total", exact_acquisition_or_decision: "Retain blank NET generation/CF for these keys; do not infer hidden pairings", required_fields_or_controls: "explicit production basis or hidden unit discriminator", acceptance_test: "Each exception resolved to explicit Netta/Lorda pairs without guessing", blocks: "Microscopic completeness of 2019-2022 CHP fuel-cell history" },
  { gap_id: "GAP-HIST-007", status: "REQUIRES DATA", artifact_or_decision: "Pumped hydro and BESS/other storage historical physical parameters", evidence_class: "DIRECT SOURCE CAPACITY / PYPSA IMPLEMENTATION MAPPING", authoritative_source: "Terna storage statistics plus reconciled physical asset data", local_search_result: "Terna total hydro MW and pumping-inclusive energy perimeter available; storage power/energy/efficiency absent", exact_acquisition_or_decision: "Acquire discharge/charge MW, energy MWh, efficiency and commissioning/status by zone", required_fields_or_controls: "technology; zone; power_MW; energy_MWh; efficiency; source; year", acceptance_test: "No installed MW double counted between generic hydro and pumped storage", blocks: "Historical baseline freeze and storage layer" },
  { gap_id: "GAP-HIST-008", status: "REQUIRES ALLOCATION", artifact_or_decision: "Wind onshore/offshore split", evidence_class: "DIRECT SOURCE CAPACITY / DIRECT SOURCE ENERGY", authoritative_source: "Terna source series plus authoritative plant/project layer", local_search_result: "Terna Eolico aggregate acquired for 2019-2024; no onshore/offshore discriminator", exact_acquisition_or_decision: "Reconcile any offshore units/projects separately and preserve aggregate Terna total", required_fields_or_controls: "zone; onshore/offshore; NET MW; NET GWh; source", acceptance_test: "Onshore+offshore reconciles to Terna Eolico by zone/year", blocks: "Detailed wind taxonomy freeze" },
];
const gapSuccessor = [...gapOld, ...gaps];

const decisionOld = await readCsv(path.join(docsDir, "MEM_v2.9_THERMAL_STACK_DECISION_REGISTER_20260901.csv"), "DecisionOld");
const decisions = [
  { decision_id: "D-HIST-001", status: "RESOLVED", decision: "2024 source-specific Terna raw evidence", evidence_class: "DIRECT SOURCE CAPACITY / DIRECT SOURCE ENERGY", rule: "Preserve exact Download Center binaries and hashes; use API only where a separate required 2024 source series was not supplied or for historical extension/cross-check.", value_or_artifact: "raw/terna/historical_2024; raw/terna/historical_2019_2025_api", model_effect: "Reproducible immutable evidence layer; no runtime Terna dependency." },
  { decision_id: "D-HIST-002", status: "RESOLVED", decision: "Bioenergy overlap", evidence_class: "QA / RECONCILIATION ONLY", rule: "Bioenergy capacity and generation are a source/fuel cross-classification within thermoelectric totals and are never additive to the thermoelectric population.", value_or_artifact: "3,800.0923 MW; 15,699.033862 GWh in 2024", model_effect: "Prevents double counting while preserving bioenergy evidence." },
  { decision_id: "D-HIST-003", status: "RESOLVED YEAR-SPECIFIC", decision: "Geothermal thermoelectric perimeter drift", evidence_class: "DIRECT SOURCE CAPACITY / QA RECONCILIATION ONLY", rule: "2019-2022 explicit thermoelectric geothermal capacity equals the renewable-source geothermal control and is non-additive there; 2023-2024 the label disappears, so source-specific geothermal is added outside the thermoelectric extract.", value_or_artifact: "767.19 MW in 2019; 771.79 MW in 2020-2024", model_effect: "Creates a year-aware non-double-counting bridge and supports the 2024 DDS-comparable 61,103.61927 MW current perimeter." },
  { decision_id: "D-HIST-004", status: "RESOLVED", decision: "Hydro annual energy control", evidence_class: "DIRECT SOURCE ENERGY", rule: "Use renewable-source Idrico NET generation as the additive annual hydro control; type-level Fluente/Bacino/Serbatoio rows are non-additive analytical detail until pumping is separated.", value_or_artifact: "52,391.704319 GWh additive 2024 hydro", model_effect: "Avoids adding pumping discharge to renewable hydro without modelling charging." },
  { decision_id: "D-HIST-005", status: "RESOLVED AS QA PROXY", decision: "Pumping energy perimeter", evidence_class: "PROJECT DERIVATION / QA RECONCILIATION ONLY", rule: "Hydric-type total minus renewable-source hydro is a national pumping-discharge perimeter proxy; signed zonal differences are QA only and not a capacity or generation allocation.", value_or_artifact: "1,583.805583 GWh national 2024 proxy", model_effect: "Keeps pumped storage separate and prevents zonal invented precision." },
  { decision_id: "D-HIST-006", status: "INTERIM RECOMMENDATION / USER APPROVAL REQUIRED", decision: "CHP representation", evidence_class: "PROJECT DERIVATION", rule: "Preserve CHP technologies separately; initial MEM implementation should be electricity-only CHP-specific generator bands with heat evidence retained for calibration, not a generic merged CHP bucket or heat co-optimization by default.", value_or_artifact: "MEM_CHP_REPRESENTATION_ASSESSMENT.md", model_effect: "Maintains economically meaningful CHP/non-CHP dispatch differences without prematurely expanding model scope." },
  { decision_id: "D-HIST-007", status: "RESOLVED", decision: "Historical time window", evidence_class: "DIRECT SOURCE CAPACITY / DIRECT SOURCE ENERGY", rule: "Use actual 2019-2024 Terna observations. Do not backcast 2024 shares. All six 2025 API endpoints returned zero rows on 2026-09-01.", value_or_artifact: "MEM_Historical_Capacity_By_Zone_Technology.csv; MEM_Historical_Generation_By_Zone_Technology.csv", model_effect: "Stable actual-history anchor with explicit 2025 unavailability." },
  { decision_id: "D-HIST-008", status: "RESOLVED", decision: "Canonical workbook promotion gate", evidence_class: "QA / RECONCILIATION ONLY", rule: "Do not create v2.9.1 until the completion matrix is explicitly HISTORICAL BASELINE FROZEN.", value_or_artifact: "Current status: HISTORICAL BASELINE INCOMPLETE", model_effect: "v2.9 workbook remains unchanged; no piecemeal historical update." },
];
const decisionSuccessor = [...decisionOld, ...decisions];

const csvEscape = (value) => {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
};
const makeMatrix = (rows) => {
  const headers = [];
  for (const row of rows) for (const key of Object.keys(row)) if (!headers.includes(key)) headers.push(key);
  return { headers, values: rows.map((row) => headers.map((header) => row[header] ?? "")) };
};
const letters = (count) => {
  let n = count;
  let result = "";
  while (n > 0) { n -= 1; result = String.fromCharCode(65 + (n % 26)) + result; n = Math.floor(n / 26); }
  return result;
};
async function author(rows, sheetName, file) {
  const { headers, values } = makeMatrix(rows);
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add(sheetName);
  sheet.getRangeByIndexes(0, 0, rows.length + 1, headers.length).values = [headers, ...values];
  const address = `A1:${letters(headers.length)}${rows.length + 1}`;
  await workbook.inspect({ kind: "table", sheetId: sheetName, range: address, tableMaxRows: 3, tableMaxCols: Math.min(24, headers.length), maxChars: 5000 });
  await fs.writeFile(file, `${[headers, ...values].map((row) => row.map(csvEscape).join(",")).join("\r\n")}\r\n`, "utf8");
  const reopened = await Workbook.fromCSV(await fs.readFile(file, "utf8"), { sheetName });
  await reopened.inspect({ kind: "table", sheetId: sheetName, range: address, tableMaxRows: 3, tableMaxCols: Math.min(24, headers.length), maxChars: 5000 });
  return { file: path.relative(phaseRoot, file).replaceAll("\\", "/"), rows: rows.length, columns: headers.length, sha256: await sha256File(file) };
}

const outputs = [];
outputs.push(await author(sourceSuccessor, "SourceManifest", path.join(phaseRoot, "SOURCE_MANIFEST_HISTORICAL_BASELINE_UPDATED_20260901.csv")));
outputs.push(await author(derivationSuccessor, "DerivationManifest", path.join(phaseRoot, "DERIVATION_MANIFEST_HISTORICAL_BASELINE_UPDATED_20260901.csv")));
outputs.push(await author(statusSuccessor, "DataStatus", path.join(phaseRoot, "DATA_STATUS_HISTORICAL_BASELINE_UPDATED_20260901.csv")));
outputs.push(await author(gapSuccessor, "GapRegister", path.join(docsDir, "MEM_v2.9_EMPIRICAL_GAP_AND_ACQUISITION_REGISTER_HISTORICAL_BASELINE_UPDATED_20260901.csv")));
outputs.push(await author(decisionSuccessor, "DecisionRegister", path.join(docsDir, "MEM_v2.9_HISTORICAL_BASELINE_DECISION_REGISTER_20260901.csv")));
await fs.writeFile(path.join(hbRoot, "qa", "HISTORICAL_REGISTER_SUCCESSOR_BUILD.json"), `${JSON.stringify({ outputs }, null, 2)}\n`, "utf8");
console.log(JSON.stringify({ outputs }, null, 2));

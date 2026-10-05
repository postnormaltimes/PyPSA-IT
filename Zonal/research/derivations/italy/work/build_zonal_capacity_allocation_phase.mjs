import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { Workbook, SpreadsheetFile, FileBlob } from "@oai/artifact-tool";

const phaseRoot = path.resolve(import.meta.dirname, "..");
const projectOutputRoot = path.resolve(phaseRoot, "..");
const scenarioDir = path.join(phaseRoot, "scenario_capacity");
const renderDir = path.join(scenarioDir, "renders");
const docsDir = path.join(phaseRoot, "docs");
const qaDir = path.join(phaseRoot, "qa");
const analysisDir = path.join(phaseRoot, "historical_baseline", "analysis");
const normalizedDir = path.join(phaseRoot, "historical_baseline", "normalized");

await fs.mkdir(scenarioDir, { recursive: true });
await fs.mkdir(renderDir, { recursive: true });

const files = {
  historicalCapacity: path.join(normalizedDir, "MEM_Historical_Capacity_By_Zone_Technology.csv"),
  historicalGeneration: path.join(normalizedDir, "MEM_Historical_Generation_By_Zone_Technology.csv"),
  historicalStorage: path.join(normalizedDir, "MEM_Historical_Storage_By_Zone_Technology.csv"),
  ternaCapacity: path.join(phaseRoot, "normalized", "Terna_Thermoelectric_Capacity_2024_Canonical.csv"),
  pumping: path.join(analysisDir, "MEM_Hydro_Pumping_Perimeter_Reconciliation.csv"),
  hydroPhysicalZone: path.join(analysisDir, "MEM_Hydro_Physical_Inventory_Zone_Summary.csv"),
  sourceManifest: path.join(phaseRoot, "SOURCE_MANIFEST.csv"),
  derivationManifest: path.join(phaseRoot, "DERIVATION_MANIFEST.csv"),
  decisionRegister: path.join(docsDir, "MEM_v2.9_THERMAL_STACK_DECISION_REGISTER_20260901.csv"),
  gapRegister: path.join(docsDir, "MEM_v2.9_EMPIRICAL_GAP_AND_ACQUISITION_REGISTER_UPDATED_20260901.csv"),
  phase3Gates: path.join(analysisDir, "MEM_PHASE3_GATE_STATUS.csv"),
  v29: path.join(projectOutputRoot, "Fase_5_PyPSA_Eur_Italia_2040_2050_Model_Ready_v2.9_MEM_ARCHITECTURE_REFINED.xlsx"),
  terna2050: path.resolve(phaseRoot, "..", "..", "..", "03_PRIMARY_SOURCES", "Terna_Prospettive_Sviluppo_Sistema_Energetico_2050.pdf"),
  terna2024: path.join(phaseRoot, "raw", "official_reports", "Terna_2024_Impianti_di_generazione_RAW.pdf"),
};

const out = {
  removalMask: path.join(scenarioDir, "MEM_2024_Coal_Oil_Removal_Mask.csv"),
  removalReconciliation: path.join(scenarioDir, "MEM_2024_Coal_Oil_Removal_Reconciliation.csv"),
  anchor: path.join(scenarioDir, "MEM_2024_Post_Coal_Oil_Thermal_Geography_Anchor.csv"),
  taxonomy: path.join(scenarioDir, "MEM_Future_Dispatchable_Technology_Taxonomy.csv"),
  crosswalk: path.join(scenarioDir, "MEM_Future_Technology_Zonal_Allocation_Crosswalk.csv"),
  hydro: path.join(scenarioDir, "MEM_Current_Hydro_Capacity_By_Zone_Class_For_Future_Scenarios.csv"),
  base2040: path.join(scenarioDir, "MEM_2040_Base_High_Thermal_Zonal_Capacity.csv"),
  perimeter2050: path.join(scenarioDir, "MEM_2050_Programmable_Perimeter_Reconciliation.csv"),
  national2050: path.join(scenarioDir, "MEM_2050_Base_High_National_Dispatchable_Capacity.csv"),
  zonal2050: path.join(scenarioDir, "MEM_2050_Base_High_Dispatchable_Zonal_Capacity.csv"),
  cdp: path.join(scenarioDir, "MEM_Slow_CDP_Dispatchable_Capacity_Adjustment.csv"),
  slow2040National: path.join(scenarioDir, "MEM_2040_Slow_Thermal_National_Capacity.csv"),
  slow2040Zonal: path.join(scenarioDir, "MEM_2040_Slow_Thermal_Zonal_Capacity.csv"),
  slow2050National: path.join(scenarioDir, "MEM_2050_Slow_Dispatchable_National_Capacity.csv"),
  slow2050Zonal: path.join(scenarioDir, "MEM_2050_Slow_Dispatchable_Zonal_Capacity.csv"),
  long: path.join(scenarioDir, "MEM_2040_2050_Zonal_Installed_Capacity_By_Scenario.csv"),
  workbook: path.join(scenarioDir, "MEM_2040_2050_Zonal_Installed_Capacity_By_Scenario.xlsx"),
  methodology: path.join(docsDir, "MEM_Zonal_Capacity_Allocation_Methodology.md"),
  qaCsv: path.join(qaDir, "MEM_Zonal_Capacity_Allocation_QA.csv"),
  gateStatus: path.join(scenarioDir, "MEM_ZONAL_CAPACITY_PHASE_GATE_STATUS.csv"),
  verification: path.join(qaDir, "MEM_ZONAL_CAPACITY_FINAL_VERIFICATION.json"),
};

const ZONES = ["NORD", "CNOR", "CSUD", "SUD", "CALA", "SICI", "SARD"];
const SCENARIOS = [
  { year: 2040, scenario: "Slow" },
  { year: 2040, scenario: "Base" },
  { year: 2040, scenario: "High" },
  { year: 2050, scenario: "Slow" },
  { year: 2050, scenario: "Base" },
  { year: 2050, scenario: "High" },
];
const THERMAL_TECHS = [
  "CCGT_NON_CHP", "CCGT_CHP", "GT_NON_CHP", "GT_CHP",
  "INTERNAL_COMBUSTION_NON_CHP", "INTERNAL_COMBUSTION_CHP",
  "STEAM_CONDENSING", "STEAM_EXTRACTION_CHP", "STEAM_BACKPRESSURE_CHP",
  "FUEL_CELL_NON_CHP", "FUEL_CELL_CHP", "EXTERNAL_COMBUSTION",
  "TURBO_EXPANSION", "OTHER_THERMAL",
];

const round = (value, digits = 9) => Number(Number(value).toFixed(digits));
const sum = (values) => values.reduce((acc, value) => acc + Number(value || 0), 0);
const almost = (a, b, tol = 1e-6) => Math.abs(Number(a) - Number(b)) <= tol;
const sha256File = async (file) => crypto.createHash("sha256").update(await fs.readFile(file)).digest("hex");
const fileSize = async (file) => (await fs.stat(file)).size;
const csvEscape = (value) => {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
};
const letters = (count) => {
  let n = count;
  let out = "";
  while (n > 0) {
    n -= 1;
    out = String.fromCharCode(65 + (n % 26)) + out;
    n = Math.floor(n / 26);
  }
  return out;
};

async function readCsvObjects(file, sheetName) {
  const workbook = await Workbook.fromCSV(await fs.readFile(file, "utf8"), { sheetName });
  const values = workbook.worksheets.getItem(sheetName).getUsedRange().values;
  const headers = values[0].map(String);
  return values.slice(1)
    .filter((row) => row.some((value) => value !== null && value !== ""))
    .map((row) => Object.fromEntries(headers.map((header, index) => [header, row[index] ?? ""])));
}

function matrix(rows) {
  const headers = [];
  for (const row of rows) for (const key of Object.keys(row)) if (!headers.includes(key)) headers.push(key);
  return { headers, values: rows.map((row) => headers.map((header) => row[header] ?? "")) };
}

async function authorCsv(rows, sheetName, file) {
  const { headers, values } = matrix(rows);
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add(sheetName);
  sheet.getRangeByIndexes(0, 0, rows.length + 1, headers.length).values = [headers, ...values];
  const address = `A1:${letters(headers.length)}${rows.length + 1}`;
  await workbook.inspect({ kind: "table", sheetId: sheetName, range: address, tableMaxRows: 3, tableMaxCols: Math.min(headers.length, 20), maxChars: 5000 });
  const csv = `${[headers, ...values].map((row) => row.map(csvEscape).join(",")).join("\r\n")}\r\n`;
  await fs.writeFile(file, csv, "utf8");
  const reopened = await Workbook.fromCSV(await fs.readFile(file, "utf8"), { sheetName });
  await reopened.inspect({ kind: "table", sheetId: sheetName, range: address, tableMaxRows: 3, tableMaxCols: Math.min(headers.length, 20), maxChars: 5000 });
  return { file: path.relative(phaseRoot, file).replaceAll("\\", "/"), rows: rows.length, columns: headers.length, sha256: await sha256File(file) };
}

function upsert(rows, keyField, row) {
  const index = rows.findIndex((item) => String(item[keyField]) === String(row[keyField]));
  if (index >= 0) rows[index] = { ...rows[index], ...row };
  else rows.push(row);
}

function groupSum(rows, keyFn, valueFn) {
  const map = new Map();
  for (const row of rows) {
    const key = keyFn(row);
    map.set(key, (map.get(key) || 0) + Number(valueFn(row) || 0));
  }
  return map;
}

// ---------------------------------------------------------------------------
// Frozen Phase-3.1 controls and source-backed scenario inputs
// ---------------------------------------------------------------------------

const historicalCapacity = await readCsvObjects(files.historicalCapacity, "HistoricalCapacity");
const historicalGeneration = await readCsvObjects(files.historicalGeneration, "HistoricalGeneration");
const historicalStorage = await readCsvObjects(files.historicalStorage, "HistoricalStorage");
const ternaCapacity = await readCsvObjects(files.ternaCapacity, "TernaCapacity");
const pumpingRows = await readCsvObjects(files.pumping, "Pumping");
const hydroPhysicalZone = await readCsvObjects(files.hydroPhysicalZone, "HydroPhysicalZone");

if (historicalCapacity.length !== 758) throw new Error(`Frozen capacity row count changed: ${historicalCapacity.length}`);
if (historicalGeneration.length !== 881) throw new Error(`Frozen generation row count changed: ${historicalGeneration.length}`);
if (historicalStorage.length !== 19) throw new Error(`Frozen storage row count changed: ${historicalStorage.length}`);

const v29Workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(files.v29));
const genValues = v29Workbook.worksheets.getItem("04_GENERATION_BY_ZONE").getUsedRange().values;
const storageValues = v29Workbook.worksheets.getItem("05_STORAGE_BY_ZONE").getUsedRange().values;

const generationScenario = new Map();
const genHeaderIndex = genValues.findIndex((row) => row[0] === "Zone" && row[1] === "Technology");
if (genHeaderIndex < 0) throw new Error("Generation scenario header not found in v2.9.");
const genHeaders = genValues[genHeaderIndex].map((value) => String(value ?? ""));
const genColumn = Object.fromEntries(genHeaders.map((header, index) => [header, index]));
const scenarioColumnNames = {
  "2040|Slow": "2040 Slow GW", "2040|Base": "2040 Base GW", "2040|High": "2040 High GW",
  "2050|Slow": "2050 Slow GW", "2050|Base": "2050 Base GW", "2050|High": "2050 High GW",
};
const genTechMap = {
  "Solar rooftop / distributed": "SOLAR_PV_ROOFTOP",
  "Solar utility": "SOLAR_PV_UTILITY",
  "Wind onshore": "WIND_ONSHORE",
  "Wind offshore": "WIND_OFFSHORE",
};
for (const row of genValues.slice(genHeaderIndex + 1)) {
  if (!ZONES.includes(String(row[0])) || !genTechMap[row[1]]) continue;
  for (const [scenarioKey, columnName] of Object.entries(scenarioColumnNames)) {
    generationScenario.set(`${scenarioKey}|${row[0]}|${genTechMap[row[1]]}`, Number(row[genColumn[columnName]]) * 1000);
  }
}

const bessScenario = new Map();
let storageYear = null;
let storageHeaders = null;
for (const row of storageValues) {
  if (String(row[0] ?? "").includes("2040 battery portfolio")) storageYear = 2040;
  if (String(row[0] ?? "").includes("2050 battery portfolio")) storageYear = 2050;
  if (row[0] === "Zone" && row[1] === "Storage class") {
    storageHeaders = Object.fromEntries(row.map((value, index) => [String(value ?? ""), index]));
    continue;
  }
  if (!storageYear || !storageHeaders || !ZONES.includes(String(row[0]))) continue;
  for (const scenario of ["Slow", "Base", "High"]) {
    const col = storageHeaders[`${scenario} discharge GW`];
    const key = `${storageYear}|${scenario}|${row[0]}`;
    bessScenario.set(key, (bessScenario.get(key) || 0) + Number(row[col] || 0) * 1000);
  }
}

const baseResourceCapacityGW = {};
for (const year of [2040, 2050]) {
  const pvMW = sum(ZONES.flatMap((zone) => ["SOLAR_PV_ROOFTOP", "SOLAR_PV_UTILITY"].map((tech) => generationScenario.get(`${year}|Base|${zone}|${tech}`))));
  const windMW = sum(ZONES.flatMap((zone) => ["WIND_ONSHORE", "WIND_OFFSHORE"].map((tech) => generationScenario.get(`${year}|Base|${zone}|${tech}`))));
  const bessMW = sum(ZONES.map((zone) => bessScenario.get(`${year}|Base|${zone}`)));
  baseResourceCapacityGW[year] = { PV: pvMW / 1000, WIND: windMW / 1000, BESS: bessMW / 1000 };
}
if (!almost(baseResourceCapacityGW[2040].PV, 120.9, 1e-7) || !almost(baseResourceCapacityGW[2040].WIND, 49.3, 1e-7) || !almost(baseResourceCapacityGW[2040].BESS, 27.2666666667, 1e-7)) throw new Error("2040 frozen resource controls failed.");
if (!almost(baseResourceCapacityGW[2050].PV, 245, 1e-7) || !almost(baseResourceCapacityGW[2050].WIND, 51, 1e-7) || !almost(baseResourceCapacityGW[2050].BESS, 43.6530612245, 1e-7)) throw new Error("2050 frozen resource controls failed.");

const capacity2024 = historicalCapacity.filter((row) => String(row.year) === "2024" && ZONES.includes(String(row.market_zone)));
const historicalTechCapacity = groupSum(
  capacity2024.filter((row) => THERMAL_TECHS.includes(String(row.technology))),
  (row) => `${row.market_zone}|${row.technology}`,
  (row) => row.capacity_NET_MW,
);
const thermoTotal2024 = sum([...historicalTechCapacity.values()]);
if (!almost(thermoTotal2024, 60330.37427, 1e-6)) throw new Error(`Unexpected 2024 thermoelectric total ${thermoTotal2024}`);

const geothermalByZone = new Map(ZONES.map((zone) => [zone, sum(capacity2024.filter((row) => row.market_zone === zone && row.technology === "GEOTHERMAL").map((row) => row.capacity_NET_MW))]));
const geothermalTotal = sum([...geothermalByZone.values()]);
if (!almost(geothermalTotal, 771.79, 1e-6)) throw new Error(`Unexpected geothermal total ${geothermalTotal}`);

const bioByZone = new Map(ZONES.map((zone) => [zone, sum(capacity2024.filter((row) => row.market_zone === zone && row.technology === "BIOENERGY").map((row) => row.capacity_NET_MW))]));
const bioRenewableTotal = sum([...bioByZone.values()]);
if (!almost(bioRenewableTotal, 3800.0923, 1e-6)) throw new Error(`Unexpected renewable-source bioenergy total ${bioRenewableTotal}`);

// ---------------------------------------------------------------------------
// Targeted 2024 coal/oil physical mask + explicit control residuals
// ---------------------------------------------------------------------------

const COAL_CONTROL_MW = 4917.4;
const OIL_CONTROL_MW = 1975.9;
const removalMask = [];
const pushRemoval = (row) => removalMask.push({
  record_id: row.record_id,
  plant: row.plant,
  unit: row.unit ?? "PLANT/PROVINCE AGGREGATE",
  zone: row.zone,
  fuel: row.fuel,
  conversion_technology: row.conversion_technology,
  capacity_MW_source: round(row.capacity_MW_source, 6),
  capacity_basis: row.capacity_basis,
  candidate_NET_MW: round(row.candidate_NET_MW, 9),
  source: row.source,
  source_date: row.source_date,
  "2024_operational_status": row.operational_status,
  confidence: row.confidence,
  Terna_control_role: row.Terna_control_role,
  removal_status: row.removal_status,
  physical_attribution_status: row.physical_attribution_status,
  historical_conversion_row_capacity_MW: round(historicalTechCapacity.get(`${row.zone}|${row.conversion_technology}`) || 0, 9),
  notes: row.notes,
});

const coalDirect = [
  { id: "COAL_TVN", plant: "Torrevaldaliga Nord", zone: "CSUD", province: "Roma", mw: 1845, source: "TERNA_DOWNLOAD_CENTER_THERMOELECTRIC_CAPACITY_2024|ENEL_ESG_FOCUS_MAY2024", note: "Terna 2024 Roma Condensazione NET row; Enel identifies Torrevaldaliga Nord as the 1.8-GW coal site." },
  { id: "COAL_FEDERICO_II", plant: "Federico II (Brindisi Sud)", zone: "SUD", province: "Brindisi", mw: 1835.1, source: "TERNA_DOWNLOAD_CENTER_THERMOELECTRIC_CAPACITY_2024|ENEL_ESG_FOCUS_MAY2024", note: "Terna 2024 Brindisi Condensazione NET row; Enel identifies Federico II as the 1.8-GW coal site." },
  { id: "COAL_FIUME_SANTO", plant: "Fiume Santo", zone: "SARD", province: "Sassari", mw: 598.52, source: "TERNA_DOWNLOAD_CENTER_THERMOELECTRIC_CAPACITY_2024|EP_PRODUZIONE_SUSTAINABILITY_2024", note: "Terna 2024 Sassari Condensazione NET row; operator reports 599 MW net and two coal units." },
  { id: "COAL_SULCIS", plant: "Grazia Deledda / Sulcis", zone: "SARD", province: "Sud Sardegna", mw: 480, source: "TERNA_DOWNLOAD_CENTER_THERMOELECTRIC_CAPACITY_2024|ENEL_ESG_FOCUS_MAY2024", note: "Terna 2024 Sud Sardegna Condensazione NET row; Enel identifies Sulcis as the 0.5-GW coal site." },
];
for (const item of coalDirect) pushRemoval({
  record_id: item.id, plant: item.plant, unit: `${item.province} province Condensazione aggregate`, zone: item.zone, fuel: "COAL",
  conversion_technology: "STEAM_CONDENSING", capacity_MW_source: item.mw, capacity_basis: "TERNA NET EFFICIENT",
  candidate_NET_MW: item.mw, source: item.source, source_date: "2024/2025 evidence",
  operational_status: "INSTALLED IN TERNA 2024 CONTROL; plant phase-out evidence retained",
  confidence: "HIGH", Terna_control_role: "DIRECT PROVINCE×TECHNOLOGY CONTRIBUTION TO NATIONAL FUEL CONTROL",
  removal_status: "DIRECT_HIGH_CONFIDENCE", physical_attribution_status: "PLANT/PROVINCE RESOLVED", notes: item.note,
});

const coalDirectMW = sum(coalDirect.map((row) => row.mw));
const coalResidualMW = round(COAL_CONTROL_MW - coalDirectMW, 9);
pushRemoval({
  record_id: "COAL_TERNA_CONTROL_RESIDUAL_NORD", plant: "TERNA coal control residual band", unit: "UNRESOLVED NORD steam-condensing attribution",
  zone: "NORD", fuel: "COAL", conversion_technology: "STEAM_CONDENSING", capacity_MW_source: coalResidualMW,
  capacity_basis: "TERNA NET CONTROL RESIDUAL", candidate_NET_MW: coalResidualMW,
  source: "TERNA_2024_IMPIANTI_GENERAZIONE_TABLE19|A2A_MONFALCONE_AND_ENEL_FUSINA_STATUS_EVIDENCE", source_date: "2024/2025",
  operational_status: "PHYSICAL UNIT UNRESOLVED; former NORD coal sites documented, but direct 2024 attribution not claimed",
  confidence: "LOW PLANT; HIGH NATIONAL CONTROL", Terna_control_role: "NATIONAL CONTROL RESIDUAL",
  removal_status: "TERNA_CONTROL_RESIDUAL", physical_attribution_status: "CONTROLLED RESIDUAL; NOT A PLANT RECORD",
  notes: "Residual is assigned to NORD steam-condensing only for zonal phase-out accounting. Monfalcone and Fusina are not asserted as 2024 direct capacity rows; operator evidence says their coal service ended in 2023. This 158.78-MW band remains a refinement target.",
});

pushRemoval({
  record_id: "OIL_SAN_FILIPPO_DEL_MELA", plant: "San Filippo del Mela", unit: "Messina province Condensazione aggregate",
  zone: "SICI", fuel: "PETROLEUM_PRODUCTS", conversion_technology: "STEAM_CONDENSING", capacity_MW_source: 886,
  capacity_basis: "TERNA NET EFFICIENT", candidate_NET_MW: 886,
  source: "TERNA_DOWNLOAD_CENTER_THERMOELECTRIC_CAPACITY_2024|A2A_SAN_FILIPPO_PLANT_PAGE|A2A_2024_MANAGEMENT_REPORT", source_date: "2024/2025",
  operational_status: "ESSENTIAL OIL-FIRED PLANT IN 2024",
  confidence: "HIGH", Terna_control_role: "DIRECT PROVINCE×TECHNOLOGY CONTRIBUTION TO NATIONAL FUEL CONTROL",
  removal_status: "DIRECT_HIGH_CONFIDENCE", physical_attribution_status: "PLANT/PROVINCE RESOLVED",
  notes: "A2A identifies oil fuel and 960 MW nominal installed capacity. MEM uses the Terna 2024 Messina Condensazione 886-MW NET row as the capacity anchor.",
});

const oilDirectMW = 886;
const oilResidualMW = round(OIL_CONTROL_MW - oilDirectMW, 9);
const oilEligibleTechs = new Set([
  "GT_NON_CHP", "GT_CHP", "INTERNAL_COMBUSTION_NON_CHP", "INTERNAL_COMBUSTION_CHP",
  "STEAM_CONDENSING", "STEAM_EXTRACTION_CHP", "STEAM_BACKPRESSURE_CHP", "OTHER_THERMAL",
]);
const directRemovalByZoneTech = groupSum(removalMask, (row) => `${row.zone}|${row.conversion_technology}`, (row) => row.candidate_NET_MW);
const oilResidualSeed = [];
for (const zone of ZONES) {
  for (const technology of oilEligibleTechs) {
    const observed = historicalTechCapacity.get(`${zone}|${technology}`) || 0;
    const alreadyRemoved = directRemovalByZoneTech.get(`${zone}|${technology}`) || 0;
    const eligible = Math.max(0, observed - alreadyRemoved);
    if (eligible > 0) oilResidualSeed.push({ zone, technology, eligible });
  }
}
const oilResidualSeedTotal = sum(oilResidualSeed.map((row) => row.eligible));
for (const row of oilResidualSeed) {
  const allocated = oilResidualMW * row.eligible / oilResidualSeedTotal;
  pushRemoval({
    record_id: `OIL_TERNA_RESIDUAL_${row.zone}_${row.technology}`, plant: "TERNA petroleum-products control residual band",
    unit: "UNRESOLVED dispersed/multi-fuel section band", zone: row.zone, fuel: "PETROLEUM_PRODUCTS",
    conversion_technology: row.technology, capacity_MW_source: allocated, capacity_basis: "TERNA NET CONTROL RESIDUAL / PROJECT SPATIAL ALLOCATION",
    candidate_NET_MW: allocated, source: "TERNA_2024_IMPIANTI_GENERAZIONE_TABLE19|TERNA_2024_CONVERSION_MATRIX", source_date: "2024",
    operational_status: "PLANT/UNIT IDENTITY UNRESOLVED",
    confidence: "HIGH NATIONAL CONTROL; LOW SPATIAL/TECHNOLOGY ATTRIBUTION",
    Terna_control_role: "NATIONAL CONTROL RESIDUAL ALLOCATED ACROSS OIL-COMPATIBLE CONVERSION CAPACITY",
    removal_status: "TERNA_CONTROL_RESIDUAL", physical_attribution_status: "CONTROLLED RESIDUAL; NOT A PLANT RECORD",
    notes: "Explicit residual allocation across remaining GT, engine, steam and other-thermal rows after direct coal/oil removals. This supersedes the interrupted internal-combustion-only heuristic and remains a validation/refinement candidate.",
  });
}

const removedCoal = sum(removalMask.filter((row) => row.fuel === "COAL").map((row) => row.candidate_NET_MW));
const removedOil = sum(removalMask.filter((row) => row.fuel === "PETROLEUM_PRODUCTS").map((row) => row.candidate_NET_MW));
if (!almost(removedCoal, COAL_CONTROL_MW, 1e-6) || !almost(removedOil, OIL_CONTROL_MW, 1e-6)) throw new Error("Removal mask does not reconcile to Terna fuel controls.");

const removalReconciliation = [
  {
    fuel: "COAL", Terna_national_NET_control_MW: COAL_CONTROL_MW, identified_direct_NET_MW: round(coalDirectMW, 9),
    identified_direct_share: round(coalDirectMW / COAL_CONTROL_MW, 9), gross_NET_basis_difference_MW: "NOT APPLICABLE — direct candidate uses Terna NET rows",
    unresolved_physical_residual_MW: coalResidualMW, allocated_control_residual_MW: coalResidualMW,
    final_removal_MW: round(removedCoal, 9), difference_to_control_MW: round(removedCoal - COAL_CONTROL_MW, 9),
    residual_allocation_method: "Assign to NORD STEAM_CONDENSING residual band because the unresolved coal geography is associated with former NORD coal sites; no plant identity claimed.",
    status: "RECONCILED_NATIONALLY; 96.77% DIRECT; RESIDUAL PHYSICAL ATTRIBUTION OPEN",
  },
  {
    fuel: "PETROLEUM_PRODUCTS", Terna_national_NET_control_MW: OIL_CONTROL_MW, identified_direct_NET_MW: oilDirectMW,
    identified_direct_share: round(oilDirectMW / OIL_CONTROL_MW, 9), gross_NET_basis_difference_MW: "A2A nominal 960 MW is not substituted for Terna 886 MW NET",
    unresolved_physical_residual_MW: oilResidualMW, allocated_control_residual_MW: round(sum(removalMask.filter((row) => row.fuel === "PETROLEUM_PRODUCTS" && row.removal_status === "TERNA_CONTROL_RESIDUAL").map((row) => row.candidate_NET_MW)), 9),
    final_removal_MW: round(removedOil, 9), difference_to_control_MW: round(removedOil - OIL_CONTROL_MW, 9),
    residual_allocation_method: "Allocate the national residual across remaining oil-compatible GT/engine/steam/other conversion capacity after direct plant removals; preserve as residual bands, not plant records.",
    status: "RECONCILED_NATIONALLY; 44.84% DIRECT; MATERIAL PHYSICAL ATTRIBUTION RESIDUAL OPEN",
  },
];

// ---------------------------------------------------------------------------
// Mutually exclusive post-phaseout anchor and future geography crosswalk
// ---------------------------------------------------------------------------

const futureMap2040 = {
  CCGT_NON_CHP: "CCGT", CCGT_CHP: "CCGT",
  GT_NON_CHP: "GT_OCGT", GT_CHP: "GT_OCGT",
  INTERNAL_COMBUSTION_NON_CHP: "INTERNAL_COMBUSTION", INTERNAL_COMBUSTION_CHP: "INTERNAL_COMBUSTION",
  STEAM_CONDENSING: "STEAM_OTHER_SURVIVING", STEAM_EXTRACTION_CHP: "STEAM_OTHER_SURVIVING", STEAM_BACKPRESSURE_CHP: "STEAM_OTHER_SURVIVING",
  FUEL_CELL_NON_CHP: "OTHER_SURVIVING_THERMAL", FUEL_CELL_CHP: "OTHER_SURVIVING_THERMAL",
  EXTERNAL_COMBUSTION: "OTHER_SURVIVING_THERMAL", TURBO_EXPANSION: "OTHER_SURVIVING_THERMAL", OTHER_THERMAL: "OTHER_SURVIVING_THERMAL",
};
const removalByZoneTech = groupSum(removalMask, (row) => `${row.zone}|${row.conversion_technology}`, (row) => row.candidate_NET_MW);
const anchorRaw = [];
for (const zone of ZONES) {
  for (const technology of THERMAL_TECHS) {
    const observed = historicalTechCapacity.get(`${zone}|${technology}`) || 0;
    const coalRemoved = sum(removalMask.filter((row) => row.zone === zone && row.conversion_technology === technology && row.fuel === "COAL").map((row) => row.candidate_NET_MW));
    const oilRemoved = sum(removalMask.filter((row) => row.zone === zone && row.conversion_technology === technology && row.fuel === "PETROLEUM_PRODUCTS").map((row) => row.candidate_NET_MW));
    const surviving = observed - coalRemoved - oilRemoved;
    if (surviving < -1e-6) throw new Error(`Negative surviving capacity ${zone} ${technology}: ${surviving}`);
    anchorRaw.push({ zone, historical_technology: technology, future_technology: futureMap2040[technology], observed, coalRemoved, oilRemoved, surviving: Math.max(0, surviving) });
  }
}
for (const zone of ZONES) anchorRaw.push({ zone, historical_technology: "GEOTHERMAL", future_technology: "GEOTHERMAL", observed: geothermalByZone.get(zone), coalRemoved: 0, oilRemoved: 0, surviving: geothermalByZone.get(zone) });

const anchorGroupedMap = new Map();
for (const row of anchorRaw) {
  const key = `${row.zone}|${row.future_technology}`;
  const current = anchorGroupedMap.get(key) || { zone: row.zone, future_technology: row.future_technology, source_technologies: [], observed: 0, coalRemoved: 0, oilRemoved: 0, surviving: 0 };
  current.source_technologies.push(row.historical_technology);
  current.observed += row.observed;
  current.coalRemoved += row.coalRemoved;
  current.oilRemoved += row.oilRemoved;
  current.surviving += row.surviving;
  anchorGroupedMap.set(key, current);
}
const anchorGrouped = [...anchorGroupedMap.values()];
const anchorTotal = sum(anchorGrouped.map((row) => row.surviving));
const anchorNationalByTech = groupSum(anchorGrouped, (row) => row.future_technology, (row) => row.surviving);
const anchorRows = anchorGrouped.map((row) => ({
  anchor_year: 2024,
  market_zone: row.zone,
  future_technology: row.future_technology,
  historical_source_technologies: [...new Set(row.source_technologies)].join("|"),
  observed_2024_capacity_NET_MW: round(row.observed, 9),
  coal_removed_NET_MW: round(row.coalRemoved, 9),
  oil_removed_NET_MW: round(row.oilRemoved, 9),
  surviving_post_coal_oil_capacity_NET_MW: round(row.surviving, 9),
  national_surviving_technology_NET_MW: round(anchorNationalByTech.get(row.future_technology), 9),
  technology_share_of_anchor: round(anchorNationalByTech.get(row.future_technology) / anchorTotal, 12),
  zone_share_within_technology: anchorNationalByTech.get(row.future_technology) > 0 ? round(row.surviving / anchorNationalByTech.get(row.future_technology), 12) : 0,
  zone_technology_share_of_total_anchor: round(row.surviving / anchorTotal, 12),
  capacity_basis: "NET EFFICIENT",
  accounting_role: "MUTUALLY_EXCLUSIVE_2040_SCALING_ANCHOR",
  evidence_class: row.future_technology === "GEOTHERMAL" ? "DIRECT TERNA CONTROL" : "PROJECT DERIVATION FROM TERNA CONVERSION MATRIX AND REMOVAL MASK",
  status: row.oilRemoved > 0 && removalMask.some((item) => item.zone === row.zone && item.removal_status === "TERNA_CONTROL_RESIDUAL") ? "CONTROLLED_RESIDUAL_ALLOCATION_PRESENT" : "READY_FOR_2040_ALLOCATION",
  bioenergy_guardrail: "Terna 3.586-GW fuel and 3.8000923-GW renewable-source views are not carved from this conversion matrix; bioenergy remains inside conversion carriers for 2040 accounting.",
}));
if (!almost(anchorTotal, 54208.86427, 1e-6)) throw new Error(`Unexpected post-coal/oil anchor ${anchorTotal}`);

const taxonomyRows = [
  ["CCGT", "THERMAL_CONVERSION", true, true, false, "Combined historical CCGT CHP+non-CHP after phaseout mask", "2040 additive carrier; geography anchor for GAS_CCS"],
  ["GT_OCGT", "THERMAL_CONVERSION", true, true, false, "Combined historical GT CHP+non-CHP after phaseout mask", "2040 additive carrier; geography anchor for GAS_OTHER_FOSSIL"],
  ["INTERNAL_COMBUSTION", "THERMAL_CONVERSION", true, false, false, "Combined engine CHP+non-CHP after oil residual removal", "2040 additive carrier"],
  ["STEAM_OTHER_SURVIVING", "THERMAL_CONVERSION", true, false, false, "Surviving condensing/extraction/back-pressure steam", "2040 additive carrier; coal and oil removed"],
  ["OTHER_SURVIVING_THERMAL", "THERMAL_CONVERSION", true, false, false, "Fuel cells/external combustion/turbo-expansion/other thermal", "2040 additive carrier"],
  ["GEOTHERMAL", "PROGRAMMABLE_RENEWABLE", true, true, false, "Direct Terna geothermal capacity", "Inside 2040 55-GW and adopted 2050 30-GW perimeters"],
  ["BIOENERGY_2040_CROSS_CLASSIFICATION", "FUEL_SOURCE_CROSS_CLASSIFICATION", false, false, false, "Terna fuel/source cross-classification", "Non-additive in 2040 conversion anchor; geography proxy only"],
  ["BIOENERGY", "PROGRAMMABLE_RENEWABLE", false, true, false, "PNIEC energy/CF-derived weight normalized to Terna envelope", "2050 additive carrier"],
  ["BIOENERGY_CCS", "PROGRAMMABLE_RENEWABLE_CCS", false, true, false, "PNIEC energy/CF-derived weight normalized to Terna envelope", "2050 additive carrier"],
  ["GAS_CCS", "THERMAL_CCS", false, true, false, "PNIEC energy/CF-derived weight normalized to Terna envelope", "2050 additive carrier; CCGT geography proxy"],
  ["GAS_OTHER_FOSSIL", "THERMAL_FOSSIL", false, true, false, "PNIEC energy/CF-derived weight normalized to Terna envelope", "2050 additive carrier; GT/OCGT geography proxy"],
  ["NUCLEAR", "NUCLEAR", false, true, false, "Direct 8-GW project scenario capacity", "2050 additive carrier; fixed NORD 5 / CSUD 3"],
].map((row) => ({
  future_technology: row[0], technology_family: row[1], additive_in_2040_55GW: row[2], additive_in_2050_30GW: row[3], historical_cross_classification_only: row[4],
  capacity_definition: row[5], scenario_role: row[6], fuel_and_conversion_separate: true, extendable: false,
  evidence_class: "PROJECT SCENARIO TAXONOMY", status: "FROZEN_FOR_ZONAL_CAPACITY_TABLES",
}));

const crosswalkRows = [];
function appendCrosswalk(futureTechnology, anchorTechnology, shares, method, evidence, notes) {
  for (const zone of ZONES) crosswalkRows.push({
    future_technology: futureTechnology, geography_anchor_technology: anchorTechnology, market_zone: zone,
    allocation_share: round(shares.get(zone) || 0, 12), allocation_anchor_year: 2024,
    zonal_allocation_method: method, evidence_class: evidence, direct_siting_forecast: false,
    status: "FROZEN_FOR_ZONAL_CAPACITY_TABLES", notes,
  });
}
const anchorShares = (technology) => new Map(ZONES.map((zone) => {
  const row = anchorGrouped.find((item) => item.zone === zone && item.future_technology === technology);
  return [zone, row ? row.surviving / anchorNationalByTech.get(technology) : 0];
}));
for (const technology of ["CCGT", "GT_OCGT", "INTERNAL_COMBUSTION", "STEAM_OTHER_SURVIVING", "OTHER_SURVIVING_THERMAL", "GEOTHERMAL"]) {
  appendCrosswalk(technology, technology, anchorShares(technology), "PROJECT_ALLOCATION_USING_POST_COAL_OIL_CURRENT_TECHNOLOGY_GEOGRAPHY", "PROJECT ALLOCATION", "Technology-specific current geography proxy; not a project siting forecast.");
}
appendCrosswalk("CCGT_CCS", "CCGT", anchorShares("CCGT"), "PROJECT_ALLOCATION_USING_CURRENT_TECHNOLOGY_GEOGRAPHY", "PROJECT ALLOCATION", "Same combined CCGT CHP+non-CHP geography as CCGT.");
appendCrosswalk("GAS_CCS", "CCGT", anchorShares("CCGT"), "PROJECT_ALLOCATION_USING_CURRENT_TECHNOLOGY_GEOGRAPHY", "PROJECT ALLOCATION", "CCGT-based CCS siting proxy; not known CCS locations.");
appendCrosswalk("GAS_OTHER_FOSSIL", "GT_OCGT", anchorShares("GT_OCGT"), "PROJECT_ALLOCATION_USING_CURRENT_TECHNOLOGY_GEOGRAPHY", "PROJECT ALLOCATION", "GT/OCGT geography proxy for the low-energy gas/other-fossil tranche.");
const bioShares = new Map(ZONES.map((zone) => [zone, bioByZone.get(zone) / bioRenewableTotal]));
appendCrosswalk("BIOENERGY", "BIOENERGY_RENEWABLE_SOURCE_CROSS_CLASSIFICATION", bioShares, "PROJECT_ALLOCATION_USING_CURRENT_BIOENERGY_GEOGRAPHY", "PROJECT ALLOCATION / CROSS-CLASSIFICATION", "Geography proxy only; does not reconstruct historical conversion technology.");
appendCrosswalk("BIOENERGY_CCS", "BIOENERGY_RENEWABLE_SOURCE_CROSS_CLASSIFICATION", bioShares, "PROJECT_ALLOCATION_USING_CURRENT_BIOENERGY_GEOGRAPHY", "PROJECT ALLOCATION / CROSS-CLASSIFICATION", "Same current bioenergy geography; not a CCS siting forecast.");
const nuclearShares = new Map(ZONES.map((zone) => [zone, zone === "NORD" ? 5 / 8 : zone === "CSUD" ? 3 / 8 : 0]));
appendCrosswalk("NUCLEAR", "USER_APPROVED_FIXED_SITING", nuclearShares, "FIXED_NUCLEAR_SITING_NORD_5GW_CSUD_3GW", "PROJECT SCENARIO ASSUMPTION", "Direct project siting allocation.");

for (const technology of [...new Set(crosswalkRows.map((row) => row.future_technology))]) {
  const total = sum(crosswalkRows.filter((row) => row.future_technology === technology).map((row) => row.allocation_share));
  if (!almost(total, 1, 1e-9)) throw new Error(`Crosswalk shares for ${technology} sum to ${total}`);
}

// ---------------------------------------------------------------------------
// Current hydro class matrix, fixed identically across all future scenarios
// ---------------------------------------------------------------------------

const hydroControls = {
  total: 23294.0,
  ror: 6239.0,
  basin: 5019.9,
  reservoir: 4782.8,
  pure: 3969.57561,
  mixed: 3282.72439,
};
const totalHydroByZone = new Map([
  ["NORD", 17108.367664581], ["CNOR", 623.19755], ["CSUD", 3326.055049822], ["SUD", 225.32915],
  ["CALA", 833.364], ["SICI", 714.267585597], ["SARD", 463.419],
]);
const pureByZone = new Map([
  ["NORD", 2393.185354581], ["CNOR", 0], ["CSUD", 995.915669822], ["SUD", 0], ["CALA", 0], ["SICI", 580.474585597], ["SARD", 0],
]);
const mixedByZone = new Map([
  ["NORD", 2305.444434376], ["CNOR", 0], ["CSUD", 736.949744032], ["SUD", 0], ["CALA", 0], ["SICI", 0], ["SARD", 240.330211592],
]);
const nonPumpedByZone = new Map(ZONES.map((zone) => [zone, totalHydroByZone.get(zone) - pureByZone.get(zone) - mixedByZone.get(zone)]));
const nordDirect = { ror: 5227.9, basin: 3512.0, reservoir: 3669.1 };
const nordScale = nonPumpedByZone.get("NORD") / sum(Object.values(nordDirect));
const hydroClassByZone = new Map();
hydroClassByZone.set("NORD", {
  HYDRO_RUN_OF_RIVER: nordDirect.ror * nordScale,
  HYDRO_BASIN: nordDirect.basin * nordScale,
  HYDRO_RESERVOIR: nordDirect.reservoir * nordScale,
  PUMPED_HYDRO_PURE: pureByZone.get("NORD"),
  PUMPED_HYDRO_MIXED: mixedByZone.get("NORD"),
});
const remaining = {
  ror: hydroControls.ror - hydroClassByZone.get("NORD").HYDRO_RUN_OF_RIVER,
  basin: hydroControls.basin - hydroClassByZone.get("NORD").HYDRO_BASIN,
  reservoir: hydroControls.reservoir - hydroClassByZone.get("NORD").HYDRO_RESERVOIR,
};
const hdamByZone = new Map(ZONES.map((zone) => [zone, sum(hydroPhysicalZone.filter((row) => row.market_zone === zone && row.hydro_class === "HDAM").map((row) => row.installed_capacity_MW))]));
const otherZones = ZONES.filter((zone) => zone !== "NORD");
const rorSeedByZone = new Map(otherZones.map((zone) => [zone, Math.max(0.001, nonPumpedByZone.get(zone) - (hdamByZone.get(zone) || 0))]));
const rorSeedTotal = sum([...rorSeedByZone.values()]);
const storageSplit = remaining.basin / (remaining.basin + remaining.reservoir);
for (const zone of otherZones) {
  const ror = remaining.ror * rorSeedByZone.get(zone) / rorSeedTotal;
  const programmable = nonPumpedByZone.get(zone) - ror;
  hydroClassByZone.set(zone, {
    HYDRO_RUN_OF_RIVER: ror,
    HYDRO_BASIN: programmable * storageSplit,
    HYDRO_RESERVOIR: programmable * (1 - storageSplit),
    PUMPED_HYDRO_PURE: pureByZone.get(zone),
    PUMPED_HYDRO_MIXED: mixedByZone.get(zone),
  });
}
const hydroRows = [];
for (const zone of ZONES) {
  for (const [technology, capacity] of Object.entries(hydroClassByZone.get(zone))) {
    hydroRows.push({
      reference_year: 2024, market_zone: zone, technology, capacity_NET_MW: round(capacity, 9),
      zone_total_hydro_NET_MW: round(totalHydroByZone.get(zone), 9), national_total_hydro_NET_MW: hydroControls.total,
      accounting_role: "MUTUALLY_EXCLUSIVE_HYDRO_CLASS; SUMS_TO_TOTAL_HYDRO_CONTROL",
      numerical_authority: technology.startsWith("PUMPED") ? "TERNA PUMPING PERIMETER CONTROL" : zone === "NORD" ? "TERNA MACRO CONTROL RECONCILED TO ZONE" : "PROJECT ALLOCATION RECONCILED TO TERNA NATIONAL/ZONE CONTROLS",
      allocation_method: technology.startsWith("PUMPED") ? "DIRECT PURE/MIXED PUMPING PERIMETER BRIDGE" : zone === "NORD" ? "NORD DIRECT CLASS VALUES SCALED 0.006% TO ZONAL NON-PUMPED CONTROL" : "ROR residual allocated using open HDAM programmable-capacity floor; remaining programmable capacity split to basin/reservoir by residual national controls",
      evidence_class: technology.startsWith("PUMPED") ? "DIRECT TERNA CONTROL / PERIMETER DERIVATION" : zone === "NORD" ? "DIRECT MACRO CONTROL + PROJECT RECONCILIATION" : "PROJECT ALLOCATION PROVISIONAL",
      scenario_capacity_rule: "UNCHANGED CURRENT CAPACITY IN 2040/2050 SLOW/BASE/HIGH",
      status: "INSTALLED_CAPACITY_TABLE_READY; OPERATIONAL_PARAMETERS_SEPARATE",
    });
  }
}
if (!almost(sum(hydroRows.map((row) => row.capacity_NET_MW)), hydroControls.total, 1e-5)) throw new Error("Hydro class matrix does not reconcile.");

// ---------------------------------------------------------------------------
// 2040 Base/High and 2050 Base/High national + zonal tables
// ---------------------------------------------------------------------------

const SCALE_2040 = 55000 / anchorTotal;
const base2040Rows = [];
for (const scenario of ["Base", "High"]) {
  for (const row of anchorRows) {
    base2040Rows.push({
      year: 2040, scenario, technology: row.future_technology, market_zone: row.market_zone,
      anchor_capacity_NET_MW: row.surviving_post_coal_oil_capacity_NET_MW,
      national_anchor_NET_MW: round(anchorTotal, 9), scaling_factor_to_55GW: round(SCALE_2040, 12),
      capacity_NET_MW: round(row.surviving_post_coal_oil_capacity_NET_MW * SCALE_2040, 9),
      national_technology_capacity_NET_MW: round(row.national_surviving_technology_NET_MW * SCALE_2040, 9),
      national_envelope_NET_MW: 55000, capacity_basis: "NET EFFICIENT",
      national_capacity_method: "POST_COAL_OIL_MUTUALLY_EXCLUSIVE_2024_ANCHOR_SCALED_TO_TERNA_55GW",
      zonal_allocation_method: "POST_COAL_OIL_ZONE×TECHNOLOGY_ANCHOR",
      evidence_class: "TERNA CAPACITY CONTROL + PROJECT DERIVATION", status: "FROZEN_FOR_INSTALLED_CAPACITY_SCENARIO_TABLE",
    });
  }
}
for (const scenario of ["Base", "High"]) if (!almost(sum(base2040Rows.filter((row) => row.scenario === scenario).map((row) => row.capacity_NET_MW)), 55000, 1e-5)) throw new Error(`2040 ${scenario} does not sum to 55 GW.`);

const raw2050 = [
  { technology: "BIOENERGY", energyTWh: 10.6, cf: 0.47 },
  { technology: "BIOENERGY_CCS", energyTWh: 6.0, cf: 0.47 },
  { technology: "GAS_CCS", energyTWh: 4.0, cf: 0.20 },
  { technology: "GAS_OTHER_FOSSIL", energyTWh: 2.1, cf: 0.20 },
].map((row) => ({ ...row, rawGW: row.energyTWh / (8.76 * row.cf) }));
const raw2050Total = sum(raw2050.map((row) => row.rawGW));
const envelope2050MW = 30000 - 8000 - geothermalTotal;
const normalization2050 = envelope2050MW / (raw2050Total * 1000);
const final2050National = raw2050.map((row) => ({ ...row, rawWeight: row.rawGW / raw2050Total, finalMW: row.rawGW / raw2050Total * envelope2050MW }));

const perimeter2050Rows = [
  { line_item: "Terna efficient programmable capacity benchmark", value_MW: 30000, accounting_sign: 1, perimeter_role: "CONTROL TOTAL", source_support: "Terna Figure 56: at least ~30 GW efficient programmable capacity after an illustrative 15% rating; programmable thermal and/or nuclear remain necessary.", decision: "ADOPTED NATIONAL CONTROL", evidence_class: "TERNA ADEQUACY BENCHMARK" },
  { line_item: "Nuclear fixed capacity", value_MW: 8000, accounting_sign: -1, perimeter_role: "INSIDE 30-GW CONTROL", source_support: "PNIEC/Terna nuclear scenario; project fixed NORD 5 GW / CSUD 3 GW.", decision: "SUBTRACT BEFORE NORMALIZING NON-NUCLEAR CATEGORIES", evidence_class: "DIRECT SOURCE CAPACITY + PROJECT SITING" },
  { line_item: "Geothermal retained current capacity", value_MW: round(geothermalTotal, 9), accounting_sign: -1, perimeter_role: "INSIDE 30-GW CONTROL", source_support: "Terna defines the residual requirement as programmable capacity (thermal and/or nuclear) after programmable hydro is already subtracted; the 2050 balance also includes programmable renewables. MEM therefore includes non-hydro programmable geothermal in the envelope.", decision: "PROJECT PERIMETER INTERPRETATION — INCLUDED", evidence_class: "SOURCE-SUPPORTED PROJECT PERIMETER DECISION" },
  { line_item: "Four-category PNIEC weight normalization envelope", value_MW: round(envelope2050MW, 9), accounting_sign: 1, perimeter_role: "DERIVED RESIDUAL ENVELOPE", source_support: "30,000 - 8,000 - 771.79", decision: "NORMALIZE BIOENERGY/BIOENERGY_CCS/GAS_CCS/GAS_OTHER_FOSSIL WEIGHTS TO THIS VALUE", evidence_class: "PROJECT DERIVATION" },
];

const national2050Rows = [];
for (const scenario of ["Base", "High"]) {
  for (const row of final2050National) national2050Rows.push({
    year: 2050, scenario, technology: row.technology, PNIEC_energy_TWh: row.energyTWh,
    initial_CF_assumption: row.cf, raw_energy_implied_GW: round(row.rawGW, 12), raw_weight: round(row.rawWeight, 12),
    Terna_normalization_envelope_GW: round(envelope2050MW / 1000, 9), normalization_factor: round(normalization2050, 12),
    final_national_capacity_GW: round(row.finalMW / 1000, 12), evidence_role: "PNIEC ENERGY/CF-DERIVED TECHNOLOGY WEIGHT NORMALIZED TO TERNA PROGRAMMABLE ENVELOPE",
    status: "INSTALLED CAPACITY; CF DOES NOT CONSTRAIN FUTURE DISPATCH",
  });
  national2050Rows.push({ year: 2050, scenario, technology: "GEOTHERMAL", PNIEC_energy_TWh: "", initial_CF_assumption: "", raw_energy_implied_GW: "", raw_weight: "", Terna_normalization_envelope_GW: 30, normalization_factor: "", final_national_capacity_GW: round(geothermalTotal / 1000, 12), evidence_role: "CURRENT TERNA CAPACITY RETAINED INSIDE PROGRAMMABLE ENVELOPE", status: "DIRECT CONTROL / PROJECT RETENTION" });
  national2050Rows.push({ year: 2050, scenario, technology: "NUCLEAR", PNIEC_energy_TWh: 64.2, initial_CF_assumption: "NOT USED TO DERIVE CAPACITY", raw_energy_implied_GW: 8, raw_weight: "", Terna_normalization_envelope_GW: 30, normalization_factor: "", final_national_capacity_GW: 8, evidence_role: "DIRECT 8-GW CAPACITY", status: "FIXED NORD 5 / CSUD 3" });
}

const crosswalkShare = (technology, zone) => Number(crosswalkRows.find((row) => row.future_technology === technology && row.market_zone === zone)?.allocation_share || 0);
const zonal2050Rows = [];
for (const scenario of ["Base", "High"]) {
  for (const row of national2050Rows.filter((item) => item.scenario === scenario)) {
    for (const zone of ZONES) {
      const share = crosswalkShare(row.technology, zone);
      zonal2050Rows.push({
        year: 2050, scenario, technology: row.technology, market_zone: zone,
        national_capacity_MW: round(Number(row.final_national_capacity_GW) * 1000, 9), allocation_share: round(share, 12),
        capacity_MW: round(Number(row.final_national_capacity_GW) * 1000 * share, 9),
        geography_anchor: row.technology === "GAS_CCS" ? "COMBINED_CCGT_CHP_AND_NON_CHP" : row.technology === "GAS_OTHER_FOSSIL" ? "COMBINED_GT_CHP_AND_NON_CHP" : row.technology.startsWith("BIOENERGY") ? "CURRENT_RENEWABLE_SOURCE_BIOENERGY" : row.technology,
        zonal_allocation_method: crosswalkRows.find((item) => item.future_technology === row.technology && item.market_zone === zone)?.zonal_allocation_method,
        evidence_class: "PROJECT ALLOCATION", status: "RECONCILED_TO_NATIONAL_CAPACITY",
      });
    }
  }
}

// ---------------------------------------------------------------------------
// Corrected Terna CDP Slow adjustments and proportional technology increments
// ---------------------------------------------------------------------------

const CDP = { PV: 32 / 180, WIND: 17 / 66, BESS: 29 / 36 };
const LEGACY_CDP = { WIND: 29 / 66, BESS: 12 / 36 };
const PROGRAMMABLE_AVAILABILITY = 0.85; // Terna Figure 56 illustrative 15% rating adopted by MEM.
const cdpRows = [];
const slowAdjustmentByYear = {};
for (const year of [2040, 2050]) {
  let lostTotal = 0;
  for (const resource of ["PV", "WIND", "BESS"]) {
    const base = baseResourceCapacityGW[year][resource];
    const slow = base * 0.8;
    const loss = base - slow;
    const lost = loss * CDP[resource];
    lostTotal += lost;
    cdpRows.push({
      year, resource, Base_capacity_GW: round(base, 12), Slow_capacity_GW: round(slow, 12), capacity_loss_GW: round(loss, 12),
      CDP_ratio: round(CDP[resource], 12), lost_CDP_GW: round(lost, 12), programmable_availability_factor: PROGRAMMABLE_AVAILABILITY,
      additional_dispatchable_capacity_GW: "", source: "TERNA_2050_PERSPECTIVES_FIGURE55", status: "ACTIVE_SOURCE_CORRECTED_MAPPING",
    });
  }
  const additional = lostTotal / PROGRAMMABLE_AVAILABILITY;
  slowAdjustmentByYear[year] = { lostCDPGW: lostTotal, additionalGW: additional };
  cdpRows.push({
    year, resource: "TOTAL_CORRECTED", Base_capacity_GW: "", Slow_capacity_GW: "", capacity_loss_GW: "", CDP_ratio: "",
    lost_CDP_GW: round(lostTotal, 12), programmable_availability_factor: PROGRAMMABLE_AVAILABILITY,
    additional_dispatchable_capacity_GW: round(additional, 12), source: "TERNA_FIGURE55_RATIOS|TERNA_FIGURE56_15PCT_RATING",
    status: "CONTROLLING_SLOW_CAPACITY_ADJUSTMENT",
  });
}
const legacy2050Lost = (245 * 0.2 * 32 / 180) + (51 * 0.2 * LEGACY_CDP.WIND) + (baseResourceCapacityGW[2050].BESS * 0.2 * LEGACY_CDP.BESS);
cdpRows.push({ year: 2050, resource: "LEGACY_WIND_RATIO", Base_capacity_GW: 51, Slow_capacity_GW: 40.8, capacity_loss_GW: 10.2, CDP_ratio: round(LEGACY_CDP.WIND, 12), lost_CDP_GW: round(10.2 * LEGACY_CDP.WIND, 12), programmable_availability_factor: PROGRAMMABLE_AVAILABILITY, additional_dispatchable_capacity_GW: "", source: "V2.9_INHERITED_WORKBOOK_MAPPING", status: "SUPERSEDED_DO_NOT_USE" });
cdpRows.push({ year: 2050, resource: "LEGACY_BESS_RATIO", Base_capacity_GW: round(baseResourceCapacityGW[2050].BESS, 12), Slow_capacity_GW: round(baseResourceCapacityGW[2050].BESS * 0.8, 12), capacity_loss_GW: round(baseResourceCapacityGW[2050].BESS * 0.2, 12), CDP_ratio: round(LEGACY_CDP.BESS, 12), lost_CDP_GW: round(baseResourceCapacityGW[2050].BESS * 0.2 * LEGACY_CDP.BESS, 12), programmable_availability_factor: PROGRAMMABLE_AVAILABILITY, additional_dispatchable_capacity_GW: round(legacy2050Lost / PROGRAMMABLE_AVAILABILITY, 12), source: "V2.9_INHERITED_WORKBOOK_MAPPING", status: "SUPERSEDED_DO_NOT_USE; LEGACY_18.9449_GW_LINEAGE" });

const base2040NationalByTech = groupSum(base2040Rows.filter((row) => row.scenario === "Base"), (row) => row.technology, (row) => row.capacity_NET_MW);
const slow2040National = [];
const slow2040Zonal = [];
for (const [technology, baseMW] of base2040NationalByTech) {
  const incrementMW = slowAdjustmentByYear[2040].additionalGW * 1000 * baseMW / 55000;
  slow2040National.push({ year: 2040, technology, Base_capacity_MW: round(baseMW, 9), Base_technology_weight: round(baseMW / 55000, 12), Slow_CDP_increment_MW: round(incrementMW, 9), Slow_total_capacity_MW: round(baseMW + incrementMW, 9), increment_method: "PROJECT_SLOW_CDP_PROPORTIONAL_TECHNOLOGY_ALLOCATION", status: "FROZEN_FOR_INSTALLED_CAPACITY_TABLE" });
  for (const zone of ZONES) {
    const baseRow = base2040Rows.find((row) => row.scenario === "Base" && row.technology === technology && row.market_zone === zone);
    const share = crosswalkShare(technology, zone);
    slow2040Zonal.push({ year: 2040, technology, market_zone: zone, Base_capacity_MW: round(baseRow.capacity_NET_MW, 9), technology_geography_share: round(share, 12), Slow_CDP_increment_MW: round(incrementMW * share, 9), Slow_total_capacity_MW: round(baseRow.capacity_NET_MW + incrementMW * share, 9), zonal_allocation_method: "SAME_POST_COAL_OIL_TECHNOLOGY_GEOGRAPHY", status: "RECONCILED" });
  }
}

const base2050NationalByTech = new Map(national2050Rows.filter((row) => row.scenario === "Base" && row.technology !== "NUCLEAR").map((row) => [row.technology, Number(row.final_national_capacity_GW) * 1000]));
const slow2050National = [];
const slow2050Zonal = [];
for (const [technology, baseMW] of base2050NationalByTech) {
  const incrementMW = slowAdjustmentByYear[2050].additionalGW * 1000 * baseMW / 22000;
  slow2050National.push({ year: 2050, technology, Base_capacity_MW: round(baseMW, 9), Base_non_nuclear_weight: round(baseMW / 22000, 12), Slow_CDP_increment_MW: round(incrementMW, 9), Slow_total_capacity_MW: round(baseMW + incrementMW, 9), increment_method: "PROJECT_SLOW_CDP_PROPORTIONAL_TECHNOLOGY_ALLOCATION", status: "FROZEN_FOR_INSTALLED_CAPACITY_TABLE" });
  for (const zone of ZONES) {
    const baseRow = zonal2050Rows.find((row) => row.scenario === "Base" && row.technology === technology && row.market_zone === zone);
    const share = crosswalkShare(technology, zone);
    slow2050Zonal.push({ year: 2050, technology, market_zone: zone, Base_capacity_MW: round(baseRow.capacity_MW, 9), technology_geography_share: round(share, 12), Slow_CDP_increment_MW: round(incrementMW * share, 9), Slow_total_capacity_MW: round(baseRow.capacity_MW + incrementMW * share, 9), zonal_allocation_method: "SAME_POST_COAL_OIL_TECHNOLOGY_GEOGRAPHY", status: "RECONCILED" });
  }
}
slow2050National.push({ year: 2050, technology: "NUCLEAR", Base_capacity_MW: 8000, Base_non_nuclear_weight: "NOT APPLICABLE", Slow_CDP_increment_MW: 0, Slow_total_capacity_MW: 8000, increment_method: "NUCLEAR_FIXED", status: "FIXED NORD 5 / CSUD 3" });
for (const zone of ZONES) slow2050Zonal.push({ year: 2050, technology: "NUCLEAR", market_zone: zone, Base_capacity_MW: zone === "NORD" ? 5000 : zone === "CSUD" ? 3000 : 0, technology_geography_share: crosswalkShare("NUCLEAR", zone), Slow_CDP_increment_MW: 0, Slow_total_capacity_MW: zone === "NORD" ? 5000 : zone === "CSUD" ? 3000 : 0, zonal_allocation_method: "FIXED_NUCLEAR_SITING", status: "RECONCILED" });

// ---------------------------------------------------------------------------
// Canonical complete six-scenario long-form installed-capacity table
// ---------------------------------------------------------------------------

const longRows = [];
function pushLong({ year, scenario, technology, zone, capacityMW, nationalMW, component, baselineOrIncrement, nationalMethod, weightMethod, allocationMethod, anchorYear, source, evidence, status }) {
  longRows.push({
    year, scenario, technology, zone, capacity_MW: round(capacityMW, 9), national_capacity_MW: round(nationalMW, 9),
    capacity_component: component, baseline_or_increment: baselineOrIncrement,
    national_capacity_method: nationalMethod, technology_weight_method: weightMethod,
    zonal_allocation_method: allocationMethod, allocation_anchor_year: anchorYear,
    source_or_assumption: source, evidence_class: evidence, status,
  });
}

for (const { year, scenario } of SCENARIOS) {
  const scenarioKey = `${year}|${scenario}`;
  for (const technology of ["SOLAR_PV_ROOFTOP", "SOLAR_PV_UTILITY", "WIND_ONSHORE", "WIND_OFFSHORE"]) {
    const national = sum(ZONES.map((zone) => generationScenario.get(`${scenarioKey}|${zone}|${technology}`)));
    for (const zone of ZONES) pushLong({ year, scenario, technology, zone, capacityMW: generationScenario.get(`${scenarioKey}|${zone}|${technology}`), nationalMW: national, component: "RENEWABLE", baselineOrIncrement: "SCENARIO_TOTAL", nationalMethod: "FROZEN V2.9 SCENARIO CAPACITY", weightMethod: scenario === "Slow" ? "80% OF BASE" : scenario === "High" ? "110% OF BASE" : "BASE", allocationMethod: "FROZEN V2.9 ZONAL ALLOCATION", anchorYear: "V2.7/V2.9", source: "CONTROLLING V2.9 SCENARIO MATRIX", evidence: "ACCEPTED SCENARIO INPUT", status: "INSTALLED_CAPACITY_TABLE_READY" });
  }
  const bessNational = sum(ZONES.map((zone) => bessScenario.get(`${year}|${scenario}|${zone}`)));
  for (const zone of ZONES) pushLong({ year, scenario, technology: "BESS", zone, capacityMW: bessScenario.get(`${year}|${scenario}|${zone}`), nationalMW: bessNational, component: "STORAGE", baselineOrIncrement: "SCENARIO_TOTAL_DISCHARGE_POWER", nationalMethod: "FROZEN V2.9 BESS POWER", weightMethod: scenario === "Slow" ? "80% OF BASE" : scenario === "High" ? "110% OF BASE" : "BASE", allocationMethod: "FROZEN V2.9 ZONAL STORAGE ALLOCATION", anchorYear: "V2.9", source: "CONTROLLING V2.9 STORAGE MATRIX", evidence: "ACCEPTED SCENARIO INPUT", status: "P_NOM ONLY; E_NOM SEPARATE" });
  for (const hydroRow of hydroRows) pushLong({ year, scenario, technology: hydroRow.technology, zone: hydroRow.market_zone, capacityMW: hydroRow.capacity_NET_MW, nationalMW: sum(hydroRows.filter((row) => row.technology === hydroRow.technology).map((row) => row.capacity_NET_MW)), component: "HYDRO_FIXED_CURRENT", baselineOrIncrement: "CURRENT_FLEET_COPIED_UNCHANGED", nationalMethod: "USER_CONFIRMED CURRENT HYDRO CAPACITY IN ALL FUTURE SCENARIOS", weightMethod: "NOT APPLICABLE", allocationMethod: hydroRow.allocation_method, anchorYear: 2024, source: "TERNA HYDRO CONTROLS + PROJECT CLASS ALLOCATION", evidence: hydroRow.evidence_class, status: "CAPACITY FROZEN; OPERATIONAL PARAMETERS PARTIAL" });

  if (year === 2040) {
    const baseRows = base2040Rows.filter((row) => row.scenario === "Base");
    for (const row of baseRows) pushLong({ year, scenario, technology: row.technology, zone: row.market_zone, capacityMW: row.capacity_NET_MW, nationalMW: row.national_technology_capacity_NET_MW, component: "BASE_THERMAL", baselineOrIncrement: "BASE_PORTFOLIO", nationalMethod: "2024 POST-COAL/OIL ANCHOR SCALED TO 55 GW", weightMethod: "ANCHOR TECHNOLOGY PROPORTIONS", allocationMethod: "POST_COAL_OIL TECHNOLOGY GEOGRAPHY", anchorYear: 2024, source: "TERNA 2024 + COAL/OIL MASK + 2040 55-GW CONTROL", evidence: "TERNA CONTROL + PROJECT DERIVATION", status: "INSTALLED_CAPACITY_TABLE_READY" });
    if (scenario === "Slow") for (const row of slow2040Zonal) pushLong({ year, scenario, technology: row.technology, zone: row.market_zone, capacityMW: row.Slow_CDP_increment_MW, nationalMW: slow2040National.find((item) => item.technology === row.technology).Slow_CDP_increment_MW, component: "SLOW_CDP_ADDITIONAL_THERMAL", baselineOrIncrement: "INCREMENT", nationalMethod: "CORRECTED TERNA CDP LOSS / 0.85 AVAILABILITY", weightMethod: "BASE NON-NUCLEAR THERMAL TECHNOLOGY PROPORTIONS", allocationMethod: "SAME POST-COAL/OIL TECHNOLOGY GEOGRAPHY", anchorYear: 2024, source: "TERNA FIGURES 55-56 + PROJECT PROPORTIONAL ALLOCATION", evidence: "PROJECT SLOW CDP DERIVATION", status: "INSTALLED_CAPACITY_TABLE_READY" });
  } else {
    const baseRows = zonal2050Rows.filter((row) => row.scenario === "Base");
    for (const row of baseRows) pushLong({ year, scenario, technology: row.technology, zone: row.market_zone, capacityMW: row.capacity_MW, nationalMW: row.national_capacity_MW, component: row.technology === "NUCLEAR" ? "NUCLEAR_FIXED" : "BASE_THERMAL", baselineOrIncrement: "BASE_PORTFOLIO", nationalMethod: row.technology === "NUCLEAR" ? "DIRECT 8-GW CAPACITY" : row.technology === "GEOTHERMAL" ? "CURRENT CAPACITY RETAINED INSIDE 30-GW ENVELOPE" : "PNIEC ENERGY/CF WEIGHTS NORMALIZED TO TERNA ENVELOPE", weightMethod: row.technology === "NUCLEAR" || row.technology === "GEOTHERMAL" ? "NOT APPLICABLE" : "PNIEC ENERGY/CF-DERIVED RELATIVE WEIGHT", allocationMethod: row.zonal_allocation_method, anchorYear: 2024, source: "TERNA 2050 + PNIEC + PROJECT GEOGRAPHY", evidence: row.technology === "NUCLEAR" ? "DIRECT SOURCE CAPACITY + PROJECT SITING" : "TERNA CONTROL + PROJECT DERIVATION", status: "INSTALLED_CAPACITY_TABLE_READY; CF NOT A DISPATCH QUOTA" });
    if (scenario === "Slow") for (const row of slow2050Zonal.filter((item) => item.technology !== "NUCLEAR")) pushLong({ year, scenario, technology: row.technology, zone: row.market_zone, capacityMW: row.Slow_CDP_increment_MW, nationalMW: slow2050National.find((item) => item.technology === row.technology).Slow_CDP_increment_MW, component: "SLOW_CDP_ADDITIONAL_THERMAL", baselineOrIncrement: "INCREMENT", nationalMethod: "CORRECTED TERNA CDP LOSS / 0.85 AVAILABILITY", weightMethod: "BASE NON-NUCLEAR PROGRAMMABLE TECHNOLOGY PROPORTIONS", allocationMethod: "SAME TECHNOLOGY-SPECIFIC 2024 GEOGRAPHY", anchorYear: 2024, source: "TERNA FIGURES 55-56 + PROJECT PROPORTIONAL ALLOCATION", evidence: "PROJECT SLOW CDP DERIVATION", status: "INSTALLED_CAPACITY_TABLE_READY" });
  }
}

// ---------------------------------------------------------------------------
// QA and phase gates
// ---------------------------------------------------------------------------

const scenarioAggregate = (year, scenario, predicate) => sum(longRows.filter((row) => row.year === year && row.scenario === scenario && predicate(row)).map((row) => row.capacity_MW));
const thermalPredicate = (row) => ["BASE_THERMAL", "SLOW_CDP_ADDITIONAL_THERMAL"].includes(row.capacity_component);
const programmablePredicate = (row) => thermalPredicate(row) || row.capacity_component === "NUCLEAR_FIXED";
const qaRows = [];
function addQa(id, check, expected, actual, tolerance, evidenceClass = "QA / RECONCILIATION") {
  const numeric = typeof expected === "number" && typeof actual === "number";
  const pass = numeric ? almost(actual, expected, tolerance) : String(actual) === String(expected);
  qaRows.push({ check_id: id, check, expected, actual: typeof actual === "number" ? round(actual, 12) : actual, tolerance, status: pass ? "PASS" : "FAIL", evidence_class: evidenceClass });
}
addQa("ZCA-001", "Frozen capacity rows unchanged", 758, historicalCapacity.length, 0);
addQa("ZCA-002", "Frozen generation rows unchanged", 881, historicalGeneration.length, 0);
addQa("ZCA-003", "Frozen storage rows unchanged", 19, historicalStorage.length, 0);
addQa("ZCA-004", "Coal mask reconciles to Terna control MW", COAL_CONTROL_MW, removedCoal, 1e-6);
addQa("ZCA-005", "Oil mask reconciles to Terna control MW", OIL_CONTROL_MW, removedOil, 1e-6);
addQa("ZCA-006", "Post-coal/oil thermoelectric+geothermal anchor MW", 54208.86427, anchorTotal, 1e-6);
addQa("ZCA-007", "2040 Base thermal MW", 55000, scenarioAggregate(2040, "Base", thermalPredicate), 1e-4);
addQa("ZCA-008", "2040 High thermal MW", 55000, scenarioAggregate(2040, "High", thermalPredicate), 1e-4);
addQa("ZCA-009", "2040 Slow thermal MW", 55000 + slowAdjustmentByYear[2040].additionalGW * 1000, scenarioAggregate(2040, "Slow", thermalPredicate), 1e-4);
addQa("ZCA-010", "2050 Base programmable MW", 30000, scenarioAggregate(2050, "Base", programmablePredicate), 1e-4);
addQa("ZCA-011", "2050 High programmable MW", 30000, scenarioAggregate(2050, "High", programmablePredicate), 1e-4);
addQa("ZCA-012", "2050 Slow programmable MW", 30000 + slowAdjustmentByYear[2050].additionalGW * 1000, scenarioAggregate(2050, "Slow", programmablePredicate), 1e-4);
for (const scenario of ["Slow", "Base", "High"]) addQa(`ZCA-HYDRO-2040-${scenario}`, `2040 ${scenario} hydro MW`, hydroControls.total, scenarioAggregate(2040, scenario, (row) => row.capacity_component === "HYDRO_FIXED_CURRENT"), 1e-4);
for (const scenario of ["Slow", "Base", "High"]) addQa(`ZCA-HYDRO-2050-${scenario}`, `2050 ${scenario} hydro MW`, hydroControls.total, scenarioAggregate(2050, scenario, (row) => row.capacity_component === "HYDRO_FIXED_CURRENT"), 1e-4);
for (const scenario of ["Slow", "Base", "High"]) addQa(`ZCA-NUCLEAR-${scenario}`, `2050 ${scenario} nuclear MW`, 8000, scenarioAggregate(2050, scenario, (row) => row.capacity_component === "NUCLEAR_FIXED"), 1e-6);
addQa("ZCA-CDP-PV", "Corrected PV CDP ratio", 32 / 180, CDP.PV, 1e-12);
addQa("ZCA-CDP-WIND", "Corrected wind CDP ratio", 17 / 66, CDP.WIND, 1e-12);
addQa("ZCA-CDP-BESS", "Corrected BESS CDP ratio", 29 / 36, CDP.BESS, 1e-12);
addQa("ZCA-CDP-LEGACY-ACTIVE", "No active legacy wind/BESS ratio rows", 0, cdpRows.filter((row) => !String(row.status).startsWith("SUPERSEDED_DO_NOT_USE") && [29 / 66, 12 / 36].some((ratio) => almost(Number(row.CDP_ratio), ratio, 1e-12))).length, 0);
addQa("ZCA-COAL-FUTURE", "No future coal carrier rows", 0, longRows.filter((row) => String(row.technology).includes("COAL")).length, 0);
addQa("ZCA-OIL-FUTURE", "No future oil carrier rows", 0, longRows.filter((row) => String(row.technology).includes("OIL") || String(row.technology).includes("PETROLEUM")).length, 0);
addQa("ZCA-BIO-DOUBLE", "No 2040 additive bioenergy cross-classification row", 0, longRows.filter((row) => row.year === 2040 && String(row.technology).startsWith("BIOENERGY")).length, 0);
for (const technology of [...new Set(crosswalkRows.map((row) => row.future_technology))]) addQa(`ZCA-SHARE-${technology}`, `${technology} zonal allocation shares sum to 1`, 1, sum(crosswalkRows.filter((row) => row.future_technology === technology).map((row) => row.allocation_share)), 1e-9);
for (const { year, scenario } of SCENARIOS) {
  for (const technology of [...new Set(longRows.filter((row) => row.year === year && row.scenario === scenario).map((row) => row.technology))]) {
    const techRows = longRows.filter((row) => row.year === year && row.scenario === scenario && row.technology === technology);
    const components = [...new Set(techRows.map((row) => row.capacity_component))];
    for (const component of components) {
      const rows = techRows.filter((row) => row.capacity_component === component);
      addQa(`ZCA-ZSUM-${year}-${scenario}-${technology}-${component}`, `${year} ${scenario} ${technology} ${component}: seven zones equal national component`, Number(rows[0].national_capacity_MW), sum(rows.map((row) => row.capacity_MW)), 1e-4);
    }
  }
}

const gateRows = [
  { gate: "HISTORICAL_TECHNOLOGY_BASELINE_STATUS", status: "FROZEN", controlling_reason: "Phase-3.1 historical Terna measurements and taxonomy remain unchanged.", next_action: "Use as source anchor; do not reopen." },
  { gate: "THERMAL_GEOGRAPHY_ANCHOR_STATUS", status: "FROZEN_WITH_CONTROLLED_RESIDUAL_BANDS", controlling_reason: `Coal direct attribution ${round(coalDirectMW, 3)} MW plus ${coalResidualMW} MW residual; oil direct ${oilDirectMW} MW plus ${oilResidualMW} MW dispersed residual; both national controls close exactly.`, next_action: "GEM/primary plant evidence may refine residual geography without changing national controls." },
  { gate: "2040_BASE_HIGH_CAPACITY_TABLE_STATUS", status: "RESOLVED", controlling_reason: "Post-coal/oil anchor scaled to exactly 55 GW; Base and High share the same thermal portfolio.", next_action: "Use fixed p_nom table in later solver-input construction." },
  { gate: "2040_SLOW_CDP_CAPACITY_STATUS", status: "RESOLVED", controlling_reason: "Corrected Figure-55 CDP ratios and Figure-56 15% rating produce the installed Slow increment.", next_action: "Use as fixed Slow p_nom; dispatch remains endogenous." },
  { gate: "2050_PROGRAMMABLE_PERIMETER_STATUS", status: "RESOLVED_BY_EXPLICIT_PROJECT_PERIMETER_DECISION", controlling_reason: "Geothermal included inside the 30-GW efficient programmable benchmark; four PNIEC categories normalize to 21.22821 GW.", next_action: "Retain as explicit project interpretation in model documentation." },
  { gate: "2050_BASE_HIGH_CAPACITY_TABLE_STATUS", status: "RESOLVED", controlling_reason: "30-GW programmable total with 8-GW nuclear, 0.77179-GW geothermal and normalized four-category portfolio.", next_action: "Use fixed p_nom table." },
  { gate: "2050_SLOW_CDP_CAPACITY_STATUS", status: "RESOLVED", controlling_reason: "Corrected CDP loss added proportionally to Base non-nuclear programmable mix; nuclear fixed.", next_action: "Use fixed Slow p_nom; dispatch remains endogenous." },
  { gate: "HYDRO_CAPACITY_SCENARIO_STATUS", status: "FROZEN_CURRENT_FLEET_ALL_SIX_SCENARIOS", controlling_reason: "User-confirmed assumption; 23,294 MW current hydro class matrix copied identically.", next_action: "No future hydro MW research required for scenario capacity." },
  { gate: "HYDRO_OPERATIONAL_PARAMETER_STATUS", status: "PARTIAL", controlling_reason: "PHS zonal e_nom/pump MW, efficiencies, inflows, active reservoir energy and SOC remain unresolved.", next_action: "Resolve in solver-parameter phase; does not block p_nom tables." },
  { gate: "FULL_SOLVER_INPUT_STATUS", status: "INCOMPLETE", controlling_reason: "Hourly load/weather/VRE/hydro inflows, availability, external prices, storage/SOC and interzonal inputs remain outside this phase.", next_action: "Proceed to solver-input construction only after selecting the next empirical parameter gates." },
  { gate: "WORKBOOK_PROMOTION_STATUS", status: "BLOCKED", controlling_reason: "Accepted v2.9 binary provenance remains unresolved; standalone analyst workbook does not promote the canonical workbook.", next_action: "Restore accepted 4a59... binary or approve the semantically verified local baseline." },
];

// ---------------------------------------------------------------------------
// Author all machine-readable CSV outputs
// ---------------------------------------------------------------------------

const authored = [];
authored.push(await authorCsv(removalMask, "RemovalMask", out.removalMask));
authored.push(await authorCsv(removalReconciliation, "RemovalRecon", out.removalReconciliation));
authored.push(await authorCsv(anchorRows, "Anchor", out.anchor));
authored.push(await authorCsv(taxonomyRows, "Taxonomy", out.taxonomy));
authored.push(await authorCsv(crosswalkRows, "Crosswalk", out.crosswalk));
authored.push(await authorCsv(hydroRows, "Hydro", out.hydro));
authored.push(await authorCsv(base2040Rows, "BaseHigh2040", out.base2040));
authored.push(await authorCsv(perimeter2050Rows, "Perimeter2050", out.perimeter2050));
authored.push(await authorCsv(national2050Rows, "National2050", out.national2050));
authored.push(await authorCsv(zonal2050Rows, "Zonal2050", out.zonal2050));
authored.push(await authorCsv(cdpRows, "CDP", out.cdp));
authored.push(await authorCsv(slow2040National, "Slow2040National", out.slow2040National));
authored.push(await authorCsv(slow2040Zonal, "Slow2040Zonal", out.slow2040Zonal));
authored.push(await authorCsv(slow2050National, "Slow2050National", out.slow2050National));
authored.push(await authorCsv(slow2050Zonal, "Slow2050Zonal", out.slow2050Zonal));
authored.push(await authorCsv(longRows, "ScenarioLong", out.long));
authored.push(await authorCsv(gateRows, "Gates", out.gateStatus));

// ---------------------------------------------------------------------------
// Standalone analyst workbook
// ---------------------------------------------------------------------------

const workbook = Workbook.create();
const theme = {
  navy: "#17324D", teal: "#0F5C5E", pale: "#DDEEEE", light: "#F4F7F9", gold: "#D39B2A",
  warn: "#FFF4D6", green: "#DFF1E5", red: "#FDE7E7", text: "#1F2933", grid: "#D6DEE5",
};
const scenarioSheetNames = SCENARIOS.map((item) => `${item.year} ${item.scenario}`);
const blockOrder = [
  { block: "VARIABLE RENEWABLES", technologies: ["SOLAR_PV_ROOFTOP", "SOLAR_PV_UTILITY", "WIND_ONSHORE", "WIND_OFFSHORE"] },
  { block: "HYDRO — MUTUALLY EXCLUSIVE CLASSES", technologies: ["HYDRO_RUN_OF_RIVER", "HYDRO_BASIN", "HYDRO_RESERVOIR", "PUMPED_HYDRO_PURE", "PUMPED_HYDRO_MIXED"] },
  { block: "STORAGE POWER", technologies: ["BESS"] },
  { block: "DISPATCHABLE / PROGRAMMABLE", technologies: ["CCGT", "GT_OCGT", "INTERNAL_COMBUSTION", "STEAM_OTHER_SURVIVING", "OTHER_SURVIVING_THERMAL", "BIOENERGY", "BIOENERGY_CCS", "GAS_CCS", "GAS_OTHER_FOSSIL", "GEOTHERMAL", "NUCLEAR"] },
];
const scenarioSheetMeta = {};

function styleScenarioSheet(sheet, year, scenario) {
  sheet.mergeCells("A1:J1");
  sheet.getRange("A1").values = [[`${year} ${scenario} — installed capacity by MEM market zone (GW)`]];
  sheet.mergeCells("A2:J2");
  sheet.getRange("A2").values = [["Fixed p_nom scenario table. Slow adds the corrected Terna-CDP dispatchable increment; annual dispatch remains endogenous."]];
  sheet.getRange("A1:J1").format = { fill: theme.navy, font: { bold: true, color: "#FFFFFF", fontSize: 16 }, verticalAlignment: "center" };
  sheet.getRange("A2:J2").format = { fill: theme.pale, font: { italic: true, color: theme.navy, fontSize: 10 }, wrapText: true, verticalAlignment: "center" };
  sheet.getRange("A1:J1").format.rowHeight = 28;
  sheet.getRange("A2:J2").format.rowHeight = 34;
  sheet.getRange("A4:J4").values = [["Block", "Technology", ...ZONES, "ITALY"]];
  sheet.getRange("A4:J4").format = { fill: theme.teal, font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true, borders: { preset: "all", style: "thin", color: theme.grid } };
  const scenarioRows = longRows.filter((row) => row.year === year && row.scenario === scenario);
  let currentRow = 5;
  const techRowNumbers = {};
  const blockRanges = {};
  for (const group of blockOrder) {
    const available = group.technologies.filter((tech) => scenarioRows.some((row) => row.technology === tech));
    if (!available.length) continue;
    const start = currentRow;
    for (const technology of available) {
      const zoneValues = ZONES.map((zone) => sum(scenarioRows.filter((row) => row.technology === technology && row.zone === zone).map((row) => row.capacity_MW)) / 1000);
      sheet.getRangeByIndexes(currentRow - 1, 0, 1, 9).values = [[group.block, technology, ...zoneValues]];
      sheet.getCell(currentRow - 1, 9).formulas = [[`=SUM(C${currentRow}:I${currentRow})`]];
      techRowNumbers[technology] = currentRow;
      currentRow += 1;
    }
    const end = currentRow - 1;
    sheet.getRangeByIndexes(currentRow - 1, 0, 1, 2).values = [[`${group.block} TOTAL`, ""]];
    for (let col = 2; col <= 9; col += 1) sheet.getCell(currentRow - 1, col).formulas = [[`=SUM(${letters(col + 1)}${start}:${letters(col + 1)}${end})`]];
    sheet.getRange(`A${currentRow}:J${currentRow}`).format = { fill: theme.light, font: { bold: true, color: theme.navy }, borders: { top: { style: "thin", color: theme.grid }, bottom: { style: "double", color: theme.navy } } };
    blockRanges[group.block] = { start, end, totalRow: currentRow };
    currentRow += 2;
  }
  const finalRow = currentRow - 1;
  sheet.getRange(`A5:J${finalRow}`).format = { font: { fontSize: 10, color: theme.text }, borders: { insideHorizontal: { style: "thin", color: "#E7ECF0" } }, verticalAlignment: "center" };
  sheet.getRange(`C5:J${finalRow}`).format.numberFormat = "0.000";
  sheet.getRange(`A5:A${finalRow}`).format.font = { bold: false, color: "#66727F", fontSize: 9 };
  for (let row = 5; row <= finalRow; row += 1) if (row % 2 === 0) sheet.getRange(`A${row}:J${row}`).format.fill = "#FAFBFC";
  sheet.getRange("A1:A200").format.columnWidth = 33;
  sheet.getRange("B1:B200").format.columnWidth = 31;
  sheet.getRange("C1:J200").format.columnWidth = 13;
  sheet.freezePanes.freezeRows(4);
  sheet.freezePanes.freezeColumns(2);
  scenarioSheetMeta[`${year}|${scenario}`] = { techRowNumbers, blockRanges, finalRow };
}

for (const { year, scenario } of SCENARIOS) {
  const sheet = workbook.worksheets.add(`${year} ${scenario}`);
  styleScenarioSheet(sheet, year, scenario);
}

const anchorsSheet = workbook.worksheets.add("Allocation Anchors");
anchorsSheet.mergeCells("A1:J1");
anchorsSheet.getRange("A1").values = [["Allocation anchors — 2024 post-coal/oil thermal geography and fixed hydro capacity"]];
anchorsSheet.mergeCells("A2:J2");
anchorsSheet.getRange("A2").values = [["Values in GW NET. Bioenergy geography is a separate cross-classification proxy and is not subtracted from the historical conversion matrix."]];
anchorsSheet.getRange("A4:J4").values = [["Anchor", "Technology", ...ZONES, "ITALY"]];
let anchorRow = 5;
for (const technology of [...anchorNationalByTech.keys()]) {
  anchorsSheet.getRangeByIndexes(anchorRow - 1, 0, 1, 9).values = [["POST_COAL_OIL_THERMAL", technology, ...ZONES.map((zone) => Number(anchorRows.find((row) => row.market_zone === zone && row.future_technology === technology)?.surviving_post_coal_oil_capacity_NET_MW || 0) / 1000)]];
  anchorsSheet.getCell(anchorRow - 1, 9).formulas = [[`=SUM(C${anchorRow}:I${anchorRow})`]];
  anchorRow += 1;
}
anchorRow += 1;
for (const technology of ["BIOENERGY", "BIOENERGY_CCS", "GAS_CCS", "GAS_OTHER_FOSSIL", "NUCLEAR"]) {
  anchorsSheet.getRangeByIndexes(anchorRow - 1, 0, 1, 9).values = [["GEOGRAPHY_SHARE", technology, ...ZONES.map((zone) => crosswalkShare(technology, zone))]];
  anchorsSheet.getCell(anchorRow - 1, 9).formulas = [[`=SUM(C${anchorRow}:I${anchorRow})`]];
  anchorRow += 1;
}
anchorRow += 1;
for (const technology of ["HYDRO_RUN_OF_RIVER", "HYDRO_BASIN", "HYDRO_RESERVOIR", "PUMPED_HYDRO_PURE", "PUMPED_HYDRO_MIXED"]) {
  anchorsSheet.getRangeByIndexes(anchorRow - 1, 0, 1, 9).values = [["FIXED_CURRENT_HYDRO", technology, ...ZONES.map((zone) => Number(hydroRows.find((row) => row.market_zone === zone && row.technology === technology)?.capacity_NET_MW || 0) / 1000)]];
  anchorsSheet.getCell(anchorRow - 1, 9).formulas = [[`=SUM(C${anchorRow}:I${anchorRow})`]];
  anchorRow += 1;
}

const nationalSheet = workbook.worksheets.add("National Capacity Logic");
nationalSheet.mergeCells("A1:H1");
nationalSheet.getRange("A1").values = [["National capacity logic — 2040 anchor scaling and 2050 programmable normalization"]];
nationalSheet.getRange("A3:D7").values = [
  ["2040 calculation", "Value", "Unit", "Evidence role"],
  ["Post-coal/oil anchor", anchorTotal / 1000, "GW NET", "Derived from removal mask"],
  ["Terna 2040 envelope", 55, "GW NET", "Terna capacity control"],
  ["Scaling factor", null, "x", "Formula"],
  ["Scaled total", null, "GW NET", "Formula"],
];
nationalSheet.getRange("B6").formulas = [["=B5/B4"]];
nationalSheet.getRange("B7").formulas = [["=B4*B6"]];
nationalSheet.getRange("A10:H10").values = [["2050 technology", "PNIEC TWh", "Initial CF", "Raw GW", "Raw weight", "Normalization envelope GW", "Final GW", "Role"]];
let nrow = 11;
for (const row of final2050National) {
  nationalSheet.getRangeByIndexes(nrow - 1, 0, 1, 8).values = [[row.technology, row.energyTWh, row.cf, row.rawGW, row.rawWeight, envelope2050MW / 1000, row.finalMW / 1000, "Weight normalized to envelope"]];
  nrow += 1;
}
nationalSheet.getRangeByIndexes(nrow - 1, 0, 1, 8).values = [["GEOTHERMAL", "", "", "", "", 30, geothermalTotal / 1000, "Inside 30-GW control"]]; nrow += 1;
nationalSheet.getRangeByIndexes(nrow - 1, 0, 1, 8).values = [["NUCLEAR", 64.2, "Direct capacity", 8, "", 30, 8, "Direct 8-GW capacity"]]; nrow += 1;
nationalSheet.getRangeByIndexes(nrow - 1, 0, 1, 8).values = [["PROGRAMMABLE TOTAL", "", "", "", "", 30, null, "Formula"]];
nationalSheet.getCell(nrow - 1, 6).formulas = [[`=SUM(G11:G${nrow - 1})`]];

const cdpSheet = workbook.worksheets.add("CDP Slow Adjustment");
cdpSheet.mergeCells("A1:J1");
cdpSheet.getRange("A1").values = [["Slow capacity adjustment — source-correct Terna CDP mapping"]];
cdpSheet.mergeCells("A2:J2");
cdpSheet.getRange("A2").values = [["Figure 55: PV 32/180, wind 17/66, electrochemical storage 29/36. Figure 56: illustrative 15% rating implies 0.85 availability."]];
cdpSheet.getRange("A4:J4").values = [["Year", "Resource", "Base GW", "Slow GW", "Loss GW", "CDP numerator", "Installed denominator", "CDP ratio", "Lost CDP GW", "Status"]];
let crow = 5;
const cdpSummaryRows = {};
for (const year of [2040, 2050]) {
  const resourceSpec = { PV: [32, 180], WIND: [17, 66], BESS: [29, 36] };
  const start = crow;
  for (const resource of ["PV", "WIND", "BESS"]) {
    const base = baseResourceCapacityGW[year][resource];
    const [num, den] = resourceSpec[resource];
    cdpSheet.getRangeByIndexes(crow - 1, 0, 1, 10).values = [[year, resource, base, base * 0.8, base * 0.2, num, den, null, null, "ACTIVE"]];
    cdpSheet.getCell(crow - 1, 7).formulas = [[`=F${crow}/G${crow}`]];
    cdpSheet.getCell(crow - 1, 8).formulas = [[`=E${crow}*H${crow}`]];
    crow += 1;
  }
  cdpSheet.getRangeByIndexes(crow - 1, 0, 1, 10).values = [[year, "TOTAL LOST CDP", "", "", "", "", "", "", null, "CONTROLLING"]];
  cdpSheet.getCell(crow - 1, 8).formulas = [[`=SUM(I${start}:I${crow - 1})`]];
  const lostRow = crow;
  crow += 1;
  cdpSheet.getRangeByIndexes(crow - 1, 0, 1, 10).values = [[year, "ADDITIONAL DISPATCHABLE", "", "", "", "", "", PROGRAMMABLE_AVAILABILITY, null, "CONTROLLING"]];
  cdpSheet.getCell(crow - 1, 8).formulas = [[`=I${lostRow}/H${crow}`]];
  cdpSummaryRows[year] = { lostRow, additionalRow: crow };
  crow += 2;
}
cdpSheet.getRangeByIndexes(crow - 1, 0, 3, 10).values = [
  [2050, "LEGACY WIND", 51, 40.8, 10.2, 29, 66, 29 / 66, 10.2 * 29 / 66, "SUPERSEDED"],
  [2050, "LEGACY BESS", baseResourceCapacityGW[2050].BESS, baseResourceCapacityGW[2050].BESS * 0.8, baseResourceCapacityGW[2050].BESS * 0.2, 12, 36, 12 / 36, baseResourceCapacityGW[2050].BESS * 0.2 * 12 / 36, "SUPERSEDED"],
  [2050, "LEGACY ADDITIONAL", "", "", "", "", "", PROGRAMMABLE_AVAILABILITY, legacy2050Lost / PROGRAMMABLE_AVAILABILITY, "SUPERSEDED 18.9449-GW LINEAGE"],
];

const reconSheet = workbook.worksheets.add("Reconciliation");
reconSheet.mergeCells("A1:H1");
reconSheet.getRange("A1").values = [["Scenario reconciliation and double-counting guardrails"]];
reconSheet.getRange("A3:H3").values = [["Scenario", "Thermal / programmable GW", "Target GW", "Difference GW", "Hydro GW", "Nuclear GW", "CDP increment GW", "Status"]];
let rrow = 4;
for (const { year, scenario } of SCENARIOS) {
  const sheetName = `${year} ${scenario}`;
  const meta = scenarioSheetMeta[`${year}|${scenario}`];
  const thermalTotalRow = meta.blockRanges["DISPATCHABLE / PROGRAMMABLE"].totalRow;
  const hydroTotalRow = meta.blockRanges["HYDRO — MUTUALLY EXCLUSIVE CLASSES"].totalRow;
  const nuclearRow = meta.techRowNumbers.NUCLEAR;
  const target = year === 2040 ? 55 + (scenario === "Slow" ? slowAdjustmentByYear[2040].additionalGW : 0) : 30 + (scenario === "Slow" ? slowAdjustmentByYear[2050].additionalGW : 0);
  const increment = scenario === "Slow" ? slowAdjustmentByYear[year].additionalGW : 0;
  reconSheet.getRangeByIndexes(rrow - 1, 0, 1, 8).values = [[sheetName, null, target, null, null, null, increment, null]];
  reconSheet.getCell(rrow - 1, 1).formulas = [[`='${sheetName}'!J${thermalTotalRow}`]];
  reconSheet.getCell(rrow - 1, 3).formulas = [[`=B${rrow}-C${rrow}`]];
  reconSheet.getCell(rrow - 1, 4).formulas = [[`='${sheetName}'!J${hydroTotalRow}`]];
  if (nuclearRow) reconSheet.getCell(rrow - 1, 5).formulas = [[`='${sheetName}'!J${nuclearRow}`]];
  else reconSheet.getCell(rrow - 1, 5).values = [[0]];
  reconSheet.getCell(rrow - 1, 7).formulas = [[`=IF(ABS(D${rrow})<0.0001,"PASS","FAIL")`]];
  rrow += 1;
}
reconSheet.getRange(`A${rrow + 1}:H${rrow + 1}`).merge();
reconSheet.getRange(`A${rrow + 1}`).values = [["Guardrails: no coal/oil future carriers; bioenergy source geography is non-additive to the 2040 conversion anchor; pumped-hydro classes sum within the 23.294-GW hydro control; nuclear is separate."]];

const assumptionsSheet = workbook.worksheets.add("Assumptions");
assumptionsSheet.mergeCells("A1:F1");
assumptionsSheet.getRange("A1").values = [["Controlling assumptions and evidence classifications"]];
const assumptions = [
  ["2040 thermal envelope", 55, "GW NET", "TERNA CAPACITY CONTROL", "Terna/DDS 2040", "Includes applicable thermoelectric categories and geothermal; excludes hydro."],
  ["2050 programmable envelope", 30, "GW", "TERNA ADEQUACY BENCHMARK", "Terna Figure 56", "Includes nuclear and adopted non-hydro programmable geothermal treatment."],
  ["Nuclear", 8, "GW", "DIRECT SOURCE CAPACITY + PROJECT SITING", "PNIEC/Terna", "5 GW NORD; 3 GW CSUD."],
  ["Slow multiplier", 0.8, "x Base", "PROJECT SCENARIO RULE", "Accepted v2.9", "Applies to PV, wind and BESS power."],
  ["CDP PV", 32 / 180, "ratio", "DIRECT TERNA FIGURE 55", "Terna 2050 Figure 55", "Controlling."],
  ["CDP wind", 17 / 66, "ratio", "DIRECT TERNA FIGURE 55", "Terna 2050 Figure 55", "Controlling; 29/66 superseded."],
  ["CDP BESS", 29 / 36, "ratio", "DIRECT TERNA FIGURE 55", "Terna 2050 Figure 55", "Controlling; 12/36 superseded."],
  ["Programmable availability", 0.85, "ratio", "TERNA ILLUSTRATIVE RATING ADOPTED BY PROJECT", "Terna 2050 Figure 56", "15% rating/derating implies 85% installed-to-available conversion."],
  ["Hydro future capacity", 23.294, "GW NET", "PROJECT SCENARIO ASSUMPTION CONFIRMED BY USER", "Current Terna control", "Identical in all six scenarios; operation remains partial."],
  ["Internal commercial Link loss", 1, "efficiency", "ACCOUNTING GUARDRAIL", "DDS demand includes losses", "Do not double-count network losses."],
  ["2050 CF use", "WEIGHTS ONLY", "", "PROJECT DERIVATION", "Approved CF assumptions", "Not realized dispatch constraints."],
  ["Workbook promotion", "BLOCKED", "", "RELEASE GATE", "Accepted v2.9 hash absent", "This standalone workbook does not promote v2.9."],
];
assumptionsSheet.getRange("A3:F3").values = [["Assumption", "Value", "Unit", "Evidence class", "Source", "Guardrail"]];
assumptionsSheet.getRangeByIndexes(3, 0, assumptions.length, 6).values = assumptions;

const sourcesSheet = workbook.worksheets.add("Sources");
sourcesSheet.mergeCells("A1:E1");
sourcesSheet.getRange("A1").values = [["Primary and controlling sources"]];
const sources = [
  ["TERNA_2024_IMPIANTI_GENERAZIONE_TABLE19", "Terna — Impianti di Generazione 2024", "https://download.terna.it/terna/03_IMPIANTI%20DI%20GENERAZIONE_8dec285ed22347a.pdf", "National NET fuel controls and active/inactive classification", "PRIMARY"],
  ["TERNA_2050_PERSPECTIVES_FIG55_56", "Terna — Prospettive di Sviluppo del Sistema Energetico 2050", "https://download.terna.it/terna/Terna_Prospettive_Sviluppo_Sistema_Energetico_2050_Copertura_domanda_elettrica_8de15802728e7d1.pdf", "CDP ratios, 15% rating, ~30-GW programmable benchmark", "PRIMARY"],
  ["ENEL_ESG_FOCUS_MAY2024", "Enel ESG focus for investors, May 2024", "https://www.enel.com/content/dam/enel-com/documenti/investitori/informazioni-finanziarie/2024/esg-focus-for-investors_may2024.pdf", "Coal plant identity and approximate capacity", "OPERATOR"],
  ["EP_PRODUZIONE_SUSTAINABILITY_2024", "EP Produzione — Sustainability report 2024", "https://epproduzione.com/wp-content/uploads/2025/07/BDS-2024-INT-17.07.2025.pdf", "Fiume Santo 599 MW net and coal units", "OPERATOR"],
  ["A2A_SAN_FILIPPO_PLANT_PAGE", "A2A — San Filippo del Mela plant", "https://www.gruppoa2a.it/it/chi-siamo/nostri-impianti/termoelettrici/centrale-san-filippo", "Oil fuel, nominal capacity and units", "OPERATOR"],
  ["ENEL_FUSINA_ENVIRONMENTAL_2025", "Enel — Fusina environmental statement 2025", "https://corporate.enel.it/content/dam/enel-corporate/progetti/documenti/impianti-emas---termoelettrici/fusina/dichiarazione-ambientale-aggiornamento-2025.pdf", "Coal service ended in 2023; prevents false direct 2024 coal attribution", "OPERATOR"],
  ["TERNA_DOWNLOAD_CENTER_THERMOELECTRIC_CAPACITY_2024", "Terna Download Center capacity export", "https://dati.terna.it/en/download-center", "2024 NET province×conversion matrix", "PRIMARY"],
  ["MEM_V2_9_SCENARIO_MATRIX", "Accepted v2.9 scenario content; local binary promotion provenance unresolved", "", "Frozen zonal PV/wind/BESS scenario inputs only", "PROJECT CONTROL"],
];
sourcesSheet.getRange("A3:E3").values = [["Source ID", "Title", "URL", "Use", "Class"]];
sourcesSheet.getRangeByIndexes(3, 0, sources.length, 5).values = sources;

function styleAux(sheet, lastColumn, usedRows) {
  sheet.getRange(`A1:${lastColumn}1`).format = { fill: theme.navy, font: { bold: true, color: "#FFFFFF", fontSize: 15 }, verticalAlignment: "center" };
  sheet.getRange(`A3:${lastColumn}3`).format = { fill: theme.teal, font: { bold: true, color: "#FFFFFF" }, wrapText: true, horizontalAlignment: "center", borders: { preset: "all", style: "thin", color: theme.grid } };
  sheet.getRange(`A4:${lastColumn}${usedRows}`).format = { font: { fontSize: 10, color: theme.text }, wrapText: true, borders: { insideHorizontal: { style: "thin", color: "#E7ECF0" } }, verticalAlignment: "center" };
  sheet.freezePanes.freezeRows(3);
}
styleAux(anchorsSheet, "J", anchorRow);
styleAux(nationalSheet, "H", nrow);
styleAux(cdpSheet, "J", crow + 3);
styleAux(reconSheet, "H", rrow + 2);
styleAux(assumptionsSheet, "F", assumptions.length + 3);
styleAux(sourcesSheet, "E", sources.length + 3);
for (const sheet of [anchorsSheet, nationalSheet, cdpSheet, reconSheet, assumptionsSheet, sourcesSheet]) {
  sheet.getRange("A1:A200").format.columnWidth = 28;
  sheet.getRange("B1:B200").format.columnWidth = 30;
  sheet.getRange("C1:J200").format.columnWidth = 16;
}
anchorsSheet.getRange(`C5:J${anchorRow}`).format.numberFormat = "0.000";
nationalSheet.getRange("B4:G25").format.numberFormat = "0.000000";
cdpSheet.getRange("C5:I30").format.numberFormat = "0.000000";
reconSheet.getRange("B4:G15").format.numberFormat = "0.000000";
sourcesSheet.getRange("C1:C30").format.columnWidth = 72;
sourcesSheet.getRange("D1:D30").format.columnWidth = 48;
assumptionsSheet.getRange("F1:F30").format.columnWidth = 58;
cdpSheet.getRange(`A${crow}:J${crow + 2}`).format = { fill: theme.warn, font: { color: "#7A4D00", italic: true }, wrapText: true };

const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(out.workbook);
const finalWorkbook = await SpreadsheetFile.importXlsx(await FileBlob.load(out.workbook));
const workbookSheets = [...scenarioSheetNames, "Allocation Anchors", "National Capacity Logic", "CDP Slow Adjustment", "Reconciliation", "Assumptions", "Sources"];
const workbookInspects = [];
for (const sheetName of workbookSheets) {
  const sheet = finalWorkbook.worksheets.getItem(sheetName);
  const used = sheet.getUsedRange();
  const inspect = await finalWorkbook.inspect({ kind: "table,formula", sheetId: sheetName, range: used.address, tableMaxRows: 80, tableMaxCols: 14, tableMaxCellChars: 180, maxChars: 25000, options: { maxResults: 1000 } });
  workbookInspects.push({ sheet: sheetName, address: used.address, inspect: inspect.ndjson });
  const blob = await finalWorkbook.render({ sheetName, autoCrop: "all", scale: 1.2, format: "png" });
  await fs.writeFile(path.join(renderDir, `${sheetName.replaceAll(" ", "_")}.png`), new Uint8Array(await blob.arrayBuffer()));
}
const formulaErrors = await finalWorkbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 500 }, summary: "Zonal capacity analyst workbook formula-error scan", maxChars: 20000 });
authored.push({ file: path.relative(phaseRoot, out.workbook).replaceAll("\\", "/"), rows: longRows.length, columns: 10, sha256: await sha256File(out.workbook) });

// ---------------------------------------------------------------------------
// Methodology, registers, manifests, QA and final verification
// ---------------------------------------------------------------------------

const methodology = `# MEM zonal capacity allocation methodology — 2040/2050\n\n` +
`## Outcome\n\nThe six seven-zone installed-capacity tables are complete as a standalone scenario layer. They do not promote or edit the canonical v2.9 workbook and they do not run PyPSA.\n\n` +
`## Corrected Terna CDP mapping\n\nDirect visual inspection of Terna Figure 55 establishes PV 32/180, wind 17/66 and electrochemical storage 29/36. The inherited wind 29/66 and BESS 12/36 mapping is superseded. Figure 56 then uses an illustrative 15% rating for programmable capacity; MEM adopts the corresponding 0.85 available fraction for the Slow installed-capacity conversion.\n\n` +
`The resulting Slow adjustments are:\n\n- 2040 lost CDP: ${round(slowAdjustmentByYear[2040].lostCDPGW, 9)} GW; additional dispatchable capacity: ${round(slowAdjustmentByYear[2040].additionalGW, 9)} GW; Slow thermal total: ${round(55 + slowAdjustmentByYear[2040].additionalGW, 9)} GW.\n- 2050 lost CDP: ${round(slowAdjustmentByYear[2050].lostCDPGW, 9)} GW; additional dispatchable capacity: ${round(slowAdjustmentByYear[2050].additionalGW, 9)} GW; Slow programmable total: ${round(30 + slowAdjustmentByYear[2050].additionalGW, 9)} GW.\n\n` +
`## 2024 coal/oil removal mask\n\nTerna Table 19 controls 4,917.4 MW coal and 1,975.9 MW petroleum products. The mask ties Terna NET province×Condensazione rows to Torrevaldaliga Nord, Federico II, Fiume Santo, Sulcis and San Filippo del Mela using operator evidence. Direct coal attribution is ${round(coalDirectMW, 3)} MW (${round(100 * coalDirectMW / COAL_CONTROL_MW, 2)}%); the ${round(coalResidualMW, 3)}-MW coal residual remains a NORD control band. Direct oil attribution is ${round(oilDirectMW, 3)} MW (${round(100 * oilDirectMW / OIL_CONTROL_MW, 2)}%); the ${round(oilResidualMW, 3)}-MW petroleum residual remains dispersed control bands across oil-compatible conversion rows. Residual bands are not plant records.\n\n` +
`The interrupted all-steam/all-engine heuristic is not used. Fuel class and conversion technology remain separate. Fusina and Monfalcone are not asserted as direct 2024 coal capacity because operator evidence says coal service ended in 2023.\n\n` +
`## 2040 Base/High\n\nThe mutually exclusive 2024 conversion matrix, after the reconciled coal/oil mask, plus direct geothermal, is ${round(anchorTotal / 1000, 9)} GW NET. It is scaled by ${round(SCALE_2040, 12)} to the 55-GW Terna envelope. Hydro is outside that envelope. Bioenergy source/fuel statistics are not separately added or subtracted; they remain embedded within conversion carriers in the 2040 anchor.\n\n` +
`## 2050 programmable perimeter\n\nMEM adopts geothermal inside the 30-GW efficient programmable benchmark. Terna's residual-load discussion subtracts programmable hydro before identifying the remaining need for programmable thermal and/or nuclear, and the 2050 balance refers to other programmable generation including programmable renewables. This is an explicit source-supported project perimeter decision, not a separately published geothermal line in Figure 55.\n\n` +
`Therefore: 30 GW - 8 GW nuclear - ${round(geothermalTotal / 1000, 6)} GW geothermal = ${round(envelope2050MW / 1000, 6)} GW for the four PNIEC energy/CF-derived relative weights. The CFs create weights only and never constrain realized dispatch.\n\n` +
`## Geography\n\n- CCGT and GAS_CCS use combined surviving CCGT CHP+non-CHP 2024 geography.\n- GT/OCGT and GAS_OTHER_FOSSIL use combined surviving GT CHP+non-CHP geography.\n- Bioenergy and Bioenergy+CCS use the current renewable-source bioenergy geography only as a project allocation proxy.\n- Geothermal retains current geography.\n- Nuclear is fixed at 5 GW NORD and 3 GW CSUD.\n- Slow increments preserve the corresponding Base non-nuclear technology weights and the same technology-specific zone shares.\n\n` +
`## Hydro\n\nInstalled hydro is fixed at the current 23.294-GW fleet in every 2040/2050 scenario. The current class allocation is reconciled once to Terna controls and copied unchanged. The remaining PHS e_nom/pump-MW allocation, efficiencies, inflows, active reservoir energy and SOC conventions are operational solver-parameter gates, not capacity-table gates.\n\n` +
`## Model guardrails\n\nAll capacities are fixed and non-extendable. Installed capacity does not prescribe annual generation. The DDS demand perimeter includes network losses, so primary internal commercial Links remain efficiency=1 unless demand is first netted of losses. The canonical v2.9 workbook remains unchanged and promotion remains blocked by its binary provenance gate.\n`;
await fs.writeFile(out.methodology, methodology, "utf8");

let sourceManifest = await readCsvObjects(files.sourceManifest, "Sources");
const sourceRows = [
  { source_id: "TERNA_2024_IMPIANTI_GENERAZIONE_TABLE19", publisher: "Terna S.p.A.", title: "Impianti di Generazione 2024 — Table 19", release_or_year: 2024, acquisition_route: "LOCAL_PRIMARY_PDF", acquisition_date: "2026-09-01", original_uploaded_filename: path.basename(files.terna2024), archived_raw_file: path.relative(phaseRoot, files.terna2024).replaceAll("\\", "/"), byte_size: await fileSize(files.terna2024), raw_sha256: await sha256File(files.terna2024), scope: "National NET thermoelectric capacity by fuel and producing/non-producing classification", evidence_class: "DIRECT SOURCE CAPACITY / TERNA FUEL CONTROL", status: "ACQUIRED_VALIDATED", url: "https://download.terna.it/terna/03_IMPIANTI%20DI%20GENERAZIONE_8dec285ed22347a.pdf" },
  { source_id: "TERNA_2050_PERSPECTIVES_FIG55_56", publisher: "Terna S.p.A.", title: "Prospettive di Sviluppo del Sistema Energetico 2050 — Figures 55-56", release_or_year: 2025, acquisition_route: "LOCAL_PRIMARY_PDF_VISUALLY_VERIFIED", acquisition_date: "2026-09-02", original_uploaded_filename: path.basename(files.terna2050), archived_raw_file: path.relative(phaseRoot, files.terna2050).replaceAll("\\", "/"), byte_size: await fileSize(files.terna2050), raw_sha256: await sha256File(files.terna2050), scope: "CDP resource mapping; illustrative programmable rating; 2050 programmable benchmark", evidence_class: "DIRECT TERNA ADEQUACY EVIDENCE", status: "ACQUIRED_VISUALLY_VERIFIED", url: "https://download.terna.it/terna/Terna_Prospettive_Sviluppo_Sistema_Energetico_2050_Copertura_domanda_elettrica_8de15802728e7d1.pdf" },
  { source_id: "ENEL_ESG_FOCUS_MAY2024", publisher: "Enel S.p.A.", title: "ESG focus for investors — coal phase-out plant map", release_or_year: 2024, acquisition_route: "OFFICIAL_OPERATOR_WEB", acquisition_date: "2026-09-02", original_uploaded_filename: "", archived_raw_file: "", byte_size: "", raw_sha256: "", scope: "Torrevaldaliga Nord, Federico II and Sulcis plant identity/capacity cross-check", evidence_class: "OFFICIAL OPERATOR EVIDENCE", status: "ONLINE_INSPECTED", url: "https://www.enel.com/content/dam/enel-com/documenti/investitori/informazioni-finanziarie/2024/esg-focus-for-investors_may2024.pdf" },
  { source_id: "EP_PRODUZIONE_SUSTAINABILITY_2024", publisher: "EP Produzione", title: "Bilancio di Sostenibilità 2024 — Fiume Santo", release_or_year: 2024, acquisition_route: "OFFICIAL_OPERATOR_WEB", acquisition_date: "2026-09-02", original_uploaded_filename: "", archived_raw_file: "", byte_size: "", raw_sha256: "", scope: "Fiume Santo 599 MW NET, coal, two operating units", evidence_class: "OFFICIAL OPERATOR EVIDENCE", status: "ONLINE_INSPECTED", url: "https://epproduzione.com/wp-content/uploads/2025/07/BDS-2024-INT-17.07.2025.pdf" },
  { source_id: "A2A_SAN_FILIPPO_PLANT_PAGE", publisher: "A2A S.p.A.", title: "Centrale termoelettrica di San Filippo del Mela", release_or_year: "CURRENT PAGE / 2024 STATUS CROSS-CHECK", acquisition_route: "OFFICIAL_OPERATOR_WEB", acquisition_date: "2026-09-02", original_uploaded_filename: "", archived_raw_file: "", byte_size: "", raw_sha256: "", scope: "Oil fuel, nominal capacity and unit history", evidence_class: "OFFICIAL OPERATOR EVIDENCE", status: "ONLINE_INSPECTED", url: "https://www.gruppoa2a.it/it/chi-siamo/nostri-impianti/termoelettrici/centrale-san-filippo" },
  { source_id: "ENEL_FUSINA_ENVIRONMENTAL_2025", publisher: "Enel Produzione S.p.A.", title: "Fusina environmental statement update 2025", release_or_year: 2025, acquisition_route: "OFFICIAL_OPERATOR_WEB", acquisition_date: "2026-09-02", original_uploaded_filename: "", archived_raw_file: "", byte_size: "", raw_sha256: "", scope: "Coal units ended service by 31 December 2023", evidence_class: "OFFICIAL OPERATOR EVIDENCE", status: "ONLINE_INSPECTED", url: "https://corporate.enel.it/content/dam/enel-corporate/progetti/documenti/impianti-emas---termoelettrici/fusina/dichiarazione-ambientale-aggiornamento-2025.pdf" },
  { source_id: "MEM_V2_9_SCENARIO_MATRIX_READ_ONLY", publisher: "MEM project", title: "v2.9 scenario matrices — read-only source for accepted PV/wind/BESS inputs", release_or_year: "v2.9", acquisition_route: "LOCAL_READ_ONLY", acquisition_date: "2026-09-02", original_uploaded_filename: path.basename(files.v29), archived_raw_file: path.relative(phaseRoot, files.v29).replaceAll("\\", "/"), byte_size: await fileSize(files.v29), raw_sha256: await sha256File(files.v29), scope: "Accepted scenario content only; not a workbook-promotion baseline", evidence_class: "PROJECT SCENARIO CONTROL", status: "CONTENT_USED; BINARY_PROMOTION_PROVENANCE_BLOCKED", url: "" },
];
for (const row of sourceRows) upsert(sourceManifest, "source_id", row);
authored.push(await authorCsv(sourceManifest, "Sources", files.sourceManifest));

let derivationManifest = await readCsvObjects(files.derivationManifest, "Derivations");
const derivations = [
  { derivation_id: "DER-ZCA-001", output: "MEM_2024_Coal_Oil_Removal_Mask.csv", evidence_class: "DIRECT PLANT EVIDENCE + TERNA CONTROL RESIDUAL", inputs: "Terna 2024 province×technology NET matrix; Table 19 fuel controls; operator plant evidence", method: "Directly map named large plants to Terna province Condensazione rows; preserve remaining national fuel controls as explicit residual bands.", key_guardrail: "Fuel class is not conversion technology; residual bands are not plant records.", status: "VALIDATED_WITH_CONTROLLED_RESIDUALS" },
  { derivation_id: "DER-ZCA-002", output: "MEM_2024_Post_Coal_Oil_Thermal_Geography_Anchor.csv", evidence_class: "PROJECT DERIVATION", inputs: "Frozen 2024 conversion matrix; approved removal mask; direct geothermal", method: "Subtract reconciled coal/oil mask at zone×technology grain and aggregate to mutually exclusive future carriers.", key_guardrail: "Do not add/subtract 3.586 or 3.8000923 GW bioenergy cross-classifications.", status: "VALIDATED" },
  { derivation_id: "DER-ZCA-003", output: "MEM_2040_Base_High_Thermal_Zonal_Capacity.csv", evidence_class: "TERNA CONTROL + PROJECT DERIVATION", inputs: "Post-coal/oil anchor; 55-GW control", method: `Multiply every anchor cell by ${round(SCALE_2040, 12)}.`, key_guardrail: "Hydro excluded; geothermal included; Base=High thermal.", status: "VALIDATED" },
  { derivation_id: "DER-ZCA-004", output: "MEM_2050_Base_High_National_Dispatchable_Capacity.csv", evidence_class: "PROJECT DERIVATION / TERNA ADEQUACY CONTROL", inputs: "PNIEC TWh; approved CF assumptions; 30-GW benchmark; 8-GW nuclear; 0.77179-GW geothermal", method: "Convert energy/CF to raw weights only; normalize four-category weights to 21.22821 GW.", key_guardrail: "CFs are not dispatch constraints; no 30-15.5136 residual.", status: "VALIDATED" },
  { derivation_id: "DER-ZCA-005", output: "MEM_Slow_CDP_Dispatchable_Capacity_Adjustment.csv", evidence_class: "TERNA CDP EVIDENCE + PROJECT DERIVATION", inputs: "Frozen Base PV/wind/BESS; Slow=80%; Figure 55 ratios; Figure 56 15% rating", method: "Lost CDP = losses×(32/180,17/66,29/36); additional installed dispatchable = lost CDP/0.85.", key_guardrail: "Legacy wind29/66 and BESS12/36 are superseded.", status: "VALIDATED" },
  { derivation_id: "DER-ZCA-006", output: "MEM_2040_2050_Zonal_Installed_Capacity_By_Scenario.csv", evidence_class: "SCENARIO INPUT CONSTRUCTION", inputs: "All zonal capacity outputs; frozen v2.9 PV/wind/BESS; fixed current hydro", method: "Combine mutually exclusive renewable, storage-power, hydro-class and dispatchable components; preserve Slow increment as a separate component.", key_guardrail: "No capacity expansion; no annual generation quota; no hydro/PHS/nuclear/bioenergy double count.", status: "VALIDATED" },
];
for (const row of derivations) upsert(derivationManifest, "derivation_id", row);
authored.push(await authorCsv(derivationManifest, "Derivations", files.derivationManifest));

let decisions = await readCsvObjects(files.decisionRegister, "Decisions");
const decisionRows = [
  { decision_id: "D-ZCA-001", status: "RESOLVED", decision: "Correct Terna Figure-55 CDP mapping", evidence_class: "DIRECT TERNA ADEQUACY EVIDENCE", rule: "Use PV32/180, wind17/66, BESS29/36.", value_or_artifact: "MEM_Slow_CDP_Dispatchable_Capacity_Adjustment.csv", model_effect: "Controls installed Slow dispatchable increment." },
  { decision_id: "D-ZCA-002", status: "SUPERSEDED", decision: "Legacy CDP resource mapping", evidence_class: "LINEAGE ONLY", rule: "Wind29/66 and BESS12/36 must never be active.", value_or_artifact: "Legacy 18.9449-GW result", model_effect: "No model effect; preserved only as superseded regression lineage." },
  { decision_id: "D-ZCA-003", status: "RESOLVED", decision: "Programmable installed-to-available conversion", evidence_class: "TERNA ILLUSTRATIVE RATING ADOPTED BY PROJECT", rule: "Terna Figure 56 illustrates a 15% rating; use 0.85 available fraction for the Slow conversion.", value_or_artifact: 0.85, model_effect: "Additional p_nom = lost CDP / 0.85." },
  { decision_id: "D-ZCA-004", status: "RESOLVED", decision: "Hydro scenario capacity", evidence_class: "PROJECT SCENARIO ASSUMPTION CONFIRMED BY USER", rule: "Current hydro MW is identical in all six future scenarios.", value_or_artifact: "MEM_Current_Hydro_Capacity_By_Zone_Class_For_Future_Scenarios.csv", model_effect: "Freezes hydro p_nom; operational parameters remain separate." },
  { decision_id: "D-ZCA-005", status: "RESOLVED_WITH_CONTROLLED_RESIDUALS", decision: "Coal/oil phase-out mask", evidence_class: "DIRECT PLANT EVIDENCE + TERNA CONTROL RESIDUAL", rule: "Use direct named-plant Terna NET rows where strong; retain national residual bands rather than invent units.", value_or_artifact: "MEM_2024_Coal_Oil_Removal_Mask.csv", model_effect: "Removes all coal/oil capacity nationally while exposing unresolved physical attribution." },
  { decision_id: "D-ZCA-006", status: "RESOLVED", decision: "2050 geothermal perimeter", evidence_class: "SOURCE-SUPPORTED PROJECT PERIMETER DECISION", rule: "Include current geothermal inside the 30-GW efficient programmable benchmark.", value_or_artifact: "Four-category envelope=21.22821 GW", model_effect: "30=8 nuclear+0.77179 geothermal+21.22821 four-category portfolio." },
  { decision_id: "D-ZCA-007", status: "RESOLVED", decision: "Slow technology/geography allocation", evidence_class: "PROJECT SCENARIO ALLOCATION", rule: "Allocate increment by Base non-nuclear technology weights and the same technology-specific 2024 geography.", value_or_artifact: "Slow national/zonal CSVs", model_effect: "Only total p_nom changes; geography and mix proportions are preserved." },
];
for (const row of decisionRows) upsert(decisions, "decision_id", row);
authored.push(await authorCsv(decisions, "Decisions", files.decisionRegister));

let gaps = await readCsvObjects(files.gapRegister, "Gaps");
const gapRows = [
  { gap_id: "GAP-ZCA-001", status: "CONTROLLED RESIDUAL — DOES NOT BLOCK TABLES", artifact_or_decision: "Coal/oil residual plant attribution", evidence_class: "TERNA CONTROL RESIDUAL", authoritative_source: "GOGPT/GCPT or equivalent plant evidence", local_search_result: "Neither complete Aug-2026 GOGPT nor Jul-2026 GCPT workbook is present.", exact_acquisition_or_decision: "Supply official trackers later to refine residual bands; do not change Terna national controls.", required_fields_or_controls: `Coal residual ${coalResidualMW} MW; petroleum physical residual ${oilResidualMW} MW`, acceptance_test: "Residual bands either mapped with compatible NET evidence or retained transparently.", blocks: "Full 2026 physical fleet; does not block six scenario capacity tables" },
  { gap_id: "GAP-ZCA-002", status: "PARTIAL — OPERATIONAL PARAMETER GATE", artifact_or_decision: "Hydro operational parameters", evidence_class: "MODEL PARAMETER REQUIRED", authoritative_source: "Terna/plant/hydrological evidence", local_search_result: "Installed hydro capacity is frozen; operational e_nom/pump/efficiency/inflow/SOC remain incomplete.", exact_acquisition_or_decision: "Resolve during solver-parameter phase.", required_fields_or_controls: "PHS zonal e_nom and pump MW; efficiencies; active reservoir energy; inflow; SOC", acceptance_test: "Physically defined and reconciled model parameters", blocks: "Full solver input only" },
  { gap_id: "GAP-ZCA-003", status: "BLOCKED — RELEASE MANAGEMENT", artifact_or_decision: "Canonical workbook promotion", evidence_class: "WORKBOOK PROVENANCE", authoritative_source: "Accepted SHA-256 4a59... binary", local_search_result: "Accepted binary not found; local c4ca... semantic evidence exists.", exact_acquisition_or_decision: "Restore accepted binary or explicitly approve a semantically verified baseline.", required_fields_or_controls: "Controlling workbook provenance", acceptance_test: "Approved promotion baseline", blocks: "v2.9.1 only; not standalone scenario workbook" },
  { gap_id: "GAP-ZCA-004", status: "REQUIRES LATER SOLVER-INPUT PHASE", artifact_or_decision: "Chronological solver inputs", evidence_class: "RUNTIME GATE", authoritative_source: "Separate data phases", local_search_result: "Not in scope.", exact_acquisition_or_decision: "Acquire/freeze weather year, hourly load/VRE/hydro/external prices, availability and SOC conventions.", required_fields_or_controls: "Frozen data contract files", acceptance_test: "Complete-year and model-balance QA", blocks: "Full annual PyPSA run" },
];
for (const row of gapRows) upsert(gaps, "gap_id", row);
authored.push(await authorCsv(gaps, "Gaps", files.gapRegister));

const qaArtifact = await authorCsv(qaRows, "QA", out.qaCsv);
authored.push(qaArtifact);

const verification = {
  generated_at: new Date().toISOString(),
  controlling_statuses: Object.fromEntries(gateRows.map((row) => [row.gate, row.status])),
  frozen_input_row_counts: { capacity: historicalCapacity.length, generation: historicalGeneration.length, storage: historicalStorage.length },
  source_corrected_CDP: { PV: CDP.PV, WIND: CDP.WIND, BESS: CDP.BESS, legacy_WIND: LEGACY_CDP.WIND, legacy_BESS: LEGACY_CDP.BESS, programmable_availability: PROGRAMMABLE_AVAILABILITY },
  slow_adjustments_GW: {
    2040: { lost_CDP: slowAdjustmentByYear[2040].lostCDPGW, additional_dispatchable: slowAdjustmentByYear[2040].additionalGW, total_thermal: 55 + slowAdjustmentByYear[2040].additionalGW },
    2050: { lost_CDP: slowAdjustmentByYear[2050].lostCDPGW, additional_dispatchable: slowAdjustmentByYear[2050].additionalGW, total_programmable: 30 + slowAdjustmentByYear[2050].additionalGW, legacy_additional_superseded: legacy2050Lost / PROGRAMMABLE_AVAILABILITY },
  },
  removal_mask: { coal_control_MW: COAL_CONTROL_MW, coal_direct_MW: coalDirectMW, coal_residual_MW: coalResidualMW, oil_control_MW: OIL_CONTROL_MW, oil_direct_MW: oilDirectMW, oil_residual_MW: oilResidualMW },
  anchor: { MW: anchorTotal, scaling_factor_to_55GW: SCALE_2040 },
  programmable_2050: { total_MW: 30000, nuclear_MW: 8000, geothermal_MW: geothermalTotal, four_category_envelope_MW: envelope2050MW, geothermal_inside: true },
  hydro: { current_and_all_scenarios_MW: hydroControls.total, status: "CAPACITY_FROZEN; OPERATIONAL_PARTIAL" },
  scenario_long_rows: longRows.length,
  qa: { checks: qaRows.length, passes: qaRows.filter((row) => row.status === "PASS").length, failures: qaRows.filter((row) => row.status === "FAIL").length },
  workbook: { path: path.relative(phaseRoot, out.workbook).replaceAll("\\", "/"), sha256: await sha256File(out.workbook), sheets: workbookSheets, formula_error_scan: formulaErrors.ndjson, inspections: workbookInspects },
  authored_files: authored,
  tracker_presence_check: "GOGPT_AUG2026_ABSENT; GCPT_JUL2026_ABSENT; checked once in interrupted/resumed phase",
  canonical_v29_mutated: false,
  full_pypsa_run_executed: false,
};
await fs.writeFile(out.verification, `${JSON.stringify(verification, null, 2)}\n`, "utf8");

if (verification.qa.failures) throw new Error(`${verification.qa.failures} QA checks failed.`);

console.log(JSON.stringify({
  status: "COMPLETE",
  outputs: authored,
  workbook: out.workbook,
  methodology: out.methodology,
  verification: out.verification,
  metrics: verification,
}, null, 2));

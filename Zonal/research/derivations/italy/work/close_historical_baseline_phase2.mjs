import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { Workbook, SpreadsheetFile, FileBlob } from "@oai/artifact-tool";
import { detectTernaDecimalConventions, parseTernaNumber } from "../scripts/terna_numeric_parser.mjs";

const phaseRoot = path.resolve(import.meta.dirname, "..");
const hbRoot = path.join(phaseRoot, "historical_baseline");
const normalizedDir = path.join(hbRoot, "normalized");
const analysisDir = path.join(hbRoot, "analysis");
const manifestDir = path.join(hbRoot, "manifests");
const qaDir = path.join(hbRoot, "qa");
const docsDir = path.join(phaseRoot, "docs");
const rawHistoricalDir = path.join(phaseRoot, "raw", "terna", "historical_2019_2025_api");
const rawReportsDir = path.join(phaseRoot, "raw", "official_reports");
const canonicalZones = ["NORD", "CNOR", "CSUD", "SUD", "CALA", "SICI", "SARD"];
const years = [2019, 2020, 2021, 2022, 2023, 2024];
const round = (value, digits = 9) => value === "" || value === null || value === undefined ? "" : Number(Number(value).toFixed(digits));
const num = (value) => value === "" || value === null || value === undefined ? 0 : Number(value);
const boolText = (value) => String(value).toLowerCase();
const sha256File = async (file) => crypto.createHash("sha256").update(await fs.readFile(file)).digest("hex");
const sum = (values) => values.reduce((total, value) => total + num(value), 0);
const unique = (values) => [...new Set(values.map((value) => String(value)))].sort();
const groupRows = (rows, keyFn) => {
  const groups = new Map();
  for (const row of rows) {
    const key = keyFn(row);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  return groups;
};

async function readCsvObjects(file, sheetName) {
  const workbook = await Workbook.fromCSV(await fs.readFile(file, "utf8"), { sheetName });
  const values = workbook.worksheets.getItem(sheetName).getUsedRange().values;
  const headers = values[0].map(String);
  return values.slice(1).filter((row) => row.some((value) => value !== null && value !== "")).map((row) => Object.fromEntries(headers.map((header, index) => [header, row[index] ?? ""])));
}

const csvEscape = (value) => {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
};
const matrix = (rows) => {
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
async function authorCsv(rows, sheetName, file) {
  const { headers, values } = matrix(rows);
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add(sheetName);
  sheet.getRangeByIndexes(0, 0, rows.length + 1, headers.length).values = [headers, ...values];
  const address = `A1:${letters(headers.length)}${rows.length + 1}`;
  await workbook.inspect({ kind: "table", sheetId: sheetName, range: address, tableMaxRows: 3, tableMaxCols: Math.min(headers.length, 24), maxChars: 5000 });
  const csv = `${[headers, ...values].map((row) => row.map(csvEscape).join(",")).join("\r\n")}\r\n`;
  await fs.writeFile(file, csv, "utf8");
  const reopened = await Workbook.fromCSV(await fs.readFile(file, "utf8"), { sheetName });
  await reopened.inspect({ kind: "table", sheetId: sheetName, range: address, tableMaxRows: 3, tableMaxCols: Math.min(headers.length, 24), maxChars: 5000 });
  return { file: path.relative(phaseRoot, file).replaceAll("\\", "/"), rows: rows.length, columns: headers.length, sha256: await sha256File(file) };
}

let capacity = await readCsvObjects(path.join(normalizedDir, "MEM_Historical_Capacity_By_Zone_Technology.csv"), "Capacity");
let generation = await readCsvObjects(path.join(normalizedDir, "MEM_Historical_Generation_By_Zone_Technology.csv"), "Generation");
let taxonomy = await readCsvObjects(path.join(normalizedDir, "MEM_HISTORICAL_TECHNOLOGY_TAXONOMY.csv"), "Taxonomy");
let sourceManifest = await readCsvObjects(path.join(manifestDir, "MEM_HISTORICAL_BASELINE_SOURCE_MANIFEST.csv"), "SourceManifest");
let derivationManifest = await readCsvObjects(path.join(manifestDir, "MEM_HISTORICAL_BASELINE_DERIVATION_MANIFEST.csv"), "DerivationManifest");
const edgeAudit = JSON.parse(await fs.readFile(path.join(qaDir, "PHASE2_EDGE_CASE_AUDIT.json"), "utf8"));

// Freeze the v2026.3 taxonomy and make every value used by the canonical tables explicit.
taxonomy = taxonomy.filter((row) => row.code !== "WIND_AGGREGATE_UNSPLIT");
for (const row of taxonomy) row.taxonomy_version = "MEM_HISTORICAL_TECHNOLOGY_TAXONOMY_V2026_3";
const ensureTaxonomyRow = (row) => {
  const existing = taxonomy.find((item) => item.taxonomy_dimension === row.taxonomy_dimension && item.code === row.code);
  if (existing) Object.assign(existing, row, { taxonomy_version: "MEM_HISTORICAL_TECHNOLOGY_TAXONOMY_V2026_3" });
  else taxonomy.push({ taxonomy_version: "MEM_HISTORICAL_TECHNOLOGY_TAXONOMY_V2026_3", ...row });
};
ensureTaxonomyRow({ taxonomy_dimension: "TECHNOLOGY", code: "WIND_TOTAL_CONTROL", family: "VARIABLE_RENEWABLE_CONTROL", label: "Terna aggregate wind source control", chp_flag: false, storage_flag: false, dispatchability_class: "VARIABLE_RENEWABLE", default_fuel_source: "WIND", perimeter_role: "SOURCE_CONTROL_NON_MODEL_COMPONENT", technology_and_fuel_separate_rule: "YES", status: "CONTROL_ROW_NOT_MODEL_COMPONENT" });
ensureTaxonomyRow({ taxonomy_dimension: "TECHNOLOGY", code: "HYDRO_RESERVOIR_INCLUDING_EVENTUAL_PUMPING_CONTROL", family: "HYDRO_CONTROL", label: "Terna Serbatoio source detail including eventual pumping", chp_flag: false, storage_flag: "MIXED_INCLUDES_PUMPED_HYDRO", dispatchability_class: "HYDRO_DISPATCHABLE", default_fuel_source: "WATER", perimeter_role: "NON_ADDITIVE_SOURCE_CONTROL_PENDING_PUMPING_SEPARATION", technology_and_fuel_separate_rule: "YES", status: "CONTROL_ROW_NOT_PURE_RESERVOIR_MODEL_COMPONENT" });
for (const [code, label] of [["GEOTHERMAL_HEAT", "Geothermal heat"], ["WATER", "Water"], ["SOLAR", "Solar"], ["WIND", "Wind"], ["ELECTRICITY", "Electricity"]]) {
  ensureTaxonomyRow({ taxonomy_dimension: "FUEL_SOURCE", code, family: "FUEL_SOURCE", label, chp_flag: "N/A", storage_flag: "N/A", dispatchability_class: "N/A", default_fuel_source: code, perimeter_role: "SEPARATE_FUEL_SOURCE_DIMENSION", technology_and_fuel_separate_rule: "YES", status: "ALLOWED_DIMENSION_VALUE" });
}
taxonomy.sort((a, b) => a.taxonomy_dimension.localeCompare(b.taxonomy_dimension) || a.code.localeCompare(b.code));
const techTaxonomy = new Map(taxonomy.filter((row) => row.taxonomy_dimension === "TECHNOLOGY").map((row) => [row.code, row]));

// The official Hydric label is not a clean reservoir component because it explicitly includes eventual pumping.
for (const row of generation) {
  if (row.technology === "HYDRO_RESERVOIR" && String(row.status).includes("INCLUDES_EVENTUAL_PUMPING")) {
    row.technology = "HYDRO_RESERVOIR_INCLUDING_EVENTUAL_PUMPING_CONTROL";
  }
}

// Split the authoritative Terna wind control with direct 30 MW Beleolico capacity evidence.
const windCapacityControls = capacity.filter((row) => row.technology === "WIND_TOTAL_CONTROL");
capacity = capacity.filter((row) => !["WIND_ONSHORE", "WIND_OFFSHORE"].includes(row.technology));
for (const row of windCapacityControls) {
  row.perimeter_role = "SOURCE_CONTROL_NON_ADDITIVE_OVER_WIND_COMPONENTS";
  row.additive_to_system_total = false;
  row.status = "ACQUIRED_TERNA_WIND_TOTAL_CONTROL; COMPONENT_CAPACITY_SPLIT_RESOLVED";
  const offshore = Number(row.year) >= 2022 && row.market_zone === "SUD" ? 30 : 0;
  const total = Number(row.capacity_NET_MW);
  if (offshore > total + 1e-9) throw new Error(`Offshore wind exceeds Terna control in ${row.year}/${row.market_zone}.`);
  const shared = {
    ...row,
    perimeter_layer: "WIND_COMPONENT_ALLOCATION",
    perimeter_role: "PRIMARY_ADDITIVE_COMPONENT",
    additive_to_system_total: true,
    source_id: `${row.source_id}|RENEXIA_BELEOLICO_PRIMARY_SOURCE`,
    evidence_class: "PROJECT DERIVATION FROM DIRECT TERNA CAPACITY CONTROL AND PRIMARY PLANT EVIDENCE",
  };
  capacity.push({ ...shared, technology: "WIND_ONSHORE", capacity_NET_MW: round(total - offshore), status: offshore ? "DERIVED_AS_TERNA_WIND_CONTROL_MINUS_30_MW_BELEOLICO" : "IDENTICAL_TO_TERNA_WIND_CONTROL; NO_OPERATIONAL_OFFSHORE_EVIDENCE" });
  capacity.push({ ...shared, technology: "WIND_OFFSHORE", capacity_NET_MW: offshore, status: offshore ? "DIRECT_30_MW_BELEOLICO_CAPACITY_WITH_TERNA_TOTAL_CONTROL" : "NO_OPERATIONAL_OFFSHORE_CAPACITY" });
}

const capacityKey = new Map(capacity.map((row) => [`${row.year}\u0000${row.market_zone}\u0000${row.technology}`, row.capacity_NET_MW]));
const windGenerationControls = generation.filter((row) => row.technology === "WIND_TOTAL_CONTROL");
generation = generation.filter((row) => !["WIND_ONSHORE", "WIND_OFFSHORE"].includes(row.technology));
for (const row of windGenerationControls) {
  row.perimeter_role = "PRIMARY_ADDITIVE_TERNA_WIND_ENERGY_CONTROL";
  row.additive_to_system_total = true;
  row.status = Number(row.year) >= 2022 && row.market_zone === "SUD"
    ? "ACQUIRED_CONTROL; ONSHORE_OFFSHORE ENERGY SPLIT CONTROLLED RESIDUAL NON-MATERIAL"
    : "ACQUIRED_CONTROL; COMPONENT IDENTITY RESOLVED";
  const unresolved = Number(row.year) >= 2022 && row.market_zone === "SUD";
  const shared = {
    ...row,
    perimeter_layer: "WIND_COMPONENT_ALLOCATION",
    perimeter_role: "NON_ADDITIVE_COMPONENT_DETAIL; TERNA WIND TOTAL REMAINS ADDITIVE CONTROL",
    additive_to_system_total: false,
    source_id: `${row.source_id}|RENEXIA_BELEOLICO_PRIMARY_SOURCE`,
    evidence_class: unresolved ? "CONTROLLED RESIDUAL - NON-MATERIAL" : "PROJECT DERIVATION FROM DIRECT SOURCE ENERGY",
    produced_heat_GWh: "",
    electricity_to_heat_ratio: "",
  };
  const onshoreCap = num(capacityKey.get(`${row.year}\u0000${row.market_zone}\u0000WIND_ONSHORE`));
  const offshoreCap = num(capacityKey.get(`${row.year}\u0000${row.market_zone}\u0000WIND_OFFSHORE`));
  const onshoreGeneration = unresolved ? "" : Number(row.generation_NET_GWh);
  const offshoreGeneration = unresolved ? "" : 0;
  generation.push({ ...shared, technology: "WIND_ONSHORE", generation_NET_GWh: onshoreGeneration, matching_capacity_NET_MW: onshoreCap, observed_CF: onshoreGeneration !== "" && onshoreCap > 0 ? round(onshoreGeneration / (onshoreCap * 8.76)) : "", status: unresolved ? "CONTROLLED RESIDUAL - NON-MATERIAL; DIRECT ANNUAL COMPONENT ENERGY UNAVAILABLE" : "COMPONENT EQUALS TERNA WIND CONTROL; NO OFFSHORE OPERATION" });
  generation.push({ ...shared, technology: "WIND_OFFSHORE", generation_NET_GWh: offshoreGeneration, matching_capacity_NET_MW: offshoreCap, observed_CF: "", status: unresolved ? "CONTROLLED RESIDUAL - NON-MATERIAL; 30 MW CAPACITY DIRECT, OBSERVED ANNUAL ENERGY UNAVAILABLE" : "ZERO BEFORE OFFSHORE COMMISSIONING OR OUTSIDE SUD" });
}

// Attach exact taxonomy invariants to the canonical data rows.
for (const row of [...capacity, ...generation]) {
  const invariant = techTaxonomy.get(row.technology);
  if (!invariant) throw new Error(`Technology ${row.technology} is absent from taxonomy.`);
  row.technology_family = invariant.family;
  row.chp_flag = invariant.chp_flag;
  row.storage_flag = invariant.storage_flag;
  row.dispatchability_class = invariant.dispatchability_class;
}
capacity.sort((a, b) => Number(a.year) - Number(b.year) || canonicalZones.indexOf(a.market_zone) - canonicalZones.indexOf(b.market_zone) || a.technology.localeCompare(b.technology) || String(a.perimeter_layer).localeCompare(String(b.perimeter_layer)));
generation.sort((a, b) => Number(a.year) - Number(b.year) || canonicalZones.indexOf(a.market_zone) - canonicalZones.indexOf(b.market_zone) || a.technology.localeCompare(b.technology) || String(a.perimeter_layer).localeCompare(String(b.perimeter_layer)));

// Resolve renewable blank semantics at the visible observation grain.
const normalizeName = (value) => String(value ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
const blankSemantics = [];
for (const year of years) {
  const capPayload = JSON.parse(await fs.readFile(path.join(rawHistoricalDir, `renewable-source-capacity_${year}_RAW.json`), "utf8"));
  const prodPayload = JSON.parse(await fs.readFile(path.join(rawHistoricalDir, `renewable-sources-production_${year}_RAW.json`), "utf8"));
  const capRows = capPayload.renewable_sources.filter((row) => row.capacity_type === "Netta");
  const prodRows = prodPayload.renewable_sources.filter((row) => row.production_type === "Netta");
  const capConvention = detectTernaDecimalConventions(capRows.map((row) => row.efficient_power_MW).filter((value) => value !== null && value !== undefined && String(value).trim() !== ""));
  const prodConvention = detectTernaDecimalConventions(prodRows.map((row) => row.production_GWh).filter((value) => value !== null && value !== undefined && String(value).trim() !== ""));
  const keyFn = (row, sourceField) => [row.year, normalizeName(row.region), normalizeName(row.province), normalizeName(row[sourceField])].join("\u0000");
  const prodGroups = groupRows(prodRows, (row) => keyFn(row, "renewable_source"));
  const capGroups = groupRows(capRows, (row) => keyFn(row, "source"));
  for (const rows of capGroups.values()) {
    const numeric = rows.filter((row) => row.efficient_power_MW !== null && row.efficient_power_MW !== undefined && String(row.efficient_power_MW).trim() !== "");
    const blanks = rows.filter((row) => !numeric.includes(row));
    if (!blanks.length) continue;
    const prodGroup = prodGroups.get(keyFn(rows[0], "source")) ?? [];
    const reportedCapacity = sum(numeric.map((row) => parseTernaNumber(row.efficient_power_MW, capConvention).value));
    const production = sum(prodGroup.map((row) => parseTernaNumber(row.production_GWh, prodConvention).value));
    const material = numeric.length === 0 && production > 1;
    blankSemantics.push({ year, source: rows[0].source, region: rows[0].region, province: rows[0].province, source_row_count: rows.length, numeric_row_count: numeric.length, blank_row_count: blanks.length, raw_blank_preserved: true, aggregation_value_for_each_blank_MW: 0, reported_numeric_capacity_sum_MW: round(reportedCapacity), matching_net_generation_GWh: round(production), semantic_status: numeric.length ? "MIXED_VISIBLE_GRAIN: BLANK HIDDEN RECORDS = NO ADDITIONAL REPORTED CAPACITY" : production > 0 ? "CONTROLLED RESIDUAL - NON-MATERIAL: ALL-BLANK CAPACITY WITH IMMATERIAL POSITIVE GENERATION" : "NO_REPORTED_CAPACITY", material_exception: material, evidence: "Reported-value sums retained; blanks never text-normalized or punctuation-stripped; material threshold 1 GWh/year for freeze review." });
  }
}

// Build the separate historical storage layer.
const hydroControls = {
  2019: { totalHydroNetMW: 22541.1, pumpedHydroNetMW: 7244.0, sourceId: "TERNA_2019_IMPIANTI_DI_GENERAZIONE_TABLE_14" },
  2020: { totalHydroNetMW: 22695.0, pumpedHydroNetMW: 7252.0, sourceId: "TERNA_2020_ANNUARIO_STATISTICO_TABLE_14" },
  2021: { totalHydroNetMW: 22749.7, pumpedHydroNetMW: 7220.9, sourceId: "TERNA_2021_ANNUARIO_STATISTICO_TABLE_14" },
  2022: { totalHydroNetMW: 22860.8, pumpedHydroNetMW: 7262.1, sourceId: "TERNA_2022_IMPIANTI_GENERAZIONE_TABLE_14" },
  2023: { totalHydroNetMW: 22912.0, pumpedHydroNetMW: 7252.0, sourceId: "TERNA_2023_ANNUARIO_STATISTICO_TABLE_14" },
  2024: { totalHydroNetMW: 23294.0, pumpedHydroNetMW: 7252.3, sourceId: "TERNA_2024_IMPIANTI_DI_GENERAZIONE_TABLE_14" },
};
const storage = [];
for (const year of years) {
  storage.push({ year, market_zone: "", spatial_scope: "NATIONAL_CONTROL_ONLY", technology: "PUMPED_HYDRO", technology_family: "STORAGE", chp_flag: false, storage_flag: true, dispatchability_class: "STORAGE", discharge_power_MW: hydroControls[year].pumpedHydroNetMW, charge_power_MW: "", energy_capacity_MWh: "", roundtrip_efficiency: "", storage_duration_h: "", capacity_basis: "NET ELECTRIC POWER - OFFICIAL TERNA NATIONAL PUMPED-HYDRO CONTROL", source_id: hydroControls[year].sourceId, evidence_class: "DIRECT SOURCE CAPACITY", status: year === 2024 ? "NATIONAL_CONTROL; SEE 2024 ZONAL PHYSICAL ALLOCATION ROWS" : "NATIONAL_CONTROL_ONLY; HISTORICAL ZONAL/ENERGY ATTRIBUTES NOT RECONSTRUCTED", additive_control: false });
}

const hydroInventoryFile = path.join(phaseRoot, "raw", "hydro_physical", "Italian_Hydropower_Programmable_Plants_Database_v2_RAW.xlsx");
const hydroInventorySha = await sha256File(hydroInventoryFile);
const hydroWorkbook = await SpreadsheetFile.importXlsx(await FileBlob.load(hydroInventoryFile));
const plantsValues = hydroWorkbook.worksheets.getItem("List of Plants").getUsedRange().values;
const plantsHeaders = plantsValues[0].map((value) => String(value ?? ""));
const plantObjects = plantsValues.slice(1).filter((row) => row.some((value) => value !== null && value !== "")).map((row) => Object.fromEntries(plantsHeaders.map((header, index) => [header, row[index] ?? ""])));
const regionCodeToZone = new Map([[1, "NORD"], [2, "NORD"], [3, "NORD"], [4, "NORD"], [5, "NORD"], [6, "NORD"], [7, "NORD"], [8, "NORD"], [9, "CNOR"], [10, "CSUD"], [11, "CNOR"], [12, "CSUD"], [13, "CSUD"], [14, "SUD"], [15, "CSUD"], [16, "SUD"], [17, "SUD"], [18, "CALA"], [19, "SICI"], [20, "SARD"]]);
const physicalPumpedByZone = Object.fromEntries(canonicalZones.map((zone) => [zone, { chargeMW: 0, energyMWh: 0, plantCount: 0 }]));
for (const plant of plantObjects.filter((row) => row.type === "HPHS")) {
  const zone = regionCodeToZone.get(Number(plant.region));
  if (!zone) throw new Error(`Unmapped hydro inventory region code ${plant.region}.`);
  physicalPumpedByZone[zone].chargeMW += num(plant["pumping_[MW]"]);
  physicalPumpedByZone[zone].energyMWh += num(plant["storage_capacity_[GWh]"]) * 1000;
  physicalPumpedByZone[zone].plantCount += 1;
}
const grossPumpedByZone = { NORD: 4721.5, CNOR: 0, CSUD: 1741.3, SUD: 0, CALA: 0, SICI: 583.3, SARD: 241.5 };
const grossPurePumpedByZone = { NORD: 2403.0, CNOR: 0, CSUD: 1000.0, SUD: 0, CALA: 0, SICI: 583.3, SARD: 0 };
const grossPumpedDisplayedSum = sum(Object.values(grossPumpedByZone));
const totalPumpedNet2024 = hydroControls[2024].pumpedHydroNetMW;
const renewableHydro2024 = sum(capacity.filter((row) => Number(row.year) === 2024 && row.technology === "HYDRO_TOTAL_CONTROL").map((row) => row.capacity_NET_MW));
const purePumpedNet2024 = round(hydroControls[2024].totalHydroNetMW - renewableHydro2024);
const totalPumpedNetByZone = Object.fromEntries(canonicalZones.map((zone) => [zone, round(totalPumpedNet2024 * grossPumpedByZone[zone] / grossPumpedDisplayedSum)]));
const purePumpedNetByZone = Object.fromEntries(canonicalZones.map((zone) => [zone, 0]));
purePumpedNetByZone.SICI = totalPumpedNetByZone.SICI;
const remainingPure = purePumpedNet2024 - purePumpedNetByZone.SICI;
const remainingPureGross = grossPurePumpedByZone.NORD + grossPurePumpedByZone.CSUD;
purePumpedNetByZone.NORD = round(remainingPure * grossPurePumpedByZone.NORD / remainingPureGross);
purePumpedNetByZone.CSUD = round(remainingPure * grossPurePumpedByZone.CSUD / remainingPureGross);
// Enforce exact national sums after decimal rounding.
totalPumpedNetByZone.SARD = round(totalPumpedNet2024 - sum(canonicalZones.filter((zone) => zone !== "SARD").map((zone) => totalPumpedNetByZone[zone])));
purePumpedNetByZone.CSUD = round(purePumpedNet2024 - purePumpedNetByZone.NORD - purePumpedNetByZone.SICI);
for (const zone of canonicalZones) {
  const physical = physicalPumpedByZone[zone];
  const discharge = totalPumpedNetByZone[zone];
  storage.push({ year: 2024, market_zone: zone, spatial_scope: "MARKET_ZONE", technology: "PUMPED_HYDRO", technology_family: "STORAGE", chp_flag: false, storage_flag: true, dispatchability_class: "STORAGE", discharge_power_MW: discharge, charge_power_MW: round(physical.chargeMW), energy_capacity_MWh: round(physical.energyMWh, 6), roundtrip_efficiency: "", storage_duration_h: discharge > 0 ? round(physical.energyMWh / discharge, 6) : "", capacity_basis: "DISCHARGE: TERNA 2024 NATIONAL NET CONTROL ALLOCATED BY OFFICIAL REGIONAL GROSS PHS SHARES; CHARGE/ENERGY: PHYSICAL OPEN PLANT INVENTORY", source_id: `TERNA_2024_IMPIANTI_DI_GENERAZIONE_TABLE_12_AND_14|ZENODO_14006948`, evidence_class: "PROJECT RECONCILIATION OF DIRECT SOURCE CONTROL AND PHYSICAL ALLOCATION EVIDENCE", status: physical.plantCount || discharge ? "2024_ZONAL_PHYSICAL_REPRESENTATION; EFFICIENCY REQUIRES MODEL ASSUMPTION" : "NO_REPORTED_PUMPED_HYDRO", additive_control: true, physical_inventory_plant_count: physical.plantCount, physical_inventory_raw_sha256: hydroInventorySha, pure_pumped_discharge_NET_MW: purePumpedNetByZone[zone], mixed_pumped_discharge_NET_MW: round(discharge - purePumpedNetByZone[zone]) });
}

const bessControls = {
  2019: { mw: "", mwh: "", source: "", status: "CONTROLLED RESIDUAL - NON-MATERIAL; AUTHORITATIVE NATIONAL HISTORY NOT ACQUIRED" },
  2020: { mw: "", mwh: "", source: "", status: "CONTROLLED RESIDUAL - NON-MATERIAL; AUTHORITATIVE NATIONAL HISTORY NOT ACQUIRED" },
  2021: { mw: 407.1, mwh: "", source: "TERNA_2021_ANNUARIO_STATISTICO_STORAGE_SYSTEMS", status: "NATIONAL_CONTROL_ONLY; ENERGY CAPACITY NOT REPORTED IN PRESERVED CONTROL" },
  2022: { mw: 1300, mwh: 2800, source: "TERNA_DECEMBER_2024_MONTHLY_REPORT_RETROSPECTIVE_CHART", status: "NATIONAL_CONTROL_ONLY; ROUNDED CHART VALUE; ALTERNATIVE 1,121 MW SOURCE/PERIMETER NOTED" },
  2023: { mw: 3500, mwh: 7000, source: "TERNA_DECEMBER_2024_MONTHLY_REPORT_RETROSPECTIVE_CHART", status: "NATIONAL_CONTROL_ONLY; ROUNDED CHART VALUE" },
  2024: { mw: 5600, mwh: 12900, source: "TERNA_DECEMBER_2024_MONTHLY_REPORT", status: "NATIONAL_CONTROL_ONLY; EXCLUDES EXISTING PUMPED STORAGE" },
};
for (const year of years) {
  const control = bessControls[year];
  storage.push({ year, market_zone: "", spatial_scope: "NATIONAL_CONTROL_ONLY", technology: "BESS", technology_family: "STORAGE", chp_flag: false, storage_flag: true, dispatchability_class: "STORAGE", discharge_power_MW: control.mw, charge_power_MW: "", energy_capacity_MWh: control.mwh, roundtrip_efficiency: "", storage_duration_h: control.mw && control.mwh ? round(control.mwh / control.mw, 6) : "", capacity_basis: control.mw ? "TERNA NOMINAL OUTPUT POWER; MAXIMUM USABLE ENERGY WHERE REPORTED" : "NOT AVAILABLE", source_id: control.source, evidence_class: control.mw ? "DIRECT SOURCE CAPACITY (ROUNDED WHERE CHART-READ)" : "QA / CONTROLLED RESIDUAL ONLY", status: control.status, additive_control: true });
}
storage.sort((a, b) => Number(a.year) - Number(b.year) || a.technology.localeCompare(b.technology) || String(a.spatial_scope).localeCompare(String(b.spatial_scope)) || canonicalZones.indexOf(a.market_zone) - canonicalZones.indexOf(b.market_zone));

// Rebuild the hydro/pumping overlap bridge for all historical years plus 2024 zones.
const hydroReconciliation = [];
for (const year of years) {
  const sourceHydroCap = sum(capacity.filter((row) => Number(row.year) === year && row.technology === "HYDRO_TOTAL_CONTROL").map((row) => row.capacity_NET_MW));
  const sourceHydroEnergy = sum(generation.filter((row) => Number(row.year) === year && row.technology === "HYDRO_TOTAL_CONTROL").map((row) => row.generation_NET_GWh));
  const hydricTypeEnergy = sum(generation.filter((row) => Number(row.year) === year && ["HYDRO_RUN_OF_RIVER", "HYDRO_BASIN", "HYDRO_RESERVOIR", "HYDRO_RESERVOIR_INCLUDING_EVENTUAL_PUMPING_CONTROL"].includes(row.technology)).map((row) => row.generation_NET_GWh));
  const purePumped = round(hydroControls[year].totalHydroNetMW - sourceHydroCap);
  const mixedPumped = round(hydroControls[year].pumpedHydroNetMW - purePumped);
  hydroReconciliation.push({ year, geographic_scope: "NATIONAL", market_zone: "", official_total_hydro_NET_MW: hydroControls[year].totalHydroNetMW, renewable_source_hydro_NET_MW: round(sourceHydroCap), total_pumped_hydro_NET_MW: hydroControls[year].pumpedHydroNetMW, pure_pumped_hydro_NET_MW_outside_renewable_source_control: purePumped, mixed_pumped_hydro_NET_MW_inside_renewable_source_control: mixedPumped, non_pumped_natural_hydro_NET_MW: round(hydroControls[year].totalHydroNetMW - hydroControls[year].pumpedHydroNetMW), renewable_source_hydro_NET_generation_GWh: round(sourceHydroEnergy), hydric_type_total_including_eventual_pumping_NET_GWh: round(hydricTypeEnergy), hydric_minus_renewable_statistical_difference_GWh: round(hydricTypeEnergy - sourceHydroEnergy), pumped_hydro_charge_MW: "", pumped_hydro_energy_MWh: "", model_input_status: year === 2024 ? "NATIONAL CONTROL RECONCILED; ZONAL STORAGE ROWS AVAILABLE" : "NATIONAL CONTROL ONLY", double_count_guardrail: "PUMPED HYDRO DISCHARGE MW IS A SUBSET OF OFFICIAL TOTAL HYDRO; PURE PUMPING IS OUTSIDE RENEWABLE-SOURCE HYDRO WHILE MIXED PUMPING OVERLAPS IT", source_id: `${hydroControls[year].sourceId}|TERNA_API_RENEWABLE_SOURCE_CAPACITY_${year}|TERNA_API_RENEWABLE_PRODUCTION_AND_HYDRIC_${year}`, status: purePumped >= 0 && mixedPumped >= -1 ? "RECONCILED" : "REVIEW" });
}
for (const zone of canonicalZones) {
  const sourceCap = sum(capacity.filter((row) => Number(row.year) === 2024 && row.market_zone === zone && row.technology === "HYDRO_TOTAL_CONTROL").map((row) => row.capacity_NET_MW));
  const sourceEnergy = sum(generation.filter((row) => Number(row.year) === 2024 && row.market_zone === zone && row.technology === "HYDRO_TOTAL_CONTROL").map((row) => row.generation_NET_GWh));
  const typeEnergy = sum(generation.filter((row) => Number(row.year) === 2024 && row.market_zone === zone && ["HYDRO_RUN_OF_RIVER", "HYDRO_BASIN", "HYDRO_RESERVOIR", "HYDRO_RESERVOIR_INCLUDING_EVENTUAL_PUMPING_CONTROL"].includes(row.technology)).map((row) => row.generation_NET_GWh));
  const physical = physicalPumpedByZone[zone];
  hydroReconciliation.push({ year: 2024, geographic_scope: "MARKET_ZONE", market_zone: zone, official_total_hydro_NET_MW: round(sourceCap + purePumpedNetByZone[zone]), renewable_source_hydro_NET_MW: round(sourceCap), total_pumped_hydro_NET_MW: totalPumpedNetByZone[zone], pure_pumped_hydro_NET_MW_outside_renewable_source_control: purePumpedNetByZone[zone], mixed_pumped_hydro_NET_MW_inside_renewable_source_control: round(totalPumpedNetByZone[zone] - purePumpedNetByZone[zone]), non_pumped_natural_hydro_NET_MW: round(sourceCap - (totalPumpedNetByZone[zone] - purePumpedNetByZone[zone])), renewable_source_hydro_NET_generation_GWh: round(sourceEnergy), hydric_type_total_including_eventual_pumping_NET_GWh: round(typeEnergy), hydric_minus_renewable_statistical_difference_GWh: round(typeEnergy - sourceEnergy), pumped_hydro_charge_MW: round(physical.chargeMW), pumped_hydro_energy_MWh: round(physical.energyMWh, 6), model_input_status: "2024 ZONAL PHYSICAL PUMPED-STORAGE REPRESENTATION AVAILABLE; ROUNDTRIP EFFICIENCY REMAINS A MODEL ASSUMPTION", double_count_guardrail: "DO NOT ADD PUMPED DISCHARGE MW TO TOTAL HYDRO; ONLY PURE PUMPING IS ADDED TO THE RENEWABLE-SOURCE CAPACITY CONTROL TO RECONSTRUCT TOTAL HYDRO", source_id: "TERNA_2024_TABLE_12_AND_14|TERNA_DOWNLOAD_CENTER_HYDRO|ZENODO_14006948", status: "RECONCILED WITH CROSS-SOURCE ALLOCATION" });
}

// Reconcile the 2024 DDS-comparable thermoelectric perimeter after exact duplicate normalization.
const thermalPerimeter = [];
for (const zone of canonicalZones) {
  const thermoRows = capacity.filter((row) => Number(row.year) === 2024 && row.market_zone === zone && row.perimeter_layer === "THERMOELECTRIC_CONVERSION_TECHNOLOGY" && boolText(row.additive_to_system_total) === "true");
  const thermoTotal = sum(thermoRows.map((row) => row.capacity_NET_MW));
  const thermoGeo = sum(thermoRows.filter((row) => row.technology === "GEOTHERMAL").map((row) => row.capacity_NET_MW));
  const sourceBio = sum(capacity.filter((row) => Number(row.year) === 2024 && row.market_zone === zone && row.technology === "BIOENERGY" && row.perimeter_layer === "RENEWABLE_SOURCE_CAPACITY").map((row) => row.capacity_NET_MW));
  const sourceGeo = sum(capacity.filter((row) => Number(row.year) === 2024 && row.market_zone === zone && row.technology === "GEOTHERMAL" && row.perimeter_layer === "RENEWABLE_SOURCE_CAPACITY").map((row) => row.capacity_NET_MW));
  thermalPerimeter.push({ year: 2024, market_zone: zone, thermoelectric_conversion_total_NET_MW: round(thermoTotal), identified_bioenergy_source_subset_NET_MW_non_additive: round(sourceBio), explicit_thermoelectric_geothermal_NET_MW_inside_total: round(thermoGeo), renewable_source_geothermal_control_NET_MW_non_additive: round(sourceGeo), geothermal_addition_required_MW: thermoGeo > 0 ? 0 : round(sourceGeo), DDS_comparable_current_perimeter_NET_MW: round(thermoTotal + (thermoGeo > 0 ? 0 : sourceGeo)), "2040_DDS_thermoelectric_control_NET_MW_national_only": "", duplicate_treatment: "2024 EXACT IDENTICAL FUEL-CELL DUPLICATES COUNTED ONCE", bioenergy_treatment: "SOURCE CROSS-CLASSIFICATION; NEVER ADD TO THERMOELECTRIC TOTAL", geothermal_treatment: thermoGeo > 0 ? "EXPLICIT THERMOELECTRIC LABEL PRESENT; SOURCE CONTROL OVERLAPS" : "ADD SOURCE GEOTHERMAL OUTSIDE THERMO EXTRACT", evidence_class: "PROJECT RECONCILIATION FROM DIRECT TERNA CAPACITY", status: "2024 PERIMETER RECONCILED; 2040 TECHNOLOGY BRIDGE NOT YET FINAL" });
}
const nationalThermal = {
  year: 2024,
  market_zone: "",
  thermoelectric_conversion_total_NET_MW: round(sum(thermalPerimeter.map((row) => row.thermoelectric_conversion_total_NET_MW))),
  identified_bioenergy_source_subset_NET_MW_non_additive: round(sum(thermalPerimeter.map((row) => row.identified_bioenergy_source_subset_NET_MW_non_additive))),
  explicit_thermoelectric_geothermal_NET_MW_inside_total: round(sum(thermalPerimeter.map((row) => row.explicit_thermoelectric_geothermal_NET_MW_inside_total))),
  renewable_source_geothermal_control_NET_MW_non_additive: round(sum(thermalPerimeter.map((row) => row.renewable_source_geothermal_control_NET_MW_non_additive))),
  geothermal_addition_required_MW: round(sum(thermalPerimeter.map((row) => row.geothermal_addition_required_MW))),
  DDS_comparable_current_perimeter_NET_MW: round(sum(thermalPerimeter.map((row) => row.DDS_comparable_current_perimeter_NET_MW))),
  "2040_DDS_thermoelectric_control_NET_MW_national_only": 55000,
  duplicate_treatment: `RAW ${edgeAudit.capacity_duplicate_reconciliation.raw_source_sum_MW} MW; EXACT-DUPLICATE-NORMALIZED ${edgeAudit.capacity_duplicate_reconciliation.exact_duplicate_normalized_sum_MW} MW; INDEPENDENT PUBLISHED CONTROL ${edgeAudit.capacity_duplicate_reconciliation.independent_terna_published_control_MW_rounded_0_1} MW`,
  bioenergy_treatment: "SOURCE CROSS-CLASSIFICATION; NEVER ADD TO THERMOELECTRIC TOTAL",
  geothermal_treatment: "SOURCE CONTROL OVERLAPS EXPLICIT THERMOELECTRIC GEOTHERMAL LABEL",
  evidence_class: "TERNA CAPACITY CONTROL / PROJECT RECONCILIATION",
  status: "2024 PERIMETER RECONCILED; ~55 GW IS A 2040 NATIONAL ENVELOPE, NOT CCGT+OCGT",
};
thermalPerimeter.push(nationalThermal);

const thermalFuelControls2024 = [
  { fuel_source: "ALL_THERMOELECTRIC", net_capacity_MW: 60330.4 },
  { fuel_source: "BIOENERGY", net_capacity_MW: 3586.0 },
  { fuel_source: "COAL", net_capacity_MW: 4917.4 },
  { fuel_source: "NATURAL_GAS", net_capacity_MW: 48016.1 },
  { fuel_source: "OIL", net_capacity_MW: 1975.9 },
  { fuel_source: "OTHER_FUEL", net_capacity_MW: 1834.9 },
].map((row) => ({ year: 2024, geographic_scope: "NATIONAL", ...row, capacity_basis: "NET", source_id: "TERNA_2024_IMPIANTI_DI_GENERAZIONE_TABLE_19", evidence_class: "DIRECT SOURCE CAPACITY CONTROL", allocation_status: row.fuel_source === "ALL_THERMOELECTRIC" ? "RECONCILES TO DUPLICATE-NORMALIZED CANONICAL TOTAL AT 0.1 MW PUBLISHED PRECISION" : "NATIONAL CONTROL ONLY; ZONE x TECHNOLOGY FUEL ALLOCATION REQUIRES GEM/PLANT EVIDENCE", additive_to_technology_total: false }));

// Taxonomy-integrity audit: one record per dataset and technology, with exact invariant counts.
const integrityQa = [];
for (const [dataset, rows] of [["CAPACITY", capacity], ["GENERATION", generation], ["STORAGE", storage]]) {
  for (const [technology, items] of groupRows(rows, (row) => row.technology)) {
    const expected = techTaxonomy.get(technology);
    const chpMismatches = items.filter((row) => boolText(row.chp_flag) !== boolText(expected.chp_flag)).length;
    const storageMismatches = items.filter((row) => boolText(row.storage_flag) !== boolText(expected.storage_flag)).length;
    const dispatchMismatches = items.filter((row) => String(row.dispatchability_class) !== String(expected.dispatchability_class)).length;
    const familyMismatches = items.filter((row) => String(row.technology_family) !== String(expected.family)).length;
    integrityQa.push({ dataset, technology, row_count: items.length, expected_chp_flag: expected.chp_flag, observed_chp_flags: unique(items.map((row) => row.chp_flag)).join("|"), chp_mismatch_count: chpMismatches, expected_storage_flag: expected.storage_flag, observed_storage_flags: unique(items.map((row) => row.storage_flag)).join("|"), storage_mismatch_count: storageMismatches, expected_family: expected.family, observed_families: unique(items.map((row) => row.technology_family)).join("|"), family_mismatch_count: familyMismatches, expected_dispatchability_class: expected.dispatchability_class, observed_dispatchability_classes: unique(items.map((row) => row.dispatchability_class)).join("|"), dispatchability_mismatch_count: dispatchMismatches, non_chp_named_true_count: technology.endsWith("_NON_CHP") ? items.filter((row) => boolText(row.chp_flag) === "true").length : 0, status: chpMismatches + storageMismatches + dispatchMismatches + familyMismatches === 0 && (!technology.endsWith("_NON_CHP") || items.every((row) => boolText(row.chp_flag) !== "true")) ? "PASS" : "FAIL" });
  }
}

const artifactSupersession = [
  { artifact: "historical_baseline/qa/MEM_HISTORICAL_BASELINE_2024_BUILD_SUMMARY.json", old_role: "2024-only intermediate build summary", new_role: "SUPERSEDED_BY_2019_2024_CANONICAL_BASELINE", superseded_by: "historical_baseline/qa/MEM_HISTORICAL_SERIES_STABILITY_AND_QA_2019_2025.json and MEM_HISTORICAL_BASELINE_PHASE2_QA.csv", reason: "Contains obsolete CHP/dispatchability fields and a 53,975.509902 GWh Hydric analytical total that must not be used as additive renewable hydro.", safe_to_use: "NO" },
  { artifact: "historical_baseline/qa/MEM_HISTORICAL_BASELINE_2024_QA.csv", old_role: "2024-only partial QA", new_role: "SUPERSEDED_BY_2019_2024_CANONICAL_BASELINE", superseded_by: "historical_baseline/qa/MEM_HISTORICAL_BASELINE_PHASE2_QA.csv", reason: "Partial-year QA does not include 2019-2024 taxonomy, storage, wind, or duplicate corrections.", safe_to_use: "NO" },
  { artifact: "historical_baseline/superseded/phase1_20260901_preclosure/*", old_role: "Pre-closure copies of prior cap/gen/taxonomy/completion artifacts", new_role: "AUDIT ARCHIVE ONLY", superseded_by: "historical_baseline/normalized and historical_baseline/analysis current files", reason: "Retained for provenance; not controlling.", safe_to_use: "NO" },
  { artifact: "historical_baseline/analysis/Terna_Hydro_2024_Zone_Type_Production.csv", old_role: "Hydric type detail including eventual pumping", new_role: "NON-ADDITIVE ANALYTICAL DETAIL", superseded_by: "MEM_Historical_Generation_By_Zone_Technology.csv plus MEM_Hydro_Pumping_Perimeter_Reconciliation.csv", reason: "53,975.509902 GWh type sum includes eventual pumping; additive renewable-source hydro control is 52,391.704319 GWh.", safe_to_use: "YES_FOR_NON_ADDITIVE_TYPE_ANALYSIS_ONLY" },
  { artifact: "historical_baseline/normalized/Terna_Bioenergy_*_2024_NET.csv; Terna_Geothermal_*_2024_NET.csv; Terna_Hydro_*_2024_NET.csv", old_role: "Technology-specific 2024 official normalized evidence", new_role: "SUPPORTING DIRECT-SOURCE EVIDENCE", superseded_by: "MEM_Historical_Capacity_By_Zone_Technology.csv and MEM_Historical_Generation_By_Zone_Technology.csv", reason: "Still valid as source-specific evidence, but the 2019-2024 canonical tables control cross-technology analysis.", safe_to_use: "YES_AS_SUPPORTING_EVIDENCE_ONLY" },
];

const completion = [];
const commonYears = "2019|2020|2021|2022|2023|2024";
completion.push({ technology: "__OVERALL__", capacity_by_zone: "YES_FOR_GENERATION TECHNOLOGIES; STORAGE HAS 2024 PHS ZONES AND BESS NATIONAL CONTROLS", generation_by_zone: "YES_FOR MATERIAL GENERATION; WIND SUD 2022-2024 COMPONENT SPLIT IS NON-MATERIAL CONTROLLED RESIDUAL", chp_distinction_resolved: "YES; ELECTRICITY-ONLY CHP-SPECIFIC BANDS FROZEN", fuel_source_resolved: "NO - MATERIAL THERMAL FUEL/PLANT ATTRIBUTION REQUIRES GEM AUGUST 2026", hydro_subtype_resolved: "ENERGY YES; PHS STORAGE YES; BASIN/RESERVOIR TYPE MW ZONAL ALLOCATION REMAINS CONTROLLED", historical_years_available: `${commonYears}; 2025 CONSOLIDATED ENDPOINTS EMPTY`, ready_for_canonical_workbook: "NO", status: "HISTORICAL BASELINE INCOMPLETE", blocker: "Single material gate: acquire the complete official GEM Global Oil and Gas Plant Tracker August-2026 release (including sub-threshold data) for Italy unit/status/fuel/geolocation reconciliation against Terna NET controls." });
for (const row of taxonomy.filter((item) => item.taxonomy_dimension === "TECHNOLOGY" && item.code !== "WIND_TOTAL_CONTROL")) {
  const t = row.code;
  if (["PUMPED_HYDRO", "BESS", "OTHER_STORAGE"].includes(t)) {
    completion.push({ technology: t, capacity_by_zone: t === "PUMPED_HYDRO" ? "YES_2024; NATIONAL_CONTROL_2019_2023" : t === "BESS" ? "NATIONAL_CONTROL_2021_2024; 2019_2020 NON-MATERIAL RESIDUAL" : "NO MATERIAL EVIDENCE", generation_by_zone: "NOT_APPLICABLE; STORAGE OPERATIONS ARE SEPARATE DIAGNOSTICS", chp_distinction_resolved: "NOT_APPLICABLE", fuel_source_resolved: t === "PUMPED_HYDRO" ? "YES_WATER" : t === "BESS" ? "YES_ELECTRICITY" : "UNRESOLVED", hydro_subtype_resolved: t === "PUMPED_HYDRO" ? "YES_WITH_EXPLICIT OVERLAP BRIDGE" : "NOT_APPLICABLE", historical_years_available: t === "PUMPED_HYDRO" ? commonYears : t === "BESS" ? "2021|2022|2023|2024" : "NONE", ready_for_canonical_workbook: "NO_OVERALL_GATE", status: t === "PUMPED_HYDRO" ? "RESOLVED_FOR_2024 CALIBRATION; HISTORICAL NATIONAL CONTROLS" : t === "BESS" ? "CONTROLLED NATIONAL HISTORY; ZONAL HISTORY NOT INVENTED" : "CONTROLLED RESIDUAL - NON-MATERIAL", blocker: t === "PUMPED_HYDRO" ? "Roundtrip efficiency is a future model assumption; basin/reservoir MW allocation remains separate." : t === "BESS" ? "Early-year/zonal distributions remain a later model-input gate, not a historical generation blocker." : "No material historical evidence; excluded unless later shown material." });
    continue;
  }
  if (t === "WIND_ONSHORE" || t === "WIND_OFFSHORE") {
    completion.push({ technology: t, capacity_by_zone: "YES_2019_2024", generation_by_zone: "YES EXCEPT SUD 2022_2024 COMPONENT SPLIT", chp_distinction_resolved: "NOT_APPLICABLE", fuel_source_resolved: "YES_WIND", hydro_subtype_resolved: "NOT_APPLICABLE", historical_years_available: commonYears, ready_for_canonical_workbook: "NO_OVERALL_GATE", status: "RESOLVED WITH CONTROLLED RESIDUAL - NON-MATERIAL", blocker: "Beleolico observed annual output is not separately published; Terna aggregate wind remains the additive control." });
    continue;
  }
  if (["HYDRO_RUN_OF_RIVER", "HYDRO_BASIN", "HYDRO_RESERVOIR", "HYDRO_RESERVOIR_INCLUDING_EVENTUAL_PUMPING_CONTROL"].includes(t)) {
    const sourceControl = t === "HYDRO_RESERVOIR_INCLUDING_EVENTUAL_PUMPING_CONTROL";
    completion.push({ technology: t, capacity_by_zone: "TOTAL HYDRO CONTROL ONLY; TYPE MW ZONAL ALLOCATION NOT FORCED", generation_by_zone: sourceControl || t !== "HYDRO_RESERVOIR" ? "YES_2019_2024 NON-ADDITIVE TYPE DETAIL" : "PURE RESERVOIR COMPONENT NOT YET SEPARATED FROM SOURCE CONTROL", chp_distinction_resolved: "NOT_APPLICABLE", fuel_source_resolved: "YES_WATER", hydro_subtype_resolved: sourceControl ? "SOURCE CONTROL EXPLICITLY INCLUDES EVENTUAL PUMPING" : "ENERGY EVIDENCE AVAILABLE; REQUIRES CAPACITY ALLOCATION", historical_years_available: commonYears, ready_for_canonical_workbook: "NO_OVERALL_GATE", status: "CONTROLLED RESIDUAL - NON-MATERIAL FOR HISTORICAL ENERGY; MODEL INPUT CAPACITY ALLOCATION LATER", blocker: "Do not derive type MW from generation shares; physical allocation must reconcile to Terna total hydro and the explicit pumped-storage bridge." });
    continue;
  }
  const thermal = row.family === "THERMAL";
  completion.push({ technology: t, capacity_by_zone: "YES_2019_2024", generation_by_zone: "YES_2019_2024", chp_distinction_resolved: thermal ? (String(row.chp_flag) === "MIXED" ? "SOURCE LABEL MIXED" : "YES") : "NOT_APPLICABLE OR SOURCE CROSS-CLASSIFICATION", fuel_source_resolved: thermal ? (t === "GEOTHERMAL" ? "YES_GEOTHERMAL_HEAT" : "NO_MATERIAL GEM/PLANT BRIDGE REQUIRED") : `YES_${row.default_fuel_source}`, hydro_subtype_resolved: t === "HYDRO_TOTAL_CONTROL" ? "TOTAL CONTROL PLUS EXPLICIT PHS OVERLAP BRIDGE" : "NOT_APPLICABLE", historical_years_available: commonYears, ready_for_canonical_workbook: "NO_OVERALL_GATE", status: thermal && t !== "GEOTHERMAL" ? "HISTORY ACQUIRED; MATERIAL FUEL ATTRIBUTION INCOMPLETE" : "HISTORY ACQUIRED AND RECONCILED", blocker: thermal && t !== "GEOTHERMAL" ? "Complete official GEM August-2026 release required for material fuel/plant reconciliation." : "Overall GEM gate only." });
}
completion.push({ technology: "WIND_TOTAL_CONTROL", capacity_by_zone: "YES_2019_2024 NON-ADDITIVE CAPACITY CONTROL", generation_by_zone: "YES_2019_2024 ADDITIVE ENERGY CONTROL", chp_distinction_resolved: "NOT_APPLICABLE", fuel_source_resolved: "YES_WIND", hydro_subtype_resolved: "NOT_APPLICABLE", historical_years_available: commonYears, ready_for_canonical_workbook: "NO_CONTROL_ROW", status: "DIRECT SOURCE CONTROL", blocker: "Not a model component." });

// Source and derivation manifest extensions.
const manifestKey = (row) => `${row.source_id}`;
const sourceMap = new Map(sourceManifest.map((row) => [manifestKey(row), row]));
const addSource = (row) => sourceMap.set(manifestKey(row), row);
const officialReports = [
  ["TERNA_2019_IMPIANTI_DI_GENERAZIONE_TABLE_14", "Terna 2019 generation plants statistical report", "2019", "Terna_2019_Impianti_di_generazione_RAW.pdf", "https://download.terna.it/terna/3-IMPIANTI%20DI%20GENERAZIONE_8d822918ed198d3.pdf"],
  ["TERNA_2020_ANNUARIO_STATISTICO_TABLE_14", "Terna 2020 statistical yearbook", "2020", "Terna_2020_Annuario_Statistico_RAW.pdf", "https://download.terna.it/terna/ANNUARIO%20STATISTICO%202020_8d9ced1f1493fb5.pdf"],
  ["TERNA_2021_ANNUARIO_STATISTICO_TABLE_14", "Terna 2021 statistical yearbook", "2021", "Terna_2021_Annuario_Statistico_RAW.pdf", "https://download.terna.it/terna/Terna_Annuario_Statistico_2021_8dafd2a9a68989c.pdf"],
  ["TERNA_2022_IMPIANTI_GENERAZIONE_TABLE_14", "Terna 2022 generation plants statistical report", "2022", "Terna_2022_Impianti_di_generazione_RAW.pdf", "https://download.terna.it/terna/3%20-%20IMPIANTI%20GENERAZIONE_8db99b7c8a48aab.pdf"],
  ["TERNA_2023_ANNUARIO_STATISTICO_TABLE_14", "Terna 2023 statistical yearbook", "2023", "Terna_2023_Annuario_Statistico_RAW.pdf", "https://download.terna.it/terna/Terna_annuario_statistico_energia_elettrica_Italia_2023_8dd211b3585b028.pdf"],
  ["TERNA_2024_IMPIANTI_DI_GENERAZIONE_TABLE_12_14_19", "Terna 2024 generation plants statistical report", "2024", "Terna_2024_Impianti_di_generazione_RAW.pdf", "https://download.terna.it/terna/03_IMPIANTI%20DI%20GENERAZIONE_8dec285ed22347a.pdf"],
  ["TERNA_DECEMBER_2024_MONTHLY_REPORT", "Terna December 2024 monthly electricity report", "2024", "Terna_Rapporto_Mensile_Dicembre_2024_RAW.pdf", "https://download.terna.it/terna/Rapporto_Mensile_Dicembre_24_8dd358635ce3ac2.pdf"],
];
for (const [sourceId, title, release, filename, url] of officialReports) {
  const file = path.join(rawReportsDir, filename);
  addSource({ source_id: sourceId, publisher: "Terna S.p.A.", title, release_or_year: release, acquisition_route: "OFFICIAL TERNA DOWNLOAD", acquisition_date: "2026-09-01", original_uploaded_filename: filename, archived_raw_file: path.relative(phaseRoot, file).replaceAll("\\", "/"), byte_size: (await fs.stat(file)).size, raw_sha256: await sha256File(file), scope: "Hydro/storage/fuel national controls as cited in methodology", evidence_class: "DIRECT SOURCE CAPACITY / QA CONTROL", status: "ACQUIRED_ARCHIVED_HASHED", url });
}
const beleolicoFile = path.join(rawReportsDir, "Renexia_Beleolico_Primary_Source_RAW.html");
addSource({ source_id: "RENEXIA_BELEOLICO_PRIMARY_SOURCE", publisher: "Renexia S.p.A.", title: "Beleolico project primary source page", release_or_year: "operational 2022", acquisition_route: "PROJECT OWNER WEB PAGE", acquisition_date: "2026-09-01", original_uploaded_filename: "Renexia_Beleolico_Primary_Source_RAW.html", archived_raw_file: path.relative(phaseRoot, beleolicoFile).replaceAll("\\", "/"), byte_size: (await fs.stat(beleolicoFile)).size, raw_sha256: await sha256File(beleolicoFile), scope: "30 MW Beleolico capacity and commissioning evidence; expected energy is not used as observed generation", evidence_class: "DIRECT PRIMARY PLANT EVIDENCE", status: "ACQUIRED_ARCHIVED_HASHED", url: "https://renexia.it/en/beleolico/" });
addSource({ source_id: "ZENODO_14006948", publisher: "Zenodo / Politecnico di Milano research dataset", title: "Italian Hydropower Programmable Plants Database v2", release_or_year: "2024", acquisition_route: "ZENODO OPEN DATA DOWNLOAD", acquisition_date: "2026-09-01", original_uploaded_filename: path.basename(hydroInventoryFile), archived_raw_file: path.relative(phaseRoot, hydroInventoryFile).replaceAll("\\", "/"), byte_size: (await fs.stat(hydroInventoryFile)).size, raw_sha256: hydroInventorySha, scope: "Physical HPHS/HDAM plant allocation evidence; Terna totals remain controlling", evidence_class: "PHYSICAL MODELLING / ALLOCATION SUPPORT", status: "ACQUIRED_ARCHIVED_HASHED", url: "https://zenodo.org/records/14006948" });
sourceManifest = [...sourceMap.values()].sort((a, b) => String(a.source_id).localeCompare(String(b.source_id)));
const derivationMap = new Map(derivationManifest.map((row) => [String(row.derivation_id), row]));
for (const row of [
  { derivation_id: "DER-HIST-020", output: "MEM_Historical_Capacity_By_Zone_Technology.csv", evidence_class: "PROJECT DERIVATION FROM DIRECT SOURCE CAPACITY", inputs: "Corrected 2019-2024 Terna canonical capacity; Renexia Beleolico evidence", method: "Apply taxonomy invariants; count 19 exact 2024 fuel-cell duplicates once; split 30 MW offshore wind in SUD from 2022; retain aggregate wind as non-additive control.", key_guardrail: "No _NON_CHP row can be CHP; technology and fuel remain separate.", status: "VALIDATED_PHASE2; THERMAL FUEL ATTRIBUTION INCOMPLETE" },
  { derivation_id: "DER-HIST-021", output: "MEM_Historical_Generation_By_Zone_Technology.csv", evidence_class: "PROJECT DERIVATION FROM DIRECT SOURCE ENERGY", inputs: "Corrected 2019-2024 Terna canonical generation", method: "Resolve eight four-row fuel-cell groups by two-zero/two-positive physical rule; retain Terna wind energy as additive control where Beleolico observed energy is not published.", key_guardrail: "No expected plant production is substituted for observed energy; observed CF remains diagnostic.", status: "VALIDATED_PHASE2" },
  { derivation_id: "DER-HIST-022", output: "MEM_Historical_Storage_By_Zone_Technology.csv", evidence_class: "PROJECT RECONCILIATION", inputs: "Terna annual hydro controls; Terna December-2024 storage controls; Zenodo hydro physical inventory", method: "Separate storage from generation; reconcile 2024 pumped discharge NET MW to Terna and use physical inventory for charge MW/energy MWh; retain BESS national-only where zonal data are unavailable.", key_guardrail: "Pumped MW is a subset of total hydro and is never added twice; no charge-power symmetry or efficiency is invented.", status: "VALIDATED_WITH_DECLARED_SOURCE_LIMITATIONS" },
  { derivation_id: "DER-HIST-023", output: "MEM_HISTORICAL_TAXONOMY_INTEGRITY_QA.csv", evidence_class: "QA / RECONCILIATION ONLY", inputs: "Final capacity, generation, storage and taxonomy", method: "Compare every technology group against exact CHP/storage/family/dispatchability invariants.", key_guardrail: "Zero invariant failures required.", status: "VALIDATED" },
]) derivationMap.set(row.derivation_id, row);
derivationManifest = [...derivationMap.values()].sort((a, b) => String(a.derivation_id).localeCompare(String(b.derivation_id)));

const unresolvedThermal2024MW = sum(capacity.filter((row) => Number(row.year) === 2024 && row.perimeter_layer === "THERMOELECTRIC_CONVERSION_TECHNOLOGY" && row.fuel_source === "UNRESOLVED" && boolText(row.additive_to_system_total) === "true").map((row) => row.capacity_NET_MW));
const duplicateGrain = (rows) => [...groupRows(rows, (row) => [row.year, row.market_zone, row.technology, row.perimeter_layer].join("|")).values()].filter((items) => items.length > 1).length;
const qaChecks = [
  { criterion: "A", check: "Final capacity/generation/storage pass taxonomy invariants", status: integrityQa.every((row) => row.status === "PASS") ? "PASS" : "FAIL", observed: `${integrityQa.filter((row) => row.status === "FAIL").length} failed technology-dataset groups`, disposition: "FREEZE REQUIREMENT" },
  { criterion: "B", check: "CHP/non-CHP consistent and electricity-only CHP bands frozen", status: integrityQa.every((row) => row.non_chp_named_true_count === 0) ? "PASS" : "FAIL", observed: `${sum(integrityQa.map((row) => row.non_chp_named_true_count))} _NON_CHP rows marked true`, disposition: "FREEZE REQUIREMENT" },
  { criterion: "C", check: "Bioenergy/geothermal overlap reconciled", status: Math.abs(nationalThermal.DDS_comparable_current_perimeter_NET_MW - nationalThermal.thermoelectric_conversion_total_NET_MW - nationalThermal.geothermal_addition_required_MW) < 1e-6 ? "PASS" : "FAIL", observed: `Thermoelectric extract ${nationalThermal.thermoelectric_conversion_total_NET_MW} MW + geothermal outside extract ${nationalThermal.geothermal_addition_required_MW} MW = DDS-comparable current perimeter ${nationalThermal.DDS_comparable_current_perimeter_NET_MW} MW; bioenergy remains non-additive`, disposition: "FREEZE REQUIREMENT" },
  { criterion: "D", check: "Renewable hydro control cannot be confused with Hydric detail", status: "PASS", observed: "2024 additive renewable hydro 52,391.704319 GWh; Hydric analytical detail 53,975.509902 GWh", disposition: "EXPLICIT ADDITIVITY FLAGS AND SUPERSESSION REGISTER" },
  { criterion: "E", check: "Wind onshore/offshore resolved or residual accepted", status: "PASS_CONTROLLED_RESIDUAL", observed: "30 MW Beleolico capacity split in SUD from 2022; SUD 2022-2024 component energy unobserved and immaterial; Terna aggregate remains additive control", disposition: "CONTROLLED RESIDUAL - NON-MATERIAL" },
  { criterion: "F", check: "Pumped hydro physical storage and no double count", status: "PASS", observed: `2024 total PHS ${totalPumpedNet2024} MW; pure PHS ${purePumpedNet2024} MW; physical inventory ${round(sum(Object.values(physicalPumpedByZone).map((row) => row.energyMWh)), 6)} MWh`, disposition: "CROSS-SOURCE RECONCILIATION" },
  { criterion: "G", check: "Historical storage table exists", status: "PASS_WITH_LIMITATIONS", observed: "PHS 2019-2024 national controls + 2024 zones; BESS 2021-2024 national controls; early/zonal BESS not invented", disposition: "MODEL-INPUT GATE LATER; NOT HISTORICAL GENERATION BLOCKER" },
  { criterion: "H", check: "Material thermal fuel attribution resolved", status: unresolvedThermal2024MW < 100 ? "PASS" : "FAIL_MATERIAL", observed: `${round(unresolvedThermal2024MW)} MW of 2024 thermoelectric conversion capacity remains fuel-unallocated at zone x technology level`, disposition: "SINGLE GEM AUGUST-2026 ACQUISITION GATE" },
  { criterion: "I", check: "Fuel-cell production exceptions closed", status: edgeAudit.fuel_cell_production_exception_reconstruction.structural_rule_fail_count === 0 ? "PASS" : "FAIL", observed: `${edgeAudit.fuel_cell_production_exception_reconstruction.structural_rule_pass_count}/8 pass; inferred Netta ${edgeAudit.fuel_cell_production_exception_reconstruction.inferred_total_netta_GWh} GWh`, disposition: "DETERMINISTIC PHYSICAL RULE" },
  { criterion: "J", check: "Renewable blank semantics resolved/accepted", status: blankSemantics.every((row) => !row.material_exception) ? "PASS_CONTROLLED_RESIDUAL" : "FAIL_MATERIAL", observed: `${blankSemantics.filter((row) => row.semantic_status.includes("ALL-BLANK") || row.semantic_status.includes("ALL_BLANK")).length} all-blank positive-generation groups; ${blankSemantics.filter((row) => row.material_exception).length} material`, disposition: "RAW BLANK PRESERVED; AGGREGATION ZERO; NON-MATERIAL EXCEPTIONS DECLARED" },
  { criterion: "QA", check: "Canonical grain duplicates", status: duplicateGrain(capacity) === 0 && duplicateGrain(generation) === 0 ? "PASS" : "FAIL", observed: `capacity=${duplicateGrain(capacity)} generation=${duplicateGrain(generation)}`, disposition: "HARD QA" },
  { criterion: "QA", check: "2024 thermoelectric exact-duplicate normalization", status: Math.abs(nationalThermal.thermoelectric_conversion_total_NET_MW - edgeAudit.capacity_duplicate_reconciliation.exact_duplicate_normalized_sum_MW) < 1e-6 ? "PASS" : "FAIL", observed: `raw=${edgeAudit.capacity_duplicate_reconciliation.raw_source_sum_MW}; normalized=${nationalThermal.thermoelectric_conversion_total_NET_MW}; published=${edgeAudit.capacity_duplicate_reconciliation.independent_terna_published_control_MW_rounded_0_1}`, disposition: "CANONICAL USES NORMALIZED" },
];

// Update the existing broader project registers without discarding unrelated open gates.
const gapFile = path.join(docsDir, "MEM_v2.9_EMPIRICAL_GAP_AND_ACQUISITION_REGISTER_HISTORICAL_BASELINE_UPDATED_20260901.csv");
let gapRegister = await readCsvObjects(gapFile, "GapRegister");
const gapById = new Map(gapRegister.map((row) => [String(row.gap_id), row]));
const updateGap = (id, values) => gapById.set(id, { ...(gapById.get(id) ?? { gap_id: id }), ...values });
updateGap("GAP-001", { status: "ACQUIRED / VALIDATED", artifact_or_decision: "Terna_Thermoelectric_Capacity_2024_Canonical.csv", evidence_class: "DIRECT SOURCE CAPACITY / TERNA CAPACITY CONTROL", authoritative_source: "Terna Download Center official XLSX; API row multiset cross-check; Terna 2024 published Table 19 control", local_search_result: "19 exact identical fuel-cell capacity duplicate groups confirmed; all raw rows preserved", exact_acquisition_or_decision: "Canonical modelling layer counts each exact duplicate visible observation once", required_fields_or_controls: "RAW_SOURCE_SUM=60,331.82927 MW; EXACT_DUPLICATE_NORMALIZED_SUM=60,330.37427 MW; PUBLISHED_CONTROL=60,330.4 MW", acceptance_test: "PASS at published 0.1 MW precision; canonical grain unique", blocks: "No longer blocks 2024 technology baseline; GEM remains the single material fuel/plant gate" });
updateGap("GAP-003", { status: "REQUIRES USER DOWNLOAD - SINGLE MATERIAL HISTORICAL FREEZE GATE", local_search_result: "No complete official GEM GOGPT August-2026 workbook found anywhere in the MEM workspace or Downloads", blocks: "Material zone x technology thermal fuel attribution; plant/unit/status/geolocation reconciliation; current 2026 physical fleet; historical freeze" });
updateGap("GAP-008", { status: "HISTORICAL CONTROLS PARTIALLY ACQUIRED / FUTURE MODEL INPUT STILL REQUIRES DATA", local_search_result: "Historical storage layer now contains national PHS controls for 2019-2024, 2024 zonal PHS power/energy reconciliation, and BESS national controls for 2021-2024", acceptance_test: "Historical table passes no-double-count QA; future scenario storage.csv still requires fixed scenario-specific power/energy/efficiency/state rules", blocks: "Future model input set, not historical generation integrity" });
updateGap("GAP-HIST-003", { status: "ENERGY AND 2024 PUMPED-STORAGE PERIMETER RESOLVED", artifact_or_decision: "Hydro renewable/source versus Hydric type/pumping perimeter", local_search_result: "2024 renewable-source NET hydro 52,391.704319 GWh; Hydric detail 53,975.509902 GWh; PHS 7,252.3 MW total NET; 3,969.57561 MW pure pumping outside renewable-source capacity control", exact_acquisition_or_decision: "Keep renewable-source hydro additive; rename Serbatoio including eventual pumping as a non-additive source control; storage layer carries pumped hydro", required_fields_or_controls: "2019-2024 national PHS NET controls; 2024 zone discharge/charge/energy; explicit pure/mixed overlap", acceptance_test: "PASS: PHS never added to total hydro; only pure-pumping residual bridges renewable-source to total hydro", blocks: "Hydro basin/reservoir zonal MW allocation remains a later model-input calibration residual" });
updateGap("GAP-HIST-005", { status: "VALIDATED PHASE2 / HISTORICAL FREEZE BLOCKED ONLY BY MATERIAL THERMAL FUEL ATTRIBUTION", local_search_result: "Final 758-row capacity and 881-row generation tables; zero taxonomy-invariant failures; seven canonical zones only", exact_acquisition_or_decision: "Use corrected 2019-2024 canonical observations; 2025 consolidated endpoints remain empty", acceptance_test: "PASS taxonomy/duplicate/exception/overlap QA; GEM fuel/plant gate remains", blocks: "Historical freeze and workbook promotion until GEM reconciliation" });
updateGap("GAP-HIST-006", { status: "RESOLVED", local_search_result: "All eight 2019-2022 FUEL_CELL_CHP groups are exactly two zeros plus two positive values", exact_acquisition_or_decision: "Smaller positive=Netta active record; larger positive=Lorda; zero+zero inactive record; no row-order dependency", acceptance_test: "8/8 structural tests pass; inferred Netta total 1.1487043 GWh; zero exceptions", blocks: "No longer blocks historical freeze" });
updateGap("GAP-HIST-007", { status: "PARTIALLY RESOLVED / CONTROLLED LIMITATIONS", local_search_result: "PHS 2019-2024 national controls and 2024 zonal physical allocation; BESS national controls 2021-2024; 2019-2020 and historical zonal BESS not fabricated", exact_acquisition_or_decision: "Separate storage table; no additive storage generation; no invented charge-power symmetry or efficiency", acceptance_test: "PASS historical storage criterion with stated limitations", blocks: "Future model input calibration only" });
updateGap("GAP-HIST-008", { status: "RESOLVED WITH CONTROLLED RESIDUAL - NON-MATERIAL", local_search_result: "Beleolico 30 MW assigned to WIND_OFFSHORE in SUD from 2022; all remaining Terna wind capacity is onshore", exact_acquisition_or_decision: "Terna aggregate wind remains capacity/energy control; observed offshore annual generation is not fabricated", acceptance_test: "Capacity components reconcile exactly; SUD 2022-2024 component energy remains blank/non-additive under aggregate control", blocks: "No longer blocks historical freeze" });
updateGap("GAP-HIST-009", { status: "RESOLVED", artifact_or_decision: "Taxonomy integrity and stale-artifact supersession", evidence_class: "QA / RECONCILIATION ONLY", authoritative_source: "Final canonical tables plus MEM historical taxonomy", local_search_result: "Original final tables contained 1,172 taxonomy-field inconsistencies; rebuilt tables now have zero failed technology-dataset groups", exact_acquisition_or_decision: "Exact CHP/storage/family/dispatchability invariants; Serbatoio-including-pumping separated as control code", required_fields_or_controls: "MEM_HISTORICAL_TAXONOMY_INTEGRITY_QA.csv; MEM_HISTORICAL_ARTIFACT_SUPERSESSION_REGISTER.csv", acceptance_test: "PASS with zero _NON_CHP rows marked CHP", blocks: "No longer blocks historical freeze" });
updateGap("GAP-HIST-010", { status: "REQUIRES GEM DATA - MATERIAL", artifact_or_decision: "Thermal fuel attribution and current-2026 plant/unit bridge", evidence_class: "GEM PLANT RECONCILIATION / TERNA CAPACITY CONTROL", authoritative_source: "Terna 2024 national fuel controls plus complete GEM GOGPT August-2026 release", local_search_result: `${round(unresolvedThermal2024MW)} MW remains UNRESOLVED at zone x conversion-technology fuel grain`, exact_acquisition_or_decision: "Acquire complete original GEM release including sub-threshold data; map units/status/fuel/coordinates without replacing Terna NET MW", required_fields_or_controls: "plant/unit IDs; technology; fuel; status; gross MW; CHP; start/retirement; coordinates; ownership; release/hash", acceptance_test: "Material thermal fleet fuel/source is allocated or retained in explicit residual bands; Terna national fuel controls reconcile", blocks: "HISTORICAL BASELINE FROZEN; current 2026 fleet; v2.9.1 workbook" });
gapRegister = [...gapById.values()];

const decisionFile = path.join(docsDir, "MEM_v2.9_HISTORICAL_BASELINE_DECISION_REGISTER_20260901.csv");
let decisionRegister = await readCsvObjects(decisionFile, "DecisionRegister");
const decisionById = new Map(decisionRegister.map((row) => [String(row.decision_id), row]));
const updateDecision = (id, values) => decisionById.set(id, { ...(decisionById.get(id) ?? { decision_id: id }), ...values });
updateDecision("D-TS-003", { status: "RESOLVED", decision: "Exact duplicate capacity normalization", evidence_class: "QA / RECONCILIATION ONLY", rule: "Preserve all 19 pairs of identical raw fuel-cell rows; count each full-visible-key identical observation once at the canonical modelling layer.", value_or_artifact: "raw 60,331.82927 MW; normalized 60,330.37427 MW; published 60,330.4 MW", model_effect: "Canonical historical thermoelectric total uses the duplicate-normalized value." });
updateDecision("D-HIST-003", { status: "RESOLVED YEAR-SPECIFIC", rule: "2019-2022 source geothermal overlaps an explicit thermoelectric label; 2023-2024 it sits outside the thermoelectric extract and is added once for DDS-comparable scope.", value_or_artifact: `2024 thermoelectric ${nationalThermal.thermoelectric_conversion_total_NET_MW} MW + geothermal ${nationalThermal.geothermal_addition_required_MW} MW = ${nationalThermal.DDS_comparable_current_perimeter_NET_MW} MW`, model_effect: "Corrects the former 1.455 MW duplicate sensitivity and prevents geothermal double counting." });
updateDecision("D-HIST-006", { status: "RESOLVED", decision: "Primary CHP representation", evidence_class: "PROJECT DERIVATION", rule: "Use electricity-only CHP-specific generator bands; preserve six CHP technologies and heat/electric metadata; do not couple a heat system in the primary model; require a later heat-led/must-run sensitivity.", value_or_artifact: "MEM_CHP_REPRESENTATION_ASSESSMENT.md", model_effect: "CHP/non-CHP merit-order differences remain available without expanding base-model scope." });
updateDecision("D-HIST-009", { status: "RESOLVED", decision: "Taxonomy hard invariants", evidence_class: "QA / RECONCILIATION ONLY", rule: "Every canonical technology maps to exact CHP flag, storage flag, family and dispatchability class; no _NON_CHP may be true.", value_or_artifact: "MEM_HISTORICAL_TAXONOMY_INTEGRITY_QA.csv", model_effect: "Eliminates the CCGT_NON_CHP=true and CCGT-as-peaker coding defect." });
updateDecision("D-HIST-010", { status: "RESOLVED", decision: "Wind historical split", evidence_class: "PROJECT DERIVATION FROM DIRECT CAPACITY EVIDENCE", rule: "Assign 30 MW Beleolico to WIND_OFFSHORE/SUD from 2022; derive onshore capacity as Terna total minus 30 MW; retain aggregate energy where observed offshore output is unavailable.", value_or_artifact: "Final canonical capacity/generation tables", model_effect: "Taxonomy contains WIND_ONSHORE and WIND_OFFSHORE without invented annual offshore generation." });
updateDecision("D-HIST-011", { status: "RESOLVED WITH CONTROLLED LIMITATIONS", decision: "Separate storage history", evidence_class: "DIRECT SOURCE CAPACITY / PROJECT RECONCILIATION", rule: "Storage does not enter additive generation energy; PHS is an explicit subset of hydro; BESS remains national-only where zonal evidence is absent.", value_or_artifact: "MEM_Historical_Storage_By_Zone_Technology.csv", model_effect: "Prevents hydro/pumping and storage-generation double counting." });
updateDecision("D-HIST-012", { status: "RESOLVED", decision: "Renewable capacity blanks", evidence_class: "QA / RECONCILIATION ONLY", rule: "Preserve raw blanks; numeric reported values control; blanks aggregate as zero only with semantic status; all all-blank positive-generation cases are under 1 GWh/year.", value_or_artifact: "MEM_RENEWABLE_CAPACITY_BLANK_SEMANTICS_QA.csv", model_effect: "No blind blank-to-zero mutation and no material freeze blocker." });
updateDecision("D-HIST-013", { status: "RESOLVED - GATE REMAINS CLOSED", decision: "Historical freeze decision", evidence_class: "QA / RECONCILIATION ONLY", rule: "Criteria A-G and I-J pass or are accepted controlled residuals; criterion H fails materially until GEM fuel/plant reconciliation.", value_or_artifact: "HISTORICAL BASELINE INCOMPLETE", model_effect: "v2.9 remains unchanged; v2.9.1 is not authorized or created." });
decisionRegister = [...decisionById.values()];

const dataStatus = [
  { artifact: "MEM_Historical_Capacity_By_Zone_Technology.csv", status: "VALIDATED_PHASE2", role: "Canonical 2019-2024 zonal NET capacity with wind components and source controls", blocking_issue: "Material thermal fuel attribution awaits GEM" },
  { artifact: "MEM_Historical_Generation_By_Zone_Technology.csv", status: "VALIDATED_PHASE2", role: "Canonical 2019-2024 zonal NET generation with CHP/hydro/wind controls", blocking_issue: "No material numerical blocker; SUD offshore energy is a controlled non-material residual" },
  { artifact: "MEM_Historical_Storage_By_Zone_Technology.csv", status: "VALIDATED_WITH_SOURCE_LIMITATIONS", role: "Separate PHS/BESS historical capacity layer", blocking_issue: "Historical zonal BESS and early BESS energy remain unavailable; future model inputs require scenario values" },
  { artifact: "MEM_HISTORICAL_TECHNOLOGY_TAXONOMY.csv", status: "VALIDATED_V2026_3", role: "Hard technology/fuel invariants", blocking_issue: "None" },
  { artifact: "MEM_HISTORICAL_TAXONOMY_INTEGRITY_QA.csv", status: "PASS", role: "Every final canonical row audited", blocking_issue: "Zero failures" },
  { artifact: "GEM_GOGPT_Aug2026_complete_release", status: "REQUIRES_USER_DOWNLOAD_SINGLE_MATERIAL_GATE", role: "Thermal plant/unit/fuel/status reconciliation", blocking_issue: "Complete official workbook absent" },
  { artifact: "MEM_Current_Thermal_Stack_2026.csv", status: "NOT_CREATED_REQUIRES_GEM", role: "Current physical hybrid unit/residual stack", blocking_issue: "GEM absent; gross/net/status bridge not defensible" },
  { artifact: "HISTORICAL_BASELINE_FREEZE_GATE", status: "HISTORICAL BASELINE INCOMPLETE", role: "Authorization for canonical workbook promotion", blocking_issue: "Criterion H material thermal fuel attribution" },
  { artifact: "Fase_5_PyPSA_Eur_Italia_2040_2050_Model_Ready_v2.9.1_HISTORICAL_BASELINE_INTEGRATED.xlsx", status: "NOT_CREATED_BY_DESIGN", role: "Future controlled workbook successor", blocking_issue: "Freeze gate has not passed" },
];

const outputs = [];
outputs.push(await authorCsv(capacity, "Capacity", path.join(normalizedDir, "MEM_Historical_Capacity_By_Zone_Technology.csv")));
outputs.push(await authorCsv(generation, "Generation", path.join(normalizedDir, "MEM_Historical_Generation_By_Zone_Technology.csv")));
outputs.push(await authorCsv(storage, "Storage", path.join(normalizedDir, "MEM_Historical_Storage_By_Zone_Technology.csv")));
outputs.push(await authorCsv(taxonomy, "Taxonomy", path.join(normalizedDir, "MEM_HISTORICAL_TECHNOLOGY_TAXONOMY.csv")));
outputs.push(await authorCsv(completion, "Completion", path.join(analysisDir, "MEM_HISTORICAL_BASELINE_COMPLETION_STATUS.csv")));
outputs.push(await authorCsv(integrityQa, "TaxonomyQA", path.join(qaDir, "MEM_HISTORICAL_TAXONOMY_INTEGRITY_QA.csv")));
outputs.push(await authorCsv(artifactSupersession, "Supersession", path.join(qaDir, "MEM_HISTORICAL_ARTIFACT_SUPERSESSION_REGISTER.csv")));
outputs.push(await authorCsv(blankSemantics, "BlankSemantics", path.join(qaDir, "MEM_RENEWABLE_CAPACITY_BLANK_SEMANTICS_QA.csv")));
outputs.push(await authorCsv(hydroReconciliation, "HydroBridge", path.join(analysisDir, "MEM_Hydro_Pumping_Perimeter_Reconciliation.csv")));
outputs.push(await authorCsv(thermalPerimeter, "ThermalPerimeter", path.join(analysisDir, "MEM_Thermal_Perimeter_Reconciliation_2024_2040.csv")));
outputs.push(await authorCsv(thermalFuelControls2024, "FuelControls", path.join(analysisDir, "MEM_Thermal_Fuel_National_Controls_2024.csv")));
outputs.push(await authorCsv(qaChecks, "Phase2QA", path.join(qaDir, "MEM_HISTORICAL_BASELINE_PHASE2_QA.csv")));
outputs.push(await authorCsv(sourceManifest, "SourceManifest", path.join(manifestDir, "MEM_HISTORICAL_BASELINE_SOURCE_MANIFEST.csv")));
outputs.push(await authorCsv(derivationManifest, "DerivationManifest", path.join(manifestDir, "MEM_HISTORICAL_BASELINE_DERIVATION_MANIFEST.csv")));
outputs.push(await authorCsv(gapRegister, "GapRegister", gapFile));
outputs.push(await authorCsv(decisionRegister, "DecisionRegister", decisionFile));
outputs.push(await authorCsv(dataStatus, "DataStatus", path.join(phaseRoot, "DATA_STATUS_HISTORICAL_BASELINE_UPDATED_20260901.csv")));
outputs.push(await authorCsv(sourceManifest, "SourceManifest", path.join(phaseRoot, "SOURCE_MANIFEST_HISTORICAL_BASELINE_UPDATED_20260901.csv")));
outputs.push(await authorCsv(derivationManifest, "DerivationManifest", path.join(phaseRoot, "DERIVATION_MANIFEST_HISTORICAL_BASELINE_UPDATED_20260901.csv")));

const summary = {
  generated_at: new Date().toISOString(),
  final_status: "HISTORICAL BASELINE INCOMPLETE",
  single_material_blocker: "Complete official GEM Global Oil and Gas Plant Tracker August-2026 release is not present; material zone x technology thermal fuel/plant attribution and current-2026 fleet remain unresolved.",
  controlled_non_material_residuals: [
    "Observed Beleolico annual generation is not separately published for 2022-2024; Terna wind total remains controlling.",
    "Renewable-capacity all-blank groups with positive generation are all below 1 GWh/year and preserved as controlled residuals.",
    "2019-2020 BESS and historical zonal BESS are not reconstructed from national-only evidence.",
    "Hydro basin/reservoir zonal MW is not allocated from production shares; total hydro and pumped storage controls remain explicit.",
  ],
  gem_integrated: false,
  current_2026_fleet_resolved: false,
  workbook_successor_authorized: false,
  workbook_successor_created: false,
  capacity_rows: capacity.length,
  generation_rows: generation.length,
  storage_rows: storage.length,
  taxonomy_integrity_failures: integrityQa.filter((row) => row.status === "FAIL").length,
  "2024_thermoelectric_capacity_reconciliation_MW": {
    raw_source_sum: edgeAudit.capacity_duplicate_reconciliation.raw_source_sum_MW,
    exact_duplicate_normalized: nationalThermal.thermoelectric_conversion_total_NET_MW,
    independent_published_control_rounded: edgeAudit.capacity_duplicate_reconciliation.independent_terna_published_control_MW_rounded_0_1,
  },
  unresolved_2024_thermal_fuel_allocation_MW: round(unresolvedThermal2024MW),
  outputs,
};
await fs.writeFile(path.join(qaDir, "MEM_HISTORICAL_BASELINE_PHASE2_BUILD_SUMMARY.json"), `${JSON.stringify(summary, null, 2)}\n`, "utf8");

const stabilityPath = path.join(qaDir, "MEM_HISTORICAL_SERIES_STABILITY_AND_QA_2019_2025.json");
const stability = JSON.parse(await fs.readFile(stabilityPath, "utf8"));
stability.phase2_closure = {
  generated_at: summary.generated_at,
  final_capacity_rows: capacity.length,
  final_generation_rows: generation.length,
  final_storage_rows: storage.length,
  taxonomy_integrity_failures: integrityQa.filter((row) => row.status === "FAIL").length,
  fuel_cell_exception_groups_remaining: generation.filter((row) => String(row.status).includes("UNRESOLVED_GT2_PAIR_EXCEPTION")).length,
  thermoelectric_2024_raw_source_sum_MW: edgeAudit.capacity_duplicate_reconciliation.raw_source_sum_MW,
  thermoelectric_2024_exact_duplicate_normalized_MW: nationalThermal.thermoelectric_conversion_total_NET_MW,
  thermoelectric_2024_published_control_MW_rounded: edgeAudit.capacity_duplicate_reconciliation.independent_terna_published_control_MW_rounded_0_1,
  DDS_comparable_2024_current_perimeter_NET_MW: nationalThermal.DDS_comparable_current_perimeter_NET_MW,
  freeze_criteria: qaChecks,
  single_material_empirical_blocker: summary.single_material_blocker,
  workbook_successor_authorized: false,
};
stability.final_status = "HISTORICAL BASELINE INCOMPLETE";
stability.workbook_successor_authorized = false;
await fs.writeFile(stabilityPath, `${JSON.stringify(stability, null, 2)}\n`, "utf8");

process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);

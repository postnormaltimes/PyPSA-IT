import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { Workbook } from "@oai/artifact-tool";
import { detectTernaDecimalConventions, parseTernaNumber } from "../scripts/terna_numeric_parser.mjs";

const phaseRoot = path.resolve("..");
const hbRoot = path.join(phaseRoot, "historical_baseline");
const normalizedDir = path.join(hbRoot, "normalized");
const analysisDir = path.join(hbRoot, "analysis");
const manifestDir = path.join(hbRoot, "manifests");
const qaDir = path.join(hbRoot, "qa");
const canonicalZones = ["NORD", "CNOR", "CSUD", "SUD", "CALA", "SICI", "SARD"];

const sha256File = async (file) => crypto.createHash("sha256").update(await fs.readFile(file)).digest("hex");
const sum = (values) => values.reduce((a, b) => a + b, 0);
const round = (value, digits = 9) => Number(Number(value).toFixed(digits));
const normalizeName = (value) => String(value ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[’']/g, " ").replace(/[^a-z0-9]+/g, " ").trim().replace(/\s+/g, " ");

async function readCsvObjects(file, sheetName) {
  const workbook = await Workbook.fromCSV(await fs.readFile(file, "utf8"), { sheetName });
  const values = workbook.worksheets.getItem(sheetName).getUsedRange().values;
  const headers = values[0].map(String);
  return values.slice(1).filter((row) => row.some((v) => v !== null && v !== "")).map((row) => Object.fromEntries(headers.map((header, index) => [header, row[index]])));
}

const crosswalk = await readCsvObjects(path.join(phaseRoot, "normalized", "MEM_Province_Region_MarketZone_Crosswalk.csv"), "Crosswalk");
const crosswalkByPair = new Map(crosswalk.map((row) => [`${normalizeName(row.terna_region_name)}\u0000${normalizeName(row.terna_province_name)}`, row]));
const mapGeo = (region, province) => {
  const geo = crosswalkByPair.get(`${normalizeName(region)}\u0000${normalizeName(province)}`);
  if (!geo) throw new Error(`Unmapped hydro source observation ${region}/${province}.`);
  return geo;
};

const rawPath = path.join(phaseRoot, "raw", "terna", "historical_2019_2025_api", "renewable-sources-production_2024_RAW.json");
const metadataPath = path.join(phaseRoot, "raw", "terna", "historical_2019_2025_api", "renewable-sources-production_2024_RAW.metadata.json");
const [payload, metadata] = await Promise.all([fs.readFile(rawPath, "utf8").then(JSON.parse), fs.readFile(metadataPath, "utf8").then(JSON.parse)]);
const rawSha = await sha256File(rawPath);
if (rawSha !== metadata.raw_sha256) throw new Error("2024 renewable production raw hash mismatch.");
const sourceRows = payload.renewable_sources.filter((row) => row.production_type === "Netta" && row.renewable_source === "Idrico");
if (sourceRows.length !== 107) throw new Error(`Expected 107 Netta Idrico rows, got ${sourceRows.length}.`);
const convention = detectTernaDecimalConventions(sourceRows.map((row) => row.production_GWh));
const hydroProduction = sourceRows.map((row, index) => {
  const parsed = parseTernaNumber(row.production_GWh, convention);
  const geo = mapGeo(row.region, row.province);
  return {
    record_id: `TERNA2024_HYDRO_RENEWABLE_NET_${String(index + 1).padStart(3, "0")}`,
    year: 2024,
    production_type_original: row.production_type,
    production_basis: "NET",
    region_code: geo.region_code,
    region_original: row.region,
    region_normalized: geo.region_name,
    province_code: geo.province_code,
    province_original: row.province,
    province_normalized: geo.province_name,
    market_zone: geo.market_zone,
    source_label_original: row.renewable_source,
    technology: "HYDRO_TOTAL_CONTROL",
    fuel_source: "WATER",
    generation_NET_GWh_raw: parsed.raw_value,
    generation_NET_GWh: parsed.value,
    raw_cell_type: parsed.raw_cell_type,
    parser_status: parsed.parser_status,
    statistical_perimeter: "RENEWABLE_SOURCE_HYDRO; EXCLUDES PUMPED-DISCHARGE COMPONENT",
    source_id: metadata.source_id,
    archived_raw_file: path.relative(phaseRoot, rawPath).replaceAll("\\", "/"),
    raw_source_sha256: rawSha,
  };
});

const hydroCapacity = await readCsvObjects(path.join(normalizedDir, "Terna_Hydro_Capacity_2024_NET.csv"), "HydroCap");
const typeZone = await readCsvObjects(path.join(analysisDir, "Terna_Hydro_2024_Zone_Type_Production.csv"), "HydroTypes");
const zoneSummary = [];
const pumping = [];
for (const scope of [...canonicalZones.map((zone) => ({ geographic_scope: "MARKET_ZONE", market_zone: zone })), { geographic_scope: "NATIONAL", market_zone: "" }]) {
  const zones = scope.market_zone ? [scope.market_zone] : canonicalZones;
  const cap = round(sum(hydroCapacity.filter((row) => zones.includes(row.market_zone)).map((row) => Number(row.capacity_NET_MW))));
  const renewable = round(sum(hydroProduction.filter((row) => zones.includes(row.market_zone)).map((row) => row.generation_NET_GWh)));
  const typeValue = (type) => round(sum(typeZone.filter((row) => zones.includes(row.market_zone) && row.hydro_type === type).map((row) => Number(row.net_generation_GWh))));
  const fluente = typeValue("HYDRO_RUN_OF_RIVER");
  const bacino = typeValue("HYDRO_BASIN");
  const serbatoio = typeValue("HYDRO_RESERVOIR_INCL_PUMPING");
  const typeTotal = round(fluente + bacino + serbatoio);
  const pumpingComponent = round(typeTotal - renewable);
  zoneSummary.push({
    year: 2024,
    geographic_scope: scope.geographic_scope,
    market_zone: scope.market_zone,
    technology: "HYDRO_TOTAL_CONTROL",
    capacity_basis: "NET",
    capacity_NET_MW: cap,
    generation_basis: "NET",
    generation_NET_GWh: renewable,
    observed_CF: cap > 0 ? round(renewable / (cap * 8.76)) : "",
    cf_status: cap > 0 ? "CALCULATED_WITH_RENEWABLE_SOURCE_NET_GENERATION" : "NOT_CALCULATED_ZERO_CAPACITY",
    interpretation: "HISTORICAL_CHARACTERIZATION_ONLY; TYPE-LEVEL SERIES HAS A BROADER PUMPING-INCLUSIVE PERIMETER",
    generation_perimeter_note: "Official renewable-source hydro NET generation is the additive annual control; detailed hydric types are analytical because Serbatoio includes eventual pumping.",
    source_id: metadata.source_id,
  });
  pumping.push({
    year: 2024,
    geographic_scope: scope.geographic_scope,
    market_zone: scope.market_zone,
    hydro_total_capacity_NET_MW: cap,
    renewable_source_hydro_NET_generation_GWh: renewable,
    run_of_river_net_generation_GWh: fluente,
    basin_net_generation_GWh: bacino,
    reservoir_including_eventual_pumping_net_generation_GWh: serbatoio,
    hydric_type_total_including_eventual_pumping_NET_GWh: typeTotal,
    hydric_minus_renewable_statistical_difference_GWh: pumpingComponent,
    implied_national_pumping_discharge_component_GWh: scope.geographic_scope === "NATIONAL" ? pumpingComponent : "",
    pumping_component_evidence_class: "PROJECT DERIVATION FROM TWO DIRECT TERNA ENERGY PERIMETERS",
    pumped_hydro_discharge_capacity_MW: "",
    pumped_hydro_charge_capacity_MW: "",
    pumped_hydro_energy_MWh: "",
    pumped_hydro_roundtrip_efficiency: "",
    model_input_status: "NOT YET A PHYSICAL STORAGE INPUT",
    current_perimeter_rule: "USE RENEWABLE-SOURCE HYDRO AS ADDITIVE ANNUAL ENERGY CONTROL; RETAIN HYDRIC TYPES AS NON-ADDITIVE ANALYTICAL DETAIL UNTIL PUMPING IS PHYSICALLY SPLIT",
    double_count_guardrail: "DO NOT ADD HYDRIC TYPE TOTAL TO RENEWABLE-SOURCE HYDRO; DO NOT ADD PUMPED MW ON TOP OF TOTAL HYDRO MW WITHOUT ASSET RECONCILIATION",
    zonal_interpretation: scope.geographic_scope === "NATIONAL" ? "NATIONAL DIFFERENCE IS A PUMPING-DISCHARGE PERIMETER PROXY" : Math.abs(pumpingComponent) <= 0.00001 ? "ZERO WITHIN SOURCE DISPLAY/ROUNDING PRECISION" : pumpingComponent > 0 ? "POSITIVE ZONAL DIFFERENCE IS PUMPING/CLASSIFICATION PROXY ONLY; NOT AN ALLOCATION" : "NEGATIVE ZONAL DIFFERENCE SHOWS CLASSIFICATION/ALLOCATION MISMATCH; NOT NEGATIVE PUMPING",
    status: scope.geographic_scope === "NATIONAL" ? "NATIONAL ENERGY PERIMETER RECONCILED; PHYSICAL PUMPED STORAGE REQUIRES DATA" : "ZONAL DIFFERENCE RETAINED FOR QA; NO PUMPED-STORAGE ALLOCATION",
    source_id: `${metadata.source_id}|TERNA_DOWNLOAD_CENTER_HYDRO_PRODUCTION_BY_TYPE_2024|TERNA_DOWNLOAD_CENTER_HYDRO_CAPACITY_2024`,
  });
}

let derivation = await readCsvObjects(path.join(manifestDir, "MEM_HISTORICAL_BASELINE_DERIVATION_MANIFEST.csv"), "Derivation");
derivation = derivation.filter((row) => row.derivation_id !== "DER-HIST-007" && row.derivation_id !== "DER-HIST-014");
derivation.push(
  { derivation_id: "DER-HIST-007", output: "Terna_Hydro_Production_2024_NET.csv", evidence_class: "DIRECT SOURCE ENERGY", inputs: "Terna renewable-sources-production API 2024 Netta Idrico; reviewed crosswalk", method: "Filter explicit Netta and Idrico rows; robust numeric parse; map 107 provinces to MEM zones.", key_guardrail: "This is the additive renewable-hydro annual energy control; hydric type total has a broader pumping-inclusive perimeter.", status: "VALIDATED_2024" },
  { derivation_id: "DER-HIST-014", output: "MEM_Hydro_Pumping_Perimeter_Reconciliation.csv", evidence_class: "PROJECT DERIVATION / QA RECONCILIATION ONLY", inputs: "Renewable-source hydro NET generation; detailed hydric type NET generation; total hydro NET capacity", method: "At zone and national levels calculate hydric type total minus renewable-source hydro as the statistically implied pumping-discharge component.", key_guardrail: "The difference is not a physical pumped-storage MW/MWh/efficiency allocation and is not a PyPSA input.", status: "ENERGY_PERIMETER_VALIDATED_PHYSICAL_STORAGE_REQUIRES_DATA" },
);

let qa = await readCsvObjects(path.join(qaDir, "MEM_HISTORICAL_BASELINE_2024_QA.csv"), "QA");
qa = qa.filter((row) => row.check_id !== "HIST-QA-014" && row.check_id !== "HIST-QA-017" && row.check_id !== "HIST-QA-018");
const nationalSummary = zoneSummary.find((row) => row.geographic_scope === "NATIONAL");
const nationalPumping = pumping.find((row) => row.geographic_scope === "NATIONAL");
qa.push(
  { check_id: "HIST-QA-014", description: "2024 renewable-source hydro NET control is mapped across seven zones", status: Math.abs(nationalSummary.generation_NET_GWh - 52391.704319) < 1e-6 ? "PASS" : "FAIL", severity: "ERROR", observed: nationalSummary.generation_NET_GWh, expected: 52391.704319 },
  { check_id: "HIST-QA-017", description: "Detailed hydric national total exceeds renewable-source hydro by the pumping-discharge perimeter component", status: Math.abs(nationalPumping.implied_national_pumping_discharge_component_GWh - 1583.805583) < 1e-6 ? "PASS" : "FAIL", severity: "ERROR", observed: nationalPumping.implied_national_pumping_discharge_component_GWh, expected: 1583.805583 },
  { check_id: "HIST-QA-018", description: "Signed zonal statistical differences sum to the national perimeter difference", status: Math.abs(sum(pumping.filter((row) => row.geographic_scope === "MARKET_ZONE").map((row) => Number(row.hydric_minus_renewable_statistical_difference_GWh))) - Number(nationalPumping.hydric_minus_renewable_statistical_difference_GWh)) < 1e-6 ? "PASS" : "FAIL", severity: "ERROR", observed: pumping.filter((row) => row.geographic_scope === "MARKET_ZONE").map((row) => `${row.market_zone}:${row.hydric_minus_renewable_statistical_difference_GWh}`).join("|"), expected: `sum=${nationalPumping.hydric_minus_renewable_statistical_difference_GWh}; signed zonal values are QA only` },
);
if (qa.some((row) => row.status === "FAIL" && row.severity === "ERROR")) throw new Error("Hydro reconciliation QA failed.");

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
async function authorCsv(rows, sheetName, file) {
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
outputs.push(await authorCsv(hydroProduction, "HydroProd", path.join(normalizedDir, "Terna_Hydro_Production_2024_NET.csv")));
outputs.push(await authorCsv(zoneSummary, "HydroSummary", path.join(analysisDir, "Terna_Hydro_2024_Zone_Summary.csv")));
outputs.push(await authorCsv(pumping, "HydroPumping", path.join(analysisDir, "MEM_Hydro_Pumping_Perimeter_Reconciliation.csv")));
outputs.push(await authorCsv(derivation, "Derivation", path.join(manifestDir, "MEM_HISTORICAL_BASELINE_DERIVATION_MANIFEST.csv")));
outputs.push(await authorCsv(qa, "QA", path.join(qaDir, "MEM_HISTORICAL_BASELINE_2024_QA.csv")));

const summaryPath = path.join(qaDir, "MEM_HISTORICAL_BASELINE_2024_BUILD_SUMMARY.json");
const summary = JSON.parse(await fs.readFile(summaryPath, "utf8"));
summary.exact_national_totals.hydro_generation_NET_GWh = nationalSummary.generation_NET_GWh;
summary.exact_national_totals.hydro_observed_CF = nationalSummary.observed_CF;
summary.exact_national_totals.hydric_type_total_including_eventual_pumping_NET_GWh = nationalPumping.hydric_type_total_including_eventual_pumping_NET_GWh;
summary.exact_national_totals.implied_pumping_discharge_component_GWh = nationalPumping.implied_national_pumping_discharge_component_GWh;
summary.hydro_energy_perimeter_rule = nationalPumping.current_perimeter_rule;
for (const output of outputs) {
  const existing = summary.outputs.find((row) => row.file === output.file);
  if (existing) Object.assign(existing, output);
}
await fs.writeFile(summaryPath, `${JSON.stringify(summary, null, 2)}\n`, "utf8");

console.log(JSON.stringify({ national_summary: nationalSummary, national_pumping_reconciliation: nationalPumping, outputs }, null, 2));

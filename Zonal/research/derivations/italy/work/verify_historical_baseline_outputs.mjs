import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { Workbook } from "@oai/artifact-tool";

const phaseRoot = path.resolve("..");
const releaseRoot = path.resolve("../..");
const hbRoot = path.join(phaseRoot, "historical_baseline");
const expectedZones = ["CALA", "CNOR", "CSUD", "NORD", "SARD", "SICI", "SUD"];
const sha256File = async (file) => crypto.createHash("sha256").update(await fs.readFile(file)).digest("hex");

const columnName = (columnCount) => {
  let n = columnCount;
  let result = "";
  while (n > 0) {
    n -= 1;
    result = String.fromCharCode(65 + (n % 26)) + result;
    n = Math.floor(n / 26);
  }
  return result;
};

async function readCsv(relativePath, sheetName) {
  const file = path.join(phaseRoot, relativePath);
  const workbook = await Workbook.fromCSV(await fs.readFile(file, "utf8"), { sheetName });
  const sheet = workbook.worksheets.getItem(sheetName);
  const used = sheet.getUsedRange();
  const values = used.values;
  const headers = values[0].map(String);
  const rows = values.slice(1)
    .filter((row) => row.some((value) => value !== null && value !== ""))
    .map((row) => Object.fromEntries(headers.map((header, index) => [header, row[index]])));
  await workbook.inspect({
    kind: "table",
    sheetId: sheetName,
    range: `A1:${columnName(headers.length)}${Math.min(rows.length + 1, 4)}`,
    tableMaxRows: 4,
    tableMaxCols: Math.min(headers.length, 24),
    maxChars: 5000,
  });
  return { file, headers, rows };
}

const numberSum = (rows, field) => rows.reduce((total, row) => {
  const value = row[field];
  return total + (value === null || value === "" || value === undefined ? 0 : Number(value));
}, 0);

const checks = [];
function addCheck(check, observed, expected, tolerance = 0) {
  let status;
  if (typeof observed === "number" && typeof expected === "number") {
    status = Number.isFinite(observed) && Math.abs(observed - expected) <= tolerance ? "PASS" : "FAIL";
  } else {
    status = String(observed) === String(expected) ? "PASS" : "FAIL";
  }
  checks.push({ check, observed, expected, tolerance, status });
}

addCheck(
  "Controlling v2.9 workbook SHA-256 unchanged",
  await sha256File(path.join(releaseRoot, "Fase_5_PyPSA_Eur_Italia_2040_2050_Model_Ready_v2.9_MEM_ARCHITECTURE_REFINED.xlsx")),
  "4a59c80bdf4da33d825ff94ea5617c03c325ca6fbc26233e7ed633ed6f910640",
);

const rawFiles = [
  ["raw/terna/historical_2024/Terna_Bioenergy_Capacity_2024_RAW.xlsx", 9385, "b6d8700e000e9f0766949a022c8e2a27eff41cb6cc14d3bc8005016e1820ea41"],
  ["raw/terna/historical_2024/Terna_Geothermal_Capacity_2024_RAW.xlsx", 8451, "e4c22a28edf6cc6679bebed858a539d74284b4b9b20822b3d8ae1432613ea3a1"],
  ["raw/terna/historical_2024/Terna_Hydro_Capacity_2024_RAW.xlsx", 9457, "0ecc2fa25a0a831f603789791dd0b19597b131af53dd7df9b9cb3dd2b73f4277"],
  ["raw/terna/historical_2024/Terna_Hydro_Production_By_Type_2024_RAW.xlsx", 17150, "e5d0c67eba558f692fb66c9ba806356fdd3f4b2dfe7bb270ff5a463506640e9f"],
  ["raw/terna/historical_2024/Terna_Bioenergy_Production_2024_RAW.xlsx", 9656, "94b6caceca9ca19e60f7471a8584019d9db75efb0bb11ac90fb52f954ab95b0a"],
  ["raw/terna/historical_2024/Terna_Geothermal_Production_2024_RAW.xlsx", 8369, "addcfd865eff34850bad31e09e603db41c2524936ba402baa07875e039a51d91"],
  ["raw/terna/historical_2024/Terna_Thermoelectric_Heat_2024_RAW.xlsx", 17887, "06a18e50214e6e5bd882819a1dde6ba1a0ee0457e0c6728727c12640a4e4a72c"],
];
for (const [relativePath, expectedBytes, expectedHash] of rawFiles) {
  const file = path.join(phaseRoot, relativePath);
  const stat = await fs.stat(file);
  addCheck(`Raw bytes: ${relativePath}`, stat.size, expectedBytes);
  addCheck(`Raw SHA-256: ${relativePath}`, await sha256File(file), expectedHash);
}

const requiredCsv = [
  ["historical_baseline/normalized/Terna_Bioenergy_Capacity_2024_NET.csv", "BioCap"],
  ["historical_baseline/normalized/Terna_Bioenergy_Production_2024_NET.csv", "BioGen"],
  ["historical_baseline/analysis/Terna_Bioenergy_2024_Zone_Summary.csv", "BioSummary"],
  ["historical_baseline/normalized/Terna_Geothermal_Capacity_2024_NET.csv", "GeoCap"],
  ["historical_baseline/normalized/Terna_Geothermal_Production_2024_NET.csv", "GeoGen"],
  ["historical_baseline/analysis/Terna_Geothermal_2024_Zone_Summary.csv", "GeoSummary"],
  ["historical_baseline/normalized/Terna_Hydro_Capacity_2024_NET.csv", "HydroCap"],
  ["historical_baseline/normalized/Terna_Hydro_Production_2024_NET.csv", "HydroGen"],
  ["historical_baseline/analysis/Terna_Hydro_2024_Zone_Summary.csv", "HydroSummary"],
  ["historical_baseline/analysis/Terna_Hydro_2024_Zone_Type_Production.csv", "HydroType"],
  ["historical_baseline/normalized/Terna_Thermoelectric_Heat_2024_Canonical.csv", "Heat"],
  ["historical_baseline/analysis/Terna_CHP_Heat_and_Electricity_2024_Zone_Technology.csv", "CHP"],
  ["historical_baseline/analysis/MEM_Hydro_Pumping_Perimeter_Reconciliation.csv", "HydroRecon"],
  ["historical_baseline/analysis/MEM_Thermal_Perimeter_Reconciliation_2024_2040.csv", "ThermalRecon"],
  ["historical_baseline/normalized/MEM_HISTORICAL_TECHNOLOGY_TAXONOMY.csv", "Taxonomy"],
  ["historical_baseline/analysis/MEM_HISTORICAL_BASELINE_COMPLETION_STATUS.csv", "Completion"],
  ["historical_baseline/normalized/MEM_Historical_Capacity_By_Zone_Technology.csv", "HistoryCap"],
  ["historical_baseline/normalized/MEM_Historical_Generation_By_Zone_Technology.csv", "HistoryGen"],
  ["historical_baseline/manifests/MEM_HISTORICAL_BASELINE_SOURCE_MANIFEST.csv", "SourceManifest"],
  ["historical_baseline/manifests/MEM_HISTORICAL_BASELINE_DERIVATION_MANIFEST.csv", "DerivationManifest"],
  ["SOURCE_MANIFEST_HISTORICAL_BASELINE_UPDATED_20260901.csv", "SourceSuccessor"],
  ["DERIVATION_MANIFEST_HISTORICAL_BASELINE_UPDATED_20260901.csv", "DerivationSuccessor"],
  ["DATA_STATUS_HISTORICAL_BASELINE_UPDATED_20260901.csv", "DataStatus"],
  ["docs/MEM_v2.9_EMPIRICAL_GAP_AND_ACQUISITION_REGISTER_HISTORICAL_BASELINE_UPDATED_20260901.csv", "GapRegister"],
  ["docs/MEM_v2.9_HISTORICAL_BASELINE_DECISION_REGISTER_20260901.csv", "DecisionRegister"],
];
const loaded = new Map();
for (const [relativePath, sheetName] of requiredCsv) {
  const table = await readCsv(relativePath, sheetName);
  loaded.set(sheetName, table);
  addCheck(`Non-empty CSV: ${relativePath}`, table.rows.length > 0 ? "YES" : "NO", "YES");
}

const historyCap = loaded.get("HistoryCap").rows;
const historyGen = loaded.get("HistoryGen").rows;
const historyYearsCap = [...new Set(historyCap.map((row) => Number(row.year)))].sort((a, b) => a - b);
const historyYearsGen = [...new Set(historyGen.map((row) => Number(row.year)))].sort((a, b) => a - b);
const capZones = [...new Set(historyCap.map((row) => String(row.market_zone)))].sort();
const genZones = [...new Set(historyGen.map((row) => String(row.market_zone)))].sort();
addCheck("Historical capacity rows", historyCap.length, 674);
addCheck("Historical generation rows", historyGen.length, 805);
addCheck("Historical capacity years", historyYearsCap.join("|"), "2019|2020|2021|2022|2023|2024");
addCheck("Historical generation years", historyYearsGen.join("|"), "2019|2020|2021|2022|2023|2024");
addCheck("Historical capacity zones", capZones.join("|"), expectedZones.join("|"));
addCheck("Historical generation zones", genZones.join("|"), expectedZones.join("|"));
addCheck("CNORD emitted in historical tables", [...historyCap, ...historyGen].filter((row) => row.market_zone === "CNORD").length, 0);

const bio = loaded.get("BioSummary").rows.find((row) => row.geographic_scope === "NATIONAL");
const geo = loaded.get("GeoSummary").rows.find((row) => row.geographic_scope === "NATIONAL");
const hydro = loaded.get("HydroSummary").rows.find((row) => row.geographic_scope === "NATIONAL");
addCheck("2024 bioenergy NET MW", Number(bio.capacity_NET_MW), 3800.0923, 1e-9);
addCheck("2024 bioenergy NET GWh", Number(bio.generation_NET_GWh), 15699.033862, 1e-9);
addCheck("2024 bioenergy observed CF", Number(bio.observed_CF), 0.471600954, 1e-9);
addCheck("2024 geothermal NET MW", Number(geo.capacity_NET_MW), 771.79, 1e-9);
addCheck("2024 geothermal NET GWh", Number(geo.generation_NET_GWh), 5275.5733, 1e-9);
addCheck("2024 geothermal observed CF", Number(geo.observed_CF), 0.780308627, 1e-9);
addCheck("2024 hydro NET MW", Number(hydro.capacity_NET_MW), 19324.42439, 1e-9);
addCheck("2024 renewable-source hydro NET GWh", Number(hydro.generation_NET_GWh), 52391.704319, 1e-9);
addCheck("2024 hydro observed CF", Number(hydro.observed_CF), 0.309493729, 1e-9);

const hydroType = loaded.get("HydroType").rows;
addCheck("2024 detailed hydric NET GWh", numberSum(hydroType, "net_generation_GWh"), 53975.509902, 1e-8);
addCheck("Hydro type set", [...new Set(hydroType.map((row) => row.hydro_type))].sort().join("|"), "HYDRO_BASIN|HYDRO_RESERVOIR_INCL_PUMPING|HYDRO_RUN_OF_RIVER");
const hydroReconNational = loaded.get("HydroRecon").rows.find((row) => row.geographic_scope === "NATIONAL");
addCheck("2024 pumping-discharge perimeter proxy GWh", Number(hydroReconNational.implied_national_pumping_discharge_component_GWh), 1583.805583, 1e-9);
addCheck("Pumped discharge MW remains blank", hydroReconNational.pumped_hydro_discharge_capacity_MW ?? "", "");
addCheck("Pumped energy MWh remains blank", hydroReconNational.pumped_hydro_energy_MWh ?? "", "");

const thermalNational = loaded.get("ThermalRecon").rows.filter((row) => row.geographic_scope === "NATIONAL");
const thermoControl = thermalNational.find((row) => row.perimeter_item === "TERNA_THERMOELECTRIC_CONVERSION_TECHNOLOGY_TOTAL");
const bioSubset = thermalNational.find((row) => row.perimeter_item === "BIOENERGY_SOURCE_SUBSET");
const geoAdd = thermalNational.find((row) => row.perimeter_item === "GEOTHERMAL_SOURCE_TECHNOLOGY");
addCheck("2024 thermoelectric technology NET MW", Number(thermoControl.capacity_NET_MW), 60331.82927, 1e-8);
addCheck("Bioenergy additive adjustment MW", Number(bioSubset.additive_adjustment_MW), 0, 1e-12);
addCheck("Geothermal additive adjustment MW", Number(geoAdd.additive_adjustment_MW), 771.79, 1e-9);
addCheck("2024 DDS-comparable NET MW", Number(geoAdd.running_DDS_comparable_NET_MW), 61103.61927, 1e-8);

const taxonomy = loaded.get("Taxonomy").rows;
const requiredTechnologyCodes = [
  "CCGT_NON_CHP", "CCGT_CHP", "GT_NON_CHP", "GT_CHP",
  "INTERNAL_COMBUSTION_NON_CHP", "INTERNAL_COMBUSTION_CHP",
  "STEAM_CONDENSING", "STEAM_EXTRACTION_CHP", "STEAM_BACKPRESSURE_CHP",
  "FUEL_CELL_NON_CHP", "FUEL_CELL_CHP", "EXTERNAL_COMBUSTION",
  "TURBO_EXPANSION", "OTHER_THERMAL", "BIOENERGY", "GEOTHERMAL",
  "HYDRO_RUN_OF_RIVER", "HYDRO_BASIN", "HYDRO_RESERVOIR", "PUMPED_HYDRO",
  "SOLAR_PV", "WIND_ONSHORE", "WIND_OFFSHORE", "BESS", "OTHER_STORAGE",
];
const taxonomyCodes = new Set(taxonomy.filter((row) => row.taxonomy_dimension === "TECHNOLOGY").map((row) => String(row.code)));
addCheck("Required technology taxonomy codes missing", requiredTechnologyCodes.filter((code) => !taxonomyCodes.has(code)).join("|"), "");
const requiredFuelCodes = ["NATURAL_GAS", "COAL", "OIL", "BIOENERGY", "OTHER_FUEL", "MULTI_FUEL", "UNRESOLVED"];
const fuelCodes = new Set(taxonomy.filter((row) => row.taxonomy_dimension === "FUEL_SOURCE").map((row) => String(row.code)));
addCheck("Required fuel/source taxonomy codes missing", requiredFuelCodes.filter((code) => !fuelCodes.has(code)).join("|"), "");

const completion = loaded.get("Completion").rows;
const overall = completion.find((row) => row.technology === "__OVERALL__");
addCheck("Historical completion status", overall.status, "HISTORICAL BASELINE INCOMPLETE");
addCheck("Canonical workbook readiness", overall.ready_for_canonical_workbook, "NO");
addCheck("Completion matrix rows", completion.length, 28);

const fuelCellExceptions = historyGen.filter((row) => String(row.status).includes("UNRESOLVED_GT2_PAIR_EXCEPTION"));
addCheck("Retained 2019-2022 fuel-cell production exceptions", fuelCellExceptions.length, 8);

const requiredDocs = [
  "README.md",
  "historical_baseline/README.md",
  "docs/MEM_HISTORICAL_BASELINE_METHOD_AND_RESULTS_20260901.md",
  "docs/MEM_CHP_REPRESENTATION_ASSESSMENT.md",
  "docs/MEM_RENEWABLE_PROGRAMMABLE_BASELINE_2024.md",
  "qa/MEM_HISTORICAL_BASELINE_QA_REPORT_20260901.md",
];
const missingDocs = [];
for (const relativePath of requiredDocs) {
  try {
    await fs.access(path.join(phaseRoot, relativePath));
  } catch {
    missingDocs.push(relativePath);
  }
}
addCheck("Required historical documentation missing", missingDocs.join("|"), "");
const methodText = await fs.readFile(path.join(phaseRoot, "docs/MEM_HISTORICAL_BASELINE_METHOD_AND_RESULTS_20260901.md"), "utf8");
addCheck("Method note contains controlling incomplete gate", methodText.includes("**HISTORICAL BASELINE INCOMPLETE**") ? "YES" : "NO", "YES");

async function walk(directory) {
  const results = [];
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) results.push(...await walk(full));
    else results.push(full);
  }
  return results;
}
const prematureWorkbooks = (await walk(releaseRoot)).filter((file) =>
  path.basename(file).toLowerCase().includes("v2.9.1") && file.toLowerCase().endsWith(".xlsx"),
);
addCheck("No premature v2.9.1 workbook", prematureWorkbooks.length, 0);

const outputHashes = {};
for (const [relativePath] of requiredCsv) outputHashes[relativePath] = await sha256File(path.join(phaseRoot, relativePath));
const result = {
  generated_at_utc: new Date().toISOString(),
  overall_verification_status: checks.some((check) => check.status === "FAIL") ? "FAIL" : "PASS",
  historical_baseline_gate: overall.status,
  canonical_workbook_ready: overall.ready_for_canonical_workbook,
  checks,
  missing_docs: missingDocs,
  premature_v2_9_1_workbooks: prematureWorkbooks.map((file) => path.relative(releaseRoot, file).replaceAll("\\", "/")),
  raw_evidence: Object.fromEntries(await Promise.all(rawFiles.map(async ([relativePath]) => [relativePath, {
    bytes: (await fs.stat(path.join(phaseRoot, relativePath))).size,
    sha256: await sha256File(path.join(phaseRoot, relativePath)),
  }]))),
  output_hashes: outputHashes,
};
const output = path.join(phaseRoot, "qa/HISTORICAL_BASELINE_FINAL_VERIFICATION_20260901.json");
await fs.writeFile(output, `${JSON.stringify(result, null, 2)}\n`, "utf8");
console.log(JSON.stringify(result, null, 2));
if (result.overall_verification_status !== "PASS") process.exitCode = 1;

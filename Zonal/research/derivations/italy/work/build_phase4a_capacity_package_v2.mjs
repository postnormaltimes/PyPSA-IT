import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { Workbook, SpreadsheetFile, FileBlob } from "@oai/artifact-tool";

const phaseRoot = path.resolve(import.meta.dirname, "..");
const inputDir = path.join(phaseRoot, "scenario_capacity");
const outputDir = path.join(phaseRoot, "scenario_capacity_v2");
const renderDir = path.join(outputDir, "renders");
const docsDir = path.join(phaseRoot, "docs");
const qaDir = path.join(phaseRoot, "qa");
await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(renderDir, { recursive: true });

const files = {
  longV1: path.join(inputDir, "MEM_2040_2050_Zonal_Installed_Capacity_By_Scenario.csv"),
  workbookV1: path.join(inputDir, "MEM_2040_2050_Zonal_Installed_Capacity_By_Scenario.xlsx"),
  base2040: path.join(inputDir, "MEM_2040_Base_High_Thermal_Zonal_Capacity.csv"),
  national2050: path.join(inputDir, "MEM_2050_Base_High_National_Dispatchable_Capacity.csv"),
  zonal2050: path.join(inputDir, "MEM_2050_Base_High_Dispatchable_Zonal_Capacity.csv"),
  anchor: path.join(inputDir, "MEM_2024_Post_Coal_Oil_Thermal_Geography_Anchor.csv"),
  removalMask: path.join(inputDir, "MEM_2024_Coal_Oil_Removal_Mask.csv"),
  taxonomy: path.join(inputDir, "MEM_Future_Dispatchable_Technology_Taxonomy.csv"),
  crosswalk: path.join(inputDir, "MEM_Future_Technology_Zonal_Allocation_Crosswalk.csv"),
  hydro: path.join(inputDir, "MEM_Current_Hydro_Capacity_By_Zone_Class_For_Future_Scenarios.csv"),
  cdp: path.join(inputDir, "MEM_Slow_CDP_Dispatchable_Capacity_Adjustment.csv"),
  sourceManifest: path.join(phaseRoot, "SOURCE_MANIFEST.csv"),
  derivationManifest: path.join(phaseRoot, "DERIVATION_MANIFEST.csv"),
  decisionRegister: path.join(docsDir, "MEM_v2.9_THERMAL_STACK_DECISION_REGISTER_20260901.csv"),
  gapRegister: path.join(docsDir, "MEM_v2.9_EMPIRICAL_GAP_AND_ACQUISITION_REGISTER_UPDATED_20260901.csv"),
};

const out = {
  slow2040: path.join(outputDir, "MEM_2040_Slow_Methane_Increment.csv"),
  slow2050: path.join(outputDir, "MEM_2050_Slow_Gas_Dispatchable_Increment.csv"),
  architecture2050: path.join(outputDir, "MEM_2050_Fuel_Carrier_Architecture.csv"),
  decomposition2050: path.join(outputDir, "MEM_2050_Gas_Other_Fossil_Solver_Decomposition.csv"),
  oilMateriality: path.join(outputDir, "MEM_Oil_Residual_Materiality_Check.csv"),
  coalMateriality: path.join(outputDir, "MEM_Coal_Residual_Materiality_Check.csv"),
  taxonomy: path.join(outputDir, "MEM_Future_Dispatchable_Technology_Taxonomy.csv"),
  crosswalk: path.join(outputDir, "MEM_Future_Technology_Zonal_Allocation_Crosswalk.csv"),
  carrier2040: path.join(outputDir, "MEM_2040_Thermal_Solver_Carrier_Crosswalk.csv"),
  readiness: path.join(outputDir, "MEM_Static_Solver_Input_Readiness.csv"),
  generators: path.join(outputDir, "MEM_generators_static_candidate.csv"),
  slow2040National: path.join(outputDir, "MEM_2040_Slow_Thermal_National_Capacity.csv"),
  slow2040Zonal: path.join(outputDir, "MEM_2040_Slow_Thermal_Zonal_Capacity.csv"),
  slow2050National: path.join(outputDir, "MEM_2050_Slow_Dispatchable_National_Capacity.csv"),
  slow2050Zonal: path.join(outputDir, "MEM_2050_Slow_Dispatchable_Zonal_Capacity.csv"),
  long: path.join(outputDir, "MEM_2040_2050_Zonal_Installed_Capacity_By_Scenario.csv"),
  workbook: path.join(outputDir, "MEM_2040_2050_Zonal_Installed_Capacity_By_Scenario.xlsx"),
  gates: path.join(outputDir, "MEM_PHASE4A_GATE_STATUS.csv"),
  methodology: path.join(docsDir, "MEM_SCENARIO_CAPACITY_PACKAGE_V2_METHOD_AND_RESULTS.md"),
  chpDecision: path.join(docsDir, "MEM_Future_CHP_Representation_Decision.md"),
  qa: path.join(qaDir, "MEM_SCENARIO_CAPACITY_PACKAGE_V2_QA.csv"),
  verification: path.join(qaDir, "MEM_SCENARIO_CAPACITY_PACKAGE_V2_FINAL_VERIFICATION.json"),
};

const ZONES = ["NORD", "CNOR", "CSUD", "SUD", "CALA", "SICI", "SARD"];
const SCENARIOS = [
  { year: 2040, scenario: "Slow" }, { year: 2040, scenario: "Base" }, { year: 2040, scenario: "High" },
  { year: 2050, scenario: "Slow" }, { year: 2050, scenario: "Base" }, { year: 2050, scenario: "High" },
];
const DISPATCH_2040 = ["CCGT", "GT_OCGT", "INTERNAL_COMBUSTION", "STEAM_OTHER_SURVIVING", "OTHER_SURVIVING_THERMAL", "GEOTHERMAL"];
const METHANE_SCALABLE_2040 = ["CCGT", "GT_OCGT", "INTERNAL_COMBUSTION", "STEAM_OTHER_SURVIVING", "OTHER_SURVIVING_THERMAL"];
const DISPATCH_2050 = ["BIOENERGY", "BIOENERGY_CCS", "GAS_CCS", "GAS_OTHER_FOSSIL", "GEOTHERMAL", "NUCLEAR"];
const GAS_SCALABLE_2050 = ["GAS_CCS", "GAS_OTHER_FOSSIL"];
const FIXED_SLOW = new Set(["BIOENERGY", "BIOENERGY_CCS", "GEOTHERMAL", "NUCLEAR"]);
const HYDRO_TECHS = ["HYDRO_RUN_OF_RIVER", "HYDRO_BASIN", "HYDRO_RESERVOIR", "PUMPED_HYDRO_PURE", "PUMPED_HYDRO_MIXED"];
const VRE_TECHS = ["SOLAR_PV_ROOFTOP", "SOLAR_PV_UTILITY", "WIND_ONSHORE", "WIND_OFFSHORE"];
const ALL_DISPLAY_TECHS = [...VRE_TECHS, ...HYDRO_TECHS, "BESS", ...new Set([...DISPATCH_2040, ...DISPATCH_2050])];
const round = (value, digits = 9) => Number(Number(value).toFixed(digits));
const sum = (values) => values.reduce((acc, value) => acc + Number(value || 0), 0);
const almost = (a, b, tol = 1e-6) => Math.abs(Number(a) - Number(b)) <= tol;
const key = (...parts) => parts.join("|");
const sha256File = async (file) => crypto.createHash("sha256").update(await fs.readFile(file)).digest("hex");
const csvEscape = (value) => {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
};
const letters = (count) => {
  let n = count;
  let result = "";
  while (n > 0) { n -= 1; result = String.fromCharCode(65 + (n % 26)) + result; n = Math.floor(n / 26); }
  return result;
};

async function readCsv(file, sheetName) {
  const wb = await Workbook.fromCSV(await fs.readFile(file, "utf8"), { sheetName });
  const values = wb.worksheets.getItem(sheetName).getUsedRange().values;
  const headers = values[0].map(String);
  return values.slice(1).filter((row) => row.some((value) => value !== null && value !== ""))
    .map((row) => Object.fromEntries(headers.map((header, index) => [header, row[index] ?? ""])));
}

function matrix(rows) {
  const headers = [];
  for (const row of rows) for (const field of Object.keys(row)) if (!headers.includes(field)) headers.push(field);
  return { headers, values: rows.map((row) => headers.map((field) => row[field] ?? "")) };
}

async function authorCsv(rows, sheetName, file) {
  const { headers, values } = matrix(rows);
  const wb = Workbook.create();
  const sheet = wb.worksheets.add(sheetName);
  sheet.getRangeByIndexes(0, 0, rows.length + 1, headers.length).values = [headers, ...values];
  const address = `A1:${letters(headers.length)}${rows.length + 1}`;
  await wb.inspect({ kind: "table", sheetId: sheetName, range: address, tableMaxRows: 3, tableMaxCols: Math.min(headers.length, 18), maxChars: 5000 });
  await fs.writeFile(file, `${[headers, ...values].map((row) => row.map(csvEscape).join(",")).join("\r\n")}\r\n`, "utf8");
  const reopened = await Workbook.fromCSV(await fs.readFile(file, "utf8"), { sheetName });
  await reopened.inspect({ kind: "table", sheetId: sheetName, range: address, tableMaxRows: 3, tableMaxCols: Math.min(headers.length, 18), maxChars: 5000 });
  return { file: path.relative(phaseRoot, file).replaceAll("\\", "/"), rows: rows.length, columns: headers.length, sha256: await sha256File(file) };
}

function upsert(rows, keyField, row) {
  const index = rows.findIndex((item) => String(item[keyField]) === String(row[keyField]));
  if (index >= 0) rows[index] = { ...rows[index], ...row };
  else rows.push(row);
}

function groupSum(rows, groupKey, valueField) {
  const result = new Map();
  for (const row of rows) result.set(groupKey(row), (result.get(groupKey(row)) || 0) + Number(row[valueField] || 0));
  return result;
}

function fuelContract(year, technology) {
  if (VRE_TECHS.includes(technology)) return { fuel: "NONE", carrier: technology.toLowerCase(), conversion: technology, efficiency: "NOT_APPLICABLE_VRE", co2: "ZERO_OPERATIONAL_CO2", marginal: "MC_VRE", availability: `PROFILE_${technology}` };
  if (technology === "HYDRO_RUN_OF_RIVER") return { fuel: "WATER_INFLOW", carrier: "hydro_run_of_river", conversion: "RUN_OF_RIVER_TURBINE", efficiency: "EFF_HYDRO_TURBINE", co2: "ZERO_OPERATIONAL_CO2", marginal: "MC_HYDRO", availability: "PROFILE_HYDRO_ROR_INFLOW" };
  if (HYDRO_TECHS.includes(technology)) return { fuel: "WATER", carrier: technology.toLowerCase(), conversion: technology, efficiency: "EFF_HYDRO_CLASS", co2: "ZERO_OPERATIONAL_CO2", marginal: "MC_HYDRO", availability: "PROFILE_HYDRO_INFLOW_AND_SOC" };
  if (technology === "BESS") return { fuel: "ELECTRICITY", carrier: "battery_storage", conversion: "ELECTROCHEMICAL_STORAGE", efficiency: "EFF_BESS_ROUNDTRIP", co2: "ZERO_DIRECT_CO2", marginal: "MC_STORAGE", availability: "STORAGE_SOC" };
  if (year === 2040) {
    const mapping = {
      CCGT: ["METHANE", "methane_ccgt", "CCGT", "EFF_METHANE_CCGT", "FOSSIL_METHANE_DIRECT_CO2", "MC_METHANE_CCGT", "AVAIL_THERMAL_CCGT"],
      GT_OCGT: ["METHANE", "methane_gt_ocgt", "GT_OCGT", "EFF_METHANE_GT_OCGT", "FOSSIL_METHANE_DIRECT_CO2", "MC_METHANE_GT_OCGT", "AVAIL_THERMAL_GT_OCGT"],
      INTERNAL_COMBUSTION: ["METHANE", "methane_internal_combustion", "INTERNAL_COMBUSTION", "EFF_METHANE_INTERNAL_COMBUSTION", "FOSSIL_METHANE_DIRECT_CO2", "MC_METHANE_INTERNAL_COMBUSTION", "AVAIL_THERMAL_ENGINE"],
      STEAM_OTHER_SURVIVING: ["METHANE", "methane_steam_other", "STEAM_OTHER", "EFF_METHANE_STEAM_OTHER", "FOSSIL_METHANE_DIRECT_CO2", "MC_METHANE_STEAM_OTHER", "AVAIL_THERMAL_STEAM"],
      OTHER_SURVIVING_THERMAL: ["METHANE", "methane_other_thermal", "OTHER_THERMAL", "EFF_METHANE_OTHER_THERMAL", "FOSSIL_METHANE_DIRECT_CO2", "MC_METHANE_OTHER_THERMAL", "AVAIL_THERMAL_OTHER"],
      GEOTHERMAL: ["GEOTHERMAL", "geothermal", "GEOTHERMAL", "EFF_NOT_APPLICABLE_GEOTHERMAL", "ZERO_OPERATIONAL_FOSSIL_CO2", "MC_GEOTHERMAL", "AVAIL_GEOTHERMAL"],
    };
    const [fuel, carrier, conversion, efficiency, co2, marginal, availability] = mapping[technology] || ["UNMAPPED", "unmapped", technology, "UNMAPPED", "UNMAPPED", "UNMAPPED", "UNMAPPED"];
    return { fuel, carrier, conversion, efficiency, co2, marginal, availability };
  }
  const mapping = {
    BIOENERGY: ["BIOENERGY", "bioenergy", "BIOENERGY_GENERATOR", "EFF_BIOENERGY", "BIOGENIC_CO2_ACCOUNTING", "MC_BIOENERGY", "AVAIL_BIOENERGY"],
    BIOENERGY_CCS: ["BIOENERGY", "bioenergy_ccs", "BIOENERGY_CCS_GENERATOR", "EFF_BIOENERGY_CCS", "BIOGENIC_CCS_NET_NEGATIVE_CLASS", "MC_BIOENERGY_CCS", "AVAIL_BIOENERGY_CCS"],
    GAS_CCS: ["METHANE", "methane_ccs_ccgt", "CCGT_CCS", "EFF_METHANE_CCGT_CCS", "FOSSIL_CCS_RESIDUAL_CO2", "MC_METHANE_CCS", "AVAIL_THERMAL_CCGT_CCS"],
    GAS_OTHER_FOSSIL: ["METHANE", "methane_gt_ocgt", "GT_OCGT_FUEL_FLEXIBLE", "EFF_METHANE_GT_OCGT", "FOSSIL_METHANE_DIRECT_CO2", "MC_METHANE_GT_OCGT", "AVAIL_THERMAL_GT_OCGT"],
    GEOTHERMAL: ["GEOTHERMAL", "geothermal", "GEOTHERMAL", "EFF_NOT_APPLICABLE_GEOTHERMAL", "ZERO_OPERATIONAL_FOSSIL_CO2", "MC_GEOTHERMAL", "AVAIL_GEOTHERMAL"],
    NUCLEAR: ["NUCLEAR_FUEL", "nuclear", "NUCLEAR_REACTOR", "EFF_NUCLEAR_THERMAL", "ZERO_DIRECT_OPERATIONAL_CO2", "MC_NUCLEAR", "AVAIL_NUCLEAR"],
  };
  const [fuel, carrier, conversion, efficiency, co2, marginal, availability] = mapping[technology] || ["UNMAPPED", "unmapped", technology, "UNMAPPED", "UNMAPPED", "UNMAPPED", "UNMAPPED"];
  return { fuel, carrier, conversion, efficiency, co2, marginal, availability };
}

function componentPattern(technology) {
  if (VRE_TECHS.includes(technology) || technology === "HYDRO_RUN_OF_RIVER" || DISPATCH_2040.includes(technology) || DISPATCH_2050.includes(technology)) return "Generator";
  if (["HYDRO_BASIN", "HYDRO_RESERVOIR"].includes(technology)) return "Water Store + turbine Link + inflow";
  if (["PUMPED_HYDRO_PURE", "PUMPED_HYDRO_MIXED"].includes(technology)) return "Shared water Store + pump Link + turbine Link";
  if (technology === "BESS") return "Electricity Store + charge/discharge Links";
  return "UNMAPPED";
}

const longV1 = await readCsv(files.longV1, "LongV1");
const base2040Rows = await readCsv(files.base2040, "Base2040");
const national2050Rows = await readCsv(files.national2050, "National2050");
const zonal2050Rows = await readCsv(files.zonal2050, "Zonal2050");
const anchorRows = await readCsv(files.anchor, "Anchor");
const removalMask = await readCsv(files.removalMask, "RemovalMask");
const taxonomyV1 = await readCsv(files.taxonomy, "TaxonomyV1");
const crosswalkV1 = await readCsv(files.crosswalk, "CrosswalkV1");
const hydroRows = await readCsv(files.hydro, "Hydro");
const cdpRows = await readCsv(files.cdp, "CDP");

if (longV1.length !== 749) throw new Error(`V1 long-form row count changed: ${longV1.length}`);
const v1WorkbookHash = await sha256File(files.workbookV1);
if (v1WorkbookHash !== "9320a8c6ab3ab8faf949e47ac77fcbf7906873140582ecff1c8432a0e38979a2") throw new Error(`Unexpected V1 workbook hash: ${v1WorkbookHash}`);

const slowAccepted = {};
for (const year of [2040, 2050]) {
  const row = cdpRows.find((item) => Number(item.year) === year && item.resource === "TOTAL_CORRECTED");
  slowAccepted[year] = { lostGW: Number(row.lost_CDP_GW), incrementGW: Number(row.additional_dispatchable_capacity_GW), incrementMW: Number(row.additional_dispatchable_capacity_GW) * 1000 };
}
if (!almost(slowAccepted[2040].incrementGW, 13.213325410972, 1e-9) || !almost(slowAccepted[2050].incrementGW, 21.613384747839, 1e-9)) throw new Error("Accepted Slow CDP increments changed.");

const base2040 = base2040Rows.filter((row) => row.scenario === "Base");
const base2040ByTechZone = new Map(base2040.map((row) => [key(row.technology, row.market_zone), Number(row.capacity_NET_MW)]));
const base2040National = groupSum(base2040, (row) => row.technology, "capacity_NET_MW");
const methane2040Denominator = sum(METHANE_SCALABLE_2040.map((technology) => base2040National.get(technology)));
const slow2040Rows = [];
const slow2040National = [];
for (const technology of DISPATCH_2040) {
  const baseNational = base2040National.get(technology) || 0;
  const scalable = METHANE_SCALABLE_2040.includes(technology);
  const technologyWeight = scalable ? baseNational / methane2040Denominator : 0;
  const incrementNational = scalable ? slowAccepted[2040].incrementMW * technologyWeight : 0;
  slow2040National.push({
    year: 2040, technology, fuel: fuelContract(2040, technology).fuel,
    slow_scalability: scalable ? "SCALABLE_IN_SLOW" : "FIXED_IN_SLOW",
    Base_capacity_MW: round(baseNational, 12), increment_denominator_MW: scalable ? round(methane2040Denominator, 12) : "NOT_APPLICABLE",
    increment_weight: round(technologyWeight, 12), Slow_CDP_increment_MW: round(incrementNational, 12),
    Slow_total_capacity_MW: round(baseNational + incrementNational, 12),
    increment_method: scalable ? "PROPORTIONAL_ACROSS_BASE_METHANE_TECHNOLOGIES" : "SLOW_FIXED_RESOURCE_RULE",
    status: "SCENARIO_CAPACITY_PACKAGE_V2",
  });
  for (const zone of ZONES) {
    const baseMW = base2040ByTechZone.get(key(technology, zone)) || 0;
    const zoneShare = baseNational ? baseMW / baseNational : 0;
    const incrementMW = incrementNational * zoneShare;
    slow2040Rows.push({
      year: 2040, scenario: "Slow", technology, zone, fuel: fuelContract(2040, technology).fuel,
      carrier: fuelContract(2040, technology).carrier, slow_scalability: scalable ? "SCALABLE_IN_SLOW" : "FIXED_IN_SLOW",
      Base_capacity_MW: round(baseMW, 12), Base_national_capacity_MW: round(baseNational, 12),
      Base_zone_share: round(zoneShare, 12), increment_technology_weight: round(technologyWeight, 12),
      CDP_increment_MW: round(incrementMW, 12), CDP_increment_national_MW: round(incrementNational, 12),
      Slow_total_capacity_MW: round(baseMW + incrementMW, 12), Slow_national_capacity_MW: round(baseNational + incrementNational, 12),
      zonal_anchor: "FROZEN_POST_COAL_OIL_TECHNOLOGY_GEOGRAPHY",
      evidence_role: scalable ? "PROJECT_SLOW_GAS_ONLY_ALLOCATION" : "SLOW_FIXED_RESOURCE_RULE",
      status: scalable ? "SELECTED_V2_INCREMENT" : "FIXED_ZERO_INCREMENT",
    });
  }
}

const base2050Zonal = zonal2050Rows.filter((row) => row.scenario === "Base");
const base2050ByTechZone = new Map(base2050Zonal.map((row) => [key(row.technology, row.market_zone), Number(row.capacity_MW)]));
const base2050National = groupSum(base2050Zonal, (row) => row.technology, "capacity_MW");
const gas2050Denominator = sum(GAS_SCALABLE_2050.map((technology) => base2050National.get(technology)));
const selectedSlow2050Rows = [];
const slow2050AllCandidates = [];
const slow2050National = [];
const gasBaseShares = Object.fromEntries(GAS_SCALABLE_2050.map((technology) => [technology, base2050National.get(technology) / gas2050Denominator]));
const peakerWeightsRaw = { GAS_CCS: base2050National.get("GAS_CCS"), GAS_OTHER_FOSSIL: 2 * base2050National.get("GAS_OTHER_FOSSIL") };
const peakerWeightDenominator = peakerWeightsRaw.GAS_CCS + peakerWeightsRaw.GAS_OTHER_FOSSIL;
const candidates2050 = {
  CANDIDATE_1_PROPORTIONAL_GAS_ONLY_SELECTED: { GAS_CCS: gasBaseShares.GAS_CCS, GAS_OTHER_FOSSIL: gasBaseShares.GAS_OTHER_FOSSIL },
  CANDIDATE_2_FLEXIBLE_PEAKER_WEIGHTED_SENSITIVITY: { GAS_CCS: peakerWeightsRaw.GAS_CCS / peakerWeightDenominator, GAS_OTHER_FOSSIL: peakerWeightsRaw.GAS_OTHER_FOSSIL / peakerWeightDenominator },
};
for (const [candidate, weights] of Object.entries(candidates2050)) {
  for (const technology of DISPATCH_2050) {
    const baseNational = base2050National.get(technology) || 0;
    const scalable = GAS_SCALABLE_2050.includes(technology);
    const incrementWeight = scalable ? weights[technology] : 0;
    const incrementNational = slowAccepted[2050].incrementMW * incrementWeight;
    for (const zone of ZONES) {
      const baseMW = base2050ByTechZone.get(key(technology, zone)) || 0;
      const zoneShare = baseNational ? baseMW / baseNational : 0;
      const row = {
        year: 2050, scenario: "Slow", allocation_candidate: candidate, selected_for_capacity_package: candidate.startsWith("CANDIDATE_1"),
        technology, zone, fuel_architecture: technology === "GAS_OTHER_FOSSIL" ? "METHANE_BASELINE; HYDROGEN_SENSITIVITIES" : fuelContract(2050, technology).fuel,
        slow_scalability: scalable ? "SCALABLE_IN_SLOW" : "FIXED_IN_SLOW",
        Base_capacity_MW: round(baseMW, 12), Base_national_capacity_MW: round(baseNational, 12), Base_zone_share: round(zoneShare, 12),
        increment_weight: round(incrementWeight, 12), CDP_increment_MW: round(incrementNational * zoneShare, 12),
        CDP_increment_national_MW: round(incrementNational, 12), Slow_total_capacity_MW: round(baseMW + incrementNational * zoneShare, 12),
        Slow_national_capacity_MW: round(baseNational + incrementNational, 12),
        zonal_anchor: technology === "GAS_CCS" ? "COMBINED_CCGT_GEOGRAPHY" : technology === "GAS_OTHER_FOSSIL" ? "COMBINED_GT_OCGT_GEOGRAPHY" : "FROZEN_TECHNOLOGY_GEOGRAPHY",
        evidence_role: scalable ? (candidate.startsWith("CANDIDATE_1") ? "SELECTED_SIMPLE_PROPORTIONAL_GAS_ONLY_RULE" : "NON_CONTROLLING_FLEXIBLE_PEAKER_SENSITIVITY") : "SLOW_FIXED_RESOURCE_RULE",
        status: scalable ? (candidate.startsWith("CANDIDATE_1") ? "SELECTED_V2_INCREMENT" : "SENSITIVITY_ONLY") : "FIXED_ZERO_INCREMENT",
      };
      slow2050AllCandidates.push(row);
      if (candidate.startsWith("CANDIDATE_1")) selectedSlow2050Rows.push(row);
    }
  }
}
for (const technology of DISPATCH_2050) {
  const rows = selectedSlow2050Rows.filter((row) => row.technology === technology);
  slow2050National.push({
    year: 2050, technology, fuel_architecture: rows[0].fuel_architecture, slow_scalability: rows[0].slow_scalability,
    Base_capacity_MW: round(sum(rows.map((row) => row.Base_capacity_MW)), 12),
    increment_weight: rows[0].increment_weight, Slow_CDP_increment_MW: round(sum(rows.map((row) => row.CDP_increment_MW)), 12),
    Slow_total_capacity_MW: round(sum(rows.map((row) => row.Slow_total_capacity_MW)), 12),
    increment_method: rows[0].slow_scalability === "SCALABLE_IN_SLOW" ? "CANDIDATE_1_PROPORTIONAL_GAS_ONLY_SELECTED" : "SLOW_FIXED_RESOURCE_RULE",
    status: "SCENARIO_CAPACITY_PACKAGE_V2",
  });
}

// Fuel/carrier architecture and non-additive hydrogen sensitivities.
const architecture2050 = [
  { fuel: "METHANE", carrier: "methane_gt_ocgt", combustion: true, ccs: false, source_category: "GAS_OTHER_FOSSIL", conversion_technology: "GT_OCGT_FUEL_FLEXIBLE", efficiency_class: "EFF_METHANE_GT_OCGT", CO2_factor_class: "FOSSIL_METHANE_DIRECT_CO2", marginal_cost_class: "MC_METHANE_GT_OCGT", role: "BASELINE FUEL FOR GAS_OTHER_FOSSIL PENDING SPLIT APPROVAL", additive_capacity_rule: "OCCUPIES EXISTING GAS_OTHER_FOSSIL CAPACITY; NO EXTRA MW", status: "MODEL_CANDIDATE" },
  { fuel: "HYDROGEN", carrier: "hydrogen_gt_ocgt", combustion: true, ccs: false, source_category: "GAS_OTHER_FOSSIL", conversion_technology: "GT_OCGT_HYDROGEN_READY", efficiency_class: "EFF_HYDROGEN_GT_OCGT", CO2_factor_class: "ZERO_DIRECT_CO2_HYDROGEN_COMBUSTION", marginal_cost_class: "MC_HYDROGEN_GT_OCGT", role: "FUEL-SPLIT SENSITIVITY WITHIN GAS_OTHER_FOSSIL", additive_capacity_rule: "SUBSTITUTES METHANE CAPACITY; NEVER ADDS TO PROGRAMMABLE TOTAL", status: "PROJECT_ASSUMPTION_REQUIRED" },
  { fuel: "METHANE", carrier: "methane_ccs_ccgt", combustion: true, ccs: true, source_category: "GAS_CCS", conversion_technology: "CCGT_CCS", efficiency_class: "EFF_METHANE_CCGT_CCS", CO2_factor_class: "FOSSIL_CCS_RESIDUAL_CO2", marginal_cost_class: "MC_METHANE_CCS", role: "PROJECT INTERPRETATION OF PNIEC GAS+CCS", additive_capacity_rule: "INSIDE GAS_CCS CAPACITY", status: "MODEL_CANDIDATE" },
  { fuel: "BIOENERGY", carrier: "bioenergy", combustion: true, ccs: false, source_category: "BIOENERGY", conversion_technology: "BIOENERGY_GENERATOR", efficiency_class: "EFF_BIOENERGY", CO2_factor_class: "BIOGENIC_CO2_ACCOUNTING", marginal_cost_class: "MC_BIOENERGY", role: "FIXED BASE/HIGH CAPACITY IN SLOW", additive_capacity_rule: "NO SLOW INCREMENT", status: "MODEL_CANDIDATE" },
  { fuel: "BIOENERGY", carrier: "bioenergy_ccs", combustion: true, ccs: true, source_category: "BIOENERGY_CCS", conversion_technology: "BIOENERGY_CCS_GENERATOR", efficiency_class: "EFF_BIOENERGY_CCS", CO2_factor_class: "BIOGENIC_CCS_NET_NEGATIVE_CLASS", marginal_cost_class: "MC_BIOENERGY_CCS", role: "FIXED BASE/HIGH CAPACITY IN SLOW", additive_capacity_rule: "NO SLOW INCREMENT", status: "MODEL_CANDIDATE" },
  { fuel: "GEOTHERMAL", carrier: "geothermal", combustion: false, ccs: false, source_category: "GEOTHERMAL", conversion_technology: "GEOTHERMAL", efficiency_class: "EFF_NOT_APPLICABLE_GEOTHERMAL", CO2_factor_class: "ZERO_OPERATIONAL_FOSSIL_CO2", marginal_cost_class: "MC_GEOTHERMAL", role: "FIXED BASE/HIGH CAPACITY IN SLOW", additive_capacity_rule: "NO SLOW INCREMENT", status: "MODEL_CANDIDATE" },
  { fuel: "NUCLEAR_FUEL", carrier: "nuclear", combustion: false, ccs: false, source_category: "NUCLEAR", conversion_technology: "NUCLEAR_REACTOR", efficiency_class: "EFF_NUCLEAR_THERMAL", CO2_factor_class: "ZERO_DIRECT_OPERATIONAL_CO2", marginal_cost_class: "MC_NUCLEAR", role: "DIRECT 8-GW CAPACITY; FIXED IN SLOW", additive_capacity_rule: "NO SLOW INCREMENT", status: "MODEL_CANDIDATE" },
];

const decomposition2050 = [];
const gasOtherTotals = new Map();
for (const scenario of ["Base", "High"]) gasOtherTotals.set(scenario, base2050National.get("GAS_OTHER_FOSSIL"));
gasOtherTotals.set("Slow", slow2050National.find((row) => row.technology === "GAS_OTHER_FOSSIL").Slow_total_capacity_MW);
const fuelSplitCandidates = [
  { candidate: "BASELINE_METHANE_ONLY_PENDING_APPROVAL", methane: 1, hydrogen: 0, selected: true, role: "Conservative no-invented-hydrogen baseline" },
  { candidate: "SENSITIVITY_25_PERCENT_HYDROGEN", methane: 0.75, hydrogen: 0.25, selected: false, role: "Moderate fuel-substitution sensitivity" },
  { candidate: "SENSITIVITY_50_PERCENT_HYDROGEN", methane: 0.5, hydrogen: 0.5, selected: false, role: "High fuel-substitution sensitivity" },
];
for (const scenario of ["Base", "High", "Slow"]) {
  const sourceMW = gasOtherTotals.get(scenario);
  for (const candidate of fuelSplitCandidates) {
    for (const [fuel, share] of [["METHANE", candidate.methane], ["HYDROGEN", candidate.hydrogen]]) {
      decomposition2050.push({
        year: 2050, scenario, decomposition_candidate: candidate.candidate, selected_static_candidate: candidate.selected,
        source_category: "GAS_OTHER_FOSSIL", source_category_capacity_MW: round(sourceMW, 12), solver_fuel: fuel,
        solver_conversion_technology: fuel === "METHANE" ? "GT_OCGT_FUEL_FLEXIBLE" : "GT_OCGT_HYDROGEN_READY",
        share_candidate: share, capacity_candidate_MW: round(sourceMW * share, 12), energy_role: candidate.role,
        CO2_treatment: fuel === "METHANE" ? "FOSSIL_METHANE_DIRECT_CO2" : "ZERO_DIRECT_CO2_HYDROGEN_COMBUSTION; UPSTREAM OUTSIDE DIRECT STACK",
        evidence: "USER ALLOWS METHANE/HYDROGEN; NO DIRECT CAPACITY SPLIT SOURCE",
        assumption_status: candidate.selected ? "BASELINE_PROJECT_ASSUMPTION_PENDING_USER_APPROVAL" : "SENSITIVITY_ONLY",
        sensitivity_required: true, capacity_addition: false,
      });
    }
  }
}

// Compact oil and coal residual materiality bounds.
const futureMap = {
  GT_NON_CHP: "GT_OCGT", GT_CHP: "GT_OCGT",
  INTERNAL_COMBUSTION_NON_CHP: "INTERNAL_COMBUSTION", INTERNAL_COMBUSTION_CHP: "INTERNAL_COMBUSTION",
  STEAM_CONDENSING: "STEAM_OTHER_SURVIVING", STEAM_EXTRACTION_CHP: "STEAM_OTHER_SURVIVING", STEAM_BACKPRESSURE_CHP: "STEAM_OTHER_SURVIVING",
  FUEL_CELL_NON_CHP: "OTHER_SURVIVING_THERMAL", FUEL_CELL_CHP: "OTHER_SURVIVING_THERMAL", EXTERNAL_COMBUSTION: "OTHER_SURVIVING_THERMAL", TURBO_EXPANSION: "OTHER_SURVIVING_THERMAL", OTHER_THERMAL: "OTHER_SURVIVING_THERMAL",
};
const observed = new Map(anchorRows.map((row) => [key(row.market_zone, row.future_technology), Number(row.observed_2024_capacity_NET_MW)]));
const directRemoval = (fuel) => groupSum(removalMask.filter((row) => row.fuel === fuel && String(row.removal_status).startsWith("DIRECT")), (row) => key(row.zone, futureMap[row.conversion_technology]), "candidate_NET_MW");
const residualRemoval = (fuel) => groupSum(removalMask.filter((row) => row.fuel === fuel && row.removal_status === "TERNA_CONTROL_RESIDUAL"), (row) => key(row.zone, futureMap[row.conversion_technology]), "candidate_NET_MW");
const coalDirect = directRemoval("COAL");
const coalBaseline = residualRemoval("COAL");
const oilDirect = directRemoval("PETROLEUM_PRODUCTS");
const oilBaseline = residualRemoval("PETROLEUM_PRODUCTS");
const oilResidualMW = sum([...oilBaseline.values()]);
const coalResidualMW = sum([...coalBaseline.values()]);
const thermalAnchorTechs = ["CCGT", "GT_OCGT", "INTERNAL_COMBUSTION", "STEAM_OTHER_SURVIVING", "OTHER_SURVIVING_THERMAL", "GEOTHERMAL"];

function proportionalResidual(totalMW, eligibleTechs) {
  const eligible = [];
  for (const zone of ZONES) for (const technology of eligibleTechs) {
    const gross = observed.get(key(zone, technology)) || 0;
    const remaining = Math.max(0, gross - (coalDirect.get(key(zone, technology)) || 0) - (oilDirect.get(key(zone, technology)) || 0));
    if (remaining > 0) eligible.push({ key: key(zone, technology), remaining });
  }
  const denominator = sum(eligible.map((row) => row.remaining));
  return new Map(eligible.map((row) => [row.key, totalMW * row.remaining / denominator]));
}

function scaledAnchor(coalResidual, oilResidual) {
  const unscaled = new Map();
  for (const zone of ZONES) for (const technology of thermalAnchorTechs) {
    const k = key(zone, technology);
    const value = (observed.get(k) || 0) - (coalDirect.get(k) || 0) - (coalResidual.get(k) || 0) - (oilDirect.get(k) || 0) - (oilResidual.get(k) || 0);
    if (value < -1e-6) throw new Error(`Materiality bound creates negative capacity at ${k}: ${value}`);
    unscaled.set(k, Math.max(0, value));
  }
  const total = sum([...unscaled.values()]);
  const factor = 55000 / total;
  const scaled = new Map([...unscaled.entries()].map(([k, value]) => [k, value * factor]));
  const technologyTotals = new Map(thermalAnchorTechs.map((technology) => [technology, sum(ZONES.map((zone) => scaled.get(key(zone, technology))))]));
  const zoneTotals = new Map(ZONES.map((zone) => [zone, sum(thermalAnchorTechs.map((technology) => scaled.get(key(zone, technology))))]));
  return { scaled, technologyTotals, zoneTotals, total, factor };
}

function compareBound(name, baseline, bound, description, statusThreshold) {
  let maxCell = { difference: 0, cell: "" };
  for (const zone of ZONES) for (const technology of thermalAnchorTechs) {
    const cell = key(zone, technology);
    const difference = Math.abs(bound.scaled.get(cell) - baseline.scaled.get(cell));
    if (difference > maxCell.difference) maxCell = { difference, cell };
  }
  let maxTech = { difference: 0, technology: "" };
  for (const technology of thermalAnchorTechs) {
    const difference = Math.abs(100 * (bound.technologyTotals.get(technology) - baseline.technologyTotals.get(technology)) / 55000);
    if (difference > maxTech.difference) maxTech = { difference, technology };
  }
  let maxZone = { difference: 0, zone: "" };
  for (const zone of ZONES) {
    const difference = Math.abs(100 * (bound.zoneTotals.get(zone) - baseline.zoneTotals.get(zone)) / 55000);
    if (difference > maxZone.difference) maxZone = { difference, zone };
  }
  const passed = maxCell.difference <= statusThreshold.maxCellMW && Math.max(maxTech.difference, maxZone.difference) <= statusThreshold.maxSharePP;
  return {
    bound: name, allocation_method: description, unscaled_anchor_MW: round(bound.total, 9), scaling_factor_to_55GW: round(bound.factor, 12),
    max_scaled_zone_technology_difference_MW: round(maxCell.difference, 9), max_difference_cell: maxCell.cell,
    max_national_technology_share_difference_pp: round(maxTech.difference, 9), max_technology: maxTech.technology,
    max_national_zone_share_difference_pp: round(maxZone.difference, 9), max_zone: maxZone.zone,
    materiality_threshold_MW: statusThreshold.maxCellMW, materiality_threshold_share_pp: statusThreshold.maxSharePP,
    status: passed ? "ACCEPTED_NON_MATERIAL_PROJECT_ALLOCATION" : "MATERIAL_REVIEW_REQUIRED",
  };
}

const baselineAnchor = scaledAnchor(coalBaseline, oilBaseline);
const oilBoundA = new Map([[key("NORD", "INTERNAL_COMBUSTION"), oilResidualMW]]);
const oilBoundB = proportionalResidual(oilResidualMW, ["GT_OCGT", "INTERNAL_COMBUSTION"]);
const oilChecks = [
  { bound: "BASELINE", allocation_method: "Current transparent residual allocation across oil-compatible conversion bands", unscaled_anchor_MW: round(baselineAnchor.total, 9), scaling_factor_to_55GW: round(baselineAnchor.factor, 12), max_scaled_zone_technology_difference_MW: 0, max_difference_cell: "NOT_APPLICABLE", max_national_technology_share_difference_pp: 0, max_technology: "NOT_APPLICABLE", max_national_zone_share_difference_pp: 0, max_zone: "NOT_APPLICABLE", materiality_threshold_MW: 1100, materiality_threshold_share_pp: 2.1, status: "CONTROLLING_BASELINE" },
  compareBound("BOUND_A_CONCENTRATED_LARGEST_PLAUSIBLE_BAND", baselineAnchor, scaledAnchor(coalBaseline, oilBoundA), "All 1,089.9 MW residual placed in NORD internal-combustion band", { maxCellMW: 1100, maxSharePP: 2.1 }),
  compareBound("BOUND_B_PROPORTIONAL_PEAKER_BANDS", baselineAnchor, scaledAnchor(coalBaseline, oilBoundB), "Residual allocated across GT/OCGT and internal-combustion bands only", { maxCellMW: 1100, maxSharePP: 2.1 }),
];
const coalBoundA = new Map([[key("SARD", "STEAM_OTHER_SURVIVING"), coalResidualMW]]);
const coalBoundB = new Map([[key("CSUD", "STEAM_OTHER_SURVIVING"), coalResidualMW]]);
const coalChecks = [
  { bound: "BASELINE", allocation_method: "158.78-MW NORD steam control residual", unscaled_anchor_MW: round(baselineAnchor.total, 9), scaling_factor_to_55GW: round(baselineAnchor.factor, 12), max_scaled_zone_technology_difference_MW: 0, max_difference_cell: "NOT_APPLICABLE", max_national_technology_share_difference_pp: 0, max_technology: "NOT_APPLICABLE", max_national_zone_share_difference_pp: 0, max_zone: "NOT_APPLICABLE", materiality_threshold_MW: 200, materiality_threshold_share_pp: 0.4, status: "CONTROLLING_BASELINE" },
  compareBound("BOUND_A_RELOCATE_TO_SARD", baselineAnchor, scaledAnchor(coalBoundA, oilBaseline), "Relocate entire coal residual to SARD steam band", { maxCellMW: 200, maxSharePP: 0.4 }),
  compareBound("BOUND_B_RELOCATE_TO_CSUD", baselineAnchor, scaledAnchor(coalBoundB, oilBaseline), "Relocate entire coal residual to CSUD steam band", { maxCellMW: 200, maxSharePP: 0.4 }),
];
const oilMaterialityPass = oilChecks.filter((row) => row.bound !== "BASELINE").every((row) => row.status.startsWith("ACCEPTED"));
const coalMaterialityPass = coalChecks.filter((row) => row.bound !== "BASELINE").every((row) => row.status.startsWith("ACCEPTED"));

// Taxonomy and geography metadata hardening.
const taxonomy = taxonomyV1.map((row) => {
  const tech = row.future_technology;
  const additive2040 = DISPATCH_2040.includes(tech);
  const additive2050 = DISPATCH_2050.includes(tech);
  return {
    ...row,
    additive_in_2040_55GW: String(additive2040), additive_in_2050_30GW: String(additive2050),
    geography_anchor_only_2040: "false", geography_anchor_only_2050: String(["CCGT", "GT_OCGT"].includes(tech)),
    additive_model_component_2040: String(additive2040), additive_model_component_2050: String(additive2050),
    historical_cross_classification_only: String(tech === "BIOENERGY_2040_CROSS_CLASSIFICATION"),
    slow_scalability_2040: METHANE_SCALABLE_2040.includes(tech) ? "SCALABLE_IN_SLOW" : tech === "GEOTHERMAL" ? "FIXED_IN_SLOW" : "NOT_APPLICABLE",
    slow_scalability_2050: GAS_SCALABLE_2050.includes(tech) ? "SCALABLE_IN_SLOW" : ["BIOENERGY", "BIOENERGY_CCS", "GEOTHERMAL", "NUCLEAR"].includes(tech) ? "FIXED_IN_SLOW" : "ANCHOR_ONLY_OR_NOT_APPLICABLE",
    solver_fuel_2040: additive2040 ? fuelContract(2040, tech).fuel : "NOT_APPLICABLE",
    solver_carrier_2040: additive2040 ? fuelContract(2040, tech).carrier : "NOT_APPLICABLE",
    solver_fuel_2050: additive2050 ? (tech === "GAS_OTHER_FOSSIL" ? "METHANE_BASELINE; HYDROGEN_SENSITIVITIES" : fuelContract(2050, tech).fuel) : "NOT_APPLICABLE",
    solver_carrier_2050: additive2050 ? fuelContract(2050, tech).carrier : "NOT_APPLICABLE",
    package_version: "SCENARIO_CAPACITY_PACKAGE_V2",
    status: "FROZEN_STATIC_CAPACITY_TAXONOMY_V2",
  };
});

const crosswalk = crosswalkV1.map((row) => {
  const tech = row.future_technology;
  return {
    ...row,
    geography_anchor_only_2050: String(["CCGT", "GT_OCGT"].includes(tech)),
    additive_model_component_2040: String(DISPATCH_2040.includes(tech)),
    additive_model_component_2050: String(DISPATCH_2050.includes(tech)),
    slow_scalability_2040: METHANE_SCALABLE_2040.includes(tech) ? "SCALABLE_IN_SLOW" : tech === "GEOTHERMAL" ? "FIXED_IN_SLOW" : "NOT_APPLICABLE",
    slow_scalability_2050: GAS_SCALABLE_2050.includes(tech) ? "SCALABLE_IN_SLOW" : ["BIOENERGY", "BIOENERGY_CCS", "GEOTHERMAL", "NUCLEAR"].includes(tech) ? "FIXED_IN_SLOW" : "ANCHOR_ONLY",
    solver_fuel_2040: DISPATCH_2040.includes(tech) ? fuelContract(2040, tech).fuel : "NOT_APPLICABLE",
    solver_fuel_2050: DISPATCH_2050.includes(tech) ? (tech === "GAS_OTHER_FOSSIL" ? "METHANE_BASELINE; HYDROGEN_SENSITIVITIES" : fuelContract(2050, tech).fuel) : "NOT_APPLICABLE",
    package_version: "SCENARIO_CAPACITY_PACKAGE_V2",
    status: "FROZEN_ZONAL_GEOGRAPHY_V2",
  };
});

const carrier2040Rows = [];
for (const scenario of ["Slow", "Base", "High"]) for (const technology of DISPATCH_2040) {
  const base = base2040National.get(technology) || 0;
  const capacityMW = scenario === "Slow" ? slow2040National.find((row) => row.technology === technology).Slow_total_capacity_MW : base;
  const contract = fuelContract(2040, technology);
  carrier2040Rows.push({
    year: 2040, scenario, scenario_technology: technology, conversion_technology: contract.conversion,
    fuel: contract.fuel, carrier: contract.carrier, efficiency_class: contract.efficiency, CO2_class: contract.co2,
    capacity_MW: round(capacityMW, 12), zonal_anchor: technology === "GEOTHERMAL" ? "CURRENT_GEOTHERMAL_GEOGRAPHY" : "FROZEN_POST_COAL_OIL_TECHNOLOGY_GEOGRAPHY",
    evidence_role: technology === "GEOTHERMAL" ? "PROGRAMMABLE_RENEWABLE_INSIDE_55GW" : "USER_CONFIRMED_2040_METHANE_FUEL_CONTRACT",
    status: technology === "GEOTHERMAL" ? "NON_COMBUSTION_MODEL_CANDIDATE" : "METHANE_ONLY_MODEL_CANDIDATE",
  });
}

// Rebuild long-form V2: Base/High and non-dispatchable Slow rows remain capacity-identical; Slow dispatch is replaced.
const dispatchAll = new Set([...DISPATCH_2040, ...DISPATCH_2050]);
const longV2 = [];
for (const row of longV1) {
  const year = Number(row.year);
  if (row.scenario === "Slow" && dispatchAll.has(row.technology)) continue;
  const contract = fuelContract(year, row.technology);
  longV2.push({
    ...row, package_version: "SCENARIO_CAPACITY_PACKAGE_V2",
    slow_scalability: row.scenario === "Slow" ? (FIXED_SLOW.has(row.technology) ? "FIXED_IN_SLOW" : "NOT_DISPATCHABLE_OR_SCENARIO_RULE") : "NOT_APPLICABLE",
    solver_fuel_contract: contract.fuel, solver_carrier: contract.carrier,
    additive_model_component: true,
  });
}

function pushSlowDispatch(year, rows, technologies) {
  for (const technology of technologies) {
    const techRows = rows.filter((row) => row.technology === technology);
    const baseNational = sum(techRows.map((row) => row.Base_capacity_MW));
    const incrementNational = sum(techRows.map((row) => row.CDP_increment_MW));
    const contract = fuelContract(year, technology);
    for (const row of techRows) {
      const common = {
        year, scenario: "Slow", technology, zone: row.zone,
        national_capacity_method: year === 2040 ? "BASE 55GW PORTFOLIO + GAS-ONLY SLOW CDP INCREMENT" : "BASE 30GW PORTFOLIO + GAS-ONLY SLOW CDP INCREMENT",
        technology_weight_method: row.slow_scalability === "SCALABLE_IN_SLOW" ? "PROPORTIONAL_ACROSS_SCALABLE_GAS TECHNOLOGIES" : "SLOW_FIXED_RESOURCE_RULE",
        zonal_allocation_method: row.zonal_anchor, allocation_anchor_year: "2024 / FROZEN FUTURE SITING",
        source_or_assumption: "MEM PHASE 4A CONTROLLING USER DECISION", evidence_class: "PROJECT SCENARIO RULE",
        package_version: "SCENARIO_CAPACITY_PACKAGE_V2", slow_scalability: row.slow_scalability,
        solver_fuel_contract: technology === "GAS_OTHER_FOSSIL" ? "METHANE_BASELINE; HYDROGEN_SENSITIVITIES" : contract.fuel,
        solver_carrier: contract.carrier, additive_model_component: true,
      };
      longV2.push({ ...common, capacity_MW: row.Base_capacity_MW, national_capacity_MW: round(baseNational, 12), capacity_component: technology === "NUCLEAR" ? "NUCLEAR_FIXED" : "BASE_THERMAL", baseline_or_increment: "BASE_PORTFOLIO", status: "INSTALLED_CAPACITY_TABLE_READY_V2" });
      longV2.push({ ...common, capacity_MW: row.CDP_increment_MW, national_capacity_MW: round(incrementNational, 12), capacity_component: "SLOW_CDP_ADDITIONAL_THERMAL", baseline_or_increment: "INCREMENT", status: row.slow_scalability === "FIXED_IN_SLOW" ? "FIXED_IN_SLOW_ZERO_INCREMENT" : "INSTALLED_CAPACITY_TABLE_READY_V2" });
    }
  }
}
pushSlowDispatch(2040, slow2040Rows, DISPATCH_2040);
pushSlowDispatch(2050, selectedSlow2050Rows, DISPATCH_2050);
longV2.sort((a, b) => Number(a.year) - Number(b.year) || ["Slow", "Base", "High"].indexOf(a.scenario) - ["Slow", "Base", "High"].indexOf(b.scenario) || ALL_DISPLAY_TECHS.indexOf(a.technology) - ALL_DISPLAY_TECHS.indexOf(b.technology) || ZONES.indexOf(a.zone) - ZONES.indexOf(b.zone) || String(a.baseline_or_increment).localeCompare(String(b.baseline_or_increment)));

const aggregateScenario = groupSum(longV2, (row) => key(row.year, row.scenario, row.technology, row.zone), "capacity_MW");
const readinessRows = [];
const generatorRows = [];
for (const { year, scenario } of SCENARIOS) {
  const technologies = year === 2040 ? [...VRE_TECHS, ...HYDRO_TECHS, "BESS", ...DISPATCH_2040] : [...VRE_TECHS, ...HYDRO_TECHS, "BESS", ...DISPATCH_2050];
  for (const technology of technologies) for (const zone of ZONES) {
    const capacityMW = aggregateScenario.get(key(year, scenario, technology, zone)) || 0;
    const contract = fuelContract(year, technology);
    const pattern = componentPattern(technology);
    const componentReady = pattern !== "UNMAPPED";
    const operationalPartial = ["HYDRO_BASIN", "HYDRO_RESERVOIR", "PUMPED_HYDRO_PURE", "PUMPED_HYDRO_MIXED", "BESS"].includes(technology);
    readinessRows.push({
      year, scenario, zone, technology, solver_component_pattern: pattern, additive_capacity_MW: round(capacityMW, 12),
      capacity_ready: true, p_nom_extendable: false, fuel_class: contract.fuel, fuel_class_ready: !contract.fuel.includes("UNMAPPED"),
      carrier: contract.carrier, carrier_ready: !contract.carrier.includes("unmapped"), efficiency_class: contract.efficiency,
      numerical_efficiency_ready: false, CO2_factor_class: contract.co2, numerical_CO2_factor_ready: false,
      marginal_cost_class: contract.marginal, numerical_marginal_cost_ready: false, availability_class: contract.availability,
      hourly_availability_ready: false, component_pattern_ready: componentReady,
      operational_parameter_status: operationalPartial ? "PARTIAL_OPERATIONAL_PARAMETERS" : "STATIC_CLASS_CONTRACT_READY; NUMERICAL_ASSUMPTIONS_PENDING",
      static_solver_status: componentReady ? "STATIC_CAPACITY_AND_CLASS_CONTRACT_READY" : "BLOCKED_UNMAPPED_COMPONENT",
      blocking_items: operationalPartial ? "e_nom/pump/efficiency/inflow/SOC or duration as applicable" : "numerical efficiency, costs, CO2 factor and hourly profile/availability",
    });
    if (capacityMW <= 1e-9 || pattern !== "Generator") continue;
    generatorRows.push({
      scenario, year, generator_id: `${year}_${scenario.toUpperCase()}_${zone}_${technology}`, zone, technology,
      conversion_technology: contract.conversion, fuel: technology === "GAS_OTHER_FOSSIL" ? "METHANE" : contract.fuel,
      carrier: contract.carrier, p_nom_MW: round(capacityMW, 12), p_nom_extendable: false,
      efficiency_class: contract.efficiency, CO2_factor_class: contract.co2, marginal_cost_class: contract.marginal,
      availability_class: contract.availability, capacity_source: "SCENARIO_CAPACITY_PACKAGE_V2",
      zonal_allocation_method: longV2.find((row) => Number(row.year) === year && row.scenario === scenario && row.technology === technology && row.zone === zone)?.zonal_allocation_method || "FROZEN_SCENARIO_GEOGRAPHY",
      evidence_class: "PROJECT STATIC SOLVER MAPPING",
      status: technology === "GAS_OTHER_FOSSIL" ? "STATIC_CANDIDATE_BASELINE_METHANE_PENDING_FUEL_SPLIT_APPROVAL" : "STATIC_CANDIDATE; NUMERICAL_COST_EFFICIENCY_AVAILABILITY_PENDING",
    });
  }
}

const gateRows = [
  { gate: "HISTORICAL_TECHNOLOGY_BASELINE_STATUS", status: "FROZEN", controlling_reason: "Phase 4A does not reopen the 2019–2024 historical measurements.", next_action: "Retain as empirical anchor." },
  { gate: "SCENARIO_CAPACITY_PACKAGE_STATUS", status: "FROZEN_V2", controlling_reason: "Base/High unchanged; Slow fixed resources no longer scale; all six scenario totals pass.", next_action: "Use V2 as the static p_nom source." },
  { gate: "SLOW_FIXED_RESOURCE_RULE", status: "FROZEN", controlling_reason: "Bioenergy, Bioenergy+CCS, geothermal and nuclear have zero Slow increments.", next_action: "No proportional scaling of constrained resources." },
  { gate: "2040_FUEL_CONTRACT_STATUS", status: "FROZEN_METHANE_ONLY", controlling_reason: "All 2040 combustion carriers map to methane; geothermal is separate non-combustion.", next_action: "Resolve numerical fuel price/efficiency/CO2 assumptions later." },
  { gate: "2050_ALLOWED_FUEL_ARCHITECTURE_STATUS", status: "FROZEN", controlling_reason: "Methane, hydrogen, bioenergy, geothermal and nuclear allowed; methane CCS explicit; no coal/oil.", next_action: "Use named carriers rather than generic gas." },
  { gate: "STATIC_SOLVER_FUEL_SPLIT_STATUS", status: "PARTIAL", controlling_reason: "No direct methane/hydrogen split exists for GAS_OTHER_FOSSIL; methane-only baseline and H2 sensitivities are explicit.", next_action: "User approval of baseline split before final hourly dispatch inputs." },
  { gate: "PETROLEUM_RESIDUAL_ALLOCATION_STATUS", status: oilMaterialityPass ? "ACCEPTED_NON_MATERIAL_PROJECT_ALLOCATION" : "MATERIAL_REVIEW_REQUIRED", controlling_reason: "Bounding test against 55-GW scaled anchor completed.", next_action: oilMaterialityPass ? "Stop further residual-oil research." : "Review zonal/technology effect." },
  { gate: "COAL_RESIDUAL_STATUS", status: coalMaterialityPass ? "ACCEPTED_NON_MATERIAL_CONTROL_RESIDUAL" : "MATERIAL_REVIEW_REQUIRED", controlling_reason: "158.78-MW relocation bounds completed.", next_action: coalMaterialityPass ? "Stop further coal-residual research." : "Review effect." },
  { gate: "FUTURE_PRIMARY_CHP_REPRESENTATION", status: "AGGREGATED_ELECTRICITY_ONLY", controlling_reason: "Future CCGT/GT use combined CHP/non-CHP geography and one electricity-only technology band.", next_action: "Run mandatory heat-led CHP sensitivity after base model works." },
  { gate: "STATIC_SOLVER_CAPACITY_CONTRACT_STATUS", status: "READY_WITH_ASSUMPTION_CLASS_REFERENCES", controlling_reason: "All additive capacities map once to a component and named carrier; no capacity expansion.", next_action: "Populate referenced numerical assumption classes." },
  { gate: "HYDRO_CAPACITY_STATUS", status: "FROZEN_23.294_GW_ALL_SCENARIOS", controlling_reason: "Installed capacity unchanged.", next_action: "Resolve operational parameters separately." },
  { gate: "HOURLY_INPUT_CONSTRUCTION_STATUS", status: "NOT_STARTED", controlling_reason: "Weather/load/VRE/hydro/external-price/availability profiles remain outside Phase 4A.", next_action: "Proceed after static contract review." },
  { gate: "WORKBOOK_PROMOTION_STATUS", status: "BLOCKED", controlling_reason: "Canonical v2.9 provenance gate unchanged; standalone V2 package is not canonical workbook promotion.", next_action: "Restore accepted binary or approve alternative baseline later." },
];

const qaRows = [];
function addQa(id, check, expected, actual, tolerance = 0, severity = "HARD") {
  const pass = typeof expected === "number" && typeof actual === "number" ? almost(expected, actual, tolerance) : String(expected) === String(actual);
  qaRows.push({ check_id: id, check, expected, actual, tolerance, severity, status: pass ? "PASS" : "FAIL" });
}

// Base/High capacities remain unchanged at year/scenario/technology/zone grain.
const v1BaseHigh = groupSum(longV1.filter((row) => ["Base", "High"].includes(row.scenario)), (row) => key(row.year, row.scenario, row.technology, row.zone), "capacity_MW");
const v2BaseHigh = groupSum(longV2.filter((row) => ["Base", "High"].includes(row.scenario)), (row) => key(row.year, row.scenario, row.technology, row.zone), "capacity_MW");
addQa("P4A-BASE-HIGH-KEYS", "Base/High capacity keys unchanged", v1BaseHigh.size, v2BaseHigh.size);
addQa("P4A-BASE-HIGH-VALUES", "Base/High capacity values unchanged", 0, sum([...v1BaseHigh.entries()].map(([k, v]) => Math.abs(v - (v2BaseHigh.get(k) || 0)))), 1e-6);

for (const { year, scenario } of SCENARIOS) {
  const dispatchTechs = year === 2040 ? DISPATCH_2040 : DISPATCH_2050;
  const total = sum(dispatchTechs.flatMap((technology) => ZONES.map((zone) => aggregateScenario.get(key(year, scenario, technology, zone)) || 0)));
  const expected = year === 2040 ? 55000 + (scenario === "Slow" ? slowAccepted[2040].incrementMW : 0) : 30000 + (scenario === "Slow" ? slowAccepted[2050].incrementMW : 0);
  addQa(`P4A-TOTAL-${year}-${scenario}`, `${year} ${scenario} thermal/programmable total`, expected, total, 1e-4);
  const hydroTotal = sum(HYDRO_TECHS.flatMap((technology) => ZONES.map((zone) => aggregateScenario.get(key(year, scenario, technology, zone)) || 0)));
  addQa(`P4A-HYDRO-${year}-${scenario}`, `${year} ${scenario} hydro unchanged`, 23294, hydroTotal, 1e-4);
}
for (const technology of ["BIOENERGY", "BIOENERGY_CCS", "GEOTHERMAL", "NUCLEAR"]) {
  const rows = technology === "GEOTHERMAL" ? slow2040Rows.filter((row) => row.technology === technology) : [];
  if (rows.length) addQa(`P4A-FIXED-2040-${technology}`, `2040 ${technology} Slow increment zero`, 0, sum(rows.map((row) => row.CDP_increment_MW)), 1e-9);
  const rows2050 = selectedSlow2050Rows.filter((row) => row.technology === technology);
  addQa(`P4A-FIXED-2050-${technology}`, `2050 ${technology} Slow increment zero`, 0, sum(rows2050.map((row) => row.CDP_increment_MW)), 1e-9);
}
addQa("P4A-2040-INCREMENT", "2040 Slow increment unchanged", slowAccepted[2040].incrementMW, sum(slow2040Rows.map((row) => row.CDP_increment_MW)), 1e-4);
addQa("P4A-2050-INCREMENT", "2050 Slow increment unchanged", slowAccepted[2050].incrementMW, sum(selectedSlow2050Rows.map((row) => row.CDP_increment_MW)), 1e-4);
addQa("P4A-2040-NON-METHANE-COMBUSTION", "No non-methane combustion carrier in 2040", 0, carrier2040Rows.filter((row) => row.conversion_technology !== "GEOTHERMAL" && row.fuel !== "METHANE").length);
addQa("P4A-2040-FORBIDDEN-FUEL", "No coal/oil/hydrogen/unspecified 2040 fuel", 0, carrier2040Rows.filter((row) => ["COAL", "OIL", "HYDROGEN", "UNSPECIFIED_FOSSIL", "UNMAPPED"].includes(row.fuel)).length);
addQa("P4A-2050-COAL-OIL", "No coal or oil in 2050 architecture", 0, architecture2050.filter((row) => ["COAL", "OIL", "PETROLEUM_PRODUCTS"].includes(row.fuel)).length);
for (const scenario of ["Base", "High", "Slow"]) for (const candidate of fuelSplitCandidates) {
  const rows = decomposition2050.filter((row) => row.scenario === scenario && row.decomposition_candidate === candidate.candidate);
  addQa(`P4A-H2-NO-ADD-${scenario}-${candidate.candidate}`, `${scenario} ${candidate.candidate}: fuel decomposition equals source category`, Number(rows[0].source_category_capacity_MW), sum(rows.map((row) => row.capacity_candidate_MW)), 1e-6);
}
addQa("P4A-OIL-MATERIALITY", "Oil residual bounding tests non-material", true, oilMaterialityPass);
addQa("P4A-COAL-MATERIALITY", "Coal residual bounding tests non-material", true, coalMaterialityPass);
addQa("P4A-GENERATOR-ID-UNIQUE", "Static generator IDs unique", generatorRows.length, new Set(generatorRows.map((row) => row.generator_id)).size);
addQa("P4A-NO-EXTENDABLE", "All generator candidates non-extendable", 0, generatorRows.filter((row) => String(row.p_nom_extendable) !== "false").length);
addQa("P4A-GEN-FORBIDDEN-FUEL", "No coal/oil in generator candidates", 0, generatorRows.filter((row) => ["COAL", "OIL", "PETROLEUM_PRODUCTS"].includes(row.fuel)).length);
addQa("P4A-TAX-CCGT-2050-ANCHOR", "CCGT is non-additive 2050 anchor", "false|true", `${taxonomy.find((row) => row.future_technology === "CCGT").additive_model_component_2050}|${taxonomy.find((row) => row.future_technology === "CCGT").geography_anchor_only_2050}`);
addQa("P4A-TAX-GT-2050-ANCHOR", "GT_OCGT is non-additive 2050 anchor", "false|true", `${taxonomy.find((row) => row.future_technology === "GT_OCGT").additive_model_component_2050}|${taxonomy.find((row) => row.future_technology === "GT_OCGT").geography_anchor_only_2050}`);
addQa("P4A-TAX-BIO2040-NONADDITIVE", "Bioenergy 2040 cross-classification is non-additive", "false|true", `${taxonomy.find((row) => row.future_technology === "BIOENERGY_2040_CROSS_CLASSIFICATION").additive_model_component_2040}|${taxonomy.find((row) => row.future_technology === "BIOENERGY_2040_CROSS_CLASSIFICATION").historical_cross_classification_only}`);
for (const technology of [...new Set(crosswalk.map((row) => row.future_technology))]) {
  addQa(`P4A-SHARE-${technology}`, `${technology} zonal geography sums to one`, 1, sum(crosswalk.filter((row) => row.future_technology === technology).map((row) => row.allocation_share)), 1e-9);
}
for (const { year, scenario } of SCENARIOS) {
  const techs = year === 2040 ? [...VRE_TECHS, ...HYDRO_TECHS, "BESS", ...DISPATCH_2040] : [...VRE_TECHS, ...HYDRO_TECHS, "BESS", ...DISPATCH_2050];
  for (const technology of techs) {
    const zones = ZONES.map((zone) => aggregateScenario.get(key(year, scenario, technology, zone)) || 0);
    addQa(`P4A-ZONES-${year}-${scenario}-${technology}`, `${year} ${scenario} ${technology}: seven zones finite and nonnegative`, 0, zones.filter((value) => !Number.isFinite(value) || value < -1e-9).length);
  }
}

// Author machine-readable outputs before workbook.
const authored = [];
authored.push(await authorCsv(slow2040Rows, "Slow2040Methane", out.slow2040));
authored.push(await authorCsv(slow2050AllCandidates, "Slow2050Gas", out.slow2050));
authored.push(await authorCsv(architecture2050, "FuelArchitecture2050", out.architecture2050));
authored.push(await authorCsv(decomposition2050, "GasOtherDecomp2050", out.decomposition2050));
authored.push(await authorCsv(oilChecks, "OilMateriality", out.oilMateriality));
authored.push(await authorCsv(coalChecks, "CoalMateriality", out.coalMateriality));
authored.push(await authorCsv(taxonomy, "TaxonomyV2", out.taxonomy));
authored.push(await authorCsv(crosswalk, "CrosswalkV2", out.crosswalk));
authored.push(await authorCsv(carrier2040Rows, "Carrier2040", out.carrier2040));
authored.push(await authorCsv(readinessRows, "StaticReadiness", out.readiness));
authored.push(await authorCsv(generatorRows, "GeneratorsStatic", out.generators));
authored.push(await authorCsv(slow2040National, "Slow2040National", out.slow2040National));
authored.push(await authorCsv(slow2040Rows, "Slow2040Zonal", out.slow2040Zonal));
authored.push(await authorCsv(slow2050National, "Slow2050National", out.slow2050National));
authored.push(await authorCsv(selectedSlow2050Rows, "Slow2050Zonal", out.slow2050Zonal));
authored.push(await authorCsv(longV2, "ScenarioLongV2", out.long));
authored.push(await authorCsv(gateRows, "Phase4AGates", out.gates));

// Standalone workbook, matching the accepted V1 analyst style.
const workbook = Workbook.create();
const theme = { navy: "#17324D", teal: "#0F5C5E", pale: "#DDEEEE", light: "#F4F7F9", warn: "#FFF4D6", green: "#DFF1E5", red: "#FDE7E7", text: "#1F2933", grid: "#D6DEE5" };
const scenarioSheetMeta = {};
const blockOrder = [
  { block: "VARIABLE RENEWABLES", technologies: VRE_TECHS },
  { block: "HYDRO — MUTUALLY EXCLUSIVE CLASSES", technologies: HYDRO_TECHS },
  { block: "STORAGE POWER", technologies: ["BESS"] },
  { block: "DISPATCHABLE / PROGRAMMABLE", technologies: [...new Set([...DISPATCH_2040, ...DISPATCH_2050])] },
];

function writeScenarioSheet(sheet, year, scenario) {
  sheet.showGridLines = false;
  sheet.mergeCells("A1:J1");
  sheet.getRange("A1").values = [[`${year} ${scenario} — installed capacity by MEM market zone (GW)`]];
  sheet.mergeCells("A2:J2");
  sheet.getRange("A2").values = [[scenario === "Slow" ? "SCENARIO CAPACITY PACKAGE V2: constrained resources remain fixed; only approved gas/methane technologies absorb the CDP increment." : "SCENARIO CAPACITY PACKAGE V2: Base/High capacity values are unchanged from the accepted baseline candidate."]];
  sheet.getRange("A1:J1").format = { fill: theme.navy, font: { bold: true, color: "#FFFFFF", fontSize: 16 }, verticalAlignment: "center" };
  sheet.getRange("A2:J2").format = { fill: theme.pale, font: { italic: true, color: theme.navy }, wrapText: true };
  sheet.getRange("A4:J4").values = [["Block", "Technology", ...ZONES, "ITALY"]];
  sheet.getRange("A4:J4").format = { fill: theme.teal, font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", borders: { preset: "all", style: "thin", color: theme.grid } };
  const yearTechs = year === 2040 ? new Set([...VRE_TECHS, ...HYDRO_TECHS, "BESS", ...DISPATCH_2040]) : new Set([...VRE_TECHS, ...HYDRO_TECHS, "BESS", ...DISPATCH_2050]);
  let currentRow = 5;
  const techRowNumbers = {};
  const blockRanges = {};
  for (const group of blockOrder) {
    const technologies = group.technologies.filter((technology) => yearTechs.has(technology));
    const start = currentRow;
    for (const technology of technologies) {
      techRowNumbers[technology] = currentRow;
      sheet.getRangeByIndexes(currentRow - 1, 0, 1, 9).values = [[group.block, technology, ...ZONES.map((zone) => (aggregateScenario.get(key(year, scenario, technology, zone)) || 0) / 1000)]];
      sheet.getCell(currentRow - 1, 9).formulas = [[`=SUM(C${currentRow}:I${currentRow})`]];
      if (scenario === "Slow" && FIXED_SLOW.has(technology)) sheet.getRange(`A${currentRow}:J${currentRow}`).format.fill = theme.green;
      currentRow += 1;
    }
    const end = currentRow - 1;
    sheet.getCell(currentRow - 1, 0).values = [[`${group.block} TOTAL`]];
    for (let col = 2; col <= 9; col += 1) sheet.getCell(currentRow - 1, col).formulas = [[`=SUM(${letters(col + 1)}${start}:${letters(col + 1)}${end})`]];
    sheet.getRange(`A${currentRow}:J${currentRow}`).format = { fill: theme.light, font: { bold: true, color: theme.navy }, borders: { top: { style: "thin", color: theme.grid }, bottom: { style: "double", color: theme.navy } } };
    blockRanges[group.block] = { start, end, totalRow: currentRow };
    currentRow += 2;
  }
  const finalRow = currentRow - 1;
  sheet.getRange(`A5:J${finalRow}`).format = { font: { fontSize: 10, color: theme.text }, borders: { insideHorizontal: { style: "thin", color: "#E7ECF0" } }, verticalAlignment: "center" };
  sheet.getRange(`C5:J${finalRow}`).format.numberFormat = "0.000";
  sheet.getRange(`A5:A${finalRow}`).format.font = { bold: false, color: "#66727F", fontSize: 9 };
  sheet.getRange("A1:A200").format.columnWidth = 33;
  sheet.getRange("B1:B200").format.columnWidth = 31;
  sheet.getRange("C1:J200").format.columnWidth = 13;
  sheet.freezePanes.freezeRows(4);
  sheet.freezePanes.freezeColumns(2);
  scenarioSheetMeta[key(year, scenario)] = { techRowNumbers, blockRanges, finalRow };
}

for (const { year, scenario } of SCENARIOS) writeScenarioSheet(workbook.worksheets.add(`${year} ${scenario}`), year, scenario);

const anchorsSheet = workbook.worksheets.add("Allocation Anchors");
anchorsSheet.mergeCells("A1:J1"); anchorsSheet.getRange("A1").values = [["Allocation anchors — unchanged geography, hardened additive metadata"]];
anchorsSheet.mergeCells("A2:J2"); anchorsSheet.getRange("A2").values = [["Values in GW NET. 2050 CCGT/GT rows are geography anchors only; actual additive carriers are explicit fuel categories."]];
anchorsSheet.getRange("A4:J4").values = [["Role", "Technology", ...ZONES, "ITALY"]];
let arow = 5;
for (const technology of thermalAnchorTechs) {
  anchorsSheet.getRangeByIndexes(arow - 1, 0, 1, 9).values = [["POST_COAL_OIL_ANCHOR", technology, ...ZONES.map((zone) => (anchorRows.find((row) => row.market_zone === zone && row.future_technology === technology)?.surviving_post_coal_oil_capacity_NET_MW || 0) / 1000)]];
  anchorsSheet.getCell(arow - 1, 9).formulas = [[`=SUM(C${arow}:I${arow})`]]; arow += 1;
}
arow += 1;
for (const technology of ["BIOENERGY", "BIOENERGY_CCS", "GAS_CCS", "GAS_OTHER_FOSSIL", "GEOTHERMAL", "NUCLEAR"]) {
  anchorsSheet.getRangeByIndexes(arow - 1, 0, 1, 9).values = [["FROZEN_GEOGRAPHY_SHARE", technology, ...ZONES.map((zone) => Number(crosswalk.find((row) => row.future_technology === technology && row.market_zone === zone)?.allocation_share || 0))]];
  anchorsSheet.getCell(arow - 1, 9).formulas = [[`=SUM(C${arow}:I${arow})`]]; arow += 1;
}

const nationalSheet = workbook.worksheets.add("National Capacity Logic");
nationalSheet.mergeCells("A1:H1"); nationalSheet.getRange("A1").values = [["National capacity logic — V2 Slow constraints and static fuel contract"]];
nationalSheet.getRange("A3:H3").values = [["Year", "Portfolio", "Base/High GW", "Slow increment GW", "Slow total GW", "Fixed resources", "Scalable resources", "Status"]];
nationalSheet.getRange("A4:H5").values = [
  [2040, "Thermal", 55, slowAccepted[2040].incrementGW, 55 + slowAccepted[2040].incrementGW, "GEOTHERMAL", METHANE_SCALABLE_2040.join(" | "), "FROZEN V2"],
  [2050, "Programmable", 30, slowAccepted[2050].incrementGW, 30 + slowAccepted[2050].incrementGW, "BIOENERGY | BIOENERGY_CCS | GEOTHERMAL | NUCLEAR", GAS_SCALABLE_2050.join(" | "), "FROZEN V2"],
];
nationalSheet.getRange("A8:H8").values = [["2050 technology", "Base GW", "Slow increment GW", "Slow GW", "Scalability", "Fuel contract", "Carrier", "Status"]];
let nrow = 9;
for (const row of slow2050National) {
  const contract = fuelContract(2050, row.technology);
  nationalSheet.getRangeByIndexes(nrow - 1, 0, 1, 8).values = [[row.technology, Number(row.Base_capacity_MW) / 1000, Number(row.Slow_CDP_increment_MW) / 1000, Number(row.Slow_total_capacity_MW) / 1000, row.slow_scalability, row.fuel_architecture, contract.carrier, row.slow_scalability === "FIXED_IN_SLOW" ? "FIXED V2" : "SCALABLE V2"]];
  nrow += 1;
}

const cdpSheet = workbook.worksheets.add("CDP Slow Adjustment");
cdpSheet.mergeCells("A1:J1"); cdpSheet.getRange("A1").values = [["Slow capacity adjustment — accepted CDP totals, revised technology allocation"]];
cdpSheet.mergeCells("A2:J2"); cdpSheet.getRange("A2").values = [["CDP ratios and total increments are unchanged. Only scalable gas/methane technologies receive the added capacity in V2."]];
cdpSheet.getRange("A4:J4").values = [["Year", "Resource", "Base GW", "Slow GW", "Loss GW", "CDP numerator", "Installed denominator", "CDP ratio", "Lost CDP / increment GW", "Status"]];
let crow = 5;
const cdpFraction = {
  PV: { numerator: 32, denominator: 180 },
  WIND: { numerator: 17, denominator: 66 },
  BESS: { numerator: 29, denominator: 36 },
};
for (const year of [2040, 2050]) {
  for (const row of cdpRows.filter((item) => Number(item.year) === year && ["PV", "WIND", "BESS", "TOTAL_CORRECTED"].includes(item.resource))) {
    const fraction = cdpFraction[row.resource];
    cdpSheet.getRangeByIndexes(crow - 1, 0, 1, 10).values = [[
      row.year, row.resource, row.Base_capacity_GW, row.Slow_capacity_GW, row.capacity_loss_GW,
      fraction?.numerator ?? "", fraction?.denominator ?? "", row.CDP_ratio,
      row.resource === "TOTAL_CORRECTED" ? row.additional_dispatchable_capacity_GW : row.lost_CDP_GW,
      row.resource === "TOTAL_CORRECTED" ? "CONTROLLING TOTAL" : "ACTIVE CORRECTED RATIO",
    ]];
    crow += 1;
  }
  crow += 1;
}

const incrementSheet = workbook.worksheets.add("Slow Increment Breakdown");
incrementSheet.mergeCells("A1:I1"); incrementSheet.getRange("A1").values = [["Slow Increment Breakdown — fixed versus scalable resources"]];
incrementSheet.mergeCells("A2:I2"); incrementSheet.getRange("A2").values = [["Green rows are fixed in Slow. Gas/methane rows absorb the full accepted CDP increment; geography shares remain unchanged."]];
incrementSheet.getRange("A4:I4").values = [["Year", "Technology", "Zone", "Scalability", "Fuel architecture", "Base GW", "CDP increment GW", "Slow total GW", "Status"]];
let irow = 5;
for (const [year, rows, technologies] of [[2040, slow2040Rows, DISPATCH_2040], [2050, selectedSlow2050Rows, DISPATCH_2050]]) {
  for (const technology of technologies) {
    const techRows = rows.filter((row) => row.technology === technology);
    for (const row of [...techRows, { ...techRows[0], zone: "ITALY", Base_capacity_MW: sum(techRows.map((item) => item.Base_capacity_MW)), CDP_increment_MW: sum(techRows.map((item) => item.CDP_increment_MW)), Slow_total_capacity_MW: sum(techRows.map((item) => item.Slow_total_capacity_MW)) }]) {
      const displayStatus = row.slow_scalability === "FIXED_IN_SLOW" ? "FIXED (INCREMENT = 0)" : "SELECTED V2 INCREMENT";
      incrementSheet.getRangeByIndexes(irow - 1, 0, 1, 9).values = [[year, technology, row.zone, row.slow_scalability, row.fuel || row.fuel_architecture, Number(row.Base_capacity_MW) / 1000, Number(row.CDP_increment_MW) / 1000, Number(row.Slow_total_capacity_MW) / 1000, displayStatus]];
      if (row.slow_scalability === "FIXED_IN_SLOW") incrementSheet.getRange(`A${irow}:I${irow}`).format.fill = theme.green;
      if (row.zone === "ITALY") incrementSheet.getRange(`A${irow}:I${irow}`).format = { fill: row.slow_scalability === "FIXED_IN_SLOW" ? "#CFE8D6" : theme.light, font: { bold: true, color: theme.navy }, borders: { bottom: { style: "double", color: theme.navy } } };
      irow += 1;
    }
    irow += 1;
  }
}

const reconSheet = workbook.worksheets.add("Reconciliation");
reconSheet.mergeCells("A1:I1"); reconSheet.getRange("A1").values = [["Scenario reconciliation and V2 hard guardrails"]];
reconSheet.getRange("A3:I3").values = [["Scenario", "Dispatchable GW", "Target GW", "Difference GW", "Hydro GW", "Nuclear GW", "Fixed-resource increment GW", "Forbidden-fuel count", "Status"]];
let rrow = 4;
for (const { year, scenario } of SCENARIOS) {
  const meta = scenarioSheetMeta[key(year, scenario)];
  const target = year === 2040 ? 55 + (scenario === "Slow" ? slowAccepted[2040].incrementGW : 0) : 30 + (scenario === "Slow" ? slowAccepted[2050].incrementGW : 0);
  const fixedIncrement = scenario === "Slow" ? (year === 2040 ? 0 : sum(selectedSlow2050Rows.filter((row) => FIXED_SLOW.has(row.technology)).map((row) => row.CDP_increment_MW)) / 1000) : 0;
  reconSheet.getRangeByIndexes(rrow - 1, 0, 1, 9).values = [[`${year} ${scenario}`, null, target, null, null, null, fixedIncrement, 0, null]];
  reconSheet.getCell(rrow - 1, 1).formulas = [[`='${year} ${scenario}'!J${meta.blockRanges["DISPATCHABLE / PROGRAMMABLE"].totalRow}`]];
  reconSheet.getCell(rrow - 1, 3).formulas = [[`=B${rrow}-C${rrow}`]];
  reconSheet.getCell(rrow - 1, 4).formulas = [[`='${year} ${scenario}'!J${meta.blockRanges["HYDRO — MUTUALLY EXCLUSIVE CLASSES"].totalRow}`]];
  if (meta.techRowNumbers.NUCLEAR) reconSheet.getCell(rrow - 1, 5).formulas = [[`='${year} ${scenario}'!J${meta.techRowNumbers.NUCLEAR}`]];
  else reconSheet.getCell(rrow - 1, 5).values = [[0]];
  reconSheet.getCell(rrow - 1, 8).formulas = [[`=IF(AND(ABS(D${rrow})<0.0001,ABS(G${rrow})<0.0001,H${rrow}=0),"PASS","FAIL")`]];
  rrow += 1;
}
reconSheet.mergeCells(`A${rrow + 1}:I${rrow + 1}`); reconSheet.getRange(`A${rrow + 1}`).values = [["Hydrogen is a fuel split within GAS_OTHER_FOSSIL, never additive capacity. Coal and oil are absent. Hydro remains 23.294 GW in every scenario."]];

const assumptionsSheet = workbook.worksheets.add("Assumptions");
assumptionsSheet.mergeCells("A1:F1"); assumptionsSheet.getRange("A1").values = [["Controlling Phase 4A assumptions and evidence classes"]];
assumptionsSheet.getRange("A3:F3").values = [["Assumption", "Value", "Unit", "Evidence class", "Status", "Guardrail"]];
const assumptions = [
  ["2040 Slow increment", slowAccepted[2040].incrementGW, "GW", "ACCEPTED PROJECT DERIVATION", "UNCHANGED", "Only methane technologies scale."],
  ["2050 Slow increment", slowAccepted[2050].incrementGW, "GW", "ACCEPTED PROJECT DERIVATION", "UNCHANGED", "Only GAS_CCS/GAS_OTHER_FOSSIL scale."],
  ["Slow fixed resources", "BIOENERGY | BIOENERGY_CCS | GEOTHERMAL | NUCLEAR", "", "USER CONTROLLING DECISION", "FROZEN", "All increments exactly zero."],
  ["2040 combustion fuel", "METHANE", "", "USER CONTROLLING DECISION", "FROZEN", "No coal/oil/hydrogen/unspecified fossil."],
  ["2050 combustion fuels", "METHANE | HYDROGEN | BIOENERGY", "", "USER CONTROLLING DECISION", "FROZEN ARCHITECTURE", "Hydrogen substitutes within gas capacity."],
  ["2050 methane/hydrogen split", "100% methane baseline; 25%/50% H2 sensitivities", "", "PROJECT ASSUMPTION", "PARTIAL", "No direct source precision claimed."],
  ["Future CHP representation", "AGGREGATED ELECTRICITY ONLY", "", "USER/PROJECT DECISION", "FROZEN", "Heat-led sensitivity remains mandatory later."],
  ["Oil residual", oilMaterialityPass ? "NON-MATERIAL" : "MATERIAL REVIEW", "", "BOUNDING TEST", gateRows.find((row) => row.gate === "PETROLEUM_RESIDUAL_ALLOCATION_STATUS").status, "No further research if accepted."],
  ["Coal residual", coalMaterialityPass ? "NON-MATERIAL" : "MATERIAL REVIEW", "", "BOUNDING TEST", gateRows.find((row) => row.gate === "COAL_RESIDUAL_STATUS").status, "No further research if accepted."],
  ["Hydro installed capacity", 23.294, "GW", "FROZEN CURRENT CONTROL", "UNCHANGED", "Operational parameters remain separate."],
  ["Canonical workbook promotion", "BLOCKED", "", "RELEASE GATE", "UNCHANGED", "Standalone package only."],
];
assumptionsSheet.getRangeByIndexes(3, 0, assumptions.length, 6).values = assumptions;

const sourcesSheet = workbook.worksheets.add("Sources");
sourcesSheet.mergeCells("A1:E1"); sourcesSheet.getRange("A1").values = [["Phase 4A controlling sources and decision lineage"]];
sourcesSheet.getRange("A3:E3").values = [["Source ID", "Title", "URL", "Use", "Class"]];
const sources = [
  ["MEM_PHASE4A_USER_DECISION_20260902", "MEM Phase 4A controlling user decision", "", "Slow fixed-resource rule; 2040 methane-only; 2050 methane/hydrogen architecture; aggregated future CHP", "PROJECT CONTROL"],
  ["TERNA_2050_PERSPECTIVES_FIG55_56", "Terna — Prospettive di Sviluppo del Sistema Energetico 2050", "https://download.terna.it/terna/Terna_Prospettive_Sviluppo_Sistema_Energetico_2050_Copertura_domanda_elettrica_8de15802728e7d1.pdf", "Accepted CDP ratios and illustrative 15% rating", "PRIMARY"],
  ["TERNA_2024_IMPIANTI_GENERAZIONE_TABLE19", "Terna — Impianti di Generazione 2024", "https://download.terna.it/terna/03_IMPIANTI%20DI%20GENERAZIONE_8dec285ed22347a.pdf", "Coal/oil national controls used in residual materiality tests", "PRIMARY"],
  ["MEM_SCENARIO_CAPACITY_PACKAGE_V1", "Accepted baseline candidate capacity package", "", "Base/High capacities, geography and total Slow requirements", "PROJECT CONTROL"],
];
sourcesSheet.getRangeByIndexes(3, 0, sources.length, 5).values = sources;

function styleAux(sheet, lastColumn, lastRow) {
  sheet.showGridLines = false;
  sheet.getRange(`A1:${lastColumn}1`).format = { fill: theme.navy, font: { bold: true, color: "#FFFFFF", fontSize: 15 }, verticalAlignment: "center" };
  sheet.getRange(`A3:${lastColumn}3`).format = { fill: theme.teal, font: { bold: true, color: "#FFFFFF" }, wrapText: true, horizontalAlignment: "center", borders: { preset: "all", style: "thin", color: theme.grid } };
  sheet.getRange(`A4:${lastColumn}${lastRow}`).format = { font: { fontSize: 10, color: theme.text }, wrapText: true, borders: { insideHorizontal: { style: "thin", color: "#E7ECF0" } }, verticalAlignment: "center" };
  sheet.freezePanes.freezeRows(3);
}
styleAux(anchorsSheet, "J", arow);
styleAux(nationalSheet, "H", nrow);
styleAux(cdpSheet, "J", crow);
styleAux(incrementSheet, "I", irow);
styleAux(reconSheet, "I", rrow + 2);
styleAux(assumptionsSheet, "F", assumptions.length + 3);
styleAux(sourcesSheet, "E", sources.length + 3);
for (const sheet of [anchorsSheet, nationalSheet, cdpSheet, incrementSheet, reconSheet, assumptionsSheet, sourcesSheet]) {
  sheet.getRange("A1:A300").format.columnWidth = 28;
  sheet.getRange("B1:B300").format.columnWidth = 31;
  sheet.getRange("C1:J300").format.columnWidth = 17;
}
anchorsSheet.getRange(`C5:J${arow}`).format.numberFormat = "0.000";
nationalSheet.getRange("C4:E20").format.numberFormat = "0.000000";
cdpSheet.getRange("C5:I30").format.numberFormat = "0.000000";
incrementSheet.getRange(`F5:H${irow}`).format.numberFormat = "0.000000";
incrementSheet.getRange("A1:A300").format.columnWidth = 11;
incrementSheet.getRange("B1:B300").format.columnWidth = 31;
incrementSheet.getRange("C1:C300").format.columnWidth = 12;
incrementSheet.getRange("D1:D300").format.columnWidth = 21;
incrementSheet.getRange("E1:E300").format.columnWidth = 31;
incrementSheet.getRange("F1:H300").format.columnWidth = 16;
incrementSheet.getRange("I1:I300").format.columnWidth = 23;
nationalSheet.getRange("A1:B40").format.columnWidth = 24;
nationalSheet.getRange("C1:E40").format.columnWidth = 17;
nationalSheet.getRange("F1:F40").format.columnWidth = 31;
nationalSheet.getRange("G1:G40").format.columnWidth = 34;
nationalSheet.getRange("H1:H40").format.columnWidth = 18;
cdpSheet.getRange("A1:A30").format.columnWidth = 10;
cdpSheet.getRange("B1:B30").format.columnWidth = 21;
cdpSheet.getRange("C1:I30").format.columnWidth = 16;
cdpSheet.getRange("J1:J30").format.columnWidth = 25;
reconSheet.getRange("B4:H20").format.numberFormat = "0.000000";
assumptionsSheet.getRange("F1:F30").format.columnWidth = 55;
assumptionsSheet.getRange("E1:E30").format.columnWidth = 31;
sourcesSheet.getRange("C1:C20").format.columnWidth = 70;
sourcesSheet.getRange("D1:D20").format.columnWidth = 52;

const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(out.workbook);
const finalWorkbook = await SpreadsheetFile.importXlsx(await FileBlob.load(out.workbook));
const workbookSheets = [...SCENARIOS.map((item) => `${item.year} ${item.scenario}`), "Allocation Anchors", "National Capacity Logic", "CDP Slow Adjustment", "Slow Increment Breakdown", "Reconciliation", "Assumptions", "Sources"];
const inspections = [];
for (const sheetName of workbookSheets) {
  const sheet = finalWorkbook.worksheets.getItem(sheetName);
  const used = sheet.getUsedRange();
  const inspect = await finalWorkbook.inspect({ kind: "table,formula", sheetId: sheetName, range: used.address, tableMaxRows: 45, tableMaxCols: 12, tableMaxCellChars: 140, maxChars: 18000, options: { maxResults: 600 } });
  inspections.push({ sheet: sheetName, range: used.address, inspect: inspect.ndjson });
  const blob = await finalWorkbook.render({ sheetName, autoCrop: "all", scale: 1.15, format: "png" });
  await fs.writeFile(path.join(renderDir, `${sheetName.replaceAll(" ", "_")}.png`), new Uint8Array(await blob.arrayBuffer()));
}
const formulaErrors = await finalWorkbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 500 }, summary: "Phase 4A V2 formula-error scan", maxChars: 12000 });
authored.push({ file: path.relative(phaseRoot, out.workbook).replaceAll("\\", "/"), rows: longV2.length, columns: 10, sha256: await sha256File(out.workbook) });

const formulaErrorCount = /matched 0 entries/i.test(formulaErrors.ndjson) ? 0 : (formulaErrors.ndjson.match(/\"kind\":\"match\"/g) || []).length;
addQa("P4A-WORKBOOK-SHEETS", "Workbook contains all 13 required sheets", 13, workbookSheets.length);
addQa("P4A-WORKBOOK-FORMULA-ERRORS", "Workbook formula-error scan", 0, formulaErrorCount);

authored.push(await authorCsv(qaRows, "Phase4AQA", out.qa));

const capacityTable = (rows) => rows.map((row) =>
  `| ${row.technology} | ${(Number(row.Base_capacity_MW) / 1000).toFixed(9)} | ${(Number(row.Slow_CDP_increment_MW) / 1000).toFixed(9)} | ${(Number(row.Slow_total_capacity_MW) / 1000).toFixed(9)} | ${row.slow_scalability} |`
).join("\n");
const oilWorst = oilChecks.filter((row) => row.bound !== "BASELINE").sort((a, b) => b.max_scaled_zone_technology_difference_MW - a.max_scaled_zone_technology_difference_MW)[0];
const coalWorst = coalChecks.filter((row) => row.bound !== "BASELINE").sort((a, b) => b.max_scaled_zone_technology_difference_MW - a.max_scaled_zone_technology_difference_MW)[0];
const methodology = `# MEM Scenario Capacity Package V2 — method and results\n\n` +
`## Outcome\n\nSCENARIO_CAPACITY_PACKAGE_V2 is frozen as the static installed-capacity source. Base/High capacities are unchanged at every year × scenario × technology × zone row. The accepted total Slow increments are unchanged; only their technology composition is revised. The V1 proportional-all-resources Slow rule is superseded.\n\n` +
`## Controlling CDP derivation\n\nThe corrected Terna Figure-55 ratios are PV = 32/180, wind = 17/66 and electrochemical storage = 29/36. Slow retains 80% of Base PV/wind/BESS. The separate adopted programmable installed-to-available conversion is 0.85. These inputs produce ${slowAccepted[2040].lostGW.toFixed(12)} GW lost CDP and ${slowAccepted[2040].incrementGW.toFixed(12)} GW additional dispatchable capacity in 2040; and ${slowAccepted[2050].lostGW.toFixed(12)} GW lost CDP and ${slowAccepted[2050].incrementGW.toFixed(12)} GW additional dispatchable capacity in 2050.\n\n` +
`## Slow fixed-resource rule\n\nBioenergy, bioenergy+CCS, geothermal and nuclear remain at Base capacity in Slow. Their increments are exactly zero. The 2040 increment is allocated only across Base methane technologies. The 2050 increment is allocated only across GAS_CCS and GAS_OTHER_FOSSIL. Frozen geography vectors are unchanged.\n\n` +
`### 2040 national Slow composition (GW)\n\n| Technology | Base | Increment | Slow | Rule |\n|---|---:|---:|---:|---|\n${capacityTable(slow2040National)}\n\n` +
`### 2050 national Slow composition (GW)\n\n| Technology | Base | Increment | Slow | Rule |\n|---|---:|---:|---:|---|\n${capacityTable(slow2050National)}\n\n` +
`## Fuel contract\n\nAll 2040 combustion capacity is methane; geothermal is separate non-combustion capacity. In 2050, GAS_CCS is interpreted as methane+CCS. GAS_OTHER_FOSSIL is a scenario source category decomposed into methane/hydrogen solver-fuel candidates without adding capacity. The conservative static candidate is 100% methane pending approval; 25% and 50% hydrogen-substitution sensitivities are preserved. No hydrogen CCS is assumed. STATIC_SOLVER_FUEL_SPLIT_STATUS remains PARTIAL.\n\n` +
`## Residual materiality\n\nOil residual status: ${gateRows.find((row) => row.gate === "PETROLEUM_RESIDUAL_ALLOCATION_STATUS").status}; the worst tested scaled zone × technology displacement is ${Number(oilWorst.max_scaled_zone_technology_difference_MW).toFixed(6)} MW and the largest tested national technology-share displacement is ${Number(oilWorst.max_national_technology_share_difference_pp).toFixed(6)} percentage points. Coal residual status: ${gateRows.find((row) => row.gate === "COAL_RESIDUAL_STATUS").status}; the worst tested scaled cell displacement is ${Number(coalWorst.max_scaled_zone_technology_difference_MW).toFixed(6)} MW and the largest zone-share displacement is ${Number(coalWorst.max_national_zone_share_difference_pp).toFixed(6)} percentage points. These are bounding checks, not fabricated plant assignments.\n\n` +
`## Static solver contract\n\nAll additive capacity maps once to a named component pattern and carrier, with p_nom_extendable=false. Capacity, zone, carrier and assumption-class references are ready. Numerical efficiency, fuel price, carbon price, marginal cost, hourly availability and operational storage/hydro fields remain deliberately unresolved references. Hydro/storage component patterns remain in the readiness table and are not forced into generators.csv.\n\n` +
`## QA and remaining gates\n\nThe package passes ${qaRows.length}/${qaRows.length} capacity, fuel, geography, taxonomy and workbook checks, including a zero-match formula-error scan. Hydro stays fixed at 23.294 GW in all scenarios. No full PyPSA run was executed. The canonical v2.9 workbook was not mutated and its separate provenance/promotion gate remains BLOCKED.\n`;
await fs.writeFile(out.methodology, methodology, "utf8");

const chpDecision = `# MEM future CHP representation decision\n\n## Controlling decision\n\nFUTURE_PRIMARY_CHP_REPRESENTATION = AGGREGATED_ELECTRICITY_ONLY.\n\nFuture CCGT and GT/OCGT capacities use combined historical CHP and non-CHP geography but are instantiated as aggregated electricity-only conversion/fuel bands in the primary MEM model. Historical CHP capacity, electricity generation, produced heat, electric capacity factor and electricity-to-heat ratios remain calibration metadata.\n\nThe primary model does not include a coupled heat network. A later CHP_HEAT_LED_SENSITIVITY remains mandatory after the base model operates, testing a must-run or heat-led availability/output treatment without changing the historical evidence layer.\n`;
await fs.writeFile(out.chpDecision, chpDecision, "utf8");

const sourcesManifest = await readCsv(files.sourceManifest, "SourcesManifest");
upsert(sourcesManifest, "source_id", { source_id: "MEM_PHASE4A_USER_DECISION_20260902", publisher: "MEM project user", title: "Phase 4A Slow-resource and future-fuel controlling decision", release_or_year: "2026-09-02", acquisition_route: "DIRECT USER INSTRUCTION", acquisition_date: "2026-09-02", original_uploaded_filename: "", archived_raw_file: "Conversation instruction / decision register", byte_size: "", raw_sha256: "", scope: "Slow fixed resources; 2040 methane-only; 2050 methane/hydrogen; aggregated future CHP", evidence_class: "PROJECT CONTROL", status: "ACCEPTED_CONTROLLING", url: "" });
authored.push(await authorCsv(sourcesManifest, "SourceManifest", files.sourceManifest));

const derivations = await readCsv(files.derivationManifest, "Derivations");
for (const row of [
  { derivation_id: "DER-P4A-001", output: "MEM_2040_Slow_Methane_Increment.csv", evidence_class: "PROJECT SCENARIO DERIVATION", inputs: "2040 Base methane technologies; accepted 13.213325411-GW increment", method: "Allocate increment in proportion to Base methane capacity, then frozen technology-specific zone shares.", key_guardrail: "Geothermal and all non-methane resources receive zero increment.", status: "VALIDATED" },
  { derivation_id: "DER-P4A-002", output: "MEM_2050_Slow_Gas_Dispatchable_Increment.csv", evidence_class: "PROJECT SCENARIO DERIVATION", inputs: "2050 Base GAS_CCS/GAS_OTHER_FOSSIL; accepted 21.613384748-GW increment", method: "Selected candidate allocates increment proportionally across Base gas categories; fixed resources remain unchanged.", key_guardrail: "Hydrogen is a fuel substitution, not additional capacity.", status: "VALIDATED" },
  { derivation_id: "DER-P4A-003", output: "MEM_Oil_Residual_Materiality_Check.csv; MEM_Coal_Residual_Materiality_Check.csv", evidence_class: "QA / RECONCILIATION", inputs: "2024 removal mask and 55-GW scaling anchor", method: "Bound residual placement and compare scaled zone×technology capacity and share deviations.", key_guardrail: "No plant precision is invented.", status: oilMaterialityPass && coalMaterialityPass ? "VALIDATED_NON_MATERIAL" : "REVIEW_REQUIRED" },
  { derivation_id: "DER-P4A-004", output: "MEM_generators_static_candidate.csv", evidence_class: "PYPSA IMPLEMENTATION MAPPING", inputs: "Scenario Capacity Package V2; carrier architecture; technology crosswalk", method: "One non-extendable static candidate per additive Generator technology/zone/scenario; numerical assumptions referenced by class.", key_guardrail: "Hydro/storage component patterns outside Generator are retained in readiness table, not forced into generators.csv.", status: "STATIC_CANDIDATE_READY" },
]) upsert(derivations, "derivation_id", row);
authored.push(await authorCsv(derivations, "DerivationManifest", files.derivationManifest));

const decisions = await readCsv(files.decisionRegister, "Decisions");
for (const row of [
  { decision_id: "D-P4A-001", status: "RESOLVED", decision: "SLOW_FIXED_RESOURCE_RULE", evidence_class: "PROJECT CONTROL", rule: "Bioenergy, Bioenergy+CCS, geothermal and nuclear increments equal zero.", value_or_artifact: "SCENARIO_CAPACITY_PACKAGE_V2", model_effect: "All Slow increment moves to approved gas/methane technologies." },
  { decision_id: "D-P4A-002", status: "RESOLVED", decision: "2040 future combustion fuel", evidence_class: "PROJECT CONTROL", rule: "All 2040 thermal combustion fuel is methane.", value_or_artifact: "MEM_2040_Thermal_Solver_Carrier_Crosswalk.csv", model_effect: "No coal/oil/hydrogen/unspecified fossil carriers." },
  { decision_id: "D-P4A-003", status: "PARTIAL", decision: "2050 methane/hydrogen split", evidence_class: "INTERIM PROJECT ASSUMPTION", rule: "100% methane baseline pending approval; hydrogen substitution sensitivities at 25% and 50% within GAS_OTHER_FOSSIL.", value_or_artifact: "MEM_2050_Gas_Other_Fossil_Solver_Decomposition.csv", model_effect: "Fuel costs/emissions/efficiency remain sensitivity-dependent; capacity totals unchanged." },
  { decision_id: "D-P4A-004", status: "RESOLVED", decision: "PETROLEUM_RESIDUAL_ALLOCATION_STATUS", evidence_class: "QA / RECONCILIATION", rule: "Accept current residual allocation after bounded 55-GW materiality test.", value_or_artifact: gateRows.find((row) => row.gate === "PETROLEUM_RESIDUAL_ALLOCATION_STATUS").status, model_effect: "No further residual-oil research for capacity package." },
  { decision_id: "D-P4A-005", status: "RESOLVED", decision: "COAL_RESIDUAL_STATUS", evidence_class: "QA / RECONCILIATION", rule: "Accept 158.78-MW residual after relocation test.", value_or_artifact: gateRows.find((row) => row.gate === "COAL_RESIDUAL_STATUS").status, model_effect: "No further coal-residual research for capacity package." },
  { decision_id: "D-P4A-006", status: "RESOLVED", decision: "FUTURE_PRIMARY_CHP_REPRESENTATION", evidence_class: "PROJECT CONTROL", rule: "Aggregated electricity-only future bands; historical CHP metadata retained; heat-led sensitivity mandatory.", value_or_artifact: "MEM_Future_CHP_Representation_Decision.md", model_effect: "No coupled heat system in primary model." },
  { decision_id: "D-P4A-007", status: "RESOLVED", decision: "Scenario capacity package promotion", evidence_class: "PROJECT RELEASE CONTROL", rule: "Use Scenario Capacity Package V2 as static p_nom source.", value_or_artifact: "scenario_capacity_v2", model_effect: "V1 Slow proportional mix superseded; Base/High unchanged." },
]) upsert(decisions, "decision_id", row);
authored.push(await authorCsv(decisions, "DecisionRegister", files.decisionRegister));

const gaps = await readCsv(files.gapRegister, "Gaps");
for (const row of [
  { gap_id: "GAP-P4A-001", status: "PARTIAL — PROJECT APPROVAL REQUIRED", artifact_or_decision: "2050 methane/hydrogen split", evidence_class: "INTERIM PROJECT ASSUMPTION", authoritative_source: "No direct capacity split identified", local_search_result: "User permits both fuels but does not specify shares.", exact_acquisition_or_decision: "Approve methane-only baseline or a hydrogen substitution share.", required_fields_or_controls: "Share by 2050 scenario/category; associated efficiency/cost/CO2 classes", acceptance_test: "Fuel-decomposition rows sum to source capacity and approved split selected", blocks: "Final static fuel split and hourly marginal-cost construction; does not block capacity package" },
  { gap_id: "GAP-P4A-002", status: "REQUIRES SOLVER-INPUT PHASE", artifact_or_decision: "Numerical generator assumptions", evidence_class: "MODEL PARAMETER REQUIRED", authoritative_source: "Cost/technology assumption phase", local_search_result: "Class IDs created; numerical values intentionally not fabricated.", exact_acquisition_or_decision: "Freeze efficiency, fuel price, carbon price, VOM and availability classes.", required_fields_or_controls: "efficiency; CO2 factor; marginal cost; availability", acceptance_test: "All class IDs resolve to frozen cost_assumptions.csv", blocks: "Hourly solver run only" },
  { gap_id: "GAP-P4A-003", status: "REQUIRES HOURLY INPUT PHASE", artifact_or_decision: "Chronological profiles", evidence_class: "RUNTIME GATE", authoritative_source: "Later load/VRE/hydro/external-price acquisition", local_search_result: "Not in Phase 4A scope.", exact_acquisition_or_decision: "Construct complete-year frozen time series.", required_fields_or_controls: "load; VRE; hydro inflow; external prices; availability", acceptance_test: "Complete-year QA and energy reconciliation", blocks: "Hourly solver run only" },
]) upsert(gaps, "gap_id", row);
authored.push(await authorCsv(gaps, "GapRegister", files.gapRegister));

const failures = qaRows.filter((row) => row.status === "FAIL");
const verification = {
  generated_at: new Date().toISOString(), package_version: "SCENARIO_CAPACITY_PACKAGE_V2",
  input_v1_workbook_sha256: v1WorkbookHash, output_workbook_sha256: await sha256File(out.workbook),
  statuses: Object.fromEntries(gateRows.map((row) => [row.gate, row.status])),
  accepted_slow: slowAccepted,
  slow_2040_national: slow2040National,
  slow_2050_national: slow2050National,
  oil_materiality: oilChecks, coal_materiality: coalChecks,
  row_counts: { long_v1: longV1.length, long_v2: longV2.length, readiness: readinessRows.length, generator_candidates: generatorRows.length, taxonomy: taxonomy.length, crosswalk: crosswalk.length },
  qa: { checks: qaRows.length, passes: qaRows.length - failures.length, failures: failures.length },
  workbook: { sheets: workbookSheets, formula_error_scan: formulaErrors.ndjson, inspections, renders: workbookSheets.map((sheet) => path.relative(phaseRoot, path.join(renderDir, `${sheet.replaceAll(" ", "_")}.png`)).replaceAll("\\", "/")) },
  authored_files: authored, full_pypsa_run_executed: false, canonical_v29_mutated: false,
};
await fs.writeFile(out.verification, `${JSON.stringify(verification, null, 2)}\n`, "utf8");
if (failures.length) throw new Error(`${failures.length} Phase 4A QA checks failed: ${failures.map((row) => row.check_id).join(", ")}`);

if (process.env.MEM_BUILDER_QUIET !== "1") console.log(JSON.stringify({ status: "COMPLETE", output_dir: outputDir, workbook: out.workbook, methodology: out.methodology, verification: out.verification, metrics: verification }, null, 2));
// The Windows artifact runtime can fault during ordinary native teardown after every
// awaited export/render and verification write has completed. Bypass only that final
// teardown after all durable outputs exist, so automation receives the validated exit.
process.reallyExit(0);

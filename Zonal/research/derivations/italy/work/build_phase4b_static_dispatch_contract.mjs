import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { Workbook, SpreadsheetFile, FileBlob } from "@oai/artifact-tool";

const phaseRoot = path.resolve(import.meta.dirname, "..");
const workspace = path.resolve(phaseRoot, "..", "..", "..");
const inputDir = path.join(phaseRoot, "scenario_capacity_v2");
const outputDir = path.join(phaseRoot, "static_solver_phase4b");
const docsDir = path.join(phaseRoot, "docs");
const qaDir = path.join(phaseRoot, "qa");
await fs.mkdir(outputDir, { recursive: true });

const files = {
  capacityLong: path.join(inputDir, "MEM_2040_2050_Zonal_Installed_Capacity_By_Scenario.csv"),
  capacityWorkbook: path.join(inputDir, "MEM_2040_2050_Zonal_Installed_Capacity_By_Scenario.xlsx"),
  generatorsV1: path.join(inputDir, "MEM_generators_static_candidate.csv"),
  readinessV1: path.join(inputDir, "MEM_Static_Solver_Input_Readiness.csv"),
  taxonomyV1: path.join(inputDir, "MEM_Future_Dispatchable_Technology_Taxonomy.csv"),
  crosswalkV1: path.join(inputDir, "MEM_Future_Technology_Zonal_Allocation_Crosswalk.csv"),
  oilMaterialityV1: path.join(inputDir, "MEM_Oil_Residual_Materiality_Check.csv"),
  coalMaterialityV1: path.join(inputDir, "MEM_Coal_Residual_Materiality_Check.csv"),
  phase4AGates: path.join(inputDir, "MEM_PHASE4A_GATE_STATUS.csv"),
  phase4AMethod: path.join(docsDir, "MEM_SCENARIO_CAPACITY_PACKAGE_V2_METHOD_AND_RESULTS.md"),
  phase4AVerification: path.join(qaDir, "MEM_SCENARIO_CAPACITY_PACKAGE_V2_FINAL_VERIFICATION.json"),
  anchor: path.join(phaseRoot, "scenario_capacity", "MEM_2024_Post_Coal_Oil_Thermal_Geography_Anchor.csv"),
  historicalCapacity: path.join(phaseRoot, "historical_baseline", "normalized", "MEM_Historical_Capacity_By_Zone_Technology.csv"),
  sourceManifest: path.join(phaseRoot, "SOURCE_MANIFEST.csv"),
  derivationManifest: path.join(phaseRoot, "DERIVATION_MANIFEST.csv"),
  decisionRegister: path.join(docsDir, "MEM_v2.9_THERMAL_STACK_DECISION_REGISTER_20260901.csv"),
  gapRegister: path.join(docsDir, "MEM_v2.9_EMPIRICAL_GAP_AND_ACQUISITION_REGISTER_UPDATED_20260901.csv"),
  v29: path.join(workspace, "outputs", "01a0595d-8cf1-7202-8ca1-1d3ab732e98e", "Fase_5_PyPSA_Eur_Italia_2040_2050_Model_Ready_v2.9_MEM_ARCHITECTURE_REFINED.xlsx"),
};

const out = {
  fuel2040: path.join(outputDir, "MEM_2040_Fuel_Carrier_Decomposition.csv"),
  bio2040: path.join(outputDir, "MEM_2040_Bioenergy_Capacity_Suballocation.csv"),
  chp: path.join(outputDir, "MEM_Future_CHP_Subband_Allocation.csv"),
  chpDecision: path.join(outputDir, "MEM_Future_CHP_Solver_Representation_Decision.md"),
  h2Decision: path.join(outputDir, "MEM_2050_Hydrogen_Baseline_Decision.csv"),
  energySoft: path.join(outputDir, "MEM_Source_Scenario_Energy_Soft_Controls.csv"),
  parameters: path.join(outputDir, "MEM_Technology_Static_Parameters_2040_2050.csv"),
  marginalCosts: path.join(outputDir, "MEM_Marginal_Cost_Derivation.csv"),
  availability: path.join(outputDir, "MEM_Generator_Availability_Assumptions.csv"),
  carriers: path.join(outputDir, "MEM_carriers_candidate.csv"),
  generators: path.join(outputDir, "MEM_generators_static_candidate_v2.csv"),
  storage: path.join(outputDir, "MEM_storage_static_candidate.csv"),
  readiness: path.join(outputDir, "MEM_Static_Solver_Input_Readiness.csv"),
  crosswalk2040: path.join(outputDir, "MEM_2040_Thermal_Solver_Carrier_Crosswalk.csv"),
  architecture2050: path.join(outputDir, "MEM_2050_Fuel_Carrier_Architecture.csv"),
  decomposition2050: path.join(outputDir, "MEM_2050_Gas_Other_Fossil_Solver_Decomposition.csv"),
  taxonomy: path.join(outputDir, "MEM_Future_Dispatchable_Technology_Taxonomy.csv"),
  crosswalk: path.join(outputDir, "MEM_Future_Technology_Zonal_Allocation_Crosswalk.csv"),
  oil: path.join(outputDir, "MEM_Oil_Residual_Materiality_Check.csv"),
  coal: path.join(outputDir, "MEM_Coal_Residual_Materiality_Check.csv"),
  sourceManifest: path.join(outputDir, "SOURCE_MANIFEST_PHASE4B.csv"),
  derivationManifest: path.join(outputDir, "DERIVATION_MANIFEST_PHASE4B.csv"),
  decisionRegister: path.join(outputDir, "MEM_v2.9_THERMAL_STACK_DECISION_REGISTER_UPDATED_PHASE4B_20260902.csv"),
  gapRegister: path.join(outputDir, "MEM_v2.9_EMPIRICAL_GAP_AND_ACQUISITION_REGISTER_UPDATED_PHASE4B_20260902.csv"),
  methodology: path.join(outputDir, "MEM_PHASE4B_STATIC_DISPATCH_ECONOMICS_AND_SOLVER_CARRIER_METHOD.md"),
  qa: path.join(outputDir, "MEM_PHASE4B_STATIC_SOLVER_QA.csv"),
  gates: path.join(outputDir, "MEM_PHASE4B_GATE_STATUS.csv"),
  verification: path.join(outputDir, "MEM_PHASE4B_FINAL_VERIFICATION.json"),
};

const ZONES = ["NORD", "CNOR", "CSUD", "SUD", "CALA", "SICI", "SARD"];
const SCENARIOS = ["Slow", "Base", "High"];
const PASS_THROUGH = new Set(["SOLAR_PV_ROOFTOP", "SOLAR_PV_UTILITY", "WIND_ONSHORE", "WIND_OFFSHORE", "HYDRO_RUN_OF_RIVER"]);
const DISPATCH_2040 = new Set(["CCGT", "GT_OCGT", "INTERNAL_COMBUSTION", "STEAM_OTHER_SURVIVING", "OTHER_SURVIVING_THERMAL", "GEOTHERMAL"]);
const DISPATCH_2050 = new Set(["BIOENERGY", "BIOENERGY_CCS", "GAS_CCS", "GAS_OTHER_FOSSIL", "GEOTHERMAL", "NUCLEAR"]);
const BIO_COMPATIBLE_2040 = new Set(["INTERNAL_COMBUSTION", "STEAM_OTHER_SURVIVING", "OTHER_SURVIVING_THERMAL"]);
const ROUND = (value, digits = 12) => Number(Number(value).toFixed(digits));
const SUM = (values) => values.reduce((acc, value) => acc + Number(value || 0), 0);
const KEY = (...parts) => parts.join("|");
const ALMOST = (a, b, tolerance = 1e-6) => Math.abs(Number(a) - Number(b)) <= tolerance;
const sha256 = async (file) => crypto.createHash("sha256").update(await fs.readFile(file)).digest("hex");
const csvEscape = (value) => {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
};
const colLetters = (count) => {
  let n = count;
  let result = "";
  while (n > 0) { n -= 1; result = String.fromCharCode(65 + (n % 26)) + result; n = Math.floor(n / 26); }
  return result;
};

async function readCsv(file, sheetName) {
  const wb = await Workbook.fromCSV(await fs.readFile(file, "utf8"), { sheetName });
  const values = wb.worksheets.getItem(sheetName).getUsedRange().values;
  const headers = values[0].map((value) => String(value));
  return values.slice(1).filter((row) => row.some((value) => value !== null && value !== ""))
    .map((row) => Object.fromEntries(headers.map((header, index) => [header, row[index] ?? ""])));
}

function matrix(rows) {
  const headers = [];
  for (const row of rows) for (const field of Object.keys(row)) if (!headers.includes(field)) headers.push(field);
  return { headers, values: rows.map((row) => headers.map((field) => row[field] ?? "")) };
}

async function authorCsv(rows, sheetName, file) {
  if (!rows.length) throw new Error(`Refusing to author empty ${path.basename(file)}`);
  const { headers, values } = matrix(rows);
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add(sheetName);
  sheet.getRangeByIndexes(0, 0, rows.length + 1, headers.length).values = [headers, ...values];
  const address = `A1:${colLetters(headers.length)}${rows.length + 1}`;
  await workbook.inspect({ kind: "table", sheetId: sheetName, range: address, tableMaxRows: 3, tableMaxCols: Math.min(headers.length, 18), maxChars: 5000 });
  const csv = `${[headers, ...values].map((row) => row.map(csvEscape).join(",")).join("\r\n")}\r\n`;
  await fs.writeFile(file, csv, "utf8");
  const reopened = await Workbook.fromCSV(await fs.readFile(file, "utf8"), { sheetName });
  await reopened.inspect({ kind: "table", sheetId: sheetName, range: address, tableMaxRows: 3, tableMaxCols: Math.min(headers.length, 18), maxChars: 5000 });
  return { file: path.relative(phaseRoot, file).replaceAll("\\", "/"), rows: rows.length, columns: headers.length, sha256: await sha256(file) };
}

function groupSum(rows, groupKey, valueField) {
  const result = new Map();
  for (const row of rows) result.set(groupKey(row), (result.get(groupKey(row)) || 0) + Number(row[valueField] || 0));
  return result;
}

function recursiveReplace(value, from, to) {
  if (typeof value === "string") return value.replaceAll(from, to);
  if (Array.isArray(value)) return value.map((item) => recursiveReplace(item, from, to));
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, recursiveReplace(item, from, to)]));
  return value;
}

const inputHashes = {
  capacity_workbook: await sha256(files.capacityWorkbook),
  capacity_csv: await sha256(files.capacityLong),
};
if (inputHashes.capacity_workbook !== "3ee9cf066fd16eaaee9a11d60a27998b193e253bda9926c48e51077b8c9ef914") throw new Error("Frozen V2 workbook hash changed.");
if (inputHashes.capacity_csv !== "5e06e427edda922125fe1e37ab8f57ce98b823ffeb7b48ab0b209933c5778195") throw new Error("Frozen V2 CSV hash changed.");

const capacityLong = await readCsv(files.capacityLong, "CapacityV2");
const generatorsV1 = await readCsv(files.generatorsV1, "GeneratorsV1");
const readinessV1 = await readCsv(files.readinessV1, "ReadinessV1");
const taxonomyV1 = await readCsv(files.taxonomyV1, "TaxonomyV1");
const crosswalkV1 = await readCsv(files.crosswalkV1, "CrosswalkV1");
const oilV1 = await readCsv(files.oilMaterialityV1, "OilV1");
const coalV1 = await readCsv(files.coalMaterialityV1, "CoalV1");
const phase4AGates = await readCsv(files.phase4AGates, "GatesV1");
const anchorRows = await readCsv(files.anchor, "Anchor");
const historicalCapacity = await readCsv(files.historicalCapacity, "HistoricalCapacity");
const sourceManifestInput = await readCsv(files.sourceManifest, "SourceManifest");
const derivationManifestInput = await readCsv(files.derivationManifest, "DerivationManifest");
const decisionRegisterInput = await readCsv(files.decisionRegister, "DecisionRegister");
const gapRegisterInput = await readCsv(files.gapRegister, "GapRegister");

if (capacityLong.length !== 756) throw new Error(`Frozen V2 capacity row count changed: ${capacityLong.length}`);
if (generatorsV1.length !== 408) throw new Error(`Phase 4A generator row count changed: ${generatorsV1.length}`);
if (readinessV1.length !== 672) throw new Error(`Phase 4A readiness row count changed: ${readinessV1.length}`);

const parentRows = generatorsV1.map((row) => ({ ...row, year: Number(row.year), p_nom_MW: Number(row.p_nom_MW) }));
const parentMap = new Map(parentRows.map((row) => [KEY(row.year, row.scenario, row.zone, row.technology), row]));
const parentKey = (row) => KEY(Number(row.year), row.scenario, row.zone, row.parent_capacity_technology || row.technology);

// ---------------------------------------------------------------------------
// 2040 bioenergy fuel suballocation inside the frozen parent conversion bands.
// ---------------------------------------------------------------------------
const anchorTotal = SUM(anchorRows.filter((row) => ZONES.includes(row.market_zone)).map((row) => row.surviving_post_coal_oil_capacity_NET_MW));
const scale2040 = 55000 / anchorTotal;
const bioFuelControl2024 = 3586.0;
const bioRenewableControl2024 = 3800.0923;
const bio2040National = bioFuelControl2024 * scale2040;
const hist2024 = historicalCapacity.filter((row) => Number(row.year) === 2024);
const histCap = new Map(hist2024.map((row) => [KEY(row.market_zone, row.technology), Number(row.capacity_NET_MW || 0)]));
const bioGeo = new Map(ZONES.map((zone) => [zone, Number(histCap.get(KEY(zone, "BIOENERGY")) || 0) / bioRenewableControl2024]));
if (!ALMOST(SUM([...bioGeo.values()]), 1, 1e-9)) throw new Error("Bioenergy geography does not sum to one.");

const bioBaseAllocation = new Map();
for (const zone of ZONES) {
  const zoneTarget = bio2040National * bioGeo.get(zone);
  const compatible = [...BIO_COMPATIBLE_2040].map((technology) => ({
    technology,
    capacity: Number(parentMap.get(KEY(2040, "Base", zone, technology))?.p_nom_MW || 0),
  }));
  const denominator = SUM(compatible.map((item) => item.capacity));
  if (!(denominator > 0) || zoneTarget > denominator + 1e-6) throw new Error(`Bioenergy allocation infeasible in ${zone}.`);
  for (const item of compatible) bioBaseAllocation.set(KEY(zone, item.technology), zoneTarget * item.capacity / denominator);
}

const bio2040Rows = [];
const fuel2040Rows = [];
const fuel2040Intermediate = [];
for (const row of parentRows.filter((item) => item.year === 2040 && DISPATCH_2040.has(item.technology))) {
  if (row.technology === "GEOTHERMAL") {
    fuel2040Rows.push({
      year: 2040, scenario: row.scenario, zone: row.zone, parent_conversion_technology: row.technology,
      parent_p_nom_MW: ROUND(row.p_nom_MW), solver_fuel: "GEOTHERMAL", solver_carrier: "geothermal",
      solver_subband_p_nom_MW: ROUND(row.p_nom_MW), accounting_role: "PARENT_PASS_THROUGH_ADDITIVE",
      allocation_basis: "FROZEN_V2_GEOTHERMAL_PARENT", evidence_class: "TERNA CONTROL + PROJECT SCENARIO DERIVATION", status: "STATIC_FUEL_DECOMPOSITION_READY",
    });
    fuel2040Intermediate.push({ ...row, parent_capacity_technology: row.technology, fuel: "GEOTHERMAL", carrier: "geothermal", CCS_flag: false, p_nom_MW: row.p_nom_MW, fuel_allocation_basis: "PARENT_PASS_THROUGH" });
    continue;
  }
  const bioMW = BIO_COMPATIBLE_2040.has(row.technology) ? Number(bioBaseAllocation.get(KEY(row.zone, row.technology)) || 0) : 0;
  const methaneMW = row.p_nom_MW - bioMW;
  if (methaneMW < -1e-7) throw new Error(`Negative methane residual: ${parentKey(row)}`);
  bio2040Rows.push({
    year: 2040, scenario: row.scenario, zone: row.zone, parent_conversion_technology: row.technology,
    parent_p_nom_MW: ROUND(row.p_nom_MW), bioenergy_subband_MW: ROUND(bioMW), methane_residual_MW: ROUND(methaneMW),
    bioenergy_national_control_2024_MW: bioFuelControl2024, "2040_envelope_scaling_factor": ROUND(scale2040),
    "2040_bioenergy_subband_national_MW": ROUND(bio2040National),
    bioenergy_allocation_basis: BIO_COMPATIBLE_2040.has(row.technology)
      ? "2024 TERNA FUEL-USE CONTROL × 55GW/POST-COAL-OIL-ANCHOR; renewable-source zonal shares; within-zone compatible-parent shares"
      : "INCOMPATIBLE PARENT — ZERO BIOENERGY",
    source: "TERNA_2024_FUEL_CONTROL|TERNA_RENEWABLE_SOURCE_BIOENERGY_2024|SCENARIO_CAPACITY_PACKAGE_V2",
    evidence_class: BIO_COMPATIBLE_2040.has(row.technology) ? "PROJECT DERIVATION / FUEL SUB-DECOMPOSITION" : "QA / ZERO",
    status: row.scenario === "Slow" && bioMW > 0 ? "FIXED_AT_BASE_IN_SLOW" : "STATIC_FUEL_DECOMPOSITION_READY",
  });
  const components = [
    { fuel: "METHANE", carrier: `methane_${row.technology.toLowerCase()}`, mw: Math.max(0, methaneMW), role: "PARENT_RESIDUAL_ADDITIVE" },
    { fuel: "BIOENERGY", carrier: `bioenergy_${row.technology.toLowerCase()}`, mw: bioMW, role: "SUBDECOMPOSITION_ADDITIVE" },
  ].filter((item) => item.mw > 1e-10);
  for (const item of components) {
    fuel2040Rows.push({
      year: 2040, scenario: row.scenario, zone: row.zone, parent_conversion_technology: row.technology,
      parent_p_nom_MW: ROUND(row.p_nom_MW), solver_fuel: item.fuel, solver_carrier: item.carrier,
      solver_subband_p_nom_MW: ROUND(item.mw), accounting_role: item.role,
      allocation_basis: item.fuel === "BIOENERGY" ? "PROJECT_BIOENERGY_FUEL_SUBALLOCATION" : "PARENT_MINUS_BIOENERGY_SUBBAND",
      evidence_class: item.fuel === "BIOENERGY" ? "PROJECT DERIVATION" : "FROZEN FUTURE FUEL CONTRACT",
      status: "STATIC_FUEL_DECOMPOSITION_READY",
    });
    fuel2040Intermediate.push({ ...row, parent_capacity_technology: row.technology, fuel: item.fuel, carrier: item.carrier, CCS_flag: false, p_nom_MW: item.mw, fuel_allocation_basis: item.fuel === "BIOENERGY" ? "PROJECT_BIOENERGY_FUEL_SUBALLOCATION" : "METHANE_RESIDUAL" });
  }
}

// ---------------------------------------------------------------------------
// Observed 2024 CHP shares and future sub-band construction.
// ---------------------------------------------------------------------------
const cap = (zone, technology) => Number(histCap.get(KEY(zone, technology)) || 0);
const anchorCap = (zone, technology) => Number(anchorRows.find((row) => row.market_zone === zone && row.future_technology === technology)?.surviving_post_coal_oil_capacity_NET_MW || 0);
const shareDefinitions = new Map();
const nationalShareParts = new Map();

function rawShareParts(kind, zone) {
  if (kind === "CCGT") return { chp: cap(zone, "CCGT_CHP"), total: cap(zone, "CCGT_CHP") + cap(zone, "CCGT_NON_CHP") };
  if (kind === "GT_OCGT") return { chp: cap(zone, "GT_CHP"), total: cap(zone, "GT_CHP") + cap(zone, "GT_NON_CHP") };
  if (kind === "INTERNAL_COMBUSTION") return { chp: cap(zone, "INTERNAL_COMBUSTION_CHP"), total: cap(zone, "INTERNAL_COMBUSTION_CHP") + cap(zone, "INTERNAL_COMBUSTION_NON_CHP") };
  if (kind === "STEAM_OTHER_SURVIVING") return { chp: cap(zone, "STEAM_EXTRACTION_CHP") + cap(zone, "STEAM_BACKPRESSURE_CHP"), total: anchorCap(zone, "STEAM_OTHER_SURVIVING") };
  if (kind === "OTHER_SURVIVING_THERMAL") return {
    chp: cap(zone, "FUEL_CELL_CHP"),
    total: cap(zone, "FUEL_CELL_CHP") + cap(zone, "FUEL_CELL_NON_CHP") + cap(zone, "EXTERNAL_COMBUSTION") + cap(zone, "TURBO_EXPANSION") + cap(zone, "OTHER_THERMAL"),
  };
  if (kind === "BIOENERGY_PROXY") return {
    chp: cap(zone, "INTERNAL_COMBUSTION_CHP") + cap(zone, "STEAM_EXTRACTION_CHP") + cap(zone, "STEAM_BACKPRESSURE_CHP") + cap(zone, "FUEL_CELL_CHP"),
    total: cap(zone, "INTERNAL_COMBUSTION_CHP") + cap(zone, "INTERNAL_COMBUSTION_NON_CHP") + cap(zone, "STEAM_EXTRACTION_CHP") + cap(zone, "STEAM_BACKPRESSURE_CHP") + cap(zone, "STEAM_CONDENSING") + cap(zone, "FUEL_CELL_CHP") + cap(zone, "FUEL_CELL_NON_CHP") + cap(zone, "EXTERNAL_COMBUSTION") + cap(zone, "TURBO_EXPANSION") + cap(zone, "OTHER_THERMAL"),
  };
  return { chp: 0, total: 0 };
}

for (const kind of ["CCGT", "GT_OCGT", "INTERNAL_COMBUSTION", "STEAM_OTHER_SURVIVING", "OTHER_SURVIVING_THERMAL", "BIOENERGY_PROXY"]) {
  const parts = ZONES.map((zone) => rawShareParts(kind, zone));
  nationalShareParts.set(kind, { chp: SUM(parts.map((item) => item.chp)), total: SUM(parts.map((item) => item.total)) });
  for (const zone of ZONES) {
    const local = rawShareParts(kind, zone);
    const national = nationalShareParts.get(kind);
    const robust = local.total >= (kind === "OTHER_SURVIVING_THERMAL" ? 1 : 10);
    const raw = robust && local.total > 0 ? local.chp / local.total : (national.total > 0 ? national.chp / national.total : 0);
    shareDefinitions.set(KEY(kind, zone), {
      share: Math.max(0, Math.min(1, raw)),
      localChp: local.chp,
      localTotal: local.total,
      basis: robust ? "2024_ZONE×CONVERSION_TECHNOLOGY_OBSERVED_SHARE" : "2024_NATIONAL_CONVERSION_TECHNOLOGY_FALLBACK",
    });
  }
}

const futureFuelIntermediate = [...fuel2040Intermediate];
const h2DecisionRows = [];
const gasOtherDecompositionRows = [];
for (const row of parentRows.filter((item) => item.year === 2050 && DISPATCH_2050.has(item.technology))) {
  let components = [];
  if (row.technology === "BIOENERGY") components = [{ fuel: "BIOENERGY", carrier: "bioenergy", ccs: false, mw: row.p_nom_MW }];
  if (row.technology === "BIOENERGY_CCS") components = [{ fuel: "BIOENERGY", carrier: "bioenergy_ccs", ccs: true, mw: row.p_nom_MW }];
  if (row.technology === "GAS_CCS") components = [{ fuel: "METHANE", carrier: "methane_ccs", ccs: true, mw: row.p_nom_MW }];
  if (row.technology === "GAS_OTHER_FOSSIL") {
    components = [
      { fuel: "METHANE", carrier: "methane_gt_ocgt", ccs: false, mw: row.p_nom_MW * 0.5 },
      { fuel: "HYDROGEN", carrier: "hydrogen_gt_ocgt", ccs: false, mw: row.p_nom_MW * 0.5 },
    ];
    h2DecisionRows.push({
      year: 2050, scenario: row.scenario, zone: row.zone, source_category: "GAS_OTHER_FOSSIL",
      parent_capacity_MW: ROUND(row.p_nom_MW), BASELINE_METHANE_SHARE: 0.5, BASELINE_H2_SHARE: 0.5,
      baseline_methane_capacity_MW: ROUND(row.p_nom_MW * 0.5), baseline_hydrogen_capacity_MW: ROUND(row.p_nom_MW * 0.5),
      capacity_addition_MW: 0, decision_status: "USER_FROZEN_PROJECT_ASSUMPTION",
      supersedes: "0% H2 BASELINE + 25%/50% H2 SENSITIVITIES", guardrail: "Hydrogen is substitutional within GAS_OTHER_FOSSIL; no hydrogen CCS.",
    });
    for (const share of [0.5]) for (const component of components) gasOtherDecompositionRows.push({
      year: 2050, scenario: row.scenario, grain: "MARKET_ZONE_BASELINE", zone: row.zone,
      decomposition_case: "BASELINE_50PCT_H2_USER_FROZEN", selected_static_candidate: true,
      source_category: "GAS_OTHER_FOSSIL", source_category_capacity_MW: ROUND(row.p_nom_MW),
      solver_fuel: component.fuel, solver_conversion_technology: "GT_OCGT_FUEL_FLEXIBLE",
      share_candidate: share, capacity_candidate_MW: ROUND(component.mw), energy_role: "ENDOGENOUS_DISPATCH; SOURCE TWH IS SOFT CONTROL ONLY",
      CO2_treatment: component.fuel === "METHANE" ? "FOSSIL_METHANE_DIRECT_CO2" : "ZERO_DIRECT_STACK_CO2; UPSTREAM OUTSIDE DIRECT STACK",
      evidence: "USER-FROZEN 50/50 PROJECT ASSUMPTION", assumption_status: "BASELINE_FROZEN", sensitivity_required: true, capacity_addition: false,
    });
  }
  if (row.technology === "GEOTHERMAL") components = [{ fuel: "GEOTHERMAL", carrier: "geothermal", ccs: false, mw: row.p_nom_MW }];
  if (row.technology === "NUCLEAR") components = [{ fuel: "NUCLEAR_FUEL", carrier: "nuclear", ccs: false, mw: row.p_nom_MW }];
  for (const component of components) futureFuelIntermediate.push({
    ...row, parent_capacity_technology: row.technology, fuel: component.fuel, carrier: component.carrier,
    CCS_flag: component.ccs, p_nom_MW: component.mw,
    fuel_allocation_basis: row.technology === "GAS_OTHER_FOSSIL" ? "USER_FROZEN_50_50_METHANE_HYDROGEN" : "PARENT_FUEL_CONTRACT",
  });
}

for (const scenario of SCENARIOS) {
  const nationalParent = SUM(parentRows.filter((row) => row.year === 2050 && row.scenario === scenario && row.technology === "GAS_OTHER_FOSSIL").map((row) => row.p_nom_MW));
  for (const h2Share of [0, 0.25, 0.75, 1]) {
    for (const fuel of ["METHANE", "HYDROGEN"]) {
      const share = fuel === "HYDROGEN" ? h2Share : 1 - h2Share;
      gasOtherDecompositionRows.push({
        year: 2050, scenario, grain: "ITALY_OPTIONAL_RUNTIME_SENSITIVITY", zone: "ITALY",
        decomposition_case: `OPTIONAL_H2_SHARE_${Math.round(h2Share * 100)}PCT`, selected_static_candidate: false,
        source_category: "GAS_OTHER_FOSSIL", source_category_capacity_MW: ROUND(nationalParent), solver_fuel: fuel,
        solver_conversion_technology: "GT_OCGT_FUEL_FLEXIBLE", share_candidate: share, capacity_candidate_MW: ROUND(nationalParent * share),
        energy_role: "OPTIONAL RUNTIME FUEL-SPLIT SENSITIVITY", CO2_treatment: fuel === "METHANE" ? "FOSSIL_METHANE_DIRECT_CO2" : "ZERO_DIRECT_STACK_CO2",
        evidence: "PROJECT SENSITIVITY", assumption_status: "NOT_BASELINE", sensitivity_required: false, capacity_addition: false,
      });
    }
  }
}

function chpKind(year, parentTechnology) {
  if (year === 2040) return parentTechnology === "GEOTHERMAL" ? null : parentTechnology;
  if (["BIOENERGY", "BIOENERGY_CCS"].includes(parentTechnology)) return "BIOENERGY_PROXY";
  if (parentTechnology === "GAS_CCS") return "CCGT";
  if (parentTechnology === "GAS_OTHER_FOSSIL") return "GT_OCGT";
  return null;
}

function solverSubtechnology(item, chpFlag) {
  const suffix = chpFlag ? "CHP" : "NON_CHP";
  const parent = item.parent_capacity_technology;
  if (item.year === 2040) {
    if (parent === "GEOTHERMAL") return "GEOTHERMAL";
    return `${item.fuel}_${parent}_${suffix}`;
  }
  if (parent === "BIOENERGY") return `BIOENERGY_${suffix}`;
  if (parent === "BIOENERGY_CCS") return `BIOENERGY_CCS_${suffix}`;
  if (parent === "GAS_CCS") return `METHANE_CCS_CCGT_${suffix}`;
  if (parent === "GAS_OTHER_FOSSIL") return `${item.fuel}_GT_OCGT_${suffix}`;
  if (parent === "GEOTHERMAL") return "GEOTHERMAL";
  if (parent === "NUCLEAR") return "NUCLEAR";
  return `${item.fuel}_${parent}_${suffix}`;
}

const chpAllocationRows = [];
const splitGeneratorRows = [];
for (const item of futureFuelIntermediate) {
  const kind = chpKind(item.year, item.parent_capacity_technology);
  if (!kind) {
    splitGeneratorRows.push({
      ...item, solver_subtechnology: solverSubtechnology(item, false), CHP_flag: false,
      chp_share_used: 0, chp_share_basis: "NOT_APPLICABLE", p_nom_MW: item.p_nom_MW,
    });
    continue;
  }
  const definition = shareDefinitions.get(KEY(kind, item.zone));
  if (!definition) throw new Error(`Missing CHP share ${kind} ${item.zone}`);
  for (const chpFlag of [true, false]) {
    const share = chpFlag ? definition.share : 1 - definition.share;
    const splitMW = item.p_nom_MW * share;
    const subtechnology = solverSubtechnology(item, chpFlag);
    chpAllocationRows.push({
      year: item.year, scenario: item.scenario, zone: item.zone, parent_capacity_technology: item.parent_capacity_technology,
      parent_zone_p_nom_MW: ROUND(parentMap.get(KEY(item.year, item.scenario, item.zone, item.parent_capacity_technology))?.p_nom_MW || 0),
      fuel: item.fuel, carrier: item.carrier, fuel_subband_p_nom_MW: ROUND(item.p_nom_MW), CHP_flag: chpFlag,
      observed_chp_capacity_MW: ROUND(definition.localChp), observed_reference_capacity_MW: ROUND(definition.localTotal),
      chp_share_used: ROUND(definition.share), chp_share_basis: definition.basis,
      solver_subtechnology: subtechnology, solver_p_nom_MW: ROUND(splitMW),
      geography_changed: false, heat_system_coupled: false, must_run: false,
      evidence_class: item.CCS_flag || item.year === 2050 ? "PROJECT DISPATCH SUBBAND PROXY" : "PROJECT DERIVATION FROM TERNA 2024 CHP CAPACITY",
      status: "ELECTRICITY_ONLY_CHP_SPECIFIC_BAND_READY",
    });
    if (splitMW > 1e-10) splitGeneratorRows.push({
      ...item, solver_subtechnology: subtechnology, CHP_flag: chpFlag,
      chp_share_used: definition.share, chp_share_basis: definition.basis, p_nom_MW: splitMW,
    });
  }
}

const passThroughRows = parentRows.filter((row) => PASS_THROUGH.has(row.technology)).map((row) => ({
  ...row, parent_capacity_technology: row.technology, solver_subtechnology: row.technology, CHP_flag: false,
  fuel: row.technology === "HYDRO_RUN_OF_RIVER" ? "WATER_INFLOW" : "NONE",
  carrier: row.carrier, CCS_flag: false, chp_share_used: 0, chp_share_basis: "NOT_APPLICABLE",
  fuel_allocation_basis: "PARENT_PASS_THROUGH",
}));
const solverCapacityRows = [...passThroughRows, ...splitGeneratorRows];

// ---------------------------------------------------------------------------
// Static technical/economic parameters and marginal costs.
// ---------------------------------------------------------------------------
const methanePrice = { 2040: 38.0563456, 2050: 22.7578 };
const co2Price = { 2040: 104.5504, 2050: 400 };
const methaneCO2 = 0.198;
const bioCO2 = 0.3667;
const bioFuelPrice = 9.3506;
const hydrogenPrice2050 = 42.0;
const geothermalPhysicalCO2 = 1996060 / (25023 * 0.6032 * 1000);
const zoneOutsideMaintenance = { NORD: 0.78, CNOR: 0.69, CSUD: 0.76, SUD: 0.77, CALA: 0.83, SICI: 0.75, SARD: 0.74 };

function availabilityFor(item) {
  const tech = item.parent_capacity_technology;
  if (/SOLAR|WIND/.test(tech)) return { availabilityClass: `PROFILE_${tech}`, parameter: "", definition: "Hourly weather profile required in Phase 4C", source: "PHASE4C_PROFILE_GATE", evidence: "RUNTIME INPUT REQUIRED", status: "PARTIAL" };
  if (tech === "HYDRO_RUN_OF_RIVER") return { availabilityClass: "PROFILE_HYDRO_ROR_INFLOW", parameter: "", definition: "Hourly inflow-limited profile; annual energy reconciles to Terna", source: "HYDRO_INFLOW_GATE", evidence: "RUNTIME INPUT REQUIRED", status: "PARTIAL" };
  if (tech === "NUCLEAR") return { availabilityClass: "NUCLEAR_2050_CENTRAL_REFUELING_PROFILE", parameter: 0.9245, definition: "8×1GW; four staggered 33-day outages; 24-month cycle; residual factor 0.96827116", source: "BLK_007_NUCLEAR_OPERATIONS", evidence: "MODEL READY PROFILE EXISTS", status: "MODEL_READY" };
  if (tech === "GEOTHERMAL") return { availabilityClass: "GEOTHERMAL_CONSTANT_0P90", parameter: 0.9, definition: "Constant technical availability; dispatch remains endogenous", source: "PROJECT_STATIC_ASSUMPTION", evidence: "PROJECT ASSUMPTION", status: "MODEL_READY_WITH_PROJECT_ASSUMPTION" };
  let maintenanceHours;
  let family;
  if (tech === "CCGT" || tech === "GAS_CCS") { maintenanceHours = 1353; family = "CCGT"; }
  else if (tech === "GT_OCGT" || tech === "GAS_OTHER_FOSSIL") { maintenanceHours = 857; family = "GT_OCGT"; }
  else { maintenanceHours = 1344; family = "TRADITIONAL_THERMAL"; }
  const outside = item.year === 2040 ? zoneOutsideMaintenance[item.zone] : 0.9;
  const annualEquivalent = outside * (1 - maintenanceHours / 8760);
  return {
    availabilityClass: item.year === 2040 ? `TERNA_MAINTENANCE_PLUS_ZONE_DERATING_${family}_${item.zone}` : `NEW_BUILD_MAINTENANCE_PLUS_10PCT_DERATING_${family}`,
    parameter: annualEquivalent,
    definition: `Annual-equivalent fallback = ${outside} outside-maintenance p_max_pu × (1 - ${maintenanceHours}/8760); Phase 4C should construct staggered outages`,
    source: "BLK_005_THERMAL_AVAILABILITY", evidence: item.year === 2040 ? "TERNA CONTROL + PROJECT PROFILE CLASS" : "TERNA NEW-BUILD DERATING + PROJECT PROFILE CLASS", status: "STATIC_EQUIVALENT_READY; HOURLY_SCHEDULE_PENDING",
  };
}

function baseParameter(item) {
  const year = Number(item.year);
  const sub = item.solver_subtechnology;
  const isChp = Boolean(item.CHP_flag);
  const base = {
    efficiency: "", efficiencyBasis: "NOT_APPLICABLE", vom: 0, fuelPrice: 0, fuelSource: "NOT_APPLICABLE",
    directCO2th: 0, chargeableCO2th: 0, captureRate: 0, residualCO2th: 0, physicalCO2el: 0,
    otherVariable: 0, carbonTreatment: "ZERO_DIRECT_OPERATIONAL_CO2", source: "PROJECT STATIC CONVENTION", evidence: "PROJECT IMPLEMENTATION MAPPING", status: "MODEL_READY",
  };
  if (/SOLAR|WIND/.test(sub)) return { ...base, source: "SCENARIO_CAPACITY_PACKAGE_V2", evidence: "READY_NA / READY_ZERO", status: "STATIC_READY; HOURLY_PROFILE_PENDING" };
  if (sub === "HYDRO_RUN_OF_RIVER") return { ...base, efficiency: 0.9, efficiencyBasis: "TURBINE ELECTRIC EFFICIENCY CANDIDATE", source: "PYPSA_TECHNOLOGY_DATA_V0_15_HYDRO", evidence: "PROJECT ASSUMPTION", status: "STATIC_READY; HOURLY_INFLOW_PENDING" };
  if (sub === "GEOTHERMAL") return {
    ...base, efficiency: 1, efficiencyBasis: "DIRECT-ELECTRIC ACCOUNTING CONVENTION; NOT A THERMODYNAMIC HEAT EFFICIENCY",
    vom: year === 2040 ? 6.0905 : 5.8023, physicalCO2el: geothermalPhysicalCO2,
    directCO2th: "NOT_APPLICABLE", carbonTreatment: "PHYSICAL GEOTHERMAL CO2 REPORTED; NO ETS CHARGE IN BASELINE; POLICY SENSITIVITY REQUIRED",
    source: "PYPSA_TECHNOLOGY_DATA_V0_15_GEOTHERMAL_VOM_PROXY|ENEL_ENVIRONMENTAL_COUNTRY_OVERVIEW_2015",
    evidence: "PROJECT ASSUMPTION + OPERATOR PHYSICAL EMISSIONS", status: "MODEL_READY_WITH_PROJECT_ASSUMPTION",
  };
  if (sub === "NUCLEAR") return {
    ...base, efficiency: 0.36, efficiencyBasis: "ACCEPTED MEM 2050 NUCLEAR BRANCH",
    vom: 4.459, fuelPrice: 7.4536, fuelSource: "PYPSA_TECHNOLOGY_DATA_V0_15 / ACCEPTED MEM",
    source: "MEM_V2_9_09_TECH_COSTS|BLK_007_NUCLEAR_OPERATIONS", evidence: "ACCEPTED PROJECT PARAMETER", status: "MODEL_READY",
  };

  const isBio = item.fuel === "BIOENERGY";
  const isH2 = item.fuel === "HYDROGEN";
  const isMethane = item.fuel === "METHANE";
  let efficiency;
  let efficiencyBasis;
  let vom;
  let source;
  let evidence = "PYPSA TECHNOLOGY-DATA V0.15 / ACCEPTED MEM";
  let status = "MODEL_READY";

  if (year === 2040) {
    if (item.parent_capacity_technology === "CCGT") { efficiency = 0.59; vom = 5.4768; efficiencyBasis = "CCGT annual-average electric efficiency"; }
    else if (item.parent_capacity_technology === "GT_OCGT") { efficiency = 0.42; vom = isChp ? 5.4768 : 6.0111; efficiencyBasis = "Simple-cycle gas turbine annual-average electric efficiency"; }
    else if (item.parent_capacity_technology === "INTERNAL_COMBUSTION") {
      if (isBio) {
        efficiency = isChp ? 0.3003 : 0.468; vom = 2.8049;
        efficiencyBasis = "technology-data biomass electricity-side efficiency; parent conversion label retained only for capacity lineage";
        evidence = "PYPSA TECHNOLOGY-DATA V0.15 / PROJECT FUEL SUB-DECOMPOSITION";
      } else {
        efficiency = 0.44; vom = 6.5;
        efficiencyBasis = "Linear 2040 interpolation of DEA gas-engine annual-average electric efficiency (43% 2030; 45% 2050)";
        evidence = "OFFICIAL DEA + PROJECT INTERPOLATION";
      }
    }
    else if (item.parent_capacity_technology === "STEAM_OTHER_SURVIVING") {
      efficiency = isBio ? (isChp ? 0.3003 : 0.468) : (isChp ? 0.36 : 0.40);
      vom = isBio ? 2.8049 : (isChp ? 5.5 : 4.0);
      efficiencyBasis = isBio ? "technology-data biomass electricity-side efficiency" : "Legacy methane steam project assumption";
      if (!isBio) { evidence = "PROJECT ASSUMPTION"; status = "MODEL_READY_WITH_PROJECT_ASSUMPTION"; }
    } else if (item.parent_capacity_technology === "OTHER_SURVIVING_THERMAL") {
      efficiency = isBio ? (isChp ? 0.3003 : 0.468) : 0.35;
      vom = isBio ? 2.8049 : 6.0;
      efficiencyBasis = isBio ? "technology-data biomass proxy" : "Residual methane thermal project assumption";
      if (!isBio) { evidence = "PROJECT ASSUMPTION"; status = "MODEL_READY_WITH_PROJECT_ASSUMPTION"; }
    }
    source = "MEM_V2_9_09_TECH_COSTS|PYPSA_TECHNOLOGY_DATA_V0_15|DEA_GAS_ENGINE_CATALOGUE";
  } else {
    if (item.parent_capacity_technology === "BIOENERGY") {
      efficiency = isChp ? 0.3003 : 0.468; vom = 2.8049; efficiencyBasis = "technology-data biomass electricity-side efficiency";
      source = "PYPSA_TECHNOLOGY_DATA_V0_15";
    } else if (item.parent_capacity_technology === "BIOENERGY_CCS") {
      const uncapturedEfficiency = isChp ? 0.3003 : 0.468;
      const captureRate = 0.95;
      const captureElectricity = 0.075 + 0.02;
      const penalty = captureElectricity * bioCO2 * captureRate;
      efficiency = uncapturedEfficiency - penalty;
      vom = 2.8049;
      efficiencyBasis = `CCS-specific net electric efficiency: ${uncapturedEfficiency} minus ${(penalty).toFixed(9)} MWh_el/MWh_th capture+compression electricity; no second penalty`;
      source = "PYPSA_TECHNOLOGY_DATA_V0_15_BIOMASS_AND_CAPTURE";
    } else if (item.parent_capacity_technology === "GAS_CCS") {
      efficiency = 0.52; vom = 5.3432; efficiencyBasis = "CCS-specific net electric efficiency inclusive of capture energy penalty";
      source = "MEM_TERNA_CCGT_CAPTURE_CONTEXT|PYPSA_TECHNOLOGY_DATA_V0_15_CCGT";
      evidence = "PROJECT ASSUMPTION ANCHORED TO ACCEPTED SOURCES"; status = "MODEL_READY_WITH_PROJECT_ASSUMPTION";
    } else if (item.parent_capacity_technology === "GAS_OTHER_FOSSIL") {
      efficiency = 0.43; vom = isChp ? 5.3432 : 6.0111;
      efficiencyBasis = isH2 ? "Hydrogen-ready GT uses 2050 OCGT electric-efficiency proxy" : "2050 OCGT annual-average electric efficiency";
      source = "PYPSA_TECHNOLOGY_DATA_V0_15_OCGT";
      if (isH2) { evidence = "PROJECT FUEL-SUBSTITUTION ASSUMPTION"; status = "MODEL_READY_WITH_PROJECT_ASSUMPTION"; }
    }
  }

  const captureRate = item.CCS_flag ? (isBio ? 0.95 : 0.90) : 0;
  const direct = isMethane ? methaneCO2 : (isBio ? bioCO2 : 0);
  const residual = direct * (1 - captureRate);
  const chargeable = isMethane ? residual : 0;
  let otherVariable = 0;
  if (item.parent_capacity_technology === "GAS_CCS") otherVariable = 54 * (direct * captureRate) / efficiency;
  if (item.parent_capacity_technology === "BIOENERGY_CCS") otherVariable = 3.1433 * (direct * captureRate) / efficiency;
  const fuelPrice = isMethane ? methanePrice[year] : (isBio ? bioFuelPrice : (isH2 ? hydrogenPrice2050 : 0));
  const fuelSource = isMethane ? (year === 2040 ? "TERNA/DDS 2040 36.4 EUR2023/MWh_th normalized to EUR2025" : "ACCEPTED MEM 2050 methane assumption")
    : isBio ? "PyPSA technology-data v0.15 generic biomass fuel"
      : isH2 ? "PROJECT EUR2025 ENGINEERING ASSUMPTION anchored to TYNDP 2024 North-African import-price range" : "NOT_APPLICABLE";
  const carbonTreatment = isMethane ? (item.CCS_flag ? "EU_ETS ON RESIDUAL STACK CO2 AFTER ONE CAPTURE-RATE APPLICATION" : "EU_ETS FULL DIRECT STACK CO2")
    : isBio ? (item.CCS_flag ? "BIOGENIC CO2 CAPTURE REPORTED SEPARATELY; NO NEGATIVE CREDIT IN OBJECTIVE" : "PHYSICAL BIOGENIC CO2 REPORTED; ZERO CHARGEABLE IN BASELINE")
      : isH2 ? "ZERO DIRECT STACK CO2; UPSTREAM OUTSIDE DIRECT STACK" : "ZERO DIRECT OPERATIONAL CO2";
  return {
    ...base, efficiency, efficiencyBasis, vom, fuelPrice, fuelSource, directCO2th: direct,
    chargeableCO2th: chargeable, captureRate, residualCO2th: residual, otherVariable,
    carbonTreatment, source, evidence, status,
  };
}

const generatorRows = [];
const staticParameterRows = [];
const staticParameterSeen = new Set();
const marginalRows = [];
const marginalSeen = new Set();
const availabilityRows = [];
const availabilitySeen = new Set();
for (const item of solverCapacityRows) {
  const p = baseParameter(item);
  const availability = availabilityFor(item);
  const efficiencyNumeric = typeof p.efficiency === "number" && p.efficiency > 0;
  const fuelComponent = efficiencyNumeric ? p.fuelPrice / p.efficiency : 0;
  const carbonComponent = efficiencyNumeric ? co2Price[item.year] * Number(p.chargeableCO2th || 0) / p.efficiency : 0;
  const marginalCost = fuelComponent + p.vom + carbonComponent + p.otherVariable;
  const id = `${item.year}_${item.scenario}_${item.zone}_${item.solver_subtechnology}`.toUpperCase().replaceAll(/[^A-Z0-9_]+/g, "_");
  generatorRows.push({
    scenario: item.scenario, year: item.year, generator_id: id, zone: item.zone,
    parent_capacity_technology: item.parent_capacity_technology, solver_subtechnology: item.solver_subtechnology,
    CHP_flag: Boolean(item.CHP_flag), fuel: item.fuel, carrier: item.carrier, CCS_flag: Boolean(item.CCS_flag),
    p_nom_MW: ROUND(item.p_nom_MW), p_nom_extendable: false, efficiency: p.efficiency,
    VOM_EUR2025_per_MWh_el: ROUND(p.vom), fuel_price_EUR2025_per_MWh_th: ROUND(p.fuelPrice),
    direct_physical_CO2_t_per_MWh_th: p.directCO2th, chargeable_CO2_t_per_MWh_th: ROUND(p.chargeableCO2th),
    capture_rate: ROUND(p.captureRate), residual_CO2_t_per_MWh_th: ROUND(p.residualCO2th),
    CO2_price_EUR2025_per_t: co2Price[item.year], other_variable_cost_EUR2025_per_MWh_el: ROUND(p.otherVariable),
    marginal_cost_EUR2025_per_MWh_el: ROUND(marginalCost), availability_class: availability.availabilityClass,
    static_availability_equivalent: availability.parameter === "" ? "" : ROUND(availability.parameter),
    annual_energy_constraint_type: "NONE_HARD", source_scenario_energy_role: "SOFT_CALIBRATION_CONTROL_ONLY",
    source_capacity: "SCENARIO_CAPACITY_PACKAGE_V2", source_parameters: p.source,
    evidence_class: p.evidence, status: p.status.includes("MODEL_READY") || p.status.includes("STATIC_READY") ? p.status : "STATIC_NUMERIC_COMPLETE",
  });

  const staticKey = KEY(item.year, item.zone, item.solver_subtechnology);
  if (!staticParameterSeen.has(staticKey)) {
    staticParameterSeen.add(staticKey);
    staticParameterRows.push({
      year: item.year, scenario_applicability: "SLOW|BASE|HIGH", zone: item.zone,
      technology: item.parent_capacity_technology, solver_subtechnology: item.solver_subtechnology,
      CHP_flag: Boolean(item.CHP_flag), fuel: item.fuel, carrier: item.carrier, CCS_flag: Boolean(item.CCS_flag),
      efficiency_el: p.efficiency, efficiency_basis: p.efficiencyBasis, VOM_EUR2025_per_MWh_el: ROUND(p.vom),
      fuel_price_EUR2025_per_MWh_th: ROUND(p.fuelPrice), fuel_price_source: p.fuelSource,
      direct_CO2_t_per_MWh_th: p.directCO2th, direct_physical_CO2_t_per_MWh_el: p.physicalCO2el ? ROUND(p.physicalCO2el) : 0,
      capture_rate: ROUND(p.captureRate), residual_CO2_t_per_MWh_th: ROUND(p.residualCO2th),
      chargeable_CO2_t_per_MWh_th: ROUND(p.chargeableCO2th), CO2_price_EUR2025_per_t: co2Price[item.year],
      carbon_cost_treatment: p.carbonTreatment, other_variable_cost_EUR2025_per_MWh_el: ROUND(p.otherVariable),
      marginal_cost_EUR2025_per_MWh_el: ROUND(marginalCost), availability_parameter: availability.parameter === "" ? "" : ROUND(availability.parameter),
      availability_definition: availability.definition, source: p.source, evidence_class: p.evidence, status: p.status,
    });
  }

  const marginalKey = KEY(item.year, item.solver_subtechnology);
  if (!marginalSeen.has(marginalKey)) {
    marginalSeen.add(marginalKey);
    marginalRows.push({
      year: item.year, solver_subtechnology: item.solver_subtechnology, fuel: item.fuel, CHP_flag: Boolean(item.CHP_flag), CCS_flag: Boolean(item.CCS_flag),
      fuel_price_EUR2025_per_MWh_th: ROUND(p.fuelPrice), efficiency_el: p.efficiency,
      fuel_component_EUR2025_per_MWh_el: ROUND(fuelComponent), VOM_EUR2025_per_MWh_el: ROUND(p.vom),
      direct_CO2_t_per_MWh_th: p.directCO2th, capture_rate: ROUND(p.captureRate),
      chargeable_CO2_t_per_MWh_th: ROUND(p.chargeableCO2th), CO2_price_EUR2025_per_t: co2Price[item.year],
      carbon_component_EUR2025_per_MWh_el: ROUND(carbonComponent), other_variable_cost_EUR2025_per_MWh_el: ROUND(p.otherVariable),
      final_marginal_cost_EUR2025_per_MWh_el: ROUND(marginalCost),
      formula: efficiencyNumeric ? "fuel_price/efficiency + VOM + CO2_price*chargeable_CO2/efficiency + other_variable_cost" : "VOM + applicable direct variable terms",
      double_fuel_conversion: false, double_CCS_penalty: false, double_carbon_charge: false, automatic_BECCS_negative_credit: false,
      source: p.source, evidence_class: p.evidence, status: "DERIVATION_RECONCILED",
    });
  }

  const availKey = KEY(item.year, item.zone, availability.availabilityClass);
  if (!availabilitySeen.has(availKey)) {
    availabilitySeen.add(availKey);
    availabilityRows.push({
      year: item.year, zone: item.zone, availability_class: availability.availabilityClass,
      applicable_parent_technology: item.parent_capacity_technology,
      outside_maintenance_p_max_pu: item.year === 2040 && !PASS_THROUGH.has(item.parent_capacity_technology) && !["GEOTHERMAL"].includes(item.parent_capacity_technology) ? zoneOutsideMaintenance[item.zone] : (item.year === 2050 && !PASS_THROUGH.has(item.parent_capacity_technology) && !["GEOTHERMAL", "NUCLEAR"].includes(item.parent_capacity_technology) ? 0.9 : ""),
      planned_maintenance_hours: item.parent_capacity_technology === "CCGT" || item.parent_capacity_technology === "GAS_CCS" ? 1353 : item.parent_capacity_technology === "GT_OCGT" || item.parent_capacity_technology === "GAS_OTHER_FOSSIL" ? 857 : (![...PASS_THROUGH, "GEOTHERMAL", "NUCLEAR"].includes(item.parent_capacity_technology) ? 1344 : ""),
      static_annual_equivalent: availability.parameter === "" ? "" : ROUND(availability.parameter),
      hourly_profile_rule: availability.definition, historical_CF_used_as_availability: false,
      Terna_CDP_used_as_hourly_p_max_pu: false, slow_0p85_factor_used_as_hourly_availability: false,
      source: availability.source, evidence_class: availability.evidence, status: availability.status,
    });
  }
}

// ---------------------------------------------------------------------------
// BESS static contract from the accepted v2.9 storage table.
// ---------------------------------------------------------------------------
const v29 = await SpreadsheetFile.importXlsx(await FileBlob.load(files.v29));
const storageValues = v29.worksheets.getItem("05_STORAGE_BY_ZONE").getUsedRange().values;
const storageRows = [];
for (const raw of storageValues) {
  const zone = String(raw[0] ?? "");
  const storageClass = String(raw[1] ?? "");
  if (!ZONES.includes(zone) || !storageClass || storageClass.includes("TOTAL")) continue;
  const year = storageClass === "BESS portfolio" ? 2050 : 2040;
  for (const [scenario, indexes] of Object.entries({ Slow: [2, 3, 4], Base: [5, 6, 7], High: [8, 9, 10] })) {
    const [chargeIndex, dischargeIndex, energyIndex] = indexes;
    storageRows.push({
      scenario, year, zone,
      technology: year === 2040 ? `BESS_${storageClass.toUpperCase().replaceAll(/[^A-Z0-9]+/g, "_").replaceAll(/^_|_$/g, "")}` : "BESS_PORTFOLIO",
      storage_class_original: storageClass, charge_power_MW: ROUND(Number(raw[chargeIndex] || 0) * 1000),
      discharge_power_MW: ROUND(Number(raw[dischargeIndex] || 0) * 1000), energy_capacity_MWh: ROUND(Number(raw[energyIndex] || 0) * 1000),
      duration_h: Number(raw[11]), charge_efficiency: Number(raw[12]), discharge_efficiency: Number(raw[13]),
      roundtrip_efficiency: Number(raw[14]), standing_loss_per_hour: 0, cyclic_candidate: true,
      p_nom_extendable: false, e_nom_extendable: false, terminal_SOC_rule: "DEFERRED_TO_PHASE4C",
      source: "MEM_V2_9_05_STORAGE_BY_ZONE", evidence_class: "ACCEPTED SCENARIO INPUT + PROJECT STATIC ASSUMPTION",
      status: "STATIC_MODEL_READY; TERMINAL_SOC_PENDING",
    });
  }
}

// ---------------------------------------------------------------------------
// Carriers and source-scenario soft controls.
// ---------------------------------------------------------------------------
const carrierRows = [
  ["solar_pv_rooftop", "NONE", true, false, 0, "ZERO_DIRECT_OPERATIONAL_CO2", "VRE profile; efficiency not applicable"],
  ["solar_pv_utility", "NONE", true, false, 0, "ZERO_DIRECT_OPERATIONAL_CO2", "VRE profile; efficiency not applicable"],
  ["wind_onshore", "NONE", true, false, 0, "ZERO_DIRECT_OPERATIONAL_CO2", "VRE profile; efficiency not applicable"],
  ["wind_offshore", "NONE", true, false, 0, "ZERO_DIRECT_OPERATIONAL_CO2", "VRE profile; efficiency not applicable"],
  ["hydro_run_of_river", "WATER_INFLOW", true, false, 0, "ZERO_DIRECT_OPERATIONAL_CO2", "Inflow-limited; no fuel price"],
  ["methane_ccgt", "METHANE", false, false, methaneCO2, "EU_ETS_FULL_DIRECT", "2040 CCGT methane"],
  ["methane_gt_ocgt", "METHANE", false, false, methaneCO2, "EU_ETS_FULL_DIRECT", "2040/2050 GT methane"],
  ["methane_internal_combustion", "METHANE", false, false, methaneCO2, "EU_ETS_FULL_DIRECT", "2040 engine methane"],
  ["methane_steam_other_surviving", "METHANE", false, false, methaneCO2, "EU_ETS_FULL_DIRECT", "2040 legacy steam methane"],
  ["methane_other_surviving_thermal", "METHANE", false, false, methaneCO2, "EU_ETS_FULL_DIRECT", "2040 residual methane thermal"],
  ["bioenergy_internal_combustion", "BIOENERGY", true, false, bioCO2, "BIOGENIC_PHYSICAL_NOT_CHARGED_BASE", "2040 fuel subband"],
  ["bioenergy_steam_other_surviving", "BIOENERGY", true, false, bioCO2, "BIOGENIC_PHYSICAL_NOT_CHARGED_BASE", "2040 fuel subband"],
  ["bioenergy_other_surviving_thermal", "BIOENERGY", true, false, bioCO2, "BIOGENIC_PHYSICAL_NOT_CHARGED_BASE", "2040 fuel subband"],
  ["bioenergy", "BIOENERGY", true, false, bioCO2, "BIOGENIC_PHYSICAL_NOT_CHARGED_BASE", "2050 bioenergy"],
  ["bioenergy_ccs", "BIOENERGY", true, true, ROUND(bioCO2 * 0.05), "BIOGENIC_CAPTURE_REPORTED_NO_NEGATIVE_CREDIT", "Captured biogenic CO2 reported separately"],
  ["methane_ccs", "METHANE", false, true, ROUND(methaneCO2 * 0.10), "EU_ETS_RESIDUAL_AFTER_CCS", "Capture rate applied once"],
  ["hydrogen_gt_ocgt", "HYDROGEN", false, false, 0, "ZERO_DIRECT_STACK_CO2", "Upstream lifecycle emissions outside direct stack factor"],
  ["geothermal", "GEOTHERMAL", true, false, "NOT_APPLICABLE_THERMAL_INPUT", "PHYSICAL_CO2_REPORTED_NO_ETS_CHARGE_BASELINE", `Operator-derived physical reference ${ROUND(geothermalPhysicalCO2, 9)} tCO2/MWh_el`],
  ["nuclear", "NUCLEAR_FUEL", false, false, 0, "ZERO_DIRECT_OPERATIONAL_CO2", "Fuel contribution represented through fuel price/efficiency"],
].map(([carrier, fuel, renewable, ccs, factor, carbonClass, notes]) => ({
  carrier, fuel, renewable_flag: renewable, CCS_flag: ccs, direct_CO2_t_per_MWh_th: factor,
  carbon_pricing_class: carbonClass, notes, source: "MEM_PHASE4B_STATIC_PARAMETER_REGISTER", status: "STATIC_CARRIER_READY",
}));

const energySoftRows = [
  ["BIOENERGY", 10.6, "TWh_e"], ["BIOENERGY_CCS", 6.0, "TWh_e"], ["GAS_CCS", 4.0, "TWh_e"],
  ["GAS_OTHER_FOSSIL", 2.1, "TWh_e"], ["NUCLEAR", 64.2, "TWh_e"],
].map(([technology, value, unit]) => ({
  year: 2050, scenario_applicability: "PNIEC NUCLEAR SCENARIO; compare all MEM 2050 runs diagnostically",
  source_category: technology, source_scenario_energy: value, unit,
  evidence_class: "DIRECT SOURCE ENERGY / SOURCE SCENARIO OUTPUT", MEM_role: "SOFT_CALIBRATION_CONTROL",
  annual_energy_constraint_type: "NONE_HARD", e_sum_min: "", e_sum_max: "", fixed_annual_generation: false,
  ex_post_validation: "Compare endogenous model generation; report absolute and percentage difference",
  source: "PNIEC_2024|TERNA_2050_SCENARIO_WORK", status: "SOFT_CONTROL_FROZEN_NOT_HARD_CONSTRAINT",
}));

// ---------------------------------------------------------------------------
// Updated architecture, crosswalk, taxonomy and carrier crosswalks.
// ---------------------------------------------------------------------------
const architecture2050Rows = [
  ["METHANE", "methane_gt_ocgt", true, false, "GAS_OTHER_FOSSIL", "GT_OCGT", "50% baseline within parent", "USER_FROZEN_PROJECT_ASSUMPTION"],
  ["HYDROGEN", "hydrogen_gt_ocgt", true, false, "GAS_OTHER_FOSSIL", "GT_OCGT_HYDROGEN_READY", "50% baseline within parent", "USER_FROZEN_PROJECT_ASSUMPTION"],
  ["METHANE", "methane_ccs", true, true, "GAS_CCS", "CCGT_CCS", "100% of GAS_CCS; no hydrogen CCS", "FROZEN_PROJECT_INTERPRETATION"],
  ["BIOENERGY", "bioenergy", true, false, "BIOENERGY", "BIOENERGY_GENERATOR", "Parent split only into CHP/non-CHP", "FROZEN"],
  ["BIOENERGY", "bioenergy_ccs", true, true, "BIOENERGY_CCS", "BIOENERGY_CCS_GENERATOR", "Parent split only into CHP/non-CHP", "FROZEN"],
  ["GEOTHERMAL", "geothermal", false, false, "GEOTHERMAL", "GEOTHERMAL", "Pass-through", "FROZEN"],
  ["NUCLEAR_FUEL", "nuclear", false, false, "NUCLEAR", "NUCLEAR_REACTOR", "Pass-through", "FROZEN"],
].map(([fuel, carrier, combustion, ccs, sourceCategory, conversion, rule, status]) => ({
  year: 2050, fuel, carrier, combustion, CCS_flag: ccs, source_category: sourceCategory,
  solver_conversion_technology: conversion, baseline_capacity_rule: rule,
  direct_CO2_class: fuel === "METHANE" ? (ccs ? "RESIDUAL_METHANE_CO2_AFTER_90PCT_CAPTURE" : "METHANE_0P198_T_PER_MWH_TH") : fuel === "BIOENERGY" ? "BIOGENIC_PHYSICAL_SEPARATE_POLICY_TREATMENT" : "ZERO_DIRECT_STACK_OR_NONCOMBUSTION",
  capacity_addition: false, evidence_class: status.includes("USER") ? "USER-FROZEN PROJECT ASSUMPTION" : "PROJECT SOLVER MAPPING", status,
}));

const crosswalk2040Rows = [];
for (const scenario of SCENARIOS) {
  for (const technology of ["CCGT", "GT_OCGT", "INTERNAL_COMBUSTION", "STEAM_OTHER_SURVIVING", "OTHER_SURVIVING_THERMAL", "GEOTHERMAL"]) {
    const parentNational = SUM(parentRows.filter((row) => row.year === 2040 && row.scenario === scenario && row.technology === technology).map((row) => row.p_nom_MW));
    const decomp = fuel2040Rows.filter((row) => row.scenario === scenario && row.parent_conversion_technology === technology);
    for (const fuel of [...new Set(decomp.map((row) => row.solver_fuel))]) {
      const capacity = SUM(decomp.filter((row) => row.solver_fuel === fuel).map((row) => row.solver_subband_p_nom_MW));
      crosswalk2040Rows.push({
        year: 2040, scenario, scenario_technology: technology, conversion_technology: technology,
        fuel, carrier: fuel === "METHANE" ? `methane_${technology.toLowerCase()}` : fuel === "BIOENERGY" ? `bioenergy_${technology.toLowerCase()}` : "geothermal",
        parent_capacity_MW: ROUND(parentNational), fuel_subband_capacity_MW: ROUND(capacity),
        CHP_subband_required: !["GEOTHERMAL"].includes(technology), CO2_class: fuel === "METHANE" ? "METHANE_DIRECT_CO2" : fuel === "BIOENERGY" ? "BIOGENIC_POLICY_SEPARATE" : "GEOTHERMAL_PHYSICAL_REPORTING",
        zonal_anchor: "FROZEN_V2_PARENT_GEOGRAPHY; FUEL/CHP SPLITS OCCUR INSIDE ZONE",
        evidence_role: fuel === "BIOENERGY" ? "PROJECT FUEL SUB-DECOMPOSITION" : "USER-FROZEN FUTURE FUEL CONTRACT",
        status: "STATIC_SOLVER_CARRIER_READY",
      });
    }
  }
}

const taxonomyRows = taxonomyV1.map((row) => {
  const technology = row.future_technology;
  const isDispatchParent2040 = ["CCGT", "GT_OCGT", "INTERNAL_COMBUSTION", "STEAM_OTHER_SURVIVING", "OTHER_SURVIVING_THERMAL"].includes(technology);
  const isDispatchParent2050 = ["BIOENERGY", "BIOENERGY_CCS", "GAS_CCS", "GAS_OTHER_FOSSIL"].includes(technology);
  return {
    ...row,
    phase4b_parent_capacity_control: isDispatchParent2040 || isDispatchParent2050,
    phase4b_additive_solver_component_2040: isDispatchParent2040 ? false : row.additive_model_component_2040,
    phase4b_additive_solver_component_2050: isDispatchParent2050 ? false : row.additive_model_component_2050,
    phase4b_solver_subbands_additive: isDispatchParent2040 || isDispatchParent2050,
    fuel_subdecomposition_required: isDispatchParent2040 || technology === "GAS_OTHER_FOSSIL",
    CHP_subband_required: isDispatchParent2040 || isDispatchParent2050,
    phase4b_rule: isDispatchParent2040 || isDispatchParent2050 ? "PARENT p_nom IS CONTROL ONLY; ADDITIVE SOLVER CAPACITY EXISTS ONLY IN EXCLUSIVE FUEL×CHP SUBBANDS" : "PASS-THROUGH OR QA ROLE",
    phase4b_status: "STATIC_SOLVER_TAXONOMY_V3",
  };
});

for (const sub of [...new Set(splitGeneratorRows.map((row) => row.solver_subtechnology))].sort()) {
  const example = splitGeneratorRows.find((row) => row.solver_subtechnology === sub);
  taxonomyRows.push({
    future_technology: sub, technology_family: "SOLVER_FUEL_CHP_SUBBAND", additive_in_2040_55GW: example.year === 2040,
    additive_in_2050_30GW: example.year === 2050, historical_cross_classification_only: false,
    capacity_definition: "Exclusive solver sub-band inside frozen parent capacity", scenario_role: "ADDITIVE SOLVER GENERATOR",
    fuel_and_conversion_separate: true, extendable: false, evidence_class: "PROJECT SOLVER SUB-DECOMPOSITION",
    status: "STATIC_SOLVER_TAXONOMY_V3", geography_anchor_only_2040: false, geography_anchor_only_2050: false,
    additive_model_component_2040: example.year === 2040, additive_model_component_2050: example.year === 2050,
    slow_scalability_2040: "INHERITS PARENT; BIOENERGY FIXED IN SLOW", slow_scalability_2050: "INHERITS PARENT; BIOENERGY/GEOTHERMAL/NUCLEAR FIXED",
    solver_fuel_2040: example.year === 2040 ? example.fuel : "NOT_APPLICABLE", solver_carrier_2040: example.year === 2040 ? example.carrier : "NOT_APPLICABLE",
    solver_fuel_2050: example.year === 2050 ? example.fuel : "NOT_APPLICABLE", solver_carrier_2050: example.year === 2050 ? example.carrier : "NOT_APPLICABLE",
    package_version: "SCENARIO_CAPACITY_PACKAGE_V2 / STATIC_SOLVER_CONTRACT_PHASE4B",
    phase4b_parent_capacity_control: false, phase4b_additive_solver_component_2040: example.year === 2040,
    phase4b_additive_solver_component_2050: example.year === 2050, phase4b_solver_subbands_additive: true,
    fuel_subdecomposition_required: false, CHP_subband_required: false,
    phase4b_rule: "MUTUALLY EXCLUSIVE ADDITIVE SOLVER BAND", phase4b_status: "STATIC_SOLVER_TAXONOMY_V3",
  });
}

const crosswalkRows = crosswalkV1.map((row) => ({
  ...row,
  phase4b_parent_geography_frozen: true,
  phase4b_fuel_split_location: ["CCGT", "GT_OCGT", "INTERNAL_COMBUSTION", "STEAM_OTHER_SURVIVING", "OTHER_SURVIVING_THERMAL", "GAS_OTHER_FOSSIL"].includes(row.future_technology) ? "INSIDE EACH FROZEN ZONE" : "NOT_APPLICABLE_OR_PARENT_PASS_THROUGH",
  phase4b_CHP_split_location: ["GEOTHERMAL", "NUCLEAR"].includes(row.future_technology) ? "NOT_APPLICABLE" : "INSIDE EACH FROZEN ZONE",
  phase4b_status: "GEOGRAPHY_UNCHANGED_FUEL_AND_CHP_SUBBANDS_ONLY",
}));

// ---------------------------------------------------------------------------
// Static readiness, including non-generator hydro/storage capacity controls.
// ---------------------------------------------------------------------------
const generatorByParent = groupSum(generatorRows, (row) => KEY(row.year, row.scenario, row.zone, row.parent_capacity_technology), "p_nom_MW");
const storagePowerByKey = groupSum(storageRows, (row) => KEY(row.year, row.scenario, row.zone), "discharge_power_MW");
const readinessRows = [];
// The frozen long table deliberately stores the Slow base portfolio and its CDP
// increment as separate lineage rows.  Readiness is a parent-component view, so
// aggregate those rows before reconciling them to the single static parent.
const readinessParentMap = new Map();
for (const item of capacityLong.filter((row) => ZONES.includes(row.zone))) {
  const key = KEY(Number(item.year), item.scenario, item.zone, item.technology);
  if (!readinessParentMap.has(key)) readinessParentMap.set(key, { ...item, year: Number(item.year), capacity_MW: 0 });
  readinessParentMap.get(key).capacity_MW += Number(item.capacity_MW || 0);
}
for (const row of readinessParentMap.values()) {
  const year = Number(row.year);
  const capacity = Number(row.capacity_MW || 0);
  const isVre = /SOLAR|WIND/.test(row.technology);
  const isBess = row.technology === "BESS";
  const isRor = row.technology === "HYDRO_RUN_OF_RIVER";
  const isHydroStorage = ["HYDRO_BASIN", "HYDRO_RESERVOIR", "PUMPED_HYDRO_PURE", "PUMPED_HYDRO_MIXED"].includes(row.technology);
  const isDispatch = DISPATCH_2040.has(row.technology) || DISPATCH_2050.has(row.technology);
  const applicableEfficiency = isVre ? "READY_NA" : isBess ? "READY" : isRor ? "READY_WITH_PROJECT_ASSUMPTION" : isHydroStorage ? "PARTIAL" : isDispatch ? "READY_WITH_PROJECT_ASSUMPTION" : "READY_NA";
  const co2Status = isVre || isBess || isRor || isHydroStorage || ["GEOTHERMAL", "NUCLEAR"].includes(row.technology) ? "READY_ZERO_OR_SEPARATE_PHYSICAL_REPORTING" : "READY";
  const marginalStatus = isHydroStorage ? "PARTIAL" : "READY";
  const availabilityStatus = isVre || isRor ? "PARTIAL" : isHydroStorage ? "PARTIAL" : row.technology === "NUCLEAR" ? "READY" : "READY_WITH_PROJECT_ASSUMPTION";
  const energyStatus = isBess ? "READY" : isHydroStorage ? "PARTIAL" : "READY_NA";
  const staticStatus = isHydroStorage ? "PARTIAL" : isVre || isRor ? "READY_STATIC_HOURLY_PROFILE_PENDING" : "READY";
  readinessRows.push({
    year, scenario: row.scenario, zone: row.zone, technology: row.technology,
    solver_component_pattern: isBess ? "Electricity Store + fixed charge/discharge Links" : isHydroStorage ? "Water Store + fixed pump/turbine Links as class requires" : "Generator",
    frozen_parent_capacity_MW: ROUND(capacity), parent_capacity_reconciled: isBess ? ALMOST(storagePowerByKey.get(KEY(year, row.scenario, row.zone)) || 0, capacity, 1e-5) : isHydroStorage ? true : ALMOST(generatorByParent.get(KEY(year, row.scenario, row.zone, row.technology)) || 0, capacity, 1e-5),
    capacity_status: "READY", p_nom_extendable: false, fuel_carrier_status: isHydroStorage ? "PARTIAL" : "READY",
    efficiency_status: applicableEfficiency, direct_CO2_status: co2Status, marginal_cost_status: marginalStatus,
    availability_status: availabilityStatus, energy_capacity_status: energyStatus,
    terminal_SOC_status: isBess || isHydroStorage ? "PARTIAL_PHASE4C" : "READY_NA",
    annual_energy_constraint_status: "READY_NA_NONE_HARD", static_solver_status: staticStatus,
    blocking_items: isVre ? "Hourly weather profile" : isRor ? "Hourly inflow profile" : isBess ? "Terminal/cyclic SOC decision" : isHydroStorage ? "Operational e_nom/pump/inflow/SOC allocation" : availabilityStatus === "READY_WITH_PROJECT_ASSUMPTION" ? "No static blocker; preserve sensitivity" : "NONE",
  });
}

// ---------------------------------------------------------------------------
// Oil semantic patch, registers, manifests and gates.
// ---------------------------------------------------------------------------
const oldOilStatus = "ACCEPTED_NON_MATERIAL_PROJECT_ALLOCATION";
const newOilStatus = "ACCEPTED_BASELINE_PROJECT_ALLOCATION_WITH_BOUNDED_RUNTIME_SENSITIVITY";
const oilRows = oilV1.map((row) => ({ ...row, status: newOilStatus, runtime_sensitivity: row.bound === "BASELINE" ? "BASELINE" : row.bound === "BOUND_A_CONCENTRATED_LARGEST_PLAUSIBLE_BAND" ? "RETAINED_ALTERNATIVE_FOR_LATER_RUNTIME" : "BOUNDED_QA_ONLY" }));
const coalRows = coalV1.map((row) => ({ ...row, status: row.bound === "BASELINE" ? row.status : "ACCEPTED_NON_MATERIAL_CONTROL_RESIDUAL" }));

const sourceAdditions = [
  { source_id: "MEM_SCENARIO_CAPACITY_PACKAGE_V2", publisher: "MEM project", title: "Frozen six-scenario installed-capacity package", release_or_year: "Phase 4A / 2026-09-02", acquisition_route: "LOCAL PROJECT ARTIFACT", acquisition_date: "2026-09-02", original_uploaded_filename: "", archived_raw_file: "scenario_capacity_v2/MEM_2040_2050_Zonal_Installed_Capacity_By_Scenario.xlsx", byte_size: (await fs.stat(files.capacityWorkbook)).size, raw_sha256: inputHashes.capacity_workbook, scope: "2040/2050 Slow/Base/High fixed p_nom by technology and zone", evidence_class: "PROJECT CONTROL", status: "FROZEN_V2", url: "" },
  { source_id: "PYPSA_TECHNOLOGY_DATA_V0_15_2040", publisher: "PyPSA", title: "technology-data v0.15.0 costs_2040.csv", release_or_year: "v0.15.0 / 2040", acquisition_route: "OFFICIAL GITHUB RELEASE", acquisition_date: "2026-09-02", original_uploaded_filename: "", archived_raw_file: "", byte_size: "", raw_sha256: "", scope: "Efficiencies, VOM, fuel and emissions", evidence_class: "PYPSA IMPLEMENTATION MAPPING / TECHNICAL SOURCE", status: "REFERENCED", url: "https://raw.githubusercontent.com/PyPSA/technology-data/v0.15.0/outputs/costs_2040.csv" },
  { source_id: "PYPSA_TECHNOLOGY_DATA_V0_15_2050", publisher: "PyPSA", title: "technology-data v0.15.0 costs_2050.csv", release_or_year: "v0.15.0 / 2050", acquisition_route: "OFFICIAL GITHUB RELEASE", acquisition_date: "2026-09-02", original_uploaded_filename: "", archived_raw_file: "", byte_size: "", raw_sha256: "", scope: "Efficiencies, VOM, fuel, emissions and biomass capture", evidence_class: "PYPSA IMPLEMENTATION MAPPING / TECHNICAL SOURCE", status: "REFERENCED", url: "https://raw.githubusercontent.com/PyPSA/technology-data/v0.15.0/outputs/costs_2050.csv" },
  { source_id: "DEA_GAS_ENGINE_CATALOGUE", publisher: "Danish Energy Agency", title: "Technology Data for Generation of Electricity and District Heating — Gas Engines", release_or_year: "official catalogue", acquisition_route: "OFFICIAL WEB PDF", acquisition_date: "2026-09-02", original_uploaded_filename: "", archived_raw_file: "", byte_size: "", raw_sha256: "", scope: "Gas-engine electrical efficiency and VOM trajectory", evidence_class: "PRIMARY TECHNICAL SOURCE", status: "REFERENCED", url: "https://ens.dk/sites/ens.dk/files/Analyser/version_05_-_technology_data_catalogue_for_el_and_dh.pdf" },
  { source_id: "ENEL_ENVIRONMENTAL_COUNTRY_OVERVIEW_2015", publisher: "Enel", title: "Environmental Country Overview 2015 — Italy", release_or_year: "2015", acquisition_route: "OFFICIAL OPERATOR PDF", acquisition_date: "2026-09-02", original_uploaded_filename: "", archived_raw_file: "", byte_size: "", raw_sha256: "", scope: "Italian geothermal-fluid CO2 and generation reference", evidence_class: "OPERATOR PHYSICAL EMISSIONS", status: "REFERENCED_AS_PHYSICAL_QA", url: "https://www.enel.com/content/dam/enel-com/documenti/investitori/sostenibilita/2015/environmental-country-overview_2015.pdf" },
  { source_id: "TYNDP_2024_HYDROGEN_PRICE_ANCHOR", publisher: "ENTSO-E / ENTSOG", title: "TYNDP 2024 scenario and hydrogen import-price evidence", release_or_year: "2024/2025", acquisition_route: "OFFICIAL WEB REPORT", acquisition_date: "2026-09-02", original_uploaded_filename: "", archived_raw_file: "", byte_size: "", raw_sha256: "", scope: "2050 hydrogen fuel-price engineering anchor; capacity share is user-frozen separately", evidence_class: "PRIMARY SCENARIO SOURCE + PROJECT ASSUMPTION", status: "REFERENCED_WITH_SENSITIVITY", url: "https://www.entsog.eu/scenarios" },
];
const sourceManifest = [...sourceManifestInput];
for (const row of sourceAdditions) if (!sourceManifest.some((item) => item.source_id === row.source_id)) sourceManifest.push(row);

const derivationAdditions = [
  { derivation_id: "DER-P4B-001", output: "MEM_2040_Bioenergy_Capacity_Suballocation.csv", evidence_class: "PROJECT DERIVATION / FUEL SUB-DECOMPOSITION", inputs: "Terna 2024 thermoelectric bioenergy fuel control; renewable-source bioenergy geography; post-coal/oil anchor; SCENARIO_CAPACITY_PACKAGE_V2", method: "Scale 3,586 MW by the common 55-GW anchor factor; allocate by renewable-source zonal shares and within-zone ICE/steam/other parent shares; hold fixed in Slow.", key_guardrail: "Bioenergy is carved from frozen parent p_nom and never added above scenario totals.", status: "VALIDATED" },
  { derivation_id: "DER-P4B-002", output: "MEM_Future_CHP_Subband_Allocation.csv", evidence_class: "PROJECT DISPATCH SUBBAND PROXY", inputs: "2024 Terna CHP/non-CHP capacity; frozen V2 parent p_nom", method: "Apply zone×conversion observed CHP shares inside each frozen zonal parent; national fallback only for sparse bands.", key_guardrail: "No zone redistribution; no coupled heat bus; no must-run constraint.", status: "VALIDATED" },
  { derivation_id: "DER-P4B-003", output: "MEM_generators_static_candidate_v2.csv", evidence_class: "STATIC SOLVER CONTRACT", inputs: "SCENARIO_CAPACITY_PACKAGE_V2; Phase 4B fuel and CHP sub-decompositions; static parameter register", method: "Create mutually exclusive fuel×CHP sub-bands and attach numerical static economics.", key_guardrail: "Every parent year×scenario×zone×technology p_nom reconciles exactly; hydrogen adds zero MW.", status: "VALIDATED" },
  { derivation_id: "DER-P4B-004", output: "MEM_Marginal_Cost_Derivation.csv", evidence_class: "PROJECT DERIVATION", inputs: "Fuel price; electrical efficiency; VOM; chargeable direct CO2; CO2 price; other variable cost", method: "MC = fuel/efficiency + VOM + CO2_price×chargeable_CO2/efficiency + other variable cost.", key_guardrail: "CCS efficiency/capture applied once; no heat credit; no automatic BECCS negative credit.", status: "VALIDATED" },
  { derivation_id: "DER-P4B-005", output: "MEM_storage_static_candidate.csv", evidence_class: "ACCEPTED SCENARIO INPUT + PROJECT STATIC ASSUMPTION", inputs: "v2.9 05_STORAGE_BY_ZONE", method: "Retain 2h/4h/8h 2040 classes and 2050 5.444h portfolio; symmetric sqrt(0.90) one-way efficiency; zero standing loss baseline.", key_guardrail: "MACSE is embedded; terminal SOC deferred; no additive subset double count.", status: "VALIDATED" },
];
const derivationManifest = [...derivationManifestInput];
for (const row of derivationAdditions) if (!derivationManifest.some((item) => item.derivation_id === row.derivation_id)) derivationManifest.push(row);

const decisionRegister = decisionRegisterInput.map((row) => row.decision_id === "D-P4A-004" ? {
  ...row, status: "SUPERSEDED_BY_D-P4B-006", value_or_artifact: newOilStatus,
  model_effect: "Capacity geography remains frozen; one bounded alternative allocation is mandatory for later runtime congestion/price sensitivity.",
} : row);
const decisions = [
  ["D-P4B-001", "RESOLVED", "2040 fuel sub-decomposition", "USER CONTROL + PROJECT DERIVATION", "2040 gaseous/fossil combustion is methane; bioenergy and geothermal remain separate.", "METHANE|BIOENERGY|GEOTHERMAL", "No coal/oil/hydrogen/unspecified fossil in 2040."],
  ["D-P4B-002", "RESOLVED", "2040 bioenergy p_nom", "PROJECT DERIVATION", "Carve scaled Terna 2024 fuel-use bioenergy capacity from compatible parent bands and hold it fixed in Slow.", `${ROUND(bio2040National, 6)} MW`, "No capacity is added above the frozen envelope."],
  ["D-P4B-003", "RESOLVED", "Future primary CHP representation", "USER CONTROL", "Use electricity-only CHP-specific dispatch sub-bands inside frozen zonal parents; no heat system and no automatic must-run.", "ELECTRICITY_ONLY_CHP_SPECIFIC_BANDS", "Heat-led/must-run sensitivity remains mandatory later."],
  ["D-P4B-004", "RESOLVED", "2050 hydrogen baseline", "USER-FROZEN PROJECT ASSUMPTION", "Split GAS_OTHER_FOSSIL 50% methane / 50% hydrogen inside every scenario×zone parent.", "H2=0.50; METHANE=0.50", "Hydrogen adds zero MW and receives no CCS."],
  ["D-P4B-005", "RESOLVED", "Source-scenario annual energy", "USER CONTROL", "PNIEC/Terna TWh outputs are soft ex-post calibration controls only.", "NONE_HARD", "No e_sum_min/e_sum_max or fixed annual generation."],
  ["D-P4B-006", "RESOLVED", "Oil residual semantics", "USER CONTROL", "Stop source research; preserve baseline geography and one bounded runtime alternative.", newOilStatus, "Uncertainty is tested later against congestion and prices without changing frozen p_nom now."],
  ["D-P4B-007", "RESOLVED WITH PROJECT ASSUMPTIONS", "Static dispatch economics", "SOURCE HIERARCHY + PROJECT ASSUMPTIONS", "Freeze numerical static efficiency/fuel/CO2/CCS/VOM values with explicit provenance and sensitivity flags.", "MEM_Technology_Static_Parameters_2040_2050.csv", "Enables Phase 4C hourly-input construction."],
];
for (const [decision_id, status, decision, evidence_class, rule, value_or_artifact, model_effect] of decisions) if (!decisionRegister.some((row) => row.decision_id === decision_id)) decisionRegister.push({ decision_id, status, decision, evidence_class, rule, value_or_artifact, model_effect });

const gapRegister = [...gapRegisterInput];
const gaps = [
  { gap_id: "GAP-P4B-001", status: "REQUIRES PHASE 4C", artifact_or_decision: "Hourly VRE availability profiles", evidence_class: "RUNTIME INPUT", authoritative_source: "Reproducible weather/reanalysis workflow reconciled to scenario capacity", local_search_result: "Static carrier and zero-marginal-cost conventions resolved", exact_acquisition_or_decision: "Select weather year and build complete annual hourly p_max_pu", required_fields_or_controls: "8760/8784 explicit snapshots; zone×technology profile; no missing/duplicates", acceptance_test: "Complete-year integrity and annual CF diagnostics", blocks: "Hourly solver input only; not static contract" },
  { gap_id: "GAP-P4B-002", status: "REQUIRES PHASE 4C", artifact_or_decision: "Hydro inflow / SOC / zonal PHS operational parameters", evidence_class: "RUNTIME INPUT", authoritative_source: "Terna annual controls + reviewed hydrology/allocation layer", local_search_result: "p_nom frozen; turbine/PHS efficiency candidates retained; national 53 GWh control unallocated", exact_acquisition_or_decision: "Approve hydro parameter allocation and construct inflow/SOC convention", required_fields_or_controls: "inflow; spill; initial/terminal SOC; pump MW; e_nom; efficiencies", acceptance_test: "Annual energy, storage conservation and no hydro/PHS double count", blocks: "Hydro hourly solver input" },
  { gap_id: "GAP-P4B-003", status: "REQUIRES PHASE 4C", artifact_or_decision: "Hourly thermal maintenance/availability profiles", evidence_class: "RUNTIME INPUT", authoritative_source: "BLK-005 Terna maintenance and derating classes", local_search_result: "Static annual-equivalent fallback and profile classes resolved", exact_acquisition_or_decision: "Create staggered maintenance schedules without a second forced-outage multiplier", required_fields_or_controls: "hourly p_max_pu by generator/subband", acceptance_test: "Class averages reconcile and no double derating", blocks: "Hourly solver input" },
  { gap_id: "GAP-P4B-004", status: "REQUIRES PHASE 4C", artifact_or_decision: "Load, external price and interface hourly series", evidence_class: "RUNTIME INPUT", authoritative_source: "Later load/external-market phase", local_search_result: "Not acquired by design in Phase 4B", exact_acquisition_or_decision: "Build frozen annual hourly contract after weather-year decision", required_fields_or_controls: "load; external prices; fixed links; objective-baseline accounting", acceptance_test: "Complete snapshots, energy controls and trade-cost netting", blocks: "Full solver input" },
  { gap_id: "GAP-P4B-005", status: "CONTROLLED PROJECT ASSUMPTION", artifact_or_decision: "2050 hydrogen fuel price", evidence_class: "PROJECT ASSUMPTION", authoritative_source: "TYNDP scenario/import-price evidence", local_search_result: "42 EUR2025/MWh_th adopted as transparent engineering anchor", exact_acquisition_or_decision: "Retain price sensitivity in runtime phase", required_fields_or_controls: "baseline and price sensitivity", acceptance_test: "No confusion with capacity share; direct stack CO2=0", blocks: "Does not block Phase 4C; affects runtime sensitivity" },
];
for (const row of gaps) if (!gapRegister.some((item) => item.gap_id === row.gap_id)) gapRegister.push(row);

const gateRows = [
  ["SCENARIO_CAPACITY_PACKAGE_STATUS", "FROZEN_V2", "Input workbook/CSV hashes pinned; parent p_nom untouched."],
  ["2040_FOSSIL_FUEL_CONTRACT_STATUS", "FROZEN_METHANE_ONLY", "Bioenergy/geothermal remain separate; no coal/oil/hydrogen."],
  ["2040_BIOENERGY_SUBALLOCATION_STATUS", "RESOLVED_PROJECT_DERIVATION", `${ROUND(bio2040National, 6)} MW inside compatible parent bands; fixed in Slow.`],
  ["FUTURE_CHP_DISPATCH_SUBBAND_STATUS", "FROZEN_ELECTRICITY_ONLY_CHP_SPECIFIC_BANDS", "No heat bus and no automatic must-run; heat-led sensitivity retained."],
  ["2050_HYDROGEN_SPLIT_STATUS", "FROZEN_50PCT_METHANE_50PCT_HYDROGEN", "Substitution inside GAS_OTHER_FOSSIL; no added MW."],
  ["THERMAL_EFFICIENCY_STATUS", "RESOLVED_STATIC_WITH_DOCUMENTED_PROJECT_ASSUMPTIONS", "All applicable generator bands have numerical efficiency."],
  ["FUEL_PRICE_STATUS", "RESOLVED_STATIC_WITH_H2_PROJECT_ANCHOR", "EUR2025/MWh_th; H2 price sensitivity required later."],
  ["CO2_PARAMETER_STATUS", "RESOLVED_STATIC", "Methane 0.198 t/MWh_th; physical/policy treatments separated."],
  ["CCS_PARAMETER_STATUS", "RESOLVED_STATIC_WITH_PROJECT_ASSUMPTIONS", "Capture and efficiency penalty applied once; no BECCS negative credit."],
  ["MARGINAL_COST_STATUS", "RESOLVED_STATIC", "Formula reconciled for every solver subtechnology."],
  ["SOURCE_SCENARIO_SOFT_CONTROL_STATUS", "FROZEN_SOFT_NO_HARD_CAPS", "PNIEC/Terna TWh retained only for ex-post comparison."],
  ["THERMAL_AVAILABILITY_STATUS", "STATIC_CLASSES_READY_HOURLY_SCHEDULE_PENDING", "BLK-005 classes resolved; no 0.85 use in p_max_pu."],
  ["NUCLEAR_AVAILABILITY_STATUS", "MODEL_READY_EXISTING_PROFILE", "BLK-007 0.9245 central profile exists."],
  ["BESS_STATIC_OPERATION_STATUS", "MODEL_READY_TERMINAL_SOC_PENDING", "Power/energy/duration/efficiency ready; terminal SOC Phase 4C."],
  ["HYDRO_STATIC_OPERATION_STATUS", "PARTIAL", "p_nom frozen; operational e_nom/pump/inflow/SOC allocations unresolved."],
  ["PETROLEUM_RESIDUAL_ALLOCATION_STATUS", newOilStatus, "No further source research; one bounded runtime alternative retained."],
  ["COAL_RESIDUAL_STATUS", "ACCEPTED_NON_MATERIAL_CONTROL_RESIDUAL", "158.78 MW control residual remains closed."],
  ["STATIC_GENERATOR_INPUT_STATUS", "READY_FOR_PHASE4C", "All applicable static generator fields numerically complete or explicit N/A."],
  ["HOURLY_INPUT_STATUS", "NOT_STARTED_BY_DESIGN", "No hourly load/VRE/thermal/hydro/external-price package created."],
  ["FULL_SOLVER_INPUT_STATUS", "PARTIAL_PHASE4C_REQUIRED", "Static contract complete; hourly and hydro operational inputs remain."],
  ["WORKBOOK_PROMOTION_STATUS", "BLOCKED", "Accepted canonical v2.9 binary provenance remains unresolved; no v2.9.1 created."],
].map(([gate, status, basis]) => ({ gate, status, basis }));

// ---------------------------------------------------------------------------
// QA.
// ---------------------------------------------------------------------------
const qaRows = [];
const addQa = (qa_id, check, expected, actual, tolerance = 0, severity = "HARD") => {
  let pass;
  if (typeof expected === "number" && typeof actual === "number") pass = Math.abs(expected - actual) <= tolerance;
  else pass = String(expected) === String(actual);
  qaRows.push({ qa_id, check, expected, actual, difference: typeof expected === "number" && typeof actual === "number" ? ROUND(actual - expected) : "", tolerance, severity, status: pass ? "PASS" : "FAIL" });
};
addQa("P4B-001", "Frozen V2 workbook hash", "3ee9cf066fd16eaaee9a11d60a27998b193e253bda9926c48e51077b8c9ef914", inputHashes.capacity_workbook);
addQa("P4B-002", "Frozen V2 CSV hash", "5e06e427edda922125fe1e37ab8f57ce98b823ffeb7b48ab0b209933c5778195", inputHashes.capacity_csv);
addQa("P4B-003", "Frozen V2 parent generator rows", 408, parentRows.length);
addQa("P4B-004", "Frozen V2 capacity rows", 756, capacityLong.length);
addQa("P4B-005", "2040 bioenergy geography sums to one", 1, SUM([...bioGeo.values()]), 1e-9);
addQa("P4B-006", "2040 bioenergy Base national subband", bio2040National, SUM(bio2040Rows.filter((row) => row.scenario === "Base").map((row) => row.bioenergy_subband_MW)), 1e-6);
addQa("P4B-007", "2040 bioenergy Slow fixed at Base", SUM(bio2040Rows.filter((row) => row.scenario === "Base").map((row) => row.bioenergy_subband_MW)), SUM(bio2040Rows.filter((row) => row.scenario === "Slow").map((row) => row.bioenergy_subband_MW)), 1e-6);
for (const scenario of SCENARIOS) {
  const expected = scenario === "Slow" ? 68213.325410972 : 55000;
  addQa(`P4B-2040-TOTAL-${scenario}`, `2040 ${scenario} additive fuel decomposition`, expected, SUM(fuel2040Rows.filter((row) => row.scenario === scenario).map((row) => row.solver_subband_p_nom_MW)), 1e-5);
  addQa(`P4B-2040-FORBIDDEN-${scenario}`, `2040 ${scenario} forbidden coal/oil/hydrogen`, 0, fuel2040Rows.filter((row) => row.scenario === scenario && ["COAL", "OIL", "HYDROGEN", "UNSPECIFIED_FOSSIL"].includes(row.solver_fuel)).length);
}
for (const parent of parentRows) {
  const actual = generatorByParent.get(KEY(parent.year, parent.scenario, parent.zone, parent.technology)) || 0;
  addQa(`P4B-PARENT-${parent.year}-${parent.scenario}-${parent.zone}-${parent.technology}`, "Solver subbands reconcile to frozen parent p_nom", parent.p_nom_MW, actual, 1e-6);
}
for (const row of h2DecisionRows) addQa(`P4B-H2-${row.scenario}-${row.zone}`, "Methane + hydrogen equals GAS_OTHER_FOSSIL parent", row.parent_capacity_MW, Number(row.baseline_methane_capacity_MW) + Number(row.baseline_hydrogen_capacity_MW), 1e-6);
addQa("P4B-H2-ADDITION", "Hydrogen additive capacity", 0, SUM(h2DecisionRows.map((row) => row.capacity_addition_MW)), 0);
addQa("P4B-COAL-OIL-GENERATORS", "Coal/oil in final generator contract", 0, generatorRows.filter((row) => ["COAL", "OIL", "PETROLEUM"].includes(row.fuel)).length);
addQa("P4B-2040-HYDROGEN-GENERATORS", "Hydrogen in 2040 generator contract", 0, generatorRows.filter((row) => row.year === 2040 && row.fuel === "HYDROGEN").length);
addQa("P4B-GENERATOR-ID-UNIQUE", "Generator IDs unique", generatorRows.length, new Set(generatorRows.map((row) => row.generator_id)).size);
addQa("P4B-P-NOM-EXTENDABLE", "All generator p_nom_extendable false", 0, generatorRows.filter((row) => row.p_nom_extendable !== false).length);
addQa("P4B-MC-FORMULA", "All marginal costs recompute", 0, marginalRows.filter((row) => {
  if (!(Number(row.efficiency_el) > 0)) return Math.abs(Number(row.final_marginal_cost_EUR2025_per_MWh_el) - Number(row.VOM_EUR2025_per_MWh_el)) > 1e-7;
  const rebuilt = Number(row.fuel_price_EUR2025_per_MWh_th) / Number(row.efficiency_el) + Number(row.VOM_EUR2025_per_MWh_el) + Number(row.CO2_price_EUR2025_per_t) * Number(row.chargeable_CO2_t_per_MWh_th) / Number(row.efficiency_el) + Number(row.other_variable_cost_EUR2025_per_MWh_el);
  return Math.abs(rebuilt - Number(row.final_marginal_cost_EUR2025_per_MWh_el)) > 1e-6;
}).length);
addQa("P4B-BIOENERGY-EFFICIENCY-SPLIT", "Every bioenergy CHP/non-CHP solver band uses the accepted 0.3003/0.468 proxy, with the CCS electricity penalty applied once where applicable", 0,
  generatorRows.filter((row) => {
    if (row.fuel !== "BIOENERGY") return false;
    const uncaptured = row.CHP_flag === true ? 0.3003 : 0.468;
    const expected = row.CCS_flag === true ? uncaptured - (0.075 + 0.02) * bioCO2 * 0.95 : uncaptured;
    return !ALMOST(row.efficiency, expected, 1e-12);
  }).length);
addQa("P4B-SOFT-CONTROLS", "All source-scenario TWh controls are non-hard", energySoftRows.length, energySoftRows.filter((row) => row.annual_energy_constraint_type === "NONE_HARD" && row.e_sum_min === "" && row.e_sum_max === "" && row.fixed_annual_generation === false).length);
addQa("P4B-AVAIL-085", "0.85 Slow adequacy factor absent from availability parameters", 0, availabilityRows.filter((row) => String(row.static_annual_equivalent) === "0.85" || row.slow_0p85_factor_used_as_hourly_availability !== false).length);
addQa("P4B-BESS-RT", "All BESS round-trip efficiencies equal 0.90", 0, storageRows.filter((row) => !ALMOST(row.roundtrip_efficiency, 0.9, 1e-12)).length);
for (const year of [2040, 2050]) for (const scenario of SCENARIOS) for (const zone of ZONES) {
  const parent = Number(capacityLong.find((row) => Number(row.year) === year && row.scenario === scenario && row.zone === zone && row.technology === "BESS")?.capacity_MW || 0);
  const storage = storagePowerByKey.get(KEY(year, scenario, zone)) || 0;
  addQa(`P4B-BESS-${year}-${scenario}-${zone}`, "BESS static discharge power reconciles to frozen parent", parent, storage, 1e-5);
}
addQa("P4B-READINESS-PARENTS", "All readiness parent capacities reconcile", 0, readinessRows.filter((row) => row.parent_capacity_reconciled !== true).length);
addQa("P4B-CHP-GEOGRAPHY", "All CHP splits preserve geography", 0, chpAllocationRows.filter((row) => row.geography_changed !== false).length);
addQa("P4B-OIL-STATUS", "Oil status patched", 0, oilRows.filter((row) => row.status !== newOilStatus).length);
addQa("P4B-COAL-STATUS", "Coal residual remains accepted", 0, coalRows.filter((row) => row.bound !== "BASELINE" && row.status !== "ACCEPTED_NON_MATERIAL_CONTROL_RESIDUAL").length);

const failuresBeforeAuthor = qaRows.filter((row) => row.status !== "PASS");
if (failuresBeforeAuthor.length) throw new Error(`Phase 4B pre-author QA failures: ${JSON.stringify(failuresBeforeAuthor.slice(0, 10))}`);

// ---------------------------------------------------------------------------
// Author outputs through artifact-tool backed CSV round-trips.
// ---------------------------------------------------------------------------
const authored = [];
authored.push(await authorCsv(fuel2040Rows, "Fuel2040", out.fuel2040));
authored.push(await authorCsv(bio2040Rows, "Bio2040", out.bio2040));
authored.push(await authorCsv(chpAllocationRows, "CHPSubbands", out.chp));
authored.push(await authorCsv(h2DecisionRows, "H2Baseline", out.h2Decision));
authored.push(await authorCsv(energySoftRows, "SoftEnergy", out.energySoft));
authored.push(await authorCsv(staticParameterRows, "StaticParameters", out.parameters));
authored.push(await authorCsv(marginalRows, "MarginalCosts", out.marginalCosts));
authored.push(await authorCsv(availabilityRows, "Availability", out.availability));
authored.push(await authorCsv(carrierRows, "Carriers", out.carriers));
authored.push(await authorCsv(generatorRows, "GeneratorsV2", out.generators));
authored.push(await authorCsv(storageRows, "StorageStatic", out.storage));
authored.push(await authorCsv(readinessRows, "StaticReadiness", out.readiness));
authored.push(await authorCsv(crosswalk2040Rows, "Carrier2040", out.crosswalk2040));
authored.push(await authorCsv(architecture2050Rows, "Architecture2050", out.architecture2050));
authored.push(await authorCsv(gasOtherDecompositionRows, "GasOther2050", out.decomposition2050));
authored.push(await authorCsv(taxonomyRows, "FutureTaxonomy", out.taxonomy));
authored.push(await authorCsv(crosswalkRows, "FutureCrosswalk", out.crosswalk));
authored.push(await authorCsv(oilRows, "OilMateriality", out.oil));
authored.push(await authorCsv(coalRows, "CoalMateriality", out.coal));
authored.push(await authorCsv(sourceManifest, "SourceManifest", out.sourceManifest));
authored.push(await authorCsv(derivationManifest, "DerivationManifest", out.derivationManifest));
authored.push(await authorCsv(decisionRegister, "DecisionRegister", out.decisionRegister));
authored.push(await authorCsv(gapRegister, "GapRegister", out.gapRegister));
authored.push(await authorCsv(gateRows, "Phase4BGates", out.gates));

// Update the live project manifests/registers with lineage-preserving successors.
await authorCsv(sourceManifest, "SourceManifest", files.sourceManifest);
await authorCsv(derivationManifest, "DerivationManifest", files.derivationManifest);
await authorCsv(decisionRegister, "DecisionRegister", files.decisionRegister);
await authorCsv(gapRegister, "GapRegister", files.gapRegister);

// Semantic patch of non-binary Phase-4A evidence. The frozen workbook/CSV remain byte-identical.
await authorCsv(oilRows, "OilMateriality", files.oilMaterialityV1);
const patchedGates = phase4AGates.map((row) => row.gate === "PETROLEUM_RESIDUAL_ALLOCATION_STATUS" ? { ...row, status: newOilStatus, next_action: "Retain one bounded alternative for later runtime congestion/price sensitivity; no further source research." } : row);
await authorCsv(patchedGates, "GatesV1", files.phase4AGates);
const phase4AMethod = await fs.readFile(files.phase4AMethod, "utf8");
await fs.writeFile(files.phase4AMethod, phase4AMethod.replaceAll(oldOilStatus, newOilStatus), "utf8");
const phase4AVerification = recursiveReplace(JSON.parse(await fs.readFile(files.phase4AVerification, "utf8")), oldOilStatus, newOilStatus);
await fs.writeFile(files.phase4AVerification, `${JSON.stringify(phase4AVerification, null, 2)}\n`, "utf8");

const chpDecision = `# MEM future CHP solver representation — Phase 4B\n\n` +
`## Controlling decision\n\nThe primary MEM solver uses **electricity-only CHP-specific dispatch sub-bands**. Combined CHP + non-CHP capacity remains the frozen geography anchor, but applicable parent capacity is split inside each market zone into mutually exclusive CHP and non-CHP generators. No capacity moves between zones.\n\n` +
`No heat buses, heat demand, heat credit or automatic minimum-output constraint are created. Historical CHP capacity shares are allocation proxies, and historical capacity factors are characterization evidence only. CCGT/GT/engine CHP variants inherit the same electricity-side conversion efficiency as the corresponding non-CHP conversion where the technical source does not justify a separate number. Bioenergy CHP uses a distinct 0.3003 electricity efficiency versus 0.468 for the non-CHP proxy.\n\n` +
`The later **CHP_HEAT_LED_MUST_RUN_SENSITIVITY** is mandatory. It may introduce an externally specified heat-led minimum or availability profile, but it must not overwrite the primary electricity-only case.\n`;
await fs.writeFile(out.chpDecision, chpDecision, "utf8");

const mcTable = marginalRows.filter((row) => !/SOLAR|WIND|HYDRO_RUN/.test(row.solver_subtechnology))
  .map((row) => `| ${row.year} | ${row.solver_subtechnology} | ${Number(row.efficiency_el).toFixed(6)} | ${Number(row.final_marginal_cost_EUR2025_per_MWh_el).toFixed(6)} |`)
  .join("\n");
const method = `# MEM Phase 4B — static dispatch economics and solver-carrier closure\n\n` +
`## Outcome\n\nSCENARIO_CAPACITY_PACKAGE_V2 remains byte-pinned and unchanged at the parent-capacity layer. Phase 4B creates exclusive fuel × CHP solver sub-bands inside those parents and attaches numerical static dispatch parameters. No hourly input file and no PyPSA run were created.\n\n` +
`## 2040 fuel accounting\n\nThe final additive fuels are methane, bioenergy and geothermal. The 2024 Terna thermoelectric fuel-use bioenergy control of 3,586 MW is scaled by the common 55-GW anchor factor ${scale2040.toFixed(12)}, producing a ${bio2040National.toFixed(9)}-MW 2040 bioenergy sub-band. It is allocated by renewable-source bioenergy geography and compatible ICE/steam/other parent shares, and subtracted from those parents. It is fixed at Base in Slow; the full Slow increment remains methane.\n\n` +
`## CHP\n\nCHP splitting occurs inside every frozen zone. Zone × conversion-technology observed 2024 shares are preferred; sparse bands use the national conversion share. The primary model is electricity-only. No heat credit or must-run condition is embedded.\n\n` +
`## 2050 fuel accounting\n\nGAS_OTHER_FOSSIL is split 50% methane and 50% hydrogen in every scenario × zone. Hydrogen is substitutional and adds zero MW. GAS_CCS is methane+CCS; no hydrogen CCS is created. Optional later H2 capacity-share sensitivities are 0%, 25%, 75% and 100%.\n\n` +
`## Soft annual energy controls\n\nPNIEC/Terna source outputs (bioenergy 10.6 TWh, bioenergy+CCS 6.0 TWh, gas+CCS 4.0 TWh, gas+other fossil 2.1 TWh and nuclear 64.2 TWh) are retained only for ex-post validation. All rows have NONE_HARD; there is no e_sum_min, e_sum_max or fixed annual generation.\n\n` +
`## Marginal-cost convention\n\nFor combustion generators, MC = fuel price / electrical efficiency + VOM + CO2 price × chargeable direct CO2 / electrical efficiency + other variable cost. CCS-specific efficiency is used once, capture rate is used once, and BECCS receives no negative-emissions credit. Methane prices are 38.0563456 EUR2025/MWh_th in 2040 and 22.7578 in 2050; bioenergy is 9.3506; hydrogen is a transparent 42 EUR2025/MWh_th engineering assumption requiring price sensitivity. CO2 prices are 104.5504 EUR2025/t in 2040 and 400 in 2050.\n\n` +
`### Dispatchable marginal costs\n\n| Year | Solver subtechnology | Efficiency | EUR2025/MWh_el |\n|---:|---|---:|---:|\n${mcTable}\n\n` +
`## CCS\n\nMethane CCGT+CCS uses a 0.52 CCS-specific net electrical efficiency, 90% capture and the accepted Terna 54 EUR/t-captured OPEX component as a project dispatch assumption; the carbon price applies only to residual stack CO2. Bioenergy+CCS uses 95% capture and subtracts the technology-data capture+compression electricity input once from the uncaptured electricity efficiency. Captured biogenic CO2 is reported but not remunerated in the objective.\n\n` +
`## Availability\n\nBLK-005 maintenance hours and Terna zonal outside-maintenance deratings define the thermal profile classes. Static annual-equivalent values are fallbacks; Phase 4C must stagger maintenance. The Slow 0.85 adequacy conversion is never used as hourly availability. Nuclear references the existing BLK-007 profile with mean 0.9245.\n\n` +
`## Storage and hydro\n\nBESS power, energy and duration are copied from the accepted scenario table; one-way efficiency is sqrt(0.90), standing loss is a transparent zero baseline, and terminal SOC is deferred. Hydro p_nom remains frozen. Hydro turbine efficiency 0.90 and symmetric PHS sqrt(0.75) remain candidates; national PHS power/53-GWh controls are not silently allocated zonally, so hydro static operation remains PARTIAL.\n\n` +
`## Residual oil\n\nOil-source research is stopped. The frozen baseline allocation is retained under ${newOilStatus}; one bounded alternative remains for later congestion/price sensitivity. Coal residual status is unchanged.\n`;
await fs.writeFile(out.methodology, method, "utf8");

// Author QA after all output and semantic-patch checks.
addQa("P4B-OUTPUT-WORKBOOK-HASH-STILL", "Frozen V2 workbook remains byte-identical after Phase 4B", inputHashes.capacity_workbook, await sha256(files.capacityWorkbook));
addQa("P4B-OUTPUT-CSV-HASH-STILL", "Frozen V2 CSV remains byte-identical after Phase 4B", inputHashes.capacity_csv, await sha256(files.capacityLong));
addQa("P4B-GENERATOR-APPLICABLE-NUMERICS", "All applicable static generator numeric fields are populated", 0, generatorRows.filter((row) => {
  const efficiencyRequired = !/SOLAR|WIND/.test(row.solver_subtechnology);
  return (efficiencyRequired && !(Number(row.efficiency) > 0)) || row.VOM_EUR2025_per_MWh_el === "" || row.fuel_price_EUR2025_per_MWh_th === "" || row.marginal_cost_EUR2025_per_MWh_el === "";
}).length);
authored.push(await authorCsv(qaRows, "Phase4BQA", out.qa));

const qaFailures = qaRows.filter((row) => row.status !== "PASS");
if (qaFailures.length) throw new Error(`Phase 4B final QA failures: ${JSON.stringify(qaFailures.slice(0, 10))}`);

const verification = {
  generated_at: new Date().toISOString(),
  phase: "MEM Phase 4B — Static Dispatch Economics and Solver-Carrier Closure",
  input_lock: inputHashes,
  input_counts: { capacity: capacityLong.length, phase4a_generators: generatorsV1.length, phase4a_readiness: readinessV1.length },
  output_counts: {
    fuel_2040: fuel2040Rows.length, bioenergy_2040: bio2040Rows.length, CHP_subbands: chpAllocationRows.length,
    generators_v2: generatorRows.length, static_parameters: staticParameterRows.length, marginal_costs: marginalRows.length,
    availability: availabilityRows.length, storage: storageRows.length, readiness: readinessRows.length, QA: qaRows.length,
  },
  controls: {
    bioenergy_2040_MW: ROUND(bio2040National, 12), bioenergy_scale: ROUND(scale2040, 12),
    h2_baseline_share: 0.5, methane_baseline_share_in_gas_other: 0.5,
    methane_price_2040: methanePrice[2040], methane_price_2050: methanePrice[2050], hydrogen_price_2050: hydrogenPrice2050,
    bioenergy_price: bioFuelPrice, CO2_price_2040: co2Price[2040], CO2_price_2050: co2Price[2050],
    methane_direct_CO2: methaneCO2, bioenergy_physical_CO2: bioCO2, geothermal_physical_CO2_t_per_MWh_el: geothermalPhysicalCO2,
  },
  statuses: Object.fromEntries(gateRows.map((row) => [row.gate, row.status])),
  QA: { pass: qaRows.filter((row) => row.status === "PASS").length, fail: qaFailures.length },
  outputs: authored,
  no_hourly_series_created: true,
  pypsa_run_executed: false,
};
await fs.writeFile(out.verification, `${JSON.stringify(verification, null, 2)}\n`, "utf8");

process.stdout.write(`${JSON.stringify({
  outcome: "PHASE4B_STATIC_CONTRACT_COMPLETE",
  output_dir: outputDir,
  input_hashes: inputHashes,
  bioenergy_2040_MW: bio2040National,
  counts: verification.output_counts,
  qa: verification.QA,
  gates: verification.statuses,
  unique_marginal_costs: marginalRows.map((row) => ({ year: row.year, solver_subtechnology: row.solver_subtechnology, marginal_cost: row.final_marginal_cost_EUR2025_per_MWh_el })),
}, null, 2)}\n`);

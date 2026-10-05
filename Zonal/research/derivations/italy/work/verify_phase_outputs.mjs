import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { Workbook } from "@oai/artifact-tool";

const phaseRoot = path.resolve("..");
const releaseRoot = path.resolve("../..");
const sha256File = async (filePath) =>
  crypto.createHash("sha256").update(await fs.readFile(filePath)).digest("hex");

async function readCsv(filePath, sheetName) {
  const workbook = await Workbook.fromCSV(await fs.readFile(filePath, "utf8"), {
    sheetName,
  });
  const values = workbook.worksheets.getItem(sheetName).getUsedRange().values;
  const headers = values[0];
  const rows = values.slice(1).map((valuesRow) =>
    Object.fromEntries(headers.map((header, index) => [header, valuesRow[index]])),
  );
  return { workbook, headers, rows };
}
const sum = (values) => values.reduce((total, value) => total + Number(value), 0);
const uniqueCount = (rows, fields) =>
  new Set(rows.map((row) => fields.map((field) => row[field]).join("\u0000"))).size;

const capacityPath = path.join(
  phaseRoot,
  "normalized",
  "Terna_Thermoelectric_Capacity_2024_Canonical.csv",
);
const productionPath = path.join(
  phaseRoot,
  "normalized",
  "Terna_Thermoelectric_Production_2024_Canonical.csv",
);
const crosswalkPath = path.join(
  phaseRoot,
  "normalized",
  "MEM_Province_Region_MarketZone_Crosswalk.csv",
);
const matrixPath = path.join(
  phaseRoot,
  "analysis",
  "Terna_Thermoelectric_Capacity_2024_Zone_Technology_CHP_Matrix.csv",
);
const cfPath = path.join(
  phaseRoot,
  "analysis",
  "Terna_Thermoelectric_2024_Observed_Capacity_Factors.csv",
);

const capacity = await readCsv(capacityPath, "Capacity");
const production = await readCsv(productionPath, "Production");
const crosswalk = await readCsv(crosswalkPath, "Crosswalk");
const matrix = await readCsv(matrixPath, "Matrix");
const cf = await readCsv(cfPath, "CF");

const capacityKeyFields = [
  "year",
  "capacity_basis",
  "region_original",
  "province_original",
  "category_original",
  "subcategory_original",
];
const productionKeyFields = [
  "year",
  "region_original",
  "province_original",
  "category_original",
  "subcategory_original",
];
const zoneSet = [...new Set(crosswalk.rows.map((row) => row.market_zone))].sort();
const expectedZones = ["CALA", "CNOR", "CSUD", "NORD", "SARD", "SICI", "SUD"];
const checks = [
  {
    check: "Controlling v2.9 workbook SHA-256",
    observed: await sha256File(
      path.join(
        releaseRoot,
        "Fase_5_PyPSA_Eur_Italia_2040_2050_Model_Ready_v2.9_MEM_ARCHITECTURE_REFINED.xlsx",
      ),
    ),
    expected: "4a59c80bdf4da33d825ff94ea5617c03c325ca6fbc26233e7ed633ed6f910640",
  },
  { check: "Canonical capacity rows", observed: capacity.rows.length, expected: 578 },
  {
    check: "Canonical capacity unique grain",
    observed: uniqueCount(capacity.rows, capacityKeyFields),
    expected: capacity.rows.length,
  },
  {
    check: "Canonical capacity total MW",
    observed: sum(capacity.rows.map((row) => row.efficient_power_MW)),
    expected: 60331.82927,
    tolerance: 1e-8,
  },
  { check: "Canonical production rows", observed: production.rows.length, expected: 587 },
  {
    check: "Canonical production unique grain",
    observed: uniqueCount(production.rows, productionKeyFields),
    expected: production.rows.length,
  },
  {
    check: "Production Lorda below Netta rows",
    observed: production.rows.filter(
      (row) => Number(row.production_lorda_GWh) < Number(row.production_netta_GWh),
    ).length,
    expected: 0,
  },
  {
    check: "Production ambiguous/equal rows",
    observed: production.rows.filter(
      (row) => row.basis_resolution_status === "AMBIGUOUS_EQUAL_VALUES_NO_ROW_ORDER_ASSIGNED",
    ).length,
    expected: 96,
  },
  {
    check: "Production Netta total GWh",
    observed: sum(production.rows.map((row) => row.production_netta_GWh)),
    expected: 146360.808172,
    tolerance: 1e-8,
  },
  { check: "Crosswalk rows", observed: crosswalk.rows.length, expected: 107 },
  {
    check: "Crosswalk unique province codes",
    observed: uniqueCount(crosswalk.rows, ["province_code"]),
    expected: 107,
  },
  {
    check: "Crosswalk zone set",
    observed: zoneSet.join("|"),
    expected: expectedZones.join("|"),
  },
  { check: "Matrix rows", observed: matrix.rows.length, expected: 98 },
  {
    check: "Matrix total MW",
    observed: sum(matrix.rows.map((row) => row.net_MW)),
    expected: 60331.82927,
    tolerance: 1e-8,
  },
  { check: "CF output rows", observed: cf.rows.length, expected: 157 },
  {
    check: "Calculated CF above 1",
    observed: cf.rows.filter(
      (row) =>
        String(row.calculation_status).startsWith("CALCULATED") &&
        Number(row.observed_capacity_factor) > 1,
    ).length,
    expected: 0,
  },
];

for (const check of checks) {
  const tolerance = check.tolerance ?? 0;
  check.status =
    typeof check.observed === "number" && typeof check.expected === "number"
      ? Math.abs(check.observed - check.expected) <= tolerance
        ? "PASS"
        : "FAIL"
      : String(check.observed).toLowerCase() === String(check.expected).toLowerCase()
        ? "PASS"
        : "FAIL";
}

const requiredDocs = [
  "README.md",
  "SOURCE_MANIFEST.csv",
  "DERIVATION_MANIFEST.csv",
  "DATA_STATUS.csv",
  "docs/MEM_THERMAL_STACK_METHOD_AND_RESULTS.md",
  "docs/MEM_SLOW_CAPACITY_CALIBRATION_SPEC.md",
  "docs/MEM_v2.9_FROZEN_DATA_CONTRACT_THERMAL_STACK_ADDENDUM_20260901.md",
  "docs/MEM_v2.9_EMPIRICAL_GAP_AND_ACQUISITION_REGISTER_UPDATED_20260901.csv",
  "docs/MEM_v2.9_THERMAL_STACK_DECISION_REGISTER_20260901.csv",
  "docs/GEM_GOGPT_AUG2026_ACQUISITION_REQUIREMENT.md",
  "qa/MEM_THERMAL_STACK_QA_REPORT_20260901.md",
];
const missingDocs = [];
for (const relativePath of requiredDocs) {
  try {
    await fs.access(path.join(phaseRoot, relativePath));
  } catch {
    missingDocs.push(relativePath);
  }
}
checks.push({
  check: "Required phase governance artifacts present",
  observed: missingDocs.length,
  expected: 0,
  status: missingDocs.length === 0 ? "PASS" : "FAIL",
});

const v291 = (await fs.readdir(releaseRoot)).filter((name) =>
  name.toLowerCase().includes("v2.9.1") && name.toLowerCase().endsWith(".xlsx"),
);
checks.push({
  check: "No premature v2.9.1 workbook",
  observed: v291.length,
  expected: 0,
  status: v291.length === 0 ? "PASS" : "FAIL",
});

const result = {
  generated_at_utc: new Date().toISOString(),
  overall_status: checks.some((check) => check.status === "FAIL") ? "FAIL" : "PASS",
  checks,
  missing_docs: missingDocs,
  v2_9_1_workbooks: v291,
  output_hashes: Object.fromEntries(
    await Promise.all(
      [capacityPath, productionPath, crosswalkPath, matrixPath, cfPath].map(
        async (filePath) => [path.relative(phaseRoot, filePath), await sha256File(filePath)],
      ),
    ),
  ),
};
await fs.writeFile(
  path.join(phaseRoot, "qa", "FINAL_PHASE_VERIFICATION.json"),
  `${JSON.stringify(result, null, 2)}\n`,
  "utf8",
);
console.log(JSON.stringify(result, null, 2));
if (result.overall_status !== "PASS") process.exitCode = 1;

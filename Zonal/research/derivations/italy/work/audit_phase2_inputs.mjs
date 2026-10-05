import fs from "node:fs/promises";
import path from "node:path";
import { Workbook } from "@oai/artifact-tool";

const phaseRoot = path.resolve("..");
const hbRoot = path.join(phaseRoot, "historical_baseline");

async function readCsv(file, sheetName) {
  const workbook = await Workbook.fromCSV(await fs.readFile(file, "utf8"), { sheetName });
  const values = workbook.worksheets.getItem(sheetName).getUsedRange().values;
  const headers = values[0].map(String);
  const rows = values.slice(1)
    .filter((row) => row.some((value) => value !== null && value !== ""))
    .map((row) => Object.fromEntries(headers.map((header, index) => [header, row[index]])));
  return { headers, rows };
}

const capPath = path.join(hbRoot, "normalized", "MEM_Historical_Capacity_By_Zone_Technology.csv");
const genPath = path.join(hbRoot, "normalized", "MEM_Historical_Generation_By_Zone_Technology.csv");
const taxPath = path.join(hbRoot, "normalized", "MEM_HISTORICAL_TECHNOLOGY_TAXONOMY.csv");
const cap = await readCsv(capPath, "Capacity");
const gen = await readCsv(genPath, "Generation");
const tax = await readCsv(taxPath, "Taxonomy");
const taxRows = tax.rows.filter((row) => row.taxonomy_dimension === "TECHNOLOGY");
const taxByCode = new Map(taxRows.map((row) => [String(row.code), row]));

const comparable = (value) => String(value ?? "").trim().toLowerCase();
const auditRows = (rows, table) => {
  const issues = [];
  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index];
    const expected = taxByCode.get(String(row.technology));
    if (!expected) {
      issues.push({ table, row_number: index + 2, technology: row.technology, field: "technology", observed: row.technology, expected: "KNOWN_TAXONOMY_CODE", issue: "UNKNOWN_TECHNOLOGY" });
      continue;
    }
    for (const field of ["chp_flag", "storage_flag", "dispatchability_class"]) {
      if (comparable(row[field]) !== comparable(expected[field])) {
        issues.push({ table, row_number: index + 2, year: row.year, market_zone: row.market_zone, technology: row.technology, field, observed: row[field], expected: expected[field], issue: "TAXONOMY_INVARIANT_MISMATCH" });
      }
    }
    if (String(row.technology).includes("_NON_CHP") && comparable(row.chp_flag) === "true") {
      issues.push({ table, row_number: index + 2, year: row.year, market_zone: row.market_zone, technology: row.technology, field: "chp_flag", observed: row.chp_flag, expected: "false", issue: "NON_CHP_FLAG_TRUE" });
    }
  }
  return issues;
};

const capIssues = auditRows(cap.rows, "CAPACITY");
const genIssues = auditRows(gen.rows, "GENERATION");
const duplicateCount = (rows, fields) => {
  const counts = new Map();
  for (const row of rows) {
    const key = fields.map((field) => String(row[field] ?? "")).join("\u0000");
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.values()].filter((count) => count > 1).length;
};

const oldSummaryPath = path.join(hbRoot, "qa", "MEM_HISTORICAL_BASELINE_2024_BUILD_SUMMARY.json");
const oldSummary = JSON.parse(await fs.readFile(oldSummaryPath, "utf8"));

const result = {
  capacity_rows: cap.rows.length,
  generation_rows: gen.rows.length,
  taxonomy_technology_rows: taxRows.length,
  capacity_taxonomy_issue_count: capIssues.length,
  generation_taxonomy_issue_count: genIssues.length,
  capacity_taxonomy_issues: capIssues,
  generation_taxonomy_issues: genIssues,
  capacity_duplicate_grains: duplicateCount(cap.rows, ["year", "market_zone", "technology", "fuel_source", "chp_flag", "storage_flag", "perimeter_layer"]),
  generation_duplicate_grains: duplicateCount(gen.rows, ["year", "market_zone", "technology", "fuel_source", "chp_flag", "storage_flag", "perimeter_layer"]),
  final_ccgt_non_chp_rows: cap.rows.filter((row) => row.technology === "CCGT_NON_CHP").slice(0, 10),
  final_ccgt_chp_rows: cap.rows.filter((row) => row.technology === "CCGT_CHP").slice(0, 10),
  old_2024_build_summary_ccgt_non_chp: oldSummary?.outputs?.capacity_by_technology?.find?.((row) => row.technology === "CCGT_NON_CHP") ?? null,
};
await fs.writeFile(path.join(hbRoot, "qa", "PHASE2_INITIAL_TAXONOMY_AUDIT.json"), `${JSON.stringify(result, null, 2)}\n`, "utf8");
console.log(JSON.stringify(result, null, 2));

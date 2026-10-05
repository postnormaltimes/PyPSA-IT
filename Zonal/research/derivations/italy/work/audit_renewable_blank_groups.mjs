import fs from "node:fs/promises";
import path from "node:path";
import { detectTernaDecimalConventions, parseTernaNumber } from "../scripts/terna_numeric_parser.mjs";

const phaseRoot = path.resolve(import.meta.dirname, "..");
const rawDir = path.join(phaseRoot, "raw", "terna", "historical_2019_2025_api");
const normalize = (value) => String(value ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
const group = (rows, keyFn) => {
  const out = new Map();
  for (const row of rows) {
    const key = keyFn(row);
    if (!out.has(key)) out.set(key, []);
    out.get(key).push(row);
  }
  return out;
};
const result = [];
for (const year of [2019, 2020, 2021, 2022, 2023, 2024]) {
  const capPayload = JSON.parse(await fs.readFile(path.join(rawDir, `renewable-source-capacity_${year}_RAW.json`), "utf8"));
  const prodPayload = JSON.parse(await fs.readFile(path.join(rawDir, `renewable-sources-production_${year}_RAW.json`), "utf8"));
  const capRows = capPayload.renewable_sources.filter((row) => row.capacity_type === "Netta");
  const prodRows = prodPayload.renewable_sources.filter((row) => row.production_type === "Netta");
  const capConvention = detectTernaDecimalConventions(capRows.map((row) => row.efficient_power_MW).filter((v) => v !== null && v !== undefined && String(v).trim() !== ""));
  const prodConvention = detectTernaDecimalConventions(prodRows.map((row) => row.production_GWh).filter((v) => v !== null && v !== undefined && String(v).trim() !== ""));
  const prodGroups = group(prodRows, (row) => [row.year, normalize(row.region), normalize(row.province), normalize(row.renewable_source)].join("\u0000"));
  const capGroups = group(capRows, (row) => [row.year, normalize(row.region), normalize(row.province), normalize(row.source)].join("\u0000"));
  for (const [key, rows] of capGroups) {
    const numeric = rows.filter((row) => row.efficient_power_MW !== null && row.efficient_power_MW !== undefined && String(row.efficient_power_MW).trim() !== "");
    const blanks = rows.length - numeric.length;
    if (!blanks) continue;
    const production = (prodGroups.get(key) ?? []).reduce((sum, row) => sum + parseTernaNumber(row.production_GWh, prodConvention).value, 0);
    const capacity = numeric.reduce((sum, row) => sum + parseTernaNumber(row.efficient_power_MW, capConvention).value, 0);
    result.push({
      year,
      source: rows[0].source,
      region: rows[0].region,
      province: rows[0].province,
      source_row_count: rows.length,
      numeric_row_count: numeric.length,
      blank_row_count: blanks,
      reported_capacity_sum_MW: Number(capacity.toFixed(9)),
      net_generation_GWh: Number(production.toFixed(9)),
      group_semantics: numeric.length > 0
        ? "MIXED_VISIBLE_GROUP: BLANK HIDDEN RECORDS ADD ZERO TO REPORTED CAPACITY; NUMERIC RECORDS CONTROL"
        : production > 0
          ? "ALL_BLANK_WITH_POSITIVE_GENERATION: CAPACITY UNRESOLVED"
          : "ALL_BLANK_NO_GENERATION: NO_REPORTED_CAPACITY",
      material_exception: numeric.length === 0 && production > 1,
    });
  }
}
const summary = {
  group_count: result.length,
  mixed_group_count: result.filter((row) => row.numeric_row_count > 0).length,
  all_blank_no_generation_count: result.filter((row) => row.numeric_row_count === 0 && row.net_generation_GWh === 0).length,
  all_blank_positive_generation_count: result.filter((row) => row.numeric_row_count === 0 && row.net_generation_GWh > 0).length,
  material_exception_count: result.filter((row) => row.material_exception).length,
  all_blank_positive_generation: result.filter((row) => row.numeric_row_count === 0 && row.net_generation_GWh > 0),
};
process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);

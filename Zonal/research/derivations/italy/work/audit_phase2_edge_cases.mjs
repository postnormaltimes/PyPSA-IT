import fs from "node:fs/promises";
import path from "node:path";

const phaseRoot = path.resolve("..");
const rawDir = path.join(phaseRoot, "raw", "terna", "historical_2019_2025_api");
const normalize = (value) => String(value ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().replace(/\s+/g, " ");
const groupRows = (rows, keyFn) => {
  const groups = new Map();
  for (const row of rows) {
    const key = keyFn(row);
    const current = groups.get(key) ?? [];
    current.push(row);
    groups.set(key, current);
  }
  return groups;
};
const parse = (value) => {
  if (value === null || value === undefined || String(value).trim() === "") return null;
  const text = String(value).trim();
  if (/^-?\d+(?:\.\d+)?$/.test(text)) return Number(text);
  if (/^-?\d+(?:,\d+)?$/.test(text)) return Number(text.replace(",", "."));
  throw new Error(`Ambiguous numeric value: ${text}`);
};

const capacityPayload = JSON.parse(await fs.readFile(path.join(rawDir, "thermoelectric-capacity_2024_RAW.json"), "utf8"));
const capacityRows = capacityPayload.thermoelectric.filter((row) => row.capacity_type === "Netta");
const capacityKey = (row) => [row.year, normalize(row.region), normalize(row.province), normalize(row.category), normalize(row.subcategory)].join("\u0000");
const capacityGroups = groupRows(capacityRows, capacityKey);
const exactDuplicateGroups = [];
let rawCapacitySum = 0;
let duplicateNormalizedSum = 0;
for (const [key, rows] of capacityGroups) {
  const values = rows.map((row) => parse(row.efficient_power_MW));
  rawCapacitySum += values.reduce((a, b) => a + b, 0);
  const allRowsIdenticalVisible = rows.every((row) => JSON.stringify(row) === JSON.stringify(rows[0]));
  const allMwIdentical = values.every((value) => value === values[0]);
  if (rows.length > 1 && allRowsIdenticalVisible && allMwIdentical) {
    duplicateNormalizedSum += values[0];
    exactDuplicateGroups.push({
      visible_key: key.split("\u0000").join(" | "),
      row_count: rows.length,
      efficient_power_MW_each: values[0],
      raw_group_sum_MW: values.reduce((a, b) => a + b, 0),
      normalized_group_sum_MW: values[0],
      source_rows_identical: true,
    });
  } else {
    duplicateNormalizedSum += values.reduce((a, b) => a + b, 0);
  }
}

const productionExceptions = [];
for (const year of [2019, 2020, 2021, 2022]) {
  const payload = JSON.parse(await fs.readFile(path.join(rawDir, `thermoelectric-production_${year}_RAW.json`), "utf8"));
  const rows = payload.thermoelectric;
  const groups = groupRows(rows, (row) => [row.year, normalize(row.region), normalize(row.province), normalize(row.category), normalize(row.subcategory)].join("\u0000"));
  for (const [key, items] of groups) {
    if (items.length <= 2) continue;
    const values = items.map((row) => parse(row.production_GWh)).sort((a, b) => a - b);
    const zeros = values.filter((value) => value === 0);
    const positives = values.filter((value) => value > 0);
    const rulePass = items.length === 4 && zeros.length === 2 && positives.length === 2;
    productionExceptions.push({
      year,
      visible_key: key.split("\u0000").join(" | "),
      region: items[0].region,
      province: items[0].province,
      category: items[0].category,
      subcategory: items[0].subcategory,
      values_GWh_sorted: values,
      zero_count: zeros.length,
      positive_count: positives.length,
      structural_rule_pass: rulePass,
      inferred_group_netta_GWh: rulePass ? positives[0] : null,
      inferred_group_lorda_GWh: rulePass ? positives[1] : null,
      method: rulePass ? "TWO_ZERO_INACTIVE_PAIR_PLUS_SORTED_POSITIVE_ACTIVE_PAIR" : "UNRESOLVED",
    });
  }
}

const renewableBlankSummary = [];
for (const year of [2019, 2020, 2021, 2022, 2023, 2024]) {
  const capPayload = JSON.parse(await fs.readFile(path.join(rawDir, `renewable-source-capacity_${year}_RAW.json`), "utf8"));
  const prodPayload = JSON.parse(await fs.readFile(path.join(rawDir, `renewable-sources-production_${year}_RAW.json`), "utf8"));
  const prodNet = prodPayload.renewable_sources.filter((row) => row.production_type === "Netta");
  const prodByKey = new Map(prodNet.map((row) => [[row.year, normalize(row.region), normalize(row.province), normalize(row.renewable_source)].join("\u0000"), parse(row.production_GWh)]));
  const capNet = capPayload.renewable_sources.filter((row) => row.capacity_type === "Netta");
  const sources = [...new Set(capNet.map((row) => row.source))];
  for (const source of sources) {
    const sourceRows = capNet.filter((row) => row.source === source);
    const blankRows = sourceRows.filter((row) => parse(row.efficient_power_MW) === null);
    const numericRows = sourceRows.filter((row) => parse(row.efficient_power_MW) !== null);
    const blankWithPositiveGeneration = blankRows.filter((row) => {
      const key = [row.year, normalize(row.region), normalize(row.province), normalize(source === "Idrico" ? "Idrico" : source)].join("\u0000");
      return Number(prodByKey.get(key) ?? 0) > 0;
    });
    renewableBlankSummary.push({
      year,
      source,
      source_rows: sourceRows.length,
      numeric_rows: numericRows.length,
      blank_rows: blankRows.length,
      numeric_sum_MW: Number(numericRows.reduce((sum, row) => sum + parse(row.efficient_power_MW), 0).toFixed(9)),
      blank_rows_with_positive_generation: blankWithPositiveGeneration.length,
      blank_positive_generation_GWh: Number(blankWithPositiveGeneration.reduce((sum, row) => {
        const key = [row.year, normalize(row.region), normalize(row.province), normalize(source)].join("\u0000");
        return sum + Number(prodByKey.get(key) ?? 0);
      }, 0).toFixed(9)),
    });
  }
}

const result = {
  capacity_duplicate_reconciliation: {
    raw_source_sum_MW: Number(rawCapacitySum.toFixed(9)),
    exact_duplicate_normalized_sum_MW: Number(duplicateNormalizedSum.toFixed(9)),
    independent_terna_published_control_MW_rounded_0_1: 60330.4,
    exact_duplicate_group_count: exactDuplicateGroups.length,
    exact_duplicate_groups: exactDuplicateGroups,
  },
  fuel_cell_production_exception_reconstruction: {
    exception_group_count: productionExceptions.length,
    structural_rule_pass_count: productionExceptions.filter((row) => row.structural_rule_pass).length,
    structural_rule_fail_count: productionExceptions.filter((row) => !row.structural_rule_pass).length,
    inferred_total_netta_GWh: Number(productionExceptions.reduce((sum, row) => sum + Number(row.inferred_group_netta_GWh ?? 0), 0).toFixed(9)),
    groups: productionExceptions,
  },
  renewable_capacity_blank_summary: renewableBlankSummary,
};
await fs.writeFile(path.join(phaseRoot, "historical_baseline", "qa", "PHASE2_EDGE_CASE_AUDIT.json"), `${JSON.stringify(result, null, 2)}\n`, "utf8");
console.log(JSON.stringify({
  capacity_duplicate_reconciliation: result.capacity_duplicate_reconciliation,
  fuel_cell_production_exception_reconstruction: result.fuel_cell_production_exception_reconstruction,
  renewable_blank_totals: renewableBlankSummary.filter((row) => row.blank_rows > 0),
}, null, 2));

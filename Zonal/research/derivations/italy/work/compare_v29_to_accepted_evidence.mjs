import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const phaseRoot = path.resolve(import.meta.dirname, "..");
const releaseRoot = path.resolve(phaseRoot, "..");
const currentPath = path.join(releaseRoot, "Fase_5_PyPSA_Eur_Italia_2040_2050_Model_Ready_v2.9_MEM_ARCHITECTURE_REFINED.xlsx");
const evidencePath = `${currentPath}.inspect.ndjson`;
const qaPath = path.join(phaseRoot, "qa", "MEM_v2.9_ACCEPTED_EVIDENCE_SEMANTIC_COMPARISON.json");
const ooxmlQaPath = path.join(phaseRoot, "qa", "MEM_v2.9_CURRENT_OOXML_SEMANTIC_INVENTORY.json");
const acceptedSha256 = "4a59c80bdf4da33d825ff94ea5617c03c325ca6fbc26233e7ed633ed6f910640";

const sha256File = async (file) => crypto.createHash("sha256").update(await fs.readFile(file)).digest("hex");
const parseNdjson = (text) => text.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
const comparable = (value) => {
  if (value === undefined || value === null || value === "") return null;
  if (typeof value === "number") return Number(value.toPrecision(15));
  return value;
};
const equalValue = (a, b) => {
  const aa = comparable(a);
  const bb = comparable(b);
  if (typeof aa === "number" && typeof bb === "number") return Math.abs(aa - bb) <= Math.max(1e-10, 1e-12 * Math.max(Math.abs(aa), Math.abs(bb)));
  return aa === bb;
};
const stable = (value) => {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().filter((key) => key !== "styleId").map((key) => [key, stable(value[key])]));
  }
  return value;
};
const normalizeStyle = (style) => {
  const normalized = stable(style);
  const normalizeFill = (fill) => {
    if (!fill || typeof fill !== "object") return fill;
    const out = { ...fill };
    delete out.type;
    if (out.pattern?.patternType === 2) delete out.pattern;
    return out;
  };
  if (normalized.fill) normalized.fill = normalizeFill(normalized.fill);
  if (normalized.font?.fill) normalized.font.fill = normalizeFill(normalized.font.fill);
  return normalized;
};
const keyOf = (record) => `${record.sheet ?? ""}|${record.address ?? record.for ?? record.target ?? ""}`;
const collectDifferencePaths = (left, right, prefix = "", counts = new Map(), transitions = new Map()) => {
  if (JSON.stringify(left) === JSON.stringify(right)) return counts;
  if (left && right && typeof left === "object" && typeof right === "object" && !Array.isArray(left) && !Array.isArray(right)) {
    for (const key of new Set([...Object.keys(left), ...Object.keys(right)])) {
      collectDifferencePaths(left[key], right[key], prefix ? `${prefix}.${key}` : key, counts, transitions);
    }
    return counts;
  }
  const property = prefix || "<root>";
  counts.set(property, (counts.get(property) ?? 0) + 1);
  const transition = `${property}: ${JSON.stringify(left ?? null)} -> ${JSON.stringify(right ?? null)}`;
  transitions.set(transition, (transitions.get(transition) ?? 0) + 1);
  return counts;
};

await fs.mkdir(path.dirname(qaPath), { recursive: true });
const evidence = parseNdjson(await fs.readFile(evidencePath, "utf8"));
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(currentPath));

const evidenceSheets = evidence.filter((r) => r.kind === "sheet").sort((a, b) => a.index - b.index);
const evidenceTables = evidence.filter((r) => r.kind === "table");
const evidenceFormulas = evidence.filter((r) => r.kind === "formula");
const evidenceStyles = evidence.filter((r) => r.kind === "computedStyle");
const evidenceThreads = evidence.filter((r) => r.kind === "thread");
const evidenceCf = evidence.filter((r) => r.kind === "conditionalFormatting");

const tableDiffs = [];
const currentTableRecords = [];
for (const expected of evidenceTables) {
  const inspected = parseNdjson((await workbook.inspect({
    kind: "table",
    sheetId: expected.sheet,
    range: expected.address,
    tableMaxRows: expected.rows,
    tableMaxCols: expected.cols,
    tableMaxCellChars: 10000,
    maxChars: 5000000,
  })).ndjson).find((r) => r.kind === "table");
  if (!inspected) {
    tableDiffs.push({ sheet: expected.sheet, address: expected.address, issue: "CURRENT_TABLE_INSPECTION_MISSING" });
    continue;
  }
  currentTableRecords.push(inspected);
  const diffs = [];
  for (let row = 0; row < expected.rows; row += 1) {
    for (let col = 0; col < expected.cols; col += 1) {
      if (!equalValue(expected.values?.[row]?.[col], inspected.values?.[row]?.[col])) {
        diffs.push({ row: row + 1, col: col + 1, accepted: expected.values?.[row]?.[col] ?? null, current: inspected.values?.[row]?.[col] ?? null });
      }
    }
  }
  if (diffs.length) tableDiffs.push({ sheet: expected.sheet, address: expected.address, difference_count: diffs.length, sample: diffs.slice(0, 25) });
}
const sheetDiffs = [];
for (let i = 0; i < evidenceSheets.length; i += 1) {
  const e = evidenceSheets[i];
  const c = currentTableRecords.find((record) => record.sheet === e.name);
  if (!c || c.address !== e.address) sheetDiffs.push({ index: i, accepted_evidence: e, current_table_region: c ?? null });
}

const currentFormulas = [];
const currentStyles = [];
const currentCf = [];
for (const sheet of evidenceSheets) {
  const formulaResult = await workbook.inspect({ kind: "formula", sheetId: sheet.name, range: sheet.address, maxChars: 3000000, options: { maxResults: 10000 } });
  currentFormulas.push(...parseNdjson(formulaResult.ndjson).filter((r) => r.kind === "formula"));
  const styleResult = await workbook.inspect({ kind: "computedStyle", sheetId: sheet.name, range: sheet.address, maxChars: 10000000, options: { maxResults: 20000 } });
  currentStyles.push(...parseNdjson(styleResult.ndjson).filter((r) => r.kind === "computedStyle"));
  const cfResult = await workbook.inspect({ kind: "conditionalFormatting", sheetId: sheet.name, range: sheet.address, maxChars: 500000, options: { maxResults: 1000 } });
  currentCf.push(...parseNdjson(cfResult.ndjson).filter((r) => r.kind === "conditionalFormatting"));
}

const compareMaps = (expectedRecords, currentRecords, canonicalize = (r) => r) => {
  const expectedMap = new Map(expectedRecords.map((r) => [keyOf(r), canonicalize(r)]));
  const currentMap = new Map(currentRecords.map((r) => [keyOf(r), canonicalize(r)]));
  const keys = [...new Set([...expectedMap.keys(), ...currentMap.keys()])].sort();
  const diffs = [];
  const sheetCounts = new Map();
  const propertyCounts = new Map();
  const transitionCounts = new Map();
  for (const key of keys) {
    const e = expectedMap.get(key);
    const c = currentMap.get(key);
    if (JSON.stringify(e) !== JSON.stringify(c)) {
      diffs.push({ key, accepted: e ?? null, current: c ?? null });
      const sheet = key.split("|")[0];
      sheetCounts.set(sheet, (sheetCounts.get(sheet) ?? 0) + 1);
      collectDifferencePaths(e, c, "", propertyCounts, transitionCounts);
    }
  }
  return {
    accepted_count: expectedRecords.length,
    current_count: currentRecords.length,
    difference_count: diffs.length,
    difference_counts_by_sheet: Object.fromEntries([...sheetCounts.entries()].sort()),
    difference_property_counts: Object.fromEntries([...propertyCounts.entries()].sort()),
    difference_transition_counts: Object.fromEntries([...transitionCounts.entries()].sort()),
    difference_sample: diffs.slice(0, 25),
  };
};

const formulaComparison = compareMaps(evidenceFormulas, currentFormulas, (r) => ({ formula: r.formula }));
const styleComparison = compareMaps(evidenceStyles, currentStyles, (r) => normalizeStyle(r.style));
const cfComparison = compareMaps(evidenceCf, currentCf, (r) => ({ type: r.type, priority: r.priority, operator: r.operator ?? "", text: r.text ?? null, formula: r.formula ?? null }));

const currentThreads = parseNdjson((await workbook.inspect({ kind: "thread", maxChars: 500000, options: { maxResults: 1000 } })).ndjson).filter((r) => r.kind === "thread");
const threadCanonical = (r) => ({ sheet: r.sheet, target: r.target, text: r.text, comments: (r.comments ?? []).map((c) => ({ text: c.text, authorId: c.authorId ?? null })) });
const threadComparison = compareMaps(evidenceThreads, currentThreads, threadCanonical);

const currentSha256 = await sha256File(currentPath);
const ooxmlQa = JSON.parse(await fs.readFile(ooxmlQaPath, "utf8"));
const directOoxmlFormulaComparison = {
  accepted_anchor_count: ooxmlQa.accepted_formula_record_count,
  current_physical_formula_cell_count: ooxmlQa.current_physical_formula_cell_count,
  difference_count: ooxmlQa.accepted_formula_difference_count,
  difference_sample: ooxmlQa.accepted_formula_differences.slice(0, 25),
  interpretation: "Direct OOXML/openpyxl comparison is controlling. Artifact inspection can omit expanded/shared formula members and is retained only as a diagnostic.",
};
const allSemanticComparisonsPass = sheetDiffs.length === 0 && tableDiffs.length === 0 && directOoxmlFormulaComparison.difference_count === 0 && styleComparison.difference_count === 0 && threadComparison.difference_count === 0 && cfComparison.difference_count === 0;
const result = {
  generated_at: new Date().toISOString(),
  accepted_binary_sha256: acceptedSha256,
  current_binary_path: currentPath,
  current_binary_sha256: currentSha256,
  binary_match: currentSha256 === acceptedSha256,
  evidence_path: evidencePath,
  evidence_file_mtime_utc: (await fs.stat(evidencePath)).mtime.toISOString(),
  current_binary_mtime_utc: (await fs.stat(currentPath)).mtime.toISOString(),
  accepted_evidence_counts: {
    sheets: evidenceSheets.length,
    tables: evidenceTables.length,
    formulas: evidenceFormulas.length,
    computed_styles: evidenceStyles.length,
    threads: evidenceThreads.length,
    conditional_formatting_rules: evidenceCf.length,
  },
  sheet_topology: { accepted_count: evidenceSheets.length, current_count: currentTableRecords.length, difference_count: sheetDiffs.length, difference_sample: sheetDiffs.slice(0, 20) },
  table_values: { accepted_count: evidenceTables.length, difference_count: tableDiffs.length, difference_sample: tableDiffs.slice(0, 20) },
  formulas_direct_ooxml_controlling: directOoxmlFormulaComparison,
  formulas_artifact_inspection_diagnostic_only: formulaComparison,
  computed_styles_normalized_for_equivalent_solid_fill_serializations: styleComparison,
  threads: threadComparison,
  conditional_formatting: cfComparison,
  all_available_pre_modification_semantic_evidence_matches: allSemanticComparisonsPass,
  provenance_decision: currentSha256 === acceptedSha256
    ? "ACCEPTED_BINARY_FOUND"
    : allSemanticComparisonsPass
      ? "BINARY_MISMATCH_BUT_ALL_AVAILABLE_PRE_MODIFICATION_SEMANTIC_EVIDENCE_MATCHES; ACCEPTED_BYTE_IDENTITY_NOT_PROVEN"
      : "BINARY_AND_SEMANTIC_EVIDENCE_MISMATCH; DO_NOT_PROMOTE",
};
await fs.writeFile(qaPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
process.stdout.write(`${JSON.stringify({
  current_binary_sha256: result.current_binary_sha256,
  binary_match: result.binary_match,
  sheet_topology_differences: result.sheet_topology.difference_count,
  table_value_differences: result.table_values.difference_count,
  direct_ooxml_formula_differences: result.formulas_direct_ooxml_controlling.difference_count,
  normalized_style_differences: result.computed_styles_normalized_for_equivalent_solid_fill_serializations.difference_count,
  normalized_style_difference_properties: result.computed_styles_normalized_for_equivalent_solid_fill_serializations.difference_property_counts,
  normalized_style_difference_transitions: result.computed_styles_normalized_for_equivalent_solid_fill_serializations.difference_transition_counts,
  normalized_style_difference_sheets: result.computed_styles_normalized_for_equivalent_solid_fill_serializations.difference_counts_by_sheet,
  thread_differences: result.threads.difference_count,
  conditional_formatting_differences: result.conditional_formatting.difference_count,
  provenance_decision: result.provenance_decision,
  output: qaPath,
}, null, 2)}\n`);
process.exitCode = 0;

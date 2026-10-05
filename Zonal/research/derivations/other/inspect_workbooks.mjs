import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const [baselinePath, successorPath, outputDir] = process.argv.slice(2);
if (!baselinePath || !successorPath || !outputDir) {
  throw new Error("Usage: node inspect_workbooks.mjs BASELINE SUCCESSOR OUTPUT_DIR");
}

await fs.mkdir(outputDir, { recursive: true });

function safeName(value) {
  return value.replace(/[\\/:*?\"<>|]/g, "_");
}

function countFormulas(matrix) {
  let count = 0;
  for (const row of matrix ?? []) {
    for (const cell of row ?? []) {
      if (typeof cell === "string" && cell.startsWith("=")) count += 1;
    }
  }
  return count;
}

function countNonBlank(matrix) {
  let count = 0;
  for (const row of matrix ?? []) {
    for (const cell of row ?? []) {
      if (cell !== null && cell !== undefined && cell !== "") count += 1;
    }
  }
  return count;
}

function recordsFromNdjson(ndjson) {
  return String(ndjson ?? "")
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => {
      try { return JSON.parse(line); } catch { return null; }
    })
    .filter(Boolean);
}

async function loadWorkbook(filePath) {
  const input = await FileBlob.load(filePath);
  return SpreadsheetFile.importXlsx(input);
}

async function sheetNames(workbook) {
  const result = await workbook.inspect({ kind: "sheet", include: "id,name", maxChars: 20000 });
  const records = recordsFromNdjson(result.ndjson);
  const names = [];
  for (const record of records) {
    if (typeof record.name === "string") names.push(record.name);
    if (record.sheet && typeof record.sheet.name === "string") names.push(record.sheet.name);
  }
  return [...new Set(names)];
}

async function inspectOne(label, filePath) {
  const workbook = await loadWorkbook(filePath);
  const overview = await workbook.inspect({
    kind: "workbook,sheet,table,definedName,drawing",
    maxChars: 30000,
    tableMaxRows: 5,
    tableMaxCols: 8,
    tableMaxCellChars: 100,
  });
  const names = await sheetNames(workbook);
  const sheets = [];
  const renderDir = path.join(outputDir, `${label}_renders`);
  await fs.mkdir(renderDir, { recursive: true });

  for (const name of names) {
    const sheet = workbook.worksheets.getItem(name);
    const used = sheet.getUsedRange();
    const values = used ? used.values : [];
    const formulas = used ? used.formulas : [];
    const sheetInfo = {
      name,
      usedAddress: used?.address ?? null,
      rowCount: values?.length ?? 0,
      columnCount: values?.[0]?.length ?? 0,
      nonBlankCellCount: countNonBlank(values),
      formulaCellCount: countFormulas(formulas),
    };
    sheets.push(sheetInfo);

    const preview = await workbook.render({
      sheetName: name,
      autoCrop: "all",
      scale: 1,
      format: "png",
    });
    await fs.writeFile(
      path.join(renderDir, `${String(sheets.length).padStart(2, "0")}_${safeName(name)}.png`),
      new Uint8Array(await preview.arrayBuffer()),
    );
  }

  const formulaErrors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 500 },
    summary: `${label} formula error scan`,
    maxChars: 30000,
  });

  const keyRanges = {};
  const desired = [
    ["00_README", "A1:K80"],
    ["02_SCENARIOS", "A1:Z120"],
    ["04_GENERATION_BY_ZONE", "A1:Z140"],
    ["09_TECH_COSTS", "A1:Z160"],
    ["10_PYPSA_INPUT_MAP", "A1:Z160"],
    ["12_ASSUMPTIONS_QA", "A1:Z220"],
  ];
  for (const [sheetId, range] of desired) {
    if (!names.includes(sheetId)) continue;
    const result = await workbook.inspect({
      kind: "table,formula,computedStyle",
      sheetId,
      range,
      maxChars: 30000,
      tableMaxRows: 220,
      tableMaxCols: 26,
      tableMaxCellChars: 300,
      options: { maxResults: 1000 },
    });
    keyRanges[sheetId] = result.ndjson;
  }

  const summary = {
    label,
    filePath,
    overview: overview.ndjson,
    sheets,
    formulaErrors: formulaErrors.ndjson,
    keyRanges,
  };
  await fs.writeFile(path.join(outputDir, `${label}_inspection.json`), JSON.stringify(summary, null, 2), "utf8");
  return summary;
}

const baseline = await inspectOne("v28_baseline", baselinePath);
const successor = await inspectOne("partial_v29", successorPath);

console.log(JSON.stringify({
  baseline: { sheets: baseline.sheets, formulaErrors: baseline.formulaErrors },
  partialSuccessor: { sheets: successor.sheets, formulaErrors: successor.formulaErrors },
}, null, 2));

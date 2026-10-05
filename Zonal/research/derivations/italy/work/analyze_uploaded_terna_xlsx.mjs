import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
import {
  detectTernaDecimalConventions,
  parseTernaNumber,
} from "../scripts/terna_numeric_parser.mjs";

const memRoot = path.resolve("../../../..");
const qaDir = path.resolve("../qa");
await fs.mkdir(qaDir, { recursive: true });

const specs = [
  { filename: "Export-DownloadCenterFile-20260901-085449.xlsx", expected: "production" },
  { filename: "Export-DownloadCenterFile-20260901-085433.xlsx", expected: "capacity" },
];

const uniqueSorted = (rows, index) =>
  [...new Set(rows.map((row) => row[index]))].sort((a, b) =>
    String(a).localeCompare(String(b), "it"),
  );
const sum = (values) => values.reduce((total, value) => total + value, 0);
const group = (rows, keyFn, valueFn) => {
  const result = new Map();
  for (const row of rows) {
    const key = keyFn(row);
    result.set(key, (result.get(key) ?? 0) + valueFn(row));
  }
  return [...result.entries()]
    .map(([key, value]) => ({ key, value }))
    .sort((a, b) => a.key.localeCompare(b.key, "it"));
};

const reports = [];
for (const spec of specs) {
  const sourcePath = path.join(memRoot, spec.filename);
  const bytes = await fs.readFile(sourcePath);
  const sha256 = crypto.createHash("sha256").update(bytes).digest("hex");
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(sourcePath));
  const sheet = workbook.worksheets.getItem("Export");
  const used = sheet.getUsedRange();
  const values = used.values;
  const formulas = used.formulas;
  const headers = values[0];
  const rows = values.slice(1);
  const formulaCount = formulas.flat().filter((value) => typeof value === "string" && value.startsWith("=")).length;
  const valueIndex = spec.expected === "capacity" ? 6 : 5;
  const indexedRows = rows.map((row, index) => ({ row_number: index + 2, raw: row }));
  const blankValueRows = indexedRows.filter(
    (row) => row.raw[valueIndex] === null || row.raw[valueIndex] === undefined || String(row.raw[valueIndex]).trim() === "",
  );
  const valueRows = indexedRows.filter((row) => !blankValueRows.includes(row));
  const convention = detectTernaDecimalConventions(valueRows.map((row) => row.raw[valueIndex]));
  const parsedRows = valueRows.map((row) => ({
    ...row,
    parsed: parseTernaNumber(row.raw[valueIndex], convention),
  }));
  const grainIndexes = spec.expected === "capacity" ? [0, 1, 2, 3, 4, 5] : [0, 1, 2, 3, 4];
  const grain = new Map();
  for (const row of parsedRows) {
    const key = grainIndexes.map((i) => String(row.raw[i])).join("\u0000");
    const items = grain.get(key) ?? [];
    items.push(row);
    grain.set(key, items);
  }
  const duplicateGroups = [...grain.entries()]
    .filter(([, items]) => items.length > 1)
    .map(([key, items]) => ({
      key: key.split("\u0000"),
      count: items.length,
      row_numbers: items.map((item) => item.row_number),
      raw_values: items.map((item) => item.parsed.raw_value),
      parsed_values: items.map((item) => item.parsed.value),
      exact_duplicate_values: new Set(items.map((item) => item.parsed.value)).size === 1,
    }));
  const missingByColumn = headers.map((header, column) => ({
    header,
    missing: rows.filter((row) => row[column] === null || row[column] === undefined || String(row[column]).trim() === "").length,
  }));

  const report = {
    filename: spec.filename,
    expected_dataset: spec.expected,
    byte_size: bytes.length,
    sha256,
    sheet_name: "Export",
    used_range: used.address,
    headers,
    data_rows: rows.length,
    columns: headers.length,
    formula_count: formulaCount,
    years: uniqueSorted(rows, 0),
    regions: uniqueSorted(rows, spec.expected === "capacity" ? 2 : 1),
    provinces: uniqueSorted(rows, spec.expected === "capacity" ? 3 : 2),
    categories: uniqueSorted(rows, spec.expected === "capacity" ? 4 : 3),
    subcategories: uniqueSorted(rows, spec.expected === "capacity" ? 5 : 4),
    missing_by_column: missingByColumn,
    blank_numeric_rows: blankValueRows.map((row) => ({ row_number: row.row_number, row: row.raw })),
    duplicate_group_count: duplicateGroups.length,
    duplicate_excess_rows: sum(duplicateGroups.map((item) => item.count - 1)),
    duplicate_groups: duplicateGroups,
    numeric_parser: {
      convention: {
        decimal_separators: [...convention.decimalSeparators],
        evidence: convention.evidence,
        detection_rule: convention.detection_rule,
      },
      cell_types: group(parsedRows, (row) => row.parsed.raw_cell_type, () => 1),
      parser_statuses: group(parsedRows, (row) => row.parsed.parser_status, () => 1),
      negative_count: parsedRows.filter((row) => row.parsed.value < 0).length,
      total: sum(parsedRows.map((row) => row.parsed.value)),
    },
  };

  if (spec.expected === "capacity") {
    report.capacity_types = uniqueSorted(rows, 1);
    report.rows_by_capacity_type = group(rows, (row) => String(row[1]), () => 1);
    report.total_MW_by_capacity_type = group(
      parsedRows,
      (row) => String(row.raw[1]),
      (row) => row.parsed.value,
    );
    const net = parsedRows.filter((row) => row.raw[1] === "Netta");
    const netGrain = new Map();
    for (const row of net) {
      const key = [row.raw[0], row.raw[2], row.raw[3], row.raw[4], row.raw[5]].join("\u0000");
      const items = netGrain.get(key) ?? [];
      items.push(row);
      netGrain.set(key, items);
    }
    const netDuplicates = [...netGrain.entries()]
      .filter(([, items]) => items.length > 1)
      .map(([key, items]) => ({
        key: key.split("\u0000"),
        count: items.length,
        row_numbers: items.map((item) => item.row_number),
        values_MW: items.map((item) => item.parsed.value),
      }));
    report.netta = {
      rows: net.length,
      total_MW: sum(net.map((row) => row.parsed.value)),
      regions: uniqueSorted(net.map((row) => row.raw), 2),
      provinces: uniqueSorted(net.map((row) => row.raw), 3),
      categories: uniqueSorted(net.map((row) => row.raw), 4),
      subcategories: uniqueSorted(net.map((row) => row.raw), 5),
      duplicate_group_count: netDuplicates.length,
      duplicate_excess_rows: sum(netDuplicates.map((item) => item.count - 1)),
      duplicate_groups: netDuplicates,
      MW_by_subcategory: group(net, (row) => String(row.raw[5]), (row) => row.parsed.value),
      MW_by_category: group(net, (row) => String(row.raw[4]), (row) => row.parsed.value),
    };
  } else {
    report.production_GWh_by_subcategory = group(
      parsedRows,
      (row) => String(row.raw[4]),
      (row) => row.parsed.value,
    );
    report.production_GWh_by_category = group(
      parsedRows,
      (row) => String(row.raw[3]),
      (row) => row.parsed.value,
    );
  }

  const preview = await workbook.render({
    sheetName: "Export",
    range: spec.expected === "capacity" ? "A1:G28" : "A1:F28",
    scale: 1.2,
    format: "png",
  });
  await fs.writeFile(
    path.join(qaDir, `${spec.expected}_download_center_preview.png`),
    new Uint8Array(await preview.arrayBuffer()),
  );
  reports.push(report);
}

await fs.writeFile(
  path.join(qaDir, "uploaded_terna_xlsx_full_analysis.json"),
  `${JSON.stringify(reports, null, 2)}\n`,
  "utf8",
);

for (const report of reports) {
  console.log(JSON.stringify({
    filename: report.filename,
    expected_dataset: report.expected_dataset,
    used_range: report.used_range,
    rows: report.data_rows,
    years: report.years,
    regions: report.regions.length,
    provinces: report.provinces.length,
    categories: report.categories,
    subcategories: report.subcategories,
    formula_count: report.formula_count,
    duplicate_group_count: report.duplicate_group_count,
    numeric_parser: report.numeric_parser,
    capacity_types: report.capacity_types,
    rows_by_capacity_type: report.rows_by_capacity_type,
    total_MW_by_capacity_type: report.total_MW_by_capacity_type,
    netta: report.netta,
    production_total_GWh: report.expected_dataset === "production" ? report.numeric_parser.total : undefined,
  }, null, 2));
}

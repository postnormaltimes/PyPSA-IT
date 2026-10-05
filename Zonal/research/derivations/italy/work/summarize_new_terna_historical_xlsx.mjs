import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const dir = "C:/Users/EmilianoBarin/Downloads";
const defaultNames = [
  "Export-DownloadCenterFile-20260901-100929.xlsx",
  "Export-DownloadCenterFile-20260901-100933.xlsx",
  "Export-DownloadCenterFile-20260901-100940.xlsx",
  "Export-DownloadCenterFile-20260901-101019.xlsx",
  "Export-DownloadCenterFile-20260901-101029.xlsx",
  "Export-DownloadCenterFile-20260901-101036.xlsx",
  "Export-DownloadCenterFile-20260901-101113.xlsx",
];
const names = process.argv.length > 2 ? process.argv.slice(2) : defaultNames;

const summaries = [];
for (const name of names) {
  const file = path.join(dir, name);
  const bytes = await fs.readFile(file);
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(file));
  const sheet = workbook.worksheets.getItemAt(0);
  const values = sheet.getUsedRange().values;
  const header = values[0];
  const footerRows = values.filter((row) => String(row?.[0] ?? "").startsWith("Applied filters:"));
  const data = values.slice(1).filter((row) => !String(row?.[0] ?? "").startsWith("Applied filters:") && row.some((v) => v !== null && v !== ""));
  const uniques = {};
  for (let c = 0; c < header.length; c++) {
    const vals = [...new Set(data.map((row) => row[c]).filter((v) => v !== null && v !== "").map((v) => String(v)))].sort();
    uniques[header[c]] = { count: vals.length, values: vals.length <= 50 ? vals : vals.slice(0, 50) };
  }
  const numericColumn = header.length - 1;
  const numericValues = data.map((r) => r[numericColumn]).filter((v) => typeof v === "number" && Number.isFinite(v));
  summaries.push({
    name,
    byte_size: bytes.length,
    sha256: crypto.createHash("sha256").update(bytes).digest("hex"),
    sheet: sheet.name,
    used_range: sheet.getUsedRange().address,
    header,
    data_rows: data.length,
    footer_rows: footerRows,
    uniques,
    numeric_count: numericValues.length,
    numeric_sum: numericValues.reduce((a, b) => a + b, 0),
    null_numeric_count: data.length - numericValues.length,
    first_rows: data.slice(0, 5),
    last_rows: data.slice(-5),
  });
}

console.log(JSON.stringify(summaries, null, 2));

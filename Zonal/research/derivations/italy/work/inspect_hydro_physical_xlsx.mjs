import path from "node:path";
import { SpreadsheetFile, FileBlob } from "@oai/artifact-tool";

const phaseRoot = path.resolve(import.meta.dirname, "..");
const file = path.join(
  phaseRoot,
  "raw",
  "hydro_physical",
  "Italian_Hydropower_Programmable_Plants_Database_v2_RAW.xlsx",
);

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(file));
const sheetNames = workbook.worksheets.items.map((sheet) => sheet.name);
const out = { file, sheetNames, sheets: {} };
for (const sheetName of sheetNames) {
  const sheet = workbook.worksheets.getItem(sheetName);
  const used = sheet.getUsedRange();
  out.sheets[sheetName] = {
    rowCount: used.values.length,
    columnCount: Math.max(0, ...used.values.map((row) => row.length)),
    preview: used.values.slice(0, 60),
  };
}
process.stdout.write(`${JSON.stringify(out, null, 2)}\n`);

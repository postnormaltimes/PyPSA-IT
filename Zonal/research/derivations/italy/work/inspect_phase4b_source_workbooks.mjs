import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workspace = path.resolve(import.meta.dirname, "..", "..", "..", "..");
const files = [
  ["FASE3D", path.join(workspace, "01_WORKBOOKS", "Fase_3D_Costi_Efficienze_Parametri_Tecnici.xlsx")],
];

for (const [label, file] of files) {
  const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(file));
  const sheetInfo = await wb.inspect({ kind: "sheet", include: "id,name", maxChars: 20000, options: { maxResults: 200 } });
  process.stdout.write(`\n=== ${label} SHEETS ===\n${sheetInfo.ndjson}\n`);
  const targets = ["TECH_PARAMETERS", "FUEL_CO2", "TECH_DATA_CANDIDATES", "3D_DECISIONS", "3D_QA"];
  for (const sheetName of targets) {
    try {
      const sheet = wb.worksheets.getItem(sheetName);
      const used = sheet.getUsedRange();
      const table = await wb.inspect({
        kind: "table,formula",
        sheetId: sheetName,
        range: used.address,
        tableMaxRows: 110,
        tableMaxCols: 30,
        tableMaxCellChars: 300,
        maxChars: 60000,
        options: { maxResults: 2500 },
      });
      process.stdout.write(`\n--- ${label}:${sheetName}:${used.address} ---\n${table.ndjson}\n`);
    } catch (error) {
      process.stdout.write(`\n--- ${label}:${sheetName}:ERROR ---\n${error.message}\n`);
    }
  }
}

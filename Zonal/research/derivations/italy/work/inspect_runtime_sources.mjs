import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workspace = path.resolve(import.meta.dirname, "..", "..", "..", "..");
const books = [
  ["FASE3A", path.join(workspace, "01_WORKBOOKS", "Fase_3A_Scenari_Domanda_Capacita.xlsx")],
  ["FASE3B", path.join(workspace, "01_WORKBOOKS", "Fase_3B_Accumuli_Idroelettrico_Flessibilita.xlsx")],
  ["FASE3C", path.join(workspace, "01_WORKBOOKS", "Fase_3C_Rete_Interzonali_Interconnessioni.xlsx")],
  ["PYPSA_SCENARIOS", path.join(workspace, "PyPSA_IT_2040_2050_SCENARIOS.xlsx")],
];

for (const [label, file] of books) {
  const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(file));
  const sheetInfo = await wb.inspect({
    kind: "sheet",
    include: "id,name",
    maxChars: 25000,
    options: { maxResults: 250 },
  });
  process.stdout.write(`\n=== ${label} | ${file} ===\n${sheetInfo.ndjson}\n`);
  const names = [];
  for (const line of sheetInfo.ndjson.trim().split(/\r?\n/)) {
    try {
      const obj = JSON.parse(line);
      if (obj.name) names.push(obj.name);
    } catch {}
  }
  for (const sheetName of names) {
    try {
      const sheet = wb.worksheets.getItem(sheetName);
      const used = sheet.getUsedRange();
      if (!used) continue;
      const table = await wb.inspect({
        kind: "table,formula",
        sheetId: sheetName,
        range: used.address,
        tableMaxRows: 120,
        tableMaxCols: 40,
        tableMaxCellChars: 220,
        maxChars: 50000,
        options: { maxResults: 3500 },
      });
      process.stdout.write(`\n--- ${label}:${sheetName}:${used.address} ---\n${table.ndjson}\n`);
    } catch (error) {
      process.stdout.write(`\n--- ${label}:${sheetName}:ERROR ---\n${error.message}\n`);
    }
  }
}

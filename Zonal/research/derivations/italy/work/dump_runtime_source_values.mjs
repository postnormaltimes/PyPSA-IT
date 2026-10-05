import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workspace = path.resolve(import.meta.dirname, "..", "..", "..", "..");
const specs = [
  [path.join(workspace, "01_WORKBOOKS", "Fase_3C_Rete_Interzonali_Interconnessioni.xlsx"), [
    "INTERZONAL_CAPACITY", "FOREIGN_INTERCONNECTORS", "GRID_TOPOLOGY", "GRID_VARIANTS", "3C_DECISIONS"
  ]],
  [path.join(workspace, "PyPSA_IT_2040_2050_SCENARIOS.xlsx"), [
    "06_DEMAND_BY_ZONE", "07_INTERZONAL_BY_ZONE", "08_FOREIGN_BY_ZONE"
  ]],
];
const requested = new Set(process.argv.slice(2));

for (const [file, sheets] of specs) {
  const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(file));
  for (const sheetName of sheets) {
    if (requested.size && !requested.has(sheetName)) continue;
    const values = wb.worksheets.getItem(sheetName).getUsedRange().values;
    const rows = values.filter((row) => row.some((v) => v !== null && v !== ""));
    process.stdout.write(`\n===${path.basename(file)}:${sheetName}===\n`);
    process.stdout.write(`${JSON.stringify(rows, null, 2)}\n`);
  }
}

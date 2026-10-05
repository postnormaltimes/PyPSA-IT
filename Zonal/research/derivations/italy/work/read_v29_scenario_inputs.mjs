import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const root = path.resolve(import.meta.dirname, "..", "..");
const file = path.join(root, "Fase_5_PyPSA_Eur_Italia_2040_2050_Model_Ready_v2.9_MEM_ARCHITECTURE_REFINED.xlsx");
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(file));
for (const item of [
  ["05_STORAGE_BY_ZONE", "A20:O34"],
  ["05_STORAGE_BY_ZONE", "A36:O46"],
]) {
  const result = await workbook.inspect({
    kind: "table,formula",
    sheetId: item[0],
    range: item[1],
    tableMaxRows: 60,
    tableMaxCols: 20,
    tableMaxCellChars: 240,
    maxChars: 100000,
    options: { maxResults: 2000 },
  });
  const table = result.ndjson.split("\n").map((line) => JSON.parse(line)).find((row) => row.kind === "table");
  process.stdout.write(`\n===${item[0]}===\n${JSON.stringify(table?.values ?? [], null, 2)}\n`);
}

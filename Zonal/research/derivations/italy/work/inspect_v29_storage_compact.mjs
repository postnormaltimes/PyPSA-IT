import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workspace = path.resolve(import.meta.dirname, "..", "..", "..", "..");
const file = path.join(workspace, "outputs", "01a0595d-8cf1-7202-8ca1-1d3ab732e98e", "Fase_5_PyPSA_Eur_Italia_2040_2050_Model_Ready_v2.9_MEM_ARCHITECTURE_REFINED.xlsx");
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(file));
const sh = wb.worksheets.getItem("05_STORAGE_BY_ZONE");
for (const range of ["A1:P16", "A17:P45", "A46:P80"]) {
  const values = sh.getRange(range).values;
  process.stdout.write(`\n${range}\n${JSON.stringify(values, null, 2)}\n`);
}

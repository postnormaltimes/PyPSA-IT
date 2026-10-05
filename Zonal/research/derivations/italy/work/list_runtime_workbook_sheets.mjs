import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workspace = path.resolve(import.meta.dirname, "..", "..", "..", "..");
for (const [label, rel] of [
  ["FASE3A", ["01_WORKBOOKS", "Fase_3A_Scenari_Domanda_Capacita.xlsx"]],
  ["FASE3B", ["01_WORKBOOKS", "Fase_3B_Accumuli_Idroelettrico_Flessibilita.xlsx"]],
  ["FASE3C", ["01_WORKBOOKS", "Fase_3C_Rete_Interzonali_Interconnessioni.xlsx"]],
  ["PYPSA_SCENARIOS", ["PyPSA_IT_2040_2050_SCENARIOS.xlsx"]],
]) {
  const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(path.join(workspace, ...rel)));
  const result = await wb.inspect({ kind: "sheet", include: "id,name", maxChars: 30000, options: { maxResults: 300 } });
  process.stdout.write(`\n===${label}===\n${result.ndjson}\n`);
}

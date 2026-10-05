import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workspace = path.resolve(import.meta.dirname, "..", "..", "..", "..");
const specs = [
  ["FASE3B", path.join(workspace, "01_WORKBOOKS", "Fase_3B_Accumuli_Idroelettrico_Flessibilita.xlsx"), [
    ["22_HYDRO", "A1:U71"],
    ["20_STORAGE", "A1:AC62"],
  ]],
  ["FASE3C", path.join(workspace, "01_WORKBOOKS", "Fase_3C_Rete_Interzonali_Interconnessioni.xlsx"), [
    ["INTERZONAL_CAPACITY", "A1:U188"],
    ["FOREIGN_INTERCONNECTORS", "A1:Z30"],
    ["GRID_TOPOLOGY", "A1:J24"],
    ["GRID_VARIANTS", "A1:O100"],
    ["3C_DECISIONS", "A1:F12"],
  ]],
  ["SCENARIOS", path.join(workspace, "PyPSA_IT_2040_2050_SCENARIOS.xlsx"), [
    ["06_DEMAND_BY_ZONE", "A1:N29"],
    ["07_INTERZONAL_BY_ZONE", "A1:L83"],
    ["08_FOREIGN_BY_ZONE", "A1:Q41"],
  ]],
];

for (const [label, file, sheets] of specs) {
  const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(file));
  for (const [sheetId, range] of sheets) {
    const result = await wb.inspect({
      kind: "table,formula",
      sheetId,
      range,
      tableMaxRows: 220,
      tableMaxCols: 35,
      tableMaxCellChars: 300,
      maxChars: 90000,
      options: { maxResults: 8000 },
    });
    process.stdout.write(`\n===${label}:${sheetId}:${range}===\n${result.ndjson}\n`);
  }
}

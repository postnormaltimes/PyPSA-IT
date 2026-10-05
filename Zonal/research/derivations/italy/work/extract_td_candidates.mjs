import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workspace = path.resolve(import.meta.dirname, "..", "..", "..", "..");
const file = path.join(workspace, "01_WORKBOOKS", "Fase_3D_Costi_Efficienze_Parametri_Tecnici.xlsx");
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(file));
const values = workbook.worksheets.getItem("TECH_DATA_CANDIDATES").getUsedRange().values;
const headerIndex = values.findIndex((row) => row.some((value) => /Record ID|Candidate ID|Provider/.test(String(value).trim())));
if (headerIndex < 0) {
  process.stdout.write(`${JSON.stringify(values.slice(0, 10), null, 2)}\n`);
  process.exit(0);
}
const headers = values[headerIndex].map((value, index) => String(value ?? `column_${index + 1}`).trim());
const rows = values.slice(headerIndex + 1).filter((row) => row.some((value) => value !== null && value !== ""))
  .map((row) => Object.fromEntries(headers.map((header, index) => [header || `column_${index + 1}`, row[index]])));
const wantedTech = /CCGT|OCGT|gas|bio|hydrogen|geothermal|nuclear|battery|hydro|fuel cell|CHP/i;
const wantedParam = /efficien|VOM|fuel|CO2|capture|emission|standing|round.trip/i;
const matches = rows.filter((row) => Object.values(row).some((value) => String(value) === "v0.15.0") && Object.values(row).some((value) => [2040, 2050].includes(Number(value))) && Object.values(row).some((value) => wantedTech.test(String(value))) && Object.values(row).some((value) => wantedParam.test(String(value))));
const v015 = rows.filter((row) => row.Versione === "v0.15.0" && [2040, 2050].includes(Number(row["Anno modello"])));
const uniqueTechnologies = [...new Set(v015.map((row) => row.Tecnologia))].sort();
process.stdout.write(`${JSON.stringify({ headerIndex, headers, sample: rows.slice(0, 2), uniqueTechnologies, matchCount: matches.length, matches }, null, 2)}\n`);

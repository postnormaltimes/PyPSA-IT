import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workspace = path.resolve(import.meta.dirname, "..", "..", "..", "..");

async function load(file) {
  return SpreadsheetFile.importXlsx(await FileBlob.load(file));
}

function asObjects(values, headerRowIndex) {
  const headers = values[headerRowIndex].map((value) => String(value ?? "").trim());
  return values.slice(headerRowIndex + 1)
    .filter((row) => row.some((value) => value !== null && value !== ""))
    .map((row) => Object.fromEntries(headers.map((header, index) => [header || `column_${index + 1}`, row[index]])));
}

const fase3d = await load(path.join(workspace, "01_WORKBOOKS", "Fase_3D_Costi_Efficienze_Parametri_Tecnici.xlsx"));
const techParams = asObjects(fase3d.worksheets.getItem("TECH_PARAMETERS").getUsedRange().values, 3);
const fuelCo2 = asObjects(fase3d.worksheets.getItem("FUEL_CO2").getUsedRange().values, 3);
const candidates = asObjects(fase3d.worksheets.getItem("TECH_DATA_CANDIDATES").getUsedRange().values, 3);

const wantedTech = /CCGT|OCGT|gas|bio|hydrogen|hydrogen storage|geothermal|nuclear|battery|hydro|fuel cell|CHP|coal|oil/i;
const wantedParam = /efficien|VOM|fuel|CO2|capture|emission|availability|standing|round.trip/i;

process.stdout.write(`TECH_PARAMETERS_MATCHES\n${JSON.stringify(techParams.filter((row) => wantedTech.test(String(row.Tecnologia)) && wantedParam.test(String(row.Parametro))), null, 2)}\n`);
process.stdout.write(`FUEL_CO2\n${JSON.stringify(fuelCo2, null, 2)}\n`);
process.stdout.write(`TECH_DATA_KEYS_SAMPLE\n${JSON.stringify(candidates.slice(0, 3), null, 2)}\n`);
process.stdout.write(`TECH_DATA_V015_MATCHES\n${JSON.stringify(candidates.filter((row) => Object.values(row).some((value) => String(value) === "v0.15.0") && Object.values(row).some((value) => [2040, 2050].includes(Number(value))) && Object.values(row).some((value) => wantedTech.test(String(value))) && Object.values(row).some((value) => wantedParam.test(String(value)))), null, 2)}\n`);

const v29 = await load(path.join(workspace, "outputs", "01a0595d-8cf1-7202-8ca1-1d3ab732e98e", "Fase_5_PyPSA_Eur_Italia_2040_2050_Model_Ready_v2.9_MEM_ARCHITECTURE_REFINED.xlsx"));
for (const sheetName of ["05_STORAGE_BY_ZONE", "10_NUCLEAR"]) {
  const values = v29.worksheets.getItem(sheetName).getUsedRange().values;
  process.stdout.write(`${sheetName}\n${JSON.stringify(values, null, 2)}\n`);
}

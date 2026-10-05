import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workspace = path.resolve(import.meta.dirname, "..", "..", "..", "..");
const v29Path = path.join(workspace, "outputs", "01a0595d-8cf1-7202-8ca1-1d3ab732e98e", "Fase_5_PyPSA_Eur_Italia_2040_2050_Model_Ready_v2.9_MEM_ARCHITECTURE_REFINED.xlsx");
const fase3dPath = path.join(workspace, "01_WORKBOOKS", "Fase_3D_Costi_Efficienze_Parametri_Tecnici.xlsx");

const load = async (file) => SpreadsheetFile.importXlsx(await FileBlob.load(file));
const objects = (values, headerIndex) => {
  const headers = values[headerIndex].map((v, i) => String(v ?? `column_${i + 1}`).trim());
  return values.slice(headerIndex + 1)
    .filter((row) => row.some((v) => v !== null && v !== ""))
    .map((row) => Object.fromEntries(headers.map((h, i) => [h || `column_${i + 1}`, row[i] ?? ""])));
};

const v29 = await load(v29Path);
for (const name of ["09_TECH_COSTS", "05_STORAGE_BY_ZONE", "10_NUCLEAR"]) {
  const values = v29.worksheets.getItem(name).getUsedRange().values;
  process.stdout.write(`\n### ${name}\n`);
  process.stdout.write(`${JSON.stringify(values, null, 2)}\n`);
}

const f3d = await load(fase3dPath);
const tp = objects(f3d.worksheets.getItem("TECH_PARAMETERS").getUsedRange().values, 3);
const selected = tp.filter((r) => {
  const t = String(r.Tecnologia ?? "");
  const p = String(r.Parametro ?? "");
  return /CCGT|OCGT|gas|bio|hydrogen|geothermal|nuclear|battery|hydro|CHP|CCUS|capture/i.test(t)
    && /efficien|VOM|fuel|CO2|capture|emission|availability|storage|round.trip|cost/i.test(p);
});
process.stdout.write(`\n### FASE3D_SELECTED\n${JSON.stringify(selected, null, 2)}\n`);

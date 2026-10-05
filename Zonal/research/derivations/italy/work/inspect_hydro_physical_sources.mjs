import fs from "node:fs/promises";
import path from "node:path";
import { Workbook, SpreadsheetFile, FileBlob } from "@oai/artifact-tool";

const phaseRoot = path.resolve(import.meta.dirname, "..");

async function readCsv(file, sheetName) {
  const workbook = await Workbook.fromCSV(await fs.readFile(file, "utf8"), { sheetName });
  const values = workbook.worksheets.getItem(sheetName).getUsedRange().values;
  return { headers: values[0], rows: values.slice(1) };
}

const jrcFile = path.join(phaseRoot, "raw", "hydro_physical", "JRC_Hydro_Power_Plant_Database_RAW.csv");
const jrc = await readCsv(jrcFile, "JRC");
const countryIndex = jrc.headers.findIndex((header) => String(header).toLowerCase() === "country_code");
const italy = jrc.rows.filter((row) => String(row[countryIndex]).toUpperCase() === "IT");

const zenodoFile = path.join(phaseRoot, "raw", "hydro_physical", "Italian_Hydropower_Programmable_Plants_Database_v2_RAW.xlsx");
const zenodoWorkbook = await SpreadsheetFile.importXlsx(await FileBlob.load(zenodoFile));
const zenodoValues = zenodoWorkbook.worksheets.getItem("List of Plants").getUsedRange().values;
const zenodoHeaders = zenodoValues[0];
const zenodoRows = zenodoValues.slice(1).filter((row) => row.some((value) => value !== null && value !== ""));

function groupedSums(headers, rows, groupHeader, valueHeaders) {
  const groupIndex = headers.indexOf(groupHeader);
  const valueIndexes = valueHeaders.map((header) => headers.indexOf(header));
  const map = new Map();
  for (const row of rows) {
    const key = String(row[groupIndex]);
    const current = map.get(key) ?? Array(valueHeaders.length).fill(0);
    valueIndexes.forEach((index, i) => {
      const number = Number(row[index]);
      if (Number.isFinite(number)) current[i] += number;
    });
    map.set(key, current);
  }
  return [...map.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([group, sums]) => ({
    group,
    ...Object.fromEntries(valueHeaders.map((header, i) => [header, sums[i]])),
  }));
}

const output = {
  jrc: {
    headers: jrc.headers,
    allRows: jrc.rows.length,
    italyRows: italy.length,
    preview: italy.slice(0, 12),
    byType: groupedSums(jrc.headers, italy, "type", ["installed_capacity_MW", "pumping_MW", "storage_capacity_MWh"]),
  },
  zenodo: {
    headers: zenodoHeaders,
    rows: zenodoRows.length,
    byType: groupedSums(zenodoHeaders, zenodoRows, "type", ["installed_capacity_[MW]", "pumping_[MW]", "storage_capacity_[GWh]"]),
  },
};
process.stdout.write(`${JSON.stringify(output, null, 2)}\n`);

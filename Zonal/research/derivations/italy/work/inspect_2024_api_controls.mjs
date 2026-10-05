import fs from "node:fs/promises";
import { detectTernaDecimalConventions, parseTernaNumber } from "../scripts/terna_numeric_parser.mjs";

const rawDir = "../raw/terna/historical_2019_2025_api";
const sumField = (rows, field) => {
  const values = rows.map((row) => row[field]).filter((v) => v !== null && v !== undefined && String(v).trim() !== "");
  const convention = detectTernaDecimalConventions(values);
  return values.reduce((total, value) => total + parseTernaNumber(value, convention).value, 0);
};
const heat = JSON.parse(await fs.readFile(`${rawDir}/thermoelectric-heat_2024_RAW.json`, "utf8")).thermoelectric_heat;
const ren = JSON.parse(await fs.readFile(`${rawDir}/renewable-sources-production_2024_RAW.json`, "utf8")).renewable_sources.filter((row) => row.production_type === "Netta");
console.log(JSON.stringify({
  api_heat_GWh: sumField(heat, "heat_production_GWh"),
  renewable_generation_NET_GWh: Object.fromEntries([...new Set(ren.map((row) => row.renewable_source))].sort().map((source) => [source, sumField(ren.filter((row) => row.renewable_source === source), "production_GWh")])),
}, null, 2));

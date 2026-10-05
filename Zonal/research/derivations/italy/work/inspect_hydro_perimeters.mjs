import fs from "node:fs/promises";
import { detectTernaDecimalConventions, parseTernaNumber } from "../scripts/terna_numeric_parser.mjs";

const rawDir = "../raw/terna/historical_2019_2025_api";
const parseSum = (rows, field) => {
  const convention = detectTernaDecimalConventions(rows.map((row) => row[field]));
  return rows.reduce((total, row) => total + parseTernaNumber(row[field], convention).value, 0);
};
const out = [];
for (let year = 2019; year <= 2024; year += 1) {
  const renewable = JSON.parse(await fs.readFile(`${rawDir}/renewable-sources-production_${year}_RAW.json`, "utf8")).renewable_sources.filter((row) => row.production_type === "Netta" && row.renewable_source === "Idrico");
  const hydric = JSON.parse(await fs.readFile(`${rawDir}/hydric_${year}_RAW.json`, "utf8")).hydric.filter((row) => row.production_type === "Netta");
  const renewableValue = parseSum(renewable, "production_GWh");
  const hydricValue = parseSum(hydric, "production_GWh");
  out.push({ year, renewable_source_hydro_NET_GWh: renewableValue, hydric_type_total_NET_GWh: hydricValue, difference_hydric_minus_renewable_GWh: hydricValue - renewableValue });
}
console.log(JSON.stringify(out, null, 2));

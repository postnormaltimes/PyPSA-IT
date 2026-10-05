import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const file = "../raw/terna/historical_2024/Terna_Hydro_Production_By_Type_2024_RAW.xlsx";
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(file));
const values = workbook.worksheets.getItem("Export").getUsedRange().values;
const normalize = (v) => String(v ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
const groups = new Map();
for (let i = 1; i < values.length; i += 1) {
  const row = values[i];
  if (String(row[0] ?? "").startsWith("Applied filters:") || row[1] !== "Netta") continue;
  const key = [row[0], normalize(row[2]), normalize(row[3]), normalize(row[4])].join("|");
  const current = groups.get(key) ?? [];
  current.push({ row_number: i + 1, row });
  groups.set(key, current);
}
const duplicates = [...groups.entries()].filter(([, rows]) => rows.length > 1).map(([key, rows]) => ({ key, count: rows.length, rows }));
console.log(JSON.stringify({ group_count: groups.size, duplicate_group_count: duplicates.length, duplicates }, null, 2));

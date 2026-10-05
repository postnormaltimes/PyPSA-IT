import fs from "node:fs/promises";
import crypto from "node:crypto";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const files = [
  "C:/Users/EmilianoBarin/Downloads/Export-DownloadCenterFile-20260901-100929.xlsx",
  "C:/Users/EmilianoBarin/Downloads/Export-DownloadCenterFile-20260901-100933.xlsx",
  "C:/Users/EmilianoBarin/Downloads/Export-DownloadCenterFile-20260901-100940.xlsx",
  "C:/Users/EmilianoBarin/Downloads/Export-DownloadCenterFile-20260901-101019.xlsx",
  "C:/Users/EmilianoBarin/Downloads/Export-DownloadCenterFile-20260901-101029.xlsx",
  "C:/Users/EmilianoBarin/Downloads/Export-DownloadCenterFile-20260901-101036.xlsx",
  "C:/Users/EmilianoBarin/Downloads/Export-DownloadCenterFile-20260901-101113.xlsx",
];

for (const file of files) {
  const bytes = await fs.readFile(file);
  const sha256 = crypto.createHash("sha256").update(bytes).digest("hex");
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(file));
  const overview = await workbook.inspect({
    kind: "workbook,sheet,table,definedName,region",
    include: "id,name,range,values,formulas",
    maxChars: 26000,
    tableMaxRows: 18,
    tableMaxCols: 18,
    tableMaxCellChars: 200,
  });
  console.log(`\n=== ${file} ===`);
  console.log(`bytes=${bytes.length} sha256=${sha256}`);
  console.log(overview.ndjson);
}

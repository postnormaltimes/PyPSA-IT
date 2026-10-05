import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const memRoot = path.resolve("../../../..");
const qaDir = path.resolve("../qa");
await fs.mkdir(qaDir, { recursive: true });

const filenames = [
  "Export-DownloadCenterFile-20260901-085449.xlsx",
  "Export-DownloadCenterFile-20260901-085433.xlsx",
];

const reports = [];
for (const filename of filenames) {
  const sourcePath = path.join(memRoot, filename);
  const bytes = await fs.readFile(sourcePath);
  const sha256 = crypto.createHash("sha256").update(bytes).digest("hex");
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(sourcePath));
  const overview = await workbook.inspect({
    kind: "workbook,sheet,table,definedName",
    include: "id,name,range,values,formulas",
    maxChars: 18000,
    tableMaxRows: 12,
    tableMaxCols: 16,
    tableMaxCellChars: 160,
  });
  reports.push({
    filename,
    source_path: sourcePath,
    byte_size: bytes.length,
    sha256,
    inspect_ndjson: overview.ndjson,
  });
}

await fs.writeFile(
  path.join(qaDir, "uploaded_terna_xlsx_initial_inspection.json"),
  `${JSON.stringify(reports, null, 2)}\n`,
  "utf8",
);

for (const report of reports) {
  console.log(`=== ${report.filename} ===`);
  console.log(`bytes=${report.byte_size} sha256=${report.sha256}`);
  console.log(report.inspect_ndjson);
}

import { Workbook } from "@oai/artifact-tool";

const workbook = Workbook.create();
console.log(
  workbook.help("csv export", {
    include: "index,examples,notes",
    maxChars: 6000,
  }).ndjson,
);

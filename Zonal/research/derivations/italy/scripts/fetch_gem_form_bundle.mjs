import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";

const url = "https://api.globalenergymonitor.org/static/gem-download-form.bundle.js";
const phaseRoot = path.resolve(
  "outputs/01a0595d-8cf1-7202-8ca1-1d3ab732e98e/thermal_stack_phase",
);
const rawDir = path.join(phaseRoot, "raw", "gem");
await fs.mkdir(rawDir, { recursive: true });

const response = await fetch(url, { redirect: "follow" });
if (!response.ok) {
  throw new Error(`GEM form bundle failed with HTTP ${response.status}.`);
}
const bytes = Buffer.from(await response.arrayBuffer());
const text = bytes.toString("utf8");
const sha256 = crypto.createHash("sha256").update(bytes).digest("hex");
const filePath = path.join(rawDir, "gem-download-form.bundle.js");
await fs.writeFile(filePath, bytes);

const strings = [...text.matchAll(/["'`]([^"'`\r\n]{1,300})["'`]/g)].map(
  (match) => match[1],
);
const relevant = [...new Set(strings.filter((value) =>
  /(?:https?:\/\/|api|download|email|organization|country|slug|file|token|captcha|consent)/i.test(
    value,
  ),
))].slice(0, 300);

const metadata = {
  source_id: "GEM_DOWNLOAD_FORM_BUNDLE",
  source_url: url,
  retrieval_timestamp_utc: new Date().toISOString(),
  http_status: response.status,
  content_type: response.headers.get("content-type"),
  bytes: bytes.length,
  sha256,
  local_file: path.basename(filePath),
  relevant_strings: relevant,
};
const metadataPath = path.join(rawDir, "gem-download-form.bundle.metadata.json");
await fs.writeFile(metadataPath, `${JSON.stringify(metadata, null, 2)}\n`, "utf8");
console.log(JSON.stringify(metadata));

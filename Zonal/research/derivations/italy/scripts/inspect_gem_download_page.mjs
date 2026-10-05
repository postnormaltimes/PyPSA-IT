import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";

const url =
  "https://globalenergymonitor.org/projects/global-oil-gas-plant-tracker";
const phaseRoot = path.resolve(
  "outputs/01a0595d-8cf1-7202-8ca1-1d3ab732e98e/thermal_stack_phase",
);
const rawDir = path.join(phaseRoot, "raw", "gem");
await fs.mkdir(rawDir, { recursive: true });

const response = await fetch(url, {
  redirect: "follow",
  headers: { "user-agent": "MEM-Italy-market-model/2.9 source-audit" },
});
if (!response.ok) {
  throw new Error(`GEM tracker page failed with HTTP ${response.status}.`);
}
const bytes = Buffer.from(await response.arrayBuffer());
const html = bytes.toString("utf8");
const sha256 = crypto.createHash("sha256").update(bytes).digest("hex");
const pagePath = path.join(rawDir, "GEM_GOGPT_Aug2026_Source_Page.html");
await fs.writeFile(pagePath, bytes);

const extract = (regex) => [...html.matchAll(regex)].map((match) => match[0]);
const forms = extract(/<form\b[^>]*>/gi).slice(0, 20);
const iframes = extract(/<iframe\b[^>]*>/gi).slice(0, 20);
const downloadUrls = extract(
  /https?:\/\/[^"'<>\s]+\.(?:xlsx?|csv|zip)(?:\?[^"'<>\s]*)?/gi,
).slice(0, 50);
const embeddedFormSignals = extract(
  /.{0,160}(?:hubspot|gravityforms|gform_|formidable|download data|mktoForm|form_id).{0,240}/gi,
).slice(0, 50);

const metadata = {
  source_id: "GEM_GOGPT_AUG2026_OFFICIAL_TRACKER_PAGE",
  source_url: url,
  final_url: response.url,
  retrieval_timestamp_utc: new Date().toISOString(),
  http_status: response.status,
  content_type: response.headers.get("content-type"),
  bytes: bytes.length,
  sha256,
  local_file: path.basename(pagePath),
  release_text_present: html.includes("August 2026"),
  sub_threshold_text_present:
    /sub[- ]threshold units/i.test(html) || /Sub-threshold units/i.test(html),
  forms,
  iframes,
  download_urls: downloadUrls,
  embedded_form_signals: embeddedFormSignals,
};

const metadataPath = path.join(
  rawDir,
  "GEM_GOGPT_Aug2026_Source_Page.metadata.json",
);
await fs.writeFile(metadataPath, `${JSON.stringify(metadata, null, 2)}\n`, "utf8");
console.log(JSON.stringify(metadata));

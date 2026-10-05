import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";

const phaseRoot = path.resolve(
  "outputs/01a0595d-8cf1-7202-8ca1-1d3ab732e98e/thermal_stack_phase",
);
const rawDir = path.join(phaseRoot, "raw", "geography");
await fs.mkdir(rawDir, { recursive: true });

const sources = [
  {
    source_id: "ISTAT_DEMOGRAPHIC_BALANCE_2024_ADMIN_GEOGRAPHY",
    url: "https://demo.istat.it/app/?a=2024&i=P02&l=en",
    filename: "ISTAT_Demographic_Balance_2024_Admin_Geography.html",
  },
  {
    source_id: "TERNA_ZONAL_CONFIGURATION_ALTERNATIVA_BASE",
    url: "https://download.terna.it/terna/0000/1033/91.PDF",
    filename: "Terna_Revisione_Configurazione_Zonale_Alternativa_Base.pdf",
  },
  {
    source_id: "TERNA_GRID_CODE_ANNEX_A24_REV06_2025",
    url: "https://download.terna.it/terna/Allegato_A.24_8dd0fd080a2324d.pdf",
    filename: "Terna_Allegato_A24_Rev06_2025.pdf",
  },
  {
    source_id: "GME_CURRENT_ZONE_GLOSSARY",
    url: "https://www.mercatoelettrico.org/Home/Glossario",
    filename: "GME_Current_Zone_Glossary.html",
  },
];

const retrievalTimestamp = new Date().toISOString();
const manifest = [];

for (const source of sources) {
  const response = await fetch(source.url, {
    headers: {
      accept: "*/*",
      "user-agent": "MEM-Italy-market-model/2.9 source-archiver",
    },
    redirect: "follow",
  });
  if (!response.ok) {
    throw new Error(`${source.source_id} failed with HTTP ${response.status}.`);
  }
  const bytes = Buffer.from(await response.arrayBuffer());
  const sha256 = crypto.createHash("sha256").update(bytes).digest("hex");
  const filePath = path.join(rawDir, source.filename);
  await fs.writeFile(filePath, bytes);
  manifest.push({
    ...source,
    retrieval_timestamp_utc: retrievalTimestamp,
    final_url: response.url,
    http_status: response.status,
    content_type: response.headers.get("content-type"),
    bytes: bytes.length,
    sha256,
    local_file: source.filename,
  });
}

const manifestPath = path.join(rawDir, "GEOGRAPHY_SOURCE_MANIFEST.json");
await fs.writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
console.log(JSON.stringify({ ok: true, source_count: manifest.length, manifest }));

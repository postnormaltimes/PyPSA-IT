import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";

const clientId = process.env.TERNA_CLIENT_ID;
const clientSecret = process.env.TERNA_CLIENT_SECRET;
if (!clientId || !clientSecret) throw new Error("Process-scoped Terna credentials are unavailable.");

const phaseRoot = path.resolve(".");
const rawDir = path.join(phaseRoot, "raw", "terna", "historical_2019_2025_api");
await fs.mkdir(rawDir, { recursive: true });

const endpoints = [
  { endpoint: "thermoelectric-capacity", arrayKey: "thermoelectric" },
  { endpoint: "thermoelectric-production", arrayKey: "thermoelectric" },
  { endpoint: "renewable-source-capacity", arrayKey: "renewable_sources" },
  { endpoint: "renewable-sources-production", arrayKey: "renewable_sources" },
  { endpoint: "hydric", arrayKey: "hydric" },
  { endpoint: "thermoelectric-heat", arrayKey: "thermoelectric_heat" },
];
const years = [2019, 2020, 2021, 2022, 2023, 2024, 2025];
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const tokenResponse = await fetch("https://api.terna.it/public-api/access-token", {
  method: "POST",
  headers: { "content-type": "application/x-www-form-urlencoded" },
  body: new URLSearchParams({ client_id: clientId, client_secret: clientSecret, grant_type: "client_credentials" }),
});
if (!tokenResponse.ok) throw new Error(`Terna OAuth failed with HTTP ${tokenResponse.status}.`);
const accessToken = (await tokenResponse.json()).access_token;
if (!accessToken) throw new Error("Terna OAuth returned no access token.");

async function fetchRaw(url) {
  const backoff = [0, 5000, 15000, 30000];
  for (let attempt = 0; attempt < backoff.length; attempt += 1) {
    if (backoff[attempt]) await sleep(backoff[attempt]);
    const response = await fetch(url, { headers: { authorization: `Bearer ${accessToken}`, accept: "application/json" } });
    const bytes = Buffer.from(await response.arrayBuffer());
    const text = bytes.toString("utf8");
    if (response.ok) return { response, bytes, payload: JSON.parse(text) };
    if (!(response.status === 403 && text.includes("Developer Over Qps")) || attempt === backoff.length - 1) {
      throw new Error(`Terna request failed with HTTP ${response.status}: ${text.slice(0, 300)}`);
    }
  }
}

const manifest = [];
for (const config of endpoints) {
  for (const year of years) {
    const stem = `${config.endpoint}_${year}`;
    const rawPath = path.join(rawDir, `${stem}_RAW.json`);
    const metadataPath = path.join(rawDir, `${stem}_RAW.metadata.json`);
    for (const target of [rawPath, metadataPath]) {
      try {
        await fs.access(target);
        throw new Error(`Refusing to overwrite existing historical raw file: ${target}`);
      } catch (error) {
        if (error?.code !== "ENOENT") throw error;
      }
    }
    const url = `https://api.terna.it/generation/v2.0/${config.endpoint}?year=${year}`;
    const retrievalTimestamp = new Date().toISOString();
    const { response, bytes, payload } = await fetchRaw(url);
    const sha256 = crypto.createHash("sha256").update(bytes).digest("hex");
    const rows = Array.isArray(payload?.[config.arrayKey]) ? payload[config.arrayKey] : [];
    const metadata = {
      source_id: `TERNA_API_${config.endpoint.toUpperCase().replaceAll("-", "_")}_${year}`,
      publisher: "Terna S.p.A.",
      endpoint: config.endpoint,
      request_url: url,
      request_parameters: { year: String(year) },
      authorization_method: "OAuth 2.0 client credentials; token not persisted",
      retrieval_timestamp_utc: retrievalTimestamp,
      http_status: response.status,
      content_type: response.headers.get("content-type"),
      response_date: response.headers.get("date"),
      etag: response.headers.get("etag"),
      last_modified: response.headers.get("last-modified"),
      raw_file: path.basename(rawPath),
      raw_bytes: bytes.length,
      raw_sha256: sha256,
      result_status: payload?.result?.status ?? null,
      result_message: payload?.result?.message ?? null,
      array_key: config.arrayKey,
      record_count: rows.length,
      properties: rows.length ? Object.keys(rows[0]).sort() : [],
    };
    await fs.writeFile(rawPath, bytes);
    await fs.writeFile(metadataPath, `${JSON.stringify(metadata, null, 2)}\n`, "utf8");
    manifest.push(metadata);
    console.log(JSON.stringify({ endpoint: config.endpoint, year, record_count: rows.length, raw_sha256: sha256 }));
    await sleep(1200);
  }
}
await fs.writeFile(path.join(rawDir, "TERNA_HISTORICAL_2019_2025_API_RAW_MANIFEST.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
console.log(JSON.stringify({ status: "COMPLETE", files: manifest.length, years, endpoints: endpoints.map((row) => row.endpoint) }));

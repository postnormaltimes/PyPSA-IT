import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";

const clientId = process.env.TERNA_CLIENT_ID;
const clientSecret = process.env.TERNA_CLIENT_SECRET;
if (!clientId || !clientSecret) {
  throw new Error("Required process-scoped Terna credentials are not available.");
}

const phaseRoot = path.resolve(
  "outputs/01a0595d-8cf1-7202-8ca1-1d3ab732e98e/thermal_stack_phase",
);
const rawDir = path.join(phaseRoot, "raw", "terna");
const rawPath = path.join(rawDir, "Terna_Thermoelectric_Production_2024_API_RAW.json");
const metadataPath = path.join(
  rawDir,
  "Terna_Thermoelectric_Production_2024_API_RAW.metadata.json",
);
await fs.mkdir(rawDir, { recursive: true });

const tokenResponse = await fetch("https://api.terna.it/public-api/access-token", {
  method: "POST",
  headers: { "content-type": "application/x-www-form-urlencoded" },
  body: new URLSearchParams({
    client_id: clientId,
    client_secret: clientSecret,
    grant_type: "client_credentials",
  }),
});
if (!tokenResponse.ok) {
  throw new Error(`Terna OAuth request failed with HTTP ${tokenResponse.status}.`);
}
const tokenPayload = await tokenResponse.json();
if (!tokenPayload?.access_token) {
  throw new Error("Terna OAuth response did not contain an access token.");
}

const requestUrl =
  "https://api.terna.it/generation/v2.0/thermoelectric-production?year=2024";
const retrievalTimestamp = new Date().toISOString();
let response;
let rawBytes;
for (const delayMs of [0, 5000, 15000, 30000]) {
  if (delayMs > 0) await new Promise((resolve) => setTimeout(resolve, delayMs));
  response = await fetch(requestUrl, {
    headers: {
      authorization: `Bearer ${tokenPayload.access_token}`,
      accept: "application/json",
    },
  });
  rawBytes = Buffer.from(await response.arrayBuffer());
  const throttled =
    response.status === 403 && rawBytes.toString("utf8").includes("Developer Over Qps");
  if (response.ok || !throttled || delayMs === 30000) break;
}
if (!response.ok) {
  throw new Error(`Terna production request failed with HTTP ${response.status}.`);
}

let parsed;
try {
  parsed = JSON.parse(rawBytes.toString("utf8"));
} catch {
  throw new Error("Terna production response was not valid JSON.");
}
const records = Array.isArray(parsed?.thermoelectric) ? parsed.thermoelectric : [];
const rawSha256 = crypto.createHash("sha256").update(rawBytes).digest("hex");
await fs.writeFile(rawPath, rawBytes);
await fs.writeFile(
  metadataPath,
  `${JSON.stringify(
    {
      source_id: "TERNA_API_THERMOELECTRIC_PRODUCTION_V2_2024",
      source_documentation:
        "https://developer.terna.it/docs/apis_catalog/generation/Thermoelectric_Production",
      request_url: requestUrl,
      request_parameters: {
        year: "2024",
        region: null,
        province: null,
        category: null,
        subcategory: null,
      },
      authorization_method: "OAuth 2.0 client credentials; token not persisted",
      retrieval_timestamp_utc: retrievalTimestamp,
      http_status: response.status,
      content_type: response.headers.get("content-type"),
      response_date: response.headers.get("date"),
      raw_file: path.basename(rawPath),
      raw_bytes: rawBytes.length,
      raw_sha256: rawSha256,
      result_status: parsed?.result?.status ?? null,
      result_message: parsed?.result?.message ?? null,
      record_count: records.length,
    },
    null,
    2,
  )}\n`,
  "utf8",
);

console.log(
  JSON.stringify({
    ok: true,
    record_count: records.length,
    raw_bytes: rawBytes.length,
    raw_sha256: rawSha256,
    capacity_types: [...new Set(records.map((row) => row.capacity_type))],
  }),
);

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
const rawPath = path.join(
  rawDir,
  "Terna_Thermoelectric_Capacity_2024_RAW.json",
);
const metadataPath = path.join(
  rawDir,
  "Terna_Thermoelectric_Capacity_2024_RAW.metadata.json",
);

await fs.mkdir(rawDir, { recursive: true });

const tokenBody = new URLSearchParams({
  client_id: clientId,
  client_secret: clientSecret,
  grant_type: "client_credentials",
});

const tokenResponse = await fetch(
  "https://api.terna.it/public-api/access-token",
  {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: tokenBody,
  },
);

if (!tokenResponse.ok) {
  throw new Error(`Terna OAuth request failed with HTTP ${tokenResponse.status}.`);
}

const tokenPayload = await tokenResponse.json();
if (!tokenPayload?.access_token) {
  throw new Error("Terna OAuth response did not contain an access token.");
}

const requestUrl =
  "https://api.terna.it/generation/v2.0/thermoelectric-capacity?year=2024&capacityType=Netta";
const retrievalTimestamp = new Date().toISOString();
let response;
let rawBytes;
const backoffMs = [0, 5000, 15000, 30000];
for (let attempt = 0; attempt < backoffMs.length; attempt += 1) {
  if (backoffMs[attempt] > 0) {
    await new Promise((resolve) => setTimeout(resolve, backoffMs[attempt]));
  }
  response = await fetch(requestUrl, {
    method: "GET",
    headers: {
      authorization: `Bearer ${tokenPayload.access_token}`,
      accept: "application/json",
    },
  });
  rawBytes = Buffer.from(await response.arrayBuffer());
  const responseText = rawBytes.toString("utf8");
  const isQpsThrottle =
    response.status === 403 && responseText.includes("Developer Over Qps");
  if (response.ok || !isQpsThrottle || attempt === backoffMs.length - 1) {
    break;
  }
  console.error(
    JSON.stringify({
      ok: false,
      stage: "capacity_request",
      http_status: response.status,
      throttle: "Developer Over Qps",
      retry_after_seconds: backoffMs[attempt + 1] / 1000,
    }),
  );
}

const rawSha256 = crypto.createHash("sha256").update(rawBytes).digest("hex");

if (!response.ok) {
  const errorText = rawBytes.toString("utf8");
  const sanitizedError = errorText
    .replaceAll(clientId, "[REDACTED_CLIENT_ID]")
    .replaceAll(clientSecret, "[REDACTED_CLIENT_SECRET]")
    .replaceAll(tokenPayload.access_token, "[REDACTED_ACCESS_TOKEN]");
  console.error(
    JSON.stringify({
      ok: false,
      stage: "capacity_request",
      http_status: response.status,
      content_type: response.headers.get("content-type"),
      www_authenticate: response.headers.get("www-authenticate"),
      response_excerpt: sanitizedError.slice(0, 1200),
    }),
  );
  throw new Error(`Terna capacity request failed with HTTP ${response.status}.`);
}

let parsed;
try {
  parsed = JSON.parse(rawBytes.toString("utf8"));
} catch {
  throw new Error("Terna capacity response was not valid JSON.");
}

await fs.writeFile(rawPath, rawBytes);

const records = Array.isArray(parsed?.thermoelectric)
  ? parsed.thermoelectric
  : [];
const metadata = {
  source_id: "TERNA_API_THERMOELECTRIC_CAPACITY_V2_2024_NETTA",
  source_documentation:
    "https://developer.terna.it/docs/apis_catalog/generation/Thermoelectric_Capacity",
  request_url: requestUrl,
  request_parameters: {
    year: "2024",
    capacityType: "Netta",
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
  etag: response.headers.get("etag"),
  last_modified: response.headers.get("last-modified"),
  raw_file: path.basename(rawPath),
  raw_bytes: rawBytes.length,
  raw_sha256: rawSha256,
  result_status: parsed?.result?.status ?? null,
  result_message: parsed?.result?.message ?? null,
  record_count: records.length,
};

await fs.writeFile(metadataPath, `${JSON.stringify(metadata, null, 2)}\n`, "utf8");

console.log(
  JSON.stringify({
    ok: true,
    http_status: response.status,
    record_count: records.length,
    raw_bytes: rawBytes.length,
    raw_sha256: rawSha256,
    raw_path: rawPath,
    metadata_path: metadataPath,
  }),
);

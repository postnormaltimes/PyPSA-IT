const clientId = process.env.TERNA_CLIENT_ID;
const clientSecret = process.env.TERNA_CLIENT_SECRET;
if (!clientId || !clientSecret) throw new Error("Process-scoped Terna credentials are unavailable.");

const tokenResponse = await fetch("https://api.terna.it/public-api/access-token", {
  method: "POST",
  headers: { "content-type": "application/x-www-form-urlencoded" },
  body: new URLSearchParams({ client_id: clientId, client_secret: clientSecret, grant_type: "client_credentials" }),
});
if (!tokenResponse.ok) throw new Error(`Terna OAuth failed with HTTP ${tokenResponse.status}.`);
const token = (await tokenResponse.json()).access_token;
if (!token) throw new Error("Terna OAuth returned no access token.");

const endpoints = [
  ["thermoelectric-capacity", "thermoelectric"],
  ["thermoelectric-production", "thermoelectric"],
  ["renewable-source-capacity", "renewable_sources"],
  ["renewable-sources-production", "renewable_sources"],
  ["hydric", "hydric"],
  ["thermoelectric-heat", "thermoelectric_heat"],
];
const years = [2019, 2024];
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function request(endpoint, year) {
  const url = `https://api.terna.it/generation/v2.0/${endpoint}?year=${year}`;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    if (attempt > 0) await sleep([0, 5000, 15000, 30000][attempt]);
    const response = await fetch(url, { headers: { authorization: `Bearer ${token}`, accept: "application/json" } });
    const text = await response.text();
    if (response.ok) return JSON.parse(text);
    if (!(response.status === 403 && text.includes("Developer Over Qps")) || attempt === 3) {
      throw new Error(`${endpoint}/${year} failed with HTTP ${response.status}: ${text.slice(0, 200)}`);
    }
  }
}

const result = [];
for (const [endpoint, arrayKey] of endpoints) {
  for (const year of years) {
    const payload = await request(endpoint, year);
    const rows = Array.isArray(payload?.[arrayKey]) ? payload[arrayKey] : [];
    const properties = rows.length ? Object.keys(rows[0]).sort() : [];
    const dimensions = {};
    for (const field of ["capacity_type", "production_type", "category", "subcategory", "source", "renewable_source", "hydric_plant", "hydric_system", "cogeneration_plant"]) {
      const values = [...new Set(rows.map((row) => row[field]).filter((v) => v !== null && v !== undefined))].sort();
      if (values.length) dimensions[field] = values;
    }
    result.push({ endpoint, year, result_status: payload?.result?.status ?? null, row_count: rows.length, properties, dimensions });
    await sleep(1200);
  }
}
console.log(JSON.stringify(result, null, 2));

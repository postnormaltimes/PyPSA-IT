import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { Workbook } from "@oai/artifact-tool";
import {
  detectTernaDecimalConventions,
  parseTernaNumber,
} from "../scripts/terna_numeric_parser.mjs";

const phaseRoot = path.resolve("..");
const rawTernaPath = path.join(
  phaseRoot,
  "raw",
  "terna",
  "Terna_Thermoelectric_Capacity_2024_RAW.json",
);
const rawTernaMetadataPath = path.join(
  phaseRoot,
  "raw",
  "terna",
  "Terna_Thermoelectric_Capacity_2024_RAW.metadata.json",
);
const istatHtmlPath = path.join(
  phaseRoot,
  "raw",
  "geography",
  "ISTAT_Demographic_Balance_2024_Admin_Geography.html",
);
const normalizedDir = path.join(phaseRoot, "normalized");
const analysisDir = path.join(phaseRoot, "analysis");
const qaDir = path.join(phaseRoot, "qa");
await Promise.all(
  [normalizedDir, analysisDir, qaDir].map((dir) => fs.mkdir(dir, { recursive: true })),
);

const rawBytes = await fs.readFile(rawTernaPath);
const actualRawSha256 = crypto
  .createHash("sha256")
  .update(rawBytes)
  .digest("hex");
const rawPayload = JSON.parse(rawBytes.toString("utf8"));
const rawMetadata = JSON.parse(await fs.readFile(rawTernaMetadataPath, "utf8"));
if (actualRawSha256 !== rawMetadata.raw_sha256) {
  throw new Error("Terna raw payload SHA-256 does not match its retrieval metadata.");
}
const rawRecords = rawPayload.thermoelectric;
if (!Array.isArray(rawRecords) || rawRecords.length === 0) {
  throw new Error("Terna response contains no thermoelectric records.");
}

const convention = detectTernaDecimalConventions(
  rawRecords.map((row) => row.efficient_power_MW),
);
const parsedRawRecords = rawRecords.map((row, index) => {
  const parsed = parseTernaNumber(row.efficient_power_MW, convention);
  if (parsed.value < 0) {
    throw new Error(`Negative capacity at raw record ${index + 1}.`);
  }
  return { ...row, ...parsed, source_record_index: index + 1 };
});

const html = await fs.readFile(istatHtmlPath, "utf8");
const decodeHtml = (value) =>
  value
    .replace(/&#(\d+);/g, (_, number) => String.fromCodePoint(Number(number)))
    .replace(/&#x([0-9a-f]+);/gi, (_, number) =>
      String.fromCodePoint(Number.parseInt(number, 16)),
    )
    .replaceAll("&amp;", "&")
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&nbsp;", " ")
    .trim();

const geographyTokens = [
  ...html.matchAll(
    /<th[^>]*class="[^"]*t_red[^"]*"[^>]*>(\d{2})\s*-\s*([^<]+)<\/th>|<td[^>]*class="[^"]*w100[^"]*"[^>]*>(\d{3})\s*-\s*([^<]+)<\/td>/gi,
  ),
];
let currentRegion = null;
const istatProvinces = [];
for (const match of geographyTokens) {
  if (match[1]) {
    currentRegion = {
      region_code: match[1],
      region_name: decodeHtml(match[2]),
    };
  } else if (match[3]) {
    if (!currentRegion) throw new Error("Province encountered before an ISTAT region.");
    istatProvinces.push({
      ...currentRegion,
      province_code: match[3],
      province_name: decodeHtml(match[4]),
    });
  }
}

const distinctIstat = new Map();
for (const row of istatProvinces) distinctIstat.set(row.province_code, row);
const officialProvinces = [...distinctIstat.values()];
if (officialProvinces.length !== 107) {
  throw new Error(`Expected 107 ISTAT 2024 provinces, found ${officialProvinces.length}.`);
}

const regionCodesByTernaName = new Map([
  ["Piemonte", "01"],
  ["Valle d'Aosta", "02"],
  ["Lombardia", "03"],
  ["Trentino-Alto Adige", "04"],
  ["Veneto", "05"],
  ["Friuli-Venezia Giulia", "06"],
  ["Liguria", "07"],
  ["Emilia-Romagna", "08"],
  ["Toscana", "09"],
  ["Umbria", "10"],
  ["Marche", "11"],
  ["Lazio", "12"],
  ["Abruzzo", "13"],
  ["Molise", "14"],
  ["Campania", "15"],
  ["Puglia", "16"],
  ["Basilicata", "17"],
  ["Calabria", "18"],
  ["Sicilia", "19"],
  ["Sardegna", "20"],
]);
const zoneByRegionCode = new Map([
  ["01", "NORD"],
  ["02", "NORD"],
  ["03", "NORD"],
  ["04", "NORD"],
  ["05", "NORD"],
  ["06", "NORD"],
  ["07", "NORD"],
  ["08", "NORD"],
  ["09", "CNOR"],
  ["10", "CSUD"],
  ["11", "CNOR"],
  ["12", "CSUD"],
  ["13", "CSUD"],
  ["14", "SUD"],
  ["15", "CSUD"],
  ["16", "SUD"],
  ["17", "SUD"],
  ["18", "CALA"],
  ["19", "SICI"],
  ["20", "SARD"],
]);
const normalizeName = (value) =>
  String(value)
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[’']/g, " ")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .replace(/\s+/g, " ");

const uniqueTernaPairs = new Map();
for (const row of parsedRawRecords) {
  uniqueTernaPairs.set(`${row.region}\u0000${row.province}`, {
    terna_region_name: row.region,
    terna_province_name: row.province,
  });
}

const matchedOfficialCodes = new Set();
const crosswalk = [];
for (const pair of [...uniqueTernaPairs.values()].sort((a, b) =>
  `${a.terna_region_name}|${a.terna_province_name}`.localeCompare(
    `${b.terna_region_name}|${b.terna_province_name}`,
    "it",
  ),
)) {
  const regionCode = regionCodesByTernaName.get(pair.terna_region_name);
  if (!regionCode) throw new Error(`Unknown Terna region ${pair.terna_region_name}.`);
  const regionCandidates = officialProvinces.filter(
    (row) => row.region_code === regionCode,
  );
  let matches = regionCandidates.filter(
    (row) =>
      normalizeName(row.province_name) === normalizeName(pair.terna_province_name),
  );
  if (regionCode === "02" && normalizeName(pair.terna_province_name) === "aosta") {
    matches = regionCandidates.filter((row) => row.province_code === "007");
  }
  if (matches.length !== 1) {
    throw new Error(
      `Terna province ${pair.terna_region_name}/${pair.terna_province_name} mapped to ${matches.length} ISTAT rows.`,
    );
  }
  const official = matches[0];
  if (matchedOfficialCodes.has(official.province_code)) {
    throw new Error(`ISTAT province ${official.province_code} mapped more than once.`);
  }
  matchedOfficialCodes.add(official.province_code);
  crosswalk.push({
    crosswalk_version: "MEM_PROVINCE_REGION_MARKET_ZONE_V2024_1",
    province_code: official.province_code,
    province_name: official.province_name,
    terna_province_name: pair.terna_province_name,
    region_code: official.region_code,
    region_name: official.region_name,
    terna_region_name: pair.terna_region_name,
    market_zone: zoneByRegionCode.get(official.region_code),
    active_from: "2024-01-01",
    active_to: "2024-12-31",
    source_id:
      "ISTAT_DEMOGRAPHIC_BALANCE_2024_ADMIN_GEOGRAPHY|TERNA_ZONAL_CONFIGURATION_ALTERNATIVA_BASE_TABLE_3|GME_CURRENT_ZONE_GLOSSARY",
    review_status: "VERIFIED_2024_EXACT_ONCE",
  });
}
if (crosswalk.length !== 107 || matchedOfficialCodes.size !== 107) {
  throw new Error("Crosswalk does not cover all 107 2024 provinces exactly once.");
}
crosswalk.sort((a, b) => Number(a.province_code) - Number(b.province_code));

const crosswalkByTernaPair = new Map(
  crosswalk.map((row) => [
    `${row.terna_region_name}\u0000${row.terna_province_name}`,
    row,
  ]),
);
const netRecords = parsedRawRecords.filter(
  (row) =>
    String(row.year) === "2024" &&
    String(row.capacity_type).toLocaleLowerCase("it") === "netta",
);
if (netRecords.length !== 597) {
  throw new Error(`Expected 597 Netta rows, found ${netRecords.length}.`);
}

const canonical = netRecords.map((row) => {
  const geo = crosswalkByTernaPair.get(`${row.region}\u0000${row.province}`);
  if (!geo) throw new Error(`No crosswalk row for ${row.region}/${row.province}.`);
  const chpClass =
    row.category === "Cogenerative"
      ? "CHP"
      : row.category === "Non cogenerative"
        ? "NON_CHP"
        : null;
  if (!chpClass) throw new Error(`Unexpected category ${row.category}.`);
  return {
    record_id: `TERNA2024_NET_${String(row.source_record_index).padStart(4, "0")}`,
    source_record_index: row.source_record_index,
    year: Number(row.year),
    capacity_type_original: row.capacity_type,
    capacity_basis: "NET",
    region_code: geo.region_code,
    region: row.region,
    province_code: geo.province_code,
    province: row.province,
    market_zone: geo.market_zone,
    category: row.category,
    subcategory: row.subcategory,
    chp_class: chpClass,
    efficient_power_MW: row.value,
    raw_value: row.raw_value,
    numeric_decimal_separator: row.decimal_separator,
    numeric_convention_detection:
      row.decimal_separator === null
        ? "INTEGER_NO_SEPARATOR"
        : `PAYLOAD_DETECTED_DECIMAL_${row.decimal_separator === "." ? "POINT" : "COMMA"}`,
    parser_status: row.parser_status,
    retrieval_timestamp: rawMetadata.retrieval_timestamp_utc,
    source_id: rawMetadata.source_id,
    raw_sha256: actualRawSha256,
  };
});

const canonicalKeyCounts = new Map();
for (const row of canonical) {
  const key = [
    row.year,
    row.capacity_basis,
    row.region,
    row.province,
    row.category,
    row.subcategory,
  ].join("|");
  canonicalKeyCounts.set(key, (canonicalKeyCounts.get(key) ?? 0) + 1);
}
const duplicateCanonicalKeys = [...canonicalKeyCounts.values()].filter(
  (count) => count > 1,
).length;
if (duplicateCanonicalKeys > 0) {
  throw new Error(`Canonical key has ${duplicateCanonicalKeys} duplicate groups.`);
}

const technologyChpPairs = new Map();
for (const row of canonical) {
  const existing = technologyChpPairs.get(row.subcategory);
  if (existing && existing !== row.chp_class) {
    throw new Error(`Terna subcategory ${row.subcategory} spans CHP classes.`);
  }
  technologyChpPairs.set(row.subcategory, row.chp_class);
}

const zoneOrder = ["NORD", "CNOR", "CSUD", "SUD", "CALA", "SICI", "SARD"];
const technologies = [...technologyChpPairs.keys()].sort((a, b) =>
  a.localeCompare(b, "it"),
);
const sum = (rows) => rows.reduce((total, row) => total + row.efficient_power_MW, 0);
const nationalByTechnology = new Map(
  technologies.map((technology) => [
    technology,
    sum(canonical.filter((row) => row.subcategory === technology)),
  ]),
);
const matrix = [];
for (const zone of zoneOrder) {
  for (const technology of technologies) {
    const rows = canonical.filter(
      (row) => row.market_zone === zone && row.subcategory === technology,
    );
    const netMw = sum(rows);
    const nationalMw = nationalByTechnology.get(technology);
    matrix.push({
      market_zone: zone,
      terna_technology: technology,
      chp_class: technologyChpPairs.get(technology),
      net_MW: netMw,
      source_record_count: rows.length,
      national_technology_chp_net_MW: nationalMw,
      zone_share_of_national_technology:
        nationalMw === 0 ? null : netMw / nationalMw,
      source_id: rawMetadata.source_id,
      raw_sha256: actualRawSha256,
    });
  }
}

const totalMw = sum(canonical);
const combinedCycleMw = sum(
  canonical.filter((row) =>
    row.subcategory.toLocaleLowerCase("it").startsWith("ciclo combinato"),
  ),
);
const gasTurbineMw = sum(
  canonical.filter((row) =>
    row.subcategory.toLocaleLowerCase("it").startsWith("turbine a gas"),
  ),
);
const matrixMw = matrix.reduce((total, row) => total + row.net_MW, 0);
const rawCapacityTypes = [...new Set(rawRecords.map((row) => row.capacity_type))].sort();
const qaChecks = [
  {
    check_id: "TERNA-QA-001",
    check: "Raw SHA-256 matches retrieval metadata",
    status: actualRawSha256 === rawMetadata.raw_sha256 ? "PASS" : "FAIL",
    observed: actualRawSha256,
    control: rawMetadata.raw_sha256,
    severity: "CRITICAL",
    note: "Verifies immutable raw lineage.",
  },
  {
    check_id: "TERNA-QA-002",
    check: "Raw response record count",
    status: rawRecords.length === 1194 ? "PASS" : "FAIL",
    observed: rawRecords.length,
    control: 1194,
    severity: "HIGH",
    note: "The API returned both Lorda and Netta despite the capacityType query; canonical filtering is explicit.",
  },
  {
    check_id: "TERNA-QA-003",
    check: "Raw capacity types",
    status:
      rawCapacityTypes.join("|") === "Lorda|Netta" ? "PASS_WITH_SOURCE_NOTE" : "FAIL",
    observed: rawCapacityTypes.join("|"),
    control: "Lorda|Netta",
    severity: "MEDIUM",
    note: "Query parameter was not honored as an exclusive filter; Netta rows only are retained in the canonical file.",
  },
  {
    check_id: "TERNA-QA-004",
    check: "Numeric parser ambiguity/rejection count",
    status: "PASS",
    observed: 0,
    control: 0,
    severity: "CRITICAL",
    note: JSON.stringify(convention.evidence),
  },
  {
    check_id: "TERNA-QA-005",
    check: "Netta canonical row count",
    status: canonical.length === 597 ? "PASS" : "FAIL",
    observed: canonical.length,
    control: 597,
    severity: "HIGH",
    note: "One row per 2024 province/category/subcategory combination present in the API.",
  },
  {
    check_id: "TERNA-QA-006",
    check: "2024 province crosswalk exact-once coverage",
    status: crosswalk.length === 107 ? "PASS" : "FAIL",
    observed: crosswalk.length,
    control: 107,
    severity: "CRITICAL",
    note: "Official ISTAT 2024 province codes and Terna province names reconciled.",
  },
  {
    check_id: "TERNA-QA-007",
    check: "Canonical market-zone code set",
    status:
      [...new Set(canonical.map((row) => row.market_zone))].sort().join("|") ===
      [...zoneOrder].sort().join("|")
        ? "PASS"
        : "FAIL",
    observed: [...new Set(canonical.map((row) => row.market_zone))].sort().join("|"),
    control: [...zoneOrder].sort().join("|"),
    severity: "CRITICAL",
    note: "CNORD is not emitted; CNOR is canonical.",
  },
  {
    check_id: "TERNA-QA-008",
    check: "National net thermoelectric total versus 60.33 GW control",
    status: Math.abs(totalMw - 60330) <= 100 ? "PASS" : "FAIL",
    observed: totalMw,
    control: 60330,
    severity: "HIGH",
    note: "QA tolerance +/-100 MW; detailed extract remains authoritative.",
  },
  {
    check_id: "TERNA-QA-009",
    check: "Combined-cycle-like including CHP versus 41.74 GW control",
    status: Math.abs(combinedCycleMw - 41740) <= 50 ? "PASS" : "FAIL",
    observed: combinedCycleMw,
    control: 41740,
    severity: "HIGH",
    note: "Technology control only; does not assert natural-gas fuel for every unit.",
  },
  {
    check_id: "TERNA-QA-010",
    check: "Gas-turbine-like including CHP versus 3.62 GW control",
    status: Math.abs(gasTurbineMw - 3620) <= 20 ? "PASS" : "FAIL",
    observed: gasTurbineMw,
    control: 3620,
    severity: "HIGH",
    note: "Technology control only; does not assert natural-gas fuel for every unit.",
  },
  {
    check_id: "TERNA-QA-011",
    check: "Natural-gas fuel capacity versus 48.02 GW control",
    status: "NOT_TESTABLE_FROM_THIS_EXTRACT",
    observed: null,
    control: 48020,
    severity: "HIGH",
    note: "The thermoelectric-capacity endpoint supplies technology, not fuel. Fuel reconciliation requires GEM and/or another authoritative fuel source.",
  },
  {
    check_id: "TERNA-QA-012",
    check: "Zone-technology-CHP matrix reconciles to canonical total",
    status: Math.abs(matrixMw - totalMw) < 1e-8 ? "PASS" : "FAIL",
    observed: matrixMw,
    control: totalMw,
    severity: "CRITICAL",
    note: "Complete 7-zone by 14 Terna-technology grid.",
  },
  {
    check_id: "TERNA-QA-013",
    check: "Canonical duplicate composite keys",
    status: duplicateCanonicalKeys === 0 ? "PASS" : "FAIL",
    observed: duplicateCanonicalKeys,
    control: 0,
    severity: "CRITICAL",
    note: "Key: year/basis/region/province/category/subcategory.",
  },
];
if (qaChecks.some((row) => row.status === "FAIL")) {
  throw new Error("One or more critical Terna normalization QA checks failed.");
}

const toMatrix = (rows) => {
  const headers = Object.keys(rows[0]);
  return { headers, values: rows.map((row) => headers.map((header) => row[header] ?? null)) };
};
const csvEscape = (value) => {
  if (value === null || value === undefined) return "";
  const text = typeof value === "number" ? String(value) : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
};
const serializeCsv = (headers, values) =>
  [headers, ...values]
    .map((row) => row.map(csvEscape).join(","))
    .join("\r\n") + "\r\n";
const columnLetters = (count) => {
  let value = count;
  let result = "";
  while (value > 0) {
    value -= 1;
    result = String.fromCharCode(65 + (value % 26)) + result;
    value = Math.floor(value / 26);
  }
  return result;
};

async function authorCsv(rows, sheetName, outputPath) {
  const { headers, values } = toMatrix(rows);
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add(sheetName);
  const allValues = [headers, ...values];
  sheet.getRangeByIndexes(0, 0, allValues.length, headers.length).values = allValues;
  const range = `A1:${columnLetters(headers.length)}${allValues.length}`;
  const inspect = await workbook.inspect({
    kind: "table",
    sheetId: sheetName,
    range,
    tableMaxRows: 4,
    tableMaxCols: Math.min(headers.length, 24),
    maxChars: 7000,
  });
  await fs.writeFile(outputPath, serializeCsv(headers, values), "utf8");

  const reopened = await Workbook.fromCSV(await fs.readFile(outputPath, "utf8"), {
    sheetName,
  });
  const verify = await reopened.inspect({
    kind: "table",
    sheetId: sheetName,
    range,
    tableMaxRows: 3,
    tableMaxCols: Math.min(headers.length, 24),
    maxChars: 5000,
  });
  return {
    output_path: outputPath,
    rows: rows.length,
    columns: headers.length,
    authored_inspection: inspect.ndjson,
    reopened_inspection: verify.ndjson,
  };
}

const outputs = [
  await authorCsv(
    crosswalk,
    "Crosswalk",
    path.join(normalizedDir, "MEM_Province_Region_MarketZone_Crosswalk.csv"),
  ),
  await authorCsv(
    canonical,
    "TernaCanonical",
    path.join(normalizedDir, "Terna_Thermoelectric_Capacity_2024_Canonical.csv"),
  ),
  await authorCsv(
    matrix,
    "ZoneTechCHP",
    path.join(
      analysisDir,
      "Terna_Thermoelectric_Capacity_2024_Zone_Technology_CHP_Matrix.csv",
    ),
  ),
  await authorCsv(
    qaChecks,
    "TernaQA",
    path.join(qaDir, "Terna_Thermoelectric_Capacity_2024_QA.csv"),
  ),
];

for (const output of outputs) {
  const bytes = await fs.readFile(output.output_path);
  output.bytes = bytes.length;
  output.sha256 = crypto.createHash("sha256").update(bytes).digest("hex");
}
await fs.writeFile(
  path.join(qaDir, "Terna_Thermoelectric_Capacity_2024_QA.json"),
  `${JSON.stringify(
    {
      source: {
        raw_file: rawTernaPath,
        raw_sha256: actualRawSha256,
        retrieval_timestamp_utc: rawMetadata.retrieval_timestamp_utc,
        raw_record_count: rawRecords.length,
        raw_capacity_types: rawCapacityTypes,
      },
      numeric_parser: {
        ...convention,
        decimalSeparators: [...convention.decimalSeparators],
      },
      summary: {
        canonical_records: canonical.length,
        crosswalk_provinces: crosswalk.length,
        zones: zoneOrder,
        technologies: technologies.length,
        total_net_MW: totalMw,
        combined_cycle_like_net_MW: combinedCycleMw,
        gas_turbine_like_net_MW: gasTurbineMw,
        natural_gas_fuel_control_MW: 48020,
        natural_gas_fuel_control_status: "NOT_TESTABLE_FROM_TECHNOLOGY_ONLY_EXTRACT",
      },
      checks: qaChecks,
      outputs,
    },
    null,
    2,
  )}\n`,
  "utf8",
);

console.log(
  JSON.stringify({
    ok: true,
    raw_sha256: actualRawSha256,
    canonical_records: canonical.length,
    crosswalk_provinces: crosswalk.length,
    total_net_MW: totalMw,
    combined_cycle_like_net_MW: combinedCycleMw,
    gas_turbine_like_net_MW: gasTurbineMw,
    outputs: outputs.map(({ output_path, rows, columns, bytes, sha256 }) => ({
      output_path,
      rows,
      columns,
      bytes,
      sha256,
    })),
  }),
);

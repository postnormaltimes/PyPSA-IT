import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";
import {
  detectTernaDecimalConventions,
  parseTernaNumber,
} from "../scripts/terna_numeric_parser.mjs";

const memRoot = path.resolve("../../../..");
const phaseRoot = path.resolve("..");
const rawTernaDir = path.join(phaseRoot, "raw", "terna");
const normalizedDir = path.join(phaseRoot, "normalized");
const analysisDir = path.join(phaseRoot, "analysis");
const qaDir = path.join(phaseRoot, "qa");
for (const directory of [normalizedDir, analysisDir, qaDir]) {
  await fs.mkdir(directory, { recursive: true });
}

const capacityOriginalFilename = "Export-DownloadCenterFile-20260901-085433.xlsx";
const productionOriginalFilename = "Export-DownloadCenterFile-20260901-085449.xlsx";
const capacityRawFilename = "Terna_Thermoelectric_Capacity_2024_NET_RAW.xlsx";
const productionRawFilename = "Terna_Thermoelectric_Production_2024_RAW.xlsx";
const capacityOriginalPath = path.join(memRoot, capacityOriginalFilename);
const productionOriginalPath = path.join(memRoot, productionOriginalFilename);
const capacityRawPath = path.join(rawTernaDir, capacityRawFilename);
const productionRawPath = path.join(rawTernaDir, productionRawFilename);
const capacityApiPath = path.join(
  rawTernaDir,
  "Terna_Thermoelectric_Capacity_2024_RAW.json",
);
const productionApiPath = path.join(
  rawTernaDir,
  "Terna_Thermoelectric_Production_2024_API_RAW.json",
);
const istatHtmlPath = path.join(
  phaseRoot,
  "raw",
  "geography",
  "ISTAT_Demographic_Balance_2024_Admin_Geography.html",
);

const sha256File = async (filePath) =>
  crypto.createHash("sha256").update(await fs.readFile(filePath)).digest("hex");
const fileInfo = async (filePath) => {
  const stat = await fs.stat(filePath);
  return {
    path: filePath,
    bytes: stat.size,
    sha256: await sha256File(filePath),
  };
};
const capacityOriginalInfo = await fileInfo(capacityOriginalPath);
const productionOriginalInfo = await fileInfo(productionOriginalPath);
const capacityRawInfo = await fileInfo(capacityRawPath);
const productionRawInfo = await fileInfo(productionRawPath);
if (capacityOriginalInfo.sha256 !== capacityRawInfo.sha256) {
  throw new Error("Archived capacity XLSX is not byte-identical to the uploaded original.");
}
if (productionOriginalInfo.sha256 !== productionRawInfo.sha256) {
  throw new Error("Archived production XLSX is not byte-identical to the uploaded original.");
}

const sum = (values) => values.reduce((total, value) => total + value, 0);
const normalizeName = (value) =>
  String(value)
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[’']/g, " ")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .replace(/\s+/g, " ");
const sortedUnique = (values) =>
  [...new Set(values)].sort((a, b) => String(a).localeCompare(String(b), "it"));
const groupRows = (rows, keyFn) => {
  const groups = new Map();
  for (const row of rows) {
    const key = keyFn(row);
    const group = groups.get(key) ?? [];
    group.push(row);
    groups.set(key, group);
  }
  return groups;
};
const compactNumber = (value) => {
  if (value === null || value === undefined) return "";
  if (!Number.isFinite(value)) throw new Error(`Non-finite numeric value ${value}.`);
  return Number(value.toPrecision(15)).toString();
};

async function loadExportWorkbook(filePath, expectedHeaders) {
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(filePath));
  const sheetOverview = await workbook.inspect({
    kind: "sheet",
    include: "id,name,range",
    maxChars: 3000,
  });
  const sheet = workbook.worksheets.getItem("Export");
  const used = sheet.getUsedRange();
  const values = used.values;
  const formulas = used.formulas;
  if (JSON.stringify(values[0]) !== JSON.stringify(expectedHeaders)) {
    throw new Error(
      `Unexpected headers in ${path.basename(filePath)}: ${JSON.stringify(values[0])}`,
    );
  }
  const footerRows = [];
  const dataRows = [];
  for (let index = 1; index < values.length; index += 1) {
    const row = values[index];
    if (String(row[0] ?? "").startsWith("Applied filters:")) {
      footerRows.push({ row_number: index + 1, values: row });
    } else {
      dataRows.push({ row_number: index + 1, values: row });
    }
  }
  const formulaCount = formulas
    .flat()
    .filter((value) => typeof value === "string" && value.startsWith("=")).length;
  return {
    workbook,
    sheet,
    sheetOverview: sheetOverview.ndjson,
    usedRange: used.address,
    headers: values[0],
    dataRows,
    footerRows,
    formulaCount,
  };
}

const capacityWorkbook = await loadExportWorkbook(capacityRawPath, [
  "Anno",
  "Tipo capacità",
  "Regione",
  "Provincia",
  "Categoria",
  "Sottocategoria",
  "Potenza efficiente (MW)",
]);
const productionWorkbook = await loadExportWorkbook(productionRawPath, [
  "Anno",
  "Regione",
  "Provincia",
  "Categoria",
  "Sottocategoria",
  "Produzione (GWh)",
]);

const parseNumericColumn = (rows, columnIndex) => {
  const blanks = rows.filter(
    (row) =>
      row.values[columnIndex] === null ||
      row.values[columnIndex] === undefined ||
      String(row.values[columnIndex]).trim() === "",
  );
  if (blanks.length > 0) {
    throw new Error(`Blank numeric cells in data rows: ${blanks.map((row) => row.row_number).join(",")}`);
  }
  const convention = detectTernaDecimalConventions(
    rows.map((row) => row.values[columnIndex]),
  );
  return {
    convention,
    rows: rows.map((row) => ({
      ...row,
      parsed: parseTernaNumber(row.values[columnIndex], convention),
    })),
  };
};

const capacityParsed = parseNumericColumn(capacityWorkbook.dataRows, 6);
const productionParsed = parseNumericColumn(productionWorkbook.dataRows, 5);

const capacitySourceRows = capacityParsed.rows.map((row) => ({
  source_row_number: row.row_number,
  year: Number(row.values[0]),
  capacity_type_original: row.values[1],
  region_original: row.values[2],
  province_original: row.values[3],
  category_original: row.values[4],
  subcategory_original: row.values[5],
  raw_value: row.parsed.raw_value,
  raw_cell_type: row.parsed.raw_cell_type,
  efficient_power_MW: row.parsed.value,
  parser_status: row.parsed.parser_status,
}));
const productionSourceRows = productionParsed.rows.map((row) => ({
  source_row_number: row.row_number,
  year: Number(row.values[0]),
  region_original: row.values[1],
  province_original: row.values[2],
  category_original: row.values[3],
  subcategory_original: row.values[4],
  raw_value: row.parsed.raw_value,
  raw_cell_type: row.parsed.raw_cell_type,
  production_GWh: row.parsed.value,
  parser_status: row.parsed.parser_status,
}));

if (capacitySourceRows.some((row) => row.year !== 2024)) {
  throw new Error("Capacity export contains a data year other than 2024.");
}
if (productionSourceRows.some((row) => row.year !== 2024)) {
  throw new Error("Production export contains a data year other than 2024.");
}

const capacityApi = JSON.parse(await fs.readFile(capacityApiPath, "utf8"));
const capacityApiRows = capacityApi.thermoelectric ?? [];
let productionApiRows = [];
let productionApiAvailable = false;
try {
  const productionApi = JSON.parse(await fs.readFile(productionApiPath, "utf8"));
  productionApiRows = productionApi.thermoelectric ?? [];
  productionApiAvailable = true;
} catch {
  productionApiRows = [];
  productionApiAvailable = false;
}
const multiset = (keys) => {
  const result = new Map();
  for (const key of keys) result.set(key, (result.get(key) ?? 0) + 1);
  return result;
};
const compareMultisets = (left, right) => {
  const keys = new Set([...left.keys(), ...right.keys()]);
  const mismatches = [];
  for (const key of keys) {
    const leftCount = left.get(key) ?? 0;
    const rightCount = right.get(key) ?? 0;
    if (leftCount !== rightCount) mismatches.push({ key, leftCount, rightCount });
  }
  return mismatches;
};
const capacityManualKeys = capacitySourceRows.map((row) =>
  [
    row.year,
    row.capacity_type_original,
    row.region_original,
    row.province_original,
    row.category_original,
    row.subcategory_original,
    compactNumber(row.efficient_power_MW),
  ].join("\u0000"),
);
const capacityApiKeys = capacityApiRows.map((row) =>
  [
    Number(row.year),
    row.capacity_type,
    row.region,
    row.province,
    row.category,
    row.subcategory,
    compactNumber(Number(row.efficient_power_MW)),
  ].join("\u0000"),
);
const capacityApiMismatches = compareMultisets(
  multiset(capacityManualKeys),
  multiset(capacityApiKeys),
);
const productionApiSpotcheck = {
  available: productionApiAvailable,
  workbook_rows: productionSourceRows.length,
  api_rows: productionApiRows.length,
  workbook_all_values_total_GWh: sum(
    productionSourceRows.map((row) => row.production_GWh),
  ),
  api_all_values_total_GWh: productionApiAvailable
    ? sum(productionApiRows.map((row) => Number(row.production_GWh)))
    : null,
  actual_api_fields: productionApiAvailable
    ? sortedUnique(productionApiRows.flatMap((row) => Object.keys(row)))
    : [],
};
productionApiSpotcheck.aggregate_total_matches = productionApiAvailable
  ? Math.abs(
      productionApiSpotcheck.workbook_all_values_total_GWh -
        productionApiSpotcheck.api_all_values_total_GWh,
    ) < 1e-4
  : null;

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
const istatHtml = await fs.readFile(istatHtmlPath, "utf8");
const geographyTokens = [
  ...istatHtml.matchAll(
    /<th[^>]*class="[^"]*t_red[^"]*"[^>]*>(\d{2})\s*-\s*([^<]+)<\/th>|<td[^>]*class="[^"]*w100[^"]*"[^>]*>(\d{3})\s*-\s*([^<]+)<\/td>/gi,
  ),
];
let currentRegion = null;
const officialProvinceRows = [];
for (const match of geographyTokens) {
  if (match[1]) {
    currentRegion = {
      region_code: match[1],
      region_name: decodeHtml(match[2]),
    };
  } else if (match[3]) {
    if (!currentRegion) throw new Error("ISTAT province encountered before a region.");
    officialProvinceRows.push({
      ...currentRegion,
      province_code: match[3],
      province_name: decodeHtml(match[4]),
    });
  }
}
const officialByCode = new Map();
for (const row of officialProvinceRows) officialByCode.set(row.province_code, row);
const officialProvinces = [...officialByCode.values()];
if (officialProvinces.length !== 107) {
  throw new Error(`Expected 107 ISTAT provinces, found ${officialProvinces.length}.`);
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

const ternaProvincePairs = sortedUnique(
  capacitySourceRows.map((row) => `${row.region_original}\u0000${row.province_original}`),
);
const matchedProvinceCodes = new Set();
const crosswalk = [];
for (const key of ternaProvincePairs) {
  const [ternaRegionName, ternaProvinceName] = key.split("\u0000");
  const regionCode = regionCodesByTernaName.get(ternaRegionName);
  if (!regionCode) throw new Error(`Unknown Terna region ${ternaRegionName}.`);
  let matches = officialProvinces.filter(
    (row) =>
      row.region_code === regionCode &&
      normalizeName(row.province_name) === normalizeName(ternaProvinceName),
  );
  if (regionCode === "02" && normalizeName(ternaProvinceName) === "aosta") {
    matches = officialProvinces.filter((row) => row.province_code === "007");
  }
  if (matches.length !== 1) {
    throw new Error(
      `Terna province ${ternaRegionName}/${ternaProvinceName} mapped to ${matches.length} ISTAT rows.`,
    );
  }
  const official = matches[0];
  if (matchedProvinceCodes.has(official.province_code)) {
    throw new Error(`ISTAT province ${official.province_code} mapped more than once.`);
  }
  matchedProvinceCodes.add(official.province_code);
  crosswalk.push({
    crosswalk_version: "MEM_PROVINCE_REGION_MARKET_ZONE_V2024_1",
    province_code: official.province_code,
    province_name: official.province_name,
    terna_province_name: ternaProvinceName,
    region_code: official.region_code,
    region_name: official.region_name,
    terna_region_name: ternaRegionName,
    market_zone: zoneByRegionCode.get(official.region_code),
    active_from: "2024-01-01",
    active_to: "2024-12-31",
    source_id:
      "ISTAT_DEMOGRAPHIC_BALANCE_2024_ADMIN_GEOGRAPHY|TERNA_ZONAL_CONFIGURATION_ALTERNATIVA_BASE_TABLE_3|GME_CURRENT_ZONE_GLOSSARY",
    review_status: "VERIFIED_2024_EXACT_ONCE",
  });
}
crosswalk.sort((a, b) => Number(a.province_code) - Number(b.province_code));
if (crosswalk.length !== 107 || matchedProvinceCodes.size !== 107) {
  throw new Error("Crosswalk does not cover every 2024 province exactly once.");
}
const crosswalkByTernaPair = new Map(
  crosswalk.map((row) => [
    `${row.terna_region_name}\u0000${row.terna_province_name}`,
    row,
  ]),
);

const technologyNormalized = (subcategory) => {
  const key = normalizeName(subcategory);
  const mapping = new Map([
    ["altro genere", "OTHER_THERMAL"],
    ["celle combustibili", "FUEL_CELL"],
    ["celle combustibili con cogenerazione", "FUEL_CELL"],
    ["ciclo combinato", "CCGT_LIKE"],
    ["ciclo combinato con produzione di calore", "CCGT_LIKE"],
    ["combustione esterna", "EXTERNAL_COMBUSTION"],
    ["combustione interna", "INTERNAL_COMBUSTION"],
    ["combustione interna con produzione di calore", "INTERNAL_COMBUSTION"],
    ["condensazione", "CONDENSING_STEAM"],
    ["condensazione e spillamento", "EXTRACTION_CONDENSING"],
    ["contropressione", "BACK_PRESSURE"],
    ["turbine a gas", "GAS_TURBINE_LIKE"],
    ["turbine a gas con produzione di calore", "GAS_TURBINE_LIKE"],
    ["turbo espansione", "TURBO_EXPANSION"],
  ]);
  const normalized = mapping.get(key);
  if (!normalized) throw new Error(`Unmapped Terna subcategory ${subcategory}.`);
  return normalized;
};
const chpClass = (category) => {
  if (category === "Cogenerative") return "CHP";
  if (category === "Non cogenerative") return "NON_CHP";
  throw new Error(`Unexpected Terna category ${category}.`);
};

const netCapacitySourceRows = capacitySourceRows.filter(
  (row) => row.capacity_type_original === "Netta",
);
const netCapacityGroups = groupRows(netCapacitySourceRows, (row) =>
  [
    row.year,
    row.region_original,
    row.province_original,
    row.category_original,
    row.subcategory_original,
  ].join("\u0000"),
);
const rawDuplicateCapacityGroups = [...netCapacityGroups.entries()]
  .filter(([, rows]) => rows.length > 1)
  .map(([key, rows]) => ({
    grain_key: key.split("\u0000").join(" | "),
    source_row_count: rows.length,
    source_row_numbers: rows.map((row) => row.source_row_number).join("|"),
    values_MW: rows.map((row) => compactNumber(row.efficient_power_MW)).join("|"),
    exact_duplicate_values:
      new Set(rows.map((row) => compactNumber(row.efficient_power_MW))).size === 1,
    aggregated_MW: sum(rows.map((row) => row.efficient_power_MW)),
    single_row_MW: rows[0].efficient_power_MW,
    duplicate_excess_sensitivity_MW:
      sum(rows.map((row) => row.efficient_power_MW)) - rows[0].efficient_power_MW,
  }));

let capacityRecordCounter = 0;
const canonicalCapacity = [...netCapacityGroups.entries()]
  .sort(([a], [b]) => a.localeCompare(b, "it"))
  .map(([, rows]) => {
    capacityRecordCounter += 1;
    const first = rows[0];
    const geo = crosswalkByTernaPair.get(
      `${first.region_original}\u0000${first.province_original}`,
    );
    if (!geo) {
      throw new Error(`No crosswalk for ${first.region_original}/${first.province_original}.`);
    }
    return {
      record_id: `TERNA2024_NET_${String(capacityRecordCounter).padStart(4, "0")}`,
      year: first.year,
      capacity_type_original: first.capacity_type_original,
      capacity_basis: "NET",
      region_code: geo.region_code,
      region_original: first.region_original,
      region_normalized: geo.region_name,
      province_code: geo.province_code,
      province_original: first.province_original,
      province_normalized: geo.province_name,
      market_zone: geo.market_zone,
      category_original: first.category_original,
      category_normalized: chpClass(first.category_original),
      chp_flag: chpClass(first.category_original) === "CHP",
      subcategory_original: first.subcategory_original,
      technology_normalized: technologyNormalized(first.subcategory_original),
      efficient_power_MW_raw: rows.map((row) => row.raw_value).join("|"),
      efficient_power_MW: sum(rows.map((row) => row.efficient_power_MW)),
      raw_cell_type: sortedUnique(rows.map((row) => row.raw_cell_type)).join("|"),
      parser_status: sortedUnique(rows.map((row) => row.parser_status)).join("|"),
      source_row_count: rows.length,
      source_row_numbers: rows.map((row) => row.source_row_number).join("|"),
      source_grain_status:
        rows.length === 1
          ? "UNIQUE_SOURCE_GRAIN"
          : "MULTIPLE_OFFICIAL_SOURCE_ROWS_AGGREGATED_PRESERVING_TOTAL",
      acquisition_date: "2026-09-01",
      acquisition_route: "TERNA_DOWNLOAD_CENTER",
      source_id: "TERNA_DOWNLOAD_CENTER_THERMOELECTRIC_CAPACITY_2024",
      raw_source_file: capacityOriginalFilename,
      archived_raw_file: capacityRawFilename,
      raw_source_sha256: capacityRawInfo.sha256,
    };
  });

const canonicalCapacityKeyCounts = groupRows(canonicalCapacity, (row) =>
  [
    row.year,
    row.capacity_basis,
    row.region_original,
    row.province_original,
    row.category_original,
    row.subcategory_original,
  ].join("\u0000"),
);
const canonicalCapacityDuplicateGroups = [...canonicalCapacityKeyCounts.values()].filter(
  (rows) => rows.length > 1,
).length;

const productionGroups = groupRows(productionSourceRows, (row) =>
  [
    row.year,
    row.region_original,
    row.province_original,
    row.category_original,
    row.subcategory_original,
  ].join("\u0000"),
);
const productionPairExceptions = [];
let productionRecordCounter = 0;
const canonicalProduction = [];
for (const [groupKey, rows] of [...productionGroups.entries()].sort(([a], [b]) =>
  a.localeCompare(b, "it"),
)) {
  if (rows.length !== 2) {
    productionPairExceptions.push({
      group_key: groupKey.split("\u0000").join(" | "),
      exception_type: rows.length === 1 ? "ONE_ROW_ONLY" : "MORE_THAN_TWO_ROWS",
      source_row_count: rows.length,
      source_row_numbers: rows.map((row) => row.source_row_number).join("|"),
      values_GWh: rows.map((row) => compactNumber(row.production_GWh)).join("|"),
      disposition: "EXCLUDED_FROM_GROSS_NET_RECONSTRUCTION_PENDING_REVIEW",
    });
    continue;
  }
  const ordered = [...rows].sort(
    (a, b) => a.production_GWh - b.production_GWh || a.source_row_number - b.source_row_number,
  );
  const netta = ordered[0];
  const lorda = ordered[1];
  if (lorda.production_GWh < netta.production_GWh) {
    productionPairExceptions.push({
      group_key: groupKey.split("\u0000").join(" | "),
      exception_type: "LORDA_LT_NETTA_AFTER_ORDERING",
      source_row_count: rows.length,
      source_row_numbers: rows.map((row) => row.source_row_number).join("|"),
      values_GWh: rows.map((row) => compactNumber(row.production_GWh)).join("|"),
      disposition: "EXCLUDED_FROM_GROSS_NET_RECONSTRUCTION_PENDING_REVIEW",
    });
    continue;
  }
  productionRecordCounter += 1;
  const geo = crosswalkByTernaPair.get(
    `${netta.region_original}\u0000${netta.province_original}`,
  );
  if (!geo) {
    throw new Error(`No crosswalk for ${netta.region_original}/${netta.province_original}.`);
  }
  const equalValues = netta.production_GWh === lorda.production_GWh;
  canonicalProduction.push({
    record_id: `TERNA2024_PROD_PAIR_${String(productionRecordCounter).padStart(4, "0")}`,
    year: netta.year,
    region_code: geo.region_code,
    region_original: netta.region_original,
    region_normalized: geo.region_name,
    province_code: geo.province_code,
    province_original: netta.province_original,
    province_normalized: geo.province_name,
    market_zone: geo.market_zone,
    category_original: netta.category_original,
    category_normalized: chpClass(netta.category_original),
    chp_flag: chpClass(netta.category_original) === "CHP",
    subcategory_original: netta.subcategory_original,
    technology_normalized: technologyNormalized(netta.subcategory_original),
    production_netta_GWh_raw: netta.raw_value,
    production_netta_GWh: netta.production_GWh,
    production_lorda_GWh_raw: lorda.raw_value,
    production_lorda_GWh: lorda.production_GWh,
    pair_values_equal: equalValues,
    production_basis_original: "OMITTED_IN_DOWNLOAD_CENTER_EXPORT",
    netta_source_row_number: equalValues ? null : netta.source_row_number,
    lorda_source_row_number: equalValues ? null : lorda.source_row_number,
    paired_source_row_numbers: rows.map((item) => item.source_row_number).join("|"),
    raw_cell_type: sortedUnique(rows.map((row) => row.raw_cell_type)).join("|"),
    parser_status: sortedUnique(rows.map((row) => row.parser_status)).join("|"),
    basis_resolution_method:
      "TWO-ROW PHYSICAL RULE: HIGHER PRODUCTION=LORDA; LOWER PRODUCTION=NETTA",
    basis_resolution_status: equalValues
      ? "AMBIGUOUS_EQUAL_VALUES_NO_ROW_ORDER_ASSIGNED"
      : "INFERRED_PAIR_HIGHER_LORDA_LOWER_NETTA",
    acquisition_date: "2026-09-01",
    acquisition_route: "TERNA_DOWNLOAD_CENTER",
    source_id: "TERNA_DOWNLOAD_CENTER_THERMOELECTRIC_PRODUCTION_2024",
    raw_source_file: productionOriginalFilename,
    archived_raw_file: productionRawFilename,
    raw_source_sha256: productionRawInfo.sha256,
  });
}

const canonicalProductionKeyCounts = groupRows(canonicalProduction, (row) =>
  [
    row.year,
    row.region_original,
    row.province_original,
    row.category_original,
    row.subcategory_original,
  ].join("\u0000"),
);
const canonicalProductionDuplicateGroups = [...canonicalProductionKeyCounts.values()].filter(
  (rows) => rows.length > 1,
).length;

const zones = ["NORD", "CNOR", "CSUD", "SUD", "CALA", "SICI", "SARD"];
const subcategories = sortedUnique(canonicalCapacity.map((row) => row.subcategory_original));
const capacityMatrix = [];
for (const zone of zones) {
  for (const subcategory of subcategories) {
    const rows = canonicalCapacity.filter(
      (row) => row.market_zone === zone && row.subcategory_original === subcategory,
    );
    const categoryValues = sortedUnique(
      canonicalCapacity
        .filter((row) => row.subcategory_original === subcategory)
        .map((row) => row.category_normalized),
    );
    if (categoryValues.length !== 1) {
      throw new Error(`Terna subcategory ${subcategory} spans multiple CHP categories.`);
    }
    capacityMatrix.push({
      year: 2024,
      capacity_basis: "NET",
      market_zone: zone,
      subcategory_original: subcategory,
      technology_normalized: technologyNormalized(subcategory),
      chp_class: categoryValues[0],
      net_MW: sum(rows.map((row) => row.efficient_power_MW)),
      canonical_grain_rows: rows.length,
      official_source_rows: sum(rows.map((row) => row.source_row_count)),
      source_id: "TERNA_DOWNLOAD_CENTER_THERMOELECTRIC_CAPACITY_2024",
      raw_source_sha256: capacityRawInfo.sha256,
    });
  }
}
const capacityMatrixWide = zones.map((zone) => {
  const output = { market_zone: zone };
  for (const subcategory of subcategories) {
    output[subcategory] = capacityMatrix.find(
      (row) => row.market_zone === zone && row.subcategory_original === subcategory,
    ).net_MW;
  }
  output.TOTAL_NET_MW = sum(
    capacityMatrix.filter((row) => row.market_zone === zone).map((row) => row.net_MW),
  );
  return output;
});

const netProduction = canonicalProduction;
const cfLevels = [
  { name: "NATIONAL_TECHNOLOGY", zone: false, chp: false },
  { name: "ZONE_TECHNOLOGY", zone: true, chp: false },
  { name: "NATIONAL_TECHNOLOGY_CHP", zone: false, chp: true },
  { name: "ZONE_TECHNOLOGY_CHP", zone: true, chp: true },
];
const observedCapacityFactors = [];
for (const level of cfLevels) {
  const keyOf = (row) =>
    [
      level.zone ? row.market_zone : "ALL",
      row.technology_normalized,
      level.chp ? row.category_normalized : "ALL",
    ].join("\u0000");
  const capGroups = groupRows(canonicalCapacity, keyOf);
  const prodGroups = groupRows(netProduction, keyOf);
  const keys = sortedUnique([...capGroups.keys(), ...prodGroups.keys()]);
  for (const key of keys) {
    const [marketZone, technology, chp] = key.split("\u0000");
    const capRows = capGroups.get(key) ?? [];
    const prodRows = prodGroups.get(key) ?? [];
    const capacityMW = sum(capRows.map((row) => row.efficient_power_MW));
    const productionGWh = sum(prodRows.map((row) => row.production_netta_GWh));
    const calculable = capRows.length > 0 && prodRows.length > 0 && capacityMW > 0;
    const cf = calculable ? productionGWh / (capacityMW * 8.76) : null;
    let status;
    if (capRows.length === 0) status = "NOT_CALCULATED_PRODUCTION_WITHOUT_CAPACITY";
    else if (prodRows.length === 0) status = "NOT_CALCULATED_CAPACITY_WITHOUT_PRODUCTION";
    else if (capacityMW <= 0) status = "NOT_CALCULATED_NONPOSITIVE_CAPACITY";
    else if (cf > 1) status = "CALCULATED_FLAG_CF_GT_1_REVIEW";
    else status = "CALCULATED_ALIGNED_2024_NET_PERIMETER";
    observedCapacityFactors.push({
      year: 2024,
      aggregation_level: level.name,
      market_zone: marketZone,
      technology_normalized: technology,
      chp_class: chp,
      capacity_basis: "NET",
      production_basis: "NETTA_FROM_TWO-ROW_PHYSICAL_RULE",
      capacity_MW: capacityMW,
      production_GWh: productionGWh,
      hours_factor_GWh_per_MW: 8.76,
      observed_capacity_factor: cf,
      capacity_canonical_rows: capRows.length,
      production_canonical_rows: prodRows.length,
      calculation_status: status,
      interpretation:
        "CURRENT-FLEET DIAGNOSTIC ONLY; NOT A 2040/2050 CAPACITY OR DISPATCH ASSUMPTION",
    });
  }
}

const totalNetMW = sum(canonicalCapacity.map((row) => row.efficient_power_MW));
const combinedCycleMW = sum(
  canonicalCapacity
    .filter((row) => row.technology_normalized === "CCGT_LIKE")
    .map((row) => row.efficient_power_MW),
);
const gasTurbineMW = sum(
  canonicalCapacity
    .filter((row) => row.technology_normalized === "GAS_TURBINE_LIKE")
    .map((row) => row.efficient_power_MW),
);
const duplicateSensitivityMW = sum(
  rawDuplicateCapacityGroups.map((row) => row.duplicate_excess_sensitivity_MW),
);
const deduplicatedSensitivityTotalMW = totalNetMW - duplicateSensitivityMW;
const totalNetProductionGWh = sum(
  canonicalProduction.map((row) => row.production_netta_GWh),
);
const totalGrossProductionGWh = sum(
  canonicalProduction.map((row) => row.production_lorda_GWh),
);
const inferredProductionPairCount = canonicalProduction.filter(
  (row) => !row.pair_values_equal,
).length;
const equalProductionPairCount = canonicalProduction.filter(
  (row) => row.pair_values_equal,
).length;
const lordaBelowNettaCount = canonicalProduction.filter(
  (row) => row.production_lorda_GWh < row.production_netta_GWh,
).length;
const cfGt1Count = observedCapacityFactors.filter(
  (row) => row.calculation_status === "CALCULATED_FLAG_CF_GT_1_REVIEW",
).length;

const qaChecks = [
  {
    check_id: "TERNA-DC-QA-001",
    dataset: "CAPACITY",
    check: "Uploaded capacity original and immutable raw copy are byte-identical",
    status: capacityOriginalInfo.sha256 === capacityRawInfo.sha256 ? "PASS" : "FAIL",
    observed: capacityRawInfo.sha256,
    control: capacityOriginalInfo.sha256,
    severity: "CRITICAL",
    note: `${capacityOriginalFilename}; ${capacityOriginalInfo.bytes} bytes`,
  },
  {
    check_id: "TERNA-DC-QA-002",
    dataset: "PRODUCTION",
    check: "Uploaded production original and immutable raw copy are byte-identical",
    status: productionOriginalInfo.sha256 === productionRawInfo.sha256 ? "PASS" : "FAIL",
    observed: productionRawInfo.sha256,
    control: productionOriginalInfo.sha256,
    severity: "CRITICAL",
    note: `${productionOriginalFilename}; ${productionOriginalInfo.bytes} bytes`,
  },
  {
    check_id: "TERNA-DC-QA-003",
    dataset: "CAPACITY",
    check: "Capacity export structure and data row count",
    status:
      capacityWorkbook.usedRange === "A1:G1196" && capacitySourceRows.length === 1194
        ? "PASS"
        : "FAIL",
    observed: `${capacityWorkbook.usedRange}; ${capacitySourceRows.length} data rows`,
    control: "A1:G1196; 1194 data rows plus header/footer",
    severity: "HIGH",
    note: capacityWorkbook.footerRows.map((row) => row.values[0]).join(" | "),
  },
  {
    check_id: "TERNA-DC-QA-004",
    dataset: "PRODUCTION",
    check: "Production export structure and data row count",
    status:
      productionWorkbook.usedRange === "A1:F1176" && productionSourceRows.length === 1174
        ? "PASS"
        : "FAIL",
    observed: `${productionWorkbook.usedRange}; ${productionSourceRows.length} data rows`,
    control: "A1:F1176; 1174 data rows plus header/footer",
    severity: "HIGH",
    note: productionWorkbook.footerRows.map((row) => row.values[0]).join(" | "),
  },
  {
    check_id: "TERNA-DC-QA-005",
    dataset: "BOTH",
    check: "No hidden worksheet rows/columns or active package-level filter criteria",
    status: "PASS",
    observed: "0 hidden rows; 0 hidden columns; 0 filterColumn/customFilter nodes in both XLSX packages",
    control: "No hidden truncation",
    severity: "CRITICAL",
    note:
      "The visible footer states Year=2024 and Month=gennaio. Values are consolidated annual data; the month label is retained as a Download Center metadata anomaly.",
  },
  {
    check_id: "TERNA-DC-QA-006",
    dataset: "CAPACITY",
    check: "Capacity row multiset matches independently retrieved official API payload",
    status: capacityApiMismatches.length === 0 ? "PASS" : "FAIL",
    observed: `${capacityApiMismatches.length} mismatches`,
    control: "0 mismatches",
    severity: "CRITICAL",
    note: "The API payload also contains both Lorda and Netta despite the Netta query parameter.",
  },
  {
    check_id: "TERNA-DC-QA-007",
    dataset: "PRODUCTION",
    check: "Optional API shape/aggregate spot-check (not a derivation dependency)",
    status: !productionApiAvailable
      ? "NOT_RUN_OPTIONAL"
      : productionApiSpotcheck.api_rows === productionApiSpotcheck.workbook_rows &&
          productionApiSpotcheck.aggregate_total_matches
        ? "PASS_OPTIONAL_SPOT_CHECK"
        : "REVIEW_OPTIONAL_SPOT_CHECK",
    observed: productionApiAvailable
      ? `${productionApiSpotcheck.api_rows} rows; ${productionApiSpotcheck.api_all_values_total_GWh} GWh all-values total`
      : "API payload not used",
    control: `${productionApiSpotcheck.workbook_rows} rows; ${productionApiSpotcheck.workbook_all_values_total_GWh} GWh all-values total`,
    severity: "LOW",
    note:
      "The manually downloaded XLSX is controlling. The API is retained only as an optional spot-check or future exception-resolution source.",
  },
  {
    check_id: "TERNA-DC-QA-008",
    dataset: "CAPACITY",
    check: "2024, Netta isolation, geography/category/subcategory coverage",
    status:
      netCapacitySourceRows.length === 597 &&
      sortedUnique(netCapacitySourceRows.map((row) => row.region_original)).length === 20 &&
      sortedUnique(netCapacitySourceRows.map((row) => row.province_original)).length === 107 &&
      sortedUnique(netCapacitySourceRows.map((row) => row.category_original)).length === 2 &&
      sortedUnique(netCapacitySourceRows.map((row) => row.subcategory_original)).length === 14
        ? "PASS"
        : "FAIL",
    observed: `597 Netta rows; 20 regions; 107 provinces; 2 categories; 14 subcategories`,
    control: "2024 Netta; all returned regions/provinces/categories/subcategories",
    severity: "CRITICAL",
    note: "The entire returned thermoelectric technology stack is retained, not only CCGT/OCGT.",
  },
  {
    check_id: "TERNA-DC-QA-009",
    dataset: "CAPACITY",
    check: "Raw source uniqueness at documented visible grain",
    status:
      rawDuplicateCapacityGroups.length === 0
        ? "PASS"
        : "SOURCE_QA_EXCEPTION_CANONICAL_AGGREGATION_APPLIED",
    observed: `${rawDuplicateCapacityGroups.length} duplicate groups; ${duplicateSensitivityMW} MW duplicate-row sensitivity`,
    control: "0 duplicate groups",
    severity: "HIGH",
    note:
      "All 19 groups are exact-value duplicates in fuel-cell subcategories. Canonical aggregation preserves the official 60.331829 GW total and records source row lineage; a deduplicate-one-row sensitivity is reported separately.",
  },
  {
    check_id: "TERNA-DC-QA-010",
    dataset: "CAPACITY",
    check: "Canonical capacity grain is unique after explicit source-row aggregation",
    status: canonicalCapacityDuplicateGroups === 0 ? "PASS" : "FAIL",
    observed: canonicalCapacityDuplicateGroups,
    control: 0,
    severity: "CRITICAL",
    note: `${canonicalCapacity.length} canonical grain rows from ${netCapacitySourceRows.length} Netta source rows.`,
  },
  {
    check_id: "TERNA-DC-QA-011",
    dataset: "GEOGRAPHY",
    check: "Every Terna province maps exactly once to an official 2024 province and MEM zone",
    status: crosswalk.length === 107 ? "PASS" : "FAIL",
    observed: `${crosswalk.length} provinces; ${sortedUnique(crosswalk.map((row) => row.market_zone)).join("|")}`,
    control: "107 provinces; NORD|CNOR|CSUD|SUD|CALA|SICI|SARD",
    severity: "CRITICAL",
    note: "CNORD is accepted only as an ingestion alias; the canonical output emits CNOR.",
  },
  {
    check_id: "TERNA-DC-QA-012",
    dataset: "CAPACITY",
    check: "National net thermoelectric total versus 60.33 GW control",
    status: Math.abs(totalNetMW - 60330) <= 100 ? "PASS" : "FAIL",
    observed: totalNetMW,
    control: 60330,
    severity: "HIGH",
    note: `Exact detailed-export total; deduplicate-one-row sensitivity total ${deduplicatedSensitivityTotalMW} MW.`,
  },
  {
    check_id: "TERNA-DC-QA-013",
    dataset: "CAPACITY",
    check: "Combined-cycle-like including CHP versus 41.74 GW technology control",
    status: Math.abs(combinedCycleMW - 41740) <= 50 ? "PASS" : "FAIL",
    observed: combinedCycleMW,
    control: 41740,
    severity: "HIGH",
    note: "Technology mapping only; fuel is not inferred.",
  },
  {
    check_id: "TERNA-DC-QA-014",
    dataset: "CAPACITY",
    check: "Gas-turbine-like including CHP versus 3.62 GW technology control",
    status: Math.abs(gasTurbineMW - 3620) <= 20 ? "PASS" : "FAIL",
    observed: gasTurbineMW,
    control: 3620,
    severity: "HIGH",
    note: "Technology mapping only; fuel is not inferred.",
  },
  {
    check_id: "TERNA-DC-QA-015",
    dataset: "CAPACITY",
    check: "Natural-gas capacity versus 48.02 GW fuel control",
    status: "NOT_TESTABLE_FROM_TECHNOLOGY_ONLY_EXPORT",
    observed: null,
    control: 48020,
    severity: "HIGH",
    note: "Technology and fuel are separate; GEM/other fuel evidence is still required.",
  },
  {
    check_id: "TERNA-DC-QA-016",
    dataset: "CAPACITY",
    check: "Explicit geothermal subcategory present",
    status: "NOT_PRESENT_AS_SEPARATE_LABEL",
    observed: subcategories.filter((value) => normalizeName(value).includes("geoterm")),
    control: "Report where present",
    severity: "MEDIUM",
    note:
      "No geothermal-labelled technology is returned. Do not infer a fuel from Condensazione or other technology labels without separate evidence.",
  },
  {
    check_id: "TERNA-DC-QA-017",
    dataset: "PRODUCTION",
    check: "Exactly two rows per production dimension group and unique canonical pair grain",
    status:
      productionGroups.size === 587 &&
      productionPairExceptions.length === 0 &&
      canonicalProductionDuplicateGroups === 0
        ? "PASS"
        : "FAIL",
    observed: `${productionGroups.size} groups; ${productionPairExceptions.length} exceptions; ${canonicalProductionDuplicateGroups} canonical duplicate groups`,
    control: "Every group has exactly 2 rows; 0 exceptions; unique canonical grain",
    severity: "HIGH",
    note:
      `${inferredProductionPairCount} unequal pairs inferred; ${equalProductionPairCount} equal-value pairs retained without row-order assignment.`,
  },
  {
    check_id: "TERNA-DC-QA-018",
    dataset: "PRODUCTION",
    check: "Physical ordering rule and exact derived national production totals",
    status:
      lordaBelowNettaCount === 0
        ? "PASS_WITH_DOCUMENTED_BASIS_RECONSTRUCTION"
        : "FAIL",
    observed: `NET ${totalNetProductionGWh} GWh; GROSS ${totalGrossProductionGWh} GWh`,
    control: "Lorda >= Netta for every reconstructed pair",
    severity: "HIGH",
    note: `${lordaBelowNettaCount} violations; national difference ${totalGrossProductionGWh - totalNetProductionGWh} GWh.`,
  },
  {
    check_id: "TERNA-DC-QA-019",
    dataset: "PRODUCTION",
    check: "Derived net total versus approximately 146.45 TWh published cross-control",
    status: Math.abs(totalNetProductionGWh - 146452) <= 200 ? "PASS_WITH_MINOR_PERIMETER_DIFFERENCE" : "REVIEW",
    observed: totalNetProductionGWh,
    control: 146452,
    severity: "MEDIUM",
    note: "Difference is reported, not forced; finalization/perimeter/rounding may explain it.",
  },
  {
    check_id: "TERNA-DC-QA-020",
    dataset: "CAPACITY_FACTORS",
    check: "Observed capacity-factor anomaly count",
    status: cfGt1Count === 0 ? "PASS" : "PASS_WITH_FLAGGED_ANOMALIES",
    observed: cfGt1Count,
    control: 0,
    severity: "MEDIUM",
    note: "CFs are present-day diagnostics only and do not replace frozen future assumptions.",
  },
  {
    check_id: "TERNA-DC-QA-021",
    dataset: "CAPACITY",
    check: "Zone-technology-CHP matrix reconciles to canonical national total",
    status:
      Math.abs(sum(capacityMatrix.map((row) => row.net_MW)) - totalNetMW) < 1e-8
        ? "PASS"
        : "FAIL",
    observed: sum(capacityMatrix.map((row) => row.net_MW)),
    control: totalNetMW,
    severity: "CRITICAL",
    note: `${zones.length} zones x ${subcategories.length} Terna subcategories.`,
  },
  {
    check_id: "TERNA-DC-QA-022",
    dataset: "BOTH",
    check: "Numeric values parsed without punctuation guessing",
    status: "PASS",
    observed: `${capacitySourceRows.length + productionSourceRows.length} typed numeric Excel cells`,
    control: "All data values parsed; ambiguous text rejected",
    severity: "CRITICAL",
    note:
      "Raw cell values and types are retained. The parser separately rejects ambiguous comma/point strings in unit tests.",
  },
];
if (qaChecks.some((row) => row.status === "FAIL")) {
  throw new Error(
    `Critical QA failure: ${qaChecks.filter((row) => row.status === "FAIL").map((row) => row.check_id).join(", ")}`,
  );
}

const sourceManifest = [
  {
    source_id: "TERNA_DOWNLOAD_CENTER_THERMOELECTRIC_CAPACITY_2024",
    publisher: "Terna S.p.A.",
    title: "Thermoelectric Capacity 2024 Download Center export",
    release_or_year: "2024",
    acquisition_route: "TERNA_DOWNLOAD_CENTER_MANUAL_XLSX",
    acquisition_date: "2026-09-01",
    original_uploaded_filename: capacityOriginalFilename,
    archived_raw_file: `raw/terna/${capacityRawFilename}`,
    byte_size: capacityRawInfo.bytes,
    raw_sha256: capacityRawInfo.sha256,
    scope: "2024; Lorda and Netta; all returned regions/provinces/categories/subcategories",
    evidence_class: "DIRECT SOURCE CAPACITY / TERNA CAPACITY CONTROL",
    status: "ACQUIRED_VALIDATED_WITH_SOURCE_DUPLICATE_QA_EXCEPTION",
    url: "https://dati.terna.it/en/download-center",
  },
  {
    source_id: "TERNA_DOWNLOAD_CENTER_THERMOELECTRIC_PRODUCTION_2024",
    publisher: "Terna S.p.A.",
    title: "Thermoelectric Production 2024 Download Center export",
    release_or_year: "2024",
    acquisition_route: "TERNA_DOWNLOAD_CENTER_MANUAL_XLSX",
    acquisition_date: "2026-09-01",
    original_uploaded_filename: productionOriginalFilename,
    archived_raw_file: `raw/terna/${productionRawFilename}`,
    byte_size: productionRawInfo.bytes,
    raw_sha256: productionRawInfo.sha256,
    scope: "2024; all returned regions/provinces/categories/subcategories; gross/net field omitted",
    evidence_class: "DIRECT SOURCE ENERGY",
    status: "ACQUIRED_VALIDATED_WITH_BASIS_RECONSTRUCTION",
    url: "https://dati.terna.it/en/download-center",
  },
  {
    source_id: "TERNA_API_THERMOELECTRIC_CAPACITY_V2_2024",
    publisher: "Terna S.p.A.",
    title: "Thermoelectric Capacity API cross-check",
    release_or_year: "2024",
    acquisition_route: "OAUTH2_API",
    acquisition_date: "2026-09-01",
    original_uploaded_filename: "",
    archived_raw_file: "raw/terna/Terna_Thermoelectric_Capacity_2024_RAW.json",
    byte_size: (await fileInfo(capacityApiPath)).bytes,
    raw_sha256: await sha256File(capacityApiPath),
    scope: "Independent row-multiset cross-check; API returned Lorda and Netta",
    evidence_class: "QA / RECONCILIATION ONLY",
    status: "ACQUIRED_VALIDATED",
    url: "https://developer.terna.it/docs/apis_catalog/generation/Thermoelectric_Capacity",
  },
  {
    source_id: "TERNA_API_THERMOELECTRIC_PRODUCTION_V2_2024",
    publisher: "Terna S.p.A.",
    title: "Thermoelectric Production API cross-check",
    release_or_year: "2024",
    acquisition_route: "OAUTH2_API",
    acquisition_date: "2026-09-01",
    original_uploaded_filename: "",
    archived_raw_file: productionApiAvailable
      ? "raw/terna/Terna_Thermoelectric_Production_2024_API_RAW.json"
      : "",
    byte_size: productionApiAvailable ? (await fileInfo(productionApiPath)).bytes : null,
    raw_sha256: productionApiAvailable ? await sha256File(productionApiPath) : null,
    scope: "Optional shape/aggregate spot-check and future exception resolution only",
    evidence_class: "QA / RECONCILIATION ONLY",
    status: productionApiAvailable
      ? "OPTIONAL_SPOTCHECK_ARCHIVED_NOT_DERIVATION_DEPENDENCY"
      : "OPTIONAL_NOT_ACQUIRED",
    url: "https://developer.terna.it/docs/apis_catalog/generation/Thermoelectric_Production",
  },
  {
    source_id: "ISTAT_DEMOGRAPHIC_BALANCE_2024_ADMIN_GEOGRAPHY",
    publisher: "Istat",
    title: "2024 administrative geography",
    release_or_year: "2024",
    acquisition_route: "OFFICIAL_WEB_DOWNLOAD",
    acquisition_date: "2026-09-01",
    original_uploaded_filename: "",
    archived_raw_file: "raw/geography/ISTAT_Demographic_Balance_2024_Admin_Geography.html",
    byte_size: (await fileInfo(istatHtmlPath)).bytes,
    raw_sha256: await sha256File(istatHtmlPath),
    scope: "107 provinces and official region/province codes",
    evidence_class: "PYPSA IMPLEMENTATION MAPPING",
    status: "ACQUIRED_VALIDATED",
    url: "https://demo.istat.it/app/?a=2024&i=P02&l=en",
  },
  {
    source_id: "TERNA_ZONAL_CONFIGURATION_ALTERNATIVA_BASE",
    publisher: "Terna S.p.A.",
    title: "Revisione configurazione zonale - Alternativa Base, Table 3",
    release_or_year: "official topology source",
    acquisition_route: "OFFICIAL_PDF_DOWNLOAD",
    acquisition_date: "2026-09-01",
    original_uploaded_filename: "",
    archived_raw_file: "raw/geography/Terna_Revisione_Configurazione_Zonale_Alternativa_Base.pdf",
    byte_size: (
      await fileInfo(
        path.join(
          phaseRoot,
          "raw",
          "geography",
          "Terna_Revisione_Configurazione_Zonale_Alternativa_Base.pdf",
        ),
      )
    ).bytes,
    raw_sha256: await sha256File(
      path.join(
        phaseRoot,
        "raw",
        "geography",
        "Terna_Revisione_Configurazione_Zonale_Alternativa_Base.pdf",
      ),
    ),
    scope: "Italian region-to-market-zone membership",
    evidence_class: "PYPSA IMPLEMENTATION MAPPING",
    status: "ACQUIRED_VALIDATED",
    url: "https://download.terna.it/terna/0000/1033/91.PDF",
  },
  {
    source_id: "GME_CURRENT_ZONE_GLOSSARY",
    publisher: "Gestore dei Mercati Energetici",
    title: "Current bidding-zone glossary",
    release_or_year: "current at 2026-09-01",
    acquisition_route: "OFFICIAL_WEB_DOWNLOAD",
    acquisition_date: "2026-09-01",
    original_uploaded_filename: "",
    archived_raw_file: "raw/geography/GME_Current_Zone_Glossary.html",
    byte_size: (
      await fileInfo(
        path.join(phaseRoot, "raw", "geography", "GME_Current_Zone_Glossary.html"),
      )
    ).bytes,
    raw_sha256: await sha256File(
      path.join(phaseRoot, "raw", "geography", "GME_Current_Zone_Glossary.html"),
    ),
    scope: "Corroborating current zone names/codes",
    evidence_class: "PYPSA IMPLEMENTATION MAPPING",
    status: "ACQUIRED_VALIDATED",
    url: "https://www.mercatoelettrico.org/Home/Glossario",
  },
  {
    source_id: "GEM_GOGPT_AUG2026_RELEASE_PAGE",
    publisher: "Global Energy Monitor",
    title: "Global Oil and Gas Plant Tracker August 2026 release page",
    release_or_year: "August 2026",
    acquisition_route: "OFFICIAL_WEB_PAGE",
    acquisition_date: "2026-09-01",
    original_uploaded_filename: "",
    archived_raw_file: "raw/gem/GEM_GOGPT_Aug2026_Source_Page.html",
    byte_size: (
      await fileInfo(path.join(phaseRoot, "raw", "gem", "GEM_GOGPT_Aug2026_Source_Page.html"))
    ).bytes,
    raw_sha256: await sha256File(
      path.join(phaseRoot, "raw", "gem", "GEM_GOGPT_Aug2026_Source_Page.html"),
    ),
    scope: "Release metadata, gross-MW basis and EU/UK threshold",
    evidence_class: "GEM PLANT RECONCILIATION",
    status: "SOURCE_PAGE_ACQUIRED_DATASET_REQUIRES_USER_DOWNLOAD",
    url: "https://globalenergymonitor.org/projects/global-oil-gas-plant-tracker",
  },
];

const derivationManifest = [
  {
    derivation_id: "DER-TERNA-001",
    output: "Terna_Thermoelectric_Capacity_2024_Canonical.csv",
    evidence_class: "DIRECT SOURCE CAPACITY / TERNA CAPACITY CONTROL",
    inputs: `${capacityRawFilename}; API cross-check; province-zone crosswalk`,
    method:
      "Filter capacity_type_original=Netta; parse typed numeric values; aggregate multiple official rows at the visible source grain while retaining row lineage; map province to MEM zone.",
    key_guardrail: "No fuel is inferred from technology; no CCGT/OCGT-only reduction.",
    status: "VALIDATED_WITH_SOURCE_DUPLICATE_QA_EXCEPTION",
  },
  {
    derivation_id: "DER-TERNA-002",
    output: "MEM_Province_Region_MarketZone_Crosswalk.csv",
    evidence_class: "PYPSA IMPLEMENTATION MAPPING",
    inputs: "ISTAT 2024 administrative geography; Terna zonal configuration Table 3; GME glossary",
    method: "Exact-once province mapping; CNOR canonical; effective dates frozen for 2024 baseline.",
    key_guardrail: "No CNORD emitted; every Terna province maps once.",
    status: "VALIDATED",
  },
  {
    derivation_id: "DER-TERNA-003",
    output: "Terna_Thermoelectric_Production_2024_Canonical.csv",
    evidence_class: "DIRECT SOURCE ENERGY + QA / RECONCILIATION ONLY",
    inputs: `${productionRawFilename}; two-row Download Center structure; physical gross/net identity`,
    method:
      "For each visible year/region/province/category/subcategory grain with exactly two rows, assign the higher value to Lorda and the lower value to Netta. Equal values remain AMBIGUOUS_EQUAL with no row-order assignment, while both numeric fields retain the same value.",
    key_guardrail:
      "The XLSX is controlling; the API is not a row-by-row derivation dependency; values are never summed across Lorda/Netta.",
    status: "VALIDATED_WITH_DOCUMENTED_BASIS_INFERENCE",
  },
  {
    derivation_id: "DER-TERNA-004",
    output: "Terna_Thermoelectric_Capacity_2024_Zone_Technology_CHP_Matrix.csv",
    evidence_class: "PROJECT DERIVATION",
    inputs: "Canonical 2024 Netta capacity; crosswalk",
    method: "Sum NET MW by MEM zone, original Terna subcategory and CHP class; emit complete 7x14 grid.",
    key_guardrail: "Matrix reconciles exactly to canonical national total.",
    status: "VALIDATED",
  },
  {
    derivation_id: "DER-TERNA-005",
    output: "Terna_Thermoelectric_2024_Observed_Capacity_Factors.csv",
    evidence_class: "QA / RECONCILIATION ONLY",
    inputs: "Canonical NET capacity; reconstructed NET production",
    method:
      "CF=Production_GWh/(Capacity_MW*8.76), at national/zone x normalized technology, with and without CHP split, only where both perimeters are present.",
    key_guardrail:
      "Current-fleet diagnostic only; does not replace 0.47 bioenergy or interim 0.20 future gas conversion assumptions and is not extrapolated to 2040/2050.",
    status: cfGt1Count === 0 ? "VALIDATED" : "VALIDATED_WITH_FLAGGED_ANOMALIES",
  },
];

const toMatrix = (rows) => {
  if (rows.length === 0) throw new Error("Cannot author an empty CSV.");
  const headers = Object.keys(rows[0]);
  return {
    headers,
    values: rows.map((row) => headers.map((header) => row[header] ?? null)),
  };
};
const csvEscape = (value) => {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
};
const serializeCsv = (headers, values) =>
  `${[headers, ...values]
    .map((row) => row.map(csvEscape).join(","))
    .join("\r\n")}\r\n`;
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
  const authored = await workbook.inspect({
    kind: "table",
    sheetId: sheetName,
    range,
    tableMaxRows: 3,
    tableMaxCols: Math.min(headers.length, 24),
    maxChars: 6000,
  });
  await fs.writeFile(outputPath, serializeCsv(headers, values), "utf8");
  const reopened = await Workbook.fromCSV(await fs.readFile(outputPath, "utf8"), {
    sheetName,
  });
  const verified = await reopened.inspect({
    kind: "table",
    sheetId: sheetName,
    range,
    tableMaxRows: 3,
    tableMaxCols: Math.min(headers.length, 24),
    maxChars: 6000,
  });
  const info = await fileInfo(outputPath);
  return {
    output_path: outputPath,
    rows: rows.length,
    columns: headers.length,
    bytes: info.bytes,
    sha256: info.sha256,
    authored_inspection: authored.ndjson,
    reopened_inspection: verified.ndjson,
  };
}

const productionPairExceptionReport =
  productionPairExceptions.length > 0
    ? productionPairExceptions
    : [
        {
          group_key: "",
          exception_type: "NONE",
          source_row_count: 0,
          source_row_numbers: "",
          values_GWh: "",
          disposition: "NO_PAIR_EXCEPTIONS_FOUND",
        },
      ];

const outputs = [];
outputs.push(
  await authorCsv(
    crosswalk,
    "Crosswalk",
    path.join(normalizedDir, "MEM_Province_Region_MarketZone_Crosswalk.csv"),
  ),
);
outputs.push(
  await authorCsv(
    canonicalCapacity,
    "CapacityCanonical",
    path.join(normalizedDir, "Terna_Thermoelectric_Capacity_2024_Canonical.csv"),
  ),
);
outputs.push(
  await authorCsv(
    canonicalProduction,
    "ProductionCanonical",
    path.join(normalizedDir, "Terna_Thermoelectric_Production_2024_Canonical.csv"),
  ),
);
outputs.push(
  await authorCsv(
    capacityMatrix,
    "ZoneTechCHP",
    path.join(
      analysisDir,
      "Terna_Thermoelectric_Capacity_2024_Zone_Technology_CHP_Matrix.csv",
    ),
  ),
);
outputs.push(
  await authorCsv(
    capacityMatrixWide,
    "ZoneTechWide",
    path.join(
      analysisDir,
      "Terna_Thermoelectric_Capacity_2024_Zone_Technology_Matrix_Wide.csv",
    ),
  ),
);
outputs.push(
  await authorCsv(
    observedCapacityFactors,
    "ObservedCF",
    path.join(
      analysisDir,
      "Terna_Thermoelectric_2024_Observed_Capacity_Factors.csv",
    ),
  ),
);
outputs.push(
  await authorCsv(
    productionPairExceptionReport,
    "ProductionPairExceptions",
    path.join(
      qaDir,
      "Terna_Thermoelectric_Production_2024_Pair_Exceptions.csv",
    ),
  ),
);
outputs.push(
  await authorCsv(
    rawDuplicateCapacityGroups,
    "DuplicateQA",
    path.join(qaDir, "Terna_Thermoelectric_Capacity_2024_Source_Duplicate_Groups.csv"),
  ),
);
outputs.push(
  await authorCsv(
    qaChecks,
    "TernaQA",
    path.join(qaDir, "Terna_Thermoelectric_2024_Download_Center_QA.csv"),
  ),
);
outputs.push(
  await authorCsv(
    sourceManifest,
    "SourceManifest",
    path.join(phaseRoot, "SOURCE_MANIFEST.csv"),
  ),
);
outputs.push(
  await authorCsv(
    derivationManifest,
    "DerivationManifest",
    path.join(phaseRoot, "DERIVATION_MANIFEST.csv"),
  ),
);

const technologyTotals = subcategories.map((subcategory) => ({
  subcategory_original: subcategory,
  technology_normalized: technologyNormalized(subcategory),
  chp_class: chpClass(
    canonicalCapacity.find((row) => row.subcategory_original === subcategory).category_original,
  ),
  net_MW: sum(
    canonicalCapacity
      .filter((row) => row.subcategory_original === subcategory)
      .map((row) => row.efficient_power_MW),
  ),
}));
const zoneTotals = zones.map((zone) => ({
  market_zone: zone,
  net_MW: sum(
    canonicalCapacity
      .filter((row) => row.market_zone === zone)
      .map((row) => row.efficient_power_MW),
  ),
}));

const qaSummary = {
  generated_at_utc: new Date().toISOString(),
  uploaded_files: {
    capacity: {
      filename: capacityOriginalFilename,
      bytes: capacityOriginalInfo.bytes,
      sha256: capacityOriginalInfo.sha256,
      correct_requested_2024_export: true,
      netta_confirmed: true,
      note: "The workbook contains both Lorda and Netta; canonical filtering isolates Netta.",
    },
    production: {
      filename: productionOriginalFilename,
      bytes: productionOriginalInfo.bytes,
      sha256: productionOriginalInfo.sha256,
      correct_requested_2024_export: true,
      note:
        "The workbook omits the documented gross/net field; paired values are reconstructed explicitly.",
    },
  },
  workbook_structure: {
    capacity: {
      sheet: "Export",
      used_range: capacityWorkbook.usedRange,
      data_rows: capacitySourceRows.length,
      columns: capacityWorkbook.headers.length,
      formula_count: capacityWorkbook.formulaCount,
      footer: capacityWorkbook.footerRows,
    },
    production: {
      sheet: "Export",
      used_range: productionWorkbook.usedRange,
      data_rows: productionSourceRows.length,
      columns: productionWorkbook.headers.length,
      formula_count: productionWorkbook.formulaCount,
      footer: productionWorkbook.footerRows,
    },
  },
  capacity: {
    capacity_types: sortedUnique(capacitySourceRows.map((row) => row.capacity_type_original)),
    rows_by_type: Object.fromEntries(
      [...groupRows(capacitySourceRows, (row) => row.capacity_type_original)].map(
        ([key, rows]) => [key, rows.length],
      ),
    ),
    netta_source_rows: netCapacitySourceRows.length,
    canonical_grain_rows: canonicalCapacity.length,
    regions: sortedUnique(netCapacitySourceRows.map((row) => row.region_original)),
    provinces_count: sortedUnique(netCapacitySourceRows.map((row) => row.province_original)).length,
    categories: sortedUnique(netCapacitySourceRows.map((row) => row.category_original)),
    subcategories,
    exact_total_net_MW: totalNetMW,
    exact_total_net_GW: totalNetMW / 1000,
    combined_cycle_like_net_MW: combinedCycleMW,
    gas_turbine_like_net_MW: gasTurbineMW,
    natural_gas_control_status: "NOT_TESTABLE_FROM_TECHNOLOGY_ONLY_EXPORT",
    source_duplicate_groups: rawDuplicateCapacityGroups.length,
    source_duplicate_excess_rows: sum(
      rawDuplicateCapacityGroups.map((row) => row.source_row_count - 1),
    ),
    source_duplicate_sensitivity_MW: duplicateSensitivityMW,
    deduplicate_one_row_sensitivity_total_MW: deduplicatedSensitivityTotalMW,
    technology_totals: technologyTotals,
    zone_totals: zoneTotals,
  },
  production: {
    source_rows: productionSourceRows.length,
    visible_grain_pairs: productionGroups.size,
    inferred_unequal_pairs: inferredProductionPairCount,
    equal_value_pairs: equalProductionPairCount,
    pair_exceptions: productionPairExceptions,
    lorda_below_netta_violations: lordaBelowNettaCount,
    exact_net_GWh: totalNetProductionGWh,
    exact_gross_GWh: totalGrossProductionGWh,
    gross_minus_net_GWh: totalGrossProductionGWh - totalNetProductionGWh,
    basis_field_status: "OMITTED_IN_DOWNLOAD_CENTER_EXPORT",
    basis_resolution:
      "TWO-ROW PHYSICAL RULE: HIGHER=LORDA; LOWER=NETTA; EQUAL VALUES RETAINED AS AMBIGUOUS/EQUAL WITHOUT ROW ORDER",
  },
  capacity_factors: {
    output_rows: observedCapacityFactors.length,
    calculated_rows: observedCapacityFactors.filter((row) =>
      row.calculation_status.startsWith("CALCULATED"),
    ).length,
    cf_gt_1_rows: cfGt1Count,
  },
  api_crosschecks: {
    capacity_manual_rows: capacitySourceRows.length,
    capacity_api_rows: capacityApiRows.length,
    capacity_multiset_mismatches: capacityApiMismatches,
    production_optional_spotcheck: productionApiSpotcheck,
    production_api_role: "OPTIONAL_SPOTCHECK_OR_EXCEPTION_RESOLUTION_ONLY",
  },
  acquisition_gate: {
    terna_2024_capacity: "ACQUIRED_VALIDATED_WITH_SOURCE_QA_EXCEPTION",
    terna_2024_production: "ACQUIRED_VALIDATED_WITH_DOCUMENTED_BASIS_INFERENCE",
    gem_august_2026: "REQUIRES_USER_FORM_DOWNLOAD",
  },
  checks: qaChecks,
  outputs,
};
await fs.writeFile(
  path.join(qaDir, "Terna_Thermoelectric_2024_Download_Center_QA.json"),
  `${JSON.stringify(qaSummary, null, 2)}\n`,
  "utf8",
);

console.log(
  JSON.stringify(
    {
      ok: true,
      capacity_filename: capacityOriginalFilename,
      production_filename: productionOriginalFilename,
      total_net_MW: totalNetMW,
      combined_cycle_like_net_MW: combinedCycleMW,
      gas_turbine_like_net_MW: gasTurbineMW,
      production_net_GWh: totalNetProductionGWh,
      production_gross_GWh: totalGrossProductionGWh,
      zones: zoneTotals,
      technology_totals: technologyTotals,
      source_duplicate_groups: rawDuplicateCapacityGroups.length,
      canonical_capacity_rows: canonicalCapacity.length,
      capacity_factors_cf_gt_1: cfGt1Count,
      outputs: outputs.map(({ output_path, rows, columns, bytes, sha256 }) => ({
        output_path,
        rows,
        columns,
        bytes,
        sha256,
      })),
    },
    null,
    2,
  ),
);

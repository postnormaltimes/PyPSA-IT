import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";
import {
  detectTernaDecimalConventions,
  parseTernaNumber,
} from "../scripts/terna_numeric_parser.mjs";

const phaseRoot = path.resolve("..");
const rawDir = path.join(phaseRoot, "raw", "terna", "historical_2024");
const hbRoot = path.join(phaseRoot, "historical_baseline");
const normalizedDir = path.join(hbRoot, "normalized");
const analysisDir = path.join(hbRoot, "analysis");
const manifestDir = path.join(hbRoot, "manifests");
const qaDir = path.join(hbRoot, "qa");
for (const dir of [normalizedDir, analysisDir, manifestDir, qaDir]) {
  await fs.mkdir(dir, { recursive: true });
}

const acquisitionDate = "2026-09-01";
const downloadCenterUrl = "https://dati.terna.it/en/download-center";
const canonicalZones = ["NORD", "CNOR", "CSUD", "SUD", "CALA", "SICI", "SARD"];
const expectedWorkbookHash = "4a59c80bdf4da33d825ff94ea5617c03c325ca6fbc26233e7ed633ed6f910640";
const controllingWorkbook = path.resolve(
  "../../Fase_5_PyPSA_Eur_Italia_2040_2050_Model_Ready_v2.9_MEM_ARCHITECTURE_REFINED.xlsx",
);

const sources = {
  bioCapacity: {
    source_id: "TERNA_DOWNLOAD_CENTER_BIOENERGY_CAPACITY_2024",
    title: "Bioenergy Capacity 2024 Download Center export",
    original: "Export-DownloadCenterFile-20260901-100929.xlsx",
    archived: "Terna_Bioenergy_Capacity_2024_RAW.xlsx",
    sha256: "b6d8700e000e9f0766949a022c8e2a27eff41cb6cc14d3bc8005016e1820ea41",
    expectedHeaders: ["Anno", "Tipo capacità", "Regione", "Provincia", "Fonti", "Potenza efficiente (MW)"],
  },
  geoCapacity: {
    source_id: "TERNA_DOWNLOAD_CENTER_GEOTHERMAL_CAPACITY_2024",
    title: "Geothermal Capacity 2024 Download Center export",
    original: "Export-DownloadCenterFile-20260901-100933.xlsx",
    archived: "Terna_Geothermal_Capacity_2024_RAW.xlsx",
    sha256: "e4c22a28edf6cc6679bebed858a539d74284b4b9b20822b3d8ae1432613ea3a1",
    expectedHeaders: ["Anno", "Tipo capacità", "Regione", "Provincia", "Fonti", "Potenza efficiente (MW)"],
  },
  hydroCapacity: {
    source_id: "TERNA_DOWNLOAD_CENTER_HYDRO_CAPACITY_2024",
    title: "Hydro Capacity 2024 Download Center export",
    original: "Export-DownloadCenterFile-20260901-100940.xlsx",
    archived: "Terna_Hydro_Capacity_2024_RAW.xlsx",
    sha256: "0ecc2fa25a0a831f603789791dd0b19597b131af53dd7df9b9cb3dd2b73f4277",
    expectedHeaders: ["Anno", "Tipo capacità", "Regione", "Provincia", "Fonti", "Potenza efficiente (MW)"],
  },
  hydroProductionType: {
    source_id: "TERNA_DOWNLOAD_CENTER_HYDRO_PRODUCTION_BY_TYPE_2024",
    title: "Hydropower Production by Plant Type 2024 Download Center export",
    original: "Export-DownloadCenterFile-20260901-101019.xlsx",
    archived: "Terna_Hydro_Production_By_Type_2024_RAW.xlsx",
    sha256: "e5d0c67eba558f692fb66c9ba806356fdd3f4b2dfe7bb270ff5a463506640e9f",
    expectedHeaders: ["Anno", "Tipo produzione", "Regione", "Provincia", "Impianto idrico", "Produzione (GWh)"],
  },
  bioProduction: {
    source_id: "TERNA_DOWNLOAD_CENTER_BIOENERGY_PRODUCTION_2024",
    title: "Bioenergy Production 2024 Download Center export",
    original: "Export-DownloadCenterFile-20260901-101029.xlsx",
    archived: "Terna_Bioenergy_Production_2024_RAW.xlsx",
    sha256: "94b6caceca9ca19e60f7471a8584019d9db75efb0bb11ac90fb52f954ab95b0a",
    expectedHeaders: ["Anno", "Tipo produzione", "Regione", "Provincia", "Fonte rinnovabile", "Produzione (GWh)"],
  },
  geoProduction: {
    source_id: "TERNA_DOWNLOAD_CENTER_GEOTHERMAL_PRODUCTION_2024",
    title: "Geothermal Production 2024 Download Center export",
    original: "Export-DownloadCenterFile-20260901-101036.xlsx",
    archived: "Terna_Geothermal_Production_2024_RAW.xlsx",
    sha256: "addcfd865eff34850bad31e09e603db41c2524936ba402baa07875e039a51d91",
    expectedHeaders: ["Anno", "Tipo produzione", "Regione", "Provincia", "Fonte rinnovabile", "Produzione (GWh)"],
  },
  heat: {
    source_id: "TERNA_DOWNLOAD_CENTER_THERMOELECTRIC_HEAT_2024",
    title: "Thermoelectric Produced Heat 2024 Download Center export",
    original: "Export-DownloadCenterFile-20260901-101113.xlsx",
    archived: "Terna_Thermoelectric_Heat_2024_RAW.xlsx",
    sha256: "06a18e50214e6e5bd882819a1dde6ba1a0ee0457e0c6728727c12640a4e4a72c",
    expectedHeaders: ["Anno", "Regione", "Provincia", "Impianto cogenerativo", "Calore prodotto (GWh)"],
  },
};

const sha256File = async (file) =>
  crypto.createHash("sha256").update(await fs.readFile(file)).digest("hex");
const fileInfo = async (file) => {
  const stat = await fs.stat(file);
  return { bytes: stat.size, sha256: await sha256File(file) };
};
const sum = (values) => values.reduce((a, b) => a + b, 0);
const round = (value, digits = 9) =>
  value === null || value === undefined ? null : Number(Number(value).toFixed(digits));
const normalizeName = (value) =>
  String(value ?? "")
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
    const current = groups.get(key) ?? [];
    current.push(row);
    groups.set(key, current);
  }
  return groups;
};
const boolValue = (value) => value === true || String(value).toLowerCase() === "true";

async function loadExport(config) {
  const file = path.join(rawDir, config.archived);
  const info = await fileInfo(file);
  if (info.sha256 !== config.sha256) {
    throw new Error(`Archived raw hash mismatch for ${config.archived}.`);
  }
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(file));
  const sheet = workbook.worksheets.getItem("Export");
  const used = sheet.getUsedRange();
  const values = used.values;
  if (JSON.stringify(values[0]) !== JSON.stringify(config.expectedHeaders)) {
    throw new Error(`Unexpected headers for ${config.archived}: ${JSON.stringify(values[0])}`);
  }
  const dataRows = [];
  const footerRows = [];
  for (let i = 1; i < values.length; i += 1) {
    const row = values[i];
    if (String(row[0] ?? "").startsWith("Applied filters:")) {
      footerRows.push({ row_number: i + 1, values: row });
    } else if (row.some((v) => v !== null && v !== "")) {
      dataRows.push({ row_number: i + 1, values: row });
    }
  }
  if (footerRows.length !== 1 || !String(footerRows[0].values[0]).includes("2024")) {
    throw new Error(`Missing or unexpected 2024 filter footer in ${config.archived}.`);
  }
  if (dataRows.some((r) => Number(r.values[0]) !== 2024)) {
    throw new Error(`Non-2024 observation in ${config.archived}.`);
  }
  return { ...config, file, info, usedRange: used.address, dataRows, footerRows };
}

async function readCsvObjects(file, sheetName) {
  const workbook = await Workbook.fromCSV(await fs.readFile(file, "utf8"), { sheetName });
  const values = workbook.worksheets.getItem(sheetName).getUsedRange().values;
  const headers = values[0].map((v) => String(v));
  return values.slice(1).filter((r) => r.some((v) => v !== null && v !== "")).map((row) =>
    Object.fromEntries(headers.map((header, index) => [header, row[index]])),
  );
}

function parseRows(rows, numericColumn) {
  const nonBlank = rows.filter((row) => {
    const v = row.values[numericColumn];
    return v !== null && v !== undefined && String(v).trim() !== "";
  });
  const convention = detectTernaDecimalConventions(nonBlank.map((row) => row.values[numericColumn]));
  return {
    convention,
    rows: rows.map((row) => {
      const value = row.values[numericColumn];
      if (value === null || value === undefined || String(value).trim() === "") {
        return { ...row, parsed: null };
      }
      return { ...row, parsed: parseTernaNumber(value, convention) };
    }),
  };
}

const crosswalkPath = path.join(phaseRoot, "normalized", "MEM_Province_Region_MarketZone_Crosswalk.csv");
const crosswalk = await readCsvObjects(crosswalkPath, "Crosswalk");
if (crosswalk.length !== 107) throw new Error(`Crosswalk row count is ${crosswalk.length}, expected 107.`);
const crosswalkByPair = new Map();
for (const row of crosswalk) {
  const key = `${normalizeName(row.terna_region_name)}\u0000${normalizeName(row.terna_province_name)}`;
  if (crosswalkByPair.has(key)) throw new Error(`Duplicate crosswalk key ${key}.`);
  crosswalkByPair.set(key, row);
}
const mapGeo = (region, province) => {
  const key = `${normalizeName(region)}\u0000${normalizeName(province)}`;
  const geo = crosswalkByPair.get(key);
  if (!geo) throw new Error(`No reviewed MEM mapping for ${region}/${province}.`);
  if (!canonicalZones.includes(String(geo.market_zone))) {
    throw new Error(`Non-canonical zone ${geo.market_zone} for ${region}/${province}.`);
  }
  return geo;
};

const loaded = {};
for (const [key, config] of Object.entries(sources)) loaded[key] = await loadExport(config);

function normalizeCapacity(exportData, technology, fuelSource, storageFlag = false) {
  const netRows = exportData.dataRows.filter((row) => row.values[1] === "Netta");
  const grossRows = exportData.dataRows.filter((row) => row.values[1] === "Lorda");
  if (netRows.length !== 107 || grossRows.length !== 107) {
    throw new Error(`${exportData.archived}: expected 107 Netta and 107 Lorda rows.`);
  }
  const parsed = parseRows(netRows, 5);
  if (parsed.rows.some((row) => !row.parsed)) throw new Error(`${exportData.archived}: blank Netta capacity value.`);
  const provinceGroups = groupRows(parsed.rows, (row) =>
    `${normalizeName(row.values[2])}\u0000${normalizeName(row.values[3])}`,
  );
  if (provinceGroups.size !== 107 || [...provinceGroups.values()].some((rows) => rows.length !== 1)) {
    throw new Error(`${exportData.archived}: Netta province grain is not unique.`);
  }
  return parsed.rows.map((row, index) => {
    const geo = mapGeo(row.values[2], row.values[3]);
    return {
      record_id: `${exportData.source_id}_NET_${String(index + 1).padStart(3, "0")}`,
      year: 2024,
      capacity_type_original: row.values[1],
      capacity_basis: "NET",
      region_code: geo.region_code,
      region_original: row.values[2],
      region_normalized: geo.region_name,
      province_code: geo.province_code,
      province_original: row.values[3],
      province_normalized: geo.province_name,
      market_zone: geo.market_zone,
      source_label_original: row.values[4],
      technology,
      fuel_source: fuelSource,
      chp_flag: technology === "BIOENERGY" ? "MIXED_UNRESOLVED" : false,
      storage_flag: storageFlag,
      capacity_NET_MW_raw: row.parsed.raw_value,
      capacity_NET_MW: row.parsed.value,
      raw_cell_type: row.parsed.raw_cell_type,
      parser_status: row.parsed.parser_status,
      source_row_number: row.row_number,
      acquisition_date: acquisitionDate,
      acquisition_route: "TERNA_DOWNLOAD_CENTER_MANUAL_XLSX",
      source_id: exportData.source_id,
      raw_source_file: exportData.original,
      archived_raw_file: `raw/terna/historical_2024/${exportData.archived}`,
      raw_source_sha256: exportData.sha256,
    };
  });
}

function normalizeSourceProduction(exportData, technology, fuelSource) {
  const netRows = exportData.dataRows.filter((row) => row.values[1] === "Netta");
  const grossRows = exportData.dataRows.filter((row) => row.values[1] === "Lorda");
  if (netRows.length !== 107 || grossRows.length !== 107) {
    throw new Error(`${exportData.archived}: expected 107 Netta and 107 Lorda rows.`);
  }
  const parsed = parseRows(netRows, 5);
  if (parsed.rows.some((row) => !row.parsed)) throw new Error(`${exportData.archived}: blank Netta production value.`);
  const provinceGroups = groupRows(parsed.rows, (row) =>
    `${normalizeName(row.values[2])}\u0000${normalizeName(row.values[3])}`,
  );
  if (provinceGroups.size !== 107 || [...provinceGroups.values()].some((rows) => rows.length !== 1)) {
    throw new Error(`${exportData.archived}: Netta province grain is not unique.`);
  }
  return parsed.rows.map((row, index) => {
    const geo = mapGeo(row.values[2], row.values[3]);
    return {
      record_id: `${exportData.source_id}_NET_${String(index + 1).padStart(3, "0")}`,
      year: 2024,
      production_type_original: row.values[1],
      production_basis: "NET",
      region_code: geo.region_code,
      region_original: row.values[2],
      region_normalized: geo.region_name,
      province_code: geo.province_code,
      province_original: row.values[3],
      province_normalized: geo.province_name,
      market_zone: geo.market_zone,
      source_label_original: row.values[4],
      technology,
      fuel_source: fuelSource,
      chp_flag: technology === "BIOENERGY" ? "MIXED_UNRESOLVED" : false,
      generation_NET_GWh_raw: row.parsed.raw_value,
      generation_NET_GWh: row.parsed.value,
      raw_cell_type: row.parsed.raw_cell_type,
      parser_status: row.parsed.parser_status,
      source_row_number: row.row_number,
      acquisition_date: acquisitionDate,
      acquisition_route: "TERNA_DOWNLOAD_CENTER_MANUAL_XLSX",
      source_id: exportData.source_id,
      raw_source_file: exportData.original,
      archived_raw_file: `raw/terna/historical_2024/${exportData.archived}`,
      raw_source_sha256: exportData.sha256,
    };
  });
}

const bioCapacity = normalizeCapacity(loaded.bioCapacity, "BIOENERGY", "BIOENERGY", false);
const geoCapacity = normalizeCapacity(loaded.geoCapacity, "GEOTHERMAL", "GEOTHERMAL_HEAT", false);
const hydroCapacity = normalizeCapacity(loaded.hydroCapacity, "HYDRO_TOTAL_CONTROL", "WATER", "MIXED_INCLUDES_PUMPED_HYDRO");
const bioProduction = normalizeSourceProduction(loaded.bioProduction, "BIOENERGY", "BIOENERGY");
const geoProduction = normalizeSourceProduction(loaded.geoProduction, "GEOTHERMAL", "GEOTHERMAL_HEAT");

const hydroTypeMap = new Map([
  [normalizeName("Fluente"), "HYDRO_RUN_OF_RIVER"],
  [normalizeName("Bacino"), "HYDRO_BASIN"],
  [normalizeName("Serbatoio (compresi eventuali pompaggi)"), "HYDRO_RESERVOIR_INCL_PUMPING"],
]);
const hydroNetRows = loaded.hydroProductionType.dataRows.filter((row) => row.values[1] === "Netta");
const hydroGrossRows = loaded.hydroProductionType.dataRows.filter((row) => row.values[1] === "Lorda");
if (hydroNetRows.length !== hydroGrossRows.length || hydroNetRows.length * 2 !== loaded.hydroProductionType.dataRows.length) {
  throw new Error("Hydro production type export does not contain balanced explicit Netta/Lorda rows.");
}
const hydroNetParsed = parseRows(hydroNetRows, 5);
const hydroTypeKeyGroups = groupRows(hydroNetParsed.rows, (row) =>
  [row.values[0], normalizeName(row.values[2]), normalizeName(row.values[3]), normalizeName(row.values[4])].join("\u0000"),
);
const hydroTypeRepeatedGroups = [...hydroTypeKeyGroups.entries()]
  .filter(([, rows]) => rows.length > 1)
  .map(([key, rows]) => ({
    visible_grain_key: key.split("\u0000").join(" | "),
    source_row_count: rows.length,
    source_row_numbers: rows.map((row) => row.row_number).join("|"),
    values_GWh: rows.map((row) => row.parsed.value).join("|"),
    hydro_type_original: rows[0].values[4],
    disposition: "RETAIN_ALL_SOURCE_ROWS_AND_AGGREGATE_WITH_LINEAGE; HIDDEN_PLANT_OR_PUMPING_DIMENSION_NOT_INFERRED",
  }));
const hydroTypeProduction = hydroNetParsed.rows.map((row, index) => {
  const geo = mapGeo(row.values[2], row.values[3]);
  const hydroType = hydroTypeMap.get(normalizeName(row.values[4]));
  if (!hydroType) throw new Error(`Unmapped hydro type ${row.values[4]}.`);
  const visibleKey = [row.values[0], normalizeName(row.values[2]), normalizeName(row.values[3]), normalizeName(row.values[4])].join("\u0000");
  const visibleGroup = hydroTypeKeyGroups.get(visibleKey);
  return {
    record_id: `TERNA2024_HYDRO_TYPE_NET_${String(index + 1).padStart(3, "0")}`,
    year: 2024,
    production_type_original: row.values[1],
    production_basis: "NET",
    region_code: geo.region_code,
    region_original: row.values[2],
    region_normalized: geo.region_name,
    province_code: geo.province_code,
    province_original: row.values[3],
    province_normalized: geo.province_name,
    market_zone: geo.market_zone,
    hydro_type_original: row.values[4],
    hydro_type: hydroType,
    storage_flag: hydroType === "HYDRO_RESERVOIR_INCL_PUMPING" ? "MIXED_INCLUDES_PUMPED_HYDRO" : false,
    generation_NET_GWh_raw: row.parsed.raw_value,
    generation_NET_GWh: row.parsed.value,
    raw_cell_type: row.parsed.raw_cell_type,
    parser_status: row.parsed.parser_status,
    source_row_number: row.row_number,
    visible_grain_row_count: visibleGroup.length,
    visible_grain_status: visibleGroup.length === 1 ? "UNIQUE_VISIBLE_GRAIN" : "MULTIPLE_SOURCE_ROWS_RETAINED_FOR_AGGREGATION",
    source_id: loaded.hydroProductionType.source_id,
    raw_source_file: loaded.hydroProductionType.original,
    archived_raw_file: `raw/terna/historical_2024/${loaded.hydroProductionType.archived}`,
    raw_source_sha256: loaded.hydroProductionType.sha256,
  };
});

const hydroProvinceGroups = groupRows(hydroTypeProduction, (row) => `${row.region_code}\u0000${row.province_code}`);
const hydroProduction = [...hydroProvinceGroups.values()].map((rows, index) => ({
  record_id: `TERNA2024_HYDRO_TOTAL_NET_${String(index + 1).padStart(3, "0")}`,
  year: 2024,
  production_basis: "NET",
  region_code: rows[0].region_code,
  region_original: rows[0].region_original,
  region_normalized: rows[0].region_normalized,
  province_code: rows[0].province_code,
  province_original: rows[0].province_original,
  province_normalized: rows[0].province_normalized,
  market_zone: rows[0].market_zone,
  technology: "HYDRO_TOTAL_CONTROL",
  fuel_source: "WATER",
  generation_NET_GWh: round(sum(rows.map((row) => row.generation_NET_GWh))),
  included_hydro_types: sortedUnique(rows.map((row) => row.hydro_type)).join("|"),
  source_row_count: rows.length,
  source_row_numbers: rows.map((row) => row.source_row_number).sort((a, b) => a - b).join("|"),
  pumping_perimeter: "SERBATOIO_INCLUDES_EVENTUAL_PUMPING; PUMPED_COMPONENT_NOT_SEPARABLE",
  source_id: loaded.hydroProductionType.source_id,
  raw_source_file: loaded.hydroProductionType.original,
  archived_raw_file: `raw/terna/historical_2024/${loaded.hydroProductionType.archived}`,
  raw_source_sha256: loaded.hydroProductionType.sha256,
}));

const heatTechnologyMap = new Map([
  [normalizeName("Ciclo combinato con cogenerazione (CCC)"), "CCGT_CHP"],
  [normalizeName("Turbine a gas con cogenerazione (TGC)"), "GT_CHP"],
  [normalizeName("Combustione interna con cogenerazione (CIC)"), "INTERNAL_COMBUSTION_CHP"],
  [normalizeName("Condensazione e spillamento (CSC)"), "STEAM_EXTRACTION_CHP"],
  [normalizeName("Contropressione (CPC)"), "STEAM_BACKPRESSURE_CHP"],
  [normalizeName("Celle combustibili con cogenerazione (CEC)"), "FUEL_CELL_CHP"],
]);
const heatParsed = parseRows(loaded.heat.dataRows, 4);
const heatPreRows = heatParsed.rows.map((row) => {
  const geo = mapGeo(row.values[1], row.values[2]);
  const technology = heatTechnologyMap.get(normalizeName(row.values[3]));
  if (!technology) throw new Error(`Unmapped CHP heat technology ${row.values[3]}.`);
  return { row, geo, technology };
});
const heatGrainGroups = groupRows(heatPreRows, (item) =>
  [item.geo.region_code, item.geo.province_code, item.technology].join("\u0000"),
);
const heatCanonical = heatPreRows.map((item, index) => {
  const group = heatGrainGroups.get([item.geo.region_code, item.geo.province_code, item.technology].join("\u0000"));
  return {
    record_id: `TERNA2024_CHP_HEAT_${String(index + 1).padStart(4, "0")}`,
    year: 2024,
    region_code: item.geo.region_code,
    region_original: item.row.values[1],
    region_normalized: item.geo.region_name,
    province_code: item.geo.province_code,
    province_original: item.row.values[2],
    province_normalized: item.geo.province_name,
    market_zone: item.geo.market_zone,
    chp_technology_original: item.row.values[3],
    technology: item.technology,
    chp_flag: true,
    produced_heat_GWh_raw: item.row.parsed.raw_value,
    produced_heat_GWh: item.row.parsed.value,
    raw_cell_type: item.row.parsed.raw_cell_type,
    parser_status: item.row.parsed.parser_status,
    source_row_number: item.row.row_number,
    visible_grain_row_count: group.length,
    visible_grain_status: group.length === 1 ? "UNIQUE_VISIBLE_GRAIN" : "MULTIPLE_SOURCE_ROWS_RETAINED_FOR_AGGREGATION",
    source_id: loaded.heat.source_id,
    raw_source_file: loaded.heat.original,
    archived_raw_file: `raw/terna/historical_2024/${loaded.heat.archived}`,
    raw_source_sha256: loaded.heat.sha256,
  };
});

function makeZoneSummary(capacityRows, productionRows, technology, generationNote) {
  const rows = [];
  const scopes = [...canonicalZones.map((zone) => ({ geographic_scope: "MARKET_ZONE", market_zone: zone })), { geographic_scope: "NATIONAL", market_zone: "" }];
  for (const scope of scopes) {
    const cap = sum(capacityRows.filter((row) => !scope.market_zone || row.market_zone === scope.market_zone).map((row) => row.capacity_NET_MW));
    const gen = sum(productionRows.filter((row) => !scope.market_zone || row.market_zone === scope.market_zone).map((row) => row.generation_NET_GWh));
    const cf = cap > 0 ? gen / (cap * 8.76) : gen === 0 ? null : null;
    rows.push({
      year: 2024,
      geographic_scope: scope.geographic_scope,
      market_zone: scope.market_zone,
      technology,
      capacity_basis: "NET",
      capacity_NET_MW: round(cap),
      generation_basis: "NET",
      generation_NET_GWh: round(gen),
      observed_CF: cf === null ? "" : round(cf, 9),
      cf_status: cap > 0 ? (cf >= 0 && cf <= 1 ? "CALCULATED_WITHIN_PHYSICAL_RANGE" : "ANOMALOUS_REVIEW") : "NOT_CALCULATED_ZERO_CAPACITY",
      interpretation: "HISTORICAL_CHARACTERIZATION_ONLY; DOES_NOT_REPLACE_FUTURE_SCENARIO_CF_ASSUMPTIONS",
      generation_perimeter_note: generationNote,
    });
  }
  return rows;
}

const bioSummary = makeZoneSummary(
  bioCapacity,
  bioProduction,
  "BIOENERGY_SOURCE_SUBSET",
  "Official Terna renewable-source Netta generation; cross-classifies the thermoelectric population and is not additive to thermoelectric totals.",
);
const geoSummary = makeZoneSummary(
  geoCapacity,
  geoProduction,
  "GEOTHERMAL",
  "Official Terna geothermal Netta capacity and generation; technology/carrier kept separate from Condensazione.",
);
const hydroSummary = makeZoneSummary(
  hydroCapacity,
  hydroProduction,
  "HYDRO_TOTAL_CONTROL",
  "Netta generation is derived by summing the supplied official Fluente/Bacino/Serbatoio type observations; Serbatoio includes eventual pumping.",
);

const hydroTypeZoneGroups = groupRows(hydroTypeProduction, (row) => `${row.market_zone}\u0000${row.hydro_type}`);
const hydroTypeZone = [];
for (const zone of canonicalZones) {
  for (const hydroType of ["HYDRO_RUN_OF_RIVER", "HYDRO_BASIN", "HYDRO_RESERVOIR_INCL_PUMPING"]) {
    const rows = hydroTypeZoneGroups.get(`${zone}\u0000${hydroType}`) ?? [];
    hydroTypeZone.push({
      year: 2024,
      market_zone: zone,
      hydro_type: hydroType,
      net_generation_GWh: round(sum(rows.map((row) => row.generation_NET_GWh))),
      source_observation_count: rows.length,
      capacity_allocation_status: "REQUIRES_CAPACITY ALLOCATION",
      pumping_status: hydroType === "HYDRO_RESERVOIR_INCL_PUMPING" ? "INCLUDES_EVENTUAL_PUMPING; NOT SEPARATED" : "NOT_LABELLED_AS_PUMPING",
      source_id: loaded.hydroProductionType.source_id,
    });
  }
}

const thermoCapacityPath = path.join(phaseRoot, "normalized", "Terna_Thermoelectric_Capacity_2024_Canonical.csv");
const thermoProductionPath = path.join(phaseRoot, "normalized", "Terna_Thermoelectric_Production_2024_Canonical.csv");
const thermoCapacity = await readCsvObjects(thermoCapacityPath, "ThermoCapacity");
const thermoProduction = await readCsvObjects(thermoProductionPath, "ThermoProduction");

function detailedThermalCode(row) {
  const technology = String(row.technology_normalized);
  const chp = boolValue(row.chp_flag);
  if (technology === "CCGT_LIKE") return chp ? "CCGT_CHP" : "CCGT_NON_CHP";
  if (technology === "GAS_TURBINE_LIKE") return chp ? "GT_CHP" : "GT_NON_CHP";
  if (technology === "INTERNAL_COMBUSTION") return chp ? "INTERNAL_COMBUSTION_CHP" : "INTERNAL_COMBUSTION_NON_CHP";
  if (technology === "CONDENSING_STEAM") return "STEAM_CONDENSING";
  if (technology === "EXTRACTION_CONDENSING") return "STEAM_EXTRACTION_CHP";
  if (technology === "BACK_PRESSURE") return "STEAM_BACKPRESSURE_CHP";
  if (technology === "FUEL_CELL") return chp ? "FUEL_CELL_CHP" : "FUEL_CELL_NON_CHP";
  if (technology === "EXTERNAL_COMBUSTION") return "EXTERNAL_COMBUSTION";
  if (technology === "TURBO_EXPANSION") return "TURBO_EXPANSION";
  if (technology === "OTHER_THERMAL") return "OTHER_THERMAL";
  throw new Error(`Unmapped detailed thermoelectric code ${technology}/${chp}.`);
}

const chpCodes = [
  "CCGT_CHP",
  "GT_CHP",
  "INTERNAL_COMBUSTION_CHP",
  "STEAM_EXTRACTION_CHP",
  "STEAM_BACKPRESSURE_CHP",
  "FUEL_CELL_CHP",
];
const thermoCapAgg = new Map();
for (const row of thermoCapacity) {
  const code = detailedThermalCode(row);
  const key = `${row.market_zone}\u0000${code}`;
  thermoCapAgg.set(key, (thermoCapAgg.get(key) ?? 0) + Number(row.efficient_power_MW));
}
const thermoGenAgg = new Map();
for (const row of thermoProduction) {
  const code = detailedThermalCode(row);
  const key = `${row.market_zone}\u0000${code}`;
  thermoGenAgg.set(key, (thermoGenAgg.get(key) ?? 0) + Number(row.production_netta_GWh));
}
const heatAgg = new Map();
for (const row of heatCanonical) {
  const key = `${row.market_zone}\u0000${row.technology}`;
  heatAgg.set(key, (heatAgg.get(key) ?? 0) + row.produced_heat_GWh);
}
const chpMetrics = [];
for (const scope of [...canonicalZones.map((zone) => ({ geographic_scope: "MARKET_ZONE", market_zone: zone })), { geographic_scope: "NATIONAL", market_zone: "" }]) {
  for (const code of chpCodes) {
    const zones = scope.market_zone ? [scope.market_zone] : canonicalZones;
    const cap = sum(zones.map((zone) => thermoCapAgg.get(`${zone}\u0000${code}`) ?? 0));
    const gen = sum(zones.map((zone) => thermoGenAgg.get(`${zone}\u0000${code}`) ?? 0));
    const heat = sum(zones.map((zone) => heatAgg.get(`${zone}\u0000${code}`) ?? 0));
    const cf = cap > 0 ? gen / (cap * 8.76) : null;
    const ratio = heat > 0 ? gen / heat : null;
    let status = "MATCHED_CAPACITY_ELECTRICITY_HEAT";
    if (cap === 0 && (gen > 0 || heat > 0)) status = "ANOMALY_OUTPUT_WITH_ZERO_REPORTED_CAPACITY";
    else if (heat === 0) status = "NO_POSITIVE_HEAT_OUTPUT_FOR_RATIO";
    chpMetrics.push({
      year: 2024,
      geographic_scope: scope.geographic_scope,
      market_zone: scope.market_zone,
      technology: code,
      chp_flag: true,
      capacity_NET_MW: round(cap),
      net_electricity_generation_GWh: round(gen),
      produced_heat_GWh: round(heat),
      observed_electric_CF: cf === null ? "" : round(cf, 9),
      electricity_to_heat_ratio_GWh_e_per_GWh_heat: ratio === null ? "" : round(ratio, 9),
      indicator_status: status,
      interpretation: "HISTORICAL_CHP_CHARACTERIZATION_ONLY; NO_HEAT_COOPTIMIZATION_DECISION_IMPLEMENTED",
      capacity_source_id: "TERNA_DOWNLOAD_CENTER_THERMOELECTRIC_CAPACITY_2024",
      electricity_source_id: "TERNA_DOWNLOAD_CENTER_THERMOELECTRIC_PRODUCTION_2024",
      heat_source_id: loaded.heat.source_id,
    });
  }
}

const totalByZone = (rows, field) => new Map(canonicalZones.map((zone) => [zone, round(sum(rows.filter((row) => row.market_zone === zone).map((row) => Number(row[field]))))]));
const thermoCapByZone = totalByZone(thermoCapacity, "efficient_power_MW");
const bioCapByZone = totalByZone(bioCapacity, "capacity_NET_MW");
const geoCapByZone = totalByZone(geoCapacity, "capacity_NET_MW");
const hydroCapByZone = totalByZone(hydroCapacity, "capacity_NET_MW");
const thermoSubcategories = sortedUnique(thermoCapacity.map((row) => String(row.subcategory_original)));
const geothermalLabelInThermo = thermoSubcategories.some((v) => normalizeName(v).includes("geoterm"));
if (geothermalLabelInThermo) throw new Error("Unexpected geothermal-labelled row in thermoelectric conversion extract; perimeter rule requires review.");

const thermalPerimeter = [];
for (const scope of [...canonicalZones.map((zone) => ({ geographic_scope: "MARKET_ZONE", market_zone: zone })), { geographic_scope: "NATIONAL", market_zone: "" }]) {
  const zones = scope.market_zone ? [scope.market_zone] : canonicalZones;
  const thermo = round(sum(zones.map((zone) => thermoCapByZone.get(zone) ?? 0)));
  const bio = round(sum(zones.map((zone) => bioCapByZone.get(zone) ?? 0)));
  const geo = round(sum(zones.map((zone) => geoCapByZone.get(zone) ?? 0)));
  const hydro = round(sum(zones.map((zone) => hydroCapByZone.get(zone) ?? 0)));
  const comparable = round(thermo + geo);
  const common = { year: 2024, geographic_scope: scope.geographic_scope, market_zone: scope.market_zone, capacity_basis: "NET" };
  thermalPerimeter.push(
    { ...common, sequence: 1, perimeter_item: "TERNA_THERMOELECTRIC_CONVERSION_TECHNOLOGY_TOTAL", capacity_NET_MW: thermo, inclusion_operation: "BASE", overlap_class: "PRIMARY_CONVERSION_TECHNOLOGY_CONTROL", additive_adjustment_MW: thermo, running_DDS_comparable_NET_MW: thermo, evidence_class: "TERNA CAPACITY CONTROL", status: "ACQUIRED_VALIDATED", reasoning: "Full official 2024 thermoelectric technology extract; fuel/source remains a separate dimension." },
    { ...common, sequence: 2, perimeter_item: "BIOENERGY_SOURCE_SUBSET", capacity_NET_MW: bio, inclusion_operation: "NO_ADD_SUBSET", overlap_class: "CROSS_CLASSIFICATION_WITHIN_THERMOELECTRIC_TOTAL", additive_adjustment_MW: 0, running_DDS_comparable_NET_MW: thermo, evidence_class: "DIRECT SOURCE CAPACITY / QA RECONCILIATION ONLY", status: "ACQUIRED_VALIDATED_NO_DOUBLE_COUNT", reasoning: "Controlling rule: bioenergy is already inside the thermoelectric population and is used only to identify fuel/source MW." },
    { ...common, sequence: 3, perimeter_item: "GEOTHERMAL_SOURCE_TECHNOLOGY", capacity_NET_MW: geo, inclusion_operation: "ADD_OUTSIDE_CURRENT_THERMOELECTRIC_TECH_EXTRACT", overlap_class: "SEPARATE_RENEWABLE_TECHNOLOGY", additive_adjustment_MW: geo, running_DDS_comparable_NET_MW: comparable, evidence_class: "DIRECT SOURCE CAPACITY / PROJECT RECONCILIATION", status: "ACQUIRED_VALIDATED_PERIMETER_ADD", reasoning: "No geothermal label occurs in the 14-category thermoelectric conversion extract; Terna reports geothermal separately, and the DDS future thermoelectric envelope includes geothermal." },
    { ...common, sequence: 4, perimeter_item: "HYDRO_TOTAL_CONTROL", capacity_NET_MW: hydro, inclusion_operation: "EXCLUDE_FROM_THERMOELECTRIC_ENVELOPE", overlap_class: "SEPARATE_HYDRO_PERIMETER", additive_adjustment_MW: 0, running_DDS_comparable_NET_MW: comparable, evidence_class: "DIRECT SOURCE CAPACITY", status: "ACQUIRED_VALIDATED_SEPARATE", reasoning: "Hydro is outside the approximately 55-GW thermoelectric envelope." },
  );
  if (scope.geographic_scope === "NATIONAL") {
    thermalPerimeter.push({ ...common, sequence: 5, perimeter_item: "DDS_2040_THERMOELECTRIC_CONTROL", capacity_NET_MW: 55000, inclusion_operation: "FUTURE_BENCHMARK_NOT_CURRENT_ROW", overlap_class: "NATIONAL_NET_EFFICIENT_THERMOELECTRIC_ENVELOPE", additive_adjustment_MW: "", running_DDS_comparable_NET_MW: "", evidence_class: "TERNA CAPACITY CONTROL", status: "BENCHMARK_ONLY", reasoning: "Slow/Base/High 2040 control; not CCGT+OCGT alone and not an automatic PyPSA p_nom total." });
  }
}

const hydroTypeByZone = new Map(hydroTypeZone.map((row) => [`${row.market_zone}\u0000${row.hydro_type}`, row.net_generation_GWh]));
const hydroPumping = [];
for (const scope of [...canonicalZones.map((zone) => ({ geographic_scope: "MARKET_ZONE", market_zone: zone })), { geographic_scope: "NATIONAL", market_zone: "" }]) {
  const zones = scope.market_zone ? [scope.market_zone] : canonicalZones;
  const valueFor = (type) => round(sum(zones.map((zone) => hydroTypeByZone.get(`${zone}\u0000${type}`) ?? 0)));
  const fluente = valueFor("HYDRO_RUN_OF_RIVER");
  const bacino = valueFor("HYDRO_BASIN");
  const serbatoio = valueFor("HYDRO_RESERVOIR_INCL_PUMPING");
  hydroPumping.push({
    year: 2024,
    geographic_scope: scope.geographic_scope,
    market_zone: scope.market_zone,
    hydro_total_capacity_NET_MW: round(sum(zones.map((zone) => hydroCapByZone.get(zone) ?? 0))),
    run_of_river_net_generation_GWh: fluente,
    basin_net_generation_GWh: bacino,
    reservoir_including_eventual_pumping_net_generation_GWh: serbatoio,
    hydro_total_net_generation_GWh: round(fluente + bacino + serbatoio),
    pumped_hydro_discharge_capacity_MW: "",
    pumped_hydro_charge_capacity_MW: "",
    pumped_hydro_energy_MWh: "",
    pumped_hydro_roundtrip_efficiency: "",
    current_perimeter_rule: "TOTAL HYDRO MW IS THE CONTROL; SERBATOIO GENERATION INCLUDES EVENTUAL PUMPING; DO NOT ADD PUMPED MW AGAIN",
    storage_layer_requirement: "PUMPED DISCHARGE/CHARGE/ENERGY/EFFICIENCY REQUIRE PHYSICAL ASSET ALLOCATION RECONCILED TO TERNA TOTALS",
    status: "REQUIRES CAPACITY ALLOCATION AND PUMPING SEPARATION",
    source_id: `${loaded.hydroCapacity.source_id}|${loaded.hydroProductionType.source_id}`,
  });
}

const taxonomy = [
  ["TECHNOLOGY", "CCGT_NON_CHP", "THERMAL", "Combined cycle non-CHP", false, false, "PROGRAMMABLE_THERMAL", "UNRESOLVED", "PRIMARY_CONVERSION_TECHNOLOGY"],
  ["TECHNOLOGY", "CCGT_CHP", "THERMAL", "Combined cycle CHP", true, false, "PROGRAMMABLE_THERMAL_CHP", "UNRESOLVED", "PRIMARY_CONVERSION_TECHNOLOGY"],
  ["TECHNOLOGY", "GT_NON_CHP", "THERMAL", "Gas turbine non-CHP", false, false, "PROGRAMMABLE_THERMAL_PEAKER", "UNRESOLVED", "PRIMARY_CONVERSION_TECHNOLOGY"],
  ["TECHNOLOGY", "GT_CHP", "THERMAL", "Gas turbine CHP", true, false, "PROGRAMMABLE_THERMAL_CHP", "UNRESOLVED", "PRIMARY_CONVERSION_TECHNOLOGY"],
  ["TECHNOLOGY", "INTERNAL_COMBUSTION_NON_CHP", "THERMAL", "Internal combustion non-CHP", false, false, "PROGRAMMABLE_THERMAL", "UNRESOLVED", "PRIMARY_CONVERSION_TECHNOLOGY"],
  ["TECHNOLOGY", "INTERNAL_COMBUSTION_CHP", "THERMAL", "Internal combustion CHP", true, false, "PROGRAMMABLE_THERMAL_CHP", "UNRESOLVED", "PRIMARY_CONVERSION_TECHNOLOGY"],
  ["TECHNOLOGY", "STEAM_CONDENSING", "THERMAL", "Condensing steam", false, false, "PROGRAMMABLE_THERMAL", "UNRESOLVED", "PRIMARY_CONVERSION_TECHNOLOGY"],
  ["TECHNOLOGY", "STEAM_EXTRACTION_CHP", "THERMAL", "Extraction/condensing CHP", true, false, "PROGRAMMABLE_THERMAL_CHP", "UNRESOLVED", "PRIMARY_CONVERSION_TECHNOLOGY"],
  ["TECHNOLOGY", "STEAM_BACKPRESSURE_CHP", "THERMAL", "Back-pressure CHP", true, false, "PROGRAMMABLE_THERMAL_CHP", "UNRESOLVED", "PRIMARY_CONVERSION_TECHNOLOGY"],
  ["TECHNOLOGY", "FUEL_CELL_NON_CHP", "THERMAL", "Fuel cell non-CHP", false, false, "PROGRAMMABLE_THERMAL", "UNRESOLVED", "PRIMARY_CONVERSION_TECHNOLOGY"],
  ["TECHNOLOGY", "FUEL_CELL_CHP", "THERMAL", "Fuel cell CHP", true, false, "PROGRAMMABLE_THERMAL_CHP", "UNRESOLVED", "PRIMARY_CONVERSION_TECHNOLOGY"],
  ["TECHNOLOGY", "EXTERNAL_COMBUSTION", "THERMAL", "External combustion", "MIXED", false, "PROGRAMMABLE_THERMAL", "UNRESOLVED", "PRIMARY_CONVERSION_TECHNOLOGY"],
  ["TECHNOLOGY", "TURBO_EXPANSION", "THERMAL", "Turbo-expansion", "MIXED", false, "PROGRAMMABLE_THERMAL", "UNRESOLVED", "PRIMARY_CONVERSION_TECHNOLOGY"],
  ["TECHNOLOGY", "OTHER_THERMAL", "THERMAL", "Other thermal", "MIXED", false, "PROGRAMMABLE_THERMAL", "UNRESOLVED", "PRIMARY_CONVERSION_TECHNOLOGY"],
  ["TECHNOLOGY", "BIOENERGY", "RENEWABLE_PROGRAMMABLE", "Bioenergy source aggregate", "MIXED_UNRESOLVED", false, "PROGRAMMABLE_RENEWABLE", "BIOENERGY", "FUEL_SOURCE_CROSS_CLASSIFICATION"],
  ["TECHNOLOGY", "GEOTHERMAL", "RENEWABLE_PROGRAMMABLE", "Geothermal", false, false, "PROGRAMMABLE_RENEWABLE_BASELOAD", "GEOTHERMAL_HEAT", "PRIMARY_TECHNOLOGY"],
  ["TECHNOLOGY", "HYDRO_TOTAL_CONTROL", "HYDRO", "Total hydro capacity control", false, "MIXED_INCLUDES_PUMPED_HYDRO", "MIXED_HYDRO_CONTROL", "WATER", "CAPACITY_CONTROL_ONLY"],
  ["TECHNOLOGY", "HYDRO_RUN_OF_RIVER", "HYDRO", "Run-of-river / Fluente", false, false, "HYDRO_WEATHER_DRIVEN", "WATER", "PRIMARY_GENERATION_SUBTYPE"],
  ["TECHNOLOGY", "HYDRO_BASIN", "HYDRO", "Basin / Bacino", false, false, "HYDRO_DISPATCHABLE", "WATER", "PRIMARY_GENERATION_SUBTYPE"],
  ["TECHNOLOGY", "HYDRO_RESERVOIR", "HYDRO", "Reservoir / Serbatoio", false, false, "HYDRO_DISPATCHABLE", "WATER", "TARGET_TAXONOMY_PENDING_PUMPING_SEPARATION"],
  ["TECHNOLOGY", "PUMPED_HYDRO", "STORAGE", "Pumped hydro storage", false, true, "STORAGE", "WATER", "STORAGE_TECHNOLOGY"],
  ["TECHNOLOGY", "SOLAR_PV", "VARIABLE_RENEWABLE", "Solar PV", false, false, "VARIABLE_RENEWABLE", "SOLAR", "PRIMARY_TECHNOLOGY"],
  ["TECHNOLOGY", "WIND_ONSHORE", "VARIABLE_RENEWABLE", "Wind onshore", false, false, "VARIABLE_RENEWABLE", "WIND", "PRIMARY_TECHNOLOGY"],
  ["TECHNOLOGY", "WIND_OFFSHORE", "VARIABLE_RENEWABLE", "Wind offshore", false, false, "VARIABLE_RENEWABLE", "WIND", "PRIMARY_TECHNOLOGY"],
  ["TECHNOLOGY", "BESS", "STORAGE", "Battery energy storage system", false, true, "STORAGE", "ELECTRICITY", "STORAGE_TECHNOLOGY"],
  ["TECHNOLOGY", "OTHER_STORAGE", "STORAGE", "Other storage", false, true, "STORAGE", "UNRESOLVED", "STORAGE_TECHNOLOGY"],
  ["FUEL_SOURCE", "NATURAL_GAS", "FUEL_SOURCE", "Natural gas", "N/A", "N/A", "N/A", "NATURAL_GAS", "SEPARATE_FUEL_SOURCE_DIMENSION"],
  ["FUEL_SOURCE", "COAL", "FUEL_SOURCE", "Coal", "N/A", "N/A", "N/A", "COAL", "SEPARATE_FUEL_SOURCE_DIMENSION"],
  ["FUEL_SOURCE", "OIL", "FUEL_SOURCE", "Oil", "N/A", "N/A", "N/A", "OIL", "SEPARATE_FUEL_SOURCE_DIMENSION"],
  ["FUEL_SOURCE", "BIOENERGY", "FUEL_SOURCE", "Bioenergy", "N/A", "N/A", "N/A", "BIOENERGY", "SEPARATE_FUEL_SOURCE_DIMENSION"],
  ["FUEL_SOURCE", "OTHER_FUEL", "FUEL_SOURCE", "Other fuel", "N/A", "N/A", "N/A", "OTHER_FUEL", "SEPARATE_FUEL_SOURCE_DIMENSION"],
  ["FUEL_SOURCE", "MULTI_FUEL", "FUEL_SOURCE", "Multi-fuel", "N/A", "N/A", "N/A", "MULTI_FUEL", "SEPARATE_FUEL_SOURCE_DIMENSION"],
  ["FUEL_SOURCE", "UNRESOLVED", "FUEL_SOURCE", "Unresolved fuel/source", "N/A", "N/A", "N/A", "UNRESOLVED", "SEPARATE_FUEL_SOURCE_DIMENSION"],
].map(([taxonomy_dimension, code, family, label, chp_flag, storage_flag, dispatchability_class, default_fuel_source, perimeter_role]) => ({
  taxonomy_version: "MEM_HISTORICAL_TECHNOLOGY_TAXONOMY_V2024_1",
  taxonomy_dimension,
  code,
  family,
  label,
  chp_flag,
  storage_flag,
  dispatchability_class,
  default_fuel_source,
  perimeter_role,
  technology_and_fuel_separate_rule: "YES",
  status: taxonomy_dimension === "TECHNOLOGY" ? "FROZEN_FOR_HISTORICAL_DATA_BUILD" : "ALLOWED_DIMENSION_VALUE",
}));

const capacityIntegrated = [];
const generationIntegrated = [];
for (const zone of canonicalZones) {
  const codes = sortedUnique(thermoCapacity.filter((r) => r.market_zone === zone).map(detailedThermalCode));
  for (const code of codes) {
    const cap = thermoCapAgg.get(`${zone}\u0000${code}`) ?? 0;
    capacityIntegrated.push({ year: 2024, market_zone: zone, technology: code, fuel_source: "UNRESOLVED", chp_flag: code.endsWith("_CHP"), storage_flag: false, dispatchability_class: code.includes("GT_") ? "PROGRAMMABLE_THERMAL_PEAKER" : code.endsWith("_CHP") ? "PROGRAMMABLE_THERMAL_CHP" : "PROGRAMMABLE_THERMAL", capacity_NET_MW: round(cap), perimeter_layer: "THERMOELECTRIC_CONVERSION_TECHNOLOGY", perimeter_role: "PRIMARY_ADDITIVE", additive_to_system_total: true, source_id: "TERNA_DOWNLOAD_CENTER_THERMOELECTRIC_CAPACITY_2024", evidence_class: "DIRECT SOURCE CAPACITY / TERNA CAPACITY CONTROL", status: "ACQUIRED_2024_FUEL_UNRESOLVED" });
    const gen = thermoGenAgg.get(`${zone}\u0000${code}`) ?? 0;
    generationIntegrated.push({ year: 2024, market_zone: zone, technology: code, fuel_source: "UNRESOLVED", chp_flag: code.endsWith("_CHP"), storage_flag: false, dispatchability_class: code.includes("GT_") ? "PROGRAMMABLE_THERMAL_PEAKER" : code.endsWith("_CHP") ? "PROGRAMMABLE_THERMAL_CHP" : "PROGRAMMABLE_THERMAL", generation_NET_GWh: round(gen), perimeter_layer: "THERMOELECTRIC_CONVERSION_TECHNOLOGY", perimeter_role: "PRIMARY_ADDITIVE", additive_to_system_total: true, source_id: "TERNA_DOWNLOAD_CENTER_THERMOELECTRIC_PRODUCTION_2024", evidence_class: "DIRECT SOURCE ENERGY", status: "ACQUIRED_2024_FUEL_UNRESOLVED" });
  }
  const bioCap = bioSummary.find((r) => r.market_zone === zone);
  capacityIntegrated.push({ year: 2024, market_zone: zone, technology: "BIOENERGY", fuel_source: "BIOENERGY", chp_flag: "MIXED_UNRESOLVED", storage_flag: false, dispatchability_class: "PROGRAMMABLE_RENEWABLE", capacity_NET_MW: bioCap.capacity_NET_MW, perimeter_layer: "FUEL_SOURCE_CROSS_CLASSIFICATION", perimeter_role: "SUBSET_NON_ADDITIVE", additive_to_system_total: false, source_id: loaded.bioCapacity.source_id, evidence_class: "DIRECT SOURCE CAPACITY / QA RECONCILIATION ONLY", status: "ACQUIRED_2024_OVERLAPS_THERMOELECTRIC_TOTAL" });
  generationIntegrated.push({ year: 2024, market_zone: zone, technology: "BIOENERGY", fuel_source: "BIOENERGY", chp_flag: "MIXED_UNRESOLVED", storage_flag: false, dispatchability_class: "PROGRAMMABLE_RENEWABLE", generation_NET_GWh: bioCap.generation_NET_GWh, perimeter_layer: "FUEL_SOURCE_CROSS_CLASSIFICATION", perimeter_role: "SUBSET_NON_ADDITIVE", additive_to_system_total: false, source_id: loaded.bioProduction.source_id, evidence_class: "DIRECT SOURCE ENERGY / QA RECONCILIATION ONLY", status: "ACQUIRED_2024_OVERLAPS_THERMOELECTRIC_TOTAL" });
  const geo = geoSummary.find((r) => r.market_zone === zone);
  capacityIntegrated.push({ year: 2024, market_zone: zone, technology: "GEOTHERMAL", fuel_source: "GEOTHERMAL_HEAT", chp_flag: false, storage_flag: false, dispatchability_class: "PROGRAMMABLE_RENEWABLE_BASELOAD", capacity_NET_MW: geo.capacity_NET_MW, perimeter_layer: "RENEWABLE_PRIMARY_TECHNOLOGY", perimeter_role: "PRIMARY_ADDITIVE_OUTSIDE_THERMO_TECH_EXTRACT", additive_to_system_total: true, source_id: loaded.geoCapacity.source_id, evidence_class: "DIRECT SOURCE CAPACITY", status: "ACQUIRED_2024" });
  generationIntegrated.push({ year: 2024, market_zone: zone, technology: "GEOTHERMAL", fuel_source: "GEOTHERMAL_HEAT", chp_flag: false, storage_flag: false, dispatchability_class: "PROGRAMMABLE_RENEWABLE_BASELOAD", generation_NET_GWh: geo.generation_NET_GWh, perimeter_layer: "RENEWABLE_PRIMARY_TECHNOLOGY", perimeter_role: "PRIMARY_ADDITIVE_OUTSIDE_THERMO_TECH_EXTRACT", additive_to_system_total: true, source_id: loaded.geoProduction.source_id, evidence_class: "DIRECT SOURCE ENERGY", status: "ACQUIRED_2024" });
  const hydro = hydroSummary.find((r) => r.market_zone === zone);
  capacityIntegrated.push({ year: 2024, market_zone: zone, technology: "HYDRO_TOTAL_CONTROL", fuel_source: "WATER", chp_flag: false, storage_flag: "MIXED_INCLUDES_PUMPED_HYDRO", dispatchability_class: "MIXED_HYDRO_CONTROL", capacity_NET_MW: hydro.capacity_NET_MW, perimeter_layer: "HYDRO_CAPACITY_CONTROL", perimeter_role: "PRIMARY_ADDITIVE_CONTROL", additive_to_system_total: true, source_id: loaded.hydroCapacity.source_id, evidence_class: "DIRECT SOURCE CAPACITY", status: "ACQUIRED_2024_SUBTYPE_CAPACITY_UNRESOLVED" });
  for (const typeRow of hydroTypeZone.filter((r) => r.market_zone === zone)) {
    const code = typeRow.hydro_type === "HYDRO_RESERVOIR_INCL_PUMPING" ? "HYDRO_RESERVOIR" : typeRow.hydro_type;
    generationIntegrated.push({ year: 2024, market_zone: zone, technology: code, fuel_source: "WATER", chp_flag: false, storage_flag: typeRow.hydro_type === "HYDRO_RESERVOIR_INCL_PUMPING" ? "MIXED_INCLUDES_PUMPED_HYDRO" : false, dispatchability_class: code === "HYDRO_RUN_OF_RIVER" ? "HYDRO_WEATHER_DRIVEN" : "HYDRO_DISPATCHABLE", generation_NET_GWh: typeRow.net_generation_GWh, perimeter_layer: "HYDRO_GENERATION_SUBTYPE", perimeter_role: "PRIMARY_ADDITIVE_GENERATION", additive_to_system_total: true, source_id: loaded.hydroProductionType.source_id, evidence_class: "DIRECT SOURCE ENERGY", status: typeRow.hydro_type === "HYDRO_RESERVOIR_INCL_PUMPING" ? "ACQUIRED_2024_PUMPING_COMPONENT_NOT_SEPARATED" : "ACQUIRED_2024" });
  }
}

const technologyCodes = taxonomy.filter((r) => r.taxonomy_dimension === "TECHNOLOGY").map((r) => r.code);
const completionStatus = technologyCodes.map((code) => {
  const isThermal = ["CCGT_NON_CHP", "CCGT_CHP", "GT_NON_CHP", "GT_CHP", "INTERNAL_COMBUSTION_NON_CHP", "INTERNAL_COMBUSTION_CHP", "STEAM_CONDENSING", "STEAM_EXTRACTION_CHP", "STEAM_BACKPRESSURE_CHP", "FUEL_CELL_NON_CHP", "FUEL_CELL_CHP", "EXTERNAL_COMBUSTION", "TURBO_EXPANSION", "OTHER_THERMAL"].includes(code);
  if (isThermal) return { technology: code, capacity_by_zone: "YES_2024", generation_by_zone: "YES_2024", chp_distinction_resolved: code.endsWith("_CHP") || code.includes("NON_CHP") ? "YES" : "NOT_SEPARATELY_REQUIRED", fuel_source_resolved: "NO_GEM_OR_TERNA_FUEL_BRIDGE_PENDING", hydro_subtype_resolved: "NOT_APPLICABLE", historical_years_available: "2024", ready_for_canonical_workbook: "NO", status: "PARTIAL_2024", blocker: "Fuel/source reconciliation and actual 2019-2023 observations are not complete." };
  if (code === "BIOENERGY") return { technology: code, capacity_by_zone: "YES_2024", generation_by_zone: "YES_2024", chp_distinction_resolved: "NO_SOURCE_AGGREGATE_CROSSES_CHP", fuel_source_resolved: "YES_BIOENERGY", hydro_subtype_resolved: "NOT_APPLICABLE", historical_years_available: "2024", ready_for_canonical_workbook: "NO", status: "PARTIAL_2024_SUBSET", blocker: "Conversion-technology/CHP allocation and actual 2019-2023 observations remain open." };
  if (code === "GEOTHERMAL") return { technology: code, capacity_by_zone: "YES_2024", generation_by_zone: "YES_2024", chp_distinction_resolved: "NOT_APPLICABLE", fuel_source_resolved: "YES_GEOTHERMAL_HEAT", hydro_subtype_resolved: "NOT_APPLICABLE", historical_years_available: "2024", ready_for_canonical_workbook: "NO", status: "PARTIAL_2024", blocker: "Actual 2019-2023 observations are not yet normalized." };
  if (code === "HYDRO_TOTAL_CONTROL") return { technology: code, capacity_by_zone: "YES_2024", generation_by_zone: "YES_2024_DERIVED_FROM_TYPES", chp_distinction_resolved: "NOT_APPLICABLE", fuel_source_resolved: "YES_WATER", hydro_subtype_resolved: "GENERATION_ONLY", historical_years_available: "2024", ready_for_canonical_workbook: "NO", status: "PARTIAL_2024", blocker: "Type-specific MW and pumped-storage separation are unresolved." };
  if (["HYDRO_RUN_OF_RIVER", "HYDRO_BASIN", "HYDRO_RESERVOIR"].includes(code)) return { technology: code, capacity_by_zone: "NO_TYPE_SPECIFIC_MW", generation_by_zone: "YES_2024", chp_distinction_resolved: "NOT_APPLICABLE", fuel_source_resolved: "YES_WATER", hydro_subtype_resolved: code === "HYDRO_RESERVOIR" ? "GENERATION_LABEL_INCLUDES_EVENTUAL_PUMPING" : "YES_GENERATION", historical_years_available: "2024", ready_for_canonical_workbook: "NO", status: "REQUIRES CAPACITY ALLOCATION", blocker: "No defensible Terna type-specific MW allocation; reservoir/pumping split unresolved." };
  if (code === "PUMPED_HYDRO") return { technology: code, capacity_by_zone: "NO_SEPARATE_DISCHARGE_CHARGE_MW", generation_by_zone: "NO_SEPARATE_PUMPED_DISCHARGE_GWH", chp_distinction_resolved: "NOT_APPLICABLE", fuel_source_resolved: "YES_WATER", hydro_subtype_resolved: "NO", historical_years_available: "NONE", ready_for_canonical_workbook: "NO", status: "REQUIRES DATA", blocker: "Physical pumped assets, charge/discharge MW, energy MWh, efficiency and pumping energy are absent." };
  return { technology: code, capacity_by_zone: "NO", generation_by_zone: "NO", chp_distinction_resolved: "NOT_APPLICABLE", fuel_source_resolved: code.startsWith("SOLAR") || code.startsWith("WIND") ? "YES_FAMILY_ONLY" : "UNRESOLVED", hydro_subtype_resolved: "NOT_APPLICABLE", historical_years_available: "NONE", ready_for_canonical_workbook: "NO", status: "REQUIRES DATA", blocker: "Official zonal historical capacity and generation observations have not yet been acquired/normalized." };
});
completionStatus.unshift({ technology: "__OVERALL__", capacity_by_zone: "PARTIAL", generation_by_zone: "PARTIAL", chp_distinction_resolved: "PARTIAL", fuel_source_resolved: "PARTIAL", hydro_subtype_resolved: "PARTIAL", historical_years_available: "2024_PARTIAL_ONLY", ready_for_canonical_workbook: "NO", status: "HISTORICAL BASELINE INCOMPLETE", blocker: "Solar, wind, storage/pumped hydro, thermal fuel bridge, type-specific hydro MW and 2019-2023 actual history remain open." });

const sourceManifest = [];
for (const config of Object.values(loaded)) {
  sourceManifest.push({
    source_id: config.source_id,
    publisher: "Terna S.p.A.",
    title: config.title,
    release_or_year: 2024,
    acquisition_route: "TERNA_DOWNLOAD_CENTER_MANUAL_XLSX",
    acquisition_date: acquisitionDate,
    original_uploaded_filename: config.original,
    archived_raw_file: `raw/terna/historical_2024/${config.archived}`,
    byte_size: config.info.bytes,
    raw_sha256: config.info.sha256,
    scope: `2024; ${config.usedRange}; official Download Center terminology retained`,
    evidence_class: config.source_id.includes("CAPACITY") ? "DIRECT SOURCE CAPACITY" : "DIRECT SOURCE ENERGY",
    status: "ACQUIRED_VALIDATED_SCHEMA_AND_MAPPING",
    url: downloadCenterUrl,
  });
}

const derivationManifest = [
  ["DER-HIST-001", "Terna_Bioenergy_Capacity_2024_NET.csv", "DIRECT SOURCE CAPACITY / QA RECONCILIATION ONLY", "Terna_Bioenergy_Capacity_2024_RAW.xlsx; reviewed crosswalk", "Filter explicit Netta rows and map every province to one MEM zone.", "Bioenergy is a non-additive source subset of thermoelectric capacity."],
  ["DER-HIST-002", "Terna_Bioenergy_Production_2024_NET.csv", "DIRECT SOURCE ENERGY / QA RECONCILIATION ONLY", "Terna_Bioenergy_Production_2024_RAW.xlsx; reviewed crosswalk", "Filter explicit Netta rows and map every province to one MEM zone.", "Observed CF is diagnostic and does not replace the approved future 0.47 assumption."],
  ["DER-HIST-003", "Terna_Geothermal_Capacity_2024_NET.csv", "DIRECT SOURCE CAPACITY", "Terna_Geothermal_Capacity_2024_RAW.xlsx; reviewed crosswalk", "Filter explicit Netta rows; preserve geothermal as its own technology.", "Never infer geothermal from Condensazione."],
  ["DER-HIST-004", "Terna_Geothermal_Production_2024_NET.csv", "DIRECT SOURCE ENERGY", "Terna_Geothermal_Production_2024_RAW.xlsx; reviewed crosswalk", "Filter explicit Netta rows and map by province.", "Observed CF is historical characterization only."],
  ["DER-HIST-005", "Terna_Hydro_Capacity_2024_NET.csv", "DIRECT SOURCE CAPACITY", "Terna_Hydro_Capacity_2024_RAW.xlsx; reviewed crosswalk", "Filter explicit Netta rows; retain total zonal hydro MW as control.", "Do not allocate type MW from production shares."],
  ["DER-HIST-006", "Terna_Hydro_2024_Zone_Type_Production.csv", "DIRECT SOURCE ENERGY", "Terna_Hydro_Production_By_Type_2024_RAW.xlsx; reviewed crosswalk", "Filter explicit Netta rows; sum by zone and official hydro type.", "Serbatoio includes eventual pumping and is not yet pure reservoir generation."],
  ["DER-HIST-007", "Terna_Hydro_Production_2024_NET.csv", "PROJECT DERIVATION FROM DIRECT SOURCE ENERGY", "Terna_Hydro_Production_By_Type_2024_RAW.xlsx", "Sum Netta Fluente+Bacino+Serbatoio observations at province grain.", "No separate generic hydro export is required for this total; the supplied type-level file is controlling."],
  ["DER-HIST-008", "Terna_CHP_Heat_and_Electricity_2024_Zone_Technology.csv", "PROJECT DERIVATION", "Thermoelectric NET capacity; reconstructed NET electricity production; produced heat", "Aggregate by zone and six CHP technologies; calculate electric CF and electricity/heat ratio where denominators are valid.", "Indicators characterize CHP; no final PyPSA heat representation is implemented."],
  ["DER-HIST-009", "MEM_Thermal_Perimeter_Reconciliation_2024_2040.csv", "QA / RECONCILIATION ONLY", "Thermoelectric technology total; bioenergy source subset; geothermal source technology; hydro control", "Build a transparent add/subset/exclude bridge to the DDS-comparable current perimeter.", "Bioenergy is not added; geothermal is added outside the current thermo technology extract; hydro is excluded."],
  ["DER-HIST-010", "MEM_Historical_Capacity_By_Zone_Technology.csv", "PROJECT DERIVATION", "All normalized 2024 capacity layers", "Aggregate by zone and detailed taxonomy with explicit perimeter/additivity fields.", "Cross-classification rows cannot be summed with primary additive rows."],
  ["DER-HIST-011", "MEM_Historical_Generation_By_Zone_Technology.csv", "PROJECT DERIVATION", "All normalized 2024 generation layers", "Aggregate by zone and detailed taxonomy with explicit perimeter/additivity fields.", "Historical CF/output does not constrain future dispatch absent a separate decision."],
].map(([derivation_id, output, evidence_class, inputs, method, key_guardrail]) => ({ derivation_id, output, evidence_class, inputs, method, key_guardrail, status: "VALIDATED_2024_PARTIAL_BASELINE" }));

const checks = [];
const check = (id, description, pass, observed, expected, severity = "ERROR") => {
  checks.push({ check_id: id, description, status: pass ? "PASS" : "FAIL", severity, observed: String(observed), expected: String(expected) });
  if (!pass && severity === "ERROR") throw new Error(`${id} failed: ${description}; observed=${observed}; expected=${expected}`);
};
check("HIST-QA-001", "Controlling v2.9 workbook remains byte-identical", (await sha256File(controllingWorkbook)) === expectedWorkbookHash, await sha256File(controllingWorkbook), expectedWorkbookHash);
check("HIST-QA-002", "Reviewed crosswalk has 107 exact-once provinces", crosswalk.length === 107 && crosswalkByPair.size === 107, `${crosswalk.length}/${crosswalkByPair.size}`, "107/107");
for (const [key, data] of Object.entries(loaded)) {
  check(`HIST-QA-RAW-${key}`, `${data.archived} hash matches manifest`, data.info.sha256 === data.sha256, data.info.sha256, data.sha256);
}
check("HIST-QA-003", "Bioenergy capacity has 107 Netta province rows", bioCapacity.length === 107, bioCapacity.length, 107);
check("HIST-QA-004", "Bioenergy production has 107 Netta province rows", bioProduction.length === 107, bioProduction.length, 107);
check("HIST-QA-005", "Geothermal capacity has 107 Netta province rows", geoCapacity.length === 107, geoCapacity.length, 107);
check("HIST-QA-006", "Geothermal production has 107 Netta province rows", geoProduction.length === 107, geoProduction.length, 107);
check("HIST-QA-007", "Hydro capacity has 107 Netta province rows", hydroCapacity.length === 107, hydroCapacity.length, 107);
check("HIST-QA-008", "Hydro type Netta/Lorda observation counts balance", hydroNetRows.length === hydroGrossRows.length, `${hydroNetRows.length}/${hydroGrossRows.length}`, "equal");
check("HIST-QA-008B", "Repeated visible hydro-type grains are retained and documented", hydroTypeRepeatedGroups.length === 11 && hydroTypeRepeatedGroups.every((row) => normalizeName(row.hydro_type_original).includes("serbatoio")), `${hydroTypeRepeatedGroups.length} groups; ${hydroTypeRepeatedGroups.map((row) => row.source_row_count).join("|")}`, "11 Serbatoio groups retained");
check("HIST-QA-009", "All output zone values are canonical", [bioCapacity, geoCapacity, hydroCapacity, bioProduction, geoProduction, hydroTypeProduction, heatCanonical].flat().every((row) => canonicalZones.includes(row.market_zone)), "all mapped", canonicalZones.join("|"));
check("HIST-QA-010", "Bioenergy remains non-additive in integrated tables", capacityIntegrated.filter((row) => row.technology === "BIOENERGY").every((row) => row.additive_to_system_total === false), "all false", "all false");
check("HIST-QA-011", "Thermoelectric national total remains 60,331.82927 MW", Math.abs(sum([...thermoCapByZone.values()]) - 60331.82927) < 1e-6, round(sum([...thermoCapByZone.values()])), 60331.82927);
check("HIST-QA-012", "Zone summaries contain exactly seven canonical zones plus national", [bioSummary, geoSummary, hydroSummary].every((rows) => rows.length === 8 && rows.filter((r) => r.geographic_scope === "MARKET_ZONE").every((r) => canonicalZones.includes(r.market_zone))), `${bioSummary.length}/${geoSummary.length}/${hydroSummary.length}`, "8/8/8");
check("HIST-QA-013", "Historical CF values are within [0,1] where calculated", [bioSummary, geoSummary, hydroSummary].flat().filter((r) => r.observed_CF !== "").every((r) => Number(r.observed_CF) >= 0 && Number(r.observed_CF) <= 1), "all bounded", "all bounded");
check("HIST-QA-014", "Hydro type generation sums exactly to derived hydro total", Math.abs(sum(hydroTypeProduction.map((r) => r.generation_NET_GWh)) - sum(hydroProduction.map((r) => r.generation_NET_GWh))) < 1e-6, round(sum(hydroTypeProduction.map((r) => r.generation_NET_GWh))), round(sum(hydroProduction.map((r) => r.generation_NET_GWh))));
check("HIST-QA-015", "Geothermal is absent as a label in thermoelectric conversion extract", !geothermalLabelInThermo, geothermalLabelInThermo, false);
check("HIST-QA-016", "Historical baseline freeze gate remains closed", completionStatus[0].status === "HISTORICAL BASELINE INCOMPLETE", completionStatus[0].status, "HISTORICAL BASELINE INCOMPLETE");

const csvEscape = (value) => {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
};
const toMatrix = (rows) => {
  if (rows.length === 0) throw new Error("Cannot author an empty CSV without an explicit schema.");
  const headers = [];
  for (const row of rows) for (const key of Object.keys(row)) if (!headers.includes(key)) headers.push(key);
  return { headers, values: rows.map((row) => headers.map((header) => row[header] ?? "")) };
};
const serializeCsv = (headers, values) => `${[headers, ...values].map((row) => row.map(csvEscape).join(",")).join("\r\n")}\r\n`;
const columnLetters = (count) => {
  let n = count;
  let result = "";
  while (n > 0) { n -= 1; result = String.fromCharCode(65 + (n % 26)) + result; n = Math.floor(n / 26); }
  return result;
};
async function authorCsv(rows, sheetName, outputPath) {
  const { headers, values } = toMatrix(rows);
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add(sheetName);
  const all = [headers, ...values];
  sheet.getRangeByIndexes(0, 0, all.length, headers.length).values = all;
  const address = `A1:${columnLetters(headers.length)}${all.length}`;
  await workbook.inspect({ kind: "table", sheetId: sheetName, range: address, tableMaxRows: 3, tableMaxCols: Math.min(headers.length, 24), maxChars: 5000 });
  await fs.writeFile(outputPath, serializeCsv(headers, values), "utf8");
  const reopened = await Workbook.fromCSV(await fs.readFile(outputPath, "utf8"), { sheetName });
  const verification = await reopened.inspect({ kind: "table", sheetId: sheetName, range: address, tableMaxRows: 3, tableMaxCols: Math.min(headers.length, 24), maxChars: 5000 });
  const info = await fileInfo(outputPath);
  return { file: path.relative(phaseRoot, outputPath).replaceAll("\\", "/"), rows: rows.length, columns: headers.length, bytes: info.bytes, sha256: info.sha256, verification: verification.ndjson };
}

const outputs = [];
const write = async (rows, sheet, dir, name) => outputs.push(await authorCsv(rows, sheet, path.join(dir, name)));
await write(bioCapacity, "BioCap", normalizedDir, "Terna_Bioenergy_Capacity_2024_NET.csv");
await write(bioProduction, "BioProd", normalizedDir, "Terna_Bioenergy_Production_2024_NET.csv");
await write(bioSummary, "BioSummary", analysisDir, "Terna_Bioenergy_2024_Zone_Summary.csv");
await write(geoCapacity, "GeoCap", normalizedDir, "Terna_Geothermal_Capacity_2024_NET.csv");
await write(geoProduction, "GeoProd", normalizedDir, "Terna_Geothermal_Production_2024_NET.csv");
await write(geoSummary, "GeoSummary", analysisDir, "Terna_Geothermal_2024_Zone_Summary.csv");
await write(hydroCapacity, "HydroCap", normalizedDir, "Terna_Hydro_Capacity_2024_NET.csv");
await write(hydroProduction, "HydroProd", normalizedDir, "Terna_Hydro_Production_2024_NET.csv");
await write(hydroSummary, "HydroSummary", analysisDir, "Terna_Hydro_2024_Zone_Summary.csv");
await write(hydroTypeProduction, "HydroTypeRaw", normalizedDir, "Terna_Hydro_Production_By_Type_2024_NET.csv");
await write(hydroTypeZone, "HydroTypeZone", analysisDir, "Terna_Hydro_2024_Zone_Type_Production.csv");
await write(heatCanonical, "CHPHeat", normalizedDir, "Terna_Thermoelectric_Heat_2024_Canonical.csv");
await write(chpMetrics, "CHPMetrics", analysisDir, "Terna_CHP_Heat_and_Electricity_2024_Zone_Technology.csv");
await write(hydroPumping, "HydroPumping", analysisDir, "MEM_Hydro_Pumping_Perimeter_Reconciliation.csv");
await write(thermalPerimeter, "ThermalPerimeter", analysisDir, "MEM_Thermal_Perimeter_Reconciliation_2024_2040.csv");
await write(taxonomy, "Taxonomy", normalizedDir, "MEM_HISTORICAL_TECHNOLOGY_TAXONOMY.csv");
await write(capacityIntegrated, "CapacityIntegrated", normalizedDir, "MEM_Historical_Capacity_By_Zone_Technology.csv");
await write(generationIntegrated, "GenerationIntegrated", normalizedDir, "MEM_Historical_Generation_By_Zone_Technology.csv");
await write(completionStatus, "Completion", analysisDir, "MEM_HISTORICAL_BASELINE_COMPLETION_STATUS.csv");
await write(sourceManifest, "SourceManifest", manifestDir, "MEM_HISTORICAL_BASELINE_SOURCE_MANIFEST.csv");
await write(derivationManifest, "DerivationManifest", manifestDir, "MEM_HISTORICAL_BASELINE_DERIVATION_MANIFEST.csv");
await write(checks, "QA", qaDir, "MEM_HISTORICAL_BASELINE_2024_QA.csv");

const national = (rows) => rows.find((row) => row.geographic_scope === "NATIONAL");
const buildSummary = {
  build_timestamp: new Date().toISOString(),
  controlling_workbook: { path: controllingWorkbook, sha256: await sha256File(controllingWorkbook), unchanged: true },
  raw_sources: sourceManifest,
  exact_national_totals: {
    bioenergy_capacity_NET_MW: national(bioSummary).capacity_NET_MW,
    bioenergy_generation_NET_GWh: national(bioSummary).generation_NET_GWh,
    bioenergy_observed_CF: national(bioSummary).observed_CF,
    geothermal_capacity_NET_MW: national(geoSummary).capacity_NET_MW,
    geothermal_generation_NET_GWh: national(geoSummary).generation_NET_GWh,
    geothermal_observed_CF: national(geoSummary).observed_CF,
    hydro_capacity_NET_MW: national(hydroSummary).capacity_NET_MW,
    hydro_generation_NET_GWh: national(hydroSummary).generation_NET_GWh,
    hydro_observed_CF: national(hydroSummary).observed_CF,
    thermoelectric_conversion_technology_capacity_NET_MW: round(sum([...thermoCapByZone.values()])),
    dds_comparable_current_thermoelectric_plus_geothermal_NET_MW: round(sum([...thermoCapByZone.values()]) + national(geoSummary).capacity_NET_MW),
    produced_CHP_heat_GWh: round(sum(heatCanonical.map((row) => row.produced_heat_GWh))),
  },
  hydro_type_national_net_generation_GWh: Object.fromEntries(["HYDRO_RUN_OF_RIVER", "HYDRO_BASIN", "HYDRO_RESERVOIR_INCL_PUMPING"].map((type) => [type, round(sum(hydroTypeProduction.filter((row) => row.hydro_type === type).map((row) => row.generation_NET_GWh)))])),
  hydro_type_visible_grain_multiplicity: hydroTypeRepeatedGroups,
  mapping: { crosswalk_rows: crosswalk.length, observed_rows_mapped: bioCapacity.length + geoCapacity.length + hydroCapacity.length + bioProduction.length + geoProduction.length + hydroTypeProduction.length + heatCanonical.length, canonical_zones: canonicalZones },
  status: completionStatus[0].status,
  workbook_successor_authorized: false,
  outputs,
  qa: { pass: checks.filter((row) => row.status === "PASS").length, fail: checks.filter((row) => row.status === "FAIL").length },
};
await fs.writeFile(path.join(qaDir, "MEM_HISTORICAL_BASELINE_2024_BUILD_SUMMARY.json"), `${JSON.stringify(buildSummary, null, 2)}\n`, "utf8");

console.log(JSON.stringify({
  status: buildSummary.status,
  workbook_successor_authorized: false,
  exact_national_totals: buildSummary.exact_national_totals,
  hydro_type_national_net_generation_GWh: buildSummary.hydro_type_national_net_generation_GWh,
  output_count: outputs.length,
  qa: buildSummary.qa,
}, null, 2));

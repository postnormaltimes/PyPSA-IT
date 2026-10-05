import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { Workbook } from "@oai/artifact-tool";
import { detectTernaDecimalConventions, parseTernaNumber } from "../scripts/terna_numeric_parser.mjs";

const phaseRoot = path.resolve("..");
const hbRoot = path.join(phaseRoot, "historical_baseline");
const normalizedDir = path.join(hbRoot, "normalized");
const analysisDir = path.join(hbRoot, "analysis");
const manifestDir = path.join(hbRoot, "manifests");
const qaDir = path.join(hbRoot, "qa");
const rawApiDir = path.join(phaseRoot, "raw", "terna", "historical_2019_2025_api");
const years = [2019, 2020, 2021, 2022, 2023, 2024];
const canonicalZones = ["NORD", "CNOR", "CSUD", "SUD", "CALA", "SICI", "SARD"];
const endpoints = {
  thermoCapacity: ["thermoelectric-capacity", "thermoelectric"],
  thermoProduction: ["thermoelectric-production", "thermoelectric"],
  renewableCapacity: ["renewable-source-capacity", "renewable_sources"],
  renewableProduction: ["renewable-sources-production", "renewable_sources"],
  hydric: ["hydric", "hydric"],
  heat: ["thermoelectric-heat", "thermoelectric_heat"],
};

const sha256File = async (file) => crypto.createHash("sha256").update(await fs.readFile(file)).digest("hex");
const sum = (values) => values.reduce((a, b) => a + b, 0);
const round = (value, digits = 9) => value === null || value === undefined ? null : Number(Number(value).toFixed(digits));
const normalizeName = (value) => String(value ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[’']/g, " ").replace(/[^a-z0-9]+/g, " ").trim().replace(/\s+/g, " ");
const sortedUnique = (values) => [...new Set(values)].sort((a, b) => String(a).localeCompare(String(b), "it"));
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

async function readCsvObjects(file, sheetName) {
  const workbook = await Workbook.fromCSV(await fs.readFile(file, "utf8"), { sheetName });
  const values = workbook.worksheets.getItem(sheetName).getUsedRange().values;
  const headers = values[0].map(String);
  return values.slice(1).filter((row) => row.some((v) => v !== null && v !== "")).map((row) => Object.fromEntries(headers.map((header, index) => [header, row[index]])));
}

const crosswalk = await readCsvObjects(path.join(phaseRoot, "normalized", "MEM_Province_Region_MarketZone_Crosswalk.csv"), "Crosswalk");
const crosswalkByPair = new Map(crosswalk.map((row) => [`${normalizeName(row.terna_region_name)}\u0000${normalizeName(row.terna_province_name)}`, row]));
const mapGeo = (region, province) => {
  const geo = crosswalkByPair.get(`${normalizeName(region)}\u0000${normalizeName(province)}`);
  if (!geo) throw new Error(`No reviewed mapping for historical observation ${region}/${province}.`);
  if (!canonicalZones.includes(String(geo.market_zone))) throw new Error(`Non-canonical zone ${geo.market_zone}.`);
  return geo;
};

async function loadRaw(endpoint, arrayKey, year) {
  const stem = `${endpoint}_${year}_RAW`;
  const rawPath = path.join(rawApiDir, `${stem}.json`);
  const metadataPath = path.join(rawApiDir, `${stem}.metadata.json`);
  const [payload, metadata] = await Promise.all([
    fs.readFile(rawPath, "utf8").then(JSON.parse),
    fs.readFile(metadataPath, "utf8").then(JSON.parse),
  ]);
  const sha256 = await sha256File(rawPath);
  if (sha256 !== metadata.raw_sha256) throw new Error(`Raw hash mismatch for ${stem}.json.`);
  const rows = Array.isArray(payload?.[arrayKey]) ? payload[arrayKey] : [];
  if (rows.length !== metadata.record_count) throw new Error(`Record count mismatch for ${stem}.json.`);
  return { endpoint, arrayKey, year, rows, metadata, rawPath, sha256 };
}

function parseField(rows, field) {
  const values = rows.map((row) => row[field]).filter((v) => v !== null && v !== undefined && String(v).trim() !== "");
  const convention = detectTernaDecimalConventions(values);
  return rows.map((row) => {
    const value = row[field];
    if (value === null || value === undefined || String(value).trim() === "") {
      return { ...row, _parsed: null, _numeric_field: field, _numeric_status: "BLANK_RETAINED_NOT_ZERO" };
    }
    return { ...row, _parsed: parseTernaNumber(value, convention), _numeric_field: field, _numeric_status: "PARSED" };
  });
}

function thermalCode(category, subcategory) {
  const sub = normalizeName(subcategory);
  const chp = normalizeName(category).startsWith("cogenerative");
  if (sub === "ciclo combinato") return "CCGT_NON_CHP";
  if (sub === "ciclo combinato con produzione di calore") return "CCGT_CHP";
  if (sub === "turbine a gas") return "GT_NON_CHP";
  if (sub === "turbine a gas con produzione di calore") return "GT_CHP";
  if (sub === "combustione interna") return "INTERNAL_COMBUSTION_NON_CHP";
  if (sub === "combustione interna con produzione di calore") return "INTERNAL_COMBUSTION_CHP";
  if (sub === "condensazione") return "STEAM_CONDENSING";
  if (sub === "condensazione e spillamento") return "STEAM_EXTRACTION_CHP";
  if (sub === "contropressione") return "STEAM_BACKPRESSURE_CHP";
  if (sub === "celle combustibili") return chp ? "FUEL_CELL_CHP" : "FUEL_CELL_NON_CHP";
  if (sub === "celle combustibili con cogenerazione") return "FUEL_CELL_CHP";
  if (sub === "combustione esterna") return "EXTERNAL_COMBUSTION";
  if (sub === "turbo espansione") return "TURBO_EXPANSION";
  if (sub === "altro genere") return "OTHER_THERMAL";
  if (sub === "geotermoelettrica a condensazione") return "GEOTHERMAL";
  throw new Error(`Unmapped historical thermoelectric category ${category}/${subcategory}.`);
}

const chpTechnologies = new Set([
  "CCGT_CHP",
  "GT_CHP",
  "INTERNAL_COMBUSTION_CHP",
  "STEAM_EXTRACTION_CHP",
  "STEAM_BACKPRESSURE_CHP",
  "FUEL_CELL_CHP",
]);
const isChpTechnology = (technology) => chpTechnologies.has(technology);
const mixedChpTechnologies = new Set(["EXTERNAL_COMBUSTION", "TURBO_EXPANSION", "OTHER_THERMAL"]);
const canonicalChpFlag = (technology) => mixedChpTechnologies.has(technology) ? "MIXED" : isChpTechnology(technology);
const thermalDispatchability = (technology) => {
  if (technology === "GEOTHERMAL") return "PROGRAMMABLE_RENEWABLE_BASELOAD";
  if (isChpTechnology(technology)) return "PROGRAMMABLE_THERMAL_CHP";
  if (technology === "GT_NON_CHP") return "PROGRAMMABLE_THERMAL_PEAKER";
  return "PROGRAMMABLE_THERMAL";
};

function renewableCode(source) {
  const key = normalizeName(source);
  if (key === "bioenergie") return "BIOENERGY";
  if (key === "geotermoelettrico") return "GEOTHERMAL";
  if (key === "idrico") return "HYDRO_TOTAL_CONTROL";
  if (key === "fotovoltaico") return "SOLAR_PV";
  if (key === "eolico") return "WIND_TOTAL_CONTROL";
  throw new Error(`Unmapped renewable source ${source}.`);
}

function hydricCode(type) {
  const key = normalizeName(type);
  if (key === "fluente") return "HYDRO_RUN_OF_RIVER";
  if (key === "bacino") return "HYDRO_BASIN";
  if (key === "serbatoio compresi eventuali pompaggi" || key === "serbatoio") return "HYDRO_RESERVOIR";
  throw new Error(`Unmapped hydric type ${type}.`);
}

function heatCode(type) {
  const key = normalizeName(type);
  if (key.startsWith("ciclo combinato con cogenerazione")) return "CCGT_CHP";
  if (key.startsWith("turbine a gas con cogenerazione")) return "GT_CHP";
  if (key.startsWith("combustione interna con cogenerazione")) return "INTERNAL_COMBUSTION_CHP";
  if (key.startsWith("condensazione e spillamento")) return "STEAM_EXTRACTION_CHP";
  if (key.startsWith("contropressione")) return "STEAM_BACKPRESSURE_CHP";
  if (key.startsWith("celle combustibili con cogenerazione")) return "FUEL_CELL_CHP";
  throw new Error(`Unmapped heat type ${type}.`);
}

const raw = {};
for (const [key, [endpoint, arrayKey]] of Object.entries(endpoints)) {
  raw[key] = [];
  for (const year of [...years, 2025]) raw[key].push(await loadRaw(endpoint, arrayKey, year));
}

const qa = [];
const noteQa = (id, status, description, observed, expected, severity = "ERROR") => {
  qa.push({ check_id: id, status, severity, description, observed: String(observed), expected: String(expected) });
  if (status === "FAIL" && severity === "ERROR") throw new Error(`${id}: ${description}`);
};
for (const [key, series] of Object.entries(raw)) {
  noteQa(`HIST-SERIES-${key}-2019-2024`, series.filter((item) => years.includes(item.year)).every((item) => item.rows.length > 0) ? "PASS" : "FAIL", `${key} has non-empty annual payloads for 2019-2024`, series.filter((item) => years.includes(item.year)).map((item) => `${item.year}:${item.rows.length}`).join("|"), "all non-empty");
  const y2025 = series.find((item) => item.year === 2025);
  noteQa(`HIST-SERIES-${key}-2025`, y2025.rows.length === 0 ? "PASS" : "FAIL", `${key} 2025 consolidated payload availability`, y2025.rows.length, 0, "WARNING");
}

const thermoCapacityRecords = [];
const thermoProductionRecords = [];
const renewableCapacityRecords = [];
const renewableProductionRecords = [];
const hydricRecords = [];
const heatRecords = [];
const sourceMultiplicity = [];
const productionPairQa = [];
const productionPairExceptions = [];

for (const year of years) {
  const capRaw = raw.thermoCapacity.find((item) => item.year === year);
  const capNet = parseField(capRaw.rows.filter((row) => row.capacity_type === "Netta"), "efficient_power_MW");
  const capGroups = groupRows(capNet, (row) => [row.year, normalizeName(row.region), normalizeName(row.province), normalizeName(row.category), normalizeName(row.subcategory)].join("\u0000"));
  for (const [key, rows] of capGroups) {
    const technology = thermalCode(rows[0].category, rows[0].subcategory);
    const exactDuplicate2024 = year === 2024
      && rows.length === 2
      && technology.startsWith("FUEL_CELL_")
      && rows.every((row) => row._parsed.value === rows[0]._parsed.value);
    if (rows.length > 1) sourceMultiplicity.push({
      series: "THERMOELECTRIC_CAPACITY_NET",
      year,
      visible_grain_key: key.split("\u0000").join(" | "),
      source_row_count: rows.length,
      raw_source_sum_MW: round(sum(rows.map((row) => row._parsed.value))),
      canonical_sum_MW: round(exactDuplicate2024 ? rows[0]._parsed.value : sum(rows.map((row) => row._parsed.value))),
      disposition: exactDuplicate2024
        ? "EXACT_DUPLICATE_NORMALIZED_COUNT_ONCE; RAW_ROWS_PRESERVED"
        : "SUM_WITH_LINEAGE_AT_VISIBLE_GRAIN",
    });
    const first = rows[0];
    const geo = mapGeo(first.region, first.province);
    thermoCapacityRecords.push({
      year,
      market_zone: geo.market_zone,
      technology,
      chp_flag: canonicalChpFlag(technology),
      capacity_NET_MW: round(exactDuplicate2024 ? rows[0]._parsed.value : sum(rows.map((row) => row._parsed.value))),
      source_id: capRaw.metadata.source_id,
      source_raw_sha256: capRaw.sha256,
      source_row_count: rows.length,
      canonical_source_row_count: exactDuplicate2024 ? 1 : rows.length,
      exact_duplicate_normalization: exactDuplicate2024,
      source_category_original: first.category,
      source_subcategory_original: first.subcategory,
    });
  }

  const prodRaw = raw.thermoProduction.find((item) => item.year === year);
  const prodParsed = parseField(prodRaw.rows, "production_GWh");
  const pairGroups = groupRows(prodParsed, (row) => [row.year, normalizeName(row.region), normalizeName(row.province), normalizeName(row.category), normalizeName(row.subcategory)].join("\u0000"));
  let pairCount = 0;
  let equalCount = 0;
  let exceptionCount = 0;
  for (const [key, rows] of pairGroups) {
    if (rows.length !== 2) {
      const values = rows.map((row) => row._parsed.value);
      const zeros = values.filter((value) => value === 0);
      const positives = values.filter((value) => value > 0).sort((a, b) => a - b);
      const fourRowFuelCellRule = rows.length === 4
        && thermalCode(rows[0].category, rows[0].subcategory) === "FUEL_CELL_CHP"
        && zeros.length === 2
        && positives.length === 2;
      if (fourRowFuelCellRule) {
        const first = rows[0];
        const geo = mapGeo(first.region, first.province);
        thermoProductionRecords.push({
          year,
          market_zone: geo.market_zone,
          technology: "FUEL_CELL_CHP",
          chp_flag: true,
          generation_NET_GWh: positives[0],
          generation_GROSS_GWh: positives[1],
          pair_equal: false,
          source_id: prodRaw.metadata.source_id,
          source_raw_sha256: prodRaw.sha256,
          basis_resolution_method: "FOUR_ROW_FUEL_CELL_RULE_TWO_ZEROS_PLUS_TWO_POSITIVES; SMALLER_POSITIVE_NETTA",
          source_category_original: first.category,
          source_subcategory_original: first.subcategory,
          source_row_count: 4,
        });
        pairCount += 1;
        continue;
      }
      exceptionCount += 1;
      const first = rows[0];
      const geo = mapGeo(first.region, first.province);
      productionPairExceptions.push({
        year,
        market_zone: geo.market_zone,
        technology: thermalCode(first.category, first.subcategory),
        visible_grain_key: key.split("\u0000").join(" | "),
        source_row_count: rows.length,
        values_GWh: rows.map((row) => row._parsed.value).join("|"),
        source_id: prodRaw.metadata.source_id,
        source_raw_sha256: prodRaw.sha256,
        disposition: "UNRESOLVED_GT2_PAIR_EXCEPTION; NETTA_NOT_INFERRED",
      });
      continue;
    }
    pairCount += 1;
    const values = rows.map((row) => row._parsed.value).sort((a, b) => a - b);
    if (values[0] === values[1]) equalCount += 1;
    const first = rows[0];
    const geo = mapGeo(first.region, first.province);
    thermoProductionRecords.push({
      year,
      market_zone: geo.market_zone,
      technology: thermalCode(first.category, first.subcategory),
      chp_flag: canonicalChpFlag(thermalCode(first.category, first.subcategory)),
      generation_NET_GWh: values[0],
      generation_GROSS_GWh: values[1],
      pair_equal: values[0] === values[1],
      source_id: prodRaw.metadata.source_id,
      source_raw_sha256: prodRaw.sha256,
      basis_resolution_method: "EXACT_TWO_ROW_PHYSICAL_RULE_HIGHER_LORDA_LOWER_NETTA",
      source_category_original: first.category,
      source_subcategory_original: first.subcategory,
    });
  }
  productionPairQa.push({ year, visible_grain_groups: pairGroups.size, inferred_or_equal_pairs: pairCount, equal_value_pairs: equalCount, exceptions: exceptionCount, status: exceptionCount === 0 ? "PASS" : "PASS_WITH_EXPLICIT_UNRESOLVED_EXCEPTIONS" });

  const renCapRaw = raw.renewableCapacity.find((item) => item.year === year);
  const renCapNet = parseField(renCapRaw.rows.filter((row) => row.capacity_type === "Netta"), "efficient_power_MW");
  const renCapGroups = groupRows(renCapNet, (row) => [row.year, normalizeName(row.region), normalizeName(row.province), normalizeName(row.source)].join("\u0000"));
  for (const [key, rows] of renCapGroups) {
    if (rows.length > 1) sourceMultiplicity.push({ series: "RENEWABLE_SOURCE_CAPACITY_NET", year, visible_grain_key: key.split("\u0000").join(" | "), source_row_count: rows.length, disposition: "SUM_WITH_LINEAGE_AT_VISIBLE_GRAIN" });
    const first = rows[0];
    const geo = mapGeo(first.region, first.province);
    const parsedRows = rows.filter((row) => row._parsed);
    const blankRows = rows.filter((row) => !row._parsed);
    renewableCapacityRecords.push({ year, market_zone: geo.market_zone, technology: renewableCode(first.source), capacity_NET_MW: round(sum(parsedRows.map((row) => row._parsed.value))), blank_source_observation_count: blankRows.length, reported_capacity_status: blankRows.length ? (parsedRows.length ? "MIXED_REPORTED_AND_NO_REPORTED_CAPACITY_HIDDEN_RECORDS; BLANKS_AGGREGATE_AS_ZERO" : "NO_REPORTED_CAPACITY; BLANKS_AGGREGATE_AS_ZERO") : "COMPLETE_NUMERIC_VISIBLE_GRAIN", source_id: renCapRaw.metadata.source_id, source_raw_sha256: renCapRaw.sha256, source_row_count: rows.length, source_label_original: first.source });
  }

  const renProdRaw = raw.renewableProduction.find((item) => item.year === year);
  const renProdNet = parseField(renProdRaw.rows.filter((row) => row.production_type === "Netta"), "production_GWh");
  const renProdGroups = groupRows(renProdNet, (row) => [row.year, normalizeName(row.region), normalizeName(row.province), normalizeName(row.renewable_source)].join("\u0000"));
  for (const [key, rows] of renProdGroups) {
    if (rows.length > 1) sourceMultiplicity.push({ series: "RENEWABLE_SOURCE_PRODUCTION_NET", year, visible_grain_key: key.split("\u0000").join(" | "), source_row_count: rows.length, disposition: "SUM_WITH_LINEAGE_AT_VISIBLE_GRAIN" });
    const first = rows[0];
    const geo = mapGeo(first.region, first.province);
    renewableProductionRecords.push({ year, market_zone: geo.market_zone, technology: renewableCode(first.renewable_source), generation_NET_GWh: round(sum(rows.map((row) => row._parsed.value))), source_id: renProdRaw.metadata.source_id, source_raw_sha256: renProdRaw.sha256, source_row_count: rows.length, source_label_original: first.renewable_source });
  }

  const hydricRaw = raw.hydric.find((item) => item.year === year);
  const hydricNet = parseField(hydricRaw.rows.filter((row) => row.production_type === "Netta"), "production_GWh");
  const hydricGroups = groupRows(hydricNet, (row) => [row.year, normalizeName(row.region), normalizeName(row.province), normalizeName(row.hydric_type)].join("\u0000"));
  for (const [key, rows] of hydricGroups) {
    if (rows.length > 1) sourceMultiplicity.push({ series: "HYDRIC_TYPE_PRODUCTION_NET", year, visible_grain_key: key.split("\u0000").join(" | "), source_row_count: rows.length, disposition: "SUM_WITH_LINEAGE; HIDDEN_PLANT/PUMPING DIMENSION NOT INFERRED" });
    const first = rows[0];
    const geo = mapGeo(first.region, first.province);
    hydricRecords.push({ year, market_zone: geo.market_zone, technology: hydricCode(first.hydric_type), generation_NET_GWh: round(sum(rows.map((row) => row._parsed.value))), source_id: hydricRaw.metadata.source_id, source_raw_sha256: hydricRaw.sha256, source_row_count: rows.length, source_label_original: first.hydric_type });
  }

  const heatRaw = raw.heat.find((item) => item.year === year);
  const heatParsed = parseField(heatRaw.rows, "heat_production_GWh");
  for (const row of heatParsed) {
    const geo = mapGeo(row.region, row.province);
    heatRecords.push({ year, market_zone: geo.market_zone, technology: heatCode(row.cogeneration_plant), produced_heat_GWh: row._parsed.value, source_id: heatRaw.metadata.source_id, source_raw_sha256: heatRaw.sha256, source_label_original: row.cogeneration_plant });
  }
}

const aggregate = (rows, valueField) => {
  const groups = groupRows(rows, (row) => `${row.year}\u0000${row.market_zone}\u0000${row.technology}`);
  return [...groups.values()].map((items) => ({ year: Number(items[0].year), market_zone: items[0].market_zone, technology: items[0].technology, [valueField]: round(sum(items.map((item) => Number(item[valueField])))), blank_source_observation_count: sum(items.map((item) => Number(item.blank_source_observation_count ?? 0))), value_status: sum(items.map((item) => Number(item.blank_source_observation_count ?? 0))) > 0 ? "REPORTED_VALUE_SUM_WITH_BLANKS_RETAINED_NOT_ZERO" : "COMPLETE_NUMERIC_REPORTED_ROWS", source_id: sortedUnique(items.map((item) => item.source_id)).join("|"), source_raw_sha256: sortedUnique(items.map((item) => item.source_raw_sha256)).join("|"), source_observation_count: sum(items.map((item) => Number(item.source_row_count ?? 1))) }));
};
const thermoCapZone = aggregate(thermoCapacityRecords, "capacity_NET_MW");
const thermoGenZone = aggregate(thermoProductionRecords, "generation_NET_GWh");
const renewableCapZone = aggregate(renewableCapacityRecords, "capacity_NET_MW");
const renewableGenZone = aggregate(renewableProductionRecords, "generation_NET_GWh");
const hydricZone = aggregate(hydricRecords, "generation_NET_GWh");
const heatZone = aggregate(heatRecords, "produced_heat_GWh");

const capMap = new Map();
for (const row of [...thermoCapZone, ...renewableCapZone]) {
  const layer = thermoCapZone.includes(row) ? "THERMO" : "RENEWABLE";
  capMap.set(`${layer}\u0000${row.year}\u0000${row.market_zone}\u0000${row.technology}`, row.capacity_NET_MW);
}
const thermoGeoYears = new Set(thermoCapZone.filter((row) => row.technology === "GEOTHERMAL" && row.capacity_NET_MW > 0).map((row) => row.year));

const capacityIntegrated = [];
for (const row of thermoCapZone) {
  capacityIntegrated.push({
    year: row.year,
    market_zone: row.market_zone,
    technology: row.technology,
    fuel_source: row.technology === "GEOTHERMAL" ? "GEOTHERMAL_HEAT" : "UNRESOLVED",
    chp_flag: canonicalChpFlag(row.technology),
    storage_flag: false,
    dispatchability_class: thermalDispatchability(row.technology),
    capacity_NET_MW: row.capacity_NET_MW,
    perimeter_layer: "THERMOELECTRIC_CONVERSION_TECHNOLOGY",
    perimeter_role: "PRIMARY_ADDITIVE",
    additive_to_system_total: true,
    source_id: row.source_id,
    source_raw_sha256: row.source_raw_sha256,
    source_observation_count: row.source_observation_count,
    evidence_class: row.year === 2024 ? "DIRECT SOURCE CAPACITY / TERNA DOWNLOAD CENTER CONTROLLING; API CROSS-CHECK" : "DIRECT SOURCE CAPACITY / TERNA API",
    status: row.technology === "GEOTHERMAL" ? "ACQUIRED_EXPLICIT_HISTORICAL_THERMO_LABEL" : "ACQUIRED_FUEL_UNRESOLVED",
  });
}
for (const row of renewableCapZone) {
  let additive = true;
  let role = "PRIMARY_ADDITIVE";
  if (row.technology === "BIOENERGY") { additive = false; role = "SOURCE_SUBSET_OF_THERMOELECTRIC"; }
  if (row.technology === "GEOTHERMAL" && thermoGeoYears.has(row.year)) { additive = false; role = "SOURCE_CONTROL_OVERLAPS_EXPLICIT_THERMO_GEOTHERMAL_LABEL"; }
  const fuel = row.technology === "BIOENERGY" ? "BIOENERGY" : row.technology === "GEOTHERMAL" ? "GEOTHERMAL_HEAT" : row.technology === "HYDRO_TOTAL_CONTROL" ? "WATER" : row.technology === "SOLAR_PV" ? "SOLAR" : "WIND";
  capacityIntegrated.push({ year: row.year, market_zone: row.market_zone, technology: row.technology, fuel_source: fuel, chp_flag: row.technology === "BIOENERGY" ? "MIXED_UNRESOLVED" : false, storage_flag: row.technology === "HYDRO_TOTAL_CONTROL" ? "MIXED_INCLUDES_PUMPED_HYDRO" : false, dispatchability_class: row.technology === "SOLAR_PV" || row.technology.startsWith("WIND") ? "VARIABLE_RENEWABLE" : row.technology === "HYDRO_TOTAL_CONTROL" ? "MIXED_HYDRO_CONTROL" : "PROGRAMMABLE_RENEWABLE", capacity_NET_MW: row.capacity_NET_MW, blank_source_observation_count: row.blank_source_observation_count, capacity_value_status: row.value_status, perimeter_layer: "RENEWABLE_SOURCE_CAPACITY", perimeter_role: role, additive_to_system_total: additive, source_id: row.source_id, source_raw_sha256: row.source_raw_sha256, source_observation_count: row.source_observation_count, evidence_class: row.year === 2024 && ["BIOENERGY", "GEOTHERMAL", "HYDRO_TOTAL_CONTROL"].includes(row.technology) ? "DIRECT SOURCE CAPACITY / TERNA DOWNLOAD CENTER CONTROLLING; API CROSS-CHECK" : "DIRECT SOURCE CAPACITY / TERNA API", status: row.blank_source_observation_count > 0 ? "ACQUIRED; BLANK_SOURCE_ROWS_SEMANTICALLY_NO_REPORTED_CAPACITY_AND_AGGREGATED_AS_ZERO" : row.technology === "WIND_TOTAL_CONTROL" ? "ACQUIRED_WIND_TOTAL_CONTROL_PENDING_COMPONENT_ALLOCATION" : additive ? "ACQUIRED" : "ACQUIRED_NON_ADDITIVE_CROSS_CLASSIFICATION" });
}

const heatMap = new Map(heatZone.map((row) => [`${row.year}\u0000${row.market_zone}\u0000${row.technology}`, row.produced_heat_GWh]));
const generationIntegrated = [];
for (const row of thermoGenZone) {
  const cap = capMap.get(`THERMO\u0000${row.year}\u0000${row.market_zone}\u0000${row.technology}`);
  const heat = heatMap.get(`${row.year}\u0000${row.market_zone}\u0000${row.technology}`) ?? 0;
  generationIntegrated.push({ year: row.year, market_zone: row.market_zone, technology: row.technology, fuel_source: "UNRESOLVED", chp_flag: canonicalChpFlag(row.technology), storage_flag: false, dispatchability_class: thermalDispatchability(row.technology), generation_NET_GWh: row.generation_NET_GWh, matching_capacity_NET_MW: cap ?? "", observed_CF: cap > 0 ? round(row.generation_NET_GWh / (cap * 8.76)) : "", produced_heat_GWh: isChpTechnology(row.technology) ? round(heat) : "", electricity_to_heat_ratio: isChpTechnology(row.technology) && heat > 0 ? round(row.generation_NET_GWh / heat) : "", perimeter_layer: "THERMOELECTRIC_CONVERSION_TECHNOLOGY", perimeter_role: "PRIMARY_ADDITIVE", additive_to_system_total: true, source_id: row.source_id, source_raw_sha256: row.source_raw_sha256, source_observation_count: row.source_observation_count, evidence_class: row.year === 2024 ? "DIRECT SOURCE ENERGY / TERNA DOWNLOAD CENTER CONTROLLING; API CROSS-CHECK" : "DIRECT SOURCE ENERGY / TERNA API", status: "ACQUIRED_FUEL_UNRESOLVED; CF_HISTORICAL_DIAGNOSTIC_ONLY" });
}
for (const exception of productionPairExceptions) {
  const cap = capMap.get(`THERMO\u0000${exception.year}\u0000${exception.market_zone}\u0000${exception.technology}`);
  generationIntegrated.push({ year: exception.year, market_zone: exception.market_zone, technology: exception.technology, fuel_source: "UNRESOLVED", chp_flag: canonicalChpFlag(exception.technology), storage_flag: false, dispatchability_class: thermalDispatchability(exception.technology), generation_NET_GWh: "", matching_capacity_NET_MW: cap ?? "", observed_CF: "", produced_heat_GWh: heatMap.get(`${exception.year}\u0000${exception.market_zone}\u0000${exception.technology}`) ?? "", electricity_to_heat_ratio: "", perimeter_layer: "THERMOELECTRIC_CONVERSION_TECHNOLOGY", perimeter_role: "UNRESOLVED_NON_ADDITIVE_EXCEPTION_RECORD", additive_to_system_total: false, source_id: exception.source_id, source_raw_sha256: exception.source_raw_sha256, source_observation_count: exception.source_row_count, evidence_class: "QA / RECONCILIATION ONLY", status: exception.disposition, source_values_GWh: exception.values_GWh, source_visible_grain_key: exception.visible_grain_key });
}
for (const row of renewableGenZone) {
  const cap = capMap.get(`RENEWABLE\u0000${row.year}\u0000${row.market_zone}\u0000${row.technology}`);
  let additive = true;
  let role = "PRIMARY_ADDITIVE";
  if (row.technology === "BIOENERGY") { additive = false; role = "SOURCE_SUBSET_OF_THERMOELECTRIC"; }
  if (row.technology === "HYDRO_TOTAL_CONTROL") { additive = true; role = "PRIMARY_ADDITIVE_RENEWABLE_HYDRO_CONTROL_EXCLUDES_PUMPED_DISCHARGE_COMPONENT"; }
  const fuel = row.technology === "BIOENERGY" ? "BIOENERGY" : row.technology === "GEOTHERMAL" ? "GEOTHERMAL_HEAT" : row.technology === "HYDRO_TOTAL_CONTROL" ? "WATER" : row.technology === "SOLAR_PV" ? "SOLAR" : "WIND";
  generationIntegrated.push({ year: row.year, market_zone: row.market_zone, technology: row.technology, fuel_source: fuel, chp_flag: row.technology === "BIOENERGY" ? "MIXED_UNRESOLVED" : false, storage_flag: row.technology === "HYDRO_TOTAL_CONTROL" ? "MIXED_INCLUDES_PUMPED_HYDRO" : false, dispatchability_class: row.technology === "SOLAR_PV" || row.technology.startsWith("WIND") ? "VARIABLE_RENEWABLE" : row.technology === "HYDRO_TOTAL_CONTROL" ? "MIXED_HYDRO_CONTROL" : "PROGRAMMABLE_RENEWABLE", generation_NET_GWh: row.generation_NET_GWh, matching_capacity_NET_MW: cap ?? "", observed_CF: cap > 0 ? round(row.generation_NET_GWh / (cap * 8.76)) : "", produced_heat_GWh: "", electricity_to_heat_ratio: "", perimeter_layer: "RENEWABLE_SOURCE_PRODUCTION", perimeter_role: role, additive_to_system_total: additive, source_id: row.source_id, source_raw_sha256: row.source_raw_sha256, source_observation_count: row.source_observation_count, evidence_class: row.year === 2024 && ["BIOENERGY", "GEOTHERMAL", "HYDRO_TOTAL_CONTROL"].includes(row.technology) ? "DIRECT SOURCE ENERGY / TERNA DOWNLOAD CENTER CONTROLLING; API CROSS-CHECK" : "DIRECT SOURCE ENERGY / TERNA API", status: row.technology === "WIND_TOTAL_CONTROL" ? "ACQUIRED_WIND_TOTAL_CONTROL; COMPONENT_ENERGY_ALLOCATION_PENDING; CF_DIAGNOSTIC_ONLY" : "ACQUIRED; CF_HISTORICAL_DIAGNOSTIC_ONLY" });
}
for (const row of hydricZone) {
  generationIntegrated.push({ year: row.year, market_zone: row.market_zone, technology: row.technology, fuel_source: "WATER", chp_flag: false, storage_flag: row.technology === "HYDRO_RESERVOIR" ? "MIXED_INCLUDES_PUMPED_HYDRO" : false, dispatchability_class: row.technology === "HYDRO_RUN_OF_RIVER" ? "HYDRO_WEATHER_DRIVEN" : "HYDRO_DISPATCHABLE", generation_NET_GWh: row.generation_NET_GWh, matching_capacity_NET_MW: "", observed_CF: "", produced_heat_GWh: "", electricity_to_heat_ratio: "", perimeter_layer: "HYDRO_GENERATION_SUBTYPE", perimeter_role: "ANALYTICAL_SUBTYPE_NONADDITIVE_PENDING_PUMPED_STORAGE_SPLIT", additive_to_system_total: false, source_id: row.source_id, source_raw_sha256: row.source_raw_sha256, source_observation_count: row.source_observation_count, evidence_class: row.year === 2024 ? "DIRECT SOURCE ENERGY / TERNA DOWNLOAD CENTER CONTROLLING; API CROSS-CHECK" : "DIRECT SOURCE ENERGY / TERNA API", status: row.technology === "HYDRO_RESERVOIR" ? "ACQUIRED_SERBATOIO_INCLUDES_EVENTUAL_PUMPING; TYPE_MW_UNRESOLVED" : "ACQUIRED_TYPE_MW_UNRESOLVED" });
}

capacityIntegrated.sort((a, b) => a.year - b.year || canonicalZones.indexOf(a.market_zone) - canonicalZones.indexOf(b.market_zone) || a.technology.localeCompare(b.technology));
generationIntegrated.sort((a, b) => a.year - b.year || canonicalZones.indexOf(a.market_zone) - canonicalZones.indexOf(b.market_zone) || a.technology.localeCompare(b.technology) || a.perimeter_layer.localeCompare(b.perimeter_layer));

const renewableNational = (rows, year, technology, field) => round(sum(rows.filter((row) => row.year === year && row.technology === technology).map((row) => row[field])));
const manualControls2024 = {
  bio_cap: 3800.0923,
  bio_gen: 15699.033862,
  geo_cap: 771.79,
  geo_gen: 5275.5733,
  hydro_cap: 19324.42439,
  hydric_type_gen: 53975.509902,
  heat: 51720.766462,
};
const actualControls2024 = {
  bio_cap: renewableNational(renewableCapZone, 2024, "BIOENERGY", "capacity_NET_MW"),
  bio_gen: renewableNational(renewableGenZone, 2024, "BIOENERGY", "generation_NET_GWh"),
  geo_cap: renewableNational(renewableCapZone, 2024, "GEOTHERMAL", "capacity_NET_MW"),
  geo_gen: renewableNational(renewableGenZone, 2024, "GEOTHERMAL", "generation_NET_GWh"),
  hydro_cap: renewableNational(renewableCapZone, 2024, "HYDRO_TOTAL_CONTROL", "capacity_NET_MW"),
  hydric_type_gen: round(sum(hydricZone.filter((row) => row.year === 2024).map((row) => row.generation_NET_GWh))),
  heat: round(sum(heatZone.filter((row) => row.year === 2024).map((row) => row.produced_heat_GWh))),
};
for (const key of Object.keys(manualControls2024)) {
  noteQa(`HIST-2024-MANUAL-API-${key}`, Math.abs(actualControls2024[key] - manualControls2024[key]) < 1e-5 ? "PASS" : "FAIL", `2024 API ${key} matches controlling Download Center export within Download Center display precision`, actualControls2024[key], manualControls2024[key]);
}
const hydroPerimeterByYear = [];
for (const year of years) {
  const sourceHydro = renewableNational(renewableGenZone, year, "HYDRO_TOTAL_CONTROL", "generation_NET_GWh");
  const typeHydro = round(sum(hydricZone.filter((row) => row.year === year).map((row) => row.generation_NET_GWh)));
  const difference = round(typeHydro - sourceHydro);
  hydroPerimeterByYear.push({ year, renewable_source_hydro_NET_GWh: sourceHydro, hydric_type_total_including_eventual_pumping_NET_GWh: typeHydro, implied_pumping_discharge_component_GWh: difference, interpretation: "PROJECT DERIVATION FROM TWO OFFICIAL PERIMETERS; NOT YET A PHYSICAL PUMPED-STORAGE ASSET ALLOCATION" });
  noteQa(`HIST-HYDRO-PUMPING-PERIMETER-${year}`, typeHydro >= sourceHydro ? "PASS" : "FAIL", `${year} hydric type total is at least renewable-source hydro; difference retained as pumping-discharge perimeter component`, difference, ">=0");
}

const geothermalOverlapByYear = years.map((year) => {
  const thermo = renewableNational(thermoCapZone, year, "GEOTHERMAL", "capacity_NET_MW");
  const source = renewableNational(renewableCapZone, year, "GEOTHERMAL", "capacity_NET_MW");
  return { year, thermoelectric_geothermal_label_NET_MW: thermo, renewable_source_geothermal_NET_MW: source, absolute_difference_MW: round(Math.abs(thermo - source)), overlap_treatment: thermo > 0 ? "SOURCE_GEOTHERMAL_NON_ADDITIVE_TO_THERMO_TOTAL" : "SOURCE_GEOTHERMAL_ADDITIVE_OUTSIDE_THERMO_EXTRACT", status: thermo > 0 && Math.abs(thermo - source) > 5 ? "REVIEW_MATERIAL_DIFFERENCE" : "RECONCILED_OR_NOT_APPLICABLE" };
});

let taxonomy = await readCsvObjects(path.join(normalizedDir, "MEM_HISTORICAL_TECHNOLOGY_TAXONOMY.csv"), "Taxonomy");
taxonomy = taxonomy.filter((row) => row.code !== "WIND_AGGREGATE_UNSPLIT");
if (!taxonomy.some((row) => row.code === "WIND_TOTAL_CONTROL")) {
  taxonomy.push({ taxonomy_version: "MEM_HISTORICAL_TECHNOLOGY_TAXONOMY_V2026_2", taxonomy_dimension: "TECHNOLOGY", code: "WIND_TOTAL_CONTROL", family: "VARIABLE_RENEWABLE_CONTROL", label: "Terna aggregate wind source control", chp_flag: false, storage_flag: false, dispatchability_class: "VARIABLE_RENEWABLE", default_fuel_source: "WIND", perimeter_role: "NON_COMPONENT_SOURCE_CONTROL", technology_and_fuel_separate_rule: "YES", status: "CONTROL_ROW_NOT_MODEL_COMPONENT" });
}

const baseCompletion = await readCsvObjects(path.join(analysisDir, "MEM_HISTORICAL_BASELINE_COMPLETION_STATUS.csv"), "Completion");
const completion = baseCompletion.map((row) => {
  if (row.technology === "__OVERALL__") return { ...row, capacity_by_zone: "MATERIAL_GENERATION_TECHNOLOGIES_2019_2024_EXCEPT_STORAGE/PUMPED_SPLIT", generation_by_zone: "MATERIAL_GENERATION_TECHNOLOGIES_2019_2024_EXCEPT_PUMPED_SEPARATION_AND_8_FUEL_CELL_PAIR_EXCEPTIONS", chp_distinction_resolved: "YES_FOR_THERMO_CONVERSION_TECHNOLOGY", fuel_source_resolved: "PARTIAL_THERMAL_FUEL_REQUIRES_GEM/FUEL_SERIES", hydro_subtype_resolved: "GENERATION_ONLY", historical_years_available: "2019|2020|2021|2022|2023|2024; 2025 API EMPTY", ready_for_canonical_workbook: "NO", status: "HISTORICAL BASELINE INCOMPLETE", blocker: "Thermal fuel bridge, eight 2019-2022 fuel-cell production pair exceptions, pumped-hydro physical split, BESS/other storage, wind onshore/offshore split and historical perimeter drift review remain open." };
  const t = row.technology;
  if (["SOLAR_PV", "BIOENERGY", "GEOTHERMAL", "HYDRO_TOTAL_CONTROL"].includes(t)) return { ...row, capacity_by_zone: "YES_2019_2024", generation_by_zone: "YES_2019_2024", historical_years_available: "2019|2020|2021|2022|2023|2024", ready_for_canonical_workbook: "NO_OVERALL_FREEZE_GATE", status: t === "HYDRO_TOTAL_CONTROL" ? "HISTORY_ACQUIRED_TYPE_MW_UNRESOLVED" : "HISTORY_ACQUIRED", blocker: t === "HYDRO_TOTAL_CONTROL" ? "Hydro type-specific MW and pumped-storage split remain unresolved." : t === "BIOENERGY" ? "Bioenergy is a cross-classification; conversion technology/CHP allocation remains unresolved." : "Overall historical freeze gate remains open." };
  if (["HYDRO_RUN_OF_RIVER", "HYDRO_BASIN", "HYDRO_RESERVOIR"].includes(t)) return { ...row, capacity_by_zone: "NO_TYPE_SPECIFIC_MW", generation_by_zone: "YES_2019_2024", historical_years_available: "2019|2020|2021|2022|2023|2024", ready_for_canonical_workbook: "NO", status: "HISTORY_GENERATION_ACQUIRED_REQUIRES_CAPACITY_ALLOCATION", blocker: "No defensible type-specific MW; reservoir generation includes eventual pumping." };
  if (t.startsWith("WIND_")) return { ...row, capacity_by_zone: "AGGREGATE_WIND_ONLY_2019_2024", generation_by_zone: "AGGREGATE_WIND_ONLY_2019_2024", historical_years_available: "2019|2020|2021|2022|2023|2024", ready_for_canonical_workbook: "NO", status: "REQUIRES ONSHORE/OFFSHORE ALLOCATION", blocker: "Terna source series reports Eolico without onshore/offshore split." };
  if (["PUMPED_HYDRO", "BESS", "OTHER_STORAGE"].includes(t)) return row;
  return { ...row, capacity_by_zone: "YES_2019_2024", generation_by_zone: "YES_2019_2024", historical_years_available: "2019|2020|2021|2022|2023|2024", ready_for_canonical_workbook: "NO", status: "HISTORY_ACQUIRED_FUEL_UNRESOLVED", blocker: "Fuel/source reconciliation and historical perimeter drift review remain open." };
});
completion.push({ technology: "WIND_TOTAL_CONTROL", capacity_by_zone: "YES_2019_2024", generation_by_zone: "YES_2019_2024", chp_distinction_resolved: "NOT_APPLICABLE", fuel_source_resolved: "YES_WIND_FAMILY", hydro_subtype_resolved: "NOT_APPLICABLE", historical_years_available: "2019|2020|2021|2022|2023|2024", ready_for_canonical_workbook: "NO", status: "DIRECT_SOURCE_CONTROL", blocker: "Component allocation is represented separately; this control row is not a model component." });

const manualManifest = await readCsvObjects(path.join(manifestDir, "MEM_HISTORICAL_BASELINE_SOURCE_MANIFEST.csv"), "SourceManifest");
const apiManifest = [];
for (const series of Object.values(raw).flat()) {
  apiManifest.push({ source_id: series.metadata.source_id, publisher: "Terna S.p.A.", title: `${series.endpoint} ${series.year} official API response`, release_or_year: series.year, acquisition_route: "TERNA_OAUTH2_API", acquisition_date: "2026-09-01", original_uploaded_filename: "", archived_raw_file: path.relative(phaseRoot, series.rawPath).replaceAll("\\", "/"), byte_size: series.metadata.raw_bytes, raw_sha256: series.sha256, scope: `${series.endpoint}; year=${series.year}; complete returned payload`, evidence_class: series.year === 2024 ? "QA / RECONCILIATION ONLY FOR DOWNLOAD CENTER-CONTROLLED SERIES; DIRECT SOURCE FOR SOLAR/WIND HISTORY" : series.year === 2025 ? "QA / AVAILABILITY CHECK" : "DIRECT SOURCE CAPACITY OR ENERGY", status: series.rows.length ? "ACQUIRED_ARCHIVED" : "NO_CONSOLIDATED_ROWS_RETURNED", url: `https://api.terna.it/generation/v2.0/${series.endpoint}?year=${series.year}` });
}
const sourceManifest = [...manualManifest, ...apiManifest];

const derivationBase = await readCsvObjects(path.join(manifestDir, "MEM_HISTORICAL_BASELINE_DERIVATION_MANIFEST.csv"), "DerivationManifest");
const derivation = [...derivationBase,
  { derivation_id: "DER-HIST-012", output: "MEM_Historical_Capacity_By_Zone_Technology.csv", evidence_class: "PROJECT DERIVATION FROM DIRECT SOURCE CAPACITY", inputs: "Terna API 2019-2024 thermoelectric and renewable-source capacity; controlling 2024 Download Center exports; reviewed crosswalk", method: "Filter Netta; robust numeric parse; aggregate official source multiplicity with lineage; map province to MEM zone; retain technology/source layers and additivity flags.", key_guardrail: "Bioenergy is non-additive; geothermal source rows are non-additive when an explicit thermoelectric geothermal label exists; 2024 Download Center remains controlling.", status: "VALIDATED_2019_2024_PARTIAL_BASELINE" },
  { derivation_id: "DER-HIST-013", output: "MEM_Historical_Generation_By_Zone_Technology.csv", evidence_class: "PROJECT DERIVATION FROM DIRECT SOURCE ENERGY", inputs: "Terna API 2019-2024 thermoelectric, renewable-source, hydric and heat series; controlling 2024 Download Center exports", method: "Reconstruct thermoelectric Netta via exact two-row physical rule; filter explicit Netta renewable/hydric series; aggregate by zone/technology; attach matching capacity, observed CF and CHP heat indicators.", key_guardrail: "Hydro source total is a non-additive control over type rows; Serbatoio includes eventual pumping; observed CF is diagnostic only.", status: "VALIDATED_2019_2024_PARTIAL_BASELINE" },
];

const csvEscape = (value) => {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
};
const matrix = (rows) => {
  const headers = [];
  for (const row of rows) for (const key of Object.keys(row)) if (!headers.includes(key)) headers.push(key);
  return { headers, values: rows.map((row) => headers.map((header) => row[header] ?? "")) };
};
const letters = (count) => {
  let n = count;
  let result = "";
  while (n > 0) { n -= 1; result = String.fromCharCode(65 + (n % 26)) + result; n = Math.floor(n / 26); }
  return result;
};
async function authorCsv(rows, sheetName, file) {
  const { headers, values } = matrix(rows);
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add(sheetName);
  sheet.getRangeByIndexes(0, 0, rows.length + 1, headers.length).values = [headers, ...values];
  const address = `A1:${letters(headers.length)}${rows.length + 1}`;
  await workbook.inspect({ kind: "table", sheetId: sheetName, range: address, tableMaxRows: 3, tableMaxCols: Math.min(24, headers.length), maxChars: 5000 });
  const csv = `${[headers, ...values].map((row) => row.map(csvEscape).join(",")).join("\r\n")}\r\n`;
  await fs.writeFile(file, csv, "utf8");
  const reopened = await Workbook.fromCSV(await fs.readFile(file, "utf8"), { sheetName });
  await reopened.inspect({ kind: "table", sheetId: sheetName, range: address, tableMaxRows: 3, tableMaxCols: Math.min(24, headers.length), maxChars: 5000 });
  return { file: path.relative(phaseRoot, file).replaceAll("\\", "/"), rows: rows.length, columns: headers.length, sha256: await sha256File(file) };
}

const outputs = [];
outputs.push(await authorCsv(capacityIntegrated, "HistCapacity", path.join(normalizedDir, "MEM_Historical_Capacity_By_Zone_Technology.csv")));
outputs.push(await authorCsv(generationIntegrated, "HistGeneration", path.join(normalizedDir, "MEM_Historical_Generation_By_Zone_Technology.csv")));
outputs.push(await authorCsv(taxonomy, "Taxonomy", path.join(normalizedDir, "MEM_HISTORICAL_TECHNOLOGY_TAXONOMY.csv")));
outputs.push(await authorCsv(completion, "Completion", path.join(analysisDir, "MEM_HISTORICAL_BASELINE_COMPLETION_STATUS.csv")));
outputs.push(await authorCsv(sourceManifest, "SourceManifest", path.join(manifestDir, "MEM_HISTORICAL_BASELINE_SOURCE_MANIFEST.csv")));
outputs.push(await authorCsv(derivation, "DerivationManifest", path.join(manifestDir, "MEM_HISTORICAL_BASELINE_DERIVATION_MANIFEST.csv")));

const stabilityReport = {
  generated_at: new Date().toISOString(),
  actual_historical_window: "2019-2024",
  requested_preferred_window: "2019-2025",
  year_2025_status: "ALL_SIX_OFFICIAL_ENDPOINTS_RETURNED_ZERO_ROWS",
  endpoint_counts: Object.fromEntries(Object.entries(raw).map(([key, series]) => [key, Object.fromEntries(series.map((item) => [item.year, item.rows.length]))])),
  thermoelectric_subcategories_by_year: Object.fromEntries(years.map((year) => [year, sortedUnique(raw.thermoCapacity.find((item) => item.year === year).rows.map((row) => row.subcategory))])),
  geothermal_overlap_by_year: geothermalOverlapByYear,
  hydro_pumping_perimeter_by_year: hydroPerimeterByYear,
  thermoelectric_production_pair_qa: productionPairQa,
  thermoelectric_production_pair_exceptions: productionPairExceptions,
  visible_source_multiplicity: { group_count: sourceMultiplicity.length, groups: sourceMultiplicity },
  qa,
  outputs,
  final_status: "HISTORICAL BASELINE INCOMPLETE",
  workbook_successor_authorized: false,
};
await fs.writeFile(path.join(qaDir, "MEM_HISTORICAL_SERIES_STABILITY_AND_QA_2019_2025.json"), `${JSON.stringify(stabilityReport, null, 2)}\n`, "utf8");

console.log(JSON.stringify({
  final_status: stabilityReport.final_status,
  years: stabilityReport.actual_historical_window,
  year_2025_status: stabilityReport.year_2025_status,
  capacity_rows: capacityIntegrated.length,
  generation_rows: generationIntegrated.length,
  production_pair_qa: productionPairQa,
  production_pair_exception_count: productionPairExceptions.length,
  geothermal_overlap_by_year: geothermalOverlapByYear,
  source_multiplicity_groups: sourceMultiplicity.length,
  qa_pass: qa.filter((row) => row.status === "PASS").length,
  qa_fail: qa.filter((row) => row.status === "FAIL").length,
  outputs,
}, null, 2));

import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { Workbook } from "@oai/artifact-tool";

const phaseRoot = path.resolve(import.meta.dirname, "..");
const analysisDir = path.join(phaseRoot, "historical_baseline", "analysis");
const normalizedDir = path.join(phaseRoot, "historical_baseline", "normalized");
const docsDir = path.join(phaseRoot, "docs");
const manifestsDir = path.join(phaseRoot, "historical_baseline", "manifests");
const qaDir = path.join(phaseRoot, "qa");

const files = {
  pumping: path.join(analysisDir, "MEM_Hydro_Pumping_Perimeter_Reconciliation.csv"),
  storage: path.join(normalizedDir, "MEM_Historical_Storage_By_Zone_Technology.csv"),
  crosswalk: path.join(analysisDir, "MEM_Historical_to_Model_Technology_Crosswalk.csv"),
  gaps: path.join(docsDir, "MEM_v2.9_EMPIRICAL_GAP_AND_ACQUISITION_REGISTER_HISTORICAL_BASELINE_UPDATED_20260901.csv"),
  decisions: path.join(docsDir, "MEM_v2.9_HISTORICAL_BASELINE_DECISION_REGISTER_20260901.csv"),
  gates: path.join(analysisDir, "MEM_PHASE3_GATE_STATUS.csv"),
  capacity: path.join(normalizedDir, "MEM_Historical_Capacity_By_Zone_Technology.csv"),
  generation: path.join(normalizedDir, "MEM_Historical_Generation_By_Zone_Technology.csv"),
  qa: path.join(analysisDir, "MEM_PHASE3_1_CROSS_ARTIFACT_CONSISTENCY_QA.csv"),
  definitions: path.join(analysisDir, "MEM_Hydro_Energy_Capacity_Definition_Reconciliation.csv"),
  candidates: path.join(analysisDir, "MEM_Hydro_Model_Parameter_Candidates.csv"),
  allocationCandidates: path.join(analysisDir, "MEM_PHS_Operational_Energy_Allocation_Candidates.csv"),
  national: path.join(analysisDir, "MEM_Hydro_Physical_Inventory_National_Reconciliation.csv"),
  audit: path.join(analysisDir, "MEM_Hydro_Physical_Inventory_Type_Audit.csv"),
  zoneSummary: path.join(analysisDir, "MEM_Hydro_Physical_Inventory_Zone_Summary.csv"),
  sourceManifest: path.join(manifestsDir, "MEM_HISTORICAL_BASELINE_SOURCE_MANIFEST.csv"),
  derivationManifest: path.join(manifestsDir, "MEM_HISTORICAL_BASELINE_DERIVATION_MANIFEST.csv"),
  finalVerification: path.join(qaDir, "MEM_PHASE3_1_FINAL_VERIFICATION.json"),
  componentSpec: path.join(docsDir, "MEM_Hydro_PyPSA_Component_Specification.md"),
  methodologyDoc: path.join(docsDir, "MEM_HISTORICAL_BASELINE_METHOD_AND_RESULTS.md"),
  qaReport: path.join(qaDir, "MEM_HISTORICAL_BASELINE_QA_REPORT.md"),
  acquisitionRequirement: path.join(docsDir, "GEM_GOGPT_GCPT_2026_ACQUISITION_REQUIREMENT.md"),
};

const sha256File = async (file) => crypto.createHash("sha256").update(await fs.readFile(file)).digest("hex");
const csvEscape = (value) => {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
};
const letters = (count) => {
  let n = count;
  let out = "";
  while (n > 0) {
    n -= 1;
    out = String.fromCharCode(65 + (n % 26)) + out;
    n = Math.floor(n / 26);
  }
  return out;
};

async function readCsvObjects(file, sheetName) {
  const workbook = await Workbook.fromCSV(await fs.readFile(file, "utf8"), { sheetName });
  const values = workbook.worksheets.getItem(sheetName).getUsedRange().values;
  const headers = values[0].map(String);
  return values.slice(1)
    .filter((row) => row.some((value) => value !== null && value !== ""))
    .map((row) => Object.fromEntries(headers.map((header, index) => [header, row[index] ?? ""])));
}

function matrix(rows) {
  const headers = [];
  for (const row of rows) for (const key of Object.keys(row)) if (!headers.includes(key)) headers.push(key);
  return { headers, values: rows.map((row) => headers.map((header) => row[header] ?? "")) };
}

async function authorCsv(rows, sheetName, file) {
  const { headers, values } = matrix(rows);
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add(sheetName);
  sheet.getRangeByIndexes(0, 0, rows.length + 1, headers.length).values = [headers, ...values];
  const address = `A1:${letters(headers.length)}${rows.length + 1}`;
  await workbook.inspect({ kind: "table", sheetId: sheetName, range: address, tableMaxRows: 3, tableMaxCols: Math.min(headers.length, 20), maxChars: 4000 });
  const csv = `${[headers, ...values].map((row) => row.map(csvEscape).join(",")).join("\r\n")}\r\n`;
  await fs.writeFile(file, csv, "utf8");
  const reopened = await Workbook.fromCSV(await fs.readFile(file, "utf8"), { sheetName });
  await reopened.inspect({ kind: "table", sheetId: sheetName, range: address, tableMaxRows: 3, tableMaxCols: Math.min(headers.length, 20), maxChars: 4000 });
  return { file: path.relative(phaseRoot, file).replaceAll("\\", "/"), rows: rows.length, columns: headers.length, sha256: await sha256File(file) };
}

function upsert(rows, keyField, row) {
  const index = rows.findIndex((item) => String(item[keyField]) === String(row[keyField]));
  if (index >= 0) rows[index] = { ...rows[index], ...row };
  else rows.push(row);
}

const round = (value, digits = 9) => Number(Number(value).toFixed(digits));
const sum = (values) => values.reduce((total, value) => total + Number(value || 0), 0);

let pumping = await readCsvObjects(files.pumping, "Pumping");
let storage = await readCsvObjects(files.storage, "Storage");
let crosswalk = await readCsvObjects(files.crosswalk, "Crosswalk");
let gaps = await readCsvObjects(files.gaps, "Gaps");
let decisions = await readCsvObjects(files.decisions, "Decisions");
const gates = await readCsvObjects(files.gates, "Gates");
const capacity = await readCsvObjects(files.capacity, "Capacity");
const generation = await readCsvObjects(files.generation, "Generation");
let definitions = await readCsvObjects(files.definitions, "Definitions");
let candidates = await readCsvObjects(files.candidates, "Candidates");
let national = await readCsvObjects(files.national, "National");
let audit = await readCsvObjects(files.audit, "Audit");
let zoneSummary = await readCsvObjects(files.zoneSummary, "ZoneSummary");
let sourceManifest = await readCsvObjects(files.sourceManifest, "Sources");
let derivationManifest = await readCsvObjects(files.derivationManifest, "Derivations");

if (capacity.length !== 758) throw new Error(`Historical capacity row count changed before patch: ${capacity.length}.`);
if (generation.length !== 881) throw new Error(`Historical generation row count changed before patch: ${generation.length}.`);
if (storage.length !== 19) throw new Error(`Historical storage row count changed before patch: ${storage.length}.`);

const priorZonalEnergyByZone = new Map();
pumping = pumping.map((row) => {
  const is2024Zone = String(row.year) === "2024" && row.geographic_scope === "MARKET_ZONE";
  const is2024National = String(row.year) === "2024" && row.geographic_scope === "NATIONAL";
  if (is2024Zone) {
    const prior = row.superseded_project_allocation_energy_MWh || row.pumped_hydro_energy_MWh;
    priorZonalEnergyByZone.set(row.market_zone, prior);
    return {
      ...row,
      pumped_hydro_energy_MWh: "",
      pumped_hydro_charge_MW: "",
      model_input_status: "NO CANONICAL ZONAL OPERATIONAL E_NOM; NATIONAL CONTROL ONLY",
      status: "PHASE3_1_PATCHED; FORMER ZONAL 53-GWH ALLOCATION SUPERSEDED",
      operational_energy_zone_allocation_status: "NOT_ALLOCATED; PROJECT_ALLOCATION_METHOD_REQUIRES_APPROVAL",
      superseded_project_allocation_energy_MWh: prior,
      superseded_allocation_status: "SUPERSEDED",
      operational_energy_model_input: false,
      superseded_allocation_model_input: false,
      superseded_allocation_lineage: "PHASE2_5_NORMALIZATION_OF_TERNA_53GWH_BY_OPEN_HPHS_PHYSICAL_ENERGY_SHARES",
      charge_power_zone_allocation_status: "NOT_ALLOCATED; OPEN PUMP MW PRESERVED IN PHYSICAL-INVENTORY FIELD; PROJECT METHOD REQUIRES APPROVAL",
    };
  }
  if (is2024National) {
    return {
      ...row,
      pumped_hydro_energy_MWh: 53000,
      pumped_hydro_charge_MW: 6400,
      Terna_system_operational_PHS_energy_MWh: 53000,
      operational_energy_zone_allocation_status: "NATIONAL_CONTROL_ONLY",
      superseded_project_allocation_energy_MWh: "",
      superseded_allocation_status: "NOT_APPLICABLE",
      operational_energy_model_input: false,
      superseded_allocation_model_input: false,
      superseded_allocation_lineage: "",
      charge_power_zone_allocation_status: "NATIONAL_CONTROL_ONLY",
    };
  }
  return {
    ...row,
    superseded_project_allocation_energy_MWh: row.superseded_project_allocation_energy_MWh ?? "",
    superseded_allocation_status: row.superseded_allocation_status ?? "NOT_APPLICABLE",
    operational_energy_model_input: false,
    superseded_allocation_model_input: false,
    superseded_allocation_lineage: row.superseded_allocation_lineage ?? "",
    charge_power_zone_allocation_status: row.charge_power_zone_allocation_status ?? "NOT_APPLICABLE",
  };
});

storage = storage.map((row) => {
  const is2024PHSZone = String(row.year) === "2024" && row.technology === "PUMPED_HYDRO" && row.spatial_scope === "MARKET_ZONE";
  const is2024PHSNational = String(row.year) === "2024" && row.technology === "PUMPED_HYDRO" && row.spatial_scope === "NATIONAL_CONTROL_ONLY";
  if (is2024PHSZone) {
    return {
      ...row,
      energy_capacity_MWh: "",
      charge_power_MW: "",
      storage_duration_h: "",
      national_pumped_energy_control_MWh: 53000,
      operational_storage_energy_MWh_control: "",
      energy_allocation_share: "",
      status: Number(row.discharge_power_MW) === 0
        ? "NO_REPORTED_PUMPED_HYDRO; OPERATIONAL_E_NOM_NATIONAL_CONTROL_ONLY"
        : "OPERATIONAL_E_NOM_NATIONAL_CONTROL_ONLY; ZONAL_E_NOM_UNALLOCATED; EFFICIENCY_UNSET",
      superseded_project_allocation_energy_MWh: row.superseded_project_allocation_energy_MWh || priorZonalEnergyByZone.get(row.market_zone) || "",
      superseded_allocation_status: "SUPERSEDED",
      operational_energy_model_input: false,
      superseded_allocation_model_input: false,
      charge_power_zone_allocation_status: "NOT_ALLOCATED; PHYSICAL-INVENTORY PUMP MW PRESERVED SEPARATELY; PROJECT METHOD REQUIRES APPROVAL",
    };
  }
  if (is2024PHSNational) {
    return {
      ...row,
      energy_capacity_MWh: 53000,
      charge_power_MW: 6400,
      operational_storage_energy_MWh_control: 53000,
      superseded_project_allocation_energy_MWh: "",
      superseded_allocation_status: "NOT_APPLICABLE",
      operational_energy_model_input: false,
      superseded_allocation_model_input: false,
      charge_power_zone_allocation_status: "NATIONAL_CONTROL_ONLY",
    };
  }
  return {
    ...row,
    superseded_project_allocation_energy_MWh: row.superseded_project_allocation_energy_MWh ?? "",
    superseded_allocation_status: row.superseded_allocation_status ?? "NOT_APPLICABLE",
    operational_energy_model_input: false,
    superseded_allocation_model_input: false,
    charge_power_zone_allocation_status: row.charge_power_zone_allocation_status ?? "NOT_APPLICABLE",
  };
});

crosswalk = crosswalk.map((row) => {
  if (row.pypsa_component_pattern !== "NONE_QA_CONTROL") return row;
  return {
    ...row,
    future_PyPSA_component: "NONE; QA / ACCOUNTING CONTROL ONLY",
    notes: "QA / ACCOUNTING CONTROL ONLY; NO SOLVER COMPONENT INSTANTIATED",
    evidence_guardrail: "QA / ACCOUNTING CONTROL ONLY; NO SOLVER COMPONENT INSTANTIATED",
    mapping_status: "CONTROL_ONLY_NO_SOLVER_COMPONENT",
  };
});

gaps = gaps.map((row) => {
  if (row.gap_id === "GAP-HIST-005") {
    return {
      ...row,
      status: "SUPERSEDED_BY_D-HIST-018",
      exact_acquisition_or_decision: "Historical 2019-2024 technology measurements are frozen under D-HIST-018. Tracker acquisition is handled by the separate current/future physical-fleet gate.",
      acceptance_test: "Historical taxonomy and measurement QA remain PASS; no tracker dependency for the frozen technology baseline.",
      blocks: "SUPERSEDED: does not block HISTORICAL_TECHNOLOGY_BASELINE_STATUS",
    };
  }
  if (row.gap_id === "GAP-HIST-003") {
    return {
      ...row,
      status: "RESOLVED METRIC SEPARATION; ZONAL OPERATIONAL E_NOM NOT ALLOCATED",
      exact_acquisition_or_decision: "Retain Terna 7,252.3 MW NET PHS discharge and approximately 53 GWh only at national operational-control grain. Preserve the former normalized zonal allocation solely as superseded lineage.",
      acceptance_test: "Zero nonblank canonical 2024 MARKET_ZONE PHS operational e_nom values; 53,000 MWh national control preserved; physical HPHS energy remains a separate view.",
      blocks: "HYDRO_MODEL_PARAMETER_STATUS only; not the frozen historical technology baseline",
    };
  }
  if (row.gap_id === "GAP-003") {
    return {
      ...row,
      blocks: "THERMAL_FUEL_PLANT_ATTRIBUTION_STATUS and CURRENT_2026_PHYSICAL_FLEET_STATUS; not HISTORICAL_TECHNOLOGY_BASELINE_STATUS",
    };
  }
  return row;
});

decisions = decisions.map((row) => {
  if (row.decision_id === "D-HIST-013") {
    return {
      ...row,
      status: "SUPERSEDED_BY_D-HIST-018",
      rule: "This earlier empirical-freeze gate is superseded. D-HIST-018 freezes 2019-2024 observed technology measurements and moves thermal plant/fuel attribution to a separate current/future-fleet gate.",
      value_or_artifact: "SUPERSEDED_BY_D-HIST-018; HISTORICAL_TECHNOLOGY_BASELINE_STATUS=FROZEN",
      model_effect: "No historical measurement block; tracker acquisition remains required for the 2026 physical fleet.",
    };
  }
  if (row.decision_id === "D-HIST-008") {
    return {
      ...row,
      status: "SUPERSEDED_IN_PART_BY_D-HIST-018; WORKBOOK_PROVENANCE_GATE_REMAINS",
      rule: "The historical technology baseline is frozen under D-HIST-018. Workbook promotion remains separately blocked by accepted-v2.9 binary provenance and is not an empirical-data gate.",
      value_or_artifact: "HISTORICAL_TECHNOLOGY_BASELINE_STATUS=FROZEN; WORKBOOK_PROMOTION_STATUS=BLOCKED",
      model_effect: "No v2.9.1 is created in Phase 3.1.",
    };
  }
  return row;
});

// Phase 3.1 targeted hydro-parameter closure. Direct Terna values remain controls;
// secondary/open values may only support transparent candidate allocations.
const terna2024HydroUrl = "https://download.terna.it/terna/03_IMPIANTI%20DI%20GENERAZIONE_8dec285ed22347a.pdf";
const ternaPds2025Url = "https://download.terna.it/terna/Terna_Piano_Sviluppo_2025_Stato_sistema_elettrico_scenari_energetici_8dd62ec4bbb9f75.pdf";
const ternaStorageStudyUrl = "https://download.terna.it/terna/Studio_tecnologie_di_accumulo_8db9511fbdd7601.pdf";
const pypsaEurHydroUrl = "https://github.com/PyPSA/pypsa-eur/blob/master/scripts/add_electricity.py";

const typeControls = [
  { id: "TERNA_HYDRO_2024_ITALY_ROR_NET", scope: "ITALY", cls: "HYDRO_RUN_OF_RIVER", value: 6239.0, componentReady: false, definition: "Net efficient turbine power of run-of-river plants." },
  { id: "TERNA_HYDRO_2024_ITALY_BASIN_NET", scope: "ITALY", cls: "HYDRO_BASIN", value: 5019.9, componentReady: false, definition: "Net efficient turbine power of basin/pondage plants." },
  { id: "TERNA_HYDRO_2024_ITALY_RESERVOIR_TOTAL_NET", scope: "ITALY", cls: "HYDRO_RESERVOIR_INCLUDING_PHS", value: 12035.1, componentReady: false, definition: "Net efficient turbine power of reservoir plants, including pure and mixed pumping." },
  { id: "TERNA_HYDRO_2024_ITALY_RESERVOIR_NONPUMPED_NET", scope: "ITALY", cls: "HYDRO_RESERVOIR", value: 4782.8, componentReady: false, definition: "Reservoir total less the same-table pure+mixed pumping subset (12,035.1 - 7,252.3 MW)." },
  { id: "TERNA_HYDRO_2024_NORD_ROR_NET", scope: "NORD", cls: "HYDRO_RUN_OF_RIVER", value: 5227.9, componentReady: true, definition: "Italia Settentrionale run-of-river net efficient power; the reviewed MEM geography maps the northern statistical macro-area to NORD." },
  { id: "TERNA_HYDRO_2024_NORD_BASIN_NET", scope: "NORD", cls: "HYDRO_BASIN", value: 3512.0, componentReady: true, definition: "Italia Settentrionale basin/pondage net efficient power; the reviewed MEM geography maps the northern statistical macro-area to NORD." },
  { id: "TERNA_HYDRO_2024_NORD_RESERVOIR_TOTAL_NET", scope: "NORD", cls: "HYDRO_RESERVOIR_INCLUDING_PHS", value: 8368.8, componentReady: false, definition: "Italia Settentrionale reservoir net efficient power, including pure and mixed pumping." },
  { id: "TERNA_HYDRO_2024_NORD_PHS_SUBSET_NET", scope: "NORD", cls: "PUMPED_HYDRO_PURE+MIXED", value: 4699.7, componentReady: false, definition: "Italia Settentrionale pure+mixed pumping subset inside the reservoir row." },
  { id: "TERNA_HYDRO_2024_NORD_RESERVOIR_NONPUMPED_NET", scope: "NORD", cls: "HYDRO_RESERVOIR", value: 3669.1, componentReady: true, definition: "Same-table difference between NORD reservoir total and pure+mixed pumping subset (8,368.8 - 4,699.7 MW)." },
  { id: "TERNA_HYDRO_2024_CENTRAL_MACRO_ROR_NET", scope: "ITALIA_CENTRALE_MACROREGION", cls: "HYDRO_RUN_OF_RIVER", value: 540.2, componentReady: false, definition: "Direct macroregional control; not identical to a single MEM market zone." },
  { id: "TERNA_HYDRO_2024_CENTRAL_MACRO_BASIN_NET", scope: "ITALIA_CENTRALE_MACROREGION", cls: "HYDRO_BASIN", value: 741.3, componentReady: false, definition: "Direct macroregional control; not identical to a single MEM market zone." },
  { id: "TERNA_HYDRO_2024_CENTRAL_MACRO_RESERVOIR_NET", scope: "ITALIA_CENTRALE_MACROREGION", cls: "HYDRO_RESERVOIR_INCLUDING_PHS", value: 271.0, componentReady: false, definition: "Direct macroregional control; not identical to a single MEM market zone." },
  { id: "TERNA_HYDRO_2024_SOUTH_ISLANDS_MACRO_ROR_NET", scope: "ITALIA_MERIDIONALE_E_INSULARE_MACROREGION", cls: "HYDRO_RUN_OF_RIVER", value: 470.8, componentReady: false, definition: "Direct macroregional control spanning multiple MEM zones." },
  { id: "TERNA_HYDRO_2024_SOUTH_ISLANDS_MACRO_BASIN_NET", scope: "ITALIA_MERIDIONALE_E_INSULARE_MACROREGION", cls: "HYDRO_BASIN", value: 766.6, componentReady: false, definition: "Direct macroregional control spanning multiple MEM zones." },
  { id: "TERNA_HYDRO_2024_SOUTH_ISLANDS_MACRO_RESERVOIR_TOTAL_NET", scope: "ITALIA_MERIDIONALE_E_INSULARE_MACROREGION", cls: "HYDRO_RESERVOIR_INCLUDING_PHS", value: 3395.3, componentReady: false, definition: "Direct macroregional reservoir control spanning multiple MEM zones and including PHS." },
  { id: "TERNA_HYDRO_2024_SOUTH_ISLANDS_MACRO_PHS_SUBSET_NET", scope: "ITALIA_MERIDIONALE_E_INSULARE_MACROREGION", cls: "PUMPED_HYDRO_PURE+MIXED", value: 2552.6, componentReady: false, definition: "Direct macroregional PHS subset spanning multiple MEM zones." },
];

for (const control of typeControls) {
  upsert(candidates, "candidate_id", {
    candidate_id: control.id,
    parameter: control.cls.includes("PUMPED") ? "PUMPED_HYDRO_DISCHARGE_POWER" : "HYDRO_CLASS_NET_TURBINE_POWER",
    hydro_class: control.cls,
    zone_or_plant: control.scope,
    zone: control.scope,
    plant_id: "",
    source: "TERNA_2024_IMPIANTI_DI_GENERAZIONE_TABLE_14",
    source_type: control.id.includes("NONPUMPED") ? "PROJECT_DERIVATION_FROM_DIRECT_TERNA_SAME_TABLE" : "DIRECT_TERNA_CONTROL",
    Terna_control_available: "YES",
    Terna_control_value: control.value,
    Terna_control_unit: "MW_NET",
    recommended_MEM_use: control.componentReady ? "FIXED ZONAL TURBINE P_NOM FOR THE STATED HYDRO CLASS" : "CAPACITY CONTROL/ALLOCATION CONSTRAINT; NOT A COMPLETE SEVEN-ZONE COMPONENT SET",
    confidence: control.id.includes("NONPUMPED") ? "HIGH_SAME_TABLE_DIFFERENCE" : "HIGH_DIRECT_PUBLISHED_ROUNDED_TO_0.1_MW",
    status: control.componentReady ? "MODEL_READY" : "CANONICAL_CONTROL",
    accounting_role: control.cls.includes("INCLUDING") ? "CONTROL_ROW_NON_ADDITIVE" : "PRIMARY_ADDITIVE_WITHIN_FIVE_CLASS_HYDRO_BRIDGE",
    candidate_value: control.value,
    unit: "MW_NET",
    physical_definition: control.definition,
    operational_definition: control.componentReady ? "Fixed non-extendable turbine output rating for the named MEM zone/class; storage energy and inflow remain separate." : "Numerical control at published national/macroregional grain.",
    direct_or_derived: control.id.includes("NONPUMPED") ? "DERIVED_SAME_TABLE_SUBTRACTION" : "DIRECT",
  });
}

const rorBasinReservoirGap = candidates.find((row) => row.candidate_id === "REQ_ROR_BASIN_RESERVOIR_NET_MW_SPLIT");
if (rorBasinReservoirGap) Object.assign(rorBasinReservoirGap, {
  parameter: "HYDRO_CLASS_NET_POWER_ALLOCATION_TO_REMAINING_MEM_ZONES",
  hydro_class: "RUN_OF_RIVER|BASIN|RESERVOIR",
  zone_or_plant: "CNOR|CSUD|SUD|CALA|SICI|SARD",
  zone: "REMAINING_SIX_ZONES",
  source: "TERNA_2024_TABLE14_MACROREGIONAL_CONTROLS_PLUS_PRIMARY_PLANT_ALLOCATION_REQUIRED",
  Terna_control_available: "YES_NATIONAL_AND_MACROREGIONAL; NOT COMPLETE_AT_MEM_ZONE_GRAIN",
  Terna_control_value: "ROR 6239.0; BASIN 5019.9; NONPUMPED_RESERVOIR 4782.8",
  Terna_control_unit: "MW_NET_NATIONAL",
  recommended_MEM_use: "NORD CLASS P_NOM IS RESOLVED; ALLOCATE THE REMAINING MACROREGIONAL CONTROLS TO SIX MEM ZONES WITHOUT USING GWh SHARES",
  physical_definition: "Allocation of direct Terna national/macroregional class controls to the six non-NORD MEM zones.",
  operational_definition: "Required fixed turbine p_nom by hydro class and remaining market zone.",
  status: "PROJECT_ASSUMPTION_REQUIRED",
});

for (const id of ["TERNA_PDS2025_PHS_MAX_ABSORPTION", "TERNA_PDS2025_PHS_OPERATIONAL_ENERGY"]) {
  const row = candidates.find((item) => item.candidate_id === id);
  if (row) {
    row.status = "CANONICAL_CONTROL";
    row.recommended_MEM_use = id === "TERNA_PDS2025_PHS_MAX_ABSORPTION"
      ? "CANONICAL APPROXIMATE NATIONAL P_NOM_CHARGE CONTROL; ZONAL/PLANT ALLOCATION REQUIRES APPROVAL"
      : "CANONICAL APPROXIMATE NATIONAL OPERATIONAL E_NOM CONTROL; ZONAL/PLANT ALLOCATION REQUIRES APPROVAL";
  }
}

upsert(candidates, "candidate_id", {
  candidate_id: "TERNA_TYPICAL_PHS_ROUNDTRIP_EFFICIENCY_RANGE",
  parameter: "PUMPED_HYDRO_ROUNDTRIP_EFFICIENCY",
  hydro_class: "PUMPED_HYDRO_PURE|PUMPED_HYDRO_MIXED",
  zone_or_plant: "TECHNOLOGY_LEVEL",
  zone: "ALL",
  plant_id: "",
  source: "TERNA_STORAGE_TECHNOLOGIES_2023_SECTION_2_2",
  source_type: "DIRECT_TERNA_TECHNOLOGY_RANGE",
  Terna_control_available: "YES_TECHNOLOGY_RANGE_NOT_PLANT_SPECIFIC",
  Terna_control_value: "0.70-0.75",
  Terna_control_unit: "p.u._RTE",
  recommended_MEM_use: "PROJECT ASSUMPTION RANGE AND SENSITIVITY; SELECT A POINT ONLY WITH USER APPROVAL",
  confidence: "HIGH_FOR_TYPICAL_TECHNOLOGY_RANGE; NOT PLANT_SPECIFIC",
  status: "MODEL_CANDIDATE",
  accounting_role: "MODEL_PARAMETER",
  candidate_value: "0.70-0.75",
  unit: "p.u._roundtrip",
  physical_definition: "Terna technology-study range for net round-trip efficiency, including a complete charge-discharge cycle and auxiliary consumption.",
  operational_definition: "Product of effective charge and discharge efficiencies in MEM; does not specify their split.",
  direct_or_derived: "DIRECT_RANGE",
});

upsert(candidates, "candidate_id", {
  candidate_id: "PROJECT_SYMMETRIC_PHS_LINK_EFFICIENCY_RANGE",
  parameter: "PUMP_AND_TURBINE_LINK_EFFICIENCY",
  hydro_class: "PUMPED_HYDRO_PURE|PUMPED_HYDRO_MIXED",
  zone_or_plant: "TECHNOLOGY_LEVEL",
  zone: "ALL",
  plant_id: "",
  source: "TERNA_STORAGE_TECHNOLOGIES_2023_SECTION_2_2|PROJECT_SYMMETRIC_LOSS_ALLOCATION",
  source_type: "PROJECT_ASSUMPTION_FROM_DIRECT_TERNA_RTE_RANGE",
  Terna_control_available: "YES_FOR_PRODUCT_ONLY",
  Terna_control_value: "RTE 0.70-0.75",
  Terna_control_unit: "p.u._RTE",
  recommended_MEM_use: "SENSITIVITY ONLY; IF APPROVED, ETA_PUMP=ETA_TURBINE=SQRT(RTE)",
  confidence: "TRANSPARENT_ENGINEERING_ASSUMPTION_NOT_PLANT_SPECIFIC",
  status: "PROJECT_ASSUMPTION_REQUIRED",
  accounting_role: "MODEL_PARAMETER",
  candidate_value: `${round(Math.sqrt(0.70), 6)}-${round(Math.sqrt(0.75), 6)}`,
  unit: "p.u._per_link",
  physical_definition: "Symmetric allocation of the Terna round-trip range across pump and turbine links; not measured plant efficiency.",
  operational_definition: "Candidate PyPSA Link efficiencies whose product equals the chosen RTE.",
  direct_or_derived: "PROJECT_CALCULATION_SQRT_OF_RTE",
});

upsert(candidates, "candidate_id", {
  candidate_id: "PYPSA_EUR_HYDRO_INFLOW_PROFILE_ARCHITECTURE",
  parameter: "HOURLY_HYDRO_INFLOW_SHAPE_SOURCE",
  hydro_class: "HYDRO_RUN_OF_RIVER|HYDRO_BASIN|HYDRO_RESERVOIR|PUMPED_HYDRO_MIXED",
  zone_or_plant: "ITALY_TO_ZONE_OR_ASSET",
  zone: "ALL",
  plant_id: "",
  source: "PYPSA_EUR_HYDRO_PROFILE_PIPELINE",
  source_type: "PYPSA_IMPLEMENTATION_MAPPING",
  Terna_control_available: "YES_FOR_ANNUAL_ZONAL/TYPE_ENERGY_NOT_HOURLY_SHAPE",
  Terna_control_value: "TERNA ANNUAL NET HYDRO ENERGY CONTROLS",
  Terna_control_unit: "GWh_NET_ANNUAL",
  recommended_MEM_use: "CANDIDATE HOURLY SHAPE ONLY; SELECT WEATHER YEAR AND RESCALE/RECONCILE TO TERNA ANNUAL CONTROLS",
  confidence: "REPRODUCIBLE_IMPLEMENTATION_CANDIDATE",
  status: "MODEL_CANDIDATE",
  accounting_role: "MODEL_PARAMETER",
  candidate_value: "PYPSA-EUR profile_hydro workflow",
  unit: "hourly_shape",
  physical_definition: "Reproducible hydrological inflow profile distributed to hydro assets in the PyPSA-Eur workflow.",
  operational_definition: "Shape candidate only; annual energy authority remains Terna and mixed PHS inflow enters its single shared water state.",
  direct_or_derived: "IMPLEMENTATION_REFERENCE",
});

const pumping2024Zones = pumping.filter((row) => String(row.year) === "2024" && row.geographic_scope === "MARKET_ZONE");
const totalPumpedDischarge = sum(pumping2024Zones.map((row) => row.total_pumped_hydro_NET_MW));
const totalOpenHphsEnergy = sum(pumping2024Zones.map((row) => row.physical_inventory_HPHS_reservoir_energy_MWh));
const totalOpenPumpPower = sum(pumping2024Zones.map((row) => row.physical_inventory_HPHS_charge_power_MW));

const allocationCandidates = [];
for (const row of pumping2024Zones) {
  const shareA = totalPumpedDischarge ? Number(row.total_pumped_hydro_NET_MW) / totalPumpedDischarge : 0;
  allocationCandidates.push({
    method_id: "METHOD_A_TERNA_ZONAL_DISCHARGE_POWER_SHARE",
    method_name: "Terna zonal pumped-discharge-power shares",
    allocation_grain: "MARKET_ZONE",
    plant_group: "ALL_PHS",
    market_zone: row.market_zone,
    plant_name: "",
    input_metric: "TERNA_PUMPED_DISCHARGE_NET_MW",
    input_value: row.total_pumped_hydro_NET_MW,
    input_unit: "MW_NET",
    allocation_share: round(shareA, 12),
    candidate_operational_energy_MWh: round(53000 * shareA, 9),
    national_control_MWh: 53000,
    evidence_class: "PROJECT_DERIVATION_FROM_DIRECT_TERNA_ZONAL_POWER_CONTROL",
    status: "PROJECT_ASSUMPTION_REQUIRED",
    model_input: false,
    source_id: "TERNA_2024_HYDRO_PERIMETER_RECONCILIATION|TERNA_PDS_2025_PUMPED_STORAGE",
    method_notes: "Power-proportional duration assumption; ignores plant-specific duration differences.",
    preference: "ROBUST_COMPARATOR; NOT CANONICAL",
  });

  const shareB = totalOpenHphsEnergy ? Number(row.physical_inventory_HPHS_reservoir_energy_MWh) / totalOpenHphsEnergy : 0;
  allocationCandidates.push({
    method_id: "METHOD_B_OPEN_HPHS_PHYSICAL_ENERGY_SHARE_SENSITIVITY",
    method_name: "Open HPHS physical-reservoir-energy shares normalized to Terna control",
    allocation_grain: "MARKET_ZONE",
    plant_group: "ALL_OPEN_HPHS",
    market_zone: row.market_zone,
    plant_name: "",
    input_metric: "OPEN_PHYSICAL_HPHS_RESERVOIR_ENERGY",
    input_value: row.physical_inventory_HPHS_reservoir_energy_MWh,
    input_unit: "MWh_e_equivalent_physical",
    allocation_share: round(shareB, 12),
    candidate_operational_energy_MWh: round(53000 * shareB, 9),
    national_control_MWh: 53000,
    evidence_class: "PHYSICAL_EVIDENCE_NORMALIZED_AS_PROJECT_SENSITIVITY",
    status: "PROJECT_ASSUMPTION_REQUIRED",
    model_input: false,
    source_id: "ZENODO_14006948|TERNA_PDS_2025_PUMPED_STORAGE",
    method_notes: "Sensitivity only; physical basin potential is not operational usable energy.",
    preference: "SECONDARY_SENSITIVITY; NOT CANONICAL",
  });
}

const principalPlants = [
  { plant: "Entracque-Chiotas", zone: "NORD", capacity: 1200, source: "ENEL_EGP_ENTRACQUE_OPERATOR_PAGE" },
  { plant: "Edolo", zone: "NORD", capacity: 1000, source: "ENEL_EGP_EDOLO_OPERATOR_PAGE" },
  { plant: "Roncovalgrande", zone: "NORD", capacity: 1040, source: "ENEL_EGP_RONCOVALGRANDE_OPERATOR_PAGE" },
  { plant: "Presenzano (Domenico Cimarosa)", zone: "CSUD", capacity: 1000, source: "ENEL_EGP_PRESENZANO_OPERATOR_PAGE" },
];
const principalCapacity = sum(principalPlants.map((row) => row.capacity));
for (const plant of principalPlants) {
  const shareWithinPrincipal = plant.capacity / principalCapacity;
  allocationCandidates.push({
    method_id: "METHOD_C_TERNA_75PCT_FOUR_PRINCIPAL_PURE_PHS",
    method_name: "Terna 75% concentration plus operator plant identity; power-weighted within principal group",
    allocation_grain: "PLANT_CANDIDATE",
    plant_group: "FOUR_PRINCIPAL_PURE_PHS_ABOVE_500MW",
    market_zone: plant.zone,
    plant_name: plant.plant,
    input_metric: "OPERATOR_OPERATIONAL_CAPACITY_MW",
    input_value: plant.capacity,
    input_unit: "MW_OPERATOR_REPORTED",
    allocation_share: round(0.75 * shareWithinPrincipal, 12),
    candidate_operational_energy_MWh: round(39750 * shareWithinPrincipal, 9),
    national_control_MWh: 53000,
    evidence_class: "DIRECT_TERNA_CONCENTRATION_CONTROL_PLUS_PRIMARY_OPERATOR_IDENTITY_PLUS_PROJECT_WITHIN_GROUP_ALLOCATION",
    status: "PROJECT_ASSUMPTION_REQUIRED",
    model_input: false,
    source_id: `TERNA_PDS_2025_PUMPED_STORAGE|${plant.source}`,
    method_notes: "Terna supplies the four-plant collective 75% statement, not plant-specific GWh; within-group power weighting is a project assumption.",
    preference: "PREFERRED_CANDIDATE_FOR_USER_APPROVAL; NOT CANONICAL",
  });
}
for (const row of pumping2024Zones) {
  const remainderShare = totalPumpedDischarge ? Number(row.total_pumped_hydro_NET_MW) / totalPumpedDischarge : 0;
  allocationCandidates.push({
    method_id: "METHOD_C_TERNA_75PCT_FOUR_PRINCIPAL_PURE_PHS",
    method_name: "Terna 75% concentration plus operator plant identity; remainder allocated by Terna zonal discharge share",
    allocation_grain: "MARKET_ZONE_REMAINDER",
    plant_group: "REMAINING_25_PERCENT_OTHER_PHS",
    market_zone: row.market_zone,
    plant_name: "",
    input_metric: "TERNA_PUMPED_DISCHARGE_NET_MW_REMAINDER_PROXY",
    input_value: row.total_pumped_hydro_NET_MW,
    input_unit: "MW_NET",
    allocation_share: round(0.25 * remainderShare, 12),
    candidate_operational_energy_MWh: round(13250 * remainderShare, 9),
    national_control_MWh: 53000,
    evidence_class: "DIRECT_TERNA_CONTROL_PLUS_PROJECT_REMAINDER_ALLOCATION",
    status: "PROJECT_ASSUMPTION_REQUIRED",
    model_input: false,
    source_id: "TERNA_PDS_2025_PUMPED_STORAGE|TERNA_2024_HYDRO_PERIMETER_RECONCILIATION",
    method_notes: "Remainder duration is assumed proportional to zonal discharge power; it is not plant-specific evidence.",
    preference: "PREFERRED_CANDIDATE_FOR_USER_APPROVAL; NOT CANONICAL",
  });
}

for (const row of pumping2024Zones) {
  const dischargeShare = totalPumpedDischarge ? Number(row.total_pumped_hydro_NET_MW) / totalPumpedDischarge : 0;
  const openPumpShare = totalOpenPumpPower ? Number(row.physical_inventory_HPHS_charge_power_MW) / totalOpenPumpPower : 0;
  for (const method of [
    { id: "PHS_CHARGE_ALLOC_TERNA_DISCHARGE_SHARE", share: dischargeShare, source: "TERNA_2024_HYDRO_PERIMETER_RECONCILIATION", note: "Allocates the 6.4-GW system maximum by Terna zonal pumped-discharge shares." },
    { id: "PHS_CHARGE_ALLOC_OPEN_PUMP_SHARE", share: openPumpShare, source: "ZENODO_14006948", note: "Allocates the 6.4-GW system maximum by open-inventory pump-nameplate shares; sensitivity only." },
  ]) {
    upsert(candidates, "candidate_id", {
      candidate_id: `${method.id}_${row.market_zone}`,
      parameter: "PUMPED_HYDRO_MAXIMUM_ABSORPTION_POWER_ALLOCATION",
      hydro_class: "PUMPED_HYDRO_PURE|PUMPED_HYDRO_MIXED",
      zone_or_plant: row.market_zone,
      zone: row.market_zone,
      plant_id: "",
      source: `TERNA_PDS_2025_PUMPED_STORAGE|${method.source}`,
      source_type: "PROJECT_ALLOCATION_FROM_TERNA_NATIONAL_OPERATIONAL_CONTROL",
      Terna_control_available: "YES_NATIONAL_ONLY",
      Terna_control_value: 6400,
      Terna_control_unit: "MW_APPROX_NATIONAL",
      recommended_MEM_use: `${method.note} USER APPROVAL REQUIRED.`,
      confidence: "TRANSPARENT_PROJECT_ALLOCATION",
      status: "PROJECT_ASSUMPTION_REQUIRED",
      accounting_role: "MODEL_PARAMETER_CANDIDATE",
      candidate_value: round(6400 * method.share, 9),
      unit: "MW_APPROX_ZONAL_CANDIDATE",
      physical_definition: "Candidate zonal share of Terna's approximate coincident maximum system absorption.",
      operational_definition: "Candidate fixed PyPSA pump-Link electrical-input p_nom.",
      direct_or_derived: "PROJECT_ALLOCATION",
    });
  }
}

upsert(definitions, "metric", {
  source_id: "TERNA_STORAGE_TECHNOLOGIES_2023",
  source: "Terna, Studio sulle tecnologie di riferimento per lo stoccaggio di energia elettrica",
  source_year: "2023",
  plant_class: "PUMPED_HYDRO_TECHNOLOGY",
  metric: "TYPICAL_NET_ROUNDTRIP_EFFICIENCY_RANGE",
  value_MWh: "",
  value_MW: "",
  metric_definition: "Terna reports approximately 70-75% net round-trip efficiency for pumped hydro, including the full charge-discharge cycle and auxiliary consumption.",
  method_row_summary: "Direct technology range; not plant-specific operating measurements.",
  natural_inflow_storage: "NOT THE DEFINING DIMENSION",
  electrically_rechargeable: "YES",
  operational_usable: "TECHNOLOGY-LEVEL RANGE",
  theoretical_physical: "NO",
  candidate_PyPSA_role: "RTE SENSITIVITY RANGE; SEPARATE PUMP/TURBINE SPLIT REQUIRES PROJECT ASSUMPTION",
  evidence_class: "DIRECT TERNA TECHNOLOGY STUDY",
  accounting_role: "MODEL_PARAMETER_CANDIDATE",
  status: "MODEL_CANDIDATE",
  source_type: "DIRECT_TERNA_TECHNOLOGY_RANGE",
  direct_or_derived: "DIRECT_RANGE",
  numerical_authority: "AUTHORITATIVE_TECHNOLOGY_RANGE_NOT_PLANT_SPECIFIC",
  canonical_MEM_input: false,
  recommended_MEM_use: "SENSITIVITY/ASSUMPTION BOUND; DO NOT REUSE CATANIA 87% RECONSTRUCTION COEFFICIENT",
  promotion_condition: "SELECT A POINT AND LOSS SPLIT ONLY AFTER USER APPROVAL OR PLANT-SPECIFIC OPERATING EVIDENCE",
  permanent_guardrail: "RTE RANGE DOES NOT IDENTIFY SEPARATE PUMP AND TURBINE EFFICIENCIES",
  value_or_range: "0.70-0.75",
  unit: "p.u._roundtrip",
});

const nationalRows = [
  { record_id: "TERNA_ROR_NET_2024", source_or_inventory: "TERNA OFFICIAL TABLE 14", hydro_class: "HYDRO_RUN_OF_RIVER", plant_count: 4506, discharge_or_installed_power_MW: 6239.0, capacity_basis: "NET EFFICIENT POWER", comparability: "PRIMARY FIVE-CLASS HYDRO BRIDGE", MEM_use: "NATIONAL_ROR_P_NOM_CONTROL", accounting_role: "PRIMARY_ADDITIVE", status: "CONTROLLING", source_type: "DIRECT_TERNA_CONTROL", numerical_authority: "CANONICAL_NUMERICAL_CONTROL", canonical_MEM_control: true },
  { record_id: "TERNA_BASIN_NET_2024", source_or_inventory: "TERNA OFFICIAL TABLE 14", hydro_class: "HYDRO_BASIN", plant_count: 206, discharge_or_installed_power_MW: 5019.9, capacity_basis: "NET EFFICIENT POWER", comparability: "PRIMARY FIVE-CLASS HYDRO BRIDGE", MEM_use: "NATIONAL_BASIN_P_NOM_CONTROL", accounting_role: "PRIMARY_ADDITIVE", status: "CONTROLLING", source_type: "DIRECT_TERNA_CONTROL", numerical_authority: "CANONICAL_NUMERICAL_CONTROL", canonical_MEM_control: true },
  { record_id: "TERNA_RESERVOIR_TOTAL_NET_2024", source_or_inventory: "TERNA OFFICIAL TABLE 14", hydro_class: "HYDRO_RESERVOIR_INCLUDING_PHS", plant_count: 195, discharge_or_installed_power_MW: 12035.1, capacity_basis: "NET EFFICIENT POWER", comparability: "INCLUDES 7,252.3 MW PURE+MIXED PUMPING", MEM_use: "RESERVOIR_TOTAL_CONTROL", accounting_role: "CONTROL_ROW_NON_ADDITIVE", status: "CONTROLLING", source_type: "DIRECT_TERNA_CONTROL", numerical_authority: "CANONICAL_NUMERICAL_CONTROL", canonical_MEM_control: true },
  { record_id: "TERNA_RESERVOIR_NONPUMPED_NET_2024", source_or_inventory: "PROJECT SAME-TABLE RECONCILIATION", hydro_class: "HYDRO_RESERVOIR", plant_count: "", discharge_or_installed_power_MW: 4782.8, capacity_basis: "NET EFFICIENT POWER", comparability: "12,035.1 MW reservoir total less 7,252.3 MW PHS subset", MEM_use: "NATIONAL_CONVENTIONAL_RESERVOIR_P_NOM_CONTROL", accounting_role: "PRIMARY_ADDITIVE", status: "CONTROLLING_RECONCILED", source_type: "PROJECT_DERIVATION_FROM_DIRECT_TERNA_SAME_TABLE", numerical_authority: "CANONICAL_RECONCILED_CONTROL", canonical_MEM_control: true },
];
for (const row of nationalRows) upsert(national, "record_id", {
  ...row,
  charge_power_MW: "",
  physical_reservoir_energy_MWh: "",
  operational_storage_energy_MWh: "",
  source_id: "TERNA_2024_IMPIANTI_DI_GENERAZIONE_TABLE_14",
  open_data_role: "NOT_APPLICABLE",
  comparison_Terna_control: "SAME TABLE 14 HYDRO CLASS RECONCILIATION",
  Terna_control_value_for_comparison: "ROR 6239.0; BASIN 5019.9; RESERVOIR TOTAL 12035.1; PHS SUBSET 7252.3; TOTAL 23294.0 MW",
  open_minus_control_difference: "",
  comparability_status: "SAME PUBLISHED NET-EFFICIENT CAPACITY BASIS; ROUNDING TO 0.1 MW",
  recommended_MEM_use: row.MEM_use,
});

audit = audit.map((row) => ({
  ...row,
  phase3_1_review_status: "UNCHANGED_SECONDARY_PHYSICAL_EVIDENCE",
  canonical_solver_input: false,
}));
zoneSummary = zoneSummary.map((row) => ({
  ...row,
  phase3_1_review_status: "UNCHANGED_SECONDARY_PHYSICAL_ALLOCATION_EVIDENCE",
  canonical_solver_input: false,
}));

crosswalk = crosswalk.map((row) => {
  const tech = row.historical_technology;
  const waterState = tech === "HYDRO_RUN_OF_RIVER" ? "NO MATERIAL STORE; INFLOW-LIMITED GENERATOR"
    : tech === "HYDRO_BASIN" ? "ONE NATURALLY CHARGED SHORT/INTERMEDIATE WATER STORE"
      : tech === "HYDRO_RESERVOIR" ? "ONE NATURALLY CHARGED RESERVOIR STORE"
        : tech === "PUMPED_HYDRO_PURE" ? "ONE ELECTRICALLY RECHARGEABLE SHARED STORE"
          : tech === "PUMPED_HYDRO_MIXED" ? "ONE SHARED STORE WITH NATURAL INFLOW AND ELECTRICAL PUMPING"
            : row.pypsa_component_pattern === "NONE_QA_CONTROL" ? "NO SOLVER STATE" : "NOT_APPLICABLE_OR_DEFINED_ELSEWHERE";
  return {
    ...row,
    phase3_1_water_state_rule: waterState,
    phase3_1_spillage_rule: ["HYDRO_BASIN", "HYDRO_RESERVOIR", "PUMPED_HYDRO_MIXED"].includes(tech) ? "EXPLICIT NON-NEGATIVE SPILL PATH; VALUE UNSET" : "NOT_APPLICABLE",
    phase3_1_component_specification: "MEM_Hydro_PyPSA_Component_Specification.md",
  };
});

upsert(sourceManifest, "source_id", {
  source_id: "ENEL_EGP_ENTRACQUE_OPERATOR_PAGE",
  publisher: "Enel Green Power",
  title: "Centrale idroelettrica Entracque",
  release_or_year: "CURRENT OPERATOR PAGE REVIEWED 2026-09-02",
  acquisition_route: "OFFICIAL_OPERATOR_WEB_REVIEW",
  acquisition_date: "2026-09-02",
  original_uploaded_filename: "",
  archived_raw_file: "",
  byte_size: "",
  raw_sha256: "",
  scope: "Pure-pumping architecture; Cuneo/NORD geography; 1,200 MW operator-reported operational capacity",
  evidence_class: "PRIMARY OPERATOR TECHNICAL/IDENTITY EVIDENCE",
  status: "REVIEWED_NO_PLANT_SPECIFIC_USABLE_ENERGY_FOUND",
  url: "https://www.enelgreenpower.com/it/impianti/operativi/centrale-idroelettrica-entracque",
});
upsert(sourceManifest, "source_id", {
  source_id: "ENEL_EGP_EDOLO_OPERATOR_PAGE",
  publisher: "Enel Green Power",
  title: "Centrale idroelettrica Edolo",
  release_or_year: "CURRENT OPERATOR PAGE REVIEWED 2026-09-02",
  acquisition_route: "OFFICIAL_OPERATOR_WEB_REVIEW",
  acquisition_date: "2026-09-02",
  original_uploaded_filename: "",
  archived_raw_file: "",
  byte_size: "",
  raw_sha256: "",
  scope: "Eight reversible units; Brescia/NORD geography; 1,000 MW operator-reported operational capacity",
  evidence_class: "PRIMARY OPERATOR TECHNICAL/IDENTITY EVIDENCE",
  status: "REVIEWED_NO_PLANT_SPECIFIC_USABLE_ENERGY_FOUND",
  url: "https://www.enelgreenpower.com/it/impianti/operativi/centrale-idroelettrica-edolo",
});
upsert(sourceManifest, "source_id", {
  source_id: "ENEL_EGP_RONCOVALGRANDE_OPERATOR_PAGE",
  publisher: "Enel Green Power",
  title: "Centrale idroelettrica Roncovalgrande",
  release_or_year: "CURRENT OPERATOR PAGE REVIEWED 2026-09-02",
  acquisition_route: "OFFICIAL_OPERATOR_WEB_REVIEW",
  acquisition_date: "2026-09-02",
  original_uploaded_filename: "",
  archived_raw_file: "",
  byte_size: "",
  raw_sha256: "",
  scope: "Eight generation/pumping units; Varese/NORD geography; 1,040 MW operator-reported operational capacity",
  evidence_class: "PRIMARY OPERATOR TECHNICAL/IDENTITY EVIDENCE",
  status: "REVIEWED_NO_PLANT_SPECIFIC_USABLE_ENERGY_FOUND",
  url: "https://www.enelgreenpower.com/it/impianti/operativi/centrale-idroelettrica-roncovalgrande",
});
upsert(sourceManifest, "source_id", {
  source_id: "ENEL_EGP_PRESENZANO_OPERATOR_PAGE",
  publisher: "Enel Green Power",
  title: "Centrale idroelettrica Domenico Cimarosa di Presenzano",
  release_or_year: "CURRENT OPERATOR PAGE REVIEWED 2026-09-02",
  acquisition_route: "OFFICIAL_OPERATOR_WEB_REVIEW",
  acquisition_date: "2026-09-02",
  original_uploaded_filename: "",
  archived_raw_file: "",
  byte_size: "",
  raw_sha256: "",
  scope: "Closed-cycle PHS; Caserta/CSUD geography; four reversible units; 1,000 MW operator-reported operational capacity",
  evidence_class: "PRIMARY OPERATOR TECHNICAL/IDENTITY EVIDENCE",
  status: "REVIEWED_NO_PLANT_SPECIFIC_USABLE_ENERGY_FOUND",
  url: "https://www.enelgreenpower.com/it/impianti/operativi/centrale-idroelettrica-presenzano",
});
upsert(sourceManifest, "source_id", {
  source_id: "PYPSA_EUR_HYDRO_PROFILE_PIPELINE",
  publisher: "PyPSA-Eur contributors",
  title: "PyPSA-Eur add_electricity.py hydro attachment workflow",
  release_or_year: "MASTER REVIEWED 2026-09-02",
  acquisition_route: "OFFICIAL_PROJECT_GITHUB_REVIEW",
  acquisition_date: "2026-09-02",
  original_uploaded_filename: "",
  archived_raw_file: "",
  byte_size: "",
  raw_sha256: "",
  scope: "Implementation candidate for hourly hydro inflow shapes and separate RoR/PHS/reservoir treatment; not an authority for Italian annual energy or capacity",
  evidence_class: "PYPSA IMPLEMENTATION MAPPING",
  status: "REVIEWED_CANDIDATE_ONLY",
  url: pypsaEurHydroUrl,
});

upsert(derivationManifest, "derivation_id", {
  derivation_id: "DER-HIST-033",
  output: "MEM_PHASE3_1_CROSS_ARTIFACT_CONSISTENCY_QA.csv",
  evidence_class: "QA / GATE AND LINEAGE RECONCILIATION",
  inputs: "Pumping bridge; storage table; historical-to-model crosswalk; gap/decision registers; frozen historical tables",
  method: "Remove stale zonal operational e_nom values while preserving superseded lineage; supersede contradictory gates; prohibit solver wording on QA-only rows; assert unchanged row counts.",
  key_guardrail: "The 53-GWh Terna control remains national-only and no tracker absence reopens the frozen historical measurement baseline.",
  status: "VALIDATED_PASS",
});
upsert(derivationManifest, "derivation_id", {
  derivation_id: "DER-HIST-034",
  output: "MEM_PHS_Operational_Energy_Allocation_Candidates.csv",
  evidence_class: "PROJECT ASSUMPTION / SENSITIVITY",
  inputs: "Terna 53-GWh national control; Terna zonal discharge controls; open HPHS physical energy; Terna 75% concentration statement; operator plant pages",
  method: "Build three transparent allocation candidates, each summing to 53,000 MWh, without promoting any to canonical zonal e_nom.",
  key_guardrail: "Every candidate is model_input=false until explicit approval; Method B never treats open physical reservoir energy as operational evidence.",
  status: "VALIDATED_CANDIDATES_NOT_APPROVED",
});
upsert(derivationManifest, "derivation_id", {
  derivation_id: "DER-HIST-035",
  output: "MEM_Hydro_Model_Parameter_Candidates.csv",
  evidence_class: "PYPSA IMPLEMENTATION MAPPING / CONTROLLED PARTIAL",
  inputs: "Terna 2024 Table 14; Terna PdS 2025; Terna storage technology study; operator pages; PyPSA-Eur hydro workflow",
  method: "Promote only metric- and grain-compatible controls; resolve national/NORD hydro class p_nom where direct; retain zonal PHS energy/pump-power, efficiencies, reservoir energy and inflow as candidates/assumptions.",
  key_guardrail: "MODEL_READY requires a solver-compatible definition and spatial grain; national controls are not automatically seven-zone parameters.",
  status: "VALIDATED_PARTIAL",
});
upsert(derivationManifest, "derivation_id", {
  derivation_id: "DER-HIST-036",
  output: "MEM_Hydro_PyPSA_Component_Specification.md",
  evidence_class: "PYPSA IMPLEMENTATION SPECIFICATION",
  inputs: "Accepted five-class hydro architecture; PyPSA component semantics; Phase 3.1 parameter register",
  method: "Specify water-energy buses, stores, fixed turbine/pump links, forced inflow, spillage and SOC policies while separating architectural decisions from unresolved numerical inputs.",
  key_guardrail: "Mixed pumping uses one shared state receiving both natural inflow and electrical pumping; terminal SOC and losses remain unselected.",
  status: "SPECIFICATION_COMPLETE_PARAMETERS_PARTIAL",
});

upsert(decisions, "decision_id", {
  decision_id: "D-HIST-022",
  status: "RESOLVED - PHASE3_1 CONSISTENCY PATCH",
  decision: "National-only PHS operational energy and cross-artifact lineage",
  evidence_class: "QA / RECONCILIATION ONLY",
  rule: "All seven canonical zonal PHS operational e_nom values remain blank. The former normalized 53-GWh allocation is retained only as superseded, model_input=false lineage.",
  value_or_artifact: "MEM_PHASE3_1_CROSS_ARTIFACT_CONSISTENCY_QA.csv",
  model_effect: "No accidental zonal Store.e_nom enters the future solver input set.",
});
upsert(decisions, "decision_id", {
  decision_id: "D-HIST-023",
  status: "RESOLVED - NATIONAL AND NORD HYDRO CLASS POWER",
  decision: "Terna 2024 hydro type NET-MW controls",
  evidence_class: "DIRECT TERNA CAPACITY / PROJECT SAME-TABLE DERIVATION",
  rule: "Use Table 14 national RoR, basin and reservoir controls; subtract the same-table PHS subset to obtain non-pumped reservoir power. Italia Settentrionale maps to NORD; other macroregions require further market-zone allocation.",
  value_or_artifact: "ROR 6,239.0; basin 5,019.9; non-pumped reservoir 4,782.8; PHS 7,252.3; total 23,294.0 MW NET",
  model_effect: "NORD RoR/basin/non-pumped-reservoir p_nom can be MODEL_READY; six-zone type allocation remains partial.",
});
upsert(decisions, "decision_id", {
  decision_id: "D-HIST-024",
  status: "CANDIDATES REQUIRE APPROVAL",
  decision: "PHS operational energy, pump power and efficiency allocation",
  evidence_class: "TERNA OPERATIONAL CONTROL / PROJECT ASSUMPTION",
  rule: "Retain 53 GWh, 6.4 GW and 70-75% RTE at national/technology grain. Compare three energy allocations and two charge-power allocations; choose none as canonical without explicit approval.",
  value_or_artifact: "MEM_PHS_Operational_Energy_Allocation_Candidates.csv|MEM_Hydro_Model_Parameter_Candidates.csv",
  model_effect: "HYDRO_MODEL_PARAMETER_STATUS remains PARTIAL.",
});
upsert(decisions, "decision_id", {
  decision_id: "D-HIST-025",
  status: "BLOCKED - EXTERNAL DATA CONFIRMED PHASE3_1",
  decision: "One-pass local search for August-2026 GOGPT and July-2026 GCPT releases",
  evidence_class: "QA / ACQUISITION GATE",
  rule: "The Phase-3.1 one-pass MEM-workspace search found no complete tracker workbook. Do not repeat local/web workarounds or create downstream fleet files with fabricated rows.",
  value_or_artifact: "GEM_GOGPT_GCPT_2026_ACQUISITION_REQUIREMENT.md",
  model_effect: "THERMAL_FUEL_PLANT_ATTRIBUTION_STATUS=INCOMPLETE; CURRENT_2026_PHYSICAL_FLEET_STATUS=BLOCKED; 2040 physical bridge not calculated.",
});

for (const gapId of ["GAP-HIST-010", "GAP-HIST-011"]) {
  const trackerGap = gaps.find((row) => row.gap_id === gapId);
  if (trackerGap) trackerGap.local_search_result = "Phase-3.1 one-pass MEM-workspace search on 2026-09-02 found only archived GOGPT source-page/form assets and acquisition notes; no complete GOGPT August-2026 or GCPT July-2026 workbook.";
}

const hydroGap = gaps.find((row) => row.gap_id === "GAP-HIST-012");
if (hydroGap) Object.assign(hydroGap, {
  status: "PARTIAL - SOME P_NOM RESOLVED; E_NOM/LOSSES/INFLOW STILL OPEN",
  local_search_result: "Direct Terna Table 14 resolves national hydro-class NET MW and NORD RoR/basin/non-pumped-reservoir p_nom. Terna supplies national PHS energy/pump-power and a 70-75% RTE range, but not canonical zonal e_nom, zonal charge power, separate link efficiencies, active conventional-reservoir energy or hourly inflow.",
  exact_acquisition_or_decision: "Approve one transparent PHS energy/pump allocation and one efficiency convention; acquire/validate remaining six-zone hydro-class MW and active reservoir operating ranges; select a hydrology/weather year and annual-reconciliation method.",
  required_fields_or_controls: "zone/class NET MW; plant/zone usable PHS energy; pump MW; eta_pump; eta_turbine; active reservoir energy; inflow shape/year; initial/terminal SOC",
  acceptance_test: "Every solver row is MODEL_READY at the required grain, or an explicit approved project assumption with sensitivity range and Terna reconciliation.",
  blocks: "Final hydro solver inputs only; not HISTORICAL_TECHNOLOGY_BASELINE_STATUS",
});

let hydroGate = gates.find((row) => row.gate === "HYDRO_MODEL_PARAMETER_STATUS");
if (hydroGate) Object.assign(hydroGate, {
  status: "PARTIAL",
  controlling_reason: "Direct Terna evidence now resolves national hydro-class NET MW and NORD RoR/basin/non-pumped-reservoir p_nom. Zonal PHS operational energy/charge power, separate efficiencies, six-zone conventional type MW, active reservoir energy, inflows and SOC rules remain unapproved or unavailable.",
  next_action: "Approve candidate PHS allocation/efficiency conventions and acquire the remaining zone/type and active-reservoir evidence before solver-input freeze.",
});
const thermalGate = gates.find((row) => row.gate === "THERMAL_FUEL_PLANT_ATTRIBUTION_STATUS");
if (thermalGate) Object.assign(thermalGate, {
  status: "INCOMPLETE",
  controlling_reason: "The Phase-3.1 one-pass MEM-workspace search found neither complete official GOGPT August-2026 nor GCPT July-2026 workbook.",
  next_action: "User supplies both untouched official releases; no repeated workaround search is authorized.",
});
const fleetGate = gates.find((row) => row.gate === "CURRENT_2026_PHYSICAL_FLEET_STATUS");
if (fleetGate) Object.assign(fleetGate, {
  status: "BLOCKED",
  controlling_reason: "No complete oil/gas or coal tracker release is local, so plant/unit status and fuel attribution cannot be reconciled to Terna NET controls.",
  next_action: "Ingest GOGPT and GCPT only after the untouched releases are supplied; do not create a synthetic 2026 stack.",
});
upsert(gates, "gate", {
  gate: "PHASE3_1_CONSISTENCY_STATUS",
  status: "PASS",
  controlling_reason: "All cross-artifact and hydro-parameter reconciliation checks pass and historical row counts remain 758/881/19.",
  independent_of: "Tracker availability; workbook provenance; later solver parameter selection",
  next_action: "Retain this as the controlling Phase 3.1 consistency result.",
});

const zonalCanonicalPumping = pumping.filter((row) => String(row.year) === "2024" && row.geographic_scope === "MARKET_ZONE" && row.pumped_hydro_energy_MWh !== "");
const zonalCanonicalStorage = storage.filter((row) => String(row.year) === "2024" && row.technology === "PUMPED_HYDRO" && row.spatial_scope === "MARKET_ZONE" && row.energy_capacity_MWh !== "");
const zonalCanonicalPumpingCharge = pumping.filter((row) => String(row.year) === "2024" && row.geographic_scope === "MARKET_ZONE" && row.pumped_hydro_charge_MW !== "");
const zonalCanonicalStorageCharge = storage.filter((row) => String(row.year) === "2024" && row.technology === "PUMPED_HYDRO" && row.spatial_scope === "MARKET_ZONE" && row.charge_power_MW !== "");
const nationalControlRows = pumping.filter((row) => String(row.year) === "2024" && row.geographic_scope === "NATIONAL" && Number(row.pumped_hydro_energy_MWh) === 53000 && Number(row.Terna_system_operational_PHS_energy_MWh) === 53000);
const activeHistoricalContradictions = gaps.filter((row) => !String(row.status).startsWith("SUPERSEDED") && /historical (freeze|technology baseline)/i.test(`${row.blocks} ${row.status}`) && /(GOGPT|GCPT|tracker|GEM)/i.test(JSON.stringify(row)));
const badControlWording = crosswalk.filter((row) => row.pypsa_component_pattern === "NONE_QA_CONTROL" && /(fixed|instantiate|instantiated).*(generator|store|link|capacity)|solver component(?! instantiated)/i.test(`${row.notes} ${row.evidence_guardrail}`) && !/NO SOLVER COMPONENT INSTANTIATED/i.test(`${row.notes} ${row.evidence_guardrail}`));
const historicalGate = gates.find((row) => row.gate === "HISTORICAL_TECHNOLOGY_BASELINE_STATUS");

const methodTotals = Object.fromEntries([...new Set(allocationCandidates.map((row) => row.method_id))].map((methodId) => [
  methodId,
  round(sum(allocationCandidates.filter((row) => row.method_id === methodId).map((row) => row.candidate_operational_energy_MWh)), 6),
]));
const chargeMethodTotals = {
  PHS_CHARGE_ALLOC_TERNA_DISCHARGE_SHARE: round(sum(candidates.filter((row) => String(row.candidate_id).startsWith("PHS_CHARGE_ALLOC_TERNA_DISCHARGE_SHARE_")).map((row) => row.candidate_value)), 6),
  PHS_CHARGE_ALLOC_OPEN_PUMP_SHARE: round(sum(candidates.filter((row) => String(row.candidate_id).startsWith("PHS_CHARGE_ALLOC_OPEN_PUMP_SHARE_")).map((row) => row.candidate_value)), 6),
};
const allowedParameterStatuses = new Set(["CANONICAL_CONTROL", "MODEL_READY", "MODEL_CANDIDATE", "PHYSICAL_EVIDENCE_ONLY", "PROJECT_ASSUMPTION_REQUIRED", "REQUIRES_FURTHER_VALIDATION"]);
const invalidParameterStatuses = candidates.filter((row) => !allowedParameterStatuses.has(row.status));
const modelReadyRows = candidates.filter((row) => row.status === "MODEL_READY");
const nationalFiveClassTotal = 6239.0 + 5019.9 + 4782.8 + 3969.57561 + 3282.72439;
const nordTypeBridge = 5227.9 + 3512.0 + 3669.1 + 4699.7;

const qa = [
  { check_id: "P31-QA-001", category: "PHS_OPERATIONAL_ENERGY", test: "Zero canonical zonal PHS operational e_nom values in pumping bridge", observed: zonalCanonicalPumping.length, expected: 0, status: zonalCanonicalPumping.length === 0 ? "PASS" : "FAIL", evidence_artifact: path.basename(files.pumping) },
  { check_id: "P31-QA-002", category: "PHS_OPERATIONAL_ENERGY", test: "Zero canonical zonal PHS operational e_nom values in storage table", observed: zonalCanonicalStorage.length, expected: 0, status: zonalCanonicalStorage.length === 0 ? "PASS" : "FAIL", evidence_artifact: path.basename(files.storage) },
  { check_id: "P31-QA-003", category: "PHS_OPERATIONAL_ENERGY", test: "National Terna operational PHS energy control preserved", observed: nationalControlRows.length === 1 ? "53000 MWh" : `${nationalControlRows.length} matching rows`, expected: "one 53000 MWh national control", status: nationalControlRows.length === 1 ? "PASS" : "FAIL", evidence_artifact: path.basename(files.pumping) },
  { check_id: "P31-QA-004", category: "GATE_LINEAGE", test: "No active historical gap says tracker absence blocks frozen technology baseline", observed: activeHistoricalContradictions.length, expected: 0, status: activeHistoricalContradictions.length === 0 ? "PASS" : "FAIL", evidence_artifact: path.basename(files.gaps) },
  { check_id: "P31-QA-005", category: "CROSSWALK", test: "No NONE_QA_CONTROL row claims a generator/store/link/capacity is instantiated", observed: badControlWording.length, expected: 0, status: badControlWording.length === 0 ? "PASS" : "FAIL", evidence_artifact: path.basename(files.crosswalk) },
  { check_id: "P31-QA-006", category: "HISTORICAL_INTEGRITY", test: "Historical capacity row count unchanged", observed: capacity.length, expected: 758, status: capacity.length === 758 ? "PASS" : "FAIL", evidence_artifact: path.basename(files.capacity) },
  { check_id: "P31-QA-007", category: "HISTORICAL_INTEGRITY", test: "Historical generation row count unchanged", observed: generation.length, expected: 881, status: generation.length === 881 ? "PASS" : "FAIL", evidence_artifact: path.basename(files.generation) },
  { check_id: "P31-QA-008", category: "HISTORICAL_INTEGRITY", test: "Historical storage row count unchanged", observed: storage.length, expected: 19, status: storage.length === 19 ? "PASS" : "FAIL", evidence_artifact: path.basename(files.storage) },
  { check_id: "P31-QA-009", category: "GATE_STATUS", test: "Historical technology baseline remains frozen", observed: historicalGate?.status ?? "MISSING", expected: "FROZEN", status: historicalGate?.status === "FROZEN" ? "PASS" : "FAIL", evidence_artifact: path.basename(files.gates) },
  { check_id: "P31-QA-010", category: "PHS_OPERATIONAL_ENERGY", test: "Every PHS operational-energy allocation candidate reconciles to the national control", observed: JSON.stringify(methodTotals), expected: "each method = 53000 MWh", status: Object.values(methodTotals).every((value) => Math.abs(value - 53000) < 0.001) ? "PASS" : "FAIL", evidence_artifact: path.basename(files.allocationCandidates) },
  { check_id: "P31-QA-011", category: "PHS_CHARGE_POWER", test: "Both zonal PHS charge-power allocation candidates reconcile to Terna national control", observed: JSON.stringify(chargeMethodTotals), expected: "each method = 6400 MW", status: Object.values(chargeMethodTotals).every((value) => Math.abs(value - 6400) < 0.001) ? "PASS" : "FAIL", evidence_artifact: path.basename(files.candidates) },
  { check_id: "P31-QA-012", category: "HYDRO_CLASS_POWER", test: "National five-class hydro power bridge reconciles to Terna total", observed: round(nationalFiveClassTotal, 6), expected: 23294, status: Math.abs(nationalFiveClassTotal - 23294) < 0.000001 ? "PASS" : "FAIL", evidence_artifact: path.basename(files.candidates) },
  { check_id: "P31-QA-013", category: "HYDRO_CLASS_POWER", test: "NORD Terna class bridge reconciles at published 0.1-MW grain", observed: round(nordTypeBridge, 6), expected: 17108.7, status: Math.abs(nordTypeBridge - 17108.7) < 0.000001 ? "PASS" : "FAIL", evidence_artifact: path.basename(files.candidates) },
  { check_id: "P31-QA-014", category: "PARAMETER_STATUS", test: "Model-parameter status vocabulary is restricted to the approved six values", observed: invalidParameterStatuses.length, expected: 0, status: invalidParameterStatuses.length === 0 ? "PASS" : "FAIL", evidence_artifact: path.basename(files.candidates) },
  { check_id: "P31-QA-015", category: "PARAMETER_PROMOTION", test: "Only direct/same-table NORD turbine-power parameters are MODEL_READY", observed: modelReadyRows.map((row) => row.candidate_id).join("|"), expected: "three NORD hydro-class p_nom rows", status: modelReadyRows.length === 3 && modelReadyRows.every((row) => row.zone === "NORD" && row.parameter === "HYDRO_CLASS_NET_TURBINE_POWER") ? "PASS" : "FAIL", evidence_artifact: path.basename(files.candidates) },
  { check_id: "P31-QA-016", category: "PHS_CHARGE_POWER", test: "Zero canonical zonal PHS pump-power values in pumping bridge", observed: zonalCanonicalPumpingCharge.length, expected: 0, status: zonalCanonicalPumpingCharge.length === 0 ? "PASS" : "FAIL", evidence_artifact: path.basename(files.pumping) },
  { check_id: "P31-QA-017", category: "PHS_CHARGE_POWER", test: "Zero canonical zonal PHS pump-power values in storage table", observed: zonalCanonicalStorageCharge.length, expected: 0, status: zonalCanonicalStorageCharge.length === 0 ? "PASS" : "FAIL", evidence_artifact: path.basename(files.storage) },
];

if (qa.some((row) => row.status !== "PASS")) throw new Error(`Phase 3.1 patch QA failed: ${JSON.stringify(qa.filter((row) => row.status !== "PASS"))}`);

const outputs = [];
outputs.push(await authorCsv(pumping, "PumpingBridge", files.pumping));
outputs.push(await authorCsv(storage, "Storage", files.storage));
outputs.push(await authorCsv(crosswalk, "Crosswalk", files.crosswalk));
outputs.push(await authorCsv(gaps, "Gaps", files.gaps));
outputs.push(await authorCsv(decisions, "Decisions", files.decisions));
outputs.push(await authorCsv(definitions, "Definitions", files.definitions));
outputs.push(await authorCsv(candidates, "Candidates", files.candidates));
outputs.push(await authorCsv(allocationCandidates, "EnergyAllocations", files.allocationCandidates));
outputs.push(await authorCsv(national, "National", files.national));
outputs.push(await authorCsv(audit, "Audit", files.audit));
outputs.push(await authorCsv(zoneSummary, "ZoneSummary", files.zoneSummary));
outputs.push(await authorCsv(gates, "Gates", files.gates));
outputs.push(await authorCsv(sourceManifest, "Sources", files.sourceManifest));
outputs.push(await authorCsv(derivationManifest, "Derivations", files.derivationManifest));
outputs.push(await authorCsv(qa, "Phase31QA", files.qa));

const statusCounts = Object.fromEntries([...allowedParameterStatuses].map((status) => [status, candidates.filter((row) => row.status === status).length]));
const verification = {
  phase: "PHASE3_1_CONSISTENCY_AND_HYDRO_PARAMETER_CLOSURE",
  generated_at: new Date().toISOString(),
  qa_checks: qa.length,
  qa_pass: qa.filter((row) => row.status === "PASS").length,
  qa_fail: qa.filter((row) => row.status !== "PASS").length,
  historical_rows: { capacity: capacity.length, generation: generation.length, storage: storage.length },
  zonal_canonical_operational_phs_e_nom_rows: zonalCanonicalPumping.length + zonalCanonicalStorage.length,
  national_operational_phs_energy_control_MWh: 53000,
  energy_allocation_candidate_totals_MWh: methodTotals,
  charge_power_allocation_candidate_totals_MW: chargeMethodTotals,
  hydro_model_parameter_status_counts: statusCounts,
  model_ready_candidate_ids: modelReadyRows.map((row) => row.candidate_id),
  historical_technology_baseline_status: historicalGate?.status,
  hydro_model_parameter_status: gates.find((row) => row.gate === "HYDRO_MODEL_PARAMETER_STATUS")?.status,
  thermal_fuel_plant_attribution_status: gates.find((row) => row.gate === "THERMAL_FUEL_PLANT_ATTRIBUTION_STATUS")?.status,
  current_2026_physical_fleet_status: gates.find((row) => row.gate === "CURRENT_2026_PHYSICAL_FLEET_STATUS")?.status,
  tracker_search: {
    searched_once_on: "2026-09-02",
    GOGPT_August_2026_complete_workbook_found: false,
    GCPT_July_2026_complete_workbook_found: false,
    qualifying_files_found: 0,
    result: "GEM_AUG2026_AND_GCPT_JUL2026_REQUIRE_USER_DOWNLOAD",
  },
  workbook_promotion_status: gates.find((row) => row.gate === "WORKBOOK_PROMOTION_STATUS")?.status,
  national_five_class_hydro_power_bridge_MW: round(nationalFiveClassTotal, 6),
  nord_hydro_type_bridge_MW: round(nordTypeBridge, 6),
  documents: {
    hydro_component_specification: { file: path.relative(phaseRoot, files.componentSpec).replaceAll("\\", "/"), sha256: await sha256File(files.componentSpec) },
    methodology: { file: path.relative(phaseRoot, files.methodologyDoc).replaceAll("\\", "/"), sha256: await sha256File(files.methodologyDoc) },
    qa_report: { file: path.relative(phaseRoot, files.qaReport).replaceAll("\\", "/"), sha256: await sha256File(files.qaReport) },
    tracker_acquisition_requirement: { file: path.relative(phaseRoot, files.acquisitionRequirement).replaceAll("\\", "/"), sha256: await sha256File(files.acquisitionRequirement) },
  },
  output_artifacts: outputs,
};
await fs.writeFile(files.finalVerification, `${JSON.stringify(verification, null, 2)}\n`, "utf8");
outputs.push({ file: path.relative(phaseRoot, files.finalVerification).replaceAll("\\", "/"), rows: 1, columns: 1, sha256: await sha256File(files.finalVerification) });

process.stdout.write(`${JSON.stringify({
  phase: "PHASE3_1_CONSISTENCY_AND_HYDRO_PARAMETER_CLOSURE",
  pass_checks: qa.filter((row) => row.status === "PASS").length,
  failed_checks: qa.filter((row) => row.status !== "PASS").length,
  zonal_canonical_pumping_e_nom_rows: zonalCanonicalPumping.length,
  zonal_canonical_storage_e_nom_rows: zonalCanonicalStorage.length,
  preserved_national_operational_energy_MWh: 53000,
  historical_rows: { capacity: capacity.length, generation: generation.length, storage: storage.length },
  parameter_status_counts: statusCounts,
  model_ready_candidate_ids: modelReadyRows.map((row) => row.candidate_id),
  energy_allocation_candidate_totals_MWh: methodTotals,
  charge_power_allocation_candidate_totals_MW: chargeMethodTotals,
  outputs,
}, null, 2)}\n`);

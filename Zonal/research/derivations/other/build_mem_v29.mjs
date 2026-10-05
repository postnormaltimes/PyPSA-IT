import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const [inputPath, outputPath, verificationDir] = process.argv.slice(2);
if (!inputPath || !outputPath || !verificationDir) {
  throw new Error("Usage: node build_mem_v29.mjs INPUT_XLSX OUTPUT_XLSX VERIFICATION_DIR");
}

const COLORS = {
  navy: "#17324D",
  teal: "#0F5C5E",
  orange: "#E87500",
  paleBlue: "#DDEEEE",
  rowBlue: "#D9EEF7",
  border: "#C9D2DA",
  text: "#1F2933",
  white: "#FFFFFF",
  warning: "#FFF2CC",
  pass: "#E2F0D9",
};

const inputBlob = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(inputBlob);

const preservedSheetNames = [
  "03_ZONE_MAP",
  "05_STORAGE_BY_ZONE",
  "06_DEMAND_BY_ZONE",
  "10_NUCLEAR",
];

function matrixSnapshot(sheetName) {
  const sheet = workbook.worksheets.getItem(sheetName);
  const used = sheet.getUsedRange();
  return JSON.stringify({ values: used.values, formulas: used.formulas });
}

const preservedBefore = Object.fromEntries(
  preservedSheetNames.map((name) => [name, matrixSnapshot(name)]),
);

function borderFormat() {
  return { preset: "all", style: "thin", color: COLORS.border };
}

function styleSection(sheet, address, text) {
  const range = sheet.getRange(address);
  range.merge();
  range.values = [[text]];
  range.format = {
    fill: COLORS.orange,
    font: { bold: true, color: COLORS.white, fontSize: 10 },
    borders: borderFormat(),
    wrapText: true,
    horizontalAlignment: "left",
    verticalAlignment: "center",
  };
  range.format.rowHeight = 22;
}

function styleHeader(range) {
  range.format = {
    fill: COLORS.teal,
    font: { bold: true, color: COLORS.white, fontSize: 9 },
    borders: borderFormat(),
    wrapText: true,
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  range.format.rowHeight = 28;
}

function styleBody(range, fill = null) {
  range.format = {
    fill: fill ?? COLORS.white,
    font: { color: COLORS.text, fontSize: 9 },
    borders: borderFormat(),
    wrapText: true,
    verticalAlignment: "center",
  };
}

function styleAlternatingRows(sheet, startRow, endRow, startCol, endCol) {
  for (let row = startRow; row <= endRow; row += 1) {
    styleBody(
      sheet.getRange(`${startCol}${row}:${endCol}${row}`),
      row % 2 === 0 ? COLORS.rowBlue : COLORS.white,
    );
  }
}

function setMergedText(sheet, cell, text) {
  sheet.getRange(cell).values = [[text]];
}

// 00_README — replace the controlling summary while retaining the existing layout.
{
  const sheet = workbook.worksheets.getItem("00_README");
  setMergedText(sheet, "A1", "MEM workbook v2.9 — independent seven-zone architecture refined; empirical gates open");
  setMergedText(sheet, "A2", "Successor to the verified v2.8 baseline. Scenario, demand, FER, storage, grid, cost and nuclear logic are retained; architecture, thermal evidence and adequacy interpretations are now separated explicitly.");
  sheet.getRange("A4:C4").values = [["Item", "v2.9 controlling rule", "Evidence / boundary"]];
  sheet.getRange("A5:C14").values = [
    ["Model architecture", "Fresh independent NORD/CNOR/CSUD/SUD/CALA/SICI/SARD hourly fixed-capacity economic-dispatch model", "Exactly seven Italian buses; external market buses are additional. Commercial transfers are fixed directional PyPSA Links, not physical AC Lines."],
    ["Preferred description", "A seven-zone, hourly, fixed-capacity economic dispatch model of Italy with transport-constrained internal exchanges and exogenous price-taking neighbouring markets.", "No capacity expansion, redispatch, unit commitment, reserves, Stage 1/2 machinery or PyPSA-IT runtime dependency."],
    ["Evidence taxonomy", "Published source capacity/energy, project derivation, interim assumption, diagnostic, adequacy benchmark and runtime result remain distinct", "A benchmark or diagnostic is never silently converted into generator p_nom."],
    ["Thermal hierarchy", "Terna 2024 NET efficient capacity -> GEM August-2026 plant/unit reconciliation -> PyPSA implementation mapping", "Terna controls MW; GEM reconciles identity/status/geography; PyPSA/powerplantmatching is non-authoritative implementation support."],
    ["Current thermal baseline", "Build province -> region -> market-zone Terna technology totals, then reconcile physical units and 2025-H1 2026 changes with GEM", "No authoritative Terna extract or GEM fleet file is present; thermal p_nom remains an explicit data gate."],
    ["2040 thermoelectric", "55 GW is a national Terna net-efficient thermoelectric control/envelope in Slow/Base/High", "It includes non-gas thermal categories; it is not 55 GW gas and does not define a CCGT+OCGT split."],
    ["2050 programmable", "15.5136 GW is an energy-implied diagnostic; approximately 30 GW is an independent Terna adequacy benchmark", "Do not add them, force installed p_nom to 30 GW, or create a 14.4864 GW required residual."],
    ["Slow / High", "Slow retains 16.1031 GW lost CDP, 18.9449 GW programmable-equivalent proxy and 48.9449 GW diagnostic only; High does not mechanically retire thermal capacity", "Slow/High physical capacity changes are determined only after chronological runtime evidence."],
    ["External markets", "External bus + fixed virtual generator + fixed virtual load + fixed import/export Links; trade is endogenous", "Remove the auxiliary external-market objective baseline and report net import purchases minus export revenue plus tolls/losses."],
    ["Release status", "Architecture and methodology are implementation-ready; empirical model inputs are not yet complete", "A deterministic one-year run may report scarcity/ENS indicators but not LOLE, probabilistic capacity credit, N-1 security or formal Terna adequacy."],
  ];
  setMergedText(sheet, "A16", "Read 02_SCENARIOS, 04_GENERATION_BY_ZONE, 09_TECH_COSTS, 11_PYPSA_IMPLEMENTATION and 12_ASSUMPTIONS_QA together. RESOLVED methodology does not imply that REQUIRES DATA and RUNTIME GATE items are complete.");
  sheet.getRange("A5:C14").format.rowHeight = 34;
  sheet.getRange("A16:J16").format.rowHeight = 42;
}

// 01_SOURCES — append the handoff and the authoritative-but-missing empirical sources.
{
  const sheet = workbook.worksheets.getItem("01_SOURCES");
  const rows = [
    ["H29-001", "PyPSA-IT to MEM frozen handoff", "PyPSA-IT project", 2026, "Frozen architecture / data-contract handoff", "2026-08-31", "Independent MEM architecture, source hierarchy, contracts, external boundary and QA patterns", "All headline scenarios", "Current/2040/2050", "Italy seven zones + external markets", "Generation, storage, load, transport, external markets", "Schemas and reviewed patterns; explicitly no empirical MEM dataset", "VERIFIED", "CORE / PROVENANCE", "MEM_PyPSA_IT_Handoff_20260831.zip", "SHA-256 7f4ad4a28e18f2d97735baefca3f966b727b27fb67d2aca394200cdd2e0bb244; no PyPSA-IT runtime dependency."],
    ["H29-002", "Thermoelectric Capacity — detailed statistical extract", "Terna S.p.A.", 2024, "Official DATA/API dataset", "Latest consolidated year 2024", "Canonical NET efficient thermoelectric capacity by province/region/category/subcategory", "Current baseline and future bridge", "2024", "Province -> region -> market zone", "Thermoelectric technologies and fuels kept distinct", "Required canonical zone x technology matrix; national controls approximately 60.33/48.02/41.74/3.62 GW", "REQUIRES DATA", "CORE", "Official Terna DATA/API — exact acquisition route in v2.9 gap register", "Acquire year=2024, capacity_type=NET, all regions/provinces/categories/subcategories; do not populate from placeholders."],
    ["H29-003", "Global Oil and Gas Plant Tracker", "Global Energy Monitor", 2026, "Plant/unit tracker", "August 2026 edition", "Plant/unit identity, technology, fuel, CHP, status, commissioning/retirement, coordinates and owner", "Current baseline reconciliation", "2024-H1 2026 bridge", "Italy plants/units -> market zone", "Oil and gas plant/unit layer", "Reconciliation layer; gross/net and coverage differences must be quantified", "REQUIRES DATA", "CORE / RECONCILIATION", "Official GEM August-2026 tracker — exact acquisition route in v2.9 gap register", "GEM does not overwrite Terna MW controls; filter Italy and preserve original capacity basis/status fields."],
    ["H29-004", "MEM v2.9 frozen-input contract", "MEM project", 2026, "Internal model specification", "2026-08-31", "Independent runtime schemas, keys, units, evidence classes and rejection rules", "All", "Current/2040/2050", "Seven-zone Italy + external markets", "All model components", "Ten frozen inputs plus provenance and validation requirements", "VERIFIED", "CORE / IMPLEMENTATION", "MEM_v2.9_FROZEN_DATA_CONTRACT.md", "Runtime consumes frozen files only; no live Terna/GEM/PyPSA-IT import."],
    ["H29-005", "Official study-year bidding-zone topology, commercial capacities and external prices", "Terna / GME / ENTSO-E as applicable", 2026, "Acquisition requirement", "Study-year specific", "Verify Italian topology, directional NTC/ATC, external bidding-zone set/mapping and complete hourly day-ahead prices", "All future runs", "2040/2050", "Italy and connected bidding zones", "Transport Links and external price-taking markets", "Provisional FR/CH/AT/SI/GR/ME mapping plus any study-year additions/exclusions", "REQUIRES DATA", "CORE", "See MEM_v2.9_EMPIRICAL_GAP_AND_ACQUISITION_REGISTER.csv", "Static versus hourly capacity and sending/receiving-side convention must be frozen explicitly."],
  ];
  sheet.getRange("A46:P50").values = rows;
  styleAlternatingRows(sheet, 46, 50, "A", "P");
  sheet.getRange("A46:P50").format.rowHeight = 48;
}

// 02_SCENARIOS — preserve scenario formulas and correct the interpretation of capacity controls.
{
  const sheet = workbook.worksheets.getItem("02_SCENARIOS");
  setMergedText(sheet, "A2", "Scenario multipliers and accepted demand/grid controls are unchanged from v2.8. Thermoelectric controls, energy-implied diagnostics, adequacy benchmarks and runtime results are distinct evidence planes.");
  sheet.getRange("L5:L10").values = [
    ["55 GW Terna NET-efficient thermoelectric control; evolve a physical Terna-2024/GEM-reconciled fleet; no Slow uplift"],
    ["55 GW Terna NET-efficient thermoelectric control; reconcile physical technology/fuel stack to the national envelope"],
    ["55 GW Terna NET-efficient thermoelectric control; no inverse dispatch-led retirement or mechanical reduction"],
    ["~30 GW independent Terna adequacy benchmark + 18.9449 GW external Slow CDP proxy; neither is fixed p_nom"],
    ["~30 GW independent Terna adequacy benchmark; physical p_nom comes from the reconciled scenario fleet"],
    ["~30 GW independent Terna adequacy benchmark; additional FER/BESS may lower utilization without forced retirement"],
  ];
  sheet.getRange("P8:P10").values = [
    ["Base demand; FER/BESS -20%; accepted Slow grid. Run scarcity/ENS/residual-load and storage-depletion tests, then compare model-derived delta with 18.9449 GW; equality is not required."],
    ["PNIEC 2050 with 8 GW nuclear. Annual production anchors and selected CF diagnostics are separate from the physical plant/unit fleet and the Terna adequacy benchmark."],
    ["Higher FER/BESS and demand do not mechanically retire programmable units. Prices, utilization, curtailment and redundancy are endogenous runtime results."],
  ];
  sheet.getRange("A25:C26").values = [
    ["2040 Terna thermoelectric control GW", 55, "Terna net-efficient national control/envelope; includes biomass, geothermal and other included thermal categories"],
    ["2050 Terna adequacy benchmark GW", 30, "Approximate independent adequacy control; not exact p_nom, not additive and not a technology split"],
  ];

  styleSection(sheet, "A31:P31", "Programmable-capacity controls — distinct, non-additive quantities");
  sheet.getRange("A32:F32").values = [["Quantity", "Value", "Unit", "Evidence / class", "Correct workbook role", "Prohibited interpretation"]];
  styleHeader(sheet.getRange("A32:F32"));
  sheet.getRange("A33:F38").values = [
    ["2040 Terna thermoelectric control", null, "GW", "TERNA CAPACITY CONTROL", "National reconciliation envelope", "CCGT+OCGT=55 GW or add biomass/geothermal above it"],
    ["2050 energy-implied capacity diagnostic", null, "GW", "ENERGY-IMPLIED DIAGNOSTIC", "Annual-energy conversion cross-check", "Complete fleet, adequacy target or direct p_nom total"],
    ["2050 Terna adequacy benchmark", null, "GW", "TERNA ADEQUACY BENCHMARK", "Independent adequacy QA/control", "Exact p_nom or additive technology row"],
    ["Slow lost FER+BESS CDP", null, "GW", "PROJECT DERIVATION", "External adequacy-loss diagnostic", "Zonal thermal siting target"],
    ["Slow programmable-equivalent proxy", null, "GW", "QA / RECONCILIATION ONLY", "Compare with model-derived Slow capacity need", "Mandatory installed-capacity addition"],
    ["30 + proxy diagnostic", null, "GW", "QA / RECONCILIATION ONLY", "Communication/reference diagnostic", "IT2050_SLOW compulsory p_nom"],
  ];
  styleAlternatingRows(sheet, 33, 38, "A", "F");
  sheet.getRange("B33:B38").formulas = [
    ["=$B$25"],
    ["='09_TECH_COSTS'!$E$58"],
    ["=$B$26"],
    ["='09_TECH_COSTS'!$G$67"],
    ["='09_TECH_COSTS'!$G$68"],
    ["='09_TECH_COSTS'!$G$69"],
  ];
  sheet.getRange("B33:B38").format.numberFormat = "0.0000";
  sheet.getRange("A33:F38").format.rowHeight = 68;
}

// 04_GENERATION_BY_ZONE — retain accepted FER formulas and replace thermal-allocation logic with the Terna/GEM gate.
{
  const sheet = workbook.worksheets.getItem("04_GENERATION_BY_ZONE");
  setMergedText(sheet, "A2", "FER formulas and nuclear treatment are unchanged. Current/future thermoelectric capacity must start from the Terna 2024 NET-efficient zone x technology baseline, reconciled to GEM units; historical aggregate thermal shares are QA-only.");
  for (let row = 46; row <= 52; row += 1) {
    sheet.getRange(`B${row}`).values = [["Terna 2024 zone x technology baseline — detailed rows pending"]];
    sheet.getRange(`I${row}`).values = [["2024 NET efficient MW by province -> region -> market zone; then GEM August-2026 unit/status reconciliation"]];
    sheet.getRange(`J${row}`).values = [["DIRECT SOURCE CAPACITY — PENDING"]];
    sheet.getRange(`K${row}`).values = [["REQUIRES DATA; do not use historical aggregate shares or powerplantmatching MW as substitute"]];
  }
  sheet.getRange("A46:K52").format.rowHeight = 38;
  sheet.getRange("A55:K60").values = [
    ["Metric", "Unit", "2040 Slow", "2040 Base", "2040 High", "2050 Slow", "2050 Base", "2050 High", "Evidence class", "Source", "Treatment"],
    ["Terna net-efficient thermoelectric control / envelope", "GW", 55, 55, 55, null, null, null, "TERNA CAPACITY CONTROL", "Terna/DDS 2040 control", "Includes biomass, geothermal and other included thermoelectric categories; never CCGT+OCGT=55 by assumption"],
    ["Terna efficient programmable adequacy benchmark", "GW", null, null, null, 30, 30, 30, "TERNA ADEQUACY BENCHMARK", "Terna 2050 adequacy figures", "Independent external control; not exact p_nom and not additive to technology rows"],
    ["PNIEC/Terna 2050 non-nuclear programmable generation anchor", "TWh", null, null, null, null, 22.7, null, "DIRECT SOURCE ENERGY", "PNIEC/Terna 2050", "Base annual-output QA only; installed technology capacity is not implied without assumptions"],
    ["Natural-gas generation historical QA", "TWh", null, 59, null, null, null, null, "QA / RECONCILIATION ONLY", "DDS 2024", "Fuel-energy QA; not a technology-capacity split"],
    ["Hydrogen-fired turbine installed capacity", "GW", null, null, null, null, null, null, "QA / RECONCILIATION ONLY", "Sources reviewed", "Do not infer MW from hydrogen demand or generic hydrogen-capable narratives"],
  ];
  styleHeader(sheet.getRange("A55:K55"));
  styleAlternatingRows(sheet, 56, 60, "A", "K");
  sheet.getRange("C56:H60").format.numberFormat = "0.0000";
  sheet.getRange("A56:K60").format.rowHeight = 38;

  styleSection(sheet, "A62:K62", "Terna technology -> MEM preliminary mapping — capacity/fuel evidence remains separate");
  sheet.getRange("A63:K63").values = [["Terna technology", "MEM candidate", null, null, null, null, null, null, "Required qualification", "Evidence class", "Status"]];
  styleHeader(sheet.getRange("A63:K63"));
  const mappings = [
    ["Combined cycle", "CCGT", "Reconcile fuel; individual multi-fuel/CHP exceptions are not silently relabelled"],
    ["Combined-cycle CHP", "CCGT_CHP", "Preserve CHP flag and reconcile fuel/heat role"],
    ["Gas turbine", "OCGT", "Reconcile fuel; technology is not fuel"],
    ["Gas-turbine CHP", "OCGT_CHP", "Preserve CHP flag and reconcile fuel/heat role"],
    ["Internal combustion", "ICE", "Determine fuel, unit identity and materiality"],
    ["Internal-combustion CHP", "ICE_CHP", "Preserve CHP flag and determine fuel/heat role"],
    ["Condensing steam", "STEAM_CONDENSING", "Fuel evidence required"],
    ["Extraction/condensing", "STEAM_EXTRACTION", "CHP/heat role and fuel evidence required"],
    ["Back-pressure", "STEAM_BACKPRESSURE", "CHP/heat role and fuel evidence required"],
    ["Geothermal", "GEOTHERMAL", "Keep separate from fossil thermal and within thermoelectric envelope accounting"],
    ["Residual / other thermal", "OTHER_THERMAL", "Retain explicit residual; never force into CCGT/OCGT"],
  ];
  for (let i = 0; i < mappings.length; i += 1) {
    const row = 64 + i;
    const [terna, mem, qualification] = mappings[i];
    sheet.getRange(`A${row}:K${row}`).values = [[terna, mem, null, null, null, null, null, null, qualification, "PYPSA IMPLEMENTATION MAPPING", "REQUIRES DATA"]];
  }
  styleAlternatingRows(sheet, 64, 74, "A", "K");
  sheet.getRange("A64:K74").format.rowHeight = 34;
}

// 07_INTERZONAL_BY_ZONE — make the transport-model boundary explicit without changing accepted MW formulas.
{
  const sheet = workbook.worksheets.getItem("07_INTERZONAL_BY_ZONE");
  setMergedText(sheet, "A2", "Base/Slow/High accepted directional capacities are retained. Every direction is a fixed non-negative commercial PyPSA Link; no physical AC Line, impedance/KVL formulation or realized-flow schedule is used.");
  for (let row = 5; row <= 28; row += 1) {
    sheet.getRange(`L${row}`).values = [["Fixed non-extendable directional PyPSA Link; commercial transport limit, not physical AC transmission"]];
  }
}

// 08_FOREIGN_BY_ZONE — integrate the fixed external price-taking market pattern.
{
  const sheet = workbook.worksheets.getItem("08_FOREIGN_BY_ZONE");
  setMergedText(sheet, "A2", "Accepted directional MW controls remain unchanged. Each neighbour is represented later by an external bus, fixed virtual generator/load and fixed import/export Links; trade is endogenous and the artificial objective baseline is removed from reported costs.");
  for (let row = 5; row <= 20; row += 1) {
    const counterparty = String(sheet.getRange(`A${row}`).values?.[0]?.[0] ?? "");
    const isHandoffSet = ["France", "Switzerland", "Austria", "Slovenia", "Montenegro", "Greece"].includes(counterparty);
    const treatment = isHandoffSet
      ? "PROVISIONAL H29 mapping; external bus + fixed virtual generator/load + fixed directional Links; verify study-year topology, NTC/ATC, capacity side and prices"
      : "Existing v2.8 candidate outside the H29 provisional set; retain as scenario evidence only pending study-year bidding-zone/topology verification";
    sheet.getRange(`I${row}`).values = [[treatment]];
  }
  sheet.getRange("A5:J20").format.rowHeight = 32;
  setMergedText(sheet, "A40", "External mappings currently supported by the handoff are provisional: FR/CH/AT/SI -> NORD, GR -> SUD, ME -> CSUD. Existing Tunisia/Malta rows remain candidate scenario controls pending study-year verification. Report domestic cost and net external settlement separately; raw virtual-generator objective terms include a removable fixed-load baseline.");
}

// 09_TECH_COSTS — append the selected energy-capacity diagnostics and Slow CDP benchmark.
{
  const sheet = workbook.worksheets.getItem("09_TECH_COSTS");
  setMergedText(sheet, "A2", "Existing technology cost calculations are retained. Zonal deratings remain interim project assumptions; the 2050 energy conversions and Slow CDP values below are diagnostics/benchmarks, not a physical-fleet or adequacy target.");
  sheet.getRange("G37:G43").values = Array.from({ length: 7 }, () => ["INTERIM static derating; no complete annual availability dataset; preserve as sensitivity until empirical profile is frozen"]);
  sheet.getRange("A37:G43").format.rowHeight = 30;
  setMergedText(sheet, "A47", "Availability boundary: current zonal deratings are INTERIM PROJECT ASSUMPTIONS, not an empirical annual outage dataset. Long-duration unavailable units belong outside p_nom; scheduled maintenance and forced/unplanned availability must not be double counted.");

  styleSection(sheet, "A51:H51", "2050 PNIEC annual energy -> capacity diagnostics (not physical-fleet or adequacy targets)");
  sheet.getRange("A52:H52").values = [["Technology", "PNIEC 2050 TWh", "Capacity basis", "CF", "Derived / direct GW", "Evidence / status", "Model-input interpretation", "Source / note"]];
  styleHeader(sheet.getRange("A52:H52"));
  sheet.getRange("A53:H59").values = [
    ["Nuclear", 64.2, "DIRECT 8 GW CAPACITY", null, null, "DIRECT SOURCE CAPACITY + ENERGY", "Fixed 8 GW; annual TWh is a dispatch QA anchor", "PNIEC 2024 / Terna 2050"],
    ["Bioenergy", 10.6, "TWh / selected CF", 0.47, null, "PROJECT DERIVATION", "Technology capacity estimate under approved project CF", "User-approved observed-2025 CF=0.47"],
    ["Bioenergy + CCS", 6, "TWh / selected CF", 0.47, null, "PROJECT DERIVATION", "Technology capacity estimate under approved project CF", "User-approved observed-2025 CF=0.47"],
    ["Gas + CCS", 4, "TWh / interim CF", 0.2, null, "INTERIM PROJECT ASSUMPTION", "Energy-implied tranche only; not fleet-wide realized CF", "User-approved interim CF=0.20"],
    ["Gas + other fossil", 2.1, "TWh / interim CF", 0.2, null, "INTERIM PROJECT ASSUMPTION", "Energy-implied tranche only; not fleet-wide realized CF", "User-approved interim CF=0.20"],
    ["Energy-implied total", null, "Sum of rows above", null, null, "ENERGY-IMPLIED DIAGNOSTIC", "Diagnostic only; not source capacity, complete fleet or adequacy requirement", "Includes direct 8 GW nuclear plus selected TWh/CF conversions"],
    ["Terna efficient programmable adequacy benchmark", null, "Independent control", null, null, "TERNA ADEQUACY BENCHMARK", "Compare with the modelled physical fleet; do not force exact p_nom", "Terna 2050 adequacy material"],
  ];
  styleAlternatingRows(sheet, 53, 59, "A", "H");
  sheet.getRange("D53").formulas = [["=B53/(E53*8.76)"]];
  sheet.getRange("E53").formulas = [["='02_SCENARIOS'!$B$27"]];
  sheet.getRange("E54:E57").formulas = [
    ["=B54/(8.76*D54)"],
    ["=B55/(8.76*D55)"],
    ["=B56/(8.76*D56)"],
    ["=B57/(8.76*D57)"],
  ];
  sheet.getRange("B58").formulas = [["=SUM(B53:B57)"]];
  sheet.getRange("E58").formulas = [["=SUM(E53:E57)"]];
  sheet.getRange("E59").formulas = [["='02_SCENARIOS'!$B$26"]];
  sheet.getRange("B53:B59").format.numberFormat = "0.0000";
  sheet.getRange("D53:D57").format.numberFormat = "0.00%";
  sheet.getRange("E53:E59").format.numberFormat = "0.0000";
  sheet.getRange("A53:H59").format.rowHeight = 42;
  const guardrail = sheet.getRange("A60:H60");
  guardrail.merge();
  guardrail.values = [["Interpretation guardrail: 30 GW and 15.5136 GW are different evidence constructs. Do not subtract them to create a 14.4864 GW required capacity, standby requirement or gas allocation."]];
  guardrail.format = { fill: COLORS.paleBlue, font: { bold: true, color: COLORS.navy, fontSize: 9 }, borders: borderFormat(), wrapText: true };
  guardrail.format.rowHeight = 28;

  styleSection(sheet, "A62:H62", "Slow CDP calculation — external adequacy benchmark, never predetermined p_nom");
  sheet.getRange("A63:H63").values = [["Resource / quantity", "Base portfolio GW", "CDP numerator GW", "Installed denominator GW", "CDP ratio", "Slow reduction", "Lost / equivalent GW", "Interpretation / source"]];
  styleHeader(sheet.getRange("A63:H63"));
  sheet.getRange("A64:H69").values = [
    ["PV", null, 32, 180, null, 0.2, null, "Terna CDP relationship; project-derived Slow loss"],
    ["Wind", null, 29, 66, null, 0.2, null, "Terna CDP relationship; project-derived Slow loss"],
    ["BESS discharge power", null, 12, 36, null, 0.2, null, "Terna CDP relationship; project-derived Slow loss"],
    ["Total lost CDP", null, null, null, null, null, null, "PROJECT DERIVATION / adequacy-loss diagnostic"],
    ["Programmable-equivalent replacement proxy", null, null, 0.85, null, null, null, "QA / RECONCILIATION ONLY; compare with runtime delta"],
    ["30 GW + proxy diagnostic", null, null, null, null, null, null, "QA / RECONCILIATION ONLY — NOT FIXED P_NOM"],
  ];
  styleAlternatingRows(sheet, 64, 69, "A", "H");
  sheet.getRange("B64").formulas = [["=SUM('04_GENERATION_BY_ZONE'!$G$5,'04_GENERATION_BY_ZONE'!$G$6,'04_GENERATION_BY_ZONE'!$G$10,'04_GENERATION_BY_ZONE'!$G$11,'04_GENERATION_BY_ZONE'!$G$15,'04_GENERATION_BY_ZONE'!$G$16,'04_GENERATION_BY_ZONE'!$G$20,'04_GENERATION_BY_ZONE'!$G$21,'04_GENERATION_BY_ZONE'!$G$25,'04_GENERATION_BY_ZONE'!$G$26,'04_GENERATION_BY_ZONE'!$G$30,'04_GENERATION_BY_ZONE'!$G$31,'04_GENERATION_BY_ZONE'!$G$35,'04_GENERATION_BY_ZONE'!$G$36)"]];
  sheet.getRange("B65").formulas = [["='04_GENERATION_BY_ZONE'!$G$41-B64"]];
  sheet.getRange("B66").formulas = [["='05_STORAGE_BY_ZONE'!$G$45"]];
  sheet.getRange("E64:E66").formulas = [["=C64/D64"], ["=C65/D65"], ["=C66/D66"]];
  sheet.getRange("G64:G66").formulas = [["=B64*E64*F64"], ["=B65*E65*F65"], ["=B66*E66*F66"]];
  sheet.getRange("G67").formulas = [["=SUM(G64:G66)"]];
  sheet.getRange("G68").formulas = [["=G67/D68"]];
  sheet.getRange("G69").formulas = [["='02_SCENARIOS'!$B$26+G68"]];
  sheet.getRange("B64:G69").format.numberFormat = "0.0000";
  sheet.getRange("E64:F66").format.numberFormat = "0.000000";
  sheet.getRange("A64:H69").format.rowHeight = 44;
  const runtimeNote = sheet.getRange("A70:H70");
  runtimeNote.merge();
  runtimeNote.values = [["RUNTIME GATE: run calibrated Base and Slow chronological cases; observe load shedding, ENS, residual-load peaks, transmission saturation and storage depletion; derive retained/additional capacity by type/location; then compare the model-derived delta with 18.9449 GW. The CDP proxy is not a solver equality constraint."]];
  runtimeNote.format = { fill: COLORS.paleBlue, font: { italic: true, color: COLORS.navy, fontSize: 9 }, borders: borderFormat(), wrapText: true };
  runtimeNote.format.rowHeight = 52;
}

// 11_PYPSA_IMPLEMENTATION — update the independent runtime mapping and append the frozen contract.
{
  const sheet = workbook.worksheets.getItem("11_PYPSA_IMPLEMENTATION");
  setMergedText(sheet, "A1", "MEM independent seven-zone implementation map — v2.9");
  setMergedText(sheet, "A2", "Fresh fixed-capacity PyPSA model consuming frozen files. PyPSA-IT provides reviewed patterns/provenance only; no runtime import, physical AC network, redispatch, unit commitment, reserves or capacity expansion.");
  sheet.getRange("A14:L20").values = [
    ["Thermoelectric 2040 — Terna/GEM", "04_GENERATION_BY_ZONE / frozen generators.csv", "Generator portfolio", "fixed p_nom; technology and fuel separate", "Terna/GEM evolved physical fleet", "same control; no automatic uplift", "same control; no mechanical reduction", "Reconcile Terna 2024 NET capacity plus evidenced changes to ~55 GW national envelope", "TERNA CAPACITY CONTROL + GEM PLANT RECONCILIATION", "No fabricated split", "No 55 GW double count", "55 GW includes biomass/geothermal/other thermal; never infer CCGT+OCGT=55"],
    ["Programmable 2050 — independent controls", "04_GENERATION_BY_ZONE / 09_TECH_COSTS / frozen generators.csv", "Generator portfolio + runtime outputs", "p_nom, TWh, ENS/LOLE-like diagnostics", "Physical fleet + independent benchmarks", "run Slow stress; compare delta", "no inverse retirement", "Use 15.5136 GW only as energy diagnostic; compare physical fleet with ~30 GW adequacy benchmark", "DIRECT SOURCE ENERGY / DERIVATION / ADEQUACY / RUNTIME", "No additive totals", "No 14.4864 residual", "Do not force sum(p_nom)=30 or add 30 + 15.5136"],
    ["Hydrogen turbines", "Evidence / physical fleet only", "Generator", "p_nom", "Only explicit physical/project records", "same", "same", "Create only from individually evidenced project/plant data", "QA / RECONCILIATION ONLY", "No inferred MW", "Ambiguous units stay separate", "Hydrogen demand or generic hydrogen-capable narratives are insufficient"],
    ["Battery power", "05_STORAGE_BY_ZONE", "StorageUnit or Store+Links", "fixed charge/discharge p_nom", "Base", "Base x0.80", "Base x1.10", "Choose component by symmetric/asymmetric power and energy contract", "PROJECT DERIVATION", "No", "Zone sum=national", "All nominal capacities non-extendable"],
    ["Battery / hydro energy", "05_STORAGE_BY_ZONE / storage.csv / inflow parquet", "StorageUnit or Store+Links", "fixed e_nom; state rule", "Base", "Base x0.80 for BESS", "Base x1.10 for BESS", "Explicit energy, efficiency, standing loss, inflow and initial/terminal rule", "PROJECT DERIVATION / REQUIRES DATA", "No", "Storage conservation", "MACSE subset not summed; hydro empirical inputs remain open"],
    ["Internal transfers", "07_INTERZONAL_BY_ZONE / interzonal_capacities.csv", "Link", "fixed directional p_nom", "Accepted Base", "Base less delayed project effects", "Exactly Base", "One non-negative Link per direction; independent limits and capacity-side convention", "DIRECT SOURCE CAPACITY / PROJECT DERIVATION", "No", "Directional flow limits", "Transport model only; Italian Lines table empty"],
    ["External markets", "08_FOREIGN_BY_ZONE / external_interfaces.csv / prices parquet", "External Bus + Generator + Load + import/export Links", "fixed p_nom and hourly marginal cost", "Endogenous trade", "same", "same", "Price-taking neighbour; compute fixed safe auxiliary bound; report net settlement after removing baseline", "PYPSA IMPLEMENTATION MAPPING", "Mapping provisional", "Auxiliary bounds non-binding; cost reconciliation", "FR/CH/AT/SI->NORD, GR->SUD, ME->CSUD provisional; verify study-year topology"],
  ];
  styleAlternatingRows(sheet, 14, 20, "A", "L");
  sheet.getRange("A14:L20").format.rowHeight = 48;
  setMergedText(sheet, "A26", "Implementation order: validate frozen contracts -> create exactly seven Italian buses plus explicit external buses -> attach fixed generation/storage/load -> add fixed directional internal and external Links -> assert complete UTC year and no extendability -> solve continuous dispatch -> export direct seven-bus dual prices, balances, storage, scarcity, curtailment, trade and reconciled costs.");
  sheet.getRange("A26:L26").format.rowHeight = 40;

  styleSection(sheet, "A29:L29", "Frozen data contract — independent MEM runtime inputs");
  sheet.getRange("A30:F30").values = [["Frozen file", "Primary key / grain", "Runtime consumer", "Authoritative source / method", "Current status", "Gate / note"]];
  styleHeader(sheet.getRange("A30:F30"));
  sheet.getRange("A31:F40").values = [
    ["zones.csv", "zone", "build_network.py", "Official study-year market topology", "ARCHITECTURE ONLY", "Exactly NORD, CNOR, CSUD, SUD, CALA, SICI, SARD; validity still to freeze"],
    ["generators.csv", "generator_id", "add_generation.py", "Terna 2024 NET controls -> GEM reconciliation -> scenario evolution", "REQUIRES DATA", "No canonical thermal row may be powerplantmatching-only"],
    ["generator_availability_hourly.parquet", "timestamp, generator_id", "add_generation.py", "Weather/availability method", "REQUIRES DATA", "Complete UTC year; 0<=p_max_pu<=1"],
    ["load_hourly.parquet", "timestamp, zone", "build_network.py", "Authoritative zonal hourly load", "REQUIRES DATA", "Seven finite non-negative values per hour; use p_set"],
    ["storage.csv", "storage_id", "add_storage.py", "Authoritative power/energy/efficiency/status", "REQUIRES DATA", "Fixed power/energy and explicit state rule"],
    ["hydro_inflow_hourly.parquet", "timestamp, storage_id", "add_storage.py", "Selected weather-year hydro inflow", "REQUIRES DATA", "No placeholder or silent forward fill"],
    ["interzonal_capacities.csv", "link_id (one direction)", "build_network.py", "Official commercial directional limits", "REQUIRES DATA", "Static annual or hourly NTC/ATC; capacity side explicit"],
    ["external_interfaces.csv", "interface_id", "add_external_markets.py", "Official interface topology and directional limits", "REQUIRES DATA", "Provisional mapping may not be promoted to VERIFIED without evidence"],
    ["external_prices_hourly.parquet", "timestamp, external_bidding_zone", "add_external_markets.py", "Historical/modelled future day-ahead prices", "REQUIRES DATA", "Complete UTC series; future method disclosed"],
    ["cost_assumptions.csv", "assumption_id", "add_generation.py", "Frozen fuel/CO2/VOM/efficiency method", "INTERIM / PARTIAL", "Static versus hourly method exclusive and price basis explicit"],
  ];
  styleAlternatingRows(sheet, 31, 40, "A", "F");
  sheet.getRange("A31:F40").format.rowHeight = 56;

  styleSection(sheet, "A42:L42", "Network, solve and interpretation invariants");
  sheet.getRange("A43:D43").values = [["Invariant", "Requirement", "Validation stage", "Failure meaning"]];
  styleHeader(sheet.getRange("A43:D43"));
  sheet.getRange("A44:D53").values = [
    ["Italian buses", "Exactly seven: NORD, CNOR, CSUD, SUD, CALA, SICI, SARD", "Built network", "Architecture failure"],
    ["Capacity expansion", "Every Generator/Link/StorageUnit/Store nominal-capacity extendable flag is false", "Built + post-solve", "Scope failure"],
    ["Transport", "Commercial directional Links only; no Italian physical AC Lines/KVL", "Built network", "Scope failure"],
    ["Time", "Complete monotonic unique hourly UTC calendar year: 8,760 or 8,784", "Frozen contract", "Input failure"],
    ["Energy balance", "Every bus/hour residual below declared MW tolerance", "Solved network", "Numerical/model failure"],
    ["Storage", "Hourly state conservation including efficiency, loss, inflow, spill and terminal rule", "Solved network", "Chronology failure"],
    ["Load shedding", "Explicit high-penalty diagnostic slack; report ENS/scarcity by zone/hour", "Built + solved", "Baseline failure unless intended and explained"],
    ["External economics", "Raw virtual-generator cost minus fixed-load baseline reconciles to net purchases minus export revenue; tolls/losses separate", "Solved/export", "Cost-accounting failure"],
    ["Prices", "Direct balance duals of seven Italian buses; no nodal-to-zonal averaging", "Export", "Price-output failure"],
    ["Adequacy language", "Scarcity/ENS indicators only; no LOLE, probabilistic capacity credit, N-1 or formal Terna-adequacy claim", "Reporting", "Interpretation failure"],
  ];
  styleAlternatingRows(sheet, 44, 53, "A", "D");
  sheet.getRange("A44:D53").format.rowHeight = 48;
}

// 12_ASSUMPTIONS_QA — retain v2.8 gates and append v2.9 formula and decision controls.
{
  const sheet = workbook.worksheets.getItem("12_ASSUMPTIONS_QA");
  setMergedText(sheet, "A2", "Canonical v2.8 QA is retained. v2.9 appends architecture, source-hierarchy, energy-diagnostic, non-additivity, external-boundary and runtime-gate decisions; independent export/error/visual checks are in the v2.9 QA report.");
  styleSection(sheet, "A45:H45", "v2.9 architecture, formula and interpretation QA");
  sheet.getRange("A46:H46").values = [["Gate", "Area", "Check", "Actual", "Target", "Status", "Tolerance", "Note / evidence"]];
  styleHeader(sheet.getRange("A46:H46"));
  const qaRows = [
    ["QA-030", "Capacity diagnostic", "Bioenergy derived GW", null, 2.5745652385, null, 0.000001, "10.6 TWh / (8.76 x 0.47)"],
    ["QA-031", "Capacity diagnostic", "Bioenergy+CCS derived GW", null, 1.4573010784, null, 0.000001, "6.0 TWh / (8.76 x 0.47)"],
    ["QA-032", "Capacity diagnostic", "Gas+CCS interim energy-implied GW", null, 2.2831050228, null, 0.000001, "4.0 TWh / (8.76 x 0.20)"],
    ["QA-033", "Capacity diagnostic", "Gas+other fossil interim energy-implied GW", null, 1.198630137, null, 0.000001, "2.1 TWh / (8.76 x 0.20)"],
    ["QA-034", "Capacity diagnostic", "Energy-implied total incl. direct 8 GW nuclear", null, 15.5136014767, null, 0.000001, "Diagnostic only"],
    ["QA-035", "Non-additivity", "No 14.4864 numeric control or model-input value", null, 0, null, 0, "Negative guardrail text is permitted; no capacity row or formula may use the subtraction"],
    ["QA-036", "Double count", "2040 55-GW control appears in exactly three scenario cells", null, 3, null, 0, "No CCGT+OCGT allocation or second additive total"],
    ["QA-037", "Double count", "2050 30-GW benchmark appears in exactly three scenario cells", null, 3, null, 0, "Benchmark only; not technology rows"],
    ["QA-038", "Slow CDP", "Lost dependable-capacity proxy GW", null, 16.1031, null, 0.0001, "PV/wind/BESS lost CDP"],
    ["QA-039", "Slow CDP", "Programmable-equivalent proxy GW", null, 18.9449, null, 0.0001, "Divide lost CDP by 0.85"],
    ["QA-040", "Slow CDP", "30 + proxy remains diagnostic GW", null, 48.9449, null, 0.0001, "Never fixed p_nom"],
    ["QA-041", "Thermal hierarchy", "All preliminary Terna->MEM mapping rows carry implementation-mapping class", null, 11, null, 0, "Terna capacity / GEM reconciliation remains the authority"],
    ["QA-042", "Architecture", "All seven Italian market-zone codes are present", null, 7, null, 0, "Exactly seven Italian buses; external buses additional"],
    ["QA-043", "External markets", "Objective-baseline reconciliation rule is present", null, 1, null, 0, "Net external trade cost, not raw auxiliary objective"],
    ["QA-044", "Formula integrity", "Scenario-matrix numeric formula links retained", null, 48, null, 0, "48 linked cells remain in D:N of the six scenario rows; B20/B24 retain the two parameter formulas"],
  ];
  sheet.getRange("A47:H61").values = qaRows;
  styleAlternatingRows(sheet, 47, 61, "A", "H");
  sheet.getRange("D47:D61").formulas = [
    ["='09_TECH_COSTS'!$E$54"],
    ["='09_TECH_COSTS'!$E$55"],
    ["='09_TECH_COSTS'!$E$56"],
    ["='09_TECH_COSTS'!$E$57"],
    ["='09_TECH_COSTS'!$E$58"],
    ["=COUNTIF('02_SCENARIOS'!$B$33:$B$38,14.4864)+COUNTIF('04_GENERATION_BY_ZONE'!$C$46:$H$60,14.4864)+COUNTIF('09_TECH_COSTS'!$E$53:$E$59,14.4864)+COUNTIF('09_TECH_COSTS'!$G$64:$G$69,14.4864)"],
    ["=COUNT('04_GENERATION_BY_ZONE'!$C$56:$H$56)"],
    ["=COUNT('04_GENERATION_BY_ZONE'!$C$57:$H$57)"],
    ["='09_TECH_COSTS'!$G$67"],
    ["='09_TECH_COSTS'!$G$68"],
    ["='09_TECH_COSTS'!$G$69"],
    ["=COUNTIF('04_GENERATION_BY_ZONE'!$J$64:$J$74,\"PYPSA IMPLEMENTATION MAPPING\")"],
    ["=--(COUNTIF('03_ZONE_MAP'!$E$5:$E$24,\"NORD\")>0)+--(COUNTIF('03_ZONE_MAP'!$E$5:$E$24,\"CNOR\")>0)+--(COUNTIF('03_ZONE_MAP'!$E$5:$E$24,\"CSUD\")>0)+--(COUNTIF('03_ZONE_MAP'!$E$5:$E$24,\"SUD\")>0)+--(COUNTIF('03_ZONE_MAP'!$E$5:$E$24,\"CALA\")>0)+--(COUNTIF('03_ZONE_MAP'!$E$5:$E$24,\"SICI\")>0)+--(COUNTIF('03_ZONE_MAP'!$E$5:$E$24,\"SARD\")>0)"],
    ["=--ISNUMBER(SEARCH(\"baseline\",'11_PYPSA_IMPLEMENTATION'!$H$20))"],
    ["=COUNT('02_SCENARIOS'!$D$5:$N$10)"],
  ];
  for (let row = 47; row <= 61; row += 1) {
    sheet.getRange(`F${row}`).formulas = [[`=IF(ABS(D${row}-E${row})<=G${row},\"PASS\",\"FAIL\")`]];
  }
  sheet.getRange("D47:G61").format.numberFormat = "0.000000";
  sheet.getRange("F47:F61").format.fill = COLORS.pass;
  sheet.getRange("A47:H61").format.rowHeight = 34;

  styleSection(sheet, "A64:H64", "v2.9 superseding decision register — v2.8 audit trail retained");
  sheet.getRange("A65:H65").values = [["Decision ID", "Theme", "Class", "Decision", "Result / interpretation", "Status", "Supersedes / guards", "Evidence"]];
  styleHeader(sheet.getRange("A65:H65"));
  sheet.getRange("A66:H78").values = [
    ["D29-001", "Model boundary", "PYPSA IMPLEMENTATION MAPPING", "Build a fresh independent seven-zone fixed-capacity economic-dispatch model", "No PyPSA-IT runtime import; transport Links and external buses", "RESOLVED", "Supersedes reduced/full-system runtime concepts", "User scope + H29 architecture"],
    ["D29-002", "Current thermal capacity", "TERNA CAPACITY CONTROL", "Use Terna 2024 NET-efficient detailed thermoelectric data as canonical MW control", "Province -> region -> market-zone technology baseline", "REQUIRES DATA", "Supersedes PPM as capacity truth", "User rule + H29 hierarchy"],
    ["D29-003", "Plant reconciliation", "GEM PLANT RECONCILIATION", "Use GEM August-2026 for unit identity/status/technology/fuel/CHP/location and evidenced updates", "GEM never blindly overwrites Terna totals", "REQUIRES DATA", "Guards gross/net and coverage mismatch", "User rule + H29 hierarchy"],
    ["D29-004", "PyPSA/PPM", "PYPSA IMPLEMENTATION MAPPING", "Use only for coordinates, bus mapping, metadata gaps and implementation support", "No canonical current Italian thermal MW", "RESOLVED", "Supersedes earlier partial v2.9 physical-fleet authority", "User rule"],
    ["D29-005", "2040 thermoelectric", "TERNA CAPACITY CONTROL", "Retain ~55 GW national net-efficient thermoelectric control in Slow/Base/High", "Not 55 GW gas; reconcile full technology stack including biomass/geothermal", "RESOLVED", "Guards 55-GW double count", "User rule"],
    ["D29-006", "2050 energy conversion", "ENERGY-IMPLIED DIAGNOSTIC", "Use CF 0.47/0.47/0.20/0.20 plus direct 8 GW nuclear", "15.5136 GW diagnostic only", "RESOLVED", "Supersedes obsolete 10%/5% gas factors and 21.3926 GW branch", "User-approved CFs"],
    ["D29-007", "Adequacy residual", "QA / RECONCILIATION ONLY", "Do not interpret 30-15.5136 as 14.4864 GW required capacity", "No residual row, target or p_nom formula", "RESOLVED", "Supersedes arithmetic residual interpretation", "User controlling correction"],
    ["D29-008", "2050 adequacy", "TERNA ADEQUACY BENCHMARK", "Retain ~30 GW as independent Terna adequacy control", "Compare with physical fleet; do not force equality", "RESOLVED", "Guards additive capacity construction", "User rule"],
    ["D29-009", "Slow CDP", "QA / RECONCILIATION ONLY", "Retain 16.1031 lost CDP, 18.9449 proxy and 48.9449 diagnostic only", "Compare runtime-derived Slow delta with 18.9449; no fixed input", "RESOLVED", "Supersedes fixed 48.9449-GW Slow p_nom reading", "Terna CDP ratios + project derivation"],
    ["D29-010", "Slow / High asymmetry", "INTERIM PROJECT ASSUMPTION", "Do not add Slow capacity or retire High capacity mechanically", "Runtime determines scarcity, utilization, redundancy, curtailment and price impacts", "RUNTIME GATE", "Guards scenario multiplier misuse", "User rule"],
    ["D29-011", "External markets", "PYPSA IMPLEMENTATION MAPPING", "Use fixed virtual generator/load and fixed import/export Links per external bus", "Endogenous trade; remove auxiliary objective baseline in cost reporting", "REQUIRES DATA", "Guards historical-flow schedules and raw-objective misreading", "H29 external-market handoff"],
    ["D29-012", "Adequacy claims", "RUNTIME VALIDATION RESULT", "Deterministic annual dispatch may report scarcity/ENS indicators only", "No LOLE, probabilistic capacity credit, N-1 or formal Terna adequacy claim", "RESOLVED", "Guards over-interpretation", "User rule + H29 validation"],
    ["D29-013", "Frozen runtime inputs", "PYPSA IMPLEMENTATION MAPPING", "Consume versioned frozen files; no live external-source or PyPSA-IT import during solve", "Independent reproducible repository boundary", "RESOLVED", "Guards hidden coupling and mutable data", "H29 data contract"],
  ];
  styleAlternatingRows(sheet, 66, 78, "A", "H");
  sheet.getRange("A66:H78").format.rowHeight = 48;
}

// 13_CHANGELOG — retain v2.8 entries and append this phase's audited changes.
{
  const sheet = workbook.worksheets.getItem("13_CHANGELOG");
  setMergedText(sheet, "A1", "Workbook changelog — v2.9 MEM architecture refinement");
  setMergedText(sheet, "A2", "The accepted v2.8 scenario/demand/FER/storage/grid/cost/nuclear logic is retained. This section records only the architecture, thermal-source, adequacy and contract refinements made from the verified v2.8 binary.");
  styleSection(sheet, "A17:H17", "v2.9 targeted methodology and architecture changes");
  sheet.getRange("A18:H18").values = [["ID", "Sheet / artifact", "Change", "Before", "After", "Evidence / rule", "Class", "QA"]];
  styleHeader(sheet.getRange("A18:H18"));
  sheet.getRange("A19:H30").values = [
    ["V29-001", "Baseline", "Canonical binary pin", "User request referenced a possible (1) suffix", "Workspace v2.8 without suffix; SHA-256 e339c55c5996ed6a23fcee18e9f7ee51b27d29464d9211028332f8c4ebdeabf2", "Filesystem audit", "Governance", "Hash verified"],
    ["V29-002", "Handoff", "Archive integrity", "Expected ZIP absent at external handoff path", "Identical named ZIP located in MEM root; SHA-256 matches 7f4ad4...bb244; unpacked package read completely", "User expected hash", "Provenance", "PASS"],
    ["V29-003", "00_README", "Model boundary", "Generic model-ready framing", "Independent seven-zone fixed-capacity transport dispatch; empirical gates open", "User scope + H29", "Methodological correction", "Reviewed"],
    ["V29-004", "02_SCENARIOS", "Capacity controls", "55-GW envelope and >=30-GW floor wording", "55-GW Terna control, 30-GW adequacy benchmark, Slow CDP diagnostics and High/Slow asymmetry separated", "User rule", "Benchmark/control", "QA-036:040"],
    ["V29-005", "04_GENERATION_BY_ZONE", "Thermal hierarchy", "Blank generic zonal dispatchable rows", "Terna 2024 zone x technology -> GEM reconciliation -> scenario evolution; PPM non-authoritative", "User rule + H29", "Source hierarchy", "QA-041"],
    ["V29-006", "09_TECH_COSTS", "2050 energy diagnostics", "No selected conversion table in v2.8", "0.47/0.47/0.20/0.20 CF formulas; 15.5136 GW diagnostic", "User-approved CFs", "Derived/diagnostic", "QA-030:034"],
    ["V29-007", "09_TECH_COSTS", "Slow CDP benchmark", "Not exposed in v2.8", "16.1031 lost CDP; 18.9449 proxy; 48.9449 diagnostic only", "Terna ratios + project derivation", "External benchmark", "QA-038:040"],
    ["V29-008", "07/08 network sheets", "Transport and external boundary", "Generic endogenous border treatment", "Fixed directional internal Links; external bus + fixed virtual generator/load + import/export Links; net cost baseline rule", "H29", "Implementation mapping", "Reviewed"],
    ["V29-009", "11_PYPSA_IMPLEMENTATION", "Independent contract", "PyPSA-Eur implementation map", "Independent modules, ten frozen inputs and network/solve invariants", "User scope + H29", "Repository/data contract", "Reviewed"],
    ["V29-010", "12_ASSUMPTIONS_QA", "Superseding decisions", "v2.8 audit trail only", "v2.9 formula/interpretation QA plus D29-001:D29-013 appended", "User rule", "Audit trail", "PASS subject to external QA"],
    ["V29-011", "Unrelated workbook sheets", "Preservation", "Accepted v2.8 content", "03_ZONE_MAP, 05_STORAGE_BY_ZONE, 06_DEMAND_BY_ZONE and 10_NUCLEAR unchanged", "User preservation rule", "No scope expansion", "Range comparison"],
    ["V29-012", "Deliverables", "Methodology and QA handoff", "v2.8 companion set", "v2.9 architecture, integration, contract, gap, repository and QA notes", "User required outputs", "Audit trail", "Verified separately"],
  ];
  styleAlternatingRows(sheet, 19, 30, "A", "H");
  sheet.getRange("A19:H30").format.rowHeight = 44;
}

// Add source/assumption comments when the facade supports threaded comments.
let commentsStatus = "not attempted";
try {
  await workbook.comments.setSelf({ displayName: "Emiliano Barin" });
  await workbook.comments.addThread({ cell: workbook.worksheets.getItem("09_TECH_COSTS").getRange("D54") }, "User-approved bioenergy CF = 0.47, based on the selected observed 2025 average and held constant for this diagnostic conversion.");
  await workbook.comments.addThread({ cell: workbook.worksheets.getItem("09_TECH_COSTS").getRange("D56") }, "Interim gas+CCS conversion CF = 0.20. This converts annual energy to a diagnostic capacity tranche; it is not a realized fleet-wide PyPSA capacity factor.");
  await workbook.comments.addThread({ cell: workbook.worksheets.getItem("09_TECH_COSTS").getRange("E58") }, "Energy-implied capacity diagnostic only. It is not an installed-capacity target, adequacy target, complete fleet total or direct p_nom total.");
  await workbook.comments.addThread({ cell: workbook.worksheets.getItem("09_TECH_COSTS").getRange("G68") }, "External Slow adequacy benchmark. Compare with the model-derived Slow capacity delta after runtime stress tests; do not impose it as an equality constraint.");
  commentsStatus = "added";
} catch (error) {
  commentsStatus = `comments unavailable: ${String(error?.message ?? error)}`;
}

const preservedAfter = Object.fromEntries(
  preservedSheetNames.map((name) => [name, matrixSnapshot(name)]),
);
const preservedComparisons = Object.fromEntries(
  preservedSheetNames.map((name) => [name, preservedBefore[name] === preservedAfter[name]]),
);

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const outputBlob = await SpreadsheetFile.exportXlsx(workbook);
await outputBlob.save(outputPath);

// Re-import the exported workbook for verification and render every sheet.
await fs.mkdir(verificationDir, { recursive: true });
const finalBlob = await FileBlob.load(outputPath);
const finalWorkbook = await SpreadsheetFile.importXlsx(finalBlob);

function recordsFromNdjson(ndjson) {
  return String(ndjson ?? "")
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => {
      try { return JSON.parse(line); } catch { return null; }
    })
    .filter(Boolean);
}

const sheetInspect = await finalWorkbook.inspect({ kind: "sheet", include: "id,name", maxChars: 20000 });
const sheetNames = [...new Set(recordsFromNdjson(sheetInspect.ndjson).map((r) => r.name ?? r.sheet?.name).filter(Boolean))];
let totalFormulaCount = 0;
const sheetSummary = [];
const renderDir = path.join(verificationDir, "renders");
await fs.mkdir(renderDir, { recursive: true });

for (let i = 0; i < sheetNames.length; i += 1) {
  const name = sheetNames[i];
  const sheet = finalWorkbook.worksheets.getItem(name);
  const used = sheet.getUsedRange();
  const formulas = used?.formulas ?? [];
  let count = 0;
  for (const row of formulas) {
    for (const cell of row ?? []) {
      if (typeof cell === "string" && cell.startsWith("=")) count += 1;
    }
  }
  totalFormulaCount += count;
  sheetSummary.push({ name, usedAddress: used?.address ?? null, formulaCount: count });
  const preview = await finalWorkbook.render({ sheetName: name, autoCrop: "all", scale: 1, format: "png" });
  const safe = name.replace(/[\\/:*?\"<>|]/g, "_");
  await fs.writeFile(path.join(renderDir, `${String(i + 1).padStart(2, "0")}_${safe}.png`), new Uint8Array(await preview.arrayBuffer()));
}

const formulaErrors = await finalWorkbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 500 },
  summary: "v2.9 final formula error scan",
  maxChars: 30000,
});
const residualMatches = await finalWorkbook.inspect({
  kind: "match",
  searchTerm: "14.4864|required residual|required capacity",
  options: { useRegex: true, maxResults: 200 },
  summary: "prohibited residual wording scan",
  maxChars: 30000,
});
const qaTable = await finalWorkbook.inspect({
  kind: "table,formula",
  sheetId: "12_ASSUMPTIONS_QA",
  range: "A45:H78",
  maxChars: 30000,
  tableMaxRows: 40,
  tableMaxCols: 8,
  tableMaxCellChars: 250,
  options: { maxResults: 500 },
});
const techTable = await finalWorkbook.inspect({
  kind: "table,formula",
  sheetId: "09_TECH_COSTS",
  range: "A51:H70",
  maxChars: 30000,
  tableMaxRows: 25,
  tableMaxCols: 8,
  tableMaxCellChars: 250,
  options: { maxResults: 500 },
});

const validation = {
  inputPath,
  outputPath,
  sheetCount: sheetNames.length,
  sheetSummary,
  totalFormulaCount,
  formulaErrors: formulaErrors.ndjson,
  prohibitedResidualScan: residualMatches.ndjson,
  preservedComparisons,
  commentsStatus,
  qaRange: qaTable.ndjson,
  techDiagnosticRange: techTable.ndjson,
};
await fs.writeFile(path.join(verificationDir, "validation.json"), JSON.stringify(validation, null, 2), "utf8");
console.log(JSON.stringify({
  outputPath,
  sheetCount: sheetNames.length,
  totalFormulaCount,
  formulaErrors: formulaErrors.ndjson,
  preservedComparisons,
  commentsStatus,
}, null, 2));

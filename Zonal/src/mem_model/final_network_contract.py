"""Non-solving Terna/MEM network-contract reconciliation for closure Phase C.

Capacity authority comes from the frozen delay register and workbook, never
from a network. No source contract, network, STATE, or result is modified here.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

from .common import ROOT, SCENARIOS, ZONES, PRICE_MARKETS, dump_json, sha256_file

QA = ROOT / "qa/final_methodology_closure/network_contract"
REGISTER = ROOT.parent / "MEM_v2.8_NETWORK_DELAY_REGISTER.csv"
RULES = ROOT.parent / "MEM_v2.8_SCENARIO_RULES.csv"
WORKBOOK = ROOT.parent / "PyPSA_IT_2040_2050_SCENARIOS.xlsx"
INTERNAL = ROOT / "pre_pypsa_inputs/MEM_Interzonal_Static_Contract.csv"
EXTERNAL = ROOT / "pre_pypsa_inputs/MEM_External_Interface_Static_Contract.csv"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
BASE = "Base 2040 directional capacity contribution"
SLOW = "Slow 2040 directional capacity contribution"


class NetworkContractError(RuntimeError):
    """A frozen source conflict or invalid transport bound must fail closed."""


def _require(condition, reason):
    if not condition:
        raise NetworkContractError(reason)


def _translate_shared_formula(formula: str, origin: str, target: str) -> str:
    def coordinate(value):
        column, row = re.fullmatch(r"([A-Z]+)(\d+)", value).groups()
        number = 0
        for letter in column:
            number = number * 26 + ord(letter) - 64
        return number, int(row)

    old_col, old_row = coordinate(origin)
    new_col, new_row = coordinate(target)

    def shift(match):
        fixed_col, column, fixed_row, row = match.groups()
        number = coordinate(column + "1")[0]
        number += 0 if fixed_col else new_col - old_col
        letters = ""
        while number:
            number, remainder = divmod(number - 1, 26)
            letters = chr(65 + remainder) + letters
        row = int(row) + (0 if fixed_row else new_row - old_row)
        return f"{fixed_col}{letters}{fixed_row}{row}"

    return re.sub(r"(\$?)([A-Z]{1,3})(\$?)([1-9]\d*)", shift, formula)


def xlsx_cells(path: Path, sheet: str) -> dict:
    """Read relevant values/formulas without refreshing or writing the XLSX."""
    with ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
        match = [s for s in workbook.find("m:sheets", NS) if s.attrib["name"] == sheet]
        _require(len(match) == 1, f"WORKBOOK_SHEET_MISSING:{sheet}")
        rel_id = match[0].attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        target = targets[rel_id]
        filename = target.lstrip("/") if target.startswith("/") else "xl/" + target
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(t.text or "" for t in node.iterfind(".//m:t", NS))
                      for node in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        cells, shared_formulas, references = {}, {}, {}
        for cell in ET.fromstring(archive.read(filename)).iterfind(".//m:c", NS):
            value = cell.find("m:v", NS)
            formula = cell.find("m:f", NS)
            raw = value.text if value is not None else None
            kind = cell.attrib.get("t")
            if kind == "s":
                raw = shared[int(raw)]
            elif kind == "inlineStr":
                raw = "".join(t.text or "" for t in cell.iterfind(".//m:t", NS))
            elif raw is not None and kind not in {"str", "e"}:
                raw = float(raw)
            cells[cell.attrib["r"]] = {"value": raw,
                "formula": formula.text if formula is not None else None}
            if formula is not None and formula.attrib.get("t") == "shared":
                identifier = formula.attrib["si"]
                references[cell.attrib["r"]] = identifier
                if formula.text:
                    shared_formulas[identifier] = (cell.attrib["r"], formula.text)
        for reference, identifier in references.items():
            _require(identifier in shared_formulas, "SHARED_FORMULA_ANCHOR_MISSING")
            origin, formula = shared_formulas[identifier]
            cells[reference]["formula"] = _translate_shared_formula(formula, origin, reference)
        return cells


def _directions(value):
    return [tuple(d.strip().split("->")) for d in value.split(";")]


def register_authority(register: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Derive directional totals independently from source project deltas."""
    _require(len(register) == 78, "DELAY_REGISTER_ROW_COVERAGE_CHANGED")
    summaries = register[register["Project ID"].str.startswith("SUMMARY-")]
    projects = register[~register["Project ID"].str.startswith("SUMMARY-")]
    _require(len(summaries) == 40 and len(projects) == 38, "DELAY_REGISTER_GRAIN_CHANGED")
    excluded = projects[(projects["Included in Base 2040?"] == "Yes") &
                        (projects["Included in Slow 2040 after delay?"] == "No")]
    _require(set(excluded["Project ID"]) == {"546-P", "563-P", "732-P"},
             "UNEXPECTED_SLOW_DELAY_EXCLUSION")
    for _, row in projects.iterrows():
        original = str(row["Original COD or COD range"])
        delayed = str(row["Slow COD / range = original + 2 years"])
        years = [int(y) for y in re.findall(r"20\d{2}", original)]
        shifted = [int(y) for y in re.findall(r"20\d{2}", delayed)]
        if row["Project ID"] == "3-P":
            _require("unchanged commissioned" in delayed, "COMMISSIONED_PROJECT_DELAYED")
        elif years:
            _require(shifted == [y + 2 for y in years],
                     f"PROJECT_DELAY_NOT_EXACT_PLUS_TWO:{row['Project ID']}")
        if row["Project ID"] in {"361-N", "723/E-P", "723/W-P"}:
            _require(row["Included in Slow 2040 after delay?"] == "Yes",
                     f"INCLUDED_PROJECT_WRONGLY_EXCLUDED:{row['Project ID']}")
    reductions = {}
    for _, row in excluded.iterrows():
        _require(float(row[SLOW]) == 0, "EXCLUDED_PROJECT_HAS_SLOW_INCREMENT")
        for edge in _directions(row["Direction(s)"]):
            reductions[edge] = reductions.get(edge, 0.0) + float(row[BASE])
    expected = []
    for _, row in summaries.iterrows():
        edges = _directions(row["Direction(s)"])
        _require(len(edges) == 1, "SUMMARY_NOT_SINGLE_DIRECTION")
        edge = edges[0]
        base = float(row[BASE])
        slow = base - reductions.get(edge, 0.0)
        _require(slow == float(row[SLOW]), f"REGISTER_SUMMARY_PROJECT_DELTA_CONFLICT:{edge}")
        for year, scenario in SCENARIOS:
            expected.append({"year": year, "scenario": scenario, "from_zone": edge[0],
                "to_zone": edge[1], "expected_MW": slow if scenario == "Slow" else base,
                "Base_MW": base, "Slow_removed_MW": reductions.get(edge, 0.0),
                "source": row["Source"], "source_location": row["Page/table"],
                "register_project_id": row["Project ID"],
                "classification": row["Internal / cross-border"]})
    frame = pd.DataFrame(expected)
    _require(not frame.duplicated(["year", "scenario", "from_zone", "to_zone"]).any(),
             "DUPLICATE_AUTHORITY_DIRECTION")
    _require(len(frame) == 240, "EXPECTED_DIRECTION_COVERAGE_FAIL")
    _require(sum(reductions.values()) == 15200, "SLOW_DIRECTIONAL_DELTA_FAIL")
    return frame, {"project_rows": 38, "summary_rows": 40,
        "excluded_project_ids": sorted(set(excluded["Project ID"])),
        "Slow_sum_directional_delta_MW": -sum(reductions.values()),
        "361_N_2036_to_2038_included": True,
        "Tyrrhenian_East_and_West_included_in_Slow": True,
        "2050_no_additional_Hypergrid_capacity": True}


def current_static_directions() -> pd.DataFrame:
    internal = pd.read_csv(INTERNAL)[["year", "scenario", "from_zone", "to_zone", "capacity_MW"]]
    external = pd.read_csv(EXTERNAL)
    rows = []
    for year, scenario in SCENARIOS:
        for row in external.itertuples(index=False):
            _require(str(year) in row.applicable_years.split("|") and
                     scenario in row.applicable_scenarios.split("|"), "EXTERNAL_SCOPE_CHANGED")
            a, b = (row.external_market, row.Italian_zone) if row.direction == "IMPORT" else (
                    row.Italian_zone, row.external_market)
            _require(row.direction in {"IMPORT", "EXPORT"}, "EXTERNAL_DIRECTION_INVALID")
            rows.append({"year": year, "scenario": scenario,
                "from_zone": a, "to_zone": b, "capacity_MW": float(row.capacity_MW)})
    frame = pd.concat([internal, pd.DataFrame(rows)], ignore_index=True)
    _require(not frame.duplicated(["year", "scenario", "from_zone", "to_zone"]).any(),
             "DUPLICATE_STATIC_DIRECTION")
    return frame


def _workbook_qa(expected: pd.DataFrame, register: pd.DataFrame) -> dict:
    scenario = xlsx_cells(WORKBOOK, "02_SCENARIOS")
    ids = [scenario[f"A{row}"]["value"] for row in range(5, 11)]
    _require(set(ids) == {f"IT{year}_{name.upper()}" for year, name in SCENARIOS},
             "WORKBOOK_SCENARIO_SCOPE_CONFLICT")
    comparisons = 0
    for sheet, start, end in [("07_INTERZONAL_BY_ZONE", 5, 28),
                              ("08_FOREIGN_BY_ZONE", 5, 20)]:
        cells = xlsx_cells(WORKBOOK, sheet)
        for row in range(start, end + 1):
            if sheet.startswith("07_"):
                a, b = cells[f"A{row}"]["value"], cells[f"B{row}"]["value"]
                _require(cells[f"C{row}"]["formula"] ==
                    f'D{row}-SUMIF($C$35:$C$83,A{row}&"->"&B{row},$I$35:$I$83)',
                    "WORKBOOK_SLOW_FORMULA_CHANGED")
            else:
                a, b = cells[f"B{row}"]["value"].split("->")
                _require(cells[f"C{row}"]["formula"] == f"D{row}",
                         "FOREIGN_OVERLAY_ADDED_TO_FLOOR")
            for column, (year, name) in zip("CDEFGH", SCENARIOS):
                match = expected[(expected.year == year) & (expected.scenario == name) &
                                 (expected.from_zone == a) & (expected.to_zone == b)]
                _require(len(match) == 1, "WORKBOOK_DIRECTION_UNEXPECTED")
                _require(cells[f"{column}{row}"]["value"] == float(match.iloc[0].expected_MW),
                         f"WORKBOOK_REGISTER_CAPACITY_CONFLICT:{sheet}:{column}{row}")
                if column in "EFGH":
                    ref = {"E": "D", "F": "C", "G": "D", "H": "G"}[column]
                    _require(cells[f"{column}{row}"]["formula"] == f"{ref}{row}",
                             "WORKBOOK_HORIZON_OR_HIGH_RULE_CHANGED")
                comparisons += 1
    implementation = xlsx_cells(WORKBOOK, "11_PYPSA_IMPLEMENTATION")
    _require(implementation["A19"]["value"] == "Internal links" and
             implementation["G19"]["value"] == "Exactly Base", "IMPLEMENTATION_MAPPING_CONFLICT")
    internal = xlsx_cells(WORKBOOK, "07_INTERZONAL_BY_ZONE")
    effects = []
    for row in range(35, 84):
        effects.append((internal[f"A{row}"]["value"], internal[f"C{row}"]["value"],
                        internal[f"D{row}"]["value"], internal[f"I{row}"]["value"]))
    source_effects = []
    for _, row in register.iterrows():
        if row["Internal / cross-border"] != "Internal" or row["Project ID"].startswith("SUMMARY-"):
            continue
        for a, b in _directions(row["Direction(s)"]):
            source_effects.append((row["Project ID"], f"{a}->{b}", float(row[BASE]),
                                  float(row[BASE]) - float(row[SLOW])))
    _require(sorted(effects) == sorted(source_effects), "WORKBOOK_49_PROJECT_EFFECTS_CONFLICT")
    return {"directional_values_checked": comparisons, "project_effect_rows_checked": len(effects),
            "scenario_ids": ids, "cached_values_and_native_formula_links": "PASS"}


def _ledger(register: pd.DataFrame) -> pd.DataFrame:
    rows = []
    rating = {"436-P": (1000.0, "S05 printed p32: nominal 1000 MW"),
              "301-P": (400.0, "S05 printed p32: complete SACOI rating 400 MW; section increment 100 MW"),
              "732-P": (1000.0, "S05 printed p32: SAPEI2 subcomponent 1000 MW, not entire portfolio rating"),
              "723/E-P": (1000.0, "Accepted branch contribution/rating; S03 Table2"),
              "723/W-P": (1000.0, "Accepted branch contribution/rating; S03 Table2"),
              "601-I": (600.0, "Accepted ELMED rating already embedded in DDS 600/600 floor"),
              "401-S": (600.0, "S05 printed p52: second 600 MW pole conditional; no accepted COD")}
    summaries = register[register["Project ID"].str.startswith("SUMMARY-")]
    summary_limits = {_directions(r["Direction(s)"])[0]: (float(r[BASE]), float(r[SLOW]))
                      for _, r in summaries.iterrows()}
    for number, row in register.iterrows():
        project = row["Project ID"]
        summary = project.startswith("SUMMARY-")
        internal = row["Internal / cross-border"] == "Internal"
        included = str(row["Included in Base 2040?"]).startswith("Yes")
        status = "SUMMARY_TOTAL_NOT_ADDITIVE" if summary else (
            "EMBEDDED_IN_CORRIDOR_TOTAL_NOT_OVERLAY" if internal else (
                "EMBEDDED_IN_FOREIGN_FLOOR_NOT_OVERLAY" if included else "SEPARATE_EXCLUDED_OVERLAY"))
        directions = row["Direction(s)"]
        if project == "301-P":
            representation = "CNOR-CORS-SARD two distinct hub legs, 400 MW/direction; no direct CNOR-SARD"
        elif summary or internal or included:
            representation = "Accepted directional limits; Phase N signed net bounds; no increment added again"
        else:
            representation = "No added Link/capacity; accepted foreign floor unchanged"
        nominal, nominal_note = rating.get(project, (np.nan,
            "Not an exact engineering rating in the accepted register; do not infer from market-section effect"))
        if project in {"355-P", "447-P", "563-P"}:
            nominal_note = "S05 printed p32-33 gives >2 GW technological capability; no fabricated exact nominal rating"
        directional_totals = []
        if "->" in directions:
            for a, b in _directions(directions):
                if (a, b) in summary_limits:
                    final_base, final_slow = summary_limits[a, b]
                    directional_totals.append(f"{a}->{b}: Base/High={final_base:g}; Slow={final_slow:g}")
        elif included and project == "601-I":
            directional_totals.append("TN->SICI:600; SICI->TN:600; all six scenarios")
        rows.append({"register_row": int(number + 2), "project_id": project,
            "project_name": row["Project name"], "internal_cross_border": row["Internal / cross-border"],
            "corridor": row["Corridor / border"], "directions": directions,
            "nominal_project_MW": nominal, "nominal_rating_evidence": nominal_note,
            "market_section_increment_MW": float(row["Capacity increment MW"]),
            "source": row["Source"], "source_location": row["Page/table"],
            "original_COD": row["Original COD or COD range"],
            "Slow_COD_plus_two_years": row["Slow COD / range = original + 2 years"],
            "Base_inclusion": row["Included in Base 2040?"],
            "Slow_inclusion": row["Included in Slow 2040 after delay?"],
            "Base_2040_contribution_MW": float(row[BASE]),
            "Slow_2040_contribution_MW": float(row[SLOW]),
            "2050_treatment": "2040 Base/High floor; Slow exactly 2040 Slow; no additional portfolio MW",
            "embedded_vs_overlay": status, "current_MEM_representation": representation,
            "accepted_corridor_totals_MW": " | ".join(directional_totals),
            "status": "PASS", "source_uncertainty_retained": row["Status / uncertainty note"]})
    return pd.DataFrame(rows)


def recover_authority(*, persist: bool = True):
    register = pd.read_csv(REGISTER)
    expected, checks = register_authority(register)
    rules = pd.read_csv(RULES)
    _require(set(rules.scenario_id) == {f"IT{y}_{s.upper()}" for y, s in SCENARIOS},
             "SCENARIO_RULE_SCOPE_CONFLICT")
    rule = rules.set_index("scenario_id")
    _require(rule.loc["IT2040_SLOW", "internal_network_rule"] ==
        "Project-by-project original COD + 2 years; include delayed COD <= 2040", "SLOW_RULE_CHANGED")
    _require(rule.loc["IT2050_SLOW", "internal_network_rule"] ==
        "Exactly the recomputed IT2040_SLOW directional matrix", "2050_SLOW_FLOOR_CHANGED")
    for year in (2040, 2050):
        _require(rule.loc[f"IT{year}_HIGH", "internal_network_rule"] == "Exactly Base; no acceleration",
                 "HIGH_NETWORK_ACCELERATED")
    checks["workbook"] = _workbook_qa(expected, register)
    current = current_static_directions()
    comparison = expected.merge(current, on=["year", "scenario", "from_zone", "to_zone"],
                                how="outer", validate="one_to_one", indicator=True)
    _require((comparison._merge == "both").all(), "STATIC_AUTHORITY_EDGESET_CONFLICT")
    comparison["static_difference_MW"] = comparison.capacity_MW - comparison.expected_MW
    comparison["status"] = np.where(comparison.static_difference_MW == 0, "PASS", "CORRECTION_REQUIRED")
    comparison = comparison.drop(columns="_merge")
    checks["current_static_directions_checked"] = len(comparison)
    checks["current_static_drift_rows"] = int((comparison.status != "PASS").sum())
    sources = [REGISTER, RULES, WORKBOOK, INTERNAL, EXTERNAL,
               ROOT.parent / "MEM_v2.8_WORKBOOK_CHANGELOG.md", ROOT.parent / "MEM_v2.8_QA_REPORT.md"]
    sources += [ROOT.parent / "03_PRIMARY_SOURCES" / name for name in (
        "Terna_PdS_2025_Benefici_robustezza_rete.pdf",
        "Terna_PdS_2025_Esigenze_sviluppo_nuovi_progetti.pdf", "Documento_Descrizione_Scenari_2024.pdf")]
    receipt = {"state": "NETWORK_CONTRACT_AUTHORITY_RECOVERED_PREPARATORY_EVIDENCE_ONLY",
        "checks": checks, "sources": [{"path": str(p), "sha256": sha256_file(p)} for p in sources],
        "official_source_locations": {
            "S03": "Table2 printed p16/PDF p18; Figure3 printed p17/PDF p19",
            "S05": "Hypergrid printed p32-33/PDF p34-35; foreign conditionality printed p52/PDF p54",
            "DDS2024": "Figure11 printed p34: directional exchange floor"},
        "source_caveat_CALA_to_SICI": {
            "official_Figure3_headline_MW": 4100, "official_displayed_base_MW": 1550,
            "Bolano_increment_MW": 500, "Ionian_increment_MW": 2100,
            "accepted_register_workbook_master_MW": 4150,
            "treatment": "Retain explicit frozen 4150 MW arithmetic reconciliation; no new capacity choice"},
        "physical_project_rating_is_not_directional_TTC_increment": True,
        "network_exports": 0, "optimization_model_constructed": False,
        "production_optimization_executed": False, "production_solver_invocations": 0}
    if persist:
        QA.mkdir(parents=True, exist_ok=True)
        _ledger(register).to_csv(QA / "NETWORK_PROJECT_BY_PROJECT_LEDGER.csv", index=False)
        comparison.to_csv(QA / "DIRECTIONAL_AUTHORITY_STATIC_RECONCILIATION.csv", index=False)
        major_projects = {
            "723/E-P": "YES: CSUD-SICI 1000/1000 in every scenario; embedded exactly once",
            "723/W-P": "YES: SARD-SICI 1000/1000 in every scenario; embedded exactly once",
            "732-P": "YES: 1000/1000 increment embedded in Base/High; 2042 delayed COD excludes it from Slow",
            "563-P": "YES: 2100/direction embedded on three sections in Base/High; excluded from Slow",
            "355-P": "YES: direct NORD-CSUD 2100/2100 plus 800 CSUD-CNOR section effect; embedded not additive",
            "356-P": "YES: 600/600 CNOR-CSUD effect embedded in all scenarios",
            "436-P": "YES: asymmetric NORD-CNOR and 1000/1000 CNOR-CSUD effects embedded once",
            "447-P": "YES: direct NORD-SUD 2100/2100 plus NORD-CNOR and SUD-CSUD section effects",
            "301-P": "YES: two 400/400 CORS legs; no direct CNOR-SARD duplicate",
            "601-I": "YES: ELMED embedded in 600/600 floor; no additional 600 MW overlay",
            "401-S": "YES: conditional second pole excluded; original 600/600 floor retained"}
        pd.DataFrame([{"project_id": p, "closure": "YES", "evidence": text,
                       "source": "Frozen 78-row delay register + native workbook formulas + current static contracts"}
                      for p, text in major_projects.items()]).to_csv(
                          QA / "HYPERGRID_TYRRHENIAN_PROJECT_CLOSURE.csv", index=False)
        dump_json(QA / "NETWORK_CONTRACT_SOURCE_AUTHORITY_RECEIPT.json", receipt)
    return expected, receipt


def audit_signed_network(network, year: int, scenario: str, expected: pd.DataFrame | None = None):
    """Read native signed bounds; compare every hour against independent authority."""
    _require((year, scenario) in SCENARIOS, "NETWORK_CONTRACT_SCENARIO_INVALID")
    if expected is None:
        expected, _ = recover_authority(persist=False)
    authority = expected[(expected.year == year) & (expected.scenario == scenario)]
    limits = {(r.from_zone, r.to_zone): float(r.expected_MW) for r in authority.itertuples()}
    bus_names = set(ZONES) | {"CORS"} | {f"EXT_{m}" for m in PRICE_MARKETS}
    links = network.links[network.links.bus0.isin(bus_names) & network.links.bus1.isin(bus_names)]
    _require(len(network.snapshots) == 8760, "SIGNED_NETWORK_CHRONOLOGY_LENGTH_FAIL")
    timestamps = pd.DatetimeIndex(network.snapshots)
    timestamps = timestamps.tz_localize("UTC") if timestamps.tz is None else timestamps.tz_convert("UTC")
    _require(timestamps.equals(pd.date_range("2019-01-01", periods=8760, freq="h", tz="UTC")),
             "SIGNED_NETWORK_CHRONOLOGY_FAIL")
    _require(len(links) == 20, "SIGNED_CORRIDOR_COUNT_FAIL")
    bound_max = network.get_switchable_as_dense("Link", "p_max_pu", network.snapshots, links.index)
    bound_min = network.get_switchable_as_dense("Link", "p_min_pu", network.snapshots, links.index)
    rows, seen = [], set()
    for name, row in links.iterrows():
        a, b = str(row.bus0).removeprefix("EXT_"), str(row.bus1).removeprefix("EXT_")
        _require(a != b and (a, b) in limits and (b, a) in limits, f"SIGNED_ENDPOINT_CONFLICT:{name}")
        pair = tuple(sorted((a, b)))
        _require(pair not in seen, f"DUPLICATE_SIGNED_CORRIDOR:{name}")
        seen.add(pair)
        _require(not bool(row.p_nom_extendable) and not bool(row.committable), "TRANSPORT_NOT_FIXED")
        _require(float(row.efficiency) == 1 and float(row.marginal_cost) == 0, "TRANSPORT_ECONOMICS_DRIFT")
        high = float(row.p_nom) * bound_max[name].to_numpy()
        low = float(row.p_nom) * bound_min[name].to_numpy()
        _require(np.isfinite(high).all() and np.isfinite(low).all(), "SIGNED_BOUND_NOT_FINITE")
        for edge, actual in [((a, b), high), ((b, a), -low)]:
            difference = np.max(np.abs(actual - limits[edge]))
            _require(difference <= 1e-9, f"SIGNED_DIRECTION_CAPACITY_DRIFT:{name}:{edge}:{difference}")
            rows.append({"year": year, "scenario": scenario, "from_zone": edge[0], "to_zone": edge[1],
                "expected_MW": limits[edge], "signed_Link": str(name),
                "max_snapshot_difference_MW": float(difference), "snapshots_checked": len(high), "status": "PASS"})
    _require(set((r["from_zone"], r["to_zone"]) for r in rows) == set(limits),
             "SIGNED_DIRECTION_COVERAGE_FAIL")
    _require(("CNOR", "SARD") not in limits and ("SARD", "CNOR") not in limits,
             "SACOI_DIRECT_LINK_DOUBLE_COUNT")
    return pd.DataFrame(rows)


def reconcile_successors(parents: list[dict], *, test_results: dict | None = None):
    """Phase-C QA only, called by the lead after N PASS; never alters inputs."""
    import json
    import pypsa

    state = json.loads((ROOT / "qa/final_methodology_closure/STATE.json").read_text(encoding="utf-8"))
    _require(state["phases"]["N"]["status"] == "PASS" and state["next_phase"] == "C",
             "PHASE_C_REQUIRES_PHASE_N_PASS")
    _require({(p["year"], p["scenario"]) for p in parents} == set(SCENARIOS) and len(parents) == 6,
             "PHASE_C_PARENT_COVERAGE_FAIL")
    expected, source_receipt = recover_authority()
    _require(source_receipt["checks"]["current_static_drift_rows"] == 0,
             "CURRENT_STATIC_DRIFT_REQUIRES_DOCUMENTED_CORRECTION")
    outputs = []
    for parent in parents:
        path = ROOT / parent["path"]
        _require(sha256_file(path) == parent["sha256"], "PHASE_C_PARENT_HASH_FAIL")
        network = pypsa.Network(path)
        table = audit_signed_network(network, parent["year"], parent["scenario"], expected)
        outputs.append(table)
        _require(sha256_file(path) == parent["sha256"], "PHASE_C_PARENT_MODIFIED")
    combined = pd.concat(outputs, ignore_index=True)
    combined.to_csv(QA / "SIGNED_NETWORK_DIRECTIONAL_AUTHORITY_RECONCILIATION.csv", index=False)
    receipt = {"state": "NETWORK_CONTRACT_RECONCILIATION_PASS",
        "source_authority_receipt_sha256": sha256_file(QA / "NETWORK_CONTRACT_SOURCE_AUTHORITY_RECEIPT.json"),
        "parents": parents, "directional_scenario_rows": len(combined),
        "snapshot_directional_checks": int(combined.snapshots_checked.sum()),
        "all_signed_bounds_match_independent_frozen_authority": True,
        "capacity_corrections": [], "capacity_changed": False,
        "Hypergrid_Tyrrhenian_project_closure": "YES", "tests": test_results or {},
        "optimization_model_constructed": False, "production_optimization_executed": False,
        "production_solver_invocations": 0, "historical_results_modified": 0,
        "governance_2050_modified": False}
    dump_json(QA / "NETWORK_CONTRACT_FINAL_VERIFICATION.json", receipt)
    artifacts = [p for p in sorted(QA.glob("*.csv")) + sorted(QA.glob("*.json"))
                 if p.name != "NETWORK_CONTRACT_OUTPUT_MANIFEST.csv"]
    pd.DataFrame([{"path": str(p.relative_to(ROOT)), "sha256": sha256_file(p)} for p in artifacts]).to_csv(
        QA / "NETWORK_CONTRACT_OUTPUT_MANIFEST.csv", index=False)
    return receipt

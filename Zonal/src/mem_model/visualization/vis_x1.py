"""Read-only VIS-X1 bridge for accepted MEM saved results.

This module has no model-building or optimization entrypoint. The sealed toolkit
is imported from its external handoff; MEM owns only lineage and semantics.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "config/vis_x1.yaml"
OUTPUT = ROOT / "outputs/visualization"
ZONE_ORDER = ("NORD", "CNOR", "CSUD", "SUD", "CALA", "SICI", "SARD")
FIGURE_STATUS = "PRE_P2X_DIAGNOSTIC_BASELINE"
WARNING = "PRE-P2X FLEX DIAGNOSTIC BASELINE | NOT CURRENT CORRECTED PRODUCTION RESULT"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _external_path(cfg: dict, key: str, environment: str) -> Path:
    value = os.environ.get(environment) or cfg.get(key)
    if not value:
        raise RuntimeError(f"VIS_X1_OPTIONAL_DEPENDENCY_NOT_CONFIGURED: {key}; set {environment} or config/vis_x1.yaml")
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _handoff_root(cfg: dict) -> Path:
    return _external_path(cfg, "external_handoff", "MEM_VIS_X1_HANDOFF")


def _toolkit(cfg: dict):
    handoff = _handoff_root(cfg)
    cfg["external_handoff"] = str(handoff)
    manifest = json.loads((handoff / "HANDOFF_MANIFEST.json").read_text(encoding="utf-8"))
    for name, expected in manifest["toolkit_file_hashes"].items():
        if _sha(handoff / name) != expected:
            raise ValueError(f"Sealed VIS-X1 toolkit hash mismatch: {name}")
    if str(handoff) not in sys.path:
        sys.path.insert(0, str(handoff))
    from visualization_toolkit import congestion, dispatch, flows, io, network_maps, prices, storage, summaries
    return congestion, dispatch, flows, io, network_maps, prices, storage, summaries


def write_authority_register() -> Path:
    """Inventory current Stage-B lineage and Stage-A price-source context."""
    state_path = ROOT / "qa/stage_b/p2x_flex_v1/MEM_STAGE_B_P2X_FLEX_V1_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state["status"] != "READY_FOR_MANUAL_EXECUTION" or state["production_optimization_executed"]:
        raise ValueError("P2X authority state changed; re-audit before visualization")
    rows = []
    for year in (2040, 2050):
        for scenario in ("Slow", "Base", "High"):
            key = f"{year}_{scenario}"
            old_dir = ROOT / f"results/MEM_{year}_{scenario.upper()}_CANONICAL"
            old_path = old_dir / f"MEM_{year}_{scenario.upper()}_8760h_SOLVED.nc"
            if not old_path.is_file() or _sha(old_path) != state["legacy_solved_sha256"][key]:
                raise ValueError(f"Legacy result missing or changed: {key}")
            acceptances = sorted(old_dir.glob("*ACCEPTANCE*.json"))
            receipt = acceptances[0] if acceptances else old_dir / "MEM_CANONICAL_REPORTING_RECEIPT.json"
            manifest = old_dir / "MEM_CANONICAL_REPORTING_MANIFEST.csv"
            rows.append(dict(result_id=f"STAGE_B_{key}_PRE_P2X", year=year, scenario=scenario,
                             network_path=_relative(old_path), solved_or_unsolved="SOLVED",
                             parent_model_version="STAGE_B_PRE_P2X_FLEX", P2X_status="PRE_P2X_FLEX_DIAGNOSTIC_BASELINE",
                             UC_status="NO_UC_LP", receipt=_relative(receipt), manifest=_relative(manifest),
                             current_role="PRE_P2X_FLEX_DIAGNOSTIC_BASELINE", visualization_allowed="DIAGNOSTIC_ONLY",
                             limitation="Historical solved result; superseded by P2X_FLEX_V1 demand formulation."))
    checks = {(int(item["year"]), item["scenario"]): item for item in state["network_checks"]}
    for year in (2040, 2050):
        for scenario in ("Slow", "Base", "High"):
            check = checks[(year, scenario)]
            path = ROOT / check["network"].replace("\\", "/")
            if not path.is_file() or _sha(path) != check["sha256"]:
                raise ValueError(f"Corrected parent missing or changed: {year} {scenario}")
            rows.append(dict(result_id=f"STAGE_B_{year}_{scenario}_P2X_FLEX_V1", year=year,
                             scenario=scenario, network_path=_relative(path), solved_or_unsolved="UNSOLVED",
                             parent_model_version="P2X_FLEX_V1", P2X_status="READY_FOR_MANUAL_EXECUTION",
                             UC_status="NO_UC_LP_NOT_RUN", receipt=_relative(state_path),
                             manifest=f"runtime_inputs/p2x_flex_v1/{year}/MEM_STAGE_B_P2X_FLEX_V1_RUNTIME_MANIFEST.csv",
                             current_role="SOLE_ACCEPTED_CORRECTED_PARENT_UNSOLVED",
                             visualization_allowed="STATIC_STRUCTURE_ONLY",
                             limitation="No solved dispatch, price, flow, SOC or objective may be plotted."))
    stage_a = yaml.safe_load((ROOT / "config/stage_a_production_price_sources.yaml").read_text(encoding="utf-8"))
    accepted_paths = {}
    for year, info in stage_a["sources"].items():
        receipt = ROOT / info["receipt"]
        manifest = ROOT / info["result_manifest"]
        directory = manifest.parent
        matches = list(directory.glob("*_SOLVED.nc"))
        if len(matches) != 1 or not receipt.is_file() or not manifest.is_file():
            raise ValueError(f"Stage-A accepted source unresolved: {year}")
        path = matches[0]
        member = pd.read_csv(manifest).loc[lambda x: x.relative_path.eq(_relative(path))]
        if len(member) != 1 or _sha(path).lower() != str(member.iloc[0].sha256).lower():
            raise ValueError(f"Stage-A accepted manifest member hash mismatch: {year}")
        if info.get("solved_network_sha256") and _sha(path).lower() != info["solved_network_sha256"].lower():
            raise ValueError(f"Stage-A accepted source hash mismatch: {year}")
        accepted_paths[path.resolve()] = (year, info)
    for path in sorted((ROOT / "stage_a_results").rglob("*_SOLVED.nc")):
        accepted = accepted_paths.get(path.resolve())
        year = int(accepted[0]) if accepted else (2040 if "2040" in path.name else 2050)
        info = accepted[1] if accepted else {}
        rows.append(dict(result_id=f"STAGE_A_{path.stem}", year=year, scenario="Base" if accepted else "DIAGNOSTIC",
                         network_path=_relative(path), solved_or_unsolved="SOLVED",
                         parent_model_version=info.get("phase", "STAGE_A_HISTORICAL_DIAGNOSTIC"),
                         P2X_status="NOT_STAGE_B_P2X", UC_status="NO_UC_LP",
                         receipt=info.get("receipt", "NOT_RESOLVED_FOR_VIS_X1"),
                         manifest=info.get("result_manifest", "NOT_RESOLVED_FOR_VIS_X1"),
                         current_role="ACCEPTED_STAGE_A_EXTERNAL_PRICE_PARENT" if accepted else "STAGE_A_HISTORICAL_DIAGNOSTIC",
                         visualization_allowed="REFERENCE_ONLY" if accepted else "NO_UNTIL_DIAGNOSTIC_RECEIPT_REVIEW",
                         limitation="Stage-A market model; not a corrected Stage-B result." if accepted else
                                    "Historical Stage-A experiment; not the accepted external-price parent."))
    known = {ROOT / row["network_path"] for row in rows}
    for path in sorted((ROOT / "results").rglob("*_SOLVED.nc")):
        if path not in known:
            rows.append(dict(result_id=f"UNVERIFIED_{path.stem}", year="UNKNOWN", scenario="UNKNOWN",
                             network_path=_relative(path), solved_or_unsolved="SOLVED_UNVERIFIED",
                             parent_model_version="UNVERIFIED", P2X_status="UNVERIFIED", UC_status="UNVERIFIED",
                             receipt="UNVERIFIED", manifest="UNVERIFIED", current_role="CANDIDATE_NOT_ACCEPTED",
                             visualization_allowed="NO", limitation="Requires run receipt, lineage and acceptance review."))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / "MEM_VIS_RESULT_AUTHORITY_REGISTER.csv"
    pd.DataFrame(rows).to_csv(target, index=False)
    return target


def _assert_close(name: str, actual: float, expected: float, qa: list, atol: float = 1e-4) -> None:
    delta = actual - expected
    passed = bool(np.isfinite(actual) and abs(delta) <= atol)
    qa.append(dict(check=name, actual=actual, expected=expected, delta=delta, tolerance=atol,
                   status="PASS" if passed else "FAIL"))
    if not passed:
        raise ValueError(f"VIS-X1 reconciliation failed: {name}: {actual} vs {expected}")


def _physical_generator_ids(network, cfg: dict) -> list[str]:
    ontology = cfg["ontology"]
    known = set(ontology["physical_generator_carriers"]) | set(ontology["shedding_carriers"]) | set(ontology["virtual_carriers"]) | set(ontology["auxiliary_carriers"])
    carriers = network.generators.carrier.astype(str)
    p2x = carriers.str.startswith(ontology["p2x_carrier_prefix"])
    unknown = set(carriers.loc[~p2x]) - known
    if unknown:
        raise ValueError(f"Unmapped Generator carriers: {sorted(unknown)}")
    for name, row in network.generators.loc[p2x].iterrows():
        if float(row.sign) != -1:
            raise ValueError(f"P2X Generator must have sign=-1: {name}")
        power = network.generators_t.p.get(name)
        if power is None or power.min() < -1e-5 or power.max() > float(row.p_nom) + 1e-5:
            raise ValueError(f"P2X dispatch/cap invalid: {name}")
        if f"P2X_ANNUAL_{row.bus}" not in network.global_constraints.index:
            raise ValueError(f"P2X annual equality missing: {name}")
        weights = network.snapshot_weightings.generators.reindex(network.snapshots)
        observed_mwh = float((power * weights).sum())
        target_mwh = float(network.global_constraints.at[f"P2X_ANNUAL_{row.bus}", "constant"])
        if abs(observed_mwh - target_mwh) > .01:
            raise ValueError(f"P2X annual withdrawal fails equality: {name}")
    return list(network.generators.index[carriers.isin(ontology["physical_generator_carriers"])])


def _physical_source(network, cfg: dict, io):
    """Expose only market topology to generic maps; retain all buses in QA inventory."""
    aliases = cfg["display_market_aliases"]
    bus_market = {str(bus): aliases[str(bus)] for bus in ZONE_ORDER}
    for bus in network.buses.index:
        if str(bus).startswith("EXT_"):
            bus_market[str(bus)] = str(bus)[4:]
    if "CORS" in network.buses.index:
        bus_market["CORS"] = "CORS"
    # Auxiliary buses are assigned by actual conversion-link terminals, not name tokens.
    for _, row in network.links.loc[network.links.carrier.isin(["battery_energy", "water_energy"])].iterrows():
        a, b = str(row.bus0), str(row.bus1)
        if a in bus_market and b not in bus_market:
            bus_market[b] = bus_market[a]
        elif b in bus_market and a not in bus_market:
            bus_market[a] = bus_market[b]
    if set(bus_market) != set(network.buses.index.astype(str)):
        raise ValueError("Some MEM buses lack a defensible market mapping")
    physical_ids = list(ZONE_ORDER) + [b for b in network.buses.index if str(b).startswith("EXT_")]
    if "CORS" in network.buses.index:
        physical_ids.append("CORS")
    actual = network.buses[["carrier", "x", "y"]].copy()
    actual["market"] = pd.Series(bus_market)
    actual["bus_role"] = np.where(actual.index.isin(physical_ids), "MARKET_OR_BOUNDARY", "AUXILIARY_STATE")
    actual.index.name = "bus"
    reporting = yaml.safe_load((ROOT / cfg["map"]["coordinate_source"].split("#")[0]).read_text(encoding="utf-8"))
    coords = reporting["schematic_coordinates"]
    if set(physical_ids) - set(coords):
        raise ValueError("Accepted schematic lacks market/boundary nodes")
    shown = network.buses.loc[physical_ids].copy()
    shown["x"] = [coords[bus][0] for bus in physical_ids]
    shown["y"] = [coords[bus][1] for bus in physical_ids]
    physical_links = network.links.loc[network.links.carrier.isin(cfg["map"]["physical_link_carriers"])].copy()
    if not set(physical_links.bus0).union(physical_links.bus1).issubset(shown.index):
        raise ValueError("Physical interface endpoint absent from schematic")
    tables = {"buses": shown, "links": physical_links,
              "lines": pd.DataFrame(columns=["bus0", "bus1"]),
              "transformers": pd.DataFrame(columns=["bus0", "bus1"]),
              "links_t.p0": network.links_t.p0[physical_links.index],
              "links_t.p1": network.links_t.p1[physical_links.index],
              "snapshot_weightings": network.snapshot_weightings}
    source = io.ResultSource(tables=tables)
    return source, actual.reset_index(), physical_links, {bus: bus_market[bus] for bus in physical_ids}


def _save_data(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".parquet":
        frame.to_parquet(path, index=True)
    else:
        frame.to_csv(path, index=True)
    return path


def _save_figure(fig, stem: Path, io, data: list[Path], rows: list, *, family: str,
                 limitation: str = "", pdf: bool = False) -> None:
    fig.text(.01, -.035, WARNING, fontsize=8, color="#9b1c31", weight="bold")
    fig.text(.99, -.035, "MEM 2040 Base", fontsize=8, color="#33485c", ha="right")
    paths = [io.save_figure(fig, stem.with_suffix(ext)) for ext in (".png", ".svg")]
    if pdf:
        paths.append(io.save_figure(fig, stem.with_suffix(".pdf")))
    plt.close(fig)
    rows.append(dict(family=family, status=FIGURE_STATUS, png=_relative(paths[0]), svg=_relative(paths[1]),
                     pdf=_relative(paths[2]) if pdf else "", underlying_data="|".join(_relative(p) for p in data),
                     limitation=limitation))


def _simple_bar(frame: pd.DataFrame, x: str, y: str, title: str, ylabel: str = ""):
    fig, ax = plt.subplots(figsize=(10, max(4, len(frame) * .34 + 1)), layout="constrained")
    ax.barh(frame[x].astype(str), frame[y].astype(float), color="#3979aa")
    ax.set(xlabel=ylabel or y, title=title)
    ax.grid(axis="x", alpha=.2)
    return fig


def build_first_case() -> Path:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    if cfg["case"]["result_status"] != FIGURE_STATUS:
        raise ValueError("First-case status changed; review authority before plotting")
    register = write_authority_register()
    congestion, dispatch, flows, io, maps, prices, storage, summaries = _toolkit(cfg)
    import pypsa
    case = cfg["case"]
    network_path = ROOT / case["network"]
    acceptance = json.loads((ROOT / case["acceptance"]).read_text(encoding="utf-8"))
    reporting = json.loads((ROOT / case["reporting_receipt"]).read_text(encoding="utf-8"))
    sanity = json.loads((ROOT / case["sanity_review"]).read_text(encoding="utf-8"))["scenarios"][case["scenario"]]
    if acceptance["status"] != "ACCEPTED_IMMUTABLE" or reporting["status"] != "PASS":
        raise ValueError("Historical receipt is not accepted/PASS")
    if _sha(network_path).lower() != acceptance["solved_network_sha256"].lower():
        raise ValueError("Selected solved-network hash mismatch")
    if _sha(ROOT / case["reporting_manifest"]).lower() != acceptance["reporting_manifest_sha256"].lower():
        raise ValueError("Canonical reporting manifest hash mismatch")
    network = pypsa.Network(str(network_path))
    if len(network.snapshots) != 8760 or network.objective is None:
        raise ValueError("Selected network lacks full-year solved result")
    if (int(network.meta["year"]), network.meta["scenario"]) != (case["year"], case["scenario"]):
        raise ValueError("Saved network scenario metadata mismatch")
    if network.generators.carrier.astype(str).str.startswith(cfg["ontology"]["p2x_carrier_prefix"]).any():
        raise ValueError("Selected historical case unexpectedly has P2X")
    physical_ids = _physical_generator_ids(network, cfg)
    folder = OUTPUT / case["model_version"] / str(case["year"]) / case["scenario"]
    data_dir = folder / "data"
    for part in ("network", "operation", "prices", "storage", "diagnostics", "summary", "data"):
        (folder / part).mkdir(parents=True, exist_ok=True)
    figures: list[dict] = []
    qa: list[dict] = []
    weights = network.snapshot_weightings.generators.reindex(network.snapshots).astype(float)
    _assert_close("snapshots", len(network.snapshots), acceptance["snapshots"], qa, 0)
    _assert_close("objective_EUR", float(network.objective), acceptance["objective_EUR_model"], qa, .01)
    _assert_close("generator_weight_hours", float(weights.sum()), 8760, qa, 1e-6)

    # Physical topology: all native coordinates are recorded; the accepted
    # reporting layout is used only as an explicitly labeled schematic.
    map_source, inventory, physical_links, display_market = _physical_source(network, cfg, io)
    inventory_path = _save_data(inventory, data_dir / "bus_inventory.csv")
    branch_path = _save_data(physical_links[["bus0", "bus1", "carrier", "p_nom"]], data_dir / "physical_link_inventory.csv")
    _assert_close("total_buses", len(inventory), len(network.buses), qa, 0)
    _assert_close("physical_interface_links", len(physical_links), 40, qa, 0)
    _assert_close("ac_lines", len(network.lines), 0, qa, 0)
    link_class = {name: ("internal" if row.carrier == "internal_transfer" else "external")
                  for name, row in physical_links.iterrows()}
    fig, topology = maps.plot_zonal_physical_network(
        map_source, bus_grouping=display_market, link_classification=link_class,
        geography=None, show_group_labels=True, show_bus_labels=False,
        title="MEM market topology — accepted schematic, not geographic")
    # The generic toolkit has an AC legend key even when the source has zero
    # Lines. Remove that unused key in this MEM-specific view.
    from matplotlib.lines import Line2D
    fig.axes[0].legend(handles=[
        Line2D([], [], color="#295f80", linewidth=2, linestyle="--", label="Controllable interzonal Link"),
        Line2D([], [], color="#ad6036", linewidth=2, linestyle="--", label="External / CORS boundary Link"),
    ], title="Plotted branches (0 AC Lines)", loc="lower left", bbox_to_anchor=(1.01, 0), frameon=False, fontsize=8)
    topo_path = _save_data(topology, data_dir / "plotted_topology.csv")
    _save_figure(fig, folder / "network/market_topology_schematic", io,
                 [inventory_path, branch_path, topo_path], figures, family="market topology",
                 limitation="All 64 saved bus x/y are (0,0); 16 market/boundary buses shown using accepted schematic coordinates. 48 auxiliary state buses recorded in inventory. No AC Lines.", pdf=True)
    metrics = congestion.compute_congestion_metrics(map_source, threshold=.9)
    metrics_path = _save_data(metrics.reset_index(), data_dir / "interface_utilization_90pct.csv")
    fig, _ = congestion.plot_congestion_map(map_source, threshold=.9, metrics=metrics,
                                            geography=None, title="MEM interface utilization — schematic layout")
    _save_figure(fig, folder / "network/interface_utilization_schematic", io, [metrics_path, branch_path], figures,
                 family="horizon congestion/utilization", limitation="Link p0/p_nom screening hours; not proof of binding constraint. Schematic coordinates.", pdf=True)
    stress = congestion.compute_system_stress_table(map_source, threshold=.9)
    stress_path = _save_data(stress.reset_index(), data_dir / "system_stress.csv")
    snapshot = stress.index[stress.stress_rank.eq(1)][0]
    fig, snapshot_flows = maps.plot_flow_map(map_source, snapshot, geography=None,
                                             title=f"System stress rank 1 — {snapshot} (schematic)")
    flow_path = _save_data(snapshot_flows, data_dir / "stress_snapshot_link_flows.csv")
    _save_figure(fig, folder / "network/system_stress_flow_schematic", io, [stress_path, flow_path], figures,
                 family="system-stress snapshot flow", limitation="Deterministic rank-1 hour; p0 bus0 sign. Schematic coordinates.", pdf=True)

    # Canonical Stage-B reporting is the accepted operation contract. The
    # adapter consumes its table and checks it against the saved network.
    canon = ROOT / case["canonical_statistics"]
    operation = pd.read_parquet(canon / "dispatch_8760_by_zone.parquet")
    national = operation.loc[operation.zone.eq("NATIONAL")].set_index("snapshot")
    national.index = pd.to_datetime(national.index)
    operation_path = _save_data(operation, data_dir / "canonical_dispatch_by_zone.parquet")
    rigid_column = "rigid_end_use_demand_MW" if "rigid_end_use_demand_MW" in national else "gross_end_use_demand_MW"
    _assert_close("national_rigid_demand_TWh", national[rigid_column].mul(weights.to_numpy()).sum()/1e6,
                  sanity["demand_TWh"], qa, 1e-6)
    _assert_close("system_shedding_TWh", national.load_shedding_MW.mul(weights.to_numpy()).sum()/1e6,
                  sanity["load_shedding_TWh"], qa, 1e-8)
    _assert_close("shedding_share", national.load_shedding_MW.sum()/max(national[rigid_column].sum(), 1), 0, qa, 1e-12)
    _assert_close("P2X_generator_count_historical", int(network.generators.carrier.astype(str).str.startswith(cfg["ontology"]["p2x_carrier_prefix"]).sum()), 0, qa, 0)
    primary = pd.read_csv(canon / "annual_primary_generation_national.csv")
    _assert_close("primary_generation_TWh", primary.annual_primary_generation_TWh.sum(),
                  sanity["primary_generation_TWh"], qa, 1e-6)
    primary_ids = network.generators.loc[physical_ids]
    physical_energy = network.generators_t.p[primary_ids.index].clip(lower=0).mul(weights, axis=0).sum().sum()/1e6
    # Reservoir and basin hydro reaches the grid through turbine Links, so
    # reconcile it after terminal-specific hydro extraction below.
    positive_columns = [col for col in primary.display_technology if col in national.columns]
    window_start = max(0, network.snapshots.get_loc(snapshot) - 84)
    sample = national.iloc[window_start:window_start+168]
    stack = sample[positive_columns].copy()
    stack["BESS discharge"] = sample["BESS"] if "BESS" not in stack else 0
    stack["PHS discharge"] = sample["PHS"] if "PHS" not in stack else 0
    stack["Net imports"] = sample.net_imports_MW
    stack["BESS charging"] = -sample.bess_charging_MW
    stack["PHS charging"] = -sample.phs_charging_MW
    stack["demand"] = -sample[rigid_column]
    stack_path = _save_data(stack, data_dir / "stress_week_dispatch_mw.csv")
    fig = dispatch.plot_stacked_dispatch(stack)
    _save_figure(fig, folder / "operation/stress_week_dispatch", io, [stack_path, operation_path], figures,
                 family="stacked hourly dispatch + rigid load", limitation="168-hour window around deterministic system-stress hour; full 8760-hour canonical table retained.")
    vre_names = ["Solar PV - rooftop", "Solar PV - utility", "Onshore wind", "Offshore wind"]
    residual = pd.DataFrame(index=national.index)
    residual["demand_mw"] = national[rigid_column]
    residual["vre_mw"] = national[vre_names].sum(axis=1)
    residual["residual_load_mw"] = residual.demand_mw - residual.vre_mw
    residual_path = _save_data(residual, data_dir / "national_residual_load_mw.parquet")
    fig = dispatch.plot_residual_load(residual.iloc[window_start:window_start+168])
    _save_figure(fig, folder / "operation/residual_load", io, [residual_path], figures,
                 family="residual/net load", limitation="Rigid demand minus dispatched solar/wind; excludes P2X because the historical result has none.")

    # Stores hold energy, but BESS/PHS/hydro electrical power is carried by
    # terminal-specific conversion Links. Build operation from those terminals.
    hydro = pd.read_csv(ROOT / cfg["ontology"]["hydro_source"])
    market_by_bus = inventory.set_index("bus").market
    source_zone = {v: k for k, v in cfg["display_market_aliases"].items()}
    store_groups: dict[str, list[str]] = {"BESS": [], "PHS": [], "Hydro - basin/pondage": [], "Hydro - reservoir": []}
    bus_group = {}
    for name, row in network.stores.iterrows():
        bus = str(row.bus)
        if row.carrier == "battery_energy":
            group = "BESS"
        elif row.carrier == "water_energy":
            zone = source_zone.get(market_by_bus.loc[bus])
            accepted = hydro.loc[hydro.zone.eq(zone) & hydro.p_nom_MW_NET.gt(0)]
            matches = [c for c in accepted.hydro_class if bus.endswith("_" + c)]
            if len(matches) != 1:
                raise ValueError(f"Cannot map accepted hydro Store class: {name}")
            group = "PHS" if matches[0] in {"PURE_PHS", "MIXED_PHS"} else (
                "Hydro - basin/pondage" if matches[0] == "BASIN_PONDAGE" else "Hydro - reservoir")
        else:
            raise ValueError(f"Unmapped Store carrier: {row.carrier}")
        store_groups[group].append(name)
        bus_group[bus] = group
    full_storage = pd.DataFrame(index=network.snapshots)
    for group, store_ids in store_groups.items():
        full_storage[(group, "energy_mwh")] = network.stores_t.e[store_ids].sum(axis=1)
        full_storage[(group, "charge_mw")] = 0.0
        full_storage[(group, "discharge_mw")] = 0.0
    for name, row in network.links.loc[network.links.carrier.isin(["battery_energy", "water_energy"])].iterrows():
        bus0, bus1 = str(row.bus0), str(row.bus1)
        if bus1 in bus_group and bus0 in ZONE_ORDER:  # charge at grid terminal
            full_storage[(bus_group[bus1], "charge_mw")] += network.links_t.p0[name].clip(lower=0)
        elif bus0 in bus_group and bus1 in ZONE_ORDER:  # delivered at grid terminal
            full_storage[(bus_group[bus0], "discharge_mw")] += (-network.links_t.p1[name]).clip(lower=0)
        else:
            raise ValueError(f"Unclassified storage/hydro conversion Link: {name}")
    full_storage.columns = pd.MultiIndex.from_tuples(full_storage.columns, names=["carrier", "metric"])
    storage_flat = full_storage.copy()
    storage_flat.columns = [f"{a}|{b}" for a, b in storage_flat.columns]
    storage_path = _save_data(storage_flat, data_dir / "storage_hydro_operation.parquet")
    storage_totals = storage.storage_summary(full_storage, weights=weights)
    totals_path = _save_data(storage_totals.reset_index(), data_dir / "storage_hydro_totals.csv")
    _assert_close("BESS_discharge_TWh", storage_totals.loc["BESS", "discharge_throughput"]/1e6,
                  sanity["bess_discharge_TWh"], qa, 1e-5)
    _assert_close("BESS_charge_TWh", storage_totals.loc["BESS", "charge_throughput"]/1e6,
                  sanity["bess_charging_TWh"], qa, 1e-5)
    _assert_close("PHS_discharge_TWh", storage_totals.loc["PHS", "discharge_throughput"]/1e6,
                  sanity["phs_discharge_TWh"], qa, 1e-5)
    _assert_close("PHS_charge_TWh", storage_totals.loc["PHS", "charge_throughput"]/1e6,
                  sanity["phs_charging_TWh"], qa, 1e-5)
    conventional_hydro_link_twh = storage_totals.loc[["Hydro - basin/pondage", "Hydro - reservoir"], "discharge_throughput"].sum()/1e6
    _assert_close("primary_generation_including_hydro_links_TWh", physical_energy + conventional_hydro_link_twh,
                  primary.annual_primary_generation_TWh.sum(), qa, 1e-5)
    for group, stem, family in (("BESS", "bess_operation", "BESS charge/discharge/SOC"),
                                ("PHS", "phs_operation", "PHS operation/stored energy"),
                                ("Hydro - reservoir", "reservoir_hydro", "reservoir hydro operation"),
                                ("Hydro - basin/pondage", "basin_hydro", "basin/pondage hydro operation")):
        fig = storage.plot_storage_operation(full_storage.iloc[window_start:window_start+168], group)
        _save_figure(fig, folder / f"storage/{stem}", io, [storage_path, totals_path], figures,
                     family=family, limitation="Store energy is MWh; electrical charge/discharge uses Link p0 and -p1. 168-hour view, full-year data retained.")
    hydro_hour = national[["Hydro - run of river", "Hydro - basin/pondage", "Hydro - reservoir", "PHS"]]
    hydro_path = _save_data(hydro_hour, data_dir / "hydro_dispatch_mw.parquet")
    fig = dispatch.plot_stacked_dispatch(hydro_hour.iloc[window_start:window_start+168], show_demand_line=False)
    _save_figure(fig, folder / "operation/hydro_dispatch", io, [hydro_path], figures,
                 family="hydro operation", limitation="PHS shown separately from run-of-river, basin and reservoir hydro.")

    vre_ids = list(primary_ids.index[primary_ids.carrier.isin(["solar_pv_rooftop", "solar_pv_utility", "wind_onshore", "wind_offshore"])])
    curtail = summaries.extract_curtailment(io.ResultSource(network=network), vre_ids)
    curtail["display_technology"] = curtail.carrier.map(yaml.safe_load((ROOT / "config/stage_b_reporting.yaml").read_text(encoding="utf-8"))["carrier_to_display_technology"])
    curtail_path = _save_data(curtail, data_dir / "vre_curtailment_by_asset.csv")
    _assert_close("VRE_curtailment_TWh", curtail.curtailment_mwh_signed.sum()/1e6,
                  sanity["vre_curtailment_TWh"], qa, 1e-5)
    availability = network.generators_t.p_max_pu.reindex(index=network.snapshots, columns=vre_ids)
    for name in vre_ids:
        availability[name] = availability[name].fillna(float(network.generators.at[name, "p_max_pu"]))
    available_mw = availability.mul(network.generators.loc[vre_ids, "p_nom"], axis=1).sum(axis=1)
    dispatched_mw = network.generators_t.p[vre_ids].sum(axis=1)
    vre_hourly = pd.DataFrame({"available_mw": available_mw, "dispatched_mw": dispatched_mw,
                               "curtailed_mw": available_mw - dispatched_mw})
    vre_hourly_path = _save_data(vre_hourly, data_dir / "vre_available_dispatched_mw.parquet")
    fig, ax = plt.subplots(figsize=(11, 4), layout="constrained")
    shown_vre = vre_hourly.iloc[window_start:window_start+168]
    ax.plot(shown_vre.index, shown_vre.available_mw, label="Available", color="#bf881c", lw=1)
    ax.plot(shown_vre.index, shown_vre.dispatched_mw, label="Dispatched", color="#287ea1", lw=1)
    ax.fill_between(shown_vre.index, shown_vre.dispatched_mw, shown_vre.available_mw,
                    label="Curtailment", color="#d7a645", alpha=.35)
    ax.set(xlabel="Snapshot", ylabel="Power [MW]", title="National VRE availability and dispatch — stress week")
    ax.legend(frameon=False)
    fig.autofmt_xdate()
    _save_figure(fig, folder / "operation/vre_availability_dispatch", io, [vre_hourly_path, curtail_path], figures,
                 family="VRE availability and dispatch", limitation="168-hour view; full 8760-hour availability, dispatch and curtailment retained.")
    fig = summaries.plot_curtailment(curtail.drop(columns="carrier").rename(columns={"display_technology": "carrier"}))
    _save_figure(fig, folder / "operation/vre_curtailment", io, [curtail_path], figures,
                 family="VRE availability/dispatch/curtailment", limitation="Annual available-minus-dispatched energy; hourly source is saved generator p_max_pu and p.")

    # Market prices: seven Italian AC buses, one bus per market in this case;
    # external price-taking buses are not mixed into Italian zonal averages.
    price = network.buses_t.marginal_price.loc[:, list(ZONE_ORDER)].rename(columns=cfg["display_market_aliases"])
    price.index.name = "snapshot"
    price_path = _save_data(price, data_dir / "italian_zonal_prices_eur_per_mwh.parquet")
    canonical_price = pd.read_csv(canon / "zonal_price_statistics.csv").set_index("zone")
    loads = network.loads.loc[network.loads.bus.isin(ZONE_ORDER)]
    for zone in ZONE_ORDER:
        load_ids = loads.index[loads.bus.eq(zone)]
        demand = network.loads_t.p_set[load_ids].sum(axis=1)
        observed = float((network.buses_t.marginal_price[zone] * demand * weights).sum() / (demand * weights).sum())
        _assert_close(f"load_weighted_price_{zone}_EUR_per_MWh", observed,
                      canonical_price.loc[zone, "load_weighted_mean_EUR_per_MWh"], qa, 1e-6)
    national_weighted_price = sum(
        float((network.buses_t.marginal_price[z] * network.loads_t.p_set[loads.index[loads.bus.eq(z)]].sum(axis=1) * weights).sum())
        for z in ZONE_ORDER) / float(national[rigid_column].mul(weights.to_numpy()).sum())
    _assert_close("load_weighted_national_price_EUR_per_MWh", national_weighted_price,
                  sanity["load_weighted_national_price_EUR_per_MWh"], qa, 1e-6)
    fig = prices.plot_price_timeseries(price)
    _save_figure(fig, folder / "prices/zonal_price_timeseries", io, [price_path], figures,
                 family="zonal price time series", limitation="Nodal marginal price at the seven Italian AC market buses; 8760 hours.")
    duration = prices.price_duration_table(price, weights=weights)
    duration_path = _save_data(duration, data_dir / "zonal_price_duration.csv")
    fig = prices.plot_price_duration(price, weights=weights)
    _save_figure(fig, folder / "prices/zonal_price_duration", io, [duration_path, price_path], figures,
                 family="price duration curves", limitation="Generator snapshot weights are one physical hour each.")
    heat = prices.price_day_hour_table(price, "NORD")
    heat_path = _save_data(heat, data_dir / "nord_price_day_hour.csv")
    fig = prices.plot_price_heatmap(price, "NORD")
    _save_figure(fig, folder / "prices/nord_price_heatmap", io, [heat_path, price_path], figures,
                 family="price heatmap", limitation="NORD shown; source table contains every Italian market.")
    shed_ids = list(network.generators.index[network.generators.carrier.eq("load_shedding")])
    shed = summaries.extract_load_shedding(io.ResultSource(network=network), shed_ids)
    shed_path = _save_data(shed, data_dir / "load_shedding_by_asset.csv")
    _assert_close("shed_MWh", shed.shedding_mwh.sum(), 0, qa, 1e-5)
    scarcity = pd.DataFrame({"market": [cfg["display_market_aliases"][z] for z in ZONE_ORDER],
                             "hours_above_500": [(price[cfg["display_market_aliases"][z]] > 500).sum() for z in ZONE_ORDER],
                             "VOLL_hours": [(price[cfg["display_market_aliases"][z]] >= 15000).sum() for z in ZONE_ORDER],
                             "shedding_MWh": [float(shed.loc[network.generators.loc[shed.generator_id, "bus"].eq(z).to_numpy(), "shedding_mwh"].sum()) for z in ZONE_ORDER],
                             "shedding_hours": [float(shed.loc[network.generators.loc[shed.generator_id, "bus"].eq(z).to_numpy(), "scarcity_weighted_hours"].sum()) for z in ZONE_ORDER]})
    scarcity_path = _save_data(scarcity, data_dir / "scarcity_by_market.csv")
    _assert_close("hours_above_500_zone_hours", scarcity.hours_above_500.sum(), sanity["hours_above_500_zone_hours"], qa, 0)
    _assert_close("VOLL_zone_hours", scarcity.VOLL_hours.sum(), 0, qa, 0)
    _assert_close("shedding_zone_hours", scarcity.shedding_hours.sum(), 0, qa, 0)
    fig = _simple_bar(scarcity, "market", "VOLL_hours", "VOLL price hours by market", "Hours")
    _save_figure(fig, folder / "prices/scarcity_voll_hours", io, [scarcity_path], figures,
                 family="scarcity/VOLL-hour diagnostics", limitation="Price-threshold diagnostic; no actual VOLL or shedding hours in this case.")
    fig = _simple_bar(scarcity, "market", "shedding_MWh", "Load shedding by market", "MWh")
    _save_figure(fig, folder / "prices/load_shedding", io, [scarcity_path, shed_path], figures,
                 family="load-shedding duration/volume", limitation="All seven market shedding volumes are zero; zero-height bars are intentional.")
    fig = _simple_bar(scarcity, "market", "shedding_hours", "Load-shedding duration by market", "Hours")
    _save_figure(fig, folder / "prices/load_shedding_duration", io, [scarcity_path, shed_path], figures,
                 family="load-shedding duration", limitation="All seven market shedding durations are zero; zero-height bars are intentional.")

    # Only physical interfaces enter exchange accounting; BESS and hydro
    # conversion Links never become inter-market imports or exports.
    exchange_flows = flows.extract_intermarket_flows(map_source, display_market,
                                                       components=("links",), branch_ids={"links": list(physical_links.index)})
    flow_all_path = _save_data(exchange_flows, data_dir / "intermarket_terminal_flows.parquet")
    exchange = flows.market_exchange_timeseries(exchange_flows)
    exchange_path = _save_data(exchange, data_dir / "market_exchange_timeseries.parquet")
    external_ids = physical_links.index[physical_links.carrier.eq("external_trade")]
    external = exchange_flows.loc[exchange_flows.branch_id.isin(external_ids)]
    ext_summary = external.groupby("branch_id", as_index=False).agg(
        from_market=("market0", "first"), to_market=("market1", "first"),
        bus0_energy_MWh=("flow_mw", "sum"), bus1_energy_MWh=("p1_mw", "sum"))
    ext_summary_path = _save_data(ext_summary, data_dir / "external_link_annual_energy.csv")
    # Net external injection into the Italian zone buses uses the accepted
    # terminal convention: -p0 at bus0, -p1 at bus1.
    ext_net = 0.0
    for _, row in ext_summary.iterrows():
        if row.from_market in cfg["display_market_aliases"].values():
            ext_net -= row.bus0_energy_MWh
        if row.to_market in cfg["display_market_aliases"].values():
            ext_net -= row.bus1_energy_MWh
    _assert_close("net_external_imports_TWh", ext_net/1e6, sanity["net_external_imports_TWh"], qa, 1e-5)
    canonical_net = pd.read_csv(canon / "annual_net_imports_by_zone.csv")
    for _, row in canonical_net.iterrows():
        market = cfg["display_market_aliases"][row.zone]
        observed = exchange.loc[exchange.market.eq(market), "net_import_mw"].sum()/1e6
        _assert_close(f"net_imports_{row.zone}_TWh", observed, row.annual_net_imports_TWh, qa, 1e-5)
    fig = flows.plot_market_exchange(exchange, "NORD")
    _save_figure(fig, folder / "operation/nord_market_exchange", io, [exchange_path, flow_all_path], figures,
                 family="interzonal and external exchanges", limitation="NORD hourly example; full signed terminal flows for all physical interfaces retained.")
    ext_market = external.loc[external.market0.isin(cfg["display_market_aliases"].values()) |
                                  external.market1.isin(cfg["display_market_aliases"].values())].copy()
    import_mwh = sum(max(0.0, -r.p1_mw if r.market1 in cfg["display_market_aliases"].values() else -r.flow_mw)
                     for r in ext_market.itertuples())
    export_mwh = sum(max(0.0, r.flow_mw if r.market0 in cfg["display_market_aliases"].values() else r.p1_mw)
                     for r in ext_market.itertuples())
    trade = pd.DataFrame({"metric": ["gross imports", "gross exports", "net imports"],
                          "TWh": [import_mwh/1e6, export_mwh/1e6, ext_net/1e6]})
    trade_path = _save_data(trade, data_dir / "annual_external_trade.csv")
    fig = _simple_bar(trade, "metric", "TWh", "Annual external trade", "TWh")
    _save_figure(fig, folder / "summary/annual_external_trade", io, [trade_path, ext_summary_path], figures,
                 family="annual imports/exports/net imports", limitation="Gross directional terminal flow; net imports reconcile to accepted receipt.")

    capacity = pd.read_csv(canon / "installed_capacity_national.csv")
    capacity_path = _save_data(capacity, data_dir / "installed_capacity_national.csv")
    fig = summaries.plot_mix(capacity.rename(columns={"display_technology": "carrier"}),
                             value="installed_capacity_GW", title="Installed capacity by MEM technology", unit="GW")
    _save_figure(fig, folder / "summary/installed_capacity", io, [capacity_path], figures,
                 family="installed capacity by carrier", limitation="BESS/PHS discharge power included as distinct technologies; Store MWh excluded.")
    primary_path = _save_data(primary, data_dir / "annual_primary_generation.csv")
    fig = summaries.plot_mix(primary.rename(columns={"display_technology": "carrier"}),
                             value="annual_primary_generation_TWh", title="Annual primary generation", unit="TWh")
    _save_figure(fig, folder / "summary/annual_primary_generation", io, [primary_path], figures,
                 family="annual generation by carrier", limitation="Excludes storage discharge, load shedding, external proxy generation and P2X.")
    factors = primary.merge(capacity, on="display_technology", how="left")
    factors["capacity_factor"] = factors.annual_primary_generation_TWh*1000 / (factors.installed_capacity_GW*8760)
    factors_path = _save_data(factors, data_dir / "generation_capacity_factor.csv")
    compare = factors.rename(columns={"display_technology": "carrier"}).assign(
        capacity_mw=lambda x: x.installed_capacity_GW*1000,
        generation_mwh=lambda x: x.annual_primary_generation_TWh*1e6)
    fig = summaries.plot_generation_vs_capacity(compare)
    _save_figure(fig, folder / "summary/generation_vs_capacity", io, [factors_path], figures,
                 family="generation versus capacity/capacity factor", limitation="Primary generation only; capacity factor uses 8760 physical hours.")
    fig = _simple_bar(storage_totals.reset_index().assign(discharge_TWh=lambda x: x.discharge_throughput/1e6),
                      "carrier", "discharge_TWh", "Storage and hydro discharge throughput", "TWh")
    _save_figure(fig, folder / "summary/storage_throughput", io, [totals_path], figures,
                 family="storage throughput", limitation="BESS/PHS separate; natural hydro discharge shown separately, not storage cycling.")
    fig = _simple_bar(curtail.groupby("display_technology", as_index=False).curtailment_mwh_signed.sum().assign(
        curtailment_TWh=lambda x: x.curtailment_mwh_signed/1e6), "display_technology", "curtailment_TWh",
        "Annual VRE curtailment", "TWh")
    _save_figure(fig, folder / "summary/curtailment_totals", io, [curtail_path], figures,
                 family="curtailment totals", limitation="Available minus dispatched VRE, reconciled to accepted sanity review.")
    objective = pd.DataFrame([{"metric": "saved model objective", "EUR": float(network.objective)}])
    objective_path = _save_data(objective, data_dir / "objective.csv")
    fig = _simple_bar(objective, "metric", "EUR", "Saved MEM model objective", "EUR")
    _save_figure(fig, folder / "summary/objective", io, [objective_path], figures,
                 family="objective summary", limitation="Model objective only; no unsupported operating-cost decomposition.")

    checks = pd.DataFrame(qa)
    checks.to_csv(folder / "diagnostics/qa_reconciliation.csv", index=False)
    pd.DataFrame(figures).to_csv(folder / "FIGURE_INDEX.csv", index=False)
    metadata = {"schema_version": "MEM_VIS_X1_FIRST_CASE_V1", "result_status": FIGURE_STATUS,
                "warning": WARNING, "case": case, "sealed_handoff": cfg["external_handoff"],
                "authority_register": _relative(register), "selected_snapshot": str(snapshot),
                "figure_count": len(figures), "qa_checks": len(qa), "qa_status": "PASS",
                "native_coordinate_status": "ALL_64_BUS_COORDINATES_ZERO",
                "map_status": "SCHEMATIC_ONLY_NO_TRUE_GEOGRAPHIC_COORDINATES",
                "unavailable_families": ["P2X withdrawal: absent before P2X_FLEX_V1",
                                         "true-coordinate geographic map: native coordinates unavailable",
                                         "emissions: not in selected accepted result contract",
                                         "operating-cost decomposition: not supported by accepted result contract"],
                "comparison_status": "NOT_RUN_FIRST_CASE_REVIEW_STOP"}
    (folder / "FIGURE_METADATA.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return folder


if __name__ == "__main__":
    print(build_first_case())

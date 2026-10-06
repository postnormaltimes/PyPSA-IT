"""UC2 authority-separated adapter to the sealed VIS-X1 plotting dependency.

Physical tables are always UC MILP. Only supplied fixed-commitment LP prices
enter price plots. Reference AC branches never receive MEM interface results.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

from mem_model.reporting import canonical_results as canonical
from mem_model.visualization.vis_x1 import CONFIG, ROOT, ZONE_ORDER, _sha, _toolkit, _physical_source, _physical_generator_ids
from mem_model.visualization.vis_x1_geographic import _zone_points, _external_points, PARENT_NUTS2
from mem_model.visualization.vis_x1_reference_42bus import _reference_tables, _base_map, _legend, _flow_overlay, BASE_EXTENT, OVERLAY_EXTENT

STATUS = "CURRENT_ACCEPTED_RESULT"
STAGE_B_SCAFFOLD = "MEM_STAGE_B_7ZONE_EXTERNAL_GRAPH"
RESULT_MAP_STEMS = ("MEM_ITALY_42BUS_ZONAL_PRICE", "MEM_ITALY_42BUS_INTERZONAL_FLOW",
                    "MEM_ITALY_42BUS_UTILIZATION_CONTEXT")


def correct_existing_uc2_maps(report_dir):
    """Map-only delta from accepted R1 CSV/Parquet outputs; never load a network."""
    from mem_model.reporting.uc2_postprocess import no_solver_calls
    report = Path(report_dir)
    r1_path = report / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json"
    r1 = json.loads(r1_path.read_text(encoding="utf-8"))
    if r1["status"] != "PASS" or r1["physical_source"] != "UC_MILP" or r1["price_source"] != "FIXED_COMMITMENT_PRICE_LP":
        raise ValueError("Map correction requires accepted authority-separated R1 reporting")
    folder = report / "VIS_X1"
    metadata_path = folder / "FIGURE_METADATA.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    snapshot = pd.Timestamp(r1["visualization"]["selected_stress_snapshot"])
    if snapshot != pd.Timestamp(metadata["selected_stress_snapshot"]):
        raise ValueError("Map delta must preserve R1 stress snapshot")
    mutable = {f"VIS_X1/network/{stem}.{ext}" for stem in RESULT_MAP_STEMS
               for ext in ("png", "svg", "pdf", "metadata.json")}
    mutable.update({"VIS_X1/FIGURE_METADATA.json", "VIS_X1/FIGURE_INDEX.csv"})
    preserved = {name: digest for name, digest in r1["artifact_hashes"].items()
                 if name.replace("\\", "/") not in mutable}
    preserved.update(r1["original_artifact_hashes"])
    for name, digest in preserved.items():
        if _sha(report / name) != digest:
            raise ValueError(f"Non-map R1 artifact changed: {name}")
    for name, digest in r1["protected_source_hashes"].items():
        if _sha(Path(name)) != digest:
            raise ValueError(f"Protected R1 model/result artifact changed: {name}")
    comparison = report.parents[1] / "COMPARISONS_VIS_X1"
    comparison_hashes = {str(path): _sha(path) for path in comparison.rglob("*") if path.is_file()}
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    _, _, _, io, maps, _, _, _ = _toolkit(cfg)
    from visualization_toolkit.styles import PlotStyle, style_context
    from adapters.pypsa_it_adapter import RECOVERED_R2B_ZONE_COLORS
    stats = report / "CANONICAL/statistics"
    inputs = [folder / "data/visualization_only_zone_anchors.csv", stats / "uc2_net_interface_registry.csv",
              folder / "data/net_interface_overlay_selected_snapshot.csv", stats / "uc2_price_annual_summary.csv",
              stats / "uc2_net_interface_hourly_MW.parquet", stats / "uc2_net_interface_summary.csv"]
    anchors = pd.read_csv(inputs[0], index_col="market")
    registry = pd.read_csv(inputs[1])
    corridors = pd.read_csv(inputs[2], index_col=0)
    annual = pd.read_csv(inputs[3])
    net = pd.read_parquet(inputs[4])
    summary = pd.read_csv(inputs[5]).set_index("interface_id")
    if not np.allclose(corridors.signed_net_p0_MW, net.loc[snapshot, corridors.interface_id], atol=1e-9, rtol=0):
        raise ValueError("Selected map flow differs from canonical UC net flow")
    if not np.allclose(corridors.hours_at_or_above_threshold,
                       summary.loc[corridors.interface_id, "hours_at_or_above_90_percent"], atol=1e-9, rtol=0):
        raise ValueError("Map utilization differs from existing canonical horizon statistics")
    input_hashes = {str(path.relative_to(report)): _sha(path) for path in inputs}
    figures, qa, artifacts = [], [], []
    def data(name, frame):
        path = folder / "data" / (name + ".csv")
        frame.to_csv(path, index=True, lineterminator="\n")
        artifacts.append(path)
        return path
    def save(fig, name, family, sources, *, limitation="", **kwargs):
        fig.text(.01, -.04, f"{STATUS} | UC2 {r1['year']} {r1['scenario']}", fontsize=8, color="#33485c")
        fig.text(.01, -.065, "Actual MEM Stage-B market/interface graph — no physical AC branches; CORS has no price", fontsize=7)
        stem = folder / family / name
        entry = {"figure": name.replace("MEM_ITALY_42BUS_", "MEM_STAGE_B_"), "legacy_output_stem": name,
                 "family": family, "year": r1["year"], "scenario": r1["scenario"], "status": STATUS,
                 "physical_source": "UC_MILP", "price_source": "FIXED_COMMITMENT_LP_DUALS",
                 "spatial_scaffold": STAGE_B_SCAFFOLD, "mem_result_resolution": "STAGE_B_ZONAL",
                 "physical_branch_results": "NOT_APPLICABLE_NO_AC_BRANCHES", "reference_AC_branch_count": 0,
                 "Italian_node_count": 7, "external_node_count": 8, "CORS_node_count": 1, "interface_count": 20,
                 "selected_stress_snapshot": str(snapshot), "limitation": limitation,
                 "underlying_data": [str(path) for path in sources]}
        for ext in ("png", "svg", "pdf"):
            path = stem.with_suffix("." + ext)
            tags = {"Date": None} if ext == "svg" else ({"CreationDate": None, "ModDate": None} if ext == "pdf" else {"Software": "MEM VIS-X1 UC2"})
            with matplotlib.rc_context({"svg.hashsalt": "MEM_UC2_VIS_X1_R1"}):
                fig.savefig(path, dpi=240, bbox_inches="tight", facecolor="white", metadata=tags)
            entry[ext] = str(path); artifacts.append(path)
        plt.close(fig)
        sidecar = stem.with_suffix(".metadata.json")
        sidecar.write_text(json.dumps(entry, indent=2), encoding="utf-8")
        artifacts.append(sidecar); figures.append(entry)
    with no_solver_calls() as guard, style_context(PlotStyle(font_family="Aptos")):
        _render_stage_b_maps(registry, anchors, corridors, annual, r1["year"], r1["scenario"], snapshot,
                             cfg, io, maps, data, save, qa, RECOVERED_R2B_ZONE_COLORS, source_paths=inputs)
    replacements = {entry["legacy_output_stem"]: entry for entry in figures}
    before_non_maps = [entry for entry in metadata["figures"] if entry["figure"] not in RESULT_MAP_STEMS
                       and entry.get("legacy_output_stem") not in RESULT_MAP_STEMS]
    metadata["figures"] = [replacements.get(entry.get("legacy_output_stem", entry["figure"]), entry)
                           for entry in metadata["figures"]]
    after_non_maps = [entry for entry in metadata["figures"] if entry.get("legacy_output_stem") not in RESULT_MAP_STEMS]
    if before_non_maps != after_non_maps:
        raise ValueError("Map patch changed unrelated figure metadata")
    metadata["map_spatial_scaffold"] = STAGE_B_SCAFFOLD
    metadata["map_qa"] = "MEM_STAGE_B_MAP_QA.csv"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    index_path = folder / "FIGURE_INDEX.csv"
    pd.DataFrame(metadata["figures"]).to_csv(index_path, index=False)
    artifacts.extend([metadata_path, index_path])
    for name, digest in preserved.items():
        if _sha(report / name) != digest:
            raise ValueError(f"Map delta modified unrelated artifact: {name}")
    if any(_sha(Path(name)) != digest for name, digest in r1["protected_source_hashes"].items()):
        raise ValueError("Map delta modified protected model/result files")
    if any(_sha(Path(name)) != digest for name, digest in comparison_hashes.items()):
        raise ValueError("Map delta modified scenario comparisons")
    qa.extend([{"check": name, "status": "PASS", "detail": detail} for name, detail in [
        ("UC_MILP_net_flow_binding", "Canonical signed net hourly values; original stress hour"),
        ("fixed_LP_price_binding", "Existing canonical annual load-weighted price table"),
        ("existing_horizon_utilization", "Original R1 >=90% hours equal canonical interface summary"),
        ("no_solver_invocations", str(guard["solver_invocations"])),
        ("protected_model_results_unchanged", "All original protected hashes valid"),
        ("non_map_R1_artifacts_unchanged", str(len(preserved))),
        ("reference_only_appendix_unchanged", "Original PNG/SVG/PDF/metadata retained"),
        ("comparisons_unchanged", str(len(comparison_hashes)))]] )
    qa_path = folder / "MEM_STAGE_B_MAP_QA.csv"
    pd.DataFrame(qa).to_csv(qa_path, index=False); artifacts.append(qa_path)
    receipt = {"status": "UC2_VIS_R1_STAGE_B_MAP_CORRECTION_PASS", "year": r1["year"], "scenario": r1["scenario"],
               "spatial_scaffold": STAGE_B_SCAFFOLD, "mem_result_resolution": "STAGE_B_ZONAL", "figure_count": 3,
               "base_R1_receipt_sha256": _sha(r1_path), "solver_invocations": guard["solver_invocations"],
               "qa_checks": len(qa), "selected_stress_snapshot": str(snapshot), "figures": figures,
               "input_hashes": input_hashes, "protected_source_hashes": r1["protected_source_hashes"],
               "preserved_non_map_hashes": preserved, "comparison_hashes": comparison_hashes,
               "code_hashes": {str(Path(__file__).relative_to(ROOT)): _sha(Path(__file__)),
                               "src/mem_model/visualization/vis_x1_reference_42bus.py": _sha(ROOT / "src/mem_model/visualization/vis_x1_reference_42bus.py")},
               "artifact_hashes": {str(path.relative_to(report)): _sha(path) for path in artifacts}}
    (report / "MEM_UC2_STAGE_B_MAP_CORRECTION_RECEIPT.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def _plot_mix_including_zero(summaries, table, *, value, title, unit, style):
    """Retain a valid all-zero diagnostic rejected by the toolkit mix API."""
    if not table.empty and table[value].eq(0).all():
        fig, ax = plt.subplots(figsize=(9, 4), layout="constrained")
        values = table.groupby("carrier", observed=True)[value].sum()
        ax.barh(values.index.astype(str), values, color=[style.color(c) for c in values.index])
        ax.set(title=title, xlabel=unit, xlim=(0, 1))
        ax.text(.5, .5, "All reported values are zero", transform=ax.transAxes,
                ha="center", va="center")
        return fig
    return summaries.plot_mix(table, value=value, title=title, unit=unit, style=style)


def storage_electrical_table(network, cfg, io):
    """Reuse accepted store-class and grid-terminal semantics, never Store p."""
    _, inventory, _, _ = _physical_source(network, cfg, io)
    markets = inventory.set_index("bus").market
    raw_zone = {display: raw for raw, display in cfg["display_market_aliases"].items()}
    mapping = pd.read_csv(ROOT / cfg["ontology"]["hydro_source"])
    groups = {"BESS": [], "PHS": [], "Hydro - basin/pondage": [], "Hydro - reservoir": []}
    bus_group = {}
    for name, row in network.stores.iterrows():
        if row.carrier == "battery_energy":
            group = "BESS"
        elif row.carrier == "water_energy":
            accepted = mapping.loc[mapping.zone.eq(raw_zone.get(markets.loc[row.bus])) & mapping.p_nom_MW_NET.gt(0)]
            classes = [c for c in accepted.hydro_class if str(row.bus).endswith("_" + c)]
            if len(classes) != 1:
                raise ValueError(f"Accepted hydro mapping unresolved: {name}")
            group = "PHS" if classes[0] in {"PURE_PHS", "MIXED_PHS"} else canonical.reporting_config()["hydro_class_to_display_technology"][classes[0]]
        else:
            raise ValueError(f"Unmapped store carrier: {row.carrier}")
        groups[group].append(name)
        bus_group[str(row.bus)] = group
    result = pd.DataFrame(index=network.snapshots)
    for group, ids in groups.items():
        result[(group, "energy_mwh")] = network.stores_t.e[ids].sum(axis=1)
        result[(group, "charge_mw")] = 0.0
        result[(group, "discharge_mw")] = 0.0
    for name, row in network.links.loc[network.links.carrier.isin(["battery_energy", "water_energy"])].iterrows():
        if str(row.bus1) in bus_group and str(row.bus0) in ZONE_ORDER:
            result[(bus_group[str(row.bus1)], "charge_mw")] += network.links_t.p0[name].clip(lower=0)
        elif str(row.bus0) in bus_group and str(row.bus1) in ZONE_ORDER:
            result[(bus_group[str(row.bus0)], "discharge_mw")] += (-network.links_t.p1[name]).clip(lower=0)
        else:
            raise ValueError(f"Unclassified storage conversion terminal: {name}")
    result.columns = pd.MultiIndex.from_tuples(result.columns, names=["carrier", "metric"])
    return result


def render_uc2_vis(uc_network, price_frame, year, scenario, report_dir, diagnostics):
    """Render existing VIS-X1 families from current, explicitly separated inputs."""
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    congestion, dispatch, flows, io, maps, prices, storage, summaries = _toolkit(cfg)
    from visualization_toolkit.styles import PlotStyle, style_context
    transfer = ROOT / Path(cfg["external_reference"])
    seal = json.loads((transfer / "TRANSFER_MANIFEST.json").read_text(encoding="utf-8"))
    palette_source = "adapters/pypsa_it_adapter.py"
    if _sha(transfer / palette_source) != seal["adapter_file_hashes"][palette_source]:
        raise ValueError("Sealed parent zone palette adapter hash mismatch")
    from adapters.pypsa_it_adapter import RECOVERED_R2B_ZONE_COLORS
    aliases = cfg["display_market_aliases"]
    report_cfg = canonical.reporting_config()
    style = PlotStyle(carrier_style={k: {"color": v} for k, v in report_cfg["technology_colors"].items()},
                      carrier_order=report_cfg["technology_order"], font_family="Aptos")
    folder = Path(report_dir) / "VIS_X1"
    data_dir = folder / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    figures, qa = [], []
    network = uc_network
    weights = canonical._weights(network, "generator")
    if not price_frame.index.equals(network.snapshots):
        raise ValueError("LP price chronology does not match UC physical chronology")
    italian_prices = price_frame[list(ZONE_ORDER)].rename(columns=aliases)
    if italian_prices.isna().any().any():
        raise ValueError("Missing verified LP Italian prices")
    _physical_generator_ids(network, cfg)

    def data(name, frame):
        suffix = ".parquet" if len(frame) > 10000 else ".csv"
        path = data_dir / (name + suffix)
        if suffix == ".parquet":
            frame.to_parquet(path)
        else:
            frame.to_csv(path, index=True)
        return path

    def save(fig, name, family, sources, *, limitation="", reference=False, map_figure=False):
        if isinstance(fig, tuple):
            fig = fig[0]
        for ax in fig.axes:
            ax.spines[["top", "right"]].set_visible(False)
        state = "REFERENCE_ONLY" if reference else STATUS
        fig.text(.01, -.04, f"{state} | UC2 {year} {scenario}", fontsize=8, color="#33485c")
        if map_figure:
            fig.text(.01, -.065, "PyPSA-IT reference topology; no MEM physical branch results" if reference else
                     "MEM Stage-B zonal/interface graph; canonical UC net flows; no AC branches", fontsize=7)
        stem = folder / family / name
        stem.parent.mkdir(parents=True, exist_ok=True)
        saved = {}
        for ext in ("png", "svg", "pdf"):
            if ext == "pdf" and not map_figure:
                continue
            path = stem.with_suffix("." + ext)
            metadata = {"Date": None} if ext == "svg" else ({"CreationDate": None, "ModDate": None} if ext == "pdf" else {"Software": "MEM VIS-X1 UC2"})
            with matplotlib.rc_context({"svg.hashsalt": "MEM_UC2_VIS_X1_R1"}):
                fig.savefig(path, dpi=240, bbox_inches="tight", facecolor="white", metadata=metadata)
            saved[ext] = str(path)
        plt.close(fig)
        entry = {"figure": name, "family": family, "year": int(year), "scenario": str(scenario), "status": state,
                 "physical_source": "UC_MILP" if not reference else "PYPSA_IT_REFERENCE",
                 "price_source": "FIXED_COMMITMENT_LP_DUALS", "underlying_data": [str(p) for p in sources],
                 "limitation": limitation, **saved}
        if map_figure:
            entry.update(spatial_scaffold="PYPSA_IT_42BUS_REFERENCE_TOPOLOGY" if reference else "MEM_STAGE_B_7ZONE_EXTERNAL_GRAPH",
                         mem_result_resolution="NONE" if reference else "STAGE_B_ZONAL",
                         physical_branch_results="NOT_FROM_MEM" if reference else "NOT_APPLICABLE_NO_AC_BRANCHES")
        (stem.with_suffix(".metadata.json")).write_text(json.dumps(entry, indent=2), encoding="utf-8")
        figures.append(entry)

    net = diagnostics["net_interface_hourly_MW"]
    util = diagnostics["net_interface_positive_utilization_hourly"].combine(diagnostics["net_interface_negative_utilization_hourly"], np.maximum)
    source = io.ResultSource(tables={"links_t.p0": net, "snapshot_weightings": network.snapshot_weightings})
    stress = congestion.compute_system_stress_table(source, utilization=util)
    snapshot = stress.stress_rank.idxmin()
    start = max(0, network.snapshots.get_loc(snapshot) - 84)
    window = slice(start, min(start + 168, len(network.snapshots)))
    stress_path = data("deterministic_net_interface_stress", stress)
    operation = canonical.dispatch_8760_by_zone(network)
    national = operation.loc[operation.zone.eq("NATIONAL")].set_index("snapshot").drop(columns="zone")
    op_path = data("canonical_uc_dispatch_by_zone", operation)
    metadata_cols = {"rigid_end_use_demand_MW", "p2x_electrical_consumption_MW", "gross_end_use_demand_MW", "bess_charging_MW", "phs_charging_MW", "storage_phs_charging_MW", "net_imports_MW", "load_shedding_MW"}
    generation_cols = [c for c in national if c not in metadata_cols]
    stack = national[generation_cols].copy()
    stack["Net imports"] = diagnostics["canonical_net_imports_hourly_MW"].sum(axis=1)
    stack["BESS charging"] = -national.bess_charging_MW
    stack["PHS charging"] = -national.phs_charging_MW
    stack["P2X withdrawal"] = -national.p2x_electrical_consumption_MW
    stack["Load shedding"] = national.load_shedding_MW
    stack["demand"] = -national.rigid_end_use_demand_MW
    stack_path = data("stress_week_signed_dispatch_MW", stack.iloc[window])
    with style_context(style):
        save(dispatch.plot_stacked_dispatch(stack.iloc[window], style=style), "stress_week_dispatch", "operation", [stack_path, op_path, stress_path], limitation="168-hour view; P2X negative withdrawal, physical generation separate from shedding.")
        residual = pd.DataFrame({"demand_mw": national.gross_end_use_demand_MW, "vre_mw": national[[c for c in generation_cols if c.startswith(("Solar", "Onshore", "Offshore"))]].sum(axis=1)})
        residual["residual_load_mw"] = residual.demand_mw - residual.vre_mw
        residual_path = data("residual_load_including_P2X_MW", residual)
        save(dispatch.plot_residual_load(residual.iloc[window]), "residual_load", "operation", [residual_path], limitation="Rigid load plus UC P2X withdrawal minus dispatched VRE.")
        p2x = pd.DataFrame(canonical._p2x_by_zone(network)).rename(columns=aliases)
        p2x_path = data("p2x_withdrawal_positive_MW", p2x)
        fig, ax = plt.subplots(figsize=(11,4), layout="constrained")
        p2x.iloc[window].plot(ax=ax, linewidth=.8)
        ax.set(title="Flexible P2X electrical withdrawal", ylabel="Consumption [MW]")
        save(fig, "p2x_electrical_withdrawal", "operation", [p2x_path], limitation="Positive plotted consumption; sign=-1 Generator withdrawal, never generation.")
        st = storage_electrical_table(network, cfg, io)
        flat = st.copy(); flat.columns = ["|".join(c) for c in flat.columns]
        st_path = data("uc_storage_hydro_operation", flat)
        st_totals = storage.storage_summary(st, weights=weights)
        totals_path = data("uc_storage_hydro_totals", st_totals)
        for group, name in (("BESS", "bess"), ("PHS", "phs"), ("Hydro - reservoir", "reservoir_hydro"), ("Hydro - basin/pondage", "basin_hydro")):
            save(storage.plot_storage_operation(st.iloc[window], group, style=style), name + "_operation", "storage", [st_path, totals_path], limitation="Grid-terminal p0 charging and -p1 discharge; Store e MWh; BESS/PHS/conventional hydro separate.")
        hydro = national[[c for c in generation_cols if c.startswith("Hydro") or c == "PHS"]]
        hydro_path = data("uc_hydro_and_phs_dispatch", hydro)
        save(dispatch.plot_stacked_dispatch(hydro.iloc[window], style=style, show_demand_line=False), "hydro_dispatch", "storage", [hydro_path], limitation="PHS separated from conventional hydro.")
        price_path = data("fixed_commitment_lp_zonal_prices", italian_prices)
        save(prices.plot_price_timeseries(italian_prices), "zonal_price_timeseries", "prices", [price_path], limitation="Fixed-commitment LP dual prices only.")
        duration_path = data("fixed_commitment_lp_price_duration", prices.price_duration_table(italian_prices, weights=weights))
        save(prices.plot_price_duration(italian_prices, weights=weights), "zonal_price_duration", "prices", [price_path, duration_path])
        save(prices.plot_price_distribution(italian_prices), "zonal_price_distribution", "prices", [price_path])
        for market in italian_prices:
            heat_path = data("price_day_hour_" + market, prices.price_day_hour_table(italian_prices, market))
            save(prices.plot_price_heatmap(italian_prices, market), market.lower() + "_price_heatmap", "prices", [price_path, heat_path])
        for key, name, title, ylabel in (("price_monthly_zonal_means", "monthly_zonal_prices", "Monthly zonal mean prices", "EUR/MWh"), ("price_hourly_all_italy_spread", "all_italy_spatial_spread", "All-Italy hourly price range", "EUR/MWh")):
            frame = diagnostics[key]
            path = data(key, frame)
            if key == "price_monthly_zonal_means":
                numeric = frame.loc[frame.market.isin(ZONE_ORDER)].pivot(index="month",columns="market",values="mean_EUR_per_MWh").rename(columns=aliases)
            else:
                numeric = frame[["spread_EUR_per_MWh"]]
            fig, ax = plt.subplots(figsize=(11,4), layout="constrained")
            numeric.plot(ax=ax, linewidth=.8)
            ax.set(title=title, ylabel=ylabel)
            save(fig, name, "prices", [path], limitation="LP dual statistics; no causal external price-setter claim.")
        profile = diagnostics["price_hour_of_day_profile"]
        profile_path = data("price_hour_of_day_profile",profile)
        for value,name,title in (("mean_EUR_per_MWh","hour_of_day_price_profile","Average hourly zonal price profile"),("zero_price_hours","zero_price_hours_by_hour_of_day","Zero-price hours by hour of day")):
            shown = profile.loc[profile.market.isin(ZONE_ORDER)].pivot(index="hour_of_day",columns="market",values=value).rename(columns=aliases)
            fig,ax=plt.subplots(figsize=(11,4),layout="constrained"); shown.plot(ax=ax,linewidth=1)
            ax.set(title=title,xlabel="Hour of day",ylabel="EUR/MWh" if value.startswith("mean") else "Hours")
            save(fig,name,"prices",[profile_path])
        national_monthly = diagnostics["price_monthly_national_load_weighted"]
        national_monthly_path = data("monthly_national_load_weighted_price",national_monthly)
        fig,ax=plt.subplots(figsize=(10,4),layout="constrained")
        value_cols=[c for c in national_monthly if "EUR_per_MWh" in c]
        national_monthly.set_index("month")[value_cols].plot(ax=ax,linewidth=1.3)
        ax.set(title="Monthly load-weighted national price",ylabel="EUR/MWh")
        save(fig,"monthly_national_load_weighted_price","prices",[national_monthly_path],limitation="Weights use canonical UC electrical load (rigid plus P2X) and snapshot duration; prices use LP duals.")
        scarcity = diagnostics["price_annual_summary"].loc[lambda x:x.market.isin(ZONE_ORDER)].copy()
        scarcity["carrier"] = scarcity.market.map(aliases)
        scarcity_path = data("scarcity_price_hours",scarcity)
        save(_plot_mix_including_zero(summaries,scarcity,value="above500_price_hours",title="Italian scarcity price hours above EUR500/MWh",unit="Hours",style=style),"scarcity_hours_above_500","prices",[scarcity_path],limitation="Price-based scarcity diagnostic; not physical shedding.")
        shed_ids = list(network.generators.index[network.generators.carrier.eq("load_shedding")])
        shedding = summaries.extract_load_shedding(io.ResultSource(network=network),shed_ids)
        shedding_path = data("uc_physical_load_shedding",shedding)
        save(_plot_mix_including_zero(summaries,shedding,value="shedding_mwh",title="Load shedding",unit="MWh",style=style),"physical_load_shedding","operation",[shedding_path],limitation="Physical shedding from UC MILP, separately classified from generation.")
        objective = pd.DataFrame({"carrier":["Canonical UC system objective"],"objective_EUR":[float(network.objective)]})
        objective_path = data("canonical_uc_system_objective",objective)
        save(summaries.plot_mix(objective,value="objective_EUR",title="Canonical UC system objective",unit="EUR",style=style),"canonical_uc_objective","summary",[objective_path,Path(report_dir)/"economic_comparison.json"],limitation="Accepted UC MILP objective; LP primal cost is not used as physical operating cost.")
        capacity = canonical.installed_capacity_by_zone(network)
        capacity_path = data("canonical_installed_capacity", capacity)
        cap = capacity.groupby("display_technology", observed=True).installed_capacity_GW.sum().reset_index().rename(columns={"display_technology": "carrier"})
        save(summaries.plot_mix(cap, value="installed_capacity_GW", title="Installed electrical capacity", unit="GW", style=style), "installed_capacity", "summary", [capacity_path])
        primary = canonical.annual_generation_by_zone(network)
        primary_path = data("canonical_primary_generation", primary)
        gen = primary.groupby("display_technology", observed=True).annual_primary_generation_TWh.sum().reset_index().rename(columns={"display_technology": "carrier"})
        save(summaries.plot_mix(gen, value="annual_primary_generation_TWh", title="Annual primary generation", unit="TWh", style=style), "annual_generation", "summary", [primary_path], limitation="BESS/PHS discharge excluded from primary generation.")
        compare = cap.loc[~cap.carrier.isin(["BESS","PHS"])].merge(gen, on="carrier", how="left")
        compare["annual_primary_generation_TWh"] = compare.annual_primary_generation_TWh.fillna(0.0)
        compare["capacity_mw"] = compare.installed_capacity_GW * 1000
        compare["generation_mwh"] = compare.annual_primary_generation_TWh * 1e6
        compare["capacity_factor"] = compare.generation_mwh.div(compare.capacity_mw.mul(weights.sum()).replace(0,np.nan))
        compare_path = data("generation_vs_capacity", compare)
        save(summaries.plot_generation_vs_capacity(compare, style=style), "generation_vs_capacity", "summary", [compare_path], limitation="Primary generation and capacity only; BESS/PHS excluded from primary generation capacity factors.")
        vre_ids = list(network.generators.index[network.generators.carrier.isin(["solar_pv_rooftop", "solar_pv_utility", "wind_onshore", "wind_offshore"])])
        vre = summaries.extract_curtailment(io.ResultSource(network=network), vre_ids)
        vre_path = data("uc_vre_curtailment", vre)
        vre["carrier"] = vre.carrier.map(report_cfg["carrier_to_display_technology"])
        save(_plot_mix_including_zero(summaries,vre,value="curtailment_mwh_signed",title="Available minus dispatched energy",unit="MWh",style=style), "vre_curtailment", "operation", [vre_path])
        availability = network.generators_t.p_max_pu.reindex(index=network.snapshots, columns=vre_ids)
        for asset in vre_ids:
            availability[asset] = availability[asset].fillna(float(network.generators.at[asset,"p_max_pu"]))
        vre_hour = pd.DataFrame({"available_MW": availability.mul(network.generators.loc[vre_ids,"p_nom"],axis=1).sum(axis=1), "dispatched_MW": network.generators_t.p[vre_ids].sum(axis=1)})
        vre_hour["curtailed_MW"] = vre_hour.available_MW - vre_hour.dispatched_MW
        vre_hour_path = data("uc_vre_hourly", vre_hour)
        fig, ax = plt.subplots(figsize=(11,4), layout="constrained"); vre_hour.iloc[window].plot(ax=ax,linewidth=.9)
        ax.set(ylabel="MW",title="VRE availability / dispatch / curtailment")
        save(fig,"vre_availability_dispatch","operation",[vre_hour_path])
        net_path = data("canonical_signed_net_interface_MW", net)
        fig, ax = plt.subplots(figsize=(12,5), layout="constrained"); net.iloc[window].plot(ax=ax,linewidth=.8)
        ax.axhline(0,color="0.5",linewidth=.6); ax.set(ylabel="Signed net MW",title="Canonical UC net interface flows")
        ax.legend(loc="upper left",bbox_to_anchor=(1,1),frameon=False,fontsize=7)
        save(fig,"net_interface_flow","exchanges",[net_path],limitation="NET_FLOW_A_TO_B_MW: native signed p0, or legacy A→B minus B→A; gross counterflow excluded.")
        import_frame = diagnostics["canonical_net_imports_hourly_MW"].rename(columns=aliases)
        imports_path = data("canonical_zonal_net_imports_MW",import_frame)
        fig, ax = plt.subplots(figsize=(11,4), layout="constrained"); import_frame.iloc[window].plot(ax=ax,linewidth=.8)
        ax.set(ylabel="MW",title="Zonal signed net imports")
        save(fig,"zonal_net_imports","exchanges",[imports_path],limitation="Positive net imports, negative net exports; UC physical flows.")
        annual = pd.DataFrame({"carrier": import_frame.columns, "imports_MWh": import_frame.clip(lower=0).mul(weights,axis=0).sum().values, "exports_MWh": (-import_frame.clip(upper=0)).mul(weights,axis=0).sum().values})
        annual["net_imports_MWh"] = annual.imports_MWh - annual.exports_MWh
        annual_path = data("annual_zonal_net_import_export",annual)
        save(summaries.plot_mix(annual,value="net_imports_MWh",title="Annual zonal net imports",unit="MWh",style=style),"annual_net_imports","summary",[annual_path],limitation="Signed net hourly domain injections; imports/exports are positive/negative portions, not gross directional flows.")
        _render_reference_maps(network, italian_prices, year, scenario, diagnostics, snapshot, folder, cfg, io, maps, data, save, qa, RECOVERED_R2B_ZONE_COLORS)
    qa.append({"check":"authority_separated_input_binding","status":"PASS","detail":"Physical network supplied separately from LP price_frame; no LP dispatch input accepted"})
    receipt = {"schema_version":"MEM_UC2_VIS_X1_R1", "status":"PASS", "year":int(year),"scenario":str(scenario), "figure_count":len(figures),"figures":figures,"qa":qa,"selected_stress_snapshot":str(snapshot),"stress_selector":"VIS_X1_LEXICOGRAPHIC_ON_CANONICAL_NET_INTERFACES","physical_source":"UC_MILP","price_source":"FIXED_COMMITMENT_LP_DUALS","solver_invocations":0}
    (folder / "FIGURE_METADATA.json").write_text(json.dumps(receipt,indent=2),encoding="utf-8")
    pd.DataFrame(figures).to_csv(folder / "FIGURE_INDEX.csv",index=False)
    pd.DataFrame(qa).to_csv(folder / "MEM_42BUS_REFERENCE_MAP_QA.csv",index=False)
    return receipt


def _render_reference_maps(network, price, year, scenario, diagnostics, snapshot, folder, cfg, io, maps, data, save, qa, palette):
    """Reuse the accepted geographic wrapper and maintain separate result layers."""
    import geopandas as gpd
    from matplotlib.colors import Normalize
    from matplotlib.cm import ScalarMappable
    aliases = cfg["display_market_aliases"]
    transfer = ROOT / Path(cfg["external_reference"])
    buses, lines, links, raw_buses, raw_branches, lineage = _reference_tables(transfer,aliases,qa)
    source = io.ResultSource(tables={"buses":buses,"lines":lines,"links":links,"transformers":pd.DataFrame(columns=["bus0","bus1"])})
    geography = gpd.read_file(PARENT_NUTS2)
    natural_earth = maps.bundled_geography_path()
    colors = {aliases[z]:color for z,color in palette.items()}
    zones = _zone_points(transfer,aliases,qa)
    external = _external_points(natural_earth,qa)
    anchors = pd.concat([zones.rename(columns={"zone":"market"})[["market","display_x","display_y"]],external[["market","display_x","display_y"]]]).set_index("market")
    bus_path, branch_path, anchor_path = data("reference_42bus",raw_buses),data("reference_179_branches",raw_branches),data("visualization_only_zone_anchors",anchors)
    lineage_path = folder / "data/MEM_ITALY_42BUS_REFERENCE_SCAFFOLD.json"
    lineage_path.write_text(json.dumps({"source_lineage":lineage,"bus_count":42,"ac_count":178,"dc_link_count":1,"physical_branch_results":"NOT_FROM_MEM"},indent=2),encoding="utf-8")
    base_data = [bus_path,branch_path,anchor_path,lineage_path]
    def base(title,extent=BASE_EXTENT):
        return _base_map(source,maps,geography,colors,buses.market.to_dict(),extent=extent,title=f"UC2 {year} {scenario} — {title}",qa=qa)[0]
    fig = base("Italy 42-bus reference topology"); _legend(fig)
    save(fig,"MEM_ITALY_REFERENCE_TOPOLOGY_42BUS","network",base_data,reference=True,map_figure=True,limitation="Historical physical reference only; UC2 does not optimize these 178 AC branches or one reference DC Link.")
    registry = diagnostics["net_interface_registry"].copy()
    if not registry.classification.isin(["ITALIAN_INTERZONAL","EXTERNAL_MARKET_INTERFACE","PHYSICAL_OR_HVDC_INTERFACE"]).all():
        raise ValueError("Auxiliary or unclassified Link in net-interface overlay")
    corridors = registry.rename(columns={"display_from":"bus0","display_to":"bus1"})[["interface_id","bus0","bus1","classification"]]
    corridors["bus0"] = corridors.bus0.str.replace("EXT_","",regex=False).replace(aliases)
    corridors["bus1"] = corridors.bus1.str.replace("EXT_","",regex=False).replace(aliases)
    missing = set(corridors.bus0).union(corridors.bus1) - set(anchors.index)
    if missing:
        raise ValueError(f"Accepted interface has no geographic display anchor: {missing}")
    corridors["signed_net_p0_MW"] = diagnostics["net_interface_hourly_MW"].loc[snapshot].reindex(corridors.interface_id).to_numpy()
    util = diagnostics["net_interface_positive_utilization_hourly"].combine(diagnostics["net_interface_negative_utilization_hourly"],np.maximum)
    corridors["hours_at_or_above_threshold"] = util.ge(.9).mul(canonical._weights(network,"generator"),axis=0).sum().reindex(corridors.interface_id).to_numpy()
    _render_stage_b_maps(registry, anchors, corridors, diagnostics["price_annual_summary"],
                         year, scenario, snapshot, cfg, io, maps, data, save, qa, palette)


def _stage_b_graph_tables(registry, anchors, corridors, annual_prices, cfg):
    """Bind existing R1 corridor quantities to one existing display point per MEM node."""
    aliases = cfg["display_market_aliases"]
    expected = registry.rename(columns={"display_from": "bus0", "display_to": "bus1"})[
        ["interface_id", "bus0", "bus1", "classification"]].set_index("interface_id")
    actual = corridors.set_index("interface_id")[expected.columns]
    pd.testing.assert_frame_equal(actual.sort_index(), expected.sort_index(), check_dtype=False)
    if expected.index.duplicated().any() or expected.classification.value_counts().to_dict() != {
            "ITALIAN_INTERZONAL": 10, "EXTERNAL_MARKET_INTERFACE": 8, "PHYSICAL_OR_HVDC_INTERFACE": 2}:
        raise ValueError("MEM canonical interface registry differs from accepted R1 graph")
    names = set(expected.bus0).union(expected.bus1)
    italian = set(aliases.values())
    required_external = {"FR", "CH", "AT", "SI", "ME", "GR", "TN", "MT"}
    if names != italian | required_external | {"CORS"} or not anchors.index.is_unique:
        raise ValueError("MEM graph requires seven Italian nodes, eight external markets and applicable CORS hub")
    nodes = anchors.loc[[name for name in anchors.index if name in names]].copy()
    nodes["role"] = ["ITALIAN_MARKET" if name in italian else "CORS_NO_PRICE_HUB" if name == "CORS"
                     else "EXTERNAL_MARKET" for name in nodes.index]
    model_ids = dict(zip(registry.display_from, registry.from_bus))
    model_ids.update(zip(registry.display_to, registry.to_bus))
    nodes["model_bus_id"] = nodes.index.map(model_ids)
    nodes = nodes.rename(columns={"display_x": "x", "display_y": "y"})
    if nodes[["x", "y"]].isna().any().any() or not np.isfinite(nodes[["x", "y"]].to_numpy()).all():
        raise ValueError("Missing accepted R1 display coordinates")
    prices = annual_prices.loc[annual_prices.market.isin(aliases)].copy()
    prices["market"] = prices.market.map(aliases)
    if len(prices) != 7 or not prices.market.is_unique or set(prices.market) != italian:
        raise ValueError("Exactly one canonical LP price per Italian zone required")
    price_nodes = nodes.loc[prices.market].copy()
    price_nodes["load_weighted_mean_EUR_per_MWh"] = prices.set_index("market").load_weighted_mean_EUR_per_MWh
    if not np.isfinite(price_nodes.load_weighted_mean_EUR_per_MWh).all():
        raise ValueError("Nonfinite canonical Italian zonal price")
    links = expected.copy()
    links["p_nom"] = registry.set_index("interface_id")[["positive_capacity_MW", "negative_capacity_MW"]].max(axis=1)
    return nodes, links, price_nodes


def _render_stage_b_maps(registry, anchors, corridors, annual_prices, year, scenario, snapshot,
                         cfg, io, maps, data, save, qa, palette, source_paths=()):
    """Three current result maps: actual MEM graph, zero reference AC branches."""
    from matplotlib.collections import PathCollection
    from matplotlib.colors import Normalize
    from matplotlib.cm import ScalarMappable
    from matplotlib.lines import Line2D
    nodes, links, price_nodes = _stage_b_graph_tables(registry, anchors, corridors, annual_prices, cfg)
    aliases = cfg["display_market_aliases"]
    colors = {aliases[zone]: color for zone, color in palette.items()}
    groups = {name: name if name in colors else "CORS hub (no price)" if name == "CORS"
              else "External market" for name in nodes.index}
    source = io.ResultSource(tables={"buses": nodes.assign(carrier="MEM_market_display"), "links": links,
                                    "lines": pd.DataFrame(columns=["bus0", "bus1"]),
                                    "transformers": pd.DataFrame(columns=["bus0", "bus1"])})
    sources = list(source_paths) + [data("MEM_STAGE_B_GRAPH_NODES", nodes),
                                   data("MEM_STAGE_B_GRAPH_INTERFACES", corridors),
                                   data("MEM_STAGE_B_PRICE_MARKERS", price_nodes)]
    def base(title, price=False, utilization=False):
        fig, plotted = maps.plot_zonal_physical_network(
            source, bus_grouping=groups, group_colors={**colors, "External market": "#7b8993",
                                                      "CORS hub (no price)": "#9b73a6"},
            link_classification={name: "internal" if row.classification == "ITALIAN_INTERZONAL" else "external"
                                 for name, row in links.iterrows()},
            geography=maps.bundled_geography_path(), extent=OVERLAY_EXTENT,
            show_group_labels=False, show_bus_labels=False, figsize=(14, 11),
            title=f"UC2 {year} {scenario} — {title}")
        ax = fig.axes[0]
        if set(plotted.branch_id) != set(links.index) or len(plotted) != 20 or not plotted.component.eq("links").all():
            raise ValueError("Plotted MEM edge set does not match canonical registry")
        markers = [artist for artist in ax.collections if isinstance(artist, PathCollection)]
        if sum(len(artist.get_offsets()) for artist in markers) != 16:
            raise ValueError("Result map must render each of the 16 MEM nodes exactly once")
        for artist in markers:
            artist.set_zorder(12)
            artist.set_sizes([65 if len(artist.get_offsets()) == 1 else 35])
        for artist in ax.lines:
            artist.set_linewidth(1.1); artist.set_alpha(.5)
        for name, row in nodes.iterrows():
            if price and name in colors:
                continue
            offset = (6, -16) if name == "SICI" else (8, 14) if name == "CNORD" else (-72, -5) if name == "CORS" else (5, 5)
            ax.annotate(name + " (no price)" if name == "CORS" else name, (row.x, row.y),
                        xytext=offset, textcoords="offset points", fontsize=8,
                        weight="bold" if name in colors else "normal", zorder=12)
        context = price or utilization
        handles = [Line2D([], [], color="#295f80" if context else "#a9255e", lw=2 if context else 3,
                          ls="--" if context else "-", label="MEM Italian interzonal interface"),
                   Line2D([], [], color="#ad6036" if context else "#d17a25", lw=2 if context else 3,
                          ls="--" if context else "-", label="MEM external / CORS interface")]
        ax.legend(handles=handles, title="Canonical MEM interfaces — no AC Lines", loc="lower left",
                  bbox_to_anchor=(1.01, 0), frameon=False, fontsize=8)
        qa.extend([{"check": title + "_canonical_20_edges_zero_AC", "status": "PASS", "detail": "20 canonical Links; no PyPSA-IT branches"},
                   {"check": title + "_16_unique_MEM_nodes", "status": "PASS", "detail": "7 Italian + 8 external + applicable CORS"}])
        return fig, markers

    fig, markers = base("Annual load-weighted MEM zonal prices", price=True)
    values = price_nodes.load_weighted_mean_EUR_per_MWh
    norm = Normalize(vmin=values.min(), vmax=values.max() if values.max() != values.min() else values.max() + 1)
    cmap = plt.get_cmap("YlOrRd")
    colored = 0
    for name, row in price_nodes.iterrows():
        matching = [artist for artist in markers if len(artist.get_offsets()) == 1 and
                    np.allclose(artist.get_offsets()[0], [row.x, row.y])]
        if len(matching) != 1:
            raise ValueError(f"Exactly one price marker required: {name}")
        marker = matching[0]
        marker.set_facecolor(cmap(norm(row.load_weighted_mean_EUR_per_MWh)))
        marker.set_edgecolor(colors[name]); marker.set_linewidth(1.6); marker.set_sizes([120])
        ax = fig.axes[0]
        ax.annotate(f"{name}\n€{row.load_weighted_mean_EUR_per_MWh:.1f}/MWh", (row.x, row.y),
                    xytext=(6, -18), textcoords="offset points", fontsize=8, weight="bold", zorder=13,
                    bbox={"facecolor": "white", "edgecolor": "none", "alpha": .9})
        colored += 1
    if colored != 7:
        raise ValueError("Result price map must have seven price markers")
    fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=fig.axes[0], shrink=.55,
                 label="Canonical LP zonal load-weighted price [EUR/MWh]")
    save(fig, "MEM_ITALY_42BUS_ZONAL_PRICE", "network", sources, map_figure=True,
         limitation="One price value per Italian MEM zone; external/CORS nodes have no price overlay. Legacy filename retained for existing links.")
    fig, _ = base(f"Signed net interface MW — {snapshot}")
    _flow_overlay(fig, corridors, anchors.loc[nodes.index], show_anchors=False, label_min_mw=0,
                  label_offset_points=(0, -9))
    for row in corridors.loc[corridors.signed_net_p0_MW.abs().lt(1)].itertuples(index=False):
        a, b = nodes.loc[row.bus0], nodes.loc[row.bus1]
        fig.axes[0].annotate(f"{abs(row.signed_net_p0_MW):,.1f} MW", ((a.x+b.x)/2, (a.y+b.y)/2), fontsize=6)
    save(fig, "MEM_ITALY_42BUS_INTERZONAL_FLOW", "network", sources, map_figure=True,
         limitation="UC MILP canonical signed net interface flows at unchanged deterministic stress hour; no physical AC branches. Legacy filename retained.")
    fig, _ = base("Horizon MEM net-interface utilization", utilization=True)
    _flow_overlay(fig, corridors, anchors.loc[nodes.index], utilization=True, show_anchors=False)
    save(fig, "MEM_ITALY_42BUS_UTILIZATION_CONTEXT", "network", sources, map_figure=True,
         limitation="Existing canonical net-interface hours ≥90% directional capacity; no AC line loading. Legacy filename retained.")
    qa.extend([{"check": "exactly_seven_Italian_price_markers", "status": "PASS", "detail": "No repeated values on physical reference buses"},
               {"check": "MEM_graph_coordinate_reuse", "status": "PASS", "detail": "Existing R1 display anchors only; visualization coordinates"}])







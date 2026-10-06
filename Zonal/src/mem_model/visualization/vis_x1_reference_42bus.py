"""Four VIS-X1 maps with a historical 42-bus reference and zonal MEM overlays.

The recovered PyPSA-IT buses, AC Lines and DC Link are context only. MEM
results enter through separate market-level overlays, never through those
physical-reference branches. No network-building or optimization API is used.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from matplotlib import colors as mcolors
from matplotlib.cm import ScalarMappable
from matplotlib.collections import PathCollection
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mem_model.visualization.vis_x1 import (  # noqa: E402
    CONFIG, FIGURE_STATUS, OUTPUT, ROOT, WARNING, _relative, _sha, _toolkit,
)
from mem_model.visualization.vis_x1_geographic import PARENT_BUS_TABLE, PARENT_NUTS2  # noqa: E402


BRANCH_TABLE = "gallery/01b_plotted_branch_qa.csv"
BRANCH_COUNTS = "gallery/01b_map_qa.json"
SOURCE_ROLE = "PYPSA_IT_42BUS_REFERENCE_TOPOLOGY"
DISPLAY_ROLE = "MEM_ITALY_42BUS_REFERENCE_SCAFFOLD"
BASE_EXTENT = (6.5, 19.2, 36.2, 47.3)
OVERLAY_EXTENT = (1.0, 25.0, 31.5, 50.0)


def _check(ok: bool, name: str, qa: list[dict], detail: str = "") -> None:
    qa.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail})
    if not ok:
        raise ValueError(f"42-bus map QA failed: {name}: {detail}")


def _reference_tables(transfer: Path, aliases: dict[str, str], qa: list[dict]):
    bus_path, branch_path, count_path = (transfer / PARENT_BUS_TABLE,
                                         transfer / BRANCH_TABLE, transfer / BRANCH_COUNTS)
    seal = json.loads((transfer / "TRANSFER_MANIFEST.json").read_text(encoding="utf-8"))
    expected = seal["files_sha256"]
    for relative, path in ((PARENT_BUS_TABLE, bus_path), (BRANCH_TABLE, branch_path),
                           (BRANCH_COUNTS, count_path)):
        _check(_sha(path).lower() == expected[relative].lower(),
               f"sealed_source_hash_{path.name}", qa)
    buses_raw = pd.read_csv(bus_path)
    branches_raw = pd.read_csv(branch_path)
    counts = json.loads(count_path.read_text(encoding="utf-8"))
    _check(len(buses_raw) == counts["buses_in_network"] == 42 and buses_raw.bus_id.is_unique,
           "reference_42_bus_identity", qa)
    _check(len(branches_raw) == 179 and branches_raw.branch_id.is_unique,
           "reference_179_branch_identity", qa)
    _check(branches_raw.component.value_counts().to_dict() == {"lines": 178, "links": 1},
           "reference_178_ac_one_link", qa)
    _check(counts["lines_plotted"] == 178 and counts["links_plotted"] == 1,
           "sealed_gallery_branch_counts", qa)
    _check(set(branches_raw.bus0).union(branches_raw.bus1).issubset(set(buses_raw.bus_id)),
           "reference_branch_endpoints", qa)
    _check(branches_raw.loc[branches_raw.component.eq("links"), "branch_id"].str.contains("500-DC").all(),
           "reference_dc_link_identity", qa)
    _check(buses_raw[["x", "y"]].notna().all().all() and
           set(buses_raw.gme_zone) == set(aliases), "reference_coordinates_and_zones", qa)
    for zone, expected_count in counts["market_groups"].items():
        _check(int(buses_raw.gme_zone.eq(zone).sum()) == int(expected_count),
               f"reference_zone_count_{zone}", qa)
    buses = buses_raw.set_index("bus_id")[["x", "y", "gme_zone"]].copy()
    buses["market"] = buses.gme_zone.map(aliases)
    buses["carrier"] = "PyPSA-IT physical reference bus"
    lines = branches_raw.loc[branches_raw.component.eq("lines")].set_index("branch_id")[["bus0", "bus1"]]
    links = branches_raw.loc[branches_raw.component.eq("links")].set_index("branch_id")[["bus0", "bus1"]]
    return buses, lines, links, buses_raw, branches_raw, {
        "bus_table": {"path": str(bus_path), "sha256": _sha(bus_path)},
        "branch_table": {"path": str(branch_path), "sha256": _sha(branch_path)},
        "branch_counts": {"path": str(count_path), "sha256": _sha(count_path)},
    }


def _base_map(source, maps, geography, colors, groups, *, extent, title, qa: list[dict],
              figsize=(14, 11)):
    fig, plotted = maps.plot_zonal_physical_network(
        source, bus_grouping=groups, group_colors=colors, geography=geography,
        extent=extent, show_group_labels=True, show_bus_labels=False,
        figsize=figsize, title=title,
    )
    _check(len(plotted) == 179 and int(plotted.component.eq("lines").sum()) == 178 and
           int(plotted.component.eq("links").sum()) == 1,
           "toolkit_plotted_all_reference_branches", qa)
    ax = fig.axes[0]
    _check(sum(len(c.get_offsets()) for c in ax.collections if isinstance(c, PathCollection)) == 42,
           "toolkit_rendered_all_42_buses", qa)
    # Toolkit layout and geography are retained; only the layer hierarchy is
    # adjusted so the later MEM overlay cannot read as a reference AC line.
    for artist in ax.lines:
        if artist.get_linestyle() == "--":
            artist.set_color("#88679c")
            artist.set_linewidth(1.7)
            artist.set_alpha(.8)
        else:
            artist.set_color("#83929a")
            artist.set_linewidth(.65)
            artist.set_alpha(.50)
    from matplotlib.legend import Legend
    for artist in ax.artists:
        if isinstance(artist, Legend):
            artist.set_title("GME market zones")
    return fig, plotted


def _legend(fig, *, result=None):
    handles = [
        Line2D([], [], color="#83929a", lw=.9, label="PyPSA-IT reference AC"),
        Line2D([], [], color="#88679c", lw=1.8, ls="--", label="PyPSA-IT reference DC Link"),
    ]
    if result == "price":
        handles.append(Line2D([], [], marker="o", linestyle="", markersize=9,
                              markerfacecolor="white", markeredgecolor="#4b2848",
                              markeredgewidth=2, label="MEM zonal price halo"))
    elif result == "flow":
        handles.extend([
            Line2D([], [], color="#a9255e", lw=3.5, label="MEM interzonal net MW"),
            Line2D([], [], color="#d17a25", lw=3.5, label="MEM external/CORS net MW"),
        ])
    elif result == "utilization":
        handles.append(Line2D([], [], color="#a9255e", lw=3.5,
                              label="MEM interface ≥90% hours"))
    fig.axes[0].legend(handles=handles, title="Reference / MEM result layers",
                       loc="lower left", bbox_to_anchor=(1.01, 0), frameon=False, fontsize=7)


def _footer(fig, *, result: bool):
    if result:
        fig.text(.01, -.035, WARNING, fontsize=8, color="#9b1c31", weight="bold")
        fig.text(.99, -.035, "MEM Stage-B zonal/interface results; reference AC has no MEM loading",
                 fontsize=7, color="#33485c", ha="right")
    else:
        fig.text(.01, -.035, "PYPSA-IT 42-BUS REFERENCE TOPOLOGY | NO MEM PHYSICAL BRANCH RESULTS",
                 fontsize=8, color="#46576b", weight="bold")


def _save(fig, path: Path, io, *, result: bool, data: list[Path], limitation: str) -> dict:
    _footer(fig, result=result)
    paths = [io.save_figure(fig, path.with_suffix(ext)) for ext in (".png", ".svg", ".pdf")]
    plt.close(fig)
    return {"figure": path.stem, "status": FIGURE_STATUS if result else "REFERENCE_ONLY",
            "spatial_scaffold": SOURCE_ROLE, "mem_result_resolution": "STAGE_B_ZONAL" if result else "NONE",
            "physical_branch_results": "NOT_FROM_MEM",
            "png": _relative(paths[0]), "svg": _relative(paths[1]), "pdf": _relative(paths[2]),
            "underlying_data": "|".join(_relative(p) for p in data), "limitation": limitation}


def _overlay_points(path: Path, qa: list[dict]) -> pd.DataFrame:
    zones = pd.read_csv(path / "data/MEM_GME_ZONE_DISPLAY_COORDINATES.csv")
    external = pd.read_csv(path / "data/MEM_EXTERNAL_MARKET_DISPLAY_COORDINATES.csv")
    _check(len(zones) == 7 and zones.zone.is_unique and len(external) == 9,
           "existing_zonal_and_boundary_anchors", qa)
    return pd.concat([
        zones.rename(columns={"zone": "market"})[["market", "display_x", "display_y"]],
        external[["market", "display_x", "display_y"]],
    ], ignore_index=True).set_index("market")


def _flow_overlay(fig, corridors: pd.DataFrame, anchors: pd.DataFrame, *, utilization=False,
                  show_anchors=True, label_min_mw=1000, label_offset_points=(0, 0)):
    ax = fig.axes[0]
    if utilization:
        cmap = plt.get_cmap("magma")
        norm = mcolors.Normalize(vmin=0, vmax=max(1.0, float(corridors.hours_at_or_above_threshold.max())))
    else:
        max_flow = max(1.0, float(corridors.signed_net_p0_MW.abs().max()))
    for row in corridors.itertuples(index=False):
        x0, y0 = anchors.loc[row.bus0, ["display_x", "display_y"]]
        x1, y1 = anchors.loc[row.bus1, ["display_x", "display_y"]]
        if utilization:
            color = cmap(norm(row.hours_at_or_above_threshold))
            ax.plot([x0, x1], [y0, y1], color="white", lw=5.2, alpha=.9, zorder=8)
            ax.plot([x0, x1], [y0, y1], color=color, lw=3.2, alpha=.92, zorder=9)
        else:
            mw = float(row.signed_net_p0_MW)
            if abs(mw) < 1:
                continue
            color = "#a9255e" if row.classification == "ITALIAN_INTERZONAL" else "#d17a25"
            start, end = ((x0, y0), (x1, y1)) if mw >= 0 else ((x1, y1), (x0, y0))
            width = 1.8 + 2.5 * np.sqrt(abs(mw) / max_flow)
            ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=12,
                                         lw=width + 2.1, color="white", alpha=.84,
                                         shrinkA=9, shrinkB=9, zorder=8))
            ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=12,
                                         lw=width, color=color, alpha=.88,
                                         shrinkA=9, shrinkB=9, zorder=9))
            if abs(mw) >= label_min_mw:
                ax.annotate(f"{abs(mw):,.0f} MW", ((x0+x1)/2, (y0+y1)/2),
                            xytext=label_offset_points, textcoords="offset points",
                            fontsize=6, color="#263444", ha="center", va="center", zorder=11,
                            bbox={"facecolor": "white", "edgecolor": "none", "alpha": .92, "pad": 1})
    for market, row in (anchors.iterrows() if show_anchors else []):
        ax.scatter(row.display_x, row.display_y, s=70 if market in
                   {"NORD", "CNORD", "CSUD", "SUD", "CALA", "SICI", "SARD"} else 25,
                   marker="D" if market in {"NORD", "CNORD", "CSUD", "SUD", "CALA", "SICI", "SARD"} else "o",
                   facecolor="white", edgecolor="#263444", lw=1.3, zorder=12)
        if market not in {"NORD", "CNORD", "CSUD", "SUD", "CALA", "SICI", "SARD"}:
            ax.annotate(market, (row.display_x, row.display_y), xytext=(4, 4),
                        textcoords="offset points", fontsize=7, color="#263444", zorder=13)
    if utilization:
        fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=ax, shrink=.56,
                     label="MEM interface hours ≥90% directional rating")


def build_reference_overlays() -> Path:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    _ = _toolkit(cfg)  # Validate sealed toolkit before importing its map API.
    from adapters.pypsa_it_adapter import RECOVERED_R2B_ZONE_COLORS
    from visualization_toolkit import io, network_maps

    qa: list[dict] = []
    case = cfg["case"]
    _check(case["result_status"] == FIGURE_STATUS, "historic_result_role", qa)
    network_path = ROOT / case["network"]
    acceptance = json.loads((ROOT / case["acceptance"]).read_text(encoding="utf-8"))
    _check(acceptance["status"] == "ACCEPTED_IMMUTABLE" and
           _sha(network_path).lower() == acceptance["solved_network_sha256"].lower(),
           "accepted_historical_network_hash", qa)
    folder = OUTPUT / case["model_version"] / str(case["year"]) / case["scenario"]
    existing_metadata = json.loads((folder / "FIGURE_METADATA.json").read_text(encoding="utf-8"))
    _check(existing_metadata["figure_count"] == 26 and existing_metadata["qa_checks"] == 37 and
           existing_metadata["qa_status"] == "PASS", "existing_26_and_37_qa", qa)
    secondary = folder / "network/geographic"
    secondary_metadata_path = secondary / "MEM_MARKET_NETWORK_FIGURE_METADATA.json"
    secondary_metadata = json.loads(secondary_metadata_path.read_text(encoding="utf-8"))
    _check(secondary_metadata["qa_status"] == "PASS" and secondary_metadata["figure_count"] == 3,
           "existing_seven_node_map_pass", qa)
    original_paths = [network_path, folder / "FIGURE_METADATA.json", folder / "FIGURE_INDEX.csv",
                      folder / "diagnostics/qa_reconciliation.csv",
                      folder / "data/stress_snapshot_link_flows.csv",
                      folder / "data/italian_zonal_prices_eur_per_mwh.parquet",
                      secondary / "MEM_MARKET_NETWORK_GEOGRAPHIC.png",
                      secondary / "MEM_MARKET_NETWORK_FLOW_20190607_0500.png",
                      secondary / "MEM_MARKET_NETWORK_UTILIZATION.png"]
    original_hashes = {str(p): _sha(p) for p in original_paths}

    transfer = ROOT / Path(cfg["external_reference"])
    transfer_seal = json.loads((transfer / "TRANSFER_MANIFEST.json").read_text(encoding="utf-8"))
    adapter_rel = "adapters/pypsa_it_adapter.py"
    _check(_sha(transfer / adapter_rel).lower() ==
           transfer_seal["adapter_file_hashes"][adapter_rel].lower(),
           "sealed_parent_palette_adapter_hash", qa)
    buses, lines, links, buses_raw, branches_raw, source_lineage = _reference_tables(
        transfer, cfg["display_market_aliases"], qa)
    geo_path = network_maps.bundled_geography_path()
    _check(geo_path.is_file() and PARENT_NUTS2.is_file(), "reference_geography_sources_resolve", qa)
    import geopandas as gpd
    # This is the original recovered NUTS2 proxy geography; the toolkit still
    # draws it with its established geographic network-map function.
    nuts = gpd.read_file(PARENT_NUTS2)
    _check(str(nuts.crs) == "EPSG:4326" and nuts.NUTS_ID.eq("MT00").sum() == 1,
           "original_nuts2_geography_epsg4326", qa)
    source_lineage["geography"] = {"path": str(PARENT_NUTS2), "sha256": _sha(PARENT_NUTS2),
                                   "role": "recovered_NUTS2_proxy_context"}
    source_lineage["packaged_natural_earth"] = {"path": str(geo_path), "sha256": _sha(geo_path),
                                                 "role": "VIS_X1_packaged_geography_reference"}
    source = io.ResultSource(tables={"buses": buses, "lines": lines, "links": links,
                                     "transformers": pd.DataFrame(columns=["bus0", "bus1"])})
    colors = {cfg["display_market_aliases"][z]: color for z, color in RECOVERED_R2B_ZONE_COLORS.items()}
    _check(len(colors) == 7 and set(buses.market) == set(colors), "parent_zone_palette_and_mapping", qa)
    _check(colors == {"NORD": "#4c78a8", "CNORD": "#54a24b", "CSUD": "#f58518",
                      "SUD": "#e45756", "CALA": "#b279a2", "SICI": "#eeca3b",
                      "SARD": "#72b7b2"}, "recovered_svg_zone_colours", qa)
    groups = buses.market.to_dict()

    output = folder / "network/reference_42bus"
    data = output / "data"
    data.mkdir(parents=True, exist_ok=True)
    bus_out = data / "MEM_ITALY_42BUS_REFERENCE_SCAFFOLD_BUSES.csv"
    branch_out = data / "MEM_ITALY_42BUS_REFERENCE_SCAFFOLD_BRANCHES.csv"
    buses_raw.to_csv(bus_out, index=False)
    branches_raw.to_csv(branch_out, index=False)
    (data / "MEM_ITALY_42BUS_REFERENCE_SCAFFOLD.json").write_text(json.dumps({
        "name": DISPLAY_ROLE, "spatial_scaffold": SOURCE_ROLE,
        "mem_result_resolution": "STAGE_B_ZONAL", "physical_branch_results": "NOT_FROM_MEM",
        "bus_count": 42, "ac_line_count": 178, "dc_link_count": 1,
        "source_lineage": source_lineage,
        "limitation": "Reference topology from historical PyPSA-IT; not the solved MEM network.",
    }, indent=2), encoding="utf-8")

    figures = []
    fig, plotted = _base_map(source, network_maps, nuts, colors, groups,
                             extent=BASE_EXTENT, title="Italy 42-bus physical reference topology by GME zone",
                             qa=qa, figsize=(13, 12))
    _legend(fig)
    plotted_path = data / "MEM_ITALY_REFERENCE_TOPOLOGY_42BUS_PLOTTED.csv"
    plotted.to_csv(plotted_path, index=False)
    figures.append(_save(fig, output / "MEM_ITALY_REFERENCE_TOPOLOGY_42BUS", io, result=False,
                         data=[bus_out, branch_out, plotted_path],
                         limitation="Historical PyPSA-IT 42-bus physical reference; no MEM nodal or AC branch result."))

    canonical_price = pd.read_csv(ROOT / case["canonical_statistics"] / "zonal_price_statistics.csv")
    price_by_zone = {cfg["display_market_aliases"][row.zone]: float(row.load_weighted_mean_EUR_per_MWh)
                     for row in canonical_price.itertuples(index=False)}
    _check(set(price_by_zone) == set(colors), "seven_canonical_market_prices", qa)
    bus_price = buses_raw[["bus_id", "gme_zone", "x", "y"]].copy()
    bus_price["display_zone"] = bus_price.gme_zone.map(cfg["display_market_aliases"])
    bus_price["mem_load_weighted_zonal_price_EUR_per_MWh"] = bus_price.display_zone.map(price_by_zone)
    _check(bus_price.groupby("display_zone").mem_load_weighted_zonal_price_EUR_per_MWh.nunique().eq(1).all(),
           "same_mem_price_at_every_bus_in_each_zone", qa)
    price_out = data / "MEM_ITALY_42BUS_ZONAL_PRICE_BUS_HALOS.csv"
    bus_price.to_csv(price_out, index=False)
    fig, _ = _base_map(source, network_maps, nuts, colors, groups,
                       extent=BASE_EXTENT, title="MEM 2040 Base load-weighted zonal price on 42-bus reference",
                       qa=qa, figsize=(13, 12))
    ax = fig.axes[0]
    price_values = bus_price.mem_load_weighted_zonal_price_EUR_per_MWh.to_numpy()
    norm = mcolors.Normalize(vmin=float(price_values.min())-.15, vmax=float(price_values.max())+.15)
    cmap = plt.get_cmap("coolwarm")
    ax.scatter(bus_price.x, bus_price.y, c=price_values, cmap=cmap, norm=norm,
               s=125, edgecolor="white", linewidth=.4, alpha=.85, zorder=7)
    ax.scatter(bus_price.x, bus_price.y, color=[colors[z] for z in bus_price.display_zone],
               s=29, edgecolor="white", linewidth=.4, zorder=8)
    fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=ax, shrink=.57,
                 label="MEM load-weighted mean zonal price (EUR/MWh)")
    _legend(fig, result="price")
    figures.append(_save(fig, output / "MEM_ITALY_42BUS_ZONAL_PRICE", io, result=True,
                         data=[bus_out, branch_out, price_out],
                         limitation="Each physical-reference bus shows its zone's one MEM price; no 42-bus nodal price is claimed."))

    anchors = _overlay_points(secondary, qa)
    corridors = pd.read_csv(secondary / "data/MEM_MARKET_NETWORK_CORRIDORS.csv")
    contributions = pd.read_csv(secondary / "data/MEM_MARKET_NETWORK_FLOW_LINK_CONTRIBUTIONS.csv")
    original_flows = pd.read_csv(folder / "data/stress_snapshot_link_flows.csv").set_index("branch_id")
    _check(len(corridors) == 20 and len(contributions) == 40 and
           str(pd.Timestamp(corridors.snapshot.iloc[0])) == existing_metadata["selected_snapshot"],
           "same_selected_hour_and_40_mem_links", qa)
    _check(set(contributions.link_id) == set(original_flows.index), "mem_flow_link_identity", qa)
    diff = (contributions.set_index("link_id").p0_MW - original_flows.loc[contributions.link_id, "p0_mw"]).abs().max()
    _check(float(diff) < 1e-5, "external_and_internal_flow_reconcile_to_existing_qa", qa,
           f"max_abs_MW={diff}")
    net = contributions.groupby("corridor_id").signed_corridor_contribution_MW.sum()
    _check(np.allclose(net.loc[corridors.corridor_id], corridors.signed_net_p0_MW, atol=1e-5),
           "signed_corridor_net_reconciles", qa)
    _check(not any(col in branches_raw for col in ("p0_MW", "signed_net_p0_MW", "hours_at_or_above_threshold")),
           "physical_reference_branches_have_no_mem_results", qa)
    flow_out = data / "MEM_ITALY_42BUS_INTERZONAL_FLOW_OVERLAY.csv"
    corridors.to_csv(flow_out, index=False)
    fig, _ = _base_map(source, network_maps, nuts, colors, groups,
                       extent=OVERLAY_EXTENT, title="MEM signed zonal/interface flow over 42-bus reference",
                       qa=qa, figsize=(16, 11))
    _flow_overlay(fig, corridors, anchors)
    _legend(fig, result="flow")
    figures.append(_save(fig, output / "MEM_ITALY_42BUS_INTERZONAL_FLOW", io, result=True,
                         data=[bus_out, branch_out, flow_out,
                               secondary / "data/MEM_MARKET_NETWORK_FLOW_LINK_CONTRIBUTIONS.csv"],
                         limitation="MEM selected-hour net MW is drawn only between market anchors; recovered AC/DC branches carry no MEM flows."))

    utilization = pd.read_csv(secondary / "data/MEM_MARKET_NETWORK_UTILIZATION.csv")
    _check(len(utilization) == 20 and set(utilization.branch_id) == set(corridors.corridor_id),
           "mem_interface_utilization_corridors", qa)
    util_out = data / "MEM_ITALY_42BUS_UTILIZATION_CONTEXT_OVERLAY.csv"
    utilization.to_csv(util_out, index=False)
    fig, _ = _base_map(source, network_maps, nuts, colors, groups,
                       extent=OVERLAY_EXTENT, title="MEM interface utilization context over 42-bus reference",
                       qa=qa, figsize=(16, 11))
    _flow_overlay(fig, utilization, anchors, utilization=True)
    _legend(fig, result="utilization")
    figures.append(_save(fig, output / "MEM_ITALY_42BUS_UTILIZATION_CONTEXT", io, result=True,
                         data=[bus_out, branch_out, util_out],
                         limitation="Coloured bands show max directional MEM Link hours ≥90%; they are not recovered AC line loading or binding congestion."))

    for path, digest in original_hashes.items():
        _check(_sha(Path(path)) == digest, f"unchanged_{Path(path).name}", qa)
    _check(len(figures) == 4 and all(f["physical_branch_results"] == "NOT_FROM_MEM" for f in figures),
           "four_figures_separate_physical_and_mem_resolution", qa)
    _check(all(WARNING in (ROOT / f["svg"]).read_text(encoding="utf-8") for f in figures[1:]),
           "pre_p2x_warning_on_each_result_svg", qa)
    qa_path = output / "MEM_42BUS_REFERENCE_MAP_QA.csv"
    pd.DataFrame(qa).to_csv(qa_path, index=False)
    pd.DataFrame(figures).to_csv(output / "MEM_ITALY_42BUS_FIGURE_INDEX.csv", index=False)
    (output / "MEM_ITALY_42BUS_FIGURE_METADATA.json").write_text(json.dumps({
        "spatial_scaffold": SOURCE_ROLE, "mem_result_resolution": "STAGE_B_ZONAL",
        "physical_branch_results": "NOT_FROM_MEM", "primary_geographic_figure": "MEM_ITALY_REFERENCE_TOPOLOGY_42BUS",
        "secondary_market_map_role": "MEM_MARKET_NETWORK_SCHEMATIC_GEOGRAPHIC",
        "result_status": FIGURE_STATUS, "reference_status": "REFERENCE_ONLY",
        "warning": WARNING, "selected_snapshot": existing_metadata["selected_snapshot"],
        "reference_bus_count": 42, "reference_ac_line_count": 178, "reference_dc_link_count": 1,
        "source_lineage": source_lineage, "figures": figures,
        "qa_status": "PASS", "qa_checks": len(qa),
    }, indent=2), encoding="utf-8")
    # Reclassify the prior seven-node view in its metadata; retain all existing
    # map images, data and original MEM gallery bytes unchanged.
    secondary_metadata["presentation_role"] = "MEM_MARKET_NETWORK_SCHEMATIC_GEOGRAPHIC"
    secondary_metadata["primary_geographic_scaffold"] = SOURCE_ROLE
    secondary_metadata["legacy_figure_filename"] = "MEM_MARKET_NETWORK_GEOGRAPHIC"
    secondary_metadata_path.write_text(json.dumps(secondary_metadata, indent=2), encoding="utf-8")
    return output


if __name__ == "__main__":
    print(build_reference_overlays())

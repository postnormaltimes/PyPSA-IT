"""Unsaved publication maps from existing UC/LP reporting tables only.

The caller owns selection, authority/pin verification, export and promotion.
No network is opened, optimization invoked, or saved reporting artifact edited.
"""
from __future__ import annotations

from pathlib import Path
import math
from types import SimpleNamespace

import matplotlib.pyplot as plt
from matplotlib.cm import ScalarMappable
from matplotlib.collections import LineCollection, PatchCollection, PathCollection
from matplotlib.colors import Normalize
from matplotlib.lines import Line2D
from matplotlib.patches import PathPatch
import numpy as np
import pandas as pd

from .publication_figures import _Inputs, _ordered, _style, PRICE_WEIGHT
from .uc2_vis_x1 import _stage_b_graph_tables
from .vis_x1 import CONFIG, _sha, _toolkit
from .vis_x1_reference_42bus import _flow_overlay, OVERLAY_EXTENT

ZONES = ("NORD", "CNORD", "CSUD", "SUD", "CALA", "SICI", "SARD")
SCAFFOLD = "MEM_STAGE_B_7ZONE_EXTERNAL_GRAPH"
VIEWS = {"price", "flow", "utilization"}


def draw_context(ax, nodes, links, cfg):
    """Existing toolkit geography for Figure 8; no additional result overlay."""
    _, _, _, io, maps, _, _, _ = _toolkit(cfg)
    from adapters.pypsa_it_adapter import RECOVERED_R2B_ZONE_COLORS
    colors = {cfg["display_market_aliases"][zone]: color for zone, color in RECOVERED_R2B_ZONE_COLORS.items()}
    geography = maps.bundled_geography_path()
    source = io.ResultSource(tables={"buses": nodes.assign(carrier="MEM_market_display"), "links": links,
        "lines": pd.DataFrame(columns=["bus0", "bus1"]), "transformers": pd.DataFrame(columns=["bus0", "bus1"])})
    groups = {name: name if name in ZONES else "External market" if name != "CORS" else "CORS hub" for name in nodes.index}
    template, plotted = maps.plot_zonal_physical_network(source, groups,
        group_colors={**colors, "External market": "#7b8993", "CORS hub": "#9b73a6"},
        geography=geography, extent=OVERLAY_EXTENT, show_group_labels=False, show_bus_labels=False,
        link_classification={key: "internal" if edge.classification == "ITALIAN_INTERZONAL" else "external" for key, edge in links.iterrows()})
    try:
        if len(plotted) != 20 or set(plotted.branch_id) != set(links.index) or not plotted.component.eq("links").all():
            raise ValueError("Geographic context does not match canonical MEM interface registry")
        _vector_base(template.axes[0], ax)
        _labels(ax, nodes, colors)
        ax.set_title("MEM geographic market graph\n7 Italian zones · 8 external markets · CORS", fontsize=12, pad=10)
    finally:
        plt.close(template)
    return [Path(geography).resolve(), Path(maps.__file__).resolve(), Path(io.__file__).resolve()]


def _case_tables(case, inp, cfg):
    report = Path(case["report"])
    receipt = inp.read(report / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json")
    metadata = inp.read(report / "VIS_X1/FIGURE_METADATA.json")
    if receipt["status"] != "PASS" or metadata["status"] != "PASS":
        raise ValueError("Geographic preview requires passing saved R1 reports")
    for item in (receipt, metadata):
        if (int(item["year"]), item["scenario"]) != (int(case["year"]), case["scenario"]):
            raise ValueError("Saved geographic case identity mismatch")
    if receipt["physical_source"] != "UC_MILP" or receipt["price_source"] != "FIXED_COMMITMENT_PRICE_LP":
        raise ValueError("Saved physical/price authority mismatch")
    snapshot = pd.Timestamp(metadata["selected_stress_snapshot"])
    if snapshot != pd.Timestamp(receipt["visualization"]["selected_stress_snapshot"]):
        raise ValueError("Saved stress snapshot differs between R1 receipts")
    anchors = inp.read(report / "VIS_X1/data/visualization_only_zone_anchors.csv").set_index("market")
    registry = inp.stat(case, "uc2_net_interface_registry.csv")
    summary = inp.stat(case, "uc2_net_interface_summary.csv").set_index("interface_id")
    annual = inp.stat(case, "uc2_price_annual_summary.csv")
    saved = inp.read(report / "VIS_X1/data/MEM_STAGE_B_GRAPH_INTERFACES.csv")
    corridors = registry.rename(columns={"display_from": "bus0", "display_to": "bus1"})[
        ["interface_id", "bus0", "bus1", "classification"]].copy()
    net = inp.stat(case, "uc2_net_interface_hourly_MW.parquet")
    if net.shape != (8760, 20) or not net.index.is_unique or snapshot not in net.index:
        raise ValueError("Full-year canonical 20-interface flow coverage required")
    corridors["signed_net_p0_MW"] = net.loc[snapshot].reindex(corridors.interface_id).to_numpy()
    corridors["hours_at_or_above_threshold"] = summary.hours_at_or_above_90_percent.reindex(corridors.interface_id).to_numpy()
    wanted = corridors.set_index("interface_id")
    actual = saved.set_index("interface_id")
    if set(actual.index) != set(wanted.index):
        raise ValueError("Saved map interface identity differs from canonical registry")
    pd.testing.assert_frame_equal(actual[wanted.columns].sort_index(), wanted.sort_index(), check_dtype=False, atol=1e-8, rtol=0)
    nodes, links, prices = _stage_b_graph_tables(registry, anchors, corridors, annual, cfg)
    if annual.loc[annual.market.isin(cfg["display_market_aliases"]), "snapshot_count"].ne(8760).any():
        raise ValueError("Annual price markers must use complete saved chronology")
    numeric = corridors[["signed_net_p0_MW", "hours_at_or_above_threshold"]].to_numpy()
    if not np.isfinite(numeric).all() or corridors.hours_at_or_above_threshold.lt(0).any() or corridors.hours_at_or_above_threshold.gt(8760).any():
        raise ValueError("Invalid saved map quantities")
    _saturation_qa(case, inp, summary, net)
    inp.check("saved_geographic_binding", "16 display nodes / 20 canonical interfaces; saved UC flow and >=90% hours reconcile", year=int(case["year"]), scenario=case["scenario"], selected_stress_snapshot=str(snapshot), Italian_nodes=7, external_nodes=8, CORS_nodes=1, reference_AC_branches=0, original_result_status=receipt.get("result_status", "UNKNOWN"))
    return dict(case=case, nodes=nodes, links=links, prices=prices, corridors=corridors,
                anchors=anchors, snapshot=snapshot, receipt=receipt, annual=annual)


def _saturation_qa(case, inp, summary, net):
    """Inspect exact-bound semantics; never redefine the >=90% plotted metric."""
    positive = inp.stat(case, "uc2_net_interface_positive_capacity_hourly_MW.parquet")
    negative = inp.stat(case, "uc2_net_interface_negative_capacity_hourly_MW.parquet")
    for frame in (positive, negative):
        if not frame.index.equals(net.index) or set(frame.columns) != set(net.columns):
            raise ValueError("Directional capacity/flow chronology mismatch")
    sat_pos = positive.gt(0) & (net - positive).abs().le(1e-4)
    sat_neg = negative.gt(0) & (net + negative).abs().le(1e-4)
    if not sat_pos.sum().eq(summary.positive_limit_hours.reindex(net.columns)).all() or not sat_neg.sum().eq(summary.negative_limit_hours.reindex(net.columns)).all() or (sat_pos & sat_neg).any().any():
        raise ValueError("Exact directional-limit counts do not reconcile")
    saturation = sat_pos | sat_neg
    above90 = net.clip(lower=0).div(positive.where(positive.gt(0))).ge(.9) | (-net).clip(lower=0).div(negative.where(negative.gt(0))).ge(.9)
    if not above90.sum().eq(summary.hours_at_or_above_90_percent.reindex(net.columns)).all():
        raise ValueError("Saved >=90% utilization hours do not reconcile")
    spreads = inp.stat(case, "uc2_net_interface_price_spread_hourly_EUR_per_MWh.parquet")
    coupled = []
    for interface in summary.index[summary.classification.eq("ITALIAN_INTERZONAL")]:
        spread = spreads[interface].reindex(net.index)
        coupled.append(dict(interface_id=interface, exact_saturation_hours=int(saturation[interface].sum()), coupled_at_capacity_hours=int((saturation[interface] & spread.abs().le(1e-6)).sum())))
    cors_error = float((net["CNORD__CORS"] + net["SARD__CORS"]).abs().max())
    if cors_error > 1e-4 or not saturation["CNORD__CORS"].equals(saturation["SARD__CORS"]):
        raise ValueError("CORS serial-leg zero-injection reconciliation failed")
    inp.check("saturation_interpretation", "Exact bounds use 1e-4 MW; >=90% utilization remains separate. Coupled-at-capacity is an exploratory cross-authority diagnostic, not a new canonical category or physical congestion proof.", year=int(case["year"]), scenario=case["scenario"], price_coupling_tolerance_EUR_per_MWh=1e-6, internal_interfaces=coupled, CORS_serial_leg_max_residual_MW=cors_error, CORS_unique_path_saturation_hours=int(saturation["CNORD__CORS"].sum()), CORS_has_market_price=False, do_not_sum_CORS_leg_hours=True)


def _vector_base(template_ax, target_ax):
    """Reuse toolkit vector geometry in the target axes' lon/lat data frame."""
    for artist in template_ax.collections:
        common = dict(alpha=artist.get_alpha(), zorder=artist.get_zorder(),
                      linewidths=artist.get_linewidths(), edgecolors=artist.get_edgecolors(),
                      facecolors=artist.get_facecolors())
        if isinstance(artist, LineCollection):
            target_ax.add_collection(LineCollection(artist.get_segments(), **common))
        elif isinstance(artist, PatchCollection):
            target_ax.add_collection(PatchCollection([PathPatch(path) for path in artist.get_paths()], **common))
        elif not isinstance(artist, PathCollection):
            raise ValueError(f"Unsupported geographic vector collection {type(artist).__name__}")
    for line in template_ax.lines:
        target_ax.plot(line.get_xdata(), line.get_ydata(), color=line.get_color(), linestyle=line.get_linestyle(), linewidth=1, alpha=.45, zorder=2)
    target_ax.set_xlim(template_ax.get_xlim())
    target_ax.set_ylim(template_ax.get_ylim())
    target_ax.set_aspect("equal", adjustable="box")
    target_ax.axis("off")


def _labels(ax, nodes, colors, price_values=None, norm=None):
    cmap = plt.get_cmap("YlOrRd")
    offsets = {"CNORD": (8, 9), "CORS": (-8, 1), "SICI": (8, -16), "MT": (7, -12), "NORD": (8, 7), "SUD": (8, 2)}
    for name, node in nodes.iterrows():
        price = price_values is not None and name in ZONES
        color = cmap(norm(price_values.loc[name])) if price else colors.get(name, "#9b73a6" if name == "CORS" else "#7b8993")
        ax.scatter(node.x, node.y, s=80 if price else 48 if name in ZONES else 36,
                   facecolor=color, edgecolor=colors[name] if price else "white", linewidth=1.2, zorder=12)
        text = f"{name}\n€{price_values.loc[name]:.1f}/MWh" if price else "CORS\n(no price)" if name == "CORS" else name
        ax.annotate(text, (node.x, node.y), xytext=offsets.get(name, (6, 6)), textcoords="offset points",
                    ha="right" if name == "CORS" else "left", fontsize=10 if name in ZONES else 9.5,
                    fontweight="bold" if name in ZONES else "normal", zorder=13,
                    bbox=dict(facecolor="white", edgecolor="none", alpha=.82, pad=1.5))


def render(view, cases):
    """Return ``figure,data,sources,qa,caption``; export belongs to the caller.

    ``cases`` are caller-verified descriptors: year, scenario, variant, report.
    All passed cases share one full price or 0..8760 utilization scale. Flow
    retains each saved deterministic stress hour; it is not a common-hour test.
    """
    view = str(view).lower()
    if view not in VIEWS:
        raise ValueError("Geographic view must be price, flow or utilization")
    cases = _ordered(list(cases))
    inp = _Inputs()
    cfg, _, font = _style()
    _, _, _, io, maps, _, _, _ = _toolkit(cfg)
    from adapters.pypsa_it_adapter import RECOVERED_R2B_ZONE_COLORS
    import adapters.pypsa_it_adapter as palette_module
    colors = {cfg["display_market_aliases"][zone]: color for zone, color in RECOVERED_R2B_ZONE_COLORS.items()}
    frames = [_case_tables(case, inp, cfg) for case in cases]
    geographic = maps.bundled_geography_path()
    for path in (CONFIG, geographic, (ROOT / Path(cfg["external_reference"])) / "TRANSFER_MANIFEST.json",
                 Path(maps.__file__), Path(io.__file__), Path(__file__),
                 Path(palette_module.__file__), Path(__file__).with_name("publication_figures.py"),
                 Path(__file__).with_name("uc2_vis_x1.py"), Path(__file__).with_name("vis_x1_reference_42bus.py")):
        if path.resolve() not in inp.sources:
            inp.sources.append(path.resolve())
    nodes, links = frames[0]["nodes"], frames[0]["links"]
    for item in frames[1:]:
        pd.testing.assert_frame_equal(item["nodes"], nodes)
        pd.testing.assert_frame_equal(item["links"][["bus0", "bus1", "classification"]], links[["bus0", "bus1", "classification"]])
    source = io.ResultSource(tables={"buses": nodes.assign(carrier="MEM_market_display"), "links": links,
                                    "lines": pd.DataFrame(columns=["bus0", "bus1"]), "transformers": pd.DataFrame(columns=["bus0", "bus1"])})
    groups = {name: name if name in ZONES else "External market" if name != "CORS" else "CORS hub" for name in nodes.index}
    columns = min(3, len(cases)); rows = math.ceil(len(cases) / columns)
    with plt.rc_context({"font.family": font, "font.size": 10, "svg.fonttype": "none"}):
        template, plotted = maps.plot_zonal_physical_network(source, groups, group_colors={**colors, "External market": "#7b8993", "CORS hub": "#9b73a6"}, geography=geographic, extent=OVERLAY_EXTENT, show_group_labels=False, show_bus_labels=False,
                link_classification={key: "internal" if edge.classification == "ITALIAN_INTERZONAL" else "external" for key, edge in links.iterrows()})
        if len(plotted) != 20 or set(plotted.branch_id) != set(links.index) or not plotted.component.eq("links").all():
            plt.close(template)
            raise ValueError("Toolkit plotted edge set differs from canonical MEM graph")
        fig, axes = plt.subplots(rows, columns, figsize=(9 * columns, 8.1 * rows), squeeze=False)
        values = pd.concat([item["prices"].load_weighted_mean_EUR_per_MWh for item in frames])
        norm = Normalize(float(values.min()), float(values.max()) if values.max() != values.min() else float(values.max()) + 1) if view == "price" else Normalize(0, 8760)
        data_rows = []
        try:
            for ax, item in zip(axes.flat, frames):
                _vector_base(template.axes[0], ax)
                case = item["case"]; snapshot = item["snapshot"]
                heading = f"{case['year']} {case['scenario']} · {case.get('variant', '')}"
                heading += "\nAnnual load-weighted zonal prices" if view == "price" else f"\nSigned UC interface MW · {snapshot}" if view == "flow" else "\nHorizon hours ≥90% directional capacity"
                ax.set_title(heading, loc="left", fontsize=12, pad=10)
                if view != "price":
                    # Existing overlay helper consumes only saved corridor tables.
                    before_text = len(ax.texts); before_lines = len(ax.lines)
                    if view == "flow":
                        _flow_overlay(SimpleNamespace(axes=[ax]), item["corridors"], item["anchors"],
                                      show_anchors=False, label_min_mw=0, label_offset_points=(0, -9))
                    if view == "utilization":
                        # The helper's utilization colorbar is per-case. Use its
                        # geometry in a temporary panel, then apply one fixed
                        # annual scale to the unchanged saved hour quantities.
                        panel, panel_ax = plt.subplots()
                        _flow_overlay(panel, item["corridors"], item["anchors"], utilization=True, show_anchors=False)
                        for line in panel_ax.lines:
                            ax.plot(line.get_xdata(), line.get_ydata(), color=line.get_color(), linewidth=line.get_linewidth(), alpha=line.get_alpha(), zorder=line.get_zorder())
                        plt.close(panel)
                        overlay = ax.lines[before_lines:]
                        for i, corridor in enumerate(item["corridors"].itertuples(index=False)):
                            overlay[2*i+1].set_color(plt.get_cmap("magma")(norm(corridor.hours_at_or_above_threshold)))
                    for text in ax.texts[before_text:]:
                        text.set_fontsize(8)
                    if view == "flow":
                        # Label offsets only; existing interface paths and arrows
                        # remain unchanged even where long corridors cross.
                        offsets = {"NORD__CNORD": (-35, 10), "NORD__CSUD": (-24, 4), "NORD__SUD": (24, 7),
                                   "CNORD__CSUD": (-21, -16), "CSUD__SICI": (-26, 5),
                                   "SUD__CALA": (26, -5), "CALA__SICI": (23, 3),
                                   "CSUD__SARD": (18, -12)}
                        labelled = item["corridors"].loc[item["corridors"].signed_net_p0_MW.abs().ge(1)]
                        for text, corridor in zip(ax.texts[before_text:], labelled.itertuples(index=False)):
                            text.set_position(offsets.get(corridor.interface_id, (0, -9)))
                    for corridor in item["corridors"].itertuples(index=False):
                        data_rows.append(dict(year=int(case["year"]), scenario=case["scenario"], variant=case.get("variant", ""), view=view, interface_id=corridor.interface_id, from_market=corridor.bus0, to_market=corridor.bus1, classification=corridor.classification,
                            value=float(corridor.signed_net_p0_MW if view == "flow" else corridor.hours_at_or_above_threshold), unit="MW" if view == "flow" else "hours/year", selected_stress_snapshot=str(snapshot) if view == "flow" else None, source_authority="UC_MILP"))
                else:
                    for name, marker in item["prices"].iterrows():
                        data_rows.append(dict(year=int(case["year"]), scenario=case["scenario"], variant=case.get("variant", ""), view=view, market=name, value=float(marker.load_weighted_mean_EUR_per_MWh), unit="EUR/MWh", source_authority="FIXED_COMMITMENT_LP_DUALS", weighting_definition=PRICE_WEIGHT))
                _labels(ax, item["nodes"], colors, item["prices"].load_weighted_mean_EUR_per_MWh if view == "price" else None, norm)
            for ax in list(axes.flat)[len(frames):]:
                ax.axis("off")
        except Exception:
            plt.close(fig)
            raise
        finally:
            plt.close(template)
        title = {"price": "MEM geographic zonal prices", "flow": "MEM geographic signed net interface flows", "utilization": "MEM geographic horizon utilization · hours ≥90%"}[view]
        fig.suptitle(title, x=.03, ha="left", y=.99, fontsize=17, fontweight="bold")
        if view in {"price", "utilization"}:
            cax = fig.add_axes((.943, .19, .012, .65))
            fig.colorbar(ScalarMappable(norm=norm, cmap="YlOrRd" if view == "price" else "magma"), cax=cax, label="Annual LP zonal price [EUR/MWh]" if view == "price" else "Hours/year ≥90% directional capacity")
        handles = [Line2D([], [], color="#a9255e" if view == "flow" else "#295f80", lw=2, ls="-" if view == "flow" else "--", label="Italian interface"), Line2D([], [], color="#d17a25" if view == "flow" else "#ad6036", lw=2, ls="-" if view == "flow" else "--", label="External / CORS interface")]
        if view == "utilization":
            handles = [Line2D([], [], color="#7b8993", marker="o", ls="", label="External market"), Line2D([], [], color="#9b73a6", marker="o", ls="", label="CORS hub · no price")]
        fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(.46, .055), ncol=2, frameon=False, fontsize=10)
        fig.text(.03, .025, "Display anchors and country borders are geographic context · 7 Italian zones + 8 external markets + CORS · 20 interfaces · no AC branches", fontsize=9, color="#586574")
        fig.subplots_adjust(left=.015, right=.918 if view != "flow" else .985, top=.875, bottom=.10, wspace=.06, hspace=.17)
    caption = ("Annual canonical fixed-commitment LP zonal prices use UC rigid load plus P2X withdrawal and a common full-range color scale across the displayed cases. " if view == "price" else "Arrows preserve canonical signed UC interface MW at each case's existing deterministic interface-stress hour; selected hours differ and are not a matched-hour comparison. " if view == "flow" else "Colors show saved full-horizon hours at or above 90% of the applicable directional capacity, on a common 0–8760 scale; these are distinct from exact directional-limit hours. ")
    if view == "flow":
        caption += "Arrow widths follow the existing overlay's scale relative to each case's largest displayed flow; labels give absolute MW. "
    caption += "Coordinates are existing market-display anchors, with Natural Earth borders as context; lines are not surveyed cable routes or AC branches. CORS is a regulated zero-injection serial hub without a market price; its two legs share the same saturation hours. Model saturation alone is not proof of physical AC congestion or economic scarcity."
    qa = dict(status="PASS_NUMERICAL_SOURCE_BINDING", view=view, spatial_scaffold=SCAFFOLD, reference_AC_branches=0,
              plotted_data_rows=len(data_rows), selected_stress_snapshots=[str(item["snapshot"]) for item in frames] if view == "flow" else [],
              common_color_scale={"minimum": norm.vmin, "maximum": norm.vmax} if view != "flow" else None,
              physical_authority="UC_MILP", price_authority="FIXED_COMMITMENT_LP_DUALS", font_resolved=font,
              canonical_price_weight=PRICE_WEIGHT, solver_invocations=0, network_opens=0, checks=inp.checks,
              source_hashes={str(path): _sha(path) for path in inp.sources}, rendered_visual_QA="CALLER_REQUIRED_ON_SAVED_PNG_AND_SVG")
    return dict(figure=fig, data=pd.DataFrame(data_rows), sources=inp.sources, qa=qa, caption=caption)

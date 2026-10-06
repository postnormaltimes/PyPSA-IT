"""Map-only geographic presentation of the accepted VIS-X1 diagnostic case.

The seven Italian points are medoids of the sealed PyPSA-IT R2B physical buses.
All display coordinates live in this visualization layer; the saved PyPSA
network and the existing engineering schematic are never changed.
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
from matplotlib.lines import Line2D

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mem_model.visualization.vis_x1 import (  # noqa: E402
    CONFIG, FIGURE_STATUS, OUTPUT, ROOT, WARNING, ZONE_ORDER,
    _relative, _save_figure, _sha, _toolkit,
)


PARENT_BUS_TABLE = "provenance/recovered_original/candidate_35_50_geo_repaired_bus_table.csv"
PARENT_NUTS2 = ROOT / "optional/geography/NUTS_RG_01M_2021_4326_LEVL_2.geojson"
EXTERNAL_COUNTRIES = {
    "FR": "France", "CH": "Switzerland", "AT": "Austria",
    "SI": "Slovenia", "ME": "Montenegro", "GR": "Greece",
    "TN": "Tunisia", "MT": "Malta",
}
EXTENT = (1.0, 25.0, 31.5, 50.0)


def _check(condition: bool, label: str, rows: list[dict], detail: str = "") -> None:
    rows.append({"check": label, "status": "PASS" if condition else "FAIL", "detail": detail})
    if not condition:
        raise ValueError(f"Geographic map QA failed: {label}: {detail}")


def _medoid(group: pd.DataFrame) -> pd.Series:
    """Select a source bus minimizing total great-circle distance in its zone."""
    ordered = group.sort_values("bus_id").reset_index(drop=True)
    lon = np.radians(ordered.x.to_numpy(dtype=float))
    lat = np.radians(ordered.y.to_numpy(dtype=float))
    delta_lat = lat[:, None] - lat[None, :]
    delta_lon = lon[:, None] - lon[None, :]
    a = np.sin(delta_lat / 2) ** 2 + np.cos(lat[:, None]) * np.cos(lat[None, :]) * np.sin(delta_lon / 2) ** 2
    km = 6371.0088 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    return ordered.iloc[int(np.argmin(km.sum(axis=1)))]


def _zone_points(transfer: Path, aliases: dict[str, str], qa: list[dict]) -> pd.DataFrame:
    path = transfer / PARENT_BUS_TABLE
    buses = pd.read_csv(path)
    _check(len(buses) == 42 and buses.bus_id.is_unique, "sealed_42_bus_source", qa)
    _check(set(buses.gme_zone) == set(ZONE_ORDER), "seven_parent_zones", qa)
    _check(buses[["x", "y"]].notna().all().all(), "parent_coordinates_complete", qa)
    rows = []
    for raw_zone in ZONE_ORDER:
        part = buses.loc[buses.gme_zone.eq(raw_zone)]
        chosen = _medoid(part)
        rows.append({
            "zone": aliases[raw_zone], "display_x": float(chosen.x), "display_y": float(chosen.y),
            "method": "geographic_medoid_haversine_source_bus",
            "source_bus_count": len(part), "source_bus_ids": "|".join(sorted(part.bus_id.astype(str))),
            "source_artifact": str(path), "source_hash": _sha(path),
            "representative_bus_id": str(chosen.bus_id), "source_gme_zone": raw_zone,
        })
    result = pd.DataFrame(rows)
    _check(len(result) == 7 and result.zone.is_unique, "exactly_seven_italian_nodes", qa)
    _check(set(result.zone) == {"NORD", "CNORD", "CSUD", "SUD", "CALA", "SICI", "SARD"},
           "accepted_display_zones", qa)
    return result


def _largest_european_part(geometry):
    parts = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
    european = [p for p in parts if -12 <= p.centroid.x <= 30 and 30 <= p.centroid.y <= 55]
    if not european:
        raise ValueError("Country has no part within bounded European/Mediterranean map")
    return max(european, key=lambda p: p.area)


def _external_points(geography: Path, qa: list[dict]) -> pd.DataFrame:
    import geopandas as gpd

    geo = gpd.read_file(geography)
    _check(str(geo.crs) == "EPSG:4326", "packaged_geography_crs", qa)
    rows = []
    for code, country in EXTERNAL_COUNTRIES.items():
        if code == "MT":
            _check(PARENT_NUTS2.is_file(), "parent_malta_geometry_present", qa)
            nuts = gpd.read_file(PARENT_NUTS2)
            feature = nuts.loc[nuts.NUTS_ID.eq("MT00")]
            _check(len(feature) == 1 and feature.iloc[0].CNTR_CODE == "MT", "parent_malta_nuts2_identity", qa)
            geom = feature.geometry.iloc[0]
            source, source_hash, method = str(PARENT_NUTS2), _sha(PARENT_NUTS2), "NUTS2_MT00_representative_point"
        else:
            feature = geo.loc[geo.name.eq(country)]
            _check(len(feature) == 1, f"packaged_country_{code}", qa)
            geom = _largest_european_part(feature.geometry.iloc[0])
            source, source_hash, method = str(geography), _sha(geography), "country_polygon_representative_point"
        point = geom.representative_point()
        rows.append({"market": code, "display_x": point.x, "display_y": point.y,
                     "method": method, "source_artifact": source, "source_hash": source_hash})
    france = geo.loc[geo.name.eq("France")].geometry.iloc[0]
    parts = list(france.geoms) if france.geom_type == "MultiPolygon" else [france]
    corsica = [p for p in parts if 8 <= p.centroid.x <= 10 and 41 <= p.centroid.y <= 44]
    _check(len(corsica) == 1, "packaged_corsica_polygon", qa)
    point = corsica[0].representative_point()
    rows.append({"market": "CORS", "display_x": point.x, "display_y": point.y,
                 "method": "Corsica_polygon_representative_point",
                 "source_artifact": str(geography), "source_hash": _sha(geography)})
    return pd.DataFrame(rows)


def _classification(network, cfg: dict, qa: list[dict]) -> pd.DataFrame:
    canonical_path = ROOT / cfg["case"]["canonical_statistics"] / "network_topology_interfaces.csv"
    canonical = pd.read_csv(canonical_path).set_index("interface_id")
    _check(len(canonical) == 40 and canonical.index.is_unique, "canonical_40_interface_links", qa)
    _check(set(canonical.index) == set(network.links.index[network.links.carrier.isin(
        cfg["map"]["physical_link_carriers"])]), "canonical_interface_identity", qa)
    for link_id, item in canonical.iterrows():
        row = network.links.loc[link_id]
        _check((str(row.bus0), str(row.bus1), str(row.carrier)) ==
               (item.from_bus, item.to_bus, item.carrier) and abs(float(row.p_nom) - float(item.capacity_MW)) < 1e-6,
               f"canonical_endpoint_capacity_{link_id}", qa)
    # The model contracts identify all directional Italian and boundary legs.
    internal_contract = pd.read_csv(ROOT / "pre_pypsa_inputs/MEM_Interzonal_Static_Contract.csv")
    internal_contract = internal_contract.loc[internal_contract.year.eq(cfg["case"]["year"]) &
                                              internal_contract.scenario.eq(cfg["case"]["scenario"])]
    external_contract = pd.read_csv(ROOT / "pre_pypsa_inputs/MEM_External_Interface_Static_Contract.csv")
    _check(len(internal_contract) == 20 and len(external_contract) == 20,
           "directional_static_contract_counts", qa)
    for row in internal_contract.itertuples(index=False):
        matching = canonical.loc[canonical.carrier.eq("internal_transfer") &
                                 canonical.from_bus.eq(row.from_zone) & canonical.to_bus.eq(row.to_zone)]
        _check(len(matching) == 1 and float(matching.iloc[0].capacity_MW) == float(row.capacity_MW),
               f"internal_contract_{row.from_zone}_{row.to_zone}", qa)
    for row in external_contract.itertuples(index=False):
        outside = "CORS" if row.external_market == "CORS" else f"EXT_{row.external_market}"
        bus0, bus1 = ((row.Italian_zone, outside) if row.direction == "EXPORT" else (outside, row.Italian_zone))
        matching = canonical.loc[canonical.from_bus.eq(bus0) & canonical.to_bus.eq(bus1)]
        _check(len(matching) == 1 and float(matching.iloc[0].capacity_MW) == float(row.capacity_MW),
               f"external_contract_{row.external_market}_{row.Italian_zone}_{row.direction}", qa)
    aliases = cfg["display_market_aliases"]
    rows = []
    for link_id, row in network.links.iterrows():
        carrier = str(row.carrier)
        if carrier == "internal_transfer":
            category, include, note = "ITALIAN_INTERZONAL", True, "Accepted directional Italian zone interface; paired on display."
            authority = str(canonical_path)
        elif carrier == "external_trade":
            category, include, note = "EXTERNAL_MARKET_INTERFACE", True, "Accepted price-taking external domain; paired on display."
            authority = "pre_pypsa_inputs/MEM_External_Interface_Static_Contract.csv"
        elif carrier == "corsica_hub":
            category, include, note = "PHYSICAL_OR_HVDC_INTERFACE", True, "Regulated zero-injection/no-price CORS commercial hub leg; display is not a surveyed cable route."
            authority = "pre_pypsa_inputs/MEM_External_Interface_Static_Contract.csv"
        else:
            category, include, note = "AUXILIARY_OR_BOOKKEEPING", False, "BESS/hydro/PHS conversion or state Link; raw engineering schematic only."
            authority = "config/vis_x1.yaml#ontology.link_roles"
        display = lambda bus: aliases.get(str(bus), str(bus)[4:] if str(bus).startswith("EXT_") else str(bus))
        rows.append({"link_id": link_id, "bus0": row.bus0, "bus1": row.bus1,
                     "display_from": display(row.bus0), "display_to": display(row.bus1),
                     "classification": category, "included_main_map": include,
                     "source_authority": authority, "notes": note})
    result = pd.DataFrame(rows)
    _check(result.classification.value_counts().to_dict() == {
        "ITALIAN_INTERZONAL": 20, "EXTERNAL_MARKET_INTERFACE": 16,
        "PHYSICAL_OR_HVDC_INTERFACE": 4, "AUXILIARY_OR_BOOKKEEPING": 82,
    }, "link_class_counts", qa)
    return result


def _corridors(classification: pd.DataFrame, network, snapshot: pd.Timestamp,
               metrics: pd.DataFrame, qa: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    shown = classification.loc[classification.included_main_map].copy()
    order = {name: i for i, name in enumerate(["NORD", "CNORD", "CSUD", "SUD", "CALA", "SICI", "SARD"])}
    def orient(a: str, b: str) -> tuple[str, str]:
        if a in order and b in order:
            return (a, b) if order[a] < order[b] else (b, a)
        return (a, b) if a in order else (b, a)
    shown[["corridor_from", "corridor_to"]] = pd.DataFrame(
        [orient(a, b) for a, b in zip(shown.display_from, shown.display_to)], index=shown.index)
    records, contributions, utilization = [], [], []
    metric_by_id = metrics.set_index("branch_id")
    for (a, b), pair in shown.groupby(["corridor_from", "corridor_to"], sort=False):
        _check(len(pair) == 2, f"directional_pair_{a}_{b}", qa)
        category = str(pair.classification.iloc[0])
        _check(pair.classification.nunique() == 1, f"pair_class_{a}_{b}", qa)
        corridor_id = f"{a}__{b}"
        net = 0.0
        forwards, reverses = [], []
        for row in pair.itertuples(index=False):
            p0 = float(network.links_t.p0.at[snapshot, row.link_id])
            p1 = float(network.links_t.p1.at[snapshot, row.link_id])
            _check(abs(p0 + p1) < 1e-4, f"lossless_sign_{row.link_id}", qa)
            sign = 1 if (row.display_from, row.display_to) == (a, b) else -1
            net += sign * p0
            (forwards if sign == 1 else reverses).append(row.link_id)
            contributions.append({"corridor_id": corridor_id, "link_id": row.link_id,
                                  "bus0": row.bus0, "bus1": row.bus1, "p0_MW": p0,
                                  "p1_MW": p1, "orientation_sign": sign,
                                  "signed_corridor_contribution_MW": sign * p0,
                                  "snapshot": str(snapshot)})
        _check(len(forwards) == 1 and len(reverses) == 1, f"opposite_link_pair_{corridor_id}", qa)
        capacity = float(network.links.at[(forwards if net >= 0 else reverses)[0], "p_nom"])
        directional = metric_by_id.loc[pair.link_id]
        hours = float(directional.hours_at_or_above_threshold.max())
        record = {"corridor_id": corridor_id, "bus0": a, "bus1": b,
                  "classification": category, "source_link_ids": "|".join(sorted(pair.link_id)),
                  "signed_net_p0_MW": net, "display_direction": f"{a} → {b}" if net >= 0 else f"{b} → {a}",
                  "directional_capacity_MW": capacity, "selected_hour_utilization": abs(net) / capacity,
                  "snapshot": str(snapshot)}
        records.append(record)
        utilization.append({"component": "links", "branch_id": corridor_id, "bus0": a, "bus1": b,
                            "threshold": .9, "hours_at_or_above_threshold": hours,
                            "max_utilization": float(directional.max_utilization.max()),
                            "source_link_ids": record["source_link_ids"],
                            "aggregation": "maximum_directional_link_hours_not_union"})
    _check(len(records) == 20, "twenty_display_corridors", qa)
    return pd.DataFrame(records), pd.DataFrame(contributions), pd.DataFrame(utilization)


def _overlay_market_points(fig, points: pd.DataFrame, colors: dict[str, str]) -> None:
    ax = fig.axes[0]
    for row in points.itertuples(index=False):
        market = row.market
        color = colors.get(market, "#7b8993")
        ax.scatter([row.x], [row.y], s=45 if market in colors else 25,
                   color=color, edgecolor="white", linewidth=.65, zorder=8)
        ax.annotate(market, (row.x, row.y), xytext=(5, 5), textcoords="offset points",
                    fontsize=8 if market in colors else 7, weight="bold" if market in colors else "normal",
                    color="#253746", zorder=9)


def _link_only_legend(fig) -> None:
    """Replace the toolkit's generic AC key; this Stage-B case has no AC Lines."""
    fig.axes[0].legend(handles=[Line2D([], [], color="#55616b", linewidth=2,
                                       linestyle="--", label="Controllable Link")],
                       loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False, fontsize=8)


def build_geographic_maps() -> Path:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    _ = _toolkit(cfg)  # Verify sealed toolkit hashes before using its APIs.
    from adapters.pypsa_it_adapter import RECOVERED_R2B_ZONE_COLORS
    from visualization_toolkit import congestion, io, network_maps
    import pypsa

    qa: list[dict] = []
    case = cfg["case"]
    _check(case["result_status"] == FIGURE_STATUS, "baseline_status", qa)
    network_path = ROOT / case["network"]
    acceptance = json.loads((ROOT / case["acceptance"]).read_text(encoding="utf-8"))
    _check(acceptance["status"] == "ACCEPTED_IMMUTABLE" and
           _sha(network_path).lower() == acceptance["solved_network_sha256"].lower(),
           "accepted_solved_network_hash", qa)
    folder = OUTPUT / case["model_version"] / str(case["year"]) / case["scenario"]
    original_metadata_path = folder / "FIGURE_METADATA.json"
    original_metadata = json.loads(original_metadata_path.read_text(encoding="utf-8"))
    _check(original_metadata["qa_status"] == "PASS" and original_metadata["qa_checks"] == 37 and
           original_metadata["figure_count"] == 26, "original_gallery_37_of_37", qa)
    snapshot = pd.Timestamp(original_metadata["selected_snapshot"])
    stress = pd.read_csv(folder / "data/system_stress.csv")
    _check(str(snapshot) == str(pd.Timestamp(stress.loc[stress.stress_rank.eq(1), "snapshot"].iloc[0])),
           "stress_selector_unchanged", qa)
    original_files = [original_metadata_path, folder / "FIGURE_INDEX.csv",
                      folder / "diagnostics/qa_reconciliation.csv",
                      folder / "data/interface_utilization_90pct.csv",
                      folder / "data/system_stress.csv",
                      network_path]
    original_hashes = {str(p): _sha(p) for p in original_files}
    transfer = ROOT / Path(cfg["external_reference"])
    zones = _zone_points(transfer, cfg["display_market_aliases"], qa)
    geo_path = network_maps.bundled_geography_path()
    _check(geo_path.is_file(), "packaged_geography_resolves", qa)
    external = _external_points(geo_path, qa)
    network = pypsa.Network(str(network_path))
    _check(len(network.buses) == 64 and len(network.links) == 122 and len(network.lines) == 0,
           "saved_network_shape", qa)
    _check((network.buses[["x", "y"]].fillna(0) == 0).all().all(), "native_zero_coordinates_unchanged", qa)
    _check(snapshot in network.snapshots, "stress_snapshot_in_saved_network", qa)
    classification = _classification(network, cfg, qa)
    metrics = pd.read_csv(folder / "data/interface_utilization_90pct.csv")
    corridors, contributions, utilization = _corridors(classification, network, snapshot, metrics, qa)

    colors = {cfg["display_market_aliases"][zone]: color for zone, color in RECOVERED_R2B_ZONE_COLORS.items()}
    _check(colors == {"NORD": "#4c78a8", "CNORD": "#54a24b", "CSUD": "#f58518",
                      "SUD": "#e45756", "CALA": "#b279a2", "SICI": "#eeca3b",
                      "SARD": "#72b7b2"}, "parent_zone_palette_stable", qa)
    points = pd.concat([
        zones.rename(columns={"zone": "market"})[["market", "display_x", "display_y"]],
        external[["market", "display_x", "display_y"]],
    ], ignore_index=True).rename(columns={"display_x": "x", "display_y": "y"})
    _check(len(points) == 16 and points.market.is_unique, "seven_zones_eight_external_one_hub", qa)
    buses = points.set_index("market")
    buses["carrier"] = "market_display"
    links = corridors.set_index("corridor_id")[["bus0", "bus1", "directional_capacity_MW"]].rename(
        columns={"directional_capacity_MW": "p_nom"})
    p0 = pd.DataFrame([corridors.set_index("corridor_id").signed_net_p0_MW], index=[snapshot])
    source = io.ResultSource(tables={"buses": buses, "links": links,
                                     "lines": pd.DataFrame(columns=["bus0", "bus1"]),
                                     "transformers": pd.DataFrame(columns=["bus0", "bus1"]),
                                     "links_t.p0": p0})
    output = folder / "network/geographic"
    data = output / "data"
    data.mkdir(parents=True, exist_ok=True)
    zone_path = data / "MEM_GME_ZONE_DISPLAY_COORDINATES.csv"
    ext_path = data / "MEM_EXTERNAL_MARKET_DISPLAY_COORDINATES.csv"
    class_path = data / "MEM_NETWORK_VIS_LINK_CLASSIFICATION.csv"
    corridor_path = data / "MEM_MARKET_NETWORK_CORRIDORS.csv"
    contribution_path = data / "MEM_MARKET_NETWORK_FLOW_LINK_CONTRIBUTIONS.csv"
    util_path = data / "MEM_MARKET_NETWORK_UTILIZATION.csv"
    for frame, path in [(zones, zone_path), (external, ext_path), (classification, class_path),
                        (corridors, corridor_path), (contributions, contribution_path),
                        (utilization, util_path)]:
        frame.to_csv(path, index=False)

    figures = []
    groups = {market: (market if market in colors else "Regulated CORS hub" if market == "CORS"
                       else "External price market") for market in buses.index}
    topology_types = {row.corridor_id: ("internal" if row.classification == "ITALIAN_INTERZONAL" else "external")
                      for row in corridors.itertuples(index=False)}
    fig, topology = network_maps.plot_zonal_physical_network(
        source, bus_grouping=groups,
        group_colors={**colors, "External price market": "#7b8993", "Regulated CORS hub": "#9b73a6"},
        link_classification=topology_types, geography=geo_path, extent=EXTENT,
        show_group_labels=False, title="MEM geographic market network — 7 Italian zones; 0 AC Lines")
    _overlay_market_points(fig, points, colors)
    fig.axes[0].legend(handles=[
        Line2D([], [], color="#295f80", linestyle="--", linewidth=2, label="Italian interzonal Link"),
        Line2D([], [], color="#ad6036", linestyle="--", linewidth=2, label="External / CORS modeled interface"),
    ], loc="lower left", bbox_to_anchor=(1.01, 0), frameon=False, fontsize=8)
    topo_path = data / "MEM_MARKET_NETWORK_GEOGRAPHIC_PLOTTED.csv"
    topology.to_csv(topo_path, index=False)
    _save_figure(fig, output / "MEM_MARKET_NETWORK_GEOGRAPHIC", io,
                 [zone_path, ext_path, class_path, corridor_path, topo_path], figures,
                 family="geographic market network",
                 limitation="Market-zone medoids and country representative points are display scaffolds, not physical substations or surveyed corridors. CORS is a regulated zero-injection hub. 0 AC Lines.", pdf=True)

    fig, flow_metrics = network_maps.plot_flow_map(
        source, snapshot, components=("links",), geography=geo_path, extent=EXTENT,
        title=f"MEM signed net market-corridor flow — {snapshot} (MW)")
    _overlay_market_points(fig, points, colors)
    _link_only_legend(fig)
    for row in corridors.itertuples(index=False):
        if abs(row.signed_net_p0_MW) < 1:
            continue
        a, b = buses.loc[row.bus0], buses.loc[row.bus1]
        fig.axes[0].annotate(f"{abs(row.signed_net_p0_MW):.0f} MW",
                             ((a.x + b.x) / 2, (a.y + b.y) / 2),
                             fontsize=6, color="#182b3b", ha="center", va="center",
                             bbox={"facecolor": "white", "edgecolor": "none", "alpha": .9, "pad": 1.5})
    flow_path = data / f"MEM_MARKET_NETWORK_FLOW_{snapshot.strftime('%Y%m%d_%H%M')}_PLOTTED.csv"
    flow_metrics.to_csv(flow_path, index=False)
    _save_figure(fig, output / f"MEM_MARKET_NETWORK_FLOW_{snapshot.strftime('%Y%m%d_%H%M')}", io,
                 [corridor_path, contribution_path, flow_path], figures,
                 family="geographic selected-hour signed net flow",
                 limitation="Arrows follow saved p0 bus0→bus1; opposite directional Links net to one display corridor. Labels are absolute net MW; simultaneous counterflows, if any, are visible in source contributions. Display capacities use the net direction.", pdf=True)

    util_metrics = utilization.set_index(["component", "branch_id"])
    fig, _ = congestion.plot_congestion_map(
        source, threshold=.9, metrics=util_metrics, geography=geo_path, extent=EXTENT,
        title="MEM geographic market-corridor utilization — ≥90% directional Link rating")
    _overlay_market_points(fig, points, colors)
    _link_only_legend(fig)
    _save_figure(fig, output / "MEM_MARKET_NETWORK_UTILIZATION", io,
                 [util_path, corridor_path, class_path], figures,
                 family="geographic horizon utilization screen",
                 limitation="Each corridor color uses the maximum of its two accepted directional Link hours ≥90%, not a union of hours and not proof of a binding constraint.", pdf=True)

    for path, digest in original_hashes.items():
        _check(_sha(Path(path)) == digest, f"original_unchanged_{Path(path).name}", qa)
    _check(all(f["status"] == FIGURE_STATUS for f in figures), "map_result_authority_labels", qa)
    _check(all(WARNING in (output / Path(f["png"]).name).with_suffix(".svg").read_text(encoding="utf-8")
               for f in figures), "warning_rendered_in_each_svg", qa)
    qa_path = output / "MEM_MARKET_NETWORK_GEOGRAPHIC_QA.csv"
    pd.DataFrame(qa).to_csv(qa_path, index=False)
    pd.DataFrame(figures).to_csv(output / "MEM_MARKET_NETWORK_FIGURE_INDEX.csv", index=False)
    (output / "MEM_MARKET_NETWORK_FIGURE_METADATA.json").write_text(json.dumps({
        "schema_version": "MEM_VIS_X1_GEOGRAPHIC_MAP_ONLY_V1",
        "result_status": FIGURE_STATUS, "warning": WARNING,
        "presentation_role": "GEOGRAPHIC_MARKET_NETWORK_REPRESENTATION",
        "retained_existing_role": "MEM_MODEL_TOPOLOGY_SCHEMATIC",
        "case": case, "selected_snapshot": str(snapshot),
        "italian_zone_count": 7, "external_price_market_count": 8,
        "corsica_hub_count": 1, "directional_links_displayed": 40,
        "display_corridors": 20, "auxiliary_links_hidden": 82,
        "qa_status": "PASS", "qa_checks": len(qa),
        "original_gallery_qa_status": "37/37 PASS, untouched",
        "figure_count": len(figures), "figures": figures,
    }, indent=2), encoding="utf-8")
    return output


if __name__ == "__main__":
    print(build_geographic_maps())

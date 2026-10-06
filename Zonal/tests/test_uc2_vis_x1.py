from types import SimpleNamespace
import pandas as pd
import pytest
import yaml

from mem_model.visualization import uc2_vis_x1 as adapter


def _map_inputs(scenario="Base"):
    report = adapter.ROOT / "results/uc2_full_year/2040" / scenario / "REPORTING"
    cfg = yaml.safe_load(adapter.CONFIG.read_text(encoding="utf-8"))
    return (pd.read_csv(report / "CANONICAL/statistics/uc2_net_interface_registry.csv"),
            pd.read_csv(report / "VIS_X1/data/visualization_only_zone_anchors.csv", index_col="market"),
            pd.read_csv(report / "VIS_X1/data/net_interface_overlay_selected_snapshot.csv", index_col=0),
            pd.read_csv(report / "CANONICAL/statistics/uc2_price_annual_summary.csv"), cfg)


@pytest.mark.parametrize("scenario", ["Slow", "Base", "High"])
def test_stage_b_map_binds_only_canonical_MEM_nodes_interfaces_and_prices(scenario):
    registry, anchors, corridors, prices, cfg = _map_inputs(scenario)
    before = [table.copy(deep=True) for table in (registry, anchors, corridors, prices)]
    nodes, edges, values = adapter._stage_b_graph_tables(registry, anchors, corridors, prices, cfg)
    assert len(nodes) == 16 and nodes.index.is_unique
    assert set(nodes.loc[nodes.role.eq("ITALIAN_MARKET"), "model_bus_id"]) == set(adapter.ZONE_ORDER)
    assert set(nodes.loc[nodes.role.eq("EXTERNAL_MARKET")].index) == {"FR", "CH", "AT", "SI", "ME", "GR", "TN", "MT"}
    assert nodes.at["CORS", "role"] == "CORS_NO_PRICE_HUB"
    assert len(edges) == 20 and set(edges.index) == set(registry.interface_id)
    assert edges.classification.value_counts().to_dict() == {"ITALIAN_INTERZONAL": 10,
        "EXTERNAL_MARKET_INTERFACE": 8, "PHYSICAL_OR_HVDC_INTERFACE": 2}
    assert len(values) == 7 and values.index.is_unique and "CORS" not in values.index
    pd.testing.assert_frame_equal(nodes[["x", "y"]], anchors.rename(columns={"display_x":"x", "display_y":"y"}))
    for original, unchanged in zip((registry, anchors, corridors, prices), before):
        pd.testing.assert_frame_equal(original, unchanged)


def test_stage_b_map_rejects_noncanonical_edge_and_duplicate_price():
    registry, anchors, corridors, prices, cfg = _map_inputs()
    corrupt = corridors.copy()
    corrupt.loc[corrupt.index[0], "bus1"] = "FAKE_PHYSICAL_BUS"
    with pytest.raises(AssertionError):
        adapter._stage_b_graph_tables(registry, anchors, corrupt, prices, cfg)
    duplicated = pd.concat([prices, prices.loc[prices.market.eq("NORD")]], ignore_index=True)
    with pytest.raises(ValueError, match="one canonical LP price"):
        adapter._stage_b_graph_tables(registry, anchors, corridors, duplicated, cfg)


def test_all_zero_scarcity_and_shedding_are_valid_diagnostics():
    toolkit = SimpleNamespace(plot_mix=lambda *args, **kwargs: pytest.fail("Zero values rejected by toolkit"))
    style = SimpleNamespace(color=lambda _: "#4c78a8")
    table = pd.DataFrame({"carrier": ["NORD", "SARD"], "hours": [0., 0.]})
    fig = adapter._plot_mix_including_zero(toolkit, table, value="hours", title="Scarcity", unit="Hours", style=style)
    assert len(fig.axes[0].patches) == 2
    assert all(p.get_width() == 0 for p in fig.axes[0].patches)
    assert "zero" in fig.axes[0].texts[0].get_text()
    adapter.plt.close(fig)


def test_storage_grid_terminals_separate_bess_phs_and_hydro(monkeypatch):
    index = pd.date_range("2019-01-01",periods=2,freq="h")
    stores = pd.DataFrame({"bus":["battery","water_PURE_PHS","water_RESERVOIR"],"carrier":["battery_energy","water_energy","water_energy"]},index=["b","p","h"])
    links = pd.DataFrame({"bus0":["NORD","battery","NORD","water_PURE_PHS","water_RESERVOIR"],"bus1":["battery","NORD","water_PURE_PHS","NORD","NORD"],"carrier":["battery_energy","battery_energy","water_energy","water_energy","water_energy"]},index=["bc","bd","pc","pd","ht"])
    p0 = pd.DataFrame([[2,5,3,4,7],[1,6,2,5,8]],index=index,columns=links.index)
    p1 = -p0 * .8
    energies = pd.DataFrame([[10,20,30],[11,21,31]],index=index,columns=stores.index)
    network = SimpleNamespace(snapshots=index,stores=stores,stores_t=SimpleNamespace(e=energies,p=energies*9999),links=links,links_t=SimpleNamespace(p0=p0,p1=p1))
    inventory = pd.DataFrame({"bus":stores.bus,"market":["NORD"]*3})
    monkeypatch.setattr(adapter,"_physical_source",lambda *args:(None,inventory,None,None))
    cfg={"display_market_aliases":{"NORD":"NORD"},"ontology":{"hydro_source":"pre_pypsa_inputs/MEM_Hydro_Static_Component_Mapping.csv"}}
    before = stores.copy(deep=True)
    observed = adapter.storage_electrical_table(network,cfg,None)
    assert observed[("BESS","charge_mw")].tolist()==[2,1]
    assert observed[("BESS","discharge_mw")].tolist()==pytest.approx([4,4.8])
    assert observed[("PHS","charge_mw")].tolist()==[3,2]
    assert observed[("PHS","discharge_mw")].tolist()==pytest.approx([3.2,4])
    assert observed[("Hydro - reservoir","charge_mw")].eq(0).all()
    assert observed[("Hydro - reservoir","discharge_mw")].tolist()==pytest.approx([5.6,6.4])
    assert observed[("PHS","energy_mwh")].tolist()==[20,21]
    pd.testing.assert_frame_equal(network.stores,before)

"""Synthetic reporting contracts; no optimization is needed or permitted."""
import numpy as np
import pandas as pd
import pypsa
import pytest

from mem_model.reporting.uc2_diagnostics import build_uc2_diagnostics, EXTERNAL_MARKETS

ZONES = ("CALA", "CNOR", "CSUD", "NORD", "SARD", "SICI", "SUD")


def case():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2040-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, "objective"] = [2., 1., 1., 1.]
    for zone in ZONES:
        n.add("Bus", zone)
        n.add("Load", "LOAD_"+zone, bus=zone)
    for external in EXTERNAL_MARKETS:
        n.add("Bus", external)
    n.loads_t.p_set = pd.DataFrame({"LOAD_"+zone: [10., 20., 30., 40.] for zone in ZONES}, index=n.snapshots)
    # Deliberately meaningless UC marginal prices must never be used.
    n.buses_t.marginal_price = pd.DataFrame(-9999., index=n.snapshots, columns=n.buses.index)
    n.add("Link", "forward", bus0="NORD", bus1="CNOR", carrier="internal_transfer", p_nom=10.)
    n.add("Link", "reverse", bus0="CNOR", bus1="NORD", carrier="internal_transfer", p_nom=7.)
    n.add("Link", "export", bus0="NORD", bus1="EXT_FR", carrier="external_trade", p_nom=8.)
    n.add("Link", "import", bus0="EXT_FR", bus1="NORD", carrier="external_trade", p_nom=9.)
    n.add("Bus", "BESS_STATE")
    n.add("Link", "auxiliary", bus0="NORD", bus1="BESS_STATE", carrier="battery_energy", p_nom=100.)
    n.links_t.p0 = pd.DataFrame({"forward": [10., 6., 0., 5.], "reverse": [0., 4., 7., 5.],
                               "export": [8., 4., 0., 1.], "import": [0., 2., 9., 1.],
                               "auxiliary": [99., 99., 99., 99.]}, index=n.snapshots)
    n.links_t.p1 = -n.links_t.p0
    prices = pd.DataFrame({zone: [0., 50., 100., 600.] for zone in ZONES}, index=n.snapshots)
    prices["CNOR"] = [0., 51., 105., 610.]
    for i, external in enumerate(EXTERNAL_MARKETS):
        prices[external] = [0., 50.+i, 100.+i, 600.+i]
    return n, prices


def test_net_counterflow_weighted_energy_and_source_separation():
    n, p = case()
    original_links = n.links_t.p0.copy(deep=True)
    result = build_uc2_diagnostics(n, p)
    np.testing.assert_allclose(result["net_interface_hourly_MW"]["NORD__CNORD"], [10., 2., -7., 0.])
    s = result["net_interface_summary"].set_index("interface_id").loc["NORD__CNORD"]
    assert s.positive_energy_MWh == 22.
    assert s.negative_energy_MWh == 7.
    assert s.net_energy_MWh == 15.
    assert s.positive_limit_hours == 1
    assert s.negative_limit_hours == 1
    assert s.gross_counterflow_hours == 2
    assert s.overlapping_gross_counterflow_MWh == 9.
    assert s.positive_saturation_mean_spread_EUR_per_MWh == 0.
    assert s.negative_saturation_mean_spread_EUR_per_MWh == 5.
    assert set(result["gross_counterflow_QA_only"].figure_status) == {"ENGINEERING / QA ONLY"}
    assert "auxiliary" not in " ".join(result["net_interface_registry"].positive_link_ids)
    assert result["diagnostics_reconciliation"].passed.all()
    pd.testing.assert_frame_equal(original_links, n.links_t.p0)
    assert result["price_annual_summary"].set_index("market").loc["NORD", "arithmetic_mean_EUR_per_MWh"] == 187.5


def test_signed_interface_utilization_and_congestion_use_same_net_flow_once():
    from mem_model.final_network_signed import convert_interfaces
    from mem_model.stage_b_zonal_vre import no_models_or_solves
    n,prices=case()
    original=build_uc2_diagnostics(n,prices)
    with no_models_or_solves():
        input_network=n.copy()
        input_network.links_t.p0=pd.DataFrame(index=n.snapshots)
        input_network.links_t.p1=pd.DataFrame(index=n.snapshots)
        child,audit=convert_interfaces(input_network)
        assert audit.eligible.all()
        child.links_t.p0=pd.DataFrame(0.,index=n.snapshots,columns=child.links.index)
        for name in child.links.index.intersection(n.links.index):
            child.links_t.p0[name]=n.links_t.p0[name]
        child.links_t.p1=-child.links_t.p0
        for row in audit.itertuples(index=False):
            child.links_t.p0[row.signed_link]=n.links_t.p0[row.forward_link]-n.links_t.p0[row.reverse_link]
            child.links_t.p1[row.signed_link]=-child.links_t.p0[row.signed_link]
        result=build_uc2_diagnostics(child,prices)
    for key in ('net_interface_hourly_MW','net_interface_positive_utilization_hourly',
                'net_interface_negative_utilization_hourly','net_interface_positive_capacity_hourly_MW',
                'net_interface_negative_capacity_hourly_MW','net_interface_price_spread_hourly_EUR_per_MWh'):
        pd.testing.assert_frame_equal(original[key].sort_index(axis=1),result[key].sort_index(axis=1))
    assert result['net_interface_summary'].gross_counterflow_hours.eq(0).all()
    assert result['net_interface_registry'].net_definition.str.startswith('NET_FLOW_A_TO_B_MW').all()


def test_price_weights_coupling_temporal_external_and_ties():
    n, p = case()
    r = build_uc2_diagnostics(n, p)
    stats = r["price_annual_summary"].set_index("market").loc["NORD"]
    assert stats.load_weighted_mean_EUR_per_MWh == pytest.approx((50*20+100*30+600*40)/110)
    assert stats.zero_price_hours == 1
    assert stats.above100_price_hours == 1
    assert stats.above500_price_hours == 1
    pair = r["price_pairwise_spreads"].query("market_a == 'CNOR' and market_b == 'NORD'").iloc[0]
    assert pair.exactly_coupled_hours == 1
    assert pair.within_1_EUR_hours == 2
    assert pair.within_5_EUR_hours == 3
    assert pair.within_10_EUR_hours == 4
    assert len(r["price_external_spreads"]) == 7*8
    assert r["price_external_availability"].available.all()
    nearest = r["price_closest_external_hourly"].query("zone == 'NORD'").iloc[0]
    assert nearest.closest_external_market == "EXT_FR"
    assert nearest.tied_closest_market_count == 8
    assert len(r["price_hour_of_day_profile"].query("market == 'NORD'")) == 4
    assert len(r["price_month_hour_matrix"].query("market == 'NORD'")) == 4
    assert r["price_distinct_simultaneous_distribution"].hours.sum() == 4


def test_explicit_missing_prices_and_ambiguous_interface_refused():
    n, p = case()
    with pytest.raises(ValueError, match="chronology"):
        build_uc2_diagnostics(n, p.iloc[:-1])
    with pytest.raises(ValueError, match="Missing"):
        build_uc2_diagnostics(n, p.drop(columns="NORD"))
    p.iloc[0, 0] = np.nan
    with pytest.raises(ValueError, match="nonfinite"):
        build_uc2_diagnostics(n, p)
    n, p = case()
    n.add("Link", "duplicate", bus0="NORD", bus1="CNOR", carrier="internal_transfer", p_nom=10.)
    with pytest.raises(ValueError, match="Ambiguous parallel"):
        build_uc2_diagnostics(n, p)


def test_solver_is_never_called(monkeypatch):
    import linopy
    def forbid(*args, **kwargs):
        raise AssertionError("Reporting invoked a solver")
    monkeypatch.setattr(linopy.Model, "solve", forbid)
    n, prices = case()
    monkeypatch.setattr(type(n.optimize), "__call__", forbid)
    monkeypatch.setattr(type(n.optimize), "solve_model", forbid)
    build_uc2_diagnostics(n, prices)


def test_repeat_outputs_deterministic_and_nonlossless_rejected():
    n, p = case()
    first, second = build_uc2_diagnostics(n, p), build_uc2_diagnostics(n, p)
    for key in first:
        pd.testing.assert_frame_equal(first[key], second[key])
    n.links_t.p1.loc[n.snapshots[0], "forward"] += 1.
    with pytest.raises(ValueError, match="lossless"):
        build_uc2_diagnostics(n, p)


def test_directional_caps_not_hidden_by_zero_division():
    n, p = case()
    n.links.loc["forward", "p_nom"] = 0.
    with pytest.raises(ValueError, match="directional cap"):
        build_uc2_diagnostics(n, p)


def test_cors_preserves_regulated_hub_semantics():
    n, p = case()
    n.add("Bus", "CORS", carrier="corsica_hub")
    for name, bus0, bus1, flow in [("cors_CNOR", "CNOR", "CORS", 3.), ("cors_SARD", "CORS", "SARD", 3.)]:
        n.add("Link", name, bus0=bus0, bus1=bus1, carrier="corsica_hub", p_nom=4.)
        n.links_t.p0[name] = flow
        n.links_t.p1[name] = -flow
    r = build_uc2_diagnostics(n, p)
    checks = r["diagnostics_reconciliation"].set_index("check")
    assert checks.loc["CORS_regulated_zero_injection_hub", "passed"]
    assert "CNORD__CORS" not in r["net_interface_price_spread_hourly_EUR_per_MWh"]
    cors = r["net_interface_summary"].query("carrier == 'corsica_hub'")
    assert cors.price_spread_limitation.eq("CORS has no market price").all()


def test_canonical_price_weight_includes_uc_P2X_and_keeps_rigid_diagnostic():
    n, prices = case()
    n.add("Generator", "P2X_NORD", bus="NORD", carrier="p2x_flexible_test", sign=-1., p_nom=40.)
    n.generators_t.p = pd.DataFrame({"P2X_NORD": [0., 0., 0., 40.]}, index=n.snapshots)
    n.add("GlobalConstraint", "P2X_ANNUAL_NORD", constant=40.)
    result = build_uc2_diagnostics(n, prices)
    row = result["price_annual_summary"].set_index("market").loc["NORD"]
    assert row.load_weighted_mean_EUR_per_MWh == pytest.approx(52000/150)
    assert row.rigid_load_weighted_mean_EUR_per_MWh == pytest.approx(28000/110)
    national = result["price_monthly_national_load_weighted"].iloc[0]
    assert national.rigid_load_plus_P2X_MWh == 810.
    assert national.rigid_load_MWh == 770.
    assert "P2X" in row.weighting_definition

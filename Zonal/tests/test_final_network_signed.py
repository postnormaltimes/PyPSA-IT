"""Transport algebra/serialization/reporting only; mathematical models forbidden."""
import numpy as np
import pandas as pd
import pypsa
import pytest
from copy import deepcopy

from mem_model import final_network_signed as signed
from mem_model.stage_b_zonal_vre import no_models_or_solves


@pytest.fixture(autouse=True)
def no_model_or_solver():
    with no_models_or_solves():
        yield


@pytest.fixture
def network():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2019-01-01", periods=4, freq="h"))
    n.add("Bus", ["CNOR", "CORS", "SARD", "NORD", "EXT_FR", "WATER"], carrier="AC")
    n.add("Link", "A", bus0="CNOR", bus1="CORS", carrier="corsica_hub", p_nom=400)
    n.add("Link", "B", bus0="CORS", bus1="CNOR", carrier="corsica_hub", p_nom=300)
    n.add("Link", "C", bus0="CORS", bus1="SARD", carrier="corsica_hub", p_nom=400)
    n.add("Link", "D", bus0="SARD", bus1="CORS", carrier="corsica_hub", p_nom=400)
    n.add("Link", "E", bus0="EXT_FR", bus1="NORD", carrier="external_trade", p_nom=4500)
    n.add("Link", "F", bus0="NORD", bus1="EXT_FR", carrier="external_trade", p_nom=2200)
    n.add("Link", "TURBINE", bus0="WATER", bus1="NORD", carrier="hydro_reservoir", p_nom=100, efficiency=.9)
    n.add("Generator", "P2X", bus="NORD", carrier="p2x_flexible_NORD", sign=-1, p_nom=5)
    n.add("GlobalConstraint", "P2X_ANNUAL_NORD", type="operational_limit", carrier_attribute="p2x_flexible_NORD", sense="==", constant=10)
    return n


def test_installed_native_negative_bounds_are_verified_without_model():
    api = signed.native_api_receipt()
    assert api["pypsa_version"] == "1.2.3"
    assert api["negative_Link_p_min_pu_supported"]
    assert api["optimization_model_constructed"] is False


def test_static_asymmetric_exact_net_projection_and_protected_components(network):
    child, audit = signed.convert_interfaces(network)
    assert len(audit) == 3 and audit.eligible.all()
    assert len(network.links) == 7 and len(child.links) == 4
    edge = audit.loc[audit.bus_a.eq("CNOR")].iloc[0]
    link = child.links.loc[edge.signed_link]
    assert link.p_nom == 400 and link.p_max_pu == 1 and link.p_min_pu == -.75
    assert "CORS" in child.buses.index
    assert not ((child.links.bus0 == "CNOR") & (child.links.bus1 == "SARD")).any()
    assert child.generators.equals(network.generators)
    assert child.global_constraints.equals(network.global_constraints)
    assert signed.validate_conversion(network, child, audit)["interval_snapshots_checked"] == 12


def test_dynamic_asymmetric_bounds_equal_every_snapshot(network):
    network.links_t.p_max_pu["A"] = [1, .75, .5, .25]
    network.links_t.p_max_pu["B"] = [.2, .4, .6, .8]
    child, audit = signed.convert_interfaces(network)
    row = audit.loc[audit.bus_a.eq("CNOR")].iloc[0]
    np.testing.assert_allclose(child.links_t.p_max_pu[row.signed_link] * child.links.at[row.signed_link, "p_nom"], [400, 300, 200, 100], atol=1e-12)
    np.testing.assert_allclose(child.links_t.p_min_pu[row.signed_link] * child.links.at[row.signed_link, "p_nom"], [-60, -120, -180, -240], atol=1e-12)


@pytest.mark.parametrize("attribute,value", [
    ("p_nom_extendable", True), ("committable", True), ("efficiency", .99),
    ("marginal_cost", 1), ("marginal_cost_quadratic", .01), ("ramp_limit_up", .1),
    ("ramp_limit_down", .1), ("ramp_limit_start_up", .5), ("p_set", 0),
    ("p_init", 10), ("delay", 1), ("active", False), ("capital_cost", 100),
])
def test_ineligible_pair_is_retained_never_silently_consolidated(network, attribute, value):
    network.links.at["A", attribute] = value
    child, audit = signed.convert_interfaces(network)
    row = audit.loc[audit.bus_a.eq("CNOR")].iloc[0]
    assert not row.eligible
    assert "A" in child.links.index and "B" in child.links.index
    assert row.signed_link not in child.links.index


def test_dynamic_cost_or_ramp_is_not_discarded(network):
    network.links_t.marginal_cost["A"] = [0, 0, 1, 0]
    assert not signed.audit_interfaces(network).loc[lambda f: f.bus_a.eq("CNOR"), "eligible"].iloc[0]


def test_unknown_link_semantics_fail_closed(network):
    network.links["custom_constraint_token"] = ""
    assert not signed.audit_interfaces(network).eligible.any()


def test_ambiguous_parallel_or_missing_reverse_not_converted(network):
    network.add("Link", "EXTRA", bus0="CNOR", bus1="CORS", carrier="corsica_hub", p_nom=50)
    assert not signed.audit_interfaces(network).loc[lambda f: f.bus_a.eq("CNOR"), "eligible"].iloc[0]
    network.remove("Link", ["EXTRA", "B"])
    assert not signed.audit_interfaces(network).loc[lambda f: f.bus_a.eq("CNOR"), "eligible"].iloc[0]


def test_physical_net_interval_constructive_reverse_lifting():
    for forward, reverse in ((6100, 5300), (4500, 2200), (100, 50)):
        net = np.linspace(-reverse, forward, 101)
        positive, negative = np.maximum(net, 0), np.maximum(-net, 0)
        assert (positive <= forward).all() and (negative <= reverse).all()
        np.testing.assert_array_equal(positive - negative, net)


def test_exact_scope_validation_rejects_noninterface_change(network):
    child, audit = signed.convert_interfaces(network)
    child.generators.at["P2X", "p_nom"] = 6
    with pytest.raises(AssertionError):
        signed.validate_conversion(network, child, audit)


def test_exact_scope_validation_rejects_directional_drift(network):
    child, audit = signed.convert_interfaces(network)
    child.links.at[audit.iloc[0].signed_link, "p_min_pu"] = -.1
    with pytest.raises(RuntimeError, match="INTERVAL_DRIFT"):
        signed.validate_conversion(network, child, audit)


def test_deterministic_ids_metadata_and_export_reload(network, tmp_path):
    child, audit = signed.convert_interfaces(network)
    other, second = signed.convert_interfaces(network)
    assert child.meta == other.meta and audit.equals(second)
    path = tmp_path / "fixture_UNSOLVED.nc"
    child.export_to_netcdf(path)
    reloaded = pypsa.Network(path)
    assert signed.validate_conversion(network, reloaded, audit)["status"] == "PASS"
    assert reloaded.meta == child.meta


def test_net_flow_legacy_and_signed_are_identical_without_p1_double_count(network):
    child, audit = signed.convert_interfaces(network)
    network.links_t.p0 = pd.DataFrame(0., index=network.snapshots, columns=network.links.index)
    child.links_t.p0 = pd.DataFrame(0., index=child.snapshots, columns=child.links.index)
    for i, row in enumerate(audit.itertuples(index=False), 1):
        network.links_t.p0[row.forward_link] = np.array([10, 20, 30, 40]) * i
        network.links_t.p0[row.reverse_link] = np.array([40, 30, 20, 10]) * i
        child.links_t.p0[row.signed_link] = network.links_t.p0[row.forward_link] - network.links_t.p0[row.reverse_link]
    legacy, native = signed.net_flow_frame(network), signed.net_flow_frame(child)
    np.testing.assert_array_equal(legacy[signed.FLOW_METRIC], native[signed.FLOW_METRIC])
    assert len(native) == 12 and signed.FLOW_METRIC in native


def test_signed_reporting_metadata_endpoints_fail_closed(network):
    child, _ = signed.convert_interfaces(network)
    child.meta["network_v2b_corridors"][0]["bus_a"] = "NORD"
    with pytest.raises(RuntimeError, match="METADATA_ENDPOINT_DRIFT"):
        signed.corridor_table(child)


def test_signed_topology_expansion_validates_actual_bounds_and_collapses_once(network):
    from mem_model.reporting.canonical_results import topology_interfaces
    from mem_model.reporting.topology import validate_model_interface_table, collapse_reciprocal_interfaces
    child, _ = signed.convert_interfaces(network)
    table = topology_interfaces(child)
    assert len(table) == 6 and len(collapse_reciprocal_interfaces(table)) == 3
    validate_model_interface_table(child, table)
    changed = table.copy()
    changed.at[0, "capacity_MW"] += 1
    with pytest.raises(ValueError, match="signed Link bounds"):
        validate_model_interface_table(child, changed)
    changed = table.copy()
    changed.at[0, "to_bus"] = "NORD"
    with pytest.raises(ValueError, match="signed Link direction"):
        validate_model_interface_table(child, changed)


def test_directional_capacity_table_preserves_dynamic_minimum(network):
    network.links_t.p_max_pu["A"] = [1, .5, .75, 1]
    child, _ = signed.convert_interfaces(network)
    table = signed.directional_capacity_table(child)
    row = table.loc[table.from_bus.eq("CNOR") & table.to_bus.eq("CORS")].iloc[0]
    assert row.capacity_MW == 400 and row.minimum_capacity_MW == 200


def test_net_imports_signed_use_common_net_quantity_once(network):
    from mem_model.reporting.canonical_results import hourly_net_imports_by_zone
    child, audit = signed.convert_interfaces(network)
    child.links_t.p0 = pd.DataFrame(0., index=child.snapshots, columns=child.links.index)
    edge = audit.loc[audit.carrier.eq("external_trade")].iloc[0]
    child.links_t.p0[edge.signed_link] = [100, -100, 200, -200]
    np.testing.assert_array_equal(hourly_net_imports_by_zone(child)["NORD"], [100, -100, 200, -200])


def test_phase_promotion_requires_h_pass_without_loading_production_files(monkeypatch):
    monkeypatch.setattr(signed, "read_json", lambda path: {"phases": {"H": {"status": "IN_PROGRESS"}}, "next_phase": "H"})
    with pytest.raises(RuntimeError, match="H_PREDECESSOR_NOT_PASS"):
        signed.promote_phase_n([], test_results={})


@pytest.mark.parametrize("representation", ["LEGACY", "SIGNED", "MIXED"])
def test_live_solver_metadata_is_read_only_and_preserves_corridors(network, monkeypatch, representation):
    network.add("Link", "I", bus0="CNOR", bus1="NORD", carrier="internal_transfer", p_nom=6100)
    network.add("Link", "J", bus0="NORD", bus1="CNOR", carrier="internal_transfer", p_nom=5300)
    network.links_t.p_max_pu["A"] = [1., .75, .5, .25]
    network.links_t.p_max_pu["B"] = [.2, .4, .6, .8]
    network.links_t.p_max_pu["I"] = [1., .5, .75, 1.]
    if representation == "MIXED":
        network.links.at["A", "efficiency"] = .99
    audit = signed.audit_interfaces(network)
    current = network if representation == "LEGACY" else signed.convert_interfaces(network)[0]
    current.links_t.p0 = pd.DataFrame(0., index=current.snapshots, columns=current.links.index)
    current.links_t.p1 = pd.DataFrame(999., index=current.snapshots, columns=current.links.index)
    net = np.array([-30., -10., 10., 30.])
    for row in audit.itertuples(index=False):
        if row.signed_link in current.links.index:
            current.links_t.p0[row.signed_link] = net
        else:
            current.links_t.p0[row.forward_link] = np.maximum(net, 0.)
            current.links_t.p0[row.reverse_link] = np.maximum(-net, 0.)
    expected_corridors = signed.corridor_table(current)
    expected_flows = signed.net_flow_frame(current)
    expected_limits = signed.directional_capacity_table(current)
    # Reproduce the previous exclusion operation before attaching the model.
    legacy = current.copy()
    covered = {item["signed_link"] for item in current.meta.get("network_v2b_corridors", [])}
    if covered:
        legacy.remove("Link", list(covered))
    pd.testing.assert_frame_equal(signed.audit_interfaces(current, excluded_link_ids=covered),
                                  signed.audit_interfaces(legacy))
    assert len(expected_corridors) == 4
    assert expected_corridors.representation.tolist() == (
        ["LEGACY"] * 4 if representation == "LEGACY" else
        ["SIGNED"] * 4 if representation == "SIGNED" else ["LEGACY", "SIGNED", "SIGNED", "SIGNED"])
    for row in audit.itertuples(index=False):
        observed = expected_corridors.loc[expected_corridors.signed_link.eq(row.signed_link)].iloc[0]
        assert (observed.bus_a, observed.bus_b, observed.carrier) == (row.bus_a, row.bus_b, row.carrier)
        assert observed.capacity_a_to_b_MW == row.capacity_a_to_b_MW
        assert observed.capacity_b_to_a_MW == row.capacity_b_to_a_MW
    cnor_cors = expected_limits.loc[expected_limits.from_bus.eq("CNOR") & expected_limits.to_bus.eq("CORS")].iloc[0]
    cors_cnor = expected_limits.loc[expected_limits.from_bus.eq("CORS") & expected_limits.to_bus.eq("CNOR")].iloc[0]
    assert (cnor_cors.capacity_MW, cnor_cors.minimum_capacity_MW) == (400., 100.)
    assert (cors_cnor.capacity_MW, cors_cnor.minimum_capacity_MW) == (240., 60.)
    for _, group in expected_flows.groupby("interface_id"):
        np.testing.assert_array_equal(group[signed.FLOW_METRIC], net)
    before = {c.name: (c.static.copy(deep=True), {k: v.copy(deep=True) for k, v in c.dynamic.items()})
              for c in current.components}
    metadata = deepcopy(current.meta)
    snapshots, weights = current.snapshots.copy(), current.snapshot_weightings.copy(deep=True)
    backend = object()
    class AttachedModel:
        @property
        def solver_model(self):
            return backend
    attached = AttachedModel()
    current._model = attached
    def forbidden(*args, **kwargs):
        raise ValueError("Copying a solved network with an attached solver model is not supported")
    monkeypatch.setattr(current, "copy", forbidden)
    monkeypatch.setattr(current, "remove", lambda *_: pytest.fail("Metadata extraction cannot remove components"))
    pd.testing.assert_frame_equal(signed.corridor_table(current), expected_corridors)
    pd.testing.assert_frame_equal(signed.net_flow_frame(current), expected_flows)
    pd.testing.assert_frame_equal(signed.directional_capacity_table(current), expected_limits)
    assert current.model is attached and attached.solver_model is backend
    assert current.meta == metadata and current.snapshots.equals(snapshots)
    pd.testing.assert_frame_equal(current.snapshot_weightings, weights)
    for c in current.components:
        static, dynamic = before[c.name]
        pd.testing.assert_frame_equal(c.static, static)
        assert set(c.dynamic) == set(dynamic)
        for key, value in c.dynamic.items():
            pd.testing.assert_frame_equal(value, dynamic[key])


def test_audit_optional_exclusion_retains_default_behavior(network):
    expected = signed.audit_interfaces(network)
    pd.testing.assert_frame_equal(signed.audit_interfaces(network, excluded_link_ids=set()), expected)
    pd.testing.assert_frame_equal(signed.audit_interfaces(network, excluded_link_ids={"not_a_link"}), expected)
    filtered = signed.audit_interfaces(network, excluded_link_ids={"A", "B"})
    pd.testing.assert_frame_equal(filtered, expected.loc[expected.bus_a.ne("CNOR")].reset_index(drop=True))
    assert {"A", "B"}.issubset(network.links.index)

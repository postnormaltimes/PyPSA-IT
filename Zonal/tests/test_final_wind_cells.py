"""Native site-LCOE and exact fixed-capacity allocation; no weather or solve."""
import numpy as np
import pandas as pd
import pytest

from mem_model.final_wind_cells import allocate_sites, site_costs, compare_wind_only
from mem_model.stage_b_zonal_vre import no_models_or_solves


@pytest.fixture(autouse=True)
def no_optimizer():
    with no_models_or_solves():
        yield


def sites():
    return pd.DataFrame({"site_id": ["B","A","C"], "annual_CF": [.3,.2,.5],
                         "p_nom_max_MW": [4.,5.,3.]})


def test_onshore_common_cost_is_exact_CF_priority():
    ordered, allocation, _ = allocate_sites(6., sites(), 100., [0.,0.,0.])
    assert ordered.site_id.to_list() == ["A","B","C"]
    np.testing.assert_array_equal(allocation, [0.,3.,3.])
    assert allocation.sum() == 6.


def test_offshore_connection_cost_can_change_ranking():
    _, allocation, _ = allocate_sites(6., sites(), 100., [0.,0.,1000.])
    np.testing.assert_array_equal(allocation, [2.,4.,0.])


def test_connection_cost_remains_aligned_when_sites_are_sorted():
    ordered, _, lcoe = allocate_sites(6., sites(), 100., [200.,0.,0.])
    np.testing.assert_allclose(lcoe, [100/(8760*.2),300/(8760*.3),100/(8760*.5)])
    assert ordered.connection_cost_EUR_per_MW_year.to_list() == [0.,200.,0.]


def test_partial_marginal_cell_is_allowed_not_extra_capacity():
    ordered, allocation, _ = allocate_sites(3.25, sites(), 100., [0.,0.,0.])
    assert allocation.sum() == 3.25
    assert (allocation <= ordered.p_nom_max_MW).all()
    np.testing.assert_array_equal(allocation, [0.,.25,3.])


def test_zero_resource_cannot_receive_capacity():
    table = sites()
    table.loc[table.site_id.eq("C"),"annual_CF"] = 0.
    _, allocation, _ = allocate_sites(6., table, 100., [0.,0.,0.])
    assert allocation[-1] == 0.


def test_capacity_exceeding_physical_potential_fails_closed():
    with pytest.raises(RuntimeError, match="EXCEEDS_PHYSICAL"):
        allocate_sites(13., sites(), 100., [0.,0.,0.])


def test_cell_id_tie_break_is_deterministic():
    table = sites()
    table.annual_CF = .3
    a = allocate_sites(6., table, 100., np.zeros(3))[1]
    b = allocate_sites(6., table.iloc[::-1], 100., np.zeros(3))[1]
    np.testing.assert_array_equal(a,b)


def test_negative_annual_cost_rejected():
    with pytest.raises(ValueError, match="COST_INVALID"):
        allocate_sites(6., sites(), -100., [0.,0.,0.])


def test_pinned_raw_costs_reproduce_native_annual_coefficients_once(monkeypatch):
    # Keep the preparation receipt out of the actual namespace in this unit test.
    monkeypatch.setattr("mem_model.final_wind_cells.dump_json", lambda *args: None)
    result = site_costs()
    assert np.isclose(result["onwind"],118819.88845458832)
    assert np.isclose(result["offwind"],195903.8637674296)
    assert np.isclose(result["offwind-ac-station"],63906.48860026332)
    assert np.isclose(result["offwind-ac-connection-submarine"],375.0342707760387)


@pytest.fixture
def network():
    import pypsa
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2019-01-01",periods=2,freq="h"))
    n.add("Bus","NORD")
    n.add("Generator","wind",bus="NORD",p_nom=100.,carrier="wind_onshore",p_max_pu=[.2,.4])
    n.add("Generator","solar",bus="NORD",p_nom=100.,carrier="solar_pv",p_max_pu=[0.,.3])
    return n


def test_wind_only_comparison_preserves_solar_and_static_fleet(network):
    out = network.copy()
    out.generators_t.p_max_pu["wind"] = [.3,.5]
    compare_wind_only(network,out,pd.Index(["wind"]))


def test_solar_profile_drift_rejected(network):
    out = network.copy()
    out.generators_t.p_max_pu["solar"] = [.1,.5]
    with pytest.raises(AssertionError):
        compare_wind_only(network,out,pd.Index(["wind"]))


def test_capacity_drift_rejected(network):
    out = network.copy()
    out.generators.loc["wind","p_nom"] += 1
    with pytest.raises(AssertionError):
        compare_wind_only(network,out,pd.Index(["wind"]))

"""Economic regression tests on synthetic saved-solution values; never solve."""
import numpy as np
import pandas as pd
import pytest

from mem_model.reporting.marginal_setter_attribution import (
    AttributionError, ZONES, generator_reconstruction, opportunity_reconstruction,
    infer_p2x_shadow, classify_candidates, local_mechanism, coupling_edge_is_open,
    validate_structure, normalize_zone,
    store_energy_stationarity,
    generator_position_admissible,
)


def test_interior_direct_generator():
    price, gradient = generator_reconstruction(np.array([60., 60.]), 0., 0., np.zeros(2), np.zeros(2), np.ones(2))
    np.testing.assert_array_equal(price, [60., 60.])
    assert local_mechanism(True, 0, 0, 0, gradient[0], "DIRECT_MARGINAL_TECHNOLOGY") == "DIRECT_MARGINAL_TECHNOLOGY"


def test_ramp_technology_and_heterogeneous_coupling_presentation():
    from mem_model.visualization.marginal_setter import presentation_categories
    table = pd.DataFrame({
        'primary_attribution_class': ['UC_RAMP_CONSTRAINED', 'MARKET_COUPLING', 'MULTIPLE_CANDIDATES'],
        'setter_technology': ['CCGT', None, None],
    })
    assert presentation_categories(table).tolist() == ['CCGT', 'Market Coupling', 'Multiple']


def test_scarcity_bound_resource_is_not_marginal():
    assert local_mechanism(False, 0, 0, 0, 0, "DIRECT_MARGINAL_TECHNOLOGY") is None
    assert local_mechanism(True, 0, -5, 0, 0, "DIRECT_MARGINAL_TECHNOLOGY") is None


def test_ramp_gradient_includes_next_snapshot():
    price, gradient = generator_reconstruction(np.array([60., 60.]), 0., 0., np.array([0., -5.]), np.zeros(2), np.ones(2))
    np.testing.assert_array_equal(price, [55., 65.])
    assert local_mechanism(True, 0, 0, 0, gradient[0], "DIRECT_MARGINAL_TECHNOLOGY") == "UC_RAMP_CONSTRAINED"


@pytest.mark.parametrize("value,eta,expected", [(90., .9, 100.), (69.2820323027551, .8660254037844386, 80.)])
def test_bess_phs_hydro_opportunity_discharge(value, eta, expected):
    assert opportunity_reconstruction(value, eta, 0, 0, 0, 1, charging=False) == pytest.approx(expected)


def test_bess_charge_efficiency_and_bound_sign():
    assert opportunity_reconstruction(100., .9, 0., 0., -10., 1., charging=True) == 80.


def test_store_intertemporal_energy_stationarity():
    dual = np.array([90., 100.])
    np.testing.assert_array_equal(store_energy_stationarity(dual, np.array([0., 10.]), np.array([-10., 0.]), np.ones(2), True), [0., 0.])
    np.testing.assert_array_equal(store_energy_stationarity(dual, np.array([0., 100.]), np.array([-10., 0.]), np.ones(2), False), [0., 0.])


def test_p2x_shadow_inference_and_full_hourly_bounds():
    # Two clean interior hours imply 105; lower/upper bound hours cannot bias it.
    p = np.array([105., 105., 110., 100.])
    lower = np.array([0., 0., 5., 0.]); upper = np.array([0., 0., 0., -5.])
    shadow = infer_p2x_shadow(p, np.zeros(4), lower, upper, np.ones(4), np.ones(4), np.array([True, True, False, False]), 1e-6)
    assert shadow == 105.
    np.testing.assert_array_equal(shadow + lower + upper, p)


def test_nonidentifiable_p2x_shadow_fails_closed():
    with pytest.raises(AttributionError, match="P2X_SHADOW_NOT_IDENTIFIABLE"):
        infer_p2x_shadow(np.array([10., 20.]), 0., 0., 0., 1., 1., np.array([True, True]), 1e-6)


def test_uncongested_coupling():
    assert coupling_edge_is_open(True, 0., 1e-14)


def test_congestion_breaks_path():
    assert not coupling_edge_is_open(False, 0., 0.)
    assert not coupling_edge_is_open(True, 5., 0.)


def candidate(technology="CCGT", mechanism="DIRECT_MARGINAL_TECHNOLOGY", zone="NORD"):
    return dict(technology=technology, mechanism=mechanism, zone=zone)


def test_same_technology_candidates_collapse():
    assert classify_candidates([candidate(), candidate()])[0] == "DIRECT_MARGINAL_TECHNOLOGY"


def test_different_technologies_or_mechanisms_are_multiple():
    assert classify_candidates([candidate(), candidate("Wind")])[0] == "MULTIPLE_CANDIDATES"
    assert classify_candidates([candidate(), candidate(mechanism="UC_RAMP_CONSTRAINED")])[0] == "MULTIPLE_CANDIDATES"


def test_distinct_remote_roots_remain_transparent():
    klass, groups = classify_candidates([candidate(), candidate(zone="SUD")], remote=True)
    assert klass == "MARKET_COUPLING" and len(groups) == 2
    assert classify_candidates([candidate()], remote=True)[0] == "MARKET_COUPLING"


@pytest.mark.parametrize("power,lower,upper", [(100., 0., 100.), (0., 0., 100.)])
def test_zero_scarcity_bound_marginality_and_degeneracy(power, lower, upper):
    assert generator_position_admissible(power, lower, upper)
    assert local_mechanism(True, 0., 0., 0., 0., "DIRECT_MARGINAL_TECHNOLOGY") == "DIRECT_MARGINAL_TECHNOLOGY"
    assert local_mechanism(True, 0., -2., 0., 0., "DIRECT_MARGINAL_TECHNOLOGY") is None


@pytest.mark.parametrize("power,lower,upper", [(0., 0., 0.), (4., 4., 4.), (105., 0., 100.), (-5., 0., 100.)])
def test_off_dark_fixed_and_infeasible_resources_excluded(power, lower, upper):
    assert not generator_position_admissible(power, lower, upper)


def test_heterogeneous_remote_roots_are_coupling_not_local_competition():
    roots = [candidate("Wind", zone="NORD"), candidate("PHS", "STORAGE_HYDRO_OPPORTUNITY_VALUE", "SUD")]
    assert classify_candidates(roots, remote=True)[0] == "MARKET_COUPLING"
    assert classify_candidates(roots, remote=False)[0] == "MULTIPLE_CANDIDATES"


def test_known_ramp_technology_is_not_relabelled():
    c = candidate("CCGT", "UC_RAMP_CONSTRAINED")
    assert classify_candidates([c])[0] == "UC_RAMP_CONSTRAINED"
    assert c['technology'] == 'CCGT'


def test_local_precedence_multi_root_island_and_unresolved_graph(monkeypatch):
    import mem_model.reporting.marginal_setter_attribution as engine
    # Small saved-solution graph fixture; full-year structural validation is
    # tested separately. Never build an optimization model.
    monkeypatch.setattr(engine, 'validate_structure', lambda table: None)
    times = pd.DatetimeIndex(['2019-01-01'])
    prices = pd.DataFrame(40., index=times, columns=['NORD','CNOR','CSUD','SUD','CALA','SICI','SARD'])
    roots = []
    for bus, technology in [('NORD','Wind'),('CSUD','CCGT')]:
        c = dict(asset_id=bus+'_asset', component='Generator', zone=bus,
                 technology=technology, carrier='test', mechanism='DIRECT_MARGINAL_TECHNOLOGY', position='interior')
        for key, value in dict(p=20., cost=40., opportunity=np.nan, recon=40., residual=0., lower=0.,upper=0.,gradient=0.,
                               operating_lower=0.,operating_upper=50.,native_power=20.,ramp_up_dual=0.,ramp_down_dual=0.,efficiency=1.,
                               objective_weight=1.,fixed_status=1.).items():
            c[key] = np.array([value])
        c['interior']=np.array([True]);c['admissible_position']=np.array([True]);roots.append(c)
    edges = [dict(id=a+'_'+b,a=a,b=b,interior=np.array([True]),dualmax=np.array([0.]),residual=np.array([0.]),
                  scale=np.array([1.]),offset=np.array([0.]),price_separating=np.array([False]))
             for a,b in [('NORD','CSUD'),('CSUD','SUD')]]
    t,audit=engine.attribute(dict(snapshots=times,prices=prices,roots=roots,edges=edges,physical_hours=np.array([1.])),2040,'Synthetic')
    t=t.set_index('zone')
    assert t.loc['NORD','primary_attribution_class']=='DIRECT_MARGINAL_TECHNOLOGY'
    assert t.loc['NORD','setter_technology']=='Wind'
    assert t.loc['SUD','primary_attribution_class']=='MARKET_COUPLING'
    assert t.loc['SUD','root_count']==2 and t.loc['SUD','remote_root_zone_count']==2
    assert t.loc['SUD','remote_root_technology'] is None  # no forced heterogeneous identity
    assert t.loc['CALA','primary_attribution_class']=='OTHER_INDETERMINATE'
    assert len(audit.loc[audit.target_zone.eq('SUD')])==2
    edges[1]['interior']=np.array([False]);edges[1]['price_separating']=np.array([True])
    blocked,_=engine.attribute(dict(snapshots=times,prices=prices,roots=roots,edges=edges,physical_hours=np.array([1.])),2040,'Synthetic')
    assert blocked.set_index('zone').loc['SUD','primary_attribution_class']=='OTHER_INDETERMINATE'


def test_indeterminate_case():
    assert classify_candidates([])[0] == "OTHER_INDETERMINATE"


def test_full_structure_and_alias_normalization():
    times = pd.date_range("2019-01-01", periods=8760, freq="h")
    frame = pd.DataFrame({"snapshot": np.repeat(times, 7), "zone": list(ZONES)*8760,
                          "primary_attribution_class": "OTHER_INDETERMINATE"})
    validate_structure(frame)
    assert normalize_zone("CNOR") == "CNORD"
    with pytest.raises(AttributionError):
        validate_structure(pd.concat([frame.iloc[:-1], frame.iloc[:1]]))


def test_zero_solver_invocation_guard():
    from mem_model.reporting.uc2_postprocess import no_solver_calls
    from linopy import Model
    with no_solver_calls() as guard:
        with pytest.raises(RuntimeError, match="SOLVER_CALL_FORBIDDEN"):
            Model.solve(None)
        assert guard["solver_invocations"] == 1  # attempted call is blocked before backend

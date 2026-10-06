"""Installed-source/structural parity and explicit unresolved gate tests."""
import pandas as pd
import numpy as np
import pypsa
import pytest

from mem_model import final_semantic_parity as parity
from mem_model.stage_b_zonal_vre import no_models_or_solves


@pytest.fixture(autouse=True)
def forbid_model_or_solver():
    with no_models_or_solves():
        yield


def test_matrix_covers_every_required_semantic_family_and_previous_corrections():
    table=parity.matrix()
    parity.validate_matrix(table)
    assert len(table)>=27
    assert not table.classification.eq('UNRESOLVED').any()
    for term in ('VRE','ROR','pure PHS','BESS','transmission','Generator','P2X','UC','fixed price LP','water values','snapshot weights','costs','capacity'):
        assert table.object.eq(term).any()
    fixed=table.loc[table.classification.eq('PREVIOUS_BUG_FIXED')]
    assert {'VRE','ROR','spill','transmission','water values'}.issubset(set(fixed.object))


def test_model_critical_unresolved_blocks_final_freeze():
    table=parity.matrix()
    table.loc[0,'classification']='UNRESOLVED'
    with pytest.raises(RuntimeError,match='UNRESOLVED_BLOCKS_F'):
        parity.validate_matrix(table)


def test_installed_native_source_and_future_price_hook_not_executed():
    receipt=parity.source_receipt()
    assert receipt['pypsa_version']=='1.2.3'
    assert len(receipt['native_sources'])==4
    assert receipt['local_PyPSA_Eur_sources']
    assert receipt['API_checked_without_model_construction']
    assert receipt['future_fixed_price_path_inspected_not_executed']


def test_hidden_capacity_expansion_fails_closed():
    n=pypsa.Network();n.set_snapshots(pd.date_range('2019-01-01',periods=8760,freq='h'))
    n.add('Bus','NORD');n.add('Generator','extra',bus='NORD',p_nom=1,p_nom_extendable=True)
    with pytest.raises(RuntimeError,match='CAPACITY_EXPANSION'):
        parity.audit_network(n,2040,'Base')


def test_weight_drift_fails_before_any_physical_or_price_audit():
    n=pypsa.Network();n.set_snapshots(pd.date_range('2019-01-01',periods=8760,freq='h'))
    n.snapshot_weightings.loc[n.snapshots[0],'stores']=2
    with pytest.raises(RuntimeError,match='SNAPSHOT_WEIGHT'):
        parity.audit_network(n,2040,'Base')


def test_profile_roundoff_is_not_a_repair_and_real_violation_fails():
    values=np.array([0.,.5,1.,np.nextafter(1.,np.inf)])
    before=values.copy()
    assert parity.profile_bounds(values)==1
    np.testing.assert_array_equal(values,before)
    for bad in ([1.000001],[-.001],[np.nan],[np.inf]):
        with pytest.raises(RuntimeError,match='PHYSICAL_BOUNDS'):
            parity.profile_bounds(bad)

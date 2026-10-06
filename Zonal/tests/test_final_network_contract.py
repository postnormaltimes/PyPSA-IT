"""Frozen-source reconciliation: no model construction or optimization."""
import pandas as pd
import pypsa
import pytest

from mem_model import final_network_contract as contract
from mem_model.stage_b_zonal_vre import no_models_or_solves


@pytest.fixture(autouse=True)
def no_models():
    with no_models_or_solves():
        yield


def test_register_all_projects_summaries_and_workbook_native_formulas():
    expected,receipt=contract.recover_authority(persist=False)
    assert len(expected)==240
    assert receipt['checks']['project_rows']==38
    assert receipt['checks']['summary_rows']==40
    assert receipt['checks']['workbook']['project_effect_rows_checked']==49
    assert receipt['checks']['workbook']['directional_values_checked']==240
    assert receipt['checks']['current_static_drift_rows']==0


def test_critical_delay_logic_and_no_2050_capacity_acceleration():
    expected,receipt=contract.recover_authority(persist=False)
    assert receipt['checks']['excluded_project_ids']==['546-P','563-P','732-P']
    assert receipt['checks']['361_N_2036_to_2038_included']
    assert receipt['checks']['Tyrrhenian_East_and_West_included_in_Slow']
    for scenario in ('Slow','Base','High'):
        a=expected.loc[expected.year.eq(2040)&expected.scenario.eq(scenario)].set_index(['from_zone','to_zone']).expected_MW
        b=expected.loc[expected.year.eq(2050)&expected.scenario.eq(scenario)].set_index(['from_zone','to_zone']).expected_MW
        pd.testing.assert_series_equal(a,b)


def test_inconsistent_official_headline_has_explicit_frozen_precedence():
    _,receipt=contract.recover_authority(persist=False)
    caveat=receipt['source_caveat_CALA_to_SICI']
    assert caveat['official_Figure3_headline_MW']==4100
    assert caveat['accepted_register_workbook_master_MW']==4150
    assert caveat['official_displayed_base_MW']+caveat['Bolano_increment_MW']+caveat['Ionian_increment_MW']==4150


def test_timing_mutation_fails_closed():
    register=pd.read_csv(contract.REGISTER)
    register.loc[register['Project ID'].eq('361-N'),'Slow COD / range = original + 2 years']='2042'
    with pytest.raises(contract.NetworkContractError,match='PLUS_TWO'):
        contract.register_authority(register)


def test_summary_delta_mutation_fails_closed():
    register=pd.read_csv(contract.REGISTER)
    index=register.index[register['Project ID'].str.startswith('SUMMARY-')][0]
    register.loc[index,contract.SLOW]+=1
    with pytest.raises(contract.NetworkContractError,match='DELTA_CONFLICT'):
        contract.register_authority(register)


def test_shared_formula_translation_preserves_absolute_references():
    assert contract._translate_shared_formula('D5-SUMIF($C$35:$C$83,A5&"->"&B5,$I$35:$I$83)','C5','C6')=='D6-SUMIF($C$35:$C$83,A6&"->"&B6,$I$35:$I$83)'


def test_missing_signed_corridors_fail_closed():
    n=pypsa.Network(); n.set_snapshots(pd.date_range('2019-01-01',periods=8760,freq='h'))
    with pytest.raises(contract.NetworkContractError,match='CORRIDOR_COUNT'):
        contract.audit_signed_network(n,2040,'Base')

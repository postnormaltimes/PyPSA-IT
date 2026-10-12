"""Non-solving checks for frozen, same-LP marginal regime input bundles."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mem_model.common import sha256_file
from mem_model.reporting import marginal_regime as mr
from mem_model.reporting import marginal_setter_attribution as engine


def write_json(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(mr, 'reporting_config', lambda: {
        'require_full_chronology_hours': 1, 'scenario_order': ['Slow', 'Base', 'High']})
    report = tmp_path/'REPORTING'
    report.mkdir()
    inputs = tmp_path/'roots'
    inputs.mkdir()
    snapshot = pd.Timestamp('2019-01-01')
    table = pd.DataFrame([dict(model_year=2050, scenario='Base', snapshot=snapshot, zone=zone,
        represented_hours=1., zonal_price_EUR_MWh=0., primary_attribution_class='DIRECT_MARGINAL_TECHNOLOGY',
        candidate_count=1, local_candidate_count=1, remote_candidate_count=0, root_count=1,
        root_group_count=1, candidate_technology_count=1, remote_root_zone_count=0)
        for zone in engine.ZONES])
    audit = pd.DataFrame([dict(model_year=2050, scenario='Base', snapshot=snapshot, target_zone=zone,
        component='Generator', asset_id='solar_'+zone, zone='CNOR' if zone == 'CNORD' else zone,
        technology='Solar', carrier='solar_pv_utility', mechanism='DIRECT_MARGINAL_TECHNOLOGY',
        remote=False, coupling_path='[]', position='interior', p=1., cost=0., opportunity=np.nan,
        recon=0., residual=0., lower=0., upper=0., gradient=0., efficiency=1., objective_weight=1.,
        operating_lower=0., operating_upper=10., native_power=1., ramp_up_dual=0.,
        ramp_down_dual=0., fixed_status=1.)
        for zone in engine.ZONES])
    prices = pd.DataFrame(0., index=[snapshot], columns=[z if z != 'CNORD' else 'CNOR' for z in engine.ZONES])
    price_path = report/'fixed_commitment_prices_hourly.csv'
    prices.to_csv(price_path)
    network = tmp_path/'matched_lp.nc'
    network.write_bytes(b'saved matched LP; never opened')
    parent_sha = 'a'*64
    job_id = 'FINAL_V4_UC2_2050_BASE_8760H_FIXED_COMMITMENT_PRICE_LP'
    receipt, verification = tmp_path/'LP_RECEIPT.json', tmp_path/'VERIFY_PRICE.json'
    write_json(receipt, dict(status='PASS', kind='FIXED_COMMITMENT_PRICE_LP', termination_condition='optimal',
        year=2050, scenario='Base', snapshots=1, job_id=job_id, solved_sha256=sha256_file(network),
        fixed_commitment_milp_sha256=parent_sha))
    write_json(verification, dict(status='PASS', year=2050, scenario='Base', job_id=job_id,
        solved_sha256=sha256_file(network), milp_solved_sha256=parent_sha))
    uc_receipt, uc_verification = tmp_path/'UC_RECEIPT.json', tmp_path/'VERIFY_UC.json'
    write_json(uc_receipt, dict(status='PASS', solved_sha256=parent_sha))
    write_json(uc_verification, dict(status='PASS', solved_sha256=parent_sha))
    register = report/'uc2_VIS_RESULT_AUTHORITY_REGISTER.csv'
    pd.DataFrame([
        dict(result_id=job_id, year=2050, scenario='Base', solved_or_unsolved='SOLVED',
             network_path=str(network), network_sha256=sha256_file(network), receipt=str(receipt), manifest=str(verification)),
        dict(result_id='FINAL_V4_UC2_2050_BASE_8760H_UC_MILP', year=2050, scenario='Base', solved_or_unsolved='SOLVED',
             network_path='unused_uc.nc', network_sha256=parent_sha, receipt=str(uc_receipt), manifest=str(uc_verification))
    ]).to_csv(register, index=False)
    paths = mr._input_paths(2050, 'Base', inputs)
    freeze = dict(status='FROZEN_AFTER_BASE_PASS', methodology=engine.RULES,
                  implementation_sha256=sha256_file(Path(engine.__file__)))
    write_json(paths[3], freeze)
    sources = [network, receipt, verification, uc_receipt, uc_verification, register, price_path]
    qa = dict(status='PASS_FROZEN_METHOD_ATTRIBUTION', methodology_frozen=True, solver_invocations=0,
        methodology=engine.RULES, implementation_sha256=freeze['implementation_sha256'],
        frozen_methodology_sha256=sha256_file(paths[3]), source_hashes=mr._pins(sources),
        job_id=job_id, fixed_commitment_milp_sha256=parent_sha)
    def save():
        table.to_parquet(paths[0], index=False)
        audit.to_parquet(paths[1], index=False)
        qa['artifact_hashes'] = {p.name: sha256_file(p) for p in paths[:2]}
        write_json(paths[2], qa)
    save()
    return dict(table=table, audit=audit, qa=qa, paths=paths, report=report, save=save)


def validate(bundle):
    return mr._validate_inputs(2050, 'Base', bundle['paths'], bundle['report'])


def test_validated_bundle_is_same_lp_and_complete_without_network_open(roots, monkeypatch):
    import pypsa
    def forbidden(*args, **kwargs):
        raise AssertionError('Network opening is forbidden in the cached consumer')
    monkeypatch.setattr(pypsa, 'Network', forbidden)
    table, audit, error = validate(roots)
    pd.testing.assert_frame_equal(table, roots['table'])
    pd.testing.assert_frame_equal(audit, roots['audit'])
    assert error == 0.


@pytest.mark.parametrize('mutation', ['job', 'parent', 'network_pin'])
def test_pass_status_cannot_substitute_for_current_lp_identity(roots, mutation):
    if mutation == 'job':
        roots['qa']['job_id'] = 'OLD_LP_WITH_THE_SAME_PRICES'
    elif mutation == 'parent':
        roots['qa']['fixed_commitment_milp_sha256'] = 'b'*64
    else:
        network = next(path for path in roots['qa']['source_hashes'] if path.endswith('.nc'))
        del roots['qa']['source_hashes'][network]
    roots['save']()
    with pytest.raises(RuntimeError, match='CANONICAL_REGIME_LP_IDENTITY_CONFLICT'):
        validate(roots)


@pytest.mark.parametrize('mutation', ['freeze_status', 'freeze_rules', 'engine_hash'])
def test_pass_status_and_rehashed_freeze_do_not_change_frozen_method(roots, mutation):
    path = roots['paths'][3]
    freeze = json.loads(path.read_text())
    if mutation == 'freeze_status':
        freeze['status'] = 'CANDIDATE'
    elif mutation == 'freeze_rules':
        freeze['methodology']['price_tolerance_EUR_MWh'] = 1.
    else:
        freeze['implementation_sha256'] = 'b'*64
        roots['qa']['implementation_sha256'] = 'b'*64
    write_json(path, freeze)
    roots['qa']['frozen_methodology_sha256'] = sha256_file(path)
    roots['save']()
    with pytest.raises(RuntimeError, match='FROZEN_ROOT_METHODOLOGY_CONFLICT'):
        validate(roots)


@pytest.mark.parametrize('mutation', ['missing_roots', 'missing_column', 'missing_native_power',
                                      'empty_identity', 'count', 'class', 'mechanism', 'case',
                                      'orphan', 'nonfinite', 'price', 'path', 'scope', 'bound',
                                      'zero_range', 'opportunity'])
def test_rehashed_pass_bundle_rejects_incomplete_or_malformed_audit(roots, mutation):
    table, audit = roots['table'], roots['audit']
    if mutation == 'missing_roots':
        audit.drop(index=0, inplace=True)
    elif mutation == 'missing_column':
        audit.drop(columns=['mechanism'], inplace=True)
    elif mutation == 'missing_native_power':
        audit.drop(columns=['native_power'], inplace=True)
    elif mutation == 'empty_identity':
        audit.loc[0, 'asset_id'] = ''
    elif mutation == 'count':
        table.loc[0, 'candidate_count'] = 2
    elif mutation == 'class':
        table.loc[0, 'primary_attribution_class'] = 'MARKET_COUPLING'
    elif mutation == 'mechanism':
        audit.loc[0, 'mechanism'] = 'P2X_FLEXIBLE_DEMAND'
    elif mutation == 'case':
        audit.loc[0, 'model_year'] = 2040
    elif mutation == 'orphan':
        audit.loc[0, 'target_zone'] = 'OTHER'
    elif mutation == 'nonfinite':
        audit.loc[0, 'recon'] = np.nan
    elif mutation == 'price':
        audit.loc[0, 'recon'] = 10.
    elif mutation == 'path':
        audit.loc[0, 'coupling_path'] = '{}'
    elif mutation == 'scope':
        audit.loc[0, 'remote'] = True
    elif mutation == 'bound':
        audit.loc[0, 'lower'] = 1.
    elif mutation == 'zero_range':
        audit.loc[0, ['operating_lower', 'operating_upper', 'native_power']] = [1., 1., 1.]
    else:
        audit.loc[0, ['component', 'technology', 'carrier', 'position']] = ['Link', 'BESS', 'battery_energy', 'charging']
        audit.loc[0, 'mechanism'] = 'STORAGE_HYDRO_OPPORTUNITY_VALUE'
    roots['save']()
    with pytest.raises(RuntimeError, match='INCOMPLETE_VALIDATED_ROOT_EVIDENCE'):
        validate(roots)


def test_nonfinite_canonical_price_cannot_pass_reconciliation(roots):
    roots['table'].loc[0, 'zonal_price_EUR_MWh'] = np.nan
    roots['save']()
    with pytest.raises(RuntimeError, match='CANONICAL_REGIME_PRICE_VALUE_CONFLICT'):
        validate(roots)


def test_invalid_explicit_bundle_is_not_promoted_to_canonical_storage(roots):
    roots['table'].loc[0, 'root_count'] = 2
    roots['save']()
    with pytest.raises(RuntimeError, match='INCOMPLETE_VALIDATED_ROOT_EVIDENCE'):
        mr.produce(2050, 'Base', validated_root_input=roots['paths'][0].parent,
                   reporting_output=roots['report'])
    assert not (roots['report']/'CANONICAL').exists()
    assert not (roots['report']/mr.RECEIPT).exists()


def test_missing_default_bundle_waits_without_creating_report_or_discovery(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No root discovery or solve is permitted')
    monkeypatch.setattr(engine, 'run_case', forbidden)
    monkeypatch.setattr(engine, 'build_evidence', forbidden)
    report = tmp_path/'REPORTING'
    result = mr.produce(2050, 'Base', reporting_output=report)
    assert result == dict(status='WAITING_FOR_VALIDATED_ROOT_EVIDENCE', model_year=2050,
                         scenario='Base', solver_invocations=0, network_opens=0)
    assert not report.exists()


def test_missing_explicit_bundle_fails_without_creating_report(tmp_path):
    report = tmp_path/'REPORTING'
    with pytest.raises(RuntimeError, match='REQUIRED_VALIDATED_ROOT_INPUT_MISSING'):
        mr.produce(2050, 'Base', validated_root_input=tmp_path/'missing', reporting_output=report)
    assert not report.exists()


def test_core_report_cache_does_not_freeze_refreshable_collection_indexes(tmp_path, monkeypatch):
    from mem_model.reporting import uc2_postprocess
    report = tmp_path / 'REPORTING'
    report.mkdir()
    core = report / 'fixed_commitment_prices_hourly.csv'
    core.write_text('snapshot,NORD\n2019-01-01,20\n')
    for name in ('OUTPUT_REGISTER.csv', 'OUTPUT_REGISTER.json'):
        (report / name).write_text('old collection index')
    monkeypatch.setattr(mr, '_report_dir', lambda *_: report)
    monkeypatch.setattr(mr, '_code_pins', lambda: {'version': 'unchanged'})
    monkeypatch.setattr(uc2_postprocess, '_protected_sources', lambda *_: {})
    result = {'status': 'PASS'}
    mr.install_report_cache(2050, 'Slow', result)
    payload = json.loads((report / mr.CACHE).read_text())
    assert set(payload['artifact_hashes']) == {str(core)}
    for name in ('OUTPUT_REGISTER.csv', 'OUTPUT_REGISTER.json'):
        (report / name).write_text('refreshed collection index')
    assert mr.cached_report(2050, 'Slow') == result
    core.write_text('changed canonical price')
    with pytest.raises(RuntimeError, match='HASH_CONFLICT'):
        mr.cached_report(2050, 'Slow')

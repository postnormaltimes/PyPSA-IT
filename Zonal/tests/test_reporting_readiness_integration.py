"""Current reporting orchestration with array/file fixtures only."""
import inspect
import json

import pytest

from mem_model import stage_b_uc2 as uc2
from mem_model.stage_b_zonal_vre import no_models_or_solves


@pytest.fixture(autouse=True)
def no_optimization():
    with no_models_or_solves():
        yield


@pytest.mark.parametrize('year,scenario', [(2040, 'Slow'), (2040, 'High'),
                                          (2050, 'Slow'), (2050, 'High')])
def test_report_integrates_current_sources_without_solving(year, scenario, tmp_path, monkeypatch):
    from mem_model.reporting import marginal_regime, inspection, uc2_postprocess
    from mem_model.reporting import output_registry, dispatch_figures, marginal_root_pipeline
    calls = []
    output = tmp_path / str(year) / scenario / 'REPORTING'
    monkeypatch.setattr(uc2, 'case_root', lambda y, s: output.parent)
    monkeypatch.setattr(marginal_regime, 'cached_report', lambda *_: None)
    monkeypatch.setattr(uc2, '_manual_solve', lambda *_: pytest.fail('Reporting cannot launch a solve'))
    monkeypatch.setattr(uc2, '_report_verified', lambda *_: calls.append('core') or {'status': 'PASS'})
    def dispatched(report, y, s):
        assert uc2.ACTIVE_VARIANT.get() == 'final_v4'
        assert (report, y, s) == (output, year, scenario)
        calls.append('dispatch')
        return {'status': 'COMPLETE'}
    monkeypatch.setattr(dispatch_figures, 'produce_dispatch_figures', dispatched)
    monkeypatch.setattr(marginal_root_pipeline, 'generate', lambda *_: calls.append('roots') or
                        {'status': 'WAITING_FOR_SAVED_NATIVE_ROOT_EVIDENCE'})
    monkeypatch.setattr(marginal_regime, 'produce', lambda *_: calls.append('regime') or
                        {'status': 'WAITING_FOR_VALIDATED_ROOT_EVIDENCE'})
    monkeypatch.setattr(marginal_regime, 'install_report_cache', lambda *_: pytest.fail('No validated roots'))
    monkeypatch.setattr(uc2_postprocess, 'update_uc2_comparisons', lambda *_: calls.append('comparisons') or
                        {'status': 'WAITING_FOR_COMPARABLE_REPORTS'})
    monkeypatch.setattr(marginal_regime, 'compare', lambda *_: calls.append('regime_comparison') or
                        {'status': 'WAITING_FOR_COMPARABLE_CASES'})
    def registered(report, y, s, **kwargs):
        assert (report, y, s) == (output, year, scenario)
        assert kwargs['regime']['status'] == 'WAITING_FOR_VALIDATED_ROOT_EVIDENCE'
        assert 'marginal_regime_comparison' in kwargs['comparisons']
        calls.append('register')
        return {'core_reporting_status': 'CORE_REPORTING_PASS',
                'accepted_visualizations_status': 'EVIDENCE_GATED_OUTPUTS_WAITING'}
    monkeypatch.setattr(output_registry, 'build_output_register', registered)
    monkeypatch.setattr(inspection, 'build_inspection', lambda *_: calls.append('inspection') or {'status': 'COMPLETE'})
    with uc2.execution_variant('final_v4'):
        result = uc2.report(year, scenario)
    assert calls == ['core', 'dispatch', 'roots', 'regime', 'comparisons', 'regime_comparison', 'register', 'inspection']
    assert result['status'] == 'PASS'
    assert result['output_registry']['accepted_visualizations_status'] != 'ACCEPTED_VISUALIZATIONS_PASS'
    assert uc2.ACTIVE_VARIANT.get() is None


def test_final_qa_distinguishes_valid_core_from_missing_economic_evidence(tmp_path, monkeypatch):
    from mem_model.reporting import inspection, output_registry
    output = tmp_path / '2050/Slow/REPORTING'
    output.mkdir(parents=True)
    (output / 'MEM_UC2_REPORTING_RECEIPT.json').write_text(json.dumps(
        {'status': 'PASS', 'year': 2050, 'scenario': 'Slow'}))
    monkeypatch.setattr(uc2, 'case_root', lambda *_: output.parent)
    monkeypatch.setattr(uc2, '_verified_previous', lambda *_: {'status': 'PASS'})
    monkeypatch.setattr(uc2, '_manual_solve', lambda *_: pytest.fail('Verification cannot launch a solve'))
    monkeypatch.setattr(inspection, 'validate_inspection', lambda *_: {'status': 'COMPLETE'})
    statuses = {'core_reporting_status': 'CORE_REPORTING_PASS',
        'accepted_visualizations_status': 'EVIDENCE_GATED_OUTPUTS_WAITING',
        'comparison_status': 'WAITING_FOR_COMPARABLE_OUTPUTS',
        'evidence_gated_outputs_status': 'EVIDENCE_GATED_OUTPUTS_WAITING',
        'cross_scenario_comparisons_status': 'CROSS_SCENARIO_COMPARISONS_WAITING'}
    monkeypatch.setattr(output_registry, 'validate_output_register', lambda *_: statuses)
    with uc2.execution_variant('final_v4'):
        result = uc2.final_qa(2050, 'Slow')
    assert result['status'] == 'PASS' and result['automatic_next_scenario'] is False
    assert {k: result[k] for k in statuses} == statuses


def test_new_report_cannot_silently_lose_consolidated_register(tmp_path, monkeypatch):
    output = tmp_path / '2050/High/REPORTING'
    output.mkdir(parents=True)
    (output / 'MEM_UC2_REPORTING_RECEIPT.json').write_text(json.dumps(
        {'status': 'PASS', 'year': 2050, 'scenario': 'High', 'output_register_required': True}))
    monkeypatch.setattr(uc2, 'case_root', lambda *_: output.parent)
    monkeypatch.setattr(uc2, '_verified_previous', lambda *_: {'status': 'PASS'})
    with uc2.execution_variant('final_v4'), pytest.raises(RuntimeError, match='declared output register is missing'):
        uc2.final_qa(2050, 'High')
    assert not (output.parent / 'QA/FINAL_SCENARIO_QA.json').exists()


def test_future_capture_occurs_before_export_and_binds_exported_bytes():
    source = inspect.getsource(uc2._manual_solve)
    assert 'extended=True' in source
    assert source.index('capture_price_evidence(network') < source.index('network.export_to_netcdf')
    assert source.index('network.export_to_netcdf') < source.index('bind_price_evidence(')
    assert 'CAPTURE_FILES' in source and 'UC2_OVERWRITE_REFUSED: existing pricing evidence' in source


@pytest.mark.parametrize('variant,year,expected', [
    ('final_v4', 2040, 'FINAL_METHODOLOGY_V3'), ('final_v4', 2050, 'FINAL_METHODOLOGY_V4'),
    ('final_v3', 2040, 'FINAL_METHODOLOGY_V3'), ('final_v1', 2040, 'FINAL_METHODOLOGY_V1'),
    (None, 2040, None),
])
def test_reported_input_version_preserves_inherited_configuration(variant, year, expected):
    from mem_model.reporting.uc2_postprocess import _final_model_version
    with uc2.execution_variant(variant):
        assert _final_model_version(year) == expected

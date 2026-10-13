"""Inspection coverage, provenance, relative links and non-solving integration."""
import json
from pathlib import Path
import csv

import pandas as pd
from PIL import Image
import pytest

from mem_model.reporting import inspection as ins
from mem_model import stage_b_uc2 as uc2
from mem_model.stage_b_zonal_vre import no_models_or_solves

@pytest.fixture(autouse=True)
def no_optimization():
    with no_models_or_solves(): yield

def write_json(p, value):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value),encoding='utf-8')

@pytest.fixture
def report(tmp_path):
    p=tmp_path/'results/final_methodology_v4/2050/Base/REPORTING';p.mkdir(parents=True)
    q=p.parent/'QA';q.mkdir()
    fields={'reference_verification':'ref','uc_verification':'uc','price_verification':'price'}
    write_json(p/'MEM_UC2_REPORTING_RECEIPT.json',dict(status='PASS',reporting_solver_invocations=0,year=2050,scenario='Base',**fields))
    for name,h in [('REFERENCE','ref'),('UC','uc'),('PRICE','price')]:
        write_json(q/f'VERIFY_{name}.json',dict(status='PASS',year=2050,scenario='Base',solved_sha256=h,load_shedding_MWh=0))
    write_json(p/'economic_comparison.json',dict(continuous_reference_objective_EUR=10,uc_milp_objective_EUR=11,uc_uplift_EUR=1,uc_uplift_percent=10))
    pd.DataFrame([dict(available_MWh=2,dispatched_MWh=1,curtailed_MWh=1)]).to_csv(p/'vre_curtailment.csv',index=False)
    data=p/'VIS_X1/data/dispatch.csv';data.parent.mkdir(parents=True)
    data.write_text('zone,generation_MWh\nNORD,1\n')
    png=p/'VIS_X1/operation/dispatch.png';png.parent.mkdir()
    Image.new('RGB',(32,32),'white').save(png)
    svg=png.with_suffix('.svg');svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    f=dict(figure='dispatch',family='operation',status='CURRENT_ACCEPTED_RESULT',underlying_data=[str(data)],png=str(png),svg=str(svg))
    write_json(p/'VIS_X1/FIGURE_METADATA.json',dict(figure_count=1,figures=[f]))
    pd.DataFrame([f]).to_csv(p/'VIS_X1/FIGURE_INDEX.csv',index=False)
    return p

def pins(p):
    return {q.relative_to(p).as_posix():ins._sha(q) for q in p.rglob('*') if q.is_file()}

def test_build_is_complete_relative_and_preserves_reporting(report):
    before=pins(report);result=ins.build_inspection(report)
    assert result['applicable_figures']==result['generated_applicable_figures']==1
    assert result['visualization_completeness_percent']==100
    assert pins(report)==before
    out=report.parent/'INSPECTION'
    assert 'http://' not in (out/'index.html').read_text()
    assert not list(out.rglob('*.nc'))
    assert ins.validate_inspection(report)['tables']==3
    assert ins.build_inspection(report)['status']=='COMPLETE'
    assert pins(report)==before

@pytest.mark.parametrize('defect',['png','svg','data','registry','orphan','orphan_root','unverified','lineage'])
def test_invalid_sources_fail_before_completion(report,defect):
    if defect in ('png','svg'):next((report/'VIS_X1').rglob('*.'+defect)).unlink()
    elif defect=='data':(report/'VIS_X1/data/dispatch.csv').unlink()
    elif defect=='registry':
        t=pd.read_csv(report/'VIS_X1/FIGURE_INDEX.csv');pd.concat([t,t]).to_csv(report/'VIS_X1/FIGURE_INDEX.csv',index=False)
    elif defect=='orphan':Image.new('RGB',(2,2)).save(report/'VIS_X1/operation/orphan.png')
    elif defect=='orphan_root':Image.new('RGB',(2,2)).save(report/'orphan.png')
    else:
        p=report.parent/'QA/VERIFY_UC.json';v=json.loads(p.read_text());v['status' if defect=='unverified' else 'solved_sha256']='bad';write_json(p,v)
    with pytest.raises((ValueError,FileNotFoundError)):ins.build_inspection(report)
    assert not (report.parent/'INSPECTION/INSPECTION_MANIFEST.json').exists()

@pytest.mark.parametrize('defect',['figure','table','gallery','orphan','orphan_table','summary','source'])
def test_modified_inspection_or_source_is_rejected(report,defect):
    ins.build_inspection(report);out=report.parent/'INSPECTION'
    if defect=='figure':next((out/'figures').rglob('*.png')).write_bytes(b'bad')
    elif defect=='table':next((out/'tables').rglob('*.csv')).write_text('bad')
    elif defect=='gallery':(out/'index.html').write_text('<img src="missing.png">')
    elif defect=='orphan':Image.new('RGB',(2,2)).save(out/'figures/orphan.png')
    elif defect=='orphan_table':(out/'tables/orphan.csv').write_text('extra\n1\n')
    elif defect=='summary':(out/'RESULTS_SUMMARY.csv').write_text('metric,value\nbad,1')
    else:(report/'VIS_X1/data/dispatch.csv').write_text('zone,generation_MWh\nNORD,9')
    with pytest.raises(ValueError):ins.validate_inspection(report)

def test_large_table_references_reporting_without_duplicate(report):
    p=report/'large.csv';p.write_text('x\n'+'1\n'*(1024*1024+10))
    ins.build_inspection(report)
    t=pd.read_csv(report.parent/'INSPECTION/TABLE_INDEX.csv')
    q=t.loc[t.display_name.eq('large')].iloc[0]
    assert q.inspection_path.startswith('../REPORTING/')
    assert ins.validate_inspection(report)['tables']==4

def test_large_vector_links_original_and_removes_only_verified_duplicate(report):
    ins.build_inspection(report)
    source = report/'VIS_X1/operation/dispatch.svg'
    source.write_text('<svg xmlns="http://www.w3.org/2000/svg"><!--'+'x'*(2*1024*1024)+'--></svg>')
    # No previous copy with different bytes may be silently removed.
    with pytest.raises(ValueError,match='unverified inspection duplicate'):ins.build_inspection(report)
    target = report.parent/'INSPECTION/figures/02_generation_vre/VIS_X1_dispatch.svg'
    target.unlink()  # This synthetic, derived fixture is not a reporting source.
    ins.build_inspection(report)
    f=pd.read_csv(report.parent/'INSPECTION/FIGURE_INDEX.csv').iloc[0]
    assert f.svg.startswith('../REPORTING/') and not target.exists()
    assert ins.validate_inspection(report)['applicable_figures']==1

def test_non_applicable_figure_is_explicit(report):
    p=report/'VIS_X1/FIGURE_METADATA.json';m=json.loads(p.read_text());f=dict(figure='optional',family='prices',status='NOT_APPLICABLE',underlying_data=[],limitation='No relevant technology in this case.')
    m['figures'].append(f);m['figure_count']=2;write_json(p,m)
    pd.DataFrame(m['figures']).to_csv(report/'VIS_X1/FIGURE_INDEX.csv',index=False)
    r=ins.build_inspection(report);assert r['non_applicable_figures']==1 and r['applicable_figures']==1

def test_metadata_index_format_disagreement_rejected(report):
    p=report/'VIS_X1/FIGURE_INDEX.csv';t=pd.read_csv(p);t['svg']='';t.to_csv(p,index=False)
    with pytest.raises(ValueError):ins.build_inspection(report)

def test_fixed_commitment_price_tables_are_grouped_with_prices():
    assert ins._category('fixed_commitment_prices_hourly.csv') == '03_prices'
    assert ins._category('commitment_events_by_technology.csv') == '08_commitment'

def test_refresh_removes_only_byte_identical_reporting_table_duplicates(report):
    import shutil
    ins.build_inspection(report);out=report.parent/'INSPECTION'
    duplicate=out/'tables/08_commitment/VIS_X1/data/dispatch.csv';duplicate.parent.mkdir(parents=True)
    shutil.copyfile(report/'VIS_X1/data/dispatch.csv', duplicate)
    ins.build_inspection(report)
    assert not duplicate.exists() and (report/'VIS_X1/data/dispatch.csv').exists()
    (out/'tables/private.csv').write_text('unrelated\n1\n')
    with pytest.raises(ValueError,match='unverified inspection table'):ins.build_inspection(report)
    assert (out/'tables/private.csv').exists()

def test_optional_regime_registry_is_inherited(report):
    d=report/'VIS_X1/marginal_regime';d.mkdir()
    Image.new('RGB',(4,4)).save(d/'regime.png');(d/'regime.svg').write_text('<svg/>')
    write_json(d/'LEXICOGRAPHIC_FIGURE_METADATA.json',[dict(files=[str(d/'regime.png'),str(d/'regime.svg')],underlying_data=[str(report/'VIS_X1/data/dispatch.csv')])])
    assert ins.build_inspection(report)['applicable_figures']==2

def test_report_and_final_qa_use_same_case_inspection(report,monkeypatch):
    from mem_model.reporting import marginal_regime as mr
    from mem_model.reporting import dispatch_figures, output_registry, uc2_postprocess
    monkeypatch.setattr(uc2,'case_root',lambda y,s:report.parent)
    monkeypatch.setattr(uc2,'_report_verified',lambda y,s:dict(status='PASS'))
    monkeypatch.setattr(mr,'cached_report',lambda y,s:None)
    monkeypatch.setattr(mr,'produce',lambda y,s:dict(status='WAITING_FOR_VALIDATED_ROOTS'))
    monkeypatch.setattr(mr,'compare',lambda y:dict(status='WAITING_FOR_COMPARABLE_CASES'))
    monkeypatch.setattr(uc2,'_verified_previous',lambda *a:dict(status='PASS'))
    monkeypatch.setattr(dispatch_figures,'produce_dispatch_figures',lambda *_:dict(status='WAITING_FOR_CASE_BOUND_DISPATCH_SOURCES'))
    monkeypatch.setattr(uc2_postprocess,'update_uc2_comparisons',lambda *_:dict(status='WAITING_FOR_COMPARABLE_REPORTS'))
    statuses = dict(core_reporting_status='CORE_REPORTING_PASS',
        accepted_visualizations_status='EVIDENCE_GATED_OUTPUTS_WAITING',
        comparison_status='WAITING_FOR_COMPARABLE_OUTPUTS',
        evidence_gated_outputs_status='EVIDENCE_GATED_OUTPUTS_WAITING',
        cross_scenario_comparisons_status='CROSS_SCENARIO_COMPARISONS_WAITING')
    monkeypatch.setattr(output_registry,'build_output_register',lambda *a,**k:statuses)
    monkeypatch.setattr(output_registry,'validate_output_register',lambda *_:statuses)
    before={p.name for p in report.parents[4].iterdir()}
    for variant in ('final_v3','final_v4'):
        with uc2.execution_variant(variant):
            assert uc2.report(2050,'Base')['inspection']['status']=='COMPLETE'
            assert uc2.final_qa(2050,'Base')['inspection']['status']=='COMPLETE'
    assert {p.name for p in report.parents[4].iterdir()}==before

@pytest.mark.parametrize('variant,year,expected',[('final_v4',2040,'final_methodology_v3'),('final_v4',2050,'final_methodology_v4')])
def test_inherited_case_routing(variant,year,expected):
    with uc2.execution_variant(variant):assert expected in uc2.case_root(year,'Base').parts


@pytest.mark.parametrize('change', ['comparison', 'core'])
def test_shared_comparison_refresh_does_not_reinterpret_case_gallery(report, monkeypatch, change):
    from mem_model.reporting import output_registry
    statuses = dict(core_reporting_status='CORE_REPORTING_PASS',
        accepted_visualizations_status='EVIDENCE_GATED_OUTPUTS_WAITING',
        comparison_status='PARTIAL_COMPARISON_COLLECTION',
        evidence_gated_outputs_status='EVIDENCE_GATED_OUTPUTS_WAITING',
        cross_scenario_comparisons_status='CROSS_SCENARIO_COMPARISONS_WAITING', rows=[])
    (report / 'OUTPUT_REGISTER.json').write_text('{}')
    monkeypatch.setattr(output_registry, 'validate_output_register', lambda *_: dict(statuses))
    ins.build_inspection(report)
    before = {p: ins._sha(p) for p in (report.parent / 'INSPECTION').rglob('*') if p.is_file()}
    statuses['register_status'] = 'STALE_COMPARISON_COLLECTION_REFRESH_REQUIRED'
    if change == 'core':
        statuses['core_reporting_status'] = 'CORE_REPORTING_OUTPUTS_WAITING'
        with pytest.raises(ValueError, match='output availability changed'):
            ins.validate_inspection(report)
    else:
        statuses['comparison_status'] = 'WAITING_FOR_COMPARISON_REGISTER_REFRESH'
        result = ins.validate_inspection(report)
        assert result['status'] == 'COMPLETE' and result['comparison_refresh_required'] is True
        assert result['output_status']['comparison_status'] == statuses['comparison_status']
    assert {p: ins._sha(p) for p in before} == before

@pytest.mark.parametrize('variant,year',[('final_methodology_v3',2040),('final_methodology_v3',2050),('final_methodology_v4',2050)])
def test_completed_result_gallery(variant,year):
    from mem_model.common import ROOT
    p=ROOT/f'results/{variant}/{year}/Base/REPORTING'
    if not p.exists():pytest.skip('Local production reporting is not bundled with source code')
    r=ins.validate_inspection(p)
    assert r['applicable_figures']==len([f for f in ins.figure_registry(p) if f['applicability']=='APPLICABLE'])
    assert r['visualization_completeness_percent']==100

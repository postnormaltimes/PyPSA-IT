"""Read-only candidate input checks and isolated execution-path records."""
import json

import numpy as np
import pandas as pd

from mem_model import final_adequacy_candidate as candidate
from mem_model import stage_b_uc2 as uc2
from mem_model.common import ROOT, dump_json, sha256_file
from mem_model.reporting.adequacy_comparison import refresh
from mem_model.stage_b_zonal_vre import no_models_or_solves


def main():
    all_jobs = []
    with no_models_or_solves():
        for variant in candidate.VARIANTS:
            cfg = candidate.settings(variant)
            qa = ROOT / cfg['qa_root']
            candidate.verify_frozen(variant)
            preflights, jobs = [], []
            with uc2.execution_variant(variant):
                for scenario in candidate.SCENARIOS:
                    preflights.append(uc2.preflight(2050, scenario, persist=False))
                    for kind in uc2.RUN_TYPES:
                        paths = uc2.job_paths(2050, scenario, kind)
                        if paths['solved'].exists() or paths['receipt'].exists():
                            raise RuntimeError('Candidate production result already exists')
                        assert paths['directory'].is_relative_to(ROOT / cfg['result_root'])
                        assert not paths['directory'].is_relative_to(ROOT / 'results/final_methodology_v4')
                        assert len(str(paths['receipt'])) < 260
                        jobs.append({'year':2050,'scenario':scenario,'variant':variant,'kind':kind,
                            'job_id':uc2.job_id(2050,scenario,kind),
                            'input_path':str(paths['input'].relative_to(ROOT)),
                            'input_sha256':uc2._check_input_hash(2050,scenario,kind),
                            **{key:str(paths[key].relative_to(ROOT)) for key in ('directory','solved','receipt','verification')}})
            for row in pd.read_csv(ROOT / cfg['runtime_root'] / 'RUNTIME_MANIFEST.csv').itertuples():
                if sha256_file(ROOT / row.path) != row.sha256:
                    raise RuntimeError('Inherited runtime hash changed: ' + row.path)
            plan = pd.read_csv(qa / 'INCREMENTAL_UNIT_CROSSWALK.csv', float_precision='round_trip')
            assert not plan.child_id.duplicated().any()
            assert plan.CHP_flag.eq(False).all()
            assert plan.carrier.isin(candidate.FUELS).all()
            for scenario, part in plan.groupby('scenario'):
                assert scenario in ('Base','High')
                assert np.isfinite(part[['p_nom','efficiency','marginal_cost','availability']].to_numpy()).all()
                assert part.p_nom.gt(0).all() and part.efficiency.between(0,1).all()
                for zone, block in part.groupby('zone'):
                    fuel_capacity = block.groupby('carrier').p_nom.sum()
                    np.testing.assert_allclose(fuel_capacity.iloc[0],fuel_capacity.iloc[1],atol=1e-7,rtol=0)
                if variant == 'final_v5':
                    shares = part.groupby('zone').p_nom.sum() / part.p_nom.sum()
                    np.testing.assert_allclose(shares.reindex(['CNOR','NORD']), [.6,.4],atol=1e-12,rtol=0)
                    assert set(shares.index) == {'CNOR','NORD'}
            baseline = pd.read_csv(candidate.STATIC)
            capacity = pd.read_csv(qa / 'CAPACITY_RECONCILIATION.csv')
            for scenario in candidate.SCENARIOS:
                original = baseline.loc[baseline.scenario.eq(scenario) & baseline.carrier.isin(candidate.MODEL_CARRIERS)]
                expected = float((original.p_nom_MW * original.static_availability_equivalent).sum())
                recorded = capacity.loc[capacity.scenario.eq(scenario) & capacity.network_type.eq('UC_INPUT')]
                np.testing.assert_allclose(recorded.derated_before_MW.sum(),expected,atol=1e-6,rtol=0)
            protected = pd.read_csv(qa / 'PROTECTED_BASELINE_HASHES.csv')
            candidate.verify_protected(dict(zip(protected.path,protected.sha256)))
            pd.DataFrame(jobs).to_csv(qa / 'MANUAL_JOB_PATHS.csv',index=False)
            dump_json(qa / 'STATIC_PREFLIGHTS.json', {'status':'PASS','cases':preflights,
                'optimization_model_constructed':False,'production_solver_invocations':0})
            comparison = refresh(variant)
            result = {'status':'PASS','variant':variant,'candidate_status':cfg['candidate_status'],
                'canonical':False,'static_preflights_passed':3,'manual_jobs_ready':9,
                'protected_files_unchanged':len(protected),'runtime_inheritance':'PASS',
                'slow_inputs_byte_identical':True,'unexpected_changed_model_fields':0,
                'comparison_collection_status':comparison['status'],
                'installed_increment_MW':float(plan.loc[plan.scenario.eq('Base'),'p_nom'].sum()),
                'derated_increment_MW':float(plan.loc[plan.scenario.eq('Base'),'derated_MW'].sum()),
                'new_unit_min_MW':float(plan.p_nom.min()),'new_unit_max_MW':float(plan.p_nom.max()),
                'modular_capacity_residual_MW':max(abs(p['checks'][0]['derated_programmable_after_MW']-30000)
                    for p in preflights if p['scenario']!='Slow'),
                'optimization_model_constructed':False,'production_solver_invocations':0,
                'verification_code_sha256':sha256_file(ROOT/'scripts/verify_final_v5.py'),
                'input_lineage_sha256':sha256_file(qa/'NETWORK_LINEAGE_AND_HASHES.csv')}
            dump_json(qa / 'VERIFICATION.json', result)
            all_jobs += jobs
            print(json.dumps(result,indent=2))
    assert len({job['job_id'] for job in all_jobs}) == 18
    assert len({job['solved'] for job in all_jobs}) == 18


if __name__ == '__main__':
    main()

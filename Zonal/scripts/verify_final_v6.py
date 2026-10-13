"""Static candidate preflights, job isolation and parent preservation; no solve."""
import json
from importlib.metadata import version

import pandas as pd

from mem_model import final_external_price_candidate as candidate
from mem_model import stage_b_uc2 as uc2
from mem_model.common import ROOT, dump_json, sha256_file
from mem_model.stage_b_zonal_vre import no_models_or_solves


def main():
    cfg = candidate.settings()
    qa = ROOT / cfg['qa_root']
    jobs, preflights = [], []
    packages = {name: version(name) for name in ('pypsa', 'linopy', 'gurobipy')}
    if packages['gurobipy'] != cfg['solver']['required_version']:
        raise RuntimeError('ETX_INSTALLED_SOLVER_VERSION_MISMATCH')
    with no_models_or_solves(), uc2.execution_variant('final_v6'):
        for scenario in ('High', 'Base', 'Slow'):
            preflights.append(uc2.preflight(2050, scenario, persist=False))
            for kind in uc2.RUN_TYPES:
                paths = uc2.job_paths(2050, scenario, kind)
                if paths['directory'].exists() and any(paths['directory'].iterdir()):
                    raise RuntimeError('ETX_PRODUCTION_DIRECTORY_ALREADY_POPULATED')
                if not paths['directory'].is_relative_to(ROOT / cfg['result_root'] / '2050' / scenario):
                    raise RuntimeError('ETX_RESULT_PATH_FAIL')
                jobs.append(dict(year=2050, scenario=scenario, variant='final_v6', kind=kind,
                    job_id=uc2.job_id(2050, scenario, kind), input_sha256=uc2._check_input_hash(2050, scenario, kind),
                    **{key: paths[key].relative_to(ROOT).as_posix() for key in
                       ('input', 'directory', 'solved', 'receipt', 'verification')}))
        for key in ('job_id', 'solved', 'receipt', 'verification'):
            assert len({j[key] for j in jobs}) == 9, key
        inherited = pd.read_csv(ROOT / cfg['runtime_root'] / 'RUNTIME_MANIFEST.csv')
        for row in inherited.itertuples():
            assert sha256_file(ROOT / row.path) == row.sha256, row.path
        protected = pd.read_csv(qa / 'PROTECTED_PARENT_HASHES.csv')
        candidate.verify_protected(dict(zip(protected.path, protected.sha256)))
        pd.DataFrame(jobs).to_csv(qa / 'MANUAL_JOB_PATHS.csv', index=False)
        dump_json(qa / 'STATIC_PREFLIGHTS.json', dict(status='PASS', cases=preflights,
            optimization_model_constructed=False, production_solver_invocations=0))
        result = dict(status='PASS', variant='final_v6', canonical=False, static_preflights_passed=3,
            solver_jobs_prepared=9, protected_files_unchanged=len(protected), runtime_manifest='PASS',
            prepared_networks=6, unexpected_changed_model_fields=0, model_construction=False,
            production_solver_invocations=0, production_results_created=0,
            execution_solver_contract=uc2.config()['solver'], installed_packages=packages,
            High_first_manual_review=True)
        dump_json(qa / 'VERIFICATION.json', result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

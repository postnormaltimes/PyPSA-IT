"""Check all six prepared final cases without constructing a model or solving."""
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

def validate():
    import gurobipy
    from mem_model import stage_b_uc2 as uc2
    from mem_model.stage_b_zonal_vre import no_models_or_solves
    def forbidden(*args,**kwargs):
        raise RuntimeError('Static input validation prohibits solver/licence calls')
    cases=[]
    with ExitStack() as guards:
        guards.enter_context(patch.object(gurobipy,'Env',forbidden))
        guards.enter_context(patch.object(gurobipy,'Model',forbidden))
        guards.enter_context(no_models_or_solves())
        for year,variant in ((2040,'final_v3'),(2050,'final_v6')):
            for scenario in ('Slow','Base','High'):
                with uc2.execution_variant(variant):receipt=uc2.preflight(year,scenario,persist=False)
                if receipt['status']!='PASS' or not receipt['manual_authorized'] or not receipt['prepared_for_manual_execution']:
                    raise RuntimeError('Prepared case failed its execution contract')
                cases.append(receipt)
    return dict(status='PASS',cases=cases,optimization_model_constructed=False,
                solver_invocations=0,licence_check='NOT_REQUIRED_FOR_STATIC_VALIDATION')

if __name__=='__main__':print(json.dumps(validate(),indent=2))

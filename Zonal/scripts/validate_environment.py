"""Validate pinned package versions and optional user-specific Gurobi licence.

Never prints licence contents, environment credentials or solver parameters.
No optimization is performed.
"""
import argparse, importlib.metadata as md, json, platform
from pathlib import Path
def inspect(check_license=False):
    required={'pypsa':'1.2.3','linopy':'0.7.0','gurobipy':'13.0.3','atlite':'0.6.1','pandas':'2.3.3','numpy':'2.4.6'}
    versions={p:md.version(p) for p in required}
    result={'python':platform.python_version(),'packages':versions,'environment_status':'PASS' if platform.python_version()=='3.11.15' and versions==required else 'FAIL','optimization_performed':False,'license_status':'NOT_CHECKED'}
    if check_license:
        try:
            import gurobipy as gp
            with gp.Env(empty=True) as env:
                env.setParam('OutputFlag',0); env.start()
            result['license_status']='AVAILABLE'
        except Exception as ex:
            result['license_status']='EXTERNAL_LICENSE_BLOCKER'
            # Gurobi error category only: no licence identity, credential or paths.
            result['license_error_code']=getattr(ex,'errno',None)
            result['license_error_type']=type(ex).__name__
    return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--check-license',action='store_true');p.add_argument('--output',type=Path)
    a=p.parse_args(); r=inspect(a.check_license)
    if a.output:a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps(r))
    if r['environment_status']!='PASS':raise SystemExit(1)

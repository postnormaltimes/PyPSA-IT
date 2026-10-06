"""Persist completed N/C artifacts without repeating their transformations."""
import json
import pandas as pd

from .common import ROOT, dump_json, sha256_file
from .final_methodology_closure import STATE, record_phase


def close_network_phase(phase):
    state=json.loads(STATE.read_text())
    if phase=='N':
        directory=ROOT/'qa/final_methodology_closure/network_v2b'
        receipt_path=directory/'NETWORK_V2B_FINAL_VERIFICATION.json'
        manifest=directory/'NETWORK_V2B_OUTPUT_MANIFEST.csv'
        predecessor='H'
        result='NETWORK_V2B_PASS'
        transfer=ROOT/'docs/final_methodology_closure/PHASE_N_SIGNED_INTERFACE_TRANSFER.md'
        decisions=['NATIVE_SIGNED_LOSSLESS_ZERO_COST_INTERVAL_PROJECTION',
                   'ASYMMETRIC_MW_AND_CORS_TOPOLOGY_EXACT','COMMON_NET_FLOW_A_TO_B_MW']
    elif phase=='C':
        directory=ROOT/'qa/final_methodology_closure/network_contract'
        receipt_path=directory/'NETWORK_CONTRACT_FINAL_VERIFICATION.json'
        manifest=directory/'NETWORK_CONTRACT_OUTPUT_MANIFEST.csv'
        predecessor='N'
        result='NETWORK_CONTRACT_RECONCILIATION_PASS'
        transfer=None
        decisions=['FROZEN_REGISTER_NATIVE_WORKBOOK_FORMULAS_AND_TERNA_EVIDENCE',
            'PROJECT_PLUS_TWO_YEAR_SLOW_DELAY','EMBEDDED_PROJECT_CONTRIBUTIONS_NOT_ADDED_TWICE',
            'TYRRHENIAN_HYPERGRID_FOREIGN_CORS_CONTRACT_RECONCILED_NO_MW_CORRECTION']
    else:
        raise ValueError('NETWORK_CHECKPOINT_UNKNOWN_PHASE')
    receipt=json.loads(receipt_path.read_text())
    if receipt['state']!=result:
        raise RuntimeError('NETWORK_PHASE_RECEIPT_NOT_PASS')
    table=pd.read_csv(manifest)
    if table.path.duplicated().any():
        raise RuntimeError('NETWORK_MANIFEST_DUPLICATE')
    for row in table.itertuples():
        if sha256_file(ROOT/row.path)!=row.sha256:
            raise RuntimeError('NETWORK_PHASE_ARTIFACT_HASH_FAIL: '+row.path)
    artifacts=[ROOT/p for p in table.path]+[manifest]
    successors=receipt['packages'] if phase=='N' else receipt['parents']
    state=record_phase(phase,'PASS',parents=state['phases'][predecessor]['successor_parents'],
        artifacts=artifacts,decisions=decisions,tests=receipt['tests'],unresolved=[])
    state['phases'][phase].update({'phase_result':result,
        'successor_parents':[{k:p[k] for k in ('year','scenario','path','sha256')} for p in successors],
        'receipt':str(receipt_path.relative_to(ROOT)),'manifest':str(manifest.relative_to(ROOT))})
    if transfer:
        state['phases'][phase]['current_transfer']=str(transfer.relative_to(ROOT))
    state['optimization_model_constructed']=False
    dump_json(STATE,state)
    return {'state':result,'next_phase':state['next_phase']}

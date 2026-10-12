"""Non-solving OCGT additions to accepted final_v4 inputs, with exact isolation."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa

from .common import ROOT, ZONES, dump_json, load_yaml, sha256_file
from .final_capacity_revision import MODEL_CARRIERS, PROVENANCE, unit_count
from .final_hydro_spatial import COMPONENTS
from .stage_b_zonal_vre import no_models_or_solves
from .uc_heterogeneity import heat_rate_cost, normalize_children

VARIANTS = ('final_v5', 'final_v5_proportional_control')
SCENARIOS = ('Slow', 'Base', 'High')
FUELS = ('methane_gt_ocgt', 'hydrogen_gt_ocgt')
STATIC = ROOT / 'runtime_inputs/final_methodology_v4/2050/generators_static.csv'
META_FIELDS = ('model_version', 'adequacy_candidate')


def config_path(variant):
    if variant not in VARIANTS:
        raise ValueError('ADEQUACY_UNKNOWN_VARIANT')
    return ROOT / f'config/final_methodology_execution_{variant.removeprefix("final_")}.yaml'


def settings(variant='final_v5'):
    own = load_yaml(config_path(variant))
    cfg = {**load_yaml(ROOT / own['extends']), **own} if 'extends' in own else own
    from .final_methodology_networks import settings as final_settings
    baseline = final_settings('final_v4')
    cfg = {**cfg, **{k: baseline[k] for k in ('solver', 'water_values', 'water_mapping')}}
    if (cfg['schema_version'] != 'MEM_FINAL_METHODOLOGY_EXECUTION_V5_CANDIDATE' or
        cfg['variant'] != variant or cfg['years'] != [2050] or cfg['scenarios'] != list(SCENARIOS) or
        cfg['parent_variant'] != 'final_v4' or cfg['candidate_status'] != 'NON_CANONICAL_ADEQUACY_REINFORCEMENT_TEST' or
        cfg['no_automatic_execution'] is not True or cfg['all_scenarios_manually_executable'] is not True or
        cfg['canonical_promotion'] != 'NEVER_AUTOMATIC' or
        cfg['fuel_shares'] != dict.fromkeys(FUELS, .5) or cfg['ocgt_cohort_target_MW'] != 350.0 or
        cfg['target_programmable_derated_MW'] != 30000.0):
        raise RuntimeError('ADEQUACY_CONFIG_FAIL')
    family = variant.removeprefix('final_')
    result_family = 'v5_control' if variant == VARIANTS[1] else family
    for key, expected in (('result_root', f'results/final_methodology_{result_family}'),
                          ('network_root', f'networks/unsolved/final_methodology_{family}'),
                          ('qa_root', f'qa/final_methodology_{family}'),
                          ('runtime_root', f'runtime_inputs/final_methodology_{family}')):
        if cfg[key] != expected:
            raise RuntimeError('ADEQUACY_PATH_ISOLATION_FAIL')
    if variant == 'final_v5' and (cfg['allocation'] != 'NORTHERN_FOCUSED' or cfg['zone_shares'] != {'CNOR': .6, 'NORD': .4}):
        raise RuntimeError('ADEQUACY_GEOGRAPHY_FAIL')
    if variant == VARIANTS[1] and cfg['allocation'] != 'PROPORTIONAL_EXISTING_OCGT':
        raise RuntimeError('ADEQUACY_CONTROL_CONFIG_FAIL')
    cfg.update(execution_path_integrated=True,
               network_receipt=cfg['qa_root'] + '/PREPARATION_RECEIPT.json',
               network_lineage=cfg['qa_root'] + '/NETWORK_LINEAGE_AND_HASHES.csv')
    return cfg


def validate_case(year, scenario):
    if year != 2050 or scenario not in SCENARIOS:
        raise ValueError('ADEQUACY_CANDIDATE_IS_2050_ONLY')


def nominal_capacity(network):
    """Installed MW times accepted constant p_max_pu; not capacity credit."""
    names = network.generators.index[network.generators.carrier.isin(MODEL_CARRIERS)]
    dense = network.get_switchable_as_dense('Generator', 'p_max_pu')[names]
    if dense.empty or not np.isfinite(dense.to_numpy()).all() or not dense.eq(dense.iloc[0]).all().all():
        raise RuntimeError('ADEQUACY_NOMINAL_DERATING_NOT_CONSTANT')
    factor = dense.iloc[0]
    if not factor.between(0, 1).all():
        raise RuntimeError('ADEQUACY_INVALID_AVAILABILITY')
    out = network.generators.loc[names, ['bus', 'carrier', 'p_nom']].copy()
    out['availability'] = factor
    out['derated_MW'] = out.p_nom * factor
    return out


def increment_contract(network, cfg):
    baseline = nominal_capacity(network)
    ocgt = baseline.loc[baseline.carrier.isin(FUELS)]
    if not np.allclose(ocgt.availability, ocgt.availability.iloc[0], rtol=0, atol=0):
        raise RuntimeError('ADEQUACY_OCGT_AVAILABILITY_DIFFERS')
    factor = float(ocgt.availability.iloc[0])
    additional = cfg['target_programmable_derated_MW'] - float(baseline.derated_MW.sum())
    if additional <= 0 or factor <= 0:
        raise RuntimeError('ADEQUACY_NONPOSITIVE_INCREMENT')
    shares = cfg['zone_shares'] if cfg['allocation'] == 'NORTHERN_FOCUSED' else (
        ocgt.groupby('bus').p_nom.sum() / ocgt.p_nom.sum()).to_dict()
    if not set(shares).issubset(ZONES) or not math.isclose(sum(shares.values()), 1, abs_tol=1e-12):
        raise RuntimeError('ADEQUACY_ZONE_SHARE_FAIL')
    rows = [{'zone': z, 'carrier': fuel, 'zone_share': shares[z], 'fuel_share': .5,
             'availability': factor, 'installed_increment_MW': additional / factor * shares[z] * .5,
             'derated_increment_MW': additional * shares[z] * .5}
            for z in ZONES if shares.get(z, 0) > 0 for fuel in FUELS]
    return pd.DataFrame(rows), baseline


def unit_plan(parent, scenario, cfg, static=None, model=None):
    """Use native UC-v2A methane cohorts; H2 inherits continuous eligibility."""
    from .stage_b_uc2a import cost_coefficient
    static = pd.read_csv(STATIC) if static is None else static
    model = json.loads(PROVENANCE.read_text())['classes']['OCGT'] if model is None else model
    additions, baseline = increment_contract(parent, cfg)
    rows = []
    for addition in additions.to_dict('records'):
        source = static.loc[static.scenario.eq(scenario) & static.zone.eq(addition['zone']) &
                            static.carrier.eq(addition['carrier']) &
                            static.solver_subtechnology.str.endswith('_NON_CHP')]
        if len(source) != 1:
            raise RuntimeError('ADEQUACY_NON_CHP_SOURCE_AMBIGUOUS')
        source = source.iloc[0]
        templates = parent.generators.loc[parent.generators.carrier.eq(addition['carrier']) &
            parent.generators.bus.eq(addition['zone']) & parent.generators.index.str.contains(str(source.generator_id), regex=False)]
        if len(templates) != 1 or float(source.efficiency) != float(templates.efficiency.iloc[0]):
            raise RuntimeError('ADEQUACY_NON_CHP_TEMPLATE_AMBIGUOUS')
        template = templates.iloc[0]
        methane = addition['carrier'] == 'methane_gt_ocgt'
        if bool(template.committable) != methane:
            raise RuntimeError('ADEQUACY_UC_ELIGIBILITY_DRIFT')
        count = unit_count(addition['installed_increment_MW'], cfg['ocgt_cohort_target_MW'])
        if methane:
            children = normalize_children(addition['installed_increment_MW'], float(template.efficiency), count, model)
        else:
            children = pd.DataFrame([{'p_nom': addition['installed_increment_MW'] / count,
                'efficiency': float(template.efficiency), 'size_class': 'NOMINAL_350MW',
                'efficiency_class': 'INHERITED', 'allocation_mode': 'CONTINUOUS_H2_EQUAL_MODULES'}] * count)
        coefficient = cost_coefficient(source)
        for number, child in enumerate(children.to_dict('records'), 1):
            name = (f'UC2A__2050__{scenario}__V5_ADDITIONAL_{addition["zone"]}_{addition["carrier"].upper()}_NON_CHP'
                    f'__{child["size_class"]}__{child["efficiency_class"]}__U{number:03d}')
            mc = heat_rate_cost(float(template.marginal_cost), 1 / float(template.efficiency),
                                1 / child['efficiency'], coefficient)
            rows.append({**addition, 'year': 2050, 'scenario': scenario, 'child_id': name,
                'source_generator_id': source.generator_id, 'template_id': templates.index[0],
                'solver_subtechnology': source.solver_subtechnology, 'committable': methane,
                'p_nom': child['p_nom'], 'efficiency': child['efficiency'], 'marginal_cost': mc,
                'cohort_unit_count': count, 'size_class': child['size_class'],
                'efficiency_class': child['efficiency_class'], 'allocation_mode': child['allocation_mode'],
                'frozen_fuel_chargeable_CO2_coefficient': coefficient,
                'source_efficiency': float(template.efficiency), 'source_marginal_cost': float(template.marginal_cost),
                **{field: source[field] for field in ('fuel', 'CHP_flag', 'CCS_flag',
                    'VOM_EUR2025_per_MWh_el', 'fuel_price_EUR2025_per_MWh_th',
                    'direct_physical_CO2_t_per_MWh_th', 'chargeable_CO2_t_per_MWh_th',
                    'CO2_price_EUR2025_per_t', 'other_variable_cost_EUR2025_per_MWh_el',
                    'availability_class', 'source_parameters')},
                'derated_MW': child['p_nom'] * addition['availability']})
    return pd.DataFrame(rows), baseline


def expected_child(parent, row):
    child = parent.generators.loc[row.template_id].copy()
    child['p_nom'] = row.p_nom
    child['efficiency'] = row.efficiency
    child['marginal_cost'] = row.marginal_cost
    for field in ('start_up_cost', 'shut_down_cost', 'stand_by_cost'):
        child[field] = float(child[field]) / float(parent.generators.at[row.template_id, 'p_nom']) * row.p_nom
    return child


def transform(parent, plan, cfg, scenario, parent_path, parent_sha, kind):
    output = parent.copy()
    additions = {attribute: {} for attribute in parent.generators_t}
    for row in plan.itertuples():
        if row.child_id in output.generators.index:
            raise RuntimeError('ADEQUACY_DUPLICATE_UNIT')
        output.generators.loc[row.child_id] = expected_child(parent, row)
        for attribute, frame in parent.generators_t.items():
            if row.template_id in frame:
                if attribute in ('marginal_cost', 'efficiency'):
                    raise RuntimeError('ADEQUACY_UNRESOLVED_TIME_DEPENDENT_COST')
                additions[attribute][row.child_id] = frame[row.template_id].copy()
    for attribute, values in additions.items():
        if values:
            output.generators_t[attribute] = pd.concat([parent.generators_t[attribute],
                pd.DataFrame(values, index=parent.snapshots)], axis=1)
            output.generators_t[attribute].columns.name = parent.generators_t[attribute].columns.name
    output.meta.update(model_version=cfg['model_version'], adequacy_candidate={
        'status': cfg['candidate_status'], 'variant': cfg['variant'], 'year': 2050, 'scenario': scenario,
        'allocation': cfg['allocation'], 'parent_variant': 'final_v4', 'parent_path': parent_path,
        'parent_sha256': parent_sha, 'network_type': kind, 'canonical': False})
    return output


def exact_diff(parent, output, plan, cfg, scenario, kind):
    """No changes to existing model rows; only fully specified new generators."""
    pd.testing.assert_index_equal(parent.snapshots, output.snapshots, exact=True)
    pd.testing.assert_frame_equal(parent.snapshot_weightings, output.snapshot_weightings, check_exact=True)
    expected_names = set(plan.child_id) if len(plan) else set()
    if set(output.generators.index) - set(parent.generators.index) != expected_names:
        raise RuntimeError('ADEQUACY_NEW_UNIT_SET_FAIL')
    rows = []
    for static in COMPONENTS:
        dynamic = static + '_t'
        old, new = getattr(parent, static), getattr(output, static)
        retained = new.loc[old.index] if static == 'generators' else new
        pd.testing.assert_frame_equal(old, retained, check_exact=True)
        for attr, frame in getattr(parent, dynamic).items():
            actual = getattr(output, dynamic)[attr]
            if static == 'generators':
                pd.testing.assert_frame_equal(frame, actual.loc[:, frame.columns], check_exact=True)
                expected_columns = set(frame.columns) | {r.child_id for r in plan.itertuples() if r.template_id in frame}
                if set(actual.columns) != expected_columns:
                    raise RuntimeError('ADEQUACY_UNEXPECTED_DYNAMIC_COLUMN')
                for r in plan.itertuples():
                    if r.template_id in frame:
                        pd.testing.assert_series_equal(frame[r.template_id], actual[r.child_id], check_names=False, check_exact=True)
            else:
                pd.testing.assert_frame_equal(frame, actual, check_exact=True)
    for r in plan.itertuples():
        pd.testing.assert_series_equal(expected_child(parent, r), output.generators.loc[r.child_id],
                                       check_names=False, check_dtype=False, check_exact=True)
        for field, value in output.generators.loc[r.child_id].items():
            rows.append({'year': 2050, 'scenario': scenario, 'network_type': kind, 'component': 'generators',
                'row': r.child_id, 'field': field, 'before': None, 'after': value,
                'classification': 'EXPECTED_INCREMENTAL_NON_CHP_OCGT'})
        for attr, frame in parent.generators_t.items():
            if r.template_id in frame:
                rows.append({'year': 2050, 'scenario': scenario, 'network_type': kind, 'component': 'generators_t',
                    'row': r.child_id, 'field': attr, 'before': None, 'after': 'BYTE_VALUE_IDENTICAL_TO_TEMPLATE:' + r.template_id,
                    'classification': 'EXPECTED_INCREMENTAL_NON_CHP_OCGT'})
    allowed = META_FIELDS if len(plan) else ()
    if {k: v for k, v in parent.meta.items() if k not in allowed} != {k: v for k, v in output.meta.items() if k not in allowed}:
        raise RuntimeError('ADEQUACY_UNEXPECTED_METADATA')
    if len(plan) and (output.meta.get('model_version') != cfg['model_version'] or
                     output.meta.get('adequacy_candidate', {}).get('variant') != cfg['variant']):
        raise RuntimeError('ADEQUACY_CANDIDATE_METADATA_FAIL')
    if len(plan):
        meta = output.meta['adequacy_candidate']
        expected = {'status': cfg['candidate_status'], 'variant': cfg['variant'], 'year': 2050, 'scenario': scenario,
                    'allocation': cfg['allocation'], 'parent_variant': 'final_v4', 'network_type': kind, 'canonical': False}
        if (set(meta) != set(expected) | {'parent_path', 'parent_sha256'} or
            any(meta.get(k) != value for k, value in expected.items())):
            raise RuntimeError('ADEQUACY_CANDIDATE_METADATA_FAIL')
        for field in META_FIELDS:
            rows.append({'year': 2050, 'scenario': scenario, 'network_type': kind, 'component': 'meta',
                'row': '', 'field': field, 'before': parent.meta.get(field), 'after': output.meta[field],
                'classification': 'EXPECTED_CANDIDATE_LINEAGE_METADATA'})
    return rows


def capacity_qa(parent, output, plan, cfg, scenario, kind):
    from .final_methodology_networks import structural_check
    expected_units = int(parent.generators.committable.sum()) + (int(plan.committable.sum()) if len(plan) and kind == 'UC_INPUT' else 0)
    structural_check(output, 2050, scenario, continuous=kind == 'CONTINUOUS_REFERENCE', expected_units=expected_units)
    before, after = nominal_capacity(parent), nominal_capacity(output)
    if scenario == 'Slow':
        pd.testing.assert_frame_equal(before, after, check_exact=True)
    else:
        tolerance = cfg['capacity_tolerance_MW']
        if abs(after.derated_MW.sum() - cfg['target_programmable_derated_MW']) > tolerance:
            raise RuntimeError('ADEQUACY_TARGET_FAIL')
        increments, _ = increment_contract(parent, cfg)
        for addition in increments.itertuples():
            block = plan.loc[plan.zone.eq(addition.zone) & plan.carrier.eq(addition.carrier)]
            for actual, expected in ((block.p_nom.sum(), addition.installed_increment_MW),
                                     (block.derated_MW.sum(), addition.derated_increment_MW)):
                if abs(actual - expected) > tolerance:
                    raise RuntimeError('ADEQUACY_INCREMENT_RECONCILIATION_FAIL')
            if abs(np.average(1 / block.efficiency, weights=block.p_nom) - 1 / block.source_efficiency.iloc[0]) > 1e-12 or abs(
                np.average(block.marginal_cost, weights=block.p_nom) - block.source_marginal_cost.iloc[0]) > 1e-9:
                raise RuntimeError('ADEQUACY_COHORT_ECONOMICS_FAIL')
        added = output.generators.loc[plan.child_id]
        if added.p_nom_extendable.any() or not added.sign.eq(1).all():
            raise RuntimeError('ADEQUACY_GENERATOR_OPERATING_SCOPE_FAIL')
        if not plan.solver_subtechnology.str.endswith('_NON_CHP').all():
            raise RuntimeError('ADEQUACY_CHP_INCREMENT_FORBIDDEN')
    return {'status': 'PASS', 'scenario': scenario, 'network_type': kind, 'snapshots': 8760,
            'installed_programmable_before_MW': float(before.p_nom.sum()),
            'installed_programmable_after_MW': float(after.p_nom.sum()),
            'derated_programmable_before_MW': float(before.derated_MW.sum()),
            'derated_programmable_after_MW': float(after.derated_MW.sum()),
            'added_units': len(plan), 'UC_units': int(output.generators.committable.sum()),
            'unexpected_changed_model_fields': 0, 'optimization_model_constructed': False, 'optimizer_invocations': 0}


def protected_hashes():
    """Pin accepted inputs/results and current version records, not source edits."""
    names = ('networks/unsolved/final_methodology_v3', 'networks/unsolved/final_methodology_v4',
             'results/final_methodology_v3', 'results/final_methodology_v4', 'runtime_inputs/final_methodology_v4',
             'pre_pypsa_inputs', 'qa/final_methodology_v4', 'qa/final_v4_analytical_acceptance', 'docs/releases')
    paths = {p for name in names for p in (ROOT / name).rglob('*') if p.is_file()}
    paths |= {p for p in (ROOT / 'config').glob('*.yaml') if '_v5' not in p.name}
    return {str(p.relative_to(ROOT)): sha256_file(p) for p in sorted(paths)}


def verify_protected(inventory):
    for path, digest in inventory.items():
        if not (ROOT / path).is_file() or sha256_file(ROOT / path) != digest:
            raise RuntimeError('ADEQUACY_PROTECTED_ARTIFACT_CHANGED: ' + path)


def controls(cfg):
    paths = [config_path(cfg['variant']), ROOT / 'config/final_methodology_execution_v4.yaml', STATIC,
             PROVENANCE, ROOT / 'config/stage_b_uc1_common.yaml', ROOT / 'config/stage_b_uc2a_heterogeneous.yaml',
             ROOT / 'qa/final_methodology_v4/FINAL_V4_NETWORK_LINEAGE_AND_HASHES.csv']
    if cfg.get('extends'):
        paths.append(ROOT / cfg['extends'])
    return {str(p.relative_to(ROOT)): sha256_file(p) for p in paths}


def prepare(variant='final_v5'):
    from .final_capacity_revision import verify_frozen
    from .final_methodology_networks import _equal
    cfg = settings(variant)
    qa = ROOT / cfg['qa_root']
    if (qa / 'PREPARATION_RECEIPT.json').exists():
        raise RuntimeError('ADEQUACY_ALREADY_PREPARED_NO_OVERWRITE')
    with no_models_or_solves():
        parents = verify_frozen()
        qa.mkdir(parents=True, exist_ok=True)
        protected_path = qa / 'PROTECTED_BASELINE_HASHES.csv'
        if protected_path.exists():
            previous = pd.read_csv(protected_path)
            protected = dict(zip(previous.path, previous.sha256))
            verify_protected(protected)
        else:
            protected = protected_hashes()
            pd.DataFrame([{'path': p, 'sha256': h} for p, h in protected.items()]).to_csv(protected_path, index=False)
        lineage, deltas, packages, plans, capacities = [], [], [], [], []
        increment = None
        for scenario in SCENARIOS:
            case = parents.loc[parents.year.eq(2050) & parents.scenario.eq(scenario)]
            uc_item = case.loc[case.network_type.eq('UC_INPUT')].iloc[0]
            parent_uc = pypsa.Network(ROOT / uc_item.path)
            plan = pd.DataFrame(columns=['child_id', 'committable']) if scenario == 'Slow' else unit_plan(parent_uc, scenario, cfg)[0]
            if len(plan):
                national = float(plan.p_nom.sum())
                if increment is not None and not math.isclose(national, increment, abs_tol=1e-8, rel_tol=0):
                    raise RuntimeError('ADEQUACY_BASE_HIGH_BASELINE_DIFFERS')
                increment = national
                plans.append(plan)
            pair = {}
            for item in case.itertuples():
                if sha256_file(ROOT / item.path) != item.sha256:
                    raise RuntimeError('ADEQUACY_PARENT_HASH_FAIL')
                parent = parent_uc if item.network_type == 'UC_INPUT' else pypsa.Network(ROOT / item.path)
                if scenario == 'Slow':
                    destination, output = ROOT / item.path, parent
                    role = 'INHERITED_UNCHANGED_INPUT_CONTROL'
                else:
                    output = transform(parent, plan, cfg, scenario, item.path, item.sha256, item.network_type)
                    deltas += exact_diff(parent, output, plan, cfg, scenario, item.network_type)
                    suffix = '_CONTINUOUS_REFERENCE' if item.network_type == 'CONTINUOUS_REFERENCE' else ''
                    destination = ROOT / cfg['network_root'] / f'MEM_2050_{scenario.upper()}_{cfg["model_version"]}{suffix}_8760h_UNSOLVED.nc'
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    # An interrupted unpublished build may be resumed only if
                    # the existing file passes the complete equality check.
                    if not destination.exists():
                        output.export_to_netcdf(destination)
                    reloaded = pypsa.Network(destination)
                    _equal(output, reloaded)
                    exact_diff(parent, reloaded, plan, cfg, scenario, item.network_type)
                    output = reloaded
                    role = 'EXPERIMENTAL_INCREMENTAL_OCGT_SUCCESSOR'
                result = capacity_qa(parent, output, plan, cfg, scenario, item.network_type)
                packages.append(result)
                lineage.append({'year': 2050, 'scenario': scenario, 'network_type': item.network_type,
                    'parent_path': item.path, 'parent_sha256': item.sha256, 'path': str(destination.relative_to(ROOT)),
                    'sha256': sha256_file(destination), 'role': role, 'status': 'PASS', 'UC_units': result['UC_units']})
                before, after = nominal_capacity(parent), nominal_capacity(output)
                for carrier in MODEL_CARRIERS:
                    for zone in ZONES:
                        a = before.loc[before.carrier.eq(carrier) & before.bus.eq(zone)]
                        b = after.loc[after.carrier.eq(carrier) & after.bus.eq(zone)]
                        capacities.append({'year': 2050, 'scenario': scenario, 'network_type': item.network_type,
                            'zone': zone, 'carrier': carrier, 'installed_before_MW': a.p_nom.sum(), 'installed_after_MW': b.p_nom.sum(),
                            'derated_before_MW': a.derated_MW.sum(), 'derated_after_MW': b.derated_MW.sum()})
                pair[item.network_type] = output
            shared = ['bus', 'carrier', 'p_nom', 'efficiency', 'marginal_cost', 'sign', 'p_nom_extendable']
            pd.testing.assert_frame_equal(pair['UC_INPUT'].generators[shared], pair['CONTINUOUS_REFERENCE'].generators[shared], check_exact=True)
            pd.testing.assert_frame_equal(pair['UC_INPUT'].generators_t.p_max_pu, pair['CONTINUOUS_REFERENCE'].generators_t.p_max_pu, check_exact=True)
        combined = pd.concat(plans, ignore_index=True)
        combined.to_csv(qa / 'INCREMENTAL_UNIT_CROSSWALK.csv', index=False, float_format='%.17g')
        pd.DataFrame(lineage).to_csv(qa / 'NETWORK_LINEAGE_AND_HASHES.csv', index=False)
        pd.DataFrame(deltas).to_csv(qa / 'INPUT_DELTA.csv', index=False)
        pd.DataFrame(capacities).to_csv(qa / 'CAPACITY_RECONCILIATION.csv', index=False, float_format='%.17g')
        runtime = ROOT / cfg['runtime_root']
        runtime.mkdir(parents=True, exist_ok=True)
        combined.to_csv(runtime / 'ADDITIONAL_GENERATORS.csv', index=False, float_format='%.17g')
        inherited = pd.read_csv(ROOT / 'runtime_inputs/final_methodology_v4/FINAL_V4_RUNTIME_MANIFEST.csv')
        inherited['candidate_authority'] = 'HASH_PINNED_UNCHANGED_FINAL_V4_INPUT'
        inherited.to_csv(runtime / 'RUNTIME_MANIFEST.csv', index=False)
        verify_protected(protected)
        artifact_paths = [qa / name for name in ('NETWORK_LINEAGE_AND_HASHES.csv', 'INCREMENTAL_UNIT_CROSSWALK.csv',
                          'INPUT_DELTA.csv', 'CAPACITY_RECONCILIATION.csv', 'PROTECTED_BASELINE_HASHES.csv')]
        artifact_paths += [runtime / 'ADDITIONAL_GENERATORS.csv', runtime / 'RUNTIME_MANIFEST.csv']
        receipt = {'status': 'PASS', 'variant': variant, 'candidate_status': cfg['candidate_status'], 'canonical': False,
            'packages': packages, 'networks_export_reload': 4, 'slow_inputs_byte_identical_to_final_v4': True,
            'protected_files': len(protected), 'protected_hashes_unchanged': True,
            'unexpected_changed_model_fields': 0, 'optimization_model_constructed': False, 'optimizer_invocations': 0,
            'controls': controls(cfg), 'artifacts': {str(p.relative_to(ROOT)): sha256_file(p) for p in artifact_paths}}
        dump_json(qa / 'PREPARATION_RECEIPT.json', receipt)
        return receipt


def verify_frozen(variant):
    cfg = settings(variant)
    receipt = json.loads((ROOT / cfg['network_receipt']).read_text())
    if (receipt.get('status') != 'PASS' or receipt.get('canonical') is not False or
        receipt.get('variant') != variant or receipt.get('unexpected_changed_model_fields') != 0 or
        receipt.get('optimizer_invocations') != 0 or receipt.get('optimization_model_constructed') is not False):
        raise RuntimeError('ADEQUACY_PREPARATION_RECEIPT_FAIL')
    for path, digest in {**receipt['controls'], **receipt['artifacts']}.items():
        if sha256_file(ROOT / path) != digest:
            raise RuntimeError('ADEQUACY_FROZEN_CONTROL_HASH_FAIL: ' + path)
    table = pd.read_csv(ROOT / cfg['network_lineage'])
    if (len(table) != 6 or table.duplicated(['year', 'scenario', 'network_type']).any() or
        set(table[['year', 'scenario', 'network_type']].itertuples(index=False, name=None)) !=
        {(2050, s, t) for s in SCENARIOS for t in ('UC_INPUT', 'CONTINUOUS_REFERENCE')}):
        raise RuntimeError('ADEQUACY_LINEAGE_CASE_SET_FAIL')
    for row in table.itertuples():
        prefix = 'networks/unsolved/final_methodology_v4' if row.scenario == 'Slow' else cfg['network_root']
        if not (ROOT / row.path).resolve().is_relative_to((ROOT / prefix).resolve()):
            raise RuntimeError('ADEQUACY_INPUT_ISOLATION_FAIL')
    return table


def package(year, scenario, variant='final_v5'):
    validate_case(year, scenario)
    case = verify_frozen(variant).loc[lambda f: f.scenario.eq(scenario)].set_index('network_type')
    return {'year': year, 'scenario': scenario, 'path': case.at['UC_INPUT', 'path'],
            'sha256': case.at['UC_INPUT', 'sha256'], 'reference_path': case.at['CONTINUOUS_REFERENCE', 'path'],
            'reference_sha256': case.at['CONTINUOUS_REFERENCE', 'sha256'], 'UC_units': int(case.at['UC_INPUT', 'UC_units'])}


def preflight(year, scenario, variant='final_v5', *, persist=True):
    validate_case(year, scenario)
    cfg, item = settings(variant), package(year, scenario, variant)
    table = verify_frozen(variant)
    plan = pd.read_csv(ROOT / cfg['qa_root'] / 'INCREMENTAL_UNIT_CROSSWALK.csv', float_precision='round_trip')
    plan = plan.loc[plan.scenario.eq(scenario)]
    results = []
    with no_models_or_solves():
        for row in table.loc[table.scenario.eq(scenario)].itertuples():
            if sha256_file(ROOT / row.path) != row.sha256 or sha256_file(ROOT / row.parent_path) != row.parent_sha256:
                raise RuntimeError('ADEQUACY_INPUT_OR_PARENT_HASH_FAIL')
            parent = pypsa.Network(ROOT / row.parent_path)
            output = parent if row.path == row.parent_path else pypsa.Network(ROOT / row.path)
            exact_diff(parent, output, plan, cfg, scenario, row.network_type)
            results.append(capacity_qa(parent, output, plan, cfg, scenario, row.network_type))
    result = {'status': 'PASS', 'year': year, 'scenario': scenario, 'variant': variant,
        'candidate_status': cfg['candidate_status'], 'manual_authorized': True, 'automatic_execution': False,
        'prepared_for_manual_execution': True, 'optimization_model_constructed': False, 'production_solver_invocations': 0,
        'uc_input_path': item['path'], 'uc_input_sha256': item['sha256'], 'parent_path': item['reference_path'],
        'parent_sha256': item['reference_sha256'], 'snapshots': 8760, 'synthetic_units': item['UC_units'], 'checks': results}
    if persist:
        dump_json(ROOT / cfg['result_root'] / str(year) / scenario / 'QA/PREFLIGHT.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'verify-protected'])
    parser.add_argument('--variant', choices=VARIANTS, default='final_v5')
    args = parser.parse_args(argv)
    if args.action == 'prepare':
        result = prepare(args.variant)
    else:
        cfg = settings(args.variant)
        rows = pd.read_csv(ROOT / cfg['qa_root'] / 'PROTECTED_BASELINE_HASHES.csv')
        verify_protected(dict(zip(rows.path, rows.sha256)))
        result = {'status': 'PASS', 'protected_files': len(rows), 'optimizer_invocations': 0}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

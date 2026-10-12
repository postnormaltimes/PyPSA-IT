"""Sparse, non-solving external-cost successors of prepared final_v5 inputs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa

from .common import ROOT, PRICE_MARKETS, dump_json, load_yaml, sha256_file
from .final_hydro_spatial import COMPONENTS
from .stage_b_zonal_vre import no_models_or_solves

VARIANT = 'final_v6'
SCENARIOS = ('Slow', 'Base', 'High')
NETWORK_TYPES = ('UC_INPUT', 'CONTINUOUS_REFERENCE')
CONFIG_PATH = ROOT / 'config/final_methodology_execution_v6.yaml'
META_FIELDS = ('model_version', 'external_price_normalization')


def settings():
    from .final_adequacy_candidate import settings as parent_settings
    own = load_yaml(CONFIG_PATH)
    parent = parent_settings('final_v5')
    expected = dict(schema_version='MEM_FINAL_METHODOLOGY_EXECUTION_V6_CANDIDATE',
        variant=VARIANT, model_version='FINAL_METHODOLOGY_V6',
        candidate_status='NON_CANONICAL_EXTERNAL_SCARCITY_NORMALIZATION_TEST',
        years=[2050], scenarios=list(SCENARIOS), parent_variant='final_v5',
        no_automatic_execution=True, all_scenarios_manually_executable=True,
        canonical_promotion='NEVER_AUTOMATIC', external_sentinel_EUR_per_MWh=15000.0,
        external_replacement_EUR_per_MWh=400.0, external_sentinel_absolute_tolerance=1e-6,
        incremental_unit_crosswalk='qa/final_methodology_v5/INCREMENTAL_UNIT_CROSSWALK.csv')
    for key, value in expected.items():
        if own.get(key) != value:
            raise RuntimeError('ETX_CONFIG_FAIL: ' + key)
    for key, value in dict(result_root='results/final_methodology_v6',
        network_root='networks/unsolved/final_methodology_v6', qa_root='qa/final_methodology_v6',
        runtime_root='runtime_inputs/final_methodology_v6').items():
        if own.get(key) != value:
            raise RuntimeError('ETX_PATH_ISOLATION_FAIL: ' + key)
    return {**own, **{key: parent[key] for key in ('solver', 'water_values', 'water_mapping')},
        'execution_path_integrated': True,
        'network_receipt': own['qa_root'] + '/PREPARATION_RECEIPT.json',
        'network_lineage': own['qa_root'] + '/NETWORK_LINEAGE_AND_HASHES.csv'}


def validate_case(year, scenario):
    if year != 2050 or scenario not in SCENARIOS:
        raise ValueError('ETX_CANDIDATE_IS_2050_ONLY')


def normalize_costs(frame):
    """Absolute sentinel equality only; this is not an upper price cap."""
    values = frame.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise RuntimeError('ETX_NONFINITE_EXTERNAL_COST')
    mask = pd.DataFrame(np.abs(values - 15000.0) <= 1e-6, index=frame.index, columns=frame.columns)
    return frame.mask(mask, 400.0), mask


def external_costs(network):
    names = [f'EXT_SUPPLY_{market}' for market in PRICE_MARKETS]
    actual = network.generators.index[network.generators.index.str.startswith('EXT_SUPPLY_')]
    if set(actual) != set(names) or not network.generators.loc[names, 'carrier'].eq('external_market').all():
        raise RuntimeError('ETX_EXTERNAL_SUPPLY_POPULATION_FAIL')
    if any(network.generators.at[name, 'bus'] != 'EXT_' + market for market, name in zip(PRICE_MARKETS, names)):
        raise RuntimeError('ETX_EXTERNAL_MARKET_IDENTITY_FAIL')
    frame = network.generators_t.marginal_cost
    if not set(names).issubset(frame.columns) or not frame.index.equals(network.snapshots):
        raise RuntimeError('ETX_HOURLY_EXTERNAL_COST_MISSING')
    costs = frame.loc[:, names].copy()
    normalize_costs(costs)
    return costs


def lineage_metadata(cfg, scenario, kind, parent_path, parent_sha, derived_path, derived_sha):
    return dict(variant=VARIANT, status=cfg['candidate_status'], canonical=False,
        year=2050, scenario=scenario, network_type=kind, parent_variant='final_v5',
        parent_path=parent_path, parent_sha256=parent_sha,
        derived_input_path=derived_path, derived_input_sha256=derived_sha,
        sentinel_EUR_per_MWh=15000.0, replacement_EUR_per_MWh=400.0,
        absolute_tolerance=1e-6, Italian_VOLL_unchanged=True)


def transform(parent, metadata):
    before = external_costs(parent)
    revised, _ = normalize_costs(before)
    output = parent.copy()
    output.generators_t.marginal_cost.loc[:, before.columns] = revised
    output.meta.update(model_version='FINAL_METHODOLOGY_V6', external_price_normalization=metadata)
    return output


def exact_diff(parent, output, metadata):
    """Allow only specified external hourly costs and two version/lineage fields."""
    pd.testing.assert_index_equal(parent.snapshots, output.snapshots, exact=True)
    pd.testing.assert_frame_equal(parent.snapshot_weightings, output.snapshot_weightings,
        check_exact=True, check_dtype=False, check_freq=False)
    expected_costs, mask = normalize_costs(external_costs(parent))
    for component in COMPONENTS:
        pd.testing.assert_frame_equal(getattr(parent, component), getattr(output, component),
            check_exact=True, check_dtype=False)
        old_t, new_t = getattr(parent, component + '_t'), getattr(output, component + '_t')
        if set(old_t) != set(new_t):
            raise RuntimeError('ETX_DYNAMIC_SCHEMA_DRIFT: ' + component)
        for field, old in old_t.items():
            expected = old.copy() if component == 'generators' and field == 'marginal_cost' else old
            if component == 'generators' and field == 'marginal_cost':
                expected.loc[:, expected_costs.columns] = expected_costs
            pd.testing.assert_frame_equal(expected, new_t[field], check_exact=True,
                check_dtype=False, check_freq=False)
    expected_meta = {**parent.meta, 'model_version': 'FINAL_METHODOLOGY_V6',
                     'external_price_normalization': metadata}
    if output.meta != expected_meta:
        raise RuntimeError('ETX_UNEXPECTED_METADATA_DRIFT')
    for field in ('name', 'investment_periods', 'investment_period_weightings'):
        a, b = getattr(parent, field), getattr(output, field)
        if isinstance(a, pd.DataFrame):
            pd.testing.assert_frame_equal(a, b, check_exact=True)
        elif isinstance(a, pd.Index):
            pd.testing.assert_index_equal(a, b)
        elif a != b:
            raise RuntimeError('ETX_NETWORK_ATTRIBUTE_DRIFT: ' + field)
    changes = []
    for name in mask.columns:
        for timestamp in mask.index[mask[name]]:
            changes.append(dict(component='generators_t', row=name, timestamp=str(timestamp),
                field='marginal_cost', before=float(parent.generators_t.marginal_cost.at[timestamp, name]),
                after=400.0, classification='EXPECTED_EXTERNAL_SENTINEL_NORMALIZATION'))
    for field in META_FIELDS:
        changes.append(dict(component='meta', row='', timestamp='', field=field,
            before=json.dumps(parent.meta.get(field), sort_keys=True),
            after=json.dumps(output.meta[field], sort_keys=True),
            classification='EXPECTED_CANDIDATE_LINEAGE_METADATA'))
    return changes


def external_source():
    path = ROOT / 'runtime_inputs/final_methodology_v5/RUNTIME_MANIFEST.csv'
    table = pd.read_csv(path)
    rows = table.loc[table.path.str.replace('\\', '/', regex=False).str.endswith('/external_prices_hourly.parquet')]
    if len(rows) != 1:
        raise RuntimeError('ETX_PARENT_EXTERNAL_SOURCE_AMBIGUOUS')
    row = rows.iloc[0]
    source = ROOT / row.path
    if sha256_file(source) != row.sha256:
        raise RuntimeError('ETX_PARENT_EXTERNAL_SOURCE_HASH_FAIL')
    frame = pd.read_parquet(source)
    keys = ['year', 'scenario', 'snapshot', 'external_market']
    if (frame.duplicated(keys).any() or set(frame.year) != {2050} or
        set(frame.scenario) != set(SCENARIOS) or set(frame.external_market) != set(PRICE_MARKETS)):
        raise RuntimeError('ETX_EXTERNAL_SOURCE_COVERAGE_FAIL')
    return source, row.sha256, frame


def source_costs(frame, scenario):
    case = frame.loc[frame.scenario.eq(scenario)].pivot(index='snapshot',
        columns='external_market', values='price_EUR_per_MWh')
    case = case.reindex(columns=PRICE_MARKETS)
    case.columns = ['EXT_SUPPLY_' + market for market in case.columns]
    expected = pd.date_range('2019-01-01', periods=8760, freq='h', name='snapshot')
    if not case.index.equals(expected):
        raise RuntimeError('ETX_EXTERNAL_SOURCE_CHRONOLOGY_FAIL')
    normalize_costs(case)
    return case


def protected_hashes():
    """Pin existing model inputs, saved results and durable version records."""
    roots = ['pre_pypsa_inputs', 'runtime_inputs/p2x_flex_v1', 'runtime_inputs/p2x_flex_v2',
             'qa/final_v4_analytical_acceptance', 'docs/releases']
    for version in ('v3', 'v4', 'v5'):
        roots += [f'networks/unsolved/final_methodology_{version}',
                  f'results/final_methodology_{version}', f'runtime_inputs/final_methodology_{version}',
                  f'qa/final_methodology_{version}']
    paths = {p for root in roots for p in (ROOT / root).rglob('*') if p.is_file()}
    paths |= {p for p in (ROOT / 'config').glob('*.yaml') if '_v6' not in p.name}
    return {p.relative_to(ROOT).as_posix(): sha256_file(p) for p in sorted(paths)}


def verify_protected(inventory):
    for path, digest in inventory.items():
        if not (ROOT / path).is_file() or sha256_file(ROOT / path) != digest:
            raise RuntimeError('ETX_PROTECTED_ARTIFACT_CHANGED: ' + path)


def prepare():
    from .final_adequacy_candidate import verify_frozen as parent_frozen
    from .final_methodology_networks import _equal, structural_check
    cfg = settings()
    qa = ROOT / cfg['qa_root']
    if (qa / 'PREPARATION_RECEIPT.json').exists():
        raise RuntimeError('ETX_PREPARATION_ALREADY_EXISTS')
    parents = parent_frozen('final_v5')
    qa.mkdir(parents=True, exist_ok=True)
    with no_models_or_solves():
        print('Pinning protected final_v3/final_v4/final_v5 artifacts', flush=True)
        protected = protected_hashes()
        pd.DataFrame([{'path': p, 'sha256': h} for p, h in protected.items()]).to_csv(
            qa / 'PROTECTED_PARENT_HASHES.csv', index=False)
        source, source_sha, original = external_source()
        derived = original.copy()
        sentinel = np.abs(derived.price_EUR_per_MWh.to_numpy() - 15000.) <= 1e-6
        derived.loc[sentinel, 'price_EUR_per_MWh'] = 400.0
        runtime = ROOT / cfg['runtime_root'] / '2050'
        runtime.mkdir(parents=True, exist_ok=True)
        derived_path = runtime / 'external_prices_hourly.parquet'
        if derived_path.exists():
            pd.testing.assert_frame_equal(pd.read_parquet(derived_path), derived, check_exact=True)
        else:
            derived.to_parquet(derived_path, index=False)
        derived_rel, derived_sha = derived_path.relative_to(ROOT).as_posix(), sha256_file(derived_path)
        audit, summary, lineage, deltas, checks = [], [], [], [], []
        for scenario in ('High', 'Base', 'Slow'):
            case = parents.loc[parents.scenario.eq(scenario)]
            source_case = source_costs(original, scenario)
            normalized, mask = normalize_costs(source_case)
            pair = {}
            for row in case.itertuples():
                print('Preparing', scenario, row.network_type, flush=True)
                if sha256_file(ROOT / row.path) != row.sha256:
                    raise RuntimeError('ETX_PARENT_NETWORK_HASH_FAIL')
                parent = pypsa.Network(ROOT / row.path)
                pd.testing.assert_frame_equal(external_costs(parent), source_case,
                    check_exact=True, check_dtype=False, check_names=False, check_freq=False)
                metadata = lineage_metadata(cfg, scenario, row.network_type, row.path, row.sha256, derived_rel, derived_sha)
                output = transform(parent, metadata)
                suffix = '_CONTINUOUS_REFERENCE' if row.network_type == 'CONTINUOUS_REFERENCE' else ''
                destination = ROOT / cfg['network_root'] / f'MEM_2050_{scenario.upper()}_FINAL_METHODOLOGY_V6{suffix}_8760h_UNSOLVED.nc'
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    output.export_to_netcdf(destination)
                reloaded = pypsa.Network(destination)
                _equal(output, reloaded)
                changes = exact_diff(parent, reloaded, metadata)
                deltas += [dict(year=2050, scenario=scenario, network_type=row.network_type, **c) for c in changes]
                count = int(parent.generators.committable.sum())
                structural_check(reloaded, 2050, scenario, continuous=row.network_type == 'CONTINUOUS_REFERENCE', expected_units=count)
                lineage.append(dict(year=2050, scenario=scenario, network_type=row.network_type,
                    parent_path=row.path, parent_sha256=row.sha256, path=destination.relative_to(ROOT).as_posix(),
                    sha256=sha256_file(destination), UC_units=count, status='PASS'))
                checks.append(dict(year=2050, scenario=scenario, network_type=row.network_type,
                    snapshots=len(reloaded.snapshots), generators=len(reloaded.generators), UC_units=count,
                    changed_external_cost_cells=int(mask.to_numpy().sum()), unexpected_changed_model_fields=0,
                    Italian_VOLL_unchanged=True, all_capacities_profiles_and_constraints_unchanged=True,
                    status='PASS'))
                pair[row.network_type] = external_costs(reloaded)
                del parent, output, reloaded
            pd.testing.assert_frame_equal(pair['UC_INPUT'], pair['CONTINUOUS_REFERENCE'], check_exact=True)
            case_paths = case.set_index('network_type')
            for market in PRICE_MARKETS:
                name = 'EXT_SUPPLY_' + market
                summary.append(dict(year=2050, scenario=scenario, external_market=market, generator=name,
                    observations=8760, replacements=int(mask[name].sum()),
                    other_cost_observations_unchanged=int((~mask[name]).sum()),
                    reference_UC_costs_equal=True, status='PASS'))
                for timestamp in mask.index[mask[name]]:
                    audit.append(dict(year=2050, scenario=scenario, external_market=market, generator=name,
                        timestamp=str(timestamp), original_marginal_cost=source_case.at[timestamp, name],
                        revised_marginal_cost=400.0, unit='EUR/MWh',
                        source_file=source.relative_to(ROOT).as_posix(), source_sha256=source_sha,
                        parent_UC_path=case_paths.at['UC_INPUT', 'path'], parent_UC_sha256=case_paths.at['UC_INPUT', 'sha256'],
                        parent_reference_path=case_paths.at['CONTINUOUS_REFERENCE', 'path'],
                        parent_reference_sha256=case_paths.at['CONTINUOUS_REFERENCE', 'sha256'],
                        derived_input=derived_rel, derived_input_sha256=derived_sha))
        pd.DataFrame(audit).to_csv(qa / 'EXTERNAL_PRICE_INPUT_CHANGE_AUDIT.csv', index=False, float_format='%.17g')
        pd.DataFrame(summary).to_csv(qa / 'EXTERNAL_PRICE_COUNTS.csv', index=False)
        pd.DataFrame(lineage).to_csv(qa / 'NETWORK_LINEAGE_AND_HASHES.csv', index=False)
        pd.DataFrame(deltas).to_csv(qa / 'INPUT_DELTA.csv', index=False)
        inherited = pd.read_csv(ROOT / 'runtime_inputs/final_methodology_v5/RUNTIME_MANIFEST.csv')
        inherited['parent_path'] = inherited.path
        inherited['parent_sha256'] = inherited.sha256
        inherited['candidate_authority'] = 'HASH_PINNED_UNCHANGED_FINAL_V5_INPUT'
        replace = inherited.path.str.replace('\\', '/', regex=False).eq(source.relative_to(ROOT).as_posix())
        inherited.loc[replace, ['path', 'sha256', 'authority', 'role', 'candidate_authority']] = [
            derived_rel, derived_sha, 'FINAL_V6_DERIVED_EXTERNAL_COSTS', 'SENTINEL_ONLY_NORMALIZATION', 'FINAL_V6_DERIVED']
        inherited = pd.concat([inherited, pd.DataFrame([dict(year=2050, scenario='ALL',
            path=cfg['incremental_unit_crosswalk'], sha256=sha256_file(ROOT / cfg['incremental_unit_crosswalk']),
            authority='HASH_PINNED_FINAL_V5_OCGT_COHORTS', role='UNCHANGED_ADDITIONAL_UNIT_IDENTITY',
            candidate_authority='HASH_PINNED_UNCHANGED_FINAL_V5_INPUT')])], ignore_index=True)
        manifest = ROOT / cfg['runtime_root'] / 'RUNTIME_MANIFEST.csv'
        inherited.to_csv(manifest, index=False)
        controls = [CONFIG_PATH, ROOT / 'config/final_methodology_execution_v5.yaml',
            ROOT / 'qa/final_methodology_v5/PREPARATION_RECEIPT.json',
            ROOT / 'qa/final_methodology_v5/NETWORK_LINEAGE_AND_HASHES.csv',
            ROOT / cfg['incremental_unit_crosswalk'], source]
        artifacts = [qa / name for name in ('EXTERNAL_PRICE_INPUT_CHANGE_AUDIT.csv', 'EXTERNAL_PRICE_COUNTS.csv',
            'NETWORK_LINEAGE_AND_HASHES.csv', 'INPUT_DELTA.csv', 'PROTECTED_PARENT_HASHES.csv')] + [manifest, derived_path]
        print('Verifying protected parent artifacts', flush=True)
        verify_protected(protected)
        receipt = dict(status='PASS', variant=VARIANT, canonical=False, candidate_status=cfg['candidate_status'],
            checks=checks, networks_export_reload=6, replaced_unique_input_observations=len(audit),
            replaced_network_cost_cells=sum(c['changed_external_cost_cells'] for c in checks),
            protected_files_unchanged=len(protected), unexpected_changed_model_fields=0,
            optimization_model_constructed=False, production_solver_invocations=0,
            controls={p.relative_to(ROOT).as_posix(): sha256_file(p) for p in controls},
            artifacts={p.relative_to(ROOT).as_posix(): sha256_file(p) for p in artifacts},
            transformation_code_sha256=sha256_file(Path(__file__)))
        dump_json(qa / 'PREPARATION_RECEIPT.json', receipt)
        return receipt


def verify_frozen():
    cfg = settings()
    receipt = json.loads((ROOT / cfg['network_receipt']).read_text())
    if (receipt.get('status') != 'PASS' or receipt.get('variant') != VARIANT or receipt.get('canonical') is not False or
        receipt.get('unexpected_changed_model_fields') != 0 or receipt.get('production_solver_invocations') != 0 or
        receipt.get('optimization_model_constructed') is not False):
        raise RuntimeError('ETX_PREPARATION_RECEIPT_FAIL')
    for path, digest in {**receipt['controls'], **receipt['artifacts']}.items():
        if sha256_file(ROOT / path) != digest:
            raise RuntimeError('ETX_FROZEN_CONTROL_HASH_FAIL: ' + path)
    table = pd.read_csv(ROOT / cfg['network_lineage'])
    expected = {(2050, s, t) for s in SCENARIOS for t in NETWORK_TYPES}
    if len(table) != 6 or set(table[['year', 'scenario', 'network_type']].itertuples(index=False, name=None)) != expected:
        raise RuntimeError('ETX_LINEAGE_COVERAGE_FAIL')
    for row in table.itertuples():
        if not (ROOT / row.path).resolve().is_relative_to((ROOT / cfg['network_root']).resolve()):
            raise RuntimeError('ETX_INPUT_ISOLATION_FAIL')
    return table


def package(year, scenario):
    validate_case(year, scenario)
    case = verify_frozen().loc[lambda f: f.scenario.eq(scenario)].set_index('network_type')
    return dict(year=year, scenario=scenario, path=case.at['UC_INPUT', 'path'], sha256=case.at['UC_INPUT', 'sha256'],
        reference_path=case.at['CONTINUOUS_REFERENCE', 'path'], reference_sha256=case.at['CONTINUOUS_REFERENCE', 'sha256'],
        UC_units=int(case.at['UC_INPUT', 'UC_units']))


def preflight(year, scenario, *, persist=True):
    from .final_methodology_networks import structural_check
    validate_case(year, scenario)
    cfg, item = settings(), package(year, scenario)
    checks = []
    with no_models_or_solves():
        for row in verify_frozen().loc[lambda f: f.scenario.eq(scenario)].itertuples():
            if sha256_file(ROOT / row.path) != row.sha256 or sha256_file(ROOT / row.parent_path) != row.parent_sha256:
                raise RuntimeError('ETX_INPUT_OR_PARENT_HASH_FAIL')
            parent, candidate = pypsa.Network(ROOT / row.parent_path), pypsa.Network(ROOT / row.path)
            m = candidate.meta.get('external_price_normalization')
            expected = lineage_metadata(cfg, scenario, row.network_type, row.parent_path, row.parent_sha256,
                cfg['runtime_root'] + '/2050/external_prices_hourly.parquet',
                sha256_file(ROOT / cfg['runtime_root'] / '2050/external_prices_hourly.parquet'))
            if m != expected:
                raise RuntimeError('ETX_LINEAGE_METADATA_FAIL')
            changes = exact_diff(parent, candidate, expected)
            structural_check(candidate, year, scenario, continuous=row.network_type == 'CONTINUOUS_REFERENCE', expected_units=row.UC_units)
            checks.append(dict(network_type=row.network_type, snapshots=len(candidate.snapshots),
                replaced_cost_cells=sum(c['component'] == 'generators_t' for c in changes),
                unexpected_changed_model_fields=0, status='PASS'))
            del parent, candidate
    result = dict(status='PASS', year=year, scenario=scenario, variant=VARIANT,
        candidate_status=cfg['candidate_status'], manual_authorized=True, automatic_execution=False,
        prepared_for_manual_execution=True, snapshots=8760, synthetic_units=item['UC_units'],
        uc_input_path=item['path'], uc_input_sha256=item['sha256'], parent_path=item['reference_path'],
        parent_sha256=item['reference_sha256'], checks=checks,
        optimization_model_constructed=False, production_solver_invocations=0)
    if persist:
        dump_json(ROOT / cfg['result_root'] / str(year) / scenario / 'QA/PREFLIGHT.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'verify-protected'])
    args = parser.parse_args(argv)
    if args.action == 'prepare':
        result = prepare()
    else:
        rows = pd.read_csv(ROOT / settings()['qa_root'] / 'PROTECTED_PARENT_HASHES.csv')
        verify_protected(dict(zip(rows.path, rows.sha256)))
        result = dict(status='PASS', protected_files=len(rows), production_solver_invocations=0)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

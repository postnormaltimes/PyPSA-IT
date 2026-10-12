"""Generate frozen marginal-root packages from verified saved price-LP evidence.

Native dual tables are restored only in memory. Original networks, numerical
reporting and the frozen attribution implementation are never modified.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from ..common import ROOT, sha256_file
from . import marginal_setter_attribution as engine
from .lp_resource_capture import TERM_FILE, VARIABLE_FILE

VERSION = 'MEM_SAVED_LP_MARGINAL_ROOT_ADAPTER_V1'
FROZEN_PATH = ROOT / 'qa/final_methodology_v4/marginal_root_pipeline/FROZEN_METHODOLOGY.json'
FROZEN_SHA256 = '92140c2ab5d70d70b712e8febd36acdc8d1bff129a53b229da4e0289bedd252c'

DUAL_FIELDS = {
    'Bus-nodal_balance': ('buses', 'marginal_price'),
    'Generator-fix-p-lower': ('generators', 'mu_lower'),
    'Generator-com-p-lower': ('generators', 'mu_lower'),
    'Generator-fix-p-upper': ('generators', 'mu_upper'),
    'Generator-com-p-upper': ('generators', 'mu_upper'),
    'Generator-p-ramp_limit_up': ('generators', 'mu_ramp_limit_up'),
    'Generator-p-ramp_limit_down': ('generators', 'mu_ramp_limit_down'),
    'Link-fix-p-lower': ('links', 'mu_lower'),
    'Link-fix-p-upper': ('links', 'mu_upper'),
    'Store-fix-e-lower': ('stores', 'mu_lower'),
    'Store-fix-e-upper': ('stores', 'mu_upper'),
    'Store-energy_balance': ('stores', 'mu_energy_balance'),
}


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _freeze(path):
    if sha256_file(path) != FROZEN_SHA256:
        raise RuntimeError('MARGINAL_ROOT_FROZEN_RECORD_CHANGED')
    record = _read(path)
    if (record.get('status') != 'FROZEN_AFTER_BASE_PASS' or
            record.get('methodology') != engine.RULES or
            record.get('implementation_sha256') != sha256_file(Path(engine.__file__))):
        raise RuntimeError('MARGINAL_ROOT_FROZEN_IMPLEMENTATION_CHANGED')
    return record


def _batches(path, columns=None):
    for batch in pq.ParquetFile(path).iter_batches(batch_size=65536, columns=columns):
        yield batch.to_pandas()


def _capture(sources, year, scenario):
    """Validate the root-relevant native primitives; no economic-stack consumer."""
    receipt = _read(sources['solve_receipt'])
    evidence = receipt.get('price_constraint_evidence')
    if not evidence:
        return None
    directory = sources['network'].parent
    path = directory / Path(evidence['path']).name
    if not path.is_file() or sha256_file(path) != evidence['sha256']:
        raise RuntimeError('MARGINAL_ROOT_CAPTURE_HASH_CONFLICT')
    manifest = _read(path)
    if manifest.get('schema_version') != 'MEM_FIXED_LP_CONSTRAINT_EVIDENCE_V2':
        raise RuntimeError('MARGINAL_ROOT_COMPLETE_NATIVE_CAPTURE_REQUIRED')
    identity = manifest.get('provenance', {})
    for field in ('year', 'scenario', 'kind', 'job_id', 'input_sha256',
                  'fixed_commitment_milp_sha256', 'solved_sha256'):
        if identity.get(field) != receipt.get(field):
            raise RuntimeError('MARGINAL_ROOT_CAPTURE_IDENTITY_CONFLICT: ' + field)
    if (identity.get('year') != year or identity.get('scenario') != scenario or
            identity.get('result_branch') != receipt.get('final_methodology_variant') or
            manifest.get('stationarity_scope') !=
            'ALL_NATIVE_CONSTRAINT_FAMILIES_WITH_DISPATCH_OR_STORE_STATE_INCIDENCE'):
        raise RuntimeError('MARGINAL_ROOT_CAPTURE_SCOPE_CONFLICT')
    paths = {'manifest': path, 'constraints': directory / manifest['table']}
    if (Path(manifest['table']).name != manifest['table'] or
            manifest['table_sha256'] != evidence['table_sha256'] or
            sha256_file(paths['constraints']) != manifest['table_sha256'] or
            pq.ParquetFile(paths['constraints']).metadata.num_rows != manifest['rows']):
        raise RuntimeError('MARGINAL_ROOT_NATIVE_CONSTRAINT_CAPTURE_CONFLICT')
    for key, expected_name in (('variables', VARIABLE_FILE), ('terms', TERM_FILE)):
        item = manifest.get('additional_tables', {}).get(key, {})
        if item.get('path') != expected_name:
            raise RuntimeError('MARGINAL_ROOT_NATIVE_PRIMITIVES_MISSING: ' + key)
        paths[key] = directory / expected_name
        if (not paths[key].is_file() or sha256_file(paths[key]) != item['sha256'] or
                pq.ParquetFile(paths[key]).metadata.num_rows != item['rows']):
            raise RuntimeError('MARGINAL_ROOT_NATIVE_PRIMITIVES_CONFLICT: ' + key)
    if manifest['additional_tables']['variables'].get('objective_type') != 'LINEAR':
        raise RuntimeError('MARGINAL_ROOT_NATIVE_OBJECTIVE_UNSUPPORTED')
    return manifest, paths


def restore_native_duals(n, manifest, paths):
    """Restore proven native duals and validate matched primal stationarity.

    Zero fills denote absent native constraint rows, not missing exported data.
    Every captured row/count is reconciled before the frozen engine is called.
    """
    snapshots = pd.DatetimeIndex(n.snapshots)
    expected = pd.date_range('2019-01-01', '2020-01-01', freq='h', inclusive='left')
    if (not snapshots.equals(expected) or manifest['snapshot_count'] != len(snapshots) or
            manifest['snapshot_sha256'] != sha256(snapshots.asi8.tobytes()).hexdigest()):
        raise RuntimeError('MARGINAL_ROOT_NATIVE_CHRONOLOGY_CONFLICT')
    weights = n.snapshot_weightings.objective.to_numpy(float)
    if not np.isfinite(weights).all() or (weights <= 0).any():
        raise RuntimeError('MARGINAL_ROOT_OBJECTIVE_WEIGHTS_INVALID')
    summaries = {entry['constraint']: entry for entry in manifest['constraints']
                 if entry['status'] == 'CAPTURED'}
    if len(summaries) != sum(entry['status'] == 'CAPTURED' for entry in manifest['constraints']):
        raise RuntimeError('MARGINAL_ROOT_DUPLICATE_CONSTRAINT_FAMILY')
    arrays, seen = {}, {}
    for table, field in set(DUAL_FIELDS.values()):
        shape = (len(snapshots), len(getattr(n, table)))
        arrays[(table, field)] = np.zeros(shape)
        seen[(table, field)] = np.zeros(shape, dtype=bool)
    largest_row = max((int(batch.constraint_label.max()) for batch in
                       _batches(paths['constraints'], ['constraint_label']) if len(batch)), default=-1)
    row_duals = np.full(largest_row + 1, np.nan)
    counts = Counter()
    globals_seen = set()
    for frame in _batches(paths['constraints']):
        if (frame.constraint_label.duplicated().any() or frame.constraint_label.lt(0).any() or
                not np.isfinite(frame[['raw_dual', 'rhs', 'activity', 'slack']].to_numpy(float)).all()):
            raise RuntimeError('MARGINAL_ROOT_INVALID_NATIVE_CONSTRAINT_ROWS')
        labels = frame.constraint_label.to_numpy(int)
        if np.isfinite(row_duals[labels]).any():
            raise RuntimeError('MARGINAL_ROOT_DUPLICATE_NATIVE_CONSTRAINT_LABEL')
        row_duals[labels] = frame.raw_dual.to_numpy(float)
        for family, block in frame.groupby('constraint_name', sort=False):
            if family not in summaries:
                raise RuntimeError('MARGINAL_ROOT_UNDECLARED_CONSTRAINT_FAMILY')
            counts[family] += len(block)
            if family.startswith('GlobalConstraint-P2X_ANNUAL_'):
                name = family.removeprefix('GlobalConstraint-')
                if len(block) != 1 or name in globals_seen or name not in n.global_constraints.index:
                    raise RuntimeError('MARGINAL_ROOT_NATIVE_P2X_SHADOW_CONFLICT')
                n.global_constraints.loc[name, 'mu'] = float(block.raw_dual.iloc[0])
                globals_seen.add(name)
            destination = DUAL_FIELDS.get(family)
            # PyPSA 1.2.3 splits high-degree buses without changing balance semantics.
            if family.startswith('Bus-meshed-') and family.endswith('-nodal_balance'):
                destination = ('buses', 'marginal_price')
            if destination is None:
                continue
            table, field = destination
            ti = snapshots.get_indexer(pd.DatetimeIndex(block.snapshot))
            ai = getattr(n, table).index.get_indexer(block.asset_id)
            if ((ti < 0).any() or (ai < 0).any() or block.duplicated(['snapshot', 'asset_id']).any() or
                    seen[(table, field)][ti, ai].any()):
                raise RuntimeError('MARGINAL_ROOT_NATIVE_ASSET_COORDINATE_CONFLICT')
            arrays[(table, field)][ti, ai] = block.raw_dual.to_numpy(float)
            seen[(table, field)][ti, ai] = True
    if any(counts[name] != entry['active_rows'] for name, entry in summaries.items()):
        raise RuntimeError('MARGINAL_ROOT_NATIVE_ROW_COVERAGE_CONFLICT')
    if any(entry['coordinate_slots'] != entry['active_rows'] + entry['inactive_slots']
           for entry in summaries.values()):
        raise RuntimeError('MARGINAL_ROOT_NATIVE_MASK_COVERAGE_CONFLICT')
    wanted_globals = set(n.global_constraints.index[n.global_constraints.index.str.startswith('P2X_ANNUAL_')])
    if globals_seen != wanted_globals:
        raise RuntimeError('MARGINAL_ROOT_NATIVE_P2X_SHADOW_MISSING')
    for family, (table, field) in DUAL_FIELDS.items():
        if field in ('marginal_price', 'mu_ramp_limit_up', 'mu_ramp_limit_down'):
            continue
        static = getattr(n, table)
        names = static.index
        if table == 'generators':
            names = static.index[static.committable.eq('-com-' in family)]
        if len(names) and family not in summaries:
            raise RuntimeError('MARGINAL_ROOT_NATIVE_BOUND_FAMILY_MISSING: ' + family)
    for table, rating in (('generators', 'p_nom'), ('links', 'p_nom'), ('stores', 'e_nom')):
        static = getattr(n, table)
        required = static.index.get_indexer(static.index[static[rating].gt(0)])
        for field in ('mu_lower', 'mu_upper'):
            if not seen[(table, field)][:, required].all():
                raise RuntimeError('MARGINAL_ROOT_NATIVE_BOUND_ROWS_MISSING: ' + table)
    for (table, field), values in arrays.items():
        if table == 'buses':
            values = values / weights[:, None]  # Native PyPSA Bus post-processing only.
        prior = getattr(n, table + '_t').get(field)
        if prior is not None and len(prior.columns):
            ai = getattr(n, table).index.get_indexer(prior.columns)
            saved = prior.to_numpy(float)
            finite = np.isfinite(saved)
            if ((ai < 0).any() or not prior.index.equals(snapshots) or
                    (np.isnan(saved) & seen[(table, field)][:, ai]).any() or
                    np.isinf(saved).any() or
                    not np.allclose(saved[finite], values[:, ai][finite], atol=engine.TOLERANCE, rtol=0)):
                raise RuntimeError('MARGINAL_ROOT_EXPORTED_DUAL_CONFLICT: ' + table + '.' + field)
        getattr(n, table + '_t')[field] = pd.DataFrame(values, index=snapshots, columns=getattr(n, table).index)
    needed_buses = set(n.generators.loc[n.generators.p_nom.gt(0), 'bus']) | set(n.stores.bus) | set(n.loads.bus)
    for column in [name for name in n.links if name.startswith('bus')]:
        needed_buses.update(n.links.loc[n.links.p_nom.gt(0), column].loc[lambda values: values.ne('')])
    bus_index = n.buses.index.get_indexer(sorted(needed_buses))
    if (bus_index < 0).any() or not seen[('buses', 'marginal_price')][:, bus_index].all():
        raise RuntimeError('MARGINAL_ROOT_NATIVE_BUS_BALANCE_MISSING')

    largest_var = max((int(batch.variable_label.max()) for batch in
                       _batches(paths['variables'], ['variable_label']) if len(batch)), default=-1)
    objective = np.full(largest_var + 1, np.nan)
    gradient = np.zeros(largest_var + 1)
    scoped = np.zeros(largest_var + 1, dtype=bool)
    variable_counts = Counter()
    fields = {'Generator-p': ('generators', 'p'), 'Link-p': ('links', 'p0'),
              'Store-p': ('stores', 'p'), 'Store-e': ('stores', 'e'),
              **{'Generator-' + attr: ('generators', attr) for attr in ('status', 'start_up', 'shut_down')}}
    variable_seen = {family: np.zeros((len(snapshots), len(getattr(n, table))), dtype=bool)
                     for family, (table, _) in fields.items()}
    for frame in _batches(paths['variables']):
        ids = frame.variable_label.to_numpy(int)
        if ((ids < 0).any() or frame.variable_label.duplicated().any() or
                np.isfinite(objective[ids]).any() or
                not np.isfinite(frame[['solution', 'objective_coefficient']].to_numpy(float)).all()):
            raise RuntimeError('MARGINAL_ROOT_INVALID_NATIVE_VARIABLE_ROWS')
        objective[ids] = frame.objective_coefficient.to_numpy(float)
        for family, block in frame.groupby('variable_name', sort=False):
            variable_counts[family] += len(block)
            if family not in fields:
                continue
            table, field = fields[family]
            ti = snapshots.get_indexer(pd.DatetimeIndex(block.snapshot))
            ai = getattr(n, table).index.get_indexer(block.asset_id)
            if (ti < 0).any() or (ai < 0).any():
                raise RuntimeError('MARGINAL_ROOT_VARIABLE_ASSET_COORDINATE_CONFLICT')
            if block.duplicated(['snapshot', 'asset_id']).any() or variable_seen[family][ti, ai].any():
                raise RuntimeError('MARGINAL_ROOT_DUPLICATE_NATIVE_VARIABLE_COORDINATE')
            variable_seen[family][ti, ai] = True
            saved = getattr(n, table + '_t')[field].reindex(index=snapshots, columns=getattr(n, table).index, fill_value=0.)
            if not np.allclose(saved.to_numpy(float)[ti, ai], block.solution, atol=engine.POWER_TOLERANCE, rtol=0):
                raise RuntimeError('MARGINAL_ROOT_NATIVE_PRIMAL_CONFLICT: ' + family)
            if family in ('Generator-p', 'Link-p', 'Store-p', 'Store-e'):
                scoped[block.variable_label.to_numpy(int)] = True
    var_meta = manifest['additional_tables']['variables']['families']
    if any(variable_counts[entry['variable']] != entry['active_rows'] for entry in var_meta):
        raise RuntimeError('MARGINAL_ROOT_NATIVE_VARIABLE_COVERAGE_CONFLICT')
    for family, (table, _) in fields.items():
        static = getattr(n, table)
        required = static.index[static.committable] if family.startswith(('Generator-status', 'Generator-start_up', 'Generator-shut_down')) else (
            static.index[static['e_nom' if table == 'stores' else 'p_nom'].gt(0)])
        if not variable_seen[family][:, static.index.get_indexer(required)].all():
            raise RuntimeError('MARGINAL_ROOT_NATIVE_VARIABLE_ROWS_MISSING: ' + family)
    for terms in _batches(paths['terms']):
        rows, variables = terms.constraint_label.to_numpy(int), terms.variable_label.to_numpy(int)
        if ((rows < 0).any() or (variables < 0).any() or
                (rows >= len(row_duals)).any() or (variables >= len(objective)).any() or
                not np.isfinite(row_duals[rows]).all() or not np.isfinite(objective[variables]).all() or
                not np.isfinite(terms.coefficient.to_numpy(float)).all()):
            raise RuntimeError('MARGINAL_ROOT_UNBOUND_NATIVE_TERM')
        np.add.at(gradient, variables, terms.coefficient.to_numpy(float) * row_duals[rows])
    residual = objective[scoped] - gradient[scoped]
    # Use the frozen price tolerance after the actual native objective weighting.
    error = float(np.max(np.abs(residual), initial=0.))
    minimum_weight = float(weights.min())
    if not np.isfinite(error) or error / minimum_weight > engine.TOLERANCE:
        raise RuntimeError('MARGINAL_ROOT_NATIVE_STATIONARITY_FAILURE')
    return dict(native_variable_rows=sum(variable_counts.values()),
                active_constraint_rows=sum(counts.values()),
                stationarity_max_abs_native=error,
                inactive_constraint_contributions='PROVEN_ABSENT_NATIVE_ROWS',
                economic_stack_population_used=False)


def generate(year, scenario, report_dir, *, frozen_methodology=FROZEN_PATH):
    """Create the same V2 root/candidate schema before routine regime rendering."""
    from . import marginal_regime as regime
    from .uc2_postprocess import no_solver_calls
    report = Path(report_dir).resolve()
    target = report / regime.config()['root_input']
    expected = regime._input_paths(year, scenario, target)
    if all(path.is_file() for path in expected):
        if _read(expected[2]).get('status') == 'KKT_VALID_REGIME_INTERPRETATION_REQUIRED':
            qa = regime._validate_pending_interpretation(year, scenario, expected, report)
            return dict(status='WAITING_FOR_SUPPORTED_ROOT_INTERPRETATION',
                        reason=qa['regime_interpretation_required'], implementation_status='IMPLEMENTATION_READY')
        regime._validate_inputs(year, scenario, expected, report)
        return dict(status='VALIDATED_ROOT_PACKAGE_REUSED', implementation_status='IMPLEMENTATION_READY')
    if target.exists() and any(target.iterdir()):
        raise RuntimeError('MARGINAL_ROOT_PARTIAL_PACKAGE_REQUIRES_REVIEW')
    from .. import stage_b_uc2 as uc2
    receipt_path = uc2.job_paths(year, scenario, 'FIXED_COMMITMENT_PRICE_LP')['receipt']
    if not receipt_path.is_file() or not _read(receipt_path).get('price_constraint_evidence'):
        return dict(status='WAITING_FOR_SAVED_NATIVE_ROOT_EVIDENCE',
                    reason='No saved native pricing capture; existing results do not require a rerun',
                    implementation_status='IMPLEMENTATION_READY', production_validated=False)
    sources = engine.discover_sources(year, scenario)
    capture = _capture(sources, year, scenario)
    if capture is None:
        return dict(status='WAITING_FOR_SAVED_NATIVE_ROOT_EVIDENCE', reason='Historical LP has no complete native capture',
                    implementation_status='IMPLEMENTATION_READY', production_validated=False)
    _freeze(frozen_methodology)
    manifest, paths = capture
    with no_solver_calls() as guard:
        import pypsa
        n = pypsa.Network(sources['network'])
        native = restore_native_duals(n, manifest, paths)
        prices = pd.read_csv(sources['prices'], index_col=0, parse_dates=True)
        zones = ['CNOR' if zone == 'CNORD' else zone for zone in engine.ZONES]
        delta = prices[zones].to_numpy(float) - n.buses_t.marginal_price[zones].to_numpy(float)
        if (not prices.index.equals(n.snapshots) or not np.isfinite(delta).all() or
                np.abs(delta).max() > 1e-8):
            raise RuntimeError('MARGINAL_ROOT_PRICE_SOURCE_CONFLICT')
        evidence = engine.build_evidence(n, pd.read_csv(sources['interfaces']), global_duals_persisted=True)
        table, audit = engine.attribute(evidence, year, scenario)
        sensitivity = {}
        for tolerance in (1e-7, 1e-5):
            other, _ = engine.attribute(evidence, year, scenario, tolerance, retain_audit=False)
            changed = int((other.primary_attribution_class != table.primary_attribution_class).sum())
            sensitivity[str(tolerance)] = dict(changed_zone_hours=changed)
            if changed:
                raise RuntimeError('MATERIAL_TOLERANCE_INSTABILITY: ' + str(changed))
        unresolved = []
        indeterminate = int(table.primary_attribution_class.eq('OTHER_INDETERMINATE').sum())
        if indeterminate:
            unresolved.append(dict(reason='NO_ADMISSIBLE_KKT_ROOT', zone_hours=indeterminate))
        if len(audit) and audit.carrier.eq('load_shedding').any():
            count = len(audit.loc[audit.carrier.eq('load_shedding'), ['snapshot', 'target_zone']].drop_duplicates())
            unresolved.append(dict(reason='LOAD_SHEDDING_ROOT_INTERPRETATION_NOT_IN_FROZEN_REGIME_CROSSWALK', zone_hours=count))
        if not unresolved:
            regime._validate_root_audit(table, audit, year, scenario)
        production_validated = not n.meta.get('saved_array_validation_fixture', False)
        qa = dict(status='PASS_FROZEN_METHOD_ATTRIBUTION', methodology=engine.RULES,
                  methodology_frozen=True, implementation_sha256=sha256_file(Path(engine.__file__)),
                  frozen_methodology_sha256=sha256_file(frozen_methodology),
                  source_hashes={**sources['hashes'], **regime._pins(paths.values())},
                  job_id=sources['job_id'], fixed_commitment_milp_sha256=sources['fixed_commitment_milp_sha256'],
                  rows=len(table), snapshots=len(n.snapshots), zones=list(engine.ZONES),
                  accepted_price_max_error_EUR_MWh=float(np.abs(delta).max()),
                  KKT_residuals=evidence['residuals'], P2X_shadows=evidence['P2X_shadows'],
                  tolerance_sensitivity=sensitivity, native_evidence_validation=native,
                  adapter_version=VERSION, adapter_sha256=sha256_file(Path(__file__)),
                  validation_scope='PRODUCTION_VALIDATED' if production_validated else 'SAVED_ARRAY_FIXTURE',
                  solver_invocations=guard['solver_invocations'], network_modifications=0,
                  network_duals_restored_in_memory=True,
                  price_formation_primal_authority='FIXED_COMMITMENT_PRICE_LP_ONLY_NOT_UC_PHYSICAL_REPORTING')
        if unresolved:
            qa.update(status='KKT_VALID_REGIME_INTERPRETATION_REQUIRED', regime_interpretation_required=unresolved)
        target.mkdir(parents=True, exist_ok=True)
        table.to_parquet(expected[0], index=False)
        audit.to_parquet(expected[1], index=False)
        shutil.copyfile(frozen_methodology, expected[3])
        qa['artifact_hashes'] = {path.name: sha256_file(path) for path in expected[:2]}
        expected[2].write_text(json.dumps(qa, indent=2) + '\n', encoding='utf-8')
        if unresolved:
            regime._validate_pending_interpretation(year, scenario, expected, report)
            return dict(status='WAITING_FOR_SUPPORTED_ROOT_INTERPRETATION', reason=unresolved,
                        implementation_status='IMPLEMENTATION_READY', root_package=str(target), solver_invocations=0)
        regime._validate_inputs(year, scenario, expected, report)
        return dict(status='VALIDATED_ROOT_PACKAGE_GENERATED', implementation_status='IMPLEMENTATION_READY',
                    root_package=str(target), rows=len(table), production_validated=production_validated,
                    solver_invocations=0, source_LP_sha256=manifest['provenance']['solved_sha256'])

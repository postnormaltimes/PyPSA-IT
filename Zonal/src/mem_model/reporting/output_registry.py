"""Current output index projected from existing declarations; never produces plots.

The inspection registry owns case-figure declarations. Comparison and marginal
regime metadata own their declarations. This index consolidates those contracts
and records evidence gates without activating the optional economic prototype.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

import pandas as pd

from . import inspection

SCHEMA = 'MEM_CURRENT_OUTPUT_REGISTER_V1'
CSV_NAME = 'OUTPUT_REGISTER.csv'
JSON_NAME = 'OUTPUT_REGISTER.json'
WAITING = 'EVIDENCE_GATED_OUTPUTS_WAITING'


def _registered_hashes(receipt):
    hashes = {}
    for field in ('original_artifact_hashes', 'artifact_hashes'):
        values = receipt.get(field, {})
        if not isinstance(values, dict):
            raise ValueError('Malformed core source hash declarations')
        for name, digest in values.items():
            name = name.replace('\\', '/')
            if name in hashes and hashes[name] != digest:
                raise ValueError('Conflicting core source hash declarations: ' + name)
            hashes[name] = digest
    return hashes


def _relative(path, report):
    return Path(os.path.relpath(Path(path).resolve(), report)).as_posix()


def _resolve(value, roots):
    """Resolve declared evidence against the selected case before historical paths."""
    text = str(value).replace('\\', '/')
    parts = text.split('/')
    for root in roots:
        root = Path(root)
        if root.name in parts:
            marker = parts.index(root.name)
            same_case = (root.name != 'REPORTING' or marker == 0 or marker >= 2
                         and parts[marker - 1] == root.parent.name and parts[marker - 2] == root.parents[1].name)
            if same_case:
                candidate = root.joinpath(*parts[marker + 1:])
                if candidate.is_file():
                    return candidate.resolve()
        if root.name == 'REPORTING':
            for marker in range(len(parts) - 1):
                if parts[marker:marker + 2] == [root.parents[1].name, root.parent.name]:
                    candidate = root.parent.joinpath(*parts[marker + 2:])
                    if candidate.is_file():
                        return candidate.resolve()
        candidate = root / text
        if candidate.is_file():
            return candidate.resolve()
    candidate = Path(value)
    if candidate.is_absolute() and candidate.is_file():
        return candidate.resolve()
    raise ValueError('Declared output or evidence is missing: ' + text)


def _pin(path, report, evidence, expected=None):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError('Declared output or evidence is missing: ' + _relative(path, report))
    digest = inspection._sha(path)
    if expected is not None and digest != expected:
        raise ValueError('Output evidence hash mismatch: ' + _relative(path, report))
    name = _relative(path, report)
    if name in evidence and evidence[name] != digest:
        raise ValueError('Output evidence changed during projection: ' + name)
    evidence[name] = digest
    return path


def _receipt(path, report, evidence, roots, *, optional_artifacts=()):
    payload = inspection._read(_pin(path, report, evidence))
    if not isinstance(payload, dict):
        raise ValueError('Malformed output receipt')
    for key in ('artifact_hashes', 'source_hashes', 'input_hashes'):
        values = payload.get(key, {})
        if not isinstance(values, dict):
            raise ValueError('Malformed output hash declarations')
        for name, digest in values.items():
            if key == 'artifact_hashes' and name in optional_artifacts:
                try:
                    candidate = _resolve(name, roots)
                except ValueError:
                    continue
                if inspection._sha(candidate) != digest:
                    continue
            _pin(_resolve(name, roots), report, evidence, digest)
    return payload


def _format_check(path):
    try:
        if path.suffix.lower() == '.png':
            from PIL import Image
            with Image.open(path) as image:
                image.verify()
        elif path.suffix.lower() == '.svg':
            if ET.parse(path).getroot().tag.split('}')[-1] != 'svg':
                raise ValueError('Declared SVG has invalid document type')
        elif path.suffix.lower() == '.pdf':
            with path.open('rb') as handle:
                if not handle.read(5).startswith(b'%PDF-'):
                    raise ValueError('Declared PDF has invalid document type')
    except (OSError, ET.ParseError) as exc:
        raise ValueError('Malformed declared rendered output: ' + path.name) from exc


def _figure_files(entry, report, evidence, roots):
    declared = entry.get('files', [])
    if not isinstance(declared, list) or not declared or not entry.get('underlying_data'):
        raise ValueError('Declared comparison figure lacks files or data')
    files = [_resolve(name, roots) for name in declared]
    if {path.suffix.lower() for path in files} != {'.png', '.svg'}:
        raise ValueError('Accepted comparison requires declared PNG and SVG')
    for path in files:
        if _relative(path, report) not in evidence:
            raise ValueError('Unbound rendered comparison file')
        _format_check(path)
        _pin(path, report, evidence)
    sources = entry['underlying_data']
    sources = [sources] if isinstance(sources, str) else sources
    for source in sources:
        path = _resolve(source, roots)
        if _relative(path, report) not in evidence:
            raise ValueError('Unbound comparison data source')
    return [_relative(path, report) for path in files], [
        _relative(_resolve(source, roots), report) for source in sources]


def _record(output_id, output_type, authority, producer, inputs, status, *,
            required=True, files=None, scenarios=None, action='None', group=''):
    return dict(output_id=output_id, output_type=output_type, source_authority=authority,
                producer=producer, supported_years=[2040, 2050],
                supported_scenarios=['Slow', 'Base', 'High'], required_inputs=inputs,
                implementation_status='IMPLEMENTED' if required else 'EVIDENCE_GATED_OPTIONAL_REVIEW',
                report_integration='PROJECTION_OF_EXISTING_DECLARATIONS',
                final_qa_integration='validate_output_register', required=required,
                remaining_action=action, status=status, files=files or [],
                actual_scenarios=scenarios or [], group=group)


def _regime_record(report, year, scenario, registry, supplied, evidence):
    from . import marginal_regime
    cfg = marginal_regime.config()
    roots = marginal_regime._input_paths(year, scenario, report / cfg['root_input'])
    inputs = [_relative(path, report) for path in roots]
    declared = [row for row in registry if row['figure_id'].startswith('MARGINAL_REGIME/')]
    receipt_path = report / marginal_regime.RECEIPT
    if receipt_path.is_file():
        q = _receipt(receipt_path, report, evidence, [report, report / cfg['data_output'], report / cfg['figure_output']])
        if (q.get('status') != 'PASS_CANONICAL_MARGINAL_REGIME'
                or (q.get('model_year'), q.get('scenario')) != (year, scenario)
                or q.get('routine_activation') is not True or q.get('solver_invocations') != 0
                or q.get('network_opens') != 0 or len(declared) != 1):
            raise ValueError('Invalid declared marginal-regime output or identity')
        source_paths = {_resolve(name, [report]).resolve() for name in q.get('source_hashes', {})}
        if not {path.resolve() for path in roots}.issubset(source_paths):
            raise ValueError('Marginal-regime output lacks validated-root bindings')
        files = [_relative(path, report) for path in declared[0]['formats'].values()]
        for path in declared[0]['formats'].values():
            if _relative(path, report) not in evidence:
                raise ValueError('Marginal-regime figure is not receipt-bound')
        status, action = 'GENERATED', 'None'
    else:
        if declared or (supplied or {}).get('status', '').startswith('PASS'):
            raise ValueError('Declared marginal-regime output lacks its receipt')
        present = [path for path in roots if path.is_file()]
        for path in present:
            _pin(path, report, evidence)
            if path.suffix == '.json':
                inspection._read(path)
        status = ('WAITING_FOR_VALIDATED_ROOT_EVIDENCE' if len(present) != len(roots)
                  else 'ROOT_FILES_PRESENT_VALIDATION_AND_PRODUCTION_REQUIRED')
        action = 'Supply current validated roots and run the existing marginal-regime producer'
        if len(present) == len(roots) and inspection._read(roots[2]).get('status') == 'KKT_VALID_REGIME_INTERPRETATION_REQUIRED':
            qa = marginal_regime._validate_pending_interpretation(year, scenario, roots, report)
            status = 'WAITING_FOR_SUPPORTED_ROOT_INTERPRETATION'
            action = json.dumps(qa['regime_interpretation_required'], sort_keys=True)
        elif (supplied or {}).get('status') == 'WAITING_FOR_SUPPORTED_ROOT_INTERPRETATION':
            raise ValueError('Unresolved regime lacks its validated source package')
        files = []
    return _record('marginal_regime', 'ACCEPTED_ROUTINE_FIGURE',
                   'FIXED_COMMITMENT_PRICE_LP_DUALS_WITH_VALIDATED_NATIVE_ROOTS',
                   cfg['producer'], inputs, status, files=files, scenarios=[scenario], action=action)


def _dispatch_records(report, year, scenario, evidence):
    """Project the dispatch adapter's own declarations; no independent plot spec."""
    try:
        from .dispatch_figures import dispatch_figure_declarations, validate_dispatch_figures
    except ModuleNotFoundError as exc:
        if exc.name != 'mem_model.reporting.dispatch_figures':
            raise
        return [_record('dispatch_presentation', 'ACCEPTED_ROUTINE_PRODUCER', 'UC_MILP',
                        'mem_model.reporting.dispatch_figures.produce_dispatch_figures', [],
                        'WAITING_FOR_DISPATCH_PRESENTATION', scenarios=[scenario],
                        action='Generate the accepted principal dispatch presentation through its adapter')]
    declarations = dispatch_figure_declarations(year, scenario)
    metadata_path = report / 'DISPATCH_PRESENTATION/FIGURE_METADATA.json'
    receipt_path = report / 'DISPATCH_PRESENTATION/DISPATCH_PRESENTATION_RECEIPT.json'
    generated = {}
    diagnostic_status = 'PASS'
    if metadata_path.exists() or receipt_path.exists():
        validated = validate_dispatch_figures(report)
        if validated.get('status') != 'PASS_DISPATCH_PRESENTATION':
            raise ValueError('Dispatch presentation metadata/receipt pair is incomplete')
        diagnostic_status = validated.get('diagnostic_status', 'PASS')
        _receipt(receipt_path, report, evidence, [report, metadata_path.parent],
                 optional_artifacts=validated.get('optional_artifacts', []))
        _pin(metadata_path, report, evidence)
        metadata = inspection._read(metadata_path)
        if (metadata.get('year'), metadata.get('scenario')) != (year, scenario):
            raise ValueError('Dispatch presentation case identity mismatch')
        generated = {entry['figure_id']: entry for entry in metadata['figures']}
    records = []
    for declaration in declarations:
        output_id = declaration.get('figure_id', declaration.get('output_id'))
        if not output_id:
            raise ValueError('Malformed dispatch adapter declaration')
        required = declaration['required']
        entry = generated.get(output_id)
        if not required and diagnostic_status != 'PASS':
            entry = None
        files = [_relative(_resolve(entry[ext], [report]), report) for ext in ('png', 'svg')] if entry else []
        inputs = declaration.get('required_inputs', declaration.get('underlying_data', []))
        inputs = list(inputs) if not isinstance(inputs, str) else [inputs]
        inputs += [name for name in declaration.get('source_files', []) if name not in inputs]
        row = _record('DISPATCH_PRESENTATION/' + output_id, 'ACCEPTED_ROUTINE_FIGURE' if required else 'DIAGNOSTIC_FIGURE',
                      'UC_MILP', declaration['producer'], inputs,
                      'GENERATED' if entry else 'WAITING_FOR_DISPATCH_PRESENTATION',
                      required=required, files=files, scenarios=[scenario],
                      action='None' if entry else 'Generate through the existing dispatch adapter')
        row['implementation_status'] = 'IMPLEMENTED'
        records.append(row)
    return records


def _comparison_records(report, year, supplied, evidence):
    from ..visualization.uc2_comparison import SELECTED_COMPARISON_VIEWS, selected_comparison_view_records, _validate_sources
    from .canonical_results import reporting_config
    expected = list(reporting_config()['scenario_order'])
    supplied = supplied or {}
    directory = Path(supplied.get('comparison_dir', supplied.get('destination',
                     report.parents[1] / 'COMPARISONS_VIS_X1'))).resolve()
    presentation = Path(supplied.get('presentation_dir', directory / 'presentation')).resolve()
    reports = {scenario: report.parents[1] / scenario / 'REPORTING' for scenario in expected
               if (report.parents[1] / scenario / 'REPORTING/MEM_UC2_VIS_REPORTING_R1_RECEIPT.json').is_file()}
    for path in reports.values():
        _pin(path / 'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json', report, evidence)
    missing = [scenario for scenario in expected if scenario not in reports]
    current_version = None
    authority = report / 'uc2_VIS_RESULT_AUTHORITY_REGISTER.csv'
    if authority.is_file():
        r1 = inspection._read(report / 'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json')
        declarations = _registered_hashes(r1)
        if authority.name not in declarations:
            raise ValueError('Current case model authority is not receipt-bound')
        _pin(authority, report, evidence, declarations[authority.name])
        versions = set(pd.read_csv(authority).parent_model_version)
        if len(versions) != 1:
            raise ValueError('Current case has ambiguous model authority')
        current_version = next(iter(versions))
    if len(reports) >= 2:
        _validate_sources(year, reports, expected_model_version=current_version)
    records = selected_comparison_view_records(year, reports, directory, expected_scenarios=expected,
                                               model_version=current_version)
    rendered = {}
    base_path = directory / 'MEM_UC2_COMPARISON_RECEIPT.json'
    roots = [report, directory, presentation, *reports.values()]
    if base_path.is_file():
        q = _receipt(base_path, report, evidence, roots)
        if (q.get('status') != 'PASS' or q.get('year') != year or q.get('model_generation') != 'UC2'
                or q.get('physical_source') != 'UC_MILP' or q.get('price_source') != 'FIXED_COMMITMENT_PRICE_LP'
                or q.get('reporting_solver_invocations') != 0):
            raise ValueError('Invalid declared comparison authority or identity')
        scenarios = q.get('scenarios', [])
        if len(scenarios) < 2 or len(set(scenarios)) != len(scenarios) or set(scenarios).difference(reports):
            raise ValueError('Comparison declares missing or incompatible cases')
        actual_reports = {scenario: reports[scenario] for scenario in scenarios}
        if q.get('model_version') is not None and current_version is not None and q['model_version'] != current_version:
            raise ValueError('Comparison model version differs from current case')
        metadata, hashes = _validate_sources(year, actual_reports, expected_model_version=current_version or q.get('model_version'))
        if q.get('source_receipt_sha256') != hashes:
            raise ValueError('Comparison uses stale case receipts')
        manifest = _pin(directory / 'MEM_UC2_COMPARISON_MANIFEST.csv', report, evidence, q['manifest_sha256'])
        entries = pd.read_csv(manifest)
        if entries.artifact.duplicated().any():
            raise ValueError('Duplicate comparison manifest artifact')
        for item in entries.itertuples(index=False):
            _pin(directory / str(item.artifact).replace('\\', '/'), report, evidence, item.sha256)
        original_metadata = directory / 'MEM_UC2_COMPARISON_FIGURE_METADATA.json'
        if _relative(original_metadata, report) not in evidence:
            raise ValueError('Comparison metadata is not manifest-bound')
        original_entries = inspection._read(original_metadata)
        if (not isinstance(original_entries, list) or len(original_entries) != q.get('figure_count')
                or len({entry.get('figure_id') for entry in original_entries}) != len(original_entries)):
            raise ValueError('Malformed comparison figure declaration count')
        retained = {'monthly_zonal_price', 'price_duration', 'interface_net_energy_MWh'}
        for entry in original_entries:
            view = entry.get('figure_id')
            if view in retained:
                if entry.get('year') != year or entry.get('scenarios') != scenarios:
                    raise ValueError('Comparison metadata lineage mismatch')
                rendered[view] = (*_figure_files(entry, report, evidence, roots), scenarios)
        for metadata_name, receipt_name in (
                ('FIGURE_METADATA.json', 'PRESENTATION_RECEIPT.json'),
                ('SELECTION_FIGURE_METADATA.json', 'COMPARISON_SELECTION_RECEIPT.json')):
            mp, rp = presentation / metadata_name, presentation / receipt_name
            if mp.exists() != rp.exists():
                raise ValueError('Selected comparison metadata/receipt pair is incomplete')
            if not mp.is_file():
                continue
            parent = _receipt(rp, report, evidence, roots)
            if (parent.get('status') != 'PASS' or parent.get('year') != year
                    or parent.get('scenarios') != scenarios or parent.get('solver_invocations') != 0
                    or parent.get('network_open_or_extraction_calls') != 0
                    or parent.get('source_receipt_sha256') != inspection._sha(base_path)):
                raise ValueError('Stale or invalid selected comparison receipt')
            if _relative(mp, report) not in evidence:
                raise ValueError('Selected comparison metadata is not receipt-bound')
            selected_entries = inspection._read(mp)
            if not isinstance(selected_entries, list) or len(selected_entries) != parent.get('figure_count'):
                raise ValueError('Malformed selected comparison declaration count')
            for entry in selected_entries:
                view = entry.get('figure')
                if view not in {item[0] for item in SELECTED_COMPARISON_VIEWS} or view in rendered:
                    raise ValueError('Unexpected or duplicate selected comparison view')
                if entry.get('year') != year or entry.get('scenarios') != scenarios:
                    raise ValueError('Selected comparison metadata lineage mismatch')
                rendered[view] = (*_figure_files(entry, report, evidence, roots), scenarios)
    elif supplied.get('status') == 'PASS':
        raise ValueError('Declared comparison output lacks its receipt')
    elif any((presentation / name).exists() for name in ('FIGURE_METADATA.json', 'SELECTION_FIGURE_METADATA.json')):
        raise ValueError('Selected comparison output lacks base lineage')
    regime_dir = directory / 'marginal_regime'
    rp = regime_dir / 'MEM_MARGINAL_REGIME_COMPARISON_RECEIPT.json'
    mp = regime_dir / 'LEXICOGRAPHIC_FIGURE_METADATA.json'
    if rp.exists() or mp.exists():
        if not rp.is_file() or not mp.is_file():
            raise ValueError('Marginal comparison declaration is incomplete')
        regime_roots = [report, directory, regime_dir, *reports.values()]
        q = _receipt(rp, report, evidence, regime_roots)
        if (q.get('status') != 'PASS_CANONICAL_MARGINAL_REGIME_COMPARISON'
                or q.get('model_year') != year or q.get('scenarios') != expected
                or q.get('routine_activation') is not True or q.get('solver_invocations') != 0
                or q.get('network_opens') != 0 or missing):
            raise ValueError('Invalid or incomplete marginal comparison collection')
        from .marginal_regime import RECEIPT as regime_receipt
        source_paths = {_resolve(name, regime_roots).resolve() for name in q.get('source_hashes', {})}
        for scenario, case_report in reports.items():
            source = case_report / regime_receipt
            if source.resolve() not in source_paths:
                raise ValueError('Marginal comparison lacks a same-case regime source')
            producer = inspection._read(source)
            if (producer.get('status') != 'PASS_CANONICAL_MARGINAL_REGIME'
                    or (producer.get('model_year'), producer.get('scenario')) != (year, scenario)):
                raise ValueError('Marginal comparison uses incompatible regime evidence')
        if _relative(mp, report) not in evidence:
            raise ValueError('Marginal comparison metadata is not receipt-bound')
        values = inspection._read(mp)
        selected = [entry for entry in values if entry.get('figure') == 'national_comparison']
        if len(selected) != 1 or selected[0].get('scenario_order') != expected:
            raise ValueError('Marginal comparison metadata identity mismatch')
        rendered['marginal_regime_national_comparison'] = (
            *_figure_files(selected[0], report, evidence, [directory, regime_dir]), expected)
    elif (supplied.get('marginal_regime_comparison') or {}).get('status', '').startswith('PASS'):
        raise ValueError('Declared marginal comparison lacks its receipt')
    output = []
    for item in records:
        view = item['view_id']
        files, sources, scenarios = rendered.get(view, ([], item['source_files'] + item['missing_source_files'], list(reports)))
        status = ('GENERATED_PARTIAL_COLLECTION' if files and set(scenarios) != set(expected)
                  else 'GENERATED' if files else item['status'])
        if status == 'SOURCE_TABLES_AVAILABLE':
            status = 'WAITING_FOR_SELECTED_COMPARISON_RENDERING'
        row = _record(view, 'SELECTED_COMPARISON_FIGURE', item['source_authority'],
                      'Existing uc2_comparison presentation / marginal_regime.compare',
                      [_relative(Path(source), report) for source in sources] if not files else sources,
                      status, files=files, scenarios=scenarios, group=item['group'],
                      action='None' if status == 'GENERATED' else 'Complete compatible cases/evidence and render the existing selected view')
        row['missing_scenarios'] = [scenario for scenario in expected if scenario not in scenarios]
        output.append(row)
    complete = all(row['status'] == 'GENERATED' for row in output)
    state = 'COMPARISON_VISUALIZATIONS_PASS' if complete else 'PARTIAL_COMPARISON_COLLECTION' if rendered else 'WAITING_FOR_COMPARABLE_OUTPUTS'
    return output, state, dict(comparison_dir=_relative(directory, report), presentation_dir=_relative(presentation, report))


def build_output_register(report, year, scenario, *, regime=None, comparisons=None, persist=True):
    """Validate and project current output declarations; persist only when called."""
    report = Path(report).resolve()
    evidence = {}
    verified = inspection._verified_report(report)
    if (verified['year'], verified['scenario']) != (year, scenario):
        raise ValueError('Output register case identity mismatch')
    registry = inspection.figure_registry(report)
    r1 = inspection._read(report / 'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json')
    if (r1.get('status') != 'PASS' or (r1.get('year'), r1.get('scenario')) != (year, scenario)
            or r1.get('physical_source') != 'UC_MILP' or r1.get('price_source') != 'FIXED_COMMITMENT_PRICE_LP'
            or r1.get('reporting_solver_invocations') != 0):
        raise ValueError('Invalid core visualization source receipt')
    registered_sources = _registered_hashes(r1)
    for name in ('MEM_UC2_REPORTING_RECEIPT.json', 'MEM_UC2_VIS_REPORTING_R1_RECEIPT.json',
                 'VIS_X1/FIGURE_METADATA.json', 'VIS_X1/FIGURE_INDEX.csv',
                 'CANONICAL/MEM_CANONICAL_REPORTING_MANIFEST.csv'):
        if (report / name).is_file():
            if name in ('VIS_X1/FIGURE_METADATA.json', 'VIS_X1/FIGURE_INDEX.csv') and name not in registered_sources:
                raise ValueError('Core figure declarations are not receipt-bound: ' + name)
            _pin(report / name, report, evidence, registered_sources.get(name))
    for kind in ('REFERENCE', 'UC', 'PRICE'):
        _pin(report.parent / f'QA/VERIFY_{kind}.json', report, evidence)
    rows = []
    for declared in registry:
        for path in [*declared['sources'], *declared['formats'].values()]:
            _pin(path, report, evidence)
        for path in declared['formats'].values():
            _format_check(Path(path))
        if declared['figure_id'].startswith(('MARGINAL_REGIME/', 'DISPATCH_PRESENTATION/')):
            continue
        for path in [*declared['sources'], *declared['formats'].values()]:
            path = Path(path).resolve()
            if path.is_relative_to(report):
                name = path.relative_to(report).as_posix()
                if name not in registered_sources:
                    raise ValueError('Core figure source is not receipt-bound: ' + name)
                _pin(path, report, evidence, registered_sources[name])
        metadata = declared.get('metadata', {})
        authority = metadata.get('source_authority', {
            'physical': 'UC_MILP', 'prices_and_water_values': 'FIXED_COMMITMENT_PRICE_LP_DUALS'})
        status = 'GENERATED' if declared['applicability'] == 'APPLICABLE' else declared['applicability']
        rows.append(_record(declared['figure_id'], 'SINGLE_CASE_FIGURE', authority,
                            'inspection.figure_registry -> existing canonical / VIS_X1 producers',
                            [_relative(path, report) for path in declared['sources']], status,
                            files=[_relative(path, report) for path in declared['formats'].values()],
                            scenarios=[scenario], group=declared['category']))
    core_count = len(rows)
    if not core_count:
        raise ValueError('Output register has no core figure declarations')
    core_status = ('CORE_REPORTING_PASS' if all(row['status'] in ('GENERATED', 'NOT_APPLICABLE') for row in rows)
                   else 'CORE_REPORTING_OUTPUTS_WAITING')
    dispatch = _dispatch_records(report, year, scenario, evidence)
    rows.extend(dispatch)
    marginal = _regime_record(report, year, scenario, registry, regime, evidence)
    rows.append(marginal)
    same_case_evidence = set(evidence)
    comparison_rows, comparison_status, request = _comparison_records(report, year, comparisons, evidence)
    comparison_evidence_paths = sorted(set(evidence).difference(same_case_evidence))
    rows.extend(comparison_rows)
    from .economic_offer_stack import economic_stack_readiness
    optional = economic_stack_readiness(model_year=year, scenario=scenario)
    rows.append(_record('economic_equilibrium_stack', 'OPTIONAL_REVIEW_FIGURE',
                        'SAME_FIXED_COMMITMENT_LP_COMPLETE_RESOURCE_AND_DECOMPOSITION_EVIDENCE',
                        'economic_offer_stack.economic_stack_readiness; opt-in review renderer',
                        optional['missing_evidence'], optional['status'], required=False,
                        scenarios=[scenario], action='Optional: supply the complete supported economic evidence bundle; prototype remains inactive'))
    accepted = ('ACCEPTED_VISUALIZATIONS_PASS' if core_status == 'CORE_REPORTING_PASS'
                and marginal['status'] == 'GENERATED'
                and all(row['status'] == 'GENERATED' for row in dispatch if row['required']) else WAITING)
    payload = dict(schema_version=SCHEMA, year=year, scenario=scenario,
                   status=accepted if accepted == 'ACCEPTED_VISUALIZATIONS_PASS' and comparison_status == 'COMPARISON_VISUALIZATIONS_PASS' else WAITING,
                   core_reporting_status=core_status, accepted_visualizations_status=accepted,
                   comparison_status=comparison_status, core_figure_count=core_count,
                   evidence_gated_outputs_status=(WAITING if
                       marginal['status'] not in ('GENERATED', 'NOT_APPLICABLE')
                       or optional['status'] != 'READY_FOR_OPT_IN_REVIEW' else 'EVIDENCE_GATED_OUTPUTS_CLEAR'),
                   cross_scenario_comparisons_status=('CROSS_SCENARIO_COMPARISONS_PASS'
                       if comparison_status == 'COMPARISON_VISUALIZATIONS_PASS' else 'CROSS_SCENARIO_COMPARISONS_WAITING'),
                   selected_comparison_count=len(comparison_rows), rows=rows,
                   evidence_hashes=dict(sorted(evidence.items())), comparison_request=request,
                   comparison_evidence_paths=comparison_evidence_paths,
                   economic_optional=True, economic_routine_activation=False,
                   solver_invocations=0, network_opens=0)
    if persist:
        fields = list(rows[0])
        fields += sorted(set().union(*(row.keys() for row in rows)).difference(fields))
        with (report / CSV_NAME).open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: json.dumps(value, sort_keys=True) if isinstance(value, (list, dict)) else value
                                 for key, value in row.items()})
        payload['register_csv_sha256'] = inspection._sha(report / CSV_NAME)
        (report / JSON_NAME).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')
    return payload


def validate_output_register(report):
    """Read-only validation against current declarations and case availability."""
    report = Path(report).resolve()
    if not (report / JSON_NAME).exists() and not (report / CSV_NAME).exists():
        case = inspection._verified_report(report)
        if case.get('output_register_required'):
            raise ValueError('The declared consolidated output register is missing')
        preview = build_output_register(report, case['year'], case['scenario'], persist=False)
        return {**preview, 'register_status': 'READ_ONLY_PREVIEW_NO_PERSISTED_REGISTER'}
    if not (report / JSON_NAME).is_file() or not (report / CSV_NAME).is_file():
        raise ValueError('Output register CSV/JSON pair is incomplete')
    stored = inspection._read(report / JSON_NAME)
    if not isinstance(stored, dict) or stored.get('schema_version') != SCHEMA or inspection._sha(report / CSV_NAME) != stored.get('register_csv_sha256'):
        raise ValueError('Output register CSV is stale or malformed')
    optional = set()
    if (report / 'DISPATCH_PRESENTATION/DISPATCH_PRESENTATION_RECEIPT.json').is_file():
        from .dispatch_figures import validate_dispatch_figures
        dispatch = validate_dispatch_figures(report)
        if dispatch.get('status') == 'PASS_DISPATCH_PRESENTATION' and dispatch.get('diagnostic_status') != 'PASS':
            optional = set(dispatch.get('optional_artifacts', []))
    comparison_paths = set(stored.get('comparison_evidence_paths', []))
    # Rebuild first: only currently valid producer receipts, manifests, source
    # bindings and rendered formats may explain changed shared evidence.
    options = {key: report / value for key, value in stored['comparison_request'].items()}
    current = build_output_register(report, stored['year'], stored['scenario'], comparisons=options, persist=False)
    for name, digest in stored['evidence_hashes'].items():
        if name in optional:
            continue
        if not (report / name).is_file() or inspection._sha(report / name) != digest:
            if name in comparison_paths and name in current['comparison_evidence_paths']:
                continue
            raise ValueError('Output register evidence changed: ' + name)
    expected = {key: value for key, value in stored.items() if key != 'register_csv_sha256'}
    if current != expected:
        if optional:
            def without_optional(value):
                return {**value,
                        'rows': [row for row in value['rows'] if row['output_type'] != 'DIAGNOSTIC_FIGURE'],
                        'evidence_hashes': {name: digest for name, digest in value['evidence_hashes'].items()
                                            if name not in optional}}
            if without_optional(current) == without_optional(expected):
                return {**current, 'register_status': 'OPTIONAL_DIAGNOSTIC_REGISTER_REFRESH_REQUIRED'}
        # New sibling evidence changes the live collection, but never upgrades a
        # persisted collection receipt to complete without an explicit refresh.
        before = [row for row in expected['rows'] if row['output_type'] != 'SELECTED_COMPARISON_FIGURE']
        after = [row for row in current['rows'] if row['output_type'] != 'SELECTED_COMPARISON_FIGURE']
        if before == after and expected['core_reporting_status'] == current['core_reporting_status']:
            # A valid producer refresh is collection availability, not a new
            # completion receipt for this older case's derived index.
            return {**current, 'status': WAITING, 'comparison_status': 'WAITING_FOR_COMPARISON_REGISTER_REFRESH',
                    'cross_scenario_comparisons_status': 'CROSS_SCENARIO_COMPARISONS_WAITING',
                    'register_status': 'STALE_COMPARISON_COLLECTION_REFRESH_REQUIRED'}
        raise ValueError('Output register projection is stale; explicit report refresh required')
    return stored

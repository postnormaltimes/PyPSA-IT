"""Native economic primitives captured from plain arrays, with no optimizer."""
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from mem_model.reporting import price_evidence as capture
from mem_model.reporting.lp_resource_capture import enumerate_population


class Variable:
    def __init__(self, labels, solution, lower=-np.inf, upper=np.inf):
        self.labels = labels
        self.solution = xr.full_like(labels, 0., dtype=float) + solution
        self.lower = xr.full_like(labels, lower, dtype=float)
        self.upper = xr.full_like(labels, upper, dtype=float)

    def isel(self, **indexers):
        result = object.__new__(Variable)
        for field in ("labels", "solution", "lower", "upper"):
            setattr(result, field, getattr(self, field).isel(**indexers))
        return result


def constraint(labels, variables, coefficients, *, rhs=0., dual=0., sign="="):
    terms = np.asarray(variables).shape[-1]
    data = xr.Dataset({"labels": labels,
        "vars": ((*labels.dims, "_term"), np.asarray(variables, dtype=int)),
        "coeffs": ((*labels.dims, "_term"), np.broadcast_to(coefficients, (*labels.shape, terms)).copy()),
        "rhs": xr.full_like(labels, 0., dtype=float) + rhs,
        "dual": xr.full_like(labels, 0., dtype=float) + dual,
        "sign": xr.full_like(labels, sign, dtype="U2")})
    return SimpleNamespace(data=data)


@pytest.fixture(autouse=True)
def no_optimization(monkeypatch):
    from pypsa.optimization.optimize import OptimizationAccessor
    from linopy import Model
    def forbidden(*args, **kwargs):
        pytest.fail("Only saved native arrays are used")
    for method in ("__call__", "create_model", "solve_model"):
        monkeypatch.setattr(OptimizationAccessor, method, forbidden)
    monkeypatch.setattr(Model, "__init__", forbidden)


@pytest.fixture
def native_arrays():
    snapshots = pd.date_range("2019-01-01", periods=3, freq="h", name="snapshot")
    def labels(start, names):
        return xr.DataArray(np.arange(start, start + 3 * len(names)).reshape(3, len(names)),
            coords={"snapshot": snapshots, "name": names}, dims=("snapshot", "name"))
    gen = labels(0, ["TG", "P2X_NORD"])
    link = labels(6, ["NS"])
    energy = labels(9, ["B"])
    power = labels(12, ["B"])
    unused = labels(15, ["z"])
    variables = {"Generator-p": Variable(gen, [[5., 2.], [6., 1.], [4., 0.]]),
        "Link-p": Variable(link, [[0.], [2.], [1.]]),
        "Store-e": Variable(energy, 5.), "Store-p": Variable(power, 0.),
        "Auxiliary-z": Variable(unused, 0.)}
    bus = labels(20, ["NORD", "SUD", "BAT"])
    ids = np.stack([np.column_stack([gen[:, 0], gen[:, 1], link[:, 0]]),
                    np.column_stack([link[:, 0], [-1] * 3, [-1] * 3]),
                    np.column_stack([power[:, 0], [-1] * 3, [-1] * 3])], axis=1)
    rows = {"Bus-nodal_balance": constraint(bus, ids, [[1., -1., -1.], [1., 0., 0.], [1., 0., 0.]],
                    rhs=[[3., 0., 0.], [3., 2., 0.], [3., 1., 0.]], dual=[100., 100., 0.]),
        "Generator-fix-p-lower": constraint(labels(30, ["TG", "P2X_NORD"]), gen.to_numpy()[..., None], 1., sign=">="),
        "Generator-fix-p-upper": constraint(labels(36, ["TG", "P2X_NORD"]), gen.to_numpy()[..., None], 1., rhs=[10., 4.], sign="<="),
        "Link-fix-p-lower": constraint(labels(42, ["NS"]), link.to_numpy()[..., None], 1., rhs=-8., sign=">="),
        "Link-fix-p-upper": constraint(labels(45, ["NS"]), link.to_numpy()[..., None], 1., rhs=8., sign="<="),
        "Store-fix-e-lower": constraint(labels(48, ["B"]), energy.to_numpy()[..., None], 1., sign=">="),
        "Store-fix-e-upper": constraint(labels(51, ["B"]), energy.to_numpy()[..., None], 1., rhs=10., sign="<="),
        "Store-energy_balance": constraint(labels(54, ["B"]),
            np.stack([energy.to_numpy(), np.roll(energy.to_numpy(), 1, axis=0), power.to_numpy()], axis=-1), [-1., 1., -1.]),
        "Generator-extra-native-p": constraint(labels(57, ["TG"]), gen[:, :1].to_numpy()[..., None], 2., rhs=20., sign="<="),
        "Auxiliary-status-only": constraint(labels(60, ["z"]), unused.to_numpy()[..., None], 1.),
        "GlobalConstraint-P2X_ANNUAL_NORD": constraint(xr.DataArray(70), [1, 3, 5], 1., rhs=3., dual=100.)}
    objective = SimpleNamespace(is_linear=True, sense="min",
        vars=xr.DataArray([0, 2, 4], dims="_term"), coeffs=xr.DataArray([100., 100., 100.], dims="_term"))
    model = SimpleNamespace(status="ok", binaries=SimpleNamespace(nvars=0), integers=SimpleNamespace(nvars=0),
                            constraints=rows, variables=variables, objective=objective)
    n = SimpleNamespace(snapshots=snapshots, model=model,
        generators=pd.DataFrame({"bus": ["NORD", "NORD"], "carrier": ["gas_ccgt", "p2x_flexible_NORD"],
            "sign": [1., -1.], "p_nom": [10., 4.], "p_min_pu": [0., 0.], "p_max_pu": [1., 1.],
            "committable": [False, False], "marginal_cost": [50., 0.]}, index=["TG", "P2X_NORD"]),
        loads=pd.DataFrame({"bus": ["NORD", "SUD"], "sign": [-1., -1.], "p_set": [3., 0.]}, index=["L", "LS"]),
        links=pd.DataFrame({"bus0": ["NORD"], "bus1": ["SUD"], "carrier": ["interconnector"],
            "p_nom": [8.], "p_min_pu": [-1.], "p_max_pu": [1.], "efficiency": [1.], "marginal_cost": [0.]}, index=["NS"]),
        stores=pd.DataFrame({"bus": ["BAT"], "carrier": ["battery_energy"], "e_nom": [10.],
            "e_cyclic": [True], "e_initial": [0.], "sign": [1.], "standing_loss": [0.], "marginal_cost": [0.]}, index=["B"]),
        buses=pd.DataFrame({"carrier": ["AC", "AC", "battery_energy"]}, index=["NORD", "SUD", "BAT"]),
        global_constraints=pd.DataFrame({"type": ["operational_limit"], "sense": ["=="], "constant": [3.],
            "carrier_attribute": ["p2x_flexible_NORD"]}, index=["P2X_ANNUAL_NORD"]),
        snapshot_weightings=pd.DataFrame({"objective": 2., "generators": 1., "stores": 1.}, index=snapshots))
    n.generators_t = {"p": variables["Generator-p"].solution.to_pandas().copy()}
    n.links_t = {"p0": variables["Link-p"].solution.to_pandas().copy()}
    n.stores_t = {"p": variables["Store-p"].solution.to_pandas().copy()}
    loads = pd.DataFrame({"L": [3., 3., 3.], "LS": [0., 2., 1.]}, index=snapshots)
    n.loads_t = {"p": loads, "p_set": loads}
    provenance = {"year": 2050, "scenario": "Slow", "kind": "FIXED_COMMITMENT_PRICE_LP",
        "job_id": "final_v4_2050_Slow_FIXED_COMMITMENT_PRICE_LP", "result_branch": "final_v4",
        "input_sha256": "1" * 64, "fixed_commitment_milp_sha256": "2" * 64}
    return n, provenance


def test_complete_native_identity_incidence_and_objective_are_recoverable(native_arrays, tmp_path):
    n, provenance = native_arrays
    evidence = capture.capture_price_evidence(n, tmp_path, provenance, extended=True, chunk_hours=1)
    evidence = capture.bind_price_evidence(tmp_path, evidence, "3" * 64)
    manifest = json.loads((tmp_path / "RAW_PRICE_CONSTRAINT_EVIDENCE.json").read_text())
    assert manifest["schema_version"].endswith("V2")
    assert "Generator-extra-native-p" in {x["constraint"] for x in manifest["constraints"]}
    assert manifest["excluded_constraint_families"] == ["Auxiliary-status-only"]
    assert manifest["additional_tables"]["electrical_population"]["includes_zero_dispatch"]
    rows = pd.read_parquet(evidence["table_path"])
    terms = pd.read_parquet(tmp_path / "RAW_PRICE_TERM_EVIDENCE.parquet")
    variables = pd.read_parquet(tmp_path / "RAW_PRICE_VARIABLE_EVIDENCE.parquet").set_index("variable_label")
    matched = terms.merge(rows[["constraint_label", "raw_dual"]], on="constraint_label", validate="many_to_one")
    contribution = (matched.coefficient * matched.raw_dual).groupby(matched.variable_label).sum()
    scoped = variables.loc[variables.variable_name.isin(["Generator-p", "Link-p", "Store-p", "Store-e"])]
    np.testing.assert_allclose(scoped.objective_coefficient, contribution.reindex(scoped.index, fill_value=0.))
    assert variables.loc[0, "objective_coefficient"] == 100.
    assert variables.loc[1, "objective_coefficient"] == 0.
    assert all(item['max_absolute_stationarity_residual'] == 0
               for item in manifest['stationarity_diagnostics'].values())
    receipt = {**provenance, "solved_sha256": "3" * 64, "final_methodology_variant": "final_v4"}
    assert capture.verify_price_evidence(tmp_path, evidence, receipt)["resource_capture"] == "COMPLETE_NATIVE_PRIMITIVES"


def test_population_contains_zero_dispatch_fixed_load_and_both_link_terminals(native_arrays, tmp_path):
    n, provenance = native_arrays
    capture.capture_price_evidence(n, tmp_path, provenance, extended=True)
    rows = pd.read_parquet(tmp_path / "RAW_PRICE_ELECTRICAL_POPULATION.parquet")
    assert len(rows) == len(enumerate_population(n)) * 3 == 18
    assert rows.loc[rows.asset_id.eq("P2X_NORD"), "electrical_injection_MW"].tolist() == [-2., -1., 0.]
    assert rows.loc[rows.asset_id.eq("NS") & rows.terminal.eq("bus0"), "electrical_injection_MW"].tolist() == [0., -2., -1.]
    assert rows.loc[rows.asset_id.eq("NS") & rows.terminal.eq("bus1"), "electrical_injection_MW"].tolist() == [0., 2., 1.]
    assert rows.groupby(["snapshot", "zone"]).electrical_injection_MW.sum().eq(0).all()
    assert rows.loc[rows.component.eq("Load"), "marginal_cost_native"].isna().all()
    assert rows.objective_weight.eq(2).all()
    assert rows.generator_weight.eq(1).all()
    assert rows.loc[rows.asset_id.eq("NS"), "native_operating_lower"].eq(-8).all()


@pytest.mark.parametrize("defect,code", [
    ("objective", "LINEAR_MIN"), ("identity", "MISSING_LINEAGE"),
    ("duplicates", "DUPLICATE_VARIABLE"), ("coefficient", "NONFINITE"),
    ("weights", "INVALID_WEIGHTS"), ("chronology", "RESOURCE_CHRONOLOGY"),
    ("primal", "RESOURCE_PRIMAL_MISMATCH"), ("missing_primal", "RESOURCE_PRIMAL_MISSING"),
])
def test_rejects_incomplete_native_capture(native_arrays, tmp_path, defect, code):
    n, provenance = native_arrays
    if defect == "objective": n.model.objective.is_linear = False
    elif defect == "identity": provenance.pop("job_id")
    elif defect == "duplicates": n.model.variables["Link-p"].labels[0, 0] = 0
    elif defect == "coefficient": n.model.constraints["Generator-extra-native-p"].data.coeffs[0, 0, 0] = np.nan
    elif defect == "weights": n.snapshot_weightings.loc[n.snapshots[0], "objective"] = 0
    elif defect == "primal": n.links_t['p0'].iloc[0, 0] = 999.
    elif defect == "missing_primal": n.links_t.pop('p0')
    else: n.loads_t["p"].index = n.loads_t["p"].index + pd.Timedelta(hours=1)
    with pytest.raises(RuntimeError, match=code):
        capture.capture_price_evidence(n, tmp_path, provenance, extended=True)


def test_binding_rejects_different_solve_and_modified_sidecar(native_arrays, tmp_path):
    n, provenance = native_arrays
    evidence = capture.capture_price_evidence(n, tmp_path, provenance, extended=True)
    evidence = capture.bind_price_evidence(tmp_path, evidence, "3" * 64)
    receipt = {**provenance, "solved_sha256": "4" * 64, "final_methodology_variant": "final_v4"}
    with pytest.raises(RuntimeError, match="LINEAGE_MISMATCH"):
        capture.verify_price_evidence(tmp_path, evidence, receipt)
    receipt["solved_sha256"] = "3" * 64
    with (tmp_path / "RAW_PRICE_VARIABLE_EVIDENCE.parquet").open("ab") as stream: stream.write(b"changed")
    with pytest.raises(RuntimeError, match="SIDECAR_CONFLICT"):
        capture.verify_price_evidence(tmp_path, evidence, receipt)


def test_report_source_protection_includes_every_native_capture_file(native_arrays, tmp_path, monkeypatch):
    from mem_model import stage_b_uc2 as uc2
    from mem_model.reporting import uc2_postprocess as post
    from mem_model.common import sha256_file
    n, provenance = native_arrays
    evidence = capture.capture_price_evidence(n, tmp_path, provenance, extended=True)
    evidence = capture.bind_price_evidence(tmp_path, evidence, '3' * 64)
    receipt = {**provenance, 'solved_sha256': '3' * 64,
               'final_methodology_variant': 'final_v4', 'price_constraint_evidence': evidence}
    paths = {}
    for kind in uc2.RUN_TYPES:
        files = {k: tmp_path / f'{kind}_{k}' for k in ('solved', 'input', 'receipt', 'verification')}
        for file in files.values(): file.write_text('{}')
        paths[kind] = {**files, 'directory': tmp_path}
    paths['FIXED_COMMITMENT_PRICE_LP']['receipt'].write_text(json.dumps(receipt))
    monkeypatch.setattr(uc2, 'job_paths', lambda y, s, k: paths[k])
    pins = post._protected_sources(2050, 'Slow')
    for name in capture.CAPTURE_FILES:
        assert pins[str(tmp_path / name)] == sha256_file(tmp_path / name)
    with (tmp_path / 'RAW_PRICE_TERM_EVIDENCE.parquet').open('ab') as stream:
        stream.write(b'modified')
    with pytest.raises(RuntimeError, match='SIDECAR_CONFLICT'):
        post._protected_sources(2050, 'Slow')


def test_actual_price_postsolve_tail_persists_live_model_evidence_and_receipt(native_arrays, tmp_path, monkeypatch):
    """Execute the current persistence branch using native arrays, never a solver."""
    import ast
    import inspect
    import textwrap
    import pypsa
    from mem_model import stage_b_uc2 as uc2
    from mem_model.common import sha256_file

    arrays, _ = native_arrays
    n = pypsa.Network()
    n.set_snapshots(arrays.snapshots)
    for component, frame in (("Bus", arrays.buses), ("Generator", arrays.generators),
                             ("Load", arrays.loads), ("Link", arrays.links),
                             ("Store", arrays.stores), ("GlobalConstraint", arrays.global_constraints)):
        n.add(component, frame.index, **{column: frame[column] for column in frame})
    n.buses.at["BAT", "carrier"] = "water_energy"
    n.stores.at["B", "carrier"] = "water_energy"
    n.links.at["NS", "carrier"] = "internal_transfer"
    n.snapshot_weightings = arrays.snapshot_weightings.copy()
    for component in ("generators", "loads", "links", "stores"):
        for key, frame in getattr(arrays, component + "_t").items():
            getattr(n, component + "_t")[key] = frame.copy()
    n.stores_t.e = arrays.model.variables["Store-e"].solution.to_pandas()
    n.meta["network_v2b_corridors"] = [{"signed_link": "NS", "carrier": "internal_transfer",
        "bus_a": "NORD", "bus_b": "SUD", "forward_link": "old_NS", "reverse_link": "old_SN"}]
    water = arrays.model.constraints["Store-energy_balance"]
    water.dual, water.labels = water.data.dual, water.data.labels
    backend = object()
    class AttachedModel(SimpleNamespace):
        @property
        def solver_model(self):
            return backend
    model = AttachedModel(**vars(arrays.model), nvars=18, ncons=43)
    n._model = model
    n._objective = 1500.
    monkeypatch.setattr(n, "copy", lambda *_: pytest.fail("The attached network cannot be copied"))
    monkeypatch.setattr(n, "remove", lambda *_: pytest.fail("Post-solve extraction cannot remove components"))
    monkeypatch.setattr(uc2, "ROOT", tmp_path)
    mapping = pd.DataFrame([{"store_id": "B", "state_id": "B", "zone": "NORD", "hydro_class": "MIXED_PHS"}])
    def final_api(name, year, scenario):
        assert (name, year, scenario) == ("water_mapping", 2040, "High")
        return mapping
    monkeypatch.setattr(uc2, "_final_api", final_api)
    paths = {"directory": tmp_path / "PRICE", "solved": tmp_path / "PRICE/SOLVED.nc",
             "receipt": tmp_path / "PRICE/SOLVE_RECEIPT.json", "input": tmp_path / "INPUT.nc"}
    # Compile only the existing branch after solve_model returns. All earlier
    # model construction and optimization statements are excluded explicitly.
    original = ast.parse(textwrap.dedent(inspect.getsource(uc2._manual_solve))).body[0]
    start = next(i for i, statement in enumerate(original.body)
                 if isinstance(statement, ast.Assign) and any(
                     isinstance(target, ast.Name) and target.id == "duration" for target in statement.targets))
    signature = "network, model, paths, solver, year, scenario, kind, expected_input_sha, fixed_milp_sha, benchmark_variant, names, binaries, integers, started, status, termination"
    function = ast.parse(f"def post_solve({signature}): pass").body[0]
    function.body = original.body[start:]
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    namespace = dict(vars(uc2))
    exec(compile(module, inspect.getsourcefile(uc2._manual_solve), "exec"), namespace)
    with uc2.execution_variant("final_v4"):
        receipt = namespace["post_solve"](n, model, paths,
            {"required_version": "13.0.3", "options": {"Threads": 1, "Seed": 0}},
            2040, "High", "FIXED_COMMITMENT_PRICE_LP", "1" * 64, "2" * 64,
            None, [], 0, 0, uc2.perf_counter(), "ok", "optimal")
    assert n.model is model and model.solver_model is backend
    assert receipt["status"] == "PASS" and receipt["automatic_successor_launched"] is False
    assert receipt["solved_sha256"] == sha256_file(paths["solved"])
    assert receipt == json.loads(paths["receipt"].read_text())
    coverage = receipt["water_dual_raw_coverage"]
    assert sha256_file(tmp_path / coverage["path"]) == coverage["sha256"]
    assert len(pd.read_parquet(tmp_path / coverage["path"])) == 3
    assert capture.verify_price_evidence(paths["directory"], receipt["price_constraint_evidence"], receipt)[
        "resource_capture"] == "COMPLETE_NATIVE_PRIMITIVES"
    manifest = json.loads((paths["directory"] / "RAW_PRICE_CONSTRAINT_EVIDENCE.json").read_text())
    assert manifest["supporting_inputs"]["signed_interface_registry"][0]["signed_link"] == "NS"
    reloaded = pypsa.Network(paths["solved"])
    pd.testing.assert_frame_equal(reloaded.links_t.p0, n.links_t.p0, check_freq=False)

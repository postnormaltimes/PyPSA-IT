"""Focused isolation, parity and continuous-bound tests; no model/solver calls."""
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa
import pytest

from mem_model import portable_rebuild as portable
from mem_model.stage_b_zonal_vre import no_models_or_solves


@pytest.fixture(autouse=True)
def prohibit_models_and_solvers():
    with no_models_or_solves():
        yield


@pytest.fixture
def small_network():
    network = pypsa.Network()
    network.set_snapshots(pd.date_range("2019-01-01", periods=3, freq="h"))
    network.add("Bus", "NORD")
    network.add("Carrier", "methane_ccgt")
    network.add("Generator", "UC__child", bus="NORD", carrier="methane_ccgt", p_nom=123.,
                efficiency=.54, marginal_cost=95., committable=True, p_min_pu=.45,
                ramp_limit_up=1., min_up_time=4, start_up_cost=7380.)
    network.generators_t.p_max_pu["UC__child"] = [.8, .9, 1.]
    return network


def test_isolation_allows_candidate_and_staging_and_blocks_original(tmp_path):
    workspace = tmp_path / "old_workspace"
    candidate, staging, original = (workspace / name for name in ("candidate", "staging", "original"))
    for directory in (candidate, staging, original):
        directory.mkdir(parents=True)
        (directory / "control.csv").write_text("value\n1\n")
    with portable.isolated_filesystem(staging, candidate_root=candidate, workspace_root=workspace) as policy:
        assert (candidate / "control.csv").read_text().endswith("1\n")
        assert (staging / "control.csv").read_text().endswith("1\n")
        with pytest.raises(RuntimeError, match="ORIGINAL_WORKSPACE_ACCESS_FORBIDDEN"):
            (original / "control.csv").read_text()
        assert len(policy["blocked_accesses"]) == 1
    # Audit installation is permanent, while enforcement is context-local.
    assert (original / "control.csv").read_text().endswith("1\n")


@pytest.mark.parametrize("change", ["static", "hourly", "constraint", "extra_component", "metadata"])
def test_parity_detects_affected_model_semantics(small_network, change):
    changed = small_network.copy()
    if change == "static":
        changed.generators.at["UC__child", "ramp_limit_up"] = .75
    elif change == "hourly":
        changed.generators_t.p_max_pu.iloc[1, 0] = .91
    elif change == "constraint":
        changed.add("GlobalConstraint", "P2X_ANNUAL_NORD", type="operational_limit", sense="==", constant=123.)
    elif change == "extra_component":
        changed.add("Line", "extra", bus0="NORD", bus1="NORD", x=.1)
    else:
        changed.meta["external_auxiliary_objective_baseline_EUR"] = 1.
    with pytest.raises((AssertionError, RuntimeError)):
        portable.compare_networks(small_network, changed)


def test_path_provenance_is_separate_and_model_identity_is_stable(small_network):
    changed = small_network.copy()
    changed.meta.update({"runtime_bundle": "a/portable/path", "runtime_manifest_sha256": "a" * 64,
                         "portable_rebuild": portable.VERSION})
    assert portable.compare_networks(small_network, changed)["status"] == "PASS"
    assert portable.model_identity(small_network) == portable.model_identity(changed)


def test_continuous_reference_restores_all_fields_from_own_p2x_parent(small_network):
    parent = pypsa.Network()
    parent.set_snapshots(small_network.snapshots)
    parent.add("Bus", "NORD")
    parent.add("Generator", "parent", bus="NORD", p_nom=500., efficiency=.6, marginal_cost=80.,
               p_min_pu=.03, ramp_limit_up=.71, ramp_limit_down=.61, up_time_before=8)
    small_network.generators_t.p_min_pu["UC__child"] = [.45, .45, .45]
    parent.generators_t.p_min_pu["parent"] = [.02, .03, .04]
    reference = portable.final.continuous_counterfactual(small_network, parent,
                    pd.DataFrame({"child_id": ["UC__child"], "parent_id": ["parent"]}))
    for field in portable.final.CONTINUOUS_FIELDS:
        expected, observed = parent.generators.at["parent", field], reference.generators.at["UC__child", field]
        assert (pd.isna(expected) and pd.isna(observed)) or expected == observed
    np.testing.assert_array_equal(reference.generators_t.p_min_pu["UC__child"], parent.generators_t.p_min_pu["parent"])
    for field in ("p_nom", "efficiency", "marginal_cost"):
        assert reference.generators.at["UC__child", field] == small_network.generators.at["UC__child", field]
    assert small_network.generators.at["UC__child", "committable"]


def test_crosswalk_reads_full_precision_and_rejects_duplicate_child_ids(tmp_path):
    path = tmp_path / "crosswalk.csv"
    columns = ["year", "scenario", "parent_id", "v1_child_id", "child_id", *portable.uc2a.OVERRIDE_FIELDS]
    value = float.fromhex("0x1.999999999999ap-4")
    frame = pd.DataFrame([[2040, "Base", "parent", "v1", "v2", value, .5, 80., 0., 0., 0.]], columns=columns)
    frame.to_csv(path, index=False, float_format="%.17g")
    assert portable.load_crosswalk(path).p_nom.iloc[0].hex() == value.hex()
    pd.concat([frame, frame]).to_csv(path, index=False)
    with pytest.raises(RuntimeError, match="CROSSWALK_FAIL"):
        portable.load_crosswalk(path)


def test_p2x_corrects_rigid_energy_and_shedding_capacity_without_optimization(tmp_path):
    network = pypsa.Network()
    network.set_snapshots(pd.date_range("2019-01-01", periods=8760, freq="h"))
    controls = pd.read_csv(portable.STATIC / "MEM_Annual_Zonal_Demand_Contract.csv")
    controls = portable._selected_case(controls, 2040, "Base").set_index("zone")
    for zone in portable.ZONES:
        network.add("Bus", zone)
        network.add("Load", f"LOAD_{zone}", bus=zone)
        network.loads_t.p_set[f"LOAD_{zone}"] = float(controls.at[zone, "annual_zonal_demand_MWh"]) / 8760
        network.add("Generator", f"LOAD_SHEDDING_{zone}", bus=zone, p_nom=999999., marginal_cost=15000.)
    corrected = portable.apply_p2x(network, 2040, "Base", tmp_path)
    assert float(corrected.loads_t.p_set.sum().sum()) == pytest.approx(411.5e6, abs=1e-6)
    assert float(corrected.global_constraints.constant.sum()) == pytest.approx(27.5e6, abs=1e-6)
    assert float(corrected.generators.loc[[f"P2X_{z}" for z in portable.ZONES], "p_nom"].sum()) == pytest.approx(7400.)
    assert corrected.global_constraints.sense.eq("==").all()
    for zone in portable.ZONES:
        assert corrected.generators.at[f"P2X_{zone}", "sign"] == -1.
        assert corrected.generators.at[f"LOAD_SHEDDING_{zone}", "p_nom"] == corrected.loads_t.p_set[f"LOAD_{zone}"].max()
    assert network.generators.at["LOAD_SHEDDING_NORD", "p_nom"] == 999999.
    assert network.global_constraints.empty


def test_output_boundary_protects_accepted_networks():
    with pytest.raises(ValueError, match="OUTPUT_MUST_BE_UNDER"):
        portable._output_root(portable.ROOT / "networks/unsolved/final_methodology_v1")


def test_guard_forbids_even_model_construction(small_network):
    with pytest.raises(RuntimeError, match="MODEL_OR_SOLVER_FORBIDDEN"):
        small_network.optimize.create_model()

"""ETX-7B4 validation without topology, PyPSA, or optimization."""

from __future__ import annotations

import argparse
import json
import math
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from mem_model.stage_a.operating_base import (
    CONFIG_PATH,
    DEFAULT_OUTPUT_DIR,
    HORIZONS,
    MARKETS,
    OUTPUT_FILES,
    ROOT,
    build_operating_base_package,
    load_config,
    sha256_file,
)


QA_DIR = ROOT / "qa" / "stage_a" / "etx7b4"


def _read(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _rows(path: Path) -> int:
    return len(_read(path))


def _add(
    rows: list[dict[str, Any]],
    check_id: str,
    description: str,
    observed: object,
    expected: object,
    passed: bool,
    notes: str = "",
) -> None:
    difference: object = ""
    try:
        difference = float(observed) - float(expected)
    except (TypeError, ValueError):
        pass
    rows.append({
        "check_id": check_id,
        "description": description,
        "observed": observed,
        "expected": expected,
        "difference": difference,
        "status": "PASS" if bool(passed) else "FAIL",
        "notes": notes,
    })


def _verify_manifest(manifest_path: Path, family: str) -> pd.DataFrame:
    manifest = pd.read_csv(manifest_path, dtype=str, keep_default_na=False)
    rows: list[dict[str, Any]] = []
    for record in manifest.to_dict(orient="records"):
        relative = Path(record["relative_path"].replace("\\", "/"))
        root_candidate = ROOT / relative
        path = root_candidate if root_candidate.exists() else manifest_path.parent / relative
        expected_hash = record.get("expected_sha256") or record.get("sha256")
        expected_rows = record.get("expected_rows") or record.get("rows")
        expected_bytes = record.get("bytes", "")
        exists = path.exists()
        observed_hash = sha256_file(path) if exists else ""
        observed_rows = _rows(path) if exists else ""
        observed_bytes = path.stat().st_size if exists else ""
        passed = (
            exists
            and observed_hash.upper() == str(expected_hash).upper()
            and int(observed_rows) == int(expected_rows)
            and (not expected_bytes or int(observed_bytes) == int(expected_bytes))
        )
        rows.append({
            "receipt_family": family,
            "artifact": record.get("artifact", path.name),
            "relative_path": relative.as_posix(),
            "expected_bytes": expected_bytes,
            "observed_bytes": observed_bytes,
            "expected_rows": expected_rows,
            "observed_rows": observed_rows,
            "expected_sha256": str(expected_hash).upper(),
            "observed_sha256": observed_hash,
            "status": "PASS" if passed else "FAIL",
        })
    return pd.DataFrame.from_records(rows)


def frozen_receipts() -> pd.DataFrame:
    items = [
        (ROOT / "qa/interim/MEM_FROZEN_STATIC_HASH_MANIFEST.csv", "FROZEN_STAGE_B"),
        (ROOT / "stage_a_inputs/etx7a_v1_0/static/MEM_ETX7A_Static_Output_Manifest_v1.0.csv", "FROZEN_ETX7B1"),
        (ROOT / "stage_a_inputs/italy_base_v1_0/static/MEM_ETX7B2_IT_Static_Output_Manifest_v1.0.csv", "FROZEN_ETX7B2"),
        (ROOT / "stage_a_inputs/temporal_2019_v1_0/MEM_ETX7B3_Temporal_Output_Manifest_v1.0.csv", "FROZEN_ETX7B3"),
    ]
    return pd.concat([_verify_manifest(path, family) for path, family in items], ignore_index=True)


def output_receipts(output_dir: Path) -> pd.DataFrame:
    manifest = pd.read_csv(output_dir / OUTPUT_FILES["manifest"], dtype=str, keep_default_na=False)
    rows: list[dict[str, Any]] = []
    for record in manifest.to_dict(orient="records"):
        path = ROOT / Path(record["relative_path"])
        rows.append({
            "artifact": record["artifact"],
            "relative_path": record["relative_path"],
            "expected_bytes": record["bytes"],
            "observed_bytes": path.stat().st_size if path.exists() else "",
            "expected_rows": record["rows"],
            "observed_rows": _rows(path) if path.exists() else "",
            "expected_sha256": record["sha256"],
            "observed_sha256": sha256_file(path) if path.exists() else "",
            "status": "PASS" if path.exists() and path.stat().st_size == int(record["bytes"]) and _rows(path) == int(record["rows"]) and sha256_file(path) == record["sha256"] else "FAIL",
        })
    return pd.DataFrame.from_records(rows)


def deterministic_receipt(output_dir: Path) -> pd.DataFrame:
    with tempfile.TemporaryDirectory(prefix="etx7b4_rebuild_") as temporary:
        rebuilt = Path(temporary) / "operating_base"
        build_operating_base_package(rebuilt)
        rows = []
        for filename in OUTPUT_FILES.values():
            accepted = output_dir / filename
            candidate = rebuilt / filename
            same = accepted.read_bytes() == candidate.read_bytes()
            rows.append({"artifact": filename, "accepted_sha256": sha256_file(accepted), "rebuilt_sha256": sha256_file(candidate), "byte_identical": same, "status": "PASS" if same else "FAIL"})
    return pd.DataFrame.from_records(rows)


def _pytest_receipt() -> dict[str, Any]:
    path = QA_DIR / "pytest_full_repository.xml"
    if not path.exists():
        return {"status": "NOT_RUN", "path": str(path.relative_to(ROOT)).replace("\\", "/")}
    suite = ET.parse(path).getroot().find("testsuite")
    if suite is None:
        return {"status": "INVALID_JUNIT", "path": str(path.relative_to(ROOT)).replace("\\", "/")}
    tests = int(suite.attrib.get("tests", 0))
    failures = int(suite.attrib.get("failures", 0))
    errors = int(suite.attrib.get("errors", 0))
    return {
        "status": "PASS" if failures == 0 and errors == 0 else "FAIL",
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": int(suite.attrib.get("skipped", 0)),
        "seconds": float(suite.attrib.get("time", 0)),
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "sha256": sha256_file(path),
    }


def validate_operating_base_package(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    write_reports: bool = True,
    run_deterministic_rebuild: bool = True,
) -> dict[str, Any]:
    config = load_config(CONFIG_PATH)
    g = _read(output_dir / OUTPUT_FILES["generators"])
    a = pd.read_parquet(output_dir / OUTPUT_FILES["availability"])
    s = _read(output_dir / OUTPUT_FILES["storage"])
    phs = _read(output_dir / OUTPUT_FILES["italy_phs"])
    h = _read(output_dir / OUTPUT_FILES["hydro"])
    inflow = _read(output_dir / OUTPUT_FILES["hydro_inflow"])
    constraints = _read(output_dir / OUTPUT_FILES["constraints"])
    inheritance = _read(output_dir / OUTPUT_FILES["inheritance"])
    erratum = _read(output_dir / OUTPUT_FILES["b3_erratum"])
    frozen = frozen_receipts()
    outputs = output_receipts(output_dir)
    deterministic = deterministic_receipt(output_dir) if run_deterministic_rebuild else pd.DataFrame([{"artifact": "SKIPPED", "status": "SKIPPED"}])
    checks: list[dict[str, Any]] = []

    _add(checks, "B4-QA-01", "All Stage-B, B1, B2 and B3 frozen receipts remain unchanged", int(frozen["status"].eq("PASS").sum()), len(frozen), frozen["status"].eq("PASS").all())
    _add(checks, "B4-QA-02", "Exactly 165 positive Stage-A generator records are mapped", len(g), 165, len(g) == 165)
    _add(checks, "B4-QA-03", "Generator scope is exactly ten markets, Base and two horizons", f"markets={sorted(g.country_code.unique())}; years={sorted(g.year.astype(int).unique())}; scenarios={sorted(g.scenario.unique())}", "10 markets; 2040/2050; Base", set(g.country_code) == set(MARKETS) and set(g.year.astype(int)) == set(HORIZONS) and set(g.scenario) == {"Base"})
    _add(checks, "B4-QA-04", "Every generator asset ID is unique and mapped exactly once", int(g.asset_id.nunique()), len(g), g.asset_id.is_unique)
    _add(checks, "B4-QA-05", "No generator capacity is extendable", g.p_nom_extendable.astype(str).str.lower().eq("false").sum(), len(g), g.p_nom_extendable.astype(str).str.lower().eq("false").all())
    _add(checks, "B4-QA-06", "Every positive generator has complete runtime disposition", g.runtime_status.eq("ETX7B4_OPERATING_PARAMETERS_COMPLETE").sum(), len(g), g.runtime_status.eq("ETX7B4_OPERATING_PARAMETERS_COMPLETE").all())

    b1 = _read(ROOT / "stage_a_inputs/etx7a_v1_0/static/MEM_ETX7A_Generators_Static_v1.0.csv")
    b2 = _read(ROOT / "stage_a_inputs/italy_base_v1_0/static/MEM_ETX7B2_IT_Generators_Static_v1.0.csv")
    source_capacity = pd.concat([b1[["asset_id", "p_nom_MW"]], b2[["asset_id", "p_nom_MW"]]], ignore_index=True).set_index("asset_id")["p_nom_MW"].astype(float)
    mapped_capacity = g.set_index("asset_id")["p_nom_MW"].astype(float)
    capacity_diff = (mapped_capacity - source_capacity.reindex(mapped_capacity.index)).abs().max()
    _add(checks, "B4-QA-07", "Generator capacities equal the frozen B1/B2 contracts asset by asset", capacity_diff, 0.0, capacity_diff <= 1e-9)
    external_identity = g.loc[g.static_source_family.eq("ETX7B1_EXTERNAL")].set_index("asset_id")["source_carrier"]
    expected_identity = b1.set_index("asset_id")["mapped_carrier"]
    _add(checks, "B4-QA-08", "External ETX-7A carrier identity is preserved without substitution", int((external_identity == expected_identity.reindex(external_identity.index)).sum()), len(external_identity), (external_identity == expected_identity.reindex(external_identity.index)).all())

    numeric = ["efficiency_el", "VOM_EUR2025_per_MWh_el", "fuel_price_EUR2025_per_MWh_th", "chargeable_CO2_t_per_MWh_th", "CO2_price_EUR2025_per_t", "other_variable_cost_EUR2025_per_MWh_el", "marginal_cost_EUR2025_per_MWh_el"]
    for column in numeric:
        g[column] = pd.to_numeric(g[column], errors="coerce")
    reconstructed = np.where(
        g.efficiency_el.notna(),
        g.fuel_price_EUR2025_per_MWh_th.fillna(0) / g.efficiency_el + g.VOM_EUR2025_per_MWh_el + g.CO2_price_EUR2025_per_t * g.chargeable_CO2_t_per_MWh_th / g.efficiency_el + g.other_variable_cost_EUR2025_per_MWh_el,
        g.VOM_EUR2025_per_MWh_el + g.other_variable_cost_EUR2025_per_MWh_el,
    )
    mc_diff = np.max(np.abs(reconstructed - g.marginal_cost_EUR2025_per_MWh_el.to_numpy(float)))
    _add(checks, "B4-QA-09", "Marginal cost reconstructs from one fuel, efficiency, VOM, carbon and other-variable-cost application", mc_diff, 0.0, mc_diff <= 1e-9)
    expected_carbon = g.year.astype(int).map({2040: 104.5504, 2050: 400.0}).to_numpy(float)
    carbon_diff = np.max(np.abs(g.CO2_price_EUR2025_per_t.to_numpy(float) - expected_carbon))
    _add(checks, "B4-QA-10", "Common Stage-A horizon carbon values apply across all ten markets", carbon_diff, 0.0, carbon_diff <= 1e-12 and g.groupby("year").CO2_price_EUR2025_per_t.nunique().eq(1).all())
    no_capex_fom = g.CAPEX_in_marginal_cost.astype(str).str.lower().eq("false").all() and g.fixed_OPEX_in_marginal_cost.astype(str).str.lower().eq("false").all()
    _add(checks, "B4-QA-11", "CAPEX and fixed OPEX are excluded from dispatch marginal cost", no_capex_fom, True, no_capex_fom)
    no_border_carbon = "border" not in "|".join(g.columns).lower()
    _add(checks, "B4-QA-12", "No border-carbon surcharge is added", no_border_carbon, True, no_border_carbon)

    scheduled_ids = set(g.loc[~g.availability_mode.eq("B3_TEMPORAL_PROFILE"), "asset_id"])
    group_sizes = a.groupby("asset_id").size()
    _add(checks, "B4-QA-13", "Every B4-owned technical-availability schedule has exactly 8,760 hours", f"assets={len(group_sizes)}; min={group_sizes.min()}; max={group_sizes.max()}", f"{len(scheduled_ids)} assets x 8760", set(group_sizes.index) == scheduled_ids and group_sizes.eq(8760).all())
    snapshots = pd.read_parquet(ROOT / "stage_a_inputs/temporal_2019_v1_0/MEM_ETX7B3_Snapshot_Index_v1.0.parquet")["snapshot"]
    aligned = all(pd.DatetimeIndex(pd.to_datetime(part.snapshot, utc=True)).equals(pd.DatetimeIndex(pd.to_datetime(snapshots, utc=True))) for _, part in a.groupby("asset_id", sort=False))
    _add(checks, "B4-QA-14", "Availability schedules use the exact immutable B3 chronology", aligned, True, aligned)
    aval = a.p_max_pu.to_numpy(float)
    _add(checks, "B4-QA-15", "Availability schedules are finite and bounded", f"min={aval.min()}; max={aval.max()}; finite={np.isfinite(aval).all()}", "finite [0,1]", np.isfinite(aval).all() and aval.min() >= 0 and aval.max() <= 1)
    schedule_means = a.groupby("asset_id").p_max_pu.mean()
    expected_means = g.set_index("asset_id").loc[schedule_means.index, "static_availability_equivalent"].astype(float)
    mean_diff = (schedule_means - expected_means).abs().max()
    _add(checks, "B4-QA-16", "Hourly availability means reconcile to accepted static equivalents", mean_diff, 0.0, mean_diff <= 1e-9)
    nuclear_ids = set(g.loc[g.availability_mode.eq("B4_BLK007_NUCLEAR"), "asset_id"])
    nuclear_means = a.loc[a.asset_id.isin(nuclear_ids)].groupby("asset_id").p_max_pu.mean()
    nuclear_ok = not nuclear_means.empty and np.allclose(nuclear_means, 0.9245, atol=1e-9, rtol=0)
    _add(checks, "B4-QA-17", "BLK-007 nuclear schedules retain the accepted 0.9245 annual mean", float(nuclear_means.mean()), 0.9245, nuclear_ok)
    geothermal_ids = set(g.loc[g.availability_mode.eq("B4_CONSTANT_GEOTHERMAL"), "asset_id"])
    geothermal_ok = not geothermal_ids or a.loc[a.asset_id.isin(geothermal_ids), "p_max_pu"].eq(0.9).all()
    _add(checks, "B4-QA-18", "Geothermal availability is exactly 0.90", geothermal_ok, True, geothermal_ok)
    no_forbidden_derating = a.historical_capacity_factor_used.astype(str).str.lower().eq("false").all() and a.slow_CDP_factor_used.astype(str).str.lower().eq("false").all()
    _add(checks, "B4-QA-19", "Historical CF and Slow CDP factors are excluded from technical availability", no_forbidden_derating, True, no_forbidden_derating)

    b1s = _read(ROOT / "stage_a_inputs/etx7a_v1_0/static/MEM_ETX7A_Storage_Static_v1.0.csv")
    b2s = _read(ROOT / "stage_a_inputs/italy_base_v1_0/static/MEM_ETX7B2_IT_Storage_Static_v1.0.csv")
    source_storage = pd.concat([b1s[["storage_id", "charge_power_MW", "discharge_power_MW", "energy_MWh"]], b2s[["storage_id", "charge_power_MW", "discharge_power_MW", "energy_MWh"]]], ignore_index=True).set_index("storage_id").astype(float)
    mapped_storage = s.set_index("storage_id")[["charge_power_MW", "discharge_power_MW", "energy_MWh"]].astype(float)
    storage_diff = (mapped_storage - source_storage.reindex(mapped_storage.index)).abs().to_numpy().max()
    _add(checks, "B4-QA-20", "All 43 storage capacities exactly preserve frozen B1/B2 values", f"rows={len(s)}; max_diff={storage_diff}", "43; 0", len(s) == 43 and storage_diff <= 1e-9)
    no_storage_extend = s.p_nom_extendable.astype(str).str.lower().eq("false").all() and s.e_nom_extendable.astype(str).str.lower().eq("false").all()
    _add(checks, "B4-QA-21", "All storage power and energy are non-extendable", no_storage_extend, True, no_storage_extend)
    bess = s.loc[s.storage_family.eq("BESS")]
    bess_ok = np.allclose(bess.round_trip_efficiency.astype(float), 0.9) and np.allclose(bess.charge_efficiency.astype(float), math.sqrt(0.9)) and np.allclose(bess.discharge_efficiency.astype(float), math.sqrt(0.9))
    _add(checks, "B4-QA-22", "BESS uses 0.90 RTE with symmetric one-way legs", bess_ok, True, bess_ok)
    phs_storage = s.loc[s.storage_family.eq("PHS")]
    phs_eff_ok = np.allclose(phs_storage.round_trip_efficiency.astype(float), 0.75) and np.allclose(phs_storage.charge_efficiency.astype(float), math.sqrt(0.75)) and np.allclose(phs_storage.discharge_efficiency.astype(float), math.sqrt(0.75))
    _add(checks, "B4-QA-23", "PHS uses 0.75 RTE with symmetric one-way legs", phs_eff_ok, True, phs_eff_ok)
    soc_ok = s.standing_loss_per_hour.astype(float).eq(0).all() and set(s.terminal_state_rule) == {"CYCLIC_ANNUAL"} and set(s.initial_state_rule) == {"ENDOGENOUS_UNDER_CYCLIC_ANNUAL_BOUNDARY"}
    _add(checks, "B4-QA-24", "BESS/PHS use zero standing loss and cyclic annual SOC", soc_ok, True, soc_ok)
    distinction_ok = {"BESS_DISTRIBUTED", "BESS_GRID"} <= set(s.storage_class)
    _add(checks, "B4-QA-25", "Distributed and grid BESS classes remain distinct", distinction_ok, True, distinction_ok)

    for year in HORIZONS:
        year_phs = phs.loc[phs.year.astype(int).eq(year)]
        observed = year_phs[["discharge_power_MW", "charge_power_MW", "operational_energy_MWh"]].astype(float).sum()
        passed = math.isclose(observed.discharge_power_MW, 7252.3, abs_tol=1e-9) and math.isclose(observed.charge_power_MW, 6400, abs_tol=1e-9) and math.isclose(observed.operational_energy_MWh, 53000, abs_tol=1e-9)
        _add(checks, f"B4-QA-{26 if year == 2040 else 27}", f"Italy {year} PHS state split reconciles to 7,252.3/6,400/53,000", f"{observed.to_dict()}", "7252.3/6400/53000", passed)
    class_discharge = phs.groupby([phs.year.astype(int), "hydro_class"]).discharge_power_MW.apply(lambda x: pd.to_numeric(x).sum())
    subset_ok = all(math.isclose(class_discharge.loc[(year, "PURE_PHS")], 3969.57561, abs_tol=1e-9) and math.isclose(class_discharge.loc[(year, "MIXED_PHS")], 3282.72439, abs_tol=1e-9) for year in HORIZONS)
    _add(checks, "B4-QA-28", "Italy pure/mixed discharge subsets remain exact", subset_ok, True, subset_ok)
    method_ok = np.allclose(phs.method_c_top4_energy_block_MWh.astype(float), 39750) and np.allclose(phs.method_c_residual_energy_block_MWh.astype(float), 13250)
    _add(checks, "B4-QA-29", "Method-C retains the 75/25 energy blocks", method_ok, True, method_ok)
    mixed = phs.loc[phs.hydro_class.eq("MIXED_PHS")]
    mixed_ok = mixed.shared_water_state_count.astype(int).eq(1).all() and mixed.natural_inflow_required.astype(str).str.lower().eq("true").all() and mixed.grid_charging_allowed.astype(str).str.lower().eq("true").all() and mixed.state_receives.eq("NATURAL_INFLOW_AND_ELECTRICAL_PUMPING").all()
    _add(checks, "B4-QA-30", "Each Italy mixed PHS system has one shared inflow-plus-pumping state", mixed_ok, True, mixed_ok)
    _add(checks, "B4-QA-31", "Italy PHS state IDs are unique with no duplicate state", int(phs.state_id.nunique()), len(phs), phs.state_id.is_unique)

    _add(checks, "B4-QA-32", "Hydro operational slices cover both horizons without duplicate IDs", f"rows={len(h)}; unique={h.operational_slice_id.nunique()}", f"{len(h)}", h.operational_slice_id.is_unique and set(h.year.astype(int)) == set(HORIZONS))
    b3_hydro = pd.read_parquet(ROOT / "stage_a_inputs/temporal_2019_v1_0/MEM_ETX7B3_Hydro_Temporal_Hourly_v1.0.parquet")
    profile_ids = set(b3_hydro.profile_id)
    inflow_profiles_ok = set(inflow.natural_inflow_profile_id) <= profile_ids
    _add(checks, "B4-QA-33", "Every natural-inflow mapping references an immutable B3 hydro profile", len(set(inflow.natural_inflow_profile_id)), len(set(inflow.natural_inflow_profile_id)), inflow_profiles_ok)
    natural = h.loc[h.natural_inflow_required.astype(str).str.lower().eq("true")]
    conservation_diff = np.max(np.abs(natural.annual_natural_inflow_MWh_water.astype(float) * natural.turbine_efficiency.astype(float) - natural.annual_electrical_generation_reference_MWh.astype(float)))
    _add(checks, "B4-QA-34", "Natural hydro annual water-energy conversion conserves energy", conservation_diff, 0.0, conservation_diff <= 1e-8)
    italy_annual = natural.loc[natural.country_code.eq("IT")].groupby(natural.loc[natural.country_code.eq("IT"), "year"].astype(int)).annual_electrical_generation_reference_MWh.apply(lambda x: pd.to_numeric(x).sum())
    italy_annual_ok = all(math.isclose(value, 52391704.319, abs_tol=1e-6) for value in italy_annual)
    _add(checks, "B4-QA-35", "Italy hydro annual reference reconciles to the accepted Terna control", italy_annual.to_dict(), "52,391,704.319 MWh each horizon", italy_annual_ok)
    conventional = h.loc[~h.hydro_class.isin(["PURE_PHS", "MIXED_PHS"])]
    no_grid_charge = conventional.grid_charging_allowed.astype(str).str.lower().eq("false").all()
    _add(checks, "B4-QA-36", "Conventional hydro has no electrical grid charging", no_grid_charge, True, no_grid_charge)
    pure = h.loc[h.hydro_class.eq("PURE_PHS")]
    no_phs_inflow = pure.natural_inflow_required.astype(str).str.lower().eq("false").all() and pure.natural_inflow_profile_id.eq("").all()
    _add(checks, "B4-QA-37", "Pure PHS receives no fictitious natural inflow", no_phs_inflow, True, no_phs_inflow)
    no_physical_inventory = h.physical_HPHS_energy_MWh_used.astype(float).eq(0).all() and h.physical_HDAM_energy_MWh_used.astype(float).eq(0).all()
    _add(checks, "B4-QA-38", "Gross HPHS/HDAM physical inventories are never promoted to operational e_nom", no_physical_inventory, True, no_physical_inventory)
    hydro_capacity = h.groupby("source_asset_id").turbine_power_MW.apply(lambda x: pd.to_numeric(x).sum())
    source_hydro_ids = set(b1.loc[b1.mapped_carrier.eq("HYDRO_NON_PHS"), "asset_id"]) | set(b2.loc[b2.stage_a_static_class.str.startswith("HYDRO_"), "asset_id"])
    source_hydro_capacity = pd.concat([b1.loc[b1.mapped_carrier.eq("HYDRO_NON_PHS"), ["asset_id", "p_nom_MW"]], b2.loc[b2.stage_a_static_class.str.startswith("HYDRO_"), ["asset_id", "p_nom_MW"]]], ignore_index=True).set_index("asset_id").p_nom_MW.astype(float)
    capacity_reconcile = max(abs(hydro_capacity.loc[list(source_hydro_ids)] - source_hydro_capacity.loc[list(source_hydro_ids)]))
    _add(checks, "B4-QA-39", "Hydro operational slicing preserves every frozen non-PHS turbine capacity", capacity_reconcile, 0.0, capacity_reconcile <= 1e-9)

    _add(checks, "B4-QA-40", "Every generator has exactly one limited-energy disposition", int(constraints.asset_id.nunique()), len(g), constraints.asset_id.is_unique and set(constraints.asset_id) == set(g.asset_id))
    soft = constraints.loc[constraints.disposition.eq("SHARED_EX_POST_SOFT_CONTROL_NOT_A_HARD_CONSTRAINT")]
    soft_ok = not soft.empty and soft.hard_e_sum_min_MWh.eq("").all() and soft.hard_e_sum_max_MWh.eq("").all() and soft.shared_control_id.ne("").all()
    _add(checks, "B4-QA-41", "Italy scenario-energy controls remain shared ex-post soft controls", soft_ok, True, soft_ok)
    external_none = constraints.loc[constraints.country_code.ne("IT"), "disposition"].eq("NO_ADDITIONAL_ANNUAL_CONSTRAINT").all()
    _add(checks, "B4-QA-42", "External generators receive no unapproved annual resource constraint", external_none, True, external_none)
    inheritance_unique = not inheritance.duplicated(["target_record_type", "target_record_id", "parameter_family"]).any() and inheritance.status.eq("RESOLVED_EXACTLY_ONCE").all()
    _add(checks, "B4-QA-43", "Asset/class x horizon inheritance coverage is complete and unique", inheritance_unique, True, inheritance_unique)

    erratum_ok = len(erratum) == 2 and set(erratum.profile_id) == {"ETX7B3_MT_SOLAR_PV_2019", "ETX7B3_TN_CSP_2019"} and erratum.hourly_values_changed.astype(str).str.lower().eq("false").all() and erratum.prospective_corrected_classification.eq("OFFICIAL_TYNDP_PECD_2019").all()
    _add(checks, "B4-QA-44", "MT solar/TN CSP provenance correction is a two-row B4 sidecar only", erratum_ok, True, erratum_ok)
    _add(checks, "B4-QA-45", "Every B4 artifact matches its deterministic output manifest", int(outputs.status.eq("PASS").sum()), len(outputs), outputs.status.eq("PASS").all())
    if run_deterministic_rebuild:
        det_ok = deterministic.status.eq("PASS").all()
        _add(checks, "B4-QA-46", "Independent B4 rebuild is byte-identical", int(deterministic.status.eq("PASS").sum()), len(deterministic), det_ok)
    gates = pd.read_csv(ROOT / "docs/project_plan/MEM_PROJECT_PLAN_MANIFEST.csv", dtype=str, keep_default_na=False)
    plan_ok = len(gates.loc[gates.authority_status.eq("ACTIVE")]) == 1 and gates.loc[gates.authority_status.eq("ACTIVE"), "plan_version"].iloc[0] == "2.2"
    _add(checks, "B4-QA-47", "Plan v2.2 is the sole active remaining-execution authority", plan_ok, True, plan_ok)
    source_text = (ROOT / "src/mem_model/stage_a/operating_base.py").read_text(encoding="utf-8").lower()
    scope_ok = "\nimport pypsa" not in source_text and "\nfrom pypsa" not in source_text and "optimize(" not in source_text and "lopf(" not in source_text
    _add(checks, "B4-QA-48", "B4 code contains no PyPSA topology/network/optimization execution", scope_ok, True, scope_ok)
    approval = ROOT / "config/approval_gates.yaml"
    import yaml
    gate_data = yaml.safe_load(approval.read_text(encoding="utf-8"))
    solver_disabled = gate_data["full_year_solver"]["execution_enabled"] is False
    _add(checks, "B4-QA-49", "Full-year solver remains disabled", solver_disabled, True, solver_disabled)

    qa = pd.DataFrame.from_records(checks)
    passed = qa.status.eq("PASS").all()
    status = "ETX7B4_BASE_RUNTIME_PARAMETER_LAYER_COMPLETE" if passed else "ETX7B4_VALIDATION_FAILED"
    verification = {
        "phase": "ETX-7B4",
        "status": status,
        "schema_version": "ETX7B4_OPERATING_BASE_V1_0",
        "plan": {"version": "2.2", "sha256": "7F858DEB139F46E2ECE26EA8D82FA972A6D304B01D44300EF31FE79D8AB11EA6"},
        "counts": {"generators": len(g), "availability_rows": len(a), "availability_assets": int(a.asset_id.nunique()), "storage": len(s), "italy_phs_states": len(phs), "hydro_slices": len(h), "natural_inflow_mappings": len(inflow), "constraint_dispositions": len(constraints), "inheritance_rows": len(inheritance), "qa_checks": len(qa), "qa_failures": int(qa.status.eq("FAIL").sum())},
        "common_carbon_EUR2025_per_tCO2": {"2040": 104.5504, "2050": 400.0},
        "italy_phs_controls": {"discharge_MW": 7252.3, "charge_MW": 6400.0, "energy_MWh": 53000.0},
        "frozen_regression": {family: ("PASS" if group.status.eq("PASS").all() else "FAIL") for family, group in frozen.groupby("receipt_family")},
        "scope": {"markets": list(MARKETS), "horizons": list(HORIZONS), "scenario": "Base", "b3_modified": False, "topology": "NOT_BUILT", "pypsa_network": "NOT_INSTANTIATED", "optimization": "NOT_RUN", "solver_execution_enabled": False},
        "repository_tests": _pytest_receipt(),
        "next_gate": "ETX-7B5_FIXED_TEN_MARKET_TOPOLOGY",
        "decisions_needed": "NONE" if passed else "REVIEW_FAILED_QA_ONLY",
    }
    if write_reports:
        QA_DIR.mkdir(parents=True, exist_ok=True)
        frozen.to_csv(QA_DIR / "MEM_ETX7B4_Frozen_Source_Hash_Receipt_v1.0.csv", index=False, encoding="utf-8", lineterminator="\n")
        outputs.to_csv(QA_DIR / "MEM_ETX7B4_Output_Manifest_Receipt_v1.0.csv", index=False, encoding="utf-8", lineterminator="\n")
        deterministic.to_csv(QA_DIR / "MEM_ETX7B4_Deterministic_Rebuild_Receipt_v1.0.csv", index=False, encoding="utf-8", lineterminator="\n")
        qa.to_csv(QA_DIR / "MEM_ETX7B4_Reconciliation_QA_v1.0.csv", index=False, encoding="utf-8", lineterminator="\n")
        (QA_DIR / "MEM_ETX7B4_Final_Verification_v1.0.json").write_text(json.dumps(verification, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"qa": qa, "verification": verification, "frozen_receipt": frozen, "output_receipt": outputs, "deterministic_receipt": deterministic}


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate deterministic ETX-7B4 operating layer")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--skip-deterministic-rebuild", action="store_true")
    args = parser.parse_args()
    result = validate_operating_base_package(args.output_dir, write_reports=True, run_deterministic_rebuild=not args.skip_deterministic_rebuild)
    print(json.dumps(result["verification"], indent=2))
    if not result["qa"].status.eq("PASS").all():
        raise SystemExit(1)


if __name__ == "__main__":
    main()

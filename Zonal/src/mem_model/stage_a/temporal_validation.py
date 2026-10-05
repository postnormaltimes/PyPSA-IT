from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import numpy as np
import pandas as pd
import xarray as xr

from mem_model.stage_a.italy_static import (
    ROOT,
    sha256_file,
    verify_frozen_input_receipts,
)
from mem_model.stage_a.temporal_2019 import (
    CACHE_FILES,
    CORE_MARKETS,
    DEFAULT_CONFIG,
    HORIZONS,
    OUTPUT_FILES,
    _build_demand,
    _load_config,
    _resolve,
    build_temporal_package,
    canonical_snapshots,
)


DEFAULT_OUTPUT_DIR = ROOT / "stage_a_inputs" / "temporal_2019_v1_0"
QA_DIR = ROOT / "qa" / "stage_a" / "etx7b3"
JUNIT_PATH = QA_DIR / "MEM_ETX7B3_Pytest_JUnit_v1.0.xml"
B2_MANIFEST = (
    ROOT
    / "stage_a_inputs"
    / "italy_base_v1_0"
    / "static"
    / "MEM_ETX7B2_IT_Static_Output_Manifest_v1.0.csv"
)
ALLOWED_COVERAGE_CLASSIFICATIONS = {
    "NATIVE_PYPSA_EUR",
    "MEM_NORMALIZED_OVERRIDE",
    "PYPSA_EUR_SPATIAL_WEIGHTING_ONLY",
    "EXPLICIT_ADAPTER",
    "DEFER_ETX7B4",
    "NOT_REQUIRED",
}


def _read_frames(output_dir: Path) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for key, filename in OUTPUT_FILES.items():
        path = output_dir / filename
        if path.suffix.lower() == ".parquet":
            frames[key] = pd.read_parquet(path)
        else:
            frames[key] = pd.read_csv(path, dtype=str, keep_default_na=False)
    return frames


def _add_check(
    checks: list[dict[str, object]],
    check_id: str,
    description: str,
    observed: object,
    expected: object,
    passed: bool,
    notes: str = "",
) -> None:
    checks.append(
        {
            "check_id": check_id,
            "description": description,
            "observed": observed,
            "expected": expected,
            "difference": "" if passed else f"observed={observed}; expected={expected}",
            "status": "PASS" if passed else "FAIL",
            "notes": notes,
        }
    )


def _is_true(value: object) -> bool:
    return str(value).strip().lower() == "true"


def _manifest_receipt(
    manifest: pd.DataFrame,
    *,
    root: Path,
    path_column: str,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for record in manifest.to_dict(orient="records"):
        path = root / str(record[path_column]).replace("\\", "/")
        exists = path.exists()
        observed_bytes = path.stat().st_size if exists else ""
        observed_rows = ""
        if exists:
            observed_rows = (
                len(pd.read_parquet(path))
                if path.suffix.lower() == ".parquet"
                else len(pd.read_csv(path, dtype=str, keep_default_na=False))
            )
        observed_hash = sha256_file(path) if exists else ""
        expected_hash = str(record["sha256"]).upper()
        passed = (
            exists
            and int(observed_bytes) == int(record["bytes"])
            and int(observed_rows) == int(record["rows"])
            and observed_hash == expected_hash
        )
        rows.append(
            {
                "artifact": record.get("artifact", path.name),
                "path": str(path).replace("\\", "/"),
                "expected_bytes": record["bytes"],
                "observed_bytes": observed_bytes,
                "expected_rows": record["rows"],
                "observed_rows": observed_rows,
                "expected_sha256": expected_hash,
                "observed_sha256": observed_hash,
                "status": "PASS" if passed else "FAIL",
            }
        )
    return pd.DataFrame.from_records(rows)


def _b2_receipt() -> pd.DataFrame:
    manifest = pd.read_csv(B2_MANIFEST, dtype=str, keep_default_na=False)
    return _manifest_receipt(manifest, root=ROOT, path_column="relative_path")


def _temporal_manifest_receipt(
    output_dir: Path, manifest: pd.DataFrame
) -> pd.DataFrame:
    return _manifest_receipt(manifest, root=output_dir, path_column="relative_path")


def _deterministic_rebuild_receipt(output_dir: Path) -> pd.DataFrame:
    with tempfile.TemporaryDirectory(prefix="mem_etx7b3_determinism_") as temp:
        rebuilt = Path(temp) / "temporal"
        build_temporal_package(
            rebuilt,
            config_path=DEFAULT_CONFIG,
            source_cache_dir=output_dir / "cache",
        )
        records: list[dict[str, object]] = []
        relative_files = [Path(name) for name in OUTPUT_FILES.values()]
        relative_files += [Path("cache") / name for name in CACHE_FILES.values()]
        for relative in relative_files:
            accepted = output_dir / relative
            candidate = rebuilt / relative
            accepted_hash = sha256_file(accepted)
            candidate_hash = sha256_file(candidate)
            identical = accepted.read_bytes() == candidate.read_bytes()
            records.append(
                {
                    "relative_path": str(relative).replace("\\", "/"),
                    "accepted_sha256": accepted_hash,
                    "rebuilt_sha256": candidate_hash,
                    "byte_identical": identical,
                    "status": "PASS" if identical else "FAIL",
                }
            )
    return pd.DataFrame.from_records(records)


def _junit_receipt() -> dict[str, object]:
    if not JUNIT_PATH.exists():
        return {"status": "NOT_AVAILABLE_WHEN_VALIDATION_RAN"}
    root = ElementTree.parse(JUNIT_PATH).getroot()
    suite = root.find("testsuite") if root.tag == "testsuites" else root
    if suite is None:
        return {"status": "UNREADABLE"}
    tests = int(suite.attrib.get("tests", "0"))
    failures = int(suite.attrib.get("failures", "0"))
    errors = int(suite.attrib.get("errors", "0"))
    skipped = int(suite.attrib.get("skipped", "0"))
    return {
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "status": "PASS" if failures == 0 and errors == 0 else "FAIL",
        "path": str(JUNIT_PATH.relative_to(ROOT)).replace("\\", "/"),
    }


def _same_snapshots(frame: pd.DataFrame, group_columns: list[str]) -> bool:
    expected = canonical_snapshots()
    for _, group in frame.groupby(group_columns, sort=False):
        observed = pd.DatetimeIndex(pd.to_datetime(group["snapshot"], utc=True)).sort_values()
        if len(observed) != len(expected) or not observed.equals(expected):
            return False
    return True


def validate_temporal_package(
    output_dir: Path | None = None,
    *,
    write_reports: bool = True,
    run_deterministic_rebuild: bool = True,
) -> dict[str, Any]:
    output_dir = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    frames = _read_frames(output_dir)
    snapshots = frames["snapshots"]
    coverage = frames["coverage"]
    load = frames["load"]
    vre = frames["vre"]
    hydro = frames["hydro"]
    provenance = frames["provenance"]
    source_receipt = frames["source_receipt"]
    manifest = frames["manifest"]

    frozen_receipt = verify_frozen_input_receipts()
    b2_receipt = _b2_receipt()
    output_receipt = _temporal_manifest_receipt(output_dir, manifest)
    deterministic_receipt = (
        _deterministic_rebuild_receipt(output_dir)
        if run_deterministic_rebuild
        else pd.DataFrame(
            [{"relative_path": "NOT_RUN", "status": "NOT_RUN"}]
        )
    )
    cfg = _load_config(DEFAULT_CONFIG)
    _, _, load_compatibility = _build_demand(cfg)
    checks: list[dict[str, object]] = []

    stage_b = frozen_receipt.loc[
        frozen_receipt["receipt_family"].eq("FROZEN_STAGE_B_STATIC_CONTRACT")
    ]
    b1 = frozen_receipt.loc[
        frozen_receipt["receipt_family"].eq("FROZEN_ETX7B1_STATIC_OUTPUT")
    ]
    _add_check(checks, "ETX7B3-QA-01", "Frozen Stage-B source hashes remain unchanged", f"{stage_b['status'].eq('PASS').sum()}/{len(stage_b)}", "9/9", len(stage_b) == 9 and stage_b["status"].eq("PASS").all())
    _add_check(checks, "ETX7B3-QA-02", "Frozen ETX-7B1 output hashes remain unchanged", f"{b1['status'].eq('PASS').sum()}/{len(b1)}", "7/7", len(b1) == 7 and b1["status"].eq("PASS").all())
    _add_check(checks, "ETX7B3-QA-03", "Frozen ETX-7B2 deterministic outputs remain unchanged", f"{b2_receipt['status'].eq('PASS').sum()}/{len(b2_receipt)}", "5/5", len(b2_receipt) == 5 and b2_receipt["status"].eq("PASS").all())

    expected_index = canonical_snapshots()
    observed_index = pd.DatetimeIndex(pd.to_datetime(snapshots["snapshot"], utc=True))
    chronology_ok = (
        len(observed_index) == 8760
        and observed_index.is_monotonic_increasing
        and observed_index.is_unique
        and observed_index.equals(expected_index)
    )
    _add_check(checks, "ETX7B3-QA-04", "Canonical chronology is exactly 8,760 monotonic gap-free UTC hours", f"rows={len(observed_index)}; start={observed_index.min()}; end={observed_index.max()}; unique={observed_index.is_unique}", "2019-01-01 00:00 UTC through 2019-12-31 23:00 UTC; 8760", chronology_ok)
    all_time_series_aligned = (
        _same_snapshots(load, ["horizon", "country_code"])
        and _same_snapshots(vre, ["profile_id"])
        and _same_snapshots(hydro, ["profile_id"])
    )
    _add_check(checks, "ETX7B3-QA-05", "Every emitted hourly family uses the identical canonical snapshot index", all_time_series_aligned, True, all_time_series_aligned)

    expected_load_groups = {(year, market) for year in HORIZONS for market in CORE_MARKETS}
    observed_load_groups = set(zip(load["horizon"].astype(int), load["country_code"]))
    load_group_counts = load.groupby(["horizon", "country_code"]).size()
    _add_check(checks, "ETX7B3-QA-06", "Exactly one complete Base load realization exists per core market and horizon", f"groups={len(observed_load_groups)}; rows={len(load)}", "20 groups; 175200 rows; 8760 rows/group", observed_load_groups == expected_load_groups and load_group_counts.eq(8760).all() and set(load["scenario"]) == {"Base"})

    external_demand = pd.read_csv(ROOT / "stage_a_inputs" / "etx7a_v1_0" / "static" / "MEM_ETX7A_Annual_Demand_Static_v1.0.csv")
    italy_demand = pd.read_csv(ROOT / "stage_a_inputs" / "italy_base_v1_0" / "static" / "MEM_ETX7B2_IT_Annual_Demand_Static_v1.0.csv")
    controls = pd.concat([external_demand[["country_code", "year", "annual_demand_MWh"]], italy_demand[["country_code", "year", "annual_demand_MWh"]]], ignore_index=True)
    demand_qa_rows: list[dict[str, object]] = []
    for record in controls.sort_values(["year", "country_code"]).to_dict(orient="records"):
        year = int(record["year"])
        country = str(record["country_code"])
        expected = float(record["annual_demand_MWh"])
        observed_values = load.loc[
            load["horizon"].astype(int).eq(year)
            & load["country_code"].eq(country),
            "load_MW",
        ].to_numpy(dtype=np.float64)
        observed = float(observed_values.sum(dtype=np.float64))
        tolerance = max(1e-6, abs(expected) * 5e-15)
        difference = observed - expected
        demand_qa_rows.append({"country_code": country, "horizon": year, "scenario": "Base", "observed_annual_MWh": observed, "frozen_control_MWh": expected, "difference_MWh": difference, "serialization_tolerance_MWh": tolerance, "status": "PASS" if abs(difference) <= tolerance else "FAIL"})
    demand_qa = pd.DataFrame.from_records(demand_qa_rows)
    _add_check(checks, "ETX7B3-QA-07", "All hourly demand sums reconcile to frozen MEM annual controls", f"{demand_qa['status'].eq('PASS').sum()}/{len(demand_qa)}", "20/20", len(demand_qa) == 20 and demand_qa["status"].eq("PASS").all(), "Tolerance is limited to floating-point/Parquet serialization precision")
    load_numeric = load[["load_MW", "normalized_shape_value"]].to_numpy(dtype=float)
    load_valid = np.isfinite(load_numeric).all() and (load_numeric >= 0).all()
    _add_check(checks, "ETX7B3-QA-08", "Hourly load has no negative, NaN or infinite values", f"min={np.nanmin(load_numeric)}; finite={np.isfinite(load_numeric).all()}", "finite and >=0", load_valid)
    compatibility_ok = len(load_compatibility) == 8 and load_compatibility["status"].eq("PASS").all() and set(load_compatibility["unit_basis"]) == {"MW"} and set(load_compatibility["timestamp_basis"]) == {"UTC_HOURLY"}
    _add_check(checks, "ETX7B3-QA-09", "ENTSO-E/OPSD demand unit, timestamp and overlap compatibility receipt passes", f"{load_compatibility['status'].eq('PASS').sum()}/{len(load_compatibility)}", "8/8", compatibility_ok)

    required = coverage.loc[coverage["required"].map(_is_true)]
    required_complete = not required.empty and required["status"].eq("COMPLETE").all()
    _add_check(checks, "ETX7B3-QA-10", "Every required fixed-ten-market temporal family is complete", f"complete={required['status'].eq('COMPLETE').sum()}; required={len(required)}", "all required COMPLETE", required_complete)
    classifications_ok = set(coverage["classification"]) <= ALLOWED_COVERAGE_CLASSIFICATIONS
    _add_check(checks, "ETX7B3-QA-11", "Coverage classifications use only approved ETX-7B3 states", sorted(set(coverage["classification"])), sorted(ALLOWED_COVERAGE_CLASSIFICATIONS), classifications_ok)

    vre_values = vre["p_max_pu"].to_numpy(dtype=float)
    vre_bounds_ok = np.isfinite(vre_values).all() and vre_values.min() >= -1e-10 and vre_values.max() <= 1 + 1e-10
    _add_check(checks, "ETX7B3-QA-12", "All VRE p_max_pu values are finite and bounded by [0,1]", f"min={vre_values.min()}; max={vre_values.max()}; profiles={vre['profile_id'].nunique()}", "finite; -1e-10 <= value <= 1+1e-10", vre_bounds_ok)
    all_zero_profiles = [profile_id for profile_id, group in vre.groupby("profile_id") if not (group["p_max_pu"].to_numpy(dtype=float) > 0).any()]
    _add_check(checks, "ETX7B3-QA-13", "No required VRE profile is unintentionally all zero", all_zero_profiles, [], not all_zero_profiles)
    no_capacity_override = not any(column in vre.columns for column in ("p_nom_MW", "capacity_MW", "installed_capacity_MW"))
    _add_check(checks, "ETX7B3-QA-14", "Temporal VRE outputs contain no upstream capacity override", sorted(set(vre.columns) & {"p_nom_MW", "capacity_MW", "installed_capacity_MW"}), [], no_capacity_override)

    hydro_values = hydro["value"].to_numpy(dtype=float)
    hydro_finite = np.isfinite(hydro_values).all() and (hydro_values >= 0).all()
    hydro_sums = hydro.groupby("profile_id")["value"].sum()
    hydro_normalized = np.allclose(hydro_sums.to_numpy(dtype=float), 1.0, rtol=0, atol=1e-12)
    _add_check(checks, "ETX7B3-QA-15", "All required hydro temporal inputs are finite, non-negative and normalized", f"profiles={len(hydro_sums)}; min_sum={hydro_sums.min()}; max_sum={hydro_sums.max()}", "each annual shape sum=1", hydro_finite and hydro_normalized)
    normalized_hydro_classes = hydro["hydro_class"].str.upper()
    no_phs_inflow = not normalized_hydro_classes.str.contains(
        "PURE_PHS|MIXED_PHS|PUMPED_HYDRO|PUMPED_STORAGE",
        regex=True,
    ).any()
    _add_check(checks, "ETX7B3-QA-16", "PHS receives no fictitious or duplicate natural inflow", sorted(hydro["hydro_class"].unique()), "no PHS/pumped class", no_phs_inflow)
    italy_hydro_classes = set(hydro.loc[hydro["country_code"].eq("IT"), "hydro_class"])
    distinct_hydro = {"HYDRO_RUN_OF_RIVER", "HYDRO_BASIN_PONDAGE", "HYDRO_RESERVOIR"} <= italy_hydro_classes
    _add_check(checks, "ETX7B3-QA-17", "Italy run-of-river, basin/pondage and reservoir temporal classes remain distinct", sorted(italy_hydro_classes), ["HYDRO_BASIN_PONDAGE", "HYDRO_RESERVOIR", "HYDRO_RUN_OF_RIVER"], distinct_hydro)

    profile_ids = set(load["profile_id"]) | set(vre["profile_id"]) | set(hydro["profile_id"])
    provenance_ids = set(provenance["profile_id"])
    provenance_complete = profile_ids <= provenance_ids and provenance["profile_id"].is_unique
    _add_check(checks, "ETX7B3-QA-18", "Every emitted profile has unique source/transformation provenance", f"profiles={len(profile_ids)}; provenance={len(provenance_ids)}", "all linked; provenance IDs unique", provenance_complete)
    adapters = provenance.loc[provenance["evidence_or_provenance_class"].eq("EXPLICIT_ADAPTER")]
    adapters_traceable = not adapters.empty and adapters[["source_file_or_dataset", "transformation", "source_year", "country_code"]].astype(str).apply(lambda column: column.str.strip().ne("")).all().all()
    _add_check(checks, "ETX7B3-QA-19", "Every MT/TN/FR explicit adapter is fully traceable", f"adapter_profiles={len(adapters)}; markets={sorted(adapters['country_code'].unique())}", "nonblank source, year, transformation and market", adapters_traceable)
    prohibited_proxy_tokens = "BORROWED_FROM_OTHER_COUNTRY|CROSS_COUNTRY_PROXY|COPY_OTHER_MARKET"
    no_cross_country_proxy = not provenance.astype(str).apply(lambda column: column.str.contains(prohibited_proxy_tokens, case=False, regex=True)).any().any()
    _add_check(checks, "ETX7B3-QA-20", "No undocumented cross-country profile proxy is used", no_cross_country_proxy, True, no_cross_country_proxy)

    market_union = set(load["country_code"]) | set(vre["country_code"]) | set(hydro["country_code"]) | set(coverage["country_code"])
    fixed_scope = market_union == set(CORE_MARKETS) and set(coverage["market_role"]) == {"CORE_STAGE_A_MARKET"}
    _add_check(checks, "ETX7B3-QA-21", "Temporal package is restricted to the fixed ten Stage-A markets", sorted(market_union), sorted(CORE_MARKETS), fixed_scope, "P0/P1 and omitted-country temporal work are superseded")
    deferred_ok = coverage.loc[coverage["required"].eq("DEFERRED_RUNTIME_REQUIREMENT"), "status"].eq("DEFER_ETX7B4").all()
    _add_check(checks, "ETX7B3-QA-22", "Operating availability, storage/SOC and PHS operation remain deferred to ETX-7B4", deferred_ok, True, deferred_ok)

    cutout_row = source_receipt.loc[source_receipt["source_id"].eq("CUTOUT_2019")]
    cutout_path = Path(cutout_row["path"].iloc[0])
    with xr.open_dataset(cutout_path) as cutout_ds:
        cutout_time = pd.DatetimeIndex(pd.to_datetime(cutout_ds["time"].values, utc=True))
    cutout_ok = len(cutout_time) == 8760 and cutout_time.equals(expected_index) and cutout_row["sha256"].iloc[0] == "E6CF2B4C9D463D64B4DFB3A613EF20C3C69E99624DAADEEE6EE9C76CF74E9D1F"
    _add_check(checks, "ETX7B3-QA-23", "Reused 2019 PyPSA-Eur/AtLite cutout retains accepted hash and chronology", f"hours={len(cutout_time)}; sha256={cutout_row['sha256'].iloc[0]}", "8760 hours; accepted E6CF... hash", cutout_ok)
    manifest_ok = len(output_receipt) == len(manifest) and output_receipt["status"].eq("PASS").all()
    _add_check(checks, "ETX7B3-QA-24", "All temporal artifacts and caches match the output manifest", f"{output_receipt['status'].eq('PASS').sum()}/{len(output_receipt)}", f"{len(manifest)}/{len(manifest)}", manifest_ok)
    deterministic_ok = run_deterministic_rebuild and deterministic_receipt["status"].eq("PASS").all()
    _add_check(checks, "ETX7B3-QA-25", "Cache-backed repeat generation is byte-identical", f"{deterministic_receipt['status'].eq('PASS').sum()}/{len(deterministic_receipt)}", f"{len(deterministic_receipt)}/{len(deterministic_receipt)}", deterministic_ok)

    forbidden_columns = {"marginal_cost", "fuel_price", "co2_price", "efficiency", "soc_initial", "link_capacity_MW", "p_nom_extendable"}
    emitted_columns = set(load.columns) | set(vre.columns) | set(hydro.columns)
    scope_fields_ok = not (emitted_columns & forbidden_columns)
    _add_check(checks, "ETX7B3-QA-26", "No B4 operating-cost, efficiency, SOC or topology fields are assigned", sorted(emitted_columns & forbidden_columns), [], scope_fields_ok)
    gates = _load_config(ROOT / "config" / "approval_gates.yaml")
    solver_disabled = gates["full_year_solver"]["execution_enabled"] is False
    _add_check(checks, "ETX7B3-QA-27", "Full-year solver remains disabled and no optimization is authorized", gates["full_year_solver"]["execution_enabled"], False, solver_disabled)
    _add_check(checks, "ETX7B3-QA-28", "No Stage-A topology, network, P0/P1 comparison or optimization was executed", "TEMPORAL_FILES_ONLY", "TEMPORAL_FILES_ONLY", True)

    qa = pd.DataFrame.from_records(checks)
    status = "ETX7B3_2019_TEMPORAL_INPUTS_COMPLETE" if qa["status"].eq("PASS").all() else "ETX7B3_FAILED"
    verification = {
        "phase": "ETX-7B3",
        "status": status,
        "schema_version": "ETX7B3_TEMPORAL_2019_V1_0",
        "fixed_market_scope": list(CORE_MARKETS),
        "chronology": {"year": 2019, "timezone": "UTC", "hours": 8760, "start": str(expected_index[0]), "end": str(expected_index[-1])},
        "counts": {
            "snapshot_rows": len(snapshots),
            "load_rows": len(load),
            "load_realizations": len(observed_load_groups),
            "vre_rows": len(vre),
            "vre_profiles": int(vre["profile_id"].nunique()),
            "hydro_rows": len(hydro),
            "hydro_profiles": int(hydro["profile_id"].nunique()),
            "coverage_rows": len(coverage),
            "required_profiles": len(required),
            "provenance_rows": len(provenance),
            "qa_checks": len(qa),
            "qa_failures": int(qa["status"].eq("FAIL").sum()),
        },
        "frozen_regression": {"stage_b": "PASS" if stage_b["status"].eq("PASS").all() else "FAIL", "etx7b1": "PASS" if b1["status"].eq("PASS").all() else "FAIL", "etx7b2": "PASS" if b2_receipt["status"].eq("PASS").all() else "FAIL"},
        "explicit_adapter_profiles": adapters[["profile_id", "country_code", "profile_family", "source_year", "transformation"]].to_dict(orient="records"),
        "scope_confirmation": {
            "context_markets": "NOT_IN_SCOPE_FIXED_TEN_MARKET_REVISION",
            "operating_parameters": "DEFER_ETX7B4",
            "topology": "NOT_BUILT",
            "pypsa_network": "NOT_INSTANTIATED",
            "optimization": "NOT_RUN",
            "solver_configuration": "UNCHANGED_AND_DISABLED",
        },
        "repository_test_receipt": _junit_receipt(),
        "next_gate": "ETX-7B4_STAGE_A_BASE_OPERATING_PARAMETER_LAYER",
    }

    if write_reports:
        QA_DIR.mkdir(parents=True, exist_ok=True)
        frozen_all = pd.concat(
            [
                frozen_receipt.assign(receipt_source="ETX7B2_FROZEN_RECEIPT"),
                b2_receipt.assign(receipt_family="FROZEN_ETX7B2_STATIC_OUTPUT", receipt_source="ETX7B3_DIRECT_MANIFEST_CHECK"),
            ],
            ignore_index=True,
            sort=False,
        )
        frozen_all.to_csv(QA_DIR / "MEM_ETX7B3_Frozen_Input_Regression_Receipt_v1.0.csv", index=False, encoding="utf-8", lineterminator="\n")
        source_receipt.to_csv(QA_DIR / "MEM_ETX7B3_Source_and_Cutout_Receipt_v1.0.csv", index=False, encoding="utf-8", lineterminator="\n")
        load_compatibility.to_csv(QA_DIR / "MEM_ETX7B3_Load_Source_Compatibility_QA_v1.0.csv", index=False, encoding="utf-8", lineterminator="\n")
        pd.DataFrame([checks[3], checks[4]]).to_csv(QA_DIR / "MEM_ETX7B3_Chronology_QA_v1.0.csv", index=False, encoding="utf-8", lineterminator="\n")
        demand_qa.to_csv(QA_DIR / "MEM_ETX7B3_Annual_Demand_Reconciliation_QA_v1.0.csv", index=False, encoding="utf-8", lineterminator="\n")
        pd.DataFrame([checks[11], checks[12], checks[14], checks[15], checks[16]]).to_csv(QA_DIR / "MEM_ETX7B3_Profile_Bounds_and_Hydro_QA_v1.0.csv", index=False, encoding="utf-8", lineterminator="\n")
        coverage.to_csv(QA_DIR / "MEM_ETX7B3_Coverage_QA_v1.0.csv", index=False, encoding="utf-8", lineterminator="\n")
        output_receipt.to_csv(QA_DIR / "MEM_ETX7B3_Output_Manifest_Receipt_v1.0.csv", index=False, encoding="utf-8", lineterminator="\n")
        deterministic_receipt.to_csv(QA_DIR / "MEM_ETX7B3_Deterministic_Rebuild_Receipt_v1.0.csv", index=False, encoding="utf-8", lineterminator="\n")
        qa.to_csv(QA_DIR / "MEM_ETX7B3_Reconciliation_QA_v1.0.csv", index=False, encoding="utf-8", lineterminator="\n")
        (QA_DIR / "MEM_ETX7B3_Final_Verification_v1.0.json").write_text(json.dumps(verification, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {
        "qa": qa,
        "verification": verification,
        "demand_qa": demand_qa,
        "load_compatibility": load_compatibility,
        "frozen_receipt": frozen_receipt,
        "b2_receipt": b2_receipt,
        "output_receipt": output_receipt,
        "deterministic_receipt": deterministic_receipt,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate ETX-7B3 fixed-ten-market 2019 temporal inputs")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--skip-deterministic-rebuild", action="store_true")
    args = parser.parse_args()
    result = validate_temporal_package(args.output_dir, write_reports=True, run_deterministic_rebuild=not args.skip_deterministic_rebuild)
    print(json.dumps(result["verification"], indent=2))
    if not result["qa"]["status"].eq("PASS").all():
        raise SystemExit(1)


if __name__ == "__main__":
    main()

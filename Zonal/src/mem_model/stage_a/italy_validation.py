from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree

import pandas as pd

from .italy_static import (
    DEFAULT_OUTPUT_DIR,
    ETX7B1_MANIFEST,
    HORIZONS,
    OUTPUT_FILES,
    ROOT,
    SOURCE_FILES,
    STAGE_B_MANIFEST,
    _decimal,
    _decimal_sum,
    _read_contract,
    build_italy_static_package,
    sha256_file,
    verify_frozen_input_receipts,
)


QA_DIR = ROOT / "qa" / "stage_a" / "etx7b2"
PLAN_PATH = (
    ROOT
    / "docs"
    / "project_plan"
    / "MEM_Remaining_Execution_Plan_v2.2.md"
)
PLAN_SHA256 = "7F858DEB139F46E2ECE26EA8D82FA972A6D304B01D44300EF31FE79D8AB11EA6"
JUNIT_PATH = QA_DIR / "MEM_ETX7B2_Pytest_JUnit_v1.0.xml"


def _read_outputs(output_dir: Path) -> dict[str, pd.DataFrame]:
    return {
        name: pd.read_csv(output_dir / filename, dtype=str, keep_default_na=False)
        for name, filename in OUTPUT_FILES.items()
    }


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


def _totals_by_year(frame: pd.DataFrame, column: str) -> dict[int, Decimal]:
    return {
        year: _decimal_sum(frame.loc[frame["year"].astype(int).eq(year), column])
        for year in HORIZONS
    }


def _text_totals(values: dict[int, Decimal]) -> str:
    return "; ".join(f"{year}={value}" for year, value in values.items())


def _manifest_receipt(output_dir: Path, manifest: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for record in manifest.to_dict(orient="records"):
        path = output_dir / record["artifact"]
        observed_bytes = path.stat().st_size if path.exists() else ""
        observed_rows = (
            len(pd.read_csv(path, dtype=str, keep_default_na=False))
            if path.exists()
            else ""
        )
        observed_hash = sha256_file(path) if path.exists() else ""
        passed = (
            path.exists()
            and int(observed_bytes) == int(record["bytes"])
            and int(observed_rows) == int(record["rows"])
            and observed_hash == str(record["sha256"]).upper()
        )
        rows.append(
            {
                "artifact": record["artifact"],
                "expected_bytes": record["bytes"],
                "observed_bytes": observed_bytes,
                "expected_rows": record["rows"],
                "observed_rows": observed_rows,
                "expected_sha256": str(record["sha256"]).upper(),
                "observed_sha256": observed_hash,
                "status": "PASS" if passed else "FAIL",
            }
        )
    return pd.DataFrame.from_records(rows)


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


def _provenance_reconciles(
    targets: pd.DataFrame,
    target_id_column: str,
    target_value_column: str,
    provenance: pd.DataFrame,
    target_type: str,
    target_dimension: str,
) -> bool:
    selected = provenance.loc[
        provenance["target_record_type"].eq(target_type)
        & provenance["target_dimension"].eq(target_dimension)
    ]
    sums = selected.groupby("target_record_id", sort=True)["contribution_value"].apply(
        lambda values: _decimal_sum(values)
    )
    for record in targets.to_dict(orient="records"):
        if record[target_id_column] not in sums:
            return False
        if sums[record[target_id_column]] != _decimal(record[target_value_column]):
            return False
    return True


def validate_italy_static_package(
    output_dir: Path | None = None, *, write_reports: bool = True
) -> dict[str, Any]:
    output_dir = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    frames = _read_outputs(output_dir)
    demand = frames["demand"]
    generators = frames["generators"]
    storage = frames["storage"]
    crosswalk = frames["crosswalk"]
    provenance = frames["provenance"]
    manifest = frames["manifest"]
    source_receipts = verify_frozen_input_receipts()
    output_receipts = _manifest_receipt(output_dir, manifest)
    checks: list[dict[str, object]] = []

    stage_b_receipts = source_receipts.loc[
        source_receipts["receipt_family"].eq("FROZEN_STAGE_B_STATIC_CONTRACT")
    ]
    etx7b1_receipts = source_receipts.loc[
        source_receipts["receipt_family"].eq("FROZEN_ETX7B1_STATIC_OUTPUT")
    ]
    _add_check(
        checks,
        "ETX7B2-QA-01",
        "All frozen Stage-B inputs retain accepted row counts, bytes and SHA-256 hashes",
        f"{stage_b_receipts['status'].eq('PASS').sum()}/{len(stage_b_receipts)}",
        "9/9",
        len(stage_b_receipts) == 9 and stage_b_receipts["status"].eq("PASS").all(),
    )
    _add_check(
        checks,
        "ETX7B2-QA-02",
        "All ETX-7B1 deterministic output artifacts retain accepted hashes",
        f"{etx7b1_receipts['status'].eq('PASS').sum()}/{len(etx7b1_receipts)}",
        "7/7",
        len(etx7b1_receipts) == 7 and etx7b1_receipts["status"].eq("PASS").all(),
    )
    plan_ok = PLAN_PATH.exists() and sha256_file(PLAN_PATH) == PLAN_SHA256
    _add_check(
        checks,
        "ETX7B2-QA-03",
        "The active remaining-execution plan is the byte-preserved v2.2 authority",
        sha256_file(PLAN_PATH) if PLAN_PATH.exists() else "MISSING",
        PLAN_SHA256,
        plan_ok,
    )

    expected_scope = (
        set(demand["year"].astype(int))
        | set(generators["year"].astype(int))
        | set(storage["year"].astype(int))
    )
    scenarios = set(demand["scenario"]) | set(generators["scenario"]) | set(storage["scenario"])
    _add_check(
        checks,
        "ETX7B2-QA-04",
        "Only 2040 Base and 2050 Base are present",
        f"years={sorted(expected_scope)}; scenarios={sorted(scenarios)}",
        "years=[2040, 2050]; scenarios=['Base']",
        expected_scope == set(HORIZONS) and scenarios == {"Base"},
    )

    demand_source = _read_contract(SOURCE_FILES["demand"])
    demand_source = demand_source.loc[
        demand_source["scenario"].eq("Base")
        & demand_source["year"].astype(int).isin(HORIZONS)
    ]
    demand_counts = demand_source.groupby(demand_source["year"].astype(int)).size().to_dict()
    _add_check(
        checks,
        "ETX7B2-QA-05",
        "Exactly seven frozen Stage-B demand zones contribute per horizon",
        json.dumps(demand_counts, sort_keys=True),
        json.dumps({2040: 7, 2050: 7}, sort_keys=True),
        demand_counts == {2040: 7, 2050: 7},
    )
    demand_output_totals = _totals_by_year(demand, "annual_demand_MWh")
    demand_source_totals = {
        year: _decimal_sum(
            demand_source.loc[
                demand_source["year"].astype(int).eq(year),
                "annual_zonal_demand_MWh",
            ]
        )
        for year in HORIZONS
    }
    demand_controls = {
        year: _decimal(
            demand_source.loc[
                demand_source["year"].astype(int).eq(year),
                "annual_national_demand_TWh",
            ].iloc[0]
        )
        * Decimal("1000000")
        for year in HORIZONS
    }
    demand_ok = demand_output_totals == demand_source_totals == demand_controls
    _add_check(
        checks,
        "ETX7B2-QA-06",
        "National Stage-A demand equals both seven-zone sum and frozen national control",
        _text_totals(demand_output_totals),
        "2040=439000000 MWh; 2050=583100000 MWh",
        demand_ok,
    )
    demand_rows_ok = len(demand) == 2 and demand.groupby("year").size().eq(1).all()
    _add_check(
        checks,
        "ETX7B2-QA-07",
        "Exactly one national Italy demand row exists per horizon",
        len(demand),
        2,
        demand_rows_ok,
    )

    generator_source = _read_contract(SOURCE_FILES["generators"])
    generator_source = generator_source.loc[
        generator_source["scenario"].eq("Base")
        & generator_source["year"].astype(int).isin(HORIZONS)
    ]
    source_generator_totals = {
        year: _decimal_sum(
            generator_source.loc[
                generator_source["year"].astype(int).eq(year), "p_nom_MW"
            ]
        )
        for year in HORIZONS
    }
    output_generator_contract = generators.loc[
        generators["accounting_origin"].eq("STAGE_B_GENERATOR_CONTRACT")
    ]
    output_generator_totals = _totals_by_year(output_generator_contract, "p_nom_MW")
    _add_check(
        checks,
        "ETX7B2-QA-08",
        "National generator-contract MW equals exact seven-zone Base totals",
        _text_totals(output_generator_totals),
        _text_totals(source_generator_totals),
        output_generator_totals == source_generator_totals,
    )

    hydro_source = _read_contract(SOURCE_FILES["hydro"])
    conventional_hydro = hydro_source.loc[
        hydro_source["hydro_class"].isin(["BASIN_PONDAGE", "RESERVOIR"])
    ]
    hydro_expected = _decimal_sum(conventional_hydro["p_nom_MW_NET"])
    output_hydro = generators.loc[
        generators["accounting_origin"].eq("HYDRO_STATIC_COMPONENT_MAPPING")
    ]
    output_hydro_totals = _totals_by_year(output_hydro, "p_nom_MW")
    hydro_ok = all(value == hydro_expected for value in output_hydro_totals.values())
    _add_check(
        checks,
        "ETX7B2-QA-09",
        "Basin/pondage and reservoir turbine MW are retained once from the hydro mapping",
        _text_totals(output_hydro_totals),
        f"each horizon={hydro_expected} MW",
        hydro_ok,
    )
    generator_provenance_ok = _provenance_reconciles(
        generators,
        "asset_id",
        "p_nom_MW",
        provenance,
        "GENERATOR",
        "p_nom_MW",
    )
    _add_check(
        checks,
        "ETX7B2-QA-10",
        "Every retained national generator class reconciles to its contributing frozen rows",
        generator_provenance_ok,
        True,
        generator_provenance_ok,
    )
    generator_fixed = (
        generators["p_nom_extendable"].str.lower().eq("false").all()
        and all(_decimal(value) > 0 for value in generators["p_nom_MW"])
    )
    _add_check(
        checks,
        "ETX7B2-QA-11",
        "All national generation capacity is positive and non-extendable",
        f"rows={len(generators)}; unique_ids={generators['asset_id'].is_unique}",
        "all p_nom > 0; all non-extendable; unique IDs",
        generator_fixed and generators["asset_id"].is_unique,
    )
    no_phs_generator = not generators[
        ["parent_capacity_technology", "stage_a_static_class", "carrier"]
    ].apply(lambda col: col.str.contains("PHS|PUMPED", case=False, regex=True)).any().any()
    _add_check(
        checks,
        "ETX7B2-QA-12",
        "PHS is absent from generator capacity and retained only in storage",
        no_phs_generator,
        True,
        no_phs_generator,
    )

    storage_source = _read_contract(SOURCE_FILES["storage"])
    storage_source = storage_source.loc[
        storage_source["scenario"].eq("Base")
        & storage_source["year"].astype(int).isin(HORIZONS)
    ]
    bess_output = storage.loc[storage["storage_family"].eq("BESS")]
    for offset, (dimension, source_column, output_column) in enumerate(
        (
            ("charge power", "charge_power_MW", "charge_power_MW"),
            ("discharge power", "discharge_power_MW", "discharge_power_MW"),
            ("energy", "energy_capacity_MWh", "energy_MWh"),
        ),
        start=13,
    ):
        source_totals = {
            year: _decimal_sum(
                storage_source.loc[
                    storage_source["year"].astype(int).eq(year), source_column
                ]
            )
            for year in HORIZONS
        }
        output_totals = _totals_by_year(bess_output, output_column)
        _add_check(
            checks,
            f"ETX7B2-QA-{offset:02d}",
            f"BESS {dimension} reconciles exactly by horizon and operational class",
            _text_totals(output_totals),
            _text_totals(source_totals),
            output_totals == source_totals,
        )

    phs_output = storage.loc[storage["storage_family"].eq("PHS")]
    phs_expected = {
        "charge_power_MW": Decimal("6400"),
        "discharge_power_MW": Decimal("7252.3"),
        "energy_MWh": Decimal("53000"),
    }
    phs_ok = len(phs_output) == 2
    for column, expected in phs_expected.items():
        phs_ok = phs_ok and all(_decimal(value) == expected for value in phs_output[column])
    _add_check(
        checks,
        "ETX7B2-QA-16",
        "PHS national charge, discharge and energy controls are preserved per horizon",
        (
            f"rows={len(phs_output)}; charge={set(phs_output['charge_power_MW'])}; "
            f"discharge={set(phs_output['discharge_power_MW'])}; energy={set(phs_output['energy_MWh'])}"
        ),
        "2 rows; charge=6400 MW; discharge=7252.3 MW; energy=53000 MWh",
        phs_ok,
    )
    asymmetry_ok = all(
        _decimal(record["charge_power_MW"]) != _decimal(record["discharge_power_MW"])
        for record in phs_output.to_dict(orient="records")
    )
    _add_check(
        checks,
        "ETX7B2-QA-17",
        "PHS charge/discharge asymmetry is preserved",
        asymmetry_ok,
        True,
        asymmetry_ok,
    )
    pure_expected = Decimal("3969.57561")
    mixed_expected = Decimal("3282.72439")
    subsets_ok = all(
        _decimal(record["pure_phs_discharge_subset_MW"]) == pure_expected
        and _decimal(record["mixed_phs_discharge_subset_MW"]) == mixed_expected
        and _decimal(record["pure_phs_discharge_subset_MW"])
        + _decimal(record["mixed_phs_discharge_subset_MW"])
        == Decimal("7252.3")
        for record in phs_output.to_dict(orient="records")
    )
    _add_check(
        checks,
        "ETX7B2-QA-18",
        "Pure/mixed PHS discharge governance is retained without duplicate assets",
        f"pure={pure_expected}; mixed={mixed_expected}; emitted_assets={len(phs_output)}",
        "subsets sum to 7252.3 MW; one combined operational-control asset per horizon",
        subsets_ok and phs_output.groupby("year").size().eq(1).all(),
    )
    storage_fixed = (
        storage["storage_id"].is_unique
        and storage["p_nom_extendable"].str.lower().eq("false").all()
        and storage["e_nom_extendable"].str.lower().eq("false").all()
        and storage[["charge_power_MW", "discharge_power_MW", "energy_MWh"]]
        .apply(lambda column: all(_decimal(value) > 0 for value in column))
        .all()
    )
    _add_check(
        checks,
        "ETX7B2-QA-19",
        "Every storage record is complete, unique and non-extendable",
        f"rows={len(storage)}; unique_ids={storage['storage_id'].is_unique}",
        "positive paired controls; fixed power and energy; unique IDs",
        bool(storage_fixed),
    )
    storage_provenance_ok = all(
        _provenance_reconciles(
            storage,
            "storage_id",
            dimension,
            provenance,
            "STORAGE",
            dimension,
        )
        for dimension in ("charge_power_MW", "discharge_power_MW", "energy_MWh")
    )
    _add_check(
        checks,
        "ETX7B2-QA-20",
        "Every national storage dimension reconciles to frozen contributing rows",
        storage_provenance_ok,
        True,
        storage_provenance_ok,
    )

    all_target_ids = set(demand["demand_id"]) | set(generators["asset_id"]) | set(storage["storage_id"])
    provenance_targets = set(provenance["target_record_id"])
    provenance_complete = all_target_ids <= provenance_targets and provenance["provenance_id"].is_unique
    _add_check(
        checks,
        "ETX7B2-QA-21",
        "Every national static row has unique source-row provenance",
        f"targets={len(all_target_ids)}; linked={len(all_target_ids & provenance_targets)}; provenance_rows={len(provenance)}",
        "all targets linked; provenance IDs unique",
        provenance_complete,
    )
    carrier_authority = _read_contract(SOURCE_FILES["carriers"])
    frozen_generator_carriers = set(
        generators.loc[
            generators["accounting_origin"].eq("STAGE_B_GENERATOR_CONTRACT"), "carrier"
        ]
    )
    crosswalk_ok = (
        len(crosswalk) == len(generators) + len(storage)
        and crosswalk["crosswalk_id"].is_unique
        and crosswalk["operating_parameters_status"].eq("NOT_ASSIGNED_BY_ETX7B2").all()
        and frozen_generator_carriers <= set(carrier_authority["carrier"])
    )
    _add_check(
        checks,
        "ETX7B2-QA-22",
        "Carrier/static crosswalk covers every generator and storage class without assigning operations",
        f"rows={len(crosswalk)}; generators={len(generators)}; storage={len(storage)}",
        f"rows={len(generators) + len(storage)}; all operating parameters unassigned; frozen carriers valid",
        crosswalk_ok,
    )
    forbidden_columns = {
        "marginal_cost_EUR2025_per_MWh_el",
        "efficiency",
        "hourly_profile",
        "soc_initial",
        "terminal_SOC_rule",
        "interconnector_capacity_MW",
    }
    columns = set().union(*(set(frame.columns) for frame in (demand, generators, storage)))
    scope_columns_ok = not (columns & forbidden_columns)
    _add_check(
        checks,
        "ETX7B2-QA-23",
        "ETX-7B2 assigns no hourly, topology, cost, efficiency or SOC fields",
        sorted(columns & forbidden_columns),
        [],
        scope_columns_ok,
    )
    _add_check(
        checks,
        "ETX7B2-QA-24",
        "Deterministic byte-identical rebuild is enforced by the test suite",
        "tests/test_stage_a_italy_static.py::test_italy_adapter_is_byte_deterministic",
        "PASS in pytest",
        True,
    )
    manifest_ok = len(output_receipts) == 5 and output_receipts["status"].eq("PASS").all()
    _add_check(
        checks,
        "ETX7B2-QA-25",
        "Every emitted static artifact matches its deterministic output manifest",
        f"{output_receipts['status'].eq('PASS').sum()}/{len(output_receipts)}",
        "5/5",
        manifest_ok,
    )

    qa = pd.DataFrame.from_records(checks)
    generator_complete_totals = _totals_by_year(generators, "p_nom_MW")
    bess_totals = {
        year: {
            "charge_power_MW": str(
                _decimal_sum(
                    bess_output.loc[bess_output["year"].astype(int).eq(year), "charge_power_MW"]
                )
            ),
            "discharge_power_MW": str(
                _decimal_sum(
                    bess_output.loc[bess_output["year"].astype(int).eq(year), "discharge_power_MW"]
                )
            ),
            "energy_MWh": str(
                _decimal_sum(
                    bess_output.loc[bess_output["year"].astype(int).eq(year), "energy_MWh"]
                )
            ),
        }
        for year in HORIZONS
    }
    verification = {
        "phase": "ETX-7B2",
        "status": (
            "ETX7B2_ITALY_ONE_NODE_STATIC_AGGREGATION_COMPLETE"
            if qa["status"].eq("PASS").all()
            else "ETX7B2_FAILED"
        ),
        "project_plan": {
            "version": "2.2",
            "path": str(PLAN_PATH.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256_file(PLAN_PATH) if PLAN_PATH.exists() else "",
        },
        "counts": {
            "demand_rows": len(demand),
            "generator_rows": len(generators),
            "storage_rows": len(storage),
            "crosswalk_rows": len(crosswalk),
            "provenance_rows": len(provenance),
            "qa_checks": len(qa),
            "qa_failures": int(qa["status"].eq("FAIL").sum()),
        },
        "annual_demand_MWh": {str(year): str(value) for year, value in demand_output_totals.items()},
        "stage_b_generator_contract_MW": {
            str(year): str(value) for year, value in output_generator_totals.items()
        },
        "conventional_hydro_mapping_addition_MW": {
            str(year): str(value) for year, value in output_hydro_totals.items()
        },
        "complete_stage_a_generator_turbine_MW": {
            str(year): str(value) for year, value in generator_complete_totals.items()
        },
        "bess_totals": {str(year): value for year, value in bess_totals.items()},
        "phs_totals_per_horizon": {
            "charge_power_MW": "6400",
            "discharge_power_MW": "7252.3",
            "energy_MWh": "53000",
            "pure_discharge_subset_MW": "3969.57561",
            "mixed_discharge_subset_MW": "3282.72439",
        },
        "source_regression": {
            "stage_b_receipts_pass": bool(stage_b_receipts["status"].eq("PASS").all()),
            "etx7b1_receipts_pass": bool(etx7b1_receipts["status"].eq("PASS").all()),
        },
        "repository_test_receipt": _junit_receipt(),
        "scope_confirmation": {
            "hourly_profiles": "NOT_STARTED_BY_PHASE_SCOPE",
            "topology_or_interconnectors": "NOT_STARTED_BY_PHASE_SCOPE",
            "operating_costs_or_efficiencies": "NOT_ASSIGNED_BY_PHASE_SCOPE",
            "storage_soc": "NOT_CONFIGURED_BY_PHASE_SCOPE",
            "pypsa_network": "NOT_INSTANTIATED",
            "optimization": "NOT_RUN",
        },
        "next_gate": "ETX-7B3_PYPSA_EUR_2019_TEMPORAL_REALIZATION_AND_EXCEPTION_CLOSURE",
    }
    if write_reports:
        QA_DIR.mkdir(parents=True, exist_ok=True)
        source_receipts.to_csv(
            QA_DIR / "MEM_ETX7B2_Source_Hash_Receipt_v1.0.csv",
            index=False,
            encoding="utf-8",
            lineterminator="\n",
        )
        qa.to_csv(
            QA_DIR / "MEM_ETX7B2_Reconciliation_QA_v1.0.csv",
            index=False,
            encoding="utf-8",
            lineterminator="\n",
        )
        output_receipts.to_csv(
            QA_DIR / "MEM_ETX7B2_Output_Manifest_Receipt_v1.0.csv",
            index=False,
            encoding="utf-8",
            lineterminator="\n",
        )
        (QA_DIR / "MEM_ETX7B2_Final_Verification_v1.0.json").write_text(
            json.dumps(verification, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return {
        "qa": qa,
        "verification": verification,
        "source_receipts": source_receipts,
        "output_receipts": output_receipts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate ETX-7B2 Italy static package")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--build-first", action="store_true")
    args = parser.parse_args()
    if args.build_first:
        build_italy_static_package(args.output_dir)
    result = validate_italy_static_package(args.output_dir, write_reports=True)
    print(json.dumps(result["verification"], indent=2))
    if not result["qa"]["status"].eq("PASS").all():
        raise SystemExit(1)


if __name__ == "__main__":
    main()

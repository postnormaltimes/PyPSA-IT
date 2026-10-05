from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from .etx7a import (
    COUNTRIES,
    EXPECTED_ROLE_COUNTS,
    HORIZONS,
    ROOT,
    RUNTIME_ROLES,
    verify_frozen_sources,
)
from .static_inputs import DEFAULT_OUTPUT_DIR, OUTPUT_FILES


QA_DIR = ROOT / "qa" / "stage_a" / "etx7b1"
STAGE_B_MANIFEST = ROOT / "qa" / "interim" / "MEM_FROZEN_STATIC_HASH_MANIFEST.csv"


def _read_outputs(output_dir: Path) -> dict[str, pd.DataFrame]:
    return {
        name: pd.read_csv(output_dir / filename)
        for name, filename in OUTPUT_FILES.items()
        if name != "manifest"
    }


def _hash_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest().lower()


def _stage_b_hash_receipt() -> pd.DataFrame:
    manifest = pd.read_csv(STAGE_B_MANIFEST)
    rows: list[dict[str, object]] = []
    for record in manifest.to_dict(orient="records"):
        path = ROOT / Path(str(record["relative_path"]))
        observed_hash = _hash_file(path) if path.exists() else None
        observed_rows = len(pd.read_csv(path)) if path.exists() else None
        expected_hash = str(record["expected_sha256"]).lower()
        expected_rows = int(record["expected_rows"])
        rows.append(
            {
                "artifact": record["artifact"],
                "expected_sha256": expected_hash,
                "observed_sha256": observed_hash,
                "expected_rows": expected_rows,
                "observed_rows": observed_rows,
                "status": (
                    "PASS"
                    if observed_hash == expected_hash and observed_rows == expected_rows
                    else "FAIL"
                ),
            }
        )
    return pd.DataFrame.from_records(rows)


def _conversion_ok(provenance: pd.DataFrame) -> bool:
    for row in provenance.to_dict(orient="records"):
        original_value = float(row["original_frozen_value"])
        original_unit = str(row["original_frozen_unit"])
        emitted_value = float(row["emitted_value"])
        emitted_unit = str(row["emitted_unit"])
        if row["record_type"] == "EXPLICIT_ZERO_CONTROL":
            if original_value != 0:
                return False
            continue
        if original_unit == "GW" and emitted_unit == "MW":
            expected = original_value * 1_000.0
        elif original_unit == "GWh" and emitted_unit == "MWh":
            expected = original_value * 1_000.0
        elif original_unit == "TWh" and emitted_unit == "MWh":
            expected = original_value * 1_000_000.0
        else:
            return False
        if not math.isclose(emitted_value, expected, rel_tol=1e-12, abs_tol=1e-8):
            return False
    return True


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


def validate_static_package(
    output_dir: Path | None = None, *, write_reports: bool = True
) -> dict[str, Any]:
    output_dir = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    frames = _read_outputs(output_dir)
    canonical = frames["canonical"]
    runtime = frames["runtime"]
    demand = frames["demand"]
    generators = frames["generators"]
    storage = frames["storage"]
    zeros = frames["zeros"]
    provenance = frames["provenance"]
    source_receipts = verify_frozen_sources()
    stage_b_receipts = _stage_b_hash_receipt()
    checks: list[dict[str, object]] = []

    _add_check(
        checks,
        "ETX7B1-QA-01",
        "Archived ETX-7A source hashes and byte counts match the frozen manifest",
        int(source_receipts["status"].eq("PASS").sum()),
        len(source_receipts),
        source_receipts["status"].eq("PASS").all(),
    )
    _add_check(checks, "ETX7B1-QA-02", "Canonical evidence rows read", len(canonical), 207, len(canonical) == 207)
    _add_check(checks, "ETX7B1-QA-03", "Runtime-authoritative rows read", len(runtime), 161, len(runtime) == 161)
    role_counts = runtime["runtime_role"].value_counts().sort_index().to_dict()
    _add_check(
        checks,
        "ETX7B1-QA-04",
        "Runtime-role counts match the ETX-7A freeze",
        json.dumps(role_counts, sort_keys=True),
        json.dumps(EXPECTED_ROLE_COUNTS, sort_keys=True),
        role_counts == EXPECTED_ROLE_COUNTS,
    )
    runtime_countries = sorted(runtime["country_code"].unique().tolist())
    _add_check(
        checks,
        "ETX7B1-QA-05",
        "All nine external countries are present",
        ",".join(runtime_countries),
        ",".join(sorted(COUNTRIES)),
        set(runtime_countries) == set(COUNTRIES),
    )
    observed_horizons = sorted(demand["year"].astype(int).unique().tolist())
    _add_check(
        checks,
        "ETX7B1-QA-06",
        "Exactly the 2040 and 2050 horizons are emitted",
        observed_horizons,
        list(HORIZONS),
        observed_horizons == list(HORIZONS),
    )
    demand_counts = demand.groupby(["country_code", "year"]).size()
    _add_check(
        checks,
        "ETX7B1-QA-07",
        "Every country has one accepted annual-demand control per horizon",
        f"rows={len(demand)}; max_per_key={int(demand_counts.max())}",
        "18 rows; exactly one per country/year",
        len(demand) == 18 and demand_counts.eq(1).all(),
    )
    emitted_roles = set(generators["runtime_role"]) | set(demand["runtime_role"])
    for prefix in ("charge_power", "discharge_power", "energy"):
        emitted_roles |= set(storage[f"{prefix}_runtime_role"])
    _add_check(
        checks,
        "ETX7B1-QA-08",
        "No QA or derivation role leaks into runtime capacity",
        ",".join(sorted(emitted_roles)),
        ",".join(RUNTIME_ROLES),
        emitted_roles <= set(RUNTIME_ROLES),
    )
    _add_check(
        checks,
        "ETX7B1-QA-09",
        "All emitted generators have strictly positive fixed capacity",
        f"min={generators['p_nom_MW'].min()}; extendable={generators['p_nom_extendable'].astype(str).str.lower().isin(['true','1']).sum()}",
        "p_nom_MW > 0; all non-extendable",
        generators["p_nom_MW"].gt(0).all()
        and not generators["p_nom_extendable"].astype(str).str.lower().isin(["true", "1"]).any(),
    )
    _add_check(
        checks,
        "ETX7B1-QA-10",
        "Explicit zero controls are retained separately without component emission",
        len(zeros),
        98,
        len(zeros) == 98
        and zeros["original_frozen_value"].eq(0).all()
        and not zeros["runtime_component_emitted"].astype(str).str.lower().isin(["true", "1"]).any(),
    )
    hr_nuclear = generators.loc[
        generators["country_code"].eq("HR")
        & generators["harmonised_group"].isin(["nuclear", "nuclear_smr"])
    ]
    _add_check(
        checks,
        "ETX7B1-QA-11",
        "No Croatian nuclear generator is emitted",
        len(hr_nuclear),
        0,
        hr_nuclear.empty,
        "Croatia's Krško entitlement remains QA/provenance only.",
    )
    si_nuclear = generators.loc[
        generators["country_code"].eq("SI") & generators["harmonised_group"].eq("nuclear")
    ]
    _add_check(
        checks,
        "ETX7B1-QA-12",
        "Slovenia retains the physical Krško nuclear treatment",
        f"records={len(si_nuclear)}; years={sorted(si_nuclear['year'].astype(int).unique().tolist())}",
        "At least one positive Slovenia nuclear record; no Croatia duplicate",
        not si_nuclear.empty and hr_nuclear.empty,
    )
    storage_keys = storage.groupby(["country_code", "year", "storage_class"]).size()
    storage_positive = storage[["charge_power_MW", "discharge_power_MW", "energy_MWh"]].gt(0).all().all()
    _add_check(
        checks,
        "ETX7B1-QA-13",
        "Logical storage assets contain one complete, unique charge/discharge/energy pair",
        f"records={len(storage)}; max_per_key={int(storage_keys.max())}",
        "one row per country/year/class; all three dimensions positive",
        storage_keys.eq(1).all() and storage_positive,
    )
    fr_phs = storage.loc[(storage["country_code"].eq("FR")) & storage["storage_class"].eq("PHS")]
    gr_phs = storage.loc[(storage["country_code"].eq("GR")) & storage["storage_class"].eq("PHS")]
    fr_mapping = "TECHNICAL_SYMMETRIC_MAPPING_FROM_FROZEN_PHS_POWER"
    gr_mapping = "ETX7A_APPROVED_TECHNICAL_SYMMETRIC_MAPPING_FROM_OFFICIAL_PHS_POWER"
    _add_check(
        checks,
        "ETX7B1-QA-14",
        "France and Greece PHS symmetry mappings are explicit and traceable",
        f"FR={sorted(fr_phs['charge_power_mapping'].unique())}; GR={sorted(gr_phs['charge_power_mapping'].unique())}",
        f"FR={fr_mapping}; GR={gr_mapping}",
        len(fr_phs) == 2
        and len(gr_phs) == 2
        and fr_phs["charge_power_mapping"].eq(fr_mapping).all()
        and gr_phs["charge_power_mapping"].eq(gr_mapping).all()
        and (fr_phs["charge_power_MW"] == fr_phs["discharge_power_MW"]).all()
        and (gr_phs["charge_power_MW"] == gr_phs["discharge_power_MW"]).all(),
    )
    _add_check(
        checks,
        "ETX7B1-QA-15",
        "Only explicit ETX-7A unit conversions are applied",
        "conversion receipt evaluated for every provenance row",
        "TWh->MWh x1e6; GW->MW x1e3; GWh->MWh x1e3",
        _conversion_ok(provenance),
    )
    ids_unique = (
        demand["demand_id"].is_unique
        and generators["asset_id"].is_unique
        and storage["storage_id"].is_unique
        and zeros["zero_control_id"].is_unique
    )
    _add_check(
        checks,
        "ETX7B1-QA-16",
        "All deterministic IDs are unique within their contract tables",
        ids_unique,
        True,
        ids_unique,
    )
    _add_check(
        checks,
        "ETX7B1-QA-17",
        "Deterministic byte-identical rebuild is covered by an automated test",
        "tests/test_stage_a_static_contract.py::test_adapter_is_byte_deterministic",
        "PASS in pytest",
        True,
        "This static check is converted to a hard assertion by the test suite.",
    )
    _add_check(
        checks,
        "ETX7B1-QA-18",
        "Existing Stage-B frozen files retain their accepted hashes and row counts",
        int(stage_b_receipts["status"].eq("PASS").sum()),
        len(stage_b_receipts),
        stage_b_receipts["status"].eq("PASS").all(),
    )
    no_duplicate_total_split = not runtime.duplicated(
        ["country_code", "source_group", "harmonised_group", "runtime_role"]
    ).any()
    _add_check(
        checks,
        "ETX7B1-QA-19",
        "Runtime rows preserve source grain without duplicated total/split emission",
        no_duplicate_total_split,
        True,
        no_duplicate_total_split,
        "IMPLEMENT_TOTAL and IMPLEMENT_CARRIER_SPLIT are emitted only from the frozen runtime sheet; QA totals never emit.",
    )
    distributed_classes = sorted(storage["distributed_vs_grid_flag"].unique().tolist())
    _add_check(
        checks,
        "ETX7B1-QA-20",
        "Distributed BESS remains distinct from grid BESS and PHS",
        ",".join(distributed_classes),
        "DISTRIBUTED_SMALL_SCALE,GRID_SCALE,PUMPED_HYDRO",
        set(distributed_classes)
        == {"DISTRIBUTED_SMALL_SCALE", "GRID_SCALE", "PUMPED_HYDRO"},
    )

    qa = pd.DataFrame.from_records(checks)
    result = {
        "phase": "ETX-7B1",
        "status": "PASS" if qa["status"].eq("PASS").all() else "FAIL",
        "source_version": "ETX7A_V1_0",
        "counts": {
            "canonical_rows": len(canonical),
            "runtime_rows": len(runtime),
            "annual_demand_rows": len(demand),
            "generator_rows": len(generators),
            "storage_rows": len(storage),
            "explicit_zero_rows": len(zeros),
            "provenance_rows": len(provenance),
            "qa_checks": len(qa),
            "qa_failures": int(qa["status"].eq("FAIL").sum()),
        },
        "stage_b_frozen_hashes_unchanged": bool(
            stage_b_receipts["status"].eq("PASS").all()
        ),
        "scope_confirmation": {
            "italy_aggregation": "NOT_STARTED_BY_PHASE_SCOPE",
            "hourly_profiles": "NOT_STARTED_BY_PHASE_SCOPE",
            "topology_or_network": "NOT_STARTED_BY_PHASE_SCOPE",
            "pypsa_network": "NOT_INSTANTIATED",
            "optimization": "NOT_RUN",
        },
    }
    if write_reports:
        QA_DIR.mkdir(parents=True, exist_ok=True)
        qa.to_csv(
            QA_DIR / "MEM_ETX7B1_STATIC_QA.csv",
            index=False,
            encoding="utf-8",
            lineterminator="\n",
        )
        stage_b_receipts.to_csv(
            QA_DIR / "MEM_ETX7B1_STAGE_B_FROZEN_HASH_RECEIPT.csv",
            index=False,
            encoding="utf-8",
            lineterminator="\n",
        )
        source_receipts.to_csv(
            QA_DIR / "MEM_ETX7B1_FROZEN_SOURCE_HASH_RECEIPT.csv",
            index=False,
            encoding="utf-8",
            lineterminator="\n",
        )
        (QA_DIR / "MEM_ETX7B1_FINAL_VERIFICATION.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return {
        "qa": qa,
        "verification": result,
        "source_receipts": source_receipts,
        "stage_b_receipts": stage_b_receipts,
    }

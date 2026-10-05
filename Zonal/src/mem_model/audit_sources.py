from __future__ import annotations

import os

import argparse
import importlib.metadata
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import xarray as xr

from .common import DECISIONS, QA, ROOT, STATIC, dump_json, ensure_output_dirs, sha256_file


PYPSA_EUR = Path(os.environ.get("MEM_RESEARCH_UPSTREAM_ROOT", str(ROOT / "data/research/upstream/pypsa-eur")))
OPSD = PYPSA_EUR / "data" / "opsd_electricity_demand" / "archive" / "2026-02-02" / "electricity_demand_opsd_raw.csv"
ENTSOE = PYPSA_EUR / "data" / "entsoe_electricity_demand" / "archive" / "2026-02-02" / "electricity_demand_entsoe_raw.csv"
RUNOFF = PYPSA_EUR / "data" / "country_runoff" / "archive" / "2025-08-13" / "era5-runoff-per-country.csv"
CUTOUT_2013 = PYPSA_EUR / "data" / "cutout" / "archive" / "v1.0" / "europe-2013-sarah3-era5.nc"
EXTERNAL_2013_1W = (
    PYPSA_EUR
    / "results"
    / "italy_dispatch_2013_1w_europe_local_64"
    / "boundary_audit"
    / "it_external_boundary_prices_hourly.csv"
)
NUCLEAR = ROOT.parent / "06_PYPSA" / "nuclear_pmax_2050_central.csv"


STATIC_EXPECTATIONS = {
    "MEM_generators_static_final.csv": (723, "81dae9397f8b53b2b3e689644435ab5f36ccf3282599eada5bc3facff76b10e0"),
    "MEM_carriers_static_final.csv": (19, "87363dd6cc400bfd3e05f8eb824a832443f37549766ae231daae2e56813f65e0"),
    "MEM_storage_static_final.csv": (105, "5ccf5ae31a80db0f0162c0ea6ad2881f21805176ae8a00427573eec715474798"),
    "MEM_Hydro_Static_Component_Mapping.csv": (35, "a2f4a22a9d7b00a55cdad7f690e25c0579065396756a848d0cb96a29eda7dd94"),
    "MEM_Interzonal_Static_Contract.csv": (120, "be590426cdeb00add24f4caef2b73cc527f503fa5c1c55be241448c6a3abb5bd"),
    "MEM_External_Interface_Static_Contract.csv": (20, "d5e19a4036c7f462bec07ac96109b885b1ffc0437dfc922fa47f3e21926770d2"),
    "MEM_Annual_Zonal_Demand_Contract.csv": (42, "e2d4e87ce7032de875f918772d28cfebde4d6381c69755449349349cd7c6f428"),
    "MEM_Hourly_Input_Source_Specification.csv": (10, "7c65ab1e88d5f3492a0f78cf666f160cbba2d4083237756d30896e54a2105eb7"),
    "MEM_PHS_Static_Runtime_Contract.csv": (7, "0ea51bc081e75e4e85bef9c4a41f2f70fd12bd9f4344c55beb8bf335964031fa"),
}


def _csv_rows(path: Path) -> int:
    return len(pd.read_csv(path))


def frozen_static_audit() -> tuple[pd.DataFrame, pd.DataFrame]:
    manifests: list[dict[str, object]] = []
    checks: list[dict[str, object]] = []
    for filename, (expected_rows, expected_hash) in STATIC_EXPECTATIONS.items():
        path = STATIC / filename
        exists = path.exists()
        observed_rows = _csv_rows(path) if exists else None
        observed_hash = sha256_file(path) if exists else None
        manifests.append(
            {
                "artifact": filename,
                "relative_path": str(path.relative_to(ROOT)),
                "bytes": path.stat().st_size if exists else None,
                "rows": observed_rows,
                "sha256": observed_hash,
                "expected_rows": expected_rows,
                "expected_sha256": expected_hash,
                "immutable_authority": "PRE_PYPSA_MODEL_INPUT_PACKAGE_V1",
                "status": "PASS" if exists and observed_rows == expected_rows and observed_hash == expected_hash else "FAIL",
            }
        )
        checks.extend(
            [
                {
                    "check_id": f"STATIC-{filename}-ROWS",
                    "description": "Frozen row count unchanged",
                    "observed": observed_rows,
                    "expected": expected_rows,
                    "status": "PASS" if observed_rows == expected_rows else "FAIL",
                },
                {
                    "check_id": f"STATIC-{filename}-HASH",
                    "description": "Frozen SHA-256 unchanged",
                    "observed": observed_hash,
                    "expected": expected_hash,
                    "status": "PASS" if observed_hash == expected_hash else "FAIL",
                },
            ]
        )

    generators = pd.read_csv(STATIC / "MEM_generators_static_final.csv")
    checks.append(
        {
            "check_id": "STATIC-GENERATORS-NONEXTENDABLE",
            "description": "Every frozen generator is non-extendable",
            "observed": int(generators["p_nom_extendable"].astype(str).str.lower().isin(["true", "1"]).sum()),
            "expected": 0,
            "status": "PASS"
            if not generators["p_nom_extendable"].astype(str).str.lower().isin(["true", "1"]).any()
            else "FAIL",
        }
    )
    return pd.DataFrame(manifests), pd.DataFrame(checks)


def _load_profile(path: Path, timestamp_column: str, year: int, value_columns: list[str]) -> dict[str, object]:
    if not path.exists():
        return {"source_exists": False}
    available = pd.read_csv(path, nrows=0).columns.tolist()
    selected = [timestamp_column] + [column for column in value_columns if column in available]
    frame = pd.read_csv(path, usecols=selected)
    timestamp = pd.to_datetime(frame[timestamp_column], utc=True, errors="coerce")
    mask = timestamp.dt.year.eq(year)
    subset = frame.loc[mask].copy()
    subset_timestamp = timestamp.loc[mask]
    return {
        "source_exists": True,
        "timestamp_rows": int(len(subset)),
        "valid_timestamps": int(subset_timestamp.notna().sum()),
        "duplicate_timestamps": int(subset_timestamp.duplicated().sum()),
        "first_timestamp": subset_timestamp.min().isoformat() if len(subset_timestamp) else None,
        "last_timestamp": subset_timestamp.max().isoformat() if len(subset_timestamp) else None,
        "nonnull": {column: int(subset[column].notna().sum()) for column in selected if column != timestamp_column},
        "columns_available": [column for column in value_columns if column in available],
    }


def _runoff_coverage(year: int) -> dict[str, object]:
    if not RUNOFF.exists():
        return {"exists": False, "rows": 0, "nonnull_it": 0}
    frame = pd.read_csv(RUNOFF, usecols=["time", "IT"])
    timestamp = pd.to_datetime(frame["time"], utc=True, errors="coerce")
    subset = frame.loc[timestamp.dt.year.eq(year)]
    return {"exists": True, "rows": int(len(subset)), "nonnull_it": int(subset["IT"].notna().sum())}


def chronology_assessment() -> pd.DataFrame:
    opsd_columns = ["IT", "IT_NORD", "IT_CNOR", "IT_CSUD", "IT_SUD", "IT_SICI", "IT_SARD", "IT_CALA"]
    opsd_2013 = _load_profile(OPSD, "utc_timestamp", 2013, opsd_columns)
    opsd_2019 = _load_profile(OPSD, "utc_timestamp", 2019, opsd_columns)
    entsoe_2019: dict[str, object] = {}
    # ENTSO-E's timestamp header has varied across archived exports; resolve it by position.
    if ENTSOE.exists():
        header = pd.read_csv(ENTSOE, nrows=0).columns.tolist()
        time_col = header[0]
        entsoe_2019 = _load_profile(ENTSOE, time_col, 2019, ["IT"])
    cutout_hours = None
    cutout_first = None
    cutout_last = None
    if CUTOUT_2013.exists():
        with xr.open_dataset(CUTOUT_2013, decode_times=True) as dataset:
            cutout_hours = int(dataset.sizes.get("time", 0))
            cutout_first = str(dataset["time"].values[0])
            cutout_last = str(dataset["time"].values[-1])

    rows = [
        {
            "candidate_id": "C2013_LOCAL_COMPLETE",
            "year": 2013,
            "expected_hours": 8760,
            "weather_cutout_local": CUTOUT_2013.exists(),
            "weather_cutout_bytes": CUTOUT_2013.stat().st_size if CUTOUT_2013.exists() else None,
            "weather_cutout_hours": cutout_hours,
            "weather_cutout_first": cutout_first,
            "weather_cutout_last": cutout_last,
            "load_source": str(OPSD),
            "load_timestamp_rows": opsd_2013.get("timestamp_rows"),
            "load_IT_nonnull_hours": (opsd_2013.get("nonnull") or {}).get("IT"),
            "load_duplicate_timestamps": opsd_2013.get("duplicate_timestamps"),
            "zonal_temporal_shape": "SIX_LEGACY_OPSD_ZONES_AVAILABLE_BUT_NO_CALA; SCRATCH_USED_NATIONAL_IT_SHAPE",
            "runoff_source": str(RUNOFF),
            "runoff_rows": _runoff_coverage(2013)["rows"],
            "external_price_full_year_available": False,
            "reproducibility": "HIGH_LOCAL_FOR_WEATHER_LOAD; EXTERNAL_PRICE_INCOMPLETE",
            "age_fit": "WEAK_RELATIVE_TO_2019_2024_HISTORICAL_BASELINE",
            "blocking_issues": "FULL_YEAR_EXTERNAL_PRICE_METHOD_NOT_APPROVED; ZONAL_LOAD_SHAPE_METHOD_REQUIRES_APPROVAL",
            "candidate_status": "TECHNICALLY_FEASIBLE_NOT_APPROVED",
        },
        {
            "candidate_id": "C2019_PREFERRED_NEWER",
            "year": 2019,
            "expected_hours": 8760,
            "weather_cutout_local": False,
            "weather_cutout_bytes": None,
            "weather_cutout_hours": None,
            "weather_cutout_first": None,
            "weather_cutout_last": None,
            "load_source": f"{OPSD}|{ENTSOE}",
            "load_timestamp_rows": entsoe_2019.get("timestamp_rows"),
            "load_IT_nonnull_hours": (entsoe_2019.get("nonnull") or {}).get("IT"),
            "load_duplicate_timestamps": entsoe_2019.get("duplicate_timestamps"),
            "zonal_temporal_shape": "NO_COMPLETE_ACCEPTED_SEVEN_ZONE_2019_SHAPE_IDENTIFIED",
            "runoff_source": str(RUNOFF),
            "runoff_rows": _runoff_coverage(2019)["rows"],
            "external_price_full_year_available": False,
            "reproducibility": "PARTIAL_LOCAL; WEATHER CUTOUT ABSENT",
            "age_fit": "PREFERRED_FIRST_YEAR_OF_FROZEN_2019_2024_HISTORICAL_WINDOW",
            "blocking_issues": "LOCAL_WEATHER_CUTOUT_ABSENT; AVAILABLE OPSD/ENTSOE ITALY LOAD HAS 8759 NONNULL HOURS WITH 2019-01-01T00:00Z BLANK; EXTERNAL_PRICE_METHOD_NOT_APPROVED",
            "candidate_status": "PREFERRED_CONCEPTUALLY_NOT_CURRENTLY_READY",
        },
    ]
    return pd.DataFrame(rows)


def external_price_assessment() -> pd.DataFrame:
    coverage = ""
    hours = 0
    if EXTERNAL_2013_1W.exists():
        sample = pd.read_csv(EXTERNAL_2013_1W)
        coverage = "|".join(sorted(sample["external_country"].dropna().astype(str).unique()))
        hours = pd.to_datetime(sample["snapshot"], errors="coerce").nunique()
    return pd.DataFrame(
        [
            {
                "candidate_id": "E1_EXISTING_MEM_PYPSA_EUR_BOUNDARY_WORKFLOW",
                "method": "Generate neighbouring-market prices with the existing wider-European PyPSA-Eur boundary workflow",
                "local_evidence": str(EXTERNAL_2013_1W),
                "observed_local_market_coverage": coverage,
                "observed_local_unique_hours": hours,
                "required_markets": "FR|CH|AT|SI|ME|GR|TN|MT",
                "corsica_price_required": False,
                "strength": "Scenario-coherent price-taking boundary architecture and reproducible code path",
                "limitation": "Only short 2013 artifacts are local; a selected-chronology full-year upstream run/export is required; TN/MT coverage unproven",
                "status": "CANDIDATE_REQUIRES_USER_APPROVAL_AND_FULL_YEAR_EXPORT",
            },
            {
                "candidate_id": "E2_OFFICIAL_OBSERVED_WITH_FUTURE_TRANSFORM",
                "method": "Official observed day-ahead prices plus an explicit 2040/2050 transformation",
                "local_evidence": "NO_COMPLETE_APPROVED_EIGHT_MARKET_SERIES_IDENTIFIED",
                "observed_local_market_coverage": "NONE_COMPLETE",
                "observed_local_unique_hours": 0,
                "required_markets": "FR|CH|AT|SI|ME|GR|TN|MT",
                "corsica_price_required": False,
                "strength": "Observed market basis and transparent empirical chronology",
                "limitation": "Complete sources and future transformation are not selected; non-EU market coverage may differ",
                "status": "CANDIDATE_REQUIRES_USER_APPROVAL_AND_ACQUISITION",
            },
            {
                "candidate_id": "E3_SHORT_2013_SCHEMA_REGRESSION_ONLY",
                "method": "Existing 24-hour/168-hour 2013 boundary artifacts",
                "local_evidence": str(EXTERNAL_2013_1W),
                "observed_local_market_coverage": coverage,
                "observed_local_unique_hours": hours,
                "required_markets": "FR|CH|AT|SI|ME|GR|TN|MT",
                "corsica_price_required": False,
                "strength": "Useful for schema, sign and smoke-fixture regression",
                "limitation": "Not full year; must never be tiled; TN/MT must not be imputed by composite",
                "status": "DIAGNOSTIC_ONLY_NOT_ELIGIBLE_AS_BASELINE",
            },
        ]
    )


def runtime_assumption_register() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ("RA-001", "BESS terminal SOC", "CYCLIC_ANNUAL", "PROJECT_RUNTIME_ASSUMPTION", "APPROVAL_REQUIRED"),
            ("RA-002", "PHS terminal SOC", "CYCLIC_ANNUAL", "PROJECT_RUNTIME_ASSUMPTION", "APPROVAL_REQUIRED"),
            ("RA-003", "PHS operational energy", "53000 MWh Method C", "TERNA_CONTROL_PLUS_PROJECT_DERIVATION", "STATIC_FROZEN"),
            ("RA-004", "PHS pump power", "6400 MW", "TERNA_OPERATIONAL_CONTROL_PLUS_ALLOCATION", "STATIC_FROZEN"),
            ("RA-005", "PHS discharge power", "7252.3 MW NET", "TERNA_2024_CONTROL", "STATIC_FROZEN"),
            ("RA-006", "PHS round-trip efficiency", "0.75", "PROJECT_STATIC_ASSUMPTION", "STATIC_FROZEN"),
            ("RA-007", "Conventional hydro e_nom/max-hours", "Screen PyPSA-Eur proxy; reconcile before use", "PROJECT_PROXY_CANDIDATE", "REQUIRES_VALIDATION"),
            ("RA-008", "Physical HPHS energy", "626262.056948 MWh", "PHYSICAL_EVIDENCE_ONLY", "EXCLUDED_FROM_E_NOM"),
            ("RA-009", "Physical HDAM energy", "5748189.091752 MWh", "PHYSICAL_EVIDENCE_ONLY", "EXCLUDED_FROM_E_NOM"),
            ("RA-010", "Thermal maintenance", "Deterministic stable-hash staggering using BLK-005", "PROJECT_DERIVATION", "IMPLEMENTATION_READY"),
            ("RA-011", "Nuclear availability", "BLK-007 profile regression", "PROJECT_DERIVATION", "IMPLEMENTATION_READY"),
            ("RA-012", "Load shedding VOLL", "15000 EUR/MWh", "PROJECT_RUNTIME_CANDIDATE", "APPROVAL_REQUIRED"),
            ("RA-013", "External transaction toll", "0.001 EUR/MWh", "PROJECT_RUNTIME_CANDIDATE", "APPROVAL_REQUIRED"),
            ("RA-014", "Conventional hydro turbine efficiency", "0.90", "PROJECT_RUNTIME_CANDIDATE", "APPROVAL_REQUIRED"),
            ("RA-015", "Internal transport efficiency", "1.0", "FROZEN_ACCOUNTING_GUARDRAIL", "STATIC_FROZEN"),
            ("RA-016", "External link efficiency", "1.0 until perimeter evidence supports losses", "PROJECT_RUNTIME_CANDIDATE", "APPROVAL_REQUIRED"),
            ("RA-017", "0.85 Slow CDP factor as hourly availability", "PROHIBITED", "ACCOUNTING_GUARDRAIL", "STATIC_FROZEN"),
            ("RA-018", "Historical CF as hourly availability", "PROHIBITED", "ACCOUNTING_GUARDRAIL", "STATIC_FROZEN"),
            (
                "RA-019",
                "Within-zone pure/mixed PHS state split",
                "Separate states; split frozen zonal energy and pump power proportional to frozen pure/mixed discharge MW",
                "PROJECT_RUNTIME_ASSUMPTION",
                "APPROVAL_REQUIRED",
            ),
        ],
        columns=["assumption_id", "parameter", "candidate_or_control", "evidence_class", "status"],
    )


def _write_markdown(chronology: pd.DataFrame, prices: pd.DataFrame) -> None:
    chronology_text = chronology.to_markdown(index=False)
    price_text = prices.to_markdown(index=False)
    (DECISIONS / "MEM_CHRONOLOGY_DECISION_PACKET.md").write_text(
        "# MEM chronology decision packet\n\n"
        "Status: `REQUIRES_USER_APPROVAL`\n\n"
        "No chronology or hourly profile is frozen by this packet. A candidate must provide one complete UTC year with no silent timestamp repair.\n\n"
        f"{chronology_text}\n\n"
        "## Recommendation\n\n"
        "2019 is methodologically preferred because it lies inside the frozen 2019-2024 evidence window, but it is not currently implementation-ready: no local 2019 weather cutout exists and the available ENTSO-E Italy series has only 8,759 populated hours. "
        "2013 is locally reproducible for weather and national load, but its age and the lack of an approved full-year external-price series must be consciously accepted. "
        "The implementation therefore recommends either approving 2013 as the first baseline with national load shape normalized to frozen zonal annual controls, or authorizing targeted closure of the 2019 cutout and one-hour load gap before approval.\n",
        encoding="utf-8",
    )
    (DECISIONS / "MEM_EXTERNAL_PRICE_DECISION_PACKET.md").write_text(
        "# MEM external-price decision packet\n\n"
        "Status: `REQUIRES_USER_APPROVAL`\n\n"
        "CORS is a zero-injection commercial hub and has no price series. Short 2013 artifacts may be used for schema/sign regression only; they may not be tiled. TN and MT may not be filled with an undocumented composite.\n\n"
        f"{price_text}\n\n"
        "## Recommendation\n\n"
        "Prefer E1 if the project is willing to perform and archive a one-off, full-year wider-European price export for the approved chronology and future assumptions. "
        "Prefer E2 if official observed price evidence and a transparent scenario transformation are desired. Neither method is silently selected here.\n",
        encoding="utf-8",
    )


def run() -> None:
    ensure_output_dirs()
    manifest, checks = frozen_static_audit()
    chronology = chronology_assessment()
    prices = external_price_assessment()
    assumptions = runtime_assumption_register()
    manifest.to_csv(QA / "MEM_FROZEN_STATIC_HASH_MANIFEST.csv", index=False)
    checks.to_csv(QA / "MEM_FROZEN_STATIC_REGRESSION_QA.csv", index=False)
    chronology.to_csv(DECISIONS / "MEM_CHRONOLOGY_CANDIDATE_ASSESSMENT.csv", index=False)
    prices.to_csv(DECISIONS / "MEM_EXTERNAL_PRICE_CANDIDATE_ASSESSMENT.csv", index=False)
    assumptions.to_csv(DECISIONS / "MEM_RUNTIME_ASSUMPTION_APPROVAL_REGISTER.csv", index=False)
    _write_markdown(chronology, prices)

    versions = {}
    for package in ("pypsa", "atlite", "linopy", "pandas", "numpy", "xarray", "highspy"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    dump_json(
        QA / "MEM_INTERIM_IMPLEMENTATION_GATE_STATUS.json",
        {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "python": platform.python_version(),
            "python_executable": sys.executable,
            "package_versions": versions,
            "frozen_static_regression": "PASS" if checks["status"].eq("PASS").all() else "FAIL",
            "chronology_gate": "REQUIRES_USER_APPROVAL",
            "external_price_gate": "REQUIRES_USER_APPROVAL",
            "runtime_assumption_gate": "REQUIRES_USER_APPROVAL",
            "accepted_hourly_profiles": 0,
            "annual_networks_built": 0,
            "smoke_optimizations_run": 0,
            "full_year_optimizations_run": 0,
        },
    )
    if checks["status"].eq("FAIL").any():
        raise AssertionError(checks.loc[checks["status"].eq("FAIL")].to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit frozen MEM inputs and create the two approval decision packets")
    parser.parse_args()
    run()


if __name__ == "__main__":
    main()

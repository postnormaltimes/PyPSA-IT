from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path
from typing import Any

import pandas as pd

from .etx7a import (
    ETX7A_VERSION,
    HORIZONS,
    ROOT,
    WORKBOOK_NAME,
    read_canonical_evidence,
    read_runtime_inputs,
    verify_frozen_sources,
)


DEFAULT_OUTPUT_DIR = ROOT / "stage_a_inputs" / "etx7a_v1_0" / "static"
OUTPUT_FILES = {
    "canonical": "MEM_ETX7A_Canonical_Evidence_v1.0.csv",
    "runtime": "MEM_ETX7A_Runtime_Rows_v1.0.csv",
    "demand": "MEM_ETX7A_Annual_Demand_Static_v1.0.csv",
    "generators": "MEM_ETX7A_Generators_Static_v1.0.csv",
    "storage": "MEM_ETX7A_Storage_Static_v1.0.csv",
    "zeros": "MEM_ETX7A_Explicit_Zero_Controls_v1.0.csv",
    "provenance": "MEM_ETX7A_Static_Provenance_v1.0.csv",
    "manifest": "MEM_ETX7A_Static_Output_Manifest_v1.0.csv",
}

STORAGE_GROUPS = {
    "phs_charge_power",
    "phs_discharge_power",
    "phs_energy",
    "bess_grid_power",
    "bess_grid_energy",
    "bess_distributed_power",
    "bess_distributed_energy",
}

STORAGE_CLASSES = {
    "PHS": {
        "charge": "phs_charge_power",
        "discharge": "phs_discharge_power",
        "energy": "phs_energy",
        "flag": "PUMPED_HYDRO",
    },
    "BESS_GRID": {
        "power": "bess_grid_power",
        "energy": "bess_grid_energy",
        "flag": "GRID_SCALE",
    },
    "BESS_DISTRIBUTED": {
        "power": "bess_distributed_power",
        "energy": "bess_distributed_energy",
        "flag": "DISTRIBUTED_SMALL_SCALE",
    },
}

CARRIER_MAP = {
    "solar_pv": "SOLAR_PV",
    "solar_csp": "SOLAR_CSP",
    "wind_onshore": "WIND_ONSHORE",
    "wind_offshore": "WIND_OFFSHORE",
    "hydro_non_phs": "HYDRO_NON_PHS",
    "geothermal": "GEOTHERMAL",
    "nuclear": "NUCLEAR",
    "nuclear_smr": "NUCLEAR_SMR",
    "coal_lignite": "COAL_LIGNITE",
    "bioenergy_biogas": "BIOENERGY_BIOGAS",
    "bioenergy_biomass": "BIOENERGY_BIOMASS",
    "bioenergy_biomass_biogas": "BIOENERGY_BIOMASS_BIOGAS",
    "thermal_methane_gas": "METHANE_GAS",
    "thermal_lowcarbon_gas_h2": "LOWCARBON_GAS_H2",
    "thermal_oil_liquid": "OIL_LIQUID",
    "thermal_backup_other": "THERMAL_BACKUP_OTHER",
    "thermal_other_chp": "THERMAL_OTHER_CHP",
    "waste_chp": "WASTE_CHP",
    "marine_other": "MARINE_OTHER",
}


def _safe_id(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value).strip()).strip("_")
    return text.upper() or "UNSPECIFIED"


def _normalise_scalar(value: object) -> object:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return value


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(
        path,
        index=False,
        encoding="utf-8",
        lineterminator="\n",
        na_rep="",
        float_format="%.17g",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _metadata(row: pd.Series, year: int) -> dict[str, object]:
    return {
        "selected_base": row["selected_base"],
        "runtime_role": row["runtime_role"],
        "source_id": row[f"source_id_{year}"],
        "evidence_class": row[f"evidence_{year}"],
        "quality": row[f"quality_{year}"],
        "source_workbook": row["source_workbook"],
        "source_sheet": row["source_sheet"],
        "source_row": row["source_row"],
        "etx7a_workbook": WORKBOOK_NAME,
        "etx7a_sheet": row["etx7a_sheet"],
        "etx7a_workbook_row": int(row["etx7a_workbook_row"]),
        "canonical_etx7a_workbook_row": int(row["canonical_etx7a_workbook_row"]),
        "mapping_note": _normalise_scalar(row.get("mapping_note", "")),
        "original_frozen_value": row[f"value_{year}"],
        "original_frozen_unit": row["unit"],
        "etx7a_version": ETX7A_VERSION,
    }


def _build_demand(runtime: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    demand_rows = runtime.loc[runtime["harmonised_group"].eq("demand")]
    for _, source in demand_rows.iterrows():
        for year in HORIZONS:
            value = float(source[f"value_{year}"])
            metadata = _metadata(source, year)
            rows.append(
                {
                    "demand_id": f"ETX7A_V1_0_{year}_{source['country_code']}_ANNUAL_DEMAND",
                    "country_code": source["country_code"],
                    "country": source["country"],
                    "year": year,
                    "annual_demand_TWh": value,
                    "annual_demand_MWh": value * 1_000_000.0,
                    **metadata,
                    "status": "FROZEN_ETX7A_STATIC_CONTROL",
                }
            )
    frame = pd.DataFrame.from_records(rows)
    return frame.sort_values(["year", "country_code"], kind="stable").reset_index(drop=True)


def _build_generators(runtime: pd.DataFrame) -> pd.DataFrame:
    candidates = runtime.loc[
        ~runtime["harmonised_group"].isin(STORAGE_GROUPS | {"demand"})
    ].copy()
    if not candidates["unit"].eq("GW").all():
        bad = candidates.loc[~candidates["unit"].eq("GW"), ["harmonised_group", "unit"]]
        raise ValueError(f"Generator rows have non-GW units: {bad.to_dict(orient='records')}")

    rows: list[dict[str, object]] = []
    for _, source in candidates.iterrows():
        carrier = CARRIER_MAP.get(str(source["harmonised_group"]))
        if carrier is None:
            raise ValueError(
                f"No static carrier mapping for {source['harmonised_group']} "
                f"(ETX-7A row {source['etx7a_workbook_row']})"
            )
        for year in HORIZONS:
            value = float(source[f"value_{year}"])
            if value <= 0:
                continue
            asset_id = "_".join(
                [
                    "ETX7A_V1_0",
                    str(year),
                    str(source["country_code"]),
                    _safe_id(source["harmonised_group"]),
                    _safe_id(source["source_group"]),
                    f"R{int(source['etx7a_workbook_row'])}",
                ]
            )
            rows.append(
                {
                    "asset_id": asset_id,
                    "country_code": source["country_code"],
                    "country": source["country"],
                    "year": year,
                    "source_category": source["category"],
                    "source_group": source["source_group"],
                    "harmonised_group": source["harmonised_group"],
                    "mapped_carrier": carrier,
                    "p_nom_MW": value * 1_000.0,
                    "p_nom_extendable": False,
                    **_metadata(source, year),
                    "status": "FROZEN_ETX7A_STATIC_INPUT",
                }
            )
    frame = pd.DataFrame.from_records(rows)
    return frame.sort_values(
        ["year", "country_code", "harmonised_group", "source_group", "asset_id"],
        kind="stable",
    ).reset_index(drop=True)


def _single_row(
    rows: pd.DataFrame, group: str, *, required: bool = True
) -> pd.Series | None:
    matched = rows.loc[rows["harmonised_group"].eq(group)]
    if matched.empty and not required:
        return None
    if len(matched) != 1:
        raise ValueError(f"Expected one {group} row, found {len(matched)}")
    return matched.iloc[0]


def _dimension_metadata(
    source: pd.Series, year: int, prefix: str, mapping: str
) -> dict[str, object]:
    return {
        f"{prefix}_source_id": source[f"source_id_{year}"],
        f"{prefix}_evidence_class": source[f"evidence_{year}"],
        f"{prefix}_quality": source[f"quality_{year}"],
        f"{prefix}_source_workbook": source["source_workbook"],
        f"{prefix}_source_sheet": source["source_sheet"],
        f"{prefix}_source_row": source["source_row"],
        f"{prefix}_etx7a_workbook_row": int(source["etx7a_workbook_row"]),
        f"{prefix}_canonical_etx7a_workbook_row": int(
            source["canonical_etx7a_workbook_row"]
        ),
        f"{prefix}_original_frozen_value": source[f"value_{year}"],
        f"{prefix}_original_frozen_unit": source["unit"],
        f"{prefix}_runtime_role": source["runtime_role"],
        f"{prefix}_mapping": mapping,
    }


def _build_storage(runtime: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (country_code, country, selected_base), country_rows in runtime.groupby(
        ["country_code", "country", "selected_base"], sort=True, dropna=False
    ):
        for storage_class, groups in STORAGE_CLASSES.items():
            relevant = country_rows.loc[
                country_rows["harmonised_group"].isin(
                    {value for key, value in groups.items() if key != "flag"}
                )
            ]
            if relevant.empty:
                continue
            for year in HORIZONS:
                if storage_class == "PHS":
                    discharge_source = _single_row(relevant, str(groups["discharge"]))
                    energy_source = _single_row(relevant, str(groups["energy"]))
                    charge_source = _single_row(
                        relevant, str(groups["charge"]), required=False
                    )
                    discharge_value = float(discharge_source[f"value_{year}"])
                    energy_value = float(energy_source[f"value_{year}"])
                    if charge_source is None:
                        if country_code != "FR":
                            raise ValueError(
                                f"Missing PHS charge row outside approved FR mapping: {country_code}"
                            )
                        charge_source = discharge_source
                        charge_value = discharge_value
                        charge_mapping = (
                            "TECHNICAL_SYMMETRIC_MAPPING_FROM_FROZEN_PHS_POWER"
                        )
                    else:
                        charge_value = float(charge_source[f"value_{year}"])
                        charge_mapping = "DIRECT_ETX7A_FROZEN_VALUE"
                        if country_code == "GR":
                            charge_mapping = (
                                "ETX7A_APPROVED_TECHNICAL_SYMMETRIC_MAPPING_"
                                "FROM_OFFICIAL_PHS_POWER"
                            )
                    discharge_mapping = "DIRECT_ETX7A_FROZEN_VALUE"
                    energy_mapping = "DIRECT_ETX7A_FROZEN_VALUE"
                else:
                    power_source = _single_row(relevant, str(groups["power"]))
                    energy_source = _single_row(relevant, str(groups["energy"]))
                    discharge_source = power_source
                    charge_source = power_source
                    charge_value = float(power_source[f"value_{year}"])
                    discharge_value = charge_value
                    energy_value = float(energy_source[f"value_{year}"])
                    charge_mapping = "TECHNICAL_SYMMETRIC_MAPPING_FROM_FROZEN_BESS_POWER"
                    discharge_mapping = charge_mapping
                    energy_mapping = "DIRECT_ETX7A_FROZEN_VALUE"

                values = (charge_value, discharge_value, energy_value)
                if all(value == 0 for value in values):
                    continue
                if any(value <= 0 for value in values):
                    raise ValueError(
                        f"Incomplete non-zero storage pair {country_code}/{year}/{storage_class}: "
                        f"charge={charge_value}, discharge={discharge_value}, energy={energy_value}"
                    )
                storage_id = f"ETX7A_V1_0_{year}_{country_code}_{storage_class}"
                mapping_notes = sorted(
                    {
                        str(value)
                        for value in relevant["mapping_note"].dropna().tolist()
                        if str(value).strip()
                    }
                )
                rows.append(
                    {
                        "storage_id": storage_id,
                        "country_code": country_code,
                        "country": country,
                        "year": year,
                        "storage_class": storage_class,
                        "charge_power_MW": charge_value * 1_000.0,
                        "discharge_power_MW": discharge_value * 1_000.0,
                        "energy_MWh": energy_value * 1_000.0,
                        "p_nom_extendable": False,
                        "e_nom_extendable": False,
                        "selected_base": selected_base,
                        "distributed_vs_grid_flag": groups["flag"],
                        **_dimension_metadata(
                            charge_source, year, "charge_power", charge_mapping
                        ),
                        **_dimension_metadata(
                            discharge_source, year, "discharge_power", discharge_mapping
                        ),
                        **_dimension_metadata(
                            energy_source, year, "energy", energy_mapping
                        ),
                        "mapping_note": " | ".join(mapping_notes),
                        "etx7a_version": ETX7A_VERSION,
                        "status": "FROZEN_ETX7A_STATIC_INPUT",
                    }
                )
    frame = pd.DataFrame.from_records(rows)
    return frame.sort_values(
        ["year", "country_code", "storage_class"], kind="stable"
    ).reset_index(drop=True)


def _zero_control_type(group: str) -> str:
    if group == "demand":
        return "ANNUAL_DEMAND"
    if group in STORAGE_GROUPS:
        return "STORAGE_DIMENSION"
    return "GENERATOR_CAPACITY"


def _build_zero_controls(runtime: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for _, source in runtime.iterrows():
        for year in HORIZONS:
            value = float(source[f"value_{year}"])
            if value != 0:
                continue
            zero_id = "_".join(
                [
                    "ETX7A_ZERO",
                    str(year),
                    str(source["country_code"]),
                    _safe_id(source["harmonised_group"]),
                    _safe_id(source["source_group"]),
                    f"R{int(source['etx7a_workbook_row'])}",
                ]
            )
            rows.append(
                {
                    "zero_control_id": zero_id,
                    "country_code": source["country_code"],
                    "country": source["country"],
                    "year": year,
                    "control_type": _zero_control_type(str(source["harmonised_group"])),
                    "source_category": source["category"],
                    "source_group": source["source_group"],
                    "harmonised_group": source["harmonised_group"],
                    **_metadata(source, year),
                    "runtime_component_emitted": False,
                    "status": "EXPLICIT_ZERO_CONTROL_RETAINED_NO_COMPONENT",
                }
            )
    frame = pd.DataFrame.from_records(rows)
    return frame.sort_values(
        ["year", "country_code", "harmonised_group", "source_group", "zero_control_id"],
        kind="stable",
    ).reset_index(drop=True)


def _build_provenance(
    demand: pd.DataFrame,
    generators: pd.DataFrame,
    storage: pd.DataFrame,
    zeros: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    simple_specs = [
        ("ANNUAL_DEMAND", demand, "demand_id", "annual_demand_MWh"),
        ("GENERATOR", generators, "asset_id", "p_nom_MW"),
        ("EXPLICIT_ZERO_CONTROL", zeros, "zero_control_id", "original_frozen_value"),
    ]
    for record_type, frame, id_col, emitted_value_col in simple_specs:
        for row in frame.to_dict(orient="records"):
            rows.append(
                {
                    "record_type": record_type,
                    "record_id": row[id_col],
                    "physical_dimension": (
                        "annual_demand" if record_type == "ANNUAL_DEMAND" else "capacity"
                    ),
                    "country_code": row["country_code"],
                    "year": row["year"],
                    "emitted_value": row[emitted_value_col],
                    "emitted_unit": (
                        "MWh" if record_type == "ANNUAL_DEMAND" else "MW"
                    ),
                    "original_frozen_value": row["original_frozen_value"],
                    "original_frozen_unit": row["original_frozen_unit"],
                    "source_id": row["source_id"],
                    "evidence_class": row["evidence_class"],
                    "quality": row["quality"],
                    "selected_base": row["selected_base"],
                    "runtime_role": row["runtime_role"],
                    "source_workbook": row["source_workbook"],
                    "source_sheet": row["source_sheet"],
                    "source_row": row["source_row"],
                    "etx7a_workbook": row["etx7a_workbook"],
                    "etx7a_sheet": row["etx7a_sheet"],
                    "etx7a_workbook_row": row["etx7a_workbook_row"],
                    "canonical_etx7a_workbook_row": row[
                        "canonical_etx7a_workbook_row"
                    ],
                    "etx7b_runtime_mapping": (
                        "NO_COMPONENT" if record_type == "EXPLICIT_ZERO_CONTROL" else "DIRECT"
                    ),
                }
            )

    for row in storage.to_dict(orient="records"):
        for dimension, value_col, unit in (
            ("charge_power", "charge_power_MW", "MW"),
            ("discharge_power", "discharge_power_MW", "MW"),
            ("energy", "energy_MWh", "MWh"),
        ):
            rows.append(
                {
                    "record_type": "STORAGE",
                    "record_id": row["storage_id"],
                    "physical_dimension": dimension,
                    "country_code": row["country_code"],
                    "year": row["year"],
                    "emitted_value": row[value_col],
                    "emitted_unit": unit,
                    "original_frozen_value": row[f"{dimension}_original_frozen_value"],
                    "original_frozen_unit": row[f"{dimension}_original_frozen_unit"],
                    "source_id": row[f"{dimension}_source_id"],
                    "evidence_class": row[f"{dimension}_evidence_class"],
                    "quality": row[f"{dimension}_quality"],
                    "selected_base": row["selected_base"],
                    "runtime_role": row[f"{dimension}_runtime_role"],
                    "source_workbook": row[f"{dimension}_source_workbook"],
                    "source_sheet": row[f"{dimension}_source_sheet"],
                    "source_row": row[f"{dimension}_source_row"],
                    "etx7a_workbook": WORKBOOK_NAME,
                    "etx7a_sheet": "03_RUNTIME_INPUTS",
                    "etx7a_workbook_row": row[f"{dimension}_etx7a_workbook_row"],
                    "canonical_etx7a_workbook_row": row[
                        f"{dimension}_canonical_etx7a_workbook_row"
                    ],
                    "etx7b_runtime_mapping": row[f"{dimension}_mapping"],
                }
            )
    frame = pd.DataFrame.from_records(rows)
    return frame.sort_values(
        ["record_type", "year", "country_code", "record_id", "physical_dimension"],
        kind="stable",
    ).reset_index(drop=True)


def _build_manifest(output_dir: Path, frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for name in sorted(key for key in frames if key != "manifest"):
        filename = OUTPUT_FILES[name]
        path = output_dir / filename
        rows.append(
            {
                "artifact": filename,
                "relative_path": f"stage_a_inputs/etx7a_v1_0/static/{filename}",
                "bytes": path.stat().st_size,
                "rows": len(frames[name]),
                "sha256": _sha256(path),
                "schema_version": "ETX7B1_STATIC_V1",
                "source_version": f"ETX7A_V{ETX7A_VERSION}",
                "status": "DETERMINISTIC_STATIC_OUTPUT",
            }
        )
    return pd.DataFrame.from_records(rows)


def build_static_package(output_dir: Path | None = None) -> dict[str, Any]:
    """Build deterministic Stage-A static tables from the frozen ETX-7A workbook."""

    output_dir = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    source_receipts = verify_frozen_sources()
    if not source_receipts["status"].eq("PASS").all():
        raise ValueError(
            "Frozen ETX-7A source hash mismatch: "
            f"{source_receipts.to_dict(orient='records')}"
        )

    canonical = read_canonical_evidence()
    runtime = read_runtime_inputs()
    demand = _build_demand(runtime)
    generators = _build_generators(runtime)
    storage = _build_storage(runtime)
    zeros = _build_zero_controls(runtime)
    provenance = _build_provenance(demand, generators, storage, zeros)

    frames: dict[str, pd.DataFrame] = {
        "canonical": canonical,
        "runtime": runtime,
        "demand": demand,
        "generators": generators,
        "storage": storage,
        "zeros": zeros,
        "provenance": provenance,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in frames.items():
        _write_csv(frame, output_dir / OUTPUT_FILES[name])
    manifest = _build_manifest(output_dir, frames)
    _write_csv(manifest, output_dir / OUTPUT_FILES["manifest"])
    frames["manifest"] = manifest
    return {"output_dir": output_dir, "frames": frames, "source_receipts": source_receipts}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build ETX-7B1 Stage-A static inputs")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    package = build_static_package(args.output_dir)
    if args.validate:
        from .validation import validate_static_package

        validation = validate_static_package(args.output_dir, write_reports=True)
        if not validation["qa"]["status"].eq("PASS").all():
            raise SystemExit(1)
    print(
        f"ETX-7B1 static package built at {package['output_dir']} with "
        f"{len(package['frames']['generators'])} generator and "
        f"{len(package['frames']['storage'])} storage records."
    )


if __name__ == "__main__":
    main()

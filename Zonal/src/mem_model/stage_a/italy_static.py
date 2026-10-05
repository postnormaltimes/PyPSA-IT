from __future__ import annotations

import argparse
import hashlib
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
HORIZONS = (2040, 2050)
SCENARIO = "Base"
COUNTRY_CODE = "IT"
COUNTRY = "Italy"

PRE_PYPSA_DIR = ROOT / "pre_pypsa_inputs"
STAGE_B_MANIFEST = ROOT / "qa" / "interim" / "MEM_FROZEN_STATIC_HASH_MANIFEST.csv"
ETX7B1_DIR = ROOT / "stage_a_inputs" / "etx7a_v1_0" / "static"
ETX7B1_MANIFEST = ETX7B1_DIR / "MEM_ETX7A_Static_Output_Manifest_v1.0.csv"
DEFAULT_OUTPUT_DIR = ROOT / "stage_a_inputs" / "italy_base_v1_0" / "static"

SOURCE_FILES = {
    "demand": PRE_PYPSA_DIR / "MEM_Annual_Zonal_Demand_Contract.csv",
    "generators": PRE_PYPSA_DIR / "MEM_generators_static_final.csv",
    "storage": PRE_PYPSA_DIR / "MEM_storage_static_final.csv",
    "carriers": PRE_PYPSA_DIR / "MEM_carriers_static_final.csv",
    "hydro": PRE_PYPSA_DIR / "MEM_Hydro_Static_Component_Mapping.csv",
    "phs": PRE_PYPSA_DIR / "MEM_PHS_Static_Runtime_Contract.csv",
}

OUTPUT_FILES = {
    "demand": "MEM_ETX7B2_IT_Annual_Demand_Static_v1.0.csv",
    "generators": "MEM_ETX7B2_IT_Generators_Static_v1.0.csv",
    "storage": "MEM_ETX7B2_IT_Storage_Static_v1.0.csv",
    "crosswalk": "MEM_ETX7B2_IT_Carrier_Crosswalk_v1.0.csv",
    "provenance": "MEM_ETX7B2_IT_Static_Provenance_v1.0.csv",
    "manifest": "MEM_ETX7B2_IT_Static_Output_Manifest_v1.0.csv",
}

GENERATOR_GROUP_COLUMNS = [
    "year",
    "parent_capacity_technology",
    "solver_subtechnology",
    "CHP_flag",
    "fuel",
    "carrier",
    "CCS_flag",
    "annual_energy_constraint_type",
    "source_scenario_energy_role",
]


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _safe_id(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value).strip()).strip("_")
    return text.upper() or "UNSPECIFIED"


def _decimal(value: object) -> Decimal:
    text = str(value).strip()
    if not text:
        raise ValueError("Blank numeric value in frozen Italy contract")
    return Decimal(text)


def _decimal_sum(values: Iterable[object]) -> Decimal:
    return sum((_decimal(value) for value in values), Decimal("0"))


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _join(values: Iterable[object]) -> str:
    return " | ".join(sorted({str(value).strip() for value in values if str(value).strip()}))


def _read_contract(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    frame["_source_row"] = range(2, len(frame) + 2)
    return frame


def _csv_rows(path: Path) -> int:
    return len(pd.read_csv(path, dtype=str, keep_default_na=False))


def _normalise_relative_path(value: object) -> Path:
    return Path(str(value).replace("\\", "/"))


def verify_frozen_input_receipts() -> pd.DataFrame:
    """Verify the accepted Stage-B and ETX-7B1 artifacts without rewriting them."""

    rows: list[dict[str, object]] = []
    stage_b = pd.read_csv(STAGE_B_MANIFEST, dtype=str, keep_default_na=False)
    for record in stage_b.to_dict(orient="records"):
        path = ROOT / _normalise_relative_path(record["relative_path"])
        observed_bytes = path.stat().st_size if path.exists() else ""
        observed_rows = _csv_rows(path) if path.exists() else ""
        observed_hash = sha256_file(path) if path.exists() else ""
        expected_hash = str(record["expected_sha256"]).upper()
        passed = (
            path.exists()
            and int(observed_bytes) == int(record["bytes"])
            and int(observed_rows) == int(record["expected_rows"])
            and observed_hash == expected_hash
        )
        rows.append(
            {
                "receipt_family": "FROZEN_STAGE_B_STATIC_CONTRACT",
                "artifact": record["artifact"],
                "relative_path": record["relative_path"].replace("\\", "/"),
                "expected_bytes": record["bytes"],
                "observed_bytes": observed_bytes,
                "expected_rows": record["expected_rows"],
                "observed_rows": observed_rows,
                "expected_sha256": expected_hash,
                "observed_sha256": observed_hash,
                "status": "PASS" if passed else "FAIL",
            }
        )

    etx7b1 = pd.read_csv(ETX7B1_MANIFEST, dtype=str, keep_default_na=False)
    for record in etx7b1.to_dict(orient="records"):
        path = ROOT / _normalise_relative_path(record["relative_path"])
        observed_bytes = path.stat().st_size if path.exists() else ""
        observed_rows = _csv_rows(path) if path.exists() else ""
        observed_hash = sha256_file(path) if path.exists() else ""
        expected_hash = str(record["sha256"]).upper()
        passed = (
            path.exists()
            and int(observed_bytes) == int(record["bytes"])
            and int(observed_rows) == int(record["rows"])
            and observed_hash == expected_hash
        )
        rows.append(
            {
                "receipt_family": "FROZEN_ETX7B1_STATIC_OUTPUT",
                "artifact": record["artifact"],
                "relative_path": record["relative_path"],
                "expected_bytes": record["bytes"],
                "observed_bytes": observed_bytes,
                "expected_rows": record["rows"],
                "observed_rows": observed_rows,
                "expected_sha256": expected_hash,
                "observed_sha256": observed_hash,
                "status": "PASS" if passed else "FAIL",
            }
        )
    return pd.DataFrame.from_records(rows).sort_values(
        ["receipt_family", "artifact"], kind="stable"
    ).reset_index(drop=True)


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(
        path,
        index=False,
        encoding="utf-8",
        lineterminator="\n",
        na_rep="",
    )


def _profile_class(solver_subtechnology: str) -> str:
    if solver_subtechnology.startswith("SOLAR_PV_"):
        return solver_subtechnology
    if solver_subtechnology in {"WIND_ONSHORE", "WIND_OFFSHORE"}:
        return solver_subtechnology
    if solver_subtechnology == "HYDRO_RUN_OF_RIVER":
        return "RUN_OF_RIVER_INFLOW"
    if solver_subtechnology == "GEOTHERMAL":
        return "GEOTHERMAL_TECHNICAL_AVAILABILITY"
    if solver_subtechnology == "NUCLEAR":
        return "NUCLEAR_AVAILABILITY_BLK007"
    return "THERMAL_MAINTENANCE_AVAILABILITY"


def _provenance_record(
    *,
    target_type: str,
    target_id: str,
    year: int,
    source_contract: str,
    source_row: int,
    source_zone: str,
    source_record_id: str,
    source_value: object,
    source_unit: str,
    target_dimension: str,
    contribution_value: object,
    contribution_unit: str,
    aggregation_rule: str,
    evidence_class: str,
    status: str = "TRACEABLE_FROZEN_SOURCE_CONTRIBUTION",
) -> dict[str, object]:
    key = "|".join(
        [target_type, target_id, str(year), source_contract, str(source_row), target_dimension]
    )
    return {
        "provenance_id": f"ETX7B2_PROV_{hashlib.sha256(key.encode()).hexdigest()[:16].upper()}",
        "target_record_type": target_type,
        "target_record_id": target_id,
        "country_code": COUNTRY_CODE,
        "year": year,
        "scenario": SCENARIO,
        "source_contract": source_contract,
        "source_row": source_row,
        "source_zone": source_zone,
        "source_record_id": source_record_id,
        "source_value": source_value,
        "source_unit": source_unit,
        "target_dimension": target_dimension,
        "contribution_value": contribution_value,
        "contribution_unit": contribution_unit,
        "aggregation_rule": aggregation_rule,
        "evidence_class": evidence_class,
        "status": status,
    }


def _build_demand() -> tuple[pd.DataFrame, list[dict[str, object]]]:
    source = _read_contract(SOURCE_FILES["demand"])
    source = source.loc[
        source["scenario"].eq(SCENARIO)
        & source["year"].astype(int).isin(HORIZONS)
    ].copy()
    rows: list[dict[str, object]] = []
    provenance: list[dict[str, object]] = []
    for year in HORIZONS:
        selected = source.loc[source["year"].astype(int).eq(year)].copy()
        zones = sorted(selected["zone"].unique().tolist())
        if len(selected) != 7 or len(zones) != 7:
            raise ValueError(f"{year} Base demand does not contain exactly seven zones")
        zonal_sum = _decimal_sum(selected["annual_zonal_demand_MWh"])
        national_values = sorted(set(selected["annual_national_demand_TWh"]))
        if len(national_values) != 1:
            raise ValueError(f"{year} Base demand has conflicting national controls")
        national_twh = _decimal(national_values[0])
        national_mwh = national_twh * Decimal("1000000")
        difference = zonal_sum - national_mwh
        if difference != 0:
            raise ValueError(
                f"{year} Base zonal demand differs from national control by {difference} MWh"
            )
        demand_id = f"ETX7B2_IT_{year}_BASE_ANNUAL_DEMAND"
        rows.append(
            {
                "demand_id": demand_id,
                "country_code": COUNTRY_CODE,
                "country": COUNTRY,
                "node": COUNTRY_CODE,
                "year": year,
                "scenario": SCENARIO,
                "annual_demand_TWh": _decimal_text(national_twh),
                "annual_demand_MWh": _decimal_text(zonal_sum),
                "frozen_national_control_TWh": _decimal_text(national_twh),
                "difference_from_control_MWh": _decimal_text(difference),
                "contributing_zone_count": len(zones),
                "contributing_zones": "|".join(zones),
                "source_contract": SOURCE_FILES["demand"].name,
                "aggregation_rule": "EXACT_SUM_OF_SEVEN_FROZEN_STAGE_B_BASE_ZONES",
                "status": "ETX7B2_FROZEN_NATIONAL_STATIC_AGGREGATE",
            }
        )
        for record in selected.to_dict(orient="records"):
            provenance.append(
                _provenance_record(
                    target_type="ANNUAL_DEMAND",
                    target_id=demand_id,
                    year=year,
                    source_contract=SOURCE_FILES["demand"].name,
                    source_row=int(record["_source_row"]),
                    source_zone=record["zone"],
                    source_record_id=f"{year}_{SCENARIO}_{record['zone']}_DEMAND",
                    source_value=record["annual_zonal_demand_MWh"],
                    source_unit="MWh",
                    target_dimension="annual_demand_MWh",
                    contribution_value=record["annual_zonal_demand_MWh"],
                    contribution_unit="MWh",
                    aggregation_rule="SUM_SEVEN_FROZEN_ZONES",
                    evidence_class=record.get("status", "FROZEN_STAGE_B_CONTRACT"),
                )
            )
    return pd.DataFrame.from_records(rows), provenance


def _build_generators() -> tuple[pd.DataFrame, list[dict[str, object]]]:
    source = _read_contract(SOURCE_FILES["generators"])
    source = source.loc[
        source["scenario"].eq(SCENARIO)
        & source["year"].astype(int).isin(HORIZONS)
    ].copy()
    if source.empty:
        raise ValueError("No frozen Italy Base generator rows found")
    if source["p_nom_extendable"].str.lower().isin({"true", "1"}).any():
        raise ValueError("Frozen Italy Base generator contract contains extendable capacity")
    if any(_decimal(value) <= 0 for value in source["p_nom_MW"]):
        raise ValueError("Frozen Italy Base generator contract contains non-positive capacity")
    carrier_authority = _read_contract(SOURCE_FILES["carriers"])
    unknown_carriers = sorted(set(source["carrier"]) - set(carrier_authority["carrier"]))
    if unknown_carriers:
        raise ValueError(
            f"Italy generator rows reference carriers absent from the frozen carrier contract: {unknown_carriers}"
        )

    rows: list[dict[str, object]] = []
    provenance: list[dict[str, object]] = []
    grouped = source.groupby(GENERATOR_GROUP_COLUMNS, sort=True, dropna=False)
    for key, selected in grouped:
        values = dict(zip(GENERATOR_GROUP_COLUMNS, key, strict=True))
        year = int(values["year"])
        p_nom = _decimal_sum(selected["p_nom_MW"])
        key_text = "|".join(str(values[column]) for column in GENERATOR_GROUP_COLUMNS)
        asset_id = (
            f"ETX7B2_IT_{year}_{_safe_id(values['solver_subtechnology'])}_"
            f"{hashlib.sha256(key_text.encode()).hexdigest()[:10].upper()}"
        )
        zones = sorted(selected["zone"].unique().tolist())
        availability_classes = _join(selected["availability_class"])
        rows.append(
            {
                "asset_id": asset_id,
                "country_code": COUNTRY_CODE,
                "country": COUNTRY,
                "node": COUNTRY_CODE,
                "year": year,
                "scenario": SCENARIO,
                "parent_capacity_technology": values["parent_capacity_technology"],
                "stage_a_static_class": values["solver_subtechnology"],
                "solver_subtechnology": values["solver_subtechnology"],
                "CHP_flag": values["CHP_flag"],
                "fuel": values["fuel"],
                "carrier": values["carrier"],
                "CCS_flag": values["CCS_flag"],
                "p_nom_MW": _decimal_text(p_nom),
                "p_nom_extendable": False,
                "component_archetype_later": (
                    "RUN_OF_RIVER_GENERATOR"
                    if values["solver_subtechnology"] == "HYDRO_RUN_OF_RIVER"
                    else "FIXED_GENERATOR"
                ),
                "intended_later_profile_class": _profile_class(
                    str(values["solver_subtechnology"])
                ),
                "intended_later_dispatch_cost_class": (
                    f"LATER_ETX7B4_{_safe_id(values['carrier'])}_DISPATCH_COST"
                ),
                "annual_energy_constraint_type": values[
                    "annual_energy_constraint_type"
                ],
                "source_scenario_energy_role": values[
                    "source_scenario_energy_role"
                ],
                "contributing_zone_count": len(zones),
                "contributing_zones": "|".join(zones),
                "contributing_availability_classes": availability_classes,
                "source_capacity_references": _join(selected["source_capacity"]),
                "source_contract": SOURCE_FILES["generators"].name,
                "accounting_origin": "STAGE_B_GENERATOR_CONTRACT",
                "evidence_class": _join(selected["evidence_class"]),
                "aggregation_rule": "SUM_FROZEN_STAGE_B_ZONES_WITHIN_RETAINED_RUNTIME_CLASS",
                "status": "ETX7B2_FROZEN_NATIONAL_STATIC_AGGREGATE",
            }
        )
        for record in selected.to_dict(orient="records"):
            provenance.append(
                _provenance_record(
                    target_type="GENERATOR",
                    target_id=asset_id,
                    year=year,
                    source_contract=SOURCE_FILES["generators"].name,
                    source_row=int(record["_source_row"]),
                    source_zone=record["zone"],
                    source_record_id=record["generator_id"],
                    source_value=record["p_nom_MW"],
                    source_unit="MW",
                    target_dimension="p_nom_MW",
                    contribution_value=record["p_nom_MW"],
                    contribution_unit="MW",
                    aggregation_rule="SUM_WITHIN_RETAINED_STAGE_A_STATIC_CLASS",
                    evidence_class=record["evidence_class"],
                )
            )

    hydro = _read_contract(SOURCE_FILES["hydro"])
    hydro_classes = {
        "BASIN_PONDAGE": ("HYDRO_BASIN_PONDAGE", "hydro_basin_pondage"),
        "RESERVOIR": ("HYDRO_RESERVOIR", "hydro_reservoir"),
    }
    for hydro_class, (technology, carrier) in hydro_classes.items():
        selected = hydro.loc[hydro["hydro_class"].eq(hydro_class)].copy()
        if len(selected) != 7:
            raise ValueError(f"Hydro class {hydro_class} does not contain seven zones")
        p_nom = _decimal_sum(selected["p_nom_MW_NET"])
        for year in HORIZONS:
            asset_id = f"ETX7B2_IT_{year}_{technology}"
            rows.append(
                {
                    "asset_id": asset_id,
                    "country_code": COUNTRY_CODE,
                    "country": COUNTRY,
                    "node": COUNTRY_CODE,
                    "year": year,
                    "scenario": SCENARIO,
                    "parent_capacity_technology": technology,
                    "stage_a_static_class": technology,
                    "solver_subtechnology": technology,
                    "CHP_flag": "false",
                    "fuel": "WATER_INFLOW",
                    "carrier": carrier,
                    "CCS_flag": "false",
                    "p_nom_MW": _decimal_text(p_nom),
                    "p_nom_extendable": False,
                    "component_archetype_later": _join(
                        selected["component_archetype_to_be_used_later"]
                    ),
                    "intended_later_profile_class": "HYDRO_INFLOW_AND_STORAGE_OPERATION",
                    "intended_later_dispatch_cost_class": "LATER_ETX7B4_HYDRO_OPERATIONAL_CLASS",
                    "annual_energy_constraint_type": "HYDRO_OPERATIONAL_CONSTRAINT_PENDING_ETX7B3_ETX7B4",
                    "source_scenario_energy_role": "FIXED_CURRENT_HYDRO_FLEET_ALL_SCENARIOS",
                    "contributing_zone_count": 7,
                    "contributing_zones": "|".join(sorted(selected["zone"].tolist())),
                    "contributing_availability_classes": "NOT_ASSIGNED_BY_ETX7B2",
                    "source_capacity_references": _join(selected["source"]),
                    "source_contract": SOURCE_FILES["hydro"].name,
                    "accounting_origin": "HYDRO_STATIC_COMPONENT_MAPPING",
                    "evidence_class": "FROZEN_STAGE_B_HYDRO_CLASS_ALLOCATION",
                    "aggregation_rule": "SUM_SEVEN_FROZEN_HYDRO_CLASS_ZONE_ROWS",
                    "status": "ETX7B2_FROZEN_NATIONAL_STATIC_AGGREGATE",
                }
            )
            for record in selected.to_dict(orient="records"):
                provenance.append(
                    _provenance_record(
                        target_type="GENERATOR",
                        target_id=asset_id,
                        year=year,
                        source_contract=SOURCE_FILES["hydro"].name,
                        source_row=int(record["_source_row"]),
                        source_zone=record["zone"],
                        source_record_id=f"{record['zone']}_{hydro_class}",
                        source_value=record["p_nom_MW_NET"],
                        source_unit="MW_NET",
                        target_dimension="p_nom_MW",
                        contribution_value=record["p_nom_MW_NET"],
                        contribution_unit="MW",
                        aggregation_rule="SUM_FROZEN_HYDRO_CLASS_ZONE_ROWS",
                        evidence_class=record["status"],
                    )
                )

    frame = pd.DataFrame.from_records(rows).sort_values(
        ["year", "parent_capacity_technology", "stage_a_static_class", "asset_id"],
        kind="stable",
    ).reset_index(drop=True)
    return frame, provenance


def _storage_scope(storage_class: str) -> str:
    if "SMALL_SCALE" in storage_class:
        return "DISTRIBUTED_SMALL_SCALE"
    if "UTILITY_SCALE" in storage_class or "CAPACITY_MARKET" in storage_class:
        return "GRID_SCALE"
    return "FROZEN_PORTFOLIO_UNSPLIT"


def _build_storage() -> tuple[pd.DataFrame, list[dict[str, object]]]:
    source = _read_contract(SOURCE_FILES["storage"])
    source = source.loc[
        source["scenario"].eq(SCENARIO)
        & source["year"].astype(int).isin(HORIZONS)
    ].copy()
    if source["p_nom_extendable"].str.lower().isin({"true", "1"}).any():
        raise ValueError("Frozen Italy BESS contract contains extendable power")
    if source["e_nom_extendable"].str.lower().isin({"true", "1"}).any():
        raise ValueError("Frozen Italy BESS contract contains extendable energy")

    rows: list[dict[str, object]] = []
    provenance: list[dict[str, object]] = []
    group_columns = ["year", "technology", "storage_class_original"]
    for key, selected in source.groupby(group_columns, sort=True, dropna=False):
        year, technology, storage_class = key
        year = int(year)
        charge = _decimal_sum(selected["charge_power_MW"])
        discharge = _decimal_sum(selected["discharge_power_MW"])
        energy = _decimal_sum(selected["energy_capacity_MWh"])
        storage_id = f"ETX7B2_IT_{year}_{_safe_id(storage_class)}"
        zones = sorted(selected["zone"].unique().tolist())
        rows.append(
            {
                "storage_id": storage_id,
                "country_code": COUNTRY_CODE,
                "country": COUNTRY,
                "node": COUNTRY_CODE,
                "year": year,
                "scenario": SCENARIO,
                "storage_family": "BESS",
                "source_technology": technology,
                "storage_class": storage_class,
                "distributed_vs_grid_flag": _storage_scope(str(storage_class)),
                "charge_power_MW": _decimal_text(charge),
                "discharge_power_MW": _decimal_text(discharge),
                "energy_MWh": _decimal_text(energy),
                "p_nom_extendable": False,
                "e_nom_extendable": False,
                "pure_phs_discharge_subset_MW": "",
                "mixed_phs_discharge_subset_MW": "",
                "pure_mixed_governance": "NOT_APPLICABLE",
                "contributing_zone_count": len(zones),
                "contributing_zones": "|".join(zones),
                "source_contract": SOURCE_FILES["storage"].name,
                "aggregation_rule": "SUM_SEVEN_FROZEN_STAGE_B_BASE_ZONES_BY_OPERATIONAL_CLASS",
                "evidence_class": _join(selected["evidence_class"]),
                "operating_parameters_status": "NOT_ASSIGNED_BY_ETX7B2",
                "status": "ETX7B2_FROZEN_NATIONAL_STATIC_AGGREGATE",
            }
        )
        for record in selected.to_dict(orient="records"):
            for dimension, column, unit in (
                ("charge_power_MW", "charge_power_MW", "MW"),
                ("discharge_power_MW", "discharge_power_MW", "MW"),
                ("energy_MWh", "energy_capacity_MWh", "MWh"),
            ):
                provenance.append(
                    _provenance_record(
                        target_type="STORAGE",
                        target_id=storage_id,
                        year=year,
                        source_contract=SOURCE_FILES["storage"].name,
                        source_row=int(record["_source_row"]),
                        source_zone=record["zone"],
                        source_record_id=f"{year}_{SCENARIO}_{record['zone']}_{storage_class}",
                        source_value=record[column],
                        source_unit=unit,
                        target_dimension=dimension,
                        contribution_value=record[column],
                        contribution_unit=unit,
                        aggregation_rule="SUM_BY_FROZEN_OPERATIONAL_STORAGE_CLASS",
                        evidence_class=record["evidence_class"],
                    )
                )

    phs = _read_contract(SOURCE_FILES["phs"])
    hydro = _read_contract(SOURCE_FILES["hydro"])
    phs_totals = {
        "charge_power_MW": _decimal_sum(phs["pump_power_MW"]),
        "discharge_power_MW": _decimal_sum(phs["discharge_power_MW_NET"]),
        "energy_MWh": _decimal_sum(phs["operational_energy_MWh"]),
    }
    pure = _decimal_sum(
        hydro.loc[hydro["hydro_class"].eq("PURE_PHS"), "p_nom_MW_NET"]
    )
    mixed = _decimal_sum(
        hydro.loc[hydro["hydro_class"].eq("MIXED_PHS"), "p_nom_MW_NET"]
    )
    if pure + mixed != phs_totals["discharge_power_MW"]:
        raise ValueError("Pure/mixed PHS discharge controls do not reconcile")
    for year in HORIZONS:
        storage_id = f"ETX7B2_IT_{year}_PHS_COMBINED_OPERATIONAL_CONTROL"
        rows.append(
            {
                "storage_id": storage_id,
                "country_code": COUNTRY_CODE,
                "country": COUNTRY,
                "node": COUNTRY_CODE,
                "year": year,
                "scenario": SCENARIO,
                "storage_family": "PHS",
                "source_technology": "PUMPED_HYDRO",
                "storage_class": "PHS_COMBINED_PURE_AND_MIXED_OPERATIONAL_CONTROL",
                "distributed_vs_grid_flag": "GRID_SYSTEM_STORAGE",
                "charge_power_MW": _decimal_text(phs_totals["charge_power_MW"]),
                "discharge_power_MW": _decimal_text(phs_totals["discharge_power_MW"]),
                "energy_MWh": _decimal_text(phs_totals["energy_MWh"]),
                "p_nom_extendable": False,
                "e_nom_extendable": False,
                "pure_phs_discharge_subset_MW": _decimal_text(pure),
                "mixed_phs_discharge_subset_MW": _decimal_text(mixed),
                "pure_mixed_governance": (
                    "PURE_AND_MIXED_DISCHARGE_SUBSETS_PRESERVED; "
                    "PUMP_AND_ENERGY_SPLIT_NOT_FROZEN; NO_DUPLICATE_ASSET"
                ),
                "contributing_zone_count": 7,
                "contributing_zones": "|".join(sorted(phs["zone"].tolist())),
                "source_contract": (
                    f"{SOURCE_FILES['phs'].name} | {SOURCE_FILES['hydro'].name}"
                ),
                "aggregation_rule": "NATIONAL_SUM_OF_FROZEN_PHS_STATIC_CONTRACT",
                "evidence_class": "FROZEN_TERNA_CONSTRAINED_OPERATIONAL_CONTROL",
                "operating_parameters_status": "NOT_ASSIGNED_BY_ETX7B2",
                "status": "ETX7B2_FROZEN_NATIONAL_STATIC_AGGREGATE",
            }
        )
        for record in phs.to_dict(orient="records"):
            for dimension, column, unit in (
                ("charge_power_MW", "pump_power_MW", "MW"),
                ("discharge_power_MW", "discharge_power_MW_NET", "MW"),
                ("energy_MWh", "operational_energy_MWh", "MWh"),
            ):
                provenance.append(
                    _provenance_record(
                        target_type="STORAGE",
                        target_id=storage_id,
                        year=year,
                        source_contract=SOURCE_FILES["phs"].name,
                        source_row=int(record["_source_row"]),
                        source_zone=record["zone"],
                        source_record_id=f"{record['zone']}_PHS_OPERATIONAL_CONTROL",
                        source_value=record[column],
                        source_unit=unit,
                        target_dimension=dimension,
                        contribution_value=record[column],
                        contribution_unit=unit,
                        aggregation_rule="SUM_FROZEN_PHS_ZONE_CONTROLS",
                        evidence_class=record["status"],
                    )
                )
        for hydro_class in ("PURE_PHS", "MIXED_PHS"):
            subset = hydro.loc[hydro["hydro_class"].eq(hydro_class)]
            for record in subset.to_dict(orient="records"):
                provenance.append(
                    _provenance_record(
                        target_type="STORAGE_SUBSET_CONTROL",
                        target_id=storage_id,
                        year=year,
                        source_contract=SOURCE_FILES["hydro"].name,
                        source_row=int(record["_source_row"]),
                        source_zone=record["zone"],
                        source_record_id=f"{record['zone']}_{hydro_class}",
                        source_value=record["p_nom_MW_NET"],
                        source_unit="MW_NET",
                        target_dimension=f"{hydro_class.lower()}_discharge_subset_MW",
                        contribution_value=record["p_nom_MW_NET"],
                        contribution_unit="MW",
                        aggregation_rule="SUBSET_CONTROL_ONLY_NO_ADDITIONAL_STORAGE_ASSET",
                        evidence_class=record["status"],
                    )
                )

    frame = pd.DataFrame.from_records(rows).sort_values(
        ["year", "storage_family", "storage_class", "storage_id"], kind="stable"
    ).reset_index(drop=True)
    return frame, provenance


def _build_crosswalk(
    generators: pd.DataFrame, storage: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for record in generators.to_dict(orient="records"):
        rows.append(
            {
                "crosswalk_id": f"XW_{record['asset_id']}",
                "record_family": "GENERATOR",
                "year": record["year"],
                "scenario": SCENARIO,
                "source_technology": record["solver_subtechnology"],
                "parent_technology": record["parent_capacity_technology"],
                "fuel": record["fuel"],
                "carrier": record["carrier"],
                "stage_a_static_class": record["stage_a_static_class"],
                "intended_later_profile_class": record[
                    "intended_later_profile_class"
                ],
                "intended_later_dispatch_cost_class": record[
                    "intended_later_dispatch_cost_class"
                ],
                "aggregation_safe": True,
                "later_operational_split_required": False,
                "rationale": (
                    "Spatial sum only; fuel, carrier, CHP, CCS and solver "
                    "subtechnology remain distinct."
                ),
                "source_contract": record["source_contract"],
                "carrier_authority": (
                    SOURCE_FILES["carriers"].name
                    if record["accounting_origin"] == "STAGE_B_GENERATOR_CONTRACT"
                    else SOURCE_FILES["hydro"].name
                ),
                "operating_parameters_status": "NOT_ASSIGNED_BY_ETX7B2",
                "status": "ETX7B2_STATIC_CROSSWALK_COMPLETE",
            }
        )
    for record in storage.to_dict(orient="records"):
        is_phs = record["storage_family"] == "PHS"
        rows.append(
            {
                "crosswalk_id": f"XW_{record['storage_id']}",
                "record_family": "STORAGE",
                "year": record["year"],
                "scenario": SCENARIO,
                "source_technology": record["source_technology"],
                "parent_technology": record["storage_family"],
                "fuel": "ELECTRICITY_AND_WATER" if is_phs else "ELECTRICITY",
                "carrier": "pumped_hydro" if is_phs else "battery",
                "stage_a_static_class": record["storage_class"],
                "intended_later_profile_class": "STORAGE_OPERATION_LATER_ETX7B4",
                "intended_later_dispatch_cost_class": "STORAGE_OPERATION_LATER_ETX7B4",
                "aggregation_safe": True,
                "later_operational_split_required": is_phs,
                "rationale": (
                    "National PHS power/energy controls are aggregation-safe; "
                    "pure/mixed operational split remains a later mapping without "
                    "duplicating capacity."
                    if is_phs
                    else "Seven-zone frozen BESS rows are summed within the retained operational class."
                ),
                "source_contract": record["source_contract"],
                "carrier_authority": "ETX7B2_STATIC_MAPPING_PENDING_ETX7B4_RUNTIME_PARAMETERS",
                "operating_parameters_status": "NOT_ASSIGNED_BY_ETX7B2",
                "status": "ETX7B2_STATIC_CROSSWALK_COMPLETE",
            }
        )
    return pd.DataFrame.from_records(rows).sort_values(
        ["year", "record_family", "parent_technology", "stage_a_static_class", "crosswalk_id"],
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
                "relative_path": f"stage_a_inputs/italy_base_v1_0/static/{filename}",
                "bytes": path.stat().st_size,
                "rows": len(frames[name]),
                "sha256": sha256_file(path),
                "schema_version": "ETX7B2_IT_STATIC_V1.0",
                "source_authority": "FROZEN_STAGE_B_BASE_CONTRACTS",
                "status": "DETERMINISTIC_STATIC_OUTPUT",
            }
        )
    return pd.DataFrame.from_records(rows)


def build_italy_static_package(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the deterministic 2040/2050 Base Italy one-node Stage-A contract."""

    output_dir = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    receipts = verify_frozen_input_receipts()
    if not receipts["status"].eq("PASS").all():
        failures = receipts.loc[receipts["status"].eq("FAIL")].to_dict(orient="records")
        raise ValueError(f"Frozen input regression detected: {failures}")

    demand, demand_provenance = _build_demand()
    generators, generator_provenance = _build_generators()
    storage, storage_provenance = _build_storage()
    crosswalk = _build_crosswalk(generators, storage)
    provenance = pd.DataFrame.from_records(
        demand_provenance + generator_provenance + storage_provenance
    ).sort_values(
        ["year", "target_record_type", "target_record_id", "target_dimension", "source_contract", "source_row"],
        kind="stable",
    ).reset_index(drop=True)

    frames: dict[str, pd.DataFrame] = {
        "demand": demand,
        "generators": generators,
        "storage": storage,
        "crosswalk": crosswalk,
        "provenance": provenance,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in frames.items():
        _write_csv(frame, output_dir / OUTPUT_FILES[name])
    manifest = _build_manifest(output_dir, frames)
    _write_csv(manifest, output_dir / OUTPUT_FILES["manifest"])
    frames["manifest"] = manifest
    return {"output_dir": output_dir, "frames": frames, "source_receipts": receipts}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build ETX-7B2 Italy Base one-node Stage-A static inputs"
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    package = build_italy_static_package(args.output_dir)
    if args.validate:
        from .italy_validation import validate_italy_static_package

        validation = validate_italy_static_package(args.output_dir, write_reports=True)
        if not validation["qa"]["status"].eq("PASS").all():
            raise SystemExit(1)
    print(
        f"ETX-7B2 Italy static package built at {package['output_dir']} with "
        f"{len(package['frames']['generators'])} generator, "
        f"{len(package['frames']['storage'])} storage and "
        f"{len(package['frames']['demand'])} demand records."
    )


if __name__ == "__main__":
    main()

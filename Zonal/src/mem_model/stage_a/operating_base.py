"""Deterministic ETX-7B4 Stage-A Base operating-parameter adapter.

This module attaches accepted operating assumptions to the immutable ETX-7B1,
ETX-7B2 and ETX-7B3 contracts.  It does not import PyPSA, build topology, or
run optimization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "config" / "stage_a_operating_base.yaml"
HORIZONS = (2040, 2050)
MARKETS = ("IT", "FR", "CH", "AT", "SI", "HR", "ME", "GR", "MT", "TN")
DEFAULT_OUTPUT_DIR = ROOT / "stage_a_inputs" / "operating_base_v1_0"
SCHEMA_VERSION = "ETX7B4_OPERATING_BASE_V1_0"

OUTPUT_FILES = {
    "generators": "MEM_ETX7B4_Generator_Operating_Parameters_v1.0.csv",
    "availability": "MEM_ETX7B4_Generator_Technical_Availability_Hourly_v1.0.parquet",
    "storage": "MEM_ETX7B4_Storage_Operating_Parameters_v1.0.csv",
    "italy_phs": "MEM_ETX7B4_Italy_PHS_State_Mapping_v1.0.csv",
    "hydro": "MEM_ETX7B4_Hydro_Annual_Scaling_and_State_v1.0.csv",
    "hydro_inflow": "MEM_ETX7B4_Hydro_Runtime_Inflow_Mapping_v1.0.csv",
    "constraints": "MEM_ETX7B4_Fuel_Energy_Constraint_Dispositions_v1.0.csv",
    "inheritance": "MEM_ETX7B4_Runtime_Inheritance_Matrix_v1.0.csv",
    "provenance": "MEM_ETX7B4_Parameter_Provenance_v1.0.csv",
    "decisions": "MEM_ETX7B4_Assumption_and_Decision_Register_v1.0.csv",
    "b3_erratum": "MEM_ETX7B4_B3_Provenance_Erratum_v1.0.csv",
    "manifest": "MEM_ETX7B4_Output_Manifest_v1.0.csv",
}


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _safe_id(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(value).strip()).strip("_").upper()


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8", lineterminator="\n", na_rep="")


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False, engine="pyarrow", compression="zstd", version="2.6")


def _row_count(path: Path) -> int:
    if path.suffix.lower() == ".parquet":
        return len(pd.read_parquet(path))
    return len(_read_csv(path))


def _resolve(root: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (root / candidate).resolve()


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _source_paths(config: dict[str, Any]) -> dict[str, Path]:
    paths = {key: _resolve(ROOT, value) for key, value in config["paths"].items()}
    upstream_root = paths["pypsa_eur_root"]
    for key in ("pypsa_eur_hydro_capacities", "pypsa_eur_eia_hydro_generation"):
        configured = Path(config["paths"][key])
        paths[key] = configured if configured.is_absolute() else (upstream_root / configured).resolve()
    return paths


def _profile_lookup(paths: dict[str, Path]) -> tuple[pd.DataFrame, pd.DataFrame]:
    vre = pd.read_parquet(paths["temporal_directory"] / "MEM_ETX7B3_VRE_Availability_Hourly_v1.0.parquet")
    hydro = pd.read_parquet(paths["temporal_hydro"])
    vre_map = vre[["country_code", "stage_a_static_or_profile_class", "profile_id"]].drop_duplicates()
    hydro_map = hydro[["country_code", "hydro_class", "profile_id"]].drop_duplicates()
    return vre_map, hydro_map


def _find_profile(
    mapping: pd.DataFrame,
    country: str,
    profile_class: str,
    year: int,
    class_column: str,
) -> str:
    selected = mapping.loc[
        mapping["country_code"].eq(country) & mapping[class_column].eq(profile_class),
        "profile_id",
    ].astype(str)
    if len(selected) > 1:
        horizon = selected.loc[selected.str.contains(str(year), regex=False)]
        if len(horizon) == 1:
            selected = horizon
    unique = sorted(set(selected))
    if len(unique) != 1:
        raise ValueError(f"Profile resolution failed for {country}/{year}/{profile_class}: {unique}")
    return unique[0]


def _is_chp(source_group: str, carrier: str) -> bool:
    return "chp" in source_group.lower() or carrier in {"THERMAL_OTHER_CHP", "WASTE_CHP"}


def _external_template(record: dict[str, str], config: dict[str, Any]) -> dict[str, Any]:
    year = int(record["year"])
    carrier = record["mapped_carrier"]
    source_group = record["source_group"]
    carbon = float(config["economics"]["co2_price_EUR2025_per_t"][year])
    methane = float(config["economics"]["methane_price_EUR2025_per_MWh_th"][year])
    hydrogen = float(config["economics"]["hydrogen_price_EUR2025_per_MWh_th"][year])
    bio = float(config["economics"]["bioenergy_price_EUR2025_per_MWh_th"][year])
    methane_co2 = float(config["economics"]["methane_direct_CO2_t_per_MWh_th"])
    bio_co2 = float(config["economics"]["bioenergy_physical_CO2_t_per_MWh_th"])
    templates = config["external_generator_templates"]
    chp = _is_chp(source_group, carrier)

    result: dict[str, Any] = {
        "mapped_dispatch_fuel": "NONE",
        "template_id": "ZERO_MARGINAL_COST_PROFILED_RESOURCE",
        "efficiency_el": "",
        "VOM_EUR2025_per_MWh_el": 0.0,
        "fuel_price_EUR2025_per_MWh_th": 0.0,
        "direct_physical_CO2_t_per_MWh_th": 0.0,
        "chargeable_CO2_t_per_MWh_th": 0.0,
        "capture_rate": 0.0,
        "residual_CO2_t_per_MWh_th": 0.0,
        "reported_non_ETS_physical_CO2_t_per_MWh_el": 0.0,
        "other_variable_cost_EUR2025_per_MWh_el": 0.0,
        "availability_mode": "B3_TEMPORAL_PROFILE",
        "maintenance_family": "NOT_APPLICABLE",
        "parameter_authority": "ACCEPTED_MEM_PYPSA_EUR_PROFILE_RESOURCE_TEMPLATE",
    }
    if carrier in {"SOLAR_PV", "SOLAR_CSP", "WIND_ONSHORE", "WIND_OFFSHORE"}:
        result["mapped_dispatch_fuel"] = "SOLAR_OR_WIND_RESOURCE"
    elif carrier == "HYDRO_NON_PHS":
        result.update(
            mapped_dispatch_fuel="WATER_INFLOW",
            template_id="HYDRO",
            efficiency_el=float(templates["hydro"]["efficiency_el"]),
            parameter_authority=templates["hydro"]["source_decision"],
        )
    elif carrier == "GEOTHERMAL":
        result.update(
            mapped_dispatch_fuel="GEOTHERMAL_HEAT",
            template_id="GEOTHERMAL",
            efficiency_el=float(templates["geothermal"]["efficiency_el"]),
            VOM_EUR2025_per_MWh_el=float(templates["geothermal"]["VOM_EUR2025_per_MWh_el"][year]),
            reported_non_ETS_physical_CO2_t_per_MWh_el=1996060 / (25023 * 0.6032 * 1000),
            availability_mode="B4_CONSTANT_GEOTHERMAL",
            maintenance_family="GEOTHERMAL",
            parameter_authority=templates["geothermal"]["source_decision"],
        )
    elif carrier == "NUCLEAR":
        t = templates["nuclear"]
        result.update(
            mapped_dispatch_fuel="NUCLEAR_FUEL",
            template_id="NUCLEAR",
            efficiency_el=float(t["efficiency_el"]),
            VOM_EUR2025_per_MWh_el=float(t["VOM_EUR2025_per_MWh_el"]),
            fuel_price_EUR2025_per_MWh_th=float(t["fuel_price_EUR2025_per_MWh_th"]),
            availability_mode="B4_BLK007_NUCLEAR",
            maintenance_family="NUCLEAR_BLK007",
            parameter_authority=t["source_decision"],
        )
    elif carrier == "METHANE_GAS":
        t = templates["methane_ccgt"]
        result.update(
            mapped_dispatch_fuel="METHANE",
            template_id="METHANE_CCGT",
            efficiency_el=float(t["efficiency_el"]),
            VOM_EUR2025_per_MWh_el=float(t["VOM_EUR2025_per_MWh_el"]),
            fuel_price_EUR2025_per_MWh_th=methane,
            direct_physical_CO2_t_per_MWh_th=methane_co2,
            chargeable_CO2_t_per_MWh_th=methane_co2,
            residual_CO2_t_per_MWh_th=methane_co2,
            availability_mode="B4_BLK005_AGGREGATE",
            maintenance_family=t["maintenance_family"],
            parameter_authority=t["source_decision"],
        )
    elif carrier == "LOWCARBON_GAS_H2":
        t = templates["hydrogen_ccgt"]
        result.update(
            mapped_dispatch_fuel="HYDROGEN",
            template_id="HYDROGEN_CCGT",
            efficiency_el=float(t["efficiency_el"]),
            VOM_EUR2025_per_MWh_el=float(t["VOM_EUR2025_per_MWh_el"]),
            fuel_price_EUR2025_per_MWh_th=hydrogen,
            availability_mode="B4_BLK005_AGGREGATE",
            maintenance_family=t["maintenance_family"],
            parameter_authority=t["source_decision"],
        )
    elif carrier == "COAL_LIGNITE":
        t = templates["lignite"]
        result.update(
            mapped_dispatch_fuel="LIGNITE",
            template_id="LIGNITE_OLD2",
            efficiency_el=float(t["efficiency_el"]),
            VOM_EUR2025_per_MWh_el=float(t["VOM_EUR2025_per_MWh_el"]),
            fuel_price_EUR2025_per_MWh_th=float(t["fuel_price_EUR2025_per_MWh_th"][year]),
            direct_physical_CO2_t_per_MWh_th=float(t["direct_CO2_t_per_MWh_th"]),
            chargeable_CO2_t_per_MWh_th=float(t["direct_CO2_t_per_MWh_th"]),
            residual_CO2_t_per_MWh_th=float(t["direct_CO2_t_per_MWh_th"]),
            availability_mode="B4_BLK005_AGGREGATE",
            maintenance_family=t["maintenance_family"],
            parameter_authority=t["source_decision"],
        )
    elif carrier == "OIL_LIQUID":
        t = templates["oil"]
        result.update(
            mapped_dispatch_fuel="OIL_LIQUID",
            template_id="OIL_LIGHT",
            efficiency_el=float(t["efficiency_el"]),
            VOM_EUR2025_per_MWh_el=float(t["VOM_EUR2025_per_MWh_el"]),
            fuel_price_EUR2025_per_MWh_th=float(t["fuel_price_EUR2025_per_MWh_th"][year]),
            direct_physical_CO2_t_per_MWh_th=float(t["direct_CO2_t_per_MWh_th"]),
            chargeable_CO2_t_per_MWh_th=float(t["direct_CO2_t_per_MWh_th"]),
            residual_CO2_t_per_MWh_th=float(t["direct_CO2_t_per_MWh_th"]),
            availability_mode="B4_BLK005_AGGREGATE",
            maintenance_family=t["maintenance_family"],
            parameter_authority=t["source_decision"],
        )
    elif carrier in {
        "BIOENERGY_BIOGAS",
        "BIOENERGY_BIOMASS",
        "BIOENERGY_BIOMASS_BIOGAS",
        "THERMAL_OTHER_CHP",
        "WASTE_CHP",
    }:
        t = templates["bioenergy_chp" if chp else "bioenergy_non_chp"]
        fuel = {
            "BIOENERGY_BIOGAS": "BIOGAS",
            "BIOENERGY_BIOMASS": "BIOENERGY_BIOMASS",
            "BIOENERGY_BIOMASS_BIOGAS": "BIOMASS_BIOGAS",
            "THERMAL_OTHER_CHP": "BIOENERGY_WASTE_MIX",
            "WASTE_CHP": "WASTE_BIOGENIC_MIX",
        }[carrier]
        result.update(
            mapped_dispatch_fuel=fuel,
            template_id="BIOENERGY_CHP" if chp else "BIOENERGY_NON_CHP",
            efficiency_el=float(t["efficiency_el"]),
            VOM_EUR2025_per_MWh_el=float(t["VOM_EUR2025_per_MWh_el"]),
            fuel_price_EUR2025_per_MWh_th=bio,
            direct_physical_CO2_t_per_MWh_th=bio_co2,
            chargeable_CO2_t_per_MWh_th=0.0,
            residual_CO2_t_per_MWh_th=bio_co2,
            availability_mode="B4_BLK005_AGGREGATE",
            maintenance_family=t["maintenance_family"],
            parameter_authority=t["source_decision"],
        )
    else:
        raise ValueError(f"No accepted external template for {record['asset_id']} / {carrier}")

    efficiency = result["efficiency_el"]
    if efficiency == "":
        fuel_component = 0.0
        carbon_component = 0.0
    else:
        fuel_component = float(result["fuel_price_EUR2025_per_MWh_th"]) / float(efficiency)
        carbon_component = carbon * float(result["chargeable_CO2_t_per_MWh_th"]) / float(efficiency)
    result["CO2_price_EUR2025_per_t"] = carbon
    result["fuel_component_EUR2025_per_MWh_el"] = fuel_component
    result["carbon_component_EUR2025_per_MWh_el"] = carbon_component
    result["marginal_cost_EUR2025_per_MWh_el"] = (
        fuel_component
        + float(result["VOM_EUR2025_per_MWh_el"])
        + carbon_component
        + float(result["other_variable_cost_EUR2025_per_MWh_el"])
    )
    result["CAPEX_in_marginal_cost"] = False
    result["fixed_OPEX_in_marginal_cost"] = False
    return result


def _weighted_phase4b_row(
    static_record: dict[str, str], phase4b: pd.DataFrame
) -> dict[str, Any]:
    year = int(static_record["year"])
    technology = static_record["stage_a_static_class"]
    selected = phase4b.loc[
        phase4b["scenario"].eq("Base")
        & phase4b["year"].astype(int).eq(year)
        & phase4b["solver_subtechnology"].eq(technology)
    ].copy()
    if selected.empty and technology in {"HYDRO_BASIN_PONDAGE", "HYDRO_RESERVOIR"}:
        return {
            "efficiency_el": 0.9,
            "VOM_EUR2025_per_MWh_el": 0.0,
            "fuel_price_EUR2025_per_MWh_th": 0.0,
            "direct_physical_CO2_t_per_MWh_th": 0.0,
            "chargeable_CO2_t_per_MWh_th": 0.0,
            "capture_rate": 0.0,
            "residual_CO2_t_per_MWh_th": 0.0,
            "CO2_price_EUR2025_per_t": 104.5504 if year == 2040 else 400.0,
            "other_variable_cost_EUR2025_per_MWh_el": 0.0,
            "marginal_cost_EUR2025_per_MWh_el": 0.0,
            "static_availability_equivalent": "",
            "parameter_authority": "PHASE4B_HYDRO_TEMPLATE_APPLIED_TO_ETX7B2_ADDED_CLASS",
        }
    if selected.empty:
        raise ValueError(f"No Phase-4B Base parameter rows for Italy class {year}/{technology}")
    expected = float(static_record["p_nom_MW"])
    observed = pd.to_numeric(selected["p_nom_MW"]).sum()
    if not math.isclose(observed, expected, rel_tol=0, abs_tol=1e-6):
        raise ValueError(f"Italy Phase-4B capacity mismatch {year}/{technology}: {observed} != {expected}")
    fields = [
        "efficiency",
        "VOM_EUR2025_per_MWh_el",
        "fuel_price_EUR2025_per_MWh_th",
        "direct_physical_CO2_t_per_MWh_th",
        "chargeable_CO2_t_per_MWh_th",
        "capture_rate",
        "residual_CO2_t_per_MWh_th",
        "CO2_price_EUR2025_per_t",
        "other_variable_cost_EUR2025_per_MWh_el",
        "marginal_cost_EUR2025_per_MWh_el",
    ]
    out: dict[str, Any] = {}
    for field in fields:
        unique = sorted(set(selected[field].astype(str)))
        if len(unique) != 1:
            raise ValueError(f"Conflicting Phase-4B values for {year}/{technology}/{field}: {unique}")
        target = "efficiency_el" if field == "efficiency" else field
        out[target] = unique[0]
    available = selected.loc[selected["static_availability_equivalent"].astype(str).str.strip().ne("")]
    if available.empty:
        out["static_availability_equivalent"] = ""
    else:
        weights = pd.to_numeric(available["p_nom_MW"]).to_numpy(float)
        values = pd.to_numeric(available["static_availability_equivalent"]).to_numpy(float)
        out["static_availability_equivalent"] = float(np.average(values, weights=weights))
    out["parameter_authority"] = "PHASE4B_FROZEN_BASE_CLASS_CAPACITY_WEIGHTED_TO_NATIONAL_IT"
    return out


def _italy_modes(record: dict[str, str], params: dict[str, Any]) -> dict[str, str]:
    technology = record["stage_a_static_class"]
    if technology.startswith("SOLAR_") or technology.startswith("WIND_") or technology.startswith("HYDRO_"):
        return {"availability_mode": "B3_TEMPORAL_PROFILE", "maintenance_family": "NOT_APPLICABLE"}
    if technology == "GEOTHERMAL":
        return {"availability_mode": "B4_CONSTANT_GEOTHERMAL", "maintenance_family": "GEOTHERMAL"}
    if technology == "NUCLEAR":
        return {"availability_mode": "B4_BLK007_NUCLEAR", "maintenance_family": "NUCLEAR_BLK007"}
    if "CCGT" in technology:
        family = "CCGT"
    elif "GT_OCGT" in technology:
        family = "GT_OCGT"
    else:
        family = "TRADITIONAL_THERMAL"
    return {"availability_mode": "B4_BLK005_AGGREGATE", "maintenance_family": family}


def _b1_profile_class(record: dict[str, str]) -> str:
    carrier = record["mapped_carrier"]
    if carrier == "SOLAR_PV":
        return "SOLAR_PV"
    if carrier == "SOLAR_CSP":
        return "CSP"
    if carrier in {"WIND_ONSHORE", "WIND_OFFSHORE"}:
        return carrier
    if carrier == "HYDRO_NON_PHS":
        return {
            "ror_hydro": "HYDRO_RUN_OF_RIVER",
            "reservoir_hydro": "HYDRO_RESERVOIR",
            "small_hydro": "HYDRO_SMALL_UNSPLIT",
        }.get(record["source_group"], "HYDRO_NON_PHS_UNSPLIT")
    return ""


def _b2_profile_class(record: dict[str, str]) -> str:
    technology = record["stage_a_static_class"]
    if technology.startswith("SOLAR_") or technology.startswith("WIND_") or technology.startswith("HYDRO_"):
        return technology
    return ""


def build_generator_parameters(
    config: dict[str, Any], paths: dict[str, Path]
) -> pd.DataFrame:
    b1 = _read_csv(paths["external_generators"])
    b2 = _read_csv(paths["italy_generators"])
    phase4b = _read_csv(paths["phase4b_directory"] / "MEM_generators_static_candidate_v2.csv")
    vre_map, hydro_map = _profile_lookup(paths)
    rows: list[dict[str, Any]] = []

    for record in b1.to_dict(orient="records"):
        params = _external_template(record, config)
        profile_class = _b1_profile_class(record)
        profile_id = ""
        if profile_class:
            lookup = hydro_map if record["mapped_carrier"] == "HYDRO_NON_PHS" else vre_map
            class_column = "hydro_class" if lookup is hydro_map else "stage_a_static_or_profile_class"
            profile_id = _find_profile(lookup, record["country_code"], profile_class, int(record["year"]), class_column)
        static_availability = ""
        if params["availability_mode"] == "B4_CONSTANT_GEOTHERMAL":
            static_availability = float(config["availability"]["geothermal_constant"])
        elif params["availability_mode"] == "B4_BLK007_NUCLEAR":
            static_availability = float(config["availability"]["nuclear_mean"])
        elif params["availability_mode"] == "B4_BLK005_AGGREGATE":
            hours = float(config["availability"]["maintenance_hours"][params["maintenance_family"]])
            static_availability = float(config["availability"]["outside_maintenance_p_max_pu_external"]) * (1 - hours / 8760)
        rows.append(
            {
                "asset_id": record["asset_id"],
                "country_code": record["country_code"],
                "year": int(record["year"]),
                "scenario": "Base",
                "static_source_family": "ETX7B1_EXTERNAL",
                "source_static_class": record["harmonised_group"],
                "source_group": record["source_group"],
                "source_carrier": record["mapped_carrier"],
                "p_nom_MW": float(record["p_nom_MW"]),
                "p_nom_extendable": False,
                "CHP_flag": _is_chp(record["source_group"], record["mapped_carrier"]),
                "CCS_flag": False,
                "temporal_profile_id": profile_id,
                "static_availability_equivalent": static_availability,
                **params,
                "common_carbon_framework": "COMMON_STAGE_A_FORWARD_CARBON_FRAMEWORK",
                "annual_energy_constraint_type": "NONE_HARD",
                "runtime_status": "ETX7B4_OPERATING_PARAMETERS_COMPLETE",
                "source_id": record["source_id"],
                "source_row": record["source_row"],
                "evidence_class": record["evidence_class"],
                "quality": record["quality"],
            }
        )

    for record in b2.to_dict(orient="records"):
        params = _weighted_phase4b_row(record, phase4b)
        modes = _italy_modes(record, params)
        efficiency = params["efficiency_el"]
        eff_value = float(efficiency) if str(efficiency).strip() else None
        fuel_price = float(params["fuel_price_EUR2025_per_MWh_th"])
        chargeable = float(params["chargeable_CO2_t_per_MWh_th"])
        carbon = float(params["CO2_price_EUR2025_per_t"])
        fuel_component = fuel_price / eff_value if eff_value else 0.0
        carbon_component = carbon * chargeable / eff_value if eff_value else 0.0
        profile_class = _b2_profile_class(record)
        profile_id = ""
        if profile_class:
            lookup = hydro_map if profile_class.startswith("HYDRO_") else vre_map
            class_column = "hydro_class" if lookup is hydro_map else "stage_a_static_or_profile_class"
            profile_id = _find_profile(lookup, "IT", profile_class, int(record["year"]), class_column)
        rows.append(
            {
                "asset_id": record["asset_id"],
                "country_code": "IT",
                "year": int(record["year"]),
                "scenario": "Base",
                "static_source_family": "ETX7B2_ITALY",
                "source_static_class": record["stage_a_static_class"],
                "source_group": record["parent_capacity_technology"],
                "source_carrier": record["carrier"],
                "p_nom_MW": float(record["p_nom_MW"]),
                "p_nom_extendable": False,
                "CHP_flag": str(record["CHP_flag"]).lower() == "true",
                "CCS_flag": str(record["CCS_flag"]).lower() == "true",
                "mapped_dispatch_fuel": record["fuel"],
                "template_id": f"ITALY_PHASE4B_{record['stage_a_static_class']}",
                "temporal_profile_id": profile_id,
                "efficiency_el": efficiency,
                "VOM_EUR2025_per_MWh_el": params["VOM_EUR2025_per_MWh_el"],
                "fuel_price_EUR2025_per_MWh_th": params["fuel_price_EUR2025_per_MWh_th"],
                "direct_physical_CO2_t_per_MWh_th": params["direct_physical_CO2_t_per_MWh_th"],
                "chargeable_CO2_t_per_MWh_th": params["chargeable_CO2_t_per_MWh_th"],
                "capture_rate": params["capture_rate"],
                "residual_CO2_t_per_MWh_th": params["residual_CO2_t_per_MWh_th"],
                "reported_non_ETS_physical_CO2_t_per_MWh_el": 1996060 / (25023 * 0.6032 * 1000) if record["stage_a_static_class"] == "GEOTHERMAL" else 0.0,
                "CO2_price_EUR2025_per_t": params["CO2_price_EUR2025_per_t"],
                "fuel_component_EUR2025_per_MWh_el": fuel_component,
                "carbon_component_EUR2025_per_MWh_el": carbon_component,
                "other_variable_cost_EUR2025_per_MWh_el": params["other_variable_cost_EUR2025_per_MWh_el"],
                "marginal_cost_EUR2025_per_MWh_el": params["marginal_cost_EUR2025_per_MWh_el"],
                "CAPEX_in_marginal_cost": False,
                "fixed_OPEX_in_marginal_cost": False,
                **modes,
                "static_availability_equivalent": params["static_availability_equivalent"],
                "parameter_authority": params["parameter_authority"],
                "common_carbon_framework": "COMMON_STAGE_A_FORWARD_CARBON_FRAMEWORK",
                "annual_energy_constraint_type": record["annual_energy_constraint_type"],
                "runtime_status": "ETX7B4_OPERATING_PARAMETERS_COMPLETE",
                "source_id": record["source_contract"],
                "source_row": "AGGREGATED_ETX7B2_PROVENANCE",
                "evidence_class": record["evidence_class"],
                "quality": "FROZEN_ACCEPTED",
            }
        )
    return pd.DataFrame.from_records(rows).sort_values(["year", "country_code", "asset_id"], kind="stable").reset_index(drop=True)


def build_availability(
    generators: pd.DataFrame, config: dict[str, Any], paths: dict[str, Path]
) -> pd.DataFrame:
    snapshots = pd.read_parquet(paths["temporal_snapshots"])["snapshot"]
    nuclear = pd.read_csv(paths["nuclear_profile"])["fleet_p_max_pu"].to_numpy(dtype=float)
    if len(snapshots) != 8760 or len(nuclear) != 8760:
        raise ValueError("Accepted B3/BLK-007 chronology does not contain 8,760 hours")
    frames: list[pd.DataFrame] = []
    applicable = generators.loc[~generators["availability_mode"].eq("B3_TEMPORAL_PROFILE")]
    for record in applicable.to_dict(orient="records"):
        if record["availability_mode"] == "B4_BLK007_NUCLEAR":
            offset = 0 if record["country_code"] == "IT" else int(hashlib.sha256(record["asset_id"].encode()).hexdigest()[:8], 16) % 8760
            values = np.roll(nuclear, offset)
            rule = "BLK007_ACCEPTED_PROFILE_STABLE_SHA256_ROLL"
        else:
            values = np.full(8760, float(record["static_availability_equivalent"]), dtype=float)
            rule = record["availability_mode"]
        frames.append(
            pd.DataFrame(
                {
                    "snapshot": snapshots,
                    "asset_id": record["asset_id"],
                    "country_code": record["country_code"],
                    "year": int(record["year"]),
                    "availability_class": record["maintenance_family"],
                    "p_max_pu": values,
                    "profile_rule": rule,
                    "historical_capacity_factor_used": False,
                    "slow_CDP_factor_used": False,
                    "status": "ETX7B4_TECHNICAL_AVAILABILITY_COMPLETE",
                }
            )
        )
    return pd.concat(frames, ignore_index=True).sort_values(["year", "asset_id", "snapshot"], kind="stable").reset_index(drop=True)


def build_storage_parameters(
    config: dict[str, Any], paths: dict[str, Path]
) -> pd.DataFrame:
    b1 = _read_csv(paths["external_storage"])
    b2 = _read_csv(paths["italy_storage"])
    rows: list[dict[str, Any]] = []
    for family, frame in (("ETX7B1_EXTERNAL", b1), ("ETX7B2_ITALY", b2)):
        for record in frame.to_dict(orient="records"):
            storage_class = record["storage_class"]
            is_phs = storage_class == "PHS" or record.get("storage_family", "") == "PHS" or "PHS_" in storage_class
            rte = float(config["storage"]["phs_round_trip_efficiency"] if is_phs else config["storage"]["bess_round_trip_efficiency"])
            source_id = record.get("charge_power_source_id", record.get("source_contract", ""))
            source_row = record.get("charge_power_source_row", "AGGREGATED_ETX7B2_PROVENANCE")
            rows.append(
                {
                    "storage_id": record["storage_id"],
                    "country_code": record["country_code"],
                    "year": int(record["year"]),
                    "scenario": "Base",
                    "static_source_family": family,
                    "storage_family": "PHS" if is_phs else "BESS",
                    "storage_class": storage_class,
                    "distributed_vs_grid_flag": record["distributed_vs_grid_flag"],
                    "charge_power_MW": float(record["charge_power_MW"]),
                    "discharge_power_MW": float(record["discharge_power_MW"]),
                    "energy_MWh": float(record["energy_MWh"]),
                    "p_nom_extendable": False,
                    "e_nom_extendable": False,
                    "round_trip_efficiency": rte,
                    "charge_efficiency": math.sqrt(rte),
                    "discharge_efficiency": math.sqrt(rte),
                    "standing_loss_per_hour": float(config["storage"]["standing_loss_per_hour"]),
                    "soc_min_pu": float(config["storage"]["soc_min_pu"]),
                    "soc_max_pu": float(config["storage"]["soc_max_pu"]),
                    "initial_state_rule": config["storage"]["initial_state_rule"],
                    "terminal_state_rule": config["storage"]["terminal_state_rule"],
                    "dispatch_availability_fraction": float(config["storage"]["dispatch_availability_fraction"]),
                    "distributed_bess_treatment": config["storage"]["distributed_bess_dispatch_rule"] if not is_phs else "NOT_APPLICABLE",
                    "runtime_state_count": 2 if family == "ETX7B2_ITALY" and is_phs else 1,
                    "component_pattern_later": "ONE_STORE_PLUS_SEPARATE_FIXED_CHARGE_AND_DISCHARGE_LINKS",
                    "source_id": source_id,
                    "source_row": source_row,
                    "evidence_class": record.get("charge_power_evidence_class", record.get("evidence_class", "")),
                    "runtime_status": "ETX7B4_STORAGE_OPERATING_PARAMETERS_COMPLETE",
                }
            )
    return pd.DataFrame.from_records(rows).sort_values(["year", "country_code", "storage_id"], kind="stable").reset_index(drop=True)


def _italy_phs_base_split(paths: dict[str, Path]) -> dict[str, float]:
    method = _read_csv(paths["italy_phs_method_c"])
    pumps = _read_csv(paths["italy_phs_pump_allocation"])
    contract = _read_csv(paths["italy_phs_contract"])
    top_ids = {"H10", "H15", "H17", "H14"}
    top_energy = method.loc[method["top4_flag"].str.lower().eq("true")].copy()
    top_pumps = pumps.loc[pumps["plant_id"].isin(top_ids)].copy()
    zone = contract.set_index("zone")
    nord_ratio = float(zone.loc["NORD", "discharge_power_MW_NET"])
    hydro_map = _read_csv(paths["italy_hydro_mapping"])
    pure_nord = float(hydro_map.loc[(hydro_map["zone"].eq("NORD")) & (hydro_map["hydro_class"].eq("PURE_PHS")), "p_nom_MW_NET"].iloc[0])
    mixed_nord = float(hydro_map.loc[(hydro_map["zone"].eq("NORD")) & (hydro_map["hydro_class"].eq("MIXED_PHS")), "p_nom_MW_NET"].iloc[0])
    pure_ratio = pure_nord / nord_ratio
    top_energy_sum = float(pd.to_numeric(top_energy["operational_e_nom_MWh"]).sum())
    top_pump_sum = float(pd.to_numeric(top_pumps["model_pump_power_MW"]).sum())
    residual_energy = {z: float(zone.loc[z, "operational_energy_MWh"]) - float(pd.to_numeric(top_energy.loc[top_energy["zone"].eq(z), "operational_e_nom_MWh"]).sum()) for z in zone.index}
    residual_pump = {z: float(zone.loc[z, "pump_power_MW"]) - float(pd.to_numeric(top_pumps.loc[top_pumps["zone"].eq(z), "model_pump_power_MW"]).sum()) for z in zone.index}
    pure_energy = top_energy_sum + residual_energy["NORD"] * pure_ratio + residual_energy["SICI"]
    pure_pump = top_pump_sum + residual_pump["NORD"] * pure_ratio + residual_pump["SICI"]
    return {
        "pure_discharge": 3969.57561,
        "mixed_discharge": 3282.72439,
        "pure_pump": pure_pump,
        "mixed_pump": 6400.0 - pure_pump,
        "pure_energy": pure_energy,
        "mixed_energy": 53000.0 - pure_energy,
        "top4_energy": top_energy_sum,
        "residual_energy": 53000.0 - top_energy_sum,
        "top4_pump": top_pump_sum,
        "nord_residual_pure_ratio": pure_ratio,
    }


def build_italy_phs_mapping(paths: dict[str, Path]) -> pd.DataFrame:
    split = _italy_phs_base_split(paths)
    rows: list[dict[str, Any]] = []
    for year in HORIZONS:
        for hydro_class in ("PURE_PHS", "MIXED_PHS"):
            pure = hydro_class == "PURE_PHS"
            rows.append(
                {
                    "state_id": f"ETX7B4_IT_{year}_{hydro_class}_STATE",
                    "country_code": "IT",
                    "year": year,
                    "scenario": "Base",
                    "hydro_class": hydro_class,
                    "source_parent_storage_id": f"ETX7B2_IT_{year}_PHS_COMBINED_OPERATIONAL_CONTROL",
                    "discharge_power_MW": split["pure_discharge" if pure else "mixed_discharge"],
                    "charge_power_MW": split["pure_pump" if pure else "mixed_pump"],
                    "operational_energy_MWh": split["pure_energy" if pure else "mixed_energy"],
                    "round_trip_efficiency": 0.75,
                    "charge_efficiency": math.sqrt(0.75),
                    "discharge_efficiency": math.sqrt(0.75),
                    "natural_inflow_required": not pure,
                    "natural_inflow_profile_id": f"ETX7B3_IT_HYDRO_RESERVOIR_2019" if not pure else "",
                    "grid_charging_allowed": True,
                    "spill_allowed": not pure,
                    "shared_water_state_count": 1,
                    "state_receives": "ELECTRICAL_PUMPING" if pure else "NATURAL_INFLOW_AND_ELECTRICAL_PUMPING",
                    "state_supplies": "TURBINE" if pure else "TURBINE_AND_SPILL",
                    "terminal_state_rule": "CYCLIC_ANNUAL",
                    "method_c_top4_energy_block_MWh": split["top4_energy"],
                    "method_c_residual_energy_block_MWh": split["residual_energy"],
                    "nord_residual_pure_share": split["nord_residual_pure_ratio"],
                    "allocation_rule": "TOP4_TO_PURE; CSUD_RESIDUAL_TO_MIXED; SICI_RESIDUAL_TO_PURE; SARD_RESIDUAL_TO_MIXED; NORD_RESIDUAL_PROPORTIONAL_TO_FROZEN_PURE_MIXED_DISCHARGE",
                    "physical_HPHS_energy_used_as_e_nom": False,
                    "status": "ETX7B4_ITALY_PHS_STATE_MAPPING_COMPLETE",
                }
            )
    return pd.DataFrame.from_records(rows)


def _eia_hydro_controls(paths: dict[str, Path]) -> dict[str, float]:
    frame = pd.read_csv(paths["pypsa_eur_eia_hydro_generation"], skiprows=2)
    frame["_country_name"] = frame["Unnamed: 1"].astype(str).str.strip()
    names = {"AT": "Austria", "CH": "Switzerland", "FR": "France", "GR": "Greece", "HR": "Croatia", "ME": "Montenegro", "SI": "Slovenia"}
    result: dict[str, float] = {}
    for code, name in names.items():
        selected = frame.loc[frame["_country_name"].eq(name), "2019"]
        if len(selected) != 1:
            raise ValueError(f"Accepted EIA hydro control missing for {code}/{name}")
        result[code] = float(selected.iloc[0]) * 1_000_000.0
    result["IT"] = 52_391_704.319
    return result


def _hydro_capacity_proxies(paths: dict[str, Path]) -> dict[str, dict[str, float]]:
    frame = pd.read_csv(paths["pypsa_eur_hydro_capacities"]).set_index("Country")
    out: dict[str, dict[str, float]] = {}
    for code in ("AT", "CH", "FR", "GR", "HR", "SI", "IT"):
        row = frame.loc[code]
        out[code] = {
            "stateful_share": min(1.0, float(row["p_nom_store[GW]"]) / float(row["p_nom_discharge[GW]"])),
            "state_energy_MWh": float(row["E_store[TWh]"]) * 1_000_000.0,
        }
    out["ME"] = {"stateful_share": 1058.4 / (78.853 + 1058.4), "state_energy_MWh": 582_400.0}
    out["TN"] = {"stateful_share": 0.0, "state_energy_MWh": 0.0}
    return out


def _hydro_profile_map(paths: dict[str, Path]) -> tuple[dict[tuple[str, str], str], dict[str, float]]:
    hydro = pd.read_parquet(paths["temporal_hydro"])
    unique = hydro[["country_code", "hydro_class", "profile_id"]].drop_duplicates()
    mapping = {(row.country_code, row.hydro_class): row.profile_id for row in unique.itertuples()}
    peaks = hydro.groupby("profile_id")["value"].max().astype(float).to_dict()
    return mapping, peaks


def build_hydro_parameters(
    generators: pd.DataFrame,
    storage: pd.DataFrame,
    italy_phs: pd.DataFrame,
    paths: dict[str, Path],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    controls = _eia_hydro_controls(paths)
    proxies = _hydro_capacity_proxies(paths)
    profiles, peaks = _hydro_profile_map(paths)
    b1 = _read_csv(paths["external_generators"])
    b2 = _read_csv(paths["italy_generators"])
    rows: list[dict[str, Any]] = []

    def add_slice(
        *, source_asset_id: str, country: str, year: int, hydro_class: str,
        p_nom: float, profile_class: str, state_energy: float, state_required: bool,
        source_rule: str,
    ) -> None:
        profile_id = profiles[(country, profile_class)]
        rows.append({
            "operational_slice_id": f"ETX7B4_{country}_{year}_{_safe_id(source_asset_id)}_{hydro_class}",
            "source_asset_id": source_asset_id,
            "country_code": country,
            "year": year,
            "scenario": "Base",
            "hydro_class": hydro_class,
            "turbine_power_MW": p_nom,
            "pump_power_MW": 0.0,
            "operational_state_energy_MWh": state_energy,
            "natural_inflow_required": True,
            "natural_inflow_profile_id": profile_id,
            "grid_charging_allowed": False,
            "spill_allowed": True,
            "energy_state_required": state_required,
            "terminal_state_rule": "CYCLIC_ANNUAL" if state_required else "NOT_APPLICABLE",
            "turbine_efficiency": 0.9,
            "state_receives": "NATURAL_INFLOW",
            "state_supplies": "TURBINE_AND_SPILL" if state_required else "DIRECT_INFLOW_LIMITED_TURBINE",
            "component_archetype_later": "NATURALLY_CHARGED_STORE_PLUS_TURBINE_LINK" if state_required else "INFLOW_LIMITED_GENERATOR",
            "operational_mapping_rule": source_rule,
            "physical_HPHS_energy_MWh_used": 0.0,
            "physical_HDAM_energy_MWh_used": 0.0,
            "status": "ETX7B4_HYDRO_RUNTIME_MAPPING_COMPLETE",
        })

    for record in b1.loc[b1["mapped_carrier"].eq("HYDRO_NON_PHS")].to_dict(orient="records"):
        country, year, p_nom = record["country_code"], int(record["year"]), float(record["p_nom_MW"])
        group = record["source_group"]
        if group == "ror_hydro":
            add_slice(source_asset_id=record["asset_id"], country=country, year=year, hydro_class="RUN_OF_RIVER", p_nom=p_nom, profile_class="HYDRO_RUN_OF_RIVER", state_energy=0.0, state_required=False, source_rule="DIRECT_ETX7A_ROR_CLASS")
        elif group == "reservoir_hydro":
            add_slice(source_asset_id=record["asset_id"], country=country, year=year, hydro_class="RESERVOIR", p_nom=p_nom, profile_class="HYDRO_RESERVOIR", state_energy=proxies[country]["state_energy_MWh"], state_required=True, source_rule="DIRECT_ETX7A_RESERVOIR_CLASS_PLUS_PYPSA_EUR_STATE_PROXY")
        elif group == "small_hydro":
            add_slice(source_asset_id=record["asset_id"], country=country, year=year, hydro_class="SMALL_HYDRO_INFLOW_LIMITED", p_nom=p_nom, profile_class="HYDRO_SMALL_UNSPLIT", state_energy=0.0, state_required=False, source_rule="DIRECT_ETX7A_SMALL_HYDRO_CLASS")
        else:
            share = proxies[country]["stateful_share"]
            profile_class = "HYDRO_NON_PHS_UNSPLIT"
            stateful = p_nom * share
            inflow_limited = p_nom - stateful
            if inflow_limited > 1e-12:
                add_slice(source_asset_id=record["asset_id"], country=country, year=year, hydro_class="UNSPLIT_INFLOW_LIMITED_SLICE", p_nom=inflow_limited, profile_class=profile_class, state_energy=0.0, state_required=False, source_rule="PYPSA_EUR_OPERATIONAL_SLICE_FROM_FROZEN_ETX7A_UNSPLIT_CAPACITY")
            if stateful > 1e-12:
                add_slice(source_asset_id=record["asset_id"], country=country, year=year, hydro_class="UNSPLIT_RESERVOIR_SLICE", p_nom=stateful, profile_class=profile_class, state_energy=proxies[country]["state_energy_MWh"], state_required=True, source_rule="PYPSA_EUR_OPERATIONAL_SLICE_FROM_FROZEN_ETX7A_UNSPLIT_CAPACITY")

    italy_natural = b2.loc[b2["stage_a_static_class"].isin(["HYDRO_RUN_OF_RIVER", "HYDRO_BASIN_PONDAGE", "HYDRO_RESERVOIR"])]
    for record in italy_natural.to_dict(orient="records"):
        cls = record["stage_a_static_class"].replace("HYDRO_", "")
        state_required = cls != "RUN_OF_RIVER"
        conventional = italy_natural.loc[italy_natural["year"].eq(record["year"])]
        state_energy = 0.0
        if state_required:
            denominator = pd.to_numeric(conventional.loc[conventional["stage_a_static_class"].isin(["HYDRO_BASIN_PONDAGE", "HYDRO_RESERVOIR"]), "p_nom_MW"]).sum()
            state_energy = proxies["IT"]["state_energy_MWh"] * float(record["p_nom_MW"]) / denominator
        add_slice(source_asset_id=record["asset_id"], country="IT", year=int(record["year"]), hydro_class=cls, p_nom=float(record["p_nom_MW"]), profile_class=record["stage_a_static_class"], state_energy=state_energy, state_required=state_required, source_rule="FROZEN_ETX7B2_CLASS_PLUS_ACCEPTED_PYPSA_EUR_OPERATIONAL_STATE_PROXY")

    # Add fixed PHS states without duplicating their combined parent storage row.
    external_phs = storage.loc[(storage["country_code"].ne("IT")) & storage["storage_family"].eq("PHS")]
    for record in external_phs.to_dict(orient="records"):
        rows.append({
            "operational_slice_id": f"ETX7B4_{record['country_code']}_{record['year']}_{_safe_id(record['storage_id'])}_PURE_PHS",
            "source_asset_id": record["storage_id"], "country_code": record["country_code"], "year": int(record["year"]), "scenario": "Base", "hydro_class": "PURE_PHS",
            "turbine_power_MW": float(record["discharge_power_MW"]), "pump_power_MW": float(record["charge_power_MW"]), "operational_state_energy_MWh": float(record["energy_MWh"]),
            "natural_inflow_required": False, "natural_inflow_profile_id": "", "grid_charging_allowed": True, "spill_allowed": False, "energy_state_required": True,
            "terminal_state_rule": "CYCLIC_ANNUAL", "turbine_efficiency": math.sqrt(0.75), "state_receives": "ELECTRICAL_PUMPING", "state_supplies": "TURBINE",
            "component_archetype_later": "ONE_STORE_PLUS_FIXED_PUMP_AND_TURBINE_LINKS", "operational_mapping_rule": "FROZEN_ETX7A_PHS_POWER_ENERGY_PLUS_ACCEPTED_RUNTIME_CONVENTION",
            "physical_HPHS_energy_MWh_used": 0.0, "physical_HDAM_energy_MWh_used": 0.0, "status": "ETX7B4_HYDRO_RUNTIME_MAPPING_COMPLETE",
        })
    for record in italy_phs.to_dict(orient="records"):
        rows.append({
            "operational_slice_id": record["state_id"], "source_asset_id": record["source_parent_storage_id"], "country_code": "IT", "year": int(record["year"]), "scenario": "Base", "hydro_class": record["hydro_class"],
            "turbine_power_MW": float(record["discharge_power_MW"]), "pump_power_MW": float(record["charge_power_MW"]), "operational_state_energy_MWh": float(record["operational_energy_MWh"]),
            "natural_inflow_required": bool(record["natural_inflow_required"]), "natural_inflow_profile_id": record["natural_inflow_profile_id"], "grid_charging_allowed": True, "spill_allowed": bool(record["spill_allowed"]), "energy_state_required": True,
            "terminal_state_rule": "CYCLIC_ANNUAL", "turbine_efficiency": math.sqrt(0.75), "state_receives": record["state_receives"], "state_supplies": record["state_supplies"],
            "component_archetype_later": "ONE_SHARED_WATER_STORE_PLUS_FIXED_PUMP_TURBINE_AND_SPILL" if record["hydro_class"] == "MIXED_PHS" else "ONE_STORE_PLUS_FIXED_PUMP_AND_TURBINE_LINKS",
            "operational_mapping_rule": record["allocation_rule"], "physical_HPHS_energy_MWh_used": 0.0, "physical_HDAM_energy_MWh_used": 0.0, "status": "ETX7B4_HYDRO_RUNTIME_MAPPING_COMPLETE",
        })

    frame = pd.DataFrame.from_records(rows).sort_values(["year", "country_code", "operational_slice_id"], kind="stable").reset_index(drop=True)
    frame["annual_electrical_generation_reference_MWh"] = 0.0
    frame["annual_natural_inflow_MWh_water"] = 0.0
    frame["hourly_inflow_scaling_MWh_water_per_unit_share"] = 0.0
    frame["annual_reference_role"] = "NOT_APPLICABLE_PHS"
    for (country, year), selected in frame.loc[frame["natural_inflow_required"]].groupby(["country_code", "year"]):
        indexes = selected.index
        p_total = selected["turbine_power_MW"].astype(float).sum()
        if country == "TN":
            profile_id = selected["natural_inflow_profile_id"].iloc[0]
            annual = p_total / peaks[profile_id]
            role = "ACCEPTED_B3_RUNOFF_PEAK_NORMALIZATION_TO_FROZEN_TURBINE_MW"
        else:
            annual = controls[country]
            role = "TERNA_2024_RENEWABLE_SOURCE_HYDRO_CONTROL" if country == "IT" else "PYPSA_EUR_EIA_2019_HYDRO_GENERATION_REFERENCE"
        shares = selected["turbine_power_MW"].astype(float) / p_total
        electrical = annual * shares
        efficiencies = selected["turbine_efficiency"].astype(float)
        water = electrical / efficiencies
        frame.loc[indexes, "annual_electrical_generation_reference_MWh"] = electrical
        frame.loc[indexes, "annual_natural_inflow_MWh_water"] = water
        frame.loc[indexes, "hourly_inflow_scaling_MWh_water_per_unit_share"] = water
        frame.loc[indexes, "annual_reference_role"] = role
    inflow = frame.loc[frame["natural_inflow_required"], [
        "operational_slice_id", "country_code", "year", "hydro_class", "natural_inflow_profile_id",
        "annual_electrical_generation_reference_MWh", "annual_natural_inflow_MWh_water",
        "hourly_inflow_scaling_MWh_water_per_unit_share", "turbine_efficiency", "annual_reference_role",
        "spill_allowed", "status",
    ]].copy()
    inflow["b3_temporal_quantity_type"] = "NORMALIZED_NATURAL_INFLOW_SHARE"
    inflow["runtime_hourly_formula"] = "B3_NORMALIZED_SHARE_X_HOURLY_INFLOW_SCALING_MWH_WATER"
    return frame, inflow


def build_constraint_dispositions(generators: pd.DataFrame, paths: dict[str, Path]) -> pd.DataFrame:
    soft = _read_csv(paths["phase4b_directory"] / "MEM_Source_Scenario_Energy_Soft_Controls.csv")
    soft_map = {row.source_category: row for row in soft.itertuples()}
    rows: list[dict[str, Any]] = []
    for record in generators.to_dict(orient="records"):
        parent = record["source_group"] if record["country_code"] == "IT" else ""
        control = soft_map.get(parent) if record["country_code"] == "IT" and int(record["year"]) == 2050 else None
        if control is None:
            disposition, control_id, value = "NO_ADDITIONAL_ANNUAL_CONSTRAINT", "", ""
            source = "ETX7A_CARRIER_GOVERNANCE_OR_PHASE4B_NONE_HARD"
        else:
            disposition = "SHARED_EX_POST_SOFT_CONTROL_NOT_A_HARD_CONSTRAINT"
            control_id = f"ETX7B4_IT_2050_{parent}_SOFT_CONTROL"
            value = float(control.source_scenario_energy) * 1_000_000.0
            source = control.source
        rows.append({
            "asset_id": record["asset_id"], "country_code": record["country_code"], "year": int(record["year"]), "source_class": record["source_static_class"],
            "disposition": disposition, "shared_control_id": control_id, "soft_control_MWh_el": value, "hard_e_sum_min_MWh": "", "hard_e_sum_max_MWh": "",
            "source": source, "duplicate_resource_budget_allowed": False, "status": "ETX7B4_LIMITED_ENERGY_DISPOSITION_COMPLETE",
        })
    return pd.DataFrame.from_records(rows).sort_values(["year", "country_code", "asset_id"], kind="stable").reset_index(drop=True)


def build_inheritance_matrix(
    generators: pd.DataFrame, storage: pd.DataFrame, hydro: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    generator_fields = [
        ("FUEL_IDENTITY", "mapped_dispatch_fuel", "fuel/energy carrier"), ("EFFICIENCY", "efficiency_el", "per unit electric"),
        ("VOM", "VOM_EUR2025_per_MWh_el", "EUR2025/MWh_el"), ("FUEL_PRICE", "fuel_price_EUR2025_per_MWh_th", "EUR2025/MWh_th"),
        ("PHYSICAL_EMISSIONS", "direct_physical_CO2_t_per_MWh_th", "tCO2/MWh_th"), ("CHARGEABLE_EMISSIONS", "chargeable_CO2_t_per_MWh_th", "tCO2/MWh_th"),
        ("CAPTURE_RATE", "capture_rate", "per unit"), ("COMMON_CO2_PRICE", "CO2_price_EUR2025_per_t", "EUR2025/tCO2"),
        ("CCS_OR_OTHER_VARIABLE_COST", "other_variable_cost_EUR2025_per_MWh_el", "EUR2025/MWh_el"), ("MARGINAL_COST", "marginal_cost_EUR2025_per_MWh_el", "EUR2025/MWh_el"),
        ("TECHNICAL_AVAILABILITY", "availability_mode", "rule"), ("ANNUAL_ENERGY_DISPOSITION", "annual_energy_constraint_type", "rule"),
    ]
    for record in generators.to_dict(orient="records"):
        for family, field, unit in generator_fields:
            rows.append({"target_record_type": "GENERATOR", "target_record_id": record["asset_id"], "country_code": record["country_code"], "year": record["year"], "parameter_family": family, "accepted_authority": record["parameter_authority"], "source_value": record[field], "source_unit": unit, "target_value": record[field], "target_unit": unit, "transformation": "DIRECT_OR_ACCEPTED_DETERMINISTIC_TEMPLATE_MAPPING", "applicability": "APPLICABLE", "evidence_or_assumption_class": record["evidence_class"], "runtime_disposition": "IMPLEMENT", "status": "RESOLVED_EXACTLY_ONCE"})
    storage_fields = [("ROUND_TRIP_EFFICIENCY", "round_trip_efficiency"), ("CHARGE_EFFICIENCY", "charge_efficiency"), ("DISCHARGE_EFFICIENCY", "discharge_efficiency"), ("STANDING_LOSS", "standing_loss_per_hour"), ("SOC_BOUNDARY", "terminal_state_rule"), ("DISPATCH_AVAILABILITY", "dispatch_availability_fraction")]
    for record in storage.to_dict(orient="records"):
        for family, field in storage_fields:
            rows.append({"target_record_type": "STORAGE", "target_record_id": record["storage_id"], "country_code": record["country_code"], "year": record["year"], "parameter_family": family, "accepted_authority": "ACCEPTED_MEM_BESS_PHS_RUNTIME_CONVENTION", "source_value": record[field], "source_unit": "RULE_OR_PER_UNIT", "target_value": record[field], "target_unit": "RULE_OR_PER_UNIT", "transformation": "DIRECT_ACCEPTED_MAPPING", "applicability": "APPLICABLE", "evidence_or_assumption_class": record["evidence_class"], "runtime_disposition": "IMPLEMENT", "status": "RESOLVED_EXACTLY_ONCE"})
    hydro_fields = [("ANNUAL_GENERATION_REFERENCE", "annual_electrical_generation_reference_MWh"), ("ANNUAL_NATURAL_INFLOW", "annual_natural_inflow_MWh_water"), ("TURBINE_EFFICIENCY", "turbine_efficiency"), ("OPERATIONAL_STATE_ENERGY", "operational_state_energy_MWh"), ("NATURAL_INFLOW", "natural_inflow_required"), ("GRID_CHARGING", "grid_charging_allowed"), ("SPILL", "spill_allowed"), ("STATE_BOUNDARY", "terminal_state_rule")]
    for record in hydro.to_dict(orient="records"):
        for family, field in hydro_fields:
            rows.append({"target_record_type": "HYDRO_SLICE", "target_record_id": record["operational_slice_id"], "country_code": record["country_code"], "year": record["year"], "parameter_family": family, "accepted_authority": record["operational_mapping_rule"], "source_value": record[field], "source_unit": "FIELD_SPECIFIC", "target_value": record[field], "target_unit": "FIELD_SPECIFIC", "transformation": "ACCEPTED_B4_OPERATIONAL_MAPPING", "applicability": "APPLICABLE", "evidence_or_assumption_class": "ACCEPTED_PROJECT_IMPLEMENTATION", "runtime_disposition": "IMPLEMENT", "status": "RESOLVED_EXACTLY_ONCE"})
    return pd.DataFrame.from_records(rows).sort_values(["target_record_type", "target_record_id", "parameter_family"], kind="stable").reset_index(drop=True)


def build_provenance(config: dict[str, Any], paths: dict[str, Path]) -> pd.DataFrame:
    items = [
        ("PHASE4B_ITALY", paths["phase4b_directory"] / "MEM_generators_static_candidate_v2.csv", "ITALIAN_ECONOMICS_AND_AVAILABILITY"),
        ("ETX7B1_EXTERNAL", paths["external_generators"], "EXTERNAL_STATIC_ASSET_IDENTITY_AND_CAPACITY"),
        ("ETX7B2_ITALY", paths["italy_generators"], "ITALY_NATIONAL_STATIC_ASSET_IDENTITY_AND_CAPACITY"),
        ("ETX7B3_TEMPORAL", paths["temporal_manifest"], "IMMUTABLE_TEMPORAL_AUTHORITY"),
        ("BLK007_NUCLEAR", paths["nuclear_profile"], "NUCLEAR_AVAILABILITY"),
        ("PYPSA_EUR_HYDRO_STATE", paths["pypsa_eur_hydro_capacities"], "CONVENTIONAL_HYDRO_OPERATIONAL_STATE_PROXY"),
        ("PYPSA_EUR_EIA_HYDRO", paths["pypsa_eur_eia_hydro_generation"], "HYDRO_ANNUAL_REFERENCE"),
        ("METHOD_C_PHS", paths["italy_phs_method_c"], "ITALY_PHS_OPERATIONAL_ENERGY_ALLOCATION"),
        ("B4_CONFIG", CONFIG_PATH, "CONTROLLING_IMPLEMENTATION_SPECIFICATION"),
    ]
    rows = []
    for source_id, path, role in items:
        rows.append({"source_id": source_id, "relative_or_absolute_path": path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path), "accepted_role": role, "transformation_boundary": "NO_STATIC_CAPACITY_OR_DEMAND_CHANGE", "status": "ACCEPTED_INHERITED_AUTHORITY"})
    return pd.DataFrame.from_records(rows)


def build_decision_register() -> pd.DataFrame:
    rows = [
        ("B4-D01", "COMMON_STAGE_A_FORWARD_CARBON_FRAMEWORK", "2040=104.5504;2050=400 EUR2025/tCO2", "IMPLEMENTED"),
        ("B4-D02", "BESS_RUNTIME", "RTE=0.90;symmetric legs;zero standing loss;cyclic annual SOC", "IMPLEMENTED"),
        ("B4-D03", "PHS_RUNTIME", "RTE=0.75;symmetric legs;zero standing loss;cyclic annual SOC;frozen asymmetric power", "IMPLEMENTED"),
        ("B4-D04", "ITALY_PHS_STATE_SPLIT", "PURE and MIXED separate;one shared water state for MIXED;Method-C totals preserved", "IMPLEMENTED"),
        ("B4-D05", "HYDRO_STATE_PROXY", "Pinned PyPSA-Eur state-energy proxy;B3 runoff scaled to accepted annual references", "IMPLEMENTED"),
        ("B4-D06", "PHYSICAL_RESERVOIR_EXCLUSION", "626.262 GWh HPHS and 5.748 TWh HDAM excluded from operational e_nom", "IMPLEMENTED"),
        ("B4-D07", "LIMITED_ENERGY", "Italy source scenario generation remains shared ex-post soft control;external none additional", "IMPLEMENTED"),
        ("B4-D08", "DIAGNOSTICS_REFERENCE_ONLY", "VOLL 15000 and external toll 0 retained for B5+;not instantiated", "DEFERRED_OUT_OF_SCOPE"),
    ]
    return pd.DataFrame(rows, columns=["decision_id", "parameter_family", "accepted_decision", "runtime_disposition"])


def build_b3_erratum(paths: dict[str, Path]) -> pd.DataFrame:
    provenance = _read_csv(paths["temporal_provenance"])
    targets = {"ETX7B3_MT_SOLAR_PV_2019": "MT solar", "ETX7B3_TN_CSP_2019": "TN CSP"}
    rows = []
    for profile_id, label in targets.items():
        selected = provenance.loc[provenance["profile_id"].eq(profile_id)]
        if len(selected) != 1:
            raise ValueError(f"B3 erratum profile not uniquely found: {profile_id}")
        record = selected.iloc[0]
        rows.append({"profile_id": profile_id, "profile_label": label, "b3_source": record["source"], "b3_source_file_or_dataset": record["source_file_or_dataset"], "accepted_b3_classification": record["evidence_or_provenance_class"], "prospective_corrected_classification": "OFFICIAL_TYNDP_PECD_2019", "hourly_values_changed": False, "b3_file_changed": False, "downstream_rule": "USE_PROSPECTIVE_CORRECTED_CLASSIFICATION", "status": "B4_SIDECAR_ERRATUM_ONLY"})
    return pd.DataFrame.from_records(rows)


def _manifest(output_dir: Path) -> pd.DataFrame:
    rows = []
    for key, filename in OUTPUT_FILES.items():
        if key == "manifest":
            continue
        path = output_dir / filename
        rows.append({"artifact": key, "relative_path": f"stage_a_inputs/operating_base_v1_0/{filename}", "bytes": path.stat().st_size, "rows": _row_count(path), "sha256": sha256_file(path), "schema_version": SCHEMA_VERSION, "status": "DETERMINISTIC_B4_OUTPUT"})
    return pd.DataFrame.from_records(rows).sort_values("artifact", kind="stable").reset_index(drop=True)


def build_operating_base_package(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *, config_path: Path = CONFIG_PATH,
) -> dict[str, Any]:
    config = load_config(config_path)
    paths = _source_paths(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    generators = build_generator_parameters(config, paths)
    availability = build_availability(generators, config, paths)
    storage = build_storage_parameters(config, paths)
    italy_phs = build_italy_phs_mapping(paths)
    hydro, hydro_inflow = build_hydro_parameters(generators, storage, italy_phs, paths)
    constraints = build_constraint_dispositions(generators, paths)
    inheritance = build_inheritance_matrix(generators, storage, hydro)
    provenance = build_provenance(config, paths)
    decisions = build_decision_register()
    erratum = build_b3_erratum(paths)
    frames = {"generators": generators, "availability": availability, "storage": storage, "italy_phs": italy_phs, "hydro": hydro, "hydro_inflow": hydro_inflow, "constraints": constraints, "inheritance": inheritance, "provenance": provenance, "decisions": decisions, "b3_erratum": erratum}
    for key, frame in frames.items():
        path = output_dir / OUTPUT_FILES[key]
        _write_parquet(frame, path) if path.suffix == ".parquet" else _write_csv(frame, path)
    manifest = _manifest(output_dir)
    _write_csv(manifest, output_dir / OUTPUT_FILES["manifest"])
    frames["manifest"] = manifest
    return {"frames": frames, "output_dir": output_dir}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build deterministic ETX-7B4 Stage-A Base operating parameters")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args()
    result = build_operating_base_package(args.output_dir, config_path=args.config)
    print(json.dumps({"status": "BUILT_NOT_VALIDATED", "output_dir": str(result["output_dir"]), "counts": {key: len(value) for key, value in result["frames"].items()}}, indent=2))


if __name__ == "__main__":
    main()

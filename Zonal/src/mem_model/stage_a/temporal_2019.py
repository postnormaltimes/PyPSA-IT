from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import yaml

from mem_model.stage_a.italy_static import sha256_file


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = ROOT / "config" / "stage_a_temporal_2019.yaml"
CORE_MARKETS = ("IT", "FR", "CH", "AT", "SI", "HR", "ME", "GR", "MT", "TN")
HORIZONS = (2040, 2050)
SCENARIO = "Base"
DASK_KWARGS = {"scheduler": "threads", "num_workers": 4}

ETX7B1_DIR = ROOT / "stage_a_inputs" / "etx7a_v1_0" / "static"
ETX7B2_DIR = ROOT / "stage_a_inputs" / "italy_base_v1_0" / "static"
PRE_PYPSA_DIR = ROOT / "pre_pypsa_inputs"

EXTERNAL_DEMAND = ETX7B1_DIR / "MEM_ETX7A_Annual_Demand_Static_v1.0.csv"
EXTERNAL_GENERATORS = ETX7B1_DIR / "MEM_ETX7A_Generators_Static_v1.0.csv"
ITALY_DEMAND = ETX7B2_DIR / "MEM_ETX7B2_IT_Annual_Demand_Static_v1.0.csv"
ITALY_GENERATORS = ETX7B2_DIR / "MEM_ETX7B2_IT_Generators_Static_v1.0.csv"
ITALY_ZONAL_DEMAND = PRE_PYPSA_DIR / "MEM_Annual_Zonal_Demand_Contract.csv"
ITALY_ZONAL_GENERATORS = PRE_PYPSA_DIR / "MEM_generators_static_final.csv"
ITALY_HYDRO_MAPPING = PRE_PYPSA_DIR / "MEM_Hydro_Static_Component_Mapping.csv"

OUTPUT_FILES = {
    "snapshots": "MEM_ETX7B3_Snapshot_Index_v1.0.parquet",
    "coverage": "MEM_ETX7B3_Profile_Requirement_and_Coverage_Registry_v1.0.csv",
    "load": "MEM_ETX7B3_Load_Hourly_v1.0.parquet",
    "vre": "MEM_ETX7B3_VRE_Availability_Hourly_v1.0.parquet",
    "hydro": "MEM_ETX7B3_Hydro_Temporal_Hourly_v1.0.parquet",
    "provenance": "MEM_ETX7B3_Profile_Provenance_v1.0.csv",
    "source_receipt": "MEM_ETX7B3_Source_and_Cutout_Receipt_v1.0.csv",
    "manifest": "MEM_ETX7B3_Temporal_Output_Manifest_v1.0.csv",
}

CACHE_FILES = {
    "vre_bus": "MEM_ETX7B3_PyPSA_Eur_Bus_VRE_2019_Cache_v1.0.parquet",
    "vre_mt_tn_offwind": "MEM_ETX7B3_MT_TN_Offwind_2019_Cache_v1.0.parquet",
    "runoff": "MEM_ETX7B3_PyPSA_Eur_Runoff_2019_Cache_v1.0.parquet",
}

PROFILE_FAMILIES = (
    "DEMAND",
    "SOLAR_PV",
    "SOLAR_PV_ROOFTOP",
    "SOLAR_PV_UTILITY",
    "WIND_ONSHORE",
    "WIND_OFFSHORE",
    "CSP",
    "HYDRO_RUN_OF_RIVER",
    "HYDRO_BASIN_PONDAGE",
    "HYDRO_RESERVOIR",
    "HYDRO_NON_PHS_UNSPLIT",
)

DEFERRED_FAMILIES = (
    "THERMAL_AVAILABILITY",
    "NUCLEAR_AVAILABILITY",
    "GEOTHERMAL_AVAILABILITY",
    "BESS_DISPATCH_SOC",
    "PHS_OPERATION_SOC",
)

EXTERNAL_VRE_GROUPS = {
    "solar_pv": "SOLAR_PV",
    "wind_onshore": "WIND_ONSHORE",
    "wind_offshore": "WIND_OFFSHORE",
    "solar_csp": "CSP",
}

ITALY_VRE_GROUPS = {
    "SOLAR_PV_ROOFTOP": "SOLAR_PV_ROOFTOP",
    "SOLAR_PV_UTILITY": "SOLAR_PV_UTILITY",
    "WIND_ONSHORE": "WIND_ONSHORE",
    "WIND_OFFSHORE": "WIND_OFFSHORE",
}

EUROPEAN_PROFILE_BUSES = {
    "AT": ("AT2 0",),
    "CH": ("CH2 0",),
    "FR": ("FR2 0", "FR5 0"),
    "GR": ("GR2 0",),
    "HR": ("HR2 0",),
    "IT": ("IT2 0", "IT4 0"),
    "ME": ("ME2 0",),
    "SI": ("SI2 0",),
}


def _load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _resolve(value: str | Path, *, pypsa_eur_root: Path | None = None) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if pypsa_eur_root is not None:
        candidate = pypsa_eur_root / path
        if candidate.exists():
            return candidate
    return ROOT / path


def canonical_snapshots() -> pd.DatetimeIndex:
    return pd.date_range(
        "2019-01-01 00:00:00+00:00",
        "2019-12-31 23:00:00+00:00",
        freq="h",
        name="snapshot",
    )


def _safe_id(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(value).upper()).strip("_")


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8", lineterminator="\n", na_rep="")


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(
        path,
        index=False,
        engine="pyarrow",
        compression="zstd",
        version="2.6",
    )


def _row_count(path: Path) -> int:
    if path.suffix.lower() == ".parquet":
        return len(pd.read_parquet(path))
    return len(pd.read_csv(path, dtype=str, keep_default_na=False))


def _normalise_shape(values: pd.Series | np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if len(array) != 8760:
        raise ValueError(f"Temporal shape has {len(array)} rows, expected 8760")
    if not np.isfinite(array).all() or (array < 0).any():
        raise ValueError("Temporal shape contains negative or non-finite values")
    total = float(array.sum(dtype=np.float64))
    if not total > 0:
        raise ValueError("Temporal shape has no positive mass")
    result = array / total
    result[int(np.argmax(result))] += 1.0 - float(result.sum(dtype=np.float64))
    return result


def _scale_shape_exact(shape: np.ndarray, annual_mwh: float) -> np.ndarray:
    values = np.asarray(shape, dtype=np.float64) * float(annual_mwh)
    values[int(np.argmax(values))] += float(annual_mwh) - float(
        values.sum(dtype=np.float64)
    )
    return values


def _read_load_archive(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index, utc=True)
    return frame.reindex(canonical_snapshots())


def _adjacent_calendar_map(
    source_values: pd.Series,
    source_calendar: pd.DatetimeIndex,
) -> np.ndarray:
    """Map an official adjacent-year series to 2019 by month/weekday/hour.

    Within each month-weekday-hour bucket, source observations and target
    observations are ordered by date. If a bucket has a different number of
    occurrences, evenly spaced source positions are selected. This preserves
    the source month, weekday and hour structure without renaming timestamps.
    """

    if len(source_values) != len(source_calendar):
        raise ValueError("Adjacent-year source values and calendar lengths differ")
    source = pd.DataFrame(
        {
            "value": np.asarray(source_values, dtype=np.float64),
            "month": source_calendar.month,
            "weekday": source_calendar.weekday,
            "hour": source_calendar.hour,
            "timestamp": source_calendar,
        }
    )
    target_index = canonical_snapshots()
    target = pd.DataFrame(
        {
            "position": np.arange(len(target_index), dtype=int),
            "month": target_index.month,
            "weekday": target_index.weekday,
            "hour": target_index.hour,
        }
    )
    mapped = np.full(len(target_index), np.nan, dtype=np.float64)
    keys = ["month", "weekday", "hour"]
    grouped_source = {key: part for key, part in source.groupby(keys, sort=True)}
    for key, target_part in target.groupby(keys, sort=True):
        source_part = grouped_source.get(key)
        if source_part is None or source_part.empty:
            raise ValueError(f"Adjacent-year calendar has no source bucket for {key}")
        source_array = source_part.sort_values("timestamp")["value"].to_numpy()
        count = len(target_part)
        if count == 1:
            positions = np.array([0], dtype=int)
        else:
            positions = np.rint(
                np.linspace(0, len(source_array) - 1, count)
            ).astype(int)
        mapped[target_part["position"].to_numpy()] = source_array[positions]
    if not np.isfinite(mapped).all():
        raise ValueError("Adjacent-year mapping left non-finite values")
    return mapped


def _read_tyndp_demand_shape(
    path: Path,
    sheet_name: str,
    header_row_zero_based: int,
    source_year: int,
) -> tuple[np.ndarray, pd.DatetimeIndex]:
    frame = pd.read_excel(
        path,
        sheet_name=sheet_name,
        header=header_row_zero_based,
        engine="openpyxl",
    )
    year_column = next(
        (column for column in frame.columns if str(column).replace(".0", "") == str(source_year)),
        None,
    )
    if year_column is None:
        raise ValueError(f"{path.name}/{sheet_name} has no {source_year} column")
    date_column = next(
        (column for column in frame.columns if str(column).strip().lower() == "date"),
        None,
    )
    if date_column is None:
        raise ValueError(f"{path.name}/{sheet_name} has no Date column")
    selected = frame[[date_column, year_column]].iloc[:8760].copy()
    calendar = pd.DatetimeIndex(pd.to_datetime(selected[date_column], errors="raise"))
    values = pd.to_numeric(selected[year_column], errors="coerce")
    if len(values) != 8760 or values.isna().any():
        raise ValueError(f"{path.name}/{sheet_name}/{source_year} is not a complete 8760-hour series")
    return values.to_numpy(dtype=np.float64), calendar


def _build_demand(
    cfg: dict[str, Any],
) -> tuple[pd.DataFrame, list[dict[str, Any]], pd.DataFrame]:
    paths = cfg["paths"]
    entsoe_path = _resolve(paths["entsoe_load"])
    opsd_path = _resolve(paths["opsd_load"])
    entsoe = _read_load_archive(entsoe_path)
    opsd = _read_load_archive(opsd_path)
    combined = entsoe.combine_first(opsd).replace(0, np.nan)

    source_shapes: dict[str, np.ndarray] = {}
    provenance: list[dict[str, Any]] = []
    compatibility_rows: list[dict[str, Any]] = []

    for market in ("IT", "FR", "CH", "AT", "SI", "HR", "ME", "GR"):
        if market not in combined:
            raise ValueError(f"No ENTSO-E/OPSD demand column for {market}")
        series = combined[market].copy()
        entsoe_missing = entsoe[market].isna()
        opsd_fill = entsoe_missing & opsd[market].notna()
        remaining_missing_timestamps = [
            timestamp.isoformat() for timestamp in series.index[series.isna()]
        ]
        missing_before = int(series.isna().sum())
        transformation = "ENTSOE_PRIMARY_WITH_OPSD_DIRECT_GAP_RECOVERY"
        classification = "MEM_NORMALIZED_OVERRIDE"
        if market == "FR" and missing_before:
            series = series.interpolate(method="linear", limit=6)
            transformation = "ENTSOE_PRIMARY_OPSD_SECONDARY_PYPSA_EUR_LINEAR_INTERPOLATION_LIMIT_6"
            classification = "EXPLICIT_ADAPTER"
        if series.isna().any():
            raise ValueError(
                f"Demand for {market} still has {int(series.isna().sum())} missing hours"
            )
        source_shapes[market] = _normalise_shape(series.to_numpy())
        provenance.append(
            {
                "profile_id": f"ETX7B3_DEMAND_SHAPE_{market}_2019",
                "profile_family": "DEMAND",
                "country_code": market,
                "source": "PyPSA-Eur electricity-demand ingestion path",
                "source_file_or_dataset": f"{entsoe_path}|{opsd_path}",
                "source_year": 2019,
                "weather_or_cutout_id": "NOT_APPLICABLE",
                "original_timezone": "UTC",
                "transformation": transformation,
                "normalization": "NORMALIZED_TO_UNIT_ANNUAL_SUM_THEN_SCALED_TO_FROZEN_MEM_MWH",
                "spatial_aggregation_method": "NATIONAL_MARKET_SERIES",
                "horizon_applicability": "2040|2050",
                "evidence_or_provenance_class": classification,
                "status": "COMPLETE",
            }
        )
        overlap = entsoe[market].notna() & opsd[market].notna() if market in entsoe and market in opsd else pd.Series(False, index=entsoe.index)
        compatibility_rows.append(
            {
                "country_code": market,
                "entsoe_populated_hours": int(entsoe[market].notna().sum()) if market in entsoe else 0,
                "opsd_populated_hours": int(opsd[market].notna().sum()) if market in opsd else 0,
                "overlap_hours": int(overlap.sum()),
                "overlap_correlation": (
                    float(entsoe.loc[overlap, market].corr(opsd.loc[overlap, market]))
                    if int(overlap.sum()) > 1
                    else ""
                ),
                "overlap_mean_absolute_difference_MW": (
                    float((entsoe.loc[overlap, market] - opsd.loc[overlap, market]).abs().mean())
                    if int(overlap.sum())
                    else ""
                ),
                "entsoe_missing_hours": int(entsoe_missing.sum()),
                "opsd_direct_fill_hours": int(opsd_fill.sum()),
                "opsd_direct_fill_timestamps_UTC": "|".join(
                    timestamp.isoformat() for timestamp in entsoe.index[opsd_fill]
                ),
                "opsd_direct_fill_values_MW": "|".join(
                    format(float(value), ".15g") for value in opsd.loc[opsd_fill, market]
                ),
                "timestamp_basis": "UTC_HOURLY",
                "unit_basis": "MW",
                "missing_before_adapter": missing_before,
                "remaining_missing_timestamps_before_adapter_UTC": "|".join(
                    remaining_missing_timestamps
                ),
                "adapter": transformation,
                "status": "PASS",
            }
        )

    mt_path = _resolve(paths["mt_demand"])
    mt_cfg = cfg["demand_adapters"]["malta"]
    mt_source_year = int(mt_cfg["source_column_year"])
    mt_values, mt_calendar = _read_tyndp_demand_shape(
        mt_path,
        str(mt_cfg["workbook_sheet"]),
        int(mt_cfg["header_row_zero_based"]),
        mt_source_year,
    )
    source_shapes["MT"] = _normalise_shape(_adjacent_calendar_map(mt_values, mt_calendar))
    provenance.append(
        {
            "profile_id": "ETX7B3_DEMAND_SHAPE_MT_2019",
            "profile_family": "DEMAND",
            "country_code": "MT",
            "source": "Official TYNDP 2024 National Trends 2040 demand profile workbook",
            "source_file_or_dataset": str(mt_path),
            "source_year": mt_source_year,
            "weather_or_cutout_id": "NOT_APPLICABLE",
            "original_timezone": "TYNDP_MARKET_MODEL_HOURLY_TEMPLATE",
            "transformation": f"MONTH_WEEKDAY_HOUR_CALENDAR_MAPPING_FROM_CLOSEST_AVAILABLE_OFFICIAL_TYNDP_CLIMATE_COLUMN_{mt_source_year}_ON_2018_TEMPLATE_TO_2019",
            "normalization": "NORMALIZED_TO_UNIT_ANNUAL_SUM_THEN_SCALED_TO_FROZEN_MEM_MWH",
            "spatial_aggregation_method": "MT00_MARKET_NODE",
            "horizon_applicability": "2040|2050",
            "evidence_or_provenance_class": "EXPLICIT_ADAPTER",
            "status": "COMPLETE",
        }
    )

    tn_path = _resolve(paths["tn_demand"])
    tn_values, tn_calendar = _read_tyndp_demand_shape(tn_path, "TN00", 7, 2018)
    source_shapes["TN"] = _normalise_shape(_adjacent_calendar_map(tn_values, tn_calendar))
    provenance.append(
        {
            "profile_id": "ETX7B3_DEMAND_SHAPE_TN_2019",
            "profile_family": "DEMAND",
            "country_code": "TN",
            "source": "Official TYNDP 2024 National Trends 2040 demand profile workbook",
            "source_file_or_dataset": str(tn_path),
            "source_year": 2018,
            "weather_or_cutout_id": "NOT_APPLICABLE",
            "original_timezone": "TYNDP_MARKET_MODEL_HOURLY_TEMPLATE",
            "transformation": "MONTH_WEEKDAY_HOUR_CALENDAR_MAPPING_FROM_OFFICIAL_2018_TO_2019",
            "normalization": "NORMALIZED_TO_UNIT_ANNUAL_SUM_THEN_SCALED_TO_FROZEN_MEM_MWH",
            "spatial_aggregation_method": "TN00_MARKET_NODE",
            "horizon_applicability": "2040|2050",
            "evidence_or_provenance_class": "EXPLICIT_ADAPTER",
            "status": "COMPLETE",
        }
    )

    external_controls = pd.read_csv(EXTERNAL_DEMAND)
    italy_controls = pd.read_csv(ITALY_DEMAND)
    controls = pd.concat(
        [
            external_controls[["country_code", "year", "annual_demand_MWh"]],
            italy_controls[["country_code", "year", "annual_demand_MWh"]],
        ],
        ignore_index=True,
    )
    controls["year"] = controls["year"].astype(int)

    italy_zonal = pd.read_csv(ITALY_ZONAL_DEMAND)
    italy_zonal = italy_zonal.loc[
        italy_zonal["scenario"].eq(SCENARIO)
        & italy_zonal["year"].astype(int).isin(HORIZONS)
    ]
    if italy_zonal.groupby("year")["zone"].nunique().to_dict() != {2040: 7, 2050: 7}:
        raise ValueError("Italy demand fallback does not trace to seven frozen zones per horizon")

    snapshots = canonical_snapshots()
    rows: list[pd.DataFrame] = []
    for record in controls.sort_values(["year", "country_code"]).to_dict(orient="records"):
        market = str(record["country_code"])
        year = int(record["year"])
        annual_mwh = float(record["annual_demand_MWh"])
        shape = source_shapes[market]
        scaled = _scale_shape_exact(shape, annual_mwh)
        if market == "IT":
            zonal = italy_zonal.loc[italy_zonal["year"].astype(int).eq(year)]
            zonal_total = 0.0
            for zonal_record in zonal.to_dict(orient="records"):
                zonal_scaled = _scale_shape_exact(
                    shape, float(zonal_record["annual_zonal_demand_MWh"])
                )
                zonal_total += zonal_scaled
            scaled = np.asarray(zonal_total, dtype=np.float64)
            scaled[int(np.argmax(scaled))] += annual_mwh - float(scaled.sum())
        rows.append(
            pd.DataFrame(
                {
                    "snapshot": snapshots,
                    "horizon": year,
                    "scenario": SCENARIO,
                    "country_code": market,
                    "load_MW": scaled,
                    "normalized_shape_value": shape,
                    "profile_id": f"ETX7B3_DEMAND_SHAPE_{market}_2019",
                }
            )
        )
    output = pd.concat(rows, ignore_index=True).sort_values(
        ["horizon", "country_code", "snapshot"], kind="stable"
    )
    return output.reset_index(drop=True), provenance, pd.DataFrame(compatibility_rows)


def _cell_area_sqkm(cutout: Any) -> Any:
    import xarray as xr

    area = cutout.grid.to_crs(3035).area / 1e6
    return xr.DataArray(
        area.values.reshape(cutout.shape),
        coords=[cutout.coords["y"], cutout.coords["x"]],
        dims=["y", "x"],
    )


def _atlite_resource(cutout: Any, family: str, *, aggregate_time: str | None) -> Any:
    if family == "SOLAR_PV":
        return cutout.pv(
            panel="CSi",
            orientation={"slope": 35.0, "azimuth": 180.0},
            aggregate_time=aggregate_time,
            show_progress=False,
            dask_kwargs=DASK_KWARGS,
        )
    if family == "WIND_ONSHORE":
        return cutout.wind(
            turbine="Vestas_V112_3MW",
            smooth=False,
            add_cutout_windspeed=True,
            aggregate_time=aggregate_time,
            show_progress=False,
            dask_kwargs=DASK_KWARGS,
        )
    if family == "WIND_OFFSHORE":
        return cutout.wind(
            turbine="NREL_ReferenceTurbine_2020ATB_5.5MW",
            smooth=False,
            add_cutout_windspeed=True,
            aggregate_time=aggregate_time,
            show_progress=False,
            dask_kwargs=DASK_KWARGS,
        )
    raise ValueError(f"Unsupported AtLite family {family}")


def _compute_profile_from_availability(
    cutout: Any,
    availability: Any,
    family: str,
    capacity_per_sqkm: float,
    correction_factor: float,
    clip_below: float,
) -> tuple[pd.DataFrame, pd.Series]:
    import xarray as xr

    area = _cell_area_sqkm(cutout)
    capacity_factor = correction_factor * _atlite_resource(
        cutout, family, aggregate_time="mean"
    )
    layout = capacity_factor * area * capacity_per_sqkm
    potential = capacity_per_sqkm * availability @ area
    positive = potential.to_pandas() > 0
    availability = availability.sel(bus=positive.index[positive])
    potential = potential.sel(bus=positive.index[positive])
    matrix = availability.stack(spatial=["y", "x"])
    if family == "SOLAR_PV":
        profile = cutout.pv(
            panel="CSi",
            orientation={"slope": 35.0, "azimuth": 180.0},
            matrix=matrix,
            layout=layout,
            index=matrix.indexes["bus"],
            per_unit=True,
            return_capacity=False,
            aggregate_time=None,
            show_progress=False,
            dask_kwargs=DASK_KWARGS,
        )
    elif family == "WIND_ONSHORE":
        profile = cutout.wind(
            turbine="Vestas_V112_3MW",
            smooth=False,
            add_cutout_windspeed=True,
            matrix=matrix,
            layout=layout,
            index=matrix.indexes["bus"],
            per_unit=True,
            return_capacity=False,
            aggregate_time=None,
            show_progress=False,
            dask_kwargs=DASK_KWARGS,
        )
    else:
        profile = cutout.wind(
            turbine="NREL_ReferenceTurbine_2020ATB_5.5MW",
            smooth=False,
            add_cutout_windspeed=True,
            matrix=matrix,
            layout=layout,
            index=matrix.indexes["bus"],
            per_unit=True,
            return_capacity=False,
            aggregate_time=None,
            show_progress=False,
            dask_kwargs=DASK_KWARGS,
        )
    profile = correction_factor * profile
    if "time" not in profile.dims or "bus" not in profile.dims:
        raise ValueError(f"Unexpected AtLite output dimensions: {profile.dims}")
    profile = profile.transpose("time", "bus")
    profile = profile.where(profile >= clip_below, 0)
    frame = profile.to_pandas()
    frame.index = canonical_snapshots()
    if not np.isfinite(frame.to_numpy()).all():
        raise ValueError(f"{family} AtLite profiles contain non-finite values")
    return frame, potential.to_pandas()


def _build_or_copy_weather_caches(
    cfg: dict[str, Any],
    cache_dir: Path,
    source_cache_dir: Path | None,
) -> dict[str, pd.DataFrame]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    if source_cache_dir is not None:
        frames: dict[str, pd.DataFrame] = {}
        for key, filename in CACHE_FILES.items():
            source = source_cache_dir / filename
            if not source.exists():
                raise FileNotFoundError(f"Missing reusable ETX-7B3 cache {source}")
            target = cache_dir / filename
            if source.resolve() != target.resolve():
                shutil.copy2(source, target)
            frames[key] = pd.read_parquet(target)
        return frames

    import atlite
    import xarray as xr

    paths = cfg["paths"]
    upstream = Path(paths["pypsa_eur_root"])
    cutout = atlite.Cutout(str(_resolve(paths["cutout"])))
    x1, y1, x2, y2 = [float(value) for value in cfg["vre"]["processing_bounds_lon_lat"]]
    cutout.data = cutout.data.sel(x=slice(x1, x2), y=slice(y1, y2))
    spatial = cfg["pypsa_eur_spatial_assets"]
    resource_cfg = cfg["vre"]
    allowed_buses = sorted({bus for values in EUROPEAN_PROFILE_BUSES.values() for bus in values})

    bus_cache_parts: list[pd.DataFrame] = []
    matrix_by_family = {
        "SOLAR_PV": spatial["availability_solar"],
        "WIND_ONSHORE": spatial["availability_onwind"],
        "WIND_OFFSHORE": spatial["availability_offwind"],
    }
    config_by_family = {
        "SOLAR_PV": resource_cfg["solar"],
        "WIND_ONSHORE": resource_cfg["onwind"],
        "WIND_OFFSHORE": resource_cfg["offwind"],
    }
    for family, relative_path in matrix_by_family.items():
        availability = xr.open_dataarray(_resolve(relative_path, pypsa_eur_root=upstream))
        availability = availability.sel(bus=[bus for bus in allowed_buses if bus in availability.bus])
        availability = availability.sel(x=cutout.coords["x"], y=cutout.coords["y"])
        resource = config_by_family[family]
        profiles, potential = _compute_profile_from_availability(
            cutout,
            availability,
            family,
            float(resource["capacity_per_sqkm"]),
            float(resource["correction_factor"]),
            float(resource["clip_p_max_pu_below"]),
        )
        long = profiles.rename_axis("snapshot").reset_index().melt(
            id_vars="snapshot", var_name="upstream_bus", value_name="p_max_pu"
        )
        long["profile_family"] = family
        long["p_nom_potential_MW"] = long["upstream_bus"].map(potential)
        bus_cache_parts.append(long)
        availability.close()
    bus_cache = pd.concat(bus_cache_parts, ignore_index=True).sort_values(
        ["profile_family", "upstream_bus", "snapshot"], kind="stable"
    )
    _write_parquet(bus_cache, cache_dir / CACHE_FILES["vre_bus"])

    mt_tn_offwind = _compute_mt_tn_offwind(cfg, cutout)
    _write_parquet(mt_tn_offwind, cache_dir / CACHE_FILES["vre_mt_tn_offwind"])

    runoff = _compute_core_runoff(cfg, cutout)
    _write_parquet(runoff, cache_dir / CACHE_FILES["runoff"])
    return {
        "vre_bus": bus_cache,
        "vre_mt_tn_offwind": mt_tn_offwind,
        "runoff": runoff,
    }


def _read_eez_geometries(cfg: dict[str, Any]) -> Any:
    import geopandas as gpd

    upstream = Path(cfg["paths"]["pypsa_eur_root"])
    archive = _resolve(
        cfg["pypsa_eur_spatial_assets"]["eez_archive"], pypsa_eur_root=upstream
    )
    with tempfile.TemporaryDirectory(prefix="mem_etx7b3_eez_") as temp_dir:
        with zipfile.ZipFile(archive) as package:
            member = next(name for name in package.namelist() if name.lower().endswith(".gpkg"))
            package.extract(member, temp_dir)
        frame = gpd.read_file(Path(temp_dir) / member)
    iso_column = next(
        column for column in frame.columns if str(column).upper() == "ISO_TER1"
    )
    mapping = {"MT": "MLT", "TN": "TUN"}
    records = {}
    for market, iso3 in mapping.items():
        selected = frame.loc[frame[iso_column].eq(iso3)]
        if selected.empty:
            raise ValueError(f"EEZ archive has no geometry for {market}/{iso3}")
        records[market] = selected.geometry.union_all()
    return gpd.GeoSeries(records, crs=frame.crs).to_crs(4326)


def _compute_mt_tn_offwind(cfg: dict[str, Any], cutout: Any) -> pd.DataFrame:
    import numpy as np
    from atlite.gis import ExclusionContainer

    geometries = _read_eez_geometries(cfg)
    availability = cutout.availabilitymatrix(
        geometries,
        ExclusionContainer(),
        nprocesses=1,
        disable_progressbar=True,
    )
    shape_dim = next(dim for dim in availability.dims if dim not in {"x", "y"})
    availability = np.ceil(availability).rename({shape_dim: "bus"})
    availability = availability.assign_coords(bus=geometries.index)
    resource = cfg["vre"]["offwind"]
    profiles, potential = _compute_profile_from_availability(
        cutout,
        availability,
        "WIND_OFFSHORE",
        float(resource["capacity_per_sqkm"]),
        float(resource["correction_factor"]),
        float(resource["clip_p_max_pu_below"]),
    )
    long = profiles.rename_axis("snapshot").reset_index().melt(
        id_vars="snapshot", var_name="country_code", value_name="p_max_pu"
    )
    long["profile_family"] = "WIND_OFFSHORE"
    long["p_nom_potential_MW"] = long["country_code"].map(potential)
    return long.sort_values(["country_code", "snapshot"], kind="stable").reset_index(drop=True)


def _read_tunisia_geometry(cfg: dict[str, Any]) -> Any:
    import geopandas as gpd

    path = _resolve(cfg["paths"]["naturalearth_lowres"])
    frame = gpd.read_file(path)
    iso_column = next(
        column for column in frame.columns if str(column).lower() in {"iso_a3", "iso_a3_eh"}
    )
    selected = frame.loc[frame[iso_column].eq("TUN")]
    if selected.empty:
        name_column = next(column for column in frame.columns if str(column).lower() in {"name", "name_en"})
        selected = frame.loc[frame[name_column].astype(str).str.casefold().eq("tunisia")]
    if selected.empty:
        raise ValueError("Natural Earth source has no Tunisia geometry")
    return selected.geometry.union_all(), frame.crs


def _compute_core_runoff(cfg: dict[str, Any], cutout: Any) -> pd.DataFrame:
    import geopandas as gpd

    upstream = Path(cfg["paths"]["pypsa_eur_root"])
    country_shapes = gpd.read_file(
        _resolve(
            cfg["pypsa_eur_spatial_assets"]["country_shapes"],
            pypsa_eur_root=upstream,
        )
    ).set_index("name")
    regions = gpd.read_file(
        _resolve(
            cfg["pypsa_eur_spatial_assets"]["onshore_regions"],
            pypsa_eur_root=upstream,
        )
    ).set_index("name")
    records: dict[str, Any] = {}
    for market in ("AT", "CH", "FR", "GR", "HR", "ME", "SI"):
        records[market] = country_shapes.loc[market, "geometry"]
    records["IT_MAINLAND_CLUSTER"] = regions.loc["IT2 0", "geometry"]
    records["IT_SARDINIA_CLUSTER"] = regions.loc["IT4 0", "geometry"]
    tn_geometry, tn_crs = _read_tunisia_geometry(cfg)
    if tn_crs != country_shapes.crs:
        tn_geometry = gpd.GeoSeries([tn_geometry], crs=tn_crs).to_crs(country_shapes.crs).iloc[0]
    records["TN_CUTOUT_INTERSECTION"] = tn_geometry
    geometries = gpd.GeoSeries(records, crs=country_shapes.crs).to_crs(4326)
    runoff = cutout.runoff(
        shapes=geometries,
        smooth=True,
        lower_threshold_quantile=True,
        aggregate_time=None,
        show_progress=False,
        dask_kwargs=DASK_KWARGS,
    )
    shape_dim = next(dim for dim in runoff.dims if dim != "time")
    runoff = runoff.rename({shape_dim: "source_region"}).assign_coords(
        source_region=geometries.index
    )
    frame = runoff.transpose("time", "source_region").to_pandas()
    frame.index = canonical_snapshots()
    long = frame.rename_axis("snapshot").reset_index().melt(
        id_vars="snapshot", var_name="source_region", value_name="raw_runoff_value"
    )
    if not np.isfinite(long["raw_runoff_value"]).all() or (long["raw_runoff_value"] < 0).any():
        raise ValueError("AtLite runoff output contains negative or non-finite values")
    return long.sort_values(["source_region", "snapshot"], kind="stable").reset_index(drop=True)


def _weighted_bus_profile(
    bus_cache: pd.DataFrame,
    family: str,
    buses: Iterable[str],
    explicit_weights: dict[str, float] | None = None,
) -> np.ndarray:
    selected = bus_cache.loc[
        bus_cache["profile_family"].eq(family)
        & bus_cache["upstream_bus"].isin(list(buses))
    ].copy()
    if selected.empty:
        raise ValueError(f"No cached {family} profile for buses {list(buses)}")
    pivot = selected.pivot(index="snapshot", columns="upstream_bus", values="p_max_pu")
    if explicit_weights is None:
        potentials = selected.groupby("upstream_bus")["p_nom_potential_MW"].first()
        weights = potentials / potentials.sum()
    else:
        weights = pd.Series(explicit_weights, dtype=float)
        weights = weights.loc[weights.index.intersection(pivot.columns)]
        weights = weights / weights.sum()
    missing = sorted(set(weights.index) - set(pivot.columns))
    if missing:
        raise ValueError(f"Missing weighted profile buses: {missing}")
    result = pivot.loc[:, weights.index].mul(weights, axis=1).sum(axis=1)
    return result.reindex(canonical_snapshots()).to_numpy(dtype=np.float64)


def _italy_vre_weights(year: int, parent: str) -> dict[str, float]:
    source = pd.read_csv(ITALY_ZONAL_GENERATORS)
    selected = source.loc[
        source["scenario"].eq(SCENARIO)
        & source["year"].astype(int).eq(year)
        & source["parent_capacity_technology"].eq(parent)
    ].copy()
    if selected.empty:
        raise ValueError(f"No frozen Italy zonal capacity for {year}/{parent}")
    selected["macro_bus"] = np.where(selected["zone"].eq("SARD"), "IT4 0", "IT2 0")
    weights = selected.groupby("macro_bus")["p_nom_MW"].sum().astype(float)
    return weights.to_dict()


def _read_pecd_profile(path: Path, source_year: int) -> np.ndarray:
    frame = pd.read_csv(path, skiprows=10)
    year_column = next(
        (column for column in frame.columns if str(column).replace(".0", "") == str(source_year)),
        None,
    )
    if year_column is None:
        raise ValueError(f"{path.name} has no {source_year} climate column")
    values = pd.to_numeric(frame[year_column], errors="coerce").iloc[:8760]
    if len(values) != 8760 or values.isna().any():
        raise ValueError(f"{path.name}/{source_year} is not a complete 8760-hour series")
    result = values.to_numpy(dtype=np.float64)
    if not np.isfinite(result).all() or (result < -1e-12).any() or (result > 1 + 1e-12).any():
        raise ValueError(f"{path.name}/{source_year} contains out-of-bound availability")
    return np.clip(result, 0.0, 1.0)


def _profile_id(country: str, profile_class: str, suffix: str = "2019") -> str:
    return f"ETX7B3_{_safe_id(country)}_{_safe_id(profile_class)}_{suffix}"


def _build_vre(
    cfg: dict[str, Any],
    caches: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, dict[tuple[str, int, str], str], list[dict[str, Any]]]:
    external = pd.read_csv(EXTERNAL_GENERATORS)
    italy = pd.read_csv(ITALY_GENERATORS)
    bus_cache = caches["vre_bus"]
    offwind_cache = caches["vre_mt_tn_offwind"]
    snapshots = canonical_snapshots()
    rows: list[pd.DataFrame] = []
    profile_map: dict[tuple[str, int, str], str] = {}
    provenance: list[dict[str, Any]] = []
    emitted: dict[str, np.ndarray] = {}

    def register(
        *,
        country: str,
        year: int,
        profile_class: str,
        values: np.ndarray,
        source: str,
        source_file: str,
        source_year: int,
        classification: str,
        transformation: str,
        spatial_method: str,
        preferred_id: str,
    ) -> None:
        array = np.asarray(values, dtype=np.float64)
        if len(array) != 8760 or not np.isfinite(array).all():
            raise ValueError(f"Invalid VRE array for {country}/{year}/{profile_class}")
        if (array < -1e-10).any() or (array > 1 + 1e-10).any():
            raise ValueError(f"Out-of-bound VRE array for {country}/{year}/{profile_class}")
        array = np.clip(array, 0.0, 1.0)
        matching = next(
            (
                profile_id
                for profile_id, existing in emitted.items()
                if profile_id.startswith(f"ETX7B3_{_safe_id(country)}_{_safe_id(profile_class)}")
                and np.array_equal(existing, array)
            ),
            None,
        )
        profile_id = matching or preferred_id
        if matching is None:
            emitted[profile_id] = array
            rows.append(
                pd.DataFrame(
                    {
                        "snapshot": snapshots,
                        "country_code": country,
                        "stage_a_static_or_profile_class": profile_class,
                        "p_max_pu": array,
                        "profile_id": profile_id,
                    }
                )
            )
            provenance.append(
                {
                    "profile_id": profile_id,
                    "profile_family": profile_class,
                    "country_code": country,
                    "source": source,
                    "source_file_or_dataset": source_file,
                    "source_year": source_year,
                    "weather_or_cutout_id": "PYPSA_CUTOUT_V1_EUROPE_2019_SARAH3_ERA5" if "AtLite" in source or "PyPSA-Eur" in source else "TYNDP_PECD",
                    "original_timezone": "UTC_OR_SOURCE_CLIMATE_HOUR_SEQUENCE",
                    "transformation": transformation,
                    "normalization": "PER_UNIT_AVAILABILITY_ATTACHED_TO_FROZEN_MEM_CAPACITY_LATER",
                    "spatial_aggregation_method": spatial_method,
                    "horizon_applicability": str(year),
                    "evidence_or_provenance_class": classification,
                    "status": "COMPLETE",
                }
            )
        else:
            existing_prov = next(record for record in provenance if record["profile_id"] == profile_id)
            years = set(str(existing_prov["horizon_applicability"]).split("|")) | {str(year)}
            existing_prov["horizon_applicability"] = "|".join(sorted(years))
        profile_map[(country, year, profile_class)] = profile_id

    for record in external.to_dict(orient="records"):
        group = str(record["harmonised_group"])
        if group not in EXTERNAL_VRE_GROUPS:
            continue
        country = str(record["country_code"])
        year = int(record["year"])
        profile_class = EXTERNAL_VRE_GROUPS[group]
        key = (country, year, profile_class)
        if key in profile_map:
            continue
        if country in EUROPEAN_PROFILE_BUSES:
            values = _weighted_bus_profile(
                bus_cache,
                profile_class,
                EUROPEAN_PROFILE_BUSES[country],
            )
            register(
                country=country,
                year=year,
                profile_class=profile_class,
                values=values,
                source="PyPSA-Eur/AtLite 2019 renewable-profile workflow",
                source_file=str(_resolve(cfg["paths"]["cutout"])),
                source_year=2019,
                classification="PYPSA_EUR_SPATIAL_WEIGHTING_ONLY",
                transformation="STANDARD_PYPSA_EUR_RESOURCE_POTENTIAL_WEIGHTED_MARKET_AGGREGATION",
                spatial_method="PYPSA_EUR_AVAILABILITY_MATRIX_AND_RESOURCE_POTENTIAL",
                preferred_id=_profile_id(country, profile_class),
            )
        elif country == "MT" and profile_class == "SOLAR_PV":
            path = _resolve(cfg["paths"]["mt_solar"])
            register(
                country=country,
                year=year,
                profile_class=profile_class,
                values=_read_pecd_profile(path, 2019),
                source="Official TYNDP 2024 PECD hourly profile",
                source_file=str(path),
                source_year=2019,
                classification="NATIVE_PYPSA_EUR",
                transformation="DIRECT_2019_PECD_COLUMN_TO_CANONICAL_UTC_SEQUENCE",
                spatial_method="MT00_MARKET_NODE",
                preferred_id=_profile_id(country, profile_class),
            )
        elif country == "TN" and profile_class in {"SOLAR_PV", "WIND_ONSHORE", "CSP"}:
            path_key = {"SOLAR_PV": "tn_solar", "WIND_ONSHORE": "tn_onwind", "CSP": "tn_csp"}[profile_class]
            source_year = 2019 if profile_class == "CSP" else 2018
            path = _resolve(cfg["paths"][path_key])
            register(
                country=country,
                year=year,
                profile_class=profile_class,
                values=_read_pecd_profile(path, source_year),
                source="Official TYNDP 2024 PECD hourly profile",
                source_file=str(path),
                source_year=source_year,
                classification="NATIVE_PYPSA_EUR" if source_year == 2019 else "EXPLICIT_ADAPTER",
                transformation=(
                    "DIRECT_2019_PECD_COLUMN_TO_CANONICAL_UTC_SEQUENCE"
                    if source_year == 2019
                    else "OFFICIAL_2018_NON_LEAP_CLIMATE_SEQUENCE_MAPPED_MONTH_DAY_HOUR_TO_2019"
                ),
                spatial_method="TN00_MARKET_NODE",
                preferred_id=_profile_id(country, profile_class),
            )
        elif country in {"MT", "TN"} and profile_class == "WIND_OFFSHORE":
            selected = offwind_cache.loc[offwind_cache["country_code"].eq(country)].sort_values("snapshot")
            register(
                country=country,
                year=year,
                profile_class=profile_class,
                values=selected["p_max_pu"].to_numpy(),
                source="PyPSA-Eur/AtLite 2019 offshore-wind conversion with official EEZ geometry",
                source_file=f"{_resolve(cfg['paths']['cutout'])}|{cfg['pypsa_eur_spatial_assets']['eez_archive']}",
                source_year=2019,
                classification="EXPLICIT_ADAPTER",
                transformation="STANDARD_OFFWIND_RESOURCE_CONVERSION_WITH_NO_CAPACITY_OVERRIDE",
                spatial_method="EEZ_AVAILABILITY_SPATIAL_WEIGHTING_ONLY_NO_ABSOLUTE_CAPACITY_TRANSFER",
                preferred_id=_profile_id(country, profile_class),
            )
        else:
            raise ValueError(f"No VRE route for {country}/{year}/{profile_class}")

    for record in italy.to_dict(orient="records"):
        parent = str(record["parent_capacity_technology"])
        if parent not in ITALY_VRE_GROUPS:
            continue
        year = int(record["year"])
        profile_class = ITALY_VRE_GROUPS[parent]
        key = ("IT", year, profile_class)
        if key in profile_map:
            continue
        physical_family = "SOLAR_PV" if profile_class.startswith("SOLAR_PV") else profile_class
        values = _weighted_bus_profile(
            bus_cache,
            physical_family,
            EUROPEAN_PROFILE_BUSES["IT"],
            explicit_weights=_italy_vre_weights(year, parent),
        )
        register(
            country="IT",
            year=year,
            profile_class=profile_class,
            values=values,
            source="PyPSA-Eur/AtLite 2019 renewable-profile workflow",
            source_file=str(_resolve(cfg["paths"]["cutout"])),
            source_year=2019,
            classification="PYPSA_EUR_SPATIAL_WEIGHTING_ONLY",
            transformation="FROZEN_STAGE_B_SARDINIA_VERSUS_REST_CAPACITY_WEIGHTED_NATIONAL_PROFILE",
            spatial_method="IT4_SARDINIA_AND_IT2_REMAINDER_WEIGHTED_BY_FROZEN_STAGE_B_CAPACITY",
            preferred_id=_profile_id("IT", profile_class, str(year)),
        )

    output = pd.concat(rows, ignore_index=True).sort_values(
        ["country_code", "stage_a_static_or_profile_class", "profile_id", "snapshot"],
        kind="stable",
    )
    return output.reset_index(drop=True), profile_map, provenance


def _runoff_series(runoff_cache: pd.DataFrame, source_region: str) -> np.ndarray:
    selected = runoff_cache.loc[runoff_cache["source_region"].eq(source_region)].sort_values("snapshot")
    if len(selected) != 8760:
        raise ValueError(f"Runoff cache for {source_region} has {len(selected)} hours")
    return selected["raw_runoff_value"].to_numpy(dtype=np.float64)


def _italy_hydro_weights(hydro_class: str) -> dict[str, float]:
    source = pd.read_csv(ITALY_HYDRO_MAPPING)
    selected = source.loc[source["hydro_class"].eq(hydro_class)].copy()
    selected = selected.loc[selected["p_nom_MW_NET"].astype(float) > 0]
    selected["source_region"] = np.where(
        selected["zone"].eq("SARD"), "IT_SARDINIA_CLUSTER", "IT_MAINLAND_CLUSTER"
    )
    return selected.groupby("source_region")["p_nom_MW_NET"].sum().astype(float).to_dict()


def _build_hydro(
    caches: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, dict[tuple[str, int, str], str], list[dict[str, Any]]]:
    external = pd.read_csv(EXTERNAL_GENERATORS)
    runoff_cache = caches["runoff"]
    snapshots = canonical_snapshots()
    rows: list[pd.DataFrame] = []
    mapping: dict[tuple[str, int, str], str] = {}
    provenance: list[dict[str, Any]] = []
    emitted: set[str] = set()

    def register(
        country: str,
        year: int,
        hydro_class: str,
        values: np.ndarray,
        classification: str,
        source_region: str,
        spatial_method: str,
    ) -> None:
        profile_id = _profile_id(country, hydro_class)
        if profile_id not in emitted:
            shape = _normalise_shape(values)
            rows.append(
                pd.DataFrame(
                    {
                        "snapshot": snapshots,
                        "country_code": country,
                        "hydro_class": hydro_class,
                        "temporal_quantity_type": "NORMALIZED_NATURAL_INFLOW_SHARE",
                        "value": shape,
                        "unit": "per_unit_annual_energy_share",
                        "profile_id": profile_id,
                    }
                )
            )
            provenance.append(
                {
                    "profile_id": profile_id,
                    "profile_family": hydro_class,
                    "country_code": country,
                    "source": "PyPSA-Eur standard AtLite runoff workflow",
                    "source_file_or_dataset": "runtime_sources/weather/PyPSA_cutout_v1.0/europe-2019-sarah3-era5.nc",
                    "source_year": 2019,
                    "weather_or_cutout_id": "PYPSA_CUTOUT_V1_EUROPE_2019_SARAH3_ERA5",
                    "original_timezone": "UTC",
                    "transformation": "SMOOTH_AND_LOWER_THRESHOLD_QUANTILE_THEN_NORMALIZE_TO_UNIT_ANNUAL_INFLOW_SHAPE",
                    "normalization": "ANNUAL_ENERGY_AND_WATER_STATE_MAPPING_DEFERRED_ETX7B4",
                    "spatial_aggregation_method": spatial_method,
                    "horizon_applicability": "2040|2050",
                    "evidence_or_provenance_class": classification,
                    "status": "COMPLETE",
                }
            )
            emitted.add(profile_id)
        mapping[(country, year, hydro_class)] = profile_id

    hydro_rows = external.loc[external["harmonised_group"].eq("hydro_non_phs")]
    for record in hydro_rows.to_dict(orient="records"):
        country = str(record["country_code"])
        year = int(record["year"])
        source_group = str(record["source_group"])
        if source_group == "ror_hydro":
            hydro_class = "HYDRO_RUN_OF_RIVER"
        elif source_group == "reservoir_hydro":
            hydro_class = "HYDRO_RESERVOIR"
        elif source_group == "small_hydro":
            hydro_class = "HYDRO_SMALL_UNSPLIT"
        else:
            hydro_class = "HYDRO_NON_PHS_UNSPLIT"
        key = (country, year, hydro_class)
        if key in mapping:
            continue
        source_region = "TN_CUTOUT_INTERSECTION" if country == "TN" else country
        register(
            country,
            year,
            hydro_class,
            _runoff_series(runoff_cache, source_region),
            "EXPLICIT_ADAPTER" if country == "TN" else "NATIVE_PYPSA_EUR",
            source_region,
            (
                "TUNISIA_LAND_GEOMETRY_INTERSECTION_WITH_VALIDATED_2019_CUTOUT"
                if country == "TN"
                else "PYPSA_EUR_COUNTRY_SHAPE"
            ),
        )

    italy_classes = {
        "RUN_OF_RIVER": "HYDRO_RUN_OF_RIVER",
        "BASIN_PONDAGE": "HYDRO_BASIN_PONDAGE",
        "RESERVOIR": "HYDRO_RESERVOIR",
    }
    for source_class, output_class in italy_classes.items():
        weights = pd.Series(_italy_hydro_weights(source_class), dtype=float)
        weights = weights / weights.sum()
        values = sum(
            _runoff_series(runoff_cache, region) * weight
            for region, weight in weights.items()
        )
        for year in HORIZONS:
            register(
                "IT",
                year,
                output_class,
                np.asarray(values),
                "PYPSA_EUR_SPATIAL_WEIGHTING_ONLY",
                "IT_MAINLAND_CLUSTER|IT_SARDINIA_CLUSTER",
                "FROZEN_STAGE_B_HYDRO_CLASS_CAPACITY_WEIGHTED_IT2_IT4_RUNOFF",
            )

    output = pd.concat(rows, ignore_index=True).sort_values(
        ["country_code", "hydro_class", "profile_id", "snapshot"], kind="stable"
    )
    return output.reset_index(drop=True), mapping, provenance


def _required_profiles() -> dict[tuple[str, int, str], bool]:
    result: dict[tuple[str, int, str], bool] = {
        (market, year, family): family == "DEMAND"
        for market in CORE_MARKETS
        for year in HORIZONS
        for family in PROFILE_FAMILIES
    }
    external = pd.read_csv(EXTERNAL_GENERATORS)
    for record in external.to_dict(orient="records"):
        country = str(record["country_code"])
        year = int(record["year"])
        group = str(record["harmonised_group"])
        if group in EXTERNAL_VRE_GROUPS:
            result[(country, year, EXTERNAL_VRE_GROUPS[group])] = True
        if group == "hydro_non_phs":
            source_group = str(record["source_group"])
            hydro_class = {
                "ror_hydro": "HYDRO_RUN_OF_RIVER",
                "reservoir_hydro": "HYDRO_RESERVOIR",
                "small_hydro": "HYDRO_SMALL_UNSPLIT",
            }.get(source_group, "HYDRO_NON_PHS_UNSPLIT")
            result[(country, year, hydro_class)] = True
    italy = pd.read_csv(ITALY_GENERATORS)
    for record in italy.to_dict(orient="records"):
        year = int(record["year"])
        parent = str(record["parent_capacity_technology"])
        if parent in ITALY_VRE_GROUPS:
            result[("IT", year, ITALY_VRE_GROUPS[parent])] = True
        if parent == "HYDRO_RUN_OF_RIVER":
            result[("IT", year, "HYDRO_RUN_OF_RIVER")] = True
        if parent == "HYDRO_BASIN_PONDAGE":
            result[("IT", year, "HYDRO_BASIN_PONDAGE")] = True
        if parent == "HYDRO_RESERVOIR":
            result[("IT", year, "HYDRO_RESERVOIR")] = True
    return result


def _coverage_registry(
    demand_provenance: list[dict[str, Any]],
    vre_map: dict[tuple[str, int, str], str],
    vre_provenance: list[dict[str, Any]],
    hydro_map: dict[tuple[str, int, str], str],
    hydro_provenance: list[dict[str, Any]],
) -> pd.DataFrame:
    requirements = _required_profiles()
    provenance = {record["profile_id"]: record for record in demand_provenance + vre_provenance + hydro_provenance}
    rows: list[dict[str, Any]] = []
    all_families = sorted(set(family for _, _, family in requirements))
    for market in CORE_MARKETS:
        for year in HORIZONS:
            for family in all_families:
                required = requirements.get((market, year, family), False)
                if family == "DEMAND":
                    profile_id = f"ETX7B3_DEMAND_SHAPE_{market}_2019"
                else:
                    profile_id = vre_map.get((market, year, family), hydro_map.get((market, year, family), ""))
                record = provenance.get(profile_id, {})
                status = "COMPLETE" if required and profile_id else ("NOT_REQUIRED" if not required else "BLOCKED")
                rows.append(
                    {
                        "country_code": market,
                        "market_role": "CORE_STAGE_A_MARKET",
                        "horizon": year,
                        "scenario": SCENARIO,
                        "profile_family": family,
                        "required": required,
                        "source_or_method": record.get("source", "NOT_REQUIRED"),
                        "source_year": record.get("source_year", ""),
                        "classification": record.get("evidence_or_provenance_class", "NOT_REQUIRED"),
                        "profile_id": profile_id,
                        "status": status,
                        "notes": (
                            record.get("transformation", "")
                            if status == "COMPLETE"
                            else "No positive frozen capacity requiring this family"
                        ),
                    }
                )
            for family in DEFERRED_FAMILIES:
                rows.append(
                    {
                        "country_code": market,
                        "market_role": "CORE_STAGE_A_MARKET",
                        "horizon": year,
                        "scenario": SCENARIO,
                        "profile_family": family,
                        "required": "DEFERRED_RUNTIME_REQUIREMENT",
                        "source_or_method": "ETX7B4_OPERATING_PARAMETER_LAYER",
                        "source_year": "",
                        "classification": "DEFER_ETX7B4",
                        "profile_id": "",
                        "status": "DEFER_ETX7B4",
                        "notes": "Explicitly outside ETX-7B3 temporal scope",
                    }
                )
    return pd.DataFrame(rows).sort_values(
        ["country_code", "horizon", "profile_family"], kind="stable"
    ).reset_index(drop=True)


def _source_receipt(cfg: dict[str, Any]) -> pd.DataFrame:
    upstream = Path(cfg["paths"]["pypsa_eur_root"])
    source_paths: list[tuple[str, str, Path, str]] = [
        ("ETX7B3_CONFIG", "TRANSFORMATION_CONFIG", DEFAULT_CONFIG, "CONFIG_AUTHORITY"),
        ("ETX7B3_TRANSFORM_CODE", "TRANSFORMATION_CODE", Path(__file__).resolve(), "DETERMINISTIC_IMPLEMENTATION"),
        ("ETX7B3_HYDRO_SOURCE_AUDIT_CONFIG", "HYDRO_SOURCE_METHOD_AUDIT", ROOT / "config" / "etx7b3_hydro_source_members.csv", "INSPECTED_NOT_SELECTED_NO_2019_COLUMNS"),
        ("CUTOUT_2019", "WEATHER_RUNOFF", _resolve(cfg["paths"]["cutout"]), "VALIDATED_REUSED_SOURCE"),
        ("ENTSOE_LOAD", "DEMAND", _resolve(cfg["paths"]["entsoe_load"]), "PRIMARY_SOURCE"),
        ("OPSD_LOAD", "DEMAND", _resolve(cfg["paths"]["opsd_load"]), "DIRECT_GAP_RECOVERY_SECONDARY_SOURCE"),
        ("MT_TYNDP_DEMAND", "DEMAND", _resolve(cfg["paths"]["mt_demand"]), "EXPLICIT_ADAPTER_SOURCE"),
        ("TN_TYNDP_DEMAND", "DEMAND", _resolve(cfg["paths"]["tn_demand"]), "EXPLICIT_ADAPTER_SOURCE"),
        ("MT_PECD_SOLAR", "VRE", _resolve(cfg["paths"]["mt_solar"]), "OFFICIAL_2019_PROFILE"),
        ("TN_PECD_SOLAR", "VRE", _resolve(cfg["paths"]["tn_solar"]), "OFFICIAL_2018_ADAPTER_PROFILE"),
        ("TN_PECD_ONWIND", "VRE", _resolve(cfg["paths"]["tn_onwind"]), "OFFICIAL_2018_ADAPTER_PROFILE"),
        ("TN_PECD_CSP", "VRE", _resolve(cfg["paths"]["tn_csp"]), "OFFICIAL_2019_PROFILE"),
        ("PYPSA_EUR_SOLAR_MATRIX", "SPATIAL_WEIGHTING", _resolve(cfg["pypsa_eur_spatial_assets"]["availability_solar"], pypsa_eur_root=upstream), "SPATIAL_WEIGHTING_ONLY"),
        ("PYPSA_EUR_ONWIND_MATRIX", "SPATIAL_WEIGHTING", _resolve(cfg["pypsa_eur_spatial_assets"]["availability_onwind"], pypsa_eur_root=upstream), "SPATIAL_WEIGHTING_ONLY"),
        ("PYPSA_EUR_OFFWIND_MATRIX", "SPATIAL_WEIGHTING", _resolve(cfg["pypsa_eur_spatial_assets"]["availability_offwind"], pypsa_eur_root=upstream), "SPATIAL_WEIGHTING_ONLY"),
        ("PYPSA_EUR_COUNTRY_SHAPES", "HYDRO_GEOMETRY", _resolve(cfg["pypsa_eur_spatial_assets"]["country_shapes"], pypsa_eur_root=upstream), "GEOMETRY_ONLY"),
        ("PYPSA_EUR_ONSHORE_REGIONS", "ITALY_SPATIAL_WEIGHTING", _resolve(cfg["pypsa_eur_spatial_assets"]["onshore_regions"], pypsa_eur_root=upstream), "SPATIAL_WEIGHTING_ONLY"),
        ("PYPSA_EUR_EEZ_ARCHIVE", "MT_TN_OFFWIND_GEOMETRY", _resolve(cfg["pypsa_eur_spatial_assets"]["eez_archive"], pypsa_eur_root=upstream), "GEOMETRY_ONLY"),
        ("EXTERNAL_DEMAND_STATIC", "FROZEN_ANNUAL_CONTROL", EXTERNAL_DEMAND, "FROZEN_ETX7B1"),
        ("EXTERNAL_GENERATORS_STATIC", "PROFILE_REQUIREMENT", EXTERNAL_GENERATORS, "FROZEN_ETX7B1"),
        ("ITALY_DEMAND_STATIC", "FROZEN_ANNUAL_CONTROL", ITALY_DEMAND, "FROZEN_ETX7B2"),
        ("ITALY_GENERATORS_STATIC", "PROFILE_REQUIREMENT", ITALY_GENERATORS, "FROZEN_ETX7B2"),
        ("ITALY_ZONAL_DEMAND", "SPATIAL_LOAD_CONTROL", ITALY_ZONAL_DEMAND, "FROZEN_STAGE_B"),
        ("ITALY_ZONAL_GENERATORS", "SPATIAL_VRE_WEIGHT", ITALY_ZONAL_GENERATORS, "FROZEN_STAGE_B"),
        ("ITALY_HYDRO_MAPPING", "SPATIAL_HYDRO_WEIGHT", ITALY_HYDRO_MAPPING, "FROZEN_STAGE_B"),
    ]
    naturalearth = _resolve(cfg["paths"]["naturalearth_lowres"])
    for sibling in sorted(naturalearth.parent.glob(f"{naturalearth.stem}.*")):
        source_paths.append((f"NATURAL_EARTH_{sibling.suffix[1:].upper()}", "TN_HYDRO_GEOMETRY", sibling, "EXPLICIT_ADAPTER_GEOMETRY"))
    hydro_audit = pd.read_csv(ROOT / "config" / "etx7b3_hydro_source_members.csv")
    for row_number, record in enumerate(hydro_audit.to_dict(orient="records"), start=1):
        source_paths.append(
            (
                f"TYNDP_HYDRO_AUDIT_{row_number:02d}",
                "HYDRO_SOURCE_METHOD_AUDIT",
                ROOT / str(record["output"]),
                "INSPECTED_NOT_SELECTED_NO_2019_COLUMNS",
            )
        )
    rows = []
    for source_id, role, path, status in source_paths:
        if not path.exists():
            raise FileNotFoundError(f"Required ETX-7B3 source is missing: {path}")
        rows.append(
            {
                "source_id": source_id,
                "role": role,
                "path": str(path).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "status": status,
            }
        )
    return pd.DataFrame(rows).sort_values("source_id", kind="stable").reset_index(drop=True)


def _manifest(output_dir: Path) -> pd.DataFrame:
    items: list[tuple[str, Path]] = [
        (key, output_dir / filename) for key, filename in OUTPUT_FILES.items() if key != "manifest"
    ]
    items.extend((f"cache_{key}", output_dir / "cache" / filename) for key, filename in CACHE_FILES.items())
    rows = []
    for artifact, path in items:
        if not path.exists():
            raise FileNotFoundError(f"Output artifact missing before manifest: {path}")
        rows.append(
            {
                "artifact": artifact,
                "relative_path": str(path.relative_to(output_dir)).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "rows": _row_count(path),
                "sha256": sha256_file(path),
                "schema_version": "ETX7B3_TEMPORAL_2019_V1_0",
                "status": "COMPLETE",
            }
        )
    return pd.DataFrame(rows).sort_values("artifact", kind="stable").reset_index(drop=True)


def build_temporal_package(
    output_dir: Path | None = None,
    *,
    config_path: Path = DEFAULT_CONFIG,
    source_cache_dir: Path | None = None,
) -> dict[str, Any]:
    cfg = _load_config(config_path)
    output_dir = output_dir or _resolve(cfg["paths"]["output_directory"])
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshots = canonical_snapshots()
    snapshot_frame = pd.DataFrame(
        {
            "snapshot": snapshots,
            "chronology_year": 2019,
            "timezone": "UTC",
            "frequency": "1h",
            "snapshot_weight_hours": 1.0,
        }
    )
    _write_parquet(snapshot_frame, output_dir / OUTPUT_FILES["snapshots"])

    source_receipt = _source_receipt(cfg)
    _write_csv(source_receipt, output_dir / OUTPUT_FILES["source_receipt"])

    load, demand_provenance, load_compatibility = _build_demand(cfg)
    _write_parquet(load, output_dir / OUTPUT_FILES["load"])

    caches = _build_or_copy_weather_caches(
        cfg,
        output_dir / "cache",
        source_cache_dir,
    )
    vre, vre_map, vre_provenance = _build_vre(cfg, caches)
    _write_parquet(vre, output_dir / OUTPUT_FILES["vre"])
    hydro, hydro_map, hydro_provenance = _build_hydro(caches)
    _write_parquet(hydro, output_dir / OUTPUT_FILES["hydro"])

    coverage = _coverage_registry(
        demand_provenance,
        vre_map,
        vre_provenance,
        hydro_map,
        hydro_provenance,
    )
    _write_csv(coverage, output_dir / OUTPUT_FILES["coverage"])
    provenance = pd.DataFrame(demand_provenance + vre_provenance + hydro_provenance)
    provenance = provenance.sort_values(
        ["profile_family", "country_code", "profile_id"], kind="stable"
    ).reset_index(drop=True)
    _write_csv(provenance, output_dir / OUTPUT_FILES["provenance"])

    manifest = _manifest(output_dir)
    _write_csv(manifest, output_dir / OUTPUT_FILES["manifest"])
    return {
        "output_dir": output_dir,
        "frames": {
            "snapshots": snapshot_frame,
            "coverage": coverage,
            "load": load,
            "vre": vre,
            "hydro": hydro,
            "provenance": provenance,
            "source_receipt": source_receipt,
            "load_compatibility": load_compatibility,
            "manifest": manifest,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build ETX-7B3 fixed-ten-market 2019 temporal inputs")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--source-cache-dir", type=Path)
    args = parser.parse_args()
    result = build_temporal_package(
        args.output_dir,
        config_path=args.config,
        source_cache_dir=args.source_cache_dir,
    )
    print(
        json.dumps(
            {
                "status": "BUILT_NOT_YET_VALIDATED",
                "output_dir": str(result["output_dir"]),
                "rows": {
                    key: len(frame)
                    for key, frame in result["frames"].items()
                    if isinstance(frame, pd.DataFrame)
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from mem_model.stage_a.temporal_2019 import (
    CACHE_FILES,
    CORE_MARKETS,
    HORIZONS,
    OUTPUT_FILES,
    ROOT,
    build_temporal_package,
    canonical_snapshots,
)
from mem_model.stage_a.temporal_validation import (
    DEFAULT_OUTPUT_DIR,
    validate_temporal_package,
)


def _read_output(key: str) -> pd.DataFrame:
    path = DEFAULT_OUTPUT_DIR / OUTPUT_FILES[key]
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def test_canonical_2019_utc_snapshot_contract() -> None:
    snapshots = _read_output("snapshots")
    observed = pd.DatetimeIndex(pd.to_datetime(snapshots["snapshot"], utc=True))
    assert len(observed) == 8760
    assert observed.is_unique
    assert observed.is_monotonic_increasing
    assert observed.equals(canonical_snapshots())


def test_all_core_market_demands_are_complete_and_exact() -> None:
    load = _read_output("load")
    expected_groups = {(year, market) for year in HORIZONS for market in CORE_MARKETS}
    assert set(zip(load["horizon"].astype(int), load["country_code"])) == expected_groups
    assert load.groupby(["horizon", "country_code"]).size().eq(8760).all()
    assert set(load["scenario"]) == {"Base"}
    assert np.isfinite(load[["load_MW", "normalized_shape_value"]].to_numpy()).all()
    assert (load[["load_MW", "normalized_shape_value"]].to_numpy() >= 0).all()


def test_required_vre_profiles_are_bounded_and_not_all_zero() -> None:
    coverage = _read_output("coverage")
    vre = _read_output("vre")
    required = coverage.loc[
        coverage["required"].astype(str).str.lower().eq("true")
        & coverage["profile_family"].isin(
            [
                "SOLAR_PV",
                "SOLAR_PV_ROOFTOP",
                "SOLAR_PV_UTILITY",
                "WIND_ONSHORE",
                "WIND_OFFSHORE",
                "CSP",
            ]
        )
    ]
    assert required["status"].eq("COMPLETE").all()
    values = vre["p_max_pu"].to_numpy(dtype=float)
    assert np.isfinite(values).all()
    assert values.min() >= -1e-10
    assert values.max() <= 1 + 1e-10
    assert all((part["p_max_pu"].to_numpy(dtype=float) > 0).any() for _, part in vre.groupby("profile_id"))


def test_hydro_profiles_are_distinct_normalized_and_exclude_phs() -> None:
    hydro = _read_output("hydro")
    assert not hydro["hydro_class"].str.upper().str.contains(
        "PURE_PHS|MIXED_PHS|PUMPED_HYDRO|PUMPED_STORAGE",
        regex=True,
    ).any()
    assert {
        "HYDRO_RUN_OF_RIVER",
        "HYDRO_BASIN_PONDAGE",
        "HYDRO_RESERVOIR",
    } <= set(hydro.loc[hydro["country_code"].eq("IT"), "hydro_class"])
    values = hydro["value"].to_numpy(dtype=float)
    assert np.isfinite(values).all()
    assert (values >= 0).all()
    assert np.allclose(
        hydro.groupby("profile_id")["value"].sum().to_numpy(dtype=float),
        1.0,
        rtol=0,
        atol=1e-12,
    )


def test_temporal_scope_is_fixed_ten_markets_and_b4_is_deferred() -> None:
    coverage = _read_output("coverage")
    assert set(coverage["country_code"]) == set(CORE_MARKETS)
    assert set(coverage["market_role"]) == {"CORE_STAGE_A_MARKET"}
    deferred = coverage.loc[
        coverage["required"].eq("DEFERRED_RUNTIME_REQUIREMENT")
    ]
    assert not deferred.empty
    assert deferred["status"].eq("DEFER_ETX7B4").all()
    required = coverage.loc[coverage["required"].astype(str).str.lower().eq("true")]
    assert required["status"].eq("COMPLETE").all()


def test_every_profile_is_uniquely_traceable() -> None:
    load = _read_output("load")
    vre = _read_output("vre")
    hydro = _read_output("hydro")
    provenance = _read_output("provenance")
    emitted = set(load["profile_id"]) | set(vre["profile_id"]) | set(hydro["profile_id"])
    assert emitted <= set(provenance["profile_id"])
    assert provenance["profile_id"].is_unique
    adapters = provenance.loc[
        provenance["evidence_or_provenance_class"].eq("EXPLICIT_ADAPTER")
    ]
    assert not adapters.empty
    assert adapters[
        ["country_code", "source_file_or_dataset", "source_year", "transformation"]
    ].apply(lambda column: column.astype(str).str.strip().ne("")).all().all()


def test_cache_backed_rebuild_is_byte_deterministic(tmp_path: Path) -> None:
    rebuilt = tmp_path / "temporal"
    build_temporal_package(
        rebuilt,
        source_cache_dir=DEFAULT_OUTPUT_DIR / "cache",
    )
    relative_files = [Path(name) for name in OUTPUT_FILES.values()]
    relative_files += [Path("cache") / name for name in CACHE_FILES.values()]
    for relative in relative_files:
        assert (DEFAULT_OUTPUT_DIR / relative).read_bytes() == (rebuilt / relative).read_bytes()


def test_full_etx7b3_validation_passes_without_topology_or_solver() -> None:
    result = validate_temporal_package(
        DEFAULT_OUTPUT_DIR,
        write_reports=False,
        run_deterministic_rebuild=False,
    )
    # The deterministic check is intentionally omitted here because the preceding
    # test performs the byte-for-byte rebuild independently.
    qa = result["qa"].loc[result["qa"]["check_id"].ne("ETX7B3-QA-25")]
    assert qa["status"].eq("PASS").all()
    assert result["verification"]["scope_confirmation"] == {
        "context_markets": "NOT_IN_SCOPE_FIXED_TEN_MARKET_REVISION",
        "operating_parameters": "DEFER_ETX7B4",
        "topology": "NOT_BUILT",
        "pypsa_network": "NOT_INSTANTIATED",
        "optimization": "NOT_RUN",
        "solver_configuration": "UNCHANGED_AND_DISABLED",
    }

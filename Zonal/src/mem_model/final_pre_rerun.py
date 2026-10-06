"""Sparse final-v2 successor preparation. Arithmetic/serialization only, never a solve."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa

from .common import ROOT, SCENARIOS, ZONES, dump_json, sha256_file
from .final_methodology_networks import structural_check, verify_v2_frozen_contract
from .stage_b_zonal_vre import no_models_or_solves

QA = ROOT / "qa/final_pre_rerun/final_successor"
NETWORKS = ROOT / "networks/unsolved/final_methodology_v3"
DOWNLOADS = Path.home() / "Downloads"
META = "final_pre_rerun_v3"
VRE = ("wind_onshore", "wind_offshore", "solar_pv_rooftop", "solar_pv_utility")
TARGETS = {"WIND": 121 / (49.1 * 8760 / 1000), "SOLAR": 168 / (121 * 8760 / 1000)}
SOLAR_ADJUSTMENT_ENABLED = False  # User delta: omit the close 1.588% downward adjustment.
FLAGS = dict(optimization_model_constructed=False, production_optimization_executed=False,
             optimizer_invocations=0, new_smoke_solves=0, full_year_solves=0,
             production_results_created=0, final_v2_modified=False, final_v1_modified=False)


def relative(path):
    return str(Path(path).relative_to(ROOT)) if Path(path).is_relative_to(ROOT) else str(path)


def write_table(name, rows):
    table = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    table.to_csv(QA / name, index=False, float_format="%.17g")
    return table


def bounded_factor(values, target, weights=None):
    """Deterministic scalar bisection of sum(w * min(1, a*x)); no optimizer."""
    x = np.asarray(values, dtype=float)
    w = np.ones_like(x) if weights is None else np.broadcast_to(weights, x.shape)
    if not np.isfinite(x).all() or (x < 0).any() or not np.isfinite(w).all() or (w < 0).any():
        raise RuntimeError("INVALID_CALIBRATION_INPUT")
    potential = float(w[x > 0].sum())
    if not np.isfinite(target) or target < 0 or target > potential + 1e-10:
        raise RuntimeError("BOUNDED_TARGET_PHYSICALLY_INFEASIBLE")
    if target == 0:
        return 0., np.zeros_like(x)
    lo, hi = 0., 1.
    while float((w * np.minimum(1, hi * x)).sum()) < target:
        hi *= 2
        if hi > 2**50:
            raise RuntimeError("BOUNDED_TARGET_NOT_REACHED")
    for _ in range(90):
        mid = (lo + hi) / 2
        if float((w * np.minimum(1, mid * x)).sum()) < target:
            lo = mid
        else:
            hi = mid
    factor = (lo + hi) / 2
    result = np.minimum(1, factor * x)
    if not np.isclose(float((w * result).sum()), target, rtol=2e-14, atol=2e-10):
        raise RuntimeError("BOUNDED_ENERGY_RECONCILIATION_FAIL")
    return factor, result


def historical_indices(raw_path=None, monthly_check_path=None):
    """Accepted source-hour accounting; no timestamp repair, deduplication or fitting."""
    raw = pd.read_csv(raw_path or DOWNLOADS / "Explore-data-2026-10-05 20_11_49.csv",
                      parse_dates=["time_utc"])
    raw = raw.loc[raw.production_type.eq("wind_onshore") & raw.time_utc.ge("2021-01-01")
                  & raw.time_utc.lt("2026-01-01")].copy()
    raw["zone"] = raw.mem_zone.replace({"CNORD": "CNOR"})
    if set(raw.zone) != set(ZONES) or len(raw) != 306768 or not np.isfinite(raw.generation_mw).all():
        raise RuntimeError("HISTORICAL_COVERAGE_FAIL")
    raw["year"] = raw.time_utc.dt.year
    raw["month"] = raw.time_utc.dt.month
    raw["quarter"] = raw.time_utc.dt.quarter
    annual = raw.groupby(["zone", "year"]).generation_mw.mean()
    tables, annual_rows = {}, []
    for period in ("month", "quarter"):
        avg = raw.groupby(["zone", "year", period]).generation_mw.mean()
        indices = (avg / annual).rename("annual_normalized_index")
        table = indices.groupby(["zone", period]).agg(["median", "min", "max"]).reset_index()
        tables[period] = table
        annual_rows.append(indices.reset_index().assign(period_type=period).rename(columns={period: "period"}))
    check = pd.read_csv(monthly_check_path or DOWNLOADS / "MEM_2040_BASE_FINAL_V1_WIND_Q2Q_MONTHLY_COMPARISON.csv")
    check["zone"] = check.zone.replace({"CNORD": "CNOR"})
    if len(check) != 84 or check.duplicated(["zone", "month"]).any():
        raise RuntimeError("EXACT_2025_CHECK_SCHEMA_FAIL")
    # Full-precision contemporary monthly CF from the supplied accepted capacity-normalized preview.
    # This is a QA-only denominator, never the temporal target or future annual CF.
    tables["month"] = tables["month"].merge(check[["zone", "month", "historical_2025_exact_CF"]],
                                             on=["zone", "month"], validate="one_to_one")
    times = pd.date_range("2019-01-01", periods=8760, freq="h")
    hours = np.bincount(times.month, minlength=13)[1:]
    for z in ZONES:
        row = tables["month"].zone.eq(z)
        annual_cf = float(np.dot(tables["month"].loc[row, "historical_2025_exact_CF"], hours) / 8760)
        tables["month"].loc[row, "historical_2025_exact_index"] = (
            tables["month"].loc[row, "historical_2025_exact_CF"] / annual_cf)
        for q in range(1, 5):
            months = np.arange(3*q-2, 3*q+1)
            cf = float(np.dot(tables["month"].loc[row].set_index("month").loc[months,
                "historical_2025_exact_CF"], hours[months-1]) / hours[months-1].sum())
            r = tables["quarter"].zone.eq(z) & tables["quarter"].quarter.eq(q)
            tables["quarter"].loc[r, "historical_2025_exact_CF"] = cf
            tables["quarter"].loc[r, "historical_2025_exact_index"] = cf / annual_cf
    coverage = [{"zone": z, "source_rows": len(group), "unique_UTC_timestamps": group.time_utc.nunique(),
                 "duplicate_rows": int(group.time_utc.duplicated().sum()),
                 "missing_UTC_timestamps": len(pd.date_range("2021-01-01", "2025-12-31 23:00", freq="h").difference(group.time_utc)),
                 "treatment": "ACCEPTED_SOURCE_ROW_EQUALS_ONE_HOUR_NO_REPAIR"}
                for z, group in raw.groupby("zone")]
    return tables, pd.concat(annual_rows, ignore_index=True), coverage


def onshore_temporal(profile, times, weights, quarter_medians, month_medians):
    stage0 = np.asarray(profile, float)
    w = np.asarray(weights, float)
    annual = float(np.dot(stage0, w))
    stage1, stage2 = stage0.copy(), stage0.copy()
    q_records, m_records = [], []
    q_hours = np.array([w[times.quarter == q].sum() for q in range(1, 5)])
    q_medians = np.asarray(quarter_medians, float)
    q_targets = annual * q_medians * q_hours / np.dot(q_medians, q_hours)
    for q in range(1, 5):
        mask = times.quarter == q
        factor, stage1[mask] = bounded_factor(stage0[mask], q_targets[q-1], w[mask])
        q_records.append({"quarter": q, "factor": factor, "target_pu_hours": q_targets[q-1],
            "before_pu_hours": float(np.dot(stage0[mask], w[mask])),
            "after_pu_hours": float(np.dot(stage1[mask], w[mask])),
            "historical_median_raw": q_medians[q-1],
            "historical_target_normalized": q_targets[q-1] / (annual / w.sum()) / w[mask].sum(),
            "cap_count": int((factor * stage0[mask] >= 1).sum()),
            "max_pre_cap": float((factor * stage0[mask]).max())})
        months = range(3*q-2, 3*q+1)
        weighted = np.array([month_medians[m-1] * w[times.month == m].sum() for m in months])
        shares = weighted / weighted.sum()
        for m, share in zip(months, shares):
            mm = times.month == m
            target = float(np.dot(stage1[mask], w[mask])) * share
            multiplier, stage2[mm] = bounded_factor(stage1[mm], target, w[mm])
            m_records.append({"month": m, "quarter": q, "factor": multiplier,
                "historical_within_quarter_energy_share": share, "target_pu_hours": target,
                "before_pu_hours": float(np.dot(stage1[mm], w[mm])),
                "after_pu_hours": float(np.dot(stage2[mm], w[mm])),
                "cap_count": int((multiplier * stage1[mm] >= 1).sum()),
                "max_pre_cap": float((multiplier * stage1[mm]).max())})
    if not np.isclose(np.dot(stage2, w), annual, rtol=2e-14, atol=2e-10):
        raise RuntimeError("ANNUAL_TEMPORAL_PRESERVATION_FAIL")
    for q in range(1, 5):
        mask = times.quarter == q
        if not np.isclose(np.dot(stage2[mask], w[mask]), np.dot(stage1[mask], w[mask]), rtol=2e-14, atol=2e-10):
            raise RuntimeError("QUARTER_TEMPORAL_PRESERVATION_FAIL")
    if not np.array_equal(stage2[stage0 == 0], stage0[stage0 == 0]):
        raise RuntimeError("ZERO_RESOURCE_HOUR_CHANGED")
    for m in range(1, 13):
        mask = times.month == m
        if (np.diff(stage2[mask][np.argsort(stage0[mask], kind="stable")]) < -1e-14).any():
            raise RuntimeError("WITHIN_MONTH_ORDER_CHANGED")
    return stage1, stage2, q_records, m_records


def exact_diff(parent, child, initialization=None):
    """Every native static/dynamic object compared, including unused components/metadata."""
    initialization = initialization or {}
    rows = []
    def frame(a, b):
        pd.testing.assert_frame_equal(a, b, check_exact=True, check_dtype=False, check_freq=False)
    try:
        pd.testing.assert_index_equal(parent.snapshots, child.snapshots)
        frame(parent.snapshot_weightings, child.snapshot_weightings)
        pd.testing.assert_index_equal(parent.investment_periods, child.investment_periods)
        frame(parent.investment_period_weightings, child.investment_period_weightings)
        assert parent.name == child.name and parent.srid == child.srid
        assert set(parent.components.keys()) == set(child.components.keys())
        ids = parent.generators.index[parent.generators.carrier.isin(VRE[:2])]
        for comp in parent.components.values():
            other = child.components[comp.name]
            a, b = comp.static, other.static.copy()
            if comp.name == "Generator":
                for name, values in initialization.items():
                    assert name in a.index and bool(a.at[name, "committable"])
                    for field, value in values.items():
                        assert field in ("up_time_before", "down_time_before")
                        assert child.generators.at[name, field] == value
                        b.at[name, field] = a.at[name, field]
            frame(a, b)
            rows.append({"object": comp.name, "field": "static", "status": "EXACT_EXCEPT_EXPLICIT_INITIALIZATION" if comp.name == "Generator" and initialization else "EXACT"})
            assert set(comp.dynamic) == set(other.dynamic)
            for key in comp.dynamic:
                a, b = comp.dynamic[key], other.dynamic[key].copy()
                if comp.name == "Generator" and key == "p_max_pu":
                    assert set(a.columns) == set(b.columns)
                    b.loc[:, ids] = a.loc[:, ids]
                frame(a, b)
                rows.append({"object": comp.name, "field": key, "status": "AUTHORIZED_WIND_PROFILE_CHANGE_SOLAR_EXACT" if comp.name == "Generator" and key == "p_max_pu" else "EXACT"})
        assert set(child.meta) == set(parent.meta) | {META}
        assert {k: v for k, v in child.meta.items() if k != META} == parent.meta
        assert child.meta[META]["parent_sha256"]
    except (AssertionError, KeyError, ValueError) as error:
        raise RuntimeError("UNEXPECTED_SUCCESSOR_DRIFT") from error
    return rows


def source_manifest():
    sources = [(DOWNLOADS / name, "SUPPLIED_IMMUTABLE_EVIDENCE") for name in (
        "Explore-data-2026-10-05 20_11_49.csv", "MEM_WIND_MONTHLY_TERNA_DATASET_TRANSFER.md",
        "MEM_2040_BASE_FINAL_V1_WIND_Q2Q_PREVIEW.md", "MEM_2040_BASE_FINAL_V1_WIND_Q2Q_QUARTERLY_PREVIEW.csv",
        "MEM_2040_BASE_FINAL_V1_WIND_Q2Q_MONTHLY_COMPARISON.csv", "Export-DownloadCenterFile-20261005-182115.xlsx")]
    sources += [(ROOT.parent / "03_PRIMARY_SOURCES" / name, "LOCAL_OFFICIAL_PRIMARY_SOURCE") for name in (
        "Documento_Descrizione_Scenari_2024.pdf", "Terna_PdS_2025_Stato_sistema_scenari.pdf",
        "Terna_PdS_2025_Esigenze_sviluppo_nuovi_progetti.pdf", "Terna_PdS_2025_Benefici_robustezza_rete.pdf", "PNIEC_2024.pdf")]
    sources += [(ROOT / name, "ACCEPTED_PROJECT_AUTHORITY") for name in (
        "docs/final_methodology_closure/PHASE_W_NATIVE_CELL_RESOLUTION.md",
        "docs/final_methodology_closure/MEM_FINAL_METHODOLOGY_PHASE_W_NATIVE_CELL_TRANSFER.md",
        "docs/final_methodology_closure/MEM_FINAL_METHODOLOGY_PHASE_W_HORIZON_COST_TRANSFER.md",
        "docs/MEM_STAGE_B_ZONAL_VRE_V1_PREPARATION_TRANSFER.md",
        "qa/final_methodology_closure/network_v2b/NATIVE_SIGNED_LINK_API_RECEIPT.json",
        "qa/final_methodology_closure/network_contract/NETWORK_CONTRACT_SOURCE_AUTHORITY_RECEIPT.json",
        "qa/stage_b/p2x_flex_v2/FINAL_V2_NETWORK_LINEAGE_AND_HASHES.csv",
        "results/UC1_2040_BASE_168H/UC1_2040_BASE_168H_UC_MILP_SOLVED.nc",
        "results/final_methodology_v1/2040/Base/UC_MILP/FINAL_V1_UC2_2040_BASE_8760H_UC_MILP_SOLVED.nc")]
    sources += [(ROOT / "inputs/provenance/WIND_CALIBRATION_DECISION.json", "WIND_CALIBRATION_DECISION")]
    return [{"path": relative(p), "sha256": sha256_file(p), "size_bytes": p.stat().st_size, "role": role} for p, role in sources]


def derive_scalars(base):
    times = base.snapshots
    weights = base.snapshot_weightings.generators.to_numpy()
    records, factors = [], {}
    for family, carriers in (("WIND", VRE[:2]), ("SOLAR", VRE[2:])):
        ids = base.generators.index[base.generators.carrier.isin(carriers)]
        x = base.generators_t.p_max_pu[ids].to_numpy()
        caps = base.generators.loc[ids, "p_nom"].to_numpy()
        w = weights[:, None] * caps
        denominator = weights.sum() * caps.sum()
        native_energy = float((x * w).sum())
        target = TARGETS[family] * denominator
        factor, scaled = bounded_factor(x, target, w)
        proposed_factor = factor
        if family == "SOLAR" and not SOLAR_ADJUSTMENT_ENABLED:
            factor, scaled = 1.0, x.copy()
        factors[family] = factor
        records.append({"family": family, "installed_MW": caps.sum(), "weighted_hours": weights.sum(),
            "native_CF": native_energy / denominator, "unbounded_first_guess_ratio": target / native_energy,
            "bounded_scalar": factor, "target_CF": TARGETS[family], "result_CF": float((scaled*w).sum()) / denominator,
            "proposed_bounded_scalar": proposed_factor,
            "treatment": "RETAIN_NATIVE_SOLAR_USER_DELTA" if family == "SOLAR" and not SOLAR_ADJUSTMENT_ENABLED else "APPLY_BOUNDED_NATIONAL_SCALAR",
            "available_TWh_before": native_energy/1e6, "available_TWh_after": float((scaled*w).sum())/1e6,
            "capped_observations": int((factor*x >= 1).sum()),
            "hours_any_capped": int((factor*x >= 1).any(axis=1).sum()), "maximum_pre_cap": float((factor*x).max()),
            "authority": "DDS24_TABLE_13_PDF59_TABLE_18_PDF66_DE_IT_2040",
            "method": "MONOTONIC_BISECTION_OF_BOUNDED_CAPACITY_WEIGHTED_AVAILABILITY"})
    return factors, records


def transform(parent, row, factors, history, initialization=None):
    n = parent.copy()
    initialization = initialization or {}
    times = n.snapshots
    w = n.snapshot_weightings.generators.to_numpy()
    if len(times) != 8760 or not times.equals(pd.date_range("2019-01-01", periods=8760, freq="h", name=times.name)):
        raise RuntimeError("PARENT_CHRONOLOGY_FAIL")
    final_rows, q_rows, m_rows, boundary, hist_month, hist_quarter, monthly_qa = [], [], [], [], [], [], []
    for name, gen in n.generators.loc[n.generators.carrier.isin(VRE)].iterrows():
        zone, carrier, cap = gen.bus, gen.carrier, float(gen.p_nom)
        raw = parent.generators_t.p_max_pu[name].to_numpy().copy()
        if not np.isfinite(raw).all() or (raw < 0).any() or (raw > 1 + 1e-12).any():
            raise RuntimeError("NATIVE_VRE_BOUNDS_FAIL")
        factor = factors["WIND" if carrier.startswith("wind_") else "SOLAR"]
        stage0 = np.minimum(1, factor*raw) if carrier.startswith("wind_") else raw.copy()
        stage1, final = stage0.copy(), stage0.copy()
        case = {"year": row["year"], "scenario": row["scenario"], "zone": zone, "family": carrier, "generator_id": name}
        treatment = "RETAIN_NATIVE_SOLAR_USER_DELTA"
        if carrier == "wind_onshore":
            qh = history["quarter"].loc[history["quarter"].zone.eq(zone)].set_index("quarter").loc[range(1,5)]
            mh = history["month"].loc[history["month"].zone.eq(zone)].set_index("month").loc[range(1,13)]
            stage1, final, qr, mr = onshore_temporal(stage0, times, w, qh["median"].to_numpy(), mh["median"].to_numpy())
            q_rows.extend([{**case, **r, "target_MWh": r["target_pu_hours"]*cap,
                           "before_MWh": r["before_pu_hours"]*cap, "after_MWh": r["after_pu_hours"]*cap} for r in qr])
            m_rows.extend([{**case, **r, "target_MWh": r["target_pu_hours"]*cap,
                           "before_MWh": r["before_pu_hours"]*cap, "after_MWh": r["after_pu_hours"]*cap} for r in mr])
            treatment = "COMMON_NATIONAL_SCALAR_THEN_Q2Q_THEN_MONTHLY_WITHIN_QUARTER"
            for period, values, holder in (("month", mh, hist_month), ("quarter", qh, hist_quarter)):
                for p, h in values.iterrows():
                    mask = getattr(times, period) == p
                    index = float(np.dot(final[mask], w[mask]) / w[mask].sum() / (np.dot(final,w)/w.sum()))
                    holder.append({**case, period: int(p), "final_seasonal_index": index,
                        "historical_median_raw": h["median"], "historical_min": h["min"], "historical_max": h["max"],
                        "exact_2025_CF": h["historical_2025_exact_CF"], "exact_2025_index": h["historical_2025_exact_index"],
                        "inside_envelope": bool(h["min"] - 1e-12 <= index <= h["max"] + 1e-12),
                        "deviation_vs_raw_median": index - h["median"],
                        "envelope_role": "DIAGNOSTIC_NOT_ANNUAL_PRODUCTIVITY_AUTHORITY"})
            for m in range(1,13):
                mask = times.month == m
                monthly_qa.append({**case, "month": m, "min": float(final[mask].min()), "max": float(final[mask].max()),
                    "source_zero_hours": int((raw[mask] == 0).sum()), "source_zeros_preserved": bool((final[mask][raw[mask] == 0] == 0).all()),
                    "finite": bool(np.isfinite(final[mask]).all()), "within_month_order_preserved": True,
                    "target_MWh_error": (float(np.dot(final[mask], w[mask]))-mr[m-1]["target_pu_hours"])*cap})
        elif carrier == "wind_offshore":
            treatment = "RETAIN_CURRENT_OFFSHORE_TEMPORAL_SHAPE_AFTER_ANNUAL_SCALING"
        n.generators_t.p_max_pu.loc[:, name] = final
        if carrier.startswith("wind_"):
            for i in np.flatnonzero(times.month != np.roll(times.month, 1)):
                prev = (i-1) % len(times)
                ramps = {label: float(v[i]-v[prev]) for label, v in (("native",raw),("stage0",stage0),("q2q",stage1),("final",final))}
                interior = np.abs(np.diff(final)[times.month[1:] == times.month[:-1]])
                extreme = abs(ramps["final"]) > interior.max()+1e-12
                inherited_wrap = i==0 and abs(ramps["stage0"]) > interior.max()+1e-12
                boundary.append({**case, "snapshot": str(times[i]), "cyclic_december_january": bool(i == 0),
                    **{k+"_ramp_pu":v for k,v in ramps.items()}, "final_interior_max_abs_ramp_pu": float(interior.max()),
                    "final_interior_p99_abs_ramp_pu": float(np.quantile(interior,.99)),
                    "outside_full_interior_ramp_range": bool(extreme),
                    "absolute_ramp_increment_vs_stage0":abs(ramps["final"])-abs(ramps["stage0"]),
                    "inherited_non_chronological_year_wrap":bool(inherited_wrap),
                    "adjustment": "NONE_WITHIN_INTRAMONTH_RAMP_RANGE" if not extreme else "NO_NEW_TREATMENT_INHERITED_YEAR_WRAP_RETAINED" if inherited_wrap else "REVIEW_REQUIRED"})
        energies = [float(np.dot(v, w))*cap for v in (raw,stage0,stage1,final)]
        quarter_indices = {}; month_indices = {}
        for label, v in (("parent",raw),("stage0",stage0),("q2q",stage1),("final",final)):
            annual_mean = np.dot(v,w)/w.sum()
            for attr, result, maximum in (("quarter",quarter_indices,4),("month",month_indices,12)):
                result[label] = [float(np.dot(v[getattr(times,attr)==p],w[getattr(times,attr)==p])/
                    w[getattr(times,attr)==p].sum()/annual_mean) for p in range(1,maximum+1)]
        final_rows.append({**case,"installed_MW": cap, "parent_annual_CF": energies[0]/cap/w.sum(),
            "post_scalar_annual_CF": energies[1]/cap/w.sum(), "post_Q2Q_annual_CF": energies[2]/cap/w.sum(),
            "final_annual_CF": energies[3]/cap/w.sum(), "parent_available_MWh": energies[0],
            "post_scalar_available_MWh": energies[1], "final_available_MWh": energies[3],
            "annual_scalar": factor, "stage0_cap_count": int((factor*raw >= 1).sum()),
            "final_cap_count": int((final >= 1).sum()), "stage0_max_pre_cap": float((factor*raw).max()),
            "quarterly_indices": json.dumps(quarter_indices), "monthly_indices": json.dumps(month_indices),
            "treatment": treatment, "status":"PASS"})
    for name, fields in initialization.items():
        for field, value in fields.items():
            n.generators.at[name, field] = int(value)
    n.meta = copy.deepcopy(parent.meta)
    n.meta[META] = {"variant": "final_v3", "parent_path": row["final_v2_path"],
        "parent_sha256": row["final_v2_sha256"], "wind_scalar": factors["WIND"], "solar_scalar": factors["SOLAR"],
        "weather": "ACCEPTED_2019_WITHIN_MONTH_ORDER", "temporal_target": "OBSERVED_2021_2025_ZONAL_MEDIAN",
        "UC_initialization": "COMPATIBLE_UNITS_ONLY_SMOKE_DERIVED_APPROXIMATION_NOT_HISTORICAL_TRUTH",
        "solar_policy": "RETAIN_NATIVE_SOLAR_USER_DELTA",
        "capacity_or_siting_change": False, **FLAGS}
    return n, {"vre": final_rows,"quarter":q_rows,"month":m_rows,"boundary":boundary,
               "historical_month":hist_month,"historical_quarter":hist_quarter,"monthly_qa":monthly_qa}


def preview_equality(parent, solved):
    records = []
    for carrier in VRE:
        a = parent.generators.index[parent.generators.carrier.eq(carrier)]
        b = solved.generators.index[solved.generators.carrier.eq(carrier)]
        if not a.equals(b) or not parent.snapshots.equals(solved.snapshots):
            raise RuntimeError("FINAL_V1_PREVIEW_VRE_IDENTITIES_OR_CHRONOLOGY_DIFFER")
        x, y = parent.generators_t.p_max_pu[a], solved.generators_t.p_max_pu[b]
        cap = parent.generators.loc[a,"p_nom"]
        diff = (x-y).abs()
        records.append({"family":carrier,"IDs_match":True,"snapshots_match":True,
            "installed_MW_match":bool(cap.equals(solved.generators.loc[b,"p_nom"])),
            "max_absolute_profile_difference":float(diff.to_numpy().max()),
            "mean_absolute_profile_difference":float(diff.to_numpy().mean()),
            "available_MWh_difference":float(((x-y).mul(cap).mul(parent.snapshot_weightings.generators,axis=0)).sum().sum()),
            "status":"EXACT" if diff.to_numpy().max() == 0 else "RECOMPUTE_FROM_FINAL_V2"})
    return records


def smoke_initialization(parent, smoke, year, scenario):
    """Exact unit identity only. No aliasing heterogeneous children onto old units."""
    fields = ("bus", "carrier", "p_nom", "sign", "efficiency", "marginal_cost", "committable",
        "p_min_pu", "p_max_pu", "min_up_time", "min_down_time", "ramp_limit_up", "ramp_limit_down",
        "ramp_limit_start_up", "ramp_limit_shut_down", "start_up_cost", "shut_down_cost", "stand_by_cost")
    dynamic = ("p_min_pu", "p_max_pu", "marginal_cost", "efficiency", "ramp_limit_up", "ramp_limit_down")
    units = parent.generators.index[parent.generators.committable]
    changes, rows = {}, []
    for name in units:
        match = name in smoke.generators.index and bool(smoke.generators.at[name, "committable"])
        reason = "EXACT_ID_ABSENT_IN_ACCEPTED_SMOKE"
        if match:
            a, b = parent.generators.loc[name, list(fields)], smoke.generators.loc[name, list(fields)]
            match = a.equals(b)
            reason = "STATIC_INPUT_MISMATCH" if not match else "EXACT_UNIT_COHORT_INPUTS"
        if match:
            for field in dynamic:
                a = parent.get_switchable_as_dense("Generator", field).loc[smoke.snapshots,name]
                b = smoke.get_switchable_as_dense("Generator", field).loc[:,name]
                if not a.equals(b):
                    match = False; reason = "HOURLY_OPERATING_INPUT_MISMATCH:"+field
                    break
        record = {"year":year,"scenario":scenario,"unit_id":name,"carrier":parent.generators.at[name,"carrier"],
                  "p_nom_MW":parent.generators.at[name,"p_nom"],"hard_compatible":bool(match),"reason":reason,
                  "soft_mismatch_role":"EXOGENOUS_P2X_WEATHER_DIFFERENCES_NOT_A_HARD_GATE"}
        if match:
            status = smoke.generators_t.status[name].to_numpy()
            if not np.isfinite(status).all() or not np.isclose(status, np.round(status),atol=1e-6,rtol=0).all():
                raise RuntimeError("SMOKE_STATUS_NOT_BINARY_OR_COMPLETE")
            status = np.round(status).astype(int)
            terminal = int(status[-1]); count = 0
            for value in status[::-1]:
                if value != terminal: break
                count += 1
            minimum = int(parent.generators.at[name,"min_up_time" if terminal else "min_down_time"])
            duration = min(count, max(1, minimum))
            changes[name] = {"up_time_before":duration if terminal else 0,
                             "down_time_before":0 if terminal else duration}
            record.update(terminal_status=terminal,observed_terminal_consecutive_hours=count,
                duration_beyond_smoke_unknown=count==len(status),
                old_up_time_before=int(parent.generators.at[name,"up_time_before"]),
                old_down_time_before=int(parent.generators.at[name,"down_time_before"]),
                new_up_time_before=changes[name]["up_time_before"],new_down_time_before=changes[name]["down_time_before"],
                treatment="SMOKE_DERIVED_UC_INITIAL_STATE_APPROXIMATION",p_init_treatment="EXACT_RETAINED")
        else:
            record["treatment"] = "RETAIN_CURRENT_INITIAL_CONDITION"
        rows.append(record)
    return changes, rows


def national_rows(records):
    result = []
    frame = pd.DataFrame(records)
    for (year,scenario), group in frame.groupby(["year","scenario"]):
        for family, carriers in [(c,(c,)) for c in VRE]+[("WIND",VRE[:2]),("SOLAR",VRE[2:])]:
            g = group.loc[group.family.isin(carriers)]
            cap = float(g.installed_MW.sum())
            row = {"year":int(year),"scenario":scenario,"zone":"NATIONAL","family":family,"installed_MW":cap,
                   "parent_available_MWh":float(g.parent_available_MWh.sum()),
                   "post_scalar_available_MWh":float(g.post_scalar_available_MWh.sum()),
                   "final_available_MWh":float(g.final_available_MWh.sum()),"status":"PASS"}
            for col in ("parent_annual_CF","post_scalar_annual_CF","post_Q2Q_annual_CF","final_annual_CF"):
                row[col] = float(np.dot(g[col],g.installed_MW)/cap)
            row.update(annual_scalar=float(g.annual_scalar.iloc[0]),stage0_cap_count=int(g.stage0_cap_count.sum()),
                       final_cap_count=int(g.final_cap_count.sum()),stage0_max_pre_cap=float(g.stage0_max_pre_cap.max()))
            for attr in ("quarterly_indices","monthly_indices"):
                parsed = [json.loads(value) for value in g[attr]]
                row[attr] = json.dumps({stage: np.average([p[stage] for p in parsed],axis=0,
                    weights=g.installed_MW*g[f'{"post_scalar" if stage=="stage0" else "post_Q2Q" if stage=="q2q" else stage}_annual_CF']).tolist()
                    for stage in ("parent","stage0","q2q","final")})
            row["treatment"] = "CAPACITY_WEIGHTED_AGGREGATE_NO_INDEPENDENT_SCENARIO_CALIBRATION"
            result.append(row)
    return result


def prepare():
    QA.mkdir(parents=True, exist_ok=True)
    controls = verify_v2_frozen_contract()
    sources = source_manifest()
    write_table("SOURCE_EVIDENCE_MANIFEST.csv",sources)
    # Reuse the accepted protection inventory rather than rediscovering W/H/source corpora.
    protection = json.loads((ROOT/"qa/stage_b/p2x_flex_v2/FINAL_V2_EXECUTION_ENABLEMENT_RECEIPT.json").read_text())["protected_hashes"]
    protection = dict(protection)
    for item in sources:
        protection[item["path"]] = item["sha256"]
    for row in controls.to_dict("records"):
        path = ROOT / row["final_v2_path"]
        if sha256_file(path) != row["final_v2_sha256"]:
            raise RuntimeError("FINAL_V2_PARENT_HASH_FAIL")
        protection[row["final_v2_path"]] = row["final_v2_sha256"]
    for name in ("config/final_methodology_execution_v1.yaml","config/final_methodology_execution_v2.yaml"):
        protection[name] = sha256_file(ROOT/name)
    for name,digest in protection.items():
        if sha256_file(ROOT/name if not Path(name).is_absolute() else Path(name)) != digest:
            raise RuntimeError("PROTECTED_PARENT_HASH_FAIL:"+name)
    dump_json(QA/"PARENT_HASHES.json",protection)
    dump_json(QA/"FINAL_PRE_RERUN_SUCCESSOR_GATE.json",{"status":"PREPARING_STATIC_SUCCESSORS",**FLAGS})
    history, annual_history, coverage = historical_indices()
    write_table("HISTORICAL_WIND_SOURCE_COVERAGE.csv",coverage)
    write_table("HISTORICAL_ANNUAL_NORMALIZED_INDICES.csv",annual_history)
    write_table("HISTORICAL_MONTHLY_INDEX_AUTHORITY.csv",history["month"])
    write_table("HISTORICAL_QUARTERLY_INDEX_AUTHORITY.csv",history["quarter"])
    # Independent calculation must reproduce the accepted preview quarter medians.
    preview = pd.read_csv(DOWNLOADS/"MEM_2040_BASE_FINAL_V1_WIND_Q2Q_QUARTERLY_PREVIEW.csv")
    preview["zone"] = preview.zone.replace({"CNORD":"CNOR"})
    comparison = history["quarter"].merge(preview,on=["zone","quarter"],validate="one_to_one")
    if not np.allclose(comparison["median"],comparison.historical_2021_2025_median_index_raw,atol=1e-13,rtol=0):
        raise RuntimeError("HISTORICAL_PREVIEW_MEDIAN_RECONCILIATION_FAIL")
    base_row = controls.loc[controls.year.eq(2040)&controls.scenario.eq("Base")&controls.network_type.eq("UC_INPUT")].iloc[0]
    base = pypsa.Network(ROOT/base_row.final_v2_path)
    factors, scalar_rows = derive_scalars(base)
    write_table("VRE_2040_BASE_NATIONAL_PRODUCTIVITY_BEFORE.csv",scalar_rows)
    for record in scalar_rows:
        family = record["family"]
        write_table(f"{family}_NATIONAL_PRODUCTIVITY_SCALAR.csv",[record])
        (QA/f"{family}_NATIONAL_PRODUCTIVITY_QA.md").write_text(
            f"# {family} national productivity\n\nSOURCE FACT: DDS24 Tables 13 (PDF59) and18 (PDF66), visually verified.\n\n" +
            ("MODELLING DECISION: native solar retained by user delta; DDS24 solar CF is diagnostic only.\n\n" if family=="SOLAR" else
             "MODELLING DECISION: DE-IT2040 wind CF applied to availability; one raw scalar carried to all six cases.\n\n") +
            f"DERIVED RESULT: native CF {record['native_CF']:.15g}; target {record['target_CF']:.15g}; "
            f"bounded scalar {record['bounded_scalar']:.15g}; {record['capped_observations']} capped observations.\n\n"
            "Frozen rounded zonal MW are retained; matching CF does not force the published headline TWh when MEM MW differ.\n"
            "No solved dispatch, price or curtailment enters calibration.\n",encoding="utf-8")
    solved = pypsa.Network(ROOT/"results/final_methodology_v1/2040/Base/UC_MILP/FINAL_V1_UC2_2040_BASE_8760H_UC_MILP_SOLVED.nc")
    write_table("FINAL_V1_VS_FINAL_V2_VRE_EQUALITY_QA.csv",preview_equality(base,solved))
    del solved
    smoke = pypsa.Network(ROOT/"results/UC1_2040_BASE_168H/UC1_2040_BASE_168H_UC_MILP_SOLVED.nc")
    if sha256_file(ROOT/"results/UC1_2040_BASE_168H/UC1_2040_BASE_168H_UC_MILP_SOLVED.nc") != "758f78b3b5929ac23caec302f74fadc145d97ee67bdaba37f2931100d8ac26ee":
        raise RuntimeError("SMOKE_HASH_FAIL")
    accumulated = {k:[] for k in ("vre","quarter","month","boundary","historical_month","historical_quarter","monthly_qa")}
    parents, lineage, diffs, uc_rows, interface_rows = [], [], [], [], []
    NETWORKS.mkdir(parents=True,exist_ok=True)
    initial_by_case = {}; profiles_by_case = {}
    for row in controls.sort_values(["year","scenario","network_type"],ascending=[True,True,False]).to_dict("records"):
        year, scenario, kind = row["year"], row["scenario"], row["network_type"]
        parent = pypsa.Network(ROOT/row["final_v2_path"])
        structural_check(parent,year,scenario,continuous=kind=="CONTINUOUS_REFERENCE")
        init = {}
        if kind == "UC_INPUT":
            init, units = smoke_initialization(parent,smoke,year,scenario)
            initial_by_case[(year,scenario)] = init
            uc_rows.extend(units)
            parents.append({"year":year,"scenario":scenario,"path":row["final_v2_path"],"sha256":row["final_v2_sha256"],
                "snapshots":len(parent.snapshots),"buses":len(parent.buses),"links":len(parent.links),
                "generators":len(parent.generators),"carrier_counts":json.dumps(parent.generators.carrier.value_counts().to_dict()),
                "UC_fields_and_ids":parent.generators.loc[parent.generators.committable].to_json(orient="index"),
                "interface_attributes":parent.links.to_json(orient="index")})
            for name,link in parent.links.loc[parent.links.carrier.isin(["internal_transfer","external_trade","corsica_hub"])].iterrows():
                interface_rows.append({"year":year,"scenario":scenario,"link_id":name,"bus0":link.bus0,"bus1":link.bus1,
                    "carrier":link.carrier,"p_nom_MW":link.p_nom,"p_min_pu":link.p_min_pu,"p_max_pu":link.p_max_pu,
                    "efficiency":link.efficiency,"marginal_cost":link.marginal_cost,"conclusion":"RETAIN_LOSSLESS_ZERO_TOLL",
                    "explicit_specific_loss_accounting_exception_proven":False,"generic_toll_authority_found":False})
        n, metrics = transform(parent,row,factors,history,init)
        exact_diff(parent,n,init)
        structural_check(n,year,scenario,continuous=kind=="CONTINUOUS_REFERENCE")
        suffix = "_CONTINUOUS_REFERENCE" if kind=="CONTINUOUS_REFERENCE" else ""
        path = NETWORKS/f"MEM_{year}_{scenario.upper()}_FINAL_METHODOLOGY_V3{suffix}_8760h_UNSOLVED.nc"
        if not path.exists():
            n.export_to_netcdf(path)
        reload = pypsa.Network(path)
        diff = exact_diff(parent,reload,init)
        # Authorized values themselves must also equal this deterministic transformation.
        pd.testing.assert_frame_equal(n.generators_t.p_max_pu,reload.generators_t.p_max_pu,check_exact=True,check_dtype=False,check_freq=False)
        structural_check(reload,year,scenario,continuous=kind=="CONTINUOUS_REFERENCE")
        if kind=="UC_INPUT":
            profiles_by_case[(year,scenario)] = reload.generators_t.p_max_pu.loc[:,reload.generators.index[reload.generators.carrier.isin(VRE)]].copy()
            for key,values in metrics.items(): accumulated[key].extend(values)
        else:
            pd.testing.assert_frame_equal(profiles_by_case[(year,scenario)],reload.generators_t.p_max_pu.loc[:,reload.generators.index[reload.generators.carrier.isin(VRE)]],check_exact=True,check_dtype=False,check_freq=False)
        diffs.extend([{ "year":year,"scenario":scenario,"network_type":kind,**d} for d in diff])
        lineage.append({"year":year,"scenario":scenario,"network_type":kind,"parent_path":row["final_v2_path"],
            "parent_sha256":row["final_v2_sha256"],"path":relative(path),"sha256":sha256_file(path),"status":"PASS",
            "initialization_units_changed":len(init),"unexpected_changed_model_fields":0})
        dump_json(QA/"FINAL_PRE_RERUN_SUCCESSOR_GATE.json",{"status":"STATIC_EXPORT_RELOAD_IN_PROGRESS",
            "completed_networks":lineage,"factors":factors,**FLAGS})
        print(f"{year} {scenario} {kind}: export/reload exact-diff PASS",flush=True)
    for record in parents:
        g=[r for r in accumulated["vre"] if (r["year"],r["scenario"])==(record["year"],record["scenario"])]
        record["VRE_MW_and_availability"] = json.dumps(g)
    write_table("PARENT_MANIFEST.csv",parents)
    write_table("FINAL_SUCCESSOR_NETWORK_LINEAGE_AND_HASHES.csv",lineage)
    write_table("FINAL_V2_TO_SUCCESSOR_EXACT_DIFF.csv",diffs)
    write_table("UC_SMOKE_COMPATIBILITY_MATRIX.csv",uc_rows)
    applied = [r for r in uc_rows if r["hard_compatible"]]
    write_table("UC_INITIALIZATION_APPLIED_MAPPING.csv",applied)
    # Signed transport carrier names are pinned by the existing network contract, not guessed.
    if not interface_rows:
        interface_rows = [{"year":p["year"],"scenario":p["scenario"],"conclusion":"RETAIN_LOSSLESS_ZERO_TOLL",
                           "authority":"SIGNED_INTERFACE_ELIGIBILITY_AND_EQUIVALENCE.csv"} for p in parents]
    write_table("INTERCONNECTOR_LOSS_TOLL_MATRIX.csv",interface_rows)
    write_table("ONSHORE_Q2Q_FINAL_FACTORS.csv",accumulated["quarter"])
    write_table("ONSHORE_Q2Q_BEFORE_AFTER.csv",accumulated["quarter"])
    write_table("ONSHORE_MONTHLY_WITHIN_QUARTER_FACTORS.csv",accumulated["month"])
    write_table("ONSHORE_MONTHLY_FINAL_PROFILE_QA.csv",accumulated["monthly_qa"])
    write_table("ONSHORE_FINAL_VS_HISTORICAL_MONTHLY.csv",accumulated["historical_month"])
    write_table("ONSHORE_FINAL_VS_HISTORICAL_QUARTERLY.csv",accumulated["historical_quarter"])
    write_table("WIND_TEMPORAL_BOUNDARY_RAMP_QA.csv",accumulated["boundary"])
    all_vre = accumulated["vre"]+national_rows(accumulated["vre"])
    write_table("FINAL_VRE_CALIBRATION_QA.csv",all_vre)
    write_table("WIND_PRODUCTIVITY_AFTER_BY_CASE_ZONE_FAMILY.csv",[r for r in all_vre if r["family"].startswith("wind_") or r["family"]=="WIND"])
    write_table("OFFSHORE_FINAL_TREATMENT.csv",[r for r in accumulated["vre"] if r["family"]=="wind_offshore"])
    dump_json(QA/"FINAL_SUCCESSOR_INVARIANTS.json",{"status":"PASS","networks_export_reload":12,"UC_cases":6,
        "continuous_reference_cases":6,"unexpected_changed_model_fields":0,"UC_reference_VRE_exact":True,
        "national_capacity_unchanged":True,"zonal_capacity_unchanged":True,"siting_unchanged":True,
        "P2X_exact":True,"hydro_exact":True,"BESS_PHS_exact":True,"network_exact":True,
        "solar_profiles_exact_preserved_user_delta":True,"annual_onshore_energy_preserved_after_stage0":True,
        "monthly_transform_preserves_quarter_energy":True,"within_month_order_and_resource_zeros_preserved":True,
        "initialization_compatible_units":len(applied),"non_P2X_model_objects_exhaustively_compared":True,**FLAGS})
    verification = verify()
    dump_json(QA/"FINAL_PRE_RERUN_SUCCESSOR_GATE.json",{**verification,"status":"STATIC_NETWORK_PREPARATION_PASS_PENDING_FOCUSED_TESTS",
        "factors":factors,"networks":lineage,"initialization_units_changed":len(applied),**FLAGS})


def verify():
    protection = json.loads((QA/"PARENT_HASHES.json").read_text())
    for name,digest in protection.items():
        if sha256_file(ROOT/name if not Path(name).is_absolute() else Path(name)) != digest:
            raise RuntimeError("PROTECTED_STATE_MODIFIED:"+name)
    lineage = pd.read_csv(QA/"FINAL_SUCCESSOR_NETWORK_LINEAGE_AND_HASHES.csv")
    if len(lineage)!=12 or lineage.duplicated(["year","scenario","network_type"]).any() or not lineage.path.is_unique:
        raise RuntimeError("SUCCESSOR_CASE_ISOLATION_FAIL")
    mapping = pd.read_csv(QA/"UC_INITIALIZATION_APPLIED_MAPPING.csv")
    for r in lineage.to_dict("records"):
        if sha256_file(ROOT/r["path"]) != r["sha256"] or sha256_file(ROOT/r["parent_path"]) != r["parent_sha256"]:
            raise RuntimeError("SUCCESSOR_OR_PARENT_HASH_FAIL")
        n = pypsa.Network(ROOT/r["path"]); parent = pypsa.Network(ROOT/r["parent_path"])
        m = mapping.loc[mapping.year.eq(r["year"])&mapping.scenario.eq(r["scenario"])] if r["network_type"]=="UC_INPUT" else mapping.iloc[0:0]
        init={row["unit_id"]:{"up_time_before":int(row["new_up_time_before"]),"down_time_before":int(row["new_down_time_before"])} for row in m.to_dict("records")}
        exact_diff(parent,n,init)
        structural_check(n,r["year"],r["scenario"],continuous=r["network_type"]=="CONTINUOUS_REFERENCE")
    monthly=pd.read_csv(QA/"ONSHORE_MONTHLY_WITHIN_QUARTER_FACTORS.csv")
    quarter=pd.read_csv(QA/"ONSHORE_Q2Q_FINAL_FACTORS.csv")
    if not np.allclose(monthly.after_MWh,monthly.target_MWh,rtol=2e-14,atol=1e-6) or not np.allclose(quarter.after_MWh,quarter.target_MWh,rtol=2e-14,atol=1e-6):
        raise RuntimeError("SUCCESSOR_TEMPORAL_ENERGY_QA_FAIL")
    vre=pd.read_csv(QA/"FINAL_VRE_CALIBRATION_QA.csv")
    on=vre.loc[vre.family.eq("wind_onshore")]
    if not np.allclose(on.final_available_MWh,on.post_scalar_available_MWh,rtol=2e-14,atol=1e-6):
        raise RuntimeError("SUCCESSOR_ANNUAL_WIND_QA_FAIL")
    for family in ("WIND",):
        national=vre.loc[vre.year.eq(2040)&vre.scenario.eq("Base")&vre.zone.eq("NATIONAL")&vre.family.eq(family)].iloc[0]
        if not np.isclose(national.final_annual_CF,TARGETS[family],rtol=0,atol=1e-13):
            raise RuntimeError("SUCCESSOR_NATIONAL_TARGET_FAIL")
    solar=vre.loc[vre.family.isin(VRE[2:])|vre.family.eq("SOLAR")]
    if not np.array_equal(solar.final_available_MWh.to_numpy(),solar.parent_available_MWh.to_numpy()):
        raise RuntimeError("SUCCESSOR_NATIVE_SOLAR_NOT_PRESERVED")
    if (ROOT/"results/final_methodology_v3").exists():
        raise RuntimeError("PRODUCTION_RESULT_PATH_EXISTS_DURING_PREPARATION")
    return {"status":"STATIC_PRESERVATION_VERIFICATION_PASS","network_export_reload_count":12,
            "protected_artifacts_checked":len(protection),"unexpected_changed_model_fields":0,**FLAGS}


def retain_native_solar():
    """Apply the user delta only to this task's unaccepted successors; reuse all wind QA."""
    lineage=pd.read_csv(QA/"FINAL_SUCCESSOR_NETWORK_LINEAGE_AND_HASHES.csv")
    metrics=pd.read_csv(QA/"FINAL_VRE_CALIBRATION_QA.csv",float_precision="round_trip")
    metrics=metrics.loc[metrics.zone.ne("NATIONAL")].copy()
    mapping=pd.read_csv(QA/"UC_INITIALIZATION_APPLIED_MAPPING.csv")
    diffs=[]
    gate_path=QA/"FINAL_PRE_RERUN_SUCCESSOR_GATE.json"
    gate=json.loads(gate_path.read_text())
    gate.update(status="RETAINING_NATIVE_SOLAR_PER_USER_DELTA",solar_adjustment_enabled=False)
    dump_json(gate_path,gate)
    for index,row in lineage.iterrows():
        path=ROOT/row.path
        if sha256_file(path)!=row.sha256 or sha256_file(ROOT/row.parent_path)!=row.parent_sha256:
            raise RuntimeError("SPARSE_SOLAR_PARENT_OR_SUCCESSOR_HASH_FAIL")
        parent=pypsa.Network(ROOT/row.parent_path)
        child=pypsa.Network(path)
        wind=child.generators_t.p_max_pu[parent.generators.index[parent.generators.carrier.isin(VRE[:2])]].copy()
        solar_ids=parent.generators.index[parent.generators.carrier.isin(VRE[2:])]
        child.generators_t.p_max_pu.loc[:,solar_ids]=parent.generators_t.p_max_pu[solar_ids]
        child.meta[META]["solar_scalar"]=1.0
        child.meta[META]["solar_policy"]="RETAIN_NATIVE_SOLAR_USER_DELTA"
        selected=mapping.loc[mapping.year.eq(row.year)&mapping.scenario.eq(row.scenario)] if row.network_type=="UC_INPUT" else mapping.iloc[0:0]
        init={r.unit_id:{"up_time_before":int(r.new_up_time_before),"down_time_before":int(r.new_down_time_before)} for r in selected.itertuples()}
        exact_diff(parent,child,init)
        child.export_to_netcdf(path)
        loaded=pypsa.Network(path)
        pd.testing.assert_frame_equal(wind,loaded.generators_t.p_max_pu[wind.columns],check_exact=True,check_dtype=False,check_freq=False)
        pd.testing.assert_frame_equal(parent.generators_t.p_max_pu[solar_ids],loaded.generators_t.p_max_pu[solar_ids],check_exact=True,check_dtype=False,check_freq=False)
        diff=exact_diff(parent,loaded,init)
        structural_check(loaded,row.year,row.scenario,continuous=row.network_type=="CONTINUOUS_REFERENCE")
        diffs.extend([{"year":row.year,"scenario":row.scenario,"network_type":row.network_type,**d} for d in diff])
        lineage.at[index,"sha256"]=sha256_file(path)
        if row.network_type=="UC_INPUT":
            for name in solar_ids:
                gen=parent.generators.loc[name]
                profile=parent.generators_t.p_max_pu[name].to_numpy()
                selector=metrics.year.eq(row.year)&metrics.scenario.eq(row.scenario)&metrics.generator_id.eq(name)
                position=metrics.index[selector][0]
                for field in ("post_scalar_annual_CF","post_Q2Q_annual_CF","final_annual_CF"):
                    metrics.at[position,field]=metrics.at[position,"parent_annual_CF"]
                for field in ("post_scalar_available_MWh","final_available_MWh"):
                    metrics.at[position,field]=metrics.at[position,"parent_available_MWh"]
                metrics.at[position,"annual_scalar"]=1.0
                metrics.at[position,"stage0_cap_count"]=0
                metrics.at[position,"final_cap_count"]=int((profile==1).sum())
                metrics.at[position,"stage0_max_pre_cap"]=float(profile.max())
                metrics.at[position,"treatment"]="RETAIN_NATIVE_SOLAR_USER_DELTA"
                for field in ("quarterly_indices","monthly_indices"):
                    indices=json.loads(metrics.at[position,field])
                    metrics.at[position,field]=json.dumps({k:indices["parent"] for k in indices})
        print(f"{row.year} {row.scenario} {row.network_type}: native solar restored; wind/init unchanged; reload exact-diff PASS",flush=True)
    write_table("FINAL_SUCCESSOR_NETWORK_LINEAGE_AND_HASHES.csv",lineage)
    write_table("FINAL_V2_TO_SUCCESSOR_EXACT_DIFF.csv",diffs)
    all_metrics=metrics.to_dict("records")
    write_table("FINAL_VRE_CALIBRATION_QA.csv",all_metrics+national_rows(all_metrics))
    parents=pd.read_csv(QA/"PARENT_MANIFEST.csv")
    for index,row in parents.iterrows():
        parents.at[index,"VRE_MW_and_availability"]=json.dumps([r for r in all_metrics if (r["year"],r["scenario"])==(row.year,row.scenario)])
    write_table("PARENT_MANIFEST.csv",parents)
    scalar=pd.read_csv(QA/"SOLAR_NATIONAL_PRODUCTIVITY_SCALAR.csv",float_precision="round_trip")
    scalar["proposed_bounded_scalar"]=scalar.bounded_scalar
    scalar["proposed_result_CF"]=scalar.result_CF
    scalar["bounded_scalar"]=1.0
    scalar["result_CF"]=scalar.native_CF
    scalar["available_TWh_after"]=scalar.available_TWh_before
    scalar["treatment"]="RETAIN_NATIVE_SOLAR_USER_DELTA"
    scalar["method"]="NO_SOLAR_TRANSFORMATION"
    base=metrics.loc[metrics.year.eq(2040)&metrics.scenario.eq("Base")&metrics.family.isin(VRE[2:])]
    scalar["maximum_pre_cap"]=base.stage0_max_pre_cap.max()
    write_table("SOLAR_NATIONAL_PRODUCTIVITY_SCALAR.csv",scalar)
    wind_scalar=pd.read_csv(QA/"WIND_NATIONAL_PRODUCTIVITY_SCALAR.csv",float_precision="round_trip")
    write_table("VRE_2040_BASE_NATIONAL_PRODUCTIVITY_BEFORE.csv",pd.concat([wind_scalar,scalar],ignore_index=True))
    invariants=json.loads((QA/"FINAL_SUCCESSOR_INVARIANTS.json").read_text())
    invariants.pop("solar_only_national_scalar",None)
    invariants["solar_profiles_exact_preserved_user_delta"]=True
    dump_json(QA/"FINAL_SUCCESSOR_INVARIANTS.json",invariants)
    protection=json.loads((QA/"PARENT_HASHES.json").read_text())
    for name,digest in protection.items():
        if sha256_file(ROOT/name if not Path(name).is_absolute() else Path(name))!=digest:
            raise RuntimeError("PROTECTED_STATE_MODIFIED:"+name)
    gate.update(status="STATIC_NETWORK_PREPARATION_PASS_PENDING_FOCUSED_TESTS",
        factors={"WIND":gate["factors"]["WIND"],"SOLAR":1.0},
        networks=lineage.to_dict("records"),solar_profiles_exact_preserved=True,
        unexpected_changed_model_fields=0,**FLAGS)
    dump_json(gate_path,gate)


def refresh_boundary_diagnostics():
    """Refresh presentation from saved ramps only; no network/profile regeneration."""
    table = pd.read_csv(QA/"WIND_TEMPORAL_BOUNDARY_RAMP_QA.csv",float_precision="round_trip")
    table["absolute_ramp_increment_vs_stage0"] = table.final_ramp_pu.abs()-table.stage0_ramp_pu.abs()
    inherited = table.cyclic_december_january & (table.stage0_ramp_pu.abs()>table.final_interior_max_abs_ramp_pu+1e-12)
    table["inherited_non_chronological_year_wrap"] = inherited
    table["adjustment"] = np.where(~table.outside_full_interior_ramp_range,
        "NONE_WITHIN_INTRAMONTH_RAMP_RANGE",np.where(inherited,
        "NO_NEW_TREATMENT_INHERITED_YEAR_WRAP_RETAINED","REVIEW_REQUIRED"))
    write_table("WIND_TEMPORAL_BOUNDARY_RAMP_QA.csv",table)
    return table


def close_static(test_report):
    """Close verified static preparation; preflights never persist production paths."""
    from xml.etree import ElementTree
    from . import final_methodology_networks as final, stage_b_uc2 as uc2
    with no_models_or_solves():
        tree=ElementTree.parse(test_report)
        suites=[node for node in tree.getroot().iter("testsuite")]
        count=sum(int(node.get("tests",0)) for node in suites)
        failures=sum(int(node.get("failures",0))+int(node.get("errors",0))+int(node.get("skipped",0)) for node in suites)
        if not count or failures:
            raise RuntimeError("STATIC_FOCUSED_TESTS_NOT_PASS")
        gate_path=QA/"FINAL_PRE_RERUN_SUCCESSOR_GATE.json"
        gate=json.loads(gate_path.read_text())
        if gate["status"] not in ("STATIC_NETWORK_PREPARATION_PASS_PENDING_FOCUSED_TESTS","FINAL_V3_STATIC_QA_PASS") or gate["network_export_reload_count"]!=12:
            raise RuntimeError("STATIC_NETWORK_PREPARATION_NOT_COMPLETE")
        boundaries=refresh_boundary_diagnostics()
        reviewed=boundaries.loc[boundaries.adjustment.eq("REVIEW_REQUIRED")]
        expected={(2050,"High","CALA","wind_onshore","2019-08-01 00:00:00")}
        observed=set(reviewed[["year","scenario","zone","family","snapshot"]].itertuples(index=False,name=None))
        if observed!=expected:
            raise RuntimeError("BOUNDARY_REVIEW_EVIDENCE_CHANGED")
        row=reviewed.iloc[0]
        boundary_review={"newly_outside_intramonth_range":len(reviewed),
            "case":"2050 High CALA onshore, August 1",
            "final_abs_ramp_pu":abs(float(row.final_ramp_pu)),
            "stage0_abs_ramp_pu":abs(float(row.stage0_ramp_pu)),
            "intramonth_max_abs_ramp_pu":float(row.final_interior_max_abs_ramp_pu),
            "excess_over_intramonth_max_pu":abs(float(row.final_ramp_pu))-float(row.final_interior_max_abs_ramp_pu),
            "decision":"RETAIN_WITH_EXPLICIT_ARTIFICIAL_MONTH_BOUNDARY_DIAGNOSTIC",
            "rationale":"Mechanical monthly step disclosed; 0.5502% above the full intramonth maximum is not a clearly exceptional extreme warranting smoothing. No new numerical threshold is adopted.",
            "smoothing_applied":False,
            "inherited_year_wrap_outliers":int((boundaries.outside_full_interior_ramp_range & boundaries.inherited_non_chronological_year_wrap).sum())}
        write_table("VRE_2040_BASE_NATIONAL_PRODUCTIVITY_BEFORE.csv",pd.concat([
            pd.read_csv(QA/f"{family}_NATIONAL_PRODUCTIVITY_SCALAR.csv",float_precision="round_trip")
            for family in ("WIND","SOLAR")],ignore_index=True))
        protection=json.loads((QA/"PARENT_HASHES.json").read_text())
        for name,digest in protection.items():
            path=ROOT/name if not Path(name).is_absolute() else Path(name)
            if sha256_file(path)!=digest:raise RuntimeError("PROTECTED_STATE_MODIFIED:"+name)
        gate.update(status="FINAL_V3_STATIC_QA_PASS",focused_tests_passed=count,
            focused_tests_failed=0,focused_tests_xml=relative(test_report),
            focused_tests_xml_sha256=sha256_file(test_report),boundary_review=boundary_review,
            production_family_canonical=False,review_sequence=["2050 Base","review","2040 Base","review","remaining four"],**FLAGS)
        dump_json(gate_path,gate)
        preflights=[]; jobs=[]
        try:
            for year,scenario in SCENARIOS:
                with uc2.execution_variant("final_v3"):
                    check=uc2.preflight(year,scenario,persist=False)
                    preflights.append(check)
                    for kind in uc2.RUN_TYPES:
                        paths=uc2.job_paths(year,scenario,kind)
                        jobs.append({"year":year,"scenario":scenario,"run_type":kind,
                            "job_id":uc2.job_id(year,scenario,kind),
                            **{key:relative(value) for key,value in paths.items()}})
            if len({r["solved"] for r in jobs})!=18 or (ROOT/"results/final_methodology_v3").exists():
                raise RuntimeError("STATIC_JOB_ISOLATION_OR_PRODUCTION_PATH_FAIL")
        except Exception as error:
            gate.update(status="BLOCKED_STATIC_EXECUTION_PREFLIGHT",reason=str(error))
            dump_json(gate_path,gate)
            raise
        write_table("FINAL_V3_STATIC_PREFLIGHTS.csv",preflights)
        write_table("FINAL_V3_MANUAL_JOB_PATHS.csv",jobs)
        lineage=pd.read_csv(QA/"FINAL_SUCCESSOR_NETWORK_LINEAGE_AND_HASHES.csv")
        members=list(QA.glob("*"))
        members += [ROOT/name for name in (
            "src/mem_model/final_pre_rerun.py","src/mem_model/stage_b_uc2.py",
            "src/mem_model/final_methodology_networks.py","config/final_methodology_execution_v3.yaml",
            "tests/test_final_pre_rerun.py","tests/test_final_v3_execution.py")]
        members += [ROOT/path for path in lineage.path]
        excluded={gate_path,QA/"FINAL_PRE_RERUN_ARTIFACT_MANIFEST.csv"}
        write_table("FINAL_PRE_RERUN_ARTIFACT_MANIFEST.csv",[
            {"path":relative(path),"sha256":sha256_file(path),"bytes":path.stat().st_size}
            for path in sorted(set(members)) if path.is_file() and path not in excluded])
        gate.update(static_preflights_passed=6,prepared_manual_jobs=18,
            artifact_manifest=relative(QA/"FINAL_PRE_RERUN_ARTIFACT_MANIFEST.csv"),
            artifact_manifest_sha256=sha256_file(QA/"FINAL_PRE_RERUN_ARTIFACT_MANIFEST.csv"),
            manifest_exclusions="manifest itself and enclosing gate (avoids circular hashes)",
            transfer=relative(QA/"FINAL_PRE_RERUN_SUCCESSOR_TRANSFER.md"))
        dump_json(gate_path,gate)
        return gate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify", "retain-solar"))
    args = parser.parse_args(argv)
    with no_models_or_solves():
        if args.action == "prepare":
            prepare()
        elif args.action == "retain-solar":
            retain_native_solar()
        else:
            verify()


if __name__ == "__main__":
    main()

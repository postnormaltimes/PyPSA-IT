"""Deterministic relative plant classes; no optimization or random sampling."""
from __future__ import annotations

import math
import numpy as np
import pandas as pd

SIZE_CLASSES = ("SZ_SMALL", "SZ_MEDIUM", "SZ_LARGE")
EFFICIENCY_CLASSES = ("ETA_LOW", "ETA_MID", "ETA_HIGH")
EFFICIENCY_MULTIPLIERS = np.array([0.90, 1.00, 1.05])
EFFICIENCY_CAPACITY_SHARES = np.array([0.15, 0.50, 0.35])
CELL_ORDER = tuple((eta, size) for eta in EFFICIENCY_CLASSES for size in SIZE_CLASSES)


def terciles(values: pd.Series) -> tuple[pd.Series, tuple[float, float]]:
    """Value-based linear quantiles; ties stay together in the lower class."""
    data = values.astype(float)
    if data.empty or not np.isfinite(data).all():
        raise ValueError("V2A_INVALID_REFERENCE_VALUES")
    boundaries = tuple(float(x) for x in np.quantile(data, [1 / 3, 2 / 3], method="linear"))
    classes = pd.Series(np.searchsorted(boundaries, data, side="left"), index=data.index)
    return classes, boundaries


def largest_remainder(count: int, shares: np.ndarray) -> np.ndarray:
    """Input class order breaks equal remainders; no banker's rounding."""
    shares = np.asarray(shares, dtype=float)
    if count < 1 or shares.ndim != 1 or len(shares) == 0 or not np.isfinite(shares).all() or (shares < 0).any() or shares.sum() <= 0:
        raise ValueError("V2A_INVALID_ALLOCATION")
    raw = count * shares / shares.sum()
    result = np.floor(raw).astype(int)
    ranking = sorted(range(len(shares)), key=lambda cell: (-float(raw[cell] - result[cell]), cell))
    for cell in ranking[:count - int(result.sum())]:
        result[cell] += 1
    if int(result.sum()) != count:
        raise AssertionError("V2A_UNIT_COUNT_DRIFT")
    return result


def _usable(values: pd.Series, minimum: int) -> bool:
    if len(values) < minimum or values.round(12).nunique() < 3:
        return False
    labels, _ = terciles(values)
    return set(labels) == {0, 1, 2}


def reference_classes(data: pd.DataFrame, technology: str, minimum: int = 12) -> dict:
    """Capacity-only terciles. Historical efficiencies never parameterize UC."""
    required = {"plant_id", "country", "technology", "capacity_MW", "capacity_observed"}
    if not required.issubset(data.columns) or data.plant_id.duplicated().any():
        raise ValueError("V2A_REFERENCE_SCHEMA_OR_DUPLICATE")
    pool = data.loc[data.technology.eq(technology)].sort_values("plant_id").copy()
    cap = pd.to_numeric(pool.capacity_MW, errors="coerce")
    pool["capacity_MW"] = cap
    cap_ok = pool.capacity_observed.eq(True) & cap.gt(0) & np.isfinite(cap)
    diagnostics = []
    for geography, mask in (("ITALY", pool.country.eq("Italy")), ("EUROPE", pd.Series(True, index=pool.index))):
        capacities = pool.loc[mask & cap_ok]
        diagnostics.append({"geography": geography, "capacity_rows": len(capacities),
                            "distinct_capacities": capacities.capacity_MW.round(12).nunique()})
        if _usable(capacities.capacity_MW, minimum):
            labels, boundaries = terciles(capacities.capacity_MW)
            representatives = [float(capacities.loc[labels.eq(i), "capacity_MW"].median()) for i in range(3)]
            denominator = float(capacities.capacity_MW.mean())
            return {"mode": "INDEPENDENT_SIZE_CONTROLLED_EFFICIENCY", "reason": "MEM_CALIBRATION_DELTA",
                    "size_geography": geography, "capacity_rows": len(capacities), "diagnostics": diagnostics,
                    "size_boundaries_MW": boundaries, "size_representatives_MW": representatives,
                    "size_denominator_unit_mean_MW": denominator,
                    "size_multipliers": np.array(representatives) / denominator,
                    "size_shares": np.array([labels.eq(i).mean() for i in range(3)]),
                    "efficiency_provenance": "CONTROLLED_MEM_RELATIVE_EFFICIENCY_ASSUMPTION",
                    "empirical_efficiency_used": False}
    return {"technology": technology, "mode": "HOMOGENEOUS_FALLBACK",
            "reason": "HETEROGENEITY_NOT_APPLIED_INSUFFICIENT_SIZE_REFERENCE_SAMPLE",
            "diagnostics": diagnostics}


def efficiency_unit_counts(count: int) -> np.ndarray:
    """Largest remainder with one child per class when N >= 3.

    MW shares are enforced separately. Repair an empty class by transferring
    one child from the most overallocated eligible donor, with fixed-order ties.
    """
    if count < 1:
        raise ValueError("V2A_INVALID_ALLOCATION")
    if count < 3:
        return np.array([0, count, 0])
    allocation = largest_remainder(count, EFFICIENCY_CAPACITY_SHARES)
    quotas = count * EFFICIENCY_CAPACITY_SHARES
    for recipient in np.flatnonzero(allocation == 0):
        donors = [index for index in range(3) if allocation[index] > 1]
        donor = min(donors, key=lambda index: (-(allocation[index] - quotas[index]), index))
        allocation[donor] -= 1
        allocation[recipient] += 1
    if (allocation < 1).any() or allocation.sum() != count:
        raise AssertionError("V2A_EFFICIENCY_UNIT_ALLOCATION_FAIL")
    return allocation


def normalize_children(parent_mw: float, parent_efficiency: float, count: int, model: dict) -> pd.DataFrame:
    if not math.isfinite(parent_mw) or parent_mw <= 0 or not 0 < parent_efficiency <= 1:
        raise ValueError("V2A_INVALID_PARENT_CAPACITY_OR_EFFICIENCY")
    allocation = efficiency_unit_counts(count)
    fallback = count < 3
    capacity_shares = np.array([0., 1., 0.]) if fallback else EFFICIENCY_CAPACITY_SHARES
    rows = []
    for eta_index, group_count in enumerate(allocation):
        if not group_count:
            continue
        efficiency = parent_efficiency * EFFICIENCY_MULTIPLIERS[eta_index]
        if not 0 < efficiency <= 1:
            raise ValueError("V2A_INVALID_CHILD_EFFICIENCY")
        envelope = parent_mw * capacity_shares[eta_index]
        size_counts = largest_remainder(int(group_count), model["size_shares"])
        size_indexes = [index for index, number in enumerate(size_counts) for _ in range(int(number))]
        size = np.array([model["size_multipliers"][index] for index in size_indexes])
        proposed = parent_mw / count * size
        alpha = envelope / proposed.sum()
        for size_index, multiplier, power in zip(size_indexes, size, alpha * proposed):
            rows.append({"cell": eta_index * 3 + size_index, "size_class": SIZE_CLASSES[size_index],
                         "efficiency_class": EFFICIENCY_CLASSES[eta_index], "p_nom": power,
                         "efficiency": efficiency, "heat_rate": 1 / efficiency,
                         "alpha_P": alpha, "beta_HR": 1., "size_multiplier": multiplier,
                         "efficiency_multiplier": EFFICIENCY_MULTIPLIERS[eta_index],
                         "efficiency_group_capacity_share": capacity_shares[eta_index],
                         "allocation_mode": "MID_ONLY_SMALL_COHORT" if fallback else "EXACT_15_50_35_MW_ENVELOPES"})
    children = pd.DataFrame(rows)
    power = children.p_nom.to_numpy()
    hr = children.heat_rate.to_numpy()
    if not math.isclose(power.sum(), parent_mw, rel_tol=0, abs_tol=1e-7):
        raise AssertionError("V2A_CAPACITY_DRIFT")
    if not math.isclose(np.average(hr, weights=power), 1 / parent_efficiency, rel_tol=0, abs_tol=1e-12):
        raise AssertionError("V2A_HEAT_RATE_DRIFT")
    return children


def heat_rate_cost(parent_mc, parent_hr: float, child_hr: float, coefficient):
    """Apply the frozen MEM fuel/chargeable-carbon coefficient once.

    Series inputs retain time dependence. Recovering that coefficient from
    an unexplained time-varying total cost is deliberately not attempted.
    """
    if isinstance(parent_mc, pd.Series) and isinstance(coefficient, pd.Series):
        if not parent_mc.index.equals(coefficient.index):
            raise ValueError("V2A_TIME_COST_ALIGNMENT_FAIL")
    result = parent_mc + coefficient * (child_hr - parent_hr)
    if not np.isfinite(np.asarray(result)).all():
        raise ValueError("V2A_NONFINITE_MARGINAL_COST")
    return result

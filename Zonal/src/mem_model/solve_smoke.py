from __future__ import annotations

import argparse
import numpy as np
import pandas as pd

from .common import ACCEPTED_RUNTIME, SMOKE_RESULTS, dump_json, ensure_output_dirs
from .network import build_network
from .validation import validate_network


def _select_window(network, hours: int, mode: str) -> pd.DatetimeIndex:
    load = network.loads_t.p_set.filter(regex=r"^LOAD_").sum(axis=1)
    vre_names = network.generators.index[network.generators["carrier"].isin(["solar_pv_rooftop", "solar_pv_utility", "wind_onshore", "wind_offshore"])]
    vre = network.generators_t.p_max_pu.reindex(columns=vre_names, fill_value=1.0).mul(network.generators.loc[vre_names, "p_nom"], axis=1).sum(axis=1)
    residual = load - vre
    score = residual.rolling(hours, min_periods=hours).sum()
    end = score.idxmax() if mode == "peak-residual" else score.idxmin()
    end_position = network.snapshots.get_loc(end)
    start_position = end_position - hours + 1
    return network.snapshots[start_position : end_position + 1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a short structural MEM optimization only")
    parser.add_argument("--year", type=int, required=True, choices=[2040])
    parser.add_argument("--scenario", default="Base", choices=["Base"])
    parser.add_argument("--hours", type=int, required=True, choices=[24, 168])
    parser.add_argument("--window", required=True, choices=["high-vre-surplus", "peak-residual"])
    parser.add_argument("--execute-smoke", action="store_true", help="Required acknowledgement that this invokes HiGHS")
    args = parser.parse_args()
    if not args.execute_smoke:
        raise SystemExit("Smoke solve not executed: pass --execute-smoke after the approval gates pass")
    raise SystemExit("STAGE_B_SMOKE_SOLVE=NOT_EXECUTED: smoke optimization is prohibited for STAGE-B-2040-RUNTIME-1")
    network, metadata = build_network(ACCEPTED_RUNTIME.resolve(), args.year, args.scenario)
    selected = _select_window(network, args.hours, args.window)
    network.set_snapshots(selected)
    pre = validate_network(network, args.year, args.scenario, fixture=True, assemble_model=True)
    if pre["status"].eq("FAIL").any():
        raise RuntimeError(pre.loc[pre["status"].eq("FAIL")].to_string(index=False))
    status, condition = network.optimize(solver_name="highs")
    ensure_output_dirs()
    SMOKE_RESULTS.mkdir(parents=True, exist_ok=True)
    stem = f"MEM_{args.year}_{args.scenario.upper()}_{args.hours}h_{args.window.replace('-', '_')}_STRUCTURAL_ONLY"
    network.export_to_netcdf(SMOKE_RESULTS / f"{stem}.nc")
    shedding = network.generators_t.p.filter(regex=r"^LOAD_SHEDDING_").sum().sum()
    dump_json(
        SMOKE_RESULTS / f"{stem}.json",
        {
            **metadata,
            "status": str(status),
            "termination_condition": str(condition),
            "hours": args.hours,
            "window": args.window,
            "load_shedding_MWh": float(shedding),
            "interpretation": "STRUCTURAL_DIAGNOSTIC_ONLY_NOT_ANNUAL_MEM_RESULT",
        },
    )
    print(status, condition)


if __name__ == "__main__":
    main()

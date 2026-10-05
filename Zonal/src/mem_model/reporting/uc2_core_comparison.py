"""Portable UC2 comparison tables; the accepted metrics need no VIS toolkit.

The analytical transformations are retained from uc2_comparison. This module
reads only reporting tables, verifies their receipts, and never opens networks.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..common import ROOT, ZONES, dump_json, sha256_file
from .canonical_results import reporting_config
from .portable_paths import portable_name, verify_pins


def _validate_sources(year, case_reports, receipt_name):
    if len(case_reports) < 2:
        raise ValueError("UC2 comparisons require at least two completed scenarios")
    metadata, hashes = [], {}
    for scenario, report in case_reports.items():
        report = Path(report)
        path = report / receipt_name
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if not (receipt.get("status") == "PASS" and receipt.get("model_generation") == "UC2"
                and receipt.get("year") == year and receipt.get("scenario") == scenario
                and receipt.get("physical_source") == "UC_MILP"
                and receipt.get("price_source") == "FIXED_COMMITMENT_PRICE_LP"
                and receipt.get("reporting_solver_invocations") == 0):
            raise ValueError(f"Incomparable or unverified UC2 report: {scenario}")
        for name, digest in receipt.get("artifact_hashes", {}).items():
            if sha256_file(report / name) != digest:
                raise RuntimeError(f"UC2_COMPARISON_REPORT_HASH_CONFLICT: {name}")
        verify_pins(receipt.get("protected_source_hashes", {}))
        metadata.append(dict(scenario_id=scenario, display_name=scenario,
                             year=year, model_generation="UC2"))
        hashes[scenario] = sha256_file(path)
    return pd.DataFrame(metadata), hashes


def build_comparison_tables(year, case_reports, *, receipt_name="MEM_UC2_CORE_REPORTING_RECEIPT.json"):
    metadata, receipt_hashes = _validate_sources(year, case_reports, receipt_name)
    # Accepted market display identity; independent of external presentation config.
    aliases = {zone: ("CNORD" if zone == "CNOR" else zone)
               for zone in ZONES}
    metrics, monthly_frames, duration_frames, interface_frames = [], [], [], []
    national_monthly_frames, distinct_price_frames = [], []
    spread_frames, price_frames, source_rows = [], [], []

    def read(scenario, directory, name):
        path = directory / name
        table = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
        source_rows.append(dict(scenario=scenario, source=portable_name(path), sha256=sha256_file(path)))
        return table

    def add(scenario, metric, value, unit, **dimensions):
        price_metric = metric.startswith("price_") or "price" in metric or "spatial_spread" in metric
        metrics.append(dict(scenario_id=scenario, metric=metric, value=float(value), unit=unit,
                            source_authority="FIXED_COMMITMENT_PRICE_LP_DUALS" if price_metric else "UC_MILP",
                            **dimensions))

    for scenario, raw_report in case_reports.items():
        report = Path(raw_report)
        stats = report / "CANONICAL/statistics"
        annual = read(scenario, stats, "uc2_price_annual_summary.csv")
        italian = annual.loc[annual.market.isin(aliases)].copy()
        if len(italian) != len(aliases) or italian.market.duplicated().any():
            raise ValueError(f"Seven unique Italian price zones required: {scenario}")
        for row in italian.itertuples(index=False):
            add(scenario, "annual_load_weighted_zonal_price", row.load_weighted_mean_EUR_per_MWh,
                "EUR/MWh", market=aliases[row.market])
            for metric in ("median", "p5", "p25", "p75", "p95", "min", "max", "std"):
                add(scenario, f"price_{metric}", getattr(row, f"{metric}_EUR_per_MWh"),
                    "EUR/MWh", market=aliases[row.market])
        monthly = read(scenario, stats, "uc2_price_monthly_zonal_means.csv")
        monthly = monthly.loc[monthly.market.isin(aliases)].copy()
        monthly.market = monthly.market.map(aliases)
        monthly.insert(0, "scenario", scenario)
        monthly_frames.append(monthly)
        national_monthly = read(scenario, stats, "uc2_price_monthly_national_load_weighted.csv")
        national_monthly.insert(0, "scenario", scenario)
        national_monthly_frames.append(national_monthly)
        hourly_prices = read(scenario, report, "fixed_commitment_prices_hourly.csv")
        for market in aliases:
            values = pd.to_numeric(hourly_prices[market], errors="raise").to_numpy()
            duration_frames.append(pd.DataFrame(dict(scenario=scenario, market=aliases[market],
                                                     rank_hour=np.arange(1, len(values) + 1),
                                                     price_EUR_per_MWh=np.sort(values)[::-1])))
            price_frames.append(pd.DataFrame(dict(scenario_id=scenario, market=aliases[market],
                                                   metric="zonal_price", value=values, unit="EUR/MWh")))
        spread = read(scenario, stats, "uc2_price_hourly_all_italy_spread.parquet")
        spread = spread.reset_index()
        spread.insert(0, "scenario", scenario)
        spread_frames.append(spread)
        distinct = spread.distinct_zonal_prices.value_counts().sort_index().rename_axis("distinct_zonal_prices").reset_index(name="hours")
        distinct["scenario"] = scenario
        distinct_price_frames.append(distinct)
        add(scenario, "all_italy_mean_spatial_spread", spread.spread_EUR_per_MWh.mean(), "EUR/MWh")
        generation = read(scenario, stats, "annual_primary_generation_national.csv")
        for row in generation.itertuples(index=False):
            add(scenario, "primary_generation", row.annual_primary_generation_TWh, "TWh",
                carrier=row.display_technology)
        curtailment = read(scenario, report, "vre_curtailment.csv")
        for carrier, group in curtailment.groupby("carrier"):
            add(scenario, "vre_curtailment", group.curtailed_MWh.sum() / 1e6, "TWh", carrier=carrier)
        balance = read(scenario, stats, "annual_electrical_balance_by_zone.csv")
        for carrier in ("bess", "phs"):
            for operation in ("charging", "discharge"):
                add(scenario, "storage_activity", balance[f"{carrier}_{operation}_TWh"].sum(), "TWh",
                    carrier=f"{carrier.upper()} {operation}")
        p2x = read(scenario, report, "p2x_annual_by_zone.csv")
        for row in p2x.itertuples(index=False):
            add(scenario, "p2x_electrical_consumption", row.consumption_MWh / 1e6,
                "TWh", market=aliases.get(row.zone, row.zone))
        imports = read(scenario, stats, "uc2_canonical_net_imports_summary.csv")
        for row in imports.itertuples(index=False):
            add(scenario, "net_imports", row.net_import_energy_MWh / 1e6,
                "TWh", market=aliases.get(row.zone, row.zone))
        interfaces = read(scenario, stats, "uc2_net_interface_summary.csv")
        interfaces.insert(0, "scenario", scenario)
        interface_frames.append(interfaces)
        events = read(scenario, report, "commitment_events_by_zone_technology.csv")
        for carrier, group in events.groupby("carrier"):
            add(scenario, "actual_starts", group.actual_starts.sum(), "starts", carrier=carrier)
            add(scenario, "actual_shutdowns", group.actual_shutdowns.sum(), "shutdowns", carrier=carrier)
        online = read(scenario, report, "hourly_online_units_and_committed_MW.csv")
        if "committed_MW" not in online:
            raise ValueError("Canonical UC hourly commitment must contain committed_MW")
        # The current UC2 report contains one national total per snapshot.
        add(scenario, "committed_capacity_hours", online.committed_MW.sum() / 1e6, "million MW h")
        economic_path = report / "economic_comparison.json"
        economics = json.loads(economic_path.read_text(encoding="utf-8"))
        source_rows.append(dict(scenario=scenario, source=portable_name(economic_path), sha256=sha256_file(economic_path)))
        add(scenario, "uc_uplift", economics["uc_uplift_EUR"] / 1e6, "million EUR")
        add(scenario, "canonical_uc_system_objective", economics["uc_milp_objective_EUR"] / 1e9, "billion EUR")

    tables = {
        "scenario_metadata": metadata,
        "comparison_metrics": pd.DataFrame(metrics),
        "monthly_zonal_prices": pd.concat(monthly_frames, ignore_index=True),
        "monthly_national_load_weighted_price": pd.concat(national_monthly_frames, ignore_index=True),
        "price_duration": pd.concat(duration_frames, ignore_index=True),
        "hourly_zonal_prices": pd.concat(price_frames, ignore_index=True),
        "all_italy_spatial_spread": pd.concat(spread_frames, ignore_index=True),
        "distinct_simultaneous_prices": pd.concat(distinct_price_frames, ignore_index=True),
        "net_interface_summary": pd.concat(interface_frames, ignore_index=True),
        "source_provenance": pd.DataFrame(source_rows),
    }
    return tables, receipt_hashes


def build_core_comparisons(year, case_reports, output, *, receipt_name="MEM_UC2_CORE_REPORTING_RECEIPT.json"):
    from .uc2_postprocess import no_solver_calls
    output = Path(output)
    data_dir = output / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    with no_solver_calls() as guard:
        tables, source_receipts = build_comparison_tables(year, case_reports, receipt_name=receipt_name)
        files = []
        for name, table in tables.items():
            path = data_dir / f"{name}.csv"
            table.to_csv(path, index=False, float_format="%.12g")
            files.append(path)
        manifest = output / "MEM_UC2_CORE_COMPARISON_MANIFEST.csv"
        pd.DataFrame([dict(artifact=path.relative_to(output).as_posix(), sha256=sha256_file(path),
                           role="UC2_CORE_COMPARISON_TABLE") for path in files]).to_csv(manifest, index=False)
        receipt = dict(status="PASS", year=year, scenarios=tables["scenario_metadata"].scenario_id.tolist(),
            model_generation="UC2", physical_source="UC_MILP", price_source="FIXED_COMMITMENT_PRICE_LP",
            reporting_solver_invocations=guard["solver_invocations"], network_opens=0,
            table_count=len(tables), figure_count=0, source_receipt_hashes=source_receipts,
            manifest_sha256=sha256_file(manifest))
        dump_json(output / "MEM_UC2_CORE_COMPARISON_RECEIPT.json", receipt)
    return receipt

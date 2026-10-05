"""Post-processing only: UC physical flows and fixed-commitment LP dual prices.

No network mutation or solve is performed. Endpoint/carrier contracts, rather
than display names, identify physical interfaces. Directional gross variables
are retained solely in the explicitly labelled degeneracy QA tables.
"""
from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd

from .canonical_results import _load_by_zone, _p2x_by_zone, _weights, _zone_order, hourly_net_imports_by_zone

EXTERNAL_MARKETS = ("EXT_FR", "EXT_CH", "EXT_AT", "EXT_SI", "EXT_ME", "EXT_GR", "EXT_TN", "EXT_MT")
GEOGRAPHIC_ORDER = ("NORD", "CNOR", "CSUD", "SUD", "CALA", "SICI", "SARD")
INTERFACE_CLASSES = {"internal_transfer": "ITALIAN_INTERZONAL", "external_trade": "EXTERNAL_MARKET_INTERFACE", "corsica_hub": "PHYSICAL_OR_HVDC_INTERFACE"}
FLOW_TOLERANCE_MW = 1e-4


def _display(bus: str) -> str:
    return "CNORD" if bus == "CNOR" else bus[4:] if bus.startswith("EXT_") else bus


def _spread_stats(spread: pd.Series) -> dict:
    absolute = spread.abs()
    n = len(spread)
    result = {"mean_signed_spread_EUR_per_MWh": float(spread.mean()),
              "mean_absolute_spread_EUR_per_MWh": float(absolute.mean()),
              "median_absolute_spread_EUR_per_MWh": float(absolute.median()),
              "p95_absolute_spread_EUR_per_MWh": float(absolute.quantile(.95)),
              "max_absolute_spread_EUR_per_MWh": float(absolute.max())}
    for threshold, label in [(0, "exactly_coupled"), (1, "within_1_EUR"), (5, "within_5_EUR"), (10, "within_10_EUR")]:
        hours = int((absolute <= threshold).sum())
        result[label + "_hours"] = hours
        result[label + "_share"] = hours / n if n else np.nan
    return result


def _capacity(network, names: list[str], direction: int) -> pd.Series:
    result = pd.Series(0.0, index=network.snapshots)
    for name in names:
        row = network.links.loc[name]
        column = "p_max_pu" if direction == 1 else "p_min_pu"
        dynamic = getattr(network.links_t, column)
        values = dynamic[name].reindex(network.snapshots) if name in dynamic else pd.Series(float(row[column]), index=network.snapshots)
        result += float(row.p_nom) * values * direction
    return result


def _interfaces(network, prices: pd.DataFrame) -> dict[str, pd.DataFrame]:
    zones = _zone_order()
    selected = network.links.loc[network.links.carrier.astype(str).isin(INTERFACE_CLASSES)]
    groups: dict[tuple[str, str, str], list[str]] = {}
    for name, row in selected.iterrows():
        a, b, carrier = str(row.bus0), str(row.bus1), str(row.carrier)
        if carrier == "internal_transfer":
            if a not in zones or b not in zones:
                raise ValueError(f"Non-Italian internal interface endpoints: {name}")
            a, b = sorted((a, b), key=GEOGRAPHIC_ORDER.index)
        else:
            if (a in zones) == (b in zones):
                raise ValueError(f"Ambiguous boundary interface endpoints: {name}")
            if b in zones:
                a, b = b, a
            if carrier == "external_trade" and b not in EXTERNAL_MARKETS:
                raise ValueError(f"Unmapped external market: {b}")
            if carrier == "corsica_hub" and b != "CORS":
                raise ValueError(f"Unmapped regulated hub: {b}")
        groups.setdefault((a, b, carrier), []).append(str(name))

    net = pd.DataFrame(index=network.snapshots)
    positive_util = pd.DataFrame(index=network.snapshots)
    negative_util = pd.DataFrame(index=network.snapshots)
    positive_caps = pd.DataFrame(index=network.snapshots)
    negative_caps = pd.DataFrame(index=network.snapshots)
    spreads = pd.DataFrame(index=network.snapshots)
    imports = pd.DataFrame(0.0, index=network.snapshots, columns=zones)
    ext_imports = imports.copy()
    weights = _weights(network, "link")
    canonical = None
    if network.meta.get('network_v2b_applied',False):
        from ..final_network_signed import net_flow_frame
        canonical = net_flow_frame(network)
    registry, summary, qa_frames = [], [], []
    for (a, b, carrier), names in groups.items():
        forward = [name for name in names if str(selected.at[name, "bus0"]) == a]
        reverse = [name for name in names if str(selected.at[name, "bus0"]) == b]
        if len(forward) > 1 or len(reverse) > 1:
            raise ValueError(f"Ambiguous parallel interface identity: {a}/{b}")
        for name in names:
            error = (network.links_t.p0[name] + network.links_t.p1[name]).abs().max()
            if error > FLOW_TOLERANCE_MW:
                raise ValueError(f"Net reporting currently requires accepted lossless interface: {name}")
        gross_pos = pd.Series(0.0, index=network.snapshots)
        gross_neg = gross_pos.copy()
        cap_pos = gross_pos.copy()
        cap_neg = gross_pos.copy()
        for direction_names, sign in [(forward, 1), (reverse, -1)]:
            for name in direction_names:
                p = network.links_t.p0[name].reindex(network.snapshots)
                if p.isna().any() or not np.isfinite(p).all():
                    raise ValueError(f"Incomplete UC interface dispatch: {name}")
                signed = p * sign
                gross_pos += signed.clip(lower=0)
                gross_neg += (-signed).clip(lower=0)
            if sign == 1:
                cap_pos += _capacity(network, direction_names, 1)
                cap_neg += _capacity(network, direction_names, -1).clip(lower=0)
            else:
                cap_neg += _capacity(network, direction_names, 1)
                cap_pos += _capacity(network, direction_names, -1).clip(lower=0)
        identifier = _display(a) + "__" + _display(b)
        signed_net = gross_pos - gross_neg
        if canonical is not None:
            same=canonical.carrier.eq(carrier)&canonical.bus_a.eq(min(a,b))&canonical.bus_b.eq(max(a,b))
            actual=canonical.loc[same].set_index('snapshot')['NET_FLOW_A_TO_B_MW'].reindex(network.snapshots)
            if len(actual)!=len(network.snapshots) or actual.isna().any():
                raise ValueError('Signed canonical net-flow coverage mismatch')
            expected=actual if a<b else -actual
            if (signed_net-expected).abs().max()>FLOW_TOLERANCE_MW:
                raise ValueError('Interface utilization/congestion does not reconcile NET_FLOW_A_TO_B_MW')
        if not np.isfinite(cap_pos.to_numpy()).all() or not np.isfinite(cap_neg.to_numpy()).all() or (cap_pos < 0).any() or (cap_neg < 0).any():
            raise ValueError(f"Invalid accepted interface capacity: {identifier}")
        if ((signed_net - cap_pos) > FLOW_TOLERANCE_MW).any() or ((-signed_net - cap_neg) > FLOW_TOLERANCE_MW).any():
            raise ValueError(f"Canonical UC net flow exceeds accepted directional cap: {identifier}")
        net[identifier] = signed_net
        positive_caps[identifier], negative_caps[identifier] = cap_pos, cap_neg
        pu_pos = signed_net.clip(lower=0).div(cap_pos.where(cap_pos > 0)).fillna(0)
        pu_neg = (-signed_net).clip(lower=0).div(cap_neg.where(cap_neg > 0)).fillna(0)
        positive_util[identifier], negative_util[identifier] = pu_pos, pu_neg
        if a in zones:
            imports[a] -= signed_net
            if carrier == "external_trade":
                ext_imports[a] -= signed_net
        if b in zones:
            imports[b] += signed_net
        overlapping = pd.concat([gross_pos, gross_neg], axis=1).min(axis=1)
        saturated_pos = (cap_pos > 0) & ((signed_net - cap_pos).abs() <= FLOW_TOLERANCE_MW)
        saturated_neg = (cap_neg > 0) & ((signed_net + cap_neg).abs() <= FLOW_TOLERANCE_MW)
        r = {"interface_id": identifier, "from_bus": a, "to_bus": b,
             "display_from": _display(a), "display_to": _display(b), "carrier": carrier,
             "classification": INTERFACE_CLASSES[carrier], "positive_link_ids": "|".join(forward),
             "negative_link_ids": "|".join(reverse), "positive_capacity_MW": float(cap_pos.max()),
             "negative_capacity_MW": float(cap_neg.max()), "physical_source": "VERIFIED_UC_MILP",
             "positive_direction": f"{a} -> {b}", "net_definition": "NET_FLOW_A_TO_B_MW (oriented to displayed endpoints)" if network.meta.get('network_v2b_applied',False) else "gross_positive_minus_gross_negative",
             "notes": "Regulated zero-injection commercial hub; not a price-taking external market" if carrier == "corsica_hub" else "Canonical endpoint/carrier interface contract"}
        registry.append(r)
        s = dict(r, positive_energy_MWh=float((signed_net.clip(lower=0) * weights).sum()),
                 negative_energy_MWh=float(((-signed_net).clip(lower=0) * weights).sum()),
                 net_energy_MWh=float((signed_net * weights).sum()),
                 positive_limit_hours=int(saturated_pos.sum()), negative_limit_hours=int(saturated_neg.sum()),
                 weighted_positive_limit_hours=float(weights[saturated_pos].sum()), weighted_negative_limit_hours=float(weights[saturated_neg].sum()),
                 mean_positive_utilization=float(pu_pos.mean()), max_positive_utilization=float(pu_pos.max()),
                 mean_negative_utilization=float(pu_neg.mean()), max_negative_utilization=float(pu_neg.max()),
                 hours_at_or_above_90_percent=int(((pu_pos >= .9) | (pu_neg >= .9)).sum()),
                 gross_counterflow_hours=int((overlapping > FLOW_TOLERANCE_MW).sum()),
                 overlapping_gross_counterflow_MWh=float((overlapping * weights).sum()),
                 gross_counterflow_status="ENGINEERING / QA ONLY", price_source="VERIFIED_FIXED_COMMITMENT_LP_DUALS")
        if a in prices and b in prices and carrier != "corsica_hub":
            spread = prices[b] - prices[a]
            spreads[identifier] = spread
            s.update(interface_mean_spread_EUR_per_MWh=float(spread.mean()),
                     interface_mean_absolute_spread_EUR_per_MWh=float(spread.abs().mean()),
                     positive_saturation_mean_spread_EUR_per_MWh=float(spread[saturated_pos].mean()),
                     negative_saturation_mean_spread_EUR_per_MWh=float(spread[saturated_neg].mean()),
                     saturation_mean_absolute_spread_EUR_per_MWh=float(spread[saturated_pos | saturated_neg].abs().mean()),
                     price_spread_definition="price_to_minus_price_from")
        else:
            s["price_spread_limitation"] = "CORS has no market price" if carrier == "corsica_hub" else "External price series absent"
        summary.append(s)
        qa_frames.append(pd.DataFrame({"snapshot": network.snapshots, "interface_id": identifier,
                                      "gross_positive_MW": gross_pos.to_numpy(), "gross_negative_MW": gross_neg.to_numpy(),
                                      "overlapping_gross_counterflow_MW": overlapping.to_numpy(),
                                      "overlapping_gross_counterflow_MWh": (overlapping * weights).to_numpy(),
                                      "figure_status": "ENGINEERING / QA ONLY",
                                      "limitation": "Gross directional variables are degeneracy diagnostics, not physical exchanges"}))
    residual = imports - hourly_net_imports_by_zone(network)
    reconciled = float(residual.abs().max().max()) if len(residual) else 0.0
    import_summary = []
    for zone in zones:
        values = imports[zone]
        import_summary.append({"zone": zone, "import_energy_MWh": float((values.clip(lower=0) * weights).sum()),
                               "export_energy_MWh": float(((-values).clip(lower=0) * weights).sum()),
                               "net_import_energy_MWh": float((values * weights).sum()),
                               "external_net_import_energy_MWh": float((ext_imports[zone] * weights).sum()),
                               "limitation": "Zone import/export volumes net simultaneous interfaces before temporal integration"})
    for frame in [net, positive_util, negative_util, positive_caps, negative_caps, spreads, imports, ext_imports]:
        frame.index.name = "snapshot"
    reconciliation = [{"check": "net_interface_bus_injections_reconcile_canonical_UC", "passed": reconciled <= FLOW_TOLERANCE_MW,
                       "detail": f"max_abs_residual_MW={reconciled:.12g}"},
                      {"check": "net_interface_energy_identity", "passed": all(abs(s["positive_energy_MWh"]-s["negative_energy_MWh"]-s["net_energy_MWh"]) < 1e-3 for s in summary), "detail": "positive minus negative equals signed net MWh"},
                      {"check": "net_interface_within_directional_limits", "passed": bool((positive_util <= 1 + FLOW_TOLERANCE_MW).all().all() and (negative_util <= 1 + FLOW_TOLERANCE_MW).all().all()), "detail": "canonical signed net relative to accepted directional caps"}]
    if any(s["carrier"] == "corsica_hub" for s in summary):
        hub_columns = [s["interface_id"] for s in summary if s["carrier"] == "corsica_hub"]
        hub_error = float(net[hub_columns].sum(axis=1).abs().max())
        reconciliation.append({"check": "CORS_regulated_zero_injection_hub", "passed": hub_error <= FLOW_TOLERANCE_MW,
                               "detail": f"max_abs_CORS_net_injection_MW={hub_error:.12g}; no price assigned"})
    return {"net_interface_hourly_MW": net, "net_interface_positive_utilization_hourly": positive_util,
            "net_interface_negative_utilization_hourly": negative_util,
            "net_interface_positive_capacity_hourly_MW": positive_caps, "net_interface_negative_capacity_hourly_MW": negative_caps,
            "net_interface_price_spread_hourly_EUR_per_MWh": spreads,
            "net_interface_registry": pd.DataFrame(registry), "net_interface_summary": pd.DataFrame(summary),
            "gross_counterflow_QA_only": pd.concat(qa_frames, ignore_index=True) if qa_frames else pd.DataFrame(),
            "canonical_net_imports_hourly_MW": imports, "canonical_external_net_imports_hourly_MW": ext_imports,
            "canonical_net_imports_summary": pd.DataFrame(import_summary), "diagnostics_reconciliation": pd.DataFrame(reconciliation)}


def _prices(network, prices: pd.DataFrame) -> dict[str, pd.DataFrame]:
    zones = _zone_order()
    loads = _load_by_zone(network)
    p2x = _p2x_by_zone(network)
    weights = _weights(network, "load")
    observed = [*zones, *[market for market in EXTERNAL_MARKETS if market in prices]]
    annual, monthly, hod, matrix = [], [], [], []
    for market in observed:
        p = prices[market]
        energy = (loads[market] + p2x[market]) * weights if market in zones else None
        rigid_energy = loads[market] * weights if market in zones else None
        quantiles = p.quantile([.05, .25, .5, .75, .95])
        r = {"market": market, "zone": market if market in zones else "", "display_market": _display(market),
             "arithmetic_mean_EUR_per_MWh": float(p.mean()), "load_weighted_mean_EUR_per_MWh": float((p * energy).sum()/energy.sum()) if energy is not None and energy.sum() > 0 else np.nan,
             "rigid_load_weighted_mean_EUR_per_MWh": float((p*rigid_energy).sum()/rigid_energy.sum()) if rigid_energy is not None and rigid_energy.sum() > 0 else np.nan,
             "median_EUR_per_MWh": float(quantiles.at[.5]), "p5_EUR_per_MWh": float(quantiles.at[.05]),
             "p25_EUR_per_MWh": float(quantiles.at[.25]), "p75_EUR_per_MWh": float(quantiles.at[.75]), "p95_EUR_per_MWh": float(quantiles.at[.95]),
             "min_EUR_per_MWh": float(p.min()), "max_EUR_per_MWh": float(p.max()), "std_EUR_per_MWh": float(p.std(ddof=0)),
             "zero_price_hours": int(np.isclose(p, 0., atol=1e-12).sum()), "zero_price_tolerance_EUR_per_MWh": 1e-12, "negative_price_hours": int(p.lt(0).sum()),
             "above100_price_hours": int(p.gt(100).sum()), "above200_price_hours": int(p.gt(200).sum()), "above500_price_hours": int(p.gt(500).sum()),
             "snapshot_count": len(p), "price_source": "VERIFIED_FIXED_COMMITMENT_LP_DUALS", "load_source": "VERIFIED_UC_MILP",
             "weighting_definition": "UC rigid end-use load plus UC P2X withdrawal, objective duration weights" if market in zones else "Not load weighted",
             "limitation": "External market has no canonical Italian load weight" if market not in zones else "Canonical load-weighted denominator preserves rigid load plus P2X; rigid-only diagnostic explicitly separate"}
        annual.append(r)
        for month, pm in p.groupby(p.index.month):
            em = energy.reindex(pm.index) if energy is not None else None
            erm = rigid_energy.reindex(pm.index) if rigid_energy is not None else None
            monthly.append({"market": market, "month": month, "mean_EUR_per_MWh": float(pm.mean()),
                            "load_weighted_mean_EUR_per_MWh": float((pm*em).sum()/em.sum()) if em is not None and em.sum() > 0 else np.nan,
                            "rigid_load_weighted_mean_EUR_per_MWh": float((pm*erm).sum()/erm.sum()) if erm is not None and erm.sum() > 0 else np.nan,
                            "weighting_definition": r["weighting_definition"]})
        for hour, ph in p.groupby(p.index.hour):
            hod.append({"market": market, "hour_of_day": hour, "mean_EUR_per_MWh": float(ph.mean()),
                        "zero_price_hours": int(np.isclose(ph, 0., atol=1e-12).sum()), "sample_hours": len(ph)})
        for (month, hour), ph in p.groupby([p.index.month, p.index.hour]):
            matrix.append({"market": market, "month": month, "hour_of_day": hour, "mean_EUR_per_MWh": float(ph.mean()), "zero_price_hours": int(np.isclose(ph, 0., atol=1e-12).sum()), "sample_hours": len(ph)})
    rigid_load_frame = pd.DataFrame(loads)
    load_frame = rigid_load_frame + pd.DataFrame(p2x)
    national_load = load_frame.sum(axis=1)
    national_numerator = (prices[list(zones)] * load_frame).sum(axis=1)
    national_hourly = national_numerator.div(national_load.where(national_load > 0))
    national_monthly = []
    for month, indices in national_load.groupby(national_load.index.month).groups.items():
        denominator = float((national_load.loc[indices]*weights.loc[indices]).sum())
        numerator = float((national_numerator.loc[indices]*weights.loc[indices]).sum())
        rigid_denominator = float((rigid_load_frame.loc[indices].sum(axis=1)*weights.loc[indices]).sum())
        rigid_numerator = float(((prices.loc[indices, list(zones)]*rigid_load_frame.loc[indices]).sum(axis=1)*weights.loc[indices]).sum())
        national_monthly.append({"month": month, "load_weighted_mean_EUR_per_MWh": numerator/denominator if denominator else np.nan,
                                 "rigid_load_weighted_mean_EUR_per_MWh": rigid_numerator/rigid_denominator if rigid_denominator else np.nan,
                                 "rigid_load_plus_P2X_MWh": denominator, "rigid_load_MWh": rigid_denominator,
                                 "weighting_definition": "UC rigid end-use load plus UC P2X withdrawal, objective duration weights"})
    pairwise = [{"market_a": a, "market_b": b, "spread_definition": "price_a_minus_price_b", **_spread_stats(prices[a]-prices[b])} for a,b in combinations(zones,2)]
    external = []
    for zone in zones:
        for market in EXTERNAL_MARKETS:
            if market not in prices:
                continue
            external.append({"zone": zone, "external_market": market,
                             "spread_definition": "Italian_price_minus_external_price", **_spread_stats(prices[zone]-prices[market]),
                             "correlation": float(prices[zone].corr(prices[market])),
                             "interpretation": "Price similarity only; no causal price-setter attribution"})
    spatial = pd.DataFrame({"spread_EUR_per_MWh": prices[list(zones)].max(axis=1)-prices[list(zones)].min(axis=1),
                            "distinct_zonal_prices": prices[list(zones)].nunique(axis=1),
                            "load_weighted_national_EUR_per_MWh": national_hourly})
    spatial.index.name = "snapshot"
    distribution = spatial.distinct_zonal_prices.value_counts().sort_index().rename_axis("distinct_zonal_prices").reset_index(name="hours")
    distribution["share"] = distribution.hours / len(spatial)
    closest_frames, closest_summary = [], []
    ext = [market for market in EXTERNAL_MARKETS if market in prices]
    if ext:
        for zone in zones:
            distance = prices[ext].sub(prices[zone], axis=0).abs()
            chosen = distance.idxmin(axis=1)
            minimum = distance.min(axis=1)
            ties = distance.eq(minimum, axis=0).sum(axis=1)
            closest_frames.append(pd.DataFrame({"snapshot": prices.index, "zone": zone, "closest_external_market": chosen.to_numpy(),
                                                "absolute_spread_EUR_per_MWh": minimum.to_numpy(), "tied_closest_market_count": ties.to_numpy(),
                                                "interpretation": "Price similarity, not causal price setting; ties selected by configured external-market order"}))
            for market in ext:
                closest_summary.append({"zone": zone, "external_market": market, "selected_closest_hours": int(chosen.eq(market).sum()),
                                        "selected_closest_share": float(chosen.eq(market).mean()),
                                        "among_tied_closest_hours": int(distance[market].eq(minimum).sum()),
                                        "interpretation": "Deterministic descriptive diagnostic; not a causal price setter"})
    return {"price_annual_summary": pd.DataFrame(annual), "price_monthly_zonal_means": pd.DataFrame(monthly),
            "price_monthly_national_load_weighted": pd.DataFrame(national_monthly), "price_hour_of_day_profile": pd.DataFrame(hod),
            "price_month_hour_matrix": pd.DataFrame(matrix), "price_pairwise_spreads": pd.DataFrame(pairwise),
            "price_external_spreads": pd.DataFrame(external), "price_hourly_all_italy_spread": spatial,
            "price_distinct_simultaneous_distribution": distribution,
            "price_closest_external_hourly": pd.concat(closest_frames, ignore_index=True) if closest_frames else pd.DataFrame(),
            "price_closest_external_summary": pd.DataFrame(closest_summary),
            "price_external_availability": pd.DataFrame({"market": EXTERNAL_MARKETS, "available": [market in prices for market in EXTERNAL_MARKETS]})}


def build_uc2_diagnostics(uc_network, price_frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Return additive tables; caller controls persistence and verified lineage.

    All physical values use ``uc_network``; its marginal prices are never read.
    ``price_frame`` must contain the verified fixed-commitment LP dual prices.
    Exact coupling means numerical equality; near coupling uses inclusive EUR
    thresholds. Counterflow overlap is min(gross forward,gross reverse), not
    twice that amount. Statistical std is population std (ddof=0).
    """
    if not price_frame.index.equals(uc_network.snapshots):
        raise ValueError("UC chronology and LP dual-price chronology differ")
    if not set(_zone_order()).issubset(price_frame.columns):
        raise ValueError("Missing fixed-commitment LP Italian zonal prices")
    columns = [*_zone_order(), *[market for market in EXTERNAL_MARKETS if market in price_frame]]
    prices = price_frame[columns].astype(float).copy()
    if not np.isfinite(prices.to_numpy()).all():
        raise ValueError("Incomplete or nonfinite LP dual-price series")
    result = _interfaces(uc_network, prices)
    result.update(_prices(uc_network, prices))
    result["diagnostics_reconciliation"] = pd.concat([result["diagnostics_reconciliation"], pd.DataFrame([
        {"check": "UC_and_LP_price_chronology_equal", "passed": True, "detail": f"{len(prices)} snapshots"},
        {"check": "Italian_price_zones_complete", "passed": True, "detail": "All seven Italian zones"},
        {"check": "physical_and_price_sources_separate", "passed": True, "detail": "UC links/load only; explicit LP price frame only"},
        {"check": "gross_counterflow_QA_only", "passed": True, "detail": "Gross flows persisted only in explicitly QA-labelled diagnostics"},
    ])], ignore_index=True)
    return result

"""Read-only KKT attribution of an accepted fixed-commitment pricing solution.

This is a candidate analytical extension, not activated by the report pipeline.
Physical annual reporting remains UC MILP; ONLY this price-formation analysis
uses the pricing LP's primal variables alongside its own duals.
"""
from __future__ import annotations

from collections import defaultdict, deque
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..common import ROOT, sha256_file
from .canonical_results import reporting_config

VERSION = "MEM_UC3_KKT_ATTRIBUTION_V2"
ZONES = ("NORD", "CNORD", "CSUD", "SUD", "CALA", "SICI", "SARD")
CLASSES = ("DIRECT_MARGINAL_TECHNOLOGY", "STORAGE_HYDRO_OPPORTUNITY_VALUE",
           "P2X_FLEXIBLE_DEMAND", "UC_RAMP_CONSTRAINED", "MARKET_COUPLING",
           "MULTIPLE_CANDIDATES", "OTHER_INDETERMINATE")
TOLERANCE = 1e-6
POWER_TOLERANCE = 1e-6
RULES = {
    "version": VERSION,
    "price_tolerance_EUR_MWh": TOLERANCE,
    "power_tolerance_MW": POWER_TOLERANCE,
    "hierarchy": "All defensible local roots first; distinct local mechanisms/technologies -> Multiple; no local root plus any admissible remote roots -> Market Coupling regardless root multiplicity or heterogeneity",
    "generator_KKT": "sign*price = cost - (mu_lower+mu_upper+R)/objective_weight; R=(ramp_up+ramp_down)_t-(ramp_up+ramp_down)_(t+1); terminal next=0",
    "persisted_ramp_duals": "mu_ramp_limit_up/down; validated against complete dispatch stationarity; mu_up/down are distinct coexisting fields, not substituted",
    "store_KKT": "internal_bus_price = store_marginal_cost + store_weight/objective_weight * mu_energy_balance",
    "store_energy_KKT": "mu_energy_t - standing_efficiency_(t+1)*mu_energy_(t+1) - mu_lower_t - mu_upper_t = 0; cyclic boundary wraps; noncyclic terminal next=0",
    "link_KKT": "efficiency*price_bus1-price_bus0 = marginal_cost-(mu_lower+mu_upper)/objective_weight",
    "P2X_KKT": "price = annual_dual*generator_weight/objective_weight - cost + (mu_lower+mu_upper)/objective_weight",
    "P2X_shadow": "Native operational-limit equality dual preferred; otherwise clean interior stationarity inference with max-min consistency check",
    "marginal_position": "Generators: nonzero fixed-status/availability operating range, feasible power, negligible both bound duals and valid full KKT; upper/lower zero-scarcity positions allowed. Store conversion and P2X retain strict interior. Ramp context requires nonzero intertemporal dispatch-gradient",
    "same_technology_collapse": "Same local mechanism and economic technology; implied prices within tolerance. Solar variants -> Solar; wind variants -> Wind; detailed carrier retained. Reservoir/pondage -> Hydro. Remote root groups stay distinct in audit but do not change Market Coupling mechanism",
    "coupling": "Canonical interface registry only; strict net interior, negligible directional bound duals, all Link KKT relations valid. Loss/cost relations propagate affinely. Congested price-separating edges break paths",
    "ambiguity": "No asset tie-break: full identities/positions and remote-root groups retained. Only distinct LOCAL mechanisms/technologies -> Multiple. Coupled-island heterogeneity remains Market Coupling",
    "interpretation": "KKT-consistent supporting marginal candidates, not unique causal setters. Zero-scarcity bound candidates may support one-sided marginality; degeneracy remains explicit",
    "presentation": "Known technology is preserved, including CCGT under ramp/UC context. Coupled roots displayed as Market Coupling, not a forced remote technology",
    "provisional_aggregates": "REFERENCE_ONLY_NOT_REGRESSION_TARGETS; revised Base compared with preserved strict output by zone-hour",
    "activation": "REVIEW_ONLY_NOT_ROUTINE",
}


class AttributionError(RuntimeError):
    pass


def fail(status, detail):
    raise AttributionError(f"{status}: {detail}")


def normalize_zone(value):
    return "CNORD" if value == "CNOR" else value


def generator_reconstruction(cost, lower, upper, ramp_up, ramp_down, weights, sign=1):
    ramp = np.asarray(ramp_up) + np.asarray(ramp_down)
    following = np.concatenate((ramp[1:], np.zeros_like(ramp[:1])), axis=0)
    gradient = ramp - following
    return (np.asarray(cost) - (np.asarray(lower) + np.asarray(upper) + gradient) / weights) / sign, gradient


def opportunity_reconstruction(value, efficiency, cost, lower, upper, weights, *, charging):
    if charging:
        return efficiency * value - cost + (lower + upper) / weights
    return (value + cost - (lower + upper) / weights) / efficiency


def store_energy_stationarity(energy_dual, lower, upper, standing_efficiency, cyclic):
    following = np.roll(np.asarray(energy_dual), -1)
    efficiency_next = np.roll(np.asarray(standing_efficiency), -1)
    if not cyclic:
        following[-1] = 0.
    return np.asarray(energy_dual) - efficiency_next * following - np.asarray(lower) - np.asarray(upper)


def infer_p2x_shadow(price, cost, lower, upper, objective_weights, generator_weights, clean, tolerance):
    implied = (price + cost - (lower + upper) / objective_weights) * objective_weights / generator_weights
    selected = np.asarray(implied)[clean]
    if len(selected) == 0 or not np.isfinite(selected).all() or np.ptp(selected) > tolerance:
        fail("P2X_SHADOW_NOT_IDENTIFIABLE", f"clean observations={len(selected)}; inconsistent/absent equality shadow")
    return float(np.median(selected))


def collapse_candidates(candidates, *, remote=False):
    groups = defaultdict(list)
    for c in candidates:
        key = (c["mechanism"], c["technology"])
        if remote:
            key += (c["zone"],)
        groups[key].append(c)
    return list(groups.values())


def classify_candidates(candidates, *, remote=False):
    groups = collapse_candidates(candidates, remote=remote)
    if not groups:
        return "OTHER_INDETERMINATE", groups
    if remote:
        return "MARKET_COUPLING", groups
    if len(groups) > 1:
        return "MULTIPLE_CANDIDATES", groups
    return groups[0][0]["mechanism"], groups


def generator_position_admissible(power, lower, upper, power_tolerance=POWER_TOLERANCE):
    """No marginal freedom for off/fixed/zero-availability resources."""
    return ((np.asarray(upper) - np.asarray(lower) > power_tolerance)
            & (np.asarray(power) >= np.asarray(lower) - power_tolerance)
            & (np.asarray(power) <= np.asarray(upper) + power_tolerance))


def local_mechanism(interior, lower_dual, upper_dual, residual, ramp_gradient, mechanism, tolerance=TOLERANCE):
    if not interior or max(abs(lower_dual), abs(upper_dual), abs(residual)) > tolerance:
        return None
    if mechanism == "DIRECT_MARGINAL_TECHNOLOGY" and abs(ramp_gradient) > tolerance:
        return "UC_RAMP_CONSTRAINED"
    return mechanism


def coupling_edge_is_open(interior, dual_max, stationarity_residual, tolerance=TOLERANCE):
    return bool(interior and dual_max <= tolerance and stationarity_residual <= tolerance)


def _dense(n, component, attr, names, *, dual=False, output=False):
    static = getattr(n, component)
    frame = getattr(n, component + "_t").get(attr)
    if frame is None or (dual and not set(names).issubset(frame.columns)):
        fail("REQUIRED_DUAL_DATA_MISSING" if dual else "REQUIRED_PRIMAL_DATA_MISSING", f"{component}.{attr}")
    if output:
        # PyPSA NetCDF serialization omits columns equal to their output default 0.
        result = frame.reindex(index=n.snapshots, columns=names, fill_value=0.)
    else:
        result = frame.reindex(index=n.snapshots, columns=names)
        if not dual:
            for name in names:
                if name not in frame:
                    result[name] = float(static.at[name, attr])
    a = result.to_numpy(dtype=float)
    if not np.isfinite(a).all():
        fail("REQUIRED_DUAL_DATA_MISSING" if dual else "REQUIRED_PRIMAL_DATA_MISSING", f"nonfinite {component}.{attr}")
    return a


def _tech(carrier):
    exact = reporting_config()["carrier_to_display_technology"]
    if carrier not in exact:
        if carrier == "external_market":
            return "External market opportunity"
        if carrier == "load_shedding":
            return "Load shedding / VOLL"
        fail("REQUIRED_PRIMAL_DATA_MISSING", f"unmapped generator carrier {carrier}")
    family = {
        "Solar PV - rooftop": "Solar", "Solar PV - utility": "Solar",
        "Onshore wind": "Wind", "Offshore wind": "Wind", "Hydro - run of river": "Hydro RoR",
        "Gas - CCGT": "CCGT", "Gas - internal combustion": "Gas IC",
        "Bioenergy - steam thermal": "Bioenergy", "Bioenergy - other thermal": "Bioenergy",
        "Bioenergy - internal combustion": "Bioenergy", "Bioenergy": "Bioenergy",
    }
    return family.get(exact[carrier], exact[carrier])


def _residual_stats(values):
    a = np.abs(np.asarray(values, dtype=float))
    a = a[np.isfinite(a)]
    return {"observations": int(len(a)), "max_abs": float(a.max()) if len(a) else None,
            "p99_abs": float(np.quantile(a, .99)) if len(a) else None,
            "median_abs": float(np.median(a)) if len(a) else None}


def discover_sources(year, scenario):
    """Resolve accepted source paths from the existing authority register."""
    from ..stage_b_uc2 import case_root
    report = case_root(year, scenario) / "REPORTING"
    register = report / "uc2_VIS_RESULT_AUTHORITY_REGISTER.csv"
    entries = pd.read_csv(register)
    lp = entries.loc[entries.UC_status.astype(str).ne("VERIFIED_UC2") &
                     entries.result_id.astype(str).str.endswith("FIXED_COMMITMENT_PRICE_LP")]
    # Some register revisions use VERIFIED_UC2 for both solved result roles.
    if lp.empty:
        lp = entries.loc[entries.result_id.astype(str).str.endswith("FIXED_COMMITMENT_PRICE_LP")]
    if len(lp) != 1:
        fail("SOURCE_AUTHORITY_CONFLICT", "pricing solution not unique in authority register")
    row = lp.iloc[0]
    def path(value):
        p = Path(value)
        return p if p.is_absolute() else ROOT / p
    source = {"network": path(row.network_path), "solve_receipt": path(row.receipt),
              "verification": path(row.manifest), "authority_register": register,
              "prices": report / "fixed_commitment_prices_hourly.csv",
              "interfaces": report / "CANONICAL/statistics/uc2_net_interface_registry.csv",
              "report_receipt": report / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json"}
    uc = entries.loc[entries.result_id.astype(str).str.endswith("_UC_MILP")]
    if len(uc) != 1:
        fail("SOURCE_AUTHORITY_CONFLICT", "UC parent not unique in authority register")
    source["uc_parent_receipt"] = path(uc.iloc[0].receipt)
    source["uc_parent_verification"] = path(uc.iloc[0].manifest)
    receipt = json.loads(source["solve_receipt"].read_text())
    verify = json.loads(source["verification"].read_text())
    accepted = json.loads(source["report_receipt"].read_text())
    uc_receipt = json.loads(source["uc_parent_receipt"].read_text())
    uc_verified = json.loads(source["uc_parent_verification"].read_text())
    if (uc_receipt["status"] != "PASS" or uc_verified["status"] != "PASS"
            or receipt["fixed_commitment_milp_sha256"] != uc_receipt["solved_sha256"]):
        fail("SOURCE_AUTHORITY_CONFLICT", "pricing LP fixed-commitment UC parent lineage differs")
    if (receipt["status"] != "PASS" or verify["status"] != "PASS" or receipt["snapshots"] != 8760
            or receipt["year"] != year or receipt["scenario"] != scenario or receipt["termination_condition"] != "optimal"
            or accepted["price_source"] != "FIXED_COMMITMENT_PRICE_LP"):
        fail("SOURCE_AUTHORITY_CONFLICT", "solve/verification/report authority mismatch")
    if sha256_file(source["network"]) != receipt["solved_sha256"]:
        fail("SOURCE_AUTHORITY_CONFLICT", "pricing network hash differs from accepted solve")
    if accepted["protected_source_hashes"].get(str(source["network"])) != receipt["solved_sha256"]:
        fail("SOURCE_AUTHORITY_CONFLICT", "pricing network differs from finalized R1 source")
    if sha256_file(ROOT / receipt["input_path"]) != receipt["input_sha256"]:
        fail("SOURCE_AUTHORITY_CONFLICT", "pricing parent input hash mismatch")
    source["hashes"] = {str(p): sha256_file(p) for p in source.values() if isinstance(p, Path)}
    source["job_id"] = receipt["job_id"]
    source["fixed_commitment_milp_sha256"] = receipt["fixed_commitment_milp_sha256"]
    return source


def build_evidence(n, registry, *, global_duals_persisted=True):
    """Evaluate all saved dispatch stationarity before identifying roots."""
    snapshots = pd.DatetimeIndex(n.snapshots)
    if snapshots.has_duplicates or not snapshots.equals(pd.date_range("2019-01-01", "2020-01-01", freq="h", inclusive="left")):
        fail("REQUIRED_PRIMAL_DATA_MISSING", "expected complete saved 2019 hourly chronology")
    weights = n.snapshot_weightings.objective.to_numpy(float)
    gw = n.snapshot_weightings.generators.to_numpy(float)
    sw = n.snapshot_weightings.stores.to_numpy(float)
    if not (np.isfinite(weights).all() and (weights > 0).all() and np.isfinite(gw).all() and np.isfinite(sw).all()):
        fail("REQUIRED_PRIMAL_DATA_MISSING", "invalid LP objective/physical weights")
    prices = n.buses_t.marginal_price
    roots = []
    residuals = defaultdict(list)
    p2x_shadows = {}
    gens = n.generators.index[n.generators.p_nom.gt(0) &
                              ~n.generators.carrier.isin(["water_energy", "spillage"])]
    for name in gens:
        row = n.generators.loc[name]
        p = _dense(n, "generators", "p", [name], output=True)[:, 0]
        cost = _dense(n, "generators", "marginal_cost", [name])[:, 0]
        if float(row.marginal_cost_quadratic) != 0:
            cost = cost + 2 * float(row.marginal_cost_quadratic) * p
        lo = _dense(n, "generators", "p_min_pu", [name])[:, 0] * float(row.p_nom)
        hi = _dense(n, "generators", "p_max_pu", [name])[:, 0] * float(row.p_nom)
        if bool(row.committable):
            status = _dense(n, "generators", "status", [name], output=True)[:, 0]
            lo, hi = lo * status, hi * status
        ml = _dense(n, "generators", "mu_lower", [name], dual=True)[:, 0]
        mu = _dense(n, "generators", "mu_upper", [name], dual=True)[:, 0]
        ru = _dense(n, "generators", "mu_ramp_limit_up", [name], dual=True)[:, 0]
        rd = _dense(n, "generators", "mu_ramp_limit_down", [name], dual=True)[:, 0]
        zone = str(row.bus)
        price = prices[zone].to_numpy(float)
        interior = (p > lo + POWER_TOLERANCE) & (p < hi - POWER_TOLERANCE)
        bound_clear = (np.abs(ml / weights) <= TOLERANCE) & (np.abs(mu / weights) <= TOLERANCE)
        if float(row.sign) == -1:
            contract = n.global_constraints.loc[n.global_constraints.carrier_attribute.eq(row.carrier)]
            if len(contract) != 1 or str(contract.iloc[0].sense) != "==" or str(contract.iloc[0].type) != "operational_limit":
                fail("P2X_SHADOW_NOT_IDENTIFIABLE", f"{name}: equality contract ambiguous")
            shadow = float(contract.iloc[0].mu) if global_duals_persisted else np.nan
            clean = interior & bound_clear & (np.abs(ru + rd) <= TOLERANCE)
            inferred = infer_p2x_shadow(price, cost, ml, mu, weights, gw, clean, TOLERANCE) if clean.any() else None
            # Native dual is audited against clean observations, including valid zero values.
            if np.isfinite(shadow):
                if inferred is not None and abs(shadow - inferred) > TOLERANCE:
                    fail("P2X_SHADOW_NOT_IDENTIFIABLE", f"{name}: native and inferred shadow differ")
                method = "NATIVE_GLOBAL_EQUALITY_DUAL_VALIDATED_BY_INTERIOR_STATIONARITY"
            else:
                if inferred is None:
                    fail("P2X_SHADOW_NOT_IDENTIFIABLE", f"{name}: no native shadow or clean interior observations")
                shadow, method = inferred, "INFERRED_INTERIOR_STATIONARITY"
            recon = shadow * gw / weights - cost + (ml + mu) / weights
            mechanism, technology = "P2X_FLEXIBLE_DEMAND", "P2X"
            gradient = np.zeros(len(p))
            p2x_shadows[name] = {"shadow": shadow, "method": method, "clean_observations": int(clean.sum()),
                                  "interior_implied_spread": float(np.ptp((price + cost)[clean] * weights[clean] / gw[clean])) if clean.any() else None}
        elif float(row.sign) == 1:
            recon, gradient = generator_reconstruction(cost, ml, mu, ru, rd, weights)
            mechanism, technology = "DIRECT_MARGINAL_TECHNOLOGY", _tech(str(row.carrier))
        else:
            fail("REQUIRED_PRIMAL_DATA_MISSING", f"unsupported Generator sign {name}")
        residual = recon - price
        residuals["P2X" if mechanism == "P2X_FLEXIBLE_DEMAND" else "conventional_generators"].extend(residual)
        if np.abs(residual).max() > TOLERANCE:
            fail("MATERIAL_KKT_RECONSTRUCTION_FAILURE", f"Generator {name}: max residual {np.abs(residual).max()}")
        roots.append(dict(asset_id=name, component="Generator", zone=zone, technology=technology,
                          carrier=str(row.carrier), mechanism=mechanism, position="interior",
                          p=p, cost=cost, opportunity=(np.full(len(p), shadow) if mechanism == "P2X_FLEXIBLE_DEMAND" else np.full(len(p), np.nan)),
                          recon=recon, residual=residual, interior=interior, bound_clear=bound_clear,
                          gradient=gradient / weights, lower=ml / weights, upper=mu / weights,
                          admissible_position=generator_position_admissible(p, lo, hi) if mechanism == "DIRECT_MARGINAL_TECHNOLOGY" else interior,
                          operating_lower=lo, operating_upper=hi, native_power=p,
                          ramp_up_dual=ru / weights, ramp_down_dual=rd / weights,
                          efficiency=np.ones(len(p)), objective_weight=weights,
                          fixed_status=status if bool(row.committable) else np.ones(len(p))))
    store_bus = {str(row.bus): (str(name), str(row.carrier)) for name, row in n.stores.iterrows()}
    if len(store_bus) != len(n.stores):
        fail("REQUIRED_PRIMAL_DATA_MISSING", "multiple Store states share a bus")
    for sid, row in n.stores.iterrows():
        mu = _dense(n, "stores", "mu_energy_balance", [sid], dual=True)[:, 0]
        lower = _dense(n, "stores", "mu_lower", [sid], dual=True)[:, 0]
        upper = _dense(n, "stores", "mu_upper", [sid], dual=True)[:, 0]
        standing = (1 - _dense(n, "stores", "standing_loss", [sid])[:, 0]) ** sw
        state_residual = store_energy_stationarity(mu, lower, upper, standing, bool(row.e_cyclic))
        residuals["Store_intertemporal_energy"].extend(state_residual)
        if np.abs(state_residual).max() > TOLERANCE:
            fail("MATERIAL_KKT_RECONSTRUCTION_FAILURE", f"Store energy {sid}: {np.abs(state_residual).max()}")
    hydro = pd.read_csv(ROOT / "pre_pypsa_inputs/MEM_Hydro_Static_Component_Mapping.csv")
    for name, row in n.links.loc[n.links.carrier.isin(["battery_energy", "water_energy"]) & n.links.p_nom.gt(0)].iterrows():
        a, b = str(row.bus0), str(row.bus1)
        charging = a in ("CNOR", *ZONES) and b in store_bus
        discharging = b in ("CNOR", *ZONES) and a in store_bus
        if not (charging or discharging):
            fail("REQUIRED_PRIMAL_DATA_MISSING", f"conversion Link endpoints {name}")
        grid, state = (a, b) if charging else (b, a)
        sid, carrier = store_bus[state]
        eta = _dense(n, "links", "efficiency", [name])[:, 0]
        p = _dense(n, "links", "p0", [name], output=True)[:, 0]
        cost = _dense(n, "links", "marginal_cost", [name])[:, 0]
        ml = _dense(n, "links", "mu_lower", [name], dual=True)[:, 0]
        mu = _dense(n, "links", "mu_upper", [name], dual=True)[:, 0]
        lo = float(row.p_nom) * _dense(n, "links", "p_min_pu", [name])[:, 0]
        hi = float(row.p_nom) * _dense(n, "links", "p_max_pu", [name])[:, 0]
        value = _dense(n, "stores", "mu_energy_balance", [sid], dual=True)[:, 0] * sw / weights + float(n.stores.at[sid, "marginal_cost"])
        residuals["Store_native_value"].extend(value - prices[state].to_numpy(float))
        if carrier == "battery_energy":
            technology = "BESS"
        else:
            matches = hydro.loc[hydro.zone.eq(grid) & hydro.hydro_class.map(lambda c: state.endswith("_" + c))]
            if len(matches) != 1:
                fail("REQUIRED_PRIMAL_DATA_MISSING", f"accepted hydro mapping not unique: {state}")
            technology = "PHS" if matches.iloc[0].hydro_class in ("PURE_PHS", "MIXED_PHS") else "Hydro"
        recon = opportunity_reconstruction(value, eta, cost, ml, mu, weights, charging=charging)
        residual = recon - prices[grid].to_numpy(float)
        residuals[technology].extend(residual)
        if np.abs(residual).max() > TOLERANCE:
            fail("MATERIAL_KKT_RECONSTRUCTION_FAILURE", f"{name}: max residual {np.abs(residual).max()}")
        roots.append(dict(asset_id=str(name), component="Link", zone=grid, technology=technology,
                          carrier=str(row.carrier), store_id=sid, mechanism="STORAGE_HYDRO_OPPORTUNITY_VALUE",
                          position="charging" if charging else "discharging", p=p if charging else p * eta,
                          cost=cost, opportunity=value, recon=recon, residual=residual,
                          interior=(p > lo + POWER_TOLERANCE) & (p < hi - POWER_TOLERANCE),
                          bound_clear=(np.abs(ml / weights) <= TOLERANCE) & (np.abs(mu / weights) <= TOLERANCE),
                          gradient=np.zeros(len(p)), lower=ml / weights, upper=mu / weights,
                          admissible_position=(p > lo + POWER_TOLERANCE) & (p < hi - POWER_TOLERANCE),
                          operating_lower=lo, operating_upper=hi, native_power=p,
                          ramp_up_dual=np.zeros(len(p)), ramp_down_dual=np.zeros(len(p)),
                          efficiency=eta, objective_weight=weights, fixed_status=np.ones(len(p))))
    edges = []
    for row in registry.itertuples(index=False):
        names = [s for s in str(row.positive_link_ids).split(";") + str(row.negative_link_ids).split(";") if s and s != "nan"]
        a, b = str(row.from_bus), str(row.to_bus)
        signed = np.zeros(len(snapshots)); cap_pos = np.zeros(len(snapshots)); cap_neg = cap_pos.copy()
        dualmax = np.zeros(len(snapshots)); relation = np.zeros(len(snapshots)); affine = None
        for name in names:
            item = n.links.loc[name]; forward = str(item.bus0) == a
            eta = _dense(n, "links", "efficiency", [name])[:, 0]
            cost = _dense(n, "links", "marginal_cost", [name])[:, 0]
            ml = _dense(n, "links", "mu_lower", [name], dual=True)[:, 0]
            mu = _dense(n, "links", "mu_upper", [name], dual=True)[:, 0]
            p = _dense(n, "links", "p0", [name], output=True)[:, 0]
            signed += p * (1 if forward else -1)
            lo = float(item.p_nom) * _dense(n, "links", "p_min_pu", [name])[:, 0]
            hi = float(item.p_nom) * _dense(n, "links", "p_max_pu", [name])[:, 0]
            cap_pos += hi if forward else -lo
            cap_neg += -lo if forward else hi
            d = eta * prices[str(item.bus1)].to_numpy(float) - prices[str(item.bus0)].to_numpy(float)
            res = d - (cost - (ml + mu) / weights)
            residuals["coupling_relations"].extend(res)
            relation = np.maximum(relation, np.abs(res))
            dualmax = np.maximum(dualmax, np.maximum(np.abs(ml), np.abs(mu)) / weights)
            # Transform price_a -> price_b; inverse used for a reverse-oriented Link.
            scale = 1 / eta if forward else eta
            offset = cost / eta if forward else -cost
            if affine is None:
                affine = (scale, offset)
        if relation.max() > TOLERANCE:
            fail("MATERIAL_KKT_RECONSTRUCTION_FAILURE", f"interface {row.interface_id}: {relation.max()}")
        edges.append(dict(id=str(row.interface_id), a=a, b=b,
                          interior=(signed > -cap_neg + POWER_TOLERANCE) & (signed < cap_pos - POWER_TOLERANCE),
                          dualmax=dualmax, residual=relation, scale=affine[0], offset=affine[1],
                          price_separating=np.abs(prices[a].to_numpy(float) - prices[b].to_numpy(float)) > TOLERANCE))
    if np.abs(residuals["Store_native_value"]).max() > TOLERANCE:
        fail("MATERIAL_KKT_RECONSTRUCTION_FAILURE", "Store native opportunity value differs from internal bus balance dual")
    return {"snapshots": snapshots, "prices": prices, "roots": roots, "edges": edges,
            "physical_hours": gw, "residuals": {k: _residual_stats(v) for k, v in residuals.items()},
            "P2X_shadows": p2x_shadows}


def representative_evidence(table, audit, evidence):
    """Compact, reproducible observation sample; does not affect classification."""
    selected = []
    def renewable_bound_mask(technology):
        # Co-marginal VRE can correctly belong to local Multiple; sample the
        # actual qualifying candidates rather than require a unique setter.
        candidates = audit.loc[~audit.remote & audit.technology.eq(technology)
                               & audit.position.str.contains("bound_zero_scarcity", na=False)]
        keys = pd.MultiIndex.from_frame(candidates[["snapshot", "target_zone"]])
        return pd.MultiIndex.from_frame(table[["snapshot", "zone"]]).isin(keys)
    masks = {
        "direct_conventional": table.primary_attribution_class.eq("DIRECT_MARGINAL_TECHNOLOGY") & table.setter_technology.eq("CCGT"),
        "wind_zero_scarcity_bound": renewable_bound_mask("Wind"),
        "solar_zero_scarcity_bound": renewable_bound_mask("Solar"),
        "BESS": table.primary_attribution_class.eq("STORAGE_HYDRO_OPPORTUNITY_VALUE") & table.setter_technology.eq("BESS"),
        "PHS": table.primary_attribution_class.eq("STORAGE_HYDRO_OPPORTUNITY_VALUE") & table.setter_technology.eq("PHS"),
        "hydro": table.primary_attribution_class.eq("STORAGE_HYDRO_OPPORTUNITY_VALUE") & table.setter_technology.eq("Hydro"),
        "P2X": table.primary_attribution_class.eq("P2X_FLEXIBLE_DEMAND"),
        "constrained_thermal": table.primary_attribution_class.eq("UC_RAMP_CONSTRAINED"),
        "simple_market_coupling": table.primary_attribution_class.eq("MARKET_COUPLING") & table.root_group_count.eq(1),
        "multi_root_market_coupling": table.primary_attribution_class.eq("MARKET_COUPLING") & table.root_group_count.gt(1),
        "heterogeneous_market_coupling": table.primary_attribution_class.eq("MARKET_COUPLING") & table.candidate_technology_count.gt(1),
        "local_multiple": table.primary_attribution_class.eq("MULTIPLE_CANDIDATES"),
    }
    for label, mask in masks.items():
        subset = table.loc[mask].sort_values(["root_count", "snapshot", "zone"], kind="stable")
        if len(subset):
            row = subset.iloc[0].to_dict(); row["sample_type"] = label; selected.append(row)
    samples = pd.DataFrame(selected)
    detail, links = [], []
    edges = {e['id']: e for e in evidence['edges']}
    snap_index = {s:i for i,s in enumerate(evidence['snapshots'])}
    for sample in selected:
        block = audit.loc[audit.snapshot.eq(sample['snapshot']) & audit.target_zone.eq(sample['zone'])].copy()
        block['sample_type'] = sample['sample_type']; detail.append(block)
        i = snap_index[sample['snapshot']]
        paths = {edge for path in block.coupling_path.map(json.loads) for edge in path}
        for key in sorted(paths):
            e = edges[key]
            links.append(dict(sample_type=sample['sample_type'],snapshot=sample['snapshot'],target_zone=sample['zone'],
                              interface_id=key,from_bus=e['a'],to_bus=e['b'],
                              from_price=float(evidence['prices'].at[sample['snapshot'],e['a']]),
                              to_price=float(evidence['prices'].at[sample['snapshot'],e['b']]),
                              strict_net_interior=bool(e['interior'][i]),max_bound_dual_EUR_MWh=float(e['dualmax'][i]),
                              KKT_residual_EUR_MWh=float(e['residual'][i]),
                              price_separating=bool(e['price_separating'][i]),
                              coupling_admissible=coupling_edge_is_open(e['interior'][i],e['dualmax'][i],e['residual'][i])))
    return samples, pd.concat(detail,ignore_index=True) if detail else pd.DataFrame(), pd.DataFrame(links)


def attribute(evidence, year, scenario, tolerance=TOLERANCE, *, retain_audit=True, allow_zero_scarcity_bounds=True):
    """One unique zone-hour row; audit every qualifying local/remote root."""
    snapshots, prices = evidence["snapshots"], evidence["prices"]
    rows, audit = [], []
    for t, snapshot in enumerate(snapshots):
        local = defaultdict(list)
        for root in evidence["roots"]:
            admissible = root["admissible_position"][t] if allow_zero_scarcity_bounds else root["interior"][t]
            mechanism = local_mechanism(admissible, root["lower"][t], root["upper"][t],
                                        root["residual"][t], root["gradient"][t], root["mechanism"], tolerance)
            if mechanism is None:
                continue
            c = {k: root[k] for k in ("asset_id", "component", "zone", "technology", "carrier", "mechanism", "position")}
            c.update({k: float(root[k][t]) for k in ("p", "cost", "opportunity", "recon", "residual")})
            c["mechanism"] = mechanism
            for key in ("operating_lower", "operating_upper", "native_power", "lower", "upper", "gradient", "ramp_up_dual", "ramp_down_dual", "efficiency", "objective_weight", "fixed_status"):
                c[key] = float(root[key][t])
            if root["component"] == "Generator" and not root["interior"][t]:
                c["position"] = "upper_operating_bound_zero_scarcity" if abs(c["native_power"]-c["operating_upper"]) <= POWER_TOLERANCE else "lower_operating_bound_zero_scarcity"
            local[c["zone"]].append(c)
        graph = defaultdict(list); binding = defaultdict(list)
        for e in evidence["edges"]:
            if coupling_edge_is_open(e["interior"][t], e["dualmax"][t], e["residual"][t], tolerance):
                scale, offset = float(e["scale"][t]), float(e["offset"][t])
                graph[e["a"]].append((e["b"], e["id"], scale, offset))
                graph[e["b"]].append((e["a"], e["id"], 1 / scale, -offset / scale))
            elif e["price_separating"][t]:
                binding[e["a"]].append(e["id"]); binding[e["b"]].append(e["id"])
        for zone in ZONES:
            native = "CNOR" if zone == "CNORD" else zone
            candidates = local[native]; remote = False
            if not candidates:
                remote = True; candidates = []
                queue = deque([(native, [], 1., 0.)]); seen = {native}
                while queue:
                    bus, path, scale, offset = queue.popleft()
                    if bus != native:
                        for c in local[bus]:
                            c = dict(c); c["path"] = path
                            # Current root-bus price = scale * target-zone price + offset.
                            c["recon"] = (c["recon"] - offset) / scale
                            c["residual"] = c["recon"] - float(prices.at[snapshot, native])
                            if abs(c["residual"]) <= tolerance:
                                candidates.append(c)
                    for nxt, interface, step_scale, step_offset in graph[bus]:
                        if nxt not in seen:
                            seen.add(nxt); queue.append((nxt, path + [interface], step_scale * scale, step_scale * offset + step_offset))
            klass, groups = classify_candidates(candidates, remote=remote)
            single = groups[0] if len(groups) == 1 else []
            identities = [c["asset_id"] for c in single]
            # Full lists express equivalent candidates; no arbitrary representative unit.
            identity = identities[0] if len(identities) == 1 else json.dumps(identities) if identities else None
            def common(key):
                vals = {c[key] for c in single}
                return next(iter(vals)) if len(vals) == 1 else None
            reconstructed = float(np.mean([c["recon"] for c in candidates])) if candidates else None
            if retain_audit:
                for c in candidates:
                    audit.append({"model_year": year, "scenario": scenario, "snapshot": snapshot, "target_zone": zone,
                                  "remote": remote, "coupling_path": json.dumps(c.get("path", [])), **c})
            rows.append(dict(model_year=year, scenario=scenario, snapshot=snapshot, zone=zone,
                represented_hours=float(evidence["physical_hours"][t]), zonal_price_EUR_MWh=float(prices.at[snapshot, native]),
                primary_attribution_class=klass, setter_mechanism="MARKET_COUPLING" if remote and candidates else common("mechanism") if single else None,
                setter_technology=common("technology") if single and not remote else None,
                setter_component_type=common("component") if single and not remote else None,
                setter_asset_id=identity if not remote else None,
                setter_asset_bus=normalize_zone(common("zone")) if single and not remote else None,
                setter_dispatch_MW=sum(c["p"] for c in single) if single and not remote and common("position") is not None else None,
                setter_position=common("position") if single and not remote else None,
                setter_marginal_cost_EUR_MWh=common("cost") if single and not remote else None,
                setter_opportunity_value_EUR_MWh=common("opportunity") if single and not remote else None,
                reconstructed_price_EUR_MWh=reconstructed,
                stationarity_residual_EUR_MWh=reconstructed-float(prices.at[snapshot, native]) if reconstructed is not None else None,
                candidate_count=len(candidates), candidate_technology_count=len({c["technology"] for c in candidates}),
                local_candidate_count=0 if remote else len(candidates), remote_candidate_count=len(candidates) if remote else 0,
                root_count=len({(c["component"],c["zone"],c["asset_id"]) for c in candidates}),
                root_group_count=len(groups),
                remote_root_zone_count=len({c["zone"] for c in candidates}) if remote else 0,
                remote_root_zones=json.dumps(sorted({normalize_zone(c["zone"]) for c in candidates})) if remote and candidates else None,
                remote_root_technologies=json.dumps(sorted({c["technology"] for c in candidates})) if remote and candidates else None,
                remote_root_assets=json.dumps(sorted({c["asset_id"] for c in candidates})) if remote and candidates else None,
                zero_scarcity_bound_candidate_count=sum("bound_zero_scarcity" in c["position"] for c in candidates),
                constraint_context=json.dumps({"ramp_uc_candidate_count":sum(c["mechanism"]=="UC_RAMP_CONSTRAINED" for c in candidates),
                                               "zero_scarcity_bound_candidate_count":sum("bound_zero_scarcity" in c["position"] for c in candidates)}),
                remote_root_zone=normalize_zone(common("zone")) if single and remote else None,
                remote_root_asset_id=identity if remote else None,
                remote_root_technology=common("technology") if single and remote else None,
                coupling_path=json.dumps(sorted({tuple(c.get("path", [])) for c in candidates})) if candidates and remote else None,
                coupling_hops=max(len(c.get("path", [])) for c in candidates) if candidates and remote else None,
                binding_price_separating_interface=bool(binding[native]), binding_interface_ids=json.dumps(binding[native]),
                attribution_status="INDETERMINATE" if not candidates else "KKT_COUPLED_MULTIPLE_ROOTS" if remote and len(groups)>1 else "AMBIGUOUS_LOCAL" if len(groups)>1 else "KKT_IDENTIFIED",
                unresolved_reason="No admissible local or economically coupled remote root" if not candidates else "Distinct local candidate mechanisms/technologies" if len(groups)>1 and not remote else None))
    result = pd.DataFrame(rows)
    validate_structure(result)
    a = pd.DataFrame(audit)
    if "path" in a:
        a = a.drop(columns="path")
    return result, a


def validate_structure(table):
    if len(table) != 61320 or set(table.zone) != set(ZONES) or table.duplicated(["snapshot", "zone"]).any():
        fail("REQUIRED_PRIMAL_DATA_MISSING", "attribution requires 61320 unique 7-zone hourly rows")
    if not table.groupby("zone").size().eq(8760).all() or not table.primary_attribution_class.isin(CLASSES).all():
        fail("REQUIRED_PRIMAL_DATA_MISSING", "zone chronology or ontology incomplete")
    expected = pd.date_range("2019-01-01", "2020-01-01", freq="h", inclusive="left")
    if not pd.DatetimeIndex(table.snapshot.unique()).sort_values().equals(expected):
        fail("REQUIRED_PRIMAL_DATA_MISSING", "not the complete saved 2019 chronology")


def run_case(year, scenario, output, *, frozen_methodology=None, strict_reference=None):
    """Reusable, explicit opt-in entry point; never invoked by normal reporting."""
    import pypsa
    from .uc2_postprocess import no_solver_calls
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    if frozen_methodology is not None:
        freeze = json.loads(Path(frozen_methodology).read_text())
        if freeze.get('status') != 'FROZEN_AFTER_BASE_PASS' or freeze.get('methodology') != RULES or freeze.get('implementation_sha256') != sha256_file(Path(__file__)):
            fail('OUT_OF_SAMPLE_METHOD_GAP', 'Frozen Base methodology/implementation changed')
    label = f"{year}_{scenario}"
    prior_qa = output / f"marginal_setter_QA_{label}.json"
    if prior_qa.exists() and json.loads(prior_qa.read_text()).get('methodology',{}).get('version') != VERSION:
        fail('PROTECTED_ARTIFACT_MUTATION', 'Use a new versioned directory; never overwrite strict reference outputs')
    sources = discover_sources(year, scenario)
    with no_solver_calls() as guard:
        n = pypsa.Network(sources["network"])
        accepted = pd.read_csv(sources["prices"], index_col=0, parse_dates=True)
        native_zones = ["CNOR" if z == "CNORD" else z for z in ZONES]
        if not accepted.index.equals(pd.DatetimeIndex(n.snapshots)):
            fail("SOURCE_AUTHORITY_CONFLICT", "accepted price chronology differs from solved LP")
        delta = accepted[native_zones].to_numpy() - n.buses_t.marginal_price[native_zones].to_numpy()
        if not np.isfinite(delta).all() or np.abs(delta).max() > 1e-8:
            fail("SOURCE_AUTHORITY_CONFLICT", "accepted price table differs from solved LP balance duals")
        import xarray as xr
        with xr.open_dataset(sources["network"]) as saved:
            persisted = "global_constraints_mu" in saved
        evidence = build_evidence(n, pd.read_csv(sources["interfaces"]), global_duals_persisted=persisted)
        table, audit = attribute(evidence, year, scenario)
        sensitivity = {}
        for tol in (1e-7, 1e-5):
            other, _ = attribute(evidence, year, scenario, tol, retain_audit=False)
            changed = int((other.primary_attribution_class != table.primary_attribution_class).sum())
            sensitivity[str(tol)] = {"changed_zone_hours": changed, "class_counts": other.primary_attribution_class.value_counts().to_dict()}
            if changed > 0:
                fail("MATERIAL_TOLERANCE_INSTABILITY", f"{scenario}: {changed} changed classes at {tol}")
        samples, sample_candidates, sample_interfaces = representative_evidence(table,audit,evidence)
        samples.to_csv(output/f'representative_zone_hours_{label}.csv',index=False)
        sample_candidates.to_csv(output/f'representative_KKT_evidence_{label}.csv',index=False)
        sample_interfaces.to_csv(output/f'representative_coupling_evidence_{label}.csv',index=False)
        transition_info = None
        if strict_reference is not None:
            old = pd.read_parquet(strict_reference).set_index(['snapshot','zone'])
            current = table.set_index(['snapshot','zone'])
            intermediate,_ = attribute(evidence,year,scenario,retain_audit=False,allow_zero_scarcity_bounds=False)
            mid = intermediate.set_index(['snapshot','zone'])
            if not old.index.equals(current.index) or not mid.index.equals(current.index):
                fail('SOURCE_AUTHORITY_CONFLICT','Strict/current transition chronology mismatch')
            transitions = pd.DataFrame({'strict_class':old.primary_attribution_class,
                                       'coupling_only_class':mid.primary_attribution_class,
                                       'strengthened_class':current.primary_attribution_class}).reset_index()
            transitions.groupby(['strict_class','coupling_only_class','strengthened_class']).size().rename('zone_hours').reset_index().to_csv(output/f'classification_transitions_{label}.csv',index=False)
            transitions.to_parquet(output/f'classification_transitions_hourly_{label}.parquet',index=False)
            transition_info = {'strict_reference_sha256':sha256_file(Path(strict_reference)),
                               'coupling_only_changed_zone_hours':int((old.primary_attribution_class!=mid.primary_attribution_class).sum()),
                               'zero_scarcity_bound_changed_zone_hours':int((mid.primary_attribution_class!=current.primary_attribution_class).sum()),
                               'total_changed_zone_hours':int((old.primary_attribution_class!=current.primary_attribution_class).sum())}
        table.to_parquet(output / f"marginal_setter_attribution_{label}.parquet", index=False)
        table.to_csv(output / f"marginal_setter_attribution_{label}.csv", index=False)
        audit.to_parquet(output / f"marginal_setter_candidates_{label}.parquet", index=False)
        summary = table.groupby(["zone", "primary_attribution_class", "setter_technology"], dropna=False).represented_hours.sum().reset_index()
        summary["share"] = summary.represented_hours / summary.groupby("zone").represented_hours.transform("sum")
        summary.to_csv(output / f"marginal_setter_summary_{label}.csv", index=False)
        qa = {"status": "PASS_KKT_AND_STRUCTURE_PENDING_BASE_SEMANTIC_REVIEW" if frozen_methodology is None else "PASS_FROZEN_METHOD_ATTRIBUTION", "methodology": RULES,
              "source_hashes": sources["hashes"], "job_id": sources["job_id"], "fixed_commitment_milp_sha256": sources["fixed_commitment_milp_sha256"],
              "rows": len(table), "snapshots": len(n.snapshots), "zones": list(ZONES),
              "accepted_price_max_error_EUR_MWh": float(np.abs(delta).max()),
              "class_counts": table.primary_attribution_class.value_counts().to_dict(),
              "KKT_residuals": evidence["residuals"], "attribution_residual": _residual_stats(table.stationarity_residual_EUR_MWh),
              "P2X_shadows": evidence["P2X_shadows"], "tolerance_sensitivity": sensitivity,
              "solver_invocations": guard["solver_invocations"], "network_modifications": 0,
              "price_formation_primal_authority": "FIXED_COMMITMENT_PRICE_LP_ONLY_NOT_UC_PHYSICAL_REPORTING"}
        qa['direct_technology_composition'] = table.loc[table.primary_attribution_class.eq('DIRECT_MARGINAL_TECHNOLOGY')].setter_technology.value_counts().to_dict()
        qa['unambiguous_storage_hydro_composition'] = table.loc[table.primary_attribution_class.eq('STORAGE_HYDRO_OPPORTUNITY_VALUE')].setter_technology.value_counts().to_dict()
        qa['ramp_context_residual'] = _residual_stats(table.loc[table.primary_attribution_class.eq('UC_RAMP_CONSTRAINED'),'stationarity_residual_EUR_MWh'])
        qa['remote_root_multiplicity'] = table.loc[table.primary_attribution_class.eq('MARKET_COUPLING'),'root_group_count'].value_counts().sort_index().to_dict()
        qa['zero_scarcity_bound_zone_hours'] = int(table.zero_scarcity_bound_candidate_count.gt(0).sum())
        qa['strict_reference_transitions'] = transition_info
        qa['methodology_frozen'] = frozen_methodology is not None
        qa['frozen_methodology_sha256'] = sha256_file(Path(frozen_methodology)) if frozen_methodology else None
        qa["implementation_sha256"] = sha256_file(Path(__file__))
        qa["artifact_hashes"] = {p.name: sha256_file(p) for p in output.glob(f"*{label}.*")
                                 if p.suffix in (".parquet", ".csv")}
        (output / f"marginal_setter_QA_{label}.json").write_text(json.dumps(qa, indent=2), encoding="utf-8")
    return table, audit, qa

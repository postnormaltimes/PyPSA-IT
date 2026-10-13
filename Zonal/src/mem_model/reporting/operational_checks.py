"""Numerical residual checks on saved Zonal results; no model construction.

The formulas follow installed PyPSA 1.2.3 fixed-capacity operational constraints.
They supplement, rather than replace, interface and report/source reconciliation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _output(n, component, attribute):
    names = getattr(n, component).index
    frame = getattr(getattr(n, component + "_t"), attribute)
    if not len(names):
        return pd.DataFrame(index=n.snapshots, columns=names, dtype=float)
    if (not frame.index.equals(n.snapshots) or frame.columns.has_duplicates or
            not names.isin(frame.columns).all()):
        raise RuntimeError(f"OPERATIONAL_DATA_ALIGNMENT: {component}.{attribute}")
    result = frame.loc[:, names]
    if not np.isfinite(result.to_numpy(dtype=float)).all():
        raise RuntimeError(f"OPERATIONAL_DATA_FINITE: {component}.{attribute}")
    return result


def _dense(n, component, attribute):
    names = getattr(n, component).index
    table = getattr(n, component)
    result = pd.DataFrame(np.broadcast_to(table[attribute].to_numpy(),
                                         (len(n.snapshots), len(names))),
                          index=n.snapshots, columns=names).copy()
    dynamic = getattr(n, component + "_t").get(attribute)
    if dynamic is not None and len(dynamic.columns):
        if not dynamic.index.equals(n.snapshots) or dynamic.columns.has_duplicates:
            raise RuntimeError(f"OPERATIONAL_DATA_ALIGNMENT: {component}.{attribute}")
        for name in names.intersection(dynamic.columns):
            result[name] = dynamic[name]
    return result.astype(float)


def input_scope(n):
    """Check only the component semantics represented by the current Zonal model."""
    if n.snapshots.empty or isinstance(n.snapshots, pd.MultiIndex) or not n.snapshots.is_unique or n.snapshots.hasnans:
        raise RuntimeError("OPERATIONAL_SCOPE: single chronological snapshot index required")
    if len(n.snapshots) > 1 and not np.all(np.diff(pd.DatetimeIndex(n.snapshots).asi8) == 3600 * 10**9):
        raise RuntimeError("OPERATIONAL_DATA_ALIGNMENT: contiguous hourly chronology required")
    for component in ("lines", "transformers", "storage_units", "processes"):
        if len(getattr(n, component, [])):
            raise RuntimeError(f"OPERATIONAL_SCOPE: unsupported component {component}")
    for component, nominal in (("generators", "p_nom"), ("links", "p_nom"), ("stores", "e_nom")):
        table = getattr(n, component)
        if (table[nominal + "_extendable"].astype(bool).any() or
                not np.isfinite(table[nominal]).all() or table[nominal].lt(0).any() or
                not table.active.astype(bool).all()):
            raise RuntimeError(f"OPERATIONAL_SCOPE: fixed active capacities required in {component}")
    if n.links.committable.astype(bool).any() or n.generators.p_nom_mod.gt(0).any():
        raise RuntimeError("OPERATIONAL_SCOPE: modular/Link commitment is not a Zonal input contract")
    for column in n.links.columns:
        if column.startswith("bus") and column not in ("bus0", "bus1") and n.links[column].astype(str).ne("").any():
            raise RuntimeError("OPERATIONAL_SCOPE: additional Link terminals")
        if column.startswith("delay") and n.links[column].ne(0).any():
            raise RuntimeError("OPERATIONAL_SCOPE: delayed Link terminals")
    weights = n.snapshot_weightings
    if not weights.index.equals(n.snapshots) or not np.isfinite(weights.to_numpy()).all() or weights.le(0).any().any():
        raise RuntimeError("OPERATIONAL_DATA_ALIGNMENT: positive finite snapshot weights required")
    return {"snapshots": len(n.snapshots), "buses": len(n.buses), "stores": len(n.stores),
            "generators": len(n.generators), "links": len(n.links),
            "committable_units": int(n.generators.committable.sum())}


def verify_operational_residuals(n, *, power_tolerance=1e-4, energy_tolerance=1e-4,
                                 commitment_tolerance=1e-7,
                                 objective_absolute_tolerance=1e-4,
                                 objective_relative_tolerance=1e-9):
    """Validate native balances, bounds, recurrence, UC constraints and objective.

    Store.p is internal discharge-positive power. Link p0/p1 are terminal
    withdrawals; Generator.sign applies to bus injection, not its MW bounds.
    Raw startup/shutdown events are lower-bound inequalities, not necessarily
    unique physical transition counts. Native terminal dwell constraints are
    truncated by the finite horizon; no extra terminal condition is imposed.
    """
    scope = input_scope(n)
    checks = []

    def check(name, values, unit, tolerance, *, equality=False):
        array = np.asarray(values, dtype=float)
        if not np.isfinite(array).all():
            raise RuntimeError(f"OPERATIONAL_DATA_FINITE: {name}")
        residual = np.abs(array) if equality else np.maximum(array, 0)
        maximum = float(residual.max()) if residual.size else 0.0
        checks.append({"check": name, "maximum_residual": maximum, "unit": unit,
                       "tolerance": tolerance, "observations": int(array.size),
                       "violations": int((residual > tolerance).sum()),
                       "passed": maximum <= tolerance})

    gp = _output(n, "generators", "p")
    lp = _output(n, "loads", "p")
    sp = _output(n, "stores", "p")
    se = _output(n, "stores", "e")
    p0 = _output(n, "links", "p0")
    p1 = _output(n, "links", "p1")

    injections = pd.DataFrame(0., index=n.snapshots, columns=n.buses.index)
    for component, frame in (("generators", gp), ("loads", lp), ("stores", sp)):
        static = getattr(n, component)
        for name, row in static.iterrows():
            if row.bus not in injections:
                raise RuntimeError("OPERATIONAL_SCOPE: component refers to absent bus")
            injections[row.bus] += float(row.sign) * frame[name]
    for name, row in n.links.iterrows():
        injections[row.bus0] -= p0[name]
        injections[row.bus1] -= p1[name]
    check("native_all_bus_balance", injections, "MW_bus_carrier", power_tolerance, equality=True)
    check("load_setpoint", lp - _dense(n, "loads", "p_set"), "MW", power_tolerance, equality=True)
    efficiency = _dense(n, "links", "efficiency")
    check("link_terminal_conversion", p1 + efficiency * p0, "MW_output_carrier", power_tolerance, equality=True)

    com = n.generators.index[n.generators.committable.astype(bool)]
    status = pd.DataFrame(1., index=n.snapshots, columns=n.generators.index)
    if len(com):
        for attr in ("status", "start_up", "shut_down"):
            frame = getattr(n.generators_t, attr)
            if (not frame.index.equals(n.snapshots) or not com.isin(frame.columns).all() or
                    not np.isfinite(frame.loc[:, com].to_numpy()).all()):
                raise RuntimeError(f"OPERATIONAL_DATA_ALIGNMENT: generators.{attr}")
            check("UC_" + attr + "_integrality", frame.loc[:, com] - frame.loc[:, com].round(),
                  "binary", commitment_tolerance, equality=True)
            check("UC_" + attr + "_unit_interval", np.maximum(-frame.loc[:, com], frame.loc[:, com] - 1),
                  "binary", commitment_tolerance)
        status.loc[:, com] = n.generators_t.status.loc[:, com]
        prev = status.loc[:, com].shift(1)
        prev.iloc[0] = n.generators.loc[com, "up_time_before"].gt(0).astype(float).to_numpy()
        su = n.generators_t.start_up.loc[:, com]
        sd = n.generators_t.shut_down.loc[:, com]
        check("UC_start_transition", status.loc[:, com] - prev - su, "status", commitment_tolerance)
        check("UC_shutdown_transition", prev - status.loc[:, com] - sd, "status", commitment_tolerance)
        for name in com:
            row = n.generators.loc[name]
            for attr, event, target in (("min_up_time", su, status[name]),
                                        ("min_down_time", sd, 1 - status[name])):
                length = int(row[attr])
                if length:
                    check("UC_" + attr + ":" + name,
                          event[name].rolling(length, min_periods=1).sum().iloc[1:] - target.iloc[1:],
                          "status", commitment_tolerance)
            for before, dwell, target in (("up_time_before", "min_up_time", 1.),
                                           ("down_time_before", "min_down_time", 0.)):
                if int(row[before]) > 0:
                    required = max(0, int(row[dwell]) - int(row[before]))
                    check("UC_initial_" + before + ":" + name, status[name].iloc[:required] - target,
                          "status", commitment_tolerance, equality=True)

    lower = _dense(n, "generators", "p_min_pu").mul(n.generators.p_nom, axis=1) * status
    upper = _dense(n, "generators", "p_max_pu").mul(n.generators.p_nom, axis=1) * status
    check("generator_lower_bound", lower - gp, "MW", power_tolerance)
    check("generator_upper_bound", gp - upper, "MW", power_tolerance)
    for comp, power in (("generators", gp), ("links", p0)):
        table = getattr(n, comp)
        setpoint = _dense(n, comp, "p_set")
        specified = setpoint.notna()
        check(comp + "_forced_setpoint", (power - setpoint).where(specified, 0), "MW", power_tolerance, equality=True)
        st = status if comp == "generators" else pd.DataFrame(1., index=n.snapshots, columns=table.index)
        before_status = st.shift(1)
        before_status.iloc[0] = table.up_time_before.gt(0).astype(float).to_numpy()
        before_power = power.shift(1)
        initial_power = table.p_init.where(table.up_time_before.gt(0), 0.)
        before_power.iloc[0] = initial_power.fillna(0).to_numpy()
        difference = power - before_power
        committed = table.committable.astype(bool)
        for direction, limit, initial_limit in (("up", "ramp_limit_up", "ramp_limit_start_up"),
                                                ("down", "ramp_limit_down", "ramp_limit_shut_down")):
            ordinary = _dense(n, comp, limit)
            event_limit = _dense(n, comp, initial_limit)
            applies = ordinary.notna() | event_limit.notna()
            applies.iloc[0] &= initial_power.notna()
            if direction == "up":
                bound = ordinary.fillna(1) * before_status
                if committed.any():
                    bound += event_limit.fillna(1) * (st - before_status)
                residual = difference - bound.mul(table.p_nom, axis=1)
            else:
                bound = ordinary.fillna(1) * st
                if committed.any():
                    bound += event_limit.fillna(1) * (before_status - st)
                residual = -difference - bound.mul(table.p_nom, axis=1)
            check(comp + "_ramp_" + direction, residual.where(applies, 0), "MW", power_tolerance)

    for suffix, power in (("lower", _dense(n, "links", "p_min_pu").mul(n.links.p_nom, axis=1) - p0),
                          ("upper", p0 - _dense(n, "links", "p_max_pu").mul(n.links.p_nom, axis=1))):
        check("link_" + suffix + "_bound", power, "MW_input_carrier", power_tolerance)
    check("store_lower_bound", _dense(n, "stores", "e_min_pu").mul(n.stores.e_nom, axis=1) - se,
          "MWh_internal", energy_tolerance)
    check("store_upper_bound", se - _dense(n, "stores", "e_max_pu").mul(n.stores.e_nom, axis=1),
          "MWh_internal", energy_tolerance)
    retained = (1 - _dense(n, "stores", "standing_loss")).pow(n.snapshot_weightings.stores, axis=0)
    previous_energy = se.shift(1)
    for name, row in n.stores.iterrows():
        previous_energy.at[n.snapshots[0], name] = se[name].iloc[-1] if row.e_cyclic else float(row.e_initial)
        if not row.e_cyclic:
            retained.at[n.snapshots[0], name] = 1.  # Native RHS uses e_initial directly.
    recurrence = se - retained * previous_energy + sp.mul(n.snapshot_weightings.stores, axis=0)
    check("store_energy_recurrence", recurrence, "MWh_internal", energy_tolerance, equality=True)

    terms = {}
    weights = n.snapshot_weightings.objective
    for comp, power in (("generators", gp), ("links", p0), ("stores", sp)):
        linear = power * _dense(n, comp, "marginal_cost")
        quadratic = power.pow(2) * _dense(n, comp, "marginal_cost_quadratic")
        if not np.isfinite(linear.to_numpy()).all() or not np.isfinite(quadratic.to_numpy()).all():
            raise RuntimeError(f"OPERATIONAL_DATA_FINITE: {comp} operating costs")
        terms[comp + "_operating_EUR"] = float((linear + quadratic).mul(weights, axis=0).sum().sum())
    for attr in ("start_up", "shut_down"):
        terms[attr + "_EUR"] = float((getattr(n.generators_t, attr).loc[:, com] *
                                       n.generators.loc[com, attr + "_cost"]).sum().sum()) if len(com) else 0.
    terms["stand_by_EUR"] = float((status.loc[:, com] * _dense(n, "generators", "stand_by_cost").loc[:, com])
                                  .mul(weights, axis=0).sum().sum()) if len(com) else 0.
    objective = sum(terms.values())
    reference = float(n.objective)
    objective_tolerance = max(objective_absolute_tolerance, objective_relative_tolerance * max(abs(reference), 1.))
    check("objective_reconstruction", [objective - reference], "EUR", objective_tolerance, equality=True)
    result = {"status": "PASS" if all(row["passed"] for row in checks) else "FAIL",
              "scope": scope, "checks": checks, "objective_terms": terms,
              "reconstructed_objective_EUR": objective, "network_objective_EUR": reference,
              "optimization_model_constructed": False, "solver_invocations": 0}
    if result["status"] != "PASS":
        failure = RuntimeError("OPERATIONAL_RESIDUAL_FAIL: " + str([row for row in checks if not row["passed"]]))
        failure.result = result
        raise failure
    return result

# MEM Slow deterministic capacity-calibration specification

## Status

`RUNTIME GATE — SPECIFIED, NOT EXECUTED`

This is an outer-loop experiment design for later use. It does not activate PyPSA capacity expansion, does not alter the accepted v2.9 workbook, and does not claim to reproduce probabilistic adequacy or LOLE.

## Preconditions

Do not execute the search until all of the following are frozen:

- GEM-reconciled hybrid 2026 physical thermal stack;
- evidenced 2040/2050 RETAIN/RETIRE/REFURBISH/CONVERT/CCS/NEW_BUILD candidate register;
- complete annual load, VRE, availability, hydro/storage and commercial-transfer inputs;
- external-interface and price treatment;
- load-shedding penalty and solver tolerances;
- calendar/weather/hydrology year;
- user-approved deterministic acceptance metric.

## Fixed-capacity outer loop

Each PyPSA solve remains a fixed-capacity economic-dispatch problem. Candidate fleets are changed outside the optimizer:

1. Freeze the Base physical fleet.
2. Construct Slow demand, Slow grid and the accepted minus-20% PV/wind/BESS portfolio.
3. Run the fixed-capacity annual chronological dispatch.
4. Record ENS, load-shedding hours, peak shedding, residual-load peaks, storage depletion, saturated directional Links, marginal prices, dispatch and curtailment.
5. Select the next defensible candidate tranche from the physical fleet register.
6. Retain or add that tranche outside the optimizer.
7. Rebuild the fixed-capacity input and rerun.
8. Search the tested candidate set for the smallest fleet that passes the approved deterministic criterion.
9. Compare the model-derived incremental programmable MW with 18.9449 GW, without imposing equality.

## Candidate ordering

Candidate ordering must be generated from the reconciled asset/band register rather than arbitrary national percentages. Each candidate is a whole evidenced unit or a documented residual zone × technology × fuel × cost band.

The ranking logic is:

1. **Already operating and source-supported to remain available** in the stressed year.
2. **Existing assets with a project retention assumption**, clearly separated from source-supported continued operation.
3. **Known refurbishment, conversion or capacity-market/new-build assets** with credible commissioning evidence.
4. **Low-CF peaking or adequacy tranches** already present in the physical stack.
5. **Replacement/new-build candidates** only where an evidenced technology and zone are available.
6. **Unresolved candidates** are tested only in sensitivity runs and remain labelled unresolved.

Within a rank, select the tranche that addresses the observed constrained zone or interface, then compare incremental production cost, emissions and scarcity reduction. Do not rank solely by generic technical lifetime. Do not invent hydrogen turbines.

## Proposed deterministic acceptance metrics for user approval

The following is a proposed, not yet frozen, acceptance set:

### Primary criterion

- annual ENS no greater than `0.001%` of annual modelled electricity demand;
- no individual hour with load shedding above `0.1%` of the national annual peak load;
- no more than 3 hours with load shedding above 1 MW.

### Feasibility and integrity conditions

- all hourly energy-balance residuals within the approved numerical tolerance;
- complete 8,760/8,784-hour chronology with no duplicate/missing snapshot;
- fixed capacities only; no extendable generators, Links, Stores or StorageUnits;
- storage conservation and approved terminal-state condition satisfied;
- reported scarcity is not caused by a data-quality failure, unintended unavailable link, or auxiliary external-market bound;
- internal commercial Links use `efficiency=1.0` under the DDS demand perimeter containing network losses;
- external-market objective baseline is removed in settlement reporting.

### Sensitivity criterion

After identifying the smallest passing tested fleet, rerun at least:

- one stricter zero-material-ENS threshold, such as ENS at solver-tolerance scale only;
- one approved alternative weather/hydrology year when available;
- one availability sensitivity for major programmable tranches;
- one external-transfer sensitivity.

These sensitivities describe deterministic robustness only. They do not estimate probabilistic capacity credit, formal LOLE, N-1 security or reserve deliverability.

## Search output contract

Every tested fleet must record:

`test_id`, `parent_test_id`, `scenario_id`, `candidate_tranche_id`, `action`, `incremental_net_MW`, `zone`, `technology`, `fuel`, `evidence_status`, `annual_ENS_MWh`, `scarcity_hours_gt_1MW`, `peak_shed_MW`, `peak_residual_load_MW`, `minimum_storage_state_MWh`, `saturated_link_hours`, `production_cost_EUR`, `net_external_trade_cost_EUR`, `emissions_tCO2`, `acceptance_result`, `notes`.

The final comparison reports:

- model-derived retained/additional programmable capacity by zone and technology;
- the 18.9449 GW CDP-derived benchmark;
- the difference as a descriptive comparison only;
- the exact deterministic criterion used;
- a prominent statement that no probabilistic adequacy claim is made.

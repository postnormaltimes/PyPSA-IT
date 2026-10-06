# Methodology

The seven-zone model uses fixed capacities and 8,760 hourly snapshots for each
2040/2050 scenario. It represents operational constraints rather than endogenous
generation or network investment.

Renewable availability uses 2019 weather. Wind resource preprocessing allocates
fixed zonal MW among eligible native cells using site economics and physical
potential, then aggregates to the existing zonal generators. The documented
annual and onshore seasonal transformations are retained in final_v4. Solar
keeps the accepted rooftop/utility profiles. Siting costs do not enter dispatch
marginal costs.

Thermal generation uses heterogeneous synthetic units with fixed parent MW and
native PyPSA commitment, ramp, minimum-duration and startup-cost semantics.
Capacity-weighted heat rate and marginal-cost reconciliation are maintained.

Natural hydro uses independent zonal runoff shapes with fixed annual water
budgets. RoR generation is limited by turbine MW; excess inflow is bypass.
Reservoir and mixed pumped-hydro operation use water Stores with explicit
forced inflow, turbine, pump and spill flows. Pure pumped hydro receives no
natural inflow. Annual cyclic water states impose no net annual water change.
No arbitrary exogenous water-value marginal cost is added.

Internal interfaces use signed Links where directional equivalence is exact,
including asymmetric limits. Distinct physical corridors and the Corsican
topology are retained. External markets use fixed boundary prices. Rigid
demand retains its electricity-demand geography; flexible P2X has separate
zonal MW and annual-energy equalities.

The UC MILP is the physical authority. A fixed-commitment LP supplies prices
and native Store energy-balance duals, without using MILP duals as prices.
See [scenario definitions](SCENARIO_GOVERNANCE.md) and
[validation limitations](releases/VALIDATION.md).

# MEM v2.9 independent repository specification

## Boundary

The eventual repository is a standalone PyPSA application. PyPSA is a runtime dependency; PyPSA-IT and PyPSA-Eur are not. Reviewed PyPSA-IT patterns may be reimplemented with attribution, but no runtime import, path reference or implicit dependency on its networks, configuration, Snakefiles or solved outputs is permitted.

The repository should be created only after the frozen data contract is accepted. This phase specifies the boundary; it does not build or run the full 2040/2050 model.

## Proposed layout

```text
mem-italy-market-model/
├── README.md
├── pyproject.toml
├── environment.yml                 # optional lock companion
├── config/
│   ├── model.yaml
│   ├── scenarios.yaml
│   ├── solver.yaml
│   └── logging.yaml
├── data/
│   ├── raw/                         # immutable source deliveries
│   ├── derived/                     # reproducible transformations
│   ├── frozen/                      # versioned solver input releases
│   │   └── <release_id>/
│   └── provenance/                  # source, derivation and release manifests
├── src/mem_model/
│   ├── __init__.py
│   ├── contracts.py
│   ├── build_network.py
│   ├── add_generation.py
│   ├── add_storage.py
│   ├── add_external_markets.py
│   ├── solve.py
│   ├── validate.py
│   ├── accounting.py
│   └── export_results.py
├── scripts/
│   ├── acquire_terna_thermal.py
│   ├── reconcile_gem_thermal.py
│   ├── freeze_inputs.py
│   └── run_scenario.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contracts/
│   └── fixtures/
├── notebooks/                       # exploration only; never production logic
├── results/
│   └── <release_id>/<scenario_id>/
└── docs/
    ├── methodology/
    ├── data_dictionary/
    └── decisions/
```

## Module responsibilities

### `contracts.py`

Load and validate all ten frozen input files and provenance manifests. Reject schema, key, domain, timezone, calendar, evidence and reconciliation failures before network construction. It is the only module that reads solver-facing tables directly.

### `build_network.py`

Create a fresh `pypsa.Network`, attach the complete UTC snapshot index, add exactly seven Italian buses, attach rigid loads via `p_set`, and create fixed directional internal transport `Link` components. It must not add Italian physical `Line` components.

### `add_generation.py`

Add fixed non-extendable generators from `generators.csv`; attach complete hourly availability; calculate or attach the selected marginal-cost method; add explicit high-penalty load-shedding generators. Preserve plant/unit or technology-cost-band heterogeneity.

### `add_storage.py`

Select `StorageUnit` or `Store + Links` according to the contract. Attach fixed power/energy, efficiency, standing loss, inflow, spill, initial state, terminal state and cyclicity. Assert state conservation inputs before solve.

### `add_external_markets.py`

For each verified interface, add an external bus, fixed virtual generator, fixed virtual load, fixed import link and fixed export link. Compute a documented safe auxiliary capacity/energy bound; all nominal capacities remain non-extendable. Attach complete hourly external prices and optional losses/tolls.

### `solve.py`

Run continuous linear economic dispatch only. Assert no extendability before and after optimization; select the configured solver; log termination status and objective components. No investment, UC, reserve, redispatch or Stage 2 logic.

### `validate.py`

Run pre-solve, post-solve and release checks: bus set, component scope, time index, energy balance, directional limits, storage conservation, auxiliary-bound slack, ENS/scarcity, price availability, curtailment, cost reconciliation and evidence-language gates.

### `accounting.py`

Calculate domestic production cost, net external import purchases/export revenue, link tolls/losses and load-shedding penalty separately. Remove the fixed external virtual-load objective baseline and reconcile component totals to the solver objective.

### `export_results.py`

Write inspectable parquet/CSV tables plus a run manifest: zonal prices, generation, load, curtailment, storage state/charge/discharge/inflow/spill, internal/external flows, link saturation, ENS/scarcity and cost decomposition. Results are keyed by release hash, scenario, solver and code commit.

## Configuration invariants

The code, not only YAML, enforces:

- seven Italian buses and only those seven;
- all nominal capacities non-extendable;
- internal and external transfers as directional Links;
- no Italian Lines;
- one complete hourly UTC year;
- no hidden scaling of capacities or scenario benchmarks;
- explicit load shedding;
- direct seven-bus marginal prices;
- no formal LOLE, capacity-credit, N-1 or Terna-adequacy claim from the deterministic run.

`scenarios.yaml` references frozen release IDs and scenario parameters; it never contains a path to a mutable source or a PyPSA-IT network.

## Required tests

### Contract tests

- All CSV/parquet schemas, primary keys, foreign keys, units and enums.
- 8,760/8,784 complete UTC snapshots, no duplicates or gaps.
- Domain checks for loads, capacities, efficiencies, availability and prices.
- Terna/GEM provenance gates for current thermal rows.

### Network tests

- Exactly seven Italian buses; external buses separately tagged.
- All extendability flags false.
- Italian `lines` table empty.
- One fixed non-negative link per commercial direction.
- Generator and storage totals reconcile to frozen inputs.

### Solve tests

- A one-zone toy case with known merit-order dispatch and price.
- A two-zone directional-congestion case with known price separation.
- A storage chronology case with known state trajectory and losses.
- An external-market import/export case that verifies endogenous direction choice and the removable objective baseline.
- A load-shedding case that verifies ENS and scarcity reporting.

### Regression tests

- Workbook scenario controls reproduce frozen release totals.
- 55 GW is not double counted and is never hard-coded as CCGT+OCGT.
- 30 GW is not added to technology rows or forced as exact `p_nom`.
- 15.5136, 18.9449 and 48.9449 remain diagnostic/benchmark values.
- No 14.4864 GW capacity input or residual is generated.

## Run lifecycle

1. Acquire raw sources into an immutable dated directory.
2. Verify hashes, editions and licenses.
3. Derive and reconcile inputs with code and manifests.
4. Run contract and reconciliation QA.
5. Freeze a signed/hash-pinned release.
6. Build a new network from that release.
7. Run pre-solve invariants.
8. Solve the chosen scenario.
9. Run post-solve physics/accounting/interpretation QA.
10. Export results and a complete run manifest.

No production run is accepted if it reads from `data/raw/` or `data/derived/` directly.

## Runtime outputs and claims

Accepted outputs include zonal dispatch and marginal prices, curtailment, storage trajectories, internal/external exchanges, saturation, load shedding, ENS, scarcity hours, residual-load peaks and cost decomposition. These support deterministic chronological stress analysis. They do not constitute probabilistic adequacy, formal LOLE, capacity credit, N-1 security or reserve/redispatch results.

## Implementation readiness

The repository architecture is frozen and implementable. Repository construction should begin after the data-contract review, but the first calibrated scenario run remains blocked by the empirical items in the v2.9 gap register.

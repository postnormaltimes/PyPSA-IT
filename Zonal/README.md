# Zonal model — Italian electricity markets in 2040 and 2050

The Zonal model is the currently implemented PyPSA-IT model for studying the Italian electricity market and power-system operation at bidding-zone resolution. It represents the seven Italian market zones — **NORD, CNOR, CSUD, SUD, CALA, SICI and SARD** — together with directional interfaces between zones and selected neighbouring markets.

The model is designed for scenario analysis rather than capacity expansion. Installed generation, storage and interconnection capacities are fixed by scenario, and the optimization determines how that system operates hour by hour. The current production family covers **Slow, Base and High** scenarios for both **2040 and 2050**.

## What the model represents

Each case uses a synchronized **8760-hour 2019 UTC chronology**. Horizon-specific demand, technology capacities, costs and accepted renewable/hydro profiles are applied to that common chronology so that the six scenarios can be compared on a consistent hourly basis.

The main operational elements are:

- exogenous zonal electricity demand;
- solar and wind generation with hourly availability profiles;
- heterogeneous thermal units with unit-commitment constraints;
- reservoir hydro and run-of-river generation;
- pumped-hydro storage and battery storage;
- flexible electrolysis / P2X demand represented in each Italian zone;
- directional internal Italian and cross-border market interfaces;
- frozen hourly external-market price series for the neighbouring markets represented at the model boundary.

The final network contains the Italian bidding-zone system plus external interfaces to **France, Switzerland, Austria, Slovenia, Montenegro, Greece, Malta and Tunisia**. Corsica is represented as a commercial/interface hub where applicable. External generation fleets are not co-optimized inside the Italian model: their accepted hourly prices are supplied from a separate reduced European-market model and frozen at the Stage-A/Stage-B boundary.

## Dispatch, commitment and prices

The production workflow deliberately separates physical system operation from market-price recovery.

The **unit-commitment MILP** provides the canonical physical dispatch and commitment state. It represents thermal start-up/shut-down behaviour and other discrete operating constraints that a continuous dispatch model cannot capture.

A **continuous reference LP** is retained as a counterfactual benchmark for understanding the effect of unit commitment.

After the MILP is solved and verified, a **fixed-commitment price LP** re-solves the same system with the commitment decisions fixed. Its dual variables provide the model's marginal-price and water-value outputs without substituting LP dispatch for the physical UC result.

This separation is important: physical quantities come from the verified UC solution, while price analysis is conditioned on that same commitment state.

## Flexibility, storage and hydro

Flexible demand is represented through seven zonal P2X withdrawal units with hourly power limits and annual energy requirements. Batteries and pumped hydro retain explicit power/energy constraints and cyclic state treatment. Hydro modelling preserves zonal natural inflows, reservoir states, run-of-river accessibility/bypass logic and the accepted water-accounting framework used in reporting.

These components make the model suitable for examining not only annual energy balances but also hourly flexibility, storage cycling, hydro operation and the interaction between variable renewables, dispatchable generation and trade.

## Interfaces and market coupling

The final signed-interface representation contains directional internal and external corridors with fixed transfer limits. Internal Italian links represent exchange between bidding zones; external links connect the Italian market model to the neighbouring price boundaries.

This allows the model to study zonal price separation, internal transfers, cross-border imports/exports and interface utilization while remaining intentionally simpler than a full transmission-network model.

## Main outputs

Core reporting is independent from optional presentation tooling and covers the principal analytical outputs required for scenario comparison, including:

- hourly and annual generation by zone and technology;
- zonal electricity prices and price distributions;
- interzonal and cross-border trade;
- storage, pumped-hydro and hydro operation;
- P2X electricity consumption;
- renewable availability and curtailment;
- thermal commitment, starts, shutdowns and online capacity;
- electrical-balance and load-shedding diagnostics;
- water values and hydro accounting;
- annual comparisons across scenarios where matching validated results exist.

Optional visualization can add maps and presentation figures, but it is not required for core analytical reporting.

## Reproducibility

The public repository separates code and compact controls from the larger model data package.

The versioned **`zonal-production-data-v1`** GitHub Release contains the larger reproducibility assets. Two execution paths are supported:

- **Path A** is the preferred execution path. It installs the prepared final UC/reference network pairs and the additional reporting support needed to run the six final cases.
- **Path B** adds the accepted lower-level baseline, renewable and hydro inputs required for deterministic reconstruction of the prepared networks. Reconstruction is non-solving and can be checked against the distributed reference pairs.

The environment is pinned to Python 3.11.15 and the versions recorded in `uv.lock`. Optimization uses Gurobi 13.0.3 with a valid locally configured licence. Solver credentials and licence files are never distributed with the model.

From `Zonal/`:

```powershell
.\scripts\create_environment.ps1
.\scripts\bootstrap_data.ps1 -SourceDir '..\.data-packages\zonal-production-data-v1' -Mode A
.\scripts\validate_installation.ps1
```

Use `-Mode All` when the deterministic Path-B reconstruction inputs are also required.

## Repository map

- `src/mem_model/` — model construction, execution and reporting code;
- `config/` — scenario, execution and reporting controls;
- `pre_pypsa_inputs/` — compact static model inputs;
- `inputs/` and `runtime_inputs/` — manifests and accepted runtime controls;
- `research/` — retained source registers, derivations and supporting research evidence;
- `scripts/` — bootstrap, environment, validation and execution helpers;
- `tests/` — automated validation of model and portability controls;
- `docs/` — methodology, architecture, provenance, governance and execution documentation;
- `optional/` — presentation-only tooling that is not required for core model execution.

## Model boundaries

The Zonal model is intentionally not a detailed transmission-grid model. It abstracts from constraints within each bidding zone, does not optimize endogenous capacity expansion, and treats neighbouring-market prices as frozen external inputs. Its results should therefore be interpreted as scenario-based market and system-operation outcomes at zonal resolution rather than as a nodal grid-feasibility study.

The future [Nodal model](../Nodal/README.md) is intended to complement this representation with explicit internal network topology and nodal balances.

## Documentation

For setup and execution, start with the **[reproducibility guide](docs/REPRODUCIBILITY_GUIDE.md)** and **[execution runbook](docs/RUNBOOK_2040_2050.md)**.

For model interpretation and modification, see:

- **[Methodology](docs/METHODOLOGY.md)** — current economic and technical formulation;
- **[Runtime architecture](docs/MODEL_ARCHITECTURE.md)** — Path A / Path B execution contracts;
- **[Data provenance](docs/DATA_PROVENANCE.md)** — source-to-model and distributed-data structure;
- **[Scenario controls](docs/SCENARIO_GOVERNANCE.md)** — current scenario family, controls and status;
- **[Methodological progression](docs/METHODOLOGICAL_PROGRESSION.md)** — retained evolution of the modelling approach.

Execution authorization, technical verification and analytical acceptance are treated as separate concepts throughout the project.

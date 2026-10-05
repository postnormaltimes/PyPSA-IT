# PyPSA-IT

PyPSA-IT is a research repository for modelling the Italian electricity system and electricity market with [PyPSA](https://pypsa.org/). The project is designed to connect long-term scenario assumptions with hourly system operation, so that questions about generation, demand, flexibility, storage, trade, prices and network representation can be studied within a transparent and reproducible modelling framework.

The repository is organized around two complementary model families rather than a single monolithic model. The **Zonal** model is the implemented model today; the **Nodal** model is a planned extension for more detailed physical-network analysis.

| Model family | Status | Spatial representation | Main focus |
| --- | --- | --- | --- |
| **[Zonal](Zonal/README.md)** | Implemented | Italy's seven electricity bidding zones plus external-market interfaces | Market dispatch, unit commitment, prices, trade, storage, hydro and system operation |
| **[Nodal](Nodal/README.md)** | Planned | Network-connected buses and passive transmission topology | Internal grid constraints, nodal feasibility, congestion and more detailed locational analysis |

## Current Zonal model

The current implementation represents Italy's seven bidding zones — NORD, CNOR, CSUD, SUD, CALA, SICI and SARD — on an hourly basis for **2040 and 2050**. Each horizon contains **Slow, Base and High** fixed-capacity scenarios. Installed capacities and annual demand are exogenous scenario inputs: the model studies how the system operates under those assumptions rather than optimising new generation or transmission investment.

Physical system operation is represented with thermal unit commitment, renewable generation, hydro, pumped hydro, batteries, flexible electrolysis demand and directional internal/external interfaces. A continuous reference case is retained for comparison, while market-price analysis uses a fixed-commitment linear programme conditioned on the unit-commitment result. External-market prices are supplied as frozen boundary inputs from a separate reduced European model rather than co-optimising neighbouring generation fleets inside the Italian model.

The Zonal model is intended for questions such as how different 2040/2050 system configurations affect dispatch, zonal prices, interzonal and cross-border exchanges, storage and hydro operation, renewable curtailment, thermal commitment and scarcity conditions. Because it works at bidding-zone resolution, it deliberately abstracts from transmission constraints inside each Italian zone; that is the main analytical gap the future Nodal family is intended to address.

## Reproducibility and data

Source code, configuration, small canonical inputs, research registers and documentation are versioned in Git. Larger prepared networks and deterministic rebuild inputs are distributed through the versioned **`zonal-production-data-v1`** GitHub Release and are checked against hashes during bootstrap.

Two executable paths are supported:

- **Path A** restores the prepared final network pairs and is the shortest route to running the model.
- **Path B** adds the accepted lower-level inputs required to reconstruct the prepared networks deterministically and verify semantic parity.

The Python environment is pinned, and optimization uses Gurobi with a valid licence configured locally by the user. Licence files and credentials are not distributed with the repository.

## Where to start

For the working model, begin with the **[Zonal overview](Zonal/README.md)**. New users should then follow the **[reproducibility guide](Zonal/docs/REPRODUCIBILITY_GUIDE.md)** and **[execution runbook](Zonal/docs/RUNBOOK_2040_2050.md)**.

More detailed documentation covers the **[methodology](Zonal/docs/METHODOLOGY.md)**, **[runtime architecture](Zonal/docs/MODEL_ARCHITECTURE.md)**, **[data provenance](Zonal/docs/DATA_PROVENANCE.md)** and **[scenario controls](Zonal/docs/SCENARIO_GOVERNANCE.md)**.

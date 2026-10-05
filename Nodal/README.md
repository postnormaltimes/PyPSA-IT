# Nodal model

The Nodal model is the planned high-resolution PyPSA-IT model family for studying the Italian power system with explicit internal transmission topology and nodal power balances.

It is intended to complement the existing [Zonal model](../Zonal/README.md), not simply replace it. The two model families address different analytical questions: the Zonal model represents Italy through electricity-market bidding zones and interface limits, while the future Nodal model is intended to represent the physical network at a finer spatial resolution.

## Intended analytical role

The working Zonal model is well suited to studying market dispatch, zonal prices, storage and hydro operation, unit commitment, interzonal exchanges and cross-border trade under alternative 2040 and 2050 scenarios. By design, however, it does not represent transmission constraints inside each bidding zone.

A nodal representation would make it possible to investigate questions that depend on the internal grid, including:

- the feasibility of dispatch across the transmission network;
- congestion within existing bidding zones;
- local network bottlenecks that are hidden by zonal aggregation;
- grid-driven redispatch and spatially constrained generation;
- the interaction between generation, demand and transmission capacity at specific network locations;
- more detailed locational analysis of future generation, storage and demand.

The intended distinction is therefore primarily one of **spatial and network resolution**. The Zonal model asks how the Italian market/system operates when each bidding zone is represented as an aggregated node with defined interfaces. The Nodal model is intended to ask how similar system conditions interact with the physical network inside and between those zones.

## Relationship with the Zonal model

The two families are expected to remain useful in parallel.

The Zonal model provides a comparatively compact representation that is easier to execute, interpret and use for broad scenario comparison. It is the appropriate model for bidding-zone market analysis and is the only executable PyPSA-IT model currently included in this repository.

The future Nodal family is intended for analyses where internal transmission feasibility and spatial congestion materially affect the question being studied. A more detailed network does not automatically make every market-analysis task better; it introduces additional data, modelling and validation requirements and should therefore be used where that additional resolution is analytically justified.

## Current status

**No executable Nodal implementation or Nodal data package is currently included.**

The repository does not yet define a final nodal topology, bus count, voltage scope, network-data authority, market-price formulation or implementation roadmap. Those choices should be established explicitly before an executable model is added rather than inferred from the Zonal model or from preliminary concepts.

Users who want to run, reproduce or inspect the current PyPSA-IT model should use the **[Zonal model](../Zonal/README.md)** and its **[reproducibility guide](../Zonal/docs/REPRODUCIBILITY_GUIDE.md)**.

This directory is reserved for the future Nodal model family and will be expanded as its methodology, data architecture and validation framework are defined.

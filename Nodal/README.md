# Nodal model

A bidding-zone price tells us what electricity costs in a market area. It does not tell us whether a particular transmission line within that area can carry the power implied by the market dispatch. The planned nodal extension would represent individual network locations and the lines that connect them.

With that detail, the model could identify congestion within a bidding zone, test whether a dispatch is feasible on the physical grid, and estimate where generators would need to adjust their output. It could also examine how the location of a new generator, battery or large consumer affects nearby lines.

This repository currently publishes the [seven-zone market model](../Zonal/README.md). An executable nodal model and its data have yet to be released. The network topology, voltage levels and pricing method for the nodal study are still open. For published dispatch and price results, see the [zonal results](../Zonal/docs/RESULTS.md).

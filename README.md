# PyPSA-IT

PyPSA-IT is a reproducible research model of Italy's electricity system and market, implemented with PyPSA.

| Model | Status | Spatial resolution |
|---|---|---|
| [Zonal](Zonal/README.md) | Completed six-scenario model for 2040 and 2050 | Seven Italian bidding zones |
| [Nodal](Nodal/README.md) | Planned extension | More detailed physical transmission network |

The Zonal model uses fixed capacities, hourly operation, thermal unit commitment, hydro, storage, flexible electrolysis and commercial electricity exchanges. Physical dispatch comes from a unit-commitment MILP; prices come from a subsequent fixed-commitment LP.

Three independent routes are documented in the [reproducibility guide](Zonal/docs/REPRODUCIBILITY_GUIDE.md): inspect published results without a solver, reproduce selected figures without optimization, or independently execute one scenario from verified prepared inputs.

- [Numerical results and data definitions](Zonal/docs/RESULTS.md)
- [Selected research figures](Zonal/docs/FIGURES.md)
- [Prepared inputs](https://github.com/postnormaltimes/PyPSA-IT/releases/tag/zonal-production-data-v3)
- [Six-scenario numerical data](https://github.com/postnormaltimes/PyPSA-IT/releases/tag/zonal-final-results-v1)
- [Methodology](Zonal/docs/METHODOLOGY.md) and [version evolution](Zonal/docs/FINAL_VERSION_EVOLUTION.md)

Final baselines are 2040 `final_v3` and 2050 `final_v6`, each with Slow, Base and High. Earlier source commits and data releases remain preserved. The Nodal directory does not contain a completed nodal simulation.

The [MIT licence](LICENSE) covers repository software. Source data retain their own terms; see [third-party notices](THIRD_PARTY_NOTICES.md).

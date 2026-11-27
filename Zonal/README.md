# Italian seven-zone electricity market

The model calculates electricity production, storage operation, exchanges and prices for every hour of 2040 and 2050 under three scenarios. Each run covers 8,760 hours using the same 2019 weather chronology. The seven bidding zones are North, Centre-North, Centre-South, South, Calabria, Sicily and Sardinia. The files use `NORD`, `CNOR`, `CSUD`, `SUD`, `CALA`, `SICI` and `SARD`. Some figures display Centre-North as `CNORD`.

Each scenario sets installed capacities, annual demand and the maximum transfer in each direction between zones. An initial linear model gives a reference dispatch. A unit-commitment model then determines which thermal units run and when, together with hourly generation, storage and trade. A final linear model holds the commitment decisions fixed to calculate zonal prices. [Methodology](docs/METHODOLOGY.md)

## Published results

`Slow`, `Base` and `High` are the IDs used in filenames and commands. In the presentation, their names are **Infrastruttura Lenta**, **Base** and **Elettrificazione+**. The [scenario guide](docs/SCENARIO_GOVERNANCE.md) explains what changes in each case.

| Year | Scenario | Version | Purchasing price (EUR/MWh) | Fixed electricity demand (TWh) | Electrolysis demand (TWh) | Unserved energy (MWh) |
|---|---|---|---:|---:|---:|---:|
| 2040 | Slow | final_v3 | 92.639185 | 411.500000 | 27.500000 | 0 |
| 2040 | Base | final_v3 | 83.896313 | 411.500000 | 27.500000 | 0 |
| 2040 | High | final_v3 | 85.690691 | 452.650000 | 30.250000 | 0 |
| 2050 | Slow | final_v6 | 74.093807 | 501.600000 | 81.500000 | 0 |
| 2050 | Base | final_v6 | 58.414647 | 501.600000 | 81.500000 | 0 |
| 2050 | High | final_v6 | 69.322920 | 551.760000 | 89.650000 | 0 |

The purchasing price is the annual national average weighted by electricity consumed, including power used for electrolysis (P2X). A simple average of the hourly national prices gives each hour the same weight and can yield a different value. The table rounds the values from `FINAL_CASE_SUMMARY.csv`. [Results](docs/RESULTS.md) and [data dictionary](docs/DATA_DICTIONARY.md)

## Using the release

- **Explore the results.** Download the hourly tables from [the numerical release](https://github.com/postnormaltimes/PyPSA-IT/releases/tag/zonal-final-results-v1). Reading the tables and reproducing the [selected figures](docs/FIGURES.md) require no solver.
- **Run a scenario.** From `Zonal/`, follow the [reproducibility guide](docs/REPRODUCIBILITY_GUIDE.md) to restore the [verified inputs](https://github.com/postnormaltimes/PyPSA-IT/releases/tag/zonal-production-data-v3). Running the model requires the pinned software environment and a valid Gurobi licence. The [runbook](docs/RUNBOOK_2040_2050.md) gives the command for a single case.
- **Trace a version.** The [source release](https://github.com/postnormaltimes/PyPSA-IT/releases/tag/zonal-model-v1.0.0) identifies the code used for publication. The [changelog](CHANGELOG.md) and [version history](docs/FINAL_VERSION_EVOLUTION.md) explain earlier configurations. Instructions under `docs/releases/` refer to those historical versions.

## Interpreting the results

The model represents commercial transfer limits between zones and uses modelled prices and supply conditions for neighbouring markets. It omits transmission constraints inside each zone. When a modelled interface reaches its limit, the result describes a restriction on trade between zones. Individual transmission lines may face different physical conditions.

All six simulated years served the full electricity demand. The runs share one weather year, so they cannot establish how often shortages might occur under other weather conditions. In the 2050 inputs, an exceptional external supply cost was reduced from €15,000 to €400/MWh. This applies only to the relevant external supply. Italian unserved energy retains a cost of €15,000/MWh, and the model imposes no €400/MWh Italian wholesale price cap. [Limitations](docs/LIMITATIONS.md)

For the underlying data and implementation, see [sources](docs/DATA_PROVENANCE.md) and [model architecture](docs/MODEL_ARCHITECTURE.md).

# Italian seven-zone electricity-market model

The model represents NORD, CNOR, CSUD, SUD, CALA, SICI and SARD for 8,760 hourly UTC snapshots using the saved 2019 chronology. Six completed, verified cases are the final research baselines:

| Horizon | Scenarios | Final version |
|---|---|---|
| 2040 | Slow, Base, High | `final_v3` |
| 2050 | Slow, Base, High | `final_v6` |

Capacities, annual demand and directional interface limits are scenario inputs. There is no capacity or network expansion. A continuous reference LP is followed by physical thermal unit commitment and a fixed-commitment pricing LP. Physical generation, storage, P2X demand and adequacy statistics use the UC result. Prices use verified pricing-LP balance duals.

## Accepted numerical results

| Year | Scenario | Version | Purchasing price (EUR/MWh) | Rigid load (TWh) | P2X (TWh) | Physical ENS (MWh) |
|---|---|---|---:|---:|---:|---:|
| 2040 | Slow | final_v3 | 92.639185 | 411.500000 | 27.500000 | 0 |
| 2040 | Base | final_v3 | 83.896313 | 411.500000 | 27.500000 | 0 |
| 2040 | High | final_v3 | 85.690691 | 452.650000 | 30.250000 | 0 |
| 2050 | Slow | final_v6 | 74.093807 | 501.600000 | 81.500000 | 0 |
| 2050 | Base | final_v6 | 58.414647 | 501.600000 | 81.500000 | 0 |
| 2050 | High | final_v6 | 69.322920 | 551.760000 | 89.650000 | 0 |

Purchasing prices weight each zonal price by rigid plus P2X electricity demand and represented duration. They differ from the time mean of the hourly modeled national index. Rounded values above come from the release's `FINAL_CASE_SUMMARY.csv`; the full-precision file is authoritative.

## Start here

Enter `Zonal/` before running model commands. Start with the [current three-route reproducibility guide](docs/REPRODUCIBILITY_GUIDE.md), [data dictionary](docs/DATA_DICTIONARY.md), [results](docs/RESULTS.md), [figures](docs/FIGURES.md), [single-case runbook](docs/RUNBOOK_2040_2050.md) and [model changelog](CHANGELOG.md). The [versioned source release](https://github.com/postnormaltimes/PyPSA-IT/releases/tag/zonal-model-v1.0.0) identifies the original publication commit; documentation-only changes to `main` do not modify that tag. Older instructions under `docs/releases/` are historical records, not the current final_v3/final_v6 execution route.

The `zonal-production-data-v3` release contains prepared inputs; `zonal-final-results-v1` contains full hourly numerical data and compact verification evidence. Downloads are verified by repository-owned manifests. No solver is needed to read results. Figure reproduction does not open solved networks or invoke optimization. Independent simulation requires the pinned environment and a valid local Gurobi licence.

## Interpretation and limitations

This is a modeled, zonally aggregated market, not observed GME prices or an AC power-flow reconstruction. The single weather realization and deterministic dispatch do not establish probabilistic adequacy, even where physical shedding is zero. Within-zone transmission constraints are absent. Commercial-interface saturation is not observed physical line overload. External markets use accepted synthetic boundary representations.

In 2050 `final_v6`, only exceptional synthetic external supply marginal costs of EUR 15,000/MWh are replaced by EUR 400/MWh. Italian load-shedding VOLL remains EUR 15,000/MWh; EUR 400 is not an Italian wholesale price cap. Read the [methodology](docs/METHODOLOGY.md), [scenario governance](docs/SCENARIO_GOVERNANCE.md), [sources](docs/DATA_PROVENANCE.md), [architecture](docs/MODEL_ARCHITECTURE.md) and [version history](docs/FINAL_VERSION_EVOLUTION.md).

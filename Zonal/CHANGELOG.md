# Model versions and release history

The current published research baselines are **2040 `final_v3`** and **2050 `final_v6`**, each executed for Slow, Base and High. Model variants track methodological changes; GitHub tags identify versioned source and data packages. See the [detailed evolution and evidence limitations](docs/FINAL_VERSION_EVOLUTION.md).

## October 2026 — final six-case research publication

- [`zonal-model-v1.0.0`](https://github.com/postnormaltimes/PyPSA-IT/releases/tag/zonal-model-v1.0.0): versioned model source and reproducibility guidance.
- [`zonal-production-data-v3`](https://github.com/postnormaltimes/PyPSA-IT/releases/tag/zonal-production-data-v3): final prepared reference/UC networks and supporting inputs.
- [`zonal-final-results-v1`](https://github.com/postnormaltimes/PyPSA-IT/releases/tag/zonal-final-results-v1): verified six-case hourly prices, modeled national index, UC dispatch and supporting result tables.
- Eleven selected figure types are distributed with numerical source tables. The earlier input releases v1/v2 and their source commits remain unchanged.

## 2040 model evolution

- **`final_v1` — executed historical comparator.** Baseline evidence is preserved for version comparisons.
- **`final_v2` — prepared, never executed.** A P2X spatial-relocation methodology milestone, **not** a numerical results series.
- **`final_v3` — final publication baseline.** All Slow/Base/High full-year cases were executed and verified after the retained P2X and resource/input refinements. A v1-to-v3 comparison reflects multiple changes and is not a single-factor causal experiment.

## 2050 model evolution

- **`final_v4` — executed historical capacity-source revision.** The source moves from the earlier PNIEC benchmark to the later Terna 2050 analysis. See the preserved details below.
- **`final_v5` — executed OCGT reinforcement.** Adds the accepted incremental non-CHP methane/hydrogen OCGT units to the 2050 adequacy configuration; the preceding source/result state is retained separately.
- **`final_v6` — final publication baseline.** Retains the v5 domestic reinforcement and replaces **only** exceptional synthetic external supply marginal-cost inputs matching EUR 15,000/MWh with EUR 400/MWh. Italian load-shedding VOLL remains EUR 15,000/MWh; EUR 400/MWh is not an Italian price cap. All Slow/Base/High full-year cases were executed and verified.

## Historical 2050 `final_v4` capacity-source revision

The 2050 Base capacity source changes from the earlier PNIEC configuration to
Terna's later system analysis: solar 245 → 180 GW, onshore wind 36 → 46 GW,
offshore wind 15 → 20 GW and nuclear 8 → 10 GW. Additional nuclear replaces
GAS_CCS within the existing programmable-capacity envelope. Slow and High use
the established scenario rules; nuclear is 10 GW in all three cases.

BESS, existing zonal allocation keys, demand, P2X, profiles, hydro, interfaces
and dispatch economics are unchanged. All 2040 configurations are inherited
unchanged. The validated 2050 Base result and the prior PNIEC-capacity benchmark
retain distinct provenance.

The above final_v4 text records its original research stage. It does **not** describe the current final_v6 implementation or the availability of later completed scenario results. For the current execution workflow, use the [reproducibility guide](docs/REPRODUCIBILITY_GUIDE.md).

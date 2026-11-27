# Scenarios and model versions

The study compares three scenarios in 2040 and 2050. Each specifies demand, generation and storage capacity, and directional limits on trade. The model calculates how the available system operates over the year. Capacity investment is outside its scope.

| Name in the presentation | ID in the data and run commands | Assumptions |
|---|---|---|
| Base | `Base` | Reference demand, capacity and grid assumptions drawn from Italian government and system-operator plans. |
| Infrastruttura Lenta | `Slow` | The same demand as Base, with slower deployment of wind, solar, batteries and grid upgrades. |
| Elettrificazione+ | `High` | Faster electrification raises electricity demand, with more renewables and battery capacity than in Base. |

## Which version to use

The six published runs use **`final_v3` for 2040** and **`final_v6` for 2050**. The 2040 `final_v2` inputs were prepared but never simulated. In 2050, `final_v5` added peaking capacity and `final_v6` retained it while revising an exceptional external supply cost. The [version history](FINAL_VERSION_EVOLUTION.md) identifies the changes and which earlier results are available.

## How cases are reproduced

Each case runs a continuous reference model, a thermal unit-commitment model for dispatch, and a pricing model with the plant commitments fixed. Verification uses outputs from the same case at each step. The published runs use Gurobi 13.0.3 with one thread and seed 0. [Single-case instructions](RUNBOOK_2040_2050.md)

`config/publication.yaml` defines the figures using local model results. `config/publication_research_release.yaml` generates the same figures from the public result tables. The prepared inputs retain the original numerical arrays. The input manifests identify both the original files and the portable copies distributed with the release. [Data provenance](DATA_PROVENANCE.md)

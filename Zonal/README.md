# MEM â€” Italian electricity markets in 2040 and 2050

Hourly electricity-market model of Italy's seven bidding zones for six fixed-capacity scenarios: Slow, Base and High in 2040 and 2050. The final model represents flexible electrolysis demand, thermal unit commitment, storage, hydro and directional market interfaces. Continuous counterfactual and fixed-commitment LP runs provide economic comparison and market prices.

The synchronized chronology is 2019 UTC (8760 hours). Installed capacity and annual scenario demand are exogenous. Frozen external-market prices are supplied by a separate reduced European model; its generation fleets do not enter the Italian model.

This is the Zonal model in PyPSA-IT. Run setup and execution commands from Zonal/.

Start with the [reproducibility guide](docs/REPRODUCIBILITY_GUIDE.md) and [execution runbook](docs/RUNBOOK_2040_2050.md). The preferred execution path uses prepared unsolved final networks. The deterministic rebuild path reproduces the same model from accepted compact inputs. Both use the pinned environment and matching versioned data package; neither requires raw weather or external-market resimulation.

```powershell
.\scripts\create_environment.ps1
.\scripts\bootstrap_data.ps1 -SourceDir '..\.data-packages\zonal-production-data-v1' -Mode A
.\scripts\validate_installation.ps1
```

Current 2040 results have passed technical verification; analytical review remains separately recorded. All six final_v1 cases are explicitly authorized for manual execution. Analytical acceptance remains separate from execution authorization.

Source code is in src/mem_model; configuration and static tables are in config and pre_pypsa_inputs. Research registers, workbooks and derivations retain the project's academic basis. See [methodology](docs/METHODOLOGY.md), [scenario controls](docs/SCENARIO_GOVERNANCE.md), [data provenance](docs/DATA_PROVENANCE.md), [architecture](docs/MODEL_ARCHITECTURE.md) and [methodological progression](docs/METHODOLOGICAL_PROGRESSION.md). Large data, environments and generated results are not tracked. Presentation rendering is optional; core analytical reporting is available independently.

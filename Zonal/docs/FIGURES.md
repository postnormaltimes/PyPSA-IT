# Research figures

The curated overview contains eleven principal figure types covering all six final cases. Original PNG/SVG files are preserved; accompanying tables and portable provenance identify the physical UC and fixed-commitment LP sources. Publication remains subject to final editorial approval. The register records every distributed file and checksum.

- [annual_dispatch_chronological_daily](figures/annual_dispatch_chronological_daily.png) ([SVG](figures/annual_dispatch_chronological_daily.svg), [data](figures/annual_dispatch_chronological_daily.csv), [provenance](figures/annual_dispatch_chronological_daily.json))
- [annual_zonal_prices](figures/annual_zonal_prices.png) ([SVG](figures/annual_zonal_prices.svg), [data](figures/annual_zonal_prices.csv), [provenance](figures/annual_zonal_prices.json))
- [capacity_generation_mix](figures/capacity_generation_mix.png) ([SVG](figures/capacity_generation_mix.svg), [data](figures/capacity_generation_mix.csv), [provenance](figures/capacity_generation_mix.json))
- [exchanges_congestion](figures/exchanges_congestion.png) ([SVG](figures/exchanges_congestion.svg), [data](figures/exchanges_congestion.csv), [provenance](figures/exchanges_congestion.json))
- [marginal_price_setting_regimes](figures/marginal_price_setting_regimes.png) ([SVG](figures/marginal_price_setting_regimes.svg), [data](figures/marginal_price_setting_regimes.csv), [provenance](figures/marginal_price_setting_regimes.json))
- [monthly_prices](figures/monthly_prices.png) ([SVG](figures/monthly_prices.svg), [data](figures/monthly_prices.csv), [provenance](figures/monthly_prices.json))
- [peak_residual_demand_week](figures/peak_residual_demand_week.png) ([SVG](figures/peak_residual_demand_week.svg), [data](figures/peak_residual_demand_week.csv), [provenance](figures/peak_residual_demand_week.json))
- [price_duration](figures/price_duration.png) ([SVG](figures/price_duration.svg), [data](figures/price_duration.csv), [provenance](figures/price_duration.json))
- [storage_flexibility_curtailment](figures/storage_flexibility_curtailment.png) ([SVG](figures/storage_flexibility_curtailment.svg), [data](figures/storage_flexibility_curtailment.csv), [provenance](figures/storage_flexibility_curtailment.json))
- [evolution_2040](figures/evolution_2040.png) ([SVG](figures/evolution_2040.svg), [data](figures/evolution_2040.csv), [provenance](figures/evolution_2040.json))
- [evolution_2050](figures/evolution_2050.png) ([SVG](figures/evolution_2050.svg), [data](figures/evolution_2050.csv), [provenance](figures/evolution_2050.json))

## Reproduction

Restore the numerical-results release and enter `Zonal/`. Use Python 3.11 and the committed lock, then run:

```powershell
uv lock --check --python 3.11
if ($LASTEXITCODE -ne 0) { throw 'Lock consistency failed' }
uv sync --locked --python 3.11 --extra test
if ($LASTEXITCODE -ne 0) { throw 'Environment setup failed' }
.\.venv\Scripts\python.exe -B -m mem_model.reporting.publication selected --config config\publication_research_release.yaml
if ($LASTEXITCODE -ne 0) { throw 'Figure reproduction failed' }
```

The selected command reads published numerical tables and verification identities only. It does not open solved networks, discover private evidence, regenerate marginal categories or invoke optimization. Reproduced CSV values must agree; image bytes can vary with fonts and renderer versions.

The larger local technical atlas and experimental presentation material are not part of this curated release. Their complete regeneration is not advertised by the public results-only profile. Historical producer code is retained separately; it is not a substitute for the selected command. Solved-network-level KKT reconstruction and additional unpublished diagnostics are outside the three documented reproduction routes.

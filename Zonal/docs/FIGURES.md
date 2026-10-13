# Research figures

The public research overview contains **eleven selected figure types** covering the six accepted 2040/2050 cases and their documented version history. The original PNG/SVG files are provided alongside plotted-data CSVs and portable provenance records; the [figure register](figures/FIGURE_REGISTER.csv) gives file sizes and checksums.

| Figure | Image | Plotted data | Provenance |
|---|---|---|---|
| 2040 model-version evolution | [PNG](figures/evolution_2040.png) · [SVG](figures/evolution_2040.svg) | [CSV](figures/evolution_2040.csv) | [JSON](figures/evolution_2040.json) |
| 2050 model-version evolution | [PNG](figures/evolution_2050.png) · [SVG](figures/evolution_2050.svg) | [CSV](figures/evolution_2050.csv) | [JSON](figures/evolution_2050.json) |
| Annual zonal modeled prices | [PNG](figures/annual_zonal_prices.png) · [SVG](figures/annual_zonal_prices.svg) | [CSV](figures/annual_zonal_prices.csv) | [JSON](figures/annual_zonal_prices.json) |
| Monthly modeled prices | [PNG](figures/monthly_prices.png) · [SVG](figures/monthly_prices.svg) | [CSV](figures/monthly_prices.csv) | [JSON](figures/monthly_prices.json) |
| National price-duration distributions | [PNG](figures/price_duration.png) · [SVG](figures/price_duration.svg) | [CSV](figures/price_duration.csv) | [JSON](figures/price_duration.json) |
| Installed capacity and annual generation | [PNG](figures/capacity_generation_mix.png) · [SVG](figures/capacity_generation_mix.svg) | [CSV](figures/capacity_generation_mix.csv) | [JSON](figures/capacity_generation_mix.json) |
| Full-year daily physical dispatch | [PNG](figures/annual_dispatch_chronological_daily.png) · [SVG](figures/annual_dispatch_chronological_daily.svg) | [CSV](figures/annual_dispatch_chronological_daily.csv) | [JSON](figures/annual_dispatch_chronological_daily.json) |
| Hourly peak residual-demand week | [PNG](figures/peak_residual_demand_week.png) · [SVG](figures/peak_residual_demand_week.svg) | [CSV](figures/peak_residual_demand_week.csv) | [JSON](figures/peak_residual_demand_week.json) |
| Storage, P2X and curtailment | [PNG](figures/storage_flexibility_curtailment.png) · [SVG](figures/storage_flexibility_curtailment.svg) | [CSV](figures/storage_flexibility_curtailment.csv) | [JSON](figures/storage_flexibility_curtailment.json) |
| Net exchanges and interface congestion | [PNG](figures/exchanges_congestion.png) · [SVG](figures/exchanges_congestion.svg) | [CSV](figures/exchanges_congestion.csv) | [JSON](figures/exchanges_congestion.json) |
| Marginal price-setting regimes | [PNG](figures/marginal_price_setting_regimes.png) · [SVG](figures/marginal_price_setting_regimes.svg) | [CSV](figures/marginal_price_setting_regimes.csv) | [JSON](figures/marginal_price_setting_regimes.json) |

The supplied images were published with the [versioned source release](https://github.com/postnormaltimes/PyPSA-IT/releases/tag/zonal-model-v1.0.0). Some preserved figure JSONs and research configurations still contain preparation-time labels such as `AWAITING_FINAL_PUBLICATION_APPROVAL` or `VERIFIED_RESULT_PENDING_ANALYTICAL_REVIEW`: those are **frozen evidence/status snapshots**, not an assertion that the current public GitHub release remains unpublished or that the accepted six-case numerical QA failed. Their hashes and source identities have not been rewritten.

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

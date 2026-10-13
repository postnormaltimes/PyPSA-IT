# Reproducibility

Use a fresh checkout of the source commit identified in each release. Enter `Zonal/`. Data downloads belong outside Git history. The manifest identifies every expected member, its size, original source hash, public hash and any portability-only transformation.

## Route A: results analysis without PyPSA or Gurobi

With a standard Python installation, Windows PowerShell:

```powershell
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force downloads | Out-Null
Invoke-WebRequest -Uri 'https://github.com/postnormaltimes/PyPSA-IT/releases/download/zonal-final-results-v1/zonal_final_results_v1.zip' -OutFile downloads\zonal_final_results_v1.zip
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\bootstrap_results.ps1 -SourceDir .\downloads -PythonExe python
if ($LASTEXITCODE -ne 0) { throw 'Results bootstrap failed' }
python -B scripts\validate_results.py
if ($LASTEXITCODE -ne 0) { throw 'Results validation failed' }
```

The bootstrap uses the Python standard library only. CSVs can be opened in ordinary statistical software; reading Parquet additionally requires an appropriate Parquet reader (for Python, pandas and pyarrow). No optimization software or licence is needed for this route. Core files are `data/research_results/cases/<year>/<scenario>/hourly_zonal_prices.csv`, `hourly_national_price_index.csv` and `hourly_generation_dispatch.csv`. Additional canonical tables and historical figure-source evidence are under `data/research_results/reports/`.

## Route B: reproduce selected figures without optimization

Restore the same results package first. Create the pinned environment with `uv lock --check --python 3.11`, then `uv sync --locked --python 3.11 --extra test` from `Zonal/`. The environment includes the simulation libraries; rendering does not create a Gurobi environment or require a licence.

```powershell
uv lock --check --python 3.11
if ($LASTEXITCODE -ne 0) { throw 'Lock consistency failed' }
uv sync --locked --python 3.11 --extra test
if ($LASTEXITCODE -ne 0) { throw 'Environment setup failed' }
.\.venv\Scripts\python.exe -B -m mem_model.reporting.publication selected --config config\publication_research_release.yaml
if ($LASTEXITCODE -ne 0) { throw 'Figure reproduction failed' }
```

This command renders the eleven principal figure types to `results/publication/research_release/`. Source hashes, solve/verification identities and final QA must match. It reads published tables and compact same-LP marginal-regime evidence; no solved network binary is required. Canonical marginal categories are reused, not re-inferred from an abandoned offer-stack procedure. PNG/SVG appearance can vary with the available font and plotting environment; numerical plotted tables must agree. Aptos is used if installed, otherwise DejaVu Sans. No proprietary font is distributed.

## Route C: independently simulate one case

Use a separate working checkout/output directory from the immutable published-results dataset. Install Python 3.11 and `uv`; synchronize the committed lock. Download and verify prepared inputs:

```powershell
uv lock --check --python 3.11
if ($LASTEXITCODE -ne 0) { throw 'Lock consistency failed' }
uv sync --locked --python 3.11 --extra test
if ($LASTEXITCODE -ne 0) { throw 'Environment setup failed' }
New-Item -ItemType Directory -Force downloads | Out-Null
Invoke-WebRequest -Uri 'https://github.com/postnormaltimes/PyPSA-IT/releases/download/zonal-production-data-v3/zonal_final_inputs_v3.zip' -OutFile downloads\zonal_final_inputs_v3.zip
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\bootstrap_data.ps1 -SourceDir .\downloads
if ($LASTEXITCODE -ne 0) { throw 'Input bootstrap failed' }
.\.venv\Scripts\python.exe -B -m mem_model.stage_b_uc2 preflight --year 2050 --scenario Base --variant final_v6
if ($LASTEXITCODE -ne 0) { throw 'Preflight failed' }
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\run_final_case.ps1 -Year 2050 -Scenario Base
if ($LASTEXITCODE -ne 0) { throw 'Scenario stopped; inspect saved receipts' }
```

The supported wrapper executes one explicit case through the nine actions and stops on the first failure. For 2040 it selects `final_v3`; for 2050 it selects `final_v6`. No automatic multi-scenario optimization is performed. Same-case reference/UC/price dependencies, authorizations, hashes and overwrite safeguards remain in force. A valid local Gurobi licence is required only for solving. Never store licence files or credentials in this repository, downloads or archives.

The solver contract is Gurobi/gurobipy 13.0.3, Threads=1, Seed=0; PyPSA 1.2.3 and Linopy 0.7.0. The lock specifies other packages. Source research reconstruction, Stage-A reruns, raw weather and historical ZIPs are not required by the prepared-input route.

## Validation and storage

Bootstrap verifies the complete archive and every member before writing, rejects unsafe/unexpected paths and refuses differing existing data. It is safe to rerun with identical assets. Keep sufficient space for downloaded archives, expanded data and new solver outputs; prepared inputs and immutable result data have separate manifests. Consult release notes for measured compressed/uncompressed sizes. Full-year MILP runtime is machine- and solver-dependent; there is no promised universal runtime. Verification compares semantic quantities and tolerances, not byte-identical newly solved NetCDFs.

Published data were mechanically reconciled against accepted saved reports. Publication validation did not rerun optimization. A machine lacking a valid licence may still pass results, bootstrap, static-input and figure checks; its independent simulation is externally blocked, not a repository failure.

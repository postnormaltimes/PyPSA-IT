# Reproduction and manual execution

Work from `Zonal/` using Python 3.11 and the versions in `uv.lock`.

```powershell
uv sync --extra test
```

Download `zonal_final_v4_inputs.zip` from the
[Zonal data release](https://github.com/postnormaltimes/PyPSA-IT/releases/tag/zonal-production-data-v2)
to a local directory. Restore its hash-checked prepared inputs:

```powershell
.\scripts\bootstrap_data.ps1 -SourceDir '..\.data-packages\zonal-production-data-v2'
```

Bootstrap verifies the archive and each file against `inputs/manifests/`.
It will not overwrite a differing file. The package contains unsolved networks
and deterministic capacity/reporting support, not solved results, raw weather
or third-party source documents. The older v1 release reproduces the earlier
configuration and must not be substituted for these inputs.

Standard reporting maps require GISCO boundaries obtained separately under
their source terms. Read the terms linked in `THIRD_PARTY_NOTICES.md` at the
repository root before using the explicit downloader:

```powershell
.\.venv\Scripts\python.exe -B scripts\fetch_optional_geography.py --accept-gisco-terms
```

These display boundaries are not needed for solves or static preflight and do
not affect the input networks. Original source reconstruction requires additional
licensed/source-specific inputs and is not part of the prepared-input route.

The solver contract is Gurobi 13.0.3, Threads=1, Seed=0 and
`include_objective_constant=false`. A valid local licence is required.
No licence material is distributed.

## One case, one command at a time

Select a case explicitly; these assignments do not execute it:

```powershell
$Year = 2050
$Scenario = 'Slow'
```

Run each command separately and check its result before the next action:

```powershell
.\.venv\Scripts\python.exe -m mem_model.stage_b_uc2 preflight --year $Year --scenario $Scenario --variant final_v4
```

```powershell
.\.venv\Scripts\python.exe -m mem_model.stage_b_uc2 run-reference --year $Year --scenario $Scenario --variant final_v4
```

```powershell
.\.venv\Scripts\python.exe -m mem_model.stage_b_uc2 verify-reference --year $Year --scenario $Scenario --variant final_v4
```

```powershell
.\.venv\Scripts\python.exe -m mem_model.stage_b_uc2 run-uc --year $Year --scenario $Scenario --variant final_v4
```

```powershell
.\.venv\Scripts\python.exe -m mem_model.stage_b_uc2 verify-uc --year $Year --scenario $Scenario --variant final_v4
```

```powershell
.\.venv\Scripts\python.exe -m mem_model.stage_b_uc2 run-price --year $Year --scenario $Scenario --variant final_v4
```

```powershell
.\.venv\Scripts\python.exe -m mem_model.stage_b_uc2 verify-price --year $Year --scenario $Scenario --variant final_v4
```

```powershell
.\.venv\Scripts\python.exe -m mem_model.stage_b_uc2 report --year $Year --scenario $Scenario --variant final_v4
```

```powershell
.\.venv\Scripts\python.exe -m mem_model.stage_b_uc2 final-qa --year $Year --scenario $Scenario --variant final_v4
```

The pattern supports all six cases. For 2040, paths retain their inherited
final_v3 identity; for 2050, inputs/results use final_v4. Results go to
`results/final_methodology_v3/<year>/<scenario>/` or
`results/final_methodology_v4/<year>/<scenario>/`, respectively. Existing local
results are protected against overwrite. Verification enforces same-case and
solved-hash dependencies. Price recovery fixes UC commitment and requires zero
integer/binary variables. Reporting never launches another solve or scenario.

Physical UC results and fixed-LP prices/water values have separate provenance.
Store water values retain native sign and EUR/MWh_water scale; signed interface
flows are not counted twice. RoR bypass is distinguished from electrical
curtailment and reservoir spill.

Publication validation is non-solving. Run the documented focused tests only;
some source-reconstruction tests require external data and some historical
tests explicitly build optimisation models.

```powershell
.\.venv\Scripts\python.exe -B scripts\validate_publication.py --output .pytest_tmp\publication
```

The test receipt lists exclusions explicitly. Historical cached-report tests
may be skipped when those optional results are absent; they do not affect the
prepared-input checks or the six static preflights.

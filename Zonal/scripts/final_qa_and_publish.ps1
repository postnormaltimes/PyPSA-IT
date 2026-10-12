param(
    [Parameter(Mandatory = $true)][ValidateSet(2040, 2050)][int]$Year,
    [Parameter(Mandatory = $true)][ValidateSet('Slow', 'Base', 'High')][string]$Scenario,
    [Parameter(Mandatory = $true)][string]$Variant
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
Push-Location -LiteralPath $projectRoot
try {
    # Future ninth-stage entrypoint only. No solve or automatic next scenario.
    & $pythonPath -B -m mem_model.reporting.publication complete --year $Year --scenario $Scenario --variant $Variant
    if ($LASTEXITCODE -ne 0) { throw "Final QA/publication command failed (exit $LASTEXITCODE)." }
} finally {
    Pop-Location
}

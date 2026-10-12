param(
    [Parameter(Mandatory=$true)][ValidateSet(2040,2050)][int]$Year,
    [Parameter(Mandatory=$true)][ValidateSet('Slow','Base','High')][string]$Scenario
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ProjectPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $ProjectPython -PathType Leaf)) { throw 'Create the pinned environment first.' }
$Variant = if ($Year -eq 2040) { 'final_v3' } else { 'final_v6' }
Push-Location $ProjectRoot
try {
    foreach ($Action in @('preflight','run-reference','verify-reference','run-uc','verify-uc','run-price','verify-price','report','final-qa')) {
        Write-Output ((Get-Date -Format o) + ' ' + $Action + ' ' + $Year + ' ' + $Scenario + ' ' + $Variant)
        & $ProjectPython -B -m mem_model.stage_b_uc2 $Action --year $Year --scenario $Scenario --variant $Variant
        if ($LASTEXITCODE -ne 0) { throw ('Stopped at ' + $Action + '; exit ' + $LASTEXITCODE + '. Inspect the saved receipt before retrying.') }
    }
} finally { Pop-Location }

[CmdletBinding()]
param([string]$OutputPath='qa/validation/2040_base_reproduction.json')
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location $repo
try {
    $python=Join-Path $repo '.venv/Scripts/python.exe'
    if (!(Test-Path -LiteralPath $python)) { throw 'Create the pinned environment first.' }
    & $python -B scripts/validate_environment.py --check-license --output qa/validation/environment.json
    if ($LASTEXITCODE -ne 0) { throw 'Environment preflight failed.' }
    $envReceipt=Get-Content qa/validation/environment.json -Raw | ConvertFrom-Json
    if ($envReceipt.license_status -ne 'AVAILABLE') {
        $dst=[IO.Path]::GetFullPath((Join-Path $repo $OutputPath))
        $null=New-Item -ItemType Directory -Path (Split-Path $dst -Parent) -Force
        @{status='EXTERNAL_LICENSE_BLOCKER';solve_executed=$false;case='2040_Base_final_v1';'2050_executed'=$false} | ConvertTo-Json | Set-Content -LiteralPath $dst -Encoding UTF8
        throw 'User-specific Gurobi licence is unavailable. No optimization was executed.'
    }
    # One explicitly requested known-case sequence; each prerequisite is fail-closed.
    $pairs=@(@('run-reference','verify-reference'),@('run-uc','verify-uc'),@('run-price','verify-price'))
    foreach ($pair in $pairs) {
        $kind=if ($pair[0] -eq 'run-reference') {'CONTINUOUS_REFERENCE'} elseif ($pair[0] -eq 'run-uc') {'UC_MILP'} else {'FIXED_COMMITMENT_PRICE_LP'}
        $existing=Get-ChildItem -LiteralPath "results/final_methodology_v1/2040/Base/$kind" -Filter '*SOLVE_RECEIPT.json' -ErrorAction SilentlyContinue
        if (!$existing) {
            & $python -B -m mem_model.stage_b_uc2 $pair[0] --year 2040 --scenario Base --variant final_v1
            if ($LASTEXITCODE -ne 0) { throw "Stopped at $($pair[0]); no successor executed." }
        }
        & $python -B -m mem_model.stage_b_uc2 $pair[1] --year 2040 --scenario Base --variant final_v1
        if ($LASTEXITCODE -ne 0) { throw "Stopped at $($pair[1]); no successor executed." }
    }
    foreach ($action in @('report','final-qa')) {
        & $python -B -m mem_model.stage_b_uc2 $action --year 2040 --scenario Base --variant final_v1
        if ($LASTEXITCODE -ne 0) { throw "Stopped at $action." }
    }
    & $python -B scripts/compare_reference.py --output $OutputPath
    if ($LASTEXITCODE -ne 0) { throw 'Reference comparison failed or requires review. Read the precise metric deltas; no2050 solve is authorized.' }
} finally { Pop-Location }

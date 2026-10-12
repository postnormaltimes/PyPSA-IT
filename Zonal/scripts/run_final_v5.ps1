param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Slow', 'Base', 'High')]
    [string]$Scenario,
    [ValidateSet('final_v5', 'final_v5_proportional_control')]
    [string]$Variant = 'final_v5',
    [ValidateSet('preflight', 'run-reference', 'verify-reference', 'run-uc', 'verify-uc', 'run-price', 'verify-price', 'report', 'final-qa')]
    [string]$StartAt = 'preflight'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Project Python executable not found: $pythonPath"
}
$logRoot = Join-Path $projectRoot "_local\logs\$Variant\2050\$Scenario"
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$actions = @('preflight', 'run-reference', 'verify-reference', 'run-uc', 'verify-uc', 'run-price', 'verify-price', 'report', 'final-qa')
$first = [array]::IndexOf($actions, $StartAt)
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Push-Location -LiteralPath $projectRoot
try {
    # The loop advances stages of this explicitly selected case only.
    foreach ($action in $actions[$first..($actions.Length - 1)]) {
        $logPath = Join-Path $logRoot "$timestamp-$action.log"
        & $pythonPath -B -m mem_model.stage_b_uc2 $action --year 2050 --scenario $Scenario --variant $Variant *> $logPath
        $stageExitCode = $LASTEXITCODE
        Get-Content -LiteralPath $logPath -Tail 12
        if ($stageExitCode -ne 0) {
            throw "Stopped at $action (exit $stageExitCode). Log: $logPath"
        }
    }
} finally {
    Pop-Location
}

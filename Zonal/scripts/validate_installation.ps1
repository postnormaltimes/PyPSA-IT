[CmdletBinding()]
param([string]$OutputPath='qa/validation/path_a_preflight.json')
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location $repo
try {
    $python=Join-Path $repo '.venv/Scripts/python.exe'
    if (!(Test-Path -LiteralPath $python)) { throw 'Pinned environment is missing. Run create_environment.ps1 first.' }
    & $python -B scripts/validate_environment.py
    if ($LASTEXITCODE -ne 0) { throw 'Package environment mismatch.' }
    & $python -B scripts/validate_inputs.py --repo $repo --output $OutputPath
    if ($LASTEXITCODE -ne 0) { throw 'Canonical non-solving preflight failed; read the receipt.' }
} finally { Pop-Location }

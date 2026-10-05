[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (!(Get-Command uv -ErrorAction SilentlyContinue)) { throw 'uv is required. Install the official uv CLI before continuing; no silent installation is performed.' }
if (!(Test-Path -LiteralPath (Join-Path $repo 'uv.lock'))) { throw 'Pinned uv.lock is missing.' }
Push-Location $repo
try {
    & uv sync --frozen --extra test --python 3.11.15
    if ($LASTEXITCODE -ne 0) { throw 'Pinned environment creation failed.' }
    & './.venv/Scripts/python.exe' -B './scripts/validate_environment.py' --check-license --output './qa/validation/environment.json'
    if ($LASTEXITCODE -ne 0) { throw 'Python/package validation failed.' }
} finally { Pop-Location }

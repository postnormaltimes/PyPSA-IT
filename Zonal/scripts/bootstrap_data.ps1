param([Parameter(Mandatory=$true)][string]$SourceDir)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ProjectPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $ProjectPython -PathType Leaf)) {
    throw 'Create the pinned local environment with uv sync --extra test first.'
}
& $ProjectPython -B (Join-Path $PSScriptRoot 'bootstrap_data.py') --source-dir $SourceDir
if ($LASTEXITCODE -ne 0) { throw 'Prepared-input restoration failed.' }

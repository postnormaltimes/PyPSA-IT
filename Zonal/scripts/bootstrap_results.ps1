param(
    [Parameter(Mandatory=$true)][string]$SourceDir,
    [string]$PythonExe = 'python'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
& $PythonExe -B (Join-Path $PSScriptRoot 'bootstrap_data.py') --source-dir $SourceDir --package results
if ($LASTEXITCODE -ne 0) { throw 'Results restoration failed; no optimizer was invoked.' }

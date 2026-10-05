[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$SourceDir)
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$source=[IO.Path]::GetFullPath($SourceDir)
$rows=Import-Csv -LiteralPath (Join-Path $repo 'inputs/manifests/data_package.csv')
$assets=@($rows | Where-Object { ($_.scope.Split('|') -notcontains 'GIT') -and ($_.role -eq 'optional presentation geography') })
if ($assets.Count -ne 1) { throw 'Expected the single approved optional NUTS2 geography asset.' }
foreach ($row in $assets) {
    $src=[IO.Path]::GetFullPath((Join-Path $source $row.destination))
    $dst=[IO.Path]::GetFullPath((Join-Path $repo $row.destination))
    if (!$src.StartsWith($source.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase) -or !$dst.StartsWith($repo.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Optional asset path escapes its root.' }
    if (!(Test-Path -LiteralPath $src) -or (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash.ToLowerInvariant() -ne $row.sha256) { throw 'Optional geography source is missing or changed.' }
    if (Test-Path -LiteralPath $dst) {
        if ((Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash.ToLowerInvariant() -ne $row.sha256) { throw 'Refusing to replace mismatched optional geography.' }
    } else { $null=New-Item -ItemType Directory -Path (Split-Path $dst -Parent) -Force; Copy-Item -LiteralPath $src -Destination $dst }
}
@{status='PASS';optional_assets=$assets.Count;core_required=$false} | ConvertTo-Json -Compress

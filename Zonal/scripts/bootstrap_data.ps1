[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$SourceDir,
      [Alias('Path')][ValidateSet('A','B','All')][string]$Mode='A')
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$source=[IO.Path]::GetFullPath($SourceDir)
if (!(Test-Path -LiteralPath $source -PathType Container)) { throw 'Production data source directory is missing.' }
$manifest=Join-Path $repo 'inputs/manifests/data_package.csv'
$rows=Import-Csv -LiteralPath $manifest
function Get-CheckedPath([string]$Root,[string]$Relative) {
    $resolved=[IO.Path]::GetFullPath((Join-Path $Root $Relative))
    if (!$resolved.StartsWith($Root.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe destination: $Relative" }
    return $resolved
}
# Downloaded release archives are optional; a local expanded staging directory works directly.
$release=Join-Path $source 'release_assets.json'
if (Test-Path -LiteralPath $release) {
    $assets=Get-Content -LiteralPath $release -Raw | ConvertFrom-Json
    $manifestHash=(Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($assets.data_manifest_sha256 -ne $manifestHash) { throw 'Release belongs to a different canonical artifact manifest.' }
    foreach ($asset in $assets.assets) {
        if ($Mode -eq 'A' -and $asset.scope -eq 'PATH_B') { continue }
        $archive=Get-CheckedPath $source $asset.filename
        if (!(Test-Path -LiteralPath $archive)) { throw "Missing release asset: $($asset.filename)" }
        if ((Get-Item -LiteralPath $archive).Length -ne [long]$asset.size_bytes -or
            (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $asset.sha256) { throw "Release asset hash mismatch: $($asset.filename)" }
        # Hash-verified archives contain repository-relative data paths.
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip=[IO.Compression.ZipFile]::OpenRead($archive)
        try { foreach ($entry in $zip.Entries) { $null=Get-CheckedPath $source $entry.FullName } }
        finally { $zip.Dispose() }
        Expand-Archive -LiteralPath $archive -DestinationPath $source -Force
    }
}
$copied=0; $already=0; $bytes=0L
foreach ($row in $rows) {
    $scopes=$row.scope.Split('|')
    if ($scopes -contains 'GIT' -or (!($scopes -contains 'PATH_A') -and !($scopes -contains 'PATH_B'))) { continue }
    if ($Mode -eq 'A' -and !($scopes -contains 'PATH_A')) { continue }
    $group=if ($scopes -contains 'PATH_A') {'path_a'} else {'path_b'}
    $src=Get-CheckedPath $source ($group+'/'+$row.destination)
    $dst=Get-CheckedPath $repo $row.destination
    if (!(Test-Path -LiteralPath $src -PathType Leaf)) { throw "Missing canonical asset: $group/$($row.destination)" }
    if ((Get-Item -LiteralPath $src).Length -ne [long]$row.size_bytes -or
        (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash.ToLowerInvariant() -ne $row.sha256) { throw "Source hash mismatch: $($row.destination)" }
    if (Test-Path -LiteralPath $dst) {
        if ((Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash.ToLowerInvariant() -ne $row.sha256) { throw "Refusing to overwrite mismatched canonical data: $($row.destination)" }
        $already++
    } else {
        $null=New-Item -ItemType Directory -Path (Split-Path $dst -Parent) -Force
        Copy-Item -LiteralPath $src -Destination $dst
        if ((Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash.ToLowerInvariant() -ne $row.sha256) { throw "Copied asset verification failed: $($row.destination)" }
        $copied++
    }
    $bytes += [long]$row.size_bytes
}
if ($Mode -ne 'A') {
    foreach ($year in @(2040,2050)) {
        $folder=if ($year -eq 2040) {'accepted'} else {'accepted_2050'}
        $origin=Join-Path $repo "stage_a_results/price_handoff/$year/external_prices_hourly.parquet"
        $alias=Join-Path $repo "runtime_inputs/$folder/external_prices_hourly.parquet"
        if (!(Test-Path -LiteralPath $origin)) { throw "B10 authority absent: $year" }
        $hash=(Get-FileHash -LiteralPath $origin -Algorithm SHA256).Hash
        if (Test-Path -LiteralPath $alias) {
            if ((Get-FileHash -LiteralPath $alias -Algorithm SHA256).Hash -ne $hash) { throw "B10 runtime alias mismatch: $year" }
        } else { Copy-Item -LiteralPath $origin -Destination $alias }
    }
}
[ordered]@{status='PASS';mode=$Mode;copied=$copied;already_verified=$already;data_bytes=$bytes;repository=$repo;source=$source} | ConvertTo-Json -Compress

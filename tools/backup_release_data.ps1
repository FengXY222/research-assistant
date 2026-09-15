[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Source,

    [Parameter(Mandatory = $true)]
    [string]$DestinationRoot
)

$sourcePath = (Resolve-Path -LiteralPath $Source -ErrorAction Stop).Path
$sourceItem = Get-Item -LiteralPath $sourcePath -ErrorAction Stop
if (-not $sourceItem.PSIsContainer) {
    throw "Source must be a directory: $sourcePath"
}

New-Item -ItemType Directory -Path $DestinationRoot -Force -ErrorAction Stop | Out-Null
$destinationRootPath = (Resolve-Path -LiteralPath $DestinationRoot -ErrorAction Stop).Path
$destinationPrefix = Join-Path $destinationRootPath ("research-assistant-data-safeguard-" + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
$destination = $destinationPrefix
$collisionIndex = 0
while ($true) {
    if ($collisionIndex -gt 0) {
        $destination = "$destinationPrefix-$('{0:D2}' -f $collisionIndex)"
    }
    try {
        New-Item -ItemType Directory -Path $destination -ErrorAction Stop | Out-Null
        break
    }
    catch {
        if (-not (Test-Path -LiteralPath $destination -PathType Container)) {
            throw
        }
        $collisionIndex++
    }
}

Copy-Item -LiteralPath $sourcePath -Destination $destination -Recurse -Force -ErrorAction Stop
$backupPath = Join-Path $destination (Split-Path -Leaf $sourcePath)

$sourceFiles = @(Get-ChildItem -LiteralPath $sourcePath -File -Recurse -ErrorAction Stop)
$backupFiles = @(Get-ChildItem -LiteralPath $backupPath -File -Recurse -ErrorAction Stop)
$sourceDirectories = @(Get-ChildItem -LiteralPath $sourcePath -Directory -Recurse -ErrorAction Stop)
$backupDirectories = @(Get-ChildItem -LiteralPath $backupPath -Directory -Recurse -ErrorAction Stop)
$sourceByRelativePath = @{}
$backupByRelativePath = @{}
$sourceDirectoriesByRelativePath = @{}
$backupDirectoriesByRelativePath = @{}

foreach ($file in $sourceFiles) {
    $relativePath = [IO.Path]::GetRelativePath($sourcePath, $file.FullName)
    $sourceByRelativePath[$relativePath] = $file
}
foreach ($file in $backupFiles) {
    $relativePath = [IO.Path]::GetRelativePath($backupPath, $file.FullName)
    $backupByRelativePath[$relativePath] = $file
}
foreach ($directory in $sourceDirectories) {
    $relativePath = [IO.Path]::GetRelativePath($sourcePath, $directory.FullName)
    $sourceDirectoriesByRelativePath[$relativePath] = $directory
}
foreach ($directory in $backupDirectories) {
    $relativePath = [IO.Path]::GetRelativePath($backupPath, $directory.FullName)
    $backupDirectoriesByRelativePath[$relativePath] = $directory
}

$totalBytes = [int64](($sourceFiles | Measure-Object -Property Length -Sum).Sum)
$hashVerified = (
    $sourceByRelativePath.Count -eq $backupByRelativePath.Count -and
    $sourceDirectoriesByRelativePath.Count -eq $backupDirectoriesByRelativePath.Count
)
if ($hashVerified) {
    foreach ($relativePath in $sourceDirectoriesByRelativePath.Keys) {
        if (-not $backupDirectoriesByRelativePath.ContainsKey($relativePath)) {
            $hashVerified = $false
            break
        }
    }
}
if ($hashVerified) {
    foreach ($relativePath in $sourceByRelativePath.Keys) {
        $sourceFile = $sourceByRelativePath[$relativePath]
        $backupFile = $backupByRelativePath[$relativePath]
        $sourceHash = (Get-FileHash -LiteralPath $sourceFile.FullName -Algorithm SHA256 -ErrorAction Stop).Hash
        $backupHash = (Get-FileHash -LiteralPath $backupFile.FullName -Algorithm SHA256 -ErrorAction Stop).Hash
        if ($sourceHash -ne $backupHash -or $sourceFile.Length -ne $backupFile.Length) {
            $hashVerified = $false
            break
        }
    }
}
$summary = [ordered]@{
    backup_path  = $destination
    file_count   = $sourceFiles.Count
    total_bytes  = [int64]$totalBytes
    hash_verified = [bool]$hashVerified
}
$summary | ConvertTo-Json -Compress
if (-not $hashVerified) {
    exit 1
}

param([Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$sourcePackages = Join-Path $PSScriptRoot "runtime\Lib\site-packages"
if (-not (Test-Path -LiteralPath $sourcePackages)) { throw "请先安装锁定的组件依赖。" }
$cache = Join-Path $PSScriptRoot "download-cache"
New-Item -ItemType Directory -Path $cache -Force | Out-Null
$archive = Join-Path $cache "python-3.12.10-embed-amd64.zip"
if (-not (Test-Path -LiteralPath $archive) -or (Get-Item -LiteralPath $archive).Length -ne 11133606) {
    $download = $archive + ".part"
    & curl.exe --fail --location --connect-timeout 15 --max-time 180 --retry 2 --noproxy '*' --output $download "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
    if ($LASTEXITCODE -ne 0) { throw "独立 Python 下载失败，请重试构建。" }
    if ((Get-Item -LiteralPath $download).Length -ne 11133606) { throw "独立 Python 下载不完整。" }
    Copy-Item -LiteralPath $download -Destination $archive -Force
}
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
Expand-Archive -LiteralPath $archive -DestinationPath $Destination -Force
$packages = Join-Path $Destination "Lib\site-packages"
New-Item -ItemType Directory -Path $packages -Force | Out-Null
foreach ($item in Get-ChildItem -LiteralPath $sourcePackages) {
    if ($item.Name -notmatch '^(pip|py_spy|py-spy)(-|$)' -and $item.Name -ne '__pycache__') {
        Copy-Item -LiteralPath $item.FullName -Destination $packages -Recurse -Force
    }
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "python312._pth") -Destination (Join-Path $Destination "python312._pth") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "requirements.lock") -Destination (Join-Path $Destination "requirements.lock") -Force
$packageRoot = (Resolve-Path -LiteralPath $packages).Path.TrimEnd('\') + '\'
$testPackages = @('numpy','scipy','pandas','sklearn','skimage','onnx','onnxruntime')
$pruneTargets = @(Get-ChildItem -LiteralPath $packages -Directory -Recurse | Where-Object {
    $_.Name -eq '__pycache__' -or ($_.Name -in @('tests','test') -and $_.FullName.Substring($packageRoot.Length).Split('\')[0] -in $testPackages)
} | Sort-Object { $_.FullName.Length })
foreach ($pruneTarget in $pruneTargets) {
    $resolvedTarget = [IO.Path]::GetFullPath($pruneTarget.FullName)
    if (-not $resolvedTarget.StartsWith($packageRoot, [StringComparison]::OrdinalIgnoreCase)) { throw '发行资源清理路径越界。' }
    if (Test-Path -LiteralPath $resolvedTarget) { Remove-Item -LiteralPath $resolvedTarget -Recurse -Force }
}
& (Join-Path $Destination "python.exe") -I -B -c "import pdf2zh_next.high_level, babeldoc, pymupdf; print('Portable translator imports OK')"
if ($LASTEXITCODE -ne 0) { throw "便携翻译环境导入校验失败。" }

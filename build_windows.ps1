param(
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"
$appName = ([char]0x79d1).ToString() + [char]0x7814 + [char]0x52a9 + [char]0x624b
$appVersion = "12.2.1"
$issFile = Join-Path $PSScriptRoot ($appName + ".iss")
$basePythonPath = "S:\Python\Scripts\python.exe"
$buildEnvironment = Join-Path $PSScriptRoot ".build-venv"
$pythonPath = Join-Path $buildEnvironment "Scripts\python.exe"
$bundledFontData = "assets\fonts\NotoSansCJKsc-Regular.otf;assets\fonts"
$bundledTranslationData = "assets\translation\opus-mt-en-zh;assets\translation\opus-mt-en-zh"
if (-not (Test-Path -LiteralPath $basePythonPath)) {
    throw "未找到指定 Python 环境：$basePythonPath"
}
if (-not (Test-Path -LiteralPath $pythonPath)) {
    & $basePythonPath -m venv $buildEnvironment
    if ($LASTEXITCODE -ne 0) { throw "创建隔离打包环境失败，已停止打包。" }
}

& $pythonPath -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败，已停止打包。" }
& $pythonPath -m pip install pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 安装失败，已停止打包。" }

$originalPath = $env:PATH
$pathEntries = @(
    $originalPath.Split([IO.Path]::PathSeparator) |
        Where-Object {
            $_ -and ($_ -notmatch '(?i)dependencies[\\/]native[\\/]poppler(?:[\\/]|$)')
        }
)
$env:PATH = $pathEntries -join [IO.Path]::PathSeparator

Push-Location $PSScriptRoot
try {
    $excludedModules = @(
        "torch", "tensorflow", "tensorrt", "openvino", "paddle",
        "transformers", "sklearn", "skimage", "panel", "bokeh", "plotly",
        "altair", "geopandas", "fiona", "osgeo", "vtk", "vtkmodules",
        "xarray", "dask", "pyarrow", "h5py", "netCDF4", "cftime",
        "statsmodels", "sqlalchemy", "selenium", "folium", "pyproj",
        "imageio", "av", "pygame", "kaleido", "narwhals", "branca",
        "fsspec", "mako", "patsy", "PyQt5", "PyQt6", "PySide2",
        "matplotlib", "pandas", "scipy", "pytest", "numba", "IPython",
        "sphinx", "docutils", "nbformat", "zmq", "tkinter"
    )
    $pyInstallerArguments = @(
        "-m", "PyInstaller", "--noconsole", "--windowed", "--clean", "-y",
        "--hidden-import", "win32crypt",
        "--hidden-import", "rapidocr.main",
        "--hidden-import", "onnxruntime",
        "--hidden-import", "pypdfium2",
        "--hidden-import", "ctranslate2",
        "--hidden-import", "sentencepiece",
        "--collect-data", "rapidocr",
        "--collect-binaries", "ctranslate2"
    )
    foreach ($module in $excludedModules) {
        $pyInstallerArguments += @("--exclude-module", $module)
    }
    $pyInstallerArguments += @(
        "--add-data", $bundledFontData,
        "--add-data", $bundledTranslationData,
        "--version-file", "version_info.txt",
        "--name", $appName
    )
    if ($OneFile) {
        $pyInstallerArguments += "--onefile"
    }
    $pyInstallerArguments += "main.py"
    & $pythonPath @pyInstallerArguments
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败，未生成安装包。" }
} finally {
    Pop-Location
    $env:PATH = $originalPath
}

$packagedAppPath = Join-Path $PSScriptRoot ("dist\" + $appName)
$packagedDataPath = Join-Path $packagedAppPath "data"
if (Test-Path -LiteralPath $packagedDataPath) {
    throw "构建产物不应包含用户 data 目录，已停止生成安装包。"
}

if (-not $OneFile) {
    $packagedInternalPath = Join-Path $packagedAppPath "_internal"
    $conflictingIcuFiles = @()
    $icuUcPath = Join-Path $packagedInternalPath "icuuc.dll"
    if (Test-Path -LiteralPath $icuUcPath -PathType Leaf) {
        $conflictingIcuFiles += Get-Item -LiteralPath $icuUcPath
    }
    if (Test-Path -LiteralPath $packagedInternalPath -PathType Container) {
        $conflictingIcuFiles += @(
            Get-ChildItem -LiteralPath $packagedInternalPath -File -Filter "icudt*.dll"
        )
    }
    if ($conflictingIcuFiles.Count -gt 0) {
        $conflictList = ($conflictingIcuFiles.FullName -join ", ")
        throw "构建产物包含会遮蔽 Windows ICU 并导致 QtCore 无法加载的 DLL：$conflictList"
    }
}

$isccPath = $null
$isccCommand = Get-Command iscc.exe -ErrorAction SilentlyContinue
if ($isccCommand) {
    $isccPath = $isccCommand.Source
}
if (-not $isccPath) {
    $candidates = @(
        "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            $isccPath = $candidate
            break
        }
    }
}

if ($isccPath) {
    & $isccPath $issFile
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup 构建失败，未生成安装包。" }
    Write-Host ("Installer generated: " + (Join-Path $PSScriptRoot ("dist\" + $appName + "-v" + $appVersion + "-" + [char]0x5b89 + [char]0x88c5 + [char]0x5305 + ".exe")))
} else {
    Write-Host ("Application generated: " + (Join-Path $PSScriptRoot ("dist\" + $appName + "\" + $appName + ".exe")))
    Write-Host "Inno Setup 6 was not found; install it and run this script again to create the installer."
}

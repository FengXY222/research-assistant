param(
    [string]$Python = "C:\Python\python.exe",
    [string]$Destination = (Join-Path $env:LOCALAPPDATA "科研助手\Components\pdf_translator\runtime")
)
$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "请通过 -Python 指定 Python 3.12 的完整路径。" }
& $Python -c "import sys; assert sys.version_info[:2] == (3,12), 'Please use Python 3.12'"
if ($LASTEXITCODE -ne 0) { throw "组件需要 Python 3.12。" }
& $Python -m venv $Destination
if ($LASTEXITCODE -ne 0) { throw "无法创建独立运行环境。" }
$componentPython = Join-Path $Destination "Scripts\python.exe"
& $componentPython -m pip install -r (Join-Path $PSScriptRoot "requirements.lock")
if ($LASTEXITCODE -ne 0) { throw "安装失败；保留目录供下次重试，主程序依赖不受影响。" }
& $componentPython -m pip check
if ($LASTEXITCODE -ne 0) { throw "组件依赖校验失败。" }
Write-Host "独立 PDF 翻译环境已安装：$Destination"

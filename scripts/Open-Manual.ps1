param([string]$Root)

$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Не найден Python проекта. Выполните установку из README.md.'
}
if (-not $Root) { $Root = Join-Path $projectPath 'data' }
$Root = [System.IO.Path]::GetFullPath($Root)
Push-Location -LiteralPath $projectPath
try {
    & $pythonPath -m megamozg --root $Root manual
    if ($LASTEXITCODE -ne 0) { throw "Пульт завершился с кодом $LASTEXITCODE" }
} finally {
    Pop-Location
}

param([switch]$SkipTests)

$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectPath '.venv\Scripts\python.exe'

function Invoke-Checked {
    param([scriptblock]$Command, [string]$Message)
    & $Command
    if ($LASTEXITCODE -ne 0) { throw $Message }
}

Push-Location -LiteralPath $projectPath
try {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $py = Get-Command py -ErrorAction SilentlyContinue
        if ($py) {
            Invoke-Checked { & $py.Source -3 -m venv .venv } 'Не удалось создать окружение Python.'
        } else {
            $python = Get-Command python -ErrorAction SilentlyContinue
            if (-not $python) { throw 'Установите Python 3.12 или новее и повторите запуск.' }
            Invoke-Checked { & $python.Source -m venv .venv } 'Не удалось создать окружение Python.'
        }
    }

    Invoke-Checked { & $venvPython -c 'import sys; assert sys.version_info >= (3, 12), "Нужен Python 3.12 или новее"' } 'Установленный Python слишком старый.'
    Invoke-Checked { & $venvPython -m pip install -r requirements-lock.txt } 'Не удалось установить зависимости.'
    Invoke-Checked { & $venvPython -m pip install -e . --no-deps } 'Не удалось установить Brainalot.'
    Invoke-Checked { & $venvPython -m megamozg init } 'Не удалось создать локальное хранилище.'

    & (Join-Path $PSScriptRoot 'Register-NativeHost.ps1')

    if (-not $SkipTests) {
        Invoke-Checked { & $venvPython -m pytest } 'Проверки Python завершились ошибкой.'
        $node = Get-Command node -ErrorAction SilentlyContinue
        if ($node) {
            Invoke-Checked { & $node.Source --test extension/tests/*.test.mjs } 'Проверки расширения завершились ошибкой.'
        } else {
            Write-Host 'Node.js не найден: проверки расширения пропущены. Для обычной работы он не нужен.' -ForegroundColor Yellow
        }
    }

    Write-Host ''
    Write-Host 'Brainalot установлен.' -ForegroundColor Green
    Write-Host 'Токен для расширения:' -ForegroundColor Cyan
    & $venvPython -m megamozg token
    Write-Host ''
    Write-Host 'Дальше: откройте INSTALL_WINDOWS.md и выполните шаги подключения Chrome.'
} finally {
    Pop-Location
}

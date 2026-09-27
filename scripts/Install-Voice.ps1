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
    if (-not (Test-Path -LiteralPath $venvPython)) { throw 'Сначала запустите Install-MegaMozg.cmd.' }
    # Speech is recognised on this computer; the model downloads once on the first microphone click.
    Invoke-Checked { & $venvPython -m pip install 'faster-whisper>=1.1,<2' } 'Не удалось установить faster-whisper.'

    Write-Host ''
    Write-Host 'Голосовой ввод установлен.' -ForegroundColor Green
    Write-Host 'Перезапустите локальный сервис Brainalot. При первом нажатии на микрофон в окне «Записать»'
    Write-Host 'модель распознавания (около 500 МБ) скачается в data\state\models один раз.'
} finally {
    Pop-Location
}

# Lets the Brainalot panel of this source checkout start the service and connect without a token.
# The same registration the installer does (megamozg/integrate.py), for the current Windows user only:
# a native-messaging host manifest and HKCU\Software\Google\Chrome\NativeMessagingHosts\com.brainalot.host.
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Сначала запустите Install-MegaMozg.cmd.' }
& $python -m megamozg integrate install
if ($LASTEXITCODE -ne 0) { throw 'Не удалось зарегистрировать связь с Chrome.' }
Write-Host 'Связь панели с программой подключена. Обновите расширение в chrome://extensions и откройте панель.' -ForegroundColor Green

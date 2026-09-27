@echo off
setlocal
pushd "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Install-Voice.ps1"
if errorlevel 1 (
  echo.
  echo Voice input installation failed. See the message above.
  pause
  exit /b 1
)
pause
popd

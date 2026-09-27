@echo off
setlocal
pushd "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Register-MegaMozg-Autostart.ps1"
if errorlevel 1 pause
popd

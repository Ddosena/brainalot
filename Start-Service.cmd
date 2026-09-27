@echo off
setlocal
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found. See README.md.
  pause
  exit /b 1
)
wscript.exe //B "%~dp0scripts\Start-MegaMozg-Hidden.vbs"
popd

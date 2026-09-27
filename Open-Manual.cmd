@echo off
setlocal
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found. See README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m megamozg manual
if errorlevel 1 pause
popd

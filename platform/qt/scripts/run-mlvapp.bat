@echo off
setlocal

if "%~1"=="" (
  echo Usage: run-mlvapp.bat ^<path-to-MLVApp.exe^> [args...]
  echo Example:
  echo   run-mlvapp.bat C:\path\to\MLVApp.exe --help
  exit /b 1
)

set "exe=%~1"
shift
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-mlvapp.ps1" -ExePath "%exe%" --%*


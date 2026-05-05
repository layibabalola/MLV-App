@echo off
setlocal

rem ---------------------------------------------------------------
rem  Agent Bridge Dashboard Launcher
rem  Double-click to open the admin dashboard in your browser.
rem ---------------------------------------------------------------

set "LAUNCHER_DIR=%~dp0"
set "BRIDGE_TOOLS_DIR=%LAUNCHER_DIR%.."
set "LAUNCHER_SCRIPT=%BRIDGE_TOOLS_DIR%\dashboard_launcher.py"
set "BRIDGE_ROOT=%USERPROFILE%\.agent-bridge"

rem Prefer windowless Python launchers so no long-lived console stays visible.
where pyw >nul 2>&1
if %ERRORLEVEL% == 0 (
    start "" /b pyw -3 "%LAUNCHER_SCRIPT%" --bridge-root "%BRIDGE_ROOT%" --background
    exit /b 0
)

where pythonw >nul 2>&1
if %ERRORLEVEL% == 0 (
    start "" /b pythonw "%LAUNCHER_SCRIPT%" --bridge-root "%BRIDGE_ROOT%" --background
    exit /b 0
)

rem Fallback: start minimized if only console Python is available.
where py >nul 2>&1
if %ERRORLEVEL% == 0 (
    start "" /min py -3 "%LAUNCHER_SCRIPT%" --bridge-root "%BRIDGE_ROOT%" --background
    exit /b 0
)

where python >nul 2>&1
if %ERRORLEVEL% == 0 (
    start "" /min python "%LAUNCHER_SCRIPT%" --bridge-root "%BRIDGE_ROOT%" --background
    exit /b 0
)

echo.
echo ERROR: Could not start the dashboard. Is Python 3 installed?
pause
endlocal

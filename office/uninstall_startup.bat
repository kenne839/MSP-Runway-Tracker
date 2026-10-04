@echo off
setlocal
cd /d "%~dp0"

set "STARTUP_LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\KMSP_Runway_Tracker.lnk"

if exist "%STARTUP_LNK%" (
    del "%STARTUP_LNK%"
    echo [SUCCESS] Removed KMSP Runway Tracker from Windows Startup.
) else (
    echo [NOTICE] Shortcut was not present in Windows Startup.
)

echo.
pause

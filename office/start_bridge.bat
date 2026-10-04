@echo off
setlocal
title KMSP Runway Tracker - Office Bridge
cd /d "%~dp0\.."

if "%OFFICE_WEB_PORT%"=="" set OFFICE_WEB_PORT=18080

echo ========================================================
echo       KMSP Runway LED Tracker - Office PC Bridge        
echo ========================================================
echo Connecting via BLE 5.0 to KMSP-Runway-Office...
echo Web Dashboard: http://localhost:%OFFICE_WEB_PORT%
echo Simulator:     http://localhost:%OFFICE_WEB_PORT%/simulator
echo (Port %OFFICE_WEB_PORT% avoids corporate network conflicts)
echo ========================================================
echo.

python -m office.bridge --web-port %OFFICE_WEB_PORT% %*
if errorlevel 1 (
    echo.
    echo Bridge exited with error. Press any key to exit...
    pause >nul
)

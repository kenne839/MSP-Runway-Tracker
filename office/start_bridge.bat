@echo off
setlocal
title KMSP Runway Tracker - Office Bridge
cd /d "%~dp0\.."

echo ========================================================
echo       KMSP Runway LED Tracker - Office PC Bridge        
echo ========================================================
echo Starting BLE Link and Web Dashboard (http://localhost:8080)...
echo.

python -m office.bridge %*
if errorlevel 1 (
    echo.
    echo Bridge exited with error. Press any key to exit...
    pause >nul
)

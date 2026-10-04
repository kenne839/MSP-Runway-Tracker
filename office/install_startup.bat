@echo off
setlocal
cd /d "%~dp0"

echo Registering KMSP Runway Tracker in Windows Startup...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_startup.ps1"

echo.
pause

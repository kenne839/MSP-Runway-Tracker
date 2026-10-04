@echo off
setlocal
cd /d "%~dp0"
echo Launching KMSP Runway Tracker in silent background mode...
wscript.exe "%~dp0run_silent.vbs"
echo Bridge started silently in background.
echo To view live dashboard: http://localhost:18080/
echo To stop the bridge: run stop_bridge.bat
timeout /t 3 >nul

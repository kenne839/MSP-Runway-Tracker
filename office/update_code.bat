@echo off
setlocal
title KMSP Runway Tracker - Pull Latest Code
cd /d "%~dp0\.."

echo ========================================================
echo       KMSP Runway Tracker - Git Pull & Dependency Update
echo ========================================================
echo Current Directory: %CD%
echo.

echo [1/3] Fetching latest updates from GitHub...
git fetch origin

echo.
echo [2/3] Pulling branch feature/esp32-rpi-hardware...
git pull origin feature/esp32-rpi-hardware

echo.
echo [3/3] Ensuring Python dependencies are installed...
pip install -r office/requirements.txt

echo.
echo ========================================================
echo Update complete!
echo ========================================================
pause

@echo off
setlocal
title KMSP Runway Tracker - Hardware Test
cd /d "%~dp0\.."

echo ========================================================
echo       KMSP Runway LED Tracker - Hardware Test Utility
echo ========================================================
echo Connecting via BLE 5.0 to send test animations to board...
echo (Tests LED comets on all runways and tests OLED screen)
echo.

python -m office.bridge --test %*
echo.
pause

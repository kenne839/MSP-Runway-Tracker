@echo off
setlocal
cd /d "%~dp0"

set "PID_FILE=%~dp0.bridge.pid"

if exist "%PID_FILE%" (
    set /p BRIDGE_PID=<"%PID_FILE%"
    echo Terminating Office Bridge process (PID: %BRIDGE_PID%)...
    taskkill /PID %BRIDGE_PID% /F >nul 2>&1
    del "%PID_FILE%" >nul 2>&1
    echo [SUCCESS] Office Bridge stopped.
) else (
    echo [NOTICE] No active PID file found. Searching for running pythonw bridge...
    powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*office.bridge*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host 'Stopped PID' $_.ProcessId }"
    echo Done.
)

echo.
pause

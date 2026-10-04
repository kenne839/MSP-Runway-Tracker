# Installs KMSP Runway Tracker Office Bridge into Windows User Startup
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$vbsPath = Join-Path $scriptDir "run_silent.vbs"

$startupDir = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupDir "KMSP_Runway_Tracker.lnk"

Write-Host "Configuring Windows Startup for KMSP Runway Tracker..." -ForegroundColor Cyan

$wshShell = New-Object -ComObject WScript.Shell
$shortcut = $wshShell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "wscript.exe"
$shortcut.Arguments = "`"$vbsPath`""
$shortcut.WorkingDirectory = "$projectRoot"
$shortcut.Description = "KMSP Runway Tracker Office BLE Bridge and Web Dashboard"
$shortcut.Save()

Write-Host "Success! Shortcut created at:" -ForegroundColor Green
Write-Host "  $shortcutPath" -ForegroundColor White
Write-Host ""
Write-Host "The Office Bridge will now launch silently whenever you log in to Windows." -ForegroundColor Cyan
Write-Host "Embedded Web Dashboard & Simulator: http://localhost:8080" -ForegroundColor Yellow

@echo off
setlocal EnableExtensions
title Mojjss Focus Studio - Remove Tunnel Watchdog

fltmc >nul 2>&1
if errorlevel 1 (
  echo Requesting Administrator permission...
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
    "Start-Process -FilePath '%~f0' -Verb RunAs -WorkingDirectory '%~dp0'"
  exit /b
)

for %%I in ("%~dp0.") do set "ROOT=%%~fI"
set "SCRIPT=%ROOT%\tools\remove_tunnel_watchdog.ps1"
if not exist "%SCRIPT%" (
  echo Missing file: "%SCRIPT%"
  pause
  exit /b 2
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%

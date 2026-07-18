@echo off
setlocal EnableExtensions
title Mojjss Focus Studio - Install Tunnel Watchdog

fltmc >nul 2>&1
if errorlevel 1 (
  echo Requesting Administrator permission...
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
    "Start-Process -FilePath '%~f0' -Verb RunAs -WorkingDirectory '%~dp0'"
  exit /b
)

for %%I in ("%~dp0.") do set "ROOT=%%~fI"
set "SCRIPT=%ROOT%\tools\install_tunnel_watchdog.ps1"
if not exist "%SCRIPT%" (
  echo Missing file: "%SCRIPT%"
  pause
  exit /b 2
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -ProjectRoot "%ROOT%"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo Automatic tunnel recovery is enabled.
) else (
  echo Watchdog installation failed with code %RC%.
)
pause
exit /b %RC%

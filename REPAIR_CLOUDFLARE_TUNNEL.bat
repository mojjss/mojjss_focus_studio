@echo off
setlocal EnableExtensions
title Mojjss Focus Studio - Repair Cloudflare Tunnel v5.6

fltmc >nul 2>&1
if errorlevel 1 (
  echo Requesting Administrator permission...
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
    "Start-Process -FilePath '%~f0' -Verb RunAs -WorkingDirectory '%~dp0'"
  exit /b
)

for %%I in ("%~dp0.") do set "ROOT=%%~fI"
set "SCRIPT=%ROOT%\tools\repair_cloudflare_tunnel.ps1"
if not exist "%SCRIPT%" (
  echo Missing file: "%SCRIPT%"
  pause
  exit /b 2
)

echo Project root: %ROOT%
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -ProjectRoot "%ROOT%"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo Tunnel repair completed successfully.
) else (
  echo Tunnel repair finished with code %RC%.
  echo Run FOCUS_STUDIO_DIAGNOSIS.bat and upload its ZIP if the tunnel is still down.
)
pause
exit /b %RC%

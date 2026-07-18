@echo off
setlocal EnableExtensions
title Mojjss Focus Studio - Full Diagnostics v5.6

fltmc >nul 2>&1
if errorlevel 1 (
  echo Requesting Administrator permission for complete service and event diagnostics...
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
    "Start-Process -FilePath '%~f0' -Verb RunAs -WorkingDirectory '%~dp0'"
  exit /b
)

for %%I in ("%~dp0.") do set "ROOT=%%~fI"
set "SCRIPT=%ROOT%\tools\diagnose_focus_studio.ps1"
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
  echo The diagnosis ZIP was created on your Desktop.
) else (
  echo Diagnosis failed with code %RC%.
)
pause
exit /b %RC%

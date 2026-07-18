@echo off
setlocal EnableExtensions
title Mojjss Focus Studio v5.6 - Install or Update

for %%I in ("%~dp0.") do set "SOURCE=%%~fI"
set "DEFAULT_TARGET=D:\my_projects\mojjss_focus_studio"

echo Mojjss Focus Studio v5.6 Stability Update
echo.
echo This updater preserves config.json, databases, schedules, timer state,
echo exports, readable data, your virtual environment, and Git history.
echo It copies only project/application files.
echo.
set /p "TARGET=Target folder [%DEFAULT_TARGET%]: "
if not defined TARGET set "TARGET=%DEFAULT_TARGET%"
for %%I in ("%TARGET%") do set "TARGET=%%~fI"

if /i "%TARGET%"=="%SOURCE%" (
  echo.
  echo This package is already in the target folder. No copy is needed.
  goto :requirements
)

echo.
echo Source: %SOURCE%
echo Target: %TARGET%
echo.
choice /c YN /n /m "Continue? [Y/N]: "
if errorlevel 2 exit /b 1

if not exist "%TARGET%" mkdir "%TARGET%"

robocopy "%SOURCE%" "%TARGET%" /E /R:2 /W:2 /NFL /NDL /NP ^
  /XD ".git" "__pycache__" ".venv" "data" "exports" "backups" ^
  /XF "config.json" "focus_history.db" "focus_history.db-wal" "focus_history.db-shm" ^
      "schedule.csv" "timer_state.json" "*.pyc" "*.log" "*.token" >nul
set "ROBO=%ERRORLEVEL%"
if %ROBO% GEQ 8 (
  echo Copy failed. Robocopy code: %ROBO%
  pause
  exit /b %ROBO%
)

:requirements
echo.
echo Checking Python 3.11...
where py.exe >nul 2>&1
if errorlevel 1 (
  echo Python launcher was not found.
  echo Install Python 3.11, then run:
  echo   py -3.11 -m pip install -r "%TARGET%\desktop_app\requirements.txt"
  pause
  exit /b 2
)

if not exist "%TARGET%\desktop_app\.venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py.exe -3.11 -m venv "%TARGET%\desktop_app\.venv"
  if errorlevel 1 (
    echo Could not create the virtual environment.
    pause
    exit /b 3
  )
)

echo Installing/updating requirements...
"%TARGET%\desktop_app\.venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
  echo Pip upgrade failed.
  pause
  exit /b 4
)
"%TARGET%\desktop_app\.venv\Scripts\python.exe" -m pip install -r "%TARGET%\desktop_app\requirements.txt"
if errorlevel 1 (
  echo Requirement installation failed.
  pause
  exit /b 5
)

echo.
echo Installation/update completed.
echo Your private data was not overwritten.
echo.
echo Start the app with:
echo   "%TARGET%\START_FOCUS_STUDIO.bat"
echo.
echo Optional automatic tunnel recovery:
echo   "%TARGET%\INSTALL_TUNNEL_WATCHDOG.bat"
echo.
pause
exit /b 0

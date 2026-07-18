@echo off
setlocal EnableExtensions
title Mojjss Focus Studio - Debug Console
cd /d "%~dp0desktop_app"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" app.py
  goto :done
)

where py.exe >nul 2>&1
if not errorlevel 1 (
  py.exe -3.11 app.py
  goto :done
)

python.exe app.py

:done
echo.
echo Focus Studio exited with code %ERRORLEVEL%.
pause

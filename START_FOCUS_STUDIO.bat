@echo off
setlocal EnableExtensions
cd /d "%~dp0desktop_app"

set "PYTHONW="
if exist ".venv\Scripts\pythonw.exe" set "PYTHONW=%CD%\.venv\Scripts\pythonw.exe"
if not defined PYTHONW (
  for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do if not defined PYTHONW set "PYTHONW=%%P"
)

if defined PYTHONW (
  start "" "%PYTHONW%" "%CD%\app.py"
  exit /b 0
)

where pyw.exe >nul 2>&1
if not errorlevel 1 (
  start "" pyw.exe -3.11 "%CD%\app.py"
  exit /b 0
)

echo pythonw.exe was not found.
echo Run INSTALL_UPDATE_KEEP_DATA.bat or install Python 3.11 and the requirements.
pause
exit /b 2

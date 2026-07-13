@echo off
setlocal
cd /d "%~dp0"
echo.
echo mojjss private camera diagnostic v5.0
echo Keep the desktop app open while this runs.
echo.
python diagnose_private_camera.py
if errorlevel 1 (
  echo.
  echo Diagnostic found a failure. See PRIVATE_CAMERA_DIAGNOSTIC.txt
) else (
  echo.
  echo Diagnostic finished. See PRIVATE_CAMERA_DIAGNOSTIC.txt
)
echo.
pause

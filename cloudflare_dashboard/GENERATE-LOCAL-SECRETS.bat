@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0GENERATE-LOCAL-SECRETS.ps1"
if errorlevel 1 (
  echo.
  echo Secret generation failed.
  pause
  exit /b 1
)
echo.
echo Keys generated. Add them manually in Cloudflare Pages.
pause

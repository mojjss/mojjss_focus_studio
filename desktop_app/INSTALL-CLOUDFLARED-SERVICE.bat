@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo mojjss Focus Studio - Cloudflare Tunnel service installer
echo =========================================================
echo.
where cloudflared.exe >nul 2>&1
if errorlevel 1 (
  echo cloudflared.exe was not found in PATH.
  echo Install the Windows cloudflared package from the Cloudflare Tunnel dashboard,
  echo then run this file again as Administrator.
  echo.
  exit /b 1
)

net session >nul 2>&1
if errorlevel 1 (
  echo This installer must be run as Administrator.
  echo Right-click this BAT file and choose "Run as administrator".
  exit /b 1
)

echo In Cloudflare, create a tunnel and copy the Windows installation token.
echo The public hostname must be:
echo   camera.timer.mojjss.ir  --^>  http://127.0.0.1:8788
echo.
set /p TUNNEL_TOKEN=Paste the tunnel token here: 
if "%TUNNEL_TOKEN%"=="" (
  echo No token entered.
  exit /b 1
)

echo.
echo Installing the cloudflared Windows service...
cloudflared.exe service install %TUNNEL_TOKEN%
if errorlevel 1 (
  echo.
  echo Installation failed. Check the token and run this file as Administrator.
  exit /b 1
)

sc start cloudflared >nul 2>&1

echo.
echo Service installed. In the desktop app use:
echo   Camera URL: https://camera.timer.mojjss.ir
echo   Allowed origins: https://timer.mojjss.ir, https://camera.timer.mojjss.ir
echo   Require Tailscale identity headers: OFF
echo.
echo Test after the desktop app is running:
echo   https://camera.timer.mojjss.ir/api/health

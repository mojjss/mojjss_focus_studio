@echo off
setlocal
set TS=C:\Program Files\Tailscale\tailscale.exe
set PORT=8788

echo.
echo mojjss live activity - Manual Tailscale Serve timeout fix
echo.

if not exist "%TS%" (
  echo Tailscale CLI not found at "%TS%"
  pause
  exit /b 1
)

echo [1/5] Checking Tailscale status...
"%TS%" status
if errorlevel 1 (
  echo.
  echo Tailscale is not connected. Sign in/open Tailscale, then run this again.
  pause
  exit /b 1
)

echo.
echo [2/5] Resetting old Serve config...
"%TS%" serve reset

echo.
echo [3/5] Starting foreground Serve so you can see/approve any consent URL...
echo If a browser approval page appears, approve it.
echo When you see the https://*.ts.net URL, press Ctrl+C here to continue.
echo.
pause
"%TS%" serve %PORT%

echo.
echo [4/5] Starting persistent background Serve...
"%TS%" serve --yes --bg http://127.0.0.1:%PORT%
if errorlevel 1 (
  echo Background Serve failed. Try running this BAT as Administrator.
  pause
  exit /b 1
)

echo.
echo [5/5] Serve status:
"%TS%" serve status

echo.
echo Copy the https://*.ts.net URL into the desktop app if it was not saved automatically.
pause

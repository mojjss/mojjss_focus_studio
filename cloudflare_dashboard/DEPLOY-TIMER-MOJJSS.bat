@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0DEPLOY-TIMER-MOJJSS.ps1"
pause

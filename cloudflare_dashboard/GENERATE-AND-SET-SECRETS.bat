@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0GENERATE-AND-SET-SECRETS.ps1"
pause

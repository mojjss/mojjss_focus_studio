#requires -Version 5.1
Set-StrictMode -Version 1.0
$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "This remover must run as Administrator." -ForegroundColor Red
    exit 5
}

$name = "Mojjss Focus Studio Tunnel Watchdog"
Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "Watchdog removed." -ForegroundColor Green
exit 0

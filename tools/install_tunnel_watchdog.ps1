#requires -Version 5.1
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

Set-StrictMode -Version 1.0
$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    Write-Host "This installer must run as Administrator." -ForegroundColor Red
    exit 5
}

$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot -ErrorAction Stop).Path
$watchdog = Join-Path $ProjectRoot "tools\tunnel_watchdog.ps1"
if (-not (Test-Path -LiteralPath $watchdog)) {
    Write-Host "Missing watchdog script: $watchdog" -ForegroundColor Red
    exit 2
}

$name = "Mojjss Focus Studio Tunnel Watchdog"
$argument = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -ProjectRoot "{1}"' -f $watchdog, $ProjectRoot
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 2) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $name `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -User "SYSTEM" `
    -RunLevel Highest `
    -Force | Out-Null
Start-ScheduledTask -TaskName $name

Write-Host "Watchdog installed and started." -ForegroundColor Green
Write-Host "It checks every two minutes and only restarts cloudflared after confirmed failures." -ForegroundColor Gray
Write-Host "Status: C:\ProgramData\MojjssFocusStudio\tunnel_watchdog_status.json" -ForegroundColor Cyan
exit 0

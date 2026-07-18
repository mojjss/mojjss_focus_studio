#requires -Version 5.1
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

Set-StrictMode -Version 1.0
$ErrorActionPreference = "Stop"

function Resolve-ProjectRoot {
    param([string]$Candidate)
    $values = @(
        $Candidate,
        (Split-Path -Parent $PSScriptRoot),
        "D:\my_projects\mojjss_focus_studio"
    )
    foreach ($value in $values) {
        if ([string]::IsNullOrWhiteSpace($value)) { continue }
        try {
            $resolved = (Resolve-Path -LiteralPath $value -ErrorAction Stop).Path
            if (Test-Path -LiteralPath (Join-Path $resolved "desktop_app\app.py")) {
                return $resolved
            }
        } catch {}
    }
    return ""
}

function Invoke-HealthCheck {
    param([string]$Url, [int]$Timeout = 12)
    if ([string]::IsNullOrWhiteSpace($Url)) {
        return [pscustomobject]@{ Status = 0; Body = "Not configured"; Error = "Not configured" }
    }
    $bodyFile = Join-Path $env:TEMP ("focus_watchdog_" + [guid]::NewGuid().ToString("N") + ".txt")
    try {
        $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
        if ($curl) {
            $raw = & $curl.Source --silent --show-error --location `
                --connect-timeout 6 --max-time $Timeout --noproxy "*" `
                --output $bodyFile --write-out "%{http_code}" $Url 2>&1
            $status = 0
            [void][int]::TryParse((($raw | Select-Object -Last 1) -as [string]), [ref]$status)
            $body = if (Test-Path -LiteralPath $bodyFile) {
                Get-Content -LiteralPath $bodyFile -Raw -ErrorAction SilentlyContinue
            } else { "" }
            return [pscustomobject]@{ Status = $status; Body = [string]$body; Error = "" }
        }
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $Timeout
            return [pscustomobject]@{ Status = [int]$response.StatusCode; Body = [string]$response.Content; Error = "" }
        } catch {
            $status = 0
            if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
                $status = [int]$_.Exception.Response.StatusCode
            }
            return [pscustomobject]@{ Status = $status; Body = ""; Error = $_.Exception.Message }
        }
    } finally {
        Remove-Item -LiteralPath $bodyFile -Force -ErrorAction SilentlyContinue
    }
}

function Read-JsonFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { return $null }
}

$ProjectRoot = Resolve-ProjectRoot $ProjectRoot
$programData = Join-Path $env:ProgramData "MojjssFocusStudio"
New-Item -ItemType Directory -Path $programData -Force | Out-Null
$statusPath = Join-Path $programData "tunnel_watchdog_status.json"
$statePath = Join-Path $programData "tunnel_watchdog_state.json"
$logPath = Join-Path $programData "tunnel_watchdog.log"

if ((Test-Path -LiteralPath $logPath) -and (Get-Item -LiteralPath $logPath).Length -gt 2097152) {
    Move-Item -LiteralPath $logPath -Destination "$logPath.old" -Force -ErrorAction SilentlyContinue
}

$checkedAt = (Get-Date).ToUniversalTime().ToString("o")
$lastAction = "No action needed"
$serviceRestarted = $false
$errorText = ""
$cameraPort = 8788
$publicBase = "https://camera.mojjss.ir"
$configPath = if ($ProjectRoot) { Join-Path $ProjectRoot "desktop_app\config.json" } else { "" }
$config = if ($configPath) { Read-JsonFile $configPath } else { $null }
if ($config) {
    try { if ($config.tailscale_camera_port) { $cameraPort = [int]$config.tailscale_camera_port } } catch {}
    try {
        if ($config.tailscale_camera_url) {
            $publicBase = ([string]$config.tailscale_camera_url).Trim().TrimEnd("/")
        }
    } catch {}
}
$localUrl = "http://127.0.0.1:$cameraPort/api/health"
$publicUrl = $publicBase.TrimEnd("/") + "/api/health"

$local = Invoke-HealthCheck $localUrl 8
$public = Invoke-HealthCheck $publicUrl 15
$service = Get-Service -Name Cloudflared -ErrorAction SilentlyContinue
$serviceExists = $null -ne $service
$serviceRunning = $serviceExists -and $service.Status -eq "Running"

$state = Read-JsonFile $statePath
$consecutiveFailures = 0
$lastRestartAt = [datetime]::MinValue
if ($state) {
    try { $consecutiveFailures = [int]$state.consecutive_failures } catch {}
    try { $lastRestartAt = [datetime]::Parse([string]$state.last_restart_at).ToUniversalTime() } catch {}
}

try {
    if (-not $serviceExists) {
        $lastAction = "Cloudflared service is missing; run REPAIR_CLOUDFLARE_TUNNEL.bat"
        $consecutiveFailures = 0
    } elseif (-not $serviceRunning) {
        Start-Service -Name Cloudflared -ErrorAction Stop
        Start-Sleep -Seconds 12
        $serviceRestarted = $true
        $lastAction = "Started stopped Cloudflared service"
        $lastRestartAt = (Get-Date).ToUniversalTime()
        $public = Invoke-HealthCheck $publicUrl 15
        $service = Get-Service -Name Cloudflared -ErrorAction SilentlyContinue
        $serviceRunning = $service -and $service.Status -eq "Running"
        $consecutiveFailures = 0
    } elseif ($public.Status -ge 200 -and $public.Status -lt 500) {
        # Any 2xx-4xx response proves that the public route reached Cloudflare/the app.
        # This includes Cloudflare Access 401/403 and a harmless 404 on a custom route.
        $consecutiveFailures = 0
        $lastAction = "Tunnel route is reachable (HTTP $($public.Status))"
    } elseif ($local.Status -eq 200) {
        $bodyText = (([string]$public.Body) + " " + ([string]$public.Error))
        $isConnectorFailure = (
            $public.Status -eq 0 -or
            $public.Status -eq 530 -or
            $bodyText -match '(?i)1033|Argo Tunnel|no healthy.*connector'
        )
        $isOriginFailure = $public.Status -in @(502, 504)
        if ($isConnectorFailure -or $isOriginFailure) {
            $consecutiveFailures++
            $minutesSinceRestart = ((Get-Date).ToUniversalTime() - $lastRestartAt).TotalMinutes
            $requiredFailures = if ($isConnectorFailure) { 2 } else { 3 }
            if ($consecutiveFailures -ge $requiredFailures -and $minutesSinceRestart -ge 5) {
                Restart-Service -Name Cloudflared -Force -ErrorAction Stop
                Start-Sleep -Seconds 15
                $serviceRestarted = $true
                $reason = if ($isConnectorFailure) { "connector/1033 failure" } else { "repeated 502/504 origin-route failure" }
                $lastAction = "Restarted Cloudflared after $reason"
                $lastRestartAt = (Get-Date).ToUniversalTime()
                $public = Invoke-HealthCheck $publicUrl 15
                $service = Get-Service -Name Cloudflared -ErrorAction SilentlyContinue
                $serviceRunning = $service -and $service.Status -eq "Running"
                $consecutiveFailures = 0
            } else {
                $lastAction = "Public route failure detected; waiting for confirmation before restart"
            }
        } else {
            $consecutiveFailures = 0
            $lastAction = "Public endpoint returned HTTP $($public.Status); no automatic restart was performed"
        }
    } else {
        $consecutiveFailures = 0
        $lastAction = "Local camera is unavailable; Cloudflared was not restarted"
    }
} catch {
    $errorText = $_.Exception.Message
    $lastAction = "Watchdog action failed"
}

$stateOut = [ordered]@{
    consecutive_failures = $consecutiveFailures
    last_restart_at = if ($lastRestartAt -eq [datetime]::MinValue) { "" } else { $lastRestartAt.ToString("o") }
}
$stateOut | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $statePath -Encoding UTF8

$statusOut = [ordered]@{
    checked_at = $checkedAt
    project_root_found = -not [string]::IsNullOrWhiteSpace($ProjectRoot)
    service_exists = $serviceExists
    service_running = $serviceRunning
    local_http = [int]$local.Status
    public_http = [int]$public.Status
    consecutive_failures = $consecutiveFailures
    service_restarted = $serviceRestarted
    last_action = $lastAction
    error = $errorText
}
$statusOut | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statusPath -Encoding UTF8

$logLine = "{0} service={1} local={2} public={3} failures={4} action={5}" -f `
    $checkedAt, $serviceRunning, $local.Status, $public.Status, $consecutiveFailures, $lastAction
Add-Content -LiteralPath $logPath -Value $logLine -Encoding UTF8
exit 0

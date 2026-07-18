#requires -Version 5.1
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

Set-StrictMode -Version 1.0
$ErrorActionPreference = "Continue"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Resolve-ProjectRootSafe {
    param([AllowNull()][string]$Candidate)
    $candidates = @(
        $Candidate,
        (Split-Path -Parent $PSScriptRoot),
        (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),
        "D:\my_projects\mojjss_focus_studio"
    )
    foreach ($item in $candidates) {
        if ([string]::IsNullOrWhiteSpace($item)) { continue }
        try {
            $resolved = (Resolve-Path -LiteralPath $item -ErrorAction Stop).Path
            if (Test-Path -LiteralPath (Join-Path $resolved "desktop_app\app.py")) {
                return $resolved
            }
        } catch {}
    }
    return ""
}

function Protect-Text {
    param([AllowNull()][string]$Text)
    if ($null -eq $Text) { return "" }
    $value = [string]$Text
    $value = [regex]::Replace(
        $value,
        '(?i)(--token(?:=|\s+))(?:"[^"]+"|\S+)',
        '$1<REDACTED>'
    )
    $value = [regex]::Replace(
        $value,
        '(?i)(--token-file(?:=|\s+))(?:"[^"]+"|\S+)',
        '$1<PRIVATE_TOKEN_FILE>'
    )
    $value = [regex]::Replace(
        $value,
        '(?i)\beyJ[A-Za-z0-9._~-]{20,}\b',
        '<REDACTED_TUNNEL_TOKEN>'
    )
    $value = [regex]::Replace(
        $value,
        '(?i)(authorization\s*:\s*(?:bearer|basic)\s+)\S+',
        '$1<REDACTED>'
    )
    $value = [regex]::Replace(
        $value,
        '(?i)((?:password|secret|api[_-]?key|write[_-]?key|viewer[_-]?key|owner[_-]?key|token)\s*[=:]\s*)\S+',
        '$1<REDACTED>'
    )
    $value = [regex]::Replace(
        $value,
        '(?i)("?(?:password|secret|token|key|hash|salt)"?\s*:\s*")[^"]*(")',
        '$1<REDACTED>$2'
    )
    return $value
}

function Add-Line {
    param([string]$Path, [AllowNull()]$Value)
    $text = Protect-Text (($Value | Out-String).TrimEnd())
    Add-Content -LiteralPath $Path -Value $text -Encoding UTF8
}

function Add-Section {
    param([string]$Path, [string]$Title)
    Add-Content -LiteralPath $Path -Value (
        "`r`n" + ("=" * 78) + "`r`n" + $Title + "`r`n" + ("=" * 78)
    ) -Encoding UTF8
}

function Add-Summary {
    param([string]$Level, [string]$Message)
    Add-Content -LiteralPath $script:SummaryFile -Value "[$Level] $(Protect-Text $Message)" -Encoding UTF8
}

function Capture {
    param([string]$Path, [string]$Title, [scriptblock]$Block)
    Add-Section $Path $Title
    try {
        $output = & $Block 2>&1 | Out-String -Width 240
        Add-Line $Path $output
    } catch {
        Add-Line $Path ("ERROR: " + $_.Exception.Message)
    }
}

function Get-Config {
    param([string]$Root)
    $path = Join-Path $Root "desktop_app\config.json"
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        return Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Get-CloudflaredPath {
    try {
        $service = Get-CimInstance Win32_Service -Filter "Name='Cloudflared'" -ErrorAction Stop
        if ($service.PathName) {
            $match = [regex]::Match(
                [string]$service.PathName,
                '^\s*"([^"]*cloudflared\.exe)"|^\s*([^\s]*cloudflared\.exe)',
                [Text.RegularExpressions.RegexOptions]::IgnoreCase
            )
            if ($match.Success) {
                $candidate = if ($match.Groups[1].Value) {
                    $match.Groups[1].Value
                } else {
                    $match.Groups[2].Value
                }
                if (Test-Path -LiteralPath $candidate) {
                    return (Resolve-Path -LiteralPath $candidate).Path
                }
            }
        }
    } catch {}

    try {
        $command = Get-Command cloudflared.exe -ErrorAction Stop
        if ($command.Source -and (Test-Path -LiteralPath $command.Source)) {
            return $command.Source
        }
    } catch {}

    $candidates = @(
        "C:\Program Files\cloudflared\cloudflared.exe",
        "C:\Program Files (x86)\cloudflared\cloudflared.exe",
        "C:\Cloudflared\bin\cloudflared.exe",
        "C:\Windows\System32\cloudflared.exe",
        (Join-Path $env:USERPROFILE "cloudflared.exe"),
        (Join-Path $ProjectRoot "cloudflared.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

function Get-PythonPath {
    param([string]$Root)
    $candidates = @(
        (Join-Path $Root "desktop_app\.venv\Scripts\python.exe"),
        (Join-Path $Root ".venv\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    try {
        $python = Get-Command python.exe -ErrorAction Stop
        if ($python.Source) { return $python.Source }
    } catch {}
    try {
        $py = Get-Command py.exe -ErrorAction Stop
        if ($py.Source) { return $py.Source }
    } catch {}
    return $null
}

function Invoke-UrlCheck {
    param(
        [string]$Url,
        [int]$ConnectTimeout = 8,
        [int]$MaxTime = 18,
        [switch]$UseUserProxy
    )
    $bodyFile = Join-Path $env:TEMP ("focus_url_" + [guid]::NewGuid().ToString("N") + ".txt")
    try {
        $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
        if ($curl) {
            $arguments = @(
                "--silent", "--show-error", "--location",
                "--connect-timeout", [string]$ConnectTimeout,
                "--max-time", [string]$MaxTime,
                "--output", $bodyFile,
                "--write-out", "%{http_code}"
            )
            if (-not $UseUserProxy) {
                $arguments += @("--noproxy", "*")
            }
            $arguments += $Url
            $statusText = & $curl.Source @arguments 2>&1
            $exitCode = $LASTEXITCODE
            $status = 0
            $last = (($statusText | Select-Object -Last 1) -as [string])
            [void][int]::TryParse($last, [ref]$status)
            $body = if (Test-Path -LiteralPath $bodyFile) {
                Get-Content -LiteralPath $bodyFile -Raw -ErrorAction SilentlyContinue
            } else { "" }
            return [pscustomobject]@{
                Url = $Url
                Status = $status
                ExitCode = $exitCode
                Body = Protect-Text (($body -as [string]).Trim())
            }
        }

        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $MaxTime
            return [pscustomobject]@{
                Url = $Url
                Status = [int]$response.StatusCode
                ExitCode = 0
                Body = Protect-Text (($response.Content -as [string]).Trim())
            }
        } catch {
            $status = 0
            if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
                $status = [int]$_.Exception.Response.StatusCode
            }
            return [pscustomobject]@{
                Url = $Url
                Status = $status
                ExitCode = 1
                Body = Protect-Text $_.Exception.Message
            }
        }
    } finally {
        Remove-Item -LiteralPath $bodyFile -Force -ErrorAction SilentlyContinue
    }
}

function Format-UrlCheck {
    param($Result)
    if (-not $Result) { return "No result." }
    $body = [string]$Result.Body
    if ($body.Length -gt 1200) { $body = $body.Substring(0, 1200) + "..." }
    return "URL=$($Result.Url)`r`nHTTP=$($Result.Status)`r`nExitCode=$($Result.ExitCode)`r`nBody=$body"
}

function Run-CloudflaredDiag {
    param([string]$CloudflaredPath, [string]$OutputPath)
    Add-Section $OutputPath "cloudflared tunnel diag (45 second limit)"
    if (-not $CloudflaredPath) {
        Add-Line $OutputPath "cloudflared.exe was not found."
        return
    }

    $work = Join-Path $env:TEMP ("FocusCloudflaredDiag_" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    $stdout = Join-Path $work "stdout.txt"
    $stderr = Join-Path $work "stderr.txt"
    try {
        $process = Start-Process -FilePath $CloudflaredPath `
            -ArgumentList @("tunnel", "diag") `
            -WorkingDirectory $work `
            -PassThru `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdout `
            -RedirectStandardError $stderr
        if (-not $process.WaitForExit(45000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            Add-Line $OutputPath "cloudflared tunnel diag timed out and was stopped."
        }
        if (Test-Path -LiteralPath $stdout) {
            Add-Line $OutputPath (Get-Content -LiteralPath $stdout -Raw)
        }
        if (Test-Path -LiteralPath $stderr) {
            Add-Line $OutputPath (Get-Content -LiteralPath $stderr -Raw)
        }
    } catch {
        Add-Line $OutputPath ("cloudflared tunnel diag failed: " + $_.Exception.Message)
    } finally {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Clear-Host
Write-Host "Mojjss Focus Studio - Full Diagnostics" -ForegroundColor Cyan
Write-Host "No network reset, adapter reset, proxy reset, v2rayN reset, or Tailscale reset is performed." -ForegroundColor Gray
Write-Host ""

$originalProjectRoot = $ProjectRoot
$ProjectRoot = Resolve-ProjectRootSafe $ProjectRoot
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    Write-Host "Project root was not found. Received: $originalProjectRoot" -ForegroundColor Red
    Write-Host "Put this tool inside the Focus Studio project folder and run it again." -ForegroundColor Yellow
    exit 10
}

$desktop = [Environment]::GetFolderPath("Desktop")
if (-not $desktop) { $desktop = $env:USERPROFILE }
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$script:ReportDir = Join-Path $desktop "FOCUS_STUDIO_DIAGNOSIS_$stamp"
New-Item -ItemType Directory -Path $script:ReportDir -Force | Out-Null

$script:SummaryFile = Join-Path $script:ReportDir "00_SUMMARY.txt"
$systemFile = Join-Path $script:ReportDir "01_SYSTEM_NETWORK.txt"
$projectFile = Join-Path $script:ReportDir "02_PROJECT_AND_CONFIG.txt"
$appFile = Join-Path $script:ReportDir "03_APP_PORTS_CAMERA.txt"
$tunnelFile = Join-Path $script:ReportDir "04_CLOUDFLARED_TUNNEL.txt"
$cloudFile = Join-Path $script:ReportDir "05_PUBLIC_CLOUDFLARE.txt"
$eventsFile = Join-Path $script:ReportDir "06_WINDOWS_EVENTS.txt"
$codeFile = Join-Path $script:ReportDir "07_CODE_DATABASE_CHECKS.txt"
$privacyFile = Join-Path $script:ReportDir "08_PRIVACY_NOTICE.txt"
$analysisFile = Join-Path $script:ReportDir "09_WATCHDOG_AND_ANALYSIS.txt"

"Focus Studio diagnosis`r`nCreated: $(Get-Date -Format o)`r`nProject: $ProjectRoot`r`n" |
    Set-Content -LiteralPath $script:SummaryFile -Encoding UTF8

$config = Get-Config $ProjectRoot
$cameraPort = 8788
$monitorPort = 8765
$cameraUrl = "https://camera.mojjss.ir"
$dashboardUrl = "https://timer.mojjss.ir"
if ($config) {
    if ($config.tailscale_camera_port) { $cameraPort = [int]$config.tailscale_camera_port }
    if ($config.monitor_port) { $monitorPort = [int]$config.monitor_port }
    if ($config.tailscale_camera_url) {
        $cameraUrl = ([string]$config.tailscale_camera_url).Trim().TrimEnd("/")
    }
    if ($config.cloud_dashboard_url) {
        $dashboardUrl = ([string]$config.cloud_dashboard_url).Trim().TrimEnd("/")
    }
}
$localCameraUrl = "http://127.0.0.1:$cameraPort/api/health"
$localMonitorUrl = "http://127.0.0.1:$monitorPort/api/health"
$publicCameraUrl = $cameraUrl.TrimEnd("/") + "/api/health"
$dashboardHealthUrl = $dashboardUrl.TrimEnd("/") + "/api/health"
$dashboardStatusUrl = $dashboardUrl.TrimEnd("/") + "/api/status"

Add-Summary "INFO" "Administrator: $(Test-IsAdministrator)"
Add-Summary "INFO" "Project root: $ProjectRoot"
Add-Summary "INFO" "Camera URL: $cameraUrl"
Add-Summary "INFO" "Dashboard URL: $dashboardUrl"
Add-Summary "INFO" "This report excludes config.json contents, databases, schedules, camera images, passwords, keys, and tunnel tokens."

Capture $systemFile "WINDOWS AND HARDWARE" {
    Get-ComputerInfo |
        Select-Object WindowsProductName, WindowsVersion, OsBuildNumber,
            OsArchitecture, CsSystemType, CsManufacturer, CsModel
    Get-CimInstance Win32_OperatingSystem |
        Select-Object Caption, Version, BuildNumber, LastBootUpTime, LocalDateTime
}

Capture $systemFile "NETWORK ADAPTERS AND ADDRESSES" {
    Get-NetAdapter |
        Sort-Object Status, Name |
        Select-Object Name, InterfaceDescription, Status, LinkSpeed, MacAddress
    Get-NetIPConfiguration |
        Select-Object InterfaceAlias, InterfaceDescription, IPv4Address,
            IPv4DefaultGateway, DNSServer
}

Capture $systemFile "DNS, PROXY, ROUTES, VPN PROCESSES" {
    "WinHTTP proxy:"
    & netsh.exe winhttp show proxy
    ""
    "User Internet Settings (secret-free fields):"
    Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" `
        -ErrorAction SilentlyContinue |
        Select-Object ProxyEnable, ProxyServer, AutoConfigURL
    ""
    "Proxy environment variables:"
    Get-ChildItem Env: |
        Where-Object { $_.Name -match "proxy" } |
        Select-Object Name, Value
    ""
    "Relevant processes:"
    Get-CimInstance Win32_Process |
        Where-Object { $_.Name -match "v2rayN|xray|sing-box|mihomo|tailscale|cloudflared" } |
        Select-Object ProcessId, ParentProcessId, Name, ExecutablePath,
            @{n="CommandLine";e={Protect-Text $_.CommandLine}}
    ""
    "Default routes:"
    Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
        Select-Object InterfaceAlias, NextHop, RouteMetric, State
}

Capture $systemFile "FIREWALL RULES RELEVANT TO FOCUS STUDIO" {
    Get-NetFirewallRule -ErrorAction SilentlyContinue |
        Where-Object {
            $_.DisplayName -match "cloudflared|Focus Studio|Python|v2ray|Tailscale"
        } |
        Select-Object DisplayName, Enabled, Direction, Action, Profile |
        Format-Table -AutoSize
}

Capture $systemFile "WINDOWS DEFENDER AND SECURITY SUMMARY" {
    $status = Get-MpComputerStatus -ErrorAction SilentlyContinue
    if ($status) {
        $status | Select-Object AntivirusEnabled, RealTimeProtectionEnabled,
            BehaviorMonitorEnabled, IoavProtectionEnabled, NISEnabled,
            QuickScanAge, FullScanAge | Format-List
    } else {
        "Microsoft Defender status was unavailable."
    }
    ""
    "Relevant Defender exclusions (paths only):"
    $preference = Get-MpPreference -ErrorAction SilentlyContinue
    if ($preference) {
        @($preference.ExclusionPath) |
            Where-Object { $_ -match "cloudflared|mojjss|focus|python" }
    }
}

Capture $systemFile "SCHEDULED TASKS AND STARTUP SERVICES" {
    Get-ScheduledTask -ErrorAction SilentlyContinue |
        Where-Object {
            $_.TaskName -match "Mojjss|Focus|cloudflared|Tailscale|v2ray"
        } |
        Select-Object TaskName, TaskPath, State | Format-Table -AutoSize
    ""
    Get-CimInstance Win32_Service -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "cloudflared|Tailscale|v2ray" -or $_.DisplayName -match "cloudflared|Tailscale|v2ray" } |
        Select-Object Name, DisplayName, State, StartMode, StartName,
            @{n="PathName";e={Protect-Text $_.PathName}} | Format-List
}

Capture $projectFile "PROJECT FILES AND VERSION" {
    "Project root: $ProjectRoot"
    Get-ChildItem -LiteralPath $ProjectRoot -Force |
        Select-Object Name, Length, LastWriteTime, Attributes
    ""
    $versionFile = Join-Path $ProjectRoot "desktop_app\version.py"
    if (Test-Path -LiteralPath $versionFile) {
        "version.py:"
        Get-Content -LiteralPath $versionFile
    }
    ""
    "Private files present (names only; contents are not copied):"
    @(
        "desktop_app\config.json",
        "desktop_app\focus_history.db",
        "desktop_app\schedule.csv",
        "desktop_app\timer_state.json",
        "desktop_app\data"
    ) | ForEach-Object {
        $candidate = Join-Path $ProjectRoot $_
        [pscustomobject]@{
            RelativePath = $_
            Exists = Test-Path -LiteralPath $candidate
        }
    }
}

Capture $projectFile "SAFE CONFIGURATION SUMMARY" {
    if (-not $config) {
        "config.json is missing or invalid."
    } else {
        [pscustomobject]@{
            timezone = $config.timezone
            appearance = $config.appearance
            monitor_enabled = [bool]$config.monitor_enabled
            monitor_port = $monitorPort
            cloud_dashboard_enabled = [bool]$config.cloud_dashboard_enabled
            cloud_dashboard_url = $dashboardUrl
            cloud_two_way_sync_enabled = [bool]$config.cloud_two_way_sync_enabled
            remote_camera_enabled = [bool]$config.remote_camera_enabled
            camera_port = $cameraPort
            camera_url = $cameraUrl
            camera_allowed_origin = $config.tailscale_camera_allowed_origin
            camera_require_identity = [bool]$config.tailscale_camera_require_identity
            camera_password_configured = [bool](
                $config.remote_camera_password_hash -and
                $config.remote_camera_password_salt
            )
            tunnel_monitor_enabled = [bool]$config.tunnel_monitor_enabled
            tunnel_check_seconds = $config.tunnel_check_seconds
            tunnel_notifications_enabled = [bool]$config.tunnel_notifications_enabled
        } | Format-List
        "Secret/key/token/hash/salt values were deliberately omitted."
    }
}

Capture $appFile "FOCUS STUDIO AND PYTHON PROCESSES" {
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -match "^pythonw?\.exe$|Focus" -or
            $_.CommandLine -match "mojjss_focus_studio|desktop_app\\app\.py"
        } |
        Select-Object ProcessId, ParentProcessId, Name, ExecutablePath,
            @{n="CommandLine";e={Protect-Text $_.CommandLine}},
            CreationDate |
        Format-List
}

Capture $appFile "LISTENING PORTS 8765 AND 8788" {
    foreach ($port in @($monitorPort, $cameraPort)) {
        "Port $port"
        $connections = Get-NetTCPConnection -LocalPort $port -State Listen `
            -ErrorAction SilentlyContinue
        if (-not $connections) {
            "No listening TCP socket."
        } else {
            foreach ($connection in $connections) {
                $process = Get-CimInstance Win32_Process `
                    -Filter "ProcessId=$($connection.OwningProcess)" `
                    -ErrorAction SilentlyContinue
                [pscustomobject]@{
                    LocalAddress = $connection.LocalAddress
                    LocalPort = $connection.LocalPort
                    PID = $connection.OwningProcess
                    Process = $process.Name
                    Executable = $process.ExecutablePath
                    CommandLine = Protect-Text $process.CommandLine
                } | Format-List
            }
        }
    }
}

Capture $appFile "DUPLICATE INSTANCES AND RUNTIME ARTIFACTS" {
    $focusProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match "^pythonw?\.exe$" -and
            $_.CommandLine -match "desktop_app[\\/]app\.py|mojjss_focus_studio"
        })
    "Focus Studio process count: $($focusProcesses.Count)"
    $focusProcesses | Select-Object ProcessId, ParentProcessId, CreationDate,
        ExecutablePath, @{n="CommandLine";e={Protect-Text $_.CommandLine}} | Format-List
    ""
    "Potential stale runtime files (names and ages only):"
    Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "desktop_app") -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "\.lock$|\.pid$|~$|\.tmp$" } |
        Select-Object Name, Length, LastWriteTime
}

$localCameraResult = Invoke-UrlCheck $localCameraUrl
$localMonitorResult = Invoke-UrlCheck $localMonitorUrl
Capture $appFile "LOCAL CAMERA HEALTH" { Format-UrlCheck $localCameraResult }
Capture $appFile "LOCAL MONITOR HEALTH" { Format-UrlCheck $localMonitorResult }

if ($localCameraResult.Status -eq 200) {
    Add-Summary "PASS" "Local camera origin returned HTTP 200."
} else {
    Add-Summary "FAIL" "Local camera origin did not return HTTP 200. HTTP=$($localCameraResult.Status)"
}
if ($localMonitorResult.Status -eq 200) {
    Add-Summary "PASS" "Local monitor returned HTTP 200."
} else {
    Add-Summary "WARNING" "Local monitor is unavailable or disabled. HTTP=$($localMonitorResult.Status)"
}

$cloudflaredPath = Get-CloudflaredPath
Capture $tunnelFile "CLOUDFLARED EXECUTABLE" {
    "Detected path: $cloudflaredPath"
    if ($cloudflaredPath) {
        & $cloudflaredPath --version
        Get-Item -LiteralPath $cloudflaredPath |
            Select-Object FullName, Length, LastWriteTime,
                @{n="SHA256";e={(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}}
    } else {
        "cloudflared.exe was not found."
    }
}

Capture $tunnelFile "CLOUDFLARED WINDOWS SERVICE" {
    $service = Get-CimInstance Win32_Service -Filter "Name='Cloudflared'" `
        -ErrorAction SilentlyContinue
    if (-not $service) {
        "Cloudflared service is missing."
    } else {
        $service |
            Select-Object Name, DisplayName, State, StartMode, ProcessId,
                @{n="PathName";e={Protect-Text $_.PathName}},
                StartName, ExitCode |
            Format-List
        ""
        & sc.exe qc Cloudflared
        ""
        & sc.exe qfailure Cloudflared
        ""
        Get-CimInstance Win32_Process |
            Where-Object { $_.Name -eq "cloudflared.exe" } |
            Select-Object ProcessId, ParentProcessId, ExecutablePath,
                @{n="CommandLine";e={Protect-Text $_.CommandLine}},
                CreationDate |
            Format-List
    }
}

Capture $tunnelFile "CLOUDFLARED LOCAL METRICS AND LISTENERS" {
    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq "cloudflared.exe" })
    foreach ($process in $processes) {
        "cloudflared PID $($process.ProcessId)"
        $listeners = @(Get-NetTCPConnection -OwningProcess $process.ProcessId -State Listen `
            -ErrorAction SilentlyContinue | Select-Object LocalAddress, LocalPort, State)
        $listeners | Format-Table -AutoSize
        foreach ($listener in $listeners) {
            if ($listener.LocalPort -ge 20241 -and $listener.LocalPort -le 20250) {
                $metricUrl = "http://127.0.0.1:$($listener.LocalPort)/metrics"
                "Metrics endpoint: $metricUrl"
                $metricResult = Invoke-UrlCheck $metricUrl -ConnectTimeout 3 -MaxTime 6
                "HTTP: $($metricResult.Status)"
                if ($metricResult.Status -eq 200) {
                    @($metricResult.Body -split "`n") |
                        Where-Object { $_ -match "cloudflared_tunnel_(ha_connections|request_errors|total_requests)|tunnel_ha_connections" } |
                        Select-Object -First 80
                }
            }
        }
    }
    if (-not $processes) { "No running cloudflared process." }
}

Capture $tunnelFile "CLOUDFLARED DNS AND PORT 7844" {
    Resolve-DnsName region1.v2.argotunnel.com -ErrorAction Continue
    Resolve-DnsName region2.v2.argotunnel.com -ErrorAction Continue
    Resolve-DnsName -Type SRV _v2-origintunneld._tcp.argotunnel.com `
        -ErrorAction Continue
    ""
    Test-NetConnection region1.v2.argotunnel.com -Port 7844 `
        -InformationLevel Detailed -WarningAction SilentlyContinue
    Test-NetConnection region2.v2.argotunnel.com -Port 7844 `
        -InformationLevel Detailed -WarningAction SilentlyContinue
}
Run-CloudflaredDiag -CloudflaredPath $cloudflaredPath -OutputPath $tunnelFile

$tcp7844Ok = $false
try {
    $tcp7844Ok = [bool](Test-NetConnection region1.v2.argotunnel.com -Port 7844 `
        -InformationLevel Quiet -WarningAction SilentlyContinue)
} catch {}
if ($tcp7844Ok) {
    Add-Summary "PASS" "Outbound TCP port 7844 to Cloudflare is reachable."
} else {
    Add-Summary "WARNING" "Outbound TCP port 7844 could not be confirmed."
}

if ($cloudflaredPath) {
    Add-Summary "PASS" "cloudflared.exe was found."
} else {
    Add-Summary "FAIL" "cloudflared.exe was not found."
}
$serviceState = Get-CimInstance Win32_Service -Filter "Name='Cloudflared'" `
    -ErrorAction SilentlyContinue
if ($serviceState -and $serviceState.State -eq "Running") {
    Add-Summary "PASS" "Cloudflared Windows service is running."
} elseif ($serviceState) {
    Add-Summary "FAIL" "Cloudflared service exists but is $($serviceState.State)."
} else {
    Add-Summary "FAIL" "Cloudflared Windows service is missing."
}

Capture $cloudFile "PUBLIC DNS" {
    Resolve-DnsName ([uri]$cameraUrl).Host -ErrorAction Continue
    Resolve-DnsName ([uri]$dashboardUrl).Host -ErrorAction Continue
}

$publicCameraDirect = Invoke-UrlCheck $publicCameraUrl
$publicCameraUserProxy = Invoke-UrlCheck $publicCameraUrl -UseUserProxy
$dashboardHealth = Invoke-UrlCheck $dashboardHealthUrl
$dashboardStatus = Invoke-UrlCheck $dashboardStatusUrl

Capture $cloudFile "PUBLIC CAMERA HEALTH - DIRECT WITHOUT USER PROXY" {
    Format-UrlCheck $publicCameraDirect
}
Capture $cloudFile "PUBLIC CAMERA HEALTH - USER NETWORK/PROXY" {
    Format-UrlCheck $publicCameraUserProxy
}
Capture $cloudFile "CLOUD DASHBOARD HEALTH" {
    Format-UrlCheck $dashboardHealth
}
Capture $cloudFile "CLOUD DASHBOARD STATUS WITHOUT KEY" {
    Format-UrlCheck $dashboardStatus
    "A 401 response can be expected when no dashboard key is supplied."
}
Capture $cloudFile "EXPECTED CLOUDFLARE ROUTE" {
    "Tunnel name: mojjss-focus-camera"
    "Public hostname: $cameraUrl"
    "Origin service: http://127.0.0.1:$cameraPort"
    "Healthy connector/replica count must be at least 1."
}

if ($publicCameraDirect.Status -eq 200) {
    Add-Summary "PASS" "Public camera health returned HTTP 200; tunnel and origin are online."
} elseif ($publicCameraDirect.Status -eq 403) {
    Add-Summary "WARNING" "Public camera hostname is reachable but Cloudflare Access returned 403."
} elseif ($publicCameraDirect.Status -eq 502) {
    Add-Summary "FAIL" "Tunnel likely connects, but Cloudflare cannot reach the local camera origin (HTTP 502)."
} elseif (
    $publicCameraDirect.Status -eq 530 -or
    $publicCameraDirect.Body -match "1033|Argo Tunnel"
) {
    Add-Summary "FAIL" "Cloudflare reports error 1033/no healthy tunnel connector."
} else {
    Add-Summary "FAIL" "Public camera health is unavailable. HTTP=$($publicCameraDirect.Status)"
}

if ($dashboardHealth.Status -eq 200) {
    Add-Summary "PASS" "Cloud dashboard health returned HTTP 200."
} else {
    Add-Summary "WARNING" "Cloud dashboard health returned HTTP=$($dashboardHealth.Status)"
}

$cloudflaredCrashEvents3d = @(Get-WinEvent -FilterHashtable @{
    LogName = "System"
    Id = 7031
    StartTime = (Get-Date).AddDays(-3)
} -ErrorAction SilentlyContinue | Where-Object { $_.Message -match "Cloudflared" })
$cloudflaredCrash1h = @($cloudflaredCrashEvents3d | Where-Object { $_.TimeCreated -ge (Get-Date).AddHours(-1) }).Count
$cloudflaredCrash24h = @($cloudflaredCrashEvents3d | Where-Object { $_.TimeCreated -ge (Get-Date).AddHours(-24) }).Count
$cloudflaredCrash3d = $cloudflaredCrashEvents3d.Count

Capture $eventsFile "CLOUDFLARED CRASH COUNTS BY RECENCY" {
    [pscustomobject]@{
        LastHour = $cloudflaredCrash1h
        Last24Hours = $cloudflaredCrash24h
        Last3Days = $cloudflaredCrash3d
    } | Format-List
    "Historical events are not treated as a current failure when the service and public route are healthy now."
}

if ($cloudflaredCrash1h -gt 0) {
    Add-Summary "FAIL" "Cloudflared terminated unexpectedly $cloudflaredCrash1h time(s) in the last hour."
} elseif ($cloudflaredCrash24h -gt 0) {
    Add-Summary "WARNING" "Cloudflared had $cloudflaredCrash24h unexpected termination(s) in the last 24 hours, but none in the last hour."
} elseif ($cloudflaredCrash3d -gt 0) {
    Add-Summary "INFO" "Cloudflared has historical crash events in the last 3 days, but none in the last 24 hours."
} else {
    Add-Summary "PASS" "No Cloudflared service crash events were found in the last 3 days."
}

Capture $eventsFile "RECENT SERVICE CONTROL MANAGER EVENTS" {
    Get-WinEvent -FilterHashtable @{
        LogName = "System"
        StartTime = (Get-Date).AddDays(-3)
    } -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ProviderName -eq "Service Control Manager" -and
            $_.Message -match "cloudflared|Python|Focus"
        } |
        Select-Object -First 100 TimeCreated, Id, LevelDisplayName,
            ProviderName, Message |
        Format-List
}

Capture $eventsFile "RECENT APPLICATION ERRORS" {
    Get-WinEvent -FilterHashtable @{
        LogName = "Application"
        StartTime = (Get-Date).AddDays(-3)
        Level = 2, 3
    } -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Message -match "cloudflared|python|mojjss|Focus Studio"
        } |
        Select-Object -First 100 TimeCreated, Id, LevelDisplayName,
            ProviderName, Message |
        Format-List
}

$pythonPath = Get-PythonPath $ProjectRoot
Capture $codeFile "PYTHON VERSION AND DEPENDENCIES" {
    "Detected Python launcher/executable: $pythonPath"
    if ($pythonPath) {
        if ((Split-Path -Leaf $pythonPath) -ieq "py.exe") {
            & $pythonPath -3.11 --version
            & $pythonPath -3.11 -c "import customtkinter, matplotlib, requests, PIL, cv2; print('core imports: OK')"
        } else {
            & $pythonPath --version
            & $pythonPath -c "import customtkinter, matplotlib, requests, PIL, cv2; print('core imports: OK')"
        }
    } else {
        "Python was not found."
    }
}

Capture $codeFile "PYTHON SOURCE COMPILE CHECK" {
    if ($pythonPath) {
        $desktopApp = Join-Path $ProjectRoot "desktop_app"
        if ((Split-Path -Leaf $pythonPath) -ieq "py.exe") {
            & $pythonPath -3.11 -m compileall -q $desktopApp
        } else {
            & $pythonPath -m compileall -q $desktopApp
        }
        "compileall exit code: $LASTEXITCODE"
    } else {
        "Skipped because Python was not found."
    }
}

Capture $codeFile "JAVASCRIPT SYNTAX CHECK" {
    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    if (-not $node) {
        "Node.js is not installed; JavaScript syntax check skipped."
    } else {
        $files = Get-ChildItem -LiteralPath (
            Join-Path $ProjectRoot "cloudflare_dashboard"
        ) -Recurse -File -Filter "*.js"
        foreach ($file in $files) {
            & $node.Source --check $file.FullName
            if ($LASTEXITCODE -ne 0) {
                "FAIL: $($file.FullName)"
            } else {
                "OK: $($file.FullName)"
            }
        }
    }
}

Capture $codeFile "SQLITE DATABASE INTEGRITY" {
    $database = Join-Path $ProjectRoot "desktop_app\focus_history.db"
    if (-not (Test-Path -LiteralPath $database)) {
        "focus_history.db does not exist yet."
    } elseif (-not $pythonPath) {
        "Skipped because Python was not found."
    } else {
        $code = @'
import sqlite3, sys
path = sys.argv[1]
con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
try:
    print("integrity_check:", con.execute("PRAGMA integrity_check").fetchone()[0])
    print("tables:", ", ".join(
        row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ))
finally:
    con.close()
'@
        if ((Split-Path -Leaf $pythonPath) -ieq "py.exe") {
            & $pythonPath -3.11 -c $code $database
        } else {
            & $pythonPath -c $code $database
        }
    }
}

Capture $codeFile "KEY FILE HASHES" {
    @(
        "desktop_app\app.py",
        "desktop_app\monitor_server.py",
        "desktop_app\tailscale_camera.py",
        "cloudflare_dashboard\functions\api\status.js",
        "cloudflare_dashboard\schema.sql"
    ) | ForEach-Object {
        $relativePath = $_
        $path = Join-Path $ProjectRoot $relativePath
        if (Test-Path -LiteralPath $path) {
            $hash = Get-FileHash -LiteralPath $path -Algorithm SHA256
            [pscustomobject]@{
                RelativePath = $relativePath
                SHA256 = $hash.Hash
            }
        }
    }
}

Capture $codeFile "GIT STATUS WHEN AVAILABLE" {
    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($git -and (Test-Path -LiteralPath (Join-Path $ProjectRoot ".git"))) {
        Push-Location $ProjectRoot
        try {
            & $git.Source status --short
            & $git.Source log --oneline -8
            & $git.Source remote -v
        } finally {
            Pop-Location
        }
    } else {
        "No .git directory or Git executable; skipped."
    }
}

$watchdogStatusPath = Join-Path $env:ProgramData "MojjssFocusStudio\tunnel_watchdog_status.json"
$watchdogStatePath = Join-Path $env:ProgramData "MojjssFocusStudio\tunnel_watchdog_state.json"
$watchdogLogPath = Join-Path $env:ProgramData "MojjssFocusStudio\tunnel_watchdog.log"
Capture $analysisFile "AUTOMATIC WATCHDOG" {
    $task = Get-ScheduledTask -TaskName "Mojjss Focus Studio Tunnel Watchdog" -ErrorAction SilentlyContinue
    if ($task) {
        $task | Select-Object TaskName, State, TaskPath | Format-List
        Get-ScheduledTaskInfo -TaskName $task.TaskName -ErrorAction SilentlyContinue |
            Select-Object LastRunTime, LastTaskResult, NextRunTime, NumberOfMissedRuns |
            Format-List
    } else {
        "The automatic SYSTEM watchdog is not installed."
    }
    ""
    if (Test-Path -LiteralPath $watchdogStatusPath) {
        "Watchdog status (secret-free):"
        Get-Content -LiteralPath $watchdogStatusPath -Raw -Encoding UTF8
    } else {
        "No watchdog status file."
    }
    ""
    if (Test-Path -LiteralPath $watchdogLogPath) {
        "Last 60 watchdog log lines:"
        Get-Content -LiteralPath $watchdogLogPath -Tail 60 -Encoding UTF8
    }
}

$pythonCompileOk = $false
if ($pythonPath -and (Test-Path -LiteralPath (Join-Path $ProjectRoot "desktop_app"))) {
    try {
        if ((Split-Path -Leaf $pythonPath) -ieq "py.exe") {
            & $pythonPath -3.11 -m compileall -q (Join-Path $ProjectRoot "desktop_app") | Out-Null
        } else {
            & $pythonPath -m compileall -q (Join-Path $ProjectRoot "desktop_app") | Out-Null
        }
        $pythonCompileOk = $LASTEXITCODE -eq 0
    } catch {}
}
if ($pythonCompileOk) {
    Add-Summary "PASS" "All desktop Python source files compiled successfully."
} else {
    Add-Summary "WARNING" "Python source compilation could not be confirmed; see 07_CODE_DATABASE_CHECKS.txt."
}

if ($serviceState -and $serviceState.PathName -match '(?i)--token-file') {
    Add-Summary "PASS" "Cloudflared uses a protected token file instead of exposing the token in the service command line."
} elseif ($serviceState -and $serviceState.PathName -match '(?i)--token(?:=|\s+)') {
    Add-Summary "INFO" "Cloudflared uses the official --token service form. The repair tool can migrate it to --token-file for local hardening."
}

$likelyCause = "No active failure was detected."
$confidence = 95
$suggestedFix = "No repair is required now. Keep the watchdog installed if you want automatic recovery."
$currentState = "Healthy"

if (-not $serviceState) {
    $currentState = "Failed"
    $likelyCause = "The Cloudflared Windows service is not installed."
    $confidence = 99
    $suggestedFix = "Run REPAIR_CLOUDFLARE_TUNNEL.bat and enter a current tunnel token privately."
} elseif ($serviceState.State -ne "Running") {
    $currentState = "Failed"
    $likelyCause = "The Cloudflared service exists but is not running."
    $confidence = 99
    $suggestedFix = "Run REPAIR_CLOUDFLARE_TUNNEL.bat or start the service, then verify the public camera health endpoint."
} elseif ($publicCameraDirect.Status -eq 502) {
    $currentState = "Failed"
    if ($localCameraResult.Status -eq 200) {
        $likelyCause = "The tunnel connector is online, but its published hostname likely points to the wrong origin protocol or port."
        $confidence = 92
        $suggestedFix = "In Cloudflare, set the camera hostname service to http://127.0.0.1:$cameraPort."
    } else {
        $likelyCause = "The tunnel is connected, but the local camera origin is not running or not listening on the configured port."
        $confidence = 98
        $suggestedFix = "Open Focus Studio, enable the private camera, and verify $localCameraUrl before changing the tunnel."
    }
} elseif ($publicCameraDirect.Status -eq 530 -or $publicCameraDirect.Body -match '1033|Argo Tunnel') {
    $currentState = "Failed"
    if (-not $tcp7844Ok) {
        $likelyCause = "No healthy connector is visible and outbound TCP port 7844 could not be confirmed."
        $confidence = 91
        $suggestedFix = "Allow outbound TCP/UDP 7844 and rerun REPAIR_CLOUDFLARE_TUNNEL.bat."
    } else {
        $likelyCause = "The service is running, but the connector is not authenticated or registered with the expected remotely-managed tunnel."
        $confidence = 94
        $suggestedFix = "Run REPAIR_CLOUDFLARE_TUNNEL.bat with the current eyJ... token from Add a replica. Never share that token."
    }
} elseif ($publicCameraDirect.Status -eq 0) {
    $currentState = "Degraded"
    $likelyCause = "The direct public-route request failed before receiving an HTTP response."
    $confidence = 78
    $suggestedFix = "Check DNS, outbound 7844 connectivity, v2rayN/TUN routing, and the Cloudflared service logs in this report."
} elseif ($publicCameraDirect.Status -in @(200, 401, 403) -and $serviceState.State -eq "Running") {
    if ($cloudflaredCrash1h -gt 0) {
        $currentState = "Degraded"
        $likelyCause = "The tunnel works now, but Cloudflared has restarted unexpectedly within the last hour."
        $confidence = 88
        $suggestedFix = "Install the SYSTEM watchdog and inspect the newest service/application events for the immediate crash reason."
    } elseif ($cloudflaredCrash24h -gt 0) {
        $currentState = "Healthy now"
        $likelyCause = "The current tunnel is healthy. Earlier crash events are historical and do not prove an active failure."
        $confidence = 90
        $suggestedFix = "No immediate repair is needed. Install the watchdog to recover automatically if the problem returns."
    } elseif ($cloudflaredCrash3d -gt 0) {
        $currentState = "Healthy now"
        $likelyCause = "The current tunnel is healthy; only older service crash events were found."
        $confidence = 95
        $suggestedFix = "No immediate repair is needed. Keep the diagnosis ZIP as a baseline."
    }
}

$analysisText = @"
CURRENT STATE
$currentState

LIKELY CAUSE
$likelyCause

ESTIMATED CONFIDENCE
$confidence%
This is a rule-based diagnostic confidence estimate, not a statistical probability.

SUGGESTED FIX
$suggestedFix

IMPORTANT DISTINCTION
Cloudflare tunnel status measures the connector-to-Cloudflare connection. A 502 means the connector is usually online but cannot reach the local origin; error 1033 means Cloudflare cannot find a healthy connector.
"@
Add-Section $analysisFile "RULE-BASED ANALYSIS"
Add-Line $analysisFile $analysisText
Add-Section $script:SummaryFile "AUTOMATIC ANALYSIS"
Add-Line $script:SummaryFile "Current state: $currentState"
Add-Line $script:SummaryFile "Likely cause: $likelyCause"
Add-Line $script:SummaryFile "Estimated confidence: $confidence% (rule-based)"
Add-Line $script:SummaryFile "Suggested fix: $suggestedFix"

@"
PRIVACY AND SHARING

This report intentionally does not copy:
- desktop_app\config.json
- SQLite database contents
- schedules or timer-state contents
- camera images
- dashboard owner/viewer/write keys
- Pixela tokens
- camera password hashes/salts
- Cloudflare tunnel tokens or token-file contents

Automatic redaction was applied to every text file. Review the ZIP before
sharing it publicly. Upload the ZIP to ChatGPT for diagnosis.
"@ | Set-Content -LiteralPath $privacyFile -Encoding UTF8

Add-Section $script:SummaryFile "NEXT ACTION"
Add-Line $script:SummaryFile "Upload the generated ZIP to ChatGPT. Do not separately upload config.json or any eyJ... tunnel token."

# Final redaction pass.
Get-ChildItem -LiteralPath $script:ReportDir -File |
    Where-Object { $_.Extension -in @(".txt", ".log", ".json") } |
    ForEach-Object {
        try {
            $content = Get-Content -LiteralPath $_.FullName -Raw -ErrorAction Stop
            $safe = Protect-Text $content
            Set-Content -LiteralPath $_.FullName -Value $safe -Encoding UTF8
        } catch {}
    }

$zipPath = "$script:ReportDir.zip"
Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $script:ReportDir "*") `
    -DestinationPath $zipPath -CompressionLevel Optimal -Force

Write-Host ""
Write-Host "DONE" -ForegroundColor Green
Write-Host "Diagnosis ZIP:" -ForegroundColor Cyan
Write-Host $zipPath -ForegroundColor White
try {
    Start-Process explorer.exe -ArgumentList "/select,`"$zipPath`"" | Out-Null
} catch {}
exit 0

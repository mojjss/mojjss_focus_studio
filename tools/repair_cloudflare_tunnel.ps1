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

function Resolve-ProjectRootSafe {
    param([AllowNull()][string]$Candidate)
    $candidates = @(
        $Candidate,
        (Split-Path -Parent $PSScriptRoot),
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
    return $value
}

$desktop = [Environment]::GetFolderPath("Desktop")
if (-not $desktop) { $desktop = $env:USERPROFILE }
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$script:LogPath = Join-Path $desktop "FOCUS_STUDIO_TUNNEL_REPAIR_$stamp.txt"
"Focus Studio Cloudflare Tunnel repair`r`nCreated: $(Get-Date -Format o)`r`n" |
    Set-Content -LiteralPath $script:LogPath -Encoding UTF8

function Write-Log {
    param([string]$Message, [ConsoleColor]$Color = [ConsoleColor]::Gray)
    $safe = Protect-Text $Message
    Write-Host $safe -ForegroundColor $Color
    Add-Content -LiteralPath $script:LogPath -Value $safe -Encoding UTF8
}

function Write-Step {
    param([string]$Title)
    Write-Host ""
    Write-Host ("=" * 74) -ForegroundColor DarkCyan
    Write-Log $Title Cyan
    Write-Host ("=" * 74) -ForegroundColor DarkCyan
}

function Get-Config {
    param([string]$Root)
    $path = Join-Path $Root "desktop_app\config.json"
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        return Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        Write-Log "Could not read config.json: $($_.Exception.Message)" Yellow
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

function Invoke-UrlCheck {
    param(
        [string]$Url,
        [int]$ConnectTimeout = 8,
        [int]$MaxTime = 18
    )
    $bodyFile = Join-Path $env:TEMP ("focus_url_" + [guid]::NewGuid().ToString("N") + ".txt")
    try {
        $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
        if ($curl) {
            $statusText = & $curl.Source `
                --silent --show-error --location `
                --connect-timeout $ConnectTimeout --max-time $MaxTime `
                --noproxy "*" `
                --output $bodyFile --write-out "%{http_code}" `
                $Url 2>&1
            $exitCode = $LASTEXITCODE
            $status = 0
            [void][int]::TryParse((($statusText | Select-Object -Last 1) -as [string]), [ref]$status)
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
    if ($body.Length -gt 700) { $body = $body.Substring(0, 700) + "..." }
    return "URL=$($Result.Url)`r`nHTTP=$($Result.Status)`r`nExitCode=$($Result.ExitCode)`r`nBody=$body"
}

function Get-VersionInfo {
    param([string]$CloudflaredPath)
    $raw = (& $CloudflaredPath --version 2>&1 | Out-String).Trim()
    $version = [version]"0.0.0"
    if ($raw -match '(\d{4}\.\d+\.\d+)') {
        try { $version = [version]$matches[1] } catch {}
    }
    return [pscustomobject]@{ Raw = $raw; Version = $version }
}

function Stop-DeleteCloudflaredService {
    Write-Log "Stopping and removing the old Cloudflared service..."
    Stop-Service -Name Cloudflared -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2

    try {
        $output = & $script:CloudflaredPath service uninstall 2>&1 | Out-String
        if ($output.Trim()) { Write-Log (Protect-Text $output.Trim()) DarkGray }
    } catch {
        Write-Log "Official service uninstall returned: $($_.Exception.Message)" Yellow
    }

    if (Get-Service -Name Cloudflared -ErrorAction SilentlyContinue) {
        $output = & sc.exe delete Cloudflared 2>&1 | Out-String
        if ($output.Trim()) { Write-Log (Protect-Text $output.Trim()) DarkGray }
        Start-Sleep -Seconds 4
    }
}

function Add-ConnectionFlags {
    param([string]$ImagePath)
    $updated = [string]$ImagePath
    $updated = [regex]::Replace(
        $updated,
        '(?i)\s+--protocol(?:=|\s+)\S+',
        ''
    )
    $updated = [regex]::Replace(
        $updated,
        '(?i)\s+--edge-ip-version(?:=|\s+)\S+',
        ''
    )
    if ($updated -notmatch '(?i)\btunnel\b') {
        throw "Cloudflared service ImagePath does not contain the tunnel command."
    }
    $tunnelRegex = [regex]'(?i)\btunnel\b'
    return $tunnelRegex.Replace(
        $updated,
        'tunnel --protocol http2 --edge-ip-version 4',
        1
    )
}

function Set-SecureTokenFile {
    param(
        [string]$ImagePath,
        [string]$Token,
        [version]$CloudflaredVersion
    )
    if ($CloudflaredVersion -lt [version]"2025.4.0") {
        Write-Log (
            "cloudflared $CloudflaredVersion is older than 2025.4.0, so " +
            "--token-file is unavailable. The official service installation will be kept."
        ) Yellow
        return $ImagePath
    }

    $tokenDir = Join-Path $env:ProgramData "MojjssFocusStudio\cloudflared"
    $tokenFile = Join-Path $tokenDir "tunnel.token"
    New-Item -ItemType Directory -Path $tokenDir -Force | Out-Null
    [IO.File]::WriteAllText(
        $tokenFile,
        $Token,
        (New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false)
    )

    & icacls.exe $tokenDir /inheritance:r `
        /grant:r "*S-1-5-18:(OI)(CI)F" `
        /grant:r "*S-1-5-32-544:(OI)(CI)F" | Out-Null
    & icacls.exe $tokenFile /inheritance:r `
        /grant:r "*S-1-5-18:F" `
        /grant:r "*S-1-5-32-544:F" | Out-Null

    $tokenPattern = '(?i)--token(?:=|\s+)(?:"[^"]+"|\S+)'
    if ($ImagePath -notmatch $tokenPattern) {
        Write-Log (
            "The official service path did not expose a --token argument. " +
            "It was left unchanged."
        ) Yellow
        return $ImagePath
    }

    $replacement = '--token-file "' + $tokenFile + '"'
    $tokenRegex = [regex]$tokenPattern
    $updated = $tokenRegex.Replace($ImagePath, $replacement, 1)
    Write-Log (
        "Moved the tunnel token into a restricted token file. " +
        "The token is not stored directly in the service command line."
    ) Green
    return $updated
}

if (-not (Test-IsAdministrator)) {
    Write-Host "This repair must run as Administrator." -ForegroundColor Red
    exit 5
}

Clear-Host
Write-Host "Mojjss Focus Studio - Cloudflare Tunnel Repair" -ForegroundColor Cyan
Write-Host "This repairs only the cloudflared connector. It does not reset v2rayN, Tailscale, Winsock, adapters, or Windows proxy." -ForegroundColor Gray
Write-Host ""

$ProjectRoot = Resolve-ProjectRootSafe $ProjectRoot
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    Write-Log "Could not locate the Focus Studio project root. Nothing was changed." Red
    Write-Log "Place this tool in the project root and run it again." Yellow
    exit 6
}
Write-Log "Project root: $ProjectRoot" DarkGray
$config = Get-Config $ProjectRoot
$cameraPort = 8788
$cameraUrl = "https://camera.mojjss.ir"
if ($config) {
    if ($config.tailscale_camera_port) {
        $cameraPort = [int]$config.tailscale_camera_port
    }
    if ($config.tailscale_camera_url) {
        $cameraUrl = ([string]$config.tailscale_camera_url).Trim().TrimEnd("/")
    }
}
$localHealthUrl = "http://127.0.0.1:$cameraPort/api/health"
$publicHealthUrl = $cameraUrl.TrimEnd("/") + "/api/health"

Write-Step "1. Check the local camera origin"
$localResult = Invoke-UrlCheck $localHealthUrl
Write-Log (Format-UrlCheck $localResult)
if ($localResult.Status -eq 200) {
    Write-Log "Local camera server is healthy." Green
} else {
    Write-Log (
        "The local camera origin is not healthy. The tunnel can still connect, " +
        "but the public camera may return 502 until Focus Studio is open and the camera is enabled."
    ) Yellow
}

Write-Step "2. Locate cloudflared and test Cloudflare connectivity"
$script:CloudflaredPath = Get-CloudflaredPath
if (-not $script:CloudflaredPath) {
    Write-Log "cloudflared.exe was not found." Red
    Write-Log "Install it from Cloudflare, then rerun this file." Yellow
    Write-Log "Report: $script:LogPath" Cyan
    exit 10
}
Write-Log "cloudflared: $script:CloudflaredPath"
$versionInfo = Get-VersionInfo $script:CloudflaredPath
Write-Log $versionInfo.Raw
try {
    $tcp = Test-NetConnection region1.v2.argotunnel.com -Port 7844 -WarningAction SilentlyContinue
    Write-Log "TCP 7844 to region1.v2.argotunnel.com: $($tcp.TcpTestSucceeded)"
    if (-not $tcp.TcpTestSucceeded) {
        Write-Log "TCP 7844 is blocked. The forced HTTP/2 connector cannot work until this is allowed outbound." Red
    }
} catch {
    Write-Log "Could not test TCP 7844: $($_.Exception.Message)" Yellow
}

Write-Step "3. Enter the current tunnel token privately"
Write-Host "Cloudflare path:" -ForegroundColor Gray
Write-Host "Networking -> Tunnels -> mojjss-focus-camera -> Add a replica -> Windows" -ForegroundColor White
Write-Host "Copy only the long value beginning with eyJ..." -ForegroundColor White
Write-Host "SECRET: never send this token here, put it in GitHub, or include it in a screenshot." -ForegroundColor Yellow
$secureToken = Read-Host "Paste the private eyJ... tunnel token" -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
$token = ""
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
}
if (-not $token -or $token -notmatch '^eyJ[A-Za-z0-9._~-]{20,}$') {
    Write-Log "The entered value does not look like a Cloudflare tunnel token. Nothing was changed." Red
    exit 11
}

Write-Step "4. Test the token with a temporary visible connector"
Stop-Service -Name Cloudflared -Force -ErrorAction SilentlyContinue
$testDir = Join-Path $env:TEMP ("FocusTunnelTest_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $testDir -Force | Out-Null
$stdout = Join-Path $testDir "stdout.log"
$stderr = Join-Path $testDir "stderr.log"
$oldTokenEnvironment = $env:TUNNEL_TOKEN
$env:TUNNEL_TOKEN = $token
$testProcess = $null
$registered = $false
try {
    $arguments = @(
        "tunnel",
        "--no-autoupdate",
        "--protocol", "http2",
        "--edge-ip-version", "4",
        "--loglevel", "info",
        "run"
    )
    $testProcess = Start-Process `
        -FilePath $script:CloudflaredPath `
        -ArgumentList $arguments `
        -PassThru `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr

    for ($index = 0; $index -lt 30; $index++) {
        Start-Sleep -Seconds 1
        if ($testProcess.HasExited) { break }
        $combined = ""
        if (Test-Path -LiteralPath $stdout) {
            $combined += Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $stderr) {
            $combined += "`n" + (Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue)
        }
        if ($combined -match '(?i)registered tunnel connection|connection .* registered|registered connection') {
            $registered = $true
            break
        }
    }

    $combinedLog = ""
    if (Test-Path -LiteralPath $stdout) {
        $combinedLog += Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $stderr) {
        $combinedLog += "`n" + (Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue)
    }
    if ($combinedLog.Trim()) {
        Write-Log "Temporary connector log:"
        Write-Log (Protect-Text $combinedLog.Trim()) DarkGray
    }

    if ($registered) {
        Write-Log "The token authenticated and cloudflared registered a tunnel connection." Green
    } elseif ($testProcess.HasExited) {
        Write-Log "The temporary connector exited before registering. Review the log above." Red
    } else {
        Write-Log "No registration message was detected within 30 seconds." Yellow
    }
} finally {
    if ($testProcess -and -not $testProcess.HasExited) {
        Stop-Process -Id $testProcess.Id -Force -ErrorAction SilentlyContinue
        try { $testProcess.WaitForExit(5000) } catch {}
    }
    $env:TUNNEL_TOKEN = $oldTokenEnvironment
    Remove-Item -LiteralPath $testDir -Recurse -Force -ErrorAction SilentlyContinue
}

if (-not $registered) {
    $answer = Read-Host "The token test was not confirmed. Type CONTINUE to reinstall the service anyway"
    if ($answer -ne "CONTINUE") {
        $token = $null
        Write-Log "Repair stopped before changing the service." Yellow
        Write-Log "Report: $script:LogPath" Cyan
        exit 12
    }
}

Write-Step "5. Reinstall the Windows service with the current token"
Stop-DeleteCloudflaredService
$installOutput = & $script:CloudflaredPath service install $token 2>&1 | Out-String
$installExitCode = $LASTEXITCODE
if ($installOutput.Trim()) {
    Write-Log (Protect-Text $installOutput.Trim()) DarkGray
}
if ($installExitCode -ne 0 -or -not (Get-Service -Name Cloudflared -ErrorAction SilentlyContinue)) {
    $token = $null
    Write-Log "Official cloudflared service installation failed with code $installExitCode." Red
    Write-Log "Report: $script:LogPath" Cyan
    exit 20
}

Stop-Service -Name Cloudflared -Force -ErrorAction SilentlyContinue
$registryPath = "HKLM:\SYSTEM\CurrentControlSet\Services\Cloudflared"
$imagePath = (Get-ItemProperty -LiteralPath $registryPath -Name ImagePath).ImagePath
$imagePath = Add-ConnectionFlags $imagePath
$imagePath = Set-SecureTokenFile `
    -ImagePath $imagePath `
    -Token $token `
    -CloudflaredVersion $versionInfo.Version
Set-ItemProperty -LiteralPath $registryPath -Name ImagePath -Value $imagePath

& sc.exe config Cloudflared start= delayed-auto | Out-Null
& sc.exe failure Cloudflared `
    reset= 86400 `
    actions= restart/5000/restart/15000/restart/60000 | Out-Null
& sc.exe failureflag Cloudflared 1 | Out-Null

Start-Service -Name Cloudflared
$token = $null
Start-Sleep -Seconds 3

Write-Step "6. Verify the repaired service and public route"
$service = Get-CimInstance Win32_Service -Filter "Name='Cloudflared'"
Write-Log "Service state: $($service.State)"
Write-Log "Start mode: $($service.StartMode)"
Write-Log "Process ID: $($service.ProcessId)"
Write-Log ("Service command: " + (Protect-Text $service.PathName))

$publicResult = $null
for ($index = 0; $index -lt 12; $index++) {
    Start-Sleep -Seconds 5
    $publicResult = Invoke-UrlCheck $publicHealthUrl
    Write-Host "." -NoNewline
    if ($publicResult.Status -eq 200) { break }
    if ($publicResult.Status -eq 403) { break }
    if ($publicResult.Status -eq 502) { break }
}
Write-Host ""
Write-Log (Format-UrlCheck $publicResult)

if ($publicResult.Status -eq 200) {
    Write-Log "SUCCESS: the Cloudflare Tunnel and camera health endpoint are online." Green
    Write-Log "Cloudflare should show at least one healthy connector/replica shortly." Green
    $exitCode = 0
} elseif ($publicResult.Status -eq 403) {
    Write-Log "The hostname is reachable but Cloudflare Access denied this health request. The tunnel appears connected." Yellow
    $exitCode = 0
} elseif ($publicResult.Status -eq 502) {
    Write-Log "The tunnel appears connected, but Cloudflare cannot reach the local camera origin." Yellow
    Write-Log "Open Focus Studio, enable the private camera, and verify $localHealthUrl." Yellow
    $exitCode = 2
} elseif ($publicResult.Status -eq 530 -or $publicResult.Body -match '1033|Argo Tunnel') {
    Write-Log "The public route still reports error 1033: no healthy connector is visible." Red
    Write-Log "Run FOCUS_STUDIO_DIAGNOSIS.bat and upload its ZIP." Yellow
    $exitCode = 3
} else {
    Write-Log "The service is installed, but the public result is not yet conclusive." Yellow
    Write-Log "Run FOCUS_STUDIO_DIAGNOSIS.bat and upload its ZIP." Yellow
    $exitCode = 4
}

Write-Log "Repair log: $script:LogPath" Cyan
try {
    Start-Process explorer.exe -ArgumentList "/select,`"$script:LogPath`"" | Out-Null
} catch {}
exit $exitCode

$ErrorActionPreference = 'Stop'
$port = 8788
$connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if (-not $connections) {
    Write-Host "Port $port is free."
    exit 0
}

foreach ($connection in $connections) {
    $pidValue = $connection.OwningProcess
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction SilentlyContinue
    $commandLine = [string]$process.CommandLine
    $executable = [string]$process.ExecutablePath
    Write-Host "Port $port is owned by PID $pidValue"
    Write-Host "Executable: $executable"
    Write-Host "Command: $commandLine"

    $looksLikePythonTimer = ($executable -match 'python') -or ($commandLine -match 'app\.py|tailscale_camera')
    if (-not $looksLikePythonTimer) {
        Write-Error "Refusing to stop PID $pidValue because it does not look like the timer's Python process."
        exit 1
    }

    Stop-Process -Id $pidValue -Force
    Write-Host "Stopped old camera listener PID $pidValue."
}
Start-Sleep -Seconds 1
exit 0

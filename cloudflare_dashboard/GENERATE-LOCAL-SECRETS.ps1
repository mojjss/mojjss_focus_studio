$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function New-RandomHexKey {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

$viewer = New-RandomHexKey
$owner  = New-RandomHexKey
$write  = New-RandomHexKey
$file = Join-Path $PSScriptRoot "PRIVATE-CLOUDFLARE-KEYS.txt"

@"
Generated: $(Get-Date -Format o)

DASHBOARD_VIEWER_KEY=$viewer
DASHBOARD_OWNER_KEY=$owner
DESKTOP_WRITE_KEY=$write

Add these three values manually in Cloudflare Pages:
Settings -> Variables and Secrets -> Add -> Encrypt -> Save

Keep this file private. It is excluded by .gitignore.
"@ | Set-Content -Path $file -Encoding UTF8

Write-Host "Created three unrelated 256-bit keys." -ForegroundColor Green
Write-Host "Saved to: $file"
Write-Host "No npm or Wrangler was used."
Start-Process notepad.exe $file

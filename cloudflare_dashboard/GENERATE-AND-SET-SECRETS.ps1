param(
    [switch]$GenerateOnly
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$ProjectName = "focus-studio-dashboard"

function New-RandomHexKey {
    $bytes = [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
    return [Convert]::ToHexString($bytes).ToLowerInvariant()
}

$ViewerKey = New-RandomHexKey
$OwnerKey = New-RandomHexKey
$WriteKey = New-RandomHexKey

$privateFile = Join-Path $PSScriptRoot "PRIVATE-CLOUDFLARE-KEYS.txt"
@"
Generated: $(Get-Date -Format o)

DASHBOARD_VIEWER_KEY=$ViewerKey
DASHBOARD_OWNER_KEY=$OwnerKey
DESKTOP_WRITE_KEY=$WriteKey

Keep this file private. It is excluded by .gitignore.
"@ | Set-Content -Path $privateFile -Encoding UTF8

Write-Host "Generated three unrelated 256-bit keys." -ForegroundColor Green
Write-Host "Private copy: $privateFile"

if ($GenerateOnly) {
    Write-Host "GenerateOnly was selected; Cloudflare was not changed."
    exit 0
}

if (-not (Test-Path ".\node_modules")) {
    npm install
    if ($LASTEXITCODE -ne 0) { throw "npm install failed." }
}

Write-Host "Setting Pages secrets for $ProjectName..." -ForegroundColor Cyan
$ViewerKey | npx wrangler pages secret put DASHBOARD_VIEWER_KEY --project-name $ProjectName
if ($LASTEXITCODE -ne 0) { throw "Could not set DASHBOARD_VIEWER_KEY." }
$OwnerKey | npx wrangler pages secret put DASHBOARD_OWNER_KEY --project-name $ProjectName
if ($LASTEXITCODE -ne 0) { throw "Could not set DASHBOARD_OWNER_KEY." }
$WriteKey | npx wrangler pages secret put DESKTOP_WRITE_KEY --project-name $ProjectName
if ($LASTEXITCODE -ne 0) { throw "Could not set DESKTOP_WRITE_KEY." }

Write-Host "Secrets were set. Redeploy the Pages project now." -ForegroundColor Green
Write-Host "Use the viewer key for the normal user and the owner key for yourself."
Write-Host "Paste only DESKTOP_WRITE_KEY into the desktop app's Cloud dashboard settings."

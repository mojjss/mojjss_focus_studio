$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$ProjectName = "focus-studio-dashboard"
$BaseUrl = "https://timer.mojjss.ir"

Write-Host ""
Write-Host "mojjss Focus Studio v5.0 deployment" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path ".\wrangler.toml")) {
    Write-Host "wrangler.toml is missing." -ForegroundColor Yellow
    Write-Host "Copy wrangler.toml.example to wrangler.toml and replace the D1 database ID."
    exit 1
}

if (-not (Test-Path ".\node_modules")) {
    Write-Host "[1/4] Installing Wrangler..."
    npm install
}

Write-Host "[2/4] Checking Pages Functions..."
if (-not (Test-Path ".\.wrangler")) {
    New-Item -ItemType Directory -Path ".\.wrangler" | Out-Null
}
npx wrangler pages functions build ".\functions" `
    --outfile ".\.wrangler\functions.js"
if ($LASTEXITCODE -ne 0) { throw "Pages Functions compilation failed." }

Write-Host "[3/4] Deploying..."
npx wrangler pages deploy ".\public" --project-name $ProjectName --branch main
if ($LASTEXITCODE -ne 0) { throw "Pages deployment failed." }

Write-Host "[4/4] Opening timer.mojjss.ir..."
Start-Process "$BaseUrl/?v=5.0&cb=$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())"

Write-Host ""
Write-Host "Deployment finished." -ForegroundColor Green
Write-Host "The custom domain must be attached in Cloudflare Pages > Custom domains."

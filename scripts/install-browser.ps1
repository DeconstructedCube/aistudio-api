<#
.SYNOPSIS
    Downloads and stages CloakBrowser for Windows x64.
.DESCRIPTION
    Installs CloakBrowser into .cloakbrowser/chromium-<version>/ for AI Studio API.
    Enforces CloakBrowser exclusively; bypasses Microsoft Edge and other generic browsers.
#>

[CmdletBinding()]
param(
    [string]$ChromiumVersion = "146.0.7680.177.4",
    [string]$ProjectRoot = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
}

$CloakRoot = Join-Path $ProjectRoot ".cloakbrowser"
$TargetDir = Join-Path $CloakRoot "chromium-$ChromiumVersion"
$ChromeExe = Join-Path $TargetDir "chrome.exe"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  CloakBrowser Setup for Windows x64 (AI Studio API)       " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "[install-browser] Project Root : $ProjectRoot"
Write-Host "[install-browser] Target Path  : $TargetDir"
Write-Host "[install-browser] Version      : $ChromiumVersion"

if ((Test-Path $ChromeExe) -and (-not $Force)) {
    Write-Host "[install-browser] CloakBrowser already installed at: $ChromeExe" -ForegroundColor Green
    try {
        $ver = & $ChromeExe --version 2>$null
        Write-Host "[install-browser] Executable verification: $ver" -ForegroundColor Green
    } catch {
        Write-Host "[install-browser] Ready for service." -ForegroundColor Green
    }
    exit 0
}

if (-not (Test-Path $CloakRoot)) {
    New-Item -ItemType Directory -Path $CloakRoot -Force | Out-Null
}

$ArchiveName = "cloakbrowser-windows-x64.zip"
$DownloadUrl = "https://github.com/CloakHQ/cloakbrowser/releases/download/chromium-v$ChromiumVersion/$ArchiveName"
$TempZip = Join-Path ([System.IO.Path]::GetTempPath()) "cloakbrowser-windows-x64-$ChromiumVersion.zip"

Write-Host "[install-browser] Downloading CloakBrowser Windows x64 package..." -ForegroundColor Yellow
Write-Host "[install-browser] Source URL: $DownloadUrl"

# Download with curl.exe (with retry & progress) or fallback to WebClient
$Downloaded = $false
if (Get-Command "curl.exe" -ErrorAction SilentlyContinue) {
    try {
        & curl.exe -fSL --retry 3 --progress-bar -o $TempZip $DownloadUrl
        if ($LASTEXITCODE -eq 0 -and (Test-Path $TempZip)) {
            $Downloaded = $true
        }
    } catch {
        $Downloaded = $false
    }
}

if (-not $Downloaded) {
    Write-Host "[install-browser] curl.exe unavailable or failed; using WebClient..." -ForegroundColor Yellow
    $webClient = New-Object System.Net.WebClient
    $webClient.Headers.Add("User-Agent", "Mozilla/5.0")
    $webClient.DownloadFile($DownloadUrl, $TempZip)
}

if (-not (Test-Path $TempZip)) {
    Write-Error "[install-browser] Failed to download CloakBrowser archive from $DownloadUrl"
    exit 1
}

Write-Host "[install-browser] Extracting archive to $TargetDir..." -ForegroundColor Yellow
if (Test-Path $TargetDir) {
    Remove-Item -Path $TargetDir -Recurse -Force
}
New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null

# Use .NET ZipFile for fast extraction
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($TempZip, $TargetDir)

# Clean up temp archive
Remove-Item -Path $TempZip -Force -ErrorAction SilentlyContinue

# Handle potential nested folder inside zip
if (-not (Test-Path $ChromeExe)) {
    $SubDir = Get-ChildItem -Path $TargetDir -Directory | Where-Object { Test-Path (Join-Path $_.FullName "chrome.exe") } | Select-Object -First 1
    if ($SubDir) {
        Get-ChildItem -Path $SubDir.FullName | Move-Item -Destination $TargetDir -Force
        Remove-Item -Path $SubDir.FullName -Recurse -Force
    }
}

if (-not (Test-Path $ChromeExe)) {
    Write-Error "[install-browser] Extraction finished but chrome.exe not found in $TargetDir"
    exit 1
}

Write-Host "[install-browser] CloakBrowser successfully staged at: $ChromeExe" -ForegroundColor Green
try {
    $ver = & $ChromeExe --version 2>$null
    Write-Host "[install-browser] Verified: $ver" -ForegroundColor Green
} catch {
    # Non-blocking smoke test
}
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  Installation Complete! You can now start the server:     " -ForegroundColor Cyan
Write-Host "    uv run python main.py server --port 8080               " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

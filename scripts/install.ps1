# Requires -Version 5.1
<#
.SYNOPSIS
    Windows 一键全自动安装与编译脚本 (Python + uv + Go + Zig + waa-signer.exe)
#>
[CmdletBinding()]
param(
    [string]$Proxy = "",
    [switch]$Start,
    [switch]$ForceDeps,
    [int]$Port = 8080
)

$ErrorActionPreference = "Continue"

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

$ScriptDir = if (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) { $PSScriptRoot } else { (Get-Location).Path }
$ProjectRoot = [System.IO.Path]::GetFullPath("$ScriptDir\..")
Set-Location $ProjectRoot

$probeFile = Join-Path $ProjectRoot ".write_probe.tmp"
try {
    [System.IO.File]::WriteAllText($probeFile, "ok")
    Remove-Item $probeFile -Force -ErrorAction SilentlyContinue
} catch {
    Write-Host "错误: 当前目录无写入权限 ($ProjectRoot)。" -ForegroundColor Red
    Write-Host "请右键 install.bat 选择「以管理员身份运行」，或将项目移至非系统保护目录（如 D:\aistudio-api）后重试。" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "  aistudio-api Windows 一键安装与本地签名引擎构建" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/3] 初始化 Python 与依赖环境..." -ForegroundColor Cyan
$setupArgs = @()
if (-not [string]::IsNullOrWhiteSpace($Proxy)) {
    $setupArgs += "-Proxy"
    $setupArgs += $Proxy
}
& "$ScriptDir\setup-env.ps1" @setupArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "[2/3] 构建本地 WAA IPC 签名引擎 (waa-signer.exe)..." -ForegroundColor Cyan
$buildArgs = @()
if (-not [string]::IsNullOrWhiteSpace($Proxy)) {
    $buildArgs += "-Proxy"
    $buildArgs += $Proxy
}
if ($ForceDeps) {
    $buildArgs += "-ForceDownload"
}
& "$ProjectRoot\waa-signer\build.ps1" @buildArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "[3/3] 检查默认配置文件..." -ForegroundColor Cyan
if (-not (Test-Path "$ProjectRoot\config.yaml") -and (Test-Path "$ProjectRoot\defaults\config.yaml")) {
    Copy-Item "$ProjectRoot\defaults\config.yaml" "$ProjectRoot\config.yaml"
    Write-Host "已初始化默认配置: config.yaml" -ForegroundColor Green
}

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Green
Write-Host "  安装与编译全部完成！" -ForegroundColor Green
Write-Host "  日常启动命令 (或直接双击 start.bat):" -ForegroundColor White
Write-Host "    .\start.bat" -ForegroundColor Cyan
Write-Host "  启动后浏览器访问管理面板: http://localhost:$Port" -ForegroundColor White
Write-Host "==================================================================" -ForegroundColor Green
Write-Host ""

if ($Start) {
    & uv run --no-sync python main.py server --port $Port
}

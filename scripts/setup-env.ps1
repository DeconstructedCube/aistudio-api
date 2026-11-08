# Requires -Version 5.1
<#
.SYNOPSIS
    Windows 环境一键初始化与依赖检查脚本 (PowerShell)
.DESCRIPTION
    检查 Python 3.11+、uv 包管理器、以及可选的 Go 与 Zig (用于本地 WAA 签名服务构建)。
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

# 统一控制台编码为 UTF-8，彻底杜绝 CMD 与 Windows Terminal 乱码
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

# 适配 Windows PowerShell 5.1 默认老旧 TLS 协议，启用 TLS 1.2 与 TLS 1.3
try {
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
    if ([System.Enum]::IsDefined([System.Net.SecurityProtocolType], "Tls13")) {
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor [System.Net.SecurityProtocolType]::Tls13
    }
} catch {}

$ScriptDir = ""
if (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) {
    $ScriptDir = $PSScriptRoot
} else {
    $ScriptDir = (Get-Location).Path
}

$ProjectRoot = [System.IO.Path]::GetFullPath("$ScriptDir\..")
Set-Location $ProjectRoot

function Write-Step([string]$msg) {
    Write-Host "[setup-env] $msg" -ForegroundColor Cyan
}

function Write-Success([string]$msg) {
    Write-Host "[setup-env] $msg" -ForegroundColor Green
}

function Write-Warn([string]$msg) {
    Write-Host "[setup-env] $msg" -ForegroundColor Yellow
}

Write-Step "正在检查 Windows 开发环境..."

# 1. 检查 Python 3.11+
$pyFound = $false
foreach ($cmd in @("python", "py")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $ver = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($ver) {
            $parts = $ver.Split('.')
            if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 11) {
                Write-Success "检测到 Python $ver ($cmd)"
                $pyFound = $true
                break
            }
        }
    }
}
if (-not $pyFound) {
    Write-Warn "未检测到 Python 3.11+。请从 https://www.python.org/downloads/ 安装 (安装时勾选 Add to PATH)。"
}

# 2. 检查 uv
if (-not (Get-Command "uv" -ErrorAction SilentlyContinue)) {
    Write-Step "未检测到 uv，正在通过官方脚本安装..."
    try {
        powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    } catch {
        Write-Warn "自动安装 uv 失败，请手动执行: powershell -ExecutionPolicy ByPass -c `"irm https://astral.sh/uv/install.ps1 | iex`""
    }
} else {
    $uvVer = (uv --version)
    Write-Success "检测到 $uvVer"
}

# 3. 检查本地 WAA 签名服务产物
$signerExe = "$ProjectRoot\waa-signer\bin\waa-signer.exe"
Write-Host ""
if (Test-Path $signerExe) {
    Write-Success "检测到本地 WAA 签名服务二进制已就绪: $signerExe"
} else {
    Write-Host "==================================================================" -ForegroundColor Yellow
    Write-Host "【可选推荐】构建本地高性能 WAA 签名服务 (waa-signer):" -ForegroundColor Yellow
    Write-Host "  若需使用本地原生 SpiderMonkey 152 毫秒级签名并脱离外部依赖:" -ForegroundColor White
    Write-Host "    .\waa-signer\build.bat" -ForegroundColor Cyan
    Write-Host "  (推荐前置环境: winget install GoLang.Go zig.zig)" -ForegroundColor Gray
    Write-Host "  [提示] 若跳过编译，系统启动时将自动降级至远程签名节点 (spinsnow)。" -ForegroundColor Gray
    Write-Host "==================================================================" -ForegroundColor Yellow
}
Write-Host ""
Write-Success "环境检查完毕！启动服务命令: uv run python main.py server --port 8080"

# Requires -Version 5.1
<#
.SYNOPSIS
    Windows Python + uv 环境高防御自动初始化脚本
.DESCRIPTION
    自动刷新 PATH、配置代理、安装 uv 与 Python 3.11+、清理异常 .venv 并执行 uv sync。
#>
[CmdletBinding()]
param(
    [string]$Proxy = ""
)

$ErrorActionPreference = "Continue"
if ([string]::IsNullOrWhiteSpace($env:UV_LINK_MODE)) { $env:UV_LINK_MODE = "copy" }

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

try {
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
    if ([System.Enum]::IsDefined([System.Net.SecurityProtocolType], "Tls13")) {
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor [System.Net.SecurityProtocolType]::Tls13
    }
} catch {}

$ScriptDir = if (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) { $PSScriptRoot } else { (Get-Location).Path }
$ProjectRoot = [System.IO.Path]::GetFullPath("$ScriptDir\..")
Set-Location $ProjectRoot

function Write-Step([string]$msg) { Write-Host "[setup-env] $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "[setup-env] $msg" -ForegroundColor Green }
function Write-Warn([string]$msg) { Write-Host "[setup-env] $msg" -ForegroundColor Yellow }

function Sync-SessionPath {
    $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $extraDirs = @(
        "$env:USERPROFILE\.local\bin",
        "$env:USERPROFILE\.cargo\bin",
        "$env:LOCALAPPDATA\uv"
    )
    $parts = @()
    foreach ($d in $extraDirs) {
        if (-not [string]::IsNullOrWhiteSpace($d) -and (Test-Path $d)) { $parts += $d }
    }
    if ($env:Path) { $parts += ($env:Path -split ';') }
    if ($userPath) { $parts += ($userPath -split ';') }
    if ($machinePath) { $parts += ($machinePath -split ';') }
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $unique = @()
    foreach ($p in $parts) {
        $trimmed = $p.Trim()
        if ($trimmed -and $seen.Add($trimmed)) { $unique += $trimmed }
    }
    $env:Path = ($unique -join ';')
}

function Get-DetectedProxy([string]$explicit) {
    if (-not [string]::IsNullOrWhiteSpace($explicit)) { return $explicit.Trim() }
    foreach ($var in @("AISTUDIO_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")) {
        $val = [System.Environment]::GetEnvironmentVariable($var)
        if (-not [string]::IsNullOrWhiteSpace($val)) { return $val.Trim() }
    }
    try {
        $reg = Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -ErrorAction SilentlyContinue
        if ($reg -and $reg.ProxyEnable -eq 1 -and -not [string]::IsNullOrWhiteSpace($reg.ProxyServer)) {
            $raw = ([string]$reg.ProxyServer).Trim()
            if ($raw -match "^https?://") { return $raw }
            if ($raw -match "https?=([^;]+)") { return "http://" + $matches[1] }
            return "http://" + $raw
        }
    } catch {}
    foreach ($port in @(7890, 7897, 10808, 10809, 2080)) {
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient
            $async = $tcp.BeginConnect("127.0.0.1", $port, $null, $null)
            if ($async.AsyncWaitHandle.WaitOne(80, $false) -and $tcp.Connected) {
                $tcp.Close()
                return "http://127.0.0.1:$port"
            }
            $tcp.Close()
        } catch {}
    }
    return ""
}

Sync-SessionPath
$EffectiveProxy = Get-DetectedProxy $Proxy
if (-not [string]::IsNullOrWhiteSpace($EffectiveProxy)) {
    Write-Step "使用网络代理: $EffectiveProxy"
    if ([string]::IsNullOrWhiteSpace($env:HTTPS_PROXY)) { $env:HTTPS_PROXY = $EffectiveProxy }
    if ([string]::IsNullOrWhiteSpace($env:HTTP_PROXY)) { $env:HTTP_PROXY = $EffectiveProxy }
}

# 1. 移除误拷贝的 Termux 专用 uv.toml
$uvToml = "$ProjectRoot\uv.toml"
if ((Test-Path $uvToml) -and ((Get-Content $uvToml -Raw -ErrorAction SilentlyContinue) -match "termux-user-repository")) {
    Write-Warn "检测到残留的 Termux 专用 uv.toml，已自动删除。"
    Remove-Item $uvToml -Force -ErrorAction SilentlyContinue
}

# 2. 检查并自动安装 uv
if (-not (Get-Command "uv" -ErrorAction SilentlyContinue)) {
    Write-Step "未检测到 uv，正在自动安装..."
    $uvInstalled = $false
    foreach ($installerUrl in @(
        "https://astral.sh/uv/install.ps1",
        "https://ghproxy.net/https://github.com/astral-sh/uv/releases/latest/download/uv-installer.ps1"
    )) {
        try {
            $scriptBody = Invoke-RestMethod -Uri $installerUrl -TimeoutSec 30
            if (-not [string]::IsNullOrWhiteSpace($scriptBody)) {
                Invoke-Expression $scriptBody
                Sync-SessionPath
                if (Get-Command "uv" -ErrorAction SilentlyContinue) {
                    $uvInstalled = $true
                    break
                }
            }
        } catch {}
    }
    if (-not $uvInstalled -and (Get-Command "winget" -ErrorAction SilentlyContinue)) {
        & winget install -e --id astral-sh.uv --accept-source-agreements --accept-package-agreements
        Sync-SessionPath
    }
}

if (-not (Get-Command "uv" -ErrorAction SilentlyContinue)) {
    Write-Host "错误: 自动安装 uv 失败。请手动执行: powershell -ExecutionPolicy ByPass -c `"irm https://astral.sh/uv/install.ps1 | iex`"" -ForegroundColor Red
    exit 1
}
Write-Ok "检测到 $(uv --version)"

# 3. 确保 Python 3.11+ 可用
$pyOk = $false
foreach ($cmd in @("python", "py")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        try {
            & $cmd -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) {
                $pyOk = $true
                break
            }
        } catch {}
    }
}
if (-not $pyOk) {
    Write-Step "未检测到 Python 3.11+，正在通过 uv 自动安装 Python 3.11..."
    & uv python install 3.11
    if ($LASTEXITCODE -ne 0) {
        Write-Host "错误: uv 安装 Python 3.11 失败。" -ForegroundColor Red
        exit 1
    }
}

# 4. 校验并修复异常 .venv
$venvPy = "$ProjectRoot\.venv\Scripts\python.exe"
if (Test-Path "$ProjectRoot\.venv") {
    $venvHealthy = $false
    if (Test-Path $venvPy) {
        try {
            & $venvPy -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { $venvHealthy = $true }
        } catch {}
    }
    if (-not $venvHealthy) {
        Write-Warn "检测到现有 .venv 损坏或 Python 版本不兼容，正在清理重建..."
        Remove-Item "$ProjectRoot\.venv" -Recurse -Force
    }
}

# 5. 同步 Python 依赖（支持清华镜像回退）
Write-Step "正在同步 Python 依赖..."
& uv sync
if ($LASTEXITCODE -ne 0) {
    Write-Warn "官方 PyPI 同步失败，尝试使用清华 PyPI 镜像重试..."
    $env:UV_DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
    & uv sync
    if ($LASTEXITCODE -ne 0) {
        Write-Host "错误: uv sync 安装依赖失败，请检查网络连接或代理配置。" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

# 6. 运行时导入自检
Write-Step "正在校验 Python 核心运行时依赖..."
& uv run --no-sync python -c "import fastapi, httpx, pydantic, yaml, uvicorn"
if ($LASTEXITCODE -ne 0) {
    Write-Host "错误: Python 运行时依赖导入自检失败。" -ForegroundColor Red
    exit 1
}

Write-Ok "Windows Python 环境已全部就绪!"

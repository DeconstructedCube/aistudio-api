# Requires -Version 5.1
<#
.SYNOPSIS
    一键编译 WAA Signer (Windows x64)
.DESCRIPTION
    自动检测环境、下载 SpiderMonkey 静态依赖，并通过 Go + CGO (Zig / MinGW-w64 GCC / Clang) 构建独立的 waa-signer.exe。
#>
[CmdletBinding()]
param(
    [string]$Proxy = "",
    [switch]$ForceDownload
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$SM_VERSION = "v152.0.4-aistudio2api.1"
$ARCH = "windows-amd64"
$Tarball = "spidermonkey-$SM_VERSION-$ARCH.tar.gz"
$DepsDir = Join-Path $ScriptDir ".deps\spidermonkey\$ARCH"
$BinDir = Join-Path $ScriptDir "bin"
$OutExe = Join-Path $BinDir "waa-signer.exe"

# 1. 检查 Go 环境
if (-not (Get-Command "go" -ErrorAction SilentlyContinue)) {
    Write-Host "错误: 未找到 Go 运行环境。" -ForegroundColor Red
    Write-Host "请先安装 Go 1.22+ 并添加至 PATH:" -ForegroundColor Yellow
    Write-Host "    winget install GoLang.Go" -ForegroundColor White
    Write-Host "或访问官方下载页: https://go.dev/dl/" -ForegroundColor White
    exit 1
}

$goVersionStr = (go version)
Write-Host ">>> 检测到 Go: $goVersionStr" -ForegroundColor Cyan

# 2. 检查 C/C++ 编译器 (Zig 优先推荐, MinGW GCC, Clang)
$CC = ""
$CXX = ""
$ExtLdFlags = ""

if (Get-Command "zig" -ErrorAction SilentlyContinue) {
    Write-Host ">>> 检测到 Zig 编译器，启用高性能免配置 CGO 静态交叉编译" -ForegroundColor Green
    $CC = "zig cc -target x86_64-windows-gnu"
    $CXX = "zig c++ -target x86_64-windows-gnu"
    $ExtLdFlags = "-Wl,--strip-all"
} elseif (Get-Command "gcc" -ErrorAction SilentlyContinue) {
    Write-Host ">>> 检测到 GCC (MinGW-w64) 编译器" -ForegroundColor Green
    $CC = "gcc"
    $CXX = "g++"
    $ExtLdFlags = "-Wl,--strip-all"
} elseif (Get-Command "clang" -ErrorAction SilentlyContinue) {
    Write-Host ">>> 检测到 Clang 编译器" -ForegroundColor Green
    $CC = "clang"
    $CXX = "clang++"
    $ExtLdFlags = "-Wl,--strip-all"
} else {
    Write-Host ""
    Write-Host "==================================================================" -ForegroundColor Yellow
    Write-Host "错误: 未找到 C/C++ 编译器 (构建嵌入式 SpiderMonkey 需要 C++20 支持)。" -ForegroundColor Red
    Write-Host "推荐安装极速免配置的 Zig (推荐方式):" -ForegroundColor Yellow
    Write-Host "    winget install zig.zig" -ForegroundColor White
    Write-Host "或使用 Scoop:" -ForegroundColor Yellow
    Write-Host "    scoop install zig" -ForegroundColor White
    Write-Host "亦可使用已配置好的 MinGW-w64 (gcc/g++) 环境。" -ForegroundColor Gray
    Write-Host "==================================================================" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

# 3. 准备依赖目录并下载 SpiderMonkey 静态依赖
$LibStatic = Join-Path $DepsDir "lib\libjs_static.a"
if ($ForceDownload -or -not (Test-Path $LibStatic)) {
    if (-not (Test-Path $DepsDir)) {
        New-Item -ItemType Directory -Path $DepsDir -Force | Out-Null
    }

    $EffectiveProxy = $Proxy
    if ([string]::IsNullOrWhiteSpace($EffectiveProxy)) {
        $EffectiveProxy = $env:AISTUDIO_PROXY
    }
    if ([string]::IsNullOrWhiteSpace($EffectiveProxy)) {
        $EffectiveProxy = $env:HTTPS_PROXY
    }
    if ([string]::IsNullOrWhiteSpace($EffectiveProxy)) {
        $EffectiveProxy = $env:HTTP_PROXY
    }

    $DownloadUrl = "https://github.com/Mag1cFall/firefox/releases/download/$SM_VERSION/$Tarball"
    $TempTar = Join-Path $ScriptDir ".deps\$Tarball"
    $TempTarDir = Split-Path -Parent $TempTar
    if (-not (Test-Path $TempTarDir)) {
        New-Item -ItemType Directory -Path $TempTarDir -Force | Out-Null
    }

    Write-Host ">>> 正在下载 SpiderMonkey 静态依赖库: $DownloadUrl" -ForegroundColor Cyan
    if (-not [string]::IsNullOrWhiteSpace($EffectiveProxy)) {
        Write-Host ">>> 使用代理: $EffectiveProxy" -ForegroundColor Gray
    }

    $downloadSuccess = $false
    # 优先使用 Windows 10+ 自带的 curl.exe
    if (Get-Command "curl.exe" -ErrorAction SilentlyContinue) {
        $curlArgs = @("-fL", "--retry", "2", "-o", $TempTar, $DownloadUrl)
        if (-not [string]::IsNullOrWhiteSpace($EffectiveProxy)) {
            $curlArgs = @("-x", $EffectiveProxy) + $curlArgs
        }
        & curl.exe @curlArgs
        if ($LASTEXITCODE -eq 0 -and (Test-Path $TempTar)) {
            $downloadSuccess = $true
        }
    }

    if (-not $downloadSuccess) {
        $iwrParams = @{
            Uri = $DownloadUrl
            OutFile = $TempTar
            UseBasicParsing = $true
        }
        if (-not [string]::IsNullOrWhiteSpace($EffectiveProxy)) {
            $iwrParams["Proxy"] = $EffectiveProxy
        }
        Invoke-WebRequest @iwrParams
        if (Test-Path $TempTar) {
            $downloadSuccess = $true
        }
    }

    if (-not $downloadSuccess -or -not (Test-Path $TempTar)) {
        Write-Host "错误: 下载 SpiderMonkey 依赖失败，请检查网络连通性或配置代理。" -ForegroundColor Red
        exit 1
    }

    Write-Host ">>> 正在解压 SpiderMonkey 到: $DepsDir" -ForegroundColor Cyan
    if (Get-Command "tar.exe" -ErrorAction SilentlyContinue) {
        & tar.exe -xzf $TempTar -C $DepsDir
    } else {
        tar -xzf $TempTar -C $DepsDir
    }
    Remove-Item $TempTar -Force -ErrorAction SilentlyContinue

    if (-not (Test-Path $LibStatic)) {
        Write-Host "错误: 解压后未找到关键静态库: $LibStatic" -ForegroundColor Red
        exit 1
    }
    Write-Host ">>> SpiderMonkey 静态库解压就绪!" -ForegroundColor Green
}

# 4. 创建产物目录
if (-not (Test-Path $BinDir)) {
    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
}

# 5. 执行 Go 编译
Write-Host ">>> 开始编译 waa-signer (CGO_ENABLED=1 GOOS=windows GOARCH=amd64)..." -ForegroundColor Cyan

$env:CGO_ENABLED = "1"
$env:GOOS = "windows"
$env:GOARCH = "amd64"
$env:CC = $CC
$env:CXX = $CXX
$IncludePath = Join-Path $DepsDir "include" -Replace "\\", "/"
$LibPath = Join-Path $DepsDir "lib" -Replace "\\", "/"
$env:CGO_CXXFLAGS = "-I$IncludePath"
$env:CGO_LDFLAGS = "-L$LibPath"

$ldflags = "-s -w"
if (-not [string]::IsNullOrWhiteSpace($ExtLdFlags)) {
    $ldflags += " -extldflags `"$ExtLdFlags`""
}

go build -trimpath -tags "netgo,osusergo" -ldflags "$ldflags" -o $OutExe ./cmd/waa-signer
if ($LASTEXITCODE -ne 0) {
    Write-Host "错误: 编译 waa-signer 失败！" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "==================================================================" -ForegroundColor Green
Write-Host ">>> 编译成功！产物位置: $OutExe" -ForegroundColor Green
Write-Host ">>> 验证与运行方式:" -ForegroundColor Cyan
Write-Host "    $OutExe -listen 127.0.0.1:8081" -ForegroundColor White
Write-Host "==================================================================" -ForegroundColor Green

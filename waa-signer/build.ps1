# Requires -Version 5.1
<#
.SYNOPSIS
    一键编译 WAA Signer (Windows x64)
.DESCRIPTION
    自动检测环境、检测系统/进程代理、下载 SpiderMonkey 静态依赖，
    并通过 Go + CGO (Zig / MinGW-w64 GCC / Clang) 构建独立的 waa-signer.exe。
#>
[CmdletBinding()]
param(
    [string]$Proxy = "",
    [string]$LocalFile = "",
    [switch]$ForceDownload
)

$ErrorActionPreference = "Stop"

# 统一控制台编码为 UTF-8，彻底杜绝 CMD 与 Windows Terminal 乱码
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

# 适配 Windows PowerShell 5.1 默认老旧 TLS 协议，强制启用 TLS 1.2 与 TLS 1.3
try {
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
    if ([System.Enum]::IsDefined([System.Net.SecurityProtocolType], "Tls13")) {
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor [System.Net.SecurityProtocolType]::Tls13
    }
} catch {}

# 获取脚本所在根目录（使用可靠的 $PSScriptRoot，避免 Split-Path 对 System.Object 的类型转换报错）
$ScriptDir = ""
if (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) {
    $ScriptDir = $PSScriptRoot
} else {
    $ScriptDir = (Get-Location).Path
}
Set-Location $ScriptDir

$SM_VERSION = "v152.0.4-aistudio2api.1"
$ARCH = "windows-amd64"
$Tarball = "spidermonkey-$SM_VERSION-$ARCH.tar.gz"

$DepsDir = "$ScriptDir\.deps\spidermonkey\$ARCH"
$BinDir = "$ScriptDir\bin"
$OutExe = "$BinDir\waa-signer.exe"

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

# 代理智能探测函数
function Get-DetectedProxy([string]$explicit) {
    if (-not [string]::IsNullOrWhiteSpace($explicit)) {
        return $explicit.Trim()
    }
    # 检查环境变量
    foreach ($var in @("AISTUDIO_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")) {
        $val = [System.Environment]::GetEnvironmentVariable($var)
        if (-not [string]::IsNullOrWhiteSpace($val)) {
            return $val.Trim()
        }
    }
    # 检查 Windows 注册表系统代理 (Clash/v2rayN/Mihomo 系统代理模式)
    try {
        $reg = Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -ErrorAction SilentlyContinue
        if ($reg -and $reg.ProxyEnable -eq 1 -and -not [string]::IsNullOrWhiteSpace($reg.ProxyServer)) {
            $raw = [string]$reg.ProxyServer
            $raw = $raw.Trim()
            if ($raw -match "^https?://") {
                return $raw
            }
            if ($raw -match "https?=([^;]+)") {
                return "http://" + $matches[1]
            }
            return "http://" + $raw
        }
    } catch {}
    # 本地常见代理端口探测 (7890 Clash, 10808/10809 v2rayN)
    foreach ($port in @(7890, 10808, 10809)) {
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient
            $async = $tcp.BeginConnect("127.0.0.1", $port, $null, $null)
            if ($async.AsyncWaitHandle.WaitOne(60, $false) -and $tcp.Connected) {
                $tcp.Close()
                return "http://127.0.0.1:$port"
            }
            $tcp.Close()
        } catch {}
    }
    return ""
}

# 3. 准备依赖目录并下载 SpiderMonkey 静态依赖
$LibStatic = "$DepsDir\lib\libjs_static.a"
if ($ForceDownload -or -not (Test-Path $LibStatic)) {
    if (-not (Test-Path $DepsDir)) {
        New-Item -ItemType Directory -Path $DepsDir -Force | Out-Null
    }

    $EffectiveProxy = Get-DetectedProxy $Proxy
    $TempTarDir = "$ScriptDir\.deps"
    if (-not (Test-Path $TempTarDir)) {
        New-Item -ItemType Directory -Path $TempTarDir -Force | Out-Null
    }
    $TempTar = "$TempTarDir\$Tarball"

    $downloadSuccess = $false

    # 3.1 优先检查显式指定的本地路径或本地下载目录中是否已有安装包 (避免重复下载与网络 504)
    $candidateFiles = @()
    if (-not [string]::IsNullOrWhiteSpace($LocalFile)) {
        $candidateFiles += $LocalFile
    }
    $candidateFiles += $TempTar
    $candidateFiles += "$DepsDir\$Tarball"
    $candidateFiles += "$ScriptDir\$Tarball"
    if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        $candidateFiles += "$env:USERPROFILE\Downloads\$Tarball"
    }

    foreach ($c in $candidateFiles) {
        if ([string]::IsNullOrWhiteSpace($c)) { continue }
        if ((Test-Path $c) -and (Get-Item $c).Length -gt 10000000) {
            Write-Host ">>> 检测到本地已存在静态依赖包: $c" -ForegroundColor Green
            if ($c -ne $TempTar) {
                Copy-Item $c $TempTar -Force
            }
            $downloadSuccess = $true
            break
        }
    }

    # 3.2 若本地无有效安装包，通过官方源及多镜像源重试下载
    if (-not $downloadSuccess) {
        $officialUrl = "https://github.com/Mag1cFall/firefox/releases/download/$SM_VERSION/$Tarball"
        $urlsToTry = @(
            $officialUrl,
            "https://ghproxy.net/$officialUrl",
            "https://gh-proxy.com/$officialUrl",
            "https://mirror.ghproxy.com/$officialUrl"
        )

        if (-not [string]::IsNullOrWhiteSpace($EffectiveProxy)) {
            Write-Host ">>> 启用代理: $EffectiveProxy" -ForegroundColor Gray
        }

        foreach ($url in $urlsToTry) {
            Write-Host ">>> 正在下载 SpiderMonkey 静态依赖库: $url" -ForegroundColor Cyan
            # 策略 A: 优先使用系统自带 curl.exe (-4 强制 IPv4 避免 Windows IPv6 超时，带 Chrome User-Agent 避免 504)
            if (Get-Command "curl.exe" -ErrorAction SilentlyContinue) {
                Remove-Item $TempTar -Force -ErrorAction SilentlyContinue
                $curlArgs = @(
                    "-4",
                    "-fL",
                    "--retry", "2",
                    "--connect-timeout", "10",
                    "--max-time", "180",
                    "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "-H", "Accept: application/octet-stream,application/gzip,*/*",
                    "-o", $TempTar,
                    $url
                )
                if (-not [string]::IsNullOrWhiteSpace($EffectiveProxy)) {
                    $curlArgs = @("-x", $EffectiveProxy) + $curlArgs
                }
                & curl.exe @curlArgs
                if ($LASTEXITCODE -eq 0 -and (Test-Path $TempTar) -and (Get-Item $TempTar).Length -gt 10000000) {
                    $downloadSuccess = $true
                    break
                }
            }

            # 策略 B: 使用 WebClient 备用下载
            try {
                Remove-Item $TempTar -Force -ErrorAction SilentlyContinue
                $wc = New-Object System.Net.WebClient
                $wc.Headers.Add("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
                $wc.Headers.Add("Accept", "application/octet-stream,application/gzip,*/*")
                if (-not [string]::IsNullOrWhiteSpace($EffectiveProxy)) {
                    $wc.Proxy = New-Object System.Net.WebProxy($EffectiveProxy)
                }
                $wc.DownloadFile($url, $TempTar)
                if ((Test-Path $TempTar) -and (Get-Item $TempTar).Length -gt 10000000) {
                    $downloadSuccess = $true
                    break
                }
            } catch {}
        }
    }

    if (-not $downloadSuccess -or -not (Test-Path $TempTar) -or (Get-Item $TempTar).Length -le 10000000) {
        Write-Host ""
        Write-Host "==================================================================" -ForegroundColor Yellow
        Write-Host "错误: 终端自动化下载 SpiderMonkey 依赖包失败 (GitHub 节点返回 504 或超时)。" -ForegroundColor Red
        Write-Host ""
        Write-Host "【极简手动解决方式】:" -ForegroundColor Yellow
        Write-Host "1. 直接在浏览器中点击下方链接下载依赖压缩包:" -ForegroundColor White
        Write-Host "   https://github.com/Mag1cFall/firefox/releases/download/$SM_VERSION/$Tarball" -ForegroundColor Cyan
        Write-Host "2. 下载完成后，直接保留在浏览器的「下载」文件夹中，" -ForegroundColor White
        Write-Host "   或者复制到本目录下的 .deps\ 文件夹中。" -ForegroundColor White
        Write-Host "3. 重新运行 build.bat，脚本将自动检测该文件并完成一秒编译！" -ForegroundColor Green
        Write-Host "==================================================================" -ForegroundColor Yellow
        Write-Host ""
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
$IncludePath = "$DepsDir\include" -replace '\\', '/'
$LibPath = "$DepsDir\lib" -replace '\\', '/'
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

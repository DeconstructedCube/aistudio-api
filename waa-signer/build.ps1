# Requires -Version 5.1
<#
.SYNOPSIS
    一键高防御编译 WAA Signer (Windows x64 / ARM64)
.DESCRIPTION
    自动刷新 PATH、检测系统/进程代理、自动安装或下载独立 Go 与 Zig 工具链、
    校验并解压 SpiderMonkey 静态库，并通过 CGO 构建 waa-signer.exe。
#>
[CmdletBinding()]
param(
    [string]$Proxy = "",
    [string]$LocalFile = "",
    [switch]$ForceDownload,
    [switch]$Test
)

$ErrorActionPreference = "Continue"

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
Set-Location $ScriptDir

$SM_VERSION = "v152.0.4-aistudio2api.1"
$ZIG_VERSION = "0.17.1"
$ARCH = "windows-amd64"
$Tarball = "spidermonkey-$SM_VERSION-$ARCH.tar.gz"

$DepsRoot = "$ScriptDir\.deps"
$ToolsDir = "$DepsRoot\tools"
$DepsDir = "$DepsRoot\spidermonkey\$ARCH"
$BinDir = "$ScriptDir\bin"
$OutExe = "$BinDir\waa-signer.exe"

function Sync-SessionPath {
    $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $extraDirs = @(
        "$ToolsDir\go\bin",
        "$ToolsDir\zig",
        "$env:ProgramFiles\Go\bin",
        "$env:LOCALAPPDATA\Programs\Go\bin",
        "$env:USERPROFILE\scoop\shims"
    )
    $parts = @()
    foreach ($d in $extraDirs) {
        if (-not [string]::IsNullOrWhiteSpace($d) -and (Test-Path $d)) {
            $parts += $d
        }
    }
    if ($env:Path) { $parts += ($env:Path -split ';') }
    if ($userPath) { $parts += ($userPath -split ';') }
    if ($machinePath) { $parts += ($machinePath -split ';') }
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $unique = @()
    foreach ($p in $parts) {
        $trimmed = $p.Trim()
        if ($trimmed -and $seen.Add($trimmed)) {
            $unique += $trimmed
        }
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

function Download-File([string]$url, [string]$dest, [string]$proxyUrl) {
    Remove-Item $dest -Force -ErrorAction SilentlyContinue
    if (Get-Command "curl.exe" -ErrorAction SilentlyContinue) {
        $curlArgs = @(
            "-4", "-fL", "--retry", "2", "--connect-timeout", "10", "--max-time", "240",
            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "-o", $dest, $url
        )
        if (-not [string]::IsNullOrWhiteSpace($proxyUrl)) {
            $curlArgs = @("-x", $proxyUrl) + $curlArgs
        }
        & curl.exe @curlArgs
        if ($LASTEXITCODE -eq 0 -and (Test-Path $dest)) { return $true }
    }
    try {
        Remove-Item $dest -Force -ErrorAction SilentlyContinue
        $wc = New-Object System.Net.WebClient
        $wc.Headers.Add("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        if (-not [string]::IsNullOrWhiteSpace($proxyUrl)) {
            $wc.Proxy = New-Object System.Net.WebProxy($proxyUrl)
        }
        $wc.DownloadFile($url, $dest)
        return (Test-Path $dest)
    } catch {
        return $false
    }
}

function Test-TarGz([string]$path, [long]$minBytes) {
    if (-not (Test-Path $path)) { return $false }
    if ((Get-Item $path).Length -lt $minBytes) { return $false }
    $bytes = [System.IO.File]::ReadAllBytes($path)
    if ($bytes.Length -lt 2 -or $bytes[0] -ne 0x1F -or $bytes[1] -ne 0x8B) { return $false }
    if (Get-Command "tar.exe" -ErrorAction SilentlyContinue) {
        & tar.exe -tzf $path >$null 2>$null
        return ($LASTEXITCODE -eq 0)
    }
    return $true
}

function Test-ZipArchive([string]$path, [long]$minBytes) {
    if (-not (Test-Path $path)) { return $false }
    if ((Get-Item $path).Length -lt $minBytes) { return $false }
    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue
        $zip = [System.IO.Compression.ZipFile]::OpenRead($path)
        $count = $zip.Entries.Count
        $zip.Dispose()
        return ($count -gt 0)
    } catch {
        return $false
    }
}

function Get-GoMajorMinor {
    if (-not (Get-Command "go" -ErrorAction SilentlyContinue)) { return $null }
    $prevToolchain = $env:GOTOOLCHAIN
    $env:GOTOOLCHAIN = "local"
    $v = (& go version 2>&1 | Out-String)
    if ($null -eq $prevToolchain) { Remove-Item Env:GOTOOLCHAIN -ErrorAction SilentlyContinue } else { $env:GOTOOLCHAIN = $prevToolchain }
    if ($v -match "go(\d+)\.(\d+)") {
        return @([int]$matches[1], [int]$matches[2])
    }
    return $null
}

Sync-SessionPath
$EffectiveProxy = Get-DetectedProxy $Proxy
if (-not [string]::IsNullOrWhiteSpace($EffectiveProxy)) {
    Write-Host ">>> 使用网络代理: $EffectiveProxy" -ForegroundColor Gray
    if ([string]::IsNullOrWhiteSpace($env:HTTPS_PROXY)) { $env:HTTPS_PROXY = $EffectiveProxy }
    if ([string]::IsNullOrWhiteSpace($env:HTTP_PROXY)) { $env:HTTP_PROXY = $EffectiveProxy }
}

# 1. 确保 Go >= 1.21 就绪（缺失或过低时自动通过官方免安装 Zip 或 winget 就绪）
$goVer = Get-GoMajorMinor
if (-not $goVer -or ($goVer[0] -eq 1 -and $goVer[1] -lt 21)) {
    if ($goVer) {
        Write-Host ">>> 检测到 Go 版本过低 ($($goVer[0]).$($goVer[1]))，正在自动升级..." -ForegroundColor Yellow
    } else {
        Write-Host ">>> 未检测到 Go，正在自动配置 Go 工具链..." -ForegroundColor Cyan
    }

    $installedGo = $false
    New-Item -ItemType Directory -Path $ToolsDir -Force | Out-Null

    $resolvedGoVer = "go1.24.4"
    foreach ($vUrl in @("https://go.dev/VERSION?m=text", "https://golang.google.cn/VERSION?m=text")) {
        try {
            $tmpVerFile = "$ToolsDir\go_version.txt"
            if (Download-File $vUrl $tmpVerFile $EffectiveProxy) {
                $line = (Get-Content $tmpVerFile -TotalCount 1).Trim()
                if ($line -match "^go1\.\d+") {
                    $resolvedGoVer = $line
                    Remove-Item $tmpVerFile -Force -ErrorAction SilentlyContinue
                    break
                }
            }
        } catch {}
    }

    $goZipName = "$resolvedGoVer.windows-amd64.zip"
    $goZipPath = "$ToolsDir\$goZipName"
    foreach ($u in @("https://go.dev/dl/$goZipName", "https://golang.google.cn/dl/$goZipName")) {
        Write-Host ">>> 正在下载免安装 Go 工具链 ($resolvedGoVer): $u" -ForegroundColor Cyan
        if ((Download-File $u $goZipPath $EffectiveProxy) -and (Test-ZipArchive $goZipPath 30000000)) {
            $goExtract = "$ToolsDir\go"
            if (Test-Path $goExtract) { Remove-Item $goExtract -Recurse -Force }
            Expand-Archive -Path $goZipPath -DestinationPath $ToolsDir -Force
            Remove-Item $goZipPath -Force -ErrorAction SilentlyContinue
            $env:GOROOT = "$ToolsDir\go"
            Sync-SessionPath
            $installedGo = $true
            break
        }
        Remove-Item $goZipPath -Force -ErrorAction SilentlyContinue
    }

    if (-not $installedGo -and (Get-Command "winget" -ErrorAction SilentlyContinue)) {
        Write-Host ">>> 回退尝试通过 winget 安装 GoLang.Go..." -ForegroundColor Cyan
        & winget install -e --id GoLang.Go --accept-source-agreements --accept-package-agreements
        Sync-SessionPath
    }

    $goVer = Get-GoMajorMinor
    if (-not $goVer -or ($goVer[0] -eq 1 -and $goVer[1] -lt 21)) {
        Write-Host "错误: 自动安装 Go 失败，请手动安装 Go 1.27+: https://go.dev/dl/" -ForegroundColor Red
        exit 1
    }
}

$goVerDisplay = (& { $env:GOTOOLCHAIN = "local"; (& go version 2>&1 | Out-String).Trim() })
Write-Host ">>> 检测到 Go: $goVerDisplay" -ForegroundColor Cyan
if ($goVer[0] -eq 1 -and $goVer[1] -lt 27) {
    $env:GOTOOLCHAIN = "auto"
}

if ([string]::IsNullOrWhiteSpace($env:GOPROXY) -or $env:GOPROXY -eq "https://proxy.golang.org,direct") {
    $tmpProbe = "$DepsRoot\_goproxy_probe.tmp"
    New-Item -ItemType Directory -Path $DepsRoot -Force | Out-Null
    $probeOk = $false
    if (Get-Command "curl.exe" -ErrorAction SilentlyContinue) {
        $pArgs = @("-4", "-fsSL", "--connect-timeout", "3", "--max-time", "5", "-o", $tmpProbe, "https://proxy.golang.org")
        if (-not [string]::IsNullOrWhiteSpace($EffectiveProxy)) { $pArgs = @("-x", $EffectiveProxy) + $pArgs }
        & curl.exe @pArgs 2>$null
        $probeOk = ($LASTEXITCODE -eq 0)
        Remove-Item $tmpProbe -Force -ErrorAction SilentlyContinue
    }
    if (-not $probeOk) {
        Write-Host ">>> 启用国内 Go 模块代理镜像 (goproxy.cn)" -ForegroundColor Gray
        $env:GOPROXY = "https://goproxy.cn,https://mirrors.aliyun.com/goproxy/,direct"
    }
}

# 2. 检查/自动就绪 Zig 编译器 (兼容 Win10 LTSC / 无 winget 环境)
$CC = ""
$CXX = ""
$ExtLdFlags = ""

function Find-ZigExecutable {
    $cmd = Get-Command "zig" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $searchPaths = @(
        "$ToolsDir\zig",
        "$env:LOCALAPPDATA\Microsoft\WinGet\Packages",
        "$env:USERPROFILE\scoop\shims",
        "$env:ProgramFiles\zig",
        "$DepsRoot\zig"
    )
    foreach ($p in $searchPaths) {
        if (Test-Path $p) {
            $found = Get-ChildItem -Path $p -Filter "zig.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($found) { return $found.FullName }
        }
    }
    return ""
}

function Test-ZigMeetsMin([string]$zigPath) {
    if ([string]::IsNullOrWhiteSpace($zigPath) -or -not (Test-Path $zigPath)) { return $false }
    try {
        $verStr = (& $zigPath version 2>$null | Out-String).Trim()
        if ($verStr -match "^(\d+)\.(\d+)\.(\d+)") {
            $maj = [int]$matches[1]; $min = [int]$matches[2]; $pat = [int]$matches[3]
            return ($maj -gt 0) -or ($min -gt 17) -or ($min -eq 17 -and $pat -ge 1)
        }
    } catch {}
    return $false
}

$mingwClang = Get-Command "x86_64-w64-mingw32-clang" -ErrorAction SilentlyContinue
$zigExe = Find-ZigExecutable
if ($zigExe -and -not (Test-ZigMeetsMin $zigExe)) {
    $oldZigVer = (& $zigExe version 2>$null | Out-String).Trim()
    Write-Host ">>> 检测到 Zig $oldZigVer 低于 0.17.1（Windows 静态链接 ICU 需要 0.17.1+ 提供的 _vsnwprintf 导入符号），正在自动准备 Zig $ZIG_VERSION..." -ForegroundColor Yellow
    $zigExe = ""
}

if (-not $zigExe -and -not $mingwClang) {
    Write-Host ">>> 正在自动下载官方免配置 Zig $ZIG_VERSION..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Path $ToolsDir -Force | Out-Null
    $zigZipName = "zig-x86_64-windows-$ZIG_VERSION.zip"
    $zigZipPath = "$ToolsDir\$zigZipName"
    $zigUrls = @(
        "https://ziglang.org/download/$ZIG_VERSION/$zigZipName",
        "https://ghproxy.net/https://ziglang.org/download/$ZIG_VERSION/$zigZipName"
    )
    foreach ($u in $zigUrls) {
        if ((Download-File $u $zigZipPath $EffectiveProxy) -and (Test-ZipArchive $zigZipPath 15000000)) {
            $zigTemp = "$ToolsDir\_zig_extract"
            if (Test-Path $zigTemp) { Remove-Item $zigTemp -Recurse -Force }
            Expand-Archive -Path $zigZipPath -DestinationPath $zigTemp -Force
            Remove-Item $zigZipPath -Force -ErrorAction SilentlyContinue
            $innerDir = Get-ChildItem -Path $zigTemp -Directory | Select-Object -First 1
            $zigTarget = "$ToolsDir\zig"
            if (Test-Path $zigTarget) { Remove-Item $zigTarget -Recurse -Force }
            if ($innerDir) {
                Move-Item -Path $innerDir.FullName -Destination $zigTarget -Force
            }
            Remove-Item $zigTemp -Recurse -Force -ErrorAction SilentlyContinue
            if (Test-Path "$zigTarget\zig.exe") {
                $zigExe = "$zigTarget\zig.exe"
            }
            break
        }
        Remove-Item $zigZipPath -Force -ErrorAction SilentlyContinue
    }
    if (-not $zigExe -and (Get-Command "winget" -ErrorAction SilentlyContinue)) {
        Write-Host ">>> 回退尝试通过 winget 安装/升级 zig.zig..." -ForegroundColor Cyan
        & winget install -e --id zig.zig --accept-source-agreements --accept-package-agreements
        Sync-SessionPath
        $cand = Find-ZigExecutable
        if (Test-ZigMeetsMin $cand) { $zigExe = $cand }
    }
}

if ($zigExe) {
    $zigDir = Split-Path -Parent $zigExe
    $env:Path = "$zigDir;$env:Path"
    Write-Host ">>> 检测到 Zig 编译器: $zigExe ($(& $zigExe version))" -ForegroundColor Green
    $CC = "zig cc -target x86_64-windows-gnu"
    $CXX = "zig c++ -target x86_64-windows-gnu"
    $ExtLdFlags = "-Wl,--strip-all"
} elseif ($mingwClang) {
    Write-Host ">>> 检测到 llvm-mingw 编译器: $($mingwClang.Source)" -ForegroundColor Green
    $CC = "x86_64-w64-mingw32-clang"
    $CXX = "x86_64-w64-mingw32-clang++"
    $ExtLdFlags = "-Wl,--strip-all"
} else {
    Write-Host "错误: 未能自动就绪 Zig $ZIG_VERSION+ 或 llvm-mingw 编译器。" -ForegroundColor Red
    exit 1
}

# 3. 下载并校验 SpiderMonkey 静态库
$LibStatic = "$DepsDir\lib\libjs_static.a"
$HeaderApi = "$DepsDir\include\jsapi.h"
if ($ForceDownload -or -not (Test-Path $LibStatic) -or -not (Test-Path $HeaderApi)) {
    if (Test-Path $DepsDir) { Remove-Item $DepsDir -Recurse -Force }
    New-Item -ItemType Directory -Path $DepsDir -Force | Out-Null

    $TempTar = "$DepsRoot\$Tarball"
    $downloadSuccess = $false

    $candidateFiles = @()
    if (-not [string]::IsNullOrWhiteSpace($LocalFile)) { $candidateFiles += $LocalFile }
    $candidateFiles += $TempTar
    $candidateFiles += "$ScriptDir\$Tarball"
    if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        $candidateFiles += "$env:USERPROFILE\Downloads\$Tarball"
    }

    foreach ($c in $candidateFiles) {
        if ([string]::IsNullOrWhiteSpace($c)) { continue }
        if (Test-TarGz $c 10000000) {
            Write-Host ">>> 检测到有效本地静态依赖包: $c" -ForegroundColor Green
            if ($c -ne $TempTar) { Copy-Item $c $TempTar -Force }
            $downloadSuccess = $true
            break
        } elseif ((Test-Path $c) -and $c -eq $TempTar) {
            Write-Host ">>> 清理损坏缓存文件: $c" -ForegroundColor Yellow
            Remove-Item $c -Force -ErrorAction SilentlyContinue
        }
    }

    if (-not $downloadSuccess) {
        $officialUrl = "https://github.com/Mag1cFall/firefox/releases/download/$SM_VERSION/$Tarball"
        $urlsToTry = @(
            $officialUrl,
            "https://ghproxy.net/$officialUrl",
            "https://gh-proxy.com/$officialUrl",
            "https://mirror.ghproxy.com/$officialUrl"
        )
        foreach ($url in $urlsToTry) {
            Write-Host ">>> 正在下载 SpiderMonkey 静态依赖库: $url" -ForegroundColor Cyan
            if ((Download-File $url $TempTar $EffectiveProxy) -and (Test-TarGz $TempTar 10000000)) {
                $downloadSuccess = $true
                break
            }
            Remove-Item $TempTar -Force -ErrorAction SilentlyContinue
        }
    }

    if (-not $downloadSuccess) {
        Write-Host ""
        Write-Host "错误: 自动下载 SpiderMonkey 静态依赖包失败。" -ForegroundColor Red
        Write-Host "请尝试使用 --Proxy http://127.0.0.1:7890 重试，或手动下载后放入 $DepsRoot\:" -ForegroundColor Yellow
        Write-Host "  https://github.com/Mag1cFall/firefox/releases/download/$SM_VERSION/$Tarball" -ForegroundColor Cyan
        exit 1
    }

    Write-Host ">>> 正在解压 SpiderMonkey 到: $DepsDir" -ForegroundColor Cyan
    if (Get-Command "tar.exe" -ErrorAction SilentlyContinue) {
        & tar.exe -xzf $TempTar -C $DepsDir
    } else {
        tar -xzf $TempTar -C $DepsDir
    }
    Remove-Item $TempTar -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path $LibStatic) -or -not (Test-Path $HeaderApi)) {
        Write-Host "错误: 解压后未找到关键静态库或头文件: $DepsDir" -ForegroundColor Red
        exit 1
    }
}

$LibJsRust = "$DepsDir\lib\libjsrust.a"
if ($zigExe -and (Test-Path $LibJsRust)) {
    $arList = & zig ar t $LibJsRust 2>$null
    if ($arList -match "compiler_builtins-37a08fb9d355cb46\.compiler_builtins\.7e37c3da72dd266-cgu\.150\.rcgu\.o") {
        Write-Host ">>> 清理 libjsrust.a 中与 Zig compiler_rt 冲突的符号..." -ForegroundColor Cyan
        & zig ar d $LibJsRust compiler_builtins-37a08fb9d355cb46.compiler_builtins.7e37c3da72dd266-cgu.150.rcgu.o 2>$null
    }
}

# 4. 配置 CGO 并执行测试或构建 (单引号包裹路径以兼容含空格目录)
$env:CGO_ENABLED = "1"
$env:GOOS = "windows"
$env:GOARCH = "amd64"
$env:CC = $CC
$env:CXX = $CXX
$IncludePath = "$DepsDir\include" -replace '\\', '/'
$LibPath = "$DepsDir\lib" -replace '\\', '/'
$env:CGO_CXXFLAGS = "'-I$IncludePath'"
$env:CGO_LDFLAGS = "'-L$LibPath'"

if ($Test) {
    Write-Host ">>> 正在运行 Go 单元测试..." -ForegroundColor Cyan
    go test -tags "netgo,osusergo" ./cmd/waa-signer ./internal/aistudio
    exit $LASTEXITCODE
}

if (-not (Test-Path $BinDir)) {
    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
}

Write-Host ">>> 开始编译 waa-signer.exe (CGO_ENABLED=1 GOOS=windows GOARCH=amd64)..." -ForegroundColor Cyan
$ldflags = "-s -w"
if (-not [string]::IsNullOrWhiteSpace($ExtLdFlags)) {
    $ldflags += " -extldflags `"$ExtLdFlags`""
}

$TmpExe = "$OutExe.tmp"
Remove-Item $TmpExe -Force -ErrorAction SilentlyContinue
& go build -buildvcs=false -trimpath -tags "netgo,osusergo" -ldflags "$ldflags" -o $TmpExe ./cmd/waa-signer
if ($LASTEXITCODE -ne 0) {
    Remove-Item $TmpExe -Force -ErrorAction SilentlyContinue
    Write-Host "错误: 编译 waa-signer.exe 失败！" -ForegroundColor Red
    exit $LASTEXITCODE
}
Move-Item -Path $TmpExe -Destination $OutExe -Force

& $OutExe -check >$null 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "错误: 编译产物自检失败 ($OutExe -check)" -ForegroundColor Red
    exit 1
}

Write-Host ">>> 编译并自检成功: $OutExe" -ForegroundColor Green

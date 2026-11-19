@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist "%~dp0.venv\Scripts\python.exe" (
    echo [aistudio-api] 检测到尚未初始化环境，正在自动运行安装...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1"
    if errorlevel 1 (
        pause
        exit /b 1
    )
)
if not exist "%~dp0waa-signer\bin\waa-signer.exe" (
    echo [aistudio-api] 检测到本地 WAA 签名器缺失，正在自动构建...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1"
    if errorlevel 1 (
        pause
        exit /b 1
    )
)

set "PORT_ARG=%AISTUDIO_PORT%"
if "%PORT_ARG%"=="" set "PORT_ARG=8080"

echo [aistudio-api] 正在启动服务 (http://localhost:%PORT_ARG%)...
uv run --no-sync python main.py server --port %PORT_ARG% %*
set "EXIT_CODE=%ERRORLEVEL%"

if %EXIT_CODE% neq 0 (
    echo.
    echo [aistudio-api] 服务异常退出 (exit code %EXIT_CODE%)。
    pause
    exit /b %EXIT_CODE%
)
exit /b 0

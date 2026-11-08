@echo off
setlocal
cd /d "%~dp0"

echo [waa-signer] 启动 Windows 编译流程...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
if errorlevel 1 (
    echo [waa-signer] 编译失败，请检查上方错误提示。
    exit /b %errorlevel%
)

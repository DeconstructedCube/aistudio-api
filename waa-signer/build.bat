@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo [waa-signer] Starting Windows build...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

if %EXIT_CODE% neq 0 (
    echo.
    echo [waa-signer] Build finished with exit code %EXIT_CODE%.
    exit /b %EXIT_CODE%
)
exit /b 0

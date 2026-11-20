@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
chcp 65001 >nul 2>&1

echo [aistudio-api] Starting Windows installation and build...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

if %EXIT_CODE% neq 0 (
    echo.
    echo [aistudio-api] Installation failed with exit code %EXIT_CODE%.
    pause
    exit /b %EXIT_CODE%
)
if "%~1"=="" pause
exit /b 0

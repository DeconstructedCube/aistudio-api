@echo off
setlocal
cd /d "%~dp0.."
echo ==========================================================
echo   Installing CloakBrowser for Windows x64...
echo ==========================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-browser.ps1" %*
if %ERRORLEVEL% NEQ 0 (
    echo [install-browser] Installation failed with error code %ERRORLEVEL%.
    exit /b %ERRORLEVEL%
)
endlocal

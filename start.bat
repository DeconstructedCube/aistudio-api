@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
chcp 65001 >nul 2>&1

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "SIGNER_EXE=%~dp0waa-signer\bin\waa-signer.exe"
set "NEED_INSTALL=0"

if not exist "%VENV_PY%" (
    echo [aistudio-api] Python virtual environment not found, running installer...
    set "NEED_INSTALL=1"
)

if "%NEED_INSTALL%"=="0" (
    if not exist "%SIGNER_EXE%" (
        echo [aistudio-api] Local waa-signer.exe not found, running installer...
        set "NEED_INSTALL=1"
    ) else (
        "%SIGNER_EXE%" -check 2>nul | findstr /C:"\"mode\":\"ipc\"" >nul
        if errorlevel 1 (
            echo [aistudio-api] Existing waa-signer.exe is outdated or invalid, rebuilding...
            set "NEED_INSTALL=1"
        )
    )
)

if "%NEED_INSTALL%"=="1" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1"
    if errorlevel 1 (
        echo.
        echo [aistudio-api] Auto-setup failed. Please inspect the error above.
        pause
        exit /b 1
    )
)

if not exist "%VENV_PY%" (
    echo [aistudio-api] ERROR: .venv\Scripts\python.exe is still missing.
    pause
    exit /b 1
)

set "PORT_ARG=%AISTUDIO_PORT%"
if "%PORT_ARG%"=="" set "PORT_ARG=8080"
set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"

echo [aistudio-api] Starting server at http://localhost:%PORT_ARG% ...
"%VENV_PY%" "%~dp0main.py" server --port %PORT_ARG% %*
set "EXIT_CODE=%ERRORLEVEL%"

if %EXIT_CODE% neq 0 (
    echo.
    echo [aistudio-api] Server exited unexpectedly with code %EXIT_CODE%.
    pause
    exit /b %EXIT_CODE%
)
exit /b 0

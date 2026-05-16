@echo off
REM ============================================================
REM  Trade Relay - Windows Electron build script
REM  Usage: run scripts\build_windows.bat from cmd
REM ============================================================

setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0\.."

set "VENV_DIR=.venv"
set "PYTHON_EXE="
set "ENV_FILE=.env.production"
set "NPM_INSTALL_CMD="
set "NPM_INSTALL_LABEL="

if exist "%ENV_FILE%" (
    echo [0/5] Loading proxy-related variables from %ENV_FILE%...
    for /f "usebackq tokens=1,* delims==" %%A in (`findstr /r /v "^[ ]*# ^[ ]*$" "%ENV_FILE%"`) do (
        set "ENV_KEY=%%A"
        set "ENV_VALUE=%%B"
        if /i "!ENV_KEY!"=="HTTP_PROXY" set "HTTP_PROXY=!ENV_VALUE!"
        if /i "!ENV_KEY!"=="HTTPS_PROXY" set "HTTPS_PROXY=!ENV_VALUE!"
        if /i "!ENV_KEY!"=="ALL_PROXY" set "ALL_PROXY=!ENV_VALUE!"
        if /i "!ENV_KEY!"=="PROXY" set "PROXY=!ENV_VALUE!"
        if /i "!ENV_KEY!"=="npm_config_proxy" set "npm_config_proxy=!ENV_VALUE!"
        if /i "!ENV_KEY!"=="npm_config_https_proxy" set "npm_config_https_proxy=!ENV_VALUE!"
    )
)

if not defined HTTPS_PROXY if defined HTTP_PROXY set "HTTPS_PROXY=%HTTP_PROXY%"
if not defined npm_config_proxy if defined HTTP_PROXY set "npm_config_proxy=%HTTP_PROXY%"
if not defined npm_config_https_proxy if defined HTTPS_PROXY set "npm_config_https_proxy=%HTTPS_PROXY%"
if not defined ELECTRON_GET_USE_PROXY if defined npm_config_https_proxy set "ELECTRON_GET_USE_PROXY=true"
if not defined ELECTRON_MIRROR set "ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/"
if not defined ELECTRON_BUILDER_BINARIES_MIRROR set "ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/"
set "npm_config_fetch_retries=5"
set "npm_config_fetch_retry_factor=2"
set "npm_config_fetch_retry_mintimeout=20000"
set "npm_config_fetch_retry_maxtimeout=120000"

if defined npm_config_proxy echo       npm proxy      = %npm_config_proxy%
if defined npm_config_https_proxy echo       npm https proxy = %npm_config_https_proxy%
if defined ELECTRON_MIRROR echo       electron mirror = %ELECTRON_MIRROR%
if defined ELECTRON_GET_USE_PROXY echo       electron proxy  = %ELECTRON_GET_USE_PROXY%

echo [1/5] Detecting Python...
where python >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_EXE=python"
) else (
    where py >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=py -3"
    )
)

if "%PYTHON_EXE%"=="" (
    echo ERROR: Python 3.11 or newer was not found.
    pause
    exit /b 1
)

echo [2/5] Preparing virtual environment...
if not exist "%VENV_DIR%\Scripts\python.exe" (
    call %PYTHON_EXE% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo       Virtual environment created.
) else (
    echo       Virtual environment already exists.
)

echo [3/5] Installing Python dependencies...
call "%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo ERROR: Failed to upgrade pip.
    pause
    exit /b 1
)

call "%VENV_DIR%\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install Python requirements.
    pause
    exit /b 1
)

echo [4/5] Syncing Node.js dependencies for Electron build...
echo       Reinstalling root dependencies to ensure new runtime modules are packaged.
echo       This keeps node_modules aligned with package-lock.json on reused Windows build machines.
if exist "package-lock.json" (
    set "NPM_INSTALL_CMD=npm ci --include=dev --no-fund --no-audit --progress=false --loglevel=info"
    set "NPM_INSTALL_LABEL=npm ci"
) else (
    set "NPM_INSTALL_CMD=npm install --include=dev --no-fund --no-audit --progress=false --loglevel=info"
    set "NPM_INSTALL_LABEL=npm install"
)

set "NPM_INSTALL_OK="
for /l %%I in (1,1,3) do (
    echo       Running !NPM_INSTALL_LABEL! ^(attempt %%I/3^)...
    call !NPM_INSTALL_CMD!
    if not errorlevel 1 (
        set "NPM_INSTALL_OK=1"
        goto :npm_install_done
    )
    echo WARNING: !NPM_INSTALL_LABEL! failed on attempt %%I/3.
)

:npm_install_done
if not defined NPM_INSTALL_OK (
    echo ERROR: !NPM_INSTALL_LABEL! failed after 3 attempts.
    pause
    exit /b 1
)

if not exist "node_modules\proxy-agent" (
    echo ERROR: proxy-agent was not installed into node_modules.
    pause
    exit /b 1
)

if not exist "node_modules\.bin\electron-builder.cmd" (
    echo ERROR: electron-builder was not installed into node_modules.
    pause
    exit /b 1
)

echo       Cleaning previous Windows artifacts...
for %%F in (
    "dist-electron\Trade Relay 1.0.0.exe"
    "dist-electron\Trade Relay 1.0.0.exe.blockmap"
    "dist-electron\Trade Relay Setup 1.0.0.exe"
    "dist-electron\Trade Relay Setup 1.0.0.exe.blockmap"
) do (
    if exist %%~F (
        del /f /q %%~F >nul 2>&1
    )
)

for %%P in (
    "Trade Relay.exe"
    "Trade Relay 1.0.0.exe"
    "Trade Relay Setup 1.0.0.exe"
) do (
    taskkill /f /im %%~P >nul 2>&1
)

echo [5/5] Building Windows Electron package...
call npm run build:win
if errorlevel 1 (
    echo ERROR: Electron Windows build failed.
    pause
    exit /b 1
)

echo Done.
echo Electron output: dist-electron\
echo Backend run command:
echo   %VENV_DIR%\Scripts\python.exe main.py --reload
pause

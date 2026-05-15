@echo off
REM ============================================================
REM  Trade Relay - Windows frontend build script
REM  Usage: run scripts\build_frontend_windows.bat from cmd
REM ============================================================

setlocal
set ELECTRON_SKIP_BINARY_DOWNLOAD=1
set npm_config_electron_skip_binary_download=true

cd /d "%~dp0\.."

echo [1/3] Checking Node.js...
where node >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js was not found. Please install Node.js 18 or newer.
    pause
    exit /b 1
)

echo [2/3] Checking frontend dependencies...
if not exist "node_modules\.bin\vite.cmd" (
    echo       vite was not found in node_modules.
    echo       Installing dependencies with visible logs. Electron binary download is disabled for frontend-only builds.
    if exist "package-lock.json" (
        call npm ci --include=dev --no-fund --no-audit --progress=false --loglevel=info
        if errorlevel 1 (
            echo ERROR: npm ci failed.
            pause
            exit /b 1
        )
    ) else (
        call npm install --include=dev --no-fund --no-audit --progress=false --loglevel=info
        if errorlevel 1 (
            echo ERROR: npm install --include=dev failed.
            pause
            exit /b 1
        )
    )
) else (
    echo       vite is available. Skipping npm install.
)

echo [3/3] Building frontend assets...
call npm run build:frontend
if errorlevel 1 (
    echo ERROR: Frontend build failed.
    pause
    exit /b 1
)

echo Done.
echo Frontend output: dist\
pause
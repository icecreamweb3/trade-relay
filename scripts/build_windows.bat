@echo off
REM ============================================================
REM  Trade Relay - Windows Electron build script
REM  Usage: run scripts\build_windows.bat from cmd
REM ============================================================

setlocal
cd /d "%~dp0\.."

set "VENV_DIR=.venv"
set "PYTHON_EXE="

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

echo [4/5] Installing Node.js dependencies for Electron build...
if not exist "node_modules\.bin\electron-builder.cmd" (
    echo       electron-builder was not found in node_modules.
    echo       Installing dependencies with visible logs. Electron download may take a few minutes...
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
            echo ERROR: npm install failed.
            pause
            exit /b 1
        )
    )
) else (
    echo       Electron build dependencies already exist.
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

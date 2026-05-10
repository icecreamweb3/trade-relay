@echo off
REM ============================================================
REM  Trade Relay — Windows 环境初始化 + 打包脚本
REM  用法：双击运行或在命令行执行 build_windows.bat
REM ============================================================
chcp 65001

set VENV_DIR=.venv

echo [1/5] 检查 Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误：未找到 python，请先安装 Python 3.11+
    pause
    exit /b 1
)

echo [2/5] 创建虚拟环境 %VENV_DIR%...
if not exist "%VENV_DIR%\Scripts\activate.bat" (
    python -m venv %VENV_DIR%
    echo       虚拟环境已创建
) else (
    echo       虚拟环境已存在，跳过创建
)

echo [3/5] 安装依赖...
%VENV_DIR%\Scripts\pip install --upgrade pip -q
%VENV_DIR%\Scripts\pip install -r requirements.txt pyinstaller

echo [4/5] 打包可执行文件...
%VENV_DIR%\Scripts\pyinstaller trade_relay.spec --clean

echo [5/5] 完成！
echo 可执行文件位于 dist\TradeRelay\TradeRelay.exe
echo.
echo 直接运行程序（不打包）：
echo   %VENV_DIR%\Scripts\python main.py
pause

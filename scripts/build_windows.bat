@echo off
REM ============================================================
REM  Trade Relay — Windows 环境初始化 + Electron 打包脚本
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

echo [3/5] 安装 Python 依赖...
%VENV_DIR%\Scripts\pip install --upgrade pip -q
%VENV_DIR%\Scripts\pip install -r requirements.txt

echo [4/5] 安装 Node.js 依赖...
call npm install

echo [5/5] 打包 Electron Windows 应用...
call npm run build:win

echo 完成！
echo Electron 安装包位于 dist-electron\
echo.
echo 本地启动后端：
echo   %VENV_DIR%\Scripts\python main.py --reload
pause

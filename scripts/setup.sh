#!/usr/bin/env bash
# ============================================================
#  Trade Relay — Linux/macOS 环境初始化脚本
#  用法：bash setup.sh
# ============================================================
set -e

VENV_DIR=".venv"

echo "[1/4] 检查 Python 版本..."
PYTHON=$(command -v python3 || command -v python)
if [ -z "$PYTHON" ]; then
    echo "错误：未找到 python3，请先安装 Python 3.11+" >&2
    exit 1
fi

PY_VER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "      使用 Python $PY_VER  ($PYTHON)"

echo "[2/4] 创建虚拟环境 $VENV_DIR ..."
if [ ! -d "$VENV_DIR" ]; then
    $PYTHON -m venv "$VENV_DIR"
    echo "      虚拟环境已创建"
else
    echo "      虚拟环境已存在，跳过创建"
fi

echo "[3/4] 安装依赖..."
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r requirements.txt

echo "[4/4] 完成！"
echo ""
echo "  激活虚拟环境："
echo "    source $VENV_DIR/bin/activate"
echo ""
echo "  启动程序："
echo "    $VENV_DIR/bin/python main.py --reload"
echo "  或激活后直接运行："
echo "    python3 main.py --reload"

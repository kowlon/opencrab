#!/usr/bin/env bash
#
# setup_venv.sh - 创建并初始化 mogox-parking-availability 的 Python 虚拟环境
#
# 用法:
#   bash scripts/setup_venv.sh
#
# 说明:
#   1. 在 .venv/ 下创建 Python3 虚拟环境
#   2. 激活虚拟环境后安装 requirements.txt 中的所有依赖
#   3. 如果虚拟环境已存在且依赖已安装，则跳过（幂等操作）
#
# 此脚本应在首次使用 mogox-parking-availability 技能时执行一次。

set -euo pipefail

# ── 定位 skill 根目录 ────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="${SKILL_DIR}/.venv"
REQUIREMENTS="${SKILL_DIR}/requirements.txt"

echo "=== mogox-parking-availability 虚拟环境初始化 ==="
echo "Skill 目录: ${SKILL_DIR}"
echo "虚拟环境:   ${VENV_DIR}"

# ── 检查 Python3 ─────────────────────────────────────────────
if ! command -v python3 &> /dev/null; then
    echo "错误: python3 未安装"
    echo "  macOS: brew install python3"
    echo "  Ubuntu: sudo apt install python3 python3-venv"
    echo "  Windows: 从 python.org 下载安装"
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1)
echo "Python 版本: ${PYTHON_VERSION}"

# ── 检查 venv 模块 ───────────────────────────────────────────
if ! python3 -c "import venv" 2>/dev/null; then
    echo "错误: Python venv 模块不可用"
    echo "  Ubuntu: sudo apt install python3-venv"
    exit 1
fi

# ── 创建虚拟环境（如不存在）──────────────────────────────────
if [ ! -d "${VENV_DIR}" ]; then
    echo ""
    echo "→ 创建虚拟环境..."
    python3 -m venv "${VENV_DIR}"
    echo "  ✓ 虚拟环境已创建"
else
    echo ""
    echo "→ 虚拟环境已存在，跳过创建"
fi

# ── 激活虚拟环境 ─────────────────────────────────────────────
source "${VENV_DIR}/bin/activate"
echo "→ 已激活虚拟环境: $(which python3)"

# ── 配置清华 pip 源 ──────────────────────────────────────────
echo ""
echo "→ 配置清华 pip 镜像源..."
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
echo "  ✓ pip 源已设置为清华镜像"

# ── 升级 pip ─────────────────────────────────────────────────
echo ""
echo "→ 升级 pip..."
pip install --upgrade pip --quiet

# ── 安装依赖 ─────────────────────────────────────────────────
if [ -f "${REQUIREMENTS}" ]; then
    echo "→ 安装依赖: ${REQUIREMENTS}"
    pip install -r "${REQUIREMENTS}"
    echo ""
    echo "  ✓ 依赖安装完成"
else
    echo "警告: requirements.txt 不存在: ${REQUIREMENTS}"
fi

# ── 验证关键依赖 ─────────────────────────────────────────────
echo ""
echo "→ 验证关键依赖..."
MISSING=0

if python3 -c "import requests" 2>/dev/null; then
    echo "  ✓ requests"
else
    echo "  ✗ requests — 未安装"
    MISSING=$((MISSING + 1))
fi

if [ $MISSING -gt 0 ]; then
    echo ""
    echo "警告: 有 ${MISSING} 个依赖未正确安装"
fi

# ── 完成 ─────────────────────────────────────────────────────
echo ""
echo "========================================="
echo "✓ mogox-parking-availability 虚拟环境初始化完成！"
echo ""
echo "虚拟环境路径: ${VENV_DIR}"
echo "Python 路径:  ${VENV_DIR}/bin/python3"
echo "Pip 路径:     ${VENV_DIR}/bin/pip"
echo ""
echo "后续执行 Python 脚本请使用:"
echo "  bash scripts/run_in_venv.sh python scripts/query_parking.py [args...]"
echo "========================================="

#!/usr/bin/env bash
#
# run_in_venv.sh - 在 mogox-parking-availability 虚拟环境中执行命令
#
# 用法:
#   bash scripts/run_in_venv.sh <command> [args...]
#
# 示例:
#   bash scripts/run_in_venv.sh python scripts/query_parking.py --help
#   bash scripts/run_in_venv.sh python scripts/select_best_match.py --candidates data.json --user-query "停车场" --output result.json
#   bash scripts/run_in_venv.sh pip list
#
# 说明:
#   此脚本会自动激活 .venv 虚拟环境后执行传入的命令。
#   如果虚拟环境不存在，会提示先运行 setup_venv.sh。

set -euo pipefail

# ── 定位 skill 根目录和虚拟环境 ──────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="${SKILL_DIR}/.venv"

# ── 检查虚拟环境是否存在 ─────────────────────────────────────
if [ ! -d "${VENV_DIR}" ]; then
    echo "错误: 虚拟环境不存在: ${VENV_DIR}"
    echo ""
    echo "请先运行初始化脚本:"
    echo "  bash scripts/setup_venv.sh"
    exit 1
fi

if [ ! -f "${VENV_DIR}/bin/activate" ]; then
    echo "错误: 虚拟环境损坏（缺少 activate 脚本）"
    echo ""
    echo "请删除后重新创建:"
    echo "  rm -rf ${VENV_DIR}"
    echo "  bash scripts/setup_venv.sh"
    exit 1
fi

# ── 检查是否传入了命令 ───────────────────────────────────────
if [ $# -eq 0 ]; then
    echo "用法: $0 <command> [args...]"
    echo ""
    echo "示例:"
    echo "  $0 python scripts/query_parking.py --help"
    echo "  $0 python scripts/select_best_match.py --candidates data.json --user-query \"停车场\" --output result.json"
    echo "  $0 pip list"
    exit 1
fi

# ── 激活虚拟环境并执行命令 ───────────────────────────────────
source "${VENV_DIR}/bin/activate"
exec "$@"

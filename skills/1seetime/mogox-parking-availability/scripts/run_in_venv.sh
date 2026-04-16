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
#   优先使用现有虚拟环境，如果不存在则自动创建。
#   如果执行失败，会自动重新安装依赖并重试。

set -euo pipefail

# ── 定位 skill 根目录和虚拟环境 ──────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="${SKILL_DIR}/.venv"

# ── 检查虚拟环境是否存在 ─────────────────────────────────────
if [ ! -d "${VENV_DIR}" ]; then
    echo "虚拟环境不存在: ${VENV_DIR}"
    echo "→ 自动创建并安装依赖..."
    bash "${SCRIPT_DIR}/setup_venv.sh"
elif [ ! -f "${VENV_DIR}/bin/activate" ]; then
    echo "虚拟环境损坏（缺少 activate 脚本）"
    echo "→ 重新创建虚拟环境..."
    rm -rf "${VENV_DIR}"
    bash "${SCRIPT_DIR}/setup_venv.sh"
else
    echo "→ 使用现有虚拟环境: ${VENV_DIR}"
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

# 固定在技能根目录执行，确保相对路径（如 ../../../data/skills_result）
# 在不同调用环境下都能解析到同一目标目录。
cd "${SKILL_DIR}"

# ── 执行命令，失败时自动重新安装依赖并重试 ──────────────────
exec "$@" 2>&1
RESULT=$?

if [ $RESULT -ne 0 ]; then
    echo ""
    echo "命令执行失败（退出码: ${RESULT}），尝试重新安装依赖..."
    bash "${SCRIPT_DIR}/setup_venv.sh"
    echo ""
    echo "→ 重新执行命令..."
    source "${VENV_DIR}/bin/activate"
    exec "$@"
fi

exit $RESULT

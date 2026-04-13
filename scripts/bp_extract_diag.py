"""诊断 _extract_input_from_query 对不同时间格式的行为差异。

目的：验证"中文日期格式会导致 keyword 等无关字段一并提取失败"的假设。

做法：
  1. 本地加载 best_practice/camera-frame-search/config.yaml
  2. 复刻 _build_combined_user_schema 生成提取用 schema
  3. 复刻 _extract_input_from_query 的 prompt 构造逻辑
  4. 直接用 Brain.think_lightweight 调 compiler 端点
  5. 对每条候选 query 打印:
     - 实际发送的 prompt（仅第一条打印，避免刷屏）
     - compiler 的原始响应
     - BPEngine._parse_output 解析后的 JSON

不启动 API 服务，不修改任何状态。只做只读的 LLM 调用。

用法：
    python scripts/bp_extract_diag.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from seeagent.bestpractice.config.loader import BPConfigLoader
from seeagent.bestpractice.engine import BPEngine
from seeagent.bestpractice.models import collect_all_properties, collect_all_upstream
from seeagent.core.brain import Brain

logging.basicConfig(level=logging.WARNING)

BP_ROOT = REPO_ROOT / "best_practice"
BP_ID = "camera-frame-search"


# ─── 由你决定：想要对比哪些 query 变体？─────────────────────────
# TODO: 扩展这个列表。脚本会对每一条走一遍完整的提取流程并打印结果。
#
# 保留前两条作为与截图对齐的基准对照，剩下的是你来补：
#   - 想看 compiler 在哪些边界情况下会崩？
#   - 例如：纯中文口语("昨天下午三点到四点")、带时区、只有结束时间、
#     中英混合、省略秒、用"~"代替"—"、不同的地址粒度（街道/小区/门牌）...
#   - label 会出现在每段输出的标题行，写短一点便于阅读
#
# 签名：list[tuple[str, str]]  # (label, query)
TEST_QUERIES: list[tuple[str, str]] = [
    (
        "中文日期 (截图失败案例)",
        "在2023年12月9日 15:03:01—2023年12月9日 15:04:01之间，"
        "衡州大道上找一辆黑色小汽车的行驶轨迹",
    ),
    (
        "ISO8601 (截图成功案例)",
        "在2023-12-09T15:03:01—2023-12-09T15:04:01之间，"
        "衡州大道上找一辆黑色小汽车的行驶轨迹",
    ),
    # TODO: 在此加入你想验证的边界情况
]


def build_combined_schema(bp_config) -> dict | None:
    """复刻 bestpractice.py:711 的 _build_combined_user_schema。"""
    combined_props: dict = {}
    for subtask in bp_config.subtasks:
        schema = subtask.input_schema
        if not schema:
            continue
        upstream = collect_all_upstream(schema)
        for name, info in collect_all_properties(schema).items():
            if name not in upstream:
                combined_props.setdefault(name, info)
    return (
        {"type": "object", "properties": combined_props} if combined_props else None
    )


def build_extraction_prompt(user_query: str, schema: dict) -> str:
    """复刻 seecrab.py:170-220 _extract_input_from_query 的 prompt 构造。"""
    branches = schema.get("oneOf") or schema.get("anyOf")
    is_multi_branch = bool(branches)
    if not is_multi_branch:
        branches = [schema]

    branch_desc_list: list[str] = []
    for idx, branch in enumerate(branches):
        props = branch.get("properties", {})
        if not props:
            continue
        fields = "\n".join(
            f"- {name}: {info.get('description', '无描述')} "
            f"(type: {info.get('type', 'string')})"
            for name, info in props.items()
        )
        if is_multi_branch:
            title = branch.get("title", f"分支 {idx + 1}")
            desc = branch.get("description", "无描述")
            branch_desc_list.append(
                f"### {title}\n描述：{desc}\n字段定义：\n{fields}"
            )
        else:
            branch_desc_list.append(fields)

    all_branches_desc = "\n\n".join(branch_desc_list)

    if is_multi_branch:
        instruction = (
            "分析以下对话上下文，判断其符合哪一种意图分支，并仅提取该分支下定义的字段。"
        )
        schema_section = f"## 可选意图分支\n{all_branches_desc}"
    else:
        instruction = "从以下对话上下文中提取所需的字段。"
        schema_section = f"## 字段定义\n{all_branches_desc}"

    return (
        f"{instruction}\n"
        "输出一个 JSON 对象。只提取明确提到或可推断的字段，没有提到的字段不要包含。\n"
        "只输出 JSON，不要其他文字。\n\n"
        f"{schema_section}\n\n"
        f"## 对话上下文\n[用户]: {user_query}"
    )


def print_separator(title: str) -> None:
    print("=" * 80)
    print(f"▶ {title}")
    print("=" * 80)


async def diagnose_one(
    brain: Brain, label: str, user_query: str, prompt: str, runs: int = 5
) -> None:
    print_separator(f"{label}  ×{runs}")
    print(f"[原始 query]\n{user_query}\n")

    expected_core = {"keyword", "query", "start_time", "end_time", "feature_text"}
    histogram: dict[frozenset, int] = {}

    for i in range(runs):
        try:
            resp = await brain.think_lightweight(prompt, max_tokens=512)
        except Exception as e:
            print(f"  run#{i + 1} 调用失败: {e}")
            continue

        raw = resp.content if hasattr(resp, "content") else str(resp)
        parsed = BPEngine._parse_output(raw)
        if isinstance(parsed, dict) and "_raw_output" not in parsed:
            keys = frozenset(parsed.keys())
        else:
            keys = frozenset(["__PARSE_FAIL__"])
        histogram[keys] = histogram.get(keys, 0) + 1

        core_hit = sorted(keys & expected_core) if "__PARSE_FAIL__" not in keys else []
        print(f"  run#{i + 1}: keys={sorted(keys)}  核心命中={core_hit}")

    print()
    print("[提取结果分布]")
    for keys, count in sorted(histogram.items(), key=lambda kv: -kv[1]):
        marker = ""
        if "keyword" in keys:
            marker = "  ← 能正确走 POI 分支"
        elif "query" in keys:
            marker = "  ← 能正确走语义分支"
        elif "region" in keys and "keyword" not in keys:
            marker = "  ← 会进 bp_ask_user(缺 keyword)"
        print(f"  ×{count}  {sorted(keys)}{marker}")
    print()


async def main() -> None:
    loader = BPConfigLoader(search_paths=[BP_ROOT])
    loader.load_all()
    bp_config = loader.configs.get(BP_ID)
    if not bp_config:
        raise SystemExit(f"未找到 BP 配置: {BP_ID}，检查 {BP_ROOT}")

    schema = build_combined_schema(bp_config)
    if not schema:
        raise SystemExit("combined_schema 为空，无法诊断")

    print_separator("combined_schema 字段一览")
    for name, info in schema["properties"].items():
        desc = (info.get("description", "") or "").replace("\n", " ")
        print(f"  - {name} ({info.get('type', 'string')}): {desc[:80]}")
    print()

    brain = Brain()
    using_compiler = brain._compiler_available()
    print_separator("端点状态")
    if using_compiler:
        print("使用 compiler 端点（小模型，与生产路径一致）")
    else:
        print("compiler 端点不可用，think_lightweight 会回退到主模型")
        print("⚠ 若回退到主模型，小模型不稳定的假设将无法被证伪")
    print()

    for i, (label, query) in enumerate(TEST_QUERIES):
        prompt = build_extraction_prompt(query, schema)
        if i == 0:
            print_separator("实际发送的 prompt（仅首条打印）")
            print(prompt)
            print()
        await diagnose_one(brain, label, query, prompt)


if __name__ == "__main__":
    asyncio.run(main())

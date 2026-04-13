"""Lint: BP input_schema descriptions must not contain LLM control-flow instructions.

Background
==========
BP `input_schema` 字段的 `description` 会被 `api/routes/seecrab.py::_extract_input_from_query`
原样塞进 compiler LLM 的 prompt。任何"如果没有提供，则不要提取"这样的控制流指令都会被
LLM 当成真正的提取策略执行，导致 `bp_offer` 阶段的字段提取在边界输入下崩掉。

历史案例：camera-frame-search 的 `keyword` 曾写有「如果没有提供，则不进行提取，走ask_user模式」，
导致 compiler 模型在面对"衡州大道"这类非纯 POI 输入时主动跳过 `keyword`，进而让
`_check_input_completeness` 因 `region` 字段命中 POI 分支而弹出"请告诉我地址关键词"。
10 次采样中 0-30% 成功率，修复后稳定 100%。详见该 BP yaml 的 git 历史。

设计原则
========
schema description 的真正读者有三个：维护者、运行时代码、LLM。每一条 description 都是
隐形 prompt，需要像 prompt 一样审查。控制流（何时提取、何时跳过、何时走 ask_user）
属于代码决策，应当在 `_check_input_completeness` + `required` 里实现，不应出现在 description。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
BP_ROOT = REPO_ROOT / "best_practice"


# ─── 禁用模式 ──────────────────────────────────────────────────────────
# 每一项: (substring, reason)
# 命中规则: 子串命中即视为控制流污染。请保证子串足够特异，避免误伤正常说明。
#
# TODO: 随着踩坑积累往这里加条目。加新条目的判据是:
#       "这句话写在 schema description 里，会让 LLM 误解为'何时提取'的策略吗？"
#       如果是，就加进来。
BANNED_PATTERNS: list[tuple[str, str]] = [
    (
        "如果没有提供",
        "控制流条件写进 description 会让 LLM 主动放弃提取；字段是否缺失应由 "
        "_check_input_completeness + required 判定",
    ),
    (
        "不进行提取",
        "description 不应告诉 LLM 何时'不提取'——那是算法决策",
    ),
    (
        "不要提取",
        "description 不应告诉 LLM 何时'不提取'——那是算法决策",
    ),
    (
        "则跳过",
        "'跳过字段'属于控制流，不应写在字段语义描述里",
    ),
    (
        "ask_user",
        "ask_user 路由是代码决策，不应出现在 description 里——让 LLM 提取所有可推断字段，"
        "路由交给 _check_input_completeness",
    ),
]


# ─── schema 遍历 ──────────────────────────────────────────────────────


def walk_descriptions(schema: dict, path: str = "") -> Iterator[tuple[str, str]]:
    """Yield `(json_path, description)` for every description string in schema tree.

    覆盖 `properties` / `oneOf` / `anyOf` / `allOf` / `items`，与 `collect_all_properties`
    的遍历范围一致，额外支持 array items。
    """
    if not isinstance(schema, dict):
        return
    if isinstance(schema.get("description"), str):
        yield path or "<root>", schema["description"]
    for name, child in (schema.get("properties") or {}).items():
        yield from walk_descriptions(child, f"{path}.{name}" if path else name)
    for branch_key in ("oneOf", "anyOf", "allOf"):
        for i, branch in enumerate(schema.get(branch_key) or []):
            yield from walk_descriptions(branch, f"{path}.{branch_key}[{i}]")
    if isinstance(schema.get("items"), dict):
        yield from walk_descriptions(schema["items"], f"{path}.items")


def iter_bp_configs() -> Iterator[tuple[Path, dict]]:
    assert BP_ROOT.is_dir(), f"best_practice 目录不存在: {BP_ROOT}"
    for config_file in sorted(BP_ROOT.rglob("config.yaml")):
        raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            yield config_file, raw


def find_banned(description: str) -> list[tuple[str, str]]:
    return [(p, r) for p, r in BANNED_PATTERNS if p in description]


# ─── tests ────────────────────────────────────────────────────────────


def test_bp_configs_are_present():
    """保护性检查：确保 lint 真的扫到了 BP 配置，而不是静默通过。"""
    configs = list(iter_bp_configs())
    assert configs, f"未在 {BP_ROOT} 找到任何 BP config.yaml"


def test_no_control_flow_in_input_schema_descriptions():
    """BP input_schema description 不应包含 LLM 控制流指令。

    若失败，参考 camera-frame-search 的 keyword/region 描述修复：
    把控制流挪到 _check_input_completeness + required，description 只描述语义和示例。
    """
    violations: list[str] = []

    for config_file, raw in iter_bp_configs():
        bp_id = raw.get("id") or config_file.parent.name
        for subtask in raw.get("subtasks") or []:
            subtask_id = subtask.get("id", "<unknown>")
            schema = subtask.get("input_schema")
            if not isinstance(schema, dict):
                continue
            for field_path, desc in walk_descriptions(schema):
                for pattern, reason in find_banned(desc):
                    violations.append(
                        f"  {bp_id}/{subtask_id}:{field_path}\n"
                        f"    命中: {pattern!r}\n"
                        f"    原因: {reason}\n"
                        f"    描述: {desc[:120]}"
                    )

    assert not violations, (
        f"BP input_schema 描述里检测到 LLM 控制流污染 ({len(violations)} 处):\n\n"
        + "\n\n".join(violations)
        + "\n\n修复指引: 把控制流逻辑移到 _check_input_completeness / required，"
        "description 只描述字段语义和示例。"
    )

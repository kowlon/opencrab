"""
外部工具类目定义与岗位角色工具预设。

将工具按功能域分组为类目（category），节点的 external_tools 字段
可以混合使用类目名和具体工具名。expand_tool_categories() 负责展开。
"""

from __future__ import annotations


TOOL_CATEGORIES: dict[str, list[str]] = {
    "research": ["web_search", "news_search"],
    "planning": [
        "create_plan", "update_plan_step",
        "get_plan_status", "complete_plan",
    ],
    "filesystem": ["run_shell", "write_file", "read_file", "list_directory"],
    "memory": ["add_memory", "search_memory", "get_memory_stats"],
    "mcp": ["call_mcp_tool", "list_mcp_servers", "get_mcp_instructions"],
    "browser": [
        "browser_task", "browser_open", "browser_navigate",
        "browser_screenshot",
    ],
    "communication": ["deliver_artifacts", "get_chat_history"],
}

ROLE_TOOL_PRESETS: dict[str, list[str]] = {
    "ceo":        ["research", "planning", "memory"],
    "cto":        ["research", "planning", "filesystem", "memory"],
    "cpo":        ["research", "planning", "memory"],
    "cmo":        ["research", "planning", "memory"],
    "cfo":        ["research", "memory"],
    "developer":  ["filesystem", "memory"],
    "engineer":   ["filesystem", "memory"],
    "researcher": ["research", "memory"],
    "writer":     ["research", "filesystem", "memory"],
    "analyst":    ["research", "memory"],
    "designer":   ["browser", "filesystem"],
    "devops":     ["filesystem", "memory"],
    "pm":         ["research", "planning", "memory"],
    "hr":         ["research", "memory"],
    "legal":      ["research", "memory"],
    "seo":        ["research", "memory"],
    "content":    ["research", "filesystem", "memory"],
    "default":    ["research", "memory"],
}

ALL_CATEGORY_NAMES: frozenset[str] = frozenset(TOOL_CATEGORIES.keys())


def expand_tool_categories(entries: list[str] | None) -> set[str]:
    """Expand a mixed list of category names and tool names into a flat set of tool names.

    >>> sorted(expand_tool_categories(["research", "create_plan"]))
    ['create_plan', 'news_search', 'web_search']
    """
    if not entries:
        return set()
    result: set[str] = set()
    for entry in entries:
        if not entry or not entry.strip():
            continue
        if entry in TOOL_CATEGORIES:
            result.update(TOOL_CATEGORIES[entry])
        else:
            result.add(entry)
    return result


_ROLE_KEYWORDS: dict[str, list[str]] = {
    "ceo": ["ceo", "执行官", "总裁"],
    "cto": ["cto", "技术总监"],
    "cpo": ["cpo", "产品总监"],
    "cmo": ["cmo", "市场总监", "营销"],
    "cfo": ["cfo", "财务总监"],
    "developer": ["developer", "dev", "工程师", "开发"],
    "engineer": ["engineer"],
    "researcher": ["researcher", "研究", "调研"],
    "writer": ["writer", "写手", "文案", "编辑"],
    "analyst": ["analyst", "分析"],
    "designer": ["designer", "设计"],
    "devops": ["devops", "运维"],
    "pm": ["pm", "产品经理", "项目经理"],
    "hr": ["hr", "人力", "人事"],
    "legal": ["legal", "法务", "法律"],
    "seo": ["seo"],
    "content": ["content", "运营", "内容"],
}


def get_preset_for_role(role_hint: str) -> list[str]:
    """Match a role hint string to the best preset, returning category names."""
    hint = role_hint.lower()
    for preset_key, keywords in _ROLE_KEYWORDS.items():
        for kw in keywords:
            if kw in hint:
                return list(ROLE_TOOL_PRESETS.get(preset_key, ROLE_TOOL_PRESETS["default"]))
    return list(ROLE_TOOL_PRESETS["default"])


def list_categories() -> list[dict[str, str | list[str]]]:
    """Return category info for frontend display."""
    return [
        {"name": name, "tools": tools}
        for name, tools in TOOL_CATEGORIES.items()
    ]

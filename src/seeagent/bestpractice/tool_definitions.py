"""BP 工具定义 — 注册到 ToolCatalog 的 6 个 BP 工具。

格式遵循 tool-definition-spec.md 规范，使用 input_schema（非 parameters）。
"""

from __future__ import annotations

BP_TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "bp_start",
        "category": "Best Practice",
        "description": "启动一个最佳实践 (Best Practice) 任务流程",
        "input_schema": {
            "type": "object",
            "properties": {
                "bp_id": {
                    "type": "string",
                    "description": "最佳实践模板 ID",
                },
                "input_data": {
                    "type": "object",
                    "description": "初始输入数据 (包含所有子任务需要的用户输入字段，必须尽可能从用户的自然语言描述中提取，不限于第一个子任务)",
                },
                "run_mode": {
                    "type": "string",
                    "enum": ["manual", "auto"],
                    "description": "执行模式: manual=手动确认每步, auto=自动执行",
                },
            },
            "required": ["bp_id"],
        },
    },
    {
        "name": "bp_edit_output",
        "category": "Best Practice",
        "description": "修改子任务输入、输出或最终输出 (Chat-to-Edit 模式)",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "BP 实例 ID (可选)",
                },
                "subtask_id": {
                    "type": "string",
                    "description": "要修改的子任务 ID；当 target_type=final_output 时可省略",
                },
                "target_type": {
                    "type": "string",
                    "enum": ["input", "output", "final_output"],
                    "description": "编辑目标类型: input=子任务输入, output=子任务输出, final_output=最终输出",
                },
                "changes": {
                    "type": "object",
                    "description": "要合并的修改内容 (深度合并，数组完整替换)",
                },
            },
            "required": ["changes"],
        },
    },
    {
        "name": "bp_switch_task",
        "category": "Best Practice",
        "description": "切换到另一个 BP 实例 (暂停当前任务，恢复目标任务)",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_instance_id": {
                    "type": "string",
                    "description": "要切换到的 BP 实例 ID",
                },
            },
            "required": ["target_instance_id"],
        },
    },
    {
        "name": "bp_next",
        "category": "Best Practice",
        "description": "执行下一个子任务（继续最佳实践流程）",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "BP 实例 ID (可选，默认当前活跃实例)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "bp_answer",
        "category": "Best Practice",
        "description": "补充子任务缺失的输入参数（响应 bp_ask_user）",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "BP 实例 ID (可选)",
                },
                "subtask_id": {
                    "type": "string",
                    "description": "等待输入的子任务 ID",
                },
                "data": {
                    "type": "object",
                    "description": "补充的参数数据 (字段名→值)",
                },
            },
            "required": ["subtask_id", "data"],
        },
    },
    {
        "name": "bp_cancel",
        "category": "Best Practice",
        "description": "取消当前最佳实践任务",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "BP 实例 ID (可选，默认当前活跃实例)",
                },
            },
            "required": [],
        },
    },
]


def get_bp_tool_names() -> list[str]:
    """返回所有 BP 工具名称。"""
    return [t["name"] for t in BP_TOOL_DEFINITIONS]

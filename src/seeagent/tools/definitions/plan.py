"""
Plan 模式工具定义

包含任务计划管理相关的工具：
- create_plan: 创建任务执行计划
- update_plan_step: 更新步骤状态
- get_plan_status: 获取计划执行状态
- complete_plan: 完成计划
"""

PLAN_TOOLS = [
    {
        "name": "create_plan",
        "category": "Plan",
        "description": "⚠️ MUST CALL FIRST for multi-step tasks! If user request needs 2+ tool calls (like 'open + search + screenshot'), call create_plan BEFORE any other tool. Examples: '打开百度搜索天气截图' → create_plan first!",
        "detail": """创建任务执行计划。

**何时使用**：
- 任务需要超过 2 步完成时
- 用户请求中有"然后"、"接着"、"之后"等词
- 涉及多个工具协作

**使用流程**：
1. create_plan → 2. 执行步骤 → 3. update_plan_step → 4. ... → 5. complete_plan

**步骤字段说明**：
- `id` + `description`: 必填
- `tool`: 可选，预计使用的工具名
- `skills`: 可选，关联的技能名称列表（用于追踪）
- `depends_on`: 可选，前置依赖步骤

**示例**：
用户："打开百度搜索天气并截图发我"
→ create_plan(steps=[打开百度, 输入关键词, 点击搜索, 截图, 发送])""",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_summary": {"type": "string", "description": "任务的一句话总结"},
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "步骤ID，如 step_1, step_2"},
                            "description": {"type": "string", "description": "步骤描述"},
                            "tool": {"type": "string", "description": "预计使用的工具（可选）"},
                            "skills": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "本步骤关联的 skill 名称列表（可选，用于追踪）",
                            },
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "依赖的步骤ID（可选）",
                            },
                        },
                        "required": ["id", "description"],
                    },
                    "description": "步骤列表",
                },
            },
            "required": ["task_summary", "steps"],
        },
    },
    {
        "name": "update_plan_step",
        "category": "Plan",
        "description": "Update the status of a plan step. MUST call after completing each step to track progress.",
        "detail": """更新计划中某个步骤的状态。

**每完成一步必须调用此工具！**

**状态值**：
- pending: 待执行
- in_progress: 执行中
- completed: 已完成
- failed: 执行失败
- skipped: 已跳过

**示例**：
执行完 browser_navigate 后：
→ update_plan_step(step_id="step_1", status="completed", result="已打开百度首页")""",
        "input_schema": {
            "type": "object",
            "properties": {
                "step_id": {"type": "string", "description": "步骤ID"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "failed", "skipped"],
                    "description": "步骤状态",
                },
                "result": {"type": "string", "description": "执行结果或错误信息"},
            },
            "required": ["step_id", "status"],
        },
    },
    {
        "name": "get_plan_status",
        "category": "Plan",
        "description": "Get the current plan execution status. Shows all steps and their completion status.",
        "detail": """获取当前计划的执行状态。

返回信息包括：
- 计划总览
- 各步骤状态
- 已完成/待执行数量
- 执行日志""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "complete_plan",
        "category": "Plan",
        "description": "Mark the plan as completed and generate a summary report. Call when ALL steps are done.",
        "detail": """标记计划完成，生成最终报告。

**在所有步骤完成后调用**

**返回**：
- 执行摘要
- 成功/失败统计
- 总耗时""",
        "input_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string", "description": "完成总结"}},
            "required": ["summary"],
        },
    },
]

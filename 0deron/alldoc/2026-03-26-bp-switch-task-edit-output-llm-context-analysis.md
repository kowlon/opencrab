# bp_switch_task & bp_edit_output — LLM 判断依据上下文深度分析

> 日期: 2026-03-26
> 范围: LLM 如何获得判断依据、上下文在何阶段注入、切换/编辑过程的完整处理链路

---

## 目录

1. [总体架构](#1-总体架构)
2. [LLM 判断依据的上下文来源](#2-llm-判断依据的上下文来源)
3. [上下文注入的阶段与时序](#3-上下文注入的阶段与时序)
4. [bp_switch_task 完整流程](#4-bp_switch_task-完整流程)
5. [bp_edit_output 完整流程](#5-bp_edit_output-完整流程)
6. [关键数据结构](#6-关键数据结构)
7. [源码文件索引](#7-源码文件索引)

---

## 1. 总体架构

LLM 对 `bp_switch_task` 和 `bp_edit_output` 的调用判断，依赖于**三层上下文注入**:

```
┌──────────────────────────────────────────────────────────────────┐
│                     System Prompt                                │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  Layer 1: 静态段 (system_static.md)                       │    │
│  │  - 工具列表 + 交互规则 + 补充输入流程                       │    │
│  │  - 对所有 session 相同，启动时构建一次                      │    │
│  └──────────────────────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  Layer 2: 动态段 (system_dynamic.md)                      │    │
│  │  - 状态表 (status_table): 实例 ID、状态、进度              │    │
│  │  - 活跃上下文 (active_context): 当前任务名和进度             │    │
│  │  - 已完成输出预览 (outputs_preview): ≤1000 字符             │    │
│  │  - 用户偏好 (user_preferences): 语义摘要                   │    │
│  │  - 意图路由 (intent_routing): 引导 LLM 选择工具            │    │
│  │  - 每轮对话重新构建，实时反映状态                            │    │
│  └──────────────────────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  Layer 3: 工具定义 (tool_definitions.py)                   │    │
│  │  - 6 个 bp_* 工具的 name + description + input_schema      │    │
│  │  - 注册到 LLM tool list, LLM 通过 function calling 调用    │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                  │
│  + 上下文恢复消息 (切换后下一轮注入到 messages)                    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. LLM 判断依据的上下文来源

### 2.1 静态段 — 规则与工具说明

**文件**: `src/seeagent/bestpractice/prompt/templates/system_static.md`

提供 LLM 「什么时候该用什么工具」的基础规则:

```markdown
## 可用工具
- `bp_edit_output`: 修改已完成子任务的输出 (subtask_id, changes)
- `bp_switch_task`: 切换到另一个挂起的 BP 实例 (target_instance_id)

## 交互规则
- Chat-to-Edit: 用户想修改已完成子任务的输出时，先调用 bp_get_output 获取当前内容，
  再调用 bp_edit_output 修改
- 任务切换: 用户想切换到另一个进行中的任务时，调用 bp_switch_task
```

**构建方**: `BPPromptBuilder.build_static_section()` (`prompt/builder.py:36-75`)
- 遍历所有已注册的 BP 配置，生成 `${bp_list}` 变量
- 渲染 `system_static.md` 模板

### 2.2 动态段 — 实时状态与意图路由

**文件**: `src/seeagent/bestpractice/prompt/templates/system_dynamic.md`

```markdown
# 当前最佳实践状态
${status_table}
${active_context}
${outputs_preview}
${user_preferences}
${intent_routing}
```

**构建方**: `BPPromptBuilder.build_dynamic_section(session_id)` (`prompt/builder.py:79-187`)

#### 2.2.1 状态表 (status_table)

由 `BPStateManager.get_status_table()` (`state_manager.py:160-222`) 生成:

```markdown
| Instance | BP | Status | Progress | Current Step | RunMode |
| --- | --- | --- | --- | --- | --- |
| bp-a1b2c3d4 | 需求分析 | active | 2/5 | 竞品调研 | manual |
| bp-e5f6g7h8 | 市场报告 | suspended | 1/3 | 数据收集 | auto |
```

**这是 LLM 判断 `bp_switch_task` 的核心依据**:
- LLM 看到 `suspended` 状态的实例和其 instance_id
- LLM 可以从用户话语中推断用户想切换到哪个任务
- 最多展示 3 个 suspended 实例，超出部分显示 `(N more suspended task(s) hidden -- use bp_switch_task to view)`

#### 2.2.2 活跃上下文 (active_context)

```
**当前活跃任务**: 需求分析 (进度: 2/5)
```

#### 2.2.3 已完成输出预览 (outputs_preview)

```markdown
### 已完成子任务输出
- s1: {"insights": ["...", "..."], "trends": ["..."]}
- s2: {"report": "...前300字..."}
```

**预算限制**: `_DYNAMIC_OUTPUTS_BUDGET = 1000` 字符，超出截断并显示 `...`

**这是 LLM 判断 `bp_edit_output` 的核心依据**:
- LLM 看到每个已完成子任务的 subtask_id 和输出预览
- 当用户说"把第一步的结论改成 XXX"时，LLM 可以定位到 subtask_id 和需要修改的字段

#### 2.2.4 用户偏好 (user_preferences)

从 `context_summary` 的 `semantic_summary` 字段提取（如果存在）:

```markdown
### 用户偏好/上下文
用户偏好简洁风格，关注 ROI 指标...
```

#### 2.2.5 意图路由 (intent_routing)

**这是指导 LLM「何时该调用哪个工具」的关键指令**，根据当前子任务状态动态生成:

| 当前状态条件 | 注入的意图路由内容 |
|---|---|
| `current_status == "waiting_input"` | 提示 `bp_answer` |
| `prev_status == "done"` 或 `current_status == "done"` | 提示 `bp_next` / `bp_edit_output` / `bp_cancel` |
| 其他情况 | 提示 `bp_edit_output` / `bp_switch_task` / `bp_cancel` |

**具体内容**:

状态 1 — 等待输入:
```
当前子任务等待用户输入参数。
如果用户提供了参数值，调用 bp_answer(subtask_id=..., data={...}) 补充。
如果用户想取消，调用 bp_cancel。
```

状态 2 — 上一步已完成:
```
上一步已完成。用户可能想要:
A) 继续下一步 → 调用 bp_next
B) 修改上一步结果 → 调用 bp_edit_output(subtask_id=..., changes={...})
C) 取消任务 → 调用 bp_cancel
D) 询问其他问题（不涉及 BP 操作）
```

状态 3 — 其他:
```
用户可能想要:
A) 修改已完成子任务结果 (bp_edit_output)
B) 切换到其他任务 (bp_switch_task)
C) 取消当前任务 (bp_cancel)
D) 询问相关问题
```

### 2.3 工具定义 — Function Calling Schema

**文件**: `src/seeagent/bestpractice/tool_definitions.py`

#### bp_switch_task 的工具定义:
```python
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
}
```

#### bp_edit_output 的工具定义:
```python
{
    "name": "bp_edit_output",
    "category": "Best Practice",
    "description": "修改已完成子任务的输出 (Chat-to-Edit 模式)",
    "input_schema": {
        "type": "object",
        "properties": {
            "instance_id": {
                "type": "string",
                "description": "BP 实例 ID (可选)",
            },
            "subtask_id": {
                "type": "string",
                "description": "要修改的子任务 ID",
            },
            "changes": {
                "type": "object",
                "description": "要合并的修改内容 (深度合并，数组完整替换)",
            },
        },
        "required": ["subtask_id", "changes"],
    },
}
```

---

## 3. 上下文注入的阶段与时序

### 3.1 完整时序图

```
用户发送消息
    │
    ▼
Agent._prepare_session_context()
    │
    ├── Step 1-9: 普通消息处理、附件、记忆等
    │
    ├── Step 10.4: BP Lazy Restore                    ←── ① 恢复状态
    │   └── 服务重启后，从 session.metadata["bp_state"]
    │       恢复内存中的 BP 实例快照
    │
    ├── Step 10.5: BP Context Switch                  ←── ② 消费 PendingContextSwitch
    │   └── 调用 ContextBridge.execute_pending_switch()
    │       - 压缩被挂起实例的上下文 (LLM/机械)
    │       - 恢复目标实例上下文 (注入 recovery message 到 messages)
    │
    ├── Step 11: Context Compression
    │
    └── Step 12: TaskMonitor
    │
    ▼
PromptAssembler.build_system_prompt()               ←── ③ 构建系统提示词
    │
    ├── _build_bp_section()
    │   ├── get_static_prompt_section()  → 静态段
    │   └── get_dynamic_prompt_section() → 动态段 (含意图路由)
    │
    └── 组装完整 system prompt (含 bp_section)
    │
    ▼
Brain.think() — LLM 调用                             ←── ④ LLM 决策
    │
    ├── system prompt 含 BP 静态 + 动态段
    ├── tools 列表含 6 个 bp_* 工具定义
    └── messages 可能含 recovery message
    │
    ▼
LLM 返回 tool_use: bp_switch_task / bp_edit_output   ←── ⑤ 工具调用
    │
    ▼
handler_registry → BPToolHandler.handle()             ←── ⑥ 执行
```

### 3.2 各阶段详解

#### 阶段 ①: BP Lazy Restore (`agent.py:3882-3899`)

**触发条件**: 服务重启后首次收到消息，内存中无 BP 实例但 `session.metadata["bp_state"]` 有数据

```python
# 仅当内存中该 session 无实例时才恢复
if _bp_sm and session_id and session and not _bp_sm.get_all_for_session(session_id):
    _bp_data = getattr(session, "metadata", {}).get("bp_state")
    if _bp_data:
        _restored = _bp_sm.restore_from_dict(session_id, _bp_data, config_map=_cmap)
```

**防回滚机制** (`state_manager.py:317-325`): 如果内存中已存在该实例，不覆盖（避免持久化数据滞后于内存数据导致进度回滚）。

#### 阶段 ②: PendingContextSwitch 消费 (`agent.py:3901-3912`)

**触发条件**: 上一轮调用了 `bp_switch_task` 或 `bp_start`（且产生了挂起），存在 `PendingContextSwitch`

```python
switched = await _bp_cb.execute_pending_switch(
    session_id, brain=self.brain, messages=messages,
)
```

内部执行:
1. **压缩被挂起实例上下文**: LLM 语义压缩 → JSON 结构化摘要
2. **恢复目标实例上下文**: 将 recovery message 注入到 `messages` 末尾

#### 阶段 ③: System Prompt 构建 (`prompt_assembler.py:116-131`)

BP section 被放在 system prompt 的靠后位置（核心原则之后、profile prompt 之前）:

```python
return f"""{base_prompt}
{system_info}
{env_snapshot}
...
{tools_text}
{tools_guide}
{core_principles}
{bp_section}          ← 这里
{profile_prompt}"""
```

#### 阶段 ④: 工具注册 (`agent.py:1087-1117`)

在 Agent 初始化时:
```python
if init_bp_system():
    bp_handler = get_bp_handler()
    if bp_handler:
        # 注册到 handler_registry（路由分发）
        self.handler_registry.register("bestpractice", _bp_handle, get_bp_tool_names())
        # 注册到 LLM tool list（LLM 可见）
        for bp_tool in BP_TOOL_DEFINITIONS:
            self._tools.append(bp_tool)
            self.tool_catalog.add_tool(bp_tool)
```

---

## 4. bp_switch_task 完整流程

### 4.1 端到端时序

```
用户: "切换回需求分析那个任务"
    │
    ▼
[System Prompt 包含]:
  status_table:
    | bp-a1b2 | 需求分析 | suspended | 2/5 | ... |
    | bp-c3d4 | 市场报告 | active    | 1/3 | ... |
  intent_routing:
    "用户可能想要:
     A) 修改已完成子任务结果 (bp_edit_output)
     B) 切换到其他任务 (bp_switch_task)  ← LLM 匹配此项
     C) ..."
    │
    ▼
LLM 决策: tool_use bp_switch_task(target_instance_id="bp-a1b2")
    │
    ▼
BPToolHandler._handle_switch_task()                   [handler.py:147-171]
    │
    ├── 1. 校验: target_id 非空
    ├── 2. 校验: 实例存在于 state_manager
    ├── 3. 校验: 实例属于当前 session (防跨会话切换)
    │
    └── 4. 调用 engine.switch()                        [core.py:142-168]
         │
         ├── a. 获取当前 active 实例
         ├── b. 如果目标已是 active → 返回 already_active
         ├── c. 挂起当前实例: state_manager.suspend(current_id)
         │      └── snap.status = SUSPENDED
         │      └── snap.suspended_at = time.time()
         ├── d. 恢复目标实例: state_manager.resume(target_id)
         │      └── snap.status = ACTIVE
         │      └── snap.suspended_at = None
         ├── e. 创建 PendingContextSwitch:
         │      └── PendingContextSwitch(
         │            suspended_instance_id=current_id,
         │            target_instance_id=target_id,
         │          )
         ├── f. 存入 state_manager._pending_switches[session_id]
         └── g. persist_to_session() → session.metadata["bp_state"]
    │
    ▼
返回给 LLM:
  "已切换到任务「需求分析」(id=bp-a1b2)。
   上下文将在下一轮对话中恢复。"
    │
    ▼
--- 下一轮用户消息到来 ---
    │
    ▼
Agent._prepare_session_context()
    │
    ├── Step 10.5: execute_pending_switch()             [context_bridge.py:56-99]
    │   │
    │   ├── consume_pending_switch(session_id)
    │   │   └── 从 _pending_switches 弹出 PendingContextSwitch
    │   │
    │   ├── 压缩被挂起实例 (bp-c3d4 市场报告):
    │   │   └── _compress_context()                      [context_bridge.py:118-203]
    │   │       ├── LLM 压缩 (优先):
    │   │       │   └── brain.think_lightweight(压缩 prompt)
    │   │       │   └── 提取: 用户偏好、关键决策、当前状态、未解决问题
    │   │       │   └── 最多 600 字符
    │   │       ├── 机械压缩 (fallback):
    │   │       │   └── 提取最近 15 条消息的文本
    │   │       │   └── 过滤 tool 消息和短 assistant 消息
    │   │       └── 组装结构化 JSON:
    │   │           {
    │   │             "version": 1,
    │   │             "bp_name": "市场报告",
    │   │             "current_subtask_index": 0,
    │   │             "total_subtasks": 3,
    │   │             "subtask_progress": [...],
    │   │             "key_outputs": { "s1": "...截断..." },
    │   │             "semantic_summary": "用户关注 ROI...",
    │   │             "user_intent": "{\"topic\": \"AI市场\"}",
    │   │             "compressed_at": 1711440000.0,
    │   │             "compression_method": "llm"
    │   │           }
    │   │
    │   └── 恢复目标实例 (bp-a1b2 需求分析):
    │       └── _restore_context()                       [context_bridge.py:281-312]
    │           ├── 解析 context_summary JSON
    │           ├── 构建 recovery prompt:
    │           │   [Task Resumed] Best Practice: 需求分析
    │           │   Progress: step 3/5
    │           │   Steps:
    │           │     [+] 需求收集
    │           │     [+] 竞品调研
    │           │     [>] 用户画像
    │           │     [ ] 功能设计
    │           │     [ ] 评审
    │           │   Completed outputs:
    │           │     s1: {"requirements": [...]}
    │           │     s2: {"competitors": [...]}
    │           │   Context summary:
    │           │     用户偏好 B2B 场景...
    │           │   Please continue from where this task was suspended.
    │           │
    │           └── 注入到 messages:
    │               - 如果最后一条是 user 消息 → 追加到内容末尾
    │               - 否则 → 添加新的 user 消息
    │
    ▼
System Prompt 动态段更新:
  status_table 现在显示:
    | bp-a1b2 | 需求分析 | active    | 2/5 | ... |
    | bp-c3d4 | 市场报告 | suspended | 1/3 | ... |
```

### 4.2 关键设计要点

1. **两阶段切换**: 工具调用阶段只做状态翻转 + 创建 PendingContextSwitch；真正的上下文压缩和恢复在**下一轮**消息处理时执行
2. **PendingContextSwitch 是一次性的**: `consume_pending_switch` 会弹出并删除，确保不重复执行
3. **上下文压缩三级 fallback**: LLM 语义 → 机械提取 → 空字符串
4. **跨会话防护**: `snap.session_id != session.id` 时拒绝切换

---

## 5. bp_edit_output 完整流程

### 5.1 端到端时序

```
用户: "把竞品调研的结论里，竞品A改成竞品B"
    │
    ▼
[System Prompt 包含]:
  outputs_preview:
    ### 已完成子任务输出
    - s1: {"requirements": [...]}
    - s2: {"competitors": [{"name": "竞品A", ...}]}  ← LLM 看到此内容
  intent_routing:
    "上一步已完成。用户可能想要:
     ...
     B) 修改上一步结果 → 调用 bp_edit_output(subtask_id=..., changes={...})"
    │
    ▼
LLM 决策: tool_use bp_edit_output(
    subtask_id="s2",
    changes={"competitors": [{"name": "竞品B", ...}]}
)
    │
    ▼
BPToolHandler._handle_edit_output()                    [handler.py:99-143]
    │
    ├── 1. 解析 instance_id:
    │      └── _resolve_instance_id(): 优先用参数，否则取 active 实例
    ├── 2. 校验: subtask_id 非空
    ├── 3. 校验: changes 非空
    ├── 4. 校验: 实例存在
    ├── 5. 校验: 实例属于当前 session
    ├── 6. 获取 BP 配置
    │
    └── 7. 调用 engine.handle_edit_output()             [core.py:739-770]
         │
         ├── a. 校验: subtask_id 在 subtask_outputs 中有输出
         │
         ├── b. 深度合并:                                [state_manager.py:116-124]
         │      └── merge_subtask_output(instance_id, subtask_id, changes)
         │          └── _deep_merge(existing, changes)
         │              - dict + dict → 递归合并
         │              - array → 完整替换 (不是追加)
         │              - 其他类型 → 直接覆盖
         │
         ├── c. 标记下游为 STALE:                        [state_manager.py:126-143]
         │      └── mark_downstream_stale(instance_id, subtask_id, bp_config)
         │          └── 遍历 bp_config.subtasks:
         │              - 找到 from_subtask_id 之后的所有子任务
         │              - 如果状态是 DONE → 改为 STALE
         │              - 返回受影响的 subtask_id 列表
         │
         └── d. 软校验:                                  [core.py:962-981]
              └── _validate_output_soft(merged, subtask_id, bp_config)
                  └── 检查 output_schema.required 字段是否都在 merged 中
                  └── 只返回警告，不阻止操作
    │
    ▼
返回给 LLM:
  "✅ 子任务输出已合并更新。
   预览: {"competitors": [{"name": "竞品B", ...}]}
   ⚠️ 以下下游子任务已标记为 stale，需要重新执行: ['s3', 's4']"
    │
    ▼
(如果有 stale 子任务) 发送 SSE 事件:               [core.py:1007-1023]
  {
    "type": "bp_stale",
    "data": {
      "instance_id": "bp-a1b2",
      "stale_subtask_ids": ["s3", "s4"],
      "reason": "子任务 s2 输出被编辑"
    }
  }
```

### 5.2 深度合并语义

```python
# 示例: 原始输出
existing = {
    "summary": "竞品分析报告",
    "competitors": [
        {"name": "竞品A", "score": 85},
        {"name": "竞品C", "score": 72}
    ],
    "metadata": {
        "author": "agent",
        "confidence": 0.9
    }
}

# 用户修改
changes = {
    "competitors": [{"name": "竞品B", "score": 90}],   # 数组: 完整替换
    "metadata": {"confidence": 0.95}                     # 嵌套dict: 递归合并
}

# 合并结果
merged = {
    "summary": "竞品分析报告",                            # 未修改字段: 保留
    "competitors": [{"name": "竞品B", "score": 90}],     # 数组: 完整替换
    "metadata": {
        "author": "agent",                               # 未修改子字段: 保留
        "confidence": 0.95                                # 被覆盖
    }
}
```

### 5.3 STALE 级联机制

```
子任务顺序: s1 → s2 → s3 → s4 → s5
子任务状态: DONE  DONE  DONE  DONE  PENDING

用户 edit s2 的输出后:
子任务状态: DONE  DONE  STALE STALE PENDING
                        ↑     ↑
                    s3 和 s4 被标记为 STALE
                    (它们是 s2 之后的所有 DONE 子任务)
```

STALE 子任务需要重新执行 (`bp_next` 会重新执行 STALE 子任务)。

### 5.4 关键设计要点

1. **instance_id 可选**: 如果省略，自动取当前 session 的 active 实例
2. **只能编辑有输出的子任务**: `subtask_id not in snap.subtask_outputs` → 报错
3. **深度合并而非替换**: 用户只需提供要改的字段，其他字段保留
4. **数组语义**: 数组是完整替换（不支持部分修改数组元素）
5. **软校验**: 合并后检查 output_schema.required 字段，缺失只警告不阻止
6. **下游级联**: 编辑后所有下游 DONE 子任务变为 STALE

---

## 6. 关键数据结构

### 6.1 BPInstanceSnapshot (运行时快照)

```python
@dataclass
class BPInstanceSnapshot:
    bp_id: str                                    # BP 配置 ID
    instance_id: str                              # 实例唯一 ID (bp-xxxxxxxx)
    session_id: str                               # 所属会话
    status: BPStatus                              # ACTIVE / SUSPENDED / COMPLETED / CANCELLED
    created_at: float
    completed_at: float | None
    suspended_at: float | None
    current_subtask_index: int                    # 当前进度指针
    run_mode: RunMode                             # MANUAL / AUTO
    subtask_statuses: dict[str, str]              # {subtask_id: "done"/"pending"/"stale"/...}
    initial_input: dict[str, Any]                 # 用户提供的初始输入
    subtask_outputs: dict[str, dict[str, Any]]    # {subtask_id: output_dict}
    context_summary: str                          # 挂起时的上下文压缩 JSON
    supplemented_inputs: dict[str, dict[str, Any]] # bp_answer 补充的输入
    bp_config: BestPracticeConfig | None          # 运行时引用 (不序列化)
```

### 6.2 PendingContextSwitch

```python
@dataclass
class PendingContextSwitch:
    """由 bp_switch_task/bp_start 创建，由 Agent._prepare_session_context() 消费。"""
    suspended_instance_id: str    # 被挂起的实例
    target_instance_id: str       # 要恢复的实例
    created_at: float             # 创建时间
```

### 6.3 SubtaskStatus 枚举

```python
class SubtaskStatus(Enum):
    PENDING = "pending"           # 待执行
    CURRENT = "current"           # 执行中
    DONE = "done"                 # 已完成
    STALE = "stale"               # 已过期 (上游 edit 后需重执行)
    FAILED = "failed"             # 执行失败
    WAITING_INPUT = "waiting_input" # 等待用户补充输入
```

### 6.4 session.metadata["bp_state"] 持久化格式

```json
{
    "version": 2,
    "instances": [
        {
            "bp_id": "requirement-analysis",
            "instance_id": "bp-a1b2c3d4",
            "session_id": "sess-xxxx",
            "status": "active",
            "current_subtask_index": 2,
            "run_mode": "manual",
            "subtask_statuses": {"s1": "done", "s2": "done", "s3": "pending"},
            "initial_input": {"topic": "AI产品"},
            "subtask_outputs": {
                "s1": {"requirements": ["..."]},
                "s2": {"competitors": ["..."]}
            },
            "context_summary": "{\"version\": 1, ...}",
            "supplemented_inputs": {}
        }
    ],
    "pending_switch": {
        "suspended_id": "bp-e5f6g7h8",
        "target_id": "bp-a1b2c3d4"
    },
    "cooldown": 0,
    "offered_bps": ["requirement-analysis"]
}
```

---

## 7. 源码文件索引

| 文件 | 职责 | 关键行 |
|------|------|--------|
| `bestpractice/tool_definitions.py` | 6 个 BP 工具的 schema 定义 | L33-55 (edit_output), L57-70 (switch_task) |
| `bestpractice/handler.py` | 工具路由 & 参数校验 | L99-143 (edit_output), L147-171 (switch_task) |
| `bestpractice/engine/core.py` | 核心业务逻辑 | L142-168 (switch), L738-770 (handle_edit_output) |
| `bestpractice/engine/state_manager.py` | 实例生命周期 & 状态管理 | L71-82 (suspend/resume), L116-143 (merge/stale), L160-222 (status_table), L300-342 (restore) |
| `bestpractice/engine/context_bridge.py` | 上下文压缩 & 恢复 | L56-99 (execute_pending_switch), L118-203 (compress), L281-312 (restore) |
| `bestpractice/prompt/builder.py` | 系统提示词构建 (静态+动态) | L36-75 (static), L79-187 (dynamic + intent_routing) |
| `bestpractice/prompt/templates/system_static.md` | 静态提示词模板 | 全文 |
| `bestpractice/prompt/templates/system_dynamic.md` | 动态提示词模板 | 全文 |
| `bestpractice/models.py` | 数据模型 (枚举、配置、快照) | L27-33 (SubtaskStatus), L91-96 (PendingContextSwitch), L99-161 (Snapshot) |
| `bestpractice/facade.py` | 单例工厂 & 公共 API | L60-128 (init), L196-211 (prompt injection API) |
| `core/agent.py` | Agent 集成 (注册、恢复、切换) | L1087-1117 (工具注册), L3882-3912 (lazy restore + switch consume) |
| `core/prompt_assembler.py` | 系统提示词组装 | L116 (bp_section 位置), L421-438 (_build_bp_section) |

---

## 附: LLM 决策路径总结

### LLM 何时调用 bp_switch_task?

1. **前提**: system prompt 中 `status_table` 展示了 ≥1 个 `suspended` 实例
2. **触发**: 用户消息表达了切换任务的意图 (如"切换到XXX"、"回到之前的任务")
3. **路由**: `intent_routing` 明确列出 `bp_switch_task` 作为选项
4. **参数**: LLM 从 `status_table` 中找到目标实例的 `instance_id`

### LLM 何时调用 bp_edit_output?

1. **前提**: system prompt 中 `outputs_preview` 展示了已完成子任务的输出
2. **触发**: 用户消息表达了修改输出的意图 (如"把XX改成YY"、"调整一下结论")
3. **路由**: `intent_routing` 在子任务完成后的状态中推荐 `bp_edit_output`
4. **参数**: LLM 从 `outputs_preview` 中提取 `subtask_id` 和需要修改的 `changes`

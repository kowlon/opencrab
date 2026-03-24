# BP Chat 意图路由设计文档

> 日期: 2026-03-24
> 状态: Draft
> 作者: Claude (Architect Review)

## 1. 背景与目标

### 1.1 现状

当前最佳实践(BP)系统有两种触发方式：

1. **前端 UI 操作**（已完善）：用户点击 BPOfferBlock → 确认启动 → TaskProgressCard 展示进度 → SubtaskCompleteBlock 控制下一步 → BPAskUserBlock 补充参数
2. **Chat 硬编码命令**（部分支持）：`_BP_START_COMMANDS`（"进入最佳实践"）和 `_BP_NEXT_COMMANDS`（"下一步"）可通过 seecrab.py 路由层拦截

**问题：用户无法通过自然语言输入（如"帮我写一篇公众号文章"）触发 BP 流程，也无法通过 chat 完成参数补充、结果编辑、取消等操作。**

### 1.2 目标

让用户通过 chat 输入自然语言，能触发与 UI 操作完全相同的 BP 后续流程。具体包含 5 个操作：

| 操作 | 触发场景 | 当前 Chat 支持 |
|---|---|---|
| **Start** | 用户描述的需求匹配某个 BP | 仅关键词匹配 → bp_offer |
| **Next** | BP 手动模式暂停，用户想继续 | 硬编码命令"下一步"等 |
| **Answer** | BP 子任务缺参数，用户通过 chat 回答 | 不支持 |
| **Edit** | 用户想修改已完成子任务的输出 | 不支持（仅 Agent 工具路径） |
| **Cancel** | 用户想取消当前 BP | 不支持 |

### 1.3 核心原则

**无论入口是 UI 还是 Chat，最终都调用相同的 engine 方法，产出相同的事件序列，前端渲染相同的 UI 组件。**

## 2. 架构现状分析

### 2.1 三个入口的当前收敛情况

```
入口 A: BP REST API（前端直调）     → bestpractice.py
入口 B: Chat 命令（seecrab.py 拦截） → seecrab.py 路由层
入口 C: Agent 工具（handler.py）     → Agent 推理循环内
```

收敛点 = engine 方法：`engine.advance()`、`engine.answer()`、`engine.handle_edit_output()`、`sm.cancel()`

#### 当前收敛状态（Review 发现的问题）

| 操作 | 入口A (REST API) | 入口B (Chat路由) | 入口C (Agent工具) |
|---|---|---|---|
| Start | `engine.advance()` ✅ | `engine.advance()` ✅ | **只创建实例，不调 `engine.advance()`** ❌ |
| Next | `engine.advance()` ✅ | `engine.advance()` ✅ | **工具不存在** ❌ |
| Answer | `engine.answer()` ✅ | **路径不存在** ❌ | **工具不存在** ❌ |
| Edit | `engine.handle_edit_output()` ✅ | 无此路径 | `engine.handle_edit_output()` ✅ |
| Cancel | `sm.cancel()` ✅ | **路径不存在** ❌ | **工具不存在** ❌ |

### 2.2 事件传递的两种机制

1. **直接 yield**（入口 A、B）：engine 方法产出的事件在 SSE 响应 generator 中直接 yield，前端通过 EventSource 接收。
2. **event_bus 转发**（入口 C）：Agent 工具运行在 `chat_with_session_stream()` 内部，事件需推送到 `session.context._sse_event_bus`，由 `adapter.transform()` 合并到 SSE 响应流中。

两种机制最终效果相同：前端收到相同的事件类型，渲染相同的 UI 组件。

**⚠️ 入口 C 的 adapter 事件透传问题**：

当前 `seecrab_adapter.py` 的 `_process_event()` 仅处理以下 BP 事件类型：
- `bp_instance_created` → pass through ✅
- `bp_progress`、`bp_subtask_output`、`bp_stale` → flatten `data` wrapper ✅
- `step_card` → pass through ✅

**以下事件类型会被静默丢弃**（return `[]`）：
- `bp_subtask_start`、`bp_subtask_complete`、`bp_waiting_next`、`bp_ask_user`、`bp_complete`、`bp_error` ❌

此外，`advance()` yield 的 `bp_progress` 事件使用**扁平格式**（`{"type": "bp_progress", "instance_id": ...}`），而 adapter 期望 `{"type": "bp_progress", "data": {...}}` 的包装格式。通过 adapter 后数据丢失。

**本次需修复 adapter 使入口 C 正常工作**（详见 3.9 节）。

### 2.3 现有代码关键路径

#### Start 操作 — UI 路径（正确示范）

```
bestpractice.py POST /api/bp/start:
  sm.create_instance(bp_config, session_id, input_data, run_mode)
  → yield bp_instance_created 事件
  → engine.advance(instance_id, session)
    → 检查 input_schema → 执行子任务 → yield bp_subtask_start/complete/progress
  → _persist_bp_to_session()
```

#### Start 操作 — Agent 工具路径（当前有问题）

```
handler.py _handle_start():
  sm.create_instance(bp_config, session_id, input_data, run_mode)
  → await bus.put(bp_instance_created)
  → return "前端将自动开始执行"  ← 但子任务实际未执行！
```

## 3. 设计方案

### 3.1 混合路由架构

采用 **路由层拦截 + Agent 工具** 的混合方案：

- **路由层**（seecrab.py，Agent 之前拦截）负责：
  - BP 触发匹配（关键词 + LLM 回退）→ 发 bp_offer
  - 硬编码命令（下一步、取消）→ 直接执行
  - 状态感知路由（waiting_input 时的参数回答）→ 直接执行

- **Agent**（通过 system prompt + 工具调用）负责：
  - 路由层未拦截的复杂意图（修改输出、模糊的继续/取消表达等）
  - 利用 Agent 的完整对话上下文做更准确的意图判断

### 3.2 Chat 消息处理流程（修订版）

```
用户消息进入 seecrab_chat()
    │
    ├─ 0. 前置初始化（每次消息都执行）               [新增]
    │   ├─ _ensure_bp_restored(request, bp_session_id, bp_sm)
    │   │   → 服务重启后从 session.metadata 恢复 BP 状态
    │   └─ bp_sm.tick_cooldown(bp_session_id)
    │       → 每次消息递减 cooldown（否则 cooldown 永久生效）
    │
    ├─ 1. _match_bp_command() → 硬编码命令？
    │   ├─ "start" → _stream_bp_start_from_chat()
    │   ├─ "next"  → 检查 waiting_input（见下方）→ _stream_bp_next_from_chat()
    │   ├─ "next_loose" → 仅有活跃 BP 时生效，否则放行到步骤 5  [新增]
    │   ├─ "cancel"→ 有活跃 BP → _cancel_bp_from_chat()           [新增]
    │   │            无活跃 BP → 提示"当前没有进行中的任务"         [新增]
    │   └─ "next"/"next_loose" + waiting_input → 提示"请先补充参数" [新增]
    │
    ├─ 2. 有活跃 BP 且状态为 waiting_input？          [新增]
    │   └─ YES → _stream_bp_answer_from_chat()
    │            内部调 engine.answer()，与 UI 路径一致
    │
    ├─ 3. match_bp_from_message() → 关键词匹配？
    │   └─ YES → emit bp_offer + 存 pending_offer
    │
    ├─ 4. llm_match_bp_from_message() → LLM 匹配？   [新增]
    │   └─ YES → emit bp_offer + 存 pending_offer（含 extracted_input）
    │
    └─ 5. agent.chat_with_session_stream() → Agent 处理
         Agent 可调用 bp_next / bp_edit_output / bp_cancel 工具
```

**步骤 0：前置初始化**（Critical，每次用户消息都必须执行）：

```python
from seeagent.api.routes.bestpractice import _ensure_bp_restored
from seeagent.bestpractice.facade import get_bp_state_manager

bp_sm = get_bp_state_manager()
if bp_sm:
    # [C2] 服务重启后内存为空，需从 session metadata 恢复
    # 一次调用覆盖后续所有分支（start/next/cancel/answer/matching）
    _ensure_bp_restored(request, bp_session_id, bp_sm)
    # [C1] 每次用户消息递减 cooldown，否则 cooldown 永久 > 0
    # tick_cooldown() 已实现但当前源码中从未被调用
    bp_sm.tick_cooldown(bp_session_id)
```

**步骤 1 中 next + waiting_input 的处理**：

当用户说"下一步"但当前子任务处于 `waiting_input` 状态时，`engine.advance()` 找不到 PENDING/STALE 状态的子任务会静默返回，用户得到空响应。需增加前置检查：

```python
if bp_cmd in ("next", "next_loose"):
    active = bp_sm.get_active(bp_session_id) if bp_sm else None
    # [I3] next_loose 仅在有活跃 BP 时生效，否则放行到 Agent
    if bp_cmd == "next_loose" and not active:
        pass  # 不 return，继续到步骤 2-5
    elif active:
        has_waiting = any(s == "waiting_input" for s in active.subtask_statuses.values())
        if has_waiting:
            fallback = {"type": "ai_text", "content": "当前子任务正在等待您补充参数，请先提供所需信息，或输入"取消任务"退出。"}
            yield f"data: {json.dumps(fallback, ensure_ascii=False)}\n\n"
            yield 'data: {"type": "done"}\n\n'
            return
        if _has_bp_next_step(active):
            # ... 正常的 next 处理逻辑
            ...
            return
    fallback = {"type": "ai_text", "content": "当前没有可继续的最佳实践任务。"}
    yield f"data: {json.dumps(fallback, ensure_ascii=False)}\n\n"
    yield 'data: {"type": "done"}\n\n'
    return
```

**步骤 1 中 cancel 无活跃 BP 的处理**：

```python
if bp_cmd == "cancel":
    active = bp_sm.get_active(bp_session_id) if bp_sm else None
    if active:
        async for event in _cancel_bp_from_chat(
            session_id=bp_session_id,
            instance_id=active.instance_id,
            session=session,
            session_manager=session_manager,
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        return
    else:
        fallback = {"type": "ai_text", "content": "当前没有进行中的最佳实践任务。"}
        yield f"data: {json.dumps(fallback, ensure_ascii=False)}\n\n"
        yield 'data: {"type": "done"}\n\n'
        return
```

**步骤 2 的限制与跳出**：

waiting_input 路由会拦截所有非命令消息。用户在此期间无法进行普通对话。这是有意的设计取舍——BP 正在执行中，参数补充是最合理的预期意图。跳出机制通过步骤 1 的 cancel 命令保证（cancel 优先级高于 waiting_input 路由）。

**路由优先级说明**：
- 步骤 1-2 是确定性路由（关键词匹配 + 状态判断），零延迟
- 步骤 3 是已有的关键词触发，零延迟
- 步骤 4 是 LLM 回退匹配，增加约 1-2s 延迟，仅在无活跃 BP 且关键词未命中时触发
- 步骤 5 是 Agent 处理，用于复杂意图或普通对话

### 3.3 LLM BP 匹配器详细设计

#### 3.3.1 函数签名

```python
# facade.py 新增
async def llm_match_bp_from_message(
    user_message: str,
    session_id: str,
    brain: Brain,
) -> dict | None:
```

#### 3.3.2 触发条件

仅当以下条件**全部满足**时才调用（避免不必要的 LLM 开销）：

1. `match_bp_from_message()` 关键词匹配返回 None
2. 无活跃 BP 实例（`sm.get_active(session_id)` 返回 None）
3. cooldown 未生效（`sm.get_cooldown(session_id) == 0`）
4. `brain` 实例可用
5. 至少存在一个未被 offered 的 BP 候选（`is_bp_offered()` 过滤后仍有候选）

#### 3.3.3 Prompt 设计

将所有已注册 BP 的 `id`、`name`、`description`、第一个子任务的 `input_schema` 组装为上下文。**使用 `_bp_prompt_loader.render("bp_match", ...)` 加载模板**（见 3.8 节），不要内联构建 prompt。

组装 BP 列表时需过滤掉已 offered 的 BP（`sm.is_bp_offered(session_id, bp_id)`）：

```python
bp_list_lines = []
for bp_id, config in _bp_config_loader.configs.items():
    if _bp_state_manager.is_bp_offered(session_id, bp_id):
        continue  # 已推荐过的不参与 LLM 匹配
    # ... 组装描述
```

Prompt 示例（由模板渲染，非硬编码）：

```
你是一个意图分类器。判断用户消息是否匹配以下最佳实践模板。

## 可用的最佳实践

1. content-pipeline: "内容创作流水线"
   描述: 从选题调研到内容发布的完整创作流程
   第一步需要的参数:
   - domain (string, 必填): 内容领域（如科技、商业、生活）
   - platform (string): 发布平台（公众号、小红书、知乎等）
   - audience (string): 目标受众描述

2. competitor-analysis: "竞品分析"
   描述: ...
   第一步需要的参数: ...

## 用户消息
"{user_message}"

## 任务
1. 判断用户消息是否明确表达了想要完成某个最佳实践能处理的任务
2. 如果匹配，从用户消息中提取第一步需要的参数值
3. 返回 JSON（不要输出其他内容）

匹配时返回:
{"matched": true, "bp_id": "<id>", "confidence": <0.0-1.0>, "extracted_input": {<从消息提取的参数>}}

不匹配时返回:
{"matched": false}
```

#### 3.3.4 返回值处理

```python
# 伪代码
# 注意: think_lightweight() 返回值属性需与代码统一
# - seecrab.py 中使用 resp.content
# - engine.py 中使用 resp.text
# 请以实际 Brain.Response 类定义为准，统一使用一个属性
resp = await brain.think_lightweight(prompt, max_tokens=512)
text = resp.content if hasattr(resp, "content") else str(resp)
parsed = json.loads(text)

if not parsed.get("matched") or parsed.get("confidence", 0) < 0.7:
    return None  # 不匹配，放行到 Agent

# 校验 bp_id 有效性
bp_id = parsed["bp_id"]
config = _bp_config_loader.configs.get(bp_id)
if not config:
    return None

# 二次校验: 确认该 BP 未被 offered
if _bp_state_manager.is_bp_offered(session_id, bp_id):
    return None

# 构造与 match_bp_from_message() 相同格式的返回值
# 包含 user_query 和 first_input_schema（与关键词匹配返回格式一致）
first_input_schema = config.subtasks[0].input_schema if config.subtasks else None
return {
    "bp_id": bp_id,
    "bp_name": config.name,
    "description": config.description,
    "subtask_count": len(config.subtasks),
    "subtasks": [{"id": s.id, "name": s.name} for s in config.subtasks],
    "extracted_input": parsed.get("extracted_input", {}),  # LLM 匹配独有字段
    "user_query": user_message,                            # 与关键词匹配一致
    "first_input_schema": first_input_schema,              # 与关键词匹配一致
}
```

#### 3.3.5 与现有流程的对接

`llm_match_bp_from_message()` 返回的 dict 与 `match_bp_from_message()` 格式一致（多一个 `extracted_input` 字段）。seecrab.py 中 bp_offer 处理逻辑大部分可复用，**但以下四处需要修改**：

**前置条件：`brain` 变量提前赋值**

当前 seecrab.py 中 `brain = getattr(agent, "brain", None)` 在第 525 行，位于 BP 匹配逻辑（第 454 行）之后。LLM 匹配（步骤 4）和 start 路径的 `_extract_input_from_query()`（步骤 1）都需要 `brain`。**必须将 `brain` 赋值提前到步骤 0 之后、步骤 1 之前**：

```python
# 步骤 0 之后立即赋值（约第 440 行附近，在 BP 命令匹配之前）
brain = getattr(agent, "brain", None)
```

原第 525 行的赋值保持不变（后续 `SeeCrabAdapter(brain=brain, ...)` 仍需使用），变量重复赋值不影响正确性。

**修改 1: `set_pending_offer` 存储 `extracted_input`**（当前代码未存储此字段）：

```python
# seecrab.py 中的处理（关键词匹配和 LLM 匹配合并为统一路径）
bp_match = match_bp_from_message(message, session_id)
if not bp_match:
    bp_match = await llm_match_bp_from_message(message, session_id, brain)

if bp_match:
    # bp_offer 事件发送（已有逻辑，复用）
    bp_offer_event = {"type": "bp_offer", "bp_id": ..., "bp_name": ..., ...}
    yield f"data: {json.dumps(bp_offer_event)}\n\n"

    # [修改] 存入 pending_offer 时增加 extracted_input（当前代码无此字段）
    bp_sm.set_pending_offer(session_id, {
        "bp_id": bp_match["bp_id"],
        "bp_name": bp_match["bp_name"],
        "subtasks": bp_match.get("subtasks", []),
        "default_run_mode": "manual",
        "user_query": bp_match.get("user_query", ""),
        "first_input_schema": bp_match.get("first_input_schema"),
        "extracted_input": bp_match.get("extracted_input", {}),  # [新增字段]
    })
```

**修改 2: `_stream_bp_start_from_chat()` 优先使用 `extracted_input`，消除重复 LLM 提取**：

当前代码在 START 时总是调用 `_extract_input_from_query()` 从 `user_query` + `first_input_schema` 实时提取。当 `extracted_input` 已存在（LLM 匹配路径预提取的）时应跳过，避免冗余 LLM 调用：

```python
# seecrab.py _match_bp_command() == "start" 分支
pending_offer = bp_sm.get_pending_offer(bp_session_id)
if pending_offer:
    # [修改] 优��使用预提取的 extracted_input，fallback 到实时提取
    extracted_input = pending_offer.get("extracted_input", {})
    if not extracted_input:
        # 关键词匹配路径没有 extracted_input，保留原有实时提取逻辑
        user_query = pending_offer.get("user_query", "")
        first_schema = pending_offer.get("first_input_schema")
        if user_query and first_schema:
            brain = getattr(agent, "brain", None)
            extracted_input = await _extract_input_from_query(brain, user_query, first_schema)

    async for event in _stream_bp_start_from_chat(
        request,
        session_id=bp_session_id,
        bp_id=pending_offer["bp_id"],
        run_mode_str=pending_offer.get("default_run_mode", "manual"),
        input_data=extracted_input,  # [修改] 统一入口
        ...
    ):
        yield f"data: {json.dumps(event)}\n\n"
```

**修改 3: bestpractice.py `POST /api/bp/start` 同步修改**（前端直调路径）：

bestpractice.py:363-374 也有类似的 fallback 提取逻辑，需同步修改为优先使用 `extracted_input`：

```python
# bestpractice.py POST /api/bp/start
if not input_data and bp_config.subtasks:
    pending_offer = sm.get_pending_offer(session_id)
    if pending_offer:
        input_data = pending_offer.get("extracted_input", {})
        if not input_data:
            # fallback: 实时提取
            user_query = pending_offer.get("user_query", "")
            first_schema = pending_offer.get("first_input_schema") or bp_config.subtasks[0].input_schema
            if user_query and first_schema:
                input_data = await _extract_input_from_query(brain, user_query, first_schema)
```

`_stream_bp_start_from_chat()` 内部调用 `sm.create_instance(bp_config, session_id, initial_input=input_data, ...)` → `engine.advance()` 检查 input_schema → 如果 extracted_input 已覆盖 required 字段则直接执行，否则发 `bp_ask_user` 让用户补充。**与 UI 路径完全一致。**

### 3.4 Chat Answer 路径详细设计（waiting_input 状态路由）

#### 3.4.1 触发条件

在 seecrab.py 路由层，`_match_bp_command()` 未命中后，检查：

```python
# 注意: _ensure_bp_restored() 已在步骤 0 中统一调用，此处无需重复
bp_sm = get_bp_state_manager()
active = bp_sm.get_active(bp_session_id) if bp_sm else None
if active:
    # 注意: subtask_statuses 存储的是 plain string（不是 enum），
    # 因为 state_manager.update_subtask_status() 存储 status.value
    waiting_subtask_id = None
    for st_id, st_status in active.subtask_statuses.items():
        if st_status == "waiting_input":
            waiting_subtask_id = st_id
            break
    if waiting_subtask_id:
        # → 路由到 _stream_bp_answer_from_chat()
```

#### 3.4.2 参数解析策略

从 BP 配置的 input_schema 和**完整解析后的输入**确定缺失字段：

**单字段缺失**：直接将用户消息作为该字段的值，无需 LLM。
```python
from seeagent.bestpractice.scheduler import LinearScheduler

# 从 waiting_subtask_id 查找对应的 subtask config
subtask_config = None
for st in active.bp_config.subtasks:
    if st.id == waiting_subtask_id:
        subtask_config = st
        break
if not subtask_config:
    # 异常：状态不一致，放行到 Agent
    pass  # fall through to agent

required = subtask_config.input_schema.get("required", [])

# [修复] 使用 scheduler.resolve_input() 获取完整解析后的输入
# 包含 initial_input（首个子任务）、上游子任务输出、supplemented_inputs 三者合并
scheduler = LinearScheduler(active.bp_config, active)
resolved_input = scheduler.resolve_input(waiting_subtask_id)
still_missing = [f for f in required if f not in resolved_input]

if len(still_missing) == 1:
    data = {still_missing[0]: user_message}
```

> **已知限制**：当用户输入完整句子（如"领域是科技"）时，单字段直接赋值会将整句作为字段值（`"领域是科技"` 而非 `"科技"`）。这是可接受的，因为下游 sub-agent 能容忍自然语言格式的输入值。如需精确提取，可统一走 LLM 解析路径。

**多字段缺失**：用 LLM 从用户消息中提取各字段值。
```python
if len(still_missing) > 1:
    data = await _llm_extract_answer_fields(
        user_message, still_missing, subtask_config.input_schema, brain
    )
```

**提取失败 fallback**（防止空提取 → answer → 重新缺字段 → ask_user 死循环）：
```python
if not data:
    # LLM 提取返回空 dict，不调用 engine.answer()，直接提示用户
    field_hints = ", ".join(still_missing)
    fallback = {
        "type": "ai_text",
        "content": f"无法从您的消息中识别参数，请按字段提供：{field_hints}",
    }
    yield fallback
    yield {"type": "done"}
    return
```

`_llm_extract_answer_fields()` 实现（复用现有 `_extract_input_from_query()` 的模式，限定到缺失字段）：

```python
async def _llm_extract_answer_fields(
    user_message: str,
    missing_fields: list[str],
    input_schema: dict,
    brain,
) -> dict:
    """从用户消息中提取指定的缺失字段值。"""
    if not brain or not missing_fields:
        return {}

    props = input_schema.get("properties", {})
    fields_desc = "\n".join(
        f"- {name}: {props.get(name, {}).get('description', '无描述')} "
        f"(type: {props.get(name, {}).get('type', 'string')})"
        for name in missing_fields
    )
    prompt = (
        "从用户消息中提取以下字段，输出一个 JSON 对象。\n"
        "只提取消息中明确提到或可推断的字段，没有提到的字段不要包含。\n"
        "只输出 JSON，不要其他文字。\n\n"
        f"## 需要提取的字段\n{fields_desc}\n\n"
        f"## 用户消息\n{user_message}"
    )
    try:
        from seeagent.bestpractice.engine import BPEngine
        resp = await brain.think_lightweight(prompt, max_tokens=512)
        text = resp.content if hasattr(resp, "content") else str(resp)
        parsed = BPEngine._parse_output(text)
        if isinstance(parsed, dict):
            # 只保留 missing_fields 中的字段
            return {k: v for k, v in parsed.items() if k in missing_fields}
    except Exception as e:
        logger.warning(f"[BP] Failed to extract answer fields: {e}")
    return {}
```

#### 3.4.3 执行路径

```python
async def _stream_bp_answer_from_chat(
    request, *, session_id, instance_id, subtask_id, data, session, session_manager, disconnect_event,
):
    """Chat 路径的 answer 处理。内部调用 engine.answer()，与 POST /api/bp/answer 一致。"""
    from seeagent.api.routes.bestpractice import (
        _bp_clear_busy,
        _bp_mark_busy,
        _collect_reply_state,
        _new_reply_state,
        _persist_bp_to_session,
    )

    engine = get_bp_engine()
    sm = get_bp_state_manager()
    if not engine or not sm:
        yield {"type": "error", "message": "BP system not initialized", "code": "bp"}
        yield {"type": "done"}
        return

    # [重要] 使用 bestpractice.py 的 _bp_busy_locks（与 UI answer 路径共享同一把锁）
    # 防止 Chat answer 和 UI answer 并发操作同一子任务
    if not await _bp_mark_busy(session_id, "seecrab_bp_answer"):
        yield {"type": "error", "message": "Session is busy", "code": "bp"}
        yield {"type": "done"}
        return

    reply_state = _new_reply_state()
    full_reply: list[str] = []
    try:
        async for event in engine.answer(instance_id, subtask_id, data, session):
            if disconnect_event.is_set():
                break
            yield event
            _collect_reply_state(event, reply_state, full_reply)

        _persist_bp_to_session(
            session, instance_id, sm,
            reply_state=reply_state,
            full_reply="".join(full_reply),
            session_manager=session_manager,
        )
        yield {"type": "done"}
    except Exception as e:
        yield {"type": "error", "message": str(e), "code": "bp"}
        yield {"type": "done"}
    finally:
        _bp_clear_busy(session_id)
```

**收敛验证**：
- UI 路径: `POST /api/bp/answer` → `engine.answer()` (bestpractice.py:539)
- Chat 路径: `_stream_bp_answer_from_chat()` → `engine.answer()`
- 两者调用相同的 `engine.answer()` ✅

### 3.5 Cancel 路径详细设计

#### 3.5.1 硬编码命令扩展

**Cancel 命令集**（新增）：

```python
_BP_CANCEL_COMMANDS = {
    "取消最佳实践", "终止最佳实践", "取消任务", "终止任务",
    "停止最佳实践", "退出最佳实践",
}
```

**Next 命令集扩展**（在现有基础上新增，拆分为严格和宽泛两组）：

```python
# 严格命令：任何时候都匹配（无活跃 BP 时返回提示信息）
_BP_NEXT_COMMANDS_STRICT = {
    # 现有
    "进入下一步", "下一步", "继续执行", "继续",
    # 新增
    "好的继续", "开始下一步", "执行下一步",
}

# 宽泛命令：仅在有活跃 BP 时匹配，否则放行到 Agent
# 这些日常用语（"好"、"ok"、"确认"）会劫持正常对话，
# 因此仅在 BP 上下文中才解释为"继续"
_BP_NEXT_COMMANDS_LOOSE = {
    "好", "没问题", "ok", "确认", "好的下一步",
}
```

在 `_match_bp_command()` 中区分返回值：

```python
def _match_bp_command(message: str) -> str | None:
    normalized = _normalize_bp_command(message)
    if normalized in _BP_START_COMMANDS:
        return "start"
    if normalized in _BP_NEXT_COMMANDS_STRICT:
        return "next"
    if normalized in _BP_NEXT_COMMANDS_LOOSE:
        return "next_loose"  # 调用方需检查是否有活跃 BP
    if normalized in _BP_CANCEL_COMMANDS:  # 新增
        return "cancel"
    return None
```

> **`next_loose` 处理逻辑**（见 3.2 步骤 1）：`seecrab_chat()` 中，`next_loose` 仅在 `bp_sm.get_active()` 返回非 None 时走 next 路径，否则跳过不 return，放行到步骤 2-5 由 Agent 处理。

> **Cancel 命令的局限性**：`_BP_CANCEL_COMMANDS` 仅匹配固定的精确短语（经 `_normalize_bp_command()` 标准化后）。更自然的取消表达（如"我想取消当前任务"、"算了不做了"、"我改主意了"）无法被路由层拦截，需要由 Agent 通过 `bp_cancel` 工具处理。这是混合路由架构的设计边界——路由层处理高频确定性意图，Agent 处理长尾模糊意图。

#### 3.5.2 执行路径

```python
async def _cancel_bp_from_chat(session_id: str, instance_id: str, session, session_manager):
    """Chat 路径的 cancel 处理。与 DELETE /api/bp/{instance_id} 调用相同的 sm.cancel()。"""
    sm = get_bp_state_manager()
    snap = sm.get(instance_id)
    bp_name = snap.bp_config.name if snap and snap.bp_config else instance_id

    sm.cancel(instance_id)

    # [新增] 触发 cooldown 防止立即重新触发同一 BP 匹配
    sm.set_cooldown(session_id)

    # 发送 cancelled 事件给前端（包含 bp_name 便于显示取消通知）
    yield {
        "type": "bp_cancelled",
        "instance_id": instance_id,
        "bp_name": bp_name,
    }
    # 持久化状态
    if session:
        session.metadata["bp_state"] = sm.serialize_for_session(session_id)
        if session_manager:
            session_manager.mark_dirty()
    yield {"type": "done"}
```

#### 3.5.3 修复 REST API cancel 持久化遗漏（已有 Bug）

当前 `DELETE /api/bp/{instance_id}` (bestpractice.py:593) 只调用 `sm.cancel()` 但不写 `session.metadata["bp_state"]`。页面刷新后，被取消的实例会被恢复为 active 状态。需一并修复：

```python
# bestpractice.py DELETE /{instance_id} 修改
@router.delete("/{instance_id}")
async def bp_cancel(instance_id: str, request: Request):
    sm = get_bp_state_manager()
    if not sm:
        return JSONResponse({"error": "BP system not initialized"}, status_code=500)
    snap = sm.get(instance_id)
    if not snap:
        return JSONResponse({"error": "Not found"}, status_code=404)
    sm.cancel(instance_id)
    # [修复] 持久化取消状态到 session metadata
    session = _resolve_session(request, snap.session_id)
    if session:
        session.metadata["bp_state"] = sm.serialize_for_session(snap.session_id)
        session_mgr = _resolve_session_manager(request)
        if session_mgr:
            session_mgr.mark_dirty()
    return JSONResponse({"status": "ok"})
```

**收敛验证**：
- UI 路径: `DELETE /api/bp/{instance_id}` → `sm.cancel()` (bestpractice.py:589)
- Chat 路径: `_cancel_bp_from_chat()` → `sm.cancel()`
- 两者调用相同的 `sm.cancel()` ✅

### 3.6 Agent 工具修复与新增

#### 3.6.1 handler.py 修复：`_handle_start` 增加 advance 调用

```python
async def _handle_start(self, params: dict, agent: Any, session: Any) -> str:
    # ... 现有的参数校验、实例创建逻辑不变 ...
    inst_id = self.state_manager.create_instance(
        bp_config, session.id, initial_input=input_data, run_mode=run_mode,
    )

    bus = getattr(session.context, "_sse_event_bus", None) if hasattr(session, "context") else None

    # 推送 bp_instance_created 事件（现有逻辑不变）
    created_event = {
        "type": "bp_instance_created",
        "instance_id": inst_id,
        "bp_id": bp_id,
        "bp_name": bp_config.name,
        "run_mode": run_mode.value,
        "subtasks": [{"id": s.id, "name": s.name} for s in bp_config.subtasks],
    }
    if bus:
        await bus.put(created_event)

    # [修复] 执行第一个子任务，推送事件到 event_bus
    async for event in self.engine.advance(inst_id, session):
        if bus:
            await bus.put(event)

    # [修复] 持久化（与 seecrab.py 一致）
    self._persist_to_session(inst_id, session)

    return f"✅ 已创建并执行 BP 实例「{bp_config.name}」(id={inst_id})"
```

**收敛验证**：
- UI 路径: bestpractice.py → `sm.create_instance()` + `engine.advance()` + `_persist_bp_to_session()`
- Agent 工具: handler.py → `sm.create_instance()` + `engine.advance()` + `_persist_to_session()`
- 相同的 engine 调用 ✅，事件通过 event_bus 传递到前端 ✅

**event_bus 时序说明**：`engine.advance()` 是一个 async generator，它 **直接 yield 事件**（不通过 event_bus）。其内部的 `_run_subtask_stream()` 会临时替换 `session.context._sse_event_bus` 为内部临时 queue 来捕获 sub-agent 的事件，消费后作为 yield 输出。因此 handler 的逻辑是：遍历 advance() yield 的每个事件 → 推送到预先捕获的原始 bus → adapter.transform() 从 bus 读取并发送到 SSE 前端。advance() 的 yield 和 bus 的 put 是两个独立机制，不会冲突。

#### 3.6.2 新增工具：`bp_next`

```python
async def _handle_next(self, params: dict, agent: Any, session: Any) -> str:
    instance_id = self._resolve_instance_id(params, session)
    if not instance_id:
        return "❌ 当前没有活跃的最佳实践任务"

    snap = self.state_manager.get(instance_id)
    if not snap:
        return "❌ BP 实例不存在"

    bus = getattr(session.context, "_sse_event_bus", None) if hasattr(session, "context") else None

    async for event in self.engine.advance(instance_id, session):
        if bus:
            await bus.put(event)

    self._persist_to_session(instance_id, session)
    return "✅ 子任务执行完成"
```

**收敛验证**：
- UI 路径: `POST /api/bp/next` → `engine.advance()` (bestpractice.py:472)
- Chat 路由: `_stream_bp_next_from_chat()` → `engine.advance()` (seecrab.py:254)
- Agent 工具: `_handle_next()` → `engine.advance()`
- 三个入口调用相同的 `engine.advance()` ✅

#### 3.6.3 新增工具：`bp_answer`

```python
async def _handle_answer(self, params: dict, agent: Any, session: Any) -> str:
    instance_id = self._resolve_instance_id(params, session)
    subtask_id = (params.get("subtask_id") or "").strip()
    data = params.get("data", {})
    if not instance_id or not subtask_id or not data:
        return "❌ 需要 subtask_id 和 data 参数"

    bus = getattr(session.context, "_sse_event_bus", None) if hasattr(session, "context") else None

    async for event in self.engine.answer(instance_id, subtask_id, data, session):
        if bus:
            await bus.put(event)

    self._persist_to_session(instance_id, session)
    return "✅ 参数已补充，子任务执行中"
```

**收敛验证**：
- UI 路径: `POST /api/bp/answer` → `engine.answer()` (bestpractice.py:539)
- Chat 路由: `_stream_bp_answer_from_chat()` → `engine.answer()`
- Agent 工具: `_handle_answer()` → `engine.answer()`
- 三个入口调用相同的 `engine.answer()` ✅

#### 3.6.4 新增工具：`bp_cancel`

```python
async def _handle_cancel(self, params: dict, agent: Any, session: Any) -> str:
    instance_id = self._resolve_instance_id(params, session)
    if not instance_id:
        return "❌ 当前没有活跃的最佳实践任务"

    snap = self.state_manager.get(instance_id)
    if not snap:
        return "❌ BP 实例不存在"

    bp_name = snap.bp_config.name if snap.bp_config else snap.bp_id
    self.state_manager.cancel(instance_id)

    # [新增] 触发 cooldown
    self.state_manager.set_cooldown(snap.session_id)

    bus = getattr(session.context, "_sse_event_bus", None) if hasattr(session, "context") else None
    if bus:
        await bus.put({
            "type": "bp_cancelled",
            "instance_id": instance_id,
            "bp_name": bp_name,
        })

    self._persist_to_session(instance_id, session)
    return f"✅ 已取消最佳实践任务「{bp_name}」(id={instance_id})"
```

**收敛验证**：
- UI 路径: `DELETE /api/bp/{instance_id}` → `sm.cancel()` (bestpractice.py:589)
- Chat 路由: `_cancel_bp_from_chat()` → `sm.cancel()`
- Agent 工具: `_handle_cancel()` → `sm.cancel()`
- 三个入口调用相同的 `sm.cancel()` ✅

#### 3.6.5 工具注册更新

```python
# handler.py
BP_TOOLS = ["bp_start", "bp_edit_output", "bp_switch_task", "bp_next", "bp_answer", "bp_cancel"]

# dispatch 表更新
dispatch = {
    "bp_start": self._handle_start,
    "bp_edit_output": self._handle_edit_output,
    "bp_switch_task": self._handle_switch_task,
    "bp_next": self._handle_next,        # 新增
    "bp_answer": self._handle_answer,     # 新增
    "bp_cancel": self._handle_cancel,     # 新增
}
```

#### 3.6.6 辅助方法：统一持久化（新增）

`_resolve_instance_id()` 已存在于 handler.py (lines 208-213)，直接复用。仅需新增 `_persist_to_session()`：

```python
# handler.py 新增
def _persist_to_session(self, instance_id: str, session: Any) -> None:
    """统一持久化方法，与 bestpractice.py 的 _persist_bp_to_session 逻辑一致。"""
    snap = self.state_manager.get(instance_id)
    if not snap or not session:
        return
    try:
        session.metadata["bp_state"] = self.state_manager.serialize_for_session(snap.session_id)
    except Exception:
        pass
```

> **注意**：此方法未调用 `session_manager.mark_dirty()`。这是有意为之——handler 运行在 Agent 推理循环内部，Agent 的 `chat_with_session_stream()` 完成后会统一执行 session 持久化（包括 dirty 检查）。与 bestpractice.py REST 端点不同（REST 需要显式 `mark_dirty()` 因为是独立的 HTTP 请求生命周期），Agent 工具路径的持久化由外层代码保证。

### 3.7 System Prompt 更新

#### 3.7.1 system_static.md 修改

**修改 1：修复不存在的工具引用**（现有 Bug）

当前 system_static.md 中引用了不存在的工具 `bp_supplement_input`（第 18 行、第 27 行），实际应为 `bp_answer`。`bp_supplement_input` 从未在 tool_definitions.py 或 handler.py 中定义过。需同步修正：

```diff
- 输入不完整时: 使用 ask_user 收集缺失字段，然后调用 bp_supplement_input 补充
+ 输入不完整时: 使用 ask_user 收集缺失字段，然后调用 bp_answer 补充

  ## 补充输入流程
  当 bp_start 或 bp_next 返回"输入不完整"的提示时:
  1. 使用 ask_user 向用户列出缺失的必要字段
  2. 收集用户提供的信息
- 3. 调用 bp_supplement_input 补充数据
+ 3. 调用 bp_answer(subtask_id=..., data={...}) 补充数据
  4. 调用 bp_next 继续执行
```

**修改 2：新增完整工具列表**

在"交互规则"段落后追加：

```markdown
## 可用工具

- `bp_start`: 启动最佳实践 (bp_id, input_data, run_mode)
- `bp_next`: 执行下一个子任务 (instance_id 可选，默认当前活跃实例)
- `bp_answer`: 补充子任务缺失的输入参数 (subtask_id, data)
- `bp_edit_output`: 修改已完成子任务的输出 (subtask_id, changes)
- `bp_cancel`: 取消当前最佳实践任务 (instance_id 可选)
- `bp_switch_task`: 切换到另一个挂起的 BP 实例 (target_instance_id)
```

#### 3.7.2 system_dynamic.md 增强状态感知指引

```markdown
# 当前最佳实践状态

${status_table}

${active_context}

## 用户意图路由指引

${intent_routing}
```

`intent_routing` 变量根据当前 BP 状态动态生成。当前 facade.py `get_dynamic_prompt_section()` 中已有此变量但逻辑较弱（仅处理 `prev_status == "done"` 的通用提示，无 `waiting_input` 分支，无 `bp_next`/`bp_cancel` 工具引导）。**需要 substantive 重写**：

```python
# facade.py get_dynamic_prompt_section() 增强
if active:
    current_status = list(active.subtask_statuses.values())[idx] if idx < total else ""

    if current_status == "waiting_input":
        intent_routing = (
            "当前子任务等待用户输入参数。\n"
            "如果用户提供了参数值，调用 bp_answer(subtask_id=..., data={...}) 补充。\n"
        )
    elif current_status == "done" or prev_status == "done":
        intent_routing = (
            "上一步已完成。用户可能想要:\n"
            "A) 继续下一步 → 调用 bp_next\n"
            "B) 修改上一步结果 → 调用 bp_edit_output(subtask_id=..., changes={...})\n"
            "C) 取消任务 → 调用 bp_cancel\n"
            "D) 询问其他问题（不涉及 BP 操作）\n"
        )
```

### 3.8 新增 Prompt 模板：bp_match.md

```markdown
# bestpractice/prompts/bp_match.md

你是一个意图分类器。判断用户消息是否匹配以下最佳实践模板。
如果匹配，同时从用户消息中提取第一步需要的参数。

## 可用的最佳实践

${bp_list}

## 用户消息

"${user_message}"

## 要求

1. 判断用户消息是否明确表达了想要完成某个最佳实践能处理的任务
2. 如果匹配，从用户消息中提取第一步需要的参数值（仅提取消息中明确提到的，不要推测）
3. confidence: 1.0=完全确定, 0.7=较确定, 0.5以下=不确定
4. 只返回 JSON，不要其他内容

匹配: {"matched": true, "bp_id": "<id>", "confidence": <0.0-1.0>, "extracted_input": {<参数>}}
不匹配: {"matched": false}
```

### 3.9 SeeCrabAdapter BP 事件透传修复（Critical）

#### 3.9.1 问题

当前 `seecrab_adapter.py` 的 `_process_event()` 方法仅处理有限的 BP 事件类型。`engine.advance()` yield 的大部分事件通过 Agent 工具路径（handler → event_bus → adapter）时会被静默丢弃或数据丢失：

| 事件类型 | 当前处理 | 问题 |
|---|---|---|
| `bp_instance_created` | pass through ✅ | 无 |
| `bp_progress` | flatten `data` wrapper | advance yield 的是**扁平格式**，无 `data` 包装 → 数据丢失 |
| `bp_subtask_output` | flatten `data` wrapper | 同上 |
| `bp_stale` | flatten `data` wrapper | 同上 |
| `bp_subtask_start` | return `[]` ❌ | 前端收不到子任务开始事件 |
| `bp_subtask_complete` | return `[]` ❌ | 前端收不到子任务完成事件 |
| `bp_waiting_next` | return `[]` ❌ | 前端收不到暂停点事件 |
| `bp_ask_user` | return `[]` ❌ | 前端收不到参数补充请求 |
| `bp_complete` | return `[]` ❌ | 前端收不到 BP 完成事件 |
| `bp_error` | return `[]` ❌ | 前端收不到 BP 错误事件 |
| `bp_cancelled` | 不存在 ❌ | 新增事件，未处理 |

#### 3.9.2 修复方案

在 `_process_event()` 中增加统一的 `bp_*` 事件处理规则：

```python
# seecrab_adapter.py _process_event() 中新增

# BP events — 统一透传
# advance() yield 的事件是扁平格式（无 data wrapper）
# _emit_*() 推送的事件有 data wrapper
# 两种格式都需要正确处理
if etype.startswith("bp_"):
    if "data" in event and isinstance(event["data"], dict):
        # data wrapper 格式（来自 _emit_progress 等）→ 展平
        return [{"type": etype, **event["data"]}]
    else:
        # 扁平格式（来自 advance() yield）→ 直接透传
        return [event]
```

此规则应放在 `_process_event()` 中所有现有 BP 事件处理逻辑的**替换位置**（删除原有的 `bp_instance_created`、`bp_progress`/`bp_subtask_output`/`bp_stale` 分支，统一为上述规则）。

#### 3.9.3 受影响的事件来源

| 来源 | 事件格式 | 示例 |
|---|---|---|
| `engine.advance()` yield | 扁平 `{"type": "bp_progress", "instance_id": ..., ...}` | `_build_progress_event()` |
| `engine._emit_progress()` bus.put | data wrapper `{"type": "bp_progress", "data": {...}}` | 旧代码路径（已不常用） |
| `handler._handle_cancel()` bus.put | 扁平 `{"type": "bp_cancelled", "instance_id": ..., ...}` | 新增 |

## 4. 收敛验证矩阵

实施完成后，5 个操作 × 3 个入口的收敛状态应为：

| 操作 | 入口A (REST API) | 入口B (Chat路由) | 入口C (Agent工具) | 收敛点 |
|---|---|---|---|---|
| Start | bestpractice.py → `engine.advance()` | seecrab.py `_stream_bp_start_from_chat()` → `engine.advance()` | handler.py `_handle_start()` → `engine.advance()` | `engine.advance()` ✅ |
| Next | bestpractice.py → `engine.advance()` | seecrab.py `_stream_bp_next_from_chat()` → `engine.advance()` | handler.py `_handle_next()` → `engine.advance()` | `engine.advance()` ✅ |
| Answer | bestpractice.py → `engine.answer()` | seecrab.py `_stream_bp_answer_from_chat()` → `engine.answer()` | handler.py `_handle_answer()` → `engine.answer()` | `engine.answer()` ✅ |
| Edit | bestpractice.py → `engine.handle_edit_output()` | Agent 推理（依赖 system_dynamic.md 意图引导，非确定性路由） | handler.py `_handle_edit_output()` → `engine.handle_edit_output()` | `engine.handle_edit_output()` ✅ |
| Cancel | bestpractice.py → `sm.cancel()` | seecrab.py `_cancel_bp_from_chat()` → `sm.cancel()` | handler.py `_handle_cancel()` → `sm.cancel()` | `sm.cancel()` ✅ |

**事件传递一致性**：
- 入口 A/B：engine 方法 yield 的事件直接在 SSE generator 中 yield
- 入口 C：engine 方法 yield 的事件 → handler 推送到 event_bus → adapter.transform() 合并到 SSE 流
- **前提**：adapter 的 `_process_event()` 已按 3.9 节修复，统一透传所有 `bp_*` 事件
- 前端收到的事件类型完全相同，UI 渲染逻辑无差异

**持久化一致性**：
- 入口 A/B：调用 `_persist_bp_to_session()`
- 入口 C：调用 `handler._persist_to_session()`（逻辑等价）
- 均将 `sm.serialize_for_session()` 写入 `session.metadata["bp_state"]`

## 5. 改动文件清单

| 文件 | 改动类型 | 具体改动 |
|---|---|---|
| `src/seeagent/bestpractice/facade.py` | 新增+修改 | 新增 `llm_match_bp_from_message()` 函数（含 `is_bp_offered()` 过滤和模板加载）；修改 `get_dynamic_prompt_section()` 中 `intent_routing` 逻辑 |
| `src/seeagent/bestpractice/handler.py` | 修复+新增 | 修复 `_handle_start()` 增加 `engine.advance()` 调用；新增 `_handle_next()`、`_handle_answer()`、`_handle_cancel()` 三个工具处理函数（cancel 含 cooldown 触发）；新增 `_persist_to_session()` 辅助方法；更新 `BP_TOOLS` 和 `dispatch` 表 |
| `src/seeagent/bestpractice/tool_definitions.py` | 修改 | 在 `BP_TOOL_DEFINITIONS` 列表中新增 `bp_next`、`bp_answer`、`bp_cancel` 三个工具的 JSON Schema 定义（Agent 通过此文件发现可用工具） |
| `src/seeagent/api/routes/seecrab.py` | 修改+新增 | 新增 `_BP_CANCEL_COMMANDS` 集合；扩展 `_BP_NEXT_COMMANDS` 集合；修改 `_match_bp_command()` 增加 cancel 返回值和 next+waiting_input 前置检查；新增 `_stream_bp_answer_from_chat()` 函数（使用 `_bp_busy_locks` 实现并发安全）；新增 `_cancel_bp_from_chat()` 函数（含 cooldown）；新增 `_llm_extract_answer_fields()` 函数；修改 bp_offer 处理逻辑增加 LLM 回退匹配和 `extracted_input` 传递；修改 start 命令处理逻辑优先使用预提取的 `extracted_input` |
| `src/seeagent/api/adapters/seecrab_adapter.py` | **修改** | **[新增文件]** 修改 `_process_event()` 统一透传所有 `bp_*` 事件类型，兼容扁平格式和 data wrapper 格式。删除原有的 `bp_instance_created`、`bp_progress`/`bp_subtask_output`/`bp_stale` 分支，替换为统一的 `etype.startswith("bp_")` 规则 |
| `src/seeagent/api/routes/bestpractice.py` | 修复 | **[新增]** 修复 `DELETE /{instance_id}` 端点持久化取消状态到 session.metadata；修改 `POST /api/bp/start` 优先使用 `pending_offer.extracted_input` 避免重复 LLM 提取 |
| `src/seeagent/bestpractice/engine.py` | 修复 | 修复 `_conform_output()` 中 `resp.text` → `resp.content`（`Brain.Response` 类只有 `content` 属性，无 `text`）。一行改动（约第 753 行） |
| `src/seeagent/bestpractice/prompts/bp_match.md` | 新增 | LLM BP 匹配的 prompt 模板（由 `_bp_prompt_loader.render("bp_match", ...)` 加载） |
| `src/seeagent/bestpractice/prompts/system_static.md` | 修改 | 修复 `bp_supplement_input` → `bp_answer`（不存在的工具引用）；新增 `bp_next`、`bp_answer`、`bp_cancel` 工具描述段落 |
| `src/seeagent/bestpractice/prompts/system_dynamic.md` | 不变 | 模板结构不变，`intent_routing` 变量内容由 facade.py 动态生成 |
| 前端 `apps/seecrab/src/types/index.ts` | 修改 | `SSEEventType` 联合类型新增 `'bp_cancelled'` |
| 前端 `apps/seecrab/src/stores/chat.ts` | 修改 | 新增 `bp_cancelled` 事件处理分支（调用 bpStore 清除对应实例状态） |
| 前端 `apps/seecrab/src/stores/bestpractice.ts` | 修改 | 新增 `handleCancelled()` 方法；修改实例恢复逻辑过滤 cancelled 状态实例（不设为 activeInstanceId） |

## 6. 风险与限制

### 6.1 LLM 匹配的延迟开销
- `llm_match_bp_from_message()` 增加约 1-2s 首次响应延迟
- 仅在关键词匹配失败且无活跃 BP 时触发，大部分场景不会命中
- 使用 `brain.think_lightweight()` 调用轻量模型，token 消耗约 300-500

### 6.2 Agent 工具路径的 advance 阻塞
- 当 Agent 调用 `bp_next` 或 `bp_answer` 工具时，`engine.advance()` 内部执行 sub-agent（可能耗时数分钟）
- 期间 Agent 推理循环被阻塞等待工具结果
- 这与 Agent 等待其他长时间运行的工具（如 web_search、browser）行为一致，不构成新问题

### 6.3 并发安全
- seecrab.py 的 `_busy_locks`（per conversation）防止同一会话的并发 chat 请求
- bestpractice.py 的 `_bp_busy_locks`（per session）防止同一会话的并发 BP 操作
- **两者是独立的锁命名空间**：Chat 路径同时持有两把锁（seecrab lock 先获取，bp lock 后获取），UI 路径只持有 bp lock
- handler.py 在 Agent 推理循环内执行，天然串行
- **关键设计**：Chat Answer 路径（`_stream_bp_answer_from_chat`）使用 bestpractice.py 的 `_bp_busy_locks`（而非 seecrab 的 `_busy_locks`），确保与 UI Answer 路径（`POST /api/bp/answer`）共享同一把锁，防止并发调用 `engine.answer()`
- 锁获取顺序始终为 seecrab → bestpractice，无死锁风险

### 6.4 前端 bp_cancelled 处理（三处改动）

1. **types/index.ts**：`SSEEventType` 联合类型新增 `'bp_cancelled'`，否则 TypeScript 编译报错

2. **chat.ts**：事件 switch 中新增 `case 'bp_cancelled'` 分支，调用 `bpStore.handleCancelled(event.instance_id)` 清除对应实例的 activeInstanceId 和 TaskProgressCard

3. **bestpractice.ts**：
   - 新增 `handleCancelled(instanceId: string)` 方法
   - 修改实例恢复逻辑（`restoreFromSession` 或类似方法）：恢复实例时如果 status 为 `cancelled`，不设为 `activeInstanceId`，避免页面刷新后显示已取消的任务进度

改动量极小（约 15-20 行），但不可省略，否则取消后 UI 不会更新。

### 6.5 LLM 匹配延迟期间的用户反馈

`llm_match_bp_from_message()` 增加约 1-2s 延迟。期间用户看不到任何反馈。建议在 LLM 匹配调用前发送一个 typing indicator 或 heartbeat 事件，避免用户以为系统无响应：

```python
# 可选：在 LLM 匹配前发送 heartbeat
yield f'data: {{"type": "heartbeat"}}\n\n'
bp_match = await llm_match_bp_from_message(message, session_id, brain)
```

### 6.6 REST API cancel 持久化遗漏（已有 Bug，本次修复）

`DELETE /api/bp/{instance_id}` 当前只调用 `sm.cancel()` 但不持久化到 `session.metadata["bp_state"]`。页面刷新后被取消的实例恢复为 active 状态。本次实施中一并修复（见 3.5.3 节）。

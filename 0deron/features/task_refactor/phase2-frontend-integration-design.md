# Phase 2 & 3: 前端集成 + 后端清理 设计文档

> 日期: 2026-03-22
> 前置: Phase 1 已完成 (Tasks 1-12, 186 tests passing)
> 依赖: `docs/plans/2026-03-21-bp-engine-orchestration-design.md` (v3)
> 需求: `0deron/features/task_refactor/requirements.md` (R1-R21)

---

## 一、概述

Phase 1 已在后端新增了 BPEngine 的 `advance()` 异步生成器、`_run_subtask_stream()`、`answer()` 方法，以及 `/bp/start`、`/bp/next`、`/bp/answer` 三个 SSE 端点。但前端仍通过 `/chat` → MasterAgent → `bp_continue` 旧路径执行 BP 子任务。

本设计覆盖:
- **Phase 2**: 前端切换到新 SSE 端点，绕过 MasterAgent 直连 BPEngine
- **Phase 3**: 后端清理旧代码（删除 4 个工具、execute_subtask()、return_direct、schema_chain.py）

---

## 二、核心流程变更

### 旧流程 (通过 MasterAgent)

```
进入下一步 → /api/seecrab/chat → MasterAgent LLM → bp_continue 工具 → engine.execute_subtask()
```

### 新流程 (直连 BPEngine)

```
进入下一步 → POST /api/bp/next SSE → engine.advance() (确定性代码，无 LLM)
```

---

## 三、完整时序图

```
用户发消息
  → POST /api/seecrab/chat (SSE)
  → MasterAgent 识别意图，调用 bp_start 工具
  → handler._handle_start() 创建实例
  → 推送 bp_instance_created 到 event_bus
  → SeeCrabAdapter 透传到前端
  → /chat SSE 流 done

前端收到 bp_instance_created
  → 显示 BPInstanceCreatedBlock 确认卡片
  → 用户点击"开始执行"

前端调用 POST /api/bp/next (SSE)
  → engine.advance() 执行第一个子任务
  → 事件流: bp_subtask_start → step_card* → bp_subtask_complete → bp_progress
  → (manual 模式) bp_waiting_next → done
  → 前端显示 SubtaskCompleteBlock "进入下一步"按钮

用户点击"进入下一步"
  → POST /api/bp/next (SSE) → 执行下一个子任务 (重复)

遇到输入缺失
  → engine.advance() yield bp_ask_user → done
  → 前端显示 BPAskUserBlock 表单
  → 用户填写提交
  → POST /api/bp/answer (SSE) → engine.answer() 合并数据 → 重新执行

全部完成
  → engine.advance() yield bp_complete → done
  → 前端显示完成状态
```

---

## 四、前端改动

### 4.1 SSEClient — 新增 `streamBP()` 方法

**文件**: `apps/seecrab/src/api/sse-client.ts`

新增通用 BP SSE 流方法，与 `sendMessage()` 平行:

```typescript
async streamBP(url: string, body: Record<string, unknown>): Promise<void>
```

设计要点:
- 独立的 `AbortController`（`bpAbortController`），不干扰 chat 流
- 事件通过 `chatStore.dispatchEvent()` 路由（BP 执行结果出现在聊天时间线中）
- SSE 解析逻辑复用 `sendMessage()` 的 `data: {json}\n\n` 模式
- 调用前由调用方负责 `chatStore.startNewReply()` 或 `addUserMessage()`
- 新增 `abortBP()` 方法用于取消 BP SSE 流

```typescript
// 使用示例
chatStore.addUserMessage('进入下一步')
await sseClient.streamBP('/api/bp/next', {
  instance_id: 'bp-xxx',
  session_id: 'sess-xxx',
})
```

### 4.2 类型定义扩展

**文件**: `apps/seecrab/src/types/index.ts`

#### SSEEventType 新增

```typescript
export type SSEEventType =
  | /* 现有类型 */
  | 'bp_instance_created'   // NEW: BP 实例已创建
  | 'bp_subtask_start'      // NEW: 子任务开始执行
  | 'bp_subtask_complete'   // NEW: 子任务完成
  | 'bp_waiting_next'       // NEW: Manual 模式暂停信号
  | 'bp_ask_user'           // NEW: 需要用户补充输入
  | 'bp_complete'           // NEW: BP 全部完成
  | 'bp_error'              // NEW: 子任务执行错误
```

#### BPSubtaskStatus 新增

```typescript
export type BPSubtaskStatus = 'pending' | 'current' | 'done' | 'failed' | 'stale' | 'waiting_input'
```

#### ReplyState 新增字段

```typescript
export interface ReplyState {
  /* 现有字段 */
  bpInstanceCreated: {
    instanceId: string
    bpId: string
    bpName: string
    runMode: BPRunMode
    subtasks: { id: string; name: string }[]
  } | null
  bpAskUser: {
    instanceId: string
    subtaskId: string
    subtaskName: string
    missingFields: string[]
    inputSchema?: Record<string, unknown>
  } | null
}
```

### 4.3 Chat Store — 新事件处理

**文件**: `apps/seecrab/src/stores/chat.ts`

`dispatchEvent()` 新增 case 分支:

| 事件类型 | 处理逻辑 |
|----------|----------|
| `bp_instance_created` | 存入 `reply.bpInstanceCreated`，更新 bpStore |
| `bp_subtask_start` | 更新 bpStore 子任务状态为 `current` |
| `bp_subtask_complete` | 更新 bpStore，设置 `reply.bpSubtaskOutput`（复用现有逻辑） |
| `bp_waiting_next` | 无额外操作（`done` 事件会使 `isDone=true`，按钮自然可用） |
| `bp_ask_user` | 存入 `reply.bpAskUser` |
| `bp_complete` | 更新 bpStore 状态为 completed |
| `bp_error` | 显示错误信息 |

注: `bp_progress`, `bp_subtask_output`, `bp_stale`, `step_card`, `thinking`, `ai_text`, `done` 已有处理，无需改动。

### 4.4 BP Store — 新增 actions

**文件**: `apps/seecrab/src/stores/bestpractice.ts`

```typescript
// 新增
function handleInstanceCreated(event: {
  instance_id: string; bp_id: string; bp_name: string;
  run_mode: string; subtasks: { id: string; name: string }[]
})

function handleSubtaskStart(instanceId: string, subtaskId: string)

function handleComplete(instanceId: string)
```

### 4.5 新组件: BPInstanceCreatedBlock.vue

**文件**: `apps/seecrab/src/components/chat/BPInstanceCreatedBlock.vue`

**触发条件**: `reply.bpInstanceCreated && reply.isDone`（等 /chat done 后才可点击）

**渲染内容**:
- 图标 + "已创建最佳实践「{bpName}」"
- 子任务列表预览: "1. Step1 → 2. Step2 → 3. Step3"
- 运行模式标签: "手动模式" / "自动模式"
- **按钮: "开始执行"** → 调用 `streamBP('/api/bp/next', {instance_id, session_id})`

**交互流程**:
1. 点击按钮后 disabled（防止重复点击）
2. `chatStore.addUserMessage('开始执行')` 添加用户消息
3. `sseClient.streamBP(...)` 开始 BP SSE 流
4. 后续事件更新 UI

### 4.6 新组件: BPAskUserBlock.vue (表单组件)

**文件**: `apps/seecrab/src/components/chat/BPAskUserBlock.vue`

**触发条件**: `reply.bpAskUser`

**渲染内容**:
- 图标 + "子任务「{subtaskName}」需要补充以下信息："
- 根据 `missingFields` + `inputSchema.properties` 动态生成表单:
  - `string` 类型 → `<input type="text">` 或 `<textarea>`（长字段）
  - `number` 类型 → `<input type="number">`
  - `boolean` 类型 → `<select>` (是/否)
  - `object`/`array` 类型 → `<textarea>` (JSON 输入)
- 字段 label: 使用 `properties[field].description` 或 field name
- **提交按钮: "提交"** → 调用 `streamBP('/api/bp/answer', {instance_id, subtask_id, data, session_id})`

**交互流程**:
1. 用户填写所有 required 字段
2. 点击提交 → 按钮 disabled
3. `chatStore.addUserMessage('补充数据: {...}')` 添加摘要消息
4. `sseClient.streamBP('/api/bp/answer', ...)` 开始 BP SSE 流
5. BPEngine.answer() 合并数据，重新执行子任务

### 4.7 修改 BotReply.vue

**文件**: `apps/seecrab/src/components/chat/BotReply.vue`

变更点:

1. **新增组件渲染**:
```vue
<BPInstanceCreatedBlock
  v-if="reply.bpInstanceCreated"
  :bp="reply.bpInstanceCreated"
  :disabled="!reply.isDone"
  @start="handleBpStart"
/>

<BPAskUserBlock
  v-if="reply.bpAskUser"
  :ask-user="reply.bpAskUser"
  @submit="handleBpAnswer"
/>
```

2. **`handleContinue()` 改造**:
```typescript
// 旧: 发消息到 /chat
await sseClient.sendMessage('进入下一步', convId, { thinking_mode: 'auto' })

// 新: 直连 /bp/next
chatStore.addUserMessage('进入下一步')
await sseClient.streamBP('/api/bp/next', {
  instance_id: bpStore.activeInstance?.instanceId,
  session_id: sessionStore.activeSessionId,
})
```

3. **新增 `handleBpStart()`**: BPInstanceCreatedBlock 的"开始执行"回调
4. **新增 `handleBpAnswer(data)`**: BPAskUserBlock 的"提交"回调

### 4.8 SubtaskCompleteBlock.vue

无结构性改动。"进入下一步" 按钮的 `disabled` 逻辑不变 (`!reply.isDone`)，因为:
- `/bp/next` SSE 流: `bp_subtask_complete` → `bp_waiting_next` → `done`
- `done` 后 `reply.isDone = true`，按钮可用

---

## 五、后端改动

### 5.1 SeeCrab Adapter — 透传 bp_instance_created

**文件**: `src/seeagent/api/adapters/seecrab_adapter.py`

在 `_process_event()` 中添加:

```python
# bp_instance_created 从 handler.py 推送，无 data wrapper，直接透传
if etype == "bp_instance_created":
    return [event]
```

注意: handler.py 的 `_handle_start()` 推送的事件结构无 `data` wrapper（直接 `{"type": "bp_instance_created", "instance_id": ..., ...}`），与 `_emit_progress()` 的 `{"type": "bp_progress", "data": {...}}` 不同。直接透传即可。

### 5.2 handler.py — 移除 4 个旧工具 (R6)

**文件**: `src/seeagent/bestpractice/handler.py`

删除:
- `BP_TOOLS` 列表中: `bp_continue`, `bp_get_output`, `bp_cancel`, `bp_supplement_input`
- `dispatch` 字典中: 对应的 4 个映射
- 方法: `_handle_continue()`, `_handle_get_output()`, `_handle_cancel()`, `_handle_supplement_input()`
- Helper: `_get_orchestrator()` (不再需要从 agent 获取 orchestrator)

保留:
```python
BP_TOOLS = ["bp_start", "bp_edit_output", "bp_switch_task"]
```

### 5.3 tool_definitions.py — 移除 4 个工具定义

**文件**: `src/seeagent/bestpractice/tool_definitions.py`

删除 `bp_continue`, `bp_get_output`, `bp_cancel`, `bp_supplement_input` 的 JSON Schema 定义。

### 5.4 engine.py — 删除 execute_subtask() 旧方法

**文件**: `src/seeagent/bestpractice/engine.py`

删除:
- `execute_subtask()` 方法 (约 130 行)
- `_resolve_input()` (已被 scheduler.resolve_input() 替代)
- `supplement_input()` (已被 answer() 替代)
- `_format_input_incomplete_result()` (旧工具返回格式)
- `_format_subtask_complete_result()` (旧工具返回格式)
- `_format_completion_result()` (旧工具返回格式)
- `reset_stale_if_needed()` (handler._handle_continue 专用)

保留:
- `advance()`, `_run_subtask_stream()`, `answer()` (Phase 1 新增)
- `handle_edit_output()` (bp_edit_output 工具仍在用)
- `_build_delegation_message()`, `_parse_output()` (advance() 依赖)
- `_check_input_completeness()` (advance() 依赖)
- `_validate_output_soft()` (handle_edit_output 依赖)
- `_persist()` (advance() 依赖)
- SSE emit 方法: `_emit_progress()`, `_emit_subtask_output()`, `_emit_stale()`, `_emit_delegate_card()` (可能仍被 handle_edit_output 使用)
- Summary 方法: `_build_summary()`, `_extract_summary_from_result()`

### 5.5 engine.py — 移除 schema_chain 依赖

`BPEngine.__init__()` 不再需要 `schema_chain` 参数:

```python
# 旧
def __init__(self, state_manager, schema_chain):
    self.schema_chain = schema_chain

# 新
def __init__(self, state_manager):
    # schema_chain 逻辑已迁移到 scheduler.derive_output_schema()
```

更新 `_run_subtask_stream()` 中的 `self.schema_chain.derive_output_schema()` 调用:
```python
# 旧
output_schema = self.schema_chain.derive_output_schema(bp_config, subtask_index)

# 新
scheduler = self._get_scheduler(bp_config, snap)
output_schema = scheduler.derive_output_schema(subtask.id)
```

更新 `_validate_output_soft()` 中的 `self.schema_chain.derive_output_schema()` 调用，同理。

### 5.6 删除 schema_chain.py

**文件**: `src/seeagent/bestpractice/schema_chain.py` — 删除

逻辑已完全迁移到 `scheduler.py` 的 `TaskScheduler.derive_output_schema()`。

### 5.7 facade.py — 移除 SchemaChain 创建

**文件**: `src/seeagent/bestpractice/facade.py`

`init_bp_system()` 中删除:
```python
from .schema_chain import SchemaChain
schema_chain = SchemaChain()
_bp_engine = BPEngine(state_manager=_bp_state_manager, schema_chain=schema_chain)
```

改为:
```python
_bp_engine = BPEngine(state_manager=_bp_state_manager)
```

### 5.8 return_direct 机制清理 (R9)

**文件**: `src/seeagent/core/reasoning_engine.py`, `src/seeagent/core/tool_executor.py`

搜索并删除所有 `_return_direct` 相关逻辑。此机制仅用于 `bp_continue` 在 manual 模式下强制终止 ReAct 循环，现在 `bp_continue` 已不存在。

### 5.9 测试更新

- 更新 `tests/unit/bestpractice/test_handler.py`: 移除对已删工具的测试
- 更新 `tests/unit/bestpractice/test_engine_*.py`: 移除 schema_chain mock
- 更新 `tests/unit/bestpractice/test_handler_start.py`: 保持（bp_start 仍存在）
- 更新 `facade` 相关测试: 移除 SchemaChain 相关

---

## 六、文件变更清单

### 前端 (apps/seecrab/src/)

| 文件 | 操作 | 说明 |
|------|------|------|
| `api/sse-client.ts` | 修改 | 新增 `streamBP()`, `abortBP()` |
| `types/index.ts` | 修改 | 新增事件类型、BPSubtaskStatus、ReplyState 字段 |
| `stores/chat.ts` | 修改 | dispatchEvent 新增 7 个 case |
| `stores/bestpractice.ts` | 修改 | 新增 handleInstanceCreated/SubtaskStart/Complete |
| `components/chat/BotReply.vue` | 修改 | handleContinue 改造 + 新组件集成 |
| `components/chat/BPInstanceCreatedBlock.vue` | 新建 | BP 创建确认卡片 |
| `components/chat/BPAskUserBlock.vue` | 新建 | BP ask_user 表单组件 |

### 后端 (src/seeagent/)

| 文件 | 操作 | 说明 |
|------|------|------|
| `api/adapters/seecrab_adapter.py` | 修改 | 透传 bp_instance_created |
| `bestpractice/handler.py` | 修改 | 删除 4 个工具方法，BP_TOOLS 缩减为 3 |
| `bestpractice/tool_definitions.py` | 修改 | 删除 4 个工具定义 |
| `bestpractice/engine.py` | 修改 | 删除 execute_subtask 及相关旧方法，移除 schema_chain 依赖 |
| `bestpractice/schema_chain.py` | 删除 | 逻辑已迁移到 scheduler.py |
| `bestpractice/facade.py` | 修改 | 移除 SchemaChain 创建 |
| `core/reasoning_engine.py` | 修改 | 删除 _return_direct 逻辑 |
| `core/tool_executor.py` | 修改 | 删除 _return_direct 逻辑 |

### 测试

| 文件 | 操作 | 说明 |
|------|------|------|
| `tests/unit/bestpractice/test_handler.py` | 修改 | 移除已删工��的测试 |
| `tests/unit/bestpractice/test_engine_*.py` | 修改 | 移除 schema_chain mock |
| `tests/unit/bestpractice/test_facade.py` | 修改 | 移除 SchemaChain 相关 |

---

## 七、风险与注意事项

1. **bp_start 工具仍保留**: MasterAgent 仍通过 `bp_start` 创建实例。只是创建后的执行由前端直连 BPEngine，不再经过 MasterAgent。

2. **busy-lock 互斥**: `/chat` 和 `/bp/*` 的 busy-lock 使用不同 key 空间（chat 用自己的锁，bp 用 `_bp_busy_locks`）。R14 时序约束（/chat done 后才能调 /bp/next）由前端按钮 disabled 逻辑保证。

3. **向后兼容**: Phase 3 清理后，旧版前端如果还发 "进入下一步" 到 /chat，MasterAgent 找不到 bp_continue 工具会回复错误。需确保前端先更新。

4. **event_bus 事件格式差异**: handler.py 推送的 `bp_instance_created` 无 `data` wrapper，直接是 flat dict。而 `_emit_progress` 等使用 `{"type": "...", "data": {...}}` 格式。adapter 处理时需注意。

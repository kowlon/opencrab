# BP Chat 意图路由 — 需求说明

> 日期: 2026-03-24

## 1. 问题描述

当前最佳实践(BP)系统的完整运行流程只能通过前端 UI 操作触发：

- 点击 BPOfferBlock 卡片进入最佳实践模式
- BPAskUserBlock 表单填写子任务缺失参数
- SubtaskCompleteBlock 按钮点击"下一步"或"修改结果"

用户在聊天框输入自然语言时，系统无法自动识别意图并走对应的 BP 流程。**需要让 chat 输入和 UI 操作的后续处理逻辑完全统一。**

## 2. 需求目标

用户在聊天框输入文字后，系统能自动判断意图，走与 UI 操作相同的后续处理流程。

## 3. 需求范围：5 个交互操作

### 3.1 BP 触发与启动

**UI 操作**：用户消息命中关键词 → 系统展示 bp_offer 卡片 → 用户点击确认 → 启动 BP

**Chat 期望**：用户输入"帮我写一篇公众号科技文章" → 系统识别匹配"内容创作流水线" BP → 展示 bp_offer 卡片让用户确认 → 用户确认后启动

**确认的行为**：
- 匹配后先展示 offer 让用户确认，不直接启动
- 意图识别采用两级策略：先关键词匹配，匹配不到再用 LLM 匹配
- LLM 匹配时根据第一个子任务的 input_schema 提取参数，预填到 input_data 中
- 预填的参数在启动后如果已覆盖 required 字段则子任务直接执行，否则走正常的 bp_ask_user 补充流程

### 3.2 参数补充（Ask User）

**UI 操作**：子任务缺少必填参数 → BPAskUserBlock 展示表单 → 用户填写提交 → POST /api/bp/answer

**Chat 期望**：子任务缺少参数时，用户直接在聊天框输入参数值（如"科技"或"领域是科技"）→ 系统识别为参数回答 → 走与 UI 提交相同的 engine.answer() 流程

**确认的行为**：
- 缺失字段判断必须基于完整的解析后输入（包含 initial_input、上游子任务输出、已补充的 supplemented_inputs），不能仅看 supplemented_inputs
- 单字段缺失时直接赋值，多字段缺失时用 LLM 提取
- waiting_input 期间用户可通过 cancel 命令跳出（cancel 命令优先级高于 answer 路由）

### 3.3 进入下一步（Next）

**UI 操作**：手动模式子任务完成后 → SubtaskCompleteBlock 展示"下一步"按钮 → 用户点击 → 发送"进入下一步"消息

**Chat 期望**：用户输入"下一步"、"继续"、"好的继续"等 → 系统识别为 next 操作 → 走相同的 engine.advance() 流程

**备注**：此操作当前已部分支持（硬编码命令"下一步"等），需扩展更多自然语言表达。

**需新增的命令表达**：在现有 `"进入下一步"、"下一步"、"继续执行"、"继续"` 基础上，补充：`"好的继续"、"好的，下一步"、"开始下一步"、"执行下一步"`

**条件匹配命令（仅在有活跃 BP 时生效）**：`"好"、"没问题"、"ok"、"确认"、"好的下一步"` — 这些日常用语过于宽泛，若无活跃 BP 时也拦截会阻止消息到达 Agent，造成不良用户体验。因此仅在有活跃 BP 实例时才匹配为 next 命令，否则放行到 Agent。

**边界处理**：若用户说"下一步"但当前子任务处于 `waiting_input` 状态（等待参数），应提示用户先补充参数，不能静默返回空响应。

### 3.4 修改子任务输出（Edit）

**UI 操作**：SubtaskCompleteBlock "修改结果"按钮 → 打开编辑面板 → 用户修改 JSON 字段 → PUT /api/bp/edit-output

**Chat 期望**：用户输入"把标题改成xxx" → 系统识别为 edit 意图 → 提取修改内容 → 走相同的 engine.handle_edit_output() 流程

### 3.5 取消/终止 BP（Cancel）

**UI 操作**：当前无 UI 操作（仅有 REST API DELETE /api/bp/{instance_id}）

**Chat 期望**：用户输入"取消最佳实践"、"终止任务"等 → 系统识别为 cancel → 走相同的 sm.cancel() 流程

**确认的行为**：
- 取消后应触发 cooldown（默认 3 轮），防止用户下一条消息立即重新触发同一 BP 匹配
- **cooldown 必须在每次用户消息时递减**：seecrab_chat() 路由入口处调用 `sm.tick_cooldown(session_id)`，否则 cooldown 永久生效（当前代码 `tick_cooldown()` 已定义但从未被调用）
- 取消后必须持久化状态到 session.metadata（当前 REST API 的 DELETE 端点有此遗漏，需一并修复）
- Cancel 命令优先级高于 waiting_input 路由，确保用户在等待参数期间也能取消

## 4. 核心约束

### 4.1 处理逻辑统一

**无论入口是 UI 还是 Chat，最终都必须调用相同的 engine 方法，产出相同的事件序列，前端渲染相同的 UI 组件。**

- Start → `engine.advance()`
- Next → `engine.advance()`
- Answer → `engine.answer()`
- Edit → `engine.handle_edit_output()`
- Cancel → `sm.cancel()`

### 4.2 架构约束：混合路由

- **路由层**（seecrab.py，在 Agent 之前拦截）负责：BP 触发匹配（关键词 + LLM 回退）、硬编码命令（下一步、取消）、状态感知路由（waiting_input 时的参数回答）
- **Agent**（通过 system prompt + 工具调用）负责：路由层未拦截的复杂意图（修改输出、模糊表达等），利用 Agent 的完整对话上下文做判断

### 4.3 前端改动（最小化）

现有前端 UI 组件已覆盖大部分 BP 事件类型的渲染。需新增以下最小改动：
1. `bp_cancelled` 事件处理：chat.ts 新增事件分支 + bestpractice.ts 清除实例状态
2. `SSEEventType` 类型定义：types/index.ts 新增 `'bp_cancelled'` 类型
3. 页面刷新恢复：bestpractice.ts 恢复实例时需过滤 cancelled 状态，不设为 activeInstanceId

### 4.4 SeeCrabAdapter 事件透传

当前 `seecrab_adapter.py` 的 `_process_event()` 仅处理部分 BP 事件类型（`bp_instance_created`、`bp_progress`、`bp_subtask_output`、`bp_stale`）。以下事件被静默丢弃：`bp_subtask_start`、`bp_subtask_complete`、`bp_waiting_next`、`bp_ask_user`、`bp_complete`、`bp_error`、`bp_cancelled`。

**Agent 工具路径（入口 C）的所有 BP 事件都经过 adapter，因此必须修复 adapter 使其透传全部 bp_* 事件类型。** 这是入口 C 正常工作的前提。

### 4.5 并发安全：统一 BP 操作锁

seecrab.py 的 `_busy_locks`（per conversation）和 bestpractice.py 的 `_bp_busy_locks`（per session）是两个独立的锁命名空间。Chat Answer 路径和 UI Answer 路径分别使用不同的锁，无法防止同一子任务被并发操作。**需要确保 Chat 路径的 BP 操作也使用 bestpractice.py 的 `_bp_busy_locks`。**

### 4.6 Cooldown 递减机制

`BPStateManager.tick_cooldown()` 已实现但当前**从未被任何代码调用**。若 cooldown 只设置不递减，则取消后 BP 匹配永久失效。**seecrab_chat() 路由入口必须在每次用户消息时调用 `tick_cooldown()`**。

### 4.7 服务重启后的状态恢复

`seecrab_chat()` 中所有 BP 状态查询（`get_active()`、检查 `waiting_input`）依赖内存中的 `BPStateManager._instances`。服务重启后内存为空，这些查询全部返回空。**seecrab_chat() 路由入口必须在所有 BP 状态查询之前统一调用 `_ensure_bp_restored()`**，从 `session.metadata["bp_state"]` 恢复状态。一次调用覆盖后续所有分支（start、next、cancel、answer、matching）。

### 4.8 参数提取失败的 fallback

Chat Answer 路径用 LLM 从用户消息提取参数时可能失败（LLM 返回空 JSON 或异常）。若提取结果为空仍调用 `engine.answer()`，会导致 subtask 重置为 PENDING → 再次缺字段 → 再次 `bp_ask_user` → 死循环。**提取结果为空时必须给出友好提示，不调用 engine.answer()**。

## 5. 不在范围内

- 不改变现有 UI 操作的流程和行为
- 不新增前端 UI 组件（复用现有组件）
- 不修改 engine.py 的核心执行逻辑（但需修复 `_conform_output()` 中 `resp.text` → `resp.content` 的属性名错误，一行改动）
- 不修改 bestpractice.py REST API 端点的主体行为（但需修复 DELETE 端点的状态持久化遗漏）

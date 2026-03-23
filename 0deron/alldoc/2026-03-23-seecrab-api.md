# SeeCrab 前端使用的 API 参考

> 生成日期: 2026-03-23
> 说明: 仅包含 `apps/seecrab/` 前端实际调用的后端 API
> 前端源码: `apps/seecrab/src/api/http-client.ts`, `apps/seecrab/src/api/sse-client.ts`

---

## 目录

- [1. SeeCrab 对话 (SeeCrab Chat)](#1-seecrab-对话-seecrab-chat)
- [2. 最佳实践工作流 (Best Practice)](#2-最佳实践工作流-best-practice)
- [3. 通用对话 — 取消 (Chat Cancel)](#3-通用对话--取消-chat-cancel)
- [4. 会话标题生成 (Session Title)](#4-会话标题生成-session-title)
- [附录 A: SSE 事件类型汇总](#附录-a-sse-事件类型汇总)
- [附录 B: 前端 API 客户端](#附录-b-前端-api-客户端)

---

## 1. SeeCrab 对话 (SeeCrab Chat)

**路由文件**: `src/seeagent/api/routes/seecrab.py`
**前缀**: `/api/seecrab`
**前端调用**: `http-client.ts`, `sse-client.ts`

### POST /api/seecrab/chat

流式对话端点（SSE）。主要的聊天接口，支持 BP（最佳实践）检测。

| 字段 | 说明 |
|------|------|
| **请求** | `SeeCrabChatRequest` |
| **响应** | SSE 流（text/event-stream） |
| **前端调用** | `sse-client.ts` → `sendMessage()` |

**请求体**:
```json
{
  "message": "用户消息",
  "conversation_id": "可选，续接会话",
  "agent_profile_id": "可选，指定 Agent 档案",
  "endpoint": "可选，指定 LLM 端点",
  "thinking_mode": "auto|on|off",
  "thinking_depth": "low|medium|high",
  "plan_mode": false,
  "attachments": [{"type": "image", "url": "/api/uploads/xxx"}],
  "client_id": "客户端标识（忙锁用）"
}
```

**SSE 事件类型**: 见[附录 A](#附录-a-sse-事件类型汇总)

### GET /api/seecrab/sessions

列出所有 SeeCrab 会话。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "sessions": [{ "id", "title", "updated_at", "message_count", "last_message" }] }` |
| **前端调用** | `http-client.ts` → `listSessions()` |

### GET /api/seecrab/sessions/{session_id}

获取指定会话的完整历史。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "session_id", "title", "messages": [{ "role", "content", "timestamp", "metadata", "reply_state"? }] }` |
| **前端调用** | `http-client.ts` → `getSession(id)` |

### POST /api/seecrab/sessions

创建新会话。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "session_id": "新会话ID" }` |
| **前端调用** | `http-client.ts` → `createSession()` |

### PATCH /api/seecrab/sessions/{session_id}

更新会话元数据（如标题）。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "title": "新标题" }` |
| **响应** | `{ "status": "ok" }` |
| **前端调用** | `http-client.ts` → `updateSession(id, data)` |

### DELETE /api/seecrab/sessions/{session_id}

删除会话。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "status": "ok" }` 或错误 |
| **前端调用** | `http-client.ts` → `deleteSession(id)` |

### POST /api/seecrab/answer

提交用户回答（对 ask_user 事件的应答）。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "conversation_id": "...", "answer": "...", "client_id"?: "..." }` |
| **响应** | `{ "status", "conversation_id", "answer", "hint" }` |
| **前端调用** | `http-client.ts` → `submitAnswer()` |
| **备注** | 实际回答通过新的 /chat 消息发送，此接口仅确认 |

---

## 2. 最佳实践工作流 (Best Practice)

**路由文件**: `src/seeagent/api/routes/bestpractice.py`
**前缀**: `/api/bp`
**前端调用**: `http-client.ts` (状态/模式), `BotReply.vue` (SSE 流)

### GET /api/bp/status

获取指定会话的所有 BP 实例状态。

| 字段 | 说明 |
|------|------|
| **查询参数** | `session_id` |
| **响应** | `{ "instances": [{ "instance_id", "bp_id", "bp_name", "status", "run_mode", "current_subtask_index", "subtask_statuses", "subtask_outputs" }], "active_id"? }` |
| **前端调用** | `http-client.ts` → `getBPStatus(sessionId)` |

### PUT /api/bp/run-mode

切换 BP 执行模式（手动/自动）。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "instance_id": "...", "run_mode": "manual"|"auto" }` |
| **响应** | `{ "success": bool, "run_mode"?: "..." }` |
| **前端调用** | `http-client.ts` → `setBPRunMode(instanceId, runMode)` |

### PUT /api/bp/edit-output

编辑子任务输出（Chat-to-Edit）。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "instance_id", "subtask_id", "changes": {} }` |
| **响应** | `{ "success": bool, ... }` |
| **前端调用** | `http-client.ts` → `editBPOutput(instanceId, subtaskId, changes)` |

### POST /api/bp/start

创建并启动 BP 实例（SSE 流）。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "bp_id", "session_id", "input_data"?, "run_mode"?, "user_message"? }` |
| **响应** | SSE 流，事件: `bp_instance_created`, `bp_progress`, `bp_subtask_output`, `bp_subtask_complete`, `bp_ask_user`, `done`, `error` |
| **前端调用** | `BotReply.vue` → `streamBP('/api/bp/start', ...)` |

### POST /api/bp/next

推进 BP 到下一个子任务（SSE 流）。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "instance_id", "session_id", "user_message"? }` |
| **响应** | SSE 流（同 /start） |
| **前端调用** | `BotReply.vue` → `streamBP('/api/bp/next', ...)` |

### POST /api/bp/answer

提交 BP ask_user 的回答（SSE 流）。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "instance_id", "subtask_id", "data": {}, "session_id", "user_message"? }` |
| **响应** | SSE 流（同 /start） |
| **前端调用** | `BotReply.vue` → `streamBP('/api/bp/answer', ...)` |

> **注意**: SeeCrab 不直接调用 `GET /api/bp/output/{instance_id}/{subtask_id}` 和 `DELETE /api/bp/{instance_id}`，这两个端点属于通用 API。

---

## 3. 通用对话 — 取消 (Chat Cancel)

**路由文件**: `src/seeagent/api/routes/chat.py`

### POST /api/chat/cancel

取消正在进行的对话。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "conversation_id", "reason" }` |
| **响应** | `{ "status": "cancelled" }` |
| **前端调用** | `sse-client.ts` → `cancelTask(conversationId)` |

> **注意**: `chat.py` 中的其他端点（/api/chat, /api/chat/busy, /api/chat/answer, /api/chat/skip, /api/chat/insert, /api/agents/sub-tasks, /api/agents/sub-records）不被 SeeCrab 前端直接调用，它们服务于 Setup-Center 或 IM 渠道。

---

## 4. 会话标题生成 (Session Title)

**路由文件**: `src/seeagent/api/routes/sessions.py`

### POST /api/sessions/generate-title

使用 LLM 自动生成会话标题。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "message": "首条消息", "reply"?: "AI回复" }` |
| **响应** | `{ "title": "生成的标题" }` |
| **前端调用** | `http-client.ts` → `generateTitle(message, reply)` |

> **注意**: `sessions.py` 中的其他端点（列表/历史/删除/追加消息）不被 SeeCrab 前端直接调用，SeeCrab 使用 `/api/seecrab/sessions/*` 替代。

---

## 附录 A: SSE 事件类型汇总

以下事件通过 `POST /api/seecrab/chat` 和 `POST /api/bp/*` 的 SSE 流返回：

| 事件类型 | 说明 | 数据格式 |
|----------|------|----------|
| `session_title` | 会话标题（首轮对话自动生成） | `{ "title": "..." }` |
| `thinking` | 流式思维内容 | `{ "content": "..." }` |
| `step_card` | 工具执行步骤卡片 | `{ "tool_name", "input", "output"?, "status" }` |
| `ai_text` | 助手文本内容（流式） | `{ "content": "..." }` |
| `timer_update` | TTFT/总耗时 | `{ "ttft_ms"?, "total_ms"? }` |
| `plan_checklist` | ReAct 计划清单 | `{ "items": [...] }` |
| `ask_user` | 需要用户输入 | `{ "question", "options"? }` |
| `agent_header` | Agent 信息头 | `{ "profile_id", "name", "icon" }` |
| `bp_offer` | 检测到可用 BP | `{ "bp_id", "bp_name", "description" }` |
| `bp_instance_created` | BP 实例已创建 | `{ "instance_id", "bp_id" }` |
| `bp_progress` | BP 进度更新 | `{ "instance_id", "current_subtask_index", "status" }` |
| `bp_subtask_start` | 子任务开始 | `{ "subtask_id", "subtask_name" }` |
| `bp_subtask_output` | 子任务输出 | `{ "subtask_id", "output", "outputSchema"? }` |
| `bp_subtask_complete` | 子任务完成 | `{ "subtask_id", "status" }` |
| `bp_waiting_next` | 等待用户确认下一步（手动模式） | `{ "instance_id" }` |
| `bp_ask_user` | BP 需要用户输入 | `{ "subtask_id", "question", "schema"? }` |
| `bp_stale` | BP 状态需要刷新 | `{ "instance_id" }` |
| `bp_complete` | BP 执行完成 | `{ "instance_id" }` |
| `bp_error` | BP 执行出错 | `{ "instance_id", "error" }` |
| `done` | 流结束 | `{}` |
| `error` | 异常 | `{ "message": "..." }` |

---

## 附录 B: 前端 API 客户端

**路径**: `apps/seecrab/src/api/`

### http-client.ts

HTTP API 封装，基础路径为 `/api/seecrab`（部分路由使用不同前缀）。

| 方法 | HTTP 请求 | 说明 |
|------|-----------|------|
| `listSessions()` | GET `/api/seecrab/sessions` | 列出会话 |
| `createSession()` | POST `/api/seecrab/sessions` | 创建会话 |
| `deleteSession(id)` | DELETE `/api/seecrab/sessions/{id}` | 删除会话 |
| `getSession(id)` | GET `/api/seecrab/sessions/{id}` | 获取会话历史 |
| `updateSession(id, data)` | PATCH `/api/seecrab/sessions/{id}` | 更新会话标题 |
| `submitAnswer(conversationId, answer)` | POST `/api/seecrab/answer` | 提交用户回答 |
| `generateTitle(message, reply)` | POST `/api/sessions/generate-title` | LLM 生成标题 |
| `getBPStatus(sessionId)` | GET `/api/bp/status?session_id=...` | BP 状态查询 |
| `setBPRunMode(instanceId, runMode)` | PUT `/api/bp/run-mode` | BP 模式切换 |
| `editBPOutput(instanceId, subtaskId, changes)` | PUT `/api/bp/edit-output` | 编辑子任务输出 |

### sse-client.ts

SSE 流式客户端，处理 JSON Lines 格式。

| 方法 | HTTP 请求 | 说明 |
|------|-----------|------|
| `sendMessage(message, conversationId?, options?)` | POST `/api/seecrab/chat` | 流式对话 |
| `cancelTask(conversationId)` | POST `/api/chat/cancel` | 取消任务 |
| `streamBP(url, body)` | POST (动态 URL) | BP SSE 流 (`/api/bp/start`, `/api/bp/next`, `/api/bp/answer`) |

---

## API 端点汇总

| 分类 | 端点数 |
|------|--------|
| SeeCrab 对话 | 7 |
| 最佳实践 (BP) | 6 |
| 通用对话 (仅 cancel) | 1 |
| 会话标题 (仅 generate-title) | 1 |
| **合计** | **15 个 API 端点** |

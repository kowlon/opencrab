# SeeCrab API 接口文档

> 版本: 1.1
> 更新日期: 2026-03-30
> Base URL: `http://{host}:18900`
> 说明: 本文档仅覆盖 SeeCrab 前端实际调用的接口

### v1.1 变更摘要

| 变更类型 | 接口 | 说明 |
|---------|------|------|
| **新增** | `GET /api/bp/configs` | BP 模板列表 |
| **新增** | `GET /api/bp/configs/{bp_id}` | BP 模板详情 |
| **新增** | `GET /api/bp/instances/stats` | BP 实例统计 |
| **调整** | `GET /api/bp/instances` | 合并原 `/status` 和 `/list`，增加 `bp_name`/`progress` 字段 |
| **调整** | `GET /api/bp/instances/{instance_id}` | 原 `/instance/{id}`，单复数统一 |
| **调整** | `GET /api/bp/instances/{id}/output/{sid}` | 原 `/output/{id}/{sid}`，嵌套资源 |
| **调整** | `PUT /api/bp/instances/{id}/run-mode` | 原 `/run-mode`，instance_id 移入路径参数 |
| **调整** | `PUT /api/bp/instances/{id}/output` | 原 `/edit-output`，instance_id 移入路径参数 |
| **调整** | `DELETE /api/bp/instances/{id}` | 原 `DELETE /{id}`，归入 instances |
| **增强** | `GET /api/bp/instances` | 新增 `session_title`/`subtask_count`/`done_count`/`subtask_names` 字段，支持实例列表导航 |

---

## 目录

1. [对话 (Chat)](#1-对话-chat)
   - 1.1 [SSE 流式对话](#11-sse-流式对话)
   - 1.2 [取消任务](#12-取消任务)
   - 1.3 [提交 ask_user 回答](#13-提交-ask_user-回答)
2. [会话管理 (Session)](#2-会话管理-session)
   - 2.1 [获取会话列表](#21-获取会话列表)
   - 2.2 [创建会话](#22-创建会话)
   - 2.3 [获取会话详情](#23-获取会话详情)
   - 2.4 [更新会话](#24-更新会话)
   - 2.5 [删除会话](#25-删除会话)
   - 2.6 [生成会话标题](#26-生成会话标题)
3. [最佳实践 (Best Practice)](#3-最佳实践-best-practice)
   - 3.1 [启动 BP 实例 (SSE)](#31-启动-bp-实例-sse)
   - 3.2 [执行下一步 (SSE)](#32-执行下一步-sse)
   - 3.3 [提交 BP 用户回答 (SSE)](#33-提交-bp-用户回答-sse)
   - 3.4 [获取 BP 模板列表 [v1.1 新增]](#34-获取-bp-模板列表-v11-新增)
   - 3.5 [获取 BP 模板详情 [v1.1 新增]](#35-获取-bp-模板详情-v11-新增)
   - 3.6 [查询 BP 实例统计 [v1.1 新增]](#36-查询-bp-实例统计-v11-新增)
   - 3.7 [查询 BP 实例列表 [v1.1 调整]](#37-查询-bp-实例列表-v11-调整)
   - 3.8 [查询 BP 实例详情 [v1.1 调整]](#38-查询-bp-实例详情-v11-调整)
   - 3.9 [查询子任务输出 [v1.1 调整]](#39-查询子任务输出-v11-调整)
   - 3.10 [切换运行模式 [v1.1 调整]](#310-切换运行模式-v11-调整)
   - 3.11 [编辑子任务输出 [v1.1 调整]](#311-编辑子任务输出-v11-调整)
   - 3.12 [取消 BP 实例 [v1.1 调整]](#312-取消-bp-实例-v11-调整)
4. [SSE 事件类型参考](#4-sse-事件类型参考)
5. [前端调用示例汇总](#5-前端调用示例汇总)

---

## 1. 对话 (Chat)

### 1.1 SSE 流式对话

发送用户消息，返回 SSE 流式事件。支持 BP 命令自动识别（"进入最佳实践"、"进入下一步"等）。

| 项目 | 说明 |
|------|------|
| **URL** | `POST /api/seecrab/chat` |
| **Content-Type** | `application/json` |
| **响应类型** | `text/event-stream` (SSE) |

**请求体 (SeeCrabChatRequest):**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message` | string | 否 | 用户消息文本 |
| `conversation_id` | string \| null | 否 | 会话 ID，为空则自动生成 `seecrab_{uuid}` |
| `agent_profile_id` | string \| null | 否 | Agent 配置 ID |
| `endpoint` | string \| null | 否 | LLM endpoint 覆盖 |
| `thinking_mode` | string \| null | 否 | 思考模式 (如 `"auto"`) |
| `thinking_depth` | string \| null | 否 | 思考深度 (`"low"` / `"medium"` / `"high"`) |
| `plan_mode` | boolean | 否 | 启用 Plan 模式，默认 `false` |
| `attachments` | AttachmentInfo[] \| null | 否 | 附件列表 |
| `client_id` | string \| null | 否 | 客户端标识，用于 busy-lock |

**SSE 事件格式:**
```
data: {"type": "<event_type>", ...}\n\n
```

**响应状态码:**

| 状态码 | 说明 |
|--------|------|
| 200 | 成功，返回 SSE 流 |
| 409 | 会话正忙 (busy-lock 冲突) |
| 503 | Agent 未初始化 |

**SSE 事件类型:** 见 [第 4 节](#4-sse-事件类型参考)

**Response 返回示例:**

成功时 (HTTP 200)，返回 SSE 流，典型的事件序列如下:

```
data: {"type": "session_title", "session_id": "seecrab_abc123", "title": "帮我分析一下市场趋势"}

data: {"type": "thinking", "content": "用户想要了解市场趋势，我需要", "agent_id": "main", "done": false}

data: {"type": "thinking", "content": "收集相关数据并进行分析...", "agent_id": "main", "done": true}

data: {"type": "timer_update", "phase": "ttft", "state": "done", "value": 1.23}

data: {"type": "plan_checklist", "steps": [{"index": 0, "title": "收集市场数据", "status": "running"}, {"index": 1, "title": "趋势分析", "status": "pending"}]}

data: {"type": "step_card", "step_id": "step_001", "title": "web_search: 2024年市场趋势", "status": "running", "source_type": "tool", "card_type": "search", "duration": null, "plan_step_index": 0, "agent_id": "main", "input": {"query": "2024年市场趋势分析"}, "output": null, "absorbed_calls": []}

data: {"type": "step_card", "step_id": "step_001", "title": "web_search: 2024年市场趋势", "status": "completed", "source_type": "tool", "card_type": "search", "duration": 2.15, "plan_step_index": 0, "agent_id": "main", "input": {"query": "2024年市场趋势分析"}, "output": "找到 5 条相关结果...", "absorbed_calls": []}

data: {"type": "ai_text", "content": "根据最新数据分析，", "agent_id": "main"}

data: {"type": "ai_text", "content": "2024年市场呈现以下趋势：\n\n1. **数字化转型加速**...", "agent_id": "main"}

data: {"type": "timer_update", "phase": "total", "state": "done", "value": 8.56}

data: {"type": "done"}
```

当触发 BP 推荐时，SSE 流中会包含 `bp_offer` 事件:

```
data: {"type": "session_title", "session_id": "seecrab_abc123", "title": "帮我做一份市场分析报告"}

data: {"type": "bp_offer", "bp_id": "market_analysis", "bp_name": "市场分析", "subtasks": [{"id": "data_collection", "name": "数据收集"}, {"id": "trend_analysis", "name": "趋势分析"}, {"id": "report_gen", "name": "报告生成"}], "default_run_mode": "manual"}

data: {"type": "done"}
```

会话正忙 (HTTP 409):

```json
{
  "error": "Another request is already processing this conversation"
}
```

Agent 未初始化 (HTTP 503):

```json
{
  "error": "Agent not initialized"
}
```

---

### 1.2 取消任务

取消指定会话的当前运行任务。

| 项目 | 说明 |
|------|------|
| **URL** | `POST /api/chat/cancel` |
| **Content-Type** | `application/json` |

**请求体:**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `conversation_id` | string | 是 | 会话 ID |
| `reason` | string | 否 | 取消原因，默认 `"用户点击停止按钮"` |

**Response 返回示例:**

成功 (HTTP 200):

```json
{
  "status": "ok",
  "action": "cancel",
  "reason": "用户点击停止按钮"
}
```

Agent 未初始化:

```json
{
  "status": "error",
  "message": "Agent not initialized"
}
```

---

### 1.3 提交 ask_user 回答

提交对 `ask_user` 事件的回答。实际操作中建议通过 `/api/seecrab/chat` 发送带相同 `conversation_id` 的新消息。

| 项目 | 说明 |
|------|------|
| **URL** | `POST /api/seecrab/answer` |
| **Content-Type** | `application/json` |

**请求体 (SeeCrabAnswerRequest):**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `conversation_id` | string | 是 | 会话 ID |
| `answer` | string | 是 | 用户回答 |
| `client_id` | string \| null | 否 | 客户端标识 |

**Response 返回示例:**

成功 (HTTP 200):

```json
{
  "status": "ok",
  "conversation_id": "seecrab_a1b2c3d4e5f6",
  "answer": "确认执行",
  "hint": "Please send the answer as a new /api/seecrab/chat message with the same conversation_id"
}
```

---

## 2. 会话管理 (Session)

### 2.1 获取会话列表

| 项目 | 说明 |
|------|------|
| **URL** | `GET /api/seecrab/sessions` |

**查询参数:**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `limit` | number | 否 | 50 | 分页大小 |
| `offset` | number | 否 | 0 | 分页偏移 |
| `title` | string | 否 | — | 按标题模糊搜索（大小写不敏感） |

**Response 返回示例:**

成功 (HTTP 200) — 包含多个会话:

```json
{
  "total": 2,
  "limit": 50,
  "offset": 0,
  "sessions": [
    {
      "id": "seecrab_a1b2c3d4e5f6",
      "title": "帮我分析一下市场趋势",
      "pinned": false,
      "icon": "",
      "updated_at": 1711267200000,
      "message_count": 8,
      "last_message": "根据最新数据分析，2024年市场呈现以下趋势：1. 数字化转型加速..."
    },
    {
      "id": "seecrab_f7e8d9c0b1a2",
      "title": "写一份项目计划",
      "pinned": false,
      "icon": "",
      "updated_at": 1711180800000,
      "message_count": 3,
      "last_message": "好的，我来帮你制定项目计划。首先需要明确几个关键信息..."
    }
  ]
}
```

Session Manager 不可用或无会话时返回空列表:

```json
{
  "total": 0,
  "limit": 50,
  "offset": 0,
  "sessions": []
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `total` | number | 匹配条件的会话总数 |
| `limit` | number | 分页大小 |
| `offset` | number | 分页偏移 |
| `sessions[].id` | string | 会话 ID (chat_id) |
| `sessions[].title` | string | 会话标题，未设置时为空字符串 `""` |
| `sessions[].pinned` | boolean | 是否置顶，默认 `false` |
| `sessions[].icon` | string | 会话图标标识，默认 `""` |
| `sessions[].updated_at` | number | 最后活跃时间 (毫秒时间戳) |
| `sessions[].message_count` | number | 消息数量 |
| `sessions[].last_message` | string | 最近消息预览 (≤80 字符) |

---

### 2.2 创建会话

| 项目 | 说明 |
|------|------|
| **URL** | `POST /api/seecrab/sessions` |

**Response 返回示例:**

成功 (HTTP 200):

```json
{
  "session_id": "seecrab_a1b2c3d4e5f6"
}
```

---

### 2.3 获取会话详情

获取会话完整消息历史，用于 SSE 重连后的状态恢复。

| 项目 | 说明 |
|------|------|
| **URL** | `GET /api/seecrab/sessions/{session_id}` |

**路径参数:**

| 参数 | 类型 | 说明 |
|------|------|------|
| `session_id` | string | 会话 ID |

**Response 返回示例:**

成功 (HTTP 200) — 含用户消息和带 reply_state 的助手回复:

```json
{
  "session_id": "seecrab_a1b2c3d4e5f6",
  "title": "帮我分析一下市场趋势",
  "pinned": false,
  "icon": "",
  "messages": [
    {
      "role": "user",
      "content": "帮我分析一下市场趋势",
      "timestamp": 1711267200,
      "metadata": {}
    },
    {
      "role": "assistant",
      "content": "根据最新数据分析，2024年市场呈现以下趋势：\n\n1. **数字化转型加速**\n2. **AI 应用落地**\n3. **可持续发展投资增长**",
      "timestamp": 1711267210,
      "metadata": {},
      "reply_state": {
        "thinking": "用户想要了解市场趋势，我需要收集相关数据并进行分析...",
        "step_cards": [
          {
            "step_id": "step_001",
            "title": "web_search: 2024年市场趋势",
            "status": "completed",
            "source_type": "tool",
            "card_type": "search",
            "duration": 2.15,
            "plan_step_index": 0,
            "agent_id": "main",
            "input": {"query": "2024年市场趋势分析"},
            "output": "找到 5 条相关结果...",
            "absorbed_calls": []
          }
        ],
        "agent_thinking": {},
        "agent_summaries": {},
        "plan_checklist": [
          {"index": 0, "title": "收集市场数据", "status": "completed"},
          {"index": 1, "title": "趋势分析", "status": "completed"}
        ],
        "timer": {"ttft": 1.23, "total": 8.56},
        "bp_progress": null,
        "bp_subtask_output": null
      }
    }
  ]
}
```

含 BP 流程的会话详情示例:

```json
{
  "session_id": "seecrab_f7e8d9c0b1a2",
  "title": "帮我做一份市场分析报告",
  "pinned": false,
  "icon": "",
  "messages": [
    {
      "role": "user",
      "content": "帮我做一份市场分析报告",
      "timestamp": 1711267200,
      "metadata": {}
    },
    {
      "role": "assistant",
      "content": "检测到您的需求匹配最佳实践「市场分析」...",
      "timestamp": 1711267205,
      "metadata": {},
      "reply_state": {
        "bp_offer": {
          "bp_id": "market_analysis",
          "bp_name": "市场分析",
          "subtasks": [
            {"id": "data_collection", "name": "数据收集"},
            {"id": "trend_analysis", "name": "趋势分析"}
          ]
        }
      }
    },
    {
      "role": "user",
      "content": "最佳实践模式",
      "timestamp": 1711267210,
      "metadata": {}
    },
    {
      "role": "assistant",
      "content": "[BP] 「市场分析」进度: 1/2",
      "timestamp": 1711267250,
      "metadata": {},
      "reply_state": {
        "thinking": "",
        "step_cards": [],
        "agent_thinking": {},
        "agent_summaries": {},
        "plan_checklist": null,
        "timer": {"ttft": null, "total": null},
        "bp_progress": {
          "type": "bp_progress",
          "instance_id": "bp_inst_001",
          "bp_name": "市场分析",
          "statuses": {"data_collection": "done", "trend_analysis": "pending"},
          "subtasks": [{"id": "data_collection", "name": "数据收集"}, {"id": "trend_analysis", "name": "趋势分析"}],
          "current_subtask_index": 1,
          "run_mode": "manual",
          "status": "active"
        },
        "bp_subtask_output": {
          "type": "bp_subtask_complete",
          "instance_id": "bp_inst_001",
          "subtask_id": "data_collection",
          "subtask_name": "数据收集",
          "output": {"market_size": "500亿", "growth_rate": "12%"},
          "summary": "已完成市场数据收集"
        },
        "bp_subtask_outputs": [
          {
            "type": "bp_subtask_output",
            "instance_id": "bp_inst_001",
            "subtask_id": "data_collection",
            "subtask_name": "数据收集",
            "output": {"market_size": "500亿", "growth_rate": "12%"},
            "summary": "已完成市场数据收集"
          }
        ],
        "bp_instance_created": {
          "type": "bp_instance_created",
          "instance_id": "bp_inst_001",
          "bp_id": "market_analysis",
          "bp_name": "市场分析",
          "run_mode": "manual",
          "subtasks": [
            {"id": "data_collection", "name": "数据收集"},
            {"id": "trend_analysis", "name": "趋势分析"}
          ]
        },
        "bp_ask_user": null
      }
    }
  ]
}
```

会话不存在 (HTTP 404):

```json
{
  "error": "Session not found"
}
```

Session Manager 不可用 (HTTP 503):

```json
{
  "error": "Session manager not available"
}
```

**响应状态码:**

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 404 | 会话不存在 |
| 503 | Session Manager 不可用 |

---

### 2.4 更新会话

更新会话元数据 (如标题)。

| 项目 | 说明 |
|------|------|
| **URL** | `PATCH /api/seecrab/sessions/{session_id}` |
| **Content-Type** | `application/json` |

**请求体 (SeeCrabSessionUpdateRequest):**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | string \| null | 否 | 新标题 |
| `pinned` | boolean \| null | 否 | 是否置顶 |
| `icon` | string \| null | 否 | 会话图标标识 |

**Response 返回示例:**

成功 (HTTP 200):

```json
{
  "status": "ok"
}
```

会话不存在 (HTTP 404):

```json
{
  "error": "Session not found"
}
```

**响应状态码:**

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 404 | 会话不存在 |
| 503 | Session Manager 不可用 |

---

### 2.5 删除会话

| 项目 | 说明 |
|------|------|
| **URL** | `DELETE /api/seecrab/sessions/{session_id}` |

**Response 返回示例:**

成功 (HTTP 200):

```json
{
  "status": "ok"
}
```

会话不存在 (HTTP 404):

```json
{
  "error": "Session not found"
}
```

**响应状态码:**

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 404 | 会话不存在 |
| 503 | Session Manager 不可用 |

---

### 2.6 生成会话标题

通过 LLM 根据首条消息生成简洁标题。

| 项目 | 说明 |
|------|------|
| **URL** | `POST /api/sessions/generate-title` |
| **Content-Type** | `application/json` |

> 注意: 该接口前缀为 `/api/sessions`，不是 `/api/seecrab/sessions`

**请求体 (GenerateTitleRequest):**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message` | string | 是 | 用户第一条消息 |
| `reply` | string | 否 | AI 回复摘要 (可选) |

**Response 返回示例:**

成功 (HTTP 200) — LLM 生成标题:

```json
{
  "title": "Q1销售数据分析"
}
```

LLM 不可用时回退到截取用户消息:

```json
{
  "title": "帮我分析一下Q1的销售数据"
}
```

---

## 3. 最佳实践 (Best Practice)

### 3.1 启动 BP 实例 (SSE)

创建 BP 实例并执行第一个子任务，返回 SSE 流。

| 项目 | 说明 |
|------|------|
| **URL** | `POST /api/bp/start` |
| **Content-Type** | `application/json` |
| **响应类型** | `text/event-stream` (SSE) |

**请求体:**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `bp_id` | string | 是 | 最佳实践配置 ID |
| `session_id` | string | 是 | 会话 ID |
| `input_data` | object | 否 | 初始输入数据，默认 `{}` |
| `run_mode` | string | 否 | 运行模式: `"manual"` (默认) / `"auto"` |
| `user_message` | string | 否 | 用户交互消息，用于持久化 |

**SSE 事件流:** 首先推送 `bp_instance_created`，然后推送子任务执行事件。

**Response 返回示例:**

成功时 (HTTP 200)，返回 SSE 流，典型的事件序列如下:

```
data: {"type": "bp_instance_created", "instance_id": "bp_inst_001", "bp_id": "market_analysis", "bp_name": "市场分析", "run_mode": "manual", "subtasks": [{"id": "data_collection", "name": "数据收集"}, {"id": "trend_analysis", "name": "趋势分析"}, {"id": "report_gen", "name": "报告生成"}]}

data: {"type": "bp_subtask_start", "instance_id": "bp_inst_001", "subtask_id": "data_collection"}

data: {"type": "thinking", "content": "开始收集市场数据...", "agent_id": "main"}

data: {"type": "step_card", "step_id": "bp_step_001", "title": "web_search: 市场规模数据", "status": "running", "source_type": "tool", "card_type": "search", "duration": null, "agent_id": "main", "input": {"query": "市场规模数据"}, "output": null, "absorbed_calls": []}

data: {"type": "step_card", "step_id": "bp_step_001", "title": "web_search: 市场规模数据", "status": "completed", "source_type": "tool", "card_type": "search", "duration": 1.85, "agent_id": "main", "input": {"query": "市场规模数据"}, "output": "搜索完成", "absorbed_calls": []}

data: {"type": "bp_subtask_output", "instance_id": "bp_inst_001", "subtask_id": "data_collection", "subtask_name": "数据收集", "output": {"market_size": "500亿", "growth_rate": "12%", "top_players": ["A公司", "B公司", "C公司"]}, "output_schema": {"type": "object", "properties": {"market_size": {"type": "string"}, "growth_rate": {"type": "string"}}}, "summary": "市场规模500亿，增长率12%"}

data: {"type": "bp_subtask_complete", "instance_id": "bp_inst_001", "subtask_id": "data_collection"}

data: {"type": "bp_progress", "instance_id": "bp_inst_001", "bp_name": "市场分析", "statuses": {"data_collection": "done", "trend_analysis": "pending", "report_gen": "pending"}, "subtasks": [{"id": "data_collection", "name": "数据收集"}, {"id": "trend_analysis", "name": "趋势分析"}, {"id": "report_gen", "name": "报告生成"}], "current_subtask_index": 1, "run_mode": "manual", "status": "active"}

data: {"type": "bp_waiting_next", "instance_id": "bp_inst_001"}

data: {"type": "done"}
```

当首个子任务缺少必填字段时，会发出 `bp_ask_user`:

```
data: {"type": "bp_instance_created", "instance_id": "bp_inst_002", "bp_id": "competitor_analysis", "bp_name": "竞品分析", "run_mode": "manual", "subtasks": [{"id": "target_select", "name": "目标选择"}, {"id": "comparison", "name": "对比分析"}]}

data: {"type": "bp_ask_user", "instance_id": "bp_inst_002", "subtask_id": "target_select", "subtask_name": "目标选择", "missing_fields": ["company_name", "industry"], "input_schema": {"type": "object", "properties": {"company_name": {"type": "string", "description": "公司名称"}, "industry": {"type": "string", "description": "所属行业"}}}}

data: {"type": "done"}
```

BP 配置不存在 (HTTP 404):

```json
{
  "error": "BP 'unknown_bp' not found"
}
```

会话正忙 (HTTP 409):

```json
{
  "error": "Session is busy"
}
```

BP 系统未初始化 (HTTP 500):

```json
{
  "error": "BP system not initialized"
}
```

**响应状态码:**

| 状态码 | 说明 |
|--------|------|
| 200 | 成功，返回 SSE 流 |
| 404 | BP 配置不存在 |
| 409 | 会话正忙 |
| 500 | BP 系统未初始化 |

---

### 3.2 执行下一步 (SSE)

推进 BP 到下一个子任务，返回 SSE 流。

| 项目 | 说明 |
|------|------|
| **URL** | `POST /api/bp/next` |
| **Content-Type** | `application/json` |
| **响应类型** | `text/event-stream` (SSE) |

**请求体:**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `instance_id` | string | 是 | BP 实例 ID |
| `session_id` | string | 是 | 会话 ID |
| `user_message` | string | 否 | 用户交互消息 |

**Response 返回示例:**

成功时 (HTTP 200)，返回 SSE 流:

```
data: {"type": "bp_subtask_start", "instance_id": "bp_inst_001", "subtask_id": "trend_analysis"}

data: {"type": "thinking", "content": "基于上一步收集的数据，开始进行趋势分析...", "agent_id": "main"}

data: {"type": "step_card", "step_id": "bp_step_002", "title": "analysis: 趋势分析", "status": "running", "source_type": "tool", "card_type": "analysis", "duration": null, "agent_id": "main", "input": {"data_source": "data_collection output"}, "output": null, "absorbed_calls": []}

data: {"type": "step_card", "step_id": "bp_step_002", "title": "analysis: 趋势分析", "status": "completed", "source_type": "tool", "card_type": "analysis", "duration": 3.42, "agent_id": "main", "input": {"data_source": "data_collection output"}, "output": "分析完成", "absorbed_calls": []}

data: {"type": "bp_subtask_output", "instance_id": "bp_inst_001", "subtask_id": "trend_analysis", "subtask_name": "趋势分析", "output": {"trend": "上升", "forecast": "预计增长15%", "key_factors": ["政策利好", "技术创新"]}, "summary": "市场呈上升趋势，预计增长15%"}

data: {"type": "bp_subtask_complete", "instance_id": "bp_inst_001", "subtask_id": "trend_analysis"}

data: {"type": "bp_progress", "instance_id": "bp_inst_001", "bp_name": "市场分析", "statuses": {"data_collection": "done", "trend_analysis": "done", "report_gen": "pending"}, "subtasks": [{"id": "data_collection", "name": "数据收集"}, {"id": "trend_analysis", "name": "趋势分析"}, {"id": "report_gen", "name": "报告生成"}], "current_subtask_index": 2, "run_mode": "manual", "status": "active"}

data: {"type": "bp_waiting_next", "instance_id": "bp_inst_001"}

data: {"type": "done"}
```

当所有子任务执行完毕时，流中会包含 `bp_complete`:

```
data: {"type": "bp_subtask_start", "instance_id": "bp_inst_001", "subtask_id": "report_gen"}

data: {"type": "bp_subtask_output", "instance_id": "bp_inst_001", "subtask_id": "report_gen", "subtask_name": "报告生成", "output": {"report_url": "/tmp/market_report.pdf"}, "summary": "报告已生成"}

data: {"type": "bp_subtask_complete", "instance_id": "bp_inst_001", "subtask_id": "report_gen"}

data: {"type": "bp_progress", "instance_id": "bp_inst_001", "bp_name": "市场分析", "statuses": {"data_collection": "done", "trend_analysis": "done", "report_gen": "done"}, "subtasks": [{"id": "data_collection", "name": "数据收集"}, {"id": "trend_analysis", "name": "趋势分析"}, {"id": "report_gen", "name": "报告生成"}], "current_subtask_index": 3, "run_mode": "manual", "status": "completed"}

data: {"type": "bp_complete", "instance_id": "bp_inst_001"}

data: {"type": "done"}
```

**响应状态码:**

| 状态码 | 说明 |
|--------|------|
| 200 | 成功，返回 SSE 流 |
| 409 | 会话正忙 |
| 500 | BP 系统未初始化 |

---

### 3.3 提交 BP 用户回答 (SSE)

提交 `bp_ask_user` 要求的缺失字段数据，并继续执行，返回 SSE 流。

| 项目 | 说明 |
|------|------|
| **URL** | `POST /api/bp/answer` |
| **Content-Type** | `application/json` |
| **响应类型** | `text/event-stream` (SSE) |

**请求体:**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `instance_id` | string | 是 | BP 实例 ID |
| `subtask_id` | string | 是 | 子任务 ID |
| `data` | object | 是 | 用户补充的数据 |
| `session_id` | string | 是 | 会话 ID |
| `user_message` | string | 否 | 用户交互消息 |

**Response 返回示例:**

成功时 (HTTP 200)，返回 SSE 流:

```
data: {"type": "bp_subtask_start", "instance_id": "bp_inst_002", "subtask_id": "target_select"}

data: {"type": "thinking", "content": "用户提供了公司名称和行业信息，开始选择分析目标...", "agent_id": "main"}

data: {"type": "bp_subtask_output", "instance_id": "bp_inst_002", "subtask_id": "target_select", "subtask_name": "目标选择", "output": {"company_name": "ABC公司", "industry": "科技", "competitors": ["X公司", "Y公司"]}, "summary": "已确定分析目标: ABC公司 (科技行业)"}

data: {"type": "bp_subtask_complete", "instance_id": "bp_inst_002", "subtask_id": "target_select"}

data: {"type": "bp_progress", "instance_id": "bp_inst_002", "bp_name": "竞品分析", "statuses": {"target_select": "done", "comparison": "pending"}, "subtasks": [{"id": "target_select", "name": "目标选择"}, {"id": "comparison", "name": "对比分析"}], "current_subtask_index": 1, "run_mode": "manual", "status": "active"}

data: {"type": "bp_waiting_next", "instance_id": "bp_inst_002"}

data: {"type": "done"}
```

**响应状态码:**

| 状态码 | 说明 |
|--------|------|
| 200 | 成功，返回 SSE 流 |
| 409 | 会话正忙 |
| 500 | BP 系统未初始化 |

---

### 3.4 获取 BP 模板列表 [v1.1 新增]

返回所有已配置的 BP 模板概要信息。

| 项目 | 说明 |
|------|------|
| **URL** | `GET /api/bp/configs` |

**查询参数:** 无

**Response 返回示例:**

成功 (HTTP 200) — 有配置的 BP 模板:

```json
{
  "total": 5,
  "configs": [
    {
      "id": "camera-feature-pipeline",
      "name": "相机特征检索出图",
      "description": "从相机搜索到预处理检索再到表格图片生成的三阶段流程",
      "subtask_count": 3,
      "default_run_mode": "manual",
      "trigger_types": ["command", "context"],
      "triggers": [
        { "type": "command", "pattern": "执行相机特征检索出图", "conditions": [], "cron": "" },
        { "type": "context", "pattern": "", "conditions": ["相机", "预处理", "特征检索", "表格图片"], "cron": "" }
      ]
    },
    {
      "id": "market-research-report",
      "name": "市场调研报告",
      "description": "从信息收集到分析再到报告生成的完整流程",
      "subtask_count": 4,
      "default_run_mode": "manual",
      "trigger_types": ["command", "context", "ui_click"],
      "triggers": [
        { "type": "command", "pattern": "执行市场调研", "conditions": [], "cron": "" },
        { "type": "context", "pattern": "", "conditions": ["市场", "调研", "行业分析"], "cron": "" },
        { "type": "ui_click", "pattern": "market-research", "conditions": [], "cron": "" }
      ]
    }
  ]
}
```

无 BP 配置时:

```json
{
  "total": 0,
  "configs": []
}
```

**输出字段说明:**

| 字段 | 类型 | 说明 |
|------|------|------|
| `total` | number | 模板总数 |
| `configs[].id` | string | 模板唯一 ID |
| `configs[].name` | string | 展示名称 |
| `configs[].description` | string | 模板描述 |
| `configs[].subtask_count` | number | 子任务数量 |
| `configs[].default_run_mode` | string | 默认运行模式: `"manual"` / `"auto"` |
| `configs[].trigger_types` | string[] | 触发方式列表: `command`/`context`/`cron`/`event`/`ui_click` |
| `configs[].triggers` | object[] | 触发器详情数组 |
| `configs[].triggers[].type` | string | 触发器类型 |
| `configs[].triggers[].pattern` | string | 匹配模式（command 类型使用） |
| `configs[].triggers[].conditions` | string[] | 上下文条件关键词（context 类型使用） |
| `configs[].triggers[].cron` | string | cron 表达式（cron 类型使用） |

---

### 3.5 获取 BP 模板详情 [v1.1 新增]

返回单个 BP 模板的完整配置信息，包括子任务定义、触发条件、输出 Schema 等。

| 项目 | 说明 |
|------|------|
| **URL** | `GET /api/bp/configs/{bp_id}` |

**路径参数:**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `bp_id` | string | 是 | BP 模板 ID |

**Response 返回示例:**

成功 (HTTP 200):

```json
{
  "id": "camera-feature-pipeline",
  "name": "相机特征检索出图",
  "description": "从相机搜索到预处理检索再到表格图片生成的三阶段流程",
  "subtask_count": 3,
  "default_run_mode": "manual",
  "trigger_types": ["command", "context"],
  "triggers": [
    {
      "type": "command",
      "pattern": "执行相机特征检索出图",
      "conditions": [],
      "cron": ""
    },
    {
      "type": "context",
      "pattern": "",
      "conditions": ["相机", "预处理", "特征检索", "表格图片"],
      "cron": ""
    }
  ],
  "subtasks": [
    {
      "id": "camera-search",
      "name": "相机搜索",
      "description": "根据用户场景描述搜索候选相机",
      "agent_profile": "camera-locator",
      "input_schema": {
        "type": "object",
        "properties": {
          "query": {
            "type": "string",
            "description": "相机搜索关键词，如园区东门"
          }
        },
        "required": ["query"]
      },
      "depends_on": [],
      "input_mapping": {},
      "timeout_seconds": null,
      "max_retries": 0
    },
    {
      "id": "feature-extraction",
      "name": "特征检索",
      "description": "对指定相机创建预处理任务，轮询完成后执行文本特征检索",
      "agent_profile": "feature-extractor",
      "input_schema": {
        "type": "object",
        "properties": {
          "camera_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "要处理的相机ID列表"
          },
          "feature_text": {
            "type": "string",
            "description": "文本特征描述"
          }
        },
        "required": ["camera_ids", "feature_text"]
      },
      "depends_on": [],
      "input_mapping": {},
      "timeout_seconds": null,
      "max_retries": 0
    },
    {
      "id": "render-image",
      "name": "结果出图",
      "description": "将特征检索结果渲染为带图片缩略图的表格 PNG",
      "agent_profile": "result-visualizer",
      "input_schema": {
        "type": "object",
        "properties": {
          "feature_result": {
            "type": "object",
            "description": "特征检索阶段输出的完整JSON对象"
          },
          "output_image_path": {
            "type": "string",
            "description": "输出图片路径"
          }
        },
        "required": ["feature_result"]
      },
      "depends_on": [],
      "input_mapping": {},
      "timeout_seconds": null,
      "max_retries": 0
    }
  ],
  "final_output_schema": {
    "type": "object",
    "properties": {
      "title": {"type": "string"},
      "camera_candidates": {"type": "array", "items": {"type": "object"}},
      "feature_result": {"type": "object"},
      "table_image_path": {"type": "string"},
      "summary": {"type": "string"}
    },
    "required": ["title", "feature_result", "table_image_path"]
  }
}
```

**输出字段说明:**

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 模板 ID |
| `name` | string | 展示名称 |
| `description` | string | 模板描述 |
| `subtask_count` | number | 子任务数量（与 `/configs` 列表一致） |
| `default_run_mode` | string | `"manual"` / `"auto"` |
| `trigger_types` | string[] | 触发方式列表（与 `/configs` 列表一致） |
| `triggers[].type` | string | 触发类型 |
| `triggers[].pattern` | string | 命令模式匹配字符串 |
| `triggers[].conditions` | string[] | 上下文关键词条件 |
| `triggers[].cron` | string | cron 表达式（仅 type=cron） |
| `subtasks[].id` | string | 子任务 ID |
| `subtasks[].name` | string | 子任务名称 |
| `subtasks[].description` | string | 子任务描述 |
| `subtasks[].agent_profile` | string | Agent Profile ID |
| `subtasks[].input_schema` | object | 输入参数 JSON Schema |
| `subtasks[].depends_on` | string[] | 上游依赖子任务 ID |
| `subtasks[].input_mapping` | object | 上游输出到输入的字段映射 |
| `subtasks[].timeout_seconds` | number \| null | 超时秒数 |
| `subtasks[].max_retries` | number | 最大重试次数 |
| `final_output_schema` | object \| null | 最终输出 JSON Schema |

BP 配置不存在 (HTTP 404):

```json
{
  "error": "BP config 'unknown_bp' not found"
}
```

BP 系统未初始化 (HTTP 500):

```json
{
  "error": "BP system not initialized"
}
```

**响应状态码:**

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 404 | BP 配置不存在 |
| 500 | BP 系统未初始化 |

---

### 3.6 查询 BP 实例统计 [v1.1 新增]

返回 BP 实例的聚合统计数据（总数 + 按状态分组），用于仪表板展示。

| 项目 | 说明 |
|------|------|
| **URL** | `GET /api/bp/instances/stats` |

**查询参数:**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 否 | 按会话过滤 |
| `bp_id` | string | 否 | 按 BP 模板过滤 |

**Response 返回示例:**

成功 (HTTP 200) — 全局统计:

```json
{
  "total": 42,
  "by_status": {
    "active": 3,
    "suspended": 5,
    "completed": 30,
    "cancelled": 4
  }
}
```

成功 (HTTP 200) — 按 session 过滤:

```json
{
  "total": 5,
  "by_status": {
    "active": 1,
    "suspended": 0,
    "completed": 3,
    "cancelled": 1
  }
}
```

**输出字段说明:**

| 字段 | 类型 | 说明 |
|------|------|------|
| `total` | number | 匹配条件的实例总数 |
| `by_status.active` | number | 活跃实例数 |
| `by_status.suspended` | number | 挂起实例数 |
| `by_status.completed` | number | 已完成实例数 |
| `by_status.cancelled` | number | 已取消实例数 |

BP 存储未初始化 (HTTP 500):

```json
{
  "error": "BP storage not initialized"
}
```

**响应状态码:**

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 500 | BP 存储未初始化 |

---

### 3.7 查询 BP 实例列表 [v1.1 调整]

> **v1.1 变更**: 合并原 `GET /api/bp/status` 和 `GET /api/bp/list` 为统一端点。路径从 `/api/bp/status` 和 `/api/bp/list` 调整为 `/api/bp/instances`。新增 `bp_name`、`progress` 字段。

返回 BP 实例列表，支持多维度过滤和分页。

| 项目 | 说明 |
|------|------|
| **URL** | `GET /api/bp/instances` |

**查询参数:**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `session_id` | string | 否 | — | 按会话过滤。有值时走内存优先（含自动恢复逻辑） |
| `status` | string | 否 | — | 按状态过滤: `active`/`suspended`/`completed`/`cancelled` |
| `bp_id` | string | 否 | — | 按 BP 模板 ID 过滤 |
| `limit` | number | 否 | 50 | 分页大小 |
| `offset` | number | 否 | 0 | 分页偏移 |

**Response 返回示例:**

成功 (HTTP 200) — 按 session 查询（替代原 `/api/bp/status`）:

```json
{
  "total": 2,
  "limit": 50,
  "offset": 0,
  "active_id": "bp-f29a234d",
  "instances": [
    {
      "instance_id": "bp-f29a234d",
      "bp_id": "camera-feature-pipeline",
      "bp_name": "相机特征检索出图",
      "session_id": "seecrab_ff9f0f08ccec",
      "session_title": "帮我查园区东门的相机",
      "status": "active",
      "run_mode": "manual",
      "current_subtask_index": 1,
      "progress": "1/3",
      "subtask_count": 3,
      "done_count": 1,
      "subtask_names": ["相机搜索", "特征检索", "结果出图"],
      "subtask_statuses": {
        "camera-search": "done",
        "feature-extraction": "current",
        "render-image": "pending"
      },
      "created_at": 1711800000.0,
      "completed_at": null,
      "suspended_at": null
    },
    {
      "instance_id": "bp-abc12345",
      "bp_id": "market-research-report",
      "bp_name": "市场调研报告",
      "session_id": "seecrab_ff9f0f08ccec",
      "session_title": "帮我查园区东门的相机",
      "status": "completed",
      "run_mode": "auto",
      "current_subtask_index": 3,
      "progress": "4/4",
      "subtask_count": 4,
      "done_count": 4,
      "subtask_names": ["信息收集", "分析", "大纲", "报告"],
      "subtask_statuses": {
        "research": "done",
        "analysis": "done",
        "outline": "done",
        "report": "done"
      },
      "created_at": 1711700000.0,
      "completed_at": 1711710000.0,
      "suspended_at": null
    }
  ]
}
```

成功 (HTTP 200) — 无实例:

```json
{
  "total": 0,
  "limit": 50,
  "offset": 0,
  "active_id": null,
  "instances": []
}
```

**输出字段说明:**

| 字段 | 类型 | 说明 |
|------|------|------|
| `total` | number | 匹配条件的实例总数 |
| `limit` | number | 分页大小 |
| `offset` | number | 分页偏移 |
| `active_id` | string \| null | 当前 session 的活跃实例 ID（仅 session_id 过滤时有值） |
| `instances[].instance_id` | string | 实例唯一 ID（格式 `bp-{uuid8}`） |
| `instances[].bp_id` | string | BP 模板 ID |
| `instances[].bp_name` | string | BP 模板展示名称 |
| `instances[].session_id` | string | 所属会话 ID |
| `instances[].session_title` | string | 所属会话标题，用于列表展示（避免 N+1 次 sessions 请求） |
| `instances[].status` | string | `active`/`suspended`/`completed`/`cancelled` |
| `instances[].run_mode` | string | `manual`/`auto` |
| `instances[].current_subtask_index` | number | 当前子任务索引 (0-based) |
| `instances[].progress` | string | 进度文本（如 `"1/3"`） |
| `instances[].subtask_count` | number | 子任务总数 |
| `instances[].done_count` | number | 已完成子任务数（注意：不等于 `current_subtask_index`，因 subtask 可能 failed/stale） |
| `instances[].subtask_names` | string[] | 各子任务名称列表，用于列表悬停展示 |
| `instances[].subtask_statuses` | Record<string, string> | 各子任务状态 |
| `instances[].created_at` | number | 创建时间戳 (Unix) |
| `instances[].completed_at` | number \| null | 完成时间戳 |
| `instances[].suspended_at` | number \| null | 挂起时间戳 |

---

### 3.8 查询 BP 实例详情 [v1.1 调整]

> **v1.1 变更**: 路径从 `GET /api/bp/instance/{instance_id}` 调整为 `GET /api/bp/instances/{instance_id}`（单复数统一）。

获取单个 BP 实例的完整信息。

| 项目 | 说明 |
|------|------|
| **URL** | `GET /api/bp/instances/{instance_id}` |

**路径参数:**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `instance_id` | string | 是 | 实例 ID |

**Response 返回示例:**

成功 (HTTP 200):

```json
{
  "instance_id": "bp-f29a234d",
  "bp_id": "camera-feature-pipeline",
  "bp_name": "相机特征检索出图",
  "session_id": "seecrab_ff9f0f08ccec",
  "status": "active",
  "run_mode": "manual",
  "current_subtask_index": 1,
  "created_at": 1711800000.0,
  "completed_at": null,
  "suspended_at": null,
  "subtask_statuses": {
    "camera-search": "done",
    "feature-extraction": "current",
    "render-image": "pending"
  },
  "subtask_outputs": {
    "camera-search": {
      "summary": "找到3个候选相机",
      "camera_ids": ["cam-001", "cam-002", "cam-003"]
    }
  },
  "initial_input": {
    "query": "园区东门"
  },
  "supplemented_inputs": {},
  "context_summary": ""
}
```

实例不存在 (HTTP 404):

```json
{
  "error": "Not found"
}
```

**响应状态码:**

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 404 | 实例不存在 |
| 500 | BP 系统未初始化 |

---

### 3.9 查询子任务输出 [v1.1 调整]

> **v1.1 变更**: 路径从 `GET /api/bp/output/{instance_id}/{subtask_id}` 调整为 `GET /api/bp/instances/{instance_id}/output/{subtask_id}`（嵌套资源）。

获取指定子任务的输出数据。

| 项目 | 说明 |
|------|------|
| **URL** | `GET /api/bp/instances/{instance_id}/output/{subtask_id}` |

**路径参数:**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `instance_id` | string | 是 | 实例 ID |
| `subtask_id` | string | 是 | 子任务 ID |

**Response 返回示例:**

成功 (HTTP 200):

```json
{
  "output": {
    "summary": "找到3个候选相机",
    "camera_ids": ["cam-001", "cam-002", "cam-003"]
  }
}
```

实例不存在 (HTTP 404):

```json
{
  "error": "Not found"
}
```

子任务无输出 (HTTP 404):

```json
{
  "error": "No output"
}
```

**响应状态码:**

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 404 | 实例不存在或子任务无输出 |
| 500 | BP 系统未初始化 |

---

### 3.10 切换运行模式 [v1.1 调整]

> **v1.1 变更**: 路径从 `PUT /api/bp/run-mode` 调整为 `PUT /api/bp/instances/{instance_id}/run-mode`。`instance_id` 从请求体移至路径参数。

切换 BP 实例的运行模式 (手动/自动)。

| 项目 | 说明 |
|------|------|
| **URL** | `PUT /api/bp/instances/{instance_id}/run-mode` |
| **Content-Type** | `application/json` |

**路径参数:**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `instance_id` | string | 是 | BP 实例 ID |

**请求体:**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `run_mode` | string | 是 | `"manual"` 或 `"auto"` |

**Response 返回示例:**

成功 (HTTP 200) — 切换为自动模式:

```json
{
  "success": true,
  "run_mode": "auto"
}
```

成功 (HTTP 200) — 切换为手动模式:

```json
{
  "success": true,
  "run_mode": "manual"
}
```

实例不存在 (HTTP 404):

```json
{
  "success": false,
  "error": "Instance bp_inst_999 not found"
}
```

BP 系统未初始化 (HTTP 500):

```json
{
  "success": false,
  "error": "BP system not initialized"
}
```

**响应状态码:**

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 404 | 实例不存在 |
| 500 | BP 系统未初始化 |

---

### 3.11 编辑子任务输出 [v1.1 调整]

> **v1.1 变更**: 路径从 `PUT /api/bp/edit-output` 调整为 `PUT /api/bp/instances/{instance_id}/output`。`instance_id` 从请求体移至路径参数。

前端编辑子任务输出 (Chat-to-Edit)。

| 项目 | 说明 |
|------|------|
| **URL** | `PUT /api/bp/instances/{instance_id}/output` |
| **Content-Type** | `application/json` |

**路径参数:**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `instance_id` | string | 是 | BP 实例 ID |

**请求体:**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `subtask_id` | string | 是 | 子任务 ID |
| `changes` | object | 是 | 修改内容 (key-value 对) |

**Response 返回示例:**

成功 (HTTP 200) — 编辑后触发下游子任务标记为过时:

```json
{
  "success": true,
  "stale_subtask_ids": ["trend_analysis", "report_gen"],
  "updated_output": {
    "market_size": "600亿",
    "growth_rate": "15%",
    "top_players": ["A公司", "B公司", "C公司"]
  }
}
```

成功 (HTTP 200) — 编辑未影响下游:

```json
{
  "success": true,
  "stale_subtask_ids": [],
  "updated_output": {
    "market_size": "500亿",
    "growth_rate": "12%",
    "top_players": ["A公司", "B公司", "D公司"]
  }
}
```

实例不存在 (HTTP 404):

```json
{
  "success": false,
  "error": "Instance bp_inst_999 not found"
}
```

BP 配置不存在 (HTTP 404):

```json
{
  "success": false,
  "error": "BP config not found"
}
```

**响应状态码:**

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 404 | 实例或 BP 配置不存在 |
| 500 | BP 系统未初始化 |

---

### 3.12 取消 BP 实例 [v1.1 调整]

> **v1.1 变更**: 路径从 `DELETE /api/bp/{instance_id}` 调整为 `DELETE /api/bp/instances/{instance_id}`（归入 instances 资源）。

取消 BP 实例。

| 项目 | 说明 |
|------|------|
| **URL** | `DELETE /api/bp/instances/{instance_id}` |

**路径参数:**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `instance_id` | string | 是 | BP 实例 ID |

**Response 返回示例:**

成功 (HTTP 200):

```json
{
  "status": "ok"
}
```

实例不存在 (HTTP 404):

```json
{
  "error": "Not found"
}
```

**响应状态码:**

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 404 | 实例不存在 |
| 500 | BP 系统未初始化 |

---

## 4. SSE 事件类型参考

以下是 `/api/seecrab/chat` 和 BP SSE 接口返回的事件类型:

### 4.1 通用对话事件

| 事件类型 | 说明 | 主要字段 |
|----------|------|----------|
| `thinking` | LLM 思考过程 | `content`, `agent_id`, `done` |
| `ai_text` | AI 回复文本 (增量) | `content`, `agent_id` |
| `step_card` | 工具/技能执行步骤卡片 | `step_id`, `title`, `status`, `source_type`, `card_type`, `duration`, `agent_id`, `input`, `output` |
| `plan_checklist` | Plan 模式检查清单 | `steps: [{index, title, status}]` |
| `ask_user` | 向用户提问 | `ask_id`, `question`, `options: [{label, value}]` |
| `agent_header` | 子 Agent 信息 | `agent_id`, `agent_name` |
| `session_title` | 会话标题更新 | `session_id`, `title` |
| `timer_update` | 计时器更新 | `phase` (`ttft`/`total`), `state`, `value` |
| `heartbeat` | 心跳 | - |
| `error` | 错误 | `message`, `code` |
| `done` | 流结束 | - |

### 4.2 最佳实践 (BP) 事件

| 事件类型 | 说明 | 主要字段 |
|----------|------|----------|
| `bp_offer` | BP 推荐 | `bp_id`, `bp_name`, `subtasks: [{id, name}]`, `default_run_mode` |
| `bp_instance_created` | BP 实例创建 | `instance_id`, `bp_id`, `bp_name`, `run_mode`, `subtasks: [{id, name}]` |
| `bp_progress` | BP 进度更新 | `instance_id`, `bp_name`, `statuses`, `subtasks`, `current_subtask_index`, `run_mode`, `status` |
| `bp_subtask_start` | 子任务开始 | `instance_id`, `subtask_id` |
| `bp_subtask_output` | 子任务输出 | `instance_id`, `subtask_id`, `subtask_name`, `output`, `output_schema`, `summary` |
| `bp_subtask_complete` | 子任务完成 | `instance_id`, `subtask_id` |
| `bp_ask_user` | BP 缺失字段询问 | `instance_id`, `subtask_id`, `subtask_name`, `missing_fields`, `input_schema` |
| `bp_waiting_next` | 等待用户执行下一步 | `instance_id` |
| `bp_stale` | 子任务过时标记 | `instance_id`, `stale_subtask_ids`, `reason` |
| `bp_complete` | BP 全部完成 | `instance_id` |
| `bp_cancelled` | BP 实例已取消 | `instance_id` |
| `bp_suspended` | BP 实例被挂起（如新 BP 启动时抢占） | `instance_id`, `reason` |
| `bp_error` | BP 错误 | `instance_id`, `message` |

---

## 5. 前端调用示例汇总

以下示例基于 `apps/seecrab/src/` 中的实际前端代码。

### 5.1 SSE 流式对话

> 源码位置: `src/api/sse-client.ts:8-71` → `SSEClient.sendMessage()`

```typescript
// 发送消息并接收 SSE 流
const resp = await fetch('/api/seecrab/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    message: '帮我分析一下市场趋势',
    conversation_id: 'seecrab_abc123',
    thinking_mode: 'auto',
    thinking_depth: 'medium',
  }),
})

const reader = resp.body!.getReader()
const decoder = new TextDecoder()
let buffer = ''

while (true) {
  const { done, value } = await reader.read()
  if (done) break

  buffer += decoder.decode(value, { stream: true })
  const parts = buffer.split('\n\n')
  buffer = parts.pop() ?? ''

  for (const part of parts) {
    for (const line of part.split('\n')) {
      if (!line.startsWith('data: ')) continue
      const event = JSON.parse(line.slice(6))
      // event.type: 'thinking' | 'ai_text' | 'step_card' | 'done' | ...
      console.log(event.type, event)
    }
  }
}
```

### 5.2 取消任务

> 源码位置: `src/api/sse-client.ts:74-88` → `SSEClient.cancelTask()`

```typescript
await fetch('/api/chat/cancel', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    conversation_id: 'seecrab_abc123',
    reason: '用户点击停止按钮',
  }),
})
```

### 5.3 获取会话列表

> 源码位置: `src/api/http-client.ts:14` → `httpClient.listSessions()`

```typescript
// 默认获取前 50 条
const { total, sessions } = await fetch('/api/seecrab/sessions').then(r => r.json())
// 分页获取
const page2 = await fetch('/api/seecrab/sessions?limit=20&offset=20').then(r => r.json())
// { total, limit, offset, sessions: [{ id, title, updated_at, message_count, last_message }] }
```

### 5.4 创建会话

> 源码位置: `src/api/http-client.ts:15` → `httpClient.createSession()`

```typescript
const { session_id } = await fetch('/api/seecrab/sessions', {
  method: 'POST',
}).then(r => r.json())
```

### 5.5 获取会话详情 (含消息历史)

> 源码位置: `src/api/http-client.ts:17` → `httpClient.getSession()`

```typescript
const { session_id, title, messages } = await fetch(
  `/api/seecrab/sessions/${sessionId}`
).then(r => r.json())
// messages: [{ role, content, timestamp, metadata, reply_state }]
```

### 5.6 更新会话标题

> 源码位置: `src/api/http-client.ts:18-22` → `httpClient.updateSession()`

```typescript
await fetch(`/api/seecrab/sessions/${sessionId}`, {
  method: 'PATCH',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ title: '新标题' }),
})
```

### 5.7 删除会话

> 源码位置: `src/api/http-client.ts:16` → `httpClient.deleteSession()`

```typescript
await fetch(`/api/seecrab/sessions/${sessionId}`, {
  method: 'DELETE',
})
```

### 5.8 生成会话标题

> 源码位置: `src/api/http-client.ts:28-36` → `httpClient.generateTitle()`

```typescript
const { title } = await fetch('/api/sessions/generate-title', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    message: '帮我分析一下Q1的销售数据',
    reply: 'Q1销售额同比增长15%...',
  }),
}).then(r => r.json())
```

### 5.9 提交 ask_user 回答

> 源码位置: `src/api/http-client.ts:23-27` → `httpClient.submitAnswer()`

```typescript
await fetch('/api/seecrab/answer', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    conversation_id: 'seecrab_abc123',
    answer: '确认执行',
  }),
})
```

### 5.10 启动 BP 实例

> 源码位置: `src/components/chat/BotReply.vue:156` → `sseClient.streamBP()`

```typescript
// 通过 SSE 流启动 BP
const response = await fetch('/api/bp/start', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    bp_id: 'market_analysis',
    session_id: 'seecrab_abc123',
    input_data: {},
    run_mode: 'manual',
    user_message: '最佳实践模式',
  }),
})
// 使用与 5.1 相同的 SSE 读取方式处理响应流
```

### 5.11 执行 BP 下一步

> 源码位置: `src/components/chat/BotReply.vue:117` → `sseClient.streamBP()`

```typescript
const response = await fetch('/api/bp/next', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    instance_id: 'bp_inst_001',
    session_id: 'seecrab_abc123',
    user_message: '进入下一步',
  }),
})
// SSE 流式读取
```

### 5.12 提交 BP 用户回答

> 源码位置: `src/components/chat/BotReply.vue:135` → `sseClient.streamBP()`

```typescript
const response = await fetch('/api/bp/answer', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    instance_id: 'bp_inst_001',
    subtask_id: 'step2',
    data: { company_name: 'ABC公司', industry: '科技' },
    session_id: 'seecrab_abc123',
    user_message: '补充数据: {"company_name":"ABC公司"}',
  }),
})
// SSE 流式读取
```

### 5.13 获取 BP 模板列表 [v1.1 新增]

```typescript
const { total, configs } = await fetch('/api/bp/configs').then(r => r.json())
// configs: [{ id, name, description, subtask_count, default_run_mode, trigger_types, triggers }]
```

### 5.14 获取 BP 模板详情 [v1.1 新增]

```typescript
const config = await fetch(`/api/bp/configs/${bpId}`).then(r => r.json())
// config: { id, name, description, subtasks, triggers, final_output_schema, ... }
```

### 5.15 查询 BP 实例统计 [v1.1 新增]

```typescript
const stats = await fetch('/api/bp/instances/stats').then(r => r.json())
// stats: { total: 42, by_status: { active: 3, suspended: 5, completed: 30, cancelled: 4 } }

// 按 session 过滤
const sessionStats = await fetch(`/api/bp/instances/stats?session_id=${sessionId}`).then(r => r.json())
```

### 5.16 查询 BP 实例列表 [v1.1 调整]

> 源码位置: `src/api/http-client.ts` → `httpClient.getBPStatus()`

```typescript
// v1.1: 路径从 /api/bp/status 调整为 /api/bp/instances
const data = await fetch(`/api/bp/instances?session_id=${sessionId}`).then(r => r.json())
// data: { total, limit, offset, active_id, instances: [{
//   instance_id, bp_name, progress, session_title,
//   subtask_count, done_count, subtask_names, ...
// }] }
```

### 5.17 切换 BP 运行模式 [v1.1 调整]

> 源码位置: `src/components/chat/BotReply.vue:94` → `httpClient.setBPRunMode()`

```typescript
// v1.1: instance_id 从 body 移至路径参数
await fetch(`/api/bp/instances/${instanceId}/run-mode`, {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    run_mode: 'auto',  // 'manual' | 'auto'
  }),
})
```

### 5.18 编辑 BP 子任务输出 [v1.1 调整]

> 源码位置: `src/components/panel/SubtaskOutputPanel.vue:155` → `httpClient.editBPOutput()`

```typescript
// v1.1: instance_id 从 body 移至路径参数
await fetch(`/api/bp/instances/${instanceId}/output`, {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    subtask_id: 'step1',
    changes: { market_size: '100亿', growth_rate: '15%' },
  }),
})
```

### 5.19 取消 BP 实例 [v1.1 调整]

```typescript
// v1.1: 路径从 DELETE /api/bp/{id} 调整为 DELETE /api/bp/instances/{id}
await fetch(`/api/bp/instances/${instanceId}`, {
  method: 'DELETE',
})
```

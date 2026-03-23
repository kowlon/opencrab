# SeeAgent 非 SeeCrab 的 API 参考

> 生成日期: 2026-03-23
> 说明: 不被 `apps/seecrab/` 前端直接调用的后端 API。服务于 Setup-Center 桌面端、IM 渠道、组织编排等场景。
> 后端框架: FastAPI (Python)
> 源码路径: `src/seeagent/api/routes/`

---

## 目录

- [1. 认证 (Auth)](#1-认证-auth)
- [2. 通用对话 (Chat)](#2-通用对话-chat)
- [3. 会话管理 (Sessions)](#3-会话管理-sessions)
- [4. 最佳实践 — 辅助端点 (Best Practice Misc)](#4-最佳实践--辅助端点-best-practice-misc)
- [5. WebSocket 实时事件](#5-websocket-实时事件)
- [6. 健康检查与诊断 (Health)](#6-健康检查与诊断-health)
- [7. 配置管理 (Config)](#7-配置管理-config)
- [8. LLM 模型 (Models)](#8-llm-模型-models)
- [9. 技能管理 (Skills)](#9-技能管理-skills)
- [10. MCP 服务器管理](#10-mcp-服务器管理)
- [11. 记忆管理 (Memory)](#11-记忆管理-memory)
- [12. 身份管理 (Identity)](#12-身份管理-identity)
- [13. 智能体管理 (Agents)](#13-智能体管理-agents)
- [14. IM 渠道查看 (IM)](#14-im-渠道查看-im)
- [15. 定时任务 (Scheduler)](#15-定时任务-scheduler)
- [16. Token 用量统计 (Token Stats)](#16-token-用量统计-token-stats)
- [17. 文件服务 (Files)](#17-文件服务-files)
- [18. 上传 (Upload)](#18-上传-upload)
- [19. 日志 (Logs)](#19-日志-logs)
- [20. 工作区备份 (Workspace IO)](#20-工作区备份-workspace-io)
- [21. 组织编排 (Orgs)](#21-组织编排-orgs)
- [22. Hub / Store (平台市场)](#22-hub--store-平台市场)
- [23. 反馈与 Bug 报告](#23-反馈与-bug-报告)

---

## 1. 认证 (Auth)

**路由文件**: `src/seeagent/api/routes/auth.py`
**前缀**: `/api/auth`

### POST /api/auth/login

密码登录，返回 access token 并设置 refresh cookie。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "password": "string" }` (支持 JSON 和 form-urlencoded) |
| **成功响应** | `{ "access_token": "...", "token_type": "bearer", "expires_in": 86400 }` |
| **失败响应** | 401 `{ "detail": "Invalid password" }` / 429 频率限制 |
| **备注** | refresh token 以 httpOnly cookie 设置，path=/api/auth |

### POST /api/auth/refresh

用 refresh cookie 换取新的 access token。

| 字段 | 说明 |
|------|------|
| **请求** | 无 body，依赖 cookie |
| **成功响应** | `{ "access_token": "...", "token_type": "bearer", "expires_in": 86400 }` |
| **失败响应** | 401 无 cookie 或已过期 |

### POST /api/auth/logout

清除 refresh cookie。

| 字段 | 说明 |
|------|------|
| **请求** | 无 |
| **响应** | `{ "status": "ok" }` |

### GET /api/auth/check

检查当前请求的认证状态。

| 字段 | 说明 |
|------|------|
| **请求** | 无 |
| **响应** | `{ "authenticated": bool, "method": "local"|"token"|"refresh_cookie", "password_user_set": bool, "needs_refresh"?: bool }` |
| **备注** | 本地请求(127.0.0.1)始终认证通过 |

### POST /api/auth/change-password

修改 Web 访问密码。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "new_password": "string", "current_password"?: "string" }` |
| **响应** | `{ "status": "ok", "message": "...", "disconnected": int }` |
| **备注** | 本地请求无需 current_password；远程修改后所有远程会话断开 |

### GET /api/auth/password-hint

获取密码提示（仅限本地请求）。

| 字段 | 说明 |
|------|------|
| **请求** | 无 |
| **响应** | `{ "hint": "string" }` |
| **限制** | 403 非本地请求 |

---

## 2. 通用对话 (Chat)

**路由文件**: `src/seeagent/api/routes/chat.py`
**前缀**: `/api/chat`

> **注意**: `POST /api/chat/cancel` 被 SeeCrab 前端调用，详见 seecrab API 文档。以下为其余端点。

### POST /api/chat

流式对话端点（SSE），支持多 Agent 和档案。供 Setup-Center 桌面端使用。

| 字段 | 说明 |
|------|------|
| **请求** | `ChatRequest` — `{ "message", "conversation_id"?, "client_id"?, "agent_profile_id"?, ... }` |
| **响应** | SSE 流 |

### GET /api/chat/busy

检查会话是否被锁定（正在处理中）。

| 字段 | 说明 |
|------|------|
| **查询参数** | `conversation_id` |
| **响应** | `{ "locked": bool, "client_id"?: "string", "elapsed"?: float }` |

### POST /api/chat/answer

提交对 ask_user 的回答。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "conversation_id", "answer_id", "answer" }` |
| **响应** | `{ "ok": bool }` |

### POST /api/chat/skip

跳过当前步骤。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "conversation_id", "reason" }` |
| **响应** | `{ "status": "skipped" }` |

### POST /api/chat/insert

向思维链插入想法。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "conversation_id", "reason" }` |
| **响应** | `{ "status": "inserted" }` |

### GET /api/agents/sub-tasks

获取子 Agent 任务。

| 字段 | 说明 |
|------|------|
| **查询参数** | `conversation_id` |
| **响应** | 任务列表 |

### GET /api/agents/sub-records

获取子 Agent 执行记录。

| 字段 | 说明 |
|------|------|
| **查询参数** | `conversation_id` |
| **响应** | 记录列表 |

---

## 3. 会话管理 (Sessions)

**路由文件**: `src/seeagent/api/routes/sessions.py`
**前缀**: `/api/sessions`

> **注意**: `POST /api/sessions/generate-title` 被 SeeCrab 前端调用，详见 seecrab API 文档。以下为其余端点。

### GET /api/sessions

列出指定渠道的会话。

| 字段 | 说明 |
|------|------|
| **查询参数** | `channel` (默认 "desktop") |
| **响应** | `{ "sessions": [{ "id", "title", "lastMessage", "timestamp", "messageCount", "agentProfileId" }] }` |

### GET /api/sessions/{conversation_id}/history

获取会话消息历史。

| 字段 | 说明 |
|------|------|
| **查询参数** | `channel`, `user_id` |
| **响应** | `{ "messages": [{ "id", "role", "content", "timestamp", "chain_summary"?, "tool_summary"?, "artifacts"?, "ask_user"? }] }` |

### DELETE /api/sessions/{conversation_id}

删除会话。

| 字段 | 说明 |
|------|------|
| **查询参数** | `channel`, `user_id` |
| **响应** | `{ "ok": bool, "removed": bool }` |

### POST /api/sessions/{conversation_id}/messages

批量追加消息到会话。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "messages": [{ "role", "content" }] }` |
| **查询参数** | `channel`, `user_id` |
| **响应** | `{ "ok": bool, "count": int }` |

---

## 4. 最佳实践 — 辅助端点 (Best Practice Misc)

**路由文件**: `src/seeagent/api/routes/bestpractice.py`
**前缀**: `/api/bp`

> **注意**: BP 的核心端点（status, run-mode, edit-output, start, next, answer）被 SeeCrab 前端调用，详见 seecrab API 文档。以下为 SeeCrab 不直接调用的辅助端点。

### GET /api/bp/output/{instance_id}/{subtask_id}

查询子任务输出。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "output": {} }` |

### DELETE /api/bp/{instance_id}

取消 BP 实例。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "status": "ok" }` |

---

## 5. WebSocket 实时事件

**路由文件**: `src/seeagent/api/routes/websocket.py`

### WS /ws/events

实时事件流 WebSocket 端点。

| 字段 | 说明 |
|------|------|
| **查询参数** | `token`（access token，本地可选） |
| **事件类型** | `connected`, `ping`/`pong`, `session_invalidated`, 自定义广播事件 |
| **用途** | 替代 Tauri 的 listen()，为 Web 客户端提供 pub/sub 实时更新 |

**广播事件列表**:
- `skills:changed` — 技能状态变更 (`{action: "reload"|"install"|"uninstall"}`)
- `scheduler:task_update` — 定时任务变更 (`{action: "create"|"update"|"delete"|"toggle"|"trigger"}`)
- `org:command_done` — 组织命令完成 (`{org_id, command_id, result?, error?}`)

---

## 6. 健康检查与诊断 (Health)

**路由文件**: `src/seeagent/api/routes/health.py`

### GET /api/health

基础健康检查。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "status": "ok", "service": "seeagent", "version", "git_hash", "version_full", "pid", "timestamp", "agent_initialized": bool, "local_ip" }` |

### POST /api/health/check

检查 LLM 端点健康状态（dry_run 模式，不影响运行中的 Agent）。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "endpoint_name"?: "string" }` |
| **响应** | `{ "results": [{ "name", "status": "healthy"|"unhealthy", "latency_ms", "error"?, "consecutive_failures"?, "cooldown_remaining"?, "is_extended_cooldown"?, "last_checked_at" }] }` |
| **备注** | 不传 endpoint_name 则并发检查所有端点 |

### GET /api/health/loop

Event loop 健康状态与 LLM 并发统计。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "dual_loop": bool, "api_loop_lag_ms", "llm_concurrent": {...}, "org_concurrency": {...} }` |

### GET /api/diagnostics

后端自诊断（运行时、pip、核心模块完整性检查）。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "summary": "healthy"|"broken", "checks": [{ "id", "title", "status": "pass"|"warn"|"fail", "code", "evidence", "autoFix", "fixHint" }], "environment": { "platform", "pythonVersion", "runtimeType", "seeagentVersion", "pid" } }` |

### GET /api/debug/pool-stats

Agent 实例池诊断统计。

| 字段 | 说明 |
|------|------|
| **响应** | 池统计信息或 `{ "error": "..." }` |

### GET /api/debug/orchestrator-state

编排器内部状态诊断。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "enabled": bool, "sub_agent_states": {}, "active_tasks": [], "health_stats": {} }` |

---

## 7. 配置管理 (Config)

**路由文件**: `src/seeagent/api/routes/config.py`

### GET /api/config/workspace-info

获取当前工作区路径和基本信息。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "workspace_path", "workspace_name", "env_exists": bool, "endpoints_exists": bool }` |

### GET /api/config/env

读取 .env 文件（敏感值脱敏）。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "env": { "KEY": "value" }, "masked": { "KEY": "sk-****yz" }, "raw": "" }` |

### POST /api/config/env

更新 .env 文件（合并写入，保留注释）。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "entries": { "KEY": "value", "DELETE_KEY": "" } }` |
| **响应** | `{ "status": "ok", "updated_keys": ["KEY", ...] }` |
| **备注** | 空值表示删除该键；同时更新 os.environ |

### GET /api/config/endpoints

读取 `data/llm_endpoints.json`。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "endpoints": [...], "raw": {} }` |

### POST /api/config/endpoints

写入 `data/llm_endpoints.json`。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "content": { ... 完整JSON内容 ... } }` |
| **响应** | `{ "status": "ok" }` |

### POST /api/config/reload

热重载 LLM 端点配置到运行中的 Agent。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "status": "ok", "reloaded": bool, "endpoints": int, "compiler_reloaded": bool, "stt_reloaded": bool }` |
| **备注** | 写入 endpoints 后调用此接口使配置生效 |

### POST /api/config/restart

触发服务优雅重启。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "status": "restarting" }` |
| **备注** | 前端应轮询 /api/health 直到服务恢复 |

### GET /api/config/skills

读取 `data/skills.json`（技能选择/白名单配置）。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "skills": {} }` |

### POST /api/config/skills

写入 `data/skills.json`。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "content": {} }` |
| **响应** | `{ "status": "ok" }` |

### GET /api/config/disabled-views

读取被禁用的模块视图列表。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "disabled_views": ["skills", "im", ...] }` |

### POST /api/config/disabled-views

更新被禁用的模块视���列表。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "views": ["skills", "im"] }` |
| **响应** | `{ "status": "ok", "disabled_views": [...] }` |

### GET /api/config/agent-mode

返回多 Agent 模式开关状态。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "multi_agent_enabled": bool }` |

### POST /api/config/agent-mode

切换多 Agent 模式（Beta）。修改立即生效并持久化。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "enabled": bool }` |
| **响应** | `{ "status": "ok", "multi_agent_enabled": bool }` |
| **备注** | 启用时自动初始化 Orchestrator 并热注入 Agent 工具 |

### GET /api/config/providers

返回后端已注册的 LLM 服务商列表。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "providers": [{ "name", "slug", "api_type", "default_base_url", "api_key_env_suggestion", "supports_model_list", "requires_api_key", "is_local", "note" }] }` |

### POST /api/config/list-models

拉取 LLM 端点的模型列表（远程模式替代 Tauri 命令）。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "api_type": "openai"|"anthropic", "base_url": "...", "provider_slug"?: "...", "api_key": "..." }` |
| **响应** | `{ "models": [...] }` 或 `{ "error": "...", "models": [] }` |

---

## 8. LLM 模型 (Models)

**路由文件**: `src/seeagent/api/routes/chat_models.py`

### GET /api/models

列出当前可用的 LLM 端点及其状态。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "models": [{ "name", "provider", "model", "status": "healthy"|"unhealthy", "has_api_key": bool }] }` |

---

## 9. 技能管理 (Skills)

**路由文件**: `src/seeagent/api/routes/skills.py`

### GET /api/skills

列出所有技能（含禁用的），带配置 schema。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "skills": [{ "skill_id", "name", "description", "name_i18n"?, "description_i18n"?, "system": bool, "enabled": bool, "category", "tool_name", "config"?, "path", "source_url"? }] }` |
| **备注** | 结果有模块级缓存，install/uninstall/reload/edit 后自动失效 |

### POST /api/skills/config

更新技能配置。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "skill_name": "...", "config": {} }` |
| **响应** | `{ "status": "ok", "skill": "...", "config": {} }` |

### POST /api/skills/install

安装技能（远程模式）。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "url": "github:user/repo/skill" }` |
| **响应** | `{ "status": "ok", "url": "..." }` 或 `{ "error": "..." }` |
| **备注** | 安装后自动重载技能、应用白名单、自动翻译 |

### POST /api/skills/uninstall

卸载技能。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "skill_id": "skill-directory-name" }` |
| **响应** | `{ "status": "ok", "skill_id": "..." }` |

### POST /api/skills/reload

热重载技能。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "skill_name"?: "可选，空则全量重载" }` |
| **响应** | `{ "status": "ok", "reloaded": "all"|["name"], "loaded"?: int, "pruned"?: int, "total"?: int }` |

### GET /api/skills/content/{skill_name}

读取单个技能的 SKILL.md 原始内容。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "content": "SKILL.md内容", "path": "文件路径", "system": bool }` |

### PUT /api/skills/content/{skill_name}

更新��能的 SKILL.md 内容并热重载。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "content": "完整SKILL.md内容" }` |
| **响应** | `{ "status": "ok", "reloaded": bool, "name": "...", "description": "..." }` |
| **备注** | 系统内置技能不可编辑 |

### GET /api/skills/marketplace

搜索 skills.sh 技能市场（代理 CORS）。

| 字段 | 说明 |
|------|------|
| **查询参数** | `q` (默认 "agent") |
| **响应** | 来自 skills.sh API 的 JSON |

---

## 10. MCP 服务器管理

**路由文件**: `src/seeagent/api/routes/mcp.py`

### GET /api/mcp/servers

列出所有 MCP 服务器及其配置和连接状态。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "mcp_enabled": bool, "servers": [{ "name", "description", "transport", "url", "command", "connected": bool, "tools": [...], "tool_count", "has_instructions", "catalog_tool_count", "source": "workspace"|"builtin", "removable": bool }], "total", "connected", "workspace_path" }` |

### POST /api/mcp/connect

连接到指定 MCP 服务器。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "server_name": "..." }` |
| **响应** | `{ "status": "connected"|"already_connected"|"failed", "server", "tools"?, "tool_count"?, "error"? }` |

### POST /api/mcp/disconnect

断开指定 MCP 服务器。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "server_name": "..." }` |
| **响应** | `{ "status": "disconnected"|"not_connected", "server" }` |

### GET /api/mcp/tools

列出所有可用 MCP 工具。

| 字段 | 说明 |
|------|------|
| **查询参数** | `server` (可选，按服务器过滤) |
| **响应** | `{ "tools": [{ "name", "description", "input_schema" }], "total" }` |

### GET /api/mcp/instructions/{server_name}

获取 MCP 服务器的 INSTRUCTIONS.md。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "server", "instructions": "string"|null }` |

### POST /api/mcp/servers/add

添加新的 MCP 服务器配置（持久化到 workspace）。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "name", "transport": "stdio"|"streamable_http"|"sse", "command"?, "args"?: [], "env"?: {}, "url"?, "description"?, "auto_connect"?: false }` |
| **响应** | `{ "status": "ok", "server", "path", "connect_result"?: { "connected": bool, "tool_count"?, "error"? } }` |
| **备注** | 添加后自动尝试连接 |

### DELETE /api/mcp/servers/{server_name}

删除 MCP 服务器配置（仅限 workspace 配置，不可删除内置）。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "status": "ok"|"error", "server", "removed": bool }` |

---

## 11. 记忆管理 (Memory)

**路由文件**: `src/seeagent/api/routes/memory.py`
**前缀**: `/api/memories`

### GET /api/memories

列出记忆条目。

| 字段 | 说明 |
|------|------|
| **查询参数** | `type`?, `search`?, `min_score`? (默认0), `limit`? (默认200) |
| **响应** | `{ "memories": [{ "id", "type", "priority", "content", "source", "subject", "predicate", "tags", "importance_score", "confidence", "access_count", "created_at", "updated_at", "last_accessed_at", "expires_at" }], "total" }` |
| **备注** | 传 search 时使用语义搜索 |

### GET /api/memories/stats

获取记忆统计信息。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "total", "by_type": { "fact": 10, ... }, "avg_score" }` |

### GET /api/memories/{memory_id}

获取单条记忆详情。

### PUT /api/memories/{memory_id}

更新记忆。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "content"?, "importance_score"?, "tags"? }` |
| **响应** | `{ "ok": true }` |

### DELETE /api/memories/{memory_id}

删除单条记忆。

### POST /api/memories/batch-delete

批量删除记忆。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "ids": ["id1", "id2", ...] }` |
| **响应** | `{ "deleted": int, "total": int }` |

### POST /api/memories/review

触发 LLM 驱动的记忆审查（整合/清理）。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "ok": true, "review": { ... } }` |

### POST /api/memories/refresh-md

从当前数据库状态重新生成 MEMORY.md。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "ok": true }` |

---

## 12. 身份管理 (Identity)

**路由文件**: `src/seeagent/api/routes/identity.py`
**前缀**: `/api/identity`

### GET /api/identity/files

列出所有可编辑的身份文件及元数据。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "files": [{ "name", "exists": bool, "restricted": bool, "is_runtime": bool, "warning_key"?, "budget_tokens"?, "size"?, "modified"?, "tokens"? }] }` |
| **包含文件** | SOUL.md, AGENT.md, USER.md, MEMORY.md, POLICIES.yaml, prompts/policies.md, personas/*.md, runtime/*.md |

### GET /api/identity/file

读取单个身份文件。

| 字段 | 说明 |
|------|------|
| **查询参数** | `name` |
| **响应** | `{ "name", "content", "tokens", "budget_tokens"? }` |

### PUT /api/identity/file

写入身份文件（带校验）。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "name": "SOUL.md", "content": "...", "force": false }` |
| **响应** | `{ "saved": true, "name", "tokens" }` 或 `{ "saved": false, "needs_confirm": true, "warnings": [...] }` |
| **备注** | 有 errors 时返回 400；有 warnings 且 force=false 时要求确认 |

### POST /api/identity/validate

校验文件内容（不保存）。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "name": "...", "content": "..." }` |
| **响应** | `{ "errors": [], "warnings": [] }` |

### POST /api/identity/reload

热重载身份文件到运行中的 Agent。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "status": "reloaded" }` |

### POST /api/identity/compile

触发身份编译。

| 字段 | 说明 |
|------|------|
| **查询参数** | `mode` = "rules" (默认) 或 "llm" |
| **响应** | `{ "mode_used", "compiled_files": { "agent_core": { "content", "tokens", "budget_tokens" }, ... } }` |

### GET /api/identity/compile-status

获取编译状态（token 计数、预算、新鲜度）。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "outdated": bool, "last_compiled"?, "files": { "agent_core": { "tokens", "budget_tokens", "has_content" }, ... } }` |

---

## 13. 智能体管理 (Agents)

**路由文件**: `src/seeagent/api/routes/agents.py`

### Agent Profile CRUD

#### GET /api/agents/profiles

列出 Agent 档案（系统预设 + 用户创建）。

| 字段 | 说明 |
|------|------|
| **查询参数** | `include_hidden` (默认 false) |
| **响应** | `{ "profiles": [...], "multi_agent_enabled": bool }` |

#### POST /api/agents/profiles

创建自定义 Agent 档案。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "id", "name", "description"?, "icon"?, "color"?, "skills"?: [], "skills_mode"?: "all"|"inclusive"|"exclusive", "custom_prompt"?, "category"? }` |
| **响应** | `{ "status": "ok", "profile": {...} }` |

#### PUT /api/agents/profiles/{profile_id}

更新 Agent 档案。

| 字段 | 说明 |
|------|------|
| **请求** | 部分更新 `{ "name"?, "description"?, "icon"?, "color"?, "skills"?, "skills_mode"?, "custom_prompt"?, "category"? }` |
| **响应** | `{ "status": "ok", "profile": {...} }` |

#### DELETE /api/agents/profiles/{profile_id}

删除自定义 Agent 档案。

#### POST /api/agents/profiles/{profile_id}/reset

重置系统 Agent 档案到出厂默认。

#### PATCH /api/agents/profiles/{profile_id}/visibility

显示/隐藏 Agent 档案。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "hidden": bool }` |

### Agent Categories

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/agents/categories` | GET | 列出所有 Agent 分类 |
| `/api/agents/categories` | POST | 创建自定义分类 `{ "id", "label", "color" }` |
| `/api/agents/categories/{category_id}` | DELETE | 删除自定义分类 |

### Bot CRUD (IM 机器人)

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/agents/bots` | GET | 列出所有配置的 IM 机器人 |
| `/api/agents/bots` | POST | 创建新机器人 `{ "id", "type", "name"?, "agent_profile_id"?, "enabled"?, "credentials": {} }` |
| `/api/agents/bots/{bot_id}` | PUT | 更新机器人配置 |
| `/api/agents/bots/{bot_id}` | DELETE | 删除机器人 |
| `/api/agents/bots/{bot_id}/toggle` | POST | 启用/禁用机器人 `{ "enabled": bool }` |

### Bot Migration

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/agents/env-bots` | GET | 列出 .env 中未迁移的机器人 |
| `/api/agents/bots/migrate-from-env` | POST | 迁移到 im_bots 统一管理 |

### Agent Health & Topology

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/agents/health` | GET | 编排器健康指标 |
| `/api/agents/topology` | GET | 聚合拓扑（池 + 子Agent + 委派边 + 统计） |
| `/api/agents/collaboration/{session_id}` | GET | 会话协作信息 |

### Agent Package Import/Export

**路由文件**: `src/seeagent/api/routes/hub.py`

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/agents/package/export` | POST | 导出 Agent 为 .akita-agent 包 |
| `/api/agents/package/batch-export` | POST | 批量导出为 .zip |
| `/api/agents/package/export-json` | POST | 导出为 JSON |
| `/api/agents/package/batch-export-json` | POST | 批量导出 JSON |
| `/api/agents/package/import` | POST | 导入 Agent (multipart) |
| `/api/agents/package/inspect` | POST | 预览包内容 |
| `/api/agents/package/exportable` | GET | 列出可导出 Agent |

---

## 14. IM 渠道查看 (IM)

**路由文件**: `src/seeagent/api/routes/im.py`

### GET /api/im/channels

列出所有配置的 IM 渠道及在线状态。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "channels": [{ "channel", "name", "status": "online"|"offline", "sessionCount", "lastActive" }] }` |

### GET /api/im/sessions

列出指定 IM 渠道的会话。

| 字段 | 说明 |
|------|------|
| **查询参数** | `channel` |
| **响应** | `{ "sessions": [{ "sessionId", "channel", "chatId", "userId", "state", "lastActive", "messageCount", "lastMessage" }] }` |

### GET /api/im/sessions/{session_id}/messages

获取指定 IM 会话的消息（分页）。

| 字段 | 说明 |
|------|------|
| **查询参数** | `limit` (1-200, 默认50), `offset` (默认0) |
| **响应** | `{ "messages": [{ "role", "content", "timestamp", "metadata"?, "chain_summary"? }], "total", "hasMore" }` |

---

## 15. 定时任务 (Scheduler)

**路由文件**: `src/seeagent/api/routes/scheduler.py`

### GET /api/scheduler/tasks

列出所有定时任务。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "tasks": [...], "total" }` |

### GET /api/scheduler/tasks/{task_id}

获取单个任务详情。

### POST /api/scheduler/tasks

创建定时任务。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "name", "task_type": "reminder"|"task", "trigger_type": "once"|"interval"|"cron", "trigger_config": {}, "reminder_message"?, "prompt"?, "channel_id"?, "chat_id"?, "enabled"? }` |
| **响应** | `{ "status": "ok", "task_id", "task": {...} }` |

### PUT /api/scheduler/tasks/{task_id}

更新定时任务。

| 字段 | 说明 |
|------|------|
| **请求** | 部分更新 `{ "name"?, "task_type"?, "trigger_type"?, "trigger_config"?, "reminder_message"?, "prompt"?, "channel_id"?, "chat_id"?, "enabled"? }` |

### DELETE /api/scheduler/tasks/{task_id}

删除定时任务（系统任务不可删除，只能禁用）。

### POST /api/scheduler/tasks/{task_id}/toggle

切换任务启用/禁用状态。

### POST /api/scheduler/tasks/{task_id}/trigger

立即触发一次任务执行。

### GET /api/scheduler/channels

列出可用的 IM 渠道和 chat_id（用于通知目标选择）。

### GET /api/scheduler/stats

获取调度器统计信息。

---

## 16. Token 用量统计 (Token Stats)

**路由文件**: `src/seeagent/api/routes/token_stats.py`
**前缀**: `/api/stats/tokens`

### GET /api/stats/tokens/summary

按维度聚合的 Token 用量统计。

| 字段 | 说明 |
|------|------|
| **查询参数** | `group_by` (默认 "endpoint_name"), `period`? ("1d","3d","1w","1m","6m","1y"), `start`?, `end`?, `endpoint_name`?, `operation_type`? |
| **响应** | `{ "start", "end", "group_by", "data": [...] }` |

### GET /api/stats/tokens/timeline

时间序列（用于图表）。

| 字段 | 说明 |
|------|------|
| **查询参数** | `interval` (默认 "hour"), `period`?, `start`?, `end`?, `endpoint_name`? |
| **响应** | `{ "start", "end", "interval", "data": [...] }` |

### GET /api/stats/tokens/sessions

按会话的 Token 用量明细。

| 字段 | 说明 |
|------|------|
| **查询参数** | `period`?, `start`?, `end`?, `limit`? (默认50), `offset`? (默认0) |
| **响应** | `{ "start", "end", "data": [...] }` |

### GET /api/stats/tokens/total

总计 Token 用量。

| 字段 | 说明 |
|------|------|
| **查询参数** | `period`?, `start`?, `end`? |
| **响应** | `{ "start", "end", "data": {...} }` |

### GET /api/stats/tokens/by-agent

按 Agent 档案分组的 Token 用量（多 Agent 模式）。

| 字段 | 说明 |
|------|------|
| **查询参数** | `period`?, `start`?, `end`? |
| **响应** | `{ "start", "end", "by_agent": [...] }` |

### GET /api/stats/tokens/context

当前上下文窗口 Token 使用情况。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "context_tokens", "context_limit", "percent" }` |

---

## 17. 文件服务 (Files)

**路由文件**: `src/seeagent/api/routes/files.py`

### GET /api/files

提供工作区文件（图片、文档等）的 HTTP 访问。

| 字段 | 说明 |
|------|------|
| **查询参数** | `path` — 相对于 workspace 或绝对路径 |
| **响应** | FileResponse（自动推断 MIME 类型） |
| **安全** | 仅允许 workspace 和用户 home 目录下的文件 |

---

## 18. 上传 (Upload)

**路由文件**: `src/seeagent/api/routes/upload.py`

### POST /api/upload

上传文件（图片、音频、文档）。

| 字段 | 说明 |
|------|------|
| **请求** | multipart/form-data, `file` 字段 |
| **响应** | `{ "status": "ok", "filename", "original_name", "size", "content_type", "url": "/api/uploads/xxx" }` |
| **限制** | 最大 50MB；禁止 .exe/.bat/.sh 等可执行扩展名 |

### GET /api/uploads/{filename}

获取已上传的文件。

| 字段 | 说明 |
|------|------|
| **响应** | FileResponse |
| **安全** | 防止路径穿越 |

---

## 19. 日志 (Logs)

**路由文件**: `src/seeagent/api/routes/logs.py`

### GET /api/logs/service

读取后端服务日志文件尾部。

| 字段 | 说明 |
|------|------|
| **查询参数** | `tail_bytes` (0-400000, 默认60000) |
| **响应** | `{ "path", "content", "truncated": bool }` |

### POST /api/logs/frontend

接收前端日志上报（Web/Capacitor 模式）。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "lines": ["log line 1", ...] }` |
| **响应** | `{ "ok": bool, "written": int }` |
| **备注** | 每请求最多 100 行；自动 size-based 轮转（5MB / 5 备份） |

### GET /api/logs/frontend

读取前端日志文件尾部。

| 字段 | 说明 |
|------|------|
| **查询参数** | `tail_bytes` (同上) |
| **响应** | `{ "path", "content", "truncated": bool }` |

### GET /api/logs/combined

合并返回后端+前端日志尾部（供导出用）。

| 字段 | 说明 |
|------|------|
| **查询参数** | `tail_bytes` (0-200000, 默认60000) |
| **响应** | `{ "backend": {...}, "frontend": {...} }` |

---

## 20. 工作区备份 (Workspace IO)

**路由文件**: `src/seeagent/api/routes/workspace_io.py`

### GET /api/workspace/backup-settings

读取备份设置。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "settings": { "enabled", "cron", "backup_path", "max_backups", "include_userdata", "include_media" } }` |

### POST /api/workspace/backup-settings

保存备份设置并同步调度任务。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "enabled": bool, "cron": "0 2 * * *", "backup_path": "...", "max_backups": 5, "include_userdata": true, "include_media": false }` |
| **响应** | `{ "status": "ok", "settings": {...} }` |

### POST /api/workspace/export

创建工作区备份 zip。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "output_dir": "...", "include_userdata"?: true, "include_media"?: false }` |
| **响应** | `{ "status": "ok", "path", "filename", "size_bytes" }` |

### POST /api/workspace/import

从备份 zip 恢复工作区。

| 字段 | 说明 |
|------|------|
| **请求** | `{ "zip_path": "..." }` |
| **响应** | `{ "status": "ok", "restored_count", "manifest"? }` |

### GET /api/workspace/backups

列出现有备份文件。

---

## 21. 组织编排 (Orgs)

**路由文件**: `src/seeagent/api/routes/orgs.py`
**前缀**: `/api/orgs`

### 组织 CRUD

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/orgs` | GET | 列出所有组织 (`include_archived`? 查询参数) |
| `/api/orgs` | POST | 创建组织 |
| `/api/orgs/{org_id}` | GET | 获取组织详情 |
| `/api/orgs/{org_id}` | PUT | 更新组织 |
| `/api/orgs/{org_id}` | DELETE | 删除组织 |
| `/api/orgs/{org_id}/duplicate` | POST | 复制组织 (`{ "name"? }`) |
| `/api/orgs/{org_id}/archive` | POST | 归档组织 |
| `/api/orgs/{org_id}/unarchive` | POST | 取消归档 |
| `/api/orgs/{org_id}/save-as-template` | POST | 保存为模板 |
| `/api/orgs/{org_id}/export` | POST | 导出组织为 JSON |

### 模板

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/orgs/templates` | GET | 列出所有模板 |
| `/api/orgs/templates/{template_id}` | GET | 获取模板详情 |
| `/api/orgs/from-template` | POST | 从模板创建组织 |

### 导入

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/orgs/import` | POST | 从 .json/.akita-org 文件导入组织 (multipart) |

### 头像

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/orgs/avatar-presets` | GET | 获取头像预设列表 |
| `/api/orgs/avatars/upload` | POST | 上传自定义头像 (multipart, ≤2MB) |
| `/api/avatars/{filename}` | GET | 静态文件服务 |

### 生命周期

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/orgs/{org_id}/start` | POST | 启动组织 |
| `/api/orgs/{org_id}/stop` | POST | 停止组织 |
| `/api/orgs/{org_id}/pause` | POST | 暂停组织 |
| `/api/orgs/{org_id}/resume` | POST | 恢复组织 |
| `/api/orgs/{org_id}/reset` | POST | 重置组织 |

### 命令

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/orgs/{org_id}/command` | POST | 发送命令 `{ "content", "target_node_id"? }` |
| `/api/orgs/{org_id}/commands/{command_id}` | GET | 轮询命令状态 |
| `/api/orgs/{org_id}/broadcast` | POST | 广播消息 `{ "content" }` |

### 节点管理

| 接口 | 方法 | 说明 |
|------|------|------|
| `/{org_id}/nodes/{node_id}/status` | GET | 节点状态 |
| `/{org_id}/nodes/{node_id}/thinking` | GET | 节点思维过程 |
| `/{org_id}/nodes/{node_id}/prompt-preview` | GET | 预览完整提示词 |
| `/{org_id}/nodes/{node_id}/freeze` | POST | 冻结节点 |
| `/{org_id}/nodes/{node_id}/unfreeze` | POST | 解冻节点 |
| `/{org_id}/nodes/{node_id}/offline` | POST | 设置离线 |
| `/{org_id}/nodes/{node_id}/online` | POST | 设置上线 |
| `/{org_id}/nodes/{node_id}/tasks` | GET | 获取节点任务 |
| `/{org_id}/nodes/{node_id}/active-plan` | GET | 节点活跃计划 |
| `/{org_id}/nodes/{node_id}/dismiss` | DELETE | 解散临时节点 |

### 节点身份

| 接口 | 方法 | 说明 |
|------|------|------|
| `/{org_id}/nodes/{node_id}/identity` | GET | 读取节点身份文件 |
| `/{org_id}/nodes/{node_id}/identity` | PUT | 更新节点身份文件 |

### 节点 MCP 配置

| 接口 | 方法 | 说明 |
|------|------|------|
| `/{org_id}/nodes/{node_id}/mcp` | GET | 读取节点 MCP 配置 |
| `/{org_id}/nodes/{node_id}/mcp` | PUT | 更新节点 MCP 配置 |

### 节点定时任务

| 接口 | 方法 | 说明 |
|------|------|------|
| `/{org_id}/nodes/{node_id}/schedules` | GET | 列出节点定时任务 |
| `/{org_id}/nodes/{node_id}/schedules` | POST | 创建 |
| `/{org_id}/nodes/{node_id}/schedules/{schedule_id}` | PUT | 更新 |
| `/{org_id}/nodes/{node_id}/schedules/{schedule_id}` | DELETE | 删除 |
| `/{org_id}/nodes/{node_id}/schedules/{schedule_id}/trigger` | POST | 手动触发 |

### 组织记忆（黑板）

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/orgs/{org_id}/memory` | GET | 查询记忆 |
| `/api/orgs/{org_id}/memory` | POST | 写入记忆 |
| `/api/orgs/{org_id}/memory/{memory_id}` | DELETE | 删除记忆 |

### 事件与消息

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/orgs/{org_id}/events` | GET | 查询事件 |
| `/api/orgs/{org_id}/events/replay` | GET | 事件回放数据 |
| `/api/orgs/{org_id}/messages` | GET | 通信记录 |

### 策略文件

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/orgs/{org_id}/policies` | GET | 列出策略文件 |
| `/api/orgs/{org_id}/policies/search` | GET | 搜索策略 |
| `/api/orgs/{org_id}/policies/{filename}` | GET | 读取策略 |
| `/api/orgs/{org_id}/policies/{filename}` | PUT | 写入策略 |
| `/api/orgs/{org_id}/policies/{filename}` | DELETE | 删除策略 |

### 收��箱

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/orgs/{org_id}/inbox` | GET | 列出收件箱消息 |
| `/api/orgs/{org_id}/inbox/{msg_id}/read` | POST | 标记已读 |
| `/api/orgs/{org_id}/inbox/read-all` | POST | 全部标记已读 |
| `/api/orgs/{org_id}/inbox/{msg_id}/resolve` | POST | 处理审批 |

### 扩缩容

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/orgs/{org_id}/scaling/requests` | GET | 扩缩容请求列表 |
| `/api/orgs/{org_id}/scaling/{request_id}/approve` | POST | 批准 |
| `/api/orgs/{org_id}/scaling/{request_id}/reject` | POST | 拒绝 |
| `/api/orgs/{org_id}/scale/clone` | POST | 克隆节点 |
| `/api/orgs/{org_id}/scale/recruit` | POST | 招募新节点 |

### 统计与报告

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/orgs/{org_id}/stats` | GET | 实时组织统计 |
| `/api/orgs/{org_id}/reports/summary` | GET | 报告摘要 |
| `/api/orgs/{org_id}/reports/generate` | POST | 生成报告 |
| `/api/orgs/{org_id}/reports` | GET | 列出已生成报告 |
| `/api/orgs/{org_id}/audit-log` | GET | 审计日志 |

### SSE 状态流

#### GET /api/orgs/{org_id}/status

实时组织状态 SSE 流。

| 字段 | 说明 |
|------|------|
| **响应** | SSE 流，事件: `connected`, `inbox`, `heartbeat` |

### 心跳与站会

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/orgs/{org_id}/heartbeat/trigger` | POST | 手动触发心跳 |
| `/api/orgs/{org_id}/standup/trigger` | POST | 手动触发站会 |

### IM 回复

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/orgs/{org_id}/im-reply` | POST | 处理 IM 通知的回复 |

### 项目看板

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/orgs/{org_id}/projects` | GET | 列出项目 |
| `/api/orgs/{org_id}/projects` | POST | 创建项目 |
| `/api/orgs/{org_id}/projects/{project_id}` | GET | 获取项目 |
| `/api/orgs/{org_id}/projects/{project_id}` | PUT | 更新项目 |
| `/api/orgs/{org_id}/projects/{project_id}` | DELETE | 删除项目 |
| `/{project_id}/tasks` | POST | 创建任务 |
| `/{project_id}/tasks/{task_id}` | PUT | 更新任务 |
| `/{project_id}/tasks/{task_id}` | DELETE | 删除任务 |
| `/{project_id}/tasks/{task_id}/dispatch` | POST | 派发任务 |
| `/api/orgs/{org_id}/tasks` | GET | 跨项目任务聚合 |
| `/api/orgs/{org_id}/tasks/{task_id}` | GET | 任务详情 |
| `/api/orgs/{org_id}/tasks/{task_id}/tree` | GET | 子任务树 |
| `/api/orgs/{org_id}/tasks/{task_id}/timeline` | GET | 任务时间线 |

### 跨组织收件箱

**前缀**: `/api/org-inbox`

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/org-inbox` | GET | 全局收件箱（跨组织） |
| `/api/org-inbox/unread-count` | GET | 全局未读计数 |
| `/api/org-inbox/{msg_id}/read` | POST | 跨组织标记已读 |
| `/api/org-inbox/read-all` | POST | 全部标记已读 |
| `/api/org-inbox/{msg_id}/act` | POST | 执行操作（审批） |

---

## 22. Hub / Store (平台市场)

**路由文件**: `src/seeagent/api/routes/hub.py`

### Agent Store

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/hub/agents` | GET | 搜索 Agent 商店 |
| `/api/hub/agents/{agent_id}` | GET | Agent 详情 |
| `/api/hub/agents/{agent_id}/install` | POST | 安装 Agent |

### Skill Store

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/hub/skills` | GET | 搜索 Skill 商店 |
| `/api/hub/skills/{skill_id}` | GET | Skill 详情 |
| `/api/hub/skills/{skill_id}/install` | POST | 安装 Skill |

> **注意**: Hub 接口为代理转发，远程平台离线时返回 502。

---

## 23. 反馈与 Bug 报告

**路由文件**: `src/seeagent/api/routes/bug_report.py`

### GET /api/system-info

返回系统环境信息（用于 Bug 报告表单）。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "os", "python", "seeagent_version", "packages": {}, "memory_total_gb"?, "disk_free_gb"?, "git_version", "node_version", "npm_version", "im_channels", ... }` |

### GET /api/feedback-config

返回反馈配置（CAPTCHA 标识等公共配置）。

| 字段 | 说明 |
|------|------|
| **响应** | `{ "captcha_scene_id", "captcha_prefix" }` |

### POST /api/bug-report

提交 Bug 报告（含系统信息、日志、LLM 调试文件）。

| 字段 | 说明 |
|------|------|
| **请求** | multipart/form-data: `title`, `description`, `captcha_verify_param`?, `steps`?, `upload_logs`?, `upload_debug`?, `images`[]? |
| **响应** | `{ "status": "ok", "report_id", "size_bytes", "issue_url"? }` 或 `{ "status": "upload_failed", "local_path", "download_url" }` |

### POST /api/feature-request

提交需求建议。

| 字段 | 说明 |
|------|------|
| **请求** | multipart/form-data: `title`, `description`, `captcha_verify_param`?, `contact_email`?, `contact_wechat`?, `images`[]? |
| **响应** | 同 bug-report |

### GET /api/feedback-download/{report_id}

下载本地保存的反馈 zip 包（上传失败时的备用下载）。

---

## API 端点汇总

| 分类 | 端点数 |
|------|--------|
| 认证 | 6 |
| 通用对话 (不含 cancel) | 6 |
| 会话管理 (不含 generate-title) | 4 |
| 最佳实践辅助 | 2 |
| WebSocket | 1 |
| 健康/诊断 | 5 |
| 配置管理 | 14 |
| LLM 模型 | 1 |
| 技能管理 | 8 |
| MCP | 7 |
| 记忆 | 8 |
| 身份 | 7 |
| 智能体 | ~20 |
| IM | 3 |
| 定时任务 | 8 |
| Token 统计 | 6 |
| 文件 | 1 |
| 上传 | 2 |
| 日志 | 4 |
| 工作区备份 | 5 |
| 组织编排 | ~65 |
| Hub/Store | 6 |
| 反馈 | 5 |
| **合计** | **~190+ 个 API 端点** |

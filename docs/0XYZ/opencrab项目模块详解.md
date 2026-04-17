# OpenCrab 项目模块详解文档

## 概述

OpenCrab（SeeAgent）是一个基于"Ralph Wiggum 模式"的多智能体 AI 助手系统，其核心特点是"永不放弃"的执行模式——通过状态持久化和重试机制确保任务完成。项目采用 Python 后端 + React 前端（Tauri 桌面应用）架构，支持 6 种 IM 渠道和 30+ LLM 提供商。

**当前版本**: 1.26.2

**技术栈**:
- **后端**: Python 3.11+ (FastAPI, asyncio, aiosqlite)
- **前端**: React 18 + TypeScript + Vite 6 (apps/setup-center/)
- **桌面端**: Tauri 2.x (Rust shell)
- **LLM**: Anthropic Claude, OpenAI 兼容 API (30+ 提供商)
- **IM 渠道**: Telegram, 飞书, 钉钉, 企业微信, QQ, OneBot

---

## 目录结构总览

```
opencrab/
├── src/seeagent/           # 核心 Python 后端 (~17个核心模块)
├── identity/               # Agent 身份定义文件
├── skills/                 # 技能定义（system/ + external/，70+技能）
├── apps/
│   └── setup-center/       # 桌面 GUI（Tauri + React + Vite）
├── tests/                  # 5层测试金字塔
├── docs/                   # 架构和部署文档
├── specs/                  # 技术规格说明
├── data/                   # 运行时数据存储
├── mcps/                   # MCP 服务器配置
├── channels/               # IM 渠道集成
├── cloud/                  # 云服务集成
├── auth_api/               # 认证 API
├── identity/               # 身份配置
├── plugins/                # 插件系统
├── best_practice/          # 最佳实践
├── research/               # 研究资料
├── 0deron/                 # 待归类内容
└── 0XYZ/                   # 本文档
```

---

## 一、核心模块（src/seeagent/）

### 1. core/ — 核心 Agent 系统

**位置**: `src/seeagent/core/`

**功能描述**: core 模块是整个 Agent 系统的核心，包含了主 Agent 逻辑、大脑（LLM 交互）、Ralph 循环引擎和推理引擎。

#### 1.1 核心文件

| 文件 | 大小 | 功能描述 |
|------|------|----------|
| `agent.py` | ~330KB | 主 Agent 类，协调所有模块（最大文件） |
| `brain.py` | ~71KB | LLM 交互层，封装 LLMClient，支持流式输出 |
| `reasoning_engine.py` | ~176KB | ReAct 推理引擎（Think → Act → Observe） |
| `ralph.py` | ~10KB | "Never give up" 重试循环引擎 |
| `identity.py` | ~16KB | Agent 身份管理 |
| `persona.py` | ~19KB | 人设管理，8种内置人设 |
| `prompt_assembler.py` | ~15KB | 动态提示词组装 |
| `context_manager.py` | ~44KB | 上下文窗口管理 |
| `tool_executor.py` | ~21KB | 工具执行引擎 |
| `skill_manager.py` | ~15KB | 技能加载和管理 |
| `supervisor.py` | ~19KB | 运行时监督器 |
| `task_monitor.py` | ~25KB | 任务监控与回顾性分析 |
| `resource_budget.py` | ~10KB | 资源预算管理 |
| `token_tracking.py` | ~6KB | Token 使用量追踪 |
| `response_handler.py` | ~16KB | LLM 响应处理 |
| `agent_state.py` | ~18KB | Agent 状态管理 |
| `policy.py` | ~13KB | 策略执行 |
| `validators.py` | ~11KB | 输入验证 |
| `proactive.py` | ~19KB | 主动行为引擎 |

#### 1.2 关键类

- **`Agent`**: 主协调器，负责整体任务执行流程，协调 Brain、Memory、Tools、Skills、Reasoning
- **`Brain`**: LLM 接口，处理流式响应和工具调用，支持流式累积和 idle 超时检测
- **`RalphLoop`**: 执行循环，支持状态持久化和断点恢复，"永不放弃"
- **`ReasoningEngine`**: ReAct 模式实现，显式推理-行动-观察循环
- **`Identity`**: 身份文档加载器，管理 SOUL.md、AGENT.md、USER.md、MEMORY.md
- **`PersonaManager`**: 人设管理系统，支持 8 种内置人设

#### 1.3 与其他模块的关联

```
core/agent.py ──────┬──► brain.py (LLM 调用)
                    ├──► tool_executor.py (工具执行)
                    ├──► memory/manager.py (记忆管理)
                    ├──► skills/skill_manager.py (技能管理)
                    └──► reasoning_engine.py (推理引擎)
```

---

### 2. agents/ — 多智能体系统

**位置**: `src/seeagent/agents/`

**功能描述**: 实现多智能体协作，支持子 Agent 创建、任务委派和实例池管理。

#### 2.1 核心文件

| 文件 | 大小 | 功能描述 |
|------|------|----------|
| `orchestrator.py` | ~47KB | 中央多智能体协调器，路由消息，处理委派（最大深度=5） |
| `factory.py` | ~17KB | 从 AgentProfile 创建 Agent 实例 |
| `presets.py` | ~32KB | Agent 预设/模板定义 |
| `profile.py` | ~15KB | AgentProfile 数据类 |
| `task_queue.py` | ~7KB | 异步任务队列管理 |
| `packager.py` | ~25KB | Agent 打包分享 |
| `fallback.py` | ~5KB | Agent 失败时的回退处理 |
| `lock_manager.py` | ~4KB | 分布式锁管理 |
| `manifest.py` | ~6KB | Agent 清单 |

#### 2.2 关键类

- **`AgentOrchestrator`**: 路由消息到各 Agent，处理委派逻辑，支持最多 5 层委派深度
- **`AgentFactory`**: 创建差异化的 Agent 实例
- **`AgentInstancePool`**: 按会话的实例管理，空闲超时 30 分钟
- **`AgentProfile`**: 每个 Agent 类型的配置
- **`ProfileStore`**: 持久化 Agent 配置到 JSON

#### 2.3 预设 Agent 类型

| Agent | 描述 |
|-------|------|
| `default` | 通用助手（"小秋"） |
| `content-creator` | 社交媒体内容创作 |
| `video-planner` | 视频脚本规划 |
| `office-doc` | 文档处理（"文助"） |
| `code-assistant` | 编程助手 |
| `browser-agent` | 网页浏览 |
| `data-analyst` | 数据分析 |

#### 2.4 与其他模块的关联

```
agents/orchestrator.py ──┬──► core/agent.py (创建子 Agent)
                         ├──► agents/profile.py (Agent 配置)
                         └──► sessions/manager.py (会话管理)
```

---

### 3. tools/ — 工具系统（89+ 工具）

**位置**: `src/seeagent/tools/`

**功能描述**: 提供 Agent 可调用的外部能力，包括文件操作、Shell 命令、浏览器自动化等。工具采用渐进式暴露设计（list → details → execute）。

#### 3.1 核心文件

| 文件 | 大小 | 功能描述 |
|------|------|----------|
| `catalog.py` | ~14KB | 工具目录，支持渐进式暴露 |
| `mcp_catalog.py` | ~13KB | MCP 工具目录 |
| `shell.py` | ~16KB | Shell 命令执行工具 |
| `file.py` | ~8KB | 文件操作工具 |
| `sticker.py` | ~10KB | 表情包生成工具 |
| `web.py` | ~6KB | 网页搜索工具 |
| `mcp.py` | ~29KB | MCP 客户端集成 |
| `subprocess_bridge.py` | ~8KB | 子进程桥接 |
| `errors.py` | ~6KB | 工具错误定义 |
| `_import_helper.py` | ~5KB | 导入辅助工具 |

#### 3.2 工具处理器（handlers/）

| 处理器 | 功能描述 |
|--------|----------|
| `browser.py` | 基于 Playwright 的浏览器自动化 |
| `filesystem.py` | 文件读写操作 |
| `shell.py` | 命令执行 |
| `memory.py` | 记忆操作 |
| `skills.py` | 技能管理（安装/发现） |
| `plan.py` | 规划工具（创建计划、更新步骤） |
| `scheduled.py` | 定时任务管理 |
| `im_channel.py` | IM 渠道操作 |
| `agent.py` | 子 Agent 委派 |
| `config.py` | 配置工具 |
| `mcp.py` | MCP 工具代理 |
| `system.py` | 系统工具 |
| `web_search.py` | 网页搜索集成 |
| `sticker.py` | 表情包生成 |
| `persona.py` | 人设切换 |
| `profile.py` | Profile 管理 |
| `agent_hub.py` | Agent Hub |
| `agent_package.py` | Agent 打包 |
| `desktop.py` | 桌面自动化 |
| `skill_store.py` | 技能商店访问 |

#### 3.3 高频工具（全量注入）

`run_shell`, `read_file`, `write_file`, `list_directory`, `ask_user`

#### 3.4 与其他模块的关联

```
tools/tool_executor.py ──► tools/handlers/* (具体实现)
                         └──► tools/catalog.py (工具注册)
```

---

### 4. memory/ — 记忆系统（v2，三层架构）

**位置**: `src/seeagent/memory/`

**功能描述**: 三层记忆系统，支持语义记忆、情节记忆和工作记忆，提供向量检索和 consolidation。

#### 4.1 核心文件

| 文件 | 大小 | 功能描述 |
|------|------|----------|
| `manager.py` | ~42KB | 中央记忆管理器（v2） |
| `storage.py` | ~62KB | SQLite 存储（FTS5 全文搜索） |
| `vector_store.py` | ~19KB | 向量存储（嵌入） |
| `extractor.py` | ~40KB | AI 驱动的记忆提取 |
| `consolidator.py` | ~25KB | 记忆整合，支持时间戳前缀和文件迁移 |
| `retrieval.py` | ~21KB | 多路径检索和重排序 |
| `types.py` | ~21KB | 记忆类型定义（7种类型） |
| `unified_store.py` | ~11KB | SQLite + SearchBackend 统一存储 |
| `search_backends.py` | ~12KB | 可插拔搜索后端 |
| `model_hub.py` | ~16KB | 嵌入模型 Hub |
| `lifecycle.py` | ~29KB | 记忆生命周期管理 |
| `daily_consolidator.py` | ~19KB | 每日整合 |

#### 4.2 记忆类型（types.py）

| 类型 | 描述 |
|------|------|
| `FACT` | 事实知识 |
| `PREFERENCE` | 用户偏好 |
| `SKILL` | 技能 |
| `CONTEXT` | 上下文/情节记忆 |
| `RULE` | 规则 |
| `ERROR` | 错误经验 |
| `PERSONA_TRAIT` | 人设特征 |
| `EXPERIENCE` | 任务经验 |

#### 4.3 记忆优先级

`TRANSIENT` → `SHORT_TERM` → `LONG_TERM` → `PERMANENT`

#### 4.4 搜索后端

| 后端 | 描述 |
|------|------|
| `FTS5Backend` | 默认，零依赖 |
| `ChromaDBBackend` | 本地向量搜索 |
| `APIEmbeddingBackend` | 在线 API（DashScope/OpenAI） |

#### 4.5 与其他模块的关联

```
memory/manager.py ──┬──► memory/unified_store.py
                    ├──► memory/vector_store.py
                    └──► memory/search_backends.py

memory/retrieval.py ──► prompt/retriever.py (提示词检索)
```

---

### 5. prompt/ — 提示词管道（v2）

**位置**: `src/seeagent/prompt/`

**功能描述**: 编译身份文档为优化片段，组装系统提示词，处理 token 预算和工具调用守卫。

#### 5.1 核心文件

| 文件 | 大小 | 功能描述 |
|------|------|----------|
| `builder.py` | ~44KB | 分层组装系统提示词 |
| `compiler.py` | ~17KB | 身份文档编译器 |
| `budget.py` | ~9KB | Token 预算裁剪 |
| `guard.py` | ~9KB | 运行时工具调用守卫 |
| `retriever.py` | ~5KB | 用于提示词的记忆检索 |

#### 5.2 提示词组装顺序（8层）

1. **Identity** — SOUL.md 原则
2. **Persona** — 人设
3. **Runtime** — 运行时
4. **Session Rules** — 会话规则
5. **AGENTS.md** — 项目上下文
6. **Catalogs** — 工具/技能/MCP 目录
7. **Memory** — 语义检索记忆
8. **User** — 用户信息

#### 5.3 与其他模块的关联

```
prompt/builder.py ──┬──► identity/runtime/ (编译后的身份片段)
                    ├──► memory/retrieval.py (记忆检索)
                    └──► tools/catalog.py (工具目录)
```

---

### 6. llm/ — LLM 客户端

**位置**: `src/seeagent/llm/`

**功能描述**: 统一的 LLM 客户端，支持流式输出、工具调用和多 Provider fallback。

#### 6.1 核心文件

| 文件 | 大小 | 功能描述 |
|------|------|----------|
| `client.py` | ~69KB | 统一 LLM 客户端，流式、工具调用、fallback |
| `config.py` | ~14KB | 端点配置加载 |
| `types.py` | ~19KB | 请求/响应类型定义 |
| `capabilities.py` | ~29KB | 模型能力定义 |
| `adapter.py` | ~7KB | 向后兼容适配器 |
| `stt_client.py` | ~7KB | 语音转文字客户端 |
| `proxy_utils.py` | ~7KB | 代理工具 |
| `anthropic.py` | ~15KB | Anthropic Provider |
| `openai.py` | ~32KB | OpenAI 兼容 Provider |

#### 6.2 支持的 Providers

| Provider | 描述 |
|----------|------|
| `anthropic` | Anthropic API（Claude 系列） |
| `openai` | OpenAI 兼容 API（30+ 提供商） |

#### 6.3 关键类型

- `LLMRequest`, `LLMResponse`
- `Message`, `ContentBlock`
- `Tool`, `ToolUseBlock`, `ToolResultBlock`
- `EndpointConfig`

#### 6.4 与其他模块的关联

```
llm/client.py ──► providers/* (具体 Provider)
                 └──► core/brain.py (使用 LLMClient)
```

---

### 7. channels/ — IM 渠道适配器

**位置**: `src/seeagent/channels/`

**功能描述**: 支持多种即时通讯渠道的统一消息路由。

#### 7.1 核心文件

| 文件 | 大小 | 功能描述 |
|------|------|----------|
| `gateway.py` | ~130KB | 统一消息路由，处理中断（最大文件） |
| `base.py` | ~9KB | ChannelAdapter 接口 |
| `types.py` | ~17KB | 消息类型定义 |
| `group_response.py` | ~3KB | 群聊响应策略 |

#### 7.2 支持的渠道

| 渠道 | 依赖 | 描述 |
|------|------|------|
| Telegram | `python-telegram-bot` | Telegram Bot |
| Feishu | `lark-oapi` | 飞书（ Lark）Bot |
| DingTalk | `dingtalk-stream` | 钉钉 Bot |
| WeCom | `wework` | 企业微信 HTTP 回调 / WebSocket |
| QQ | `qq-botpy` | QQ 官方 Bot |
| OneBot | `websockets` | OneBot 协议（NapCat/Lagrange） |

#### 7.3 渠道适配器结构

```
channels/
├── gateway.py          # 中央消息路由
├── base.py             # 基础适配器接口
├── types.py            # 类型定义
├── adapters/           # 平台特定实现
│   ├── telegram.py
│   ├── feishu.py
│   ├── dingtalk.py
│   ├── wework_bot.py
│   ├── wework_ws.py
│   ├── onebot.py
│   └── qq_official.py
└── media/              # 媒体处理（音频、图片）
```

#### 7.4 与其他模块的关联

```
channels/gateway.py ──► sessions/manager.py
                       └──► api/routes/chat.py
```

---

### 8. api/ — FastAPI 服务器

**位置**: `src/seeagent/api/`

**功能描述**: 提供 RESTful API 和 WebSocket 支持，默认端口 18900。

#### 8.1 核心文件

| 文件 | 大小 | 功能描述 |
|------|------|----------|
| `server.py` | ~19KB | FastAPI 应用设置 |
| `auth.py` | ~15KB | 认证中间件 |
| `schemas.py` | ~3KB | Pydantic schemas |
| `sse_utils.py` | ~2KB | SSE 工具 |

#### 8.2 主要路由（routes/）

| 路由 | 文件 | 功能 |
|------|------|------|
| `/chat` | `chat.py` | 聊天端点（SSE 流式） |
| `/agents` | `agents.py` | 多 Agent 管理 |
| `/config` | `config.py` | 配置端点 |
| `/skills` | `skills.py` | 技能管理 |
| `/memory` | `memory.py` | 记忆操作 |
| `/scheduler` | `scheduler.py` | 任务调度 |
| `/mcp` | `mcp.py` | MCP 服务器管理 |
| `/orgs` | `orgs.py` | 组织管理 |
| `/health` | `health.py` | 健康检查 |
| `/websocket` | `websocket.py` | WebSocket 支持 |
| `/sessions` | `sessions.py` | 会话管理 |
| `/files` | `files.py` | 文件上传/下载 |
| `/logs` | `logs.py` | 日志查询 |
| `/hub` | `hub.py` | Agent/技能市场 |
| `/identity` | `identity.py` | 身份管理 |
| `/bestpractice` | `bestpractice.py` | 最佳实践 |
| `/chat_models` | `chat_models.py` | 聊天模型 |
| `/im` | `im.py` | IM 渠道管理 |
| `/orgs` | `orgs.py` | 组织 |
| `/token_stats` | `token_stats.py` | Token 统计 |
| `/upload` | `upload.py` | 文件上传 |
| `/workspace_io` | `workspace_io.py` | 工作区 IO |

#### 8.3 与其他模块的关联

```
api/routes/* ──┬──► agents/orchestrator.py
               ├──► memory/manager.py
               ├──► skills/loader.py
               └──► sessions/manager.py
```

---

### 9. skills/ — 技能系统

**位置**: `src/seeagent/skills/`

**功能描述**: 发现和加载 SKILL.md 文件，提供技能注册表和渐进式暴露。

#### 9.1 核心文件

| 文件 | 大小 | 功能描述 |
|------|------|----------|
| `loader.py` | ~26KB | 发现和加载 SKILL.md 文件 |
| `registry.py` | ~14KB | 技能注册表 |
| `parser.py` | ~13KB | SKILL.md 解析器 |
| `catalog.py` | ~5KB | 技能目录生成 |
| `i18n.py` | ~5KB | 国际化 |

#### 9.2 技能加载顺序（优先级从高到低）

1. `__builtin__` — 内置技能
2. `__user_workspace__` — 工作区特定
3. `skills/` — 项目级别
4. `.cursor/skills` — Cursor IDE 集成
5. `.claude/skills` — Claude IDE 集成
6. 全局 home 目录

#### 9.3 系统技能（skills/system/）- 70+ 内置技能

- `add-memory`, `browser-*`, `cancel-scheduled-task`
- `create-plan`, `complete-plan`, `deliver-artifacts`
- `enable-thinking`, `export-agent`, `find-skills`
- `generate-*`, `get-*`, `call-mcp-tool` 等

#### 9.4 外部技能（skills/external/）

- `xiaohongshu-creator` — 小红书内容创作
- `wechat-article` — 微信公众号文章
- `douyin-tool` — 抖音工具
- `summarizer` — 内容摘要
- `knowledge-capture` — 知识捕获
- `pretty-mermaid` — Mermaid 图表
- `ppt-creator` — PPT 创建
- `apify-scraper` — 网页爬虫
- `translate-pdf` — PDF 翻译
- `todoist-task` — Todoist 集成

#### 9.5 技能格式

遵循 Agent Skills 规范（agentskills.io），基于 SKILL.md 文件定义。

#### 9.6 与其他模块的关联

```
skills/loader.py ──► skills/registry.py
                   └──► skills/catalog.py ──► prompt/builder.py
```

---

### 10. scheduler/ — 任务调度器

**位置**: `src/seeagent/scheduler/`

**功能描述**: 类 Cron 的任务调度器，支持时区、最大并发数和任务超时。

#### 10.1 核心文件

| 文件 | 大小 | 功能描述 |
|------|------|----------|
| `scheduler.py` | ~19KB | 主调度器 |
| `executor.py` | ~35KB | 任务执行器（支持 Cron） |
| `task.py` | ~12KB | 任务定义 |
| `triggers.py` | ~10KB | 触发器实现 |
| `consolidation_tracker.py` | ~5KB | 记忆整合调度跟踪 |

#### 10.2 与其他模块的关联

```
scheduler/scheduler.py ──► tasks (延迟任务)
                          └──► memory/consolidation_tracker.py
```

---

### 11. sessions/ — 会话管理

**位置**: `src/seeagent/sessions/`

**功能描述**: 管理用户会话，包括会话创建、状态维护和用户管理。

#### 11.1 核心文件

| 文件 | 大小 | 功能描述 |
|------|------|----------|
| `manager.py` | ~600+ | 会话管理器 |

---

### 12. evolution/ — 自我进化引擎

**位置**: `src/seeagent/evolution/`

**功能描述**: 自动诊断和修复，分析失败模式，生成和安装新技能。

#### 12.1 核心文件

| 文件 | 大小 | 功能描述 |
|------|------|----------|
| `self_check.py` | ~63KB | 自我诊断和修复（每日自检） |
| `generator.py` | ~12KB | 技能/代码生成 |
| `installer.py` | ~5KB | 能力安装 |
| `log_analyzer.py` | ~13KB | 日志分析学习 |
| `failure_analysis.py` | ~15KB | 基于模式的失败分析 |
| `analyzer.py` | ~6KB | 分析工具 |

#### 12.2 进化流程

1. **日志分析** (`log_analyzer.py`) — 分析执行日志
2. **失败分析** (`failure_analysis.py`) — 根因分析
3. **技能生成** (`generator.py`) — 生成新技能
4. **技能安装** (`installer.py`) — 安装到系统

---

### 13. evaluation/ — 评估系统

**位置**: `src/seeagent/evaluation/`

**功能描述**: 质量评估流水线，使用 LLM-as-judge 评分。

---

### 14. tracing/ — 执行追踪

**位置**: `src/seeagent/tracing/`

**功能描述**: Agent 执行追踪和导出。

---

### 15. orgs/ — 组织系统（多租户）

**位置**: `src/seeagent/orgs/`

**功能描述**: 多租户组织管理，支持组织隔离、共享黑板和 Agent 心跳。

#### 15.1 核心文件

| 文件 | 大小 | 功能描述 |
|------|------|----------|
| `runtime.py` | ~900+ | 组织运行时上下文 |
| `manager.py` | - | 组织管理 |
| `blackboard.py` | - | Agent 共享黑板 |
| `identity.py` | - | 组织身份管理 |
| `models.py` | - | 数据模型 |
| `tool_handler.py` | - | 按组织的工具处理 |
| `event_store.py` | - | 事件溯源 |
| `heartbeat.py` | - | Agent 心跳 |
| `messenger.py` | - | Agent 间消息 |

---

### 16. 其他模块

| 模块 | 位置 | 功能描述 |
|------|------|----------|
| `bestpractice/` | `src/seeagent/bestpractice/` | 最佳实践引擎（结构化工作流） |
| `logging/` | `src/seeagent/logging/` | 日志设置 |
| `storage/` | `src/seeagent/storage/` | 通用存储工具 |
| `workspace/` | `src/seeagent/workspace/` | 工作区管理 |
| `hub/` | `src/seeagent/hub/` | Agent/技能市场 |
| `mcp_servers/` | `src/seeagent/mcp_servers/` | MCP 服务器实现（web_search 等） |
| `testing/` | `src/seeagent/testing/` | 测试工具 |

---

## 二、身份系统（identity/）

**位置**: `identity/`

**功能描述**: 定义 Agent 的核心价值观、行为规范、用户偏好和持久记忆。

| 文件 | 大小 | 功能描述 |
|------|------|----------|
| `SOUL.md` | ~15KB | 核心哲学和价值观 |
| `AGENT.md` | ~10KB | 行为规范 |
| `USER.md` | ~1.8KB | 用户偏好 |
| `MEMORY.md` | ~3.4KB | 持久记忆 |
| `POLICIES.yaml` | - | 策略规则（工具权限） |
| `runtime/` | - | 编译后的身份片段 |
| `personas/` | - | 人设预设（8种） |

### 内置人设（identity/personas/）

1. `default.md` — 默认
2. `boyfriend.md` — 男友
3. `business.md` — 商务
4. `butler.md` — 管家
5. `family.md` — 家人
6. `girlfriend.md` — 女友
7. `jarvis.md` — JARVIS 风格
8. `tech_expert.md` — 技术专家

---

## 三、技能目录（skills/）

**位置**: `skills/`

**功能描述**: 存放预定义的技能定义，包括内置技能和外部技能。

| 目录 | 功能描述 |
|------|----------|
| `system/` | 内置系统技能（70+ 个） |
| `external/` | 外部技能（用户可安装） |

---

## 四、MCP 系统

### MCP 配置（mcps/）

| 目录 | 功能描述 |
|------|----------|
| `chrome-devtools/` | Chrome DevTools MCP |
| `web-search/` | 网页搜索 MCP |

### MCP 服务器实现（src/seeagent/mcp_servers/）

| 文件 | 功能描述 |
|------|----------|
| `web_search.py` | 网页搜索 MCP 服务器 |

---

## 五、应用（apps/）

### 5.1 setup-center/ — 桌面 GUI

**技术栈**: Tauri 2.x + React 18 + TypeScript + Vite 6

**功能描述**: 基于 Tauri + React 的桌面应用，提供可视化配置界面。

```bash
cd apps/setup-center && npm ci && npm run build         # 构建
cd apps/setup-center && VITE_BUILD_TARGET=web npm run build:web  # Web standalone
cd apps/setup-center && npx tauri build                 # Tauri 桌面应用
```

---

## 六、测试（tests/）— 5层测试金字塔

**位置**: `tests/`

| 层级 | 目录 | 运行时间 | 描述 |
|------|------|----------|------|
| L1 | `unit/` | <30s | 单元测试 |
| L2 | `component/` | <2min | 组件测试 |
| L3 | `integration/` | <3min | 集成测试 |
| L4 | `e2e/` | - | 端到端测试（需要 LLM_TEST_MODE=replay） |
| L5 | `quality/` | - | 质量评估 |

---

## 七、运行时数据目录（data/）

**位置**: `data/`

**功能描述**: 项目运行后动态生成的运行时数据存储目录。

| 文件/目录 | 功能描述 | 控制代码 |
|-----------|----------|----------|
| `agent.db` | 主 SQLite 数据库 | `config.py` |
| `backend.heartbeat` | 后端心跳文件 | 调度器维护 |
| `device.json` | 设备 ID（16位十六进制） | `hub/device.py` |
| `llm_endpoints.json` | LLM 端点配置 | - |
| `sub_agent_states.json` | 子 Agent 状态 | `core/agent.py` |
| `agents/` | AgentProfile 配置 | `orgs/runtime.py` |
| `avatars/` | 用户/组织头像 | `api/routes/orgs.py` |
| `delegation_logs/` | 委派日志（按日期分片） | `agents/orchestrator.py` |
| `llm_debug/` | LLM 请求/响应调试日志 | `core/brain.py` |
| `memory/` | 三层记忆系统存储 | `memory/manager.py` |
| `orgs/` | 多租户组织数据 | `orgs/manager.py` |
| `plans/` | Agent 计划文件 | `tools/handlers/plan.py` |
| `react_traces/` | ReAct 执行追踪 | `core/reasoning_engine.py` |
| `retrospects/` | 任务回顾日志 | `core/reasoning_engine.py` |
| `scheduler/` | 调度器数据（tasks/executions） | `scheduler/scheduler.py` |
| `selfcheck/` | 自我诊断报告 | `evolution/self_check.py` |
| `sessions/` | 会话状态持久化 | `sessions/manager.py` |
| `sticker/` | 表情包数据 | `tools/sticker.py` |
| `traces/` | 执行追踪导出 | `tracing/exporter.py` |
| `user/` | 用户偏好状态 | `core/user_profile.py` |
| `media/{channel}/` | 各 IM 渠道媒体文件 | 各渠道适配器 |

---

## 八、关键设计模式

### 8.1 Ralph Wiggum 模式

永不放弃的执行模式：
- 将状态持久化到文件
- 失败时从断点恢复
- 无限重试（可配置上限）

### 8.2 ReAct 推理

Think → Act → Observe 三阶段显式推理循环。

### 8.3 多智能体委派

- AgentOrchestrator 协调多个子 Agent
- 最大委派深度 = 5
- 支持 Agent 实例池（空闲超时 30 分钟）

### 8.4 三层记忆

- **Working Memory** — 对话上下文
- **Core Memory** — 持久化（aiosqlite）
- **Dynamic Memory** — 语义检索（向量嵌入）

### 8.5 渐进式暴露工具

Level 1 (list) → Level 2 (details) → Level 3 (execute)

### 8.6 自我进化

- 每日自检 (`self_check.py`)
- 失败分析 (`failure_analysis.py`)
- 技能生成 (`generator.py`)
- 自动安装 (`installer.py`)

---

## 九、架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              用户层                                          │
│        CLI / API / Telegram / Feishu / DingTalk / QQ / 企业微信 / OneBot   │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
┌────────────────────────────────────▼────────────────────────────────────────┐
│                          MessageGateway                                     │
│                      （IM 渠道适配 + 消息路由）                               │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
┌────────────────────────────────────▼────────────────────────────────────────┐
│                        AgentOrchestrator                                    │
│                    （多 Agent 协调 + 任务委派）                               │
│                         最大委派深度 = 5                                      │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
          ┌──────────────────────────┼──────────────────────────┐
          │                          │                          │
          ▼                          ▼                          ▼
   ┌─────────────┐           ┌─────────────┐           ┌─────────────┐
   │ Agent (主)  │◄─────────►│ Agent (子1) │◄─────────►│ Agent (子2) │
   └──────┬──────┘           └──────┬──────┘           └──────┬──────┘
          │                          │                          │
          └──────────────────────────┼──────────────────────────┘
                                     │
┌────────────────────────────────────▼────────────────────────────────────────┐
│                         ReasoningEngine                                    │
│                     （ReAct: Reason → Act → Observe）                        │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
        ┌────────────────────────────┼────────────────────────────┐
        │                            │                            │
        ▼                            ▼                            ▼
 ┌─────────────┐             ┌─────────────┐             ┌─────────────┐
 │    Brain    │             │ToolExecutor │             │   Memory    │
 │   (LLM)     │             │   (工具)    │             │  Manager    │
 └──────┬──────┘             └──────┬──────┘             └──────┬──────┘
        │                            │                            │
        ▼                            ▼                            ▼
 ┌─────────────┐             ┌─────────────┐             ┌─────────────┐
 │ LLMClient  │             │ ToolHandler │             │UnifiedStore │
 │ + Providers│             │   目录      │             │ + FTS5/     │
 │(Anthropic/ │             │ browser/    │             │ ChromaDB    │
 │ OpenAI兼容)│             │ filesystem/ │             └─────────────┘
 └─────────────┘             │ skills/     │
                            │ plan/       │
                            │ ...         │
                            └─────────────┘
```

---

## 十、数据流

1. **用户输入** → MessageGateway（IM 渠道适配器）
2. **路由** → AgentOrchestrator → 根据会话配置选择合适的 Agent
3. **执行** → ReasoningEngine（ReAct 循环）
4. **LLM 调用** → Brain → LLMClient → Provider（Anthropic/OpenAI）
5. **工具调用** → ToolExecutor → ToolHandler → 外部服务
6. **记忆** → MemoryManager → UnifiedStore（SQLite + SearchBackend）
7. **进度持久化** → RalphLoop 写入状态文件

---

## 十一、关键接口说明

### 11.1 Agent 接口

```python
class Agent:
    async def run(self, task: str, session_id: str) -> str: ...
    async def delegate(self, task: str, agent_type: str) -> str: ...
    async def get_state(self) -> AgentState: ...
```

### 11.2 Brain 接口

```python
class Brain:
    async def think(self, messages: list[Message]) -> LLMResponse: ...
    async def stream(self, messages: list[Message]) -> AsyncIterator[ContentBlock]: ...
```

### 11.3 Memory 接口

```python
class MemoryManager:
    async def store(self, memory: Memory) -> None: ...
    async def retrieve(self, query: str, limit: int = 10) -> list[Memory]: ...
    async def consolidate(self) -> None: ...
```

### 11.4 ToolExecutor 接口

```python
class ToolExecutor:
    async def execute(self, tool_call: ToolCall, timeout: int = 60) -> ToolResult: ...
```

### 11.5 LLMClient 接口

```python
class LLMClient:
    async def complete(self, request: LLMRequest) -> LLMResponse: ...
    async def stream(self, request: LLMRequest) -> AsyncIterator[ContentBlock]: ...
```

---

## 十二、使用场景

### 12.1 CLI 模式

```bash
seeagent                           # 交互模式
seeagent run "你的任务"             # 单次任务
```

### 12.2 API 服务模式

```bash
seeagent serve                     # 启动 FastAPI 服务（端口 18900）
seeagent serve --dev               # 开发模式（热重载）
```

### 12.3 守护进程模式

```bash
seeagent daemon start              # 启动后台守护进程
seeagent status                    # 查看状态
```

### 12.4 桌面应用模式

```bash
cd apps/setup-center && npx tauri dev
```

### 12.5 集成 IM 渠道

配置相应的 Bot Token/Secret 后，服务启动即可接收消息。

---

## 十三、依赖关系图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              API 层                                          │
│   routes/ ──► orchestrator ──► agent ──► brain ──► llm/client              │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              工具层                                          │
│   tool_executor ──► tools/handlers/* (browser, filesystem, shell...)       │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              记忆层                                          │
│   memory/manager ──► unified_store ──► storage + vector_store              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 十四、pyproject.toml 主要依赖

| 类别 | 依赖 |
|------|------|
| LLM | `anthropic`, `openai` |
| MCP | `mcp>=1.0.0` |
| Web | `ddgs`, `playwright`, `browser-use` |
| CLI/UI | `rich`, `prompt-toolkit`, `typer` |
| 异步 | `httpx`, `aiofiles`, `nest-asyncio` |
| 数据库 | `aiosqlite` |
| API | `fastapi`, `uvicorn` |
| IM 渠道 | `python-telegram-bot`, `lark-oapi`, `dingtalk-stream`, `wework`, `qq-botpy`, `onebot` |

---

## 十五、CLI 入口（main.py）

**位置**: `src/seeagent/main.py` (~72KB)

基于 Typer 的命令行应用：

```bash
seeagent                    # 交互式 CLI 聊天
seeagent run "task"        # 单次任务执行
seeagent serve             # API 服务器模式（端口 18900）
seeagent serve --dev       # 开发模式（热重载）
seeagent daemon start      # 后台守护进程
seeagent status            # 状态检查
```

---

*文档版本: 1.3*
*最后更新: 2026-04-17*

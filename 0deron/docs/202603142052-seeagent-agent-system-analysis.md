# 202603142052 — SeeAgent Agent 实例全景分析

> 深度代码阅读报告：Agent 类型、职责、多 Agent 配置与协作机制

---

## 目录

1. [总体架构](#1-总体架构)
2. [Agent 数据模型](#2-agent-数据模型)
3. [22 个系统预设 Agent](#3-22-个系统预设-agent)
4. [Agent 实例的 6 种存在形态](#4-agent-实例的-6-种存在形态)
5. [核心模块关系](#5-核心模块关系)
6. [多 Agent 配置与启用](#6-多-agent-配置与启用)
7. [编排与调度流程](#7-编排与调度流程)
8. [4 种委派工具](#8-4-种委派工具)
9. [防递归与深度控制](#9-防递归与深度控制)
10. [故障降级机制](#10-故障降级机制)
11. [核心执行引擎](#11-核心执行引擎)
12. [运行时监督](#12-运行时监督)
13. [关键代码摘录](#13-关键代码摘录)

---

## 1. 总体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         用户消息入口                              │
│                  CLI / Telegram / Feishu / API                   │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
              ┌─────────────────────────┐
              │   settings.multi_agent   │
              │      _enabled ?          │
              └─────┬───────────┬───────┘
                    │           │
               False│           │True
                    ▼           ▼
         ┌──────────────┐  ┌───────────────────┐
         │ 单 Agent 模式 │  │ AgentOrchestrator │
         │ agent.chat() │  │  .handle_message() │
         └──────────────┘  └─────────┬─────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
             ┌───────────┐   ┌───────────┐   ┌───────────┐
             │ProfileStore│   │ Instance  │   │ Fallback  │
             │  (profiles)│   │   Pool    │   │ Resolver  │
             └─────┬─────┘   └─────┬─────┘   └─────┬─────┘
                   │               │               │
                   └───────┬───────┘               │
                           ▼                       │
                   ┌───────────────┐               │
                   │ AgentFactory  │               │
                   │  .create()    │               │
                   └───────┬───────┘               │
                           ▼                       │
                   ┌───────────────┐               │
                   │    Agent      │◄──────────────┘
                   │  (运行实例)    │   (故障时降级)
                   └───────┬───────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │  Brain   │ │ Reasoning│ │   Tool   │
        │ (LLM)   │ │  Engine  │ │ Executor │
        └──────────┘ └──────────┘ └──────────┘
```

**核心原则：所有 Agent 实例都是同一个 `Agent` 类，差异化完全通过 `AgentProfile` 实现。**

---

## 2. Agent 数据模型

### 2.1 AgentType 枚举

```
文件: src/seeagent/agents/profile.py
```

```python
class AgentType(str, Enum):
    SYSTEM  = "system"   # 系统预设，不可删除，id/type/created_by 不可变
    CUSTOM  = "custom"   # 用户创建，完全可编辑/删除
    DYNAMIC = "dynamic"  # AI 运行时创建，持久化的 7 天后自动清理，临时的立即清理
```

### 2.2 SkillsMode 枚举

```python
class SkillsMode(str, Enum):
    INCLUSIVE  = "inclusive"   # 只启用列表中的技能
    EXCLUSIVE  = "exclusive"  # 启用列表之外的所有技能
    ALL        = "all"        # 启用全部技能
```

### 2.3 AgentProfile 核心字段

```
┌─────────────────────────────────────────────────────┐
│                   AgentProfile                       │
├──────────────────┬──────────────────────────────────┤
│ id               │ 唯一标识 (如 "code-assistant")     │
│ name / name_i18n │ 显示名称 (多语言)                  │
│ type             │ AgentType (SYSTEM/CUSTOM/DYNAMIC) │
│ skills           │ 技能 ID 列表                      │
│ skills_mode      │ 技能过滤模式                       │
│ custom_prompt    │ 追加到系统 prompt 的自定义指令       │
│ fallback_profile │ 故障降级目标 Agent                  │
│ preferred_endpoint│ LLM 端点覆盖                      │
│ ephemeral        │ 是否仅内存 (不持久化)               │
│ inherit_from     │ spawn 时的父 Profile               │
│ category         │ 分类标签                           │
│ icon / color     │ UI 展示                           │
└──────────────────┴──────────────────────────────────┘
```

### 2.4 六大 Agent 分类

```
┌─────────────┬──────────────────┬─────────┐
│  Category   │     Label        │  Color  │
├─────────────┼──────────────────┼─────────┤
│ general     │ 通用             │ #4A90D9 │
│ content     │ 内容创作         │ #FF6B6B │
│ enterprise  │ 企业/办公        │ #27AE60 │
│ education   │ 教育             │ #8E44AD │
│ productivity│ 效率工具         │ #E74C3C │
│ devops      │ 开发/运维        │ #95A5A6 │
└─────────────┴──────────────────┴─────────┘
```

---

## 3. 22 个系统预设 Agent

```
文件: src/seeagent/agents/presets.py
```

所有预设 `fallback_profile_id = "default"`（default 自身为 None），形成星型降级拓扑：

```
                    ┌──────────┐
          ┌────────►│  default │◄────────┐
          │         │  (小秋)  │         │
          │         └────┬─────┘         │
          │              │               │
    ┌─────┴─────┐  ┌────┴────┐  ┌───────┴──────┐
    │ content   │  │enterprise│  │  devops      │
    │ (4 agents)│  │(6 agents)│  │ (5 agents)   │
    └───────────┘  └─────────┘  └──────────────┘
          │              │               │
    ┌─────┴─────┐  ┌────┴────┐
    │ education │  │productiv│
    │ (3 agents)│  │(3 agents)│
    └───────────┘  └─────────┘
```

### 完整预设列表

| # | ID | 中文名 | 英文名 | 类别 | 核心技能 |
|---|---|--------|--------|------|----------|
| 1 | `default` | 小秋 | Akita | general | ALL (全部技能) |
| **内容创作** |||||
| 2 | `content-creator` | 自媒体达人 | Content Creator | content | 小红书/微信/抖音/图片生成 |
| 3 | `video-planner` | 视频策划 | Video Planner | content | 抖音/B站/YouTube/幻灯片 |
| 4 | `seo-writer` | SEO 写手 | SEO Writer | content | 内容研究/Apify 爬虫 |
| 5 | `novelist` | 小说作家 | Novelist | content | 中文小说/漫画/图片生成 |
| **企业办公** |||||
| 6 | `office-doc` | 文助 | DocHelper | enterprise | docx/pptx/xlsx/pdf |
| 7 | `hr-assistant` | 人事助理 | HR Assistant | enterprise | docx/xlsx/pdf/中文写作 |
| 8 | `legal-advisor` | 法务顾问 | Legal Advisor | enterprise | docx/pdf/PDF翻译 |
| 9 | `marketing-planner` | 营销策划 | Marketing Planner | enterprise | 内容研究/小红书/信息图 |
| 10 | `customer-support` | 客服专员 | Customer Support | enterprise | 知识捕获/摘要 |
| 11 | `project-manager` | 项目经理 | Project Manager | enterprise | xlsx/todoist/mermaid |
| **教育** |||||
| 12 | `language-tutor` | 语言教练 | Language Tutor | education | PDF翻译/中文写作/摘要 |
| 13 | `academic-assistant` | 学术助手 | Academic Assistant | education | 内容研究/pdf/docx |
| 14 | `math-tutor` | 数学辅导 | Math Tutor | education | mermaid/xlsx/画布设计 |
| **效率工具** |||||
| 15 | `schedule-manager` | 日程管家 | Schedule Manager | productivity | todoist/日历/邮件 |
| 16 | `knowledge-manager` | 知识管理 | Knowledge Manager | productivity | obsidian/notebooklm |
| 17 | `yuque-assistant` | 语雀助手 | Yuque Assistant | productivity | 语雀/中文写作 |
| **开发运维** |||||
| 18 | `code-assistant` | 码哥 | CodeBro | devops | TDD/调试/Git/代码审查 |
| 19 | `browser-agent` | 网探 | WebScout | devops | 浏览器/桌面截图/爬虫 |
| 20 | `data-analyst` | 数析 | DataPro | devops | xlsx/pdf/mermaid/爬虫 |
| 21 | `devops-engineer` | DevOps 工程师 | DevOps Engineer | devops | GitHub自动化/系统调试 |
| 22 | `architect` | 架构师 | Architect | devops | mermaid/脑暴/信息图 |

---

## 4. Agent 实例的 6 种存在形态

```
┌───────────────────┬───────────┬─────────────────────────┬───────────────────┬────────────┐
│     实例类型       │ AgentType │      创建方式            │     生命周期       │ 能否委派?  │
├───────────────────┼───────────┼─────────────────────────┼───────────────────┼────────────┤
│ 主 Agent          │ N/A       │ main.py 直接 Agent()    │ 应用生命周期       │ Yes(depth=0)│
│ 系统预设 Agent    │ SYSTEM    │ Factory.create(preset)  │ Pool管理,30min闲置│ No(子Agent) │
│ 用户自定义 Agent  │ CUSTOM    │ Factory.create(custom)  │ Pool管理,30min闲置│ No(子Agent) │
│ 动态持久 Agent    │ DYNAMIC   │ create_agent(persist=T) │ 磁盘持久,7天清理  │ No(子Agent) │
│ 临时 Agent        │ DYNAMIC   │ spawn_agent / create    │ 仅内存,任务后清理  │ No(子Agent) │
│ 并行克隆 Agent    │ DYNAMIC   │ delegate_parallel       │ 临时,gather后清理  │ No(子Agent) │
└───────────────────┴───────────┴─────────────────────────┴───────────────────┴────────────┘
```

**关键规则：只有顶层 Agent (depth=0) 可以使用委派工具，所有被委派的子 Agent 自动剥离委派能力，防止无限递归。**

---

## 5. 核心模块关系

```
文件分布:

src/seeagent/
├── agents/                          # Agent 管理层
│   ├── profile.py                   # AgentProfile + AgentType 数据模型
│   ├── presets.py                   # 22 个系统预设定义
│   ├── factory.py                   # AgentFactory (创建) + AgentInstancePool (缓存)
│   ├── orchestrator.py              # AgentOrchestrator (编排调度核心)
│   ├── fallback.py                  # FallbackResolver (故障降级)
│   ├── manifest.py                  # AgentManifest (.akita-agent 包规范)
│   ├── packager.py                  # AgentPackager + AgentInstaller (打包/安装)
│   └── task_queue.py                # TaskQueue (异步优先级队列)
│
├── core/                            # Agent 执行层
│   ├── agent.py                     # Agent 类 (唯一的运行时实体)
│   ├── brain.py                     # Brain (LLM 接口, 双客户端)
│   ├── reasoning_engine.py          # ReasoningEngine (ReAct 循环)
│   ├── ralph.py                     # RalphLoop ("永不放弃" 重试)
│   ├── supervisor.py                # RuntimeSupervisor (运行时监督)
│   ├── tool_executor.py             # ToolExecutor (工具执行引擎)
│   ├── proactive.py                 # ProactiveEngine (主动行为引擎)
│   └── context_manager.py           # ContextManager (上下文窗口管理)
│
└── tools/
    ├── definitions/agent.py         # 4 种委派工具定义
    └── handlers/agent.py            # AgentToolHandler (委派工具实现)
```

### 模块依赖图

```
                    ┌──────────────────┐
                    │ AgentOrchestrator│
                    └────────┬─────────┘
                             │ uses
            ┌────────────────┼────────────────┐
            ▼                ▼                ▼
    ┌──────────────┐  ┌────────────┐  ┌──────────────┐
    │ ProfileStore │  │ InstancePool│  │FallbackResolver│
    └──────────────┘  └──────┬─────┘  └──────────────┘
                             │ creates via
                             ▼
                     ┌──────────────┐
                     │ AgentFactory │
                     └──────┬───────┘
                            │ instantiates
                            ▼
                     ┌──────────────┐
                     │    Agent     │
                     └──────┬───────┘
                            │ contains
         ┌──────────┬───────┼───────┬──────────┐
         ▼          ▼       ▼       ▼          ▼
    ┌────────┐ ┌────────┐ ┌─────┐ ┌────────┐ ┌──────────┐
    │  Brain │ │Reasoning│ │Ralph│ │  Tool  │ │Supervisor│
    │        │ │ Engine  │ │Loop │ │Executor│ │          │
    └────────┘ └────────┘ └─────┘ └────────┘ └──────────┘
```

---

## 6. 多 Agent 配置与启用

### 6.1 主开关

```
文件: src/seeagent/config.py (line 342)
```

```python
multi_agent_enabled: bool = Field(
    default=False,
    description="多Agent模式 (Beta)，开启后支持多Agent协作、专用Agent、IM多Bot等",
)
```

### 6.2 启用方式

| 方式 | 入口 | 说明 |
|------|------|------|
| 配置 API | `/api/config/multi-agent` | 运行时切换，立即生效 |
| IM 命令 | `/模式` | 通过 IM 通道切换 |
| 环境变量 | `.env` 中的 `MULTI_AGENT_ENABLED` | 启动时读取 |

### 6.3 运行时决策

```
文件: src/seeagent/main.py (line 898)
```

```python
async def agent_handler(session, message: str) -> str:
    if settings.multi_agent_enabled and _orchestrator is not None:
        return await _orchestrator.handle_message(session, message)
    # 单 Agent 路径
    response = await agent.chat_with_session(...)
    return response
```

**这是运行时检查——切换 `multi_agent_enabled` 立即生效，无需重启。**

### 6.4 模式启用时自动部署预设

```python
# agents/presets.py
async def ensure_presets_on_mode_enable():
    """多 Agent 模式首次启用时，部署系统预设并清理过期动态 Agent"""
```

---

## 7. 编排与调度流程

### 7.1 完整消息处理流程

```
用户消息
  │
  ▼
AgentOrchestrator.handle_message(session, message)
  │
  ├─ 读取 session.context.agent_profile_id
  │
  ▼
_dispatch(session, message, profile_id, depth=0)
  │
  ├─ ProfileStore.get(profile_id) → AgentProfile
  │
  ├─ FallbackResolver.get_effective_profile()
  │  └─ 如果已降级 → 返回 fallback_profile_id
  │
  ▼
_run_with_progress_timeout(agent_fn, idle_timeout=1200s)
  │
  ├─ AgentInstancePool.get_or_create(session_id, profile)
  │  │
  │  ├─ 缓存命中 → 返回现有 Agent (刷新时间戳)
  │  │
  │  └─ 缓存未命中 → AgentFactory.create(profile)
  │     │
  │     ├─ Agent(name=profile.get_display_name())
  │     ├─ agent.initialize(lightweight=True)
  │     ├─ _apply_skill_filter(agent, profile)
  │     └─ 注入 custom_prompt + preferred_endpoint
  │
  ▼
_call_agent(agent, session, message)
  │
  ├─ 设置 agent._is_sub_agent_call (depth > 0 时为 True)
  │
  ▼
agent.chat_with_session()
  │
  ▼
ReasoningEngine.run() ─── ReAct 循环 ───┐
  │                                      │
  ├─ REASON: Brain.messages_create()     │
  │                                      │
  ├─ 决策分支:                            │
  │  ├─ FINAL_ANSWER → 返回文本          │
  │  └─ TOOL_CALLS → ACT 阶段           │
  │     │                                │
  │     ├─ ToolExecutor.execute_batch()  │
  │     │  └─ 可能触发委派工具 ──────────┤
  │     │                                │
  │     └─ OBSERVE: 分析结果             │
  │        └─ Supervisor.evaluate() ─────┘
  │
  ▼
进度监控 (每3秒指纹检测)
  ├─ 闲置 > 20min → 终止 + 尝试降级
  └─ 连续3次失败 → 自动降级到 fallback
  │
  ▼
结果返回用户
```

### 7.2 委派子流程

```
顶层 Agent (depth=0) 调用委派工具
  │
  ▼
AgentToolHandler.handle("delegate_to_agent", params)
  │
  ▼
orchestrator.delegate(from_agent, to_agent, message, depth)
  │
  ├─ depth + 1 (最大 MAX_DELEGATION_DEPTH=5)
  │
  ▼
_dispatch(session, message, to_agent, depth=1)
  │
  ▼
_call_agent(agent, is_sub_agent=True)
  │
  ├─ agent._is_sub_agent_call = True
  │  └─ 委派工具被剥离，不可再委派
  │
  ▼
子 Agent 执行任务并返回结果
  │
  ▼
结果返回给顶层 Agent
```

### 7.3 并行委派流程

```
delegate_parallel(tasks=[
    {agent_id: "code-assistant", task: "写代码"},
    {agent_id: "browser-agent",  task: "搜索文档"},
    {agent_id: "code-assistant", task: "写测试"},  ← 同一Agent重复
])
  │
  ├─ 检测到 "code-assistant" 出现2次
  │  └─ 自动 spawn 临时克隆: "code-assistant-clone-1"
  │
  ▼
asyncio.gather(
    delegate("code-assistant", "写代码"),
    delegate("browser-agent",  "搜索文档"),
    delegate("code-assistant-clone-1", "写测试"),
)
  │
  ▼
所有结果汇总返回
  │
  └─ 临时克隆自动清理
```

---

## 8. 4 种委派工具

```
文件: src/seeagent/tools/definitions/agent.py
文件: src/seeagent/tools/handlers/agent.py
```

**优先级从高到低：**

```
┌───┬─────────────────────┬────────────────────────────────────────┐
│ # │       工具名         │              用途                      │
├───┼─────────────────────┼────────────────────────────────────────┤
│ 1 │ delegate_to_agent   │ 首选。委派任务给已存在的 Agent Profile  │
│ 2 │ spawn_agent         │ 次选。继承现有 Profile + 叠加技能/提示  │
│ 3 │ delegate_parallel   │ 并行。2-5个任务并行执行，自动克隆重复ID │
│ 4 │ create_agent        │ 末选。从零创建全新 Agent，默认临时      │
└───┴─────────────────────┴────────────────────────────────────────┘
```

### 工具注册条件

```python
# core/agent.py (line 369)
if settings.multi_agent_enabled:
    _all_tools.extend(AGENT_TOOLS)

# core/agent.py (line 1078)
if settings.multi_agent_enabled:
    self.handler_registry.register("agent", AgentToolHandler(...))
```

### 动态 Agent 策略

```python
# tools/handlers/agent.py (line 21)
DYNAMIC_AGENT_POLICIES = {
    "max_agents_per_session": 5,    # 每个会话最多5个Agent
    "max_delegation_depth": 5,       # 最大委派深度5层
    "agent_lifetime_minutes": 60,    # Agent最长存活60分钟
}
```

---

## 9. 防递归与深度控制

```
MAX_DELEGATION_DEPTH = 5

depth=0: 顶层 Agent ──── 拥有全部委派工具
  │
  └─ delegate_to_agent
     │
     depth=1: 子 Agent ──── 委派工具被剥离
       │
       └─ (无法再委派)
```

### 实现机制

**1. 工具剥离** (`core/agent.py`):
```python
@property
def _effective_tools(self) -> list[dict]:
    if self._is_sub_agent_call:
        return [t for t in self._tools if t.get("name") not in self._agent_tool_names]
    return self._tools
```

**2. Prompt 注入** (告诉 LLM 不可委派):
```python
# orchestrator.py (line 2281-2289)
def _build_multi_agent_prompt_section():
    if is_sub_agent:
        return "你是一个被委派的子Agent，不可以再委派任务给其他Agent。"
```

**3. Handler 拦截** (`tools/handlers/agent.py`):
```python
class AgentToolHandler:
    async def handle(self, tool_name, params):
        if self.agent._is_sub_agent_call:
            return "子Agent不允许使用委派工具"
```

---

## 10. 故障降级机制

```
文件: src/seeagent/agents/fallback.py
```

```
                    正常运行
                       │
                       ▼
              ┌─────────────────┐
              │ 连续失败计数 < 3 │──── 成功 → 重置计数
              └────────┬────────┘
                       │ 5分钟内连续3次失败
                       ▼
              ┌─────────────────┐
              │   自动降级       │
              │ → fallback Agent│
              └────────┬────────┘
                       │ 单次成功
                       ▼
              ┌─────────────────┐
              │   恢复正常       │
              └─────────────────┘
```

### 核心代码

```python
class FallbackResolver:
    def record_failure(self, profile_id: str):
        entry = self._health[profile_id]
        # 5分钟窗口内的连续失败
        if now - entry.last_failure_time > 300:
            entry.consecutive_failures = 1
        else:
            entry.consecutive_failures += 1
        if entry.consecutive_failures >= 3:  # _AUTO_DEGRADE_THRESHOLD
            entry.degraded = True

    def get_effective_profile(self, profile_id: str) -> str:
        if self._health[profile_id].degraded:
            return profile.fallback_profile_id  # 通常是 "default"
        return profile_id
```

---

## 11. 核心执行引擎

### 11.1 ReasoningEngine — ReAct 循环

```
文件: src/seeagent/core/reasoning_engine.py
```

```
                    ┌──────────────┐
                    │   开始任务    │
                    └──────┬───────┘
                           │
                ┌──────────▼──────────┐
            ┌──►│    REASON (推理)     │
            │   │ Brain.messages_create│
            │   └──────────┬──────────┘
            │              │
            │     ┌────────▼────────┐
            │     │  Decision Type? │
            │     └───┬─────────┬──┘
            │         │         │
            │   FINAL_ANSWER  TOOL_CALLS
            │         │         │
            │         ▼         ▼
            │    ┌────────┐ ┌────────────────┐
            │    │ 返回   │ │  ACT (执行)     │
            │    │ 文本   │ │ ToolExecutor    │
            │    └────────┘ │ .execute_batch()│
            │               └────────┬───────┘
            │                        │
            │               ┌────────▼───────┐
            │               │ OBSERVE (观察)  │
            │               │ 结果分析 +      │
            │               │ Supervisor检查  │
            │               └────────┬───────┘
            │                        │
            │               ┌────────▼───────┐
            │               │ 需要回滚?       │
            │               └───┬─────────┬──┘
            │              No   │         │ Yes
            │                   │         ▼
            │                   │  ┌──────────────┐
            │                   │  │  _rollback() │
            │                   │  │ 恢复checkpoint│
            │                   │  │ 注入失败经验  │
            │                   │  └──────┬───────┘
            │                   │         │
            └───────────────────┴─────────┘
```

#### Checkpoint 回滚机制

```python
class Checkpoint:
    messages_snapshot: list[dict]   # 深拷贝的消息历史
    state_snapshot: dict            # 序列化的任务状态
    decision_summary: str           # 当时的决策摘要
    tool_names: list[str]           # 调用了哪些工具

# 回滚条件：
# 1. 批次中所有工具都失败
# 2. 单个工具连续失败 >= 3 次 (CONSECUTIVE_FAIL_THRESHOLD)

# 回滚操作：
def _rollback(self, reason: str):
    cp = self._checkpoints.pop()
    self._messages = cp.messages_snapshot
    # 注入失败经验提示
    hint = f"之前的方案失败了（原因: {reason}）。请尝试完全不同的方法。"
```

### 11.2 RalphLoop — "永不放弃" 重试

```
文件: src/seeagent/core/ralph.py
```

```
┌─────────────────────────────────────────────┐
│              RalphLoop.run()                 │
│                                             │
│  while iteration < max_iterations (100):    │
│    │                                        │
│    ├─ 1. 从 MEMORY.md 加载进度              │
│    ├─ 2. 调用 execute_fn(task)              │
│    ├─ 3. 成功? → 保存进度，返回             │
│    ├─ 4. 失败? → mark_failed               │
│    │      │                                 │
│    │      ├─ attempts < max_attempts (10)?  │
│    │      │   ├─ Yes → StopHook.intercept() │
│    │      │   │        → analyze_and_adapt()│
│    │      │   │        → 继续下一轮          │
│    │      │   └─ No  → 最终失败，退出       │
│    │                                        │
│    └─ 5. 每轮保存进度到 MEMORY.md           │
└─────────────────────────────────────────────┘
```

```python
class TaskStatus(Enum):
    PENDING     = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED   = "completed"
    FAILED      = "failed"
    BLOCKED     = "blocked"

class Task:
    max_attempts: int = 10
    def mark_failed(self, error: str):
        if self.attempts >= self.max_attempts:
            self.status = TaskStatus.FAILED    # 最终失败
        else:
            self.status = TaskStatus.PENDING   # 可重试
```

---

## 12. 运行时监督

```
文件: src/seeagent/core/supervisor.py
```

### 12.1 干预级别

```
NONE(0) → NUDGE(1) → STRATEGY_SWITCH(2) → MODEL_SWITCH(3) → ESCALATE(4) → TERMINATE(5)
```

### 12.2 检测的 7 种问题模式

```
┌────────────────────┬──────────────────────────────┬─────────────────┐
│    模式             │        触发条件               │    干预动作      │
├────────────────────┼──────────────────────────────┼─────────────────┤
│ SIGNATURE_REPEAT   │ 相同工具签名重复3次/5次       │ 警告/终止        │
│ TOOL_THRASHING     │ 同工具6次调用中失败3次        │ 策略切换         │
│ EDIT_THRASHING     │ 同文件10次操作中读写交替3次   │ 提示             │
│ REASONING_LOOP     │ LLM返回相同内容(MD5)3次      │ 策略切换         │
│ TOKEN_ANOMALY      │ 单次迭代 > 40,000 tokens     │ 仅记录           │
│ EXTREME_ITERATIONS │ 连续50次迭代                  │ 上报用户         │
│ SELF_CHECK         │ 每10轮自动触发                │ 注入自评估prompt  │
└────────────────────┴──────────────────────────────┴─────────────────┘
```

### 12.3 干预响应

```python
class Intervention:
    should_inject_prompt: bool    # 注入系统提示
    should_rollback: bool         # 触发 checkpoint 回滚
    should_terminate: bool        # 安全终止
    should_escalate: bool         # 请求用户帮助
    should_switch_model: bool     # 切换到不同模型
```

---

## 13. 关键代码摘录

### 13.1 Agent 构造函数 (核心属性)

```python
# src/seeagent/core/agent.py
class Agent:
    def __init__(self, name=None, api_key=None):
        self.brain = Brain(api_key=api_key)              # LLM 接口
        self.ralph = RalphLoop(...)                       # 永不放弃循环
        self.tool_catalog = ToolCatalog(...)              # 渐进式工具发现
        self.tool_executor = ToolExecutor(...)            # 工具执行引擎
        self.reasoning_engine = ReasoningEngine(...)      # ReAct 推理
        self.memory_manager = MemoryManager(...)          # 三层记忆
        self.skill_registry = SkillRegistry()             # 技能注册表
        self.proactive_engine = ProactiveEngine(...)      # 主动行为
        self.context_manager = ContextManager(...)        # 上下文管理
        self.agent_state = AgentState()                   # 状态追踪

        # 由 AgentFactory 设置:
        self._agent_profile = None                        # 当前 Profile
        self._custom_prompt_suffix = ""                   # Profile 自定义指令
        self._preferred_endpoint = None                   # LLM 端点覆盖
        self._is_sub_agent_call = False                   # 是否为子 Agent
```

### 13.2 AgentFactory 创建流程

```python
# src/seeagent/agents/factory.py
class AgentFactory:
    async def create(self, profile: AgentProfile) -> Agent:
        agent = Agent(name=profile.get_display_name())
        await agent.initialize(start_scheduler=False, lightweight=True)

        # 技能过滤
        if profile.skills_mode == SkillsMode.INCLUSIVE:
            # 只保留列表中的技能 + ESSENTIAL_SYSTEM_SKILLS
            agent.skills = [s for s in agent.skills
                          if s.id in profile.skills or s.id in ESSENTIAL_SYSTEM_SKILLS]
        elif profile.skills_mode == SkillsMode.EXCLUSIVE:
            # 移除列表中的技能
            agent.skills = [s for s in agent.skills if s.id not in profile.skills]

        agent._agent_profile = profile
        agent._custom_prompt_suffix = profile.custom_prompt
        agent._preferred_endpoint = profile.preferred_endpoint
        return agent

# 不可剥离的核心技能
ESSENTIAL_SYSTEM_SKILLS = frozenset({
    "create-plan", "update-plan-step", "get-plan-status", "complete-plan",
    "get-skill-info", "list-skills",
    "run-shell", "read-file", "write-file", "list-directory",
    "deliver-artifacts", "get-chat-history",
    "search-memory", "add-memory", "web-search",
    "get-tool-info", "set-task-timeout",
})
```

### 13.3 AgentInstancePool 缓存

```python
# src/seeagent/agents/factory.py
class AgentInstancePool:
    # 缓存键: "{session_id}::{profile_id}"
    # 同一会话可以同时运行多个不同 Profile 的 Agent

    async def get_or_create(self, session_id, profile) -> Agent:
        key = f"{session_id}::{profile.id}"
        if key in self._pool:
            self._pool[key].touch()  # 刷新时间戳
            return self._pool[key].agent
        agent = await self._factory.create(profile)
        self._pool[key] = PoolEntry(agent=agent, ...)
        return agent

    # 30分钟闲置清理
    async def _reap_idle(self):
        for key, entry in list(self._pool.items()):
            if now - entry.last_access > 1800:
                del self._pool[key]
```

### 13.4 Orchestrator 核心调度

```python
# src/seeagent/agents/orchestrator.py

MAX_DELEGATION_DEPTH = 5

class AgentOrchestrator:
    async def handle_message(self, session, message):
        profile_id = session.context.agent_profile_id or "default"
        return await self._dispatch(session, message, profile_id, depth=0)

    async def _dispatch(self, session, message, profile_id, depth=0, from_agent=None):
        if depth > MAX_DELEGATION_DEPTH:
            return "委派深度超限"

        # 故障降级检查
        effective_id = self._fallback.get_effective_profile(profile_id)
        profile = self._profile_store.get(effective_id)
        agent = await self._pool.get_or_create(session.id, profile)

        try:
            result = await self._run_with_progress_timeout(
                lambda: self._call_agent(agent, session, message,
                                         is_sub_agent=(depth > 0)),
                idle_timeout=1200
            )
            self._fallback.record_success(profile_id)
            return result
        except Exception as e:
            self._fallback.record_failure(profile_id)
            raise

    async def delegate(self, session, from_agent, to_agent, message, depth, reason):
        """被委派工具调用的入口"""
        return await self._dispatch(session, message, to_agent, depth + 1, from_agent)
```

### 13.5 进度感知超时

```python
# src/seeagent/agents/orchestrator.py
async def _run_with_progress_timeout(self, agent_fn, idle_timeout=1200):
    """
    每3秒检测 Agent 进度指纹 (iteration, status, tools_count)
    - 指纹变化 → Agent 仍在工作，重置空闲计时器
    - 指纹不变超过 idle_timeout → Agent 卡死，终止
    """
    last_fingerprint = None
    idle_since = time.time()

    while True:
        await asyncio.sleep(3)
        fp = self._get_progress_fingerprint(agent)
        if fp != last_fingerprint:
            last_fingerprint = fp
            idle_since = time.time()
        elif time.time() - idle_since > idle_timeout:
            agent.cancel()
            raise TimeoutError("Agent 空闲超时")
```

### 13.6 Brain 双客户端架构

```python
# src/seeagent/core/brain.py
class Brain:
    def __init__(self):
        self._llm_client = LLMClient(...)      # 主模型 (推理)
        self._compiler_client = LLMClient(...)  # 编译器模型 (轻量任务)

        # 编译器断路器: 连续5次失败后熔断，5分钟后恢复
        _COMPILER_FAIL_THRESHOLD = 5
        _COMPILER_CIRCUIT_RESET_S = 300

    def messages_create(self, messages, system, tools):
        """主推理调用"""
        return self._llm_client.chat(...)

    def compiler_think(self, prompt):
        """Prompt 编译器: 优先用编译器模型，故障回退主模型"""
        if not self._compiler_circuit_open:
            try:
                return self._compiler_client.chat(prompt)
            except:
                self._compiler_fail_count += 1

    def think_lightweight(self, prompt):
        """轻量思考: 用于记忆提取、分类等，完全独立于主推理链"""
```

---

## 附: 全景视图

```
┌─────────────────────────── SeeAgent Agent System ──────────────────────────────┐
│                                                                                  │
│  ┌─────────── 管理层 ───────────┐    ┌─────────── 执行层 ───────────────────┐    │
│  │                              │    │                                      │    │
│  │  ProfileStore                │    │  Agent (唯一运行时实体)               │    │
│  │  ├─ 22 SYSTEM presets        │    │  ├─ Brain (双LLM客户端)              │    │
│  │  ├─ N CUSTOM profiles        │    │  ├─ ReasoningEngine (ReAct循环)     │    │
│  │  └─ M DYNAMIC profiles      │    │  │  ├─ Checkpoint 回滚              │    │
│  │                              │    │  │  └─ Supervisor 监督              │    │
│  │  AgentOrchestrator           │    │  ├─ RalphLoop (永不放弃)            │    │
│  │  ├─ 消息路由                 │    │  ├─ ToolExecutor (工具执行)         │    │
│  │  ├─ 委派调度 (depth 0-5)     │    │  │  ├─ 89+ 内置工具               │    │
│  │  └─ 进度感知超时             │    │  │  └─ 4 委派工具 (multi-agent)    │    │
│  │                              │    │  ├─ SkillManager (技能管理)        │    │
│  │  AgentInstancePool           │    │  ├─ MemoryManager (三层记忆)       │    │
│  │  └─ key: session::profile    │    │  ├─ ProactiveEngine (主动行为)     │    │
│  │                              │    │  └─ ContextManager (上下文管理)    │    │
│  │  FallbackResolver            │    │                                      │    │
│  │  └─ 3次失败自动降级到default │    │                                      │    │
│  │                              │    │                                      │    │
│  └──────────────────────────────┘    └──────────────────────────────────────┘    │
│                                                                                  │
│  ┌─────────── 分发层 ───────────┐    ┌─────────── 扩展层 ─────────────────┐     │
│  │                              │    │                                      │    │
│  │  AgentPackager               │    │  AgentManifest (.akita-agent 包)    │    │
│  │  └─ Profile + Skills → ZIP  │    │  └─ 技能打包/安装/安全检查          │    │
│  │                              │    │                                      │    │
│  │  TaskQueue                   │    │  Skills Marketplace                  │    │
│  │  └─ 5级优先级异步队列        │    │  └─ 70+ 可安装技能                 │    │
│  │                              │    │                                      │    │
│  └──────────────────────────────┘    └──────────────────────────────────────┘    │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

> 分析基于 SeeAgent v1.26.2 源码，深度阅读了 agents/, core/, tools/ 下的 20+ 核心文件。

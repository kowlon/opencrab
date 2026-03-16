# 202603142112 — SeeAgent 多Agent模式：生命周期与上下文管理深度分析

> 基于源码深度阅读，涵盖 Agent 实例从创建到销毁的完整生命周期，以及每一层上下文隔离机制。

---

## 目录

1. [全景生命周期图](#1-全景生命周期图)
2. [6 种 Agent 实例的生命周期对比](#2-6-种-agent-实例的生命周期对比)
3. [创建阶段：Factory 与 Pool](#3-创建阶段factory-与-pool)
4. [运行阶段：消息处理流水线](#4-运行阶段消息处理流水线)
5. [上下文管理：7 层隔离体系](#5-上下文管理7-层隔离体系)
6. [上下文压缩机制](#6-上下文压缩机制)
7. [清理与销毁阶段](#7-清理与销毁阶段)
8. [故障降级的生命周期影响](#8-故障降级的生命周期影响)
9. [并行委派的上下文隔离](#9-并行委派的上下文隔离)
10. [关键代码摘录](#10-关键代码摘录)

---

## 1. 全景生命周期图

### 1.1 Agent 实例完整生命周期

```
                        ┌─────────────────────┐
                        │   multi_agent_enabled│
                        │   = True             │
                        └──────────┬──────────┘
                                   │
                                   ▼
                        ┌──────────────────────┐
                        │ Orchestrator.start()  │
                        │ ├─ ProfileStore       │
                        │ ├─ InstancePool       │
                        │ ├─ FallbackResolver   │
                        │ └─ 启动 Reaper 定时器 │
                        └──────────┬──────────┘
                                   │
            ┌──────────────────────┼──────────────────────┐
            │                      │                      │
            ▼                      ▼                      ▼
    ┌───────────────┐   ┌──────────────────┐   ┌──────────────────┐
    │  消息到达      │   │  60s Reaper 循环  │   │ 技能变更事件      │
    │  handle_msg() │   │  _reap_idle()    │   │ notify_skills_   │
    └───────┬───────┘   └────────┬─────────┘   │ changed()        │
            │                    │              └────────┬─────────┘
            ▼                    ▼                       ▼
    ┌───────────────┐   ┌──────────────────┐   ┌──────────────────┐
    │ Pool.get_or_  │   │ 闲置>30min的Agent│   │ skills_version++ │
    │ create()      │   │ → shutdown()     │   │ 下次访问时重建    │
    │               │   │ → 清理临时Profile│   │                  │
    │ 缓存命中?     │   └──────────────────┘   └──────────────────┘
    │  ├─ Yes: touch│
    │  └─ No: create│
    └───────┬───────┘
            │ (创建)
            ▼
    ┌───────────────────────────────────────────┐
    │              AgentFactory.create()         │
    │  1. Agent(name=...) 构造                   │
    │  2. initialize(lightweight=True)           │
    │     ├─ 加载身份文档                         │
    │     ├─ 加载技能                             │
    │     ├─ 启动内置MCP                          │
    │     ├─ 启动记忆会话                         │
    │     ├─ 构建系统提示词                       │
    │     └─ (跳过: 预热/表情包/人格特征)         │
    │  3. _apply_skill_filter(profile)           │
    │  4. 注入 custom_prompt / endpoint           │
    └───────────────┬───────────────────────────┘
                    │
                    ▼
    ┌───────────────────────────────────────────┐
    │         Agent 实例进入 Pool 缓存           │
    │    key = "{session_id}::{profile_id}"     │
    │    skills_version = current                │
    │    last_used = now                         │
    └───────────────┬───────────────────────────┘
                    │
                    ▼
    ┌───────────────────────────────────────────┐
    │        接收消息 → 执行 ReAct 循环          │
    │    (详见第4节)                              │
    └───────────────┬───────────────────────────┘
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
     正常完成    超时终止    异常失败
          │         │         │
          ▼         ▼         ▼
    ┌───────────────────────────────────────────┐
    │           清理阶段                         │
    │  1. _finalize_session()                   │
    │  2. _cleanup_session_state()              │
    │  3. reasoning_engine.release_large_buffers│
    │  4. 临时Profile → 清理                    │
    │  5. 子Agent状态 → 120s后清理              │
    └───────────────┬───────────────────────────┘
                    │
          ┌─────────┼─────────┐
          ▼                   ▼
    ┌───────────┐      ┌─────────────┐
    │ 留在Pool  │      │  被Reaper   │
    │ 等待复用  │      │  回收       │
    │ (30min)   │      │  shutdown() │
    └───────────┘      └─────────────┘
```

### 1.2 时间线视图

```
t=0         创建           t=n         最后使用       t=n+30min    回收
 │           │              │             │              │          │
 ▼           ▼              ▼             ▼              ▼          ▼
 ─────┬──────┬──────────────┬─────────────┬──────────────┬──────────┬──▶
      │      │              │             │              │          │
      │ Factory.create()   │        Pool.touch()   Reaper检测    shutdown()
      │ initialize()       │                       闲置>30min
      │ 进入Pool缓存      消息处理
      │                    ReAct循环
      │
   Orchestrator
   .handle_message()
```

---

## 2. 6 种 Agent 实例的生命周期对比

```
┌───────────────────┬──────────────┬────────────────┬───────────────────┬──────────────┐
│     实例类型       │   创建触发    │    Pool 缓存    │     销毁条件       │  Profile清理  │
├───────────────────┼──────────────┼────────────────┼───────────────────┼──────────────┤
│ 主Agent (CLI)     │ main.py启动  │ 不经过Pool     │ 应用退出           │ 无           │
│ 系统预设Agent     │ 首次委派     │ 30min闲置回收  │ Reaper回收         │ 不清理(SYSTEM)│
│ 用户自定义Agent   │ 首次委派     │ 30min闲置回收  │ Reaper回收         │ 不清理(CUSTOM)│
│ 动态持久Agent     │ create_agent │ 30min闲置回收  │ Reaper回收+7天清理 │ 7天自动清理   │
│ 临时Agent         │ spawn_agent  │ 30min闲置回收  │ 任务完成后立即清理 │ 立即清理      │
│ 并行克隆Agent     │ delegate_par │ 30min闲置回收  │ gather完成后清理   │ 立即清理      │
└───────────────────┴──────────────┴────────────────┴───────────────────┴──────────────┘
```

### 各类型详细生命周期

```
系统预设 Agent (如 code-assistant):
  创建 ──→ Pool缓存 ──→ [复用N次] ──→ 30min无人访问 ──→ Reaper回收
  │                      │                               │
  │                      └─ 每次访问 touch()              └─ shutdown()
  │                         重置闲置计时                      Profile保留
  └─ AgentFactory.create(preset_profile)

临时 Agent (spawn_agent):
  创建 ──→ Pool缓存 ──→ 执行任务 ──→ 任务完成 ──→ 立即清理Profile
  │                                    │              │
  │                                    │              └─ _try_cleanup_ephemeral()
  │                                    │                 store.remove_ephemeral()
  └─ AgentFactory.create(ephemeral_profile)
     ephemeral=True

并行克隆 Agent (delegate_parallel):
  创建N个 ──→ 各自进Pool ──→ asyncio.gather() ──→ 全部完成 ──→ 批量清理
  │                                                │              │
  │                                                │              └─ _cleanup_ephemeral_ids()
  └─ 同一agent_id的每次出现都创建独立克隆
     id=f"ephemeral_{agent_id}_{ts}_{idx}"
```

---

## 3. 创建阶段：Factory 与 Pool

### 3.1 AgentFactory.create() — 轻量初始化

```
文件: src/seeagent/agents/factory.py
```

```python
async def create(self, profile: AgentProfile, **kwargs) -> Agent:
    agent = Agent(name=profile.get_display_name(), **kwargs)
    agent._agent_profile = profile
    await agent.initialize(start_scheduler=False, lightweight=True)
    self._apply_skill_filter(agent, profile)
    if profile.custom_prompt:
        agent._custom_prompt_suffix = profile.custom_prompt
    if profile.preferred_endpoint:
        agent._preferred_endpoint = profile.preferred_endpoint
    return agent
```

### 3.2 initialize() — 完整 vs 轻量

```
文件: src/seeagent/core/agent.py (line 789)
```

```
┌──────────────────────┬──────────────────┬──────────────────┐
│       初始化步骤      │ 完整模式(主Agent) │ 轻量模式(子Agent) │
├──────────────────────┼──────────────────┼──────────────────┤
│ Token用量追踪         │       Yes        │       Yes        │
│ 加载身份文档          │       Yes        │       Yes        │
│ 加载已安装技能        │       Yes        │       Yes        │
│ 加载 MCP 服务器       │   全部加载       │   仅内置MCP      │
│ 启动记忆会话          │       Yes        │       Yes        │
│ 启动定时任务调度器    │       Yes        │       No         │
│ 构建系统提示词        │       Yes        │       Yes        │
│ 预热(清单/向量库)     │       Yes        │       No         │
│ 表情包引擎            │       Yes        │       No         │
│ 加载人格特征          │       Yes        │       No         │
│ Browser LLM注入       │       Yes        │       Yes        │
└──────────────────────┴──────────────────┴──────────────────┘
```

**轻量模式跳过的原因**：子 Agent 是按需创建的短期执行者，不需要预热缓存、表情包、人格特征等长期运行才需要的能力。

### 3.3 AgentInstancePool — 双重检查锁 + 版本感知

```
文件: src/seeagent/agents/factory.py
```

```
Pool 缓存键: "{session_id}::{profile_id}"

get_or_create(session_id, profile):
  │
  ├─ 1. 快速路径: 缓存命中 && skills_version 当前
  │     → touch() → 返回现有Agent
  │
  ├─ 2. 版本过期: skills_version < current
  │     → 驱逐旧Agent → shutdown(fire-and-forget)
  │
  ├─ 3. 缓存未命中: 加锁创建
  │     → asyncio.Lock (per-key)
  │     → 二次检查(防并发)
  │     → AgentFactory.create(profile)
  │     → 存入Pool
  │
  └─ 返回 Agent 实例
```

核心代码：

```python
async def get_or_create(self, session_id: str, profile: AgentProfile) -> Agent:
    key = self._make_key(session_id, profile.id)
    current_version = self._skills_version

    entry = self._pool.get(key)
    if entry:
        if entry.skills_version >= current_version:
            entry.touch()                    # 刷新 last_used
            return entry.agent
        # 版本过期，驱逐
        self._pool.pop(key, None)
        asyncio.ensure_future(entry.agent.shutdown())  # fire-and-forget

    # Per-key 锁，防止并发创建
    if key not in self._create_locks:
        self._create_locks[key] = asyncio.Lock()

    async with self._create_locks[key]:
        entry = self._pool.get(key)          # 二次检查
        if entry and entry.skills_version >= current_version:
            entry.touch()
            return entry.agent
        agent = await self._factory.create(profile)
        self._pool[key] = _PoolEntry(agent, profile.id, session_id, current_version)

    return agent
```

### 3.4 版本感知 — 技能变更时的重建

```
技能安装/卸载/更新
  │
  ▼
Pool.notify_skills_changed()
  │
  └─ self._skills_version += 1
     │
     ▼ (下次 get_or_create 时)
     │
     entry.skills_version < current_version
       → 驱逐旧Agent → 创建新Agent
```

---

## 4. 运行阶段：消息处理流水线

### 4.1 完整消息处理链路

```
用户消息到达
  │
  ▼
Orchestrator.handle_message(session, message)
  │
  ├─ 读取 session.context.agent_profile_id
  │
  ▼
_dispatch(session, message, profile_id, depth=0)
  │
  ├─ 深度检查 (MAX_DELEGATION_DEPTH = 5)
  ├─ 健康统计 + 委派链记录
  │
  ▼
_run_with_progress_timeout(session, message, profile_id)
  │
  ├─ Pool.get_or_create(session.id, profile) → Agent
  │
  ├─ asyncio.create_task(_call_agent(...))
  │
  ├─ ┌──── 进度监控循环 (每3秒) ────┐
  │  │                               │
  │  │ fingerprint = (iteration,     │
  │  │   status, tools_count)        │
  │  │                               │
  │  │ 指纹变化? → 重置闲置计时器    │
  │  │ 闲置 > 20min? → cancel + 超时 │
  │  │ hard_timeout? → cancel + 超时 │
  │  │                               │
  │  └───────────────────────────────┘
  │
  ▼
_call_agent(agent, session, message, is_sub_agent)
  │
  ├─ agent._is_sub_agent_call = is_sub_agent
  │
  ├─ session.context.get_messages() → session_messages
  │
  ▼
agent.chat_with_session(message, session_messages, session)
  │
  ├── _prepare_session_context() ─── 12步准备 ──┐
  │                                               │
  │   1. Memory session 对齐                      │
  │   2. IM ContextVar 设置                       │
  │   3. Agent state 初始化                       │
  │   4. Proactive engine 更新                    │
  │   5. 用户消息记忆记录                          │
  │   6. 特征挖掘 (LLM)                           │
  │   7. Prompt Compiler (两段式)                 │
  │   8. Plan 模式自动检测                        │
  │   9. Task Definition 设置                     │
  │   9.5 话题变更检测 + 上下文边界                │
  │   9.7 Scratchpad 工作记忆更新                 │
  │   10. 消息历史构建                             │
  │   11. 上下文压缩                               │
  │   12. TaskMonitor 创建                         │
  │                                               │
  ├── _chat_with_tools_and_context()              │
  │   │                                           │
  │   ├── 构建 System Prompt                      │
  │   │                                           │
  │   └── reasoning_engine.run() ── ReAct 循环 ──┤
  │       │                                       │
  │       │  for iteration in range(300):         │
  │       │    REASON → ACT → OBSERVE             │
  │       │                                       │
  │       └── 返回 response_text                  │
  │                                               │
  ├── _finalize_session() ─── 收尾 ──────────────┤
  │   │                                           │
  │   ├── 快照 react_trace                        │
  │   ├── 提取 token 用量                         │
  │   ├── 写入思维链摘要                           │
  │   ├── 完成 TaskMonitor                        │
  │   ├── 记录助手响应到记忆                       │
  │   ├── 自动关闭 Plan (子Agent跳过)             │
  │   └── 结束记忆会话 → 触发提取                  │
  │                                               │
  └── _cleanup_session_state() ─── 清理 (finally) │
      │                                           │
      ├── 清除 task_definition / task_query       │
      ├── 重置 IM ContextVar                      │
      ├── 清空 task-local session 引用             │
      ├── 重置已完成/取消的任务状态                 │
      └── reasoning_engine.release_large_buffers() │
```

---

## 5. 上下文管理：7 层隔离体系

### 5.1 隔离架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                          隔离层                                      │
│                                                                      │
│  Layer 7: Session 层                                                 │
│  ┌────────────────────────────────────────────────────────────┐      │
│  │ SessionManager._sessions[channel:chat:user:thread]         │      │
│  │ → 每个会话独立的 SessionContext (messages, variables, ...)  │      │
│  └────────────────────────────────────────────────────────────┘      │
│                                                                      │
│  Layer 6: Pool 层                                                    │
│  ┌────────────────────────────────────────────────────────────┐      │
│  │ AgentInstancePool._pool[session_id::profile_id]            │      │
│  │ → 每个 session+profile 组合独立的 Agent 实例                │      │
│  └────────────────────────────────────────────────────────────┘      │
│                                                                      │
│  Layer 5: Agent State 层                                             │
│  ┌────────────────────────────────────────────────────────────┐      │
│  │ AgentState._tasks[session_id] → TaskState                  │      │
│  │ → 每个 session 独立的任务状态 (iteration, tools, cancel)    │      │
│  └────────────────────────────────────────────────────────────┘      │
│                                                                      │
│  Layer 4: asyncio Task 层                                            │
│  ┌────────────────────────────────────────────────────────────┐      │
│  │ Agent._task_key() → id(asyncio.current_task())             │      │
│  │ → 并发 chat_with_session 调用之间的属性隔离                  │      │
│  │ → ContextVar 继承确保子协程找到父任务的 session              │      │
│  └────────────────────────────────────────────────────────────┘      │
│                                                                      │
│  Layer 3: IM Context 层                                              │
│  ┌────────────────────────────────────────────────────────────┐      │
│  │ ContextVar: current_im_session, current_im_gateway         │      │
│  │ → 协程级隔离，set/reset 在 prepare/cleanup 中              │      │
│  └────────────────────────────────────────────────────────────┘      │
│                                                                      │
│  Layer 2: Working Messages 层                                        │
│  ┌────────────────────────────────────────────────────────────┐      │
│  │ reasoning_engine.run() 内的 working_messages 局部变量       │      │
│  │ → 每次调用完全独立，不会在并发调用间共享                     │      │
│  └────────────────────────────────────────────────────────────┘      │
│                                                                      │
│  Layer 1: Memory Session 层                                          │
│  ┌────────────────────────────────────────────────────────────┐      │
│  │ MemoryManager._current_session_id                          │      │
│  │ → start_session() 重置内存缓冲区                            │      │
│  │ → Scratchpad 在新会话时清空                                  │      │
│  │ → SQLite 存储按 session_id 隔离                              │      │
│  └────────────────────────────────────────────────────────────┘      │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.2 各层详细说明

#### Layer 7: Session — 会话级隔离

```
文件: src/seeagent/sessions/session.py
```

```python
class SessionContext:
    messages: list[dict]                # 独立的对话历史
    agent_profile_id: str = "default"   # 绑定的Agent Profile
    delegation_chain: list[dict]        # 当前请求的委派链
    sub_agent_records: list[dict]       # 子Agent工作记录
    handoff_events: list[dict]          # Agent切换事件 (SSE)
    _msg_lock: threading.RLock          # 线程安全的消息访问
```

**Session 决定了哪个 Agent Profile 处理消息：**

```
Session.context.agent_profile_id = "code-assistant"
  → Orchestrator 读取此字段
  → 路由到 code-assistant Agent
```

**消息历史管理：**
- 每个 Session 维护独立的 `messages` 列表
- 超过 `max_history` (默认100条) 时自动截断，保留75%近期消息
- 截断的消息中提取规则性内容，作为合成摘要保留

#### Layer 6: Pool — 实例级隔离

```
Pool Key = "{session_id}::{profile_id}"

示例:
  "telegram_123_user1_abc::default"     → Agent实例A
  "telegram_123_user1_abc::code-assist" → Agent实例B  (同session,不同profile)
  "telegram_456_user2_def::default"     → Agent实例C  (不同session,同profile)
```

**同一个 session 可以同时有多个不同 profile 的 Agent 在运行。**

#### Layer 5: Agent State — 任务级隔离

```
文件: src/seeagent/core/agent_state.py
```

```python
class AgentState:
    _tasks: dict[str, TaskState] = {}  # session_id → TaskState
    _tasks_lock: threading.RLock       # 线程安全

class TaskState:
    task_id: str
    session_id: str
    status: TaskStatus
    cancelled: bool
    cancel_event: asyncio.Event        # 跨循环安全取消
    iteration: int                     # 当前迭代次数
    tools_executed: list[str]          # 已执行工具列表
    recent_tool_signatures: list[str]  # 循环检测签名
    original_user_messages: list[dict] # 模型切换时重置上下文
```

**状态机：**

```
IDLE → COMPILING → REASONING → ACTING → OBSERVING → VERIFYING
                      │           │         │
                      └───────────┴─────────┘  (循环)
                                  │
                           COMPLETED / FAILED / CANCELLED
```

#### Layer 4: asyncio Task — 协程级隔离

```
文件: src/seeagent/core/agent.py (line 255-309)
```

```python
# 类级别 ContextVar —— 跨协程传递 task key
_inherited_task_key: contextvars.ContextVar[int] = contextvars.ContextVar(
    "_inherited_task_key", default=0,
)

@staticmethod
def _task_key() -> int:
    inherited = Agent._inherited_task_key.get(0)
    if inherited:
        return inherited
    task = asyncio.current_task()
    return id(task) if task else 0
```

**为什么需要 ContextVar？**

```
Agent实例 (单例，服务多个session)
  │
  ├─ asyncio.Task A (session_1 的 chat_with_session)
  │   ├─ _current_session = session_1  ← 存在 _tls_session[task_key_A]
  │   └─ create_task(工具执行)          ← 子Task 通过 ContextVar 继承 task_key_A
  │       └─ 能找到 session_1
  │
  └─ asyncio.Task B (session_2 的 chat_with_session，并发)
      ├─ _current_session = session_2  ← 存在 _tls_session[task_key_B]
      └─ 不会读到 session_1 的数据
```

```python
# Task-local 属性的实现
@property
def _current_session(self):
    return self.__dict__.get("_tls_session", {}).get(self._task_key())

@_current_session.setter
def _current_session(self, value):
    tls = self.__dict__.setdefault("_tls_session", {})
    key = self._task_key()
    if value is None:
        tls.pop(key, None)
    else:
        tls[key] = value
        Agent._inherited_task_key.set(key)  # 传递给子协程
```

#### Layer 3: IM Context — ContextVar 隔离

```
文件: src/seeagent/core/im_context.py
```

```python
current_im_session: ContextVar[Any | None] = ContextVar("current_im_session", default=None)
current_im_gateway: ContextVar[Any | None] = ContextVar("current_im_gateway", default=None)
```

```
_prepare_session_context:
  im_tokens = set_im_context(session, gateway)   # 设置

_cleanup_session_state:
  reset_im_context(im_tokens)                     # 重置
```

#### Layer 2: Working Messages — 调用级隔离

```
文件: src/seeagent/core/reasoning_engine.py (line 532)
```

```python
working_messages = list(messages)  # 每次 run() 调用的局部变量
```

**Checkpoint 也是每次调用独立的：**

```python
# MAX_CHECKPOINTS = 5，存在 self._checkpoints 中
# 每次 run() 结束后在 release_large_buffers() 中清空
cp = Checkpoint(
    messages_snapshot=copy.deepcopy(messages),  # 深拷贝!
    state_snapshot={...},
    ...
)
```

#### Layer 1: Memory Session — 记忆级隔离

```
文件: src/seeagent/memory/manager.py
```

每个 Agent 实例拥有独立的 MemoryManager，但**共享底层 SQLite 数据库**：

```
Agent A (code-assistant)          Agent B (browser-agent)
  │                                 │
  ├─ MemoryManager 实例A           ├─ MemoryManager 实例B
  │  ├─ _session_turns = []        │  ├─ _session_turns = []
  │  ├─ _recent_messages = []      │  ├─ _recent_messages = []
  │  └─ _session_cited = []        │  └─ _session_cited = []
  │                                 │
  └──────────┬──────────────────────┘
             │
             ▼
      共享 SQLite (seeagent.db)
      ├─ memories 表 (全局)
      ├─ episodes 表 (按 session_id 分)
      └─ turns 表 (按 session_id 分)
```

**会话切换时的清理：**

```python
# _prepare_session_context (agent.py line 3449)
if memory_manager._current_session_id != conversation_safe_id:
    memory_manager.start_session(conversation_safe_id)
    # 清空 Scratchpad，防止跨会话泄漏
    store.save_scratchpad(Scratchpad(user_id="default"))
```

---

## 6. 上下文压缩机制

```
文件: src/seeagent/core/context_manager.py
```

### 6.1 压缩触发条件

```
Token 预算计算:
  max_tokens = get_max_context_tokens(conversation_id)
                │
                ├─ 读取端点的 context_window (默认 200,000)
                ├─ 减去 max_tokens (输出预留，最大1/3)
                └─ 乘以 0.95 (5% 安全缓冲)

  hard_limit = max_tokens - system_tokens - tools_tokens - 500
  soft_limit = hard_limit × 0.85

  当 current_tokens > soft_limit 时触发压缩
```

### 6.2 5 级压缩瀑布

```
┌──────────────────────────────────────────────────┐
│ Level 1: 大 tool_result 压缩                      │
│ 单个 tool_result > 5000 tokens                    │
│ → LLM 摘要到 15% 大小                             │
├──────────────────────────────────────────────────┤
│ Level 2: 上下文边界压缩                            │
│ 检测 "[上下文边界]" 标记                           │
│ → 边界前内容压缩到 18%                             │
│ → 生成 "[旧话题摘要]"                              │
├──────────────────────────────────────────────────┤
│ Level 3: 分组分块摘要                              │
│ 保留最近 8 组消息                                  │
│ 早期消息分块 (30000 tokens/块) LLM 摘要到 15%     │
├──────────────────────────────────────────────────┤
│ Level 4: 递归压缩                                  │
│ 仍然超标 → 保留组数降到 4，重复 Level 3            │
├──────────────────────────────────────────────────┤
│ Level 5: 硬截断 (最后手段)                         │
│ 逐条丢弃最早消息                                   │
│ 超长消息字符截断 (70% 头 + 20% 尾)                │
└──────────────────────────────────────────────────┘

压缩后:
  → rewrite_after_compression() 注入方向性提示
    (plan状态, scratchpad摘要, 已完成工具, 任务描述)
  → 丢弃的消息入队 memory extraction (不永久丢失)
```

### 6.3 每个 Agent 独立压缩

每个 Agent 实例有自己的 `ContextManager`，压缩阈值根据各自的 LLM 端点动态计算。子 Agent 如果使用不同的 `preferred_endpoint`，其 context window 可能不同。

---

## 7. 清理与销毁阶段

### 7.1 三阶段清理链

```
chat_with_session() 返回后:

try:
    response = await reasoning_engine.run(...)
    await _finalize_session(response, ...)     # 阶段1: 收尾
finally:
    _cleanup_session_state(im_tokens)          # 阶段2: 清理 (必定执行)
                                               # 内含阶段3: 释放大缓冲
```

#### 阶段 1: _finalize_session

```python
# agent.py line 3862
async def _finalize_session(self, response_text, session, session_id, task_monitor):
    # 1. 快照 react_trace → _last_finalized_trace
    # 2. 提取 token 用量摘要
    # 3. 写入思维链摘要到 session metadata
    # 4. 完成 TaskMonitor + 后台回顾
    # 5. 记录助手响应到记忆 (带工具调用数据)
    # 6. 自动关闭孤立 Plan (子Agent跳过此步)
    # 7. 结束记忆会话 → 触发异步 episode 生成和记忆提取
```

#### 阶段 2: _cleanup_session_state

```python
# agent.py line 3955
def _cleanup_session_state(self, im_tokens):
    self._current_task_definition = ""
    self._current_task_query = ""
    if im_tokens is not None:
        reset_im_context(im_tokens)              # 重置 ContextVar
    self._current_session = None                  # 清除 task-local 属性
    self.agent_state.current_session = None
    self._current_task_monitor = None
    # 重置已完成/取消的任务状态
    self.agent_state.reset_task(session_id=...)
    self._pending_cancels.pop(session_id, None)
    self._current_session_id = None
    self._current_conversation_id = None
    self.reasoning_engine.release_large_buffers() # 阶段3
```

#### 阶段 3: release_large_buffers

```python
# reasoning_engine.py line 184
def release_large_buffers(self):
    self._last_working_messages = []    # 释放工作消息
    self._checkpoints.clear()           # 清空所有 checkpoint
    self._tool_failure_counter.clear()  # 清空工具失败计数
    self._supervisor.reset()            # 重置监督器状态
```

### 7.2 Pool Reaper — 闲置回收

```
文件: src/seeagent/agents/factory.py
```

```
每 60 秒执行一次 _reap_idle():

1. 清理无用的 create_locks (key不在pool中 && lock未被持有)

2. 找出所有 idle_seconds > 30分钟 的 PoolEntry

3. 对每个过期 Entry:
   ├─ 从 Pool 中移除
   ├─ asyncio.ensure_future(agent.shutdown())  # fire-and-forget
   └─ 如果 profile.ephemeral:
       └─ ProfileStore.remove_ephemeral(profile_id)
```

### 7.3 Agent.shutdown() — 最终清理

```python
# agent.py line 7267
async def shutdown(self, task_description="", success=True, errors=None):
    # 1. 结束记忆会话 (触发 episode 生成)
    self.memory_manager.end_session(
        task_description=task_description,
        success=success,
        errors=errors or [],
    )
    # 2. 等待记忆系统挂起的异步任务 (最多15秒)
    await self.memory_manager.await_pending_tasks(timeout=15.0)
    # 3. 标记为已停止
    self._running = False
```

### 7.4 Orchestrator 子 Agent 状态清理

```
_update_sub_state(key, status="completed"):
  │
  ├─ 1. 更新状态到 "completed"
  ├─ 2. 持久化所有子Agent状态到磁盘 (sub_agent_states.json)
  ├─ 3. 清理临时 Profile: _try_cleanup_ephemeral(profile_id)
  └─ 4. 调度 120秒延迟清理:
       └─ asyncio.sleep(120) → 从 _sub_agent_states 中移除
          (给前端轮询留出2分钟窗口)
```

---

## 8. 故障降级的生命周期影响

```
文件: src/seeagent/agents/fallback.py
```

```
正常请求流:
  dispatch(profile_id="code-assistant")
    │
    ├─ FallbackResolver.get_effective_profile("code-assistant")
    │  └─ 未降级 → 返回 "code-assistant"
    │
    ▼
  Pool.get_or_create(session_id, code_assistant_profile)
    │
    └─ 成功 → FallbackResolver.record_success("code-assistant")

降级流:
  连续3次失败 (5分钟窗口内)
    │
    ▼
  FallbackResolver: code-assistant → degraded = True
    │
    ▼
  下次请求:
    dispatch(profile_id="code-assistant")
      │
      ├─ get_effective_profile("code-assistant")
      │  └─ 已降级 → 返回 fallback_profile_id = "default"
      │
      ▼
    Pool.get_or_create(session_id, default_profile)
      │
      └─ 使用 default Agent 处理
         │
         └─ 成功 → record_success("code-assistant")
                   → degraded = False (恢复)

生命周期影响:
  - 降级不会销毁原 Agent，它仍留在 Pool 中
  - 降级只改变路由目标
  - 恢复后原 Agent 可以继续复用 (如果未被 Reaper 回收)
```

---

## 9. 并行委派的上下文隔离

```
文件: src/seeagent/tools/handlers/agent.py
```

### 9.1 并行委派的克隆机制

```
delegate_parallel(tasks=[
    {agent_id: "code-assistant", task: "写代码"},
    {agent_id: "code-assistant", task: "写测试"},
    {agent_id: "browser-agent",  task: "搜索"},
])
```

```
检测到 "code-assistant" 出现 2 次
  │
  ▼
为每次出现创建独立的临时克隆:

  原始 Profile: code-assistant
    │
    ├─→ 克隆1: ephemeral_code-assistant_{ts}_0
    │   ├─ inherit_from = "code-assistant"
    │   ├─ ephemeral = True
    │   └─ 独立的 Agent 实例 (独立 Pool key)
    │
    └─→ 克隆2: ephemeral_code-assistant_{ts}_1
        ├─ inherit_from = "code-assistant"
        ├─ ephemeral = True
        └─ 独立的 Agent 实例 (独立 Pool key)

  browser-agent: 只出现1次，直接使用原 Profile
```

### 9.2 并行执行与清理

```python
# 并行执行
coros = [_run_one(t) for t in resolved_tasks]
raw_results = await asyncio.gather(*coros, return_exceptions=True)

# 结果收集
combined = []
for i, res in enumerate(raw_results):
    if isinstance(res, Exception):
        combined.append(f"[任务{i+1}] 失败: {res}")
    else:
        combined.append(f"[任务{i+1}] {res}")

# 清理所有临时克隆
self._cleanup_ephemeral_ids(ephemeral_ids, store)
```

### 9.3 上下文完全隔离

```
                    ┌─ 克隆1 ─────────────────────┐
                    │ Pool: session::ephemeral_0   │
                    │ MemoryManager 实例独立        │
                    │ working_messages 独立         │
                    │ TaskState 独立               │
asyncio.gather ────►│ ContextVar 独立              │
                    └──────────────────────────────┘
                    ┌─ 克隆2 ─────────────────────┐
                    │ Pool: session::ephemeral_1   │
                    │ MemoryManager 实例独立        │
                    │ working_messages 独立         │
                    │ TaskState 独立               │
               ────►│ ContextVar 独立              │
                    └──────────────────────────────┘
                    ┌─ browser-agent ─────────────┐
                    │ Pool: session::browser-agent │
                    │ MemoryManager 实例独立        │
                    │ ...                          │
               ────►│ ...                          │
                    └──────────────────────────────┘
```

---

## 10. 关键代码摘录

### 10.1 子 Agent 的委派上下文传递

```python
# tools/handlers/agent.py

async def _delegate(self, agent_id, message, reason, ...):
    # 不传递对话历史，只传递任务描述
    isolated_message = message
    if reason:
        isolated_message = f"[委派任务] {message}\n[委派原因] {reason}"

    result = await orchestrator.delegate(
        session=session,
        from_agent=current_agent,
        to_agent=agent_id,
        message=isolated_message,  # 仅任务，不含父Agent的上下文
        reason=reason,
    )
    return result
```

**子 Agent 不继承父 Agent 的对话历史。** 它们通过 Session 共享 `session.context.messages`（由 Session 层管理），但 ReasoningEngine 内的 `working_messages` 是完全独立的。

### 10.2 spawn_agent 的上下文继承

```python
# tools/handlers/agent.py

async def _spawn(self, inherit_from, extra_skills, custom_prompt_overlay, task, ...):
    base_profile = store.get(inherit_from)

    # 合并技能: 基础 + 额外
    merged_skills = list(base_profile.skills) + [s for s in extra_skills if s not in base_profile.skills]

    # 合并提示: 基础 + 覆盖
    merged_prompt = base_profile.custom_prompt
    if custom_prompt_overlay:
        merged_prompt = f"{merged_prompt}\n\n{custom_prompt_overlay}" if merged_prompt else custom_prompt_overlay

    ephemeral_profile = AgentProfile(
        id=f"ephemeral_{inherit_from}_{ts}",
        name=f"{base_profile.name} (临时)",
        type=AgentType.DYNAMIC,
        skills=merged_skills,
        skills_mode=base_profile.skills_mode,
        custom_prompt=merged_prompt,
        ephemeral=True,
        inherit_from=inherit_from,
        preferred_endpoint=base_profile.preferred_endpoint,
    )
    store.save(ephemeral_profile)

    # 委派到临时 Profile
    result = await orchestrator.delegate(session=session, to_agent=ephemeral_profile.id, ...)
    return result
```

### 10.3 _persist_sub_agent_record — 子 Agent 工作记录持久化

```python
# orchestrator.py

def _persist_sub_agent_record(agent, session, message, result, start_time):
    """将子Agent的工作记录写入父Session的 sub_agent_records"""
    record = {
        "agent_id": agent._agent_profile.id if agent._agent_profile else "unknown",
        "agent_name": agent.name,
        "task": message[:500],
        "result_preview": result[:500],
        "start_time": start_time,
        "end_time": time.time(),
        "duration_s": round(time.time() - start_time),
    }
    if hasattr(session.context, "sub_agent_records"):
        session.context.sub_agent_records.append(record)
        # 限制最多50条记录
        if len(session.context.sub_agent_records) > 50:
            session.context.sub_agent_records = session.context.sub_agent_records[-50:]
```

### 10.4 _try_fallback_or — 降级尝试

```python
# orchestrator.py

async def _try_fallback_or(self, session, message, profile_id, depth, default):
    """尝试降级到 fallback Agent，失败则返回默认错误信息"""
    fb = self._fallback.resolve_fallback(profile_id)
    if fb and fb.id != profile_id:
        hint = self._fallback.build_fallback_hint(profile_id)
        try:
            result = await self._dispatch(session, message, fb.id, depth)
            if hint:
                result = f"{hint}\n\n{result}"
            return result
        except Exception:
            pass
    return default
```

---

> 文档基于 SeeAgent v1.26.2 源码深度阅读，涉及 agents/, core/, sessions/, memory/, tools/ 下 25+ 核心文件。

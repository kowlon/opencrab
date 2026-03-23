# 最佳实践(BP)手动模式 — 全组件协作完整模拟

> 日期: 2026-03-23
> 基于代码版本: main 分支 (608f87d)

---

## 1. 参与组件一览

| 组件 | 源文件 | 职责 |
|------|--------|------|
| **Frontend** | `apps/setup-center/` (SeeCrab UI) | 用户交互、SSE 消费、按钮驱动 |
| **REST API** | `api/routes/seecrab.py` + `api/routes/bestpractice.py` | HTTP 入口、SSE 流生成、忙锁、断连监控 |
| **MasterAgent** | `core/agent.py` + `core/prompt_assembler.py` | LLM 推理、意图识别、BP 工具调用 |
| **BPToolHandler** | `bestpractice/handler.py` | 路由 bp_start / bp_edit_output / bp_switch_task |
| **BPStateManager** | `bestpractice/state_manager.py` | 实例生命周期、状态持久化、冷却期 |
| **BPEngine** | `bestpractice/engine.py` | 子任务调度执行、流式事件、输出整形 |
| **Orchestrator** | `agents/orchestrator.py` | SubAgent 创建、委派、超时看门狗 |
| **SubAgent** | `core/agent.py` (独立实例) | 具体子任务执行、ReAct 推理、工具调用 |

---

## 2. MasterAgent 的 BP 意图识别机制

MasterAgent 在 BP 流程中扮演 **"意图路由器"** 角色，通过两层机制识别 BP 意图：

### 2.1 第一层：预推理关键词匹配（拦截层）

在 MasterAgent 开始 LLM 推理 **之前**，SeeCrab API 层会先做关键词匹配：

```
用户消息 "帮我做竞品分析"
         │
         ▼
┌─────────────────────────────────┐
│  seecrab.py (line 150-212)      │
│                                 │
│  match_bp_from_message(         │
│    user_message, session_id     │
│  )                              │
│                                 │
│  扫描所有 BP 配置的 CONTEXT     │
│  触发器关键词：                  │
│  - "竞品" ✓ 命中!               │
│  - cooldown == 0 ✓              │
│  - 无活跃实例 ✓                  │
│  - 未曾推荐过 ✓                  │
│                                 │
│  → 返回 bp_match 对象           │
└────────────┬────────────────────┘
             │
             ▼
    ┌─────────────────┐
    │ 直接发 bp_offer  │  ← 不进入 LLM 推理！
    │ SSE 事件给前端    │
    │ + yield done     │
    │ + return         │
    └─────────────────┘
```

**关键设计**: 命中后立即 `return`，**跳过整个 MasterAgent 推理流程**，等待用户在前端选择。

### 2.2 第二层：LLM 推理识别（兜底层）

如果第一层未命中（比如 COMMAND 类型触发器、或用户用非常规表述），消息进入 MasterAgent 正常推理：

```
┌─────────────────────────────────────────────────────┐
│  MasterAgent 的 System Prompt 中包含 BP 段：         │
│                                                     │
│  PromptAssembler.build_system_prompt()              │
│    └─ _build_bp_section()                           │
│        ├─ get_static_prompt_section()  // 静态能力  │
│        │   → "你拥有最佳实践能力。可用模板:         │
│        │      - 竞品分析 (competitor_analysis)       │
│        │        关键词: 竞品, 分析                    │
│        │        流程: 数据收集 → 分析 → 报告         │
│        │        必需参数: topic(...)"                │
│        │                                             │
│        └─ get_dynamic_prompt_section() // 动态状态   │
│            → 当前活跃 BP 状态表                       │
│            → 意图路由提示                             │
│                                                     │
│  LLM 的 Tools 列表中包含：                           │
│    - bp_start(bp_id, input_data, run_mode)           │
│    - bp_edit_output(instance_id, subtask_id, changes)│
│    - bp_switch_task(target_instance_id)              │
│                                                     │
│  LLM 推理: "用户想做竞品分析 → 调用 bp_start"        │
└─────────────────────────────────────────────────────┘
```

### 2.3 两层协作关系

```
用户消息
  │
  ├─ 第一层: seecrab.py 关键词匹配
  │   ├─ 命中 → bp_offer 推荐卡片 → 等待用户选择 → (用户选BP) → MasterAgent bp_start
  │   └─ 未命中 ↓
  │
  └─ 第二层: MasterAgent LLM 推理
      ├─ System Prompt 包含 BP 能力声明
      ├─ Tools 列表包含 bp_start
      └─ LLM 自主判断是否调用 bp_start
```

---

## 3. 完整模拟：手动模式 3 个子任务

以下模拟一个包含 3 个子任务的 BP（竞品分析）从头到尾的完整流程。

### 场景设定

```yaml
BP 配置: competitor_analysis
子任务:
  - t1: 数据收集 (agent: data_collector)
  - t2: 数据分析 (agent: data_analyst)
  - t3: 报告生成 (agent: report_writer)
触发关键词: ["竞品", "竞争对手"]
默认模式: manual
```

---

### Phase 0: 系统初始化

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│server.py │     │facade.py │     │BPEngine  │     │BPStateMgr│     │Orchestr. │
└────┬─────┘     └────┬─────┘     └────┬─────┘     └────┬─────┘     └────┬─────┘
     │                │                │                │                │
     │ init_bp_system()                │                │                │
     │───────────────>│                │                │                │
     │                │                │                │                │
     │                │ ① new BPStateManager()          │                │
     │                │───────────────────────────────>│                │
     │                │                │                │                │
     │                │ ② new BPEngine(state_manager)  │                │
     │                │──────────────>│                │                │
     │                │                │                │                │
     │                │ ③ BPConfigLoader.load_all()    │                │
     │                │   扫描 best_practice/*/config.yaml               │
     │                │   加载 _shared/ 和各 BP 的 profiles              │
     │                │──────┐        │                │                │
     │                │<─────┘        │                │                │
     │                │  configs={competitor_analysis: BestPracticeConfig}│
     │                │                │                │                │
     │                │ ④ new BPToolHandler(engine, sm, configs)         │
     │                │──────┐        │                │                │
     │                │<─────┘        │                │                │
     │                │                │                │                │
     │ set_bp_orchestrator(orch)       │                │                │
     │───────────────>│                │                │                │
     │                │ engine.set_orchestrator()       │                │
     │                │──────────────>│                │                │
     │                │                │ self._orchestrator = orch       │
     │                │                │                │                │
     │ ⑤ Agent._init_handlers()       │                │                │
     │   注册 bp_start/bp_edit_output/bp_switch_task 工具到 LLM Tools 列表
     │──────┐         │                │                │                │
     │<─────┘         │                │                │                │
     │                │                │                │                │
```

---

### Phase 1: 用户发送消息 → BP 推荐

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│ Frontend │     │ SeeCrab  │     │ facade   │     │BPStateMgr│
│  (用户)   │     │  API     │     │          │     │          │
└────┬─────┘     └────┬─────┘     └────┬─────┘     └────┬─────┘
     │                │                │                │
     │ 用户输入:       │                │                │
     │ "帮我做竞品分析" │               │                │
     │                │                │                │
     │ POST /api/seecrab/chat          │                │
     │ {message: "帮我做竞品分析",      │                │
     │  conversation_id: "conv-123"}   │                │
     │───────────────>│                │                │
     │                │                │                │
     │                │ ──── 第一层拦截 ────             │
     │                │                │                │
     │                │ match_bp_from_message(           │
     │                │   "帮我做竞品分析",              │
     │                │   session_id)                    │
     │                │──────────────>│                │
     │                │                │                │
     │                │                │ 遍历所有 BP configs:
     │                │                │ competitor_analysis.triggers:
     │                │                │   type=CONTEXT, conditions=["竞品","竞争对手"]
     │                │                │   "竞品" in "帮我做竞品分析" → ✓ 命中!
     │                │                │                │
     │                │                │ 检查前置条件:   │
     │                │                │ get_cooldown() == 0 ✓
     │                │                │───────────────>│
     │                │                │  cooldown=0    │
     │                │                │<──────────────│
     │                │                │                │
     │                │                │ get_active() == None ✓
     │                │                │───────────────>│
     │                │                │  None          │
     │                │                │<──────────────│
     │                │                │                │
     │                │                │ is_bp_offered() == false ✓
     │                │                │───────────────>│
     │                │                │  false         │
     │                │                │<──────────────│
     │                │                │                │
     │                │  bp_match = {  │                │
     │                │    bp_id: "competitor_analysis", │
     │                │    bp_name: "竞品分析",          │
     │                │    subtask_count: 3,             │
     │                │    subtasks: [                   │
     │                │      {id:"t1", name:"数据收集"},  │
     │                │      {id:"t2", name:"数据分析"},  │
     │                │      {id:"t3", name:"报告生成"}   │
     │                │    ]                             │
     │                │  }             │                │
     │                │<──────────────│                │
     │                │                │                │
     │                │ ──── 生成推荐 ────              │
     │                │                │                │
     │                │ 构造推荐文本:                     │
     │                │ "检测到您的需求匹配最佳实践        │
     │                │  「竞品分析」，该任务包含 3 个     │
     │                │  子任务: 数据收集 → 数据分析 →    │
     │                │  报告生成。是否使用最佳实践流程？"  │
     │                │                │                │
     │ SSE: session_title              │                │
     │ {title: "帮我做竞品分析..."}    │                │
     │<───────────────│                │                │
     │                │                │                │
     │ SSE: bp_offer  │                │                │
     │ {type: "bp_offer",              │                │
     │  bp_id: "competitor_analysis",  │                │
     │  bp_name: "竞品分析",           │                │
     │  subtasks: [...],               │                │
     │  default_run_mode: "manual"}    │                │
     │<───────────────│                │                │
     │                │                │                │
     │                │ mark_bp_offered(session_id,      │
     │                │   "competitor_analysis")         │
     │                │──────────────────────────────>│
     │                │                │   已标记，本session│
     │                │                │   不会再推荐       │
     │                │                │                │
     │                │ session.add_message("assistant", │
     │                │   question, reply_state={        │
     │                │     bp_offer: {bp_id, bp_name}}) │
     │                │                │                │
     │ SSE: done      │                │                │
     │<───────────────│                │                │
     │                │                │                │
     │ return ← 不进入 MasterAgent 推理!                │
     │                │                │                │
```

**要点**: 命中后直接 `return`，MasterAgent 根本没有被调用。

---

### Phase 2: 用户选择「使用最佳实践」→ MasterAgent 调用 bp_start

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│ Frontend │     │ SeeCrab  │     │MasterAgnt│     │BPToolHdlr│     │BPStateMgr│
│  (用户)   │     │  API     │     │(LLM推理) │     │          │     │          │
└────┬─────┘     └────┬─────┘     └────┬─────┘     └────┬─────┘     └────┬─────┘
     │                │                │                │                │
     │ 用户点击       │                │                │                │
     │ [使用最佳实践]  │                │                │                │
     │                │                │                │                │
     │ POST /api/seecrab/chat          │                │                │
     │ {message: "请启用最佳实践       │                │                │
     │   competitor_analysis",         │                │                │
     │  conversation_id: "conv-123"}   │                │                │
     │───────────────>│                │                │                │
     │                │                │                │                │
     │                │ match_bp_from_message()         │                │
     │                │ → None (已 mark_bp_offered,     │                │
     │                │   不会再触发)                    │                │
     │                │                │                │                │
     │                │ ──── 进入正常 LLM 推理 ────     │                │
     │                │                │                │                │
     │                │ engine_stream(agent, session, message)           │
     │                │──────────────>│                │                │
     │                │                │                │                │
     │                │                │ ① 构建 System Prompt:          │
     │                │                │  PromptAssembler                │
     │                │                │   .build_system_prompt()        │
     │                │                │   └─ _build_bp_section()        │
     │                │                │       ├─ static: "你拥有最佳实践能力..."
     │                │                │       └─ dynamic: "(无活跃实例)" │
     │                │                │                │                │
     │                │                │ ② LLM 推理:                    │
     │                │                │  System Prompt 说:              │
     │                │                │  "当用户回复'请启用最佳实践     │
     │                │                │   (bp_id)'时，直接调用           │
     │                │                │   bp_start 启动"                │
     │                │                │                │                │
     │                │                │  用户说: "请启用最佳实践         │
     │                │                │   competitor_analysis"          │
     │                │                │                │                │
     │                │                │  LLM → tool_use: bp_start      │
     │                │                │  {bp_id: "competitor_analysis", │
     │                │                │   run_mode: "manual"}           │
     │                │                │                │                │
     │ SSE: thinking  │                │                │                │
     │ "用户选择了最佳实践模式..."      │                │                │
     │<───────────────│<───────────────│                │                │
     │                │                │                │                │
     │                │                │ ③ tool_executor.execute_tool()  │
     │                │                │   → handler_registry            │
     │                │                │   → "bestpractice" handler      │
     │                │                │──────────────>│                │
     │                │                │                │                │
     │                │                │                │ _handle_start():
     │                │                │                │                │
     │                │                │                │ 检查现有实例:   │
     │                │                │                │ get_active()    │
     │                │                │                │──────────────>│
     │                │                │                │  None          │
     │                │                │                │<──────────────│
     │                │                │                │                │
     │                │                │                │ 创建实例:      │
     │                │                │                │ create_instance(
     │                │                │                │   bp_config,   │
     │                │                │                │   session.id,  │
     │                │                │                │   input_data={},
     │                │                │                │   run_mode=MANUAL)
     │                │                │                │──────────────>│
     │                │                │                │                │
     │                │                │                │   ┌─ 创建 BPInstanceSnapshot:
     │                │                │                │   │  instance_id = "bp-a1b2c3d4"
     │                │                │                │   │  status = ACTIVE
     │                │                │                │   │  run_mode = MANUAL
     │                │                │                │   │  current_subtask_index = 0
     │                │                │                │   │  subtask_statuses = {
     │                │                │                │   │    t1: "pending",
     │                │                │                │   │    t2: "pending",
     │                │                │                │   │    t3: "pending"
     │                │                │                │   │  }
     │                │                │                │   │  initial_input = {}
     │                │                │                │   └─>
     │                │                │                │                │
     │                │                │                │  "bp-a1b2c3d4" │
     │                │                │                │<──────────────│
     │                │                │                │                │
     │                │                │                │ 推送 SSE 事件到 event_bus:
     │                │                │                │ {type: "bp_instance_created",
     │                │                │                │  instance_id: "bp-a1b2c3d4",
     │                │                │                │  bp_id: "competitor_analysis",
     │                │                │                │  bp_name: "竞品分析",
     │                │                │                │  subtasks: [{id:"t1",...},...]
     │                │                │                │ }
     │                │                │                │                │
     │ SSE: bp_instance_created        │                │                │
     │<────────────────────────────────│                │                │
     │                │                │                │                │
     │                │                │ 工具返回:       │                │
     │                │                │ "✅ 已创建 BP 实例「竞品分析」   │
     │                │                │  (id=bp-a1b2c3d4)。             │
     │                │                │  前端将自动开始执行。"           │
     │                │                │<──────────────│                │
     │                │                │                │                │
     │ SSE: ai_text   │                │                │                │
     │ "已启动竞品分析最佳实践..."      │                │                │
     │<───────────────│<───────────────│                │                │
     │                │                │                │                │
     │ SSE: done      │                │                │                │
     │<───────────────│                │                │                │
     │                │                │                │                │
     │ ════════════════════════════════════════════════════════════════│
     │ 此时前端收到 bp_instance_created，                               │
     │ 切换到 BP 执行界面，自动调用 /api/bp/start                       │
     │ ════════════════════════════════════════════════════════════════│
```

---

### Phase 3: 前端启动 BP 执行 → 第一个子任务

```
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ Frontend │  │ BP API   │  │ BPEngine │  │Scheduler │  │Orchestr. │  │ SubAgent │
│          │  │ /api/bp  │  │          │  │          │  │          │  │data_collr│
└────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
     │             │             │             │             │             │
     │ POST /api/bp/start       │             │             │             │
     │ {bp_id: "competitor_analysis",          │             │             │
     │  session_id: "conv-123", │             │             │             │
     │  input_data: {topic: "AI助手"},         │             │             │
     │  run_mode: "manual"}     │             │             │             │
     │────────────>│             │             │             │             │
     │             │             │             │             │             │
     │             │ _bp_mark_busy("conv-123", "bp_start")  │             │
     │             │────┐        │             │             │             │
     │             │<───┘ true (获取锁)        │             │             │
     │             │             │             │             │             │
     │             │ sm.create_instance(bp_config, ...)     │             │
     │             │ → instance_id = "bp-a1b2c3d4"         │             │
     │             │             │             │             │             │
     │             │ resolve_session(create_if_missing=true) │             │
     │             │────┐        │             │             │             │
     │             │<───┘        │             │             │             │
     │             │             │             │             │             │
     │ SSE: bp_instance_created  │             │             │             │
     │ {instance_id, bp_id,      │             │             │             │
     │  subtasks: [t1,t2,t3]}    │             │             │             │
     │<────────────│             │             │             │             │
     │             │             │             │             │             │
     │             │ ═══ engine.advance("bp-a1b2c3d4", session) ═══      │
     │             │────────────>│             │             │             │
     │             │             │             │             │             │
     │             │             │ ① yield bp_progress (初始状态)         │
     │ SSE: bp_progress          │             │             │             │
     │ {statuses: {t1:"pending", │             │             │             │
     │  t2:"pending", t3:"pending"},           │             │             │
     │  current_subtask_index: 0}│             │             │             │
     │<────────────│<────────────│             │             │             │
     │             │             │             │             │             │
     │             │             │ ② scheduler.get_ready_tasks()          │
     │             │             │────────────>│             │             │
     │             │             │             │ idx=0, t1 状态=PENDING    │
     │             │             │  [SubtaskConfig(id="t1",  │             │
     │             │             │   name="数据收集",         │             │
     │             │             │   agent_profile="data_collector")]      │
     │             │             │<────────────│             │             │
     │             │             │             │             │             │
     │             │             │ ③ scheduler.resolve_input("t1")        │
     │             │             │────────────>│             │             │
     │             │             │             │ idx==0 → 使用 initial_input
     │             │             │  {topic: "AI助手"}        │             │
     │             │             │<────────────│             │             │
     │             │             │             │             │             │
     │             │             │ ④ _check_input_completeness()          │
     │             │             │   → missing = [] (无缺失) │             │
     │             │             │             │             │             │
     │             │             │ ⑤ sm.update_subtask_status("t1", CURRENT)
     │             │             │             │             │             │
     │             │             │ ⑥ yield bp_subtask_start  │             │
     │ SSE: bp_subtask_start     │             │             │             │
     │ {subtask_id: "t1",        │             │             │             │
     │  subtask_name: "数据收集"} │             │             │             │
     │<────────────│<────────────│             │             │             │
     │             │             │             │             │             │
     │             │             │ ═══ _run_subtask_stream() ═══          │
     │             │             │             │             │             │
     │             │             │ ⑦ _build_delegation_message():         │
     │             │             │ ┌─────────────────────────────────┐    │
     │             │             │ │ ## 最佳实践任务: 竞品分析       │    │
     │             │             │ │ ### 当前子任务: 数据收集         │    │
     │             │             │ │                                 │    │
     │             │             │ │ ### 输入数据                    │    │
     │             │             │ │ ```json                         │    │
     │             │             │ │ {"topic": "AI助手"}             │    │
     │             │             │ │ ```                             │    │
     │             │             │ │                                 │    │
     │             │             │ │ ### 输出格式要求                │    │
     │             │             │ │ ```json                         │    │
     │             │             │ │ {"raw_data": [...],             │    │
     │             │             │ │  "sources": [...]}              │    │
     │             │             │ │ ```                             │    │
     │             │             │ │                                 │    │
     │             │             │ │ ## 限制                         │    │
     │             │             │ │ - 禁止使用 ask_user 工具       │    │
     │             │             │ │ - JSON 必须严格符合输出格式     │    │
     │             │             │ └─────────────────────────────────┘    │
     │             │             │             │             │             │
     │             │             │ ⑧ 替换 event_bus 为临时 Queue          │
     │             │             │   old_bus = session.context._sse_event_bus
     │             │             │   session.context._sse_event_bus = temp_queue
     │             │             │             │             │             │
     │             │             │ ⑨ orchestrator.delegate(  │             │
     │             │             │   session, "bp_engine",   │             │
     │             │             │   "data_collector",       │             │
     │             │             │   message,                │             │
     │             │             │   session_messages=[])    │  ← 上下文隔离!
     │             │             │────────────────────────>│             │
     │             │             │             │             │             │
     │             │             │             │             │ _dispatch()  │
     │             │             │             │             │             │
     │             │             │             │             │ ProfileStore │
     │             │             │             │             │ .get("data_collector")
     │             │             │             │             │────┐        │
     │             │             │             │             │<───┘        │
     │             │             │             │             │             │
     │             │             │             │             │ AgentPool   │
     │             │             │             │             │ .get_or_create()
     │             │             │             │             │ → 创建新 Agent 实例
     │             │             │             │             │────┐        │
     │             │             │             │             │<───┘        │
     │             │             │             │             │             │
     │             │             │             │             │ asyncio.create_task(
     │             │             │             │             │  _call_agent_streaming())
     │             │             │             │             │────────────>│
     │             │             │             │             │             │
     │             │             │             │             │             │ agent.chat_with_
     │             │             │             │             │             │ session_stream()
     │             │             │             │             │             │
     │             │             │             │             │             │ ┌─ ReAct 循环 ──┐
     │             │             │             │             │             │ │ Think:        │
     │             │             │             │             │             │ │ "需要搜索竞品 │
     │             │             │             │             │             │ │  AI助手信息"  │
     │             │             │             │             │             │ └───────────────┘
     │             │             │             │             │             │
     │             │             │ ⑩ 事件循环: 从 temp_queue 消费事件     │
     │             │             │             │             │             │
     │             │             │ event_bus.get() ← thinking_delta       │
     │             │             │ yield {type:"thinking",   │             │
     │             │             │  content:"需要搜索...",   │             │
     │             │             │  agent_id:"data_collector"}│             │
     │ SSE: thinking             │             │             │             │
     │ {content: "需要搜索..."}  │             │             │             │
     │<────────────│<────────────│             │             │             │
     │             │             │             │             │             │
     │             │             │ event_bus.get() ← tool_call_start      │
     │             │             │ (首个非thinking事件,                    │
     │             │             │  先 yield delegate card running)       │
     │             │             │             │             │             │
     │ SSE: step_card            │             │             │             │
     │ {step_id: "delegate_t1", │             │             │             │
     │  title: "委派 data_collector: 数据收集",│             │             │
     │  status: "running",       │             │             │             │
     │  card_type: "delegate"}   │             │             │             │
     │<────────────│<────────────│             │             │             │
     │             │             │             │             │             │
     │             │             │ StepFilter.classify("web_search", args)│
     │             │             │ StepAggregator.on_tool_call_start()    │
     │             │             │             │             │             │
     │ SSE: step_card            │             │             │             │
     │ {step_id: "tool_xxx",     │             │             │             │
     │  title: "web_search",     │             │             │             │
     │  status: "running"}       │             │             │             │
     │<────────────│<────────────│             │             │             │
     │             │             │             │             │             │
     │             │             │ ... SubAgent 继续执行多轮 ReAct ...    │
     │             │             │ ... 每个 tool_call 都产生 step_card ...│
     │             │             │             │             │             │
     │ SSE: step_card (web_search completed)   │             │             │
     │ SSE: step_card (read_file running)      │             │             │
     │ SSE: step_card (read_file completed)    │             │             │
     │<────────────│<────────────│             │             │             │
     │             │             │             │             │             │
     │             │             │ ⑪ delegate_task.done() == true         │
     │             │             │             │             │             │
     │             │             │ raw_result = await delegate_task       │
     │             │             │ = "**总结**: 已收集3个竞品数据...\n   │
     │             │             │   ```json\n{\"raw_data\":[...],\n     │
     │             │             │   \"sources\":[...]}\n```"             │
     │             │             │             │             │             │
     │             │             │ 恢复原始 event_bus                     │
     │             │             │ session.context._sse_event_bus = old_bus
     │             │             │             │             │             │
     │             │             │ ⑫ _parse_output(raw_result)           │
     │             │             │  Strategy 2: 提取 ```json...``` 代码块 │
     │             │             │  → {raw_data: [...], sources: [...]}   │
     │             │             │             │             │             │
     │             │             │ yield {type: "_internal_output",       │
     │             │             │  data: {raw_data:[...], sources:[...]},│
     │             │             │  raw_result: "**总结**...",            │
     │             │             │  tool_results: ["搜索结果1","..."]}    │
     │             │             │             │             │             │
```

---

### Phase 4: 子任务完成 → 手动模式暂停

```
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ Frontend │  │ BP API   │  │ BPEngine │  │Scheduler │  │BPStateMgr│
│          │  │          │  │ advance()│  │          │  │          │
└────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
     │             │             │             │             │
     │             │             │ (回到 advance() 主循环)    │
     │             │             │             │             │
     │             │             │ ① yield step_card (delegate completed)
     │ SSE: step_card            │             │             │
     │ {step_id: "delegate_t1", │             │             │
     │  status: "completed",     │             │             │
     │  duration: 45.2}          │             │             │
     │<────────────│<────────────│             │             │
     │             │             │             │             │
     │             │             │ ② _conform_output()       │
     │             │             │  检查: output 的 keys 是否满足 t2.input_schema.required
     │             │             │  如果不满足 → 调用 LLM 轻量映射:
     │             │             │  "请从执行结果中提取并整理出符合目标格式的 JSON"
     │             │             │  → conformed_output       │
     │             │             │             │             │
     │             │             │ ③ scheduler.complete_task("t1", output)
     │             │             │────────────>│             │
     │             │             │             │ snap.subtask_outputs["t1"] = output
     │             │             │             │ snap.subtask_statuses["t1"] = "done"
     │             │             │             │ snap.current_subtask_index = 1
     │             │             │             │             │
     │             │             │ ④ _persist_state()        │
     │             │             │────────────────────────>│
     │             │             │             │ session.metadata["bp_state"] =
     │             │             │             │   sm.serialize_for_session()
     │             │             │             │ = {version:1, instances:[{
     │             │             │             │     instance_id: "bp-a1b2c3d4",
     │             │             │             │     status: "active",
     │             │             │             │     current_subtask_index: 1,
     │             │             │             │     subtask_statuses: {
     │             │             │             │       t1: "done",
     │             │             │             │       t2: "pending",
     │             │             │             │       t3: "pending"
     │             │             │             │     },
     │             │             │             │     subtask_outputs: {
     │             │             │             │       t1: {raw_data:[...], sources:[...]}
     │             │             │             │     }
     │             │             │             │   }]}
     │             │             │             │             │
     │             │             │ ⑤ yield bp_subtask_complete
     │ SSE: bp_subtask_complete  │             │             │
     │ {instance_id: "bp-a1b2c3d4",           │             │
     │  subtask_id: "t1",        │             │             │
     │  subtask_name: "数据收集", │             │             │
     │  output: {raw_data:[...], │             │             │
     │           sources:[...]}, │             │             │
     │  output_schema: (t2的input_schema),     │             │
     │  summary: "已收集3个竞品数据..."}       │             │
     │<────────────│<────────────│             │             │
     │             │             │             │             │
     │             │             │ ⑥ yield bp_progress       │
     │ SSE: bp_progress          │             │             │
     │ {statuses: {t1:"done",    │             │             │
     │  t2:"pending", t3:"pending"},           │             │
     │  current_subtask_index: 1}│             │             │
     │<────────────│<────────────│             │             │
     │             │             │             │             │
     │             │             │ ⑦ === MANUAL MODE 检查 === │
     │             │             │ snap.run_mode == MANUAL    │
     │             │             │   → YES!                   │
     │             │             │             │             │
     │             │             │ yield bp_waiting_next      │
     │ SSE: bp_waiting_next      │             │             │
     │ {instance_id: "bp-a1b2c3d4",           │             │
     │  next_subtask_index: 1}   │             │             │
     │<────────────│<────────────│             │             │
     │             │             │             │             │
     │             │             │ return (generator 结束)    │
     │             │             │             │             │
     │             │ _persist_bp_to_session()  │             │
     │             │ (写入 session 消息历史)    │             │
     │             │────┐        │             │             │
     │             │<───┘        │             │             │
     │             │             │             │             │
     │ SSE: done   │             │             │             │
     │<────────────│             │             │             │
     │             │             │             │             │
     │             │ _bp_clear_busy("conv-123")│             │
     │             │────┐        │             │             │
     │             │<───┘ (释放锁)             │             │
     │             │             │             │             │
     │ ═══════════════════════════════════════════════════════│
     │                                                       │
     │  前端此时显示:                                         │
     │  ┌────────────────────────────────────┐               │
     │  │ ✅ 数据收集 (完成)                  │               │
     │  │    raw_data: [...3条竞品数据...]    │               │
     │  │    sources: [...数据来源...]        │               │
     │  │                                    │               │
     │  │ ⏳ 数据分析 (待执行)                │               │
     │  │ ⏳ 报告生成 (待执行)                │               │
     │  │                                    │               │
     │  │  [进入下一步]  [编辑输出]           │               │
     │  └────────────────────────────────────┘               │
     │                                                       │
     │ ═══ 用户审查输出，决定下一步 ═══════════════════════════│
```

---

### Phase 5: 用户点击「进入下一步」→ 第二个子任务

```
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ Frontend │  │ BP API   │  │ BPEngine │  │Scheduler │  │Orchestr. │  │ SubAgent │
│  (用户)   │  │ /bp/next │  │          │  │          │  │          │  │data_anlst│
└────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
     │             │             │             │             │             │
     │ 用户点击     │             │             │             │             │
     │ [进入下一步]  │             │             │             │             │
     │             │             │             │             │             │
     │ POST /api/bp/next         │             │             │             │
     │ {instance_id: "bp-a1b2c3d4",            │             │             │
     │  session_id: "conv-123",  │             │             │             │
     │  user_message: "进入下一步"}             │             │             │
     │────────────>│             │             │             │             │
     │             │             │             │             │             │
     │             │ _ensure_bp_restored()     │             │             │
     │             │ (检查内存中是否有该实例,   │             │             │
     │             │  若服务重启后需从          │             │             │
     │             │  session.metadata恢复)    │             │             │
     │             │             │             │             │             │
     │             │ _bp_mark_busy()           │             │             │
     │             │ _persist_user_message()   │             │             │
     │             │  → session.add_message("user", "进入下一步")          │
     │             │             │             │             │             │
     │             │ engine.advance("bp-a1b2c3d4", session)  │             │
     │             │────────────>│             │             │             │
     │             │             │             │             │             │
     │             │             │ yield bp_progress          │             │
     │ SSE: bp_progress          │             │             │             │
     │<────────────│<────────────│             │             │             │
     │             │             │             │             │             │
     │             │             │ scheduler.get_ready_tasks()│             │
     │             │             │────────────>│             │             │
     │             │             │             │ idx=1, t2 状态=PENDING    │
     │             │             │ [SubtaskConfig(id="t2",   │             │
     │             │             │  name="数据分析",          │             │
     │             │             │  agent_profile="data_analyst")]         │
     │             │             │<────────────│             │             │
     │             │             │             │             │             │
     │             │             │ scheduler.resolve_input("t2")           │
     │             │             │────────────>│             │             │
     │             │             │             │ idx=1 (非0) │             │
     │             │             │             │ → 使用 subtask_outputs["t1"]
     │             │             │ {raw_data:[...],          │             │
     │             │             │  sources:[...]}           │             │
     │             │             │<────────────│             │             │
     │             │             │             │             │             │
     │             │             │ (同 Phase 3 流程:                       │
     │             │             │  check_input → CURRENT → subtask_start │
     │             │             │  → delegate → SubAgent ReAct           │
     │             │             │  → 事件流式转发                         │
     │             │             │  → output → conform → complete)         │
     │             │             │             │             │             │
     │             │             │ orchestrator.delegate(     │             │
     │             │             │   to_agent="data_analyst", │             │
     │             │             │   message=委派消息,        │             │
     │             │             │   session_messages=[])     │             │
     │             │             │────────────────────────>│             │
     │             │             │             │             │────────────>│
     │             │             │             │             │             │
     │ SSE: thinking, step_cards (同 Phase 3)  │             │ ReAct 循环  │
     │<────────────│<────────────│             │             │             │
     │             │             │             │             │             │
     │             │             │ ... 子任务完成 ...         │             │
     │             │             │             │             │             │
     │ SSE: bp_subtask_complete  │             │             │             │
     │ {subtask_id: "t2",        │             │             │             │
     │  output: {insights:[...], │             │             │             │
     │           trends:[...]}}  │             │             │             │
     │<────────────│<────────────│             │             │             │
     │             │             │             │             │             │
     │ SSE: bp_progress          │             │             │             │
     │ {statuses: {t1:"done",    │             │             │             │
     │  t2:"done", t3:"pending"}}│             │             │             │
     │<────────────│<────────────│             │             │             │
     │             │             │             │             │             │
     │             │             │ MANUAL → yield bp_waiting_next          │
     │ SSE: bp_waiting_next      │             │             │             │
     │<────────────│<────────────│             │             │             │
     │             │             │             │             │             │
     │ SSE: done   │             │             │             │             │
     │<────────────│             │             │             │             │
     │             │             │             │             │             │
     │ ═══ 用户再次审查，再点击 [进入下一步] ═════════════════│             │
```

---

### Phase 6: 最终子任务完成 → BP 整体完成

```
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ Frontend │  │ BP API   │  │ BPEngine │  │Scheduler │  │BPStateMgr│
│  (用户)   │  │ /bp/next │  │ advance()│  │          │  │          │
└────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
     │             │             │             │             │
     │ POST /api/bp/next (第三次) │             │             │
     │────────────>│             │             │             │
     │             │             │             │             │
     │             │ engine.advance()          │             │
     │             │────────────>│             │             │
     │             │             │             │             │
     │             │             │ ... 执行 t3 (报告生成) ...│
     │             │             │ (同 Phase 3-4 流程)       │
     │             │             │             │             │
     │ SSE: thinking, step_cards │             │             │
     │ SSE: bp_subtask_complete  │             │             │
     │ {subtask_id: "t3",        │             │             │
     │  output: {report:"...",   │             │             │
     │           files:[...]}}   │             │             │
     │<────────────│<────────────│             │             │
     │             │             │             │             │
     │             │             │ bp_progress (all done)    │
     │ SSE: bp_progress          │             │             │
     │ {statuses: {t1:"done",    │             │             │
     │  t2:"done", t3:"done"}}   │             │             │
     │<────────────│<────────────│             │             │
     │             │             │             │             │
     │             │             │ scheduler.is_done()       │
     │             │             │────────────>│             │
     │             │             │  true       │             │
     │             │             │<────────────│             │
     │             │             │             │             │
     │             │             │ sm.complete("bp-a1b2c3d4")│
     │             │             │────────────────────────>│
     │             │             │             │ status = COMPLETED
     │             │             │             │ completed_at = now()
     │             │             │             │             │
     │             │             │ _persist_state()          │
     │             │             │────────────────────────>│
     │             │             │             │             │
     │             │             │ yield bp_complete         │
     │ SSE: bp_complete          │             │             │
     │ {instance_id: "bp-a1b2c3d4",           │             │
     │  bp_id: "competitor_analysis",          │             │
     │  bp_name: "竞品分析",     │             │             │
     │  outputs: {               │             │             │
     │    t1: {raw_data:[...], sources:[...]}, │             │
     │    t2: {insights:[...], trends:[...]},  │             │
     │    t3: {report:"...", files:[...]}      │             │
     │  }}                       │             │             │
     │<────────────│<────────────│             │             │
     │             │             │             │             │
     │             │             │ return (不再 yield bp_waiting_next)    │
     │             │             │             │             │
     │ SSE: done   │             │             │             │
     │<────────────│             │             │             │
     │             │             │             │             │
     │ ═══════════════════════════════════════════════════════│
     │  前端显示:                                            │
     │  ┌────────────────────────────────────┐               │
     │  │ ✅ 竞品分析 — 全部完成             │               │
     │  │                                    │               │
     │  │ ✅ 数据收集 ✅ 数据分析 ✅ 报告生成 │               │
     │  │                                    │               │
     │  │ 最终报告:                          │               │
     │  │ [查看报告] [下载文件]              │               │
     │  └────────────────────────────────────┘               │
     │ ═══════════════════════════════════════════════════════│
```

---

## 4. 特殊流程模拟

### 4.1 缺少输入字段 (bp_ask_user)

```
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ Frontend │  │ BP API   │  │ BPEngine │  │Scheduler │
└────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
     │             │             │             │
     │             │             │ advance() 执行到 t2       │
     │             │             │             │
     │             │             │ resolve_input("t2")       │
     │             │             │────────────>│
     │             │             │ {raw_data:[...]}          │  ← 缺少 analysis_scope
     │             │             │<────────────│
     │             │             │             │
     │             │             │ _check_input_completeness()
     │             │             │ t2.input_schema.required = ["raw_data", "analysis_scope"]
     │             │             │ input_data 中无 "analysis_scope"
     │             │             │ → missing = ["analysis_scope"]
     │             │             │             │
     │             │             │ sm.update_subtask_status("t2", WAITING_INPUT)
     │             │             │             │
     │             │             │ yield bp_ask_user
     │ SSE: bp_ask_user          │             │
     │ {instance_id: "bp-a1b2c3d4",           │
     │  subtask_id: "t2",        │             │
     │  subtask_name: "数据分析", │             │
     │  missing_fields: ["analysis_scope"],    │
     │  input_schema: {          │             │
     │    properties: {          │             │
     │      analysis_scope: {    │             │
     │        type: "string",    │             │
     │        description: "分析范围"          │
     │      }                    │             │
     │    }                      │             │
     │  }}                       │             │
     │<────────────│<────────────│             │
     │             │             │             │
     │             │             │ return (暂停)│
     │             │             │             │
     │ ═══ 前端弹出输入表单 ═══   │             │
     │                           │             │
     │ 用户填写:                  │             │
     │ analysis_scope = "功能对比+定价策略"    │
     │                           │             │
     │ POST /api/bp/answer       │             │
     │ {instance_id: "bp-a1b2c3d4",           │
     │  subtask_id: "t2",        │             │
     │  data: {analysis_scope:   │             │
     │    "功能对比+定价策略"},   │             │
     │  session_id: "conv-123"}  │             │
     │────────────>│             │             │
     │             │             │             │
     │             │ engine.answer("bp-a1b2c3d4", "t2", data, session)
     │             │────────────>│             │
     │             │             │             │
     │             │             │ ① snap.supplemented_inputs["t2"] =
     │             │             │     {analysis_scope: "功能对比+定价策略"}
     │             │             │             │
     │             │             │ ② sm.update_subtask_status("t2", PENDING)
     │             │             │             │
     │             │             │ ③ 重新进入 advance()      │
     │             │             │             │
     │             │             │ resolve_input("t2")       │
     │             │             │────────────>│
     │             │             │ {raw_data:[...],          │
     │             │             │  analysis_scope: "功能对比+定价策略"}
     │             │             │<────────────│  ← supplemented_inputs 已合并!
     │             │             │             │
     │             │             │ _check_input_completeness()
     │             │             │ → missing = [] ✓ 全部满足! │
     │             │             │             │
     │             │             │ (正常执行子任务...)         │
```

### 4.2 编辑已完成输出 (Chat-to-Edit)

```
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ Frontend │  │ BP API   │  │ BPEngine │  │BPStateMgr│
└────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
     │             │             │             │
     │ t1 已完成, t2 已完成, t3 待执行          │
     │ 用户发现 t1 数据有误，想修改             │
     │             │             │             │
     │ PUT /api/bp/edit-output   │             │
     │ {instance_id: "bp-a1b2c3d4",           │
     │  subtask_id: "t1",        │             │
     │  changes: {               │             │
     │    raw_data: [新的数据...]  │             │
     │  }}                       │             │
     │────────────>│             │             │
     │             │             │             │
     │             │ engine.handle_edit_output()│
     │             │────────────>│             │
     │             │             │             │
     │             │             │ ① sm.merge_subtask_output("t1", changes)
     │             │             │────────────────────────>│
     │             │             │             │ deep_merge(existing, changes)
     │             │             │             │ t1.output.raw_data = [新的数据...]
     │             │             │             │             │
     │             │             │ ② sm.mark_downstream_stale("t1", bp_config)
     │             │             │────────────────────────>│
     │             │             │             │ t2: done → STALE
     │             │             │             │ t3: pending (不变)
     │             │             │  [stale_ids: ["t2"]]    │
     │             │             │<────────────────────────│
     │             │             │             │             │
     │             │ {success: true,           │             │
     │             │  merged: {...},           │             │
     │             │  stale_subtasks: ["t2"]}  │             │
     │             │<────────────│             │             │
     │             │             │             │             │
     │ 200 OK      │             │             │             │
     │ {success, stale: ["t2"]}  │             │             │
     │<────────────│             │             │             │
     │             │             │             │             │
     │ ═══ 前端更新状态 ═══       │             │             │
     │ t1: ✅ done (已编辑)      │             │             │
     │ t2: ⚠️ stale (需重新执行)  │             │             │
     │ t3: ⏳ pending            │             │             │
     │             │             │             │             │
     │ 用户点击 [进入下一步]      │             │             │
     │             │             │             │             │
     │ POST /api/bp/next         │             │             │
     │────────────>│             │             │             │
     │             │ engine.advance()          │             │
     │             │────────────>│             │             │
     │             │             │             │             │
     │             │             │ get_ready_tasks()         │
     │             │             │ → idx=1 (current_subtask_index未回退)
     │             │             │   但 t2 状态=STALE，满足条件
     │             │             │ → [t2] 重新执行！         │
     │             │             │             │             │
     │             │             │ (使用编辑后的 t1 输出作为 t2 输入)
```

### 4.3 任务切换 (bp_switch_task)

```
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ Frontend │  │MasterAgnt│  │BPToolHdlr│  │BPStateMgr│  │CtxBridge │
│  (用户)   │  │          │  │          │  │          │  │          │
└────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
     │             │             │             │             │
     │ "切换到之前的内容创建任务" │             │             │
     │────────────>│             │             │             │
     │             │             │             │             │
     │             │ LLM 推理:   │             │             │
     │             │ System Prompt 动态段包含:  │             │
     │             │ "用户可能想要: B) 切换到   │             │
     │             │  其他任务 (bp_switch_task)"│             │
     │             │             │             │             │
     │             │ tool_use: bp_switch_task   │             │
     │             │ {target_instance_id:       │             │
     │             │  "bp-x5y6z7w8"}           │             │
     │             │────────────>│             │             │
     │             │             │             │             │
     │             │             │ ① sm.suspend("bp-a1b2c3d4")
     │             │             │ (暂停当前竞品分析)         │
     │             │             │────────────────────────>│
     │             │             │             │ status=SUSPENDED
     │             │             │             │             │
     │             │             │ ② sm.resume("bp-x5y6z7w8")
     │             │             │ (恢复内容创建)             │
     │             │             │────────────────────────>│
     │             │             │             │ status=ACTIVE
     │             │             │             │             │
     │             │             │ ③ set_pending_switch()    │
     │             │             │────────────────────────>│
     │             │             │             │ 入队 PendingContextSwitch:
     │             │             │             │ {suspended: "bp-a1b2c3d4",
     │             │             │             │  target: "bp-x5y6z7w8"}
     │             │             │             │             │
     │             │ "已切换到任务「内容创建」" │             │
     │             │<────────────│             │             │
     │             │             │             │             │
     │             │ ──── 下一轮推理前 ────     │             │
     │             │             │             │             │
     │             │ Agent._pre_reasoning_hook()│             │
     │             │────────────────────────────────────────>│
     │             │             │             │             │
     │             │             │             │ consume_pending_switch()
     │             │             │             │────────────>│
     │             │             │             │ (取出 switch)│
     │             │             │             │<────────────│
     │             │             │             │             │
     │             │             │             │ execute_pending_switch():
     │             │             │             │             │
     │             │             │             │ _compress_context():
     │             │             │             │  压缩竞品分析的上下文为摘要
     │             │             │             │             │
     │             │             │             │ _restore_context():
     │             │             │             │  注入 "[任务恢复] 内容创建..."
     │             │             │             │  到 MasterAgent 的对话上下文
     │             │             │             │             │
     │             │<────────────────────────────────────────│
     │             │             │             │             │
     │             │ 继续正常推理, 上下文已切换到内容创建任务  │
```

---

## 5. 完整 SSE 事件时间线

```
时间   事件                        来源           数据摘要
────   ────                        ────           ────────
 │
 │     ═══ Phase 1: BP 推荐 ═══
 │
 ├──   session_title               seecrab.py     {title: "帮我做竞品分析..."}
 ├──   bp_offer                    seecrab.py     {bp_id, bp_name, subtasks, default_run_mode}
 ├──   done                        seecrab.py
 │
 │     ═══ Phase 2: 用户选择BP → MasterAgent ═══
 │
 ├──   thinking                    MasterAgent    "用户选择了最佳实践模式..."
 ├──   bp_instance_created         BPToolHandler  {instance_id, bp_id, subtasks}
 ├──   ai_text                     MasterAgent    "已启动竞品分析..."
 ├──   done                        seecrab.py
 │
 │     ═══ Phase 3: 子任务 1 执行 ═══
 │
 ├──   bp_progress                 BPEngine       {t1:pending, t2:pending, t3:pending}
 ├──   bp_subtask_start            BPEngine       {subtask_id:"t1", name:"数据收集"}
 ├──   thinking                    SubAgent       "需要搜索竞品AI助手信息..."
 ├──   step_card (delegate)        BPEngine       {status:"running", delegate_agent:"data_collector"}
 ├──   step_card (tool)            SubAgent       {title:"web_search", status:"running"}
 ├──   step_card (tool)            SubAgent       {title:"web_search", status:"completed"}
 ├──   step_card (tool)            SubAgent       {title:"read_file", status:"running"}
 ├──   step_card (tool)            SubAgent       {title:"read_file", status:"completed"}
 ├──   step_card (delegate)        BPEngine       {status:"completed", duration:45.2}
 ├──   bp_subtask_complete         BPEngine       {subtask_id:"t1", output:{...}, summary:"..."}
 ├──   bp_progress                 BPEngine       {t1:done, t2:pending, t3:pending}
 ├──   bp_waiting_next             BPEngine       {next_subtask_index:1}
 ├──   done                        BP API
 │
 │     ═══ 用户审查 → 点击[进入下一步] ═══
 │
 │     ═══ Phase 5: 子任务 2 执行 ═══
 │
 ├──   bp_progress                 BPEngine       {t1:done, t2:pending, t3:pending}
 ├──   bp_subtask_start            BPEngine       {subtask_id:"t2", name:"数据分析"}
 ├──   thinking                    SubAgent       "分析收集到的竞品数据..."
 ├──   step_card (delegate)        BPEngine       {status:"running"}
 ├──   ...tool events...           SubAgent
 ├──   step_card (delegate)        BPEngine       {status:"completed", duration:62.1}
 ├──   bp_subtask_complete         BPEngine       {subtask_id:"t2", output:{...}}
 ├──   bp_progress                 BPEngine       {t1:done, t2:done, t3:pending}
 ├──   bp_waiting_next             BPEngine       {next_subtask_index:2}
 ├──   done                        BP API
 │
 │     ═══ 用户审查 → 点击[进入下一步] ═══
 │
 │     ═══ Phase 6: 子任务 3 执行 (最终) ═══
 │
 ├──   bp_progress                 BPEngine       {t1:done, t2:done, t3:pending}
 ├──   bp_subtask_start            BPEngine       {subtask_id:"t3", name:"报告生成"}
 ├──   thinking                    SubAgent       "根据分析结果撰写报告..."
 ├──   step_card (delegate)        BPEngine       {status:"running"}
 ├──   ...tool events...           SubAgent
 ├──   step_card (delegate)        BPEngine       {status:"completed", duration:38.5}
 ├──   bp_subtask_complete         BPEngine       {subtask_id:"t3", output:{...}}
 ├──   bp_progress                 BPEngine       {t1:done, t2:done, t3:done}
 ├──   bp_complete                 BPEngine       {outputs: {t1:{...}, t2:{...}, t3:{...}}}
 ├──   done                        BP API
 │
 ▼
```

---

## 6. 组件职责总结

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│  Frontend (SeeCrab UI)                                              │
│  ├─ 渲染 bp_offer 推荐卡片                                          │
│  ├─ 用户选择后发送 "请启用最佳实践 {bp_id}"                           │
│  ├─ 收到 bp_instance_created 后切换到 BP 执行界面                     │
│  ├─ 调用 /api/bp/start 启动第一个子任务                              │
│  ├─ 消费 SSE 流: thinking → step_cards → subtask_complete            │
│  ├─ 收到 bp_waiting_next 时显示 [进入下一步] 按钮                     │
│  ├─ 用户点击后调用 /api/bp/next                                      │
│  └─ 收到 bp_complete 时显示最终结果                                   │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  REST API (seecrab.py + bestpractice.py)                            │
│  ├─ 预推理层: match_bp_from_message() 关键词拦截                     │
│  ├─ SSE 流生成: StreamingResponse + async generator                 │
│  ├─ 忙锁: _bp_mark_busy / _bp_clear_busy (防并发)                   │
│  ├─ 断连监控: _disconnect_watcher (防僵尸)                           │
│  ├─ 状态持久化: _persist_bp_to_session (防丢失)                      │
│  └─ 恢复: _ensure_bp_restored (服务重启后从 metadata 恢复)           │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  MasterAgent (core/agent.py)                                        │
│  ├─ 意图识别: System Prompt 中 BP 段指导 LLM 识别 BP 意图            │
│  │   └─ static: 能力声明 + 触发规则 + 交互规则                       │
│  │   └─ dynamic: 当前状态表 + 意图路由提示                           │
│  ├─ 工具调用: bp_start / bp_edit_output / bp_switch_task             │
│  │   └─ 3 个工具注册到 LLM Tools 列表                               │
│  │   └─ 工具执行路由到 BPToolHandler                                 │
│  └─ 上下文管理: _pre_reasoning_hook 消费 PendingContextSwitch        │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  BPToolHandler (handler.py)                                         │
│  ├─ bp_start: 创建实例 → 推送 bp_instance_created 事件              │
│  ├─ bp_edit_output: 深度合并 → 标记下游 STALE                       │
│  └─ bp_switch_task: suspend/resume → 入队 PendingContextSwitch      │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  BPStateManager (state_manager.py)                                  │
│  ├─ 实例生命周期: create → suspend → resume → complete → cancel     │
│  ├─ 子任务状态: PENDING → CURRENT → DONE / STALE / FAILED           │
│  ├─ 持久化: serialize_for_session ↔ restore_from_dict               │
│  ├─ 冷却期: 防止 BP 反复触发                                         │
│  └─ 去重: mark_bp_offered 防重复推荐                                  │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  BPEngine (engine.py)                                               │
│  ├─ advance(): 核心 async generator，驱动子任务执行                  │
│  │   ├─ Scheduler 获取就绪任务                                       │
│  │   ├─ 输入完整性检查 → bp_ask_user                                 │
│  │   ├─ _run_subtask_stream() 委派执行                               │
│  │   ├─ _conform_output() LLM 输出整形                               │
│  │   ├─ manual: yield bp_waiting_next + return                       │
│  │   └─ auto: continue while loop                                   │
│  ├─ answer(): 处理用户补充输入 → 重新进入 advance()                   │
│  └─ handle_edit_output(): 深度合并 + 下游 STALE                      │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Orchestrator (orchestrator.py)                                     │
│  ├─ delegate(): 接收 BPEngine 委派请求                               │
│  ├─ _dispatch(): 加载 Profile → 创建/获取 Agent 实例                 │
│  ├─ _call_agent_streaming(): 流式执行 + 事件转发到 event_bus          │
│  ├─ 超时看门狗: 每 3s 检查 fingerprint，idle 1200s 则 kill           │
│  └─ session_messages=[] 实现上下文隔离                                │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  SubAgent (独立 Agent 实例)                                          │
│  ├─ 由 AgentPool.get_or_create() 创建                               │
│  ├─ 使用专属 AgentProfile (如 data_collector)                        │
│  ├─ 接收: 委派消息 (输入数据 + 输出格式要求)                          │
│  ├─ 执行: ReAct 循环 (Think → Act → Observe)                        │
│  │   └─ 可使用所有注册工具 (web_search, read_file, ...)              │
│  ├─ 流式事件: thinking_delta, tool_call_start/end → event_bus        │
│  └─ 返回: 文本结果 (含 JSON 代码块)                                   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 7. 关键源文件索引

| 文件 | 行号 | 关键逻辑 |
|------|------|----------|
| `api/routes/seecrab.py` | 150-212 | 预推理 BP 关键词匹配 + bp_offer 事件 |
| `api/routes/bestpractice.py` | 339-417 | /api/bp/start SSE 端点 |
| `api/routes/bestpractice.py` | 420-482 | /api/bp/next SSE 端点 |
| `api/routes/bestpractice.py` | 485-547 | /api/bp/answer SSE 端点 |
| `core/agent.py` | 1087-1117 | BP 工具注册到 MasterAgent |
| `core/prompt_assembler.py` | 115-131 | BP 段注入 System Prompt |
| `core/prompt_assembler.py` | 421-438 | _build_bp_section() |
| `bestpractice/facade.py` | 61-118 | init_bp_system() 初始化 |
| `bestpractice/facade.py` | 161-197 | match_bp_from_message() 触发匹配 |
| `bestpractice/facade.py` | 203-241 | get_static_prompt_section() |
| `bestpractice/facade.py` | 244-303 | get_dynamic_prompt_section() |
| `bestpractice/handler.py` | 65-121 | _handle_start() 创建实例 |
| `bestpractice/engine.py` | 81-248 | advance() 核心执行循环 |
| `bestpractice/engine.py` | 297-508 | _run_subtask_stream() 委派执行 |
| `bestpractice/engine.py` | 540-565 | _build_delegation_message() |
| `bestpractice/engine.py` | 660-695 | _parse_output() JSON 提取 |
| `bestpractice/engine.py` | 699-765 | _conform_output() LLM 映射 |
| `bestpractice/scheduler.py` | 88-107 | LinearScheduler 线性调度 |
| `bestpractice/state_manager.py` | 42-68 | create_instance() |
| `bestpractice/state_manager.py` | 215-245 | serialize/restore 持久化 |
| `agents/orchestrator.py` | 822-899 | delegate() 委派入口 |
| `agents/orchestrator.py` | 1080-1132 | _call_agent_streaming() |
| `bestpractice/prompts/system_static.md` | 全文 | BP 能力声明模板 |
| `bestpractice/prompts/system_dynamic.md` | 全文 | BP 状态注入模板 |

# bp_switch_task 与 bp_edit_output：LLM 判断依据、注入阶段与切换处理深度梳理

## 0. 结论先行（TL;DR）

当前 `bp_switch_task` / `bp_edit_output` 是否会被 LLM 选中，核心由三类输入共同决定：

1. **工具可见性（Tool Schema）**  
   由 `BP_TOOL_DEFINITIONS` 在 Agent 初始化阶段注册进 LLM 工具列表，LLM 才“看得见并可调用”这两个工具。

2. **系统提示词中的 BP 规则文本（System Prompt）**  
   当前编译管线实际稳定注入的是 **BP 静态段**（工具说明与交互规则）。  
   其中明确写了“用户要改输出→`bp_edit_output`”“用户要切任务→`bp_switch_task`”。

3. **消息历史（Messages）中的运行时证据**  
   包括用户最新输入、历史对话、工具结果（`tool_result`），以及切换后注入的恢复消息（`[Task Resumed] ...`）。

---

## 1. LLM 的“判断依据上下文”到底有哪些

### 1.1 工具定义层（function calling schema）

`bp_edit_output` 与 `bp_switch_task` 的 schema 定义在：

- `src/seeagent/bestpractice/tool_definitions.py`

关键点：

- `bp_edit_output` 必填 `subtask_id` + `changes`
- `bp_switch_task` 必填 `target_instance_id`
- 描述语义直接引导意图匹配（修改输出 / 切换任务）

这层决定 LLM **能不能调用、参数该怎么填**。

---

### 1.2 BP 规则文本层（system_static 模板）

静态模板在：

- `src/seeagent/bestpractice/prompt/templates/system_static.md`

它明确写出：

- Chat-to-Edit 时用 `bp_edit_output`
- 切换进行中任务时用 `bp_switch_task`

这层决定 LLM **“何时应该优先选哪个工具”** 的规则倾向。

---

### 1.3 运行时消息层（conversation + tool_result + 恢复消息）

推理循环会把工具执行结果以 `role=user, content=[tool_result...]` 回灌给模型，形成闭环上下文，位置在：

- `src/seeagent/core/reasoning_engine.py`

切换任务后，`ContextBridge` 会把恢复文本注入到当前用户消息或追加一条 user 消息，位置在：

- `src/seeagent/bestpractice/engine/context_bridge.py`

这层决定 LLM **下一轮是否“知道自己刚切过任务、当前在什么子任务、已有什么输出”**。

---

## 2. 注入发生在什么阶段（按真实时序）

## 阶段 A：Agent 初始化（一次性）

在 `Agent._init_handlers()` 中：

1. `init_bp_system()` 初始化 BP 子系统（有配置才启用）
2. 注册 `bestpractice` handler（工具路由到 `BPToolHandler`）
3. 将 `BP_TOOL_DEFINITIONS` append 到 `self._tools`，并 add 到 `tool_catalog`

对应文件：

- `src/seeagent/core/agent.py`

这一步后，LLM 调用时的 tools 列表才会包含 `bp_edit_output` / `bp_switch_task`。

---

## 阶段 B：每轮请求准备（_prepare_session_context）

在 `Agent._prepare_session_context()` 中，BP 相关关键步骤是：

1. **BP lazy restore**：若内存中没有该 session 的 BP 实例，从 `session.metadata["bp_state"]` 恢复  
2. **BP context switch 消费**：调用 `execute_pending_switch()`，执行挂起任务压缩 + 目标任务恢复注入

对应文件：

- `src/seeagent/core/agent.py`
- `src/seeagent/bestpractice/engine/context_bridge.py`
- `src/seeagent/bestpractice/engine/state_manager.py`

这一步发生在真正调用模型前，属于 **messages 侧注入**。

---

## 阶段 C：每轮构建 system prompt（编译管线）

主流程使用的是：

- `Agent._build_system_prompt_compiled()`
- `PromptAssembler.build_system_prompt_compiled()`
- `prompt.builder.build_system_prompt()`

其中 BP 段来自 `_build_bp_section()`（`src/seeagent/prompt/builder.py`），当前实现只拿：

- `get_static_prompt_section()`

即：**当前编译管线稳定注入静态段，不注入动态段**（状态表 / 意图路由等动态文本未走入此路径）。

---

## 3. 关于“动态判断上下文”的一个关键现状

`bestpractice/prompt/builder.py` 里确实实现了 `build_dynamic_section()`（含 `status_table`、`intent_routing` 等），但当前主用编译路径 `prompt/builder.py::_build_bp_section()` 没有调用 `get_dynamic_prompt_section(session_id)`。

这意味着：

- **设计上有动态注入能力**
- **但当前主运行路径里，LLM 对 `bp_edit_output` / `bp_switch_task` 的判断主要依赖静态规则 + 消息历史，而不是动态状态表**

注意：

- `core/prompt_assembler.py` 的旧式 `_build_bp_section()` 是“静态+动态”实现
- 但聊天主流程走的是 compiled pipeline（`_build_system_prompt_compiled`）

---

## 4. bp_switch_task 的处理链路（含切换过程中做了什么）

## 4.1 LLM 调用前提

LLM 看到：

- 工具 schema（可调用）
- 静态规则“切换任务调用 bp_switch_task”
- 用户语义（如“切回 XX 任务”）

然后发起：

- `bp_switch_task(target_instance_id=...)`

---

## 4.2 Handler 层校验（防错/防串会话）

`BPToolHandler._handle_switch_task()` 会依次检查：

1. `target_instance_id` 非空
2. 目标实例存在
3. 目标实例属于当前 session（防跨会话切换）

然后调用 `engine.switch()`。

---

## 4.3 Engine 层状态变更

`BPEngine.switch()` 做这些事：

1. 找当前 active 实例
2. 若目标已是 active，返回 `already_active`
3. 有当前 active 则 `suspend(current)`
4. `resume(target)`
5. 写入 `PendingContextSwitch(suspended_id, target_id)`
6. 持久化到 `session.metadata["bp_state"]`

**重要**：此时只是完成“状态切换 + 挂起上下文切换任务”，真正上下文交接在下一轮 `_prepare_session_context()` 消费 pending switch 时执行。

---

## 4.4 ContextBridge 真正执行切换

`execute_pending_switch()`：

1. `consume_pending_switch(session_id)`（一次性消费）
2. 对被挂起实例做上下文压缩  
   - 优先 LLM 语义压缩（`brain.think_lightweight`）  
   - 失败回退机械压缩
3. 把压缩结果写回 `suspended.context_summary`
4. 给目标实例注入恢复提示（`[Task Resumed]...`）到 messages

恢复提示包含：

- 任务名、步骤进度
- 子任务状态列表
- 已有关键输出摘要
- 语义上下文总结
- 初始用户意图

这会直接影响下一轮 LLM 的工具选择与回应上下文。

---

## 5. bp_edit_output 的处理链路（含副作用）

## 5.1 LLM 调用前提

LLM 根据：

- 工具 schema（`subtask_id + changes`）
- 静态规则（用户修改上一步结果→`bp_edit_output`）
- 当前对话语义（“把刚才输出改成...”）

调用 `bp_edit_output(...)`。

---

## 5.2 Handler 层校验

`BPToolHandler._handle_edit_output()`：

1. 解析实例（显式 `instance_id` 或 fallback 当前 active）
2. 校验 `subtask_id`、`changes`
3. 校验实例存在
4. 校验实例归属当前 session
5. 获取 bp_config
6. 调用 `engine.handle_edit_output(...)`

---

## 5.3 Engine/State 层变更与副作用

`BPEngine.handle_edit_output()`：

1. 检查该 subtask 是否已有 output（无输出不可编辑）
2. 深度合并 `changes`（dict 递归；数组整体替换）
3. 将该 subtask 后续已 DONE 的子任务标记为 `STALE`
4. 做软校验，返回 warning（不阻断）

同时 handler 会触发 `_emit_stale(...)`，向前端同步“下游需重跑”的信号。

---

## 6. 切换过程中最关键的状态结构

## 6.1 BPInstanceSnapshot

字段要点：

- `status`（active/suspended/...）
- `current_subtask_index`
- `subtask_statuses`
- `subtask_outputs`
- `context_summary`
- `supplemented_inputs`

定义在：

- `src/seeagent/bestpractice/models.py`

---

## 6.2 PendingContextSwitch

字段：

- `suspended_instance_id`
- `target_instance_id`

语义：

- 由 `bp_start` / `bp_switch_task` 创建
- 由 `Agent._prepare_session_context()` 消费

定义在：

- `src/seeagent/bestpractice/models.py`

---

## 6.3 Session 持久化入口

`BPStateManager.persist_to_session()` 会将 session 的 BP 全状态写到：

- `session.metadata["bp_state"]`

重启后由 `restore_from_dict()` 进行 lazy restore。

---

## 7. “LLM 为什么会在某一轮选 switch / edit”的可解释框架

可按下面顺序解释一次真实决策：

1. **工具可调用**：初始化时已注册 schema  
2. **规则有指引**：静态 BP 提示词里明确场景  
3. **语义匹配**：用户话语更像“改结果”还是“切任务”  
4. **历史证据**：前几轮 `tool_result`、恢复消息、当前问句共同约束  
5. **参数可填性**：`subtask_id` / `target_instance_id` 是否可从上下文提取

---

## 8. 风险与观察（当前实现下）

1. **动态 BP 段未进入主编译路径**  
   使 LLM 少了 status_table/intent_routing 这类强引导，可能增加误选工具概率。

2. **system_static 有历史文案残留**  
   其中提到 `bp_get_output`，但当前工具定义中并无该工具；可能造成模型轻微混淆。

3. **切换上下文恢复是“下一轮生效”**  
   用户在切换后的第一句若很短，模型主要依赖 recovery message + 历史，设计上合理但需认知这一时序。

---

## 9. 关键源码索引（便于继续深挖）

- 工具定义：`src/seeagent/bestpractice/tool_definitions.py`
- 工具注册：`src/seeagent/core/agent.py` (`_init_handlers`)
- 主聊天入口：`src/seeagent/core/agent.py` (`chat_with_session_stream`)
- 会话准备：`src/seeagent/core/agent.py` (`_prepare_session_context`)
- prompt 编译组装：`src/seeagent/core/prompt_assembler.py` + `src/seeagent/prompt/builder.py`
- BP 静态/动态构建：`src/seeagent/bestpractice/prompt/builder.py`
- 切换与编辑处理：`src/seeagent/bestpractice/handler.py`, `src/seeagent/bestpractice/engine/core.py`
- 状态持久化与 pending switch：`src/seeagent/bestpractice/engine/state_manager.py`
- 上下文压缩恢复：`src/seeagent/bestpractice/engine/context_bridge.py`
- 工具结果回灌：`src/seeagent/core/reasoning_engine.py`

---

## 10. 请求级时序（你关心的“在哪一阶段注入”）

### 10.1 `bp_switch_task` 一次完整闭环

1. 用户表达“切到某任务”  
2. LLM 基于 schema + 静态规则选择 `bp_switch_task`  
3. `BPToolHandler._handle_switch_task` 做参数/归属校验  
4. `BPEngine.switch` 执行 `suspend(current) + resume(target)`  
5. 写入 `PendingContextSwitch`，并 `persist_to_session()` 到 `session.metadata["bp_state"]`  
6. 当前轮工具结果以 `tool_result` 回灌到 `working_messages`  
7. 下一轮 `_prepare_session_context()` 调 `execute_pending_switch()`  
8. `ContextBridge` 压缩旧任务上下文并注入目标任务恢复消息  
9. 之后才进入本轮模型推理（带恢复后的 messages）

### 10.2 `bp_edit_output` 一次完整闭环

1. 用户表达“修改上一步结果”  
2. LLM 选择 `bp_edit_output(subtask_id, changes)`  
3. handler 做 instance/subtask/session 校验  
4. engine 深度合并输出  
5. 下游 DONE 子任务改标 `STALE`  
6. 返回“合并预览 + stale 列表 + warning(可选)”  
7. tool_result 回灌到 messages，影响同轮后续 ReAct 与下一轮决策  
8. 前端收到 stale 事件后，用户通常会继续 `bp_next` 触发重跑

---

## 11. 你问题的直接对照答案

1. **“判断依据上下文是什么？”**  
   工具 schema + BP 静态规则段 + 当前消息历史（含 tool_result 与恢复消息）。

2. **“是什么阶段注入的？”**  
   - 工具 schema：Agent 初始化阶段注入到 tool list  
   - BP 静态规则：每轮构建 system prompt 时注入  
   - 恢复消息：每轮 `_prepare_session_context()` 里、模型调用前注入到 messages  
   - 工具执行结果：ReAct 工具执行后即刻回灌到 messages

3. **“切换过程中有什么处理？”**  
   `suspend/resume + pending_switch 入队 + 持久化 + 下一轮消费 pending_switch + 压缩旧上下文 + 恢复新上下文`，并通过恢复消息让 LLM 在下一轮立即进入正确任务语境。

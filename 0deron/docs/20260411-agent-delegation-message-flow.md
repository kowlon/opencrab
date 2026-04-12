# MainAgent 与 SubAgent (含最佳实践 BP) 的消息传递与处理逻辑深度解析

本文档详细梳理了 SeeAgent 系统中，主 Agent (MainAgent) 在分派任务给普通子 Agent (SubAgent) 和最佳实践 (Best Practice, 简称 BP) 子 Agent 时，用户消息的传递、隔离与处理机制。

## 1. 核心架构与入口

多 Agent 协作的核心大脑是 `AgentOrchestrator` (`src/seeagent/agents/orchestrator.py`)。它负责接收来自 Agent 的委派请求，并调度特定的子 Agent 来执行。

系统的委派方式主要分为两种路径：
1. **普通子 Agent 委派**：通过工具调用（如 `delegate_to_agent`、`delegate_parallel` 等）。
2. **BP 子 Agent 委派**：由 BP 引擎 (`BPStateManager` 和 `_run_subtask_stream`) 自动编排和调用。

无论是哪种路径，最终都会调用 `orchestrator.delegate()` 方法，其核心签名如下：
```python
async def delegate(
    self,
    session: Any,
    from_agent: str,
    to_agent: str,
    message: str,                  # 传递给子 Agent 的具体任务描述
    depth: int = 0,
    reason: str = "",
    session_messages: list[dict] | None = None,  # 决定上下文隔离程度的关键参数
) -> str:
```

---

## 2. 普通子 Agent 的消息传递机制 (Tool Delegation)

当主 Agent 认为当前任务需要其他领域专家（如 `code-assistant`）的协助时，会调用 `delegate_to_agent` 工具。

### 2.1 消息构造 (Message Construction)
在 `src/seeagent/tools/handlers/agent.py` 的 `_delegate` 方法中，系统会提取工具参数 `message` 和 `reason`，并构造一个针对该任务的“聚焦消息”：
```python
isolated_message = message
if reason:
    isolated_message = f"[委派任务] {message}\n[委派原因] {reason}"
```
这个 `isolated_message` 将作为 `delegate()` 函数的 `message` 参数传入。

### 2.2 会话历史处理 (The History Inheritance)
**关键行为**：在普通委派中，`_delegate` 工具处理器**并没有显式传递** `session_messages` 参数，因此其默认为 `None`。

这导致在 `orchestrator._call_agent` 的最终执行阶段触发了回退逻辑：
```python
if session_messages is None:
    session_messages = session.context.get_messages()
```
**结论**：
- 普通的子 Agent **并没有真正的历史上下文隔离**。
- 它会**继承父会话（MainAgent）的完整对话历史**（`session.context.get_messages()`）。
- `isolated_message` 仅仅是作为本轮对话的最新一条 User 提示词追加到了对话末尾。
- **优点**：子 Agent 可以看到用户之前说过的所有背景信息，避免了信息断层。
- **缺点**：上下文 Token 消耗较大，且如果父会话中包含大量无关讨论，可能会对子 Agent 造成干扰。

---

## 3. 最佳实践 (BP) 子 Agent 的消息传递机制

最佳实践 (BP) 是针对固定标准流程（SOP）的引擎，它的执行由 `src/seeagent/bestpractice/engine/core.py` 驱动。主 Agent 确认开启 BP 后，任务流转交由 BP 引擎接管。

### 3.1 消息构造 (Dynamic Message Build)
BP 子任务的消息并非来自主 Agent 的自然语言生成，而是由系统高度结构化生成的。
在 `_run_subtask_stream` 中，系统调用 `_build_delegation_message` 构造 Prompt：
```python
message = self._build_delegation_message(
    bp_config, subtask, input_data, output_schema, snap=snap,
)
```
这个 `message` 包含了极度明确的：
- `input_data`：从主对话提取出的该步骤所需结构化输入。
- `output_schema`：要求该子 Agent 严格输出的 JSON 结构。
- 任务上下文和执行约束（比如不能生成文件，只能输出 JSON 等）。

### 3.2 会话历史处理 (Strict Context Isolation)
**关键行为**：在调用 Orchestrator 执行 BP 子任务时，BP 引擎进行了**严格的上下文隔离**。
```python
delegate_task = asyncio.create_task(
    orchestrator.delegate(
        session=session,
        from_agent="bp_engine",
        to_agent=subtask.agent_profile,
        message=message,
        reason=f"BP:{bp_config.name} / {subtask.name}",
        session_messages=[],  # <--- 核心差异：强制空历史
    )
)
```
**结论**：
- `session_messages=[]` 覆盖了默认的 `None`。
- BP 子 Agent 启动时，其对话历史是**完全空白**的。
- 它只能看到当前步骤由 `_build_delegation_message` 构造的那一条长 Prompt。
- **优点**：极致节省 Token，防止子 Agent 被主对话中用户的闲聊或前序任务带偏，确保其严格按照 JSON Schema 完成标准动作（这也是为什么 BP 子 Agent 还需要在 Factory 中过滤掉 plan 工具的原因，因为它的环境极度纯粹）。
- **缺点**：子 Agent 对上下文的认知被完全锁定，如果 `_build_delegation_message` 漏传了关键数据，子 Agent 将无法从历史聊天中找补。

---

## 4. 结果处理与返回给主会话

### 4.1 结果记录
在子 Agent (无论是普通还是 BP) 执行完毕后，`Orchestrator._call_agent` 会将结果通过 `_persist_sub_agent_record` 写入父会话。
这样，当主 Agent 重新获得控制权时，它能从上下文中读取到子 Agent 交付的成果。

### 4.2 过程流式透传 (Streaming / SSE)
在 GUI (桌面端) 场景中，用户需要实时看到子 Agent 正在思考和打字。
为了实现这一点：
1. `Orchestrator._call_agent` 会检测是否存在 `_sse_event_bus`。
2. 如果是子 Agent 运行（`is_sub_agent=True`），它会调用 `_call_agent_streaming`。
3. `_call_agent_streaming` 会截获子 Agent 产生的 `text_delta`、`tool_call` 等事件，并向事件总线投递 `agent_header` (声明子 Agent 身份)，将子 Agent 的行为**实时透传**到前端用户界面。
4. **BP 引擎特有处理**：在 `_run_subtask_stream` 中，BP 引擎甚至会劫持 `_sse_event_bus` 到局部的 `event_bus` 队列中进行二次格式化（使用 `BPEventFormatter` 包装成更美观的流程卡片），然后再投递给前端。

---

## 5. 总结对比矩阵

| 维度 | 普通 SubAgent (`delegate_to_agent`) | Best Practice SubAgent (BP Engine) |
| :--- | :--- | :--- |
| **触发方式** | 主 Agent 自主决策调用 Tool | 匹配到 SOP，经用户同意后系统自动驱动 |
| **任务描述来源** | 主 Agent 生成的自然语言 (`message`) | 系统基于 JSON Schema 和输入数据组装 |
| **对话历史 (Context)** | **继承全部历史** (`session.context.messages`) | **完全隔离** (`session_messages=[]`) |
| **状态标识** | `_is_sub_agent_call = True` | `_is_sub_agent_call = True` |
| **前端展示机制** | 原生透传事件，展示思考过程 | 经过 `BPEventFormatter` 包装，展示任务卡片 |
| **工具可用性** | 保留绝大部分常规工具 | 被 `Factory` 严格过滤（如去除 plan 工具） |
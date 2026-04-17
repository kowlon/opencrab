# core 模块

**位置**: `src/seeagent/core/`

**功能描述**: core 模块是整个 Agent 系统的核心，包含了主 Agent 逻辑、大脑（LLM 交互）、Ralph 循环引擎和推理引擎。

## 模块组成

| 文件                    | 行数      | 功能描述                     |
| --------------------- | ------- | ------------------------ |
| `agent.py`            | \~7431  | 主 Agent 类，协调所有模块         |
| `brain.py`            | \~1772  | LLM 交互层，支持流式输出、工具调用、重试机制 |
| `ralph.py`            | \~371   | Ralph Wiggum 循环引擎        |
| `reasoning_engine.py` | \~3777  | ReAct 推理引擎               |
| `identity.py`         | -       | 身份文档加载器                  |
| `context_manager.py`  | \~1200+ | 上下文压缩和管理                 |
| `prompt_assembler.py` | -       | 系统提示词组装                  |
| `tool_executor.py`    | -       | 工具执行器                    |
| `agent_state.py`      | -       | Agent 状态管理               |
| `task_monitor.py`     | -       | 任务监控                     |
| `token_tracking.py`   | -       | Token 使用追踪               |
| `resource_budget.py`  | -       | 资源预算管理                   |
| `supervisor.py`       | -       | 运行时监督                    |
| `proactive.py`        | -       | 主动行为引擎                   |
| `persona.py`          | -       | 人设管理                     |
| `user_profile.py`     | -       | 用户偏好                     |

## 核心类

### 1. Agent

**文件**: `core/agent.py`

主协调器，负责整体任务执行流程。

```python
class Agent:
    def __init__(self, name: str | None = None, api_key: str | None = None)
```

**关键常量**:

| 常量                           | 值      | 说明          |
| ---------------------------- | ------ | ----------- |
| `DEFAULT_MAX_CONTEXT_TOKENS` | 160000 | 默认上下文上限     |
| `CHARS_PER_TOKEN`            | 2      | JSON序列化比率   |
| `MIN_RECENT_TURNS`           | 4      | 最少保留对话轮数    |
| `COMPRESSION_RATIO`          | 0.15   | 目标压缩比率      |
| `CHUNK_MAX_TOKENS`           | 30000  | 压缩块最大token数 |

**关键方法**:

| 方法                          | 签名                                                              | 说明        |
| --------------------------- | --------------------------------------------------------------- | --------- |
| `initialize`                | `async def initialize(start_scheduler=True, lightweight=False)` | 初始化 Agent |
| `_execute_tool_calls_batch` | `async def _execute_tool_calls_batch(tool_calls, ...)`          | 批量执行工具调用  |

### 2. Brain

**文件**: `core/brain.py`

LLM 接口，处理流式响应和工具调用，支持流式累积和 idle 超时检测。

**关键常量**:

| 常量                          | 值     | 说明        |
| --------------------------- | ----- | --------- |
| `_COMPILER_FAIL_THRESHOLD`  | 5     | 编译器熔断失败阈值 |
| `_COMPILER_CIRCUIT_RESET_S` | 300.0 | 编译器熔断重置时间 |

**关键方法**:

| 方法                              | 签名                                                              | 说明            |
| ------------------------------- | --------------------------------------------------------------- | ------------- |
| `messages_create`               | `def messages_create(**kwargs) -> AnthropicMessage`             | 同步LLM调用       |
| `messages_create_async`         | `async def messages_create_async(**kwargs) -> AnthropicMessage` | 异步LLM调用（优先流式） |
| `think`                         | `async def think(prompt, context=None, ...) -> Response`        | 发送推理请求        |
| `_supports_stream_accumulation` | `def _supports_stream_accumulation() -> bool`                   | 检测是否支持流式累积    |

**流式配置**:

| 环境变量                               | 默认值 | 说明                        |
| ---------------------------------- | --- | ------------------------- |
| `SEEAGENT_BRAIN_DISABLE_STREAM`    | -   | 设为 `1/true/yes/on` 禁用流式路径 |
| `SEEAGENT_BRAIN_STREAM_IDLE_LIMIT` | 120 | 流式 chunk 间最大 idle 间隔（秒）   |

### 3. ReasoningEngine

**文件**: `core/reasoning_engine.py`

ReAct 模式实现，显式推理-行动-观察循环。

```python
class ReasoningEngine:
    def __init__(self, brain, tool_executor, context_manager, response_handler, agent_state, memory_manager=None)
```

**关键常量**:

| 常量                           | 值 | 说明         |
| ---------------------------- | - | ---------- |
| `MAX_CHECKPOINTS`            | 5 | 最大检查点数量    |
| `CONSECUTIVE_FAIL_THRESHOLD` | 3 | 连续失败触发回滚阈值 |

**关键方法**:

| 方法                 | 签名                                                           | 说明         |
| ------------------ | ------------------------------------------------------------ | ---------- |
| `run`              | `async def run(messages, tools, system_prompt, ...)`         | 主ReAct推理循环 |
| `_save_checkpoint` | `def _save_checkpoint(messages, state, decision, iteration)` | 保存检查点      |
| `_rollback`        | `def _rollback(reason) -> tuple[list, int]`                  | 回滚到检查点     |

### 4. RalphLoop

**文件**: `core/ralph.py`

永不放弃的执行循环，带状态持久化。

```python
class RalphLoop:
    def __init__(self, max_iterations=100, memory_path=None, on_iteration=None, on_error=None)
```

**关键方法**:

| 方法               | 签名                                              | 说明             |
| ---------------- | ----------------------------------------------- | -------------- |
| `run`            | `async def run(task, execute_fn) -> TaskResult` | 运行Ralph循环      |
| `_load_progress` | `async def _load_progress()`                    | 从MEMORY.md加载进度 |
| `_save_progress` | `async def _save_progress()`                    | 保存进度到MEMORY.md |

**嵌套类型**:

```python
class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"

@dataclass
class Task:
    id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = 0
    max_attempts: int = 10
```

## 模块依赖

```
core/
├── agent.py ──────┬──► brain.py (LLM调用)
                  ├──► tool_executor.py (工具执行)
                  ├──► memory/manager.py (记忆管理)
                  ├──► skills/skill_manager.py (技能管理)
                  └──► reasoning_engine.py (推理引擎)
```

## 相关链接

- 上一页：[架构设计](../02-架构设计/README.md)
- 下一页：[agents 模块](./agents模块.md)


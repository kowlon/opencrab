# Agent 接口

**文件**: `src/seeagent/core/agent.py`

## 类定义

```python
class Agent:
    """SeeAgent 主类 - 协调所有模块"""
    
    def __init__(
        self,
        name: str | None = None,
        api_key: str | None = None,
    ) -> None:
```

## 主要方法

### 初始化

```python
async def initialize(
    self,
    start_scheduler: bool = True,
    lightweight: bool = False,
) -> None:
    """
    初始化 Agent，加载技能、MCP 服务器、记忆会话
    
    Args:
        start_scheduler: 是否启动调度器
        lightweight: 是否轻量模式（跳过某些初始化）
    """
```

### 任务执行

```python
async def run(
    self,
    task: str,
    session_id: str | None = None,
    agent_type: str | None = None,
    **kwargs,
) -> str:
    """
    运行任务
    
    Args:
        task: 任务描述
        session_id: 会话ID（可选）
        agent_type: Agent类型（可选）
    
    Returns:
        任务结果字符串
    """
```

### 任务委派

```python
async def delegate(
    self,
    task: str,
    agent_type: str,
) -> str:
    """
    委派任务给子 Agent
    
    Args:
        task: 任务描述
        agent_type: 子Agent类型
    
    Returns:
        子Agent执行结果
    """
```

### 状态获取

```python
def get_state(self) -> AgentState:
    """
    获取当前Agent状态
    
    Returns:
        AgentState 对象
    """
```

## 常量定义

| 常量 | 类型 | 值 | 说明 |
|------|------|-----|------|
| `DEFAULT_MAX_CONTEXT_TOKENS` | `int` | 160000 | 默认上下文上限 |
| `CHARS_PER_TOKEN` | `int` | 2 | JSON序列化比率 |
| `MIN_RECENT_TURNS` | `int` | 4 | 最少保留对话轮数 |
| `COMPRESSION_RATIO` | `float` | 0.15 | 目标压缩比率 |
| `CHUNK_MAX_TOKENS` | `int` | 30000 | 压缩块最大token数 |
| `LARGE_TOOL_RESULT_THRESHOLD` | `int` | 5000 | 大结果压缩阈值 |
| `STOP_COMMANDS` | `set[str]` | `{"stop", "停"}` | 停止命令 |
| `SKIP_COMMANDS` | `set[str]` | `{"skip", "跳过"}` | 跳过命令 |

## 相关链接

- 上一页：[接口定义](../04-接口定义/README.md)
- 下一页：[Brain 接口](./Brain接口.md)

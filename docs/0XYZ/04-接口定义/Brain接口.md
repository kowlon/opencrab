# Brain 接口

**文件**: `src/seeagent/core/brain.py`

## 类定义

```python
class Brain:
    """Agent大脑 - LLM交互层"""
    
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
```

## 主要方法

### LLM 调用

```python
def messages_create(
    self,
    use_thinking: bool | None = None,
    thinking_depth: str | None = None,
    **kwargs,
) -> AnthropicMessage:
    """
    同步LLM API调用
    
    Returns:
        AnthropicMessage 响应
    """

async def messages_create_async(
    self,
    use_thinking: bool | None = None,
    thinking_depth: str | None = None,
    **kwargs,
) -> AnthropicMessage:
    """
    异步LLM API调用
    
    Returns:
        AnthropicMessage 响应
    """
```

### 推理调用

```python
async def think(
    self,
    prompt: str,
    context: Context | None = None,
    system: str | None = None,
    tools: list[ToolParam] | None = None,
    max_tokens: int | None = None,
    thinking_depth: str | None = None,
) -> Response:
    """
    发送推理请求到LLM
    
    Args:
        prompt: 提示词
        context: 对话上下文
        system: 系统提示词
        tools: 工具列表
        max_tokens: 最大输出token
        thinking_depth: 思考深度
    
    Returns:
        Response 对象
    """
```

### 轻量级推理

```python
async def think_lightweight(
    self,
    prompt: str,
    system: str | None = None,
    max_tokens: int = 2048,
) -> Response:
    """
    轻量级推理（用于记忆提取等）
    使用compiler端点，强制thinking=off
    """
```

### 思考模式

```python
def set_thinking_mode(self, enabled: bool) -> None:
    """启用/禁用思考模式"""

def is_thinking_enabled(self) -> bool:
    """检查思考模式是否启用"""
```

### 端点信息

```python
def get_current_endpoint_info(self) -> dict:
    """
    获取当前端点信息
    
    Returns:
        {"name": str, "model": str, "healthy": bool}
    """
```

## 嵌套类型

```python
@dataclass
class Response:
    content: str                          # 文本内容
    tool_calls: list[dict] = field(default_factory=list)  # 工具调用
    stop_reason: str = ""                 # 停止原因
    usage: dict = field(default_factory=dict)  # 使用统计

@dataclass
class Context:
    """对话上下文"""
    messages: list[MessageParam] = field(default_factory=list)
    system: str = ""
    tools: list[ToolParam] = field(default_factory=list)
```

## 常量定义

| 常量 | 类型 | 值 | 说明 |
|------|------|-----|------|
| `_COMPILER_FAIL_THRESHOLD` | `int` | 5 | 编译器熔断失败阈值 |
| `_COMPILER_CIRCUIT_RESET_S` | `float` | 300.0 | 编译器熔断重置时间（秒） |

## 相关链接

- 上一页：[Agent 接口](./Agent接口.md)
- 下一页：[Memory 接口](./Memory接口.md)

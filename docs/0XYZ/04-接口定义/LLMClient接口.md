# LLMClient 接口

**文件**: `src/seeagent/llm/client.py`

## 类定义

```python
class LLMClient:
    """统一LLM客户端"""
    
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int = 120,
    ) -> None:
```

## 主要方法

### 消息创建

```python
def messages_create(
    self,
    request: LLMRequest,
) -> LLMResponse:
    """
    同步创建消息
    
    Args:
        request: LLMRequest 对象
    
    Returns:
        LLMResponse 对象
    """

def messages_create_stream(
    self,
    request: LLMRequest,
) -> Iterator[LLMResponse]:
    """
    流式创建消息
    
    Args:
        request: LLMRequest 对象
    
    Yields:
        LLMResponse 对象（增量）
    """

async def messages_create_async(
    self,
    request: LLMRequest,
) -> LLMResponse:
    """
    异步创建消息
    
    Args:
        request: LLMRequest 对象
    
    Returns:
        LLMResponse 对象
    """
```

### Provider 管理

```python
def register_provider(
    self,
    name: str,
    provider: LLMProvider,
) -> None:
    """注册LLM Provider"""

def get_provider(self, name: str) -> LLMProvider | None:
    """获取Provider"""

def list_providers(self) -> list[str]:
    """列出所有注册的Provider"""
```

### 配置

```python
def set_default_model(self, model: str) -> None:
    """设置默认模型"""

def get_default_model(self) -> str:
    """获取默认模型"""

def set_max_tokens(self, max_tokens: int) -> None:
    """设置最大输出token"""
```

## 嵌套类型

```python
@dataclass
class LLMRequest:
    model: str                           # 模型名称
    messages: list[Message]              # 消息列表
    temperature: float = 1.0             # 温度参数
    top_p: float | None = None           # Top-p 参数
    max_tokens: int | None = None        # 最大输出token
    tools: list[Tool] | None = None      # 工具列表
    thinking: ThinkingConfig | None = None  # 思考配置
    metadata: dict = field(default_factory=dict)

@dataclass
class LLMResponse:
    id: str                              # 响应ID
    content: str                         # 文本内容
    tool_calls: list[ToolCall] = field(default_factory=list)  # 工具调用
    stop_reason: str                     # 停止原因
    usage: Usage = field(default_factory=Usage)  # 使用统计
    raw: dict = field(default_factory=dict)     # 原始响应

@dataclass
class Message:
    role: str                            # system/user/assistant
    content: str | list[ContentBlock]    # 内容

@dataclass
class ContentBlock:
    type: str                            # text/tool_use/tool_result
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict | None = None
    content: str | None = None

@dataclass
class Tool:
    name: str                            # 工具名称
    description: str                     # 工具描述
    input_schema: dict                   # 输入Schema

@dataclass
class ToolCall:
    id: str                              # 调用ID
    name: str                            # 工具名称
    arguments: dict                      # 参数

@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0

class LLMProvider(ABC):
    """LLM Provider 基类"""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Provider名称"""
    
    @property
    def supported_models(self) -> list[str]:
        """支持的模型列表"""
        return []
    
    @abstractmethod
    async def create(self, request: LLMRequest) -> LLMResponse:
        """创建响应"""
    
    @abstractmethod
    async def create_stream(self, request: LLMRequest) -> Iterator[LLMResponse]:
        """流式创建响应"""
```

## Provider 实现

### AnthropicProvider

**文件**: `llm/providers/anthropic.py`

支持 Claude 系列模型。

### OpenAIProvider

**文件**: `llm/providers/openai.py`

支持 OpenAI 兼容 API（30+ 提供商）。

## 相关链接

- 上一页：[ToolExecutor 接口](./ToolExecutor接口.md)
- 下一页：[数据模型](../05-数据模型/README.md)

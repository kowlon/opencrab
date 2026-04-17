# llm 模块

**位置**: `src/seeagent/llm/`

**功能描述**: 统一的 LLM 客户端，支持流式输出、工具调用和多 Provider fallback。

## 模块组成

| 文件 | 行数 | 功能描述 |
|------|------|----------|
| `client.py` | ~1800+ | 统一 LLM 客户端 |
| `adapter.py` | - | 向后兼容适配器 |
| `config.py` | - | 端点配置加载 |
| `types.py` | - | 请求/响应类型定义 |
| `providers/` | - | Provider 实现 |
| `registries/` | - | Provider 注册表 |

## 支持的 Providers

| Provider | 文件 | 支持模型 |
|----------|------|----------|
| Anthropic | `providers/anthropic.py` | Claude 系列 |
| OpenAI 兼容 | `providers/openai.py` | 30+ 提供商 |

## 核心类

### LLMClient

**文件**: `llm/client.py`

统一 LLM 客户端。

**关键方法**:

| 方法 | 签名 | 说明 |
|------|------|------|
| `messages_create` | `def messages_create(request) -> Response` | 创建消息 |
| `messages_create_stream` | `def messages_create_stream(request) -> Iterator` | 流式创建消息 |
| `register_provider` | `def register_provider(name, provider)` | 注册 Provider |

### 关键类型

```python
@dataclass
class LLMRequest:
    model: str
    messages: list[Message]
    temperature: float = 1.0
    max_tokens: int | None = None
    tools: list[Tool] | None = None

@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall]
    stop_reason: str
    usage: dict

@dataclass
class Message:
    role: str  # system, user, assistant
    content: str | list[ContentBlock]

@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
```

## 模块依赖

```
llm/
├── client.py ──► providers/* (具体Provider)
                └──► core/brain.py (使用LLMClient)
```

## 相关链接

- 上一页：[prompt 模块](./prompt模块.md)
- 下一页：[channels 模块](./channels模块.md)

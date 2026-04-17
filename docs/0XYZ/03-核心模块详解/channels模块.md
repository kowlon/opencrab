# channels 模块

**位置**: `src/seeagent/channels/`

**功能描述**: 支持多种即时通讯渠道的统一消息路由和适配。

## 模块组成

| 文件 | 行数 | 功能描述 |
|------|------|----------|
| `gateway.py` | ~3231 | 统一消息路由 |
| `base.py` | - | ChannelAdapter 接口 |
| `types.py` | - | 消息类型定义 |
| `group_response.py` | - | 群聊响应策略 |
| `adapters/` | - | 平台特定实现 |

## 支持的渠道

| 渠道 | 文件 | 协议 |
|------|------|------|
| Telegram | `adapters/telegram.py` | Telegram Bot API |
| Feishu | `adapters/feishu.py` | 飞书开放平台 |
| DingTalk | `adapters/dingtalk.py` | 钉钉开放平台 |
| WeCom HTTP | `adapters/wework_bot.py` | 企业微信 HTTP 回调 |
| WeCom WebSocket | `adapters/wework_ws.py` | 企业微信 WebSocket |
| OneBot | `adapters/onebot.py` | OneBot 协议 |
| QQ Official | `adapters/qq_official.py` | QQ 官方 Bot |

## 核心类

### MessageGateway

**文件**: `channels/gateway.py`

统一消息网关。

**关键方法**:

| 方法 | 签名 | 说明 |
|------|------|------|
| `route_message` | `async def route_message(message, session_id)` | 路由消息 |
| `handle_interrupt` | `def handle_interrupt()` | 处理中断（CTRL+C） |
| `register_adapter` | `def register_adapter(adapter)` | 注册渠道适配器 |

### ChannelAdapter

**文件**: `channels/base.py`

渠道适配器基类。

```python
class ChannelAdapter(ABC):
    @abstractmethod
    async def connect(self) -> None: ...
    
    @abstractmethod
    async def disconnect(self) -> None: ...
    
    @abstractmethod
    async def send_message(self, message: Message, **kwargs) -> None: ...
    
    @abstractmethod
    async def receive_message(self) -> Message: ...
```

## 消息类型

```python
@dataclass
class Message:
    id: str
    session_id: str
    content: str
    role: str  # user, assistant, system
    channel: str
    timestamp: datetime
    attachments: list[Attachment] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
```

## 模块依赖

```
channels/
├── gateway.py ──┬──► sessions/manager.py
                └──► agents/orchestrator.py
```

## 相关链接

- 上一页：[llm 模块](./llm模块.md)
- 下一页：[api 模块](./api模块.md)

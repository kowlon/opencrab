# ToolExecutor 接口

**文件**: `src/seeagent/core/tool_executor.py`

## 类定义

```python
class ToolExecutor:
    """工具执行器 - 负责调用具体的工具处理器"""
    
    def __init__(
        self,
        tool_catalog: ToolCatalog,
        timeout: int = 60,
        max_retries: int = 3,
    ) -> None:
```

## 主要方法

### 执行工具

```python
async def execute(
    self,
    tool_call: ToolCall,
    timeout: int | None = None,
) -> ToolResult:
    """
    执行单个工具调用
    
    Args:
        tool_call: ToolCall 对象（包含 name, arguments）
        timeout: 超时时间（秒），覆盖默认值
    
    Returns:
        ToolResult 对象
    """

async def execute_batch(
    self,
    tool_calls: list[ToolCall],
    parallel: bool = True,
    timeout: int | None = None,
) -> list[ToolResult]:
    """
    批量执行工具调用
    
    Args:
        tool_calls: ToolCall 列表
        parallel: 是否并行执行
        timeout: 每个工具的超时时间
    
    Returns:
        ToolResult 列表
    """
```

### 工具注册

```python
def register_handler(
    self,
    name: str,
    handler: ToolHandler,
) -> None:
    """注册工具处理器"""

def unregister_handler(self, name: str) -> None:
    """注销工具处理器"""

def get_handler(self, name: str) -> ToolHandler | None:
    """获取工具处理器"""
```

### 工具发现

```python
def list_tools(self) -> list[str]:
    """列出所有可用工具"""

def get_tool_schema(self, name: str) -> dict | None:
    """获取工具的JSON Schema"""
```

## 嵌套类型

```python
@dataclass
class ToolCall:
    id: str                    # 调用ID
    name: str                  # 工具名称
    arguments: dict = field(default_factory=dict)  # 参数
    created_at: datetime = field(default_factory=datetime.now)

@dataclass
class ToolResult:
    id: str                    # 结果ID
    tool_call_id: str          # 对应的调用ID
    success: bool              # 是否成功
    content: str | dict        # 结果内容
    error: str | None = None   # 错误信息（如果失败）
    duration_ms: int = 0       # 执行耗时（毫秒）
    metadata: dict = field(default_factory=dict)

class ToolHandler(ABC):
    """工具处理器基类"""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称"""
    
    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述"""
    
    @property
    @abstractmethod
    def input_schema(self) -> dict:
        """输入JSON Schema"""
    
    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """执行工具"""
    
    async def validate(self, arguments: dict) -> bool:
        """验证参数"""
```

## 内置工具处理器

| 处理器 | 类 | 功能 |
|--------|-----|------|
| BrowserHandler | `tools/handlers/browser.py` | Playwright 浏览器自动化 |
| FilesystemHandler | `tools/handlers/filesystem.py` | 文件读写操作 |
| ShellHandler | `tools/handlers/shell.py` | 命令执行 |
| MemoryHandler | `tools/handlers/memory.py` | 记忆操作 |
| SkillsHandler | `tools/handlers/skills.py` | 技能管理 |
| PlanHandler | `tools/handlers/plan.py` | 规划工具 |
| ScheduledHandler | `tools/handlers/scheduled.py` | 定时任务 |
| IMChannelHandler | `tools/handlers/im_channel.py` | IM渠道操作 |
| AgentHandler | `tools/handlers/agent.py` | 子Agent委派 |
| ConfigHandler | `tools/handlers/config.py` | 配置工具 |
| MCPHandler | `tools/handlers/mcp.py` | MCP工具代理 |
| SystemHandler | `tools/handlers/system.py` | 系统工具 |
| WebSearchHandler | `tools/handlers/web_search.py` | 网页搜索 |

## 相关链接

- 上一页：[Memory 接口](./Memory接口.md)
- 下一页：[LLMClient 接口](./LLMClient接口.md)

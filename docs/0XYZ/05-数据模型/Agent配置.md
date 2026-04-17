# Agent 配置

**文件**: `src/seeagent/agents/profile.py`

## AgentProfile

```python
@dataclass
class AgentProfile:
    """Agent配置数据类"""
    
    id: str                             # 唯一标识符
    name: str                           # 显示名称
    type: str                           # 类型 (default/code_assistant/content_creator...)
    description: str                    # 描述
    system_prompt: str                  # 系统提示词
    tools: list[str] = field(default_factory=list)  # 可用工具列表
    max_depth: int = 5                  # 最大委派深度
    enabled: bool = True                # 是否启用
    is_system: bool = False             # 是否系统预设
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime | None = None
    metadata: dict = field(default_factory=dict)
```

## 预设 Agent 配置

### Default Agent

```python
DEFAULT_PROFILE = AgentProfile(
    id="default",
    name="小秋",
    type="default",
    description="通用助手，可以帮助你完成各种任务",
    system_prompt="你是一个有帮助的AI助手...",
    tools=["*"],  # 所有工具
    max_depth=5,
    is_system=True,
)
```

### Code Assistant

```python
CODE_ASSISTANT_PROFILE = AgentProfile(
    id="code-assistant",
    name="代码助手",
    type="code_assistant",
    description="专门帮助编程的助手",
    system_prompt="你是一个专业的程序员...",
    tools=["read_file", "write_file", "run_shell"],
    max_depth=3,
    is_system=True,
)
```

## ProfileStore

配置文件存储：

```python
class ProfileStore:
    """Agent配置持久化"""
    
    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
    
    async def save(self, profile: AgentProfile) -> None:
        """保存配置"""
    
    async def load(self, profile_id: str) -> AgentProfile | None:
        """加载配置"""
    
    async def list(self) -> list[AgentProfile]:
        """列出所有配置"""
    
    async def delete(self, profile_id: str) -> bool:
        """删除配置"""
    
    async def exists(self, profile_id: str) -> bool:
        """检查配置是否存在"""
```

## 配置存储格式

JSON 文件存储于 `data/profiles/` 目录：

```json
// data/profiles/default.json
{
    "id": "default",
    "name": "小秋",
    "type": "default",
    "description": "通用助手",
    "system_prompt": "...",
    "tools": ["*"],
    "max_depth": 5,
    "enabled": true,
    "is_system": true,
    "created_at": "2024-01-01T00:00:00",
    "updated_at": null,
    "metadata": {}
}
```

## 相关链接

- 上一页：[消息类型](./消息类型.md)
- 下一页：[工具定义](./工具定义.md)

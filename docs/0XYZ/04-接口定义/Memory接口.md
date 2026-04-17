# Memory 接口

**文件**: `src/seeagent/memory/manager.py`

## 类定义

```python
class MemoryManager:
    """记忆管理器 v2 - 三层记忆系统协调器"""
    
    def __init__(
        self,
        data_dir: Path,
        memory_md_path: Path,
        brain=None,
        embedding_model: str | None = None,
        embedding_device: str = "cpu",
        model_download_source: str = "auto",
        search_backend: str = "fts5",
        embedding_api_provider: str = "",
        embedding_api_key: str = "",
        embedding_api_model: str = "",
    ) -> None:
```

## 主要方法

### 会话管理

```python
def start_session(self, session_id: str) -> None:
    """启动新的记忆会话"""

def end_session(
    self,
    task_description: str = "",
    success: bool = True,
    errors: list | None = None,
) -> None:
    """结束会话：生成Episode + 双重提取"""
```

### 记忆操作

```python
def add_memory(
    self,
    memory: Memory,
    scope: str = "global",
    scope_owner: str = "",
) -> str:
    """
    添加记忆
    
    Args:
        memory: Memory 对象
        scope: 作用域 (global/org)
        scope_owner: 作用域所有者
    
    Returns:
        memory_id
    """

def get_memory(self, memory_id: str) -> Memory | None:
    """获取指定记忆"""

def search_memories(
    self,
    query: str = "",
    memory_type: MemoryType | None = None,
    tags: list[str] | None = None,
    limit: int = 10,
    scope: str = "global",
    scope_owner: str = "",
) -> list[Memory]:
    """
    搜索记忆
    
    Args:
        query: 查询字符串
        memory_type: 记忆类型过滤
        tags: 标签过滤
        limit: 返回数量限制
    
    Returns:
        记忆列表
    """

def delete_memory(self, memory_id: str) -> bool:
    """删除记忆"""
```

### 记录对话

```python
def record_turn(
    self,
    role: str,
    content: str,
    tool_calls: list | None = None,
    tool_results: list | None = None,
    attachments: list[dict] | None = None,
) -> None:
    """
    记录对话轮次到 SQLite + JSONL + 异步提取
    """
```

### 整合

```python
async def consolidate_daily(self) -> dict:
    """每日整合（委托给 LifecycleManager）"""

def get_stats(
    self,
    scope: str = "global",
    scope_owner: str = "",
) -> dict:
    """获取记忆统计信息"""
```

### 附件

```python
def record_attachment(
    self,
    filename: str,
    mime_type: str = "",
    local_path: str = "",
    url: str = "",
    description: str = "",
    transcription: str = "",
    extracted_text: str = "",
    tags: list[str] | None = None,
    direction: str = "inbound",
    file_size: int = 0,
    original_filename: str = "",
) -> str:
    """记录文件/媒体附件"""

def search_attachments(
    self,
    query: str = "",
    mime_type: str | None = None,
    direction: str | None = None,
    session_id: str | None = None,
    limit: int = 20,
) -> list[Attachment]:
    """搜索附件"""
```

## 嵌套类型

```python
class MemoryType(Enum):
    SEMANTIC = "semantic"      # 实体-属性结构
    EPISODE = "episode"        # 完整交互故事
    SCRATCHPAD = "scratchpad"  # 跨会话工作记忆

@dataclass
class Memory:
    id: str
    content: str
    memory_type: MemoryType
    created_at: datetime
    scope: str = "global"
    scope_owner: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

@dataclass
class Attachment:
    id: str
    filename: str
    mime_type: str
    direction: str  # inbound/outbound
    session_id: str
    created_at: datetime
```

## 常量定义

| 常量 | 类型 | 值 | 说明 |
|------|------|-----|------|
| `DUPLICATE_DISTANCE_THRESHOLD` | `float` | 0.12 | 向量距离阈值（重复检测） |
| `COMMON_PREFIXES` | `list[str]` | `["重复:", "已记录:", ...]` | 去重前缀 |

## 持续时间映射

```python
_DURATION_MAP = {
    "permanent": None,    # 永久
    "7d": timedelta(days=7),
    "24h": timedelta(hours=24),
    "session": timedelta(hours=2),
}
```

## 相关链接

- 上一页：[Brain 接口](./Brain接口.md)
- 下一页：[ToolExecutor 接口](./ToolExecutor接口.md)

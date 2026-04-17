# memory 模块

**位置**: `src/seeagent/memory/`

**功能描述**: 三层记忆系统，支持语义记忆、情节记忆和工作记忆，提供向量检索和 consolidation。

## 模块组成

| 文件 | 功能描述 |
|------|----------|
| `unified_store.py` | SQLite + SearchBackend 协调 |
| `storage.py` | SQLite 存储（FTS5） |
| `vector_store.py` | 向量存储（嵌入） |
| `extractor.py` | AI 驱动的记忆提取 |
| `consolidator.py` | 记忆整合，支持时间戳前缀和文件迁移 |
| `retrieval.py` | 多路径检索和重排序 |
| `manager.py` | 记忆生命周期管理 |
| `search_backends.py` | 可插拔搜索后端 |
| `types.py` | 记忆类型定义 |

## 三层记忆类型

| 类型 | 说明 |
|------|------|
| `SemanticMemory` | 实体-属性结构化记忆 |
| `Episode` | 完整交互故事/情节 |
| `Scratchpad` | 跨会话持久化工作记忆 |

## 核心类

### MemoryManager

**文件**: `memory/manager.py`

记忆管理器 v2 的核心协调器。

```python
class MemoryManager:
    def __init__(
        self,
        data_dir: Path,
        memory_md_path: Path,
        brain=None,
        embedding_model: str | None = None,
        embedding_device: str = "cpu",
        search_backend: str = "fts5",
        embedding_api_provider: str = "",
        embedding_api_key: str = "",
        embedding_api_model: str = "",
    )
```

**关键常量**:

| 常量 | 值 | 说明 |
|------|-----|------|
| `DUPLICATE_DISTANCE_THRESHOLD` | 0.12 | 向量距离阈值（重复检测） |

**关键方法**:

| 方法 | 签名 | 说明 |
|------|------|------|
| `start_session` | `def start_session(session_id)` | 启动新会话 |
| `record_turn` | `def record_turn(role, content, tool_calls, ...)` | 记录对话轮次 |
| `add_memory` | `def add_memory(memory, scope, scope_owner) -> str` | 添加记忆 |
| `search_memories` | `def search_memories(query, memory_type, tags, limit, ...)` | 搜索记忆 |
| `consolidate_daily` | `async def consolidate_daily() -> dict` | 每日整合 |
| `get_stats` | `def get_stats(scope, scope_owner) -> dict` | 获取统计信息 |

## MemoryConsolidator

**文件**: `memory/consolidator.py`

记忆整合器，支持时间戳前缀和文件迁移。

**关键特性**:

| 特性 | 说明 |
|------|------|
| 时间戳前缀 | 会话文件命名为 `{YYYYMMDDHHMMSS}__{session_id}.jsonl` 格式 |
| 文件迁移 | 自动检测并补充旧格式文件的时间戳前缀 |
| 历史优化 | 会话历史的保存和加载逻辑优化 |

## 搜索后端

| 后端 | 说明 |
|------|------|
| `FTS5Backend` | SQLite FTS5，默认，零依赖 |
| `ChromaDBBackend` | 本地向量搜索 |
| `APIEmbeddingBackend` | 在线 API（DashScope/OpenAI） |

## 模块依赖

```
memory/
├── manager.py ──┬──► unified_store.py
                ├──► vector_store.py
                └──► search_backends.py

retrieval.py ──► storage.py (FTS5)
               └──► prompt/retriever.py (提示词检索)
```

## 相关链接

- 上一页：[tools 模块](./tools模块.md)
- 下一页：[prompt 模块](./prompt模块.md)

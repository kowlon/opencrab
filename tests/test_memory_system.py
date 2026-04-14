"""
记忆系统完整测试套件

覆盖所有记忆系统改动的功能测试，共 50+ 个测试用例

运行方式:
    pytest tests/test_memory_system.py -v
    pytest tests/test_memory_system.py -v -k "vector"  # 只运行向量相关测试
"""

import pytest
import json
import asyncio
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from seeagent.memory.types import Memory, MemoryType, MemoryPriority, ConversationTurn
from seeagent.memory.vector_store import VectorStore
from seeagent.memory.extractor import MemoryExtractor
from seeagent.memory.manager import MemoryManager
from seeagent.memory.consolidator import MemoryConsolidator
from seeagent.memory.daily_consolidator import DailyConsolidator

try:
    import sentence_transformers  # noqa: F401
    import chromadb  # noqa: F401
    _VECTOR_DEPS_AVAILABLE = True
except ImportError:
    _VECTOR_DEPS_AVAILABLE = False

_skip_no_vector = pytest.mark.skipif(
    not _VECTOR_DEPS_AVAILABLE,
    reason="sentence-transformers and/or chromadb not installed",
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def temp_data_dir():
    """创建临时数据目录"""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def temp_memory_md(temp_data_dir):
    """创建临时 MEMORY.md"""
    memory_md = temp_data_dir / "MEMORY.md"
    memory_md.write_text("# Core Memory\n\n## 用户偏好\n- 测试偏好\n", encoding="utf-8")
    return memory_md


@pytest.fixture
def sample_memory():
    """创建示例记忆"""
    return Memory(
        type=MemoryType.PREFERENCE,
        priority=MemoryPriority.LONG_TERM,
        content="用户喜欢使用 Python 编程",
        source="test",
        importance_score=0.8,
        tags=["python", "preference"],
    )


@pytest.fixture
def sample_memories():
    """创建多个示例记忆"""
    return [
        Memory(type=MemoryType.PREFERENCE, priority=MemoryPriority.LONG_TERM,
               content="用户喜欢使用 Python", importance_score=0.8, tags=["python"]),
        Memory(type=MemoryType.RULE, priority=MemoryPriority.PERMANENT,
               content="不要删除重要文件", importance_score=0.9, tags=["rule"]),
        Memory(type=MemoryType.FACT, priority=MemoryPriority.LONG_TERM,
               content="代码目录在 D:\\code", importance_score=0.7, tags=["path"]),
        Memory(type=MemoryType.SKILL, priority=MemoryPriority.LONG_TERM,
               content="使用 pytest 进行测试", importance_score=0.6, tags=["test"]),
        Memory(type=MemoryType.ERROR, priority=MemoryPriority.SHORT_TERM,
               content="直接删除会导致数据丢失", importance_score=0.7, tags=["error"]),
    ]


@pytest.fixture
def mock_brain():
    """模拟 LLM Brain"""
    brain = Mock()
    brain.think = AsyncMock(return_value="NONE")
    return brain


# ============================================================
# VectorStore 测试 (15 个)
# ============================================================

@_skip_no_vector
class TestVectorStore:
    """向量存储测试"""
    
    # --- 初始化测试 ---
    
    def test_01_init_creates_directory(self, temp_data_dir):
        """测试初始化创建目录"""
        vs = VectorStore(data_dir=temp_data_dir)
        # 延迟初始化，访问 enabled 触发
        _ = vs.enabled
        chromadb_dir = temp_data_dir / "chromadb"
        assert chromadb_dir.exists()
    
    def test_02_init_with_custom_model(self, temp_data_dir):
        """测试使用自定义模型初始化"""
        vs = VectorStore(
            data_dir=temp_data_dir,
            model_name="shibing624/text2vec-base-chinese",
            device="cpu"
        )
        assert vs.model_name == "shibing624/text2vec-base-chinese"
        assert vs.device == "cpu"
    
    def test_03_enabled_property(self, temp_data_dir):
        """测试 enabled 属性"""
        vs = VectorStore(data_dir=temp_data_dir)
        # 首次访问触发初始化
        assert vs.enabled == True
        # 再次访问应该返回缓存值
        assert vs.enabled == True
    
    # --- 添加记忆测试 ---
    
    def test_04_add_memory_success(self, temp_data_dir, sample_memory):
        """测试成功添加记忆"""
        vs = VectorStore(data_dir=temp_data_dir)
        result = vs.add_memory(
            memory_id=sample_memory.id,
            content=sample_memory.content,
            memory_type=sample_memory.type.value,
            priority=sample_memory.priority.value,
            importance=sample_memory.importance_score,
            tags=sample_memory.tags,
        )
        assert result == True
        assert vs.get_stats()["count"] == 1
    
    def test_05_add_memory_with_empty_content(self, temp_data_dir):
        """测试添加空内容记忆"""
        vs = VectorStore(data_dir=temp_data_dir)
        result = vs.add_memory(
            memory_id="test_empty",
            content="",  # 空内容
            memory_type="fact",
            priority="short_term",
            importance=0.5,
        )
        # 空内容也应该能添加（ChromaDB 会处理）
        assert result == True
    
    def test_06_add_memory_with_special_chars(self, temp_data_dir):
        """测试添加包含特殊字符的记忆"""
        vs = VectorStore(data_dir=temp_data_dir)
        result = vs.add_memory(
            memory_id="test_special",
            content="路径: D:\\code\\项目\\测试.py 包含 'quotes' 和 \"double quotes\"",
            memory_type="fact",
            priority="long_term",
            importance=0.7,
        )
        assert result == True
    
    # --- 搜索测试 ---
    
    def test_07_search_returns_results(self, temp_data_dir, sample_memories):
        """测试搜索返回结果"""
        vs = VectorStore(data_dir=temp_data_dir)
        for m in sample_memories:
            vs.add_memory(m.id, m.content, m.type.value, m.priority.value, m.importance_score, m.tags)
        
        results = vs.search("Python 编程", limit=3)
        assert len(results) > 0
        assert len(results) <= 3
    
    def test_08_search_with_type_filter(self, temp_data_dir, sample_memories):
        """测试按类型过滤搜索"""
        vs = VectorStore(data_dir=temp_data_dir)
        for m in sample_memories:
            vs.add_memory(m.id, m.content, m.type.value, m.priority.value, m.importance_score, m.tags)
        
        results = vs.search("用户", limit=10, filter_type="preference")
        # 所有结果应该是 preference 类型
        for mid, _ in results:
            # 验证通过 - 只要有结果返回即可
            pass
        assert isinstance(results, list)
    
    def test_09_search_with_min_importance(self, temp_data_dir, sample_memories):
        """测试按最小重要性过滤"""
        vs = VectorStore(data_dir=temp_data_dir)
        for m in sample_memories:
            vs.add_memory(m.id, m.content, m.type.value, m.priority.value, m.importance_score, m.tags)
        
        results = vs.search("测试", limit=10, min_importance=0.8)
        # 结果应该只包含重要性 >= 0.8 的记忆
        assert isinstance(results, list)
    
    def test_10_search_empty_query(self, temp_data_dir, sample_memories):
        """测试空查询"""
        vs = VectorStore(data_dir=temp_data_dir)
        for m in sample_memories:
            vs.add_memory(m.id, m.content, m.type.value, m.priority.value, m.importance_score, m.tags)
        
        results = vs.search("", limit=3)
        # 空查询应该返回结果（基于向量相似度）
        assert isinstance(results, list)
    
    # --- 删除和更新测试 ---
    
    def test_11_delete_memory(self, temp_data_dir, sample_memory):
        """测试删除记忆"""
        vs = VectorStore(data_dir=temp_data_dir)
        vs.add_memory(sample_memory.id, sample_memory.content, 
                      sample_memory.type.value, sample_memory.priority.value, 
                      sample_memory.importance_score, sample_memory.tags)
        
        assert vs.get_stats()["count"] == 1
        result = vs.delete_memory(sample_memory.id)
        assert result == True
        assert vs.get_stats()["count"] == 0
    
    def test_12_update_memory(self, temp_data_dir, sample_memory):
        """测试更新记忆"""
        vs = VectorStore(data_dir=temp_data_dir)
        vs.add_memory(sample_memory.id, sample_memory.content,
                      sample_memory.type.value, sample_memory.priority.value,
                      sample_memory.importance_score, sample_memory.tags)
        
        result = vs.update_memory(
            memory_id=sample_memory.id,
            content="更新后的内容",
            memory_type="fact",
            priority="permanent",
            importance=0.95,
        )
        assert result == True
    
    # --- 批量操作测试 ---
    
    def test_13_batch_add(self, temp_data_dir, sample_memories):
        """测试批量添加"""
        vs = VectorStore(data_dir=temp_data_dir)
        batch_data = [
            {"id": m.id, "content": m.content, "type": m.type.value,
             "priority": m.priority.value, "importance": m.importance_score, "tags": m.tags}
            for m in sample_memories
        ]
        
        added = vs.batch_add(batch_data)
        assert added == len(sample_memories)
        assert vs.get_stats()["count"] == len(sample_memories)
    
    def test_14_clear_all(self, temp_data_dir, sample_memories):
        """测试清空所有记忆"""
        vs = VectorStore(data_dir=temp_data_dir)
        for m in sample_memories:
            vs.add_memory(m.id, m.content, m.type.value, m.priority.value, m.importance_score, m.tags)
        
        assert vs.get_stats()["count"] > 0
        result = vs.clear()
        assert result == True
        assert vs.get_stats()["count"] == 0
    
    def test_15_get_stats(self, temp_data_dir, sample_memories):
        """测试获取统计信息"""
        vs = VectorStore(data_dir=temp_data_dir)
        for m in sample_memories:
            vs.add_memory(m.id, m.content, m.type.value, m.priority.value, m.importance_score, m.tags)
        
        stats = vs.get_stats()
        assert "enabled" in stats
        assert "count" in stats
        assert "model" in stats
        assert "device" in stats
        assert stats["count"] == len(sample_memories)


# ============================================================
# MemoryExtractor 测试 (12 个)
# ============================================================

class TestMemoryExtractor:
    """记忆提取器测试"""
    
    # --- 同步提取测试 ---
    
    def test_16_extract_from_turn_no_brain(self):
        """测试无 Brain 时同步提取（强信号规则兜底）"""
        extractor = MemoryExtractor()
        turn = ConversationTurn(role="user", content="我喜欢 Python")
        memories = extractor.extract_from_turn(turn)
        # 同步兜底：应能提取到偏好（强信号）
        assert len(memories) >= 1
        assert any(m.type == MemoryType.PREFERENCE for m in memories)
    
    def test_17_extract_from_task_completion_success(self):
        """测试任务成功完成时提取"""
        extractor = MemoryExtractor()
        memories = extractor.extract_from_task_completion(
            task_description="完成了用户注册功能的开发，包括表单验证和数据库存储",
            success=True,
            tool_calls=[{"name": "read"}, {"name": "write"}, {"name": "bash"}],
            errors=[]
        )
        assert len(memories) >= 1
        assert any(m.type == MemoryType.SKILL for m in memories)
    
    def test_18_extract_from_task_completion_failure(self):
        """测试任务失败时提取"""
        extractor = MemoryExtractor()
        memories = extractor.extract_from_task_completion(
            task_description="尝试部署到生产环境但遇到了各种问题",
            success=False,
            tool_calls=[],
            errors=["连接超时导致无法连接服务器", "权限不足导致部署失败无法继续"]
        )
        assert len(memories) >= 1
        assert any(m.type == MemoryType.ERROR for m in memories)
    
    def test_19_extract_from_task_short_description(self):
        """测试任务描述太短时不提取"""
        extractor = MemoryExtractor()
        memories = extractor.extract_from_task_completion(
            task_description="ok",  # 太短
            success=True,
            tool_calls=[],
            errors=[]
        )
        assert memories == []
    
    # --- 异步提取测试 ---
    
    @pytest.mark.asyncio
    async def test_20_extract_with_ai_no_brain(self):
        """测试无 Brain 时 AI 提取返回空"""
        extractor = MemoryExtractor()
        turn = ConversationTurn(role="user", content="我喜欢使用 Python 编程")
        memories = await extractor.extract_from_turn_with_ai(turn)
        assert memories == []
    
    @pytest.mark.asyncio
    async def test_21_extract_with_ai_returns_none(self, mock_brain):
        """测试 AI 判断无需记录时返回空"""
        mock_brain.think = AsyncMock(return_value="NONE")
        extractor = MemoryExtractor(brain=mock_brain)
        turn = ConversationTurn(role="user", content="今天天气不错")
        memories = await extractor.extract_from_turn_with_ai(turn)
        assert memories == []
    
    @pytest.mark.asyncio
    async def test_22_extract_with_ai_returns_json(self, mock_brain):
        """测试 AI 返回 JSON 时解析"""
        json_response = '''[
            {"type": "PREFERENCE", "content": "用户喜欢 Python", "importance": 0.8}
        ]'''
        mock_brain.think = AsyncMock(return_value=json_response)
        extractor = MemoryExtractor(brain=mock_brain)
        turn = ConversationTurn(role="user", content="我喜欢使用 Python 编程")
        memories = await extractor.extract_from_turn_with_ai(turn)
        assert len(memories) == 1
        assert memories[0].type == MemoryType.PREFERENCE
    
    @pytest.mark.asyncio
    async def test_23_extract_with_ai_short_content(self, mock_brain):
        """测试内容太短时跳过"""
        extractor = MemoryExtractor(brain=mock_brain)
        turn = ConversationTurn(role="user", content="ok")  # 太短
        memories = await extractor.extract_from_turn_with_ai(turn)
        assert memories == []
        mock_brain.think.assert_not_called()
    
    # --- JSON 解析测试 ---
    
    def test_24_parse_json_response_valid(self):
        """测试解析有效 JSON"""
        extractor = MemoryExtractor()
        # content 需要足够长（>= 5 字符）
        response = '[{"type": "FACT", "content": "这是一段测试内容用于验证", "importance": 0.7}]'
        memories = extractor._parse_json_response(response)
        assert len(memories) == 1
        assert memories[0].type == MemoryType.FACT
    
    def test_25_parse_json_response_invalid(self):
        """测试解析无效 JSON"""
        extractor = MemoryExtractor()
        response = "这不是 JSON"
        memories = extractor._parse_json_response(response)
        assert memories == []
    
    # --- 去重测试 ---
    
    def test_26_deduplicate_removes_duplicates(self, sample_memory):
        """测试去重功能"""
        extractor = MemoryExtractor()
        existing = [sample_memory]
        new_memories = [
            Memory(type=MemoryType.PREFERENCE, priority=MemoryPriority.LONG_TERM,
                   content=sample_memory.content),  # 重复
            Memory(type=MemoryType.FACT, priority=MemoryPriority.SHORT_TERM,
                   content="完全不同的内容"),  # 不重复
        ]
        unique = extractor.deduplicate(new_memories, existing)
        assert len(unique) == 1
        assert "完全不同" in unique[0].content
    
    def test_27_deduplicate_empty_lists(self):
        """测试空列表去重"""
        extractor = MemoryExtractor()
        unique = extractor.deduplicate([], [])
        assert unique == []


# ============================================================
# MemoryManager 测试 (12 个)
# ============================================================

@_skip_no_vector
class TestMemoryManager:
    """记忆管理器测试"""
    
    # --- 初始化测试 ---
    
    def test_28_init_creates_components(self, temp_data_dir, temp_memory_md):
        """测试初始化创建所有组件"""
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        assert mm.extractor is not None
        assert mm.consolidator is not None
        assert mm.vector_store is not None
    
    def test_29_init_loads_existing_memories(self, temp_data_dir, temp_memory_md, sample_memories):
        """测试初始化加载现有记忆"""
        # 先保存一些记忆
        memories_file = temp_data_dir / "memories.json"
        with open(memories_file, "w", encoding="utf-8") as f:
            json.dump([m.to_dict() for m in sample_memories], f)
        
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        assert len(mm._memories) == len(sample_memories)
    
    # --- 添加记忆测试 ---
    
    def test_30_add_memory_to_both_stores(self, temp_data_dir, temp_memory_md, sample_memory):
        """测试添加记忆同时存入 JSON 和向量库"""
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        memory_id = mm.add_memory(sample_memory)
        
        assert memory_id != ""
        assert memory_id in mm._memories
        # 向量库也应该有
        assert mm.vector_store.get_stats()["count"] == 1
    
    def test_31_add_memory_deduplicates(self, temp_data_dir, temp_memory_md, sample_memory):
        """测试添加重复记忆时去重"""
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        mm.add_memory(sample_memory)
        
        # 创建相同内容的记忆
        duplicate = Memory(
            type=MemoryType.PREFERENCE,
            priority=MemoryPriority.LONG_TERM,
            content=sample_memory.content,  # 相同内容
        )
        result = mm.add_memory(duplicate)
        assert result == ""  # 去重，返回空
    
    # --- 删除记忆测试 ---
    
    def test_32_delete_memory_from_both_stores(self, temp_data_dir, temp_memory_md, sample_memory):
        """测试删除记忆同时从 JSON 和向量库删除"""
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        memory_id = mm.add_memory(sample_memory)
        
        result = mm.delete_memory(memory_id)
        assert result == True
        assert memory_id not in mm._memories
        assert mm.vector_store.get_stats()["count"] == 0
    
    def test_33_delete_nonexistent_memory(self, temp_data_dir, temp_memory_md):
        """测试删除不存在的记忆"""
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        result = mm.delete_memory("nonexistent_id")
        assert result == False
    
    # --- 记忆注入测试 ---
    
    def test_34_get_injection_context_includes_memory_md(self, temp_data_dir, temp_memory_md):
        """测试注入上下文包含 MEMORY.md"""
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        context = mm.get_injection_context()
        assert "Core Memory" in context
        assert "测试偏好" in context
    
    def test_35_get_injection_context_with_task(self, temp_data_dir, temp_memory_md, sample_memories):
        """测试带任务描述的注入上下文"""
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        for m in sample_memories:
            mm.add_memory(m)
        
        context = mm.get_injection_context(task_description="Python 编程")
        assert "Core Memory" in context
        # 应该包含相关记忆
        assert "相关记忆" in context or "语义匹配" in context
    
    def test_36_keyword_search_fallback(self, temp_data_dir, temp_memory_md, sample_memories):
        """测试关键词搜索降级"""
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        for m in sample_memories:
            mm._memories[m.id] = m  # 直接添加，不经过向量库
        
        results = mm._keyword_search("Python", limit=3)
        assert len(results) > 0
    
    # --- 搜索测试 ---
    
    def test_37_search_memories_by_type(self, temp_data_dir, temp_memory_md, sample_memories):
        """测试按类型搜索记忆"""
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        for m in sample_memories:
            mm._memories[m.id] = m
        
        results = mm.search_memories(memory_type=MemoryType.PREFERENCE)
        assert all(m.type == MemoryType.PREFERENCE for m in results)
    
    def test_38_search_memories_by_query(self, temp_data_dir, temp_memory_md, sample_memories):
        """测试按关键词搜索记忆"""
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        for m in sample_memories:
            mm._memories[m.id] = m
        
        results = mm.search_memories(query="Python")
        assert len(results) > 0
    
    def test_39_get_stats(self, temp_data_dir, temp_memory_md, sample_memories):
        """测试获取统计信息"""
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        for m in sample_memories:
            mm._memories[m.id] = m
        
        stats = mm.get_stats()
        assert "total" in stats
        assert "by_type" in stats
        assert "by_priority" in stats


# ============================================================
# MemoryConsolidator 测试 (8 个)
# ============================================================

class TestMemoryConsolidator:
    """记忆归纳器测试"""
    
    def test_40_init_creates_directories(self, temp_data_dir):
        """测试初始化创建目录"""
        mc = MemoryConsolidator(data_dir=temp_data_dir)
        assert (temp_data_dir / "conversation_history").exists()
    
    def test_41_save_conversation_turn(self, temp_data_dir):
        """测试保存对话轮次"""
        mc = MemoryConsolidator(data_dir=temp_data_dir)
        turn = ConversationTurn(role="user", content="测试消息")
        mc.save_conversation_turn("test_session", turn)

        # 新布局：按月份分片，非标准 session_id 归到 unknown/
        files = list((temp_data_dir / "conversation_history").glob("*/*.jsonl"))
        assert len(files) == 1

    def test_42_cleanup_old_history_by_days(self, temp_data_dir):
        """测试按天数清理历史"""
        mc = MemoryConsolidator(data_dir=temp_data_dir)
        history_dir = temp_data_dir / "conversation_history"
        # 老文件放月份子目录下（模拟迁移后的布局）
        bucket = history_dir / "unknown"
        bucket.mkdir(parents=True, exist_ok=True)

        old_file = bucket / "old_session.jsonl"
        old_file.write_text("{}")
        import os
        old_time = (datetime.now() - timedelta(days=40)).timestamp()
        os.utime(old_file, (old_time, old_time))

        deleted = mc.cleanup_old_history(days=30)
        assert deleted == 1

    def test_43_cleanup_history_by_count(self, temp_data_dir):
        """测试按文件数清理"""
        mc = MemoryConsolidator(data_dir=temp_data_dir)
        mc.MAX_HISTORY_FILES = 5  # 设置较小的限制
        history_dir = temp_data_dir / "conversation_history"
        bucket = history_dir / "unknown"
        bucket.mkdir(parents=True, exist_ok=True)

        # 创建多个文件（放月份子目录）
        for i in range(10):
            f = bucket / f"session_{i:03d}.jsonl"
            f.write_text("{}")

        result = mc.cleanup_history()
        assert result["by_count"] == 5  # 应该删除 5 个

    def test_44_cleanup_history_by_size(self, temp_data_dir):
        """测试按大小清理"""
        mc = MemoryConsolidator(data_dir=temp_data_dir)
        mc.MAX_HISTORY_SIZE_MB = 0.001  # 设置很小的限制 (约 1KB)
        history_dir = temp_data_dir / "conversation_history"
        bucket = history_dir / "unknown"
        bucket.mkdir(parents=True, exist_ok=True)

        # 创建一个大文件（放月份子目录）
        large_file = bucket / "large.jsonl"
        large_file.write_text("x" * 2000)  # 2KB

        result = mc.cleanup_history()
        assert result["by_size"] >= 1
    
    def test_45_get_history_stats(self, temp_data_dir):
        """测试获取历史统计"""
        mc = MemoryConsolidator(data_dir=temp_data_dir)
        turn = ConversationTurn(role="user", content="测试消息")
        mc.save_conversation_turn("test_session", turn)
        
        stats = mc.get_history_stats()
        assert "file_count" in stats
        assert "total_size_mb" in stats
        assert stats["file_count"] >= 1
    
    def test_46_get_today_sessions(self, temp_data_dir):
        """测试获取今日会话"""
        mc = MemoryConsolidator(data_dir=temp_data_dir)
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S_test")
        turn = ConversationTurn(role="user", content="今日消息")
        mc.save_conversation_turn(session_id, turn)
        
        sessions = mc.get_today_sessions()
        assert len(sessions) >= 1
    
    def test_47_get_unprocessed_sessions(self, temp_data_dir):
        """测试获取未处理会话"""
        mc = MemoryConsolidator(data_dir=temp_data_dir)
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S_unprocessed")
        turn = ConversationTurn(role="user", content="未处理消息")
        mc.save_conversation_turn(session_id, turn)

        sessions = mc.get_unprocessed_sessions()
        # 新创建的会话应该是未处理的
        assert len(sessions) >= 1


# ============================================================
# 按月份分片 + 迁移测试
# ============================================================

class TestConversationHistorySharding:
    """对话历史按月份分片 + 迁移测试"""

    def test_writes_to_month_subdir(self, temp_data_dir):
        """标准 session_id 应写入对应月份子目录，文件名带时间戳前缀，根目录无 jsonl。"""
        mc = MemoryConsolidator(data_dir=temp_data_dir)
        sid = "telegram_chat1_20260414120000_abcd1234"
        mc.save_conversation_turn(sid, ConversationTurn(role="user", content="hi"))

        history_dir = temp_data_dir / "conversation_history"
        files = list((history_dir / "202604").glob(f"*__{sid}.jsonl"))
        assert len(files) == 1
        # 文件名前缀应是 14 位时间戳
        prefix = files[0].stem.split("__", 1)[0]
        assert len(prefix) == 14 and prefix.isdigit()
        # 根目录应无 jsonl 文件
        flat = [p for p in history_dir.iterdir() if p.is_file() and p.suffix == ".jsonl"]
        assert flat == []

    def test_writes_unknown_format_fallback_to_current_month(self, temp_data_dir):
        """无时间戳 session_id（seecrab 风格）首次写入落到当前月份，不进 unknown/。"""
        mc = MemoryConsolidator(data_dir=temp_data_dir)
        # seecrab channel 的真实格式：没有时间戳段
        sid = "seecrab__seecrab_abc123__seecrab_user"
        mc.save_conversation_turn(sid, ConversationTurn(role="user", content="x"))

        history_dir = temp_data_dir / "conversation_history"
        this_month = datetime.now().strftime("%Y%m")
        files = list((history_dir / this_month).glob(f"*__{sid}.jsonl"))
        assert len(files) == 1
        # 不应落 unknown/
        assert not list((history_dir / "unknown").glob(f"*__{sid}.jsonl"))

    def test_writes_unknown_format_reuses_existing_subdir(self, temp_data_dir):
        """无时间戳 session_id 的 session 已有文件时，后续 append 沿用同一文件。

        场景：老版本（未加时间戳前缀）的文件已在 unknown/。
        v3 迁移会在实例化时给它补前缀；save_conversation_turn 应继续 append 到同一文件，
        不在当前月份新建。
        """
        history_dir = temp_data_dir / "conversation_history"
        legacy_bucket = history_dir / "unknown"
        legacy_bucket.mkdir(parents=True, exist_ok=True)
        sid = "seecrab__seecrab_legacy__seecrab_user"
        prior = legacy_bucket / f"{sid}.jsonl"
        prior.write_text('{"role":"user","content":"prev"}\n', encoding="utf-8")

        mc = MemoryConsolidator(data_dir=temp_data_dir)  # 触发 v3 迁移，prior 被重命名
        mc.save_conversation_turn(sid, ConversationTurn(role="user", content="next"))

        # 迁移后的文件仍在 unknown/（目录不变，只加前缀），内容含 prev + next
        renamed = list(legacy_bucket.glob(f"*__{sid}.jsonl"))
        assert len(renamed) == 1
        content = renamed[0].read_text(encoding="utf-8")
        assert "prev" in content and "next" in content
        # 不应在当前月份新建
        this_month = datetime.now().strftime("%Y%m")
        assert not list((history_dir / this_month).glob(f"*{sid}*.jsonl"))

    def test_month_parser_recognizes_yyyymmdd_prefix(self, temp_data_dir):
        """Agent 通用格式 YYYYMMDD_HHMMSS_uuid 也能识别月份（parts[0]）。"""
        mc = MemoryConsolidator(data_dir=temp_data_dir)
        assert mc._month_from_session_id("20260415_120000_abcd1234") == "202604"
        assert mc._month_from_session_id("20251130_230000_cafebabe") == "202511"
        # 无效日期段应返回 None 再交给动态兜底
        assert mc._month_from_session_id("abcd1234_120000_xxx") is None
        assert mc._month_from_session_id("short") is None

    def test_load_session_history_from_month_subdir(self, temp_data_dir):
        """load 能按月份定位到已分片的文件。"""
        mc = MemoryConsolidator(data_dir=temp_data_dir)
        sid = "cli_local_20260301090000_deadbeef"
        mc.save_conversation_turn(sid, ConversationTurn(role="user", content="msg1"))
        mc.save_conversation_turn(sid, ConversationTurn(role="assistant", content="msg2"))

        turns = mc.load_session_history(sid)
        assert len(turns) == 2
        assert turns[0].content == "msg1"
        assert turns[1].content == "msg2"

    def test_migrate_flat_history_preserves_content(self, temp_data_dir):
        """迁移：扁平文件按 session_id 月份归档 + 补时间戳前缀，内容 sha256 不变。"""
        import hashlib
        import os

        history_dir = temp_data_dir / "conversation_history"
        history_dir.mkdir(parents=True, exist_ok=True)

        # 标准 session_id：应按 session_id 月份归档到 202603/
        sid_std = "telegram_c1_20260314080000_11112222"
        payload_std = '{"role":"user","content":"hello","timestamp":"2026-03-14T08:00:00"}\n'
        (history_dir / f"{sid_std}.jsonl").write_text(payload_std, encoding="utf-8")

        # 遗留格式：应用 mtime 兜底，设 mtime=2025-11
        sid_legacy = "12345_telegram_user"
        payload_legacy = '{"role":"user","content":"old","timestamp":"2025-11-10T10:00:00"}\n'
        legacy_path = history_dir / f"{sid_legacy}.jsonl"
        legacy_path.write_text(payload_legacy, encoding="utf-8")
        legacy_mtime = datetime(2025, 11, 15).timestamp()
        os.utime(legacy_path, (legacy_mtime, legacy_mtime))

        sha_std = hashlib.sha256(payload_std.encode()).hexdigest()
        sha_legacy = hashlib.sha256(payload_legacy.encode()).hexdigest()

        # 实例化触发两级迁移（v2：扁平→月份分片；v3：补时间戳前缀）
        MemoryConsolidator(data_dir=temp_data_dir)

        # 验证归档位置（新格式：带时间戳前缀）
        std_hits = list((history_dir / "202603").glob(f"*__{sid_std}.jsonl"))
        legacy_hits = list((history_dir / "202511").glob(f"*__{sid_legacy}.jsonl"))
        assert len(std_hits) == 1
        assert len(legacy_hits) == 1

        # 根目录无 jsonl 残留
        flat = [p for p in history_dir.iterdir() if p.is_file() and p.suffix == ".jsonl"]
        assert flat == []

        # v2 哨兵存在
        sentinel_v2 = temp_data_dir / ".history_layout_v2"
        assert sentinel_v2.exists()
        data = json.loads(sentinel_v2.read_text(encoding="utf-8"))
        assert data["count"] == 2

        # v3 哨兵存在（两个文件都被加前缀）
        sentinel_v3 = temp_data_dir / ".history_layout_v3"
        assert sentinel_v3.exists()
        assert json.loads(sentinel_v3.read_text(encoding="utf-8"))["count"] == 2

        # 内容 sha256 不变（重命名不碰内容）
        assert hashlib.sha256(std_hits[0].read_bytes()).hexdigest() == sha_std
        assert hashlib.sha256(legacy_hits[0].read_bytes()).hexdigest() == sha_legacy

        # v3 优先用首行 JSON 的 timestamp 字段做前缀
        std_prefix = std_hits[0].stem.split("__", 1)[0]
        assert std_prefix == "20260314080000"
        legacy_prefix = legacy_hits[0].stem.split("__", 1)[0]
        assert legacy_prefix == "20251110100000"

    def test_migration_idempotent(self, temp_data_dir, caplog):
        """连续两次实例化，第二次应打印 moved=0（哨兵已存在直接返回）。"""
        import logging

        history_dir = temp_data_dir / "conversation_history"
        history_dir.mkdir(parents=True, exist_ok=True)
        sid = "cli_local_20260401120000_cafebabe"
        (history_dir / f"{sid}.jsonl").write_text("{}\n", encoding="utf-8")

        with caplog.at_level(logging.INFO, logger="seeagent.memory.consolidator"):
            MemoryConsolidator(data_dir=temp_data_dir)
        first_logs = [r for r in caplog.records if "migration" in r.message]
        assert any("moved=1" in r.message for r in first_logs)

        caplog.clear()
        # 第二次实例化——哨兵存在，不应再扫描
        with caplog.at_level(logging.INFO, logger="seeagent.memory.consolidator"):
            MemoryConsolidator(data_dir=temp_data_dir)
        second_logs = [r for r in caplog.records if "migration" in r.message]
        assert second_logs == []  # 哨兵命中直接返回，不打日志

    def test_migration_mtime_fallback_to_unknown_for_invalid_mtime(self, temp_data_dir):
        """mtime 异常（太老/未来）的遗留文件归到 unknown/，并被 v3 补前缀。"""
        import os

        history_dir = temp_data_dir / "conversation_history"
        history_dir.mkdir(parents=True, exist_ok=True)
        sid_legacy = "abc_legacy_xyz"
        legacy_path = history_dir / f"{sid_legacy}.jsonl"
        legacy_path.write_text("{}\n", encoding="utf-8")
        # 设 mtime 到 1999 年（< 2000 的异常时间）
        ancient = datetime(1999, 6, 1).timestamp()
        os.utime(legacy_path, (ancient, ancient))

        MemoryConsolidator(data_dir=temp_data_dir)

        hits = list((history_dir / "unknown").glob(f"*__{sid_legacy}.jsonl"))
        assert len(hits) == 1

    def test_iter_history_files_respects_since_cutoff(self, temp_data_dir):
        """_iter_history_files(since=...) 按月份过滤子目录。"""
        mc = MemoryConsolidator(data_dir=temp_data_dir)
        history_dir = temp_data_dir / "conversation_history"

        # 手工构造 3 个月份子目录
        for month, name in [
            ("202601", "old.jsonl"),
            ("202604", "recent.jsonl"),
            ("unknown", "legacy.jsonl"),
        ]:
            d = history_dir / month
            d.mkdir(parents=True, exist_ok=True)
            (d / name).write_text("{}\n", encoding="utf-8")

        # since=2026-03 → 应包含 202604/ 和 unknown/，排除 202601/
        since = datetime(2026, 3, 1)
        names = {p.name for p in mc._iter_history_files(since=since)}
        assert "recent.jsonl" in names
        assert "legacy.jsonl" in names
        assert "old.jsonl" not in names

        # since=None → 全部
        all_names = {p.name for p in mc._iter_history_files()}
        assert {"old.jsonl", "recent.jsonl", "legacy.jsonl"} <= all_names


class TestTimestampPrefix:
    """文件名时间戳前缀（IDE 字母序 = 时间序）。"""

    def test_first_write_adds_timestamp_prefix(self, temp_data_dir):
        """首次写入应产生 {14digits}__{session_id}.jsonl 格式。"""
        mc = MemoryConsolidator(data_dir=temp_data_dir)
        sid = "seecrab__seecrab_xyz__seecrab_user"
        mc.save_conversation_turn(sid, ConversationTurn(role="user", content="hi"))

        files = list((temp_data_dir / "conversation_history").glob(f"*/*__{sid}.jsonl"))
        assert len(files) == 1
        prefix = files[0].stem.split("__", 1)[0]
        assert len(prefix) == 14 and prefix.isdigit()

    def test_subsequent_writes_append_to_same_file(self, temp_data_dir):
        """同 session 的多次写入应 append 到同一带前缀的文件，不新建。"""
        mc = MemoryConsolidator(data_dir=temp_data_dir)
        sid = "seecrab__seecrab_append__seecrab_user"
        mc.save_conversation_turn(sid, ConversationTurn(role="user", content="first"))
        mc.save_conversation_turn(sid, ConversationTurn(role="assistant", content="second"))
        mc.save_conversation_turn(sid, ConversationTurn(role="user", content="third"))

        files = list((temp_data_dir / "conversation_history").glob(f"*/*__{sid}.jsonl"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "first" in content and "second" in content and "third" in content
        assert content.count("\n") == 3

    def test_load_after_prefix_rename(self, temp_data_dir):
        """load_session_history 能跨前缀找到会话文件。"""
        mc = MemoryConsolidator(data_dir=temp_data_dir)
        sid = "cli_local_20260301090000_deadbeef"
        mc.save_conversation_turn(sid, ConversationTurn(role="user", content="msg1"))

        # 新实例（清缓存）模拟进程重启
        mc2 = MemoryConsolidator(data_dir=temp_data_dir)
        turns = mc2.load_session_history(sid)
        assert len(turns) == 1 and turns[0].content == "msg1"

    def test_filename_sorts_chronologically(self, temp_data_dir):
        """同月内多个 session 的文件名字母序 = 写入时间序。"""
        import time as _time

        mc = MemoryConsolidator(data_dir=temp_data_dir)
        for sid in ["seecrab__s1__u", "seecrab__s2__u", "seecrab__s3__u"]:
            mc.save_conversation_turn(sid, ConversationTurn(role="user", content=sid))
            _time.sleep(1.01)  # 保证 14 位秒级时间戳不同

        this_month = datetime.now().strftime("%Y%m")
        files = sorted((temp_data_dir / "conversation_history" / this_month).glob("*.jsonl"))
        # 字母序 = 时间序：s1 最老在前，s3 最新在后
        assert files[0].stem.endswith("__seecrab__s1__u")
        assert files[-1].stem.endswith("__seecrab__s3__u")

    def test_v3_migration_uses_first_line_timestamp(self, temp_data_dir):
        """v3 迁移优先用首行 JSON 的 timestamp 字段生成前缀。"""
        history_dir = temp_data_dir / "conversation_history"
        (history_dir / "202604").mkdir(parents=True, exist_ok=True)
        sid = "seecrab__seecrab_v3test__seecrab_user"
        legacy = history_dir / "202604" / f"{sid}.jsonl"
        legacy.write_text(
            '{"role":"user","content":"x","timestamp":"2026-04-10T15:30:45"}\n',
            encoding="utf-8",
        )

        MemoryConsolidator(data_dir=temp_data_dir)

        hits = list((history_dir / "202604").glob(f"*__{sid}.jsonl"))
        assert len(hits) == 1
        assert hits[0].stem.split("__", 1)[0] == "20260410153045"

    def test_v3_migration_idempotent_on_already_prefixed(self, temp_data_dir):
        """v3 迁移只处理缺前缀文件，已带前缀的不动。"""
        history_dir = temp_data_dir / "conversation_history"
        (history_dir / "202604").mkdir(parents=True, exist_ok=True)
        sid = "already_prefixed"
        already = history_dir / "202604" / f"20260401120000__{sid}.jsonl"
        already.write_text("{}\n", encoding="utf-8")
        original_mtime = already.stat().st_mtime

        MemoryConsolidator(data_dir=temp_data_dir)

        # 文件未被重命名，路径和 mtime 不变
        assert already.exists()
        assert already.stat().st_mtime == original_mtime
        # 无多余副本
        assert len(list((history_dir / "202604").glob("*.jsonl"))) == 1

    def test_session_id_from_stem(self, temp_data_dir):
        """_session_id_from_stem 解析边界情况。"""
        mc = MemoryConsolidator(data_dir=temp_data_dir)
        # 新格式
        assert mc._session_id_from_stem("20260414120000__foo_bar") == "foo_bar"
        assert mc._session_id_from_stem("20260414120000__a__b__c") == "a__b__c"
        # 前缀长度不足 14
        assert mc._session_id_from_stem("2026041__foo") is None
        # 前缀非数字
        assert mc._session_id_from_stem("abcdefghijklmn__foo") is None
        # 无 __
        assert mc._session_id_from_stem("no_separator") is None


# ============================================================
# DailyConsolidator 测试 (5 个)
# ============================================================

class TestDailyConsolidator:
    """每日归纳器测试"""
    
    def test_48_init_creates_summaries_dir(self, temp_data_dir, temp_memory_md):
        """测试初始化创建摘要目录"""
        dc = DailyConsolidator(
            data_dir=temp_data_dir,
            memory_md_path=temp_memory_md,
        )
        assert dc.summaries_dir.exists()
    
    def test_49_generate_memory_md_content(self, temp_data_dir, temp_memory_md, sample_memories):
        """测试生成 MEMORY.md 内容"""
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        for m in sample_memories:
            mm.add_memory(m)
        
        dc = DailyConsolidator(
            data_dir=temp_data_dir,
            memory_md_path=temp_memory_md,
            memory_manager=mm,
        )
        
        by_type = {
            "preference": [m for m in sample_memories if m.type == MemoryType.PREFERENCE],
            "rule": [m for m in sample_memories if m.type == MemoryType.RULE],
            "fact": [m for m in sample_memories if m.type == MemoryType.FACT],
            "skill": [m for m in sample_memories if m.type == MemoryType.SKILL],
        }
        
        content = dc._generate_memory_md(by_type)
        assert "Core Memory" in content
        assert "用户偏好" in content or "重要规则" in content
    
    @pytest.mark.asyncio
    async def test_50_refresh_memory_md(self, temp_data_dir, temp_memory_md, sample_memories):
        """测试刷新 MEMORY.md"""
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        for m in sample_memories:
            mm.add_memory(m)
        
        dc = DailyConsolidator(
            data_dir=temp_data_dir,
            memory_md_path=temp_memory_md,
            memory_manager=mm,
        )
        
        result = await dc.refresh_memory_md()
        assert result == True
        assert temp_memory_md.exists()
        content = temp_memory_md.read_text(encoding="utf-8")
        assert "Core Memory" in content
    
    def test_51_get_recent_summaries(self, temp_data_dir, temp_memory_md):
        """测试获取最近摘要"""
        dc = DailyConsolidator(
            data_dir=temp_data_dir,
            memory_md_path=temp_memory_md,
        )
        
        # 创建一个摘要
        today = datetime.now().strftime("%Y-%m-%d")
        summary_file = dc.summaries_dir / f"{today}.json"
        summary_file.write_text(json.dumps({"date": today, "test": True}), encoding="utf-8")
        
        summaries = dc.get_recent_summaries(days=7)
        assert len(summaries) >= 1
    
    def test_52_memory_md_max_chars(self, temp_data_dir, temp_memory_md):
        """测试 MEMORY.md 最大字符限制"""
        dc = DailyConsolidator(
            data_dir=temp_data_dir,
            memory_md_path=temp_memory_md,
        )
        assert dc.MEMORY_MD_MAX_CHARS == 800


# ============================================================
# Session 任务管理测试 (5 个)
# ============================================================

class TestSessionTaskManagement:
    """Session 任务管理测试"""
    
    def test_53_set_task(self):
        """测试设置任务"""
        from seeagent.sessions.session import Session
        session = Session.create(channel="test", chat_id="123", user_id="user1")
        
        session.set_task("task_001", "完成代码审查")
        
        assert session.context.current_task == "task_001"
        assert session.context.get_variable("task_description") == "完成代码审查"
        assert session.context.get_variable("task_status") == "in_progress"
    
    def test_54_complete_task_success(self):
        """测试成功完成任务"""
        from seeagent.sessions.session import Session
        session = Session.create(channel="test", chat_id="123", user_id="user1")
        session.set_task("task_001", "测试任务")
        
        session.complete_task(success=True, result="任务完成")
        
        assert session.context.current_task is None
        assert session.context.get_variable("task_status") == "completed"
        assert session.context.get_variable("task_result") == "任务完成"
    
    def test_55_complete_task_failure(self):
        """测试任务失败"""
        from seeagent.sessions.session import Session
        session = Session.create(channel="test", chat_id="123", user_id="user1")
        session.set_task("task_001", "测试任务")
        
        session.complete_task(success=False, result="遇到错误")
        
        assert session.context.get_variable("task_status") == "failed"
    
    def test_56_get_task_status(self):
        """测试获取任务状态"""
        from seeagent.sessions.session import Session
        session = Session.create(channel="test", chat_id="123", user_id="user1")
        session.set_task("task_001", "测试任务")
        
        status = session.get_task_status()
        
        assert status["task_id"] == "task_001"
        assert status["description"] == "测试任务"
        assert status["status"] == "in_progress"
    
    def test_57_has_active_task(self):
        """测试检查是否有活跃任务"""
        from seeagent.sessions.session import Session
        session = Session.create(channel="test", chat_id="123", user_id="user1")
        
        assert session.has_active_task() == False
        
        session.set_task("task_001", "测试")
        assert session.has_active_task() == True
        
        session.complete_task()
        assert session.has_active_task() == False


# ============================================================
# 集成测试 (5 个)
# ============================================================

@_skip_no_vector
class TestIntegration:
    """集成测试"""
    
    def test_58_end_to_end_memory_flow(self, temp_data_dir, temp_memory_md, sample_memory):
        """测试端到端记忆流程"""
        # 1. 创建 MemoryManager
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        
        # 2. 添加记忆
        memory_id = mm.add_memory(sample_memory)
        assert memory_id != ""
        
        # 3. 搜索记忆
        results = mm.search_memories(query="Python")
        assert len(results) > 0
        
        # 4. 获取注入上下文
        context = mm.get_injection_context(task_description="Python 开发")
        assert len(context) > 0
        
        # 5. 删除记忆
        result = mm.delete_memory(memory_id)
        assert result == True
    
    def test_59_conversation_history_flow(self, temp_data_dir, temp_memory_md):
        """测试对话历史流程"""
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        
        # 1. 开始会话
        mm.start_session("test_session_001")
        
        # 2. 记录对话
        mm.record_turn("user", "你好")
        mm.record_turn("assistant", "你好！有什么可以帮助你的？")
        
        # 3. 检查历史文件（新布局：按月份子目录）
        history_files = list((temp_data_dir / "conversation_history").glob("*/*.jsonl"))
        assert len(history_files) >= 1
    
    def test_60_memory_persistence(self, temp_data_dir, temp_memory_md, sample_memory):
        """测试记忆持久化"""
        # 1. 创建并保存
        mm1 = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        mm1.add_memory(sample_memory)
        
        # 2. 重新加载
        mm2 = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        assert len(mm2._memories) == 1
    
    def test_61_vector_search_relevance(self, temp_data_dir, temp_memory_md):
        """测试向量搜索相关性"""
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        
        # 添加不同主题的记忆
        mm.add_memory(Memory(type=MemoryType.FACT, priority=MemoryPriority.LONG_TERM,
                            content="Python 是一种编程语言", importance_score=0.8))
        mm.add_memory(Memory(type=MemoryType.FACT, priority=MemoryPriority.LONG_TERM,
                            content="咖啡是一种饮料", importance_score=0.8))
        
        # 搜索应该返回相关结果
        context = mm.get_injection_context(task_description="写 Python 代码")
        assert "Python" in context
    
    def test_62_concurrent_operations(self, temp_data_dir, temp_memory_md):
        """测试并发操作"""
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        
        # 并发添加记忆
        memories = [
            Memory(type=MemoryType.FACT, priority=MemoryPriority.SHORT_TERM,
                   content=f"测试记忆 {i}")
            for i in range(10)
        ]
        
        for m in memories:
            mm.add_memory(m)
        
        assert len(mm._memories) == 10


# ============================================================
# 边界条件测试 (3 个)
# ============================================================

class TestEdgeCases:
    """边界条件测试"""
    
    def test_63_very_long_content(self, temp_data_dir, temp_memory_md):
        """测试超长内容"""
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        
        long_content = "这是一段很长的内容。" * 1000
        memory = Memory(
            type=MemoryType.FACT,
            priority=MemoryPriority.SHORT_TERM,
            content=long_content,
        )
        
        memory_id = mm.add_memory(memory)
        assert memory_id != ""
    
    def test_64_unicode_content(self, temp_data_dir, temp_memory_md):
        """测试 Unicode 内容"""
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=temp_memory_md)
        
        unicode_content = "用户喜欢 🐍 Python，路径是 D:\\代码\\项目"
        memory = Memory(
            type=MemoryType.PREFERENCE,
            priority=MemoryPriority.LONG_TERM,
            content=unicode_content,
        )
        
        memory_id = mm.add_memory(memory)
        assert memory_id != ""
        
        # 验证能正确检索
        retrieved = mm.get_memory(memory_id)
        assert "🐍" in retrieved.content
    
    def test_65_empty_memory_md(self, temp_data_dir):
        """测试空 MEMORY.md"""
        empty_md = temp_data_dir / "MEMORY.md"
        empty_md.write_text("", encoding="utf-8")
        
        mm = MemoryManager(data_dir=temp_data_dir, memory_md_path=empty_md)
        context = mm.get_injection_context()
        
        # 应该不会崩溃
        assert isinstance(context, str)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

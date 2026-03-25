"""Tests for new BP tool handlers: bp_next, bp_answer, bp_cancel."""
import asyncio
import pytest

from seeagent.bestpractice.config import BestPracticeConfig
from seeagent.bestpractice.context_bridge import ContextBridge
from seeagent.bestpractice.engine import BPEngine
from seeagent.bestpractice.handler import BP_TOOLS, BPToolHandler
from seeagent.bestpractice.models import RunMode, SubtaskConfig, SubtaskStatus
from seeagent.bestpractice.state_manager import BPStateManager


class MockEventBus:
    def __init__(self):
        self.events = []
    async def put(self, event):
        self.events.append(event)


class MockSession:
    def __init__(self):
        self.id = "test-session"
        self.metadata = {}
        class Ctx:
            _sse_event_bus = None
        self.context = Ctx()


class MockAgent:
    def __init__(self):
        self._current_session = MockSession()
        self._current_session.context._sse_event_bus = MockEventBus()


@pytest.fixture
def bp_config():
    return BestPracticeConfig(
        id="test-bp", name="Test BP", description="desc",
        subtasks=[
            SubtaskConfig(
                id="s1", name="Step 1", agent_profile="agent-a",
                input_schema={
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            ),
            SubtaskConfig(id="s2", name="Step 2", agent_profile="agent-b"),
        ],
    )


@pytest.fixture
def handler(bp_config):
    state_mgr = BPStateManager()
    engine = BPEngine(state_manager=state_mgr)
    bridge = ContextBridge(state_manager=state_mgr)
    return BPToolHandler(
        engine=engine, state_manager=state_mgr,
        context_bridge=bridge, config_registry={bp_config.id: bp_config},
    )


class TestBPToolsList:
    def test_all_tools_registered(self):
        assert "bp_next" in BP_TOOLS
        assert "bp_answer" in BP_TOOLS
        assert "bp_cancel" in BP_TOOLS


class TestBPNext:
    @pytest.mark.asyncio
    async def test_next_no_active_instance(self, handler):
        agent = MockAgent()
        result = await handler.handle("bp_next", {}, agent)
        assert "没有活跃" in result

    @pytest.mark.asyncio
    async def test_next_nonexistent_instance(self, handler):
        agent = MockAgent()
        result = await handler.handle("bp_next", {"instance_id": "fake"}, agent)
        assert "不存在" in result


class TestBPAnswer:
    @pytest.mark.asyncio
    async def test_answer_missing_params(self, handler):
        agent = MockAgent()
        result = await handler.handle("bp_answer", {}, agent)
        assert "需要" in result or "required" in result.lower()

    @pytest.mark.asyncio
    async def test_answer_missing_data(self, handler):
        agent = MockAgent()
        result = await handler.handle("bp_answer", {"subtask_id": "s1"}, agent)
        assert "需要" in result or "data" in result.lower()


class TestBPCancel:
    @pytest.mark.asyncio
    async def test_cancel_no_active_instance(self, handler):
        agent = MockAgent()
        result = await handler.handle("bp_cancel", {}, agent)
        assert "没有活跃" in result

    @pytest.mark.asyncio
    async def test_cancel_active_instance(self, handler):
        agent = MockAgent()
        await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "x"}}, agent)
        active = handler.state_manager.get_active("test-session")
        assert active is not None
        result = await handler.handle("bp_cancel", {}, agent)
        assert "已取消" in result
        snap = handler.state_manager.get(active.instance_id)
        assert snap.status.value == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_sets_cooldown(self, handler):
        agent = MockAgent()
        await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "x"}}, agent)
        await handler.handle("bp_cancel", {}, agent)
        cooldown = handler.state_manager.get_cooldown("test-session")
        assert cooldown > 0

    @pytest.mark.asyncio
    async def test_cancel_emits_bp_cancelled_event(self, handler):
        agent = MockAgent()
        await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "x"}}, agent)
        active = handler.state_manager.get_active("test-session")
        bus = agent._current_session.context._sse_event_bus
        await handler.handle("bp_cancel", {}, agent)
        cancelled_events = [e for e in bus.events if e.get("type") == "bp_cancelled"]
        assert len(cancelled_events) == 1
        assert cancelled_events[0]["instance_id"] == active.instance_id
        assert cancelled_events[0]["bp_name"] == "Test BP"

    @pytest.mark.asyncio
    async def test_cancel_persists_to_session(self, handler):
        agent = MockAgent()
        await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "x"}}, agent)
        await handler.handle("bp_cancel", {}, agent)
        bp_state = agent._current_session.metadata.get("bp_state")
        assert bp_state is not None


class TestPersistToSession:
    @pytest.mark.asyncio
    async def test_persist_writes_metadata(self, handler):
        agent = MockAgent()
        await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "x"}}, agent)
        active = handler.state_manager.get_active("test-session")
        handler.state_manager.persist_to_session(active.instance_id, agent._current_session)
        assert "bp_state" in agent._current_session.metadata

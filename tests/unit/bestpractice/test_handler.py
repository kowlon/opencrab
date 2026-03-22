"""BPToolHandler tests."""

import json

import pytest

from seeagent.bestpractice.config import BestPracticeConfig
from seeagent.bestpractice.context_bridge import ContextBridge
from seeagent.bestpractice.engine import BPEngine
from seeagent.bestpractice.handler import BPToolHandler
from seeagent.bestpractice.models import RunMode, SubtaskConfig, SubtaskStatus
from seeagent.bestpractice.state_manager import BPStateManager


class MockOrchestrator:
    async def delegate(self, **kw):
        return json.dumps({"result": "ok"})


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
        self._orchestrator = MockOrchestrator()


@pytest.fixture
def bp_config():
    return BestPracticeConfig(
        id="test-bp", name="Test", description="desc",
        subtasks=[
            SubtaskConfig(
                id="s1", name="S1", agent_profile="agent-a",
                input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            ),
            SubtaskConfig(id="s2", name="S2", agent_profile="agent-b"),
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


# ── bp_start ───────────────────────────────────────────────────


class TestBPStart:
    @pytest.mark.asyncio
    async def test_start_creates_instance(self, handler):
        agent = MockAgent()
        result = await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "hello"}}, agent)
        assert "已创建" in result or "instance" in result.lower()

    @pytest.mark.asyncio
    async def test_start_unknown_bp(self, handler):
        agent = MockAgent()
        result = await handler.handle("bp_start", {"bp_id": "nonexistent"}, agent)
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_start_missing_bp_id(self, handler):
        agent = MockAgent()
        result = await handler.handle("bp_start", {}, agent)
        assert "required" in result

    @pytest.mark.asyncio
    async def test_start_with_auto_mode(self, handler):
        agent = MockAgent()
        result = await handler.handle(
            "bp_start", {"bp_id": "test-bp", "run_mode": "auto", "input_data": {"q": "x"}}, agent,
        )
        assert "已创建" in result or "instance" in result.lower()


# ── bp_edit_output ─────────────────────────────────────────────


class TestBPEditOutput:
    @pytest.mark.asyncio
    async def test_edit_output(self, handler):
        agent = MockAgent()
        await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "x"}}, agent)
        active = handler.state_manager.get_active("test-session")
        # Manually populate subtask output (bp_start no longer executes subtasks)
        handler.state_manager.update_subtask_output(
            active.instance_id, "s1", {"result": "original"}
        )
        handler.state_manager.update_subtask_status(
            active.instance_id, "s1", SubtaskStatus.DONE
        )
        result = await handler.handle("bp_edit_output", {
            "instance_id": active.instance_id,
            "subtask_id": "s1",
            "changes": {"result": "modified"},
        }, agent)
        assert "合并" in result or "✅" in result

    @pytest.mark.asyncio
    async def test_edit_missing_subtask_id(self, handler):
        agent = MockAgent()
        result = await handler.handle("bp_edit_output", {"instance_id": "x"}, agent)
        assert "required" in result


# ── bp_switch_task ─────────────────────────────────────────────


class TestBPSwitchTask:
    @pytest.mark.asyncio
    async def test_switch_task(self, handler):
        agent = MockAgent()
        await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "1"}}, agent)
        first_id = handler.state_manager.get_active("test-session").instance_id
        await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "2"}}, agent)
        second_id = handler.state_manager.get_active("test-session").instance_id

        result = await handler.handle("bp_switch_task", {"target_instance_id": first_id}, agent)
        assert "切换" in result or first_id in result

    @pytest.mark.asyncio
    async def test_switch_to_same_task(self, handler):
        agent = MockAgent()
        await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "1"}}, agent)
        active_id = handler.state_manager.get_active("test-session").instance_id
        result = await handler.handle("bp_switch_task", {"target_instance_id": active_id}, agent)
        assert "已经是" in result


# ── Unknown tool ───────────────────────────────────────────────


class TestUnknown:
    @pytest.mark.asyncio
    async def test_unknown_tool(self, handler):
        agent = MockAgent()
        result = await handler.handle("bp_unknown", {}, agent)
        assert "Unknown" in result


# ── No session ─────────────────────────────────────────────────


class TestNoSession:
    @pytest.mark.asyncio
    async def test_no_session(self, handler):
        class NoSessionAgent:
            _current_session = None
        result = await handler.handle("bp_start", {"bp_id": "test-bp"}, NoSessionAgent())
        assert "无活跃会话" in result

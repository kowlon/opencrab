"""BPToolHandler tests."""

import json

import pytest

from seeagent.bestpractice.config import BestPracticeConfig
from seeagent.bestpractice.engine import ContextBridge
from seeagent.bestpractice.engine import BPEngine
from seeagent.bestpractice.handler import BPToolHandler
from seeagent.bestpractice.models import RunMode, SubtaskConfig, SubtaskStatus
from seeagent.bestpractice.engine import BPStateManager


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
def bp_config2():
    return BestPracticeConfig(
        id="test-bp-2", name="Test2", description="desc2",
        subtasks=[
            SubtaskConfig(
                id="s1", name="S1", agent_profile="agent-a",
                input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            ),
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


@pytest.fixture
def handler_multi(bp_config, bp_config2):
    state_mgr = BPStateManager()
    engine = BPEngine(state_manager=state_mgr)
    bridge = ContextBridge(state_manager=state_mgr)
    return BPToolHandler(
        engine=engine, state_manager=state_mgr,
        context_bridge=bridge,
        config_registry={bp_config.id: bp_config, bp_config2.id: bp_config2},
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
    async def test_edit_input(self, handler):
        agent = MockAgent()
        await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "x"}}, agent)
        active = handler.state_manager.get_active("test-session")
        active.current_subtask_index = 1
        handler.state_manager.update_subtask_output(active.instance_id, "s1", {"result": "original"})
        handler.state_manager.update_subtask_status(active.instance_id, "s1", SubtaskStatus.DONE)

        result = await handler.handle("bp_edit_output", {
            "instance_id": active.instance_id,
            "subtask_id": "s2",
            "target_type": "input",
            "changes": {"result": "updated"},
        }, agent)

        assert "输入已更新" in result
        assert "重新执行" in result

    @pytest.mark.asyncio
    async def test_edit_final_output_without_subtask_id(self, handler):
        agent = MockAgent()
        await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "x"}}, agent)
        active = handler.state_manager.get_active("test-session")
        handler.state_manager.update_subtask_output(active.instance_id, "s2", {"result": "original"})
        handler.state_manager.update_subtask_status(active.instance_id, "s2", SubtaskStatus.DONE)

        result = await handler.handle("bp_edit_output", {
            "instance_id": active.instance_id,
            "target_type": "final_output",
            "changes": {"result": "final"},
        }, agent)

        assert "最终输出已合并更新" in result

    @pytest.mark.asyncio
    async def test_edit_missing_subtask_id(self, handler):
        agent = MockAgent()
        result = await handler.handle("bp_edit_output", {"instance_id": "x"}, agent)
        assert "required" in result

    @pytest.mark.asyncio
    async def test_edit_completed_reactivates_to_active(self, handler, bp_config):
        """COMPLETED 实例编辑后应被重激活为 ACTIVE，使后续 get_active() 能找到它。"""
        from seeagent.bestpractice.models import BPStatus
        agent = MockAgent()
        session = agent._current_session
        inst_id = handler.state_manager.create_instance(bp_config, session.id, {"q": "x"})
        for st in bp_config.subtasks:
            handler.state_manager.update_subtask_status(inst_id, st.id, SubtaskStatus.DONE)
            handler.state_manager.update_subtask_output(inst_id, st.id, {f"o_{st.id}": 1})
        snap = handler.state_manager.get(inst_id)
        snap.current_subtask_index = len(bp_config.subtasks)
        handler.state_manager.complete(inst_id)

        result = await handler.handle("bp_edit_output", {
            "instance_id": inst_id,
            "subtask_id": "s1",
            "changes": {"o_s1": 99},
        }, agent)

        assert "✅" in result
        snap = handler.state_manager.get(inst_id)
        assert snap.status == BPStatus.ACTIVE
        assert snap.completed_at is None
        # get_active() 应能找到重激活后的实例
        active = handler.state_manager.get_active(session.id)
        assert active is not None
        assert active.instance_id == inst_id
        # session metadata 应已持久化
        bp_state = session.metadata.get("bp_state")
        assert bp_state is not None
        inst_data = next(
            i for i in bp_state["instances"] if i["instance_id"] == inst_id
        )
        assert inst_data["status"] == "active"

    @pytest.mark.asyncio
    async def test_edit_completed_resolve_fallback(self, handler, bp_config):
        """无 instance_id 参数时，handler 应通过降级查找定位到最近完成的实例。"""
        agent = MockAgent()
        session = agent._current_session
        inst_id = handler.state_manager.create_instance(bp_config, session.id, {"q": "x"})
        handler.state_manager.update_subtask_output(inst_id, "s1", {"o": 1})
        handler.state_manager.update_subtask_status(inst_id, "s1", SubtaskStatus.DONE)
        handler.state_manager.complete(inst_id)

        # 不传 instance_id，依赖 _handle_edit_output 的降级查找
        result = await handler.handle("bp_edit_output", {
            "subtask_id": "s1",
            "changes": {"o": 2},
        }, agent)

        assert "✅" in result
        snap = handler.state_manager.get(inst_id)
        assert snap.subtask_outputs["s1"]["o"] == 2


# ── bp_switch_task ─────────────────────────────────────────────


class TestBPSwitchTask:
    @pytest.mark.asyncio
    async def test_switch_task(self, handler_multi):
        agent = MockAgent()
        await handler_multi.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "1"}}, agent)
        first_id = handler_multi.state_manager.get_active("test-session").instance_id
        await handler_multi.handle("bp_start", {"bp_id": "test-bp-2", "input_data": {"q": "2"}}, agent)
        second_id = handler_multi.state_manager.get_active("test-session").instance_id

        result = await handler_multi.handle("bp_switch_task", {"target_instance_id": first_id}, agent)
        assert "切换" in result or first_id in result

    @pytest.mark.asyncio
    async def test_switch_to_same_task(self, handler):
        agent = MockAgent()
        await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "1"}}, agent)
        active_id = handler.state_manager.get_active("test-session").instance_id
        result = await handler.handle("bp_switch_task", {"target_instance_id": active_id}, agent)
        assert "已经是" in result

    @pytest.mark.asyncio
    async def test_switch_persists_stateTest(self, handler_multi):
        """switch 后 session.metadata['bp_state'] 应包含两个 instances。"""
        agent = MockAgent()
        session = agent._current_session
        await handler_multi.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "1"}}, agent)
        first_id = handler_multi.state_manager.get_active("test-session").instance_id
        await handler_multi.handle("bp_start", {"bp_id": "test-bp-2", "input_data": {"q": "2"}}, agent)
        second_id = handler_multi.state_manager.get_active("test-session").instance_id

        await handler_multi.handle("bp_switch_task", {"target_instance_id": first_id}, agent)
        bp_state = session.metadata.get("bp_state")
        assert bp_state is not None
        assert len(bp_state["instances"]) == 2

    @pytest.mark.asyncio
    async def test_switch_cross_session_rejectedTest(self, handler):
        """不同 session 的实例不能被 switch。"""
        agent_a = MockAgent()
        await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "1"}}, agent_a)
        inst_a = handler.state_manager.get_active("test-session").instance_id

        # 创建另一个 session 的 agent
        agent_b = MockAgent()
        agent_b._current_session.id = "other-session"
        # 先在 session-B 创建一个实例让它有 active
        await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "2"}}, agent_b)

        result = await handler.handle(
            "bp_switch_task", {"target_instance_id": inst_a}, agent_b,
        )
        assert "不属于当前会话" in result


class TestBPStartSuspend:
    @pytest.mark.asyncio
    async def test_start_suspends_with_pending_switchTest(self, handler_multi):
        """bp_start 挂起旧实例时应创建 PendingContextSwitch。"""
        agent = MockAgent()
        await handler_multi.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "1"}}, agent)
        first_id = handler_multi.state_manager.get_active("test-session").instance_id

        await handler_multi.handle("bp_start", {"bp_id": "test-bp-2", "input_data": {"q": "2"}}, agent)
        second_id = handler_multi.state_manager.get_active("test-session").instance_id

        pending = handler_multi.state_manager._pending_switches.get("test-session")
        assert pending is not None
        assert pending.suspended_instance_id == first_id
        assert pending.target_instance_id == second_id

    @pytest.mark.asyncio
    async def test_start_persists_after_suspendTest(self, handler_multi):
        """bp_start 挂起 + 创建后应持久化，旧实例状态为 suspended。"""
        agent = MockAgent()
        session = agent._current_session
        await handler_multi.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "1"}}, agent)
        await handler_multi.handle("bp_start", {"bp_id": "test-bp-2", "input_data": {"q": "2"}}, agent)

        bp_state = session.metadata.get("bp_state")
        assert bp_state is not None
        statuses = [inst["status"] for inst in bp_state["instances"]]
        assert "suspended" in statuses
        assert "active" in statuses


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

"""Test that bp_start delegates to engine.start() and relays events."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from seeagent.bestpractice.models import BPStatus


@pytest.mark.asyncio
async def test_bp_start_calls_advance():
    from seeagent.bestpractice.handler import BPToolHandler
    from seeagent.bestpractice.models import RunMode

    mock_engine = MagicMock()

    # engine.start() returns an async iterable that yields bp_instance_created + advance events
    async def fake_start(bp_config, session, input_data, run_mode):
        yield {"type": "bp_instance_created", "instance_id": "bp-new123",
               "bp_id": "test_bp", "bp_name": "Test BP",
               "run_mode": "manual", "subtasks": [{"id": "s1", "name": "Step 1"}]}
        yield {"type": "bp_progress", "instance_id": "bp-new123"}
        yield {"type": "bp_subtask_start", "instance_id": "bp-new123", "subtask_id": "s1"}
    mock_engine.start = fake_start

    mock_sm = MagicMock()
    mock_sm.get_active.return_value = None
    mock_cb = MagicMock()
    mock_cfg = MagicMock()
    mock_cfg.id = "test_bp"
    mock_cfg.name = "Test BP"
    mock_cfg.default_run_mode = RunMode.MANUAL
    mock_cfg.subtasks = [MagicMock(id="s1", name="Step 1")]
    config_registry = {"test_bp": mock_cfg}

    handler = BPToolHandler(mock_engine, mock_sm, mock_cb, config_registry)

    agent = MagicMock()
    agent._current_session = MagicMock()
    agent._current_session.id = "sess-1"
    agent._current_session.metadata = {}
    bus = asyncio.Queue()
    agent._current_session.context = MagicMock(_sse_event_bus=bus)

    result = await handler.handle("bp_start", {"bp_id": "test_bp"}, agent)

    # Should have pushed bp_instance_created + advance events to event_bus
    events = []
    while not bus.empty():
        events.append(bus.get_nowait())
    assert events[0]["type"] == "bp_instance_created"
    assert events[0]["instance_id"] == "bp-new123"
    # advance() events should follow
    assert any(e["type"] == "bp_progress" for e in events)
    assert any(e["type"] == "bp_subtask_start" for e in events)
    # Return message reflects execution
    assert "已创建并执行" in result


@pytest.mark.asyncio
async def test_bp_start_resumes_suspended_instead_of_creating_newTest():
    """bp_start with a bp_id that has a SUSPENDED instance should resume it."""
    from seeagent.bestpractice.handler import BPToolHandler
    from seeagent.bestpractice.models import BPInstanceSnapshot, RunMode

    mock_engine = MagicMock()

    # engine.switch() returns success
    mock_engine.switch = AsyncMock(return_value={"success": True, "target_id": "bp-suspended1"})

    # engine.advance() returns async generator with events
    async def fake_advance(instance_id, session):
        yield {"type": "bp_progress", "instance_id": instance_id}
        yield {"type": "bp_subtask_start", "instance_id": instance_id, "subtask_id": "s2"}
    mock_engine.advance = fake_advance

    # State manager with a SUSPENDED instance of the same bp_id
    suspended_snap = MagicMock(spec=BPInstanceSnapshot)
    suspended_snap.bp_id = "test_bp"
    suspended_snap.instance_id = "bp-suspended1"
    suspended_snap.session_id = "sess-1"
    suspended_snap.status = BPStatus.SUSPENDED
    suspended_snap.suspended_at = 1000.0

    mock_sm = MagicMock()
    mock_sm.get_active.return_value = None
    mock_sm.get_all_for_session.return_value = [suspended_snap]

    mock_cb = MagicMock()
    mock_cfg = MagicMock()
    mock_cfg.id = "test_bp"
    mock_cfg.name = "Test BP"
    mock_cfg.default_run_mode = RunMode.MANUAL
    config_registry = {"test_bp": mock_cfg}

    handler = BPToolHandler(mock_engine, mock_sm, mock_cb, config_registry)

    agent = MagicMock()
    agent._current_session = MagicMock()
    agent._current_session.id = "sess-1"
    agent._current_session.metadata = {}
    bus = asyncio.Queue()
    agent._current_session.context = MagicMock(_sse_event_bus=bus)

    result = await handler.handle("bp_start", {"bp_id": "test_bp"}, agent)

    # Should resume, not create new
    assert "已恢复并继续" in result
    mock_engine.switch.assert_called_once_with("bp-suspended1", agent._current_session)
    # Should NOT call engine.start
    assert not hasattr(mock_engine, "start") or not getattr(mock_engine.start, "called", False)

    # Advance events should be relayed
    events = []
    while not bus.empty():
        events.append(bus.get_nowait())
    assert any(e["type"] == "bp_progress" for e in events)


@pytest.mark.asyncio
async def test_bp_start_creates_new_when_no_suspendedTest():
    """bp_start without suspended instances should create new as usual."""
    from seeagent.bestpractice.handler import BPToolHandler
    from seeagent.bestpractice.models import RunMode

    mock_engine = MagicMock()

    async def fake_start(bp_config, session, input_data, run_mode):
        yield {"type": "bp_instance_created", "instance_id": "bp-new1",
               "bp_id": "test_bp", "bp_name": "Test BP",
               "run_mode": "manual", "subtasks": []}
    mock_engine.start = fake_start

    mock_sm = MagicMock()
    mock_sm.get_active.return_value = None
    mock_sm.get_all_for_session.return_value = []  # No suspended instances

    mock_cb = MagicMock()
    mock_cfg = MagicMock()
    mock_cfg.id = "test_bp"
    mock_cfg.name = "Test BP"
    mock_cfg.default_run_mode = RunMode.MANUAL
    config_registry = {"test_bp": mock_cfg}

    handler = BPToolHandler(mock_engine, mock_sm, mock_cb, config_registry)

    agent = MagicMock()
    agent._current_session = MagicMock()
    agent._current_session.id = "sess-1"
    agent._current_session.metadata = {}
    bus = asyncio.Queue()
    agent._current_session.context = MagicMock(_sse_event_bus=bus)

    result = await handler.handle("bp_start", {"bp_id": "test_bp"}, agent)

    assert "已创建并执行" in result

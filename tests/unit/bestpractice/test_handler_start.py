"""Test that bp_start creates instance AND calls engine.advance() to execute."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_bp_start_calls_advance():
    from seeagent.bestpractice.handler import BPToolHandler
    from seeagent.bestpractice.models import RunMode

    mock_engine = MagicMock()
    mock_engine.execute_subtask = AsyncMock()  # Should NOT be called (legacy)

    # advance() must return an async iterable
    async def fake_advance(instance_id, session):
        yield {"type": "bp_progress", "instance_id": instance_id}
        yield {"type": "bp_subtask_start", "instance_id": instance_id, "subtask_id": "s1"}
    mock_engine.advance = fake_advance

    mock_sm = MagicMock()
    mock_sm.get_active.return_value = None
    mock_sm.create_instance.return_value = "bp-new123"
    mock_sm.get.return_value = MagicMock(session_id="sess-1")
    mock_sm.serialize_for_session.return_value = {"version": 1, "instances": []}
    mock_cb = MagicMock()
    mock_cfg = MagicMock()
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

    # Should NOT have called execute_subtask (legacy method)
    mock_engine.execute_subtask.assert_not_called()
    # Should have created instance
    mock_sm.create_instance.assert_called_once()
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

"""Test that bp_start delegates to engine.start() and relays events."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock


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

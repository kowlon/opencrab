# tests/unit/bestpractice/test_engine_stream.py
"""Tests for BPEngine._run_subtask_stream() and answer()."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from seeagent.bestpractice.models import (
    BPInstanceSnapshot, BestPracticeConfig, SubtaskConfig,
    SubtaskStatus, RunMode,
)
from seeagent.bestpractice.engine import BPEngine


def _make_config():
    return BestPracticeConfig(
        id="test_bp", name="Test BP",
        subtasks=[
            SubtaskConfig(
                id="s1", name="Step 1", agent_profile="default",
                input_schema={
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            ),
        ],
        final_output_schema={"type": "object"},
    )


def _make_snap(cfg):
    return BPInstanceSnapshot(
        bp_id=cfg.id, instance_id="bp-test", session_id="sess-1",
        created_at=0.0, current_subtask_index=0,
        run_mode=RunMode.MANUAL,
        subtask_statuses={"s1": SubtaskStatus.WAITING_INPUT.value},
        initial_input={"q": "hello"},
        subtask_outputs={}, context_summary="",
        supplemented_inputs={},
    )


def _make_schema_chain():
    """Create a SchemaChain mock that returns proper dicts."""
    sc = MagicMock()
    sc.derive_output_schema.return_value = {"type": "object"}
    return sc


@pytest.mark.asyncio
class TestRunSubtaskStream:
    async def test_streams_events_from_delegate(self):
        cfg = _make_config()
        sm = MagicMock()
        sc = _make_schema_chain()
        engine = BPEngine(sm, sc)

        # Mock orchestrator.delegate to return result
        mock_orch = AsyncMock()
        mock_orch.delegate = AsyncMock(return_value='```json\n{"answer": "42"}\n```')
        engine.set_orchestrator(mock_orch)

        # Mock session with event_bus
        session = MagicMock()
        ctx = MagicMock()
        ctx._sse_event_bus = None
        ctx._bp_delegate_task = None
        session.context = ctx

        subtask = cfg.subtasks[0]
        events = []
        async for ev in engine._run_subtask_stream(
            "bp-test", subtask, {"q": "hello"}, cfg, session
        ):
            events.append(ev)

        # Should have _internal_output as last event
        assert any(e["type"] == "_internal_output" for e in events)
        output_ev = next(e for e in events if e["type"] == "_internal_output")
        assert "answer" in output_ev["data"]

    async def test_delegate_task_exposed_on_context(self):
        """R17: delegate_task is stored on session.context._bp_delegate_task."""
        cfg = _make_config()
        sm = MagicMock()
        sc = _make_schema_chain()
        engine = BPEngine(sm, sc)
        mock_orch = AsyncMock()
        mock_orch.delegate = AsyncMock(return_value='{"ok": true}')
        engine.set_orchestrator(mock_orch)

        session = MagicMock()
        ctx = MagicMock()
        ctx._sse_event_bus = None
        ctx._bp_delegate_task = None
        session.context = ctx

        subtask = cfg.subtasks[0]
        events = []
        async for ev in engine._run_subtask_stream(
            "bp-test", subtask, {"q": "test"}, cfg, session
        ):
            events.append(ev)

        # After completion, _bp_delegate_task should be cleaned up (None)
        assert session.context._bp_delegate_task is None

    async def test_yields_error_when_no_orchestrator(self):
        """When no orchestrator is available, yields an error event."""
        cfg = _make_config()
        sm = MagicMock()
        sc = _make_schema_chain()
        engine = BPEngine(sm, sc)
        # Do NOT set orchestrator
        engine._get_orchestrator = MagicMock(return_value=None)

        session = MagicMock()
        subtask = cfg.subtasks[0]
        events = []
        async for ev in engine._run_subtask_stream(
            "bp-test", subtask, {"q": "hello"}, cfg, session
        ):
            events.append(ev)

        assert len(events) == 1
        assert events[0]["type"] == "error"

    async def test_passthrough_events_from_event_bus(self):
        """Intermediate events from event_bus are yielded (except 'done')."""
        cfg = _make_config()
        sm = MagicMock()
        sc = _make_schema_chain()
        engine = BPEngine(sm, sc)

        # Simulate delegate that puts events on the bus then returns
        async def fake_delegate(**kwargs):
            session = kwargs.get("session")
            bus = session.context._sse_event_bus
            await bus.put({"type": "step_card", "data": "card1"})
            await bus.put({"type": "thinking", "data": "hmm"})
            await bus.put({"type": "done"})  # should be filtered
            return '{"result": "ok"}'

        mock_orch = AsyncMock()
        mock_orch.delegate = AsyncMock(side_effect=fake_delegate)
        engine.set_orchestrator(mock_orch)

        session = MagicMock()
        ctx = MagicMock()
        ctx._sse_event_bus = None
        ctx._bp_delegate_task = None
        session.context = ctx

        subtask = cfg.subtasks[0]
        events = []
        async for ev in engine._run_subtask_stream(
            "bp-test", subtask, {"q": "hello"}, cfg, session
        ):
            events.append(ev)

        event_types = [e["type"] for e in events]
        assert "step_card" in event_types
        assert "thinking" in event_types
        assert "done" not in event_types  # "done" should be filtered
        assert "_internal_output" in event_types

    async def test_restores_old_event_bus(self):
        """After stream completes, the old event_bus is restored."""
        cfg = _make_config()
        sm = MagicMock()
        sc = _make_schema_chain()
        engine = BPEngine(sm, sc)

        mock_orch = AsyncMock()
        mock_orch.delegate = AsyncMock(return_value='{"ok": true}')
        engine.set_orchestrator(mock_orch)

        old_bus = asyncio.Queue()
        session = MagicMock()
        ctx = MagicMock()
        ctx._sse_event_bus = old_bus
        ctx._bp_delegate_task = None
        session.context = ctx

        subtask = cfg.subtasks[0]
        async for _ in engine._run_subtask_stream(
            "bp-test", subtask, {"q": "hello"}, cfg, session
        ):
            pass

        # Old bus should be restored
        assert session.context._sse_event_bus is old_bus


@pytest.mark.asyncio
class TestAnswer:
    async def test_answer_merges_supplemented_inputs_and_resets_status(self):
        cfg = _make_config()
        snap = _make_snap(cfg)
        snap.bp_config = cfg
        sm = MagicMock()
        sm.get.return_value = snap
        sm.update_subtask_status = MagicMock()
        sc = MagicMock()
        engine = BPEngine(sm, sc)
        engine._get_config = MagicMock(return_value=cfg)

        # Mock _run_subtask_stream so advance() works
        async def mock_stream(*args, **kwargs):
            yield {"type": "_internal_output", "data": {"answer": "yes"}}
        engine._run_subtask_stream = mock_stream

        session = MagicMock()
        events = []
        async for ev in engine.answer("bp-test", "s1", {"extra_field": "val"}, session):
            events.append(ev)

        # supplemented_inputs should be updated
        assert snap.supplemented_inputs["s1"] == {"extra_field": "val"}
        # Status should have been reset to PENDING
        sm.update_subtask_status.assert_any_call(
            "bp-test", "s1", SubtaskStatus.PENDING
        )

    async def test_answer_instance_not_found(self):
        sm = MagicMock()
        sm.get.return_value = None
        sc = MagicMock()
        engine = BPEngine(sm, sc)

        events = []
        async for ev in engine.answer("bp-missing", "s1", {"q": "test"}, MagicMock()):
            events.append(ev)

        assert len(events) == 1
        assert events[0]["type"] == "error"

    async def test_answer_merges_with_existing_supplemented_data(self):
        """When supplemented_inputs already has data, it should be merged."""
        cfg = _make_config()
        snap = _make_snap(cfg)
        snap.bp_config = cfg
        snap.supplemented_inputs["s1"] = {"old_field": "old_val"}
        sm = MagicMock()
        sm.get.return_value = snap
        sm.update_subtask_status = MagicMock()
        sc = MagicMock()
        engine = BPEngine(sm, sc)
        engine._get_config = MagicMock(return_value=cfg)

        async def mock_stream(*args, **kwargs):
            yield {"type": "_internal_output", "data": {"answer": "yes"}}
        engine._run_subtask_stream = mock_stream

        session = MagicMock()
        events = []
        async for ev in engine.answer("bp-test", "s1", {"new_field": "new_val"}, session):
            events.append(ev)

        # Both old and new fields should exist
        assert snap.supplemented_inputs["s1"]["old_field"] == "old_val"
        assert snap.supplemented_inputs["s1"]["new_field"] == "new_val"

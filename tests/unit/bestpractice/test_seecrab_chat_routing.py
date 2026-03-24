"""Tests for _cancel_bp_from_chat."""
import pytest

from seeagent.api.routes.seecrab import _cancel_bp_from_chat


class MockStateManager:
    def __init__(self):
        self._cancelled = []
        self._cooldowns = {}

    def cancel(self, instance_id):
        self._cancelled.append(instance_id)

    def set_cooldown(self, session_id, turns=3):
        self._cooldowns[session_id] = turns

    def serialize_for_session(self, session_id):
        return {"version": 1, "instances": [], "cooldown": self._cooldowns.get(session_id, 0)}


class MockSession:
    def __init__(self):
        self.metadata = {}


class MockSessionManager:
    def mark_dirty(self):
        pass


class TestCancelBPFromChat:
    @pytest.mark.asyncio
    async def test_cancel_yields_bp_cancelled_event(self):
        events = []
        async for event in _cancel_bp_from_chat(
            session_id="s1", instance_id="inst1", bp_name="Test BP",
            sm=MockStateManager(), session=MockSession(), session_manager=MockSessionManager(),
        ):
            events.append(event)
        assert any(e.get("type") == "bp_cancelled" for e in events)
        cancelled = next(e for e in events if e.get("type") == "bp_cancelled")
        assert cancelled["instance_id"] == "inst1"
        assert cancelled["bp_name"] == "Test BP"

    @pytest.mark.asyncio
    async def test_cancel_sets_cooldown(self):
        sm = MockStateManager()
        async for _ in _cancel_bp_from_chat(
            session_id="s1", instance_id="inst1", bp_name="Test",
            sm=sm, session=MockSession(), session_manager=MockSessionManager(),
        ):
            pass
        assert sm._cooldowns.get("s1", 0) > 0

    @pytest.mark.asyncio
    async def test_cancel_persists_to_session(self):
        session = MockSession()
        sm = MockStateManager()
        async for _ in _cancel_bp_from_chat(
            session_id="s1", instance_id="inst1", bp_name="Test",
            sm=sm, session=session, session_manager=MockSessionManager(),
        ):
            pass
        assert "bp_state" in session.metadata

    @pytest.mark.asyncio
    async def test_cancel_yields_done(self):
        events = []
        async for event in _cancel_bp_from_chat(
            session_id="s1", instance_id="inst1", bp_name="Test",
            sm=MockStateManager(), session=MockSession(), session_manager=MockSessionManager(),
        ):
            events.append(event)
        assert events[-1]["type"] == "done"

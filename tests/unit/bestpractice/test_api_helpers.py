# tests/unit/bestpractice/test_api_helpers.py
"""Tests for BP API route helpers: busy-lock, session resolution, persistence."""
import asyncio
import pytest

@pytest.mark.asyncio
class TestBusyLock:
    async def test_mark_and_clear(self):
        from seeagent.api.routes.bestpractice import (
            _bp_mark_busy, _bp_clear_busy, _bp_busy_locks,
        )
        _bp_busy_locks.clear()
        assert await _bp_mark_busy("sess-1", "bp_start", "lock-1")
        assert not await _bp_mark_busy("sess-1", "bp_next", "lock-2")  # Already locked
        _bp_clear_busy("sess-1", "lock-1")
        assert await _bp_mark_busy("sess-1", "bp_next", "lock-2")  # Free now
        _bp_busy_locks.clear()

    async def test_renew_updates_timestamp(self):
        import time
        from seeagent.api.routes.bestpractice import (
            _bp_mark_busy, _bp_renew_busy, _bp_clear_busy, _bp_busy_locks,
        )
        _bp_busy_locks.clear()
        await _bp_mark_busy("sess-1", "bp_start", "lock-1")
        _, ts1, _ = _bp_busy_locks["sess-1"]
        await asyncio.sleep(0.01)
        _bp_renew_busy("sess-1")
        _, ts2, _ = _bp_busy_locks["sess-1"]
        assert ts2 > ts1
        _bp_busy_locks.clear()

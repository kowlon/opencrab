# tests/unit/test_sse_heartbeat.py
"""Tests for SSE heartbeat stream wrapper."""
from __future__ import annotations

import asyncio
import warnings

import pytest

from seeagent.api.sse_utils import (
    _HEARTBEAT_COMMENT,
    _get_heartbeat_interval,
    sse_heartbeat_stream,
)


# ── Helpers ──────────────────────────────────────────────────────


async def _fast_stream(events: list[dict]):
    """Yield events immediately (no delay)."""
    for e in events:
        yield e


async def _slow_stream(events: list[dict], delay: float):
    """Yield events with *delay* seconds between each."""
    for e in events:
        await asyncio.sleep(delay)
        yield e


async def _collect(stream, max_items: int = 50) -> list:
    """Collect items from an async iterator with a safety cap."""
    items = []
    async for item in stream:
        items.append(item)
        if len(items) >= max_items:
            break
    return items


@pytest.fixture()
def _low_min_interval(monkeypatch):
    """Temporarily lower _MIN_INTERVAL so heartbeats can fire in sub-second tests."""
    import seeagent.api.sse_utils as mod
    monkeypatch.setattr(mod, "_MIN_INTERVAL", 0)


# ── Basic passthrough ────────────────────────────────────────────


class TestPassthrough:
    @pytest.mark.asyncio
    async def test_empty_stream_yields_nothing(self):
        """An empty source stream produces no output."""
        result = await _collect(sse_heartbeat_stream(_fast_stream([]), interval=5))
        assert result == []

    @pytest.mark.asyncio
    async def test_fast_events_no_heartbeat(self):
        """Events arriving faster than interval should pass through without heartbeats."""
        events = [{"type": "a"}, {"type": "b"}, {"type": "c"}]
        result = await _collect(sse_heartbeat_stream(_fast_stream(events), interval=5))
        assert result == events

    @pytest.mark.asyncio
    async def test_event_order_preserved(self):
        """Events must come out in the same order they went in."""
        events = [{"id": i} for i in range(20)]
        result = await _collect(sse_heartbeat_stream(_fast_stream(events), interval=5))
        assert result == events


# ── Heartbeat injection ──────────────────────────────────────────


class TestHeartbeatInjection:
    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_low_min_interval")
    async def test_heartbeat_fires_on_idle(self):
        """When the source is idle longer than interval, None heartbeats MUST appear."""
        async def slow_one_event():
            await asyncio.sleep(0.4)
            yield {"type": "data"}

        result = await _collect(
            sse_heartbeat_stream(slow_one_event(), interval=0.1),
            max_items=10,
        )
        heartbeats = [x for x in result if x is None]
        real_events = [x for x in result if x is not None]
        assert len(heartbeats) >= 1, f"Expected heartbeats, got {result}"
        assert real_events == [{"type": "data"}]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_low_min_interval")
    async def test_heartbeat_is_none_not_dict(self):
        """Heartbeat signals must be None; real events must be dicts."""
        async def slow_event():
            await asyncio.sleep(0.3)
            yield {"type": "done"}

        result = await _collect(
            sse_heartbeat_stream(slow_event(), interval=0.1),
            max_items=10,
        )
        for item in result:
            assert item is None or isinstance(item, dict)
        # Must have at least one heartbeat AND the real event
        assert None in result
        assert {"type": "done"} in result

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_low_min_interval")
    async def test_multiple_heartbeats_before_event(self):
        """A long idle period should produce multiple heartbeats."""
        async def very_slow():
            await asyncio.sleep(0.5)
            yield {"type": "data"}

        result = await _collect(
            sse_heartbeat_stream(very_slow(), interval=0.1),
            max_items=20,
        )
        heartbeats = [x for x in result if x is None]
        assert len(heartbeats) >= 3, f"Expected >=3 heartbeats, got {len(heartbeats)}"

    @pytest.mark.asyncio
    async def test_heartbeat_comment_format(self):
        """The exported heartbeat comment is a valid SSE comment."""
        assert _HEARTBEAT_COMMENT == ": heartbeat\n\n"
        assert _HEARTBEAT_COMMENT.startswith(":")
        assert _HEARTBEAT_COMMENT.endswith("\n\n")


# ── Interval floor ───────────────────────────────────────────────


class TestIntervalFloor:
    @pytest.mark.asyncio
    async def test_interval_floor_enforced(self):
        """Even with interval=1, the floor should clamp it to 5."""
        timed_out = False

        async def never_yields():
            await asyncio.Event().wait()
            yield  # pragma: no cover

        async def run():
            nonlocal timed_out
            async for item in sse_heartbeat_stream(never_yields(), interval=1):
                if item is None:
                    timed_out = True
                    break

        # If floor works (5s), this 2-second timeout should NOT produce a heartbeat
        try:
            await asyncio.wait_for(run(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        assert not timed_out, "Heartbeat fired before 5-second floor"

    def test_get_heartbeat_interval_floor(self):
        """_get_heartbeat_interval should never return below 5."""
        result = _get_heartbeat_interval()
        assert result >= 5

    def test_get_heartbeat_interval_returns_int(self):
        """Return type should be int."""
        result = _get_heartbeat_interval()
        assert isinstance(result, int)


# ── Error propagation ────────────────────────────────────────────


class TestErrorPropagation:
    @pytest.mark.asyncio
    async def test_source_exception_propagates(self):
        """Exceptions from the source stream should propagate to the caller."""
        async def exploding_stream():
            yield {"type": "ok"}
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await _collect(sse_heartbeat_stream(exploding_stream(), interval=5))

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_low_min_interval")
    async def test_source_exception_after_heartbeat(self):
        """Exceptions should propagate even if heartbeats were sent before."""
        async def slow_then_explode():
            await asyncio.sleep(0.3)
            raise ValueError("delayed boom")
            yield  # pragma: no cover

        with pytest.raises(ValueError, match="delayed boom"):
            await _collect(
                sse_heartbeat_stream(slow_then_explode(), interval=0.1),
                max_items=10,
            )


# ── Cleanup / cancellation ───────────────────────────────────────


class TestCleanup:
    @pytest.mark.asyncio
    async def test_generator_close_cancels_pending(self):
        """Closing the heartbeat generator should clean up the pending future
        AND await the upstream generator's finally block."""
        cleanup_reached = False

        async def infinite_stream():
            nonlocal cleanup_reached
            try:
                while True:
                    await asyncio.sleep(100)
                    yield {"type": "tick"}
            finally:
                cleanup_reached = True

        gen = sse_heartbeat_stream(infinite_stream(), interval=5)
        aiter = gen.__aiter__()

        # Start iteration — this will block on the first __anext__ of infinite_stream
        task = asyncio.create_task(aiter.__anext__())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Explicitly close the async generator — triggers finally with await
        await gen.aclose()

        # Give event loop a tick for cleanup
        await asyncio.sleep(0.05)
        assert cleanup_reached, "Source stream's finally block was not reached"

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_low_min_interval")
    async def test_no_task_exception_warning(self):
        """Closing after iteration starts should not trigger
        'Task exception was never retrieved' warning."""
        async def fail_after_delay():
            await asyncio.sleep(0.15)
            raise RuntimeError("should be consumed in finally")
            yield  # pragma: no cover

        gen = sse_heartbeat_stream(fail_after_delay(), interval=0.1)

        # Actually start iterating so the generator enters try/finally
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            try:
                async for item in gen:
                    if item is None:
                        # Got a heartbeat, now close while pending may have exception
                        break
            except RuntimeError:
                pass

            await gen.aclose()
            await asyncio.sleep(0.1)  # Let GC run

        # Check no "Task exception was never retrieved" warning
        task_warnings = [
            x for x in w
            if "never retrieved" in str(x.message).lower()
        ]
        assert task_warnings == [], f"Got unexpected warnings: {task_warnings}"


# ── Integration-like: realistic SSE pattern ──────────────────────


class TestRealisticPattern:
    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_low_min_interval")
    async def test_mixed_fast_and_slow_events(self):
        """Simulate a realistic stream: fast burst -> long pause -> more events."""
        async def realistic_stream():
            yield {"type": "thinking", "content": "..."}
            yield {"type": "ai_text", "content": "Hello"}
            # Long pause (agent executing tools)
            await asyncio.sleep(0.3)
            yield {"type": "step_card", "tool": "search"}
            yield {"type": "done"}

        result = await _collect(
            sse_heartbeat_stream(realistic_stream(), interval=0.1),
            max_items=20,
        )
        real_events = [e for e in result if e is not None]
        assert len(real_events) == 4
        assert real_events[0]["type"] == "thinking"
        assert real_events[-1]["type"] == "done"
        # Should have heartbeats during the 0.3s pause
        heartbeats = [e for e in result if e is None]
        assert len(heartbeats) >= 1

    @pytest.mark.asyncio
    async def test_heartbeat_does_not_duplicate_events(self):
        """No event should appear more than once, even with heartbeats interleaved."""
        events = [{"id": i} for i in range(5)]

        async def spaced_stream():
            for e in events:
                await asyncio.sleep(0.01)
                yield e

        result = await _collect(
            sse_heartbeat_stream(spaced_stream(), interval=5),
            max_items=20,
        )
        real_events = [e for e in result if e is not None]
        assert real_events == events

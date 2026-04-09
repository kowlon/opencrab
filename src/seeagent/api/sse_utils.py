"""SSE streaming utilities — periodic heartbeat injection."""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_HEARTBEAT_COMMENT = ": heartbeat\n\n"
_MIN_INTERVAL = 5  # seconds — hard floor to prevent accidental flooding


def _get_heartbeat_interval() -> int:
    """Read SSE heartbeat interval from settings, with a 5-second floor."""
    try:
        from seeagent.config import settings
        interval = settings.sse_heartbeat_interval
    except Exception:
        interval = 30
    return max(interval, _MIN_INTERVAL)


async def sse_heartbeat_stream(
    stream: AsyncIterator[T],
    interval: int | None = None,
) -> AsyncIterator[T | None]:
    """Wrap *stream*, yielding ``None`` whenever no event arrives within *interval* seconds.

    Callers should treat ``None`` as a signal to emit an SSE heartbeat comment.
    The underlying ``__anext__`` is never cancelled — we keep it alive across
    idle rounds so the producer's internal state is preserved.
    """
    if interval is None:
        interval = _get_heartbeat_interval()
    interval = max(interval, _MIN_INTERVAL)

    aiter = stream.__aiter__()
    pending: asyncio.Future | None = None

    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(aiter.__anext__())

            done, _ = await asyncio.wait({pending}, timeout=interval)

            if done:
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    break
                pending = None
                yield event
            else:
                # Idle timeout — signal caller to send heartbeat
                logger.info(f"[SSE-HB] Idle {interval}s, emitting heartbeat")
                yield None
    finally:
        if pending is not None:
            if not pending.done():
                pending.cancel()
                try:
                    await pending
                except (asyncio.CancelledError, Exception):
                    pass
            elif not pending.cancelled():
                # Consume result to suppress "Task exception was never retrieved"
                try:
                    pending.result()
                except Exception:
                    pass

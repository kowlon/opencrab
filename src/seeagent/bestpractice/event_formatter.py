"""BPEventFormatter -- transforms SubAgent streaming events into SSE step_card events.

Encapsulates the step card pipeline (StepFilter, CardBuilder, TimerTracker,
TitleGenerator, StepAggregator) so that BPEngine does not depend on api.adapters.
"""

from __future__ import annotations

import asyncio

from seeagent.api.adapters.card_builder import CardBuilder
from seeagent.api.adapters.step_aggregator import StepAggregator
from seeagent.api.adapters.step_filter import StepFilter
from seeagent.api.adapters.timer_tracker import TimerTracker
from seeagent.api.adapters.title_generator import TitleGenerator


class BPEventFormatter:
    """Transforms raw SubAgent streaming events into SSE step_card events."""

    def __init__(
        self,
        agent_profile: str,
        subtask_name: str,
        instance_id: str,
        subtask_id: str,
        delegate_step_id: str = "",
    ) -> None:
        self._agent_profile = agent_profile
        self._subtask_name = subtask_name
        self._delegate_step_id = delegate_step_id
        self._sub_agent_id = agent_profile

        step_filter = StepFilter()
        card_builder = CardBuilder()
        timer = TimerTracker()
        timer.start(f"bp_{instance_id}_{subtask_id}")
        title_gen = TitleGenerator(brain=None, user_messages=[])
        self._title_queue: asyncio.Queue = asyncio.Queue()
        self._aggregator = StepAggregator(
            title_gen=title_gen,
            card_builder=card_builder,
            timer=timer,
            title_update_queue=self._title_queue,
            agent_id=self._sub_agent_id,
        )
        self._step_filter = step_filter
        self._delegate_card_yielded = False

    @property
    def sub_agent_id(self) -> str:
        return self._sub_agent_id

    # ── Event handlers ────────────────────────────────────────

    def on_agent_header(self, event: dict) -> None:
        """Track sub-agent identity."""
        aid = event.get("agent_id")
        if aid and aid != "main":
            self._sub_agent_id = aid
            self._aggregator._agent_id = aid

    def make_thinking_event(self, event: dict) -> dict:
        """Format thinking_delta into SSE thinking event."""
        return {
            "type": "thinking",
            "content": event.get("content", ""),
            "agent_id": self._sub_agent_id,
        }

    def make_delegate_card(self, status: str, duration: float | None = None) -> dict:
        """Build delegate step_card event."""
        return {
            "type": "step_card",
            "step_id": self._delegate_step_id,
            "title": f"委派 {self._agent_profile}: {self._subtask_name}",
            "status": status,
            "source_type": "tool",
            "card_type": "delegate",
            "agent_id": "main",
            "delegate_agent_id": self._sub_agent_id,
            "duration": duration,
        }

    def ensure_delegate_card(self) -> list[dict]:
        """Yield delegate card (running) if not yet yielded. Call before first non-thinking event."""
        if not self._delegate_card_yielded:
            self._delegate_card_yielded = True
            return [self.make_delegate_card("running")]
        return []

    async def on_tool_call_start(self, event: dict) -> list[dict]:
        """Classify + aggregate tool call start. Returns step_card events."""
        events: list[dict] = []
        tool_name = event.get("tool", "")
        args = event.get("args", {})
        tool_id = event.get("id", f"bp_tool_{id(event)}")
        fr = self._step_filter.classify(tool_name, args)
        for ev in await self._aggregator.on_tool_call_start(
            tool_name, args, tool_id, fr
        ):
            events.append(ev)
        events.extend(self._drain_title_queue())
        return events

    async def on_tool_call_end(self, event: dict) -> list[dict]:
        """Update aggregated card on tool completion. Returns step_card events."""
        tool_name = event.get("tool", "")
        tool_id = event.get("id", "")
        result = event.get("result", "")
        is_error = event.get("is_error", False)
        events: list[dict] = []
        for ev in await self._aggregator.on_tool_call_end(
            tool_name, tool_id, result, is_error
        ):
            events.append(ev)
        return events

    async def on_text_delta(self) -> list[dict]:
        """Close active aggregation. Returns step_card events."""
        events: list[dict] = []
        for ev in await self._aggregator.on_text_delta():
            events.append(ev)
        return events

    async def flush(self) -> list[dict]:
        """Flush pending aggregation + drain title queue."""
        events: list[dict] = []
        for ev in await self._aggregator.flush():
            events.append(ev)
        events.extend(self._drain_title_queue())
        return events

    def _drain_title_queue(self) -> list[dict]:
        events: list[dict] = []
        while not self._title_queue.empty():
            try:
                events.append(self._title_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events

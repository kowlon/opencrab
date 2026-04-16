# src/seeagent/api/adapters/seecrab_adapter.py
"""SeeCrabAdapter: translates raw Agent event stream → refined SSE events."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from .card_builder import CardBuilder
from .seecrab_models import FilterResult, StepFilterConfig
from .step_aggregator import StepAggregator
from .step_filter import StepFilter
from .timer_tracker import TimerTracker
from .title_generator import TitleGenerator

logger = logging.getLogger(__name__)

_STREAM_DONE = object()


class SeeCrabAdapter:
    """Core translation layer: raw reason_stream events → refined SSE events."""

    def __init__(self, brain: object | None, user_messages: list[str], debug_enabled: bool = False):
        filter_config = StepFilterConfig(debug_enabled=debug_enabled)
        self.step_filter = StepFilter(filter_config)
        self.step_filter.set_user_messages(user_messages)
        self.timer = TimerTracker()
        self.title_gen = TitleGenerator(brain, user_messages)
        self.card_builder = CardBuilder()
        self._title_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._show_subagent_actions = bool(self.step_filter.config.debug_enabled)
        self.aggregator = StepAggregator(
            title_gen=self.title_gen,
            card_builder=self.card_builder,
            timer=self.timer,
            title_update_queue=self._title_queue,
            show_subagent_actions=self._show_subagent_actions,
        )
        self._aggregators: dict[str, StepAggregator] = {"main": self.aggregator}
        self._active_agent_id = "main"
        self._delegation_step_ids: dict[str, str] = {}
        # 仅在 STEP_FILTER_DEBUG_ENABLED=true 时，展示子 Agent 的细粒度执行动作。
        # 关闭时仍保留“委派卡 + 执行计划”，但隐藏 run_shell/read_file 等细节步骤。
        self._subagent_visible_tools = {
            "web_search",
            "news_search",
            "browser_task",
            "generate_image",
            "list_skills",
            "get_skill_info",
            "run_skill_script",
            "run_shell",
            "write_file",
            "read_file",
            "list_directory",
            # 把交付动作也显示出来，避免最后一个可见步骤已经结束，
            # 但前端仍在等待 artifact 发送而看起来“卡住很久”。
            "deliver_artifacts",
        }
        logger.info(
            "[SeeCrab] adapter initialized: debug_enabled=%s show_subagent_actions=%s",
            debug_enabled,
            self._show_subagent_actions,
        )

    async def transform(
        self,
        raw_events: AsyncIterator[dict],
        reply_id: str,
        event_bus: asyncio.Queue | None = None,
    ) -> AsyncIterator[dict]:
        """Consume raw events + title_update_queue, yield refined events."""
        self.timer.start(reply_id)
        yield self.timer.make_event("ttft", "running")

        source = self._merge_sources(raw_events, event_bus) if event_bus else raw_events

        async for event in source:
            for refined in await self._process_event(event):
                yield refined
            # Drain any pending title updates between raw events
            while not self._title_queue.empty():
                try:
                    title_event = self._title_queue.get_nowait()
                    yield title_event
                except asyncio.QueueEmpty:
                    break

        # Flush pending aggregation from all agents
        for agg in self._aggregators.values():
            for e in await agg.flush():
                yield e

        # Drain remaining title updates after stream ends
        while not self._title_queue.empty():
            try:
                yield self._title_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Final timing
        yield self.timer.make_event("total", "done")
        yield {"type": "done"}

    async def _process_event(self, event: dict) -> list[dict]:
        """Dispatch a single raw event to handlers."""
        etype = event.get("type", "")

        if etype == "agent_header":
            return await self._handle_agent_switch(event)

        # When debug visibility is disabled, hide delegated sub-agent execution traces.
        # This keeps the UI focused on main-agent level progress only.
        if (
            not self._show_subagent_actions
            and self._active_agent_id != "main"
            and etype
            in {
                "thinking_start",
                "thinking_delta",
                "thinking_end",
                "text_delta",
                "tool_call_start",
                "tool_call_end",
                "ask_user",
            }
        ):
            # 正式模式（STEP_FILTER_DEBUG_ENABLED=false）下，隐藏子 Agent 的细粒度执行流，
            # 避免前端展示过多底层动作噪音。
            logger.info(
                "[SeeCrab] hide sub-agent event: agent=%s type=%s debug_enabled=%s",
                self._active_agent_id,
                etype,
                self._show_subagent_actions,
            )
            return []

        if (
            not self._show_subagent_actions
            and self._active_agent_id != "main"
            and etype in {"plan_created", "plan_step_updated", "plan_completed", "step_card"}
        ):
            # 即使关闭调试展示，也要保留子 Agent 的“执行计划/计划步骤卡”，
            # 这样用户仍能看到委派后的整体执行进度。
            logger.info(
                "[SeeCrab] keep sub-agent planning event: agent=%s type=%s debug_enabled=%s",
                self._active_agent_id,
                etype,
                self._show_subagent_actions,
            )

        if etype == "thinking_delta":
            return self._handle_thinking(event)

        if etype == "thinking_start":
            return []  # absorbed, we use delta

        if etype == "thinking_end":
            return []  # timing info only

        if etype == "text_delta":
            return await self._handle_text_delta(event)

        if etype == "tool_call_start":
            return await self._handle_tool_call_start(event)

        if etype == "tool_call_end":
            return await self._handle_tool_call_end(event)

        if etype == "plan_created":
            return await self.aggregator.on_plan_created(event.get("plan", event))

        if etype == "plan_step_updated":
            # Engine sends stepId as string (e.g. "step_1"), normalize to index
            raw_step_id = str(event.get("stepId", event.get("step_index", "")))
            if not raw_step_id:
                logger.warning("[SeeCrab] plan_step_updated with empty stepId, skipping")
                return []
            step_index = self.aggregator._plan_id_to_index.get(raw_step_id, 0)
            if step_index == 0:
                # Fallback 1: parse "step_N" → N
                if "_" in raw_step_id:
                    try:
                        step_index = int(raw_step_id.split("_")[-1])
                    except (ValueError, IndexError):
                        pass
                # Fallback 2: try direct integer parse
                if step_index == 0:
                    try:
                        step_index = int(raw_step_id)
                    except (ValueError, TypeError):
                        pass
                # Fallback 3: try step_index field directly as integer
                if step_index == 0:
                    si = event.get("step_index", event.get("stepIndex", 0))
                    if isinstance(si, int) and si > 0:
                        step_index = si
            if step_index <= 0:
                logger.warning(
                    f"[SeeCrab] plan_step_updated: unknown stepId={raw_step_id!r}, skipping"
                )
                return []
            status = event.get("status", "")
            return await self.aggregator.on_plan_step_updated(step_index, status)

        if etype == "plan_completed":
            return await self.aggregator.on_plan_completed()

        if etype == "ask_user":
            return [self._map_ask_user(event)]

        # Pre-built step_card from BP engine (delegate cards) — pass through
        if etype == "step_card":
            # Track delegation card step_id per agent_id for parent linking
            card_type = event.get("card_type", "")
            if card_type == "delegate":
                input_data = event.get("input", {})
                delegated_agent = (
                    input_data.get("agent_id", "") if isinstance(input_data, dict) else ""
                )
                if delegated_agent:
                    self._delegation_step_ids[delegated_agent] = event.get("step_id", "")
            return [event]

        # BP events — unified passthrough for all bp_* event types
        # Two formats: flat (from engine.advance() yield) and data-wrapped (from _emit_*())
        if etype.startswith("bp_"):
            if "data" in event and isinstance(event["data"], dict):
                # data wrapper format → flatten
                return [{"type": etype, **event["data"]}]
            else:
                # flat format → pass through directly
                return [event]

        if etype == "heartbeat":
            return [{"type": "heartbeat"}]

        if etype == "error":
            return [{"type": "error", "message": event.get("message", ""), "code": "agent_error"}]

        # Explicitly ignored event types (from engine, not relevant for SeeCrab):
        # - "done": engine done signal — adapter emits its own done
        # - "iteration_start": internal iteration counter
        # - "context_compressed": context window management
        # - "chain_text": IM-facing internal monologue
        # - "user_insert": IM gateway user injection
        # - "agent_handoff": multi-agent internal routing
        # - "tool_call_skipped": policy-denied tools
        return []

    def _handle_thinking(self, event: dict) -> list[dict]:
        events = []
        ttft = self.timer.check_ttft()
        if ttft:
            events.append(ttft)
            events.append(self.timer.make_event("total", "running"))
        events.append(
            {
                "type": "thinking",
                "content": event.get("content", ""),
                "agent_id": self._active_agent_id,
            }
        )
        return events

    async def _handle_text_delta(self, event: dict) -> list[dict]:
        events = []
        ttft = self.timer.check_ttft()
        if ttft:
            events.append(ttft)
            events.append(self.timer.make_event("total", "running"))
        # Close any active aggregation
        events += await self.aggregator.on_text_delta()
        events.append(
            {
                "type": "ai_text",
                "content": event.get("content", ""),
                "agent_id": self._active_agent_id,
            }
        )
        return events

    async def _handle_tool_call_start(self, event: dict) -> list[dict]:
        tool_name = event.get("tool", "")
        args = event.get("args", {})
        tool_id = event.get("id", "")
        fr = self.step_filter.classify(tool_name, args)
        # Sub-agent execution visibility:
        # even when global debug is off, we still want to show key file/shell actions
        # under delegated agents for better traceability.
        if (
            self._show_subagent_actions
            and
            self._active_agent_id != "main"
            and fr == FilterResult.HIDDEN
            and tool_name in self._subagent_visible_tools
        ):
            # 调试模式（STEP_FILTER_DEBUG_ENABLED=true）下，把原本会被隐藏的子 Agent 关键动作
            # 提升为可见步骤卡，便于排查委派执行过程。
            logger.info(
                "[SeeCrab] promote hidden sub-agent tool to visible: agent=%s tool=%s",
                self._active_agent_id,
                tool_name,
            )
            fr = FilterResult.WHITELIST
        events = await self.aggregator.on_tool_call_start(tool_name, args, tool_id, fr)
        # Normal delegation cards are created by aggregator from tool_call_start
        # (not raw "step_card" events), so we must also capture parent mapping here.
        if fr == FilterResult.AGENT_TRIGGER:
            delegated_agent = args.get("agent_id", "") if isinstance(args, dict) else ""
            if delegated_agent:
                for e in events:
                    if e.get("type") == "step_card" and e.get("card_type") == "delegate":
                        delegate_step_id = e.get("step_id", "")
                        self._delegation_step_ids[delegated_agent] = delegate_step_id
                        logger.info(
                            "[SeeCrab] delegation map set: delegated_agent=%s parent_step_id=%s tool=%s",
                            delegated_agent,
                            delegate_step_id,
                            tool_name,
                        )
                        break
        return events

    async def _handle_tool_call_end(self, event: dict) -> list[dict]:
        tool_name = event.get("tool", "")
        tool_id = event.get("id", "")
        result = event.get("result", "")
        is_error = event.get("is_error", False)
        events = await self.aggregator.on_tool_call_end(tool_name, tool_id, result, is_error)
        return events

    @staticmethod
    def _map_ask_user(event: dict) -> dict:
        """Map raw ask_user event (id→value)."""
        options = event.get("options", [])
        mapped = [
            {"label": o.get("label", ""), "value": o.get("id", o.get("value", ""))} for o in options
        ]
        return {
            "type": "ask_user",
            "ask_id": event.get("id", event.get("ask_id", "")),
            "question": event.get("question", ""),
            "options": mapped,
        }

    async def _merge_sources(self, raw_events, event_bus):
        """Merge raw_events + event_bus into a single async stream.

        When raw_events blocks (during delegation), event_bus items
        are still consumed.  After raw_events finishes, any remaining
        items in event_bus are drained before the stream ends.
        """
        merged = asyncio.Queue()

        async def _feed_raw():
            try:
                async for event in raw_events:
                    await merged.put(event)
                    # Yield control so _feed_bus can forward queued items
                    await asyncio.sleep(0)
            except Exception as e:
                logger.error(f"[SeeCrab] raw_events feeder error: {e}")
            finally:
                await merged.put(_STREAM_DONE)

        async def _feed_bus():
            try:
                while True:
                    event = await event_bus.get()
                    if event is _STREAM_DONE:
                        break
                    await merged.put(event)
            except asyncio.CancelledError:
                pass

        raw_task = asyncio.create_task(_feed_raw())
        bus_task = asyncio.create_task(_feed_bus())

        try:
            while True:
                item = await merged.get()
                if item is _STREAM_DONE:
                    # Drain any remaining event_bus items that arrived
                    # before raw_events finished
                    while not event_bus.empty():
                        try:
                            leftover = event_bus.get_nowait()
                            if leftover is not _STREAM_DONE:
                                yield leftover
                        except asyncio.QueueEmpty:
                            break
                    break
                yield item
        finally:
            bus_task.cancel()
            try:
                await bus_task
            except (asyncio.CancelledError, Exception):
                pass
            if not raw_task.done():
                raw_task.cancel()

    async def _handle_agent_switch(self, event: dict) -> list[dict]:
        """Handle agent switch: flush current aggregator, switch to new agent."""
        agent_id = event.get("agent_id", "main") or "sub_agent"
        logger.info("[SeeCrab] agent switch: from=%s to=%s", self._active_agent_id, agent_id)
        events: list[dict] = []
        # Flush current aggregator
        current_agg = self._aggregators.get(self._active_agent_id)
        if current_agg:
            events.extend(await current_agg.flush())
        # Get parent_step_id before creating new aggregator
        parent_step_id = self._delegation_step_ids.pop(agent_id, None) if agent_id != "main" else None
        if agent_id != "main":
            logger.info(
                "[SeeCrab] resolve parent step: agent_id=%s parent_step_id=%s",
                agent_id,
                parent_step_id or "",
            )
        # Switch aggregator (create if new)
        if agent_id not in self._aggregators:
            self._aggregators[agent_id] = StepAggregator(
                title_gen=self.title_gen,
                card_builder=self.card_builder,
                timer=self.timer,
                title_update_queue=self._title_queue,
                agent_id=agent_id,
                parent_step_id=parent_step_id,
                show_subagent_actions=self._show_subagent_actions,
            )
        self._active_agent_id = agent_id
        self.aggregator = self._aggregators[agent_id]
        # Emit delegation_context before agent_header so frontend links sub-agent cards to parent
        if parent_step_id:
            logger.info(
                "[SeeCrab] emit delegation_context: agent_id=%s parent_step_id=%s",
                agent_id,
                parent_step_id,
            )
            events.append(
                {
                    "type": "delegation_context",
                    "parent_step_id": parent_step_id,
                    "agent_id": agent_id,
                }
            )
        # Pass through agent_header to frontend
        events.append(
            {
                "type": "agent_header",
                "agent_id": agent_id,
                "agent_name": event.get("agent_name", agent_id),
                "agent_description": event.get("agent_description", ""),
            }
        )
        return events

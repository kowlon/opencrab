"""
BPEngine — BP 子任务执行引擎 (streaming API)。

核心设计:
- advance() async generator 驱动子任务执行，yield SSE events
- auto 模式连续执行，manual 模式执行一轮后暂停
- 首个子任务输入从 initial_input 获取 (M8)
- 执行前检查 input_schema required 字段完整性
- SubAgent 上下文隔离: session_messages=[] (C-1)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from ..models import (
    BPStatus,
    RunMode,
    SubtaskStatus,
    collect_all_properties,
    collect_all_upstream,
)
from .event_formatter import BPEventFormatter

if TYPE_CHECKING:
    from ..models import BestPracticeConfig, SubtaskConfig
    from .state_manager import BPStateManager

logger = logging.getLogger(__name__)

_PARTIAL_RESULT_ITEM_LIMIT = 2000
_PARTIAL_RESULTS_TOTAL_LIMIT = 8000

# JSONSchema 类型 → 用户友好中文名（共享给 LLM prompt 路径与 template 路径）
_TYPE_LABELS: dict[str, str] = {
    "string": "文本", "number": "数字", "integer": "整数",
    "boolean": "是/否", "array": "列表", "object": "JSON 对象",
}

# 从 description 中提取示例的正则: 匹配"如'xxx'" / "如：xxx" / "例如xxx" / "例如：xxx"
# 避免句中孤立"如"误匹配（如必须紧跟引号或冒号）
_EXAMPLE_PATTERN = re.compile(
    r"(?:例如[：:]?|如[：:]|如(?=['\"\u2018\u201c]))\s*"
    r"['\"\u2018\u201c]?([^'\"\u2019\u201d，。）)\n]+)"
)


class BPEngine:
    def __init__(
        self,
        state_manager: BPStateManager,
    ) -> None:
        self.state_manager = state_manager
        self._orchestrator = None

    # ── Orchestrator injection ────────────────────────────────

    def set_orchestrator(self, orchestrator) -> None:
        """由 facade 或 server.py 在启动时注入。"""
        self._orchestrator = orchestrator

    def _get_orchestrator(self):
        """获取 orchestrator，fallback 到全局实例。"""
        if self._orchestrator:
            return self._orchestrator
        try:
            import seeagent.main
            return getattr(seeagent.main, "_orchestrator", None)
        except ImportError:
            return None

    def _get_scheduler(self, bp_config, snap):
        """工厂方法: 根据 config 返回合适的 scheduler。"""
        from .scheduler import LinearScheduler
        return LinearScheduler(bp_config, snap)

    def _get_config(self, snap):
        """获取实例对应的 BP 配置。"""
        if snap.bp_config:
            return snap.bp_config
        try:
            from ..facade import get_bp_config_loader
            loader = get_bp_config_loader()
            if loader and loader.configs:
                return loader.configs.get(snap.bp_id)
            return None
        except Exception:
            return None

    # ── Core execution (new: async generator) ────────────────────

    # ── High-level operations (called by handler) ───────────────

    async def start(
        self,
        bp_config: Any,
        session: Any,
        input_data: dict[str, Any] | None = None,
        run_mode: RunMode = RunMode.MANUAL,
    ) -> AsyncIterator[dict]:
        """Create a BP instance, handle suspension of old, emit events, advance first subtask.

        Yields: bp_instance_created, then all events from advance().
        """
        existing = self.state_manager.get_active(session.id)
        if existing:
            await self.request_suspend(
                existing.instance_id, session, "start", pending_target_id="",
            )
            old_name = existing.bp_config.name if existing.bp_config else existing.bp_id
            logger.info(
                f"[BP] Suspended existing instance {existing.instance_id} "
                f"({old_name}) to start {bp_config.id}"
            )

        logger.debug(
            f"[BP] bp_start: bp_id={bp_config.id}, "
            f"session_id={session.id}, run_mode={run_mode.value}"
        )
        inst_id = self.state_manager.create_instance(
            bp_config, session.id, initial_input=input_data or {}, run_mode=run_mode,
        )
        # Backfill target_instance_id on pending switch
        pending = self.state_manager._pending_switches.get(session.id)
        if pending and not pending.target_instance_id:
            pending.target_instance_id = inst_id

        # Pre-distribute initial_input fields to downstream subtasks
        self._distribute_initial_input(inst_id, bp_config)

        await self.state_manager.persist_instance(inst_id)
        self.state_manager.persist_to_session(inst_id, session)

        yield {
            "type": "bp_instance_created",
            "instance_id": inst_id,
            "bp_id": bp_config.id,
            "bp_name": bp_config.name,
            "run_mode": run_mode.value,
            "subtasks": [
                {"id": s.id, "name": s.name} for s in bp_config.subtasks
            ],
        }

        async for event in self.advance(inst_id, session):
            yield event

        self.state_manager.persist_to_session(inst_id, session)

    async def request_suspend(
        self,
        instance_id: str,
        session: Any,
        reason: str,
        pending_target_id: str | None = None,
    ) -> bool:
        """Suspend an active instance and persist the cancellation state."""
        from ..models import PendingContextSwitch

        snap = self.state_manager.get(instance_id)
        if not snap:
            return False

        self.state_manager.suspend(instance_id)
        await self.state_manager.persist_status_change(instance_id)

        if pending_target_id is not None:
            self.state_manager.set_pending_switch(
                snap.session_id,
                PendingContextSwitch(
                    suspended_instance_id=instance_id,
                    target_instance_id=pending_target_id,
                ),
            )

        ctx = getattr(session, "context", None) if session else None
        if ctx is not None:
            ctx._bp_cancelled_instance = instance_id
            dt = getattr(ctx, "_bp_delegate_task", None)
            if dt and not dt.done():
                dt.cancel()

        logger.info(
            f"[BP] request_suspend: instance={instance_id} "
            f"reason={reason} pending_target={pending_target_id or ''}"
        )
        self.state_manager.persist_to_session(instance_id, session)
        return True

    async def resume_if_needed(self, instance_id: str, session: Any) -> dict[str, Any]:
        """Resume a suspended instance when safe, or report the conflict."""
        snap = self.state_manager.get(instance_id)
        if not snap:
            logger.warning(f"[BP] resume_if_needed: instance not found id={instance_id}")
            return {"success": False, "error": "Instance not found", "code": "not_found"}

        ctx = getattr(session, "context", None) if session else None
        current_active = self.state_manager.get_active(snap.session_id)
        if snap.status == BPStatus.SUSPENDED:
            if current_active and current_active.instance_id != instance_id:
                logger.info(
                    f"[BP] resume_if_needed: conflict instance={instance_id} "
                    f"active={current_active.instance_id}"
                )
                return {
                    "success": False,
                    "error": "Another BP instance is already active",
                    "code": "conflict",
                    "active_instance_id": current_active.instance_id,
                }
            self.state_manager.resume(instance_id)
            await self.state_manager.persist_status_change(instance_id)
            if ctx is not None and getattr(ctx, "_bp_cancelled_instance", None) == instance_id:
                ctx._bp_cancelled_instance = None
            self.state_manager.persist_to_session(instance_id, session)
            logger.info(
                f"[BP] resume_if_needed: resumed instance={instance_id} "
                f"bp_id={snap.bp_id}"
            )
            return {"success": True, "resumed": True}

        if current_active and current_active.instance_id != instance_id:
            logger.info(
                f"[BP] resume_if_needed: conflict instance={instance_id} "
                f"active={current_active.instance_id}"
            )
            return {
                "success": False,
                "error": "Another BP instance is already active",
                "code": "conflict",
                "active_instance_id": current_active.instance_id,
            }

        if ctx is not None and getattr(ctx, "_bp_cancelled_instance", None) == instance_id:
            ctx._bp_cancelled_instance = None
        logger.debug(f"[BP] resume_if_needed: already active instance={instance_id}")
        return {"success": True, "resumed": False}

    async def switch(self, target_id: str, session: Any) -> dict[str, Any]:
        """Switch active instance. Returns result metadata dict."""
        from ..models import PendingContextSwitch

        target = self.state_manager.get(target_id)
        if not target:
            return {"success": False, "error": f"BP instance {target_id} not found"}

        current_active = self.state_manager.get_active(session.id)
        current_id = current_active.instance_id if current_active else ""

        if current_id == target_id:
            return {"success": False, "already_active": True}

        if current_id:
            await self.request_suspend(
                current_id, session, "switch", pending_target_id=target_id,
            )
        else:
            self.state_manager.set_pending_switch(
                session.id,
                PendingContextSwitch(
                    suspended_instance_id="",
                    target_instance_id=target_id,
                ),
            )
        self.state_manager.resume(target_id)
        await self.state_manager.persist_status_change(target_id)
        self.state_manager.persist_to_session(target_id, session)
        return {"success": True, "target_id": target_id}

    async def cancel(
        self, instance_id: str, session: Any,
    ) -> AsyncIterator[dict]:
        """Cancel instance, clean up delegate task, yield bp_cancelled."""
        snap = self.state_manager.get(instance_id)
        if not snap:
            return

        bp_name = snap.bp_config.name if snap.bp_config else snap.bp_id
        self.state_manager.cancel(instance_id)
        self.state_manager.set_cooldown(snap.session_id)
        await self.state_manager.persist_status_change(instance_id)

        # Cancel running delegate task if any
        if session and hasattr(session, "context"):
            dt = getattr(session.context, "_bp_delegate_task", None)
            if dt and not dt.done():
                dt.cancel()

        yield {
            "type": "bp_cancelled",
            "instance_id": instance_id,
            "bp_name": bp_name,
        }
        self.state_manager.persist_to_session(instance_id, session)

    # ── Core execution ─────────────────────────────────────────

    async def advance(
        self, instance_id: str, session: Any,
    ) -> AsyncIterator[dict]:
        """Execute the next ready subtask(s) and yield SSE events.

        This is the core async generator that replaces execute_subtask() for
        the new streaming architecture. It does NOT yield a final ``done``
        event (R2).

        Manual mode: executes one subtask then yields ``bp_waiting_next``.
        Auto mode: loops through all remaining subtasks until completion.
        """
        snap = self.state_manager.get(instance_id)
        if not snap:
            yield {"type": "error", "message": f"BP instance {instance_id} not found"}
            return

        bp_config = self._get_config(snap)
        if not bp_config:
            yield {"type": "error", "message": f"BP config not found for {snap.bp_id}"}
            return

        logger.debug(
            f"[BP] advance() entry: instance={instance_id} "
            f"idx={snap.current_subtask_index} statuses={snap.subtask_statuses}"
        )

        scheduler = self._get_scheduler(bp_config, snap)

        # Gap 1: yield initial progress so TaskProgressCard is visible immediately
        yield self._build_progress_event(instance_id, snap, bp_config)

        while True:
            # Check if instance was suspended (e.g. user started a new BP)
            snap = self.state_manager.get(instance_id)
            if not snap or snap.status == BPStatus.SUSPENDED:
                yield {
                    "type": "bp_suspended",
                    "instance_id": instance_id,
                }
                return

            # Keep scheduler snapshot reference in sync with state_manager.
            # If state_manager restores/overwrites the in-memory snapshot object (e.g. after restart),
            # a scheduler that still points to the old object would update the wrong snapshot and
            # persistence would appear "rolled back" on the next /bp/next.
            if getattr(scheduler, "_snap", None) is not snap:
                logger.warning(
                    f"[BP] scheduler snapshot drift detected; resyncing "
                    f"instance={instance_id}"
                )
                scheduler._snap = snap

            logger.debug(
                f"[BP] advance() loop: instance={instance_id} "
                f"idx={snap.current_subtask_index}"
            )

            ready = scheduler.get_ready_tasks()
            if not ready:
                # No tasks ready — might already be done
                if scheduler.is_done():
                    logger.info(
                        f"[BP] advance: completed instance={instance_id} "
                        f"bp_id={bp_config.id}"
                    )
                    self.state_manager.complete(instance_id)
                    await self.state_manager.persist_status_change(instance_id)
                    await self._persist_state(instance_id, session)
                    yield self._build_bp_complete_event(instance_id, snap, bp_config)
                return

            for subtask in ready:
                # Quick path: check input completeness
                input_data = scheduler.resolve_input(subtask.id)
                output_schema = scheduler.derive_output_schema(subtask.id)
                missing, matched_schema = self._check_input_completeness(subtask, input_data)
                if missing:
                    logger.info(
                        f"[BP] advance: waiting_input instance={instance_id} "
                        f"subtask={subtask.id} missing={missing}"
                    )
                    self.state_manager.update_subtask_status(
                        instance_id, subtask.id, SubtaskStatus.WAITING_INPUT,
                    )
                    from seeagent.config import settings
                    mode = getattr(settings, "bp_ask_user_mode", "card")

                    event: dict[str, Any] = {
                        "type": "bp_ask_user",
                        "mode": mode,
                        "instance_id": instance_id,
                        "subtask_id": subtask.id,
                        "subtask_name": subtask.name,
                        "missing_fields": missing,
                        "input_schema": matched_schema or subtask.input_schema,
                    }
                    if mode == "message":
                        event["message"] = await self._build_ask_user_nl_message(
                            subtask, missing, matched_schema or subtask.input_schema
                        )
                    yield event
                    return

                # Mark CURRENT and yield subtask_start
                self.state_manager.update_subtask_status(
                    instance_id, subtask.id, SubtaskStatus.CURRENT,
                )
                await self.state_manager.persist_subtask_progress(instance_id)
                yield {
                    "type": "bp_subtask_start",
                    "instance_id": instance_id,
                    "subtask_id": subtask.id,
                    "subtask_name": subtask.name,
                }

                delegate_step_id = f"delegate_{subtask.id}"
                delegate_start = time.monotonic()

                # Execute via _run_subtask_stream with error handling (R20)
                # Delegate card is yielded INSIDE _run_subtask_stream after
                # thinking events so that thinking appears first in timeline.
                output = None
                raw_result_text = ""
                tool_results_list: list[str] = []
                received_internal_output = False
                subtask_failed = False
                try:
                    async for event in self._run_subtask_stream(
                        instance_id, subtask, input_data, bp_config, session,
                        delegate_step_id=delegate_step_id,
                    ):
                        if event.get("type") == "_internal_output":
                            received_internal_output = True
                            output = event.get("data", {})
                            raw_result_text = event.get("raw_result", "")
                            tool_results_list = event.get("tool_results", [])
                        elif event.get("type") == "bp_ask_user":
                            self.state_manager.update_subtask_status(
                                instance_id, subtask.id, SubtaskStatus.WAITING_INPUT,
                            )
                            yield event
                            return
                        else:
                            if event.get("type") in ("error", "bp_error"):
                                subtask_failed = True
                            # Passthrough other events to the caller
                            yield event
                except asyncio.CancelledError:
                    logger.info(f"[BP] Subtask {subtask.id} cancelled (instance suspended)")
                    return
                except Exception as exc:
                    logger.error(
                        f"[BP] Subtask {subtask.id} failed: {exc}", exc_info=True,
                    )
                    self.state_manager.update_subtask_status(
                        instance_id, subtask.id, SubtaskStatus.FAILED,
                    )
                    yield {
                        "type": "bp_error",
                        "instance_id": instance_id,
                        "subtask_id": subtask.id,
                        "error": str(exc),
                    }
                    return

                if subtask_failed or not received_internal_output:
                    self.state_manager.update_subtask_status(
                        instance_id, subtask.id, SubtaskStatus.FAILED,
                    )
                    return

                # Gap 5: yield delegate card (completed)
                delegate_duration = round(time.monotonic() - delegate_start, 1)
                yield {
                    "type": "step_card",
                    "step_id": delegate_step_id,
                    "title": f"委派 {subtask.agent_profile}: {subtask.name}",
                    "status": "completed",
                    "source_type": "tool",
                    "card_type": "delegate",
                    "agent_id": "main",
                    "delegate_agent_id": subtask.agent_profile,
                    "subtask_id": subtask.id,
                    "duration": delegate_duration,
                }

                # Subtask completed successfully
                if output is None:
                    output = {}

                # Conform output to next subtask's input_schema via LLM
                output = await self._conform_output(
                    output, output_schema, raw_result_text, tool_results_list,
                )

                scheduler.complete_task(subtask.id, output)
                logger.debug(
                    f"[BP] complete_task: subtask={subtask.id} "
                    f"idx={snap.current_subtask_index}"
                )

                # Save raw result for precise context restoration on resume
                if raw_result_text:
                    snap.subtask_raw_outputs[subtask.id] = raw_result_text[:8000]
                # Clear partial results (subtask completed successfully)
                snap.subtask_partial_results.pop(subtask.id, None)

                await self.state_manager.persist_subtask_output(instance_id, subtask.id)
                await self.state_manager.persist_subtask_progress(instance_id)
                await self._persist_state(instance_id, session)

                yield {
                    "type": "bp_subtask_complete",
                    "instance_id": instance_id,
                    "subtask_id": subtask.id,
                    "subtask_name": subtask.name,
                    "output": output,
                    "output_schema": scheduler.derive_output_schema(subtask.id),
                    "summary": (
                        self._extract_summary_from_result(raw_result_text, output)
                        if raw_result_text
                        else self._extract_summary(output)
                    ),
                }
                yield self._build_progress_event(instance_id, snap, bp_config)

            # After processing ready tasks, check if done
            if scheduler.is_done():
                self.state_manager.complete(instance_id)
                await self.state_manager.persist_status_change(instance_id)
                await self._persist_state(instance_id, session)
                yield self._build_bp_complete_event(instance_id, snap, bp_config)
                return

            # Manual mode: stop after one round, yield waiting_next
            if snap.run_mode == RunMode.MANUAL:
                yield {
                    "type": "bp_waiting_next",
                    "instance_id": instance_id,
                    "next_subtask_index": snap.current_subtask_index,
                }
                return

            # Auto mode: continue the while loop to pick up next ready tasks

    # ── advance() helpers ──────────────────────────────────────

    def _build_bp_complete_event(
        self, instance_id: str, snap: Any, bp_config: BestPracticeConfig,
    ) -> dict:
        """Build the bp_complete SSE event dict."""
        return {
            "type": "bp_complete",
            "instance_id": instance_id,
            "bp_id": bp_config.id,
            "bp_name": bp_config.name,
            "outputs": dict(snap.subtask_outputs),
        }

    def _build_progress_event(
        self, instance_id: str, snap: Any, bp_config: BestPracticeConfig,
    ) -> dict:
        """Build a bp_progress SSE event dict."""
        return {
            "type": "bp_progress",
            "instance_id": instance_id,
            "bp_name": bp_config.name,
            "statuses": dict(snap.subtask_statuses),
            "subtasks": [
                {"id": st.id, "name": st.name}
                for st in bp_config.subtasks
            ],
            "current_subtask_index": snap.current_subtask_index,
            "run_mode": snap.run_mode.value if isinstance(snap.run_mode, RunMode) else snap.run_mode,
            "status": snap.status.value if hasattr(snap.status, "value") else str(snap.status),
        }

    async def _persist_state(self, instance_id: str, session: Any) -> None:
        """Persist BP state to session metadata."""
        self.state_manager.persist_to_session(instance_id, session)

    @staticmethod
    def _extract_summary(output: dict) -> str:
        """Extract a short summary from subtask output."""
        if not output:
            return ""
        import json as _json
        keys = list(output.keys())
        preview = _json.dumps(output, ensure_ascii=False)[:200]
        return f"fields: {', '.join(keys)} | {preview}"

    # ── Subtask streaming execution ─────────────────────────────

    async def _run_subtask_stream(
        self,
        instance_id: str,
        subtask: SubtaskConfig,
        input_data: dict[str, Any],
        bp_config: BestPracticeConfig,
        session: Any,
        *,
        delegate_step_id: str = "",
    ) -> AsyncIterator[dict]:
        """Execute a single subtask, yield SubAgent streaming events.

        Uses orchestrator.delegate() + temporary event_bus to capture streaming
        events. The final output is yielded as an ``_internal_output`` event.

        The delegate card (running) is yielded AFTER thinking events so that
        thinking blocks appear before the delegate card in the timeline.

        R17: delegate_task is exposed on session.context._bp_delegate_task for
        disconnect watcher cancellation.
        """
        orchestrator = self._get_orchestrator()
        if not orchestrator:
            yield {"type": "error", "message": "Orchestrator not available"}
            return

        # Derive output schema for this subtask
        snap = self.state_manager.get(instance_id)
        scheduler = self._get_scheduler(bp_config, snap)
        output_schema = scheduler.derive_output_schema(subtask.id)

        # Build delegation message (pass snap for partial results injection)
        message = self._build_delegation_message(
            bp_config, subtask, input_data, output_schema, snap=snap,
        )

        # Temporary event_bus to capture SubAgent streaming events
        event_bus: asyncio.Queue = asyncio.Queue()
        old_bus = None
        old_thinking_mode = None
        if hasattr(session, "context"):
            old_bus = getattr(session.context, "_sse_event_bus", None)
            session.context._sse_event_bus = event_bus
        # Enable thinking for sub-agent so frontend can display thinking blocks
        if hasattr(session, "metadata"):
            old_thinking_mode = session.metadata.get("thinking_mode")
            session.metadata["thinking_mode"] = "on"

        try:
            # Launch SubAgent (non-blocking)
            delegate_task = asyncio.create_task(
                orchestrator.delegate(
                    session=session,
                    from_agent="bp_engine",
                    to_agent=subtask.agent_profile,
                    message=message,
                    reason=f"BP:{bp_config.name} / {subtask.name}",
                    session_messages=[],  # Context isolation
                )
            )
            # R17: expose delegate_task for disconnect watcher cancellation
            if hasattr(session, "context"):
                session.context._bp_delegate_task = delegate_task

            # Initialize event formatter (encapsulates step card pipeline)
            fmt = BPEventFormatter(
                agent_profile=subtask.agent_profile,
                subtask_name=subtask.name,
                instance_id=instance_id,
                subtask_id=subtask.id,
                delegate_step_id=delegate_step_id,
            )

            # Accumulate tool call results for richer output extraction
            tool_results: list[str] = []

            while True:
                try:
                    event = await asyncio.wait_for(event_bus.get(), timeout=1.0)
                except TimeoutError:
                    # Check session-level cancellation signal (fastest path)
                    _cancelled_id = getattr(
                        getattr(session, "context", None),
                        "_bp_cancelled_instance", None,
                    )
                    # Check state_manager suspended status (fallback)
                    _snap = self.state_manager.get(instance_id)
                    _suspended = _snap and _snap.status == BPStatus.SUSPENDED
                    if _cancelled_id == instance_id or _suspended:
                        if _cancelled_id == instance_id:
                            logger.info(
                                f"[BP] Detected cancel signal for {instance_id}"
                            )
                        if _snap and tool_results:
                            _snap.subtask_partial_results[subtask.id] = list(
                                tool_results
                            )
                            self._persist_state(instance_id, session)
                        if not delegate_task.done():
                            delegate_task.cancel()
                        break
                    if delegate_task.done():
                        break
                    continue

                etype = event.get("type")
                if etype == "done":
                    continue

                # Per-event suspension check: catches cancellation even when events
                # stream so rapidly that asyncio.TimeoutError never fires.
                _snap_chk = self.state_manager.get(instance_id)
                _cid_chk = getattr(
                    getattr(session, "context", None), "_bp_cancelled_instance", None,
                )
                if _cid_chk == instance_id or (
                    _snap_chk and _snap_chk.status == BPStatus.SUSPENDED
                ):
                    logger.info(
                        f"[BP] Detected cancel mid-stream: instance={instance_id} subtask={subtask.id}"
                    )
                    if not delegate_task.done():
                        delegate_task.cancel()
                    break

                # Track sub-agent identity
                if etype == "agent_header":
                    fmt.on_agent_header(event)
                    continue

                # Forward thinking content BEFORE delegate card
                if etype == "thinking_delta":
                    yield fmt.make_thinking_event(event)
                    continue

                if etype in ("thinking_start", "thinking_end"):
                    continue

                # For any non-thinking event, ensure delegate card is yielded first
                for ev in fmt.ensure_delegate_card():
                    yield ev

                # Tool call start → filter + aggregate
                if etype == "tool_call_start":
                    for ev in await fmt.on_tool_call_start(event):
                        yield ev
                    continue

                # Tool call end → update aggregated card
                if etype == "tool_call_end":
                    result = event.get("result", "")
                    is_error = event.get("is_error", False)
                    if not is_error and result:
                        tool_results = self._append_budgeted_partial_result(
                            tool_results, result,
                        )
                    for ev in await fmt.on_tool_call_end(event):
                        yield ev
                    continue

                # Text delta → close any active aggregation
                if etype == "text_delta":
                    for ev in await fmt.on_text_delta():
                        yield ev
                    continue

                # Pass through pre-built step_card events (e.g. from nested delegates)
                if etype == "step_card":
                    yield event
                    continue

            # Ensure delegate card was yielded (edge case: only thinking events)
            for ev in fmt.ensure_delegate_card():
                yield ev

            # Flush any pending aggregation
            for ev in await fmt.flush():
                yield ev

            # Get final result (may have been cancelled due to suspend)
            logger.info(f"[BP] awaiting delegate_task cancel: instance={instance_id} subtask={subtask.id}")
            try:
                raw_result = await asyncio.wait_for(delegate_task, timeout=5.0)
            except TimeoutError:
                logger.warning(
                    f"[BP] delegate_task cancel timed out: instance={instance_id} subtask={subtask.id}"
                )
                return
            except asyncio.CancelledError:
                logger.info(f"[BP] delegate_task cancelled done: instance={instance_id} subtask={subtask.id}")
                return
            output = self._parse_output(raw_result)
            yield {
                "type": "_internal_output",
                "data": output,
                "raw_result": raw_result,
                "tool_results": tool_results,
            }

        finally:
            # Cancel the delegate task if still running (e.g. user cancelled BP)
            if not delegate_task.done():
                delegate_task.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(delegate_task), timeout=3.0,
                    )
                except (TimeoutError, asyncio.CancelledError, Exception):
                    pass
            if hasattr(session, "context"):
                # Another request may have already attached a fresh event bus or
                # delegate task to the same session. Only restore/clear the
                # handles if this stream still owns them.
                if getattr(session.context, "_sse_event_bus", None) is event_bus:
                    session.context._sse_event_bus = old_bus
                if getattr(session.context, "_bp_delegate_task", None) is delegate_task:
                    session.context._bp_delegate_task = None
            if hasattr(session, "metadata"):
                if old_thinking_mode is not None:
                    session.metadata["thinking_mode"] = old_thinking_mode
                else:
                    session.metadata.pop("thinking_mode", None)

    # ── Answer (user response to bp_ask_user) ─────────────────

    async def answer(
        self,
        instance_id: str,
        subtask_id: str,
        data: dict,
        session: Any,
    ) -> AsyncIterator[dict]:
        """Handle ask_user answer: merge supplemented data, re-execute subtask."""
        snap = self.state_manager.get(instance_id)
        if not snap:
            yield {"type": "error", "message": "Instance not found"}
            return

        resume = await self.resume_if_needed(instance_id, session)
        if not resume.get("success"):
            yield {
                "type": "error",
                "message": resume.get("error", "Failed to resume BP instance"),
                "code": resume.get("code", "bp_resume_failed"),
                "active_instance_id": resume.get("active_instance_id"),
            }
            return

        # Merge supplemented data into dedicated field (don't pollute subtask_outputs)
        existing = snap.supplemented_inputs.get(subtask_id, {})
        existing.update(data)
        snap.supplemented_inputs[subtask_id] = existing

        # Reset subtask status to PENDING to allow re-execution
        self.state_manager.update_subtask_status(
            instance_id, subtask_id, SubtaskStatus.PENDING,
        )
        await self.state_manager.persist_supplemented_input(instance_id, subtask_id)
        await self.state_manager.persist_subtask_progress(instance_id)

        # Reuse advance() flow
        async for event in self.advance(instance_id, session):
            yield event

    # ── Delegation message ──────────────────────────────────────

    def _build_delegation_message(
        self,
        bp_config: BestPracticeConfig,
        subtask: SubtaskConfig,
        input_data: dict[str, Any],
        output_schema: dict[str, Any] | None,
        snap: Any = None,
    ) -> str:
        schema_hint = self._schema_to_example(output_schema) if output_schema else (
            "由你自行决定合适的输出格式"
        )

        partial_section = ""
        if snap:
            partial = snap.subtask_partial_results.get(subtask.id)
            if partial:
                partial_section = self._format_partial_results(partial)

        return (
            f"## 最佳实践任务: {bp_config.name}\n"
            f"### 当前子任务: {subtask.name}\n"
            f"{subtask.description or ''}\n\n"
            f"{partial_section}"
            f"### 输入数据\n```json\n"
            f"{json.dumps(input_data, ensure_ascii=False, indent=2)}\n```\n\n"
            f"### 输出格式要求\n\n"
            f"请严格按以下格式输出（先写总结再写 JSON）:\n\n"
            f"**总结**: [用1-2句话简洁描述本子任务的执行结果和关键发现]\n\n"
            f"```json\n{schema_hint}\n```\n\n"
            f"## 限制\n"
            f"- **禁止使用 `create_plan`、`update_plan_step` 等任何计划相关工具**。当前任务已经是拆分好的单个子任务，请直接执行具体操作即可\n"
            f"- 禁止使用 ask_user 工具，所有信息已在输入数据中提供\n"
            f"- JSON 必须严格符合输出格式要求，包含上述所有字段\n"
            f"- **总结**行必须在 JSON 代码块之前\n"
            f"- 不要把最终输出的 JSON 存入文件，必须直接在回复中以代码块形式输出。如果你需要生成其他产物文件（如图片、报告），请正常生成并在 JSON 中返回文件路径"
        )

    @staticmethod
    def _format_partial_results(partial: list[str]) -> str:
        """Format partial tool results as a continuation section."""
        lines = ["### 已完成进展\n"]
        lines.append("本子任务之前被中断，以下是已完成的部分结果，请基于这些结果继续:\n")
        for i, result in enumerate(partial, 1):
            truncated = result[:2000]
            lines.append(f"**结果 {i}:**\n{truncated}\n")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _schema_to_example(schema: dict[str, Any]) -> str:
        """Convert a JSON Schema to an example JSON template with placeholders."""
        branches = schema.get("oneOf") or schema.get("anyOf")
        if branches:
            examples = []
            for i, branch in enumerate(branches):
                title = branch.get("title", f"分支 {i+1}")
                examples.append(f"// {title}\n{BPEngine._schema_to_example(branch)}")
            return "\n\n或\n\n".join(examples)

        props = schema.get("properties", {})
        if not props:
            return json.dumps(schema, ensure_ascii=False, indent=2)

        example: dict[str, Any] = {}
        for key, spec in props.items():
            ptype = spec.get("type", "string") if isinstance(spec, dict) else "string"
            desc = spec.get("description", "") if isinstance(spec, dict) else ""
            if ptype == "array":
                items = spec.get("items", {}) if isinstance(spec, dict) else {}
                itype = items.get("type", "string") if isinstance(items, dict) else "string"
                if itype == "object":
                    example[key] = [{"...": desc or "object items"}]
                else:
                    example[key] = [desc or f"{itype} items"]
            elif ptype == "object":
                example[key] = {"...": desc or "object"}
            elif ptype == "number" or ptype == "integer":
                example[key] = 0
            elif ptype == "boolean":
                example[key] = True
            else:
                example[key] = desc or f"<{ptype}>"

        required = schema.get("required", [])
        lines = json.dumps(example, ensure_ascii=False, indent=2)
        if required:
            lines += f"\n// 必填字段: {', '.join(required)}"
        return lines

    @staticmethod
    def _stringify_partial_result(result: Any) -> str:
        if isinstance(result, str):
            return result
        try:
            return json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(result)

    @classmethod
    def _append_budgeted_partial_result(
        cls, partials: list[str], result: Any,
    ) -> list[str]:
        text = cls._stringify_partial_result(result).strip()
        if not text:
            return partials

        candidate = text[:_PARTIAL_RESULT_ITEM_LIMIT]
        total = sum(len(item) for item in partials)
        if total >= _PARTIAL_RESULTS_TOTAL_LIMIT:
            return partials

        remaining = _PARTIAL_RESULTS_TOTAL_LIMIT - total
        if len(candidate) > remaining:
            candidate = candidate[:remaining]
        if not candidate:
            return partials
        return partials + [candidate]

    # ── Chat-to-Edit ───────────────────────────────────────────

    def handle_edit_output(
        self,
        instance_id: str,
        subtask_id: str,
        changes: dict[str, Any],
        bp_config: BestPracticeConfig,
        *,
        target_type: str = "output",
    ) -> dict[str, Any]:
        """编辑子任务输入/输出或最终输出，并更新后续执行起点。"""
        snap = self.state_manager.get(instance_id)
        if not snap:
            return {"success": False, "error": f"instance {instance_id} 不存在"}

        if not bp_config.subtasks:
            return {"success": False, "error": "BP 未配置子任务"}

        target_type = (target_type or "output").strip().lower()
        if target_type not in {"input", "output", "final_output"}:
            return {"success": False, "error": f"不支持的 target_type: {target_type}"}

        target_subtask_id = self._resolve_edit_target_subtask_id(
            subtask_id, bp_config, target_type,
        )
        target_subtask = next(
            (subtask for subtask in bp_config.subtasks if subtask.id == target_subtask_id),
            None,
        )
        if not target_subtask:
            return {"success": False, "error": f"子任务 {target_subtask_id} 不存在"}

        scheduler = self._get_scheduler(bp_config, snap)

        if target_type == "input":
            merged = self.state_manager.merge_supplemented_input(
                instance_id, target_subtask_id, changes,
            )
            target_idx = next(
                (i for i, subtask in enumerate(bp_config.subtasks) if subtask.id == target_subtask_id),
                -1,
            )
            if target_idx == 0:
                snap.initial_input = self.state_manager._deep_merge(snap.initial_input, changes)
                self._distribute_initial_input(instance_id, bp_config)

            resolved = scheduler.resolve_input(target_subtask_id)
            invalidation = self.state_manager.invalidate_from_subtask(
                instance_id,
                target_subtask_id,
                bp_config,
            )
            if not invalidation.get("success"):
                return invalidation
            warning = self._validate_input_soft(target_subtask, resolved)
            result: dict[str, Any] = {
                "success": True,
                "target_type": target_type,
                "target_subtask_id": target_subtask_id,
                "merged": merged,
                "resolved": resolved,
                **invalidation,
            }
        else:
            if target_subtask_id not in snap.subtask_outputs:
                return {"success": False, "error": f"子任务 {target_subtask_id} 无输出可编辑"}

            merged = self.state_manager.merge_subtask_output(
                instance_id, target_subtask_id, changes,
            )
            warning = self._validate_output_soft(merged, target_subtask_id, bp_config)
            target_idx = next(
                (i for i, subtask in enumerate(bp_config.subtasks) if subtask.id == target_subtask_id),
                -1,
            )
            next_idx = target_idx + 1
            if next_idx < len(bp_config.subtasks):
                invalidation = self.state_manager.invalidate_from_subtask(
                    instance_id,
                    bp_config.subtasks[next_idx].id,
                    bp_config,
                )
                if not invalidation.get("success"):
                    return invalidation
            else:
                invalidation = {
                    "success": True,
                    "stale_subtasks": [],
                    "invalidated_subtasks": [],
                    "rerun_from_subtask_id": None,
                    "rerun_from_index": snap.current_subtask_index,
                }
            result = {
                "success": True,
                "target_type": target_type,
                "target_subtask_id": target_subtask_id,
                "merged": merged,
                **invalidation,
            }

        # COMPLETED → ACTIVE 重激活：若 BP 已完成且本次编辑触发了重执行，
        # 需要把实例状态改回 ACTIVE，否则 get_active() 找不到它，bp_next 无法定位。
        rerun_from_idx = invalidation.get("rerun_from_index")
        if (
            snap.status == BPStatus.COMPLETED
            and rerun_from_idx is not None
            and rerun_from_idx < len(bp_config.subtasks)
        ):
            snap.status = BPStatus.ACTIVE
            snap.completed_at = None
            result["reactivated"] = True
            logger.info(
                f"[BP] handle_edit_output: COMPLETED→ACTIVE instance={instance_id} "
                f"rerun_from_index={rerun_from_idx}"
            )

        if warning:
            result["warning"] = warning
        return result

    @staticmethod
    def _resolve_edit_target_subtask_id(
        subtask_id: str,
        bp_config: BestPracticeConfig,
        target_type: str,
    ) -> str:
        if target_type == "final_output":
            return bp_config.subtasks[-1].id
        return subtask_id

    # ── Input pre-distribution ──────────────────────────────────

    def _distribute_initial_input(
        self, instance_id: str, bp_config: BestPracticeConfig,
    ) -> None:
        """Pre-fill downstream subtasks' supplemented_inputs from initial_input.

        For each subtask beyond the first, identify non-upstream fields.
        If any of those fields exist in initial_input, copy them to
        snap.supplemented_inputs[subtask_id].
        """
        snap = self.state_manager.get(instance_id)
        if not snap or not snap.initial_input:
            return

        initial = snap.initial_input

        for idx, subtask in enumerate(bp_config.subtasks):
            if idx == 0:
                continue
            schema = subtask.input_schema
            if not schema:
                continue

            upstream = collect_all_upstream(schema)
            candidate_fields = set(collect_all_properties(schema).keys()) - upstream

            prefill = {f: initial[f] for f in candidate_fields if f in initial}
            if prefill:
                snap.supplemented_inputs.setdefault(subtask.id, {}).update(prefill)
                logger.info(
                    "[BP] Pre-filled supplemented_inputs for %s: %s",
                    subtask.id, list(prefill.keys()),
                )

    # ── ask_user message generation ──────────────────────────────

    async def _build_ask_user_nl_message(
        self,
        subtask: SubtaskConfig,
        missing_fields: list[str],
        schema: dict[str, Any],
    ) -> str:
        """根据 input_schema 生成自然语言提问文本。

        优先调用 LLM 生成友好自然的提问；若 brain 不可用、LLM 返回空、
        被 max_tokens 截断或调用异常，则回退到
        `_build_ask_user_nl_message_template`。

        Label 解析链:
        - 优先用 property.description 作为 LLM 上下文中的中文标签
        - description 缺失时退化为字段名本身（防御性兜底，BP config 应保证
          每个 property 都有 description，避免触发该路径）

        输出契约（软约束，由 prompt 引导）:
        - 自然口语化中文，1~3 句话（字段较多时可延长至 4~5 句）
        - 不含英文标识符或字段名
        - 必要时融入示例，不单列示例行
        """
        brain = self._get_brain()
        if not brain:
            logger.debug(
                "[BP] _build_ask_user_nl_message: no brain, using template fallback"
            )
            return self._build_ask_user_nl_message_template(
                subtask, missing_fields, schema,
            )

        # 清理注入到 prompt 的用户可控字符串（skill 配置），防止 prompt 注入
        def _sanitize(text: Any, max_len: int = 200) -> str:
            s = str(text) if text is not None else ""
            # 去除换行和回车，避免多行注入
            s = s.replace("\n", " ").replace("\r", " ").strip()
            if len(s) > max_len:
                s = s[:max_len] + "..."
            return s

        # 组装 LLM 上下文: 字段描述、类型、默认值、示例
        properties = schema.get("properties", {})
        field_entries: list[str] = []
        for field in missing_fields:
            prop = properties.get(field, {})
            raw_desc = prop.get("description")
            # Label 解析链: description → 字段名（防御性兜底）
            label = _sanitize(raw_desc) if raw_desc else _sanitize(field, max_len=100)
            field_type = prop.get("type", "string")
            type_hint = _TYPE_LABELS.get(field_type, field_type)
            entry = f"- {label}（{type_hint}）"
            if prop.get("default") is not None:
                entry += f"\n  默认值: {_sanitize(prop['default'], max_len=100)}"
            example_match = _EXAMPLE_PATTERN.search(str(raw_desc) if raw_desc else "")
            if example_match:
                entry += f"\n  示例: {_sanitize(example_match.group(1), max_len=100)}"
            field_entries.append(entry)

        # oneOf/anyOf 分支标题作为上下文提示
        # 注意: `schema` 参数是 _check_input_completeness 已经选定的单分支，
        # 所以需要从 subtask.input_schema (原始完整 schema) 读取 branches
        original_schema = subtask.input_schema or {}
        branches = original_schema.get("oneOf") or original_schema.get("anyOf")
        branch_hint = ""
        if branches:
            titles = [
                _sanitize(b.get("title", f"选项{i + 1}"), max_len=100)
                for i, b in enumerate(branches)
            ]
            branch_hint = f"\n\n可选方案: {' / '.join(titles)}"

        safe_subtask_name = _sanitize(subtask.name, max_len=100)
        fields_block = "\n".join(field_entries)
        prompt = (
            f"你是一个友好的智能助手。用户正在使用「{safe_subtask_name}」功能，"
            f"但还缺少一些信息。\n\n"
            f"需要补充的信息:\n{fields_block}"
            f"{branch_hint}\n\n"
            "请用自然、口语化的中文向用户提问。要求:\n"
            "- 绝对不要使用英文标识符（如 notify_email、room_id），"
            "用上面给出的标签代替；若标签本身是英文，请翻译为中文\n"
            "- 单字段场景: 1 句话对话式提问，示例融入句子（用「比如」）\n"
            "- 多字段场景: 一句引导语 + Markdown 无序列表，每项独立一行"
            "（必须真实换行，不要把列表挤在一行），"
            "格式 `- **标签**：说明（示例 xxx）`\n\n"
            "✗ 反例（列表挤在一行、暴露字段名）:\n"
            "请提供以下信息：1. start_date（文本）：开始时间 "
            "2. end_date（文本）：结束时间\n\n"
            "✓ 单字段正例:\n"
            "为了把通知发出去，告诉我你的邮箱就行，比如 alice@example.com。\n\n"
            "✓ 多字段正例:\n"
            "为了进行图像帧检索，请提供以下两个信息：\n"
            "- **开始时间**：查询范围起点，格式 YYYY-MM-DD HH:MM（例如 2026-04-14 08:00）\n"
            "- **结束时间**：查询范围终点，格式同上（例如 2026-04-14 12:00）\n\n"
            "直接输出提问文本，不要任何前缀、解释或 Markdown 代码块。"
        )

        # max_tokens 根据字段数量动态分配，保底 512，最多 1024
        # 这是 LLM 回复的预算: 每字段约 120 tokens（中文展开 + 示例融入），
        # 外加 150 tokens 作为引导句和首尾礼貌用语的最小空间
        dynamic_max_tokens = min(1024, max(512, len(missing_fields) * 120 + 150))

        try:
            resp = await brain.think_lightweight(
                prompt, max_tokens=dynamic_max_tokens,
            )
            text = resp.content if hasattr(resp, "content") else str(resp)
            text = (text or "").strip()
            stop_reason = getattr(resp, "stop_reason", "") or ""

            # 如果被 max_tokens 截断，视为无效输出触发 fallback
            if stop_reason == "max_tokens":
                logger.warning(
                    "[BP] _build_ask_user_nl_message: LLM output truncated "
                    f"(stop_reason=max_tokens, max_tokens={dynamic_max_tokens}), "
                    "falling back to template"
                )
            elif text:
                logger.debug(
                    "[BP] _build_ask_user_nl_message: LLM generated message "
                    f"for subtask={subtask.id} fields={missing_fields}"
                )
                return text
            else:
                logger.warning(
                    "[BP] _build_ask_user_nl_message: LLM returned empty text, "
                    "falling back to template"
                )
        except Exception as e:
            logger.warning(
                f"[BP] _build_ask_user_nl_message: LLM call failed ({e}), "
                "falling back to template"
            )

        return self._build_ask_user_nl_message_template(
            subtask, missing_fields, schema,
        )

    def _build_ask_user_nl_message_template(
        self,
        subtask: SubtaskConfig,
        missing_fields: list[str],
        schema: dict[str, Any],
    ) -> str:
        """根据 input_schema 生成自然语言提问文本（模板拼接，不调用 LLM）。

        作为 `_build_ask_user_nl_message` 的 fallback。
        """
        properties = schema.get("properties", {})
        lines = [f"要执行「{subtask.name}」子任务，还需要你提供以下信息：\n"]
        for field in missing_fields:
            prop = properties.get(field, {})
            desc = prop.get("description", field)
            field_type = prop.get("type", "string")
            type_hint = _TYPE_LABELS.get(field_type, field_type)
            line = f"- **{desc}**（{type_hint}）"
            example_match = _EXAMPLE_PATTERN.search(desc)
            if example_match:
                line += f"，例如：{example_match.group(1).strip()}"
            elif prop.get("default") is not None:
                line += f"，默认值：{prop['default']}"
            lines.append(line)
        lines.append("\n请直接回复以上所需信息。")
        return "\n".join(lines)

    # ── Input completeness ─────────────────────────────────────

    def _check_input_completeness(
        self, subtask: SubtaskConfig, input_data: dict[str, Any],
    ) -> tuple[list[str], dict[str, Any] | None]:
        """检查 input_schema.required 字段是否都在 input_data 中。
        如果是单分支，检查 required。
        如果是多分支 (oneOf/anyOf)，找到匹配度最高的分支，检查其 required。
        匹配策略：优先选择输入数据中命中 properties 最多的分支；若命中数相同，则选择缺失 required 最少的分支。
        返回: (缺失字段列表, 匹配到的单分支schema)
        """
        schema = subtask.input_schema
        if not schema:
            return [], None

        branches = schema.get("oneOf") or schema.get("anyOf")
        if not branches:
            required = schema.get("required", [])
            missing = [field for field in required if field not in input_data]
            return missing, schema

        best_match = None
        best_missing = []
        max_provided_count = -1
        min_missing_count = float('inf')

        for branch in branches:
            props = branch.get("properties", {})
            req = branch.get("required", [])

            # 1. 命中的已有字段数量（在这个分支定义的 properties 中）
            provided_count = sum(1 for field in input_data if field in props)
            # 2. 缺失的必填字段数量
            missing = [field for field in req if field not in input_data]

            if len(missing) == 0:
                # 完美匹配，直接返回
                return [], branch

            # 优先级：先看哪个分支命中的已有字段多，如果一样多，看哪个缺失的少
            if provided_count > max_provided_count or (provided_count == max_provided_count and len(missing) < min_missing_count):
                max_provided_count = provided_count
                min_missing_count = len(missing)
                best_missing = missing
                best_match = branch

        return best_missing, best_match

    # ── Output parsing ─────────────────────────────────────────

    @staticmethod
    def _parse_output(result: str) -> dict[str, Any]:
        """从委派结果中提取 JSON 输出。"""
        # Strategy 1: entire string is JSON
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            pass

        # Strategy 2: ```json ... ``` code block
        match = re.search(r"```json\s*(.*?)\s*```", result, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Strategy 3: find JSON objects in text (try last-to-first to prefer
        # the final result over earlier examples, but unlike the old rfind
        # approach, continue to earlier braces when an inner object is too small)
        brace_positions = [i for i, c in enumerate(result) if c == "{"]
        for start in reversed(brace_positions):
            depth = 0
            for i in range(start, len(result)):
                if result[i] == "{":
                    depth += 1
                elif result[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            parsed = json.loads(result[start : i + 1])
                            if isinstance(parsed, dict) and len(parsed) > 1:
                                return parsed
                        except json.JSONDecodeError:
                            pass
                        break

        return {"_raw_output": str(result)}

    # ── Output conforming (LLM-based) ─────────────────────────

    async def _conform_output(
        self,
        raw_output: dict[str, Any],
        output_schema: dict[str, Any] | None,
        raw_result_text: str,
        tool_results: list[str] | None = None,
    ) -> dict[str, Any]:
        """用轻量 LLM 把子任务输出映射到下一个子任务的 input_schema。

        这是一个确定性的转换步骤——不依赖 sub-agent 自觉遵守格式，
        而是在子任务完成后，显式地用 LLM 做结构化映射。
        """
        if not output_schema:
            return raw_output

        # 快速检查：如果 output 已经符合 schema，直接返回
        required = set(output_schema.get("required", []))
        if required and required.issubset(raw_output.keys()):
            return self._sanitize_output(raw_output, output_schema)

        brain = self._get_brain()
        if not brain:
            logger.warning("[BP] No brain available for _conform_output, using required-field fallback")
            return self._ensure_required_fields(raw_output, output_schema)

        example = self._schema_to_example(output_schema)

        # Build source text: combine text output + tool results for full context
        source_parts: list[str] = []
        if raw_result_text:
            source_parts.append(raw_result_text)
        if tool_results:
            source_parts.append(
                "\n\n--- 工具调用结果 ---\n" + "\n---\n".join(tool_results)
            )
        if not source_parts:
            source_parts.append(json.dumps(raw_output, ensure_ascii=False))
        source_text = "\n".join(source_parts)[:8000]

        prompt = (
            "请从以下子任务的执行结果中，提取并整理出符合目标格式的 JSON。\n\n"
            "## 子任务执行结果\n"
            f"```\n{source_text}\n```\n\n"
            "## 目标 JSON 格式\n"
            f"```json\n{example}\n```\n\n"
            "## 要求\n"
            "- 只输出一个 JSON 代码块，不要其他文字\n"
            "- 从执行结果中提取相关数据填入目标格式的各字段\n"
            "- 如果某个字段在执行结果中没有对应数据，用合理的空值（空数组[]或空字符串\"\"）\n"
            "- 不要编造数据，只从执行结果中提取"
        )

        try:
            resp = await brain.think_lightweight(prompt, max_tokens=4096)
            text = resp.content if hasattr(resp, "content") else str(resp)
            # 从 LLM 回复中提取 JSON
            conformed = self._parse_output(text)
            if "_raw_output" not in conformed:
                conformed = self._ensure_required_fields(conformed, output_schema)
                logger.debug(
                    f"[BP] _conform_output: mapped {list(raw_output.keys())} "
                    f"-> {list(conformed.keys())}"
                )
                return conformed
        except Exception as e:
            logger.warning(f"[BP] _conform_output LLM call failed: {e}")

        return self._ensure_required_fields(raw_output, output_schema)

    @staticmethod
    def _sanitize_output(output: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        required = set(schema.get("required", []))
        properties = set(collect_all_properties(schema).keys())
        allowed = required | properties if properties else None
        cleaned: dict[str, Any] = {}
        for key, value in output.items():
            if str(key).startswith("_") and key not in required:
                continue
            if allowed is not None and key not in allowed:
                continue
            cleaned[key] = value
        return cleaned

    @staticmethod
    def _default_value_for_field(field_schema: Any) -> Any:
        if not isinstance(field_schema, dict):
            return ""
        ftype = field_schema.get("type", "string")
        if ftype == "array":
            return []
        if ftype == "object":
            return {}
        if ftype in {"number", "integer"}:
            return 0
        if ftype == "boolean":
            return False
        return ""

    def _ensure_required_fields(
        self,
        output: dict[str, Any],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        cleaned = self._sanitize_output(output, schema)
        required = schema.get("required", [])
        if not required:
            return cleaned

        props = collect_all_properties(schema)

        merged = dict(cleaned)
        for field in required:
            if field in merged:
                continue
            merged[field] = self._default_value_for_field(props.get(field, {}))
        if set(merged.keys()) != set(output.keys()):
            logger.warning(
                "[BP] _conform_output fallback filled required fields: missing=%s",
                [f for f in required if f not in cleaned],
            )
        return merged

    def _get_brain(self):
        """获取 Brain 实例用于轻量 LLM 调用。"""
        try:
            import seeagent.main
            agent = getattr(seeagent.main, "_agent", None)
            if agent and hasattr(agent, "brain"):
                return agent.brain
        except ImportError:
            pass
        orchestrator = self._get_orchestrator()
        if orchestrator:
            pool = getattr(orchestrator, "_pool", None)
            if pool:
                # Try to get brain from any cached agent
                for agent in getattr(pool, "_agents", {}).values():
                    if hasattr(agent, "brain"):
                        return agent.brain
        return None

    def _validate_output_soft(
        self, output: dict, subtask_id: str, bp_config: BestPracticeConfig,
    ) -> str | None:
        """宽松校验输出。返回警告文本或 None。"""
        from .scheduler import LinearScheduler
        # Use a minimal snapshot just for schema derivation
        dummy_snap = type("_Snap", (), {
            "subtask_statuses": {},
            "subtask_outputs": {},
            "initial_input": {},
            "current_subtask_index": 0,
            "supplemented_inputs": {},
        })()
        scheduler = LinearScheduler(bp_config, dummy_snap)
        schema = scheduler.derive_output_schema(subtask_id)
        if schema and "required" in schema:
            missing = [f for f in schema["required"] if f not in output]
            if missing:
                return f"输出缺少字段: {missing}"
        return None

    def _validate_input_soft(
        self, subtask: SubtaskConfig, resolved_input: dict[str, Any],
    ) -> str | None:
        schema = subtask.input_schema
        if not schema:
            return None
        all_props = collect_all_properties(schema)
        unknown = [field for field in resolved_input if field not in all_props]
        warnings: list[str] = []
        if unknown:
            warnings.append(f"输入包含未在 schema 中声明的字段: {unknown}")
        missing, _ = self._check_input_completeness(subtask, resolved_input)
        if missing:
            warnings.append(f"输入仍缺少必填字段: {missing}")
        if warnings:
            return "；".join(warnings)
        return None

    # ── SSE Events ─────────────────────────────────────────────

    @staticmethod
    def _extract_summary_from_result(raw_result: str, output: dict) -> str | None:
        """从 SubAgent 返回文本中提取 **总结** 行作为摘要。"""
        # 匹配 **总结**: ... 或 **总结**： ...
        match = re.search(r"\*\*总结\*\*[：:]\s*(.+?)(?:\n|$)", raw_result)
        if match:
            summary = match.group(1).strip()
            if summary:
                return summary[:300]
        # 尝试提取 JSON 代码块前的说明文本
        json_match = re.search(r"```json", raw_result)
        if json_match:
            text_before = raw_result[:json_match.start()].strip()
            if text_before:
                lines = [
                    line.strip() for line in text_before.split("\n")
                    if line.strip() and not line.startswith("#")
                ]
                if lines:
                    return " ".join(lines)[:300]
        return None

    async def _emit_stale(
        self, instance_id: str, stale_ids: list[str], reason: str, session: Any,
    ) -> None:
        bus = getattr(getattr(session, "context", None), "_sse_event_bus", None)
        if not bus:
            return
        try:
            await bus.put({
                "type": "bp_stale",
                "data": {
                    "instance_id": instance_id,
                    "stale_subtask_ids": stale_ids,
                    "reason": reason,
                },
            })
        except Exception:
            pass

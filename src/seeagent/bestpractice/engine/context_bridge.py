"""ContextBridge -- context compression and restoration for BP instance switching.

Uses ContextEnvelope + CompressionStrategy abstraction for structured
context capture, compression (LLM → mechanical → truncation fallback),
and precise restoration with full snapshot data and budget control.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, TYPE_CHECKING

from .compression import (
    LLMCompression,
    MechanicalCompression,
    TruncationCompression,
    extract_text,
)
from ..models import (
    ArtifactKind, ContextArtifact, ContextEnvelope, ContextLevel, SubtaskStatus,
)

if TYPE_CHECKING:
    from ..models import BPInstanceSnapshot, PendingContextSwitch  # noqa: F401
    from .state_manager import BPStateManager

logger = logging.getLogger(__name__)

_PER_OUTPUT_LIMIT = 4000
_PER_RAW_LIMIT = 3000
_TOTAL_BUDGET = 15000

_STATUS_ICONS = {
    SubtaskStatus.DONE.value: "+",
    SubtaskStatus.CURRENT.value: ">",
    SubtaskStatus.PENDING.value: " ",
    SubtaskStatus.STALE.value: "!",
    SubtaskStatus.FAILED.value: "x",
    SubtaskStatus.WAITING_INPUT.value: "?",
}


class ContextBridge:
    """Manages context switching between BP instances."""

    def __init__(self, state_manager: BPStateManager | None = None) -> None:
        self._state_manager = state_manager

    def set_state_manager(self, state_manager: BPStateManager) -> None:
        self._state_manager = state_manager

    # ── Public API ────────────────────────────────────────────

    async def execute_pending_switch(
        self,
        session_id: str,
        brain: Any = None,
        messages: list[dict] | None = None,
    ) -> bool:
        """Consume PendingContextSwitch and execute context handover.

        Called from Agent._prepare_session_context() step 10.5.
        """
        if not self._state_manager:
            return False

        switch = self._state_manager.consume_pending_switch(session_id)
        if not switch:
            return False

        logger.info(
            f"[BP] Executing context switch: "
            f"{switch.suspended_instance_id} -> {switch.target_instance_id}"
        )

        # 1. Compress suspended instance context
        if switch.suspended_instance_id:
            suspended = self._state_manager.get(switch.suspended_instance_id)
            summary = await self._compress_context(
                messages=messages, snap=suspended, brain=brain,
            )
            if suspended:
                suspended.context_summary = summary

        # 2. Restore target instance context
        target = self._state_manager.get(switch.target_instance_id)
        if target and messages is not None:
            self._restore_context(messages, target)

        return True

    def build_recovery_message(self, snap: Any) -> str:
        """Generate recovery message for frontend display or prompt injection."""
        if snap.context_summary:
            try:
                summary = json.loads(snap.context_summary)
                return self._build_recovery_prompt(summary, snap)
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
        bp_name = snap.bp_config.name if snap.bp_config else snap.bp_id
        return (
            f"[Task Resumed] Best Practice: {bp_name}\n"
            f"Progress: step {snap.current_subtask_index + 1}\n"
            f"Context: {snap.context_summary or '(none)'}"
        )

    # ── Compression ───────────────────────────────────────────

    async def _compress_context(
        self,
        messages: list[dict] | None = None,
        snap: Any = None,
        brain: Any = None,
    ) -> str:
        """Compress context using CompressionStrategy fallback chain.

        Collects ContextArtifacts, delegates semantic compression to
        CompressionStrategy (LLM → mechanical → truncation), then
        serializes as ContextEnvelope JSON.
        """
        try:
            bp_config = getattr(snap, "bp_config", None) if snap else None
            bp_name = bp_config.name if bp_config else (
                snap.bp_id if snap else ""
            )

            # Collect artifacts
            artifacts: list[ContextArtifact] = []

            # Section 1: Progress
            if bp_config:
                for st in bp_config.subtasks:
                    status = snap.subtask_statuses.get(st.id, "pending")
                    artifacts.append(ContextArtifact(
                        kind=ArtifactKind.PROGRESS,
                        key=st.id,
                        content=json.dumps(
                            {"id": st.id, "name": st.name, "status": status},
                            ensure_ascii=False,
                        ),
                    ))

            # Section 2: Key outputs (preview for compression prompt)
            if snap and snap.subtask_outputs:
                for st_id, output in snap.subtask_outputs.items():
                    artifacts.append(ContextArtifact(
                        kind=ArtifactKind.STRUCTURED_OUTPUT,
                        key=st_id,
                        content=json.dumps(
                            output, ensure_ascii=False,
                        )[:500],
                    ))

            # Section 3: User intent
            if snap and snap.initial_input:
                artifacts.append(ContextArtifact(
                    kind=ArtifactKind.USER_INTENT,
                    key="intent",
                    content=json.dumps(
                        snap.initial_input, ensure_ascii=False,
                    )[:500],
                ))

            # Section 4: Semantic summary via CompressionStrategy chain
            semantic_summary = ""
            compression_method = "none"

            if messages:
                idx = snap.current_subtask_index if snap else 0
                total = len(bp_config.subtasks) if bp_config else 0
                current_step = ""
                completed_lines = []
                if bp_config:
                    if idx < total:
                        current_step = bp_config.subtasks[idx].name
                    for st in bp_config.subtasks[:idx]:
                        completed_lines.append(f"- {st.name}")

                compress_kwargs = {
                    "bp_name": bp_name,
                    "current_step": current_step,
                    "current_index": idx + 1,
                    "total": total,
                    "completed_steps": (
                        "\n".join(completed_lines)
                        if completed_lines else "(none)"
                    ),
                    "messages": messages,
                }

                if brain and hasattr(brain, "think_lightweight"):
                    try:
                        strategy = LLMCompression(brain)
                        semantic_summary = await strategy.compress(
                            artifacts, 1000, **compress_kwargs,
                        )
                        compression_method = "llm"
                    except Exception as e:
                        logger.warning(
                            f"[BP] LLM compression failed: {e}, "
                            f"falling back to mechanical"
                        )
                        strategy = MechanicalCompression()
                        semantic_summary = await strategy.compress(
                            artifacts, 1000, **compress_kwargs,
                        )
                        compression_method = "mechanical"
                else:
                    strategy = MechanicalCompression()
                    semantic_summary = await strategy.compress(
                        artifacts, 1000, **compress_kwargs,
                    )
                    compression_method = "mechanical"

            # Build envelope
            envelope = ContextEnvelope(
                level=ContextLevel.BP_INSTANCE,
                source_id=bp_name,
                artifacts=artifacts,
                summary=semantic_summary,
                compressed_at=time.time(),
                compression_method=compression_method,
            )

            # Serialize as v1-compatible JSON for backward compat
            subtask_progress = []
            for a in envelope.get_artifacts(ArtifactKind.PROGRESS):
                try:
                    subtask_progress.append(json.loads(a.content))
                except json.JSONDecodeError:
                    pass

            key_outputs = {}
            for a in envelope.get_artifacts(ArtifactKind.STRUCTURED_OUTPUT):
                key_outputs[a.key] = a.content

            user_intent = ""
            intent_arts = envelope.get_artifacts(ArtifactKind.USER_INTENT)
            if intent_arts:
                user_intent = intent_arts[0].content

            v1_summary = {
                "version": 1,
                "bp_name": bp_name,
                "current_subtask_index": (
                    snap.current_subtask_index if snap else 0
                ),
                "total_subtasks": (
                    len(bp_config.subtasks) if bp_config else 0
                ),
                "subtask_progress": subtask_progress,
                "key_outputs": key_outputs,
                "semantic_summary": semantic_summary,
                "user_intent": user_intent,
                "compressed_at": envelope.compressed_at,
                "compression_method": compression_method,
            }
            return json.dumps(v1_summary, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"[BP] Context compression failed: {e}")
            return ""

    # ── Restoration ───────────────────────────────────────────

    def _restore_context(self, messages: list[dict], snap: Any) -> None:
        """Inject structured recovery message from context_summary.

        Uses full snap.subtask_outputs and snap.subtask_raw_outputs
        for precise restoration instead of truncated summary data.
        Falls back to minimal recovery when context_summary is empty.
        """
        try:
            if snap.context_summary:
                try:
                    summary = json.loads(snap.context_summary)
                    recovery_msg = self._build_recovery_prompt(summary, snap)
                except (json.JSONDecodeError, KeyError, TypeError):
                    recovery_msg = (
                        f"[Task Resumed] Continuing a Best Practice task.\n"
                        f"Previous context: {snap.context_summary}\n"
                        f"Please continue the current subtask."
                    )
            else:
                recovery_msg = self._build_minimal_recovery(snap)
                if not recovery_msg:
                    return

            if messages and messages[-1].get("role") == "user":
                content = messages[-1]["content"]
                if isinstance(content, str):
                    messages[-1]["content"] = content + f"\n\n{recovery_msg}"
                elif isinstance(content, list):
                    content.append({"type": "text", "text": recovery_msg})
            else:
                messages.append({"role": "user", "content": recovery_msg})
        except Exception as e:
            logger.warning(f"[BP] Context restore failed: {e}")

    @staticmethod
    def _build_recovery_prompt(summary: dict, snap: Any) -> str:
        """Build recovery prompt using full snapshot data with budget control.

        Key improvement: reads snap.subtask_outputs (full data) instead of
        summary["key_outputs"] (truncated to 300 chars).
        """
        parts = []

        bp_name = summary.get("bp_name", getattr(snap, "bp_id", "unknown"))
        idx = summary.get(
            "current_subtask_index",
            getattr(snap, "current_subtask_index", 0),
        )
        total = summary.get("total_subtasks", 0)
        parts.append(f"[Task Resumed] Best Practice: {bp_name}")
        parts.append(f"Progress: step {idx + 1}/{total}")

        # Subtask progress table
        progress = summary.get("subtask_progress", [])
        if progress:
            lines = []
            for p in progress:
                icon = _STATUS_ICONS.get(p.get("status", ""), "?")
                lines.append(f"  [{icon}] {p.get('name', p.get('id', '?'))}")
            parts.append("Steps:\n" + "\n".join(lines))

        # Full outputs + raw execution details from snapshot
        output_parts, _ = ContextBridge._format_snap_outputs(snap)
        parts.extend(output_parts)

        # Semantic summary
        semantic = summary.get("semantic_summary", "")
        if semantic:
            parts.append(f"Context summary:\n{semantic}")

        # User intent
        intent = summary.get("user_intent", "")
        if intent:
            parts.append(f"Original input: {intent}")

        parts.append("Please continue from where this task was suspended.")
        return "\n\n".join(parts)

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _extract_text(content: Any) -> str:
        """Extract text from message content (str or list of content blocks)."""
        return extract_text(content)

    @staticmethod
    def _format_snap_outputs(snap: Any) -> tuple[list[str], int]:
        """Format snapshot outputs + raw outputs with budget control.

        Returns (parts_list, total_chars_consumed).
        """
        parts: list[str] = []
        total_chars = 0

        snap_outputs = getattr(snap, "subtask_outputs", {}) or {}
        if snap_outputs:
            out_lines = []
            for k, v in snap_outputs.items():
                serialized = json.dumps(v, ensure_ascii=False, indent=2)
                truncated = serialized[:_PER_OUTPUT_LIMIT]
                if total_chars + len(truncated) > _TOTAL_BUDGET:
                    out_lines.append(
                        f"  {k}: (truncated, {len(serialized)} chars total)"
                    )
                    break
                out_lines.append(f"  {k}: {truncated}")
                total_chars += len(truncated)
            if out_lines:
                parts.append("Completed outputs:\n" + "\n".join(out_lines))

        raw_outputs = getattr(snap, "subtask_raw_outputs", {}) or {}
        if raw_outputs:
            raw_lines = []
            for k, v in raw_outputs.items():
                excerpt = v[:_PER_RAW_LIMIT]
                if total_chars + len(excerpt) > _TOTAL_BUDGET:
                    break
                raw_lines.append(f"  [{k}] {excerpt}")
                total_chars += len(excerpt)
            if raw_lines:
                parts.append("Execution details:\n" + "\n".join(raw_lines))

        return parts, total_chars

    @staticmethod
    def _build_minimal_recovery(snap: Any) -> str:
        """Build minimal recovery from Snapshot when context_summary is empty."""
        has_data = (
            getattr(snap, "subtask_outputs", None)
            or getattr(snap, "initial_input", None)
        )
        if not has_data:
            return ""

        parts = ["[Task Resumed]"]
        bp_name = (
            snap.bp_config.name
            if getattr(snap, "bp_config", None)
            else getattr(snap, "bp_id", "")
        )
        if bp_name:
            parts.append(f"Best Practice: {bp_name}")

        output_parts, _ = ContextBridge._format_snap_outputs(snap)
        parts.extend(output_parts)

        if snap.initial_input:
            parts.append(
                f"Original input: "
                f"{json.dumps(snap.initial_input, ensure_ascii=False)[:500]}"
            )

        parts.append("Please continue from where this task was suspended.")
        return "\n\n".join(parts)

"""ContextBridge -- context compression and restoration for BP instance switching.

Uses ContextEnvelope + CompressionStrategy abstraction for structured
context capture, compression (LLM → mechanical → truncation fallback),
and precise restoration with full snapshot data and budget control.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

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
        session: Any = None,
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
                await self._state_manager.persist_context_summary(suspended.instance_id)

        # 2. Restore target instance context
        target = self._state_manager.get(switch.target_instance_id)
        if target and messages is not None:
            self._restore_context(messages, target)

        self._persist_session_state(session_id, session)

        return True

    def build_recovery_message(self, snap: Any) -> str:
        """Generate recovery message for frontend display or prompt injection."""
        if snap.context_summary:
            if snap.context_summary.strip().startswith("{"):
                envelope = self._load_envelope(snap.context_summary)
                if self._has_recovery_data(envelope):
                    return self._build_recovery_prompt(envelope, snap)
            return self._build_snapshot_recovery_prompt(snap)
        return self._build_minimal_recovery(snap) or self._build_snapshot_recovery_prompt(snap)

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
                "messages": messages or [],
            }
            semantic_summary, compression_method = await self._run_compression_chain(
                artifacts, brain=brain, compress_kwargs=compress_kwargs,
            )
            if semantic_summary:
                artifacts.append(ContextArtifact(
                    kind=ArtifactKind.SEMANTIC_SUMMARY,
                    key="semantic",
                    content=semantic_summary,
                ))

            return semantic_summary
        except Exception as e:
            logger.warning(f"[BP] Context compression failed: {e}")
            return ""

    # ── Restoration ───────────────────────────────────────────

    def _restore_context(self, messages: list[dict], snap: Any) -> None:
        """Inject structured recovery message from context_summary.

        Routes by format: old JSON (starts with '{') uses ContextEnvelope path
        for backward compatibility; new plain-text format uses snapshot fields
        directly. Falls back to minimal recovery when context_summary is empty.
        """
        try:
            if snap.context_summary:
                if snap.context_summary.strip().startswith("{"):
                    # Backward compat: old JSON ContextEnvelope format
                    envelope = self._load_envelope(snap.context_summary)
                    if self._has_recovery_data(envelope):
                        recovery_msg = self._build_recovery_prompt(envelope, snap)
                    else:
                        recovery_msg = self._build_snapshot_recovery_prompt(snap)
                else:
                    # New format: plain-text semantic summary, restore from snapshot
                    recovery_msg = self._build_snapshot_recovery_prompt(snap)
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
    def _build_recovery_prompt(envelope: ContextEnvelope, snap: Any) -> str:
        """Build recovery prompt using full snapshot data with budget control.

        Uses envelope artifacts for progress/summary/intent and snapshot
        outputs for full execution details.
        """
        parts = []

        bp_name = (
            envelope.source_id
            or (snap.bp_config.name if getattr(snap, "bp_config", None) else "")
            or getattr(snap, "bp_id", "unknown")
        )
        progress = ContextBridge._progress_records(envelope)
        idx = ContextBridge._infer_progress_index(
            progress, getattr(snap, "current_subtask_index", 0),
        )
        total = len(progress) or len(getattr(snap, "subtask_statuses", {}) or {})
        parts.append(f"[Task Resumed] Best Practice: {bp_name}")
        parts.append(f"Progress: step {idx + 1}/{total}")

        # Subtask progress table
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
        semantic = envelope.summary or ContextBridge._artifact_content(
            envelope, ArtifactKind.SEMANTIC_SUMMARY,
        )
        if semantic:
            parts.append(f"Context summary:\n{semantic}")

        # User intent
        intent = ContextBridge._artifact_content(
            envelope, ArtifactKind.USER_INTENT,
        )
        if intent:
            parts.append(f"Original input: {intent}")

        parts.append("Please continue from where this task was suspended.")
        return "\n\n".join(parts)

    @staticmethod
    def _build_snapshot_recovery_prompt(snap: Any) -> str:
        """Build recovery prompt directly from snapshot fields without JSON parsing.

        Used for new plain-text context_summary format and as fallback when
        the old ContextEnvelope JSON has no recovery data.
        """
        parts: list[str] = []

        bp_config = getattr(snap, "bp_config", None)
        bp_name = (
            bp_config.name if bp_config else getattr(snap, "bp_id", "")
        )
        idx = getattr(snap, "current_subtask_index", 0)

        header = "[Task Resumed]"
        if bp_name:
            header += f" Best Practice: {bp_name}"
        parts.append(header)

        if bp_config and bp_config.subtasks:
            total = len(bp_config.subtasks)
            parts.append(f"Progress: step {idx + 1}/{total}")
            statuses = getattr(snap, "subtask_statuses", {}) or {}
            lines = []
            for st in bp_config.subtasks:
                status = statuses.get(st.id, "pending")
                icon = _STATUS_ICONS.get(status, "?")
                lines.append(f"  [{icon}] {st.name}")
            if lines:
                parts.append("Steps:\n" + "\n".join(lines))

        output_parts, _ = ContextBridge._format_snap_outputs(snap)
        parts.extend(output_parts)

        context_summary = getattr(snap, "context_summary", "")
        if context_summary and not context_summary.strip().startswith("{"):
            parts.append(f"Context summary:\n{context_summary}")

        initial_input = getattr(snap, "initial_input", None)
        if initial_input:
            parts.append(
                f"Original input: "
                f"{json.dumps(initial_input, ensure_ascii=False)[:500]}"
            )

        parts.append("Please continue from where this task was suspended.")
        return "\n\n".join(parts)

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _extract_text(content: Any) -> str:
        """Extract text from message content (str or list of content blocks)."""
        return extract_text(content)

    @staticmethod
    async def _run_compression_chain(
        artifacts: list[ContextArtifact],
        *,
        brain: Any = None,
        compress_kwargs: dict[str, Any],
    ) -> tuple[str, str]:
        budget = 1000
        attempts: list[tuple[str, Any]] = []
        if brain and hasattr(brain, "think_lightweight"):
            attempts.append(("llm", LLMCompression(brain)))
        attempts.append(("mechanical", MechanicalCompression()))
        attempts.append(("truncation", TruncationCompression()))

        if not compress_kwargs.get("messages") and not artifacts:
            return "", "none"

        for name, strategy in attempts:
            try:
                summary = (await strategy.compress(
                    artifacts, budget, **compress_kwargs,
                )).strip()
            except Exception as e:
                logger.warning(
                    f"[BP] {name} compression failed: {e}; "
                    f"falling back to next strategy"
                )
                continue
            if summary:
                return summary[:budget], name

        return "", "none"

    @staticmethod
    def _load_envelope(raw: str) -> ContextEnvelope:
        return ContextEnvelope.from_v1(raw)

    @staticmethod
    def _has_recovery_data(envelope: ContextEnvelope) -> bool:
        return bool(envelope.source_id or envelope.summary or envelope.artifacts)

    @staticmethod
    def _artifact_content(
        envelope: ContextEnvelope, kind: ArtifactKind,
    ) -> str:
        arts = envelope.get_artifacts(kind)
        if not arts:
            return ""
        return arts[0].content

    @staticmethod
    def _progress_records(envelope: ContextEnvelope) -> list[dict[str, Any]]:
        progress: list[dict[str, Any]] = []
        for art in envelope.get_artifacts(ArtifactKind.PROGRESS):
            try:
                parsed = json.loads(art.content)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(parsed, dict):
                progress.append(parsed)
        return progress

    @staticmethod
    def _infer_progress_index(progress: list[dict[str, Any]], fallback: int) -> int:
        for idx, item in enumerate(progress):
            if item.get("status") == SubtaskStatus.CURRENT.value:
                return idx
        for idx, item in enumerate(progress):
            if item.get("status") == SubtaskStatus.WAITING_INPUT.value:
                return idx
        done_count = sum(
            1 for item in progress if item.get("status") == SubtaskStatus.DONE.value
        )
        if progress and done_count < len(progress):
            return done_count
        return fallback

    def _persist_session_state(self, session_id: str, session: Any) -> None:
        if not self._state_manager or not session or not hasattr(session, "metadata"):
            return
        try:
            session.metadata["bp_state"] = self._state_manager.serialize_for_session(
                session_id,
            )
        except Exception as e:
            logger.warning(f"[BP] Failed to persist session state: {e}")

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

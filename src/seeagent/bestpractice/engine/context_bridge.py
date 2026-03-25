"""ContextBridge -- context compression and restoration for BP instance switching.

Compresses suspended instance context via LLM semantic summary (with mechanical
fallback) into a structured JSON format, and restores it as a layered recovery
prompt when the instance is resumed.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import BPInstanceSnapshot, PendingContextSwitch  # noqa: F401
    from .state_manager import BPStateManager

logger = logging.getLogger(__name__)

_COMPRESS_PROMPT_TEMPLATE = (
    "You are a context compression assistant. Extract essential context "
    "from a BP (Best Practice) task execution session.\n\n"
    "## Task Information\n"
    "- BP Name: {bp_name}\n"
    "- Current Step: {current_step} ({current_index}/{total})\n"
    "- Completed Steps:\n{completed_steps}\n\n"
    "## Recent Conversation\n{raw_messages}\n\n"
    "## Completed Subtask Outputs\n{outputs_summary}\n\n"
    "## Instructions\n"
    "Produce a concise summary (max 500 characters) covering ONLY:\n"
    "1. User preferences/constraints explicitly stated\n"
    "2. Key decisions made and their rationale\n"
    "3. Current state when suspended\n"
    "4. Unresolved issues or blockers\n\n"
    "Do NOT repeat subtask output data. Output plain text only."
)

_STATUS_ICONS = {
    "done": "+", "current": ">", "pending": " ",
    "stale": "!", "failed": "x", "waiting_input": "?",
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

        Args:
            session_id: Current session ID.
            brain: Brain instance for LLM-based compression.
            messages: Current conversation history list.

        Returns: True if switch was executed, False if no pending switch.
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
        """Compress context to structured JSON string.

        Three-tier fallback:
        1. LLM semantic compression (brain available)
        2. Improved mechanical extraction (brain unavailable or LLM fails)
        3. Empty string (all compression fails)
        """
        try:
            bp_config = getattr(snap, "bp_config", None) if snap else None

            # Section 1: Progress (always available from snapshot)
            subtask_progress = []
            if bp_config:
                for st in bp_config.subtasks:
                    status = snap.subtask_statuses.get(st.id, "pending")
                    subtask_progress.append({
                        "id": st.id,
                        "name": st.name,
                        "status": status,
                    })

            # Section 2: Key outputs (truncated)
            key_outputs = {}
            if snap and snap.subtask_outputs:
                for st_id, output in snap.subtask_outputs.items():
                    key_outputs[st_id] = json.dumps(
                        output, ensure_ascii=False,
                    )[:200]

            # Section 3: Semantic summary
            semantic_summary = ""
            compression_method = "none"

            if messages:
                if brain and hasattr(brain, "think_lightweight"):
                    try:
                        semantic_summary = await self._llm_compress(
                            messages, snap, brain,
                        )
                        compression_method = "llm"
                    except Exception as e:
                        logger.warning(
                            f"[BP] LLM compression failed: {e}, "
                            f"falling back to mechanical"
                        )
                        semantic_summary = self._mechanical_compress(messages)
                        compression_method = "mechanical"
                else:
                    semantic_summary = self._mechanical_compress(messages)
                    compression_method = "mechanical"

            # Section 4: User intent
            user_intent = ""
            if snap and snap.initial_input:
                user_intent = json.dumps(
                    snap.initial_input, ensure_ascii=False,
                )[:300]

            summary = {
                "version": 1,
                "bp_name": bp_config.name if bp_config else (
                    snap.bp_id if snap else ""
                ),
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
                "compressed_at": time.time(),
                "compression_method": compression_method,
            }
            return json.dumps(summary, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"[BP] Context compression failed: {e}")
            return ""

    async def _llm_compress(
        self,
        messages: list[dict],
        snap: Any,
        brain: Any,
    ) -> str:
        """Use LLM to extract semantic summary from conversation."""
        bp_config = getattr(snap, "bp_config", None) if snap else None
        bp_name = bp_config.name if bp_config else (
            snap.bp_id if snap else "unknown"
        )

        # Build completed steps
        completed_lines = []
        idx = snap.current_subtask_index if snap else 0
        if bp_config:
            for st in bp_config.subtasks[:idx]:
                completed_lines.append(f"- {st.name}")
        completed_str = "\n".join(completed_lines) if completed_lines else "(none)"

        current_step = ""
        total = 0
        if bp_config:
            total = len(bp_config.subtasks)
            if idx < total:
                current_step = bp_config.subtasks[idx].name

        # Extract raw message text (last 15)
        raw_parts = []
        for msg in (messages or [])[-15:]:
            role = msg.get("role", "?")
            text = self._extract_text(msg.get("content", ""))
            if text:
                raw_parts.append(f"[{role}] {text[:500]}")
        raw_messages = "\n".join(raw_parts)

        # Build outputs summary
        outputs_parts = []
        if snap and snap.subtask_outputs:
            for st_id, output in snap.subtask_outputs.items():
                outputs_parts.append(
                    f"{st_id}: {json.dumps(output, ensure_ascii=False)[:300]}"
                )
        outputs_str = "\n".join(outputs_parts) if outputs_parts else "(none)"

        prompt = _COMPRESS_PROMPT_TEMPLATE.format(
            bp_name=bp_name,
            current_step=current_step,
            current_index=idx + 1,
            total=total,
            completed_steps=completed_str,
            raw_messages=raw_messages,
            outputs_summary=outputs_str,
        )

        resp = await brain.think_lightweight(prompt, max_tokens=512)
        text = resp.content if hasattr(resp, "content") else str(resp)
        return text.strip()[:600]

    def _mechanical_compress(self, messages: list[dict]) -> str:
        """Improved fallback: extract meaningful messages, filtering noise."""
        parts = []
        for msg in (messages or [])[-15:]:
            role = msg.get("role", "?")
            if role == "tool":
                continue
            text = self._extract_text(msg.get("content", ""))
            if not text:
                continue
            if role == "assistant" and len(text) < 20:
                continue
            parts.append(f"[{role}] {text[:300]}")
        return "\n".join(parts[-10:])

    # ── Restoration ───────────────────────────────────────────

    def _restore_context(self, messages: list[dict], snap: Any) -> None:
        """Inject structured recovery message from context_summary."""
        if not snap.context_summary:
            return
        try:
            try:
                summary = json.loads(snap.context_summary)
                recovery_msg = self._build_recovery_prompt(summary, snap)
            except (json.JSONDecodeError, KeyError, TypeError):
                recovery_msg = (
                    f"[Task Resumed] Continuing a Best Practice task.\n"
                    f"Previous context: {snap.context_summary}\n"
                    f"Please continue the current subtask."
                )

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
        """Build a structured recovery message from parsed context summary."""
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

        # Key outputs
        key_outputs = summary.get("key_outputs", {})
        if key_outputs:
            out_lines = [f"  {k}: {v[:150]}" for k, v in key_outputs.items()]
            parts.append("Completed outputs:\n" + "\n".join(out_lines))

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
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif block.get("type") == "tool_result":
                        texts.append(
                            f"[tool_result: "
                            f"{str(block.get('content', ''))[:100]}]"
                        )
            return " ".join(texts).strip()
        return ""

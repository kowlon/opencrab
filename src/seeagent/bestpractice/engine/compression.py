"""CompressionStrategy — pluggable context compression for BP execution.

Three concrete strategies with fallback chain: LLM → Mechanical → Truncation.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import ContextArtifact

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
    "Produce a concise summary (max {max_chars} characters) covering ONLY:\n"
    "1. User preferences/constraints explicitly stated\n"
    "2. Key decisions made and their rationale\n"
    "3. Current state when suspended\n"
    "4. Unresolved issues or blockers\n\n"
    "Do NOT repeat subtask output data. Output plain text only."
)


class CompressionStrategy(ABC):
    """Base class for context compression strategies."""

    @abstractmethod
    async def compress(
        self,
        artifacts: list[ContextArtifact],
        budget: int,
        **kwargs: Any,
    ) -> str:
        """Compress artifacts into a summary string within budget."""
        ...


class LLMCompression(CompressionStrategy):
    """Use LLM (brain.think_lightweight) for semantic compression."""

    def __init__(self, brain: Any) -> None:
        self._brain = brain

    async def compress(
        self,
        artifacts: list[ContextArtifact],
        budget: int,
        **kwargs: Any,
    ) -> str:
        from ..models import ArtifactKind

        bp_name = kwargs.get("bp_name", "unknown")
        current_step = kwargs.get("current_step", "")
        current_index = kwargs.get("current_index", 0)
        total = kwargs.get("total", 0)
        completed_steps = kwargs.get("completed_steps", "(none)")
        messages = kwargs.get("messages", [])

        raw_parts = []
        for msg in (messages or [])[-15:]:
            role = msg.get("role", "?")
            text = extract_text(msg.get("content", ""))
            if text:
                raw_parts.append(f"[{role}] {text[:500]}")
        raw_messages = "\n".join(raw_parts)

        outputs_parts = []
        for a in artifacts:
            if a.kind == ArtifactKind.STRUCTURED_OUTPUT:
                outputs_parts.append(f"{a.key}: {a.content[:500]}")
        outputs_str = "\n".join(outputs_parts) if outputs_parts else "(none)"

        prompt = _COMPRESS_PROMPT_TEMPLATE.format(
            bp_name=bp_name,
            current_step=current_step,
            current_index=current_index,
            total=total,
            completed_steps=completed_steps,
            raw_messages=raw_messages,
            outputs_summary=outputs_str,
            max_chars=budget,
        )

        resp = await self._brain.think_lightweight(prompt, max_tokens=512)
        text = resp.content if hasattr(resp, "content") else str(resp)
        return text.strip()[:budget]


class MechanicalCompression(CompressionStrategy):
    """Fallback: extract meaningful messages without LLM."""

    async def compress(
        self,
        artifacts: list[ContextArtifact],
        budget: int,
        **kwargs: Any,
    ) -> str:
        messages = kwargs.get("messages", [])
        parts = []
        for msg in (messages or [])[-15:]:
            role = msg.get("role", "?")
            if role == "tool":
                continue
            text = extract_text(msg.get("content", ""))
            if not text:
                continue
            if role == "assistant" and len(text) < 20:
                continue
            parts.append(f"[{role}] {text[:300]}")
        result = "\n".join(parts[-10:])
        return result[:budget]


class TruncationCompression(CompressionStrategy):
    """Last resort: concatenate artifact contents by priority, truncate to budget."""

    async def compress(
        self,
        artifacts: list[ContextArtifact],
        budget: int,
        **kwargs: Any,
    ) -> str:
        sorted_arts = sorted(artifacts, key=lambda a: a.priority, reverse=True)
        parts = []
        total = 0
        for a in sorted_arts:
            remaining = budget - total
            if remaining <= 0:
                break
            chunk = f"[{a.key}] {a.content[:remaining]}"
            parts.append(chunk)
            total += len(chunk)
        return "\n".join(parts)[:budget]


def extract_text(content: Any) -> str:
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

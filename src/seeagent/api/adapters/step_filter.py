"""StepFilter: classifies tool calls for step card visibility."""
from __future__ import annotations

import logging

from .seecrab_models import FilterResult, StepFilterConfig

logger = logging.getLogger(__name__)


class StepFilter:
    """Classifies tool calls as visible step cards or hidden internals."""

    def __init__(self, config: StepFilterConfig | None = None):
        self.config = config or StepFilterConfig()
        self._user_messages: list[str] = []

    def set_user_messages(self, messages: list[str]) -> None:
        """Set recent user messages for mention detection."""
        self._user_messages = messages[-5:]

    def classify(self, tool_name: str, args: dict) -> FilterResult:
        """Classify a tool call.

        Priority: skill_trigger > mcp_trigger > agent_trigger > whitelist > user_mention > hidden.
        When debug_enabled is False, internal tools are treated as hidden regardless of whitelist.
        """
        if not self.config.debug_enabled and tool_name in self.config.internal_tools:
            result = FilterResult.HIDDEN
        elif tool_name in self.config.skill_triggers:
            result = FilterResult.SKILL_TRIGGER
        elif tool_name == self.config.mcp_trigger:
            result = FilterResult.MCP_TRIGGER
        elif tool_name in self.config.agent_triggers:
            result = FilterResult.AGENT_TRIGGER
        elif tool_name in self.config.whitelist:
            result = FilterResult.WHITELIST
        elif self._check_user_mention(tool_name):
            result = FilterResult.USER_MENTION
        else:
            result = FilterResult.HIDDEN

        if self.config.debug_enabled:
            logger.debug(f"[StepFilter] tool={tool_name} → {result.value}")
        return result

    def _check_user_mention(self, tool_name: str) -> bool:
        """Check if user recently mentioned this tool's operation."""
        keywords = self.config.user_mention_keywords.get(tool_name)
        if not keywords:
            return False
        combined = " ".join(self._user_messages).lower()
        return any(kw in combined for kw in keywords)

"""StepFilter: classifies tool calls for step card visibility."""
from __future__ import annotations

import logging
from fnmatch import fnmatch

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
        reason = "default_hidden"
        if not self.config.debug_enabled and self._matches_any(tool_name, self.config.internal_tools):
            result = FilterResult.HIDDEN
            reason = "internal_tool_hidden"
        elif self._matches_any(tool_name, self.config.skill_triggers):
            result = FilterResult.SKILL_TRIGGER
            reason = "skill_trigger"
        elif tool_name == self.config.mcp_trigger:
            result = FilterResult.MCP_TRIGGER
            reason = "mcp_trigger"
        elif self._matches_any(tool_name, self.config.agent_triggers):
            result = FilterResult.AGENT_TRIGGER
            reason = "agent_trigger"
        elif self._matches_any(tool_name, self.config.whitelist):
            result = FilterResult.WHITELIST
            reason = "whitelist"
        elif self._check_user_mention(tool_name):
            result = FilterResult.USER_MENTION
            reason = "user_mention"
        else:
            result = FilterResult.HIDDEN

        if self.config.debug_enabled or result != FilterResult.HIDDEN:
            logger.info(
                "[StepFilter] tool=%s result=%s reason=%s debug_enabled=%s",
                tool_name,
                result.value,
                reason,
                self.config.debug_enabled,
            )
        return result

    @staticmethod
    def _matches_any(tool_name: str, patterns: list[str]) -> bool:
        """支持精确和通配符匹配（如 browser_*）。"""
        for p in patterns:
            if p == tool_name:
                return True
            if "*" in p and fnmatch(tool_name, p):
                return True
        return False

    def _check_user_mention(self, tool_name: str) -> bool:
        """Check if user recently mentioned this tool's operation."""
        keywords = self.config.user_mention_keywords.get(tool_name)
        if not keywords:
            return False
        combined = " ".join(self._user_messages).lower()
        return any(kw in combined for kw in keywords)

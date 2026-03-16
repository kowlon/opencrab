"""Tests for TitleGenerator — LLM title generation + humanize mapping."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from seeagent.api.adapters.title_generator import TitleGenerator


class TestHumanizeToolTitle:
    def setup_method(self):
        self.gen = TitleGenerator(brain=None, user_messages=[])

    def test_web_search(self):
        title = self.gen.humanize_tool_title("web_search", {"query": "Karpathy 2026"})
        assert "Karpathy 2026" in title

    def test_news_search(self):
        title = self.gen.humanize_tool_title("news_search", {"query": "AI"})
        assert "AI" in title

    def test_browser_task(self):
        title = self.gen.humanize_tool_title("browser_task", {})
        assert title  # non-empty

    def test_unknown_tool_fallback(self):
        title = self.gen.humanize_tool_title("unknown_tool", {})
        assert title  # should return a fallback


class TestDelegationInstantTitle:
    def setup_method(self):
        self.gen = TitleGenerator(brain=None, user_messages=[])

    def test_with_agent_id_and_reason(self):
        title = self.gen.delegation_instant_title({
            "agent_id": "researcher",
            "message": "搜索最新AI论文",
            "reason": "需要专业调研",
        })
        assert "researcher" in title
        assert "需要专业调研" in title

    def test_with_agent_id_and_message_no_reason(self):
        title = self.gen.delegation_instant_title({
            "agent_id": "code-assistant",
            "message": "重构这个模块的错误处理逻辑",
        })
        assert "code-assistant" in title
        assert "重构" in title

    def test_minimal_args(self):
        title = self.gen.delegation_instant_title({"agent_id": "helper"})
        assert "helper" in title

    def test_empty_args(self):
        title = self.gen.delegation_instant_title({})
        assert "专家" in title  # fallback

    def test_long_message_truncated(self):
        title = self.gen.delegation_instant_title({
            "agent_id": "x",
            "message": "这是一个非常非常非常非常非常非常长的消息内容",
        })
        assert len(title) < 50


class TestDelegationFallback:
    def test_with_name_and_reason(self):
        title = TitleGenerator._delegation_fallback(
            {"name": "研究员"}, {"reason": "调研论文", "message": ""}
        )
        assert "研究员" in title
        assert "调研论文" in title

    def test_with_name_only(self):
        title = TitleGenerator._delegation_fallback(
            {"name": "码哥"}, {"reason": "", "message": ""}
        )
        assert "码哥" in title

# tests/unit/test_step_aggregator.py
"""Tests for StepAggregator — aggregation state machine."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from openakita.api.adapters.card_builder import CardBuilder
from openakita.api.adapters.seecrab_models import AggregatorState, FilterResult
from openakita.api.adapters.step_aggregator import StepAggregator
from openakita.api.adapters.title_generator import TitleGenerator
from openakita.api.adapters.timer_tracker import TimerTracker


def _make_deps():
    """Create test dependencies."""
    title_gen = TitleGenerator(brain=None, user_messages=[])
    card_builder = CardBuilder()
    timer = TimerTracker()
    timer.start("test_reply")
    return title_gen, card_builder, timer


class TestIDLEState:
    pytestmark = pytest.mark.asyncio

    async def test_whitelist_creates_independent_card(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        events = await agg.on_tool_call_start(
            "web_search", {"query": "test"}, "t1", FilterResult.WHITELIST
        )
        assert len(events) >= 1
        card = next(e for e in events if e["type"] == "step_card")
        assert card["status"] == "running"
        assert "test" in card["title"]
        assert agg.state == AggregatorState.IDLE
        assert "t1" in agg._independent_cards
        step_id, title = agg._independent_cards["t1"]
        assert "test" in title

    async def test_independent_card_completed_on_tool_end(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "web_search", {"query": "test"}, "t1", FilterResult.WHITELIST
        )
        events = await agg.on_tool_call_end("web_search", "t1", "results", False)
        assert len(events) == 1
        assert events[0]["status"] == "completed"
        assert events[0]["type"] == "step_card"
        assert "t1" not in agg._independent_cards

    async def test_independent_card_failed_on_error(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "web_search", {"query": "test"}, "t1", FilterResult.WHITELIST
        )
        events = await agg.on_tool_call_end("web_search", "t1", "error!", True)
        assert len(events) == 1
        assert events[0]["status"] == "failed"

    async def test_skill_trigger_enters_absorb(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        events = await agg.on_tool_call_start(
            "load_skill", {"skill": "web_researcher"}, "t1", FilterResult.SKILL_TRIGGER
        )
        assert agg.state == AggregatorState.SKILL_ABSORB
        assert agg.pending_card is not None

    async def test_mcp_trigger_enters_absorb(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        events = await agg.on_tool_call_start(
            "call_mcp_tool", {"server": "github", "tool": "search"}, "t1",
            FilterResult.MCP_TRIGGER,
        )
        assert agg.state == AggregatorState.MCP_ABSORB
        assert agg.pending_card is not None
        assert agg.pending_card.mcp_server == "github"

    async def test_hidden_tool_no_events(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        events = await agg.on_tool_call_start(
            "read_file", {"path": "test"}, "t1", FilterResult.HIDDEN
        )
        assert events == []
        assert agg.state == AggregatorState.IDLE


class TestSKILL_ABSORB:
    pytestmark = pytest.mark.asyncio

    async def test_absorbs_tool_calls(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "load_skill", {}, "t1", FilterResult.SKILL_TRIGGER
        )
        events = await agg.on_tool_call_start(
            "web_search", {"query": "test"}, "t2", FilterResult.WHITELIST
        )
        assert events == []
        assert len(agg.pending_card.absorbed_calls) == 1

    async def test_text_delta_completes_skill(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "load_skill", {}, "t1", FilterResult.SKILL_TRIGGER
        )
        events = await agg.on_text_delta()
        assert agg.state == AggregatorState.IDLE
        assert agg.pending_card is None
        completed = [
            e for e in events
            if e.get("type") == "step_card" and e.get("status") == "completed"
        ]
        assert len(completed) == 1

    async def test_new_skill_completes_previous(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "load_skill", {}, "t1", FilterResult.SKILL_TRIGGER
        )
        events = await agg.on_tool_call_start(
            "load_skill", {}, "t2", FilterResult.SKILL_TRIGGER
        )
        completed = [e for e in events if e.get("status") == "completed"]
        assert len(completed) == 1
        assert agg.state == AggregatorState.SKILL_ABSORB


class TestMCP_ABSORB:
    pytestmark = pytest.mark.asyncio

    async def test_same_server_absorbed(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "call_mcp_tool", {"server": "gh", "tool": "t1"}, "t1",
            FilterResult.MCP_TRIGGER,
        )
        events = await agg.on_tool_call_start(
            "call_mcp_tool", {"server": "gh", "tool": "t2"}, "t2",
            FilterResult.MCP_TRIGGER,
        )
        assert events == []
        assert agg.state == AggregatorState.MCP_ABSORB
        assert len(agg.pending_card.absorbed_calls) == 1

    async def test_different_server_creates_new(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "call_mcp_tool", {"server": "gh", "tool": "t1"}, "t1",
            FilterResult.MCP_TRIGGER,
        )
        events = await agg.on_tool_call_start(
            "call_mcp_tool", {"server": "arxiv", "tool": "t2"}, "t2",
            FilterResult.MCP_TRIGGER,
        )
        completed = [e for e in events if e.get("status") == "completed"]
        assert len(completed) == 1
        assert agg.state == AggregatorState.MCP_ABSORB
        assert agg.pending_card.mcp_server == "arxiv"

    async def test_non_mcp_tool_breaks_absorb(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "call_mcp_tool", {"server": "gh", "tool": "t1"}, "t1",
            FilterResult.MCP_TRIGGER,
        )
        events = await agg.on_tool_call_start(
            "web_search", {"query": "test"}, "t2", FilterResult.WHITELIST
        )
        completed = [e for e in events if e.get("status") == "completed"]
        assert len(completed) == 1
        assert agg.state == AggregatorState.IDLE


class TestPLAN_ABSORB:
    pytestmark = pytest.mark.asyncio

    async def test_plan_created_enters_absorb(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        events = await agg.on_plan_created({
            "steps": [
                {"id": "step_1", "description": "步骤1", "status": "pending"},
                {"id": "step_2", "description": "步骤2", "status": "pending"},
            ]
        })
        assert agg.state == AggregatorState.PLAN_ABSORB
        checklist = next(e for e in events if e["type"] == "plan_checklist")
        assert len(checklist["steps"]) == 2
        assert checklist["steps"][0]["index"] == 1
        assert checklist["steps"][0]["title"] == "步骤1"
        assert agg._plan_id_to_index["step_1"] == 1
        assert agg._plan_id_to_index["step_2"] == 2

    async def test_plan_absorbs_all_tools(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_plan_created({
            "steps": [{"id": "step_1", "description": "步骤1", "status": "pending"}]
        })
        await agg.on_plan_step_updated(1, "running")
        events = await agg.on_tool_call_start(
            "load_skill", {}, "t1", FilterResult.SKILL_TRIGGER
        )
        assert events == []
        events = await agg.on_tool_call_start(
            "web_search", {}, "t2", FilterResult.WHITELIST
        )
        assert events == []

    async def test_plan_step_completed(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_plan_created({
            "steps": [
                {"id": "step_1", "description": "步骤1", "status": "pending"},
                {"id": "step_2", "description": "步骤2", "status": "pending"},
            ]
        })
        await agg.on_plan_step_updated(1, "running")
        events = await agg.on_plan_step_updated(1, "completed")
        step_cards = [e for e in events if e["type"] == "step_card"]
        assert any(c["status"] == "completed" for c in step_cards)

    async def test_plan_completed_returns_to_idle(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_plan_created({
            "steps": [{"id": "step_1", "description": "步骤1", "status": "pending"}]
        })
        await agg.on_plan_step_updated(1, "running")
        await agg.on_plan_step_updated(1, "completed")
        events = await agg.on_plan_completed()
        assert agg.state == AggregatorState.IDLE


class TestToolCallEnd:
    pytestmark = pytest.mark.asyncio

    async def test_updates_absorbed_call_result(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "load_skill", {}, "t1", FilterResult.SKILL_TRIGGER
        )
        await agg.on_tool_call_start(
            "web_search", {"query": "test"}, "t2", FilterResult.WHITELIST
        )
        events = await agg.on_tool_call_end("web_search", "t2", "results", False)
        assert len(agg.pending_card.absorbed_calls) == 1
        assert agg.pending_card.absorbed_calls[0].get("result") == "results"

    async def test_error_flag_recorded(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "load_skill", {}, "t1", FilterResult.SKILL_TRIGGER
        )
        await agg.on_tool_call_start(
            "web_search", {}, "t2", FilterResult.WHITELIST
        )
        await agg.on_tool_call_end("web_search", "t2", "error!", True)
        assert agg.pending_card.absorbed_calls[0].get("is_error") is True


class TestFlush:
    pytestmark = pytest.mark.asyncio

    async def test_flush_completes_skill(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "load_skill", {}, "t1", FilterResult.SKILL_TRIGGER
        )
        events = await agg.flush()
        assert agg.state == AggregatorState.IDLE
        assert any(e.get("status") == "completed" for e in events)

    async def test_flush_completes_mcp(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "call_mcp_tool", {"server": "gh"}, "t1", FilterResult.MCP_TRIGGER
        )
        events = await agg.flush()
        assert agg.state == AggregatorState.IDLE
        assert any(e.get("status") == "completed" for e in events)

    async def test_flush_idle_returns_empty(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        events = await agg.flush()
        assert events == []


class TestUserMentionIDLE:
    pytestmark = pytest.mark.asyncio

    async def test_user_mention_creates_card(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        events = await agg.on_tool_call_start(
            "read_file", {"path": "config.yaml"}, "t1", FilterResult.USER_MENTION
        )
        assert len(events) >= 1
        card = next(e for e in events if e["type"] == "step_card")
        assert card["status"] == "running"
        assert agg.state == AggregatorState.IDLE

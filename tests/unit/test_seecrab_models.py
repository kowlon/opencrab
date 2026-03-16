"""Tests for SeeCrab data models."""
from __future__ import annotations

from seeagent.api.adapters.seecrab_models import (
    AggregatorState,
    FilterResult,
    PendingCard,
    ReplyTimer,
    StepFilterConfig,
    StepTimer,
)


class TestAggregatorState:
    def test_all_states_exist(self):
        assert AggregatorState.IDLE.value == "idle"
        assert AggregatorState.SKILL_ABSORB.value == "skill_absorb"
        assert AggregatorState.MCP_ABSORB.value == "mcp_absorb"
        assert AggregatorState.PLAN_ABSORB.value == "plan_absorb"


class TestFilterResult:
    def test_all_results_exist(self):
        assert FilterResult.SKILL_TRIGGER.value == "skill_trigger"
        assert FilterResult.MCP_TRIGGER.value == "mcp_trigger"
        assert FilterResult.WHITELIST.value == "whitelist"
        assert FilterResult.USER_MENTION.value == "user_mention"
        assert FilterResult.HIDDEN.value == "hidden"


class TestStepFilterConfig:
    def test_defaults(self):
        config = StepFilterConfig()
        assert "web_search" in config.whitelist
        assert "load_skill" in config.skill_triggers
        assert "get_skill_info" not in config.skill_triggers
        assert config.mcp_trigger == "call_mcp_tool"
        assert "read_file" in config.user_mention_keywords

    def test_custom_whitelist(self):
        config = StepFilterConfig(whitelist=["custom_tool"])
        assert config.whitelist == ["custom_tool"]


class TestPendingCard:
    def test_defaults(self):
        card = PendingCard(step_id="s1", title="test")
        assert card.status == "running"
        assert card.source_type == ""
        assert card.agent_id == "main"
        assert card.absorbed_calls == []
        assert card.mcp_server is None

    def test_absorbed_calls_are_independent(self):
        c1 = PendingCard(step_id="s1", title="t1")
        c2 = PendingCard(step_id="s2", title="t2")
        c1.absorbed_calls.append({"tool": "x"})
        assert c2.absorbed_calls == []


class TestReplyTimer:
    def test_defaults(self):
        timer = ReplyTimer(reply_id="r1", t_request=100.0)
        assert timer.t_first_token is None
        assert timer.t_done is None
        assert timer.step_timers == {}


class TestStepTimer:
    def test_creation(self):
        t = StepTimer(step_id="s1", t_start=100.0)
        assert t.t_end is None

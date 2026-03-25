"""BPEngine core execution tests."""

import pytest

from seeagent.bestpractice.config import BestPracticeConfig
from seeagent.bestpractice.engine import BPEngine
from seeagent.bestpractice.models import SubtaskConfig, SubtaskStatus
from seeagent.bestpractice.state_manager import BPStateManager


class MockSession:
    def __init__(self, session_id="test-session"):
        self.id = session_id
        self.metadata = {}

        class MockContext:
            _sse_event_bus = None
        self.context = MockContext()


@pytest.fixture
def bp_config():
    return BestPracticeConfig(
        id="test-bp", name="测试BP", description="test",
        subtasks=[
            SubtaskConfig(
                id="s1", name="调研", agent_profile="researcher",
                input_schema={
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "description": "调研主题"},
                    },
                    "required": ["topic"],
                },
            ),
            SubtaskConfig(
                id="s2", name="分析", agent_profile="analyst",
                input_schema={
                    "type": "object",
                    "properties": {
                        "findings": {"type": "array", "description": "调研发现"},
                    },
                    "required": ["findings"],
                },
            ),
            SubtaskConfig(id="s3", name="报告", agent_profile="writer"),
        ],
    )


@pytest.fixture
def engine():
    return BPEngine(state_manager=BPStateManager())


# ── Chat-to-Edit ──────────────────────────────────────────────


class TestChatToEdit:
    def test_edit_output_success(self, engine, bp_config):
        session = MockSession()
        inst_id = engine.state_manager.create_instance(bp_config, session.id, {"topic": "AI"})
        engine.state_manager.update_subtask_output(inst_id, "s1", {"a": 1, "b": 2})
        engine.state_manager.update_subtask_status(inst_id, "s1", SubtaskStatus.DONE)
        engine.state_manager.update_subtask_status(inst_id, "s2", SubtaskStatus.DONE)
        engine.state_manager.update_subtask_output(inst_id, "s2", {"x": 10})

        result = engine.handle_edit_output(inst_id, "s1", {"b": 99, "c": 3}, bp_config)
        assert result["success"]
        assert result["merged"]["b"] == 99
        assert result["merged"]["c"] == 3
        assert "s2" in result["stale_subtasks"]

    def test_edit_nonexistent_output(self, engine, bp_config):
        session = MockSession()
        inst_id = engine.state_manager.create_instance(bp_config, session.id, {})
        result = engine.handle_edit_output(inst_id, "s1", {"x": 1}, bp_config)
        assert not result["success"]

    def test_edit_nonexistent_instance(self, engine, bp_config):
        result = engine.handle_edit_output("ghost", "s1", {}, bp_config)
        assert not result["success"]


# ── Parse output ──────────────────────────────────────────────


class TestParseOutput:
    def test_parse_json(self):
        assert BPEngine._parse_output('{"a": 1}') == {"a": 1}

    def test_parse_json_block(self):
        text = "Some text\n```json\n{\"x\": 2}\n```\nMore text"
        assert BPEngine._parse_output(text) == {"x": 2}

    def test_fallback_raw(self):
        result = BPEngine._parse_output("plain text response")
        assert result["_raw_output"] == "plain text response"


class TestConformOutputFallback:
    @pytest.mark.asyncio
    async def test_fills_required_fields_when_brain_unavailable(self, engine):
        engine._get_brain = lambda: None
        output_schema = {
            "type": "object",
            "properties": {
                "insights": {"type": "array"},
                "trends": {"type": "array"},
                "recommendations": {"type": "array"},
            },
            "required": ["insights", "trends"],
        }
        raw_output = {"_raw_output": "⚠️ 大模型返回异常：未产生可用输出。任务已中断。"}

        conformed = await engine._conform_output(
            raw_output=raw_output,
            output_schema=output_schema,
            raw_result_text=raw_output["_raw_output"],
            tool_results=[],
        )

        assert conformed["insights"] == []
        assert conformed["trends"] == []
        assert "_raw_output" not in conformed

    @pytest.mark.asyncio
    async def test_keeps_existing_required_values(self, engine):
        engine._get_brain = lambda: None
        output_schema = {
            "type": "object",
            "properties": {
                "insights": {"type": "array"},
                "summary": {"type": "string"},
            },
            "required": ["insights", "summary"],
        }
        raw_output = {"insights": [{"k": "v"}], "_raw_output": "fallback"}

        conformed = await engine._conform_output(
            raw_output=raw_output,
            output_schema=output_schema,
            raw_result_text="",
            tool_results=[],
        )

        assert conformed["insights"] == [{"k": "v"}]
        assert conformed["summary"] == ""
        assert "_raw_output" not in conformed

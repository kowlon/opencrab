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


# ── A2: _emit_progress subtasks array ────────────────────────


class MockEventBus:
    """Captures events put onto the SSE bus."""

    def __init__(self):
        self.events: list[dict] = []

    async def put(self, event: dict):
        self.events.append(event)


class TestEmitProgressSubtasks:
    """A2: _emit_progress should include subtasks array with id+name."""

    @pytest.mark.asyncio
    async def test_emit_progress_includes_subtasks_array(self, engine, bp_config):
        inst_id = engine.state_manager.create_instance(bp_config, "sess-1", {"topic": "AI"})
        bus = MockEventBus()
        session = MockSession()
        session.context._sse_event_bus = bus

        await engine._emit_progress(inst_id, session)

        assert len(bus.events) == 1
        data = bus.events[0]["data"]
        assert "subtasks" in data, "_emit_progress missing 'subtasks' array"
        assert isinstance(data["subtasks"], list)
        assert len(data["subtasks"]) == 3
        assert data["subtasks"][0] == {"id": "s1", "name": "调研"}
        assert data["subtasks"][1] == {"id": "s2", "name": "分析"}
        assert data["subtasks"][2] == {"id": "s3", "name": "报告"}


# ── C2: _emit_subtask_output extra fields ─────────────────────


class TestEmitSubtaskOutputFields:
    """C2: _emit_subtask_output should include subtask_name, output_schema, summary."""

    @pytest.mark.asyncio
    async def test_includes_subtask_name(self, engine, bp_config):
        inst_id = engine.state_manager.create_instance(bp_config, "sess-1", {"topic": "AI"})
        bus = MockEventBus()
        session = MockSession()
        session.context._sse_event_bus = bus

        output = {"findings": ["data1", "data2"]}
        await engine._emit_subtask_output(
            inst_id, "s1", output, session, bp_config=bp_config,
        )

        data = bus.events[0]["data"]
        assert data["subtask_name"] == "调研"

    @pytest.mark.asyncio
    async def test_includes_output_schema(self, engine, bp_config):
        """output_schema should be the NEXT subtask's input_schema."""
        inst_id = engine.state_manager.create_instance(bp_config, "sess-1", {"topic": "AI"})
        bus = MockEventBus()
        session = MockSession()
        session.context._sse_event_bus = bus

        output = {"findings": ["data1"]}
        await engine._emit_subtask_output(
            inst_id, "s1", output, session, bp_config=bp_config,
        )

        data = bus.events[0]["data"]
        assert "output_schema" in data
        # s1's output_schema = s2's input_schema
        assert data["output_schema"] == bp_config.subtasks[1].input_schema

    @pytest.mark.asyncio
    async def test_last_subtask_has_no_output_schema(self, engine, bp_config):
        """Last subtask has no downstream, output_schema should be None."""
        inst_id = engine.state_manager.create_instance(bp_config, "sess-1", {"topic": "AI"})
        bus = MockEventBus()
        session = MockSession()
        session.context._sse_event_bus = bus

        await engine._emit_subtask_output(
            inst_id, "s3", {"report": "done"}, session, bp_config=bp_config,
        )

        data = bus.events[0]["data"]
        assert data["output_schema"] is None

    @pytest.mark.asyncio
    async def test_includes_summary(self, engine, bp_config):
        inst_id = engine.state_manager.create_instance(bp_config, "sess-1", {"topic": "AI"})
        bus = MockEventBus()
        session = MockSession()
        session.context._sse_event_bus = bus

        output = {"key1": "value1", "key2": {"nested": True}}
        await engine._emit_subtask_output(
            inst_id, "s1", output, session, bp_config=bp_config,
        )

        data = bus.events[0]["data"]
        assert "summary" in data
        assert isinstance(data["summary"], str)
        assert "key1" in data["summary"]
        assert "key2" in data["summary"]

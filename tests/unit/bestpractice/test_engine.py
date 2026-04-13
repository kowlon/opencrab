"""BPEngine core execution tests."""

import pytest
from unittest.mock import MagicMock

from seeagent.bestpractice.config import BestPracticeConfig
from seeagent.bestpractice.engine import BPEngine
from seeagent.bestpractice.models import BPStatus, SubtaskConfig, SubtaskStatus
from seeagent.bestpractice.engine import BPStateManager


class MockSession:
    def __init__(self, session_id="test-session"):
        self.id = session_id
        self.metadata = {}

        class MockContext:
            _sse_event_bus = None
            _bp_delegate_task = None
            _bp_cancelled_instance = None
        self.context = MockContext()


@pytest.fixture
def bp_config():
    return BestPracticeConfig(
        id="test-bp", name="测试BP", description="test",
        final_output_schema={
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "score": {"type": "number"},
            },
            "required": ["summary"],
        },
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
        assert result["rerun_from_subtask_id"] == "s2"
        assert engine.state_manager.get(inst_id).current_subtask_index == 1

    def test_edit_nonexistent_output(self, engine, bp_config):
        session = MockSession()
        inst_id = engine.state_manager.create_instance(bp_config, session.id, {})
        result = engine.handle_edit_output(inst_id, "s1", {"x": 1}, bp_config)
        assert not result["success"]

    def test_edit_input_rewinds_from_current_subtask(self, engine, bp_config):
        session = MockSession()
        inst_id = engine.state_manager.create_instance(bp_config, session.id, {"topic": "AI"})
        snap = engine.state_manager.get(inst_id)
        snap.current_subtask_index = 1
        engine.state_manager.update_subtask_output(inst_id, "s1", {"findings": ["a"]})
        engine.state_manager.update_subtask_status(inst_id, "s1", SubtaskStatus.DONE)
        engine.state_manager.update_subtask_output(inst_id, "s2", {"analysis": "old"})
        engine.state_manager.update_subtask_status(inst_id, "s2", SubtaskStatus.DONE)

        result = engine.handle_edit_output(
            inst_id,
            "s2",
            {"findings": ["updated"]},
            bp_config,
            target_type="input",
        )

        assert result["success"]
        assert result["target_type"] == "input"
        assert result["merged"]["findings"] == ["updated"]
        assert result["resolved"]["findings"] == ["updated"]
        assert "s2" in result["stale_subtasks"]
        assert result["rerun_from_subtask_id"] == "s2"
        snap = engine.state_manager.get(inst_id)
        assert snap.current_subtask_index == 1
        assert snap.subtask_statuses["s2"] == SubtaskStatus.PENDING.value
        assert "s2" not in snap.subtask_outputs

    def test_edit_final_output_targets_last_subtask(self, engine, bp_config):
        session = MockSession()
        inst_id = engine.state_manager.create_instance(bp_config, session.id, {"topic": "AI"})
        engine.state_manager.update_subtask_output(inst_id, "s3", {"summary": "draft"})
        engine.state_manager.update_subtask_status(inst_id, "s3", SubtaskStatus.DONE)

        result = engine.handle_edit_output(
            inst_id,
            "",
            {"score": 9},
            bp_config,
            target_type="final_output",
        )

        assert result["success"]
        assert result["target_subtask_id"] == "s3"
        assert result["merged"]["summary"] == "draft"
        assert result["merged"]["score"] == 9

    def test_edit_nonexistent_instance(self, engine, bp_config):
        result = engine.handle_edit_output("ghost", "s1", {}, bp_config)
        assert not result["success"]

    def test_edit_first_subtask_input_distributes(self, engine, bp_config):
        # Add a shared field to s2's input schema so we can test distribution
        bp_config.subtasks[1].input_schema["properties"]["topic"] = {"type": "string"}

        session = MockSession()
        inst_id = engine.state_manager.create_instance(
            bp_config, session.id, initial_input={"topic": "A"}
        )
        engine._distribute_initial_input(inst_id, bp_config)
        snap = engine.state_manager.get(inst_id)
        assert snap.supplemented_inputs["s2"].get("topic") == "A"

        # Edit first subtask input
        res = engine.handle_edit_output(
            inst_id, "s1", {"topic": "B"}, bp_config, target_type="input"
        )
        assert res["success"] is True
        # The initial input should be updated
        assert snap.initial_input["topic"] == "B"
        # And distributed to downstream subtask
        assert snap.supplemented_inputs["s2"]["topic"] == "B"

    # ── COMPLETED → ACTIVE 重激活场景 ────────────────────────────

    def _make_all_done_completed(self, engine, bp_config, session):
        """辅助：创建实例并把所有子任务置为 DONE + 实例 COMPLETED。"""
        inst_id = engine.state_manager.create_instance(
            bp_config, session.id, {"topic": "AI"},
        )
        for i, st in enumerate(bp_config.subtasks):
            engine.state_manager.update_subtask_status(inst_id, st.id, SubtaskStatus.DONE)
            engine.state_manager.update_subtask_output(inst_id, st.id, {f"o_{st.id}": i})
        snap = engine.state_manager.get(inst_id)
        snap.current_subtask_index = len(bp_config.subtasks)
        engine.state_manager.complete(inst_id)
        return inst_id

    def test_edit_completed_output_reactivates_to_active(self, engine, bp_config):
        """全部 DONE + COMPLETED，编辑 s1 output → 实例应被重激活为 ACTIVE 以便 bp_next 可定位。"""
        session = MockSession()
        inst_id = self._make_all_done_completed(engine, bp_config, session)

        result = engine.handle_edit_output(inst_id, "s1", {"o_s1": 99}, bp_config)

        assert result["success"]
        assert result.get("reactivated") is True
        snap = engine.state_manager.get(inst_id)
        assert snap.status == BPStatus.ACTIVE
        assert snap.completed_at is None
        # invalidate_from_subtask 应该已将 s2/s3 回退为 PENDING 并清理 outputs
        assert snap.subtask_statuses["s2"] == SubtaskStatus.PENDING.value
        assert snap.subtask_statuses["s3"] == SubtaskStatus.PENDING.value
        assert "s2" not in snap.subtask_outputs
        assert "s3" not in snap.subtask_outputs
        # 重激活后 get_active() 应能找到实例（验证核心修复效果）
        active = engine.state_manager.get_active(session.id)
        assert active is not None
        assert active.instance_id == inst_id

    def test_edit_completed_last_subtask_does_not_reactivate(self, engine, bp_config):
        """COMPLETED 时编辑最后一步 output（无下游）不应重激活，BP 保持 COMPLETED。"""
        session = MockSession()
        inst_id = self._make_all_done_completed(engine, bp_config, session)

        result = engine.handle_edit_output(inst_id, "s3", {"o_s3": 99}, bp_config)

        assert result["success"]
        # 最后一步没有下游，invalidation 为空，不触发重激活
        assert result.get("reactivated") is not True
        snap = engine.state_manager.get(inst_id)
        assert snap.status == BPStatus.COMPLETED
        assert snap.completed_at is not None

    def test_edit_completed_input_reactivates_and_rewinds(self, engine, bp_config):
        """COMPLETED 时用 target_type=input 编辑 s2 的输入，应重激活并回退到 s2。"""
        session = MockSession()
        inst_id = self._make_all_done_completed(engine, bp_config, session)

        result = engine.handle_edit_output(
            inst_id, "s2", {"findings": ["new"]}, bp_config, target_type="input",
        )

        assert result["success"]
        assert result.get("reactivated") is True
        snap = engine.state_manager.get(inst_id)
        assert snap.status == BPStatus.ACTIVE
        assert snap.current_subtask_index == 1  # s2 的位置


class TestLifecycleHelpers:
    @pytest.mark.asyncio
    async def test_request_suspend_marks_state_and_persists(self, engine, bp_config):
        session = MockSession()
        inst_id = engine.state_manager.create_instance(
            bp_config, session.id, {"topic": "AI"},
        )
        task = MagicMock()
        task.done.return_value = False
        session.context._bp_delegate_task = task

        assert await engine.request_suspend(
            inst_id, session, "disconnect", pending_target_id="bp-next",
        )

        snap = engine.state_manager.get(inst_id)
        assert snap.status == BPStatus.SUSPENDED
        assert session.context._bp_cancelled_instance == inst_id
        task.cancel.assert_called_once()
        pending = engine.state_manager.consume_pending_switch(session.id)
        assert pending is not None
        assert pending.suspended_instance_id == inst_id
        assert pending.target_instance_id == "bp-next"
        assert "bp_state" in session.metadata

    @pytest.mark.asyncio
    async def test_resume_if_needed_resumes_suspended_instance(self, engine, bp_config):
        session = MockSession()
        inst_id = engine.state_manager.create_instance(
            bp_config, session.id, {"topic": "AI"},
        )
        engine.state_manager.suspend(inst_id)
        session.context._bp_cancelled_instance = inst_id

        result = await engine.resume_if_needed(inst_id, session)

        assert result == {"success": True, "resumed": True}
        snap = engine.state_manager.get(inst_id)
        assert snap.status == BPStatus.ACTIVE
        assert session.context._bp_cancelled_instance is None

    @pytest.mark.asyncio
    async def test_resume_if_needed_conflicts_with_other_active(self, engine, bp_config):
        session = MockSession()
        target_id = engine.state_manager.create_instance(
            bp_config, session.id, {"topic": "AI"},
        )
        engine.state_manager.suspend(target_id)
        other_id = engine.state_manager.create_instance(
            bp_config, session.id, {"topic": "ML"},
        )

        result = await engine.resume_if_needed(target_id, session)

        assert result["success"] is False
        assert result["code"] == "conflict"
        assert result["active_instance_id"] == other_id


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

    def test_nested_json_in_text(self):
        """嵌套 JSON：外层是完整结果，内层是 extracted_input 子对象。
        rfind 曾只找到内层 { 导致解析失败。"""
        text = (
            '分析结果: {"matched": true, "bp_id": "camera-frame-search", '
            '"confidence": 0.9, "extracted_input": {"query": "衡州大道"}}'
        )
        result = BPEngine._parse_output(text)
        assert result["matched"] is True
        assert result["bp_id"] == "camera-frame-search"
        assert result["extracted_input"] == {"query": "衡州大道"}

    def test_multiple_json_fragments_prefers_last(self):
        """多个独立 JSON 片段：前面是示例/解释，后面才是最终结果。
        应优先选后面的对象。"""
        text = (
            '示例: {"matched": true, "bp_id": "wrong", "confidence": 0.5}\n'
            '结果: {"matched": true, "bp_id": "correct", "confidence": 0.9}'
        )
        result = BPEngine._parse_output(text)
        assert result["bp_id"] == "correct"
        assert result["confidence"] == 0.9


# ── Delegation message with partial results ──────────────────


class TestDelegationWithPartialResults:
    def test_delegationIncludesPartialResultsTest(self, engine, bp_config):
        """Delegation message includes partial results when snap has them."""
        from seeagent.bestpractice.models import BPInstanceSnapshot

        snap = BPInstanceSnapshot(
            bp_id="test-bp", instance_id="bp-test", session_id="sess-1",
            subtask_statuses={"s1": "pending"},
            subtask_partial_results={"s1": ["search result 1", "search result 2"]},
        )
        subtask = bp_config.subtasks[0]
        msg = engine._build_delegation_message(
            bp_config, subtask, {"topic": "AI"}, None, snap=snap,
        )

        assert "已完成进展" in msg
        assert "search result 1" in msg
        assert "search result 2" in msg
        assert "被中断" in msg

    def test_delegationNoPartialResultsTest(self, engine, bp_config):
        """No partial results section when snap has no partial data."""
        from seeagent.bestpractice.models import BPInstanceSnapshot

        snap = BPInstanceSnapshot(
            bp_id="test-bp", instance_id="bp-test", session_id="sess-1",
            subtask_statuses={"s1": "pending"},
        )
        subtask = bp_config.subtasks[0]
        msg = engine._build_delegation_message(
            bp_config, subtask, {"topic": "AI"}, None, snap=snap,
        )

        assert "已完成进展" not in msg

    def test_delegationWithoutSnapTest(self, engine, bp_config):
        """Delegation message works when snap is None."""
        subtask = bp_config.subtasks[0]
        msg = engine._build_delegation_message(
            bp_config, subtask, {"topic": "AI"}, None, snap=None,
        )

        assert "已完成进展" not in msg
        assert "当前子任务" in msg


class TestFormatPartialResults:
    def test_formatPartialResultsTest(self, engine):
        partial = ["result A", "result B"]
        result = engine._format_partial_results(partial)

        assert "已完成进展" in result
        assert "结果 1" in result
        assert "result A" in result
        assert "结果 2" in result
        assert "result B" in result

    def test_formatPartialResultsTruncatesLongResultsTest(self, engine):
        partial = ["x" * 5000]
        result = engine._format_partial_results(partial)

        assert len(result) < 5000


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


class TestSanitizeOutput:
    """_sanitize_output properties 白名单过滤测试。"""

    def test_strips_fields_not_in_schema_properties(self):
        """schema 声明了 properties 时，剔除 schema 外的多余字段。"""
        output = {
            "findings": [{"topic": "AI"}],
            "raw_data": {"src": "web"},
            "hello": "should be removed",
            "extra": "also removed",
        }
        schema = {
            "type": "object",
            "properties": {
                "findings": {"type": "array"},
                "raw_data": {"type": "object"},
            },
            "required": ["findings"],
        }
        result = BPEngine._sanitize_output(output, schema)
        assert "findings" in result
        assert "raw_data" in result
        assert "hello" not in result
        assert "extra" not in result

    def test_keeps_all_when_no_properties(self):
        """schema 无 properties 定义时，保留所有非 _ 前缀字段。"""
        output = {"a": 1, "b": 2, "_internal": 3}
        schema = {"type": "object", "required": ["a"]}
        result = BPEngine._sanitize_output(output, schema)
        assert result == {"a": 1, "b": 2}
        assert "_internal" not in result

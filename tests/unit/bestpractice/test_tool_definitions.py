"""Tests for BP tool definitions completeness."""
from seeagent.bestpractice.tool_definitions import BP_TOOL_DEFINITIONS, get_bp_tool_names


class TestToolDefinitions:
    def test_all_six_tools_defined(self):
        names = get_bp_tool_names()
        expected = {"bp_start", "bp_edit_output", "bp_switch_task", "bp_next", "bp_answer", "bp_cancel"}
        assert set(names) == expected

    def test_bp_next_schema(self):
        bp_next = next(t for t in BP_TOOL_DEFINITIONS if t["name"] == "bp_next")
        props = bp_next["input_schema"]["properties"]
        assert "instance_id" in props

    def test_bp_answer_schema(self):
        bp_answer = next(t for t in BP_TOOL_DEFINITIONS if t["name"] == "bp_answer")
        assert "subtask_id" in bp_answer["input_schema"]["required"]
        assert "data" in bp_answer["input_schema"]["required"]

    def test_bp_edit_output_schema(self):
        bp_edit_output = next(t for t in BP_TOOL_DEFINITIONS if t["name"] == "bp_edit_output")
        props = bp_edit_output["input_schema"]["properties"]
        assert "target_type" in props
        assert props["target_type"]["enum"] == ["input", "output", "final_output"]
        assert "changes" in bp_edit_output["input_schema"]["required"]

    def test_bp_cancel_schema(self):
        bp_cancel = next(t for t in BP_TOOL_DEFINITIONS if t["name"] == "bp_cancel")
        props = bp_cancel["input_schema"]["properties"]
        assert "instance_id" in props

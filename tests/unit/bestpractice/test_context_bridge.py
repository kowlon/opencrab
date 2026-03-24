"""ContextBridge tests — compression and restoration."""

from types import SimpleNamespace

import pytest

from seeagent.bestpractice.context_bridge import ContextBridge
from seeagent.bestpractice.state_manager import BPStateManager


@pytest.fixture
def bridge():
    sm = BPStateManager()
    return ContextBridge(state_manager=sm)


def _make_snap(**kwargs):
    defaults = {
        "context_summary": "",
        "subtask_outputs": {},
        "bp_config": None,
        "bp_id": "test-bp",
        "current_subtask_index": 0,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ── Compression ────────────────────────────────────────────────


class TestCompress:
    def test_compress_with_messagesTest(self, bridge):
        messages = [
            {"role": "user", "content": f"msg-{i}"} for i in range(10)
        ]
        result = bridge._compress_context(messages=messages)
        assert "Recent messages" in result
        assert "msg-9" in result

    def test_compress_with_subtask_outputsTest(self, bridge):
        snap = _make_snap(subtask_outputs={"s1": {"result": "done"}})
        result = bridge._compress_context(snap=snap)
        assert "Subtask outputs" in result
        assert "s1" in result
        assert "done" in result

    def test_compress_list_content_blocksTest(self, bridge):
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "hello world"}]},
        ]
        result = bridge._compress_context(messages=messages)
        assert "hello world" in result

    def test_compress_empty_returns_emptyTest(self, bridge):
        result = bridge._compress_context(messages=[], snap=None)
        assert result == ""

    def test_compress_both_messages_and_outputsTest(self, bridge):
        snap = _make_snap(subtask_outputs={"s1": {"x": 1}})
        messages = [{"role": "user", "content": "question"}]
        result = bridge._compress_context(messages=messages, snap=snap)
        assert "Subtask outputs" in result
        assert "Recent messages" in result


# ── Restoration ─────────────────────────────────────────────────


class TestRestore:
    def test_restore_merges_into_user_messageTest(self, bridge):
        messages = [
            {"role": "user", "content": "original question"},
        ]
        snap = _make_snap(context_summary="previous task context")
        bridge._restore_context(messages, snap)
        assert len(messages) == 1
        assert "previous task context" in messages[0]["content"]
        assert "original question" in messages[0]["content"]

    def test_restore_appends_when_last_is_assistantTest(self, bridge):
        messages = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]
        snap = _make_snap(context_summary="task context")
        bridge._restore_context(messages, snap)
        assert len(messages) == 3
        assert messages[2]["role"] == "user"
        assert "task context" in messages[2]["content"]

    def test_restore_handles_multimodal_contentTest(self, bridge):
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "see image"}]},
        ]
        snap = _make_snap(context_summary="restoring task")
        bridge._restore_context(messages, snap)
        assert len(messages) == 1
        content = messages[0]["content"]
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[1]["type"] == "text"
        assert "restoring task" in content[1]["text"]

    def test_restore_skips_when_no_summaryTest(self, bridge):
        messages = [{"role": "user", "content": "q"}]
        snap = _make_snap(context_summary="")
        bridge._restore_context(messages, snap)
        assert len(messages) == 1
        assert messages[0]["content"] == "q"

    def test_restore_appends_to_empty_messagesTest(self, bridge):
        messages = []
        snap = _make_snap(context_summary="task context")
        bridge._restore_context(messages, snap)
        assert len(messages) == 1
        assert messages[0]["role"] == "user"


# ── Extract text ────────────────────────────────────────────────


class TestExtractText:
    def test_string_contentTest(self):
        assert ContextBridge._extract_text("hello") == "hello"

    def test_list_contentTest(self):
        content = [
            {"type": "text", "text": "hello"},
            {"type": "image", "source": {}},
            {"type": "text", "text": "world"},
        ]
        result = ContextBridge._extract_text(content)
        assert "hello" in result
        assert "world" in result

    def test_tool_result_contentTest(self):
        content = [{"type": "tool_result", "content": "success"}]
        result = ContextBridge._extract_text(content)
        assert "tool_result" in result
        assert "success" in result

    def test_none_contentTest(self):
        assert ContextBridge._extract_text(None) == ""
        assert ContextBridge._extract_text(123) == ""

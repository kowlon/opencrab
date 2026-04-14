"""
单元测试 - OpenAIProvider._convert_stream_event 流式事件归一化

验证 4 个历史 gap 已修复:
1. delta.content is None (glm-5 thinking 阶段) 不进入 text 分支
2. delta.reasoning_content 识别为 reasoning delta
3. delta.tool_calls 保留完整列表 (含 index)
4. event 顶层 usage 在末 chunk 时 relay
"""

import pytest
from seeagent.llm.providers.openai import OpenAIProvider


@pytest.fixture
def provider():
    """构造一个最小 Provider 用于调用 _convert_stream_event (不需要真实 config)."""
    p = OpenAIProvider.__new__(OpenAIProvider)
    return p


class TestContentNoneNotRouted:
    """gap-1: delta.content=None 不应进入 text 分支"""

    def test_content_is_none_no_text_delta(self, provider):
        event = {
            "choices": [
                {"delta": {"content": None, "role": "assistant"}, "index": 0}
            ],
            "model": "glm-5",
        }
        result = provider._convert_stream_event(event)
        assert result["type"] == "content_block_delta"
        assert "delta" not in result  # 既不是 text 也不是其他类型

    def test_content_present_routes_to_text(self, provider):
        event = {
            "choices": [
                {"delta": {"content": "hello"}, "index": 0}
            ]
        }
        result = provider._convert_stream_event(event)
        assert result["type"] == "content_block_delta"
        assert result["delta"] == {"type": "text", "text": "hello"}

    def test_empty_string_content_still_routes_to_text(self, provider):
        # delta.content == "" 是合法的 (服务端可能发送空字符串占位)
        event = {
            "choices": [
                {"delta": {"content": ""}, "index": 0}
            ]
        }
        result = provider._convert_stream_event(event)
        assert result["delta"]["type"] == "text"
        assert result["delta"]["text"] == ""


class TestReasoningContentRecognized:
    """gap-2: delta.reasoning_content 识别为 reasoning delta"""

    def test_reasoning_content_basic(self, provider):
        event = {
            "choices": [
                {
                    "delta": {
                        "content": None,
                        "reasoning_content": "用户想要",
                        "role": "assistant",
                    },
                    "index": 0,
                }
            ],
            "model": "glm-5",
        }
        result = provider._convert_stream_event(event)
        assert result["type"] == "content_block_delta"
        assert result["delta"] == {"type": "reasoning", "reasoning": "用户想要"}

    def test_reasoning_takes_precedence_only_when_content_is_none(self, provider):
        # content 非空时应该走 text 分支, 不进入 reasoning
        event = {
            "choices": [
                {
                    "delta": {
                        "content": "ans",
                        "reasoning_content": "should-not-be-used",
                    },
                    "index": 0,
                }
            ]
        }
        result = provider._convert_stream_event(event)
        assert result["delta"]["type"] == "text"
        assert result["delta"]["text"] == "ans"


class TestToolCallsPreserveIndex:
    """gap-3: tool_calls 保留完整列表 (含 index)"""

    def test_single_tool_call_with_index(self, provider):
        event = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_abc",
                                "type": "function",
                                "function": {"name": "foo", "arguments": ""},
                            }
                        ]
                    },
                    "index": 0,
                }
            ]
        }
        result = provider._convert_stream_event(event)
        assert result["delta"]["type"] == "tool_calls_delta"
        assert result["delta"]["tool_calls"] == [
            {
                "index": 0,
                "id": "call_abc",
                "type": "function",
                "function": {"name": "foo", "arguments": ""},
            }
        ]

    def test_multi_tool_calls_preserved(self, provider):
        event = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "c1", "function": {"name": "a"}},
                            {"index": 1, "id": "c2", "function": {"name": "b"}},
                        ]
                    },
                    "index": 0,
                }
            ]
        }
        result = provider._convert_stream_event(event)
        # 完整列表 (两个 tool call) 都应该保留, 不只取 [0]
        assert len(result["delta"]["tool_calls"]) == 2
        assert result["delta"]["tool_calls"][0]["index"] == 0
        assert result["delta"]["tool_calls"][1]["index"] == 1

    def test_tool_calls_arguments_fragment(self, provider):
        # 后续 chunk 只有 arguments 片段, 没有 id/name
        event = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": '{"x":'}}
                        ]
                    },
                    "index": 0,
                }
            ]
        }
        result = provider._convert_stream_event(event)
        assert result["delta"]["tool_calls"][0]["function"]["arguments"] == '{"x":'


class TestUsageRelay:
    """gap-4: 顶层 usage 在末 chunk relay"""

    def test_usage_at_top_level(self, provider):
        event = {
            "choices": [
                {"delta": {}, "finish_reason": "stop", "index": 0}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "model": "glm-5",
            "id": "chatcmpl-1",
        }
        result = provider._convert_stream_event(event)
        assert result["usage"] == {"prompt_tokens": 10, "completion_tokens": 5}
        # 末 chunk 还应该带 message_stop / stop_reason / model / id
        assert result["type"] == "message_stop"
        assert result["stop_reason"] == "stop"
        assert result["model"] == "glm-5"
        assert result["id"] == "chatcmpl-1"

    def test_no_usage_no_field(self, provider):
        event = {
            "choices": [
                {"delta": {"content": "x"}, "index": 0}
            ]
        }
        result = provider._convert_stream_event(event)
        assert "usage" not in result


class TestPingAndStop:
    """边缘场景"""

    def test_no_choices_yields_ping(self, provider):
        result = provider._convert_stream_event({"choices": []})
        assert result["type"] == "ping"

    def test_finish_reason_promotes_to_message_stop(self, provider):
        event = {
            "choices": [
                {"delta": {"content": "tail"}, "finish_reason": "stop", "index": 0}
            ],
            "model": "glm-5",
            "id": "chatcmpl-z",
        }
        result = provider._convert_stream_event(event)
        assert result["type"] == "message_stop"
        assert result["stop_reason"] == "stop"
        # 末 chunk 仍可携带 delta (text 内容)
        assert result["delta"]["type"] == "text"
        assert result["delta"]["text"] == "tail"

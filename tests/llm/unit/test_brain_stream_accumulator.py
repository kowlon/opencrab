"""
单元测试 - Brain.messages_create_async_stream 流式累积器

验证从 chat_stream 事件序列正确累积成 LLMResponse + AnthropicMessage:
- 纯文本 / 纯 thinking / thinking+text / 单工具 / 多工具 / 异常 / 空 stream / usage 缺失 / 透明包装
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock

from seeagent.llm.types import LLMError


def _make_brain():
    """构建 Brain 实例 (mock 外部依赖, 仅保留 stream 累积逻辑)."""
    with (
        patch("seeagent.core.brain.settings") as mock_settings,
        patch("seeagent.core.brain.get_default_config_path") as mock_config_path,
        patch("seeagent.core.brain.LLMClient") as mock_client,
    ):
        mock_settings.max_tokens = 4096
        mock_settings.thinking_mode = "auto"
        mock_config_path.return_value = MagicMock(exists=MagicMock(return_value=False))
        mock_client.return_value = MagicMock(endpoints=[], providers={}, _providers={})
        from seeagent.core.brain import Brain
        return Brain()


async def _fake_stream(events: list):
    for e in events:
        yield e


def _install_chat_stream(brain, events_or_factory):
    """把 events 装到 brain._llm_client.chat_stream 上 (支持 list 或 factory)."""
    if callable(events_or_factory):
        brain._llm_client.chat_stream = lambda **kwargs: events_or_factory()
    else:
        brain._llm_client.chat_stream = lambda **kwargs: _fake_stream(list(events_or_factory))


async def _consume(brain, **kwargs):
    """消费 messages_create_async_stream 的所有事件, 返回 (events, done_response)."""
    events = []
    done = None
    async for ev in brain.messages_create_async_stream(messages=[], system="", tools=None, **kwargs):
        events.append(ev)
        if ev.get("type") == "done":
            done = ev["response"]
    return events, done


@pytest.mark.asyncio
class TestStreamAccumulator:

    async def test_pure_text(self):
        """TC-01: 纯文本 chunks → 单一 TextBlock + END_TURN."""
        brain = _make_brain()
        _install_chat_stream(brain, [
            {"type": "content_block_delta", "delta": {"type": "text", "text": "Hello"}},
            {"type": "content_block_delta", "delta": {"type": "text", "text": ", World"}},
            {"type": "message_stop", "stop_reason": "stop", "model": "glm-5", "id": "id-1"},
        ])
        events, done = await _consume(brain)
        assert done is not None
        assert done.model == "glm-5"
        assert done.id == "id-1"
        assert done.stop_reason == "end_turn"
        # 一个 TextBlock, content == "Hello, World"
        text_blocks = [b for b in done.content if getattr(b, "type", "") == "text"]
        assert len(text_blocks) == 1
        assert text_blocks[0].text == "Hello, World"
        # 期间没有 reasoning_delta 事件 yield
        assert all(e["type"] != "reasoning_delta" for e in events)

    async def test_thinking_and_text(self):
        """TC-02: reasoning chunks + text chunks → reasoning_content 字段非空, 期间 yield N 个 reasoning_delta."""
        brain = _make_brain()
        _install_chat_stream(brain, [
            {"type": "content_block_delta", "delta": {"type": "reasoning", "reasoning": "用户"}},
            {"type": "content_block_delta", "delta": {"type": "reasoning", "reasoning": "想要"}},
            {"type": "content_block_delta", "delta": {"type": "text", "text": "回答"}},
            {"type": "message_stop", "stop_reason": "stop"},
        ])
        events, done = await _consume(brain)
        # 增量事件应该 yield 了 2 次 reasoning_delta
        reasoning_evs = [e for e in events if e["type"] == "reasoning_delta"]
        assert len(reasoning_evs) == 2
        assert reasoning_evs[0]["text"] == "用户"
        assert reasoning_evs[1]["text"] == "想要"
        # done.response.reasoning_content 拼接完整
        # 注意 brain._convert_response_to_anthropic 会把 reasoning_content 包成 <thinking>...</thinking> 嵌入 TextBlock
        text_blocks = [b for b in done.content if getattr(b, "type", "") == "text"]
        all_text = "".join(b.text for b in text_blocks)
        assert "<thinking>" in all_text and "用户想要" in all_text
        assert "回答" in all_text

    async def test_single_tool_call_args_fragmented(self):
        """TC-03: 单工具 + arguments 分多 chunks 累积."""
        brain = _make_brain()
        _install_chat_stream(brain, [
            {"type": "content_block_delta", "delta": {
                "type": "tool_calls_delta",
                "tool_calls": [
                    {"index": 0, "id": "call_abc", "type": "function",
                     "function": {"name": "get_weather", "arguments": ""}}
                ],
            }},
            {"type": "content_block_delta", "delta": {
                "type": "tool_calls_delta",
                "tool_calls": [
                    {"index": 0, "function": {"arguments": '{"location"'}}
                ],
            }},
            {"type": "content_block_delta", "delta": {
                "type": "tool_calls_delta",
                "tool_calls": [
                    {"index": 0, "function": {"arguments": ': "Beijing"}'}}
                ],
            }},
            {"type": "message_stop", "stop_reason": "tool_calls"},
        ])
        _, done = await _consume(brain)
        tool_blocks = [b for b in done.content if getattr(b, "type", "") == "tool_use"]
        assert len(tool_blocks) == 1
        tu = tool_blocks[0]
        assert tu.id == "call_abc"
        assert tu.name == "get_weather"
        assert tu.input == {"location": "Beijing"}
        assert done.stop_reason == "tool_use"

    async def test_multi_tool_calls_by_index(self):
        """TC-04: 多工具按 index 聚合."""
        brain = _make_brain()
        _install_chat_stream(brain, [
            {"type": "content_block_delta", "delta": {
                "type": "tool_calls_delta",
                "tool_calls": [
                    {"index": 0, "id": "c1", "type": "function",
                     "function": {"name": "tool_a", "arguments": '{"a": 1}'}},
                    {"index": 1, "id": "c2", "type": "function",
                     "function": {"name": "tool_b", "arguments": '{"b": 2}'}},
                ],
            }},
            {"type": "message_stop", "stop_reason": "tool_calls"},
        ])
        _, done = await _consume(brain)
        tool_blocks = [b for b in done.content if getattr(b, "type", "") == "tool_use"]
        assert len(tool_blocks) == 2
        assert tool_blocks[0].name == "tool_a"
        assert tool_blocks[0].input == {"a": 1}
        assert tool_blocks[1].name == "tool_b"
        assert tool_blocks[1].input == {"b": 2}

    async def test_mid_stream_exception_propagates(self):
        """TC-05: stream 中途异常应原样向上抛."""
        brain = _make_brain()

        async def _failing_factory():
            yield {"type": "content_block_delta", "delta": {"type": "text", "text": "partial"}}
            raise ConnectionError("stream interrupted")

        brain._llm_client.chat_stream = lambda **kwargs: _failing_factory()
        with pytest.raises(ConnectionError, match="stream interrupted"):
            await _consume(brain)

    async def test_empty_stream_raises_llm_error(self):
        """TC-06: 零 chunks → raise LLMError."""
        brain = _make_brain()
        _install_chat_stream(brain, [])
        with pytest.raises(LLMError, match="empty response"):
            await _consume(brain)

    async def test_missing_stop_reason_defaults_to_end_turn(self):
        """TC-07: 末 chunk 无 finish_reason → 降级 END_TURN, 不抛异常."""
        brain = _make_brain()
        _install_chat_stream(brain, [
            {"type": "content_block_delta", "delta": {"type": "text", "text": "ok"}},
            # 注意: 没有 message_stop
        ])
        _, done = await _consume(brain)
        assert done is not None
        assert done.stop_reason == "end_turn"

    async def test_missing_usage_zero_fallback(self):
        """TC-08: usage 缺失 → Usage() 全零, response 仍能构造."""
        brain = _make_brain()
        _install_chat_stream(brain, [
            {"type": "content_block_delta", "delta": {"type": "text", "text": "x"}},
            {"type": "message_stop", "stop_reason": "stop"},
        ])
        _, done = await _consume(brain)
        assert done.usage.input_tokens == 0
        assert done.usage.output_tokens == 0

    async def test_usage_in_final_chunk(self):
        """补充: usage 在末 chunk 时被正确提取."""
        brain = _make_brain()
        _install_chat_stream(brain, [
            {"type": "content_block_delta", "delta": {"type": "text", "text": "x"}},
            {"type": "message_stop", "stop_reason": "stop",
             "usage": {"prompt_tokens": 100, "completion_tokens": 50}},
        ])
        _, done = await _consume(brain)
        assert done.usage.input_tokens == 100
        assert done.usage.output_tokens == 50


@pytest.mark.asyncio
class TestStreamIdleHangDetection:
    """chunk-level idle / hang 检测在 brain 层 (方案 B)."""

    async def test_chunk_idle_timeout_raises_llm_error(self, monkeypatch):
        """两个 chunk 之间间隔 > idle limit → LLMError."""
        brain = _make_brain()

        async def _stuck_stream():
            yield {"type": "content_block_delta", "delta": {"type": "text", "text": "ok"}}
            await asyncio.sleep(5)  # 远超 idle limit
            yield {"type": "message_stop", "stop_reason": "stop"}

        brain._llm_client.chat_stream = lambda **kw: _stuck_stream()
        # 把 idle limit 调小, 让 timeout 在测试时间内触发
        monkeypatch.setenv("SEEAGENT_BRAIN_STREAM_IDLE_LIMIT", "0.3")

        with pytest.raises(LLMError, match="chunk idle"):
            await _consume(brain)

    async def test_pure_text_long_output_no_idle_misfire(self, monkeypatch):
        """方案 B 回归: 纯 text 长输出 (无 reasoning_delta) 不应误判 idle。

        即使上层 (reasoning_engine) 完全不消费事件, 只要底层 chat_stream
        持续推送 chunks (即使是 text), 就算正常进展, 不抛 idle error。
        """
        brain = _make_brain()

        async def _long_text_stream():
            for _ in range(100):
                yield {"type": "content_block_delta", "delta": {"type": "text", "text": "x"}}
                await asyncio.sleep(0.005)  # 远小于 idle limit, 但累积总时长会超过 idle limit
            yield {"type": "message_stop", "stop_reason": "stop"}

        brain._llm_client.chat_stream = lambda **kw: _long_text_stream()
        # idle limit 0.1s, 单 chunk 间隔 0.005s, 总时长 ~0.5s
        # 旧实现 (单 idle limit = 总时长) 会在 0.1s 时误判;
        # 新实现 (chunk idle limit) 不应触发, 因为 chunk 间隔从未超 0.1s
        monkeypatch.setenv("SEEAGENT_BRAIN_STREAM_IDLE_LIMIT", "0.1")

        events, done = await _consume(brain)
        assert done is not None
        text_blocks = [b for b in done.content if getattr(b, "type", "") == "text"]
        assert len(text_blocks) == 1
        assert len(text_blocks[0].text) == 100  # 100 个 'x'
        # 没有 reasoning_delta 事件 (因为没有 reasoning chunk)
        assert all(e["type"] != "reasoning_delta" for e in events)


@pytest.mark.asyncio
class TestMessagesCreateAsyncTransparency:
    """TC-09: messages_create_async 内部消费 stream 后透明返回 AnthropicMessage."""

    async def test_messages_create_async_returns_anthropic_message(self):
        brain = _make_brain()
        _install_chat_stream(brain, [
            {"type": "content_block_delta", "delta": {"type": "text", "text": "hi"}},
            {"type": "message_stop", "stop_reason": "stop", "model": "glm-5"},
        ])
        # 因为 mock 的 _providers 是空 dict, _supports_stream_accumulation 会返回 False
        # 走非流式 fallback 分支. 这里需要 patch 一下让它走 stream 分支.
        with patch.object(brain, "_supports_stream_accumulation", return_value=True):
            response = await brain.messages_create_async(messages=[], system="")
        assert response is not None
        # AnthropicMessage 类型, 通过字段访问验证
        assert hasattr(response, "content")
        assert hasattr(response, "stop_reason")
        text_blocks = [b for b in response.content if getattr(b, "type", "") == "text"]
        assert any("hi" in b.text for b in text_blocks)

    async def test_supports_stream_check_falls_back_when_no_providers(self):
        """fallback: _supports_stream_accumulation=False → 走 _messages_create_async_nonstream."""
        brain = _make_brain()
        # mock 非流式版本
        async def _fake_nonstream(**kwargs):
            return "nonstream-result"
        brain._messages_create_async_nonstream = _fake_nonstream
        # _supports_stream_accumulation 默认 False (空 _providers)
        result = await brain.messages_create_async(messages=[], system="")
        assert result == "nonstream-result"

    async def test_disable_stream_env_flag_falls_back(self, monkeypatch):
        """SEEAGENT_BRAIN_DISABLE_STREAM=1 → 走非流式分支."""
        brain = _make_brain()

        async def _fake_nonstream(**kwargs):
            return "nonstream-result"
        brain._messages_create_async_nonstream = _fake_nonstream
        # 即使 stream 支持也要 fallback
        with patch.object(brain, "_supports_stream_accumulation", return_value=True):
            monkeypatch.setenv("SEEAGENT_BRAIN_DISABLE_STREAM", "1")
            result = await brain.messages_create_async(messages=[], system="")
        assert result == "nonstream-result"

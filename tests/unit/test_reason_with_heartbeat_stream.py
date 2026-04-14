"""
单元测试 - ReasoningEngine._reason_with_heartbeat 流式改造

验证:
- 流式路径下 reasoning_delta → thinking_delta_incremental
- done 事件 → 经 _parse_decision 转成 decision
- idle > HEARTBEAT_INTERVAL → yield heartbeat
- idle > IDLE_HARD_LIMIT → 抛 LLMError
- cancel_event → 抛 UserCancelledError
- 非 OpenAI provider / feature flag 关 → 降级 _reason_with_heartbeat_nonstream
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from anthropic.types import Message as AnthropicMessage
from anthropic.types import TextBlock as AnthropicTextBlock
from anthropic.types import Usage as AnthropicUsage

from seeagent.llm.types import LLMError
from seeagent.core.reasoning_engine import ReasoningEngine, UserCancelledError, DecisionType


def _make_anthropic_message(text: str = "ok", thinking: str = "") -> AnthropicMessage:
    """构造一个简单的 AnthropicMessage 给 _parse_decision 用."""
    full = f"<thinking>{thinking}</thinking>\n{text}" if thinking else text
    return AnthropicMessage(
        id="msg_1",
        type="message",
        role="assistant",
        content=[AnthropicTextBlock(type="text", text=full)],
        model="glm-5",
        stop_reason="end_turn",
        stop_sequence=None,
        usage=AnthropicUsage(input_tokens=10, output_tokens=5),
    )


def _make_engine(brain_mock):
    """构造一个最小 ReasoningEngine 实例."""
    engine = ReasoningEngine.__new__(ReasoningEngine)
    engine._brain = brain_mock
    engine._state = MagicMock()
    engine._state.current_task = MagicMock(cancel_event=asyncio.Event(), cancel_reason="")
    engine._state.get_task_for_session = MagicMock(return_value=None)
    engine._supervisor = MagicMock()
    return engine


async def _fake_brain_stream(events: list):
    for e in events:
        yield e


@pytest.mark.asyncio
class TestReasonWithHeartbeatStream:

    async def test_reasoning_delta_forwarded_then_decision(self):
        """流式路径: reasoning_delta → thinking_delta_incremental, done → decision."""
        brain = MagicMock()
        brain._supports_stream_accumulation = MagicMock(return_value=True)
        brain.max_tokens = 4096
        msg = _make_anthropic_message(text="answer")
        brain.messages_create_async_stream = lambda **kw: _fake_brain_stream([
            {"type": "reasoning_delta", "text": "思考片段1"},
            {"type": "reasoning_delta", "text": "思考片段2"},
            {"type": "done", "response": msg, "chunk_count": 3},
        ])

        engine = _make_engine(brain)
        events = []
        async for ev in engine._reason_with_heartbeat(
            messages=[], system_prompt="", tools=[], current_model="glm-5",
        ):
            events.append(ev)

        # 应该有 2 个 thinking_delta_incremental 和 1 个 decision
        thinking_evs = [e for e in events if e["type"] == "thinking_delta_incremental"]
        decision_evs = [e for e in events if e["type"] == "decision"]
        assert len(thinking_evs) == 2
        assert thinking_evs[0]["content"] == "思考片段1"
        assert thinking_evs[1]["content"] == "思考片段2"
        assert len(decision_evs) == 1
        assert decision_evs[0]["decision"].type == DecisionType.FINAL_ANSWER

    async def test_idle_yields_heartbeat(self):
        """idle 超过 HEARTBEAT_INTERVAL → yield heartbeat (不抛异常)."""
        brain = MagicMock()
        brain._supports_stream_accumulation = MagicMock(return_value=True)
        brain.max_tokens = 4096

        async def _slow_stream():
            await asyncio.sleep(0.3)  # 比 HB_INTERVAL 长一点
            yield {"type": "done", "response": _make_anthropic_message(), "chunk_count": 1}

        brain.messages_create_async_stream = lambda **kw: _slow_stream()

        engine = _make_engine(brain)
        # 临时把 HEARTBEAT_INTERVAL 改小, 让我们能在测试时间内看到 heartbeat
        with patch.object(engine, "_HEARTBEAT_INTERVAL", 0.1):
            events = []
            async for ev in engine._reason_with_heartbeat(
                messages=[], system_prompt="", tools=[], current_model="glm-5",
            ):
                events.append(ev)

        heartbeats = [e for e in events if e["type"] == "heartbeat"]
        assert len(heartbeats) >= 1, f"expected heartbeat events, got: {events}"
        # 最终也应该有 decision
        assert any(e["type"] == "decision" for e in events)

    async def test_brain_idle_error_propagates_via_pending_result(self):
        """chunk-level hang 检测已下沉到 brain 层。
        当 brain 抛 LLMError(idle) 时, reasoning_engine 应原样向上传播,
        而不是自己做检测。
        """
        brain = MagicMock()
        brain._supports_stream_accumulation = MagicMock(return_value=True)
        brain.max_tokens = 4096

        async def _brain_idle_error_stream():
            yield {"type": "reasoning_delta", "text": "起步"}
            # brain 自己检测到 chunk idle → 抛 LLMError
            raise LLMError("Brain stream chunk idle > 120s; assumed hang (chunks_received=1)")

        brain.messages_create_async_stream = lambda **kw: _brain_idle_error_stream()

        engine = _make_engine(brain)
        # 不需要 patch _STREAM_IDLE_HARD_LIMIT 了 — 已下沉到 brain
        with pytest.raises(LLMError, match="chunk idle"):
            async for _ in engine._reason_with_heartbeat(
                messages=[], system_prompt="", tools=[], current_model="glm-5",
            ):
                pass

    async def test_heartbeat_does_not_raise_on_long_idle(self):
        """reasoning_engine 层不再做 hang 检测: 即使两个 brain 上层事件之间
        间隔很久, 也只发 heartbeat 不抛异常 (hang 由 brain 检测)。
        """
        brain = MagicMock()
        brain._supports_stream_accumulation = MagicMock(return_value=True)
        brain.max_tokens = 4096

        async def _slow_brain_stream():
            await asyncio.sleep(0.5)
            yield {"type": "done", "response": _make_anthropic_message(), "chunk_count": 1}

        brain.messages_create_async_stream = lambda **kw: _slow_brain_stream()

        engine = _make_engine(brain)
        with patch.object(engine, "_HEARTBEAT_INTERVAL", 0.1):
            events = []
            async for ev in engine._reason_with_heartbeat(
                messages=[], system_prompt="", tools=[], current_model="glm-5",
            ):
                events.append(ev)

        # 应该有多次 heartbeat (至少 2-3 次), 但最终成功拿到 decision
        heartbeats = [e for e in events if e["type"] == "heartbeat"]
        decisions = [e for e in events if e["type"] == "decision"]
        assert len(heartbeats) >= 2, f"expected multiple heartbeats, got {len(heartbeats)}"
        assert len(decisions) == 1

    async def test_cancel_event_raises_user_cancelled(self):
        """cancel_event.set() → UserCancelledError."""
        brain = MagicMock()
        brain._supports_stream_accumulation = MagicMock(return_value=True)
        brain.max_tokens = 4096

        cancel_event = asyncio.Event()

        async def _slow_stream():
            await asyncio.sleep(5)
            yield {}

        brain.messages_create_async_stream = lambda **kw: _slow_stream()

        engine = _make_engine(brain)
        engine._state.current_task = MagicMock(
            cancel_event=cancel_event, cancel_reason="test cancel"
        )

        async def _trigger_cancel():
            await asyncio.sleep(0.05)
            cancel_event.set()

        with patch.object(engine, "_HEARTBEAT_INTERVAL", 0.1):
            cancel_task = asyncio.create_task(_trigger_cancel())
            with pytest.raises(UserCancelledError):
                async for _ in engine._reason_with_heartbeat(
                    messages=[], system_prompt="", tools=[], current_model="glm-5",
                ):
                    pass
            await cancel_task

    async def test_falls_back_when_brain_lacks_stream_method(self):
        """brain 没有 messages_create_async_stream → 走 _reason_with_heartbeat_nonstream."""
        brain = MagicMock(spec=["max_tokens", "messages_create_async"])  # 故意不暴露 stream
        brain.max_tokens = 4096

        engine = _make_engine(brain)

        async def _fake_nonstream(*args, **kwargs):
            yield {"type": "decision", "decision": MagicMock(type=DecisionType.FINAL_ANSWER, tool_calls=[])}

        engine._reason_with_heartbeat_nonstream = _fake_nonstream

        events = []
        async for ev in engine._reason_with_heartbeat(
            messages=[], system_prompt="", tools=[], current_model="glm-5",
        ):
            events.append(ev)

        assert len(events) == 1
        assert events[0]["type"] == "decision"

    async def test_falls_back_when_provider_unsupported(self):
        """_supports_stream_accumulation=False → 走非流式兜底."""
        brain = MagicMock()
        brain._supports_stream_accumulation = MagicMock(return_value=False)
        brain.max_tokens = 4096
        # 即使有 stream 方法也不应被调用
        brain.messages_create_async_stream = MagicMock(side_effect=AssertionError("should not be called"))

        engine = _make_engine(brain)

        async def _fake_nonstream(*args, **kwargs):
            yield {"type": "decision", "decision": MagicMock(type=DecisionType.FINAL_ANSWER, tool_calls=[])}

        engine._reason_with_heartbeat_nonstream = _fake_nonstream

        events = []
        async for ev in engine._reason_with_heartbeat(
            messages=[], system_prompt="", tools=[], current_model="glm-5",
        ):
            events.append(ev)

        assert len(events) == 1
        assert events[0]["type"] == "decision"

    async def test_falls_back_when_env_flag_disabled(self, monkeypatch):
        """SEEAGENT_BRAIN_DISABLE_STREAM=1 → 走非流式兜底."""
        brain = MagicMock()
        brain._supports_stream_accumulation = MagicMock(return_value=True)
        brain.max_tokens = 4096
        brain.messages_create_async_stream = MagicMock(side_effect=AssertionError("should not be called"))

        engine = _make_engine(brain)

        async def _fake_nonstream(*args, **kwargs):
            yield {"type": "decision", "decision": MagicMock(type=DecisionType.FINAL_ANSWER, tool_calls=[])}

        engine._reason_with_heartbeat_nonstream = _fake_nonstream

        monkeypatch.setenv("SEEAGENT_BRAIN_DISABLE_STREAM", "1")
        events = []
        async for ev in engine._reason_with_heartbeat(
            messages=[], system_prompt="", tools=[], current_model="glm-5",
        ):
            events.append(ev)

        assert len(events) == 1
        assert events[0]["type"] == "decision"

"""CompressionStrategy tests -- LLM, Mechanical, Truncation implementations."""

import pytest
from unittest.mock import AsyncMock
from types import SimpleNamespace

from seeagent.bestpractice.engine.compression import (
    LLMCompression,
    MechanicalCompression,
    TruncationCompression,
    extract_text,
)
from seeagent.bestpractice.models import ArtifactKind, ContextArtifact


def _make_artifacts():
    return [
        ContextArtifact(
            kind=ArtifactKind.PROGRESS,
            key="s1",
            content='{"id": "s1", "name": "Research", "status": "done"}',
        ),
        ContextArtifact(
            kind=ArtifactKind.STRUCTURED_OUTPUT,
            key="s1",
            content='{"result": "findings"}',
        ),
        ContextArtifact(
            kind=ArtifactKind.USER_INTENT,
            key="intent",
            content='"AI adoption"',
        ),
    ]


def _make_brain(response_text="LLM compressed summary"):
    brain = SimpleNamespace()
    resp = SimpleNamespace(content=response_text)
    brain.think_lightweight = AsyncMock(return_value=resp)
    return brain


class TestLLMCompression:
    @pytest.mark.asyncio
    async def test_llmCompressBasicTest(self):
        brain = _make_brain("User wants B2B focus")
        strategy = LLMCompression(brain)
        artifacts = _make_artifacts()

        result = await strategy.compress(
            artifacts, 500,
            bp_name="Test BP",
            current_step="Analysis",
            current_index=2,
            total=3,
            completed_steps="- Research",
            messages=[
                {"role": "user", "content": "focus on B2B"},
                {"role": "assistant", "content": "Got it"},
            ],
        )

        assert "B2B" in result
        brain.think_lightweight.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_llmCompressTruncatesToBudgetTest(self):
        brain = _make_brain("x" * 200)
        strategy = LLMCompression(brain)

        result = await strategy.compress([], 50, messages=[])

        assert len(result) <= 50

    @pytest.mark.asyncio
    async def test_llmCompressUsesMessageContextTest(self):
        brain = _make_brain("summary")
        strategy = LLMCompression(brain)

        await strategy.compress(
            [], 500,
            messages=[{"role": "user", "content": "important input"}],
            bp_name="BP",
            current_step="Step1",
            current_index=1,
            total=3,
            completed_steps="(none)",
        )

        call_args = brain.think_lightweight.call_args
        prompt = call_args[0][0]
        assert "important input" in prompt
        assert "BP" in prompt


class TestMechanicalCompression:
    @pytest.mark.asyncio
    async def test_mechanicalBasicTest(self):
        strategy = MechanicalCompression()
        messages = [
            {"role": "user", "content": "important context about project"},
            {"role": "assistant", "content": "Here is my detailed analysis of the data"},
        ]

        result = await strategy.compress([], 1000, messages=messages)

        assert "important context" in result
        assert "detailed analysis" in result

    @pytest.mark.asyncio
    async def test_mechanicalFiltersToolMessagesTest(self):
        strategy = MechanicalCompression()
        messages = [
            {"role": "user", "content": "user input"},
            {"role": "tool", "content": "tool result should be excluded"},
            {"role": "assistant", "content": "Here is my longer response with analysis"},
        ]

        result = await strategy.compress([], 1000, messages=messages)

        assert "user input" in result
        assert "tool result" not in result

    @pytest.mark.asyncio
    async def test_mechanicalSkipsShortAssistantTest(self):
        strategy = MechanicalCompression()
        messages = [
            {"role": "assistant", "content": "ok"},
            {"role": "assistant", "content": "Here is a detailed analysis with findings"},
        ]

        result = await strategy.compress([], 1000, messages=messages)

        assert "[assistant] ok" not in result
        assert "detailed analysis" in result

    @pytest.mark.asyncio
    async def test_mechanicalRespectsbudgetTest(self):
        strategy = MechanicalCompression()
        messages = [
            {"role": "user", "content": "x" * 500},
        ]

        result = await strategy.compress([], 100, messages=messages)

        assert len(result) <= 100


class TestTruncationCompression:
    @pytest.mark.asyncio
    async def test_truncationByPriorityTest(self):
        strategy = TruncationCompression()
        artifacts = [
            ContextArtifact(
                kind=ArtifactKind.PROGRESS,
                key="s1",
                content="progress data",
                priority=10,
            ),
            ContextArtifact(
                kind=ArtifactKind.RAW_TEXT,
                key="raw",
                content="raw text data",
                priority=3,
            ),
        ]

        result = await strategy.compress(artifacts, 1000)

        assert "progress data" in result
        assert "raw text data" in result

    @pytest.mark.asyncio
    async def test_truncationRespectsbudgetTest(self):
        strategy = TruncationCompression()
        artifacts = [
            ContextArtifact(
                kind=ArtifactKind.PROGRESS,
                key="s1",
                content="a" * 500,
                priority=10,
            ),
            ContextArtifact(
                kind=ArtifactKind.RAW_TEXT,
                key="raw",
                content="b" * 500,
                priority=3,
            ),
        ]

        result = await strategy.compress(artifacts, 50)

        assert len(result) <= 50


class TestExtractText:
    def test_extractStringTest(self):
        assert extract_text("hello world") == "hello world"

    def test_extractListContentBlocksTest(self):
        content = [
            {"type": "text", "text": "hello"},
            {"type": "image", "source": {}},
            {"type": "text", "text": "world"},
        ]
        result = extract_text(content)
        assert "hello" in result
        assert "world" in result

    def test_extractToolResultTest(self):
        content = [{"type": "tool_result", "content": "success"}]
        result = extract_text(content)
        assert "tool_result" in result

    def test_extractNoneReturnsEmptyTest(self):
        assert extract_text(None) == ""
        assert extract_text(123) == ""

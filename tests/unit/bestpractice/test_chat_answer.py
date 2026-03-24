# tests/unit/bestpractice/test_chat_answer.py
"""Tests for _stream_bp_answer_from_chat and _llm_extract_answer_fields."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from seeagent.api.routes.seecrab import _llm_extract_answer_fields


class TestLLMExtractAnswerFields:
    @pytest.mark.asyncio
    async def test_single_field_extraction(self):
        """When brain returns valid JSON, extract matching fields."""
        mock_brain = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = '{"domain": "科技"}'
        mock_brain.think_lightweight = AsyncMock(return_value=mock_resp)

        result = await _llm_extract_answer_fields(
            user_message="科技",
            missing_fields=["domain"],
            input_schema={
                "type": "object",
                "properties": {"domain": {"type": "string", "description": "领域"}},
            },
            brain=mock_brain,
        )
        assert result == {"domain": "科技"}

    @pytest.mark.asyncio
    async def test_filters_non_missing_fields(self):
        """Only return fields that are in missing_fields list."""
        mock_brain = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = '{"domain": "科技", "extra": "ignore"}'
        mock_brain.think_lightweight = AsyncMock(return_value=mock_resp)

        result = await _llm_extract_answer_fields(
            user_message="科技",
            missing_fields=["domain"],
            input_schema={"type": "object", "properties": {"domain": {"type": "string"}}},
            brain=mock_brain,
        )
        assert "extra" not in result
        assert "domain" in result

    @pytest.mark.asyncio
    async def test_returns_empty_on_no_brain(self):
        result = await _llm_extract_answer_fields(
            user_message="test",
            missing_fields=["field1"],
            input_schema={"type": "object", "properties": {}},
            brain=None,
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        mock_brain = AsyncMock()
        mock_brain.think_lightweight = AsyncMock(side_effect=Exception("LLM error"))

        result = await _llm_extract_answer_fields(
            user_message="test",
            missing_fields=["field1"],
            input_schema={"type": "object", "properties": {}},
            brain=mock_brain,
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_on_no_missing_fields(self):
        result = await _llm_extract_answer_fields(
            user_message="test",
            missing_fields=[],
            input_schema={"type": "object", "properties": {}},
            brain=AsyncMock(),
        )
        assert result == {}

"""Tests for LLM BP matching."""
import pytest
from unittest.mock import AsyncMock, MagicMock

import seeagent.bestpractice.facade as facade
from seeagent.bestpractice.facade import llm_match_bp_from_message
from seeagent.bestpractice.models import (
    BestPracticeConfig,
    SubtaskConfig,
    TriggerConfig,
    TriggerType,
)
from seeagent.bestpractice.engine import BPStateManager


@pytest.fixture(autouse=True)
def reset_facade():
    facade._initialized = False
    facade._bp_engine = None
    facade._bp_handler = None
    facade._bp_state_manager = None
    facade._bp_config_loader = None
    facade._bp_context_bridge = None
    facade._bp_prompt_loader = None
    facade._bp_matcher = None
    facade._bp_prompt_builder = None
    yield
    facade._initialized = False
    facade._bp_matcher = None
    facade._bp_prompt_builder = None


@pytest.fixture
def setup_llm_match():
    """Wire up facade for LLM matching tests."""
    config = BestPracticeConfig(
        id="content-pipeline",
        name="内容创作流水线",
        description="从选题调研到内容发布",
        subtasks=[
            SubtaskConfig(
                id="topic-research", name="选题调研", agent_profile="default",
                input_schema={
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string", "description": "内容领域"},
                        "platform": {"type": "string", "description": "发布平台"},
                    },
                    "required": ["domain"],
                },
            ),
        ],
        triggers=[TriggerConfig(type=TriggerType.CONTEXT, conditions=["写文章"])],
    )

    mock_loader = MagicMock()
    mock_loader.configs = {"content-pipeline": config}

    mock_prompt_loader = MagicMock()
    mock_prompt_loader.render = MagicMock(return_value="rendered prompt")

    state_mgr = BPStateManager()

    from seeagent.bestpractice.prompt import BPMatcher

    facade._initialized = True
    facade._bp_config_loader = mock_loader
    facade._bp_state_manager = state_mgr
    facade._bp_prompt_loader = mock_prompt_loader
    facade._bp_matcher = BPMatcher(
        config_loader=mock_loader,
        state_manager=state_mgr,
        prompt_loader=mock_prompt_loader,
    )
    return state_mgr, config


class TestLLMMatchBPFromMessage:
    @pytest.mark.asyncio
    async def test_match_returns_bp_info(self, setup_llm_match):
        sm, config = setup_llm_match
        mock_brain = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = (
            '{"matched": true, "bp_id": "content-pipeline", '
            '"confidence": 0.9, "extracted_input": {"domain": "科技"}}'
        )
        mock_brain.think_lightweight = AsyncMock(return_value=mock_resp)

        result = await llm_match_bp_from_message("帮我写一篇科技文章", "s1", mock_brain)
        assert result is not None
        assert result["bp_id"] == "content-pipeline"
        assert result["extracted_input"]["domain"] == "科技"
        assert result["user_query"] == "帮我写一篇科技文章"

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self, setup_llm_match):
        mock_brain = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = '{"matched": false}'
        mock_brain.think_lightweight = AsyncMock(return_value=mock_resp)

        result = await llm_match_bp_from_message("今天天气怎么样", "s1", mock_brain)
        assert result is None

    @pytest.mark.asyncio
    async def test_low_confidence_returns_none(self, setup_llm_match):
        mock_brain = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = (
            '{"matched": true, "bp_id": "content-pipeline", '
            '"confidence": 0.5, "extracted_input": {}}'
        )
        mock_brain.think_lightweight = AsyncMock(return_value=mock_resp)

        result = await llm_match_bp_from_message("也许写点什么", "s1", mock_brain)
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_bp_id_returns_none(self, setup_llm_match):
        mock_brain = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = (
            '{"matched": true, "bp_id": "nonexistent", '
            '"confidence": 0.9, "extracted_input": {}}'
        )
        mock_brain.think_lightweight = AsyncMock(return_value=mock_resp)

        result = await llm_match_bp_from_message("写文章", "s1", mock_brain)
        assert result is None

    @pytest.mark.asyncio
    async def test_already_offered_bp_skipped(self, setup_llm_match):
        sm, _ = setup_llm_match
        sm.mark_bp_offered("s1", "content-pipeline")

        mock_brain = AsyncMock()
        result = await llm_match_bp_from_message("写一篇文章", "s1", mock_brain)
        assert result is None

    @pytest.mark.asyncio
    async def test_active_instance_skipped(self, setup_llm_match):
        sm, config = setup_llm_match
        sm.create_instance(config, "s1")

        mock_brain = AsyncMock()
        result = await llm_match_bp_from_message("写文章", "s1", mock_brain)
        assert result is None

    @pytest.mark.asyncio
    async def test_cooldown_skipped(self, setup_llm_match):
        sm, _ = setup_llm_match
        sm.set_cooldown("s1", 3)

        mock_brain = AsyncMock()
        result = await llm_match_bp_from_message("写文章", "s1", mock_brain)
        assert result is None

    @pytest.mark.asyncio
    async def test_suspended_instance_still_matched_in_llm_matchTest(self, setup_llm_match):
        """LLM match should still match bp_id even when a SUSPENDED instance exists.

        Suspended BPs are allowed to re-match so the offer card is shown for new starts.
        Resume intent is handled upstream by the route layer, not by the matcher.
        """
        sm, config = setup_llm_match
        inst_id = sm.create_instance(config, "s1")
        sm.suspend(inst_id)

        mock_brain = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = (
            '{"matched": true, "bp_id": "content-pipeline", '
            '"confidence": 0.9, "extracted_input": {"domain": "科技"}}'
        )
        mock_brain.think_lightweight = AsyncMock(return_value=mock_resp)

        result = await llm_match_bp_from_message("写文章", "s1", mock_brain)
        assert result is not None
        assert result["bp_id"] == "content-pipeline"

    @pytest.mark.asyncio
    async def test_no_brain_returns_none(self, setup_llm_match):
        result = await llm_match_bp_from_message("写文章", "s1", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_exception_returns_none(self, setup_llm_match):
        mock_brain = AsyncMock()
        mock_brain.think_lightweight = AsyncMock(side_effect=Exception("timeout"))

        result = await llm_match_bp_from_message("写文章", "s1", mock_brain)
        assert result is None

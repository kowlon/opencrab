"""Tests for engine bug fixes."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from seeagent.bestpractice.engine import BPEngine
from seeagent.bestpractice.engine import BPStateManager


class TestConformOutputAttribute:
    """Verify _conform_output uses resp.content (not resp.text)."""

    @pytest.mark.asyncio
    async def test_conform_output_uses_content_attribute(self):
        sm = BPStateManager()
        engine = BPEngine(state_manager=sm)

        mock_brain = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = '{"title": "mapped title"}'
        del mock_resp.text
        mock_brain.think_lightweight = AsyncMock(return_value=mock_resp)
        engine._get_brain = MagicMock(return_value=mock_brain)

        raw_output = {"raw_field": "some value"}
        output_schema = {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        }

        result = await engine._conform_output(raw_output, output_schema, raw_result_text="some raw text")
        assert "title" in result
        assert result["title"] == "mapped title"


class TestDeleteEndpointPersistence:
    """Verify bp_cancel DELETE endpoint persists cancelled state to session."""

    @pytest.mark.asyncio
    async def test_bp_cancel_persists_to_session(self):
        from seeagent.api.routes.bestpractice import bp_cancel

        # Build a fake snap with session_id
        mock_snap = MagicMock()
        mock_snap.session_id = "test-session-123"

        mock_sm = MagicMock()
        mock_sm.persist_instance = AsyncMock()
        mock_sm.persist_status_change = AsyncMock()
        mock_sm.ensure_loaded = AsyncMock(return_value=mock_snap)
        mock_sm.get.return_value = mock_snap
        mock_sm.serialize_for_session.return_value = {"cancelled": True}

        mock_session = MagicMock()
        mock_session.metadata = {}

        mock_session_mgr = MagicMock()

        mock_request = MagicMock()

        with (
            patch(
                "seeagent.api.routes.bestpractice.get_bp_state_manager",
                return_value=mock_sm,
            ),
            patch(
                "seeagent.api.routes.bestpractice._resolve_session",
                return_value=mock_session,
            ),
            patch(
                "seeagent.api.routes.bestpractice._resolve_session_manager",
                return_value=mock_session_mgr,
            ),
        ):
            response = await bp_cancel("inst-1", mock_request)

        # Verify cancel was called
        mock_sm.cancel.assert_called_once_with("inst-1")
        # Verify state was persisted to session metadata
        assert mock_session.metadata["bp_state"] == {"cancelled": True}
        mock_sm.serialize_for_session.assert_called_once_with("test-session-123")
        # Verify dirty flag was set
        mock_session_mgr.mark_dirty.assert_called_once()

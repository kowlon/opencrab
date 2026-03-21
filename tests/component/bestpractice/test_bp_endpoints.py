# tests/component/bestpractice/test_bp_endpoints.py
"""Component tests for BP SSE endpoints."""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from seeagent.api.routes.bestpractice import router


def _create_test_app():
    app = FastAPI()
    app.include_router(router)
    app.state.session_manager = MagicMock()
    app.state.session_manager.get_session.return_value = MagicMock(
        id="sess-1", metadata={}, context=MagicMock(_sse_event_bus=None)
    )
    return app


class TestBpNextEndpoint:
    def test_returns_409_when_busy(self):
        from seeagent.api.routes.bestpractice import _bp_busy_locks, _bp_clear_busy
        import time
        _bp_busy_locks.clear()
        _bp_busy_locks["sess-1"] = ("other", time.time())

        app = _create_test_app()
        client = TestClient(app)
        resp = client.post("/api/bp/next", json={
            "instance_id": "bp-123", "session_id": "sess-1",
        })
        assert resp.status_code == 409
        _bp_busy_locks.clear()


class TestBpOutputEndpoint:
    def test_returns_output(self):
        app = _create_test_app()
        mock_sm = MagicMock()
        mock_snap = MagicMock()
        mock_snap.subtask_outputs = {"s1": {"data": "hello"}}
        mock_sm.get.return_value = mock_snap

        with patch("seeagent.api.routes.bestpractice.get_bp_state_manager", return_value=mock_sm):
            client = TestClient(app)
            resp = client.get("/api/bp/output/bp-123/s1")
            assert resp.status_code == 200
            assert resp.json()["output"] == {"data": "hello"}

    def test_returns_404_not_found(self):
        app = _create_test_app()
        mock_sm = MagicMock()
        mock_sm.get.return_value = None

        with patch("seeagent.api.routes.bestpractice.get_bp_state_manager", return_value=mock_sm):
            client = TestClient(app)
            resp = client.get("/api/bp/output/bp-missing/s1")
            assert resp.status_code == 404


class TestBpDeleteEndpoint:
    def test_cancel_instance(self):
        app = _create_test_app()
        mock_sm = MagicMock()
        mock_snap = MagicMock()
        mock_sm.get.return_value = mock_snap

        with patch("seeagent.api.routes.bestpractice.get_bp_state_manager", return_value=mock_sm):
            client = TestClient(app)
            resp = client.delete("/api/bp/bp-123")
            assert resp.status_code == 200
            mock_sm.cancel.assert_called_once_with("bp-123")

    def test_cancel_not_found(self):
        app = _create_test_app()
        mock_sm = MagicMock()
        mock_sm.get.return_value = None

        with patch("seeagent.api.routes.bestpractice.get_bp_state_manager", return_value=mock_sm):
            client = TestClient(app)
            resp = client.delete("/api/bp/bp-missing")
            assert resp.status_code == 404

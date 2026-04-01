# tests/component/bestpractice/test_bp_endpoints.py
"""Component tests for BP endpoints (v1.1 route structure)."""
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


# ── /api/bp/next (unchanged path) ───────────────────────────


class TestBpNextEndpoint:
    def test_returns_409_when_busy(self):
        from seeagent.api.routes.bestpractice import _bp_busy_locks
        import time
        _bp_busy_locks.clear()
        _bp_busy_locks["sess-1"] = ("other", time.time(), "lock-x")

        app = _create_test_app()
        client = TestClient(app)
        resp = client.post("/api/bp/next", json={
            "instance_id": "bp-123", "session_id": "sess-1",
        })
        assert resp.status_code == 409
        _bp_busy_locks.clear()


# ── /api/bp/instances/{id}/output/{sid} (v1.1 path) ────────


class TestBpOutputEndpoint:
    def test_returns_output(self):
        app = _create_test_app()
        mock_sm = MagicMock()
        mock_snap = MagicMock()
        mock_snap.subtask_outputs = {"s1": {"data": "hello"}}
        mock_sm.ensure_loaded = AsyncMock(return_value=mock_snap)

        with patch("seeagent.api.routes.bestpractice.get_bp_state_manager", return_value=mock_sm):
            client = TestClient(app)
            resp = client.get("/api/bp/instances/bp-123/output/s1")
            assert resp.status_code == 200
            assert resp.json()["output"] == {"data": "hello"}

    def test_returns_404_not_found(self):
        app = _create_test_app()
        mock_sm = MagicMock()
        mock_sm.ensure_loaded = AsyncMock(return_value=None)

        with patch("seeagent.api.routes.bestpractice.get_bp_state_manager", return_value=mock_sm):
            client = TestClient(app)
            resp = client.get("/api/bp/instances/bp-missing/output/s1")
            assert resp.status_code == 404

    def test_returns_404_no_output(self):
        app = _create_test_app()
        mock_sm = MagicMock()
        mock_snap = MagicMock()
        mock_snap.subtask_outputs = {}
        mock_sm.ensure_loaded = AsyncMock(return_value=mock_snap)

        with patch("seeagent.api.routes.bestpractice.get_bp_state_manager", return_value=mock_sm):
            client = TestClient(app)
            resp = client.get("/api/bp/instances/bp-123/output/missing")
            assert resp.status_code == 404


# ── DELETE /api/bp/instances/{id} (v1.1 path) ──────────────


class TestBpDeleteEndpoint:
    def test_cancel_instance(self):
        app = _create_test_app()
        mock_sm = MagicMock()
        mock_snap = MagicMock()
        mock_snap.session_id = "sess-1"
        mock_sm.ensure_loaded = AsyncMock(return_value=mock_snap)
        mock_sm.persist_status_change = AsyncMock()
        mock_sm.serialize_for_session.return_value = {}

        with patch("seeagent.api.routes.bestpractice.get_bp_state_manager", return_value=mock_sm):
            client = TestClient(app)
            resp = client.delete("/api/bp/instances/bp-123")
            assert resp.status_code == 200
            mock_sm.cancel.assert_called_once_with("bp-123")

    def test_cancel_not_found(self):
        app = _create_test_app()
        mock_sm = MagicMock()
        mock_sm.ensure_loaded = AsyncMock(return_value=None)

        with patch("seeagent.api.routes.bestpractice.get_bp_state_manager", return_value=mock_sm):
            client = TestClient(app)
            resp = client.delete("/api/bp/instances/bp-missing")
            assert resp.status_code == 404


# ── GET /api/bp/configs (v1.1 new) ──────────────────────────


class TestBpConfigsEndpoint:
    def test_returns_config_list(self):
        app = _create_test_app()
        mock_loader = MagicMock()
        mock_cfg = MagicMock()
        mock_cfg.id = "test-bp"
        mock_cfg.name = "测试流程"
        mock_cfg.description = "测试描述"
        mock_cfg.subtasks = [MagicMock(), MagicMock()]
        mock_cfg.default_run_mode = MagicMock(value="manual")
        mock_cfg.triggers = [MagicMock(type=MagicMock(value="command"))]
        mock_loader.configs = {"test-bp": mock_cfg}

        with patch("seeagent.api.routes.bestpractice.get_bp_config_loader", return_value=mock_loader):
            client = TestClient(app)
            resp = client.get("/api/bp/configs")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 1
            assert data["configs"][0]["id"] == "test-bp"
            assert data["configs"][0]["subtask_count"] == 2

    def test_returns_empty_when_loader_none(self):
        app = _create_test_app()
        with patch("seeagent.api.routes.bestpractice.get_bp_config_loader", return_value=None):
            client = TestClient(app)
            resp = client.get("/api/bp/configs")
            assert resp.status_code == 200
            assert resp.json() == {"total": 0, "configs": []}


class TestBpConfigDetailEndpoint:
    def test_returns_404_not_found(self):
        app = _create_test_app()
        mock_loader = MagicMock()
        mock_loader.configs = {}

        with patch("seeagent.api.routes.bestpractice.get_bp_config_loader", return_value=mock_loader):
            client = TestClient(app)
            resp = client.get("/api/bp/configs/nonexistent")
            assert resp.status_code == 404


# ── GET /api/bp/instances (v1.1 merged list) ────────────────


def _make_mock_snap(
    instance_id="bp-001", bp_id="test-bp", session_id="sess-1",
    status_val="active", run_mode_val="manual", subtask_statuses=None,
):
    """Create a mock BPInstanceSnapshot for testing."""
    from seeagent.bestpractice.models import BPInstanceSnapshot
    snap = MagicMock(spec=BPInstanceSnapshot)
    snap.instance_id = instance_id
    snap.bp_id = bp_id
    snap.session_id = session_id
    snap.status = MagicMock(value=status_val)
    snap.run_mode = MagicMock(value=run_mode_val)
    snap.current_subtask_index = 0
    snap.created_at = 1711800000.0
    snap.completed_at = None
    snap.suspended_at = None
    snap.bp_config = MagicMock()
    snap.bp_config.name = "测试流程"
    sub1, sub2 = MagicMock(), MagicMock()
    sub1.name = "步骤1"
    sub2.name = "步骤2"
    snap.bp_config.subtasks = [sub1, sub2]
    if subtask_statuses is None:
        subtask_statuses = {"s1": MagicMock(value="done"), "s2": MagicMock(value="pending")}
    snap.subtask_statuses = subtask_statuses
    snap.subtask_outputs = {}
    return snap


class TestBpInstancesListEndpoint:
    """Tests for GET /api/bp/instances (merged /status + /list)."""

    def test_session_id_memory_path(self):
        """With session_id: uses memory-first path, returns enriched fields."""
        app = _create_test_app()
        mock_sm = MagicMock()
        snap = _make_mock_snap()
        mock_sm.get_all_for_session.return_value = [snap]
        mock_sm.get_active.return_value = snap
        mock_sm.restore_from_db = AsyncMock(return_value=0)

        with patch("seeagent.api.routes.bestpractice.get_bp_state_manager", return_value=mock_sm), \
             patch("seeagent.api.routes.bestpractice.get_bp_config_loader", return_value=MagicMock(configs={})):
            client = TestClient(app)
            resp = client.get("/api/bp/instances?session_id=sess-1")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 1
            assert data["active_id"] == "bp-001"
            inst = data["instances"][0]
            assert inst["instance_id"] == "bp-001"
            assert inst["bp_name"] == "测试流程"
            assert inst["progress"] == "1/2"
            assert inst["subtask_count"] == 2
            assert inst["done_count"] == 1
            assert "session_title" in inst
            assert "subtask_names" in inst

    def test_no_session_id_sqlite_path(self):
        """Without session_id: uses SQLite path."""
        app = _create_test_app()
        mock_sm = MagicMock()
        mock_storage = MagicMock()
        mock_storage.count_instances = AsyncMock(return_value=1)
        mock_storage.load_all_instances = AsyncMock(return_value=[{
            "instance_id": "bp-db-001",
            "bp_id": "test-bp",
            "session_id": "sess-2",
            "status": "completed",
            "run_mode": "auto",
            "current_subtask_index": 2,
            "subtask_statuses": {"s1": "done", "s2": "done"},
            "created_at": 1711700000.0,
            "completed_at": 1711710000.0,
            "suspended_at": None,
        }])
        mock_sm._storage = mock_storage

        with patch("seeagent.api.routes.bestpractice.get_bp_state_manager", return_value=mock_sm), \
             patch("seeagent.api.routes.bestpractice.get_bp_config_loader", return_value=MagicMock(configs={})):
            client = TestClient(app)
            resp = client.get("/api/bp/instances")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 1
            assert data["active_id"] is None
            inst = data["instances"][0]
            assert inst["instance_id"] == "bp-db-001"
            assert inst["progress"] == "2/2"
            assert inst["done_count"] == 2

    def test_status_filter_memory_path(self):
        """session_id + status filter works."""
        app = _create_test_app()
        mock_sm = MagicMock()
        active_snap = _make_mock_snap(instance_id="bp-a", status_val="active")
        completed_snap = _make_mock_snap(instance_id="bp-c", status_val="completed")
        mock_sm.get_all_for_session.return_value = [active_snap, completed_snap]
        mock_sm.get_active.return_value = active_snap
        mock_sm.restore_from_db = AsyncMock(return_value=0)

        with patch("seeagent.api.routes.bestpractice.get_bp_state_manager", return_value=mock_sm), \
             patch("seeagent.api.routes.bestpractice.get_bp_config_loader", return_value=MagicMock(configs={})):
            client = TestClient(app)
            resp = client.get("/api/bp/instances?session_id=sess-1&status=completed")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 1
            assert data["instances"][0]["instance_id"] == "bp-c"

    def test_pagination(self):
        """limit/offset pagination works on memory path."""
        app = _create_test_app()
        mock_sm = MagicMock()
        snaps = [_make_mock_snap(instance_id=f"bp-{i}") for i in range(5)]
        mock_sm.get_all_for_session.return_value = snaps
        mock_sm.get_active.return_value = None
        mock_sm.restore_from_db = AsyncMock(return_value=0)

        with patch("seeagent.api.routes.bestpractice.get_bp_state_manager", return_value=mock_sm), \
             patch("seeagent.api.routes.bestpractice.get_bp_config_loader", return_value=MagicMock(configs={})):
            client = TestClient(app)
            resp = client.get("/api/bp/instances?session_id=sess-1&limit=2&offset=1")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 5
            assert len(data["instances"]) == 2
            assert data["instances"][0]["instance_id"] == "bp-1"

    def test_empty_when_sm_none(self):
        """Returns empty result when state manager is None."""
        app = _create_test_app()
        with patch("seeagent.api.routes.bestpractice.get_bp_state_manager", return_value=None), \
             patch("seeagent.api.routes.bestpractice.get_bp_config_loader", return_value=None):
            client = TestClient(app)
            resp = client.get("/api/bp/instances?session_id=sess-1")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 0
            assert data["instances"] == []
            assert data["active_id"] is None


# ── GET /api/bp/instances/stats (v1.1 new) ──────────────────


class TestBpInstanceStatsEndpoint:
    def test_returns_stats(self):
        app = _create_test_app()
        mock_sm = MagicMock()
        mock_storage = MagicMock()
        mock_storage.count_by_status = AsyncMock(
            return_value={"active": 2, "suspended": 1, "completed": 10, "cancelled": 0}
        )
        mock_sm._storage = mock_storage

        with patch("seeagent.api.routes.bestpractice.get_bp_state_manager", return_value=mock_sm):
            client = TestClient(app)
            resp = client.get("/api/bp/instances/stats")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 13
            assert data["by_status"]["active"] == 2

    def test_returns_500_when_storage_not_initialized(self):
        app = _create_test_app()
        mock_sm = MagicMock()
        mock_sm._storage = None

        with patch("seeagent.api.routes.bestpractice.get_bp_state_manager", return_value=mock_sm):
            client = TestClient(app)
            resp = client.get("/api/bp/instances/stats")
            assert resp.status_code == 500


# ── Route priority regression ───────────────────────────────


class TestRoutePriority:
    """Ensure /instances/stats is not captured by /instances/{instance_id}."""

    def test_stats_not_captured_as_instance_id(self):
        app = _create_test_app()
        mock_sm = MagicMock()
        mock_storage = MagicMock()
        mock_storage.count_by_status = AsyncMock(
            return_value={"active": 0, "suspended": 0, "completed": 0, "cancelled": 0}
        )
        mock_sm._storage = mock_storage

        with patch("seeagent.api.routes.bestpractice.get_bp_state_manager", return_value=mock_sm):
            client = TestClient(app)
            resp = client.get("/api/bp/instances/stats")
            # Should hit stats endpoint (200), not instance detail (which would 404)
            assert resp.status_code == 200
            assert "by_status" in resp.json()

    def test_configs_not_captured_as_bp_id(self):
        app = _create_test_app()
        mock_loader = MagicMock()
        mock_loader.configs = {}

        with patch("seeagent.api.routes.bestpractice.get_bp_config_loader", return_value=mock_loader):
            client = TestClient(app)
            resp = client.get("/api/bp/configs")
            # Should hit configs list (200), not config detail
            assert resp.status_code == 200
            assert resp.json() == {"total": 0, "configs": []}

import importlib.util
import time
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_DIR = Path(__file__).resolve().parents[1]


def build_client(monkeypatch):
    monkeypatch.chdir(PROJECT_DIR)
    module_path = PROJECT_DIR / "main.py"
    spec = importlib.util.spec_from_file_location("camera_search_main", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load main.py")
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)

    main.PREPROCESS_TOTAL_SECONDS = 2

    async def fast_sleep(_seconds: float):
        return

    monkeypatch.setattr(main.asyncio, "sleep", fast_sleep)
    client = TestClient(main.app)
    return client


def test_camera_search(monkeypatch):
    with build_client(monkeypatch) as client:
        resp = client.post("/api/v1/cameras/search", json={"query": "园区东门"})
        assert resp.status_code == 200
        data = resp.json()
        assert "cameras" in data
        assert len(data["cameras"]) >= 1
        assert {"id", "location", "image_url"}.issubset(data["cameras"][0].keys())
        assert data["cameras"][0]["image_url"].endswith(".jpg")


def test_search_with_query_compatibility(monkeypatch):
    with build_client(monkeypatch) as client:
        resp = client.post("/api/v1/search", json={"query": "东门"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "text"
        assert "items" in data
        assert len(data["items"]) >= 1
        assert {"camera_id", "location", "image_url", "score"}.issubset(data["items"][0].keys())


def test_preprocess_task_lifecycle(monkeypatch):
    with build_client(monkeypatch) as client:
        create_resp = client.post(
            "/api/v1/cameras/preprocess",
            json={"camera_ids": ["cam_001", "cam_999"]},
        )
        assert create_resp.status_code == 200
        task_id = create_resp.json()["task_id"]
        assert task_id

        final_data = None
        for _ in range(30):
            status_resp = client.get(f"/api/v1/cameras/preprocess/{task_id}")
            assert status_resp.status_code == 200
            data = status_resp.json()
            if data["status"] == "completed":
                final_data = data
                break
            time.sleep(0.01)

        assert final_data is not None
        assert final_data["progress"] == 100
        results = {item["camera_id"]: item for item in final_data["results"]}
        assert results["cam_001"]["status"] in {"success", "error"}
        assert results["cam_999"]["status"] == "not_found"


def test_preprocess_status_not_found(monkeypatch):
    with build_client(monkeypatch) as client:
        resp = client.get("/api/v1/cameras/preprocess/not-exists")
        assert resp.status_code == 404


def test_feature_search_modes(monkeypatch):
    with build_client(monkeypatch) as client:
        text_resp = client.post(
            "/api/v1/search",
            json={"mode": "text", "text": "园区东门"},
        )
        assert text_resp.status_code == 200
        assert len(text_resp.json()["items"]) >= 1

        image_resp = client.post(
            "/api/v1/search",
            json={"mode": "image", "image": "mock://image-1"},
        )
        assert image_resp.status_code == 200
        assert len(image_resp.json()["items"]) >= 1

        image_text_resp = client.post(
            "/api/v1/search",
            json={"mode": "image_text", "image": "mock://image-1", "text": "东门"},
        )
        assert image_text_resp.status_code == 200
        assert len(image_text_resp.json()["items"]) >= 1


def test_feature_search_validation(monkeypatch):
    with build_client(monkeypatch) as client:
        resp = client.post("/api/v1/search", json={"mode": "image"})
        assert resp.status_code == 422

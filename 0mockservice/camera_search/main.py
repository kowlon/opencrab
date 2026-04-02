import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Literal

import cv2
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, model_validator

CONFIG_DIR = "data/configs"
VIDEO_DIR = "data/videos"
IMAGE_DIR = "data/images"
CONFIG_FILE = os.path.join(CONFIG_DIR, "cameras.json")
PREPROCESS_TOTAL_SECONDS = 100
os.makedirs(IMAGE_DIR, exist_ok=True)

cameras_db: dict[str, dict[str, Any]] = {}
preprocess_tasks: dict[str, dict[str, Any]] = {}


def _video_file(camera_id: str, location: str) -> str:
    return os.path.join(VIDEO_DIR, f"{camera_id}_{location}.mp4")


def _image_file(camera_id: str) -> str:
    return os.path.join(IMAGE_DIR, f"{camera_id}.jpg")


def _build_http_image_url(camera_id: str) -> str:
    return f"http://127.0.0.1:8010/static/{camera_id}.jpg"


def _query_match(query: str, camera: dict[str, Any]) -> bool:
    q = query.strip().lower()
    location = str(camera["location"]).lower()
    camera_id = str(camera["id"]).lower()
    if not q:
        return True
    if q in location or q in camera_id:
        return True
    if any(token in q for token in ["全部", "所有", "相机"]):
        return True
    common = [c for c in q if c.strip() and c in location]
    return len(common) >= 2


def _prepare_local_images() -> None:
    os.makedirs(IMAGE_DIR, exist_ok=True)
    for camera in cameras_db.values():
        camera_id = camera["id"]
        location = camera["location"]
        video_path = _video_file(camera_id, location)
        image_path = _image_file(camera_id)
        if os.path.exists(image_path):
            continue
        if not os.path.exists(video_path):
            continue
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            continue
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            continue
        cv2.imwrite(image_path, frame)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.path.exists(CONFIG_FILE):
        raise RuntimeError(f"Camera config directory or file not found: {CONFIG_FILE}")
    with open(CONFIG_FILE, encoding="utf-8") as f:
        try:
            cameras = json.load(f)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Failed to parse camera config file: {CONFIG_FILE}") from exc
    if not cameras:
        raise RuntimeError("Camera config is empty")
    for camera in cameras:
        if "id" in camera and "location" in camera:
            cameras_db[camera["id"]] = camera
    _prepare_local_images()
    yield
    cameras_db.clear()
    preprocess_tasks.clear()


app = FastAPI(title="Camera Search Mock Service", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=IMAGE_DIR), name="images")


class CameraSearchRequest(BaseModel):
    query: str | None = None
    location: str | None = None
    center: str | None = None
    radius: str | None = None
    time_range: str | None = None


class CameraItem(BaseModel):
    id: str
    location: str
    image_url: str


class CameraSearchResponse(BaseModel):
    cameras: list[CameraItem]


class SearchRequest(BaseModel):
    mode: Literal["text", "image", "image_text"] = "text"
    query: str | None = None
    text: str | None = None
    image: str | None = None

    @model_validator(mode="after")
    def validate_by_mode(self):
        if not self.text and self.query:
            self.text = self.query
        if self.mode == "text" and not self.text:
            raise ValueError("mode=text 时，text 或 query 必填")
        if self.mode == "image" and not self.image:
            raise ValueError("mode=image 时，image 必填")
        if self.mode == "image_text" and (not self.text or not self.image):
            raise ValueError("mode=image_text 时，text 与 image 均必填")
        return self


class PreprocessRequest(BaseModel):
    camera_ids: list[str]


class PreprocessTaskCreateResponse(BaseModel):
    task_id: str
    status: str
    progress: int
    camera_ids: list[str]


class PreprocessItemResult(BaseModel):
    camera_id: str
    status: str
    message: str | None = None


class PreprocessStatusResponse(BaseModel):
    task_id: str
    status: str
    progress: int
    camera_ids: list[str]
    results: list[PreprocessItemResult] | None = None
    created_at: float
    updated_at: float


class FeatureSearchItem(BaseModel):
    camera_id: str
    location: str
    image_url: str
    score: float | None = None


class SearchResponse(BaseModel):
    mode: Literal["text", "image", "image_text"]
    items: list[FeatureSearchItem]


def _all_image_items() -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for camera in cameras_db.values():
        image_path = _image_file(camera["id"])
        if not os.path.exists(image_path):
            continue
        items.append(
            {
                "camera_id": camera["id"],
                "location": camera["location"],
                "image_url": _build_http_image_url(camera["id"]),
            }
        )
    return items


async def _run_preprocess(task_id: str) -> None:
    task = preprocess_tasks[task_id]
    task["status"] = "running"
    task["updated_at"] = time.time()
    for second in range(PREPROCESS_TOTAL_SECONDS):
        await asyncio.sleep(1)
        progress = int(((second + 1) / PREPROCESS_TOTAL_SECONDS) * 100)
        task["progress"] = progress
        task["updated_at"] = time.time()
    results: list[dict[str, Any]] = []
    for camera_id in task["camera_ids"]:
        camera = cameras_db.get(camera_id)
        if camera is None:
            results.append(
                {"camera_id": camera_id, "status": "not_found", "message": "camera id not found"}
            )
            continue
        video_path = _video_file(camera["id"], camera["location"])
        if not os.path.exists(video_path):
            results.append(
                {
                    "camera_id": camera_id,
                    "status": "error",
                    "message": "video file missing or naming mismatch",
                }
            )
            continue
        results.append(
            {
                "camera_id": camera_id,
                "status": "success",
                "message": "preprocess finished and sent to remote service (mock)",
            }
        )
    task["results"] = results
    task["status"] = "completed"
    task["progress"] = 100
    task["updated_at"] = time.time()


@app.post("/api/v1/cameras/search", response_model=CameraSearchResponse)
async def search_cameras(req: CameraSearchRequest):
    matched: list[CameraItem] = []
    
    # 决定要搜索的词
    search_q = req.query or req.location or req.center or ""
    
    for camera in cameras_db.values():
        if _query_match(search_q, camera):
            matched.append(
                CameraItem(
                    id=camera["id"],
                    location=camera["location"],
                    image_url=_build_http_image_url(camera["id"]),
                )
            )
    unique: dict[str, CameraItem] = {camera.id: camera for camera in matched}
    return CameraSearchResponse(cameras=list(unique.values()))


@app.post("/api/v1/search", response_model=SearchResponse)
async def search_by_feature(req: SearchRequest):
    source = _all_image_items()
    if not source:
        return SearchResponse(mode=req.mode, items=[])
    items: list[FeatureSearchItem] = []
    for index, item in enumerate(source):
        camera = cameras_db[item["camera_id"]]
        score = 0.1
        if req.mode in ("text", "image_text") and req.text and _query_match(req.text, camera):
            score += 0.7
        if req.mode in ("image", "image_text") and req.image:
            score += 0.2 + ((index % 3) * 0.05)
        if req.mode == "text" and req.text and req.text.lower() in camera["id"].lower():
            score += 0.1
        score = min(score, 0.99)
        if req.mode == "text" and score < 0.5:
            continue
        if req.mode == "image_text" and score < 0.6:
            continue
        items.append(
            FeatureSearchItem(
                camera_id=item["camera_id"],
                location=item["location"],
                image_url=item["image_url"],
                score=round(score, 3),
            )
        )
    items.sort(key=lambda x: x.score or 0.0, reverse=True)
    if not items:
        items = [
            FeatureSearchItem(
                camera_id=item["camera_id"],
                location=item["location"],
                image_url=item["image_url"],
                score=0.5,
            )
            for item in source[:3]
        ]
    return SearchResponse(mode=req.mode, items=items[:10])


@app.post("/api/v1/cameras/preprocess", response_model=PreprocessTaskCreateResponse)
async def create_preprocess_task(req: PreprocessRequest):
    if not req.camera_ids:
        raise HTTPException(status_code=400, detail="camera_ids can not be empty")
    task_id = str(uuid.uuid4())
    now = time.time()
    preprocess_tasks[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "progress": 0,
        "camera_ids": req.camera_ids,
        "results": None,
        "created_at": now,
        "updated_at": now,
    }
    asyncio.create_task(_run_preprocess(task_id))
    task = preprocess_tasks[task_id]
    return PreprocessTaskCreateResponse(
        task_id=task["task_id"],
        status=task["status"],
        progress=task["progress"],
        camera_ids=task["camera_ids"],
    )


@app.get("/api/v1/cameras/preprocess/{task_id}", response_model=PreprocessStatusResponse)
async def get_preprocess_status(task_id: str):
    task = preprocess_tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task_id not found: {task_id}")
    parsed_results: list[PreprocessItemResult] | None = None
    if task["results"] is not None:
        parsed_results = [PreprocessItemResult(**x) for x in task["results"]]
    return PreprocessStatusResponse(
        task_id=task["task_id"],
        status=task["status"],
        progress=task["progress"],
        camera_ids=task["camera_ids"],
        results=parsed_results,
        created_at=task["created_at"],
        updated_at=task["updated_at"],
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8010, reload=True)

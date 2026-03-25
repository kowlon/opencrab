#!/usr/bin/env python3

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def _http_json(method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.getcode()
            body = resp.read().decode("utf-8")
            if not body:
                return status, {}
            parsed = json.loads(body)
            if not isinstance(parsed, dict):
                raise ValueError("response json must be an object")
            return status, parsed
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"raw": body}
        if not isinstance(parsed, dict):
            parsed = {"raw": body}
        return exc.code, parsed


def _exit_with_error(code: str, message: str, detail: dict[str, Any] | None = None) -> None:
    output: dict[str, Any] = {"ok": False, "error": {"code": code, "message": message}}
    if detail is not None:
        output["error"]["detail"] = detail
    print(json.dumps(output, ensure_ascii=False, indent=2))
    sys.exit(1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="预处理 + 特征检索串联调用脚本")
    parser.add_argument(
        "--camera-ids",
        required=True,
        help="逗号分隔的相机ID列表，例如 cam_001,cam_999",
    )
    parser.add_argument(
        "--feature-text",
        required=True,
        help="特征文本描述（模拟）",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8010",
        help="服务地址，默认 http://127.0.0.1:8010",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=5,
        help="轮询间隔秒数，默认 5",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="总超时秒数，默认 180",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    camera_ids = [item.strip() for item in args.camera_ids.split(",") if item.strip()]
    if not camera_ids:
        _exit_with_error("invalid_input", "camera_ids 不能为空")
    feature_text = args.feature_text.strip()
    if not feature_text:
        _exit_with_error("invalid_input", "feature_text 不能为空")
    if args.poll_interval <= 0:
        _exit_with_error("invalid_input", "poll_interval 必须大于 0")
    if args.timeout <= 0:
        _exit_with_error("invalid_input", "timeout 必须大于 0")

    base_url = args.base_url.rstrip("/")
    preprocess_url = f"{base_url}/api/v1/cameras/preprocess"
    search_url = f"{base_url}/api/v1/search"

    create_status, create_data = _http_json(
        "POST",
        preprocess_url,
        {"camera_ids": camera_ids},
    )
    if create_status != 200:
        _exit_with_error(
            "create_task_error",
            "创建预处理任务失败",
            {"http_status": create_status, "response": create_data},
        )

    task_id = create_data.get("task_id")
    if not task_id or not isinstance(task_id, str):
        _exit_with_error("invalid_response", "预处理任务创建响应缺少 task_id", {"response": create_data})

    query_status_url = f"{base_url}/api/v1/cameras/preprocess/{task_id}"
    started_at = time.time()
    poll_count = 0
    final_preprocess: dict[str, Any] | None = None

    while True:
        poll_count += 1
        status_code, status_data = _http_json("GET", query_status_url)
        if status_code != 200:
            _exit_with_error(
                "query_task_error",
                "查询预处理任务状态失败",
                {"http_status": status_code, "response": status_data, "task_id": task_id},
            )

        status = status_data.get("status")
        final_preprocess = status_data

        if status == "completed":
            break
        if status not in {"pending", "running"}:
            _exit_with_error(
                "status_error",
                "预处理任务状态异常",
                {"task_id": task_id, "status": status, "response": status_data},
            )
        if time.time() - started_at > args.timeout:
            _exit_with_error(
                "timeout",
                "轮询预处理任务超时",
                {"task_id": task_id, "last_status": status_data},
            )
        time.sleep(args.poll_interval)

    search_status, search_data = _http_json(
        "POST",
        search_url,
        {"mode": "text", "text": feature_text},
    )
    if search_status != 200:
        _exit_with_error(
            "search_error",
            "特征检索失败",
            {"http_status": search_status, "response": search_data, "task_id": task_id},
        )

    items = search_data.get("items")
    if not isinstance(items, list):
        _exit_with_error("invalid_response", "检索响应缺少 items 列表", {"response": search_data})

    camera_set = set(camera_ids)
    matched_items = [
        item
        for item in items
        if isinstance(item, dict) and isinstance(item.get("camera_id"), str) and item["camera_id"] in camera_set
    ]

    output = {
        "ok": True,
        "task": create_data,
        "preprocess": final_preprocess,
        "search": search_data,
        "matched_items": matched_items,
        "meta": {
            "camera_ids": camera_ids,
            "feature_text": feature_text,
            "poll_interval": args.poll_interval,
            "poll_count": poll_count,
            "timeout": args.timeout,
            "base_url": base_url,
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

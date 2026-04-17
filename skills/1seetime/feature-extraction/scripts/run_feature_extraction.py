import argparse
import json
import time
import urllib.request
import urllib.error
import sys
from datetime import datetime, timezone

def post_json(url, data):
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return {"code": e.code, "msg": e.reason, "result": None}
    except Exception as e:
        return {"code": 500, "msg": str(e), "result": None}

def get_json(url):
    req = urllib.request.Request(url, method='GET')
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return {"code": e.code, "msg": e.reason, "result": None}
    except Exception as e:
        return {"code": 500, "msg": str(e), "result": None}

def parse_iso8601_to_ms(time_str):
    """Parse ISO8601 string to millisecond timestamp."""
    try:
        dt = datetime.fromisoformat(time_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        # Fallback: if already a numeric timestamp, use directly
        return int(float(time_str))

def group_by_camera(raw_results):
    """Group per-camera API results into nested frame_results structure.

    API returns: [{c_id, name, addr, lat, lon, results: [{image_url, timestamp, features}]}]
    Transforms to nested structure (1 item = 1 camera):
        [{camera_id, camera_name, location, latitude, longitude,
          frames: [{image_url, timestamp, features: [{feature_name, feature_bbox}]}]}]

    Filters out:
    - frames with empty features (no feature matched in that frame)
    - cameras with no matched frames after frame filtering
    """
    grouped = []
    for cam in raw_results:
        # Keep only frames that have at least one feature match
        matched_frames = []
        for frame in cam.get("results", []):
            features = frame.get("features", []) or []
            if not features:
                continue
            matched_frames.append({
                "image_url": frame.get("image_url") or frame.get("img_url"),
                "timestamp": frame.get("timestamp"),
                "features": features,
            })

        # Skip cameras with no matched frames
        if not matched_frames:
            continue

        grouped.append({
            "camera_id": cam.get("c_id"),
            "camera_name": cam.get("name"),
            "location": cam.get("addr"),
            "latitude": cam.get("lat"),
            "longitude": cam.get("lon"),
            "frames": matched_frames,
        })
    return grouped

def main():
    parser = argparse.ArgumentParser(description="Feature Extraction Pipeline")
    parser.add_argument("--camera-ids", required=True, help="Comma-separated camera IDs")
    parser.add_argument("--features", required=True,
                        help="Comma-separated English feature keywords (e.g. 'yellow van,pedestrian')")
    parser.add_argument("--start-time", required=True, help="Start time (ISO8601)")
    parser.add_argument("--end-time", required=True, help="End time (ISO8601)")
    parser.add_argument("--base-url", default="https://search.zhidaozhixing.com", help="Base URL")
    parser.add_argument("--poll-interval", type=int, default=5, help="Poll interval in seconds")
    parser.add_argument("--timeout", type=int, default=3600, help="Timeout in seconds")
    parser.add_argument("--source", type=int, default=None, help="Data source: 1=toc, 2=governance")
    parser.add_argument("--output-file", default=None,
                        help="Write full frame_results JSON here; stdout only prints summary + path. "
                             "Defaults to /tmp/seeagent_frame_results_{task_id[:8]}.json")

    args = parser.parse_args()

    camera_ids = [cid.strip() for cid in args.camera_ids.split(",") if cid.strip()]
    features_list = [f.strip() for f in args.features.split(",") if f.strip()]

    if not features_list:
        print(json.dumps({"error": "features_empty", "msg": "At least one feature is required"}))
        sys.exit(1)

    # Parse and validate time range
    start_ts_ms = parse_iso8601_to_ms(args.start_time)
    end_ts_ms = parse_iso8601_to_ms(args.end_time)

    if end_ts_ms <= start_ts_ms:
        print(json.dumps({"error": "invalid_time_range",
                          "msg": "end_time must be greater than start_time"}))
        sys.exit(1)

    if (end_ts_ms - start_ts_ms) > 86400 * 1000:
        print(json.dumps({"error": "time_range_exceeds_limit",
                          "msg": "Time range must be <= 1 day (86400s). "
                                 "Please split into multiple requests."}))
        sys.exit(1)

    start_time_stamp = time.time()

    # Step 1: Create Preprocess Task
    create_url = f"{args.base_url}/api/v1/cameras/preprocess"
    create_payload = {
        "c_ids": camera_ids,
        "start_time": start_ts_ms,
        "end_time": end_ts_ms,
        "features": features_list,
    }
    if args.source is not None:
        create_payload["source"] = args.source

    create_res = post_json(create_url, create_payload)
    if create_res.get("code") != 200 or not create_res.get("result"):
        print(json.dumps({"error": "Failed to create preprocess task", "details": create_res}))
        sys.exit(1)

    task_info = create_res["result"]
    task_id = task_info.get("task_id")
    if not task_id:
        print(json.dumps({"error": "No task_id returned", "details": create_res}))
        sys.exit(1)

    # Step 2: Poll Preprocess Status (with detail=true for partial results)
    status_url = f"{args.base_url}/api/v1/cameras/preprocess/{task_id}?detail=true"

    poll_start = time.time()
    final_status = None
    partial_data = None

    while True:
        if time.time() - poll_start > args.timeout:
            print(json.dumps({
                "error": "timeout",
                "task": task_info,
                "preprocess": final_status
            }))
            sys.exit(1)

        status_res = get_json(status_url)
        if status_res.get("code") != 200:
            print(f"Poll warning: {status_res.get('msg', 'unknown error')}", file=sys.stderr)
            time.sleep(args.poll_interval)
            continue

        final_status = status_res.get("result", {})
        status = final_status.get("status", "").upper()

        try:
            progress = float(final_status.get("progress", 0))
        except (ValueError, TypeError):
            progress = 0

        # Log partial progress from data field
        data = final_status.get("data", [])
        if data:
            partial_data = data
            cam_count = len(data)
            frame_count = sum(len(c.get("results", [])) for c in data)
            print(f"Progress: {progress:.0f}% | {cam_count} cameras, "
                  f"{frame_count} frames so far", file=sys.stderr)

        if status == "COMPLETED" or progress >= 100:
            break
        elif status == "FAILED":
            print(json.dumps({
                "error": "task_failed",
                "task": task_info,
                "preprocess": final_status
            }))
            sys.exit(1)

        time.sleep(args.poll_interval)

    # Step 3: Get Task Results
    # If polling with detail=true already returned complete data, use it directly
    raw_results = partial_data if partial_data else []

    if not raw_results:
        results_url = f"{args.base_url}/api/v1/task/results/{task_id}"
        results_res = get_json(results_url)
        if results_res.get("code") == 200 and results_res.get("result"):
            raw_results = results_res["result"]

    # Group per-camera results into nested frame_results (filter empty frames/cameras)
    frame_results = group_by_camera(raw_results)

    total_time = time.time() - start_time_stamp

    meta = {
        "task_id": task_info.get("task_id"),
        "status": final_status.get("status") if final_status else None,
        "progress": final_status.get("progress") if final_status else None,
        "total_time": total_time,
        "features": features_list,
        "camera_count": len(camera_ids),
        "matched_cameras": len(frame_results),
        "matched_frames": sum(len(c["frames"]) for c in frame_results),
    }

    # Determine output path: use task_id prefix for uniqueness across concurrent/sequential runs
    output_file = args.output_file
    if output_file is None:
        task_short = (meta["task_id"] or "unknown")[:8]
        output_file = f"/tmp/seeagent_frame_results_{task_short}.json"

    # Write full results to file (avoids stdout truncation in BP engine tool_results)
    full_output = {"frame_results": frame_results, "meta": meta}
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(full_output, f, ensure_ascii=False, indent=2)

    # stdout: compact summary + file path only (keeps tool_result small, no truncation)
    print(json.dumps({
        "frame_results_path": output_file,
        "meta": meta,
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()

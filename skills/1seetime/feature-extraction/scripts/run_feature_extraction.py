import argparse
import json
import time
import urllib.request
import urllib.error
import sys

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

def main():
    parser = argparse.ArgumentParser(description="Feature Extraction Pipeline")
    parser.add_argument("--camera-ids", required=True, help="Comma-separated camera IDs")
    parser.add_argument("--feature-text", required=True, help="Feature text to search")
    parser.add_argument("--start-time", required=True, help="Start time (ISO8601)")
    parser.add_argument("--end-time", required=True, help="End time (ISO8601)")
    parser.add_argument("--base-url", default="https://api-platform-test.zhidaozhixing.com", help="Base URL")
    parser.add_argument("--poll-interval", type=int, default=5, help="Poll interval in seconds")
    parser.add_argument("--timeout", type=int, default=3600, help="Timeout in seconds")
    parser.add_argument("--top-k", type=int, default=20, help="Top K results")

    args = parser.parse_args()

    camera_ids = [cid.strip() for cid in args.camera_ids.split(",") if cid.strip()]
    
    start_time_stamp = time.time()

    # Step 1: Create Preprocess Task (API 5)
    create_url = f"{args.base_url}/api/v1/cameras/preprocess"
    create_payload = {
        "camera_ids": camera_ids,
        "start_time": args.start_time,
        "end_time": args.end_time,
        "frame_rate": 1
    }

    create_res = post_json(create_url, create_payload)
    if create_res.get("code") != 200 or not create_res.get("result"):
        print(json.dumps({"error": "Failed to create preprocess task", "details": create_res}))
        sys.exit(1)

    task_info = create_res["result"]
    task_id = task_info.get("task_id")
    if not task_id:
        print(json.dumps({"error": "No task_id returned", "details": create_res}))
        sys.exit(1)

    # Step 2: Poll Preprocess Status (API 6)
    status_url = f"{args.base_url}/api/v1/cameras/preprocess/{task_id}?detail=false"
    
    poll_start = time.time()
    final_status = None

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
        status = final_status.get("status")
        
        try:
            progress = float(final_status.get("progress", 0))
        except (ValueError, TypeError):
            progress = 0

        if status == "completed" or progress >= 100:
            break
        elif status == "failed":
            print(json.dumps({
                "error": "task_failed",
                "task": task_info,
                "preprocess": final_status
            }))
            sys.exit(1)
        
        time.sleep(args.poll_interval)

    # Step 3: Feature Search (API 7)
    search_url = f"{args.base_url}/api/v1/search"
    search_payload = {
        "text": args.feature_text,
        "task_id": task_id,
        "img_url": None,
        "img_base64": None,
        "top_k": args.top_k
    }

    search_res = post_json(search_url, search_payload)
    search_result = []
    if search_res.get("code") == 200 and search_res.get("result"):
        search_result = search_res["result"].get("result", [])

    total_time = time.time() - start_time_stamp

    output = {
        "task": task_info,
        "preprocess": final_status,
        "search": search_result,
        "meta": {
            "total_time": total_time,
            "feature_text": args.feature_text,
            "camera_count": len(camera_ids)
        }
    }

    print(json.dumps(output, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Use LLM to select the best matching parking lot from API candidates.
"""
import argparse
import json
import math
import re
import sys
import os
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

DASHSCOPE_API_KEY_PATH = "/root/.dashscope_key"
DASHSCOPE_ENDPOINT = "https://seeapi.zhidaoauto.com/v1/chat/completions"
MODEL = "MiniMax-M2.5"
DEFAULT_DASHSCOPE_API_KEY = "sk-sc86Gw7sG3g9VMyDtIOezDF2uuCN45igR7zHH05iSAUerH4f"

OUTDATED_THRESHOLD_MINUTES = 20
FORBIDDEN_OUTPUT_TOKENS = ("```", "seeclaw-json-park")


def save_json(filepath, data):
    """Save data as JSON to the specified file path."""
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_api_key():
    """Load DashScope API key from env or file."""
    env_key = os.getenv("DASHSCOPE_API_KEY")
    if env_key:
        return env_key.strip()

    try:
        with open(DASHSCOPE_API_KEY_PATH, 'r') as f:
            return f.read().strip()
    except Exception as e:
        if DEFAULT_DASHSCOPE_API_KEY:
            return DEFAULT_DASHSCOPE_API_KEY
        print(f"Error loading API key: {e}", file=sys.stderr)
        sys.exit(1)


def select_best_match(candidates, user_query):
    """Use LLM to select the best matching parking lot."""
    if not candidates:
        return None

    api_key = load_api_key()

    # 只提取停车场名称发送给 LLM，减少 token 消耗
    simplified_candidates = []
    for idx, candidate in enumerate(candidates):
        parking_name = candidate.get('parkingName') or candidate.get('parking_name') or candidate.get('name') or f"停车场{idx+1}"
        simplified_candidates.append({
            "index": idx,
            "parkingName": parking_name
        })

    candidates_text = json.dumps(simplified_candidates, ensure_ascii=False, indent=2)

    prompt = f"""你是一个停车场匹配助手。用户询问了停车场信息，API 返回了多个候选停车场。请分析用户的查询意图，从候选列表中选择最匹配的停车场。

用户查询：
{user_query}

候选停车场列表：
{candidates_text}

请分析用户提到的停车场名称或地标，与候选停车场的名称相似度，选择最匹配的停车场。

请直接返回最匹配的停车场在候选列表中的索引（从0开始），只返回数字，不要其他内容。"""

    try:
        response = requests.post(
            DASHSCOPE_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": MODEL,
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            },
            timeout=30
        )
        response.raise_for_status()
        result = response.json()

        content = result['choices'][0]['message']['content'].strip()
        # 剥离 LLM 可能返回的 <think>...</think> 思考过程
        content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        # 提取第一个整数
        match = re.search(r'\d+', content)
        if not match:
            print(f"Warning: LLM response contains no integer, falling back to first candidate. Response: {content[:200]}", file=sys.stderr)
            return candidates[0]
        selected_index = int(match.group())

        if 0 <= selected_index < len(candidates):
            return candidates[selected_index]
        else:
            return candidates[0]

    except Exception as e:
        print(f"Error calling LLM: {e}", file=sys.stderr)
        return candidates[0] if candidates else None


def _first(existing, *keys):
    for key in keys:
        if isinstance(existing, dict) and key in existing and existing[key] is not None:
            return existing[key]
    return None


def _to_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default=0):
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_iso(ts):
    if not ts or not isinstance(ts, str):
        return None
    try:
        normalized = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


# 北京时区 UTC+8
_BEIJING_TZ = timezone(timedelta(hours=8))


def _normalize_timestamp(ts):
    """将时间戳规范化为 YYYY-MM-DDTHH:MM:SS+08:00 格式。

    支持的输入格式：
    - ISO8601 带时区：2026-04-01T14:30:46+08:00
    - ISO8601 UTC：2026-04-01T06:30:46Z
    - ISO8601 无时区：2026-04-01T14:30:46（视为北京时间）
    - 空格分隔：2026-04-01 14:30:46（视为北京时间）
    - Unix 时间戳（毫秒）：1751426319003
    - Unix 时间戳（秒）：1751426319
    """
    if not ts:
        return None

    # 处理数字型 Unix 时间戳
    if isinstance(ts, (int, float)):
        # 毫秒级时间戳（13位数字）
        if ts > 1e12:
            ts = ts / 1000
        dt = datetime.fromtimestamp(ts, tz=_BEIJING_TZ)
        return dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")

    if not isinstance(ts, str):
        return None

    ts = ts.strip()

    # 处理纯数字字符串（Unix 时间戳）
    if ts.isdigit():
        numeric_ts = int(ts)
        if numeric_ts > 1e12:
            numeric_ts = numeric_ts / 1000
        dt = datetime.fromtimestamp(numeric_ts, tz=_BEIJING_TZ)
        return dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")

    # 处理空格分隔的日期时间格式
    ts = ts.replace(" ", "T", 1)

    parsed = _parse_iso(ts)
    if not parsed:
        return None

    # 无时区信息，视为北京时间
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_BEIJING_TZ)

    # 转换为北京时间
    beijing_dt = parsed.astimezone(_BEIJING_TZ)
    return beijing_dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _is_outdated(data_timestamp, explicit_flag, explicit_minutes):
    if explicit_flag is not None:
        return bool(explicit_flag)
    if explicit_minutes is not None:
        return _to_int(explicit_minutes, 0) > OUTDATED_THRESHOLD_MINUTES
    parsed = _parse_iso(data_timestamp)
    if not parsed:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = datetime.now(parsed.tzinfo)
    return now - parsed > timedelta(minutes=OUTDATED_THRESHOLD_MINUTES)


def _build_snapshots(cameras):
    snapshots = []
    for idx, camera in enumerate(cameras or []):
        snapshots.append({
            "cameraId": str(_first(camera, "cameraId", "camera_id", "id", "cameraId") or f"cam_{idx + 1}"),
            "cameraName": str(_first(camera, "cameraName", "camera_name", "name") or f"摄像头{idx + 1}"),
            "url": str(_first(camera, "url", "snapshot_url", "snapshotUrl") or ""),
            "timestamp": _normalize_timestamp(_first(camera, "timestamp", "snapshot_time", "snapshotTime")),
            "isAvailable": bool(_first(camera, "isAvailable", "is_available", "available", "status") not in [False, "offline", "unavailable"])
        })
    return snapshots


def _build_entrances(entries):
    entrances = []
    for idx, entry in enumerate(entries or []):
        entry_loc = _first(entry, "location", "entry_location") or {}
        entrances.append({
            "entryId": str(_first(entry, "entryId", "entry_id", "id") or f"entry_{idx + 1}"),
            "location": {
                "lat": _to_float(_first(entry_loc, "lat", "latitude")),
                "lng": _to_float(_first(entry_loc, "lng", "longitude"))
            },
            "direction": str(_first(entry, "direction", "name") or "未知方向")
        })
    return entrances


def _haversine_distance(lat1, lng1, lat2, lng2):
    """Calculate distance between two points in meters using Haversine formula."""
    R = 6371000  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _parse_distance_meters(distance_str):
    """Parse distance strings like '350米', '1.2km', '0.8公里' into float meters."""
    if not distance_str or not isinstance(distance_str, str):
        return None
    s = distance_str.strip().lower()
    m = re.match(r'^([\d.]+)\s*(km|公里|千米|米|m)?$', s)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2) or '米'
    if unit in ('km', '公里', '千米'):
        return value * 1000
    return value


def _resolve_distance_meters(scenario, search_lat=None, search_lng=None):
    """Get distance in meters: prefer scenario['distance'] string, fallback to haversine."""
    dist_str = scenario.get("distance")
    parsed = _parse_distance_meters(dist_str)
    if parsed is not None:
        return parsed
    if search_lat is not None and search_lng is not None:
        loc = scenario.get("location", {})
        lat = _to_float(loc.get("latitude"), None)
        lng = _to_float(loc.get("longitude"), None)
        if lat is not None and lng is not None and (lat != 0.0 or lng != 0.0):
            return _haversine_distance(search_lat, search_lng, lat, lng)
    return float('inf')


def _find_forbidden_tokens(value, path="$"):
    issues = []
    if isinstance(value, dict):
        for key, item in value.items():
            issues.extend(_find_forbidden_tokens(item, f"{path}.{key}"))
        return issues
    if isinstance(value, list):
        for idx, item in enumerate(value):
            issues.extend(_find_forbidden_tokens(item, f"{path}[{idx}]"))
        return issues
    if isinstance(value, str):
        for token in FORBIDDEN_OUTPUT_TOKENS:
            if token in value:
                issues.append((path, token))
    return issues


def _sanitize_forbidden_tokens(value):
    if isinstance(value, dict):
        return {k: _sanitize_forbidden_tokens(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_forbidden_tokens(v) for v in value]
    if isinstance(value, str):
        cleaned = value
        cleaned = cleaned.replace("```seeclaw-json-park", "")
        cleaned = cleaned.replace("```", "")
        cleaned = cleaned.replace("seeclaw-json-park", "")
        return cleaned.strip()
    return value


def sanitize_and_validate_output(output_obj, max_attempts=2):
    """Clean fenced-markdown leakage from JSON strings, then validate payload."""
    candidate = copy.deepcopy(output_obj)

    for _ in range(max_attempts + 1):
        issues = _find_forbidden_tokens(candidate)
        if not issues:
            # Ensure object is JSON-serializable and valid.
            json.loads(json.dumps(candidate, ensure_ascii=False))
            return candidate
        candidate = _sanitize_forbidden_tokens(candidate)

    issue_text = ", ".join([f"{path}:{token}" for path, token in issues])
    raise ValueError(f"输出仍包含非法代码块标记: {issue_text}")


def to_seeclaw_codeblock(output_obj):
    safe_obj = sanitize_and_validate_output(output_obj)
    return "```seeclaw-json-park\n" + json.dumps(safe_obj, ensure_ascii=False, indent=2) + "\n```"


def build_standard_output(selected, user_query):
    # 先清理输入数据中可能存在的代码块标记污染
    if selected:
        selected = _sanitize_forbidden_tokens(selected)
    user_query = _sanitize_forbidden_tokens(user_query) if isinstance(user_query, str) else user_query

    if not selected:
        return {
            "parkingScenarios": [
                {
                    "parkingId": None,
                    "status": "no_camera",
                    "coverageStatus": "uncovered",
                    "measuredStatus": "unknown",
                    "parkingName": "未找到匹配停车场",
                    "statusText": "暂未找到相关停车场",
                    "statusDescription": "请尝试更具体的停车场名称或附近地标",
                    "hasNavigation": False,
                    "hasRefresh": False,
                    "hasNearbyParking": True,
                    "processedBy": "SeeClaw",
                    "userMessage": user_query,
                    "location": {
                        "latitude": 0.0,
                        "longitude": 0.0,
                        "coordSystem": "wgs84"
                    }
                }
            ]
        }

    location = _first(selected, "location", "geo", "coordinate") or {}
    cameras = _first(selected, "cameras", "camera_list", "cameraList") or []
    entries = _first(selected, "entry_points", "entries", "entrances") or []

    # 尝试从顶层获取车位数据
    available_spots = _first(selected, "availableSpots", "available_spots", "available")
    total_spots = _first(selected, "totalSpots", "total_spots", "capacity")

    # 如果顶层没有，从 cameras 数组聚合
    if available_spots is None or total_spots is None:
        camera_total = 0
        camera_available = 0
        for camera in cameras:
            cam_total = _first(camera, "totalSpots", "total_spots", "capacity")
            cam_available = _first(camera, "availableSpots", "available_spots", "available")
            if cam_total is not None:
                camera_total += _to_int(cam_total, 0)
            if cam_available is not None:
                camera_available += _to_int(cam_available, 0)

        if camera_total > 0:
            total_spots = camera_total
            available_spots = camera_available

    total_known = total_spots is not None

    data_timestamp_raw = _first(selected, "dataTimestamp", "data_timestamp", "last_update", "lastUpdate")
    # 如果顶层没有时间戳，从摄像头时间戳中取最新的
    if data_timestamp_raw is None and cameras:
        cam_timestamps = []
        for cam in cameras:
            ct = _first(cam, "timestamp", "snapshot_time", "snapshotTime")
            normalized = _normalize_timestamp(ct)
            if normalized:
                cam_timestamps.append(normalized)
        if cam_timestamps:
            data_timestamp_raw = max(cam_timestamps)
    data_timestamp = _normalize_timestamp(data_timestamp_raw) or data_timestamp_raw
    explicit_outdated = _first(selected, "isSnapshotOutdated", "is_snapshot_outdated")
    explicit_outdated_minutes = _first(selected, "dataOutdatedMinutes", "data_outdated_minutes")
    is_outdated = _is_outdated(data_timestamp, explicit_outdated, explicit_outdated_minutes)

    if not cameras:
        status = "no_camera"
    elif is_outdated:
        status = "outdated"
    else:
        status = "normal"

    # 计算 measuredStatus（实测空位状态）
    measured_status = "unknown"
    if status != "no_camera" and total_known and _to_int(total_spots, 0) > 0:
        vacancy_rate = _to_int(available_spots, 0) / _to_int(total_spots, 0)
        if vacancy_rate >= 0.4:
            measured_status = "spacious"
        elif vacancy_rate > 0.2:
            measured_status = "moderate"
        elif vacancy_rate > 0.05:
            measured_status = "tight"
        else:
            measured_status = "full"

    snapshots = _build_snapshots(cameras)
    entrances = _build_entrances(entries)

    # 计算 outdated 状态下的分钟数（用于 statusText）
    outdated_minutes = 0
    if status == "outdated" and data_timestamp:
        parsed_ts = _parse_iso(data_timestamp)
        if parsed_ts:
            if parsed_ts.tzinfo is None:
                parsed_ts = parsed_ts.replace(tzinfo=timezone.utc)
            now = datetime.now(parsed_ts.tzinfo)
            outdated_minutes = int((now - parsed_ts).total_seconds() / 60)

    # 根据状态生成不同的文案
    if status == "normal":
        available_num = _to_int(available_spots, 0)
        total_num = _to_int(total_spots, 0)
        if total_known and total_num > 0:
            occupancy_rate = (1 - available_num / total_num) * 100
            if occupancy_rate < 50:
                status_text = "空位充足，适合现在前往"
                status_desc = "已覆盖区域空位较充裕，进场顺畅"
            elif occupancy_rate < 80:
                status_text = "空位适中，可以前往"
                status_desc = "已覆盖区域有一定空位，建议尽快前往"
            else:
                status_text = "空位紧张，请谨慎前往"
                status_desc = "已覆盖区域空位较少，可能需要等待"
        else:
            status_text = "空位信息可参考"
            status_desc = "已获取停车场实时数据"
    elif status == "outdated":
        status_text = f"约{outdated_minutes}分钟前空位充足，现状待确认" if outdated_minutes > 0 else "数据可能已过时，现状待确认"
        status_desc = f"数据采集于{data_timestamp}，距现在约{outdated_minutes}分钟，仅供参考" if outdated_minutes > 0 else f"数据采集时间为{data_timestamp}，请谨慎参考"
    else:  # no_camera
        status_text = "暂未覆盖该停车场"
        status_desc = "该停车场尚未接入实时摄像头数据，无法提供空位信息"

    # 按照统一数据结构的字段顺序构建 scenario
    raw_parking_id = _first(selected, "parkingId", "parking_id", "id")
    # coverageStatus: covered (有摄像头覆盖) / uncovered (无摄像头覆盖)
    coverage_status = "uncovered" if status == "no_camera" else "covered"

    scenario = {
        "parkingId": str(raw_parking_id) if raw_parking_id is not None else None,
        "status": status,
        "coverageStatus": coverage_status,
        "measuredStatus": measured_status,
        "parkingName": str(_first(selected, "parkingName", "parking_name", "name") or "未知停车场")
    }

    # snapshots (仅 normal 和 outdated)
    if status in ("normal", "outdated") and snapshots:
        scenario["snapshots"] = snapshots

    # isSnapshotOutdated (仅 outdated)
    if status == "outdated":
        scenario["isSnapshotOutdated"] = True

    # statusText 和 statusDescription
    scenario["statusText"] = status_text
    scenario["statusDescription"] = status_desc

    # dataTimestamp (仅 normal 和 outdated)
    if status in ("normal", "outdated") and data_timestamp:
        scenario["dataTimestamp"] = data_timestamp

    # dataOutdatedMinutes (仅 outdated)
    if status == "outdated" and outdated_minutes > 0:
        scenario["dataOutdatedMinutes"] = outdated_minutes

    # historyData (仅 outdated，暂时使用占位数据)
    if status == "outdated":
        scenario["historyData"] = {
            "title": "历史规律参考",
            "description": "该时段通常空位尚可",
            "sampleCount": 0,
            "note": "暂无历史数据样本"
        }

    # hasNavigation, hasRefresh, hasNearbyParking
    scenario["hasNavigation"] = True if status == "normal" else (True if status == "no_camera" else False)
    scenario["hasRefresh"] = True if status in ("normal", "outdated") else False
    scenario["hasNearbyParking"] = False if status == "normal" else True

    # processedBy
    scenario["processedBy"] = "SeeClaw"

    # userMessage
    scenario["userMessage"] = user_query

    # location
    scenario["location"] = {
        "latitude": _to_float(_first(location, "latitude", "lat")),
        "longitude": _to_float(_first(location, "longitude", "lng")),
        "coordSystem": _first(location, "coordSystem", "coord_system") or "wgs84"
    }

    # distance (可选)
    distance = _first(selected, "distance")
    if distance is not None:
        scenario["distance"] = str(distance)

    # availability (仅 normal 和 outdated)
    if status in ("normal", "outdated") and available_spots is not None:
        available_num = _to_int(available_spots, 0)
        total_num = _to_int(total_spots, 0)
        if total_known and total_num > 0:
            occupancy_rate = max(0.0, min(100.0, (1 - available_num / total_num) * 100))
            occupancy_text = f"{occupancy_rate:.1f}%"
        else:
            occupancy_text = "未知"

        scenario["availability"] = {
            "totalSpots": total_num,
            "availableSpots": available_num,
            "occupancyRate": occupancy_text,
            "totalSpotsKnown": bool(total_known)
        }

    # feeStandard (可选)
    fee_standard = _first(selected, "feeStandard", "fee_standard")
    if fee_standard is not None:
        scenario["feeStandard"] = str(fee_standard)

    # entrances (可选)
    if entrances:
        scenario["entrances"] = entrances

    return {"parkingScenarios": [scenario]}


_MEASURED_STATUS_PRIORITY = {"spacious": 0, "moderate": 1, "tight": 2, "unknown": 3}


def rank_nearby_scenarios(scenarios, search_lat=None, search_lng=None):
    """Rank nearby scenarios: filter full, sort by status/freshness/distance, return top 3."""
    filtered = [s for s in scenarios if s.get("measuredStatus") != "full"]
    if not filtered:
        return {"totalFound": 0, "rankedList": []}

    def sort_key(s):
        status_pri = _MEASURED_STATUS_PRIORITY.get(s.get("measuredStatus", "unknown"), 3)
        freshness = 1 if s.get("status") == "outdated" else 0
        dist = _resolve_distance_meters(s, search_lat, search_lng)
        return (status_pri, freshness, dist)

    filtered.sort(key=sort_key)
    top = filtered[:3]
    for i, s in enumerate(top, start=1):
        s["rank"] = i
        # Move rank before parkingId in key order
        reordered = {"rank": s.pop("rank")}
        reordered.update(s)
        top[i - 1] = reordered
    return {"totalFound": len(filtered), "rankedList": top}


def main():
    parser = argparse.ArgumentParser(description='Select best matching parking lot using LLM')
    parser.add_argument('--candidates', required=True, help='Path to JSON file with API candidates')
    parser.add_argument('--user-query', required=True, help='Original user query')
    parser.add_argument('--output', required=True, help='Output JSON file path for final result')
    parser.add_argument(
        '--emit-codeblock',
        action='store_true',
        help='Print final output as a fenced seeclaw-json-park code block'
    )
    parser.add_argument(
        '--mode',
        choices=['name', 'nearby'],
        default='name',
        help='Search mode: "name" uses LLM to pick best match, "nearby" returns all candidates (default: name)'
    )
    parser.add_argument('--lat', type=float, default=None, help='Search latitude for distance calculation (nearby mode)')
    parser.add_argument('--lng', type=float, default=None, help='Search longitude for distance calculation (nearby mode)')

    args = parser.parse_args()

    output_path = Path(args.output)
    task_dir = str(output_path.parent)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Load candidates
    try:
        with open(args.candidates, 'r', encoding='utf-8') as f:
            data = json.load(f)
            candidates = data.get('data', [])
    except Exception as e:
        print(f"Error loading candidates: {e}", file=sys.stderr)
        sys.exit(1)

    # Select best match or build multi-scenario for nearby mode
    if args.mode == 'nearby':
        # Nearby mode: skip LLM, build output for all candidates, then rank
        if candidates:
            all_scenarios = []
            for candidate in candidates:
                single_output = build_standard_output(candidate, args.user_query)
                all_scenarios.extend(single_output["parkingScenarios"])
            standard_output = rank_nearby_scenarios(all_scenarios, args.lat, args.lng)
        else:
            standard_output = {"totalFound": 0, "rankedList": []}
        save_json(os.path.join(task_dir, "selected_match.json"),
                  {"mode": "nearby", "candidate_count": len(candidates)})
    else:
        # Name mode: use LLM to pick best match
        selected = select_best_match(candidates, args.user_query)
        save_json(os.path.join(task_dir, "selected_match.json"),
                  selected if selected else {"error": "No matching parking lot found"})
        standard_output = build_standard_output(selected, args.user_query)
    safe_output = sanitize_and_validate_output(standard_output)
    save_json(str(output_path), safe_output)
    if args.emit_codeblock:
        print(to_seeclaw_codeblock(safe_output))
    else:
        print(json.dumps(safe_output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

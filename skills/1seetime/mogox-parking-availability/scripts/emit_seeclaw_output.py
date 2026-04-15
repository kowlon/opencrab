#!/usr/bin/env python3
"""Emit final seeclaw-json-park code block from task output, with safe fallback."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from select_best_match import sanitize_and_validate_output, to_seeclaw_codeblock


def _fallback_output(user_query: str, mode: str = "name") -> dict:
    if mode == "nearby":
        return {"totalFound": 0, "rankedList": []}
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
                    "coordSystem": "wgs84",
                },
            }
        ]
    }


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Emit validated seeclaw-json-park code block from final_output.json"
    )
    parser.add_argument("--input", required=True, help="Path to final_output.json file")
    parser.add_argument("--user-query", default="", help="Fallback user query text for no_camera output")
    parser.add_argument("--mode", choices=["name", "nearby"], default="name",
                        help="Search mode: affects fallback output structure (default: name)")
    args = parser.parse_args()

    final_output_path = Path(args.input)

    if final_output_path.exists():
        output_obj = _load_json(final_output_path)
    else:
        output_obj = _fallback_output(args.user_query, args.mode)

    safe_obj = sanitize_and_validate_output(output_obj)
    print(to_seeclaw_codeblock(safe_obj))


if __name__ == "__main__":
    main()

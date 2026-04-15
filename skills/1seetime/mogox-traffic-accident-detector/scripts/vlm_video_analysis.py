#!/usr/bin/env python3
"""
交通事故时间区间识别脚本 (VLM 原视频分析版)
使用多模态大模型直接分析视频，识别交通事故发生的时间区间
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Union
from urllib.parse import unquote, urlparse

# LLM 配置
DEFAULT_BASE_URL = "https://seeapi.zhidaoauto.com/v1"
DEFAULT_API_KEY = "sk-sc86Gw7sG3g9VMyDtIOezDF2uuCN45igR7zHH05iSAUerH4f"
DEFAULT_MODEL = "Qwen3.5-122B-A10B-FP8"
SKILL_NAME = "mogox-traffic-accident-detector"
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_BASE = WORKSPACE_ROOT / "skills_result" / SKILL_NAME

SYSTEM_PROMPT = """你是一个专业的交通事故分析专家。你的任务是分析视频，识别交通事故发生的时间区间。

## 输出要求
请以 JSON 格式返回分析结果，包含以下字段：
- accident_intervals: 事故区间数组，每个区间包含：
  - start_time: 事故开始时间（秒）
  - end_time: 事故结束时间（秒）
  - confidence: 置信度（0-1）
  - description: 事故描述
  - road_structure_type: 道路结构类型（intersection=路口, road_segment=路段, viaduct=高架桥）
  - road_structure_confidence: 道路结构识别置信度（0-1）
  - phase_traffic_flow: 相位车流量分析（对象，键为相位名，值为 low/medium/high 或 0-1）
  - traffic_light_state: 事故发生时对应相位红绿灯状态（red/green/yellow）
  - traffic_light_confidence: 红绿灯状态推断置信度（0-1）

## 关键能力要求
1. 道路结构识别：必须在 intersection（路口）、road_segment（路段）、viaduct（高架桥）三类中选择一种，目标准确率 >= 90%。
2. 红绿灯状态判断：结合各相位车流量结果推断 red/green/yellow，目标准确率 >= 85%。

## 事故类型识别
请识别以下常见交通事故类型：
- 追尾事故：后车撞上前车
- 正面碰撞：车辆正面相撞
- 侧面碰撞：车辆侧面相撞（如变道、转弯）
- 倒车事故：倒车时发生碰撞
- 碰撞行人/非机动车
- 车辆侧翻
- 车辆起火燃烧

## 注意事项
1. 只返回确实发生事故的时间区间，不要误报
2. 如果视频中没有交通事故，返回空的 accident_intervals 数组
3. 置信度低于 0.5 的事故不要报告
4. 如果无法确定精确时间，给出合理的估计范围
5. 所有时间基于视频的第一帧为 0 秒计算
"""


@dataclass
class AccidentInterval:
    """事故时间区间"""
    start_time: float
    end_time: float
    confidence: float
    description: str
    road_structure_type: str = "unknown"
    road_structure_confidence: float = 0.0
    phase_traffic_flow: Optional[dict] = None
    traffic_light_state: str = "unknown"
    traffic_light_confidence: float = 0.0


@dataclass
class DetectionResult:
    """检测结果"""
    video_path: str
    model: str
    analyzed_frames: int
    accident_intervals: list
    total_accidents: int
    processing_time_seconds: float

    def to_dict(self):
        return {
            "video_path": self.video_path,
            "model": self.model,
            "analyzed_frames": self.analyzed_frames,
            "accident_intervals": [asdict(interval) for interval in self.accident_intervals],
            "total_accidents": self.total_accidents,
            "processing_time_seconds": self.processing_time_seconds
        }


def parse_vlm_response(response_text: str) -> list:
    """
    解析 VLM 模型返回的 JSON 响应
    """
    # 尝试提取 JSON
    json_match = re.search(r'\{.*\}|\[.*\]', response_text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group())
            if isinstance(data, dict) and "accident_intervals" in data:
                return data["accident_intervals"]
            elif isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    # 尝试解析自由格式的响应
    intervals = []
    lines = response_text.split("\n")
    for line in lines:
        # 匹配时间模式
        time_pattern = r'(\d+\.?\d*)\s*[-~至]\s*(\d+\.?\d*)'
        match = re.search(time_pattern, line)
        if match:
            try:
                interval = {
                    "start_time": float(match.group(1)),
                    "end_time": float(match.group(2)),
                    "confidence": 0.8,
                    "description": line.strip()[:200]
                }
                intervals.append(interval)
            except ValueError:
                pass

    return intervals


def get_video_name_from_url(video_url: str) -> str:
    """从视频 URL 提取可用于目录名的视频名称（自动解码 URL 编码）。"""
    parsed = urlparse(video_url)
    filename = Path(unquote(parsed.path)).name
    video_name = Path(filename).stem if filename else "video"

    # 清洗不适合目录名的字符，避免不同系统下路径异常
    video_name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", video_name).strip().strip(".")
    return video_name or "video"


def normalize_structure_type(raw_value: Optional[str]) -> str:
    """将道路结构类型归一化到三类标签。"""
    if not raw_value:
        return "unknown"
    value = str(raw_value).strip().lower()
    mapping = {
        "intersection": "intersection",
        "crossroad": "intersection",
        "junction": "intersection",
        "路口": "intersection",
        "road_segment": "road_segment",
        "segment": "road_segment",
        "straight_road": "road_segment",
        "路段": "road_segment",
        "viaduct": "viaduct",
        "elevated": "viaduct",
        "overpass": "viaduct",
        "高架桥": "viaduct",
    }
    return mapping.get(value, "unknown")


def to_flow_level(value: Union[str, int, float, None]) -> int:
    """将车流量描述转换为离散等级: low=1, medium=2, high=3。"""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric < 0.33:
            return 1
        if numeric < 0.66:
            return 2
        return 3
    text = str(value).strip().lower()
    mapping = {
        "low": 1,
        "small": 1,
        "轻": 1,
        "低": 1,
        "medium": 2,
        "mid": 2,
        "moderate": 2,
        "中": 2,
        "high": 3,
        "heavy": 3,
        "高": 3,
    }
    return mapping.get(text, 0)


def infer_traffic_light_state(phase_traffic_flow: Optional[dict]) -> tuple[str, float]:
    """
    基于相位车流量推断红绿灯状态:
    - 主导相位流量显著高于其它相位 -> green
    - 多相位接近且都较高 -> yellow
    - 主导相位较低且其它相位更高 -> red
    """
    if not phase_traffic_flow or not isinstance(phase_traffic_flow, dict):
        return "unknown", 0.0

    levels = [to_flow_level(v) for v in phase_traffic_flow.values()]
    levels = [x for x in levels if x > 0]
    if not levels:
        return "unknown", 0.0

    levels.sort(reverse=True)
    top = levels[0]
    second = levels[1] if len(levels) > 1 else 0

    if top >= 3 and second <= 1:
        return "green", 0.88
    if top >= 2 and abs(top - second) <= 1:
        return "yellow", 0.80
    if top <= 1 and second >= 2:
        return "red", 0.82
    if top > second:
        return "green", 0.74
    return "red", 0.70


def call_vlm_analyze_video(video_url: str, model: str,
                           base_url: str, api_key: str,
                           fps: float = 2.0) -> list:
    """
    调用 VLM 模型直接分析视频

    Args:
        video_url: 视频 URL（仅支持 http/https）
        model: VLM 模型名称
        base_url: API Base URL
        api_key: API Key
        fps: 视频采样帧率

    Returns:
        accident_intervals: 事故区间列表
    """
    from openai import OpenAI

    client = OpenAI(
        api_key=api_key,
        base_url=base_url
    )

    # 构建消息内容，直接传递视频 URL
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "video_url",
                    "video_url": {
                        "url": video_url
                    }
                },
                {
                    "type": "text",
                    "text": "请分析这个视频，识别交通事故发生的时间区间。以 JSON 格式返回结果。"
                }
            ]
        }
    ]

    # 调用 VLM 模型
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=8192,
            temperature=0.1,
            extra_body={
                "mm_processor_kwargs": {"fps": fps, "do_sample_frames": True},
            },
        )

        result_text = response.choices[0].message.content
        return parse_vlm_response(result_text)

    except Exception as e:
        print(f"VLM 模型调用失败: {e}", file=sys.stderr)
        return []


def analyze_video(video_url: str, model: str = DEFAULT_MODEL,
                  fps: float = 2.0,
                  api_key: str = DEFAULT_API_KEY,
                  base_url: str = DEFAULT_BASE_URL) -> dict:
    """
    使用 VLM 模型直接分析视频，检测交通事故时间区间

    Args:
        video_url: 视频 URL（仅支持 http/https）
        model: 使用的 VLM 模型
        fps: 视频采样帧率
        api_key: API Key
        base_url: API Base URL

    Returns:
        检测结果字典
    """
    start_time = time.time()

    # 仅支持 HTTP/HTTPS 视频地址
    if not (video_url.startswith("http://") or video_url.startswith("https://")):
        print(f"错误: 仅支持 http/https 视频地址: {video_url}", file=sys.stderr)
        return {
            "video_path": video_url,
            "model": model,
            "analyzed_frames": 0,
            "accident_intervals": [],
            "total_accidents": 0,
            "processing_time_seconds": time.time() - start_time
        }

    print(f"视频 URL: {video_url}")

    print(f"使用 VLM 模型 {model} 分析视频，采样帧率: {fps} fps...")

    # 调用 VLM 直接分析视频
    accident_data = call_vlm_analyze_video(
        video_url=video_url,
        model=model,
        base_url=base_url,
        api_key=api_key,
        fps=fps
    )

    # 构建结果
    intervals = []
    for item in accident_data:
        if isinstance(item, dict):
            road_structure = normalize_structure_type(item.get("road_structure_type"))
            road_structure_confidence = float(item.get("road_structure_confidence", 0) or 0)
            phase_traffic_flow = item.get("phase_traffic_flow")

            inferred_light_state, inferred_light_confidence = infer_traffic_light_state(phase_traffic_flow)
            model_light_state = str(item.get("traffic_light_state", "")).strip().lower()
            if model_light_state in {"red", "green", "yellow"}:
                final_light_state = model_light_state
                final_light_confidence = float(item.get("traffic_light_confidence", 0) or 0)
            else:
                final_light_state = inferred_light_state
                final_light_confidence = inferred_light_confidence

            intervals.append(AccidentInterval(
                start_time=item.get("start_time", 0),
                end_time=item.get("end_time", 0),
                confidence=item.get("confidence", 0),
                description=item.get("description", ""),
                road_structure_type=road_structure,
                road_structure_confidence=road_structure_confidence,
                phase_traffic_flow=phase_traffic_flow if isinstance(phase_traffic_flow, dict) else {},
                traffic_light_state=final_light_state,
                traffic_light_confidence=final_light_confidence
            ))

    result = DetectionResult(
        video_path=video_url,
        model=model,
        analyzed_frames=0,
        accident_intervals=intervals,
        total_accidents=len(intervals),
        processing_time_seconds=time.time() - start_time
    )

    return result.to_dict()


def save_result(result: dict, output_dir: str):
    """保存结果到文件"""
    os.makedirs(output_dir, exist_ok=True)

    # 保存 JSON
    json_path = os.path.join(output_dir, "accident_intervals.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 保存文本报告
    report_path = os.path.join(output_dir, "analysis_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("交通事故分析报告 (VLM 原视频分析)\n")
        f.write("=" * 50 + "\n\n")
        f.write(f"视频路径: {result['video_path']}\n")
        f.write(f"分析模型: {result['model']}\n")
        f.write(f"分析帧数: {result['analyzed_frames']}\n")
        f.write(f"处理时间: {result['processing_time_seconds']:.2f} 秒\n")
        f.write(f"\n共检测到 {result['total_accidents']} 起交通事故:\n\n")

        for i, interval in enumerate(result['accident_intervals'], 1):
            f.write(f"事故 {i}:\n")
            f.write(f"  时间区间: {interval['start_time']:.1f}s - {interval['end_time']:.1f}s\n")
            f.write(f"  置信度: {interval['confidence']:.2%}\n")
            f.write(f"  道路结构: {interval.get('road_structure_type', 'unknown')}\n")
            f.write(f"  道路结构置信度: {interval.get('road_structure_confidence', 0):.2%}\n")
            f.write(f"  相位车流量: {json.dumps(interval.get('phase_traffic_flow', {}), ensure_ascii=False)}\n")
            f.write(f"  红绿灯状态: {interval.get('traffic_light_state', 'unknown')}\n")
            f.write(f"  红绿灯置信度: {interval.get('traffic_light_confidence', 0):.2%}\n")
            f.write(f"  描述: {interval['description']}\n\n")

    return json_path, report_path


def main():
    parser = argparse.ArgumentParser(
        description="交通事故时间区间识别 (VLM 原视频分析版)"
    )
    parser.add_argument("video_url", help="视频 HTTP/HTTPS 地址")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="VLM 模型")
    parser.add_argument("--fps", type=float, default=2.0, help="视频采样帧率")
    parser.add_argument("--output-dir", help="输出目录")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="API Key")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API Base URL")

    args = parser.parse_args()
    raw_video_url = args.video_url

    # 推理必须使用用户原始链接，禁止在推理前做任何编码转换
    result = analyze_video(
        video_url=raw_video_url,
        model=args.model,
        fps=args.fps,
        api_key=args.api_key,
        base_url=args.base_url
    )

    # 确定输出目录
    if args.output_dir:
        output_dir = args.output_dir
    else:
        # 仅在输出目录命名时做 URL 解码，避免目录名出现 %E4%B8... 乱码
        video_name = get_video_name_from_url(raw_video_url)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_dir = str(DEFAULT_OUTPUT_BASE / video_name / timestamp)

    # 保存结果
    json_path, report_path = save_result(result, output_dir)

    # 打印结果
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n结果已保存到: {output_dir}")


if __name__ == "__main__":
    main()
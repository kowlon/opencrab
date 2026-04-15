#!/usr/bin/env python3
"""
交通事故时间区间识别脚本
使用多模态大模型分析视频帧图片，识别交通事故出现的时间区间
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from openai import OpenAI

# 默认 LLM 配置
DEFAULT_BASE_URL = "https://seeapi.zhidaoauto.com/v1"
DEFAULT_API_KEY = "sk-sc86Gw7sG3g9VMyDtIOezDF2uuCN45igR7zHH05iSAUerH4f"
DEFAULT_MODEL = "Qwen3.5-122B-A10B-FP8"
SKILL_NAME = "mogox-traffic-accident-detector"
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_BASE = WORKSPACE_ROOT / "skills_result" / SKILL_NAME

SYSTEM_PROMPT = """你是一个专业的交通事故分析专家。你的任务是分析交通画面，识别交通事故发生的时间区间。

## 输入信息
- 视频总时长：{duration} 秒
- 分析帧数：{frame_count} 帧
- 帧时间范围：{time_range}

## 每帧分析结果
以下是每帧的详细分析结果（已由模型逐帧分析）：
{frame_results}

## 输出要求
请根据上述每帧分析结果，综合判断交通事故发生的时间区间。

请以 JSON 格式返回分析结果，包含以下字段：
- accident_intervals: 事故区间数组，每个区间包含：
  - start_time: 事故开始时间（秒）
  - end_time: 事故结束时间（秒）
  - confidence: 置信度（0-1）
  - description: 事故描述

## 事故类型识别
请识别以下常见交通事故类型：
- 追尾事故：后车撞上前车
- 正面碰撞：车辆正面相撞
- 侧面碰撞：车辆侧面相撞（如变道、转弯）
- 倒车事故：倒车时发生碰撞
- 碰撞行人/非机动车
- 车辆侧翻
- 车辆起火燃烧

## 事故判定口径（非常重要）
1. 不仅要识别“碰撞发生瞬间”，还要识别“事故发生后的持续阶段”。
2. 若画面已出现事故后果（例如车辆异常停滞、明显占道、车头/车身异常姿态、残骸散落、周围车辆明显绕行避让），即使没有拍到碰撞瞬间，也应判定为事故持续中。
3. 对于疑似事故帧，宁可给出较低置信度并交由时序合并，不要直接忽略。

## 注意事项
1. 只返回确实发生事故的时间区间，不要误报
2. 如果视频中没有交通事故，返回空的 accident_intervals 数组
3. 置信度低于 0.5 的事故不要报告
4. 如果无法确定精确时间，给出合理的估计范围
5. 所有时间基于视频的第一帧为 0 秒计算
6. 综合考虑相邻帧的分析结果，识别出连续的事故区间
"""


@dataclass
class AccidentInterval:
    """事故时间区间"""
    start_time: float
    end_time: float
    confidence: float
    description: str


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


def encode_image_to_base64(image_path: str) -> str:
    """将图片编码为 base64 字符串"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def extract_frames_from_video(video_path: str, fps: float = 1.0, max_frames: int = 100,
                               output_dir: Optional[str] = None) -> tuple:
    """
    从视频中提取帧

    Returns:
        (frame_paths, timestamps): 帧文件路径列表和时间戳列表
    """
    video_path = os.path.abspath(video_path)

    # 获取视频时长
    probe_cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", video_path
    ]
    try:
        duration = float(subprocess.check_output(probe_cmd, stderr=subprocess.STDOUT).decode().strip())
    except Exception as e:
        print(f"获取视频时长失败: {e}", file=sys.stderr)
        duration = 0

    # 确定输出目录
    if output_dir is None:
        video_name = Path(video_path).stem
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_dir = str(DEFAULT_OUTPUT_BASE / video_name / timestamp / "frames")
    os.makedirs(output_dir, exist_ok=True)

    # 计算提取帧数
    num_frames = min(int(duration * fps), max_frames)
    if num_frames == 0:
        num_frames = 1

    # 使用 ffmpeg 提取帧
    # 注意：ffmpeg 的 fps 滤镜需要使用目标帧率，而不是 num_frames/duration
    # 这样可以确保帧之间的时间间隔均匀
    output_pattern = os.path.join(output_dir, "frame_%04d.jpg")
    target_fps = min(fps, max_frames / duration) if duration > 0 else fps  # 不超过 max_frames
    ffmpeg_cmd = [
        "ffmpeg", "-i", video_path,
        "-vf", f"fps={target_fps}",
        "-q:v", "2",
        "-frames:v", str(num_frames),
        output_pattern,
        "-y"
    ]

    try:
        subprocess.run(ffmpeg_cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"ffmpeg 提取帧失败: {e.stderr.decode() if e.stderr else str(e)}", file=sys.stderr)
        return [], []

    # 获取提取的帧文件
    frame_files = sorted(Path(output_dir).glob("frame_*.jpg"))
    frame_paths = [str(f) for f in frame_files]

    # 计算时间戳 - 修正公式，确保最后一帧对应视频结束时间
    # 帧时间间隔 = 总时长 / (帧数 - 1)，这样 57 帧可以覆盖 0~57.8 秒
    timestamps = []
    if duration > 0 and frame_files:
        if len(frame_files) > 1:
            frame_interval = duration / (len(frame_files) - 1)  # 修正：除以 (n-1) 而非 n
            timestamps = [i * frame_interval for i in range(len(frame_files))]
        else:
            timestamps = [0.0] * len(frame_files)

    return frame_paths, timestamps


def get_frames_from_directory(frames_dir: str) -> tuple:
    """
    从目录中获取帧文件

    Returns:
        (frame_paths, timestamps): 帧文件路径列表和时间戳列表
    """
    frames_dir = os.path.abspath(frames_dir)

    # 获取所有图片文件
    image_extensions = {".jpg", ".jpeg", ".png", ".bmp"}
    frame_files = []
    for ext in image_extensions:
        frame_files.extend(sorted(Path(frames_dir).glob(f"*{ext}")))
        frame_files.extend(sorted(Path(frames_dir).glob(f"*{ext.upper()}")))

    frame_paths = [str(f) for f in frame_files]

    # 尝试从文件名中提取时间戳
    timestamps = []
    for f in frame_files:
        name = f.stem
        # 尝试多种命名模式
        for pattern in ["ts_", "time_", "t_", "frame_"]:
            if pattern in name:
                try:
                    ts_str = name.split(pattern)[1].split("_")[0]
                    ts = float(ts_str)
                    timestamps.append(ts)
                    break
                except (ValueError, IndexError):
                    pass

    # 如果无法从文件名获取，使用序号估算
    if not timestamps and frame_paths:
        # 假设帧率 1fps
        timestamps = list(range(len(frame_paths)))

    return frame_paths, timestamps


def call_llm_analyze_frames(frame_paths: list, timestamps: list, model: str,
                             base_url: str, api_key: str,
                             frame_results: list = None) -> list:
    """
    调用 LLM 分析帧图片

    Args:
        frame_paths: 帧文件路径列表
        timestamps: 时间戳列表
        model: 使用的模型
        base_url: API Base URL
        api_key: API Key
        frame_results: 可选，逐帧分析结果列表

    Returns:
        accident_intervals: 事故区间列表
    """
    client = OpenAI(
        api_key=api_key,
        base_url=base_url
    )

    # 计算视频时长
    duration = timestamps[-1] - timestamps[0] if len(timestamps) > 1 else 0
    time_range = f"{timestamps[0]:.1f} 秒到 {timestamps[-1]:.1f} 秒"

    # 准备逐帧分析结果摘要
    frame_results_text = ""
    if frame_results and len(frame_results) > 0:
        # 选取关键帧的结果（包含事故的或高置信度的）
        key_frames = []
        for i, fr in enumerate(frame_results):
            ts = fr.get("timestamp", timestamps[i] if i < len(timestamps) else i)
            has_acc = fr.get("has_accident", False)
            conf = fr.get("confidence", 0.0)
            desc = fr.get("description", "")
            # 包含事故的或置信度 > 0.3 的帧
            if has_acc or conf > 0.3:
                key_frames.append(f"- 第 {i+1} 帧 (时间 {ts:.1f}s): has_accident={has_acc}, confidence={conf:.2f}, description=\"{desc}\"")
        
        if key_frames:
            frame_results_text = "\n".join(key_frames)
        else:
            # 如果没有关键帧，列出所有帧的简要信息
            frame_results_text = f"(共 {len(frame_results)} 帧，所有帧均未检测到事故迹象)"
    else:
        frame_results_text = "(无逐帧分析结果，将基于图片直接判断)"

    # 填充 SYSTEM_PROMPT 模板
    prompt = SYSTEM_PROMPT.format(
        duration=duration,
        frame_count=len(frame_paths),
        time_range=time_range,
        frame_results=frame_results_text
    )

    # 构建消息内容 - 如果有逐帧结果，主要传文字描述；否则传图片
    if frame_results and len(frame_results) > 0 and frame_results_text.startswith("-"):
        # 有逐帧结果时，主要依赖文字分析
        content = [
            {
                "type": "text",
                "text": f"请根据以下逐帧分析结果，综合判断交通事故发生的时间区间。\n\n"
                        f"视频总时长：{duration:.1f} 秒\n"
                        f"分析帧数：{len(frame_paths)} 帧\n"
                        f"帧时间范围：{time_range}\n\n"
                        f"逐帧分析结果：\n{frame_results_text}\n\n"
                        f"请综合以上信息，识别出连续的事故区间，以 JSON 格式返回结果。"
            }
        ]
        # 辅以少量关键图片（最多6张）
        max_images = 6
        step = max(1, len(frame_paths) // max_images)
        selected_indices = list(range(0, len(frame_paths), step))[:max_images]
        for idx in selected_indices:
            img_path = frame_paths[idx]
            if os.path.exists(img_path):
                b64_img = encode_image_to_base64(img_path)
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}
                })
    else:
        # 无逐帧结果时，回退到原来的逻辑
        content = [
            {
                "type": "text",
                "text": "请分析以下交通画面帧序列，识别交通事故发生的时间区间。\n\n"
                        f"共有 {len(frame_paths)} 帧图片，时间范围从 {timestamps[0]:.1f} 秒到 {timestamps[-1]:.1f} 秒。\n\n"
                        "请仔细观察每帧交通画面中的车辆行为、碰撞痕迹、事故现场等细节。"
            }
        ]
        # 添加图片（限制数量避免超出上下文）
        max_images = 20
        step = max(1, len(frame_paths) // max_images)
        selected_indices = list(range(0, len(frame_paths), step))[:max_images]
        for idx in selected_indices:
            img_path = frame_paths[idx]
            if os.path.exists(img_path):
                b64_img = encode_image_to_base64(img_path)
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}
                })

    # 调用 LLM
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": content}
            ],
            temperature=0.1,
            max_tokens=2048,
            extra_body={
                "chat_template_kwargs": {"enable_thinking": False}
            }
        )

        result_text = response.choices[0].message.content

        # 提取 JSON
        return parse_llm_response(result_text)

    except Exception as e:
        print(f"LLM 调用失败: {e}", file=sys.stderr)
        return []


def parse_single_frame_response(response_text: str, timestamp: float, frame_path: str) -> dict:
    """解析单帧推理结果，统一为结构化字典。"""
    default_result = {
        "frame_path": frame_path,
        "timestamp": timestamp,
        "has_accident": False,
        "confidence": 0.0,
        "description": "模型未返回可解析结果"
    }

    payload = try_parse_first_json(response_text)
    if not isinstance(payload, dict):
        default_result["description"] = response_text.strip()[:200] or default_result["description"]
        return default_result

    try:
        data = payload
        return {
            "frame_path": frame_path,
            "timestamp": timestamp,
            "has_accident": bool(data.get("has_accident", False)),
            "confidence": float(data.get("confidence", 0.0)),
            "description": normalize_frame_description(str(data.get("description", "")))
        }
    except Exception:
        default_result["description"] = response_text.strip()[:200] or default_result["description"]
        return default_result


def parse_batch_frame_response(response_text: str, batch_items: list) -> list:
    """解析批量多帧推理响应，返回与 batch_items 对齐的结构化结果列表。"""
    default_results = [
        {
            "frame_path": item["frame_path"],
            "timestamp": item["timestamp"],
            "has_accident": False,
            "confidence": 0.0,
            "description": "模型未返回可解析结果"
        }
        for item in batch_items
    ]

    payload = try_parse_first_json(response_text)
    if payload is None:
        return default_results

    if isinstance(payload, dict):
        candidates = payload.get("results", [])
    elif isinstance(payload, list):
        candidates = payload
    else:
        candidates = []

    for i, item in enumerate(batch_items):
        matched = None
        for c in candidates:
            if isinstance(c, dict) and c.get("frame_index") == i:
                matched = c
                break
        if matched is None and i < len(candidates) and isinstance(candidates[i], dict):
            matched = candidates[i]

        if isinstance(matched, dict):
            default_results[i] = {
                "frame_path": item["frame_path"],
                "timestamp": item["timestamp"],
                "has_accident": bool(matched.get("has_accident", False)),
                "confidence": float(matched.get("confidence", 0.0)),
                "description": normalize_frame_description(str(matched.get("description", "")))
            }

    return default_results


def try_parse_first_json(text: str):
    """
    从模型输出中尽量稳健地提取第一个合法 JSON（对象或数组）。
    支持包含前后说明文本、Markdown 代码块等情况。
    """
    if not text:
        return None

    # 先尝试提取 markdown json 代码块
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    candidates = [fence_match.group(1)] if fence_match else []
    candidates.append(text.strip())

    decoder = json.JSONDecoder()
    for candidate in candidates:
        if not candidate:
            continue
        for start_idx, ch in enumerate(candidate):
            if ch not in "{[":
                continue
            try:
                data, _ = decoder.raw_decode(candidate[start_idx:])
                return data
            except Exception:
                continue
    return None


def normalize_frame_description(description: str) -> str:
    """
    规整逐帧 description：仅保留客观事实，避免废话/主观推测。
    """
    text = (description or "").strip()
    if not text:
        return "画面信息不足，未见明确交通事故。"

    # 去除常见冗词和主观化表达
    replacements = [
        ("根据画面", ""),
        ("可以看到", ""),
        ("初步判断", ""),
        ("疑似", ""),
        ("可能", ""),
        ("看起来", ""),
        ("综合来看", ""),
    ]
    for old, new in replacements:
        text = text.replace(old, new)

    # 限制为简洁单句，避免过长解释
    text = " ".join(text.split())
    if len(text) > 80:
        text = text[:80].rstrip("，,。.;； ") + "。"

    return text or "画面信息不足，未见明确交通事故。"


def analyze_single_frame_with_retry(client, model: str, frame_path: str, timestamp: float, max_retries: int = 2) -> dict:
    """对单帧做补偿推理，减少批量解析失败导致的空结果。"""
    base_result = {
        "frame_path": frame_path,
        "timestamp": timestamp,
        "has_accident": False,
        "confidence": 0.0,
        "description": "单帧补偿推理失败"
    }
    if not os.path.exists(frame_path):
        base_result["description"] = "帧文件不存在"
        return base_result

    b64_img = encode_image_to_base64(frame_path)
    prompt_text = (
        "请仅根据这1帧图像判断是否存在交通事故相关状态（碰撞瞬间或事故后持续状态）。"
        "description 只允许写客观画面事实与是否事故，不允许推测、原因分析、建议和套话。"
        "示例：'两车接触后停在车道内，判定有交通事故'、'车辆正常行驶，判定无交通事故'。"
        "你必须且只能返回一个 JSON 对象，不得输出任何额外文字。"
        "格式严格为："
        "{\"has_accident\":false,\"confidence\":0.0,\"description\":\"...\"}"
    )

    for _ in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是交通事故分析专家，必须严格输出 JSON 对象。"},
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                    ]}
                ],
                temperature=0.0,
                max_tokens=300,
                extra_body={
                    "chat_template_kwargs": {"enable_thinking": False}
                }
            )
            result_text = response.choices[0].message.content
            parsed = parse_single_frame_response(result_text, timestamp, frame_path)
            if parsed.get("description") != "模型未返回可解析结果":
                return parsed
        except Exception as e:
            base_result["description"] = f"单帧补偿推理失败: {e}"

    return base_result


def analyze_frame_by_frame(frame_paths: list, timestamps: list, model: str,
                           base_url: str, api_key: str,
                           batch_size: int = 8) -> list:
    """
    对每一帧做结构化推理（批量多帧同次请求），返回逐帧结果。
    结果用于落盘保存，便于与帧文件一一对应排查。
    """
    client = OpenAI(api_key=api_key, base_url=base_url)
    results = []
    batch_size = max(1, int(batch_size))

    for start in range(0, len(frame_paths), batch_size):
        batch_paths = frame_paths[start:start + batch_size]
        batch_items = []
        content = []

        for i, frame_path in enumerate(batch_paths):
            global_idx = start + i
            ts = float(timestamps[global_idx]) if global_idx < len(timestamps) else float(global_idx)
            batch_items.append({"frame_path": frame_path, "timestamp": ts})

        content.append({
            "type": "text",
            "text": (
                "请分析下面这组交通画面帧，识别每一帧是否存在“交通事故相关状态”。"
                "事故相关状态包含两类："
                "A) 碰撞发生瞬间；"
                "B) 事故发生后的持续状态（车辆异常停滞/占道、明显受损姿态、碎片散落、其他车辆绕行避让等）。"
                "即使未看到碰撞瞬间，只要可见事故后果，也要将该帧标记为 has_accident=true。"
                "每一帧的 description 只描述客观可见事实和事故结论，禁止废话和主观推测。"
                "推荐短句：'车辆正常通行，判定无交通事故' 或 '两车碰撞后停滞占道，判定有交通事故'。"
                "你必须只返回 JSON，且只能输出一个 JSON 对象，不得包含任何解释文字或 Markdown。"
                "严格格式："
                "{\"results\":[{\"frame_index\":0,\"has_accident\":false,\"confidence\":0.0,\"description\":\"...\"}]}"
                "其中 frame_index 对应本组图片顺序（从 0 开始），confidence 取值 0~1。"
            )
        })

        valid = True
        for item in batch_items:
            if not os.path.exists(item["frame_path"]):
                valid = False
                break
            b64_img = encode_image_to_base64(item["frame_path"])
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}
            })

        if not valid:
            for item in batch_items:
                results.append({
                    "frame_path": item["frame_path"],
                    "timestamp": item["timestamp"],
                    "has_accident": False,
                    "confidence": 0.0,
                    "description": "帧文件不存在"
                })
            continue

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是交通事故分析专家，专注交通画面中的事故识别。"},
                    {"role": "user", "content": content}
                ],
                temperature=0.1,
                max_tokens=800,
                extra_body={
                    "chat_template_kwargs": {"enable_thinking": False}
                }
            )
            result_text = response.choices[0].message.content
            batch_results = parse_batch_frame_response(result_text, batch_items)

            # 对批量中未解析出的帧逐帧补偿推理，避免整批返回不可解析结果
            for idx, item in enumerate(batch_items):
                if idx >= len(batch_results):
                    batch_results.append({
                        "frame_path": item["frame_path"],
                        "timestamp": item["timestamp"],
                        "has_accident": False,
                        "confidence": 0.0,
                        "description": "模型未返回可解析结果"
                    })
                if batch_results[idx].get("description") == "模型未返回可解析结果":
                    batch_results[idx] = analyze_single_frame_with_retry(
                        client=client,
                        model=model,
                        frame_path=item["frame_path"],
                        timestamp=item["timestamp"],
                        max_retries=2
                    )

            results.extend(batch_results)
        except Exception as e:
            for item in batch_items:
                # 批量请求失败时退化为逐帧补偿推理
                fallback = analyze_single_frame_with_retry(
                    client=client,
                    model=model,
                    frame_path=item["frame_path"],
                    timestamp=item["timestamp"],
                    max_retries=2
                )
                if fallback.get("description") == "单帧补偿推理失败":
                    fallback["description"] = f"批量帧推理失败后补偿失败: {e}"
                results.append(fallback)

    return results


def save_frame_inference_results(frame_results: list, frames_dir: str) -> str:
    """将逐帧推理结果按“每帧一个 JSON”保存到帧目录。"""
    os.makedirs(frames_dir, exist_ok=True)
    for item in frame_results:
        frame_path = item.get("frame_path", "")
        frame_stem = Path(frame_path).stem if frame_path else ""
        if not frame_stem:
            frame_stem = f"frame_{int(item.get('timestamp', 0)):04d}"
        output_path = os.path.join(frames_dir, f"{frame_stem}.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(item, f, ensure_ascii=False, indent=2)
    return frames_dir


def _build_interval_description(accident_frame_indices: list, frame_results: list) -> str:
    """
    基于区间内逐帧描述，生成“事件情况 + 主体信息”的简要说明。
    """
    if not accident_frame_indices:
        return "检测到交通事故，涉及道路通行异常。"

    ignore_desc = {
        "模型未返回可解析结果",
        "帧文件不存在",
        "单帧补偿推理失败"
    }
    descriptions = []
    for idx in accident_frame_indices:
        if idx < 0 or idx >= len(frame_results):
            continue
        desc = str(frame_results[idx].get("description", "")).strip()
        if not desc or desc in ignore_desc:
            continue
        if desc.startswith("批量帧推理失败") or desc.startswith("单帧补偿推理失败"):
            continue
        descriptions.append(desc)

    if not descriptions:
        return f"检测到交通事故，持续约 {len(accident_frame_indices)} 帧，主体疑似为机动车。"

    # 通过关键词聚合主体和事件，尽量避免冗长描述
    subject_candidates = [
        ("多车", ["多车", "多辆车", "多车连撞"]),
        ("小汽车", ["小汽车", "轿车", "私家车"]),
        ("货车", ["货车", "卡车", "工程车"]),
        ("非机动车", ["电动车", "自行车", "非机动车"]),
        ("行人", ["行人", "路人"]),
    ]
    event_candidates = [
        ("追尾", ["追尾"]),
        ("侧向碰撞", ["侧撞", "侧面碰撞", "刮擦"]),
        ("正面碰撞", ["正面碰撞", "迎面相撞"]),
        ("停滞占道", ["停滞", "占道", "停车不动", "堵塞"]),
        ("疑似受损", ["受损", "变形", "碎片", "散落"]),
    ]

    def _pick_best_label(candidates):
        best_label = None
        best_score = 0
        text_blob = " ".join(descriptions)
        for label, keys in candidates:
            score = sum(text_blob.count(k) for k in keys)
            if score > best_score:
                best_score = score
                best_label = label
        return best_label

    subject = _pick_best_label(subject_candidates) or "机动车"
    event = _pick_best_label(event_candidates) or "发生碰撞或事故后停滞"
    sample_desc = descriptions[0][:40]

    return (
        f"{subject}疑似{event}，事故后存在持续异常通行状态，"
        f"持续约 {len(accident_frame_indices)} 帧（线索：{sample_desc}）。"
    )


def fix_time_intervals(accident_intervals: list, frame_results: list, timestamps: list) -> list:
    """
    根据实际的帧-时间映射修正事故区间时间戳
    
    LLM 可能返回错误的帧号/时间对应关系，这个函数根据实际逐帧分析结果来修正。
    """
    if not accident_intervals or not frame_results:
        return accident_intervals
    
    # 构建 frame_index -> timestamp 的映射
    # frame_results 已经包含正确的 timestamp
    frame_time_map = {}
    for i, fr in enumerate(frame_results):
        ts = fr.get("timestamp", timestamps[i] if i < len(timestamps) else float(i))
        frame_time_map[i] = ts
    
    # 找出所有标记为 has_accident=True 的帧及其时间戳
    accident_frames = []
    for i, fr in enumerate(frame_results):
        if fr.get("has_accident", False):
            ts = frame_time_map.get(i, timestamps[i] if i < len(timestamps) else float(i))
            conf = fr.get("confidence", 0.0)
            accident_frames.append({
                "frame_index": i,
                "timestamp": ts,
                "confidence": conf
            })
    
    if not accident_frames:
        return []
    
    # 按时间排序
    accident_frames.sort(key=lambda x: x["timestamp"])
    
    # 合并连续的事故帧为区间
    merged_intervals = []
    if accident_frames:
        current_start = accident_frames[0]["timestamp"]
        current_end = accident_frames[0]["timestamp"]
        current_conf = accident_frames[0]["confidence"]
        current_indices = [accident_frames[0]["frame_index"]]
        
        for i in range(1, len(accident_frames)):
            ts = accident_frames[i]["timestamp"]
            conf = accident_frames[i]["confidence"]
            frame_idx = accident_frames[i]["frame_index"]
            
            # 如果当前帧与前一个帧时间间隔小于 3 秒，认为是连续事故
            if ts - current_end < 3.0:
                current_end = ts
                current_conf = max(current_conf, conf)
                current_indices.append(frame_idx)
            else:
                # 保存当前区间，开始新区间
                merged_intervals.append({
                    "start_time": current_start,
                    "end_time": current_end,
                    "confidence": current_conf,
                    "description": _build_interval_description(current_indices, frame_results)
                })
                current_start = ts
                current_end = ts
                current_conf = conf
                current_indices = [frame_idx]
        
        # 保存最后一个区间
        merged_intervals.append({
            "start_time": current_start,
            "end_time": current_end,
            "confidence": current_conf,
            "description": _build_interval_description(current_indices, frame_results)
        })
    
    return merged_intervals


def parse_llm_response(response_text: str) -> list:
    """
    解析 LLM 返回的 JSON 响应
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
                interval = AccidentInterval(
                    start_time=float(match.group(1)),
                    end_time=float(match.group(2)),
                    confidence=0.8,
                    description=line.strip()[:200]
                )
                intervals.append(asdict(interval))
            except ValueError:
                pass

    return intervals


def resolve_task_output_dir(input_path: str, output_dir: Optional[str] = None) -> str:
    """
    解析本次任务输出目录。
    - 传入 output_dir 时直接使用
    - 未传入时，优先复用同视频下尚未生成 accident_intervals.json 的时间戳目录
    """
    if output_dir:
        return os.path.abspath(output_dir)

    video_name = Path(input_path).stem
    video_base_dir = DEFAULT_OUTPUT_BASE / video_name
    os.makedirs(video_base_dir, exist_ok=True)

    for task_dir in sorted(video_base_dir.iterdir(), reverse=True):
        if task_dir.is_dir() and not (task_dir / "accident_intervals.json").exists():
            return str(task_dir)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return str(video_base_dir / timestamp)


def detect_accidents(input_path: str, model: str = DEFAULT_MODEL,
                     fps: float = 1.0, max_frames: int = 100,
                     batch_size: int = 8,
                     output_dir: Optional[str] = None,
                     api_key: str = DEFAULT_API_KEY,
                     base_url: str = DEFAULT_BASE_URL) -> dict:
    """
    检测交通事故时间区间

    Args:
        input_path: 视频文件路径或帧目录路径
        model: 使用的模型
        fps: 抽帧帧率（仅对视频输入有效）
        max_frames: 最大分析帧数
        batch_size: 单次多帧推理数量
        output_dir: 输出目录
        api_key: API Key
        base_url: API Base URL

    Returns:
        检测结果字典
    """
    start_time = time.time()
    input_path = os.path.abspath(input_path)
    output_dir = resolve_task_output_dir(input_path, output_dir)

    # 判断输入类型
    if os.path.isfile(input_path):
        # 视频文件，提取帧
        frames_output_dir = os.path.join(output_dir, "frames")
        frame_paths, timestamps = extract_frames_from_video(
            input_path, fps, max_frames, frames_output_dir
        )
        if not frame_paths:
            print("警告: 未能提取到帧图片", file=sys.stderr)
            return {
                "video_path": input_path,
                "model": model,
                "analyzed_frames": 0,
                "accident_intervals": [],
                "total_accidents": 0,
                "processing_time_seconds": time.time() - start_time
            }
    elif os.path.isdir(input_path):
        # 帧目录
        frame_paths, timestamps = get_frames_from_directory(input_path)
        frames_output_dir = input_path
        if not frame_paths:
            print("警告: 目录中没有找到帧图片", file=sys.stderr)
            return {
                "video_path": input_path,
                "model": model,
                "analyzed_frames": 0,
                "accident_intervals": [],
                "total_accidents": 0,
                "processing_time_seconds": time.time() - start_time
            }
    else:
        print(f"错误: 输入路径不存在: {input_path}", file=sys.stderr)
        return {
            "video_path": input_path,
            "model": model,
            "analyzed_frames": 0,
            "accident_intervals": [],
            "total_accidents": 0,
            "processing_time_seconds": time.time() - start_time
        }

    # 逐帧推理并落盘到帧目录
    frame_results = analyze_frame_by_frame(
        frame_paths=frame_paths,
        timestamps=timestamps,
        model=model,
        base_url=base_url,
        api_key=api_key,
        batch_size=batch_size
    )
    save_frame_inference_results(frame_results, frames_output_dir)

    # 分析帧
    accident_data = call_llm_analyze_frames(
        frame_paths, timestamps, model, base_url, api_key, frame_results=frame_results
    )
    
    # 使用实际帧时间戳修正事故区间
    accident_data = fix_time_intervals(accident_data, frame_results, timestamps)

    # 构建结果
    result = DetectionResult(
        video_path=input_path,
        model=model,
        analyzed_frames=len(frame_paths),
        accident_intervals=[AccidentInterval(**item) for item in accident_data]
            if accident_data and isinstance(accident_data[0], dict) else accident_data,
        total_accidents=len(accident_data),
        processing_time_seconds=time.time() - start_time
    )
    return result.to_dict()


def analyze_frames(frame_paths: list, timestamps: list, model: str = DEFAULT_MODEL,
                   api_key: str = DEFAULT_API_KEY,
                   base_url: str = DEFAULT_BASE_URL) -> list:
    """
    直接分析帧列表

    Args:
        frame_paths: 帧文件路径列表
        timestamps: 时间戳列表
        model: 使用的模型
        api_key: API Key
        base_url: API Base URL

    Returns:
        事故区间列表
    """
    accident_data = call_llm_analyze_frames(
        frame_paths, timestamps, model, base_url, api_key
    )
    return accident_data


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
        f.write("交通事故分析报告\n")
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
            f.write(f"  描述: {interval['description']}\n\n")

    return json_path, report_path


def main():
    parser = argparse.ArgumentParser(description="交通事故时间区间识别")
    parser.add_argument("input", help="视频文件路径或帧目录路径")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="使用的模型")
    parser.add_argument("--fps", type=float, default=1.0, help="抽帧帧率（视频输入时）")
    parser.add_argument("--max-frames", type=int, default=100, help="最大分析帧数")
    parser.add_argument("--batch-size", type=int, default=8, help="单次多帧推理数量")
    parser.add_argument("--output-dir", help="输出目录")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="API Key")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API Base URL")

    args = parser.parse_args()
    output_dir = resolve_task_output_dir(args.input, args.output_dir)

    # 检测
    result = detect_accidents(
        input_path=args.input,
        model=args.model,
        fps=args.fps,
        max_frames=args.max_frames,
        batch_size=args.batch_size,
        output_dir=output_dir,
        api_key=args.api_key,
        base_url=args.base_url
    )

    # 保存结果
    json_path, report_path = save_result(result, output_dir)

    # 打印结果
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n结果已保存到: {output_dir}")


if __name__ == "__main__":
    main()

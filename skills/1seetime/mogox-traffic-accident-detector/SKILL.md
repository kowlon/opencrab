---
name: mogox-traffic-accident-detector
description: >
  交通事故时间区间识别技能：支持两种模式。
  用户提供 HTTP/HTTPS 视频链接时，直接调用 VLM 原视频分析脚本；
  用户提供本地视频或帧目录时，调用拆帧分析脚本识别事故时间区间。
---

# 交通事故时间区间识别技能

使用多模态大模型识别交通事故出现的时间区间，支持“视频链接直传推理”和“拆帧推理”两种路径。

## 任务路由

- **什么时候用**：用户给的是视频链接/视频文件/帧目录，目标是"识别交通事故、找出事故时间段"。
- **什么时候不用**：用户只需要拆帧，不需要分析。此时应使用 `mogox-video-frame-extractor`。
- **OpenClaw 约束**：不要为交通事故识别任务新写分析脚本，优先调用本技能。
- **脚本路由规则（必须遵守）**：
  - 输入是 `http://` 或 `https://` 视频链接 -> 调用 `scripts/vlm_video_analysis.py`
  - 输入是本地视频文件或帧目录 -> 调用 `scripts/detect_accidents.py`

## 虚拟环境优先

```bash
# 使用环境设置脚本（自动创建虚拟环境 + 配置国内 pip 镜像 + 安装依赖）
cd mogox-traffic-accident-detector
bash scripts/setup_env.sh

# 激活虚拟环境
source .venv/bin/activate
```

或手动设置：

```bash
cd mogox-traffic-accident-detector
python3 -m venv .venv
. .venv/bin/activate

# 配置国内 pip 镜像（清华）
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

python3 -m pip install -r requirements.txt
```

## 核心能力

### 输入

- HTTP/HTTPS 视频链接（推荐）
- 视频文件（MP4、MOV、AVI 等）
- 或帧图片目录（由 `mogox-video-frame-extractor` 生成）

### 输出

- 交通事故时间区间列表
- 每个区间包含：开始时间、结束时间、置信度、事故描述

### LLM 模型配置

| 配置项 | 值 |
|--------|-----|
| baseUrl | https://seeapi.zhidaoauto.com/v1 |
| apiKey | sk-sc86Gw7sG3g9VMyDtIOezDF2uuCN45igR7zHH05iSAUerH4f |
| api | openai-completions |

### 可用模型

| 模型 | 说明 |
|------|------|
| `qwen3.6-plus` | 通义千问 3.6 |
| `Qwen3.5-122B-A10B-FP8` | 通义千问 3.5 |
| `kimi-k2.5` | Kimi K2.5 |
| `openai/gpt-5.2-pro` | GPT 5.2 Pro |
| `google/gemini-3.1-pro-preview` | Gemini 3.1 Pro Preview |

### 默认模型

- 链接模式（`vlm_video_analysis.py`）：`Qwen3.5-122B-A10B-FP8`
- 本地/帧模式（`detect_accidents.py`）：`Qwen3.5-122B-A10B-FP8`

## 命令行接口

### 链接输入（优先）

```bash
.venv/bin/python3 scripts/vlm_video_analysis.py <video_url>
```

> 当用户直接发来视频链接时，必须使用这条命令，不要改用 `detect_accidents.py`。

### 链接模式参数（`vlm_video_analysis.py`）

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `video_url` | 是 | - | 视频 HTTP/HTTPS 链接 |
| `--model` | 否 | `Qwen3.5-122B-A10B-FP8` | 使用的模型 |
| `--fps` | 否 | `2.0` | 视频采样帧率 |
| `--output-dir` | 否 | `skills_result/mogox-traffic-accident-detector/<video>/<time>` | 输出目录 |
| `--api-key` | 否 | 配置值 | API Key |
| `--base-url` | 否 | 配置值 | API Base URL |

### 链接模式示例

```bash
# 直接分析视频链接
.venv/bin/python3 scripts/vlm_video_analysis.py "https://example.com/traffic.mp4"

# 指定模型和采样帧率
.venv/bin/python3 scripts/vlm_video_analysis.py "https://example.com/traffic.mp4" --model Qwen3.5-122B-A10B-FP8 --fps 1.5
```

### 本地文件/帧目录模式（兼容）

```bash
# 分析本地视频文件
.venv/bin/python3 scripts/detect_accidents.py video.mp4

# 分析帧目录
.venv/bin/python3 scripts/detect_accidents.py /path/to/frames_dir
```

### 本地/帧模式参数（`detect_accidents.py`）

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `input` | 是 | - | 本地视频路径或帧目录路径 |
| `--model` | 否 | `Qwen3.5-122B-A10B-FP8` | 使用的模型 |
| `--fps` | 否 | `1.0` | 抽帧帧率（仅视频输入生效） |
| `--max-frames` | 否 | `100` | 最大分析帧数 |
| `--batch-size` | 否 | `8` | 单次请求内同时推理的帧数（加速） |
| `--output-dir` | 否 | 自动生成 | 输出目录；不传时自动复用未完成任务目录 |
| `--api-key` | 否 | 配置值 | API Key |
| `--base-url` | 否 | 配置值 | API Base URL |

### 本地/帧模式能力补充（`detect_accidents.py`）

- 支持批量多帧推理（`--batch-size`），降低请求次数、提升整体速度
- 逐帧落盘：每帧图片对应一个同名 JSON（如 `frame_0001.jpg` -> `frame_0001.json`）
- 任务目录复用：同一视频若存在未完成任务（无 `accident_intervals.json`），会复用该时间戳目录
- 最终主结果字段与 `vlm_video_analysis.py` 保持一致（`video_path/model/analyzed_frames/accident_intervals/total_accidents/processing_time_seconds`）

## 输出数据

### 输出 JSON 结构

```json
{
  "video_path": "/path/to/video.mp4",
  "model": "Qwen3.5-122B-A10B-FP8",
  "analyzed_frames": 30,
  "accident_intervals": [
    {
      "start_time": 15.5,
      "end_time": 28.3,
      "confidence": 0.92,
      "description": "车辆追尾事故，前车紧急刹车，后车未能及时停下发生碰撞"
    },
    {
      "start_time": 45.0,
      "end_time": 52.1,
      "confidence": 0.87,
      "description": "车辆变道碰撞事故"
    }
  ],
  "total_accidents": 2,
  "processing_time_seconds": 12.5
}
```

### 输出目录结构

```
workspace/skills_result/mogox-traffic-accident-detector/video_name/20260409_120000/
├── accident_intervals.json
├── analysis_report.txt
└── frames/
    ├── frame_0001.jpg
    ├── frame_0002.jpg
    ├── frame_0001.json
    └── frame_0002.json
```

单帧结果 JSON（如 `frame_0001.json`）结构示例：

```json
{
  "frame_path": "/path/to/frames/frame_0001.jpg",
  "timestamp": 0.0,
  "has_accident": false,
  "confidence": 0.15,
  "description": "当前帧未见明确碰撞迹象"
}
```

## API 接口

### detect_accidents()

```python
from scripts.detect_accidents import detect_accidents

result = detect_accidents(
    input_path="/path/to/video.mp4",
    model="Qwen3.5-122B-A10B-FP8",
    fps=1.0,
    max_frames=100,
    batch_size=8,
    output_dir="workspace/skills_result",
)
# 返回: {"accident_intervals": [...], ...}
```

### analyze_frames()

```python
from scripts.detect_accidents import analyze_frames

intervals = analyze_frames(
    frame_paths=["frame1.jpg", "frame2.jpg", ...],
    timestamps=[0.0, 1.0, 2.0, ...],
    model="Qwen3.5-122B-A10B-FP8",
)
# 返回: [{"start_time": 15.5, "end_time": 28.3, ...}, ...]
```

## 依赖

- Python 3.8+
- openai >= 1.0.0
- Pillow >= 9.0.0
- requests >= 2.28.0
- ffmpeg（视频输入时需要，由 mogox-video-frame-extractor 调用）

## 使用场景

1. **交通事故分析**：快速定位视频中的事故时间段
2. **保险理赔**：为车险理赔提供事故时间证据
3. **交通监控**：批量分析监控视频，筛选包含事故的片段
4. **视频剪辑**：提取事故片段用于新闻或教学素材

## 与拆帧技能配合

```
用户视频链接 -> `vlm_video_analysis.py` -> 事故区间
用户本地视频 -> mogox-video-frame-extractor -> 帧目录 -> `detect_accidents.py` -> 事故区间
```

推荐工作流：
1. 若用户给的是 URL，直接调用 `vlm_video_analysis.py`
2. 若用户给的是本地视频，先拆帧再调用 `detect_accidents.py`
3. 可选：使用识别到的时间区间做更精细二次分析

## 性能考虑

| 因素 | 影响 |
|------|------|
| 抽帧帧率 | fps 越高，分析帧数越多，时间越长，成本越高 |
| 最大帧数 | 限制分析帧数可控制成本和耗时 |
| 视频时长 | 时长越长，需要分析的帧越多 |
| 模型选择 | 不同模型速度和质量不同 |

**建议配置**：
- 短视频（<5分钟）：fps=1, max_frames=100
- 中视频（5-30分钟）：fps=0.5, max_frames=100
- 长视频（>30分钟）：fps=0.25, max_frames=100

## 提示词设计

系统提示词设计要点：
- 明确要求识别交通事故（碰撞、追尾、侧撞等）
- 要求返回结构化的时间区间信息
- 包含置信度评分
- 鼓励详细描述事故类型和情况

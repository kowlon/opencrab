---
name: seeagent/skills@feature-extraction
description: 串联真实的相机预处理、轮询状态与任务结果查询。输入相机列表+英文特征关键词进行查找时调用。
license: MIT
metadata:
  author: seeagent
  version: "2.0.0"
---

# Feature Extraction (1seetime)

## When to Use

- 用户提供"相机 ID 列表"、"时间范围"和"特征描述"，希望获取包含对应特征的图像帧
- 需要执行严格的任务流：创建预处理任务 -> 轮询状态直至 completed -> 查询任务结果

## Inputs

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| camera_ids | array[string] | 是 | 待处理相机 ID 列表 |
| features | string | 是 | 逗号分隔的**英文**特征关键词（如 `"yellow van,pedestrian"`）。如果用户提供中文描述，需先翻译为英文 |
| start_time | string | 是 | 截帧开始时间 (ISO8601)，时间范围不能超过 1 天 |
| end_time | string | 是 | 截帧结束时间 (ISO8601)，时间范围不能超过 1 天 |
| base_url | string | 否 | 服务地址，默认 `https://search.zhidaozhixing.com` |
| poll_interval | number | 否 | 轮询间隔秒数，默认 `5` |
| timeout | number | 否 | 轮询超时秒数，默认 `3600`（1小时） |
| source | number | 否 | 数据源：1=toc, 2=治理（默认不传） |

## Orchestration Flow

**以下流程已封装在脚本 `run_feature_extraction.py` 中，直接通过 `run_skill_script` 一次调用即可完成全部步骤，无需手动逐步调用 API：**

1. 创建预处理任务：`POST /api/v1/cameras/preprocess`（传入 `c_ids`, `features`, 毫秒时间戳）
2. 轮询任务状态：`GET /api/v1/cameras/preprocess/{task_id}?detail=true`，直至 `completed`（轮询过程中可获取部分结果）
3. 查询任务结果：`GET /api/v1/task/results/{task_id}`（如轮询已获取完整数据则跳过）
4. 将按相机嵌套的结果展平为扁平数组，并归一化字段名

## Script

脚本位于 `scripts/run_feature_extraction.py`，无额外第三方依赖，使用标准库实现。

**使用 `run_shell` 工具执行脚本：**

（注：如果需要处理时间较长，可以在 `run_shell` 的参数中传入 `"timeout": 3600`，放宽底层执行超时限制）

```bash
python scripts/run_feature_extraction.py --camera-ids cam_001,cam_002 --features "yellow van,pedestrian" --start-time "2024-01-15T08:30:00" --end-time "2024-01-15T09:30:00"
```

## Output

脚本会把完整结果直接打印到 **stdout**（BP 引擎从 tool_results 自动提取）。

> **重要**：调用此脚本后，你的 agent 文本回复应当简短总结（50 字以内），
> 例如 "已完成，共找到 10 个相机 84 帧匹配白色货车"。**不要** 把 stdout 的 JSON
> 再复制到文本回复里，否则会触发 `max_tokens` 截断导致子任务卡死。

输出 JSON 结构（`frame_results` **按相机分组的嵌套结构**，已过滤无命中的帧和相机）：
```json
{
  "frame_results": [
    {
      "camera_id": "cam_001",
      "camera_name": "园区东门摄像头",
      "location": "园区东门/1号路口",
      "latitude": 39.908722,
      "longitude": 116.397499,
      "frames": [
        {
          "image_url": "http://example.local/cam_001_001.jpg",
          "timestamp": 1700000000000,
          "features": [
            {"feature_name": "pedestrian", "feature_bbox": [150.0, 200.0, 250.0, 400.0]}
          ]
        },
        {
          "image_url": "http://example.local/cam_001_002.jpg",
          "timestamp": 1700000300000,
          "features": [
            {"feature_name": "pedestrian", "feature_bbox": [160.0, 210.0, 260.0, 410.0]},
            {"feature_name": "yellow van", "feature_bbox": [10.0, 20.0, 300.0, 400.0]}
          ]
        }
      ]
    }
  ],
  "meta": {
    "task_id": "7fa5de09-ec5f-4758-a300-00c158e36e9e",
    "status": "COMPLETED",
    "progress": 100,
    "total_time": 12.3,
    "features": ["yellow van", "pedestrian"],
    "camera_count": 2,
    "matched_cameras": 1,
    "matched_frames": 2
  }
}
```

> 说明：早期版本曾输出 `task` 和 `preprocess` 两个冗余字段（包含 API 原始响应），
> 现已移除以避免 stdout 过大（原 160KB → 现 ~60KB）。
> 如需排查原始 API 响应，可用 `meta.task_id` 调 `GET /api/v1/task/results/{task_id}` 重新获取。

---
name: seeagent/skills@feature-extraction
description: 串联真实的相机预处理（接口5）、轮询状态（接口6）与聚合特征检索（接口7）。输入相机列表+文本描述进行查找时调用。
license: MIT
metadata:
  author: seeagent
  version: "1.0.0"
---

# Feature Extraction (1seetime)

## When to Use

- 用户提供“相机 ID 列表”、“时间范围”和“文本特征描述”，希望获取包含对应特征的图像帧
- 需要执行严格的任务流：调用预处理接口 5 -> 轮询接口 6 状态直至 completed -> 调用聚合特征接口 7

## Inputs

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| camera_ids | array[string] | 是 | 待处理相机 ID 列表 |
| feature_text | string | 是 | 文本特征描述（如“找东门入口”） |
| start_time | string | 是 | 截帧开始时间 (ISO8601) |
| end_time | string | 是 | 截帧结束时间 (ISO8601) |
| base_url | string | 否 | 服务地址，默认 `http://10.11.0.131:19988` |
| poll_interval | number | 否 | 轮询间隔秒数，默认 `5` |
| timeout | number | 否 | 轮询超时秒数，默认 `3600`（1小时） |
| top_k | number | 否 | 检索返回数量，默认 `20` |

## Orchestration Flow

1. 调用 `POST /api/v1/cameras/preprocess` 创建预处理任务（接口 5）。
   - body: `{"camera_ids": [...], "start_time": "...", "end_time": "...", "frame_rate": 1}`
   - 解析返回的 `task_id`
2. 每隔 `poll_interval`（默认 5s）轮询 `GET /api/v1/cameras/preprocess/{task_id}`（接口 6）。
   - 检查 `status` 和 `progress` 字段，直至 `status` 变为 `completed` 或者 `progress` 达到 `100`。若为 `failed` 则直接终止。
3. 状态变为 `completed` 或进度达到 `100` 后，调用 `POST /api/v1/search` 进行特征检索（接口 7）。
   - body: `{"text": "{feature_text}", "task_id": "{task_id}", "top_k": 20}`
4. 返回 `result.result` 中的图像帧列表。

## Script

脚本位于 `scripts/run_feature_extraction.py`，无额外第三方依赖，使用标准库实现。

```bash
python skills/1seetime/feature-extraction/scripts/run_feature_extraction.py \
  --camera-ids cam_001,cam_002 \
  --feature-text "找东门入口" \
  --start-time "2024-01-15T08:30:00" \
  --end-time "2024-01-15T09:30:00"
```

## Output

输出 JSON 结构：
```json
{
  "task": { "task_id": "...", "status": "pending" },
  "preprocess": { "task_id": "...", "status": "completed", "progress": 100 },
  "search": [
    {
      "camera_id": "cam_001",
      "camera_name": "园区东门摄像头",
      "location": "园区东门/1号路口",
      "image_url": "http://example.local/cam_001.jpg",
      "timestamp": 1700000000000,
      "score": 0.9123,
      "latitude": 39.908722,
      "longitude": 116.397499
    }
  ],
  "meta": { "total_time": 12.3 }
}
```
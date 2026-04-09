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
| base_url | string | 否 | 服务地址，默认 `https://api-platform-test.zhidaozhixing.com` |
| poll_interval | number | 否 | 轮询间隔秒数，默认 `5` |
| timeout | number | 否 | 轮询超时秒数，默认 `3600`（1小时） |
| top_k | number | 否 | 检索返回数量，默认 `20` |

## Orchestration Flow

**以下流程已封装在脚本 `run_feature_extraction.py` 中，直接通过 `run_skill_script` 一次调用即可完成全部步骤，无需手动逐步调用 API：**

1. 创建预处理任务（接口 5）：`POST /api/v1/cameras/preprocess`
2. 轮询任务状态（接口 6）：`GET /api/v1/cameras/preprocess/{task_id}`，直至 `completed`
3. 特征检索（接口 7）：`POST /api/v1/search`
4. 返回 `result.result` 中的图像帧列表

## Script

脚本位于 `scripts/run_feature_extraction.py`，无额外第三方依赖，使用标准库实现。

**必须使用 `run_skill_script` 执行，禁止通过 `run_shell` 手动拼路径：**

```
run_skill_script(
  skill_name="feature-extraction",
  script_name="run_feature_extraction.py",
  args=["--camera-ids", "cam_001,cam_002", "--feature-text", "找东门入口", "--start-time", "2024-01-15T08:30:00", "--end-time", "2024-01-15T09:30:00"]
)
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
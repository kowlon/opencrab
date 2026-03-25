---
name: seeagent/skills@feature-extraction
description: 串联相机预处理与特征检索：先创建预处理任务并每5秒轮询完成状态，再基于文本特征检索。用户需要按相机列表+文本描述做检索时调用。
license: MIT
metadata:
  author: seeagent
  version: "1.0.0"
---

# Feature Extraction

## When to Use

- 用户输入“相机 ID 列表 + 文本描述”，希望拿到最终检索结果
- 需要严格按流程执行：创建预处理任务 → 轮询状态 → 完成后检索
- 需要可维护、可复用的一键脚本化调用能力

## Inputs

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| camera_ids | array[string] | 是 | 待处理相机 ID 列表 |
| feature_text | string | 是 | 特征文本描述（当前用文本模拟） |
| base_url | string | 否 | 服务地址，默认 `http://127.0.0.1:8010` |
| poll_interval | number | 否 | 轮询间隔秒数，默认 `5` |
| timeout | number | 否 | 总超时秒数，默认 `180` |

## Orchestration Flow

1. 调用 `POST /api/v1/cameras/preprocess` 创建预处理任务。
2. 从响应中提取 `task_id`。
3. 每隔 `5s` 轮询 `GET /api/v1/cameras/preprocess/{task_id}`。
4. 当状态为 `completed` 后，调用 `POST /api/v1/search`，请求体使用：
   - `mode: "text"`
   - `text: feature_text`
5. 聚合并输出：
   - 预处理任务信息
   - 预处理最终状态与结果
   - 特征检索结果（含按 `camera_ids` 过滤后的结果）

## Script

- 脚本路径：`scripts/run_feature_extraction.py`
- 设计原则：
  - 仅使用 Python 标准库，无额外依赖
  - 单文件实现，便于阅读与维护
  - 错误统一结构化返回，便于上层系统处理

## Usage

```bash
python skills/0seetime/feature-extraction/scripts/run_feature_extraction.py \
  --camera-ids cam_001,cam_999 \
  --feature-text "园区东门可疑人员"
```

## Output

脚本输出 JSON，核心字段如下：

| 字段 | 说明 |
|---|---|
| task | 任务创建阶段返回（含 task_id） |
| preprocess | 轮询结束后的任务详情 |
| search | 特征检索原始结果 |
| matched_items | 按输入 camera_ids 过滤后的检索结果 |
| meta | 执行参数与统计信息 |

## Failure Handling

- 预处理创建失败：直接返回错误并退出
- 轮询超时：返回 `timeout` 错误与当前任务状态
- 任务状态异常：返回 `status_error`
- 检索失败：返回 `search_error`
- 服务响应格式异常：返回 `invalid_response`

---
name: seeagent/skills@camera-search
description: 调用真实的相机搜索 API（向量检索，空间检索）。支持多分支的输入 schema。用户需要根据文本或地点搜索候选摄像头时调用。
license: MIT
metadata:
  author: seeagent
  version: "2.0.0"
---

# Camera Search (1seetime)

## When to Use

- 用户要求"搜索摄像头""按位置查找相机"或"根据特征描述返回监控点位"
- 用户提供自然语言查询或 POI 地点关键词，希望得到相机的结构化列表
- 需要将输入分发到"向量检索"或"POI 范围检索"的 API 上

## API Contract

- 服务地址：`https://search.zhidaozhixing.com`
- 向量检索：`GET /api/v1/cameras/search`
- POI 检索：`GET /api/v1/cameras/search/poi`

### 分支 1: 语义搜索 (向量检索) ← 默认优先
- 触发条件：输入参数包含 `query`（地点名称、道路、门牌地址、自然语言描述均走此分支）
- API: `GET /api/v1/cameras/search`
- 参数: 
  - `query` (string, 必填): 自然语言文本
  - `limit` (int, 可选, 默认 20): 返回数量，范围 1-100
  - `source` (int, 可选, 默认 2): 数据源，1=toc 2=治理

### 分支 2: POI 范围检索
- 触发条件：输入参数包含 `keyword`，且用户明确要求"附近""周边""方圆X公里"等空间范围检索
- API: `GET /api/v1/cameras/search/poi`
- 参数:
  - `keyword` (string, 必填): POI 关键词
  - `radius_m` (number, 可选, 默认 1000): 半径(米)
  - `region` (string, 可选): 区域（用于提高高德 POI 命中率）
  - `types` (string, 可选): POI 类型（高德 types 参数）
  - `limit` (int, 可选, 默认 20): 返回数量，范围 1-100
  - `offset` (int, 可选, 默认 0): 分页偏移
  - `source` (int, 可选, 默认 2): 数据源，1=toc 2=治理

## Output Contract

API 返回的 JSON 结构（注意字段名为缩写形式）：
```json
{
  "code": 200,
  "msg": "ok",
  "result": {
    "cameras": [
      {
        "c_id": "cam_001",
        "name": "园区东门摄像头",
        "addr": "园区东门/1号路口",
        "lat": 39.908722,
        "lon": 116.397499,
        "img_url": "http://example.local/cam_001.jpg",
        "timestamp": 1776223405088,
        "score": 0.8231,
        "distance_m": null
      }
    ]
  }
}
```

## Instructions

1. 检查用户输入，判断是包含 `query` 还是 `keyword`。
2. 根据判断结果构造请求调用对应的 API。
3. 解析响应 JSON，提取 `result.cameras`。
4. 将 API 字段映射为下游字段：`c_id`→`camera_id`, `addr`→`location`, `lat`→`latitude`, `lon`→`longitude`, `img_url`→`image_url`。`name` 和 `timestamp` 保持不变。
5. 返回映射后的 `camera_id`、`name`、`location` 等核心字段给下游任务。
6. 如果 `cameras` 为空，明确告知未检索到匹配相机。

## Request Example

**使用 `run_shell` 执行 curl 命令调用 API（此技能无脚本，直接调 API）：**

### 示例 1：语义搜索 (向量检索)
当输入包含 `query` 时，执行：
```bash
curl -s -X GET 'https://search.zhidaozhixing.com/api/v1/cameras/search?query=园区东门的摄像头&limit=5'
```

### 示例 2：POI 范围检索
当输入包含 `keyword` 且包含"附近""周边"等关键词时，执行：
```bash
curl -s -X GET 'https://search.zhidaozhixing.com/api/v1/cameras/search/poi?keyword=停车场&radius_m=500&limit=10'
```

**注意**：API 返回格式为 `{"code": 200, "result": {"cameras": [...]}}`，摄像头列表在 `result.cameras` 字段中。字段使用缩写（`c_id`, `addr`, `lat`, `lon`, `img_url`），输出时需映射为完整名称。

## Response Example

```json
{
  "code": 200,
  "msg": "ok",
  "result": {
    "cameras": [
      {
        "c_id": "cam_001",
        "name": "园区东门摄像头",
        "addr": "园区东门/1号路口",
        "lat": 39.908722,
        "lon": 116.397499,
        "img_url": "http://example.local/cam_001.jpg",
        "timestamp": 1776223405088
      }
    ]
  }
}
```

## Response Style

- 提取 `result.cameras` 列表，优先保留高相关性结果。
- 明确输出供下游环节使用的 `camera_ids` 数组（从 `c_id` 字段提取）。

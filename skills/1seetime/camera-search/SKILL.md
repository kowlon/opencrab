---
name: seeagent/skills@camera-search
description: 调用真实的相机搜索 API（向量检索，空间检索）。支持多分支的输入 schema。用户需要根据文本或地点搜索候选摄像头时调用。
license: MIT
metadata:
  author: seeagent
  version: "1.0.0"
---

# Camera Search (1seetime)

## When to Use

- 用户要求“搜索摄像头”“按位置查找相机”或“根据特征描述返回监控点位”
- 用户提供自然语言查询或 POI 地点关键词，希望得到相机的结构化列表
- 需要将输入分发到“向量检索”或“POI 范围检索”的 API 上

## API Contract

- 服务地址：`http://10.11.0.131:19988`
- API 3 向量检索：`GET /api/v1/cameras/search`
- API 4 POI 检索：`GET /api/v1/cameras/search/poi`

### 分支 1: 语义搜索 (向量检索)
- 触发条件：输入参数包含 `query`
- API: `GET /api/v1/cameras/search`
- 参数: 
  - `query` (string, 必填): 自然语言文本
  - `limit` (int, 可选, 默认 20): 返回数量

### 分支 2: POI 范围检索
- 触发条件：输入参数包含 `keyword`
- API: `GET /api/v1/cameras/search/poi`
- 参数:
  - `keyword` (string, 必填): POI 关键词
  - `radius_m` (number, 可选, 默认 1000): 半径(米)
  - `region` (string, 可选): 区域
  - `types` (string, 可选): POI 类型
  - `limit` (int, 可选, 默认 20): 返回数量

## Output Contract

返回的 JSON 结构:
```json
{
  "code": 200,
  "msg": "ok",
  "result": {
    "cameras": [
      {
        "camera_id": "cam_001",
        "name": "园区东门摄像头",
        "location": "园区东门/1号路口",
        "latitude": 39.908722,
        "longitude": 116.397499,
        "image_url": "http://example.local/cam_001.jpg",
        "score": 0.8231,
        "distance_m": null
      }
    ]
  }
}
```

## Instructions

1. 检查用户输入，判断是包含 `query` 还是 `keyword`。
2. 根据判断结果构造请求调用对应的 API（API 3 或 API 4）。
3. 解析响应 JSON，提取 `result.cameras`。
4. 返回 `camera_id`、`name`、`location` 等核心字段给下游任务。
5. 如果 `cameras` 为空，明确告知未检索到匹配相机。

## Request Example

### 示例 1：语义搜索 (向量检索)
当用户输入 `{"query": "园区东门的摄像头", "limit": 5}` 时，执行：
```bash
curl -X GET 'http://10.11.0.131:19988/api/v1/cameras/search?query=%E5%9B%AD%E5%8C%BA%E4%B8%9C%E9%97%A8%E7%9A%84%E6%91%84%E5%83%8F%E5%A4%B4&limit=5' \
  -H 'Content-Type: application/json'
```

### 示例 2：POI 范围检索
当用户输入 `{"keyword": "停车场", "radius_m": 500, "limit": 10}` 时，执行：
```bash
curl -X GET 'http://10.11.0.131:19988/api/v1/cameras/search/poi?keyword=%E5%81%9C%E8%BD%A6%E5%9C%BA&radius_m=500&limit=10' \
  -H 'Content-Type: application/json'
```

## Response Example

```json
{
  "code": 200,
  "msg": "ok",
  "result": {
    "cameras": [
      {
        "camera_id": "cam_001",
        "name": "园区东门摄像头",
        "location": "园区东门/1号路口",
        "image_url": "http://example.local/cam_001.jpg"
      }
    ]
  }
}
```

## Response Style

- 提取 `result.cameras` 列表，优先保留高相关性结果。
- 明确输出供下游环节使用的 `camera_ids` 数组。
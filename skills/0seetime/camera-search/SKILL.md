---
name: seeagent/skills@camera-search
description: 调用 Camera Search Mock 的相机搜索接口（POST /api/v1/cameras/search）按自然语言返回相机列表。用户提到找摄像头、按地点搜索监控时调用。
license: MIT
metadata:
  author: seeagent
  version: "1.0.0"
---

# Camera Search

## When to Use

- 用户要求“查找相机”“按位置搜索摄像头”“根据描述返回监控点位”
- 用户提供自然语言地点或场景描述并希望得到可预览的相机结果
- 需要把自然语言查询转换为标准化相机列表输出

## API Contract

- 服务地址：`http://127.0.0.1:8010`
- 方法与路径：`POST /api/v1/cameras/search`
- 请求头：`Content-Type: application/json`
- 请求体：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| query | string | 是 | 自然语言描述，例如“园区东门” |

- 成功响应：`200`
- 参数校验失败：`422`

## Output Contract

返回字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| cameras | array | 相机列表 |
| cameras[].id | string | 相机 ID |
| cameras[].location | string | 相机位置 |
| cameras[].image_url | string | 相机首帧图片 URL |

## Instructions

1. 从用户输入中提取检索语句，作为 `query`。
2. 向 `http://127.0.0.1:8010/api/v1/cameras/search` 发起 `POST` 请求。
3. 校验响应状态码与 JSON 结构。
4. 将结果整理为“相机ID + 位置 + 预览图URL”返回给用户。
5. 若 `cameras` 为空，明确告知“未检索到匹配相机”。

## Request Example

```bash
curl -X POST 'http://127.0.0.1:8010/api/v1/cameras/search' \
  -H 'Content-Type: application/json' \
  -d '{"query":"园区东门"}'
```

## Response Example

```json
{
  "cameras": [
    {
      "id": "cam_001",
      "location": "园区东门",
      "image_url": "http://127.0.0.1:8010/static/cam_001.jpg"
    }
  ]
}
```

## Response Style

- 默认按相关性顺序列出相机结果
- 每条结果使用统一格式：`[camera_id] location - image_url`
- 返回前可附带查询词回显：`查询：{query}`

## Failure Handling

- `422`：提示用户补充或修正查询词
- 非 `200/422`：返回“相机搜索服务暂不可用”，并附状态码
- 响应非 JSON 或缺少 `cameras` 字段：返回“服务响应格式异常”

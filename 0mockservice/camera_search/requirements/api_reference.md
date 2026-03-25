# Camera Search Mock 服务接口文档

## 1. 基本信息

- 服务地址：`http://127.0.0.1:8010`
- 协议：`HTTP`
- 数据格式：请求与响应均为 `application/json`
- 鉴权：无（Mock 阶段）

## 2. 通用约定

- 成功响应：HTTP 状态码 `200`
- 参数校验失败：HTTP 状态码 `422`
- 资源不存在：HTTP 状态码 `404`
- 业务校验失败：HTTP 状态码 `400`

---

## 3. 接口列表

1. 相机搜索：`POST /api/v1/cameras/search`
2. 预处理任务创建：`POST /api/v1/cameras/preprocess`
3. 预处理状态查询：`GET /api/v1/cameras/preprocess/{task_id}`
4. 特征检索（统一）：`POST /api/v1/search`

---

## 4. 相机搜索

### 4.1 接口说明

- 方法与路径：`POST /api/v1/cameras/search`
- 用途：根据自然语言描述搜索相机，并返回相机首帧预览图地址

### 4.2 请求参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| query | string | 是 | 自然语言描述，如“园区东门” |

### 4.3 响应参数

| 字段 | 类型 | 说明 |
|---|---|---|
| cameras | array | 相机列表 |
| cameras[].id | string | 相机 ID |
| cameras[].location | string | 相机位置 |
| cameras[].image_url | string | 相机首帧图片 URL |

### 4.4 请求示例

```bash
curl -X POST 'http://127.0.0.1:8010/api/v1/cameras/search' \
  -H 'Content-Type: application/json' \
  -d '{"query":"园区东门"}'
```

### 4.5 响应示例

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

---

## 5. 预处理任务创建

### 5.1 接口说明

- 方法与路径：`POST /api/v1/cameras/preprocess`
- 用途：创建相机预处理任务，模拟截帧并发送远端处理（耗时模拟 100s）

### 5.2 请求参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| camera_ids | array[string] | 是 | 需要处理的相机 ID 列表 |

### 5.3 响应参数

| 字段 | 类型 | 说明 |
|---|---|---|
| task_id | string | 任务 ID |
| status | string | 初始状态，通常为 `pending` |
| progress | integer | 初始进度，范围 `0~100` |
| camera_ids | array[string] | 请求中的相机 ID 列表 |

### 5.4 请求示例

```bash
curl -X POST 'http://127.0.0.1:8010/api/v1/cameras/preprocess' \
  -H 'Content-Type: application/json' \
  -d '{"camera_ids":["cam_001","cam_999"]}'
```

### 5.5 响应示例

```json
{
  "task_id": "7fe73f06-4ec8-4c97-a43c-228bd4523950",
  "status": "pending",
  "progress": 0,
  "camera_ids": ["cam_001", "cam_999"]
}
```

### 5.6 失败示例（业务参数错误）

```json
{
  "detail": "camera_ids can not be empty"
}
```

---

## 6. 预处理状态查询

### 6.1 接口说明

- 方法与路径：`GET /api/v1/cameras/preprocess/{task_id}`
- 用途：查询预处理任务状态与进度，完成后返回每个相机的处理结果

### 6.2 路径参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| task_id | string | 是 | 任务 ID |

### 6.3 响应参数

| 字段 | 类型 | 说明 |
|---|---|---|
| task_id | string | 任务 ID |
| status | string | `pending` / `running` / `completed` |
| progress | integer | 进度，范围 `0~100` |
| camera_ids | array[string] | 任务内相机 ID 列表 |
| results | array/null | 完成后返回聚合结果，未完成时可能为空 |
| results[].camera_id | string | 相机 ID |
| results[].status | string | `success` / `error` / `not_found` |
| results[].message | string/null | 结果描述 |
| created_at | number | 任务创建时间戳 |
| updated_at | number | 任务更新时间戳 |

### 6.4 请求示例

```bash
curl 'http://127.0.0.1:8010/api/v1/cameras/preprocess/7fe73f06-4ec8-4c97-a43c-228bd4523950'
```

### 6.5 响应示例（运行中）

```json
{
  "task_id": "7fe73f06-4ec8-4c97-a43c-228bd4523950",
  "status": "running",
  "progress": 42,
  "camera_ids": ["cam_001", "cam_999"],
  "results": null,
  "created_at": 1774422596.278971,
  "updated_at": 1774422638.110002
}
```

### 6.6 响应示例（已完成）

```json
{
  "task_id": "7fe73f06-4ec8-4c97-a43c-228bd4523950",
  "status": "completed",
  "progress": 100,
  "camera_ids": ["cam_001", "cam_999"],
  "results": [
    {
      "camera_id": "cam_001",
      "status": "success",
      "message": "preprocess finished and sent to remote service (mock)"
    },
    {
      "camera_id": "cam_999",
      "status": "not_found",
      "message": "camera id not found"
    }
  ],
  "created_at": 1774422596.278971,
  "updated_at": 1774422696.310221
}
```

### 6.7 失败示例（任务不存在）

```json
{
  "detail": "task_id not found: not-exists"
}
```

---

## 7. 特征检索（统一）

### 7.1 接口说明

- 方法与路径：`POST /api/v1/search`
- 用途：统一支持文字检索、图片检索、图文联合检索

### 7.2 请求参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| mode | string | 否 | `text` / `image` / `image_text`，默认 `text` |
| query | string | 否 | 文本检索兼容字段，等价于 `text` |
| text | string | 否 | 文本特征 |
| image | string | 否 | 图片 URL 或上传后的标识 |

### 7.3 参数约束

- 当 `mode=text` 时：`text` 或 `query` 必填
- 当 `mode=image` 时：`image` 必填
- 当 `mode=image_text` 时：`text` 与 `image` 均必填

### 7.4 响应参数

| 字段 | 类型 | 说明 |
|---|---|---|
| mode | string | 实际检索模式 |
| items | array | 检索结果 |
| items[].camera_id | string | 相机 ID |
| items[].location | string | 相机位置 |
| items[].image_url | string | 图片 URL |
| items[].score | number | Mock 相关性分数 |

### 7.5 请求示例（文本）

```bash
curl -X POST 'http://127.0.0.1:8010/api/v1/search' \
  -H 'Content-Type: application/json' \
  -d '{"query":"园区东门"}'
```

### 7.6 请求示例（图片）

```bash
curl -X POST 'http://127.0.0.1:8010/api/v1/search' \
  -H 'Content-Type: application/json' \
  -d '{"mode":"image","image":"mock://image-1"}'
```

### 7.7 请求示例（图文）

```bash
curl -X POST 'http://127.0.0.1:8010/api/v1/search' \
  -H 'Content-Type: application/json' \
  -d '{"mode":"image_text","text":"东门","image":"mock://image-1"}'
```

### 7.8 响应示例

```json
{
  "mode": "text",
  "items": [
    {
      "camera_id": "cam_001",
      "location": "园区东门",
      "image_url": "http://127.0.0.1:8010/static/cam_001.jpg",
      "score": 0.8
    }
  ]
}
```

### 7.9 失败示例（参数校验失败）

```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body"],
      "msg": "Value error, mode=image 时，image 必填",
      "input": {
        "mode": "image"
      }
    }
  ]
}
```

---

## 8. 字段字典

| 字段 | 说明 |
|---|---|
| id / camera_id | 相机唯一标识 |
| location | 相机位置 |
| image_url | 首帧截图可访问地址 |
| task_id | 异步预处理任务标识 |
| status | 任务状态或子任务状态 |
| progress | 任务进度（0~100） |
| score | 特征检索相关性分 |

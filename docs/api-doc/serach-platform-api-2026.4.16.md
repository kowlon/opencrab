Base_url:https://search.zhidaozhixing.com

## 1. 相机搜索（向量检索）

### GET /api/v1/cameras/search

输入自然语言文本 `query`，调用数据源的接口，默认调用，三方接口.md中`/api/v1/camera/vector/search接口。`

**Query 参数**

| 参数     | 类型     | 必填 | 默认值 | 说明             |
| :----- | :----- | -: | --: | :------------- |
| query  | string |  是 |   - | 自然语言文本，不能为空    |
| limit  | int    |  否 |  20 | 返回数量，范围 1-100  |
| source | int    |  否 |   2 | 数据源：1:toc 2:治理 |

**响应 200**

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
        "score": 0.8231
      }
    ]
  }
}
```

**cameras\[] 字段说明**

| 字段         | 类型          | 说明             |
| :--------- | :---------- | :------------- |
| camera\_id | string      | 相机业务 ID        |
| name       | string      | 相机名称           |
| addr       | string      | 位置描述           |
| lat        | number      | 纬度             |
| lon        | number      | 经度             |
| img\_url   | string/null | 图片 URL（可选）     |
| timestamp  | long        | 图片对应的时间戳       |
| score      | number/null | 向量相似度得分（越大越相似） |

**常见错误**

- 400：query 为空
- 502：Embedding 服务异常或超时

## 2. 相机搜索（POI-Geo）

### GET /api/v1/cameras/search/poi

输入 `keyword`（以及可选 `region/types`），服务会：

- 若配置了 `AMAP_KEY`：调用高德 POI 文本搜索接口 `https://restapi.amap.com/v5/place/text` 获取经纬度作为中心点；
- 得到经纬度，调用数据源的接口，默认调用，三方接口.md中`/api/v1/camera/geo/search接口`。

**Query 参数**

| 参数        | 类型     | 必填 |  默认值 | 说明                  |
| :-------- | :----- | -: | ---: | :------------------ |
| keyword   | string |  是 |    - | POI 关键词或地址文本        |
| region    | string |  否 | null | 区域（用于提高高德 POI 命中率）  |
| types     | string |  否 | null | POI 类型（高德 types 参数） |
| radius\_m | number |  否 | 1000 | 半径（米），> 0           |
| limit     | int    |  否 |   20 | 返回数量，范围 1-100       |
| offset    | int    |  否 |    0 | 分页偏移，>= 0           |
| source    | int    |  否 |    2 | 数据源：1:toc 2:治理      |

**响应 200**

```json
{
  "code": 200,
  "msg": "ok",
  "result": {
    "cameras": [
      {
        "c_id": "cam_003",
        "name": "停车场摄像头A",
        "addr": "北侧停车场/入口",
        "lat": 39.918722,
        "lon": 116.397999,
        "img_url": "http://example.local/cam_003.jpg",
        "timestamp": 1776223405088,
        "distance_m": 132.45
      }
    ]
  }
}
```

**常见错误**

- 400：keyword 为空
- 404：未配置 AMAP\_KEY 且 keyword 在本地相机库未命中
- 502：高德服务不可用/返回异常，且本地降级也未命中 keyword

## 3. 预处理任务（创建）

### POST /api/v1/cameras/preprocess

按相机 ID 列表创建预处理任务。调用数据源的接口，默认调用，三方接口.md中`/api/v1/camera/image/range接口，得到相机对应的图片集，创建主任务，如果图片集较大，需要创建子任务`。\
如果某个相机的图片数 `<= 64`：整个相机作为一个批次

- 如果某个相机的图片数 `> 64`：\
  将该相机图片按 **64 张一组** 拆分\
  （最后不足 64 的单独成批）

> ✅ 优点：逻辑简单，避免不同相机数据混杂\
> ❌ 缺点：可能出现大量不足 64 的批次，浪费容量

**请求体**

```json
{
  "c_ids": ["cam_001", "cam_002"],
  "start_time": 1776223405088,
  "end_time": 1776223406088,
  "features": ["黄色面包车","穿红色衣服的人"]
}
```

**字段说明**

| 字段          | 类型        | 必填 | 说明                     |
| :---------- | :-------- | -: | :--------------------- |
| c\_ids      | string\[] |  是 | 相机 ID 列表，至少 1 个        |
| start\_time | Long      |  是 | 开始时间戳                  |
| end\_time   | Long      |  是 | 结束时间戳，必须大于 start\_time |
| features    | string\[] |  是 | 特征集合（要求英文的）            |
| source      | int       |  否 | 数据源：1:toc 2:治理         |

**响应 200**

```json
{
  "code": 200,
  "msg": "ok",
  "result": {
    "task_id": "task_1709312345678",
    "status": "pending",
    "progress": 0
  }
}
```

**字段说明**

| 字段       | 类型       | 说明                                          |
| :------- | :------- | :------------------------------------------ |
| task\_id | string   | 任务 ID（来自第三方任务服务）                            |
| status   | string   | pending/processing/completed/failed（由第三方决定） |
| progress | int/null | 0-100（第三方可能不返回，可能为 null）                    |

**常见错误**

- 400：为获取到时段下的数据
- 502：第三方任务服务不可用或超时

## 4. 预处理任务（状态查询）

### GET /api/v1/cameras/preprocess/{task\_id}

查询预处理任务状态。

- 服务端调用第三方任务服务：`GET /api/tasks/{task_id}`。

**Path 参数**

| 参数       | 类型     | 必填 | 说明    |
| :------- | :----- | -: | :---- |
| task\_id | string |  是 | 任务 ID |

**Query 参数**

| 参数     | 类型      | 必填 |   默认值 | 说明                |
| :----- | :------ | -: | ----: | :---------------- |
| detail | boolean |  否 | false | true 时返回返回任务处理的结果 |

**响应 200**

```json
{
	"code": 200,
	"msg": "ok",
	"result": {
		"task_id": "f9d88616-eb21-44ec-a134-499d3f33f68e",
		"status": "COMPLETED",
		"progress": 100,
		"data": [
			{
				"c_id": "11010148001310003002",
				"name": "东直门内大街262号门前",
				"addr": "北京市/北京市/东城区/北新桥街道",
				"lat": 39.939409,
				"lon": 116.412469,
				"results": [
					{
						"features": [
							{
								"feature_bbox": [
									371.57157157157155,
									96.57657657657658,
									392.07207207207205,
									113.15315315315316
								],
								"feature_name": "white car"
							}
						],
						"image_url": "https://aidata-oss-cloud-prod.zhidaozhixing.com/oss-cloud/open/files/tmp/622dd3bfd9a749bebbcfb42b5aa84fe7.jpg",
						"timestamp": 1776140137712,
						"camera_id": "11010148001310003002"
					}
				]
			}
		]
	}
}
```

**result 字段说明**

| 字段       | 类型        | 说明                                                    |
| :------- | :-------- | :---------------------------------------------------- |
| task\_id | string    | 任务 ID                                                 |
| status   | string    | 任务状态（示例：pending/processing/completed/failed；以第三方为准）   |
| progress | int/null  | 任务进度 0-100（第三方可能不返回，可能为 null）                         |
| c\_ids   | string\[] | 本次处理涉及的相机 ID 集合                                       |
| data     | arry      | 当detail为true时返回任务处理的结果信息，对应的数据结构参考"任务结果查询"接口返回的data数据 |

## 5. 任务结果查询

### GET /api/v1/task/results/{task\_id}

**Path 参数**

| 参数       | 类型     | 必填 | 说明    |
| :------- | :----- | -: | :---- |
| task\_id | string |  是 | 任务 ID |

<br />

**响应（200）**

```json
{
	"code": 200,
	"msg": "ok",
	"result": [
		{
			"c_id": "11010148001310003002",
			"name": "东直门内大街262号门前",
			"addr": "北京市/北京市/东城区/北新桥街道",
			"lat": 39.939409,
			"lon": 116.412469,
			"results": [
				{
					"features": [
						{
							"feature_bbox": [
								371.57157157157155,
								96.57657657657658,
								392.07207207207205,
								113.15315315315316
							],
							"feature_name": "white car"
						}
					],
					"image_url": "https://aidata-oss-cloud-prod.zhidaozhixing.com/oss-cloud/open/files/tmp/622dd3bfd9a749bebbcfb42b5aa84fe7.jpg",
					"timestamp": 1776140137712,
					"camera_id": "11010148001310003002"
				}
			]
		}
	]
}
```

**常见错误**

- 404：任务不存在（第三方返回 404）
- 502：第三方任务服务不可用或超时

| 字段        | 类型     | 说明       |
| :-------- | :----- | :------- |
| c\_id     | string | 相机 ID    |
| name      | string | 相机名称     |
| addr      | string | 相机位置描述   |
| lat       | number | 纬度       |
| lon       | number | 经度       |
| `results` | array  | 该相机的检索结果 |

`results[]`（相机内结果）：

| 字段        | 类型     | 说明       |
| :-------- | :----- | :------- |
| img\_url  | string | 图片 URL   |
| timestamp | Long   | 图片对应的时间戳 |
| features  | array  | 识别特征列表   |

`features[]`：

| 字段   | 类型        | 说明                         |
| :--- | :-------- | :------------------------- |
| name | string    | 特征名称（示例：pedestrian）        |
| bbox | number\[] | 边界框与置信度：`[x1, y1, x2, y2]` |




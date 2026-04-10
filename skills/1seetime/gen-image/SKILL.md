---
name: seeagent/skills@gen-image
description: 将 1seetime-feature-extraction 的 JSON 结果（或包含图像帧URL的数组）生成为带有表格的 PNG 图片。需要把检索结果做成图表分享时调用。
license: MIT
metadata:
  author: seeagent
  version: "1.0.0"
---

# Gen Image (1seetime)

## When to Use

- 已有 `1seetime-feature-extraction` 的输出 JSON 或类似的 `frame_results` 数据，需要将结果可视化
- 需要把图像帧结果转换成“包含相机名称、位置、时间戳和缩略图”的图片（PNG）
- 需要直观展示最佳实践第三阶段“检索结果画图”的输出结果

## Input

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| input | string | 是 | `feature-extraction` 阶段输出的 JSON 文件路径 |
| output | string | 否 | 导出 PNG 的路径，默认 `result_report.png` |
| title | string | 否 | 图片标题，默认 `Feature Search Result` |

## Data Mapping

- 读取 `input` 指定的 JSON 文件
- 提取根级数组（如果是数组）或提取 `search` 字段数组
- 每一行需展示：
  - `camera_name`
  - `location`
  - `timestamp`（时间戳转格式化时间）
  - `image`（下载 `image_url` 渲染缩略图）

## Script

- 脚本路径：`scripts/render_feature_table.py`
- 依赖：`Pillow`, `requests`

安装依赖：
```bash
pip install pillow requests
```

## Usage

**必须使用 `run_skill_script` 执行，禁止通过 `run_shell` 手动拼路径：**

（注：生成图片时可按需传入 `timeout` 延长执行时间）

```
run_skill_script(
  skill_name="gen-image",
  script_name="render_feature_table.py",
  args=["--input", "/path/to/feature_result.json", "--output", "/path/to/result_report.png", "--title", "园区东门特征检索结果"],
  timeout=300
)
```

## Output

- 脚本标准输出返回生成的图片路径等总结信息
- 实际生成一张 PNG 格式的图表文件
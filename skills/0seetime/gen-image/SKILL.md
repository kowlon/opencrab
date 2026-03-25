---
name: seeagent/skills@gen-image
description: 将 feature-extraction 的 JSON 输出转换为带图片列的表格，并导出 PNG 图片。用户需要把检索结果做成可分享图表时调用。
license: MIT
metadata:
  author: seeagent
  version: "1.0.0"
---

# Gen Image

## When to Use

- 已有 `feature-extraction` 的输出 JSON，需要可视化交付
- 需要把 `matched_items` 或 `search.items` 做成“可读表格 + 缩略图”图片
- 需要直接产出一张 PNG 供汇报、IM 发送或归档

## Input

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| input | string | 是 | `feature-extraction` 输出 JSON 文件路径 |
| output | string | 否 | 导出 PNG 路径，默认 `feature_table.png` |
| title | string | 否 | 图片标题，默认 `Feature Extraction Result` |
| max_rows | number | 否 | 最大行数，默认 `20` |

## Data Mapping

- 优先使用 `matched_items`
- 若 `matched_items` 为空，回退到 `search.items`
- 表格列固定为：
  - `camera_id`
  - `location`
  - `score`
  - `image`（缩略图）

## Script

- 脚本路径：`scripts/render_feature_table.py`
- 依赖：`Pillow`

安装依赖：

```bash
pip install pillow
```

## Usage

```bash
python skills/0seetime/gen-image/scripts/render_feature_table.py \
  --input /path/to/feature_extraction_output.json \
  --output /path/to/feature_table.png \
  --title "园区东门检索结果"
```

## Output

- 生成一张 PNG 表格图片
- 图片中每行展示相机信息与对应图片缩略图
- 脚本标准输出返回摘要 JSON（输出路径、行数、来源字段）

## Failure Handling

- 输入文件不存在或非 JSON：返回 `invalid_input_file`
- 结果字段缺失或为空：返回 `no_items`
- 图片拉取失败：该单元格展示占位图并继续生成

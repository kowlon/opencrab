#!/usr/bin/env python3

import argparse
import json
import sys
from io import BytesIO
from pathlib import Path
from typing import Any, NoReturn
from urllib.error import URLError
from urllib.request import urlopen

from PIL import Image, ImageDraw, ImageFont


def _fail(code: str, message: str, detail: dict[str, Any] | None = None) -> NoReturn:
    payload: dict[str, Any] = {"ok": False, "error": {"code": code, "message": message}}
    if detail is not None:
        payload["error"]["detail"] = detail
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.exit(1)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将 feature-extraction 输出渲染为带图片列的表格 PNG")
    parser.add_argument("--input", required=True, help="feature-extraction 输出 JSON 文件路径")
    parser.add_argument("--output", default="feature_table.png", help="输出 PNG 路径")
    parser.add_argument("--title", default="Feature Extraction Result", help="图片标题")
    parser.add_argument("--max-rows", type=int, default=20, help="最大渲染行数")
    parser.add_argument("--thumb-width", type=int, default=180, help="缩略图宽度")
    parser.add_argument("--thumb-height", type=int, default=100, help="缩略图高度")
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        _fail("invalid_input_file", "输入文件不存在", {"path": str(path)})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _fail("invalid_input_file", "输入文件不是有效 JSON", {"path": str(path), "error": str(exc)})
    if not isinstance(data, dict):
        _fail("invalid_input_file", "输入 JSON 顶层必须是对象", {"path": str(path)})
    return data


def _pick_items(data: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    matched = data.get("matched_items")
    if isinstance(matched, list) and matched:
        clean = [item for item in matched if isinstance(item, dict)]
        if clean:
            return clean, "matched_items"
    search = data.get("search")
    if isinstance(search, dict):
        items = search.get("items")
        if isinstance(items, list) and items:
            clean = [item for item in items if isinstance(item, dict)]
            if clean:
                return clean, "search.items"
    _fail("no_items", "未找到可渲染的数据，matched_items 与 search.items 均为空")


def _load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttc",
    ]
    for font_path in candidates:
        try:
            return ImageFont.truetype(font_path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _fetch_thumb(image_url: str, width: int, height: int) -> Image.Image:
    try:
        with urlopen(image_url, timeout=10) as resp:
            content = resp.read()
        img = Image.open(BytesIO(content)).convert("RGB")
        canvas = Image.new("RGB", (width, height), "#f3f4f6")
        img.thumbnail((width, height))
        x = (width - img.width) // 2
        y = (height - img.height) // 2
        canvas.paste(img, (x, y))
        return canvas
    except (URLError, OSError, ValueError):
        fallback = Image.new("RGB", (width, height), "#e5e7eb")
        draw = ImageDraw.Draw(fallback)
        font = _load_font(16)
        label = "image unavailable"
        tw = int(draw.textlength(label, font=font))
        draw.text(((width - tw) // 2, height // 2 - 8), label, fill="#6b7280", font=font)
        return fallback


def _draw_table(
    rows: list[dict[str, Any]],
    source: str,
    output: Path,
    title: str,
    thumb_width: int,
    thumb_height: int,
) -> dict[str, Any]:
    header_h = 52
    row_h = thumb_height + 20
    pad = 24
    title_h = 56
    footer_h = 40
    col_id = 180
    col_location = 300
    col_score = 120
    col_image = thumb_width + 20

    table_w = col_id + col_location + col_score + col_image
    width = pad * 2 + table_w
    height = title_h + header_h + len(rows) * row_h + footer_h + pad

    img = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(img)
    font_title = _load_font(28)
    font_header = _load_font(20)
    font_cell = _load_font(18)
    font_small = _load_font(15)

    draw.text((pad, 16), title, fill="#111827", font=font_title)
    draw.text((pad, 48), f"source: {source} | rows: {len(rows)}", fill="#6b7280", font=font_small)

    table_x = pad
    table_y = title_h
    draw.rectangle(
        [table_x, table_y, table_x + table_w, table_y + header_h],
        fill="#f3f4f6",
        outline="#d1d5db",
        width=1,
    )

    headers = ["camera_id", "location", "score", "image"]
    col_widths = [col_id, col_location, col_score, col_image]
    x = table_x
    for i, head in enumerate(headers):
        draw.text((x + 10, table_y + 14), head, fill="#111827", font=font_header)
        x += col_widths[i]
        draw.line([(x, table_y), (x, table_y + header_h + len(rows) * row_h)], fill="#d1d5db", width=1)

    y = table_y + header_h
    for idx, row in enumerate(rows):
        bg = "#ffffff" if idx % 2 == 0 else "#fafafa"
        draw.rectangle([table_x, y, table_x + table_w, y + row_h], fill=bg, outline="#e5e7eb", width=1)

        camera_id = str(row.get("camera_id", ""))
        location = str(row.get("location", ""))
        score_raw = row.get("score", "")
        score = f"{score_raw:.4f}" if isinstance(score_raw, (float, int)) else str(score_raw)
        image_url = str(row.get("image_url", ""))

        draw.text((table_x + 10, y + 12), camera_id, fill="#111827", font=font_cell)
        draw.text((table_x + col_id + 10, y + 12), location, fill="#111827", font=font_cell)
        draw.text((table_x + col_id + col_location + 10, y + 12), score, fill="#111827", font=font_cell)

        thumb = _fetch_thumb(image_url, thumb_width, thumb_height)
        img.paste(thumb, (table_x + col_id + col_location + col_score + 10, y + 10))
        y += row_h

    draw.rectangle([table_x, table_y, table_x + table_w, y], outline="#d1d5db", width=1)
    draw.text((pad, y + 12), "generated by gen-image skill", fill="#6b7280", font=font_small)

    output.parent.mkdir(parents=True, exist_ok=True)
    img.save(output, format="PNG")
    return {
        "ok": True,
        "output": str(output),
        "rows": len(rows),
        "source": source,
        "width": width,
        "height": height,
    }


def main() -> None:
    args = _args()
    if args.max_rows <= 0:
        _fail("invalid_args", "max_rows 必须大于 0")
    if args.thumb_width <= 0 or args.thumb_height <= 0:
        _fail("invalid_args", "thumb_width 与 thumb_height 必须大于 0")

    data = _load_json(Path(args.input))
    items, source = _pick_items(data)
    limited_rows = items[: args.max_rows]
    result = _draw_table(
        rows=limited_rows,
        source=source,
        output=Path(args.output),
        title=args.title,
        thumb_width=args.thumb_width,
        thumb_height=args.thumb_height,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

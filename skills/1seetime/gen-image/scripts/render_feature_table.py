import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO


def _summarize_features(features):
    """Aggregate features into a short label, e.g. 'red car × 2, pedestrian × 1'."""
    if not features:
        return ""
    counter = Counter()
    for f in features:
        name = f.get("feature_name") or f.get("name") or "?"
        counter[name] += 1
    return ", ".join(f"{name} × {cnt}" for name, cnt in counter.most_common())


def _flatten_nested(frame_results):
    """Nested structure (cameras → frames → features) → flat rows for rendering."""
    rows = []
    for cam in frame_results:
        for frame in cam.get("frames", []):
            rows.append({
                "camera_name": cam.get("camera_name", "Unknown"),
                "location": cam.get("location", "Unknown"),
                "timestamp": frame.get("timestamp"),
                "image_url": frame.get("image_url", ""),
                "feature_summary": _summarize_features(frame.get("features", [])),
            })
    return rows


def _adapt_legacy_flat(items):
    """Legacy flat format (1 row = 1 frame with camera fields inlined) → render rows."""
    rows = []
    for item in items:
        feats = item.get("features", []) or []
        rows.append({
            "camera_name": item.get("camera_name") or item.get("name", "Unknown"),
            "location": item.get("location", "Unknown"),
            "timestamp": item.get("timestamp"),
            "image_url": item.get("image_url", ""),
            "feature_summary": _summarize_features(feats),
        })
    return rows


def load_data(input_path):
    """Load input and normalize to a flat list of render rows.

    Supports:
    - New nested: {frame_results: [{camera_id, frames: [...]}]}
    - New nested (top-level list): [{camera_id, frames: [...]}]
    - Legacy flat: {search: [...]} or {frame_results: [...]} with inline camera fields
    - Raw API passthrough: {result: {data: [...]}}
    """
    if not os.path.exists(input_path):
        print(json.dumps({"error": "invalid_input_file", "msg": f"File not found: {input_path}"}))
        sys.exit(1)

    with open(input_path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            print(json.dumps({"error": "invalid_input_file", "msg": "Invalid JSON format"}))
            sys.exit(1)

    # Extract the relevant list
    raw = None
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        if "frame_results" in data and isinstance(data["frame_results"], list):
            raw = data["frame_results"]
        elif "search" in data and isinstance(data["search"], list):
            raw = data["search"]
        elif "result" in data and isinstance(data["result"], dict):
            inner = data["result"].get("data") or data["result"].get("result", [])
            if isinstance(inner, list):
                raw = inner

    if not raw:
        return []

    # Detect structure: nested if first item has "frames" key, else legacy flat
    first = raw[0] if raw else {}
    if isinstance(first, dict) and "frames" in first:
        return _flatten_nested(raw)
    return _adapt_legacy_flat(raw)


def get_image(url, size=(160, 90)):
    try:
        if url.startswith("http"):
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            img = Image.open(BytesIO(response.content)).convert("RGB")
        else:
            if os.path.exists(url):
                img = Image.open(url).convert("RGB")
            else:
                return Image.new("RGB", size, (200, 200, 200))
        img.thumbnail(size)
        return img
    except Exception:
        return Image.new("RGB", size, (220, 220, 220))


def render_table(rows, output_path, title):
    if not rows:
        print(json.dumps({"error": "no_items", "msg": "No items found to render"}))
        sys.exit(1)

    cell_padding = 10
    row_height = 120
    header_height = 50
    title_height = 60

    col_widths = {
        "name": 200,
        "location": 250,
        "time": 180,
        "features": 180,
        "image": 180,
    }

    table_width = sum(col_widths.values()) + (len(col_widths) + 1) * cell_padding
    table_height = title_height + header_height + (len(rows) * row_height) + 20

    img = Image.new('RGB', (table_width, table_height), color='white')
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("arial.ttf", 16)
        title_font = ImageFont.truetype("arial.ttf", 24)
        header_font = ImageFont.truetype("arial.ttf", 18)
    except IOError:
        font = ImageFont.load_default()
        title_font = font
        header_font = font

    draw.text((cell_padding, cell_padding), title, font=title_font, fill='black')

    y_offset = title_height
    headers = [
        ("Camera Name", col_widths["name"]),
        ("Location", col_widths["location"]),
        ("Time", col_widths["time"]),
        ("Features", col_widths["features"]),
        ("Image", col_widths["image"]),
    ]

    x_offset = cell_padding
    for h_text, width in headers:
        draw.text((x_offset, y_offset), h_text, font=header_font, fill='black')
        x_offset += width + cell_padding

    y_offset += header_height
    draw.line([(cell_padding, y_offset - 5), (table_width - cell_padding, y_offset - 5)], fill='black', width=2)

    for row in rows:
        x_offset = cell_padding
        text_y = y_offset + row_height // 2 - 10

        draw.text((x_offset, text_y), str(row.get("camera_name", "Unknown")), font=font, fill='black')
        x_offset += col_widths["name"] + cell_padding

        draw.text((x_offset, text_y), str(row.get("location", "Unknown")), font=font, fill='black')
        x_offset += col_widths["location"] + cell_padding

        ts = row.get("timestamp")
        if ts:
            try:
                dt = datetime.fromtimestamp(ts / 1000.0).strftime('%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError, OSError):
                dt = str(ts)
        else:
            dt = "N/A"
        draw.text((x_offset, text_y), dt, font=font, fill='black')
        x_offset += col_widths["time"] + cell_padding

        draw.text((x_offset, text_y), row.get("feature_summary", ""), font=font, fill='darkred')
        x_offset += col_widths["features"] + cell_padding

        img_url = row.get("image_url", "")
        if img_url:
            thumbnail = get_image(img_url, size=(160, 90))
            img.paste(thumbnail, (x_offset, y_offset + (row_height - thumbnail.height) // 2))

        y_offset += row_height
        draw.line([(cell_padding, y_offset), (table_width - cell_padding, y_offset)], fill='lightgray', width=1)

    img.save(output_path)

    return {
        "status": "success",
        "output_path": output_path,
        "items_count": len(rows)
    }


def main():
    parser = argparse.ArgumentParser(description="Render Feature Search Results to Table Image")
    parser.add_argument("--input", required=True, help="Input JSON file path")
    parser.add_argument("--output", default="result_report.png", help="Output PNG path")
    parser.add_argument("--title", default="Feature Search Result", help="Table title")

    args = parser.parse_args()

    rows = load_data(args.input)
    result = render_table(rows, args.output, args.title)

    print(json.dumps(result))


if __name__ == "__main__":
    main()

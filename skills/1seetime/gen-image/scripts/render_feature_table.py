import argparse
import json
import os
import sys
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO

def load_data(input_path):
    if not os.path.exists(input_path):
        print(json.dumps({"error": "invalid_input_file", "msg": f"File not found: {input_path}"}))
        sys.exit(1)
        
    with open(input_path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            print(json.dumps({"error": "invalid_input_file", "msg": "Invalid JSON format"}))
            sys.exit(1)
            
    if isinstance(data, list):
        return data
    elif isinstance(data, dict):
        if "search" in data and isinstance(data["search"], list):
            return data["search"]
        elif "frame_results" in data and isinstance(data["frame_results"], list):
            return data["frame_results"]
        elif "result" in data and isinstance(data["result"], dict):
            inner = data["result"].get("result", [])
            if isinstance(inner, list):
                return inner

    return []

def get_image(url, size=(160, 90)):
    try:
        if url.startswith("http"):
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            img = Image.open(BytesIO(response.content)).convert("RGB")
        else:
            # handle local path mock
            if os.path.exists(url):
                img = Image.open(url).convert("RGB")
            else:
                return Image.new("RGB", size, (200, 200, 200))
        img.thumbnail(size)
        return img
    except Exception:
        # return placeholder if failed
        return Image.new("RGB", size, (220, 220, 220))

def render_table(items, output_path, title):
    if not items:
        print(json.dumps({"error": "no_items", "msg": "No items found to render"}))
        sys.exit(1)

    # Basic settings
    cell_padding = 10
    row_height = 120
    header_height = 50
    title_height = 60
    
    col_widths = {
        "name": 200,
        "location": 250,
        "time": 200,
        "image": 180
    }
    
    table_width = sum(col_widths.values()) + (len(col_widths) + 1) * cell_padding
    table_height = title_height + header_height + (len(items) * row_height) + 20
    
    img = Image.new('RGB', (table_width, table_height), color='white')
    draw = ImageDraw.Draw(img)
    
    # Use default font
    try:
        font = ImageFont.truetype("arial.ttf", 16)
        title_font = ImageFont.truetype("arial.ttf", 24)
        header_font = ImageFont.truetype("arial.ttf", 18)
    except IOError:
        font = ImageFont.load_default()
        title_font = font
        header_font = font

    # Draw Title
    draw.text((cell_padding, cell_padding), title, font=title_font, fill='black')
    
    # Draw Headers
    y_offset = title_height
    headers = [("Camera Name", col_widths["name"]), ("Location", col_widths["location"]), 
               ("Time", col_widths["time"]), ("Image", col_widths["image"])]
    
    x_offset = cell_padding
    for h_text, width in headers:
        draw.text((x_offset, y_offset), h_text, font=header_font, fill='black')
        x_offset += width + cell_padding
        
    y_offset += header_height
    draw.line([(cell_padding, y_offset - 5), (table_width - cell_padding, y_offset - 5)], fill='black', width=2)
    
    # Draw Rows
    for item in items:
        x_offset = cell_padding
        
        # Name
        name = str(item.get("camera_name", item.get("name", "Unknown")))
        draw.text((x_offset, y_offset + row_height//2 - 10), name, font=font, fill='black')
        x_offset += col_widths["name"] + cell_padding
        
        # Location
        loc = str(item.get("location", "Unknown"))
        draw.text((x_offset, y_offset + row_height//2 - 10), loc, font=font, fill='black')
        x_offset += col_widths["location"] + cell_padding
        
        # Time
        ts = item.get("timestamp")
        if ts:
            try:
                dt = datetime.fromtimestamp(ts / 1000.0).strftime('%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError, OSError):
                dt = str(ts)
        else:
            dt = "N/A"
        draw.text((x_offset, y_offset + row_height//2 - 10), dt, font=font, fill='black')
        x_offset += col_widths["time"] + cell_padding
        
        # Image
        img_url = item.get("image_url", "")
        if img_url:
            thumbnail = get_image(img_url, size=(160, 90))
            # Paste image
            img.paste(thumbnail, (x_offset, y_offset + (row_height - thumbnail.height)//2))
            
        y_offset += row_height
        draw.line([(cell_padding, y_offset), (table_width - cell_padding, y_offset)], fill='lightgray', width=1)
        
    img.save(output_path)
    
    return {
        "status": "success",
        "output_path": output_path,
        "items_count": len(items)
    }

def main():
    parser = argparse.ArgumentParser(description="Render Feature Search Results to Table Image")
    parser.add_argument("--input", required=True, help="Input JSON file path")
    parser.add_argument("--output", default="result_report.png", help="Output PNG path")
    parser.add_argument("--title", default="Feature Search Result", help="Table title")
    
    args = parser.parse_args()
    
    items = load_data(args.input)
    result = render_table(items, args.output, args.title)
    
    print(json.dumps(result))

if __name__ == "__main__":
    main()

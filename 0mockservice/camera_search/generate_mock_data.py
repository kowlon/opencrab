import cv2
import numpy as np
import json
import os

CONFIG_DIR = "data/configs"
VIDEO_DIR = "data/videos"

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(VIDEO_DIR, exist_ok=True)

cameras = [
    {"id": "cam_001", "location": "园区东门"},
    {"id": "cam_002", "location": "园区南门"},
    {"id": "cam_003", "location": "食堂入口"},
    {"id": "cam_004", "location": "地下车库A区"},
]

# Write config file
with open(os.path.join(CONFIG_DIR, "cameras.json"), "w", encoding="utf-8") as f:
    json.dump(cameras, f, ensure_ascii=False, indent=2)

print("Generated cameras.json")

# Generate mock videos
width, height = 640, 480
fps = 30
duration = 2  # seconds
num_frames = duration * fps

for cam in cameras:
    video_filename = f"{cam['id']}_{cam['location']}.mp4"
    video_path = os.path.join(VIDEO_DIR, video_filename)
    
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(video_path, fourcc, fps, (width, height))
    
    for i in range(num_frames):
        # Create a frame with text
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        # Background color based on camera id hash to differentiate
        bg_color = (hash(cam['id']) % 255, hash(cam['location']) % 255, (hash(cam['id']) + hash(cam['location'])) % 255)
        frame[:] = bg_color
        
        # Put text
        cv2.putText(frame, f"Camera: {cam['id']}", (50, 100), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        cv2.putText(frame, f"Location: {cam['location']}", (50, 150), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        cv2.putText(frame, f"Frame: {i}", (50, 200), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        
        out.write(frame)
        
    out.release()
    print(f"Generated mock video: {video_filename}")

print("Mock data generation complete.")

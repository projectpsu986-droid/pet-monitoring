#!/usr/bin/env python3
"""
camera_bridge.py
Pull RTSP from a LAN camera and PUSH JPEG frames to your Render server.

Usage (Windows PowerShell example):
  setx RENDER_BASE_URL "https://pet-monitoring-xxxx.onrender.com"
  setx CAM_PUSH_TOKEN "your-long-random-token"
  setx RTSP_URL "rtsp://user:pass@192.168.1.50:554/..."
  setx ROOM_NAME "kitchen"
  setx CAMERA_INDEX "0"
  python camera_bridge.py

Optional:
  setx PUSH_INTERVAL_MS "700"     # default 700ms
  setx JPEG_QUALITY "80"          # default 80
  setx RESIZE_W "640"             # optional
  setx RESIZE_H "360"             # optional
"""
import os
import sys
import time
from typing import Optional

import requests

try:
    import cv2  # pip install opencv-python
except Exception as e:
    print("❌ ต้องติดตั้ง OpenCV ก่อน: pip install opencv-python")
    raise

def env_int(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    if not v:
        return default
    try:
        return int(v)
    except Exception:
        return default

def must_env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        print(f"❌ missing env: {name}")
        sys.exit(2)
    return v

def main():
    base_url = must_env("RENDER_BASE_URL").rstrip("/")
    token = os.environ.get("CAM_PUSH_TOKEN", "").strip()
    rtsp_url = must_env("RTSP_URL")
    room = os.environ.get("ROOM_NAME", "room").strip() or "room"
    index = env_int("CAMERA_INDEX", 0)

    interval_ms = env_int("PUSH_INTERVAL_MS", 700)
    quality = env_int("JPEG_QUALITY", 80)
    resize_w = env_int("RESIZE_W", 0)
    resize_h = env_int("RESIZE_H", 0)

    push_url = f"{base_url}/api/camera/push/{room}/{index}"

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    print("🔌 RTSP:", rtsp_url)
    print("🌐 PUSH:", push_url)
    print("⏱️ interval(ms):", interval_ms)

    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        print("❌ เปิด RTSP ไม่ได้ ตรวจ user/pass หรือ path stream")
        sys.exit(1)

    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]

    last_ok = time.time()
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            # try reconnect
            if time.time() - last_ok > 5:
                print("⚠️ อ่านเฟรมไม่ได้ กำลัง reconnect...")
            cap.release()
            time.sleep(1)
            cap = cv2.VideoCapture(rtsp_url)
            continue

        last_ok = time.time()

        if resize_w > 0 and resize_h > 0:
            frame = cv2.resize(frame, (resize_w, resize_h))

        ok2, buf = cv2.imencode(".jpg", frame, encode_params)
        if not ok2:
            time.sleep(interval_ms / 1000.0)
            continue

        files = {"frame": ("frame.jpg", buf.tobytes(), "image/jpeg")}
        try:
            r = requests.post(push_url, files=files, headers=headers, timeout=10)
            if r.status_code != 200:
                print("⚠️ push failed:", r.status_code, r.text[:200])
            # else: print(".", end="", flush=True)
        except Exception as e:
            print("⚠️ push error:", e)

        time.sleep(interval_ms / 1000.0)

if __name__ == "__main__":
    main()

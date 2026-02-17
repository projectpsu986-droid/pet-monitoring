#!/usr/bin/env python3
"""
camera_bridge.py
Pull RTSP from a LAN camera and PUSH JPEG frames to your Render server (bridge mode).

Required env:
  RENDER_BASE_URL   e.g. https://pet-monitoring-xxxx.onrender.com
  CAM_PUSH_TOKEN    must match Render env CAM_PUSH_TOKEN
  RTSP_URL          e.g. rtsp://user:pass@192.168.1.50:554/...
Optional env:
  ROOM_NAME         default "room"
  CAMERA_INDEX      default 0
  PUSH_INTERVAL_MS  default 250   (smaller = smoother but more CPU/bandwidth)
  JPEG_QUALITY      default 60    (smaller = faster)
  RESIZE_W / RESIZE_H optional resize for speed (e.g. 640x360)

This version is tuned to reduce RTSP buffering / latency:
- CAP_PROP_BUFFERSIZE=1 (when supported)
- grab() a few frames each loop to drop stale frames
- reconnect with exponential backoff
"""

import os
import sys
import time
from typing import Tuple

import requests

try:
    import cv2  # pip install opencv-python
except Exception:
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


def open_capture(rtsp_url: str):
    # Prefer FFMPEG backend when available (better RTSP behavior)
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG) if hasattr(cv2, "CAP_FFMPEG") else cv2.VideoCapture(rtsp_url)
    # Reduce internal buffering (not supported on all builds, but harmless if ignored)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass
    return cap


def split_interval(ms: int) -> float:
    # keep sane
    return max(0.05, min(ms / 1000.0, 5.0))


def main():
    base_url = must_env("RENDER_BASE_URL").rstrip("/")
    token = must_env("CAM_PUSH_TOKEN")  # required (avoid accidental public push)
    rtsp_url = must_env("RTSP_URL")
    room = os.environ.get("ROOM_NAME", "room").strip() or "room"
    index = env_int("CAMERA_INDEX", 0)

    interval_ms = env_int("PUSH_INTERVAL_MS", 250)
    quality = env_int("JPEG_QUALITY", 60)
    resize_w = env_int("RESIZE_W", 0)
    resize_h = env_int("RESIZE_H", 0)

    push_url = f"{base_url}/api/camera/push/{room}/{index}"
    headers = {"X-CAM-TOKEN": token}

    print("🔌 RTSP:", rtsp_url)
    print("🌐 PUSH:", push_url)
    print("⏱️ interval(ms):", interval_ms)
    print("🖼️ JPEG_QUALITY:", quality)

    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
    sleep_s = split_interval(interval_ms)

    backoff = 1.0
    cap = open_capture(rtsp_url)

    if not cap.isOpened():
        print("❌ เปิด RTSP ไม่ได้ ตรวจ user/pass หรือ path stream")
        sys.exit(1)

    last_push_ok = time.time()

    while True:
        # Drop stale frames: grab() is fast, retrieve() gets latest
        frame = None
        try:
            # grab a few frames to stay near real-time (tune 2-6)
            for _ in range(4):
                cap.grab()
            ok, frame = cap.retrieve()
        except Exception:
            ok, frame = False, None

        if not ok or frame is None:
            if time.time() - last_push_ok > 5:
                print(f"⚠️ อ่านเฟรมไม่ได้ กำลัง reconnect... (backoff={backoff:.1f}s)")
            try:
                cap.release()
            except Exception:
                pass
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 10.0)
            cap = open_capture(rtsp_url)
            continue

        backoff = 1.0  # reset on success

        if resize_w > 0 and resize_h > 0:
            try:
                frame = cv2.resize(frame, (resize_w, resize_h))
            except Exception:
                pass

        ok2, buf = cv2.imencode(".jpg", frame, encode_params)
        if not ok2:
            time.sleep(sleep_s)
            continue

        files = {"frame": ("frame.jpg", buf.tobytes(), "image/jpeg")}

        try:
            r = requests.post(push_url, files=files, headers=headers, timeout=10)
            if r.status_code != 200:
                print("⚠️ push failed:", r.status_code, r.text[:200])
            else:
                last_push_ok = time.time()
        except Exception as e:
            print("⚠️ push error:", e)

        time.sleep(sleep_s)


if __name__ == "__main__":
    main()

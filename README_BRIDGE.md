# Pet Monitoring - Render Free + LAN IP Camera (Bridge Mode)

Render (cloud) cannot access LAN IP cameras directly (192.168.x.x).  
Solution: run `camera_bridge.py` on a home PC that can see the camera, and PUSH JPEG frames to Render.

## On Render (Web Service)
Set these Environment Variables:
- DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME (as you already did)
- SECRET_KEY (generate)
- CAM_PUSH_TOKEN = (random long token, e.g. 32+ chars)

Deploy / redeploy.

## On Home PC (Windows)
Install dependencies:
- Python 3.10+
- `pip install opencv-python requests`

Set env (PowerShell):
- $env:RENDER_BASE_URL = "https://<your-app>.onrender.com"
- $env:CAM_PUSH_TOKEN  = "<same token as Render>"
- $env:RTSP_URL        = "rtsp://user:pass@192.168.1.50:554/..."  (your Vstarcam RTSP)
- $env:ROOM_NAME       = "room1"
- $env:CAMERA_INDEX    = "0"

Run:
- python camera_bridge.py

## On Web UI
The UI will show the latest JPEG at:
- /camera_latest/<room>/<index>.jpg
and auto-refresh every ~700ms.

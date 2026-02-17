import os, time, cv2, requests

BASE = os.environ.get("RENDER_BASE_URL","").rstrip("/")
TOKEN = os.environ.get("CAM_PUSH_TOKEN","")
RTSP  = os.environ.get("RTSP_URL","")
ROOM  = os.environ.get("ROOM_NAME","garage")
IDX   = os.environ.get("CAMERA_INDEX","0")

INTERVAL_MS = int(os.environ.get("PUSH_INTERVAL_MS","250"))  # 200-400 แนะนำ
JPEG_Q      = int(os.environ.get("JPEG_QUALITY","60"))       # 50-70 แนะนำ
GRAB_N      = int(os.environ.get("GRAB_N","8"))              # ทิ้งเฟรมเก่าแรงขึ้น

if not all([BASE, TOKEN, RTSP]):
    raise SystemExit("❌ missing env: RENDER_BASE_URL / CAM_PUSH_TOKEN / RTSP_URL")

PUSH_URL = f"{BASE}/api/camera/push/{ROOM}/{IDX}"
print("🔌 RTSP:", RTSP)
print("🌐 PUSH:", PUSH_URL)
print("⏱️ interval(ms):", INTERVAL_MS)

def open_cap():
    cap = cv2.VideoCapture(RTSP)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass
    return cap

cap = open_cap()
backoff = 1.0

while True:
    if not cap.isOpened():
        print(f"⚠️ open failed, retry in {backoff}s")
        time.sleep(backoff)
        backoff = min(backoff*2, 20)
        cap = open_cap()
        continue
    backoff = 1.0

    # ทิ้งเฟรมเก่าให้เยอะขึ้น แล้วค่อยดึงเฟรมล่าสุด
    ok = True
    for _ in range(GRAB_N):
        ok = cap.grab()
        if not ok:
            break
    if not ok:
        print("⚠️ grab failed, reconnect...")
        cap.release()
        cap = open_cap()
        continue

    ok, frame = cap.retrieve()
    if not ok:
        print("⚠️ retrieve failed, reconnect...")
        cap.release()
        cap = open_cap()
        continue

    ok, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_Q])
    if not ok:
        time.sleep(INTERVAL_MS/1000)
        continue

    try:
        r = requests.post(
            PUSH_URL,
            headers={"X-CAM-TOKEN": TOKEN},
            files={"frame": ("frame.jpg", enc.tobytes(), "image/jpeg")},
            timeout=10,
        )
        if r.status_code != 200:
            print("⚠️ push failed:", r.status_code, r.text[:200])
    except Exception as e:
        print("⚠️ push exception:", e)

    time.sleep(INTERVAL_MS/1000)

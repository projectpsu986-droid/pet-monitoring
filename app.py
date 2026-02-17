from flask import Flask, Response, jsonify, request, send_from_directory, session
from flask_cors import CORS
import mysql.connector
import hmac
import random
import string

try:
    import cv2 as _cv2  # type: ignore
except Exception:  # pragma: no cover
    _cv2 = None


def _get_cv2():
    """Return a validated cv2 module or None.

    We validate presence of VideoCapture and imencode. If missing, it's usually an
    environment/package issue (e.g., wrong 'cv2' module shadowing OpenCV).
    """
    if _cv2 is None:
        return None
    if not hasattr(_cv2, "VideoCapture") or not hasattr(_cv2, "imencode"):
        return None
    return _cv2
from threading import Lock
import threading
import time as time_module
from datetime import datetime, date, time, timedelta
import calendar
from typing import Optional
import os
import secrets
import hashlib
import smtplib
from email.message import EmailMessage


import zipfile
import uuid
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# Web Push
from pywebpush import webpush, WebPushException
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
import base64
import json


# =========================================
# Static/Assets (cat images)
# =========================================
# The frontend must load cat images by URL. We serve files from ./Colorcat
# and user uploads will be saved under ./Colorcat/uploads
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "Colorcat")
ASSETS_ZIP = os.path.join(BASE_DIR, "Colorcat.zip")
UPLOADS_DIR = os.path.join(ASSETS_DIR, "uploads")

# Limit upload size (10 MB)
app_max_bytes = 10 * 1024 * 1024

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def _ensure_assets_folder():
    """Make sure Colorcat/ exists (extract from Colorcat.zip if needed)."""
    try:
        if os.path.isdir(ASSETS_DIR):
            os.makedirs(UPLOADS_DIR, exist_ok=True)
            return
        if os.path.isfile(ASSETS_ZIP):
            os.makedirs(ASSETS_DIR, exist_ok=True)
            with zipfile.ZipFile(ASSETS_ZIP, "r") as zf:
                zf.extractall(BASE_DIR)
            os.makedirs(UPLOADS_DIR, exist_ok=True)
    except Exception as e:  # pragma: no cover
        print("⚠️  cannot prepare assets folder:", e)


def _basename_from_any_path(p: str) -> str:
    if not p:
        return ""
    p = str(p).strip()
    if not p:
        return ""
    p = p.replace("\\", "/")
    return os.path.basename(p)


def _is_probably_url(s: str) -> bool:
    s = (s or "").strip().lower()
    return s.startswith("http://") or s.startswith("https://") or s.startswith("data:")


def normalize_image_to_url(val: str) -> str:
    """Normalize DB value to something the browser can load.

    Supported:
      - http(s)://... or data:... -> keep
      - /assets/... -> keep
      - any filesystem path -> convert to /assets/<filename> or /assets/uploads/<filename>
    """
    if val is None:
        return None
    v = str(val).strip()
    if not v:
        return None

    if _is_probably_url(v) or v.startswith("/assets/"):
        return v

    v_low = v.lower().replace("\\", "/")
    filename = _basename_from_any_path(v)
    if not filename:
        return None

    # If the old value hints it was an uploaded file, keep it under uploads/
    if "uploads" in v_low:
        return f"/assets/uploads/{filename}"

    return f"/assets/{filename}"


def _allowed_file(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[-1].lower()
    return ext in ALLOWED_EXTENSIONS

# Optional: APScheduler for in-app daily run (23:59).
# If not installed, the app will still run; daily scheduler will be disabled.
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
except Exception:  # pragma: no cover
    BackgroundScheduler = None
    CronTrigger = None


app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
app.secret_key = app.config['SECRET_KEY']
CORS(app, supports_credentials=True)


# =========================================
# C) DB CONFIG
# =========================================
# NOTE: Web Push needs DB access very early (startup) to ensure the
# push_subscriptions table exists. Therefore db_config/get_db must be
# defined before the Web Push init code runs.
db_config = {
    # Default values are for local development.
    # On Render, set these as environment variables.
    "host": os.environ.get("DB_HOST", "localhost"),
    "user": os.environ.get("DB_USER", "root"),
    "password": os.environ.get("DB_PASSWORD", "root"),
    "database": os.environ.get("DB_NAME", "pet_monitoring"),
    "port": int(os.environ.get("DB_PORT", "3306")),
}
def get_db():
    """Create a MySQL connection using db_config."""
    return mysql.connector.connect(**db_config)

# ============================================================
# Web Push (VAPID) setup
# - Works on HTTPS or localhost
# - Stores VAPID keys in vapid_keys.json
# - Stores subscriptions in MySQL table push_subscriptions
# ============================================================
VAPID_KEYS_PATH = os.path.join(BASE_DIR, "vapid_keys.json")
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _ensure_vapid_keys() -> dict:
    """Create (if missing) and load VAPID keys.

    Returns dict: {"publicKey": <base64url>, "privateKeyPem": <pem str>}
    """
    try:
        if os.path.isfile(VAPID_KEYS_PATH):
            with open(VAPID_KEYS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("publicKey") and data.get("privateKeyPem"):
                return data
    except Exception:
        pass

    # Generate new keys (P-256)
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_b64url = _b64url_encode(public_bytes)

    data = {"publicKey": public_b64url, "privateKeyPem": private_pem}
    try:
        with open(VAPID_KEYS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        print("⚠️ cannot write vapid keys:", e)
    return data


def _ensure_push_table():
    """Create subscriptions table if not exists and ensure required columns exist.

    Older versions created the table without `user_id`. We keep it backward-
    compatible by creating the new schema and then (best-effort) migrating the
    missing column/index.
    """
    conn = None
    cur = None
    try:
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("USE `%s`" % db_config.get("database"))
        except Exception:
            pass

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS push_subscriptions (
              id INT AUTO_INCREMENT PRIMARY KEY,
              user_id INT NULL,
              endpoint TEXT NOT NULL,
              p256dh VARCHAR(255) NOT NULL,
              auth VARCHAR(255) NOT NULL,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              UNIQUE KEY uniq_endpoint (endpoint(255)),
              KEY idx_user_id (user_id)
            )
            """
        )

        # Best-effort migration for older schema
        try:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'push_subscriptions'
                  AND COLUMN_NAME = 'user_id'
                """
            )
            has_user_id = (cur.fetchone() or [0])[0] > 0
            if not has_user_id:
                cur.execute("ALTER TABLE push_subscriptions ADD COLUMN user_id INT NULL")
        except Exception:
            pass

        try:
            # ensure index exists
            cur.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'push_subscriptions'
                  AND INDEX_NAME = 'idx_user_id'
                """
            )
            has_idx = (cur.fetchone() or [0])[0] > 0
            if not has_idx:
                cur.execute("ALTER TABLE push_subscriptions ADD INDEX idx_user_id (user_id)")
        except Exception:
            pass

        conn.commit()
    except Exception as e:
        print("⚠️ cannot ensure push_subscriptions table:", e)
    finally:
        try:
            if cur:
                cur.close()
        finally:
            if conn:
                conn.close()




# Create push_subscriptions table at import-time as well (safe in dev/reloader).
try:
    _ensure_push_table()
except Exception as e:  # pragma: no cover
    print("⚠️ cannot ensure push_subscriptions table at startup:", e)


def _get_all_subscriptions() -> list[dict]:
    """Return all stored subscriptions (used for admin broadcast)."""
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT endpoint, p256dh, auth, user_id FROM push_subscriptions")
        return cur.fetchall() or []
    finally:
        cur.close()
        conn.close()


def _get_subscriptions_for_user(user_id: int) -> list[dict]:
    """Return subscriptions for a specific user."""
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT endpoint, p256dh, auth, user_id FROM push_subscriptions WHERE user_id=%s",
            (int(user_id),),
        )
        return cur.fetchall() or []
    finally:
        cur.close()
        conn.close()


def _delete_subscription_by_endpoint(endpoint: str, user_id: int | None = None):
    if not endpoint:
        return
    conn = get_db()
    cur = conn.cursor()
    try:
        if user_id is None:
            cur.execute("DELETE FROM push_subscriptions WHERE endpoint=%s", (endpoint,))
        else:
            cur.execute(
                "DELETE FROM push_subscriptions WHERE endpoint=%s AND user_id=%s",
                (endpoint, int(user_id)),
            )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _send_web_push_to_user(user_id: int, title: str, body: str, url: str = "/") -> int:
    """Send push to a specific user's subscriptions. Returns count of successful sends."""
    keys = _ensure_vapid_keys()
    vapid_private_key = keys["privateKeyPem"]
    vapid_claims = {"sub": VAPID_SUBJECT}

    payload = json.dumps({"title": title, "body": body, "url": url})
    ok = 0
    for sub in _get_subscriptions_for_user(int(user_id)):
        info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        try:
            webpush(
                subscription_info=info,
                data=payload,
                vapid_private_key=vapid_private_key,
                vapid_claims=vapid_claims,
            )
            ok += 1
        except WebPushException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410):
                _delete_subscription_by_endpoint(sub["endpoint"], user_id=int(user_id))
        except Exception:
            pass
    return ok


def _send_web_push_to_all(title: str, body: str, url: str = "/") -> int:
    """Send push to all subscriptions (admin broadcast). Returns count of successful sends."""
    keys = _ensure_vapid_keys()
    vapid_private_key = keys["privateKeyPem"]
    vapid_claims = {"sub": VAPID_SUBJECT}

    payload = json.dumps({"title": title, "body": body, "url": url})
    ok = 0
    for sub in _get_all_subscriptions():
        info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        try:
            webpush(
                subscription_info=info,
                data=payload,
                vapid_private_key=vapid_private_key,
                vapid_claims=vapid_claims,
            )
            ok += 1
        except WebPushException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410):
                _delete_subscription_by_endpoint(sub["endpoint"], user_id=sub.get("user_id"))
        except Exception:
            pass
    return ok



# =========================================
# Automatic Alert Push Worker (Facebook-like)
# - Runs periodically in the background and pushes NEW alerts automatically
# - Does NOT require the user to open /api/alerts
# =========================================

ALERT_PUSH_WORKER_ENABLED = os.environ.get("ALERT_PUSH_WORKER_ENABLED", "1") != "0"
# How often to check realtime alerts (seconds)
ALERT_PUSH_INTERVAL_SECONDS = int(os.environ.get("ALERT_PUSH_INTERVAL_SECONDS", "30") or 30)
# How often to re-evaluate "daily behavior" alerts for today (seconds)
ALERT_PUSH_DAILY_CHECK_SECONDS = int(os.environ.get("ALERT_PUSH_DAILY_CHECK_SECONDS", "600") or 600)


def _ensure_notification_state_table():
    """Store small key/value state for background jobs (MySQL)."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_state (
              k VARCHAR(64) PRIMARY KEY,
              v VARCHAR(255) NULL,
              updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        try:
            cur.close()
        finally:
            conn.close()


def _state_get(key: str, default: str = "") -> str:
    _ensure_notification_state_table()
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT v FROM notification_state WHERE k=%s LIMIT 1", (key,))
        row = cur.fetchone() or {}
        return (row.get("v") or default)
    finally:
        cur.close()
        conn.close()


def _state_set(key: str, value: str):
    _ensure_notification_state_table()
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO notification_state (k, v) VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE v=VALUES(v)
            """,
            (key, value),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _fetch_new_alerts_since(cursor, last_id: int, limit: int = 5) -> list[dict]:
    """Return newest alerts inserted after last_id (excluding deleted/archived)."""
    cursor.execute(
        """
        SELECT id, cat_name, alert_type, message, created_at
        FROM alerts_log
        WHERE id > %s AND is_read <> 2
        ORDER BY id ASC
        LIMIT %s
        """,
        (int(last_id), int(limit)),
    )
    return cursor.fetchall() or []


def _get_latest_alert_id(cursor) -> int:
    cursor.execute("SELECT COALESCE(MAX(id), 0) AS mx FROM alerts_log WHERE is_read <> 2")
    row = cursor.fetchone() or {}
    return int(row.get("mx") or 0)


def _compose_push_from_alerts(rows: list[dict]) -> tuple[str, str]:
    """Create a single push title/body from a list of new alert rows."""
    if not rows:
        return ("Pet Monitoring", "มีการแจ้งเตือนใหม่")
    if len(rows) == 1:
        a = rows[0]
        cat = (a.get("cat_name") or "").strip()
        msg = (a.get("message") or "").strip()
        title = f"แจ้งเตือน: {cat}" if cat else "Pet Monitoring"
        body = msg or "มีการแจ้งเตือนใหม่"
        return (title, body)

    a = rows[0]
    cat = (a.get("cat_name") or "").strip()
    msg = (a.get("message") or "").strip()
    head = f"{cat}: {msg}" if cat and msg else (msg or "มีการแจ้งเตือนใหม่")
    return ("Pet Monitoring", f"มีการแจ้งเตือนใหม่ {len(rows)} รายการ • {head}")


def _alert_push_worker_loop():
    """Background loop: ingest alerts and push only when NEW alerts appear."""
    # state keys
    STATE_LAST_PUSH_ID = "last_alert_push_id"
    last_push_id = 0
    try:
        last_push_id = int(_state_get(STATE_LAST_PUSH_ID, "0") or 0)
    except Exception:
        last_push_id = 0

    last_daily_check_at = 0.0

    while True:
        try:
            connection = mysql.connector.connect(**db_config)
            cursor = connection.cursor(dictionary=True)
            try:
                inserted = 0

                # 1) realtime: no_cat
                try:
                    inserted += int(_ingest_realtime_no_cat(cursor) or 0)
                except Exception:
                    pass

                # 2) periodic: daily behavior (eat/excrete) for today
                now_ts = time_module.time()
                if now_ts - last_daily_check_at >= float(ALERT_PUSH_DAILY_CHECK_SECONDS):
                    last_daily_check_at = now_ts
                    try:
                        inserted += int(_ingest_daily_behavior_for_day(cursor, date.today()) or 0)
                    except Exception:
                        pass

                connection.commit()

                # 3) detect new alerts since last push
                latest_id = _get_latest_alert_id(cursor)
                if latest_id > int(last_push_id):
                    new_rows = _fetch_new_alerts_since(cursor, int(last_push_id), limit=5)
                    title, body = _compose_push_from_alerts(new_rows)
                    try:
                        _send_web_push_to_all(title=title, body=body, url="/?page=notifications")
                    except Exception:
                        pass
                    try:
                        _send_line_push_to_all_linked(title=title, body=body, url="/?page=notifications")
                    except Exception:
                        pass
                    last_push_id = int(latest_id)
                    try:
                        _state_set(STATE_LAST_PUSH_ID, str(last_push_id))
                    except Exception:
                        pass
            finally:
                try:
                    cursor.close()
                finally:
                    connection.close()
        except Exception:
            # swallow exceptions to keep the worker alive
            pass

        time_module.sleep(max(5, int(ALERT_PUSH_INTERVAL_SECONDS)))


def _start_alert_push_worker():
    """Start background worker once (avoid Flask reloader double-start)."""
    if not ALERT_PUSH_WORKER_ENABLED:
        return

    # When using Flask debug reloader, the module imports twice.
    # Only start in the "main" process.
    if os.environ.get("FLASK_DEBUG") == "1" or os.environ.get("WERKZEUG_RUN_MAIN") is not None:
        # If WERKZEUG_RUN_MAIN exists, it will be "true" in the reloader child process.
        if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
            return

    t = threading.Thread(target=_alert_push_worker_loop, daemon=True, name="alert-push-worker")
    t.start()


# Ensure subscription table exists (run once, after the module has fully loaded).
#
# On Windows/Flask reloader, import order + hot-reload can sometimes execute
# startup code earlier than expected. If _ensure_push_table() runs while the
# module is still loading, it may raise NameError (e.g., get_db not defined).
# Running this in a request hook guarantees the module finished importing.
_push_init_done = False


@app.before_request
def _init_push_once():
    global _push_init_done
    if _push_init_done:
        return
    _push_init_done = True
    _ensure_push_table()


# Prepare local assets folder (Colorcat images)
_ensure_assets_folder()
app.config["MAX_CONTENT_LENGTH"] = app_max_bytes


@app.route("/assets/<path:filename>")
def assets(filename):
    """Serve local cat images as HTTP URLs.

    Supported URLs:
      - /assets/<file>              -> searches Colorcat/ then Colorcat/uploads/
      - /assets/uploads/<file>      -> searches Colorcat/uploads/
    """
    # Normalise slashes
    filename = (filename or "").replace("\\", "/")

    # 1) direct hit: Colorcat/<filename>
    try:
        full = os.path.join(ASSETS_DIR, filename)
        if os.path.isfile(full):
            return send_from_directory(ASSETS_DIR, filename)

        base = os.path.basename(filename)
        sub = os.path.dirname(filename).replace("\\", "/").strip("/")

        wanted = base.lower()

        # 2) If explicitly under uploads/, serve from uploads
        if sub.lower() == "uploads":
            if os.path.isdir(UPLOADS_DIR):
                for f in os.listdir(UPLOADS_DIR):
                    if f.lower() == wanted:
                        return send_from_directory(ASSETS_DIR, f"uploads/{f}")

        # 3) Fallback: if request is /assets/<file> but the file is actually in uploads/
        if os.path.isdir(UPLOADS_DIR):
            for f in os.listdir(UPLOADS_DIR):
                if f.lower() == wanted:
                    return send_from_directory(ASSETS_DIR, f"uploads/{f}")

        # 4) Case-insensitive fallback for root folder
        if os.path.isdir(ASSETS_DIR):
            for f in os.listdir(ASSETS_DIR):
                if os.path.isfile(os.path.join(ASSETS_DIR, f)) and f.lower() == wanted:
                    return send_from_directory(ASSETS_DIR, f)
    except Exception:
        # let flask return 404 below
        pass

    return ("Not Found", 404)


# =========================================
# Frontend static files (same-origin)
# =========================================
@app.route("/")
def serve_index():
    return send_from_directory(BASE_DIR, "index.html")


# Also serve index.html explicitly to avoid redirect loops when some clients navigate to /index.html
@app.route("/index.html")
def serve_index_html():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/script.js")
def serve_script():
    return send_from_directory(BASE_DIR, "script.js")


@app.route("/style.css")
def serve_style():
    return send_from_directory(BASE_DIR, "style.css")


@app.route("/sw.js")
def serve_sw():
    # Service worker must be served from the same origin and root scope
    resp = send_from_directory(BASE_DIR, "sw.js")
    resp.headers["Cache-Control"] = "no-cache"
    return resp

@app.route("/login.html")
def serve_login():
    return send_from_directory(BASE_DIR, "login.html")


@app.route("/auth.js")
def serve_auth_js():
    return send_from_directory(BASE_DIR, "auth.js")


@app.route("/admin.html")
def serve_admin():
    return send_from_directory(BASE_DIR, "admin.html")


@app.route("/admin.js")
def serve_admin_js():
    return send_from_directory(BASE_DIR, "admin.js")


# =========================================
# A) CAMERA CONFIG
# =========================================
# แก้/เพิ่มกล้องได้ที่นี่ (ห้อง: garden, garage, kitchen, hall)
ROOMS_CFG = [
    {
        "name": "garden",
        "cameras": [
            {"label": "Cam1", "rtsp_url": "rtsp://user:pass@10.0.0.10/stream1"},
            {"label": "Cam2", "rtsp_url": "rtsp://user:pass@10.0.0.11/stream1"},
        ],
    },
    {
        "name": "garage",
        "cameras": [
            {"label": "Cam3", "rtsp_url": "rtsp://admin:05032544@10.56.223.41:10554/tcp/av0_0"},
        ],
    },
    {
        "name": "kitchen",
        "cameras": [
            {"label": "Cam4", "rtsp_url": "rtsp://user:pass@10.0.0.13/stream1"},
        ],
    },
    {
        "name": "hall",
        "cameras": [
            {"label": "Cam5", "rtsp_url": "rtsp://admin:05032544@192.168.22.94:10554/tcp/av0_0"},
        ],
    },
]

_cam_lock = Lock()


def get_rtsp_by_room_index(room_name: str, index: int):
    with _cam_lock:
        room = next((r for r in ROOMS_CFG if r.get("name") == room_name), None)
        if not room:
            return None
        cams = room.get("cameras", [])
        if index < 0 or index >= len(cams):
            return None
        return cams[index].get("rtsp_url")


# =========================================
# B) RTSP STREAMING (MJPEG)
# =========================================
def generate_frames_rtsp(rtsp_url: str):
    """อ่านเฟรมจาก RTSP แล้วส่งเป็น MJPEG multipart

    หมายเหตุ: ฟังก์ชันนี้เป็น generator (streamed response)
    ดังนั้น *ห้าม* ปล่อย exception หลุดออกไปหลังส่ง headers แล้ว
    เพราะจะเกิด error แบบ "headers were already sent" ใน Werkzeug.
    """
    cv2 = _get_cv2()
    if cv2 is None:
        # Should be pre-checked by the route, but keep it safe.
        return

    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        print("❌ ไม่สามารถเชื่อมต่อกล้อง RTSP ได้:", rtsp_url)
        return
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            ret, buf = cv2.imencode(".jpg", frame)
            if not ret:
                break
            yield (
                b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                + buf.tobytes()
                + b"\r\n"
            )
    except Exception as e:  # pragma: no cover
        # Don't let streaming crash after headers are sent.
        print("❌ RTSP streaming error:", e)
    finally:
        try:
            cap.release()
        except Exception:
            pass


# สตรีมกล้องตามห้อง/ลำดับกล้อง (index เริ่ม 0)
@app.route("/video_feed/<room_name>/<int:index>")
def video_feed_room_index(room_name, index):
    rtsp = get_rtsp_by_room_index(room_name, index)
    if not rtsp:
        return Response("camera not found", status=404)

    # Validate OpenCV availability *before* starting a streamed response.
    # This prevents Werkzeug's "headers already sent" error.
    if _get_cv2() is None:
        return Response(
            "OpenCV (cv2) ไม่พร้อมใช้งาน: ไม่พบ VideoCapture/imencode.\n"
            "แก้ไข: ถอนแพ็กเกจ cv2 แปลกๆ (ถ้ามี) แล้วติดตั้ง opencv-python หรือ opencv-python-headless\n"
            "ตัวอย่าง: pip uninstall -y cv2 && pip install -U opencv-python\n",
            status=500,
            mimetype="text/plain; charset=utf-8",
        )
    return Response(
        generate_frames_rtsp(rtsp),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


# ให้หน้าเว็บโหลดรายการห้อง–กล้อง (ไม่เปิดเผย RTSP)
@app.route("/api/rooms", methods=["GET"])
def api_rooms():
    with _cam_lock:
        result = [
            {
                "name": r["name"],
                "cameras": [
                    {"label": c.get("label", f"Camera {i+1}"), "index": i}
                    for i, c in enumerate(r.get("cameras", []))
                ],
            }
            for r in ROOMS_CFG
        ]
    return jsonify(result)


# =========================================
# Web Push APIs
# =========================================
@app.route('/api/push/vapid_public_key', methods=['GET'])
def push_vapid_public_key():
    keys = _ensure_vapid_keys()
    return jsonify({"publicKey": keys['publicKey']})



@app.route('/api/push/subscribe', methods=['POST'])
def push_subscribe():
    """Bind a browser push subscription to the *currently logged-in* user."""
    req = _require_login()
    if req:
        return req

    sub = request.get_json(silent=True) or {}
    endpoint = sub.get('endpoint')
    keys = (sub.get('keys') or {})
    p256dh = keys.get('p256dh')
    auth = keys.get('auth')
    if not (endpoint and p256dh and auth):
        return jsonify({"ok": False, "error": "invalid_subscription"}), 400

    user_id = int(session.get('user_id'))

    _ensure_push_table()
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE user_id=VALUES(user_id), p256dh=VALUES(p256dh), auth=VALUES(auth)
            """,
            (user_id, endpoint, p256dh, auth),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()

    return jsonify({"ok": True})


@app.route('/api/push/unsubscribe', methods=['POST'])
def push_unsubscribe():
    req = _require_login()
    if req:
        return req

    data = request.get_json(silent=True) or {}
    endpoint = data.get('endpoint')
    if endpoint:
        _delete_subscription_by_endpoint(endpoint, user_id=int(session.get('user_id')))
    return jsonify({"ok": True})


@app.route('/api/push/test', methods=['POST'])
def push_test():
    """Send a test notification to the current user only (used by Notification Settings page)."""
    req = _require_login()
    if req:
        return req

    data = request.get_json(silent=True) or {}
    title = data.get('title') or 'Pet Monitoring'
    body = data.get('body') or 'Test notification'
    url = data.get('url') or '/'
    sent = _send_web_push_to_user(int(session.get('user_id')), title, body, url)
    return jsonify({"ok": True, "sent": sent})


@app.route('/api/push/broadcast', methods=['POST'])
def push_broadcast():
    """Admin-only broadcast to all subscriptions."""
    req = _require_admin()
    if req:
        return req

    data = request.get_json(silent=True) or {}
    title = data.get('title') or 'Pet Monitoring'
    body = data.get('body') or 'Broadcast notification'
    url = data.get('url') or '/'
    sent = _send_web_push_to_all(title, body, url)
    return jsonify({"ok": True, "sent": sent})

# ============================================================
# LINE Messaging API (Alternative channel to notify users)
# - LINE Notify was terminated on Mar 31, 2025. Use Messaging API instead.
# - Requires:
#     LINE_CHANNEL_ACCESS_TOKEN (long-lived channel access token)
#     LINE_CHANNEL_SECRET (for webhook signature verification; optional but recommended)
# - Linking method:
#     1) User generates a one-time code in the web app
#     2) User adds your LINE Official Account (bot) and sends that code to the bot
#     3) Server receives webhook, links LINE userId -> logged-in user_id
# ============================================================
import urllib.request
import urllib.error

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "").strip().rstrip("/")



def _ensure_line_tables():
    """Create tables needed for LINE linking."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS line_links (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                line_user_id VARCHAR(64) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uniq_user (user_id),
                UNIQUE KEY uniq_line_user (line_user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS line_link_codes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                code VARCHAR(16) NOT NULL,
                expires_at DATETIME NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uniq_code (code),
                KEY idx_user (user_id),
                KEY idx_exp (expires_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _line_verify_signature(body_bytes: bytes, signature_b64: str) -> bool:
    """Verify X-Line-Signature. If LINE_CHANNEL_SECRET is empty, skip verification (dev mode)."""
    if not LINE_CHANNEL_SECRET:
        return True
    try:
        mac = hmac.new(LINE_CHANNEL_SECRET.encode("utf-8"), body_bytes, hashlib.sha256).digest()
        expected = base64.b64encode(mac).decode("utf-8")
        return hmac.compare_digest(expected, signature_b64 or "")
    except Exception:
        return False


def _line_api_post(path: str, payload: dict) -> tuple[int, str]:
    """POST to LINE Messaging API. Returns (status_code, response_text)."""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        return (0, "LINE_CHANNEL_ACCESS_TOKEN not set")

    url = "https://api.line.me/v2/bot" + path
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return (resp.status, resp.read().decode("utf-8", errors="ignore"))
    except urllib.error.HTTPError as e:
        try:
            return (e.code, e.read().decode("utf-8", errors="ignore"))
        except Exception:
            return (e.code, str(e))
    except Exception as e:
        return (0, str(e))


def _line_get_user_id_for_user(user_id: int):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT line_user_id FROM line_links WHERE user_id=%s LIMIT 1", (int(user_id),))
        row = cur.fetchone()
        return row["line_user_id"] if row else None
    finally:
        cur.close()
        conn.close()


def _line_abs_url(url: str) -> str:
    """Convert relative URL like '/?page=notifications' to absolute URL for LINE rendering."""
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith("http://") or u.startswith("https://"):
        return u
    # If base URL not configured, return as-is (LINE may not linkify relative paths)
    if not globals().get("APP_BASE_URL",""):
        return u
    if not u.startswith("/"):
        u = "/" + u
    return globals().get("APP_BASE_URL","") + u


def _send_line_push_to_user(user_id: int, title: str, body: str, url: str = "/") -> bool:
    """Push LINE message to a linked user."""
    line_user_id = _line_get_user_id_for_user(int(user_id))
    if not line_user_id:
        return False

    text = f"{title}\n{body}".strip()
    if url:
        text += f"\n{_line_abs_url(url)}"

    status, _ = _line_api_post(
        "/message/push",
        {"to": line_user_id, "messages": [{"type": "text", "text": text[:4800]}]},
    )
    return status in (200, 201)


def _send_line_push_to_all_linked(title: str, body: str, url: str = "/") -> int:
    """Push LINE message to all linked users. Returns count of successful sends."""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        return 0
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    ok = 0
    try:
        cur.execute("SELECT user_id FROM line_links")
        users = cur.fetchall() or []
    finally:
        cur.close()
        conn.close()

    for u in users:
        try:
            if _send_line_push_to_user(int(u["user_id"]), title, body, url):
                ok += 1
        except Exception:
            pass
    return ok


@app.route("/api/line/link_code", methods=["POST"])
def line_link_code():
    """Create a short-lived link code for the current logged-in user."""
    req = _require_login()
    if req:
        return req

    _ensure_line_tables()
    user_id = int(session["user_id"])

    code = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    expires_at = datetime.utcnow() + timedelta(minutes=10)

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM line_link_codes WHERE user_id=%s", (user_id,))
        cur.execute(
            "INSERT INTO line_link_codes (user_id, code, expires_at) VALUES (%s, %s, %s)",
            (user_id, code, expires_at.strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()

    return jsonify({"ok": True, "code": code, "expires_in_seconds": 600})


@app.route("/api/line/status", methods=["GET"])
def line_status():
    req = _require_login()
    if req:
        return req
    _ensure_line_tables()
    user_id = int(session["user_id"])
    linked = bool(_line_get_user_id_for_user(user_id))
    return jsonify({"ok": True, "linked": linked})

@app.route("/api/line/test", methods=["POST"])
def line_test():
    """Send a test LINE push message to the currently linked user (for debugging)."""
    req = _require_login()
    if req:
        return req
    _ensure_line_tables()

    user_id = int(session["user_id"])
    line_user_id = _line_get_user_id_for_user(user_id)
    if not line_user_id:
        return jsonify({"ok": False, "error": "not_linked"}), 400

    if not LINE_CHANNEL_ACCESS_TOKEN:
        return jsonify({"ok": False, "error": "LINE_CHANNEL_ACCESS_TOKEN not set"}), 500

    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "Pet Monitoring").strip() or "Pet Monitoring"
    body = str(data.get("body") or "ทดสอบแจ้งเตือนจากระบบ").strip() or "ทดสอบแจ้งเตือนจากระบบ"
    url = str(data.get("url") or "/?page=notifications").strip()

    text = f"{title}\n{body}".strip()
    if url:
        text += f"\n{_line_abs_url(url)}"

    status, resp_text = _line_api_post(
        "/message/push",
        {"to": line_user_id, "messages": [{"type": "text", "text": text[:4800]}]},
    )

    if status not in (200, 201):
        return jsonify({"ok": False, "error": "send_failed", "status": status, "response": resp_text}), 500

    return jsonify({"ok": True, "status": status})


@app.route("/line/webhook", methods=["POST"])
def line_webhook():
    """Webhook for LINE Messaging API. Links LINE userId to our user_id using one-time code."""
    _ensure_line_tables()

    body_bytes = request.get_data() or b""
    signature = request.headers.get("X-Line-Signature", "")
    if not _line_verify_signature(body_bytes, signature):
        return Response("bad signature", status=400)

    payload = request.get_json(silent=True) or {}
    events = payload.get("events") or []

    for ev in events:
        try:
            if ev.get("type") != "message":
                continue
            msg = ev.get("message") or {}
            if msg.get("type") != "text":
                continue
            text = (msg.get("text") or "").strip().upper()
            if not text:
                continue

            line_user_id = (ev.get("source") or {}).get("userId")
            if not line_user_id:
                continue

            conn = get_db()
            cur = conn.cursor(dictionary=True)
            try:
                cur.execute("DELETE FROM line_link_codes WHERE expires_at < UTC_TIMESTAMP()")
                cur.execute(
                    "SELECT user_id FROM line_link_codes WHERE code=%s AND expires_at >= UTC_TIMESTAMP() LIMIT 1",
                    (text,),
                )
                row = cur.fetchone()
                if not row:
                    conn.commit()
                    continue

                user_id = int(row["user_id"])
                cur.execute("DELETE FROM line_links WHERE user_id=%s", (user_id,))
                cur.execute("DELETE FROM line_links WHERE line_user_id=%s", (line_user_id,))
                cur.execute("INSERT INTO line_links (user_id, line_user_id) VALUES (%s, %s)", (user_id, line_user_id))
                cur.execute("DELETE FROM line_link_codes WHERE code=%s", (text,))
                conn.commit()
            finally:
                cur.close()
                conn.close()

            reply_token = ev.get("replyToken")
            if reply_token and LINE_CHANNEL_ACCESS_TOKEN:
                _line_api_post(
                    "/message/reply",
                    {
                        "replyToken": reply_token,
                        "messages": [{"type": "text", "text": "✅ เชื่อมต่อสำเร็จ! จากนี้ระบบจะส่งแจ้งเตือนผ่าน LINE ให้คุณอัตโนมัติ"}],
                    },
                )
        except Exception:
            continue

    return jsonify({"ok": True})


ACTIVE_CONFIG_ID = 2
DEFAULT_CONFIG_ID = 1

# timeslot: 1 slot = 10 วินาที (ตามที่ผู้ใช้กำหนด)
TIMESLOT_SECONDS = 10

# mapping กล้อง -> ห้อง (ขยายเพิ่มในอนาคตได้)
# เพิ่มกล้องใหม่ในห้องเดิม: เพิ่ม key ใหม่ชี้ไปห้องเดิม เช่น "C5": "hall"
CAM_CODE_TO_ROOM = {
    "C1": "hall",
    "C2": "kitchen",
    "C3": "garage",
    "C4": "garden",
}

# =========================================
# D) SYSTEM CONFIG HELPERS
# =========================================
SNAKE_TO_CAMEL = {
    "alert_no_cat": "alertNoCat",
    "alert_no_eat": "alertNoEating",
    "alert_no_excrete_min": "minExcretion",
    "alert_no_excrete_max": "maxExcretion",
    "max_supported_cats": "maxCats",
}


def row_to_camel(row_dict: dict):
    return {SNAKE_TO_CAMEL.get(k, k): v for k, v in row_dict.items()}


def apply_config_cursor(cursor, config_id: int):
    cursor.execute("SELECT * FROM system_config WHERE id=%s", (config_id,))
    row = cursor.fetchone()
    return row_to_camel(row) if row else None


def _normalize_prefix(s: str) -> str:
    """แปลงชื่อสีให้เป็น prefix ของคอลัมน์ใน timeslot (csv เป็น lowercase)"""
    return (s or "").strip().lower()


def _safe_identifier(name: str) -> bool:
    """กัน SQL injection ในชื่อคอลัมน์: อนุญาตเฉพาะ a-z 0-9 _"""
    if not name:
        return False
    for ch in name:
        ok = ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "_"
        if not ok:
            return False
    return True


def _get_timeslot_columns(cursor) -> set:
    """ดึงรายชื่อคอลัมน์ในตาราง timeslot ไว้ตรวจสอบก่อนอ้างอิง"""
    cursor.execute(
        """
        SELECT COLUMN_NAME
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'timeslot'
        """
    )
    return {r["COLUMN_NAME"] for r in (cursor.fetchall() or [])}


def _get_cat_prefix_map(cursor):
    """
    คืน mapping: cat_name -> timeslot_prefix (เช่น "Black" -> "black")
    โดยใช้ cats.color เป็นหลัก เพราะ timeslot แยกคอลัมน์ตามสี

    สำคัญ:
    - ตาราง timeslot ของบางชุดข้อมูลอาจไม่มีคอลัมน์ของบางสี (เช่น "black")
      ถ้าเราอ้างถึงคอลัมน์ที่ไม่มี จะทำให้ API /api/alerts ล้มเหลวด้วย
      Error: Unknown column ... in 'where clause'
    - ดังนั้นจะคืนเฉพาะ prefix ที่พบคอลัมน์สำคัญใน timeslot จริงเท่านั้น
      (ต้องมีอย่างน้อย: prefix, prefix_cam, prefix_ac)
    """
    cursor.execute("SELECT name, color FROM cats WHERE display_status=1")
    cats_rows = cursor.fetchall() or []

    cols = _get_timeslot_columns(cursor)
    out = {}
    for r in cats_rows:
        name = r.get("name")
        if not name:
            continue
        prefix = _normalize_prefix(r.get("color") or name)

        status_col = prefix
        cam_col = f"{prefix}_cam"
        ac_col = f"{prefix}_ac"

        if not (_safe_identifier(status_col) and _safe_identifier(cam_col) and _safe_identifier(ac_col)):
            continue

        if status_col not in cols or cam_col not in cols or ac_col not in cols:
            # ข้ามแมวที่ไม่มีคอลัมน์ใน timeslot เพื่อกันระบบล้ม
            continue

        out[name] = prefix

    return out


def _timeslot_get_latest_for_cat(cursor, prefix: str):
    """
    คืน record ล่าสุดของแมวตัวหนึ่งจาก timeslot:
      - status: F/NF
      - cam: C1/C2...
      - ac: eat/excrete/NO
      - date_slot: datetime
    """
    status_col = prefix
    cam_col = f"{prefix}_cam"
    ac_col = f"{prefix}_ac"

    if not (_safe_identifier(status_col) and _safe_identifier(cam_col) and _safe_identifier(ac_col)):
        return None

    cols = _get_timeslot_columns(cursor)
    if status_col not in cols or cam_col not in cols or ac_col not in cols or "date_slot" not in cols:
        return None

    sql = f"""
        SELECT date_slot,
               `{status_col}` AS status,
               `{cam_col}` AS cam,
               `{ac_col}` AS activity
        FROM timeslot
        WHERE `{status_col}` IS NOT NULL
        ORDER BY date_slot DESC
        LIMIT 1
    """
    cursor.execute(sql)
    return cursor.fetchone()


def _timeslot_get_last_found_time(cursor, prefix: str):
    """คืน datetime ล่าสุดที่พบแมว (status = 'F')"""
    status_col = prefix
    cols = _get_timeslot_columns(cursor)
    if "date_slot" not in cols or status_col not in cols:
        return None
    if not _safe_identifier(status_col):
        return None
    sql = f"""
        SELECT MAX(date_slot) AS last_found
        FROM timeslot
        WHERE `{status_col}` = 'F'
    """
    cursor.execute(sql)
    row = cursor.fetchone() or {}
    return row.get("last_found")


def _fetch_timeslots_for_cat(cursor, prefix: str, start_dt: datetime, end_dt: datetime):
    """
    ดึง timeslot ของแมวตัวเดียวในช่วงเวลา (start_dt <= date_slot < end_dt)
    คืน list ของ dict: {date_slot,status,cam,activity}
    """
    status_col = prefix
    cam_col = f"{prefix}_cam"
    ac_col = f"{prefix}_ac"

    if not (_safe_identifier(status_col) and _safe_identifier(cam_col) and _safe_identifier(ac_col)):
        return []

    cols = _get_timeslot_columns(cursor)
    need = {"date_slot", status_col, cam_col, ac_col}
    if not need.issubset(cols):
        return []

    sql = f"""
        SELECT date_slot,
               `{status_col}` AS status,
               `{cam_col}` AS cam,
               `{ac_col}` AS activity
        FROM timeslot
        WHERE date_slot >= %s AND date_slot < %s
        ORDER BY date_slot ASC
    """
    cursor.execute(sql, (start_dt, end_dt))
    return cursor.fetchall() or []


def _count_activity_transitions(slots, target_activity: str):
    """
    นับ "จำนวนครั้ง" แบบ transition:
      - นับเมื่อ activity เปลี่ยนจาก ไม่ใช่ target -> เป็น target
      - นับเฉพาะ slot ที่ status == 'F' (พบแมว)
    """
    cnt = 0
    prev = None
    for s in slots:
        if (s.get("status") or "").upper() != "F":
            continue
        cur = (s.get("activity") or "").lower()
        if cur == target_activity and prev != target_activity:
            cnt += 1
        prev = cur
    return cnt


def _latest_date_in_timeslot(cursor):
    cursor.execute("SELECT MAX(DATE(date_slot)) AS d FROM timeslot")
    row = cursor.fetchone() or {}
    return row.get("d")


def _latest_datetime_in_timeslot(cursor):
    """คืน datetime ล่าสุดใน timeslot (MAX(date_slot))"""
    cursor.execute("SELECT MAX(date_slot) AS dt FROM timeslot")
    row = cursor.fetchone() or {}
    return row.get("dt")


def _latest_datetime_in_timeslot_day(cursor, day_start: datetime, day_end: datetime):
    """คืน datetime ล่าสุดใน timeslot เฉพาะวันนั้น (MAX(date_slot) ภายในช่วงวัน)"""
    cursor.execute(
        "SELECT MAX(date_slot) AS dt FROM timeslot WHERE date_slot >= %s AND date_slot < %s",
        (day_start, day_end),
    )
    row = cursor.fetchone() or {}
    return row.get("dt")


def _get_alert_target_day(cursor) -> date:
    """เลือก 'วันล่าสุดที่มีข้อมูล' เป็นฐานสำหรับ Alert (ถ้าไม่มีข้อมูลเลยค่อยใช้วันนี้)"""
    d = _latest_date_in_timeslot(cursor)
    return d if d else datetime.now().date()



# =========================================
# E) SYSTEM CONFIG APIs
# =========================================
@app.route("/api/system_config", methods=["GET"])
def get_system_config():
    """ดึง System Config

    รองรับหลายแมวโดยอิงจากสี (cats.color):
      - ถ้า query ?cat=<ชื่อแมว> จะคืนค่า config เฉพาะแมวตัวนั้น (fallback = global active)
      - ถ้าไม่ส่ง cat จะคืนค่า global active เหมือนเดิม

    Response เป็น camelCase (เหมือนเดิม) และจะมี field เพิ่ม:
      - scope: "global" | "cat"
      - catName, catColor (เฉพาะ scope=cat)
    """
    cat_name = (request.args.get("cat") or "").strip()

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        if not cat_name:
            cfg = apply_config_cursor(cur, ACTIVE_CONFIG_ID)
            if not cfg:
                return jsonify({"message": "Error fetching system config"}), 500
            cfg["scope"] = "global"
            return jsonify(cfg)

        # หา color ของแมว เพื่อใช้เป็น key (ตาม requirement: อิงจาก color)
        cur.execute("SELECT name, color FROM cats WHERE name=%s LIMIT 1", (cat_name,))
        crow = cur.fetchone() or {}
        if not crow:
            return jsonify({"message": f"cat not found: {cat_name}"}), 404

        cat_color = (crow.get("color") or "").strip()
        # config เฉพาะแมว (fallback = global active)
        cfg_snake = _get_system_config_for_cat(cur, cat_color)
        cfg = row_to_camel(cfg_snake) if cfg_snake else apply_config_cursor(cur, ACTIVE_CONFIG_ID)
        if not cfg:
            return jsonify({"message": "Error fetching system config"}), 500

        cfg["scope"] = "cat"
        cfg["catName"] = crow.get("name")
        cfg["catColor"] = cat_color
        return jsonify(cfg)
    finally:
        cur.close()
        conn.close()


@app.route("/api/system_config", methods=["POST"])
def update_system_config():
    """อัปเดต System Config

    รองรับ 2 โหมด:
      1) Global: ไม่ส่ง catName/catColor -> update แถว ACTIVE_CONFIG_ID เดิม
      2) Per-cat: ส่ง catName หรือ catColor -> upsert ไป system_config_cat (key = catColor)

    Body (camelCase):
      - alertNoCat, alertNoEating, minExcretion, maxExcretion, maxCats
      - catName (optional), catColor (optional)
    """
    body = request.json or {}
    cat_name = (body.get("catName") or "").strip()
    cat_color = (body.get("catColor") or "").strip()

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        # ถ้ามี catName แต่ไม่มี catColor ให้ดึงจากตาราง cats
        if cat_name and not cat_color:
            cur.execute("SELECT color FROM cats WHERE name=%s LIMIT 1", (cat_name,))
            r = cur.fetchone() or {}
            cat_color = (r.get("color") or "").strip()

        # ---------- Per-cat ----------
        if cat_color:
            # เริ่มจากค่า effective ปัจจุบันของแมว (fallback = global)
            current_snake = _get_system_config_for_cat(cur, cat_color) or _get_system_config_global(cur)

            merged = {}
            for snake_key, camel_key in SNAKE_TO_CAMEL.items():
                merged[snake_key] = body.get(camel_key, current_snake.get(snake_key))

            # validation: ค่า min/max ขับถ่ายไม่ควรสลับกัน
            try:
                min_ex = int(merged["alert_no_excrete_min"]) if merged.get("alert_no_excrete_min") is not None else None
                max_ex = int(merged["alert_no_excrete_max"]) if merged.get("alert_no_excrete_max") is not None else None
                if min_ex is not None and max_ex is not None and min_ex > max_ex:
                    return jsonify({"message": "minExcretion ต้องน้อยกว่าหรือเท่ากับ maxExcretion"}), 400
            except (TypeError, ValueError):
                return jsonify({"message": "minExcretion/maxExcretion ต้องเป็นตัวเลข"}), 400

            # upsert
            cur.execute(
                """
                INSERT INTO system_config_cat
                  (cat_color, alert_no_cat, alert_no_excrete_min, alert_no_excrete_max, alert_no_eat, max_supported_cats, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,NOW())
                ON DUPLICATE KEY UPDATE
                  alert_no_cat=VALUES(alert_no_cat),
                  alert_no_excrete_min=VALUES(alert_no_excrete_min),
                  alert_no_excrete_max=VALUES(alert_no_excrete_max),
                  alert_no_eat=VALUES(alert_no_eat),
                  max_supported_cats=VALUES(max_supported_cats),
                  updated_at=NOW()
                """,
                (
                    cat_color,
                    merged["alert_no_cat"],
                    merged["alert_no_excrete_min"],
                    merged["alert_no_excrete_max"],
                    merged["alert_no_eat"],
                    merged["max_supported_cats"],
                ),
            )
            conn.commit()
            return jsonify({"message": "Config updated successfully", "scope": "cat", "catColor": cat_color})

        # ---------- Global (เดิม) ----------
        cur.execute("SELECT * FROM system_config WHERE id=%s", (ACTIVE_CONFIG_ID,))
        current_cfg_snake = cur.fetchone()
        if not current_cfg_snake:
            return jsonify({"message": "Active config not found"}), 404

        merged = {}
        for snake_key, camel_key in SNAKE_TO_CAMEL.items():
            merged[snake_key] = body.get(camel_key, current_cfg_snake.get(snake_key))

        # validation: ค่า min/max ขับถ่ายไม่ควรสลับกัน
        try:
            min_ex = int(merged["alert_no_excrete_min"]) if merged.get("alert_no_excrete_min") is not None else None
            max_ex = int(merged["alert_no_excrete_max"]) if merged.get("alert_no_excrete_max") is not None else None
            if min_ex is not None and max_ex is not None and min_ex > max_ex:
                return jsonify({"message": "minExcretion ต้องน้อยกว่าหรือเท่ากับ maxExcretion"}), 400
        except (TypeError, ValueError):
            return jsonify({"message": "minExcretion/maxExcretion ต้องเป็นตัวเลข"}), 400

        update_sql = """
            UPDATE system_config
            SET alert_no_cat=%s,
                alert_no_excrete_min=%s,
                alert_no_excrete_max=%s,
                alert_no_eat=%s,
                max_supported_cats=%s
            WHERE id=%s
        """
        cur2 = conn.cursor()
        cur2.execute(
            update_sql,
            (
                merged["alert_no_cat"],
                merged["alert_no_excrete_min"],
                merged["alert_no_excrete_max"],
                merged["alert_no_eat"],
                merged["max_supported_cats"],
                ACTIVE_CONFIG_ID,
            ),
        )
        conn.commit()
        cur2.close()
        return jsonify({"message": "Config updated successfully", "scope": "global"})
    finally:
        cur.close()
        conn.close()



@app.route("/api/system_config/summaries", methods=["GET"])
def get_system_config_summaries():
    """คืน "ค่าสรุป" ของระบบต่อแมว ที่มีค่า (alert_no_eat, alert_no_excrete_max) ซ้ำกัน >= 3 เดือน

    สำคัญ: หน้านี้ต้อง "เคารพการตั้งค่าแสดงผลแมว" (cats.display_status)
      - display_status = 1 -> แสดง/ให้เลือกได้
      - display_status = 0 -> ซ่อน (ไม่ต้องโชว์ใน Summary)

    ใช้สำหรับหน้า System config เพื่อให้ผู้ใช้กด "แอดข้อมูลจากค่าสรุป"
    ไปบันทึกเป็น config เฉพาะแมวได้ (key อิงจาก cats.color)

    Response:
      [
        {
          catName, catColor,
          alertNoEating, maxExcretion,
          monthsCount, latestMonth
        }, ...
      ]
    """
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        # รองรับการกรองตามแมวที่เลือกจากหน้าเว็บ: /api/system_config/summaries?cat=<catName>
        cat_q = (request.args.get("cat") or "").strip()

        filters = ["c.display_status = 1"]
        params = []

        if cat_q:
            # แปลง cat name -> color เพื่อให้กรองได้แม่น (cat_config_monthly key หลักคือ cat_color)
            cur.execute("SELECT color FROM cats WHERE name=%s LIMIT 1", (cat_q,))
            row = cur.fetchone() or {}
            cat_color = (row.get("color") or "").strip()
            if cat_color:
                filters.append("m.cat_color = %s")
                params.append(cat_color)
            else:
                # fallback กรองตามชื่อ (กรณีไม่มี color)
                filters.append("COALESCE(NULLIF(m.cat_name,''), c.name) = %s")
                params.append(cat_q)

        where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""

        sql = f"""
            SELECT
              m.cat_color,
              COALESCE(NULLIF(m.cat_name,''), c.name) AS cat_name,
              m.alert_no_eat,
              m.alert_no_excrete_max,
              COUNT(*) AS months_count,
              MAX(m.month_ym) AS latest_month
            FROM cat_config_monthly m
            JOIN cats c
              ON c.color = m.cat_color
            {where_clause}
            GROUP BY
              m.cat_color,
              COALESCE(NULLIF(m.cat_name,''), c.name),
              m.alert_no_eat,
              m.alert_no_excrete_max
            HAVING COUNT(*) >= 3
            ORDER BY latest_month DESC
            """
        cur.execute(sql, params)
        rows = cur.fetchall() or []

        # เลือก "ชุดล่าสุด" ต่อแมว 1 ชุด
        chosen = {}
        for r in rows:
            color = (r.get("cat_color") or "").strip()
            if not color:
                continue
            if color in chosen:
                continue
            chosen[color] = {
                "catName": r.get("cat_name"),
                "catColor": color,
                "alertNoEating": int(r.get("alert_no_eat") or 0),
                "maxExcretion": int(r.get("alert_no_excrete_max") or 0),
                "monthsCount": int(r.get("months_count") or 0),
                "latestMonth": r.get("latest_month"),
            }

        # sort by catName for stable UI
        result = list(chosen.values())
        result.sort(key=lambda x: (str(x.get("catName") or ""), str(x.get("catColor") or "")))
        return jsonify(result)
    finally:
        cur.close()
        conn.close()


@app.route("/api/system_config/apply_summary", methods=["POST"])
def apply_system_config_summary():
    """บันทึกค่าสรุปลง system_config_cat (เฉพาะแมว) โดย override เฉพาะ:
      - alert_no_eat (camel: alertNoEating)
      - alert_no_excrete_max (camel: maxExcretion)

    Body:
      - catColor (required) หรือ catName
      - alertNoEating (required)
      - maxExcretion (required)
    """
    body = request.get_json(silent=True) or {}
    cat_color = (body.get("catColor") or "").strip()
    cat_name = (body.get("catName") or "").strip()

    try:
        alert_no_eat = int(body.get("alertNoEating"))
        alert_no_excrete_max = int(body.get("maxExcretion"))
    except (TypeError, ValueError):
        return jsonify({"message": "alertNoEating/maxExcretion ต้องเป็นตัวเลข"}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        if not cat_color and cat_name:
            cur.execute("SELECT color FROM cats WHERE name=%s LIMIT 1", (cat_name,))
            r = cur.fetchone() or {}
            cat_color = (r.get("color") or "").strip()

        if not cat_color:
            return jsonify({"message": "catColor is required"}), 400

        # base = effective config ปัจจุบัน (fallback global)
        eff = _get_effective_config(cur, cat_color) or {}
        eff["alert_no_eat"] = alert_no_eat
        eff["alert_no_excrete_max"] = alert_no_excrete_max

        # เขียน config เฉพาะแมว: เพื่อให้ทำงานได้แม้ไม่มี UNIQUE/PK ที่ cat_color
        # (ถ้ามี UNIQUE ก็ใช้ได้เช่นกัน แต่แนวทางนี้กันเคสข้อมูลซ้ำ/อ่านค่าไม่ตรง)
        cur.execute("DELETE FROM system_config_cat WHERE cat_color=%s", (cat_color,))
        cur.execute(
            """
            INSERT INTO system_config_cat
              (cat_color, alert_no_cat, alert_no_excrete_min, alert_no_excrete_max, alert_no_eat, max_supported_cats, updated_at)
            VALUES
              (%s,%s,%s,%s,%s,%s,NOW())
            """,
            (
              cat_color,
              eff.get("alert_no_cat"),
              eff.get("alert_no_excrete_min"),
              eff.get("alert_no_excrete_max"),
              eff.get("alert_no_eat"),
              eff.get("max_supported_cats"),
            ),
        )
        conn.commit()

        return jsonify({"message": "Applied summary config", "scope": "cat", "catColor": cat_color})
    finally:
        cur.close()
        conn.close()



@app.route("/api/system_config/reset", methods=["POST"])
def reset_system_config():
    """รีเซ็ต System Config

    รองรับ:
      - POST /api/system_config/reset?cat=<ชื่อแมว> -> ลบ/รีเซ็ต config เฉพาะแมว (กลับไปใช้ global)
      - POST /api/system_config/reset (ไม่ส่ง cat) -> รีเซ็ต global active กลับ default (เหมือนเดิม)
    """
    cat_name = (request.args.get("cat") or "").strip()

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        # ---------- per-cat reset: delete row ให้ fallback ไปใช้ global ----------
        if cat_name:
            cur.execute("SELECT color FROM cats WHERE name=%s LIMIT 1", (cat_name,))
            r = cur.fetchone() or {}
            cat_color = (r.get("color") or "").strip()
            if not cat_color:
                return jsonify({"message": f"cat not found: {cat_name}"}), 404

            cur.execute("DELETE FROM system_config_cat WHERE cat_color=%s", (cat_color,))
            conn.commit()
            return jsonify({"message": "Cat config has been reset to global defaults", "scope": "cat", "catColor": cat_color})

        # ---------- global reset (เดิม) ----------
        cur.execute("SELECT * FROM system_config WHERE id=%s", (DEFAULT_CONFIG_ID,))
        default_config = cur.fetchone()
        if not default_config:
            return jsonify({"message": "Error resetting system config"}), 500

        update_query = """
            UPDATE system_config
            SET alert_no_cat=%s,
                alert_no_excrete_min=%s,
                alert_no_excrete_max=%s,
                alert_no_eat=%s,
                max_supported_cats=%s
            WHERE id=%s
        """
        cur2 = conn.cursor()
        cur2.execute(
            update_query,
            (
                default_config.get("alert_no_cat"),
                default_config.get("alert_no_excrete_min"),
                default_config.get("alert_no_excrete_max"),
                default_config.get("alert_no_eat"),
                default_config.get("max_supported_cats"),
                ACTIVE_CONFIG_ID,
            ),
        )
        conn.commit()
        cur2.close()
        return jsonify({"message": "System config has been reset to default values", "scope": "global"})
    finally:
        cur.close()
        conn.close()


# =========================================
# F) ALERTS (Persistent) - TIMESLOT BASED
# =========================================
def _get_system_config_global(cursor) -> dict:
    """อ่านค่า global config (snake_case) จาก system_config แถว ACTIVE_CONFIG_ID"""
    cursor.execute("SELECT * FROM system_config WHERE id=%s", (ACTIVE_CONFIG_ID,))
    row = cursor.fetchone()
    return row or {}


@app.route("/api/monthly_rollup", methods=["POST"])
def monthly_rollup():
    """ประมวลผลสรุปรายเดือนจาก timeslot แล้วบันทึกลง cat_config_monthly

    Body (optional):
      - month_ym: "YYYY-MM" (ถ้าไม่ส่ง จะใช้เดือนที่ผ่านมา)

    หมายเหตุ:
      - จะไม่บันทึกเดือนปัจจุบัน
      - จะไม่บันทึกถ้าเดือนนั้นข้อมูลยังไม่ครบทุกวัน
    """
    body = request.get_json(silent=True) or {}
    month_ym = body.get("month_ym") or _prev_month_ym(date.today())

    conn = mysql.connector.connect(**db_config)
    cur = conn.cursor(dictionary=True)
    try:
        ok = _ensure_monthly_rollup_for_month(cur, month_ym)
        conn.commit()
        return jsonify({"month_ym": month_ym, "processed": bool(ok)})
    finally:
        cur.close()
        conn.close()

def _get_system_config_for_cat(cursor, cat_color: str) -> Optional[dict]:
    """อ่านค่า config เฉพาะแมว (snake_case) จาก system_config_cat โดย key = cat_color

    คืน None ถ้าไม่มีแถว (ให้ caller fallback ไปใช้ global)
    """
    if not cat_color:
        return None
    cursor.execute("SELECT * FROM system_config_cat WHERE cat_color=%s LIMIT 1", (cat_color,))
    row = cursor.fetchone()
    return row or None


def _get_effective_config(cursor, cat_color: str) -> dict:
    """คืนค่า effective config สำหรับแมว 1 ตัว (snake_case) โดย fallback ไป global"""
    return _get_system_config_for_cat(cursor, cat_color) or _get_system_config_global(cursor)


def _time_last_found(cursor, prefix: str):
    """เวลา date_slot ล่าสุดที่พบแมว (status='F')"""
    return _timeslot_get_last_found_time(cursor, prefix)


def _count_activity_events_in_range(cursor, prefix: str, activity: str, start_dt: datetime, end_dt: datetime) -> int:
    """นับจำนวนครั้งของ activity แบบ transition ภายในช่วงเวลา"""
    slots = _fetch_timeslots_for_cat(cursor, prefix, start_dt, end_dt)
    return _count_activity_transitions(slots, activity)

def _compute_alerts_for_day(cursor, target_day: date):
    """คำนวณ Alert โดยอิงวัน (target_day)

    - no_cat: เทียบช่วงเวลาจาก last_found ถึง 'เวลาล่าสุดในวันนั้น' (ไม่ใช้ NOW เพื่อให้ทดสอบย้อนหลังได้)
    - no_eating / excretion: นับจาก timeslot ภายในวันนั้น (00:00-23:59)
    """
    # ค่า global ใช้เป็น fallback เฉพาะกรณีแมวตัวนั้นไม่มี config เฉพาะ
    global_cfg = _get_system_config_global(cursor)

    # ตาราง system_config ใช้ snake_case:
    # alert_no_cat, alert_no_eat, alert_no_excrete_min, alert_no_excrete_max
    global_no_cat_hours = int(global_cfg.get("alert_no_cat", 12) or 12)
    global_no_eating_min = int(global_cfg.get("alert_no_eat", 2) or 2)
    global_min_excretion = int(global_cfg.get("alert_no_excrete_min", 3) or 3)
    global_max_excretion = int(global_cfg.get("alert_no_excrete_max", 5) or 5)


    # cats: map name -> prefix(color) (กรองเฉพาะที่มีคอลัมน์ใน timeslot จริง)
    cat_prefix = _get_cat_prefix_map(cursor)

    # day window
    day_start = datetime.combine(target_day, time(0, 0, 0))
    day_end = day_start + timedelta(days=1)

    # reference time: latest slot inside that day (fallback = end-of-day)
    ref_dt = _latest_datetime_in_timeslot_day(cursor, day_start, day_end) or day_end

    alerts = []
    for cat_name, prefix in cat_prefix.items():
        # หา cat_color (original case) จาก cats table แล้ว normalize key เป็นค่าใน cats.color
        cursor.execute("SELECT color FROM cats WHERE name=%s LIMIT 1", (cat_name,))
        crow = cursor.fetchone() or {}
        cat_color = (crow.get("color") or "").strip()

        cfg = _get_effective_config(cursor, cat_color)

        no_cat_hours = int(cfg.get("alert_no_cat", global_no_cat_hours) or global_no_cat_hours)
        no_eating_min = int(cfg.get("alert_no_eat", global_no_eating_min) or global_no_eating_min)
        min_excretion = int(cfg.get("alert_no_excrete_min", global_min_excretion) or global_min_excretion)
        max_excretion = int(cfg.get("alert_no_excrete_max", global_max_excretion) or global_max_excretion)

        # 1) no_cat
        # last time found 'F'
        last_found = _time_last_found(cursor, prefix)
        if last_found:
            hours_since = (ref_dt - last_found).total_seconds() / 3600.0
            if hours_since >= no_cat_hours:
                alerts.append(
                    {
                        "cat_name": cat_name,
                        "alert_type": "no_cat",
                        "message": f"ไม่พบ {cat_name} เกิน {no_cat_hours} ชั่วโมง",
                    }
                )

        # 2) no_eating: count eat transitions inside day
        eat_count = _count_activity_events_in_range(cursor, prefix, "eat", day_start, day_end)
        if eat_count < no_eating_min:
            alerts.append(
                {
                    "cat_name": cat_name,
                    "alert_type": "no_eating",
                    "message": f"{cat_name} กินอาหารน้อยกว่า {no_eating_min} ครั้ง/วัน",
                }
            )

        # 3) excretion: count excrete transitions inside day
        ex_count = _count_activity_events_in_range(cursor, prefix, "excrete", day_start, day_end)
        if ex_count < min_excretion:
            alerts.append(
                {
                    "cat_name": cat_name,
                    "alert_type": "low_excrete",
                    "message": f"{cat_name} ขับถ่ายน้อยกว่าที่กำหนด ({ex_count}/{min_excretion})",
                }
            )
        if ex_count > max_excretion:
            alerts.append(
                {
                    "cat_name": cat_name,
                    "alert_type": "high_excrete",
                    "message": f"{cat_name} ขับถ่ายมากกว่าที่กำหนด ({ex_count}/{max_excretion})",
                }
            )

    return alerts


def _table_has_column(cursor, table: str, column: str) -> bool:
    if not _safe_identifier(table) or not _safe_identifier(column):
        return False
    cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE %s", (column,))
    return cursor.fetchone() is not None


def _is_generated_column(cursor, table: str, column: str) -> bool:
    """เช็คว่า column เป็น generated column หรือไม่"""
    cursor.execute(
        """
        SELECT EXTRA
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        """,
        (table, column),
    )
    row = cursor.fetchone() or {}
    extra = (row.get("EXTRA") or "").upper()
    return "GENERATED" in extra


def _ingest_alerts_for_day(cursor, target_day: date):
    """คำนวณและบันทึกลง alerts_log โดยอิงวัน (target_day)

    - กันการบันทึกซ้ำแบบง่าย: (cat_name, alert_type, alert_date) ซ้ำจะไม่ insert
    - รองรับกรณี alert_date เป็น generated column: ห้าม insert alert_date เอง
      ให้ตั้ง created_at อยู่ในวันนั้น แล้ว alert_date (generated) จะคำนวณเอง
    - รองรับคอลัมน์ color (ถ้ามี): insert color เพื่อให้ frontend ใช้ได้
    """
    table = "alerts_log"

    new_alerts = _compute_alerts_for_day(cursor, target_day)
    inserted = 0

    has_color = _table_has_column(cursor, table, "color")
    alert_date_generated = (
        _is_generated_column(cursor, table, "alert_date")
        if _table_has_column(cursor, table, "alert_date")
        else False
    )

    # ใช้ created_at ให้อยู่ในวัน target_day เพื่อให้ alert_date generated ถูกต้อง
    created_at_for_day = datetime.combine(target_day, time(0, 0, 0))

    for a in new_alerts:
        cat_name = a.get("cat_name")
        alert_type = a.get("alert_type")
        message = a.get("message", "")

        # กันซ้ำ: cat+type+day
        cursor.execute(
            f"""
            SELECT id
            FROM `{table}`
            WHERE cat_name = %s AND alert_type = %s AND alert_date = %s AND is_read <> 2
            LIMIT 1
            """,
            (cat_name, alert_type, target_day),
        )
        if cursor.fetchone():
            continue

        # color (ถ้าตารางมี)
        color_val = None
        if has_color:
            cursor.execute("SELECT color FROM cats WHERE name=%s LIMIT 1", (cat_name,))
            r = cursor.fetchone() or {}
            color_val = r.get("color")

        if alert_date_generated:
            if has_color:
                cursor.execute(
                    f"""
                    INSERT INTO `{table}` (cat_name, color, alert_type, message, is_read, created_at)
                    VALUES (%s, %s, %s, %s, 0, %s)
                    """,
                    (cat_name, color_val, alert_type, message, created_at_for_day),
                )
            else:
                cursor.execute(
                    f"""
                    INSERT INTO `{table}` (cat_name, alert_type, message, is_read, created_at)
                    VALUES (%s, %s, %s, 0, %s)
                    """,
                    (cat_name, alert_type, message, created_at_for_day),
                )
        else:
            if has_color:
                cursor.execute(
                    f"""
                    INSERT INTO `{table}` (cat_name, color, alert_type, message, is_read, created_at, alert_date)
                    VALUES (%s, %s, %s, %s, 0, %s, %s)
                    """,
                    (cat_name, color_val, alert_type, message, created_at_for_day, target_day),
                )
            else:
                cursor.execute(
                    f"""
                    INSERT INTO `{table}` (cat_name, alert_type, message, is_read, created_at, alert_date)
                    VALUES (%s, %s, %s, 0, %s, %s)
                    """,
                    (cat_name, alert_type, message, created_at_for_day, target_day),
                )

        inserted += 1

    return inserted


# =========================================
# =========================================
# F.0) MONTHLY ROLLUP FROM TIMESLOT (NO AUTO-ADJUST)
# =========================================
def _ym(dt: date) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def _month_bounds(month_ym: str):
    """คืนค่า (start_dt, end_dt, days_in_month) ของเดือน YYYY-MM"""
    y, m = month_ym.split("-")
    y = int(y)
    m = int(m)
    days = calendar.monthrange(y, m)[1]
    start = datetime(y, m, 1, 0, 0, 0)
    end = start + timedelta(days=days)
    return start, end, days


def _prev_month_ym(today: date) -> str:
    first = today.replace(day=1)
    prev_last = first - timedelta(days=1)
    return _ym(prev_last)


def _is_month_complete_in_timeslot(cursor, month_start: datetime, month_end: datetime, days_in_month: int) -> bool:
    """เดือนจะถือว่า 'ครบ' เมื่อมีข้อมูลใน timeslot ครบทุกวันของเดือนนั้น
    (นับจาก DISTINCT DATE(date_slot) ในช่วง [start, end))
    """
    cursor.execute(
        """
        SELECT COUNT(DISTINCT DATE(date_slot)) AS dcnt
        FROM timeslot
        WHERE date_slot >= %s AND date_slot < %s
        """,
        (month_start, month_end),
    )
    row = cursor.fetchone() or {}
    dcnt = int(row.get("dcnt") or 0)
    return dcnt >= int(days_in_month)


def _ensure_monthly_rollup_for_month(cursor, month_ym: str) -> bool:
    """ประมวลผลรายเดือนจาก timeslot แล้วบันทึกลง cat_config_monthly

    เงื่อนไข:
      - จะบันทึกเฉพาะ 'เดือนที่ผ่านมา' หรือเดือนที่ระบุ และต้องเป็น 'เดือนที่ครบ' เท่านั้น
      - ถ้าเป็นเดือนปัจจุบัน จะไม่บันทึก (เพราะยังไม่ครบเดือน)

    วิธีคำนวณ:
      - สำหรับแต่ละแมว: นับจำนวน event 'eat' และ 'excrete' รายวันในเดือนนั้น
      - เฉลี่ยรายเดือน = total_events / จำนวนวันของเดือน
      - เก็บทั้งค่าเฉลี่ย (avg_*) และค่า integer ที่ใช้เป็น config (alert_no_eat, alert_no_excrete_max)
    """
    # กันเดือนปัจจุบัน
    today = date.today()
    if month_ym == _ym(today):
        return False

    month_start, month_end, days_in_month = _month_bounds(month_ym)

    # เดือนต้อง "ครบ" ก่อน
    if not _is_month_complete_in_timeslot(cursor, month_start, month_end, days_in_month):
        return False

    # ดึงแมวที่มีคอลัมน์ใน timeslot จริง
    cols = _get_timeslot_columns(cursor)
    cursor.execute("SELECT name, color FROM cats WHERE display_status=1")
    cats_rows = cursor.fetchall() or []

    cats = []
    for r in cats_rows:
        cat_name = r.get("name")
        color_val = r.get("color") or cat_name
        prefix = _normalize_prefix(color_val)

        status_col = prefix
        cam_col = f"{prefix}_cam"
        ac_col = f"{prefix}_ac"

        if not (_safe_identifier(status_col) and _safe_identifier(cam_col) and _safe_identifier(ac_col)):
            continue
        if status_col not in cols or cam_col not in cols or ac_col not in cols:
            continue

        cats.append({"name": cat_name, "color": color_val, "prefix": prefix})

    if not cats:
        return False

    # วนวันในเดือนเพื่อคำนวณค่าเฉลี่ยรายเดือน
    for c in cats:
        total_eat = 0
        total_ex = 0

        for d in range(days_in_month):
            day_start = month_start + timedelta(days=d)
            day_end = day_start + timedelta(days=1)

            total_eat += _count_activity_events_in_range(cursor, c["prefix"], "eat", day_start, day_end)
            total_ex += _count_activity_events_in_range(cursor, c["prefix"], "excrete", day_start, day_end)

        avg_eat = float(total_eat) / float(days_in_month)
        avg_ex = float(total_ex) / float(days_in_month)

        # ค่า config ที่เก็บเป็น int (เฉลี่ยรายเดือนแล้วปัดเป็นจำนวนเต็ม)
        # - no_eat เป็น minimum/วัน => ใช้ round() (ปรับได้ภายหลังถ้าต้องการ floor/ceil)
        # - excrete_max เป็น maximum/วัน => ใช้ round() เช่นกัน
        alert_no_eat = int(round(avg_eat))
        alert_no_excrete_max = int(round(avg_ex))

        # กันไม่ให้เป็น 0 แบบผิดปกติ (ถ้าอยากให้ 0 ได้ ให้ลบ 2 บรรทัดนี้)
        if alert_no_eat < 0:
            alert_no_eat = 0
        if alert_no_excrete_max < 0:
            alert_no_excrete_max = 0

        cursor.execute(
            """
            INSERT INTO cat_config_monthly
              (month_ym, cat_color, cat_name, alert_no_eat, alert_no_excrete_max, avg_eat_per_day, avg_excrete_per_day, days_in_month, created_at)
            VALUES
              (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            AS new
            ON DUPLICATE KEY UPDATE
              alert_no_eat = new.alert_no_eat,
              alert_no_excrete_max = new.alert_no_excrete_max,
              avg_eat_per_day = new.avg_eat_per_day,
              avg_excrete_per_day = new.avg_excrete_per_day,
              days_in_month = new.days_in_month,
              created_at = new.created_at
            """,
            (
                month_ym,
                c["color"],
                c["name"],
                alert_no_eat,
                alert_no_excrete_max,
                avg_eat,
                avg_ex,
                days_in_month,
            ),
        )

    return True


def _maybe_monthly_rollup(cursor):
    """รัน rollup ให้ 'เดือนที่ผ่านมา' อัตโนมัติ (idempotent)"""
    month_ym = _prev_month_ym(date.today())
    _ensure_monthly_rollup_for_month(cursor, month_ym)


@app.route("/api/alerts", methods=["GET"])
def list_alerts():
    """ดึงรายการแจ้งเตือน (จะ trigger ingest ของ "วันล่าสุดที่มีข้อมูลใน timeslot" ก่อนเสมอ)

    Query:
      - cat: ชื่อแมว (optional)
      - include_read=1/0
    """
    cat = request.args.get("cat")  # optional - กรองตามแมว
    include_read = request.args.get("include_read", "1") == "1"

    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor(dictionary=True)
    try:
        table = "alerts_log"

        
        inserted = 0
        # trigger ingest ตามโหมด
        mode = (request.args.get("mode") or "realtime").strip().lower()
        date_str = (request.args.get("date") or "").strip()

        # monthly rollup (เฉพาะเดือนที่ผ่านมา ถ้าครบเดือนแล้ว)
        _maybe_monthly_rollup(cursor)

        if mode == "mixed":
            # โหมดเดิม: คำนวณครบทุกประเภทของ "วันล่าสุดที่มีข้อมูล"
            target_day = _get_alert_target_day(cursor)
            inserted = _ingest_alerts_for_day(cursor, target_day)
        elif mode == "daily":
            # โหมดรายวัน: กิน/ขับถ่าย (สำหรับวันระบุ หรือวันล่าสุดที่มีข้อมูล)
            if date_str:
                target_day = datetime.strptime(date_str, "%Y-%m-%d").date()
            else:
                target_day = _get_alert_target_day(cursor)
            inserted = _ingest_daily_behavior_for_day(cursor, target_day)
        else:
            # default = realtime: เฉพาะแมวหาย ตามชั่วโมง config และอ้าง NOW()
            inserted = _ingest_realtime_no_cat(cursor)

        connection.commit()


        has_color = _table_has_column(cursor, table, "color")

        if has_color:
            base_sql = f"""
            SELECT id,
                   cat_name AS cat,
                   color,
                   alert_type AS type,
                   message,
                   is_read,
                   created_at,
                   alert_date
            FROM `{table}`
            WHERE 1=1
              AND is_read <> 2
            """
        else:
            # ถ้ายังไม่มีคอลัมน์ color ใน alerts_log ให้ join cats เพื่อคืนสีให้ frontend
            base_sql = f"""
            SELECT a.id,
                   a.cat_name AS cat,
                   c.color AS color,
                   a.alert_type AS type,
                   a.message,
                   a.is_read,
                   a.created_at,
                   a.alert_date
            FROM `{table}` a
            LEFT JOIN cats c ON c.name = a.cat_name
            WHERE 1=1
              AND a.is_read <> 2
            """

        params = []
        if cat:
            base_sql += " AND cat_name=%s" if has_color else " AND a.cat_name=%s"
            params.append(cat)
        if not include_read:
            base_sql += " AND is_read=0" if has_color else " AND a.is_read=0"

        base_sql += " ORDER BY created_at DESC, id DESC" if has_color else " ORDER BY a.created_at DESC, a.id DESC"

        cursor.execute(base_sql, params)
        rows = cursor.fetchall() or []
        return jsonify(rows)
    finally:
        cursor.close()
        connection.close()

@app.route("/api/alerts/daily_run", methods=["POST"])
def daily_run():
    """Run daily summary (eat/excrete) alerts for a specific day.

    Query:
      - date=YYYY-MM-DD (optional). Default = today.
    """
    date_str = (request.args.get("date") or "").strip()
    target_day = date.today()
    if date_str:
        target_day = datetime.strptime(date_str, "%Y-%m-%d").date()

    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor(dictionary=True)
    try:
        inserted = _ingest_daily_behavior_for_day(cursor, target_day)
        connection.commit()
        try:
            if inserted and inserted > 0:
                _send_web_push_to_all('Pet Monitoring', f'สรุปประจำวัน: มีการแจ้งเตือนใหม่ {inserted} รายการ', '/')
        except Exception:
            pass
        return jsonify({"ok": True, "date": str(target_day), "inserted": inserted})
    except Exception as e:
        connection.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        cursor.close()
        connection.close()

@app.route("/api/alerts/mark_read", methods=["PATCH"])
def mark_alerts_read():
    """ทำเครื่องหมายอ่านแล้ว: ส่ง ids=[...]"""
    body = request.json or {}
    ids = body.get("ids") or []
    if not isinstance(ids, list) or len(ids) == 0:
        return jsonify({"message": "ids required"}), 400

    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor()
    try:
        q = "UPDATE alerts_log SET is_read=1 WHERE id IN (" + ",".join(["%s"] * len(ids)) + ")"
        cursor.execute(q, tuple(ids))
        connection.commit()
        return jsonify({"updated": cursor.rowcount})
    finally:
        cursor.close()
        connection.close()


@app.route("/api/alerts/mark_all_read", methods=["PATCH"])
def mark_all_read():
    """อ่านทั้งหมด (option: กรองตามแมว)"""
    cat = request.args.get("cat")
    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor()
    try:
        if cat:
            cursor.execute("UPDATE alerts_log SET is_read=1 WHERE cat_name=%s AND is_read=0", (cat,))
        else:
            cursor.execute("UPDATE alerts_log SET is_read=1 WHERE is_read=0")
        connection.commit()
        return jsonify({"updated": cursor.rowcount})
    finally:
        cursor.close()
        connection.close()


# =========================================
# G) CATS (CURRENT ROOM FROM TIMESLOT)
# =========================================
@app.route("/api/cats", methods=["GET"])
def get_cats():
    """
    ให้ตรง schema ในรูป:
      cats(name,image_url,real_image_url,color,display_status)
    current_room: คำนวณจาก timeslot ล่าสุดที่ status='F' โดย map cam_code -> room
    """
    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT name, image_url, real_image_url, color, display_status
            FROM cats
            """
        )
        cats_rows = cursor.fetchall() or []
        prefix_map = {r["name"]: _normalize_prefix(r.get("color") or r["name"]) for r in cats_rows}

        out = []
        for r in cats_rows:
            name = r["name"]
            prefix = prefix_map.get(name)
            current_room = None

            if prefix:
                # หา "slot ล่าสุดที่พบ" ก่อน แล้วเอา cam ไป map ห้อง
                status_col = prefix
                cam_col = f"{prefix}_cam"
                if _safe_identifier(status_col) and _safe_identifier(cam_col):
                    cols = _get_timeslot_columns(cursor)
                    if "date_slot" in cols and status_col in cols and cam_col in cols:
                        sql = f"""
                            SELECT `{cam_col}` AS cam
                            FROM timeslot
                            WHERE `{status_col}`='F'
                              AND `{cam_col}` IS NOT NULL
                            ORDER BY date_slot DESC
                            LIMIT 1
                        """
                        cursor.execute(sql)
                        row = cursor.fetchone() or {}
                        cam = row.get("cam")
                        current_room = CAM_CODE_TO_ROOM.get(str(cam).strip(), None) if cam else None

            out.append(
                {
                    "name": name,
                    "image_url": normalize_image_to_url(r.get("image_url")),
                    "real_image_url": normalize_image_to_url(r.get("real_image_url")),
                    "color": r.get("color"),
                    "display_status": r.get("display_status"),
                    "current_room": current_room,
                }
            )

        return jsonify(out)
    finally:
        cursor.close()
        connection.close()


@app.route("/api/cats/display_status", methods=["PATCH"])
def update_cats_display_status():
    """อัปเดตการแสดงผลแมว (cats.display_status)

    Frontend จะส่งแบบใดแบบหนึ่ง:
      1) { "selected": ["Black","Orange"] }
         -> set ทั้งหมดเป็น 0 แล้ว set รายชื่อที่เลือกเป็น 1
      2) { "updates": [ { "name": "Black", "display_status": 1 }, ... ] }
         -> อัปเดตเฉพาะรายการที่ส่งมา

    Response:
      { "updated": <int> }
    """
    body = request.json or {}
    selected = body.get("selected", None)
    updates = body.get("updates", None)

    conn = get_db()
    cur = conn.cursor()
    try:
        updated = 0

        if selected is not None:
            if not isinstance(selected, list):
                return jsonify({"message": "selected must be a list"}), 400

            names = [str(x).strip() for x in selected if str(x).strip()]

            # 1) reset all to 0
            cur.execute("UPDATE cats SET display_status=0")
            updated += int(cur.rowcount or 0)

            # 2) set selected to 1
            if names:
                placeholders = ",".join(["%s"] * len(names))
                cur.execute(
                    f"UPDATE cats SET display_status=1 WHERE name IN ({placeholders})",
                    tuple(names),
                )
                updated += int(cur.rowcount or 0)

            conn.commit()
            return jsonify({"updated": updated})

        if updates is not None:
            if not isinstance(updates, list):
                return jsonify({"message": "updates must be a list"}), 400

            for u in updates:
                if not isinstance(u, dict):
                    continue
                name = str(u.get("name") or "").strip()
                if not name:
                    continue
                val = u.get("display_status")
                try:
                    val_i = 1 if int(val) == 1 else 0
                except Exception:
                    val_i = 0

                cur.execute(
                    "UPDATE cats SET display_status=%s WHERE name=%s",
                    (val_i, name),
                )
                updated += int(cur.rowcount or 0)

            conn.commit()
            return jsonify({"updated": updated})

        return jsonify({"message": "body must contain 'selected' or 'updates'"}), 400
    finally:
        cur.close()
        conn.close()




@app.route("/api/cats/update", methods=["PATCH"])
def update_cat():
    """Update cat name and/or reset/set real_image_url.

    JSON body:
      - old_name (required)  [or oldName]
      - new_name (optional)  [or newName]
      - reset_image (optional bool) [also reset_real_image] => sets real_image_url = NULL
      - real_image_url (optional string) => if provided, store as URL (prefer /assets/... or http(s)://...)
    """
    data = request.get_json(silent=True) or {}
    old_name = (data.get("old_name") or data.get("oldName") or "").strip()
    if not old_name:
        return jsonify({"error": "old_name is required"}), 400

    new_name = (data.get("new_name") or data.get("newName") or "").strip()
    reset_image = bool(data.get("reset_image") or data.get("resetImage") or data.get("reset_real_image") or data.get("resetRealImage") or data.get("resetReal"))
    new_real = data.get("real_image_url") or data.get("realImageUrl")

    if isinstance(new_real, str) and new_real.strip():
        new_real = normalize_image_to_url(new_real.strip())

    conn = mysql.connector.connect(**db_config)
    cur = conn.cursor()
    try:
        if new_name and new_name != old_name:
            cur.execute("UPDATE cats SET name=%s WHERE name=%s", (new_name, old_name))
            # best-effort cascade to common tables
            cascade_updates = [
                ("UPDATE alerts_log SET cat_name=%s WHERE cat_name=%s", (new_name, old_name)),
                ("UPDATE cat_activities SET cat_name=%s WHERE cat_name=%s", (new_name, old_name)),
                ("UPDATE cat_movements SET cat_name=%s WHERE cat_name=%s", (new_name, old_name)),
            ]
            for sql, params in cascade_updates:
                try:
                    cur.execute(sql, params)
                except mysql.connector.Error as e:
                    # 1146 = table doesn't exist (some DB schemas don't have these tables)
                    if getattr(e, "errno", None) == 1146:
                        continue
                    raise
            old_name = new_name

        if reset_image:
            cur.execute("UPDATE cats SET real_image_url=NULL WHERE name=%s", (old_name,))
        elif isinstance(new_real, str) and new_real.strip():
            cur.execute("UPDATE cats SET real_image_url=%s WHERE name=%s", (new_real.strip(), old_name))

        conn.commit()
        return jsonify({"message": "updated"}), 200
    finally:
        cur.close()
        conn.close()


@app.route("/api/cats/upload_image", methods=["POST"])
def upload_cat_image():
    """Upload an image file and store it as cats.real_image_url.

    Expected multipart/form-data:
      - cat_name (or catName)
      - file (image)
    Returns:
      - { real_image_url: "/assets/uploads/<filename>" }
    """
    cat_name = (request.form.get("cat_name") or request.form.get("catName") or "").strip()
    if not cat_name:
        return jsonify({"error": "cat_name is required"}), 400

    if "file" not in request.files:
        return jsonify({"error": "file is required"}), 400

    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"error": "empty filename"}), 400

    filename = secure_filename(f.filename)
    if not _allowed_file(filename):
        return jsonify({"error": "invalid file type"}), 400

    ext = filename.rsplit(".", 1)[-1].lower()
    # Keep a readable filename (supports Thai), but avoid path separators.
    safe_cat = (cat_name or "cat").strip().replace("/", "_").replace("\\", "_")
    safe_cat = "_".join(safe_cat.split())
    if len(safe_cat) > 40:
        safe_cat = safe_cat[:40]
    new_name = f"{safe_cat}_{uuid.uuid4().hex}.{ext}"
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    save_path = os.path.join(UPLOADS_DIR, new_name)
    f.save(save_path)

    url_path = f"/assets/uploads/{new_name}"

    conn = mysql.connector.connect(**db_config)
    cur = conn.cursor()
    try:
        cur.execute("UPDATE cats SET real_image_url=%s WHERE name=%s", (url_path, cat_name))
        conn.commit()
    finally:
        cur.close()
        conn.close()

    return jsonify({"ok": True, "url": url_path, "real_image_url": url_path}), 200

# =========================================
# H) TIMESLOT (แทน cat_activities)
# =========================================
@app.route("/api/cat_activities", methods=["GET"])
def get_cat_activities_timeslot():
    """
    **แนวทาง B**: ไม่ใช้ cat_activities แล้ว → ใช้ timeslot แทน
    Response เป็น "ราย slot" (ไม่ใช่ start/end event)
    Query params:
      cat_name: (required) ชื่อแมว
      start_date, end_date: YYYY-MM-DD (optional)
      limit: default 5000 (กันโหลดหนัก)
    คืน:
      [
        {
          "cat_name": "...",
          "date_slot": "YYYY-MM-DD HH:MM:SS",
          "status": "F|NF|...",
          "cam": "C1|C2|...",
          "room": "hall|kitchen|...",
          "activity": "eat|excrete|NO|..."
        }, ...
      ]
    """
    cat_name = request.args.get("cat_name")
    start = request.args.get("start_date")
    end = request.args.get("end_date")
    limit = request.args.get("limit", "5000")

    if not cat_name:
        return jsonify({"message": "cat_name required"}), 400

    try:
        limit_n = int(limit)
        limit_n = max(1, min(limit_n, 20000))
    except ValueError:
        limit_n = 5000

    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT color FROM cats WHERE name=%s LIMIT 1", (cat_name,))
        crow = cursor.fetchone()
        if not crow:
            return jsonify([])

        prefix = _normalize_prefix(crow.get("color") or cat_name)
        cols = _get_timeslot_columns(cursor)

        status_col = prefix
        cam_col = f"{prefix}_cam"
        ac_col = f"{prefix}_ac"
        if not (status_col in cols and cam_col in cols and ac_col in cols and "date_slot" in cols):
            return jsonify([])

        # date filters
        where = []
        params = []

        if start:
            try:
                sdt = datetime.strptime(start, "%Y-%m-%d")
                where.append("date_slot >= %s")
                params.append(sdt)
            except ValueError:
                pass
        if end:
            try:
                edt = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
                where.append("date_slot < %s")
                params.append(edt)
            except ValueError:
                pass

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        sql = f"""
            SELECT date_slot,
                   `{status_col}` AS status,
                   `{cam_col}` AS cam,
                   `{ac_col}` AS activity
            FROM timeslot
            {where_sql}
            ORDER BY date_slot DESC
            LIMIT {limit_n}
        """
        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall() or []

        out = []
        for r in rows:
            cam = r.get("cam")
            out.append(
                {
                    "cat_name": cat_name,
                    "date_slot": r["date_slot"].strftime("%Y-%m-%d %H:%M:%S") if r.get("date_slot") else None,
                    "status": r.get("status"),
                    "cam": cam,
                    "room": CAM_CODE_TO_ROOM.get(str(cam).strip(), None) if cam else None,
                    "activity": r.get("activity"),
                }
            )
        return jsonify(out)
    finally:
        cursor.close()
        connection.close()


# =========================================
# I) STATISTICS API (FROM TIMESLOT)
# =========================================


@app.route("/api/timeline", methods=["GET"])
def get_timeline_timeslot():
    """
    Timeline ราย 10 วินาทีแบบ scroll (cursor-based)
    ใช้ timeslot + mapping จาก cats.color

    Query params:
      cat: (required) ชื่อแมว (เช่น Black)
      date: YYYY-MM-DD (optional) จำกัดเฉพาะวันนั้น (default = today)
      before: ISO datetime (optional) โหลดรายการที่ date_slot < before (ใช้สำหรับ scroll ต่อ)
      limit: จำนวนแถว (default 300, max 2000)

    Response:
      {
        "date": "YYYY-MM-DD",
        "rows": [ ... ],
        "returned": N,
        "has_more": true/false,
        "next_before": "YYYY-MM-DD HH:MM:SS" | null
      }
    """
    cat = request.args.get("cat", "").strip()
    date_str = request.args.get("date", "").strip()
    before_str = request.args.get("before", "").strip()
    limit_str = request.args.get("limit", "").strip()

    if not cat:
        return jsonify({"message": "cat required"}), 400

    # limit
    try:
        limit_n = int(limit_str or 300)
        limit_n = max(1, min(limit_n, 2000))
    except ValueError:
        limit_n = 300

    # date range
    if date_str:
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"message": "date must be YYYY-MM-DD"}), 400
    else:
        day = datetime.now().date()
        date_str = day.strftime("%Y-%m-%d")

    day_start = datetime.combine(day, time.min)
    day_end = day_start + timedelta(days=1)

    # before cursor
    before_dt = None
    if before_str:
        try:
            before_dt = datetime.strptime(before_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                before_dt = datetime.fromisoformat(before_str)
            except ValueError:
                return jsonify({"message": "before must be datetime"}), 400

    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor(dictionary=True)
    try:
        # หา prefix จาก cats.color -> lower
        cursor.execute("SELECT color FROM cats WHERE name=%s LIMIT 1", (cat,))
        row = cursor.fetchone()
        if not row or not row.get("color"):
            return jsonify({"message": f"cat not found: {cat}"}), 404

        prefix = str(row["color"]).strip().lower()
        # ตรวจว่าคอลัมน์นี้มีใน timeslot จริง
        cols = _get_timeslot_columns(cursor)
        needed = {prefix, f"{prefix}_cam", f"{prefix}_ac"}
        if not needed.issubset(cols):
            return jsonify({
                "message": "timeslot columns not found for this cat color",
                "expected_columns": sorted(list(needed))
            }), 400

        where = [f"date_slot >= %s", f"date_slot < %s"]
        params = [day_start, day_end]

        if before_dt:
            where.append("date_slot < %s")
            params.append(before_dt)

        # ดึงมากกว่า 1 แถวเพื่อเช็ค has_more
        sql = f"""
            SELECT
              date_slot,
              `{prefix}` AS status,
              `{prefix}_cam` AS cam,
              `{prefix}_ac` AS activity
            FROM timeslot
            WHERE {' AND '.join(where)}
            ORDER BY date_slot DESC
            LIMIT %s
        """
        params.append(limit_n + 1)
        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall() or []

        has_more = len(rows) > limit_n
        rows = rows[:limit_n]

        out = []
        for r in rows:
            cam = r.get("cam")
            room = CAM_CODE_TO_ROOM.get(str(cam).strip().upper()) if cam else None
            out.append({
                "cat_name": cat,
                "date_slot": r.get("date_slot").strftime("%Y-%m-%d %H:%M:%S") if r.get("date_slot") else None,
                "status": r.get("status"),
                "cam": cam,
                "room": room or "-",
                "activity": r.get("activity"),
            })

        next_before = out[-1]["date_slot"] if out else None

        return jsonify({
            "date": date_str,
            "rows": out,
            "returned": len(out),
            "has_more": bool(has_more),
            "next_before": next_before
        })
    finally:
        cursor.close()
        connection.close()


# =========================================
# I.1) TIMELINE TABLE (HOURLY / DAILY GRID)
# =========================================

# =========================================
@app.route("/api/timeline_table")
def api_timeline_table():
    """
    ตาราง Timeline รายชั่วโมง (00-23) สำหรับ "ทุกแมวที่ display_status=1"
    อิงจาก timeslot ในช่วงวันเดียวตาม query param:
      - date=YYYY-MM-DD (required)

    Response:
      {
        "date": "YYYY-MM-DD",
        "hours": ["00","01",...,"23"],
        "rows": [
          {
            "date": "...",
            "color": "...",
            "cat_name": "...",
            "cells": { "00": "...", ... }
          }, ...
        ]
      }
    """
    date_str = request.args.get("date")
    if not date_str:
        return jsonify({"message": "date is required (YYYY-MM-DD)"}), 400

    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"message": "date must be YYYY-MM-DD"}), 400

    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)

    conn = get_db()
    cur = conn.cursor(dictionary=True)

    try:
        cur.execute("SELECT name, color FROM cats WHERE display_status=1 ORDER BY name")
        cats = cur.fetchall() or []
        cols = _get_timeslot_columns(cur)

        hours = [f"{h:02d}" for h in range(24)]
        rows = []

        for cat in cats:
            prefix = _normalize_prefix(cat.get("color"))
            if not prefix:
                continue

            # ต้องมีคอลัมน์ prefix/prefix_cam/prefix_ac ใน timeslot
            if not all(c in cols for c in (prefix, f"{prefix}_cam", f"{prefix}_ac")):
                continue

            cur.execute(
                f"""
                SELECT date_slot,
                       `{prefix}` AS status,
                       `{prefix}_cam` AS cam,
                       `{prefix}_ac` AS activity
                FROM timeslot
                WHERE date_slot >= %s AND date_slot < %s
                ORDER BY date_slot ASC
                """,
                (start, end),
            )

            slots = cur.fetchall() or []
            per_hour = {h: [] for h in range(24)}
            for s in slots:
                dt = s.get("date_slot")
                if not dt:
                    continue
                per_hour[int(dt.hour)].append(s)

            cells = {}
            for h in range(24):
                slot_list = per_hour[h]
                if not slot_list:
                    cells[f"{h:02d}"] = "-"
                    continue

                found = [x for x in slot_list if (x.get("status") or "").upper() == "F"]
                if not found:
                    cells[f"{h:02d}"] = "Not found (NF)"
                    continue

                # สรุป (1) "จำนวนครั้ง" ของพฤติกรรมต่อชั่วโมง (นับแบบ transition)
                #     และ (2) "ระยะเวลา" ที่อยู่ในพฤติกรรมนั้นต่อชั่วโมง (นับจากจำนวน timeslot)
                # key = (activity, room) เช่น ("eat","kitchen")
                counts = {}          # transition counts (ครั้ง)
                slot_counts = {}     # raw slot counts (จำนวน timeslot) สำหรับคำนวณเวลา
                order = []           # preserve first-seen order for nicer display
                last_key = None

                for s in found:
                    act_raw = s.get("activity")
                    act = (str(act_raw).strip().lower() if act_raw is not None else "")
                    if act in ("no", "", "none", "no activity"):
                        # ไม่ใช่พฤติกรรมที่ต้องการนับ และให้รีเซ็ต transition
                        last_key = None
                        continue

                    if act not in ("eat", "excrete"):
                        # ถ้ามี activity อื่น ๆ ก็ไม่เอามานับ แต่รีเซ็ต transition เช่นกัน
                        last_key = None
                        continue

                    cam = s.get("cam")
                    room = CAM_CODE_TO_ROOM.get(str(cam).strip(), "") if cam else ""
                    room = room or "-"

                    key = (act, room)

                    # duration: นับทุก slot ที่เป็นพฤติกรรมนี้ (ไม่สน transition)
                    slot_counts[key] = slot_counts.get(key, 0) + 1

                    # count: นับแบบ transition (เปลี่ยนพฤติกรรม/ห้องแล้วค่อย +1)
                    if key != last_key:
                        if key not in counts:
                            order.append(key)
                            counts[key] = 0
                        counts[key] += 1
                        last_key = key

                if not counts:
                    cells[f"{h:02d}"] = "-"
                else:
                    # 1 slot = 10 วินาที
                    def _fmt_minutes(slots: int) -> str:
                        mins = (slots * 10.0) / 60.0
                        # ปัดเป็น 1 ตำแหน่ง (เช่น 2.0 -> 2)
                        mins_1 = round(mins, 1)
                        if abs(mins_1 - int(mins_1)) < 1e-9:
                            return str(int(mins_1))
                        return f"{mins_1:.1f}"

                    parts = []
                    for k in order:
                        c = counts.get(k, 0)
                        sc = slot_counts.get(k, 0)
                        m = _fmt_minutes(sc)
                        parts.append(f"{c} {k[0]} @{k[1]} ({m} นาที)")

                    cells[f"{h:02d}"] = ", ".join(parts)


            rows.append(
                {
                    "date": date_str,
                    "color": cat.get("color"),
                    "cat_name": cat.get("name"),
                    "cells": cells,
                }
            )

        return jsonify({"date": date_str, "hours": hours, "rows": rows})
    finally:
        cur.close()
        conn.close()

@app.route("/api/statistics/years", methods=["GET"])
def api_statistics_years():
    """คืน 'ทุกปี' ที่มีข้อมูลใน timeslot (เรียง ASC)"""
    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT DISTINCT YEAR(date_slot) AS y
            FROM timeslot
            WHERE date_slot IS NOT NULL
            ORDER BY y ASC
            """
        )
        years = [int(r["y"]) for r in cursor.fetchall() if r.get("y") is not None]
        return jsonify({"years": years})
    finally:
        cursor.close()
        connection.close()


def _aggregate_counts_by_period(prefix: str, slots, period: str):
    """
    สร้าง labels + series จาก slots (list of dict)
    period: daily/monthly/yearly/range
    นับแบบ "transition count" ต่อช่วงเวลา
    """
    buckets = {}  # label -> list(slots)
    for s in slots:
        dt = s.get("date_slot")
        if not dt:
            continue
        if period == "monthly":
            label = f"{dt.year:04d}-{dt.month:02d}"
        elif period == "yearly":
            label = f"{dt.year:04d}"
        else:
            label = dt.strftime("%Y-%m-%d")
        buckets.setdefault(label, []).append(s)

    labels = sorted(buckets.keys())
    eat_series = []
    exc_series = []
    for lb in labels:
        bslots = buckets[lb]
        eat_series.append(_count_activity_transitions(bslots, "eat"))
        exc_series.append(_count_activity_transitions(bslots, "excrete"))

    return labels, eat_series, exc_series


@app.route("/api/statistics", methods=["GET"])
def api_statistics():
    """
    Query params:
      cat: ชื่อแมว (จำเป็น)
      period: daily | monthly | yearly | range
      year: ใช้กับ daily/monthly (ปีสิ้นสุด)
      month: ใช้กับ daily (เดือน 01-12)
      start_year, end_year: ใช้กับ yearly
      start_date, end_date: ใช้กับ range (YYYY-MM-DD)
    NOTE: ใช้ timeslot แทน cat_activities แล้ว
    """
    cat = request.args.get("cat")
    period = (request.args.get("period") or "daily").lower()
    year = request.args.get("year")
    month = request.args.get("month")
    start_year = request.args.get("start_year")
    end_year = request.args.get("end_year") or year
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    if not cat:
        return jsonify({"message": "missing cat"}), 400

    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT color FROM cats WHERE name=%s LIMIT 1", (cat,))
        crow = cursor.fetchone()
        if not crow:
            return jsonify({"labels": [], "series": {}, "summary": {}})

        prefix = _normalize_prefix(crow.get("color") or cat)

        # bounds
        cursor.execute(
            """
            SELECT MIN(YEAR(date_slot)) AS miny,
                   MAX(YEAR(date_slot)) AS maxy
            FROM timeslot
            WHERE date_slot IS NOT NULL
            """
        )
        bounds = cursor.fetchone() or {}
        miny = int(bounds["miny"]) if bounds.get("miny") else None
        maxy = int(bounds["maxy"]) if bounds.get("maxy") else None

        labels, eat_cnt, excrete_cnt = [], [], []
        total_eat_cnt = 0
        total_excrete = 0

        if period == "range":
            if not start_date or not end_date:
                return jsonify({"labels": [], "series": {}, "summary": {}})

            sdt = datetime.strptime(start_date, "%Y-%m-%d")
            edt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)

            slots = _fetch_timeslots_for_cat(cursor, prefix, sdt, edt)
            labels, eat_cnt, excrete_cnt = _aggregate_counts_by_period(prefix, slots, "daily")

        elif period == "daily":
            if not year:
                if maxy:
                    year = str(maxy)
                else:
                    return jsonify({"labels": [], "series": {}, "summary": {}})
            if not month:
                month = "01"

            y = int(year)
            m = int(month)
            start_dt = datetime(y, m, 1)
            if m == 12:
                end_dt = datetime(y + 1, 1, 1)
            else:
                end_dt = datetime(y, m + 1, 1)

            slots = _fetch_timeslots_for_cat(cursor, prefix, start_dt, end_dt)
            labels, eat_cnt, excrete_cnt = _aggregate_counts_by_period(prefix, slots, "daily")

        elif period == "monthly":
            if not year:
                if maxy:
                    year = str(maxy)
                else:
                    return jsonify({"labels": [], "series": {}, "summary": {}})

            y = int(year)
            start_dt = datetime(y, 1, 1)
            end_dt = datetime(y + 1, 1, 1)

            slots = _fetch_timeslots_for_cat(cursor, prefix, start_dt, end_dt)
            labels, eat_cnt, excrete_cnt = _aggregate_counts_by_period(prefix, slots, "monthly")

        else:
            # yearly
            if not end_year and maxy:
                end_year = str(maxy)
            if not start_year and miny:
                start_year = str(miny)
            if not start_year or not end_year:
                return jsonify({"labels": [], "series": {}, "summary": {}})

            s_y = int(start_year)
            e_y = int(end_year)
            if miny is not None:
                s_y = max(s_y, miny)
            if maxy is not None:
                e_y = min(e_y, maxy)
            if s_y > e_y:
                s_y, e_y = e_y, s_y

            start_dt = datetime(s_y, 1, 1)
            end_dt = datetime(e_y + 1, 1, 1)

            slots = _fetch_timeslots_for_cat(cursor, prefix, start_dt, end_dt)
            labels, eat_cnt, excrete_cnt = _aggregate_counts_by_period(prefix, slots, "yearly")

        total_eat_cnt = sum(int(x or 0) for x in eat_cnt)
        total_excrete = sum(int(x or 0) for x in excrete_cnt)

        return jsonify(
            {
                "labels": labels,
                "series": {
                    "eatCount": eat_cnt,
                    "excreteCount": excrete_cnt,
                },
                "summary": {
                    "totalEatCount": total_eat_cnt,
                    "totalExcreteCount": total_excrete,
                },
            }
        )
    finally:
        cursor.close()
        connection.close()


# =========================================
# J) ROOM TIMELINE (LATEST DAY) - FROM TIMESLOT
# =========================================
@app.route("/api/statistics/room_timeline", methods=["GET"])
def api_room_timeline_latest_day():
    """
    คืนตาราง 'ห้องที่แมวอยู่' รายชั่วโมง (00:00-23:00) ของ 'วันล่าสุด' ที่ปรากฏในฐานข้อมูล (timeslot)
    Query params:
      cat: ชื่อแมว (จำเป็น)
    Response:
      {
        "date": "YYYY-MM-DD",
        "hours": ["00:00",...,"23:00"],
        "rooms": ["kitchen",...]
      }
    """
    cat = request.args.get("cat")
    if not cat:
        return jsonify({"date": None, "hours": [], "rooms": []}), 400

    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT color FROM cats WHERE name=%s LIMIT 1", (cat,))
        crow = cursor.fetchone()
        if not crow:
            return jsonify({"date": None, "hours": [], "rooms": []})

        prefix = _normalize_prefix(crow.get("color") or cat)
        latest_day = _latest_date_in_timeslot(cursor)
        if not latest_day:
            return jsonify({"date": None, "hours": [], "rooms": []})

        day_start = datetime.combine(latest_day, time.min)
        day_end = day_start + timedelta(days=1)

        slots = _fetch_timeslots_for_cat(cursor, prefix, day_start, day_end)

        # สร้าง mapping ชั่วโมง -> ห้อง (ใช้ slot ล่าสุดที่ status='F' ภายในชั่วโมงนั้น)
        hours = [f"{h:02d}:00" for h in range(24)]
        rooms = []

        # เตรียม list ต่อชั่วโมง
        idx = 0
        for h in range(24):
            h_start = day_start + timedelta(hours=h)
            h_end = h_start + timedelta(hours=1)

            last_cam = None
            # เดิน idx ต่อเนื่องเพื่อลดเวลา (slots เรียง ASC)
            while idx < len(slots) and slots[idx].get("date_slot") < h_start:
                idx += 1

            j = idx
            while j < len(slots):
                dt = slots[j].get("date_slot")
                if not dt or dt >= h_end:
                    break
                if (slots[j].get("status") or "").upper() == "F":
                    cam = slots[j].get("cam")
                    if cam:
                        last_cam = str(cam).strip()
                j += 1

            room = CAM_CODE_TO_ROOM.get(last_cam) if last_cam else None
            rooms.append(room if room else "-")

        return jsonify(
            {
                "date": latest_day.strftime("%Y-%m-%d"),
                "hours": hours,
                "rooms": rooms,
            }
        )
    finally:
        cursor.close()
        connection.close()


# =========================================
# MAIN
# =========================================
# ============================================================
# Daily vs Realtime alert modes
# ============================================================

def _ingest_alerts_list(cursor, target_day: date, alerts: list[dict]) -> int:
    """Insert alerts list into alerts_log using same duplicate rules as _ingest_alerts_for_day.

    alerts item schema:
      {"cat_name": str, "alert_type": str, "message": str}
    Returns number of inserted rows.
    """
    table = "alerts_log"
    inserted = 0

    has_color = _table_has_column(cursor, table, "color")
    alert_date_generated = _is_generated_column(cursor, table, "alert_date")

    for al in alerts:
        cat_name = al.get("cat_name")
        alert_type = al.get("alert_type")
        message = al.get("message")
        if not (cat_name and alert_type and message):
            continue

        # กันซ้ำ: (cat_name, alert_type, alert_date) และ is_read <> 2
        cursor.execute(
            f"""
            SELECT id
            FROM `{table}`
            WHERE cat_name = %s AND alert_type = %s AND alert_date = %s AND is_read <> 2
            LIMIT 1
            """,
            (cat_name, alert_type, target_day),
        )
        if cursor.fetchone():
            continue

        # color (ถ้าตารางมี)
        color_val = None
        if has_color:
            cursor.execute("SELECT color FROM cats WHERE name=%s LIMIT 1", (cat_name,))
            r = cursor.fetchone() or {}
            color_val = r.get("color")

        # ถ้า alert_date เป็น generated: ห้าม insert alert_date เอง
        if alert_date_generated:
            created_at = datetime.combine(target_day, time(23, 59, 0))
            if has_color:
                cursor.execute(
                    f"""
                    INSERT INTO `{table}` (cat_name, color, alert_type, message, is_read, created_at)
                    VALUES (%s, %s, %s, %s, 0, %s)
                    """,
                    (cat_name, color_val, alert_type, message, created_at),
                )
            else:
                cursor.execute(
                    f"""
                    INSERT INTO `{table}` (cat_name, alert_type, message, is_read, created_at)
                    VALUES (%s, %s, %s, 0, %s)
                    """,
                    (cat_name, alert_type, message, created_at),
                )
        else:
            if has_color:
                cursor.execute(
                    f"""
                    INSERT INTO `{table}` (cat_name, color, alert_type, message, is_read, alert_date, created_at)
                    VALUES (%s, %s, %s, %s, 0, %s, NOW())
                    """,
                    (cat_name, color_val, alert_type, message, target_day),
                )
            else:
                cursor.execute(
                    f"""
                    INSERT INTO `{table}` (cat_name, alert_type, message, is_read, alert_date, created_at)
                    VALUES (%s, %s, %s, 0, %s, NOW())
                    """,
                    (cat_name, alert_type, message, target_day),
                )

        inserted += 1

    return inserted


def _ingest_daily_behavior_for_day(cursor, target_day: date) -> int:
    """Daily mode: ingest only eating/excretion alerts for target_day."""
    all_alerts = _compute_alerts_for_day(cursor, target_day)
    behavior = [a for a in all_alerts if a.get("alert_type") in ("no_eating", "low_excrete", "high_excrete")]
    return _ingest_alerts_list(cursor, target_day, behavior)


def _compute_no_cat_realtime(cursor) -> list[dict]:
    """Realtime mode: compute only no_cat using NOW() as reference time."""
    alerts = []

    global_cfg = _get_system_config_global(cursor)
    global_no_cat_hours = int(global_cfg.get("alert_no_cat", 12) or 12)

    cat_prefix = _get_cat_prefix_map(cursor)

    now_dt = datetime.now()

    for cat_name, prefix in cat_prefix.items():
        # per-cat config fallback
        cursor.execute("SELECT color FROM cats WHERE name=%s LIMIT 1", (cat_name,))
        crow = cursor.fetchone() or {}
        cat_color = (crow.get("color") or "").strip()
        cfg = _get_effective_config(cursor, cat_color)
        no_cat_hours = int(cfg.get("alert_no_cat", global_no_cat_hours) or global_no_cat_hours)

        last_found = _time_last_found(cursor, prefix)
        if not last_found:
            continue

        hours_since = (now_dt - last_found).total_seconds() / 3600.0
        if hours_since >= no_cat_hours:
            alerts.append(
                {
                    "cat_name": cat_name,
                    "alert_type": "no_cat",
                    "message": f"ไม่พบ {cat_name} เกิน {no_cat_hours} ชั่วโมง",
                }
            )

    return alerts


def _ingest_realtime_no_cat(cursor) -> int:
    """Realtime mode: ingest only no_cat alerts using NOW()."""
    alerts = _compute_no_cat_realtime(cursor)
    # บันทึกด้วย target_day = วันนี้ (เพื่อ grouping ใน alerts_log)
    target_day = date.today()
    return _ingest_alerts_list(cursor, target_day, alerts)



# ============================================================
# Authentication (Email/Password) + Role-based Admin
# - users register with email; default role=user and is_approved=0
# - admin approves pending accounts
# - forgot password sends real email via SMTP (config via env)
# ============================================================

def _ensure_users_table():
    """Ensure the users table exists (MySQL)."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
              id INT AUTO_INCREMENT PRIMARY KEY,
              name VARCHAR(120) NOT NULL,
              email VARCHAR(190) NOT NULL,
              password_hash VARCHAR(255) NOT NULL,
              role VARCHAR(20) NOT NULL DEFAULT 'user',
              is_approved TINYINT(1) NOT NULL DEFAULT 0,
              approved_at DATETIME NULL,
              reset_token_hash VARCHAR(64) NULL,
              reset_token_expires DATETIME NULL,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              UNIQUE KEY uniq_email (email)
            );
            """
        )
        conn.commit()
    finally:
        try:
            cur.close()
        finally:
            conn.close()


def _bootstrap_admin_user():
    """Optionally create/approve an initial admin user from env vars.

    Set:
      - BOOTSTRAP_ADMIN_EMAIL
      - BOOTSTRAP_ADMIN_PASSWORD
      - BOOTSTRAP_ADMIN_NAME (optional, default 'Admin')
    """
    email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL")
    pwd = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")
    name = os.environ.get("BOOTSTRAP_ADMIN_NAME", "Admin")
    if not email or not pwd:
        return

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, role FROM users WHERE email=%s", (email,))
        row = cur.fetchone()
        if row:
            # Ensure role + approval
            cur.execute(
                "UPDATE users SET role='admin', is_approved=1, approved_at=COALESCE(approved_at, NOW()) WHERE id=%s",
                (row["id"],),
            )
        else:
            cur.execute(
                """
                INSERT INTO users (name, email, password_hash, role, is_approved, approved_at)
                VALUES (%s, %s, %s, 'admin', 1, NOW())
                """,
                (name, email, generate_password_hash(pwd)),
            )
        conn.commit()
    finally:
        try:
            cur.close()
        finally:
            conn.close()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _smtp_config():
    """Read SMTP settings from env. Raises ValueError if missing."""
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    use_tls = os.environ.get("SMTP_USE_TLS", "1").strip() in ("1", "true", "True", "yes", "YES")
    use_ssl = os.environ.get("SMTP_USE_SSL", "0").strip() in ("1", "true", "True", "yes", "YES")
    mail_from = os.environ.get("SMTP_FROM") or user
    if not host or not port or not mail_from:
        raise ValueError("SMTP is not configured: set SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS/SMTP_FROM")
    return host, port, user, password, use_tls, use_ssl, mail_from


def _send_email(to_email: str, subject: str, body_text: str):
    """Send a plain-text email using SMTP env settings."""
    host, port, user, password, use_tls, use_ssl, mail_from = _smtp_config()
    msg = EmailMessage()
    msg["From"] = mail_from
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body_text)

    if use_ssl:
        server = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)

    try:
        server.ehlo()
        if use_tls and not use_ssl:
            server.starttls()
            server.ehlo()
        if user and password:
            server.login(user, password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


def _require_login():
    if not session.get("user_id"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return None


def _require_admin():
    if not session.get("user_id"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    return None


@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    if not session.get("user_id"):
        return jsonify({"ok": True, "logged_in": False})
    return jsonify(
        {
            "ok": True,
            "logged_in": True,
            "user": {
                "id": session.get("user_id"),
                "email": session.get("email"),
                "name": session.get("name"),
                "role": session.get("role"),
            },
        }
    )


@app.route("/api/auth/register", methods=["POST"])
def auth_register():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not name or not email or not password:
        return jsonify({"ok": False, "error": "missing_fields"}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        if cur.fetchone():
            return jsonify({"ok": False, "error": "email_exists"}), 409

        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, is_approved) VALUES (%s, %s, %s, 'user', 0)",
            (name, email, generate_password_hash(password)),
        )
        conn.commit()
        return jsonify({"ok": True, "message": "registered_pending_approval"})
    finally:
        try:
            cur.close()
        finally:
            conn.close()


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM users WHERE email=%s", (email,))
        user = cur.fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            return jsonify({"ok": False, "error": "invalid_credentials"}), 401
        if int(user.get("is_approved") or 0) != 1:
            return jsonify({"ok": False, "error": "not_approved"}), 403

        session["user_id"] = user["id"]
        session["email"] = user["email"]
        session["name"] = user["name"]
        session["role"] = user.get("role") or "user"

        return jsonify(
            {
                "ok": True,
                "user": {
                    "id": user["id"],
                    "email": user["email"],
                    "name": user["name"],
                    "role": user.get("role") or "user",
                },
            }
        )
    finally:
        try:
            cur.close()
        finally:
            conn.close()


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/auth/forgot", methods=["POST"])
def auth_forgot():
    """Forgot password: generate reset token, store hash, email the reset link."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    # Always respond OK to avoid user enumeration
    generic_ok = jsonify({"ok": True, "message": "if_account_exists_email_sent"})

    if not email:
        return generic_ok

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, name FROM users WHERE email=%s", (email,))
        user = cur.fetchone()
        if not user:
            return generic_ok

        token = secrets.token_urlsafe(32)
        token_hash = _hash_token(token)
        expires = datetime.utcnow() + timedelta(minutes=30)

        cur.execute(
            "UPDATE users SET reset_token_hash=%s, reset_token_expires=%s WHERE id=%s",
            (token_hash, expires, user["id"]),
        )
        conn.commit()

        base_url = os.environ.get("APP_BASE_URL", "http://localhost:5000")
        reset_link = f"{base_url}/login.html#reset?email={email}&token={token}"

        subject = "Pet Monitoring - Reset your password"
        body = (
            f"สวัสดี {user['name']},\n\n"
            f"คุณได้ขอรีเซ็ตรหัสผ่าน สำหรับบัญชี {email}\n"
            f"กรุณาเปิดลิงก์นี้ภายใน 30 นาทีเพื่อกำหนดรหัสผ่านใหม่:\n\n"
            f"{reset_link}\n\n"
            f"หากคุณไม่ได้เป็นผู้ร้องขอ กรุณาเพิกเฉยกับอีเมลนี้\n"
        )

        try:
            _send_email(email, subject, body)
        except Exception as e:
            app.logger.error(f"[AUTH] Email send failed: {e}", exc_info=True)
            # If SMTP is not configured, return error so you notice in production setup.
            return jsonify({"ok": False, "error": "email_send_failed"}), 500

        return generic_ok
    finally:
        try:
            cur.close()
        finally:
            conn.close()


@app.route("/api/auth/reset", methods=["POST"])
def auth_reset():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    token = data.get("token") or ""
    new_password = data.get("new_password") or ""

    if not email or not token or not new_password:
        return jsonify({"ok": False, "error": "missing_fields"}), 400

    token_hash = _hash_token(token)

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, reset_token_hash, reset_token_expires FROM users WHERE email=%s", (email,))
        user = cur.fetchone()
        if not user:
            return jsonify({"ok": False, "error": "invalid_token"}), 400

        if not user.get("reset_token_hash") or user.get("reset_token_hash") != token_hash:
            return jsonify({"ok": False, "error": "invalid_token"}), 400

        exp = user.get("reset_token_expires")
        if not exp:
            return jsonify({"ok": False, "error": "invalid_token"}), 400

        # exp may be naive datetime from MySQL connector; compare with utcnow
        if datetime.utcnow() > exp:
            return jsonify({"ok": False, "error": "token_expired"}), 400

        cur.execute(
            "UPDATE users SET password_hash=%s, reset_token_hash=NULL, reset_token_expires=NULL WHERE id=%s",
            (generate_password_hash(new_password), user["id"]),
        )
        conn.commit()
        return jsonify({"ok": True, "message": "password_updated"})
    finally:
        try:
            cur.close()
        finally:
            conn.close()


# -------------------------
# Admin endpoints (role=admin)
# -------------------------

@app.route("/api/admin/pending", methods=["GET"])
def admin_pending():
    err = _require_admin()
    if err:
        return err

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT id, name, email, role, created_at FROM users WHERE is_approved=0 ORDER BY created_at ASC"
        )
        rows = cur.fetchall() or []
        return jsonify({"ok": True, "pending": rows})
    finally:
        try:
            cur.close()
        finally:
            conn.close()


@app.route("/api/admin/users", methods=["GET"])
def admin_users():
    """List users for admin panel.
    Query:
      include_pending=1|0  (default 1)
    """
    err = _require_admin()
    if err:
        return err

    include_pending = (request.args.get("include_pending") or "1").strip().lower() in ("1", "true", "yes", "y")
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        if include_pending:
            cur.execute(
                """
                SELECT id, name, email, role, is_approved, approved_at, created_at
                FROM users
                ORDER BY created_at DESC
                """
            )
        else:
            cur.execute(
                """
                SELECT id, name, email, role, is_approved, approved_at, created_at
                FROM users
                WHERE is_approved=1
                ORDER BY created_at DESC
                """
            )
        rows = cur.fetchall() or []
        return jsonify({"ok": True, "users": rows})
    finally:
        try:
            cur.close()
        finally:
            conn.close()


@app.route("/api/admin/approve", methods=["POST"])
def admin_approve():
    err = _require_admin()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "missing_user_id"}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET is_approved=1, approved_at=NOW() WHERE id=%s", (user_id,))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        try:
            cur.close()
        finally:
            conn.close()


@app.route("/api/admin/reject", methods=["POST"])
def admin_reject():
    err = _require_admin()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "missing_user_id"}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        # only delete if still pending
        cur.execute("DELETE FROM users WHERE id=%s AND is_approved=0", (user_id,))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        try:
            cur.close()
        finally:
            conn.close()


@app.route("/api/admin/set_role", methods=["POST"])
def admin_set_role():
    err = _require_admin()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    role = (data.get("role") or "").strip().lower()
    if not user_id or role not in ("user", "admin"):
        return jsonify({"ok": False, "error": "invalid_fields"}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET role=%s WHERE id=%s", (role, user_id))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        try:
            cur.close()
        finally:
            conn.close()


# Create table + optional bootstrap admin at startup
try:
    _ensure_users_table()
    _bootstrap_admin_user()
except Exception as e:
    app.logger.error(f"[AUTH] users table/bootstrap error: {e}", exc_info=True)


# ============================================================
# In-app scheduler (Daily at 23:59 Asia/Bangkok)
# ============================================================

_scheduler = None


def _run_daily_summary_job():
    """Run daily behavior (eat/excrete) alerts for today at 23:59."""
    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor(dictionary=True)
    try:
        target_day = date.today()
        inserted = _ingest_daily_behavior_for_day(cursor, target_day)
        connection.commit()
        try:
            if inserted and inserted > 0:
                _send_web_push_to_all('Pet Monitoring', f'สรุปประจำวัน: มีการแจ้งเตือนใหม่ {inserted} รายการ', '/')
        except Exception:
            pass
        app.logger.info(f"[DAILY JOB] {target_day} inserted={inserted}")
    except Exception as e:
        app.logger.error(f"[DAILY JOB] Error: {e}", exc_info=True)
    finally:
        try:
            cursor.close()
        finally:
            connection.close()


def start_scheduler():
    """Start APScheduler to run daily summary at 23:59 Asia/Bangkok.

    Note: In Flask debug mode, Werkzeug reloads the process; we guard to avoid double-start.
    """
    global _scheduler

    if BackgroundScheduler is None or CronTrigger is None:
        app.logger.warning("[SCHEDULER] APScheduler not installed; daily scheduler disabled.")
        return

    if _scheduler is not None:
        return

    # Avoid starting twice under the dev reloader
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    _scheduler = BackgroundScheduler(timezone="Asia/Bangkok")
    _scheduler.add_job(
        _run_daily_summary_job,
        trigger=CronTrigger(hour=23, minute=59),
        id="daily_summary_2359",
        replace_existing=True,
    )
    _scheduler.start()
    app.logger.info("[SCHEDULER] Daily summary scheduled at 23:59 Asia/Bangkok")


# Start automatic alert push worker at import-time (for gunicorn/uwsgi too).
# IMPORTANT: If you run multiple Gunicorn workers, each worker process will start its own
# background thread(s), which can cause duplicated work/notifications.
# For Render, we recommend running a SINGLE worker (e.g. --workers 1) unless you refactor
# the background workers into a separate process.
if os.environ.get("START_BACKGROUND_WORKERS", "1") == "1":
    try:
        _start_alert_push_worker()
    except Exception as e:  # pragma: no cover
        try:
            app.logger.error(f"[PUSH WORKER] start error: {e}", exc_info=True)
        except Exception:
            pass


if __name__ == "__main__":
    # Local dev entrypoint. On Render, use Gunicorn (see Procfile / start command).
    start_scheduler()
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Nguồn video ────────────────────────────────────────────────────────────
# Dùng webcam:    CAMERA_SOURCE = 0
# Dùng file .mp4: CAMERA_SOURCE = "test.mp4"   (hoặc "1.mp4", "2.mp4", ...)
CAMERA_SOURCE = "1.mp4"

MODEL_PATH = os.path.join(BASE_DIR, "yolov8n.pt")
OUTPUT_DIR = os.path.join(BASE_DIR, "alerts")   # thư mục lưu ảnh cảnh báo
STATIC_DIR = os.path.join(BASE_DIR, "static")   # thư mục frontend

# ── Vùng cấm (Forbidden Zone) ──────────────────────────────────────────────
ZONE_HOLD_SECS = 3.0   # phải đứng trong zone ≥ N giây mới gửi cảnh báo
ZONE_COOLDOWN  = 5     # cooldown giữa các lần cảnh báo cùng track_id (giây)
ZONE_FLASH_HZ  = 0.5   # tốc độ nhấp nháy overlay (giây/chu kỳ)

# ── Telegram ────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = ""
TELEGRAM_CHAT_ID = "6560022754"

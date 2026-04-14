"""
router.py – Định nghĩa tất cả API endpoint của hệ thống CamAI.

Bao gồm:
  - Phục vụ trang HTML (index, history)
  - API cấu hình (GET/POST /api/config)
  - API vùng cấm (GET/POST/DELETE /api/zone)
  - API lịch sử cảnh báo (GET /api/history)
  - Video stream MJPEG (GET /api/stream)
  - SSE real-time alerts (GET /api/alerts)
  - Test Telegram (POST /api/test-telegram)
"""

import os
import time
import json
import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from typing import List

from config import STATIC_DIR

# ── Khởi tạo router ────────────────────────────────────────────────────────
router = APIRouter()

# ── Đường dẫn file lưu trữ ─────────────────────────────────────────────────
_BASE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(_BASE, "settings.json")
ZONE_FILE     = os.path.join(_BASE, "zone.json")


# ── Quản lý vùng cấm (Zone) ────────────────────────────────────────────────
def load_zone() -> list:
    """Tải tọa độ vùng cấm từ file khi khởi động."""
    if not os.path.exists(ZONE_FILE):
        return []
    try:
        with open(ZONE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def save_zone(points: list):
    """Lưu tọa độ vùng cấm vào file JSON."""
    try:
        with open(ZONE_FILE, "w") as f:
            json.dump(points, f)
    except Exception as e:
        print(f"[WARN] Không lưu được zone: {e}")


def save_settings(data: dict):
    """Lưu cài đặt hệ thống vào file JSON."""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"[WARN] Không lưu được settings: {e}")


# Tải zone khi module được import
zone_points: List[List[int]] = load_zone()


# ── Pydantic models ────────────────────────────────────────────────────────
class ConfigBody(BaseModel):
    zone_hold_secs:   float
    zone_cooldown:    int
    telegram_token:   str
    telegram_chat_id: str
    zone_max_points:  int


class ZoneBody(BaseModel):
    points: List[List[int]]


# ── HTML Pages ─────────────────────────────────────────────────────────────
@router.get("/", response_class=HTMLResponse)
async def get_index():
    """Trả về trang dashboard chính."""
    path = os.path.join(STATIC_DIR, "index.html")
    return HTMLResponse(open(path, encoding="utf-8").read())


@router.get("/history", response_class=HTMLResponse)
async def get_history_page():
    """Trả về trang lịch sử cảnh báo."""
    path = os.path.join(STATIC_DIR, "history.html")
    return HTMLResponse(open(path, encoding="utf-8").read())


# ── API: Cấu hình hệ thống ─────────────────────────────────────────────────
@router.get("/api/config")
async def get_config():
    """Lấy cấu hình hiện tại."""
    import main
    return {
        "status":           "ok",
        "zone_hold_secs":   main.state.get("zone_hold_secs", 3.0),
        "zone_cooldown":    main.state.get("zone_cooldown", 5),
        "telegram_token":   main.state.get("telegram_token", ""),
        "telegram_chat_id": main.state.get("telegram_chat_id", ""),
        "zone_max_points":  main.state.get("zone_max_points", 4),
    }


@router.post("/api/config")
async def set_config(body: ConfigBody):
    """Cập nhật cấu hình và lưu vào file."""
    import main
    main.state["zone_hold_secs"]   = max(0.1, body.zone_hold_secs)
    main.state["zone_cooldown"]    = max(1,   body.zone_cooldown)
    main.state["telegram_token"]   = body.telegram_token.strip()
    main.state["telegram_chat_id"] = body.telegram_chat_id.strip()
    main.state["zone_max_points"]  = max(3,   body.zone_max_points)

    save_settings({
        "zone_hold_secs":   main.state["zone_hold_secs"],
        "zone_cooldown":    main.state["zone_cooldown"],
        "telegram_token":   main.state["telegram_token"],
        "telegram_chat_id": main.state["telegram_chat_id"],
        "zone_max_points":  main.state["zone_max_points"],
    })
    return {"status": "ok"}


# ── API: Kiểm tra kết nối Telegram ─────────────────────────────────────────
@router.post("/api/test-telegram")
async def test_telegram():
    """Gửi tin nhắn thử nghiệm qua Telegram."""
    import main
    import telegram_utils

    token   = main.state.get("telegram_token", "").strip()
    chat_id = main.state.get("telegram_chat_id", "").strip()

    if not token or not chat_id:
        return {"status": "error", "message": "Chưa cấu hình Token hoặc Chat ID"}

    try:
        msg = "🔔 Hệ thống CamAI: Tin nhắn kiểm tra kết nối thành công! ✅"
        await asyncio.to_thread(telegram_utils.send_telegram_text, token, chat_id, msg)
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": f"Lỗi: {e}"}


# ── API: Lịch sử cảnh báo ───────────────────────────────────────────────────
@router.get("/api/history")
async def get_history():
    """Lấy danh sách tất cả ảnh cảnh báo đã lưu."""
    import main

    records = []
    if not os.path.exists(main.OUTPUT_DIR):
        return {"status": "ok", "data": records}

    for fname in os.listdir(main.OUTPUT_DIR):
        if not fname.endswith(".jpg"):
            continue
        if not (fname.startswith("alert_") or fname.startswith("zone_")):
            continue
        try:
            # Định dạng tên file: alert_{tid}_{ts}.jpg hoặc zone_{tid}_{ts}.jpg
            parts = fname.replace(".jpg", "").split("_")
            tid, ts = int(parts[1]), int(parts[2])
            dt = time.localtime(ts)
            records.append({
                "id":        fname,
                "track_id":  tid,
                "timestamp": ts,
                "date":      time.strftime("%Y-%m-%d", dt),
                "time":      time.strftime("%H:%M:%S", dt),
                "img_url":   f"/alerts/{fname}",
                "intrusion": fname.startswith("zone_"),
            })
        except Exception:
            continue

    records.sort(key=lambda r: r["timestamp"], reverse=True)
    return {"status": "ok", "data": records}


# ── API: Vùng cấm ───────────────────────────────────────────────────────────
@router.get("/api/zone")
async def get_zone():
    """Lấy tọa độ vùng cấm hiện tại."""
    return {"status": "ok", "points": zone_points}


@router.post("/api/zone")
async def set_zone(body: ZoneBody):
    """Lưu polygon vùng cấm (tối thiểu 3 điểm)."""
    if len(body.points) < 3:
        return {"status": "error", "msg": "Cần tối thiểu 3 điểm"}

    global zone_points
    zone_points = body.points
    save_zone(zone_points)
    print(f"✅ Zone saved: {zone_points}")
    return {"status": "ok", "points": zone_points}


@router.delete("/api/zone")
async def reset_zone():
    """Xóa vùng cấm."""
    global zone_points
    zone_points = []
    save_zone([])
    print("✅ Zone reset")
    return {"status": "ok"}


# ── API: Stream video MJPEG ─────────────────────────────────────────────────
@router.get("/api/stream")
async def video_stream():
    """Stream video từ camera dưới dạng MJPEG."""
    import main
    return StreamingResponse(
        main.video_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


# ── API: SSE – Nhận cảnh báo real-time ────────────────────────────────────
@router.get("/api/alerts")
async def sse_alerts(request: Request):
    """Server-Sent Events: đẩy alert và trạng thái real-time tới trình duyệt."""
    import main

    async def generator():
        q = asyncio.Queue()
        main.sse_clients.append(q)
        try:
            while not await request.is_disconnected():
                yield f"data: {await q.get()}\n\n"
        finally:
            if q in main.sse_clients:
                main.sse_clients.remove(q)

    return StreamingResponse(generator(), media_type="text/event-stream")

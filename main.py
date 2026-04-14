"""
main.py – Khởi tạo ứng dụng FastAPI, load model YOLO và chạy vòng lặp xử lý video.
"""

import os
import time
import json
import asyncio
import threading

import cv2 as cv
import numpy as np
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from ultralytics import YOLO

import router
import telegram_utils
from config import (
    CAMERA_SOURCE, MODEL_PATH, OUTPUT_DIR, STATIC_DIR,
    ZONE_HOLD_SECS, ZONE_COOLDOWN,
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
)

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── Khởi động ứng dụng FastAPI ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("\n" + "="*50)
    print("🚀  http://localhost:8000")
    print("="*50 + "\n")
    yield


app = FastAPI(title="CamAI", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/alerts", StaticFiles(directory=OUTPUT_DIR),  name="alerts")
app.include_router(router.router)


# ── Khởi tạo model YOLOv8 ──────────────────────────────────────────────────
model      = YOLO(MODEL_PATH)
model_lock = threading.Lock()
PERSON_CLS = [k for k, v in model.names.items() if v == "person"] or [0]


# ── Trạng thái ứng dụng (shared state) ────────────────────────────────────
state: dict = {
    # Zone tracking
    "zone_enter_times": {},   # track_id → timestamp lần đầu vào zone
    "zone_missed":      {},   # track_id → số frame vắng mặt liên tiếp
    "zone_alerted":     set(),
    "zone_last_alert":  {},   # track_id → timestamp cảnh báo gần nhất
    "zone_hold_secs":   ZONE_HOLD_SECS,
    "zone_cooldown":    ZONE_COOLDOWN,
    "zone_max_points":  4,
    # Telegram
    "telegram_token":   TELEGRAM_TOKEN,
    "telegram_chat_id": TELEGRAM_CHAT_ID,
}


# ── Tải cài đặt từ file (nếu có) ───────────────────────────────────────────
def load_settings():
    """Ghi đè state bằng cài đặt đã lưu trước đó."""
    if not os.path.exists(router.SETTINGS_FILE):
        return
    try:
        with open(router.SETTINGS_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        keys = ["zone_hold_secs", "zone_cooldown", "zone_max_points",
                "telegram_token", "telegram_chat_id"]
        for key in keys:
            if key in cfg:
                state[key] = cfg[key]
    except Exception as e:
        print(f"[WARN] Không đọc được settings.json: {e}")


load_settings()


# ── SSE – danh sách client đang kết nối ───────────────────────────────────
sse_clients: list[asyncio.Queue] = []


async def _broadcast(payload: str):
    """Gửi dữ liệu tới tất cả client SSE đang kết nối."""
    dead = []
    for q in sse_clients:
        try:
            await q.put(payload)
        except Exception:
            dead.append(q)
    for q in dead:
        sse_clients.remove(q)


# ── Gửi cảnh báo (Telegram + SSE) ─────────────────────────────────────────
def dispatch_alert(img_path: str, track_id: int, is_intrusion: bool = False):
    """Gửi cảnh báo không đồng bộ: ảnh qua Telegram và sự kiện qua SSE."""
    now_struct = time.localtime()
    date_str   = time.strftime("%d/%m/%Y", now_struct)
    time_str   = time.strftime("%H:%M:%S", now_struct)
    msg        = "Xâm nhập vùng cấm!" if is_intrusion else "Phát hiện người"

    def _run():
        # Gửi Telegram
        telegram_utils.send_formatted_intrusion_alert(
            img_path,
            state["telegram_token"],
            state["telegram_chat_id"],
            track_id,
            is_intrusion,
            hold_secs=state["zone_hold_secs"],
        )
        # Gửi SSE cho trình duyệt
        payload = json.dumps({
            "id":        track_id,
            "time":      f"{date_str} {time_str}",
            "msg":       msg,
            "intrusion": is_intrusion,
            "img_url":   f"/alerts/{os.path.basename(img_path)}",
        })
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_broadcast(payload))
        except RuntimeError:
            asyncio.run(_broadcast(payload))

    threading.Thread(target=_run, daemon=True).start()


# ── Gửi trạng thái real-time ────────────────────────────────────────────────
def dispatch_status(people_count: int, anyone_inside: bool):
    """Gửi số người và trạng thái xâm nhập qua SSE theo thời gian thực."""
    payload = json.dumps({
        "type":      "status",
        "count":     people_count,
        "intrusion": anyone_inside,
    })
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_broadcast(payload))
    except RuntimeError:
        asyncio.run(_broadcast(payload))


# ── Sinh frame video có overlay ────────────────────────────────────────────
FRAME_W = 640
FRAME_H = 480
# Số frame vắng mặt tối đa trước khi reset trạng thái zone của một track
ZONE_MISS_TOLERANCE = 15


def video_generator():
    """
    Generator: đọc frame từ camera, chạy YOLO tracking, vẽ bbox,
    kiểm tra vùng cấm, phát cảnh báo và trả về JPEG stream.
    """
    if isinstance(CAMERA_SOURCE, str):
        cap = cv.VideoCapture(CAMERA_SOURCE)           # file video
    else:
        cap = cv.VideoCapture(CAMERA_SOURCE, cv.CAP_DSHOW)  # webcam

    if not cap.isOpened():
        print(f"❌ Không mở được nguồn: {CAMERA_SOURCE}")
        return

    cap.set(cv.CAP_PROP_FRAME_WIDTH,  FRAME_W)
    cap.set(cv.CAP_PROP_FRAME_HEIGHT, FRAME_H)
    cap.set(cv.CAP_PROP_FPS, 30)

    target_delay = 1.0 / 30
    frame_count = 0
    last_boxes  = None
    last_ids    = None
    last_confs  = None

    while True:
        t0 = time.time()

        ok, frame = cap.read()
        if not ok:
            if isinstance(CAMERA_SOURCE, str):   # nguồn là file video → loop lại
                cap.set(cv.CAP_PROP_POS_FRAMES, 0)
            else:                                 # nguồn là camera → thử lại
                time.sleep(0.05)
            continue

        disp         = frame.copy()
        now          = time.time()
        anyone_inside = False
        frame_count += 1

        # Chạy YOLO tracking (giảm tải bằng cách bỏ qua frame, xử lý 1 nửa số frame)
        if frame_count % 2 == 1 or last_boxes is None:
            with model_lock:
                results = model.track(frame, persist=True, verbose=False, classes=PERSON_CLS, imgsz=480)
            
            if results[0].boxes is not None and results[0].boxes.id is not None:
                last_boxes = results[0].boxes.xyxy.cpu().numpy()
                last_ids   = results[0].boxes.id.int().cpu().tolist()
                last_confs = results[0].boxes.conf.cpu().numpy()
            else:
                last_boxes, last_ids, last_confs = [], [], []

        active_ids = last_ids if last_ids else []

        if len(active_ids) > 0:
            boxes = last_boxes
            ids   = last_ids
            confs = last_confs

            # Scale tọa độ zone từ frontend (640×480) sang kích thước frame thực
            sh, sw = frame.shape[:2]
            sx, sy = sw / FRAME_W, sh / FRAME_H
            scaled_zone = np.array(
                [[int(p[0] * sx), int(p[1] * sy)] for p in router.zone_points],
                dtype=np.int32,
            )

            for box, tid, conf in zip(boxes, ids, confs):
                x1, y1, x2, y2 = map(int, box)

                # Kiểm tra người có nằm trong vùng cấm không
                inside = False
                point_x = (x1 + x2) // 2
                point_y = (y1 + y2) // 2
                
                if len(scaled_zone) >= 3:
                    inside = cv.pointPolygonTest(
                        scaled_zone, 
                        (float(point_x), float(point_y)), 
                        False
                    ) >= 0

                # Màu bbox: đỏ nếu vi phạm (trong vùng cấm), xanh lá nếu bình thường (ngoài vùng)
                color = (0, 0, 255) if inside else (0, 255, 0)
                cv.rectangle(disp, (x1, y1), (x2, y2), color, 2)
               
                # Vẽ điểm tâm 
                cv.circle(disp, (point_x, point_y), 6, color, -1)

                if inside:
                    anyone_inside = True
                    state["zone_missed"][tid] = 0

                    if tid not in state["zone_enter_times"]:
                        state["zone_enter_times"][tid] = now

                    duration       = now - state["zone_enter_times"][tid]
                    hold           = state["zone_hold_secs"]
                    cooldown       = state["zone_cooldown"]
                    last_tid_alert = state["zone_last_alert"].get(tid, 0)

                    # Gửi cảnh báo khi đủ thời gian hold và hết cooldown
                    if duration >= hold and now - last_tid_alert >= cooldown:
                        state["zone_last_alert"][tid] = now

                        # Tạo ảnh riêng để lưu cảnh báo kèm Zone
                        alert_img = disp.copy()
                        if len(scaled_zone) >= 3:
                            cv.polylines(alert_img, [scaled_zone], isClosed=True, color=(255, 0, 0), thickness=2)

                        path = os.path.join(OUTPUT_DIR, f"zone_{tid}_{int(now)}.jpg")
                        cv.imwrite(path, alert_img)

                        t_str = time.strftime("[%H:%M:%S]")
                        print(f"{t_str} | Camera 1 | people={len(active_ids)} | track_id={tid} | conf={conf:.2f}")
                        dispatch_alert(path, tid, is_intrusion=True)
                else:
                    # Cho phép vắng mặt tối đa ZONE_MISS_TOLERANCE frame trước khi reset
                    state["zone_missed"][tid] = state["zone_missed"].get(tid, 0) + 1
                    if state["zone_missed"][tid] > ZONE_MISS_TOLERANCE:
                        state["zone_enter_times"].pop(tid, None)
                        state["zone_alerted"].discard(tid)

        # Dọn dẹp track đã biến mất hoàn toàn khỏi kết quả YOLO
        for tid in list(state["zone_enter_times"]):
            if tid not in active_ids:
                state["zone_enter_times"].pop(tid, None)
                state["zone_alerted"].discard(tid)
                state["zone_missed"].pop(tid, None)
                state["zone_last_alert"].pop(tid, None)

        # Encode frame thành JPEG và trả về cho stream
        _, buf = cv.imencode(".jpg", disp, [cv.IMWRITE_JPEG_QUALITY, 85])
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"

        # Broadcast trạng thái real-time cho client SSE
        dispatch_status(len(active_ids), anyone_inside)

        elapsed = time.time() - t0
        time.sleep(max(0.0, target_delay - elapsed))


# ── Entry point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
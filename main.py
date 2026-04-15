
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


# ── Gửi trạng thái────────────────────────────────────────────────
def dispatch_status(people_count: int, anyone_inside: bool, fps: float = 0.0):
    """Gửi số người, trạng thái xâm nhập và FPS qua SSE theo thời gian thực."""
    payload = json.dumps({
        "type":      "status",
        "count":     people_count,
        "intrusion": anyone_inside,
        "fps":       round(fps, 1),
    })
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_broadcast(payload))
    except RuntimeError:
        asyncio.run(_broadcast(payload))


# ── Sinh frame video có overlay
FRAME_W = 640
FRAME_H = 480
# Số frame vắng mặt tối đa trước khi reset trạng thái zone của một track
ZONE_MISS_TOLERANCE = 15


def video_generator():
    """
    Generator chính xử lý luồng video (Camera stream).
    Quy trình được viết chuẩn theo 9 bước rõ ràng nhằm phục vụ mục đích giảng dạy
    và giúp sinh viên/nhà phát triển dễ theo dõi logic hệ thống camera AI.
    """
    if isinstance(CAMERA_SOURCE, str):
        cap = cv.VideoCapture(CAMERA_SOURCE)           # Nguồn: File video giả lập
    else:
        cap = cv.VideoCapture(CAMERA_SOURCE, cv.CAP_DSHOW)  # Nguồn: Webcam thực tế

    if not cap.isOpened():
        print(f"❌ Không mở được nguồn: {CAMERA_SOURCE}")
        return

    # Thiết lập độ phân giải và FPS mặc định để hệ thống chạy mượt
    cap.set(cv.CAP_PROP_FRAME_WIDTH, FRAME_W)
    cap.set(cv.CAP_PROP_FRAME_HEIGHT, FRAME_H)
    cap.set(cv.CAP_PROP_FPS, 30)

    target_delay = 1.0 / 30
    frame_count = 0
    
    # Bộ đệm lưu kết quả Track của frame trước (Giúp giảm tải GPU nếu không chạy YOLO mọi frame)
    last_boxes = []
    last_ids = []
    last_confs = []

    # ── FPS Counter ────────────────────────────────────────────────────────────
    # Cách tính chuẩn: đếm số frame đọc được trong 1 giây, sau đó chia ra FPS
    fps_counter    = 0           # Số frame đã đọc từ lần reset cuối
    fps_display    = 0.0         # Giá trị FPS sẽ hiển thị lên màn hình
    fps_time_start = time.time() # Mốc thời gian bắt đầu đếm

    while True:
        t0 = time.time()

        # =====================================================================
        # BƯỚC 1: CAMERA STREAM (Đọc chuỗi Frame từ Nguồn)
        # =====================================================================
        ok, frame = cap.read()
        if not ok:
            if isinstance(CAMERA_SOURCE, str):  # Hết video thì tua lại từ đầu
                cap.set(cv.CAP_PROP_POS_FRAMES, 0)
            else:                               # Mất kết nối camera thì chờ tín hiệu
                time.sleep(0.05)
            continue

        # Đếm frame: mỗi lần đọc được 1 frame hợp lệ thì +1
        fps_counter += 1
        frame_count += 1
        now = time.time()
        anyone_inside = False

        # =====================================================================
        # BƯỚC 2: TIỀN XỬ LÝ (Preprocess Frame)
        # =====================================================================
        # Tạo bản sao (disp_frame) để vẽ (draw UI) lên đó, bảo toàn ảnh gốc `frame` cho model.
        # Sinh viên lưu ý: Có thể thêm resize, tăng sáng, crop vùng quan tâm (ROI) tại đây.
        disp_frame = frame.copy()

        # =====================================================================
        # BƯỚC 3 & 4: DETECT (Phát hiện) & TRACKING (Theo dõi) VỚI YOLO
        # =====================================================================
        # Chạy chuẩn: Dùng model.track() định danh ID (Persist=True)
        # Việc chạy nhảy cóc (frame % 2) là trick tối ưu tăng nhẹ tốc độ cho máy tính yếu
        if frame_count % 2 == 1 or len(last_boxes) == 0:
            with model_lock:
                # Phân lớp (classes): Chỉ lọc class Người (Person)
                results = model.track(frame, persist=True, verbose=False, classes=PERSON_CLS, conf=0.4)
            
            # Lưu lại trạng thái Bounding box, Track ID của đối tượng hiện tại
            if results[0].boxes is not None and results[0].boxes.id is not None:
                last_boxes = results[0].boxes.xyxy.cpu().numpy()
                last_ids = results[0].boxes.id.int().cpu().tolist()
                last_confs = results[0].boxes.conf.cpu().numpy()
            else:
                last_boxes, last_ids, last_confs = [], [], []

        active_ids = last_ids if last_ids else []

        # Tọa độ vùng cấm lưu trong hệ thống được vẽ trên UI tỷ lệ chuẩn (Scaled Zone)
        sh, sw = frame.shape[:2]
        sx, sy = sw / FRAME_W, sh / FRAME_H
        scaled_zone = np.array(
            [[int(p[0] * sx), int(p[1] * sy)] for p in router.zone_points],
            dtype=np.int32,
        )

        # Frontend đã vẽ zone polygon qua JS overlay → backend KHÔNG vẽ lại để tránh duplicate
        has_zone = len(scaled_zone) >= 3

        # =====================================================================
        # BƯỚC 5: DUYỆT QUA TỪNG ĐỐI TƯỢNG ĐƯỢC NHẬN DIỆN
        # =====================================================================
        if len(active_ids) > 0:
            for box, tid, conf in zip(last_boxes, last_ids, last_confs):
                x1, y1, x2, y2 = map(int, box)

                # --- Tính TÂM của Bounding Box ---
                # Đây là điểm đại diện cho vị trí của đối tượng trên màn hình.
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2

                # --- Kiểm tra xem TÂM có nằm trong vùng cấm không ---
                # Thuật toán: Point-in-Polygon Test (OpenCV)
                # Kết quả >= 0: Điểm nằm TRONG hoặc TRÊN cạnh của đa giác → xâm nhập!
                is_inside = False
                if has_zone:
                    is_inside = cv.pointPolygonTest(scaled_zone, (float(cx), float(cy)), False) >= 0

                # --- Đổi màu sắc theo trạng thái ---
                # Đỏ  (0, 0, 255) = Đang xâm nhập vùng cấm
                # Xanh (0, 255, 0) = An toàn, ngoài vùng cấm
                color = (0, 0, 255) if is_inside else (0, 255, 0)

                # --- Vẽ lên màn hình ---
                cv.rectangle(disp_frame, (x1, y1), (x2, y2), color, 2)           # Khung bao
                cv.circle(disp_frame, (cx, cy), 5, color, -1)                     # Chấm tâm
                cv.putText(disp_frame, f"ID:{tid}", (x1, max(20, y1 - 8)),        # Nhãn ID
                           cv.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)

                # =============================================================
                # BƯỚC 6: XÁC NHẬN QUA NHIỀU FRAME (Multiframe Validation)
                # =============================================================
                if is_inside:
                    anyone_inside = True
                    # Đang ở trong vùng, đặt số frame vắng mặt về 0 (Trạng thái Lành mạnh)
                    state["zone_missed"][tid] = 0 

                    # Lưu Timestamp khi lần đầu đặt chân vào vùng
                    if tid not in state["zone_enter_times"]:
                        state["zone_enter_times"][tid] = now

                    # Tính số giây đã đứng trong vùng
                    time_in_zone = now - state["zone_enter_times"][tid]
                    
                    hold_secs = state["zone_hold_secs"]
                    cooldown_secs = state["zone_cooldown"]
                    last_tid_alert = state["zone_last_alert"].get(tid, 0)

                    # =========================================================
                    # BƯỚC 7 & BƯỚC 8: CẢNH BÁO SỰ KIỆN & CHỐNG BÁO TRÙNG
                    # =========================================================
                    # Điều kiện sinh event cảnh báo: 
                    # 1. Đứng liên tục vượt qua thời gian quy định (hold_secs -> Chống báo động lệch do run/nháy frame)
                    # 2. Xa lệnh quá thời gian cooldown (cooldown_secs -> Bước 8 chống báo trùng thông báo)
                    if (time_in_zone >= hold_secs) and (now - last_tid_alert >= cooldown_secs):
                        
                        state["zone_last_alert"][tid] = now # Đánh dấu đã báo động xong

                        # Ghi bằng chứng ảnh hiện trường
                        # (Ghi chú: disp_frame đã có sẵn khung zone màu vàng và bbox đỏ)
                        alert_img = disp_frame.copy()

                        # Lưu DB hoặc hệ điều hành
                        img_filename = f"zone_{tid}_{int(now)}.jpg"
                        img_path = os.path.join(OUTPUT_DIR, img_filename)
                        cv.imwrite(img_path, alert_img)

                        time_str = time.strftime("[%H:%M:%S]")
                        print(f"{time_str} | BÁO ĐỘNG | Xâm nhập ID={tid} | Thời gian trong vùng={time_in_zone:.1f}s")
                        
                        # Điều hướng Dispatch Cảnh báo qua Telegram + Frontend
                        dispatch_alert(img_path, tid, is_intrusion=True)
                else:
                    # TẠM MẤT HOẶC RA KHỎI VÙNG
                    # Tăng số lượng Frame vắng mặt (Missed frames)
                    state["zone_missed"][tid] = state["zone_missed"].get(tid, 0) + 1
                    # Nếu đi ngang bị mất tracking 15-30 frames thì mới reset logic vùng bị huỷ 
                    if state["zone_missed"][tid] > ZONE_MISS_TOLERANCE:
                        state["zone_enter_times"].pop(tid, None)
                        state["zone_alerted"].discard(tid)

        # XÓA RÁC (Garbage Collection): Remove hoàn toàn đối tượng đi khỏi màn hình
        for tid in list(state["zone_enter_times"]):
            if tid not in active_ids:
                state["zone_enter_times"].pop(tid, None)
                state["zone_alerted"].discard(tid)
                state["zone_missed"].pop(tid, None)
                state["zone_last_alert"].pop(tid, None)

        # =====================================================================
        # BƯỚC 9: GỬI LẠI TRẠNG THÁI HIỂN THỊ (Frontend Display & Status)
        # =====================================================================
        _, buf = cv.imencode(".jpg", disp_frame, [cv.IMWRITE_JPEG_QUALITY, 85])
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"

        # Stabilize FPS
        elapsed = time.time() - t0

        # ── Cập nhật FPS mỗi 1 giây ──────────────────────────────────────────
        # Công thức: FPS = (số frame đọc được) / (thời gian trôi qua)
        elapsed_fps = time.time() - fps_time_start
        if elapsed_fps >= 1.0:
            fps_display    = fps_counter / elapsed_fps  # Tính FPS thực tế
            fps_counter    = 0                          # Reset bộ đếm
            fps_time_start = time.time()               # Reset mốc thời gian

        # Vẽ FPS lên góc trên trái frame
        cv.putText(disp_frame, f"FPS: {fps_display:.0f}",
                   (8, 24), cv.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

        # Cập nhật SSE State lên web dashboard (PHẢI sau khi tính FPS mới có giá trị đúng)
        dispatch_status(len(active_ids), anyone_inside, fps_display)

        time.sleep(max(0.0, target_delay - elapsed))


# ── Entry point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
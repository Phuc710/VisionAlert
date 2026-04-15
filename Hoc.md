
# Phân tích các giai đoạn xử lý

Hệ thống giám sát an ninh được xây dựng theo quy trình xử lý tuần tự gồm 6 giai đoạn. Mỗi giai đoạn đảm nhận một nhiệm vụ cụ thể, đảm bảo hệ thống hoạt động chính xác, ổn định và phản hồi theo thời gian thực.

---

## Giai đoạn 1 – Thu nhận dữ liệu (Input)

Camera hoặc video là nguồn dữ liệu đầu vào của hệ thống. Từng khung hình (frame) được đọc liên tục theo thời gian thực thông qua OpenCV. Hệ thống xử lý hai trường hợp nguồn đầu vào:
- **Webcam thực tế**: đọc trực tiếp từ thiết bị phần cứng.
- **File video**: được dùng để kiểm thử, tự động tua lại khi hết.

Nếu mất kết nối hoặc không đọc được frame, hệ thống không dừng mà chờ và thử lại ở frame tiếp theo, đảm bảo giám sát liên tục không bị gián đoạn.

**Code trọng tâm – `main.py`:**
```python
# Mở nguồn video: webcam thực tế hoặc file video giả lập
if isinstance(CAMERA_SOURCE, str):
    cap = cv.VideoCapture(CAMERA_SOURCE)               # Nguồn: file video
else:
    cap = cv.VideoCapture(CAMERA_SOURCE, cv.CAP_DSHOW) # Nguồn: webcam

# Vòng lặp chính: đọc liên tục từng frame
ok, frame = cap.read()
if not ok:
    if isinstance(CAMERA_SOURCE, str):  # Hết video → tua lại từ đầu
        cap.set(cv.CAP_PROP_POS_FRAMES, 0)
    else:                               # Mất tín hiệu → chờ và thử lại
        time.sleep(0.05)
    continue
```

---

## Giai đoạn 2 – Tiền xử lý (Preprocessing)

Trước khi đưa frame vào mô hình AI, hệ thống thực hiện bước tiền xử lý nhằm chuẩn hóa dữ liệu đầu vào và tối ưu hiệu năng xử lý.

YOLOv8 yêu cầu ảnh đầu vào phải có kích thước chuẩn (thường là 640×640). Khi gọi `model.track(frame, ...)`, thư viện Ultralytics sẽ tự động thực hiện pipeline chuẩn hóa nội bộ bao gồm:
- **Resize / Scale**: đưa frame về kích thước phù hợp với mô hình.
- **Normalize**: chuẩn hóa giá trị pixel từ [0–255] về [0.0–1.0].
- **Padding**: giữ tỉ lệ khung hình, bổ sung vùng đệm nếu cần.

Ngoài ra, để giảm tải tính toán trên máy tính cấu hình thấp, hệ thống áp dụng kỹ thuật **frame skipping**: chỉ chạy mô hình AI trên các frame lẻ (frame % 2 == 1). Các frame chẵn tái sử dụng kết quả của frame trước đó, không cần chạy lại model.

Khung hình cũng được **sao chép** (`frame.copy()`) trước khi vẽ, giúp bảo toàn dữ liệu gốc nguyên vẹn cho mô hình và chỉ vẽ annotation lên bản sao hiển thị.

**Code trọng tâm – `main.py`:**
```python
# Tạo bản sao để vẽ UI, giữ nguyên `frame` gốc cho model
disp_frame = frame.copy()

# Frame skipping: chỉ chạy model AI trên frame lẻ
# → Frame chẵn tái dùng kết quả cũ, tiết kiệm CPU/GPU
if frame_count % 2 == 1 or len(last_boxes) == 0:
    with model_lock:  # Thread-safe: tránh race condition khi đa luồng
        # Ultralytics tự động resize + normalize frame trước khi đưa vào model
        results = model.track(
            frame,
            persist=True,       # Giữ track_id ổn định qua nhiều frame
            verbose=False,
            classes=PERSON_CLS, # Chỉ nhận diện class "person"
            conf=0.4            # Bỏ qua các phát hiện có độ tin cậy < 40%
        )
```

---

## Giai đoạn 3 – Phát hiện và theo dõi đối tượng (Detection & Tracking)

Hệ thống sử dụng mô hình **YOLOv8** để phát hiện người trong từng frame. Kết quả trả về gồm:
- **Bounding Box** (`xyxy`): tọa độ hình chữ nhật bao quanh đối tượng dưới dạng `[x1, y1, x2, y2]`.
- **Confidence**: độ tin cậy của phát hiện, chỉ giữ lại các kết quả ≥ 40%.
- **Track ID**: mã định danh duy nhất cho từng đối tượng, được duy trì ổn định qua nhiều frame nhờ cơ chế `persist=True`.

Việc sử dụng Tracking (thay vì chỉ Detect) mang lại hai lợi ích quan trọng:
- **Tránh đếm lặp**: cùng một người chỉ có một Track ID duy nhất, không bị tính nhiều lần.
- **Đo thời gian chính xác**: biết được đối tượng đã đứng trong vùng cấm bao nhiêu giây dựa trên ID đó.

**Code trọng tâm – `main.py`:**
```python
# Trích xuất kết quả phát hiện + tracking từ mô hình
if results[0].boxes is not None and results[0].boxes.id is not None:
    last_boxes = results[0].boxes.xyxy.cpu().numpy()       # Tọa độ [x1, y1, x2, y2]
    last_ids   = results[0].boxes.id.int().cpu().tolist()  # Track ID duy nhất mỗi người
    last_confs = results[0].boxes.conf.cpu().numpy()       # Độ tin cậy
else:
    # Không phát hiện ai → xóa trắng kết quả cũ
    last_boxes, last_ids, last_confs = [], [], []
```

---

## Giai đoạn 4 – Phân tích vùng cấm (ROI Processing)

Hệ thống kiểm tra xem đối tượng có đang đứng trong **vùng cấm (Region of Interest – ROI)** hay không. Vùng cấm do người dùng tự vẽ trên giao diện web dưới dạng đa giác (polygon) và được lưu vào file `zone.json`.

Do tọa độ vùng cấm được lưu theo độ phân giải chuẩn (640×480), hệ thống cần **quy đổi (scale)** tọa độ này sang kích thước thực tế của frame từ camera trước khi kiểm tra.

Điểm đại diện cho vị trí của đối tượng là **tâm (center) của Bounding Box**, được tính bằng công thức:
```
cx = (x1 + x2) / 2
cy = (y1 + y2) / 2
```

Sau đó, hệ thống sử dụng thuật toán **Point-in-Polygon Test** (`cv.pointPolygonTest`) để kiểm tra điểm tâm có nằm bên trong đa giác hay không:
- Kết quả **≥ 0**: điểm nằm TRONG hoặc TRÊN cạnh của đa giác → **Xâm nhập**.
- Kết quả **< 0**: điểm nằm NGOÀI đa giác → **An toàn**.

**Code trọng tâm – `main.py`:**
```python
# Bước 1: Scale tọa độ vùng cấm về đúng kích thước frame thực
sh, sw = frame.shape[:2]
sx, sy = sw / FRAME_W, sh / FRAME_H
scaled_zone = np.array(
    [[int(p[0] * sx), int(p[1] * sy)] for p in router.zone_points],
    dtype=np.int32,
)

# Bước 2: Tính tâm Bounding Box
cx = (x1 + x2) // 2
cy = (y1 + y2) // 2

# Bước 3: Kiểm tra tâm có nằm trong polygon vùng cấm không
is_inside = False
if has_zone:
    is_inside = cv.pointPolygonTest(
        scaled_zone,
        (float(cx), float(cy)),
        False    # False = chỉ trả về dấu (+/-), không tính khoảng cách
    ) >= 0
```

---

## Giai đoạn 5 – Xử lý logic xâm nhập (Decision Making)

Hệ thống không đưa ra cảnh báo ngay lập tức mà áp dụng **hai điều kiện thời gian** để tăng độ chính xác và tránh cảnh báo sai:

1. **`zone_hold_secs`** (mặc định 3 giây): Đối tượng phải đứng trong vùng cấm **liên tục** đủ thời gian này thì mới tính là xâm nhập. Điều này loại bỏ các trường hợp đi ngang qua nhanh hoặc nhiễu tạm thời.
2. **`zone_cooldown`** (mặc định 10 giây): Sau mỗi lần gửi cảnh báo, hệ thống chờ đủ thời gian cooldown mới gửi lại. Mỗi Track ID có bộ đếm cooldown **độc lập** – người A cảnh báo không ảnh hưởng đến người B.

```
Đối tượng vào zone (is_inside = True)
    │
    ├── [Lần đầu] → lưu zone_enter_times[tid] = now
    │
    ├── time_in_zone = now - zone_enter_times[tid]
    │
    ├── time_in_zone >= hold_secs  (ví dụ: 3 giây)?
    │       VÀ (now - last_alert[tid]) >= cooldown?  (ví dụ: 10 giây)
    │           │
    │           └─► GỬI CẢNH BÁO → zone_last_alert[tid] = now
    │
    └── Chưa đủ điều kiện → bỏ qua, frame tiếp theo tiếp tục kiểm tra
```

**Code trọng tâm – `main.py`:**
```python
if is_inside:
    # Ghi nhận thời điểm lần đầu bước vào vùng cấm
    if tid not in state["zone_enter_times"]:
        state["zone_enter_times"][tid] = now

    time_in_zone  = now - state["zone_enter_times"][tid]
    hold_secs     = state["zone_hold_secs"]
    cooldown_secs = state["zone_cooldown"]
    last_tid_alert = state["zone_last_alert"].get(tid, 0)

    # Điều kiện kép: đủ thời gian lưu trú VÀ hết cooldown
    if (time_in_zone >= hold_secs) and (now - last_tid_alert >= cooldown_secs):
        state["zone_last_alert"][tid] = now   # Đặt lại mốc cooldown

        # Lưu ảnh bằng chứng và gửi cảnh báo
        img_path = os.path.join(OUTPUT_DIR, f"zone_{tid}_{int(now)}.jpg")
        cv.imwrite(img_path, disp_frame.copy())
        dispatch_alert(img_path, tid, is_intrusion=True)
```

---

## Giai đoạn 6 – Gửi cảnh báo (Output & Alert)

Khi xác định có hành vi xâm nhập, hệ thống đồng thời thực hiện ba hành động:
- **Chụp lại hình ảnh** hiện trường tại thời điểm phát hiện và lưu vào thư mục `alerts/`.
- **Gửi cảnh báo Telegram** kèm ảnh và thông tin chi tiết đến người quản lý.
- **Cập nhật giao diện web** theo thời gian thực thông qua Server-Sent Events (SSE).

Quá trình gửi Telegram được xử lý trong một **luồng (thread) riêng**, đảm bảo hệ thống không bị treo trong khi chờ phản hồi từ mạng.

**Code trọng tâm – `telegram_utils.py`:**
```python
def send_formatted_intrusion_alert(photo_path, token, chat_id, track_id, is_intrusion, hold_secs):
    timestamp = time.strftime("%H:%M:%S %d/%m/%Y")
    title     = "🚨 CẢNH BÁO XÂM NHẬP 🚨" if is_intrusion else "🚨 PHÁT HIỆN NGƯỜI 🚨"
    caption   = (
        f"{title}\n"
        f"{'─' * 30}\n"
        f"👤 Đối tượng: #{track_id}\n"
        f"⏱ Thời gian: {timestamp}"
    )
    # Gửi ảnh qua Telegram Bot API
    # → POST https://api.telegram.org/bot{token}/sendPhoto
    return send_alert_photo(photo_path, token, chat_id, caption)
```

**Code trọng tâm – `main.py` (gửi không đồng bộ):**
```python
def dispatch_alert(img_path: str, track_id: int, is_intrusion: bool = False):
    """Gửi cảnh báo trong thread riêng, không làm chậm luồng video chính."""
    def _run():
        telegram_utils.send_formatted_intrusion_alert(...)   # Gửi Telegram
        loop.create_task(_broadcast(payload))                # Cập nhật SSE → Web UI

    threading.Thread(target=_run, daemon=True).start()       # Chạy tách biệt
```

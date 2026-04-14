"""
telegram_utils.py – Tiện ích gửi thông báo qua Telegram Bot API.
"""

import os
import time

import requests


def send_alert_photo(photo_path: str, token: str, chat_id: str, caption: str) -> bool:
    """
    Gửi ảnh cảnh báo kèm chú thích qua Telegram.

    Args:
        photo_path: Đường dẫn tới file ảnh cần gửi.
        token:      Telegram Bot Token.
        chat_id:    ID chat nhận thông báo.
        caption:    Chú thích đính kèm ảnh.

    Returns:
        True nếu gửi thành công, False nếu thất bại.
    """
    if not token or not chat_id or not os.path.exists(photo_path):
        return False

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    try:
        with open(photo_path, "rb") as f:
            res = requests.post(
                url,
                files={"photo": f},
                data={"chat_id": chat_id, "caption": caption},
                timeout=15,
            )
        res.raise_for_status()
        return True
    except Exception as e:
        print(f"❌ Telegram Error (sendPhoto): {e}")
        return False


def send_telegram_text(token: str, chat_id: str, text: str) -> bool:
    """
    Gửi tin nhắn văn bản qua Telegram.

    Args:
        token:   Telegram Bot Token.
        chat_id: ID chat nhận thông báo.
        text:    Nội dung tin nhắn.

    Returns:
        True nếu gửi thành công, False nếu thất bại.
    """
    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        res = requests.post(
            url,
            data={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        res.raise_for_status()
        return True
    except Exception as e:
        print(f"❌ Telegram Error (sendMessage): {e}")
        return False


def send_formatted_intrusion_alert(
    photo_path: str,
    token: str,
    chat_id: str,
    track_id: int,
    is_intrusion: bool,
    hold_secs: float = 3.0,
) -> bool:
    """
    Gửi ảnh cảnh báo xâm nhập với caption được định dạng sẵn.

    Args:
        photo_path:   Đường dẫn ảnh chụp sự kiện.
        token:        Telegram Bot Token.
        chat_id:      ID chat nhận thông báo.
        track_id:     ID định danh đối tượng (từ YOLO).
        is_intrusion: True nếu là xâm nhập vùng cấm, False nếu chỉ phát hiện người.
        hold_secs:    Thời gian đối tượng đã đứng trong vùng cấm.

    Returns:
        True nếu gửi thành công.
    """
    timestamp = time.strftime("%H:%M:%S %d/%m/%Y")
    title     = "🚨 CẢNH BÁO XÂM NHẬP 🚨" if is_intrusion else "🚨 PHÁT HIỆN NGƯỜI 🚨"
    caption   = (
        f"{title}\n"
        f"{'─' * 30}\n"
        f"👤 Đối tượng: #{track_id}\n"
        f"⏱ Thời gian: {timestamp}"
    )
    return send_alert_photo(photo_path, token, chat_id, caption)
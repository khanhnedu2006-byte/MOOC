import logging
import time

import client
from config import settings
from process_data import normalize

logger = logging.getLogger(__name__)

# {email chữ thường: {tên khóa đã chuẩn hóa, ...}}
_index: dict[str, set[str]] = {}
_loaded_at: float | None = None


def _email_key(email) -> str:
    return str(email or "").strip().lower()


def _course_key(course_name) -> str:
    return normalize(course_name)


def is_due() -> bool:
    """Tới lúc nạp lại chưa? Chưa nạp lần nào thì luôn là 'rồi'."""
    if _loaded_at is None:
        return True
    minutes = max(1, settings.history_refresh_minutes)
    return (time.time() - _loaded_at) >= minutes * 60


def refresh(force: bool = False) -> bool:
    """Nạp lại chỉ mục từ eLIS. Trả về True nếu vừa nạp xong.

    Chỉ mục CŨ không nguy hiểm: nó chỉ làm hệ thống bỏ sót ca trùng, tức xử lý
    y như trước khi có luật này. Vì vậy nạp lại thưa (mặc định mỗi giờ) là đủ,
    và lỗi khi nạp thì giữ nguyên bản cũ chứ không xóa đi.
    """
    global _index, _loaded_at

    if not settings.duplicate_check:
        return False
    if not (force or is_due()):
        return False

    try:
        rows = client.get_all_by_status("APPROVED")
    except Exception as e:
        logger.error("Không nạp được lịch sử đã duyệt: %s — luật chống nộp "
                     "trùng tạm nghỉ, chứng chỉ vẫn được xử lý bình thường.", e)
        _loaded_at = time.time()      # đừng thử lại mỗi vòng poll khi eLIS lỗi
        return False

    index: dict[str, set[str]] = {}
    skipped = 0
    for row in rows:
        email = _email_key(row.get("employeeEmail"))
        course = _course_key(row.get("courseName"))
        if not email or not course:
            skipped += 1
            continue
        index.setdefault(email, set()).add(course)

    _index = index
    _loaded_at = time.time()
    logger.info("Nạp lịch sử đã duyệt: %d bản ghi -> %d nhân viên%s.",
                len(rows), len(index),
                f" (bỏ {skipped} bản ghi thiếu email hoặc tên khóa)" if skipped else "")
    return True


def already_completed(email, course_name) -> bool:
    """Nhân viên này đã được duyệt khóa này chưa?

    Thiếu email hoặc tên khóa -> False. Không đoán: từ chối một chứng chỉ dựa
    trên dữ liệu khuyết là kiểu sai đắt nhất, còn bỏ sót chỉ khiến nó đi tiếp
    theo luồng thường.
    """
    email_key, course_key = _email_key(email), _course_key(course_name)
    if not email_key or not course_key:
        return False
    return course_key in _index.get(email_key, ())


def remember(email, course_name) -> None:
    """Ghi thêm một khóa vừa được duyệt vào chỉ mục.

    Bịt khe hở giữa hai lần nạp: nhân viên nộp cùng một khóa hai lần trong
    cùng một giờ thì cái thứ hai vẫn bị bắt, không phải đợi lần nạp sau.
    """
    email_key, course_key = _email_key(email), _course_key(course_name)
    if email_key and course_key:
        _index.setdefault(email_key, set()).add(course_key)


def stats() -> tuple[int, int]:
    """(số nhân viên, tổng số khóa) — để in ra log lúc khởi động."""
    return len(_index), sum(len(v) for v in _index.values())


def reset() -> None:
    """Xóa sạch chỉ mục. Chỉ dùng trong test."""
    global _index, _loaded_at
    _index, _loaded_at = {}, None

"""Lịch gửi báo cáo tự động (scheduler).

Chạy trong CÙNG tiến trình với vòng lặp xử lý của run.py: mỗi vòng lặp gọi
`kiem_tra_va_gui()` một lần. Không dùng luồng riêng, không dùng thư viện lịch.

VÌ SAO KHÔNG DÙNG cron / APScheduler / luồng nền:
  - cron trong container Docker cần thêm một tiến trình nữa và một lớp cấu
    hình nữa; container nên chạy MỘT tiến trình.
  - Luồng nền chia sẻ kết nối SQLite với vòng chính -> phải lo khóa.
  - Vòng lặp chính vốn đã chạy mỗi vài giây rồi. Chỉ cần hỏi "còn nợ kỳ nào
    không" ở mỗi vòng là đủ chính xác tới từng phút.

GỬI BÙ:
Mỗi vòng, scheduler tính MỐC GỬI GẦN NHẤT ĐÃ QUA rồi so với mốc đã gửi —
chứ không hỏi "bây giờ có đúng giờ gửi không". Nhờ vậy job tắt đúng lúc tới
hạn (máy sập, container restart) thì lần bật lại vẫn gửi bù. Chỉ gửi kỳ gần
nhất, không gửi dồn mọi kỳ đã bỏ lỡ.

LẦN ĐẦU BẬT sẽ gửi ngay một báo cáo cho kỳ vừa kết thúc, vì chưa có mốc nào
được ghi nhận. Đây là CHỦ ĐÍCH: bạn biết ngay cấu hình SMTP có chạy không,
thay vì đợi hết một tuần mới phát hiện sai mật khẩu.

CHỐNG GỬI TRÙNG:
Mốc đã gửi được ghi xuống FILE, không giữ trong biến. Giữ trong biến thì
container restart lúc 18:05 sẽ gửi lại báo cáo vừa gửi lúc 18:00 — và với
`restart: unless-stopped` thì một job crash-loop sẽ spam mentor hàng chục
thư. File tồn tại qua restart nên mốc đã gửi vẫn được nhớ.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import settings  # noqa: E402

logger = logging.getLogger("scheduler")

# Nơi ghi mốc đã gửi. Đặt ở gốc dự án để docker-compose gắn ra ngoài được.
STATE_FILE = Path(__file__).resolve().parent / ".report_state.json"

VALID_MODES = ("off", "daily", "weekly", "monthly")

# Giãn cách giữa các lần thử lại khi GỬI HỎNG, theo số lần đã hỏng liên tiếp.
#
# VÌ SAO CẦN: vòng lặp chính chạy mỗi POLL_INTERVAL_SECONDS (mặc định 5 giây).
# Không có giãn cách thì một mật khẩu sai sẽ thành 12 lần đăng nhập Gmail mỗi
# phút, liên tục cho tới khi ai đó để ý — Gmail sẽ khóa tài khoản vì nghi
# brute-force, và log ngập traceback tới mức che hết thông tin thật.
#
# Giãn dần chứ không cố định: lỗi tạm thời (mạng chập) được thử lại sớm, còn
# lỗi cấu hình (sai mật khẩu) tự lùi về mỗi giờ một lần.
RETRY_BACKOFF_MINUTES = (1, 5, 15, 30, 60)

# Mốc gom số liệu mặc định theo từng chế độ. Báo cáo tháng mà vẽ theo ngày
# thì biểu đồ có 30 cột chen chúc; theo tuần thì đọc được.
DEFAULT_BUCKET = {"daily": "day", "weekly": "day", "monthly": "week"}

# Mốc gom KHÔNG được thô hơn kỳ báo cáo: gom 7 ngày theo "week" cho ra
# đúng một cột, biểu đồ thành vô nghĩa. Bảng này chặn cấu hình như vậy.
MIN_DAYS_PER_BUCKET = {"day": 2, "week": 14, "month": 62}


def _read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    except OSError as e:
        # Không ghi được thì vẫn tiếp tục, nhưng CẢNH BÁO to: mất file này
        # nghĩa là mất cơ chế chống gửi trùng.
        logger.error("Không ghi được %s (%s) — có nguy cơ gửi trùng báo cáo!",
                     STATE_FILE.name, e)


def _parse_hour_minute(text: str) -> tuple[int, int]:
    """Đọc 'HH:MM'. Sai định dạng thì lùi về 18:00 và cảnh báo, không ném lỗi.

    Cấu hình sai giờ không đáng để làm chết job xác minh chứng chỉ.
    """
    try:
        g, p = text.strip().split(":")
        g, p = int(g), int(p)
        if 0 <= g <= 23 and 0 <= p <= 59:
            return g, p
    except (ValueError, AttributeError):
        pass
    logger.warning("REPORT_TIME=%r không hợp lệ, dùng 18:00.", text)
    return 18, 0


def _last_due_moment(mode: str, now: datetime) -> datetime:
    """Mốc gửi GẦN NHẤT đã trôi qua, tính tới thời điểm bay_gio.

    Đây là chỗ quyết định hành vi GỬI BÙ. Cách cũ hỏi "bây giờ có đúng thứ
    Sáu 18:00 không" — nghĩa là job phải đang chạy đúng phút đó. Máy tắt hôm
    thứ Sáu, hoặc container restart ngay lúc ấy, là báo cáo tuần đó mất luôn
    và không ai được báo.

    Cách mới hỏi "mốc gửi gần nhất đã qua là lúc nào" rồi đối chiếu với mốc
    đã gửi. Job bật lại lúc nào cũng phát hiện được là còn nợ báo cáo.
    """
    hour, minute = _parse_hour_minute(settings.report_time)

    if mode == "daily":
        moment = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return moment if moment <= now else moment - timedelta(days=1)

    if mode == "weekly":
        thu = max(0, min(6, settings.report_weekday))
        lech = (now.weekday() - thu) % 7
        moment = (now - timedelta(days=lech)).replace(
            hour=hour, minute=minute, second=0, microsecond=0)
        return moment if moment <= now else moment - timedelta(days=7)

    # monthly. Chặn ở 28 để tháng nào cũng có ngày đó — đặt 31 thì tháng Hai
    # không bao giờ tới hạn và báo cáo lặng lẽ không bao giờ được gửi.
    day = max(1, min(28, settings.report_monthday))
    moment = now.replace(day=day, hour=hour, minute=minute,
                          second=0, microsecond=0)
    if moment > now:
        cuoi_thang_truoc = now.replace(day=1) - timedelta(days=1)
        moment = cuoi_thang_truoc.replace(day=day, hour=hour, minute=minute,
                                       second=0, microsecond=0)
    return moment


def _report_period(mode: str, moment: datetime) -> tuple[str, str, str]:
    """(from_day, to_day, khoa_ky) cho kỳ ứng với mốc gửi `moc`.

    khoa_ky định danh KỲ BÁO CÁO, không phải ngày gửi. Khác biệt đó chính là
    thứ làm cho gửi bù chạy đúng: gửi bù vào thứ Bảy vẫn mang khóa của tuần
    ấy, nên không bị tính thành một kỳ mới, và tuần sau vẫn gửi bình thường.

    Luôn báo cáo kỳ ĐÃ TRỌN VẸN:
      - daily   : 7 ngày kết thúc ở ngày của mốc, GỬI MỖI NGÀY
      - weekly  : 7 ngày kết thúc ở ngày của mốc
      - monthly : trọn THÁNG TRƯỚC tháng của mốc
    Báo cáo tháng cho tháng đang chạy dở luôn thấp hơn thực tế và làm người
    đọc tưởng khối lượng đang giảm.

    VÌ SAO "daily" LÀ 7 NGÀY CHỨ KHÔNG PHẢI 1 NGÀY: một ngày chỉ cho ra MỘT
    mốc, nên biểu đồ đường chỉ có một điểm — không vẽ được xu hướng gì, và
    con số một ngày cũng không cho biết nó cao hay thấp so với bình thường.
    Cửa sổ trượt 7 ngày thì mỗi bản báo cáo vẫn "mới mỗi ngày" mà luôn có đủ
    ngữ cảnh. Khóa vẫn theo NGÀY nên vẫn đúng một thư mỗi ngày.
    """
    state = moment.date()

    if mode == "daily":
        return ((state - timedelta(days=6)).isoformat(), state.isoformat(),
                f"daily:{state.isoformat()}")

    if mode == "weekly":
        start = state - timedelta(days=6)
        year, tuan, _ = state.isocalendar()      # khóa theo TUẦN ISO
        return start.isoformat(), state.isoformat(), f"weekly:{year}-W{tuan:02d}"

    dau_thang = state.replace(day=1)
    cuoi_thang_truoc = dau_thang - timedelta(days=1)
    dau_thang_truoc = cuoi_thang_truoc.replace(day=1)
    return (dau_thang_truoc.isoformat(), cuoi_thang_truoc.isoformat(),
            f"monthly:{cuoi_thang_truoc.strftime('%Y-%m')}")


def _sensible_bucket(bucket: str, start: str, end: str, mode: str) -> str:
    """Hạ mốc gom xuống nếu nó thô hơn kỳ báo cáo.

    REPORT_BUCKET=week với lịch daily (kỳ 7 ngày) cho ra đúng một cột. Thay
    vì im lặng vẽ một biểu đồ vô nghĩa, hạ về mốc mịn hơn và ghi log để người
    cấu hình biết mình đặt sai.
    """
    day_count = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    order = ("day", "week", "month")
    if bucket not in order:
        logger.warning("REPORT_BUCKET=%r không hợp lệ, dùng 'day'.", bucket)
        return "day"

    i = order.index(bucket)
    while i > 0 and day_count < MIN_DAYS_PER_BUCKET[order[i]]:
        i -= 1
    if order[i] != bucket:
        logger.warning(
            "REPORT_BUCKET=%r quá thô cho kỳ %d ngày (lịch %s) — biểu đồ sẽ "
            "chỉ có một cột. Dùng %r thay thế.",
            bucket, day_count, mode, order[i])
    return order[i]


def _may_retry(state: dict, key: str, now: datetime) -> bool:
    """Đã đủ giãn cách để thử gửi lại kỳ này chưa."""
    failure = state.get("last_failure")
    if not failure or failure.get("key") != key:
        return True                       # chưa hỏng lần nào cho kỳ này

    attempts = failure.get("attempts", 1)
    wait = RETRY_BACKOFF_MINUTES[min(attempts - 1, len(RETRY_BACKOFF_MINUTES) - 1)]
    try:
        lan_cuoi = datetime.fromisoformat(failure["at"])
    except (KeyError, ValueError):
        return True
    return now - lan_cuoi >= timedelta(minutes=wait)


def _record_failure(state: dict, key: str, now: datetime, error) -> None:
    """Ghi nhận một lần gửi hỏng, và tính lần chờ tiếp theo."""
    failure = state.get("last_failure") or {}
    attempts = (failure.get("attempts", 0) + 1) if failure.get("key") == key else 1
    wait = RETRY_BACKOFF_MINUTES[min(attempts - 1, len(RETRY_BACKOFF_MINUTES) - 1)]
    state["last_failure"] = {
        "key": key,
        "attempts": attempts,
        "at": now.isoformat(timespec="seconds"),
    }
    _write_state(state)

    # Lần đầu in đầy đủ traceback để còn chẩn đoán; các lần sau chỉ một dòng,
    # nếu không log sẽ ngập và che mất log xử lý chứng chỉ.
    if attempts == 1:
        logger.exception("Gửi báo cáo %s lỗi: %s. Thử lại sau %d phút.",
                         key, error, wait)
    else:
        logger.error("Gửi báo cáo %s vẫn lỗi (lần %d): %s. Thử lại sau %d phút.",
                     key, attempts, error, wait)


def check_and_send(now: datetime | None = None, really_send: bool = True) -> str | None:
    """Gọi mỗi vòng lặp. Gửi báo cáo nếu tới hạn; trả về khóa kỳ đã gửi.

    Trả None nghĩa là chưa tới hạn hoặc đã gửi rồi — trường hợp thường gặp
    nhất, và phải RẺ, vì hàm này chạy mỗi vài giây.
    """
    mode = (settings.report_schedule or "off").strip().lower()
    if mode == "off":
        return None
    if mode not in VALID_MODES:
        logger.warning("REPORT_SCHEDULE=%r không hợp lệ (%s). Bỏ qua.",
                       mode, "/".join(VALID_MODES))
        return None

    now = now or datetime.now()
    # CHỈ xét kỳ gần nhất còn nợ, không gửi bù toàn bộ các kỳ đã bỏ lỡ. Job
    # tắt một tháng rồi bật lại thì mentor nhận MỘT báo cáo, không phải bốn.
    start, end, key = _report_period(mode, _last_due_moment(mode, now))
    state = _read_state()
    if state.get("da_gui") == key:
        return None                   # kỳ này gửi rồi

    if not _may_retry(state, key, now):
        return None

    bucket = (settings.report_bucket or "").strip() \
        or DEFAULT_BUCKET.get(mode, "day")
    bucket = _sensible_bucket(bucket, start, end, mode)

    logger.info("Tới hạn báo cáo %s: %s → %s (mốc %s)", mode, start, end, bucket)
    if really_send:
        try:
            import send_report
            send_report.send_period_report(start, end, bucket)
        except Exception as e:
            # KHÔNG ghi nhận đã gửi khi gửi hỏng -> sẽ thử lại, nhưng có
            # giãn cách (xem _duoc_thu_lai). Cũng KHÔNG ném lỗi ra ngoài:
            # báo cáo hỏng không được phép làm dừng việc xác minh chứng chỉ.
            _record_failure(state, key, now, e)
            return None

    state["da_gui"] = key
    state["thoi_diem"] = now.isoformat(timespec="seconds")
    state.pop("last_failure", None)      # gửi được rồi thì xóa lịch sử hỏng
    _write_state(state)
    logger.info("Đã gửi báo cáo kỳ %s.", key)
    return key

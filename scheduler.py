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
FILE_TRANG_THAI = Path(__file__).resolve().parent / ".report_state.json"

CHE_DO_HOP_LE = ("off", "daily", "weekly", "monthly")

# Giãn cách giữa các lần thử lại khi GỬI HỎNG, theo số lần đã hỏng liên tiếp.
#
# VÌ SAO CẦN: vòng lặp chính chạy mỗi POLL_INTERVAL_SECONDS (mặc định 5 giây).
# Không có giãn cách thì một mật khẩu sai sẽ thành 12 lần đăng nhập Gmail mỗi
# phút, liên tục cho tới khi ai đó để ý — Gmail sẽ khóa tài khoản vì nghi
# brute-force, và log ngập traceback tới mức che hết thông tin thật.
#
# Giãn dần chứ không cố định: lỗi tạm thời (mạng chập) được thử lại sớm, còn
# lỗi cấu hình (sai mật khẩu) tự lùi về mỗi giờ một lần.
GIAN_CACH_THU_LAI_PHUT = (1, 5, 15, 30, 60)

# Mốc gom số liệu mặc định theo từng chế độ. Báo cáo tháng mà vẽ theo ngày
# thì biểu đồ có 30 cột chen chúc; theo tuần thì đọc được.
BUCKET_MAC_DINH = {"daily": "day", "weekly": "day", "monthly": "week"}

# Mốc gom KHÔNG được thô hơn kỳ báo cáo: gom 7 ngày theo "week" cho ra
# đúng một cột, biểu đồ thành vô nghĩa. Bảng này chặn cấu hình như vậy.
SO_NGAY_TOI_THIEU = {"day": 2, "week": 14, "month": 62}


def _doc_trang_thai() -> dict:
    try:
        return json.loads(FILE_TRANG_THAI.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _ghi_trang_thai(d: dict) -> None:
    try:
        FILE_TRANG_THAI.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    except OSError as e:
        # Không ghi được thì vẫn tiếp tục, nhưng CẢNH BÁO to: mất file này
        # nghĩa là mất cơ chế chống gửi trùng.
        logger.error("Không ghi được %s (%s) — có nguy cơ gửi trùng báo cáo!",
                     FILE_TRANG_THAI.name, e)


def _gio_phut(chuoi: str) -> tuple[int, int]:
    """Đọc 'HH:MM'. Sai định dạng thì lùi về 18:00 và cảnh báo, không ném lỗi.

    Cấu hình sai giờ không đáng để làm chết job xác minh chứng chỉ.
    """
    try:
        g, p = chuoi.strip().split(":")
        g, p = int(g), int(p)
        if 0 <= g <= 23 and 0 <= p <= 59:
            return g, p
    except (ValueError, AttributeError):
        pass
    logger.warning("REPORT_TIME=%r không hợp lệ, dùng 18:00.", chuoi)
    return 18, 0


def _moc_gui_gan_nhat(che_do: str, bay_gio: datetime) -> datetime:
    """Mốc gửi GẦN NHẤT đã trôi qua, tính tới thời điểm bay_gio.

    Đây là chỗ quyết định hành vi GỬI BÙ. Cách cũ hỏi "bây giờ có đúng thứ
    Sáu 18:00 không" — nghĩa là job phải đang chạy đúng phút đó. Máy tắt hôm
    thứ Sáu, hoặc container restart ngay lúc ấy, là báo cáo tuần đó mất luôn
    và không ai được báo.

    Cách mới hỏi "mốc gửi gần nhất đã qua là lúc nào" rồi đối chiếu với mốc
    đã gửi. Job bật lại lúc nào cũng phát hiện được là còn nợ báo cáo.
    """
    gio, phut = _gio_phut(settings.report_time)

    if che_do == "daily":
        moc = bay_gio.replace(hour=gio, minute=phut, second=0, microsecond=0)
        return moc if moc <= bay_gio else moc - timedelta(days=1)

    if che_do == "weekly":
        thu = max(0, min(6, settings.report_weekday))
        lech = (bay_gio.weekday() - thu) % 7
        moc = (bay_gio - timedelta(days=lech)).replace(
            hour=gio, minute=phut, second=0, microsecond=0)
        return moc if moc <= bay_gio else moc - timedelta(days=7)

    # monthly. Chặn ở 28 để tháng nào cũng có ngày đó — đặt 31 thì tháng Hai
    # không bao giờ tới hạn và báo cáo lặng lẽ không bao giờ được gửi.
    ngay = max(1, min(28, settings.report_monthday))
    moc = bay_gio.replace(day=ngay, hour=gio, minute=phut,
                          second=0, microsecond=0)
    if moc > bay_gio:
        cuoi_thang_truoc = bay_gio.replace(day=1) - timedelta(days=1)
        moc = cuoi_thang_truoc.replace(day=ngay, hour=gio, minute=phut,
                                       second=0, microsecond=0)
    return moc


def _khoang_bao_cao(che_do: str, moc: datetime) -> tuple[str, str, str]:
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
    d = moc.date()

    if che_do == "daily":
        return ((d - timedelta(days=6)).isoformat(), d.isoformat(),
                f"daily:{d.isoformat()}")

    if che_do == "weekly":
        tu = d - timedelta(days=6)
        nam, tuan, _ = d.isocalendar()      # khóa theo TUẦN ISO
        return tu.isoformat(), d.isoformat(), f"weekly:{nam}-W{tuan:02d}"

    dau_thang = d.replace(day=1)
    cuoi_thang_truoc = dau_thang - timedelta(days=1)
    dau_thang_truoc = cuoi_thang_truoc.replace(day=1)
    return (dau_thang_truoc.isoformat(), cuoi_thang_truoc.isoformat(),
            f"monthly:{cuoi_thang_truoc.strftime('%Y-%m')}")


def _bucket_hop_ly(bucket: str, tu: str, den: str, che_do: str) -> str:
    """Hạ mốc gom xuống nếu nó thô hơn kỳ báo cáo.

    REPORT_BUCKET=week với lịch daily (kỳ 7 ngày) cho ra đúng một cột. Thay
    vì im lặng vẽ một biểu đồ vô nghĩa, hạ về mốc mịn hơn và ghi log để người
    cấu hình biết mình đặt sai.
    """
    so_ngay = (date.fromisoformat(den) - date.fromisoformat(tu)).days + 1
    thu_tu = ("day", "week", "month")
    if bucket not in thu_tu:
        logger.warning("REPORT_BUCKET=%r không hợp lệ, dùng 'day'.", bucket)
        return "day"

    i = thu_tu.index(bucket)
    while i > 0 and so_ngay < SO_NGAY_TOI_THIEU[thu_tu[i]]:
        i -= 1
    if thu_tu[i] != bucket:
        logger.warning(
            "REPORT_BUCKET=%r quá thô cho kỳ %d ngày (lịch %s) — biểu đồ sẽ "
            "chỉ có một cột. Dùng %r thay thế.",
            bucket, so_ngay, che_do, thu_tu[i])
    return thu_tu[i]


def _duoc_thu_lai(trang_thai: dict, khoa: str, bay_gio: datetime) -> bool:
    """Đã đủ giãn cách để thử gửi lại kỳ này chưa."""
    tb = trang_thai.get("that_bai")
    if not tb or tb.get("khoa") != khoa:
        return True                       # chưa hỏng lần nào cho kỳ này

    so_lan = tb.get("so_lan", 1)
    cho = GIAN_CACH_THU_LAI_PHUT[min(so_lan - 1, len(GIAN_CACH_THU_LAI_PHUT) - 1)]
    try:
        lan_cuoi = datetime.fromisoformat(tb["luc"])
    except (KeyError, ValueError):
        return True
    return bay_gio - lan_cuoi >= timedelta(minutes=cho)


def _ghi_that_bai(trang_thai: dict, khoa: str, bay_gio: datetime, loi) -> None:
    """Ghi nhận một lần gửi hỏng, và tính lần chờ tiếp theo."""
    tb = trang_thai.get("that_bai") or {}
    so_lan = (tb.get("so_lan", 0) + 1) if tb.get("khoa") == khoa else 1
    cho = GIAN_CACH_THU_LAI_PHUT[min(so_lan - 1, len(GIAN_CACH_THU_LAI_PHUT) - 1)]
    trang_thai["that_bai"] = {
        "khoa": khoa,
        "so_lan": so_lan,
        "luc": bay_gio.isoformat(timespec="seconds"),
    }
    _ghi_trang_thai(trang_thai)

    # Lần đầu in đầy đủ traceback để còn chẩn đoán; các lần sau chỉ một dòng,
    # nếu không log sẽ ngập và che mất log xử lý chứng chỉ.
    if so_lan == 1:
        logger.exception("Gửi báo cáo %s lỗi: %s. Thử lại sau %d phút.",
                         khoa, loi, cho)
    else:
        logger.error("Gửi báo cáo %s vẫn lỗi (lần %d): %s. Thử lại sau %d phút.",
                     khoa, so_lan, loi, cho)


def kiem_tra_va_gui(bay_gio: datetime | None = None, gui_that: bool = True) -> str | None:
    """Gọi mỗi vòng lặp. Gửi báo cáo nếu tới hạn; trả về khóa kỳ đã gửi.

    Trả None nghĩa là chưa tới hạn hoặc đã gửi rồi — trường hợp thường gặp
    nhất, và phải RẺ, vì hàm này chạy mỗi vài giây.
    """
    che_do = (settings.report_schedule or "off").strip().lower()
    if che_do == "off":
        return None
    if che_do not in CHE_DO_HOP_LE:
        logger.warning("REPORT_SCHEDULE=%r không hợp lệ (%s). Bỏ qua.",
                       che_do, "/".join(CHE_DO_HOP_LE))
        return None

    bay_gio = bay_gio or datetime.now()
    # CHỈ xét kỳ gần nhất còn nợ, không gửi bù toàn bộ các kỳ đã bỏ lỡ. Job
    # tắt một tháng rồi bật lại thì mentor nhận MỘT báo cáo, không phải bốn.
    tu, den, khoa = _khoang_bao_cao(che_do, _moc_gui_gan_nhat(che_do, bay_gio))
    trang_thai = _doc_trang_thai()
    if trang_thai.get("da_gui") == khoa:
        return None                   # kỳ này gửi rồi

    if not _duoc_thu_lai(trang_thai, khoa, bay_gio):
        return None

    bucket = (settings.report_bucket or "").strip() \
        or BUCKET_MAC_DINH.get(che_do, "day")
    bucket = _bucket_hop_ly(bucket, tu, den, che_do)

    logger.info("Tới hạn báo cáo %s: %s → %s (mốc %s)", che_do, tu, den, bucket)
    if gui_that:
        try:
            import send_report
            send_report.gui_bao_cao_ky(tu, den, bucket)
        except Exception as e:
            # KHÔNG ghi nhận đã gửi khi gửi hỏng -> sẽ thử lại, nhưng có
            # giãn cách (xem _duoc_thu_lai). Cũng KHÔNG ném lỗi ra ngoài:
            # báo cáo hỏng không được phép làm dừng việc xác minh chứng chỉ.
            _ghi_that_bai(trang_thai, khoa, bay_gio, e)
            return None

    trang_thai["da_gui"] = khoa
    trang_thai["thoi_diem"] = bay_gio.isoformat(timespec="seconds")
    trang_thai.pop("that_bai", None)      # gửi được rồi thì xóa lịch sử hỏng
    _ghi_trang_thai(trang_thai)
    logger.info("Đã gửi báo cáo kỳ %s.", khoa)
    return khoa

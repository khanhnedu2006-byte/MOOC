"""Module chuẩn hóa dữ liệu (process_data).

Chỉ làm sạch chuỗi để chuẩn bị so sánh; việc so nằm ở compare.py. Mọi dữ liệu
đi qua đúng hàm này — một cửa duy nhất, để không nơi nào chuẩn hóa kiểu khác.

Thứ tự đã chốt: NFC -> bỏ dấu (unidecode) -> strip -> lower -> gộp khoảng
trắng. NFC trước vì tiếng Việt có hai cách mã hóa dấu (NFC gộp "ế" thành 1 ký
tự, NFD tách thành "e" + dấu) khác byte dù trông giống hệt nhau. Bỏ dấu vì
chứng chỉ quốc tế hay in tên không dấu.
"""

import re
import unicodedata

from unidecode import unidecode


def normalize(text: str | None) -> str:
    """Chuẩn hóa một chuỗi để chuẩn bị so sánh.

    None hoặc chuỗi rỗng đều trả về chuỗi rỗng, nơi gọi không phải kiểm None.
    "  Nguyễn  Văn   A " -> "nguyen van a".
    """
    if not text:
        return ""

    # 1. NFC: gộp dấu về ký tự đơn, để cùng một chữ luôn cùng biểu diễn.
    text = unicodedata.normalize("NFC", text)

    # 2. Bỏ dấu: unidecode xử lý luôn ký tự Latin mở rộng nên an toàn với tên
    #    nước ngoài.
    text = unidecode(text)

    # 3. lower
    text = text.lower()

    # 4. Mọi ký tự KHÔNG phải chữ/số/khoảng trắng thành khoảng trắng: loại
    #    nhiễu như -, ", (), / mà chứng chỉ hay có còn người nhập bỏ qua.
    #    Chỉ bỏ dấu câu, không bỏ chữ.
    text = re.sub(r"[^a-z0-9\s]", " ", text)

    # 5. Gộp mọi cụm khoảng trắng thành một dấu cách, cắt đầu/cuối.
    text = re.sub(r"\s+", " ", text).strip()

    return text


from datetime import date, timedelta

import dateparser


_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December")

# "tháng 9" / "Thang 10" -> tên tháng tiếng Anh. dateparser không hiểu chữ
# "tháng", nên "tháng 9 2026" (Coursera tiếng Việt) và "tháng 10 8, 2026"
# (bản dịch máy của "Oct 8, 2026") đều ra None. Đổi sang tên tháng thì thứ tự
# ngày/tháng hết mơ hồ.
_VN_MONTH = re.compile(r"\bth[aá]ng\s*(\d{1,2})\b", re.IGNORECASE)
# "ngày 8 tháng 10 năm 2026": bỏ chữ "ngày", "năm" cho dateparser đọc.
_VN_FILLER = re.compile(r"\b(ng[aà]y|n[aă]m)\b", re.IGNORECASE)


def _vietnamese_to_english(date_string: str) -> str | None:
    """None khi có "tháng N" với N ngoài 1-12: để nguyên thì dateparser tự
    đoán bừa ("tháng 13 2026" -> 03/01/2026)."""
    text = unicodedata.normalize("NFC", date_string)
    if any(not 1 <= int(m.group(1)) <= 12 for m in _VN_MONTH.finditer(text)):
        return None

    def month(m):
        return f" {_MONTH_NAMES[int(m.group(1)) - 1]} "

    text = _VN_MONTH.sub(month, text)
    text = _VN_FILLER.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_with_order(date_string: str, order: str):
    """Parse ngày theo một thứ tự cụ thể (DMY hoặc MDY). Trả date hoặc None."""
    text = _vietnamese_to_english(date_string)
    if text is None:
        return None
    result = dateparser.parse(
        text,
        settings={
            "DATE_ORDER": order,
            # Bắt buộc đủ ngày+tháng+năm; thiếu phần nào -> None.
            "REQUIRE_PARTS": ["day", "month", "year"],
        },
    )
    return result.date() if result else None


def _parse_month_year(date_string: str):
    """Chuỗi CHỈ có tháng + năm ("tháng 9 2026", "Sep 2026", "09/2026").

    Trả (ngày đầu tháng, ngày cuối tháng) hoặc None. Chỉ gọi khi đã thử đọc
    đủ ngày-tháng-năm mà không được.
    """
    text = _vietnamese_to_english(date_string)
    if text is None:
        return None
    result = dateparser.parse(
        text,
        settings={"REQUIRE_PARTS": ["month", "year"],
                  "PREFER_DAY_OF_MONTH": "first"},
    )
    if not result:
        return None
    first = result.date().replace(day=1)
    next_month = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return first, next_month - timedelta(days=1)


def parse_date(date_string: str | None):
    """Chuyển chuỗi ngày thành date theo kiểu Việt Nam (ngày trước tháng).

    Chỉ để hiển thị; kiểm tra hợp lệ dùng date_in_range. Trả None nếu rỗng,
    chữ lạ, hoặc chỉ có năm.
    """
    if not date_string or not date_string.strip():
        return None
    return _parse_with_order(date_string, "DMY")


# Kết quả kiểm ngày, tách rõ để lý do từ chối nói đúng chuyện gì xảy ra:
#   DATE_OK           trong khoảng
#   DATE_MISSING      chứng chỉ không có ngày (AI trả null / chuỗi rỗng)
#   DATE_UNREADABLE   có chữ nhưng không ra được ngày đủ ngày-tháng-năm
#   DATE_OUT_OF_RANGE ra được ngày nhưng nằm ngoài [start, end]
DATE_OK = "ok"
DATE_MISSING = "missing"
DATE_UNREADABLE = "unreadable"
DATE_OUT_OF_RANGE = "out_of_range"


def date_status(date_string: str | None, start: str, end: str) -> str:
    """Như date_in_range() nhưng trả về VÌ SAO không hợp lệ.

    Cùng luật thử cả DMY và MDY: một cách hiểu rơi vào khoảng là DATE_OK.
    """
    if not date_string or not date_string.strip():
        return DATE_MISSING

    day_from = date.fromisoformat(start)
    day_to = date.fromisoformat(end)

    parsed = False
    for order in ("DMY", "MDY"):
        day = _parse_with_order(date_string, order)
        if day is None:
            continue
        parsed = True
        if day_from <= day <= day_to:
            return DATE_OK
    if parsed:
        return DATE_OUT_OF_RANGE

    # Chỉ có tháng + năm: hợp lệ khi CẢ THÁNG nằm trong khoảng. Tháng chạm
    # biên (vd tháng 10 khi hạn là 15/10) không biết ngày thật trước hay sau
    # hạn -> không nhận.
    month = _parse_month_year(date_string)
    if month is None:
        return DATE_UNREADABLE
    first, last = month
    return DATE_OK if day_from <= first and last <= day_to else DATE_OUT_OF_RANGE


def date_in_range(date_string: str | None, start: str, end: str) -> bool:
    """Kiểm tra ngày hoàn thành có nằm trong khoảng [start, end] không.

    Ngày dạng số như "06-12-2026" MƠ HỒ: 6 tháng 12 (kiểu Việt Nam) hay 12
    tháng 6 (kiểu Mỹ, dùng bởi chứng chỉ nước ngoài). Luật đã chốt: thử CẢ
    HAI cách hiểu (DMY và MDY), một trong hai rơi vào khoảng hợp lệ là đủ, vì
    mục tiêu là quyết định hợp lệ chứ không phải đoán đúng ngày.

    start, end: chuỗi "YYYY-MM-DD" lấy từ config.
    """
    return date_status(date_string, start, end) == DATE_OK

    day_from = date.fromisoformat(start)
    day_to = date.fromisoformat(end)

    for order in ("DMY", "MDY"):
        day = _parse_with_order(date_string, order)
        if day is not None and day_from <= day <= day_to:
            return True
    return False


def code_from_email(email: str | None) -> str:
    """Lấy mã nhân viên (username) từ email — phần đứng trước dấu @.

    ĐẶT Ở ĐÂY vì ba nơi cùng cần: run.py, evaluation/ và công cụ ghép ảnh.
    Mỗi nơi một bản sao thì chúng lệch nhau và evaluation/ sẽ chấm điểm trên
    employee_code khác với hệ thống thật.

    KHÔNG giới hạn tên miền: FPT có nhiều đuôi (fpt.com, fsoft.com.vn,
    fe.edu.vn...) mà thứ in trên chứng chỉ là USERNAME.

    Trả RỖNG nếu thiếu email hoặc thiếu dấu @ — khi đó chỉ còn đối chiếu bằng
    tên. Cố ý không trả nguyên chuỗi khi thiếu @, vì như vậy là coi một chuỗi
    rác là mã nhân viên. "  HoaBD3@FSOFT.COM.VN" -> "hoabd3".
    """
    if not email or not email.strip():
        return ""
    email = email.strip().lower()
    if "@" not in email:
        return ""
    return email.split("@", 1)[0].strip()

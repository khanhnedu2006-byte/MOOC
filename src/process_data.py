"""Module chuẩn hóa dữ liệu (process_data).

Chỉ lo MỘT việc: biến chuỗi thô thành chuỗi đã chuẩn hóa để so sánh.
Không so sánh ở đây — việc so nằm ở compare.py. Module này chỉ làm sạch.

Mọi dữ liệu (input người nhập, kết quả LLM1, kết quả LLM2) đều đi qua đúng
hàm này trước khi được đem so. Một cửa duy nhất, để không nơi nào chuẩn hóa
kiểu khác gây lệch kết quả.

Thứ tự chuẩn hóa (đã chốt):
    NFC -> bỏ dấu (unidecode) -> strip -> lower -> gộp khoảng trắng

Vì sao theo đúng thứ tự này:
    1. NFC trước tiên: tiếng Việt có hai cách mã hóa dấu khác nhau (NFC gộp
       "ế" thành 1 ký tự, NFD tách thành "e" + dấu). Hai chuỗi trông giống hệt
       nhau vẫn khác byte. Chuẩn NFC trước để unidecode xử lý nhất quán.
    2. Bỏ dấu: chứng chỉ (nhất là quốc tế) hay in tên không dấu. Bỏ dấu cả hai
       bên thì "NGUYEN VAN A" và "Nguyễn Văn A" mới khớp được.
    3. strip: bỏ khoảng trắng đầu/cuối.
    4. lower: bỏ phân biệt hoa/thường.
    5. Gộp khoảng trắng: nhiều dấu cách liên tiếp -> một dấu cách.
"""

import re
import unicodedata

from unidecode import unidecode


def normalize(text: str | None) -> str:
    """Chuẩn hóa một chuỗi để chuẩn bị so sánh.

    Nhận None hoặc chuỗi rỗng đều trả về chuỗi rỗng, để nơi gọi không phải
    tự kiểm tra None trước.

    Ví dụ:
        normalize("  Nguyễn  Văn   A ")  -> "nguyen van a"
        normalize("NGUYEN VAN A")        -> "nguyen van a"
        normalize(None)                  -> ""
    """
    if not text:
        return ""

    # 1. NFC: gộp dấu về dạng ký tự đơn, để cùng một chữ luôn cùng biểu diễn.
    text = unicodedata.normalize("NFC", text)

    # 2. Bỏ dấu: "Nguyễn" -> "Nguyen". unidecode cũng xử lý luôn các ký tự
    #    Latin mở rộng khác, nên an toàn với cả tên nước ngoài.
    text = unidecode(text)

    # 3. lower
    text = text.lower()

    # 4. Bỏ dấu câu: thay mọi ký tự KHÔNG phải chữ/số/khoảng trắng thành khoảng
    #    trắng. Loại nhiễu vô nghĩa như dấu -, ", (), / mà chứng chỉ hay có
    #    nhưng người nhập bỏ qua (vd 'ky nang "feedback"' = 'ky nang feedback',
    #    'HIỆU QUẢ - TĂNG' = 'HIỆU QUẢ -TĂNG'). Vẫn phân biệt được nội dung khác
    #    nhau vì chỉ bỏ dấu câu, không bỏ chữ.
    text = re.sub(r"[^a-z0-9\s]", " ", text)

    # 5. Gộp mọi cụm khoảng trắng thành một dấu cách, cắt đầu/cuối.
    text = re.sub(r"\s+", " ", text).strip()

    return text


from datetime import date

import dateparser


def _parse_with_order(date_string: str, order: str):
    """Parse ngày theo một thứ tự cụ thể (DMY hoặc MDY). Trả date hoặc None."""
    result = dateparser.parse(
        date_string,
        settings={
            "DATE_ORDER": order,
            # Bắt buộc đủ ngày+tháng+năm; thiếu phần nào -> None (vd chỉ có năm).
            "REQUIRE_PARTS": ["day", "month", "year"],
        },
    )
    return result.date() if result else None


def parse_date(date_string: str | None):
    """Chuyển chuỗi ngày thành date theo kiểu Việt Nam (ngày trước tháng).

    Dùng để hiển thị / tham khảo. Việc kiểm tra hợp lệ dùng date_in_range (xét
    cả hai cách hiểu). Trả None nếu rỗng, chữ lạ, hoặc chỉ có năm.
    """
    if not date_string or not date_string.strip():
        return None
    return _parse_with_order(date_string, "DMY")


def date_in_range(date_string: str | None, start: str, end: str) -> bool:
    """Kiểm tra ngày hoàn thành có nằm trong khoảng [tu, den] không.

    Ngày dạng số như "06-12-2026" MƠ HỒ: có thể là 6 tháng 12 (kiểu Việt Nam)
    hoặc 12 tháng 6 (kiểu Mỹ, dùng bởi chứng chỉ nước ngoài như MongoDB). Không
    có cách nào nhìn chuỗi mà biết chắc.

    Luật đã chốt: thử CẢ HAI cách hiểu (DMY và MDY). Chỉ cần MỘT trong hai rơi
    vào khoảng hợp lệ -> tính là hợp lệ. Vì mục tiêu là quyết định hợp lệ hay
    không, không phải đoán đúng ngày; nếu cách hiểu nào cũng cho ngày hợp lệ
    thì kết quả như nhau.

    tu, den: chuỗi "YYYY-MM-DD" (lấy từ config).
    Trả False nếu không parse được cả hai cách, hoặc cả hai đều ngoài khoảng.
    """
    if not date_string or not date_string.strip():
        return False

    day_from = date.fromisoformat(start)
    day_to = date.fromisoformat(end)

    for order in ("DMY", "MDY"):
        day = _parse_with_order(date_string, order)
        if day is not None and day_from <= day <= day_to:
            return True
    return False


def code_from_email(email: str | None) -> str:
    """Lấy mã nhân viên (username) từ email — phần đứng trước dấu @.

    ĐẶT Ở ĐÂY chứ không ở run.py vì có BA nơi cần nó: job thật (run.py), bộ
    đánh giá (evaluation/) và công cụ ghép ảnh. Trước đây mỗi nơi giữ một bản
    sao và chúng đã lệch nhau thật: bản trong evaluation/ KHÔNG hạ chữ thường
    và trả nguyên chuỗi khi thiếu dấu @. Hậu quả kín đáo: bộ đánh giá chấm
    điểm trên một employee_code khác với employee_code mà hệ thống thật dùng,
    nên mọi con số đo được đều không nói về hệ thống đang chạy.

    KHÔNG giới hạn tên miền. Lý do: FPT có nhiều đuôi khác nhau (fpt.com,
    fpt.com.vn, fsoft.com.vn, fe.edu.vn, fptsoftware.com...). Thứ được in
    trên chứng chỉ là USERNAME, không phụ thuộc tên miền — nên chặn theo
    đuôi chỉ làm mất mã đối chiếu và từ chối oan đúng những chứng chỉ in
    username, tức là đúng ca mà việc lấy mã từ email sinh ra để xử lý.

    Trả RỖNG nếu không có email hoặc chuỗi không chứa dấu @. Rỗng nghĩa là
    không đối chiếu được qua mã, chỉ còn đối chiếu bằng tên. Cố ý KHÔNG trả
    nguyên chuỗi trong ca thiếu @: làm vậy là lặng lẽ coi một chuỗi rác nào
    đó là mã nhân viên.

    Ví dụ:
        "hungnt97@fpt.com"      -> "hungnt97"
        "hoabd3@fpt.com.vn"     -> "hoabd3"
        "  HoaBD3@FSOFT.COM.VN" -> "hoabd3"
        "hoabd3"                -> ""      (thiếu @ -> không tin được)
    """
    if not email or not email.strip():
        return ""
    email = email.strip().lower()
    if "@" not in email:
        return ""
    return email.split("@", 1)[0].strip()

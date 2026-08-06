import re
import unicodedata

from unidecode import unidecode


def chuan_hoa(text: str | None) -> str:
    """Chuẩn hóa một chuỗi để chuẩn bị so sánh.

    Nhận None hoặc chuỗi rỗng đều trả về chuỗi rỗng, để nơi gọi không phải
    tự kiểm tra None trước.

    Ví dụ:
        chuan_hoa("  Nguyễn  Văn   A ")  -> "nguyen van a"
        chuan_hoa("NGUYEN VAN A")        -> "nguyen van a"
        chuan_hoa(None)                  -> ""
    """
    if not text:
        return ""

    # 1. NFC: gộp dấu về dạng ký tự đơn, để cùng một chữ luôn cùng biểu diễn.
    text = unicodedata.normalize("NFC", text)

    # 2. Bỏ dấu: "Nguyễn" -> "Nguyen". unidecode cũng xử lý luôn các ký tự
    #    Latin mở rộng khác, nên an toàn với cả tên nước ngoài.
    text = unidecode(text)

    # 3. strip + 4. lower
    text = text.strip().lower()

    # 5. Gộp mọi cụm khoảng trắng (space, tab, xuống dòng) thành một dấu cách.
    text = re.sub(r"\s+", " ", text)

    return text
"""Tiện ích xử lý file (file_utils).

Hai việc:
  1. Kiểm tra file có phải ảnh hoặc PDF hợp lệ không — dựa trên NỘI DUNG thật
     (python-magic đọc byte đầu), không tin đuôi file. Bắt được cả trường hợp
     file HEIC/WebP bị đổi đuôi thành .jpg.
  2. Chuyển file thành ảnh dạng bytes để đưa cho Gemma (LLM nhận ảnh):
     - Ảnh sẵn: đọc thẳng bytes.
     - PDF: render trang thành ảnh PNG (pypdfium2).
"""

from pathlib import Path

import magic
import pypdfium2 as pdfium

# Các MIME được chấp nhận. Azure và Gemma đều đọc được các loại này.
MIME_ANH = {"image/jpeg", "image/png", "image/bmp", "image/tiff"}
MIME_PDF = "application/pdf"

# Độ phân giải render PDF. 200 DPI đủ rõ để đọc chữ, kể cả dấu tiếng Việt.
# pypdfium2 dùng scale (1.0 = 72 DPI), nên scale = DPI / 72.
PDF_SCALE = 200 / 72


class FileKhongHopLe(Exception):
    """File không phải ảnh/PDF hợp lệ, hoặc không đọc được."""


def kiem_tra_loai(duong_dan: str | Path) -> str:
    duong_dan = Path(duong_dan)
    if not duong_dan.is_file():
        raise FileKhongHopLe(f"Không tìm thấy file: {duong_dan}")

    mime = magic.from_file(str(duong_dan), mime=True)

    if mime in MIME_ANH or mime == MIME_PDF:
        return mime

    # Chẩn đoán rõ vài loại hay bị đổi đuôi.
    if mime == "image/heic" or mime == "image/heif":
        raise FileKhongHopLe(
            f"File là HEIC (ảnh iPhone), không hỗ trợ. Chuyển sang JPEG. ({duong_dan.name})"
        )
    if mime == "image/webp":
        raise FileKhongHopLe(
            f"File là WebP, không hỗ trợ. Chuyển sang JPEG. ({duong_dan.name})"
        )
    raise FileKhongHopLe(f"Loại file không hỗ trợ: {mime} ({duong_dan.name})")


def doc_thanh_anh(duong_dan: str | Path) -> list[bytes]:
    """Đọc file thành danh sách ảnh (bytes).

    Trả về list vì PDF có thể nhiều trang. Ảnh đơn thì list có 1 phần tử.
      - Ảnh: trả bytes gốc.
      - PDF: render từng trang thành PNG bytes.
    """
    duong_dan = Path(duong_dan)
    mime = kiem_tra_loai(duong_dan)

    if mime == MIME_PDF:
        return _render_pdf(duong_dan)

    # Ảnh sẵn: đọc thẳng bytes.
    return [duong_dan.read_bytes()]


def _render_pdf(duong_dan: Path) -> list[bytes]:
    """Render mỗi trang PDF thành PNG bytes."""
    import io

    anh_list: list[bytes] = []
    pdf = pdfium.PdfDocument(str(duong_dan))
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            bitmap = page.render(scale=PDF_SCALE)
            pil_image = bitmap.to_pil()
            buf = io.BytesIO()
            pil_image.save(buf, format="PNG")
            anh_list.append(buf.getvalue())
    finally:
        pdf.close()

    if not anh_list:
        raise FileKhongHopLe(f"PDF không có trang nào: {duong_dan.name}")
    return anh_list
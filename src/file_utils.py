"""Tiện ích xử lý file (file_utils).

Hai việc:
  1. Kiểm tra file có phải ảnh hoặc PDF hợp lệ không — dựa trên NỘI DUNG thật
     (python-magic đọc byte đầu), không tin đuôi file. Bắt được cả trường hợp
     file HEIC/WebP bị đổi đuôi thành .jpg.
  2. Chuyển file thành ảnh dạng bytes để đưa cho Gemma (LLM nhận ảnh):
     - Ảnh sẵn: đọc thẳng bytes.
     - PDF: render trang thành ảnh PNG (pypdfium2).

LƯU Ý WINDOWS: python-magic cần libmagic. Nếu import lỗi 'failed to find
libmagic', cài: pip install python-magic-bin
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
    """Trả về MIME thật của file. Ném FileKhongHopLe nếu không hỗ trợ.

    Đọc nội dung thật, không tin đuôi file.
    """
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
      - Ảnh: đọc bytes gốc.
      - PDF: render từng trang thành ảnh.

    Mọi ảnh trả về đều đã qua nen_cho_vua() để không vượt giới hạn kích
    thước request của API — xem giải thích ở hàm đó.
    """
    duong_dan = Path(duong_dan)
    mime = kiem_tra_loai(duong_dan)

    if mime == MIME_PDF:
        return _render_pdf(duong_dan)

    # Ảnh sẵn: đọc thẳng bytes, nhưng vẫn phải ép vừa ngưỡng — ảnh chụp
    # từ điện thoại có thể 5-10 MB.
    return [nen_cho_vua(duong_dan.read_bytes())]


def _render_pdf(duong_dan: Path) -> list[bytes]:
    """Render mỗi trang PDF thành ảnh bytes, đã ép vừa ngưỡng."""
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
            anh_list.append(nen_cho_vua(buf.getvalue()))
    finally:
        pdf.close()

    if not anh_list:
        raise FileKhongHopLe(f"PDF không có trang nào: {duong_dan.name}")
    return anh_list


# ===== Ép ảnh vừa giới hạn kích thước request =====

# Ngưỡng cho MỘT ảnh, tính bằng byte.
#
# Vì sao 600 KB: ảnh được nhúng vào request dưới dạng base64, mà base64 làm
# phình dữ liệu thêm khoảng 33%. 600 KB ảnh -> ~800 KB trong request, cộng
# prompt vẫn nằm dưới 1 MB — giới hạn client_max_body_size mặc định của
# nginx. Vượt ngưỡng đó thì nginx CHẶN NGAY và trả về trang HTML "400 Bad
# Request", không phải lỗi JSON của API, nên rất khó đoán nguyên nhân.
#
# Đo thực tế trên một chứng chỉ Coursera: PDF render 200 DPI ra PNG 1,04 MB
# -> request 1,4 MB -> nginx chặn. Cùng ảnh đó lưu JPEG chất lượng 85 chỉ
# còn 357 KB mà KHÔNG giảm độ phân giải.
GIOI_HAN_ANH = 600_000

# Chất lượng JPEG khi phải nén. 85 gần như không ảnh hưởng việc đọc chữ in
# nhưng nhỏ hơn PNG khoảng ba lần.
CHAT_LUONG_JPEG = 85

# Các mức cạnh dài thử lần lượt khi đổi JPEG vẫn chưa đủ nhỏ.
_CAC_MUC_CANH = (2400, 2000, 1600, 1400, 1200, 1000)


def nen_cho_vua(anh_bytes: bytes, gioi_han: int = GIOI_HAN_ANH) -> bytes:
    """Ép ảnh xuống dưới ngưỡng byte, giữ độ nét nhiều nhất có thể.

    Thứ tự ưu tiên — hy sinh thứ ít ảnh hưởng tới việc đọc chữ trước:
      1. Đã đủ nhỏ  -> giữ NGUYÊN, không nén lại vô ích.
      2. Đổi sang JPEG, GIỮ NGUYÊN độ phân giải. Thường là đủ, và không mất
         chi tiết chữ.
      3. Thu nhỏ dần cạnh dài. Chỉ tới bước này khi ảnh thật sự lớn.
      4. Hạ chất lượng JPEG. Để cuối vì nó làm nhòe chữ nhiều nhất.

    KHÔNG ném lỗi nếu vẫn không đạt: trả về bản nhỏ nhất làm được. Thà gửi
    ảnh hơi to rồi nhận lỗi rõ ràng từ API, còn hơn chặn ngay tại đây và làm
    chứng chỉ thất bại vì một lý do người vận hành không nhìn thấy.
    """
    if len(anh_bytes) <= gioi_han:
        return anh_bytes

    import io
    from PIL import Image

    try:
        anh = Image.open(io.BytesIO(anh_bytes))
        anh.load()
    except Exception:
        # Không mở được thì trả nguyên trạng, để tầng trên báo lỗi tử tế.
        return anh_bytes

    # JPEG không có kênh trong suốt; ghép nền trắng để không ra ảnh đen.
    if anh.mode in ("RGBA", "LA", "P"):
        anh = anh.convert("RGBA")
        nen = Image.new("RGB", anh.size, (255, 255, 255))
        nen.paste(anh, mask=anh.split()[-1])
        anh = nen
    else:
        anh = anh.convert("RGB")

    nho_nhat = _luu_jpeg(anh, CHAT_LUONG_JPEG)
    if len(nho_nhat) <= gioi_han:
        return nho_nhat

    canh_goc = max(anh.size)
    for canh in _CAC_MUC_CANH:
        if canh >= canh_goc:
            continue
        thu = _luu_jpeg(_thu_nho(anh, canh / canh_goc), CHAT_LUONG_JPEG)
        if len(thu) < len(nho_nhat):
            nho_nhat = thu
        if len(thu) <= gioi_han:
            return thu

    # Vẫn quá lớn: hạ chất lượng ở mức nhỏ nhất đã thử.
    anh_nho = _thu_nho(anh, min(1.0, _CAC_MUC_CANH[-1] / canh_goc))
    for chat_luong in (70, 55, 40):
        thu = _luu_jpeg(anh_nho, chat_luong)
        if len(thu) < len(nho_nhat):
            nho_nhat = thu
        if len(thu) <= gioi_han:
            return thu

    return nho_nhat


def _thu_nho(anh, ty_le: float):
    from PIL import Image
    if ty_le >= 1:
        return anh
    return anh.resize((max(1, int(anh.width * ty_le)),
                       max(1, int(anh.height * ty_le))), Image.LANCZOS)


def _luu_jpeg(anh, chat_luong: int) -> bytes:
    import io
    buf = io.BytesIO()
    anh.save(buf, format="JPEG", quality=chat_luong, optimize=True)
    return buf.getvalue()
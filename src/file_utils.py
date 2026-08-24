"""Tiện ích xử lý file (file_utils).

Hai việc:
  1. Kiểm tra file có phải ảnh hoặc PDF hợp lệ không — dựa trên NỘI CORRECT thật
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
MIME_IMAGE = {"image/jpeg", "image/png", "image/bmp", "image/tiff"}
MIME_PDF = "application/pdf"

# Độ phân giải render PDF. 200 DPI đủ rõ để đọc chữ, kể cả dấu tiếng Việt.
# pypdfium2 dùng scale (1.0 = 72 DPI), nên scale = DPI / 72.
PDF_SCALE = 200 / 72


class InvalidFileError(Exception):
    """File không phải ảnh/PDF hợp lệ, hoặc không đọc được."""


def check_mime(path: str | Path) -> str:
    """Trả về MIME thật của file. Ném InvalidFileError nếu không hỗ trợ.

    Đọc nội dung thật, không tin đuôi file.
    """
    path = Path(path)
    if not path.is_file():
        raise InvalidFileError(f"Không tìm thấy file: {path}")

    mime = magic.from_file(str(path), mime=True)

    if mime in MIME_IMAGE or mime == MIME_PDF:
        return mime

    # Chẩn đoán rõ vài loại hay bị đổi đuôi.
    if mime == "image/heic" or mime == "image/heif":
        raise InvalidFileError(
            f"File là HEIC (ảnh iPhone), không hỗ trợ. Chuyển sang JPEG. ({path.name})"
        )
    if mime == "image/webp":
        raise InvalidFileError(
            f"File là WebP, không hỗ trợ. Chuyển sang JPEG. ({path.name})"
        )
    raise InvalidFileError(f"Loại file không hỗ trợ: {mime} ({path.name})")


def read_as_images(path: str | Path) -> list[bytes]:
    """Đọc file thành danh sách ảnh (bytes).

    Trả về list vì PDF có thể nhiều trang. Ảnh đơn thì list có 1 phần tử.
      - Ảnh: đọc bytes gốc.
      - PDF: render từng trang thành ảnh.

    Mọi ảnh trả về đều đã qua compress_to_fit() để không vượt giới hạn kích
    thước request của API — xem giải thích ở hàm đó.
    """
    path = Path(path)
    mime = check_mime(path)

    if mime == MIME_PDF:
        return _render_pdf(path)

    # Ảnh sẵn: đọc thẳng bytes, nhưng vẫn phải ép vừa ngưỡng — ảnh chụp
    # từ điện thoại có thể 5-10 MB.
    return [compress_to_fit(path.read_bytes())]


def _render_pdf(path: Path) -> list[bytes]:
    """Render mỗi trang PDF thành ảnh bytes, đã ép vừa ngưỡng."""
    import io

    images: list[bytes] = []
    pdf = pdfium.PdfDocument(str(path))
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            bitmap = page.render(scale=PDF_SCALE)
            pil_image = bitmap.to_pil()
            buf = io.BytesIO()
            pil_image.save(buf, format="PNG")
            images.append(compress_to_fit(buf.getvalue()))
    finally:
        pdf.close()

    if not images:
        raise InvalidFileError(f"PDF không có trang nào: {path.name}")
    return images


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
IMAGE_SIZE_LIMIT = 600_000

# Chất lượng JPEG khi phải nén. 85 gần như không ảnh hưởng việc đọc chữ in
# nhưng nhỏ hơn PNG khoảng ba lần.
JPEG_QUALITY = 85

# Các mức cạnh dài thử lần lượt khi đổi JPEG vẫn chưa đủ nhỏ.
_EDGE_STEPS = (2400, 2000, 1600, 1400, 1200, 1000)


def compress_to_fit(image_bytes: bytes, limit: int = IMAGE_SIZE_LIMIT) -> bytes:
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
    if len(image_bytes) <= limit:
        return image_bytes

    import io
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except Exception:
        # Không mở được thì trả nguyên trạng, để tầng trên báo lỗi tử tế.
        return image_bytes

    # JPEG không có kênh trong suốt; ghép nền trắng để không ra ảnh đen.
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[-1])
        image = background
    else:
        image = image.convert("RGB")

    smallest = _save_jpeg(image, JPEG_QUALITY)
    if len(smallest) <= limit:
        return smallest

    original_edge = max(image.size)
    for edge in _EDGE_STEPS:
        if edge >= original_edge:
            continue
        candidate = _save_jpeg(_resize(image, edge / original_edge), JPEG_QUALITY)
        if len(candidate) < len(smallest):
            smallest = candidate
        if len(candidate) <= limit:
            return candidate

    # Vẫn quá lớn: hạ chất lượng ở mức nhỏ nhất đã thử.
    small_image = _resize(image, min(1.0, _EDGE_STEPS[-1] / original_edge))
    for quality in (70, 55, 40):
        candidate = _save_jpeg(small_image, quality)
        if len(candidate) < len(smallest):
            smallest = candidate
        if len(candidate) <= limit:
            return candidate

    return smallest


def _resize(image, ratio: float):
    from PIL import Image
    if ratio >= 1:
        return image
    return image.resize((max(1, int(image.width * ratio)),
                       max(1, int(image.height * ratio))), Image.LANCZOS)


def _save_jpeg(image, quality: int) -> bytes:
    import io
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()
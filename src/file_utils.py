"""Tiện ích xử lý file (file_utils).

  1. Kiểm tra file có phải ảnh hoặc PDF hợp lệ không, dựa trên NỘI DUNG thật
     (python-magic đọc byte đầu), không tin đuôi file — bắt được cả file
     HEIC/WebP bị đổi đuôi thành .jpg.
  2. Chuyển file thành ảnh bytes cho Gemma; PDF render ra PNG bằng pypdfium2.

LƯU Ý WINDOWS: python-magic cần libmagic. Nếu import lỗi 'failed to find
libmagic', cài: pip install python-magic-bin
"""

from pathlib import Path

import magic
import pypdfium2 as pdfium

# MIME chấp nhận: Azure và Gemma đều đọc được.
MIME_IMAGE = {"image/jpeg", "image/png", "image/bmp", "image/tiff"}
MIME_PDF = "application/pdf"

# 200 DPI đủ rõ để đọc chữ, kể cả dấu tiếng Việt. pypdfium2 dùng scale
# (1.0 = 72 DPI) nên scale = DPI / 72.
PDF_SCALE = 200 / 72


class InvalidFileError(Exception):
    """File không phải ảnh/PDF hợp lệ, hoặc không đọc được."""


# libmagic chỉ cần vài trăm byte đầu (magic number); 8 KB là dư, mà không
# phải đọc cả file cho mỗi chứng chỉ.
_MAGIC_BYTES = 8192


def check_mime(path: str | Path) -> str:
    """Trả về MIME thật của file. Ném InvalidFileError nếu không hỗ trợ.

    DÙNG from_buffer CHỨ KHÔNG from_file: trên Windows libmagic nhận đường dẫn
    dưới dạng byte theo bảng mã hệ thống (CP1258/CP1252), nên MỌI file có dấu
    tiếng Việt trong tên đều hỏng với thông báo khó lần:

        'utf-8' codec can't decode bytes in position 74-75: invalid continuation byte

    Đọc byte bằng Python rồi mới đưa cho libmagic thì libmagic không bao giờ
    nhìn thấy tên file.
    """
    path = Path(path)
    if not path.is_file():
        raise InvalidFileError(f"Không tìm thấy file: {path}")

    try:
        with path.open("rb") as f:
            dau_file = f.read(_MAGIC_BYTES)
    except OSError as e:
        raise InvalidFileError(f"Không đọc được file: {path.name} ({e})") from e

    if not dau_file:
        raise InvalidFileError(f"File rỗng: {path.name}")

    mime = magic.from_buffer(dau_file, mime=True)

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
    """Đọc file thành danh sách ảnh (bytes); PDF nhiều trang -> nhiều phần tử.

    Mọi ảnh trả về đều đã qua compress_to_fit() để không vượt giới hạn kích
    thước request của API.
    """
    path = Path(path)
    mime = check_mime(path)

    if mime == MIME_PDF:
        return _render_pdf(path)

    # Ảnh sẵn vẫn phải ép vừa ngưỡng: ảnh chụp từ điện thoại có thể 5-10 MB.
    return [compress_to_fit(path.read_bytes())]


def _render_pdf(path: Path) -> list[bytes]:
    """Render mỗi trang PDF thành ảnh bytes, đã ép vừa ngưỡng.

    Truyền BYTE chứ không truyền đường dẫn, cùng lý do với check_mime: thư
    viện C phía dưới nhận tên file theo bảng mã hệ thống nên tên có dấu tiếng
    Việt hỏng lặng lẽ.
    """
    import io

    images: list[bytes] = []
    pdf = pdfium.PdfDocument(path.read_bytes())
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

# Ngưỡng cho MỘT ảnh, tính bằng byte. Ảnh nhúng vào request dưới dạng base64,
# phình thêm ~33%: 600 KB ảnh -> ~800 KB, cộng prompt vẫn dưới 1 MB — giới hạn
# client_max_body_size mặc định của nginx. Vượt thì nginx CHẶN và trả trang
# HTML "400 Bad Request", không phải lỗi JSON của API, rất khó đoán.
IMAGE_SIZE_LIMIT = 600_000

# 85 gần như không ảnh hưởng việc đọc chữ in, mà nhỏ hơn PNG khoảng ba lần.
JPEG_QUALITY = 85

# Các mức cạnh dài thử lần lượt khi đổi JPEG vẫn chưa đủ nhỏ.
_EDGE_STEPS = (2400, 2000, 1600, 1400, 1200, 1000)


def compress_to_fit(image_bytes: bytes, limit: int = IMAGE_SIZE_LIMIT) -> bytes:
    """Ép ảnh xuống dưới ngưỡng byte, giữ độ nét nhiều nhất có thể.

    Hy sinh thứ ít ảnh hưởng tới việc đọc chữ trước: đủ nhỏ thì giữ nguyên,
    rồi đổi JPEG giữ nguyên độ phân giải, rồi thu nhỏ cạnh dài, cuối cùng mới
    hạ chất lượng JPEG vì nó làm nhòe chữ nhiều nhất. KHÔNG ném lỗi nếu vẫn
    không đạt, mà trả bản nhỏ nhất làm được.
    """
    if len(image_bytes) <= limit:
        return image_bytes

    import io
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except Exception:
        # Không mở được thì trả nguyên trạng để tầng trên báo lỗi.
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

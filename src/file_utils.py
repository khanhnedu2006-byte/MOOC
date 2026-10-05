"""Tiện ích xử lý file (file_utils).

  1. Kiểm tra file có phải ảnh hoặc PDF hợp lệ không, dựa trên NỘI DUNG thật
     (đọc byte đầu), không tin đuôi file — bắt được cả file HEIC/WebP bị đổi
     đuôi thành .jpg.
  2. Chuyển file thành ảnh bytes cho Gemma; PDF render ra PNG bằng pypdfium2.
"""

from pathlib import Path

import puremagic
import pypdfium2 as pdfium

# MIME chấp nhận: Azure và Gemma đều đọc được.
MIME_IMAGE = {"image/jpeg", "image/png", "image/bmp", "image/tiff"}
MIME_PDF = "application/pdf"

# 200 DPI đủ rõ để đọc chữ, kể cả dấu tiếng Việt. pypdfium2 dùng scale
# (1.0 = 72 DPI) nên scale = DPI / 72.
PDF_SCALE = 200 / 72


class InvalidFileError(Exception):
    """File không phải ảnh/PDF hợp lệ, hoặc không đọc được."""


# Chỉ cần vài trăm byte đầu (magic number); 8 KB là dư, mà không phải đọc cả
# file cho mỗi chứng chỉ.
_MAGIC_BYTES = 8192

# puremagic gọi tên vài loại khác libmagic. Quy về tên trong MIME_IMAGE, nếu
# không BMP hợp lệ bị từ chối oan.
_DONG_NGHIA = {
    "image/x-ms-bmp": "image/bmp",
    "image/x-bmp": "image/bmp",
}


def _doan_mime(dau_file: bytes) -> str:
    """MIME đoán từ byte đầu. Trả "" khi không nhận ra loại nào.

    puremagic ném PureError thay vì trả "text/plain" như libmagic; nuốt ở đây
    để tầng trên vẫn báo "loại file không hỗ trợ" như cũ.
    """
    try:
        mime = puremagic.from_string(dau_file, mime=True)
    except Exception:
        return ""
    return _DONG_NGHIA.get(mime, mime or "")


def unsupported_mime(data: bytes) -> str | None:
    """MIME thật của file nếu hệ thống KHÔNG đọc được loại này, None nếu đọc được.

    Cùng luật với check_mime(), nhưng nhận bytes để client.py gắn cờ ngay khi
    tải về từ API ②. None cũng có nghĩa "chưa kết luận được" (bytes rỗng) —
    để check_mime() báo lỗi như cũ. puremagic không nhận ra loại nào thì trả
    "application/octet-stream": vẫn là "không đọc được".
    """
    if not data:
        return None
    mime = _doan_mime(data[:_MAGIC_BYTES])
    if mime in MIME_IMAGE or mime == MIME_PDF:
        return None
    return mime or "application/octet-stream"


def check_mime(path: str | Path) -> str:
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

    mime = _doan_mime(dau_file)

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
    raise InvalidFileError(
        f"Loại file không hỗ trợ: {mime or 'không nhận ra'} ({path.name})")


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

    # pypdfium2 ném lỗi của RIÊNG nó (PdfiumError), không phải InvalidFileError.
    # Không đổi sang mẫu này thì lỗi đi xuyên qua scan_certificate và giết cả
    # vòng TRƯỚC KHI kịp ghi log — mà không có dòng log thì không đếm, không
    # cooldown, nên nó lặp lại mỗi 5 giây và khóa cả hàng đợi, im lặng.
    #
    # Ca thật: PDF có mật khẩu, hoặc tải về dở dang. check_mime vẫn cho qua vì
    # phần đầu file đúng là PDF.
    try:
        pdf = pdfium.PdfDocument(path.read_bytes())
    except Exception as e:
        raise InvalidFileError(f"Không mở được PDF: {e}") from e

    try:
        for i in range(len(pdf)):
            page = pdf[i]
            bitmap = page.render(scale=PDF_SCALE)
            pil_image = bitmap.to_pil()
            buf = io.BytesIO()
            pil_image.save(buf, format="PNG")
            images.append(compress_to_fit(buf.getvalue()))
    except Exception as e:
        # Mở được nhưng vỡ ở giữa: hỏng từ trang thứ n trở đi.
        raise InvalidFileError(f"Không đọc được trang PDF: {e}") from e
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

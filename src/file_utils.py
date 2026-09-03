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


# Số byte đầu file đưa cho libmagic đoán loại. libmagic chỉ cần vài trăm byte
# đầu (magic number); 8 KB là dư cho mọi định dạng ở đây. Đọc cả file 2 MB
# chỉ để đoán loại là phí, nhất là khi hàm này chạy cho từng chứng chỉ.
_MAGIC_BYTES = 8192


def check_mime(path: str | Path) -> str:
    """Trả về MIME thật của file. Ném InvalidFileError nếu không hỗ trợ.

    Đọc nội dung thật, không tin đuôi file.

    DÙNG from_buffer CHỨ KHÔNG from_file. Đây là một lỗi đã xảy ra thật:
    trên Windows, libmagic nhận đường dẫn dưới dạng byte theo bảng mã hệ
    thống (CP1258/CP1252), nên MỌI file có dấu tiếng Việt trong tên đều hỏng,
    với một trong hai thông báo chẳng nói lên điều gì:

        'utf-8' codec can't decode bytes in position 74-75: invalid continuation byte
        Loại file không hỗ trợ: cannot open `...\\Mở Khoá AI_cẩm nang...`

    Đo trên bộ dữ liệu thật 133 chứng chỉ: 84 file có dấu -> hỏng 84/84;
    49 file tên thuần ASCII -> chạy 49/49. Tách sạch, không một ngoại lệ.

    Job chạy thật KHÔNG lộ ra lỗi này vì nó ghi byte tải từ eLIS ra file tạm
    có tên ASCII do tempfile sinh. Chỉ khi đọc thẳng file do người dùng đặt
    tên — như bộ đánh giá làm — mới lòi ra. Đó là lý do một lỗi chặn 63% dữ
    liệu vẫn nằm im được lâu như vậy.

    Đọc byte bằng Python rồi mới đưa cho libmagic thì đường dẫn Unicode do
    Python xử lý (nó làm đúng), còn libmagic không bao giờ nhìn thấy tên file.
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
    """Render mỗi trang PDF thành ảnh bytes, đã ép vừa ngưỡng.

    Truyền BYTE chứ không truyền đường dẫn, cùng lý do với check_mime: thư
    viện C phía dưới nhận tên file theo bảng mã hệ thống, nên tên có dấu
    tiếng Việt là một nguồn hỏng lặng lẽ. Python đọc file rồi đưa byte sang
    thì cả tầng đó biến mất. Chứng chỉ ở đây tối đa vài MB nên nạp vào RAM
    không thành vấn đề.
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

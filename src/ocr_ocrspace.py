"""OCR bằng OCR.space (ocr_ocrspace).
"""

import logging
import time

import requests

import llm_error
from config import settings

logger = logging.getLogger(__name__)

URL = "https://api.ocr.space/parse/image"

# Trần của bậc miễn phí. file_utils ép ảnh xuống dưới 600 KB nên bình thường
# còn dư, nhưng trang PDF render ở 200 DPI có thể vượt.
SIZE_LIMIT_BYTES = 1024 * 1024

# Mã lỗi TẠM THỜI — thử lại thì có cơ may thành công. Cố ý giống
# ocr_azure.TRANSIENT_CODES: cùng loại vấn đề thì hai nhà cung cấp phải cư xử
# như nhau.
TRANSIENT_CODES = frozenset({408, 429, 500, 502, 503, 504})

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (2, 5)

# Engine 1 KHÔNG đọc được tiếng Việt — gọi vào trả lỗi E201. Chặn từ đây thay
# vì để người vận hành tự đoán khi cả hàng đợi đứng.
ENGINE_WITHOUT_VIETNAMESE = 1


class OcrError(Exception):
    """Lỗi khi gọi OCR.space, đã diễn giải sang tiếng Việt."""


def create_client() -> requests.Session:
    """OCR.space gọi bằng HTTP thuần, không có client bền như Azure.

    Trả một Session để tái dùng kết nối TCP — mỗi chứng chỉ nhiều trang là
    nhiều lần gọi liên tiếp tới cùng một máy chủ.
    """
    if not settings.ocrspace_api_key:
        raise OcrError(
            f"{llm_error.TAG_NEEDS_HUMAN}: Chưa có OCRSPACE_API_KEY. Lấy key "
            f"miễn phí tại https://ocr.space/ocrapi rồi lưu vào kho khóa "
            f"(tab Cấu hình) hoặc .env.")
    return requests.Session()


def ocr_bytes(session: requests.Session, image_bytes: bytes) -> str:
    """OCR một ảnh (bytes), trả về text thô.

    TỰ THỬ LẠI với lỗi tạm thời; lỗi vĩnh viễn (sai key, hết hạn mức, ảnh quá
    cỡ) ném ngay vì kết quả không đổi.
    """
    if len(image_bytes) > SIZE_LIMIT_BYTES:
        raise OcrError(
            f"Ảnh {len(image_bytes) / 1024:.0f} KB vượt trần 1 MB của "
            f"OCR.space. file_utils.compress_to_fit đáng lẽ đã ép xuống dưới "
            f"600 KB — kiểm lại IMAGE_SIZE_LIMIT.")

    engine = int(settings.ocrspace_engine)
    if engine == ENGINE_WITHOUT_VIETNAMESE:
        raise OcrError(
            f"{llm_error.TAG_NEEDS_HUMAN}: OCRSPACE_ENGINE=1 không hỗ trợ "
            f"tiếng Việt. Đổi sang 2 (khai ngôn ngữ) hoặc 3 (tự nhận diện).")

    data = {
        "apikey": settings.ocrspace_api_key,
        "OCREngine": str(engine),
        "isOverlayRequired": "false",
        "detectOrientation": "true",
        "scale": "true",
    }
    # OCR.space chỉ nhận MỘT ngôn ngữ mỗi lần gọi — không có "vnm+eng" như
    # Tesseract. Engine 3 tự nhận diện nên không khai.
    if engine != 3:
        data["language"] = settings.ocrspace_language

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        started = time.monotonic()
        try:
            r = session.post(URL, data=data,
                             files={_upload_name(image_bytes): image_bytes},
                             timeout=settings.timeout_seconds)
        except requests.RequestException as e:
            last_error = OcrError(f"Lỗi kết nối OCR.space: {e}")
            if attempt == MAX_ATTEMPTS:
                raise last_error from e
            _sleep_before_retry(attempt, str(e))
            continue

        seconds = time.monotonic() - started

        if r.status_code != 200:
            # Ghi KÍCH THƯỚC ảnh và THỜI GIAN chờ, cùng lý do với ocr_azure:
            # hai con số này phân biệt lỗi phía mình với lỗi phía dịch vụ.
            logger.warning("OCR.space HTTP %d sau %.1fs (ảnh %.0f KB, lần "
                           "%d/%d)", r.status_code, seconds,
                           len(image_bytes) / 1024, attempt, MAX_ATTEMPTS)
            last_error = OcrError(_explain_http_error(r, attempt))
            if r.status_code not in TRANSIENT_CODES or attempt == MAX_ATTEMPTS:
                raise last_error
            _sleep_before_retry(attempt, f"HTTP {r.status_code}")
            continue

        try:
            body = r.json()
        except ValueError:
            raise OcrError(
                f"OCR.space trả về không phải JSON. Content-Type="
                f"{r.headers.get('Content-Type')!r}, bắt đầu bằng "
                f"{r.text[:80]!r}") from None

        if body.get("IsErroredOnProcessing"):
            raise OcrError(_explain_body_error(body, seconds))

        text = "\n".join((k.get("ParsedText") or "")
                         for k in (body.get("ParsedResults") or []))
        if not text.strip():
            # Trả 200 nhưng rỗng: ảnh không có chữ. Thử lại vô ích.
            raise OcrError(
                f"OCR.space không đọc được chữ nào (ảnh có thể mờ hoặc "
                f"trống). [ảnh {len(image_bytes) / 1024:.0f} KB, {seconds:.1f}s]")

        logger.debug("OCR.space OK sau %.1fs (ảnh %.0f KB, %d ký tự).",
                     seconds, len(image_bytes) / 1024, len(text))
        return text

    raise last_error or OcrError("OCR.space thất bại không rõ lý do.")


def ocr_images(session: requests.Session, images: list[bytes]) -> str:
    """OCR nhiều ảnh (PDF nhiều trang), nối text lại.

    Một trang lỗi thì bỏ qua trang đó; chỉ ném lỗi khi KHÔNG trang nào đọc
    được, và khi đó thông báo mang theo lý do thật của từng trang — hết hạn
    mức, sai key và ảnh mờ nếu không nói rõ thì hiện ra y hệt nhau.
    """
    parts = []
    failures: list[str] = []
    for i, image in enumerate(images, 1):
        try:
            parts.append(ocr_bytes(session, image))
        except OcrError as e:
            failures.append(f"trang {i}: {e}")

    if not parts:
        detail = " | ".join(failures) if failures else "không có trang nào"
        raise OcrError(f"Không trang nào đọc được chữ ({detail}).")
    return "\n\n".join(parts)


# Chữ ký đầu file -> đuôi tên. OCR.space đoán loại file theo TÊN gửi lên, nên
# gắn sai đuôi là nó từ chối dù byte hoàn toàn hợp lệ.
#
# file_utils trả JPEG khi phải nén lại, nhưng trả NGUYÊN BYTE GỐC (thường PNG)
# khi ảnh đã đủ nhỏ — nên không được đoán cứng một đuôi.
_FILE_SIGNATURES = ((b"\x89PNG", "png"), (b"%PDF", "pdf"), (b"\xff\xd8", "jpg"),
           (b"GIF8", "gif"), (b"BM", "bmp"))


def _upload_name(image_bytes: bytes) -> str:
    """Tên file gửi kèm. Cũng chính là TÊN TRƯỜNG trong multipart.

    Dạng này đã gọi thật được (xem tools/thu_ocrspace.py); đừng đổi sang
    files={"file": ...} nếu chưa gọi thử lại.
    """
    for signature, suffix in _FILE_SIGNATURES:
        if image_bytes.startswith(signature):
            return f"chungchi.{suffix}"
    return "chungchi.jpg"


def _sleep_before_retry(attempt: int, reason: str) -> None:
    wait = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
    logger.warning("OCR.space lỗi tạm thời (%s), thử lại lần %d/%d sau %ds.",
                   reason, attempt + 1, MAX_ATTEMPTS, wait)
    time.sleep(wait)


def _explain_http_error(r, attempts: int) -> str:
    # 401/403 gắn TAG_NEEDS_HUMAN: chúng KHÔNG tự khỏi. alert.py đọc mốc này
    # để đổi giọng email, vì thư mặc định hẹn "tự xử lý" là sai với hai ca này.
    explanation = {
        401: f"{llm_error.TAG_NEEDS_HUMAN}: Sai OCRSPACE_API_KEY.",
        403: (f"{llm_error.TAG_NEEDS_HUMAN}: Bị từ chối — thường là hết hạn "
              f"mức miễn phí (25.000 lượt/tháng, 500 lượt/ngày mỗi IP). "
              f"Thử lại sẽ không tự khỏi trước khi hạn mức làm mới."),
        429: "Bị giới hạn tốc độ. Tạm thời.",
        500: "Lỗi phía OCR.space. Tạm thời.",
        502: "Gateway của OCR.space lỗi. Tạm thời.",
        503: "OCR.space quá tải hoặc bảo trì. Tạm thời.",
    }
    hint = explanation.get(r.status_code, "")
    attempts_note = f" (đã thử {attempts} lần)" if attempts > 1 else ""
    body_text = " ".join((r.text or "")[:200].split())
    return f"[OCR.space {r.status_code}] {body_text}. {hint}{attempts_note}".strip()


def _explain_body_error(body: dict, seconds: float) -> str:
    """Lỗi OCR.space báo trong thân JSON kèm HTTP 200."""
    message = body.get("ErrorMessage") or body.get("ErrorDetails") or "?"
    if isinstance(message, list):
        message = " | ".join(str(x) for x in message)
    message = " ".join(str(message).split())

    hint = ""
    if "E201" in message:
        hint = (f" {llm_error.TAG_NEEDS_HUMAN}: engine đang dùng không hỗ trợ "
                f"ngôn ngữ {settings.ocrspace_language!r}. Đổi OCRSPACE_ENGINE "
                f"sang 2 hoặc 3.")
    elif "E216" in tb or "api key" in tb.lower():
        hint = f" {llm_error.TAG_NEEDS_HUMAN}: kiểm lại OCRSPACE_API_KEY."

    return f"[OCR.space] {message}.{hint} [{seconds:.1f}s]".strip()

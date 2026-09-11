"""OCR bằng Azure Document Intelligence (ocr_azure).

Chỉ lo một việc: nhận ảnh (bytes) -> trả text thô.

KHÔNG kiểm tra định dạng file hay render PDF — file_utils đã làm việc đó và
đưa vào đây ảnh bytes sạch sẽ.
"""

import logging
import time

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError

import llm_error
from config import settings

logger = logging.getLogger(__name__)

MODEL_READ = "prebuilt-read"

# Mã lỗi TẠM THỜI — thử lại thì có cơ may thành công. 408 gặp thật: Azure báo
# "The operation was timeout" sau ~43 giây do dịch vụ chậm, không phải ảnh hỏng
# hay hết quota. Không thử lại thì chứng chỉ HỢP LỆ bị từ chối vĩnh viễn eLIS.
MA_LOI_TAM_THOI = frozenset({408, 429, 500, 502, 503, 504})

# Số lần gọi Azure tối đa cho MỘT trang, và giãn cách. Ngắn thôi: nằm trong
# vòng xử lý chứng chỉ, kéo dài thì cả lô đứng.
SO_LAN_THU = 3
GIAN_CACH_GIAY = (2, 5)


class OcrError(Exception):
    """Lỗi khi gọi Azure OCR, đã diễn giải sang tiếng Việt."""


def create_client() -> DocumentIntelligenceClient:
    """Tạo client Azure Document Intelligence từ cấu hình .env."""
    return DocumentIntelligenceClient(
        endpoint=settings.azure_endpoint,
        credential=AzureKeyCredential(settings.azure_key),
    )


def ocr_bytes(client: DocumentIntelligenceClient, image_bytes: bytes) -> str:
    """OCR một ảnh (bytes), trả về text thô.

    TỰ THỬ LẠI với lỗi tạm thời (timeout, rate limit, 5xx); lỗi vĩnh viễn (sai
    key, ảnh hỏng, hết quota) thì ném ngay vì kết quả không đổi. Ném OcrError
    nếu hết lượt thử hoặc không đọc được chữ nào.
    """
    loi_cuoi = None
    for attempt in range(1, SO_LAN_THU + 1):
        bat_dau = time.monotonic()
        try:
            poller = client.begin_analyze_document(
                MODEL_READ,
                body=image_bytes,
                content_type="application/octet-stream",
            )
            verdict = poller.result()
        except HttpResponseError as e:
            loi_cuoi = e
            # Ghi KÍCH THƯỚC ảnh và THỜI GIAN chờ: hai con số này phân biệt lỗi
            # phía mình với lỗi phía Azure — ảnh vài trăm KB mà timeout sau ~40
            # giây là dịch vụ chậm, không phải tài liệu nặng.
            logger.warning("Azure lỗi sau %.1fs (ảnh %.0f KB, lần %d/%d): %s",
                           time.monotonic() - bat_dau, len(image_bytes) / 1024,
                           attempt, SO_LAN_THU, e.status_code)
            if (e.status_code or 0) not in MA_LOI_TAM_THOI or attempt == SO_LAN_THU:
                raise OcrError(_explain_error(e, attempt)) from e
            wait = GIAN_CACH_GIAY[min(attempt - 1, len(GIAN_CACH_GIAY) - 1)]
            logger.warning("Azure lỗi tạm thời (%s), thử lại lần %d/%d sau %ds.",
                           e.status_code, attempt + 1, SO_LAN_THU, wait)
            time.sleep(wait)
            continue

        giay = time.monotonic() - bat_dau
        text = verdict.content or ""
        if not text.strip():
            # Azure trả 200 nhưng rỗng: ảnh không có chữ. Thử lại vô ích.
            raise OcrError(
                f"Azure không đọc được chữ nào (ảnh có thể mờ hoặc trống). "
                f"[ảnh {len(image_bytes) / 1024:.0f} KB, {giay:.1f}s]")
        logger.debug("Azure OK sau %.1fs (ảnh %.0f KB, %d ký tự).",
                     giay, len(image_bytes) / 1024, len(text))
        return text

    raise OcrError(_explain_error(loi_cuoi, SO_LAN_THU))


def ocr_images(client: DocumentIntelligenceClient, images: list[bytes]) -> str:
    """OCR nhiều ảnh (ví dụ PDF nhiều trang), nối text lại.

    Một trang lỗi thì bỏ qua trang đó; chỉ ném lỗi khi KHÔNG trang nào đọc được.

    KHI HỎNG HẾT, THÔNG BÁO PHẢI MANG THEO LÝ DO THẬT của từng trang: hết quota
    (403), sai key (401), rate-limit (429) và ảnh mờ nếu không nói rõ thì hiện
    ra y hệt nhau, người vận hành không biết phải đi sửa gì.
    """
    parts = []
    ly_do_hong: list[str] = []
    for i, image in enumerate(images, 1):
        try:
            parts.append(ocr_bytes(client, image))
        except OcrError as e:
            # Một trang lỗi không nên làm hỏng cả tài liệu, nhưng phải nhớ vì sao.
            ly_do_hong.append(f"trang {i}: {e}")

    if not parts:
        chi_tiet = " | ".join(ly_do_hong) if ly_do_hong else "không có trang nào"
        raise OcrError(f"Không trang nào đọc được chữ ({chi_tiet}).")
    return "\n\n".join(parts)


def _explain_error(e: HttpResponseError, attempts: int = 1) -> str:
    # Ca 401/403 gắn TAG_NEEDS_HUMAN: chúng KHÔNG tự khỏi. alert.py đọc mốc này
    # để đổi giọng email, vì thư mặc định hẹn "tự xử lý" là sai với hai ca này.
    explanation = {
        400: "Yêu cầu không hợp lệ (ảnh hỏng hoặc định dạng lỗi).",
        401: f"{llm_error.TAG_NEEDS_HUMAN}: Sai AZURE_KEY. Sửa .env rồi khởi động lại job.",
        403: (f"{llm_error.TAG_NEEDS_HUMAN}: Hết quota Azure Free tier "
              f"(500 trang/tháng) hoặc ảnh quá 4 MB. Hết quota thì phải NÂNG "
              f"GÓI — thử lại sẽ không tự khỏi wait tới đầu tháng sau."),
        408: ("Azure xử lý quá lâu rồi bỏ cuộc. Lỗi TẠM THỜI — thường do dịch "
              "vụ đang tải nặng, không phải ảnh hỏng."),
        429: "Bị giới hạn tốc độ.",
        500: "Lỗi phía Azure. Tạm thời.",
        503: "Azure đang quá tải hoặc bảo trì. Tạm thời.",
    }
    added = explanation.get(e.status_code or 0, "")
    attempt = f" (đã thử {attempts} lần)" if attempts > 1 else ""
    # message của Azure hay xuống dòng nhiều lần; ép về một dòng cho log đọc được.
    thong_diep = " ".join((e.message or "").split())
    return f"[Azure {e.status_code}] {thong_diep}. {added}{attempt}".strip()

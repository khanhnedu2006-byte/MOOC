"""OCR bằng Azure Document Intelligence (ocr_azure).

Chỉ lo một việc: nhận ảnh (bytes) -> trả text thô.

Khác với bản cũ ở phần chứng chỉ: module này KHÔNG kiểm tra định dạng file
hay render PDF — file_utils đã làm việc đó và đưa vào đây ảnh bytes sạch sẽ.

Dùng trong pipeline:
    from ocr_azure import tao_client, ocr_bytes
    client = tao_client()
    text = ocr_bytes(client, anh_bytes)
"""

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError

from config import settings

MODEL_READ = "prebuilt-read"


class OcrError(Exception):
    """Lỗi khi gọi Azure OCR, đã diễn giải sang tiếng Việt."""


def tao_client() -> DocumentIntelligenceClient:
    """Tạo client Azure Document Intelligence từ cấu hình .env."""
    return DocumentIntelligenceClient(
        endpoint=settings.azure_endpoint,
        credential=AzureKeyCredential(settings.azure_key),
    )


def ocr_bytes(client: DocumentIntelligenceClient, anh_bytes: bytes) -> str:
    """OCR một ảnh (bytes), trả về text thô.

    Ném OcrError nếu gọi Azure thất bại hoặc không đọc được chữ nào.
    """
    try:
        poller = client.begin_analyze_document(
            MODEL_READ,
            body=anh_bytes,
            content_type="application/octet-stream",
        )
        ket_qua = poller.result()
    except HttpResponseError as e:
        raise OcrError(_dien_giai_loi(e)) from e

    text = ket_qua.content or ""
    if not text.strip():
        raise OcrError("Azure không đọc được chữ nào (ảnh có thể mờ hoặc trống).")
    return text


def ocr_nhieu_anh(client: DocumentIntelligenceClient, anh_list: list[bytes]) -> str:
    """OCR nhiều ảnh (ví dụ PDF nhiều trang), nối text lại.

    Nếu một trang lỗi thì bỏ qua trang đó, vẫn trả text các trang còn lại.
    Chỉ ném lỗi khi KHÔNG trang nào đọc được.
    """
    cac_phan = []
    for i, anh in enumerate(anh_list, 1):
        try:
            cac_phan.append(ocr_bytes(client, anh))
        except OcrError:
            # Một trang lỗi không nên làm hỏng cả tài liệu.
            continue

    if not cac_phan:
        raise OcrError("Không trang nào đọc được chữ.")
    return "\n\n".join(cac_phan)


def _dien_giai_loi(e: HttpResponseError) -> str:
    giai_thich = {
        400: "Yêu cầu không hợp lệ (ảnh hỏng hoặc định dạng lỗi).",
        401: "Sai key Azure.",
        403: "Hết quota Free tier (500 trang/tháng) hoặc ảnh quá 4 MB.",
        429: "Bị giới hạn tốc độ, thử lại sau.",
    }
    them = giai_thich.get(e.status_code or 0, "")
    return f"[Azure {e.status_code}] {e.message}. {them}".strip()
"""Gemma đọc ảnh chứng chỉ qua FPT (llm_vision) — đây là LLM1.

Nhận ảnh (bytes) -> trích 4 trường -> trả về ExtractedInfo.

Cách ép JSON: yêu cầu rõ trong prompt + tự parse bằng Pydantic. Không dùng
with_structured_output vì không chắc endpoint FPT hỗ trợ json_schema mode;
cách này chạy được với mọi endpoint OpenAI-compatible.

Dùng trong pipeline:
    from llm_vision import extract_from_image
    info = extract_from_image(image_bytes)
"""

import base64
import json

from langchain_core.messages import HumanMessage

from config import get_llm
import llm_error
from schemas import ExtractedInfo

PROMPT = """Bạn là công cụ trích xuất dữ liệu từ ảnh chứng chỉ/bằng cấp.
Ảnh có thể là tiếng Việt, tiếng Anh hoặc lẫn cả hai.

Trả về ĐÚNG một object JSON với 4 trường sau, không kèm giải thích, không kèm
markdown:

{
  "recipient_name": "tên NGƯỜI ĐƯỢC CẤP, hoặc null",
  "signatory_name": "tên người KÝ chứng chỉ (giám đốc/hiệu trưởng...), hoặc null",
  "certificate_name": "tên khóa học, bản TIẾNG VIỆT nếu có (xem quy tắc), hoặc null",
  "certificate_name_alt": "tên khóa học bản TIẾNG ANH, null nếu chỉ có một ngôn ngữ",
  "issue_date": "ngày cấp giữ nguyên như in trên chứng chỉ, hoặc null",
  "expiry_date": "ngày hết hạn, hoặc null nếu vô thời hạn/không ghi"
}

QUY TẮC recipient_name — ĐỌC KỸ, đây là chỗ hay sai nhất:
- Người được cấp thường nằm ở KHOẢNG GIỮA trang, ngay sau các cụm như
  "Ghi nhận", "Chứng nhận rằng", "This is to certify that", "Awarded to",
  "Presented to", "has successfully completed".
- TUYỆT ĐỐI KHÔNG lấy tên nằm cạnh CHỮ KÝ, CON DẤU, hay chức danh
  ("Giám đốc", "Chief Delivery Officer", "Director", "CEO", "Hiệu trưởng")
  — thường ở CUỐI trang, góc phải. Tên đó điền vào signatory_name.
- ĐỪNG chọn theo cỡ chữ hay độ đậm. Tên giám đốc thường IN ĐẬM VIẾT HOA to
  hơn hẳn, còn tên người nhận có khi chỉ là chữ mảnh, hoặc chỉ là username
  dạng "tienpham89", "hungnt97". Chữ to hơn KHÔNG có nghĩa là người nhận.
- Nếu chỗ người nhận chỉ có username hoặc email, hãy trả về ĐÚNG chuỗi đó,
  không suy ra tên thật, không lấy tên nào khác thay thế.
- Nếu không chắc đâu là người nhận, để null. Null tốt hơn lấy nhầm.

QUY TẮC certificate_name / certificate_name_alt — TÁCH THEO NGÔN NGỮ:
- CHỈ lấy TÊN KHÓA HỌC, KHÔNG lấy câu bao quanh nó. Ví dụ dòng in trên ảnh:
      Đã hoàn thành khoá học "Python cơ bản"
      Has successfully completed the course "Python fundamentals"
  thì tên khóa là "Python cơ bản" và "Python fundamentals" — KHÔNG phải cả
  câu "Đã hoàn thành khoá học...". Tên khóa thường nằm trong dấu ngoặc kép,
  in đậm, hoặc trên một dòng riêng cỡ chữ lớn hơn.
- BỎ các cụm chung chung: "Certificate of Completion", "Chứng nhận hoàn
  thành", "Đã hoàn thành khóa học", "Has successfully completed the course",
  "Giấy chứng nhận".
- Nếu tên khóa xuất hiện bằng HAI ngôn ngữ (dù nằm trên hai dòng riêng, hay
  cùng một dòng nối bằng "-", "–", "/", hay trong ngoặc):
      certificate_name     = bản TIẾNG VIỆT
      certificate_name_alt = bản TIẾNG ANH
  Ví dụ "BỘ QUY ĐỊNH CHÍNH SÁCH CẦN BIẾT FPT - FPT KEY REGULATIONS AND
  POLICIES (ENGLISH VERSION)" -> certificate_name = "BỘ QUY ĐỊNH CHÍNH SÁCH
  CẦN BIẾT FPT", certificate_name_alt = "FPT KEY REGULATIONS AND POLICIES
  (ENGLISH VERSION)".
- Nếu chỉ có MỘT ngôn ngữ: đặt vào certificate_name, còn
  certificate_name_alt = null. Không phân biệt đó là tiếng gì.
- KHÔNG TỰ DỊCH. Chỉ tách khi ảnh THẬT SỰ in cả hai ngôn ngữ.

QUY TẮC CHỮ KHÔNG PHẢI LATIN (Nhật, Trung, Hàn...):
- CHÉP NGUYÊN KÝ TỰ GỐC. Không phiên âm, không romaji, không dịch.
- Nếu KHÔNG đọc được một cụm ký tự, hãy BỎ TRỐNG cụm đó và giữ nguyên phần
  còn lại — TUYỆT ĐỐI không đoán bừa một chuỗi chữ Latin thay vào. Đoán bừa
  làm hỏng phép đối chiếu tệ hơn hẳn so với thiếu vài chữ.

QUY TẮC ngày: giữ nguyên như in trên ảnh, không đổi định dạng, không suy diễn.
Số hiệu chứng chỉ KHÔNG phải ngày. KHÔNG lấy ngày từ đồng hồ hệ thống hay
thanh taskbar nếu ảnh là ảnh chụp màn hình.

Trường nào không thấy thì để null, không bịa. Chỉ trả JSON."""


class LlmVisionError(Exception):
    """Lỗi khi gọi Gemma hoặc parse kết quả."""


# Chữ ký byte đầu file -> kiểu MIME. Nhận dạng theo NỘI CORRECT, không theo
# đuôi file hay giả định, vì file_utils.compress_to_fit() có thể đã đổi ảnh sang
# JPEG để lọt giới hạn kích thước request.
_MIME_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff",      "image/jpeg"),
    (b"BM",                "image/bmp"),
    (b"II*\x00",           "image/tiff"),
    (b"MM\x00*",           "image/tiff"),
)


def _image_to_data_url(image_bytes: bytes) -> str:
    """Mã hóa ảnh thành data URL base64 để gửi qua API.

    Kiểu MIME lấy từ CHỮ KÝ BYTE thật. Trước đây hàm khai cứng "image/png"
    cho mọi ảnh — chấp nhận được khi mọi ảnh đều là PNG, nhưng sai kể từ khi
    compress_to_fit() đổi ảnh lớn sang JPEG. Khai sai MIME thì API có thể từ
    chối, hoặc tệ hơn là đọc sai ảnh mà không báo gì.
    """
    kind = next((mime for signature, mime in _MIME_SIGNATURES
                 if image_bytes.startswith(signature)), "image/png")
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{kind};base64,{b64}"


def _strip_json_fence(text: str) -> str:
    """Bóc phần JSON ra khỏi text, phòng khi model kèm markdown ```json."""
    text = text.strip()
    if text.startswith("```"):
        # Bỏ ```json ... ``` nếu có
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def extract_from_image(image_bytes: bytes, llm=None) -> ExtractedInfo:
    """Gửi ảnh cho Gemma, trả về ExtractedInfo.

    Cho phép truyền llm sẵn (để test hoặc tái dùng client). Nếu không thì tự tạo.
    """
    if llm is None:
        llm = get_llm()

    message = HumanMessage(content=[
        {"type": "text", "text": PROMPT},
        {"type": "image_url", "image_url": {"url": _image_to_data_url(image_bytes)}},
    ])

    # Thử lại lỗi TẠM THỜI (rate-limit, 5xx, rớt mạng), KHÔNG thử lại lỗi
    # vĩnh viễn (hết tiền, sai key). Bảng phân loại nằm ở llm_error để hai
    # file llm_vision/llm_text dùng chung một bản — chép hai bản là cách chắc
    # chắn để chúng lệch nhau, đúng chuyện đã xảy ra với prompt.
    try:
        phan_hoi = llm_error.call_with_retry(
            lambda: llm.invoke([message]), "LLM1 (Gemma đọc ảnh)")
    except Exception as e:
        raise LlmVisionError(llm_error.describe(e, llm_error.MAX_ATTEMPTS)) from e

    content = _strip_json_fence(phan_hoi.content)

    try:
        data_bytes = json.loads(content)
    except json.JSONDecodeError as e:
        raise LlmVisionError(f"Gemma trả về không phải JSON hợp lệ: {content[:200]}") from e

    try:
        return ExtractedInfo.model_validate(data_bytes)
    except Exception as e:
        raise LlmVisionError(f"JSON không khớp schema: {e}") from e

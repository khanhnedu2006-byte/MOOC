"""Gemma đọc ảnh chứng chỉ qua FPT (llm_vision) — LLM1.

Nhận ảnh (bytes) -> trích 4 trường -> ExtractedInfo.

Ép JSON bằng prompt + parse Pydantic, không dùng with_structured_output:
không chắc endpoint FPT hỗ trợ json_schema mode. Cách này chạy với mọi
endpoint OpenAI-compatible.
"""

import base64
import json

from langchain_core.messages import HumanMessage

from config import get_llm
import llm_error
from schemas import ExtractedInfo, ExtractionParseError, ImageKind

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
- Lấy thêm suffix BY PROVIDER NAME nếu có, ví dụ "Python cơ bản by akabot"
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


class LlmVisionParseError(LlmVisionError, ExtractionParseError):
    """Gemma trả lời nhưng không phải JSON hợp lệ / sai schema."""


# Chữ ký byte đầu file -> kiểu MIME. Nhận theo nội dung, không theo đuôi file:
# file_utils.compress_to_fit() có thể đã đổi ảnh sang JPEG cho lọt giới hạn.
_MIME_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff",      "image/jpeg"),
    (b"BM",                "image/bmp"),
    (b"II*\x00",           "image/tiff"),
    (b"MM\x00*",           "image/tiff"),
)


def _image_to_data_url(image_bytes: bytes) -> str:
    """Mã hóa ảnh thành data URL base64 để gửi qua API.

    MIME lấy từ CHỮ KÝ BYTE thật. Khai sai MIME thì API có thể từ chối, hoặc
    tệ hơn là đọc sai ảnh mà không báo gì.
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


def _ask_json(prompt: str, image_bytes: bytes, llm, label: str) -> dict:
    """Gửi prompt + ảnh cho Gemma, trả về JSON đã parse."""
    if llm is None:
        llm = get_llm()

    message = HumanMessage(content=[
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": _image_to_data_url(image_bytes)}},
    ])

    # Thử lại lỗi TẠM THỜI (rate-limit, 5xx, rớt mạng), KHÔNG thử lại lỗi vĩnh
    # viễn (hết tiền, sai key). Bảng phân loại để chung ở llm_error cho
    # llm_vision và llm_text dùng một bản, tránh hai bên lệch nhau.
    try:
        phan_hoi = llm_error.call_with_retry(lambda: llm.invoke([message]), label)
    except Exception as e:
        raise LlmVisionError(llm_error.describe(e, llm_error.MAX_ATTEMPTS)) from e

    content = _strip_json_fence(phan_hoi.content)

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise LlmVisionParseError(
            f"Gemma trả về không phải JSON hợp lệ: {content[:200]}") from e


def extract_from_image(image_bytes: bytes, llm=None) -> ExtractedInfo:
    """Gửi ảnh cho Gemma, trả về ExtractedInfo.

    Truyền sẵn llm để test hoặc tái dùng client; không thì tự tạo.
    """
    data_bytes = _ask_json(PROMPT, image_bytes, llm, "LLM1 (Gemma đọc ảnh)")
    try:
        return ExtractedInfo.model_validate(data_bytes)
    except Exception as e:
        raise LlmVisionParseError(f"JSON không khớp schema: {e}") from e


# Prompt RIÊNG cho bước lọc ảnh nhiều khóa học, KHÔNG gộp vào PROMPT của LLM1:
# đổi PROMPT là phải đánh giá lại toàn bộ bước trích xuất. Gọi riêng tốn thêm
# một lượt Gemma (~1 giây) mỗi chứng chỉ.
#
# Hai tín hiệu, pipeline đòi CẢ HAI: page_type là "course_list" VÀ có từ 2
# tên khóa trở lên. Chứng chỉ chương trình (Specialization, Learning Path...)
# in danh sách khóa con nhưng vẫn là MỘT chứng chỉ — prompt dặn rõ ca này.
COURSE_LIST_PROMPT = """Bạn phân loại ảnh người học nộp làm bằng chứng hoàn thành khóa học.

Trả về ĐÚNG một object JSON, không giải thích, không markdown:

{
  "page_type": "certificate" | "course_list" | "other",
  "course_titles": ["tên từng khóa học RIÊNG BIỆT hiện trên ảnh"]
}

page_type:
- "certificate": MỘT chứng chỉ / giấy chứng nhận / trang xác nhận hoàn thành
  cho MỘT khóa học (hoặc một chương trình). Chứng chỉ chương trình
  (Specialization, Professional Certificate, Learning Path, Nanodegree...)
  có in danh sách các khóa con BÊN TRONG nó vẫn là "certificate".
- "course_list": ảnh chụp màn hình một DANH SÁCH / BẢNG / LƯỚI nhiều khóa học
  khác nhau, mỗi khóa là một dòng hoặc một thẻ riêng có trạng thái / ngày /
  nút riêng — ví dụ trang hồ sơ "Registrations", "My learning", "My courses",
  "Đã hoàn thành", trang quản lý khóa học, dashboard, lịch sử học tập.
  Cũng tính là "course_list" khi ghép NHIỀU chứng chỉ của các khóa KHÁC NHAU
  vào một ảnh.
- "other": không thuộc hai loại trên.

course_titles: chép nguyên văn tên các khóa học mà ảnh trình bày như những
mục RIÊNG BIỆT ngang hàng nhau. KHÔNG tính: khóa con nằm trong một chứng chỉ
chương trình, khóa "gợi ý / đề xuất / liên quan" ở thanh bên, tên trên tab
trình duyệt. Chứng chỉ thường thì danh sách chỉ có 1 phần tử."""


def detect_course_list(image_bytes: bytes, llm=None) -> ImageKind:
    """Hỏi Gemma ảnh là một chứng chỉ hay danh sách nhiều khóa học."""
    data = _ask_json(COURSE_LIST_PROMPT, image_bytes, llm,
                     "Lọc ảnh nhiều khóa học (Gemma)")
    try:
        return ImageKind.model_validate(data)
    except Exception as e:
        raise LlmVisionError(f"JSON không khớp schema: {e}") from e

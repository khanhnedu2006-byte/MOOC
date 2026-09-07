"""LLM2 trích thông tin từ text OCR (llm_text).

Nhận text thô (do Azure OCR đọc ra) -> trích 4 trường -> ExtractedInfo.

Khác llm_vision: nhận TEXT chứ không phải ảnh. Dùng ở nhánh backup của
pipeline, sau khi Azure OCR đã đọc ảnh khó thành text sạch.

Trả về cùng schema ExtractedInfo với llm_vision, để pipeline so được
kết quả LLM1 (Gemma) với LLM2.

Dùng trong pipeline:
    from llm_text import extract_from_text
    info = extract_from_text(ocr_text)
"""

import json

from langchain_core.messages import HumanMessage

from config import get_llm
import llm_error
from schemas import ExtractedInfo

PROMPT = """Bạn là công cụ trích xuất dữ liệu từ text đọc được từ chứng chỉ.
Text do OCR đọc ra, có thể tiếng Việt, tiếng Anh hoặc lẫn cả hai, có thể sai
chính tả nhẹ do lỗi nhận dạng ký tự.

Trả về ĐÚNG một object JSON với 4 trường sau, không kèm giải thích, không kèm
markdown:

{
  "recipient_name": "tên NGƯỜI ĐƯỢC CẤP, hoặc null",
  "signatory_name": "tên người KÝ chứng chỉ (giám đốc/hiệu trưởng...), hoặc null",
  "certificate_name": "tên khóa học, bản TIẾNG VIỆT nếu có (xem quy tắc), hoặc null",
  "certificate_name_alt": "tên khóa học bản TIẾNG ANH, null nếu chỉ có một ngôn ngữ",
  "issue_date": "ngày cấp giữ nguyên như in trong text, hoặc null",
  "expiry_date": "ngày hết hạn, hoặc null nếu vô thời hạn/không ghi"
}

QUY TẮC recipient_name — ĐỌC KỸ, đây là chỗ hay sai nhất:
- Người được cấp thường xuất hiện ở PHẦN ĐẦU text, ngay sau các cụm như
  "Ghi nhận", "Chứng nhận rằng", "This is to certify that", "Awarded to",
  "Presented to", "has successfully completed".
- TUYỆT ĐỐI KHÔNG lấy tên nằm cạnh CHỮ KÝ, CON DẤU, hay chức danh
  ("Giám đốc", "Chief Delivery Officer", "Director", "CEO", "Hiệu trưởng")
  — thường ở CUỐI text. Tên đó điền vào signatory_name.
- OCR mất hết thông tin cỡ chữ nên đừng đoán theo hình thức. Lưu ý tên người nhận có khi chỉ là chữ mảnh, hoặc chỉ là username
  dạng "tienpham89", "hungnt97". Chữ to hơn KHÔNG có nghĩa là người nhận.
- Nếu chỗ người nhận chỉ có username hoặc email, hãy trả về ĐÚNG chuỗi đó,
  không suy ra tên thật, không lấy tên nào khác thay thế.
- Nếu không chắc đâu là người nhận, để null. Null tốt hơn lấy nhầm.

QUY TẮC certificate_name / certificate_name_alt — TÁCH THEO NGÔN NGỮ:
- CHỈ lấy TÊN KHÓA HỌC, KHÔNG lấy câu bao quanh nó. Ví dụ dòng có trong text:
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
- KHÔNG TỰ DỊCH. Chỉ tách khi text THẬT SỰ có cả hai ngôn ngữ.

QUY TẮC CHỮ KHÔNG PHẢI LATIN (Nhật, Trung, Hàn...):
- CHÉP NGUYÊN KÝ TỰ GỐC. Không phiên âm, không romaji, không dịch.
- Nếu KHÔNG đọc được một cụm ký tự, hãy BỎ TRỐNG cụm đó và giữ nguyên phần
  còn lại — TUYỆT ĐỐI không đoán bừa một chuỗi chữ Latin thay vào. Đoán bừa
  làm hỏng phép đối chiếu tệ hơn hẳn so với thiếu vài chữ.

QUY TẮC ngày: giữ nguyên như trong text, không đổi định dạng, không suy diễn.
Số hiệu chứng chỉ KHÔNG phải ngày. KHÔNG lấy ngày từ đồng hồ hệ thống hay
thanh taskbar nếu text lấy từ ảnh chụp màn hình.

Trường nào không thấy thì để null, không bịa. Chỉ trả JSON.

Đây là text cần trích:
---
{ocr_text}
---"""


# Chỗ trong PROMPT sẽ được thay bằng text OCR thật.
#
# Đặt thành HẰNG SỐ chứ không viết chuỗi thẳng trong .replace(): trước đây
# PROMPT ghi "{ocr_text}" còn code lại replace("{text_ocr}") — lệch tên nên
# không thay gì cả, và LLM nhận nguyên chuỗi "{ocr_text}" rồi trả lời "vui
# lòng cung cấp nội dung {ocr_text}". Không có lỗi nào được ném ra ở chỗ
# thay thế; hỏng chỉ lộ ra ở tận bước parse JSON. Một hằng số dùng chung cho
# cả hai nơi khiến kiểu lệch đó không xảy ra được nữa, và có test canh.
PLACEHOLDER = "{ocr_text}"


class LlmTextError(Exception):
    """Lỗi khi gọi LLM2 hoặc parse kết quả."""


def _strip_json_fence(text: str) -> str:
    """Bóc phần JSON ra khỏi text, phòng khi model kèm markdown ```json."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def extract_from_text(ocr_text: str, llm=None) -> ExtractedInfo:
    """Gửi text OCR cho LLM2, trả về ExtractedInfo.

    Cho phép truyền llm sẵn (để test hoặc tái dùng client).
    """
    if not ocr_text or not ocr_text.strip():
        raise LlmTextError("Text OCR rỗng, không có gì để trích.")

    if llm is None:
        llm = get_llm()

    prompt_content = PROMPT.replace(PLACEHOLDER, ocr_text)
    message = HumanMessage(content=prompt_content)

    # Thử lại lỗi TẠM THỜI (rate-limit, 5xx, rớt mạng), KHÔNG thử lại lỗi
    # vĩnh viễn (hết tiền, sai key). Bảng phân loại nằm ở llm_error để hai
    # file llm_vision/llm_text dùng chung một bản — chép hai bản là cách chắc
    # chắn để chúng lệch nhau, đúng chuyện đã xảy ra với prompt.
    try:
        phan_hoi = llm_error.call_with_retry(
            lambda: llm.invoke([message]), "LLM2 (đọc text OCR)")
    except Exception as e:
        raise LlmTextError(llm_error.describe(e, llm_error.MAX_ATTEMPTS)) from e

    content = _strip_json_fence(phan_hoi.content)

    try:
        data_bytes = json.loads(content)
    except json.JSONDecodeError as e:
        raise LlmTextError(f"LLM2 trả về không phải JSON hợp lệ: {content[:200]}") from e

    try:
        return ExtractedInfo.model_validate(data_bytes)
    except Exception as e:
        raise LlmTextError(f"JSON không khớp schema: {e}") from e

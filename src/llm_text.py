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
from schemas import ExtractedInfo

PROMPT = """Bạn là công cụ trích xuất dữ liệu từ text đọc được từ chứng chỉ.
Text do OCR đọc ra, có thể tiếng Việt, tiếng Anh hoặc lẫn cả hai, có thể sai
chính tả nhẹ do lỗi nhận dạng ký tự.

Trả về ĐÚNG một object JSON với 4 trường sau, không kèm giải thích, không kèm
markdown:

{
  "recipient_name": "tên đầy đủ người được cấp, hoặc null",
  "certificate_name": "tên CỤ THỂ của chứng chỉ/khóa học/danh hiệu, hoặc null",
  "certificate_name_alt": "nếu tên khóa học in SONG NGỮ thì đây là phần ngôn ngữ còn lại, ngược lại null",
  "issue_date": "ngày cấp giữ nguyên như in trong text, hoặc null",
  "expiry_date": "ngày hết hạn, hoặc null nếu vô thời hạn/không ghi"
}

QUY TẮC certificate_name:
- Lấy tên CỤ THỂ (vd "Certified Management Accountant", "Data Analyst Nanodegree").
- KHÔNG lấy cụm chung chung như "Certificate of Completion", "Chứng nhận hoàn thành".
- Nếu có cả hai, ưu tiên tên cụ thể.

QUY TẮC SONG NGỮ:
- Nếu tên khóa học được in bằng CẢ tiếng Việt VÀ tiếng Anh (ví dụ "An toàn
  thông tin / Information Security", hoặc "Data Analysis (Phân tích dữ liệu)"),
  hãy TÁCH thành hai phần:
  - certificate_name: một ngôn ngữ (ưu tiên tiếng Việt nếu có)
  - certificate_name_alt: ngôn ngữ còn lại
- Nếu tên khóa học chỉ có MỘT ngôn ngữ thì certificate_name_alt = null.
- KHÔNG tự dịch. Chỉ tách khi ảnh THẬT SỰ in cả hai ngôn ngữ.

QUY TẮC ngày: giữ nguyên như trong text, không đổi định dạng, không suy diễn.
Số hiệu chứng chỉ KHÔNG phải ngày.

Trường nào không thấy thì để null, không bịa. Chỉ trả JSON.

Đây là text cần trích:
---
{ocr_text}
---"""


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

    prompt_content = PROMPT.replace("{text_ocr}", ocr_text)
    message = HumanMessage(content=prompt_content)

    try:
        phan_hoi = llm.invoke([message])
    except Exception as e:
        raise LlmTextError(f"Lỗi gọi LLM2: {e}") from e

    content = _strip_json_fence(phan_hoi.content)

    try:
        data_bytes = json.loads(content)
    except json.JSONDecodeError as e:
        raise LlmTextError(f"LLM2 trả về không phải JSON hợp lệ: {content[:200]}") from e

    try:
        return ExtractedInfo.model_validate(data_bytes)
    except Exception as e:
        raise LlmTextError(f"JSON không khớp schema: {e}") from e
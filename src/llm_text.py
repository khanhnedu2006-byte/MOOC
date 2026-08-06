import json

from langchain_core.messages import HumanMessage

from config import get_llm
from schemas import ThongTinTrichXuat

PROMPT = """Bạn là công cụ trích xuất dữ liệu từ text đọc được từ chứng chỉ.
Text do OCR đọc ra, có thể tiếng Việt, tiếng Anh hoặc lẫn cả hai.

Trả về ĐÚNG một object JSON với 4 trường sau, không kèm giải thích, không kèm
markdown:

{
  "ten_nguoi_nhan": "tên đầy đủ người được cấp, hoặc null",
  "ten_chung_chi": "tên CỤ THỂ của chứng chỉ/khóa học/danh hiệu, hoặc null",
  "ngay_nhan": "ngày cấp giữ nguyên như in trong text, hoặc null",
  "ngay_het_han": "ngày hết hạn, hoặc null nếu vô thời hạn/không ghi"
}

QUY TẮC ten_chung_chi:
- Lấy tên CỤ THỂ (vd "Certified Management Accountant", "Data Analyst Nanodegree").
- KHÔNG lấy cụm chung chung như "Certificate of Completion", "Chứng nhận hoàn thành".
- Nếu có cả hai, ưu tiên tên cụ thể.

QUY TẮC ngày: giữ nguyên như trong text, không đổi định dạng, không suy diễn.
Số hiệu chứng chỉ KHÔNG phải ngày.

Trường nào không thấy thì để null, không bịa. Chỉ trả JSON.

Đây là text cần trích:
---
{text_ocr}
---"""

class LlmTextError(Exception):
    """Lỗi khi gọi LLM2 hoặc parse kết quả."""

def _lam_sach_json(text: str) -> str:
    """Bóc phần JSON ra khỏi text, phòng khi model kèm markdown ```json."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def trich_tu_text(text_ocr: str, llm=None) -> ThongTinTrichXuat:
    """Gửi text OCR cho LLM2, trả về ThongTinTrichXuat.

    Cho phép truyền llm sẵn (để test hoặc tái dùng client).
    """
    if not text_ocr or not text_ocr.strip():
        raise LlmTextError("Text OCR rỗng, không có gì để trích.")

    if llm is None:
        llm = get_llm()

    noi_dung_prompt = PROMPT.replace("{text_ocr}", text_ocr)
    message = HumanMessage(content=noi_dung_prompt)

    try:
        phan_hoi = llm.invoke([message])
    except Exception as e:
        raise LlmTextError(f"Lỗi gọi LLM2: {e}") from e

    noi_dung = _lam_sach_json(phan_hoi.content)

    try:
        du_lieu = json.loads(noi_dung)
    except json.JSONDecodeError as e:
        raise LlmTextError(f"LLM2 trả về không phải JSON hợp lệ: {noi_dung[:200]}") from e

    try:
        return ThongTinTrichXuat.model_validate(du_lieu)
    except Exception as e:
        raise LlmTextError(f"JSON không khớp schema: {e}") from e
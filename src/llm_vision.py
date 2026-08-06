import base64
import json

from langchain_core.messages import HumanMessage

from config import get_llm
from schemas import ThongTinTrichXuat

PROMPT = """Bạn là công cụ trích xuất dữ liệu từ ảnh chứng chỉ/bằng cấp.
Ảnh có thể là tiếng Việt, tiếng Anh hoặc lẫn cả hai.

Trả về ĐÚNG một object JSON với 4 trường sau, không kèm giải thích, không kèm
markdown:

{
  "ten_nguoi_nhan": "tên đầy đủ người được cấp, hoặc null",
  "ten_chung_chi": "tên CỤ THỂ của chứng chỉ/khóa học/danh hiệu, hoặc null",
  "ngay_nhan": "ngày cấp giữ nguyên như in trên chứng chỉ, hoặc null",
  "ngay_het_han": "ngày hết hạn, hoặc null nếu vô thời hạn/không ghi"
}

QUY TẮC ten_chung_chi:
- Lấy tên CỤ THỂ (vd "Certified Management Accountant", "Data Analyst Nanodegree").
- KHÔNG lấy cụm chung chung như "Certificate of Completion", "Chứng nhận hoàn thành".
- Nếu có cả hai, ưu tiên tên cụ thể.

QUY TẮC ngày: giữ nguyên như in trên ảnh, không đổi định dạng, không suy diễn.
Số hiệu chứng chỉ KHÔNG phải ngày.

Trường nào không thấy thì để null, không bịa. Chỉ trả JSON."""


class LlmVisionError(Exception):
    """Lỗi khi gọi Gemma hoặc parse kết quả."""


def _anh_thanh_data_url(anh_bytes: bytes) -> str:
    """Mã hóa ảnh thành data URL base64 để gửi qua API."""
    b64 = base64.b64encode(anh_bytes).decode("ascii")
    # Dùng png cho an toàn; API chấp nhận data URL cho hầu hết định dạng ảnh.
    return f"data:image/png;base64,{b64}"


def _lam_sach_json(text: str) -> str:
    """Bóc phần JSON ra khỏi text, phòng khi model kèm markdown ```json."""
    text = text.strip()
    if text.startswith("```"):
        # Bỏ ```json ... ``` nếu có
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def trich_tu_anh(anh_bytes: bytes, llm=None) -> ThongTinTrichXuat:
    """Gửi ảnh cho Gemma, trả về ThongTinTrichXuat.

    Cho phép truyền llm sẵn (để test hoặc tái dùng client). Nếu không thì tự tạo.
    """
    if llm is None:
        llm = get_llm()

    message = HumanMessage(content=[
        {"type": "text", "text": PROMPT},
        {"type": "image_url", "image_url": {"url": _anh_thanh_data_url(anh_bytes)}},
    ])

    try:
        phan_hoi = llm.invoke([message])
    except Exception as e:
        raise LlmVisionError(f"Lỗi gọi Gemma: {e}") from e

    noi_dung = _lam_sach_json(phan_hoi.content)

    try:
        du_lieu = json.loads(noi_dung)
    except json.JSONDecodeError as e:
        raise LlmVisionError(f"Gemma trả về không phải JSON hợp lệ: {noi_dung[:200]}") from e

    try:
        return ThongTinTrichXuat.model_validate(du_lieu)
    except Exception as e:
        raise LlmVisionError(f"JSON không khớp schema: {e}") from e
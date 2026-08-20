"""Gemma đọc ảnh chứng chỉ qua FPT (llm_vision) — đây là LLM1.

Nhận ảnh (bytes) -> trích 4 trường -> trả về ThongTinTrichXuat.

Cách ép JSON: yêu cầu rõ trong prompt + tự parse bằng Pydantic. Không dùng
with_structured_output vì không chắc endpoint FPT hỗ trợ json_schema mode;
cách này chạy được với mọi endpoint OpenAI-compatible.

Dùng trong pipeline:
    from llm_vision import trich_tu_anh
    thong_tin = trich_tu_anh(anh_bytes)
"""

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
  "ten_chung_chi_phu": "nếu tên khóa học in SONG NGỮ thì đây là phần ngôn ngữ còn lại, ngược lại null",
  "ngay_nhan": "ngày cấp giữ nguyên như in trên chứng chỉ, hoặc null",
  "ngay_het_han": "ngày hết hạn, hoặc null nếu vô thời hạn/không ghi"
}

QUY TẮC ten_chung_chi:
- Lấy tên CỤ THỂ (vd "Certified Management Accountant", "Data Analyst Nanodegree").
- KHÔNG lấy cụm chung chung như "Certificate of Completion", "Chứng nhận hoàn thành".
- Nếu có cả hai, ưu tiên tên cụ thể.

QUY TẮC SONG NGỮ:
- Nếu tên khóa học được in bằng CẢ tiếng Việt VÀ tiếng Anh (ví dụ "An toàn
  thông tin / Information Security", hoặc "Data Analysis (Phân tích dữ liệu)"),
  hãy TÁCH thành hai phần:
  - ten_chung_chi: một ngôn ngữ (ưu tiên tiếng Việt nếu có)
  - ten_chung_chi_phu: ngôn ngữ còn lại
- Nếu tên khóa học chỉ có MỘT ngôn ngữ thì ten_chung_chi_phu = null.
- KHÔNG tự dịch. Chỉ tách khi ảnh THẬT SỰ in cả hai ngôn ngữ.

QUY TẮC ngày: giữ nguyên như in trên ảnh, không đổi định dạng, không suy diễn.
Số hiệu chứng chỉ KHÔNG phải ngày.

Trường nào không thấy thì để null, không bịa. Chỉ trả JSON."""


class LlmVisionError(Exception):
    """Lỗi khi gọi Gemma hoặc parse kết quả."""


# Chữ ký byte đầu file -> kiểu MIME. Nhận dạng theo NỘI DUNG, không theo
# đuôi file hay giả định, vì file_utils.nen_cho_vua() có thể đã đổi ảnh sang
# JPEG để lọt giới hạn kích thước request.
_CHU_KY_MIME = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff",      "image/jpeg"),
    (b"BM",                "image/bmp"),
    (b"II*\x00",           "image/tiff"),
    (b"MM\x00*",           "image/tiff"),
)


def _anh_thanh_data_url(anh_bytes: bytes) -> str:
    """Mã hóa ảnh thành data URL base64 để gửi qua API.

    Kiểu MIME lấy từ CHỮ KÝ BYTE thật. Trước đây hàm khai cứng "image/png"
    cho mọi ảnh — chấp nhận được khi mọi ảnh đều là PNG, nhưng sai kể từ khi
    nen_cho_vua() đổi ảnh lớn sang JPEG. Khai sai MIME thì API có thể từ
    chối, hoặc tệ hơn là đọc sai ảnh mà không báo gì.
    """
    kieu = next((mime for chu_ky, mime in _CHU_KY_MIME
                 if anh_bytes.startswith(chu_ky)), "image/png")
    b64 = base64.b64encode(anh_bytes).decode("ascii")
    return f"data:{kieu};base64,{b64}"


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
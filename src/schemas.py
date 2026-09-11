"""Khuôn dữ liệu (schemas) bằng Pydantic.

1. ExtractedInfo — thứ LLM (Gemma và LLM2) phải trả về sau khi đọc ảnh; chung
   một khuôn cho cả hai để so sánh được với nhau.
2. InputInfo — thứ nhân viên nhập trên ELIS, dùng để đối chiếu.
3. ProcessResult — kết quả cuối của một chứng chỉ, để submit lại ELIS và log.

Pydantic ép LLM trả đúng cấu trúc: JSON thiếu trường hay sai kiểu là báo lỗi
ngay, thay vì để dữ liệu hỏng chạy sâu vào hệ thống.
"""

from enum import Enum

from pydantic import BaseModel, Field


class ExtractedInfo(BaseModel):
    """Thông tin LLM đọc được từ ảnh chứng chỉ.

    Mọi trường có thể là None: không tìm thấy trên ảnh thì để None, KHÔNG được
    bịa. expiry_date thường None vì nhiều chứng chỉ vô thời hạn.
    """

    recipient_name: str | None = Field(
        default=None, description="Tên đầy đủ của người được cấp chứng chỉ"
    )
    certificate_name: str | None = Field(
        default=None, description="Tên cụ thể của chứng chỉ/khóa học/danh hiệu"
    )
    # Ảnh in tên khóa học SONG NGỮ thì trường này giữ ngôn ngữ còn lại
    # (certificate_name giữ bản tiếng Việt). Ảnh một ngôn ngữ -> None.
    certificate_name_alt: str | None = Field(
        default=None, description="Tên khóa học ở ngôn ngữ thứ hai (nếu ảnh song ngữ)"
    )
    issue_date: str | None = Field(
        default=None, description="Ngày cấp, giữ nguyên như in trên chứng chỉ"
    )
    expiry_date: str | None = Field(
        default=None, description="Ngày hết hạn, None nếu vô thời hạn/không ghi"
    )
    # Tên NGƯỜI KÝ (giám đốc, hiệu trưởng...) — KHÔNG dùng để đối chiếu.
    #
    # Là CHỖ CHỨA: chứng chỉ hay in tên người nhận bằng chữ mảnh (có khi chỉ là
    # username) còn tên giám đốc ký ở cuối trang lại IN ĐẬM VIẾT HOA, nên model
    # dễ lấy nhầm tên đậm nhất. Bắt điền riêng trường này buộc nó PHÂN BIỆT hai
    # vai trò thay vì chọn bừa. Giá trị ở đây không dùng để so khớp.
    signatory_name: str | None = Field(
        default=None,
        description="Tên người KÝ chứng chỉ (không phải người nhận) — chỉ để tách bạch",
    )


class InputInfo(BaseModel):
    """Thông tin nhân viên nhập trên ELIS, dùng để đối chiếu với ảnh."""

    employee_name: str
    course_name: str
    # Mã NV bắt buộc: dùng để đối chiếu khi chứng chỉ in username/ID thay cho
    # tên thật (vd "hungnt97"). Tên trên ảnh khớp TÊN hoặc MÃ đều được duyệt.
    employee_code: str


class Verdict(str, Enum):
    """Trạng thái của một chứng chỉ.

    APPROVED/REJECTED là hai kết luận CUỐI: đã nộp về eLIS, chứng chỉ đóng.

    WAITING KHÔNG BAO GIỜ được nộp về eLIS — chỉ dùng để ghi log và in ra màn
    hình cho ca hỏng kỹ thuật (Azure sập, lỗi tải file...). Những ca đó cố ý để
    nguyên WAITING bên eLIS cho vòng sau xử lý lại, nên ghi REJECTED vào log là
    nói sai. Báo cáo không đụng tới giá trị này (nó lọc theo stage nghiệp vụ).
    """

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WAITING = "WAITING"


class ProcessResult(BaseModel):
    """Kết quả xử lý một chứng chỉ, để submit lại ELIS và ghi log."""

    employee_code: str | None = None
    verdict: Verdict

    # Trích được gì (để log lại, tiện đối chiếu khi cần rà soát).
    extracted: ExtractedInfo | None = None

    # Ghi rõ đi qua tầng nào và vì sao ra kết quả đó, phục vụ debug/kiểm toán.
    reason: str | None = None
    stage: str | None = Field(
        default=None,
        description="Kết luận ở đâu: 'llm1', 'llm1_vs_llm2', 'llm2', ...",
    )

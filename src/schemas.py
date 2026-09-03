"""Khuôn dữ liệu (schemas) bằng Pydantic.

Định nghĩa các cấu trúc dữ liệu chạy trong hệ thống. Có ba nhóm:

1. ExtractedInfo — thứ LLM (cả Gemma lẫn LLM2) phải trả về sau khi đọc ảnh.
   Dùng chung một khuôn cho cả hai để so sánh được với nhau.

2. InputInfo — thứ nhân viên nhập trên ELIS, dùng để đối chiếu.

3. ProcessResult — kết quả cuối của một chứng chỉ, để submit lại ELIS và ghi log.

Vì sao dùng Pydantic: nó ép LLM trả về đúng cấu trúc và tự kiểm tra kiểu dữ
liệu. Nếu LLM trả JSON thiếu trường hay sai kiểu, Pydantic báo lỗi ngay thay
vì để dữ liệu hỏng chạy sâu vào hệ thống.
"""

from enum import Enum

from pydantic import BaseModel, Field


class ExtractedInfo(BaseModel):
    """Thông tin LLM đọc được từ ảnh chứng chỉ.

    Mọi trường đều có thể là None: nếu LLM không tìm thấy trên ảnh thì để None,
    KHÔNG được bịa. expiry_date thường None vì nhiều chứng chỉ vô thời hạn.
    """

    recipient_name: str | None = Field(
        default=None, description="Tên đầy đủ của người được cấp chứng chỉ"
    )
    certificate_name: str | None = Field(
        default=None, description="Tên cụ thể của chứng chỉ/khóa học/danh hiệu"
    )
    # Khi ảnh in tên khóa học SONG NGỮ (cả tiếng Việt và tiếng Anh), trường này
    # giữ phần ngôn ngữ còn lại. Ví dụ ảnh ghi "An toàn thông tin / Information
    # Security" -> certificate_name="An toàn thông tin", certificate_name_alt="Information
    # Security". Nếu ảnh chỉ có một ngôn ngữ thì trường này là None.
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
    # Trường này tồn tại để làm CHỖ CHỨA cho một thứ gây nhầm lẫn có thật:
    # nhiều chứng chỉ in tên người nhận bằng chữ mảnh, nhỏ, ở giữa trang
    # (thậm chí chỉ là username như "tienpham89"), trong khi tên giám đốc ký ở
    # cuối trang lại IN ĐẬM VIẾT HOA. Model nhìn thấy chữ đậm nhất và tưởng đó
    # là người nhận — đã xảy ra thật: chứng chỉ của "tienpham89" bị đọc thành
    # "ĐỖ VĂN KHẮC" (Giám đốc sản xuất), và bị từ chối oan vì sai tên.
    #
    # Bắt model điền riêng tên người ký buộc nó phải PHÂN BIỆT hai vai trò,
    # thay vì chọn bừa một cái tên. Giá trị ở đây không được dùng để so khớp.
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

    WAITING KHÔNG BAO GIỜ được nộp về eLIS — nó chỉ dùng để ghi log và in ra
    màn hình cho ca hỏng kỹ thuật (Azure sập, lỗi tải file...). Những ca đó
    được cố ý để nguyên WAITING bên eLIS cho vòng sau xử lý lại, nên ghi
    REJECTED vào log là NÓI SAI: người vận hành đọc log tưởng chứng chỉ đã bị
    từ chối và đi giải thích với học viên, trong khi hệ thống đang hẹn thử
    lại. Báo cáo không đụng tới giá trị này (nó lọc theo stage nghiệp vụ).
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

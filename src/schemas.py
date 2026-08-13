"""Khuôn dữ liệu (schemas) bằng Pydantic.

Định nghĩa các cấu trúc dữ liệu chạy trong hệ thống. Có ba nhóm:

1. ThongTinTrichXuat — thứ LLM (cả Gemma lẫn LLM2) phải trả về sau khi đọc ảnh.
   Dùng chung một khuôn cho cả hai để so sánh được với nhau.

2. ThongTinNhap — thứ nhân viên nhập trên ELIS, dùng để đối chiếu.

3. KetQuaXuLy — kết quả cuối của một chứng chỉ, để submit lại ELIS và ghi log.

Vì sao dùng Pydantic: nó ép LLM trả về đúng cấu trúc và tự kiểm tra kiểu dữ
liệu. Nếu LLM trả JSON thiếu trường hay sai kiểu, Pydantic báo lỗi ngay thay
vì để dữ liệu hỏng chạy sâu vào hệ thống.
"""

from enum import Enum

from pydantic import BaseModel, Field


class ThongTinTrichXuat(BaseModel):
    """Thông tin LLM đọc được từ ảnh chứng chỉ.

    Mọi trường đều có thể là None: nếu LLM không tìm thấy trên ảnh thì để None,
    KHÔNG được bịa. ngay_het_han thường None vì nhiều chứng chỉ vô thời hạn.
    """

    ten_nguoi_nhan: str | None = Field(
        default=None, description="Tên đầy đủ của người được cấp chứng chỉ"
    )
    ten_chung_chi: str | None = Field(
        default=None, description="Tên cụ thể của chứng chỉ/khóa học/danh hiệu"
    )
    # Khi ảnh in tên khóa học SONG NGỮ (cả tiếng Việt và tiếng Anh), trường này
    # giữ phần ngôn ngữ còn lại. Ví dụ ảnh ghi "An toàn thông tin / Information
    # Security" -> ten_chung_chi="An toàn thông tin", ten_chung_chi_phu="Information
    # Security". Nếu ảnh chỉ có một ngôn ngữ thì trường này là None.
    ten_chung_chi_phu: str | None = Field(
        default=None, description="Tên khóa học ở ngôn ngữ thứ hai (nếu ảnh song ngữ)"
    )
    ngay_nhan: str | None = Field(
        default=None, description="Ngày cấp, giữ nguyên như in trên chứng chỉ"
    )
    ngay_het_han: str | None = Field(
        default=None, description="Ngày hết hạn, None nếu vô thời hạn/không ghi"
    )


class ThongTinNhap(BaseModel):
    """Thông tin nhân viên nhập trên ELIS, dùng để đối chiếu với ảnh."""

    ten_nhan_vien: str
    ten_khoa_hoc: str
    # Mã NV bắt buộc: dùng để đối chiếu khi chứng chỉ in username/ID thay cho
    # tên thật (vd "hungnt97"). Tên trên ảnh khớp TÊN hoặc MÃ đều được duyệt.
    ma_nhan_vien: str


class KetQua(str, Enum):
    """Hai trạng thái kết quả cuối của một chứng chỉ."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class KetQuaXuLy(BaseModel):
    """Kết quả xử lý một chứng chỉ, để submit lại ELIS và ghi log."""

    ma_nhan_vien: str | None = None
    ket_qua: KetQua

    # Trích được gì (để log lại, tiện đối chiếu khi cần rà soát).
    trich_xuat: ThongTinTrichXuat | None = None

    # Ghi rõ đi qua tầng nào và vì sao ra kết quả đó, phục vụ debug/kiểm toán.
    ly_do: str | None = None
    tang_xu_ly: str | None = Field(
        default=None,
        description="Kết luận ở đâu: 'llm1', 'llm1_vs_llm2', 'llm2', ...",
    )
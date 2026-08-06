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
    # Mã NV không đối chiếu với ảnh (thường không in trên chứng chỉ), chỉ để
    # định danh khi ghi log và submit lại ELIS.
    ma_nhan_vien: str | None = None


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
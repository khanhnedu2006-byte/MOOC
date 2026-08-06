from rapidfuzz import fuzz

from process_data import chuan_hoa

# Ngưỡng khớp mờ khi so với input người nhập (0-100).
# VD:90 = phải giống nhau 90% mới tính khớp.
NGUONG_TEN = 97
NGUONG_KHOA_HOC = 95


def diem_giong(a: str | None, b: str | None) -> float:
    """Trả về điểm giống nhau 0-100 giữa hai chuỗi, sau khi đã chuẩn hóa.
    Dùng fuzz.ratio (so ký tự).
    """
    return fuzz.ratio(chuan_hoa(a), chuan_hoa(b))


def khop_voi_input(gia_tri_llm: str | None, gia_tri_nhap: str | None, nguong: float) -> bool:
    a, b = chuan_hoa(gia_tri_llm), chuan_hoa(gia_tri_nhap)
    if not a or not b:
        return False
    return fuzz.ratio(a, b) >= nguong


def hai_ket_qua_giong_nhau(gia_tri_1: str | None, gia_tri_2: str | None) -> bool:
    "So kết quả LLM1 với LLM2. So CHẶT: bằng nhau tuyệt đối sau chuẩn hóa."
    a, b = chuan_hoa(gia_tri_1), chuan_hoa(gia_tri_2)
    if not a or not b:
        return False
    return a == b


def khop_ten(gia_tri_llm, gia_tri_nhap):
    return khop_voi_input(gia_tri_llm, gia_tri_nhap, NGUONG_TEN)


def khop_khoa_hoc(gia_tri_llm, gia_tri_nhap):
    return khop_voi_input(gia_tri_llm, gia_tri_nhap, NGUONG_KHOA_HOC)
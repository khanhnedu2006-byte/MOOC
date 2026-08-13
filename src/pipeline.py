"""Điều phối xử lý MỘT chứng chỉ (pipeline).

Nối các module theo đúng sơ đồ ba lần so:

  1. Gemma (LLM1) đọc ảnh -> so tên + khóa học với input người nhập.
     Cả hai khớp -> APPROVED (dừng, không tốn Azure).

  2. Không khớp -> Azure OCR + LLM2 đọc lại từ ảnh.

  3. So LLM1 với LLM2 (chặt tuyệt đối):
     Giống nhau -> REJECTED (hai máy đồng thuận: ảnh khác input).

  4. Khác nhau -> so LLM2 với input:
     Khớp -> APPROVED, không khớp -> REJECTED.

pipeline chỉ xử lý MỘT chứng chỉ. Việc lặp qua nhiều cái nằm ở run.py.
"""

import logging

import compare
import process_data
from config import settings
from schemas import KetQua, KetQuaXuLy, ThongTinNhap, ThongTinTrichXuat

logger = logging.getLogger(__name__)


def _khop_ca_hai(trich: ThongTinTrichXuat, nhap: ThongTinNhap) -> bool:
    """True khi tên VÀ khóa học khớp với input.

    - Tên: khớp tên nhân viên hoặc mã nhân viên.
    - Khóa học: khớp (hỗ trợ song ngữ, khớp một trong hai ngôn ngữ là đủ).
    Thời gian kiểm riêng (_thoi_gian_hop_le) để ghi được lý do rõ ràng.
    """
    return (
        compare.khop_ten_hoac_ma(
            trich.ten_nguoi_nhan, nhap.ten_nhan_vien, nhap.ma_nhan_vien
        )
        and compare.khop_khoa_hoc_song_ngu(
            trich.ten_chung_chi, trich.ten_chung_chi_phu, nhap.ten_khoa_hoc
        )
    )


def _thoi_gian_hop_le(trich: ThongTinTrichXuat) -> bool:
    """Ngày hoàn thành trên chứng chỉ có nằm trong khoảng công ty quy định không."""
    return process_data.ngay_hop_le(
        trich.ngay_nhan,
        settings.thoi_gian_hop_le_tu,
        settings.thoi_gian_hop_le_den,
    )


def _hai_llm_giong_nhau(t1: ThongTinTrichXuat, t2: ThongTinTrichXuat) -> bool:
    """True khi LLM1 và LLM2 trích ra cùng tên VÀ cùng khóa học (so chặt)."""
    return (
        compare.hai_ket_qua_giong_nhau(t1.ten_nguoi_nhan, t2.ten_nguoi_nhan)
        and compare.hai_ket_qua_giong_nhau(t1.ten_chung_chi, t2.ten_chung_chi)
    )


def _khoa_hoc_tren_anh(trich: ThongTinTrichXuat) -> str:
    """Gộp tên khóa học chính + phụ (song ngữ) để hiển thị trong lý do."""
    if trich.ten_chung_chi_phu:
        return f"{trich.ten_chung_chi} / {trich.ten_chung_chi_phu}"
    return f"{trich.ten_chung_chi}"


def _ly_do_khong_khop(trich: ThongTinTrichXuat, nhap: ThongTinNhap, tang: str) -> str:
    """Tạo lý do chi tiết: trường nào sai, giá trị trên ảnh vs nhập, ở tầng nào.

    tang: 'LLM1' hoặc 'LLM2' — nêu rõ kết quả này đọc từ đâu.
    Liệt kê MỌI trường không khớp (có thể vừa sai tên vừa sai khóa vừa sai giờ).
    """
    ten_khop = compare.khop_ten_hoac_ma(
        trich.ten_nguoi_nhan, nhap.ten_nhan_vien, nhap.ma_nhan_vien
    )
    khoa_khop = compare.khop_khoa_hoc_song_ngu(
        trich.ten_chung_chi, trich.ten_chung_chi_phu, nhap.ten_khoa_hoc
    )
    gio_hop_le = _thoi_gian_hop_le(trich)

    loi = []
    if not ten_khop:
        loi.append(
            f"Tên không khớp — trên ảnh ({tang}): \"{trich.ten_nguoi_nhan}\", "
            f"nhập: \"{nhap.ten_nhan_vien}\" (mã: \"{nhap.ma_nhan_vien}\")"
        )
    if not khoa_khop:
        loi.append(
            f"Khóa học không khớp — trên ảnh ({tang}): \"{_khoa_hoc_tren_anh(trich)}\", "
            f"nhập: \"{nhap.ten_khoa_hoc}\""
        )
    if not gio_hop_le:
        loi.append(
            f"Thời gian không hợp lệ — trên ảnh ({tang}): \"{trich.ngay_nhan}\", "
            f"ngoài khoảng {settings.thoi_gian_hop_le_tu}..{settings.thoi_gian_hop_le_den}"
        )
    return "; ".join(loi) if loi else "Không khớp"


def xu_ly(
    anh_list: list[bytes],
    nhap: ThongTinNhap,
    trich_tu_anh,   # hàm llm_vision.trich_tu_anh
    ocr_nhieu_anh,  # hàm ocr_azure.ocr_nhieu_anh
    trich_tu_text,  # hàm llm_text.trich_tu_text
    azure_client,
) -> KetQuaXuLy:
    """Xử lý một chứng chỉ, trả về KetQuaXuLy (APPROVED / REJECTED).

    Các hàm gọi API được truyền vào (dependency injection) để test được mà
    không cần gọi API thật, và để pipeline không phụ thuộc cứng vào module nào.
    """
    def ket_qua(kq, ly_do, tang, trich=None):
        return KetQuaXuLy(
            ma_nhan_vien=nhap.ma_nhan_vien,
            ket_qua=kq,
            trich_xuat=trich,
            ly_do=ly_do,
            tang_xu_ly=tang,
        )

    # ===== Tầng 1: Gemma đọc ảnh =====
    # Chứng chỉ có thể nhiều trang (PDF); dùng trang đầu để đọc, vì thông tin
    # chính (tên, khóa học) thường nằm ở trang đầu. Nếu cần đọc mọi trang thì
    # mở rộng sau.
    try:
        llm1 = trich_tu_anh(anh_list[0])
    except Exception as e:
        logger.warning("LLM1 lỗi: %s", e)
        return ket_qua(KetQua.REJECTED, f"LLM1 lỗi: {e}", "llm1_loi")

    if _khop_ca_hai(llm1, nhap):
        if not _thoi_gian_hop_le(llm1):
            return ket_qua(KetQua.REJECTED,
                           _ly_do_khong_khop(llm1, nhap, "LLM1"), "llm1", llm1)
        return ket_qua(KetQua.APPROVED, "Tên, khóa học và thời gian đều khớp (LLM1)", "llm1", llm1)

    # ===== Tầng 2: Azure OCR + LLM2 =====
    try:
        text_ocr = ocr_nhieu_anh(azure_client, anh_list)
        llm2 = trich_tu_text(text_ocr)
    except Exception as e:
        logger.warning("Tầng 2 lỗi: %s", e)
        # Tầng 2 hỏng (ảnh mờ Azure không đọc được...): không khẳng định được,
        # theo luồng 2 nhãn thì về REJECTED để người kiểm tra khi cần.
        return ket_qua(KetQua.REJECTED, f"Tầng 2 lỗi: {e}", "tang2_loi", llm1)

    # ===== So LLM1 với LLM2 (chặt) =====
    if _hai_llm_giong_nhau(llm1, llm2):
        # Hai máy đọc ra GIỐNG nhau -> tin tưởng kết quả đó, và nó khác input.
        ly_do = (
            f"LLM1 và LLM2 đồng thuận (cùng đọc ra tên \"{llm2.ten_nguoi_nhan}\", "
            f"khóa học \"{_khoa_hoc_tren_anh(llm2)}\", ngày \"{llm2.ngay_nhan}\"). "
            f"Khác với thông tin nhập: " + _ly_do_khong_khop(llm2, nhap, "đồng thuận")
        )
        return ket_qua(KetQua.REJECTED, ly_do, "llm1_vs_llm2", llm2)

    # ===== So LLM2 với input =====
    if _khop_ca_hai(llm2, nhap):
        if not _thoi_gian_hop_le(llm2):
            return ket_qua(KetQua.REJECTED,
                           _ly_do_khong_khop(llm2, nhap, "LLM2"), "llm2", llm2)
        return ket_qua(KetQua.APPROVED,
                       "Khớp ở LLM2 (Azure đọc lại, LLM1 đọc sai)", "llm2", llm2)

    return ket_qua(KetQua.REJECTED, _ly_do_khong_khop(llm2, nhap, "LLM2"), "llm2", llm2)
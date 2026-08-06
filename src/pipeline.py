"""Điều phối xử lý 1 chứng chỉ.
  1. Gemma (LLM1) đọc ảnh -> so tên + khóa học với input người nhập.
     Cả hai khớp -> APPROVED (dừng, không tốn Azure).

  2. Không khớp -> Azure OCR + LLM2 đọc lại từ ảnh.

  3. So LLM1 với LLM2 (chặt tuyệt đối):
     Giống nhau -> REJECTED (hai máy đồng thuận: ảnh khác input).

  4. Khác nhau -> so LLM2 với input:
     Khớp -> APPROVED, không khớp -> REJECTED.
"""

import logging

import compare
from schemas import KetQua, KetQuaXuLy, ThongTinNhap, ThongTinTrichXuat

logger = logging.getLogger(__name__)


def _khop_ca_hai(trich: ThongTinTrichXuat, nhap: ThongTinNhap) -> bool:
    return (
        compare.khop_ten(trich.ten_nguoi_nhan, nhap.ten_nhan_vien)
        and compare.khop_khoa_hoc(trich.ten_chung_chi, nhap.ten_khoa_hoc)
    )


def _hai_llm_giong_nhau(t1: ThongTinTrichXuat, t2: ThongTinTrichXuat) -> bool:
    return (
        compare.hai_ket_qua_giong_nhau(t1.ten_nguoi_nhan, t2.ten_nguoi_nhan)
        and compare.hai_ket_qua_giong_nhau(t1.ten_chung_chi, t2.ten_chung_chi)
    )


def xu_ly(
    anh_list: list[bytes],
    nhap: ThongTinNhap,
    trich_tu_anh,   # hàm llm_vision.trich_tu_anh
    ocr_nhieu_anh,  # hàm ocr_azure.ocr_nhieu_anh
    trich_tu_text,  # hàm llm_text.trich_tu_text
    azure_client,
) -> KetQuaXuLy:
    "Xử lý một chứng chỉ, trả về KetQuaXuLy (APPROVED / REJECTED)."

    def ket_qua(kq, ly_do, tang, trich=None):
        return KetQuaXuLy(
            ma_nhan_vien=nhap.ma_nhan_vien,
            ket_qua=kq,
            trich_xuat=trich,
            ly_do=ly_do,
            tang_xu_ly=tang,
        )

    # ===== Tầng 1: Gemma đọc ảnh =====
    try:
        llm1 = trich_tu_anh(anh_list[0])
    except Exception as e:
        logger.warning("LLM1 lỗi: %s", e)
        return ket_qua(KetQua.REJECTED, f"LLM1 lỗi: {e}", "llm1_loi")

    if _khop_ca_hai(llm1, nhap):
        return ket_qua(KetQua.APPROVED, "Khớp ngay ở LLM1", "llm1", llm1)

    # ===== Tầng 2: Azure OCR + LLM2 =====
    try:
        text_ocr = ocr_nhieu_anh(azure_client, anh_list)
        llm2 = trich_tu_text(text_ocr)
    except Exception as e:
        logger.warning("Tầng 2 lỗi: %s", e)
        return ket_qua(KetQua.REJECTED, f"Tầng 2 lỗi: {e}", "tang2_loi", llm1)

    # ===== So LLM1 với LLM2 (chặt) =====
    if _hai_llm_giong_nhau(llm1, llm2):
        return ket_qua(KetQua.REJECTED, "LLM1 và LLM2 đồng thuận, khác input", "llm1_vs_llm2", llm2)

    # ===== So LLM2 với input =====
    if _khop_ca_hai(llm2, nhap):
        return ket_qua(KetQua.APPROVED, "Khớp ở LLM2 (LLM1 đọc sai)", "llm2", llm2)

    return ket_qua(KetQua.REJECTED, "LLM2 cũng không khớp input", "llm2", llm2)
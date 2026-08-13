"""Test cho process_data: chuẩn hóa chuỗi và xử lý ngày tháng.

Chạy: pytest tests/test_process_data.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from process_data import chuan_hoa, parse_ngay, ngay_hop_le


# ===== chuan_hoa =====

class TestChuanHoa:
    def test_bo_dau_tieng_viet(self):
        assert chuan_hoa("Nguyễn Văn A") == "nguyen van a"

    def test_hoa_thuong(self):
        assert chuan_hoa("NGUYEN VAN A") == "nguyen van a"

    def test_khong_dau_va_co_dau_ra_cung_ket_qua(self):
        assert chuan_hoa("NGUYEN VAN A") == chuan_hoa("Nguyễn Văn A")

    def test_gop_khoang_trang_thua(self):
        assert chuan_hoa("  Nguyễn   Văn  A  ") == "nguyen van a"

    def test_none_tra_rong(self):
        assert chuan_hoa(None) == ""

    def test_chuoi_rong(self):
        assert chuan_hoa("") == ""

    def test_chi_khoang_trang(self):
        assert chuan_hoa("   ") == ""

    def test_nfc_nfd_giong_nhau(self):
        import unicodedata
        nfc = "Nguyễn"
        nfd = unicodedata.normalize("NFD", "Nguyễn")
        # Khác byte nhưng sau chuẩn hóa phải giống nhau
        assert nfc.encode() != nfd.encode()
        assert chuan_hoa(nfc) == chuan_hoa(nfd)

    # Bỏ dấu câu (ca thật gặp khi test dữ liệu)
    def test_bo_dau_gach_thua_khoang_trang(self):
        assert chuan_hoa("HIỆU QUẢ - TĂNG") == chuan_hoa("HIỆU QUẢ -TĂNG")

    def test_bo_dau_ngoac_kep(self):
        assert chuan_hoa('ky nang "nhan feedback"') == chuan_hoa("ky nang nhan feedback")

    def test_bo_ngoac_don(self):
        assert chuan_hoa("Data Analysis (Phân tích)") == "data analysis phan tich"

    def test_van_phan_biet_noi_dung_khac(self):
        # Bỏ dấu câu KHÔNG được làm hai nội dung khác nhau thành giống
        assert chuan_hoa("Python cơ bản") != chuan_hoa("Python nâng cao")


# ===== parse_ngay =====

class TestParseNgay:
    def test_ngay_kieu_viet_nam(self):
        # 05/03 = 5 tháng 3, KHÔNG phải 3 tháng 5
        d = parse_ngay("05/03/2026")
        assert d.day == 5 and d.month == 3

    def test_ngay_tieng_anh(self):
        d = parse_ngay("March 15, 2026")
        assert d.year == 2026 and d.month == 3 and d.day == 15

    def test_ngay_tieng_viet(self):
        d = parse_ngay("ngày 15 tháng 3 năm 2026")
        assert d.month == 3 and d.day == 15

    def test_chi_co_nam_tra_none(self):
        # Thiếu ngày/tháng -> không đủ thông tin -> None
        assert parse_ngay("2026") is None

    def test_chu_la_tra_none(self):
        assert parse_ngay("abcxyz") is None

    def test_none_tra_none(self):
        assert parse_ngay(None) is None

    def test_rong_tra_none(self):
        assert parse_ngay("") is None


# ===== ngay_hop_le =====

class TestNgayHopLe:
    TU = "2026-01-01"
    DEN = "2026-09-30"

    def test_trong_khoang(self):
        assert ngay_hop_le("15/03/2026", self.TU, self.DEN) is True

    def test_ngay_dau_khoang(self):
        assert ngay_hop_le("01/01/2026", self.TU, self.DEN) is True

    def test_ngay_cuoi_khoang(self):
        assert ngay_hop_le("30/09/2026", self.TU, self.DEN) is True

    def test_ngoai_khoang_sau(self):
        assert ngay_hop_le("01/10/2026", self.TU, self.DEN) is False

    def test_ngoai_khoang_nam_truoc(self):
        assert ngay_hop_le("31/12/2025", self.TU, self.DEN) is False

    def test_khong_doc_duoc_ngay(self):
        assert ngay_hop_le("abcxyz", self.TU, self.DEN) is False

    def test_chi_co_nam(self):
        assert ngay_hop_le("2026", self.TU, self.DEN) is False

    def test_none(self):
        assert ngay_hop_le(None, self.TU, self.DEN) is False
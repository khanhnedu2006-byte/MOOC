"""Test cho compare: các luật khớp tên, khóa học, mã, và so LLM1-LLM2.

Chạy: pytest tests/test_compare.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from compare import (
    khop_ten,
    khop_khoa_hoc,
    khop_ma,
    khop_ten_hoac_ma,
    khop_khoa_hoc_song_ngu,
    hai_ket_qua_giong_nhau,
    giong_tap_hop_tu,
)


# ===== khop_ten (so tập hợp từ, bỏ qua thứ tự) =====

class TestKhopTen:
    def test_khac_dau(self):
        assert khop_ten("NGUYEN VAN A", "Nguyễn Văn A") is True

    def test_dao_thu_tu(self):
        # Chứng chỉ nước ngoài đảo surname/given name
        assert khop_ten("A NGUYEN VAN", "Nguyen Van A") is True

    def test_dao_thu_tu_kieu_khac(self):
        assert khop_ten("VAN A NGUYEN", "Nguyen Van A") is True

    def test_khac_ho(self):
        assert khop_ten("Tran Van A", "Nguyen Van A") is False

    def test_khac_ten(self):
        assert khop_ten("Nguyen Van B", "Nguyen Van A") is False

    def test_thieu_tu(self):
        assert khop_ten("Nguyen Van", "Nguyen Van A") is False

    def test_ocr_lech_1_chu_khong_khop(self):
        # Đã chọn so chặt: OCR đọc lệch 1 ký tự -> không khớp (chấp nhận)
        assert khop_ten("Nguyeen Van A", "Nguyen Van A") is False

    def test_rong_khong_khop(self):
        assert khop_ten(None, "Nguyen Van A") is False
        assert khop_ten("", "") is False


# ===== khop_khoa_hoc =====

class TestKhopKhoaHoc:
    def test_khac_dau_hoa_thuong(self):
        assert khop_khoa_hoc("Data Analyst Nanodegree", "data analyst nanodegree") is True

    def test_dao_thu_tu(self):
        assert khop_khoa_hoc("Python Co Ban", "co ban python") is True

    def test_co_ban_vs_nang_cao(self):
        assert khop_khoa_hoc("Python cơ bản", "Python nâng cao") is False

    # Ca thật: nhiễu dấu câu
    def test_dau_gach_thua_khoang_trang(self):
        assert khop_khoa_hoc("HIỆU QUẢ - TĂNG TỶ LỆ", "HIỆU QUẢ -TĂNG TỶ LỆ") is True

    def test_dau_ngoac_kep(self):
        assert khop_khoa_hoc('ky nang "nhan feedback"', "ky nang nhan feedback") is True


# ===== khop_ma (chặt tuyệt đối, cụm từ liên tiếp) =====

class TestKhopMa:
    def test_ma_don(self):
        assert khop_ma("hungnt97", "hungnt97") is True

    def test_ma_lap_doi(self):
        # Chứng chỉ in ID hai lần: "hungnt97 hungnt97"
        assert khop_ma("hungnt97 hungnt97", "hungnt97") is True

    def test_ma_lan_trong_chu(self):
        assert khop_ma("Certificate hungnt97 completion", "hungnt97") is True

    def test_ma_khac_1_ky_tu(self):
        # 97 vs 98 = người khác, phải chặt tuyệt đối
        assert khop_ma("hungnt97", "hungnt98") is False

    def test_ma_nhieu_tu(self):
        # Mã có dấu -> chuẩn hóa thành nhiều từ, khớp cụm liên tiếp
        assert khop_ma("nv 001 completion", "nv-001") is True

    def test_ma_nhieu_tu_khong_lien_tiep(self):
        assert khop_ma("nv abc 001", "nv-001") is False

    def test_ma_khong_khop_mot_phan(self):
        # "nv" không được khớp nhầm với "nvidia"
        assert khop_ma("nvidia card", "nv") is False


# ===== khop_ten_hoac_ma =====

class TestKhopTenHoacMa:
    def test_khop_qua_ten(self):
        assert khop_ten_hoac_ma("A NGUYEN VAN", "Nguyen Van A", "hungnt97") is True

    def test_khop_qua_ma(self):
        # Ảnh in ID, không phải tên thật
        assert khop_ten_hoac_ma("hungnt97 hungnt97", "Nguyen Van A", "hungnt97") is True

    def test_khong_khop_ca_hai(self):
        assert khop_ten_hoac_ma("Tran Thi B", "Nguyen Van A", "hungnt97") is False


# ===== khop_khoa_hoc_song_ngu =====

class TestKhopKhoaHocSongNgu:
    CHINH = "An toàn thông tin"
    PHU = "Information Security"

    def test_nhap_tieng_viet_khop_phan_chinh(self):
        assert khop_khoa_hoc_song_ngu(self.CHINH, self.PHU, "An toàn thông tin") is True

    def test_nhap_tieng_anh_khop_phan_phu(self):
        assert khop_khoa_hoc_song_ngu(self.CHINH, self.PHU, "Information Security") is True

    def test_nhap_khong_dau(self):
        assert khop_khoa_hoc_song_ngu(self.CHINH, self.PHU, "an toan thong tin") is True

    def test_nhap_khoa_khac(self):
        assert khop_khoa_hoc_song_ngu(self.CHINH, self.PHU, "Marketing cơ bản") is False

    def test_mot_ngon_ngu_phu_none(self):
        assert khop_khoa_hoc_song_ngu("Data Analysis", None, "Data Analysis") is True


# ===== hai_ket_qua_giong_nhau (LLM1 vs LLM2, so chặt) =====

class TestHaiKetQuaGiongNhau:
    def test_cung_noi_dung_khac_dinh_dang(self):
        assert hai_ket_qua_giong_nhau("Nguyễn Văn A", "NGUYEN VAN A") is True

    def test_khac_ten(self):
        assert hai_ket_qua_giong_nhau("Nguyễn Văn A", "Nguyễn Văn B") is False

    def test_ca_hai_none(self):
        # Cả hai không đọc được -> không phải "đồng thuận"
        assert hai_ket_qua_giong_nhau(None, None) is False

    def test_mot_ben_rong(self):
        assert hai_ket_qua_giong_nhau("", "Nguyễn Văn A") is False
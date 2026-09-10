"""Test cho compare: các luật khớp tên, khóa học, mã, và so LLM1-LLM2.

Chạy: pytest tests/test_compare.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from compare import (
    match_name,
    match_course,
    match_code,
    match_name_or_code,
    match_course_bilingual,
    identical_after_normalize,
)


# ===== match_name (so tập hợp từ, bỏ qua thứ tự) =====

class TestMatchName:
    def test_different_diacritics(self):
        assert match_name("NGUYEN VAN A", "Nguyễn Văn A") is True

    def test_reversed_order(self):
        # Chứng chỉ nước ngoài đảo surname/given name
        assert match_name("A NGUYEN VAN", "Nguyen Van A") is True

    def test_reversed_order_other_style(self):
        assert match_name("VAN A NGUYEN", "Nguyen Van A") is True

    def test_different_surname(self):
        assert match_name("Tran Van A", "Nguyen Van A") is False

    def test_different_name(self):
        assert match_name("Nguyen Van B", "Nguyen Van A") is False

    def test_missing_word(self):
        assert match_name("Nguyen Van", "Nguyen Van A") is False

    def test_one_word_ocr_drift_no_match(self):
        # Đã chọn so chặt: OCR đọc lệch 1 ký tự -> không khớp (chấp nhận)
        assert match_name("Nguyeen Van A", "Nguyen Van A") is False

    def test_empty_no_match(self):
        assert match_name(None, "Nguyen Van A") is False
        assert match_name("", "") is False


# ===== match_course =====

class TestMatchCourse:
    def test_different_diacritics_and_case(self):
        assert match_course("Data Analyst Nanodegree", "data analyst nanodegree") is True

    def test_reversed_order(self):
        assert match_course("Python Co Ban", "co ban python") is True

    def test_basic_vs_advanced(self):
        assert match_course("Python cơ bản", "Python nâng cao") is False

    # Ca thật: nhiễu dấu câu
    def test_dash_becomes_space(self):
        assert match_course("HIỆU QUẢ - TĂNG TỶ LỆ", "HIỆU QUẢ -TĂNG TỶ LỆ") is True

    def test_double_quotes(self):
        assert match_course('ky nang "nhan feedback"', "ky nang nhan feedback") is True


# ===== match_code (chặt tuyệt đối, cụm từ liên tiếp) =====

class TestMatchCode:
    def test_single_word_code(self):
        assert match_code("hungnt97", "hungnt97") is True

    def test_repeated_code(self):
        # Chứng chỉ in ID hai lần: "hungnt97 hungnt97"
        assert match_code("hungnt97 hungnt97", "hungnt97") is True

    def test_code_inside_word(self):
        assert match_code("Certificate hungnt97 completion", "hungnt97") is True

    def test_code_off_by_one_char(self):
        # 97 vs 98 = người khác, phải chặt tuyệt đối
        assert match_code("hungnt97", "hungnt98") is False

    def test_multi_word_code(self):
        # Mã có dấu -> chuẩn hóa thành nhiều từ, khớp cụm liên tiếp
        assert match_code("nv 001 completion", "nv-001") is True

    def test_multi_word_code_non_adjacent(self):
        assert match_code("nv abc 001", "nv-001") is False

    def test_partial_code_no_match(self):
        # "nv" không được khớp nhầm với "nvidia"
        assert match_code("nvidia card", "nv") is False


# ===== match_name_or_code =====

class TestMatchNameOrCode:
    def test_match_via_name(self):
        assert match_name_or_code("A NGUYEN VAN", "Nguyen Van A", "hungnt97") is True

    def test_match_via_code(self):
        # Ảnh in ID, không phải tên thật
        assert match_name_or_code("hungnt97 hungnt97", "Nguyen Van A", "hungnt97") is True

    def test_neither_matches(self):
        assert match_name_or_code("Tran Thi B", "Nguyen Van A", "hungnt97") is False


# ===== match_course_bilingual =====

class TestMatchCourseBilingual:
    PRIMARY = "An toàn thông tin"
    SECONDARY = "Information Security"

    def test_vietnamese_input_matches_primary(self):
        assert match_course_bilingual(self.PRIMARY, self.SECONDARY, "An toàn thông tin") is True

    def test_english_input_matches_secondary(self):
        assert match_course_bilingual(self.PRIMARY, self.SECONDARY, "Information Security") is True

    def test_input_without_diacritics(self):
        assert match_course_bilingual(self.PRIMARY, self.SECONDARY, "an toan thong tin") is True

    def test_different_course_input(self):
        assert match_course_bilingual(self.PRIMARY, self.SECONDARY, "Marketing cơ bản") is False

    def test_single_language_secondary_none(self):
        assert match_course_bilingual("Data Analysis", None, "Data Analysis") is True


# ===== identical_after_normalize (LLM1 vs LLM2, so chặt) =====

class TestIdenticalAfterNormalize:
    def test_same_content_different_format(self):
        assert identical_after_normalize("Nguyễn Văn A", "NGUYEN VAN A") is True

    def test_different_name(self):
        assert identical_after_normalize("Nguyễn Văn A", "Nguyễn Văn B") is False

    def test_both_none(self):
        # Cả hai không đọc được -> không phải "đồng thuận"
        assert identical_after_normalize(None, None) is False

    def test_one_side_empty(self):
        assert identical_after_normalize("", "Nguyễn Văn A") is False


# ===== Tên khóa song ngữ: phải thử CẢ BA dạng =====
#
# eLIS lưu tên khóa ở ba dạng khác nhau tùy khóa: chỉ tiếng Việt, chỉ tiếng
# Anh, hoặc CẢ HAI nối lại. Chứng chỉ thì in song ngữ và LLM tách làm hai nửa.
# Thiếu phép so bản GHÉP là từ chối oan đúng những chứng chỉ đọc đúng nhất.

def test_song_ngu_khop_khi_eLIS_luu_TIENG_VIET():
    assert match_course_bilingual(
        "Python cơ bản", "Python fundamentals", "Python cơ bản", "loose")


def test_song_ngu_khop_khi_eLIS_luu_TIENG_ANH():
    assert match_course_bilingual(
        "Python cơ bản", "Python fundamentals", "Python fundamentals", "loose")


def test_song_ngu_khop_khi_eLIS_luu_CA_HAI():
    """Ca 096_tienlx6 — đã từ chối oan trên dữ liệu thật vì thiếu phép so này.

    Chứng chỉ in đúng nguyên chuỗi eLIS lưu, model tách làm hai theo đúng yêu
    cầu của prompt, rồi không nửa nào bằng chuỗi eLIS nữa.
    """
    assert match_course_bilingual(
        "BỘ QUY ĐỊNH CHÍNH SÁCH CẦN BIẾT FPT",
        "FPT KEY REGULATIONS AND POLICIES (ENGLISH VERSION)",
        "Bộ Quy định chính sách cần biết FPT - FPT Key Regulations and "
        "Policies (English version)", "loose")


def test_song_ngu_khop_ca_hai_o_che_do_strict():
    assert match_course_bilingual(
        "Python cơ bản", "Python fundamentals",
        "Python cơ bản - Python fundamentals", "strict")


def test_ghep_hai_nua_KHONG_lam_khop_khoa_khac_han():
    """Đối chứng: nới thêm đường khớp không được biến khóa khác thành khớp."""
    assert not match_course_bilingual(
        "Python cơ bản", "Python fundamentals", "Java nâng cao", "loose")


def test_ghep_hai_nua_KHONG_lam_khop_khi_eLIS_dai_hon():
    """eLIS có từ mà ảnh không có -> vẫn phải trượt, kể cả sau khi ghép."""
    assert not match_course_bilingual(
        "An toàn thông tin", "Information Security",
        "An toàn thông tin nâng cao", "loose")


def test_song_ngu_noi_bang_DAU_GACH_DUOI():
    """Ca thật gặp trên bản demo 10/09: eLIS lưu "AI CƠ BẢN_AI FOR EVERYONE"
    — hai ngôn ngữ nối bằng dấu GẠCH DƯỚI, không phải " - " như mọi test cũ.

    Đáng test riêng vì dấu phân cách ở đây do normalize() lo (nó biến mọi ký
    tự không phải chữ/số thành khoảng trắng). Ai đó siết normalize lại — chẳng
    hạn giữ "_" cho tên file — là ca này gãy ngay, mà gãy im lặng: chứng chỉ
    đọc đúng vẫn bị ghi "Tên khóa học không khớp".
    """
    assert match_course_bilingual(
        "AI co ban", "AI for Everyone", "AI CƠ BẢN_AI FOR EVERYONE", "loose")
    assert match_course_bilingual(
        "AI co ban", "AI for Everyone", "AI CƠ BẢN_AI FOR EVERYONE", "strict")


def test_LLM_khong_tach_duoc_song_ngu_thi_TRUOT():
    """Ghi lại một GIỚI HẠN ĐANG CÓ, không phải hành vi mong muốn.

    Khi model chỉ đọc ra MỘT nửa và bỏ trống nửa kia, trong khi eLIS lưu cả
    hai, hệ thống từ chối. Đúng luật hiện hành ("người nhập không được thừa
    từ so với ảnh") nhưng có thể là từ chối oan.

    Để test này ở đây để nếu ai đó nới luật thì phải sửa nó một cách CÓ Ý
    THỨC, chứ không nới nhầm rồi không ai biết.
    """
    assert not match_course_bilingual(
        "AI co ban", None, "AI CƠ BẢN_AI FOR EVERYONE", "loose")


def test_chi_mot_ngon_ngu_thi_khong_ghep_bua():
    """Nửa thứ hai rỗng -> không được ghép, tránh so với chuỗi cụt."""
    assert match_course_bilingual("Python cơ bản", None,
                                          "Python cơ bản", "loose")
    assert not match_course_bilingual("Python cơ bản", None,
                                              "Python fundamentals", "loose")

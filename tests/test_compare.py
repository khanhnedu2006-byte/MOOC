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
    same_word_set,
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
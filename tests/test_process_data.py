"""Test cho process_data: chuẩn hóa chuỗi và xử lý ngày tháng.

Chạy: pytest tests/test_process_data.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from process_data import normalize, parse_date, date_in_range


# ===== normalize =====

class TestNormalize:
    def test_strips_vietnamese_diacritics(self):
        assert normalize("Nguyễn Văn A") == "nguyen van a"

    def test_case_insensitive(self):
        assert normalize("NGUYEN VAN A") == "nguyen van a"

    def test_with_and_without_diacritics_equal(self):
        assert normalize("NGUYEN VAN A") == normalize("Nguyễn Văn A")

    def test_collapses_extra_whitespace(self):
        assert normalize("  Nguyễn   Văn  A  ") == "nguyen van a"

    def test_none_returns_empty(self):
        assert normalize(None) == ""

    def test_empty_string(self):
        assert normalize("") == ""

    def test_whitespace_only(self):
        assert normalize("   ") == ""

    def test_nfc_nfd_equal(self):
        import unicodedata
        nfc = "Nguyễn"
        nfd = unicodedata.normalize("NFD", "Nguyễn")
        # Khác byte nhưng sau chuẩn hóa phải giống nhau
        assert nfc.encode() != nfd.encode()
        assert normalize(nfc) == normalize(nfd)

    # Bỏ dấu câu (ca thật gặp khi test dữ liệu)
    def test_strips_dash_to_space(self):
        assert normalize("HIỆU QUẢ - TĂNG") == normalize("HIỆU QUẢ -TĂNG")

    def test_strips_double_quotes(self):
        assert normalize('ky nang "nhan feedback"') == normalize("ky nang nhan feedback")

    def test_strips_parentheses(self):
        assert normalize("Data Analysis (Phân tích)") == "data analysis phan tich"

    def test_still_distinguishes_content(self):
        # Bỏ dấu câu KHÔNG được làm hai nội dung khác nhau thành giống
        assert normalize("Python cơ bản") != normalize("Python nâng cao")


# ===== parse_date =====

class TestParseDate:
    def test_vietnamese_date_format(self):
        # 05/03 = 5 tháng 3, KHÔNG phải 3 tháng 5
        d = parse_date("05/03/2026")
        assert d.day == 5 and d.month == 3

    def test_english_date(self):
        d = parse_date("March 15, 2026")
        assert d.year == 2026 and d.month == 3 and d.day == 15

    def test_vietnamese_date(self):
        d = parse_date("ngày 15 tháng 3 năm 2026")
        assert d.month == 3 and d.day == 15

    def test_year_only_returns_none(self):
        # Thiếu ngày/tháng -> không đủ thông tin -> None
        assert parse_date("2026") is None

    def test_garbage_text_returns_none(self):
        assert parse_date("abcxyz") is None

    def test_none_returns_none(self):
        assert parse_date(None) is None

    def test_empty_returns_none(self):
        assert parse_date("") is None


# ===== date_in_range =====

class TestDateInRange:
    VALID_FROM = "2026-01-01"
    VALID_TO = "2026-09-30"

    def test_inside_range(self):
        assert date_in_range("15/03/2026", self.VALID_FROM, self.VALID_TO) is True

    def test_first_day_of_range(self):
        assert date_in_range("01/01/2026", self.VALID_FROM, self.VALID_TO) is True

    def test_last_day_of_range(self):
        assert date_in_range("30/09/2026", self.VALID_FROM, self.VALID_TO) is True

    def test_outside_range_after(self):
        assert date_in_range("01/10/2026", self.VALID_FROM, self.VALID_TO) is False

    def test_outside_range_previous_year(self):
        assert date_in_range("31/12/2025", self.VALID_FROM, self.VALID_TO) is False

    def test_unparseable_date(self):
        assert date_in_range("abcxyz", self.VALID_FROM, self.VALID_TO) is False

    def test_year_only(self):
        assert date_in_range("2026", self.VALID_FROM, self.VALID_TO) is False

    def test_none(self):
        assert date_in_range(None, self.VALID_FROM, self.VALID_TO) is False
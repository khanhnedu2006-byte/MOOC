"""Test cho compare: các luật khớp tên, khóa học, mã, và so LLM1-LLM2.

Chạy: pytest tests/test_compare.py -v
"""

import sys

import pytest
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


# ===== Từ viết tắt trong tên khóa học =====

class TestCourseAbbreviation:
    # Ca thật: eLIS lưu "Intro", chứng chỉ in "Introduction"
    def test_elis_abbreviated_image_full(self):
        assert match_course("Introduction to agent skills",
                            "Intro to Agent Skills") is True

    def test_elis_abbreviated_image_full_loose(self):
        assert match_course("Introduction to agent skills",
                            "Intro to Agent Skills", mode="loose") is True

    def test_image_abbreviated_elis_full(self):
        assert match_course("Intro to Agent Skills",
                            "Introduction to agent skills") is True

    # Bảng chỉ có "intro": từ viết tắt khác (AI) vẫn so chặt
    def test_only_listed_abbreviations_expand(self):
        assert match_course("Introduction to Artificial Intelligence",
                            "Intro to AI") is False
        assert match_course("Introduction to AI", "Intro to AI") is True

    def test_bilingual_goes_through_expansion(self):
        assert match_course_bilingual("Nhập môn kỹ năng agent",
                                      "Introduction to agent skills",
                                      "Intro to Agent Skills") is True

    # Không so theo tiền tố: "java" không được khớp "javascript"
    def test_no_prefix_matching(self):
        assert match_course("JavaScript basics", "Java basics") is False
        assert match_course("JavaScript basics", "Java basics", mode="loose") is False

    # Từ không có trong bảng thì vẫn so chặt như cũ
    def test_unknown_abbreviation_still_strict(self):
        assert match_course("Introduction to agent skills",
                            "Introd to Agent Skills") is False

    # Mở rộng viết tắt không được làm mất khác biệt nội dung
    def test_expansion_keeps_other_words_strict(self):
        assert match_course("Introduction to agent skills",
                            "Intro to Agent Tools") is False


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


# ===== Tên khóa bị nhà cung cấp đổi (COURSE_ALIASES) =====

class TestCourseAliases:
    # Ca thật: eLIS "Building with Claude API", chứng chỉ Anthropic in tên khác
    def test_ca_that_anthropic(self):
        assert match_course_bilingual("Claude with the Anthropic API", None,
                                      "Building with Claude API", "strict") is True
        assert match_course_bilingual("Claude with the Anthropic API", None,
                                      "Building with Claude API", "loose") is True

    def test_khong_phan_biet_hoa_thuong_dau_cau(self):
        assert match_course_bilingual("CLAUDE WITH THE ANTHROPIC API.", None,
                                      "building with claude api", "strict") is True

    def test_ten_goc_van_khop(self):
        assert match_course_bilingual("Building with Claude API", None,
                                      "Building with Claude API", "strict") is True

    # Khóa eLIS khác (dù giống tên) không được ăn theo ngoại lệ
    def test_khoa_elis_khac_khong_ap_ngoai_le(self):
        assert match_course_bilingual("Claude with the Anthropic API", None,
                                      "Building with the Claude API by Anthropic",
                                      "strict") is False

    # Ngoại lệ không mở cửa cho tên chứng chỉ bất kỳ
    def test_ten_chung_chi_khac_van_tu_choi(self):
        assert match_course_bilingual("Claude 101", None,
                                      "Building with Claude API", "loose") is False

    # Chiều ngược lại không tự áp: eLIS ghi tên chứng chỉ thì so như thường
    def test_khong_tu_dong_hai_chieu(self):
        assert match_course_bilingual("Building with Claude API", None,
                                      "Claude with the Anthropic API", "strict") is False


# ===== Tên viết liền (username dạng họ tên) =====

class TestJoinedName:
    @pytest.mark.parametrize("on_image", [
        "buiduchoa", "duchoabui", "hoabuiduc", "BuiDucHoa", "duchoa bui",
        "bui-duc-hoa", "BÙIĐỨCHOÀ",
    ])
    def test_moi_cach_ghep_deu_khop(self, on_image):
        assert match_name(on_image, "Bùi Đức Hoà") is True
        assert match_name_or_code(on_image, "Bùi Đức Hoà", "hoabd3") is True

    @pytest.mark.parametrize("on_image", [
        "buiduc",          # thiếu một từ
        "buiduchoa97",     # thừa ký tự
        "buiduchoang",     # từ khác
        "buihoaduchoa",    # lặp từ
    ])
    def test_thieu_thua_hoac_sai_thi_KHONG_khop(self, on_image):
        assert match_name(on_image, "Bùi Đức Hoà") is False

    def test_ten_co_dau_cach_van_nhu_cu(self):
        assert match_name("Hoa Bui Duc", "Bùi Đức Hoà") is True


# ===== Mã khóa ở cuối tên eLIS =====

from compare import strip_course_code


class TestTrailingCourseCode:
    @pytest.mark.parametrize("elis, stripped", [
        ("SAP Certified - Database Administrator - SAP HANA (C_DBADM_2601)",
         "SAP Certified - Database Administrator - SAP HANA"),
        ("SAP Certified - Implementation Consultant - SAP Customer Data Platform (C_C4H63)",
         "SAP Certified - Implementation Consultant - SAP Customer Data Platform"),
        ("Cloud Practitioner ( CLF-C02 )", "Cloud Practitioner"),
    ])
    def test_bo_ma(self, elis, stripped):
        assert strip_course_code(elis) == stripped

    @pytest.mark.parametrize("elis", [
        "Tiếng Anh theo chủ đề (Phần 1)",
        "Learning SAP MM (Materials Management)",
        "FPT KEY REGULATIONS AND POLICIES (ENGLISH VERSION)",
        "Python (2026)",            # chỉ có số, không có dấu nối
        "SAP (C_DBADM) basics",     # ngoặc không nằm ở cuối
        "Claude 101",
    ])
    def test_KHONG_bo_ngoac_that(self, elis):
        assert strip_course_code(elis) is None

    # Ca thật: chứng chỉ SAP không in mã khóa
    @pytest.mark.parametrize("mode", ["strict", "loose"])
    def test_ca_that_sap(self, mode):
        assert match_course_bilingual(
            "SAP Certified - Implementation Consultant - SAP Customer Data Platform",
            None,
            "SAP Certified - Implementation Consultant - SAP Customer Data Platform (C_C4H63)",
            mode) is True

    def test_anh_co_ca_ma_van_khop(self):
        name = "SAP Certified - Database Administrator - SAP HANA (C_DBADM_2601)"
        assert match_course_bilingual(name, None, name, "strict") is True

    def test_bo_ma_nhung_ten_khac_van_TU_CHOI(self):
        assert match_course_bilingual(
            "SAP Certified - Database Administrator - SAP HANA", None,
            "SAP Certified - Implementation Consultant - SAP Customer Data Platform (C_C4H63)",
            "loose") is False

    def test_phan_1_KHONG_khop_phan_2(self):
        assert match_course_bilingual("Tiếng Anh theo chủ đề", None,
                                      "Tiếng Anh theo chủ đề (Phần 2)", "loose") is False

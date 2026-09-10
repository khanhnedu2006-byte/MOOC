"""Test câu LÝ DO từ chối không đổ oan (test_ly_do).

LỖI ĐÃ XẢY RA THẬT (demo 10/09/2026). Chứng chỉ "AI cơ bản_AI for Everyone",
người nộp gõ SAI mỗi tên nhân viên. Màn hình trả về:

    Tên không khớp; Tên khóa học không khớp

Tên khóa học thì đúng — sửa lại tên người là chứng chỉ được APPROVED ngay.

VÌ SAO XẢY RA: phán quyết luôn tính trên bản đọc CUỐI CÙNG. Tên sai làm LLM1
trượt, pipeline rơi xuống tầng 2; LLM2 tách tên khóa song ngữ kém hơn LLM1
nên lý do đổ luôn cho tên khóa học.

HẬU QUẢ không chỉ là xấu mặt: học viên đọc lý do rồi đi sửa nhầm chỗ, nộp
lại vẫn trượt. Sai một trường mà bị báo sai hai trường.

CÁCH SỬA: một trường chỉ bị nêu tên khi CẢ HAI bản đọc đều trượt nó. Hai
bản đọc mâu thuẫn nhau ở trường nào thì im về trường đó — chưa đủ chắc để
bảo người ta đi sửa.

Test ở đây canh phần SINH CHUỖI LÝ DO. Phần phán quyết (APPROVED/REJECTED)
không đổi và có test riêng — xem test cuối file.
"""

import pipeline
import pytest
from schemas import ExtractedInfo, InputInfo

NHAP = InputInfo(
    employee_name="Nguyen Mai Huong",
    employee_code="NV001",
    course_name="AI CƠ BẢN_AI FOR EVERYONE",
)

# LLM1 tách song ngữ ĐÚNG, chỉ tên người là khác input.
DOC_TOT = ExtractedInfo(
    recipient_name="Nguyễn Thị Ngọc",
    certificate_name="AI cơ bản",
    certificate_name_alt="AI for Everyone",
    issue_date="10/09/2026",
)

# LLM2 đọc cùng ảnh nhưng KHÔNG tách được nửa thứ hai.
DOC_KEM = DOC_TOT.model_copy(update={"certificate_name_alt": None})


@pytest.fixture(autouse=True)
def khoang_ngay_hop_le(monkeypatch):
    """Cố định khoảng ngày để test không hỏng khi sang năm."""
    monkeypatch.setattr(pipeline.settings, "valid_from", "2026-01-01")
    monkeypatch.setattr(pipeline.settings, "valid_to", "2026-12-31")
    monkeypatch.setattr(pipeline.settings, "course_match_mode", "loose")


def test_ban_doc_kem_MOT_MINH_van_do_oan():
    """Đối chứng: đây là hành vi CŨ, và nó sai."""
    assert pipeline._mismatch_reason(DOC_KEM, NHAP) == (
        "Tên không khớp; Tên khóa học không khớp")


def test_co_ban_doi_chieu_thi_KHONG_do_oan_ten_khoa_hoc():
    """Bản kia đọc được tên khóa -> không nói chắc là tên khóa sai."""
    ly_do = pipeline._mismatch_reason(DOC_KEM, NHAP, DOC_TOT)
    assert "Tên không khớp" in ly_do
    assert "khóa học" not in ly_do


def test_ly_do_chi_gom_ten_truong_KHONG_them_chu_nao():
    """Người đọc cần biết đi sửa chỗ nào, không cần biết máy đọc mấy lần.

    Lý do phải là danh sách tên trường, hết. Thêm chữ giải thích vào đây là
    bắt học viên đọc chuyện nội bộ của hệ thống."""
    assert pipeline._mismatch_reason(DOC_KEM, NHAP, DOC_TOT) == "Tên không khớp"


def test_ca_HAI_ban_doc_deu_truot_thi_van_noi_chac():
    """Nới lý do không được biến ca sai thật thành mơ hồ."""
    sai_that = DOC_TOT.model_copy(update={"certificate_name": "Java nâng cao",
                                          "certificate_name_alt": None})
    ly_do = pipeline._mismatch_reason(sai_that, NHAP, sai_that)
    assert ly_do == "Tên không khớp; Tên khóa học không khớp"


def test_khong_co_ban_doi_chieu_thi_giu_nguyen_cach_ghi_cu():
    """Tầng 1 chưa có bản thứ hai — lý do phải y như trước."""
    sai_that = DOC_TOT.model_copy(update={"certificate_name": "Java nâng cao",
                                          "certificate_name_alt": None})
    assert pipeline._mismatch_reason(sai_that, NHAP) == (
        "Tên không khớp; Tên khóa học không khớp")


def test_ngay_ngoai_khoang_van_ghi_dung_cau_cu():
    ngoai = DOC_TOT.model_copy(update={"issue_date": "10/09/2019"})
    ly_do = pipeline._mismatch_reason(ngoai, NHAP, ngoai)
    assert "Ngày không hợp lệ" in ly_do


def test_chi_sai_ten_thi_chi_ghi_moi_ten():
    """Ca của bản demo, sau khi sửa: đúng một dòng, đúng một trường."""
    assert pipeline._mismatch_reason(DOC_TOT, NHAP, DOC_TOT) == "Tên không khớp"


def test_LY_DO_KHONG_DUOC_DOI_PHAN_QUYET():
    """Chốt quan trọng nhất: nới câu chữ không được nới cả kết luận.

    `_mismatch_reason` chỉ sinh chuỗi. Cái quyết định APPROVED/REJECTED là
    `_both_fields_match`, và nó KHÔNG nhìn sang bản đọc còn lại."""
    assert pipeline._both_fields_match(DOC_KEM, NHAP) is False
    assert pipeline._both_fields_match(DOC_TOT, NHAP) is False

    dung_ten = NHAP.model_copy(update={"employee_name": "Nguyễn Thị Ngọc"})
    assert pipeline._both_fields_match(DOC_TOT, dung_ten) is True
    # Bản đọc kém vẫn trượt dù tên đã đúng — đúng như trước khi sửa.
    assert pipeline._both_fields_match(DOC_KEM, dung_ten) is False

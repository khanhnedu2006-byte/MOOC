"""Test câu lý do từ chối không đổ oan (test_ly_do).

Phán quyết luôn tính trên bản đọc CUỐI CÙNG. Tên sai làm LLM1 trượt,
pipeline rơi xuống tầng 2; LLM2 tách tên khóa song ngữ kém hơn LLM1 thì lý
do đổ luôn cho tên khóa học, và người nộp đi sửa nhầm chỗ.

Luật: một trường chỉ bị nêu tên khi CẢ HAI bản đọc đều trượt nó. Hai bản mâu
thuẫn ở trường nào thì im về trường đó.

Test ở đây canh phần sinh chuỗi lý do. Phần phán quyết có test riêng ở cuối
file.
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
    """Đối chứng: một bản đọc thì không lọc được gì."""
    assert pipeline._mismatch_reason(DOC_KEM, NHAP) == (
        "Tên không khớp; Tên khóa học không khớp")


def test_co_ban_doi_chieu_thi_KHONG_do_oan_ten_khoa_hoc():
    """Bản kia đọc được tên khóa nên không nói chắc là tên khóa sai."""
    ly_do = pipeline._mismatch_reason(DOC_KEM, NHAP, DOC_TOT)
    assert "Tên không khớp" in ly_do
    assert "khóa học" not in ly_do


def test_ly_do_chi_gom_ten_truong_KHONG_them_chu_nao():
    """Lý do là danh sách tên trường, hết. Không thêm chữ giải thích."""
    assert pipeline._mismatch_reason(DOC_KEM, NHAP, DOC_TOT) == "Tên không khớp"


def test_ca_HAI_ban_doc_deu_truot_thi_van_noi_chac():
    """Lọc bớt không được biến ca sai thật thành mơ hồ."""
    sai_that = DOC_TOT.model_copy(update={"certificate_name": "Java nâng cao",
                                          "certificate_name_alt": None})
    ly_do = pipeline._mismatch_reason(sai_that, NHAP, sai_that)
    assert ly_do == "Tên không khớp; Tên khóa học không khớp"


def test_khong_co_ban_doi_chieu_thi_giu_nguyen_cach_ghi_cu():
    """Tầng 1 chưa có bản thứ hai để đối chiếu."""
    sai_that = DOC_TOT.model_copy(update={"certificate_name": "Java nâng cao",
                                          "certificate_name_alt": None})
    assert pipeline._mismatch_reason(sai_that, NHAP) == (
        "Tên không khớp; Tên khóa học không khớp")


def test_ngay_ngoai_khoang_van_ghi_dung_cau_cu():
    ngoai = DOC_TOT.model_copy(update={"issue_date": "10/09/2019"})
    ly_do = pipeline._mismatch_reason(ngoai, NHAP, ngoai)
    assert "Ngày không hợp lệ" in ly_do


def test_chi_sai_ten_thi_chi_ghi_moi_ten():
    """Sai một trường thì lý do đúng một dòng."""
    assert pipeline._mismatch_reason(DOC_TOT, NHAP, DOC_TOT) == "Tên không khớp"


def test_LY_DO_KHONG_DUOC_DOI_PHAN_QUYET():
    """`_mismatch_reason` chỉ sinh chuỗi. Quyết định APPROVED/REJECTED là
    `_both_fields_match`, và nó không nhìn sang bản đọc còn lại."""
    assert pipeline._both_fields_match(DOC_KEM, NHAP) is False
    assert pipeline._both_fields_match(DOC_TOT, NHAP) is False

    dung_ten = NHAP.model_copy(update={"employee_name": "Nguyễn Thị Ngọc"})
    assert pipeline._both_fields_match(DOC_TOT, dung_ten) is True
    # Bản đọc kém vẫn trượt dù tên đã đúng — đúng như trước khi sửa.
    assert pipeline._both_fields_match(DOC_KEM, dung_ten) is False


# ===== Tên thiếu họ/tên đệm, email cá nhân: KHÔNG nêu lỗi tên =====

GIVEN_HIEU = InputInfo(employee_name="Trần Trung Hiếu", employee_code="hieutt126",
                       course_name="Intro to Agent Skills")


def test_thieu_ten_dem_KHONG_ghi_ten_khong_khop():
    """Ca thật: 'Hieu Tran' là đúng người, chỉ thiếu tên đệm. Bị từ chối vì
    không có ngày thì comment chỉ được nói chuyện ngày."""
    doc = ExtractedInfo(recipient_name="Hieu Tran",
                        certificate_name="Introduction to agent skills",
                        issue_date=None)
    ly_do = pipeline._mismatch_reason(doc, GIVEN_HIEU, doc)
    assert ly_do == "Không tìm thấy ngày hoàn thành chứng chỉ"


def test_email_ca_nhan_KHONG_ghi_ten_khong_khop():
    doc = ExtractedInfo(recipient_name="hieu.tran@gmail.com",
                        certificate_name="Khóa khác", issue_date="10/09/2026")
    ly_do = pipeline._mismatch_reason(doc, GIVEN_HIEU, doc)
    assert ly_do == "Tên khóa học không khớp"
    assert "gmail" not in ly_do


def test_ten_khac_han_VAN_ghi_ten_khong_khop():
    doc = ExtractedInfo(recipient_name="Nguyen Van B",
                        certificate_name="Khóa khác", issue_date="10/09/2026")
    ly_do = pipeline._mismatch_reason(doc, GIVEN_HIEU, doc)
    assert ly_do == "Tên không khớp; Tên khóa học không khớp"


def test_email_cong_ty_sai_nguoi_VAN_ghi_ten_khong_khop():
    doc = ExtractedInfo(recipient_name="nguoikhac@fpt.com",
                        certificate_name="Khóa khác", issue_date="10/09/2026")
    assert pipeline._mismatch_reason(doc, GIVEN_HIEU, doc).startswith("Tên không khớp")


def test_KHONG_doi_phan_quyet():
    """Chỉ đổi câu chữ: thiếu tên đệm + thiếu ngày vẫn REJECTED."""
    doc = ExtractedInfo(recipient_name="Hieu Tran",
                        certificate_name="Introduction to agent skills",
                        issue_date=None)
    r = pipeline.process(images=[b"x"], given=GIVEN_HIEU,
                         extract_from_image=lambda i: doc,
                         ocr_images=lambda c, i: "ocr",
                         extract_from_text=lambda t: doc, azure_client=None)
    assert r.verdict.value == "REJECTED"
    assert r.reason == "Không tìm thấy ngày hoàn thành chứng chỉ"


# ===== Không tìm thấy tên người học =====

GIVEN_ANH = InputInfo(employee_name="Nguyễn Bá Anh", employee_code="anhnb2",
                      course_name="Building with the Claude API")


def test_ca_hai_ban_khong_co_ten_thi_KHONG_TIM_THAY_TEN():
    """Ca thật: ảnh chụp trang khóa học Coursera, không in tên người học."""
    doc = ExtractedInfo(recipient_name=None,
                        certificate_name="Building with the Claude API",
                        issue_date="10/09/2026")
    assert pipeline._mismatch_reason(doc, GIVEN_ANH, doc) == \
        "Không tìm thấy tên người học trên chứng chỉ"


def test_mot_ban_doc_ra_ten_khac_thi_van_TEN_KHONG_KHOP():
    khong_ten = ExtractedInfo(recipient_name="  ", certificate_name="Building with the Claude API",
                              issue_date="10/09/2026")
    ten_khac = khong_ten.model_copy(update={"recipient_name": "Tran Van C"})
    assert pipeline._mismatch_reason(khong_ten, GIVEN_ANH, ten_khac) == "Tên không khớp"
    assert pipeline._mismatch_reason(ten_khac, GIVEN_ANH, khong_ten) == "Tên không khớp"


def test_khong_ten_va_khong_ngay_ghep_ca_hai():
    doc = ExtractedInfo(recipient_name=None,
                        certificate_name="Building with the Claude API", issue_date=None)
    assert pipeline._mismatch_reason(doc, GIVEN_ANH, doc) == (
        "Không tìm thấy tên người học trên chứng chỉ; "
        "Không tìm thấy ngày hoàn thành chứng chỉ")


def test_khong_ten_qua_pipeline_van_REJECTED():
    doc = ExtractedInfo(recipient_name=None,
                        certificate_name="Building with the Claude API",
                        issue_date="10/09/2026")
    r = pipeline.process(images=[b"x"], given=GIVEN_ANH,
                         extract_from_image=lambda i: doc,
                         ocr_images=lambda c, i: "ocr",
                         extract_from_text=lambda t: doc, azure_client=None)
    assert r.verdict.value == "REJECTED"
    assert r.reason == "Không tìm thấy tên người học trên chứng chỉ"

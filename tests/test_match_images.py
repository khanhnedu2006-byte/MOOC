"""Test công cụ ghép Excel với ảnh (test_match_images).

Sai ở đây KHÔNG CÓ TRIỆU CHỨNG: ghép lệch một dòng thì bộ đánh giá vẫn chạy
trơn nhưng chấm model bằng nhãn của ảnh khác, và không tầng nào trên bắt được.

  1. Ghép ĐÚNG bất kể cách đặt tên file (dấu _, dấu -, chữ hoa, dấu tiếng Việt).
  2. Ghép KHÔNG ĐƯỢC thì phải BÁO RA, tuyệt đối không tự đoán bừa.
"""

import pathlib
import sys

import pytest

GOC = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GOC))
sys.path.insert(0, str(GOC / "src"))

from evaluation import match_images                      # noqa: E402


def _dong(row, email, course, name="Bùi Đức Hòa"):
    from process_data import code_from_email
    return {"excel_row": row, "email": email, "course_name": course,
            "employee_name": name, "employee_code": code_from_email(email)}


def _tao_anh(thu_muc, *name):
    """Tạo file rỗng — công cụ ghép chỉ đọc TÊN, không mở nội dung."""
    ra = []
    for t in name:
        p = thu_muc / t
        p.write_bytes(b"")
        ra.append(p)
    return ra


# ===== Ghép đúng =====

@pytest.mark.parametrize("file_name", [
    "hoabd3_Learning Microsoft 365 Copilot for Work.jpg",
    "hoabd3_Learning_Microsoft_365_Copilot_for_Work.png",
    "HOABD3-Learning-Microsoft-365-Copilot-for-Work.pdf",
    "hoabd3_learning microsoft 365 copilot for work (1).jpeg",
])
def test_ghep_duoc_moi_cach_dat_ten(tmp_path, file_name):
    """Bốn cách đặt tên khác nhau phải cho cùng một kết quả ghép.

    Ca "(1)" là hậu tố trình duyệt thêm khi tải trùng tên; không bỏ nó thì
    "1" thành một từ trong tên khóa và ảnh đó không khớp dòng nào.
    """
    anh = _tao_anh(tmp_path, file_name)
    rows = [_dong(2, "hoabd3@fpt.com.vn", "Learning Microsoft 365 Copilot for Work")]

    result = match_images.match(rows, anh)

    assert len(result["matched"]) == 1, f"không ghép được: {file_name}"
    assert result["matched"][0][1] == anh[0]
    assert result["rows_no_image"] == [] and result["images_no_row"] == []


def test_ghep_dung_cap_khi_thu_tu_lech(tmp_path):
    """Đây là bài toán gốc: Excel và thư mục ảnh KHÔNG cùng thứ tự."""
    anh = _tao_anh(
        tmp_path,
        "anv2_AI Trends.jpg",                       # ứng dòng 4
        "hoabd3_Beyond Basic PowerPoint Slides.jpg",  # ứng dòng 2
        "bttc1_ISO 27001.jpg",                      # ứng dòng 3
    )
    rows = [
        _dong(2, "hoabd3@fpt.com", "Beyond Basic PowerPoint Slides"),
        _dong(3, "bttc1@fpt.com", "ISO 27001"),
        _dong(4, "anv2@fpt.com", "AI Trends"),
    ]

    result = match_images.match(rows, anh)
    cap = {r["excel_row"]: p.name for r, p in result["matched"]}

    assert cap == {
        2: "hoabd3_Beyond Basic PowerPoint Slides.jpg",
        3: "bttc1_ISO 27001.jpg",
        4: "anv2_AI Trends.jpg",
    }, "ghép theo thứ tự thư mục thay vì theo nội dung"


def test_ten_khoa_co_dau_tieng_viet(tmp_path):
    """Tên file bỏ dấu, Excel có dấu — vẫn phải khớp."""
    anh = _tao_anh(tmp_path, "hoabd3_An toan thong tin co ban.jpg")
    rows = [_dong(2, "hoabd3@fpt.com", "An toàn thông tin cơ bản")]
    assert len(match_images.match(rows, anh)["matched"]) == 1


def test_ten_khoa_chua_dau_gach_duoi(tmp_path):
    """Tên khóa học có "_" bên trong, tách sai một lần là ghép sai cả bộ."""
    anh = _tao_anh(tmp_path, "hoabd3_Excel_Power_Query_101.jpg")
    rows = [_dong(2, "hoabd3@fpt.com", "Excel Power Query 101")]
    assert len(match_images.match(rows, anh)["matched"]) == 1


# ===== Không ghép được thì phải BÁO, không đoán =====

def test_dong_thieu_anh_duoc_bao_ra(tmp_path):
    anh = _tao_anh(tmp_path, "hoabd3_ISO 27001.jpg")
    rows = [_dong(2, "hoabd3@fpt.com", "ISO 27001"),
            _dong(3, "anv2@fpt.com", "Khóa không có ảnh")]

    result = match_images.match(rows, anh)

    assert len(result["matched"]) == 1
    assert [r["excel_row"] for r in result["rows_no_image"]] == [3]


def test_anh_thua_duoc_bao_ra(tmp_path):
    anh = _tao_anh(tmp_path, "hoabd3_ISO 27001.jpg", "xxx_Khong co trong excel.jpg")
    rows = [_dong(2, "hoabd3@fpt.com", "ISO 27001")]

    result = match_images.match(rows, anh)

    assert len(result["matched"]) == 1
    assert [p.name for p in result["images_no_row"]] == ["xxx_Khong co trong excel.jpg"]


def test_trung_khoa_thi_bao_mo_ho_chu_khong_tu_chon(tmp_path):
    """Cùng NV + cùng khóa, hai ảnh -> KHÔNG được chọn bừa một cái.

    Chọn bừa vẫn ra bộ dữ liệu chạy được, với 50% khả năng gán sai nhãn.
    """
    anh = _tao_anh(tmp_path, "hoabd3_ISO 27001.jpg", "hoabd3_ISO-27001.png")
    rows = [_dong(2, "hoabd3@fpt.com", "ISO 27001")]

    result = match_images.match(rows, anh)

    assert result["matched"] == [], "đã tự chọn một ảnh trong ca mơ hồ"
    assert len(result["ambiguous"]) == 1
    assert sorted(result["ambiguous"][0]["rows"]) == [2]
    assert len(result["ambiguous"][0]["images"]) == 2


def test_hai_dong_trung_nhau_cung_bao_mo_ho(tmp_path):
    """Nộp lại cùng một khóa 2 lần: 2 dòng Excel, 1 ảnh -> mơ hồ."""
    anh = _tao_anh(tmp_path, "hoabd3_ISO 27001.jpg")
    rows = [_dong(2, "hoabd3@fpt.com", "ISO 27001"),
            _dong(9, "hoabd3@fpt.com", "ISO 27001")]

    result = match_images.match(rows, anh)

    assert result["matched"] == []
    assert sorted(result["ambiguous"][0]["rows"]) == [2, 9]


def test_khong_ghep_mo_chi_goi_y(tmp_path):
    """Tên gần giống (bị cắt ngắn) KHÔNG được tự ghép, chỉ được gợi ý."""
    anh = _tao_anh(tmp_path, "hoabd3_Learning Microsoft 365 Copilot.jpg")
    rows = [_dong(2, "hoabd3@fpt.com", "Learning Microsoft 365 Copilot for Work")]

    result = match_images.match(rows, anh)

    assert result["matched"] == [], "đã tự ghép mờ — đúng loại sai không có triệu chứng"
    assert len(result["rows_no_image"]) == 1
    assert len(result["suggestions"]) == 1
    assert result["suggestions"][0]["excel_row"] == 2
    assert 0.5 <= result["suggestions"][0]["score"] < 1.0


def test_phep_cong_luon_khop(tmp_path):
    """Mọi dòng và mọi ảnh phải rơi vào đúng MỘT nhóm, không mất, không đếm hai lần."""
    anh = _tao_anh(tmp_path,
                   "hoabd3_ISO 27001.jpg",          # ghép được
                   "anv2_AI Trends.jpg",            # ghép được
                   "zzz_Anh thua.jpg",              # thừa
                   "bttc1_Trung.jpg", "bttc1-Trung.png")   # mơ hồ
    rows = [_dong(2, "hoabd3@fpt.com", "ISO 27001"),
            _dong(3, "anv2@fpt.com", "AI Trends"),
            _dong(4, "khac@fpt.com", "Khóa thiếu ảnh"),
            _dong(5, "bttc1@fpt.com", "Trung")]

    result = match_images.match(rows, anh)

    mo_ho_dong = sum(len(a["rows"]) for a in result["ambiguous"])
    mo_ho_anh = sum(len(a["images"]) for a in result["ambiguous"])
    assert len(result["matched"]) + len(result["rows_no_image"]) + mo_ho_dong == len(rows)
    assert len(result["matched"]) + len(result["images_no_row"]) + mo_ho_anh == len(anh)


# ===== Sinh file nhãn =====

def test_to_cases_de_trong_nhan_chuan(tmp_path):
    """Cột gt_* PHẢI trống: nhãn chuẩn lấy từ model thì model luôn đúng 100%."""
    anh = _tao_anh(tmp_path, "hoabd3_ISO 27001.jpg")
    rows = [_dong(2, "hoabd3@fpt.com", "ISO 27001", name="Bùi Đức Hòa")]

    cases = match_images.to_cases(match_images.match(rows, anh)["matched"])

    assert len(cases) == 1
    c = cases[0]
    assert c.input_employee_code == "hoabd3"
    assert c.input_course_name == "ISO 27001"
    assert c.input_employee_name == "Bùi Đức Hòa"
    assert c.image_path.endswith("hoabd3_ISO 27001.jpg")
    assert "excel_row=2" in c.note
    assert c.gt_recipient_name is None
    assert c.gt_verdict is None


def test_case_id_khong_trung(tmp_path):
    """Hai ca cùng nhân viên phải có case_id khác nhau, nếu không CSV mất dòng."""
    anh = _tao_anh(tmp_path, "hoabd3_ISO 27001.jpg", "hoabd3_AI Trends.jpg")
    rows = [_dong(2, "hoabd3@fpt.com", "ISO 27001"),
            _dong(3, "hoabd3@fpt.com", "AI Trends")]

    cases = match_images.to_cases(match_images.match(rows, anh)["matched"])
    assert len({c.case_id for c in cases}) == 2


# ===== Đọc Excel =====

def _tao_excel(path, headers, rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(path)


def test_doc_excel_tu_doan_cot(tmp_path):
    f = tmp_path / "d.xlsx"
    _tao_excel(f, ["STT", "Employee Email", "Employee Name", "Course Name"],
               [[1, "hoabd3@fpt.com", "Bùi Đức Hòa", "ISO 27001"]])

    rows, cot = match_images.read_excel(f)

    assert cot["email"] == "Employee Email"
    assert cot["course"] == "Course Name", "đoán nhầm 'Course Name' thành cột họ tên"
    assert cot["name"] == "Employee Name"
    assert rows[0]["employee_code"] == "hoabd3"
    assert rows[0]["excel_row"] == 2


def test_doc_excel_tieu_de_tieng_viet(tmp_path):
    f = tmp_path / "d.xlsx"
    _tao_excel(f, ["Email nhân viên", "Họ tên", "Tên khóa học"],
               [["hoabd3@fpt.com", "Bùi Đức Hòa", "ISO 27001"]])

    rows, cot = match_images.read_excel(f)

    assert cot["email"] == "Email nhân viên"
    assert cot["course"] == "Tên khóa học"
    assert rows[0]["course_name"] == "ISO 27001"


def test_khong_doan_duoc_cot_thi_bao_loi_ro(tmp_path):
    """Báo lỗi phải LIỆT KÊ cột đang có, nếu không người dùng phải mò."""
    f = tmp_path / "d.xlsx"
    _tao_excel(f, ["Cot A", "Cot B"], [["x", "y"]])

    with pytest.raises(ValueError) as e:
        match_images.read_excel(f)

    assert "Cot A" in str(e.value) and "--col-email" in str(e.value)


def test_bo_qua_dong_trong_cuoi_sheet(tmp_path):
    f = tmp_path / "d.xlsx"
    _tao_excel(f, ["Email", "Course Name"],
               [["hoabd3@fpt.com", "ISO 27001"], [None, None], ["", ""]])

    rows, _ = match_images.read_excel(f)
    assert len(rows) == 1


# ===== Kết luận của eLIS chỉ để tham khảo, KHÔNG thành nhãn chuẩn =====

def test_submit_status_khong_thanh_gt_verdict(tmp_path):
    """Cột APPROVED/REJECTED của eLIS KHÔNG được đổ vào gt_verdict.

    Người duyệt từ chối vì nhiều lý do hệ thống không kiểm, nên lấy nó làm
    nhãn chuẩn là chấm AI trượt vì thứ chưa bao giờ giao cho nó.
    """
    anh = _tao_anh(tmp_path, "hoabd3_ISO 27001.jpg")
    r = _dong(2, "hoabd3@fpt.com", "ISO 27001")
    r["elis_status"] = "REJECTED"
    r["elis_comment"] = "CB log trùng khóa học"

    cases = match_images.to_cases(match_images.match([r], anh)["matched"])

    assert cases[0].gt_verdict is None, "đã đổ kết luận eLIS vào nhãn chuẩn"
    # Nhưng vẫn phải thấy được bối cảnh khi gán nhãn.
    assert "REJECTED" in cases[0].note
    assert "log trùng" in cases[0].note


def test_doc_duoc_cot_status_va_comment(tmp_path):
    f = tmp_path / "d.xlsx"
    _tao_excel(f, ["Employee Email", "Course Name", "Submit Status", "Comment"],
               [["hoabd3@fpt.com", "ISO 27001", "REJECTED", "CB submit trùng khoá"]])

    rows, cot = match_images.read_excel(f)

    assert cot["status"] == "Submit Status"
    assert rows[0]["elis_status"] == "REJECTED"
    assert rows[0]["elis_comment"] == "CB submit trùng khoá"


def test_thieu_cot_status_van_chay_binh_thuong(tmp_path):
    """File Excel không có hai cột đó thì công cụ vẫn phải chạy."""
    f = tmp_path / "d.xlsx"
    _tao_excel(f, ["Employee Email", "Course Name"], [["hoabd3@fpt.com", "ISO 27001"]])

    rows, cot = match_images.read_excel(f)

    assert cot["status"] is None and cot["comment"] is None
    assert rows[0]["elis_status"] == ""


# ===== Hoa/thường trong email KHÔNG được ảnh hưởng tới việc ghép =====

@pytest.mark.parametrize("email,file_name", [
    ("DungHA31@fpt.com",  "DUNGHA31_Java Cơ bản.png"),
    ("dungHA31@fpt.com",  "dungha31_Java Cơ bản.png"),
    ("DUNGHA31@FPT.COM",  "DungHA31_Java Cơ bản.png"),
    ("DungNV114@fpt.com", "DUNGNV114_Java Cơ bản.png"),
])
def test_hoa_thuong_trong_email_khong_anh_huong(tmp_path, email, file_name):
    """Email FPT viết kiểu 'DungHA31' (tên thường + chữ cái đầu viết hoa).

    code_from_email() và normalize() đều hạ chữ thường, nên mọi cách viết
    hoa phải ra cùng một khóa ghép.
    """
    anh = _tao_anh(tmp_path, file_name)
    rows = [_dong(2, email, "Java Cơ bản")]
    assert len(match_images.match(rows, anh)["matched"]) == 1


def test_bao_cao_tach_chua_nop_anh_voi_ghep_hut(tmp_path, capsys):
    """Hai loại 'thiếu ảnh' phải được tách, vì cần hai cách xử lý khác nhau.

    - Mã không có ảnh nào  -> chưa nộp, KHÔNG phải lỗi ghép.
    - Mã có ảnh khóa khác  -> ghép hụt thật, đáng soi tên khóa.
    """
    anh = _tao_anh(tmp_path,
                   "HOABD3_ISO 27001.jpg",          # hoabd3 có ảnh, khóa khác
                   "ANV2_AI Trends.jpg")            # ghép được
    rows = [
        _dong(2, "anv2@fpt.com", "AI Trends"),                  # khớp
        _dong(3, "hoabd3@fpt.com", "Khóa khác hẳn"),            # hụt
        _dong(4, "khongco@fpt.com", "Khóa nào đó"),             # chưa nộp
    ]

    result = match_images.match(rows, anh)
    match_images._in_bao_cao(rows, anh, result, {
        "email": "Email", "course": "Course", "name": None,
        "status": None, "comment": None, "all": ["Email", "Course"]})

    ra = capsys.readouterr().out
    assert "KHÔNG có ảnh nào trong thư mục" in ra
    assert "khongco" in ra
    assert "hụt đúng khóa này" in ra
    assert "hoabd3" in ra
    assert "HOABD3_ISO 27001.jpg" in ra, "không chỉ ra ảnh đang có của người đó"


# ===== Hậu tố "(N)" vs NĂM trong ngoặc =====

def test_nam_trong_ngoac_KHONG_bi_cat(tmp_path):
    """NĂM trong ngoặc không được cắt như thể là hậu tố tải trùng.

    Regex \\(\\d+\\) ăn mất '(2025)' thì hai bên lệch MỘT từ, không ghép được.
    """
    anh = _tao_anh(
        tmp_path,
        "QUYND10_Luyện thi PMP_Tư duy & mẹo làm bài (Mindset & Tips) (2025).pdf")
    rows = [_dong(177, "QuyND10@fpt.com",
                  "Luyện thi PMP: Tư duy & mẹo làm bài (Mindset & Tips) (2025)")]

    assert len(match_images.match(rows, anh)["matched"]) == 1


@pytest.mark.parametrize("duoi", ["(2024)", "(2025)", "(2026)", "(1999)"])
def test_moi_nam_4_chu_so_deu_duoc_giu(tmp_path, duoi):
    anh = _tao_anh(tmp_path, f"hoabd3_Khóa gì đó {duoi}.pdf")
    rows = [_dong(2, "hoabd3@fpt.com", f"Khóa gì đó {duoi}")]
    assert len(match_images.match(rows, anh)["matched"]) == 1


@pytest.mark.parametrize("duoi", ["(1)", "(2)", "(10)", " - Copy"])
def test_hau_to_tai_trung_van_bi_bo(tmp_path, duoi):
    """Đối chứng: sửa cho năm không được làm mất khả năng bỏ hậu tố thật."""
    anh = _tao_anh(tmp_path, f"hoabd3_ISO 27001{duoi}.jpg")
    rows = [_dong(2, "hoabd3@fpt.com", "ISO 27001")]
    assert len(match_images.match(rows, anh)["matched"]) == 1


# ===== Gợi ý không được trỏ sang ảnh của người khác =====

def test_khong_goi_y_anh_cua_nguoi_khac(tmp_path):
    """Gợi ý sai NGƯỜI còn tệ hơn không gợi ý gì.

    Gợi ý theo tên khóa mà bỏ qua mã nhân viên là gán chứng chỉ của người
    này cho người kia, sai không có triệu chứng.
    """
    anh = _tao_anh(tmp_path, "KIENNT128_Claude in Google Vertex Al.png")
    rows = [_dong(52, "ducdm45@fpt.com", "Claude in Google Vertex Al")]

    result = match_images.match(rows, anh)

    assert result["matched"] == []
    assert result["suggestions"] == [], "đã gợi ý ảnh của nhân viên khác"


def test_van_goi_y_khi_CUNG_ma_nhan_vien(tmp_path):
    """Đối chứng: cùng người, tên khóa lệch chút -> vẫn phải gợi ý."""
    anh = _tao_anh(tmp_path, "hoabd3_Learning Microsoft 365 Copilot.jpg")
    rows = [_dong(2, "hoabd3@fpt.com", "Learning Microsoft 365 Copilot for Work")]

    result = match_images.match(rows, anh)

    assert result["matched"] == []
    assert len(result["suggestions"]) == 1
    assert result["suggestions"][0]["excel_row"] == 2


# ===== Báo cáo đầy đủ ra CSV =====

def test_bao_cao_day_du_KHONG_cat_bot_dong(tmp_path):
    """Bảng trên màn hình cắt ở 20 dòng; file này thì KHÔNG được cắt.

    Bị cắt thì người nhận tưởng 20 là tất cả và bổ sung thiếu.
    """
    import csv as _csv
    anh = _tao_anh(tmp_path, *[f"NGUOI{i}_Khóa {i}.jpg" for i in range(30)])
    rows = [_dong(i + 2, f"khac{i}@fpt.com", f"Khóa nào đó {i}") for i in range(25)]

    result = match_images.match(rows, anh)
    ra = tmp_path / "bao_cao.csv"
    n = match_images.write_full_report(rows, anh, result, ra)

    assert n == 55, "cắt bớt dòng trong file báo cáo"
    doc = list(_csv.DictReader(ra.open(encoding="utf-8-sig")))
    assert len(doc) == 55
    assert sum(1 for d in doc if d["loai"] == "anh_thua") == 30
    assert sum(1 for d in doc if d["loai"] == "chua_nop_anh") == 25


def test_bao_cao_day_du_phan_loai_dung(tmp_path):
    import csv as _csv
    anh = _tao_anh(tmp_path,
                   "HOABD3_ISO 27001.jpg",        # hoabd3 có ảnh, khóa khác
                   "ZZZ_Anh thua.jpg")            # không có dòng nào
    rows = [_dong(2, "hoabd3@fpt.com", "Khóa khác hẳn"),      # ghep_hut
            _dong(3, "khongco@fpt.com", "Khóa gì đó")]        # chua_nop_anh

    result = match_images.match(rows, anh)
    ra = tmp_path / "bao_cao.csv"
    match_images.write_full_report(rows, anh, result, ra)

    theo_loai = {d["loai"]: d
                 for d in _csv.DictReader(ra.open(encoding="utf-8-sig"))}
    assert theo_loai["anh_thua"]["ten_file_anh"] == "ZZZ_Anh thua.jpg"
    assert theo_loai["chua_nop_anh"]["ma_nv"] == "khongco"
    assert theo_loai["ghep_hut"]["ma_nv"] == "hoabd3"
    # Ca ghép hụt phải chỉ ra ảnh người đó ĐANG CÓ, để người đọc biết so tên
    # khóa với cái gì.
    assert "HOABD3_ISO 27001.jpg" in theo_loai["ghep_hut"]["ghi_chu"]


def test_bao_cao_day_du_ghi_ca_ca_mo_ho(tmp_path):
    import csv as _csv
    anh = _tao_anh(tmp_path, "hoabd3_ISO 27001.jpg", "hoabd3_ISO-27001.png")
    rows = [_dong(2, "hoabd3@fpt.com", "ISO 27001")]

    result = match_images.match(rows, anh)
    ra = tmp_path / "bao_cao.csv"
    match_images.write_full_report(rows, anh, result, ra)

    doc = list(_csv.DictReader(ra.open(encoding="utf-8-sig")))
    mo_ho = [d for d in doc if d["loai"] == "mo_ho"]
    assert len(mo_ho) == 1
    assert "KHÔNG tự chọn" in mo_ho[0]["ghi_chu"]

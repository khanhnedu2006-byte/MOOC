"""Test báo cáo của bộ đánh giá (test_eval_report).

VẤN ĐỀ ĐÃ XẢY RA THẬT: một lượt chạy trên dữ liệu thật cho ra
    "Không trích xuất được (84 ca): 002_nguyentd36, 005_linhnt8, ..."
84 mã ca trần, KHÔNG một chữ nào nói vì sao. Thiếu file, hết quota LLM và
file hỏng là ba nguyên nhân cần ba cách xử lý hoàn toàn khác nhau, mà báo
cáo gộp cả ba thành một danh sách như nhau. Lý do có được in lúc chạy nhưng
nằm lẫn giữa hàng trăm dòng và cuộn mất — một lượt chạy tốn tiền LLM thật
nên "chạy lại để xem lỗi" không phải cách chấp nhận được.

Đây đúng loại lỗi đã sửa ở ocr_azure.ocr_images trước đây: nuốt mất nguyên
nhân rồi báo bằng một câu chung chung.
"""

import pathlib
import sys

import pytest

GOC = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GOC))
sys.path.insert(0, str(GOC / "src"))

from evaluation import run_eval                       # noqa: E402
from evaluation.dataset import EvalCase               # noqa: E402


def _ca(case_id, image_path="data/image/x.jpg", **kw):
    return EvalCase(case_id=case_id, image_path=image_path,
                    input_employee_name="Bùi Đức Hòa",
                    input_course_name="ISO 27001",
                    input_employee_code="hoabd3", **kw)


# ===== Gom lý do hỏng =====

def test_gom_theo_ly_do_cung_nguyen_nhan_ve_mot_nhom():
    """Cùng loại lỗi nhưng khác đuôi (tên file, id) phải về CÙNG một nhóm.

    Gom nguyên văn thì 84 ca ra 84 nhóm và không tóm tắt được gì.
    """
    errors = {
        "a": "LlmVisionError: Lỗi gọi Gemma: 429 Too Many Requests",
        "b": "LlmVisionError: Lỗi gọi Gemma: 429 Too Many Requests",
        "c": "LlmVisionError: Lỗi gọi Gemma: 429 Too Many Requests",
        "d": "FileNotFoundError: không thấy file: data/image/x.jpg",
    }

    nhom = run_eval._gom_theo_ly_do(errors)

    assert len(nhom) == 2, f"gom sai: {nhom}"
    # Nhóm đông nhất phải lên đầu — đó là nguyên nhân đáng sửa trước.
    assert len(nhom[0][1]) == 3
    assert "Gemma" in nhom[0][0]
    assert len(nhom[1][1]) == 1


def test_gom_theo_ly_do_giu_du_ngu_canh():
    """Chỉ giữ tên lớp lỗi là chưa đủ: 'Exception' không nói được gì."""
    nhom = run_eval._gom_theo_ly_do({"a": "LlmVisionError: JSON không khớp schema: x"})
    khoa = nhom[0][0]
    assert "LlmVisionError" in khoa
    assert "JSON" in khoa, "cắt mất phần giải thích, chỉ còn tên lớp lỗi"


def test_gom_theo_ly_do_khong_co_dau_hai_cham():
    nhom = run_eval._gom_theo_ly_do({"a": "hỏng không rõ"})
    assert nhom == [("hỏng không rõ", ["a"])]


# ===== run_pipeline trả về lý do, không chỉ None =====

def test_run_pipeline_ghi_lai_ly_do_thieu_file(tmp_path, monkeypatch):
    """Thiếu file phải vào errors kèm đường dẫn, không chỉ biến mất thành None."""
    monkeypatch.setattr(run_eval, "PROJECT_ROOT", tmp_path)
    results, errors = run_eval.run_pipeline([_ca("001", "khong/co/that.jpg")], None)

    assert results["001"] is None
    assert "không thấy file" in errors["001"]
    assert "khong/co/that.jpg" in errors["001"]


def test_run_pipeline_ghi_lai_ly_do_ngoai_le(tmp_path, monkeypatch):
    """Ngoại lệ phải được ghi kèm TÊN LỚP lỗi — 'lỗi' trần không sửa được gì."""
    anh = tmp_path / "data" / "image"
    anh.mkdir(parents=True)
    (anh / "x.jpg").write_bytes(b"gia")

    monkeypatch.setattr(run_eval, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(run_eval.file_utils, "read_as_images",
                        lambda p: (_ for _ in ()).throw(ValueError("file hỏng")))

    results, errors = run_eval.run_pipeline([_ca("001")], None)

    assert results["001"] is None
    assert errors["001"] == "ValueError: file hỏng"


def test_ca_chay_duoc_khong_vao_errors(tmp_path, monkeypatch):
    """Đối chứng: ca chạy trót lọt KHÔNG được nằm trong errors."""
    from schemas import ProcessResult, Verdict
    anh = tmp_path / "data" / "image"
    anh.mkdir(parents=True)
    (anh / "x.jpg").write_bytes(b"gia")

    monkeypatch.setattr(run_eval, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(run_eval.file_utils, "read_as_images", lambda p: [b"anh"])
    monkeypatch.setattr(run_eval.pipeline, "process", lambda **kw: ProcessResult(
        employee_code="hoabd3", verdict=Verdict.APPROVED, reason="ok", stage="llm1"))

    results, errors = run_eval.run_pipeline([_ca("001")], None)

    assert results["001"] is not None
    assert errors == {}


# ===== Ghi kết quả thô =====

def test_ghi_ket_qua_tho_giu_ca_ket_qua_lan_loi(tmp_path, monkeypatch):
    """Một lượt chạy tốn tiền LLM thật -> phải lưu được để xem lại."""
    import csv
    from schemas import ExtractedInfo, ProcessResult, Verdict

    cases = [_ca("001"), _ca("002")]
    results = {
        "001": ProcessResult(employee_code="hoabd3", verdict=Verdict.APPROVED,
                             reason="khớp", stage="llm1",
                             extracted=ExtractedInfo(recipient_name="Bui Duc Hoa",
                                                     certificate_name="ISO 27001",
                                                     issue_date="01/08/2026")),
        "002": None,
    }
    out = tmp_path / "last_run.csv"
    run_eval._ghi_ket_qua_tho(cases, results, {"002": "ValueError: hỏng"}, out)

    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert rows[0]["verdict"] == "APPROVED"
    assert rows[0]["recipient_name"] == "Bui Duc Hoa"
    assert rows[0]["error"] == ""
    assert rows[1]["verdict"] == ""
    assert rows[1]["error"] == "ValueError: hỏng"


# ===== Lệnh check (không gọi LLM) =====

def test_check_dem_dung_file_thieu(tmp_path, monkeypatch, capsys):
    anh = tmp_path / "data" / "image"
    anh.mkdir(parents=True)
    (anh / "co.jpg").write_bytes(b"x")
    monkeypatch.setattr(run_eval, "PROJECT_ROOT", tmp_path)

    ma = run_eval.check_dataset([_ca("001", "data/image/co.jpg"),
                                 _ca("002", "data/image/khong.jpg")])

    ra = capsys.readouterr().out
    assert "Có file ảnh : 1" in ra
    assert "THIẾU file  : 1" in ra
    assert "data/image/khong.jpg" in ra
    assert ma == 1, "thiếu file mà vẫn trả mã 0 thì script gọi nó không biết"


def test_check_bao_ro_chua_gan_gt_verdict(tmp_path, monkeypatch, capsys):
    """Phần 2 trống là ĐÚNG THIẾT KẾ khi chưa gán nhãn — phải nói rõ ra."""
    anh = tmp_path / "data" / "image"
    anh.mkdir(parents=True)
    (anh / "co.jpg").write_bytes(b"x")
    monkeypatch.setattr(run_eval, "PROJECT_ROOT", tmp_path)

    run_eval.check_dataset([_ca("001", "data/image/co.jpg")])

    ra = capsys.readouterr().out
    assert "Chưa gán gt_verdict ca nào" in ra
    assert "không phải lỗi" in ra


def test_check_dem_dung_so_nhan_da_gan(tmp_path, monkeypatch, capsys):
    anh = tmp_path / "data" / "image"
    anh.mkdir(parents=True)
    (anh / "co.jpg").write_bytes(b"x")
    monkeypatch.setattr(run_eval, "PROJECT_ROOT", tmp_path)

    run_eval.check_dataset([
        _ca("001", "data/image/co.jpg", gt_recipient_name="Bùi Đức Hòa",
            gt_verdict="APPROVED"),
        _ca("002", "data/image/co.jpg"),
    ])

    ra = capsys.readouterr().out
    assert "gt_recipient_name            1/2" in ra
    assert "gt_verdict                   1/2" in ra


# ===== Nhãn chuẩn lấy từ NGƯỜI DUYỆT (không phải từ model) =====
#
# Submit Status của eLIS là quyết định của người chấm thật — chính là thứ hệ
# thống sinh ra để thay thế. Dùng nó làm gt_verdict KHÔNG vòng tròn, và trả
# lời đúng câu "thay người bằng máy thì kết quả có giống không".

def test_rut_verdict_va_ly_do_tu_note():
    note = "excel_row=2 | elis=REJECTED | lý do người duyệt: CB log trùng khóa học"
    assert run_eval._verdict_nguoi_duyet(note) == "REJECTED"
    assert run_eval._ly_do_nguoi_duyet(note) == "CB log trùng khóa học"


def test_note_khong_co_thong_tin_elis():
    """Bộ dữ liệu sinh từ kho chứng chỉ không có cột đó -> không được đoán bừa."""
    assert run_eval._verdict_nguoi_duyet("excel_row=9") == ""
    assert run_eval._ly_do_nguoi_duyet("excel_row=9") == "(không ghi lý do)"


def test_fill_verdict_dien_dung_va_khong_dung_toi_gt_khac(tmp_path):
    from evaluation.dataset import write_dataset
    f = tmp_path / "bo.csv"
    write_dataset([
        _ca("001", note="excel_row=2 | elis=APPROVED"),
        _ca("002", note="excel_row=3 | elis=REJECTED | lý do người duyệt: log trùng"),
        _ca("003", note="excel_row=4"),           # không có thông tin eLIS
    ], f)

    n = run_eval.fill_verdict_from_elis(f)

    from evaluation.dataset import read_dataset
    ra = {c.case_id: c for c in read_dataset(f)}
    assert n == 2
    assert ra["001"].gt_verdict == "APPROVED"
    assert ra["002"].gt_verdict == "REJECTED"
    assert ra["003"].gt_verdict is None, "đoán bừa nhãn cho ca không có dữ liệu"
    # Chỉ đụng gt_verdict, KHÔNG đụng nhãn trích xuất — hai câu hỏi khác nhau.
    assert ra["001"].gt_recipient_name is None


@pytest.mark.parametrize("ly_do,ten,trong_pham_vi", [
    ("CB log trùng khóa học", "Nộp trùng khóa", False),
    ("log trùng", "Nộp trùng khóa", False),
    ("CB log double khóa học", "Nộp trùng khóa", False),
    ("FIS HR tự động xuất Udemy và ghi nhận cho CB", "HR/hệ thống đã tự ghi nhận", False),
    ("Hệ thống tự động đồng bộ kết quả từ FPT Elearning",
     "HR/hệ thống đã tự ghi nhận", False),
    ("Khóa thi trang Elearning FIS không cần submit ELIS",
     "HR/hệ thống đã tự ghi nhận", False),
    ("khóa không thuộc danh mục quy đổi MOOC", "Khóa ngoài danh mục MOOC", False),
    ("Chứng chỉ thiếu thời gian hoàn thành, CB vui lòng bổ sung thêm",
     "Chứng chỉ thiếu thời gian hoàn thành", True),
])
def test_phan_loai_ly_do_nguoi_duyet(ly_do, ten, trong_pham_vi):
    """Cùng một chuyện có chục cách viết tay — phải về cùng một nhóm."""
    assert run_eval._nhom_ly_do(ly_do) == (ten, trong_pham_vi)


def test_ly_do_la_thi_coi_la_TRONG_pham_vi():
    """Không nhận ra thì phải nghiêng về 'lỗi thật', không phải 'tha'.

    Đoán nhầm thành 'ngoài phạm vi' là lặng lẽ tha cho một lỗi thật của hệ
    thống — hướng sai nguy hiểm hơn hẳn hướng ngược lại.
    """
    ten, trong_pham_vi = run_eval._nhom_ly_do("một lý do chưa từng gặp")
    assert trong_pham_vi is True
    assert "Khác" in ten


def test_bang_bat_dong_tach_trong_va_ngoai_pham_vi(capsys):
    """Bảng phải tách rõ 'lỗi thật' với 'khoảng cách phạm vi'.

    Trộn hai thứ là lý do một con số như 'REJECTED recall 10%' bị đọc thành
    'AI đọc chứng chỉ kém' trong khi sự thật là 'AI không kiểm trùng lặp'.
    """
    class KqGia:
        total = 3
        wrong_cases = [
            ("001", "REJECTED", "APPROVED", "", "llm1"),
            ("002", "REJECTED", "APPROVED", "", "llm1"),
            ("003", "REJECTED", "APPROVED", "", "llm1"),
        ]

    cases = [
        _ca("001", note="elis=REJECTED | lý do người duyệt: CB log trùng khóa"),
        _ca("002", note="elis=REJECTED | lý do người duyệt: FIS HR tự động xuất Udemy"),
        _ca("003", note="elis=REJECTED | lý do người duyệt: Chứng chỉ thiếu thời gian hoàn thành"),
    ]
    run_eval._bang_bat_dong(cases, KqGia())

    ra = capsys.readouterr().out
    assert "2 ca ngoài phạm vi" in ra
    assert "1 ca TRONG phạm vi" in ra
    assert "Nộp trùng khóa" in ra


# ===== Ghi file không được làm mất nhãn đã gán =====
#
# File nhãn chứa hàng giờ công gán tay. Mở bằng "w" là truncate ngay lập tức,
# nên lỗi giữa chừng (Excel khóa file, hết đĩa, Ctrl+C) sẽ để lại file rỗng
# hoặc mất một nửa, không có bản nào quay lại.

def test_ghi_that_bai_thi_file_cu_van_nguyen(tmp_path, monkeypatch):
    """Đây là test chống mất dữ liệu, không phải test tiện lợi."""
    from evaluation import dataset as ds

    f = tmp_path / "bo.csv"
    ds.write_dataset([_ca("001", gt_recipient_name="Bùi Đức Hòa",
                          gt_verdict="APPROVED")], f)
    truoc = f.read_text(encoding="utf-8-sig")

    # Mô phỏng Excel đang khóa file: os.replace hỏng ở bước cuối.
    def khoa(a, b):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(ds.os, "replace", khoa)

    with pytest.raises(PermissionError) as e:
        ds.write_dataset([_ca("002")], f)

    assert f.read_text(encoding="utf-8-sig") == truoc, "đã phá file cũ khi ghi hỏng"
    assert "EXCEL" in str(e.value).upper(), "không chỉ ra nguyên nhân hay gặp nhất"
    assert not list(tmp_path.glob("*.tmp")), "để lại file tạm"


def test_ghi_hong_giua_chung_khong_de_lai_file_tam(tmp_path, monkeypatch):
    from evaluation import dataset as ds
    f = tmp_path / "bo.csv"

    def no(*a, **kw):
        raise OSError("hết đĩa")
    monkeypatch.setattr(ds.os, "replace", no)

    with pytest.raises(OSError):
        ds.write_dataset([_ca("001")], f)
    assert not list(tmp_path.glob("*.tmp"))


def test_last_run_bi_khoa_thi_khong_lam_chet_ca_luot_chay(tmp_path, monkeypatch, capsys):
    """Tới bước này tiền LLM đã tiêu — không được vứt cả lượt chạy vì file phụ."""
    out = tmp_path / "last_run.csv"

    goc = pathlib.Path.open

    def gia(self, *a, **kw):
        if self.name == "last_run.csv":
            raise PermissionError(13, "Permission denied")
        return goc(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "open", gia)
    run_eval._ghi_ket_qua_tho([_ca("001")], {"001": None}, {}, out)   # không được ném

    assert "KHÔNG ghi được last_run.csv" in capsys.readouterr().out

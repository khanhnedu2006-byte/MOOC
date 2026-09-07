"""Test bảng đối chiếu HUMAN vs AI (test_export_compare).

Hai thứ file này PHẢI làm đúng, và cả hai đều hỏng lặng lẽ nếu sai:

1. Chép NOTE viết tay sang bản mới theo (tên NV + tên khóa), KHÔNG theo
   case_id. case_id đánh số theo thứ tự dòng Excel, nên bộ dữ liệu đổi là
   cùng một mã ứng với người khác — chép theo nó là dán ghi chú sai người,
   file vẫn đủ dòng vẫn có chữ, không có triệu chứng nào.

2. Dòng chưa có ảnh phải để TRỐNG cột AI. Điền bừa vào đó là bịa ra một kết
   luận mà hệ thống chưa từng đưa ra.
"""

import csv
import pathlib
import sys

import pytest

GOC = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GOC))
sys.path.insert(0, str(GOC / "src"))

from evaluation import export_compare                  # noqa: E402


def _excel(path, rows):
    """rows: [(email, ho_ten, ten_khoa, submit_status)]"""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Employee Email", "Employee Name", "Course Name", "Submit Status"])
    for r in rows:
        ws.append(list(r))
    wb.save(path)


def _anh(thu_muc, *name):
    thu_muc.mkdir(parents=True, exist_ok=True)
    for t in name:
        (thu_muc / t).write_bytes(b"")
    return thu_muc


def _bo(tmp_path, eval_rows=(), run_rows=()):
    label = tmp_path / "eval_set.csv"
    with label.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "input_employee_name", "input_course_name"])
        w.writerows(eval_rows)
    result = tmp_path / "last_run.csv"
    with result.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "verdict"])
        w.writerows(run_rows)
    return label, result


def _doc(path):
    return list(csv.DictReader(path.open(encoding="utf-8-sig")))


# ===== In ĐỦ mọi dòng Excel =====

def test_in_du_moi_dong_ke_ca_dong_chua_co_anh(tmp_path):
    xl = tmp_path / "d.xlsx"
    _excel(xl, [("hoabd3@fpt.com", "Bùi Đức Hòa", "ISO 27001", "APPROVED"),
                ("anv2@fpt.com", "Nguyễn Văn A", "AI Trends", "REJECTED"),
                ("khac@fpt.com", "Trần Văn C", "Khóa chưa nộp", "APPROVED")])
    anh = _anh(tmp_path / "anh", "HOABD3_ISO 27001.jpg", "ANV2_AI Trends.jpg")
    label, result = _bo(tmp_path,
                   [["001_hoabd3", "Bùi Đức Hòa", "ISO 27001"],
                    ["002_anv2", "Nguyễn Văn A", "AI Trends"]],
                   [["001_hoabd3", "APPROVED"], ["002_anv2", "APPROVED"]])
    ra = tmp_path / "compare.csv"

    tk = export_compare.build(xl, anh, label, result, ra)

    assert tk["tong"] == 3, "bỏ mất dòng — file không còn khớp 1-1 với Excel"
    assert (tk["co_anh"], tk["khong_anh"]) == (2, 1)
    assert [r["input_employee_name"] for r in _doc(ra)] == [
        "Bùi Đức Hòa", "Nguyễn Văn A", "Trần Văn C"], "sai thứ tự dòng Excel"


def test_dong_khong_anh_de_TRONG_cot_AI(tmp_path):
    """Trống = chưa xử lý. Điền bừa là bịa ra kết luận chưa từng có.

    Bộ đánh giá CÒN kết quả cũ cho đúng ca này (đợt trước có ảnh, đợt này
    không). Nếu code cứ thế lấy verdict cũ ra điền thì dòng chưa hề được xử
    lý lần này lại mang một kết luận trông như thật.
    """
    xl = tmp_path / "d.xlsx"
    _excel(xl, [("khac@fpt.com", "Trần Văn C", "Khóa chưa nộp", "APPROVED")])
    anh = _anh(tmp_path / "anh", "AI_KHAC_Khóa nào đó.jpg")
    label, result = _bo(tmp_path,
                   [["009_khac", "Trần Văn C", "Khóa chưa nộp"]],
                   [["009_khac", "REJECTED"]])
    ra = tmp_path / "compare.csv"

    export_compare.build(xl, anh, label, result, ra)

    d = _doc(ra)[0]
    assert d["AI"] == ""
    assert d["NOTE"] == export_compare.GHI_CHU_KHONG_ANH
    assert d["HUMAN"] == "APPROVED", "HUMAN lấy từ Submit Status, không cần ảnh"
    assert d["case_id"] == "", "còn case_id thì người đọc tưởng ca này đã chạy"


def test_dong_khong_anh_khong_tinh_vao_so_ca_lech(tmp_path):
    """AI trống thì không thể 'lệch' với HUMAN — đếm vào là thổi phồng lỗi."""
    xl = tmp_path / "d.xlsx"
    _excel(xl, [("khac@fpt.com", "Trần Văn C", "Khóa chưa nộp", "REJECTED")])
    label, result = _bo(tmp_path)
    tk = export_compare.build(xl, _anh(tmp_path / "anh", "X_Y.jpg"),
                              label, result, tmp_path / "c.csv")
    assert tk["lech"] == 0


# ===== Chép NOTE viết tay =====

def test_chep_note_theo_TEN_chu_khong_theo_case_id(tmp_path):
    """Bộ dữ liệu đổi -> case_id 001 ứng với NGƯỜI KHÁC. Ghi chú phải đi theo người.

    Đây là test quan trọng nhất file. Chép theo case_id thì ghi chú của Hòa
    nhảy sang Nguyên, và không có cách nào phát hiện.
    """
    ra = tmp_path / "compare.csv"
    with ra.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(export_compare.COLUMNS)
        w.writerow(["001_hoabd3", "Bùi Đức Hòa", "ISO 27001",
                    "APPROVED", "APPROVED", "ghi chú CỦA HÒA"])

    # Bộ mới: Hòa tụt xuống dòng 2, dòng 1 là người khác.
    xl = tmp_path / "d.xlsx"
    _excel(xl, [("anv2@fpt.com", "Nguyễn Văn A", "AI Trends", "APPROVED"),
                ("hoabd3@fpt.com", "Bùi Đức Hòa", "ISO 27001", "APPROVED")])
    anh = _anh(tmp_path / "anh", "ANV2_AI Trends.jpg", "HOABD3_ISO 27001.jpg")
    label, result = _bo(tmp_path,
                   [["001_anv2", "Nguyễn Văn A", "AI Trends"],
                    ["002_hoabd3", "Bùi Đức Hòa", "ISO 27001"]],
                   [["001_anv2", "APPROVED"], ["002_hoabd3", "APPROVED"]])

    export_compare.build(xl, anh, label, result, ra)

    theo_ten = {r["input_employee_name"]: r["NOTE"] for r in _doc(ra)}
    assert theo_ten["Bùi Đức Hòa"] == "ghi chú CỦA HÒA"
    assert theo_ten["Nguyễn Văn A"] == "", "ghi chú nhảy sang nhầm người"


def test_bao_so_note_mo_coi(tmp_path):
    """Ghi chú không còn dòng tương ứng phải được BÁO, không im lặng bỏ."""
    ra = tmp_path / "compare.csv"
    with ra.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(export_compare.COLUMNS)
        w.writerow(["001_x", "Người Đã Rời", "Khóa cũ", "APPROVED", "APPROVED", "ghi chú cũ"])

    xl = tmp_path / "d.xlsx"
    _excel(xl, [("hoabd3@fpt.com", "Bùi Đức Hòa", "ISO 27001", "APPROVED")])
    label, result = _bo(tmp_path)

    tk = export_compare.build(xl, _anh(tmp_path / "anh", "X_Y.jpg"), label, result, ra)

    assert tk["note_mo_coi"] == 1
    assert tk["note_giu"] == 0


def test_ghi_chu_viet_tay_thang_ghi_chu_tu_sinh(tmp_path):
    """Người đã xem rồi thì lời của họ có giá trị hơn câu 'không có ảnh'."""
    ra = tmp_path / "compare.csv"
    with ra.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(export_compare.COLUMNS)
        w.writerow(["", "Trần Văn C", "Khóa chưa nộp", "APPROVED", "",
                    "CB đã nộp qua email, HR xác nhận"])

    xl = tmp_path / "d.xlsx"
    _excel(xl, [("khac@fpt.com", "Trần Văn C", "Khóa chưa nộp", "APPROVED")])
    label, result = _bo(tmp_path)

    export_compare.build(xl, _anh(tmp_path / "anh", "X_Y.jpg"), label, result, ra)

    assert _doc(ra)[0]["NOTE"] == "CB đã nộp qua email, HR xác nhận"


def test_khong_chep_lai_ghi_chu_tu_sinh(tmp_path):
    """'không có ảnh' của lần trước không được dính vào dòng nay ĐÃ có ảnh."""
    ra = tmp_path / "compare.csv"
    with ra.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(export_compare.COLUMNS)
        w.writerow(["", "Bùi Đức Hòa", "ISO 27001", "APPROVED", "",
                    export_compare.GHI_CHU_KHONG_ANH])

    xl = tmp_path / "d.xlsx"
    _excel(xl, [("hoabd3@fpt.com", "Bùi Đức Hòa", "ISO 27001", "APPROVED")])
    anh = _anh(tmp_path / "anh", "HOABD3_ISO 27001.jpg")
    label, result = _bo(tmp_path, [["001_hoabd3", "Bùi Đức Hòa", "ISO 27001"]],
                   [["001_hoabd3", "APPROVED"]])

    export_compare.build(xl, anh, label, result, ra)

    assert _doc(ra)[0]["NOTE"] == "", "ghi chú tự sinh còn dính lại"


# ===== Cấu trúc file =====

def test_dung_6_cot_dung_thu_tu(tmp_path):
    """Người dùng đang làm việc trên đúng 6 cột này — đổi là bắt họ làm lại."""
    xl = tmp_path / "d.xlsx"
    _excel(xl, [("hoabd3@fpt.com", "Bùi Đức Hòa", "ISO 27001", "APPROVED")])
    label, result = _bo(tmp_path)
    ra = tmp_path / "c.csv"
    export_compare.build(xl, _anh(tmp_path / "anh", "X_Y.jpg"), label, result, ra)

    with ra.open(encoding="utf-8-sig", newline="") as f:
        assert next(csv.reader(f)) == [
            "case_id", "input_employee_name", "input_course_name",
            "HUMAN", "AI", "NOTE"]


def test_ghi_hong_thi_file_cu_van_nguyen(tmp_path, monkeypatch):
    """File chứa ghi chú viết tay — hỏng giữa chừng không được để lại file cụt."""
    ra = tmp_path / "compare.csv"
    with ra.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(export_compare.COLUMNS)
        w.writerow(["001_x", "Bùi Đức Hòa", "ISO 27001", "APPROVED", "APPROVED", "giữ tôi"])
    truoc = ra.read_text(encoding="utf-8-sig")

    xl = tmp_path / "d.xlsx"
    _excel(xl, [("hoabd3@fpt.com", "Bùi Đức Hòa", "ISO 27001", "APPROVED")])
    label, result = _bo(tmp_path)
    monkeypatch.setattr(export_compare.os, "replace",
                        lambda a, b: (_ for _ in ()).throw(
                            PermissionError(13, "Permission denied")))

    with pytest.raises(PermissionError) as e:
        export_compare.build(xl, _anh(tmp_path / "anh", "X_Y.jpg"), label, result, ra)

    assert ra.read_text(encoding="utf-8-sig") == truoc
    assert "EXCEL" in str(e.value).upper()
    assert not list(tmp_path.glob("*.tmp"))

"""Xuất bảng Excel các ca hệ thống lệch với người duyệt (evaluation.export_errors).

    python -m evaluation.run_eval export-errors

Dùng để mang đi trao đổi với HR: mỗi ca một dòng, có sẵn cột trống để HR ghi ý
kiến.

TÁCH LÀM HAI SHEET THEO HƯỚNG LỆCH, không gộp một bảng. Hai hướng là hai câu
hỏi khác nhau, hỏi HR hai chuyện khác nhau:

  - Máy DUYỆT / người TỪ CHỐI: AI đọc đúng cả ba trường nhưng người duyệt từ
    chối vì luật ngoài phạm vi (nộp trùng, HR đã ghi nhận giờ...). Câu hỏi cho
    HR: luật đó là gì, và lấy dữ liệu ở đâu để hệ thống tự kiểm được?

  - Máy TỪ CHỐI / người DUYỆT: hệ thống chặn oan người làm thật. Câu hỏi cho
    HR: chứng chỉ này có thật sự hợp lệ không? Đây là lỗi của hệ thống.

Gộp chung một bảng thì cột "lý do" mang hai nghĩa khác nhau ở hai nhóm dòng
(lý do người duyệt từ chối / lý do máy từ chối) và người đọc sẽ hiểu nhầm.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from process_data import normalize                      # noqa: E402

FONT = "Arial"

# Ngày AI trả về khi KHÔNG đọc được. '01/01/0001' là giá trị rác model hay
# sinh ra khi bị ép trả về một ngày; chuỗi rỗng là khi nó bỏ trống.
NGAY_KHONG_DOC_DUOC = ("", "01/01/0001", "0001-01-01")

COT = [
    ("case_id", 14),
    ("Mã NV", 13),
    ("Tên NV (eLIS)", 22),
    ("Tên khóa học (eLIS)", 44),
    ("File chứng chỉ", 40),
    ("Người duyệt", 12),
    ("Lý do người duyệt", 40),
    ("Máy kết luận", 12),
    ("Lý do máy", 40),
    ("Tầng", 14),
    ("AI đọc: tên", 24),
    ("AI đọc: tên khóa học", 40),
    ("AI đọc: ngày", 15),
    ("Nguyên nhân (phân tích)", 46),
    ("Ý KIẾN HR", 34),
    ("KẾT LUẬN THỐNG NHẤT", 22),
]
COT_HR_DAU = 15          # cột O — từ đây trở đi là chỗ HR điền


def _giong_email_hoac_username(text: str) -> bool:
    """Chuỗi trông như email/username chứ không phải tên người."""
    t = (text or "").strip()
    return bool(t) and ("@" in t or (" " not in t and any(c.isdigit() for c in t)))


# Ngưỡng để nói "hai tên khóa chỉ lệch vài từ" thay vì "khác hẳn".
#
# Đo bằng |giao| / |tập nhỏ hơn| chứ không phải "có từ nào chung không". Lý do:
# hai khóa hoàn toàn khác nhau vẫn dễ chung vài từ phổ thông —
#   'Storytelling trong bán hàng trên kênh cá nhân'
#   'KỸ NĂNG BÁN HÀNG BẰNG CÂU CHUYỆN'
# chung {bán, hàng} nhưng là hai khóa khác nhau. Chỉ cần "có từ chung" là đủ
# thì ca đó bị dán nhãn "lệch vài từ", và bảng gửi HR sẽ khẳng định sai.
#
# 0.5 giữ được ca chỉ lệch một chữ trong tên hai từ ('AIoT Foundations' vs
# 'AloT Foundations' -> 1/2), đồng thời loại ca trên (2/7 = 0.29).
NGUONG_TRUNG = 0.5


def _trung_nhieu(a: set, b: set) -> bool:
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= NGUONG_TRUNG


def nguyen_nhan_tu_choi_oan(e: dict, r: dict) -> str:
    """Phân tích VÌ SAO hệ thống chặn oan một ca, dựa trên dữ liệu thật.

    Chỉ suy ra từ những gì đo được (chuỗi eLIS gửi vs chuỗi AI đọc). Ca không
    quy được về mẫu nào thì ghi rõ "cần mở ảnh kiểm tra" chứ KHÔNG đoán bừa —
    bảng này sẽ đi tới HR, một dòng đoán sai ở đây là một lần mất uy tín.
    """
    ly_do = r.get("reason") or ""
    ra = []

    if "Ngày không hợp lệ" in ly_do:
        if (r.get("issue_date") or "").strip() in NGAY_KHONG_DOC_DUOC:
            ra.append("Không đọc được ngày, hệ thống đang xử như ngày sai")
        else:
            ra.append("Ngày đọc được nhưng ngoài khoảng quy định")

    if "Tên không khớp" in ly_do:
        ai = r.get("recipient_name") or ""
        elis = e.get("input_employee_name") or ""
        tu_ai, tu_elis = set(normalize(ai).split()), set(normalize(elis).split())
        if _giong_email_hoac_username(ai):
            ra.append("Chứng chỉ in email/username thay cho họ tên")
        elif tu_ai and tu_ai < tu_elis:
            ra.append("Chứng chỉ bỏ bớt tên đệm (luật so tên đòi khớp đủ mọi từ)")
        elif tu_ai and tu_elis and not (tu_ai & tu_elis):
            ra.append("Tên đọc ra KHÁC HẲN — cần mở ảnh kiểm tra, có thể nộp nhầm")
        else:
            ra.append("Tên lệch cách viết — cần mở ảnh kiểm tra")

    if "Tên khóa học không khớp" in ly_do:
        ai = normalize(r.get("certificate_name") or "")
        elis = normalize(e.get("input_course_name") or "")
        tu_ai, tu_elis = set(ai.split()), set(elis.split())
        if tu_ai and tu_elis and (tu_ai <= tu_elis or tu_elis <= tu_ai):
            ra.append("Một bên là tên rút gọn của bên kia (song ngữ / thiếu hậu tố)")
        elif _trung_nhieu(tu_ai, tu_elis):
            ra.append("Tên khóa lệch vài từ (viết tắt, nhầm chữ I/l, khác bản dịch)")
        else:
            ra.append("Tên khóa KHÁC HẲN — cần mở ảnh kiểm tra")

    return "; ".join(ra) if ra else (ly_do or "(không rõ)")


def _doc(file_nhan: Path, file_ket_qua: Path) -> tuple[dict, dict]:
    if not file_ket_qua.is_file():
        raise FileNotFoundError(
            f"Chưa có {file_ket_qua.name}. Chạy trước:\n"
            f"    python -m evaluation.run_eval run"
        )
    ev = {r["case_id"]: r
          for r in csv.DictReader(file_nhan.open(encoding="utf-8-sig"))}
    run = {r["case_id"]: r
           for r in csv.DictReader(file_ket_qua.open(encoding="utf-8-sig"))}
    return ev, run


def _dong(cid, e, r, nguyen_nhan, ly_do_nguoi) -> list:
    return [
        cid,
        e.get("input_employee_code", ""),
        e.get("input_employee_name", ""),
        e.get("input_course_name", ""),
        Path(e.get("image_path", "")).name,
        e.get("gt_verdict", ""),
        ly_do_nguoi,
        r.get("verdict", ""),
        r.get("reason", ""),
        r.get("stage", ""),
        r.get("recipient_name", ""),
        r.get("certificate_name", ""),
        r.get("issue_date", ""),
        nguyen_nhan,
        "",          # Ý KIẾN HR
        "",          # KẾT LUẬN THỐNG NHẤT
    ]


def build(file_nhan, file_ket_qua, file_ra) -> tuple[int, int]:
    """Dựng workbook. Trả về (số ca máy-duyệt-người-từ-chối, số ca ngược lại)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    from evaluation.run_eval import _ly_do_nguoi_duyet, _nhom_ly_do

    ev, run = _doc(Path(file_nhan), Path(file_ket_qua))

    duyet_oan, tu_choi_oan = [], []
    for cid, e in ev.items():
        r = run.get(cid)
        if not r or not r.get("verdict"):
            continue
        that, may = e.get("gt_verdict", ""), r["verdict"]
        if that == may:
            continue
        ly_do_nguoi = _ly_do_nguoi_duyet(e.get("note", ""))
        if that == "REJECTED" and may == "APPROVED":
            nhom, _ = _nhom_ly_do(ly_do_nguoi)
            duyet_oan.append(_dong(cid, e, r, f"Ngoài phạm vi: {nhom}", ly_do_nguoi))
        elif that == "APPROVED" and may == "REJECTED":
            tu_choi_oan.append(
                _dong(cid, e, r, nguyen_nhan_tu_choi_oan(e, r),
                      ly_do_nguoi if ly_do_nguoi != "(không ghi lý do)"
                      else "(người duyệt CHẤP NHẬN nên không có lý do)"))
    duyet_oan.sort(key=lambda x: x[0])
    tu_choi_oan.sort(key=lambda x: x[0])

    dam = Font(name=FONT, bold=True, color="FFFFFF")
    thuong = Font(name=FONT, size=10)
    nen_dau = PatternFill("solid", fgColor="2A4E7C")
    nen_hr = PatternFill("solid", fgColor="FFF2CC")       # vàng = chỗ HR điền
    vien = Border(*[Side(style="thin", color="BFBFBF")] * 4)
    tren_trai = Alignment(vertical="top", wrap_text=True)

    wb = Workbook()
    tong_quan = wb.active
    tong_quan.title = "Tổng quan"

    def _sheet(ten, rows, mo_ta):
        ws = wb.create_sheet(ten)
        ws["A1"] = mo_ta
        ws["A1"].font = Font(name=FONT, bold=True, size=11)
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(COT))
        ws.row_dimensions[1].height = 32
        ws["A1"].alignment = Alignment(vertical="center", wrap_text=True)

        for i, (ten_cot, rong) in enumerate(COT, start=1):
            o = ws.cell(row=2, column=i, value=ten_cot)
            o.font = dam
            o.fill = nen_hr if i >= COT_HR_DAU else nen_dau
            if i >= COT_HR_DAU:
                o.font = Font(name=FONT, bold=True)
            o.alignment = Alignment(vertical="center", wrap_text=True)
            o.border = vien
            ws.column_dimensions[get_column_letter(i)].width = rong

        for so, row in enumerate(rows, start=3):
            for i, gia_tri in enumerate(row, start=1):
                o = ws.cell(row=so, column=i, value=gia_tri)
                o.font = thuong
                o.alignment = tren_trai
                o.border = vien
                if i >= COT_HR_DAU:
                    o.fill = nen_hr

        ws.freeze_panes = "A3"
        ws.auto_filter.ref = (f"A2:{get_column_letter(len(COT))}"
                              f"{max(2, len(rows) + 2)}")
        return ws

    _sheet("Máy duyệt - Người từ chối", duyet_oan,
           "AI đọc ĐÚNG cả ba trường (tên, khóa học, ngày) nhưng người duyệt vẫn từ "
           "chối vì luật nằm NGOÀI phạm vi hệ thống. Câu hỏi cho HR: luật đó là gì, "
           "và hệ thống lấy dữ liệu ở đâu để tự kiểm được?")
    _sheet("Máy từ chối - Người duyệt", tu_choi_oan,
           "Hệ thống CHẶN OAN người làm thật — đây là lỗi của hệ thống, không phải "
           "của học viên. Câu hỏi cho HR: chứng chỉ này có thật sự hợp lệ không?")

    # ===== Sheet tổng quan =====
    tq = tong_quan
    tq.column_dimensions["A"].width = 52
    tq.column_dimensions["B"].width = 12
    tq.column_dimensions["C"].width = 62

    def _o(vt, gia_tri, bold=False, size=10):
        c = tq[vt]
        c.value = gia_tri
        c.font = Font(name=FONT, bold=bold, size=size)
        c.alignment = Alignment(vertical="top", wrap_text=True)
        return c

    _o("A1", "Các ca hệ thống MOOC lệch với người duyệt", True, 14)
    _o("A2", f"Nguồn: {Path(file_nhan).name} + {Path(file_ket_qua).name}. "
             f"Nhãn chuẩn lấy từ cột Submit Status của eLIS (quyết định của "
             f"người duyệt), không phải do AI tự gán.")
    tq.merge_cells("A2:C2")
    tq.row_dimensions[2].height = 30

    _o("A4", "Nhóm", True)
    _o("B4", "Số ca", True)
    _o("C4", "Nghĩa là gì", True)

    # Dùng CÔNG THỨC chứ không viết số cứng: HR lọc hay xóa dòng thì số tự đổi
    # theo, không lệch với bảng bên dưới.
    _o("A5", "Máy duyệt / Người từ chối")
    tq["B5"] = "=COUNTA('Máy duyệt - Người từ chối'!A3:A1000)"
    _o("C5", "AI làm đúng việc của nó; thiếu luật nghiệp vụ ngoài phạm vi.")

    _o("A6", "Máy từ chối / Người duyệt")
    tq["B6"] = "=COUNTA('Máy từ chối - Người duyệt'!A3:A1000)"
    _o("C6", "Lỗi thật của hệ thống — chặn oan người làm thật.")

    _o("A7", "TỔNG", True)
    tq["B7"] = "=B5+B6"
    for vt in ("B5", "B6", "B7"):
        tq[vt].font = Font(name=FONT, bold=(vt == "B7"), size=10)
    _o("A7", "TỔNG", True)

    _o("A9", "HƯỚNG DẪN ĐIỀN", True, 12)
    _o("A10", "Chỉ điền hai cột NỀN VÀNG ở cuối mỗi sheet:")
    _o("A11", "  • Ý KIẾN HR")
    _o("C11", "HR ghi nhận xét / xác nhận cho ca đó.")
    _o("A12", "  • KẾT LUẬN THỐNG NHẤT")
    _o("C12", "Chốt lại: giữ nguyên / sửa hệ thống / thêm luật.")
    _o("A13", "Các cột còn lại là dữ liệu đo được, xin đừng sửa — sửa vào đó thì "
              "lần chạy sau sẽ ghi đè và mất.")
    tq.merge_cells("A13:C13")
    tq.row_dimensions[13].height = 28

    _o("A15", "Ví dụ một dòng đã điền", True)
    _o("A16", "Ý KIẾN HR")
    _o("C16", "Khóa này HR đã ghi nhận giờ tự động từ Udemy nên CB không cần "
              "submit. Cần lấy danh sách khóa đồng bộ tự động từ hệ thống HR.")
    _o("A17", "KẾT LUẬN THỐNG NHẤT")
    _o("C17", "Thêm luật: bỏ qua khóa đã đồng bộ tự động.")
    for h in (16, 17):
        tq.cell(row=h, column=3).fill = nen_hr

    _o("A19", "LƯU Ý BẢO MẬT", True)
    _o("A20", "File này chứa họ tên và mã nhân viên thật. Chỉ chia sẻ trong nội bộ "
              "FPT với người có nhu cầu; không đưa ra ngoài, không đính kèm email "
              "cá nhân.")
    tq.merge_cells("A20:C20")
    tq.row_dimensions[20].height = 30

    Path(file_ra).parent.mkdir(parents=True, exist_ok=True)
    wb.save(file_ra)
    return len(duyet_oan), len(tu_choi_oan)

"""Xuất bảng đối chiếu HUMAN vs AI (evaluation.export_compare).

    python -m evaluation.run_eval export-compare

Đúng 6 cột, đúng thứ tự, không thêm không bớt:

    case_id | input_employee_name | input_course_name | HUMAN | AI | NOTE

CSV THUẦN, KHÔNG tô màu, KHÔNG gộp ô, KHÔNG chia sheet. Đây là định dạng
người dùng đang làm việc trên đó; đổi cấu trúc là bắt họ làm lại từ đầu.

GHI ĐỦ MỌI DÒNG CỦA EXCEL, kể cả dòng chưa có ảnh để xử lý. Lý do: file này
để đặt cạnh Excel gốc mà soi, nên phải khớp 1-1 với nó. Bỏ bớt dòng thì người
đọc không biết dòng nào rơi đi đâu, và dễ tưởng hệ thống đã xử lý hết.

  - Dòng CÓ ảnh    : AI = kết luận của hệ thống (APPROVED/REJECTED).
  - Dòng KHÔNG ảnh : AI để TRỐNG, NOTE ghi "không có ảnh".
    Trống ở đây nghĩa là CHƯA XỬ LÝ — khác hẳn với đã xử lý rồi ra kết quả.
    Điền bừa một giá trị vào đó là bịa ra một kết luận chưa từng có.

HUMAN điền được cho MỌI dòng vì nó lấy từ cột Submit Status của eLIS, không
phụ thuộc việc có ảnh hay không.

GIỮ NGUYÊN CỘT NOTE ĐÃ VIẾT TAY — đây là điểm quan trọng nhất của file này.
NOTE là công người: nhận xét từng ca, không tái tạo được từ dữ liệu. Sinh lại
báo cáo mà ghi đè lên nó là xóa mất thứ đáng giá nhất trong bảng.
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from process_data import normalize                     # noqa: E402

from evaluation import match_images                    # noqa: E402

COLUMNS = ["case_id", "input_employee_name", "input_course_name",
           "HUMAN", "AI", "NOTE"]

GHI_CHU_KHONG_ANH = "không có ảnh"


def khoa_ghi_chu(employee_name: str, course_name: str) -> tuple[str, str]:
    """Khóa để nối các bản với nhau: (tên nhân viên, tên khóa học).

    TUYỆT ĐỐI KHÔNG dùng case_id làm khóa. case_id được đánh số theo thứ tự
    dòng Excel ("001_", "002_"...), nên khi bộ dữ liệu đổi — thêm dòng, bớt
    dòng, đổi thứ tự — cùng một case_id sẽ ứng với NGƯỜI KHÁC. Chép NOTE theo
    case_id lúc đó là dán ghi chú của người này sang người kia, và không có
    triệu chứng nào để phát hiện: file vẫn đủ dòng, vẫn có ghi chú, chỉ là
    ghi chú sai người.

    Cặp (tên NV, tên khóa) không đổi khi đánh số lại, nên nó là khóa đúng.
    """
    return (normalize(employee_name), normalize(course_name))


def doc_note_cu(path: str | Path) -> dict[tuple[str, str], str]:
    """Đọc cột NOTE của bản cũ, khóa theo (tên NV, tên khóa) — xem khoa_ghi_chu.

    File chưa có, hỏng, hay thiếu cột NOTE thì trả về dict rỗng — không được
    làm chết cả lệnh chỉ vì không đọc lại được ghi chú.

    Bỏ qua ghi chú tự sinh ("không có ảnh"): đó là thứ hàm này tự điền lại
    mỗi lần chạy, chép lại chỉ khiến nó dính vào dòng nay đã có ảnh.

    Bỏ qua dòng có mọi ô trống: file lưu từ Excel kèm cả triệu dòng trống.
    """
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            ra = {}
            for row in csv.DictReader(f):
                ten = (row.get("input_employee_name") or "").strip()
                khoa = (row.get("input_course_name") or "").strip()
                note = (row.get("NOTE") or "").strip()
                if not note or not (ten or khoa):
                    continue
                if note == GHI_CHU_KHONG_ANH:
                    continue
                ra[khoa_ghi_chu(ten, khoa)] = note
            return ra
    except (OSError, csv.Error):
        return {}


def _doc_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def build(excel, images_dir, file_nhan, file_ket_qua, file_ra,
          col_email=None, col_course=None, col_name=None) -> dict:
    """Dựng bảng đối chiếu từ TOÀN BỘ dòng Excel.

    Trả về dict thống kê để nơi gọi in ra: tong / co_anh / khong_anh / lech /
    note_giu / note_mo_coi.
    """
    file_nhan, file_ket_qua = Path(file_nhan), Path(file_ket_qua)
    file_ra = Path(file_ra)

    rows, _ = match_images.read_excel(excel, col_email, col_course, col_name)
    images = match_images.list_images(images_dir)
    ket_qua_ghep = match_images.match(rows, images)
    co_anh = {khoa_ghi_chu(r["employee_name"], r["course_name"])
              for r, _ in ket_qua_ghep["matched"]}

    # Nối (tên NV, tên khóa) -> kết luận AI, đi vòng qua eval_set để lấy case_id.
    ai_theo_khoa, case_id_theo_khoa = {}, {}
    if file_nhan.is_file() and file_ket_qua.is_file():
        verdict_theo_case = {r["case_id"]: (r.get("verdict") or "").strip()
                             for r in _doc_csv(file_ket_qua) if r.get("case_id")}
        for e in _doc_csv(file_nhan):
            cid = (e.get("case_id") or "").strip()
            if not cid:
                continue
            k = khoa_ghi_chu(e.get("input_employee_name", ""),
                             e.get("input_course_name", ""))
            case_id_theo_khoa[k] = cid
            ai_theo_khoa[k] = verdict_theo_case.get(cid, "")

    note_cu = doc_note_cu(file_ra)

    dong, lech, da_dung, so_khong_anh = [], 0, set(), 0
    for r in rows:
        k = khoa_ghi_chu(r["employee_name"], r["course_name"])
        human = (r.get("elis_status") or "").strip().upper()
        ai = ai_theo_khoa.get(k, "")

        note = note_cu.get(k, "")
        if k in note_cu:
            da_dung.add(k)
        case_id = case_id_theo_khoa.get(k, "")
        if k not in co_anh:
            so_khong_anh += 1
            # Ghi chú viết tay được ưu tiên: người đã xem rồi thì lời của họ
            # có giá trị hơn câu tự sinh.
            note = note or GHI_CHU_KHONG_ANH
            ai = ""                       # chưa xử lý -> KHÔNG bịa kết luận
            # XÓA luôn case_id. Có case_id nghĩa là "ca này đã vào bộ đánh
            # giá"; để lại một mã cũ sót từ đợt trước trên dòng chưa hề xử lý
            # là nói sai, và người đọc sẽ đi tra mã đó trong last_run.csv.
            case_id = ""

        if human and ai and human != ai:
            lech += 1

        dong.append([case_id, r["employee_name"], r["course_name"],
                     human, ai, note])

    # Giữ nguyên THỨ TỰ DÒNG CỦA EXCEL để đặt cạnh file gốc mà soi từng dòng.
    # (rows đã theo thứ tự đọc từ sheet, nên không sắp lại.)

    tam = file_ra.with_name(file_ra.name + ".tmp")
    file_ra.parent.mkdir(parents=True, exist_ok=True)
    try:
        # utf-8-sig: Excel trên Windows cần BOM mới hiện đúng tiếng Việt.
        with tam.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(COLUMNS)
            w.writerows(dong)
        os.replace(tam, file_ra)
    except PermissionError as e:
        tam.unlink(missing_ok=True)
        raise PermissionError(
            f"Không ghi được {file_ra.name}: file đang bị khóa.\n"
            f"Nguyên nhân hay gặp nhất: file đang MỞ TRONG EXCEL. "
            f"Đóng Excel rồi chạy lại.\n"
            f"(File cũ vẫn còn nguyên, không mất ghi chú nào.)"
        ) from e
    except Exception:
        tam.unlink(missing_ok=True)
        raise

    return {"tong": len(dong), "co_anh": len(dong) - so_khong_anh,
            "khong_anh": so_khong_anh, "lech": lech,
            "note_giu": len(da_dung), "note_mo_coi": len(note_cu) - len(da_dung)}

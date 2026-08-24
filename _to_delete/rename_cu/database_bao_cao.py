"""Soạn báo cáo ngày từ bảng log (bao_cao).

Đọc SQLite, gom số liệu một ngày, trả về dict thuần. KHÔNG gửi mail, KHÔNG
dựng HTML — hai việc đó nằm ở gui_bao_cao.py. Tách ra để test được số liệu
mà không cần mạng, và để đổi cách gửi mà không phải sửa chỗ tính toán.

Dùng:
    from database import bao_cao
    so_lieu = bao_cao.bao_cao_ngay("2026-08-17")
    so_lieu = bao_cao.bao_cao_ngay()        # mặc định: HÔM QUA
"""

import sqlite3
from datetime import date, timedelta

from database.database import DB_PATH, _ket_noi, khoi_tao

# Số ca lỗi tối đa liệt kê chi tiết trong báo cáo. Hỏng hàng loạt thì
# email dài vô ích — biết 200 cái cùng lỗi là đủ, không cần đọc hết 200.
GIOI_HAN_CA_LOI = 30


def _ngay_mac_dinh() -> str:
    """Hôm qua, dạng YYYY-MM-DD.

    Báo cáo thường chạy vào sáng sớm hoặc cuối ngày cho ngày ĐÃ KẾT THÚC,
    nên mặc định là hôm qua chứ không phải hôm nay.
    """
    return (date.today() - timedelta(days=1)).isoformat()


def _dem(conn, ngay, dieu_kien="", tham_so=()):
    sql = "SELECT COUNT(*) FROM log_xu_ly WHERE substr(thoi_diem,1,10)=?"
    if dieu_kien:
        sql += f" AND {dieu_kien}"
    return conn.execute(sql, (ngay, *tham_so)).fetchone()[0]


def bao_cao_ngay(ngay: str | None = None, db_path=None) -> dict:
    """Gom số liệu một ngày. Trả dict thuần, không phụ thuộc cách hiển thị.

    ngay: "YYYY-MM-DD". None -> hôm qua.

    Các con số đều đọc từ cột thoi_diem theo GIỜ ĐỊA PHƯƠNG của máy chạy
    job. Trong Docker, TZ đã đặt Asia/Ho_Chi_Minh nên ranh giới ngày khớp
    giờ Việt Nam. Không có dòng đó thì container chạy UTC và "hôm qua" lệch
    7 tiếng.
    """
    ngay = ngay or _ngay_mac_dinh()
    hom_truoc = (date.fromisoformat(ngay) - timedelta(days=1)).isoformat()

    # Nâng cấp bảng trước khi đọc. Bắt buộc: báo cáo dùng các cột thêm sau
    # (elis_gui_ok, user_course_id...), mà khoi_tao() chỉ được run.py gọi.
    # Chạy gui_bao_cao.py riêng trên máy chưa chạy run.py kể từ lần đổi
    # schema thì sẽ lỗi "no such column". khoi_tao() an toàn khi gọi lại
    # nhiều lần nên đặt ở đây không tốn gì.
    khoi_tao(db_path)

    conn = _ket_noi(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tong = _dem(conn, ngay)
        approved = _dem(conn, ngay, "ket_qua='APPROVED'")
        rejected = _dem(conn, ngay, "ket_qua='REJECTED'")

        # Thất bại kỹ thuật: không đọc được file, không phải do nhân viên
        # khai sai. Tách riêng vì hai loại này cần hành động khác nhau.
        that_bai = _dem(
            conn, ngay,
            "tang_xu_ly IN ('loi_tai_file','khong_co_file','soft_fail_zip','file_loi','loi_he_thong')"
        )

        # Nộp lên ELIS có được nhận không.
        gui_ok = _dem(conn, ngay, "elis_gui_ok=1")
        gui_that_bai = _dem(conn, ngay, "elis_gui_ok=0")
        chua_gui = _dem(conn, ngay, "elis_gui_ok IS NULL")

        # Phân theo tầng xử lý -> biết bao nhiêu lần phải dùng tới Azure.
        theo_tang = {
            r["tang_xu_ly"] or "(không rõ)": r["sl"]
            for r in conn.execute(
                "SELECT tang_xu_ly, COUNT(*) sl FROM log_xu_ly "
                "WHERE substr(thoi_diem,1,10)=? GROUP BY tang_xu_ly "
                "ORDER BY sl DESC", (ngay,))
        }

        # Lý do từ chối, gom nhóm. Nếu 90% cùng một lý do thì nhiều khả năng
        # là lỗi hệ thống chứ không phải nhân viên gian lận.
        ly_do_tu_choi = [
            {"ly_do": _rut_gon_ly_do(r["ly_do"]), "so_luong": r["sl"]}
            for r in conn.execute(
                "SELECT ly_do, COUNT(*) sl FROM log_xu_ly "
                "WHERE substr(thoi_diem,1,10)=? AND ket_qua='REJECTED' "
                "GROUP BY ly_do ORDER BY sl DESC LIMIT 10", (ngay,))
        ]

        # Gộp lại các lý do rút gọn trùng nhau.
        gom = {}
        for m in ly_do_tu_choi:
            gom[m["ly_do"]] = gom.get(m["ly_do"], 0) + m["so_luong"]
        ly_do_tu_choi = [{"ly_do": k, "so_luong": v}
                         for k, v in sorted(gom.items(), key=lambda x: -x[1])]

        # Danh sách ca THẤT BẠI kèm id, để mentor tra thẳng trên eLIS.
        # Chỉ liệt kê ca lỗi kỹ thuật và ca bị eLIS từ chối — đó là những
        # thứ cần người can thiệp. Ca APPROVED/REJECTED bình thường chỉ đếm
        # số, vì chúng là kết quả đúng của hệ thống, không cần rà từng cái.
        ca_loi = [
            {
                "user_course_id": r["user_course_id"],
                "employee_id": r["employee_id"],
                "ten_chung_chi": r["ten_chung_chi"],
                "ly_do": r["ly_do"],
                "tang_xu_ly": r["tang_xu_ly"],
                "elis_gui_ok": r["elis_gui_ok"],
                "elis_message": r["elis_message"],
            }
            for r in conn.execute(
                """
                SELECT user_course_id, employee_id, ten_chung_chi, ly_do,
                       tang_xu_ly, elis_gui_ok, elis_message
                FROM log_xu_ly
                WHERE substr(thoi_diem,1,10)=?
                  AND (tang_xu_ly IN ('loi_tai_file','khong_co_file','soft_fail_zip',
                                      'file_loi','loi_he_thong')
                       OR elis_gui_ok = 0)
                ORDER BY id
                LIMIT ?
                """, (ngay, GIOI_HAN_CA_LOI))
        ]
        tong_ca_loi = _dem(
            conn, ngay,
            "(tang_xu_ly IN ('loi_tai_file','khong_co_file','soft_fail_zip',"
            "'file_loi','loi_he_thong') OR elis_gui_ok = 0)")

        tong_hom_truoc = _dem(conn, hom_truoc)
        approved_hom_truoc = _dem(conn, hom_truoc, "ket_qua='APPROVED'")
    finally:
        conn.close()

    return {
        "ngay": ngay,
        "tong": tong,
        "approved": approved,
        "rejected": rejected,
        "that_bai_ky_thuat": that_bai,
        "ty_le_duyet": round(approved / tong * 100, 1) if tong else 0.0,
        "elis": {
            "da_nhan": gui_ok,
            "tu_choi": gui_that_bai,
            "chua_gui": chua_gui,
        },
        "theo_tang": theo_tang,
        "ly_do_tu_choi": ly_do_tu_choi,
        "ca_loi": ca_loi,
        "tong_ca_loi": tong_ca_loi,
        "ca_loi_bi_cat": max(0, tong_ca_loi - len(ca_loi)),
        "hom_truoc": {"tong": tong_hom_truoc, "approved": approved_hom_truoc},
        "chenh_lech": tong - tong_hom_truoc,
        "canh_bao": _canh_bao(tong, that_bai, gui_that_bai, chua_gui, rejected),
    }


def _rut_gon_ly_do(ly_do: str | None) -> str:
    """Rút lý do dài thành nhãn ngắn để gom nhóm.

    Lý do từ pipeline có kèm giá trị cụ thể của từng người (tên, khóa học)
    nên mỗi dòng một khác, gom nguyên văn thì mỗi nhóm chỉ có 1. Cắt lấy
    phần mô tả loại lỗi, bỏ phần giá trị.
    """
    if not ly_do:
        return "(không ghi lý do)"
    # Lý do dạng: 'Tên không khớp — trên ảnh (LLM1): "..." , nhập: "..."'
    nhan = ly_do.split("—")[0].split(":")[0].strip()
    return nhan[:80] if nhan else ly_do[:80]


def _canh_bao(tong, that_bai, gui_that_bai, chua_gui, rejected) -> list[str]:
    """Những điều người đọc cần chú ý, diễn giải sẵn thành câu.

    Mục đích: người nhận mail không phải tự suy luận từ các con số. Một báo
    cáo "0 xử lý" có thể là ngày nghỉ, cũng có thể là job chết — phải nói rõ.
    """
    ra = []
    if tong == 0:
        ra.append("Không xử lý chứng chỉ nào. Có thể không ai nộp, "
                  "cũng có thể job không chạy — nên kiểm tra job còn sống không.")
        return ra

    if that_bai:
        ty_le = that_bai / tong * 100
        ra.append(f"{that_bai}/{tong} chứng chỉ ({ty_le:.0f}%) thất bại vì lỗi "
                  f"kỹ thuật, không phải do nhân viên khai sai.")
        if ty_le >= 50:
            ra.append("Quá nửa số chứng chỉ không đọc được file — nhiều khả "
                      "năng API tải file của eLIS đang hỏng.")

    if gui_that_bai:
        ra.append(f"{gui_that_bai} kết quả bị eLIS từ chối khi nộp. Thường do "
                  f"bản ghi đã được duyệt trước đó, hoặc sai mã nhân viên.")

    if chua_gui:
        ra.append(f"{chua_gui} kết quả chưa nộp được lên eLIS.")

    if tong and rejected / tong >= 0.9 and rejected > 3:
        ra.append(f"Tỷ lệ từ chối rất cao ({rejected}/{tong}). Nếu phần lớn "
                  f"cùng một lý do thì nên xem lại luật đối chiếu.")
    return ra

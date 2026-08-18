"""Ghi log xử lý chứng chỉ vào SQLite (database).

Mỗi lần xử lý một chứng chỉ -> ghi một dòng log: thời điểm, thông tin nhận
diện, kết quả, lý do, tầng xử lý. Dùng để xem lại / kiểm toán sau này.

Dùng sqlite3 có sẵn trong Python — không cần cài server, không thêm thư viện.
Database là một file (.db), mặc định mooc_log.db ở gốc dự án.

Có HAI trường định danh người, đừng nhầm:
  - employee_id  : mã nhân viên do ELIS cấp (vd "00332383"). Dùng để đối
                   soát với dữ liệu nhân sự, và là thứ gửi ngược về ELIS.
  - ma_nhan_vien : username lấy từ employeeEmail (vd "hoabd3"). CHỈ dùng để
                   đối chiếu với tên in trên chứng chỉ, vì nhiều chứng chỉ
                   in username thay cho tên thật.

Dùng:
    from database.database import ghi_log, khoi_tao
    khoi_tao()                              # tạo/nâng cấp bảng khi khởi động
    ghi_log(ket_qua_xu_ly, employee_id="00332383")
"""

import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "mooc_log.db"


def _ket_noi(db_path=None):
    """Mở kết nối tới file SQLite."""
    return sqlite3.connect(str(db_path or DB_PATH))


# Các cột thêm sau khi bảng đã tồn tại ngoài thực tế. Xem giải thích ở
# khoi_tao() về việc vì sao phải liệt kê riêng thay vì chỉ sửa CREATE TABLE.
_COT_THEM_SAU = {
    "employee_id": "TEXT",     # mã NV của ELIS, khác ma_nhan_vien (username)
    "user_course_id": "TEXT",  # id bản ghi, để cập nhật trạng thái gửi sau
    "elis_gui_ok": "INTEGER",  # 1=successList, 0=failList, NULL=chưa gửi
    "elis_message": "TEXT",    # message ELIS trả về khi từ chối
}


def khoi_tao(db_path=None):
    """Tạo bảng log nếu chưa có, và thêm cột mới nếu bảng cũ còn thiếu.

    Vì sao cần phần "thêm cột": CREATE TABLE IF NOT EXISTS chỉ chạy khi bảng
    CHƯA tồn tại. Với máy đã chạy job trước đó, bảng đã có sẵn nên câu lệnh
    đó bị bỏ qua HOÀN TOÀN — thêm cột vào phần CREATE cũng không có tác dụng,
    và chương trình sẽ lỗi "no such column" dù code trông đúng.

    Nên phải hỏi bảng hiện có những cột nào rồi ALTER TABLE thêm phần thiếu.
    Cách này an toàn với cả DB mới lẫn DB đã có dữ liệu — dữ liệu cũ giữ
    nguyên, cột mới nhận giá trị NULL.
    """
    conn = _ket_noi(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS log_xu_ly (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                thoi_diem       TEXT NOT NULL,
                user_course_id  TEXT,
                employee_id     TEXT,
                ma_nhan_vien    TEXT,
                ten_tren_anh    TEXT,
                ten_chung_chi   TEXT,
                ngay_tren_anh   TEXT,
                ket_qua         TEXT NOT NULL,
                ly_do           TEXT,
                tang_xu_ly      TEXT,
                elis_gui_ok     INTEGER,
                elis_message    TEXT
            )
        """)

        dang_co = {r[1] for r in conn.execute("PRAGMA table_info(log_xu_ly)")}
        for ten_cot, kieu in _COT_THEM_SAU.items():
            if ten_cot not in dang_co:
                conn.execute(f"ALTER TABLE log_xu_ly ADD COLUMN {ten_cot} {kieu}")

        conn.commit()
    finally:
        conn.close()


def ghi_log(ket_qua_xu_ly, employee_id=None, user_course_id=None, db_path=None):
    """Ghi một dòng log từ KetQuaXuLy. Trả về id dòng vừa ghi.

    employee_id: mã nhân viên do ELIS cấp (getCert.employeeId). Truyền riêng
    vì KetQuaXuLy không mang theo trường này — nó chỉ giữ ma_nhan_vien
    (username dùng để đối chiếu với ảnh).

    user_course_id: id bản ghi trên ELIS. Cần để sau khi gọi API ③ còn biết
    dòng log nào ứng với item nào mà cập nhật trạng thái gửi.

    LƯU DẠNG CHUỖI: mã NV có thể có số 0 ở đầu ("00332383"), ép sang số là
    mất số 0 và không đối soát được với dữ liệu nhân sự.
    """
    trich = ket_qua_xu_ly.trich_xuat
    conn = _ket_noi(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO log_xu_ly
                (thoi_diem, user_course_id, employee_id, ma_nhan_vien,
                 ten_tren_anh, ten_chung_chi, ngay_tren_anh, ket_qua,
                 ly_do, tang_xu_ly)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                str(user_course_id) if user_course_id is not None else None,
                str(employee_id) if employee_id is not None else None,
                ket_qua_xu_ly.ma_nhan_vien,
                trich.ten_nguoi_nhan if trich else None,
                trich.ten_chung_chi if trich else None,
                trich.ngay_nhan if trich else None,
                ket_qua_xu_ly.ket_qua.value,
                ket_qua_xu_ly.ly_do,
                ket_qua_xu_ly.tang_xu_ly,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def ghi_log_that_bai(user_course_id, employee_id, ket_qua, ly_do,
                     tang_xu_ly, db_path=None):
    """Ghi log cho chứng chỉ KHÔNG chạy được pipeline (vd tải ZIP hỏng).

    Vì sao cần riêng: những ca này không có đối tượng KetQuaXuLy để truyền
    vào ghi_log(). Nếu bỏ qua không ghi gì, chúng biến mất khỏi mọi báo cáo
    — người đọc thấy "hôm nay xử lý 30" mà không biết thật ra có 50 cái chờ,
    20 cái còn lại thất bại lặng lẽ.
    """
    conn = _ket_noi(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO log_xu_ly
                (thoi_diem, user_course_id, employee_id, ket_qua,
                 ly_do, tang_xu_ly)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                str(user_course_id) if user_course_id is not None else None,
                str(employee_id) if employee_id is not None else None,
                ket_qua, ly_do, tang_xu_ly,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def cap_nhat_ket_qua_gui(user_course_id, thanh_cong, message=None, db_path=None):
    """Ghi lại ELIS có nhận kết quả không (từ successList / failList API ③).

    Chỉ cập nhật dòng log MỚI NHẤT của user_course_id đó, phòng khi một bản
    ghi bị xử lý lại nhiều lần qua các vòng poll.
    """
    conn = _ket_noi(db_path)
    try:
        conn.execute(
            """
            UPDATE log_xu_ly
            SET elis_gui_ok = ?, elis_message = ?
            WHERE id = (
                SELECT id FROM log_xu_ly
                WHERE user_course_id = ?
                ORDER BY id DESC LIMIT 1
            )
            """,
            (1 if thanh_cong else 0, message, str(user_course_id)),
        )
        conn.commit()
    finally:
        conn.close()


def doc_log_gan_nhat(so_dong=20, db_path=None):
    """Đọc các dòng log gần nhất. Trả về list dict."""
    conn = _ket_noi(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM log_xu_ly ORDER BY id DESC LIMIT ?", (so_dong,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def dem_theo_ket_qua(db_path=None):
    """Đếm số log theo từng kết quả (APPROVED/REJECTED). Trả về dict."""
    conn = _ket_noi(db_path)
    try:
        rows = conn.execute(
            "SELECT ket_qua, COUNT(*) FROM log_xu_ly GROUP BY ket_qua"
        ).fetchall()
        return {ket_qua: so for ket_qua, so in rows}
    finally:
        conn.close()
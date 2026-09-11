"""Ghi log xử lý chứng chỉ vào SQLite (database).

Mỗi chứng chỉ -> một dòng log. Dùng sqlite3 có sẵn trong Python; database là
một file (.db), mặc định mooc_log.db ở gốc dự án.

Hai trường định danh người, đừng nhầm:
  - employee_id  : mã ELIS cấp (vd "00332383"), gửi ngược về ELIS.
  - employee_code : username từ employeeEmail (vd "hoabd3"), CHỈ dùng đối
                   chiếu với tên in trên chứng chỉ.

Dùng:
    from database.database import write_log, init_db
    init_db()                              # tạo/nâng cấp bảng khi khởi động
    write_log(process_result, employee_id="00332383")
"""

import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "mooc_log.db"


def _connect(db_path=None):
    """Mở kết nối tới file SQLite."""
    return sqlite3.connect(str(db_path or DB_PATH))


# Các cột thêm sau khi bảng đã tồn tại. Xem init_db() về lý do phải liệt kê
# riêng thay vì chỉ sửa CREATE TABLE.
_ADDED_COLUMNS = {
    "employee_id": "TEXT",     # mã NV của ELIS, khác employee_code (username)
    "user_course_id": "TEXT",  # id bản ghi, để cập nhật trạng thái gửi sau
    "elis_sent_ok": "INTEGER",  # 1=successList, 0=failList, NULL=chưa gửi
    "elis_message": "TEXT",    # message ELIS trả về khi từ chối
    # Nhà cung cấp chứng chỉ (getCert.providerName): Udemy, Coursera...
    # Dòng cũ nhận NULL, báo cáo gom vào "(không rõ)".
    "provider": "TEXT",
    # Tên khóa do eLIS đăng ký (getCert.courseName), KHÁC cột certificate_name
    # vốn là tên khóa AI đọc được từ ảnh. Thiếu cột này thì không biết dòng log
    # ứng với khóa nào — ca hỏng kỹ thuật không đọc được ảnh nên
    # certificate_name cũng NULL.
    "course_name": "TEXT",
}


def init_db(db_path=None):
    """Tạo bảng log nếu chưa có, và thêm cột mới nếu bảng cũ còn thiếu.

    CREATE TABLE IF NOT EXISTS chỉ chạy khi bảng CHƯA tồn tại, nên với DB cũ
    thì thêm cột vào phần CREATE không có tác dụng và chương trình lỗi "no
    such column". Phải ALTER TABLE thêm phần thiếu; cột mới nhận NULL.
    """
    conn = _connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS process_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at       TEXT NOT NULL,
                user_course_id  TEXT,
                employee_id     TEXT,
                employee_code    TEXT,
                name_on_image    TEXT,
                certificate_name   TEXT,
                date_on_image   TEXT,
                verdict         TEXT NOT NULL,
                reason           TEXT,
                stage      TEXT,
                elis_sent_ok     INTEGER,
                elis_message    TEXT
            )
        """)

        existing = {r[1] for r in conn.execute("PRAGMA table_info(process_log)")}
        for column_name, kind in _ADDED_COLUMNS.items():
            if column_name not in existing:
                conn.execute(f"ALTER TABLE process_log ADD COLUMN {column_name} {kind}")

        conn.commit()
    finally:
        conn.close()


def write_log(process_result, employee_id=None, user_course_id=None,
              provider=None, course_name=None, verdict_override=None,
              db_path=None):
    """Ghi một dòng log từ ProcessResult. Trả về id dòng vừa ghi.

    employee_id: mã ELIS cấp (getCert.employeeId), truyền riêng vì
    ProcessResult chỉ giữ employee_code.
    user_course_id: id bản ghi trên ELIS, cần để biết dòng log nào ứng với
    item nào khi gọi API ③.
    course_name: tên khóa do ELIS đăng ký (getCert.courseName), đừng nhầm với
    extracted.certificate_name (tên AI đọc trên ảnh).
    verdict_override: CHỈ dùng cho ca hỏng kỹ thuật, ghi "WAITING" vì ca đó
    không nộp về eLIS. Không truyền thì lấy verdict của ProcessResult.

    LƯU DẠNG CHUỖI: mã NV có thể có số 0 ở đầu ("00332383").
    """
    extracted = process_result.extracted
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO process_log
                (created_at, user_course_id, employee_id, employee_code,
                 name_on_image, certificate_name, date_on_image, verdict,
                 reason, stage, provider, course_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                str(user_course_id) if user_course_id is not None else None,
                str(employee_id) if employee_id is not None else None,
                process_result.employee_code,
                extracted.recipient_name if extracted else None,
                extracted.certificate_name if extracted else None,
                extracted.issue_date if extracted else None,
                verdict_override or process_result.verdict.value,
                process_result.reason,
                process_result.stage,
                str(provider).strip() if provider else None,
                str(course_name).strip() if course_name else None,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def write_failure_log(user_course_id, employee_id, verdict, reason,
                      stage, provider=None, course_name=None, db_path=None):
    """Ghi log cho chứng chỉ KHÔNG chạy được pipeline (vd tải ZIP hỏng).

    Cần hàm riêng vì những ca này không có ProcessResult để truyền vào
    write_log(); bỏ qua thì chúng biến mất khỏi mọi báo cáo.

    course_name ở đây quan trọng hơn ở write_log: ca này không đọc được ảnh
    nên certificate_name luôn NULL.
    """
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO process_log
                (created_at, user_course_id, employee_id, verdict,
                 reason, stage, provider, course_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                str(user_course_id) if user_course_id is not None else None,
                str(employee_id) if employee_id is not None else None,
                verdict, reason, stage,
                str(provider).strip() if provider else None,
                str(course_name).strip() if course_name else None,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_send_result(user_course_id, succeeded, message=None, db_path=None):
    """Ghi lại ELIS có nhận kết quả không (successList / failList của API ③).

    Chỉ cập nhật dòng log MỚI NHẤT của user_course_id đó, phòng khi bản ghi bị
    xử lý lại qua nhiều vòng poll.
    """
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            UPDATE process_log
            SET elis_sent_ok = ?, elis_message = ?
            WHERE id = (
                SELECT id FROM process_log
                WHERE user_course_id = ?
                ORDER BY id DESC LIMIT 1
            )
            """,
            (1 if succeeded else 0, message, str(user_course_id)),
        )
        conn.commit()
    finally:
        conn.close()


def read_recent_logs(row_count=20, db_path=None):
    """Đọc các dòng log gần nhất. Trả về list dict."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM process_log ORDER BY id DESC LIMIT ?", (row_count,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# Các stage nghĩa là "hỏng kỹ thuật", không phải kết luận nghiệp vụ.
# Trùng với report.FAILURE_GROUPS — có test canh hai bên không lệch nhau.
TECHNICAL_STAGES = ("llm1_error", "stage2_error", "system_error",
                    "file_error", "download_error", "no_file", "soft_fail_zip",
                    "duplicate_check_error")

# Ca BỎ QUA — CỐ Ý KHÔNG nằm trong TECHNICAL_STAGES. Thêm vào đó thì
# technical_retry_state() đếm nó như hỏng kỹ thuật và với chu kỳ poll 5 giây,
# chỉ 25 giây sau đã gửi mail báo động nhầm.
SKIP_STAGE = "skipped_external_email"


def skipped_ids(user_course_ids, db_path=None) -> set:
    """Những chứng chỉ ĐANG bị bỏ qua, để không xử lý lại.

    Phải chặn bằng DB vì ca này chỉ lộ ra SAU KHI chạy hết pipeline, mà bản
    ghi vẫn nằm WAITING nên vòng getCert sau trả về đúng nó. Kết quả không đổi
    được: đường ra duy nhất là người duyệt xử lý trên eLIS, thao tác đó đưa
    bản ghi RỜI WAITING.

    Lấy dòng MỚI NHẤT (MAX(id)) để chứng chỉ sau đó xử lý được thì tự rơi khỏi
    danh sách. Không dùng MAX(created_at) vì nó chỉ chính xác tới giây.
    """
    ids = [str(i) for i in user_course_ids if i]
    if not ids:
        return set()

    conn = _connect(db_path)
    try:
        out = set()
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            placeholders = ",".join("?" * len(part))
            rows = conn.execute(
                f"SELECT p.user_course_id FROM process_log p "
                f"WHERE p.user_course_id IN ({placeholders}) "
                f"  AND p.stage = ? "
                f"  AND p.id = (SELECT MAX(q.id) FROM process_log q "
                f"              WHERE q.user_course_id = p.user_course_id)",
                (*part, SKIP_STAGE)).fetchall()
            out.update(r[0] for r in rows)
        return out
    finally:
        conn.close()


def technical_retry_state(user_course_ids, db_path=None) -> dict:
    """Với mỗi user_course_id: đã hỏng kỹ thuật MẤY LẦN và LẦN CUỐI khi nào.

    Bộ đếm SỐNG QUA CÁC LẦN CHẠY, chặn vòng lặp đốt tiền: ca hỏng kỹ thuật để
    nguyên WAITING nên vòng getCert sau trả về đúng nó và job gọi LLM lại mãi.
    Đếm từ bảng log chứ không giữ trong RAM, vì restart là mất sạch bộ đếm.

    Trả về {user_course_id: (so_lan, thoi_diem_lan_cuoi_iso)}; id chưa hỏng
    lần nào thì KHÔNG có trong dict.
    """
    ids = [str(i) for i in user_course_ids if i]
    if not ids:
        return {}

    conn = _connect(db_path)
    try:
        # Chia lô để không vượt giới hạn số tham số của SQLite (999).
        out = {}
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            cho_id = ",".join("?" * len(part))
            cho_stage = ",".join("?" * len(TECHNICAL_STAGES))
            rows = conn.execute(
                f"SELECT user_course_id, COUNT(*), MAX(created_at) "
                f"FROM process_log "
                f"WHERE user_course_id IN ({cho_id}) "
                f"  AND stage IN ({cho_stage}) "
                f"GROUP BY user_course_id",
                (*part, *TECHNICAL_STAGES)).fetchall()
            out.update({r[0]: (r[1], r[2]) for r in rows})
        return out
    finally:
        conn.close()


def technical_failure_detail(user_course_ids, db_path=None) -> dict:
    """Với mỗi user_course_id: (stage, reason) của lần hỏng kỹ thuật GẦN NHẤT.

    Dùng để dựng email cảnh báo: technical_retry_state() chỉ trả về SỐ LẦN.
    Lấy theo MAX(id) chứ không MAX(created_at): id tự tăng nên luôn đúng thứ
    tự ghi, còn created_at chỉ chính xác tới giây.

    Trả về {user_course_id: (stage, reason)}; id chưa hỏng lần nào thì KHÔNG
    có trong dict.
    """
    ids = [str(i) for i in user_course_ids if i]
    if not ids:
        return {}

    conn = _connect(db_path)
    try:
        out = {}
        for i in range(0, len(ids), 400):
            part = ids[i:i + 400]
            cho_id = ",".join("?" * len(part))
            cho_stage = ",".join("?" * len(TECHNICAL_STAGES))
            rows = conn.execute(
                f"SELECT user_course_id, stage, reason FROM process_log "
                f"WHERE id IN ("
                f"    SELECT MAX(id) FROM process_log "
                f"    WHERE user_course_id IN ({cho_id}) "
                f"      AND stage IN ({cho_stage}) "
                f"    GROUP BY user_course_id)",
                (*part, *TECHNICAL_STAGES)).fetchall()
            out.update({r[0]: (r[1], r[2]) for r in rows})
        return out
    finally:
        conn.close()


def count_by_verdict(db_path=None):
    """Đếm số log theo từng kết quả (APPROVED/REJECTED). Trả về dict."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT verdict, COUNT(*) FROM process_log GROUP BY verdict"
        ).fetchall()
        return {verdict: count for verdict, count in rows}
    finally:
        conn.close()

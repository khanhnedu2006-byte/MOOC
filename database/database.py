"""Ghi log xử lý chứng chỉ vào SQLite (database).

Mỗi lần xử lý một chứng chỉ -> ghi một dòng log: thời điểm, thông tin nhận
diện, kết quả, lý do, tầng xử lý. Dùng để xem lại / kiểm toán sau này.

Dùng sqlite3 có sẵn trong Python — không cần cài server, không thêm thư viện.
Database là một file (.db), mặc định mooc_log.db ở gốc dự án.

Có HAI trường định danh người, đừng nhầm:
  - employee_id  : mã nhân viên do ELIS cấp (vd "00332383"). Dùng để đối
                   soát với dữ liệu nhân sự, và là thứ gửi ngược về ELIS.
  - employee_code : username lấy từ employeeEmail (vd "hoabd3"). CHỈ dùng để
                   đối chiếu với tên in trên chứng chỉ, vì nhiều chứng chỉ
                   in username thay cho tên thật.

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


# Các cột thêm sau khi bảng đã tồn tại ngoài thực tế. Xem giải thích ở
# init_db() về việc vì sao phải liệt kê riêng thay vì chỉ sửa CREATE TABLE.
_ADDED_COLUMNS = {
    "employee_id": "TEXT",     # mã NV của ELIS, khác employee_code (username)
    "user_course_id": "TEXT",  # id bản ghi, để cập nhật trạng thái gửi sau
    "elis_sent_ok": "INTEGER",  # 1=successList, 0=failList, NULL=chưa gửi
    "elis_message": "TEXT",    # message ELIS trả về khi từ chối
    # Nhà cung cấp chứng chỉ (getCert.providerName): Udemy, Coursera...
    # Thêm sau nên phải nằm ở đây; dòng cũ nhận NULL, báo cáo gom vào
    # "(không rõ)" thay vì biến mất.
    "provider": "TEXT",
    # Tên khóa học do eLIS đăng ký (getCert.courseName) — KHÁC với cột
    # certificate_name, vốn là tên khóa mà AI ĐỌC ĐƯỢC TỪ ẢNH.
    # Vì sao phải có: thiếu cột này thì từ DB không thể biết dòng log nào ứng
    # với khóa học nào. Ca hỏng kỹ thuật còn tệ hơn — không đọc được ảnh nên
    # certificate_name cũng NULL, dòng log chỉ còn một chuỗi id vô nghĩa và
    # người vận hành phải đoán bằng cách so mốc thời gian với log console.
    "course_name": "TEXT",
}


def init_db(db_path=None):
    """Tạo bảng log nếu chưa có, và thêm cột mới nếu bảng cũ còn thiếu.

    Vì sao cần phần "thêm cột": CREATE TABLE IF NOT EXISTS chỉ chạy khi bảng
    CHƯA tồn tại. Với máy đã chạy job trước đó, bảng đã có sẵn nên câu lệnh
    đó bị bỏ qua HOÀN TOÀN — thêm cột vào phần CREATE cũng không có tác dụng,
    và chương trình sẽ lỗi "no such column" dù code trông đúng.

    Nên phải hỏi bảng hiện có những cột nào rồi ALTER TABLE thêm phần thiếu.
    Cách này an toàn với cả DB mới lẫn DB đã có dữ liệu — dữ liệu cũ giữ
    nguyên, cột mới nhận giá trị NULL.
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

    employee_id: mã nhân viên do ELIS cấp (getCert.employeeId). Truyền riêng
    vì ProcessResult không mang theo trường này — nó chỉ giữ employee_code
    (username dùng để đối chiếu với ảnh).

    user_course_id: id bản ghi trên ELIS. Cần để sau khi gọi API ③ còn biết
    dòng log nào ứng với item nào mà cập nhật trạng thái gửi.

    course_name: tên khóa do ELIS đăng ký (getCert.courseName). Đừng nhầm với
    extracted.certificate_name — cái đó là tên AI đọc được trên ảnh, hai giá
    trị này lệch nhau chính là lý do chứng chỉ bị từ chối.

    verdict_override: CHỈ dùng cho ca hỏng kỹ thuật, để ghi "WAITING" thay cho
    verdict của pipeline. Những ca đó không được nộp về eLIS nên bên eLIS
    chúng vẫn đang chờ duyệt; ghi REJECTED vào log là sai sự thật. Không
    truyền thì lấy nguyên verdict của ProcessResult.

    LƯU DẠNG CHUỖI: mã NV có thể có số 0 ở đầu ("00332383"), ép sang số là
    mất số 0 và không đối soát được với dữ liệu nhân sự.
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

    Vì sao cần riêng: những ca này không có đối tượng ProcessResult để truyền
    vào write_log(). Nếu bỏ qua không ghi gì, chúng biến mất khỏi mọi báo cáo
    — người đọc thấy "hôm nay xử lý 30" mà không biết thật ra có 50 cái chờ,
    20 cái còn lại thất bại lặng lẽ.

    course_name ở đây QUAN TRỌNG HƠN ở write_log: ca này không đọc được ảnh
    nên certificate_name luôn NULL. Không ghi tên khóa thì dòng log không còn
    manh mối nào để biết chứng chỉ nào đang hỏng.
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
    """Ghi lại ELIS có nhận kết quả không (từ successList / failList API ③).

    Chỉ cập nhật dòng log MỚI NHẤT của user_course_id đó, phòng khi một bản
    ghi bị xử lý lại nhiều lần qua các vòng poll.
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
                    "file_error", "download_error", "no_file", "soft_fail_zip")

# Ca BỎ QUA — CỐ Ý KHÔNG nằm trong TECHNICAL_STAGES.
# Thêm vào đó thì technical_retry_state() đếm nó như hỏng kỹ thuật: với chu kỳ
# poll 5 giây, chỉ 25 giây sau hệ thống gửi mail báo động cho người vận hành về
# một chứng chỉ mà hệ thống chẳng làm gì sai cả.
SKIP_STAGE = "skipped_external_email"


def skipped_ids(user_course_ids, db_path=None) -> set:
    """Những chứng chỉ ĐANG bị bỏ qua, để không xử lý lại.

    Vì sao phải chặn bằng DB chứ không kiểm tra lại mỗi vòng: ca này chỉ lộ ra
    SAU KHI đã chạy hết pipeline (Gemma + Azure + LLM2). Bản ghi vẫn nằm
    WAITING nên vòng getCert sau trả về đúng nó — không chặn thì cứ 5 giây lại
    đốt một lượt LLM cho một kết quả không bao giờ đổi.

    Không bao giờ đổi thật: cái email cá nhân in cứng trên ảnh rồi, không ai
    sửa được. Đường ra duy nhất là người duyệt vào eLIS xử lý, mà thao tác đó
    đưa bản ghi RỜI WAITING nên nó không quay lại hàng đợi nữa.

    Lấy dòng MỚI NHẤT (MAX(id)) chứ không phải "từng có dòng skip": nếu chứng
    chỉ sau đó được xử lý bình thường thì dòng mới đè lên và nó tự rơi khỏi
    danh sách này. Dùng MAX(id) chứ không MAX(created_at) vì created_at chỉ
    chính xác tới giây — hai dòng trong cùng một giây sẽ hòa nhau.
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

    Đây là bộ đếm SỐNG QUA CÁC LẦN CHẠY. Nó tồn tại để chặn một vòng lặp đốt
    tiền: khi ca hỏng kỹ thuật được để nguyên WAITING (không nộp eLIS), vòng
    getCert sau sẽ trả về đúng nó, job lại tải + gọi LLM lại. Với chu kỳ 5
    giây thì một chứng chỉ hỏng vĩnh viễn (file thật sự lỗi) sẽ quay vòng mãi
    mãi, mỗi vòng một lượt LLM, và không có gì báo cho ai biết.

    Giữ trong RAM không đủ: container restart là mất sạch bộ đếm. Bảng log
    vốn đã ghi mọi lần thử rồi, nên đếm từ đó là nguồn duy nhất đáng tin.

    Trả về {user_course_id: (so_lan, thoi_diem_lan_cuoi_iso)}. Id chưa hỏng
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

    Dùng để dựng email cảnh báo. technical_retry_state() chỉ trả về SỐ LẦN,
    mà số lần một mình thì email chỉ nói được "5 chứng chỉ hỏng" — người nhận
    vẫn phải mở log lên mới biết hỏng vì cái gì. Có stage và reason thì thư
    nói thẳng "Azure hết hạn mức" hay "eLIS không trả file", tức là đọc xong
    biết đi sửa ở đâu.

    Lấy theo MAX(id) chứ không phải MAX(created_at): id tự tăng nên luôn đúng
    thứ tự ghi, còn created_at là chuỗi và hai lần thử trong cùng một giây sẽ
    bằng nhau, lúc đó không biết dòng nào mới hơn.

    Trả về {user_course_id: (stage, reason)}. Id chưa hỏng lần nào thì KHÔNG
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

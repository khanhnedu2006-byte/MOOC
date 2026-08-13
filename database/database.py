"""Log kết quả xử lý chứng chỉ (database).

Lo hai việc:
  1. Nhớ chứng chỉ nào đã xử lý rồi -> vòng poll sau không chạy AI lại và
     không nộp lại. Quan trọng vì mỗi lần chạy lại tốn tiền LLM + Azure, và
     ELIS sẽ trả về failList vì bản ghi không còn ở trạng thái WAITING.
  2. Lưu vết để tra cứu khi cần rà soát: chứng chỉ này bị REJECTED vì lý do
     gì, đọc ra thông tin gì, kết luận ở tầng nào.

Dùng SQLite (có sẵn trong Python, không cần cài thêm) — đủ cho một job chạy
đơn luồng. Nếu sau này chạy nhiều instance song song thì đổi sang
Postgres/MySQL, phần còn lại của code không phải sửa vì chỉ gọi qua các hàm
trong file này.

File .db được .gitignore che sẵn (dòng "database/*.db").
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "elis_log.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS xu_ly_log (
    user_course_id   TEXT PRIMARY KEY,  -- id từ getCert, khóa chính chống trùng
    certificate_id   TEXT,
    course_id        TEXT,
    employee_id      TEXT,              -- mã NV, LƯU DẠNG CHUỖI (giữ số 0 đầu)
    employee_name    TEXT,
    course_name      TEXT,

    ket_qua          TEXT,              -- APPROVED / REJECTED
    ly_do            TEXT,              -- lý do chi tiết từ pipeline
    tang_xu_ly       TEXT,              -- llm1 / llm2 / llm1_vs_llm2 / soft_fail_zip...

    elis_submit_ok   INTEGER,           -- 1=successList, 0=failList, NULL=chưa nộp
    elis_message     TEXT,              -- message ELIS trả về khi fail

    thoi_gian_xu_ly     TEXT,
    thoi_gian_nop_elis  TEXT
);
"""


@contextmanager
def ket_noi():
    """Mở kết nối SQLite, tự tạo bảng nếu chưa có, tự commit và đóng."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _bay_gio() -> str:
    return datetime.now().isoformat(timespec="seconds")


def da_xu_ly(conn: sqlite3.Connection, user_course_id: str) -> bool:
    """True nếu chứng chỉ này đã được xử lý VÀ đã nộp ELIS thành công.

    Cố tình KHÔNG tính các bản ghi nộp thất bại (elis_submit_ok = 0 hoặc
    NULL) là "đã xử lý" — như vậy vòng poll sau sẽ thử lại những cái nộp
    hụt do mạng lỗi, thay vì bỏ quên chúng vĩnh viễn.
    """
    row = conn.execute(
        "SELECT 1 FROM xu_ly_log WHERE user_course_id = ? AND elis_submit_ok = 1",
        (user_course_id,),
    ).fetchone()
    return row is not None


def ghi_ket_qua_xu_ly(
    conn: sqlite3.Connection,
    user_course_id: str,
    certificate_id: str,
    course_id: str | None,
    employee_id: str,
    employee_name: str,
    course_name: str,
    ket_qua: str,
    ly_do: str,
    tang_xu_ly: str,
) -> None:
    """Ghi kết quả AI scan một chứng chỉ (trước khi biết ELIS có nhận không).

    Dùng UPSERT để chạy lại không bị lỗi trùng khóa chính.
    """
    conn.execute(
        """
        INSERT INTO xu_ly_log (
            user_course_id, certificate_id, course_id, employee_id,
            employee_name, course_name, ket_qua, ly_do, tang_xu_ly,
            thoi_gian_xu_ly
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_course_id) DO UPDATE SET
            certificate_id = excluded.certificate_id,
            course_id      = excluded.course_id,
            employee_id    = excluded.employee_id,
            employee_name  = excluded.employee_name,
            course_name    = excluded.course_name,
            ket_qua        = excluded.ket_qua,
            ly_do          = excluded.ly_do,
            tang_xu_ly     = excluded.tang_xu_ly,
            thoi_gian_xu_ly = excluded.thoi_gian_xu_ly
        """,
        (
            user_course_id, certificate_id, course_id, str(employee_id),
            employee_name, course_name, ket_qua, ly_do, tang_xu_ly, _bay_gio(),
        ),
    )


def ghi_ket_qua_nop_elis(
    conn: sqlite3.Connection,
    user_course_id: str,
    thanh_cong: bool,
    message: str | None = None,
) -> None:
    """Ghi lại ELIS có nhận kết quả không (từ successList / failList của API ③)."""
    conn.execute(
        """
        UPDATE xu_ly_log
        SET elis_submit_ok = ?, elis_message = ?, thoi_gian_nop_elis = ?
        WHERE user_course_id = ?
        """,
        (1 if thanh_cong else 0, message, _bay_gio(), user_course_id),
    )


def thong_ke() -> dict:
    """Thống kê nhanh để xem job chạy có bình thường không.

    Tỷ lệ REJECTED tăng vọt bất thường thường là dấu hiệu sai mapping
    (vd gửi nhầm Guid vào employeeId thay vì mã NV).
    """
    with ket_noi() as conn:
        theo_ket_qua = {
            r["ket_qua"]: r["sl"]
            for r in conn.execute(
                "SELECT ket_qua, COUNT(*) AS sl FROM xu_ly_log GROUP BY ket_qua"
            )
        }
        chua_nop = conn.execute(
            "SELECT COUNT(*) AS sl FROM xu_ly_log WHERE elis_submit_ok IS NOT 1"
        ).fetchone()["sl"]
        return {"theo_ket_qua": theo_ket_qua, "chua_nop_duoc": chua_nop}

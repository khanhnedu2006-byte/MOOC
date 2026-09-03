"""Test cột course_name trong bảng log (test_course_name).

VẤN ĐỀ ĐÃ XẢY RA THẬT: một chứng chỉ nằm im trên eLIS, hỏi "cái nào?" thì
không trả lời được. Màn hình eLIS chỉ hiện TÊN KHÓA, còn bảng log chỉ có
user_course_id — không có cột nào nối hai thứ đó. Cột certificate_name không
thay được: đó là tên AI ĐỌC TỪ ẢNH, mà ca hỏng kỹ thuật thì đọc không được
nên nó luôn NULL — đúng những ca cần tra nhất lại là những ca trống nhất.
Kết quả là phải đoán bằng cách so mốc thời gian giữa log console và DB.

Bốn test dưới đây khóa lại từng mắt xích của chuỗi đó.
"""

import pathlib
import sqlite3
import sys

GOC = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GOC))
sys.path.insert(0, str(GOC / "src"))

from database import database                 # noqa: E402
from schemas import ExtractedInfo, ProcessResult, Verdict   # noqa: E402


def _cot(db):
    conn = sqlite3.connect(str(db))
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(process_log)")}
    finally:
        conn.close()


def test_db_moi_co_cot_course_name(tmp_path):
    """DB tạo mới phải có sẵn cột."""
    db = tmp_path / "moi.db"
    database.init_db(db)
    assert "course_name" in _cot(db)


def test_db_cu_duoc_them_cot(tmp_path):
    """DB đã chạy từ trước (chưa có cột) phải được ALTER TABLE thêm vào.

    Đây là test đáng giá nhất trong file: CREATE TABLE IF NOT EXISTS bị bỏ
    qua HOÀN TOÀN khi bảng đã tồn tại, nên chỉ thêm cột vào câu CREATE là
    máy thật sẽ lỗi "no such column" dù test trên máy sạch vẫn xanh.
    Dữ liệu cũ phải còn nguyên, cột mới nhận NULL.
    """
    db = tmp_path / "cu.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE process_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            user_course_id TEXT,
            verdict TEXT NOT NULL,
            stage TEXT
        )
    """)
    conn.execute("INSERT INTO process_log (created_at, user_course_id, verdict, stage)"
                 " VALUES ('2026-01-01T00:00:00', 'uc-cu', 'REJECTED', 'llm1')")
    conn.commit()
    conn.close()

    database.init_db(db)

    assert "course_name" in _cot(db)
    rows = database.read_recent_logs(10, db_path=db)
    assert len(rows) == 1, "dữ liệu cũ bị mất khi thêm cột"
    assert rows[0]["user_course_id"] == "uc-cu"
    assert rows[0]["course_name"] is None


def test_write_log_ghi_ten_khoa(tmp_path):
    """Tên khóa eLIS và tên khóa AI đọc được là HAI cột khác nhau.

    Chúng lệch nhau chính là lý do chứng chỉ bị từ chối, nên gộp một cột là
    mất luôn bằng chứng của quyết định từ chối.
    """
    db = tmp_path / "log.db"
    database.init_db(db)

    kq = ProcessResult(
        verdict=Verdict.REJECTED,
        reason="Tên khóa học không khớp",
        stage="llm1",
        employee_code="nvA",
        extracted=ExtractedInfo(
            recipient_name="Nguyen Van A",
            certificate_name="Beyond Basic PowerPoint Slides",
            issue_date="01/08/2026",
        ),
    )
    database.write_log(kq, employee_id="E1", user_course_id="uc-1",
                       provider="Coursera",
                       course_name="AI Trends", db_path=db)

    row = database.read_recent_logs(1, db_path=db)[0]
    assert row["course_name"] == "AI Trends"                        # eLIS đăng ký
    assert row["certificate_name"] == "Beyond Basic PowerPoint Slides"  # AI đọc


def test_write_failure_log_ghi_ten_khoa(tmp_path):
    """Ca hỏng kỹ thuật: certificate_name NULL nên course_name là manh mối duy nhất."""
    db = tmp_path / "fail.db"
    database.init_db(db)

    database.write_failure_log(
        user_course_id="uc-2", employee_id="E2",
        verdict=Verdict.WAITING.value, reason="Azure 408",
        stage="stage2_error", provider="LinkedIn",
        course_name="Learning Microsoft 365 Copilot for Work", db_path=db)

    row = database.read_recent_logs(1, db_path=db)[0]
    assert row["certificate_name"] is None
    assert row["course_name"] == "Learning Microsoft 365 Copilot for Work"

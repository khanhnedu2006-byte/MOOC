"""Test luồng thử lại ca hỏng kỹ thuật (test_retry).

Ca hỏng kỹ thuật KHÔNG bị nộp REJECTED — để nguyên WAITING trên eLIS để còn
được xử lý lại. Cách làm đó tạo ra một rủi ro phải chặn: bản ghi còn WAITING
thì vòng getCert sau lại trả về nó, job lại tải và gọi LLM lại. Với chu kỳ 5
giây, một file hỏng vĩnh viễn sẽ quay vòng mãi mãi, mỗi vòng tốn một lượt LLM.

Ba lớp chặn, mỗi lớp một test ở đây:
  1. Cooldown  — chưa tới lượt thì BỎ QUA hẳn, không tải, không gọi LLM.
  2. Giới hạn  — quá số lần thì BỎ CUỘC, nộp REJECTED thật.
  3. Thứ tự    — ca thử lại xếp sau ca mới, không chặn hàng đợi.
"""

import logging
import pathlib
import sqlite3
import sys
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

GOC = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GOC))
sys.path.insert(0, str(GOC / "src"))

import client                          # noqa: E402
import run                             # noqa: E402
from database import database          # noqa: E402
from schemas import ProcessResult, Verdict   # noqa: E402

logging.disable(logging.CRITICAL)


def _item(uc_id: str) -> dict:
    return {"id": uc_id, "certificate_id": f"c-{uc_id}", "courseId": "K1",
            "employeeId": "003", "employeeName": "Bùi Đức Hòa",
            "employeeEmail": "hoabd3@fpt.com", "courseName": "ISO 27001",
            "providerName": "Coursera"}


def _hong(stage="stage2_error"):
    return ProcessResult(verdict=Verdict.REJECTED,
                         reason="Azure timeout", stage=stage)


def _duyet():
    return ProcessResult(verdict=Verdict.APPROVED, reason="Khớp", stage="llm1")


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    """DB riêng cho từng test + cấu hình cố định."""
    db = str(tmp_path / "t.db")
    database.init_db(db)
    monkeypatch.setattr(database, "DB_PATH", db)
    monkeypatch.setattr(run.settings, "technical_retry_max", 3)
    monkeypatch.setattr(run.settings, "technical_retry_cooldown_minutes", 30)
    monkeypatch.setattr(run.settings, "batch_size", 5)
    return db


def _chay(items, ket_qua_scan):
    """Chạy một vòng, trả về (RoundResult, danh sách DTO đã nộp)."""
    da_nop = []
    it_kq = iter(ket_qua_scan)

    def nop(dtos):
        da_nop.extend(dtos)
        return {"successList": [{"id": d["id"]} for d in dtos], "failList": []}

    with patch.object(client, "get_pending_list", return_value=items), \
         patch.object(client, "download_certificates",
                      side_effect=lambda cc: [
                          {"userCourseId": c["UserCourseId"], "anh_bytes": b"x"}
                          for c in cc]), \
         patch.object(client, "update_status", side_effect=nop), \
         patch.object(run, "_scan_certificate", side_effect=lambda *a: next(it_kq)), \
         patch.object(run.archive, "save", return_value=None), \
         patch.object(run.archive, "write_verdict", return_value=None):
        kq = run.process_one_batch(None)
    return kq, da_nop


def _lui_thoi_gian(db, uc_id, gio):
    conn = sqlite3.connect(db)
    conn.execute("UPDATE process_log SET created_at=? WHERE user_course_id=?",
                 ((datetime.now() - timedelta(hours=gio)).isoformat(
                     timespec="seconds"), uc_id))
    conn.commit()
    conn.close()


# ===== Không nộp REJECTED cho ca hỏng kỹ thuật =====

def test_hong_ky_thuat_khong_bi_nop_len_elis(moi_truong):
    """Đây là yêu cầu chính: để WAITING, KHÔNG đóng bằng REJECTED."""
    _, da_nop = _chay([_item("uc-1")], [_hong(), _hong()])
    assert da_nop == [], (
        "ca hỏng kỹ thuật vẫn bị nộp — bản ghi rời WAITING và không bao giờ "
        "được xử lý lại")


@pytest.mark.parametrize("stage", ["stage2_error", "llm1_error",
                                   "download_error", "no_file", "file_error"])
def test_moi_loai_hong_ky_thuat_deu_duoc_giu_lai(moi_truong, stage):
    _, da_nop = _chay([_item("uc-1")], [_hong(stage), _hong(stage)])
    assert da_nop == [], f"stage {stage!r} vẫn bị nộp"


def test_ca_nghiep_vu_van_nop_binh_thuong(moi_truong):
    """Chỉ ca HỎNG KỸ THUẬT được giữ lại. Ca từ chối vì sai tên vẫn phải nộp."""
    tu_choi = ProcessResult(verdict=Verdict.REJECTED,
                            reason="Tên không khớp", stage="llm1")
    _, da_nop = _chay([_item("uc-1")], [tu_choi])
    assert len(da_nop) == 1
    assert da_nop[0]["status"] == "REJECTED"


# ===== Thử lại ở cuối vòng =====

def test_thu_lai_cuoi_vong_thanh_cong_thi_nop(moi_truong):
    """Hỏng lượt đầu, thử lại cuối vòng thành công -> nộp như bình thường."""
    _, da_nop = _chay([_item("uc-1")], [_hong(), _duyet()])
    assert len(da_nop) == 1
    assert da_nop[0]["status"] == "APPROVED"


def test_thu_lai_chi_MOT_lan_trong_mot_vong(moi_truong):
    """Không được đệ quy vô hạn trong cùng một vòng."""
    so_lan = {"n": 0}

    def scan(*a):
        so_lan["n"] += 1
        return _hong()

    with patch.object(client, "get_pending_list", return_value=[_item("uc-1")]), \
         patch.object(client, "download_certificates",
                      side_effect=lambda cc: [
                          {"userCourseId": c["UserCourseId"], "anh_bytes": b"x"}
                          for c in cc]), \
         patch.object(client, "update_status", return_value={"successList": [], "failList": []}), \
         patch.object(run, "_scan_certificate", side_effect=scan), \
         patch.object(run.archive, "save", return_value=None), \
         patch.object(run.archive, "write_verdict", return_value=None):
        run.process_one_batch(None)

    assert so_lan["n"] == 2, f"quét {so_lan['n']} lần, mong đúng 2 (đầu + thử lại)"


# ===== Cooldown: chặn vòng lặp đốt tiền =====

def test_chua_het_cooldown_thi_KHONG_goi_llm(moi_truong):
    """Lớp chặn quan trọng nhất về chi phí.

    Không có nó, với POLL_INTERVAL_SECONDS=5 thì cùng một chứng chỉ hỏng sẽ
    tốn 12 lượt LLM mỗi phút, mãi mãi.
    """
    _chay([_item("uc-1")], [_hong(), _hong()])       # tạo lịch sử hỏng

    so_lan = {"n": 0}

    def scan(*a):
        so_lan["n"] += 1
        return _duyet()

    with patch.object(client, "get_pending_list", return_value=[_item("uc-1")]), \
         patch.object(client, "download_certificates", side_effect=lambda cc: []), \
         patch.object(run, "_scan_certificate", side_effect=scan):
        run.process_one_batch(None)

    assert so_lan["n"] == 0, "vẫn gọi LLM dù chưa hết cooldown"


def test_het_cooldown_thi_duoc_thu_lai(moi_truong):
    _chay([_item("uc-1")], [_hong(), _hong()])
    _lui_thoi_gian(moi_truong, "uc-1", gio=2)

    _, da_nop = _chay([_item("uc-1")], [_duyet()])
    assert len(da_nop) == 1, "hết cooldown rồi mà vẫn bị hoãn"


# ===== Bỏ cuộc: không để WAITING vĩnh viễn =====

def test_qua_gioi_han_thi_bo_cuoc_va_nop_rejected(moi_truong):
    """Không có nhánh này thì bản ghi nằm WAITING mãi mãi, không ai biết."""
    conn = sqlite3.connect(moi_truong)
    for _ in range(3):                                # = technical_retry_max
        conn.execute(
            "INSERT INTO process_log (created_at,user_course_id,verdict,stage,reason)"
            " VALUES (?,?,?,?,?)",
            ((datetime.now() - timedelta(hours=5)).isoformat(timespec="seconds"),
             "uc-X", "REJECTED", "stage2_error", "Azure timeout"))
    conn.commit()
    conn.close()

    da_nop = []
    with patch.object(client, "get_pending_list", return_value=[_item("uc-X")]), \
         patch.object(client, "download_certificates", side_effect=lambda cc: []), \
         patch.object(client, "update_status",
                      side_effect=lambda d: (da_nop.extend(d),
                                             {"successList": [{"id": x["id"]} for x in d],
                                              "failList": []})[1]):
        run.process_one_batch(None)

    assert len(da_nop) == 1, "quá giới hạn mà vẫn để WAITING vĩnh viễn"
    assert da_nop[0]["status"] == "REJECTED"
    # Lý do phải nói rõ đây là lỗi hệ thống, không đổ cho học viên.
    assert "liên hệ" in da_nop[0]["comment"].lower()
    assert "thử 3 lần" in da_nop[0]["comment_cer"]


# ===== Thứ tự hàng đợi =====

def test_ca_moi_xu_ly_truoc_ca_thu_lai(moi_truong):
    """Một chứng chỉ mắc kẹt không được chặn cả hàng đợi."""
    conn = sqlite3.connect(moi_truong)
    conn.execute(
        "INSERT INTO process_log (created_at,user_course_id,verdict,stage,reason)"
        " VALUES (?,?,?,?,?)",
        ((datetime.now() - timedelta(hours=5)).isoformat(timespec="seconds"),
         "uc-cu", "REJECTED", "stage2_error", "x"))
    conn.commit()
    conn.close()

    sap, hoan, bo = run._sap_xep_va_loc(
        [_item("uc-cu"), _item("uc-moi-1"), _item("uc-moi-2")])
    assert [x["id"] for x in sap] == ["uc-moi-1", "uc-moi-2", "uc-cu"]
    assert hoan == [] and bo == []


def test_stage_ky_thuat_khong_lech_giua_run_va_database():
    """run.TECHNICAL_STAGES phải lấy từ database, không chép tay.

    Hai danh sách chép tay sẽ lệch nhau sau lần thêm stage tiếp theo, và khi
    đó một loại lỗi kỹ thuật mới sẽ bị nộp REJECTED mà không ai để ý.
    """
    assert run.TECHNICAL_STAGES == frozenset(database.TECHNICAL_STAGES)


def test_danh_sach_stage_khop_voi_bao_cao():
    """database.TECHNICAL_STAGES phải phủ đúng nhóm report coi là kỹ thuật."""
    from database import report
    assert set(database.TECHNICAL_STAGES) == set(report.FAILURE_GROUPS)


# ===== Log không được phình khi có ca treo lâu =====

def test_log_khong_lap_moi_vong_khi_ca_dang_cooldown(moi_truong, monkeypatch):
    """Cooldown là hàng TIẾNG, vòng lặp là vài GIÂY.

    In trạng thái mỗi vòng thì 6 tiếng chờ sinh ra hơn 4.000 dòng giống hệt
    nhau, nhấn chìm mọi thông tin thật. Log phải nói về việc job LÀM, không
    phải việc job đang bỏ qua.
    """
    import io

    conn = sqlite3.connect(moi_truong)
    conn.execute(
        "INSERT INTO process_log (created_at,user_course_id,verdict,stage,reason)"
        " VALUES (?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"),
         "uc-1", "REJECTED", "stage2_error", "Azure 408"))
    conn.commit()
    conn.close()

    monkeypatch.setattr(run, "_da_bao_hoan", frozenset())
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter("%(message)s"))
    lg = logging.getLogger("run")
    monkeypatch.setattr(lg, "handlers", [h])
    monkeypatch.setattr(lg, "propagate", False)
    lg.setLevel(logging.INFO)
    logging.disable(logging.NOTSET)
    try:
        with patch.object(client, "get_pending_list", return_value=[_item("uc-1")]), \
             patch.object(client, "download_certificates", side_effect=lambda cc: []):
            for _ in range(100):
                run.process_one_batch(None)
    finally:
        logging.disable(logging.CRITICAL)

    dong = [dg for dg in buf.getvalue().splitlines() if dg.strip()]
    assert len(dong) <= 2, f"100 vòng sinh {len(dong)} dòng log:\n" + "\n".join(dong[:5])


def test_van_bao_khi_co_viec_that(moi_truong, monkeypatch):
    """Chống ồn không được làm câm luôn: có ca mới thì phải báo."""
    import io

    conn = sqlite3.connect(moi_truong)
    conn.execute(
        "INSERT INTO process_log (created_at,user_course_id,verdict,stage,reason)"
        " VALUES (?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"),
         "uc-cu", "REJECTED", "stage2_error", "x"))
    conn.commit()
    conn.close()

    monkeypatch.setattr(run, "_da_bao_hoan", frozenset())
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter("%(message)s"))
    lg = logging.getLogger("run")
    monkeypatch.setattr(lg, "handlers", [h])
    monkeypatch.setattr(lg, "propagate", False)
    lg.setLevel(logging.INFO)
    logging.disable(logging.NOTSET)
    try:
        with patch.object(client, "get_pending_list",
                          return_value=[_item("uc-cu"), _item("uc-moi")]), \
             patch.object(client, "download_certificates", side_effect=lambda cc: []):
            run.process_one_batch(None)
    finally:
        logging.disable(logging.CRITICAL)

    ra = buf.getvalue()
    assert "Có 1 chứng chỉ chờ duyệt" in ra, "im lặng cả khi có việc thật"
    assert "bỏ qua 1 ca chưa tới lượt" in ra, "không cho biết còn ca đang treo"


def test_log_hoan_noi_ro_ca_nao_va_bao_lau(moi_truong, monkeypatch):
    """Log phải trả lời được: ca NÀO, hỏng MẤY LẦN, còn BAO LÂU.

    Bản trước chỉ in "Hoãn 1 chứng chỉ". Khi dòng đó nằm ngay cạnh một ca vừa
    bị từ chối vì sai tên, người đọc tưởng hệ thống đang hoãn nhầm cả ca
    nghiệp vụ — đúng hiểu nhầm đã xảy ra thật.
    """
    import io

    conn = sqlite3.connect(moi_truong)
    for _ in range(2):
        conn.execute(
            "INSERT INTO process_log (created_at,user_course_id,verdict,stage,reason)"
            " VALUES (?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"),
             "uc-treo", "REJECTED", "stage2_error", "Azure timeout"))
    conn.commit()
    conn.close()

    monkeypatch.setattr(run, "_da_bao_hoan", frozenset())
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter("%(message)s"))
    lg = logging.getLogger("run")
    monkeypatch.setattr(lg, "handlers", [h])
    monkeypatch.setattr(lg, "propagate", False)
    lg.setLevel(logging.INFO)
    logging.disable(logging.NOTSET)
    try:
        with patch.object(client, "get_pending_list", return_value=[_item("uc-treo")]), \
             patch.object(client, "download_certificates", side_effect=lambda cc: []):
            run.process_one_batch(None)
    finally:
        logging.disable(logging.CRITICAL)

    ra = buf.getvalue()
    assert "HỎNG KỸ THUẬT" in ra, "không nói rõ chỉ hoãn ca hỏng kỹ thuật"
    assert "uc-treo" in ra, "không cho biết ca nào bị hoãn"
    assert "2/3 lần" in ra, "không cho biết đã hỏng mấy lần trên tổng bao nhiêu"
    assert "thử lại sau" in ra, "không cho biết bao giờ thử lại"


def test_che_do_retry_bo_qua_cooldown(moi_truong):
    """`python run.py retry`: người vận hành biết sự cố đã khỏi, muốn thử ngay.

    Không có lối này thì cách duy nhất để thử lại sớm là sửa .env rồi khởi
    động lại job — hoặc tệ hơn, xóa dòng trong DB log (làm hỏng kiểm toán).
    """
    _chay([_item("uc-1")], [_hong(), _hong()])       # tạo lịch sử hỏng

    # Chạy thường: bị hoãn, không quét lần nào.
    so_lan = {"n": 0}

    def scan(*a):
        so_lan["n"] += 1
        return _duyet()

    with patch.object(client, "get_pending_list", return_value=[_item("uc-1")]), \
         patch.object(client, "download_certificates", side_effect=lambda cc: []), \
         patch.object(run, "_scan_certificate", side_effect=scan):
        run.process_one_batch(None)
    assert so_lan["n"] == 0, "chạy thường mà không hoãn"

    # Chạy retry: bỏ qua giãn cách, xử lý ngay.
    _, hoan, _ = run._sap_xep_va_loc([_item("uc-1")], bo_qua_cooldown=True)
    assert hoan == [], "chế độ retry vẫn hoãn"


def test_log_hoan_co_ten_khoa_hoc(moi_truong, monkeypatch):
    """Người vận hành nhìn eLIS thấy TÊN KHÓA HỌC, không thấy user_course_id.

    Log chỉ in id thì không đối chiếu được với màn hình eLIS, và dễ kết luận
    hệ thống đang bỏ sót chứng chỉ — đúng hiểu nhầm đã xảy ra thật.
    """
    import io

    conn = sqlite3.connect(moi_truong)
    conn.execute(
        "INSERT INTO process_log (created_at,user_course_id,verdict,stage,reason)"
        " VALUES (?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"),
         "uc-treo", "REJECTED", "stage2_error", "Azure timeout"))
    conn.commit()
    conn.close()

    it = _item("uc-treo")
    it["courseName"] = "Learning Microsoft 365 Copilot for Work"

    monkeypatch.setattr(run, "_da_bao_hoan", frozenset())
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter("%(message)s"))
    lg = logging.getLogger("run")
    monkeypatch.setattr(lg, "handlers", [h])
    monkeypatch.setattr(lg, "propagate", False)
    lg.setLevel(logging.INFO)
    logging.disable(logging.NOTSET)
    try:
        with patch.object(client, "get_pending_list", return_value=[it]), \
             patch.object(client, "download_certificates", side_effect=lambda cc: []):
            run.process_one_batch(None)
    finally:
        logging.disable(logging.CRITICAL)

    assert "Learning Microsoft 365 Copilot" in buf.getvalue(), (
        "log không có tên khóa học — không đối chiếu được với eLIS")


# ===== Ca hỏng kỹ thuật phải được GHI LÀ WAITING, không phải REJECTED =====
#
# Đây là chỗ log từng nói dối: bản ghi KHÔNG hề được nộp về eLIS (nên bên đó
# vẫn WAITING và sẽ được thử lại), nhưng log và màn hình lại ghi REJECTED.
# Người vận hành đọc log rồi đi báo học viên "chứng chỉ bị từ chối" trong khi
# hệ thống chỉ đang hẹn thử lại sau vài tiếng.

def test_hong_ky_thuat_ghi_log_la_waiting(moi_truong):
    """Pipeline trả REJECTED, nhưng vì là stage kỹ thuật nên log phải WAITING."""
    _, da_nop = _chay([_item("uc-w")], [_hong(), _hong()])
    assert da_nop == []                       # đúng là chưa nộp gì

    row = database.read_recent_logs(1, db_path=moi_truong)[0]
    assert row["verdict"] == "WAITING", (
        "ghi REJECTED cho ca chưa hề nộp eLIS — log mâu thuẫn với thực tế")
    assert row["stage"] == "stage2_error"


def test_ca_nghiep_vu_van_ghi_dung_verdict(moi_truong):
    """Đối chứng: ca AI phán đoán được thì KHÔNG bị đổi thành WAITING.

    Thiếu test này thì một lần sửa ẩu có thể biến mọi dòng log thành WAITING
    và báo cáo sẽ đếm ra 0 duyệt / 0 từ chối mà không ai thấy lỗi.
    """
    _chay([_item("uc-ok")], [_duyet()])
    row = database.read_recent_logs(1, db_path=moi_truong)[0]
    assert row["verdict"] == "APPROVED"


def test_lo_khong_tai_duoc_file_cung_ghi_waiting(moi_truong):
    """Lô tải hỏng: cũng không nộp gì, nên cũng phải là WAITING."""
    with patch.object(client, "get_pending_list", return_value=[_item("uc-dl")]), \
         patch.object(client, "download_certificates",
                      side_effect=client.ElisError("mạng hỏng")):
        run.process_one_batch(None)

    row = database.read_recent_logs(1, db_path=moi_truong)[0]
    assert row["verdict"] == "WAITING"
    assert row["stage"] == "download_error"


def test_ten_khoa_hoc_duoc_ghi_vao_log(moi_truong):
    """Cột course_name phải được điền ở CẢ hai đường ghi log.

    Không có nó thì từ DB không biết dòng log nào ứng với khóa nào — đúng
    tình huống đã xảy ra: một chứng chỉ treo trên eLIS mà phải đoán xem nó là
    cái nào bằng cách so mốc thời gian.
    """
    # Đường 1: chạy được pipeline (write_log).
    _chay([_item("uc-n1")], [_duyet()])
    assert database.read_recent_logs(1, db_path=moi_truong)[0]["course_name"] == "ISO 27001"

    # Đường 2: hỏng trước cả khi có ảnh (write_failure_log) — quan trọng hơn,
    # vì ca này không đọc được ảnh nên certificate_name luôn NULL.
    with patch.object(client, "get_pending_list", return_value=[_item("uc-n2")]), \
         patch.object(client, "download_certificates",
                      side_effect=client.ElisError("mạng hỏng")):
        run.process_one_batch(None)

    row = database.read_recent_logs(1, db_path=moi_truong)[0]
    assert row["certificate_name"] is None
    assert row["course_name"] == "ISO 27001"

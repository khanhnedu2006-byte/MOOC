"""Test luồng thử lại ca hỏng kỹ thuật (test_retry).

Ca hỏng kỹ thuật để nguyên WAITING, không nộp REJECTED. Rủi ro kèm theo: nó
còn WAITING nên vòng sau lại tải và gọi LLM lại, quay vòng mãi. Ba lớp chặn:

  1. Cooldown  — chưa tới lượt thì BỎ QUA hẳn, không tải, không gọi LLM.
  2. Cảnh báo  — hỏng tới ngưỡng thì gửi EMAIL, và VẪN thử tiếp.
  3. Thứ tự    — ca thử lại xếp sau ca mới, không chặn hàng đợi.

Luật HR: quá ngưỡng mà vẫn nộp REJECTED là LỖI.
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
    monkeypatch.setattr(run.settings, "technical_alert_after", 3)
    monkeypatch.setattr(run.settings, "technical_retry_cooldown_minutes", 30)
    # Không test nào được chạm SMTP thật; muốn quan sát thì tự bắt send_alert.
    monkeypatch.setattr(run.alert, "send_alert", lambda *a, **k: False)
    # File trạng thái cảnh báo RIÊNG cho từng test. Thiếu dòng này, bộ đếm ghi
    # vào .alert_state.json thật và test sau đọc phải bộ đếm của test trước.
    monkeypatch.setattr(run.alert, "STATE_FILE", tmp_path / ".alert_state.json")
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
         patch.object(run, "scan_certificate", side_effect=lambda *a: next(it_kq)), \
         patch.object(run.archive, "save", return_value=None), \
         patch.object(run.archive, "write_verdict", return_value=None):
        result = run.process_one_round(None)
    return result, da_nop


def _lich_su_hong(db, uc_id, attempts, gio_truoc=5, stage="stage2_error"):
    """Dựng sẵn N lần hỏng kỹ thuật trong DB cho một chứng chỉ."""
    conn = sqlite3.connect(db)
    for _ in range(attempts):
        conn.execute(
            "INSERT INTO process_log (created_at,user_course_id,verdict,stage,reason)"
            " VALUES (?,?,?,?,?)",
            ((datetime.now() - timedelta(hours=gio_truoc)).isoformat(timespec="seconds"),
             uc_id, "WAITING", stage, "Azure timeout"))
    conn.commit()
    conn.close()


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


# ===== KHÔNG thử lại trong cùng một vòng =====

def test_moi_vong_chi_quet_MOI_CHUNG_CHI_DUNG_MOT_LAN(moi_truong):
    """Bỏ hẳn lượt thử lại cuối vòng.

    Mỗi vòng đếm HAI lần hỏng thì TECHNICAL_ALERT_AFTER=5 thật ra là 3 vòng.
    """
    attempts = {"n": 0}

    def scan(*a):
        attempts["n"] += 1
        return _hong()

    with patch.object(client, "get_pending_list", return_value=[_item("uc-1")]), \
         patch.object(client, "download_certificates",
                      side_effect=lambda cc: [
                          {"userCourseId": c["UserCourseId"], "anh_bytes": b"x"}
                          for c in cc]), \
         patch.object(client, "update_status", return_value={"successList": [], "failList": []}), \
         patch.object(run, "scan_certificate", side_effect=scan), \
         patch.object(run.archive, "save", return_value=None), \
         patch.object(run.archive, "write_verdict", return_value=None):
        run.process_one_round(None)

    assert attempts["n"] == 1, (
        f"quét {attempts['n']} lần trong một vòng, mong đúng 1 — "
        "lượt thử lại cuối vòng đã quay lại")


def test_mot_lan_hong_ghi_dung_MOT_dong_log(moi_truong):
    """Ngưỡng cảnh báo phải đếm đúng số VÒNG, không phải số lượt quét.

    Một vòng hỏng ghi đúng một dòng log, nên "hỏng 5 lần" = 5 vòng.
    """
    _chay([_item("uc-1")], [_hong()])

    conn = sqlite3.connect(moi_truong)
    n = conn.execute(
        "SELECT COUNT(*) FROM process_log WHERE user_course_id='uc-1'"
    ).fetchone()[0]
    conn.close()
    assert n == 1, f"một vòng hỏng ghi {n} dòng log, mong đúng 1"


def test_ca_hong_CHAN_cac_ca_sau(moi_truong):
    """CHẶN ĐẦU HÀNG: chứng chỉ 1 hỏng thì 2, 3 chưa tới lượt.

    Chạy tiếp thì cả ba cùng hỏng và cùng chạm ngưỡng vì đúng một sự cố.
    """
    _, da_nop = _chay([_item("uc-1"), _item("uc-2"), _item("uc-3")],
                      [_hong(), _duyet(), _duyet()])
    assert da_nop == [], "ca sau vẫn được xử lý dù ca đầu hàng đang hỏng"


def test_ca_hong_CHAN_ca_o_bat_ky_vi_tri_nao(moi_truong):
    """Ca 1 xong thì tới 2; 2 hỏng thì 3 dừng lại chờ."""
    _, da_nop = _chay([_item("uc-1"), _item("uc-2"), _item("uc-3")],
                      [_duyet(), _hong(), _duyet()])
    assert [d["id"] for d in da_nop] == ["uc-1"], (
        "ca 3 vẫn chạy dù ca 2 đứng trước nó đang hỏng")


def test_ca_hong_CHAN_ca_sau_KHONG_ton_luot_LLM(moi_truong):
    """Ca sau không chỉ bị bỏ kết quả — nó không được QUÉT lần nào.

    Quét rồi vứt kết quả vẫn tốn tiền LLM và vẫn đội số lần hỏng của nó lên.
    """
    attempts = {"n": 0}
    ket_qua = iter([_hong(), _duyet(), _duyet()])

    def scan(*a):
        attempts["n"] += 1
        return next(ket_qua)

    with patch.object(client, "get_pending_list",
                      return_value=[_item("uc-1"), _item("uc-2"), _item("uc-3")]), \
         patch.object(client, "download_certificates",
                      side_effect=lambda cc: [
                          {"userCourseId": c["UserCourseId"], "anh_bytes": b"x"}
                          for c in cc]), \
         patch.object(client, "update_status",
                      return_value={"successList": [], "failList": []}), \
         patch.object(run, "scan_certificate", side_effect=scan), \
         patch.object(run.archive, "save", return_value=None), \
         patch.object(run.archive, "write_verdict", return_value=None):
        run.process_one_round(None)

    assert attempts["n"] == 1, (
        f"quét {attempts['n']} chứng chỉ, mong đúng 1 — ca sau ca hỏng vẫn tốn LLM")


def test_ca_dau_hang_dang_gian_cach_thi_ca_sau_CHO_THEO(moi_truong):
    """Ca đầu hàng chưa tới lượt thử lại -> cả hàng đợi đứng yên.

    Thiếu luật này thì 2, 3, 4 chạy trước và 1 mất chỗ đứng đầu.
    """
    _lich_su_hong(moi_truong, "uc-1", attempts=1, gio_truoc=0)   # còn giãn cách

    sap, hoan, _ = run.filter_queue(
        [_item("uc-1"), _item("uc-2"), _item("uc-3")])
    assert sap == [], "ca sau vẫn chạy dù ca đầu hàng chưa tới lượt"
    assert [it["id"] for it, *_ in hoan] == ["uc-1"]


# ===== Cooldown: chặn vòng lặp đốt tiền =====

def test_chua_het_cooldown_thi_KHONG_goi_llm(moi_truong):
    """Lớp chặn quan trọng nhất về chi phí.

    Thiếu nó, một chứng chỉ hỏng tốn 12 lượt LLM mỗi phút, mãi mãi.
    """
    _chay([_item("uc-1")], [_hong(), _hong()])       # tạo lịch sử hỏng

    attempts = {"n": 0}

    def scan(*a):
        attempts["n"] += 1
        return _duyet()

    with patch.object(client, "get_pending_list", return_value=[_item("uc-1")]), \
         patch.object(client, "download_certificates", side_effect=lambda cc: []), \
         patch.object(run, "scan_certificate", side_effect=scan):
        run.process_one_round(None)

    assert attempts["n"] == 0, "vẫn gọi LLM dù chưa hết cooldown"


def test_het_cooldown_thi_duoc_thu_lai(moi_truong):
    _chay([_item("uc-1")], [_hong(), _hong()])
    _lui_thoi_gian(moi_truong, "uc-1", gio=2)

    _, da_nop = _chay([_item("uc-1")], [_duyet()])
    assert len(da_nop) == 1, "hết cooldown rồi mà vẫn bị hoãn"


# ===== Quá ngưỡng: CẢNH BÁO chứ không bỏ cuộc =====

def test_qua_nguong_KHONG_BAO_GIO_nop_rejected(moi_truong):
    """Luật HR: lỗi hệ thống không bao giờ thành REJECTED.

    Nộp REJECTED sau N lần hỏng là từ chối oan hàng loạt chứng chỉ hợp lệ.
    """
    _lich_su_hong(moi_truong, "uc-X", attempts=9)     # gấp ba lần ngưỡng

    da_nop = []
    with patch.object(client, "get_pending_list", return_value=[_item("uc-X")]), \
         patch.object(client, "download_certificates", side_effect=lambda cc: []), \
         patch.object(client, "update_status",
                      side_effect=lambda d: (da_nop.extend(d),
                                             {"successList": [{"id": x["id"]} for x in d],
                                              "failList": []})[1]):
        run.process_one_round(None)

    assert da_nop == [], (
        "hỏng 9 lần mà vẫn nộp kết quả về eLIS — nhánh bỏ cuộc đã quay lại")


def test_qua_nguong_van_duoc_thu_lai(moi_truong):
    """Vượt ngưỡng KHÔNG loại chứng chỉ khỏi hàng đợi.

    Không từ chối thì phải tiếp tục thử, nếu không nó nằm WAITING vĩnh viễn.
    """
    _lich_su_hong(moi_truong, "uc-X", attempts=9)

    sap, hoan, canh_bao = run.filter_queue([_item("uc-X")])
    assert [x["id"] for x in sap] == ["uc-X"], (
        "ca vượt ngưỡng bị loại khỏi hàng đợi — sẽ không bao giờ được thử lại")
    assert hoan == []
    assert [(it["id"], n) for it, n in canh_bao] == [("uc-X", 9)]


def test_qua_nguong_thi_gui_canh_bao_kem_du_thong_tin(moi_truong, monkeypatch):
    """Email cảnh báo phải nói được CÁI GÌ hỏng, không chỉ 'có lỗi'.

    Thiếu stage/reason thì người nhận phải mở log mới biết sửa ở đâu.
    """
    _lich_su_hong(moi_truong, "uc-X", attempts=5, stage="stage2_error")

    da_gui = []
    monkeypatch.setattr(run.alert, "send_alert",
                        lambda ds, *a, **k: (da_gui.append(ds), True)[1])

    with patch.object(client, "get_pending_list", return_value=[_item("uc-X")]), \
         patch.object(client, "download_certificates", side_effect=lambda cc: []):
        run.process_one_round(None)

    assert len(da_gui) == 1, "quá ngưỡng mà không gửi cảnh báo"
    ca = da_gui[0][0]
    assert ca["failure_count"] == 5
    assert ca["stage"] == "stage2_error"
    assert ca["reason"] == "Azure timeout"
    assert ca["employee_name"] == "Bùi Đức Hòa"
    assert ca["course_name"] == "ISO 27001"


def test_chua_toi_nguong_thi_KHONG_gui_canh_bao(moi_truong, monkeypatch):
    """Hỏng một hai lần là chuyện thường — báo ngay thì thư thành tiếng ồn.

    alert_operator() chạy MỖI vòng, nên phải canh DANH SÁCH GỬI ĐI RỖNG.
    """
    _lich_su_hong(moi_truong, "uc-X", attempts=2)      # ngưỡng = 3

    da_gui = []
    monkeypatch.setattr(run.alert, "send_alert",
                        lambda ds, *a, **k: (da_gui.append(ds), True)[1])

    with patch.object(client, "get_pending_list", return_value=[_item("uc-X")]), \
         patch.object(client, "download_certificates", side_effect=lambda cc: []):
        run.process_one_round(None)

    assert all(payload == [] for payload in da_gui), (
        f"mới hỏng 2/3 lần đã đưa chứng chỉ vào thư cảnh báo: {da_gui}")


def test_ca_lo_cung_hong_thi_gop_MOT_thu(moi_truong, monkeypatch):
    """Azure hết hạn mức -> cả hàng đợi cùng vượt ngưỡng trong một vòng.

    Phải là MỘT send_alert mang cả danh sách; 50 thư thì người nhận lọc bỏ.
    """
    for i in range(4):
        _lich_su_hong(moi_truong, f"uc-{i}", attempts=5)

    da_gui = []
    monkeypatch.setattr(run.alert, "send_alert",
                        lambda ds, *a, **k: (da_gui.append(ds), True)[1])

    with patch.object(client, "get_pending_list",
                      return_value=[_item(f"uc-{i}") for i in range(4)]), \
         patch.object(client, "download_certificates", side_effect=lambda cc: []):
        run.process_one_round(None)

    assert len(da_gui) == 1, "gửi nhiều thư cho cùng một sự cố"
    assert len(da_gui[0]) == 4, "thư không liệt kê đủ các ca đang hỏng"


# ===== Thứ tự hàng đợi =====

def test_GIU_NGUYEN_thu_tu_elis_tra_ve(moi_truong):
    """KHÔNG đẩy ca đã hỏng xuống cuối hàng đợi.

    Đẩy xuống cuối không cứu được gì mà làm log lệch thứ tự màn hình eLIS.
    """
    _lich_su_hong(moi_truong, "uc-cu", attempts=1, gio_truoc=5)

    sap, hoan, canh_bao = run.filter_queue(
        [_item("uc-cu"), _item("uc-moi-1"), _item("uc-moi-2")])
    assert [x["id"] for x in sap] == ["uc-cu", "uc-moi-1", "uc-moi-2"], (
        "thứ tự bị đảo — cơ chế đẩy xuống cuối đã quay lại")
    assert hoan == [] and canh_bao == []


def test_ca_hong_o_GIUA_cung_giu_nguyen_cho(moi_truong):
    """Không chỉ ca đầu: ca hỏng ở bất kỳ vị trí nào cũng nằm nguyên chỗ."""
    _lich_su_hong(moi_truong, "uc-2", attempts=1, gio_truoc=5)

    sap, _, _ = run.filter_queue(
        [_item("uc-1"), _item("uc-2"), _item("uc-3")])
    assert [x["id"] for x in sap] == ["uc-1", "uc-2", "uc-3"]


def test_stage_ky_thuat_khong_lech_giua_run_va_database():
    """run.TECHNICAL_STAGES phải lấy từ database, không chép tay.

    Hai danh sách lệch nhau thì lỗi kỹ thuật mới bị nộp REJECTED.
    """
    assert run.TECHNICAL_STAGES == frozenset(database.TECHNICAL_STAGES)


def test_danh_sach_stage_khop_voi_bao_cao():
    """database.TECHNICAL_STAGES phải phủ đúng nhóm report coi là kỹ thuật."""
    from database import report
    assert set(database.TECHNICAL_STAGES) == set(report.FAILURE_GROUPS)


# ===== Log không được phình khi có ca treo lâu =====

def test_log_khong_lap_moi_vong_khi_ca_dang_cooldown(moi_truong, monkeypatch):
    """Cooldown là hàng TIẾNG, vòng lặp là vài GIÂY.

    In mỗi vòng thì 6 tiếng chờ sinh hơn 4.000 dòng giống hệt nhau.
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

    monkeypatch.setattr(run, "_last_deferred_ids", frozenset())
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
                run.process_one_round(None)
    finally:
        logging.disable(logging.CRITICAL)

    rows = [dg for dg in buf.getvalue().splitlines() if dg.strip()]
    assert len(rows) <= 2, f"100 vòng sinh {len(rows)} dòng log:\n" + "\n".join(rows[:5])


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

    monkeypatch.setattr(run, "_last_deferred_ids", frozenset())
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter("%(message)s"))
    lg = logging.getLogger("run")
    monkeypatch.setattr(lg, "handlers", [h])
    monkeypatch.setattr(lg, "propagate", False)
    lg.setLevel(logging.INFO)
    logging.disable(logging.NOTSET)
    try:
        # uc-moi đứng TRƯỚC nên chạy được; uc-cu đứng sau đang trong giãn
        # cách nên chặn từ chỗ đó trở đi.
        with patch.object(client, "get_pending_list",
                          return_value=[_item("uc-moi"), _item("uc-cu")]), \
             patch.object(client, "download_certificates", side_effect=lambda cc: []):
            run.process_one_round(None)
    finally:
        logging.disable(logging.CRITICAL)

    ra = buf.getvalue()
    assert "Có 1 chứng chỉ chờ duyệt" in ra, "im lặng cả khi có việc thật"
    assert "bỏ qua 1 ca chưa tới lượt" in ra, "không cho biết còn ca đang treo"


def test_log_hoan_noi_ro_ca_nao_va_bao_lau(moi_truong, monkeypatch):
    """Log phải trả lời được: ca NÀO, hỏng MẤY LẦN, còn BAO LÂU.

    Chỉ in "Hoãn 1 chứng chỉ" thì người đọc tưởng hoãn nhầm cả ca nghiệp vụ.
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

    monkeypatch.setattr(run, "_last_deferred_ids", frozenset())
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
            run.process_one_round(None)
    finally:
        logging.disable(logging.CRITICAL)

    ra = buf.getvalue()
    assert "HỎNG KỸ THUẬT" in ra, "không nói rõ chỉ hoãn ca hỏng kỹ thuật"
    assert "uc-treo" in ra, "không cho biết ca nào bị hoãn"
    assert "hỏng 2 lần" in ra, "không cho biết đã hỏng mấy lần"
    assert "thử lại sau" in ra, "không cho biết bao giờ thử lại"
    # KHÔNG in dạng phân số "2/3": mẫu số gợi ý tới đó là dừng, mà không còn
    # mốc dừng nào. Người đọc sẽ tưởng chứng chỉ sắp bị từ chối.
    assert "2/3" not in ra, "vẫn in dạng phân số như thể còn nhánh bỏ cuộc"


def test_che_do_retry_bo_qua_cooldown(moi_truong):
    """`python run.py retry`: người vận hành biết sự cố đã khỏi, muốn thử ngay.

    Thiếu lối này thì phải sửa .env khởi động lại job, hoặc xóa dòng DB log.
    """
    _chay([_item("uc-1")], [_hong(), _hong()])       # tạo lịch sử hỏng

    # Chạy thường: bị hoãn, không quét lần nào.
    attempts = {"n": 0}

    def scan(*a):
        attempts["n"] += 1
        return _duyet()

    with patch.object(client, "get_pending_list", return_value=[_item("uc-1")]), \
         patch.object(client, "download_certificates", side_effect=lambda cc: []), \
         patch.object(run, "scan_certificate", side_effect=scan):
        run.process_one_round(None)
    assert attempts["n"] == 0, "chạy thường mà không hoãn"

    # Chạy retry: bỏ qua giãn cách, xử lý ngay.
    _, hoan, _ = run.filter_queue([_item("uc-1")], ignore_cooldown=True)
    assert hoan == [], "chế độ retry vẫn hoãn"


def test_log_hoan_co_ten_khoa_hoc(moi_truong, monkeypatch):
    """Người vận hành nhìn eLIS thấy TÊN KHÓA HỌC, không thấy user_course_id.

    Log chỉ in id thì không đối chiếu được với màn hình eLIS.
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

    monkeypatch.setattr(run, "_last_deferred_ids", frozenset())
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
            run.process_one_round(None)
    finally:
        logging.disable(logging.CRITICAL)

    assert "Learning Microsoft 365 Copilot" in buf.getvalue(), (
        "log không có tên khóa học — không đối chiếu được với eLIS")


# ===== Ca hỏng kỹ thuật phải được GHI LÀ WAITING, không phải REJECTED =====
#
# Bản ghi không hề được nộp về eLIS nên bên đó vẫn WAITING. Log ghi REJECTED
# là nói dối: người vận hành sẽ đi báo học viên "chứng chỉ bị từ chối".

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

    Nếu mọi dòng log thành WAITING thì báo cáo đếm ra 0 duyệt / 0 từ chối.
    """
    _chay([_item("uc-ok")], [_duyet()])
    row = database.read_recent_logs(1, db_path=moi_truong)[0]
    assert row["verdict"] == "APPROVED"


def test_lo_khong_tai_duoc_file_cung_ghi_waiting(moi_truong):
    """Lô tải hỏng: cũng không nộp gì, nên cũng phải là WAITING."""
    with patch.object(client, "get_pending_list", return_value=[_item("uc-dl")]), \
         patch.object(client, "download_certificates",
                      side_effect=client.ElisError("mạng hỏng")):
        run.process_one_round(None)

    row = database.read_recent_logs(1, db_path=moi_truong)[0]
    assert row["verdict"] == "WAITING"
    assert row["stage"] == "download_error"


def test_ten_khoa_hoc_duoc_ghi_vao_log(moi_truong):
    """Cột course_name phải được điền ở CẢ hai đường ghi log.

    Thiếu nó thì từ DB không biết dòng log nào ứng với khóa nào.
    """
    # Đường 1: chạy được pipeline (write_log).
    _chay([_item("uc-n1")], [_duyet()])
    assert database.read_recent_logs(1, db_path=moi_truong)[0]["course_name"] == "ISO 27001"

    # Đường 2: hỏng trước khi có ảnh (write_failure_log) — ca này không đọc
    # được ảnh nên certificate_name luôn NULL.
    with patch.object(client, "get_pending_list", return_value=[_item("uc-n2")]), \
         patch.object(client, "download_certificates",
                      side_effect=client.ElisError("mạng hỏng")):
        run.process_one_round(None)

    row = database.read_recent_logs(1, db_path=moi_truong)[0]
    assert row["certificate_name"] is None
    assert row["course_name"] == "ISO 27001"
